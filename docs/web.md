# Web microservice

Сервис `web` (контейнер из `Dockerfile.web`) = nginx, который раздаёт
скомпилированный фронтенд и проксирует API/WebSocket на сервис `core`.
Фронтенд — vanilla TypeScript PWA Telegram Mini App: карта на **Leaflet**
(базовый слой — вектор `MapLibre GL` через `maplibre-gl-leaflet`), state — единый
`zustand` store, события из WebSocket агрегируются client-side.

См. `web/CLAUDE.md` — 8 архитектурных правил-инвариантов.

## Технологический стек

| Компонент | Назначение |
|-----------|-----------|
| `leaflet` 1.9.x | Карта + per-feature слои (markers/circles/polylines/polygons) |
| `leaflet.markercluster` 1.5.x | Кластеризация маркеров событий (chunkedLoading) |
| `maplibre-gl` + `maplibre-gl-leaflet` | Вектор-баземап (OpenFreeMap positron) |
| `zustand` 5.x | Reactive store с persistence subscription |
| webpack 5 | Bundle js/* → dist/js/* (production mode) |
| Telegram WebApp SDK | Через `window.Telegram.WebApp` global |
| nginx:alpine | Static serving + reverse-proxy |

## Архитектура модулей

```
web/
├── index.html              # Gate page: /api/validate-init → JWT
├── map.html                # Map page: gate-check → loadScript dist/js/*
├── sw.js                   # Service worker — precaches app shell
├── manifest.webmanifest    # PWA manifest
├── js/
│   ├── common.js           # serverNow, hapticFeedback, showNotification
│   ├── core/
│   │   ├── store.ts        # Zustand store (eventsById, filters, TTL)
│   │   ├── local_cache.ts  # localStorage persistence adapter
│   │   ├── websocket.ts    # /ws connection + reconnect + heartbeat
│   │   ├── event_manager.ts# store.subscribe → rAF scheduler
│   │   ├── map.ts          # popup creators
│   │   ├── ui.ts           # bootstrapUI, initializeMap, renderFromCache
│   │   └── token-manager.js# JWT refresh loop
│   ├── modules/
│   │   ├── popups.js       # Legend popup, click handlers
│   │   └── notifications.js# New-event notification rendering
│   └── telegram/
│       └── integration.js  # tg.WebApp wrapper, theme, haptic
├── css/styles.css
└── dist/                   # webpack output (gitignored)
```

## Pipeline загрузки

```
1. Browser opens map.html → проверка token в sessionStorage
2. Нет токена → redirect /index.html → POST /api/validate-init → JWT
3. POST /api/config → session confirm → загрузка dist/js/*.js
4. Zustand store init → LocalCache.loadEvents ← localStorage
5. bootstrapUI / initializeMap (Leaflet + layer groups)
6. WebSocketManager.connect (JWT auth)
7. Snapshot (batch silent) + live push (notifications)
8. store.subscribe → rAF schedule → renderFromCache (инкрементный diff)
9. setInterval 30s → store.pruneExpired (60min TTL)
```

## TTL и серверное время

TTL событий анкорится на серверное время (`serverNow()`), не на локальные
часы устройства — защита от device clock skew.

## Haptic feedback

Цепочка fallback: Telegram HapticFeedback API → navigator.vibrate → CSS
`hapticPulse` (визуальная вибрация, срабатывает на всех платформах).
