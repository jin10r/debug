# Rules — Parser Service

**Сервис:** `parser/` (kurigram + text preprocessing + photo download)  
**Точка входа:** `python -m parser.monitoring`  
**Порт:** 8765 (healthcheck only)

---

## 1. Архитектурные правила

### R-P1: Parser — ТОЛЬКО приём и предобработка

Parser НЕ выполняет NLP-классификацию, не ищет гео-объекты, не вычисляет геометрию. Его обязанность:

```
Telegram → strip_tail → preprocess_light → INSERT INTO pending_events
```

**Запрещено:**
- вызов `process_candidates()` или `geo_execute_scenario()`
- импорт `GeoMatcher`, `SemanticResolver`, `LayerClassifier`
- любой NLP-код (лемматизация, стемминг, токенизация)

**Исключение:** `text_preprocessor.py` — это **общий** модуль, переиспользуемый processor. Parser использует только `strip_tail` и `preprocess_light`.

### R-P2: Async архитектура — один event loop

Parser работает в одном asyncio event loop. Все операции — async/await. Синхронные вызовы (кроме `_write_heartbeat`) запрещены в hot path.

```python
# ✅ Правильно
await self.db.pool.execute(query, *args)

# ❌ Неправильно
import subprocess
subprocess.run([...])  # блокирует event loop
```

### R-P3: Graceful shutdown — drain before exit

При SIGTERM parser:
1. Перестаёт принимать новые сообщения (`self._running = False`)
2. Дренирует очередь (`pending_queue.join()`, timeout=20s)
3. Отменяет worker tasks
4. Останавливает Telegram client
5. Закрывает DB пул

```python
async def shutdown(self, drain_timeout: float = 20.0):
    self._running = False
    await asyncio.wait_for(self._pending_queue.join(), timeout=drain_timeout)
    # ... cancel tasks, close connections
```

**Правило:** `stop_grace_period` в docker-compose должен быть ≥ `drain_timeout + запас`.

### R-P4: Heartbeat healthcheck

Parser пишет timestamp в `/tmp/parser_heartbeat` каждую секунду. Docker healthcheck проверяет свежесть файла:

```bash
test -f /tmp/parser_heartbeat && [ $(( $(date +%s) - $(cat /tmp/parser_heartbeat) )) -lt 60 ]
```

**Правило:** Если heartbeat не обновляется >60s → контейнер перезапускается.

### R-P5: Idempotent INSERT

Все вставки в `pending_events` используют `ON CONFLICT DO NOTHING`:

```sql
INSERT INTO pending_events (message_id, text, event_time, photo_file_id)
VALUES ($1, $2, $3, $4)
ON CONFLICT (message_id, event_time) DO NOTHING
```

**Правило:** Ретраи и backfill НЕ создают дубликатов.

---

## 2. Правила работы с текстом

### R-P6: preprocess_light СОХРАНЯЕТ регистр

`preprocess_light()` используется для `description` (отображается на фронтенде). Регистр, emoji, пунктуация — сохраняются.

```python
# ✅ Результат уходит на фронтенд
description = preprocess_light(raw_text)  # "На Гаванной 🔥 блокпост"

# ❌ Не использовать clean() для description
description = clean(raw_text)  # "на гаванной блокпост" — потеря регистра
```

### R-P7: strip_tail — МЯГКАЯ очистка

`strip_tail()` удаляет служебный хвост ("сообщить", "подписаться"), но НЕ трогает основной контент. Маркеры — регистронезависимые.

### R-P8: Unicode normalization

Украинские буквы → русские:
- `і`, `І` → `и`, `И`
- `ї`, `Ї` → `и`, `И`
- `є`, `Є` → `е`, `Е`

Украинские окончания → русские:
- `івська` → `овская`
- `ський` → `ский`

**Правило:** Нормализация применяется в `preprocess_light()` и `clean()`.

### R-P9: Emoji handling

Emoji УДАЛЯЮТСЯ только перед токенизацией/матчингом (`strip_emoji()`). В `description` emoji СОХРАНЯЮТСЯ.

### R-P10: Промо/спам детекция

`is_promotional()` — высокоточный детектор (ссылки, Telegram-хендлы, призывы к подписке). При срабатывании → `strategy=random` (случайная точка), но сообщение НЕ игнорируется.

**Правило:** Precision важнее recall — мягкая реклама намеренно не ловится.

---

## 3. Правила работы с БД

### R-P11: Один пул соединений

Parser использует один `asyncpg.Pool` на весь процесс. Пул создаётся при старте, закрывается при shutdown.

```python
self.__pool = await asyncpg.create_pool(
    min_size=settings.db.pool_min_size,   # 5
    max_size=settings.db.pool_max_size,   # 30
    command_timeout=settings.db.command_timeout,  # 60s
)
```

