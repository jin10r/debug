# Rules — Processor Service (NLP Pipeline)

**Сервис:** `processor/` (pymorphy3 + rapidfuzz + PostGIS)
**Точка входа:** `python -m processor.main`
**Порт:** Нет (только heartbeat healthcheck)

---

## 1. Архитектурные правила

### R-PR1: Processor — ЕДИНСТВЕННЫЙ NLP обработчик

Processor — единственный сервис, выполняющий NLP-пайплайн:
```
pending_events → tokenize → lemmatize → classify → find_geo → resolve → INSERT INTO events
```

**Запрещено:**
- NLP-код в parser (кроме `text_preprocessor.py`)
- NLP-код в core
- Дублирование логики лемматизации/классификации в других сервисах

### R-PR2: Async архитектура — worker pool

Processor работает в одном asyncio event loop с пулом воркеров:

```python
self._worker_concurrency = max(1, min(8, settings.processor.worker_concurrency))

for _ in range(self._worker_concurrency):
    self._spawn_worker()
```

**Правило:** Каждый воркер потребляет одну запись из `pending_events` через `FOR UPDATE SKIP LOCKED`.

### R-PR3: Graceful shutdown — drain + cancel

При SIGTERM processor:
1. `self._running = False` — воркеров перестают появляться новые
2. Все worker tasks отменяются через `task.cancel()`
3. `asyncio.gather(*tasks, return_exceptions=True)` — ждём завершения
4. UNLISTEN `geo_updated` + release connection
5. Закрытие GeoMatcher, SemanticResolver, DB pool

```python
async def shutdown(self):
    self._running = False
    tasks = [t for t in self._worker_tasks if t and not t.done()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    # ... close resources
```

**Правило:** `stop_grace_period` в docker-compose ≥ 30s.

### R-PR4: Heartbeat healthcheck

Processor пишет timestamp в `/tmp/processor_heartbeat` каждую секунду:

```python
@staticmethod
def _write_heartbeat():
    with open('/tmp/processor_heartbeat', 'w') as f:
        f.write(str(int(datetime.now(timezone.utc).timestamp())))
```

Docker healthcheck:
```bash
test -f /tmp/processor_heartbeat && [ $(( $(date +%s) - $(cat /tmp/processor_heartbeat) )) -lt 60 ]
```

**Правило:** Если heartbeat не обновляется >60s + `start_period` 120s → контейнер перезапускается.

### R-PR5: Worker auto-respawn

```python
def _supervise_worker(self, task):
    if not self._running:
        return
    exc = task.exception()
    logger.critical(f"Worker died unexpectedly ({exc!r}) — respawning")
    self._spawn_worker()
```

**Правило:** Падший воркер автоматически пересоздаётся.

### R-PR6: Retry with exponential backoff

```python
# Transient errors — до 8 попыток
# Permanent errors — до 3 попыток
attempt < (8 if transient else 3)
delay = min(2 ** attempt, 30)
```

**Правило:** После исчерпания попыток → `mark_error` (статус 'error' в pending_events).

---

## 2. Правила NLP-пайплайна

### R-PR7: Tokenize → Lemmatize → Classify → FindGeo → Resolve → Insert

```python
tokens = tokenize(raw_text)
lemmas = self.morph.lemmatize_tokens(tokens)
layer = self.layer_classifier.classify(lemmas)
entities = await self.matcher.find_geo(tokens=tokens, lemmas=lemmas)
# ... resolve conflicts ...
await self._insert_event_from_candidates(...)
```

**Правило:** Порядок шагов фиксирован. Нельзя менять последовательность.

### R-PR8: is_promotional → random strategy

Промо/спам детектируется через `is_promotional()`. При срабатывании → `strategy=random`, сообщение НЕ игнорируется.

```python
if promotional or not raw_text or len(raw_text) > max_text_length:
    return self._enrich(
        await self._insert_event(..., strategy='random', geom_wkt=self._random_point()),
        tokens=tokens, geo_ids=[],
    )
```

**Правило:** Даже спам-сообщения попадают на карту (random точка).

### R-PR8.1: Каждое сообщение отображается на фронтенде

Любое сообщение из `pending_events` ДОЛЖНО попасть в `events` и быть видимым на карте.

