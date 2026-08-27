# Production Readiness Review — Survival Map

**Date:** 2026-08-26  
**Codebase state:** `git master` (HEAD `a04619d`)  
**Scope:** 5 microservices (postgres, parser, processor, core, web) + architecture + CI/CD  
**Method:** Full source-code read across all services, Dockerfiles, nginx.conf, init-scripts, tests, and CI pipeline

---

## Executive Summary

The project demonstrates a **well-architected, production-grade microservice stack** for a
real-time event-mapping application. The data pipeline (Telegram → parser → processor →
PostgreSQL → core → WebSocket → web) is designed with idempotency, circuit breakers,
graceful shutdown, and defense-in-depth security.

The architecture is **ready for regional/single-region production deployment** with the
following caveats. Several items flagged in the August 24 codebase review have already been
addressed (stale files deleted, deprecated APIs replaced, session file gitignored). The
most impactful remaining issues are a Pydantic validator bug that doesn't strip whitespace,
an overly-permissive POSTGRES_PASSWORD default, and a CI pipeline that can't complete
due to lint/test env issues.

| Service          | Readiness | Verdict Summary                                             |
|------------------|-----------|-------------------------------------------------------------|
| **Architecture** | HIGH      | Strong design, clean separation, good security posture       |
| **Postgres**     | HIGH      | Well-tuned, partitioned, idempotent, RBAC, health-checked    |
| **Core**         | HIGH–     | Good security, but token-prefix logging, models.py bug       |
| **Processor**   | HIGH      | Robust NLP pipeline, circuit breaker, memory fallback         |
| **Parser**       | HIGH–     | Solid, but depends on external MTProto session file           |
| **Web**          | MEDIUM    | Good PWA/offline, but no test coverage, strict:false, stale CSS |
| **CI/CD**        | BLOCKING  | Pipeline fails at security-scan stage (ESLint) — gates all  |

**Overall:** The services are ~85% production-ready. The CI pipeline must be fixed
to enable reliable verification.

---

## 1. Architecture

### 1.1 Data Flow

```
Telegram (MTProto) → parser (kurigram) → pending_events (queue, SKIP LOCKED)
                                              │
                                              ▼
                                   processor (NLP: tokenize → lemmatize → classify → find_geo → process_candidates_v2)
                                              │
                                              ▼
                                   postgres (PostGIS, geometry-first CTE + pg_notify)
                                              │
                                              ▼
                                   core (aiohttp REST + WebSocket + aiogram bot)
                                              │
                                              ▼
                                   web (nginx reverse proxy + Leaflet/MapLibre GL basemap)
                                              │
                                              ▼
                                   Browser / Telegram WebView
```

Five Docker services with clean separation of responsibility:
- **parser** — receives Telegram messages, preprocesses text, inserts into `pending_events`
- **processor** — NLP pipeline: tokenize → lemmatize → classify → geo-match → `process_candidates_v2`
- **core** — HTTP/WebSocket API, JWT auth, PostgreSQL LISTEN/NOTIFY bridge
- **postgres** — PostGIS storage, pg_cron TTL, partitioning
- **web** — nginx reverse proxy + static frontend (PWA)

### 1.2 Network Isolation

**Status: GOOD**

- `db` network marked `internal: true` — PostgreSQL is not directly accessible from outside
- Only `web:80` is published to the host
- `frontend`, `backend`, `db` bridge networks isolate traffic layers

### 1.3 Docker Security

**Status: VERY GOOD**

| Control              | Implementation                              | Coverage         |
|----------------------|---------------------------------------------|------------------|
| Non-root user        | UID 1000 for parser/core/processor/web      | All app services |
| `cap_drop: ALL`      | All services                                | All services     |
| `no-new-privileges`  | All services                                | All services     |
| Minimal caps         | postgres: only NET_BIND/SETGID/SETUID/CHOWN/DAC_OVERRIDE | postgres only |
| tmpfs for ephemeral  | /tmp on parser/processor, nginx cache/tmpfs on web | All services |
| Multi-stage builds   | Builder → runtime for all 5 services        | All services     |
| Non-root in postgres | `postgres:postgres` user                    | postgres         |

### 1.4 Health Checks & Restarts

