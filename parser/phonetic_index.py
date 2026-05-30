"""PhoneticIndex — фонетический индекс улиц (Russian Metaphone) + леммо-индекс.

Заменяет связку NER+SymSpell. Идея:
  • для каждого названия улицы при старте строим все возможные словоформы
    (через `parse[0].lexeme` mawo_pymorphy3, только content-words: NOUN/ADJF/ADJS/NUMR),
    декартово произведение по токенам;
  • для каждой словоформной строки считаем Metaphone-код (fonetika.RussianMetaphone)
    → `Dict[code, List[PhoneticEntry]]`;
  • параллельно строим леммо-индекс `Dict[Tuple[lemmas,...], List[PhoneticEntry]]`
    и список лемматизированных фраз для tier-B rapidfuzz-fallback.

В рантайме матчер на сообщении гонит n-граммы (исходные surface формы токенов)
через `query_phonetic(text)` → O(1) lookup → rapidfuzz-верификация.
"""

import itertools
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .morphology import Morphology
from .razdel_tokenizer import RazdelTokenizer
from .text_preprocessor import clean

try:
    from .settings import settings
except Exception:
    settings = None

logger = logging.getLogger(__name__)

# Content POS — слова, которые имеет смысл инфлектировать. Предлоги, союзы,
# частицы оставляем в одной форме (их lexeme короткий или пустой, при этом
# Metaphone бы давал одинаковый код для всех форм).
_CONTENT_POS = frozenset({'NOUN', 'ADJF', 'ADJS', 'NUMR'})


@dataclass(frozen=True)
class PhoneticEntry:
    """Запись индекса: улица + одно из её каноничных названий + строка-вариант."""
    street_id: int
    canonical_name: str   # первое значение из streets.names — для UI/логов
    variant_text: str     # лемматизированная/инфлектированная фраза (lowercase, single-space)


