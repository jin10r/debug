# Survival Map

Telegram Mini App — интерактивная карта событий Одессы (блокпосты, ТЦК, полиция,
транспорт). Парсер читает Telegram-канал, NLP-пайплайн извлекает упоминания улиц,
PostGIS геолоцирует события, WebSocket в реальном времени доставляет их на карту.
События живут 60 минут (TTL) и исчезают автоматически.

- **Извлечение улиц** — sliding-window матчер: морфология (`mawo-pymorphy3`) +
  fuzzy-сопоставление (`rapidfuzz`) против справочника гео-объектов (`postgres/data/geo.csv`).
  CPU-only, без NER/нейросетей. Детали — [docs/parser.md](docs/parser.md).
- **Карта** — PWA на Leaflet + MapLibre GL, offline-first. Детали —
  [docs/web.md](docs/web.md).

## Архитектура

Пять Docker-сервисов (`docker-compose.yml`):

| Сервис | Назначение | Публичный порт |
|--------|-----------|----------------|
| `postgres` | PostgreSQL + PostGIS: справочник гео-объектов, события с геометрией | — |
| `parser` | kurigram-клиент: канал → предобработка текста → `pending_events` | — |
| `processor` | NLP-пайплайн: морфология → geo-матчинг → семантический резолвер → PostGIS | — |
| `core` | aiohttp: REST + WebSocket, JWT-валидация Telegram, `LISTEN events` | — |
| `web` | nginx reverse-proxy + статика фронтенда | **80** |

Поток данных:

```
Telegram-канал → parser → pending_events → processor (NLP pipeline)
  → events (PostGIS) → pg_notify → core (LISTEN → WebSocket) → web → фронтенд
```

Сети изолированы: БД во внутренней сети (`internal: true`), наружу торчит только `web:80`.

## Деплой

### 0. Требования

Хост — Linux или macOS с `bash` (Windows — через WSL2). Нужны:

- **Docker** + **Docker Compose v2**
- **Git**
- **Python 3.10+** с `pip` и `venv` — только для одноразовой генерации Telegram-сессии
- **Telegram-аккаунт, подписанный на целевой канал**
- **Бот от [@BotFather](https://t.me/BotFather)** (`BOT_TOKEN`)
- **Только для production Mini App:** публичный домен с **HTTPS**

### 1. Клонирование

```bash
git clone https://github.com/develop4alive/survival_map
cd survival_map
```

### 2. Создание Telegram-сессии (один раз)

Парсер использует файл `parser/session.session`. Создаётся один раз через
скрипт `scripts/gen_session.py`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install kurigram qrcode
python scripts/gen_session.py <api_id> <api_hash>
deactivate
rm -rf .venv
```

Аккаунт должен быть **подписан на целевой канал**.

### 3. Конфигурация `.env`

```bash
cp env.example .env
# Обязательно: вписать BOT_TOKEN
```

| Переменная | Обяз. | Описание |
|-----------|-------|----------|
| `BOT_TOKEN` | да | Токен бота от @BotFather |
| `WEBAPP_URL` | для prod | Публичный HTTPS-URL приложения |
| `TELEGRAM_VALIDATION_ENABLED` | нет | `True` — доступ только из Telegram; `False` — локальная отладка |
| `REDIRECT_URL` | нет | Куда редиректить не-Telegram трафик |

### 4. Запуск

```bash
docker compose up -d --build
```

Проверка:

```bash
docker compose ps
curl -fsS http://localhost/health/ready
docker compose logs -f parser
```

### 5. Открытие приложения

**Локально (dev):** `TELEGRAM_VALIDATION_ENABLED=False` → `http://localhost/`

**Production Mini App:** HTTPS-фронт (Cloudflare Tunnel / Caddy) → `WEBAPP_URL` →
настройка в @BotFather.

### Остановка

```bash
docker compose down        # остановить сервисы
docker compose down -v     # + удалить тома (БД, медиа)
```

## Структура репозитория

```
core/         backend (aiohttp, REST + WebSocket, middleware)
parser/       Telegram-клиент (kurigram), текстовая предобработка
processor/    NLP-пайплайн (морфология, geo-матчинг, семантический резолвер)
postgres/     init-скрипты схемы PostGIS, справочные данные (geo.csv)
web/          фронтенд (TypeScript + Leaflet + MapLibre GL, PWA)
docs/         документация микросервисов
```

## Документация

- [docs/core.md](docs/core.md) — backend: REST/WS API, JWT/Telegram, middleware
- [docs/parser.md](docs/parser.md) — алгоритм парсера (kurigram, предобработка)
- [docs/processor.md](docs/processor.md) — NLP-пайплайн (морфология, матчинг, резолвер)
- [docs/web.md](docs/web.md) — фронтенд PWA + nginx
- [docs/postgres.md](docs/postgres.md) — схема PostGIS, TTL событий