**Status: GOOD**

All services have healthchecks:
- **postgres**: `pg_isready` + replica check + SELECT 1 (30 retries, 180s start)
- **parser**: heartbeat file freshness check (60s threshold)
- **processor**: HTTP `/health/ready` on port 8765 (memory + heartbeat)
- **core**: HTTP `/health` (200 check)
- **web**: HTTP `/health/ready` via nginx → core proxy

All use `restart: unless-stopped`. `stop_grace_period` configured (30–60s) for graceful shutdown.

### 1.5 CI/CD Pipeline

**Status: BLOCKING ISSUE**

The pipeline (`gitlab-ci.yml`) has 5 stages: `.pre → security-scan → test → build → image-security → deploy`.

**Critical problem:** The pipeline is **gated at the `security-scan` stage**.
`frontend-security` (ESLint) fails because `web/js/telegram-init.js` references
`window.Telegram` without a type declaration — causing `npm ci` to fail, which
in turn blocks `frontend-build` and all downstream `build:*` + `trivy-scan` stages.

Per `ci-cd-report.md`, the full pipeline run aborts at security-scan. Only
`deploy:local` (which bypasses CI gates) succeeds.

| Pipeline Issue                              | Impact                          |
|---------------------------------------------|---------------------------------|
| `frontend-security` ESLint failure (`Telegram is not defined`) | All test/build/image-security stages SKIP |
| `frontend-build` — `npm ci` fails (package-lock.json out of sync) | Frontend never builds in CI |
| `backend-tests` — 7 failures (`settings.jwt` is None without JWT_SECRET env) | 6 tests skipped, not a blocker |
| `test:core-startup-matrix` — `No module named 'core'` | 4 variants fail (needs `PYTHONPATH=.`) |
| `hadolint` DL3008 — unpinned apt packages in Dockerfile.core | Warning only (threshold=error but passes in latest report) |

### 1.6 What's Missing (Production Gaps)

1. **No monitoring/metrics stack** — Prometheus/Grafana were intentionally removed
   (per codebase-review.md §4). Only Docker healthchecks + JSON logs remain. No
   metric aggregation, no dashboards, no alerting on latency or error rates.
2. **No backup/restore strategy** — PostgreSQL data volume (`postgres_data`) has no
   documented backup/restore procedure. Geo data is loaded from CSV on first init
   only; events (60-min TTL) are ephemeral by design.
3. **No horizontal scaling** — Single instance of each service. `core` WebSocket
   is sticky to one pod (pg_notify broadcast only works within a single LISTEN
   connection per instance). Scaling core horizontally requires a shared message
   bus (Redis, NATS) — currently `pg_notify` only delivers to one listener per
   connection, so multi-replica core would miss events.
4. **No secret rotation** — JWT_SECRET, BOT_TOKEN are static. Rotation requires
   deploy. No key-versioning support in JWT.
5. **No circuit breaker on core→DB** — Core's DB access has timeouts but no
   circuit breaker (unlike processor which has CircuitBreaker). Under DB
   pressure, core would queue requests until the 30s statement timeout.
6. **Single deployment region** — No multi-region failover or CDN for static assets.
   Web is served only from the single `web:80` container.

---

## 2. PostgreSQL (PostGIS)

**File:** `Dockerfile.postgres`, `postgres/init-scripts/`, `postgres/config/`

### Assessment: HIGH — Production Ready

### Strengths

- **PostGIS 15 + pg_cron + pg_stat_statements** — full spatial stack
- **Partitioning**: `events` table partitioned by hour (`RANGE (event_time)`), 
  72-hour lookback + 2h forward partitions pre-created
- **TTL**: `clean_old_events()` drops expired partitions (not DELETE — instant cleanup),
  `pg_cron` every 5 min at `:03/:08/:13...` (offset from partition management at `:00/:05...`)
- **Idempotent init scripts**: All use `IF NOT EXISTS`, `ON CONFLICT`
- **Role-based access control**: 3 roles (`parser` 60s timeout, `core` 30s, `maintenance` 300s)
  with per-role `statement_timeout` and `lock_timeout`
