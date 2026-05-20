// ui.js — карта на MapLibre GL JS для survival_map.
//
// Архитектура data-driven: GeoJSON-источники + слои поверх. renderFromCache
// делает setData; MapLibre сам диффит и перерисовывает только изменения —
// никаких ручных «addLayer/removeLayer» на каждое событие, как было с
// Leaflet.markercluster.

import maplibregl from 'maplibre-gl';

// Доступ из других модулей (например, для маркера попапов из popups.js,
// если понадобится в будущем).
window.maplibregl = maplibregl;

// Совместимость с предыдущей моделью (использовалось в initializeAdSquares).
window.adSquares = {};

// ============================================================================
// Тайловые провайдеры (raster для MapLibre)
// ============================================================================
const TILE_PROVIDERS = {
    osm: {
        tiles: [
            'https://a.tile.openstreetmap.org/{z}/{x}/{y}.png',
            'https://b.tile.openstreetmap.org/{z}/{x}/{y}.png',
            'https://c.tile.openstreetmap.org/{z}/{x}/{y}.png'
        ],
        attribution: '&copy; OpenStreetMap'
    },
    dark: {
        tiles: [
            'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
            'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
            'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
            'https://d.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png'
        ],
        attribution: '&copy; OpenStreetMap &copy; CARTO'
    }
};

let currentTileKey = 'osm';

function baseStyle(tileKey) {
    const p = TILE_PROVIDERS[tileKey];
    return {
        version: 8,
        sources: {
            'raster-base': {
                type: 'raster',
                tiles: p.tiles,
                tileSize: 256,
                attribution: p.attribution
            }
        },
        layers: [{
            id: 'base-tiles',
            type: 'raster',
            source: 'raster-base'
        }]
        // glyphs не подключаем — text-field на слоях не используем.
    };
}

window.switchTileLayer = function(tileKey) {
    if (!TILE_PROVIDERS[tileKey] || tileKey === currentTileKey) return;
    const map = window.currentMapInstance;
    if (!map) {
        console.error('[switchTileLayer] Map instance not available');
        return;
    }
    currentTileKey = tileKey;
    try { localStorage.setItem('preferred_tile_layer', tileKey); } catch (e) {}

    // setStyle пересоздаёт стиль — кастомные источники/слои нужно поднять снова
    // после события style.load.
    map.setStyle(baseStyle(tileKey));
    map.once('style.load', async () => {
        await ensureIconsLoaded(map);
        addCustomLayers(map);
        addQuestionOverlay(map);
        addAdBanner(map);
        attachLayerClickHandlers(map);
        if (typeof window.renderFromCache === 'function') window.renderFromCache();
    });

    console.log('[switchTileLayer] Switched to:', tileKey);
};

// ============================================================================
// Иконки слоёв (pig/cops/bus) — регистрируем в реестре MapLibre
// ============================================================================
function ensureIconsLoaded(map) {
    const entries = Object.entries(window.ICON_URLS || {});
    const tasks = entries.map(([layer, url]) =>
        new Promise((resolve) => {
            const iconId = (window.ICON_NAMES || {})[layer];
            if (!iconId) { resolve(); return; }
            if (map.hasImage(iconId)) { resolve(); return; }
            map.loadImage(url).then(image => {
                // MapLibre 4.x: { data: HTMLImageElement | ImageBitmap | ImageData }
                const data = image && image.data ? image.data : image;
                if (!map.hasImage(iconId)) {
                    try { map.addImage(iconId, data); }
                    catch (e) { console.warn('[ui] addImage failed:', iconId, e); }
                }
                resolve();
            }).catch(err => {
                console.error('[ui] icon load failed:', layer, url, err);
                resolve();
            });
        })
    );
    return Promise.all(tasks);
}

