// core/local_cache.js - Локальное хранилище событий с TTL 60 минут и интеграцией Telegram Cloud Storage

class LocalCache {
    constructor() {
        // Максимальное время жизни события - 60 минут
        this.TTL_MS = 60 * 60 * 1000;
        
        // Мастер-объект GeoJSON для хранения всех событий
        this.masterGeoJSON = { 
            type: 'FeatureCollection', 
            features: [] 
        };
        
        // Карта для быстрого поиска событий по ID
        this.eventsById = new Map();
        
        // Таймер для периодической очистки устаревших событий
        this.cleanupTimer = null;
        
        // Запускаем таймер очистки каждые 5 минут
        this.startCleanupTimer();
        
        // Загружаем данные из кэша при инициализации (cache-first архитектура)
        this.loadFromCache();
    }
    
    /**
     * Загрузка данных из кэша (Telegram Cloud Storage → localStorage → fallback)
     */
    async loadFromCache() {
        try {
            const cachedData = await window.cacheUtility.loadFromCache('events_geojson');
            if (cachedData && cachedData.type === 'FeatureCollection' && Array.isArray(cachedData.features)) {
                this.masterGeoJSON = cachedData;
                
                // Обновляем карту событий по ID
                this.eventsById.clear();
                for (const feature of this.masterGeoJSON.features) {
                    const eventId = this.getEventId(feature);
                    if (eventId) {
                        this.eventsById.set(eventId, feature);
                    }
                }
                
                
                // Clean up expired events after loading from cache
                this.cleanupExpiredEvents();

                // Update the store with loaded events to keep it in sync
                if (this.masterGeoJSON.features.length > 0 && typeof window.updateEventsInStore === 'function') {
                    window.updateEventsInStore(this.masterGeoJSON);
                }
                
                // Вызываем рендеринг после загрузки из кэша
                if (typeof window.eventManager !== 'undefined' && typeof window.eventManager.render === 'function') {
                    window.eventManager.render();
                }
            } else {
                // Initialize with empty collection if no valid cache
                this.masterGeoJSON = {
                    type: 'FeatureCollection',
                    features: []
                };
                this.eventsById.clear();
            }
        } catch (error) {
            console.error('Error loading from cache:', error);
            // Initialize with empty collection if there's an error
            this.masterGeoJSON = {
                type: 'FeatureCollection',
                features: []
            };
            this.eventsById.clear();
        }
    }
    
    /**
     * Сохранение данных в кэш (Telegram Cloud Storage + localStorage)
     */
    async saveToCache() {
        try {
            // Удаляем устаревшие события перед сохранением
            this.cleanupExpiredEvents();
            
            await window.cacheUtility.saveToCache('events_geojson', this.masterGeoJSON);
        } catch (error) {
            console.error('Error saving to cache:', error);
            // Don't throw error, just log it - app should continue working
        }
    }

    /**
     * Добавить новое событие в кеш
     * @param {Object} event - GeoJSON Feature события
     */
    addEvent(event) {
        const eventId = this.getEventId(event);
        if (!eventId) {
            console.warn('Event without ID cannot be added to cache:', event);
            return false;
        }

        // Проверяем, не является ли событие устаревшим
        const eventTime = this.getEventTime(event);
        if (eventTime) {
            const now = Date.now();
            const eventAge = now - eventTime.getTime();

            if (eventAge > this.TTL_MS) {
                console.log('Event rejected due to age:', eventAge, 'ms');
                return false;
            }
        }

        // Проверяем, является ли это новым событием (еще не существует в кеше)
        const isNewEvent = !this.eventsById.has(eventId);

        // Если событие уже существует, обновляем его
        if (this.eventsById.has(eventId)) {
            const existingIndex = this.masterGeoJSON.features.findIndex(f => this.getEventId(f) === eventId);
            if (existingIndex !== -1) {
                this.masterGeoJSON.features[existingIndex] = event;
            }
        } else {
            // Добавляем новое событие
            this.masterGeoJSON.features.push(event);
            this.eventsById.set(eventId, event);
        }

        // Синхронизируем с reactive store для реактивного рендеринга
        // Вызываем всегда, чтобы обновлять существующие события тоже
        if (typeof window.updateEventsInStore === 'function') {
            window.updateEventsInStore(this.masterGeoJSON);
        }

        // Принудительно вызываем рендер для немедленного отображения
        if (typeof window.eventManager !== 'undefined' && typeof window.eventManager.render === 'function') {
            window.eventManager.render();
        }

        // Уведомляем о новом событии
        if (isNewEvent && typeof window.eventManager !== 'undefined' && typeof window.eventManager.notify === 'function') {
            // Если initial data еще не снимал флаг first load (часто в TG WebView),
            // то первое push-событие не должно быть подавлено
            if (window.eventTracker && window.eventTracker.isFirstLoad) {
                window.eventTracker.isFirstLoad = false;
            }
            setTimeout(() => {
                window.eventManager.notify([event]);
            }, 0);
        }

        // Сохраняем в кэш асинхронно
        setTimeout(() => this.saveToCache(), 0);

        return isNewEvent;
    }