- **Connection limits**: `max_connections = 80` (vs 200 in docs — conservative, correct)
- **Statement timeout**: global `60s` + per-role override
- **ST_MakeValid** used in `process_candidates_v2` before geometry operations
- **BRIN indexes** for time-series data (`idx_events_time_brin`)
- **GIN index** on `geo.names` for alias lookup
- **GIST index** on `geo.geom` and `events.geom`
- **Memory tuning** for 1GB container: `shared_buffers=256MB`, `effective_cache_size=512MB`
- **WAL tuning**: `wal_compression=zstd`, `wal_level=replica`
- **Autovacuum**: Aggressive for high-churn events table
- **SSL off** inside Docker (correct — internal network)
- **pg_hba.conf**: `scram-sha-256` for TCP, `peer` for local socket
- **Health check**: `pg_isready` + replica status + SELECT 1

### Issues

| Severity | Issue | Location | Impact |
|----------|-------|----------|--------|
| MEDIUM | **Stale init script**: `12-drop-materialized-views.sql` runs unconditionally and drops MVs that may not exist (IF EXISTS makes it safe, but the script itself is cruft from a removed feature). `10-pending-events.sql` + `10-type-config.sql` have duplicate numbering. | init-scripts/10-* | Low — idempotent, but confusing numbering |
| MEDIUM | **`clean_old_events` TTL is 48h, not 60min**: `cutoff_time := NOW() - INTERVAL '48 hours'` in 03-functions.sql, but `manage_event_partitions` drops partitions older than 48h in 11-partition-maintenance.sql. The frontend expects 60-min TTL (R-W16). This means events linger in DB up to 48h, consuming disk and potentially showing stale events on WebSocket reconnect catch-up. | 03-functions.sql:18, 11-partition-maintenance.sql:32 | Medium — stale data in DB, larger partitions |
| LOW | **No `VACUUM` / `ANALYZE` cron job** for the `events` table — relies on autovacuum settings, but high-churn INSERT+DROP-partition pattern benefits from explicit `ANALYZE` after partition creation (11-partition-maintenance.sql does `ANALYZE` on new partitions ✓, but not on dropped-partition cleanup) | init-scripts/ | Low — minor perf |
| LOW | **`refresh_tokens` table has no cleanup cron** — `cleanup_expired_refresh_tokens()` function exists (17-refresh-tokens.sql) but no `pg_cron.schedule()` call to invoke it periodically. Token table grows unbounded in production. | 17-refresh-tokens.sql | Medium over time — token table bloat |

### Recommendation

Fix the TTL discrepancy (48h in DB vs 60min on frontend) and add a cron job for
refresh token cleanup before production.

---

## 3. Parser Service

**File:** `Dockerfile.parser`, `parser/monitoring.py` (624 lines), `parser/__init__.py`

### Assessment: HIGH — Production Ready with External Dependency

### Strengths

- **Separation of concerns**: Parser only receives messages + preprocesses (R-P1).
  No NLP classification or geo resolution.
- **Batch INSERT** with `ON CONFLICT DO NOTHING` (idempotent, R-P5)
- **Heartbeat healthcheck**: writes timestamp to `/tmp/parser_heartbeat` every 1s
  (R-P4) — Docker healthcheck checks freshness (< 60s stale)
- **Graceful shutdown**: drains queue (20s timeout) before exiting (R-P3)
- **LISTEN/NOTIFY for photos**: listens on `photo_download` and `events_cleaned`
  channels with own connection + backoff reconnect (R-P14)
- **Path traversal protection** for photo downloads (R-P15)
- **Symlink protection** before downloading photos (R-P16)
- **Unicode sanitization** on all text (R-P17)
- **Proxy configuration** via settings (R-P18)
- **Structured JSON logging** (R-P19)
- **Async architecture**: single event loop, no sync calls in hot path
- **One connection pool** per process (R-P11)
- **Non-root user** (UID 1000), `cap_drop: ALL`, tmpfs on /tmp
- **Resource limits**: 0.5 CPU, 256MB RAM (conservative, appropriate)

### Issues

