# Rules — PostgreSQL (PostGIS)

**Сервис:** `postgres/` (PostgreSQL 15 + PostGIS 3.3 + pg_cron)  
**Порт:** 5432 (internal network only)  
**Конфигурация:** `postgres/config/postgresql.conf`

---

## 1. Архитектурные правила

### R-DB1: Единый источник гео-данных

Таблица `geo` — ЕДИНСТВЕННЫЙ справочник гео-объектов (улицы, нас.пункты, POI). Старые таблицы `streets` и `settlements` удалены.

```sql
CREATE TABLE geo (
    id SERIAL PRIMARY KEY,
    names TEXT[] NOT NULL,
    type TEXT NOT NULL DEFAULT 'street',
    geom GEOMETRY(Geometry, 4326) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

**Правило:** Все geo-запросы идут ТОЛЬКО через таблицу `geo`.

### R-DB2: Партиционирование events по дням

Таблица `events` партиционирована по `event_time` (RANGE). Автоматическое создание партиций:

```sql
CREATE TABLE events (
    id SERIAL,
    event_time TIMESTAMPTZ NOT NULL,
    ...
    PRIMARY KEY (id, event_time)
) PARTITION BY RANGE (event_time);
```

**Правило:** Never use `DELETE FROM events WHERE event_time < ...` напрямую. Используй `clean_old_events()` или `pg_partman`.

### R-DB3: TTL через pg_cron

Очистка событий > 60 минут через pg_cron каждые 5 минут:

```sql
SELECT cron.schedule('clean-old-events', '*/5 * * * *', 'SELECT clean_old_events()');
```

**Правило:** Функция `clean_old_events()` — partition-aware: DROP целых партиций + DELETE из текущей.

### R-DB4: pg_notify как брокер сообщений

PostgreSQL используется как in-process message broker через `pg_notify`:

| Канал | Назначение | Кто слушает |
|-------|-----------|-------------|
| `events_new` | Новое событие | core (WebSocket) |
| `events_cleaned` | Очистка событий | parser (удаление фото) |
| `geo_updated` | Изменение geo | processor (reindex) |
| `photo_download` | Скачивание фото | parser |

**Правило:** pg_notify payload — JSON строка. Размер payload ≤ 8000 bytes (ограничение PostgreSQL).

### R-DB5: Идемпотентные операции

Все INSERT используют `ON CONFLICT`:

```sql
-- events
ON CONFLICT (message_id, event_time) DO NOTHING

-- pending_events
ON CONFLICT (message_id, event_time) DO NOTHING

-- events_meta
ON CONFLICT (id) DO NOTHING
```

**Правило:** Ретраи НЕ создают дубликатов.

---

## 2. Правила работы с геометрией

### R-DB6: SRID = 4326 (WGS 84)

Все геометрии хранятся в SRID 4326. Для расчётов расстояний — трансформация в SRID 3857 (метры):

```sql
-- Хранение
ST_SetSRID(ST_MakePoint(lng, lat), 4326)

-- Расстояние в метрах
ST_Distance(
    ST_Transform(geom1, 3857),
    ST_Transform(geom2, 3857)
)
```

### R-DB7: ST_MakeValid перед операциями

Все PostGIS-операции требуют валидной геометрии:

```sql
-- ✅ Правильно
SELECT ST_MakeValid(geom) FROM geo WHERE id = $1;

