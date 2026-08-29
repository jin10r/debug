# Rules — PostgreSQL (PostGIS) v2.1

**Сервис:** `postgres/` (PostgreSQL 15 + PostGIS 3.3 + pg_cron)
**Порт:** 5432 (internal network only)
**Конфигурация:** `postgres/config/postgresql.conf`
**Docker:** Base image `postgis/postgis:15-3.4`, non-root, LABEL

---

## 1. Архитектурные правила

### R-DB0: БД как геопространственный калькулятор и ephemeral-буфер

PostgreSQL в этом проекте **НЕ является** архивным хранилищем или сейфом для секретов.
Его главные роли:

1. **Геопространственный калькулятор:** Выполнение тяжёлых PostGIS-операций (`process_candidates_v2`, `ST_Distance`, `ST_MakeValid`) внутри CTE, чтобы избежать пересылки сырых координат в Python.
2. **Краткосрочный буфер состояний:** Хранение актуальных событий строго за последний ~1 час (TTL). Данные старше 2 часов DROPаются атомарно на уровне метаданных.
3. **In-memory брокер:** Использование `pg_notify` для мгновенного проброса событий между processor → core.

**Следствия для разработки:**

- **Производительность > Долговечность:** Жертвуем избыточной надёжностью (sync commits, сложные транзакции) в пользу скорости записи и чтения.
- **Безопасность через изоляцию:** Отсутствие внешних портов и использование внутренней Docker-сети (`internal: true`) является достаточной защитой. Дефолтные/простые креды допустимы.
- **Минимизация состояния:** Никаких Materialized Views для данных «последнего часа». Прямые запросы по BRIN/GiST индексам быстрее и надёжнее, чем `REFRESH CONCURRENTLY`.
- **Упрощение очистки:** Очистка старых данных — атомарная операция уровня метаданных (`DROP TABLE partition`), а не построчный `DELETE`.

---

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

### R-DB2: Партиционирование events по часам

Таблица `events` партиционирована по `event_time` (RANGE) с шагом 1 час. Автоматическое создание партиций:

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
| `random` | POINT | 0 совпадений (генерируется processor) |
| `random_null` | NULL | Внутренний маркер: v2 не смог вычислить геометрию |
| `single_match` | Любой | 1 совпадение (score >= порога, по умолчанию 0.70) |
| `intersection` | POINT | Компактный кластер кандидатов (spread <= 40м), нет валидного street_segment |
| `street_segment` | LINESTRING / MULTILINESTRING | Линия, имеющая связь с 2+ кандидатами (ST_Intersects или ST_DWithin <= 50м) |
| `weighted_centroid` | POINT | 2+ кандидатов, scatter <= 1500м, **нет ни одного пересечения** между кандидатами |

**Правило:** `random`, `intersection`, `weighted_centroid` ВСЕГДА возвращают POINT (валидация через триггер). `street_segment` возвращает LINESTRING или MULTILINESTRING. `single_match` может быть любым типом. `random_null` имеет geom=NULL и НЕ проходит триггер — processor конвертирует в `random` перед INSERT.

**Описание стратегий v2:**
- `single_match`: выбирается один кандидат с highest score. При score >= `p_score_threshold` (по умолчанию 0.70, настраивается через `GEO_CANDIDATE_MIN_SCORE`) → участвует в гипотезах. При anti-list guard (сильный выброс >3000м) → принудительный single_match.
- `intersection`: среднее координат всех кандидатов. Только если spread <= 40м (или ≤200м + хотя бы одна линия). Не переопределяет валидный `street_segment`.
- `street_segment`: сегмент главной линии между первым и последним якорем. Главная линия: connection_count >= 2, tiebreak по score desc, длина desc. MULTILINESTRING → longest component. Сегмент 50–2500м. Boundary protection: GREATEST(0.001, ...) / LEAST(0.999, ...).
- `weighted_centroid`: Weighted centroid из пересечений пар (вес ×2.5) и центроидов кандидатов (вес ×1.0). Scatter <= 1500м. **Применяется ТОЛЬКО если ни одна пара кандидатов не пересекается** — любое пересечение даёт приоритет `intersection`/`street_segment`.
- `random_null`: 0 валидных кандидатов (score < порога или невалидная геометрия). Processor генерирует случайную точку в зоне `question_overlay` (R-PR22).

### R-DB9: Валидация geometry ↔ strategy

Триггер `trg_validate_event_geom` проверяет соответствие:

```sql
IF NEW.strategy IN ('random', 'weighted_centroid', 'intersection')
   AND ST_GeometryType(NEW.geom) != 'ST_Point' THEN
    RAISE EXCEPTION 'strategy "%" требует POINT-геометрию';
END IF;

IF NEW.strategy = 'street_segment'
   AND ST_GeometryType(NEW.geom) NOT LIKE 'ST_LineString%' THEN
    RAISE EXCEPTION 'strategy "street_segment" требует LINESTRING/MULTILINESTRING-геометрию';
END IF;
```

