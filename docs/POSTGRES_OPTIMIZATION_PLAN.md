# План оптимизации PostgreSQL

## Текущая архитектура

- **Образ:** `postgis/postgis:15-3.3` (PostgreSQL 15 + PostGIS 3.3)
- **Расширения:** postgis, pg_cron, pg_stat_statements
- **Конфиг:** кастомный `postgresql.conf` (50 строк)
- **Ресурсы:** лимит 0.5 CPU / 512MB RAM (docker-compose), без reservations
- **Сеть:** `db` internal — полная изоляция от внешнего мира
- **Схема:** 6 таблиц (geo, stopwords, layer_keywords, events, events_meta, table_updates)

---

## 1. Конфигурация PostgreSQL

### 1.1 Память

**Сейчас:**
```ini
shared_buffers = 256MB          # 50% от лимита 512MB
effective_cache_size = 384MB    # 75%
work_mem = 4MB
maintenance_work_mem = 64MB
```

**Проблема:** `shared_buffers = 256MB` — верхняя граница для 512MB контейнера, PostGIS
тяжело использует кэш. `work_mem = 4MB` может быть мал для сортировок в
`process_candidates` (соединения 10+ геометрий).

**Рекомендации:**

| Параметр | Текущее | Целевое | Обоснование |
|----------|---------|---------|-------------|
| `shared_buffers` | 256MB | 384MB | PostGIS+PostgreSQL кэшируют страницы; 75% от RAM — безопасно |
| `effective_cache_size` | 384MB | 768MB | Оценка кэша ОС + shared_buffers для планировщика |
| `work_mem` | 4MB | 8MB | Сортировки в process_candidates — 1-5MB на операцию. При 20 коннектах макс. 160MB |
| `maintenance_work_mem` | 64MB | 64MB | OK для 512MB контейнера |
| `wal_buffers` | default (~16MB) | 32MB | High-churn INSERTs в events |
| `max_worker_processes` | 8 | 4 | Т.к. 0.5 CPU лимит |

**Важно:** `work_mem` умножается на число concurrent запросов. При пике
parser (8 workers) + core (10 conns) = 18 × 8MB = 144MB. В пределах 512MB.

### 1.2 Планировщик

**Сейчас:** не кастомизирован (defaults).

```ini
# —random_page_cost = 1.1     # указан в файле как комментарий
# —effective_cache_size = 384MB
```

**Проблема:** `random_page_cost` не установлен явно — используется default 4.0
(для HDD). На SSD/NVMe это заставляет планировщик переоценивать стоимость
index scan vs seq scan.

**Рекомендация:**
```ini
random_page_cost = 1.1         # для SSD (default 4.0 — для HDD)
effective_cache_size = 768MB   # поднять вместе с shared_buffers
seq_page_cost = 1.0            # default
cpu_tuple_cost = 0.01          # default
cpu_index_tuple_cost = 0.005   # default
cpu_operator_cost = 0.0025     # default
```

### 1.3 Autovacuum

**Сейчас:**
```ini
autovacuum = on
autovacuum_naptime = 30s
autovacuum_vacuum_scale_factor = 0.05
autovacuum_analyze_scale_factor = 0.02
autovacuum_vacuum_cost_delay = 10ms
```

**Проблема:** events — high-churn таблица (INSERT каждые несколько секунд +
DELETE каждые 5 минут через pg_cron). `scale_factor = 0.05` означает, что vacuum
запускается только при 5% изменений. При ~1000 rows это 50 строк — OK. Но
autovacuum может не успевать за cholera.

**Рекомендация:** добавить per-table tuning для events:
```sql
ALTER TABLE events SET (
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_threshold = 50,
    autovacuum_analyze_scale_factor = 0.01,
    autovacuum_analyze_threshold = 50
);
```

Для geo (read-only после загрузки):
```sql
ALTER TABLE geo SET (
    autovacuum_vacuum_scale_factor = 0.1,
    autovacuum_analyze_scale_factor = 0.1
);
```

### 1.4 Блокировки и конкуренция

**Сейчас:** `max_connections = 100` (избыточно для 4 сервисов).

Пиковое число коннектов: parser (8 workers + 1 listen = 9) + core (pool 10) +
model (1) + pg_cron (1) = ~21.

