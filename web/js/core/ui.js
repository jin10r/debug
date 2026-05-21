// ui.js — UI инициализация, управление картой, попапы, оверлеи
// Архитектурно оптимизированная версия для Telegram Mini Apps
//
// Карта = Leaflet 1.9. Полотно (basemap) — vector-тайлы OpenFreeMap
// через MapLibre GL JS, встроенный в Leaflet через плагин
// @maplibre/maplibre-gl-leaflet (L.maplibreGL). Маркеры/кластеры/попапы/
// оверлеи остаются нативно-Leaflet'овыми и рендерятся поверх MapLibre
// canvas (markerPane z=600, popupPane z=700 vs tilePane z=200).
//
// Лейблов (symbol-слои) на карте нет — все скрываются через
// applyPaletteAndHideLabels после загрузки стиля.

// Инициализация глобальных переменных
window.adSquares = {};

// Палитры — порт Telegram-style из libre.html. Ключи 'osm'/'dark'
// сохранены ради совместимости с UI-кнопками и localStorage-флагом.
const STYLE_PALETTES = {
    osm: {
        // Light Telegram-style
        water: '#a6c8e6', land: '#f5f0e8', roads: '#ffffff',
        roadsAlt: '#e8e0d4', buildings: '#d4cdc4', parks: '#a8d5a3',
        labels: '#3d3d3d'
    },
    dark: {
        // Dark Telegram-style (тёмно-синяя суша, оранжевые primary roads)
        water: '#62a0ea', land: '#1a2e3d', roads: '#ffbe6f',
        roadsAlt: '#1a2e3d', buildings: '#5e5c64', parks: '#26a269',
        labels: '#c8b58d'
    }
};

const STYLE_URL = 'https://tiles.openfreemap.org/styles/positron';

let currentTileLayer = null;     // L.maplibreGL instance
let currentTileKey = 'osm';

/**
 * Классифицируем слой OpenFreeMap-стиля по id/source-layer/type —
 * нужно, чтобы знать, какой paint-property менять (line-color для дорог,
 * fill-color для воды и т.д.) и какие слои являются «лейблами» (symbol).
 *
 * Порт из libre.html.
 */
function classifyLayer(layer) {
    const id = (layer.id || '').toLowerCase();
    const srcLayer = (layer['source-layer'] || '').toLowerCase();
    const t = layer.type;
    if (t === 'background') return 'background';
    if (/water|ocean|sea|lake|river|pond|reservoir/.test(id) || /water|waterway|ocean/.test(srcLayer)) return 'water';
    if (/park|landcover|landuse_park|wood|forest|grass|cemetery|pitch|recreation|nature/.test(id)) return 'parks';
    if (/building/.test(id) || srcLayer === 'building') return 'buildings';
    if (/transit|rail|tram|subway|aerialway|ferry/.test(id)) return 'transit';
    if (t === 'line' && (/road|street|highway|motorway|trunk|service|residential|tertiary|secondary|primary|track|path|tunnel|bridge|transportation/.test(id) || /transportation|road/.test(srcLayer))) {
        if (/motorway|trunk|primary|major/.test(id)) return 'road-primary';
        return 'road-secondary';
    }
    if (/boundary|admin|country|state/.test(id)) return 'boundary';
    if (/landuse|land(?!cover)/.test(id)) return 'landuse';
    if (t === 'symbol') return 'label';   // Все символьные слои = лейблы
    return 'other';
}

/**
 * Применяем палитру (Telegram-style) к загруженному style'у MapLibre
 * через setPaintProperty + скрываем все symbol-слои (лейблы) через
 * setLayoutProperty('visibility','none').
 *
 * Идемпотентно — можно вызывать многократно (day/night toggle).
 * Кастомные слои отсутствуют (мы используем MapLibre только как
 * basemap), поэтому setStyle тут не нужен — setPaintProperty
 * достаточно.
 */
