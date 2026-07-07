# Анализатор geo-конфликтов: варианты интеграции

## Проблема

Текущий `SemanticResolver` (pre-filter rules + Ollama) не может валидировать
кандидатов по **типу** из газеттира, потому что `type` отбрасывается
в `PhoneticIndex.build()` и не доходит до резолвера. Все type-правила —
мёртвый код.

**Нужен компонент, который:**
- получает текст + кандидатов (с type из geo)
- определяет **роль** каждого упоминания в тексте
- валидирует: role == candidate.type → keep; mismatch → filter/downgrade
- выбирает стратегию (single_match / intersection / midpoint)
- работает CPU-only, лёгкий, без обучения или out-of-the-box

---

## Вариант A: Rule-based + type propagation (рекомендуемый базовый)

### Принцип
Починить type propagation (P10), затем построить систему правил, которая
определяет роль упоминания по **морфологическим и синтаксическим паттернам**
в тексте — без ML/DL.

### Компоненты

#### A1. Type Propagation (фундамент)
- `PhoneticEntry` + `type`, `_link_span` возвращает `type`
- Все type-правила в `_pre_filter` оживают

#### A2. Role Detector
Определяет семантическую роль geo-упоминания по контексту:

```python
class RoleDetector:
    # Паттерны: контекст → роль
    _ROLE_PATTERNS = [
        (r'ул\.?\s*\{name}', 'street'),
        (r'улица\s+{name}', 'street'),
        (r'проспект\s+{name}', 'street'),
        (r'переулок\s+{name}', 'street'),
        (r'село\s+{name}', 'village'),
        (r'пгт\s+{name}', 'town'),
        (r'станци(?:я|и)\s+{name}', 'station'),
        (r'парк\s+{name}', 'park'),
        (r'рынок\s+{name}', 'market'),
        (r'{name}\s+(?:станция|платформа)', 'station'),
        (r'{name}\s+(?:парк|сквер)', 'park'),
        (r'(?:на|в|по|из)\s+{name}', 'street'),  # предлог + топоним → улица
    ]

    def detect(self, text: str, candidates: List[Dict]) -> List[Dict]:
        for c in candidates:
            role = self._match_role(text, c['matched_name'])
            c['_role'] = role or c.get('type')
        return candidates
```

#### A3. Type Validator
```python
def validate(candidates):
    for c in candidates:
        if c['_role'] != c.get('type'):
            c['_type_conflict'] = True
            c['score'] *= 0.5  # штраф за несоответствие
    # Удалить кандидаты с низким score после штрафа
    return [c for c in candidates if c['score'] >= 0.8]
```

#### A4. Strategy Selector
После валидации:
- 0 валидных → fallback random
- 1 валидный → single_match
- 2+ валидных с "/" или "и" между ними → intersection
- 2+ валидных с "от...до" / "между" → midpoint
- 2+ валидных одного типа → midpoint
- 2+ валидных разных типов → intersection (if geom intersect) else midpoint
- остальное → Ollama (если доступен)

### Плюсы
- **Ноль зависимостей** — только существующие pymorphy3 + rapidfuzz
- **Полностью объяснимый** — каждое решение логируется
- **~0.1ms** на сообщение
- Легко адаптируется под новый газеттир (правила в конфиг-таблицах)

### Минусы
- Не ловит сложные семантические случаи
- Правила нужно писать под каждый тип объектов
- Не различает "Жукова 47" (адрес) vs "улица Жукова" (топоним)

### Сложность: 2 дня (P10 + RoleDetector + Validation + Strategy)

---

## Вариант B: Natasha + Rule-based (гибрид)

### Принцип
Использовать `natasha` — российскую NLP-библиотеку без ML (rule-based):
- NER (извлечение именованных сущностей: PER, LOC, ORG)
- Syntax parsing (зависимостный анализ для ролей)
- Morphological tagging (уточнение pymorphy3)

### Компоненты

#### B1. Natasha NER для извлечения LOC
```python
from natasha import (
    Segmenter, NewsEmbedding, NewsMorphoTagger,
    NewsSyntaxParser, NewsNERTagger, Doc
)

ner = NewsNERTagger(NewsEmbedding())
doc = Doc(text)
doc.segment(Segmenter())
doc.tag_morph(NewsMorphoTagger(NewsEmbedding()))
doc.parse_syntax(NewsSyntaxParser(NewsEmbedding()))
doc.tag_ner(ner)

for span in doc.spans:
    if span.type == 'LOC':
        # это гео-упоминание
        # проверить, совпадает ли с кандидатом
```

