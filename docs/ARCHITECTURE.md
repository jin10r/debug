# Архитектура Survival Map

Survival Map — Telegram Mini App для картирования событий Одессы в реальном времени.
Парсер читает Telegram-канал, извлекает геолокации из сообщений с помощью NLP-пайплайна,
записывает в PostGIS, и через `LISTEN/NOTIFY` доставляет события на фронтенд (Leaflet PWA)
за <100 мс.

**Стек:** PostgreSQL 15 + PostGIS 3.3 · Python 3.11 (aiohttp + asyncio + kurigram) · TypeScript + Leaflet · nginx · Docker Compose

---

## 1. Общая архитектура

```
                 Telegram (MTProto)
                        │
                        ▼
┌─────────────────────────────────────────────────┐
│                  parser                          │
│  kurigram (MTProto клиент) + NLP Pipeline        │
│  asyncio.Queue(maxsize=100) × 5 workers         │
│  CPU-only: pymorphy3 + rapidfuzz + ONNX BERT     │
└───────────────────┬─────────────────────────────┘
                    │ INSERT INTO events (CTE + process_candidates)
                    │ + pg_notify('events_new')
                    ▼
┌─────────────────────────────────────────────────┐
│              postgres (PostGIS)                   │
│  PostgreSQL 15 + PostGIS 3.3 + pg_cron           │
│  geo (1728 записей, 8 типов) + events (TTL 60м) │
│  process_candidates() — постGIS geo-resolution    │
│  LISTEN/NOTIFY → core                             │
└───────────────────┬─────────────────────────────┘
                    │ LISTEN('events_new', 'events_cleaned')
                    ▼
┌─────────────────────────────────────────────────┐
│                   core                            │
│  aiohttp (HTTP + WebSocket) + aiogram (TG bot)   │
│  REST API (15+ endpoints) + WS GeoJSON broadcast │
│  JWT auth + HMAC initData + rate limiting         │
└───────────────────┬─────────────────────────────┘
                    │ WebSocket / HTTP
                    ▼
┌─────────────────────────────────────────────────┐
│                   web (nginx)                     │
│  Reverse proxy (10r/s API, 1r/s auth)            │
│  Статика: Leaflet PWA Telegram Mini App           │
└───────────────────┬─────────────────────────────┘
                    │
                    ▼
           Браузер / Telegram WebView
```

### Сети Docker

| Сеть | Область | Доступ |
|------|---------|--------|
| `db` | postgres | **internal: true** — изолирована от внешнего мира |
| `backend` | parser, core | внутренняя связь парсер ↔ core |
| `frontend` | web, core | проксирование nginx → core |

Изоляция `db` — ключевой принцип безопасности: ни парсер, ни core не доступны напрямую снаружи; единственный вход — nginx:80.

---

## 2. Структура репозитория

```
survival_map/
├── core/                   # aiohttp backend (API, WS, JWT, middleware)
├── parser/                 # Telegram парсер (kurigram + NLP pipeline)
├── postgres/
│   ├── init-scripts/       # SQL: схема, функции, триггеры, данные
│   ├── config/             # postgresql.conf, pg_hba.conf
│   └── data/               # geo.csv (1728), stopwords.csv
├── web/                    # Leaflet PWA (TypeScript + webpack)
│   ├── js/                 # TypeScript source (core/, modules/, telegram/)
│   ├── assets/             # vendor libs (leaflet, maplibre-gl, markercluster)
│   └── css/
├── scripts/                # Утилиты экспорта, анализа, миграции
├── tests/                  # pytest (parser + core)
├── docs/                   # Архитектура по микросервисам
├── docker-compose.yml      # Оркестрация 4 сервисов
├── Dockerfile.core         # Multi-stage: python:3.11 → runtime
├── Dockerfile.parser       # Multi-stage: python:3.11 → runtime
├── Dockerfile.postgres     # postgis/postgis:15-3.3 + pg_cron
├── Dockerfile.web          # node:20 builder → nginx:1.27-alpine
├── nginx.conf              # Reverse proxy + rate limiting + CSP
├── main.py                 # Точка входа core
└── gen_session.py          # Генерация Telegram session (один раз)
```

