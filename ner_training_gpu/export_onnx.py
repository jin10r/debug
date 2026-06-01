#!/usr/bin/env python3
"""Export the fine-tuned token-classification model to ONNX for CPU inference,
int8-quantize it, copy the fast tokenizer + label map, and smoke-test the
result using ONLY onnxruntime + tokenizers (no torch/transformers) — exactly
the runtime the parser will use.

Usage:
  python training/export_onnx.py [--model DIR] [--no-quant]
"""
from __future__ import annotations

import argparse
import json
import shutil

import numpy as np

from ner_common import LABELS, ONNX_DIR, OUTPUT_DIR, id2label, label2id


def export(model_dir, quant=True):
    # Low-level exporter (optimum.exporters) writes model.onnx + config.json
    # WITHOUT instantiating a torch-coupled ORTModel session — sidesteps the
    # onnxruntime/torch int4 dtype mismatch and matches the project's prior
    # scripts/export_onnx_model.py approach.
    from optimum.exporters.onnx import main_export
    from transformers import AutoTokenizer

    ONNX_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Exporting {model_dir} -> ONNX ({ONNX_DIR}) ...")
    main_export(
        model_name_or_path=str(model_dir),
        output=str(ONNX_DIR),
        task="token-classification",
        opset=18,                               # recommended min for BERT export
    )

    tok = AutoTokenizer.from_pretrained(model_dir)
    tok.save_pretrained(str(ONNX_DIR))                      # writes fast tokenizer.json
    (ONNX_DIR / "labels.json").write_text(
        json.dumps({"id2label": id2label, "label2id": label2id}, ensure_ascii=False),
        encoding="utf-8")

    if quant:
        try:
            # Pure onnxruntime dynamic int8 (no torch) — robust across versions.
            from onnxruntime.quantization import quantize_dynamic, QuantType
            print("int8 dynamic quantization ...")
            quantize_dynamic(
                model_input=str(ONNX_DIR / "model.onnx"),
                model_output=str(ONNX_DIR / "model_quantized.onnx"),
                weight_type=QuantType.QInt8,
                per_channel=True,
            )
            print("  -> model_quantized.onnx")
        except Exception as e:
            print(f"  ⚠ quantization failed ({e}); shipping fp32 model.onnx")


def decode_spans(offsets, label_ids):
    spans, cur = [], None
    for (cs, ce), lid in zip(offsets, label_ids):
        if cs == ce:
            continue
        lab = LABELS[lid]
        if lab == "B-LOC":
            if cur:
                spans.append(cur)
            cur = (cs, ce)
        elif lab == "I-LOC" and cur:
            cur = (cur[0], ce)
        else:
            if cur:
                spans.append(cur); cur = None
    if cur:
        spans.append(cur)
    return spans


def smoke_test():
    """Runtime-faithful test: onnxruntime + tokenizers only."""
    import onnxruntime as ort
    from tokenizers import Tokenizer

    onnx_file = ONNX_DIR / "model_quantized.onnx"
    if not onnx_file.exists():
        onnx_file = ONNX_DIR / "model.onnx"
    sess = ort.InferenceSession(str(onnx_file), providers=["CPUExecutionProvider"])
    tk = Tokenizer.from_file(str(ONNX_DIR / "tokenizer.json"))

    text = "Магазин Метро, проверка документов на Жукова - осторожно."
    enc = tk.encode(text)
    feed = {}
    want = {i.name for i in sess.get_inputs()}
    if "input_ids" in want:
        feed["input_ids"] = np.array([enc.ids], dtype=np.int64)
    if "attention_mask" in want:
        feed["attention_mask"] = np.array([enc.attention_mask], dtype=np.int64)
    if "token_type_ids" in want:
        feed["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)
    logits = sess.run(None, feed)[0]
    label_ids = logits[0].argmax(-1).tolist()
    spans = decode_spans(enc.offsets, label_ids)
    frags = [text[s:e] for s, e in spans]
    print(f"\n=== smoke test ===\n  text:  {text!r}\n  spans: {frags}")
    # Mechanics gate: inference must run and yield valid char-span tuples.
    assert isinstance(spans, list) and all(
        isinstance(s, tuple) and len(s) == 2 and 0 <= s[0] < s[1] <= len(text)
        for s in spans
    ), f"invalid span structure: {spans}"
    print("  ✅ onnxruntime inference OK (no torch/transformers)")
    # Quality signal (expected to pass only with a properly trained model).
    if any("Жукова" in f or f in "Жукова" for f in frags):
        print("  ✅ found expected 'Жукова' span")
    else:
        print("  ⚠ 'Жукова' not detected — fine for an undertrained dry-run model, "
              "but a fully trained model should find it")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(OUTPUT_DIR))
    ap.add_argument("--no-quant", action="store_true")
    ap.add_argument("--skip-export", action="store_true", help="only run smoke test")
    args = ap.parse_args()

    if not args.skip_export:
        export(args.model, quant=not args.no_quant)
    smoke_test()
    print(f"\n✅ inference artifact ready -> {ONNX_DIR}")
    print(f"   files: {sorted(p.name for p in ONNX_DIR.iterdir())}")


if __name__ == "__main__":
    main()