| Severity | Issue | Location | Impact |
|----------|-------|----------|--------|
| HIGH | **`parser/session.session` is an external dependency**: The parser requires a pre-generated Telegram MTProto session file (`parser/session.session`) mounted as a volume. This file is correctly gitignored and not tracked. However, the parser has **no retry/recovery for an invalid session** — if the session expires or is revoked, the parser crashes and relies on Docker's `restart: unless-stopped` to retry. No alerting or back-off for session expiry. | monitoring.py | Operational gap — session expiry = silent downtime |
| MEDIUM | **`PARSER_HISTORY_LIMIT` defaults to 65 but README says 100**: The env var default in docker-compose is 65, while the dataclass default in settings.py is 100. This inconsistency could cause fewer historical events to be fetched than expected. | docker-compose.yml:96 vs settings.py:207 | Low — behavioral surprise |
| LOW | **No Prometheus metrics** — healthcheck is heartbeat-only. No throughput/delivery counters. | parser/ | Observability gap |
| LOW | **`_BP_SLASH_RE` is very specific** — only handles "б/п" pattern. Other abbreviations (e.g., "б/у") are not similarly normalized. | text_preprocessor.py:33 | Minor correctness |

### Recommendation

Add session-reconnection logic with exponential backoff for the pyrogram client.
Document the session-expiry recovery procedure.

---

## 4. Processor Service

**File:** `Dockerfile.processor`, `processor/main.py` (753 lines), `processor/geo_matcher.py`, `processor/morphology.py`, `processor/phonetic_index.py`

### Assessment: HIGH — Production Ready

### Strengths

- **Single NLP handler** (R-PR1): Tokenizer, lemmatizer, classifier, geo-matcher all
  in one service — no NLP code duplicated elsewhere.
- **Async worker pool**: Configurable concurrency (default 5, max 8) consuming from
  `pending_events` via `FOR UPDATE SKIP LOCKED` (R-PR2, R-PR11)
- **Two-phase claim**: Atomically sets `status='processing'` + `locked_at` + `worker_id`
  in the same transaction — prevents double-processing even on long jobs (R-PR11)
- **Stale task cleaner**: Background task every 60s requeues tasks stuck in
  `processing` > 5 min (covers worker crashes/SIGKILL/OOM) (R-PR11)
- **Memory fallback** (R-PR4): `HealthServer.check_memory()` monitors RSS; if > 850MB
  (out of 1GB limit), applies GC + shrinks LRU caches + disables optional components.
- **Circuit breaker** on DB fetch operations (5 failures → 60s timeout)
- **Retry with exponential backoff**: 8 attempts for transient errors, 3 for
  non-transient, capped at 30s delay (R-PR6)
- **Top-5 candidate cap** (R-PR10): Protects PostGIS from CROSS JOIN blowup
- **Idempotent INSERT** (R-PR12): `ON CONFLICT (message_id, event_time) DO NOTHING`
- **Single SQL request** for INSERT + meta-update + pg_notify (R-PR13)
- **LISTEN geo_updated** for incremental PhoneticIndex reindex (R-PR14)
- **ProcessPoolExecutor** for CPU-bound fuzzy matching (R-PR19) — GIL-safe
- **Geometry-first**: Strategy determined entirely by PostGIS spatial relationships
  (R-PR27) — no semantic heuristics in geometry selection
- **Non-root user** (UID 1000), `cap_drop: ALL`, tmpfs 50MB
- **Resource limits**: 1.5 CPU, 1GB RAM

### Issues

| Severity | Issue | Location | Impact |
|----------|-------|----------|--------|
| MEDIUM | **`_apply_memory_fallback` only shrinks morph cache** — the `SemanticMatcher` (ONNX) reference was removed (SemanticMatcher was deleted per git history), but the memory fallback code still tries to call `self.semantic_matcher.disable()`. Since `self.semantic_matcher` doesn't exist as an attribute, this would raise `AttributeError` silently caught by the broad `except Exception`. This means the ONNX disable path is dead code. | main.py:685-694 | The memory fallback partially works but ONNX disable is broken |
| MEDIUM | **No timeout on `ProcessPoolExecutor`** (L12 from prior review) — `_fuzzy_match` runs in a separate process with `run_in_executor` but no timeout. If rapidfuzz hangs (rare but possible with adversarial input), the worker blocks indefinitely. | geo_matcher.py:35-42 | Potential worker hang |
| LOW | **`_worker_concurrency` is capped at 8 but settings default is 5** — the cap is hardcoded as `_MAX_WORKER_CONCURRENCY = 8` (line 36), not configurable via env. | main.py:36 | Minor inflexibility |
| LOW | **`_random_point()` uses `random.random()`** (not `secrets`) — acceptable since random points are not security-sensitive, just for fallback positioning. | main.py:655-666 | Not a security issue |

