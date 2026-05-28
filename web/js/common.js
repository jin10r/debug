// common.js — общие функции, доступные глобально
// Оптимизированная версия — удалено дублирование с modules/notifications.js

/**
 * Синхронизация часов с сервером (киевское время).
 *
 * Фильтрация событий должна опираться на время сервера, а не на часы
 * устройства: устройство может быть в другом часовом поясе или иметь
 * сбитые часы. WebSocketManager обновляет serverClockOffsetMs из поля
 * timestamp каждого сообщения сервера.
 */
window.serverClockOffsetMs = 0;
window.serverNow = function() {
    return Date.now() + (window.serverClockOffsetMs || 0);
};

/**
 * Функция тактильной отдачи (вибрации).
 *
 * Telegram WebApp HapticFeedback API доступен с версии 6.1. В v6.0 API
 * существует, но методы кидают «not supported in version 6.0» — нужно
 * проверять версию ДО вызова.
 *
 * Стратегия:
 *   1. Если tg.version >= 6.1 → native HapticFeedback
 *   2. Иначе → navigator.vibrate(N) (HTML5 Vibration API)
 *   3. Иначе → silent no-op
 */
const _HAPTIC_VIBRATE_PATTERN = {
    light: 30,
    medium: 50,
    heavy: 100,
    success: [30, 10, 30],
    warning: [50, 20, 50],
    error: [100, 20, 100],
    selection_changed: 10,
};

function _parseTgVersion(versionStr) {
    if (!versionStr) return 6.1;  // assume safe default
    const parts = String(versionStr).split('.');
    const major = parseInt(parts[0], 10) || 6;
    const minor = parseInt(parts[1] || '0', 10) || 0;
    return major + minor / 10;
}

window.hapticFeedback = function(type = 'medium') {
    const tg = window.Telegram?.WebApp;
    const hasNativeHaptics = !!tg?.HapticFeedback;
    const tgVersion = _parseTgVersion(tg?.version);
    const nativeSupported = hasNativeHaptics && tgVersion >= 6.1;

    if (window.location?.hostname === 'localhost') {
        try {
            if (!window.__hapticDebugOnce) {
                window.__hapticDebugOnce = true;
                console.log('[hapticFeedback] env:', {
                    webappVersion: tg?.version,
                    versionNumber: tgVersion,
                    platform: tg?.platform,
                    hasNativeHaptics,
                    nativeSupported,
                    hasNavigatorVibrate: !!navigator.vibrate,
                });
            }
        } catch (e) {
        }
    }

    // 1. Native Telegram HapticFeedback (только для v6.1+)
    if (nativeSupported) {
        try {
            switch (type) {
                case 'light':
                    tg.HapticFeedback.impactOccurred('light');
                    return;
                case 'heavy':
                    tg.HapticFeedback.impactOccurred('heavy');
                    return;
                case 'success':
                case 'warning':
                case 'error':
                    tg.HapticFeedback.notificationOccurred(type);
                    return;
                case 'selection_changed':
                    tg.HapticFeedback.selectionChanged();
                    return;
                default:
                    tg.HapticFeedback.impactOccurred('medium');
                    return;
            }
        } catch (e) {
            // native упал — пробуем фолбэки
            if (window.location?.hostname === 'localhost') {
                console.log('[hapticFeedback] Native failed, falling back:', e);
            }
        }
    }

    // 2. telegramIntegration (отдельный wrapper, тоже проверяет версию)
    if (window.telegramIntegration) {
        try {
            const ok = window.telegramIntegration.hapticFeedback(type);
            if (ok) {
                return;
            }
        } catch (e) {
        }
    }

    // 3. HTML5 Vibration API — работает в браузерах и старых Telegram WebView
    if (navigator.vibrate) {
        try {
            const pattern = _HAPTIC_VIBRATE_PATTERN[type] || 50;
            navigator.vibrate(pattern);
            return;
        } catch (e) {
        }
    }
    // silent no-op если ничего не поддерживается
};

window.playNotificationSound = (function() {
    let audioContext = null;
    let enabled = false;

    function init() {
        if (enabled) return;
        enabled = true;
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            audioContext = new Ctx();
            if (audioContext.state === 'suspended') {
                audioContext.resume().catch(() => {});
            }
        } catch (e) {
            audioContext = null;
        }
    }

    if (document && document.addEventListener) {
        document.addEventListener('pointerdown', init, { once: true, passive: true });
        document.addEventListener('touchstart', init, { once: true, passive: true });
        document.addEventListener('mousedown', init, { once: true, passive: true });
        document.addEventListener('keydown', init, { once: true, passive: true });
    }

    return function() {
        if (!window.Telegram?.WebApp) return false;
        if (!audioContext) return false;

        try {
            if (audioContext.state === 'suspended') {
                audioContext.resume().catch(() => {});
                if (audioContext.state === 'suspended') return false;
            }

            const osc = audioContext.createOscillator();
            const gain = audioContext.createGain();

            osc.type = 'sine';
            osc.frequency.value = 880;

            gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.02, audioContext.currentTime + 0.01);
            gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.10);

            osc.connect(gain);
            gain.connect(audioContext.destination);

            osc.start();
            osc.stop(audioContext.currentTime + 0.11);
            return true;
        } catch (e) {
            return false;
        }
    };
})();

