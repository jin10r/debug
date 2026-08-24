# Rules — Core Service v2.1

**Сервис:** `core/` (aiohttp HTTP + WebSocket + aiogram Telegram bot)
**Точка входа:** `python main.py`
**Порт:** 8080 (internal network only)
**Docker:** Multi-stage build (libpq-dev builder → libpq5 runtime), non-root UID 1000

---

## 1. Архитектурные правила

### R-C1: Core — API gateway, НЕ обработчик

Core принимает HTTP/WebSocket запросы от фронтенда, валидирует авторизацию, раздаёт данные из PostgreSQL. Core НЕ выполняет NLP, НЕ парсит Telegram, НЕ вычисляет геометрию.

**Запрещено:**
- NLP-код (лемматизация, стемминг, токенизация, classify, find_geo)
- Прямые вызовы Telegram MTProto (парсинг каналов)
- Геометрические вычисления (PostGIS вызовы кроме SELECT)

### R-C2: Async архитектура — один event loop

Core работает в одном asyncio event loop. Все операции — async/await.

```python
# ✅ Правильно
async with self.db.pool.acquire() as conn:
    rows = await conn.fetch(query, *args)

# ❌ Неправильно
import requests
requests.get(url)  # блокирует event loop
```

### R-C3: Graceful shutdown — orderly teardown

При SIGTERM core:
1. Устанавливает `shutdown_event` (бот, WS, pg_notify, DB pool)
2. Закрывает все WebSocket-соединения (close_all, timeout 5s)
3. Останавливает bot polling (dp.stop_polling, timeout 3s)
4. Останавливает pg_notify listener (timeout 3s)
5. Закрывает bot session (timeout 3s)
6. Закрывает cache manager (timeout 3s)
7. Закрывает DB pool (timeout 5s)
8. Закрывает runner

**Правило:** `stop_grace_period` в docker-compose ≥ 30s.

### R-C4: Middleware chain — порядок неизменяемый

```python
app = web.Application(middlewares=[
    logging_middleware,       # 1. Логирование
    body_size_limit_middleware,  # 2. Лимит тела запроса
    jwt_auth_middleware,      # 3. JWT аутентификация
    rate_limiter.middleware   # 4. Rate limiting
])
```

**Правило:** Порядок middleware фиксирован. Добавление нового middleware — только в конец chain (перед rate_limiter) или с пересчётом приоритетов.

**CSRF:** не используется. Клиент авторизуется ТОЛЬКО через `Authorization: Bearer`
(JWT в sessionStorage); cookie `session_token` нигде не устанавливается, поэтому
stateless CSRF-проверка всегда проходила насквозь (мёртвый код). Bearer-токены
браузер не отправляет автоматически, CORS выключен (same-origin) — CSRF-вектор
отсутствует. Если когда-либо будет введена cookie-аутентификация — вернуть
`csrf_middleware` (модуль сохранён в истории git).

### R-C5: PostgreSQL LISTEN/NOTIFY + Catch-Up

Core слушает `events_new`. Так как `pg_notify` не гарантирует доставку при рестарте, Core при старте **ОБЯЗАН выполнить Catch-Up**: после подписки на `LISTEN` сделать `SELECT` событий за последние 5 минут и разослать их подключенным клиентам как `events_snapshot`.

```python
# После add_listener('events_new'):
try:
    async with db_pool.pool.acquire() as catchup_conn:
        recent_events = await catchup_conn.fetch(
            "SELECT id, event_time, description, layer, strategy, geom, photo_url, matches "
            "FROM events "
            "WHERE event_time > NOW() - INTERVAL '5 minutes' "
            "ORDER BY event_time ASC"
        )
    if recent_events and ws_manager:
        await ws_manager.send_snapshot(recent_events, channel='events_new')
        logger.info(f"Catch-Up: sent {len(recent_events)} events_snapshot to WS clients")
except Exception as e:
    logger.warning(f"Catch-Up failed: {e}")
```

### R-C6: Bot polling — handle_signals=False

```python
await dp.start_polling(bot, handle_signals=False)
```

**Правило:** aiogram НЕ ставит свои SIGTERM/SIGINT-хендлеры. Единый хендлер в `main.py` → `shutdown_event`.

### R-C7: Двухуровневая валидация

| Уровень | Механизм | Когда |
|---------|----------|-------|
| Telegram initData | HMAC-SHA256 по BOT_TOKEN | Первичная валидация (Mini App) |
| JWT access token | HS256, 15min TTL | Все API запросы |

**Правило:** `/api/validate-init` → выдаёт JWT. Все остальные `/api/*` требуют JWT.

### R-C8: JWT в ENV (Запрет эфемерной генерации)

`JWT_SECRET` **ОБЯЗАН** быть задан в переменных окружения. Эфемерная генерация (`secrets.token_urlsafe`) **КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНА**. Если `JWT_SECRET` отсутствует при старте, Core должен упасть с ошибкой (Fail Fast).

```python
def _resolve_jwt_secret(env: Env) -> str:
    secret = env.str("JWT_SECRET", None)
    if not secret:
        raise RuntimeError("FATAL: JWT_SECRET is required in environment (R-C8).")
    insecure_defaults = {
        "your-secret-key",
        "your-secret-key-change-in-production",
        "your-secret-key-change-in-production-min-32-chars",
        "secret",
        "changeme",
        "change-me",
    }
    if secret.lower() in insecure_defaults or secret.startswith("your-secret"):
        raise RuntimeError(
            "FATAL: JWT_SECRET is a placeholder — set a real secret (R-C8)."
        )
    if len(secret) < 32:
        raise RuntimeError(
            f"FATAL: JWT_SECRET must be >= 32 chars (got {len(secret)}) (R-C8)."
        )
    return secret
```