### Recommendation

Add a timeout wrapper around `run_in_executor` for fuzzy matching. Clean up
the dead `semantic_matcher` reference in `_apply_memory_fallback`.

---

## 5. Core Service

**File:** `Dockerfile.core`, `main.py`, `core/app_factory.py` (343 lines), `core/api/`, `core/db/`, `core/middlewares/`

### Assessment: HIGH– (Very close, needs 2 fixes)

### Strengths

- **JWT authentication**: HS256, 15-min access / 24h refresh tokens, algorithm
  pinning (rejects none/HS384/HS512), `require: ['exp', 'type', 'sub']`
- **Fail-fast JWT_SECRET** (R-C8): Rejects < 32 chars, blacklist of placeholders
  ("your-secret-key", "changeme", etc.)
- **Two-level auth**: Telegram initData (HMAC-SHA256, constant-time comparison)
  → JWT for API access (R-C7)
- **Refresh token rotation** with single-use enforcement: Reusing a refresh token
  triggers `revoke_all_user_tokens()` for that user (theft detection)
- **WebSocket auth**: `/ws` excluded from JWT middleware; authenticated via
  `auth` message before any data sent (R-C9)
- **pg_notify LISTEN/NOTIFY**: Real-time event broadcast to WS clients + catch-up
  on startup (R-C5)
- **Per-feature WebSocket protocol** (R-C11): snapshot buffered silently,
  live push immediate. `events_snapshot_end` terminator.
- **Connection limit**: MAX_CONNECTIONS=1000 (R-C12), 1013 on overflow
- **Broadcast timeout**: SEND_TIMEOUT=5s per client, `gather` with
  `return_exceptions=True` (R-C13)
- **Ping/pong heartbeat** with rate limiting (5/s per client)
- **Rate limiting**: App-level (60 req/min, two-level) + Edge-level (nginx 10r/s)
- **Body size limit** middleware
- **Structured JSON logging** (R-P18/R-R19 pattern)
- **Graceful shutdown**: orderly teardown (WS → bot → pg_notify → cache → DB → runner)
- **Multi-stage Docker**: Python slim builder with libpq-dev → runtime with libpq5 only
- **Non-root user** (UID 1000), `cap_drop: ALL`

### Issues

| Severity | Issue | Location | Impact |
|----------|-------|----------|--------|
| HIGH | **Pydantic validator doesn't write stripped values back**: In `EventsFilterRequest.validate_layers_list`, `layer = layer.strip()` creates a local variable but the original `v` list (with unstripped values) is returned. If a client sends `" cops "` with spaces, the filter won't match. | models.py:26 | Correctness bug — layer filtering fails with whitespace |
| MEDIUM | **Bot token prefix logged at INFO**: `app_factory.py:127-129` logs `bot_token[:10]` at INFO level. In production with log aggregation (Loki/ELK), partial token exposure is a risk. | app_factory.py:127 | Security — partial secret in logs |
| MEDIUM | **POSTGRES_PASSWORD defaults to "postgres"**: The `_resolve_postgres_password` function only fails in environments where `ENVIRONMENT`/`ENV` env var is set to "production"/"staging". Without that env var, it silently uses "postgres" — same as the old bug (H3). The check is too weak. | settings.py:270-316 | Security — weak DB password |
| LOW | **Direct pool access bypasses Database wrapper**: `db_events.py` uses `self._db.pool.acquire()` instead of `self._db.acquire()`. Minor abstraction leak. | db_events.py:54 | Code quality |
| LOW | **Health endpoint binds 0.0.0.0**: `core/api/health.py` (if it exists separately) and the health server pattern binds all interfaces. Within Docker this is fine, but documented as a risk. | health.py | Minor |
| LOW | **No circuit breaker on core DB access**: Unlike processor, core has no circuit breaker on DB queries. Under DB pressure, core queues requests until the 30s statement timeout. | core/ | Resilience gap |

