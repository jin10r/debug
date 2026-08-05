# Developer Notes: WebView Validation

## Для разработчика, который первый раз видит этот код

### 🎯 Суть в двух словах

**Проблема:** Фронтенд должен работать только в Telegram WebView (production), но для локальной разработки нужен доступ из обычного браузера.

**Решение:** Два режима - strict (только Telegram) и dev (любой браузер).

---

## 🔍 Как это работает

### Frontend Flow (gate.js)

```javascript
// 1. Загружаем конфиг с сервера
const config = await fetch('/api/validation-config');
// {telegram_validation_enabled: true, redirect_url: "..."}

// 2. Проверяем webview
const isTelegram = window.Telegram?.WebApp?.initData;

// 3. Если strict + не Telegram → редирект
if (config.validation_enabled && !isTelegram) {
    window.location.replace(config.redirect_url);
    return;
}

// 4. Если Telegram → валидируем initData на бекенде
const response = await fetch('/api/validate-init', {
    body: JSON.stringify({init_data: window.Telegram.WebApp.initData})
});

// 5. Получаем JWT токены и загружаем app
const {access_token, refresh_token} = await response.json();
window.location.replace('/map.html');
```

### Backend Flow (auth.py)

```python
# POST /api/validate-init
async def validate_init_handler(request):
    data = await request.json()
    init_data = data.get('init_data')
    
    if not settings.telegram_validation_enabled:
        # Dev mode - accept anything
        return {"valid": True, "access_token": "..."}
    
    # Strict mode - validate HMAC signature
    is_valid, user = validate_telegram_webapp_data(
        init_data, 
        bot_token
    )
    
    if not is_valid:
        return {"valid": False}, 401
    
    # Generate JWT tokens
    return {
        "valid": True,
        "access_token": "...",
        "user": user
    }
```

---

## 🛠️ Локальная разработка

### 1. Отключить валидацию

```bash
# .env
TELEGRAM_VALIDATION_ENABLED=False
BOT_TOKEN=123456:ABC-DEF...
```

### 2. Запустить сервер

```bash
docker-compose up core
# или
python main.py
```

### 3. Открыть в браузере

```
http://localhost/
```

**Что произойдёт:**
1. `gate.js` проверит конфиг → validation disabled
2. Создаст dev токены автоматически
3. Загрузит `/map.html`
4. Всё работает как обычно

---

## 🔐 Production

### 1. Включить валидацию

```bash
# .env
TELEGRAM_VALIDATION_ENABLED=True
REDIRECT_URL=https://t.me/your_bot
BOT_TOKEN=real_bot_token
```

### 2. Открыть через Telegram

```
https://t.me/your_bot/app
```

**Что произойдёт:**
1. `gate.js` проверит webview → это Telegram
2. Отправит `initData` на бекенд
3. Бекенд проверит HMAC подпись
4. Выдаст JWT токены
5. Загрузит `/map.html`

**Если открыть в браузере:**
1. `gate.js` проверит webview → НЕ Telegram
2. Редирект на `REDIRECT_URL`
3. Приложение НЕ загружается

---

## 🧪 Тестирование

### Unit tests

```bash
pytest tests/test_webview_validation.py -v
```

### Manual testing

**Test 1: Dev mode**
```bash
export TELEGRAM_VALIDATION_ENABLED=False
docker-compose up
open http://localhost/
# Expected: работает в любом браузере
```

**Test 2: Strict mode + browser**
```bash
export TELEGRAM_VALIDATION_ENABLED=True
export REDIRECT_URL=https://google.com
docker-compose up
open http://localhost/
# Expected: редирект на google.com
```

**Test 3: Strict mode + Telegram**
```bash
# Открыть через Telegram бота
# Expected: работает, валидные токены
```

---

## 🐛 Debugging

### Frontend

**Browser Console:**
```javascript
// Должны быть такие логи:
[Gate] Config loaded: {validationEnabled: true, ...}
[Gate] Access granted
// или
[Gate] Access denied: not a Telegram WebView
[Gate] Redirecting to: ...
```

**Проверить токены:**
```javascript
console.log(sessionStorage.getItem('access_token'));
console.log(sessionStorage.getItem('user'));
```

### Backend

**Логи:**
```bash
docker logs core | grep "Gate\|Auth"
```

**Должны быть:**
```
[Auth] User authenticated: john_doe
```

**Или:**
```
[Auth] Strict mode: invalid initData signature
```

---

## 📁 Важные файлы

```
web/assets/js/gate.js          # Frontend validation gate
core/api/auth.py               # Backend validation
core/utils/telegram_validation.py  # HMAC validation
core/settings.py               # Configuration
```

### gate.js - Frontend gate

**Что делает:**
1. Проверяет webview (Telegram или нет)
2. В strict mode: редирект если не Telegram
3. Валидирует initData на бекенде
4. Сохраняет JWT токены
5. Загружает приложение

**Ключевые функции:**
- `isTelegramWebView()` - проверка webview
- `loadConfig()` - загрузка конфига
- `validateAndAuth()` - валидация на бекенде

### auth.py - Backend validation

**Что делает:**
1. `/api/validation-config` - отдаёт конфиг
2. `/api/validate-init` - валидирует initData
3. Генерирует JWT токены
4. Возвращает user data

**Ключевые функции:**
- `validate_init_handler` - главная валидация
- `validate_telegram_webapp_data` - HMAC проверка

---

## 💡 Tips & Tricks

### Быстро переключить режимы

```bash
# Dev mode
export TELEGRAM_VALIDATION_ENABLED=False && docker-compose restart core

# Strict mode
export TELEGRAM_VALIDATION_ENABLED=True && docker-compose restart core
```

### Посмотреть что приходит с Telegram

```javascript
// В browser console (внутри Telegram)
console.log(window.Telegram.WebApp.initData);
// query_id=...&user={"id":123}...&hash=...
```

### Проверить HMAC вручную

```python
import hmac, hashlib

# Secret key
secret_key = hmac.new(
    b"WebAppData",
    bot_token.encode(),
    hashlib.sha256
).digest()

# Hash
calculated = hmac.new(
    secret_key,
    data_check_string.encode(),
    hashlib.sha256
).hexdigest()

print(f"Calculated: {calculated}")
print(f"Expected: {hash_from_telegram}")
```

---

## ⚠️ Частые ошибки

### 1. Забыл отключить валидацию

**Симптом:** Редирект в локальной разработке

**Решение:**
```bash
export TELEGRAM_VALIDATION_ENABLED=False
```

### 2. Неправильный BOT_TOKEN

**Симптом:** `[Auth] Strict mode: invalid initData signature`

**Решение:**
```bash
# Проверить токен
echo $BOT_TOKEN
# Должен быть: 123456:ABC-DEF...
```

### 3. initData устарел

**Симптом:** `[Auth] initData expired: XXXs old`

**Решение:**
Закрыть и открыть заново через Telegram

---

## 🔗 Дополнительная информация

- [Полная документация](WEBVIEW_VALIDATION.md)
- [Быстрый старт](QUICK_START_WEBVIEW.md)
- [Telegram WebApp Docs](https://core.telegram.org/bots/webapps)
- [HMAC-SHA256 Validation](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)

---

## 🎓 Если что-то непонятно

1. Прочитать [QUICK_START_WEBVIEW.md](QUICK_START_WEBVIEW.md)
2. Запустить тесты: `pytest tests/test_webview_validation.py -v`
3. Посмотреть примеры в коде
4. Создать issue если проблема остаётся

---

**Happy coding! 🚀**