// ============================================================================
// Кастомные источники + слои (события)
// ============================================================================
function addCustomLayers(map) {
    if (!map.getSource('events-street')) {
        map.addSource('events-street', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] },
            cluster: true,
            clusterRadius: 50,
            clusterMaxZoom: 18
        });
    }
    if (!map.getSource('events-random')) {
        map.addSource('events-random', {
            type: 'geojson',
            data: { type: 'FeatureCollection', features: [] }
        });
    }

    // Кластерные кружки — размер по point_count (без текста: glyphs не
    // подключены; число точек кодируется ступенчатым радиусом).
    if (!map.getLayer('event-clusters')) {
        map.addLayer({
            id: 'event-clusters',
            type: 'circle',
            source: 'events-street',
            filter: ['has', 'point_count'],
            paint: {
                'circle-color': 'rgba(255, 87, 51, 0.85)',
                'circle-stroke-color': '#ffffff',
                'circle-stroke-width': 2,
                'circle-radius': [
                    'step', ['get', 'point_count'],
                    18,
                    10, 24,
                    30, 30,
                    100, 38
                ]
            }
        });
    }

    // Match-выражение «layer → icon-image» одинаково для street и random.
    const iconMatch = [
        'match', ['get', 'layer'],
        'bus',  window.ICON_NAMES.bus,
        'cops', window.ICON_NAMES.cops,
        window.ICON_NAMES.pig
    ];

    if (!map.getLayer('event-points-street')) {
        map.addLayer({
            id: 'event-points-street',
            type: 'symbol',
            source: 'events-street',
            filter: ['!', ['has', 'point_count']],
            layout: {
                'icon-image': iconMatch,
                'icon-size': 1,
                'icon-allow-overlap': true,
                'icon-ignore-placement': true
            }
        });
    }

    if (!map.getLayer('event-points-random')) {
        map.addLayer({
            id: 'event-points-random',
            type: 'symbol',
            source: 'events-random',
            layout: {
                'icon-image': iconMatch,
                'icon-size': 1,
                'icon-allow-overlap': true,
                'icon-ignore-placement': true
            }
        });
    }
}

// ============================================================================
// Click handlers для интерактивных слоёв
// ============================================================================
function attachLayerClickHandlers(map) {
    // Кластер → плавный зум на раскрытие.
    map.on('click', 'event-clusters', (e) => {
        const features = map.queryRenderedFeatures(e.point, { layers: ['event-clusters'] });
        if (!features.length) return;
        const clusterId = features[0].properties.cluster_id;
        const src = map.getSource('events-street');
        if (!src) return;
        src.getClusterExpansionZoom(clusterId)
            .then(zoom => {
                map.easeTo({
                    center: features[0].geometry.coordinates,
                    zoom: Math.min(zoom, 19)
                });
            })
            .catch(err => console.warn('[ui] getClusterExpansionZoom failed:', err));
    });

    function openMarkerPopup(e) {
        const feature = e.features && e.features[0];
        if (!feature) return;
        const coords = feature.geometry.coordinates.slice();
        const html = window.createPopupContent(feature.properties);
        new maplibregl.Popup({ closeButton: true, maxWidth: '400px', offset: 14 })
            .setLngLat(coords)
            .setHTML(html)
            .addTo(map);
    }

    map.on('click', 'event-points-street', openMarkerPopup);
    map.on('click', 'event-points-random', openMarkerPopup);

    ['event-clusters', 'event-points-street', 'event-points-random'].forEach(layerId => {
        map.on('mouseenter', layerId, () => { map.getCanvas().style.cursor = 'pointer'; });
        map.on('mouseleave', layerId, () => { map.getCanvas().style.cursor = ''; });
    });
}

// ============================================================================
// Overlay-картинки (вопрос-зона + рекламный баннер)
// ============================================================================
function addQuestionOverlay(map) {
    // bounds: [NW, NE, SE, SW] в формате [lng, lat] для MapLibre.
    const bounds = [
        [30.76985, 46.54304],
        [30.89285, 46.54304],
        [30.89285, 46.45304],
        [30.76985, 46.45304]
    ];
    const url = `/assets/images/overlay.svg?v=${Date.now()}`;

    if (!map.getSource('question-overlay')) {
        map.addSource('question-overlay', { type: 'image', url, coordinates: bounds });
    }
    if (!map.getLayer('question-overlay-layer')) {
        // beforeId='event-clusters' — оверлей под событиями.
        map.addLayer({
            id: 'question-overlay-layer',
            type: 'raster',
            source: 'question-overlay',
            paint: { 'raster-opacity': 1.0 }
        }, 'event-clusters');
    }
}