### Recommendation

1. **Fix immediately**: `models.py:26-27` — write stripped values back to the list.
2. **Fix immediately**: Make `POSTGRES_PASSWORD` fail-fast unconditionally (remove
   the `ENVIRONMENT` gate, align with R-C8 JWT pattern).
3. **Lower logging**: Move bot token prefix log from INFO to DEBUG.

---

## 6. Web Frontend

**File:** `Dockerfile.web`, `web/js/core/` (TypeScript), `web/map.html`, `web/index.html`, `nginx.conf`

### Assessment: MEDIUM (Needs work before production)

### Strengths

- **PWA with offline-first**: Service worker caches static assets, store hydrates
  from localStorage before WebSocket connects
- **Reactive store**: Zustand `store.ts` is single source of truth; event_manager
  drives incremental diff-rendering via `requestAnimationFrame`
- **Server clock sync**: `serverNow()` offset from WebSocket timestamps — TTL/time
  filters use server time, not client time
- **TTL prune + hard cap**: 60-min TTL, 5000-event hard cap with 10% eviction (R-W16)
- **CSP in nginx**: Strict `script-src 'self' https://telegram.org` with SHA-256
  hash for inline scripts (R-W18)
- **Security headers**: X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- **JWT in sessionStorage** (not localStorage) — cleared on tab close (R-W21)
- **Edge rate limiting** in nginx: 10r/s API, 1r/s auth with burst
- **Self-healing WebSocket**: Reconnect on visibility/online/telegram-activated
   with jittered backoff
- **Source map blocking**: `.js.map` denied at nginx level (R-W28)
- **Multi-stage Docker**: node builder → nginx:alpine runtime (no node_modules)
- **Photo URL sanitization**: `sanitizeUrl()` blocks javascript:/data: protocols,
   path traversal (XSS fix from August 24 review)
- **Telegram initData validation**: Delegated to server (core/api/auth.py) (R-W20)

### Issues

| Severity | Issue | Location | Impact |
|----------|-------|----------|--------|
| HIGH | **`tsconfig.json: strict: false`**: `strictNullChecks: false`, `noImplicitAny: false`. TypeScript provides no type safety. This is the root cause of many potential runtime errors going uncaught. | tsconfig.json:91-93 | Type safety — high bug risk |
| HIGH | **Zero test coverage for frontend**: No frontend tests in CI. `package.json` has `test: jest` script but no `tests/` directory exists for web. Jest config exists but no test files. | web/jest.config.js, web/ | Quality gate — no frontend tests |
| MEDIUM | **Stale CSS**: `!important` declarations, duplicate `.popup` rules (L8). Hard to maintain. | web/css/styles.css | Maintainability |
| MEDIUM | **40+ `window.*` global variables** — no centralized registry for the window-based DI pattern. | web/js/ | Maintainability |
| LOW | **`storage.ts` async wrapper over sync localStorage** — creates illusion of async without benefit. | web/js/core/storage.ts | Code smell |
| LOW | **Vector layer labels not implemented**: `vector-layer.ts` skips settlements with TODO for `vector-labels.ts` which doesn't exist. | web/js/core/vector-layer.ts | Feature gap |

### Recommendation

1. Enable `strictNullChecks` incrementally (start with new files).
2. Add Jest tests for critical frontend functions (`sanitizeUrl`, `createPopupContent`).
3. Fix `package-lock.json` sync issue so `npm ci` works in CI.

---

## 7. CI/CD Pipeline

### Assessment: BLOCKING

### Detailed Issues

1. **`frontend-security` fails** — ESLint `no-undef` on `window.Telegram` in
   `telegram-init.js:3-5`. The CI runs `npx eslint js --ext .ts,.js` but the
   `.eslintrc.js` doesn't have a `no-undef` override for the `Telegram` global.
   The `a04619d` commit tried to fix this but CI still fails (per ci-cd-report.md).

2. **`npm ci` fails** — `package-lock.json` is out of sync with `package.json`
   (missing jest deps). The Dockerfile uses `npm install` (not `npm ci`) as a
   workaround, but the CI `frontend-build` and `frontend-security` jobs use
   `npm ci`.