**Рекомендация:**
```ini
max_connections = 50            # запас 2x
```
Снижение `max_connections` уменьшает размер shared-памяти под lock-структуры.

---

## 2. Индексы

### 2.1 Текущие индексы

```sql
geo:       idx_geo_names (GIN names),  idx_geo_type (btree type),  idx_geo_geom (GiST geom)
events:    idx_events_time (btree event_time),  idx_events_geom (GiST geom),
           idx_events_layer (btree layer),  idx_events_message_id (UNIQUE btree message_id)
```

### 2.2 Проблемы

1. **`events` — нет составного индекса (layer, event_time):** фронтенд фильтрует
   по слоям + времени — sequential scan при фильтрации по одному слою.

2. **`events` — нет индекса на стратегию:** мониторинг и аналитика фильтруют
   по strategy.

3. **`events` — GIST на geom избыточен для POINT-событий:** ~80% событий —
   POINT, GiST здесь дороже btree. Но т.к. нужны пространственные запросы,
   оставляем.

4. **`geo` — idx_geo_names (GIN) избыточен для runtime:** парсер грузит geo
   в in-memory индекс, не запрашивает БД. Индекс нужен только для model
   service (pg_trgm). Сейчас pg_trgm не установлен, а GIN не оптимален для
   триграммного поиска.

### 2.3 Рекомендации

```sql
-- Составной индекс для фильтрации событий по слою + времени
CREATE INDEX IF NOT EXISTS idx_events_layer_time
    ON events (layer, event_time DESC);

-- Индекс для стратегии (мониторинг/аналитика)
CREATE INDEX IF NOT EXISTS idx_events_strategy
    ON events (strategy)
    WHERE strategy IN ('random', 'single_match');

-- Замена GIN → GIST trigram для geo (если model service использует pg_trgm)
-- Требует установки расширения pg_trgm
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- CREATE INDEX IF NOT EXISTS idx_geo_names_trgm ON geo USING gist (names gist_trgm_ops);

-- Частичный индекс для активных событий (< 1 часа)
CREATE INDEX IF NOT EXISTS idx_events_active
    ON events (event_time DESC)
    WHERE event_time > NOW() - INTERVAL '1 hour';
```

---

## 3. Таблица events: партиционирование

### 3.1 Проблема

Очистка через `DELETE FROM events WHERE event_time < NOW() - INTERVAL '1 hour'`
создаёт:
- Мёртвые строки в heap → autovacuum нагрузка
- Bloat индексов
- Заполнение WAL (каждый DELETE логируется)
- Конкуренция с INSERT + SELECT (AccessExclusiveLock на таблицу)

### 3.2 Решение: партиционирование по времени

```sql
-- Превращаем events в partitioned table
CREATE TABLE IF NOT EXISTS events (
    id SERIAL,
    message_id BIGINT,
    event_time TIMESTAMPTZ NOT NULL,
    description TEXT NOT NULL,
    photo_url TEXT,
    layer TEXT NOT NULL DEFAULT 'pig',
    matches JSONB,
    strategy VARCHAR(40) NOT NULL,
    geom GEOMETRY,
    PRIMARY KEY (id, event_time)  -- event_time обязателен в PK для partition
) PARTITION BY RANGE (event_time);

-- Ежечасные партиции
CREATE TABLE events_20260629_14 PARTITION OF events
    FOR VALUES FROM ('2026-06-29 14:00:00+00') TO ('2026-06-29 15:00:00+00');
CREATE TABLE events_20260629_15 PARTITION OF events
    FOR VALUES FROM ('2026-06-29 15:00:00+00') TO ('2026-06-29 16:00:00+00');
-- ... и т.д.

-- Автоматическое создание партиций через pg_cron
CREATE OR REPLACE FUNCTION create_next_hour_partition()
RETURNS void AS $$
DECLARE
    partition_name TEXT;
    start_time TIMESTAMPTZ;
    end_time TIMESTAMPTZ;
BEGIN
    start_time := date_trunc('hour', NOW() + INTERVAL '1 hour');
    end_time := start_time + INTERVAL '1 hour';
    partition_name := 'events_' || to_char(start_time, 'YYYYMMDD_HH24');

    IF NOT EXISTS (
        SELECT 1 FROM pg_class WHERE relname = partition_name
    ) THEN
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF events FOR VALUES FROM (%L) TO (%L)',
            partition_name, start_time, end_time
        );
    END IF;
END;
$$ LANGUAGE plpgsql;
```

