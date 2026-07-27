"""SemanticMatcher — ONNX rubert-tiny2 для валидации кандидатов в серой зоне."""

import logging
from pathlib import Path
from typing import List, Dict, Optional

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger(__name__)

FUZZY_CONFIDENT_THRESHOLD = 0.85
FUZZY_GRAY_ZONE_LOW = 0.70
SEMANTIC_ACCEPT_THRESHOLD = 0.75


class SemanticMatcher:
    def __init__(self, model_dir: str = "/app/models/rubert-tiny2-onnx"):
        self._model_dir = Path(model_dir)
        self._session: Optional[ort.InferenceSession] = None
        self._tokenizer: Optional[Tokenizer] = None
        self._geo_embeddings: Optional[np.ndarray] = None
        self._geo_meta: Optional[List[Dict]] = None

    def initialize(self, geo_data: List[Dict]) -> None:
        logger.info(f"Loading ONNX model from {self._model_dir}")

        model_path = self._model_dir / "model.onnx"
        if not model_path.exists():
            raise FileNotFoundError(f"ONNX model not found: {model_path}")

        self._session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )

        tokenizer_path = self._model_dir / "tokenizer.json"
        if not tokenizer_path.exists():
            raise FileNotFoundError(f"Tokenizer not found: {tokenizer_path}")

        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_padding(length=64)
        self._tokenizer.enable_truncation(max_length=64)

        self._precompute_geo_embeddings(geo_data)

        logger.info(
            f"SemanticMatcher ready: {len(self._geo_embeddings)} aliases indexed, "
            f"model={self._session.get_inputs()[0].shape}"
        )

    def _precompute_geo_embeddings(self, geo_data: List[Dict]) -> None:
        texts = []
        meta = []

        for geo in geo_data:
            geo_id = geo["id"]
            names = geo.get("names", [])
            for idx, name in enumerate(names):
                texts.append(name.lower())
                meta.append({
                    "geo_id": geo_id,
                    "name": name,
                    "alias_idx": idx,
                })

        logger.info(f"Precomputing embeddings for {len(texts)} aliases...")

        batch_size = 256
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_embeddings = self._embed_batch(batch_texts)
            all_embeddings.append(batch_embeddings)

        embeddings = np.vstack(all_embeddings)

        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        self._geo_embeddings = embeddings / np.maximum(norms, 1e-9)
        self._geo_meta = meta

        logger.info(f"Precomputed {len(self._geo_embeddings)} embeddings")

    def _embed_batch(self, texts: List[str]) -> np.ndarray:
        encodings = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            }
        )

        return outputs[0][:, 0, :]

    def _embed_single(self, text: str) -> np.ndarray:
        enc = self._tokenizer.encode(text)
        input_ids = np.array([enc.ids], dtype=np.int64)
        attention_mask = np.array([enc.attention_mask], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            }
        )

        emb = outputs[0][:, 0, :]
        norm = np.linalg.norm(emb)
        return emb / max(norm, 1e-9)

    def filter_candidates(
        self,
        candidates: List[Dict],
        window_text: str,
    ) -> List[Dict]:
        if not candidates:
            return candidates

        accepted = []
        gray_zone = []

        for cand in candidates:
            score = cand.get("score", 0.0)
            if score >= FUZZY_CONFIDENT_THRESHOLD:
                accepted.append(cand)
            elif score >= FUZZY_GRAY_ZONE_LOW:
                gray_zone.append(cand)

        if not gray_zone:
            return accepted

        query_emb = self._embed_single(window_text.lower())

        for cand in gray_zone:
            cand_emb = self._find_alias_embedding(cand)
            if cand_emb is None:
                logger.warning(
                    f"No precomputed embedding for candidate: {cand.get('matched_name')}"
                )
                continue

            similarity = float(np.dot(query_emb, cand_emb))

            if similarity >= SEMANTIC_ACCEPT_THRESHOLD:
                cand["semantic_score"] = similarity
                cand["source"] = cand.get("source", "") + "+semantic"
                accepted.append(cand)
            else:
                logger.debug(
                    f"Semantic reject: '{window_text}' vs '{cand.get('matched_name')}' "
                    f"(fuzzy={cand['score']:.2f}, semantic={similarity:.2f})"
                )

        logger.debug(
            f"[SemanticFilter] {len(candidates)} candidates -> {len(accepted)} accepted "
            f"({len(gray_zone)} checked by model)"
        )

        return accepted

    def _find_alias_embedding(self, cand: Dict) -> Optional[np.ndarray]:
        if self._geo_meta is None or self._geo_embeddings is None:
            return None

        matched = (cand.get("matched_name") or "").lower()

        for i, meta in enumerate(self._geo_meta):
            if meta["name"].lower() == matched:
                return self._geo_embeddings[i]

        geo_id = cand.get("geo_id")
        for i, meta in enumerate(self._geo_meta):
            if meta["geo_id"] == geo_id:
                return self._geo_embeddings[i]

        return None

    def close(self) -> None:
        if self._session:
            del self._session
            self._session = None
        if self._tokenizer:
            del self._tokenizer
            self._tokenizer = None
        self._geo_embeddings = None
        self._geo_meta = None
        logger.info("SemanticMatcher closed")
