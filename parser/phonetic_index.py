"""StreetIndex — индекс улиц: леммо-индекс + surface-индекс для fuzzy-матча.

Архитектура:
  • для каждого алиаса улицы строим:
    - леммо-кортеж (exact-match Tier 2)
    - лемматизированную фразу (fuzzy Tier 3)
    - сырую поверхностную форму (surface fuzzy Tier 1)
  • Tier 1 — rapidfuzz напрямую по surface-тексту кандидата против списка сырых алиасов;
    ловит опечатки в 1-2 буквы без фонетики (чепаевская → чапаевская = 90%).
  • Tier 2 — O(1) exact lemma tuple lookup.
  • Tier 3 — rapidfuzz по лемматизированным фразам; ловит падежные варианты.

Класс называется PhoneticIndex для обратной совместимости (используется в тестах и
street_matcher.py под этим именем).
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .morphology import Morphology
from .text_preprocessor import clean
from .word_tokenizer import tokenize

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Content POS — слова, которые имеет смысл лемматизировать полноценно.
_CONTENT_POS = frozenset({'NOUN', 'ADJF', 'ADJS', 'NUMR'})


@dataclass(frozen=True)
class PhoneticEntry:
    """Запись индекса: улица + одно из её каноничных названий + строка-вариант."""
    street_id: int
    canonical_name: str   # первое значение из streets.names — для UI/логов
    variant_text: str     # лемматизированная или сырая фраза (lowercase, single-space)


class PhoneticIndex:
    """Индекс улиц: surface-phrases + lemma-tuple + lemma-phrases. Использует Morphology + токенайзер.

    Полная пересборка через `build(rows)`; точечная замена одной улицы через
    `replace_street(street_id, row)`. Оба метода — sync (без await); вызываются
    через `asyncio.to_thread` если нужен off-loop запуск.

    Все структуры меняются атомарно через single-shot assignment в конце build/replace.
    """

    def __init__(self, morph: Morphology) -> None:
        self._morph = morph

        # Основные структуры индекса. Пустые до первого build().
        self._lemma_tuple: Dict[Tuple[str, ...], List[PhoneticEntry]] = {}
        self._lemma_phrases: List[str] = []
        self._lemma_phrase_meta: List[PhoneticEntry] = []
        # Surface phrases — сырые алиасы (lowercase, clean()) для Tier 1 fuzzy.
        self._surface_phrases: List[str] = []
        self._surface_phrase_meta: List[PhoneticEntry] = []
        # Предрасщепление лемма-фраз на single-word / multi-word —
        # вычисляется один раз при build/replace.
        self._lemma_phrases_single: List[str] = []
        self._lemma_phrase_meta_single: List[PhoneticEntry] = []
        self._lemma_phrases_multi: List[str] = []
        self._lemma_phrase_meta_multi: List[PhoneticEntry] = []
        # Обратный индекс street_id → lemma_tuple первого алиаса.
        self._street_to_lemmas: Dict[int, Tuple[str, ...]] = {}

    # ----------------------------------------------------------- lemma helpers

    def _lemma_tuple_for_name(self, name: str) -> Tuple[str, ...]:
        """Кортеж лемм имени улицы — ключ для exact-lemma fallback."""
        cleaned = clean(name)
        if not cleaned:
            return ()
        tokens = tokenize(cleaned)
        if not tokens:
            return ()
        lemmas = self._morph.lemmatize_tokens(tokens)
        return tuple(l.normal_form for l in lemmas if l.normal_form)

    # ---------------------------------------------------------------- build

    def _entries_for_street(self, street_id: int, names: List[str]) -> Tuple[
        List[Tuple[Tuple[str, ...], PhoneticEntry]],  # (lemma_tuple, entry)
        List[Tuple[str, PhoneticEntry]],              # (lemma_phrase, entry)
        List[Tuple[str, PhoneticEntry]],              # (surface_phrase, entry)
    ]:
        """Собрать все индексные записи для одной улицы.

        Возвращает три списка: lemma-tuple-pairs, lemma-phrase-pairs, surface-phrase-pairs.
        Дубликаты внутри одной улицы отсекаются.
        """
        if not names:
            return [], [], []
        canonical = names[0]

        lemma_tuple_pairs: List[Tuple[Tuple[str, ...], PhoneticEntry]] = []
        lemma_phrase_pairs: List[Tuple[str, PhoneticEntry]] = []
        surface_phrase_pairs: List[Tuple[str, PhoneticEntry]] = []

        seen_lemma_phrase: set = set()
        seen_lemma_tuple: set = set()
        seen_surface_phrase: set = set()

        for name in names:
            lemma_tuple = self._lemma_tuple_for_name(name)
            if lemma_tuple and lemma_tuple not in seen_lemma_tuple:
                seen_lemma_tuple.add(lemma_tuple)
                lemma_tuple_pairs.append(
                    (lemma_tuple, PhoneticEntry(street_id, canonical, ' '.join(lemma_tuple)))
                )

            lemma_phrase = ' '.join(lemma_tuple) if lemma_tuple else self._morph.lemma_for_phrase(clean(name))
            if lemma_phrase and lemma_phrase not in seen_lemma_phrase:
                seen_lemma_phrase.add(lemma_phrase)
                lemma_phrase_pairs.append(
                    (lemma_phrase, PhoneticEntry(street_id, canonical, lemma_phrase))
                )

            # Surface phrase — сырой алиас для Tier 1 fuzzy.
            surface_phrase = clean(name).strip()
            if surface_phrase and surface_phrase not in seen_surface_phrase:
                seen_surface_phrase.add(surface_phrase)
                surface_phrase_pairs.append(
                    (surface_phrase, PhoneticEntry(street_id, canonical, surface_phrase))
                )

        return lemma_tuple_pairs, lemma_phrase_pairs, surface_phrase_pairs

    def build(self, rows) -> int:
        """Полная пересборка всех индексов из строк `streets`.

        rows — iterable of dict-like с ключами 'id' и 'names'. Возвращает
        количество surface-фраз в индексе.
        """
        new_lemma_tuple: Dict[Tuple[str, ...], List[PhoneticEntry]] = {}
        new_phrases: List[str] = []
        new_phrase_meta: List[PhoneticEntry] = []
        new_surface_phrases: List[str] = []
        new_surface_meta: List[PhoneticEntry] = []
        new_street_to_lemmas: Dict[int, Tuple[str, ...]] = {}

        street_count = 0
        for row in rows:
            street_id = row['id']
            names = row['names'] or []
            street_count += 1
            lt_pairs, lp_pairs, sp_pairs = self._entries_for_street(street_id, names)
            for lemma_tup, entry in lt_pairs:
                new_lemma_tuple.setdefault(lemma_tup, []).append(entry)
            for phrase, entry in lp_pairs:
                new_phrases.append(phrase)
                new_phrase_meta.append(entry)
            for surface, entry in sp_pairs:
                new_surface_phrases.append(surface)
                new_surface_meta.append(entry)
            # Обратный индекс — первый алиас как ref-tuple для confirmation
            if names:
                first_lemmas = self._lemma_tuple_for_name(names[0])
                if first_lemmas:
                    new_street_to_lemmas[street_id] = first_lemmas

        # Atomic swap.
        self._lemma_tuple = new_lemma_tuple
        self._lemma_phrases = new_phrases
        self._lemma_phrase_meta = new_phrase_meta
        self._surface_phrases = new_surface_phrases
        self._surface_phrase_meta = new_surface_meta
        self._street_to_lemmas = new_street_to_lemmas
        self._rebuild_phrase_split()

        logger.info(
            f"[PhoneticIndex] built: {len(new_surface_phrases)} surface phrases, "
            f"{len(new_lemma_tuple)} lemma tuples, "
            f"{len(new_phrases)} lemma phrases from {street_count} streets"
        )
        return len(new_surface_phrases)

    def replace_street(self, street_id: int, row: Optional[dict]) -> None:
        """Точечно заменить все записи одной улицы в индексе.

        Если row=None — улица удалена/скрыта (нет geom), все её записи
        вычищаются. Снапшот делается локально, swap — атомарно.
        """
        new_lemma_tuple = {
            t: [e for e in entries if e.street_id != street_id]
            for t, entries in self._lemma_tuple.items()
        }
        new_lemma_tuple = {t: ents for t, ents in new_lemma_tuple.items() if ents}

        new_phrases: List[str] = []
        new_phrase_meta: List[PhoneticEntry] = []
        for phrase, entry in zip(self._lemma_phrases, self._lemma_phrase_meta):
            if entry.street_id != street_id:
                new_phrases.append(phrase)
                new_phrase_meta.append(entry)

        new_surface_phrases: List[str] = []
        new_surface_meta: List[PhoneticEntry] = []
        for surface, entry in zip(self._surface_phrases, self._surface_phrase_meta):
            if entry.street_id != street_id:
                new_surface_phrases.append(surface)
                new_surface_meta.append(entry)

        new_street_to_lemmas = dict(self._street_to_lemmas)
        new_street_to_lemmas.pop(street_id, None)

        # Добавить новые записи если улица существует.
        if row:
            names = row['names'] or []
            lt_pairs, lp_pairs, sp_pairs = self._entries_for_street(street_id, names)
            for lemma_tup, entry in lt_pairs:
                new_lemma_tuple.setdefault(lemma_tup, []).append(entry)
            for phrase, entry in lp_pairs:
                new_phrases.append(phrase)
                new_phrase_meta.append(entry)
            for surface, entry in sp_pairs:
                new_surface_phrases.append(surface)
                new_surface_meta.append(entry)
            if names:
                first_lemmas = self._lemma_tuple_for_name(names[0])
                if first_lemmas:
                    new_street_to_lemmas[street_id] = first_lemmas

        self._lemma_tuple = new_lemma_tuple
        self._lemma_phrases = new_phrases
        self._lemma_phrase_meta = new_phrase_meta
        self._surface_phrases = new_surface_phrases
        self._surface_phrase_meta = new_surface_meta
        self._street_to_lemmas = new_street_to_lemmas
        self._rebuild_phrase_split()
        logger.info(f"[PhoneticIndex] reindexed street {street_id}")

    def _rebuild_phrase_split(self) -> None:
        """Пересобрать single/multi-word split лемма-фраз (после swap)."""
        single_p, single_m, multi_p, multi_m = [], [], [], []
        for ph, mt in zip(self._lemma_phrases, self._lemma_phrase_meta):
            if ' ' in ph:
                multi_p.append(ph)
                multi_m.append(mt)
            else:
                single_p.append(ph)
                single_m.append(mt)
        self._lemma_phrases_single = single_p
        self._lemma_phrase_meta_single = single_m
        self._lemma_phrases_multi = multi_p
        self._lemma_phrase_meta_multi = multi_m

    # ---------------------------------------------------------------- queries

    def query_lemma_tuple(self, lemmas: Tuple[str, ...]) -> List[PhoneticEntry]:
        """Точное совпадение по кортежу лемм (Tier 2)."""
        if not lemmas:
            return []
        return list(self._lemma_tuple.get(lemmas, ()))

    def get_lemma_tuple_for_street(self, street_id: int) -> Tuple[str, ...]:
        """Кортеж лемм canonical-имени улицы (для multiword confirmation).

        Возвращает пустой tuple если street_id неизвестен.
        """
        return self._street_to_lemmas.get(street_id, ())

    def lemma_phrases(self) -> Tuple[List[str], List[PhoneticEntry]]:
        """Параллельные списки для rapidfuzz Tier 3 (lemma fuzzy)."""
        return self._lemma_phrases, self._lemma_phrase_meta

    def lemma_phrases_split(self) -> Tuple[
        List[str], List[PhoneticEntry], List[str], List[PhoneticEntry]
    ]:
        """Предрасщеплённые (single_phrases, single_meta, multi_phrases, multi_meta)."""
        return (
            self._lemma_phrases_single, self._lemma_phrase_meta_single,
            self._lemma_phrases_multi, self._lemma_phrase_meta_multi,
        )

    def surface_phrases(self) -> Tuple[List[str], List[PhoneticEntry]]:
        """Параллельные списки сырых алиасов для rapidfuzz Tier 1 (surface fuzzy)."""
        return self._surface_phrases, self._surface_phrase_meta

    @property
    def is_empty(self) -> bool:
        return not self._lemma_tuple and not self._lemma_phrases
