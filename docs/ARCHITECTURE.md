# Архитектура микросервисов Survival Map

## 1. Общая архитектура

Система состоит из 5 микросервисов, объединённых тремя изолированными Docker-сетями
(`frontend`, `backend`, `db`). База данных (`db`) изолирована от внешнего мира
(internal: true).

```
                   Telegram (MTProto)
                          │
                          ▼
  ┌───────────────────────────────────────────────────┐
  │                   parser                          │
  │  Kurigram (MTProto клиент) + NLP Pipeline         │
  │  ~200-400ms/сообщение, 8 workers, asyncio.Queue   │
  │  HTTP → model:8082  (Tier-2 LLM geo-resolution)   │
  └─────────────────────┬─────────────────────────────┘
                        │ INSERT + SELECT geo_execute_scenario()
                        ▼
  ┌───────────────────────────────────────────────────┐
  │               postgres (PostGIS)                   │
  │  PostgreSQL 15 + PostGIS 3.3 + pg_cron            │
  │  process_candidates() — PostGIS geo-resolution     │
  │  geo_execute_scenario() — LLM-directed сценарии   │
  │  pg_notify → 'events_new', 'geo_updated'           │
  └─────────────────────┬─────────────────────────────┘
                        │ LISTEN/NOTIFY
                        ▼
  ┌───────────────────────────────────────────────────┐
  │                  core                              │
  │  aiohttp (HTTP + WebSocket) + aiogram (TG bot)    │
  │  REST API (15+ endpoints) + WS GeoJSON broadcast  │
  │  JWT auth + HMAC initData + rate limiting         │
  └─────────────────────┬─────────────────────────────┘
                        │ WebSocket / HTTP
                        ▼
  ┌───────────────────────────────────────────────────┐
  │                  web (nginx)                       │
  │  Reverse proxy, rate limit (10r/s api, 1r/s auth) │
  │  Статика: Leaflet PWA Telegram Mini App           │
  └─────────────────────┬─────────────────────────────┘
                        │
                        ▼
              Браузер / Telegram WebView
```

---

## 2. Технологический стек контейнеров

### 2.1 postgres

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | `postgis/postgis:15-3.3` (PostgreSQL 15 + PostGIS 3.3) |
| **Доп. расширения** | `pg_cron` (очистка событий каждые 5 мин), `pg_stat_statements` |
| **Язык PL** | `plpgsql` |
| **Конфигурация** | `shared_buffers = 256MB`, `effective_cache_size = 384MB`, `work_mem = 4MB`, `maintenance_work_mem = 64MB`, `random_page_cost = 1.1` (SSD), `statement_timeout = 30s`, `max_connections = 20` |
| **Скрипты инициализации** | 9 файлов: `01-extensions.sql`, `02-tables.sql`, `03-functions.sql`, `04-load-data.sql`, `06-notify-trigger.sql`, `08-process-candidates.sql`, `09-event-geom-trigger.sql`, `10-geometry-scenarios.sql` |
| **Пользователи** | `app_user` (parser), `ws_user` (core), оба с доступом только из Docker CIDR |
| **Сети** | `db` (internal) — изолирована от внешнего мира |
| **Ресурсы** | лимит: 0.5 CPU / 512MB RAM |

**Основные таблицы:**
- `events` — события с геометрией, слоем, стратегией; TTL 60 мин (pg_cron)
- `geo` — единый справочник гео-объектов (улицы, деревни, ж/д станции и т.д.; 1728 записей, 8 типов)
- `stopwords` — стоп-слова, `layer_keywords` — ключевые слова слоёв
- `events_meta` — метаданные для синхронизации WebSocket (version, max_event_id)

**Ключевые функции:**
- `process_candidates()` — geo-resolution: 0 matches → random; 1 → полная геометрия;
  2+ → ST_Intersection / псевдопересечение (≤150m) / convex hull
- `geo_execute_scenario(p_scenario, ...)` — диспетчеризация по 10 сценариям
  (intersection, nearest_point, buffer_area, along_line, within_polygon,
  pseudo_intersection, convex_hull, random, single_object, centroid_area)
- Триггер `notify_event_inserted` — pg_notify('events_new', ...)
- Триггер `event_geom_trigger` — аудит изменений геометрии
- Триггер `notify_geo_updated` — pg_notify('geo_updated', ...)