**Преимущества:**
- `DROP TABLE events_20260629_13` вместо `DELETE` — мгновенно, не bloat, не WAL
- Каждая партиция маленькая → индексы строятся быстрее
- Можно настроить таблицу с default partition для неожиданных данных

---

## 4. Расширения

### 4.1 pg_cron

**Сейчас:** очистка `clean_old_events()` каждые 5 минут.

**Проблема:** DELETE при 1000+ событий может длиться >1s и создавать пиковую
нагрузку.

**Рекомендации:**
- После перехода на партиционирование: заменить `DELETE` на `DROP PARTITION`
- `cron.schedule('clean-old-events', '*/10 * * * *', ...)` — реже, т.к. партиции
  можно дропать раз в 10 минут

```sql
-- Новая версия clean_old_events для партиционированной таблицы
CREATE OR REPLACE FUNCTION clean_old_events()
RETURNS INTEGER AS $$
DECLARE
    dropped INTEGER := 0;
    part_name TEXT;
BEGIN
    FOR part_name IN
        SELECT relname FROM pg_class c
        JOIN pg_inherits i ON c.oid = i.inheritrelid
        WHERE i.inherited = (
            SELECT oid FROM pg_class WHERE relname = 'events'
        )
        AND position('_', relname) > 0
        AND to_timestamp(
            split_part(relname, '_', 2) || split_part(relname, '_', 3),
            'YYYYMMDDHH24'
        ) < NOW() - INTERVAL '2 hours'
    LOOP
        EXECUTE 'DROP TABLE IF EXISTS ' || part_name;
        dropped := dropped + 1;
    END LOOP;

    IF dropped > 0 THEN
        UPDATE events_meta SET version = version + 1, updated_at = NOW() WHERE id = 1;
    END IF;

    RETURN dropped;
END;
$$ LANGUAGE plpgsql;
```

### 4.2 pg_trgm

**Рекомендация:** установить `pg_trgm` для триграммного поиска по `geo.names`.
Model service использует `FROM geo ... WHERE similarity(names, $1) > threshold`.

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_geo_names_trgm ON geo USING gist (names gist_trgm_ops);
```

### 4.3 pg_stat_statements

**Сейчас:** установлен, но нет сборщика.

**Рекомендация:** добавить endpoint в core или cron для сбора top-N slow queries:
```sql
-- Периодический SELECT в cron или monitoring
SELECT query, calls, total_exec_time, mean_exec_time, rows
FROM pg_stat_statements
ORDER BY total_exec_time DESC LIMIT 20;
```

---

## 5. Функции и триггеры

### 5.1 validate_event_geom_strategy

Триггер `BEFORE INSERT OR UPDATE ON events` проверяет соответствие
strategy ↔ geometry type.

**Проблема:** триггер срабатывает на **каждый INSERT**, включая вставки через
CTE с `geo_execute_scenario`. Проверка `ST_GeometryType(NEW.geom)` — вызов
PostGIS на каждую строку.

**Рекомендация:** триггер лёгкий (< 0.01ms), менять не стоит. Но можно
добавить исключение для стратегий, которые всегда возвращают POINT
(nearest_point, along_line, within_polygon) — они уже проверены в SQL-функциях.

### 5.2 process_candidates

Функция `process_candidates()` выполняет сложные PostGIS операции:
- ST_Intersection (CROSS JOIN декартово произведение)
- ST_ClosestPoint + ST_Collect
- ST_ConvexHull
- ST_DWithin (с преобразованием в SRID 3857)

**Рекомендации:**

1. **Добавить параллелизм для CROSS JOIN** — если geo геометрии большие,
   `CROSS JOIN` может быть тяжёлым. Убедиться, что `max_parallel_workers_per_gather > 0`.

2. **Оптимизировать ST_Transform** — один раз на входе, не повторять:
   ```sql
   ST_Transform(ST_MakeValid(s.geom), 3857) AS geom_m
   ```
   Уже так и сделано. Хорошо.

3. **Добавить ST_Simplify** для геометрий с большим числом точек:
   ```sql
   ST_Simplify(ST_MakeValid(s.geom), 0.0001)  -- ~10м точность
   ```

4. **Добавить NOTICE при падении в fallback** — уже есть на 182-188:
   ```sql
   RAISE WARNING 'process_candidates: первый проход вернул NULL...';
   ```

### 5.3 geo_execute_scenario

**Проблема:** каждый сценарий внутри делает одинаковый запрос matches:
```sql
SELECT COALESCE(jsonb_agg(...), '[]'::jsonb) INTO v_matches
FROM geo s
JOIN unnest(p_street_ids, v_scores) AS u(id, score) ON s.id = u.id;
```
Это повторяется в 9 из 10 функций.

**Рекомендация:** вынести формирование matches в общую функцию:
```sql
CREATE OR REPLACE FUNCTION build_matches(
    p_street_ids INT[], p_street_scores FLOAT[]
) RETURNS JSONB AS $$
    SELECT COALESCE(jsonb_agg(
        jsonb_build_object(
            'street_id', s.id,
            'name', s.names[1],
            'similarity', u.score
        ) ORDER BY u.score DESC
    ), '[]'::jsonb)
    FROM geo s
    JOIN unnest(p_street_ids, COALESCE(
        p_street_scores,
        ARRAY_FILL(1.0::float, ARRAY[COALESCE(array_length(p_street_ids, 1), 0)])
    )) AS u(id, score) ON s.id = u.id;
