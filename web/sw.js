// sw.js — PWA service worker for Survival Map.
//
// Precaches the app shell (HTML/CSS/JS/Leaflet/icons) so the frontend launches
// with no network. Event data is NOT cached here — it lives in localStorage
// and arrives via WebSocket.

// __BUILD_ID__ подставляется на сборке (Dockerfile.web) — таймстамп билда.
// Любой деплой меняет CACHE_VERSION → старая оболочка инвалидируется в activate.
const CACHE_VERSION = '__BUILD_ID__-2';
const SHELL_CACHE = `survival-shell-${CACHE_VERSION}`;
const TILE_CACHE = `survival-tiles-${CACHE_VERSION}`;
const TILE_CACHE_LIMIT = 300;

// Canonical (un-versioned) app-shell URLs to precache.
const SHELL_ASSETS = [
    '/map.html',
    '/index.html',
    // Bootstrap scripts externalized from the HTML (strict CSP) — part of the
    // app shell, must be precached so the offline gate keeps working.
    '/assets/js/gate.js',
    '/assets/js/map-setup.js',
    '/assets/js/map-bootstrap.js',
    '/manifest.webmanifest',
    '/css/styles.css',
    '/assets/images/pig.png',
    '/assets/images/cops.png',
    '/assets/images/bus.png',
    '/assets/images/daynight.svg',
    '/assets/images/legend-info.svg',
    '/assets/images/banner.svg',
    '/assets/images/question.svg',
    '/dist/js/common.js',
    '/dist/js/core/store.js',
    '/dist/js/core/local_cache.js',
    '/dist/js/core/websocket.js',
    '/dist/js/core/event_manager.js',
    '/dist/js/core/token-manager.js',
    '/dist/js/core/map.js',
    '/dist/js/core/data.js',
    '/dist/js/core/ui.js',
    '/dist/js/modules/popups.js',
    '/dist/js/modules/notifications.js',
    '/js/telegram/integration.js'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(SHELL_CACHE)
            // Per-asset add so a single 404 cannot abort the whole precache.
            .then((cache) => Promise.all(
                SHELL_ASSETS.map((url) => cache.add(url).catch((e) => {
                    console.warn('[SW] precache miss:', url, e);
                }))
            ))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys.filter((k) => k !== SHELL_CACHE && k !== TILE_CACHE)
                    .map((k) => caches.delete(k))
            ))
            .then(() => self.clients.claim())
    );
});

// Normalize a request URL to its canonical cache key:
//   - drop the query string (the ?t=<timestamp> cache-buster);
//   - drop the nginx /__v__/<token> versioning suffix.
function shellCacheKey(url) {
    const u = new URL(url);
    let path = u.pathname;
    const vIdx = path.indexOf('/__v__/');
    if (vIdx !== -1) path = path.slice(0, vIdx);
    return u.origin + path;
}

function isTileRequest(url) {
    return /tile\.openstreetmap\.org|basemaps\.cartocdn\.com/.test(url);
}

async function trimCache(cacheName, limit) {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    for (let i = 0; i < keys.length - limit; i++) {
        await cache.delete(keys[i]);
    }
}

self.addEventListener('fetch', (event) => {
    const req = event.request;
    if (req.method !== 'GET') return;

    const url = req.url;

    // API + WebSocket — always network, never cached.
    if (url.indexOf('/api/') !== -1 || url.indexOf('/ws') !== -1) return;

    // Map tiles — stale-while-revalidate in a capped cache.
    if (isTileRequest(url)) {
        event.respondWith(
            caches.open(TILE_CACHE).then(async (cache) => {
                const cached = await cache.match(req);
                const network = fetch(req).then((res) => {
                    if (res && res.status === 200) {
                        cache.put(req, res.clone());
                        trimCache(TILE_CACHE, TILE_CACHE_LIMIT);
                    }
                    return res;
                }).catch(() => cached);
                return cached || network;
            })
        );
        return;
    }

    // Telegram SDK — network-first (may update), cache fallback for offline.
    if (url.indexOf('telegram.org') !== -1) {
        event.respondWith(
            fetch(req).then((res) => {
                const copy = res.clone();
                caches.open(SHELL_CACHE).then((c) => c.put(req, copy));
                return res;
            }).catch(() => caches.match(req))
        );
        return;
    }

    // Same-origin app shell — cache-first against the normalized key, with a
    // background refresh (stale-while-revalidate).
    if (new URL(url).origin === self.location.origin) {
        const key = shellCacheKey(url);
        event.respondWith(
            caches.open(SHELL_CACHE).then(async (cache) => {
                const cached = await cache.match(key);
                if (cached) {
                    fetch(req).then((res) => {
                        if (res && res.status === 200) cache.put(key, res.clone());
                        else if (res && (res.status === 404 || res.status === 410)) cache.delete(key);
                    }).catch(() => {});
                    return cached;
                }
                try {
                    const res = await fetch(req);
                    if (res && res.status === 200) cache.put(key, res.clone());
                    return res;
                } catch (e) {
                    // Offline navigation → fall back to the cached map shell.
                    if (req.mode === 'navigate') {
                        const shell = await cache.match(self.location.origin + '/map.html');
                        if (shell) return shell;
                    }
                    return Response.error();
                }
            })
        );
        return;
    }

    // Other cross-origin requests — passthrough (default network).
});