---

## 3. Поток данных: Telegram → Карта

### 3.1 Полный цикл (с латентностями)

```
  Шаг 1: Получение сообщения (~5-50ms)
  ────────────────────────────────────
  kurigram (MTProto) получает сообщение из Telegram-канала.
  Фильтр по CHANNEL_ID в handler. Помещается в asyncio.Queue.

  Шаг 2: Предобработка текста (~5-10ms)
  ──────────────────────────────────────
  strip_tail()        — удаление "сообщить/подписаться"
  preprocess_light()  — HTML, UA→RU нормализация, время
  word_tokenizer()    — regex-токенизация, слияние "5я"

  Шаг 3: Морфология (~30-80ms)
  ─────────────────────────────
  pymorphy3 (DAWG, LRU 10k) → леммы
  snowballstemmer → стеммы (для fuzzy-индекса)

  Шаг 4: Классификация слоя (~0.1ms)
  ───────────────────────────────────
  LayerClassifier: cops / bus / traffic / pig
  по ключевым словам (layer_keywords в БД + fallback в коде).

  Шаг 5: Поиск гео-объектов (~30-120ms)
  ──────────────────────────────────────
  GeoMatcher: скользящее окно 1-3 токенов, 3 тира:
    T1: surface fuzzy (rapidfuzz, порог 0.85)
    T2: lemma exact (O(1) dict lookup)
    T3: lemma fuzzy (rapidfuzz, порог 0.82)
  → до 5 кандидатов с geo_id, score, matched_text.

  Шаг 6: TypeValidator + SemanticResolver (~5-20ms)
  ──────────────────────────────────────────────────
  ONNX BERT (rubert-tiny2, ~15MB) — zero-shot определение типа
  по контексту ±5 токенов. Heuristic fallback без модели.
  SemanticResolver: pre-filter правила → Ollama (опционально).

  Шаг 7: PostGIS geo-resolution (~5-50ms)
  ───────────────────────────────────────
  process_candidates() — один SQL CTE:
    0 кандидатов → random (точка в overlay-зоне)
    1 кандидат → single_match (полная геометрия)
    2+ кандидатов → приоритетная цепочка:
      1) intersection (ST_Intersects)
      2) area (cluster пересечений, все в 1 км → ConvexHull)
      3) pseudo_intersection (ST_DWithin 150м)
      4) proximity (ST_DWithin 500м)
      5) centroid
      6) single_match (лучший по score)
  ON CONFLICT (message_id) DO NOTHING — идемпотентность.

  Шаг 8: Уведомление (~5-20ms)
  ─────────────────────────────
  Триггер → pg_notify('events_new', GeoJSON Feature).
  core LISTEN → WebSocketManager → broadcast всем клиентам.
  Фронтенд: addEvent → marker на карту + анимация.
```

**Итого:** ~80-320 мс на сообщение (CPU-only, без GPU).

### 3.2 Альтернативные потоки

| Поток | Описание |
|-------|----------|
| **Ручное событие** | Карта → клик → POST /api/events → INSERT → pg_notify → WS |
| **TTL очистка** | pg_cron каждые 5 мин → DELETE events > 1ч → pg_notify('events_cleaned') |
| **Обновление газеттира** | INSERT/UPDATE в geo → trigger → pg_notify('geo_updated') → parser |

---

## 4. Микросервисы: детали

### 4.1 postgres — [docs/postgres.md](postgres.md)

| Компонент | Значение |
|-----------|----------|
| Базовый образ | `postgis/postgis:15-3.3` |
| Расширения | pg_cron (TTL), pg_stat_statements (мониторинг) |
| Конфигурация | shared_buffers=384MB, effective_cache_size=768MB, work_mem=8MB |
| Таймауты | statement_timeout: parser 60s, core 30s, maintenance 300s |
| Партиционирование | events по дням (PARTITION BY RANGE event_time) |
| Ресурсы | 1 CPU / 1GB RAM |

