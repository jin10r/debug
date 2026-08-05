# WebView Validation - Section for Main README

> Добавьте этот раздел в основной README.md проекта

---

## 🔒 Security & Access Control

### WebView Validation System

Приложение поддерживает два режима доступа:

#### Production Mode (Strict)
```bash
TELEGRAM_VALIDATION_ENABLED=True
REDIRECT_URL=https://t.me/your_bot
```
- ✅ Доступ **только** через Telegram WebView
- ✅ HMAC-SHA256 валидация initData
- ✅ Автоматический редирект других источников
- ✅ Проверка свежести данных (24 часа)

#### Development Mode
```bash
TELEGRAM_VALIDATION_ENABLED=False
```
- ✅ Доступ из любого браузера
- ✅ Автоматические dev токены
- ⚠️ **Только для локальной разработки!**

### Quick Start

**Development:**
```bash
# .env
TELEGRAM_VALIDATION_ENABLED=False
BOT_TOKEN=your_bot_token

# Запуск
docker-compose up -d

# Открыть в браузере
open http://localhost/
```

**Production:**
```bash
# .env
TELEGRAM_VALIDATION_ENABLED=True
REDIRECT_URL=https://t.me/your_bot
BOT_TOKEN=your_bot_token

# Запуск
docker-compose up -d

# Открыть через Telegram бота
# https://t.me/your_bot/app
```

### Как это работает

1. **Frontend проверка** (`gate.js`)
   - Определяет источник (Telegram vs другой webview)
   - В strict mode: редирект если не Telegram
   - В dev mode: разрешает любой доступ

2. **Backend валидация** (`/api/validate-init`)
   - Проверяет HMAC-SHA256 подпись
   - Выдаёт JWT токены при успехе
   - Отклоняет невалидные запросы

3. **WebSocket защита** (`/ws`)
   - Требует auth сообщение
   - Проверяет JWT или initData
   - Закрывает соединение при failure

### Troubleshooting

**"Доступ запрещён" в Telegram:**
```bash
# Проверить BOT_TOKEN
echo $BOT_TOKEN

# Проверить логи
docker logs core | grep Auth
```

**Редирект в dev mode:**
```bash
# Отключить валидацию
export TELEGRAM_VALIDATION_ENABLED=False
docker-compose restart core
```

### Документация

- 📖 [Полная документация](docs/WEBVIEW_VALIDATION.md)
- 🚀 [Быстрый старт](docs/QUICK_START_WEBVIEW.md)
- 📦 [Миграция](docs/MIGRATION_WEBVIEW.md)
- 🧪 [Тесты](tests/test_webview_validation.py)

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TELEGRAM_VALIDATION_ENABLED` | Yes | `True` | Включить строгую валидацию |
| `REDIRECT_URL` | If validation=True | - | URL для редиректа |
| `BOT_TOKEN` | Yes | - | Токен от @BotFather |

### Security Features

- ✅ HMAC-SHA256 signature verification
- ✅ Constant-time hash comparison
- ✅ Timestamp freshness check (24h)
- ✅ Circuit breaker for DDoS protection
- ✅ Early redirect before resource loading
- ✅ WebSocket authentication
- ✅ JWT token management

---

**См. также:**
- [Security Best Practices](docs/SECURITY.md)
- [API Documentation](docs/API.md)
- [Deployment Guide](docs/DEPLOYMENT.md)
