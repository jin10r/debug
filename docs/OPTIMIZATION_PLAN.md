# Survival Map — План оптимизации и обзор проекта

**Дата:** 13 июля 2026  
**Версия:** v3 (Parser → Processor → PostgreSQL → Core)  
**Статус:** Документ для планирования следующих итераций

---

## 1. Архитектурный обзор

### 1.1 Текущая архитектура (v3)

```
Telegram (MTProto)
       │
       ▼
┌─────────────────────────────────────┐
│  parser (kurigram)                  │
│  ~200-400ms/сообщение              │
│  4 workers, asyncio.Queue          │
└──────────────┬──────────────────────┘
               │ INSERT INTO pending_events
               ▼
┌─────────────────────────────────────┐
│  processor (NLP pipeline)           │
│  pymorphy3 + rapidfuzz + PostGIS   │
│  5 workers, SKIP LOCKED            │
└──────────────┬──────────────────────┘
               │ INSERT INTO events + pg_notify
               ▼
┌─────────────────────────────────────┐
│  postgres (PostGIS 15-3.3)         │
│  pg_cron + pg_stat_statements      │
│  events (partitioned by day)        │
└──────────────┬──────────────────────┘
               │ LISTEN/NOTIFY → WebSocket
               ▼
┌─────────────────────────────────────┐
│  core (aiohttp + aiogram)          │
│  REST API + WebSocket + JWT auth   │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  web (nginx + Leaflet PWA)          │
│  Reverse proxy + статика            │
└─────────────────────────────────────┘
```

### 1.2 Ключевые компоненты

| Сервис | Технология | Ресурсы | Назначение |
|--------|-----------|---------|------------|
| **parser** | kurigram (MTProto) | 0.5 CPU / 256MB | Telegram клиент, предобработка текста, фото |
| **processor** | pymorphy3 + rapidfuzz | 1.5 CPU / 1GB | NLP pipeline: токенизация → морфология → geo matching |
| **postgres** | PostgreSQL 15 + PostGIS | 1.0 CPU / 1GB | Хранение событий, PostGIS-вычисления, триггеры |
| **core** | aiohttp + aiogram | 1.0 CPU / 768MB | REST API, WebSocket, JWT auth, Telegram bot |
| **web** | nginx + Leaflet PWA | 0.5 CPU / 128MB | Reverse proxy, статика фронтенда |

### 1.3 Поток данных

```
1. parser получает сообщение из Telegram канала
2. Предобработка: strip_tail → preprocess_light → word_tokenizer
3. Запись в pending_events (ON CONFLICT DO NOTHING)
4. processor забирает из pending_events (SKIP LOCKED)
5. NLP pipeline: tokenize → lemmatize → classify layer → find_geo
6. GeoMatcher: Tier 1 (stem exact) → Tier 2 (surface typo)
7. SemanticResolver: pre-filter → Ollama (опционально)
8. INSERT в events через process_candidates() (PostGIS)
9. pg_notify → core → WebSocket → фронтенд
```

---

## 2. Анализ текущего состояния

### 2.1 Сильные стороны

| № | Аспект | Описание |
|---|--------|----------|
| 1 | **Изоляция сетей** | `db` сеть internal: true — PostgreSQL недоступна извне |
| 2 | **Idempotent INSERT** | ON CONFLICT DO NOTHING — ретраи не создают дубликатов |
| 3 | **Один roundtrip** | Геометрия вычисляется внутри CTE, INSERT, meta-update и pg_notify — один запрос |
| 4 | **Tiered matching** | 3 уровня поиска улиц (стемминг → семантика → нечёткий) + LLM Tier-2 |
| 5 | **WebSocket realtime** | pg_notify → WebSocket — события доходят до фронта за <100ms |
| 6 | **Graceful shutdown** | drain очередь (20s), stop_grace_period (30s) — не теряет сообщения |
| 7 | **Hardened контейнеры** | cap_drop: ALL, no-new-privileges, tmpfs, readonly rootfs |
| 8 | **Healthchecks** | У всех сервисов, parser/processor используют heartbeat-файлы |
| 9 | **Без Redis** | In-memory LRU кэш в core — меньше движущихся частей |
| 10 | **pg_notify** | Встроенный брокер сообщений без Kafka/RabbitMQ |
| 11 | **PWA фронтенд** | Service worker, offline-first, кэширование событий на клиенте |
| 12 | **Rate limiting** | Двухуровневый (nginx edge + app-level) |
| 13 | **Prometheus метрики** | core экспортирует метрики |
| 14 | **Морфологический анализатор** | pymorphy3 + snowballstemmer — устойчив к OOV-проперам |
| 15 | **PostGIS сценарии** | 10 геометрических сценариев через process_candidates() |

