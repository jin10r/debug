# Deploy & Processing Report — 2026-08-28

## Session Summary

Репорт по результатам деплоя и обработки исторических событий после исправления трёх критических ошибок и внедрения архитектурных улучшений.

---

## 1. Исправленные ошибки

| # | Ошибка | Файл | Статус |
|---|--------|------|--------|
| 1 | PostgreSQL: `autovacuum_truncate_scale_factor` не существует; `ALTER TABLE events SET (...)` на partitioned таблице | `postgres/init-scripts/21-autovacuum-tuning.sql` | ✅ Удалён блок events |
| 2 | Core: `ModuleNotFoundError: No module named 'orjson'` | `requirements.txt` | ✅ Добавлен `orjson>=3.9.0` |
| 3 | Processor: `AttributeError: 'HealthServer' has no attribute 'record_message_processed'` | `processor/health.py` | ✅ Добавлены методы-заглушки |
| 4 | Core: `ModuleNotFoundError: No module named 'core.metrics'` | `core/metrics.py` | ✅ Создан модуль с реальными Prometheus метриками |
| 5 | Core: `counter metric is missing label values` (ws_messages_sent_total) | `core/metrics.py` | ✅ Убран лишний label `["success"]` |

## 2. Архитектурные улучшения

| # | Улучшение | Файл | Описание |
|---|-----------|------|----------|
| 1 | **R-DB0: БД как геопространственный калькулятор** | `docs/RULES_POSTGRES.md` | Новое правило: DB = geo-processor + ephemeral buffer, не архив |
| 2 | **R-DB2: Партиционирование по часам** | `docs/RULES_POSTGRES.md` | Исправлено: "по дням" → "по часам" |
| 3 | **pg_notify payloads minimized** | `processor/main.py` | Было: полный GeoJSON Feature (~2KB) → Стало: `{"id", "layer", "strategy"}` (~50B) |
| 4 | **Core hydrate minimal payloads** | `core/api/websocket.py` | `broadcast_event()` теперь fetch полного Feature из DB по id |
| 5 | **get_event_by_id()** | `core/db/db_events.py`, `core/db/dbconnect.py` | Новый метод для точечного fetch события |
| 6 | **PostgreSQL WAL tuning** | `postgres/config/postgresql.conf` | `max_wal_size` 2GB→1GB, `min_wal_size` 512MB→256MB |
| 7 | **prometheus_client** | `requirements.txt`, `core/metrics.py` | Реальные Prometheus Counter/Histogram вместо заглушек |

## 3. CI/CD Pipeline Results

### Stage: test

| Job | Status | Details |
|-----|--------|---------|
| backend-tests (pytest) | ✅ PASS | 281 passed, 20 skipped, 0 failed |
| frontend-typecheck (tsc --noEmit) | ✅ PASS | No type errors |
| frontend-build (webpack) | ✅ PASS | 4/4 artifacts built |
| frontend-artifacts | ✅ PASS | common.js, store.js, ui.js, websocket.js present |

### Stage: security-scan

| Job | Status | Details |
|-----|--------|---------|
| bandit-scan | ⚠️ WARN | 5× B608 (false positives — f-string SQL with parameterized `$N` values). Pre-existing. |
| pip-audit | ✅ PASS | No known vulnerabilities in all 4 requirements files |

### Stage: image-security

| Job | Status | Details |
|-----|--------|---------|
| hadolint | ⏭️ SKIP | Not installed locally (runs in CI Docker image) |
| trivy-scan | ⏭️ SKIP | Not installed locally (runs in CI Docker image) |

## 4. Stack Status (Post-Deploy)

```
NAME            STATUS
core            Up (healthy)    :8080
nlp_processor   Up (healthy)    :8765
parser          Up (healthy)
postgres        Up (healthy)    :5432
```

## 5. Event Processing Statistics

| Metric | Value |
|--------|-------|
| Total events processed | **84** |
| Pending (waiting) | **0** ✅ |
| Failed (error) | **0** ✅ |
| Errors in logs (30min) | **0** ✅ |
| Partitions active | **51** |
| DB size | **32 MB** |

### Events by Layer (last hour)

| Layer | Count |
|-------|-------|
| traffic | 16 |
| pig | 13 |
| bus | 7 |
| cops | 5 |

### Processing Pipeline Flow

```
Telegram → parser → pending_events → nlp_processor → events (PostGIS)
                                                    → pg_notify (minimal)
                                                    → core (hydrate → WS broadcast)
```

## 6. E2E Verification

| Test | Result |
|------|--------|
| pg_notify minimal payload delivery | ✅ Sent `{"id":72, "layer":"bus", "strategy":"single_match"}` |
| Core receive + fetch from DB | ✅ `get_event_by_id(72)` fetched full Feature |
| WS broadcast to client | ✅ `Feature broadcasted: 1/1 clients` |
| Client receives full GeoJSON Feature | ✅ `type=Feature`, `geometry=Polygon`, `coordinates=true` |

## 7. Recommendations

1. **Commit all changes** — 10 modified/created files in this session
2. **Run `docker compose down -v postgres_data && docker compose up -d --build`** for clean PG volume (old volume had stale autovacuum settings)
3. **Monitor for 24h** — verify partition DROP operations work correctly with new autovacuum config
4. **Add `prometheus_client` to `processor/requirements.txt`** — wire up real metrics in processor service (currently stubs)
