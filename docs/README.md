# 📚 Документация проекта pg_kimi

**Версия:** 2.0.0  
**Дата обновления:** 2026-03-13

---

## 🗂️ Навигация по документации

### Основная документация
- [📊 Архитектурный отчет](ARCHITECTURE_REPORT.md) - полный анализ архитектуры
- [🔧 Исправления валидации Telegram](TELEGRAM_VALIDATION_FIXES.md) - последние исправления
- [📡 API документация](API.md) - REST API reference

### Быстрый старт
- [Установка и запуск](#установка-и-запуск)
- [Конфигурация](#конфигурация)
- [Разработка](#разработка)

---

## 🚀 Быстрый старт

### Требования
- Docker 20+
- Docker Compose 2.0+
- 2GB RAM минимум
- PostgreSQL 16 с PostGIS 3

### Установка

```bash
# 1. Клонируйте репозиторий
git clone <repository_url>
cd pg_kimi

# 2. Скопируйте .env.example
cp .env.example .env

# 3. Отредактируйте .env
# Обязательно измените:
# - BOT_TOKEN
# - CHANNEL_ID
# - JWT_SECRET (минимум 32 символа)

# 4. Запустите сервисы
docker-compose up -d

# 5. Проверьте логи
docker-compose logs -f app

# 6. Откройте в браузере
# http://localhost
```

### Переменные окружения

**Обязательные:**
```env
# Telegram Bot
BOT_TOKEN=123456:ABC-DEF...
CHANNEL_ID=-1001234567890

# Database
POSTGRES_PASSWORD=secure_password

# JWT (обязательно измените в production!)
JWT_SECRET=your-secret-key-min-32-characters-long

# Redis (опционально, но рекомендуется)
REDIS_PASSWORD=secure_redis_password
```

**Опциональные:**
```env
# Режим валидации
TELEGRAM_VALIDATION_ENABLED=true

# Редирект при неудачной валидации
REDIRECT_URL=https://github.com/404

# Настройки карты
MAP_CENTER_LAT=46.4825
MAP_CENTER_LNG=30.7233
MAP_DEFAULT_ZOOM=10

# Feature flags
ENABLE_RANDOM_POINTS=true
```

---

## 🏗️ Архитектура

### Компоненты

```
┌─────────────┐
│   NGINX     │ (80/443 - reverse proxy, статика)
└──────┬──────┘
       │
   ┌───┴────┐
   │        │
┌──▼──┐  ┌──▼────┐
│ App │  │Parser │ (aiohttp + bot)  (Pyrogram)
└──┬──┘  └──┬────┘
   │        │
┌──▼────────▼────┐
│  PostgreSQL    │ (PostGIS - события, улицы)
└────────────────┘
┌────────────────┐
│    Redis       │ (кэш, сессии, replay protection)
└────────────────┘
```

### Сервисы

| Сервис | Порт | Описание |
|--------|------|----------|
| **nginx** | 80 | Reverse proxy, статика |
| **app** | 8080 | Основное приложение (aiohttp) |
| **parser** | - | Парсер Telegram канала |
| **postgres** | 5432 | PostgreSQL + PostGIS |
| **redis** | 6379 | Redis cache |

---

## 📡 API Reference

### Аутентификация

#### `POST /api/validation-config`
Получить конфигурацию валидации.

**Response:**
```json
{
  "telegram_validation_enabled": true,
  "redirect_url": "https://github.com/404"
}
```

#### `POST /api/validate-init`
Валидация Telegram initData.

**Request:**
```json
{
  "init_data": "query_string_from_telegram"
}
```

**Response (успех):**
```json
{
  "valid": true,
  "user": {
    "id": 123456789,
    "first_name": "John",
    "username": "john_doe"
  },
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "expires_in": 900
}
```

#### `POST /api/auth/refresh`
Обновить access token.

**Request:**
```json
{
  "refresh_token": "eyJ..."
}
```

---

### События

#### `GET /api/events`
Получить snapshot событий.

**Query parameters:**
- `limit` (default: 5000)

**Response:** GeoJSON FeatureCollection

#### `GET /api/events/status`
Получить метаданные событий.

**Response:**
```json
{
  "version": 42,
  "max_event_id": 1234,
  "updated_at": "2026-03-13T10:30:00Z"
}
```

#### `POST /api/events/updates`
Инкрементальные обновления.

**Request:**
```json
{
  "after_id": 1000,
  "limit": 2000
}
```

---

### Локации

#### `POST /api/location`
Поиск локации по тексту.

**Request:**
```json
{
  "query": "Жукова и Восточная"
}
```

#### `POST /api/location/batch`
Пакетный поиск.

---

### Кэш

#### `POST /api/cache/manifest`
Manifest с хэшами файлов.

#### `POST /api/cache/status`
Статус кэша.

#### `POST /api/cache/check_update`
Проверка обновлений.

---

### Health Checks

| Endpoint | Описание |
|----------|----------|
| `GET /health` | Basic health |
| `GET /health/live` | Liveness probe |
| `GET /health/ready` | Readiness probe |
| `GET /health/detailed` | Детальный статус |

---

## 🔐 Безопасность

### Аутентификация

1. **Telegram HMAC-SHA256** - валидация initData
2. **JWT токены** - access (15 мин) + refresh (7 дней)
3. **Replay protection** - Redis hash tracking

### Rate Limiting

| Endpoint | Лимит |
|----------|-------|
| `/api/events` | 120 req/min |
| `/api/streets` | 30 req/min |
| `/api/location` | 60 req/min |
| Остальные | 60 req/min |

### Security Headers

```
X-Frame-Options: SAMEORIGIN
X-Content-Type-Options: nosniff
Referrer-Policy: same-origin
Content-Security-Policy: default-src 'self'
```

---

## 🧪 Тестирование

### Запуск тестов

```bash
# Все тесты
pytest -v

# С покрытием
pytest --cov=core --cov-report=html

# Только unit
pytest -m unit

# Только integration
pytest -m integration
```

### Структура тестов

```
tests/
├── test_api_auth.py          # Auth API тесты
├── test_telegram_validation.py # HMAC валидация
├── test_cache.py             # Кэш тесты
├── test_db_events.py         # DB события
├── test_db_location.py       # DB локации
├── test_location_service.py  # Location service
├── test_message_processor.py # Parser логика
├── test_ratelimit.py         # Rate limiting
└── test_settings.py          # Конфигурация
```

---

## 🛠️ Разработка

### Локальная разработка

```bash
# Без Docker (требуется PostgreSQL и Redis)
pip install -r requirements.txt
python main.py

# С Docker (hot reload)
docker-compose up -d redis postgres
python main.py
```

### Frontend разработка

```bash
cd web

# TypeScript компиляция
npx tsc --watch

# Или просто откройте map.html в браузере
# (для отладки без Telegram)
```

### Режимы валидации

**Development:**
```env
TELEGRAM_VALIDATION_ENABLED=false
```
- Валидация отключена
- Тестовый пользователь
- Redis не требуется

**Production:**
```env
TELEGRAM_VALIDATION_ENABLED=true
REDIRECT_URL=https://github.com/404
```
- Строгая HMAC валидация
- Redis replay protection
- Редирект при ошибке

---

## 📊 Мониторинг

### Prometheus метрики

**Доступ к метрикам:**
```bash
# Через API
curl http://localhost:8080/metrics

# Через Docker
docker-compose exec app curl http://localhost:8080/metrics
```

**Основные метрики:**
- `http_requests_total` - HTTP запросы
- `http_request_duration_seconds` - длительность
- `db_pool_size` - пул БД
- `db_query_duration_seconds` - запросы БД
- `cache_hits_total` - попадания кэша

### Grafana Dashboard

Импортируйте dashboard из `docs/grafana_dashboard.json` (если есть).

---

## 🐛 Troubleshooting

### Parser не подключается: Session file not found

**Проблема:** Ошибка `Session file not found` или `pyrogram.errors.exceptions.badrequest_400.ApiIdPublishedFlood`

**Решение:**

#### Шаг 1: Получите API credentials

1. Перейдите на https://my.telegram.org
2. Войдите по номеру телефона
3. Перейдите в **API development tools**
4. Создайте новое приложение
5. Скопируйте `api_id` (число) и `api_hash` (строка)

#### Шаг 2: Создайте файл сессии

Создайте временный скрипт в корне проекта:

```bash
cat > create_session.py << 'EOF'
import asyncio
from pyrogram import Client

async def main():
    # Замените на ваши credentials из my.telegram.org
    app = Client(
        "my_session",
        api_id=YOUR_API_ID,          # Замените на ваш api_id
        api_hash="YOUR_API_HASH"     # Замените на ваш api_hash
    )
    
    async with app:
        print("✅ Сессия создана успешно!")
        print(f"Файл сессии: my_session.session")

asyncio.run(main())
EOF
```

Запустите:

```bash
pip install pyrogram
python create_session.py
```

**Вам будет предложено:**
1. Ввести номер телефона (с кодом страны, например `+380XXXXXXXXX`)
2. Ввести код подтверждения из Telegram
3. Ввести 2FA пароль (если включен)

После успешной аутентификации будет создан файл `my_session.session`.

#### Шаг 3: Скопируйте сессию в директорию парсера

```bash
cp my_session.session parser/session.session
```

**Важно:** Файл должен называться точно `session.session` и находиться в директории `parser/`.

#### Шаг 4: Проверьте сессию

```bash
ls -lh parser/session.session
```

Ожидаемый размер: 5-15 KB

#### ⚠️ Примечания по безопасности

- **Никогда не коммитьте** `session.session` в Git (уже в `.gitignore`)
- **Сделайте бэкап** файла сессии в безопасном месте
- Если сессия удалена или истекла, повторите процесс
- Сессия привязана к вашему аккаунту Telegram

#### Шаг 5: Перезапустите парсер

```bash
docker-compose restart parser
docker-compose logs -f parser
```

### Redis недоступен

**Проблема:** Ошибка `Redis connection failed`

**Решение:**
```bash
# Проверьте Redis
docker-compose ps redis

# Перезапустите
docker-compose restart redis

# Проверьте логи
docker-compose logs redis
```

### WebSocket не подключается

**Проблема:** Ошибка подключения к `/ws`

**Решение:**
1. Проверьте nginx конфиг
2. Убедитесь, что `proxy_set_header Upgrade` настроен
3. Проверьте логи app

```bash
docker-compose logs app | grep WebSocket
```

### JWT токен истёк

**Проблема:** 401 Unauthorized

**Решение:**
```javascript
// Frontend автоматически refresh через token-manager.js
const token = await window.tokenManager.getValidToken();

// Или вручную
const response = await fetch('/api/auth/refresh', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({refresh_token: storedRefreshToken})
});
```

---

## 📝 Changelog

### 2.0.0 (2026-03-13)

**Исправления безопасности:**
- ✅ Исправлена уязвимость WebSocket auth (initData не в URL)
- ✅ Добавлен Redis replay protection
- ✅ Унифицирована HMAC валидация
- ✅ Добавлен JWT token manager

**Улучшения:**
- ✅ Модульная архитектура frontend
- ✅ TypeScript поддержка
- ✅ Auto-refresh токенов
- ✅ Graceful shutdown

**Известные проблемы:**
- ⚠️ Redis обязателен для production
- ⚠️ Нет E2E тестов

---

## 📞 Контакты

- **Документация:** `docs/`
- **Тесты:** `tests/`
- **API:** `/api/*`
- **Health:** `/health`

---

*Документация обновлена: 2026-03-13*
