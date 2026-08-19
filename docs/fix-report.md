# Отчёт о реализации плана исправлений

Дата: 2026-08-19  
Задачи 1.1 и 1.2 (отзыв секретов, удаление session.session) выполняются вручную.  
Все остальные 15 задач реализованы в коде.

---

## Фаза 1 — Срочные баги (🔴)

### 1.3 — TypeError и AttributeError в `get_events_handler`

**Файл:** `core/api/events.py`

**Проблема 1:** `filters.time_filter` объявлен как `Optional[int]`. При запросе без этого поля сравнение `> 5` бросало `TypeError`.  
**Исправление:** `ttl = 60 if (filters.time_filter or 0) > 5 else 30`

**Проблема 2:** `filters.since` — Pydantic уже возвращает `datetime`-объект. Вызов `.replace('Z', '+00:00')` (метод строки) на нём бросал `AttributeError`.  
**Исправление:** убрана обработка строки, нормализация через `since_dt.replace(tzinfo=timezone.utc)` если `tzinfo is None`.

Добавлен импорт `timezone` из `datetime`.

---

### 1.4 — Race condition в `logging_middleware` (LogRecordFactory)

**Файл:** `core/utils/logging_config.py`

**Проблема:** `logging.setLogRecordFactory()` вызывался на каждый запрос и восстанавливался в `finally`. При конкурентных запросах фабрики перезаписывали друг друга — request_id в логах перемешивался.

**Исправление:**
- Фабрика `_request_id_record_factory` устанавливается **один раз при загрузке модуля** через `logging.setLogRecordFactory()`.
- `ContextVar` `_request_id_var` обеспечивает изоляцию request_id для каждого async-контекста без глобальных перезаписей.
- Из middleware полностью удалены `old_factory = logging.getLogRecordFactory()`, `logging.setLogRecordFactory(record_factory)` и восстановление в `finally`.
- Добавлен импорт `timezone` в `JSONFormatter`.

---

## Фаза 2 — Безопасность аутентификации (🟠)

### 2.1 — Refresh Token Rotation (single-use tokens)

**Новые файлы:**
- `postgres/init-scripts/17-refresh-tokens.sql` — таблица `refresh_tokens(jti UUID PK, user_id, issued_at, expires_at, used_at, revoked)` с индексами и функцией `cleanup_expired_refresh_tokens()`.
- `core/db/db_auth.py` — класс `AuthOperations` с методами:
  - `store_refresh_token(jti, user_id, expires_at)` — сохранить новый токен.
  - `consume_refresh_token(jti) → bool` — атомарный UPDATE; `False` = уже использован/отозван.
  - `revoke_all_user_tokens(user_id)` — инвалидация всех токенов при обнаружении кражи.

**Изменённые файлы:**

`core/db/dbconnect.py` — `Request` расширен `AuthOperations` и тремя делегирующими методами.

`core/middlewares/auth.py` — `generate_jwt_tokens` теперь добавляет `jti` (UUID) в refresh payload и возвращает кортеж `(access_token, refresh_token, jti)`.

`core/api/auth.py`:
- `validate_init_handler` — после выдачи токенов сохраняет `jti` в БД через `db.store_refresh_token()`.
- `refresh_token_handler` — полная реализация Rotation:
  1. Криптопроверка подписи.
  2. Атомарный `consume_refresh_token(jti)`.
  3. Если токен уже использован → `revoke_all_user_tokens()` + `401`.
  4. Выдаёт **новую пару** (access + refresh), сохраняет новый jti.
  5. Ответ теперь содержит и `refresh_token` (ранее отсутствовал).

---

### 2.2 — Dev-bypass guard через APP_ENV

**Файл:** `core/api/auth.py` (встроено в `validate_init_handler`)

При `TELEGRAM_VALIDATION_ENABLED=False` и `APP_ENV` не из набора `{development, dev, local, test}` — возвращает `403 Forbidden` и пишет ERROR в лог. Предотвращает случайный деплой в production без валидации.

**Файл:** `.env.example` — добавлена переменная `APP_ENV=production`.

---

### 2.3 — `validate_max_age_hours` — нижняя граница 1

