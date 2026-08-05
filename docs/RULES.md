# Правила проекта — Survival Map

Единый документ правил для всех микросервисов. Каждый контейнер — независимый сервис.

---

## Общие правила (все сервисы)

### G-1: Каждый контейнер — независимый сервис

5 сервисов: `web`, `core`, `postgres`, `parser`, `processor`. Каждый — отдельный Dockerfile, отдельная точка входа, отдельные зависимости.

### G-2: Минимум зависимостей

Каждый сервис содержит только необходимые ему библиотеки:
- **web**: nginx + статика (node только при сборке)
- **core**: aiohttp + aiogram + asyncpg + pyjwt
- **parser**: kurigram + asyncpg + environs
- **processor**: pymorphy3 + rapidfuzz + asyncpg + snowballstemmer
- **postgres**: postgis/postgis + pg_cron

### G-3: Запуск одной командой

```bash
docker-compose up --build
```

Без хелп-скриптов, без предварительных шагов (кроме `session.session` и `.env`).

### G-4: Параллельная сборка

Все Dockerfile используют `--network=host` при build для параллельной сборки. Образы кэшируются через BuildKit (`--mount=type=cache`).

### G-5: Кэширование сборки

| Сервис | Механизм кэширования |
|--------|---------------------|
| web | `--mount=type=cache,target=/root/.npm` |
| core | `--mount=type=cache,target=/root/.cache/pip` + apt |
| parser | Multi-stage: builder + runtime, pip cache |
| processor | Multi-stage: builder + runtime, pip cache |
| postgres | apt cache |

### G-6: Внутренняя Docker-сеть

| Сеть | Тип | Сервисы |
|------|-----|---------|
| `frontend` | bridge | web, core |
| `backend` | bridge | core, parser, processor |
| `db` | bridge, **internal: true** | postgres, parser, processor, core |

**Правило:** Только `web` имеет внешний порт (80). Все остальные — internal only.

### G-7: Ресурсы контейнеров

| Сервис | CPU limit | Memory limit | CPU reservation | Memory reservation |
|--------|-----------|--------------|-----------------|-------------------|
| postgres | 1.0 | 1G | 0.5 | 512M |
| parser | 0.5 | 256M | 0.25 | 128M |
| processor | 1.5 | 1G | 0.5 | 512M |
| core | 1.0 | 768M | 0.25 | 128M |
| web | 0.5 | 128M | 0.1 | 64M |

### G-8: Безопасность контейнеров

Все сервисы (кроме postgres):
```yaml
security_opt:
  - no-new-privileges:true
cap_drop:
  - ALL
```

### G-9: Healthcheck

Все сервисы имеют healthcheck. Порядок готовности: `postgres → parser/core/processor → web`.

### G-10: Логирование

Единый формат: `json-file` с ротацией (max 10MB × 5 файлов на сервис).

```yaml
x-default-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "5"
```

### G-11: Единый settings.py

Все Python-сервисы (core, parser, processor) переиспользуют `core/settings.py` для конфигурации. Собственные настройки минимальны.

### G-12: Restart policy

```yaml
restart: unless-stopped
```

### G-13: Shared volumes

| Volume | Сервисы | Назначение |
|--------|---------|-----------|
| `postgres_data` | postgres | Данные PostgreSQL |
| `events_media` | parser (rw), core (ro), web (ro) | Фотографии событий |

---

## Поток данных

```
Telegram (MTProto) → parser (strip_tail + preprocess_light) → pending_events
    → processor (tokenize → lemmatize → classify → find_geo → resolve → INSERT)
    → postgres (process_candidates CTE + pg_notify)
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
| Дроп сообщения при спаме/нет гео | Событие не попадает на карту | R-PR8.1 |
| Отсутствие drain при shutdown | Потеря сообщений | R-P3, R-C3, R-PR3 |
| Hardcoded credentials | Security | G-11 |
| Внешние порты кроме 80 | Security | G-6 |
| dev-bypass в production | Security | R-C10 |

---

*Правила проекта — июль 2026*
