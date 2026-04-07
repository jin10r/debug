# 📖 Project Overview — Survival Map v2

**Полный обзор проекта для разработчиков и архитекторов**

**Дата:** 2026-04-03  
**Версия:** 2.0.0

---

## 🎯 Что это за проект

**Survival Map v2** — это платформа реального времени для мониторинга Telegram-каналов и визуализации локальных событий на интерактивной карте города Одесса.

### Типичные события
- 🚔 Полицейские блокпосты и патрули
- 🚌 Движение автобусов (эвакуационных, гуманитарных)
- 🚗 ДТП, пробки, перекрытия дорог
- 📍 Общие сообщения об опасностях

### Пользователи
- **Жители Одессы** — получают актуальную информацию о ситуации в городе
- **Водители** — избегают блокпостов и пробок
- **Волонтёры** — координируют перемещения

---

## 🏛️ Архитектура в двух словах

```
Telegram → Parser (ищет улицы) → PostgreSQL (считает координаты) → App (раздаёт клиентам)
```

### 5 микросервисов

| Сервис | Что делает | Технологии |
|--------|-----------|------------|
| **Parser** | Читает Telegram, ищет названия улиц | Pyrogram, Rapidfuzz |
| **PostgreSQL** | Хранит события, считает геометрию | PostGIS, pg_cron |
| **App** | Web API, WebSocket, Telegram бот | aiohttp, aiogram |
| **Redis** | Кэш, сессии, защита от повторов | Redis 7 |
| **Nginx** | Обратный прокси, статика | Nginx |

---

## 🔍 Как работает поиск улиц (Sliding Window)

Это **ключевая фича** проекта — алгоритм нечёткого поиска названий улиц в тексте.

### Пример работы

**Входной текст:** `"Преображенская в сторону Софиевской бп"`

**Шаг 1 — Очистка:**
```
"преображенская в сторону софиевской бп"
```

**Шаг 2 — Bigrams (пары слов):**
```
"преображенская в" → нет совпадения
"в сторону" → пропуск (стоп-слова)
"сторону софиевской" → Софиевская (сходство: 0.85) ✅
"софиевской бп" → нет совпадения
```

**Шаг 3 — Unigrams (одиночные слова):**
```
"преображенская" → Преображенская (сходство: 0.92) ✅
"бп" → Блок Пост (сходство: 0.75) ✅
```

**Результат:** `[Преображенская (0.92), Софиевская (0.85), Блок Пост (0.75)]`

**Стратегия геометрии:** `centroid` (3 улицы → центр треугольника)

### Почему это работает хорошо

✅ **Быстро:** 1-5 миллисекунд на сообщение  
✅ **Точно:** 85-95% правильных совпадений  
✅ **Устойчиво к опечаткам:** "Преображенской" → "Преображенская"  
✅ **Падежные формы:** "Ромашковой" → "Ромашкова"  

### Проблемы (известные)

❌ **Стоп-слова:** "Таврия" в стоп-словах, но это жилой массив  
❌ **Перестановки:** "Кюри Жолио" хуже чем "Жолио Кюри"  
❌ **Только биграммы:** "жилой массив Таврия" не найдётся как фраза  

**Детальный анализ:** [docs/SLIDING_WINDOW_ANALYSYS.md](docs/SLIDING_WINDOW_ANALYSYS.md)

---

## 🗄️ База данных

### Главные таблицы

**streets** — Справочник улиц (~1200 записей)
```
id: 1
names: ['Преображенская', 'Преображенской', 'Преображенскую']
geom: POINT(30.75 46.50)
```

**events** — События (автоочистка через 1 час)
```
id: 1234
event_time: 2026-04-03 10:30:00
description: "Преображенская в сторону Софиевской"
layer: cops
strategy: centroid
geom: POINT(30.76 46.51)
matches: [{"street_id": 1, "score": 0.92}, ...]
```

### Как считается геометрия

**process_location_smart()** — главная функция:

1. **Одна улица** → берём её координаты (`single_match`)
2. **Две улицы** → строим линию, берём центр (`centroid`)
3. **3+ улиц** → центр многоугольника (`polygon`)
4. **Ничего не найдено** → случайная точка в круге (`random`)

---

## 📡 API (основные эндпоинты)

### Для веб-клиента

```
GET  /api/events           # Получить все события (GeoJSON)
POST /api/events/updates   # Получить новые события (after_id)
GET  /api/events/status    # Версия и последний ID
GET  /api/events/streets   # Справочник улиц
WS   /ws                   # WebSocket для реального времени
```

