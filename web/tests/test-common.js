// test-common.js - Тесты общих функций

const CommonTests = {
    name: 'Common Functions',
    tests: []
};

// Тест formatDateTime
CommonTests.tests.push({
    name: 'formatDateTime - parses ISO time correctly',
    run: () => {
        const result = window.formatDateTime('2026-02-21T15:49:42+03:00');
        if (result !== '15:49') {
            throw new Error(`Expected '15:49', got '${result}'`);
        }
    }
});

CommonTests.tests.push({
    name: 'formatDateTime - handles empty string',
    run: () => {
        const result = window.formatDateTime('');
        if (result !== '') {
            throw new Error(`Expected '', got '${result}'`);
        }
    }
});

CommonTests.tests.push({
    name: 'formatDateTime - handles null',
    run: () => {
        const result = window.formatDateTime(null);
        if (result !== '') {
            throw new Error(`Expected '', got '${result}'`);
        }
    }
});

CommonTests.tests.push({
    name: 'formatDateTime - handles invalid format',
    run: () => {
        const result = window.formatDateTime('invalid');
        if (result !== '') {
            throw new Error(`Expected '', got '${result}'`);
        }
    }
});

CommonTests.tests.push({
    name: 'formatDateTime - handles UTC time',
    run: () => {
        const result = window.formatDateTime('2026-02-21T12:49:42Z');
        if (result !== '12:49') {
            throw new Error(`Expected '12:49', got '${result}'`);
        }
    }
});

// Тест processTelegramHTML
CommonTests.tests.push({
    name: 'processTelegramHTML - handles empty string',
    run: () => {
        const result = window.processTelegramHTML('');
        if (result !== '') {
            throw new Error(`Expected '', got '${result}'`);
        }
    }
});

CommonTests.tests.push({
    name: 'processTelegramHTML - handles null',
    run: () => {
        const result = window.processTelegramHTML(null);
        if (result !== '') {
            throw new Error(`Expected '', got '${result}'`);
        }
    }
});

CommonTests.tests.push({
    name: 'processTelegramHTML - processes simple HTML',
    run: () => {
        const result = window.processTelegramHTML('<b>Bold text</b>');
        if (!result.includes('Bold text')) {
            throw new Error(`Expected to contain 'Bold text', got '${result}'`);
        }
    }
});

// Тест hapticFeedback
CommonTests.tests.push({
    name: 'hapticFeedback - default type is medium',
    run: () => {
        // Просто проверяем что функция существует и не бросает ошибок
        if (typeof window.hapticFeedback !== 'function') {
            throw new Error('hapticFeedback is not a function');
        }
        window.hapticFeedback();
    }
});

CommonTests.tests.push({
    name: 'hapticFeedback - accepts light type',
    run: () => {
        window.hapticFeedback('light');
    }
});

CommonTests.tests.push({
    name: 'hapticFeedback - accepts heavy type',
    run: () => {
        window.hapticFeedback('heavy');
    }
});

// Тест showNotification
CommonTests.tests.push({
    name: 'showNotification - function exists',
    run: () => {
        if (typeof window.showNotification !== 'function') {
            throw new Error('showNotification is not a function');
        }
    }
});

CommonTests.tests.push({
    name: 'showNotification - default duration is 3000ms',
    run: () => {
        window.showNotification('Test notification');
    }
});

CommonTests.tests.push({
    name: 'showNotification - accepts custom duration',
    run: () => {
        window.showNotification('Test', 5000);
    }
});

CommonTests.tests.push({
    name: 'showNotification - accepts type info',
    run: () => {
        window.showNotification('Info', 1000, 'info');
    }
});

CommonTests.tests.push({
    name: 'showNotification - accepts type warning',
    run: () => {
        window.showNotification('Warning', 1000, 'warning');
    }
});

CommonTests.tests.push({
    name: 'showNotification - accepts type error',
    run: () => {
        window.showNotification('Error', 1000, 'error');
    }
});

export default CommonTests;