- **Распознанные локации:** normal strategy (single_match / intersection / midpoint) — точная геометрия
- **Нераспознанные / спам / пустые:** `strategy=random` — точка в зоне `question_overlay`

**Запрещено:**
- Дропать сообщения из-за отсутствия гео-кандидатов (`return None`)
- Отбрасывать сообщения после LLM-детекции спама — вместо этого `strategy=random`
- Фильтровать сообщения по длине текста, промо-характеру или слою

**Исключение:** Технические дубликаты (`ON CONFLICT DO NOTHING`). Сообщение признаётся дубликатом только при совпадении `(message_id, event_time)`.

```python
# ❌ НЕПРАВИЛЬНО: дроп при LLM junk
if llm_result.get('layer') == 'junk':
    return None  # нарушает R-PR8.1

# ✅ ПРАВИЛЬНО: random strategy для junk
if llm_result.get('layer') == 'junk':
    layer = 'junk'
    strategy = 'random'
```

### R-PR9: SemanticResolver — опциональный

SemanticResolver используется ТОЛЬКО при >1 geo-кандидата:

```python
if len(geo_ids) > 1:
    resolved = await self.resolver.resolve(
        text=raw_text, tokens=tokens, lemmas=lemmas, candidates=entities,
    )
```

**Правило:** При 0 кандидатах → random. При 1 → single_match без resolver.

### R-PR10: process_candidates в PostGIS

Для 2+ кандидатов — INSERT через CTE с `process_candidates()`:

```python
_INSERT_EVENT_FROM_CANDIDATES = """
    WITH pc AS (
        SELECT result_geom, result_strategy, result_matches
        FROM process_candidates($6::int[], $7::double precision[], $8::text[], $9::varchar)
    ),
    inserted AS (
        INSERT INTO events (...) SELECT ... FROM pc WHERE pc.result_geom IS NOT NULL
        ON CONFLICT (message_id, event_time) DO NOTHING
        RETURNING ...
    ),
    ...
"""
```

**Правило:** Геометрия вычисляется в PostGIS, НЕ в Python.

---

## 3. Правила работы с БД

### R-PR11: Pending events — SKIP LOCKED

```python
"SELECT id, message_id, text, event_time, photo_file_id "
"FROM pending_events "
"WHERE status = 'pending' "
"ORDER BY created_at "
"LIMIT 1 "
"FOR UPDATE SKIP LOCKED"
```

**Правило:** Multiple workers безопасно потребляют очередь без блокировок.

### R-PR12: Idempotent INSERT

```sql
ON CONFLICT (message_id, event_time) DO NOTHING
```

**Правило:** Ретраи НЕ создают дубликатов.

### R-PR13: CTE pipeline — INSERT + meta + notify

```sql
WITH inserted AS (...),
     meta_upd AS (UPDATE events_meta SET version = version + 1 ...),
     notify_call AS (SELECT pg_notify('events_new', ...))
SELECT i.id, i.layer, i.strategy FROM inserted i
```

**Правило:** INSERT + meta-update + pg_notify — ОДИН SQL-запрос.

### R-PR14: LISTEN geo_updated для reindex

```python
await self._listen_conn.add_listener("geo_updated", self._on_geo_updated)
```

При `geo_updated` — перестройка PhoneticIndex:

```python
async def _on_geo_updated(self, conn, pid, channel, payload):
    geo_id = json_lib.loads(payload).get('geo_id')
    if geo_id:
        task = asyncio.create_task(_reindex(self.matcher.reindex_geo, self.db.pool, geo_id))
    else:
        task = asyncio.create_task(_reindex(self.matcher.reindex_all, self.db.pool))
```

**Правило:** Reindex запускается как background task (не блокирует воркеры).

### R-PR15: Один asyncpg pool

```python
self.__pool = await asyncpg.create_pool(
    min_size=settings.db.pool_min_size,   # 5
    max_size=settings.db.pool_max_size,   # 30
    command_timeout=settings.db.command_timeout,  # 60s
)
```

**Правило:** Один пул на весь процесс. Connection release — через `async with pool.acquire()`.

---

## 4. Правила работы с NLP модулями

### R-PR16: Morphology — LRU cache