function applyPaletteAndHideLabels(glMap, palette) {
    const layers = glMap.getStyle().layers || [];
    layers.forEach((layer) => {
        const id = layer.id;
        const kind = classifyLayer(layer);
        try {
            switch (kind) {
                case 'background':
                    glMap.setPaintProperty(id, 'background-color', palette.land);
                    break;
                case 'landuse':
                    if (layer.type === 'fill') glMap.setPaintProperty(id, 'fill-color', palette.land);
                    break;
                case 'water':
                    if (layer.type === 'fill') glMap.setPaintProperty(id, 'fill-color', palette.water);
                    if (layer.type === 'line') glMap.setPaintProperty(id, 'line-color', palette.water);
                    break;
                case 'parks':
                    if (layer.type === 'fill') glMap.setPaintProperty(id, 'fill-color', palette.parks);
                    if (layer.type === 'line') glMap.setPaintProperty(id, 'line-color', palette.parks);
                    break;
                case 'buildings':
                    if (layer.type === 'fill') glMap.setPaintProperty(id, 'fill-color', palette.buildings);
                    if (layer.type === 'fill-extrusion') glMap.setPaintProperty(id, 'fill-extrusion-color', palette.buildings);
                    break;
                case 'road-primary':
                    glMap.setPaintProperty(id, 'line-color', palette.roads);
                    break;
                case 'road-secondary':
                    glMap.setPaintProperty(id, 'line-color', palette.roadsAlt);
                    break;
                case 'transit':
                    glMap.setPaintProperty(id, 'line-color', palette.roadsAlt);
                    glMap.setLayoutProperty(id, 'visibility', 'none');
                    break;
                case 'boundary':
                    glMap.setPaintProperty(id, 'line-color', palette.labels);
                    break;
                case 'label':
                    // Лейблы скрываем — требование архитектуры
                    glMap.setLayoutProperty(id, 'visibility', 'none');
                    break;
            }
        } catch (_) { /* layer не поддерживает свойство */ }
    });
}

// Функция для переключения тайлов (day/night). Один и тот же
// style.json — меняем только палитру через setPaintProperty.
// Кастомные слои Leaflet'а (markercluster, image overlays) живут отдельно
// в Leaflet-панелях, MapLibre-стиль их не трогает.
window.switchTileLayer = function(tileKey) {
    if (!STYLE_PALETTES[tileKey] || tileKey === currentTileKey) {
        return;
    }

    const map = window.currentMapInstance;
    if (!map || !currentTileLayer) {
        console.error('[switchTileLayer] Map instance not available');
        return;
    }

    currentTileKey = tileKey;
    const glMap = currentTileLayer.getMaplibreMap();
    if (glMap.isStyleLoaded()) {
        applyPaletteAndHideLabels(glMap, STYLE_PALETTES[tileKey]);
    } else {
        glMap.once('load', () => applyPaletteAndHideLabels(glMap, STYLE_PALETTES[tileKey]));
    }

    // Сохраняем выбор в localStorage
    try {
        localStorage.setItem('preferred_tile_layer', tileKey);
    } catch (e) {
        // Игнорируем ошибки localStorage
    }

    console.log('[switchTileLayer] Switched to:', tileKey);
};