### Для Telegram бота

```
POST /api/validate-init    # Валидация Telegram initData
POST /api/auth/refresh     # Обновить JWT токен
```

### Health checks

```
GET /health                # Базовый health
GET /health/live           # Liveness probe
GET /health/ready          # Readiness probe
```

---

## 🔄 Полный жизненный события

```
1. Пользователь пишет в Telegram канал
   ↓
2. Parser читает сообщение (Pyrogram)
   ↓
3. Очистка текста (HTML → plain text)
   ↓
4. Sliding Window ищет улицы (Rapidfuzz)
   ↓
5. Определение слоя (cops/bus/traffic/pig)
   ↓
6. PostgreSQL process_location_smart()
   ↓
7. INSERT INTO events + pg_notify
   ↓
8. App получает NOTIFY → WebSocket → все клиенты
   ↓
9. Клиент обновляет карту (менее 1 секунды)
   ↓
10. Через 1 час: pg_cron удаляет событие
```

---

## 🛠️ Инструменты для разработки

### Скрипты анализа

| Скрипт | Что делает |
|--------|-----------|
| `enrich_streets.py` | Ищет новые улицы в событиях |
| `analyze_events.py` | Анализирует качество matching |
| `export_events.py` | Экспорт событий в CSV |

### Как добавить новую улицу

```python
# 1. Запустить анализ
python enrich_streets.py

# 2. Получить SQL для добавления
# Вывод: INSERT INTO streets (names, geom) VALUES ...

# 3. Применить к БД
docker-compose exec postgres psql -U postgres

# 4. Parser автоматически обновит кэш (pg_notify)
```

---

## 📊 Метрики качества

### Entity Matching

| Метрика | Значение |
|---------|----------|
| **Точность** | 85-95% |
| **Скорость** | 1-5 ms |
| **Ложные срабатывания** | <5% |

### Производительность

| Операция | Задержка |
|----------|----------|
| Поиск сущностей | 1-5 ms |
| Вычисление геометрии | 5-20 ms |
| Полная обработка | 10-50 ms |
| WebSocket доставка | <100 ms |

### Ресурсы

| Сервис | CPU | RAM |
|--------|-----|-----|
| Parser | 0.25 | 256 MB |
| PostgreSQL | 0.5 | 512 MB |
| App | 0.25 | 256 MB |
| Redis | 0.1 | 128 MB |
| Nginx | 0.1 | 64 MB |
| **Итого** | **1.2 CPU** | **~1.2 GB RAM** |

---

## 🚀 Развёртывание

### Быстрый старт

```bash
# 1. Настройка
cp .env.example .env
# Редактировать .env (BOT_TOKEN, CHANNEL_ID, JWT_SECRET)

# 2. Запуск
docker-compose up -d

# 3. Проверка
docker-compose logs -f app
# Открыть http://localhost
```

### Обязательные переменные

```env
BOT_TOKEN=123456:ABC-DEF...     # От @BotFather
CHANNEL_ID=-1001234567890       # ID канала
JWT_SECRET=<32+ символов>       # python -c "import secrets; print(secrets.token_urlsafe(32))"
POSTGRES_PASSWORD=secure        # Пароль PostgreSQL
```

---

## 🐛 Типичные проблемы

### Parser не подключается к Telegram: Session file not found

**Симптом:** `Session file not found` или `API_ID_PUBLISH_FLOOD`

**Полная инструкция по созданию сессии:**

#### 1. Получите API credentials

1. Перейдите на https://my.telegram.org
2. Войдите по номеру телефона
3. Перейдите в **API development tools**
4. Создайте новое приложение
5. Скопируйте `api_id` и `api_hash`

#### 2. Создайте файл сессии

```bash
cat > create_session.py << 'EOF'
import asyncio
from pyrogram import Client

async def main():
    app = Client(
        "my_session",
        api_id=YOUR_API_ID,          # Замените!
        api_hash="YOUR_API_HASH"     # Замените!
    )
    
    async with app:
        print("✅ Сессия создана!")

asyncio.run(main())
EOF
```

```bash
pip install pyrogram
python create_session.py
```

Введите:
- Номер телефона (например `+380XXXXXXXXX`)
- Код подтверждения из Telegram
- 2FA пароль (если есть)

#### 3. Скопируйте сессию

```bash
cp my_session.session parser/session.session
ls -lh parser/session.session  # Проверьте: 5-15 KB
```