```python
self.morph = Morphology()  # pymorphy3 + snowballstemmer, LRU 10k
```

**Правило:** Лемматизация кэшируется. Один экземпляр `Morphology` на процесс.

### R-PR17: PhoneticIndex — in-memory DAWG

```python
self.index = PhoneticIndex(self.morph)  # stem-based inverted index
```

**Правило:** Индекс строится при старте из `geo` таблицы. При `geo_updated` — инкрементальный reindex.

### R-PR18: GeoMatcher — sliding window 1-3 tokens

```python
entities = await self.matcher.find_geo(tokens=tokens, lemmas=lemmas)
# Три тира:
# Tier 1: stem exact (snowballstemmer)
# Tier 1b: semantic (e5-small ONNX + FAISS) — опционально
# Tier 2: surface typo (rapidfuzz token_sort_ratio ≥ 0.85)
```

**Правило:** Матчер возвращает список `[{geo_id, score, text, type}]`.

### R-PR19: ProcessPoolExecutor для fuzzy matching

```python
# geo_matcher.py
loop = asyncio.get_event_loop()
result = await loop.run_in_executor(
    self._executor, _fuzzy_match, query, phrases, threshold
)
```

**Правило:** CPU-bound fuzzy matching вынесен в `ProcessPoolExecutor` для неблокирования event loop.

---

## 5. Правила безопасности

### R-PR20: Parameterized queries

Все SQL-запросы используют параметризацию ($1, $2, ...).

```python
await conn.execute(
    "INSERT INTO pending_events ... VALUES ($1, $2, $3, $4)",
    message_id, text, event_time, photo_file_id,
)
```

### R-PR21: Sanitize text

```python
@staticmethod
def _sanitize_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    return text.encode('utf-8', errors='replace').decode('utf-8')
```

**Правило:** Все тексты проходят UTF-8 sanitize перед вставкой в БД.

### R-PR22: Random point в зоне

```python
def _random_point(self):
    qo = settings.question_overlay
    r = radius * math.sqrt(random.random())
    theta = random.random() * 2 * math.pi
    return f"POINT({center_lng + r * math.cos(theta)} {center_lat + r * math.sin(theta)})"
```

**Правило:** Random точка генерируется внутри заданной зоны (question_overlay), не глобально.

---

## 6. Правила логирования

### R-PR23: Structured logging

```python
if _LOG_FORMAT == 'json':
    from core.utils.logging_config import JSONFormatter
    _formatter = JSONFormatter()
else:
    _formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
```

### R-PR24: Log levels

- `DEBUG` — progress обработки, детали matching
- `INFO` — инициализация, успешная обработка
- `WARNING` — retry, fallback, потенциальные проблемы
- `ERROR` — ошибки обработки, DB-ошибки
- `CRITICAL` — crash воркера (auto-respawn)

---

## 7. Правила конфигурации

### R-PR25: Settings из core/settings.py

Processor переиспользует `core/settings.py`. Собственные настройки минимальны:

```python
@dataclass
class ProcessorConfig:
    worker_concurrency: int = 5
    poll_interval: float = 0.5
```

### R-PR26: Environment variables — только DB credentials

| Переменная | Обязательна | Описание |
|-----------|-------------|----------|
| `POSTGRES_USER` | да | Пользователь PostgreSQL |
| `POSTGRES_PASSWORD` | да | Пароль PostgreSQL |
| `POSTGRES_DB` | нет | Имя БД (default: postgres) |

**Правило:** Всё остальное — хардкод в `settings.py`.

---

## 8. Антипаттерны (ЗАПРЕЩЕНО)

| Антипаттерн | Почему | Правило |
|-------------|--------|---------|
| NLP в parser | Нарушение разделения | R-PR1 |
| Синхронные SQL в воркере | Блокирует event loop | R-PR2 |
| INSERT без ON CONFLICT | Дубликаты | R-PR12 |
| Геометрия в Python | Неточность, нет PostGIS | R-PR10 |
| Ручной pool.release() без try | Утечка соединений | R-PR15 |
| Отсутствие SKIP LOCKED | Гонка воркеров | R-PR11 |
| Hardcoded credentials | Security | R-PR26 |

---

*Правила основаны на анализе кодовой базы processor/ — июль 2026*
