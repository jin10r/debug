# 🚀 Quick Start: Telegram WebApp Authentication

## 📋 Краткая справка

Система аутентификации для Telegram Mini Apps с JWT токенами.

---

## ⚡ Быстрый старт

### 1. Настройка .env

```env
# ОБЯЗАТЕЛЬНО для production
BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_VALIDATION_ENABLED=true
REDIRECT_URL=https://t.me/your_bot

# ОПЦИОНАЛЬНО (для multi-replica)
JWT_SECRET=your-strong-secret-min-32-chars-long
```

### 2. Запуск

```bash
docker-compose up -d core
```

### 3. Проверка

```bash
# Логи аутентификации
docker-compose logs -f core | grep "\[Auth\]"

# Проверка health
curl http://localhost:8080/health
```

---

## 🔑 Основные компоненты

| Компонент | Файл | Назначение |
|-----------|------|------------|
| Telegram Validation | `core/utils/telegram_validation.py` | HMAC-SHA256 проверка initData |
| JWT Tokens | `core/middlewares/auth.py` | Генерация/верификация JWT |
| API Endpoints | `core/api/auth.py` | /api/validate-init, /api/auth/refresh |
| JWT Middleware | `core/middlewares/jwt_auth.py` | Защита всех API |
| Configuration | `core/settings.py` | Настройки системы |

---

## 🌐 API Endpoints

### POST /api/validate-init
**Аутентификация пользователя**

Request:
```json
{"init_data": "query_id=...&user=...&hash=..."}
```

Response:
```json
{
  "valid": true,
  "user": {"id": 123, "first_name": "John"},
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "expires_in": 900
}
```

### POST /api/auth/refresh
**Обновление Access Token**

Request:
```json
{"refresh_token": "eyJ..."}
```

Response:
```json
{
  "access_token": "eyJ...",
  "expires_in": 900
}
```

### GET /api/validation-config
**Конфигурация для фронтенда**

Response:
```json
{
  "telegram_validation_enabled": true,
  "redirect_url": "https://t.me/your_bot"
}
```

---

## 🔐 Токены

### Access Token (15 минут)
```json
{
  "sub": "123456789",
  "first_name": "John",
  "username": "johndoe",
  "iat": 1234567890,
  "exp": 1234568790,
  "type": "access"
}
```

### Refresh Token (24 часа)
```json
{
  "sub": "123456789",
  "iat": 1234567890,
  "exp": 1234654290,
  "type": "refresh"
}
```

---

## 🛡️ Защита API

Все endpoints защищены JWT Middleware, кроме:
- `/health/*` - health checks
- `/api/validation-config` - конфигурация
- `/api/validate-init` - аутентификация
- `/api/auth/refresh` - refresh токенов

**Использование:**
```bash
# С JWT токеном
curl -H "Authorization: Bearer eyJ..." http://localhost:8080/api/events

# Без токена → 401 UNAUTHORIZED
curl http://localhost:8080/api/events
```

---

## 🧪 Тестирование

### Файл: test_telegram_flow.html

```bash
# Открыть в Telegram WebView
https://your-domain.com/test_telegram_flow.html
```

### Проверка в браузере:

```javascript
// DevTools Console
console.log(window.Telegram?.WebApp?.initData);
console.log(sessionStorage.getItem('access_token'));
```

---

## 📊 Мониторинг

### Логи

```bash
# Все логи аутентификации
docker-compose logs core | grep -E "\[Auth\]|\[JWT\]|\[Gate\]"

# Только ошибки
docker-compose logs core | grep -E "ERROR|WARNING" | grep Auth

# Circuit breaker
docker-compose logs core | grep "Circuit"
```

### Метрики

- ✅ Успешные аутентификации: `[Auth] User authenticated`
- ❌ Неуспешные попытки: `[Auth] invalid initData`
- 🔄 Circuit breaker: `CircuitBreaker state`
- 💾 JWT cache: hit/miss rate

---

## 🐛 Troubleshooting

