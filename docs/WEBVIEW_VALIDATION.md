# WebView Validation System

## Обзор

Система проверки доступа к фронтенду на основе webview источника. Проверка выполняется **ДО загрузки основного приложения**.

## Режимы работы

### Строгий режим (Production)
```bash
TELEGRAM_VALIDATION_ENABLED=True
REDIRECT_URL=https://t.me/your_bot
```

**Правила:**
- ✅ Доступ **ТОЛЬКО** через Telegram WebView
- ❌ Любой другой webview → редирект на `REDIRECT_URL`
- ✅ Обязательная валидация `initData` с HMAC-SHA256 проверкой
- ✅ Проверка свежести данных (максимум 24 часа)

### Режим разработки (Development)
```bash
TELEGRAM_VALIDATION_ENABLED=False
```

**Правила:**
- ✅ Доступ из **любого** webview
- ✅ Если открыто в Telegram → валидация `initData`
- ✅ Если открыто вне Telegram → автоматически создаются dev-токены
- ⚠️ **НИКОГДА не используйте в production!**

## Архитектура

### Frontend (`web/assets/js/gate.js`)

```javascript
// 1. Загрузка конфигурации с сервера
const config = await loadConfig();

// 2. Проверка webview ДО любых действий
const isTelegramWebView = isTelegramWebView();

// 3. Если strict mode + не Telegram → редирект
if (config.telegram_validation_enabled && !isTelegramWebView) {
    redirectTo(config.redirect_url);
    return;
}

// 4. Если Telegram → валидация initData на бекенде
if (isTelegramWebView) {
    const initData = window.Telegram.WebApp.initData;
    await validateAndAuth(initData);
}
```

**Критично:** Проверка webview выполняется **перед** загрузкой `map.html` и любых других ресурсов.

### Backend (`core/api/auth.py`)

#### GET `/api/validation-config`
Возвращает конфигурацию безопасности:
```json
{
    "telegram_validation_enabled": true,
    "redirect_url": "https://t.me/your_bot"
}
```

#### POST `/api/validate-init`
Валидирует `initData` и выдаёт JWT токены:

**Request:**
```json
{
    "init_data": "query_id=...&user=...&auth_date=...&hash=..."
}
```

**Response (success):**
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

**Response (failure):**
```json
{
    "valid": false,
    "error": "Invalid or expired init_data"
}
```

### Валидация initData

#### Алгоритм (HMAC-SHA256)

1. Извлечь `hash` из `initData`
2. Построить `data_check_string` из остальных параметров (URL-encoded)
3. Вычислить `secret_key = HMAC-SHA256("WebAppData", bot_token)`
4. Вычислить `calculated_hash = HMAC-SHA256(secret_key, data_check_string)`
5. Сравнить `calculated_hash` с `hash` (constant-time)
6. Проверить `auth_date` (макс. 24 часа)

#### Circuit Breaker
- Открывается после 5 ошибок
- Восстанавливается через 30 секунд
- Защита от перегрузки при проблемах с валидацией

## WebSocket Authentication

WebSocket соединения также защищены:

```javascript
// Клиент отправляет auth сообщение
ws.send(JSON.stringify({
    type: 'auth',
    token: access_token,  // JWT токен
    init_data: initData   // или initData
}));
```

**Сервер проверяет:**
1. Если `telegram_validation_enabled=true` → требует валидный JWT или initData
2. Если `telegram_validation_enabled=false` → пропускает любые соединения (dev)

## Конфигурация

### Environment Variables

```bash
# Включить/выключить строгую валидацию
TELEGRAM_VALIDATION_ENABLED=True

# URL для редиректа неавторизованных пользователей
REDIRECT_URL=https://t.me/your_bot

# Bot token для валидации initData (обязателен если validation=True)
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
```

### Рекомендации для production

```bash
# Всегда включайте валидацию
TELEGRAM_VALIDATION_ENABLED=True

# Укажите правильный redirect URL
REDIRECT_URL=https://t.me/your_actual_bot

# Никогда не коммитьте BOT_TOKEN
BOT_TOKEN=${BOT_TOKEN}  # из secrets manager
```

## Security Features