**Таблицы:**

| Таблица | Назначение | Ключевые индексы |
|---------|-----------|------------------|
| `geo` | Газеттир (1728 записей, 8 типов): names TEXT[], geom GEOMETRY, type | GIN(names), GiST(geom) |
| `events` | События с TTL 60 мин (партиционирована по дням) | time DESC, GiST(geom), layer, message_id UNIQUE |
| `stopwords` | Стоп-слова матчера | PK(word) |
| `layer_keywords` | Ключевые слова классификации слоёв | PK(layer) |
| `events_meta` | Метаданные для WS-синхронизации (version, max_event_id) | version++ на INSERT/DELETE |
| `geo_type_descriptions` | Описания типов для zero-shot BERT | PK(type) |
| `geo_role_patterns` | Роли geo-объектов (source/destination/via/landmark) | PK(role) |
| `strategy_type_filters` | Разрешённые типы для каждой стратегии | PK(strategy) |
| `layer_geo_types` | Релевантные типы для каждого слоя | PK(layer) |

**Ключевые SQL-функции:**

- `process_candidates()` — geo-resolution: 0→random, 1→single_match, 2+→приоритетная цепочка
- `clean_old_events()` — pg_cron: DROP целых партиций + DELETE текущей, pg_notify

**Init-скрипты (13 файлов, по порядку):**

01-extensions → 02-tables → 03-functions → 04-load-data → 05-role-timeouts → 06-notify-trigger → 07-indexes → 08-process-candidates → 09-event-geom-trigger → 10-type-config → 11-partition-maintenance → 12-materialized-views → 14-training-examples

> Init-скрипты исполняются только при **пустом** томе. Правка geo.csv требует `docker compose down -v` или ручного INSERT.

### 4.2 parser — [docs/parser.md](parser.md)

| Компонент | Значение |
|-----------|----------|
| Базовый образ | `python:3.11.10-slim-bookworm` (multi-stage) |
| Точка входа | `python -m parser.monitoring` |
| Telegram клиент | kurigram (форк Pyrogram), user session |
| NLP | pymorphy3 (DAWG), snowballstemmer, rapidfuzz |
| ONNX | rubert-tiny2 (~15MB), mean-pooling + cosine similarity |
| Очередь | asyncio.Queue(maxsize=100), 5 workers |
| Drain | stop_grace_period=60s, drain_timeout=20s |
| Ресурсы | 1 CPU / 768MB RAM |

**Модули:**

```
parser/
├── monitoring.py          # kurigram client + Queue + workers + heartbeat
├── message_processor.py   # Оркестратор: pipeline → SQL
├── text_preprocessor.py   # strip_tail + preprocess_light
├── word_tokenizer.py      # regex-токенизация, слияние
├── morphology.py          # pymorphy3 + Lemma + LRU
├── layer_classifier.py    # cops/bus/traffic/pig
├── phonetic_index.py      # Surface + lemma индексы при старте
├── geo_matcher.py         # Sliding-window: 3 тира matching
├── type_validator.py      # ONNX BERT zero-shot type probe
├── semantic_resolver.py   # Pre-filter → Ollama (опционально)
├── onnx_encoder.py        # rubert-tiny2 ONNX inference
├── collector.py           # Сбор метрик
└── db_adapter.py          # asyncpg pool
```

**Метрики качества (~99 событий):**

| Стратегия | % | Описание |
|-----------|---|----------|
| single_match | ~58 | Одно geo-существо |
| random | ~28 | Не распознано |
| intersection | ~9 | Пересечение 2+ улиц |
| polygon_intersection | ~5 | ConvexHull кластера |

### 4.3 core — [docs/core.md](core.md)

