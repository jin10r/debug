# Полное ревью архитектуры и микросервисов
## Survival Map v5.0.0
**Дата:** 2026-08-28

---

## 1. Общая архитектура

### 1.1 Схема потоков данных

```
Telegram Channel
       │
       ▼
   ┌─────────┐     pending_events     ┌──────────────┐     events     ┌─────────┐
   │ Parser  │ ──────────────────────→ │  Processor   │ ────────────→ │  Core   │
   │ (kurigram)│     INSERT + NOTIFY   │  (NLP/Geo)   │  INSERT +     │ (aiohttp)│
   └─────────┘                         └──────────────┘  NOTIFY       └────┬────┘
                                                                    ┌──────┼──────┐
                                                                    ▼      ▼      ▼
                                                              PostgreSQL  WebSocket  REST API
                                                                         (real-time)  (HTTP)
```

### 1.2 Оценка архитектуры

| Аспект | Оценка | Комментарий |
|--------|--------|-------------|
| Разделение ответственности | **9/10** | Чёткое разделение: parser → processor → core |
| Связность сервисов | **9/10** | Слабая связь через PostgreSQL (LISTEN/NOTIFY) |
| Масштабируемость | **7/10** | Stateless processor/parser, но core — single instance |
| Надёжность | **8/10** | Circuit breaker, retry, graceful shutdown |
| Мониторинг | **8/10** | Prometheus metrics, Grafana dashboards |
| Безопасность | **9/10** | JWT, rate limiting, body size limits, path traversal protection |

**Общая оценка: 8.5/10**

---

## 2. Core (API сервер)

### 2.1 Структура

```
core/
├── app_factory.py      # Application factory, startup/shutdown lifecycle
├── settings.py         # Configuration (dataclass-based)
├── models.py           # Pydantic request models
├── metrics.py          # Prometheus metrics
├── api/
│   ├── routes.py       # Route registration
│   ├── events.py       # Events API (CRUD + ETag)
│   ├── websocket.py    # WebSocket manager (real-time streaming)
│   ├── auth.py         # JWT auth endpoints
│   ├── health.py       # Health checks (liveness/readiness)
│   ├── config.py       # Client configuration
│   └── media.py        # Photo serving
├── db/
│   ├── dbconnect.py    # Facade pattern (Request class)
│   ├── db_base.py      # Connection pool factory
│   ├── db_events.py    # Event queries (GeoJSON)
│   ├── db_geo.py       # Geo data queries
│   ├── db_spatial.py   # PostGIS spatial operations
│   └── db_auth.py      # Refresh token operations
├── middlewares/
│   ├── jwt_auth.py     # JWT authentication
│   ├── ratelimit.py    # Rate limiting (fixed-window)
│   ├── body_size_limit.py  # Request body size guard
│   └── auth.py         # JWT primitives (generate/verify)
├── handlers/
│   └── basic.py        # Telegram bot handlers (/start)
└── utils/
    ├── cache.py         # In-memory LRU cache
    └── telegram_validation.py  # Telegram initData validation
```

### 2.2 Сильные стороны

**✅ Facade Pattern (dbconnect.py)**
`Request` класс делегирует `GeoOperations`, `EventOperations`, `SpatialOperations`, `AuthOperations`. Чистое разделение, легко тестировать.

**✅ Middleware Chain**
```
logging → body_size_limit → jwt_auth → rate_limiter
```
Каждый middleware независим, легко добавлять/удалять.

**✅ WebSocket Architecture**
- Copy-on-write broadcast (parallel sending)
- Snapshot mode для catch-up
- Auth timeout, rate limiting, layer subscriptions
- Resync mechanism при out-of-range watermark

**✅ JWT Refresh Token Rotation**
- Single-use токены в БД
- Атомарный `consume_refresh_token` (anti-replay)
- Auto-revoke при обнаружении кражи

**✅ ETag Caching**
Кэширование ETag по `events_meta.version` — -95% CPU на вычисление.

**✅ Security**
- Path traversal protection (media.py)
- Body size limits (1MB default, 5MB for GeoJSON)
- Rate limiting (app-level + nginx edge)
- CSP headers, CORS control

### 2.3 Слабые стороны и рекомендации

**⚠️ Single Instance Core**
Core — один экземпляр. При росте нагрузки нужно добавить:
- Session affinity на nginx (для WebSocket)
- Или external cache (Redis) для multi-replica

**⚠️ In-Memory Cache (CacheManager)**
`CacheManager` хранит данные в OrderedDict. При multiple instances кэш не разделяется. Рекомендация: Redis при scale-out.

