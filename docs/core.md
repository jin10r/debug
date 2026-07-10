# Core microservice — логика и архитектура

> Общая архитектура: [docs/ARCHITECTURE.md](ARCHITECTURE.md)

Сервис `core` (контейнер из `Dockerfile.core`) — backend на `aiohttp`. Отдаёт
REST API и WebSocket фронтенду, валидирует Telegram-сессии (JWT), и мостит
события из PostgreSQL в реальном времени: `LISTEN/NOTIFY` → broadcast по
WebSocket. Наружу не публикуется — всё проксирует сервис `web` (nginx).

Код — каталог `core/`. Точка входа — `main.py` → `core/app_factory.py`.

---

## Технологический стек

| Компонент | Назначение |
|-----------|-----------|
| `aiohttp` 3.14 | HTTP-сервер, REST, WebSocket |
| `asyncpg` 0.31 | PostgreSQL-пул (min=5, max=30) + LISTEN/NOTIFY |
| `aiogram` 3.29 | Telegram-бот (Mini App entry) |
| `pyjwt` 2.13 (HS256) | access/refresh-токены сессии |
| `prometheus-client` 0.25 | метрики `/metrics` |
| `pybreaker` 1.4 | circuit breaker вокруг Telegram-валидации |
| `pydantic` 2.13 | валидация моделей запросов/ответов |

---

## Архитектура модулей

```
core/
├── app_factory.py          # сборка aiohttp app, middleware-цепочка,
│                           #   pg_notify→WebSocket listener, startup/shutdown
├── settings.py             # @dataclass-конфиг (env только для секретов)
├── models.py               # pydantic-модели запросов/ответов
├── api/
│   ├── routes.py           # регистрация всех маршрутов
│   ├── health.py           # /health, /health/ready, /health/detailed
│   ├── auth.py             # /api/validate-init, /api/auth/refresh
│   ├── config.py           # /api/config, /api/validation-config
│   ├── events.py           # /api/events (snapshot + инкременты), /status
│   ├── websocket.py        # /ws — WebSocketManager (register/broadcast)
│   └── media.py            # отдача медиа событий
├── middlewares/
│   ├── logging_config.py   # structured logging
│   ├── metrics.py          # prometheus metrics middleware + /metrics
│   ├── csrf.py             # CSRF-защита мутаций
│   ├── jwt_auth.py         # проверка access-токена на защищённых маршрутах
│   ├── auth.py             # JWT issue/verify
│   ├── ratelimit.py        # fixed-window rate limiter (per ip+path)
│   └── dbmiddleware.py     # инъекция db-адаптера в request
├── db/
│   ├── db_base.py          # asyncpg-пул
│   ├── dbconnect.py        # подключение/жизненный цикл
│   ├── db_events.py        # CRUD событий, снапшоты
│   ├── db_geo.py           # газеттир гео-объектов
│   └── db_spatial.py       # PostGIS-запросы
└── utils/
    ├── cache.py            # in-memory TTL+LRU кэш событий
    ├── telegram_validation.py  # HMAC-SHA256 валидация initData
    └── metrics.py          # prometheus-метрики
```

---

## Middleware-цепочка

Порядок (`core/app_factory.py`), запрос проходит сверху вниз:

1. `logging_middleware` — структурный лог запроса
2. `metrics_middleware` — prometheus-счётчики latency/статусов
3. `csrf_middleware` — CSRF-проверка мутирующих запросов
4. `jwt_auth_middleware` — валидация access-токена (защищённые маршруты)
5. `rate_limiter.middleware` — fixed-window лимит (60/мин по умолчанию,
   per-endpoint override для `/api/events`, `/api/geo`; health исключён)

---

## Маршруты

| Метод | Путь | Назначение |
|-------|------|-----------|
| GET | `/health`, `/health/live` | liveness |
| GET | `/health/ready` | readiness (актуальный probe БД/bot) |
| GET | `/health/detailed` | метрики пула/кэша |
| GET/POST | `/api/validation-config` | конфиг валидации для фронта |
| POST | `/api/validate-init` | HMAC-проверка Telegram initData → JWT |
| POST | `/api/auth/refresh` | обновление access-токена |
| POST | `/api/config` | подтверждение сессии |
| GET | `/api/events` | snapshot событий |
| POST | `/api/events` | создание события (ручной ввод с карты) |
| GET | `/api/events/snapshot` | полный снапшот |
| GET | `/api/events/status`, `/api/data_status` | статус данных |
| GET | `/api/geo`, `/api/geo/all` | газеттир |
| GET | `/api/data-status` | статус данных |
| WS | `/ws` | WebSocket (live-события) |
| GET | `/metrics` | prometheus |
| GET | `/media/{filename}` | раздача фото |

