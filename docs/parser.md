# Parser microservice — логика и алгоритм

Парсер мониторит Telegram-канал, извлекает упоминания улиц из сообщений и
сохраняет геолокированные события в PostgreSQL. Sliding-window NLP-пайплайн
на CPU (без GPU и без NER), русский язык, ~200–400 ms на сообщение.

## Технологический стек

| Компонент | Назначение |
|-----------|-----------|
| `kurigram` (pyrogram fork) | Telegram MTProto client |
| `mawo-pymorphy3` 1.0.4 | Морфологический анализатор (DAWG, ~15-20 MB RAM) |
| `rapidfuzz` 3.0+ | Surface fuzzy + lemma fuzzy матч против alias-индекса |
| `spacy` 3.7+ | Семантический анализ пространственных отношений (опционально) |
| `asyncpg` 0.29+ | PostgreSQL async driver |

## Архитектура модулей

```
parser/
├── monitoring.py          # Pyrogram client + asyncio.Queue + worker
├── message_processor.py   # Оркестратор pipeline
├── text_preprocessor.py   # strip_tail + preprocess_light (мягкая очистка)
├── word_tokenizer.py      # regex-разбивка по не-буквенным символам; ## → is_anchored=True
├── morphology.py          # mawo_pymorphy3 + Lemma dataclass + LRU-кэш
├── layer_classifier.py    # cops/bus/traffic/pig по keyword-матчу (hashtag-override)
├── phonetic_index.py      # Сборщик surface + lemma индексов при старте
├── geo_matcher.py      # Sliding-window линкер: 3 тира (surface/lemma)
├── spacy_relation_extractor.py  # Семантический анализ пространственных отношений (spaCy)
├── semantic_resolver.py   # Определение стратегии geo-резолюции (pre-filter + Ollama)
└── db_adapter.py          # PostgreSQL pool
```

## Pipeline (по шагам)

```
1. monitoring.py: Telegram handler → asyncio.Queue maxsize=1000
2. _message_worker → MessageProcessor.process_message
3. _extract_text — plain str из pyrogram, защита от UTF-16 surrogates
4. strip_tail(text) — убрать «подписаться/сообщить» хвост
5. preprocess_light(text) — HTML/время/укр-буквы+суффиксы; регистр сохранён = preserved
6. _sanitize_text — выбросить одиночные суррогаты
7. strip_emoji → match_text (только для матчинга)
8. word_tokenizer.tokenize(match_text) → tokens
9. Morphology.lemmatize_tokens(tokens) → lemmas (pymorphy3, LRU 10k)
10. LayerClassifier.classify(lemmas, tokens) → 'cops'|'bus'|'traffic'|'pig'
    └─ ## -якорные токены проверяются первыми (hashtag-override)
11. [пусто / >380 симв.] → strategy=random, выход
  12. GeoMatcher.find_geo(tokens, lemmas):
    _strip_noise (пунктуация)
    _candidates_sliding_window: 1..max_sliding_window(=3) токенов
    для каждого кандидата _link_span:
      Tier 1 [Surface fuzzy] rapidfuzz(surface vs alias-names, порог 0.85)
      Tier 2 [Lemma exact]   O(1) dict lookup по lemma-tuple
      Tier 3 [Lemma fuzzy]   rapidfuzz(lemma_text vs lemma-phrases, порог 0.82)
    dedup по geo_id: max score; is_anchored → +0.05 bonus
    top-K = max_entities(=5)
  12.5. SpaCyRelationExtractor.extract_plan (если enabled):
    Связывание кандидатов с токенами spaCy по span
    Уточнение типа по контекстным маркерам (сквер → park, улица → street)
    Извлечение пространственных отношений (между X и Y, от X к Y, в квадрате, недалеко от)
    Генерация плана: tool/args (single_match, intersection, midpoint)
 13. SemanticResolver.resolve (с учётом spaCy плана):
    Priority 1: spaCy план → маппинг в strategy
    Priority 2: pre-filter правила (предлоги, типы)
    Priority 3: Ollama модель (если enabled)
 14. process_candidates / geo_execute_scenario SQL (PostGIS): пересечения → geom + strategy
15. INSERT events ON CONFLICT (message_id) + pg_notify('events_new', feature_json)
```

## Блок-схема (Mermaid)

```mermaid
flowchart TD
    A[Pyrogram handler<br/>chat filter] --> B[asyncio.Queue<br/>maxsize=1000]
    B --> C[_message_worker<br/>+heartbeat /tmp/parser_heartbeat]
    C --> D[strip_tail<br/>+ preprocess_light]
    D -->|match_text| E[word_tokenizer.tokenize<br/>## → is_anchored]
    E --> F[Morphology.lemmatize_tokens<br/>mawo_pymorphy3 LRU10k<br/>~30-80ms]
    F --> G[LayerClassifier.classify<br/>hashtag-override → keyword ∩ lemmas]
    F -->|lemmas| H
    E -->|tokens| H[GeoMatcher.find_geo<br/>sliding-window 1..3<br/>Tier1 surface fuzzy 0.85<br/>Tier2 lemma exact<br/>Tier3 lemma fuzzy 0.82<br/>~30-120ms]
    H --> I[SpaCyRelationExtractor<br/>type refinement +<br/>spatial patterns<br/>~10-50ms]
    I --> J[SemanticResolver<br/>spaCy plan → strategy<br/>pre-filter → Ollama<br/>~5-20ms]
    J --> K[process_candidates SQL<br/>ST_Intersects + pseudo_radius<br/>~5-50ms]
    G -.layer.-> L
    K -.geom + strategy.-> L[INSERT events ON CONFLICT<br/>+ pg_notify events_new]
    L --> M[app LISTEN events_new<br/>→ WebSocket broadcast]
```