---

### 2.2 parser

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | `python:3.11.10-slim-bookworm` (multi-stage) |
| **Точка входа** | `python -m parser.monitoring` |
| **Порт** | 8765 (только healthcheck) |
| **Telegram клиент** | `kurigram` (форк Pyrogram) — user session, MTProto |
| **NLP библиотеки** | `pymorphy3` (лемматизация), `snowballstemmer` (стемминг), `rapidfuzz` (нечёткий поиск), `onnxruntime` (семантическая модель), `faiss-cpu` (векторный поиск) |
| **Семантическая модель** | `multilingual-e5-small` ONNX (~118MB) |
| **Асинхронный фреймворк** | `asyncio` + `asyncpg` (пул соединений к PG) |
  | **HTTP клиенты** | `aiohttp`: `model_client.py` → model:8082 (30s timeout, TTL cache 60s) |
| **Очередь сообщений** | `asyncio.Queue(maxsize=1000)`, 8 workers, drain_timeout=20s |
  | **Сети** | `backend` (model) + `db` (postgres) |
| **Ресурсы** | лимит: 1.0 CPU / 512MB RAM |

**Pipeline обработки сообщения:**
1. `strip_tail` — удаление маркера "сообщить"/"подписаться"
2. `preprocess_light` — удаление HTML, нормализация UA→RU, удаление хештегов
3. `word_tokenizer.tokenize` — токенизация, слияние "5я", is_anchored для ##
4. `morphology.lemmatize_tokens` — pymorphy3 (LRU 10k) + snowballstemmer
5. `layer_classifier.classify` — cops / bus / traffic / pig (keyword match)
 6. `geo_matcher.find_geo` — скользящее окно 1-3 токенов, 3 уровня:
    - T1: стемминг (snowballstemmer, точное совпадение)
    - T1b: семантические эмбеддинги (e5-small ONNX + FAISS cosine)
    - T2: нечёткое совпадение (rapidfuzz token_sort_ratio ≥ 0.85)
 7. Классификация сценария через `process_candidates` или `geo_execute_scenario()`
 8. INSERT в events через CTE с `process_candidates()`

---

