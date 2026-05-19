"""Определение слоя события по ключевым словам с морфологической нормализацией.

Раньше слой определялся жёстким substring-match (`word.startswith(keyword)`),
что давало ложные срабатывания (`пост` ловил `постель`) и не учитывало
словоформы. Здесь и ключевые слова, и токены сообщения приводятся к начальной
форме через mawo_pymorphy3 — поэтому `патрулём`, `патруля`, `патрули` одинаково
матчатся с ключом `патруль`. Коды и аббревиатуры (`h1`-`h5`, `бп`, `дтп`)
лемматизация не меняет — они матчатся как есть.

Приоритет при совпадении ключей из разных слоёв: bus → cops → traffic → pig.
"""

import logging
from typing import Dict, Set

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Порядок задаёт приоритет: первый совпавший слой выигрывает.
_LAYER_PRIORITY = ('bus', 'cops', 'traffic')


def _get_layer_keywords(layer: str) -> tuple:
    """Ключевые слова слоя из настроек (БД или fallback из core/settings)."""
    if settings and settings.similarity:
        return settings.similarity.get_layer_keywords(layer)
    return ()


class LayerClassifier:
    """Морфологический классификатор слоя события."""

    def __init__(self, morph) -> None:
        """morph — общий экземпляр mawo_pymorphy3.MorphAnalyzer (см. LexicalMatcher)."""
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
        """Начальная форма слова; для неизвестных слов и кодов — само слово."""
        word = word.strip().lower()
        if not word:
            return ''
        parses = self._morph.parse(word)
        return parses[0].normal_form if parses else word

    def classify(self, cleaned_text: str) -> str:
        """Вернуть слой по приоритету bus → cops → traffic, иначе 'pig'."""
        if not cleaned_text:
            return 'pig'

        token_lemmas: Set[str] = {
            self._lemma(token) for token in cleaned_text.split()
        }

        for layer in _LAYER_PRIORITY:
            if self._keyword_lemmas[layer] & token_lemmas:
                return layer

        return 'pig'