### 2.2 Слабые стороны и риски

| № | Проблема | Серьёзность | Описание |
|---|----------|-------------|----------|
| 1 | **Нет оркестрации** | 🔴 Критично | docker-compose для продакшена — нет автоскейлинга, rolling update |
| 2 | **Одиночный postgres** | 🔴 Критично | Одна нода — нет высокой доступности, single point of failure |
| 3 | **Нет SSL/TLS** | 🔴 Критично | nginx без HTTPS, JWT передаются в открытом виде |
| 4 | **JWT секрет эфемерный** | 🟡 Средне | Автогенерация при старте — сброс всех токенов при рестарте |
| 5 | **X-Forwarded-For спуфинг** | 🔴 Критично | nginx `set_real_ip_from` не настроен |
| 6 | **Heartbeat healthcheck** | 🟡 Средне | Файловый heartbeat может не отловить зависший event loop |
| 7 | **Нет CI/CD** | 🟡 Средне | Только .gitlab-ci.yml базовая настройка |
| 8 | **TypeScript strict: false** | 🟡 Средне | Фронтенд без строгой типизации |
| 9 | **parser не распределён** | 🟡 Средне | Один контейнер на все Telegram каналы — единая точка отказа |
| 10 | **Нет distributed tracing** | 🟡 Средне | Нет OpenTelemetry для отслеживания полного цикла |
| 11 | **session.session в plaintext** | 🔴 Критично | Файл сессии Telegram монтируется без шифрования |
| 12 | **Polling вместо PUSH** | 🟡 Средне | processor poll с интервалом 0.5s — задержка при малой нагрузке |
| 13 | **ProcessPoolExecutor** | 🟢 Низко | `_MAX_WORKER_CONCURRENCY = 4` — может быть мало при высокой нагрузке |
| 14 | **SemanticResolver Ollama** | 🟢 Низко | Ollama внешний依赖 — может быть недоступен |

---

## 3. План оптимизации по приоритетам

### 3.1 P0 — Критично (нужно сделать немедленно)

#### 3.1.1 Настроить HTTPS (nginx)

**Проблема:** JWT и данные передаются в открытом виде.

**Решение:**
```nginx
# nginx.conf
server {
    listen 443 ssl http2;
    ssl_certificate /etc/nginx/ssl/cert.pem;
    ssl_certificate_key /etc/nginx/ssl/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    # HSTS
    add_header Strict-Transport-Security "max-age=31530000; includeSubDomains" always;
}

server {
    listen 80;
    return 301 https://$host$request_uri;
}
```

**Срок:** 1-2 дня  
**Ресурсы:** Let's Encrypt (бесплатно) или коммерческий сертификат

#### 3.1.2 Настроить X-Forwarded-For в nginx

**Проблема:** Rate limiting и логирование получают фейковые IP.

**Решение:**
```nginx
# nginx.conf
set_real_ip_from 172.16.0.0/12;  # Docker network
real_ip_header X-Forwarded-For;
real_ip_recursive on;
```

**Срок:** 1 день

#### 3.1.3 Вынести JWT_SECRET в Docker secret

**Проблема:** Эфемерный секрет сбрасывается при рестарте.

**Решение:**
```yaml
# docker-compose.yml
services:
  core:
    secrets:
      - jwt_secret

secrets:
  jwt_secret:
    file: ./secrets/jwt_secret.txt

# core/settings.py
def _resolve_jwt_secret(env: Env) -> str:
    secret_file = os.environ.get('JWT_SECRET_FILE')
    if secret_file and os.path.exists(secret_file):
        with open(secret_file) as f:
            return f.read().strip()
    return _resolve_jwt_secret(env)
```