| Компонент | Значение |
|-----------|----------|
| Базовый образ | `python:3.11.10-slim-bookworm` (multi-stage) |
| Точка входа | `python main.py` |
| HTTP сервер | aiohttp (TCPSite, port 8080) |
| Telegram бот | aiogram (polling) |
| БД пул | asyncpg (min=5, max=30, timeout=60s) |
| JWT | HS256, access 15мин / refresh 24ч, ephemeral secret |
| Telegram auth | HMAC-SHA256 initData, circuit breaker (fail_max=5, reset=30s) |
| Rate limiting | Fixed-window, default 60/мин, nginx edge: 10r/s api, 1r/s auth |
| Кэш | In-memory OrderedDict LRU + TTL (без Redis) |
| Метрики | Prometheus /metrics |
| Ресурсы | 1 CPU / 768MB RAM |

**Модули:**

```
core/
├── app_factory.py      # Сборка app, middleware, pg_notify listener
├── settings.py         # @dataclass конфиг (env только для секретов)
├── models.py           # Pydantic модели
├── api/
│   ├── routes.py       # Регистрация маршрутов
│   ├── health.py       # /health, /health/ready, /health/detailed
│   ├── auth.py         # /api/validate-init, /api/auth/refresh
│   ├── config.py       # /api/config, /api/validation-config
│   ├── events.py       # /api/events (CRUD), /api/events/snapshot, /status
│   ├── websocket.py    # WebSocketManager (register/broadcast)
│   └── media.py        # /media/{filename}
├── middlewares/
│   ├── logging_config.py
│   ├── metrics.py      # Prometheus
│   ├── csrf.py         # CSRF HMAC token
│   ├── jwt_auth.py     # JWT verification
│   ├── auth.py         # JWT issue/verify
│   ├── ratelimit.py    # Fixed-window per ip+path
│   └── dbmiddleware.py # inject db adapter
├── db/
│   ├── db_base.py      # asyncpg pool
│   ├── dbconnect.py    # Lifecycle management
│   ├── db_events.py    # CRUD events, snapshots
│   ├── db_geo.py       # Gazetteer queries
│   └── db_spatial.py   # PostGIS queries
└── utils/
    ├── cache.py        # In-memory LRU + TTL
    ├── telegram_validation.py  # HMAC-SHA256 initData
    └── metrics.py      # Prometheus counters
```

**Middleware chain (порядок):**
1. Logging
2. Metrics (Prometheus)
3. CSRF
4. JWT Auth
5. Rate Limiter

**Эндпоинты (15+):**

| Метод | Путь | Назначение |
|-------|------|-----------|
| GET | `/health`, `/health/live`, `/health/ready`, `/health/detailed` | Мониторинг |
| GET/POST | `/api/validation-config` | Конфиг валидации |
| POST | `/api/validate-init` | Telegram initData → JWT |
| POST | `/api/auth/refresh` | Обновление JWT |
| GET/POST | `/api/config` | Конфигурация фронтенда |
| GET | `/api/events` | Snapshot событий |
| POST | `/api/events` | Создание события |
| GET | `/api/events/snapshot` | Полный снапшот |
| GET | `/api/events/status` | Метаданные очереди |
| GET | `/api/geo`, `/api/geo/all` | Газеттир |
| GET | `/api/data-status` | Статус данных |
| WS | `/ws` | WebSocket (live-события) |
| GET | `/metrics` | Prometheus |
| GET | `/media/{filename}` | Медиа |

### 4.4 web — [docs/web.md](web.md)

