# Rewrite парсера на экосистеме mawo + natasha

> **Update 2026-05-26**: после Шага 0 верификации `mawo-natasha` исключён из стека
> (сломан: Navec URL 404, NER даёт `Москва → PER`). NER — через оригинальный
> `natasha`. Остальной стек mawo (`pymorphy3`, `razdel`) — работает.

## Контекст

Текущий парсер работает на голом `mawo-pymorphy3 + rapidfuzz`. Его слабые места —
системные, выявлены в анализе ~145 событий из таблицы `events`:

1. **Нет semantic-фильтрации**: фуззи-поиск идёт по всему тексту, включая описания
   объектов ("малый автобус" → "Малая Арнаутская").
2. **Деструктивный `clean()`**: стирает пунктуацию и регистр **до** любого NLP-шага,
   убивая сигналы, нужные для NER и токенизации.
3. **Простая токенизация `.split()`**: не понимает аббревиатуры ("ул.", "пр."),
   инициалы, дефисные слова.

Локально-инкрементальные правки (n-граммы, порог) уже выжали что могли.
Дальнейший рост качества требует **архитектурного** изменения: NER-first пайплайн
с правильной токенизацией. Экосистема mawo предоставляет CPU-only компоненты
(natasha, slovnet, razdel) которые встают в текущий стек без GPU.

---

## Ограничения (от пользователя)

- `monitoring.py` — без изменений.
- `text_preprocessor.strip_tail` — без изменений.
- Работа в новой ветке `mawo_parser`, текущее состояние ветки `leaflet` коммитится **до** ветвления.
- Приоритет: **качество определения локаций**.
- Тот же функциональный контракт: `MessageProcessor.process_message(msg_data)` остаётся API surface.

---

## Workflow git

```bash
# 1. Коммит текущего состояния на leaflet
git status                         # проверить рабочее дерево
git add <relevant files>           # никаких .env, никаких чужих изменений
git commit -m "..."                # описать состояние перед миграцией

# 2. Создать и переключиться на новую ветку
git checkout -b mawo_parser

# 3. Сохранить план в проекте (post-approval)
mkdir -p docs
cp /home/eliot/.claude/plans/lovely-floating-unicorn.md docs/mawo_parser_plan.md
git add docs/mawo_parser_plan.md
git commit -m "docs: добавить план rewrite парсера на mawo"

# 4. Дальнейшие шаги (реализация) — отдельными коммитами в этой ветке.
```

---

## Архитектура нового пайплайна

