// data.js — интеграция с реактивным стором

// Экспорт константы
window.DEFAULT_TIME_FILTER = 30;

// Функция обновления фильтра времени
window.updateTimeFilter = function(minutes) {
    window.store.dispatch({
        type: 'UPDATE_CURRENT_TIME_FILTER',
        payload: { minutes: minutes }
    });
    window.eventManager.render();
};

// Функция установки экземпляра карты
window.setMapInstance = function(map) {
    window.currentMapInstance = map;
};

// Функция для получения ID события
function getEventId(feature) {
    const props = feature.properties || {};
    return props.id || props.event_id || props._id || props.uid || null;
}

// Функция для получения типа события
function getEventType(feature) {
    const props = feature.properties || {};
    return props.layer || props.type || 'unknown';
}

// Функция для получения описания события
function getEventDescription(feature) {
    const props = feature.properties || {};
    return props.description || props.title || props.name || 'Новое событие';
}

// Функция фильтрации и подготовки данных для отображения
window.getFilteredDataForRendering = function() {
    const filtered = window.store.getFilteredItems();
    // Debug logging disabled to reduce console noise
    // console.log('[getFilteredDataForRendering] Filtered data:', {
    //     total_in_store: window.store.getState().events.features.length,
    //     filtered_count: filtered.features.length,
    //     filter_applied: window.store.getState().currentTimeFilter + ' minutes'
    // });
    return filtered;
};

// Функция рендеринга данных на карте
window.renderDataOnMap = function() {
    if (typeof window.renderFromCache === 'function') {
        window.renderFromCache();
    }
};

// Функция для обновления событий в сторе
window.updateEventsInStore = function(eventsData) {
    console.log('[data.js] updateEventsInStore called:', eventsData.features.length, 'events');
    window.store.dispatch({
        type: 'SET_EVENTS',
        payload: { events: eventsData }
    });
};

// Функция для добавления новых событий в стор (с дедупликацией)
window.addEventsToStore = function(events) {
    console.log('[data.js] addEventsToStore called:', events.length, 'events');
    if (!events || !events.length) {
        console.warn('[data.js] addEventsToStore: no events provided');
        return;
    }

    const currentState = window.store.getState();
    const existingIds = new Set(
        currentState.events.features
            .map(f => getEventId(f))
            .filter(id => id !== null)
    );

    // Фильтруем дубликаты
    const newEvents = events.filter(event => {
        const eventId = getEventId(event);
        return !eventId || !existingIds.has(eventId);
    });

    console.log('[data.js] addEventsToStore: filtered to', newEvents.length, 'new events');

    if (newEvents.length > 0) {
        window.store.dispatch({
            type: 'ADD_EVENTS',
            payload: { events: newEvents }
        });
    } else {
        console.log('[data.js] addEventsToStore: no new events to add');
    }
};

// Функция для переключения слоя
window.toggleLayerInStore = function(layer) {
    window.store.dispatch({
        type: 'TOGGLE_LAYER',
        payload: { layer: layer }
    });
};