// Инициализация карты и UI компонентов
window.initializeMap = function() {
    // Инициализация карты (Leaflet). Полотно — MapLibre vector tiles
    // через плагин L.maplibreGL (см. ниже).
    //
    // minZoom/maxZoom 11–19 нужны не из-за тайлов (vector-тайлы доступны
    // на любом зуме), а чтобы markerCluster при addLayer не уходил за
    // вершину дерева кластеров (_topClusterLevel.__parent === undefined →
    // TypeError). Эти границы — наследие от raster-провайдеров; оставляем
    // для стабильности markercluster.
    const map = L.map('map', {
        attributionControl: false,
        zoomControl: true,
        preferCanvas: false,
        minZoom: 11,
        maxZoom: 19
    }).setView([window.APP_CONFIG.map_center_lat, window.APP_CONFIG.map_center_lng], window.APP_CONFIG.map_default_zoom);

    // Проверяем сохраненный выбор палитры
    try {
        const savedTile = localStorage.getItem('preferred_tile_layer');
        if (savedTile && STYLE_PALETTES[savedTile]) {
            currentTileKey = savedTile;
        } else if (savedTile) {
            // Старый ключ больше не существует — очищаем localStorage
            console.log('[initializeMap] Clearing outdated tile key from localStorage:', savedTile);
            localStorage.removeItem('preferred_tile_layer');
        }
    } catch (e) {
        // Игнорируем ошибки localStorage
    }

    // MapLibre GL JS как basemap-слой Leaflet'а. Плагин maplibre-gl-leaflet
    // создаёт <div class="leaflet-gl-layer"> внутри tilePane Leaflet'а
    // (z-index 200) и встраивает в него maplibregl.Map; pointer-events:none
    // → все клики получает Leaflet (маркеры, попапы поверх).
    if (typeof L.maplibreGL !== 'function') {
        console.error('[initializeMap] L.maplibreGL plugin not loaded — basemap will be missing');
    } else {
        currentTileLayer = L.maplibreGL({ style: STYLE_URL }).addTo(map);
        const glMap = currentTileLayer.getMaplibreMap();
        const applyPalette = () => applyPaletteAndHideLabels(glMap, STYLE_PALETTES[currentTileKey]);
        if (glMap.isStyleLoaded()) applyPalette();
        else glMap.once('load', applyPalette);
    }

    // Иконка Day/Night — статичная, не меняется при переключении

    // Устанавливаем экземпляр карты в глобальное состояние
    window.setMapInstance(map);

    // Слои карты создаются ОДИН РАЗ и переиспользуются. renderFromCache
    // обновляет их инкрементно (diff), без пересоздания на каждое событие.
    initializeMapLayers(map);

    // Инициализация UI компонентов
    initializeControls(map);
    addQuestionOverlay(map);
    initializeAdSquares(map);

    // Инициализация WebSocket соединения
    window.initializeWebSocket();
};

// Создание постоянных слоёв карты (один раз за сессию)
function initializeMapLayers(map) {
    window.markerClusterGroup = L.markerClusterGroup({
        chunkedLoading: true,
        maxClusterRadius: 50,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
        zoomToBoundsOnClick: true,
        iconCreateFunction: function(cluster) {
            const childCount = cluster.getChildCount();
            return new L.DivIcon({
                html: '<div style="background-color: rgba(255, 87, 51, 0.8); border-radius: 50%; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 14px;">' + childCount + '</div>',
                className: 'marker-cluster-custom',
                iconSize: new L.Point(40, 40)
            });
        }
    });
    window.geometryLayerGroup = L.layerGroup();
    window.randomMarkersGroup = L.layerGroup();

    map.addLayer(window.markerClusterGroup);
    map.addLayer(window.geometryLayerGroup);
    map.addLayer(window.randomMarkersGroup);
}

// Функция для обновления индикатора статуса соединения
window.updateOnlineStatus = function(isOnline) {
    const connectionIndicator = document.getElementById('connection-indicator');
    if (connectionIndicator) {
        connectionIndicator.style.display = isOnline ? 'none' : 'block';
        if (!isOnline) {
            connectionIndicator.textContent = '⚠️ Нет связи с сервером';
        }
    }
};

