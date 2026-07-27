# Архитектура микросервисов Survival Map

## 1. Общая архитектура

Система состоит из 5 микросервисов, объединённых тремя изолированными Docker-сетями
(`frontend`, `backend`, `db`). База данных (`db`) изолирована от внешнего мира
(`internal: true`).

```
                    Telegram (MTProto)
                           │
                           ▼
  ┌───────────────────────────────────────────────────┐
  │                   parser                          │
  │  Kurigram (MTProto клиент)                        │
  │  Чтение канала, предобработка текста,             │
  │  загрузка фото, запись в pending_events           │
  └─────────────────────┬─────────────────────────────┘
                         │ INSERT pending_events
                         ▼
  ┌───────────────────────────────────────────────────┐
  │                  processor                         │
  │  NLP-пайплайн:                                     │
  │  pymorphy3 + rapidfuzz + sliding-window матчер    │
  │  SemanticResolver (pre-filter) + LayerClassifier   │
  └─────────────────────┬─────────────────────────────┘
                         │ INSERT events (PostGIS)
                         ▼
  ┌───────────────────────────────────────────────────┐
  │               postgres (PostGIS)                   │
  │  PostgreSQL 15 + PostGIS 3.3 + pg_cron            │
  │  process_candidates() — PostGIS geo-resolution     │
  │  pg_notify → 'events_new'                          │
  └─────────────────────┬─────────────────────────────┘
                         │ LISTEN/NOTIFY
                         ▼
  ┌───────────────────────────────────────────────────┐
  │                  core                              │
  │  aiohttp (HTTP + WebSocket) + aiogram (TG bot)    │
  │  REST API + WS GeoJSON broadcast                  │
  │  JWT auth + HMAC initData + rate limiting         │
  └─────────────────────┬─────────────────────────────┘
                         │ WebSocket / HTTP
                         ▼
  ┌───────────────────────────────────────────────────┐
  │                  web (nginx)                       │
  │  Reverse proxy, rate limit, статика PWA           │
  └─────────────────────┬─────────────────────────────┘
                         │
                         ▼
               Браузер / Telegram WebView
```

## 2. Технологический стек контейнеров

### 2.1 postgres

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | `postgis/postgis:15-3.3` (PostgreSQL 15 + PostGIS 3.3) |
| **Доп. расширения** | `pg_cron` (очистка событий каждые 5 мин) |
| **Конфигурация** | `shared_buffers = 256MB`, `work_mem = 4MB`, `statement_timeout = 30s` |
| **Скрипты инициализации** | `01-extensions.sql`, `02-tables.sql`, `03-functions.sql`, `04-load-data.sql`, `05-role-timeouts.sql`, `06-notify-trigger.sql`, `07-indexes.sql`, `08-process-candidates.sql`, `09-event-geom-trigger.sql`, `10-pending-events.sql`, `11-partition-maintenance.sql`, `12-materialized-views.sql` |
| **Сети** | `db` (internal) |
| **Ресурсы** | лимит: 1 CPU / 1GB RAM |

**Основные таблицы:**
- `events` — события с геометрией, слоем, стратегией; TTL 60 мин (pg_cron)
- `pending_events` — очередь необработанных сообщений для processor
- `geo` — справочник гео-объектов (улицы, сёла, станции и т.д.)
- `stopwords` — стоп-слова матчера
- `events_meta` — метаданные синхронизации WebSocket

**Ключевые функции:**
- `process_candidates()` — geo-resolution для списка кандидатов (0 → random, 1 → full geom, 2+ → intersection/pseudo-intersection)
- Триггер `notify_event_inserted` → pg_notify('events_new')

### 2.2 parser

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | python:3.11.10-slim-bookworm |
| **Точка входа** | `python -m parser.monitoring` |
| **Telegram клиент** | `kurigram` (форк Pyrogram) — user session, MTProto |
| **БД** | `asyncpg` |
| **Сети** | `backend` + `db` |
| **Ресурсы** | лимит: 0.5 CPU / 256MB RAM |

**Pipeline:**
1. Kurigram получает новое сообщение из канала
2. `strip_tail` — удаление маркера "сообщить"/"подписаться"
3. `preprocess_light` — удаление HTML, нормализация UA→RU, удаление хештегов
4. Сохранение в `pending_events` + pg_notify для processor
5. Загрузка photo_file_id при наличии