### Ошибка: "Hash mismatch"
```bash
# Проверить BOT_TOKEN
grep BOT_TOKEN .env

# Проверить логи
docker-compose logs core | grep "Hash mismatch" -A 5
```

### Ошибка: "Invalid token"
```bash
# Проверить время сервера
docker-compose exec core date

# Декодировать JWT
echo "eyJhbGci..." | base64 -d
```

### Ошибка: "Circuit breaker OPEN"
```bash
# Подождать 30 секунд (auto-recovery)
sleep 30

# Проверить логи
docker-compose logs core | grep "CircuitBreaker"
```

### Frontend не открывается
```bash
# 1. Проверить настройки
grep TELEGRAM_VALIDATION_ENABLED .env

# 2. Dev mode (без валидации)
echo "TELEGRAM_VALIDATION_ENABLED=false" >> .env
docker-compose restart core

# 3. Проверить логи браузера
# DevTools → Console → [Gate]
```

---

## 📚 Полная документация

- **AUTHENTICATION_ARCHITECTURE.md** - архитектура системы
- **SPEC_COMPLIANCE_CHECKLIST.md** - соответствие спецификации
- **FINAL_AUTHENTICATION_SUMMARY.md** - полный отчёт
- **WEBVIEW_FIX_SUMMARY.md** - исправления WebView

---

## 🔧 Режимы работы

### Production (строгий)
```env
TELEGRAM_VALIDATION_ENABLED=true
BOT_TOKEN=your_real_token
REDIRECT_URL=https://t.me/your_bot
```

### Development (без проверки)
```env
TELEGRAM_VALIDATION_ENABLED=false
```

---

## ⚙️ Конфигурация

| Параметр | Default | Описание |
|----------|---------|----------|
| `jwt_access_ttl` | 900 | Access Token TTL (сек) |
| `jwt_refresh_ttl` | 86400 | Refresh Token TTL (сек) |
| `jwt_algorithm` | HS256 | Алгоритм подписи |
| Circuit Breaker fail_max | 5 | Ошибок до открытия |
| Circuit Breaker reset | 30 | Восстановление (сек) |
| JWT Cache size | 10000 | Записей в LRU кэше |
| JWT Cache TTL | 10 | TTL кэша (сек) |
| initData max_age | 24 | Свежесть initData (часы) |

---

## 💡 Best Practices

### Frontend:
1. ✅ Сохранять токены в `sessionStorage` (не `localStorage`)
2. ✅ Проверять `Telegram.WebApp.initData` в 3 источниках
3. ✅ Использовать `Authorization: Bearer <token>` для API
4. ✅ Обновлять токен через `/api/auth/refresh` при 401

### Backend:
1. ✅ Всегда логировать попытки аутентификации
2. ✅ Использовать Circuit Breaker для защиты
3. ✅ Проверять `request['telegram_user']` в handlers
4. ✅ Мониторить JWT cache hit rate

### Security:
1. ✅ Использовать HTTPS в production
2. ✅ Не логировать полные токены/секреты
3. ✅ Ротировать JWT_SECRET при утечке
4. ✅ Мониторить аномальные паттерны аутентификации

---

## 🎯 Checklist деплоя

- [ ] BOT_TOKEN установлен в .env
- [ ] REDIRECT_URL настроен
- [ ] TELEGRAM_VALIDATION_ENABLED=true
- [ ] HTTPS настроен (production)
- [ ] Логи мониторятся
- [ ] Алерты настроены на ошибки
- [ ] test_telegram_flow.html протестирован
- [ ] JWT_SECRET установлен (multi-replica)

---

## 📞 Support

**Документация:**
- Telegram WebApp: https://core.telegram.org/bots/webapps
- JWT: https://jwt.io/

**Логи:**
```bash
docker-compose logs -f core
```

**Статус:**
```bash
curl http://localhost:8080/health/detailed
```

---

**Статус:** ✅ Ready for Production  
**Версия:** 1.0  
**Дата:** 2026-08-02
