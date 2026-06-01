#!/usr/bin/env python3
"""Prune postgres/data/streets.csv to only streets that actually occur in the
dataset. A street "occurs" if the gazetteer matcher links any labelled entity
surface (from dataset.clean.json ∪ dataset.retrain.json) to its street_id.

Rationale: ~1060 gazetteer rows include streets never seen in 252k messages —
dead weight in the phonetic index and a needless FP surface. Keep only the seen
set. Original is backed up to streets.full.csv (reversible).

CAVEAT: removed streets become unlinkable at inference even if they appear in a
FUTURE message. Intentional per request; restore from streets.full.csv if needed.

Usage:
  .venv-eval/bin/python training/prune_streets.py            # report only (dry)
  .venv-eval/bin/python training/prune_streets.py --apply    # back up + rewrite
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

os.environ.setdefault('BOT_TOKEN', 'x')
os.environ.setdefault('CHANNEL_ID', '-1001234567890')
os.environ.setdefault('JWT_SECRET', 'a' * 32)
os.environ.setdefault('REDIS_PASSWORD', 'z')

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from parser.morphology import Morphology
from parser.phonetic_index import PhoneticIndex
from parser.razdel_tokenizer import RazdelTokenizer
from parser.street_matcher import StreetMatcher

_STREETS_CSV = _REPO / 'postgres' / 'data' / 'streets.csv'
_BACKUP = _REPO / 'postgres' / 'data' / 'streets.full.csv'
_STOPWORDS_CSV = _REPO / 'postgres' / 'data' / 'stopwords.csv'
_DATASETS = [_REPO / 'train_data' / 'dataset.clean.json',
             _REPO / 'train_data' / 'dataset.retrain.json']


def _load_rows():
    """Return (rows_for_index, raw_csv_rows). id = 1-based CSV data-row index."""
    idx_rows, raw = [], []
    with open(_STREETS_CSV, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        for i, rec in enumerate(reader, start=1):
            names = [n.strip() for n in (rec['names'] or '').split('|') if n.strip()]
            raw.append(rec)
            if names:
                idx_rows.append({'id': i, 'names': names})
    return idx_rows, raw, fields


def _load_stopwords():
    with open(_STOPWORDS_CSV, encoding='utf-8') as f:
        r = csv.reader(f); next(r, None)
        return {x[0].strip().lower() for x in r if x and x[0].strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    idx_rows, raw, fields = _load_rows()
    morph = Morphology()
    idx = PhoneticIndex(morph); idx.build(idx_rows)
    matcher = StreetMatcher(morph, idx)
    matcher._initialized = True
    matcher._stopwords = _load_stopwords()
    tokenizer = RazdelTokenizer()

    # unique labelled surfaces across both datasets
    surfaces = set()
    for ds in _DATASETS:
        if not ds.exists():
            continue
        for text, ann in json.load(open(ds, encoding='utf-8')):
            for s, e, _ in ann.get('entities', []):
                sv = text[s:e].strip()
                if sv:
                    surfaces.add(sv)
    print(f"unique labelled surfaces: {len(surfaces)}")

    seen_ids = set()
    for sv in surfaces:
        t = tokenizer.tokenize(sv)
        if not t:
            continue
        l = morph.lemmatize_tokens(t)
        for ent in matcher.find_streets(tokens=t, lemmas=l):
            seen_ids.add(ent['street_id'])

    total = len(raw)
    kept = [rec for i, rec in enumerate(raw, start=1) if i in seen_ids]
    removed = [rec for i, rec in enumerate(raw, start=1) if i not in seen_ids]
    print(f"streets.csv rows:   {total}")
    print(f"seen (kept):        {len(kept)}")
    print(f"unseen (removed):   {len(removed)}")
    print("\nsample removed (canonical name):")
    for rec in removed[:30]:
        print(f"   - {rec['names'].split('|')[0]}")

    if not args.apply:
        print("\n(dry run — rerun with --apply to back up + rewrite streets.csv)")
        return

    if not _BACKUP.exists():
        _BACKUP.write_text(_STREETS_CSV.read_text(encoding='utf-8'), encoding='utf-8')
        print(f"\nbacked up original -> {_BACKUP}")
    with open(_STREETS_CSV, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(kept)
    print(f"✅ wrote pruned streets.csv: {len(kept)} rows (was {total})")


if __name__ == '__main__':
    main()
