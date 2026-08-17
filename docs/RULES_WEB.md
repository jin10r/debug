# Rules — Web Frontend

**Сервис:** `web/` (nginx + Leaflet PWA + Telegram Mini App)  
**Точка входа:** `map.html` (основной), `index.html` (gate)  
**Сборка:** `webpack --mode production`

---

## 1. Архитектурные правила

### R-W1: Store — единственный источник правды

Все данные событий хранятся в `zustand` store (`js/core/store.ts`). localStorage — только persistence adapter.

```typescript
// ✅ Правильно — читаем из store
const events = window.store.getState().getFilteredItems();

// ❌ Неправительно — читаем напрямую из cache
const events = window.localCache.getEvents();
```

**Правило:** WebSocket пушит события прямо в store. Cache синхронизируется из store.

### R-W2: Reactive rendering

Store использует `revision` counter для отслеживания изменений. Подписчики (map renderer) перерисовываются при любом изменении:

```typescript
// store.ts
revision: number;  // bumped on every mutation

// event_manager.ts — подписка на store
window.store.subscribe(() => {
    renderFromCache();
});
```

### R-W3: Per-feature WebSocket protocol

WebSocket протокол работает с отдельными Feature (не FeatureCollection):

```
Server → Client: {type: "feature", data: GeoJSON Feature}
Server → Client: {type: "events_snapshot_end"}
Client → Server: {type: "get_events", since_timestamp: "...", since_id: <int>}
```

**Правило:** Снапшот (batch sync) буферизуется и отдаётся тихо. Live push — мгновенно.

**Catch-up watermark:** клиент шлёт `since_id` = max event id в store (см. `getLatestId`). `event_time` для водяного знака НЕ годится: backfill исторических сообщений даёт новые id при старом `event_time`, и catch-up по времени такие события теряет навсегда (баг «2 события на карте»). Сервер при наличии `since_id` игнорирует `since_timestamp`.

### R-W4: Self-healing WebSocket

WebSocket автоматически переподключается при:
- `visibilitychange` (вкладка снова видима)
- `online` (сеть вернулась)
- `tg:activated` (Telegram Mini App активирован)

```typescript
// reconnectNow() — немедленный reconnect без backoff
private reconnectNow(reason: string): void {
    this.reconnectAttempts = 0;
    this.connect();
}
```

**Правило:** Backoff с jitter (±20%) для предотвращения thundering herd.

### R-W5: Heartbeat — ping/pong

```typescript
PING_INTERVAL_MS = 25_000;  // отправляем ping каждые 25s
PONG_TIMEOUT_MS  = 15_000;  // ждём pong 15s
maxMissedPongs   = 2;       // 2 пропущенных pong → force reconnect
```

**Правило:** Pong timeout запускается после каждого ping. При превышении `maxMissedPongs` — закрытие сокета.

---

## 2. Правила TypeScript

### R-W6: strict: false (текущее состояние)

```json
// tsconfig.json
{
  "compilerOptions": {
    "strict": false,
    "noImplicitAny": false,
    "strictNullChecks": false
  }
}
```

**Правило:** При добавлении новых `.ts` файлов — стремиться к strict-подобной типизации через JSDoc или явные аннотации.

### R-W7: Типы для GeoJSON

Все типы гео-данных определены в `js/types/geojson.ts`:

```typescript
export interface EventFeature {
    type: 'Feature';
    geometry: GeoJSON.Geometry;
    properties: {
        id: number;
        layer: EventLayer;
        strategy: string;
        description: string;
        photo_url?: string;
        time: string;
        matches?: GeoMatch[];
    };
}

export type EventLayer = 'pig' | 'cops' | 'bus' | 'traffic';
```

**Правило:** Не использовать `any` для GeoJSON — использовать типы из `geojson.ts`.

### R-W8: Модульная система

Каждый модуль — отдельный entry point в webpack:

```javascript
// webpack.config.js
entry: {
    'js/common': './js/common.js',
    'js/core/store': './js/core/store.ts',
    'js/core/websocket': './js/core/websocket.ts',
    'js/core/event_manager': './js/core/event_manager.ts',
    'js/core/map': './js/core/map.js',
    'js/core/data': './js/core/data.js',
    'js/modules/popups': './js/modules/popups.js',
    'js/modules/notifications': './js/modules/notifications.js',
}
```

**Правило:** Каждый entry загружается как отдельный `<script>` тег. splitChunks отключён.

### R-W9: Глобальные интерфейсы

Модули общаются через `window` (не ES modules imports между entry points):

```typescript
// store.ts
window.store = store;

// data.js — bridge к store
window.getFilteredDataForRendering = function() {
    return window.store.getState().getFilteredItems();
};

// map.js
window.renderFromCache = function() {
    const data = window.getFilteredDataForRendering();
    // ... render on map
};
```

