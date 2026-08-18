# План рефакторинга Survival Map

Дата: 2026-08-17. Основан на полном ревью репозитория (без git-истории, состояние на момент написания).

---

## 0. Резюме ревью — что есть на самом деле

### Фронтенд (`web/`)

- **Стек:** Leaflet + векторный слой **MapLibre GL** через плагин `maplibre-gl-leaflet`. Все три библиотеки — **вендоренные статикой** в `web/assets/lib/` (не npm), подключаются `<script>` в `map.html`. Webpack собирает только прикладной TS в `web/dist/js/*`.
- **OpenFreeMap уже подключён и является слоем по умолчанию.** В `web/js/core/ui.ts` (`TILE_PROVIDERS`):
  - `vector-light` — MapLibre, `https://tiles.openfreemap.org/styles/liberty` (дефолт `currentTileKey`);
  - `dark` — тот же liberty-стиль + клиентская «перекраска» (`setPaintProperty`/`setLayoutProperty`);
  - `osm` — растровый fallback `https://{s}.tile.openstreetmap.org/...`.
- **Подписи выключены** в обоих режимах (`hideMaplibreLabels` прячет все symbol-слои). Вопрос «русских лейблов» сегодня в коде не стоит — их нет вообще.
- **Service Worker (`web/sw.js`)** кэширует тайлы только `tile.openstreetmap.org` и `basemaps.cartocdn.com`. Запросы к `tiles.openfreemap.org` (style.json, glyphs, sprites, .pbf) **SW не перехватывает** → при офлайне подложка пустая.
- **CSP в `nginx.conf` уже разрешает OpenFreeMap** (`connect-src … https://tiles.openfreemap.org`, `worker-src 'self' blob:`). Отдельной работы по CSP для карты почти нет.
- Вендоренный `maplibre-gl.js` — **v2.4.0** (заметно старее актуальной v5 из quick-start OpenFreeMap).
- Мусор: в `web/js/` лежат устаревшие скомпилированные артефакты `common.js`, `modules/popups.js`, `modules/notifications.js`, `telegram/integration.js` — webpack собирает из `.ts`, на странице они не подключаются.

### Мониторинг (нужно удалить)