    /**
     * Добавить несколько событий в кеш
     * @param {Array} events - Массив GeoJSON Feature событий
     * @param {boolean} suppressNotifications - Флаг для подавления уведомлений (например, при начальной загрузке)
     */
    addEvents(events, suppressNotifications = false) {
        let addedCount = 0;
        const newEvents = []; // Список новых событий для уведомлений

        events.forEach(event => {
            const eventId = this.getEventId(event);
            // Проверяем, является ли это новым событием
            const isNewEvent = eventId && !this.eventsById.has(eventId);

            if (this.addEvent(event)) { // addEvent сам синхронизируется со store
                addedCount++;
                // Если это новое событие, добавляем в список для уведомлений
                if (isNewEvent) {
                    newEvents.push(event);
                }
            }
        });

        // Если есть новые события и уведомления не подавлены, вызываем уведомления для них
        if (newEvents.length > 0 && !suppressNotifications && typeof window.eventManager !== 'undefined' && typeof window.eventManager.notify === 'function') {
            setTimeout(() => {
                window.eventManager.notify(newEvents);
            }, 0);
        }

        // Сохраняем в кэш после добавления
        setTimeout(() => {
            this.saveToCache();
        }, 0);

        return addedCount;
    }

    /**
     * Заменить все события в кеше (например, при восстановлении соединения)
     * @param {Array} events - Массив GeoJSON Feature событий
     * @param {boolean} suppressNotifications - Флаг для подавления уведомлений (например, при начальной загрузке)
     */
    replaceAllEvents(events, suppressNotifications = false) {
        // Сохраняем старые ID для определения новых событий
        const oldEventIds = new Set(this.eventsById.keys());
        
        // Очищаем текущие данные
        this.clear();

        // Добавляем новые события
        const newEvents = [];
        events.forEach(event => {
            const eventId = this.getEventId(event);
            if (eventId) {
                this.masterGeoJSON.features.push(event);
                this.eventsById.set(eventId, event);
                
                // Если это новое событие (не было в старом списке), добавляем в список новых
                if (!oldEventIds.has(eventId)) {
                    newEvents.push(event);
                }
            }
        });

        // Удаляем устаревшие события
        this.cleanupExpiredEvents();

        // Sync with store to ensure both have the same data
        // updateEventsInStore вызовет notifySubscribers, который триггерит реактивный рендер
        window.updateEventsInStore(this.masterGeoJSON);

        // Если есть новые события и уведомления не подавлены, вызываем уведомления для них
        if (newEvents.length > 0 && !suppressNotifications && typeof window.eventManager !== 'undefined' && typeof window.eventManager.notify === 'function') {
            setTimeout(() => {
                window.eventManager.notify(newEvents);
            }, 0);
        }

        // Сохраняем в кэш после замены
        setTimeout(() => {
            this.saveToCache();
        }, 0);

        return this.masterGeoJSON.features.length;
    }

    /**
     * Получить все события из кеша
     * @returns {Object} - GeoJSON FeatureCollection
     */
    getAllEvents() {
        return this.masterGeoJSON;
    }