### ✅ Защита от несанкционированного доступа
- Только Telegram WebView в strict mode
- HMAC-SHA256 валидация всех запросов
- Constant-time hash сравнение (защита от timing attacks)

### ✅ Защита от replay attacks
- Проверка `auth_date` (максимум 24 часа)
- Каждый `initData` уникален для сессии

### ✅ Защита от tampering
- HMAC подпись всех параметров
- Любое изменение → провал валидации

### ✅ Rate limiting & Circuit breaking
- Circuit breaker для validator
- Protection от перегрузки

## Тестирование

### Development mode
```bash
export TELEGRAM_VALIDATION_ENABLED=False
npm run dev
# Открыть в любом браузере: http://localhost:3000
```

### Production mode (local)
```bash
export TELEGRAM_VALIDATION_ENABLED=True
export REDIRECT_URL=https://google.com
export BOT_TOKEN=your_test_token
npm run dev
# Открыть НЕ в Telegram → редирект на google.com
```

### Telegram WebView test
1. Создать bot в @BotFather
2. Настроить Web App URL
3. Открыть через Telegram
4. Проверить логи: `[Gate] Access granted`

## Troubleshooting

### "Доступ запрещён" в Telegram WebView

**Причины:**
1. ❌ Неверный `BOT_TOKEN` в `.env`
2. ❌ `initData` устарел (>24 часа)
3. ❌ Проблемы с HMAC валидацией

**Решение:**
```bash
# Проверить токен
echo $BOT_TOKEN

# Проверить логи backend
docker logs core | grep "Auth"

# Проверить frontend console
# Должно быть: [Gate] Access granted
```

### Редирект в dev mode

**Причина:** `TELEGRAM_VALIDATION_ENABLED=True` но открыто не в Telegram

**Решение:**
```bash
export TELEGRAM_VALIDATION_ENABLED=False
```

### Circuit breaker открыт

**Причина:** Много ошибок валидации подряд (>5)

**Решение:**
1. Подождать 30 секунд (auto-reset)
2. Проверить `BOT_TOKEN`
3. Проверить connectivity к backend

## Миграция с предыдущей версии

### Изменения в frontend

**Было:**
```javascript
// Валидация ПОСЛЕ загрузки app
if (config.telegram_validation_enabled) {
    validate();
}
```

**Стало:**
```javascript
// Валидация ДО загрузки app
if (config.telegram_validation_enabled && !isTelegramWebView) {
    redirectTo(config.redirect_url);  // Немедленный редирект
    return;
}
```

### Изменения в backend

**Было:**
```python
# Слабая проверка
if not settings.app.telegram_validation_enabled:
    user_data = {"id": "123"}  # Всегда успех
```

**Стало:**
```python
# Строгая проверка
if not init_data:
    return web.json_response({'valid': False}, status=401)

is_valid, user_data = validate_telegram_webapp_data(init_data, bot_token)
if not is_valid:
    return web.json_response({'valid': False}, status=401)
```

## API Reference

### `/api/validation-config` - GET
Получить конфигурацию валидации

**Response:** `200 OK`
```json
{
    "telegram_validation_enabled": boolean,
    "redirect_url": string | null
}
```

### `/api/validate-init` - POST
Валидировать initData и получить токены

**Request:**
```json
{
    "init_data": string
}
```

**Response:** `200 OK` | `401 Unauthorized` | `500 Internal Server Error`

### WebSocket `/ws` - Auth message
```json
{
    "type": "auth",
    "token": string,      // JWT access token (опционально)
    "init_data": string   // Telegram initData (опционально)
}
```

**Response:**
```json
{
    "type": "auth_ok"
}
```
или
```json
{
    "type": "error",
    "message": "authentication failed"
}
```

## Логи

### Успешная аутентификация
```
[Gate] Config loaded: {validationEnabled: true, redirectUrl: "..."}
[Gate] Access granted
[Auth] User authenticated: john_doe
```

### Блокировка доступа
```
[Gate] Access denied: not a Telegram WebView
[Gate] Redirecting to: https://t.me/your_bot
```

### Ошибка валидации
```
[Auth] Strict mode: invalid initData signature or expired
[Gate] Backend validation failed: 401
```