#### B2. Syntax-based role detection
Использовать dependency parse для определения роли:
- `nsubj` (подлежащее) → "Толбухина блокпост" → street
- `obl` (косвенное дополнение с предлогом) → "на Толбухина" → street
- `nmod` (именное дополнение) → "парк Шевченко" → park
- `appos` (приложение) → "село Александровка" → village

```python
for token in doc.tokens:
    if token.pos == 'NOUN' and token.feats.get('Animacy') == 'Inan':
        if token.head.rel == 'nmod' and token.head.text in type_hints:
            role = type_hints[token.head.text]
```

#### B3. Type Validator (как в A3)
Сравнить тип из газеттира с ролью от Natasha.

### Плюсы
- **Без обучения** — out-of-the-box
- Русская NLP «из коробки» (NER, синтаксис, морфология)
- Лучше понимает синтаксические роли, чем варианты A
- Natasha лёгкая (~50MB с моделями)

### Минусы
- Natasha тоrien на новостном корпусе — точность на Telegram-сленге ниже
- Syntax parser ошибается на коротких/неграмматичных текстах
- Доп. зависимость (~5MB natasha + ~45MB embedding)
- ~5-20ms на сообщение

### Сложность: 4 дня

---

## Вариант C: Tiny zero-shot BERT (ONNX) — ВЫБРАН

### Принцип
`rubert-tiny2` (2-layer BERT, ~45MB) → ONNX quantized int8 (~15MB) →
ONNX Runtime CPU inference.
Используется как **sentence-encoder** для zero-shot определения
типа объекта по контексту в тексте сообщения.

**Без обучения, без Natasha, без fine-tuning.**

### Архитектура

```
text + candidates (с type)
    │
    ▼
┌─────────────────────┐
│  1. Pre-filter      │  быстрые правила (существующие):
│                     │  предлоги, type_hints, TYPE_MARKERS, "/"
│                     │  ~0.1ms
└────────┬────────────┘
         │ resolved? ──yes──→ strategy
         │ no (остались конфликты)
         ▼
┌──────────────────────────────────┐
│  2. BERT Zero-shot Type Probe    │  ~2-5ms на ВСЕХ кандидатов
│                                  │
│  Для каждого кандидата:          │
│  a) Извлечь контекст из текста   │
│     (окно ±5 токенов вокруг      │
│      matched_text)               │
│  b) Закодировать контекст        │
│     → вектор V_context           │
│  c) Для каждого типа газеттира   │
│     → V_type (кэширован)         │
│  d) cosine_sim(V_context, V_type)│
│     = confidence                 │
│  e) candidate.type совпадает     │
│     с best_type? → keep          │
│     иначе → score × 0.5          │
└────────┬─────────────────────────┘
         │
         ▼
┌─────────────────────┐
│  3. Strategy Select │  правила по остатку кандидатов
│                     │  single_match / intersection /
│                     │  midpoint / random
└────────┬────────────┘
         │
         ▼
      process_candidates SQL
```

### Компоненты

#### C0. Type Propagation (фундамент)
Без этого BERT бесполезен — type не доходит до анализатора.

- `phonetic_index.py:PhoneticEntry` + поле `type` (из geo.csv)
- `geo_matcher.py:_link_span` — возвращать `type` в dict
- `message_processor.py` — передавать `type` в SQL и в резолвер

#### C1. ONNX encoder (parser/onnx_encoder.py)

```python
import onnxruntime as ort
import numpy as np
from transformers import AutoTokenizer

class OnnxEncoder:
    def __init__(self, model_path: str):
        self.tokenizer = AutoTokenizer.from_pretrained("cointegrated/rubert-tiny2")
        self.session = ort.InferenceSession(model_path)
        # Кэш эмбеддингов типов (строится один раз)
        self._type_embeddings: dict = {}

    def _mean_pool(self, hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
        mask = np.expand_dims(mask, axis=-1).astype(np.float32)
        return np.sum(hidden * mask, axis=1) / np.maximum(np.sum(mask, axis=1), 1e-9)

    def encode(self, text: str) -> np.ndarray:
        inputs = self.tokenizer(
            text, return_tensors="np",
            padding=True, truncation=True, max_length=64
        )
        outputs = self.session.run(None, {
            'input_ids': inputs['input_ids'],
            'attention_mask': inputs['attention_mask']
        })
        return self._mean_pool(outputs[0], inputs['attention_mask'])[0]

    def warmup_types(self, type_map: dict[str, str]):
        """type_map: {type_key: russian_description}"""
        for key, desc in type_map.items():
            self._type_embeddings[key] = self.encode(desc)

    def probe(self, context: str) -> tuple[str, float]:
        """Вернуть (best_type, confidence) для контекста."""
        if not self._type_embeddings:
            return ('', 0.0)
        v = self.encode(context)
        best_type, best_score = '', -1.0
        for t, t_emb in self._type_embeddings.items():
            sim = float(np.dot(v, t_emb) / (np.linalg.norm(v) * np.linalg.norm(t_emb)))
            if sim > best_score:
                best_score, best_type = sim, t
        return best_type, best_score
```

