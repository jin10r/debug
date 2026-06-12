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

Хост — Linux или macOS с `bash` (Windows — через WSL2). Нужны:

- **Docker** + **Docker Compose v2** — рантайм всего стека.
- **Git** — клонирование репозитория.
- **Python 3.10+** с `pip` и `venv` на хосте — только для одноразовой генерации
  Telegram-сессии (шаг 2); в рантайме приложения не используется.
- **Telegram-аккаунт, подписанный на целевой канал.** Парсер читает канал под
  **пользовательской** сессией (не под ботом); без подписки Telegram не отдаст
  сообщения — и событий на карте не будет.
- **Бот от [@BotFather](https://t.me/BotFather)** (`BOT_TOKEN`) — **обязателен**: в
  сервисе `core` работает aiogram-бот, без валидного токена `core` не стартует.
- **Только для production Mini App:** публичный домен с **HTTPS** — Telegram
  открывает WebApp лишь по `https://` (см. раздел 5, вариант B).

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

> **Важно.** Авторизованный аккаунт должен быть **подписан на целевой канал**
> (`CHANNEL_ID` в [core/settings.py](core/settings.py)). Без подписки Telegram не
> отдаст историю и новые сообщения — парсер не увидит события. Подпишитесь этим
> аккаунтом на канал до запуска стека.

### 3. Конфигурация `.env`

```bash
cp env.example .env
```

**Минимум для старта — вписать реальный `BOT_TOKEN`** (без валидного токена `core`
не поднимется). `env.example` содержит только секреты и per-deployment URL;
остальное захардкожено дефолтами в [core/settings.py](core/settings.py):

| Переменная                    | Обяз. | Описание                                            |
|-------------------------------|-------|-----------------------------------------------------|
| `BOT_TOKEN`                   | да    | токен бота от @BotFather; без него `core` (aiogram-бот) не стартует |
| `WEBAPP_URL`                  | для prod | публичный HTTPS-URL приложения; бот вставляет его в кнопку «Открыть приложение» и он же задаётся в @BotFather. Для локальной проверки не нужен |
| `TELEGRAM_VALIDATION_ENABLED` | нет   | `True` (дефолт) — доступ только из Telegram (Mini App); `False` — открытый доступ для локальной проверки. **Пустым не оставлять** |
| `REDIRECT_URL`                | нет   | куда отправлять не-Telegram трафик при включённой валидации |
| `JWT_SECRET`                  | нет   | автогенерируется в памяти при старте; задать только для стабильного/общего секрета (≥32 символов) |

Как именно открыть приложение (локально в браузере или как Telegram Mini App) —
см. раздел 5 ниже.

### 4. Запуск

```bash
docker compose up -d --build
```

Фронтенд собирается внутри `Dockerfile.web` (node-builder → `nginx:alpine`),
отдельный `npm run build` не нужен. Порядок готовности:
`postgres → parser/core → web`. Стек слушает `http://<host>:80/`, но как открыть
само приложение — см. раздел 5 (по умолчанию доступ только из Telegram).

Проверка, что стек поднялся:

```bash
docker compose ps                       # все сервисы healthy
curl -fsS http://localhost/health/ready # 200 OK
docker compose logs -f parser           # «Telegram client started», обработка сообщений
```

### 5. Открытие приложения: локально (dev) или как Mini App (prod)

Стек поднимается одинаково, но «увидеть карту» можно двумя путями. По умолчанию
API и WebSocket пускают **только трафик из Telegram** — в обычном браузере карта
будет пустой/редиректнет.

#### Вариант A — локальная проверка в браузере (dev)

1. В `.env`: `TELEGRAM_VALIDATION_ENABLED=False`.
2. `docker compose up -d --build` (или `docker compose restart core`, если стек уже запущен).
3. Откройте <http://localhost/> на той же машине — карта с событиями.

> ⚠️ В этом режиме авторизация выключена — это **только для локальной отладки**.
> Не выставляйте такой стек в интернет.

#### Вариант B — публичный Telegram Mini App (production)

Telegram открывает WebApp только по HTTPS, а контейнер `web` отдаёт HTTP на `:80` —
поэтому перед ним нужен HTTPS-фронт.

1. **Поднимите HTTPS к `web:80`.** Без своего домена/проброса портов проще всего —
   [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
   (`cloudflared`, бесплатно, выдаёт адрес `https://…`). Классика со своим доменом —
   reverse-proxy с авто-TLS ([Caddy](https://caddyserver.com/) или nginx + certbot),
   проксирующий на `web:80`.
2. В `.env`: `TELEGRAM_VALIDATION_ENABLED=True` (дефолт) и `WEBAPP_URL=https://<ваш-домен>`.
3. Перезапустите: `docker compose up -d --build`.
4. В [@BotFather](https://t.me/BotFather): `/mybots` → ваш бот → **Bot Settings →
   Configure Mini App** → укажите URL `https://<ваш-домен>` (по желанию — тот же URL
   на **Menu Button**).
5. Откройте бота в Telegram, отправьте `/start` → кнопка «🌐 Открыть приложение»
   запустит карту. `initData` проверяется по `BOT_TOKEN`.

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