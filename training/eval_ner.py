#!/usr/bin/env python3
"""Span-level evaluation of the fine-tuned LOC NER model on the held-out test
split, with a FP/FN error dump. Char-span exact-match P/R/F1 (the metric the
runtime cares about), computed by decoding BIO -> char spans.

Usage:
  python training/evaluate.py [--model DIR] [--limit N] [--dump 40]
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

from ner_common import LABELS, MAX_LENGTH, OUTPUT_DIR, TEST_META_PATH


def decode_spans(offsets, label_ids):
    """BIO label ids + subword char-offsets -> list of (char_start, char_end)."""
    spans = []
    cur = None
    for (cs, ce), lid in zip(offsets, label_ids):
        if cs == ce:                       # special token
            continue
        lab = LABELS[lid]
        if lab == "B-LOC":
            if cur:
                spans.append(cur)
            cur = (cs, ce)
        elif lab == "I-LOC" and cur:
            cur = (cur[0], ce)
        else:                              # O (or stray I- with no open span)
            if cur:
                spans.append(cur)
                cur = None
    if cur:
        spans.append(cur)
    return spans


@torch.no_grad()
def predict(texts, model, tokenizer, max_length, batch_size=32):
    out = []
    for i in range(0, len(texts), batch_size):
        chunk = texts[i:i + batch_size]
        enc = tokenizer(chunk, truncation=True, max_length=max_length,
                        padding=True, return_offsets_mapping=True,
                        return_tensors="pt")
        offsets = enc.pop("offset_mapping").tolist()
        logits = model(**enc).logits
        preds = logits.argmax(-1).tolist()
        for off, pr in zip(offsets, preds):
            out.append(decode_spans(off, pr))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(OUTPUT_DIR))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dump", type=int, default=40, help="# of error examples to print")
    ap.add_argument("--max-length", type=int, default=MAX_LENGTH)
    args = ap.parse_args()

    torch.set_num_threads(4)
    records = json.loads(TEST_META_PATH.read_text(encoding="utf-8"))
    if args.limit:
        records = records[: args.limit]
    texts = [r[0] for r in records]
    golds = [{(s, e) for s, e, _ in r[1].get("entities", [])} for r in records]

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForTokenClassification.from_pretrained(args.model).eval()

    def score(name, eval_texts, dump=0):
        preds = [set(p) for p in predict(eval_texts, model, tokenizer, args.max_length)]
        tp = fp = fn = 0
        errors = []
        for text, g, p in zip(eval_texts, golds, preds):
            tp += len(g & p)
            cur_fp, cur_fn = p - g, g - p
            fp += len(cur_fp); fn += len(cur_fn)
            if (cur_fp or cur_fn) and len(errors) < dump:
                errors.append((text, sorted(g), sorted(p)))
        prec = tp / (tp + fp) if (tp + fp) else 1.0
        rec = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        print(f"\n=== {name} — span-level (exact char match) ===")
        print(f"TP={tp}  FP={fp}  FN={fn}")
        print(f"precision={prec:.4f}  recall={rec:.4f}  f1={f1:.4f}")
        for text, g, p in errors:
            gf = [text[s:e] for s, e in g]; pf = [text[s:e] for s, e in p]
            print(f"  gold={gf}  pred={pf}\n      {text[:90]!r}")
        return f1

    print(f"Predicting on {len(texts)} test records (natural + lowercased) ...")
    # Lowercasing preserves char length → gold offsets stay valid. The gap between
    # natural and lowercased F1 is the case-invariance gate (should be ~0 after aug).
    lower_texts = [t.lower() for t in texts]
    f1_nat = score("NATURAL case", texts, dump=args.dump)
    f1_low = score("LOWERCASED", lower_texts, dump=max(0, args.dump // 2))
    print(f"\n>>> case-invariance gap (natural f1 - lower f1): {f1_nat - f1_low:+.4f}")


if __name__ == "__main__":
    main()