**⚠️ JWT Cache Without Invalidation**
`_jwt_token_cache` кэширует валидные токены на 10s. При token revocation задержка до истечения TTL. Приемлемо для текущего scale.

**⚠️ Heartbeat File-Based**
Processor/Parser пишут heartbeat в `/tmp/` файлы. Core читает их для метрик. При scale-out — ненадёжно. Рекомендация: PG NOTIFY heartbeat.

**Оценка: 8.5/10**

---

## 3. Parser (Telegram клиент)

### 3.1 Структура

```
parser/
├── monitoring.py       # ParserBot: kurigram + pending_events
├── metrics.py          # Prometheus metrics
├── session.session     # Telegram session file
└── requirements.txt    # kurigram, asyncpg
```

### 3.2 Архитектура

```
Telegram Channel → kurigram Client → preprocess_text → pending_events (queue)
                                         │
                                    adaptive pool (2-8 workers)
                                         │
                                    photo download (NOTIFY-based)
```

### 3.3 Сильные стороны

**✅ Adaptive Worker Pool**
Автоматическое масштабирование 2-8 воркеров по размеру очереди. Backpressure при переполнении.

**✅ At-Least-Once Delivery**
При переполнении очереди — прямая запись в DB (не потеря). Recovery через `_recover_missing_photos()`.

**✅ Photo Pipeline**
- NOTIFY-based download (event-driven)
- Semaphore для concurrency control (max 3)
- Orphan cleanup (daily)
- Path traversal protection

**✅ Graceful Shutdown**
Drain queue с таймаутом, корректное закрытие Telegram client.

**✅ Heartbeat + Healthcheck**
Enriched heartbeat с RSS и LRU size. Docker healthcheck через `/tmp/parser_heartbeat`.

### 3.4 Слабые стороны

**⚠️ Session File Management**
`session.session` — файл сессии Telegram. Если утерян — parser не стартует. Рекомендация: volume mount + backup.

**⚠️ History Load on Startup**
`_load_chat_history()` загружает `PARSER_HISTORY_LIMIT` (65) сообщений при старте. При large history — медленный старт.

**⚠️ Duplicate Code**
`_preprocess_message_text()` и `_process_message()` дублируют логику preprocess. Рекомендация: единый метод.

**Оценка: 8/10**

---

## 4. Processor (NLP Pipeline)

### 4.1 Структура

```
processor/
├── main.py              # ProcessorBot: NLP pipeline + pending_events consumer
├── morphology.py        # pymorphy3 + Snowball stemmer (LRU caches)
├── phonetic_index.py    # Stem + Surface index (Tier 1 + Tier 2)
├── geo_matcher.py       # Geo matching (sliding window + fuzzy)
├── layer_classifier.py  # Layer classification (keyword-based)
├── word_tokenizer.py    # Tokenizer (regex-based)
├── health.py            # Health server (HTTP)
└── requirements.txt     # pymorphy3, rapidfuzz, asyncpg
```

### 4.2 Архитектура NLP Pipeline

```
pending_events → tokenize → lemmatize → classify layer → find_geo → insert events
                                      │
                            ┌─────────┴─────────┐
                            │   GeoMatcher       │
                            │  Tier 1: Stem exact│
                            │  Tier 2: Fuzzy     │
                            └─────────┬─────────┘
                                      │
                              PhoneticIndex
                            (stem tuples + surface phrases)
```

### 4.3 Сильные стороны

**✅ Two-Tier Geo Matching**
- Tier 1: Stem exact (O(1) lookup) — 89%命中率
- Tier 2: Surface fuzzy (rapidfuzz) — для опечаток

**✅ Morphology Caching**
3 LRU-кэша: lemma (20K), phrase (2K), stem (20K). Hit-rate ~80%.

**✅ Sliding Window Candidates**
N-gram generation с якорями (предлоги) и предфильтрацией.

**✅ ProcessPoolExecutor**
Tier 2 fuzzy match в отдельном process pool (4 workers). Не блокирует event loop.

**✅ Circuit Breaker**
Защита БД от каскадных сбоев.

**✅ Stale Task Cleanup**
Фоновый очиститель зависших 'processing' задач (каждые 60s).

**✅ Memory Management**
RSS monitoring + gc.collect + LRU shrink при approach to 1GB limit.

### 4.4 Слабые стороны

**⚠️ Keyword-Based Layer Classifier**
`LayerClassifier` использует ~50 ключевых слов. 25.5% событий попадают в fallback "pig". Рекомендация: ML-based classifier или расширение словаря.

**⚠️ Single Geo Database**
1,732 geo-объекта. Многие улицы отсутствуют → 10.9% fallback. Рекомендация: импорт из OpenStreetMap (5,000+ объектов).