**Срок:** 1 день

#### 3.1.4 Шифрование session.session

**Проблема:** Файл сессии Telegram в открытом виде.

**Решение:**
- Docker secret для файла сессии
- Или: volume с шифрованием (LUKS/dm-crypt)
- Или: монтирование как tmpfs + загрузка из vault при старте

**Срок:** 2-3 дня

### 3.2 P1 — Высокий приоритет

#### 3.2.1 PostgreSQL High Availability

**Проблема:** Одна нода — нет отказоустойчивости.

**Решение (постепенное):**

1. **Phase 1: Streaming Replica** (1-2 дня)
   - Настроить primary-replica replication
   - Core читает из replica (read-only queries)
   - Автоматическое переключение при падении primary

2. **Phase 2: Patroni** (1 неделя)
   - Автоматический failover
   - Leader election через etcd/Consul
   - Health checks и автоматическое восстановление

3. **Phase 3: PgBouncer** (2-3 дня)
   - Connection pooling перед PostgreSQL
   - Уменьшение нагрузки на postgres process model

```yaml
# docker-compose.yml (Phase 1)
services:
  postgres:
    environment:
      POSTGRES_PRIMARY: postgres
      POSTGRES_REPLICA: postgres-replica
  
  postgres-replica:
    image: survival_postgres:latest
    environment:
      POSTGRES_PRIMARY: postgres
      POSTGRES_ROLE: replica
    volumes:
      - postgres_replica_data:/var/lib/postgresql/data
```

#### 3.2.2 Circuit breaker для HTTP вызовов

**Проблема:** parser/processor вызывают Ollama без circuit breaker — при падении сервиса зависают.

**Решение:**
```python
# processor/semantic_resolver.py
import pybreaker

OLLAMA_BREAKER = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    name="ollama",
)

@OLLAMA_BREAKER
async def _model_call(self, text, candidates):
    # ... existing code
```

**Срок:** 1 день

#### 3.2.3 OpenTelemetry tracing

**Проблема:** Нет distributed tracing — невозможно отследить полный цикл.

**Решение:**
```python
# requirements.txt
opentelemetry-api~=1.20
opentelemetry-sdk~=1.20
opentelemetry-exporter-otlp~=1.20
opentelemetry-instrumentation-aiohttp~=0.41
opentelemetry-instrumentation-asyncpg~=0.41

# core/utils/tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://jaeger:4317"))
provider.add_span_processor(processor)
trace.set_tracer_provider(provider)
```

**Срок:** 2-3 дня

#### 3.2.4 Заменить heartbeat healthcheck на HTTP

**Проблема:** Файловый heartbeat не проверяет реальное состояние.

**Решение:**
```python
# parser/monitoring.py
from aiohttp import web

class ParserBot:
    async def _start_health_server(self):
        app = web.Application()
        app.router.add_get('/health', self._health_handler)
        app.router.add_get('/health/ready', self._health_ready_handler)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8765)
        await site.start()
    
    async def _health_handler(self, request):
        return web.json_response({
            'status': 'healthy',
            'messages_processed': self._messages_processed,
            'queue_size': self._pending_queue.qsize(),
            'workers': len(self._worker_tasks),
        })
    
    async def _health_ready_handler(self, request):
        # Проверяем что все компоненты готовы
        ready = (
            self.db is not None and self.db.is_connected and
            self.app is not None and self.app.is_connected
        )
        status = 200 if ready else 503
        return web.json_response({'ready': ready}, status=status)
```

**Срок:** 1 день

#### 3.2.5 PostgreSQL → pg_partman для TTL

**Проблема:** pg_cron DELETE неэффективен для high-churn таблицы.

**Решение:**
```sql
-- Вместо DELETE через pg_cron:
CREATE EXTENSION pg_partman;

SELECT partman.create_parent(
    'public.events', 
    'event_time', 
    'native', 
    'daily'
);

-- Настройка автоматического создания партиций
UPDATE partman.part_config 
SET infinite_time_partitions = true,
    retention = '1 hour',
    retention_keep_table = false
WHERE parent_table = 'public.events';
```

