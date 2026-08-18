# Полное ревью проекта — Survival Map (август 2026)

Статус кода: `git master @ e7ccc5a`. Ревью выполнено по исходникам всех сервисов
(`core`, `parser`, `processor`, `postgres`, `web`) и конфигурации (compose, nginx).

---

## 1. Архитектура и потоки данных

```
Telegram канал ──► parser (kurigram) ──► pending_events (queue)
                                             │  (SKIP LOCKED)
                                             ▼
                                         processor (NLP: tokenize → lemmatize → classify → geo)
                                             │  INSERT events + pg_notify('events_new')
                                             ▼
                                     postgres (PostGIS, партиции по часам)
                                             │  LISTEN/NOTIFY
                                             ▼
                                core (aiohttp REST + WS + aiogram bot)
                                             │  WS per-feature stream
                                             ▼
                              web (nginx + Leaflet + MapLibre GL basemap, PWA)
```

- **5 сервисов** (postgres, parser, processor, core, web) — разделение ответственности
  чистое: parser только читает Telegram и пишет очередь, processor делает NLP,
  core только отдаёт данные, web — статика nginx.
- **Единый источник настроек** `core/settings.py`, который импортируют parser и
  processor (копируется в их образы) — хорошо.
- **Доставка событий** — двойная: INSERT из processor атомарно шлёт `pg_notify` →
  core транслирует в WS; плюс catch-up на подключении клиента (`since_id`, починено
  в этой сессии). Архитектура консистентна (at-least-once + идемпотентный upsert).
- **Документация правил** (`docs/RULES_*.md`) — реально отражает код (редкость).

---

## 2. Сильные стороны

| Область | Что сделано хорошо |
|---|---|
| **Дедупликация** | `ON CONFLICT (message_id, event_time) DO NOTHING` в pending_events и events — идемпотентность при ретраях/повторном старте |
| **Очередь** | `FOR UPDATE SKIP LOCKED` + автонадзор воркеров (respawn упавших) |
| **Партиционирование** | Почасовые партиции, TTL 48ч через `DROP TABLE` (мгновенная очистка), расписания pg_cron разнесены, чтобы не конфликтовать по блокировкам |
| **Гео-разрешение** | `process_candidates_v2` — «geometry-first» арбитр с гипотезами (single_match → intersection → weighted_centroid → street_segment) и anti-list guard; матчер — 3 тира (стемы → опечатки → семантика) |
| **Безопасность HTTP** | Строгий CSP в nginx, JWT c валидацией секрета (min 32 симв., чёрный список плейсхолдеров, fail-fast), лимиты тела запроса, rate limiting с доверенным X-Real-IP |
| **WebSocket** | Heartbeat ping/pong, backoff с jitter, self-heal по visibility/online/telegram-activated, лимит соединений, rate limit ping |
| **Фронтенд** | Инкрементный diff-рендер (renderedById), zustand-store как единственный источник правды, синхронизация часов с сервером (`serverNow`), hard cap 5000 событий |
| **Защита от дурака** | Триггер валидации «strategy ↔ тип геометрии», path traversal guard при скачивании фото, защита от symlink |

---

## 3. Критические фрагменты — оценка

### 🔴 Высокий приоритет

**H1. `tests/test_street_matcher.py` импортирует несуществующий модуль.**
Строка 47: `from parser.text_preprocessor import ...` — модуль переехал в
`core/utils/text_preprocessor.py`, тест не обновлён. **Блокирует весь `pytest`**
(collect error). Падал уже в snapshot-коммите 958f398. Починка — одна строка
импорта. ⚠️ Существовало до текущих изменений.

**H2. Парсер теряет сообщения при переполнении очереди (backpressure = drop).**
`parser/monitoring.py`: очередь 65 сообщений; при заполнении (92%) live-сообщение
**молча выбрасывается навсегда** (нет DLQ, нет повторной выборки по message_id).
Для приложения-сигнализации потеря события = потеря оповещения. Рекомендация:
при переполнении писать в БД напрямую (без очереди) или сохранять в таблицу-буфер
и до-выбирать при старте.

**H3. Несоответствие TTL между слоями.**
- Клиентский store: TTL 60 мин (`store.ts`), фильтр 15/30/60 мин.
- Серверное окно catch-up: 60 мин.
- БД: партиции живут **48 часов**, REST `GET /api/events` (snapshot) отдаёт до 5000
  событий **без окна по времени**.
Клиент не использует REST-snapshot (только WS), поэтому сейчас не больно, но при
любом будущем использовании `/api/events` клиент получит двое суток событий.
Рекомендация: ограничить snapshot окном 60 мин.

**H4. Фото могут не скачаться после рестарта parser.**
Триггер `notify_photo_download` шлёт NOTIFY только в момент перехода
`pending_events.status → 'done'`. Если parser в этот момент недоступен/рестартует —
NOTIFY потерян, фото не скачается никогда (строки уже `done`, повторного триггера
нет). Рекомендация: восстановление по догоняющему запросу при старте
(`SELECT id, message_id, photo_file_id FROM events WHERE photo_url IS NULL`), либо
NOTIFY-пакет с retry.

