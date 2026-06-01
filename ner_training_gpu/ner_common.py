"""Shared constants & helpers for the LOC NER training pipeline (standalone GPU package).

Single label set (LOC) in BIO scheme. All paths are anchored to THIS folder so the
package is self-contained and portable to a Windows + GPU box — no dependency on the
main repository layout.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

BASE = Path(__file__).resolve().parent

# --- model / label scheme ---
MODEL_NAME = "cointegrated/rubert-tiny2"
MAX_LENGTH = 256
SEED = 42

LABELS: List[str] = ["O", "B-LOC", "I-LOC"]
label2id = {lab: i for i, lab in enumerate(LABELS)}
id2label = {i: lab for i, lab in enumerate(LABELS)}

# --- paths (all inside this package) ---
DATASET_PATH = BASE / "data" / "dataset.retrain.json"     # training input (prebuilt)
DATA_DIR = BASE / "data" / "prepared"                     # HF DatasetDict (arrow)
DATASET_DICT_DIR = DATA_DIR / "ner_dataset"
TEST_META_PATH = DATA_DIR / "test_meta.json"              # raw (text, entities) for eval
SPLIT_INDICES_PATH = DATA_DIR / "split_indices.json"
OUTPUT_DIR = BASE / "output" / "ner_loc"                  # HF checkpoints / best model
ONNX_DIR = BASE / "output" / "ner_loc_onnx"               # exported ONNX + tokenizer

Record = Tuple[str, dict]


def load_dataset_records(path=None) -> List[list]:
    """Load the spaCy-format dataset: [[text, {"entities": [[s,e,"LOC"],...]}], ...]."""
    p = Path(path) if path else DATASET_PATH
    with open(p, encoding="utf-8") as f:
        return json.load(f)
