# Processor microservice — NLP pipeline

Сервис `processor` (контейнер из `Dockerfile.processor`) — NLP-пайплайн,
потребляющий сообщения из `pending_events`, извлекающий упоминания улиц через
sliding-window матчер и сохраняющий геолоцированные события в PostGIS.

CPU-only, без GPU и без NER/LLM. ~200–400 ms на сообщение.

## Технологический стек

| Компонент | Назначение |
|-----------|-----------|
| `mawo-pymorphy3` | Морфологический анализатор (DAWG, ~15-20 MB RAM) |
| `snowballstemmer` | Стемминг для точного совпадения |
| `rapidfuzz` | Surface fuzzy + lemma fuzzy матч |
| `asyncpg` | PostgreSQL async driver |

## Архитектура модулей

```
processor/
├── main.py                 # ProcessorBot — оркестратор, SKIP LOCKED workers
├── db_adapter.py           # PostgreSQL pool
├── word_tokenizer.py       # Токенизация, слияние "5я"→"5_я"
├── text_preprocessor.py    # strip_tail, preprocess_light, is_promotional
├── morphology.py           # pymorphy3 + Lemma dataclass + LRU-кэш
├── phonetic_index.py       # Сборщик surface + lemma индексов при старте
├── geo_matcher.py          # Sliding-window линкер: 3 тира матчинга
├── semantic_resolver.py    # Pre-filter: стратегия по контексту (без модели)
├── layer_classifier.py     # cops/bus/traffic/pig по keyword-матчу
└── settings.py             # Конфигурация (наследует core/settings.py)
```

## Pipeline (по шагам)

```
1. Worker читает pending_events (SKIP LOCKED)
2. tokenize(raw_text) → tokens
3. lemmatize_tokens(tokens) → lemmas (pymorphy3, LRU 10k)
4. classify(lemmas) → 'cops'|'bus'|'traffic'|'pig'
5. [пусто / >380 симв. / promotional] → strategy=random, выход
6. find_geo(tokens, lemmas):
   - _candidates_sliding_window: окно 1..max_sliding_window(=3) токенов
   - для каждого кандидата _link_span:
     Tier 1 [Surface fuzzy] rapidfuzz(surface vs alias-names, порог 0.85)
     Tier 2 [Lemma exact]   O(1) dict lookup по lemma-tuple
     Tier 3 [Lemma fuzzy]   rapidfuzz(lemma_text vs lemma-phrases, порог 0.82)
   - dedup по geo_id: max score
   - top-K = max_entities(=5)
7. SemanticResolver.resolve (pre-filter):
   - предлоги (от/до/между) → midpoint
   - type hints (село/пгт/станция/парк) → single_match
   - дубликаты имён → None (fallback)
8. INSERT events через process_candidates() PostGIS
```

## Тиры матчинга в `_link_span`

| Тир | Метод | Порог | Source |
|-----|-------|-------|--------|
| 1 | `fuzz.token_sort_ratio(surface, alias_names)` | 0.85 | surface_fuzzy |
| 2 | exact `lemma_tuple` dict lookup | — | lemma_exact |
| 3 | `fuzz.token_sort_ratio(lemma_text, lemma_phrases)` | 0.82 | lemma_fuzzy |

Тир 1 ловит опечатки (чепаевская→чапаевская ≈ 90%). Тир 2 ловит падежи без
расходов на fuzzy. Тир 3 — fallback при POS-расхождениях лемматизатора.

## SemanticResolver (pre-filter)

Без вызова LLM/модели. Три правила:

| Правило | Условие | Стратегия |
|---------|---------|-----------|
| 1. Предлоги направления | "от X до Y", "между X и Y" | midpoint |
| 2. Type hint | "село", "пгт", "станция", "парк" и т.д. | single_match / midpoint |
| 3. Дубликаты имён | Одноимённые объекты + другой кандидат | None (fallback) |

При `None` — решение отдаётся PostGIS функции `process_candidates()`.

## Параметры калибровки

`core/settings.py` → `SimilarityConfig`:

| Поле | Default | Назначение |
|------|---------|-----------|
| `entity_similarity_threshold` | 0.82 | Порог tier-3 lemma fuzzy |
| `phonetic_match_threshold` | 0.85 | Порог tier-1 surface fuzzy |
| `surface_typo_threshold` | 0.90 | Порог tier-2 surface typo |
| `max_entities` | 5 | Финальный top-K результатов |
| `max_sliding_window` | 3 | Максимальный размер окна (токенов) |
| `prepositional_boost` | 0.05 | Бонус score при предлоге перед кандидатом |
| `geometry_min_score` | 0.85 | Мин. score вторичного матча для геометрии |
| `max_text_length` | 380 | Длиннее → strategy=random |
| `lemma_fallback_enabled` | True | Включение tier-3 lemma fuzzy |
| `midpoint_max_distance_m` | 150.0 | Макс. дистанция для midpoint |