### 🟠 Средний приоритет

**M1. CSRF-защита — мёртвый код.**
`csrf_middleware` проверяет токен только при наличии cookie `session_token`, но
cookie **нигде не устанавливается** (grep: только чтение). Клиент авторизуется
через `Authorization: Bearer` (JWT в sessionStorage). Итог: CSRF-проверка всегда
пропускается. Уязвимости нет (Bearer не отправляется браузером автоматически,
same-origin + CORS выключен), но защита не работает, а её наличие создаёт ложное
ощущение безопасности. Рекомендация: либо убрать middleware, либо реально внедрить
cookie-сессию с CSRF-токеном.

**M2. body_size_limit «съедает» chunked-тело.**
В `body_size_limit_middleware` при отсутствии `Content-Length` (chunked
transfer-encoding) страж читает `request.content` **и не восстанавливает** поток —
обработчик после этого получит пустое тело (`request.json()` упадёт → 400).
Клиенты с Content-Length не страдают, но любой chunked POST `/api/*` сломан.
Рекомендация: буферизовать прочитанное и подменить `request._read_bytes`
(или ограничить только проверкой заголовка).

**M3. JWT refresh без ротации/отзыва.**
Refresh-токен живёт 24 часа, stateless, без версии/чёрного списка: утёкший
refresh не отзывается, повторное использование не детектируется. Для этого
масштаба допустимо, но стоит хотя бы добавить `jti` + короткий denylist.

**M4. `events_meta.max_event_id` — lost-update гонка.**
CTE вставки processor делает `UPDATE events_meta SET max_event_id = (SELECT id FROM
inserted)` в каждой транзакции. При конкурентных вставках значения могут
перезаписываться не в порядке возрастания (неатомарное чтение-запись). Сейчас
поле используется только REST-метаданными (клиент их не читает) — риск низкий,
но при включении REST-resync станет источником рассинхрона.

**M5. Дублирование очистки партиций.**
`clean_old_events` (03) и `manage_event_partitions` (11) оба дропают партиции
старше 48ч с разницей в 1 час. Расписания разнесены, DDL идемпотентен, но
ответственность дублируется. Рекомендация: оставить дроп только в одном месте.

**M6. «2-й» лишний процессорный проход одного сообщения.**
`_fetch_pending` освобождает `FOR UPDATE`-блокировку сразу после SELECT (транзакция
закрывается), а обработка идёт потом — второй воркер может взять ту же строку.
INSERT идемпотентен, но работа дублируется. Рекомендация: выставлять
`status='processing'` в той же транзакции (с таймаут-восстановлением по
`processed_at IS NULL AND status='processing' AND created_at < now()-X`).

**M7. День/ночь на фронтенде — пересоздание слоя и «хак» тёмной темы.**
`switchTileLayer` на каждый переключатель снимает MapLibre-слой и создаёт новый
вместо `setStyle`; тёмная тема — ручная перекраска paint-свойств liberty-стиля.
Работает, но хрупко к изменению стиля провайдера. Плюс подписи скрываются
полностью (`hideMaplibreLabels`), русские лейблы не включены (отдельная фаза).

**M8. OSM-URL с устаревшими поддоменами.**
`https://{s}.tile.openstreetmap.org/...` — a/b/c поддомены legacy; актуальный
формат без `{s}`. Работает, но OSM может отключить. Плюс нет WebGL-fallback:
если MapLibre не инициализировался, карта пустая (в `initializeMap` нет проверки
`isWebGLSupported()`).

**M9. Полная запись localStorage каждые 30 сек.**
`tickClock()` каждые 30с делает `set()` → срабатывает подписка LocalCache →
полная сериализация всех событий (до 5000) в localStorage. Не баг, но лишняя
работа на мобильных. Рекомендация: не персистить при «чисто часовом» изменении
(фильтровать по полю `revision`).

### 🟡 Низкий приоритет

**L1.** `process_candidates` (08) — легаси-версия, заменена v2 (16), но остаётся в
схеме. Удалить для чистоты.
**L2.** `idx_events_confidence_low WHERE confidence < 0.7` и колонка `confidence`
созданы, но фильтрация по confidence нигде не реализована (ни в SQL, ни в клиенте).
**L3.** Entrypoint core — `main.py` в корне репозитория, но docstring у него
«Temperature Optimization application» (пережиток копирования) и `core/settings.py`
лежит не рядом. Стоит перенести в `core/main.py` и переименовать docstring.
**L4.** `events_exporrt.csv` (72 КБ, опечатка в имени) закоммичен в корень — мусор.
**L5.** `docs/RULES_WEB.md` описывает `CACHE_KEY = 'survival_events'`, в коде —
`'events_geojson'`; README говорит «4 сервиса» и «нативный MapLibre» — устарел.
**L6.** `get_incremental_events` (REST, время-based) и `since_timestamp`-ветка в
`get_filtered_events_as_geojson` остались как fallback после перехода на `since_id` —
для старых клиентов. Можно оставить, но пометить deprecated.

