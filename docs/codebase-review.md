# Полное ревью кодовой базы — Survival Map v2.1

Дата: 24.08.2026. Статус: `git master`.
Объём: 5 сервисов (core, parser, processor, postgres, web), Docker, nginx, тесты.

---

## 1. Архитектура и потоки данных

```
Telegram (MTProto) → parser (kurigram) → pending_events (queue, SKIP LOCKED)
                                              │
                                              ▼
                                   processor (NLP: tokenize → lemmatize → classify → find_geo → resolve → process_candidates_v2)
                                              │
                                              ▼
                                      postgres (PostGIS, geometry-first CTE + pg_notify)
                                              │
                                              ▼
                                    core (aiohttp REST + WebSocket + aiogram bot)
                                              │
                                              ▼
                                  web (nginx reverse proxy + Leaflet/MapLibre GL basemap, PWA)
                                              │
                                              ▼
                                    Browser / Telegram WebView
```

**5 сервисов** с чистым разделением ответственности:
- **parser** — приём Telegram-сообщений, strip_tail, preprocess_light, INSERT INTO pending_events
- **processor** — NLP-пайплайн (tokenize → lemmatize → classify → find_geo → resolve → process_candidates_v2)
- **core** — HTTP/WebSocket API, JWT auth, PostgreSQL LISTEN/NOTIFY
- **postgres** — PostGIS, pg_cron, партиционирование, materialized views
- **web** — nginx, Leaflet/MapLibre GL, PWA service worker

**Единый источник настроек:** `common/settings.py` переиспользуется parser и processor (копируется в образы).

**Доставка событий** — двойная:
1. INSERT из processor атомарно шлёт `pg_notify` → core транслирует в WS
2. Catch-up на подключении клиента (`since_id`, починено в этой сессии)

Архитектура консистентна: at-least-once + идемпотентный upsert.

---

## 2. Сильные стороны

| Область | Что сделано хорошо |
|---|---|
| **Дедупликация** | `ON CONFLICT (message_id, event_time) DO NOTHING` в pending_events и events — идемпотентность при ретраях/повторном старте |
| **Очередь** | `FOR UPDATE SKIP LOCKED` + автонадзор воркеров (respawn упавших) + двухфазный claim (status='processing' в той же транзакции) |
| **Партиционирование** | Почасовые партиции, TTL через `DROP TABLE` (мгновенная очистка), расписания pg_cron разнесены |
| **Гео-разрешение** | `process_candidates_v2` — «geometry-first» арбитр с гипотезами (single_match → intersection → weighted_centroid → street_segment) и anti-list guard; матчер — 3 тира (стемы → опечатки → семантика) |
| **Безопасность HTTP** | Строгий CSP в nginx, JWT c валидацией секрета (min 32 симв., чёрный список плейсхолдеров, fail-fast), лимиты тела запроса, rate limiting с доверенным X-Real-IP |
| **WebSocket** | Heartbeat ping/pong, backoff с jitter, self-heal по visibility/online/telegram-activated, лимит соединений, rate limit ping |
| **Фронтенд** | Инкрементальный diff-рендер (renderedById), zustand-store как единственный источник правды, синхронизация часов с сервером (`serverNow`), hard cap 5000 событий |
| **Защита от дурака** | Триггер валидации «strategy ↔ тип геометрии», path traversal guard при скачивании фото, защита от symlink |
| **Docker** | Multi-stage builds, non-root (UID 1000), `cap_drop: ALL`, `no-new-privileges`, tmpfs для ephemeral данных |

---

## 3. Найденные проблемы

### 🔴 Высокий приоритет (H)

**H1. `settings.py` — trailing comma создаёт пустую строку в keywords cops.**
`common/settings.py` — в списке keywords для cops слоя trailing comma создаёт пустую строку `''`. При матчинге пустая строка всегда совпадает → ложные позитивы.
**Рекомендация:** Убрать trailing comma.

**H2. `models.py` — strip whitespace не записывается обратно.**
`core/models.py` — `field.strip()` в Pydantic v2 model validator создаёт cleaned значение, но не записывает его обратно в поле. Белые пробелы в validated данных сохраняются.
**Рекомендация:** Использовать `field = field.strip()` или `model_validator(mode='after')`.

**H3. `settings.py` — POSTGRES_PASSWORD defaults to "postgres".**
`common/settings.py` — если `POSTGRES_PASSWORD` не задан, используется `"postgres"` (дефолт постгриза). Противоречит комментарию «пароль обязателен». В проде — безопасное значение по умолчанию отсутствует.
**Рекомендация:** Требовать `POSTGRES_PASSWORD` в env, как `JWT_SECRET`.