**Правило:** `window` используется как dependency injection container.

---

## 3. Правила работы с картой

### R-W10: Leaflet + MapLibre

Карта использует Leaflet с MapLibre GL для векторных тайлов:

```html
<!-- assets/lib -->
leaflet.js, leaflet.css
maplibre-gl.js, maplibre-gl.css
maplibre-gl-leaflet.js
leaflet.markercluster.js
```

**Правило:** Не смешивать Leaflet и MapLibre API для одного слоя.

### R-W11: Маркеры с кластеризацией

События отображаются через `L.markerClusterGroup`:

```javascript
const markers = L.markerClusterGroup({
    maxClusterRadius: 50,
    spiderfyOnMaxZoom: true,
    showCoverageOnHover: false,
});
```

### R-W12: Цвета слоёв

| Слой | Цвет маркера | Описание |
|------|--------------|----------|
| `pig` | 🟡 Жёлтый | Общие события |
| `cops` | 🔵 Синий | Полиция/военные |
| `bus` | 🟢 Зелёный | Транспорт |
| `traffic` | 🔴 Красный | ДТП/пробки |

### R-W13: GeoJSON rendering

События рендерятся как GeoJSON Feature на карте:

```javascript
function renderFeature(feature) {
    const { geometry, properties } = feature;
    const marker = L.geoJSON(feature, {
        pointToLayer: (feat, latlng) => {
            return L.circleMarker(latlng, {
                radius: 8,
                fillColor: getLayerColor(feat.properties.layer),
                color: '#333',
                weight: 1,
                fillOpacity: 0.8,
            });
        }
    });
    markers.addLayer(marker);
}
```

---

## 4. Правила кэширования

### R-W14: localStorage для persistence

События сохраняются в `localStorage` для offline-first:

```typescript
// local_cache.ts
const CACHE_KEY = 'survival_events';
const CACHE_TTL = 60 * 60 * 1000;  // 1 час

export function saveEvents(events: EventFeature[]): void {
    localStorage.setItem(CACHE_KEY, JSON.stringify({
        events,
        timestamp: Date.now(),
    }));
}
```

**Правило:** Cache — fallback. При наличии WebSocket connection store является авторитетным источником.

### R-W15: Server clock synchronization

Клиент синхронизирует часы с сервером через timestamp в WebSocket:

```typescript
// websocket.ts
if (typeof data.timestamp === 'string') {
    const serverMs = Date.parse(data.timestamp);
    if (!Number.isNaN(serverMs)) {
        window.serverClockOffsetMs = serverMs - Date.now();
    }
}
```

**Правило:** Временные фильтры (15/30/60 min) используют серверное время, не клиентское.

### R-W16: TTL prune

События автоматически удаляются по истечении TTL (60 минут):

```typescript
// store.ts
const TTL_MS = 60 * 60 * 1000;
const FUTURE_TOLERANCE_MS = 5 * 60 * 1000;

function isAcceptable(feature: EventFeature): boolean {
    const t = getEventTime(feature);
    if (!t) return true;
    const age = window.serverNow() - t.getTime();
    return age <= TTL_MS && age >= -FUTURE_TOLERANCE_MS;
}
```

**Правило:** Hard cap 5000 событий — при превышении удаляются 10% самых старых.

### R-W17: Cache-Control в nginx