    /**
     * Получить события, отфильтрованные по времени
     * @param {number} timeFilterMinutes - Фильтр времени в минутах
     * @returns {Object} - GeoJSON FeatureCollection
     */
    getEventsByTimeFilter(timeFilterMinutes) {
        const filterMs = timeFilterMinutes * 60 * 1000;
        const now = Date.now();

        const filteredFeatures = this.masterGeoJSON.features.filter(feature => {
            const eventTime = this.getEventTime(feature);
            if (!eventTime) return true; // Если нет времени, включаем событие

            const ageMs = now - eventTime.getTime();
            // Only include past events within the time filter
            return ageMs >= 0 && ageMs <= filterMs;
        });

        return {
            type: 'FeatureCollection',
            features: filteredFeatures
        };
    }

    /**
     * Получить ID события
     * @param {Object} event - GeoJSON Feature
     * @returns {number|string|null} - ID события или null
     */
    getEventId(event) {
        if (!event || !event.properties) return null;
        return event.properties.id || event.properties.event_id || event.properties._id || event.properties.uid || null;
    }

    /**
     * Получить время события
     * @param {Object} event - GeoJSON Feature
     * @returns {Date|null} - Время события или null
     */
    getEventTime(event) {
        if (!event || !event.properties) return null;
        
        // Пробуем разные возможные поля для времени
        const timeStr = event.properties.time || event.properties.created_at || event.properties.timestamp;
        if (!timeStr) return null;
        
        try {
            return new Date(timeStr);
        } catch (e) {
            console.error('Error parsing event time:', timeStr, e);
            return null;
        }
    }

    /**
     * Получить максимальный ID среди всех событий
     * @returns {number} - Максимальный ID или 0
     */
    getMaxEventId() {
        let maxId = 0;
        for (const feature of this.masterGeoJSON.features) {
            const eventId = this.getEventId(feature);
            if (eventId && eventId > maxId) {
                maxId = eventId;
            }
        }
        return maxId;
    }

    /**
     * Получить максимальное время среди всех событий
     * @returns {Date|null} - Максимальное время или null
     */
    getMaxEventTime() {
        let maxTime = null;
        for (const feature of this.masterGeoJSON.features) {
            const eventTime = this.getEventTime(feature);
            if (eventTime && (!maxTime || eventTime > maxTime)) {
                maxTime = eventTime;
            }
        }
        return maxTime;
    }

    /**
     * Удалить устаревшие события из кеша
     */
    cleanupExpiredEvents() {
        const now = Date.now();

        const validFeatures = [];
        const removedIds = [];

        for (const feature of this.masterGeoJSON.features) {
            const eventTime = this.getEventTime(feature);

            // Если у события нет времени или оно не устарело (не старше 60 минут), оставляем его
            const eventAge = eventTime ? now - eventTime.getTime() : 0;

            if (!eventTime || (eventAge >= 0 && eventAge <= this.TTL_MS)) {
                const eventId = this.getEventId(feature);
                if (eventId) {
                    validFeatures.push(feature);
                } else {
                    // Если у события нет ID, но оно не устарело, тоже оставляем
                    validFeatures.push(feature);
                }
            } else {
                // Удаляем устаревшее событие из карты
                const eventId = this.getEventId(feature);
                if (eventId) {
                    this.eventsById.delete(eventId);
                    removedIds.push(eventId);
                }
            }
        }

        this.masterGeoJSON.features = validFeatures;

        if (removedIds.length > 0) {
            console.log(`Cleaned up ${removedIds.length} expired events`);
        }

        return removedIds.length;
    }

    /**
     * Запустить таймер периодической очистки
     */
    startCleanupTimer() {
        if (this.cleanupTimer) {
            clearInterval(this.cleanupTimer);
        }

        // Запускаем очистку каждые 5 минут
        this.cleanupTimer = setInterval(() => {
            this.cleanupExpiredEvents();
        }, 5 * 60 * 1000); // 5 минут
    }

    /**
     * Остановить таймер очистки
     */
    stopCleanupTimer() {
        if (this.cleanupTimer) {
            clearInterval(this.cleanupTimer);
            this.cleanupTimer = null;
        }
    }

    /**
     * Очистить все данные из кеша
     */
    clear() {
        this.masterGeoJSON = { 
            type: 'FeatureCollection', 
            features: [] 
        };
        this.eventsById.clear();
        
        // Сохраняем пустой кэш
        this.saveToCache();
    }
}

// Создаем глобальный экземпляр кеша
window.localCache = new LocalCache();