**Правило:** `random_null` имеет geom=NULL и НЕ проходит через триггер — processor конвертирует в `random` перед INSERT. Невалидная комбинация → INSERT/UPDATE отклоняется с ошибкой.

### R-DB10: process_candidates_v2 — контракт функции

```sql
CREATE OR REPLACE FUNCTION process_candidates_v2(
    p_geo_ids            INTEGER[]   DEFAULT NULL,
    p_scores             DOUBLE PRECISION[] DEFAULT NULL,
    p_texts              TEXT[]      DEFAULT NULL,
    p_hint               VARCHAR     DEFAULT NULL,
    p_score_threshold    DOUBLE PRECISION DEFAULT 0.70
)
RETURNS TABLE (
    result_strategy      TEXT,
    result_geom          GEOMETRY(4326),
    result_matches       JSONB,
    result_confidence    DOUBLE PRECISION,
    result_diagnostics   JSONB
)
```

**Входные параметры:**
- `p_geo_ids` — массив ID гео-объектов (из NLP-матчера)
- `p_scores` — массив similarity scores (0.0–1.0)
- `p_texts` — массив matched_text для дедупликации и diagnostics
- `p_hint` — игнорируется (совместимость со старыми инструментами)

**Выход:**
- `result_strategy` — `random_null` | `single_match` | `intersection` | `street_segment` | `weighted_centroid`
- `result_geom` — итоговая геометрия (POINT для random/intersection/weighted_centroid/single_match, LINESTRING/MULTILINESTRING для street_segment, NULL для random_null)
- `result_matches` — JSONB массив всех кандидатов (geo_id, name, similarity, matched_text)
- `result_confidence` — итоговый score (0.0–1.0)
- `result_diagnostics` — JSONB с типом гипотезы, geo_ids, score

**Внутренняя логика:**
1. Дедупликация по geo_id (max score), фильтр score >= 0.70, лимит 10 кандидатов
2. Загрузка `geo.geom_m` (3857) для быстрых ST_DWithin/ST_Distance
3. split на lines (LINESTRING/MULTILINESTRING) и points (POINT/POLYGON)
4. connections: ST_Intersects OR ST_DWithin(50m) между lines и всеми кандидатами
5. Main Line Election: line с connection_count >= 2, tiebreak score desc, length desc
6. Normalize Main Line: LINESTRING as-is; MULTILINESTRING → longest component
7. Street Segment: project anchors via ST_LineLocatePoint, build ST_LineSubstring с boundary protection, validate 50–2500m
8. Intersection: compact anchor cluster (radius <= 40m), только если нет валидного street_segment
9. Weighted Centroid: scatter <= 1500м, только если нет линии/сегмента
10. Anti-list Guard: сильный кандидат (score >= 0.85) на расстоянии >2000м от выбранной геометрии → fallback single_match
11. Single Match: лучший кандидат по score, tiebreak тип (line > point), длина, geo_id
12. Random Null: 0 валидных кандидатов → strategy='random_null', geom=NULL

**Вызов из Python (CTE pipeline):**

```sql
WITH pc AS (
    SELECT result_strategy, result_geom, result_matches,
           result_confidence, result_diagnostics
    FROM process_candidates_v2(
        $6::int[], $7::double precision[], $8::text[], $9::varchar
    )
),
inserted AS (
    INSERT INTO events (...) SELECT ... FROM pc
    WHERE pc.result_strategy != 'random_null'
    ON CONFLICT (message_id, event_time) DO NOTHING
    RETURNING ...
),
...
```

**Правило:** `random_null` не вставляется. Processor генерирует случайную точку и вставляет со strategy=`random` (R-PR22). INSERT + meta-update + pg_notify — один SQL-запрос.

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
max_connections = 20  -- only PgBouncer connects; pgbouncer max_db_connections=20
```

**Правило:** `pool_max_size` на стороне приложения ≤ `max_connections` PgBouncer'а
(`default_pool_size` + `max_db_connections`). PostgreSQL видит только PgBouncer.

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

### R-DB28: Docker Security

```yaml
# docker-compose.yml
postgres:
  user: "postgres:postgres"
  security_opt:
    - no-new-privileges:true
  cap_drop:
    - ALL
  cap_add:
    - NET_BIND_SERVICE
    - CHOWN
    - SETGID
    - SETUID
    - DAC_OVERRIDE
```

**Правило:** PostgreSQL нужен root для `chown`/`setuid` при старте, но `cap_drop: ALL` + minimal `cap_add`.

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

*Правила основаны на анализе postgres/ — август 2026 (обновлено: Docker security, cap_drop ALL)*