**Срок:** 2-3 дня  
**Выигрыш:** O(1) удаление вместо O(n) DELETE

#### 3.2.6 JWT cache key hash

**Проблема:** Ключ кэша — raw token, не hash (security best practice).

**Решение:**
```python
# core/middlewares/auth.py
import hashlib

class JWTVerifier:
    def __init__(self):
        self._cache = {}  # {hash(token): (user_data, expiry)}
    
    def _token_hash(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()[:16]
    
    def verify(self, token: str) -> Optional[dict]:
        h = self._token_hash(token)
        cached = self._cache.get(h)
        if cached and cached[1] > time.time():
            return cached[0]
        # ... verify and cache with hash key
```

**Срок:** 1 день

### 3.3 P2 — Средний приоритет

#### 3.3.1 Prometheus + Grafana

**Проблема:** Метрики экспортируются, но нет дашборда.

**Решение:**
```yaml
# docker-compose.yml
services:
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
  
  grafana:
    image: grafana/grafana:latest
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/dashboards:/var/lib/grafana/dashboards
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
```

**Срок:** 2-3 дня

#### 3.3.2 CI/CD pipeline

**Проблема:** Нет автоматической сборки и деплоя.

**Решение (GitLab CI):**
```yaml
# .gitlab-ci.yml
stages:
  - test
  - build
  - deploy

test:
  stage: test
  script:
    - pip install -r requirements-dev.txt
    - pytest tests/ -v
    - mypy core/ parser/ processor/

build:
  stage: build
  script:
    - docker-compose build
    - docker-compose push
  only:
    - main

deploy:
  stage: deploy
  script:
    - docker-compose pull
    - docker-compose up -d
  only:
    - main
  when: manual
```

**Срок:** 2-3 дня

#### 3.3.3 Стриминг-реплика PostgreSQL

**Проблема:** Core читает из primary — лишняя нагрузка.

**Решение:**
```python
# core/db/dbconnect.py
class Request:
    def __init__(self, primary_db, replica_db=None):
        self.db = primary_db  # writes
        self.replica = replica_db or primary_db  # reads
    
    async def get_filtered_events_as_geojson(self, ...):
        # Read queries use replica
        return await self.replica.events.get_filtered_events_as_geojson(...)
```

**Срок:** 1-2 дня

#### 3.3.4 Sliding window rate limiter

**Проблема:** Fixed-window rate limiter не идеален.

**Решение:**
```python
# core/middlewares/ratelimit.py
from collections import deque

class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window = window_seconds
        self._requests: dict[str, deque] = {}
    
    def is_allowed(self, key: str) -> bool:
        now = time.time()
        if key not in self._requests:
            self._requests[key] = deque()
        
        # Remove old entries
        while self._requests[key] and self._requests[key][0] < now - self.window:
            self._requests[key].popleft()
        
        if len(self._requests[key]) >= self.limit:
            return False
        
        self._requests[key].append(now)
        return True
```

**Срок:** 1 день

#### 3.3.5 Async tool execution в SemanticResolver

**Проблема:** Инструменты выполняются последовательно.

**Решение:**
```python
# processor/semantic_resolver.py
async def _model_call_with_tools(self, text, candidates):
    # Если нужно вызвать несколько инструментов параллельно
    results = await asyncio.gather(
        self._search_geo(query1),
        self._search_geo(query2),
        return_exceptions=True
    )
    return results
```

**Срок:** 1 день

#### 3.3.6 Кэширование ответов Ollama

**Проблема:** Одинаковые адреса обрабатываются повторно.

**Решение:**
```python
# processor/semantic_resolver.py
from functools import lru_cache
import hashlib

class SemanticResolver:
    def __init__(self):
        self._response_cache = {}  # {hash(text+candidates): result}
        self._cache_max = 1000
    
    def _cache_key(self, text: str, candidates: list) -> str:
        data = text + str(sorted(c.get('geo_id') for c in candidates))
        return hashlib.md5(data.encode()).hexdigest()
    
    async def resolve(self, text, tokens, lemmas, candidates):
        key = self._cache_key(text, candidates)
        if key in self._response_cache:
            return self._response_cache[key]
        
        result = await self._resolve_uncached(text, tokens, lemmas, candidates)
        
        if result and len(self._response_cache) < self._cache_max:
            self._response_cache[key] = result
        
        return result
```