#### C2. Type descriptions (газеттир → семантический прототип)

Каждый тип из газеттира описывается короткой фразой на русском.
Эмбеддинги считаются при старте, кэшируются.

| Тип | Описание |
|-----|----------|
| `street` | "городская улица, проспект, бульвар, переулок, площадь" |
| `village` | "село, деревня, посёлок, населённый пункт" |
| `town` | "город, районный центр, пгт" |
| `station` | "железнодорожная станция, платформа, остановка, вокзал" |
| `park` | "парк, сквер, зелёная зона отдыха" |
| `market` | "рынок, торговый центр, ярмарка" |
| `infrastructure` | "завод, фабрика, порт, аэропорт, предприятие" |

Загружаются из таблицы `geo_type_descriptions` (type, description).

#### C3. Context Extractor

Извлекает релевантный фрагмент текста вокруг matched_text
каждого кандидата.

```python
def extract_context(text: str, matched_text: str, window: int = 5) -> str:
    """Вернуть ±window токенов вокруг matched_text."""
    tokens = text.lower().split()
    mt = matched_text.lower()
    for i, t in enumerate(tokens):
        if mt in t or t in mt:
            start = max(0, i - window)
            end = min(len(tokens), i + window + 1)
            return ' '.join(tokens[start:end])
    return text[:120]
```

#### C4. Type Validator (parser/type_validator.py)

```python
class TypeValidator:
    def __init__(self, encoder: OnnxEncoder, threshold: float = 0.35):
        self._encoder = encoder
        self._threshold = threshold

    def validate(self, text: str, candidates: list[dict]) -> list[dict]:
        for c in candidates:
            ctx = extract_context(text, c.get('text', ''))
            best_type, confidence = self._encoder.probe(ctx)
            if c.get('type') == best_type and confidence >= self._threshold:
                c['_type_confirmed'] = True
                c['score'] = min(1.0, c['score'] + 0.03)  # бонус
            else:
                c['_type_confirmed'] = False
                c['score'] *= 0.5  # штраф
        # Отфильтровать безнадёжные
        return [c for c in candidates if c['score'] >= 0.5]
```

#### C5. Strategy Selector

После валидации типов:

| Остаток кандидатов | Стратегия |
|-------------------|-----------|
| 0 | `random` (fallback) |
| 1 | `single_match` |
| 2+ с "/" или "и" | `intersection` |
| 2+ "от...до"/"между" | `midpoint` |
| 2+ одного типа и ≤150м | `midpoint` |
| 2+ разных типов | `intersection` (если geom пересекаются) |
| остальное | Ollama (если доступен) |

### Процесс сборки модели

```bash
# 1. Установка зависимостей
pip install transformers onnx onnxruntime

# 2. Экспорт rubert-tiny2 в ONNX
python -m scripts.export_onnx \
    --model cointegrated/rubert-tiny2 \
    --output parser/models/rubert-tiny2.onnx

# 3. Квантизация int8 (опционально, ~3x сжатие)
python -m scripts.quantize_onnx \
    --input parser/models/rubert-tiny2.onnx \
    --output parser/models/rubert-tiny2-int8.onnx
```

Размер: ONNX fp32 ~45MB, int8 ~15MB.

### Плюсы
- **Без обучения, zero-shot** — работает сразу после сборки
- Семантическое понимание: "менти на Толбухина" → street
- ONNX int8 ~15MB, inference ~2-5ms на ВСЕХ кандидатов
- Нет внешних HTTP-запросов (всё in-process)
- Не зависит от сторонних API
- При отказе ONNX (не загрузился) → falls back на rule-based

### Минусы
- ~15MB модель в контейнере
- ONNX Runtime — ещё одна C-ext зависимость
- Zero-shot accuracy ~80-85% (не идеал, но достаточен для фильтрации)
- Descriptions типов нужно подбирать под конкретный газеттир

### Сложность: 5 дней

---

## Сравнение вариантов