**H4. `notifications.js` — XSS через naive regex strip.**
`web/js/modules/notifications.js` — используется `/<[^>]+>/g` для strip HTML-тегов. Этот regex не escaping special chars → XSS через `onerror`, `onload` атрибуты в img/script тегах.
**Рекомендация:** Удалить stale файл (не используется).

**H5. `parser/session.session` — Telegram session файл в репозитории.**
`parser/session.session` — содержит credentials для Telegram MTProto. Если закоммичен — credentials скомпрометированы.
**Рекомендация:** Добавить в `.gitignore`, проверить git history.

### 🟠 Средний приоритет (M)

**M1. `ratelimit.py` — fragile in-place mutation after unpacking.**
`core/middlewares/ratelimit.py:58-61` — `key, limit = rate_limiter._rate_limit_storage[...].popitem(last=True)` — `popitem` возвращает кортеж, unpacking в `key, limit` хрупок: если значение в storage — не tuple, `ValueError`. Плюс in-place mutation во время итерации.
**Рекомендация:** Добавить type check или использовать safe access.

**M2. `app_factory.py` — bot token prefix logged at INFO.**
`core/app_factory.py` — логируется префикс токена (`token[:8]`) на уровне INFO. В production логи могут быть доступны сторонним сервисам → утечка частичного секрета.
**Рекомендация:** Снизить до DEBUG или убрать.

**M3. `health.py` — runner/site не хранятся (нет cleanup).**
`core/health.py` — `web.TCPSite(runner, ...)` создаётся каждый раз при старте, но `runner` и `site` не сохраняются в атрибуты экземпляра → нет `await runner.cleanup()` при shutdown → potential resource leak.
**Рекомендация:** Сохранять runner/site, добавить cleanup.

**M4. `db_events.py` — прямой доступ к pool bypass Database wrapper.**
`core/db/db_events.py` — прямое обращение к `self._db.pool.acquire()` вместо `self._db.acquire()`. Нарушает абстракцию Database wrapper, потенциальные проблемы с connection lifecycle.
**Рекомендация:** Использовать `self._db.acquire()`.

**M5-M7. `asyncio.get_event_loop()` deprecated.**
- `web/js/core/websocket.ts`
- `processor/geo_matcher.py`
- `processor/main.py`
Используется deprecated `asyncio.get_event_loop()` (Python 3.10+).
**Рекомендация:** Заменить на `asyncio.get_running_loop()`.

**M8. `vector-layer.ts` — settlements skipped, но vector-labels.ts не существует.**
`web/js/core/vector-layer.ts` — settlements пропускаются с комментарием «see vector-labels.ts», но этого файла не существует. Label-слои не интегрированы.
**Рекомендация:** Реализовать или удалить TODO.

**M9. Stale .js файлы в web/js/.**
- `web/js/modules/popups.js` — не используется (заменён `map.ts`)
- `web/js/modules/notifications.js` — не используется (заменён `store.ts` + `data.js`)
- `web/js/telegram/integration.js` — не используется (заменён `map-bootstrap.js`)
**Рекомендация:** Удалить все три файла + обновить webpack.config.js entry points.

**M10. `tsconfig.json` — strict: false.**
`web/tsconfig.json` — `strict: false`, `noImplicitAny: false`, `strictNullChecks: false`. TypeScript не обеспечивает type safety.
**Рекомендация:** Включить strict mode постепенно, начидая с `strictNullChecks`.

**M11. `storage.ts` — async обёртка над синхронным localStorage.**
`web/js/core/storage.ts` — `LocalStorageAdapter` оборачивает синхронный `localStorage` в async методы. Избыточная абстракция, создаёт иллюзию асинхронности.
**Рекомендация:** Упростить до sync API или использовать IndexedDB.

**M12. `health.py` — binds 0.0.0.0 без auth.**
`core/health.py` — health-эндпоинты привязаны к `0.0.0.0` без аутентификации. В Docker network допустимо, но при портировании на хост — уязвимость.
**Рекомендация:** Привязать к `127.0.0.1` или добавить basic auth.

**M13. `map.ts` — photo_url в img src без escape.**
`web/js/core/map.ts:242-244` — `properties.photo_url` вставляется в `<img src>` без escape. Если photo_url содержит кавычки/JS — XSS вектор.
**Рекомендация:** Escape через `encodeURIComponent` или DOMPurify.

### 🟡 Низкий приоритет (L)

