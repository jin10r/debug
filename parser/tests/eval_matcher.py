"""Eval-харнесс матчера улиц — precision/recall по street + layer-accuracy.

Грузит РЕАЛЬНЫЙ газеттир `postgres/data/streets.csv` + стоп-слова
`postgres/data/stopwords.csv`, строит индекс ровно как продакшен, прогоняет
golden-set (`parser/tests/golden/cases.jsonl`) через ту же цепочку
предобработки, что и `MessageProcessor.process_message`, и считает метрики.

Сопоставление по КАНОНИЧЕСКОМУ ИМЕНИ улицы (`names[0]`), а не по street_id:
в БД `ON CONFLICT DO NOTHING` смещает SERIAL-id относительно номера строки CSV,
поэтому id нестабилен, а имя — стабильно.

Запуск:
    python -m parser.tests.eval_matcher
    python -m parser.tests.eval_matcher --verbose   # построчный diff
"""

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

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from parser.layer_classifier import LayerClassifier
from parser.morphology import Morphology
from parser.phonetic_index import PhoneticIndex
from parser.street_matcher import StreetMatcher
from parser.text_preprocessor import preprocess_light, strip_emoji, strip_tail
from parser.word_tokenizer import tokenize

_STREETS_CSV = _REPO / 'postgres' / 'data' / 'streets.csv'
_STOPWORDS_CSV = _REPO / 'postgres' / 'data' / 'stopwords.csv'
_GOLDEN = Path(__file__).resolve().parent / 'golden' / 'cases.jsonl'


def _load_streets():
    """streets.csv → [{'id', 'names'}], names split по '|' (как string_to_array)."""
    rows = []
    with open(_STREETS_CSV, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for i, rec in enumerate(reader, start=1):
            names = [n.strip() for n in (rec['names'] or '').split('|') if n.strip()]
            if names:
                rows.append({'id': i, 'names': names})
    return rows


def _load_stopwords():
    with open(_STOPWORDS_CSV, encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        return {row[0].strip().lower() for row in reader if row and row[0].strip()}


def _load_golden():
    cases = []
    with open(_GOLDEN, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('//'):
                cases.append(json.loads(line))
    return cases


def build_matcher():
    morph = Morphology()
    idx = PhoneticIndex(morph)
    idx.build(_load_streets())
    matcher = StreetMatcher(morph, idx)
    matcher._initialized = True
    matcher._stopwords = _load_stopwords()
    classifier = LayerClassifier(morph)
    return matcher, morph, classifier


def run_case(text, matcher, morph, classifier):
    """Воспроизводит цепочку MessageProcessor.process_message (matching-ветка)."""
    stripped = strip_tail(text or '')
    preserved = preprocess_light(stripped) or ''
    match_text = strip_emoji(preserved)
    tokens = tokenize(match_text)
    lemmas = morph.lemmatize_tokens(tokens)
    layer = classifier.classify(lemmas)
    entities = matcher.find_streets(tokens=tokens, lemmas=lemmas)
    return {
        'layer': layer,
        'streets': [e['matched_name'] for e in entities],
        'raw': entities,
    }


def evaluate(verbose=False):
    matcher, morph, classifier = build_matcher()
    cases = _load_golden()

    tp = fp = fn = 0
    layer_ok = 0
    rows = []
    for c in cases:
        expected = set(c.get('expected_streets', []))
        got = run_case(c['text'], matcher, morph, classifier)
        got_set = set(got['streets'])

        case_tp = len(expected & got_set)
        case_fp = len(got_set - expected)
        case_fn = len(expected - got_set)
        tp += case_tp
        fp += case_fp
        fn += case_fn

        exp_layer = c.get('expected_layer')
        layer_match = (exp_layer is None) or (got['layer'] == exp_layer)
        if layer_match:
            layer_ok += 1

        clean = case_fp == 0 and case_fn == 0 and layer_match
        rows.append((c, expected, got, case_fp, case_fn, layer_match, clean))

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    layer_acc = layer_ok / len(cases) if cases else 1.0

    if verbose:
        for c, expected, got, cfp, cfn, lmatch, clean in rows:
            if clean:
                continue
            print(f"\n[msg {c.get('message_id')}] {c['text'][:80]}")
            print(f"  expected streets: {sorted(expected)}")
            print(f"  got streets:      {got['streets']}")
            if cfp:
                print(f"  FP: {sorted(set(got['streets']) - expected)}")
            if cfn:
                print(f"  FN: {sorted(expected - set(got['streets']))}")
            if not lmatch:
                print(f"  LAYER: expected {c.get('expected_layer')} got {got['layer']}")

    print("\n" + "=" * 56)
    print(f"cases:            {len(cases)}")
    print(f"street TP/FP/FN:  {tp} / {fp} / {fn}")
    print(f"precision:        {precision:.3f}")
    print(f"recall:           {recall:.3f}")
    print(f"f1:               {f1:.3f}")
    print(f"layer accuracy:   {layer_acc:.3f}  ({layer_ok}/{len(cases)})")
    print("=" * 56)
    return {
        'precision': precision, 'recall': recall, 'f1': f1,
        'layer_accuracy': layer_acc, 'tp': tp, 'fp': fp, 'fn': fn,
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--verbose', '-v', action='store_true', help='построчный diff')
    args = ap.parse_args()
    evaluate(verbose=args.verbose)