## Тиры матчинга в `_link_span`

| Тир | Метод | Порог | Source |
|-----|-------|-------|--------|
| 1 | `fuzz.token_sort_ratio(surface, alias_names)` | 0.85 | `surface_fuzzy` |
| 2 | exact `lemma_tuple` dict lookup | — | `lemma_exact` |
| 3 | `fuzz.token_sort_ratio(lemma_text, lemma_phrases)` | 0.82 | `lemma_fuzzy` |

Тир 1 ловит опечатки (чепаевская→чапаевская ≈ 90%). Тир 2 ловит падежи без
расходов на fuzzy. Тир 3 — fallback при POS-расхождениях лемматизатора.

## Strategy от `process_candidates`

| Strategy | Когда | Геометрия |
|----------|-------|-----------|
| `random` | matches пустой или текст >380 симв. | случайная точка в overlay-зоне |
| `single_match` | 1 улица, или 2+ без пересечения | full geom лучшей улицы |
| `single_intersection` | 2+ улиц, 1 точка пересечения (или псевдо) | POINT |
| `polygon_intersection` | 2+ улиц, 2+ точек пересечения | LINESTRING/POLYGON |

## Параметры калибровки (`core/settings.py` → `SimilarityConfig`)

| Поле | Default | Назначение |
|------|---------|-----------|
| `entity_similarity_threshold` | 0.82 | Порог tier-3 lemma fuzzy (0-1) |
| `phonetic_match_threshold` | 0.85 | Порог tier-1 surface fuzzy (0-1) |
| `max_entities` | 5 | Финальный top-K результатов |
| `max_sliding_window` | 3 | Максимальный размер окна (токенов) |
| `prepositional_boost` | 0.05 | Бонус score при предлоге перед кандидатом |
| `pseudo_intersection_radius_meters` | 150.0 | Радиус псевдо-пересечений в SQL |
| `max_text_length` | 380 | Длиннее → strategy=random |
| `lemma_fallback_enabled` | True | Включение tier-3 lemma fuzzy |

### Параметры SpaCy (`core/settings.py` → `SpaCyConfig`)

| Поле | Default | Назначение |
|------|---------|-----------|
| `enabled` | True | Включение spaCy анализа (опционально для экономии ресурсов) |
| `model_name` | 'ru_core_news_sm' | Модель spaCy для русского языка |
| `timeout_ms` | 50 | Таймаут обработки (предупреждение если превышен) |

| Поле | Default | Назначение |
|------|---------|-----------|
| `history_limit` | 100 | Сообщений из истории при старте |
| `message_queue_maxsize` | 1000 | Размер asyncio.Queue |

## Метрики качества (события_экспорт, ~99 событий)

| Метрика | Значение |
|---------|---------|
| `random` % | ~28 (нет распознанной улицы) |
| `single_match` % | ~58 |
| `single_intersection` % | ~9 |
| `polygon_intersection` % | ~5 |
| FP stopwords заблокировано | мусорской, семья, книжный |

## Известные ограничения

1. **Отсутствующие объекты**: Ватутина, Бабеля, Старопортофранковская, Чепаевская
   и др. не в `geo.csv` → стратегия `random`.
2. **Упрощённые геометрии**: ~132 улицы хранятся как прямой отрезок из 2 точек;
   `ST_Intersects` находит пересечение не для всех реально перекрёстных пар.
3. **Sliding-window лимит 3**: улицы из 4+ слов не покрываются одним окном.
4. **Один последовательный воркер**: при росте потока канала throughput упирается
   в одно ядро. Решение: `ProcessPoolExecutor` для `find_streets`.
5. **Layer-keywords**: точное равенство лемм. Производные формы расширяются
   через явное перечисление в `DEFAULT_LAYER_KEYWORDS`.
6. **SpaCy модель**: требует дополнительной памяти (~50-100MB) и времени (~10-50ms).
   Может быть отключена через `spacy.enabled = False` в настройках.

## SpaCyRelationExtractor

Модуль для семантического анализа пространственных отношений между кандидатами.

### Функциональность

- **Связывание кандидатов с токенами**: мапинг candidate spans на spaCy токены через char_span
- **Уточнение типов по контексту**: фильтрация кандидатов по синтаксически связанным маркерам
  (например, "Кировский сквер" → только park-типы, не street)
- **Извлечение пространственных отношений**: определение паттернов:
  - "между X и Y" → bounds → intersection
  - "от X в направлении Y" / "от X к Y" → from, to → midpoint
  - "в квадрате A, B, C, D" → bounds → intersection
  - "недалеко от A, B, C" / "рядом с A" → objects → single_match/intersection

### Интеграция

Модуль вставляется в pipeline между GeoMatcher и SemanticResolver:
1. GeoMatcher.find_geo → кандидаты с geo_id
2. SpaCyRelationExtractor.extract_plan → уточнение типов + пространственные роли
3. SemanticResolver.resolve → финальная стратегия (с учётом spaCy плана)
4. process_candidates SQL → вычисление геометрии

### Конфигурация

```python
# core/settings.py
@dataclass
class SpaCyConfig:
    enabled: bool = True  # Отключить для экономии ресурсов
    model_name: str = 'ru_core_news_sm'
    timeout_ms: int = 50
```

### Производительность

- Lazy loading модели при первом запросе
- Таймаут с предупреждением при превышении 50ms
- Опциональное отключение без нарушения обратной совместимости
