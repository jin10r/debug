// test-time-filtering.js - Тесты фильтрации по времени

const TimeFilteringTests = {
    name: 'Time Filtering',
    tests: []
};

// Тест фильтрации в store
TimeFilteringTests.tests.push({
    name: 'getFilteredItems filters events within time window',
    run: () => {
        const now = new Date();
        const kievOffset = 3 * 60 * 60 * 1000; // 3 hours
        const kievNow = new Date(now.getTime() + kievOffset);
        
        // Создаём событие 15 минут назад (в киевском времени)
        const eventTime = new Date(kievNow.getTime() - 15 * 60 * 1000);
        
        const testEvents = {
            type: 'FeatureCollection',
            features: [{
                type: 'Feature',
                properties: { 
                    id: 1, 
                    time: eventTime.toISOString(),
                    layer: 'pig'
                },
                geometry: { type: 'Point', coordinates: [30, 50] }
            }]
        };
        
        window.store.dispatch({ type: 'SET_EVENTS', payload: { events: testEvents } });
        window.store.dispatch({ 
            type: 'UPDATE_CURRENT_TIME_FILTER', 
            payload: { minutes: 30 } 
        });
        
        const result = window.store.getFilteredItems();
        if (result.features.length !== 1) {
            throw new Error(`Expected 1 event within 30 min, got ${result.features.length}`);
        }
    }
});

TimeFilteringTests.tests.push({
    name: 'getFilteredItems excludes events older than filter',
    run: () => {
        const now = new Date();
        const kievOffset = 3 * 60 * 60 * 1000;
        const kievNow = new Date(now.getTime() + kievOffset);
        
        // Создаём событие 45 минут назад (старше 30-минутного фильтра)
        const eventTime = new Date(kievNow.getTime() - 45 * 60 * 1000);
        
        const testEvents = {
            type: 'FeatureCollection',
            features: [{
                type: 'Feature',
                properties: { 
                    id: 2, 
                    time: eventTime.toISOString(),
                    layer: 'pig'
                },
                geometry: { type: 'Point', coordinates: [30, 50] }
            }]
        };
        
        window.store.dispatch({ type: 'SET_EVENTS', payload: { events: testEvents } });
        window.store.dispatch({ 
            type: 'UPDATE_CURRENT_TIME_FILTER', 
            payload: { minutes: 30 } 
        });
        
        const result = window.store.getFilteredItems();
        if (result.features.length !== 0) {
            throw new Error(`Expected 0 events (older than 30 min), got ${result.features.length}`);
        }
    }
});

TimeFilteringTests.tests.push({
    name: 'getFilteredItems filters by active layers',
    run: () => {
        const now = new Date();
        const kievOffset = 3 * 60 * 60 * 1000;
        const kievNow = new Date(now.getTime() + kievOffset);
        const eventTime = kievNow;
        
        const testEvents = {
            type: 'FeatureCollection',
            features: [
                {
                    type: 'Feature',
                    properties: { id: 3, time: eventTime.toISOString(), layer: 'pig' },
                    geometry: { type: 'Point', coordinates: [30, 50] }
                },
                {
                    type: 'Feature',
                    properties: { id: 4, time: eventTime.toISOString(), layer: 'cops' },
                    geometry: { type: 'Point', coordinates: [30.5, 50.5] }
                },
                {
                    type: 'Feature',
                    properties: { id: 5, time: eventTime.toISOString(), layer: 'bus' },
                    geometry: { type: 'Point', coordinates: [31, 51] }
                }
            ]
        };
        
        window.store.dispatch({ type: 'SET_EVENTS', payload: { events: testEvents } });
        window.store.dispatch({ 
            type: 'UPDATE_CURRENT_TIME_FILTER', 
            payload: { minutes: 60 } 
        });
        
        // Фильтруем только pig слой
        window.store.state.activeLayers = new Set(['pig']);
        
        const result = window.store.getFilteredItems();
        if (result.features.length !== 1) {
            throw new Error(`Expected 1 pig event, got ${result.features.length}`);
        }
        if (result.features[0].properties.layer !== 'pig') {
            throw new Error(`Expected pig layer, got ${result.features[0].properties.layer}`);
        }
    }
});

TimeFilteringTests.tests.push({
    name: 'getFilteredItems includes events without time',
    run: () => {
        const testEvents = {
            type: 'FeatureCollection',
            features: [{
                type: 'Feature',
                properties: { id: 6, layer: 'pig' }, // Нет времени
                geometry: { type: 'Point', coordinates: [30, 50] }
            }]
        };
        
        window.store.dispatch({ type: 'SET_EVENTS', payload: { events: testEvents } });
        window.store.dispatch({ 
            type: 'UPDATE_CURRENT_TIME_FILTER', 
            payload: { minutes: 30 } 
        });
        window.store.state.activeLayers = new Set(['pig']);
        
        const result = window.store.getFilteredItems();
        // События без времени должны включаться
        if (result.features.length < 1) {
            throw new Error(`Expected at least 1 event, got ${result.features.length}`);
        }
    }
});

TimeFilteringTests.tests.push({
    name: 'formatDateTime extracts time from ISO string',
    run: () => {
        const result = window.formatDateTime('2026-02-21T15:30:45+03:00');
        if (result !== '15:30') {
            throw new Error(`Expected '15:30', got '${result}'`);
        }
    }
});

TimeFilteringTests.tests.push({
    name: 'formatDateTime handles UTC format',
    run: () => {
        const result = window.formatDateTime('2026-02-21T12:30:45Z');
        if (result !== '12:30') {
            throw new Error(`Expected '12:30', got '${result}'`);
        }
    }
});

export default TimeFilteringTests;