- `docker-compose.yml`: сервисы `postgres-exporter`, `prometheus`, `grafana` + тома `prometheus_data`, `grafana_data`.
- `prometheus/` (prometheus.yml + grafana-provisioning), `Dockerfile.grafana`.
- `postgres/config/queries.yml`, `postgres/config/postgres_exporter.yml` (конфиги exporter'а).
- `nginx.conf`: location `= /metrics` (проксирует на core).
- **Код метрик в бэкенде:** `core/utils/metrics.py` (middleware + `/metrics` + WS-метрики), использование в `core/app_factory.py` и `core/api/websocket.py`; `processor/metrics.py`, использование в `processor/main.py`, `processor/geo_matcher.py`, `processor/semantic_matcher.py`, `processor/health.py`. Зависимость `prometheus-client==0.19.0` в `requirements.txt` и `processor/requirements.txt`. Стабы `prometheus_client` в `tests/conftest.py` и `tests/test_semantic_matcher.py`. Упоминание `metrics_middleware` в `docs/RULES_CORE.md`.

### Документация (дрейф)

- `README.md` утверждает «нативный MapLibre GL JS» и «4 сервиса» — фактически Leaflet + MapLibre-слой, а compose содержит 7 сервисов.
- `docs/web.md` **отсутствует**, хотя README на него ссылается.
- `docs/` содержит только RULES_*.md.

### Прочее

- **В репозитории нет `.git`** — до любых изменений нужен контроль версий.
- `web/package.json` (keywords) и README описывают стек по-разному; `package.json` не содержит leaflet/maplibre в зависимостях (они вендорены).

---

## 1. Расхождения с приложенным планом миграции

Приложенный план предполагает, что фронтенд «сейчас на OSM-растре и его надо переводить на OpenFreeMap». Фактически **миграция на OpenFreeMap уже выполнена** (дефолтный слой — liberty, CSP готов, fallback на OSM есть). Поэтому план ниже — это не «миграция», а **доводка и чистка** по 4 направлениям:

| Пункт приложенного плана | Статус сейчас | Что делать |
|---|---|---|
| URL стиля `…/styles/liberty/style.json` | **Ошибочный.** Актуальный URL (подтверждён quick-start): `https://tiles.openfreemap.org/styles/liberty` без `.json` — он и используется в коде | Ничего; не «чинить» по плану |
| Установка `npm i maplibre-gl @maplibre/maplibre-gl-leaflet` | Не требуется: библиотеки вендорены в `assets/lib` | При желании обновить вендоренные файлы с unpkg (см. Фазу 2.7) |
| Feature flag `BASEMAP_PROVIDER` через DefinePlugin | Не нужен: переключение runtime-переключателем `switchTileLayer` уже есть | Оставить как есть; добавить только WebGL-fallback |
| CSP | Уже обновлён | Только опционально сузить `img-src https:` |
| Service Worker кэш OpenFreeMap | **Не сделан** | Фаза 2.1 |
| Русские лейблы | Подписи выключены полностью | Отдельное решение + spike (Фаза 2.4) |
| Day/night через `setStyle` | Сейчас пересоздание слоя + перекраска | Фаза 2.2 |

---

## 2. Фаза 0 — Страховка (до любых правок)

Репозиторий не под git. До удаления сервисов:

1. `git init`, `.gitignore` уже есть — проверить, что секреты (`parser/session.session`, `.env`) не попадут.
2. Первый коммит «snapshot before refactoring».
3. Снять `docker compose config` как эталон текущего стека (до удаления сервисов).

**Приёмка:** `git status` чистый, коммит создан.

---

## 3. Фаза 1 — Удаление мониторинг-стека

### Решение по объёму (decision point)

Есть два уровня:

- **A. Минимальный** — только инфраструктура: сервисы compose, `prometheus/`, `Dockerfile.grafana`, конфиги exporter'а, location `/metrics` в nginx. Код метрик в core/processor остаётся (мёртвый, но безвредный).
- **B. Полный (рекомендуется)** — A + удаление кода метрик и зависимости `prometheus-client` из core/processor/тестов. Иначе остаётся мёртвый код и лишняя зависимость, которую никто не скрейпит.

План ниже описан для варианта **B**; пункты, специфичные для B, помечены.

### 3.1 docker-compose.yml

Удалить:

- блок «Monitoring: postgres-exporter + Prometheus + Grafana» целиком: сервисы `postgres-exporter`, `prometheus`, `grafana`;
- тома `prometheus_data:` и `grafana_data:` из секции `volumes`;
- оставить `x-default-logging` (используется остальными сервисами).

Проверить, что `depends_on` остальных сервисов не ссылается на удаляемые (не ссылается).

### 3.2 Файлы и каталоги (удалить)

```bash
rm -rf prometheus/ Dockerfile.grafana \
       postgres/config/queries.yml postgres/config/postgres_exporter.yml
```

### 3.3 nginx.conf

Удалить location `= /metrics` (блок с комментарием «Prometheus metrics endpoint …»).

### 3.4 core (вариант B)

- `core/app_factory.py`:
  - убрать импорт `from core.utils.metrics import setup_metrics_routes, set_application_info, metrics_middleware`;
  - убрать `metrics_middleware,` из списка middlewares;
  - убрать `set_application_info(version='2.0.0')` и `setup_metrics_routes(app)`.
- `core/api/websocket.py`: убрать импорт из `core.utils.metrics` и все вызовы `ws_connections_active/rejected_total/ping_rate_limited_total/broadcasts_total/broadcast_latency_seconds/broadcast_errors_total` (строки ~52, 83, 87, 111, 132, 161–164, 270, 285, 398).
- Удалить `core/utils/metrics.py`.
- `core/middlewares/jwt_auth.py`: убрать `'/metrics'` из `PUBLIC_ENDPOINTS`.
- `core/middlewares/csrf.py`: убрать `'/metrics'` из whitelist.

### 3.5 processor (вариант B)

- `processor/health.py`: убрать импорт `metrics_handler` и строку `app.router.add_get("/metrics", metrics_handler)`.
- `processor/main.py`: убрать импорты `processor_match_time_seconds, processor_tier_distribution` и вызовы.
- `processor/geo_matcher.py`: убрать импорт `record_geo_matches` и вызов `record_geo_matches(results)`.
- `processor/semantic_matcher.py`: убрать импорты метрик и вызовы (`semantic_checked/accepted/rejected/missing_embedding`).
- Удалить `processor/metrics.py`.

### 3.6 Зависимости и тесты (вариант B)

- `requirements.txt`, `processor/requirements.txt`: убрать `prometheus-client==0.19.0`.
- `tests/conftest.py`, `tests/test_semantic_matcher.py`: убрать стабы `prometheus_client`.
- `docs/RULES_CORE.md`: убрать строку про `metrics_middleware`.

### 3.7 Приёмка Фазы 1

- [ ] `docker compose config` валиден, сервисов мониторинга нет;
- [ ] `docker compose up -d --build` поднимает 4 сервиса (postgres, parser, processor, core, web);
- [ ] `curl -i http://localhost/metrics` → 404;
- [ ] `curl http://localhost/health/ready` → 200;
- [ ] `pytest -q` зелёный;
- [ ] `grep -rn "prometheus" core/ processor/ nginx.conf docker-compose.yml` → пусто (кроме упоминаний в этой документации).

---

## 4. Фаза 2 — Фронтенд: доводка OpenFreeMap

### 2.1 Service Worker: кэширование OpenFreeMap (важно)

`web/sw.js`, функция `isTileRequest`:

```js
function isTileRequest(url) {
    return /tile\.openstreetmap\.org|basemaps\.cartocdn\.com|tiles\.openfreemap\.org/.test(url);
}
```

Дополнительно (рекомендуется):

- style.json, glyphs, sprites (пути `/styles/`, `/fonts/`, `/sprites/`) — **cache-first** в `SHELL_CACHE` (или отдельный кэш), т.к. это маленькие стабильные файлы;
- `.pbf` тайлы — stale-while-revalidate в `TILE_CACHE` с лимитом (текущий `TILE_CACHE_LIMIT = 1000` можно оставить);
- при подмене `CACHE_VERSION` (в `Dockerfile.web` уже штампуется `__BUILD_ID__`) старые кэши инвалидируются — ок.

Проверить CORS: OpenFreeMap отдаёт `Access-Control-Allow-Origin: *` (стиль и тайлы), поэтому SW-кэширование работает.

### 2.2 Basemap-адаптер (рефакторинг `web/js/core/ui.ts`)

Цель — единый интерфейс базового слоя и меньше дублирования:

1. Выделить интерфейс (id, create(map), remove(), setStyle?) и оставить `TILE_PROVIDERS` как реестр: `vector-light`, `dark`, `osm`.
2. **Day/night:** вместо «удалить слой → создать новый → перекрасить» держать один `L.maplibreGL`-слой и переключать `glMap.setStyle(...)`. Плагин (`maplibre-gl-leaflet.js`) не имеет `setStyle` на слое, но `getMaplibreMap().setStyle(url)` доступен. Проблема: публичный dark-стиль OpenFreeMap **не документирован** (URL `…/styles/dark` из приложенного плана не подтверждён). Варианты:
   - оставить текущую клиентскую «перекраску» (работает, но код грязный);
   - завести свой dark-стиль как статический файл (`/assets/styles/dark.json`), ссылающийся на те же тайлы OpenFreeMap — самый чистый путь.
3. **WebGL fallback:** `isWebGLSupported()`; если vector-режим выбран, а WebGL нет → `osm`.
4. **Починить OSM-URL** в `'osm'`-провайдере: `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png` → `https://tile.openstreetmap.org/{z}/{x}/{y}.png` (OSM больше не использует поддомены `{s}`; `subdomains: 'abc'` убрать).
5. **Не переименовывать ключи** провайдеров (`vector-light`, `dark`, `osm`): в localStorage лежит `preferred_tile_layer` со старыми значениями — переименование сломает сохранённый выбор без миграции.

### 2.3 Чистка мёртвого кода

- Объединить `hideMaplibreLabels`/`_applyDarkTheme` (дублирование логики скрытия symbol-слоёв).
- Удалить устаревшие .js-артефакты, если подтверждено, что страница их не грузит: `web/js/common.js`, `web/js/modules/popups.js`, `web/js/modules/notifications.js`, `web/js/telegram/integration.js` (webpack собирает из `.ts` в `dist`). Проверить `grep` по `map.html`/`map-bootstrap.js` перед удалением.

### 2.4 Русские лейблы (decision point + spike)

Сейчас подписи скрыты в обоих режимах — это осознанное решение кода (мешает читаемости? хотят чистую карту?). Если лейблы нужны:

1. Spike-страница (или консоль): `glMap.queryRenderedFeatures({layers:['place_label']})` — проверить, какие поля есть в тайлах (`name`, `name:ru`, `name:uk`, `name:en`).
2. Если `name:ru` есть — клиентская правка style.json перед передачей в `L.maplibreGL` (fetch стиля, замена `text-field` на `['coalesce', ['get','name:ru'], ['get','name']]`).
3. **Риск:** гарантий `name:ru` в тайлах OpenFreeMap нет; если критично — локальная генерация тайлов Одесской области (за рамками текущего рефакторинга).

Решение: по умолчанию оставить лейблы выключенными (текущее поведение), spike — опционально.

### 2.5 CSP (опционально)

`img-src 'self' data: blob: https:` → сузить до `https://tiles.openfreemap.org`. `connect-src` уже точечный. Никаких `https://*`.

### 2.6 PWA-проверка

- После 2.1: первый заход создаёт кэш; офлайн показывает ранее загруженные тайлы; нет bulk-кэширования «всего мира».

### 2.7 Обновление вендоренных библиотек (опционально, отдельная задача)

Вендоренный `maplibre-gl.js` — v2.4.0, quick-start OpenFreeMap рекомендует v5. Перед обновлением — проверить совместимость текущего liberty-стиля и плагина `maplibre-gl-leaflet` с новой версией (spike на `map.html?basemap=...`). Обновление — перекачать `maplibre-gl@5` и актуальный `@maplibre/maplibre-gl-leaflet` с unpkg в `assets/lib`. **Не смешивать с Фазой 2.2** — отдельный PR/коммит.

### 2.8 Приёмка Фазы 2

- [ ] Карта открывается, дефолт — OpenFreeMap liberty; в DevTools Network **нет** запросов к `tile.openstreetmap.org` в vector-режиме;
- [ ] style.json/`.pbf`/glyphs/sprites отдают 200, CORS `*` на месте;
- [ ] attribution виден (© OpenStreetMap, © OpenFreeMap);
- [ ] day/night переключение работает без ошибок консоли;
- [ ] маркеры/кластеры/попапы/фильтры/WebSocket работают поверх vector-слоя;
- [ ] при отключённом WebGL — автоматический fallback на `osm`;
- [ ] офлайн: ранее загруженные тайлы OpenFreeMap отображаются;
- [ ] в консоли нет `Content Security Policy … blocked`;
- [ ] проверен Telegram WebView (WebGL, нет белого экрана).

---

## 5. Фаза 3 — Документация и гигиена

1. `README.md`: исправить описание стека («Leaflet + MapLibre GL layer + векторные тайлы OpenFreeMap, fallback OSM-растр»), таблицу сервисов (после Фазы 1 — 4 сервиса), поток данных, раздел «Структура репозитория».
2. Создать `docs/web.md` (README на него ссылается, файла нет): архитектура фронтенда, провайдеры тайлов, CSP, SW-кэширование, порядок сборки.
3. `docs/RULES_CORE.md` — правка из Фазы 1.
4. Удалить `.pyc`-артефакты `core/__pycache__`, `processor/__pycache__` (могут мешать grep-проверкам), если они не в `.gitignore`-исключениях — проверить.
5. Финальный коммит.

---

## 6. Фаза 4 — Итоговый чек-лист приёмки

- [ ] `docker compose up -d --build` — стек из 4 сервисов, все healthy;
- [ ] `/health/ready` → 200;
- [ ] `/metrics` → 404 (мониторинг удалён);
- [ ] `pytest -q` — зелёный;
- [ ] `npm run typecheck` и `npm run build` в `web/` — без ошибок;
- [ ] карта: OpenFreeMap liberty по умолчанию, day/night, fallback OSM, офлайн-тайлы, Telegram WebView;
- [ ] `grep -rn "prometheus" core/ processor/ nginx.conf docker-compose.yml` — чисто;
- [ ] README и docs/web.md актуальны.

---

## 7. Риски

| Риск | Уровень | Митигация |
|---|---|---|
| Полное удаление метрик из core/processor заденет логику | Низкий | Метрики — «записывающий» код без побочных эффектов; вариант A как откат |
| OpenFreeMap недоступен | Средний | Fallback `osm` (уже есть) |
| Нет `name:ru` в тайлах | Высокий (если нужны лейблы) | Spike 2.4; при критичности — локальные тайлы |
| Вендоренные maplibre v2.4 vs актуальный стиль | Средний | Отдельная задача 2.7, spike перед обновлением |
| localStorage `preferred_tile_layer` при переименовании ключей | Низкий | Не переименовывать ключи (2.2.5) |

---

## 8. Порядок внедрения

1. **Фаза 0** — git snapshot (обязательно, 5 минут).
2. **Фаза 1** — удаление мониторинга (независима, низкий риск).
3. **Фаза 2.1** — SW-кэш OpenFreeMap (быстрая победа, заметно улучшает офлайн).
4. **Фаза 2.2–2.6** — basemap-адаптер, чистка, лейблы (spike).
5. **Фаза 3** — документация.
6. **Фаза 4** — приёмка.

Каждая фаза — отдельный коммит, чтобы можно было откатиться точечно.
