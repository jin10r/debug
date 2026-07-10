# Postgres microservice — хранилище (PostGIS)

> Общая архитектура: [docs/ARCHITECTURE.md](ARCHITECTURE.md)

Сервис `postgres` (контейнер из `Dockerfile.postgres`) — PostgreSQL 15 + PostGIS 3.3.
Хранит газеттир улиц и геолоцированные события. Во внутренней сети
(`db`, `internal: true`) — наружу не публикуется. Креды захардкожены
(`postgres/postgres`), синхронизированы с `core/settings.py DatabaseConfig`.

---

## Технологический стек

| Компонент | Значение |
|-----------|----------|
| Базовый образ | `postgis/postgis:15-3.3` |
| PostgreSQL | 15 |
| PostGIS | 3.3 |
| Расширения | pg_cron (TTL), pg_stat_statements (мониторинг) |
| Язык PL | plpgsql |

---

## Конфигурация (`postgres/config/postgresql.conf`)

| Параметр | Значение | Описание |
|----------|----------|----------|
| shared_buffers | 384MB | Буферы общей памяти |
| effective_cache_size | 768MB | Эффективный размер кэша |
| work_mem | 8MB | Память для сортировки/hashing |
| maintenance_work_mem | 128MB | Память для VACUUM/CREATE INDEX |
| max_connections | 200 | Максимум соединений |
| random_page_cost | 1.1 | Стоимость случайного чтения (SSD) |
| effective_io_concurrency | 200 | Параллельные I/O операции |
| max_parallel_workers_per_gather | 2 | Параллельные воркеры на запрос |
| max_parallel_workers | 4 | Всего параллельных воркеров |
| statement_timeout | 60s | Глобальный таймаут (переопределяется per-role) |
| lock_timeout | 30s | Таймаут блокировок |
| wal_level | replica | Уровень WAL (для репликации) |
| max_wal_size | 1GB | Максимальный размер WAL |
| wal_compression | zstd | Сжатие WAL |
| autovacuum_naptime | 20s | Интервал autovacuum |
| autovacuum_vacuum_scale_factor | 0.05 | Порог для VACUUM |

---

## Схема (`postgres/init-scripts/02-tables.sql`)

### Таблицы

| Таблица | Назначение | Ключевые индексы |
|---------|-----------|------------------|
| `geo` | Газеттир (1728 записей, 8 типов): names TEXT[], geom GEOMETRY, type | GIN(names), GiST(geom) |
| `events` | События с TTL 60 мин, партиционирована по дням | time DESC, GiST(geom), layer, message_id UNIQUE |
| `stopwords` | Стоп-слова матчера | PK(word) |
| `layer_keywords` | Ключевые слова классификации слоёв | PK(layer) |
| `events_meta` | Метаданные для WS-синхронизации (version, max_event_id) | version++ на INSERT/DELETE |
| `geo_type_descriptions` | Описания типов для zero-shot BERT | PK(type) |
| `geo_role_patterns` | Роли geo-объектов (source/destination/via/landmark) | PK(role) |
| `strategy_type_filters` | Разрешённые типы для каждой стратегии | PK(strategy) |
| `layer_geo_types` | Релевантные типы для каждого слоя | PK(layer) |

### Партиционирование events

```sql
CREATE TABLE events (
    id SERIAL,
    message_id BIGINT,
    event_time TIMESTAMPTZ NOT NULL,
    description TEXT NOT NULL CHECK (char_length(description) <= 500),
    photo_url TEXT,
    layer TEXT NOT NULL DEFAULT 'pig'
        CHECK (layer IN ('pig', 'cops', 'bus', 'traffic')),
    matches JSONB,
    strategy VARCHAR(40) NOT NULL CHECK (strategy IN (
        'random', 'single_match', 'intersection', 'midpoint',
        'pseudo_intersection', 'proximity', 'centroid', 'area'
    )),
    geom GEOMETRY,
    PRIMARY KEY (id, event_time)
) PARTITION BY RANGE (event_time);
```

Партиции создаются на текущий день + 2 дня вперёд автоматически при инициализации.
`clean_old_events()` использует `DROP TABLE` для целых партиций и `DELETE` для текущей.

---

## Init-скрипты (13 файлов, по порядку)

| # | Скрипт | Назначение |
|---|--------|-----------|
| 01 | `01-extensions.sql` | PostGIS, pg_cron, pg_stat_statements |
| 02 | `02-tables.sql` | Схема таблиц + индексы + партиции |
| 03 | `03-functions.sql` | TTL-очистка событий (pg_cron каждые 5 мин) |
| 04 | `04-load-data.sql` | Идемпотентная загрузка geo.csv, stopwords.csv |
| 05 | `05-role-timeouts.sql` | Таймауты per-role: parser 60s, core 30s, maintenance 300s |
| 06 | `06-notify-trigger.sql` | NOTIFY об изменении улиц (для парсера) |
| 07 | `07-indexes.sql` | Дополнительные индексы |
| 08 | `08-process-candidates.sql` | Geo-resolution: process_candidates() |
| 09 | `09-event-geom-trigger.sql` | Валидация geometry type ↔ strategy |
| 10 | `10-type-config.sql` | geo_type_descriptions, geo_role_patterns, strategy_type_filters, layer_geo_types |
| 11 | `11-partition-maintenance.sql` | Создание/удаление партиций |
| 12 | `12-materialized-views.sql` | Материализованные представления |
| 14 | `14-training-examples.sql` | Тренировочные данные |

> Init-скрипты исполняются только при **пустом** томе. После правки
> `geo.csv` нужно либо `docker compose down -v` (пересоздать том), либо
> вставить запись вручную через `psql`.

---

## Роли и таймауты (`05-role-timeouts.sql`)

| Роль | statement_timeout | lock_timeout | Права |
|------|-------------------|--------------|-------|
| `parser` | 60s | 30s | ALL на все таблицы |
| `core` | 30s | 15s | SELECT на все таблицы |
| `maintenance` | 300s | 120s | ALL на все таблицы |
| `postgres` | 120s | — | полные |

---

## Ключевые SQL-функции

### process_candidates()

Geo-resolution: принимает массив ID кандидатов, scores, matched_texts и вычисляет
геометрию через приоритетную цепочку пространственных проверок:

1. **intersection** — ST_Intersects между парами (score ≥ 0.80)
2. **area** — кластер пересечений, все точки в 1 км → ST_ConvexHull
3. **pseudo_intersection** — ST_DWithin 150м, нет истинного пересечения
4. **proximity** — ST_DWithin 500м, нет intersection/pseudo
5. **centroid** — ST_Centroid всех кандидатов
6. **single_match** — лучший по score

Дедубликация: по идентичной геометрии (SnapToGrid 0.0001° ≈ 10м) + по имени
(ближайший к центроиду кластера).

### clean_old_events()

pg_cron каждые 5 минут:
1. DROP целых партиций, попавших в TTL-окно (< cutoff_time)
2. DELETE из текущей (неполной) партиции
3. UPDATE events_meta (version++)
4. pg_notify('events_cleaned', ...)

---

## TTL событий

`events.event_time TIMESTAMPTZ`; pg_cron каждые 5 минут удаляет события старше
1 часа (`event_time < NOW() - INTERVAL '1 hour'`). Сравнение
по абсолютным моментам — не зависит от session timezone.

---

## Live-уведомления

- `parser` пишет событие → триггер шлёт `pg_notify('events_new', …)`.
- pg_cron при очистке шлёт `events_cleaned`.
- Слушает сервис `core` (`asyncpg add_listener`), мостит в WebSocket
  (см. [core.md](core.md)).
