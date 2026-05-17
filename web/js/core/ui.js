// ui.js — UI инициализация, управление картой, попапы, оверлеи
// Архитектурно оптимизированная версия для Telegram Mini Apps

// Инициализация глобальных переменных
window.adSquares = {};

// Доступные тайлы карт (OpenStreetMap + CartoDB Dark Matter - без API ключей)
const TILE_PROVIDERS = {
    'osm': {
        url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        options: {
            subdomains: 'abc',
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            crossOrigin: true
        }
    },
    'dark': {
        url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        options: {
            subdomains: 'abcd',
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>',
            crossOrigin: true
        }
    }
};

// Текущий активный тайл
let currentTileLayer = null;
let currentTileKey = 'osm';

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
    const newLayer = L.tileLayer(provider.url, {
        minZoom: 11,
        maxZoom: 19,
        ...provider.options
    });

    newLayer.addTo(map);
    newLayer.bringToBack(); // Перемещаем тайлы на задний план

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
    const map = L.map('map', {
        attributionControl: false,
        zoomControl: true,
        preferCanvas: false
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
    currentTileLayer = L.tileLayer(provider.url, {
        minZoom: 11,
        maxZoom: 19,
        ...provider.options
    });
    currentTileLayer.addTo(map);

    // Иконка Day/Night — статичная, не меняется при переключении

    // Устанавливаем экземпляр карты в глобальное состояние
    window.setMapInstance(map);

    // Инициализация UI компонентов
    initializeControls(map);
    addQuestionOverlay(map);
    initializeAdSquares(map);

    // Инициализация WebSocket соединения
    window.initializeWebSocket();
};

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
            // Обновляем фильтр
            window.updateTimeFilter(newFilter);

            // Локальная фильтрация и рендер (автономный процесс)
            // Используем requestAnimationFrame для более плавного обновления UI
            requestAnimationFrame(() => {
                window.eventManager.render();
            });
        });

        // Устанавливаем активную кнопку в соответствии с текущим значением фильтра
        realtimeControls.querySelector(`button[data-minutes="${window.store?.getState?.()?.currentTimeFilter || 30}"]`)?.classList.add('active');

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


// Функция рендеринга данных на карту
window.renderFromCache = function() {
    // Получаем экземпляр карты из глобального состояния
    const map = window.currentMapInstance;

    if (!map) {
        console.error('[renderFromCache] Map instance not available');
        return;
    }

    // Получаем отфильтрованные данные для отображения из data.js
    const geoJsonData = window.getFilteredDataForRendering();

    // Debug logging disabled to reduce console noise
    // console.log('[renderFromCache] Rendering:', {
    //     total_features: geoJsonData.features.length,
    //     currentTimeFilter: window.store.getState().currentTimeFilter,
    //     activeLayers: Array.from(window.store.getState().activeLayers)
    // });

    // Очищаем существующие маркеры и геометрию
    if (window.markerClusterGroup) {
        map.removeLayer(window.markerClusterGroup);
    }
    if (window.geometryLayerGroup) {
        map.removeLayer(window.geometryLayerGroup);
    }
    if (window.randomMarkersGroup) {
        map.removeLayer(window.randomMarkersGroup);
    }

    // Создаем новый кластер маркеров
    const markers = L.markerClusterGroup({
        chunkedLoading: true,
        maxClusterRadius: 50,
        spiderfyOnMaxZoom: true,
        showCoverageOnHover: false,
        zoomToBoundsOnClick: true,
        // Важно: отключаем стандартные иконки кластеров
        iconCreateFunction: function(cluster) {
            const childCount = cluster.getChildCount();
            return new L.DivIcon({
                html: '<div style="background-color: rgba(255, 87, 51, 0.8); border-radius: 50%; width: 40px; height: 40px; display: flex; align-items: center; justify-content: center; color: white; font-weight: bold; font-size: 14px;">' + childCount + '</div>',
                className: 'marker-cluster-custom',
                iconSize: new L.Point(40, 40)
            });
        }
    });

    // Группа слоев для геометрии (линии, полигоны)
    const geometryLayers = L.layerGroup();

    // Группа слоев для случайных маркеров (не кластеризуются)
    const randomMarkers = L.layerGroup();

    // Оптимизируем рендеринг для большого количества элементов
    const features = geoJsonData.features;
    const len = features.length;
    
    // Используем временное хранилище для ускорения рендеринга
    const tempMarkersToAdd = [];
    const tempGeometryToAdd = [];
    const tempRandomMarkersToAdd = [];

    for (let i = 0; i < len; i++) {
        const feature = features[i];
        if (feature.geometry) {
            let geometryElements;

            switch (feature.geometry.type) {
                case 'Point':
                    // Для точек создаем круг и маркер
                    geometryElements = window.createCircle(map, feature.geometry.coordinates, feature.properties, feature.properties.strategy);
                    break;

                case 'LineString':
                    // Для линий создаем полилинию и маркер в центре
                    geometryElements = window.createPolyline(map, feature.geometry.coordinates, feature.properties);
                    break;

                case 'Polygon':
                    // Для полигонов создаем полигон и маркер в центре
                    geometryElements = window.createPolygon(map, feature.geometry.coordinates, feature.properties);
                    break;

                default:
                    console.warn('Unsupported geometry type:', feature.geometry.type);
                    continue; // Пропускаем неподдерживаемые типы геометрии
            }

            // Разделяем геометрию и маркеры
            const elementsLen = geometryElements.length;
            for (let j = 0; j < elementsLen; j++) {
                const element = geometryElements[j];
                if (element) {
                    if (element instanceof L.Marker) {
                        // Для случайных точек добавляем маркеры в отдельную группу
                        if (feature.properties.strategy === 'random') {
                            tempRandomMarkersToAdd.push(element);
                        } else {
                            // Обычные маркеры добавляем в кластер
                            tempMarkersToAdd.push(element);
                        }
                    } else {
                        // Геометрию (линии, полигоны, круги) добавляем в группу геометрии
                        tempGeometryToAdd.push(element);
                    }
                }
            }
        }
    }

    // Добавляем все элементы за один раз для лучшей производительности
    // Используем addLayer в цикле, так как layerGroup не имеет метода addLayers
    if (tempMarkersToAdd.length > 0) {
        // Проверяем наличие метода addLayers у marker cluster группы
        if (markers.addLayers) {
            markers.addLayers(tempMarkersToAdd);
        } else {
            for (const marker of tempMarkersToAdd) {
                markers.addLayer(marker);
            }
        }
    }
    if (tempGeometryToAdd.length > 0) {
        for (const layer of tempGeometryToAdd) {
            geometryLayers.addLayer(layer);
        }
    }
    if (tempRandomMarkersToAdd.length > 0) {
        for (const marker of tempRandomMarkersToAdd) {
            randomMarkers.addLayer(marker);
        }
    }

    map.addLayer(markers);
    map.addLayer(geometryLayers);
    map.addLayer(randomMarkers);

    // Сохраняем ссылки на слои в глобальное состояние
    window.markerClusterGroup = markers;
    window.geometryLayerGroup = geometryLayers;
    window.randomMarkersGroup = randomMarkers;
};

// Функция инициализации UI
window.bootstrapUI = function() {
    window.initializeMap();

    setTimeout(() => {
        requestAnimationFrame(() => {
            window.eventManager.render();
        });
    }, 1000);
};