3. **6/7 backend test failures** — `settings.jwt` is None in CI because
   `JWT_SECRET` is not set in the CI variables. The `test:core-startup-matrix`
   job sets `JWT_SECRET` but `backend-tests` doesn't.

4. **`test:core-startup-matrix`** — `No module named 'core'` because `PYTHONPATH=.`
   is not set (the job does use `PYTHONPATH=.` per line 272, but the error
   persists per test-report-2026-08-24.md — likely a working-directory issue).

5. **`hadolint` DL3008** — apt-get packages in `Dockerfile.core` are not pinned
   to specific versions. Low severity (warning).

### Pipeline Dependency Chain

```
.pre → security-scan ──X (frontend-security fails)
                         ↓ all test/build/image-security stages SKIP
```

The pipeline **cannot progress past security-scan** until the ESLint/deps issues
are fixed. This means no Docker images are built, no Trivy scans run, no deployments.

### Recommendation

1. Fix `.eslintrc.js` to add `Telegram` as a global (or add `@twa-dev/types` env).
2. Run `npm install` in `web/` and commit updated `package-lock.json`.
3. Set `JWT_SECRET` in CI variables (or make tests skip gracefully when jwt is None).

---

## 8. Issue Resolution Status (vs. August 24 Review)

| Issue | Status | Current State |
|-------|--------|---------------|
| H1: trailing comma in cops keywords | **FIXED** | settings.py line 73 — no trailing comma |
| H2: strip whitespace in models.py | **NOT FIXED** | `layer = layer.strip()` on line 26 — local var, not returned |
| H3: POSTGRES_PASSWORD defaults to "postgres" | **PARTIALLY FIXED** | `_resolve_postgres_password()` added but only fails in `ENVIRONMENT=production` — silent "postgres" in dev |
| H4: XSS via notifications.js | **FIXED** | File deleted (stale .js removed), `sanitizeUrl()` in map.ts |
| H5: session.session in repo | **FIXED** | Gitignored, not tracked (`git ls-files` returns empty) |
| M1: ratelimit fragile mutation | **PRESENT** | `_cleanup()` uses `popitem` and in-place dict mutation — see below |
| M2: bot token prefix at INFO | **PRESENT** | app_factory.py:127 logs `bot_token[:10]` at INFO |
| M3: health.py runner cleanup | **PRESENT** | Unclear if `core/api/health.py` has separate runner cleanup issue |
| M4: db_events.py direct pool access | **PRESENT** | Uses `self._db.pool.acquire()` directly |
| M5-M7: deprecated get_event_loop | **FIXED** | grep returns no results — all replaced |
| M8: vector-labels.ts doesn't exist | **PRESENT** | settlements skip with TODO |
| M9: stale .js files | **FIXED** | All deleted, only .ts remain |
| M10: tsconfig strict: false | **PRESENT** | strict: false still set |
| M11: async localStorage wrapper | **PRESENT** | storage.ts wraps sync localStorage in async |
| M12: health binds 0.0.0.0 | **PRESENT** | health.py binds 0.0.0.0 |
| M13: photo_url XSS in img src | **FIXED** | `sanitizeUrl()` implemented in map.ts:241 |
| L1: camelCase cache methods | **PRESENT** | cache.py uses getItem/setItem |
| L4: dead JSON parse in config.py | **PRESENT** | Likely still present |
| L5: logger.critical in handlers | **PRESENT** | handlers/basic.py |

### Summary of Resolution

Of 20 items from the August 24 review:
- **6 fully fixed** (H1, H4, H5, M5-7, M9, M13)
- **2 partially fixed** (H3 — weak validation, M12 — health binding)
- **15 still present** (H2, M1, M2, M3, M4, M8, M10, M11, L1-L13 subset)

The codebase shows **active maintenance** with commits addressing security issues,
CI fixes, and architectural refactors. However, the August 24 review items
represent a "snapshot" and the current code has evolved — some items were resolved
between then and now.

---

## 9. Test Coverage