### R-C9: WebSocket auth — отдельный путь

`/ws` исключён из `jwt_auth_middleware`. Аутентификация через auth-сообщение:

```json
{"type": "auth", "token": "..."}     // JWT access token
{"type": "auth", "init_data": "..."} // Telegram initData
```

**Правило:** До `auth_ok` сервер НЕ отправляет данные клиенту.

### R-C10: Dev-bypass — ТОЛЬКО для разработки

```python
if not settings.app.telegram_webview_validation:
    return True  # dev mode
```

**Строгий парсинг (Secure by Default):** `TELEGRAM_WEBVIEW_VALIDATION` парсится в
`core/settings.py` функцией `_parse_strict_bool` (внутри `load_settings`, после
`env.read_env()`): default `True`; `False` — ТОЛЬКО при явном `'false'`/`'0'`
(регистронезависимо); отсутствие/пустое/любое другое значение — `True`.

**Правило:** При выключенной валидации core логирует WARNING (в `load_settings` —
`SECURITY RISK`, при старте — предупреждение dev-режима). Никогда не деплоить с
`TELEGRAM_WEBVIEW_VALIDATION=false` в production.

### R-C11: Per-feature protocol

WebSocket протокол работает с отдельными GeoJSON Features:

```
Server → Client: {"type": "feature", "data": <GeoJSON Feature>, "timestamp": "..."}
Server → Client: {"type": "events_snapshot_end", "count": N, "timestamp": "..."}
Client → Server: {"type": "auth", "token": "..."}
Client → Server: {"type": "get_events", "since_timestamp": "...", "since_id": <int>}
Client → Server: {"type": "ping"}
Server → Client: {"type": "pong", "timestamp": "..."}
```

**Правило:** `events_snapshot_end` — терминатор батча. Клиент молча обрабатывает батч; уведомления ТОЛЬКО для live push после snapshot_end.

**Catch-up watermark (R-C11a):** клиент шлёт `since_id` = max event id в своём store. `id` (SERIAL) монотонен по моменту ВСТАВКИ, а `event_time` у backfill-исторических сообщений лежит в прошлом — catch-up по времени такие события теряет навсегда. При наличии `since_id` сервер игнорирует `since_timestamp` и отдаёт события с `id > since_id` в окне 60 минут.

### R-C12: Connection limit

```python
MAX_CONNECTIONS = 1000
```

**Правило:** При превышении лимита — новый клиент получает `1013 Try Again Later`.

### R-C13: Broadcast timeout

```python
SEND_TIMEOUT = 5.0  # секунд на отправку одному клиенту
```

**Правило:** Зависший клиент не блокирует рассылку остальным. `asyncio.gather` с `return_exceptions=True`.

### R-C14: Snapshot on connect

При `get_events` (или `since_id=None`) core отправляет все события за 60 минут:

```python
events_data = await self.db_request.get_filtered_events_as_geojson(
    time_interval_minutes=60,
    since_timestamp=None if since_id is not None else since_timestamp,
    after_id=since_id
)
```

**Правило:** Snapshot отправляется по одному feature за сообщение (не массивом) для совместимости с медленными WebView. Catch-up по `after_id` предпочтителен: доставляет backfill-события, которые catch-up по `event_time` пропускает.

### R-C15: asyncpg pool — min/max sizing

```python
pool = await asyncpg.create_pool(
    min_size=2,
    max_size=10,
    command_timeout=30,
)
```

**Правило:** `max_size ≤ max_connections / количество_сервисов` (см. R-DB15).

### R-C16: Request object — DB access layer

Все запросы к БД идут через `Request` объект (`core/db/dbconnect.py`). Прямые SQL-запросы вне Request — запрещены.

### R-C17: Parameterized queries

Все SQL-запросы используют параметризацию ($1, $2, ...). Конкатенация строк ЗАПРЕЩЕНА.

### R-C18: CacheManager — in-memory LRU

```python
cache = CacheManager()  # OrderedDict LRU + TTL, async lock
```

**Правило:** Кэш — in-memory, без Redis.

### R-C19: Docker Security

```yaml
# docker-compose.yml
core:
  user: "1000:1000"
  security_opt:
    - no-new-privileges:true
  cap_drop:
    - ALL
```

**Правило:** Core работает от non-root (UID 1000). `cap_drop: ALL` без `cap_add`.

---

## Антипаттерны (ЗАПРЕЩЕНО)

| Антипаттерн | Почему | Правило |
|-------------|--------|---------|
| NLP-код в core | Нарушение разделения | R-C1 |
| Синхронные вызовы в hot path | Блокирует event loop | R-C2 |
| SQL-конкатенация | SQL injection | R-C17 |
| `/ws` без auth | Открытый доступ | R-C9 |
| Dev-bypass в production | Security | R-C10 |
| handle_signals=True в aiogram | Двойной SIGTERM handler | R-C6 |
| Эфемерный JWT_SECRET | Массовый logout | R-C8 |
| Redis кэш (prematurely) | Лишняя зависимость | R-C18 |
| Прямые SQL вне Request | Нарушение абстракции | R-C16 |

---

*Правила основаны на анализе кодовой базы core/ — август 2026 (обновлено: Docker security, CSRF removed)*