**⚠️ ProcessPoolExecutor Overhead**
IPC для каждого Tier 2 batch. При малом числе кандидатов (<5) — overhead превышает benefit.

**⚠️ No Semantic Validation**
Geo-matcher не учитывает семантику контекста. "Балковская Кача бусов" → classified as "pig" вместо "bus".

**Оценка: 8/10**

---

## 5. PostgreSQL

### 5.1 Схема

```
events (partitioned by event_time, hourly)
├── events_2026_08_28_11
├── events_2026_08_28_12
├── events_2026_08_28_13
└── events_2026_08_28_14

pending_events (queue: pending → processing → done/error)
geo (1,732 objects: streets, villages, towns, POI)
stopwords
refresh_tokens
events_meta (version counter for cache invalidation)
```

### 5.2 Сильные стороны

**✅ Hourly Partitioning**
Автоматическое создание партиций (+2h вперёд, -72h назад). Old partitions drop.

**✅ LISTEN/NOTIFY**
Real-time event propagation: parser → core (WebSocket), processor → parser (photo download).

**✅ PostGIS**
Spatial queries: intersection, midpoint, shortest line. process_candidates_v2 SQL function.

**✅ Table-Specific Autovacuum**
Events: aggressive (0.02 scale factor, 1ms delay). Geo: conservative. Pending: moderate.

**✅ Monitoring**
pg_stat_statements + Prometheus exporter + Grafana dashboards.

**✅ 60-Minute Window**
`clean_old_events()` — DELETE old events + DROP empty partitions. Cron every 5 min.

### 5.3 Слабые стороны

**⚠️ No Connection Pooling (PgBouncer)**
Прямое подключение asyncpg → PostgreSQL. 25-30 backend processes. PgBouncer добавлен в конфиг, но image не построен.

**⚠️ max_connections=40**
С PgBouncer можно снизить до 20. Без PgBouncer — 40 это минимум для 3 сервисов.

**⚠️ No Read Replicas**
При росте нагрузки — single point of failure. Рекомендация: streaming replication.

**Оценка: 8/10**

---

## 6. Web (nginx)

### 6.1 Конфигурация

```
nginx
├── Reverse proxy → core:8080
├── Static files (HTML/CSS/JS)
├── WebSocket proxy (ws → core)
├── Media files (volume mount)
├── Rate limiting (edge level)
├── Gzip compression
├── Cache headers (static assets)
└── Security headers (CSP, X-Frame-Options)
```

### 6.2 Сильные стороны

**✅ Multi-Layer Rate Limiting**
- nginx edge: 10 req/s per IP (api), 1 req/s (auth)
- App level: 60 req/min per IP

**✅ WebSocket Proxy**
Long-lived connections (1h timeout), gzip off, buffering off.

**✅ Security Headers**
CSP, X-Frame-Options, X-Content-Type-Options, server_tokens off.

**✅ Static Asset Caching**
JS/CSS: 7d immutable. Images: 365d. HTML: no-cache.

### 6.3 Слабые стороны

**⚠️ worker_connections=512**
При 1000 WebSocket клиентов —不够. Рекомендация: 1024+.

**⚠️ No HTTP/2 Server Push**
Статические ресурсы можно push'ить для первого загрузки.

**Оценка: 8/10**

---

## 7. Common (Shared Libraries)

### 7.1 Структура

```
common/
├── settings.py          # Centralized configuration
├── db_adapter.py        # DBAdapter (parser/processor shared)
├── db/base.py           # Database class + create_pool factory
├── circuit_breaker.py   # Circuit breaker pattern
├── retry.py             # Exponential backoff retry
├── pg_listener.py       # LISTEN/NOTIFY with auto-reconnect
├── text_preprocessor.py # Text cleaning (HTML, emoji, Ukrainian normalization)
└── logging_config.py    # JSON structured logging
```

### 7.2 Сильные стороны

**✅ Shared DB Adapter**
Единый `DBAdapter` для parser/processor. Избегает дублирования.

**✅ Circuit Breaker**
Защита от каскадных сбоев. State machine: CLOSED → OPEN → HALF_OPEN.

**✅ PgNotifyListener**
Auto-reconnecting LISTEN/NOTIFY с backoff. Post-subscribe hook для catch-up.

**✅ Text Preprocessor**
Ukrainian → Russian normalization, emoji removal, hashtag cleanup, time removal.

**✅ Retry Utility**
Exponential backoff с configurable retryable exceptions.

### 7.3 Слабые стороны