### R-P12: Parameterized queries

Все SQL-запросы используют параметризацию ($1, $2, ...). Конкатенация строк ЗАПРЕЩЕНА:

```python
# ✅ Правильно
await conn.execute("INSERT INTO ... VALUES ($1, $2)", val1, val2)

# ❌ SQL injection
await conn.execute(f"INSERT INTO ... VALUES ('{val1}')")
```

### R-P13: Connection release

При использовании `pool.acquire()` соединение автоматически возвращается в пул при выходе из `async with`. Ручной `release()` — только для long-lived соединений (LISTEN/NOTIFY).

### R-P14: LISTEN/NOTIFY для фото

Parser слушает два канала:
- `photo_download` — скачивание фото после обработки events
- `events_cleaned` — удаление физических файлов фото

**Правило:** Каждый listener имеет own connection из пула + backoff reconnect.

---

## 4. Правила безопасности

### R-P15: Path traversal protection

Скачивание фото блокирует path traversal:

```python
final_path = (target_dir / filename).resolve()
try:
    final_path.relative_to(target_dir)
except ValueError:
    logger.error(f"Path traversal blocked: {final_path}")
    return None
```

### R-P16: Symlink protection

Если `final_path` — symlink, он удаляется перед скачиванием:

```python
if final_path.is_symlink():
    final_path.unlink()
```

### R-P17: Sanitize text

Все тексты проходят `utf-8` sanitize:

```python
def _sanitize_text(text: Optional[str]) -> Optional[str]:
    if not text:
        return text
    return text.encode('utf-8', errors='replace').decode('utf-8')
```

### R-P18: Proxy configuration

SOCKS5/HTTP proxy настраивается через settings (не env для敏感ных данных):

```python
proxy_config = {
    "scheme": settings.parser.proxy_scheme,
    "hostname": proxy_host,
    "port": settings.parser.proxy_port,
}
```

---

## 5. Правила логирования

### R-P19: Structured logging

Parser использует JSON-формат (по умолчанию) или текстовый — через `settings.app.log_format`.

```python
if _LOG_FORMAT == 'json':
    from core.utils.logging_config import JSONFormatter
    _formatter = JSONFormatter()
else:
    _formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
```

### R-P20: Log levels

- `DEBUG` — progress загрузки истории, детали обработки
- `INFO` — инициализация, успешная обработка, shutdown
- `WARNING` — retry, fallback, потенциальные проблемы
- `ERROR` — ошибки обработки, DB-ошибки
- `CRITICAL` — crash воркера (auto-respawn)

---

## 6. Правила конфигурации

### R-P21: Settings из core/settings.py

Parser переиспользует `core/settings.py` для всех настроек. Собственные настройкиparser — минимальны:

```python
@dataclass
class ParserConfig:
    history_limit: int = 100
    events_media_dir: str = "/media/events"
    socks5_host: Optional[str] = None
    proxy_host: Optional[str] = None
    proxy_scheme: str = "socks5"
    proxy_port: int = 1080
```

### R-P22: Environment variables

Только sensitive/per-deployment настройки через env:
- `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`
- `PROXY_HOST`, `PROXY_PORT`, `PROXY_SCHEME`

Всё остальное — хардкод в `settings.py`.

---

## 7. Правила тестирования

### R-P23: Минимальное покрытие

Parser — простой сервис, основной фокус на интеграционных тестах:
- Unit: `text_preprocessor.py` (strip_tail, preprocess_light, is_promotional)
- Integration: DB operations (pending_events INSERT/SELECT)
- E2E: Telegram → pending_events flow

### R-P24: Mock Telegram messages

Тесты используют mock-объекты для `pyrogram.Message`:

```python
class MockMessage:
    def __init__(self, text, chat_id, message_id, date, photo=None):
        self.text = text
        self.chat = type('Chat', (), {'id': chat_id})()
        self.id = message_id
        self.date = date
        self.photo = photo
        self.caption = None
```

---

## 8. Антипаттерны (ЗАПРЕЩЕНО)

| Антипаттерн | Почему | Правило |
|-------------|--------|---------|
| NLP-код в parser | Нарушение разделения обязанностей | R-P1 |
| Синхронные вызовы в hot path | Блокирует event loop | R-P2 |
| SQL-конкатенация | SQL injection | R-P12 |
| `clean()` для description | Потеря регистра/emoji | R-P6 |
| Hardcoded credentials | Security | R-P22 |
| Отсутствие drain при shutdown | Потеря сообщений | R-P3 |
| Ручной `pool.release()` без try/except | Утечка соединений | R-P13 |
| Игнорирование `ON CONFLICT` | Дубликаты при ретраях | R-P5 |

---

*Правила основаны на анализе кодовой базы parser/ — июль 2026*
