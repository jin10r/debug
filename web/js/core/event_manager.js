// js/core/event_manager.js - Centralized event management for display and notifications with reactive store

window.eventManager = {
    /**
     * Add new events to the map and show notifications
     * @param {Array} events - Array of GeoJSON features
     */
    addNewEvents: function(events) {
        if (!events || !events.length) return;

        // Обновляем отображение и показываем уведомления
        requestAnimationFrame(() => {
            this.render();
            this.notify(events);
        });
    },

    /**
     * Update all events (full reload without notifications)
     * @param {Object} eventsData - GeoJSON FeatureCollection
     */
    updateAllEvents: function(eventsData) {
        // Обновляем события в сторе
        window.updateEventsInStore(eventsData);

        // Обновляем отображение с новыми данными
        requestAnimationFrame(() => {
            this.render();
        });
    },

    /**
     * Render events on the map
     */
    render: function() {
        if (typeof window.renderDataOnMap === 'function') {
            // Используем requestAnimationFrame для более плавного рендера
            requestAnimationFrame(() => {
                window.renderDataOnMap();
            });
        } else {
            console.error('renderDataOnMap function not found');
        }
    },

    /**
     * Show notifications for new events
     * @param {Array} events - Array of GeoJSON features
     */
    notify: function(events) {
        if (!events.length || typeof window.handleNewEvents !== 'function' || !window.eventTracker) return;

        const eventObjects = events.map(event => ({
            id: event.properties.id,
            layer: event.properties.layer || event.properties.type || 'unknown',
            description: event.properties.description || event.properties.name || 'Новое событие',
            ...event.properties
        }));

        const newEvents = window.eventTracker.checkForNewEvents(eventObjects);
        if (newEvents.length > 0) {
            window.handleNewEvents(newEvents);
        }
    },
    
    // Отслеживаем последний фильтр времени и количество событий для оптимизации
    _lastTimeFilter: 30,
    _lastEventsCount: 0
};

// Подписываемся на изменения стора для реактивного рендеринга
window.store.subscribe((state) => {
    const currentTimeFilter = state.currentTimeFilter;
    const eventsCount = state.events.features.length;
    const eventsUpdatedAt = state.eventsUpdatedAt || 0;

    // Рендерим если изменился фильтр времени ИЛИ количество событий ИЛИ было обновление событий
    if (currentTimeFilter !== window.eventManager._lastTimeFilter ||
        eventsCount !== window.eventManager._lastEventsCount ||
        eventsUpdatedAt !== window.eventManager._lastEventsUpdatedAt) {
        
        window.eventManager._lastTimeFilter = currentTimeFilter;
        window.eventManager._lastEventsCount = eventsCount;
        window.eventManager._lastEventsUpdatedAt = eventsUpdatedAt;
        
        requestAnimationFrame(() => {
            window.eventManager.render();
        });
    }
});

// Запуск циклического автообновления каждые 5 секунд
window.autoRefreshInterval = setInterval(() => {
    // Используем requestAnimationFrame для более плавного обновления UI
    requestAnimationFrame(() => {
        window.eventManager.render();
    });
}, 5000); // Обновление каждые 5 секунд