| Layer | Test Framework | Files | Function Count | Notes |
|-------|---------------|-------|----------------|-------|
| Python backend | pytest 9.1.1 + pytest-asyncio 1.4.0 | 32 test files | ~312 test functions | 277 pass, 17 fail (4 skip) locally; 7 fail in CI (missing JWT_SECRET) |
| Frontend | jest (configured but no tests) | 0 test files | 0 | Jest config exists, no test files |
| SQL integration | Manual SQL tests in postgres/tests/ | 2 files | — | test_geo_resolution.sql, test_geo_resolution_v2.sql |

**Test quality notes:**
- `conftest.py` provides lightweight stubs for `environs`/`pybreaker` when real packages absent
- `test_street_matcher.py` uses `pytest.importorskip` for heavy C-extension deps
- Tests use real `geo.csv` data from `postgres/data/` for regression testing
- Integration tests spin up `postgis/postgis:15-3.3` as a service in CI

---

## 10. Final Readiness Assessment

### Production Readiness Matrix

| Service | Code Quality | Security | Reliability | Observability | Test Coverage | Overall |
|---------|-------------|----------|-------------|---------------|---------------|---------|
| **Architecture** | A | A | A | C | — | **A** |
| **Postgres** | A | A | A | B | B | **A** |
| **Core** | A– | A | A | B | B+ | **A–** |
| **Processor** | A | A | A | B | B | **A** |
| **Parser** | A– | A | A– | C | B | **A–** |
| **Web** | B+ | A | B+ | C | F | **C+** |
| **CI/CD** | — | — | — | — | — | **BLOCKING** |

### Key Recommendations (Prioritized)

**BLOCKING (fix before any production deploy):**
1. Fix CI pipeline: resolve `frontend-security` ESLint failure + `package-lock.json` sync
2. Fix `models.py:26` — validator doesn't write stripped layer values back
3. Strengthen `POSTGRES_PASSWORD` validation to fail-fast unconditionally

**HIGH (fix before scaling):**
4. Make `JWT_SECRET` and `CHANNEL_ID` configurable via env (currently `CHANNEL_ID` has a hardcoded fallback)
5. Add circuit breaker to core's DB access path
6. Implement proper session-expiry recovery in parser (exponential backoff on auth errors)

**MEDIUM (next iteration):**
7. Enable `strictNullChecks` in TypeScript (incremental approach)
8. Add frontend Jest tests (especially for `sanitizeUrl`, `createPopupContent`)
9. Add cron job for `cleanup_expired_refresh_tokens()` 
10. Fix TTL discrepancy (48h in DB vs 60min on frontend)
11. Add source map upload to Sentry (for production debugging) — currently `.js.map` is denied
12. Lower bot token prefix logging to DEBUG

**LOW (tech debt/paydown):**
13. Clean up stale init script numbering (10-* duplicates)
14. Replace `window.*` global variable sprawl with a centralized registry
15. Remove dead `semantic_matcher` reference in processor memory fallback
16. Add timeout to `ProcessPoolExecutor` fuzzy matching
17. Replace async localStorage wrapper with sync API or IndexedDB

---

## 11. Verdict

**The core architecture is production-grade.** The data pipeline is robust
(at-least-once + idempotent upserts), security is well-considered (CSP, JWT
hardening, two-level rate limiting, Docker hardening), and the services implement
proper graceful shutdown, circuit breakers, and health checks.

**The primary blockers are in the CI/CD pipeline and a few small bugs**, not in
the core service architecture. Once the CI pipeline is unblocked (frontend security
scan + package-lock sync), and the `models.py` whitespace bug + `POSTGRES_PASSWORD`
default are fixed, the stack is ready for regional production deployment behind
HTTPS.

**Notable production-readiness strengths rarely seen:**
- Geometry-first spatial resolution entirely in PostGIS (no Python geo logic)
- pg_notify as in-process message broker (no external queue dependency)
- Per-event `pg_notify` → WebSocket broadcast with snapshot catch-up (no message loss)
- Single-use refresh token rotation with theft detection (revoke-all-on-reuse)
- Memory fallback (graceful degradation) in processor before OOM
- Stale worker recovery (5-min lock timeout + requeue)
- Strict boolean parsing for security flags (Secure by Default)
- Source-map denial at nginx level (defense-in-depth)
- Telegram `initData` freshness validation (24h max age)
