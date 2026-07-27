# Core microservice

Сервис `core` (контейнер из `Dockerfile.core`) — backend на `aiohttp`. Отдаёт
REST API и WebSocket фронтенду, валидирует Telegram-сессии (JWT), мостит
события из PostgreSQL в реальном времени: `LISTEN/NOTIFY` → broadcast по
WebSocket. Наружу не публикуется — проксируется сервисом `web` (nginx).

## Технологический стек

| Компонент | Назначение |
|-----------|-----------|
| `aiohttp` | HTTP-сервер, REST, WebSocket |
| `asyncpg` | PostgreSQL-пул + `LISTEN/NOTIFY` |
| `aiogram` | Telegram-бот (Mini App entry) |
| `pyjwt` (HS256) | access/refresh-токены сессии |
| `prometheus-client` | метрики `/metrics` |
| `pybreaker` | circuit breaker вокруг Telegram-валидации |

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
│   ├── events.py           # /api/events (snapshot + инкременты)
│   ├── websocket.py        # /ws — WebSocketManager (register/broadcast)
│   └── media.py            # отдача медиа событий
├── middlewares/
│   ├── auth.py             # JWT issue/verify
│   ├── jwt_auth.py         # проверка access-токена на защищённых маршрутах
│   ├── csrf.py             # CSRF-защита мутаций
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
    ├── logging_config.py   # structured logging
    ├── metrics.py          # prometheus-метрики
    └── telegram_validation.py  # HMAC-SHA256 валидация initData
```

## Middleware-цепочка

Порядок (`core/app_factory.py`):

1. `logging_middleware` — структурный лог запроса
2. `metrics_middleware` — prometheus-счётчики latency/статусов
3. `csrf_middleware` — CSRF-проверка мутирующих запросов
4. `jwt_auth_middleware` — валидация access-токена (защищённые маршруты)
5. `rate_limiter.middleware` — fixed-window лимит (60/мин по умолчанию)

## Маршруты

| Метод | Путь | Назначение |
|-------|------|-----------|
| GET | `/health`, `/health/live` | liveness |
| GET | `/health/ready` | readiness (актуальный probe БД) |
| GET | `/health/detailed` | метрики пула/кэша |
| GET/POST | `/api/validation-config` | конфиг валидации для фронта |
| POST | `/api/validate-init` | HMAC-проверка Telegram initData → JWT |
| POST | `/api/auth/refresh` | обновление access-токена |
| POST | `/api/config` | подтверждение сессии |
| GET | `/api/events` | snapshot событий |
| POST | `/api/events` | создание события (ручной ввод с карты) |
| GET | `/api/events/status`, `/api/data-status` | статус данных |
| GET | `/api/geo` | газеттир |
| GET | `/ws` | WebSocket (live-события) |
| GET | `/metrics` | prometheus |

## Поток live-событий

Выделенное соединение `asyncpg` слушает каналы `events_new` и `events_cleaned`.
На NOTIFY создаётся broadcast-задача, `WebSocketManager._broadcast_payload`
рассылает всем клиентам. Доставка best-effort: при реконнекте листенера
фронт делает полный fetch.

## Аутентификация

- **Telegram initData** — HMAC-SHA256 по спецификации Telegram
  (`core/utils/telegram_validation.py`). Обёрнуто в circuit breaker (pybreaker).
- **JWT** — HS256, access TTL 15 мин / refresh 24 ч. Секрет автогенерируется
  эфемерно в памяти при старте, если `JWT_SECRET` не задан в env.

## Конфигурация

`core/settings.py` — всё, кроме секретов, захардкожено в `@dataclass`. Из env
читаются только: `BOT_TOKEN`, `WEBAPP_URL`, `REDIRECT_URL`,
`TELEGRAM_VALIDATION_ENABLED`.
