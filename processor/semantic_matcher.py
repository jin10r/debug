"""SemanticMatcher — ONNX rubert-tiny2 для валидации кандидатов в серой зоне."""

import logging
from pathlib import Path
from typing import List, Dict, Optional

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from processor.metrics import (
    semantic_checked_total,
    semantic_accepted_total,
    semantic_rejected_total,
    semantic_missing_embedding_total,
)

logger = logging.getLogger(__name__)

FUZZY_CONFIDENT_THRESHOLD = 0.85
FUZZY_GRAY_ZONE_LOW = 0.70
# Порог семантической близости полного текста сообщения к названию объекта.
# Значение берётся из settings (semantic_accept_threshold), здесь — fallback.
SEMANTIC_ACCEPT_THRESHOLD = 0.55


class SemanticMatcher:
    def __init__(self, model_dir: str = "/app/models/rubert-tiny2-onnx"):
        """Инициализация с путём к ONNX-модели."""
        self._model_dir = Path(model_dir)
        self._session: Optional[ort.InferenceSession] = None
        self._tokenizer: Optional[Tokenizer] = None
        self._geo_embeddings: Optional[np.ndarray] = None
        self._geo_meta: Optional[List[Dict]] = None
        # name.lower() -> индекс в _geo_meta/_geo_embeddings (O(1) вместо O(n))
        self._alias_index: Optional[Dict[str, int]] = None

    def initialize(self, geo_data: List[Dict]) -> None:
        """Загрузка модели, токенизатора и предрасчёт эмбеддингов geo-данных."""
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
        """Предрасчёт и нормализация эмбеддингов всех алиасов geo-объектов."""
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
        self._alias_index = {m['name'].lower(): i for i, m in enumerate(meta)}

        logger.info(f"Precomputed {len(self._geo_embeddings)} embeddings")

    def _embed_batch(self, texts: List[str]) -> np.ndarray:
        """Получение эмбеддингов для батча текстов через ONNX-модель."""
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
        """Получение нормализованного эмбеддинга одного текста."""
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

        # outputs[0] = (batch=1, seq_len, hidden) — берём [CLS]-токен первого батча.
        emb = outputs[0][0, 0, :]  # (hidden,) — 1D, как у предрасчитанных эмбеддингов
        norm = np.linalg.norm(emb)
        return emb / max(norm, 1e-9)

    def filter_candidates(
        self,
        candidates: List[Dict],
        message_text: str,
        semantic_threshold: Optional[float] = None,
    ) -> List[Dict]:
        """Валидация кандидатов серой зоны семантической моделью.

        ВАЖНО: вместо n-граммы окна передаётся ПОЛНЫЙ текст сообщения — модель
        решает, упомянут ли geo-объект в контексте сообщения (косинус между
        эмбеддингом всего текста и эмбеддингом названия объекта). Кандидаты с
        высоким fuzzy-скором (>= FUZZY_CONFIDENT_THRESHOLD, в т.ч. стем-exact)
        модель не трогает — они и так уверенные.
        """
        if not candidates:
            return candidates
        if not message_text:
            return candidates

        threshold = (
            semantic_threshold
            if semantic_threshold is not None else SEMANTIC_ACCEPT_THRESHOLD
        )

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

        query_emb = self._embed_single(message_text.lower())

        for cand in gray_zone:
            semantic_checked_total.inc()
            cand_emb = self._find_alias_embedding(cand, query_emb)
            if cand_emb is None:
                # Эмбеддинга нет (редкий кейс) — не молча дропаем кандидата:
                # отсутствие эмбеддинга ≠ отсутствие улицы. Консервативно
                # сохраняем кандидата (как accepted) с логом на DEBUG.
                semantic_missing_embedding_total.inc()
                logger.debug(
                    f"No embedding for {cand.get('matched_name')} — keeping "
                    f"candidate (fuzzy={cand['score']:.2f})"
                )
                accepted.append(cand)
                continue

            similarity = float(np.dot(query_emb, cand_emb))

            if similarity >= threshold:
                semantic_accepted_total.inc()
                cand["semantic_score"] = similarity
                cand["source"] = cand.get("source", "") + "+semantic"
                accepted.append(cand)
            else:
                semantic_rejected_total.inc()
                logger.debug(
                    f"Semantic reject: '{message_text[:60]}' vs "
                    f"'{cand.get('matched_name')}' "
                    f"(fuzzy={cand['score']:.2f}, semantic={similarity:.2f})"
                )

        logger.debug(
            f"[SemanticFilter] {len(candidates)} candidates -> {len(accepted)} accepted "
            f"({len(gray_zone)} checked by model)"
        )

        return accepted

    def _find_alias_embedding(
        self, cand: Dict, query_emb: Optional[np.ndarray] = None
    ) -> Optional[np.ndarray]:
        """Поиск предрасчитанного эмбеддинга для кандидата (O(1) через индекс)."""
        if self._geo_embeddings is None or self._alias_index is None:
            return None

        matched = (cand.get("matched_name") or "").lower()
        idx = self._alias_index.get(matched)
        if idx is not None:
            return self._geo_embeddings[idx]

        # Fallback: лучший алиас того же geo_id по косинусу с query. Кандидат из
        # матчера может ссылаться на canonical_name, которого нет в списке алиасов
        # (регистр/форма). Берём НЕ первый попавшийся алиас, а семантически
        # ближайший к тексту сообщения — иначе сравнение идёт с чужим именем.
        geo_id = cand.get("geo_id")
        if geo_id is None:
            return None
        best_emb: Optional[np.ndarray] = None
        best_sim = -1.0
        for i, meta in enumerate(self._geo_meta):
            if meta["geo_id"] != geo_id:
                continue
            if query_emb is None:
                return self._geo_embeddings[i]  # без query — любой алиас
            sim = float(np.dot(query_emb, self._geo_embeddings[i]))
            if sim > best_sim:
                best_sim = sim
                best_emb = self._geo_embeddings[i]
        return best_emb

    def close(self) -> None:
        """Освобождение ресурсов модели и токенизатора."""
        if self._session:
            del self._session
            self._session = None
        if self._tokenizer:
            del self._tokenizer
            self._tokenizer = None
        self._geo_embeddings = None
        self._geo_meta = None
        self._alias_index = None
        logger.info("SemanticMatcher closed")
