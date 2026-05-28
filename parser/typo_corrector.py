"""SymSpell pre-correction: исправляет опечатки в токенах ДО pymorphy3.

Проблема: pymorphy3 галлюцинирует леммы для редких proper nouns. Например
«поустовского» (опечатка от «Паустовского», edit_distance=1) → лемма
«поустовскто» с тегом NPRO — мусорная форма, которую rapidfuzz уже не
может матчить с alias «паустовский».

Решение: SymSpell (Symmetric Delete spelling correction) с алфавитом из
индексированных alias-фраз. Pre-correction чинит токены ДО pymorphy3.

Архитектура pipeline:
    razdel.tokenize → TypoCorrector.correct → Morphology.lemmatize → ...

Index size: ~1000 streets × ~2 alias-фраз × ~1.5 слов = ~3000 уникальных
слов. SymSpell с edit_distance=2 → ~30K-50K delete-variants (~5MB RAM).
Lookup ~0.1ms на токен.
"""

import logging
from typing import List, Optional, Set

from .razdel_tokenizer import Token

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)


class TypoCorrector:
    """SymSpell-based pre-correction для известных топонимов streets."""

    def __init__(self) -> None:
        self._initialized = False
        self._sym = None  # SymSpell instance, lazy-imported
        self._dictionary: Set[str] = set()
        self._max_edit_distance = 2
        self._min_word_length = 4

    def initialize(self, alias_phrases: List[str]) -> bool:
        """Построить SymSpell-словарь из всех слов alias-фраз streets.

        Args:
            alias_phrases: оригинальные имена улиц/объектов из БД, например
                ['Малая Арнаутская', 'Преображенская', 'пр. Шевченко', ...].

        Returns:
            True если SymSpell успешно построен. False — корректор остаётся
            disabled и `correct()` возвращает входные токены как есть.
        """
        try:
            from symspellpy import SymSpell
        except ImportError as exc:
            logger.warning(f"[TypoCorrector] symspellpy not installed ({exc}); disabled")
            return False

        if settings and settings.similarity:
            sim = settings.similarity
            self._max_edit_distance = getattr(sim, 'typo_correction_max_edit_distance', 2)
            self._min_word_length = getattr(sim, 'typo_correction_min_word_length', 4)

        try:
            sym = SymSpell(
                max_dictionary_edit_distance=self._max_edit_distance,
                prefix_length=7,
            )
            words: Set[str] = set()
            for phrase in alias_phrases:
                if not phrase:
                    continue
                for word in phrase.lower().split():
                    # Игнорируем короткие слова (3-буквенные служебные «ул», «пр»,
                    # «пер» дают много ложных коррекций).
                    if len(word) >= self._min_word_length and not word.isdigit():
                        words.add(word)
            for w in words:
                sym.create_dictionary_entry(w, 1)

            self._sym = sym
            self._dictionary = words
            self._initialized = True
            logger.info(
                f"[TypoCorrector] Ready: {len(words)} unique words indexed, "
                f"max_ed={self._max_edit_distance}, min_len={self._min_word_length}"
            )
            return True
        except Exception as exc:
            logger.warning(f"[TypoCorrector] Init failed: {exc}")
            return False

    def correct(self, tokens: List[Token]) -> List[Token]:
        """Заменить токены на ближайший alias-вариант, если расстояние ≤ N.

        Если корректор не инициализирован или включён через настройку — возвращает
        входные токены неизменно (defensive fallback).
        """
        if not self._initialized:
            return tokens
        # Глобальное отключение через env / settings — для быстрого rollback
        if settings and settings.similarity:
            enabled = getattr(settings.similarity, 'typo_correction_enabled', True)
            if not enabled:
                return tokens

        result: List[Token] = []
        for t in tokens:
            corrected = self._maybe_correct(t.text)
            if corrected and corrected != t.text.lower():
                result.append(Token(text=corrected, start=t.start, stop=t.stop))
            else:
                result.append(t)
        return result

    def _maybe_correct(self, word: str) -> Optional[str]:
        """Корректировка одного слова. None если изменение не нужно."""
        lowered = word.lower()
        if len(lowered) < self._min_word_length or lowered.isdigit():
            return None
        # Если слово уже в словаре известных алиасов — не трогаем
        if lowered in self._dictionary:
            return None
        try:
            from symspellpy import Verbosity
            suggestions = self._sym.lookup(
                lowered,
                Verbosity.TOP,
                max_edit_distance=self._max_edit_distance,
                include_unknown=False,
            )
        except Exception as exc:
            logger.debug(f"[TypoCorrector] lookup failed for {lowered!r}: {exc}")
            return None

        if not suggestions:
            return None
        best = suggestions[0]
        # Safety: убеждаемся что distance в допустимом диапазоне
        if best.distance > self._max_edit_distance or best.distance == 0:
            return None
        return best.term
