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
Теги '#' на классификацию не влияют — '#' удаляется в preprocess_light, слой
определяется только по тексту (леммам), а не по тому, что автор пометил тегом.
"""

import logging
from typing import Dict, List, Set

from .morphology import Lemma, Morphology

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# ── Layer keywords — канонические словоформы (не стемы) ──────────────────────
# Лемматизируются при инициализации LayerClassifier, поэтому все
# падежи/числа словоформ совпадают автоматически.
#
# Порядок ключей задаёт приоритет классификации: первый совпавший слой
# выигрывает (ниже в classify()). 'pig' — fallback без ключей.
DEFAULT_LAYER_KEYWORDS: dict[str, tuple] = {
    'bus': (
        'автобус',
        'бус',
        'хайс',
        'спринтер',
        'рено',
        'фольксваген',
        'фольц',
        'хёндай',
        'Хундай',
        'вито',
        'сталкер',
        'транспортёр',
        'h1', 'h2', 'h3', 'h4', 'h5',
        'т5', 'т4', 'т3', 'т2', 'т1',
        'н1', 'н2', 'н3', 'н4', 'н5',
        # pymorphy лемматизирует «бус»→«бусы», но «буса»/«бусик»→самостоятельные
        # леммы ⇒ косвенные/слэнговые формы не совпадали. Добавлены явно.
        'буса', 'бусик', 'бусинка',
    ),
    'cops': (
        'коп',
        'полиция',
        'мусор',
        'мусара',
        'люстра',
        'мигалка',
        'патруль',
        'экипаж',
        'мент',
        'менты',
        'менти',
        'полицейский',
        'полицай',
        'police',
        'мусорня',
        'мусорской',
        'сирена',
    ),
    'traffic': (
        'дтп',
        'авария',
        'пробка',
        'затор',
        'светофор',
        'блокпост',
        'пост',
        'бп',
        'б/п'
    ),
    'pig': (),
}

# Порядок приоритета (исключая fallback 'pig').
LAYER_PRIORITY: tuple = tuple(k for k in DEFAULT_LAYER_KEYWORDS if k != 'pig')


def _get_layer_keywords(layer: str) -> tuple:
    """Ключевые слова слоя: DEFAULT_LAYER_KEYWORDS (этот модуль)."""
    return DEFAULT_LAYER_KEYWORDS.get(layer, ())


class LayerClassifier:
    """Морфологический классификатор слоя события."""

    def __init__(self, morph: Morphology) -> None:
        """morph — Morphology обёртка (общий MorphAnalyzer на процесс)."""
        self._morph = morph
        # {layer: множество лемматизированных ключевых слов}
        self._keyword_lemmas: Dict[str, Set[str]] = {}
        for layer in LAYER_PRIORITY:
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

    def classify(self, lemmas: List[Lemma]) -> str:
        """Слой по приоритету bus → cops → traffic, иначе 'pig'.

        Принимает уже лемматизированные токены (от Morphology.lemmatize_tokens).
        Слой определяется только по совпадению лемм с ключевыми словами слоёв.
        """
        if not lemmas:
            return 'pig'

        token_lemmas: Set[str] = {l.normal_form for l in lemmas if l.normal_form}

        for layer in LAYER_PRIORITY:
            if self._keyword_lemmas[layer] & token_lemmas:
                return layer

        # Fuzzy fallback: for 'pig' results, try fuzzy-matching
        # original surfaces against keyword lemmas (catches typos).
        from rapidfuzz import fuzz
        token_surfaces = {l.surface.lower() for l in lemmas if l.surface}
        for layer in LAYER_PRIORITY:
            for kw_lemma in self._keyword_lemmas[layer]:
                for token_surface in token_surfaces:
                    if fuzz.ratio(kw_lemma, token_surface) >= 85:
                        return layer

        return 'pig'
