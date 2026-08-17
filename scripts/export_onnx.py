"""Export rubert-tiny2 to ONNX int8 for CPU inference.

Usage:
    python scripts/export_onnx.py

Requires: torch, transformers, optimum[onnxruntime]
Output: models/rubert_tiny2_int8.onnx (~15MB)
"""

import logging
import shutil
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MODEL_NAME = "cointegrated/rubert-tiny2"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "models"
ONNX_PATH = OUTPUT_DIR / "rubert_tiny2_int8.onnx"


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info(f"Loading {MODEL_NAME}...")
    from transformers import AutoTokenizer, AutoModel
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    model.eval()

    dummy_input = "тестовая строка для экспорта"
    onnx_input_path = OUTPUT_DIR / "rubert_tiny2.onnx"

    logger.info("Exporting to ONNX...")
    import torch
    with torch.no_grad():
        inputs = tokenizer(dummy_input, return_tensors="pt")
        torch.onnx.export(
            model,
            (inputs["input_ids"], inputs["attention_mask"]),
            onnx_input_path,
            input_names=["input_ids", "attention_mask"],
            output_names=["last_hidden_state", "pooler_output"],
            dynamic_axes={
                "input_ids": {0: "batch", 1: "seq"},
                "attention_mask": {0: "batch", 1: "seq"},
                "last_hidden_state": {0: "batch", 1: "seq"},
                "pooler_output": {0: "batch"},
            },
            opset_version=14,
        )

    logger.info(f"ONNX export done: {onnx_input_path}")

    logger.info("Quantizing to int8...")
    from onnxruntime.quantization import quantize_dynamic, QuantType
    quantize_dynamic(
        str(onnx_input_path),
        str(ONNX_PATH),
        weight_type=QuantType.QInt8,
    )

    size_mb = ONNX_PATH.stat().st_size / 1024 / 1024
    logger.info(f"Int8 ONNX model: {ONNX_PATH} ({size_mb:.1f} MB)")

    onnx_input_path.unlink(missing_ok=True)
    shutil.rmtree(OUTPUT_DIR / "transformers_cache", ignore_errors=True)

    logger.info("Done")


if __name__ == "__main__":
    main()
