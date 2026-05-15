# 🗺️ Survival Map v2 — Real-time Event Mapping Platform

**Real-time geospatial event mapping for Odesa, Ukraine**

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![PostgreSQL 15](https://img.shields.io/badge/postgresql-15-blue.svg)](https://www.postgresql.org/)
[![PostGIS 3.3](https://img.shields.io/badge/postgis-3.3-green.svg)](https://postgis.net/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 Overview

A microservices platform that monitors Telegram channels for user-submitted reports about local events (police checkpoints, bus movements, traffic incidents, etc.), extracts location entities using fuzzy string matching, geocodes them, and displays them on an interactive web map in real time.

### Key Features

- 🔄 **Real-time updates** via WebSocket (sub-second delivery)
- 🔍 **Fuzzy entity matching** using Rapidfuzz with sliding window algorithm (1-5ms)
- 🗺️ **PostGIS geocoding** with multiple strategies (single match, intersection, random fallback)
- 🤖 **Telegram bot** with WebApp integration
- 🔐 **JWT authentication** with Telegram HMAC validation
- 📊 **Prometheus metrics** for monitoring
- 🐳 **Docker microservices** with resource limits (~2GB RAM total)

---

## 🏗️ Architecture

```
  Telegram Channel
        |
        v
  +----------+        +----------------+
  |  Parser  |------->|  PostgreSQL    |
  | (Pyrogram|  events|  + PostGIS     |
  | +Rapidfuzz)       +-------+--------+
  +----------+                |
                              | events_new NOTIFY
                              v
  +----------+        +-------+--------+
  |  Nginx   |<------>|  App (aiohttp) |
  | (reverse |  proxy |  + aiogram bot |
  |  proxy)  |        |  + WebSocket   |
  +----------+        +----------------+
        |                     |
        v                     v
   Static files         Redis (cache)
   (web frontend)
```

### Services

| Service | Tech Stack | Resources | Purpose |
|---------|-----------|-----------|---------|
| **Parser** | Pyrogram + Rapidfuzz | 0.25 CPU, 256MB RAM | Monitor Telegram, extract entities |
| **PostgreSQL** | PostGIS 3.3 + pg_cron | 0.5 CPU, 512MB RAM | Store events, streets, compute geometry |
| **App** | aiohttp + aiogram | 0.25 CPU, 256MB RAM | Web API, WebSocket, bot |
| **Redis** | Redis 7 | 0.1 CPU, 128MB RAM | Cache, sessions, replay protection |
| **Nginx** | Nginx | 0.1 CPU, 64MB RAM | Reverse proxy, static files |

---

## 🚀 Quick Start

### Prerequisites

- Docker 20+
- Docker Compose 2.0+
- 2GB RAM minimum
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))

### Installation

```bash
# 1. Clone repository
git clone <repository_url>
cd rapid_window

# 2. Configure environment
cp .env.example .env
# Edit .env and set:
#   - BOT_TOKEN (from BotFather)
#   - CHANNEL_ID (Telegram channel to monitor)
#   - JWT_SECRET (generate: python -c "import secrets; print(secrets.token_urlsafe(32))")

# 3. Start services
docker-compose up -d

# 4. Check logs
docker-compose logs -f app

# 5. Open in browser
# http://localhost
```

### Environment Variables

**Required:**
```env
# Telegram
BOT_TOKEN=123456:ABC-DEF...
CHANNEL_ID=-1001234567890

# Database
POSTGRES_PASSWORD=secure_password

# JWT (MUST be changed in production!)
JWT_SECRET=<min-32-characters-secret>
```

**Optional:**
```env
# Feature flags
TELEGRAM_VALIDATION_ENABLED=true
ENABLE_RANDOM_POINTS=true

# Map settings
MAP_CENTER_LAT=46.49804
MAP_CENTER_LNG=30.83135

# Redis
REDIS_PASSWORD=secure_redis_password
```

---

## 📊 Data Flow

1. **Parser** reads Telegram messages from monitored channel
2. **Text cleaning**: HTML stripping, Cyrillic normalization, truncation at "Сообщить" marker
3. **Entity matching**: Sliding window algorithm (bigrams → unigrams) with Rapidfuzz
4. **Layer detection**: Keyword-based classification (cops/bus/traffic/pig)
5. **Database insertion**: PostgreSQL `process_location_smart()` computes geometry
6. **Real-time broadcast**: PG NOTIFY → asyncpg LISTEN → WebSocket → web clients
7. **Auto-cleanup**: pg_cron deletes events older than 1 hour (every 5 minutes)

### Entity Matching Pipeline

```
Input text: "Преображенская в сторону Софиевской"
    ↓
Clean: "преображенская в сторону софиевской"
    ↓
Stage 1 - Bigrams (window=2):
  - "преображенская в" → no match
  - "в сторону" → skip (stopwords)
  - "сторону софиевской" → Софиевская (0.85) ✅
    ↓
Stage 2 - Unigrams:
  - "преображенская" → Преображенская (0.92) ✅
  - "софиевской" → already found
    ↓
Result: [Преображенская (0.92), Софиевская (0.85)]
Strategy: centroid (two streets → midpoint)
```

---

## 📡 API Reference

### Events

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/events` | GET | Get events snapshot (GeoJSON) |
| `/api/events/status` | GET | Get metadata (version, max_event_id) |
| `/api/events/updates` | POST | Get incremental updates (after_id, limit) |
| `/api/events/streets` | GET | Get all streets with synonyms |

### Location

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/location` | POST | Search location by text query |
| `/api/location/batch` | POST | Batch location search |

### Authentication

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/validation-config` | POST | Get validation config |
| `/api/validate-init` | POST | Validate Telegram initData |
| `/api/auth/refresh` | POST | Refresh access token |

### Health & Cache

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Basic health check |
| `/health/live` | GET | Liveness probe |
| `/health/ready` | GET | Readiness probe |
| `/health/detailed` | GET | Detailed service status |
| `/api/cache/manifest` | POST | Get cache manifest |
| `/api/cache/status` | POST | Get cache status |

### WebSocket

**Endpoint:** `ws://localhost/ws`

**Protocol:**
1. Connect with Telegram initData in first message
2. Server validates and sends initial events snapshot
3. Server broadcasts new events in real-time
4. Client can send time filter changes

---

## 🔍 Entity Search (Sliding Window)

The core entity search algorithm uses a **two-stage sliding window** approach:

### Configuration

```python
# parser/message_processor.py
MAX_ENTITIES = 5              # Max entities per message
MAX_CANDIDATES = 3           # Max candidates per entity
WINDOW_SIZE = 2              # Bigram window size
DEFAULT_SIMILARITY_THRESHOLD = 0.67  # Fuzzy matching threshold
```

### Algorithm

1. **Stage 1 - Bigrams**: Check all 2-word combinations with higher priority
2. **Stage 2 - Unigrams**: Check individual words if capacity remains
3. **Deduplication**: Each street_id appears only once
4. **Sorting**: Bigrams first, then unigrams

### Performance

| Metric | Value |
|--------|-------|
| **Search latency** | 1-5 ms |
| **Accuracy** | 85-95% |
| **Memory usage** | ~10 MB |
| **False positives** | <5% |

**Detailed analysis:** See [docs/SLIDING_WINDOW_ANALYSYS.md](docs/SLIDING_WINDOW_ANALYSYS.md)

---

## 🗄️ Database Schema

### Core Tables

**streets**
- `id` (SERIAL PRIMARY KEY)
- `names` (TEXT[]) — Array of synonyms and case forms
- `geom` (GEOMETRY) — Point geometry

**events**
- `id` (SERIAL PRIMARY KEY)
- `event_time` (TIMESTAMPTZ)
- `description` (TEXT)
- `layer` (VARCHAR) — cops/bus/traffic/pig
- `strategy` (VARCHAR) — single_match/centroid/random
- `geom` (GEOMETRY)
- `matches` (JSONB) — Matched entities with scores

**events_meta**
- Single-row table tracking version and max_event_id for incremental updates

### Key Functions

- `process_location_smart()` — Computes event geometry from matched street IDs
- `search_locations()` — API location search
- `generate_random_location_v2()` — Random point fallback for unmatched events
- `clean_old_events()` — Auto-deletes events >1 hour (pg_cron)

---

## 🧪 Development

### Local Development

```bash
# Without Docker (requires PostgreSQL + Redis)
pip install -r requirements.txt
python main.py

# With Docker (hot reload)
docker-compose up -d redis postgres
python main.py
```

### Parser Development

```bash
# Run parser separately
python -m parser.monitoring
```

### Frontend Development

```bash
cd web

# TypeScript compilation
npx tsc --watch

# Or open map.html directly for debugging
```

### Running Tests

```bash
# Web frontend tests (browser)
cd web/tests
open test-runner.html
# Or: python -m http.server 8000
# Then: http://localhost:8000/tests/test-runner.html

# Backend unit tests (SAFE for CI/CD)
pytest tests/backend/ -v --cov=core --cov-report=html

# Parser unit tests (SAFE - no Telegram session required)
pytest tests/backend/parser/unit/ -v

# Middleware tests
pytest tests/backend/middlewares/ -v

# Database tests (requires PostgreSQL)
pytest tests/database/ -v

# Integration tests (requires PostgreSQL + Redis)
pytest tests/integration/ -v

# All tests with coverage
pytest tests/ -v --cov=core --cov=parser --cov-report=html
```

**Test Coverage Reports:**
- HTML report: `htmlcov/index.html`
- Coverage XML: `coverage.xml`

**CI/CD Pipeline:**
- See [GitLab CI/CD Documentation](docs/GITLAB_CI_CD.md) for setup
- Pipeline runs automatically on push to main or MR creation
- Manual approval required for staging and production deployments

---

## 📈 Monitoring

### Prometheus Metrics

**Access:**
```bash
curl http://localhost:8080/metrics
```

**Key metrics:**
- `http_requests_total` — HTTP requests count
- `http_request_duration_seconds` — Request duration
- `db_pool_size` — Database pool size
- `db_query_duration_seconds` — Query duration
- `cache_hits_total` — Cache hits

### Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f parser
docker-compose logs -f app

# JSON structured logs (production)
docker-compose logs -f --tail=100 app | jq
```

---

## 🛠️ Utility Scripts

| Script | Purpose |
|--------|---------|
| `enrich_streets.py` | Analyze events to find new street names |
| `analyze_events.py` | Detailed event matching quality analysis |
| `export_events.py` | Export events to CSV |

### Street Enrichment Workflow

```bash
# 1. Analyze events for new streets
python enrich_streets.py

# 2. Review suggested additions
# Output: SQL statements for new streets

# 3. Apply to database
docker-compose exec postgres psql -U postgres -f enrich_streets.sql

# 4. Parser will auto-reload streets via pg_notify
```

---

## 📚 Documentation

- [📊 Architecture Report](docs/ARCHITECTURE_REPORT.md) — Full architecture analysis
- [🔧 Docker Architecture](docs/DOCKER_ARCHITECTURE.md) — Docker-specific documentation
- [🔐 Security](docs/SECURITY.md) — Security best practices
- [🔍 Sliding Window Analysis](docs/SLIDING_WINDOW_ANALYSYS.md) — Entity search quality analysis
- [📝 Street Enrichment Report](REPORT.md) — Street database improvements

---

## 🐛 Troubleshooting

### Parser not connecting

**Problem:** `Session file not found`

**Solution:**
```bash
# Create session separately
python -c "
from pyrogram import Client
app = Client('my_session', api_id=YOUR_API_ID, api_hash='YOUR_API_HASH')
async with app:
    print('Session created')
"

# Copy session to parser
cp my_session.session parser/session.session
```

### Redis unavailable

**Problem:** `Redis connection failed`

**Solution:**
```bash
docker-compose ps redis
docker-compose restart redis
docker-compose logs redis
```

### WebSocket not connecting

**Problem:** Connection to `/ws` fails

**Solution:**
1. Check nginx config for `proxy_set_header Upgrade`
2. Check app logs: `docker-compose logs app | grep WebSocket`

### JWT token expired

**Problem:** 401 Unauthorized

**Solution:** Frontend auto-refreshes tokens. Manual refresh:
```javascript
const response = await fetch('/api/auth/refresh', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({refresh_token: storedRefreshToken})
});
```

---

## 📊 Performance Benchmarks

| Operation | Latency | Notes |
|-----------|---------|-------|
| **Entity search** | 1-5 ms | Sliding window + Rapidfuzz |
| **process_location()** | 5-20 ms | PostGIS geometry computation |
| **Full processing** | 10-50 ms | From message to NOTIFY |
| **WebSocket broadcast** | <100 ms | To all connected clients |

**Recommended load:** 60 messages/minute (1 per second)

---

## 🔮 Future Improvements

- [ ] Add trigram support (window size = 3)
- [ ] Contextual entity validation (nearby words like "ул", "ТЦ")
- [ ] Dynamic stopword updates from analytics
- [ ] A/B testing for similarity thresholds
- [ ] Phonetic matching for Cyrillic variants
- [ ] Machine learning-based entity recognition (GLiNER, spaCy)

---

## 📝 License

MIT License — see [LICENSE](LICENSE) file

---

## 👥 Contacts

- **Documentation:** `docs/`
- **API:** `/api/*`
- **Health:** `/health`

---

*Last updated: 2026-04-03*  
*Version: 2.0.0*