-- ❌ Неправильно
SELECT geom FROM geo WHERE id = $1;  -- может быть invalid
```

### R-DB8: Стратегии геометрии

| Стратегия | Тип геометрии | Когда |
|-----------|---------------|-------|
| `random` | POINT | 0 совпадений |
| `single_match` | Любой | 1 совпадение (score >= 0.85) |
| `intersection` | POINT | 2+ пересекающихся объекта |
| `street_segment` | LINESTRING | Линия, пересекающая 2+ объекта (сегмент ≤ 2000м) |
| `weighted_centroid` | POINT | 2+ объекта, scatter ≤ 1500м |

**Правило:** `random`, `intersection`, `weighted_centroid` ВСЕГДА возвращают POINT (валидация через триггер). `street_segment` всегда возвращает LINESTRING. `single_match` может быть любым типом.

**Описание стратегий:**
- `single_match`: выбирается один кандидат с highest score. При score >= 0.85 → full confidence (weight ×0.4). При score < 0.85 → `random`.
- `intersection`: пересечение геометрий двух кандидатов. Harmonic mean * 1.0 + bonus 0.3. Допускается если один >= 0.95, второй >= 0.80.
- `street_segment`: сегмент линии между первым и последним пересечением с объектами. Вес 0.9. Сегмент ≤ 2000м.
- `weighted_centroid`: Weighted centroid из пересечений пар (вес ×2.5) и центроидов кандидатов (вес ×1.0). Scatter ≤ 1500м. Confidence = AVG(base_score) * 0.85 - scatter_penalty.
- `random`: случайная точка в зоне `question_overlay`.

### R-DB9: Валидация geometry ↔ strategy

Триггер `trg_validate_event_geom` проверяет соответствие:

```sql
IF NEW.strategy IN ('random', 'weighted_centroid', 'intersection')
   AND ST_GeometryType(NEW.geom) != 'ST_Point' THEN
    RAISE EXCEPTION 'strategy "%" требует POINT-геометрию';
END IF;

IF NEW.strategy = 'street_segment'
   AND ST_GeometryType(NEW.geom) != 'ST_LineString' THEN
    RAISE EXCEPTION 'strategy "street_segment" требует LINESTRING-геометрию';
END IF;
```

**Правило:** Невалидная комбинация → INSERT/UPDATE отклоняется с ошибкой.

### R-DB10: process_candidates — контракт функции

```sql
CREATE OR REPLACE FUNCTION process_candidates(
    p_geo_ids           INT[]   DEFAULT NULL,
    p_scores            FLOAT[] DEFAULT NULL,
    p_matched_texts     TEXT[]  DEFAULT NULL,
    p_center_lon        FLOAT   DEFAULT 30.83135,
    p_center_lat        FLOAT   DEFAULT 46.49804,
    p_radius            FLOAT   DEFAULT 0.045
)
RETURNS TABLE(
    result_geom       GEOMETRY,
    result_strategy   VARCHAR(40),
    result_matches    JSONB,
    result_confidence FLOAT,
    result_diagnostics JSONB
)
```

**Входные параметры:**
- `p_geo_ids` — массив ID гео-объектов (из NLP-матчера)
- `p_scores` — массив similarity scores (0.0–1.0)
- `p_matched_texts` — массив matched_text для штрафа коротких совпадений
- `p_center_lon/lat` — центр зоны для `random`
- `p_radius` — радиус зоны для `random`

**Выход:**
- `result_geom` — итоговая геометрия (POINT для random/intersection/weighted_centroid/single_match_point, LINESTRING для street_segment)
- `result_strategy` — `random` | `single_match` | `intersection` | `street_segment` | `weighted_centroid`
- `result_matches` — JSONB массив всех кандидатов (geo_id, name, similarity, matched_text)
- `result_confidence` — итоговый score (0.0–1.0+)
- `result_diagnostics` — JSONB с типом гипотезы, geo_ids, score

**Внутренняя логика:**
1. Фильтрация по району (если есть district в кандидатах)
2. Штраф за короткие совпадения: length < 3 → *0.7, только цифры → *0.6
3. Дедупликация по `ST_AsText(ST_SnapToGrid(geom, 0.0001))`
4. Генерация 4 гипотез (H1–H4)
5. Выбор лучшей по приоритету: intersection (5) > street_segment (4) > weighted_centroid (3) > single_match (1)
6. Fallback: если нет гипотез → лучший кандидат или random

**Вызов из Python (CTE pipeline):**

```sql
WITH pc AS (
    SELECT result_geom, result_strategy, result_matches,
           result_confidence, result_diagnostics
    FROM process_candidates(
        $6::int[], $7::double precision[], $8::text[],
        $9::float, $10::float, $11::float
    )
),
inserted AS (
    INSERT INTO events (...) SELECT ... FROM pc WHERE pc.result_geom IS NOT NULL
    ON CONFLICT (message_id, event_time) DO NOTHING
    RETURNING ...
),
meta_upd AS (
    UPDATE events_meta SET version = version + 1 ...
),
notify_call AS (
    SELECT pg_notify('events_new', ...)
)
SELECT i.id FROM inserted i;
```

**Правило:** INSERT + meta-update + pg_notify — один SQL-запрос.

---

## 3. Правила индексации

### R-DB11: Обязательные индексы

```sql
-- geo
CREATE INDEX idx_geo_names ON geo USING gin (names);
CREATE INDEX idx_geo_geom ON geo USING gist (geom);