```
monitoring.py (UNCHANGED)
    ↓ msg_data
MessageProcessor.process_message
    ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. strip_tail(raw)         (UNCHANGED)                                  │
│       ↓ stripped_text (case+punctuation intact)                         │
│                                                                          │
│ 2. preprocess_light(stripped)   (NEW: HTML/time/UA→RU, БЕЗ удаления     │
│       ↓ preserved_text                  пунктуации и регистра)          │
│                                                                          │
│ 3. ─┬─ NERExtractor.extract(preserved_text)                             │
│     │      ↓ loc_spans: List[Span(text, start, end)]                    │
│     │                                                                    │
│     └─ RazdelTokenizer.tokenize(preserved_text)                         │
│            ↓ tokens: List[Token(text, start, end)]                      │
│                                                                          │
│ 4. Morphology.lemmatize_tokens(tokens)                                  │
│       ↓ lemmas: List[Lemma(surface, normal_form, pos)]                  │
│                                                                          │
│ 5. LayerClassifier.classify(lemmas)   (UPDATED: работает на List[Lemma])│
│       ↓ layer                                                            │
│                                                                          │
│ 6. StreetMatcher.find_streets(                                          │
│       loc_spans=loc_spans,                                              │
│       lemmas=lemmas,                                                    │
│       raw=preserved_text                                                │
│    )                                                                     │
│       ↓ entities (street_id, score, matched_part, source)               │
│                                                                          │
│ 7. process_candidates SQL   (UNCHANGED)                                 │
│       ↓ geom + strategy                                                 │
│                                                                          │
│ 8. _insert_event   (UNCHANGED)                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**Ключевые отличия от текущего пайплайна:**
- NER работает на тексте с сохранённым регистром/пунктуацией.
- Токенизация razdel'ом даёт правильные границы для аббревиатур.
- StreetMatcher имеет **три уровня кандидатов** (см. ниже) вместо одного.
- LayerClassifier работает на `List[Lemma]` (со словарём POS) — не нужно перелемматизировать строку.

---

## Выбор компонентов (после верификации Шага 0)

> **Корректировка после Шага 0 (2026-05-26)**: `mawo-natasha` оказался сломан —
> Navec URL → HTTP 404, NER без правильных embeddings даёт мусорные классификации
> (`Москва → PER`, `Малой Арнаутской → PER`). **Заменён на оригинальный `natasha`**.

| Компонент | Используем? | Зачем |
|-----------|-------------|-------|
| `mawo-pymorphy3` | да (оставляем) | Лемматизация и POS-теги. Уже работает |
| `mawo-razdel` | да | Корректная токенизация (аббревиатуры, инициалы), `.start`/`.stop` |
| `natasha` (**оригинальный**) | да | NER через `NewsNERTagger`: LOC/PER/ORG на capitalized тексте |
| `navec` (зависимость natasha) | да | Pretrained embeddings (~50 MB, качаются один раз) |
| ~~`mawo-natasha`~~ | **нет, СЛОМАН** | Navec 404; NER даёт `Москва → PER`. См. таблицу ниже |
| ~~`mawo-slovnet`~~ | **нет** | Не нужен напрямую — natasha сам тянет slovnet через свои deps |
| `mawo-core` | **нет** | Wrapper скрывает контроль. Берём компоненты явно |
| `rapidfuzz` | да (оставляем) | Финальный фуззи-матч против БД улиц. Незаменим |

### Почему `natasha`, а не `mawo-natasha` — данные верификации

| Тест-текст | `mawo-natasha` | оригинальный `natasha` |
|------------|----------------|------------------------|
| "Путин посетил Москву" | `Москву → PER` ❌ | `Путин → PER`, `Москву → LOC` ✓ |
| "На Малой Арнаутской задержали" | `Малой Арнаутской → PER` ❌ | `Малой Арнаутской → LOC` ✓ |
| "стоит Volkswagen на Дерибасовской" | (no spans) ❌ | `Volkswagen → ORG`, `Дерибасовской → LOC` ✓ |
| "на ольгиевском спуске" (lowercase) | (no spans) | (no spans) — оба ограничены news-tagger'ом |

`mawo-natasha` при инициализации пишет `❌ Failed to download Navec 'news_v1': HTTP Error 404`
и далее работает на дефолтных весах → произвольная классификация. Lowercase Telegram-стиль
не покрывается ни одним NER (обучены на новостях) — это OK, T3-fallback по полному тексту это покрывает.

---

## Структура файлов в ветке `mawo_parser`

```
parser/
├── monitoring.py            UNCHANGED — import contract сохраняется
├── text_preprocessor.py     KEEP strip_tail; REWORK clean → preprocess_light
├── ner_extractor.py         NEW — wrapper над mawo-natasha
├── razdel_tokenizer.py      NEW — wrapper над mawo-razdel
├── morphology.py            NEW — централизованная работа с pymorphy3 (Lemma dataclass)
├── street_matcher.py        NEW — замена lexical_matcher.py (NER-first matching)
├── layer_classifier.py      UPDATED — принимает List[Lemma] вместо строки
├── message_processor.py     UPDATED — оркестрирует новый пайплайн, API surface сохраняется
├── db_adapter.py            UNCHANGED
├── settings.py              UNCHANGED
└── requirements.txt         UPDATED — добавить natasha, slovnet, razdel
```

Старые `lexical_matcher.py` **удаляется** (заменён `street_matcher.py`).

---

## Дизайн модулей

### `text_preprocessor.py` — мягкая предобработка

```python
def strip_tail(text: str) -> str: ...   # UNCHANGED

def preprocess_light(text: str) -> str:
    """Снять HTML, удалить таймстампы, нормализовать украинские буквы.
    НЕ удаляет пунктуацию и регистр — это нужно NER'у и razdel'у.
    """
    text = html.unescape(text)
    text = _TAG_RE.sub(' ', text)
    text = _TIME_RE.sub(' ', text)
    text = text.translate(_UA_TABLE)      # і→и, ї→и, є→е
    text = _SPACES_RE.sub(' ', text).strip()
    return text

