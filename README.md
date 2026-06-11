# Survival Map

Telegram Mini App — интерактивная карта событий Одессы (блокпосты, ТЦК, полиция,
транспорт). Парсер читает Telegram-канал, извлекает из сообщений упоминания улиц,
геолоцирует их в PostGIS и в реальном времени отдаёт на карту через WebSocket.
События живут 60 минут (TTL) и исчезают сами.

- **Извлечение улиц** — sliding-window матчер: морфология (`mawo-pymorphy3`) +
  fuzzy-сопоставление (`rapidfuzz`) против справочника гео-объектов (postgres/data/streets.csv). Без NER/нейросетей,
  CPU-only. Детали алгоритма — [docs/parser.md](docs/parser.md).
- **Карта** — PWA на нативном MapLibre GL JS, offline-first. Детали —
  [docs/web.md](docs/web.md)

## Архитектура

Пять Docker-сервисов (`docker-compose.yml`):

| Сервис     | Назначение                                                        | Публичный порт |
|------------|-------------------------------------------------------------------|----------------|
| `postgres` | PostgreSQL + PostGIS: объекты (справочник) и события с геометрией      | —              |
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

> **sudo.** Если ваш пользователь не в группе `docker`, команды `docker …` ниже
> запускайте с `sudo` (`sudo docker compose …`). Либо один раз добавьте себя в
> группу и перелогиньтесь: `sudo usermod -aG docker $USER`.

### 1. Клонирование репозитория

```bash
git clone https://github.com/develop4alive/survival_map
cd survival_map
```

Все последующие команды выполняются **из корня проекта** (`survival_map/`).

### 2. Создание Telegram-сессии (один раз)

Парсер **не логинится в рантайме** — он ожидает готовый файл
`parser/session.session` и монтирует его volume'ом
(см. `_init_telegram_client` в [parser/monitoring.py](parser/monitoring.py)).
Делается **один раз**: пока сессия валидна, при обновлении или передеплое
приложения пересоздавать сессию не нужно. `api_id`/`api_hash` в кодовой базе не хранятся.

1. Получите `api_id` и `api_hash` на <https://my.telegram.org/apps>.
2. Создайте виртуальное окружение и установите клиент:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install kurigram qrcode
   ```

3. Запустите готовый скрипт [gen_session.py](gen_session.py), передав
   `api_id`/`api_hash` аргументами:

   ```bash
   python gen_session.py <api_id> <api_hash>           # вход по QR (по умолчанию)
   ```

   Telegram → Настройки → Устройства → «Подключить устройство» → отсканируйте
   QR из терминала (если включена 2FA — введите пароль). Альтернатива — вход по
   телефону: добавьте флаг `--phone`.

4. Готово — скрипт сам сохраняет `parser/session.session` с правами `600`.
   Ручные `mv`/`chmod` не нужны.

5. Деактивируйте и удалите виртуальное окружение — для рантайма оно не нужно:

   ```bash
   deactivate
   rm -rf .venv
   ```

`api_id`/`api_hash` зашиваются внутрь `session.session` — в рантайме они больше
не нужны. Файл в `.gitignore` (`*.session`), в репозиторий не попадает.

### 3. Конфигурация `.env`

```bash
cp .env.example .env
```

Заполните (`.env.example` содержит только секреты и per-deployment URL; остальное захардкожено дефолтными значениями в [core/settings.py](core/settings.py)):

| Переменная                    | Обяз. | Описание                                            |
|-------------------------------|-------|-----------------------------------------------------|
| `BOT_TOKEN`                   | да    | токен бота от @BotFather (для Mini App)             |
| `JWT_SECRET`                  | да    | ≥32 символов: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `CHANNEL_ID`                  | да    | ID канала для мониторинга, формат `-100…`           |
| `WEBAPP_URL` / `REDIRECT_URL` | нет   | публичные HTTPS-URL для Telegram WebApp             |
| `TELEGRAM_VALIDATION_ENABLED` | нет   | по умолч. `True`; `False` — только для dev          |

### 4. Запуск

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
- [docs/postgres.md](docs/postgres.md) — схема PostGIS, справочник, TTL событий
- [docs/redis.md](docs/redis.md) — кэш / session-store

Поддержать разработчиков монетой здесь:
 https://bastyon.com/keep_alive_odessa?ref=PHQHKADhBPxxSwjiggV6G2BxSvy6TY1Lgb