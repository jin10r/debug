# Quick Start: WebView Validation

## 🚀 5 минут до запуска

### Production (Telegram только)

```bash
# 1. Настроить .env
cat > .env << EOF
TELEGRAM_VALIDATION_ENABLED=True
REDIRECT_URL=https://t.me/your_bot
BOT_TOKEN=123456:ABC-DEF...
POSTGRES_PASSWORD=secure_password
EOF

# 2. Запустить
docker-compose up -d

# 3. Проверить
curl http://localhost/api/validation-config
# {"telegram_validation_enabled": true, "redirect_url": "https://t.me/your_bot"}

# 4. Открыть через Telegram бота
# https://t.me/your_bot/app
```

### Development (любой браузер)

```bash
# 1. Настроить .env
cat > .env << EOF
TELEGRAM_VALIDATION_ENABLED=False
BOT_TOKEN=123456:ABC-DEF...
POSTGRES_PASSWORD=dev_password
EOF

# 2. Запустить
docker-compose up -d

# 3. Открыть в браузере
open http://localhost/
```

## ⚙️ Конфигурация

### Environment Variables

| Переменная | Production | Development | Описание |
|-----------|-----------|-------------|----------|
| `TELEGRAM_VALIDATION_ENABLED` | `True` | `False` | Строгая проверка webview |
| `REDIRECT_URL` | `https://t.me/bot` | не нужен | Куда редиректить |
| `BOT_TOKEN` | обязателен | обязателен | Токен от @BotFather |

### Режимы работы

#### ✅ Strict Mode (Production)
```bash
TELEGRAM_VALIDATION_ENABLED=True
```
- Доступ **только** из Telegram WebView
- Проверка HMAC-SHA256 подписи
- Автоматический редирект других источников

#### 🔧 Dev Mode (Development)
```bash
TELEGRAM_VALIDATION_ENABLED=False
```
- Доступ из **любого** браузера
- Автоматические dev токены
- **НЕ для production!**

## 🧪 Быстрая проверка

### Проверка конфигурации
```bash
curl http://localhost/api/validation-config | jq
```

### Проверка валидации (dev mode)
```bash
curl -X POST http://localhost/api/validate-init \
  -H "Content-Type: application/json" \
  -d '{"init_data": ""}' | jq
```

### Проверка логов
```bash
# Backend
docker logs core | grep "Gate\|Auth"

# Nginx
docker logs web | tail -20
```

## 📱 Настройка Telegram бота

### 1. Создать бота
```
Открыть @BotFather
/newbot
Следовать инструкциям
Скопировать BOT_TOKEN
```

### 2. Настроить Web App
```
/myapps
Выбрать бота
Настроить URL: https://your-domain.com
```

### 3. Протестировать
```
Открыть бота
Нажать кнопку с Web App
Проверить что загружается карта
```

## 🐛 Troubleshooting в 3 шага

### 1. Проверить переменные
```bash
echo "VALIDATION: $TELEGRAM_VALIDATION_ENABLED"
echo "REDIRECT: $REDIRECT_URL"
echo "BOT_TOKEN: ${BOT_TOKEN:0:10}..."
```

### 2. Проверить endpoint
```bash
curl http://localhost/api/validation-config
```

### 3. Проверить логи
```bash
docker logs core --tail 50 | grep -i "error\|auth"
```

## 📚 Документация

- Полная документация: [WEBVIEW_VALIDATION.md](./WEBVIEW_VALIDATION.md)
- Миграция: [MIGRATION_WEBVIEW.md](./MIGRATION_WEBVIEW.md)
- Тесты: `tests/test_webview_validation.py`

## 💡 Частые вопросы

**Q: Как открыть в браузере для разработки?**
```bash
export TELEGRAM_VALIDATION_ENABLED=False
docker-compose restart core
```

**Q: Почему редиректит на REDIRECT_URL?**
```
Причина: TELEGRAM_VALIDATION_ENABLED=True но открыто не в Telegram
Решение: Открыть через Telegram бота или отключить валидацию
```

**Q: Как проверить что валидация работает?**
```bash
# Должен вернуть 401 в strict mode
curl -X POST http://localhost/api/validate-init \
  -H "Content-Type: application/json" \
  -d '{"init_data": "fake_data"}'
```

**Q: Что делать если не работает?**
```bash
# 1. Отключить валидацию временно
export TELEGRAM_VALIDATION_ENABLED=False

# 2. Проверить логи
docker logs core --tail 100

# 3. Обратиться к TROUBLESHOOTING в WEBVIEW_VALIDATION.md
```

## ⚡ Команды для копипасты

### Полный рестарт
```bash
docker-compose down
docker-compose build
docker-compose up -d
docker logs -f core
```

### Проверка статуса
```bash
docker-compose ps
curl http://localhost/health
curl http://localhost/api/validation-config | jq
```

### Смотреть логи в реальном времени
```bash
docker logs -f core | grep -E "Gate|Auth|WS"
```
