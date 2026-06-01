#!/usr/bin/env python3
"""Prepare the LOC NER dataset for token-classification training.

Steps:
  1. Load train_data/dataset.clean.json (spaCy format, single LOC label).
  2. Deterministic 90/5/5 train/val/test split (seed 42), persist indices.
  3. Tokenize with the rubert-tiny2 fast tokenizer (offset_mapping) and convert
     char-offset entities -> BIO subword labels (O / B-LOC / I-LOC).
  4. Save an HF DatasetDict (arrow) to training/data/ + raw test meta for eval.

Usage:
  python training/prepare_data.py [--limit N] [--max-length 256]

--limit subsamples the FIRST N records (after shuffle) for fast pipeline dry-runs.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter

from datasets import Dataset, DatasetDict
from transformers import AutoTokenizer

from ner_common import (
    DATA_DIR, DATASET_DICT_DIR, MAX_LENGTH, MODEL_NAME, SEED,
    SPLIT_INDICES_PATH, TEST_META_PATH, label2id, load_dataset_records,
)


def split_indices(n: int, seed: int = SEED):
    idx = list(range(n))
    random.Random(seed).shuffle(idx)
    n_test = n_val = max(1, n // 20)          # 5% each
    test = idx[:n_test]
    val = idx[n_test:n_test + n_val]
    train = idx[n_test + n_val:]
    return train, val, test


def align_labels(text, entities, tokenizer, max_length, stats):
    """Tokenize one text and produce BIO label ids aligned to subword offsets."""
    enc = tokenizer(
        text, truncation=True, max_length=max_length, return_offsets_mapping=True,
    )
    offsets = enc["offset_mapping"]
    # Sort entities by start; track per-entity coverage to detect truncation drops.
    ents = sorted(([s, e] for s, e, _ in entities), key=lambda x: x[0])
    labels = []
    prev_ent = None  # index of entity the previous subword belonged to
    covered = set()
    for (cs, ce) in offsets:
        if cs == ce:                          # special token ([CLS]/[SEP]/[PAD])
            labels.append(-100)
            prev_ent = None
            continue
        # find entity whose char-span contains this subword's start
        hit = None
        for ei, (es, ee) in enumerate(ents):
            if es <= cs < ee:
                hit = ei
                break
        if hit is None:
            # partial overlap (subword starts before entity but ends inside)?
            for ei, (es, ee) in enumerate(ents):
                if cs < es < ce:
                    hit = ei
                    stats["partial"] += 1
                    break
        if hit is None:
            labels.append(label2id["O"])
            prev_ent = None
        else:
            covered.add(hit)
            labels.append(label2id["B-LOC"] if hit != prev_ent else label2id["I-LOC"])
            prev_ent = hit
    enc["labels"] = labels
    enc.pop("offset_mapping", None)
    stats["dropped_entities"] += len(ents) - len(covered)
    stats["entities"] += len(ents)
    return enc


def build_split(record_list, tokenizer, max_length, stats):
    """record_list: iterable of (text, ann-dict)."""
    rows = {"input_ids": [], "attention_mask": [], "labels": []}
    for text, ann in record_list:
        enc = align_labels(text, ann.get("entities", []), tokenizer, max_length, stats)
        rows["input_ids"].append(enc["input_ids"])
        rows["attention_mask"].append(enc["attention_mask"])
        rows["labels"].append(enc["labels"])
        if "token_type_ids" in enc:
            rows.setdefault("token_type_ids", []).append(enc["token_type_ids"])
    return Dataset.from_dict(rows)


def lowercase_record(text, ann):
    """Lowercase the text; entity char-offsets stay valid because str.lower()
    preserves length for Cyrillic/Latin. Returns None if length changed (rare)."""
    low = text.lower()
    if len(low) != len(text):
        return None
    return (low, ann)


def augment_lower(records, fraction, seed, stats):
    """Append lowercased copies of `fraction` of records (case-invariance training).
    Kills the 'capital = LOC' shortcut that tanked recall on lowercase street mentions."""
    if fraction <= 0:
        return list(records)
    out = list(records)
    rng = random.Random(seed)
    n_aug = 0
    for text, ann in records:
        if rng.random() < fraction:
            low = lowercase_record(text, ann)
            if low is None:
                stats["lower_len_mismatch"] += 1
                continue
            out.append(low)
            n_aug += 1
    stats["lower_augmented"] = n_aug
    rng.shuffle(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="subsample first N records (dry run)")
    ap.add_argument("--max-length", type=int, default=MAX_LENGTH)
    ap.add_argument("--dataset", default=None, help="override dataset path")
    ap.add_argument("--augment-lower", type=float, default=0.5,
                    help="fraction of TRAIN records to duplicate lowercased (case-invariance)")
    args = ap.parse_args()

    print(f"Loading dataset from disk ...")
    records = load_dataset_records(args.dataset)
    n_all = len(records)
    train_i, val_i, test_i = split_indices(n_all)
    if args.limit:
        # keep proportions but cap total for a fast dry-run
        cap = args.limit
        train_i = train_i[: int(cap * 0.9)]
        val_i = val_i[: max(1, int(cap * 0.05))]
        test_i = test_i[: max(1, int(cap * 0.05))]
        print(f"DRY RUN: limited to {len(train_i)}/{len(val_i)}/{len(test_i)} (train/val/test)")
    print(f"Records: {n_all} total -> {len(train_i)} train / {len(val_i)} val / {len(test_i)} test")

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    assert tokenizer.is_fast, "need a fast tokenizer for offset_mapping"

    stats = Counter()
    train_recs = [records[i] for i in train_i]
    val_recs = [records[i] for i in val_i]
    test_recs = [records[i] for i in test_i]

    # Case augmentation on TRAIN only; val/test kept natural.
    train_recs = augment_lower(train_recs, args.augment_lower, SEED, stats)
    # Lowercased copy of the WHOLE validation set → case-invariance gate in eval.
    val_lower_recs = [r for r in (lowercase_record(t, a) for t, a in val_recs) if r]

    ds = DatasetDict({
        "train": build_split(train_recs, tokenizer, args.max_length, stats),
        "validation": build_split(val_recs, tokenizer, args.max_length, stats),
        "validation_lower": build_split(val_lower_recs, tokenizer, args.max_length, stats),
        "test": build_split(test_recs, tokenizer, args.max_length, stats),
    })

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ds.save_to_disk(str(DATASET_DICT_DIR))
    SPLIT_INDICES_PATH.write_text(json.dumps(
        {"train": train_i, "val": val_i, "test": test_i}), encoding="utf-8")
    # raw test records for span-level error analysis in evaluate.py
    TEST_META_PATH.write_text(json.dumps(
        [records[i] for i in test_i], ensure_ascii=False), encoding="utf-8")

    # label distribution sanity
    label_counts = Counter()
    for row in ds["train"]:
        for l in row["labels"]:
            if l != -100:
                label_counts[l] += 1
    # case-balance check: fraction of train examples whose text is all-lowercase
    n_lower = sum(1 for t, _ in train_recs if t == t.lower())
    print("\n=== prep stats ===")
    print(f"train size (after aug): {len(train_recs)}  "
          f"(lowercased-augmented: {stats.get('lower_augmented', 0)}, "
          f"len-mismatch skipped: {stats.get('lower_len_mismatch', 0)})")
    print(f"train all-lowercase fraction: {n_lower/max(len(train_recs),1):.1%}")
    print(f"validation_lower size: {len(val_lower_recs)}")
    print(f"entities seen:      {stats['entities']}")
    print(f"entities dropped (truncation): {stats['dropped_entities']}")
    print(f"partial-overlap subwords:      {stats['partial']}")
    print(f"train label dist (O/B/I): "
          f"{label_counts[0]}/{label_counts[1]}/{label_counts[2]}")
    print(f"\nsaved DatasetDict -> {DATASET_DICT_DIR}")
    print(f"saved test meta    -> {TEST_META_PATH}")


if __name__ == "__main__":
    main()