### 2.3 model (LLM Geo-Resolution — Tier-2)

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | `python:3.12-slim` |
| **Фреймворк** | `aiohttp` |
| **Точка входа** | `python -m model.server` |
| **Порт** | 8082 |
| **LLM движок** | `llama-cpp-python` + Qwen2.5-0.5B-Instruct GGUF (q4_k_m, ~400MB) |
| **Контекст** | ctx=8192, threads=4 |
| **Tool-calling** | MAX_TOOL_TURNS=5, thresholds [0.5, 0.7, 0.85, 0.95, 0.99] |
| **Инструменты** (8 шт) | search_geo, get_geo_info, compute_intersection, compute_distance, compute_convex_hull, get_nearby, normalize_text, spatial_filter_outliers |
| **Доступ к postgres** | Прямое asyncpg-соединение (DSN: postgresql://postgres:postgres@postgres:5432/postgres) |
| **Сети** | `backend` + `db` |
| **Ресурсы** | лимит: 1.5 CPU / 1GB RAM |

**Эндпоинты:**
- `GET /health` — проверка здоровья
- `POST /resolve` — geo-resolution с LLM tool-calling

---

### 2.4 core

| Компонент | Значение |
|-----------|----------|
| **Базовый образ** | `python:3.11.10-slim-bookworm` |
| **Точка входа** | `python main.py` |
| **Порт** | 8080 |
| **HTTP сервер** | `aiohttp` (TCPSite) |
| **Telegram бот** | `aiogram` (polling) |
| **База данных** | `asyncpg` (pool min=2, max=10, timeout=30s) |
| **JWT auth** | HS256, автогенерация secret, access 15min / refresh 24h, LRU verify cache (10k entries, 60s TTL) |
| **Telegram auth** | HMAC-SHA256 initData, circuit breaker (pybreaker, fail_max=5, reset_timeout=30s) |
| **CSRF** | HMAC токен, 1h TTL |
| **Rate limiting** | Fixed-window, default 60/60s, per-endpoint переопределения |
| **WebSocket** | `WebSocketManager` — pg_notify('events_new'), GeoJSON FeatureCollection broadcast |
| **Кэш (in-memory)** | `CacheManager` — OrderedDict LRU + TTL, async lock, без Redis |
| **Метрики** | Prometheus `/metrics` |
| **Сети** | `frontend` (web) + `backend` (model) + `db` (postgres) |
| **Ресурсы** | лимит: 1.0 CPU / 512MB RAM |

**Эндпоинты (15+):**
- `GET /health*` — мониторинг
- `GET /api/validation-config`, `POST /api/validate-init` — Telegram initData
- `POST /api/auth/refresh` — обновление JWT
- `GET /api/config` — конфигурация фронтенда
- `GET /api/events` — список событий с фильтрацией
- `POST /api/events` — создание события (ручной ввод с карты)
- `GET /api/events/updates` — long-polling (legacy, основной канал — WS)
- `GET /api/events/snapshot` — снепшот всех событий
- `GET /api/events/status` — метаданные очереди
- `GET /api/geo`, `/api/geo/all` — справочник гео-объектов
- `GET /api/data-status` — статус данных
- `WS /ws` — WebSocket (основной канал)
- `GET /metrics` — Prometheus
- `GET /media/{filename}` — раздача фото

**Middleware chain (порядок):**
1. Logging
2. Metrics (Prometheus)
3. CSRF
4. JWT Auth
5. Rate Limiter

---

## 3. Поток данных (Data Flow)

### 3.1 Полный цикл обработки сообщения из Telegram

```
  Шаг 1: Парсинг Telegram канала
  ────────────────────────────────
  parser (monitoring.py) через kurigram (MTProto) получает новое
  сообщение из заданных Telegram каналов. Сообщение содержит:
  текст, время, опционально фото. Сообщение помещается в
  asyncio.Queue(maxsize=1000), откуда его забирает один из 8 workers.


  Шаг 2: Предобработка текста
  ──────────────────────────────
  Парсер последовательно применяет:
  - strip_tail() — удаление "сообщить"/"подписаться"
  - preprocess_light() — удаление HTML, замена UA→RU ("ї"→"и", "є"→"е"),
    удаление хештегов, извлечение времени из текста
  - word_tokenizer.tokenize() — разбиение на токены, слияние "5я"→"5_я"
  - morphology.lemmatize_tokens() — лемматизация pymorphy3 + стемминг
    snowballstemmer


  Шаг 3: Определение слоя (layer)
  ─────────────────────────────────
  layer_classifier.classify() сопоставляет ключевые слова с 4 слоями:
  - "cops" — посты про полицию/военных
  - "bus" — посты про автобусы/общественный транспорт
  - "traffic" — посты про ДТП/пробки
  - "pig" — всё остальное (default)


  Шаг 4: Поиск гео-объектов (geo matching)
  ──────────────────────────────────────────
  geo_matcher.find_geo() применяет скользящее окно 1-3 токенов
  с тремя уровнями поиска в gazetteer улиц:

  [T1] Стемминг: точное совпадение стеммированных токенов
       (snowballstemmer) с нормализованными названиями.
       ≈ 85-90% всех совпадений.

  [T1b] Семантический: эмбеддинги multilingual-e5-small ONNX →
        FAISS cosine similarity. Срабатывает когда стемминг не дал
        результата (опечатки, варианты написания).

  [T2] Нечёткий: rapidfuzz token_sort_ratio ≥ 0.85.
       Самый медленный, срабатывает редко.


  Шаг 5: Вычисление геометрии в PostGIS
  ───────────────────────────────────────
  Parser вызывает process_candidates() или geo_execute_scenario()
  для вычисления геометрии на основе найденных ID гео-объектов.
  INSERT ... SELECT CTE запрос:

  WITH pc AS (
    SELECT * FROM process_candidates($geo_ids, $scores, ...)
  ),
  inserted AS (
    INSERT INTO events (...) SELECT ... FROM pc
    ON CONFLICT (message_id) DO NOTHING
    RETURNING ...
  )
  ... pg_notify ...


  Шаг 6: PostGIS сценарии геометрии
  ───────────────────────────────────
  Каждый сценарий возвращает (geom, strategy, matches):

  - intersection: ST_Intersection всех пар улиц
  - nearest_point: ST_ClosestPoint к референсной точке
  - buffer_area: ST_Buffer вокруг объединённой геометрии
  - along_line: ST_LineInterpolatePoint вдоль линии
  - within_polygon: ST_PointOnSurface внутри полигона
  - pseudo_intersection: ST_ClosestPoint между близкими объектами
  - convex_hull: ST_ConvexHull объединённой геометрии
  - single_object: полная геометрия лучшего объекта
  - centroid_area: ST_Centroid объединённой геометрии
  - random: случайная точка в зоне


  Шаг 7: WebSocket уведомление
  ──────────────────────────────
  Триггер notify_event_inserted() → pg_notify('events_new', GeoJSON).

  core (WebSocketManager) слушает pg_notify и транслирует событие
  всем подключённым WebSocket-клиентам в формате GeoJSON FeatureCollection.

  Frontend (Leaflet PWA) получает событие, добавляет маркер на карту
  с анимацией, отображает описание, слой (цвет маркера), фото.


  Шаг 8: Tier-2 LLM geo-resolution (опционально)
  ────────────────────────────────────────────────
  Если strategy = 'random' или максимальная схожесть < 0.85:
  parser вызывает model:8082 POST /resolve.

  model-service запускает tool-calling цикл (до 5 итераций) с
  Qwen2.5-0.5B-Instruct. Модель может вызывать инструменты для
  уточнения геолокации. Результат сохраняется как UPDATE события.
```

### 3.2 Альтернативные потоки данных

```
  А. Ручное создание события с карты (фронтенд → core → postgres)
  ────────────────────────────────────────────────────────────────
  Пользователь на карте → клик → выбор слоя → POST /api/events
  → core валидирует JWT → INSERT в events → pg_notify → всем WS


  Б. Очистка событий по TTL (pg_cron → postgres)
  ───────────────────────────────────────────────
  pg_cron каждые 5 минут запускает очистку событий старше 60 минут.
  Триггер очистки → pg_notify('events_cleaned') → core уведомляет WS.


  В. Обновление справочника гео-объектов
  ─────────────────────────────────────────
  Вставка/обновление в geo → триггер → pg_notify('geo_updated')
  → parser перестраивает PhoneticIndex.
```

---

## 4. Плюсы и минусы реализации

### 4.1 Плюсы (+)

| № | Плюс | Описание |
|---|------|----------|
| 1 | **Изоляция сетей** | `db` сеть internal: true — база данных недоступна извне Docker |
| 2 | **Idempotent INSERT** | `ON CONFLICT (message_id) DO NOTHING` — ретраи не создают дубликатов |
| 3 | **Один roundtrip** | Геометрия вычисляется внутри CTE, INSERT, meta-update и pg_notify — один запрос к БД |
| 4 | **Tiered matching** | 3 уровня поиска улиц (стемминг → семантика → нечёткий) + LLM Tier-2 |
| 5 | **WebSocket realtime** | pg_notify → WebSocket — события доходят до фронта за <100ms |
| 6 | **Graceful shutdown** | drain очередь (20s), stop_grace_period (30s) — не теряет сообщения |
| 7 | **Hardened контейнеры** | cap_drop: ALL, no-new-privileges, tmpfs, readonly rootfs |
| 8 | **Healthchecks** | У всех сервисов, parser использует heartbeat-файл |
| 9 | **Без Redis** | In-memory LRU кэш в core — меньше движущихся частей |
| 10 | **pg_notify** | Встроенный брокер сообщений без Kafka/RabbitMQ |
| 11 | **PWA фронтенд** | Service worker, offline-first, кэширование событий на клиенте |
| 12 | **Rate limiting** | Двухуровневый (nginx edge + app-level) |
| 13 | **Prometheus метрики** | core экспортирует метрики, но нет сборщика |

### 4.2 Минусы (-)

| № | Минус | Описание | Степень |
|---|-------|----------|---------|
| 1 | **Нет оркестрации** | docker-compose для продакшена — нет автоскейлинга, нет rolling update, нет service discovery | Критично |
| 2 | **Одиночный процесс parser** | 1 asyncio процесс, 8 workers — GIL ограничивает CPU | Высоко |
| 3 | **llm-service удалён** | Классификация сценария интегрирована в parser, ресурсы оптимизированы | Устранено |
| 4 | **Нет replica postgres** | Одна нода — нет высокой доступности | Высоко |
| 5 | **pg_cron, не pg_partman** | TTL через pg_cron DELETE, не партиционирование | Средне |
| 6 | **JWT секрет в памяти** | Автогенерация при старте — сброс всех токенов при рестарте core | Средне |
| 7 | **JWT cache без хеша** | Ключ кэша — raw token, не hash | Средне |
| 8 | **statement_timeout 30s** | Мягкий таймаут, но не настроен на уровне пользователя | Средне |
| 9 | **No SSL/TLS** | В nginx закомментирован, нет HTTPS | Критично |
| 10 | **Нет CI/CD** | Только .gitlab-ci.yml базовая настройка | Высоко |
| 11 | **Parser healthcheck ненадёжен** | Heartbeat файл — может не отловить зависший event loop | Средне |
| 12 | **llm-service удалён** | Сервис заменён на встроенную логику в parser | Устранено |
| 13 | **Отсутствует observability** | Нет distributed tracing, нет централизованного логирования | Высоко |
| 14 | **parser не распределён** | Один контейнер на все Telegram каналы — единая точка отказа | Высоко |
| 15 | **X-Forwarded-For спуфинг** | nginx `set_real_ip_from` не настроен | Критично |
| 16 | **TypeScript strict: false** | Фронтенд без строгой типизации | Средне |

---

## 5. Рекомендации и оптимизации

### 5.1 parser

| № | Рекомендация | Приоритет | Обоснование |
|---|-------------|-----------|-------------|
| 1 | **Scale out: несколько инстансов parser** | P0 | Один процесс — единая точка отказа. Разделить каналы между 2-3 инстансами или использовать распределённую очередь (NATS/RabbitMQ) |
| 2 | **Вынести stopwords/layer_keywords в core/LLM** | P1 | Синхронизация стоп-слов между parser и core через pg_notify уже есть, но layer_keywords дублируются в коде и БД |
| 3 | **Добавить distributed tracing** | P1 | OpenTelemetry для отслеживания полного цикла сообщения через parser → PG → core → WS |
| 4 | **Оптимизировать semantic_matcher** | P2 | ONNX модель e5-small можно квантизовать до int8 для ускорения CPU inference |
| 5 | **Добавить circuit breaker для HTTP вызовов** | P1 | Сейчас только голый timeout для model_client. Нужен breaker с fallback |
| 6 | **Увеличить drain_timeout** | P2 | 20s может не хватить при 1000 сообщений в очереди, увеличить до 60s |
| 7 | **Вынести session.session в secrets** | P1 | Файл сессии монтируется в volume — должен быть Docker secret или encrypted |
| 8 | **Убрать healthcheck через heartbeat** | P1 | Заменить на HTTP endpoint с проверкой внутреннего состояния (размер очереди, статус workers, время последней обработки) |

### 5.2 postgres

| № | Рекомендация | Приоритет | Обоснование |
|---|-------------|-----------|-------------|
| 1 | **Настроить стриминг-реплику** | P0 | Одна нода — single point of failure. Реплика для read-only запросов core и аварийного переключения |
| 2 | **Заменить pg_cron на pg_partman** | P1 | DELETE 60-min TTL неэффективен. Партиционирование по дням с DROP старой партиции — O(1) |
| 3 | **Настроить statement_timeout по пользователям** | P1 | app_user (parser): 30s, ws_user (core): 10s, отдельный пользователь для model: 60s |
| 4 | **Увеличить shared_buffers** | P1 | 256MB для 512MB контейнера — маловато. Рекомендация: 25% RAM = ~128MB для выделенного сервера, но в контейнере с лимитом 512MB можно поднять до 384MB |
| 5 | **Добавить pg_stat_statements мониторинг** | P2 | Расширение установлено, но нет сборщика метрик. Подключить к Prometheus |
| 6 | **Настроить autovacuum агрессивнее** | P2 | events часто вставляются и удаляются (TTL). Ускорить autovacuum для этой таблицы |
| 7 | **Добавить индекс на (layer, event_time)** | P2 | Фронтенд фильтрует по слоям — составной индекс ускорит запросы core |
| 8 | **Обновить конфигурацию для M1/M2** | P2 | `random_page_cost = 1.1` и `effective_cache_size = 384MB` заточены под HDD/старый SSD. Для NVMe: `random_page_cost = 1.0` |

### 5.3 model (LLM Geo-Resolution)

| № | Рекомендация | Приоритет | Обоснование |
|---|-------------|-----------|-------------|
| 1 | **Добавить кэширование ответов** | P1 | Одинаковые или похожие адреса будут повторяться. TTL-кэш на уровне model или parser.model_client |
| 2 | **Увеличить MAX_TOOL_TURNS** | P2 | 5 итераций может не хватить для сложных случаев. Сделать конфигурируемым через env |
| 3 | **Async tool execution** | P1 | Инструменты model/tools.py выполняются последовательно. Переписать на asyncio.gather для независимых вызовов |
| 4 | **Rate limiting на model** | P1 | parser может зафлудить model при массовом поступлении сообщений. Добавить ограничение |
| 5 | **Healthcheck через /resolve lightweight** | P2 | Текущий healthcheck через urllib — поверхностный. Добавить легковесный тест с маленьким промптом |
| 6 | **Скачивание модели при сборке** | P1 | model_manager.py скачивает GGUF при старте — увеличивает время запуска. Перенести в Dockerfile |

### 5.4 core

| № | Рекомендация | Приоритет | Обоснование |
|---|-------------|-----------|-------------|
| 1 | **JWT secret в Docker secret** | P0 | Автогенерация при старте сбрасывает все токены. Вынести в BOT_TOKEN-derived или Docker secret |
| 2 | **JWT cache key hash** | P1 | Хранить в кэше hash токена, не raw token (security best practice) |
| 3 | **Добавить ACL для WebSocket** | P1 | Сейчас любой с валидным JWT/initData получает все события. Добавить фильтрацию по слоям |
| 4 | **Переделать healthcheck на HTTP** | P1 | Текущий healthcheck через urllib — заменить на проверку: БД connected, WS loop active |
| 5 | **Добавить graceful shutdown** | P1 | aiohttp TCPSite не дожидается завершения WS-соединений при SIGTERM |
| 6 | **Оптимизировать rate limiter** | P2 | Fixed-window не идеален. Sliding-window или token bucket для более равномерного ограничения |
| 7 | **Убрать blocking CSV I/O** | P1 | db/db_geo.py читает CSV синхронно в async коде. Обернуть в asyncio.to_thread или читать при старте |
| 8 | **Добавить Pydantic field constraints** | P1 | Pydantic модели в api/events.py без валидации полей (min_length, max_length, ge, le) |
| 9 | **Заменить LRU кэш на Redis** | P2 | При нескольких инстансах core in-memory кэш не синхронизируется. Пока неактуально (1 инстанс), но на вырост |
| 10 | **Prometheus + Grafana** | P2 | Метрики экспортируются, но нет дашборда. Добавить docker-compose сервис grafana с готовым дашбордом |

---

## 6. Диаграмма зависимостей сервисов

```
       web (nginx:80)
          ↑
       core (aiohttp:8080)
       ┣━━ postgres (5432) — asyncpg
       ┣━━ model (8082) — для LLM geo-resolution
       ┗━━ ... (другие сервисы не вызывает напрямую)

       parser (kurigram)
       ┣━━ postgres (5432) — asyncpg INSERT + SELECT
       ┗━━ model (8082) — POST /resolve (Tier-2)

       model (aiohttp:8082)
       ┗━━ postgres (5432) — asyncpg (справочники гео-объектов)
```

---

## 7. Стратегия миграции на Kubernetes

При переходе на Kubernetes рекомендуется:

1. **Заменить pg_notify на NATS JetStream** — нативный брокер для K8s, поддержка exactly-once
2. **Parser → Deployment с HPA** — автоскейлинг по длине очереди сообщений
3. **Postgres → StatefulSet + Patroni** — HA кластер с автоматическим failover
4. **Core → Deployment с HPA** — автоскейлинг по WebSocket соединениям
5. **Model → Deployment с GPU толерантностью** — если появятся GPU ноды
6. **Web → Ingress + Cert-Manager** — TLS termination на уровне Ingress
7. **Metrics → Prometheus Operator + Grafana** — дашборды + алерты