| Характеристика | A (Rule) | B (Natasha) | C (BERT) | D (гибрид) |
|---|---|---|---|---|
| Зависимости | — | natasha ~50MB | **onnxruntime+bert ~25MB** | ~75MB |
| Inference | ~0.1ms | ~5-15ms | **~2-5ms** | ~5-20ms |
| Точность (оценка) | 75-80% | 82-87% | **80-85%** | 87-92% |
| Газеттир-агностик | Правила | Частично | **Zero-shot (любые типы)** | Комбинированная |
| Сложность внедрения | 2 дня | 4 дня | **5 дней** | 7 дней |
| CPU only | ✅ | ✅ | **✅** | ✅ |

---

## Детальный план работ (Вариант C)

### P10: Type propagation ✅
- `phonetic_index.py`: `PhoneticEntry` + `geo_type` ✅
- `geo_matcher.py:_link_span`: вернуть `type` в dict ✅
- `message_processor.py`: передать `geo_types` в `_INSERT_EVENT_FROM_CANDIDATES` ✅
- `semantic_resolver.py`: type-правила работают (c.get('type') больше не None) ✅
- Все 78 тестов проходят ✅

### P11: Stopwords/abbreviations (уже сделано)
- `обл`, `р-н`, `буд`, `корп`, `оф`, `кв` в stopwords.csv ✅
- Фильтр min_length=3 в `_link_span` ✅

### P12: Slash intersection fix (уже сделано)
- Правило 4a в pre-filter: `text` + `matched_name` ✅

### P13: Layer keywords (уже сделано)
- `фольц`, `бусинка` → bus; `менти`, `тцк` → cops ✅
- Fuzzy fallback в layer_classifier ✅

### P20: Экспорт модели ✅
- `scripts/export_onnx.py` — экспорт rubert-tiny2 в ONNX + int8 квантизация ✅
- `requirements.txt` — `onnxruntime~=1.27`, `transformers~=4.40` ✅
- Модель генерируется скриптом при наличии torch (CPU-only env пропускает) ✅

### P21: OnnxEncoder (parser/onnx_encoder.py) ✅
- Загрузка ONNX в `onnxruntime` с CPUExecutionProvider ✅
- `encode(texts)` — mean pooling + L2 normalization → (B, 312) ✅
- `warmup_types(type_map)` — прекомпьютинг эмбеддингов типов ✅
- `probe(context)` → cosine similarity scores per type ✅
- Graceful fallback при отсутствии модели ✅

### P22: Type descriptions table ✅
- `geo_type_descriptions` (type TEXT PK, description TEXT) — 15 типов с описаниями ✅
- `postgres/init-scripts/10-type-config.sql` — CREATE TABLE + INSERT ✅
- Загрузка при `initialize()` через `OnnxEncoder.warmup_types()` ✅

### P23: Type Validator (parser/type_validator.py) ✅
- `_extract_context(tokens, surface, span)` — окно ±5 токенов вокруг кандидата ✅
- `validate(candidates, text, tokens)` — BERT или heuristic fallback ✅
- Heuristic fallback через `_HEURISTIC_MARKERS` (без модели) ✅
- Вызывается в `SemanticResolver.resolve()` перед pre-filter ✅

### P24: Strategy Selector ✅
- TypeValidator интегрирован в `SemanticResolver.resolve()` (Phase 0) ✅
- Pre-filter использует `validated_type` (BERT) с fallback на `type` (gazetteer) ✅
- Ollama model call сохранён как Phase 2 (когда BERT + rules не хватило) ✅
- Graceful fallback при отстуствии ONNX модели ✅

### P30: DB config tables (универсализация) ✅
- `geo_type_descriptions` — описания типов для BERT zero-shot ✅
- `geo_role_patterns` — роли упоминаний (source/destination/via/landmark) + предлоги ✅
- `strategy_type_filters` — разрешённые типы для midpoint/intersection/single_match ✅
- `layer_geo_types` — релевантные типы для каждого слоя ✅
- Кэшируются при `initialize()` — без SELECT в runtime ✅

### P40: PostGIS ✅
- `process_candidates` принимает `p_geo_types TEXT[]` для midpoint type filter ✅
- `v_midpoint_types` вычисляется: p_geo_types → strategy_type_filters → fallback ✅
- SQL параметры передаются в правильном порядке из message_processor.py ✅

### P50: Очистка ✅
- `_MIDPOINT_TYPES` — заменено на `self._midpoint_types` из БД ✅
- `_TYPE_MARKERS` — заменено на `self._type_markers` из БД + geo_role_patterns ✅
- type_hints dict — заменено на `self._type_hints` из БД + geo_role_patterns ✅
- `DEFAULT_LAYER_KEYWORDS` — оставлен как fallback, данные в `layer_keywords` таблице ✅