### 2.3 processor

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | python:3.11.10-slim-bookworm |
| **Точка входа** | `python -m processor.main` |
| **NLP библиотеки** | `mawo-pymorphy3` (лемматизация), `snowballstemmer` (стемминг), `rapidfuzz` (нечёткий поиск) |
| **Асинхронный фреймворк** | `asyncio` + `asyncpg` |
| **Сети** | `backend` + `db` |
| **Ресурсы** | лимит: 1.5 CPU / 1GB RAM |

**Pipeline:**
1. Чтение из `pending_events` (SKIP LOCKED)
2. `word_tokenizer.tokenize` — токенизация, слияние "5я"→"5_я"
3. `morphology.lemmatize_tokens` — pymorphy3 (LRU 10k) + snowballstemmer
4. `layer_classifier.classify` — cops / bus / traffic / pig
5. `geo_matcher.find_geo` — sliding-window 1-3 токенов, 3 тира:
   - T1: surface fuzzy (rapidfuzz, порог 0.85)
   - T2: lemma exact (O(1) dict lookup)
   - T3: lemma fuzzy (rapidfuzz, порог 0.82)
6. `SemanticResolver` (pre-filter) — определение стратегии по контексту
7. INSERT в events через `process_candidates()` PostGIS

### 2.4 core

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | python:3.11.10-slim-bookworm |
| **Точка входа** | `python main.py` |
| **HTTP сервер** | aiohttp (TCPSite) |
| **Telegram бот** | aiogram (polling) |
| **БД** | asyncpg (pool max=30) |
| **JWT auth** | HS256, автогенерация secret, access 15min / refresh 24h |
| **WebSocket** | pg_notify('events_new') → GeoJSON broadcast |
| **Сети** | `frontend` + `backend` + `db` |
| **Ресурсы** | лимит: 1 CPU / 768MB RAM |

**Эндпоинты:**
- `GET /health*` — мониторинг
- `POST /api/validate-init`, `POST /api/auth/refresh` — аутентификация
- `GET /api/config`, `GET /api/validation-config` — конфиг фронтенда
- `GET /api/events`, `POST /api/events` — события
- `GET /api/geo` — справочник гео-объектов
- `WS /ws` — WebSocket (live-события)
- `GET /metrics` — Prometheus

**Middleware chain:**
1. Logging
2. Metrics (Prometheus)
3. CSRF
4. JWT Auth
5. Rate Limiter

### 2.5 web

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | nginx:1.27-alpine (multi-stage: node:20-alpine builder) |
| **Карта** | Leaflet + MapLibre GL (vectortile basemap) |
| **State** | Zustand store |
| **Сборка** | webpack 5 + TypeScript |
| **Порт** | 80 |
| **Сети** | `frontend` |
| **Ресурсы** | лимит: 0.5 CPU / 128MB RAM |

## 3. Поток данных

### Полный цикл обработки сообщения

```
Шаг 1: Парсинг Telegram канала
  parser (monitoring.py) через kurigram получает новое сообщение.
  Применяет: strip_tail → preprocess_light. Сохраняет текст + photo_file_id
  в pending_events.

Шаг 2: NLP-пайплайн (processor)
  Processor читает pending_events (SKIP LOCKED):
  - tokenize → lemmatize (pymorphy3)
  - classify layer (cops/bus/traffic/pig)
  - find_geo (sliding-window матчер, 3 тира)
  - SemanticResolver (pre-filter: стратегия по контексту)
  - INSERT INTO events через process_candidates() PostGIS

Шаг 3: PostGIS geo-resolution
  process_candidates() вычисляет геометрию:
  - 0 matches → random
  - 1 match → полная геометрия
  - 2+ → ST_Intersection / псевдопересечение / convex hull

Шаг 4: WebSocket уведомление
  Триггер notify_event_inserted() → pg_notify('events_new', GeoJSON)
  core слушает pg_notify → broadcast всем WebSocket-клиентам

Шаг 5: Отображение на карте
  Фронтенд (Leaflet PWA) получает событие, добавляет маркер на карту
  с анимацией, отображает описание, слой (цвет маркера), фото.

Шаг 6: TTL очистка
  pg_cron каждые 5 минут удаляет события старше 60 минут.
  Триггер очистки → pg_notify('events_cleaned') → core уведомляет WS.
```

## 4. Сети

| Сеть | Тип | Участники | Доступ наружу |
|------|-----|-----------|---------------|
| `frontend` | bridge | core, web | web:80 |
| `backend` | bridge | parser, processor, core | нет |
| `db` | bridge, internal | parser, processor, core, postgres | нет |
