# Survival Map

Telegram Mini App — интерактивная карта событий Одессы (блокпосты, ТЦК, полиция,
транспорт). Парсер читает Telegram-канал, извлекает из сообщений упоминания улиц,
геолоцирует их в PostGIS и в реальном времени отдаёт на карту через WebSocket.
События живут 60 минут (TTL) и исчезают сами.

- **Извлечение улиц** — sliding-window матчер: морфология (`mawo-pymorphy3`) +
  fuzzy-сопоставление (`rapidfuzz`) против газеттира улиц. Без NER/нейросетей,
  CPU-only. Детали алгоритма — [docs/parser.md](docs/parser.md).
- **Карта** — PWA на нативном MapLibre GL JS, offline-first. Детали —
  [docs/web.md](docs/web.md), правила — [web/CLAUDE.md](web/CLAUDE.md).

## Архитектура

Пять Docker-сервисов (`docker-compose.yml`):

| Сервис     | Назначение                                                        | Публичный порт |
|------------|-------------------------------------------------------------------|----------------|
| `postgres` | PostgreSQL + PostGIS: улицы (газеттир) и события с геометрией      | —              |
| `parser`   | kurigram-клиент: канал → матчер → запись событий + `pg_notify`     | —              |
| `core`     | aiohttp: REST + WebSocket, JWT-валидация Telegram, `LISTEN events` | —              |
| `redis`    | кэш                                                               | —              |
| `web`      | reverse-proxy + статика фронтенда (собирается в образе)            | **80**         |

Поток данных:

```
Telegram-канал → parser (sliding-window матчер) → PostgreSQL (PostGIS)
   → pg_notify → core (LISTEN → WebSocket) → web → фронтенд (карта MapLibre)
```

Сети изолированы: БД во внутренней сети (`internal: true`), наружу торчит только
web:80.

## Деплой

### 0. Требования

- Docker + Docker Compose v2
- Telegram-аккаунт для чтения канала (парсер работает под **пользовательской**
  сессией, не под ботом) и бот от [@BotFather](https://t.me/BotFather) для Mini App

### 1. Создание Telegram-сессии (отдельно от приложения)

Парсер **не логинится в рантайме** — он ожидает готовый файл
`parser/session.session` и монтирует его volume'ом
(см. `_init_telegram_client` в [parser/monitoring.py](parser/monitoring.py)).
Сессию нужно создать **один раз заранее** вне приложения чтобы не хранить ваш api_id и api_hash в кодовой базе.

1. Получите `api_id` и `api_hash` на <https://my.telegram.org/apps>.
2. Установите клиент локально: `pip install kurigram qrcode`.
3. Создайте `gen_session.py` и запустите его — он спросит номер телефона и код
   из Telegram (и пароль 2FA, если включён):

   ```python
   # gen_session.py — запустить ОДИН раз; создаёт ./session.session
   from pyrogram import Client            # модуль ставится пакетом kurigram

   API_ID = 0000000                       # с my.telegram.org/apps
   API_HASH = "xxxxxxxxxxxxxxxxxxxxxxxx"  # с my.telegram.org/apps

   app = Client(
    "session",
    api_id=API_ID,
    api_hash=API_HASH
  )
  app.start(use_qr=True)
  print("Сессия создана:", app.get_me().username)
  app.stop()
   ```

   ```bash
   python gen_session.py        # вводите телефон + код
   ```

4. Положите полученный файл рядом с парсером и закройте права:

   ```bash
   mv session.session parser/session.session
   chmod 600 parser/session.session
   ```

`api_id`/`api_hash` зашиваются внутрь `session.session` — в рантайме они больше
не нужны. Файл в `.gitignore` (`*.session`), в репозиторий не попадает.

### 2. Конфигурация `.env`

```bash
cp .env.example .env
```

Заполните (`.env.example` содержит только секреты и per-deployment URL; остальное
захардкожено в [core/settings.py](core/settings.py)):

| Переменная                    | Обяз. | Описание                                            |
|-------------------------------|-------|-----------------------------------------------------|
| `BOT_TOKEN`                   | да    | токен бота от @BotFather (для Mini App)             |
| `JWT_SECRET`                  | да    | ≥32 символов: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `CHANNEL_ID`                  | да    | ID канала для мониторинга, формат `-100…`           |
| `WEBAPP_URL` / `REDIRECT_URL` | нет   | публичные HTTPS-URL для Telegram WebApp             |
| `TELEGRAM_VALIDATION_ENABLED` | нет   | по умолч. `True`; `False` — только для dev          |

### 3. Запуск

```bash
docker compose up -d --build
```

Фронтенд собирается внутри `Dockerfile.web` (node-builder → `nginx:alpine`),
отдельный `npm run build` не нужен. Порядок готовности:
`postgres → parser/core → web`. Приложение доступно на `http://<host>:80/`.

Проверка:

```bash
docker compose ps                       # все сервисы healthy
curl -fsS http://localhost/health/ready # 200 OK
docker compose logs -f parser           # «Telegram client started», обработка сообщений
```

### 4. Обновление данных улиц

Газеттир — [postgres/data/streets.csv](postgres/data/streets.csv)
(формат `название|алиас1|алиас2,WKT-геометрия`). Он загружается **при инициализации
БД**, поэтому после правки нужно либо пересоздать том БД:

```bash
docker compose down -v && docker compose up -d --build
```

либо добавить запись в работающую БД вручную (без потери событий):

```bash
docker compose exec postgres psql -U postgres -c \
  "INSERT INTO streets(names, geom) VALUES (ARRAY['7 км'],
   ST_GeomFromText('POINT(30.6402739 46.4419476)',4326));"
```

### Остановка

```bash
docker compose down        # все сервисы завершаются корректно (exit 0)
docker compose down -v     # + удалить тома (БД, медиа, redis)
```

## Структура репозитория

```
core/        backend сервиса `core` (aiohttp app, API, БД-адаптеры, settings)
parser/      сервис `parser` (kurigram + sliding-window матчер)
postgres/    init-скрипты схемы и данные (streets.csv, stopwords.csv)
web/         фронтенд сервиса `web` (TypeScript + MapLibre GL, webpack)
docs/        по одному файлу на микросервис (core, parser, web, postgres, redis)
```

## Документация

По документу на каждый микросервис:

- [docs/core.md](docs/core.md) — backend: REST + WebSocket API, JWT/Telegram, middleware, БД-адаптеры
- [docs/parser.md](docs/parser.md) — алгоритм парсера (sliding-window, тиры матча)
- [docs/web.md](docs/web.md) — фронтенд + nginx (PWA, MapLibre, reverse-proxy)
- [docs/postgres.md](docs/postgres.md) — схема PostGIS, газеттир, TTL событий
- [docs/redis.md](docs/redis.md) — кэш / session-store

Поддержать разработчиков монетой здесь:
 https://bastyon.com/keep_alive_odessa?ref=PHQHKADhBPxxSwjiggV6G2BxSvy6TY1Lgb