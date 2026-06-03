# Frontend development rules — Survival Map

The frontend (`web/`) is a standalone PWA microservice: a Telegram Mini App map
of events. Vanilla TypeScript, no UI framework. Webpack compiles `js/**` entry
points to `dist/js/**`; nginx serves the static result. State lives in a single
zustand store (`js/core/store.ts`); the map is **MapLibre GL JS** (native —
Leaflet has been removed entirely). Vector tiles from OpenFreeMap positron with
a custom Telegram palette and all labels hidden. GeoJSON sources power all
feature rendering; `renderFromCache` calls `source.setData()` on every update.

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

Target slow Telegram WebView, not just desktop browsers. Render incrementally:
MapLibre GeoJSON sources (`events-points` with `cluster: true`, `events-geo`)
are created once at boot, then `renderFromCache` calls `source.setData()` with
the full filtered FeatureCollection on every update — no per-object diffing,
no layer recreation. No timing-based hacks (`setTimeout`-forced renders) —
rendering must be driven reactively by store changes. Keep the main thread
responsive; defer heavy work with `requestAnimationFrame`. The MapLibre basemap
style is re-themed via `setPaintProperty` (day/night) — never `setStyle`, never
recreated, so GeoJSON layers on top stay untouched.

**Layer map:**
| Source | Layers | Purpose |
|--------|--------|---------|
| `events-points` | `clusters`, `cluster-count`, `unclustered-points` | Markers + cluster circles |
| `events-geo` | `geo-polygon-fill`, `geo-polygon-line`, `geo-line`, `geo-accuracy-circle` | Geometry + accuracy circles |
| `overlay-question` | `overlay-question-layer` | Question overlay image |
| `overlay-ad` | `overlay-ad-layer` | Banner ad image |

## 8. Lightweight final image

The Docker build is multi-stage (`Dockerfile.nginx`): node/npm live only in the
builder stage. The final image is `nginx:alpine` plus static assets — no
`node_modules`, no build tooling. Keep dependencies minimal.
