"""Определение слоя события по ключевым словам с морфологической нормализацией.

Раньше слой определялся жёстким substring-match (`word.startswith(keyword)`),
что давало ложные срабатывания (`пост` ловил `постель`) и не учитывало
словоформы. Сейчас и ключевые слова, и токены сообщения приводятся к
нормальной форме через mawo_pymorphy3 — поэтому `патрулём`, `патруля`,
`патрули` одинаково матчатся с ключом `патруль`. Коды и аббревиатуры
(`h1`-`h5`, `бп`, `дтп`) лемматизация не меняет — они матчатся как есть.

В новой архитектуре `classify()` принимает уже лемматизированный List[Lemma]
от Morphology (общая лемматизация для матчера и классификатора — единый
проход pymorphy3 на сообщение).

Приоритет при совпадении ключей из разных слоёв: bus → cops → traffic → pig.
Хэштег-токены (is_anchored=True из word_tokenizer) обрабатываются первыми:
если хэштег явно указывает слой, он выигрывает у soft-keywords других слоёв.
Пример: "##блокпост + полиция" → traffic (##блокпост explicit), а не cops.
"""

import logging
from typing import Dict, List, Optional, Sequence, Set

from .morphology import Lemma, Morphology
from .word_tokenizer import Token

try:
    from .settings import settings
    from .settings import LAYER_PRIORITY as _LAYER_PRIORITY
except Exception:
    settings = None
    _LAYER_PRIORITY = ('bus', 'cops', 'traffic')

logger = logging.getLogger(__name__)


def _get_layer_keywords(layer: str) -> tuple:
    """Ключевые слова слоя из настроек (БД или fallback из core/settings)."""
    if settings and settings.similarity:
        return settings.similarity.get_layer_keywords(layer)
    return ()


class LayerClassifier:
    """Морфологический классификатор слоя события."""

    def __init__(self, morph: Morphology) -> None:
        """morph — Morphology обёртка (общий MorphAnalyzer на процесс)."""
        self._morph = morph
        # {layer: множество лемматизированных ключевых слов}
        self._keyword_lemmas: Dict[str, Set[str]] = {}
        for layer in _LAYER_PRIORITY:
            self._keyword_lemmas[layer] = {
                self._lemma(kw) for kw in _get_layer_keywords(layer) if kw
            }
        logger.info(
            "[Layer] keyword lemmas: "
            + ", ".join(f"{l}={len(s)}" for l, s in self._keyword_lemmas.items())
        )

    def _lemma(self, word: str) -> str:
        """Начальная форма ключевого слова через Morphology."""
        word = word.strip().lower()
        if not word:
            return ''
        return self._morph.lemmatize_word(word).normal_form

    def classify(
        self,
        lemmas: List[Lemma],
        tokens: Optional[Sequence[Token]] = None,
    ) -> str:
        """Слой по приоритету bus → cops → traffic, иначе 'pig'.

        Принимает уже лемматизированные токены (от Morphology.lemmatize_tokens).
        Если передан список tokens, сначала проверяются только ##-хэштег-токены
        (is_anchored=True): совпадение хэштега с ключевым словом любого слоя
        даёт этот слой с наивысшим приоритетом, игнорируя soft-keywords.
        """
        if not lemmas:
            return 'pig'

        # Хэштег-override: явный сигнал пользователя бьёт мягкие ключевые слова.
        if tokens:
            anchored_lemmas: Set[str] = {
                self._lemma(t.text) for t in tokens if t.is_anchored
            }
            for layer in _LAYER_PRIORITY:
                if self._keyword_lemmas[layer] & anchored_lemmas:
                    return layer

        token_lemmas: Set[str] = {l.normal_form for l in lemmas if l.normal_form}

        for layer in _LAYER_PRIORITY:
            if self._keyword_lemmas[layer] & token_lemmas:
                return layer

        return 'pig'