**Срок:** 1 день

### 3.4 P3 — Низкий приоритет

#### 3.4.1 Kubernetes миграция

**Срок:** 2-4 недели  
**Этапы:**
1. Заменить pg_notify на NATS JetStream
2. Parser → Deployment с HPA (autoscale по длине очереди)
3. Postgres → StatefulSet + Patroni (HA кластер)
4. Core → Deployment с HPA (autoscale по WebSocket)
5. Web → Ingress + Cert-Manager (TLS termination)
6. Metrics → Prometheus Operator + Grafana

#### 3.4.2 GraphQL API

**Срок:** 1 неделя  
**Преимущества:**
- Клиент запрашивает только нужные данные
- Subscription для real-time обновлений
- Автодокументация

#### 3.4.3 Elasticsearch для полнотекстового поиска

**Срок:** 2-3 дня  
**Преимущества:**
- Быстрый поиск по описаниям событий
- Faceted search (по слоям, времени, локации)
- Анализаторы для русского языка

#### 3.4.4 Rate limiting per-user

**Срок:** 1 день  
**Реализация:**
```python
# core/middlewares/ratelimit.py
class UserRateLimiter:
    def __init__(self, default_limit=60, window_seconds=60):
        self._limits = {
            '/api/events': (100, 60),      # 100/min
            '/api/auth/refresh': (10, 60),  # 10/min
            '/ws': (30, 60),                # 30 connections/min
        }
```

---

## 4. Дорожная карта

### 4.1 Фаза 1: Безопасность (1-2 недели)

| Задача | Приоритет | Срок | Ресурсы |
|--------|-----------|------|---------|
| Настроить HTTPS | P0 | 1-2 дня | Let's Encrypt |
| X-Forwarded-For в nginx | P0 | 1 день | - |
| JWT_SECRET в Docker secret | P0 | 1 день | - |
| Шифрование session.session | P0 | 2-3 дня | - |
| Circuit breaker для Ollama | P1 | 1 день | pybreaker |

### 4.2 Фаза 2: Надёжность (2-3 недели)

| Задача | Приоритет | Срок | Ресурсы |
|--------|-----------|------|---------|
| PostgreSQL streaming replica | P1 | 1-2 дня | Второй контейнер |
| HTTP healthcheck вместо heartbeat | P1 | 1 день | - |
| JWT cache key hash | P1 | 1 день | - |
| OpenTelemetry tracing | P1 | 2-3 дня | Jaeger |
| pg_partman для TTL | P1 | 2-3 дня | - |

### 4.3 Фаза 3: Производительность (1-2 недели)

| Задача | Приоритет | Срок | Ресурсы |
|--------|-----------|------|---------|
| Prometheus + Grafana | P2 | 2-3 дня | - |
| CI/CD pipeline | P2 | 2-3 дня | GitLab CI |
| Sliding window rate limiter | P2 | 1 день | - |
| Кэширование Ollama ответов | P2 | 1 день | - |
| Async tool execution | P2 | 1 день | - |

### 4.4 Фаза 4: Масштабирование (1-2 месяца)

| Задача | Приоритет | Срок | Ресурсы |
|--------|-----------|------|---------|
| Kubernetes миграция | P3 | 2-4 недели | K8s кластер |
| NATS JetStream | P3 | 1 неделя | - |
| GraphQL API | P3 | 1 неделя | strawberry-graphql |
| Elasticsearch | P3 | 2-3 дня | - |

---

## 5. Метрики успеха

### 5.1 Текущие метрики

| Метрика | Текущее значение | Целевое значение |
|---------|------------------|------------------|
| Latency (p50) | ~200ms | <100ms |
| Latency (p95) | ~500ms | <200ms |
| Latency (p99) | ~1s | <500ms |
| Throughput | ~50 msg/s | ~100 msg/s |
| Error rate | ~1% | <0.1% |
| Uptime | ~99% | 99.9% |
| Time to first event | <500ms | <200ms |

### 5.2 Метрики для отслеживания

