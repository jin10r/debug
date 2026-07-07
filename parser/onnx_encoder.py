"""OnnxEncoder — zero-shot sentence encoder via rubert-tiny2 ONNX int8.

Loads once at startup (~15MB), encodes context windows and type descriptions
for cosine-similarity zero-shot type prediction.

Usage:
    encoder = OnnxEncoder("models/rubert_tiny2_int8.onnx")
    encoder.warmup_types({"street": "городская улица, проспект, бульвар", ...})
    scores = encoder.probe("контекст вокруг улицы")
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import onnxruntime as ort
except ImportError:
    ort = None

logger = logging.getLogger(__name__)

# rubert-tiny2: 2-layer BERT, 312 hidden
_MODEL_DIM = 312
_MAX_SEQ_LEN = 64


def _mean_pool(last_hidden: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Mean pooling over non-padded tokens."""
    mask = attention_mask.astype(np.float32)[:, :, np.newaxis]
    masked = last_hidden * mask
    return masked.sum(axis=1) / mask.sum(axis=1).clip(min=1e-9)


def _normalize(emb: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(emb, axis=1, keepdims=True)
    return emb / norm.clip(min=1e-9)


class OnnxEncoder:
    """ONNX-based sentence encoder for zero-shot type validation."""

    def __init__(self, model_path: str) -> None:
        self._model_path = model_path
        self._session: Optional[ort.InferenceSession] = None
        self._tokenizer = None
        self._type_embeddings: Dict[str, np.ndarray] = {}
        self._ready = False

    async def initialize(self) -> bool:
        """Load ONNX model and tokenizer."""
        return await self._load_model()

    async def _load_model(self) -> bool:
        if ort is None:
            logger.warning("[ONNX] onnxruntime not installed")
            return False

        model_path = Path(self._model_path)
        if not model_path.exists():
            logger.warning(f"[ONNX] Model not found: {model_path}")
            return False

        try:
            opts = ort.SessionOptions()
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            opts.intra_op_num_threads = 2
            self._session = ort.InferenceSession(
                str(model_path), opts,
                providers=["CPUExecutionProvider"],
            )
            logger.info(f"[ONNX] Loaded {model_path}")

            from transformers import AutoTokenizer
            self._tokenizer = AutoTokenizer.from_pretrained("cointegrated/rubert-tiny2")
            self._ready = True
            return True
        except Exception as exc:
            logger.error(f"[ONNX] Load failed: {exc}")
            return False

    def encode(self, texts: List[str]) -> np.ndarray:
        """Encode texts to normalized sentence embeddings.

        Returns shape (len(texts), _MODEL_DIM).
        """
        if not self._ready or self._session is None or self._tokenizer is None:
            return np.zeros((len(texts), _MODEL_DIM), dtype=np.float32)

        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=_MAX_SEQ_LEN,
            return_tensors="np",
        )
        ort_inputs = {
            "input_ids": encoded["input_ids"].astype(np.int64),
            "attention_mask": encoded["attention_mask"].astype(np.int64),
        }
        outputs = self._session.run(["last_hidden_state"], ort_inputs)
        emb = _mean_pool(outputs[0], encoded["attention_mask"])
        return _normalize(emb)

    def warmup_types(self, type_descriptions: Dict[str, str]) -> None:
        """Precompute and cache embeddings for all geo type descriptions."""
        if not self._ready:
            return
        if not type_descriptions:
            return
        types = list(type_descriptions.keys())
        descriptions = list(type_descriptions.values())
        embs = self.encode(descriptions)
        self._type_embeddings = {t: embs[i] for i, t in enumerate(types)}
        logger.info(
            f"[ONNX] Warmed up {len(self._type_embeddings)} type descriptions"
        )

    def probe(self, context: str) -> Dict[str, float]:
        """Zero-shot type classification via cosine similarity.

        Returns dict of {type: score} sorted by score descending.
        Empty dict if not warmed up.
        """
        if not self._ready or not self._type_embeddings:
            return {}
        context_emb = self.encode([context])[0]
        scores = {
            t: float(np.dot(context_emb, self._type_embeddings[t]).item())
            for t in self._type_embeddings
        }
        return dict(sorted(scores.items(), key=lambda x: -x[1]))

    @property
    def is_ready(self) -> bool:
        return self._ready

    async def close(self) -> None:
        self._session = None
        self._tokenizer = None
        self._type_embeddings.clear()
        self._ready = False
        logger.info("[ONNX] Encoder closed")