#### 4. Перезапустите парсер

```bash
docker-compose restart parser
docker-compose logs -f parser
```

**⚠️ Важно:**
- Никогда не коммитьте `session.session` в Git
- Сделайте бэкап сессии
- Сессия привязана к вашему аккаунту Telegram

### События не появляются на карте

**Причины:**
1. WebSocket не подключён → проверить nginx config
2. PostgreSQL NOTIFY не работает → проверить `docker-compose logs app | grep NOTIFY`
3. Нет совпадений улиц → проверить `enrich_streets.py`

### Много событий со стратегией "random"

**Причина:** Не находятся улицы в тексте

**Решение:**
```bash
# 1. Проверить качество matching
python analyze_events.py

# 2. Добавить новые улицы
python enrich_streets.py

# 3. Проверить стоп-слова (может, реальные улицы в стоп-словах?)
# Файл: core/settings.py → DEFAULT_STOPWORDS
```

---

## 📚 Структура проекта

```
rapid_window/
├── core/                    # Главный app (aiohttp)
│   ├── api/                 # REST API handlers
│   ├── db/                  # Database connectors
│   ├── handlers/            # Telegram bot handlers
│   ├── middlewares/         # Auth, CSRF, rate limiting
│   ├── tasks/               # Background tasks
│   ├── utils/               # Utilities
│   └── settings.py          # Конфигурация
│
├── parser/                  # Парсер Telegram
│   ├── monitoring.py        # Главный цикл парсера
│   ├── message_processor.py # Обработка сообщений + SlidingWindowMatcher
│   ├── db_adapter.py        # DB adapter для парсера
│   └── settings.py          # Импорт настроек
│
├── postgres/                # PostgreSQL
│   ├── init-scripts/        # Скрипты инициализации
│   └── migrations/ARCHIVE/  # Архив миграций
│
├── web/                     # Веб-фронтенд
│   ├── index.html           # Landing page
│   ├── map.html             # Карта
│   ├── js/                  # TypeScript source
│   └── tests/               # Frontend tests
│
├── docs/                    # Документация
│   ├── ARCHITECTURE_REPORT.md
│   ├── DOCKER_ARCHITECTURE.md
│   ├── SECURITY.md
│   ├── SLIDING_WINDOW_ANALYSYS.md
│   └── ARCHIVE/             # Архив документов
│
├── main.py                  # Entry point
├── enrich_streets.py        # Поиск новых улиц
├── analyze_events.py        # Анализ качества
├── export_events.py         # Экспорт в CSV
├── README.md                # Главная документация
├── REPORT.md                # Отчёт по обогащению улиц
└── CLEANUP_REPORT.md        # Отчёт по очистке
```

---

## 🔮 Планы развития

### Критические улучшения

- [ ] Удалить "Таврия" из стоп-слов (реальная локация!)
- [ ] Добавить триграммы (окно = 3 слова)
- [ ] Заменить `fuzz.ratio` на `fuzz.WRatio`

### Важные улучшения

- [ ] Контекстная валидация сущностей
- [ ] Метрики precision/recall
- [ ] Динамическое обновление стоп-слов

### Эксперименты

- [ ] GLiNER NER модель (ML-based)
- [ ] spaCy для русского языка
- [ ] Фонетическое сравнение для кириллицы

---

## 📞 Полезные ссылки

- [Документация Rapidfuzz](https://rapidfuzz.github.io/rapidfuzz/)
- [PostGIS документация](https://postgis.net/documentation/)
- [aiohttp документация](https://docs.aiohttp.org/)
- [Pyrogram документация](https://docs.pyrogram.org/)

---

## 🎓 Onboarding нового разработчика

### День 1: Понимание архитектуры

1. Прочитать `README.md`
2. Прочитать `docs/ARCHITECTURE_REPORT.md`
3. Запустить проект локально (`docker-compose up -d`)
4. Открыть `http://localhost`, посмотреть карту

### День 2: Изучение кода

1. Прочитать `parser/message_processor.py` (SlidingWindowMatcher)
2. Прочитать `core/settings.py` (конфигурация)
3. Прочитать `postgres/init-scripts/03-functions.sql` (геометрия)

### День 3: Первая задача

1. Добавить новую улицу (через `enrich_streets.py`)
2. Изменить порог сходства в `settings.py`
3. Посмотреть эффект в `analyze_events.py`

---

*Документ создан: 2026-04-03*  
*Версия проекта: 2.0.0*
