"""NERExtractor — извлечение LOC-сущностей через natasha NewsNERTagger.

Используется оригинальный `natasha` (не mawo-natasha): проверка показала,
что mawo-natasha не может скачать Navec embeddings (HTTP 404) и без них
NER выдаёт мусорные классификации ("Москва → PER"). См. docs/mawo_parser_plan.md.

Стратегия: запускать NER на тексте, сохранившем регистр и пунктуацию
(после preprocess_light), извлекать только LOC-сущности и передавать их
в street_matcher как высокоприоритетных кандидатов.

Graceful degrade: если natasha не доступен (сетевые проблемы при загрузке
моделей, отсутствие зависимости), `initialize()` возвращает False, а
`extract()` всегда возвращает []. Парсер продолжает работать через T2/T3
стратегии (proper-noun n-граммы и полнотекстовый fallback).
"""

import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class Span:
    """LOC-спан с позицией в исходной строке."""
    text: str
    start: int
    stop: int


class NERExtractor:
    """Wrapper над natasha NewsNERTagger для LOC-сущностей."""

    def __init__(self) -> None:
        self._initialized = False
        self._Doc = None
        self._segmenter = None
        self._ner = None

    def initialize(self) -> bool:
        """Lazy-load моделей natasha + Navec embeddings.

        Возвращает True при успехе, False при любой ошибке (отсутствие пакета,
        сетевые проблемы, OOM). Никогда не выбрасывает исключение наружу.
        """
        try:
            from natasha import Segmenter, NewsEmbedding, NewsNERTagger, Doc
        except ImportError as exc:
            logger.warning(f"[NER] natasha не установлен ({exc}) — degraded mode")
            return False

        try:
            emb = NewsEmbedding()  # тянет Navec ~50 МБ при первом запуске
            self._segmenter = Segmenter()
            self._ner = NewsNERTagger(emb)
            self._Doc = Doc
            self._initialized = True
            logger.info("[NER] NewsNERTagger загружен (natasha + navec)")
            return True
        except Exception as exc:
            logger.warning(f"[NER] Init failed ({exc}) — degraded mode")
            return False

    def extract(self, text: str) -> List[Span]:
        """Список LOC-спанов из текста. Возвращает [] при degraded mode."""
        if not self._initialized or not text:
            return []
        try:
            doc = self._Doc(text)
            doc.segment(self._segmenter)
            doc.tag_ner(self._ner)
            return [
                Span(text=s.text, start=s.start, stop=s.stop)
                for s in doc.spans
                if s.type == 'LOC'
            ]
        except Exception as exc:
            logger.debug(f"[NER] extract failed: {exc}")
            return []

    @property
    def is_available(self) -> bool:
        """True если NER загружен и готов извлекать спаны."""
        return self._initialized
