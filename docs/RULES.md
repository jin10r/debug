# Правила проекта — Survival Map v2.0

Единый документ правил для всех микросервисов. Каждый контейнер — независимый сервис.

---

## Глобальные правила

### G-1: Single Source of Truth

PostgreSQL — единственное надежное хранилище состояния.

### G-2: Минимум зависимостей

Каждый сервис содержит только необходимые ему библиотеки.

### G-3: Fail Fast

Валидация конфигурации при старте. Ошибки должны быть громкими.

### G-4: Observability First

Структурированные логи (JSON), метрики.

### G-5: Security by Default

Запрет хардкод-секретов. Строгая валидация входных данных.

### G-6: Idempotency

Все операции записи идемпотентны (`ON CONFLICT`).

### G-7: Resource Limits

Лимиты CPU/RAM в `docker-compose.yml` обязательны.

### G-11: Единый settings.py

Все Python-сервисы переиспользуют `core/settings.py`.

**Булевы env-переменные парсятся строго** через `_parse_strict_bool`
(по умолчанию `True`; `False` — только при явном `'false'`/`'0'`). Никаких
inline-`os.getenv`/`env.bool` для auth-флагов за пределами `settings.py`.

### G-14: Модель в образе

Веса LLM скачиваются только при сборке Dockerfile. В рантайме — запрещено.

### G-15 / G-16: Сессия без авторизации

`api_id/api_hash` только в `gen_session.py`. Рантайм-авторизация запрещена.

---

## Поток данных

```
Telegram (MTProto) → parser (strip_tail + preprocess_light) → pending_events
    → processor (tokenize → lemmatize → classify → find_geo → resolve → process_candidates_v2)
    → postgres (geometry-first CTE + pg_notify)
    → core (LISTEN events_new → WebSocket broadcast)
    → web (nginx reverse proxy + Leaflet PWA)
    → Browser / Telegram WebView
```

---

## Правила по сервисам

Подробные правила каждого сервиса — в отдельных файлах:

| Сервис | Файл правил | Точка входа | Порт |
|--------|-------------|-------------|------|
| **postgres** | [RULES_POSTGRES.md](RULES_POSTGRES.md) | `postgres -c config_file=...` | 5432 (internal) |
| **parser** | [RULES_PARSER.md](RULES_PARSER.md) | `python -m parser.monitoring` | heartbeat only |
| **processor** | [RULES_PROCESSOR.md](RULES_PROCESSOR.md) | `python -m processor.main` | heartbeat only |
| **core** | [RULES_CORE.md](RULES_CORE.md) | `python main.py` | 8080 (internal) |
| **web** | [RULES_WEB.md](RULES_WEB.md) | nginx | **80 (external)** |

---

## Антипаттерны (ЗАПРЕЩЕНО — глобальные)

| Антипаттерн | Почему | Правило |
|-------------|--------|---------|
| NLP-код в parser/core | Нарушение разделения | G-1, R-P1, R-C1 |
| Синхронные вызовы в async hot path | Блокирует event loop | R-P2, R-C2 |
| SQL-конкатенация | SQL injection | R-P12, R-C17 |
| INSERT без ON CONFLICT | Дубликаты при ретраях | R-P5, R-PR12 |
| Дроп сообщений при спаме/нет гео | Событие не попадает на карту | R-PR8.1 |
| Отсутствие drain при shutdown | Потеря сообщений | R-P3, R-C3, R-PR3 |
| Hardcoded credentials | Security | G-11 |
| Внешние порты кроме 80 | Security | G-6 |
| dev-bypass в production | Security | R-C10 |
| Эфемерная генерация JWT_SECRET | Массовый logout при деплое | R-C8 |
| Ручной DELETE по времени в events | Lock contention, ломает BRIN | R-DB2, R-DB3 |
| ThreadPoolExecutor для rapidfuzz | GIL блокирует event loop | R-PR19 |
| Семантические эвристики текста для геометрии | Нарушение Geometry-First, неточность | R-PR27, R-DB8 |

---

*Правила проекта — август 2026*
