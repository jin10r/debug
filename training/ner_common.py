"""Shared constants & helpers for the LOC NER training pipeline.

Single label set (LOC) in BIO scheme. Paths are anchored to the repo root so
scripts work regardless of the current working directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- model / label scheme ---
MODEL_NAME = "cointegrated/rubert-tiny2"
MAX_LENGTH = 256
SEED = 42

LABELS: List[str] = ["O", "B-LOC", "I-LOC"]
label2id = {lab: i for i, lab in enumerate(LABELS)}
id2label = {i: lab for i, lab in enumerate(LABELS)}

# --- paths ---
# retrain = clean + full oblique relabel + template dedup + capped negatives
# (training/rebuild_dataset.py). Falls back to dataset.clean.json if absent.
DATASET_PATH = REPO_ROOT / "train_data" / "dataset.retrain.json"
DATA_DIR = REPO_ROOT / "training" / "data"          # HF DatasetDict (arrow) — gitignored
DATASET_DICT_DIR = DATA_DIR / "ner_dataset"
TEST_META_PATH = DATA_DIR / "test_meta.json"        # raw (text, entities) for error dumps
SPLIT_INDICES_PATH = DATA_DIR / "split_indices.json"
OUTPUT_DIR = REPO_ROOT / "training" / "output" / "ner_loc"   # HF checkpoints — gitignored
ONNX_DIR = REPO_ROOT / "models" / "ner_loc_onnx"            # committed inference artifact

Record = Tuple[str, dict]


def load_dataset_records(path=None) -> List[list]:
    """Load the cleaned spaCy-format dataset: [[text, {"entities": [[s,e,"LOC"],...]}], ...]."""
    p = Path(path) if path else DATASET_PATH
    if not p.exists() and p == DATASET_PATH:
        p = REPO_ROOT / "train_data" / "dataset.clean.json"   # graceful fallback
    with open(p, encoding="utf-8") as f:
        return json.load(f)