**Файл:** `core/utils/validators.py`

`validate_int(max_age_hours, "max_age_hours", 0, 720)` → `validate_int(..., 1, 720)`.  
При `max_age_hours=0` любой init_data был бы немедленно истёкшим.

---

## Фаза 3 — Архитектура и надёжность (🟡)

### 3.1 — `channel_id` в env

**Файл:** `core/settings.py`

`BotConfig.channel_id` убран из хардкода в dataclass. Теперь обязательный параметр без дефолта, читается в `load_settings()` из `env.str("CHANNEL_ID", "-1002050105527")`.

**Файл:** `.env.example` — добавлена строка `CHANNEL_ID=-1002050105527`.

---

### 3.2 — Дефолтный пароль БД

**Файл:** `core/settings.py`

`DatabaseConfig.password` изменён с `"postgres"` на `""`. При отсутствии `POSTGRES_PASSWORD` в env подключение упадёт с ошибкой аутентификации вместо молчаливого использования слабого пароля.

---

### 3.3 — Удалён мёртвый `MessageProcessor`

- `parser/monitoring.py` — импорт `truncate_for_geo` перенесён с `parser.message_processor` на `core.utils.text_preprocessor` (где функция и определена).
- `parser/message_processor.py` — **удалён** (был полностью мёртвым кодом).

---

### 3.4 — Удалён мёртвый `DbMiddleware`

- `core/app_factory.py` — удалён импорт `DbMiddleware` и вызов `dp.update.middleware(DbMiddleware(db_request))`.
- `core/middlewares/dbmiddleware.py` — **удалён**. Ни один aiogram-хендлер не использовал DB из контекста апдейта.

---

### 3.5 — WebSocket auth race condition

**Файл:** `core/api/websocket.py`

**Проблема:** между `not auth_deadline_task.done()` и `auth_deadline_task.cancel()` таск мог уже закрыть сокет — клиент получал разрыв с валидным токеном.

**Исправление:** вместо `asyncio.sleep` + проверки флага используется `asyncio.Event`:

```python
_auth_event = asyncio.Event()

async def _auth_timeout():
    try:
        await asyncio.wait_for(_auth_event.wait(), timeout=WS_AUTH_TIMEOUT)
    except asyncio.TimeoutError:
        if not ws.closed:
            await ws.close(code=1008, message=b'auth timeout')
```

При успешной аутентификации: `_auth_event.set()` — атомарно, без `cancel()`.  
В `finally`: `_auth_event.set()` как страховка при любом выходе из handler.

---

### 3.6 — Двойной `wait_for` в `db_spatial.py`

**Файл:** `core/db/db_spatial.py`

Все 4 вызова вида:
```python
await asyncio.wait_for(self.db.fetchval(query, ...), timeout=settings.db.command_timeout)
```
заменены на:
```python
await self.db.fetchval(query, ..., timeout=settings.db.command_timeout)
```

`db_base.fetchval/fetch` уже принимают параметр `timeout` и применяют его внутри. Двойной `wait_for` создавал риск незакрытых соединений при отмене. Удалён неиспользуемый импорт `asyncio`.

---

## Фаза 4 — Качество кода (🔵)

### 4.1 — `datetime.utcnow()` → `datetime.now(timezone.utc)`

**Файлы:**
- `core/api/health.py` — 3 вхождения. Добавлен импорт `timezone`.
- `core/utils/logging_config.py` — `JSONFormatter.format()`. Добавлен импорт `timezone`. Убран суффикс `'Z'` — `isoformat()` возвращает `+00:00` для aware datetime.

---

### 4.2 — Кэш `base.resolve()` в `media.py`

**Файл:** `core/api/media.py`

`Path.resolve()` — блокирующий syscall (stat + readlink). До правки вызывался на каждый запрос медиафайла.

**Исправление:** добавлен `_media_base_cache: dict[str, Path]` и функция `_get_media_base(events_dir)` — resolved Path директории кэшируется один раз при первом обращении. `candidate.resolve()` (для конкретного файла) по-прежнему вызывается на каждый запрос — это необходимо для защиты от symlink-escape.

---

