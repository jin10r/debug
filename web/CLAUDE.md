# Frontend development rules — Survival Map

The frontend (`web/`) is a standalone PWA microservice: a Telegram Mini App map
of events. Vanilla TypeScript, no UI framework. Webpack compiles `js/**` entry
points to `dist/js/**`; nginx serves the static result. State lives in a single
zustand store (`js/core/store.ts`); the map framework is **Leaflet** (`L.map`),
with a **MapLibre GL vector-tile basemap** mounted as a Leaflet layer via
`maplibre-gl-leaflet` (`L.maplibreGL`) — OpenFreeMap positron tiles with a custom
Telegram palette and labels hidden. Events render as **per-feature Leaflet layers**
(markers/circles/polylines/polygons), not GeoJSON `setData` sources; `renderFromCache`
(`js/core/ui.js`) does an incremental id-keyed diff over those layers.

These 9 rules are the architectural contract. Any frontend change must keep all
of them true.

## 1. PWA microservice — works online and offline

The frontend is an independent service. A service worker (`sw.js`) precaches the
app shell (HTML/CSS/JS bundles/icons) so the app loads with no network. Event
data persists in `localStorage`. Offline, the app still renders and filters
events from the local cache — never gate rendering on a live connection.

## 2. Validation gate — no components before backend confirms access

The frontend renders only when:
- opened in Telegram WebView **and** validation is enabled, or
- opened in any WebView **and** validation is disabled.

Otherwise redirect to `REDIRECT_URL`. **No frontend component (`dist/js/*`) is
loaded until the backend confirms a valid session.** `map.html` must request
`/api/config` first and inject app scripts only on `200`. A `401` (after one
refresh attempt) bounces to `/index.html`, which redirects to `REDIRECT_URL`.
Offline (network error, not `401`) is trusted and proceeds from cache.

**Entry point flow (`/` → `index.html` → `gate.js`):**
1. `gate.js` fetches `/api/validation-config` to get `telegram_validation_enabled` and `redirect_url`.
2. If validation disabled → dev mode, redirect to `/map.html` with dev tokens.
3. If validation enabled:
   a. Check `window.Telegram.WebApp` exists → if not, redirect to `REDIRECT_URL`.
   b. Check `initData` exists → if not, redirect to `REDIRECT_URL`.
   c. POST `/api/validate-init` with `initData` → server validates HMAC signature.
   d. If valid → store JWT tokens in `sessionStorage`, redirect to `/map.html`.
   e. If invalid → redirect to `REDIRECT_URL`.

**Server-side enforcement:** Backend validates JWT on every `/api/*` request and
WebSocket `auth` message. Client-side gate is a UX filter; backend is the
authority.

## 3. Incremental local cache — reactive FeatureCollection from local data

Events enter the local cache incrementally; WebSocket delivers one `feature` per
event. The `FeatureCollection` is built reactively, client-side, **only** from
local-storage-backed store data. The store (`js/core/store.ts`) is the single
source of truth; `local_cache.ts` is just the `localStorage` persistence
adapter. Losing the connection must not stop display or filtering.

## 4. Full load on connect, then live stream — notify only new events

On first connect, fetch all existing events from the backend (silent batch).
After that, each newly pushed live event is appended to the stack. **Only new
live events trigger a notification** — the initial full load and reconnect
catch-up batches are silent. The snapshot boundary is the `events_snapshot_end`
WebSocket message: features before it are batch (silent), after it are live.

## 5. Haptic feedback on every notification

Every user-facing notification — including errors — fires haptic feedback.
Route all notifications through `window.showNotification()`, which always calls
`window.hapticFeedback()`.

## 6. Event TTL — 60 minutes

Each event has a 60-minute TTL measured from the `time` field inside the
`feature` object (anchored to server/Kiev clock via `window.serverNow()`, not
the device clock). Expired events are pruned from the store on a periodic tick
and disappear from the map. TTL keeps the local cache from growing unbounded.

## 7. Optimised for Telegram WebView

Target slow Telegram WebView, not just desktop browsers. The map framework is
**Leaflet**; the basemap is a MapLibre GL vector-tile layer mounted via
`maplibre-gl-leaflet` (libs loaded in `map.html`). Events render as per-feature
Leaflet layers built by `window.create{Circle,Polyline,Polygon,MultiPolygon}`
(`js/core/map.js`), grouped into three layer groups created once at boot.

Render incrementally: `renderFromCache` (`js/core/ui.js`) keeps an id→layers map
(`renderedById`) and **diffs** the filtered feature set — adds new ids, removes
dropped ids, re-creates only changed features, skips unchanged — instead of
rebuilding everything. Rendering is driven reactively by store changes via
`event_manager.ts` (one `renderFromCache` per `requestAnimationFrame`); no
timing-based hacks (`setTimeout`-forced renders). Keep the main thread
responsive; defer heavy work with `requestAnimationFrame`.

**Layer groups (Leaflet):**
| Group | Holds | Fed by |
|-------|-------|--------|
| `markerClusterGroup` | event markers + cluster circles (`chunkedLoading`) | non-random Point markers |
| `geometryLayerGroup` | accuracy circles (200 m), polylines, polygons, multipolygons | LineString / Polygon / MultiPolygon + точные Point-круги |
| `randomMarkersGroup` | unclustered markers | events with `strategy === 'random'` |

`addRenderedEvent`'s geometry `switch` must cover every type the backend can emit
(`Point`/`LineString`/`Polygon`/`MultiPolygon`/`MultiLineString`/`MultiPoint`/`GeometryCollection`) — an unhandled type silently drops
the event.

## 8. Automatic reconnection — wait and resume on connection loss

When the WebSocket connection drops, the frontend must:
1. **Show connection indicator** — display "⚠️ Нет связи с сервером" overlay.
2. **Retry with exponential backoff** — 1s → 1.5s → 2.25s → … → 30s cap, up to 10 attempts. Jitter ±20% to prevent thundering herd.
3. **Immediate reconnect on wake events** — `visibilitychange` (tab visible), `online` (network restored), Telegram `activated` (app foreground). These reset the backoff counter and connect immediately.
4. **Heartbeat detection** — ping every 25s, pong timeout 15s. After 2 consecutive missed pongs, force close (code 4000) which triggers backoff reconnect.
5. **Catch-up on reconnect** — `requestEvents()` sends `get_events` with `since_timestamp` from the store's latest event. Server sends only events newer than that timestamp as a silent batch (`events_snapshot_end` boundary).
6. **Resume live stream** — after snapshot boundary, new events arrive as live pushes with notifications.

The connection indicator hides automatically on `handleOpen`. The store persists events to `localStorage`; on boot, events are hydrated from cache before WS connects — the map renders immediately from local data, then syncs silently.

## 9. Lightweight final image

The Docker build is multi-stage (`Dockerfile.web`): node/npm live only in the
builder stage. The final image is `nginx:alpine` plus static assets — no
`node_modules`, no build tooling. Keep dependencies minimal.
