# Migration Guide: WebView Validation

## Что изменилось

### 🔒 Безопасность усилена

**До:**
- Проверка webview была опциональной
- Валидация initData выполнялась слабо
- Можно было обойти через прямой доступ к `/map.html`

**После:**
- Проверка webview выполняется **ДО** загрузки приложения
- Строгая HMAC-SHA256 валидация initData
- Два режима: строгий (production) и dev (разработка)

## Шаги миграции

### 1. Обновить файлы

```bash
# Обновлены следующие файлы:
# - web/assets/js/gate.js (frontend gate)
# - core/api/auth.py (backend validation)
# - core/api/routes.py (routes)
# - core/api/websocket.py (WS auth)
```

### 2. Настроить environment variables

#### Production (строгий режим)
```bash
# .env
TELEGRAM_VALIDATION_ENABLED=True
REDIRECT_URL=https://t.me/your_bot
BOT_TOKEN=123456:ABC-DEF...  # от @BotFather
```

#### Development (dev режим)
```bash
# .env.local
TELEGRAM_VALIDATION_ENABLED=False
# REDIRECT_URL не нужен в dev mode
BOT_TOKEN=123456:ABC-DEF...  # всё равно нужен для других фич
```

### 3. Проверить конфигурацию

```bash
# Запустить сервер
docker-compose up core

# Проверить endpoint
curl http://localhost:8080/api/validation-config

# Ожидаемый ответ (production):
# {
#   "telegram_validation_enabled": true,
#   "redirect_url": "https://t.me/your_bot"
# }
```

### 4. Тестирование

#### Тест 1: Dev mode в браузере
```bash
export TELEGRAM_VALIDATION_ENABLED=False
docker-compose up

# Открыть в браузере: http://localhost/
# Ожидается: успешная загрузка, dev токены
```

#### Тест 2: Strict mode в браузере
```bash
export TELEGRAM_VALIDATION_ENABLED=True
export REDIRECT_URL=https://google.com
docker-compose up

# Открыть в браузере: http://localhost/
# Ожидается: редирект на google.com через 1 секунду
```

#### Тест 3: Strict mode в Telegram
1. Настроить бота в @BotFather
2. Добавить Web App URL
3. Открыть через Telegram
4. Ожидается: успешная загрузка, токены из initData

### 5. Проверить логи

#### Frontend (browser console)
```
[Gate] Config loaded: {validationEnabled: true, redirectUrl: "..."}
[Gate] Access granted
```

или

```
[Gate] Access denied: not a Telegram WebView
[Gate] Redirecting to: https://t.me/your_bot
```

#### Backend (docker logs core)
```
[Auth] User authenticated: john_doe
```

или

```
[Auth] Strict mode: invalid initData signature or expired
```

## Обратная совместимость

### ❌ Ломающие изменения

1. **Endpoint `/api/validation-config`**
   - Было: `POST`
   - Стало: `GET` (POST метод удалён)
   
   **Решение:** Обновить frontend на новую версию `gate.js`

2. **Strict mode по умолчанию**
   - Было: по умолчанию разрешён любой доступ
   - Стало: по умолчанию только Telegram WebView
   
   **Решение:** Явно задать `TELEGRAM_VALIDATION_ENABLED=False` для dev

### ✅ Сохранена совместимость

- JWT токены остаются прежними
- WebSocket аутентификация работает как раньше
- Все API endpoints не изменились (кроме `/api/validation-config`)

## Rollback план

Если возникли проблемы, можно откатиться:

### Вариант 1: Отключить валидацию
```bash
export TELEGRAM_VALIDATION_ENABLED=False
docker-compose restart core
```

### Вариант 2: Вернуться к старой версии
```bash
git checkout <previous_commit>
docker-compose down
docker-compose build
docker-compose up
```

## Troubleshooting

### Проблема: "Доступ запрещён" в Telegram

**Диагностика:**
```bash
# Проверить BOT_TOKEN
echo $BOT_TOKEN

# Проверить логи
docker logs core 2>&1 | grep "Auth"

# Проверить browser console
# Должно быть: [Gate] Access granted
```

**Решение:**
1. Убедитесь что `BOT_TOKEN` правильный
2. Проверьте что initData не устарел (откройте заново)
3. Проверьте что сервер доступен из Telegram

### Проблема: Редирект в dev mode

**Причина:** `TELEGRAM_VALIDATION_ENABLED=True` но работаете локально

**Решение:**
```bash
export TELEGRAM_VALIDATION_ENABLED=False
docker-compose restart
```

### Проблема: WebSocket не подключается

**Диагностика:**
```bash
# Проверить WS auth в browser console
# Должно быть: [WS] Authenticated via JWT: ...
```

**Решение:**
1. Убедитесь что токены сохранены в sessionStorage
2. Проверьте что валидация включена корректно
3. Проверьте логи: `docker logs core | grep WS`

## Контрольный список миграции

- [ ] Обновлены файлы: `gate.js`, `auth.py`, `routes.py`, `websocket.py`
- [ ] Настроены environment variables
- [ ] Проверена конфигурация через `/api/validation-config`
- [ ] Протестирован dev mode в браузере
- [ ] Протестирован strict mode с редиректом
- [ ] Протестирован strict mode в Telegram WebView
- [ ] Проверены логи frontend и backend
- [ ] Обновлена документация для команды
- [ ] Создан rollback план

## Дополнительная информация

Полная документация: [WEBVIEW_VALIDATION.md](./WEBVIEW_VALIDATION.md)

Тесты: `tests/test_webview_validation.py`

Вопросы: см. раздел Troubleshooting в WEBVIEW_VALIDATION.md
