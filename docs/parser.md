# Parser microservice

Сервис `parser` (контейнер из `Dockerfile.parser`) — Telegram-клиент на `kurigram`.
Читает сообщения из целевого канала, выполняет минимальную текстовую предобработку
и сохраняет результат в таблицу `pending_events` для дальнейшей обработки
сервисом `processor`.

## Технологический стек

| Компонент | Назначение |
|-----------|-----------|
| `kurigram` (pyrogram fork) | Telegram MTProto client |
| `asyncpg` | PostgreSQL async driver |

## Pipeline

```
1. Kurigram handler получает новое сообщение из канала
2. strip_tail(text) — удаление хвоста «подписаться/сообщить»
3. preprocess_light(text) — удаление HTML, нормализация укр. букв,
   удаление хештегов, извлечение времени
4. Запись в pending_events (text, message_id, event_time, photo_file_id)
5. pg_notify → processor забирает сообщение
```

## Модули

| Файл | Назначение |
|------|-----------|
| `monitoring.py` | Pyrogram client + asyncio.Queue + worker |
| `text_preprocessor.py` | strip_tail + preprocess_light |
| `db_adapter.py` | PostgreSQL pool |
| `settings.py` | Конфигурация (наследует core/settings.py) |