def to_lowercase_lemma_form(text: str) -> str:
    """Используется только StreetMatcher'ом для финального матчинга против lemma index.
    Применяется к маленьким фрагментам (LOC-спанам), не ко всему сообщению.
    """
    return _NON_ALNUM_RE.sub(' ', text).lower().strip()
```

### `ner_extractor.py` — NER через оригинальный natasha

```python
@dataclass
class Span:
    text: str          # текст спана из исходной строки
    start: int         # символьная позиция в preserved_text
    end: int

class NERExtractor:
    def __init__(self): self._initialized = False

    def initialize(self) -> bool:
        """Lazy-load модели. Не падает наружу — degraded mode если NER недоступен."""
        try:
            from natasha import Segmenter, NewsEmbedding, NewsNERTagger, Doc
            emb = NewsEmbedding()              # Navec ~50 MB
            self._segmenter = Segmenter()
            self._ner = NewsNERTagger(emb)
            self._Doc = Doc
            self._initialized = True
            return True
        except Exception as exc:
            logger.warning(f"[NER] Init failed → degraded mode: {exc}")
            return False

    def extract(self, text: str) -> List[Span]:
        """LOC-спаны. Возвращает [] если NER не загружен (graceful)."""
        if not self._initialized or not text:
            return []
        doc = self._Doc(text)
        doc.segment(self._segmenter)
        doc.tag_ner(self._ner)
        return [
            Span(text=s.text, start=s.start, end=s.stop)
            for s in doc.spans
            if s.type == 'LOC'
        ]
```

_API натаsha верифицировано в Шаге 0 (см. начало раздела «Выбор компонентов»)._

### `razdel_tokenizer.py` — токенизация

```python
@dataclass
class Token:
    text: str
    start: int
    end: int

class RazdelTokenizer:
    def tokenize(self, text: str) -> List[Token]:
        from mawo_razdel import tokenize
        return [Token(t.text, t.start, t.stop) for t in tokenize(text)]

    def sentenize(self, text: str) -> List[str]:
        from mawo_razdel import sentenize
        return [s.text for s in sentenize(text)]
```

### `morphology.py` — централизованная лемматизация

```python
@dataclass
class Lemma:
    surface: str       # как в тексте
    normal_form: str   # нормальная форма из pymorphy3
    pos: str           # NOUN, ADJF, VERB, PREP, ...
    is_proper: bool    # Name, Surn, Patr, Geox теги

class Morphology:
    def __init__(self):
        import mawo_pymorphy3
        self._morph = mawo_pymorphy3.MorphAnalyzer()

    def lemmatize_word(self, word: str) -> Lemma: ...
    def lemmatize_tokens(self, tokens: List[Token]) -> List[Lemma]: ...
    def lemma_for(self, text: str) -> str:
        """Single-shot лемматизация фразы (для алиасов улиц при инициализации)."""
```

Сохраняем существующий `ORDINAL_MAP` (порядковые числительные → цифры) — он работает хорошо.

### `street_matcher.py` — NER-first matcher (замена lexical_matcher.py)

Главный модуль. Логика поиска улиц с **тремя уровнями кандидатов**:

```python
class StreetMatcher:
    async def initialize(self, pg_pool):
        """Загружает alias-индекс из streets, лемматизирует через Morphology."""
        # как сейчас, но через self._morph: Morphology

    def find_streets(
        self,
        loc_spans: List[Span],
        lemmas: List[Lemma],
        threshold: float = 0.75,
        top_k: int = 3,
    ) -> List[Dict]:
        """
        Стратегии (по убыванию precision, в порядке поиска):
          T1 [NER]:   фуззи-матч против LOC-спанов из NER (×1.00)
          T2 [Proper]: фуззи-матч против пар (Geox/Name + соседнее существительное) (×0.90)
          T3 [Full]:   фуззи-матч против всего лемматизированного текста (×0.80)

        Возвращает union стратегий, дедуплицированный по street_id (max score).
        Каждый match помечен ['source']: 'ner' | 'proper' | 'lexical'.
        """