$$ LANGUAGE sql STABLE;
```

Это сократит каждый сценарий на ~15 строк и устранит дублирование.

---

## 6. Таблица geo: покрывающие индексы

### 6.1 Проблема

Каждый запрос к `geo` делает:
- Index scan по `id` (PK)
- Fetch `names`, `geom`, `type` из heap

Для `process_candidates` и `geo_execute_scenario` почти всегда нужны
все три колонки.

### 6.2 Решение — covering indexes (PostgreSQL 15)

```sql
-- Индекс, покрывающий id + names + type + geom
CREATE INDEX IF NOT EXISTS idx_geo_cover ON geo USING btree (id)
    INCLUDE (names, type);
```

**Эффект:** index-only scan для запросов вида:
```sql
SELECT id, names, type FROM geo WHERE id = ANY(...)
```

Для `process_candidates` запрос `FROM geo WHERE id = $1` станет index-only,
если `ST_MakeValid(geom)` не нужен (а он нужен почти всегда — покрытие
ограничено).

### 6.2 BRIN index для event_time

Учитывая что events вставляются монотонно (event_time растёт), BRIN-индекс
будет компактнее btree:

```sql
-- BRIN index на event_time (~40KB vs ~2MB для btree)
CREATE INDEX IF NOT EXISTS idx_events_time_brin
    ON events USING brin (event_time)
    WITH (pages_per_range = 32);