| Тип контента | Cache-Control | Пример |
|-------------|---------------|--------|
| HTML | `no-cache, must-revalidate` | index.html, map.html |
| JS (наш код) | `no-cache` | /js/* |
| JS (vendor) | `public, immutable`, 7d | /dist/* |
| CSS | `public, immutable`, 30d | /css/* |
| Изображения | `public, immutable`, 365d | *.jpg, *.png |
| Service Worker | `no-cache, no-store` | sw.js |
| Media photos | `public, immutable`, 30d | /media/events/* |

---

## 5. Правила безопасности

### R-W18: Content Security Policy

```nginx
# map.html
add_header Content-Security-Policy "
    default-src 'self';
    script-src 'self' https://telegram.org;
    style-src 'self' 'unsafe-inline';
    img-src 'self' data: blob: https:;
    connect-src 'self' wss: ws: https://tiles.openfreemap.org;
    font-src 'self' data:;
    worker-src 'self' blob:;
    frame-ancestors 'self';
";
```

**Правило:** `script-src` строгий — только `'self'` + Telegram SDK. Нет `unsafe-inline` для script.

### R-W19: Security headers

```nginx
add_header X-Frame-Options "SAMEORIGIN";
add_header X-Content-Type-Options "nosniff";
add_header Referrer-Policy "strict-origin-when-cross-origin";
```

### R-W20: Telegram initData validation

Валидация Telegram initData через HMAC-SHA256:

```javascript
// token-manager.js
async function validateInitData(initData) {
    const response = await fetch('/api/validate-init', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ init_data: initData }),
    });
    return response.ok;
}
```

**Правило:** Клиент НЕ верифицирует подпись — это делает сервер (`core/api/auth.py`).

### R-W21: JWT storage

JWT хранится в `sessionStorage` (не `localStorage`):

```javascript
sessionStorage.setItem('access_token', token);
```

**Правило:** `sessionStorage` очищается при закрытии вкладки — более безопасно чем `localStorage`.

---

## 6. Правила Rate Limiting

### R-W22: Edge rate limiting (nginx)

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=auth:10m rate=1r/s;
```

| Зона | Лимит | Burst |
|------|-------|-------|
| `/api/*` | 10 req/s | 20 |
| `/api/validate-init` | 1 req/s | 5 |

### R-W23: App-level rate limiting (core)

```python
# core/middlewares/ratelimit.py
rate_limiter = RateLimiter(
    default_limit=60,      # 60 requests per minute
    window_seconds=60,
    cleanup_interval=300
)
```

**Правило:** Двухуровневый rate limiting: edge (nginx) + app (core).

---

## 7. Правила PWA

### R-W24: Service Worker

`sw.js` кэширует статические ресурсы для offline-first:

```javascript
// sw.js
const CACHE_NAME = 'survival-map-v1';
const STATIC_ASSETS = [
    '/',
    '/map.html',
    '/js/core/store.js',
    '/js/core/websocket.js',
    '/css/styles.css',
];
```

**Правило:** Service Worker НЕ кэширует API responses — только статику.

### R-W25: Manifest

```json
// manifest.webmanifest
{
    "name": "Survival Map",
    "short_name": "SurvivalMap",
    "start_url": "/map",
    "display": "standalone",
    "background_color": "#1a1a2e",
    "theme_color": "#e94560"
}
```

---

## 8. Правила сборки

### R-W26: Webpack configuration

```javascript
// webpack.config.js
module.exports = {
    optimization: {
        minimize: true,
        splitChunks: false,  // каждый entry self-contained
        runtimeChunk: false,
    },
    devtool: 'source-map',  // для debugging
};
```

**Правило:** `splitChunks: false` — каждый entry загружается отдельно (нет chunk loader в HTML).

### R-W27: TypeScript compilation

```json
// tsconfig.json
{
    "compilerOptions": {
        "target": "ES2020",
        "module": "ESNext",
        "moduleResolution": "bundler",
        "transpileOnly": true  // ts-loader option
    }
}
```

**Правило:** `transpileOnly: true` — быстрая сборка без type checking (type check через `npm run typecheck`).

### R-W28: Babel для JS

```javascript
// webpack.config.js
{
    test: /\.js$/,
    exclude: /node_modules/,
    use: {
        loader: 'babel-loader',
        options: {
            presets: ['@babel/preset-env']
        }
    }
}
```

**Правило:** Babel транспилирует JS для совместимости с older browsers (Telegram WebView).

---

## 9. Правила UX

### R-W29: Offline indicator

При потере WebSocket connection показывается индикатор:

```javascript
window.updateOnlineStatus = function(connected) {
    const indicator = document.getElementById('online-status');
    if (indicator) {
        indicator.className = connected ? 'online' : 'offline';
        indicator.textContent = connected ? '● Live' : '● Offline';
    }
};
```

### R-W30: Push notifications

Новые события push-уведомлениями (только для live, не для batch sync):

```javascript
window.webSocketManager.onFeature = (feature) => {
    const isNew = window.store.getState().addEvent(feature);
    if (isNew) {
        notifyNewEvent(feature);  // push notification
    }
};
```

### R-W31: Time filter UI

Три варианта фильтра: 15, 30, 60 минут:

```javascript
window.updateTimeFilter = function(minutes) {
    window.store.getState().updateTimeFilter(minutes);
};
```

---

## 10. Антипаттерны (ЗАПРЕЩЕНО)

| Антипаттерн | Почему | Правило |
|-------------|--------|---------|
| Частичный рендер без store | Несогласованное состояние | R-W1 |
| `any` для GeoJSON | Потеря типизации | R-W7 |
| Кэширование API в localStorage | Stale data | R-W14 |
| `unsafe-inline` для script | XSS vulnerability | R-W18 |
| `localStorage` для JWT | Persistent exposure | R-W21 |
| Отсутствие TTL prune | Memory leak | R-W16 |
| Синхронный fetch в main thread | UI freeze | R-W2 |
| vendor libs в `no-cache` | Performance regression | R-W17 |

---

*Правила основаны на анализе web/ — июль 2026*