```

**Почему три уровня:**
- T1 хорош, но NER может пропускать (особенно низкорегистровый Telegram-сленг).
- T2 — fallback на pymorphy3-теги: словосочетания с именами собственными.
- T3 — последняя сетка: текущая логика, с пенальти.

Внутри T2/T3 переиспользуем текущую логику `_generate_ngrams`+`rapidfuzz` (она уже отлажена в коммитах `1b0e48d`).

### `layer_classifier.py` — UPDATED

Сейчас `classify(cleaned_text)` сам лемматизирует. В новом пайплайне получает уже готовые леммы:

```python
def classify(self, lemmas: List[Lemma]) -> str:
    token_lemmas = {l.normal_form for l in lemmas}
    for layer in _LAYER_PRIORITY:
        if self._keyword_lemmas[layer] & token_lemmas:
            return layer
    return 'pig'
```

Меньше работы, одно общее место лемматизации.

### `message_processor.py` — UPDATED оркестрация

```python
class MessageProcessor:                 # API сохранён ради monitoring.py
    def __init__(self, db_pool):
        self.db_pool = db_pool
        self.morph = Morphology()
        self.ner = NERExtractor()
        self.tokenizer = RazdelTokenizer()
        self.layer_classifier = LayerClassifier(self.morph)
        self.matcher = StreetMatcher(self.morph)

    async def initialize(self) -> bool:
        self.ner.initialize()           # допускает graceful fallback
        return await self.matcher.initialize(self.db_pool)

    async def process_message(self, msg_data):
        raw = msg_data.get('text', '') or ''
        stripped = strip_tail(raw)
        preserved = preprocess_light(stripped)

        loc_spans = self.ner.extract(preserved)
        tokens    = self.tokenizer.tokenize(preserved)
        lemmas    = self.morph.lemmatize_tokens(tokens)

        layer = self.layer_classifier.classify(lemmas)

        if not preserved or len(preserved) > MAX_TEXT_LENGTH:
            return await self._insert_random(...)

        entities = self.matcher.find_streets(loc_spans, lemmas)
        if not entities:
            return await self._insert_random(...)
        # process_candidates + _insert_event — без изменений