```python
# core/utils/metrics.py
from prometheus_client import Counter, Histogram, Gauge

# Throughput
messages_processed = Counter(
    'parser_messages_processed_total',
    'Total messages processed',
    ['source', 'status']
)

# Latency
processing_latency = Histogram(
    'parser_processing_latency_seconds',
    'Message processing latency',
    ['stage'],  # preprocess, tokenize, match, insert
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Queue size
pending_queue_size = Gauge(
    'parser_pending_queue_size',
    'Current size of pending queue'
)

# Geo matching
geo_match_rate = Counter(
    'geo_match_total',
    'Geo matching results',
    ['source', 'strategy']  # stem_exact, surface_typo, random
)

# PostGIS
postgis_query_duration = Histogram(
    'postgis_query_duration_seconds',
    'PostGIS query execution time',
    ['function']  # process_candidates, geo_execute_scenario
)
```

---

## 6. Рекомендации по коду

### 6.1 parser/monitoring.py

**Текущие проблемы:**
1. Heartbeat через файл — ненадёжно
2. Нет HTTP healthcheck endpoint
3. `stop_grace_period: 60s` — слишком долго для 4 workers

**Рекомендации:**
```python
# Добавить HTTP healthcheck
async def _start_health_server(self):
    from aiohttp import web
    app = web.Application()
    app.router.add_get('/health', self._health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8765)
    await site.start()
```

### 6.2 processor/main.py

**Текущие проблемы:**
1. `_MAX_WORKER_CONCURRENCY = 8` — хардкод
2. Polling interval 0.5s — можно уменьшить до 0.1s
3. Нет graceful drain при shutdown

**Рекомендации:**
```python
# Сделать конфигурируемым
self._worker_concurrency = settings.processor.worker_concurrency
self._poll_interval = settings.processor.poll_interval

# Добавить drain при shutdown
async def shutdown(self):
    # Ждём завершения текущих задач
    for task in self._worker_tasks:
        task.cancel()
    await asyncio.gather(*self._worker_tasks, return_exceptions=True)
```

### 6.3 processor/geo_matcher.py

**Текущие проблемы:**
1. `ProcessPoolExecutor(max_workers=None)` — может создать слишком много процессов
2. Нет кэширования результатов fuzzy matching

**Рекомендации:**
```python
# Ограничить количество воркеров
self._executor = ProcessPoolExecutor(max_workers=4)

# Добавить кэш
from functools import lru_cache

@lru_cache(maxsize=1000)
def _cached_fuzzy_match(query: str, phrases_key: str, threshold: float):
    return _fuzzy_match(query, phrases_key, threshold)
```

### 6.4 processor/semantic_resolver.py

**Текущие проблемы:**
1. Нет circuit breaker для Ollama
2. Нет кэширования ответов
3. Timeout 10s — может быть мало для сложных запросов

**Рекомендации:**
```python
import pybreaker

OLLAMA_BREAKER = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=30,
    name="ollama",
)

@OLLAMA_BREAKER
async def _model_call(self, text, candidates):
    # ... existing code
```

### 6.5 core/db/db_events.py

**Текущие проблемы:**
1. Дублирование SQL-запросов
2. Нет batching для bulk operations
3. Много `try/except` — можно упростить

**Рекомендации:**
```python
# Использовать CTE для атомарности
async def add_event(self, ...):
    query = """
        WITH inserted AS (
            INSERT INTO events (...)
            VALUES (...)
            RETURNING id
        ),
        meta_upd AS (
            UPDATE events_meta SET version = version + 1
            WHERE id = 1
        )
        SELECT id FROM inserted
    """
    return await self.db.fetchval(query, ...)
```

---

## 7. Заключение

### 7.1 Краткосрочные действия (1-2 недели)

1. **Безопасность:** HTTPS, X-Forwarded-For, JWT_SECRET, session.session
2. **Надёжность:** Circuit breaker, HTTP healthcheck, JWT cache hash
3. **Мониторинг:** OpenTelemetry tracing, Prometheus + Grafana

### 7.2 Среднесрочные действия (1-2 месяца)