function addAdBanner(map) {
    const bounds = [
        [30.92288, 46.5240],
        [31.06208, 46.5240],
        [31.06208, 46.4370],
        [30.92288, 46.4370]
    ];
    const url = `/assets/images/banner.svg?v=${Date.now()}`;

    if (!map.getSource('ad-banner')) {
        map.addSource('ad-banner', { type: 'image', url, coordinates: bounds });
    }
    if (!map.getLayer('ad-banner-layer')) {
        map.addLayer({
            id: 'ad-banner-layer',
            type: 'raster',
            source: 'ad-banner',
            paint: { 'raster-opacity': 1.0 }
        }, 'event-clusters');
    }
    window.adSquares.ad1 = { bounds };
}

// Клики по оверлей-зонам ловим общим обработчиком: если в точке клика есть
// маркер/кластер — приоритет у его попапа (слой-специфичный handler уже
// открыл попап события).
function attachOverlayClick(map) {
    map.on('click', (e) => {
        const feats = map.queryRenderedFeatures(e.point, {
            layers: ['event-clusters', 'event-points-street', 'event-points-random']
        });
        if (feats.length > 0) return;

        const { lng, lat } = e.lngLat;

        if (lng >= 30.76985 && lng <= 30.89285 && lat >= 46.45304 && lat <= 46.54304) {
            new maplibregl.Popup({ closeButton: true, maxWidth: '300px' })
                .setLngLat([30.83135, 46.49804])
                .setHTML('Здесь отображаются события не имеющие привязки к местности, либо могут быть не точными!')
                .addTo(map);
            return;
        }

        if (lng >= 30.92288 && lng <= 31.06208 && lat >= 46.4370 && lat <= 46.5240) {
            const popupContent =
                `<h3>Исходный код приложения доступен на <a href="https://github.com/iseeupigs/iseeupigs-web" target="_blank">GitHub</a></h3>`
                + `<br>поблагодарить разработчика можно на <a href="https://bastyon.com/keep_alive_odessa?ref=PHQHKADhBPxxSwjiggV6G2BxSvy6TY1Lgb" target="_blank">bastyon</a>`;
            new maplibregl.Popup({ closeButton: true, maxWidth: '400px' })
                .setLngLat([lng, lat])
                .setHTML(popupContent)
                .addTo(map);
        }
    });
}

// ============================================================================
// Главная инициализация
// ============================================================================
window.initializeMap = function() {
    // Восстановить сохранённый выбор тайла
    try {
        const saved = localStorage.getItem('preferred_tile_layer');
        if (saved && TILE_PROVIDERS[saved]) currentTileKey = saved;
        else if (saved) localStorage.removeItem('preferred_tile_layer');
    } catch (e) { /* ignore */ }

    // MapLibre: центр — [lng, lat]; APP_CONFIG приходит [lat, lng] отдельными полями.
    const center = [window.APP_CONFIG.map_center_lng, window.APP_CONFIG.map_center_lat];
    const zoom = window.APP_CONFIG.map_default_zoom;

    const map = new maplibregl.Map({
        container: 'map',
        style: baseStyle(currentTileKey),
        center,
        zoom,
        minZoom: 11,
        maxZoom: 19,
        attributionControl: false,
        // Telegram WebView: компромисс по перфомансу
        antialias: false,
        fadeDuration: 150
    });

    // Нативный зум-контрол слева (правая сторона занята legend/daynight кнопками)
    map.addControl(new maplibregl.NavigationControl({ showCompass: false, showZoom: true }), 'top-left');

    window.setMapInstance(map);

    map.on('load', async () => {
        await ensureIconsLoaded(map);
        addCustomLayers(map);
        addQuestionOverlay(map);
        addAdBanner(map);
        attachLayerClickHandlers(map);
        attachOverlayClick(map);

        // Первичный рендер из store (после гидратации из localStorage)
        if (typeof window.renderFromCache === 'function') window.renderFromCache();
    });

    // Контролы и WebSocket — независимы от готовности карты
    initializeControls(map);
    initializeInteractionControls();
    if (typeof window.initializeWebSocket === 'function') window.initializeWebSocket();
};