// Функция для инициализации контролов
function initializeControls(map) {
    const controlsContainer = document.getElementById('controlsContainer');
    const controlsSlider = document.getElementById('controlsSlider');
    const indicators = document.querySelectorAll('#controlsIndicators .dot');

    let startX = 0, currentX = 0, deltaX = 0, isSwiping = false, activePanel = 0;
    const panels = Array.from(controlsSlider.querySelectorAll('.controlPanel'));
    const panelCount = Math.max(1, panels.length);
    const stepPercent = 100 / panelCount;
    let isInitialized = false; // Флаг для отслеживания инициализации

    function setPanel(idx, skipDataLoad = false) {
        activePanel = Math.min(Math.max(idx, 0), panelCount - 1);
        controlsSlider.style.transform = `translateX(-${activePanel * stepPercent}%)`;
        indicators.forEach((el, i) => el.classList.toggle('active', i === activePanel));

        window.hapticFeedback('selection_changed');
    }

    // Touch события
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

        if (Math.abs(deltaX) > 40) { // minSwipe = 40
            if (deltaX < 0 && activePanel < panelCount - 1) setPanel(activePanel + 1);
            else if (deltaX > 0 && activePanel > 0) setPanel(activePanel - 1);
            else setPanel(activePanel);
        } else {
            setPanel(activePanel);
        }

        isSwiping = false;
        deltaX = 0;
    });

    // Индикаторы
    indicators.forEach((el, idx) => {
        el.addEventListener('click', () => setPanel(idx));
    });

    // Инициализация: устанавливаем панель 0 без загрузки данных
    // (данные загрузит bootstrapUI() после инициализации всех компонентов)
    setPanel(0, true);
    isInitialized = true; // Помечаем как инициализированное после первого setPanel

    // Фильтр времени
    const realtimeControls = document.querySelector('#realtimeControls .buttons');
    if (realtimeControls) {
        realtimeControls.addEventListener('click', e => {
            if (e.target.tagName !== 'BUTTON') return;

            window.hapticFeedback('light');
            const newFilter = parseInt(e.target.dataset.minutes, 10);
            // Снимаем активный класс со всех кнопок
            realtimeControls.querySelectorAll('button').forEach(btn => btn.classList.remove('active'));
            // Устанавливаем активный класс на нажатую кнопку
            e.target.classList.add('active');
            // Обновляем фильтр. Перерисовка карты произойдёт реактивно —
            // через подписку event_manager на изменения store.
            window.updateTimeFilter(newFilter);
        });

        // Устанавливаем активную кнопку в соответствии с текущим значением фильтра
        const currentFilter = (window.store && window.store.getState)
            ? window.store.getState().currentTimeFilter
            : (window.DEFAULT_TIME_FILTER || 30);
        realtimeControls.querySelector(`button[data-minutes="${currentFilter}"]`)?.classList.add('active');

        // Если не нашли кнопку для текущего значения, используем значение по умолчанию
        if (!realtimeControls.querySelector('.active')) {
            realtimeControls.querySelector(`button[data-minutes="${window.DEFAULT_TIME_FILTER || 30}"]`)?.classList.add('active');
        }
    }

    // Переключение тайлов карты
    const tileControls = document.querySelector('#mapTileControls .tile-buttons');
    if (tileControls) {
        tileControls.addEventListener('click', e => {
            if (e.target.tagName !== 'BUTTON') return;

            window.hapticFeedback('light');
            const tileKey = e.target.dataset.tile;

            // Снимаем активный класс со всех кнопок
            tileControls.querySelectorAll('button').forEach(btn => btn.classList.remove('active'));
            // Устанавливаем активный класс на нажатую кнопку
            e.target.classList.add('active');

            // Переключаем тайл
            window.switchTileLayer(tileKey);
        });

        // Устанавливаем активную кнопку в соответствии с текущим тайлом
        const activeTileButton = tileControls.querySelector(`button[data-tile="${currentTileKey}"]`);
        if (activeTileButton) {
            tileControls.querySelectorAll('button').forEach(btn => btn.classList.remove('active'));
            activeTileButton.classList.add('active');
        }
    }

    // Кнопки взаимодействия
    initializeInteractionControls();
}

// Функция для инициализации кнопок взаимодействия
function initializeInteractionControls() {
    const legendBtn = document.getElementById('legendBtn');
    const dayNightBtn = document.getElementById('dayNightBtn');
    const closeBtn = document.getElementById('closeCenterPopup');
    const overlay = document.getElementById('centerPopupOverlay');

    legendBtn?.addEventListener('click', () => {
        window.hapticFeedback('light');
        showLegendPopup();
    });

    // Кнопка День/Ночь
    dayNightBtn?.addEventListener('click', () => {
        window.hapticFeedback('light');
        toggleDayNightMode();
    });

    closeBtn?.addEventListener('click', () => {
        window.hapticFeedback('light');
        hideCenterPopup();
    });

    overlay?.addEventListener('click', () => {
        window.hapticFeedback('light');
        hideCenterPopup();
    });
}

