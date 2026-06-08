// ui.js — UI инициализация, управление картой, попапы, оверлеи
// Архитектурно оптимизированная версия для Telegram Mini Apps

// Инициализация глобальных переменных
window.adSquares = {};

// Скрывает все текстовые слои (type: symbol) в уже загруженном MapLibre GL стиле.
function hideMaplibreLabels(glMap) {
    glMap.getStyle().layers
        .filter(function(l) { return l.type === 'symbol'; })
        .forEach(function(l) { glMap.setLayoutProperty(l.id, 'visibility', 'none'); });
}

function _applyDarkTheme(glMap) {
    glMap.getStyle().layers
        .filter(l => l.type === 'symbol')
        .forEach(l => glMap.setLayoutProperty(l.id, 'visibility', 'none'));

    glMap.getStyle().layers.forEach(layer => {
        const id = (layer.id || '').toLowerCase();
        const sl = (layer['source-layer'] || '').toLowerCase();
        try {
            if (layer.type === 'background') {
                glMap.setPaintProperty(layer.id, 'background-color', '#0d1b2e');
            } else if (layer.type === 'fill') {
                if (id.includes('water') || sl === 'water' || sl.includes('water')) {
                    glMap.setPaintProperty(layer.id, 'fill-color', '#4db8d4');
                    glMap.setPaintProperty(layer.id, 'fill-opacity', 0.6);
                } else if (id.includes('building') || sl === 'building') {
                    glMap.setPaintProperty(layer.id, 'fill-color', '#c8ccd0');
                    glMap.setPaintProperty(layer.id, 'fill-opacity', 0.25);
                } else {
                    glMap.setPaintProperty(layer.id, 'fill-color', '#0d1b2e');
                }
            } else if (layer.type === 'line') {
                if (id.includes('water') || sl === 'water') {
                    glMap.setPaintProperty(layer.id, 'line-color', '#4db8d4');
                } else if (id.includes('motorway') || id.includes('trunk') || id.includes('primary')) {
                    glMap.setPaintProperty(layer.id, 'line-color', '#3a7bd5');
                } else if (id.includes('secondary') || id.includes('tertiary')) {
                    glMap.setPaintProperty(layer.id, 'line-color', '#5b9bd5');
                } else if (sl === 'transportation' || id.includes('road') || id.includes('street')) {
                    glMap.setPaintProperty(layer.id, 'line-color', '#2d5a8e');
                }
            }
        } catch (_) {}
    });
}

// Доступные тайлы карт
const TILE_PROVIDERS = {
    'vector-light': {
        type: 'maplibre',
        style: 'https://tiles.openfreemap.org/styles/liberty'
    },
    'osm': {
        url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        options: {
            subdomains: 'abc',
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            crossOrigin: true
        }
    },
    'dark': {
        type: 'maplibre',
        style: 'https://tiles.openfreemap.org/styles/liberty',
        theme: 'dark'
    }
};

// Текущий активный тайл
let currentTileLayer = null;
let currentTileKey = 'vector-light';

// Функция для переключения тайлов
window.switchTileLayer = function(tileKey) {
    if (!TILE_PROVIDERS[tileKey] || tileKey === currentTileKey) {
        return;
    }

    const map = window.currentMapInstance;
    if (!map) {
        console.error('[switchTileLayer] Map instance not available');
        return;
    }

    // Удаляем текущий слой
    if (currentTileLayer) {
        map.removeLayer(currentTileLayer);
    }

    // Создаем и добавляем новый слой
    const provider = TILE_PROVIDERS[tileKey];
    let newLayer;
    if (provider.type === 'maplibre') {
        newLayer = L.maplibreGL({ style: provider.style });
        newLayer.addTo(map);
        const glMap = newLayer.getMaplibreMap();
        const applyTheme = provider.theme === 'dark'
            ? () => _applyDarkTheme(glMap)
            : () => hideMaplibreLabels(glMap);
        if (glMap.isStyleLoaded()) { applyTheme(); }
        else { glMap.once('load', applyTheme); }
    } else {
        newLayer = L.tileLayer(provider.url, { minZoom: 11, maxZoom: 19, ...provider.options });
        newLayer.addTo(map);
        newLayer.bringToBack();
    }

    currentTileLayer = newLayer;
    currentTileKey = tileKey;

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
    // Инициализация карты (Leaflet)
    // minZoom/maxZoom совпадают с диапазоном тайлов (см. TILE_PROVIDERS, 11–19).
    // Это гарантирует, что зум карты никогда не опустится ниже minZoom тайлов:
    // иначе markerCluster при addLayer уходит за вершину дерева кластеров
    // (_topClusterLevel.__parent === undefined) и падает с TypeError.
    const map = L.map('map', {
        attributionControl: false,
        zoomControl: true,
        preferCanvas: false,
        minZoom: 11,
        maxZoom: 19
    }).setView([window.APP_CONFIG.map_center_lat, window.APP_CONFIG.map_center_lng], window.APP_CONFIG.map_default_zoom);

    // Проверяем сохраненный выбор тайла
    try {
        const savedTile = localStorage.getItem('preferred_tile_layer');
        // Проверяем, что сохраненный ключ существует в TILE_PROVIDERS
        if (savedTile && TILE_PROVIDERS[savedTile]) {
            currentTileKey = savedTile;
        } else if (savedTile) {
            // Старый ключ больше не существует — очищаем localStorage
            console.log('[initializeMap] Clearing outdated tile key from localStorage:', savedTile);
            localStorage.removeItem('preferred_tile_layer');
        }
    } catch (e) {
        // Игнорируем ошибки localStorage
    }

    // Добавляем выбранный тайл
    const provider = TILE_PROVIDERS[currentTileKey];
    if (provider.type === 'maplibre') {
        currentTileLayer = L.maplibreGL({ style: provider.style });
        currentTileLayer.addTo(map);
        const glMap = currentTileLayer.getMaplibreMap();
        const applyTheme = provider.theme === 'dark'
            ? () => _applyDarkTheme(glMap)
            : () => hideMaplibreLabels(glMap);
        if (glMap.isStyleLoaded()) { applyTheme(); }
        else { glMap.once('load', applyTheme); }
    } else {
        currentTileLayer = L.tileLayer(provider.url, { minZoom: 11, maxZoom: 19, ...provider.options });
        currentTileLayer.addTo(map);
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

    // Фильтр слоёв
    const layerControls = document.querySelector('#layerControls .layers');
    if (layerControls) {
        const activeLayers = window.store?.getState().activeLayers;
        if (activeLayers) {
            layerControls.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                cb.checked = activeLayers.has(cb.dataset.layer);
            });
        }

        layerControls.addEventListener('change', e => {
            if (e.target.tagName !== 'INPUT' || e.target.type !== 'checkbox') return;
            window.hapticFeedback('selection_changed');
            window.toggleLayerInStore(e.target.dataset.layer);
        });
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

    // Переключаем между 'vector-light' (день) и 'dark' (ночь)
    const newTileKey = isDarkMode ? 'vector-light' : 'dark';

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
    const overlayUrl = `/assets/images/question.svg?v=${Date.now()}`;

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

    const popupContent = `<h3>Исходный код приложения доступен на <a href="https://github.com/develop4alive/survival_map" target="_blank">GitHub</h3><br>поблагодарить разработчика можно на <a href="https://bastyon.com/keep_alive_odessa?ref=PHQHKADhBPxxSwjiggV6G2BxSvy6TY1Lgb" target="_blank">bastyon</a>`;

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