// ============================================================================
// Online/offline индикатор (без изменений с Leaflet-версии)
// ============================================================================
window.updateOnlineStatus = function(isOnline) {
    const indicator = document.getElementById('connection-indicator');
    if (!indicator) return;
    indicator.style.display = isOnline ? 'none' : 'block';
    if (!isOnline) indicator.textContent = '⚠️ Нет связи с сервером';
};

// ============================================================================
// UI контролы (свайп-слайдер панелей, time filter, day/night, легенда) —
// не зависят от движка карты, перенесены без изменений.
// ============================================================================
function initializeControls(map) {
    const controlsContainer = document.getElementById('controlsContainer');
    const controlsSlider = document.getElementById('controlsSlider');
    const indicators = document.querySelectorAll('#controlsIndicators .dot');

    if (!controlsContainer || !controlsSlider) return;

    let startX = 0, currentX = 0, deltaX = 0, isSwiping = false, activePanel = 0;
    const panels = Array.from(controlsSlider.querySelectorAll('.controlPanel'));
    const panelCount = Math.max(1, panels.length);
    const stepPercent = 100 / panelCount;

    function setPanel(idx) {
        activePanel = Math.min(Math.max(idx, 0), panelCount - 1);
        controlsSlider.style.transform = `translateX(-${activePanel * stepPercent}%)`;
        indicators.forEach((el, i) => el.classList.toggle('active', i === activePanel));
        window.hapticFeedback('selection_changed');
    }

    controlsContainer.addEventListener('touchstart', e => {
        if (e.touches.length !== 1) return;
        startX = e.touches[0].clientX;
        currentX = startX;
        isSwiping = true;
        controlsSlider.style.transition = 'none';
    });
    controlsContainer.addEventListener('touchmove', e => {
        if (!isSwiping) return;
        currentX = e.touches[0].clientX;
        deltaX = currentX - startX;
        controlsSlider.style.transform = `translateX(calc(-${activePanel * stepPercent}% + ${deltaX}px))`;
    });
    controlsContainer.addEventListener('touchend', () => {
        if (!isSwiping) return;
        controlsSlider.style.transition = '';
        if (Math.abs(deltaX) > 40) {
            if (deltaX < 0 && activePanel < panelCount - 1) setPanel(activePanel + 1);
            else if (deltaX > 0 && activePanel > 0) setPanel(activePanel - 1);
            else setPanel(activePanel);
        } else {
            setPanel(activePanel);
        }
        isSwiping = false;
        deltaX = 0;
    });

    indicators.forEach((el, idx) => {
        el.addEventListener('click', () => setPanel(idx));
    });

    setPanel(0);

    // Time filter
    const realtimeControls = document.querySelector('#realtimeControls .buttons');
    if (realtimeControls) {
        realtimeControls.addEventListener('click', e => {
            if (e.target.tagName !== 'BUTTON') return;
            window.hapticFeedback('light');
            const newFilter = parseInt(e.target.dataset.minutes, 10);
            realtimeControls.querySelectorAll('button').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            if (typeof window.updateTimeFilter === 'function') window.updateTimeFilter(newFilter);
        });
        const currentFilter = (window.store && window.store.getState)
            ? window.store.getState().currentTimeFilter
            : (window.DEFAULT_TIME_FILTER || 30);
        realtimeControls.querySelector(`button[data-minutes="${currentFilter}"]`)?.classList.add('active');
        if (!realtimeControls.querySelector('.active')) {
            realtimeControls.querySelector(`button[data-minutes="${window.DEFAULT_TIME_FILTER || 30}"]`)?.classList.add('active');
        }
    }

    // Tile switcher
    const tileControls = document.querySelector('#mapTileControls .tile-buttons');
    if (tileControls) {
        tileControls.addEventListener('click', e => {
            if (e.target.tagName !== 'BUTTON') return;
            window.hapticFeedback('light');
            const tileKey = e.target.dataset.tile;
            tileControls.querySelectorAll('button').forEach(b => b.classList.remove('active'));
            e.target.classList.add('active');
            window.switchTileLayer(tileKey);
        });
        const activeTileButton = tileControls.querySelector(`button[data-tile="${currentTileKey}"]`);
        if (activeTileButton) {
            tileControls.querySelectorAll('button').forEach(b => b.classList.remove('active'));
            activeTileButton.classList.add('active');
        }
    }
}