```

---

## requirements.txt (parser/)

Добавить (версии зафиксированы по результатам Шага 0):
```
natasha>=1.6.0           # ОРИГИНАЛЬНЫЙ (не mawo) — NewsNERTagger работает корректно
navec>=0.10.0            # Pretrained embeddings ~50 MB, тянется natasha'ой как зависимость
mawo-razdel>=1.0.6       # Токенизация — работает идеально
```

Сохранить:
```
mawo-pymorphy3==1.0.4
rapidfuzz>=3.0.0
asyncpg>=0.29.0
kurigram==2.2.9
tgcrypto>=1.2.0
environs>=11.0.0
tzdata>=2024.1
```

Docker:
- Navec-эмбеддинги (~50 MB) тянутся при `NewsEmbedding()` из github (рабочий URL,
  в отличие от mawo-natasha). В `Dockerfile.parser` добавить warm-up на этапе build:
  `RUN python -c "from natasha import NewsEmbedding, NewsNERTagger; NewsNERTagger(NewsEmbedding())"`,
  чтобы итоговый image содержал модели в кэше и не качал при старте контейнера.

---

## Порядок реализации

После согласования плана и переключения на ветку `mawo_parser`:

1. **Шаг 0 (верификация API) — ВЫПОЛНЕН 2026-05-26**: установка в venv показала
   что `mawo-natasha` сломан (Navec HTTP 404 → мусорный NER). Решение: использовать
   оригинальный `natasha` (NER LOC работает корректно). `mawo-razdel` и
   `mawo-pymorphy3` валидированы — работают. См. таблицу в «Выбор компонентов».

2. **Шаг 1 (parser/text_preprocessor.py)**: добавить `preprocess_light`, оставить
   `strip_tail`. Старый `clean` оставить (используется только StreetMatcher'ом
   для канонизации lemma index).

3. **Шаг 2 (parser/morphology.py)**: новый модуль, перенести логику лемматизации
   и `ORDINAL_MAP` из текущего `lexical_matcher.py`. Добавить POS/proper-noun.

4. **Шаг 3 (parser/razdel_tokenizer.py)**: тонкий wrapper над `mawo-razdel`.

5. **Шаг 4 (parser/ner_extractor.py)**: wrapper над `mawo-natasha`. Graceful degrade.

6. **Шаг 5 (parser/street_matcher.py)**: основная работа. Реализовать T1/T2/T3
   стратегии. Переиспользовать `_generate_ngrams` и rapidfuzz-логику из старого
   `lexical_matcher.py`.

7. **Шаг 6 (parser/layer_classifier.py)**: переход на `List[Lemma]`.

8. **Шаг 7 (parser/message_processor.py)**: переписать `process_message`, сохранить
   публичные методы и сигнатуры (важно для monitoring.py).

9. **Шаг 8 (parser/requirements.txt + Dockerfile.parser)**: обновить зависимости,
   warm-up для Navec.

10. **Шаг 9 (удаление)**: удалить `parser/lexical_matcher.py` после убеждения,
    что `street_matcher.py` покрывает все его обязанности.

11. **Шаг 10 (запуск)**: `docker compose build parser && docker compose up -d parser`.

Каждый шаг — отдельный коммит для атомарных revert'ов при проблемах.

---

## Стратегия миграции и rollback

- Ветка `leaflet` остаётся стабильным production. `mawo_parser` живёт параллельно.
- На сервере деплоится `mawo_parser` рядом с `leaflet` (если есть staging) или
  переключается тег docker-образа в `docker-compose.yml` через env var.
- **Rollback**: `git checkout leaflet && docker compose build parser && docker compose up -d parser`.
- БД схема НЕ меняется (`events`, `streets`, `process_candidates` — те же).
  Это значит: один и тот же production-БД работает с обеими версиями парсера.

---

## Верификация качества

Цель — измеримо подтвердить улучшение локаций.

### Сбор baseline (на ветке `leaflet`)
- Экспорт ~200 свежих событий из `events`: `id, description, matches, geom, strategy`.
- Ручная разметка ~50 событий: правильно ли поставлена точка (human gold-label).

### A/B-прогон на `mawo_parser`
- Прогнать те же 200 описаний через новый `MessageProcessor` (offline-скрипт,
  без БД-вставки — только результаты).
- Сравнить:
  - **Recall**: для скольких правильно-локационных событий новый парсер нашёл хоть какую улицу?
  - **Precision** (на gold-50): сколько % новых матчей правильны?
  - **Strategy distribution**: больше ли стало `single_match` (точные геометрии)
    в ущерб `random`?

### Регрессионные кейсы (минимум)
```
"на Малой Арнаутской задержали мужчину с малым пистолетом"
  → NER LOC: ["Малой Арнаутской"]; match: Малая Арнаутская ✓
  → "малый" НЕ матчит "Малую Арнаутскую" из контекста описания пистолета ✓

"на ольгиевском спуске у мужика с машины тцкуны украли"
  → match: Ольгиевский спуск ✓ (низкорегистровое — NER может пропустить,
                                  тогда T3-fallback находит)
  → "переулок" из текущей баги не дабл-матчится ✓

"мутный зелёный Volkswagen у дерева"
  → loc_spans пусто; full-text с порогом 0.75 → 0 матчей → random ✓

"возле парка Марка Твена стоит скорая"
  → NER: "парка Марка Твена" (LOC) → не находит в streets DB → random ✓
  → "Марата" не матчится в режиме T1 (NER spans узкие) ✓
```

### Перфоманс
```
sudo docker stats parser --no-stream
```
- RAM: ожидаем рост с ~50 MB до ~120 MB (+Navec ~50 MB, +slovnet ~10 MB).
- Latency на сообщение: ожидаем <50 ms (NER ~10 ms, остальное как раньше).

---

## Открытые вопросы / решения по умолчанию

- **mawo-core вместо отдельных wrapper'ов?** Решение: нет, отдельные wrapper'ы
  для контроля и понятных границ. Можно вернуться к core, если интерфейсы окажутся
  громоздкими (низкая вероятность).
- **NER на каждом сообщении ИЛИ только для коротких?** Решение: на каждом
  (сообщения уже усечены 380 chars, NER успевает за 10 ms).
- **Сохранять ли `loc_spans` в `matches.source='ner'`?** Решение: да, для будущей
  отладки и для возможной подсветки в UI.
- **Удалять ли `lexical_matcher.py`?** Решение: да, в Шаге 9 — иначе будет
  путаница и dead-code.