-- events
CREATE INDEX idx_events_time ON events(event_time DESC);
CREATE INDEX idx_events_geom ON events USING gist (geom);
CREATE INDEX idx_events_layer ON events(layer);
CREATE INDEX idx_events_message_id ON events(message_id);
CREATE UNIQUE INDEX idx_events_message_id_unique ON events(message_id, event_time);

-- Составные
CREATE INDEX idx_events_time_layer ON events(event_time DESC, layer);
CREATE INDEX idx_events_photo_url ON events(photo_url) WHERE photo_url IS NOT NULL;
CREATE INDEX idx_events_strategy_time ON events(strategy, event_time DESC);

-- BRIN для time-series
CREATE INDEX idx_events_time_brin ON events USING brin (event_time) WITH (pages_per_range = 32);
```

### R-DB12: GIN для массивов

Индекс `idx_geo_names` использует GIN для поиска по массиву имён:

```sql
SELECT * FROM geo WHERE 'улица Гаванная' = ANY(names);
-- Использует idx_geo_names (GIN)
```

### R-DB13: BRIN для time-series

BRIN-индексы компактнее B-tree для последовательных timestamp:

```sql
-- ✅ BRIN для TTL-очистки
CREATE INDEX idx_events_time_brin ON events USING brin (event_time);

-- ❌ B-tree избыточен для sequential data
CREATE INDEX idx_events_time_btree ON events(event_time);
```

---

## 4. Правила безопасности

### R-DB14: Role-based access control

Три роли с разными привилегиями:

| Роль | Statement timeout | Доступ |
|------|-------------------|--------|
| `parser` | 60s | SELECT, INSERT, UPDATE (все таблицы) |
| `core` | 30s | SELECT (все таблицы) |
| `maintenance` | 300s | ALL (для pg_cron, VACUUM) |

```sql
ALTER ROLE parser SET statement_timeout = '60s';
ALTER ROLE core SET statement_timeout = '30s';
ALTER ROLE maintenance SET statement_timeout = '300s';
```

### R-DB15: Connection limits

```sql
-- postgresql.conf
max_connections = 200  -- с запасом для 3 сервисов × pool_max=30
```

**Правило:** `pool_max_size` на стороне приложения ≤ `max_connections / количество сервисов`.

### R-DB16: Statement timeout

Глобальный safety net:

```sql
statement_timeout = '60s'
lock_timeout = '30s'
idle_in_transaction_session_timeout = '5min'
```

**Правило:** Каждая роль переопределяет `statement_timeout` индивидуально.

### R-DB17: SSL отключён в Docker internal network

```sql
-- postgresql.conf
ssl = off  -- Docker bridge network, нет внешнего трафика
```

**Правило:** SSL включается только при выходе за пределы Docker network.

---

## 5. Правила оптимизации

### R-DB18: Autovacuum для high-churn таблиц

Events — high-churn таблица (вставка + удаление каждые 5 минут):

```sql
-- postgresql.conf
autovacuum = on
autovacuum_naptime = 20s
autovacuum_vacuum_scale_factor = 0.05
autovacuum_analyze_scale_factor = 0.02
autovacuum_vacuum_threshold = 500
autovacuum_analyze_threshold = 250
autovacuum_vacuum_cost_delay = 5ms
autovacuum_vacuum_cost_limit = 500
```

### R-DB19: Memory tuning для 1GB контейнера

```sql
shared_buffers = 384MB           -- ~38% RAM
effective_cache_size = 768MB     -- ~75% RAM
work_mem = 8MB                   -- для сортировок
maintenance_work_mem = 128MB     -- для VACUUM, CREATE INDEX
```

### R-DB20: Parallel queries

```sql
max_parallel_workers_per_gather = 2
max_parallel_workers = 4
parallel_tuple_cost = 0.01
parallel_setup_cost = 100
```

**Правило:** PostGIS CTE scans используют parallel workers.

### R-DB21: WAL tuning

```sql
wal_level = replica             -- для streaming replication
max_wal_size = 1GB
min_wal_size = 256MB
wal_buffers = 16MB
wal_compression = zstd
```

---

## 6. Правила миграций

### R-DB22: Init scripts — идемпотентны

Все init-scripts используют `IF NOT EXISTS`:

```sql
CREATE TABLE IF NOT EXISTS geo (...);
CREATE INDEX IF NOT EXISTS idx_geo_names ON geo USING gin (names);
```

**Правило:** Скрипты можно запускать повторно без побочных эффектов.

### R-DB23: Миграции через ALTER TABLE

Schema changes — через `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`:

```sql
ALTER TABLE events ADD COLUMN IF NOT EXISTS message_id BIGINT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_events_message_id_unique
    ON events(message_id, event_time);
