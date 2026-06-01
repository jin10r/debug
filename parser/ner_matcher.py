"""NER Matcher — LOC-спан детектор на rubert-tiny2 ONNX.

Лёгкий CPU-инференс БЕЗ torch/transformers: только onnxruntime + tokenizers.
Заменяет n-gram-перебор в StreetMatcher: модель сразу указывает спаны улиц,
а привязка спан→street_id остаётся за существующими phonetic/lemma-проходами.

Паттерн (singleton + CPU-оптимизированный SessionOptions) — как у бывшего
parser/embedder.py. Деградирует мягко: нет модели → predict_spans вернёт [].
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Дефолтная директория артефакта (относительно /app в контейнере или repo-root).
_DEFAULT_MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "ner_loc_onnx"


class NERMatcher:
    """ONNX token-classification (BIO) → char-спаны LOC-сущностей."""

    def __init__(
        self,
        model_dir: Optional[str] = None,
        onnx_filename: str = "model_quantized.onnx",
        max_seq_length: int = 256,
        intra_op_threads: int = 2,
        inter_op_threads: int = 1,
    ) -> None:
        self._model_dir = Path(model_dir) if model_dir else _DEFAULT_MODEL_DIR
        self._onnx_filename = onnx_filename
        self._max_seq_length = max_seq_length
        self._intra_op = intra_op_threads
        self._inter_op = inter_op_threads
        self._session = None
        self._tokenizer = None
        self._input_names: List[str] = []
        self._id2label: dict = {}
        self._initialized = False

    def initialize(self) -> bool:
        """Загрузить ONNX-модель, токенизатор и label-map. Идемпотентно."""
        if self._initialized:
            return True
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as e:
            logger.error(f"[NER] onnxruntime/tokenizers not available: {e}")
            return False

        # Предпочитаем квантованную модель, иначе fp32.
        model_path = self._model_dir / self._onnx_filename
        if not model_path.exists():
            model_path = self._model_dir / "model.onnx"
        tok_path = self._model_dir / "tokenizer.json"
        labels_path = self._model_dir / "labels.json"

        if not model_path.exists() or not tok_path.exists():
            logger.error(
                f"[NER] model/tokenizer not found in {self._model_dir} "
                f"(model={model_path.exists()}, tok={tok_path.exists()})"
            )
            return False

        try:
            self._tokenizer = Tokenizer.from_file(str(tok_path))
            self._tokenizer.enable_truncation(max_length=self._max_seq_length)

            id2label = json.loads(labels_path.read_text(encoding="utf-8"))["id2label"]
            self._id2label = {int(k): v for k, v in id2label.items()}

            so = ort.SessionOptions()
            so.intra_op_num_threads = self._intra_op
            so.inter_op_num_threads = self._inter_op
            so.enable_mem_pattern = False        # экономия RAM
            so.enable_cpu_mem_arena = False       # экономия RAM
            self._session = ort.InferenceSession(
                str(model_path), so, providers=["CPUExecutionProvider"]
            )
            self._input_names = [i.name for i in self._session.get_inputs()]
            self._initialized = True
            logger.info(
                f"[NER] initialized: model={model_path.name}, "
                f"inputs={self._input_names}, labels={self._id2label}"
            )
            return True
        except Exception as e:
            logger.error(f"[NER] init failed: {e}")
            return False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def predict_spans(self, text: str) -> List[Tuple[int, int]]:
        """Вернуть char-спаны [(start, stop), ...] LOC-сущностей в тексте.

        Мягкая деградация: не инициализирован / пустой текст → [].
        """
        if not self._initialized or not text:
            return []
        try:
            enc = self._tokenizer.encode(text)
            ids = enc.ids
            if not ids:
                return []
            feed = {}
            if "input_ids" in self._input_names:
                feed["input_ids"] = np.array([ids], dtype=np.int64)
            if "attention_mask" in self._input_names:
                feed["attention_mask"] = np.array([enc.attention_mask], dtype=np.int64)
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.array([enc.type_ids], dtype=np.int64)

            logits = self._session.run(None, feed)[0]      # (1, seq, n_labels)
            label_ids = logits[0].argmax(-1).tolist()
            return self._decode_bio(enc.offsets, label_ids)
        except Exception as e:
            logger.warning(f"[NER] inference error: {e}")
            return []

    def _decode_bio(self, offsets, label_ids) -> List[Tuple[int, int]]:
        """BIO + субворд-офсеты → объединённые char-спаны."""
        spans: List[Tuple[int, int]] = []
        cur: Optional[List[int]] = None
        for (cs, ce), lid in zip(offsets, label_ids):
            if cs == ce:                          # спецтокен ([CLS]/[SEP]/pad)
                continue
            lab = self._id2label.get(lid, "O")
            if lab == "B-LOC":
                if cur:
                    spans.append((cur[0], cur[1]))
                cur = [cs, ce]
            elif lab == "I-LOC" and cur is not None:
                cur[1] = ce
            else:                                 # O или висячий I- без открытого спана
                if cur:
                    spans.append((cur[0], cur[1]))
                    cur = None
        if cur:
            spans.append((cur[0], cur[1]))
        return spans


# --- singleton ---------------------------------------------------------------
_ner_matcher: Optional[NERMatcher] = None


def get_ner_matcher(**kwargs) -> NERMatcher:
    """Глобальный экземпляр NERMatcher (ленивая инициализация конфигом)."""
    global _ner_matcher
    if _ner_matcher is None:
        _ner_matcher = NERMatcher(**kwargs)
    return _ner_matcher