class PhoneticIndex:
    """Метафон-индекс улиц + леммо-индекс. Использует Morphology+razdel.

    Полная пересборка через `build(rows)`; точечная замена одной улицы через
    `replace_street(street_id, row)`. Оба метода — sync (без await); вызываются
    через `asyncio.to_thread` если нужен off-loop запуск.

    Все три структуры (`_phonetic`, `_lemma_tuple`, `_lemma_phrases`/`_lemma_phrase_meta`)
    меняются атомарно через single-shot assignment в конце build/replace.
    """

    def __init__(self, morph: Morphology) -> None:
        self._morph = morph
        # Tokenizer лениво — чтобы тесты импорта не падали без mawo_razdel.
        self._tokenizer: Optional[RazdelTokenizer] = None
        # Metaphone лениво — чтобы ImportError fonetika не валил весь модуль.
        self._metaphoner = None
        self._metaphoner_ready = False

        # Основные структуры индекса. Пустые до первого build().
        self._phonetic: Dict[str, List[PhoneticEntry]] = {}
        self._lemma_tuple: Dict[Tuple[str, ...], List[PhoneticEntry]] = {}
        self._lemma_phrases: List[str] = []
        self._lemma_phrase_meta: List[PhoneticEntry] = []
        # Предрасщепление фраз на single-word / multi-word (+ их meta) —
        # вычисляется один раз при build/replace. Раньше StreetMatcher._lemma_pass
        # пересчитывал этот split на КАЖДЫЙ 1-gram кандидат (O(phrases) на каждый).
        self._lemma_phrases_single: List[str] = []
        self._lemma_phrase_meta_single: List[PhoneticEntry] = []
        self._lemma_phrases_multi: List[str] = []
        self._lemma_phrase_meta_multi: List[PhoneticEntry] = []
        # Обратный индекс street_id → lemma_tuple первого алиаса. Используется
        # confirmation logic в StreetMatcher: когда n-gram матчит часть
        # многословной улицы, ищем оставшиеся ref-лемм в окрестности сообщения.
        self._street_to_lemmas: Dict[int, Tuple[str, ...]] = {}

    # --------------------------------------------------------------- lazy deps

    def _get_tokenizer(self) -> RazdelTokenizer:
        if self._tokenizer is None:
            self._tokenizer = RazdelTokenizer()
        return self._tokenizer

    def _metaphone(self, text: str) -> str:
        """Вычислить русский Metaphone-код фразы.

        Возвращает пустую строку, если fonetika недоступна или текст пустой.
        Метафонизируется как единый блок — fonetika сама нормализует пробелы.
        """
        if not text:
            return ''
        if not self._metaphoner_ready:
            try:
                from fonetika.metaphone import RussianMetaphone
                self._metaphoner = RussianMetaphone(
                    reduce_phonemes=True,
                    replace_ego_ogo_endings=True,
                    deaf_all_consonants=True,
                )
            except Exception as exc:
                logger.warning(
                    f"[PhoneticIndex] fonetika import failed: {exc}; "
                    "phonetic strategy disabled (falls back to lemma)"
                )
                self._metaphoner = None
            self._metaphoner_ready = True

        if self._metaphoner is None:
            return ''
        try:
            return self._metaphoner.transform(text)
        except Exception as exc:
            logger.debug(f"[PhoneticIndex] metaphone({text!r}) failed: {exc}")
            return ''

    # ------------------------------------------------------------ config knobs

    @staticmethod
    def _forms_cap() -> int:
        if settings and settings.similarity:
            return int(getattr(settings.similarity, 'phonetic_forms_cap', 12))
        return 12

    @staticmethod
    def _variants_cap() -> int:
        if settings and settings.similarity:
            return int(getattr(settings.similarity, 'phonetic_variants_per_street_cap', 500))
        return 500

    # ----------------------------------------------------------- variants gen

    def _word_forms(self, token_text: str) -> List[str]:
        """Получить список словоформ для одного токена.

        Для content-POS (NOUN/ADJF/ADJS/NUMR) — `parse[0].lexeme` с дедупом и
        cap=`phonetic_forms_cap`. Для остальных — одиночный исходный токен.
        """
        if not token_text:
            return []
        if token_text.isdigit():
            return [token_text]

        parses = self._morph.analyzer.parse(token_text)
        if not parses:
            return [token_text.lower()]
        best = parses[0]
        pos = str(best.tag.POS) if best.tag.POS else ''
        if pos not in _CONTENT_POS:
            return [token_text.lower()]

        cap = self._forms_cap()
        seen: set = set()
        forms: List[str] = []
        try:
            lexeme = best.lexeme
        except Exception:
            return [token_text.lower()]
        for form in lexeme:
            word = (form.word or '').lower()
            if not word or word in seen:
                continue
            seen.add(word)
            forms.append(word)
            if len(forms) >= cap:
                break
        return forms or [token_text.lower()]

    def _is_content_token(self, surface: str) -> bool:
        """Содержательное слово (NOUN/ADJF/ADJS/NUMR), достойное single-word индексации."""
        if not surface or surface.isdigit():
            return surface.isdigit()  # цифра — содержательна
        parses = self._morph.analyzer.parse(surface)
        if not parses:
            return False
        pos = str(parses[0].tag.POS) if parses[0].tag.POS else ''
        return pos in _CONTENT_POS

    def _generate_variants(self, name: str) -> List[str]:
        """Варианты для имени улицы.

        Для **одно-токенной** улицы (Канатная, Пастера, Гагарина) — индексируются
        все словоформы (single-word варианты).

        Для **многословной** улицы (Малая Арнаутская, Преображенская улица) —
        индексируется только cartesian product словоформ всех токенов
        (полнофразовые варианты). Single-word индексация для многословных
        улиц **сознательно отключена**: она порождала FP «арнаутская → обе
        Арнаутские», «кладбище → 3 кладбища-улицы». Partial recall на
        одиночное слово восстанавливается через T3 lemma_fuzzy + User#1
        confirmation (см. parser/street_matcher.py).

        При превышении `phonetic_variants_per_street_cap` cartesian product
        пересобирается, инфлектируя только первый content-токен.
        """
        cleaned = clean(name)
        if not cleaned:
            return []

        tokenizer = self._get_tokenizer()
        tokens = tokenizer.tokenize(cleaned)
        if not tokens:
            return []

        forms_per_token: List[List[str]] = [
            self._word_forms(t.text) for t in tokens
        ]
        forms_per_token = [f for f in forms_per_token if f]
        if not forms_per_token:
            return []

        # P1: для одно-токенной улицы — single-word варианты,
        #     для многословной — ТОЛЬКО полнофразовый cartesian product.
        if len(forms_per_token) == 1:
            seen: set = set()
            variants: List[str] = []
            for form in forms_per_token[0]:
                if form not in seen:
                    seen.add(form)
                    variants.append(form)
            return variants

        cap = self._variants_cap()

        total = 1
        for f in forms_per_token:
            total *= len(f)
            if total > cap:
                break

        if total > cap:
            # Fallback: инфлектируем только первый content-токен.
            head_idx = next(
                (i for i, t in enumerate(tokens) if self._is_content_token(t.text)),
                0,
            )
            phrase_forms = [
                forms_per_token[i] if i == head_idx else [tokens[i].text.lower()]
                for i in range(len(tokens))
            ]
        else:
            phrase_forms = forms_per_token

        variants = []
        seen = set()
        for combo in itertools.product(*phrase_forms):
            variant = ' '.join(combo)
            if variant not in seen:
                seen.add(variant)
                variants.append(variant)
                if len(variants) >= cap:
                    break
        return variants

    def _lemma_tuple_for_name(self, name: str) -> Tuple[str, ...]:
        """Кортеж лемм имени улицы — ключ для exact-lemma fallback."""
        cleaned = clean(name)
        if not cleaned:
            return ()
        tokens = self._get_tokenizer().tokenize(cleaned)
        if not tokens:
            return ()
        lemmas = self._morph.lemmatize_tokens(tokens)
        return tuple(l.normal_form for l in lemmas if l.normal_form)

    # ---------------------------------------------------------------- build

    def _entries_for_street(self, street_id: int, names: List[str]) -> Tuple[
        List[Tuple[str, PhoneticEntry]],          # (metaphone_code, entry)
        List[Tuple[Tuple[str, ...], PhoneticEntry]],  # (lemma_tuple, entry)
        List[Tuple[str, PhoneticEntry]],          # (lemma_phrase, entry)
    ]:
        """Собрать все индексные записи для одной улицы.

        Возвращает три списка: phonetic-pairs, lemma-tuple-pairs, lemma-phrase-pairs.
        Дубликаты внутри одной улицы по `(code, street_id)` отсекаются.
        """
        if not names:
            return [], [], []
        canonical = names[0]

        phonetic_pairs: List[Tuple[str, PhoneticEntry]] = []
        lemma_tuple_pairs: List[Tuple[Tuple[str, ...], PhoneticEntry]] = []
        lemma_phrase_pairs: List[Tuple[str, PhoneticEntry]] = []

        # Set локальных ключей чтобы один и тот же variant_text не попадал
        # в индекс дважды (например, если у улицы есть алиас «улица Дерибасовская»
        # и «Дерибасовская улица» — лексемы могут пересечься).
        seen_phonetic: set = set()
        seen_lemma_phrase: set = set()
        seen_lemma_tuple: set = set()

        for name in names:
            variants = self._generate_variants(name)
            for variant in variants:
                code = self._metaphone(variant)
                key = (code, variant)
                if not code or key in seen_phonetic:
                    continue
                seen_phonetic.add(key)
                phonetic_pairs.append(
                    (code, PhoneticEntry(street_id, canonical, variant))
                )

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

        return phonetic_pairs, lemma_tuple_pairs, lemma_phrase_pairs

    def build(self, rows) -> int:
        """Полная пересборка всех трёх индексов из строк `streets`.

        rows — iterable of dict-like с ключами 'id' и 'names'. Возвращает
        количество фонетических вариантов в индексе.
        """
        new_phonetic: Dict[str, List[PhoneticEntry]] = {}
        new_lemma_tuple: Dict[Tuple[str, ...], List[PhoneticEntry]] = {}
        new_phrases: List[str] = []
        new_phrase_meta: List[PhoneticEntry] = []
        new_street_to_lemmas: Dict[int, Tuple[str, ...]] = {}

        street_count = 0
        for row in rows:
            street_id = row['id']
            names = row['names'] or []
            street_count += 1
            ph_pairs, lt_pairs, lp_pairs = self._entries_for_street(street_id, names)
            for code, entry in ph_pairs:
                new_phonetic.setdefault(code, []).append(entry)
            for lemma_tup, entry in lt_pairs:
                new_lemma_tuple.setdefault(lemma_tup, []).append(entry)
            for phrase, entry in lp_pairs:
                new_phrases.append(phrase)
                new_phrase_meta.append(entry)
            # Обратный индекс — первый алиас (canonical) как ref-tuple для confirmation
            if names:
                first_lemmas = self._lemma_tuple_for_name(names[0])
                if first_lemmas:
                    new_street_to_lemmas[street_id] = first_lemmas

        variant_count = sum(len(v) for v in new_phonetic.values())

        # Atomic swap: пять синхронных присваиваний без await между ними.
        self._phonetic = new_phonetic
        self._lemma_tuple = new_lemma_tuple
        self._lemma_phrases = new_phrases
        self._lemma_phrase_meta = new_phrase_meta
        self._street_to_lemmas = new_street_to_lemmas
        self._rebuild_phrase_split()

        logger.info(
            f"[PhoneticIndex] built: {variant_count} variants, "
            f"{len(new_lemma_tuple)} lemma tuples, "
            f"{len(new_phrases)} lemma phrases from {street_count} streets"
        )
        return variant_count

    def replace_street(self, street_id: int, row: Optional[dict]) -> None:
        """Точечно заменить все записи одной улицы в индексе.

        Если row=None — улица удалена/скрыта (нет geom), все её записи
        вычищаются. Снапшот делается локально, swap — атомарно.
        """
        # Удалить существующие записи улицы из всех четырёх структур.
        new_phonetic = {
            code: [e for e in entries if e.street_id != street_id]
            for code, entries in self._phonetic.items()
        }
        new_phonetic = {code: ents for code, ents in new_phonetic.items() if ents}

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

        new_street_to_lemmas = dict(self._street_to_lemmas)
        new_street_to_lemmas.pop(street_id, None)

        # Добавить новые записи если улица существует.
        if row:
            names = row['names'] or []
            ph_pairs, lt_pairs, lp_pairs = self._entries_for_street(street_id, names)
            for code, entry in ph_pairs:
                new_phonetic.setdefault(code, []).append(entry)
            for lemma_tup, entry in lt_pairs:
                new_lemma_tuple.setdefault(lemma_tup, []).append(entry)
            for phrase, entry in lp_pairs:
                new_phrases.append(phrase)
                new_phrase_meta.append(entry)
            if names:
                first_lemmas = self._lemma_tuple_for_name(names[0])
                if first_lemmas:
                    new_street_to_lemmas[street_id] = first_lemmas

        self._phonetic = new_phonetic
        self._lemma_tuple = new_lemma_tuple
        self._lemma_phrases = new_phrases
        self._lemma_phrase_meta = new_phrase_meta
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

    def query_phonetic(self, ngram_text: str) -> List[PhoneticEntry]:
        """Найти кандидатов улицы по фонетическому коду n-граммы.

        Возвращает список записей с одним и тем же metaphone-кодом — нескольких
        улиц быть может, и rapidfuzz-верификация делает финальный отбор.
        """
        if not ngram_text:
            return []
        code = self._metaphone(ngram_text)
        if not code:
            return []
        return list(self._phonetic.get(code, ()))

    def query_lemma_tuple(self, lemmas: Tuple[str, ...]) -> List[PhoneticEntry]:
        """Точное совпадение по кортежу лемм (T3 tier-A)."""
        if not lemmas:
            return []
        return list(self._lemma_tuple.get(lemmas, ()))

    def get_lemma_tuple_for_street(self, street_id: int) -> Tuple[str, ...]:
        """Кортеж лемм canonical-имени улицы (для User#1 multiword confirmation).

        Возвращает пустой tuple если street_id неизвестен. Используется
        StreetMatcher для поиска недостающих ref-лемм в окне сообщения.
        """
        return self._street_to_lemmas.get(street_id, ())

    def lemma_phrases(self) -> Tuple[List[str], List[PhoneticEntry]]:
        """Параллельные списки для rapidfuzz tier-B fallback.

        Индекс i списка фраз соответствует i-й записи в meta — синхронизация
        даётся гарантированно в `build`/`replace_street` (одно присваивание).
        """
        return self._lemma_phrases, self._lemma_phrase_meta

    def lemma_phrases_split(self) -> Tuple[
        List[str], List[PhoneticEntry], List[str], List[PhoneticEntry]
    ]:
        """Предрасщеплённые (single_phrases, single_meta, multi_phrases, multi_meta).

        Для T3 tier-B: 1-gram запрос сравнивается отдельно с single-word фразами
        (fuzz.ratio) и multi-word фразами (_max_token_ratio). Split вычислен один
        раз в build/replace — caller не пересчитывает его на каждый кандидат.
        """
        return (
            self._lemma_phrases_single, self._lemma_phrase_meta_single,
            self._lemma_phrases_multi, self._lemma_phrase_meta_multi,
        )

    @property
    def is_empty(self) -> bool:
        return not self._phonetic and not self._lemma_phrases
