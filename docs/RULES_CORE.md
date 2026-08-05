# Rules — Core Service

**Сервис:** `core/` (aiohttp HTTP + WebSocket + aiogram Telegram bot)
**Точка входа:** `python main.py`
**Порт:** 8080 (internal network only)

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
1. Устанавливает `shutdown_event` (bot polling + pg_notify listener выходят)
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
    metrics_middleware,       # 2. Prometheus метрики
    csrf_middleware,          # 3. CSRF защита
    jwt_auth_middleware,      # 4. JWT аутентификация
    rate_limiter.middleware   # 5. Rate limiting
])
```

**Правило:** Порядок middleware фиксирован. Добавление нового middleware — только в конец chain (перед rate_limiter) или с пересчётом приоритетов.

### R-C5: PostgreSQL LISTEN/NOTIFY как event bridge

Core слушает каналы `events_new` и `events_cleaned` через dedicated connection из пула. При shutdown — UNLISTEN + release с timeout.

```python
conn = await db_pool.pool.acquire()
await conn.add_listener('events_new', _on_notify)
await conn.add_listener('events_cleaned', _on_notify)
```

**Правило:** pg_notify listener работает на отдельном соединении, НЕ на основном пуле. При потере соединения — автоматический reconnect не требуется (shutdown + restart).

### R-C6: Bot polling — handle_signals=False

```python
await dp.start_polling(bot, handle_signals=False)
```

**Правило:** aiogram НЕ ставит свои SIGTERM/SIGINT-хендлеры. Единый хендлер в `main.py` → `shutdown_event`.

---

## 2. Правила аутентификации

### R-C7: Двухуровневая валидация

| Уровень | Механизм | Когда |
|---------|----------|-------|
| Telegram initData | HMAC-SHA256 по BOT_TOKEN | Первичная валидация (Mini App) |
| JWT access token | HS256, 15min TTL | Все API запросы |

**Правило:** `/api/validate-init` → выдаёт JWT. Все остальные `/api/*` требуют JWT.

### R-C8: JWT в memory-only

JWT secret генерируется эфемерно при старте, если не задан в env. Токены валидны только пока жив процесс core.

```python
# core/settings.py
generated = secrets.token_urlsafe(48)  # ephemeral secret
```

**Правило:** При рестарте core все ранее выданные JWT инвалидируются. Для стабильности — задать `JWT_SECRET` в env (≥32 символов).

### R-C9: WebSocket auth — отдельный путь

`/ws` исключён из `jwt_auth_middleware`. Аутентификация через auth-сообщение:

```json
{"type": "auth", "token": "..."}     // JWT access token
{"type": "auth", "init_data": "..."} // Telegram initData
```

**Правило:** До `auth_ok` сервер НЕ отправляет данные клиенту. При `TELEGRAM_VALIDATION_ENABLED=False` — dev-bypass (любой клиент).

### R-C10: Dev-bypass — ТОЛЬКО для разработки

```python
if not settings.app.telegram_validation_enabled:
    return True  # dev mode
```

**Правило:** При выключенной валидации core логирует WARNING. Никогда не деплоить с `TELEGRAM_VALIDATION_ENABLED=False` в production.

---

## 3. Правила WebSocket

### R-C11: Per-feature protocol

WebSocket протокол работает с отдельными GeoJSON Features:

```
Server → Client: {"type": "feature", "data": <GeoJSON Feature>, "timestamp": "..."}
Server → Client: {"type": "events_snapshot_end", "count": N, "timestamp": "..."}
Client → Server: {"type": "auth", "token": "..."}
Client → Server: {"type": "get_events", "since_timestamp": "..."}
Client → Server: {"type": "ping"}
Server → Client: {"type": "pong", "timestamp": "..."}
```

**Правило:** `events_snapshot_end` — терминатор батча. Клиент молча обрабатывает батч; уведомления ТОЛЬКО для live push после snapshot_end.

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

При `get_events` (или `since_timestamp=None`) core отправляет все события за 60 минут:

```python
events_data = await self.db_request.get_filtered_events_as_geojson(
    time_interval_minutes=60,
    since_timestamp=since_timestamp
)
```

**Правило:** Snapshot отправляется по одному feature за сообщение (не массивом) для совместимости с медленными WebView.

---

## 4. Правила работы с БД

### R-C15: asyncpg pool — min/max sizing

```python
# core/db/dbconnect.py — Database class
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

```python
# ✅ Правильно
rows = await conn.fetch("SELECT * FROM events WHERE layer = $1", layer)

# ❌ SQL injection
rows = await conn.fetch(f"SELECT * FROM events WHERE layer = '{layer}'")
```

### R-C18: CacheManager — in-memory LRU

```python
cache = CacheManager()  # OrderedDict LRU + TTL, async lock
```

**Правило:** Кэш — in-memory, без Redis. При масштабировании core на несколько реплик — кэш не синхронизируется (пока неактуально).

---

## 5. Правила Telegram бота

### R-C19: aiogram polling only

Core использует aiogram ТОЛЬКО для bot commands (start, help). Парсинг каналов — в parser.

**Запрещено:**
- `client.get_chat_history()` в core
- Мониторинг каналов через bot
- Отправка сообщений в канал из core

### R-C20: Bot session proxy

Бот может использовать SOCKS5 proxy для Telegram API:

```python
_proxy_url = f"{settings.parser.proxy_scheme}://{_proxy_host}:{_proxy_port}"
_bot_session = AiohttpSession(proxy=_proxy_url)
```

**Правило:** Proxy настраивается через settings (не env для sensitive данных).

---

## 6. Правила Rate Limiting

### R-C21: Two-level rate limiting

| Уровень | Реализация | Лимит |
|---------|-----------|-------|
| Edge (nginx) | `limit_req_zone` | 10r/s api, 1r/s auth |
| App (core) | `RateLimiter` middleware | 60 req/min default |

**Правило:** Двухуровневый rate limiting обязателен. App-level дополняет edge-level.

### R-C22: Per-endpoint overrides

```python
rate_limiter = RateLimiter(
    default_limit=60,
    window_seconds=60,
    cleanup_interval=300
)
```

**Правило:** `/api/validate-init` — строже (nginx burst=5). `/api/auth/refresh` — тоже строже.

---

## 7. Правила безопасности

### R-C23: CSRF middleware

```python
csrf_middleware  # HMAC токен, 1h TTL
```

**Правило:** POST-запросы к `/api/*` требуют CSRF-токен.

### R-C24: No sensitive data in logs

BOT_TOKEN, JWT_SECRET, POSTGRES_PASSWORD НЕ логируются.

### R-C25: Security headers через nginx

Core НЕ ставит security headers — это делает nginx (R-W18, R-W19).

---

## 8. Правила мониторинга

### R-C26: Healthcheck endpoints

| Эндпоинт | Назначение | Проверяет |
|----------|-----------|-----------|
| `/health/live` | Liveness | Всегда 200 |
| `/health/ready` | Readiness | DB connected |
| `/health/detailed` | Details | DB + uptime + connections |

**Правило:** `/health/ready` используется docker-compose healthcheck.

### R-C27: Prometheus metrics

```python
setup_metrics_routes(app)  # /metrics
```

**Правило:** Метрики экспортируются, но нет сборщика (Prometheus/Grafana — рекомендация).

---

## 9. Правила конфигурации

### R-C28: Settings из core/settings.py

Core переиспользует единый `core/settings.py` для всех настроек. Все сервисы (parser, processor, core) используют один и тот же Settings.

### R-C29: Environment variables — только sensitive

| Переменная | Обязательна | Описание |
|-----------|-------------|----------|
| `BOT_TOKEN` | да | Токен бота |
| `POSTGRES_PASSWORD` | да | Пароль PostgreSQL |
| `WEBAPP_URL` | нет | URL Mini App |
| `REDIRECT_URL` | нет | Куда редиректить |
| `TELEGRAM_VALIDATION_ENABLED` | нет | Dev-bypass (default: True) |

**Правило:** Всё остальное — хардкод в `settings.py`.

---

## 10. Антипаттерны (ЗАПРЕЩЕНО)

| Антипаттерн | Почему | Правило |
|-------------|--------|---------|
| NLP-код в core | Нарушение разделения | R-C1 |
| Синхронные вызовы в hot path | Блокирует event loop | R-C2 |
| SQL-конкатенация | SQL injection | R-C17 |
| `/ws` без auth | Открытый доступ | R-C9 |
| Dev-bypass в production | Security | R-C10 |
| handle_signals=True в aiogram | Двойной SIGTERM handler | R-C6 |
| Redis кэш ( prematurely ) | Лишняя зависимость | R-C18 |
| Прямые SQL вне Request | Нарушение абстракции | R-C16 |

---

*Правила основаны на анализе кодовой базы core/ — июль 2026*