---

## Поток live-событий (LISTEN/NOTIFY → WebSocket)

```mermaid
flowchart LR
    P[parser INSERT events] --> T[триггер pg_notify]
    T -->|events_new| L[_run_pg_notify_listener<br/>core/app_factory.py]
    C[pg_cron TTL cleanup] -->|events_cleaned| L
    L --> WM[WebSocketManager]
    WM -->|FeatureCollection| F[фронтенд-клиенты]
```

- Выделенное соединение `asyncpg` слушает каналы `events_new` и `events_cleaned`
  (`conn.add_listener`, `core/app_factory.py`).
- На NOTIFY создаётся broadcast-задача (хранится в set, чтобы не потеряться GC),
  `WebSocketManager._broadcast_payload` рассылает всем клиентам; мёртвые
  соединения снимаются с регистрации в процессе рассылки и в `finally`.
- Доставка best-effort: при реконнекте листенера событие может потеряться, но
  оно persist в БД, а фронт при (ре)коннекте делает полный fetch и догоняет.

---

## Аутентификация

- **Telegram initData** — HMAC-SHA256 по спецификации Telegram
  (`core/utils/telegram_validation.py`): `secret = HMAC("WebAppData", bot_token)`,
  затем сверка `hash` через `hmac.compare_digest` (constant-time). Проверка
  свежести `auth_date` (24 ч). Обёрнуто в circuit breaker (`pybreaker`).
- **JWT** — HS256, access TTL 15 мин / refresh 24 ч (`core/settings.py JWTConfig`).
  Секрет автогенерируется эфемерно в памяти при старте (`_resolve_jwt_secret`),
  если `JWT_SECRET` не задан в env; рестарт → новый секрет → ранее выданные токены
  инвалидируются. `JWT_SECRET` в env — опциональный override для стабильного/общего
  (multi-replica) секрета (≥32 символов).
- **Состояние аутентификации** — stateless: JWT проверяется по подписи, кэш
  верификации — in-memory (`_jwt_token_cache` в `core/middlewares/auth.py`).
  Внешний session/nonce store не используется (core — один процесс).

---

## Конфигурация

`core/settings.py` — всё, кроме секретов, захардкожено в `@dataclass`. Из env
читаются только: `BOT_TOKEN`, `WEBAPP_URL`, `REDIRECT_URL`,
`TELEGRAM_VALIDATION_ENABLED` (`JWT_SECRET` — опциональный override автогенерации).
Параметры пула БД/матчера правятся прямо в `settings.py`.

### Ключевые настройки (core/settings.py)

| Класс | Поле | Default | Описание |
|-------|------|---------|----------|
| DatabaseConfig | pool_min_size | 5 | Минимум соединений в пуле |
| DatabaseConfig | pool_max_size | 30 | Максимум соединений в пуле |
| DatabaseConfig | command_timeout | 60 | Таймаут SQL-запроса |
| JWTConfig | access_token_ttl | 900 | 15 минут |
| JWTConfig | refresh_token_ttl | 86400 | 24 часа |
| SimilarityConfig | entity_similarity_threshold | 0.82 | Порог tier-3 lemma fuzzy |
| SimilarityConfig | phonetic_match_threshold | 0.85 | Порог tier-1 surface fuzzy |
| SimilarityConfig | max_entities | 5 | Top-K результатов |
| SimilarityConfig | max_sliding_window | 3 | Макс. размер окна (токенов) |
| SimilarityConfig | max_text_length | 380 | Длиннее → random |
| SimilarityConfig | semantic_enabled | True | ONNX BERT type validator |
| SimilarityConfig | semantic_model | qwen2.5:0.5b | Ollama модель (опционально) |
| ParserConfig | history_limit | 70 | Сообщений из истории при старте |
| ParserConfig | message_queue_maxsize | 100 | Размер asyncio.Queue |
| ParserConfig | worker_concurrency | 5 | Число воркеров очереди |

---

## Health / observability

- `/health/ready` — без кэша (LB не пошлёт трафик на падающую БД), проверяет
  PostgreSQL (обязателен), bot.
- `/health` (liveness) — TTL-кэш probe БД 5 с.
- `/metrics` — prometheus (`set_application_info(version='2.0.0')`).


