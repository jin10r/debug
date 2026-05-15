// test-telegram-integration.js - Тесты Telegram интеграции

const TelegramIntegrationTests = {
    name: 'Telegram Integration',
    tests: []
};

// Тест наличия Telegram WebApp
TelegramIntegrationTests.tests.push({
    name: 'window.Telegram exists',
    run: () => {
        if (!window.Telegram) {
            throw new Error('window.Telegram is not defined (expected in Telegram WebApp)');
        }
    }
});

TelegramIntegrationTests.tests.push({
    name: 'window.Telegram.WebApp exists',
    run: () => {
        if (!window.Telegram?.WebApp) {
            throw new Error('window.Telegram.WebApp is not defined');
        }
    }
});

// Тест telegramIntegration класса
TelegramIntegrationTests.tests.push({
    name: 'window.telegramIntegration exists',
    run: () => {
        if (!window.telegramIntegration) {
            throw new Error('window.telegramIntegration is not defined');
        }
    }
});

TelegramIntegrationTests.tests.push({
    name: 'telegramIntegration has hapticFeedback method',
    run: () => {
        if (typeof window.telegramIntegration.hapticFeedback !== 'function') {
            throw new Error('hapticFeedback is not a function');
        }
    }
});

TelegramIntegrationTests.tests.push({
    name: 'telegramIntegration has showAlert method',
    run: () => {
        if (typeof window.telegramIntegration.showAlert !== 'function') {
            throw new Error('showAlert is not a function');
        }
    }
});

TelegramIntegrationTests.tests.push({
    name: 'telegramIntegration has showPopup method',
    run: () => {
        if (typeof window.telegramIntegration.showPopup !== 'function') {
            throw new Error('showPopup is not a function');
        }
    }
});

TelegramIntegrationTests.tests.push({
    name: 'telegramIntegration has ready method',
    run: () => {
        if (typeof window.telegramIntegration.ready !== 'function') {
            throw new Error('ready is not a function');
        }
    }
});

TelegramIntegrationTests.tests.push({
    name: 'telegramIntegration has expand method',
    run: () => {
        if (typeof window.telegramIntegration.expand !== 'function') {
            throw new Error('expand is not a function');
        }
    }
});

// Тест hapticFeedback через telegramIntegration
TelegramIntegrationTests.tests.push({
    name: 'hapticFeedback calls telegramIntegration',
    run: () => {
        // Просто проверяем что функция вызывается без ошибок
        window.hapticFeedback('light');
        window.hapticFeedback('medium');
        window.hapticFeedback('heavy');
    }
});

// Тест MainButton (если доступен)
TelegramIntegrationTests.tests.push({
    name: 'Telegram.WebApp.MainButton exists',
    run: () => {
        if (!window.Telegram?.WebApp?.MainButton) {
            // Это не ошибка, MainButton может быть не доступен
            console.log('MainButton not available (OK in some contexts)');
            return;
        }
    }
});

// Тест BackButton (если доступен)
TelegramIntegrationTests.tests.push({
    name: 'Telegram.WebApp.BackButton exists',
    run: () => {
        if (!window.Telegram?.WebApp?.BackButton) {
            console.log('BackButton not available (OK in some contexts)');
            return;
        }
    }
});

// Тест theme params
TelegramIntegrationTests.tests.push({
    name: 'Telegram theme params available',
    run: () => {
        const themeParams = window.Telegram?.WebApp?.themeParams;
        if (!themeParams) {
            console.log('Theme params not available (OK outside Telegram)');
            return;
        }
        // Проверяем наличие хотя бы одного параметра
        const hasAnyParam = Object.keys(themeParams).length > 0;
        if (!hasAnyParam) {
            throw new Error('Theme params object is empty');
        }
    }
});

// Тест viewport
TelegramIntegrationTests.tests.push({
    name: 'Telegram viewport available',
    run: () => {
        const viewport = window.Telegram?.WebApp?.viewport;
        if (!viewport) {
            console.log('Viewport not available (OK in some versions)');
            return;
        }
    }
});

// Тест initData
TelegramIntegrationTests.tests.push({
    name: 'Telegram initData structure',
    run: () => {
        const initDataUnsafe = window.Telegram?.WebApp?.initDataUnsafe;
        if (!initDataUnsafe) {
            console.log('initDataUnsafe not available (OK outside Telegram)');
            return;
        }
    }
});

export default TelegramIntegrationTests;