---

## 4. Рекомендации (приоритезированный план)

### Сейчас (быстрые победы)
1. **Починить `test_street_matcher.py`** — заменить импорт на `core.utils.text_preprocessor`
   (одна строка; pytest перестанет падать целиком).
2. **Убрать мусор**: `events_exporrt.csv`, лишний `process_candidates` (08),
   `postgres/monitoring_queries.sql` (ручная диагностика) — в отдельную папку или git rm.
3. **M2 (chunked body)** — починить страж размера тела.
4. **M1 (CSRF)** — принять решение: удалить или внедрить cookie-сессии.

### Ближайшая итерация
5. **H2**: DLQ/буфер для переполненной очереди parser вместо drop.
6. **H4**: догоняющая загрузка фото при старте parser.
7. **M9**: не персистить store при clock-тикании.
8. **M6**: `status='processing'` с таймаут-восстановлением.

### Среднесрочно
9. **M8**: WebGL-fallback (проверка `isWebGLSupported` → растровый слой) + новый
   OSM-URL без `{s}`; вынести basemap-адаптер (план в `docs/refactoring-plan.md`).
10. **H3**: единое окно событий (60 мин) на всех уровнях, в т.ч. REST-snapshot.
11. **Лейблы**: включить русские подписи OpenFreeMap (`name:ru` присутствует в
    TileJSON — spike в плане рефакторинга).
12. **M3**: `jti` + denylist для refresh-токенов.

### Мониторинг (после Фазы 1 удаления Prometheus)
Стек метрик удалён, остались только health-эндпоинты (core `/health/*`, processor
:8765, heartbeat parser). Минимум для восстановления наблюдаемости:
- docker healthchecks уже есть — добавить `restart`-политики с алертами;
- структурные логи (JSON) уже есть — подключить сбор/агрегацию (например,
  docker logging driver → Loki/ELK) без введения in-process метрик;
- если нужны числовые метрики позже — вернуть **только** exporter-часть
  (например, `pg_stat`/`pg_stat_statements` через postgres_exporter), не grafana-стек.

---

## 5. Что уже починено в этой сессии

- **WS catch-up терял backfill-историю** (баг «2 события на карте»): водяной знак
  переведён с `event_time` на `since_id` (id монотонен по вставке). Коммит e7ccc5a.
- **Мониторинг-стек удалён** (compose, nginx `/metrics`, код метрик, зависимости).
  Коммит 86a27b3.
- **Service Worker кэширует OpenFreeMap** (стиль/глифы/спрайты cache-first, тайлы
  stale-while-revalidate с лимитом). Коммит 6f4a6b6.

## 6. Статус исправлений (чистка + критические проблемы)

| Пункт | Статус | Что сделано |
|---|---|---|
| H1: сломанный импорт в тесте | ✅ | `tests/test_street_matcher.py` → `core.utils.text_preprocessor`; pytest проходит целиком (72 passed, 3 skipped) |
| H2: parser терял сообщения | ✅ | `handle_message` → `put_nowait` + прямая запись в `pending_events` при переполнении (at-least-once, без дропа) |
| H3: REST snapshot без окна | ✅ | `get_events_snapshot_as_geojson` ограничен 60 минутами |
| H4: фото терялись при рестарте | ✅ | догоняющий запрос `_recover_missing_photos()` при старте parser |
| M1: мёртвый CSRF | ✅ | middleware и валидатор удалены; RULES_CORE.md R-C4 обновлён (Bearer-only, cookie не используется) |
| M2: chunked-тело «съедалось» | ✅ | страж читает через `request.read()` (aiohttp кэширует), поток не теряется |
| M-матчер: recall/FP | ✅ | anchor-предфильтр: `has_stem_anywhere` (многословные имена в начале) + escape для Tier-2; синхронный fallback Tier-2 без executor'а; префиксный guard (≥3) отсекает ложный позитив «Маяковского→Маловского» |
| L3: докстринг entrypoint | ✅ | «Temperature Optimization» → «Survival Map core service» |
| L4: мусор в корне | ✅ | удалён `events_exporrt.csv` (72 КБ) |
| L5: дрейф доков | ✅ | README: 5 сервисов, поток данных, ссылки на RULES_*.md, JWT_SECRET обязателен; создан шаблон `.env.example` (README ссылался на несуществующий env.example); RULES_WEB.md CACHE_KEY → `events_geojson` |
| L1: легаси process_candidates (08) | ⏸ | оставлен: на него завязан интеграционный тест `postgres/tests/test_geo_resolution.sql` |
| M3/M4/M5/M6/M7/M8/M9 | ⏸ | среднесрочные улучшения — см. раздел 4 |

---

*Составлено по результатам полного прочтения кода; severity присвоен с учётом
реального масштаба (локальный/региональный сервис, десятки событий в час).*