1. **PostgreSQL HA:** Streaming replica, Patroni, pg_partman
2. **CI/CD:** Автоматическая сборка и деплой
3. **Производительность:** Кэширование, async execution, sliding window rate limiter

### 7.3 Долгосрочные действия (3-6 месяцев)

1. **Kubernetes:** Миграция с docker-compose
2. **NATS JetStream:** Замена pg_notify
3. **GraphQL:** Новый API для фронтенда
4. **Elasticsearch:** Полнотекстовый поиск

### 7.4 Ожидаемые результаты

| Метрика | До оптимизации | После оптимизации |
|---------|----------------|-------------------|
| Uptime | 99% | 99.9% |
| Latency (p95) | 500ms | 200ms |
| Throughput | 50 msg/s | 100 msg/s |
| Error rate | 1% | 0.1% |
| Time to recovery | 5-10 мин | <1 мин |
| Security score | C | A |

---

## 8. Приложение

### 8.1 Структура проекта

```
survival_map/
├── core/                    # API сервер (aiohttp + aiogram)
│   ├── api/                 # REST API endpoints
│   ├── db/                  # Database operations
│   ├── handlers/            # Telegram bot handlers
│   ├── middlewares/          # JWT, CSRF, rate limiting
│   ├── utils/               # Logging, metrics, cache
│   ├── app_factory.py       # Application factory
│   ├── models.py            # Pydantic models
│   └── settings.py          # Configuration
├── parser/                  # Telegram клиент (kurigram)
│   ├── monitoring.py        # Main entry point
│   ├── db_adapter.py        # Database adapter
│   └── text_preprocessor.py # Text preprocessing
├── processor/               # NLP pipeline
│   ├── main.py              # Main entry point
│   ├── geo_matcher.py       # Geo matching
│   ├── morphological.py     # Morphological analysis
│   ├── phonetic_index.py    # Phonetic indexing
│   ├── semantic_resolver.py # Semantic resolution
│   ├── layer_classifier.py  # Layer classification
│   ├── word_tokenizer.py    # Word tokenization
│   ├── text_preprocessor.py # Text preprocessing
│   ├── db_adapter.py        # Database adapter
│   └── settings.py          # Configuration
├── postgres/                # PostgreSQL configuration
│   ├── config/              # postgresql.conf, pg_hba.conf
│   ├── data/                # geo.csv, stopwords.csv
│   └── init-scripts/        # SQL initialization scripts
├── web/                     # Frontend (Leaflet PWA)
│   ├── js/                  # JavaScript modules
│   ├── css/                 # Styles
│   ├── assets/              # Static assets
│   ├── map.html             # Main map page
│   └── package.json         # Node.js dependencies
├── tests/                   # Test suite
├── scripts/                 # Utility scripts
├── docs/                    # Documentation
├── docker-compose.yml       # Docker orchestration
├── Dockerfile.core          # Core service image
├── Dockerfile.parser        # Parser service image
├── Dockerfile.processor     # Processor service image
├── Dockerfile.postgres      # PostgreSQL image
├── Dockerfile.web           # Web service image
└── nginx.conf               # Nginx configuration
```

### 8.2 Зависимости

**Python (core):**
- aiohttp~=3.9
- aiogram~=3.4
- asyncpg~=0.29
- pyjwt~=2.8
- pydantic~=2.0
- prometheus-client~=0.19

**Python (parser):**
- kurigram==2.2.9
- asyncpg~=0.29

**Python (processor):**
- asyncpg~=0.29
- mawo-pymorphy3==1.0.4
- snowballstemmer~=2.2
- rapidfuzz~=3.0

**Node.js (web):**
- webpack
- leaflet
- maplibre-gl

### 8.3 Ссылки на документацию

- [ARCHITECTURE.md](./ARCHITECTURE.md) — Архитектура микросервисов
- [POSTGRES_OPTIMIZATION_PLAN.md](./POSTGRES_OPTIMIZATION_PLAN.md) — Оптимизация PostgreSQL
- [core.md](./core.md) — Документация core сервиса
- [parser.md](./parser.md) — Документация parser сервиса
- [web.md](./web.md) — Документация веб-интерфейса

---

*Документ создан: 13 июля 2026*  
*Последнее обновление: 13 июля 2026*