/**
 * Функция показа уведомлений
 */
window.showNotification = function(message, duration = 3000, type = 'info') {
    // Rule 5: every notification — including errors — fires haptic feedback.
    // Map the notification type to the matching haptic.
    const HAPTIC_BY_TYPE = { error: 'error', warning: 'warning', success: 'success', info: 'light' };
    window.hapticFeedback(HAPTIC_BY_TYPE[type] || 'light');

    let container = document.getElementById('notificationContainer');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notificationContainer';
        container.style.cssText = `
            position: fixed;
            top: 10px;
            left: 50%;
            transform: translateX(-50%);
            z-index: 10000;
            width: 90%;
            max-width: 500px;
            pointer-events: none;
        `;
        document.body.appendChild(container);
    }

    const notification = document.createElement('div');

    let bgColor, textColor, borderColor;
    switch (type) {
        case 'warning':
            bgColor = 'rgba(255, 165, 0, 0.9)';
            textColor = '#000000';
            borderColor = 'rgba(255, 140, 0, 0.8)';
            break;
        case 'error':
            bgColor = 'rgba(255, 50, 50, 0.9)';
            textColor = '#ffffff';
            borderColor = 'rgba(255, 0, 0, 0.8)';
            break;
        default:
            bgColor = 'var(--tg-secondary-bg-color, rgba(0, 0, 0, 0.85))';
            textColor = 'var(--tg-text-color, #ffffff)';
            borderColor = 'var(--tg-hint-color, rgba(255, 255, 255, 0.1))';
    }

    notification.style.cssText = `
        background-color: ${bgColor};
        color: ${textColor};
        padding: 12px 20px;
        border-radius: 8px;
        margin-bottom: 10px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        font-size: 14px;
        line-height: 1.4;
        pointer-events: auto;
        animation: slideDown 0.3s ease-out, hapticPulse 0.4s ease-out 0.05s;
        backdrop-filter: blur(10px);
        border: 1px solid ${borderColor};
    `;
    notification.innerHTML = message;

    if (!document.getElementById('notificationStyles')) {
        const style = document.createElement('style');
        style.id = 'notificationStyles';
        // hapticPulse — визуальный аналог тактильной отдачи. Срабатывает на
        // каждое уведомление, давая визуальную «вибрацию» независимо от
        // platform/Telegram-version. На устройствах с native HapticFeedback
        // дублирует tactile импульс; на desktop/web без vibration —
        // единственная обратная связь.
        style.textContent = `
            @keyframes slideDown {
                from { opacity: 0; transform: translateY(-20px); }
                to { opacity: 1; transform: translateY(0); }
            }
            @keyframes slideUp {
                from { opacity: 1; transform: translateY(0); }
                to { opacity: 0; transform: translateY(-20px); }
            }
            @keyframes hapticPulse {
                0%   { transform: scale(1) translateX(0); }
                15%  { transform: scale(1.02) translateX(-2px); }
                30%  { transform: scale(1.02) translateX(2px); }
                45%  { transform: scale(1.02) translateX(-1px); }
                60%  { transform: scale(1.01) translateX(1px); }
                100% { transform: scale(1) translateX(0); }
            }
            @media (prefers-reduced-motion: reduce) {
                @keyframes hapticPulse { 0%,100% { transform: none; } }
            }
        `;
        document.head.appendChild(style);
    }

    container.appendChild(notification);

    setTimeout(() => {
        notification.style.animation = 'slideUp 0.3s ease-out';
        setTimeout(() => notification.remove(), 300);
    }, duration);

    notification.addEventListener('click', () => {
        notification.style.animation = 'slideUp 0.3s ease-out';
        setTimeout(() => notification.remove(), 300);
    });
};

/**
 * Функция форматирования времени (только часы:минуты)
 * API возвращает время в UTC в формате ISO 8601
 * Конвертируем в Europe/Kiev для отображения
 */
window.formatDateTime = function(dateTimeStr) {
    if (!dateTimeStr) return '';
    try {
        const timeMatch = dateTimeStr.match(/T(\d{2}):(\d{2})/);
        if (timeMatch) {
            return `${timeMatch[1]}:${timeMatch[2]}`;
        }

        const date = new Date(dateTimeStr);
        if (isNaN(date.getTime())) return '';

        // Конвертируем UTC время в киевское
        // Используем Intl.DateTimeFormat для правильной конвертации с учетом DST
        const timeFormatter = new Intl.DateTimeFormat('ru-RU', {
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
            timeZone: 'Europe/Kiev'
        });
        
        return timeFormatter.format(date);
    } catch (e) {
        console.error('Invalid datetime format:', dateTimeStr, e);
        return '';
    }
};

/**
 * Экранирование текста описания события для безопасной вставки в попап.
 *
 * Описание приходит из Telegram-канала через парсер. Парсер уже очищает текст
 * до простых символов (text_preprocessor.clean), но рендер не должен полагаться
 * на это: текст всегда экранируется и интерпретируется только как текст —
 * никакой разметки из данных события в DOM не попадает.
 */
window.processTelegramHTML = function(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML; // textContent → innerHTML = HTML-экранированная строка
};

console.log('✅ Common functions loaded globally');