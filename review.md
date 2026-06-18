Комплексное ревью проекта Survival Map — отчёт и рекомендации
Context
Запрошено полное ревью проекта (Telegram Mini App: карта событий Одессы). Микросервисы: postgres (PostGIS), parser (kurigram → NLP/геокодинг → запись), core (aiohttp REST+WS + aiogram-бот), web (nginx: статика + reverse-proxy). Метод: прямое чтение кода в течение сессии + 3 Explore-агента (infra/ops, core-backend, tests/deps/hygiene). Все «секреты в git» перепроверены через git ls-files/check-ignore.

Дата: 2026-06-17. Ветка работ: parser-opt-redis-proxy-cleanup (запушена).

Общая оценка
Архитектура крепкая: чистое разделение сервисов, изолированная db-сеть (internal: true), drop-ALL caps + non-root + tmpfs у parser/core, multi-stage образы, параметризованный SQL (инъекций нет), корректная HMAC-валидация initData (constant-time + freshness), реактивный стор + offline-first PWA. Основные долги: нет тестов, TypeScript strict:false, ряд edge/nginx-хардненингов, несколько app-уровневых багов (rate-limit спуфинг, path-traversal в media, JWT-кэш), дрейф документации и рассинхрон пинов зависимостей.

✅ Исправлено в этой сессии (текущее состояние)
Аутентификация /ws (был открыт всем) — _ws_authenticate проверяет JWT/initData.
MultiPolygon рендеринг (молча терялся) + дыры полигонов.
Self-heal WS: jitter, visibility/online/Telegram-activated reconnect, snapshot-warning.
WARNING при TELEGRAM_VALIDATION_ENABLED=False.
Удалён мёртвый EventTracker; web/CLAUDE.md приведён к Leaflet-реальности.
Redis удалён, добавлен proxy; env.example санитизирован.
streets.csv: 21 вырожденных POLYGON → LINESTRING.
node_modules вынесен из git.
❗ Поправка к авто-разбору (ложные тревоги — проверено)
Агенты пометили как CRITICAL «секреты в истории git». Это неверно:

