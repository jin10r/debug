// test-notifications.js - Тесты уведомлений

const NotificationsTests = {
    name: 'Notifications',
    tests: []
};

// Тест eventTracker
NotificationsTests.tests.push({
    name: 'window.eventTracker exists',
    run: () => {
        if (!window.eventTracker) {
            throw new Error('window.eventTracker is not defined');
        }
    }
});

NotificationsTests.tests.push({
    name: 'eventTracker.checkForNewEvents returns empty on first call',
    run: () => {
        // Сбрасываем трекер
        window.eventTracker.knownEventIds.clear();
        window.eventTracker.isFirstLoad = true;
        
        const events = [
            { id: 1, layer: 'pig' },
            { id: 2, layer: 'cops' }
        ];
        const result = window.eventTracker.checkForNewEvents(events);
        if (result.length !== 0) {
            throw new Error(`Expected 0 new events on first call, got ${result.length}`);
        }
    }
});

NotificationsTests.tests.push({
    name: 'eventTracker.checkForNewEvents detects new events',
    run: () => {
        const events = [
            { id: 3, layer: 'pig' },
            { id: 4, layer: 'bus' }
        ];
        const result = window.eventTracker.checkForNewEvents(events);
        if (result.length !== 2) {
            throw new Error(`Expected 2 new events, got ${result.length}`);
        }
    }
});

NotificationsTests.tests.push({
    name: 'eventTracker.checkForNewEvents ignores known events',
    run: () => {
        const events = [
            { id: 3, layer: 'pig' },
            { id: 4, layer: 'bus' }
        ];
        const result = window.eventTracker.checkForNewEvents(events);
        if (result.length !== 0) {
            throw new Error(`Expected 0 new events for known IDs, got ${result.length}`);
        }
    }
});

NotificationsTests.tests.push({
    name: 'eventTracker.cleanup removes old IDs',
    run: () => {
        window.eventTracker.knownEventIds.add(999);
        window.eventTracker.cleanup([1, 2, 3, 4]);
        if (window.eventTracker.knownEventIds.has(999)) {
            throw new Error('Old ID was not removed');
        }
    }
});

// Тест handleNewEvents
NotificationsTests.tests.push({
    name: 'window.handleNewEvents exists',
    run: () => {
        if (typeof window.handleNewEvents !== 'function') {
            throw new Error('window.handleNewEvents is not a function');
        }
    }
});

NotificationsTests.tests.push({
    name: 'handleNewEvents processes events with layer cops',
    run: () => {
        const events = [
            { id: 10, layer: 'cops', description: 'Test event' }
        ];
        window.handleNewEvents(events);
        // Функция должна выполниться без ошибок
    }
});

NotificationsTests.tests.push({
    name: 'handleNewEvents processes events with layer bus',
    run: () => {
        const events = [
            { id: 11, layer: 'bus', description: 'Test event' }
        ];
        window.handleNewEvents(events);
    }
});

NotificationsTests.tests.push({
    name: 'handleNewEvents processes events with layer pig',
    run: () => {
        const events = [
            { id: 12, layer: 'pig', description: 'Test event' }
        ];
        window.handleNewEvents(events);
    }
});

NotificationsTests.tests.push({
    name: 'handleNewEvents handles long description',
    run: () => {
        const events = [
            { 
                id: 13, 
                layer: 'pig', 
                description: 'A'.repeat(200) // 200 characters
            }
        ];
        window.handleNewEvents(events);
    }
});

NotificationsTests.tests.push({
    name: 'handleNewEvents handles missing description',
    run: () => {
        const events = [
            { id: 14, layer: 'pig' }
        ];
        window.handleNewEvents(events);
    }
});

export default NotificationsTests;