**L1.** `cache.py` — camelCase методы (`getItem`/`setItem`) нарушают PEP 8.
**L2.** `cache.py` — `_make_key()` never called anywhere in codebase.
**L3.** `cache.py` — `connect()` has unused `max_retries` parameter.
**L4.** `config.py` — dead JSON parse code (мёртвая логика после рефакторинга).
**L5.** `handlers/basic.py` — `logger.critical()` для non-critical situations (SHUTDOWN по команде).
**L6.** `word_tokenizer.py` — offsets wrong after `re.sub` (смещения токенов невалидны).
**L7.** `phonetic_index.py` — misleading name: метод `stem` не использует фонетику.
**L8.** CSS — 30+ `!important` declarations, duplicate `.popup` rules.
**L9.** 40+ `window.*` global variables — нет единого registry.
**L10.** Нет тестового покрытия для `web/` (TypeScript/JS).
**L11.** `phonetic_index.py` — `replace_street` rebuilds `_all_stems` O(n) при каждом добавлении.
**L12.** `geo_matcher.py` — no timeout on `ProcessPoolExecutor` для `rapidfuzz`.
**L13.** `postgres/init-scripts/` — duplicate numbering (10, 12).
**L14.** `docs/RULES_WEB.md` — old references to `CACHE_KEY = 'survival_events'` (уже исправлено).

---

## 4. Безопасность (Security Posture)

### JWT Authentication
- Algorithm restriction: HS256 (reject none/HS384/HS512)
- Expiration: 15min access, 24h refresh
- Type checking: validates `type: "bot"` in Telegram initData
- Secret validation: min 32 chars, blacklist of placeholders, fail-fast
- **Найдено:** refresh token без rotation/denylist (M-класс)

### HMAC-SHA256 Constant-Time Comparison
- `hmac.compare_digest()` для Telegram initData verification
- Предотвращает timing attacks

### Rate Limiting (двухуровневый)
- **Edge (nginx):** `limit_req_zone` — 10r/s для API, 1r/s для auth
- **App (core):** `RateLimiter` — 60 req/min per IP
- Anti-spoofing: trusted `X-Real-IP` header validation

### Input Validation
- Pydantic модели для всех входных данных
- Body size limits в nginx и core middleware
- Path traversal protection (media download)
- Symlink protection

### Docker Security Hardening
- **Все сервисы:** `cap_drop: ALL`, `no-new-privileges:true`
- **parser, core, processor, web:** `user: "1000:1000"` (non-root)
- **postgres:** minimal `cap_add` (NET_BIND_SERVICE, CHOWN, SETGID, SETUID, DAC_OVERRIDE)
- **tmpfs:** web (nginx cache), processor (/tmp 50MB), postgres (/tmp 10MB)
- **Multi-stage builds:** builder → runtime (minimal image size)

### CSP (Content Security Policy)
```
default-src 'self';
script-src 'self' 'sha256-...' https://telegram.org;
style-src 'self' 'unsafe-inline';
img-src 'self' data: blob: https:;
connect-src 'self' wss: ws: https://tiles.openfreemap.org;
font-src 'self' data:;
worker-src 'self' blob:;
frame-ancestors 'self';
```

---

## 5. Статус исправлений (из предыдущих сессий)

| Пункт | Статус | Что сделано |
|---|---|---|
| H1: сломанный импорт в тесте | ✅ | `tests/test_street_matcher.py` → `core.utils.text_preprocessor` |
| H2: parser терял сообщения | ✅ | direct write + at-least-once buffer при переполнении очереди |
| H3: REST snapshot без окна | ✅ | `get_events_snapshot_as_geojson` ограничен 60 минутами |
| H4: фото терялись при рестарте | ✅ | `_recover_missing_photos()` при старте parser |
| M1: мёртвый CSRF | ✅ | middleware и валидатор удалены; RULES_CORE.md обновлён |
| M2: chunked body | ✅ | страж читает через `request.read()` (aiohttp кэширует) |
| M-матчер: recall/FP | ✅ | anchor-предфильтр + escape + sync fallback Tier-2 + prefix guard |
| L3: докстринг entrypoint | ✅ | «Temperature Optimization» → «Survival Map core service» |
| L4: мусор в корне | ✅ | удалён `events_exporrt.csv` (72 КБ) |
| L5: дрейф доков | ✅ | README обновлён, `.env.example` создан, RULES_WEB.md CACHE_KEY исправлен |
| Docker hardening | ✅ | multi-stage, cap_drop ALL, non-root, tmpfs, no-new-privileges |
| nginx CSP | ✅ | connect-src 'self' добавлен, script-src с хешем для Telegram SDK |
| Auth fallback | ✅ | `'about:blank'` → `''` в auth.py |
| Dead GET code | ✅ | удалён из config.py |
| Мониторинг-стек | ✅ | удалён (Prometheus, Grafana, exporters) |
| Service Worker | ✅ | OpenFreeMap кэшируется (cache-first style, stale-while-revalidate tiles) |