| Компонент | Значение |
|-----------|----------|
| Сервер | nginx:1.27-alpine |
| Фронтенд | Vanilla TypeScript + Leaflet + MapLibre GL (basemap) |
| State | zustand 5.x (reactive store) |
| Сборка | webpack 5 → dist/js/* |
| PWA | Service worker (sw.js), offline-first, localStorage cache |
| TS strict | **Включён** (strict: true, noImplicitAny, strictNullChecks) |
| Ресурсы | 0.5 CPU / 128MB RAM |

**Модули фронтенда:**

```
web/js/
├── common.ts           # window.serverNow, hapticFeedback, showNotification
├── core/
│   ├── store.ts        # Zustand store (eventsById, filters, TTL pruning)
│   ├── local_cache.ts  # localStorage persistence adapter
│   ├── websocket.ts    # /ws connection + reconnect + heartbeat
│   ├── event_manager.ts# store.subscribe → rAF scheduler
│   ├── map.ts          # Leaflet layer creation (Circle/Polyline/Polygon)
│   ├── ui.ts           # bootstrapUI, initializeMap, renderFromCache
│   ├── data.ts         # Data fetching
│   └── storage.ts      # Storage abstraction
├── modules/
│   ├── popups.ts       # Legend, click handlers
│   └── notifications.ts# New-event notifications
└── telegram/
    └── integration.ts  # tg.WebApp wrapper, theme, haptic
```

**8 архитектурных правил (web/CLAUDE.md):**
1. PWA microservice — online + offline
2. Validation gate — компоненты только после подтверждения бэкенда
3. Incremental local cache — store = source of truth
4. Full load on connect → live stream (boundary: `events_snapshot_end`)
5. Haptic feedback on every notification
6. Event TTL 60 минут (anchored к `serverNow()`)
7. Leaflet per-feature слои + инкрементный diff (add/remove/update)
8. Lightweight final image (multi-stage, node только в builder)

---

## 5. Безопасность

| Мера | Реализация |
|------|------------|
| **Изоляция сетей** | `db` internal: true |
| **Non-root контейнеры** | parser (uid 1000), core (appuser), web (nginx) |
| **Capabilities** | cap_drop: ALL; parser/core: none добавлено; web: NET_BIND_SERVICE |
| **tmpfs** | parser: /tmp (100M, noexec) |
| **Read-only fs** | core: readonly rootfs |
| **JWT** | HS256, эфемерный secret (или env override), access 15мин |
| **Telegram auth** | HMAC-SHA256 initData, constant-time compare, 24ч freshness |
| **Circuit breaker** | pybreaker (fail_max=5, reset=30s) вокруг Telegram validation |
| **CSRF** | HMAC token, 1ч TTL |
| **Rate limiting** | nginx edge: 10r/s api, 1r/s auth; app-level: 60/мин default |
| **set_real_ip_from** | nginx trusted proxy CIDR (10.0/8, 172.16/12, 192.168/16) |
| **CSP** | Strict CSP для map.html: script-src 'self' https://telegram.org |
| **Idempotent INSERT** | ON CONFLICT (message_id) DO NOTHING |
| **Параметризованный SQL** | Все запросы через asyncpg ($1, $2, ...) |
| **statement_timeout** | parser: 60s, core: 30s, maintenance: 300s |
| **Log rotation** | docker json-file: max 10MB × 5 файлов на сервис |

---

## 6. Инфраструктура

### Docker Compose

| Сервис | Образ | Порт | Depends_on | Сети |
|--------|-------|------|-----------|------|
| postgres | survival_postgres | 5432 (internal) | — | db |
| parser | survival_parser | — | postgres (healthy) | backend, db |
| core | survival_core | 8080 (internal) | postgres (healthy) | frontend, backend, db |
| web | survival_web | **80** (public) | core (healthy) | frontend |

> **Примечание:** модель service была удалена из docker-compose.yml. ONNX BERT type validator
> интегрирован в parser с lazy imports — при отсутствии onnxruntime работает graceful fallback
> на heuristic markers.

### Healthchecks

| Сервис | Метод | Интервал | Start period |
|--------|-------|----------|-------------|
| postgres | pg_isready + recovery check | 5s | 180s |
| parser | heartbeat file (/tmp/parser_heartbeat) | 30s | 60s |
| core | HTTP /health (urllib) | 30s | 40s |
| web | curl /health/ready | 15s | 30s |

### Graceful Shutdown

- parser: stop_grace_period=60s, drain queue timeout=20s
- core: stop_grace_period=30s, WS drain
- web: stop_grace_period=30s

### Dockerfile (multi-stage)

| Сервис | Builder | Runtime | Итого |
|--------|---------|---------|-------|
| core | python:3.11 + gcc + libpq-dev | python:3.11 + libpq5 | ~163MB |
| parser | python:3.11 + gcc | python:3.11 + procps | ~250MB |
| postgres | — | postgis/postgis:15-3.3 + pg_cron | ~400MB |
| web | node:20-alpine (build + typecheck) | nginx:1.27-alpine | ~30MB |

---

## 7. Оценка архитектуры

### Сильные стороны

| # | Плюс |
|---|------|
| 1 | **Изоляция сетей** — db internal: true, единственный вход nginx:80 |
| 2 | **Idempotent INSERT** — ON CONFLICT, безопасные ретраи |
| 3 | **Один roundtrip** — геометрия внутри CTE: INSERT + pg_notify в одном запросе |
| 4 | **Tiered matching** — 3 тира (стемминг → семантика → fuzzy) + ONNX BERT type validator |
| 5 | **WebSocket realtime** — pg_notify → WS, <100ms latency |
| 6 | **Graceful shutdown** — drain очереди, stop_grace_period |
| 7 | **Hardened контейнеры** — non-root, cap_drop ALL, tmpfs, readonly rootfs |
| 8 | **Healthchecks** — у всех сервисов |
| 9 | **Без Redis** — in-memory LRU, меньше движущихся частей |
| 10 | **pg_notify** — встроенный брокер, без Kafka/RabbitMQ |
| 11 | **PWA** — service worker, offline-first |
| 12 | **Rate limiting** — двухуровневый (nginx + app) |
| 13 | **Prometheus** — core экспортирует метрики |
| 14 | **TS strict** — фронтенд с полной типизацией |
| 15 | **ONNX BERT** — zero-shot type classification без обучения |

### Слабые стороны

| # | Минус | Степень |
|---|-------|---------|
| 1 | **Нет оркестрации** — docker-compose, нет автоскейлинга | Высоко |
| 2 | **Одиночный parser** — 1 process, GIL ограничивает CPU | Высоко |
| 3 | **Нет replica postgres** — single point of failure | Высоко |
| 4 | **pg_cron, не pg_partman** — DELETE вместо DROP партиций | Средне |
| 5 | **JWT secret в памяти** — сброс при рестарте | Средне |
| 6 | **JWT cache без хеша** — ключ = raw token | Средне |
| 7 | **No TLS** — nginx без HTTPS | Критично |
| 8 | **Нет CI/CD** — базовый .gitlab-ci.yml | Высоко |
| 9 | **Parser healthcheck** — heartbeat файл ненадёжен | Средне |
| 10 | **Нет observability** — нет distributed tracing | Высоко |
| 11 | **parser не распределён** — единая точка отказа | Высоко |

---

## 8. Рекомендации

### Краткосрочные (1-2 недели)

1. **TLS/SSL** — Cloudflare Tunnel или Let's Encrypt (критично для production)
2. **CI/CD pipeline** — ruff + typecheck + pytest + Docker build
3. **Rate-limit hardening** — set_real_ip_from уже есть, проверить edge
4. **pytest coverage** — street_matcher, process_candidates, telegram_validation

### Среднесрочные (3-4 недели)

5. **Prometheus + Grafana** — dashboard + alerts
6. **pg_dump cron** — ежедневные бэкапы
7. **JWT secret в Docker secret** — стабильность при рестартах
8. **Parser HTTP healthcheck** — вместо heartbeat файла

### Долгосрочные (5+ недель)

9. **Kubernetes migration** — Deployment + HPA + Ingress
10. **Postgres replica** — streaming replication + Patroni
11. **NATS JetStream** — замена pg_notify для масштабирования
12. **Distributed tracing** — OpenTelemetry

---

## 9. Стратегия миграции на Kubernetes

| Сервис | Компонент | Стратегия |
|--------|-----------|-----------|
| postgres | StatefulSet | Patroni + streaming replica, PVC для данных |
| parser | Deployment + HPA | Автоскейлинг по длине очереди (NATS JetStream) |
| core | Deployment + HPA | Автоскейлинг по WebSocket connections |
| web | Ingress + Cert-Manager | TLS termination, static content via CDN |
| metrics | Prometheus Operator + Grafana | Дашборды + алерты |