```

Btree оставить тоже — для точечных запросов. BRIN — для сканов больших
диапазонов (снимки, TTL-очистка).

---

## 7. Вопросы безопасности

### 7.1 pg_hba.conf

**Сейчас:**
```
host    all             all             0.0.0.0/0               scram-sha-256
host    all             all             ::/0                    scram-sha-256
```

**Проблема:** открытый доступ по паролю со всех IP, хотя Docker-сеть internal.

**Рекомендация:** сузить до подсетей Docker:
```
host    all             all             172.0.0.0/8             scram-sha-256
host    all             all             10.0.0.0/8              scram-sha-256
host    all             all             192.168.0.0/16          scram-sha-256
```

### 7.2 Пользователи

Сейчас пользователи `app_user` и `ws_user` прописаны, но:
- Пароли задаются через `POSTGRES_*` env (один пароль на всех)
- Нет отдельного пользователя для model service

**Рекомендация:**
```sql
-- Пользователь для model service (только SELECT на geo)
CREATE USER model_user WITH PASSWORD 'model_pass';
GRANT CONNECT ON DATABASE postgres TO model_user;
GRANT USAGE ON SCHEMA public TO model_user;
GRANT SELECT ON geo TO model_user;
```

---

## 8. Мониторинг

### 8.1 Что добавить

| Метрика | Запрос | Период |
|---------|--------|--------|
| Размер events | `SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('events'))` | 1min |
| Slow queries | `SELECT * FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 10` | 5min |
| Autovacuum счётчики | `SELECT schemaname, relname, n_dead_tup, last_vacuum, last_autovacuum FROM pg_stat_all_tables` | 5min |
| Индекс bloat | через `pgstattuple` (extension) | daily |
| Cache hit ratio | `SELECT SUM(blks_hit) / SUM(blks_hit + blks_read) * 100 FROM pg_stat_database` | 1min |

### 8.2 Healthcheck

**Сейчас:** `pg_isready -U postgres -d postgres && psql -U postgres -d postgres -c 'SELECT pg_is_in_recovery()' | grep -q 'f' && psql -U postgres -d postgres -c 'SELECT 1'`

**Проблема:** 3 последовательных psql. Если БД под нагрузкой, каждый вызов
создаёт коннект.

**Рекомендация:** упростить:
```yaml
test: ["CMD-SHELL", "psql -U postgres -d postgres -c 'SELECT 1 FROM events LIMIT 1' > /dev/null 2>&1 || exit 1"]
```

---

## 9. Dockerfile

### 9.1 Сейчас
```dockerfile
FROM postgis/postgis:15-3.3
RUN apt-get update && apt-get install -y postgresql-15-cron
```

### 9.2 Проблемы
- Нет кэширования apt (уже есть `--mount=type=cache`)
- Нет установки pg_trgm (потребуется для model service)
- init-скрипты сортируются по алфавиту (сейчас имена 01-, 02- и т.д. —
  корректно)

### 9.3 Рекомендация
```dockerfile
RUN --mount=type=cache,target=/var/cache/apt,id=postgres-runtime \
    apt-get update && apt-get install -y \
        postgresql-15-cron \
        postgresql-15-pgtrgm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
```

---

## 10. Сводная таблица приоритетов

| № | Изменение | Приоритет | Эффект | Сложность |
|---|-----------|-----------|--------|-----------|
| 1 | `random_page_cost = 1.1` | **P0** | Ускорение всех index scan на 2-5x | 1 строка в conf |
| 2 | `shared_buffers = 384MB` | **P0** | Меньше чтений с диска | 1 строка в conf |
| 3 | `work_mem = 8MB` | **P0** | Ускорение сортировок в process_candidates | 1 строка в conf |
| 4 | per-table autovacuum для events | **P0** | Меньше bloat, стабильная производительность | 2 SQL запроса |
| 5 | Индекс `idx_events_layer_time` | **P1** | Ускорение фильтрации на фронтенде | 1 SQL |
| 6 | Индекс `idx_events_active` | **P1** | Ускорение снепшотов | 1 SQL |
| 7 | pg_trgm + индекс на geo.names | **P1** | Ускорение model service | 2 SQL + Dockerfile |
| 8 | Партиционирование events | **P2** | Мгновенная очистка, нет bloat | Неделя работы |
| 9 | BRIN index на event_time | **P2** | Экономия места (~40KB vs 2MB) | 1 SQL |
| 10 | Вынести `build_matches()` | **P2** | Устранение дублирования в 9 функциях | Рефакторинг |
| 11 | Отдельный пользователь model_user | **P2** | Безопасность | 2 SQL |
| 12 | Мониторинг через cron | **P2** | Видимость | 2 SQL + Python |

---

## 11. Быстрый старт (P0 — сделать немедленно)

```sql
-- 1. Конфиг PostgreSQL (postgresql.conf)
--    shared_buffers = 384MB
--    work_mem = 8MB
--    random_page_cost = 1.1

-- 2. Per-table autovacuum
ALTER TABLE events SET (
    autovacuum_vacuum_scale_factor = 0.01,
    autovacuum_vacuum_threshold = 50,
    autovacuum_analyze_scale_factor = 0.01,
    autovacuum_analyze_threshold = 50
);

-- 3. Индексы
CREATE INDEX IF NOT EXISTS idx_events_layer_time
    ON events (layer, event_time DESC);

CREATE INDEX IF NOT EXISTS idx_events_active
    ON events (event_time DESC)
    WHERE event_time > NOW() - INTERVAL '1 hour';

-- 4. Сузить pg_hba до Docker CIDR
```