// Функция переключения режима День/Ночь
function toggleDayNightMode() {
    const dayNightIcon = document.getElementById('dayNightIcon');
    const isDarkMode = currentTileKey === 'dark';

    // Переключаем между 'osm' (день) и 'dark' (ночь)
    const newTileKey = isDarkMode ? 'osm' : 'dark';

    window.switchTileLayer(newTileKey);

    // Иконка daynight.svg статична — src и filter не меняются

    console.log('[DayNight] Switched to:', newTileKey);
}

// Функция для добавления оверлея вопроса
function addQuestionOverlay(map) {
    const questionBounds = L.latLngBounds(
        [46.45304, 30.76985],
        [46.54304, 30.89285]
    );

    // Добавляем версионирование для обхода кеша
    const overlayUrl = `/assets/images/overlay.svg?v=${Date.now()}`;

    const questionOverlay = L.imageOverlay(overlayUrl, questionBounds, {
        interactive: true,
        opacity: 1.0,
        zIndex: 1000
    }).addTo(map);

    questionOverlay.on('click', () => {
        const popup = window.createTelegramPopup(
            "Здесь отображаются события не имеющие привязки к местности, либо могут быть не точными!"
        );
        popup.setLatLng([46.49804, 30.83135]).openOn(map);
    });
}

// Функция для инициализации рекламных квадратов - использует статичный banner.svg
function initializeAdSquares(map) {
    console.log('[initializeAdSquares] Starting banner initialization...');

    const bounds = L.latLngBounds([46.4370, 30.92288], [46.5240, 31.06208]);
    const imageUrl = '/assets/images/banner.svg';
    const fullUrl = imageUrl + '?v=' + Date.now();
    console.log('[initializeAdSquares] Banner URL:', fullUrl);
    console.log('[initializeAdSquares] Current host:', window.location.host);

    const popupContent = `<h3>Исходный код приложения доступен на <a href="https://github.com/iseeupigs/iseeupigs-web" target="_blank">GitHub</h3><br>поблагодарить разработчика можно на <a href="https://bastyon.com/keep_alive_odessa?ref=PHQHKADhBPxxSwjiggV6G2BxSvy6TY1Lgb" target="_blank">bastyon</a>`;

    if (!window.adSquares.ad1) {
        console.log('[initializeAdSquares] Creating image overlay...');

        // Test image loading
        const testImg = new Image();
        testImg.onload = function() {
            console.log('[initializeAdSquares] Banner image preloaded successfully:', this.width, 'x', this.height);
        };
        testImg.onerror = function() {
            console.error('[initializeAdSquares] Failed to preload banner image!');
        };
        testImg.src = fullUrl;

        const overlay = L.imageOverlay(fullUrl, bounds, {
            opacity: 1,
            interactive: true,
            pane: 'overlayPane',
            className: 'ad-overlay'
        }).addTo(map);

        console.log('[initializeAdSquares] Overlay added to map');

        overlay.bindPopup(popupContent, window.DEFAULT_POPUP_OPTIONS);
        window.adSquares.ad1 = overlay;

        // Check if overlay is actually visible
        overlay.on('load', function() {
            console.log('[initializeAdSquares] Banner image loaded on map');
        });
        overlay.on('error', function() {
            console.error('[initializeAdSquares] Banner image failed to load on map');
        });
    } else {
        console.log('[initializeAdSquares] Banner already exists, skipping');
    }
}


// =============================================================================
// Инкрементный рендер карты
//
// renderedById хранит, какие Leaflet-слои созданы для каждого id события и в
// какую группу они добавлены. На каждый вызов renderFromCache() выполняется
// diff отфильтрованного набора против отрисованного:
//   - новые id            → создать слои и добавить в группы;
//   - исчезнувшие id       → удалить слои из групп (истёк TTL / фильтр / слой);
//   - изменившиеся feature → удалить старые слои и создать заново;
//   - неизменные           → не трогать.
// Добавление одного события стоит O(1) вместо полного пересоздания карты.
// =============================================================================