function initializeInteractionControls() {
    const legendBtn = document.getElementById('legendBtn');
    const dayNightBtn = document.getElementById('dayNightBtn');
    const closeBtn = document.getElementById('closeCenterPopup');
    const overlay = document.getElementById('centerPopupOverlay');

    legendBtn?.addEventListener('click', () => {
        window.hapticFeedback('light');
        if (typeof window.showLegendPopup === 'function') window.showLegendPopup();
    });
    dayNightBtn?.addEventListener('click', () => {
        window.hapticFeedback('light');
        toggleDayNightMode();
    });
    closeBtn?.addEventListener('click', () => {
        window.hapticFeedback('light');
        if (typeof window.hideCenterPopup === 'function') window.hideCenterPopup();
    });
    overlay?.addEventListener('click', () => {
        window.hapticFeedback('light');
        if (typeof window.hideCenterPopup === 'function') window.hideCenterPopup();
    });
}

function toggleDayNightMode() {
    const newKey = currentTileKey === 'dark' ? 'osm' : 'dark';
    window.switchTileLayer(newKey);
    console.log('[DayNight] Switched to:', newKey);
}

// ============================================================================
// Инкрементный рендер: setData на каждый источник.
// MapLibre сам диффит и перерисовывает изменённые точки.
// ============================================================================
window.renderFromCache = function() {
    const map = window.currentMapInstance;
    if (!map) {
        console.error('[renderFromCache] Map instance not available');
        return;
    }
    if (!map.isStyleLoaded()) {
        // Стиль ещё не загружен — initializeMap сам вызовет renderFromCache
        // в map.on('load') после готовности источников.
        return;
    }
    const streetSrc = map.getSource('events-street');
    const randomSrc = map.getSource('events-random');
    if (!streetSrc || !randomSrc) return;

    const fc = window.getFilteredDataForRendering();
    const features = (fc && fc.features) ? fc.features : [];

    const street = [];
    const random = [];
    for (const f of features) {
        const pt = toPointFeature(f);
        if (!pt) continue;
        const isRandom = f.properties && f.properties.strategy === 'random';
        (isRandom ? random : street).push(pt);
    }

    streetSrc.setData({ type: 'FeatureCollection', features: street });
    randomSrc.setData({ type: 'FeatureCollection', features: random });
};

// Привести feature к Point: для LineString — серединная точка,
// для Polygon — центроид первого кольца. Symbol-слой принимает только Point.
function toPointFeature(f) {
    if (!f || !f.geometry) return null;
    let coords = null;
    switch (f.geometry.type) {
        case 'Point':
            coords = f.geometry.coordinates;
            break;
        case 'LineString': {
            const c = f.geometry.coordinates;
            if (!c || !c.length) return null;
            coords = c[Math.floor(c.length / 2)];
            break;
        }
        case 'Polygon': {
            const ring = f.geometry.coordinates && f.geometry.coordinates[0];
            if (!ring || !ring.length) return null;
            let sx = 0, sy = 0;
            for (const p of ring) { sx += p[0]; sy += p[1]; }
            coords = [sx / ring.length, sy / ring.length];
            break;
        }
        default:
            return null;
    }
    if (!Array.isArray(coords) || coords.length < 2) return null;
    return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [coords[0], coords[1]] },
        properties: f.properties || {}
    };
}

// ============================================================================
// Bootstrap UI — точка входа после store-гидратации и инициализации модулей.
// renderFromCache первый раз вызовется внутри map.on('load') в initializeMap.
// Дальнейшие обновления идут реактивно через event_manager → store.subscribe.
// ============================================================================
window.bootstrapUI = function() {
    window.initializeMap();
};

console.log('✅ ui.js (MapLibre GL) loaded');
