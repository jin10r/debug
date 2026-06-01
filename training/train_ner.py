#!/usr/bin/env python3
"""Fine-tune rubert-tiny2 for LOC BIO token classification (CPU-tuned).

Run training/prepare_data.py first. Designed for a 4-core / ~6GB (Docker down)
box: small batch + dynamic padding + group_by_length. Checkpoints are resumable.

Usage:
  python training/train_ner.py [--epochs 3] [--batch 16] [--resume]
"""
from __future__ import annotations

import argparse

import numpy as np
import torch
from datasets import load_from_disk
from transformers import (
    AutoModelForTokenClassification, AutoTokenizer,
    DataCollatorForTokenClassification, Trainer, TrainingArguments,
    set_seed,
)
from seqeval.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
)

from ner_common import (
    DATASET_DICT_DIR, LABELS, MODEL_NAME, OUTPUT_DIR, SEED, id2label, label2id,
)


def build_compute_metrics():
    def compute_metrics(eval_pred):
        logits, labels = eval_pred
        preds = np.argmax(logits, axis=-1)
        true_labels, true_preds = [], []
        for pred_row, lab_row in zip(preds, labels):
            cur_t, cur_p = [], []
            for p, l in zip(pred_row, lab_row):
                if l == -100:
                    continue
                cur_t.append(LABELS[l])
                cur_p.append(LABELS[p])
            true_labels.append(cur_t)
            true_preds.append(cur_p)
        return {
            "precision": precision_score(true_labels, true_preds),
            "recall": recall_score(true_labels, true_preds),
            "f1": f1_score(true_labels, true_preds),
            "accuracy": accuracy_score(true_labels, true_preds),
        }
    return compute_metrics


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--grad-accum", type=int, default=2)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--threads", type=int, default=4)
    # workers=0: forking dataloader workers after MKL/OpenMP thread-pool init
    # crashes (ud2/invalid-opcode) on this AVX-only Sandy Bridge CPU. The arrow
    # dataset is pre-tokenized so in-process loading costs ~nothing. Keep 0.
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--max-steps", type=int, default=0, help=">0 caps steps (smoke test)")
    ap.add_argument("--eval-steps", type=int, default=2000)
    ap.add_argument("--save-steps", type=int, default=2000)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    set_seed(SEED)
    torch.set_num_threads(args.threads)

    ds = load_from_disk(str(DATASET_DICT_DIR))
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForTokenClassification.from_pretrained(
        MODEL_NAME, num_labels=len(LABELS), id2label=id2label, label2id=label2id,
    )
    collator = DataCollatorForTokenClassification(tokenizer)

    targs = TrainingArguments(
        output_dir=str(OUTPUT_DIR),
        overwrite_output_dir=not args.resume,
        per_device_train_batch_size=args.batch,
        per_device_eval_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        max_steps=args.max_steps if args.max_steps > 0 else -1,
        learning_rate=args.lr,
        weight_decay=0.01,
        warmup_ratio=0.1,
        fp16=False, bf16=False,                 # CPU = fp32
        dataloader_num_workers=args.workers,
        eval_strategy="steps", eval_steps=args.eval_steps,
        save_strategy="steps", save_steps=args.save_steps, save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="f1", greater_is_better=True,
        logging_steps=200,
        optim="adamw_torch",
        group_by_length=True,                   # big CPU win: less padding
        report_to="none",
        seed=SEED,
    )

    trainer = Trainer(
        model=model,
        args=targs,
        train_dataset=ds["train"],
        eval_dataset=ds["validation"],
        data_collator=collator,
        tokenizer=tokenizer,
        compute_metrics=build_compute_metrics(),
    )

    print(f"Training on {len(ds['train'])} examples "
          f"(batch={args.batch}x{args.grad_accum}, epochs={args.epochs}, threads={args.threads})")
    trainer.train(resume_from_checkpoint=args.resume)

    trainer.save_model(str(OUTPUT_DIR))
    tokenizer.save_pretrained(str(OUTPUT_DIR))
    print(f"\n✅ best model saved -> {OUTPUT_DIR}")

    metrics = trainer.evaluate()
    print(f"validation: {metrics}")


if __name__ == "__main__":
    main()
