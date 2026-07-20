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

These 8 rules are the architectural contract. Any frontend change must keep all
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
(`Point`/`LineString`/`Polygon`/`MultiPolygon`) — an unhandled type silently drops
the event.

## 8. Lightweight final image

The Docker build is multi-stage (`Dockerfile.web`): node/npm live only in the
builder stage. The final image is `nginx:alpine` plus static assets — no
`node_modules`, no build tooling. Keep dependencies minimal.
