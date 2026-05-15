"""ONNX Embedder — кодирование текста в векторы через rubert-tiny2 ONNX.

Легковесный модуль для инференса на CPU без зависимости от PyTorch.
Использует onnxruntime + tokenizers для быстрого кодирования текста.
"""

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger(__name__)

# Размерность вектора rubert-tiny2
EMBEDDING_DIM = 312

# Максимальная длина последовательности (как у rubert-tiny2)
MAX_SEQ_LENGTH = 512


class RuBertEmbedder:
    """ONNX-эмбеддер на базе rubert-tiny2.
    
    Кодирует текст в 312-мерные векторы для семантического поиска.
    Поддерживает кэширование и батчинг для оптимизации.
    """

    def __init__(self, model_dir: Optional[str] = None):
        self._model_dir = Path(model_dir) if model_dir else Path(__file__).parent.parent / "rubert-tiny2-onnx"
        self._session: Optional[ort.InferenceSession] = None
        self._tokenizer: Optional[Tokenizer] = None
        self._input_names: List[str] = []
        self._output_name: Optional[str] = None
        self._initialized = False

    async def initialize(self, model_dir: Optional[str] = None) -> bool:
        """Загрузить модель и токенизатор.
        
        Args:
            model_dir: Путь к директории с model.onnx и tokenizer.json
            
        Returns:
            True если инициализация успешна
        """
        try:
            target_dir = Path(model_dir) if model_dir else self._model_dir
            
            model_path = target_dir / "model.onnx"
            tokenizer_path = target_dir / "tokenizer.json"
            
            if not model_path.exists():
                logger.error(f"Model file not found: {model_path}")
                return False
            
            if not tokenizer_path.exists():
                logger.error(f"Tokenizer file not found: {tokenizer_path}")
                return False
            
            # Загрузка токенизатора
            logger.info(f"Loading tokenizer from {tokenizer_path}...")
            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            
            # Загрузка ONNX модели
            logger.info(f"Loading ONNX model from {model_path}...")
            
            # Оптимизация для CPU (уменьшение потребления RAM)
            sess_options = ort.SessionOptions()
            sess_options.intra_op_num_threads = 2
            sess_options.inter_op_num_threads = 1
            sess_options.enable_mem_pattern = False  # Экономия RAM
            sess_options.enable_cpu_mem_arena = False  # Экономия RAM
            
            providers = [
                ("CPUExecutionProvider", {
                    "arena_extend_strategy": "kSameAsRequested",
                })
            ]
            
            self._session = ort.InferenceSession(
                str(model_path),
                sess_options,
                providers=providers,
            )
            
            self._input_names = [inp.name for inp in self._session.get_inputs()]
            self._output_name = self._session.get_outputs()[0].name
            
            self._initialized = True
            logger.info(
                f"✅ RuBertEmbedder initialized: "
                f"dim={EMBEDDING_DIM}, inputs={self._input_names}"
            )
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize RuBertEmbedder: {e}")
            return False

    def encode(self, texts: List[str]) -> np.ndarray:
        """Кодировать список текстов в векторы.
        
        Args:
            texts: Список текстов для кодирования
            
        Returns:
            np.ndarray формы (len(texts), 312) — нормализованные векторы
        """
        if not self._initialized:
            logger.error("Embedder not initialized")
            return np.zeros((len(texts), EMBEDDING_DIM), dtype=np.float32)
        
        if not texts:
            return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
        
        # Токенизация
        self._tokenizer.enable_truncation(max_length=MAX_SEQ_LENGTH)
        encoded = self._tokenizer.encode_batch(
            texts,
            add_special_tokens=True,
        )
        self._tokenizer.no_truncation()  # Сброс после батча
        
        input_ids = [e.ids for e in encoded]
        attention_mask = [[1] * len(e.ids) for e in encoded]
        
        # Паддинг до максимальной длины в батче
        max_len = max(len(ids) for ids in input_ids)
        input_ids_padded = []
        attention_mask_padded = []
        
        for ids, mask in zip(input_ids, attention_mask):
            pad_len = max_len - len(ids)
            input_ids_padded.append(ids + [0] * pad_len)
            attention_mask_padded.append(mask + [0] * pad_len)
        
        # Подготовка входов для ONNX
        feed_dict = {
            "input_ids": np.array(input_ids_padded, dtype=np.int64),
            "attention_mask": np.array(attention_mask_padded, dtype=np.int64),
        }
        
        # Добавляем token_type_ids если модель ожидает
        if "token_type_ids" in self._input_names:
            feed_dict["token_type_ids"] = np.zeros_like(feed_dict["input_ids"])
        
        # Инференс
        outputs = self._session.run(
            [self._output_name],
            feed_dict,
        )
        
        # Mean pooling по токенам (с учётом attention mask)
        # outputs[0] имеет форму (batch, seq_len, hidden)
        last_hidden_state = outputs[0]
        mask = np.array(attention_mask_padded, dtype=np.float32)
        mask_expanded = mask[:, :, np.newaxis]
        
        # Суммируем эмбеддинги только для реальных токенов
        sum_embeddings = np.sum(last_hidden_state * mask_expanded, axis=1)
        sum_mask = np.sum(mask_expanded, axis=1)
        
        # Избегаем деления на ноль
        sum_mask = np.clip(sum_mask, a_min=1e-9, a_max=None)
        mean_embeddings = sum_embeddings / sum_mask
        
        # L2 нормализация (для косинусного сходства)
        norms = np.linalg.norm(mean_embeddings, axis=1, keepdims=True)
        norms = np.clip(norms, a_min=1e-9, a_max=None)
        normalized = mean_embeddings / norms

        return normalized.astype(np.float32)

    def encode_single(self, text: str) -> np.ndarray:
        """Кодировать один текст в вектор.
        
        Args:
            text: Текст для кодирования
            
        Returns:
            np.ndarray формы (312,) — нормализованный вектор
        """
        result = self.encode([text])
        return result[0] if len(result) > 0 else np.zeros(EMBEDDING_DIM, dtype=np.float32)

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def close(self):
        """Освободить ресурсы."""
        if self._session:
            del self._session
            self._session = None
        self._tokenizer = None
        self._initialized = False
        logger.info("RuBertEmbedder closed")


# Глобальный экземпляр (singleton)
_embedder: Optional[RuBertEmbedder] = None


def get_embedder() -> RuBertEmbedder:
    """Получить глобальный экземпляр эмбеддера."""
    global _embedder
    if _embedder is None:
        _embedder = RuBertEmbedder()
    return _embedder