.env не отслеживается, никогда не коммитился, в .gitignore — реальный BOT_TOKEN/WEBAPP_URL(ngrok) лежат только в локальной рабочей копии. Утечки в репозитории нет. Действие: просто никогда не коммитить .env (правило уже есть).
__pycache__/*.pyc — 0 файлов в git. parser/session.session — не в git (отслеживается только gen_session.py). Гигиена репозитория в порядке.
Находки и рекомендации (по приоритету)
P0 — Безопасность/корректность (сделать до прод-деплоя)
Rate-limit обходится через X-Forwarded-For. core/middlewares/ratelimit.py:73-77 берёт первый XFF без доверенного источника → любой клиент шлёт произвольный IP и получает отдельную квоту. nginx не ставит real_ip. Fix: в nginx — set_real_ip_from <docker/edge CIDR>; real_ip_header X-Forwarded-For;, а в приложении доверять XFF только от внутренней сети, иначе request.remote.
Path-traversal/symlink в выдаче медиа. core/api/media.py:24-28 использует Path.resolve().is_relative_to(...), но не отсекает symlink внутри events_dir и возвращает str(e) клиенту (утечка путей). Fix: запретить symlink-таргеты, строгая проверка префикса, generic-ошибка наружу (детали — в лог).
nginx edge-хардненинг (nginx.conf): access_log off (нет аудита), нет server_tokens off, нет limit_req (рейтлимит только в приложении), нет client_max_body_size, wildcard Access-Control-Allow-Origin * на /media (+ лишние методы POST/OPTIONS). Fix: включить access_log с ротацией; server_tokens off; limit_req_zone для /api/ и жёстче для /api/validate-init; client_max_body_size 2-10m; сузить CORS до GET (или убрать — фронт same-origin).
TLS/HSTS. nginx слушает только :80, TLS терминируется выше. Fix: зафиксировать в деплой-доках требование TLS-терминатора и выставить Strict-Transport-Security на нём (или добавить listen 443 ssl в nginx).
Лишние capabilities у web. docker-compose.yml web: CHOWN/SETUID/SETGID/ DAC_OVERRIDE поверх cap_drop: ALL. Fix: оставить только NET_BIND_SERVICE (или запускать nginx非-root и слушать >1024 за прокси).
P1 — Надёжность / приложение
JWT-кэш: ключ = сырой токен в памяти (core/middlewares/auth.py:81) и TTL 60с без отзыва/logout. Fix: хэшировать ключ (sha256), снизить TTL до 5-10с (токены и так 15 мин), при желании — blacklist на logout.
Pydantic без границ (core/models.py): time_filter/layers/строки без Field(ge/le/max_length). Fix: добавить ограничения (time_filter 1..10080, layers max_length, description max_length) — defense-in-depth против абуза.
transaction() не транзакция (core/db/db_base.py:148-153) — возвращает pool.acquire() (autocommit), без rollback. Fix: вернуть реальный connection.transaction() либо переименовать, чтобы не вводить в заблуждение.
Блокирующий I/O в async (core/db/db_streets.py:58, загрузка CSV) и get_all_streets() без LIMIT (:123) — на старте/больших данных. Fix: run_in_executor для файлов; LIMIT/пагинация.
statement_timeout на стороне БД (есть только клиентский command_timeout=60) — runaway PostGIS-запрос держит коннект. Fix: statement_timeout=10s в postgresql.conf (также было в рекомендациях по postgres).
Широкие except Exception в ряде хендлеров/БД (db_events.py, api/events.py, api/media.py) скрывают тип ошибки и код ответа. Fix: различать DB/validation/ transient, ставить корректный HTTP-статус, наружу — generic.
P2 — Качество / процессы
Нет автотестов вообще (python и js; web/package.json test = echo). Это главный долг качества: непокрыты street_matcher (3 тира), process_candidates, telegram_validation (HMAC/replay), websocket-auth/broadcast, store TTL-pruning. Fix: минимальный pytest-набор на критичную логику + 1-2 JS unit на store/ws; подключить в CI.
TypeScript strict:false (+ noImplicitAny/strictNullChecks off) — web/tsconfig.json. Нулевая типобезопасность. Fix: включить strict поэтапно (минимум strictNullChecks), чинить ошибки; npm run typecheck сделать гейтом.
CI/CD отсутствует. Fix: пайплайн: ruff/flake8 + npm run lint+typecheck+ build + pytest + (опц.) Trivy-скан образов.
Зависимости: рассинхрон пинов (requirements.txt ~= vs parser/requirements.txt >=; asyncpg/environs разные), rapidfuzz>=3 без верхней границы, cryptography~=42 помечен удаляемым (JWT=HS256) но стоит. Fix: единый стиль пинов, верхние границы для C-расширений, удалить cryptography (или объяснить), закоммитить lock’и.
Дрейф документации: docs/web.md всё ещё описывает «MapLibre native» (реально Leaflet+maplibre-gl-leaflet); docs/postgres.md не упоминает 21 LINESTRING-фикс; нет LICENSE при "license":"MIT". Fix: синхронизировать docs/web.md как web/CLAUDE.md, добавить LICENSE, отметить geom-фикс.
Healthcheck-нюансы: parser heartbeat не ловит блокировку event-loop если обновляется отдельно; web start_period:10s маловат. Fix: при желании — pgrep+heartbeat, поднять start_period/ retries.
Что сделано хорошо (сохранить)
Параметризованный SQL; изоляция db-сети; non-root + drop-caps + tmpfs (parser/core); multi-stage образы; HMAC initData (constant-time + freshness); JWT с circuit breaker; graceful shutdown (core и parser); offline-first PWA + SW; LISTEN/NOTIFY + pg_cron TTL; лог-ротация docker; разумный CSP в html.

Критические файлы (для исполнения)
core/middlewares/ratelimit.py, core/api/media.py, nginx.conf, docker-compose.yml (web caps), core/middlewares/auth.py (jwt-cache), core/models.py, core/db/db_base.py, core/db/db_streets.py, postgres/config/postgresql.conf, web/tsconfig.json, web/package.json + requirements.txt/parser/requirements.txt, docs/web.md, docs/postgres.md, новый tests/, новый .gitlab-ci.yml, новый LICENSE.

Verification (по мере реализации)
Edge: curl -I → проверить отсутствие Server: nginx/…, наличие security-headers, 429 при limit_req, отклонение больших тел (client_max_body_size).
Rate-limit: запрос с поддельным X-Forwarded-For НЕ должен сбрасывать квоту.
Media: запрос ..%2f/symlink → 403, без утечки путей.
Tests/CI: pytest зелёный; cd web && npm ci && npm run typecheck && npm run build без ошибок; пайплайн проходит на push.
Docs: docs/web.md соответствует коду (Leaflet); присутствует LICENSE.
