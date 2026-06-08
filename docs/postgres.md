# Postgres microservice — хранилище (PostGIS)

Сервис `postgres` (контейнер из `Dockerfile.postgres`) — PostgreSQL + PostGIS.
Хранит газеттир улиц и геолоцированные события. Во внутренней сети
(`db`, `internal: true`) — наружу не публикуется. Креды захардкожены
(`postgres/postgres`), синхронизированы с `core/settings.py DatabaseConfig`.

## Схема (`postgres/init-scripts/02-tables.sql`)

| Таблица | Назначение |
|---------|-----------|
| `streets` | газеттир: `names TEXT[]` (синонимы), `geom GEOMETRY(Geometry,4326)`. Индексы: GIN по `names`, GiST по `geom` |
| `events` | события: `event_time TIMESTAMPTZ`, `layer`, `strategy` (CHECK на 4 значения), `geom`, уникальный `message_id`. Индексы по time/geom/layer |
| `stopwords` | стоп-слова матчера |
| `layer_keywords` | ключевые слова классификатора слоёв |
| `events_meta`, `table_updates` | служебные метаданные/таймстемпы обновлений |

`strategy ∈ {random, single_match, single_intersection, polygon_intersection}` —
ровно те значения, что выдаёт `process_candidates`.

## Init-скрипты (выполняются при инициализации тома БД, по порядку)

| Скрипт | Назначение |
|--------|-----------|
| `01-extensions.sql` | PostGIS, pg_cron, pg_stat_statements |
| `02-tables.sql` | схема таблиц + индексы |
| `03-functions.sql` | TTL-очистка событий (pg_cron каждые 5 мин) |
| `04-load-data.sql` | идемпотентная загрузка `streets.csv`, `stopwords.csv` |
| `06-notify-trigger.sql` | NOTIFY об изменении улиц (для парсера) |
| `08-process-candidates.sql` | геолокация: кандидаты → точка/пересечение/полигон |
| `09-event-geom-trigger.sql` | валидация geometry type ↔ strategy при INSERT/UPDATE |

> Init-скрипты исполняются только при **пустом** томе. После правки
> `streets.csv` нужно либо `docker compose down -v` (пересоздать том), либо
> вставить запись вручную через `psql` (см. README).

## TTL событий

`events.event_time TIMESTAMPTZ`; pg_cron каждые 5 минут удаляет события старше
1 часа (`03-functions.sql`, `event_time < NOW() - INTERVAL '1 hour'`). Сравнение
по абсолютным моментам — не зависит от session timezone.

## Live-уведомления

- `parser` пишет событие → триггер шлёт `pg_notify('events_new', …)`.
- pg_cron при очистке шлёт `events_cleaned`.
- Слушает сервис `core` (`asyncpg add_listener`), мостит в WebSocket
  (см. [core.md](core.md)).
