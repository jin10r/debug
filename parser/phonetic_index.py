"""PhoneticIndex — индекс geo-объектов: стем-индекс (распознавание) + surface (опечатки).

Архитектура (после перехода на морфологический распознаватель):
  • для каждого алиаса geo-объекта строим:
    - **стем-кортеж** (Snowball по каждому токену) — ключ точного распознавания;
    - **сырую поверхностную форму** — для орфо-корректора (typo, Tier 2).
  • Tier 1 (распознавание) — точный lookup по стем-кортежу: O(1), детерминирован,
    инвариантен к падежу. Гаванной≡Гаванная, потому что стем один ("гава").
  • Tier 2 (опечатки) — rapidfuzz по сырым алиасам, только как корректор
    орфографии (высокий cutoff + length-guard), НЕ как основной матч.

Стем устойчив к OOV-проперам (имена объектов несловарны), где pymorphy-лемматизация
врёт. Класс называется PhoneticIndex для обратной совместимости (используется в
geo_matcher.py и тестах под этим именем).
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


@dataclass(frozen=True)
class PhoneticEntry:
    """Запись индекса: geo-объект + одно из его каноничных названий + строка-вариант.

    frozen=True позволяет использовать в качестве ключа хеш-таблиц.
    """
    street_id: int
    canonical_name: str   # первое значение из geo.names — для UI/логов
    variant_text: str     # стем-строка или сырой алиас (lowercase, single-space)
    geo_type: str = 'street'  # тип объекта: street, village, town, station, park, ...


class PhoneticIndex:
    """Индекс geo-объектов: стем-кортежи (распознавание) + surface-фразы (опечатки).

    Полная пересборка через `build(rows)`; точечная замена одной улицы через
    `replace_street(street_id, row)`. Оба метода — sync; вызываются через
    `asyncio.to_thread` если нужен off-loop запуск. Все структуры меняются
    атомарно через single-shot assignment в конце build/replace.
    """

    def __init__(self, morph: Morphology) -> None:
        self._morph = morph

        # Стем-индекс: кортеж стемов токенов алиаса → записи (Tier 1, exact).
        self._stem_index: Dict[Tuple[str, ...], List[PhoneticEntry]] = {}
        # Порядок-независимый индекс многословных имён (ключ = sorted стем-кортеж):
        # "Застава 2" ≡ "2 застава". Только len>=2; запрашивается при промахе exact.
        self._stem_index_sorted: Dict[Tuple[str, ...], List[PhoneticEntry]] = {}
        # Surface-фразы — сырые алиасы (lowercase, clean()) для Tier 2 (опечатки).
        self._surface_phrases: List[str] = []
        self._surface_phrase_meta: List[PhoneticEntry] = []

    # ----------------------------------------------------------- helpers

    def _stem_tuple_for_name(self, name: str) -> Tuple[str, ...]:
        """Кортеж стемов имени улицы — ключ распознавания."""
        cleaned = clean(name)
        if not cleaned:
            return ()
        tokens = tokenize(cleaned)
        if not tokens:
            return ()
        return tuple(s for s in self._morph.stem_tokens(tokens) if s)

    def _entries_for_street(self, street_id: int, names: List[str], obj_type: str = 'street') -> Tuple[
        List[Tuple[Tuple[str, ...], PhoneticEntry]],  # (stem_tuple, entry)
        List[Tuple[str, PhoneticEntry]],              # (surface_phrase, entry)
    ]:
        """Собрать индексные записи для одной улицы (дубликаты внутри отсекаются)."""
        if not names:
            return [], []
        canonical = names[0]

        stem_pairs: List[Tuple[Tuple[str, ...], PhoneticEntry]] = []
        surface_pairs: List[Tuple[str, PhoneticEntry]] = []
        seen_stem: set = set()
        seen_surface: set = set()

        for name in names:
            surface_phrase = clean(name).strip()
            stem_tuple = self._stem_tuple_for_name(name)
            # variant_text = сырой surface алиаса (не стем): нужен для разрешения
            # over-stem коллизий по поверхностной близости (Гаваи/Гаванная→"гава").
            if stem_tuple and stem_tuple not in seen_stem:
                seen_stem.add(stem_tuple)
                stem_pairs.append(
                    (stem_tuple, PhoneticEntry(street_id, canonical, surface_phrase, obj_type))
                )


            if surface_phrase and surface_phrase not in seen_surface:
                seen_surface.add(surface_phrase)
                surface_pairs.append(
                    (surface_phrase, PhoneticEntry(street_id, canonical, surface_phrase, obj_type))
                )


        return stem_pairs, surface_pairs

    # ---------------------------------------------------------------- build

    def build(self, rows) -> int:
        """Полная пересборка индексов из строк `geo`.

        rows — iterable of dict-like с ключами 'id' и 'names' (из таблицы geo). Возвращает
        количество surface-фраз.
        """
        new_stem_index: Dict[Tuple[str, ...], List[PhoneticEntry]] = {}
        new_stem_index_sorted: Dict[Tuple[str, ...], List[PhoneticEntry]] = {}
        new_surface_phrases: List[str] = []
        new_surface_meta: List[PhoneticEntry] = []

        street_count = 0
        for row in rows:
            street_id = row['id']
            names = row['names'] or []
            obj_type = row.get('type', 'street') or 'street'
            street_count += 1
            st_pairs, sp_pairs = self._entries_for_street(street_id, names, obj_type)
            for stem_tup, entry in st_pairs:
                new_stem_index.setdefault(stem_tup, []).append(entry)
                if len(stem_tup) >= 2:
                    new_stem_index_sorted.setdefault(
                        tuple(sorted(stem_tup)), []).append(entry)
            for surface, entry in sp_pairs:
                new_surface_phrases.append(surface)
                new_surface_meta.append(entry)

        # Atomic swap.
        self._stem_index = new_stem_index
        self._stem_index_sorted = new_stem_index_sorted
        self._surface_phrases = new_surface_phrases
        self._surface_phrase_meta = new_surface_meta

        logger.info(
            f"[PhoneticIndex] built: {len(new_surface_phrases)} surface phrases, "
            f"{len(new_stem_index)} stem tuples from {street_count} objects"
        )
        return len(new_surface_phrases)

    def replace_street(self, street_id: int, row: Optional[dict]) -> None:
        """Точечно заменить все записи одной улицы. row=None → улица удалена."""
        def _purge(idx):
            out = {t: [e for e in ents if e.street_id != street_id]
                   for t, ents in idx.items()}
            return {t: ents for t, ents in out.items() if ents}

        new_stem_index = _purge(self._stem_index)
        new_stem_index_sorted = _purge(self._stem_index_sorted)

        new_surface_phrases: List[str] = []
        new_surface_meta: List[PhoneticEntry] = []
        for surface, entry in zip(self._surface_phrases, self._surface_phrase_meta):
            if entry.street_id != street_id:
                new_surface_phrases.append(surface)
                new_surface_meta.append(entry)

        if row:
            names = row['names'] or []
            obj_type = row.get('type', 'street') or 'street'
            st_pairs, sp_pairs = self._entries_for_street(street_id, names, obj_type)
            for stem_tup, entry in st_pairs:
                new_stem_index.setdefault(stem_tup, []).append(entry)
                if len(stem_tup) >= 2:
                    new_stem_index_sorted.setdefault(
                        tuple(sorted(stem_tup)), []).append(entry)
            for surface, entry in sp_pairs:
                new_surface_phrases.append(surface)
                new_surface_meta.append(entry)

        self._stem_index = new_stem_index
        self._stem_index_sorted = new_stem_index_sorted
        self._surface_phrases = new_surface_phrases
        self._surface_phrase_meta = new_surface_meta
        logger.info(f"[PhoneticIndex] reindexed street {street_id}")

    # ---------------------------------------------------------------- queries

    def query_stem_tuple(self, stems: Tuple[str, ...]) -> List[PhoneticEntry]:
        """Точное совпадение по кортежу стемов (Tier 1)."""
        if not stems:
            return []
        return list(self._stem_index.get(stems, ()))

    def query_stem_tuple_sorted(self, stems: Tuple[str, ...]) -> List[PhoneticEntry]:
        """Совпадение многословного имени без учёта порядка слов (Tier 1b).

        Только для len>=2 ("Застава 2" ≡ "2 застава"). Запрашивать после промаха
        exact-порядка, чтобы не подменять точный матч.
        """
        if len(stems) < 2:
            return []
        return list(self._stem_index_sorted.get(tuple(sorted(stems)), ()))

    def surface_phrases(self) -> Tuple[List[str], List[PhoneticEntry]]:
        """Параллельные списки сырых алиасов для rapidfuzz Tier 2 (опечатки)."""
        return self._surface_phrases, self._surface_phrase_meta

    @property
    def is_empty(self) -> bool:
        return not self._stem_index and not self._surface_phrases
