// test-store.js - Тесты реактивного хранилища

const StoreTests = {
    name: 'Reactive Store',
    tests: []
};

// Тест инициализации
StoreTests.tests.push({
    name: 'Store exists globally',
    run: () => {
        if (!window.store) {
            throw new Error('window.store is not defined');
        }
    }
});

StoreTests.tests.push({
    name: 'Store has initial state',
    run: () => {
        const state = window.store.getState();
        if (!state.events) {
            throw new Error('state.events is not defined');
        }
        if (state.events.type !== 'FeatureCollection') {
            throw new Error(`Expected FeatureCollection, got ${state.events.type}`);
        }
        if (!Array.isArray(state.events.features)) {
            throw new Error('state.events.features is not an array');
        }
    }
});

StoreTests.tests.push({
    name: 'Store has default time filter',
    run: () => {
        const state = window.store.getState();
        if (typeof state.currentTimeFilter !== 'number') {
            throw new Error('currentTimeFilter is not a number');
        }
        if (state.currentTimeFilter !== 30) {
            throw new Error(`Expected 30, got ${state.currentTimeFilter}`);
        }
    }
});

StoreTests.tests.push({
    name: 'Store has default active layers',
    run: () => {
        const state = window.store.getState();
        if (!(state.activeLayers instanceof Set)) {
            throw new Error('activeLayers is not a Set');
        }
    }
});

// Тест dispatch
StoreTests.tests.push({
    name: 'dispatch UPDATE_CURRENT_TIME_FILTER',
    run: () => {
        window.store.dispatch({
            type: 'UPDATE_CURRENT_TIME_FILTER',
            payload: { minutes: 60 }
        });
        const state = window.store.getState();
        if (state.currentTimeFilter !== 60) {
            throw new Error(`Expected 60, got ${state.currentTimeFilter}`);
        }
    }
});

StoreTests.tests.push({
    name: 'dispatch SET_EVENTS',
    run: () => {
        const testEvents = {
            type: 'FeatureCollection',
            features: [
                {
                    type: 'Feature',
                    properties: { id: 1, layer: 'pig' },
                    geometry: { type: 'Point', coordinates: [30, 50] }
                }
            ]
        };
        window.store.dispatch({
            type: 'SET_EVENTS',
            payload: { events: testEvents }
        });
        const state = window.store.getState();
        if (state.events.features.length !== 1) {
            throw new Error(`Expected 1 feature, got ${state.events.features.length}`);
        }
    }
});

// Тест getFilteredItems
StoreTests.tests.push({
    name: 'getFilteredItems returns FeatureCollection',
    run: () => {
        const result = window.store.getFilteredItems();
        if (result.type !== 'FeatureCollection') {
            throw new Error(`Expected FeatureCollection, got ${result.type}`);
        }
        if (!Array.isArray(result.features)) {
            throw new Error('result.features is not an array');
        }
    }
});

// Тест getEventTime
StoreTests.tests.push({
    name: 'getEventTime extracts time from properties.time',
    run: () => {
        const event = {
            properties: { time: '2026-02-21T15:49:42+03:00' }
        };
        const result = window.store.getEventTime(event);
        if (!(result instanceof Date)) {
            throw new Error('Expected Date object');
        }
    }
});

StoreTests.tests.push({
    name: 'getEventTime returns null for missing time',
    run: () => {
        const event = { properties: {} };
        const result = window.store.getEventTime(event);
        if (result !== null) {
            throw new Error(`Expected null, got ${result}`);
        }
    }
});

StoreTests.tests.push({
    name: 'getEventTime returns null for invalid time',
    run: () => {
        const event = { properties: { time: 'invalid' } };
        const result = window.store.getEventTime(event);
        if (result !== null) {
            throw new Error(`Expected null, got ${result}`);
        }
    }
});

// Тест getEventId
StoreTests.tests.push({
    name: 'getEventId extracts id from properties.id',
    run: () => {
        const event = { properties: { id: 123 } };
        const result = window.store.getEventId(event);
        if (result !== 123) {
            throw new Error(`Expected 123, got ${result}`);
        }
    }
});

StoreTests.tests.push({
    name: 'getEventId returns null for missing id',
    run: () => {
        const event = { properties: {} };
        const result = window.store.getEventId(event);
        if (result !== null) {
            throw new Error(`Expected null, got ${result}`);
        }
    }
});

// Тест subscribe
StoreTests.tests.push({
    name: 'subscribe registers callback',
    run: () => {
        let called = false;
        const unsubscribe = window.store.subscribe(() => { called = true; });
        window.store.dispatch({
            type: 'UPDATE_CURRENT_TIME_FILTER',
            payload: { minutes: 15 }
        });
        if (!called) {
            throw new Error('Callback was not called');
        }
        unsubscribe();
    }
});

export default StoreTests;
