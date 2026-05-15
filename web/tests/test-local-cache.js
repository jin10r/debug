// test-local-cache.js - Тесты локального кэша

const LocalCacheTests = {
    name: 'Local Cache',
    tests: []
};

// Тест инициализации
LocalCacheTests.tests.push({
    name: 'window.localCache exists',
    run: () => {
        if (!window.localCache) {
            throw new Error('window.localCache is not defined');
        }
    }
});

LocalCacheTests.tests.push({
    name: 'localCache has masterGeoJSON',
    run: () => {
        if (!window.localCache.masterGeoJSON) {
            throw new Error('masterGeoJSON is not defined');
        }
        if (window.localCache.masterGeoJSON.type !== 'FeatureCollection') {
            throw new Error('masterGeoJSON is not FeatureCollection');
        }
    }
});

LocalCacheTests.tests.push({
    name: 'localCache has eventsById Map',
    run: () => {
        if (!(window.localCache.eventsById instanceof Map)) {
            throw new Error('eventsById is not a Map');
        }
    }
});

// Тест getEventId
LocalCacheTests.tests.push({
    name: 'getEventId extracts id from feature',
    run: () => {
        const feature = {
            properties: { id: 456 }
        };
        const result = window.localCache.getEventId(feature);
        if (result !== 456) {
            throw new Error(`Expected 456, got ${result}`);
        }
    }
});

LocalCacheTests.tests.push({
    name: 'getEventId returns null for missing id',
    run: () => {
        const feature = { properties: {} };
        const result = window.localCache.getEventId(feature);
        if (result !== null) {
            throw new Error(`Expected null, got ${result}`);
        }
    }
});

// Тест getEventTime
LocalCacheTests.tests.push({
    name: 'getEventTime extracts time from feature',
    run: () => {
        const feature = {
            properties: { time: '2026-02-21T15:49:42+03:00' }
        };
        const result = window.localCache.getEventTime(feature);
        if (!(result instanceof Date)) {
            throw new Error('Expected Date object');
        }
    }
});

LocalCacheTests.tests.push({
    name: 'getEventTime returns null for missing time',
    run: () => {
        const feature = { properties: {} };
        const result = window.localCache.getEventTime(feature);
        if (result !== null) {
            throw new Error(`Expected null, got ${result}`);
        }
    }
});

// Тест addEvents
LocalCacheTests.tests.push({
    name: 'addEvents adds single event',
    run: () => {
        const event = {
            type: 'Feature',
            properties: { id: 999, time: new Date().toISOString(), layer: 'pig' },
            geometry: { type: 'Point', coordinates: [30, 50] }
        };
        const result = window.localCache.addEvents([event]);
        if (!result) {
            throw new Error('addEvents returned false');
        }
        if (!window.localCache.eventsById.has(999)) {
            throw new Error('Event was not added to eventsById');
        }
    }
});

LocalCacheTests.tests.push({
    name: 'addEvents skips event without ID',
    run: () => {
        const event = {
            type: 'Feature',
            properties: { time: new Date().toISOString() },
            geometry: { type: 'Point', coordinates: [30, 50] }
        };
        const result = window.localCache.addEvents([event]);
        if (result !== false) {
            throw new Error('addEvents should return false for event without ID');
        }
    }
});

// Тест replaceAllEvents
LocalCacheTests.tests.push({
    name: 'replaceAllEvents replaces all events',
    run: () => {
        const events = {
            type: 'FeatureCollection',
            features: [
                {
                    type: 'Feature',
                    properties: { id: 1, time: new Date().toISOString(), layer: 'pig' },
                    geometry: { type: 'Point', coordinates: [30, 50] }
                },
                {
                    type: 'Feature',
                    properties: { id: 2, time: new Date().toISOString(), layer: 'cops' },
                    geometry: { type: 'Point', coordinates: [30.5, 50.5] }
                }
            ]
        };
        window.localCache.replaceAllEvents(events);
        if (window.localCache.masterGeoJSON.features.length !== 2) {
            throw new Error(`Expected 2 features, got ${window.localCache.masterGeoJSON.features.length}`);
        }
    }
});

// Тест getEventsByTimeFilter
LocalCacheTests.tests.push({
    name: 'getEventsByTimeFilter filters by time',
    run: () => {
        const now = new Date();
        const recentEvent = {
            type: 'Feature',
            properties: { id: 100, time: now.toISOString(), layer: 'pig' },
            geometry: { type: 'Point', coordinates: [30, 50] }
        };
        window.localCache.addEvents([recentEvent]);
        
        const result = window.localCache.getEventsByTimeFilter(30);
        if (result.type !== 'FeatureCollection') {
            throw new Error('Expected FeatureCollection');
        }
    }
});

// Тест cleanupExpiredEvents
LocalCacheTests.tests.push({
    name: 'cleanupExpiredEvents removes old events',
    run: () => {
        // Добавляем событие с очень старым временем
        const oldDate = new Date(Date.now() - 2 * 60 * 60 * 1000); // 2 hours ago
        const oldEvent = {
            type: 'Feature',
            properties: { id: 9999, time: oldDate.toISOString(), layer: 'pig' },
            geometry: { type: 'Point', coordinates: [30, 50] }
        };
        window.localCache.addEvents([oldEvent]);
        
        const removed = window.localCache.cleanupExpiredEvents();
        if (window.localCache.eventsById.has(9999)) {
            throw new Error('Old event was not removed');
        }
    }
});

export default LocalCacheTests;