---

## 6. Рекомендации (приоритезированный план)

### Сейчас (быстрые победы)
1. **H1:** Убрать trailing comma в keywords cops (`common/settings.py`)
2. **H2:** Исправить strip whitespace в Pydantic model validator (`core/models.py`)
3. **H4+M9:** Удалить stale `.js` файлы (popups.js, notifications.js, integration.js) + обновить webpack entry points
4. **L1-L3:** Починить cache.py (rename camelCase, удалить мёртвый `_make_key`, убрать unused param)

### Ближайшая итерация
5. **H3:** Требовать `POSTGRES_PASSWORD` в env (как `JWT_SECRET`)
6. **H5:** Удалить `parser/session.session` из репозитория, добавить в `.gitignore`
7. **M2:** Снизить логирование bot token prefix до DEBUG
8. **M5-M7:** Заменить `asyncio.get_event_loop()` на `asyncio.get_running_loop()`
9. **M10:** Включить `strictNullChecks` в tsconfig.json

### Среднесрочно
10. **M3:** Сохранять runner/site в health.py, добавить cleanup
11. **M8:** Реализовать vector-labels.ts или удалить TODO в vector-layer.ts
12. **M12:** Привязать health к `127.0.0.1` или добавить basic auth
13. **M13:** Escape photo_url при вставке в img src
14. **L10:** Добавить тестовое покрытие для TypeScript/JS
15. **L11-L12:** Оптимизировать phonetic_index rebuild + добавить timeout для ProcessPoolExecutor

### Мониторинг (после удаления Prometheus)
- docker healthchecks уже есть → добавить restart-политики с алертами
- Структурные логи (JSON) уже есть → подключить сбор/агрегацию (Loki/ELK)
- Если нужны числовые метрики позже → только exporter-часть (pg_stat_statements через postgres_exporter)

---

## 7. Файловая структура

```
.
├── core/                    # aiohttp HTTP + WebSocket + aiogram bot
│   ├── api/                 # handlers: events, auth, config, validation-config, health
│   ├── db/                  # Database wrapper, db_events, dbconnect
│   ├── middlewares/          # jwt_auth, ratelimit, body_size_limit, logging
│   ├── settings.py          # Единый источник настроек (shared с parser/processor)
│   ├── models.py            # Pydantic модели
│   ├── app_factory.py       # Application factory
│   └── main.py              # Entrypoint
├── parser/                  # kurigram + text preprocessing + photo download
│   ├── monitoring.py        # Telegram channel monitoring (737 lines)
│   ├── db.py                # PostgreSQL connection
│   └── text_preprocessor.py # strip_tail, preprocess_light (shared с processor)
├── processor/               # NLP pipeline (pymorphy3 + rapidfuzz + PostGIS)
│   ├── main.py              # Orchestrator (835 lines)
│   ├── geo_matcher.py       # 3-tier geo matching
│   ├── phonetic_index.py    # Stem-based inverted index
│   ├── layer_classifier.py  # Event layer classification
│   └── semantic_matcher.py  # Semantic similarity (ONNX, optional)
├── postgres/
│   ├── init-scripts/        # Schema, functions, triggers, indexes (01-07 SQL)
│   ├── config/              # postgresql.conf (tuned for 1GB)
│   └── tests/               # SQL integration tests
├── web/
│   ├── js/core/             # TypeScript: store, websocket, map, ui, vector-layer
│   ├── js/modules/          # popups.js, notifications.js (STALE — удалить)
│   ├── assets/lib/          # Leaflet, MapLibre GL, markercluster (vendored)
│   ├── map.html             # Main map page
│   ├── index.html           # Validation gate page
│   ├── sw.js                # Service worker (PWA)
│   └── webpack.config.js    # Build configuration
├── tests/                   # Python unit tests (pytest)
├── scripts/                 # Maintenance scripts
├── docs/                    # RULES_*.md, codebase-review.md
├── docker-compose.yml       # 5 services + nginx
├── nginx.conf               # Reverse proxy + CSP + rate limiting
├── Dockerfile.core          # Multi-stage (builder libpq-dev → runtime libpq5)
├── Dockerfile.parser        # Multi-stage
├── Dockerfile.processor     # Multi-stage
├── Dockerfile.web           # Multi-stage (node → nginx)
├── Dockerfile.postgres      # postgis base + cron/contrib
└── .env                     # Secrets (NOT committed)
```

---

*Ревью выполнено по результатам полного прочтения кода всех сервисов. Severity присвоена с учётом масштаба (локальный/региональный сервис, десятки событий в час).*