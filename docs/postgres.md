# Postgres microservice

Сервис `postgres` (контейнер из `Dockerfile.postgres`) — PostgreSQL + PostGIS.
Хранит справочник гео-объектов, события с геометрией и очередь необработанных
сообщений. Во внутренней сети (`db`, `internal: true`).

## Схема

| Таблица | Назначение |
|---------|-----------|
| `geo` | Справочник: `names TEXT[]` (синонимы), `geom GEOMETRY(Geometry,4326)`, `type VARCHAR(20)` |
| `events` | События: `event_time TIMESTAMPTZ`, `layer`, `strategy`, `geom GEOMETRY(Geometry,4326)` |
| `pending_events` | Очередь: необработанные сообщения от parser, читается processor (SKIP LOCKED) |
| `stopwords` | Стоп-слова матчера |
| `layer_keywords` | Ключевые слова классификатора слоёв |
| `events_meta` | Метаданные: version, max_event_id для синхронизации WebSocket |

## Init-скрипты

Выполняются при инициализации пустого тома БД, по порядку:

| Скрипт | Назначение |
|--------|-----------|
| `01-extensions.sql` | PostGIS, pg_cron, pg_stat_statements |
| `02-tables.sql` | Схема таблиц + индексы |
| `03-functions.sql` | TTL-очистка событий (pg_cron каждые 5 мин) |
| `04-load-data.sql` | Загрузка `geo.csv`, `stopwords.csv` |
| `05-role-timeouts.sql` | statement_timeout по ролям |
| `06-notify-trigger.sql` | pg_notify для новых событий |
| `07-indexes.sql` | Дополнительные индексы |
| `08-process-candidates.sql` | Функция geo-resolution |
| `09-event-geom-trigger.sql` | Валидация geometry ↔ strategy |
| `10-pending-events.sql` | Таблица и триггеры pending_events |
| `11-partition-maintenance.sql` | Обслуживание партиций |
| `12-materialized-views.sql` | Materialized views |

## TTL событий

`pg_cron` каждые 5 минут удаляет события старше 1 часа
(`event_time < NOW() - INTERVAL '1 hour'`).

## Live-уведомления

- Триггер `notify_event_inserted` → `pg_notify('events_new', GeoJSON)`
- pg_cron при очистке → `events_cleaned`
- Слушает сервис `core` (asyncpg add_listener) → WebSocket broadcast

## Справочные данные

- `postgres/data/geo.csv` — гео-объекты (улицы, сёла, станции)
- `postgres/data/geo_additions.csv` — дополнения к справочнику
- `postgres/data/stopwords.csv` — стоп-слова