```

### R-DB24: Data migrations — в init scripts

Data migrations (напр., миграция photo_url) — в `ensure_schema()` при старте parser:

```python
status = await conn.execute(
    "UPDATE events "
    "SET photo_url = '/media/events/' || regexp_replace(photo_url, '^.*/', '') "
    "WHERE photo_url LIKE '/app/media/events/%'"
)
```

---

## 7. Правила мониторинга

### R-DB25: pg_stat_statements

Расширение установлено для аудита запросов:

```sql
-- Топ-20 медленных запросов
SELECT calls, total_exec_time, mean_exec_time, query
FROM pg_stat_statements
ORDER BY total_exec_time DESC
LIMIT 20;
```

### R-DB26: pg_cron jobs

Все фоновые задачи через pg_cron:

| Job | Интервал | Назначение |
|-----|----------|------------|
| `clean-old-events` | `*/5 * * * *` | Очистка событий > 60min |
| `manage-event-partitions` | `0 * * * *` | Создание партиций на +2 дня |
| `refresh-events-mv` | `*/30 * * * *` | Обновление MV events |
| `refresh-geo-mv` | `*/5 * * * *` | Обновление MV geo |

### R-DB27: Materialized views

MV для дашбордов (обновляются pg_cron):

```sql
-- Recent events by layer
CREATE MATERIALIZED VIEW mv_recent_events_by_layer AS
SELECT layer, COUNT(*) AS count, MAX(event_time) AS latest_time
FROM events WHERE event_time > NOW() - INTERVAL '1 hour'
GROUP BY layer WITH NO DATA;
```

**Правило:** MV обновляются через `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

---

## 8. Антипаттерны (ЗАПРЕЩЕНО)

| Антипаттерн | Почему | Правило |
|-------------|--------|---------|
| DELETE по времени без partitioning | O(n)扫描, lock contention | R-DB2 |
| Отсутствие ST_MakeValid | Invalid geometry → crash | R-DB7 |
| raw WKT в JSON payload | 8KB limit, overhead | R-DB4 |
| shared_buffers > 50% RAM | OOM risk | R-DB19 |
| max_connections без учёта pool | Connection starvation | R-DB15 |
| SSL в Docker internal | Unnecessary overhead | R-DB17 |
| Ручной INSERT без ON CONFLICT | Дубликаты при ретраях | R-DB5 |
| B-tree для time-series | BRIN компактнее | R-DB13 |

---

*Правила основаны на анализе postgres/ — июль 2026*