const renderedById = new Map();

// Извлечение стабильного id из feature.
function featureId(feature) {
    const p = feature && feature.properties;
    if (!p) return null;
    if (p.id != null) return p.id;
    if (p.event_id != null) return p.event_id;
    if (p._id != null) return p._id;
    if (p.uid != null) return p.uid;
    return null;
}

// Удаление всех слоёв, отрисованных для данного id.
function removeRenderedEvent(id) {
    const record = renderedById.get(id);
    if (!record) return;
    for (const item of record.items) {
        try {
            item.group.removeLayer(item.layer);
        } catch (e) {
            // Слой мог быть уже удалён — игнорируем
        }
    }
    renderedById.delete(id);
}

// Создание и добавление слоёв для одного feature.
function addRenderedEvent(id, feature, map) {
    if (!feature.geometry) return;

    let elements;
    switch (feature.geometry.type) {
        case 'Point':
            elements = window.createCircle(map, feature.geometry.coordinates, feature.properties, feature.properties.strategy);
            break;
        case 'LineString':
            elements = window.createPolyline(map, feature.geometry.coordinates, feature.properties);
            break;
        case 'Polygon':
            elements = window.createPolygon(map, feature.geometry.coordinates, feature.properties);
            break;
        default:
            console.warn('[renderFromCache] Unsupported geometry type:', feature.geometry.type);
            return;
    }

    const items = [];
    for (const element of elements) {
        if (!element) continue;

        let group;
        if (element instanceof L.Marker) {
            // Случайные точки — отдельная некластеризуемая группа
            group = (feature.properties.strategy === 'random')
                ? window.randomMarkersGroup
                : window.markerClusterGroup;
        } else {
            // Геометрия (круги, линии, полигоны)
            group = window.geometryLayerGroup;
        }

        group.addLayer(element);
        items.push({ layer: element, group: group });
    }

    renderedById.set(id, { featureRef: feature, items: items });
}

// Инкрементная синхронизация карты с отфильтрованным набором событий из store.
window.renderFromCache = function() {
    const map = window.currentMapInstance;
    if (!map) {
        console.error('[renderFromCache] Map instance not available');
        return;
    }
    if (!window.markerClusterGroup || !window.geometryLayerGroup || !window.randomMarkersGroup) {
        console.error('[renderFromCache] Map layers not initialized');
        return;
    }

    const geoJsonData = window.getFilteredDataForRendering();
    const features = (geoJsonData && geoJsonData.features) ? geoJsonData.features : [];

    const nextIds = new Set();
    let added = 0;
    let updated = 0;

    for (let i = 0; i < features.length; i++) {
        const feature = features[i];
        const id = featureId(feature);
        if (id == null) continue;

        nextIds.add(id);

        const existing = renderedById.get(id);
        if (existing && existing.featureRef === feature) {
            continue; // не изменилось — пропускаем
        }
        if (existing) {
            removeRenderedEvent(id); // изменилось — пересоздаём
            updated++;
        } else {
            added++;
        }
        addRenderedEvent(id, feature, map);
    }

    // Удаляем слои событий, выпавших из отфильтрованного набора
    let removed = 0;
    for (const id of Array.from(renderedById.keys())) {
        if (!nextIds.has(id)) {
            removeRenderedEvent(id);
            removed++;
        }
    }

    if (added || removed || updated) {
        console.log('[renderFromCache] diff:', { added, updated, removed, total: nextIds.size });
    }
};

// Функция инициализации UI
window.bootstrapUI = function() {
    window.initializeMap();

    // Первичный рендер из того, что уже есть в store (гидратация из
    // localStorage для офлайн-отображения). Все последующие изменения
    // отрисовываются реактивно через подписку event_manager на store —
    // никаких таймеров-костылей.
    requestAnimationFrame(() => {
        window.renderFromCache();
    });
};