### 4.3 — `asyncio.Event` для shutdown в `processor/main.py`

**Файл:** `processor/main.py`

Добавлен `self._shutdown_event = asyncio.Event()` в `__init__`.

`_request_stop()` теперь вызывает `loop.call_soon_threadsafe(self._shutdown_event.set)` — безопасно из signal handler в любом потоке. `self._running = False` сохранён для обратной совместимости с `while self._running` в `_worker`.

Главный цикл `run()`: `while self._running` → `while not self._shutdown_event.is_set()` — идиоматично, согласованно с паттерном в `core/app_factory.py`.

---

### 4.4 — Защитный лимит в `get_batch_intersections`

**Файл:** `core/db/db_spatial.py`

Добавлена явная проверка перед CROSS JOIN запросом:

```python
_MAX_GEO_IDS = 20
if len(geo_ids) > _MAX_GEO_IDS:
    logger.warning(f"get_batch_intersections: {len(geo_ids)} geo_ids exceeds limit {_MAX_GEO_IDS}, truncating")
    geo_ids = geo_ids[:_MAX_GEO_IDS]
```

Процессор уже ограничивает top-5 (R-PR10), лимит служит явной защитой при будущих изменениях `max_entities`.

---

## Сводная таблица изменений

| # | Задача | Статус | Затронутые файлы |
|---|--------|--------|-----------------|
| 1.1 | Отзыв BOT_TOKEN + JWT_SECRET | ⏳ Вручную | `.env` |
| 1.2 | Удалить session.session из репо | ⏳ Вручную | `parser/session.session`, `.gitignore` |
| 1.3 | TypeError time_filter + AttributeError since | ✅ | `core/api/events.py` |
| 1.4 | Race condition LogRecordFactory | ✅ | `core/utils/logging_config.py` |
| 2.1 | Refresh Token Rotation | ✅ | `core/api/auth.py`, `core/middlewares/auth.py`, `core/db/db_auth.py`, `core/db/dbconnect.py`, `postgres/init-scripts/17-refresh-tokens.sql` |
| 2.2 | Dev-bypass APP_ENV guard | ✅ | `core/api/auth.py`, `.env.example` |
| 2.3 | validate_max_age_hours min=1 | ✅ | `core/utils/validators.py` |
| 3.1 | channel_id в env | ✅ | `core/settings.py`, `.env.example` |
| 3.2 | Убрать password="postgres" из dataclass | ✅ | `core/settings.py` |
| 3.3 | Удалить MessageProcessor | ✅ | `parser/monitoring.py`, ~~`parser/message_processor.py`~~ |
| 3.4 | Удалить DbMiddleware | ✅ | `core/app_factory.py`, ~~`core/middlewares/dbmiddleware.py`~~ |
| 3.5 | WS auth race condition | ✅ | `core/api/websocket.py` |
| 3.6 | Двойной wait_for в db_spatial | ✅ | `core/db/db_spatial.py` |
| 4.1 | datetime.utcnow() → now(timezone.utc) | ✅ | `core/api/health.py`, `core/utils/logging_config.py` |
| 4.2 | Кэш base.resolve() в media.py | ✅ | `core/api/media.py` |
| 4.3 | asyncio.Event для shutdown processor | ✅ | `processor/main.py` |
| 4.4 | Лимит в get_batch_intersections | ✅ | `core/db/db_spatial.py` |

**Итого изменённых файлов:** 17  
**Удалено файлов:** 2 (`parser/message_processor.py`, `core/middlewares/dbmiddleware.py`)  
**Создано файлов:** 2 (`core/db/db_auth.py`, `postgres/init-scripts/17-refresh-tokens.sql`)

---

## Действия после деплоя

1. Применить миграцию `17-refresh-tokens.sql` к БД (или пересоздать контейнер postgres — init-scripts выполняются автоматически).
2. Добавить `CHANNEL_ID` и `APP_ENV` в production `.env`.
3. Выполнить задачи 1.1 и 1.2 вручную (отзыв токенов, очистка git-истории).
4. После ротации `JWT_SECRET` — все текущие сессии инвалидируются, пользователи повторно авторизуются через Telegram.