**⚠️ settings.py Loaded at Import Time**
`settings = load_settings(require_jwt=False)` — выполняется при import. Если .env нет — crash.

**⚠️ No Configuration Validation**
Settings валидируются частично (JWT secret, postgres password). Остальные — raw env vars.

**Оценка: 8.5/10**

---

## 8. Инфраструктура

### 8.1 Docker Compose

| Сервис | Image | CPU | Memory | Healthcheck |
|--------|-------|-----|--------|-------------|
| postgres | survival_postgres | 1.0 | 1GB | pg_isready |
| parser | survival_parser | 0.5 | 256MB | heartbeat file |
| nlp_processor | survival_processor | 1.5 | 1GB | HTTP /health/ready |
| core | survival_core | 1.0 | 768MB | HTTP /health |
| web | survival_web | 0.5 | 128MB | curl /health/ready |
| prometheus | prom/prometheus | 0.5 | 512MB | — |
| grafana | grafana/grafana | 0.25 | 256MB | — |

### 8.2 Сильные стороны

**✅ Resource Limits**
Все сервисы с CPU/memory limits. Prevents OOM cascade.

**✅ Health Checks**
Kubernetes-style liveness/readiness probes.

**✅ Security**
`no-new-privileges`, `cap_drop: ALL`, minimal capabilities.

**✅ Logging**
json-file driver с ротацией (10MB × 5 files).

**✅ Networks**
3 сети: frontend (web→core), backend (core↔parser↔processor), db (internal).

### 8.3 Слабые стороны

**⚠️ No Rolling Updates**
`docker compose up -d` — полная остановка перед стартом. Рекомендация: Docker Swarm/K8s.

**⚠️ No Resource Reservations**
Есть limits, но нет reservations. При contention — garbled scheduling.

**⚠️ PgBouncer Not Built**
Dockerfile.pgbouncer создан, но image не построен (edoburu/pgbouncer not found).

> **Footnote (2026-09-04):** PgBouncer was removed entirely in v5.0.0. Connection pooling now handled by asyncpg's built-in `asyncpg.Pool` with `min_size=1/max_size=10` (R-DB15). See plan `1788523952652-pgBouncer-removal.md` for details. This section is retained as historical analysis.

**Оценка: 7.5/10**

---

## 9. CI/CD Pipeline

### 9.1 Стадии

```
.pre → security-scan → test → build → image-security → deploy
```

### 9.2 Сильные стороны

**✅ Multi-Stage Pipeline**
Security → Test → Build → Scan → Deploy.

**✅ Security Scanning**
Bandit (Python SAST), pip-audit (dependencies), hadolint (Dockerfile).

**✅ Parallel Tests**
Backend tests, frontend tests, matrix tests (4 env combos).

**✅ Local Deploy**
`CI_LOCAL=true` — полный деплой одной командой.

### 9.3 Слабые стороны

**⚠️ No Integration Tests в CI**
Integration tests требуют PostgreSQL service container. Не запускаются в local mode.

**⚠️ No Canary/Blue-Green Deploy**
Деплой — полная замена. Рекомендация: blue-green или canary.

**Оценка: 8/10**

---

## 10. Сводная таблица

| Компонент | Оценка | Ключевые проблемы |
|-----------|--------|-------------------|
| **Core (API)** | 8.5/10 | Single instance, in-memory cache |
| **Parser** | 8/10 | Session management, duplicate code |
| **Processor** | 8/10 | Keyword classifier, geo index size |
| **PostgreSQL** | 8/10 | No PgBouncer built, no read replicas |
| **Web (nginx)** | 8/10 | worker_connections |
| **Common** | 8.5/10 | Settings import-time loading |
| **Infrastructure** | 7.5/10 | No rolling updates, PgBouncer missing |
| **CI/CD** | 8/10 | No integration tests in CI |
| **Overall** | **8/10** | |

---

## 11. Топ-5 рекомендаций по улучшению

| # | Рекомендация | Приоритет | Ожидаемый эффект |
|---|-------------|-----------|------------------|
| 1 | **Расширить geo-индекс** до 5,000+ объектов (OSM import) | 🔴 Critical | +50% geo-match rate |
| 2 | **Построить PgBouncer image** и включить в pipeline | 🟡 High | -72% backend connections |
| 3 | **ML-based layer classifier** вместо keyword matching | 🟡 High | +30% layer accuracy |
| 4 | **Redis cache** для multi-replica core | 🟢 Medium | Horizontal scaling |
| 5 | **Integration tests в CI** с PostgreSQL service | 🟢 Medium | Earlier bug detection |

---

*Architecture review generated automatically | Survival Map v5.0.0*
