// modules/notifications.js - Уведомления о новых событиях
// Оптимизированная версия - использует функции из common.js

/**
 * Обрабатывает новые события: показывает уведомление и вибрацию
 */
interface NewEvent {
    id: string | number;
    layer: string;
    description?: string;
}

function handleNewEvents(events: NewEvent[]): void {
    events.forEach((event: NewEvent, index: number) => {
        setTimeout(() => {
            let message = '';

            if (event.layer === 'cops') {
                message = '<img src="/assets/images/cops.png" width="16" height="16" style="vertical-align: middle; margin-right: 4px;">';
            } else if (event.layer === 'bus') {
                message = '<img src="/assets/images/bus.png" width="16" height="16" style="vertical-align: middle; margin-right: 4px;">';
            } else {
                message = '<img src="/assets/images/pig.png" width="16" height="16" style="vertical-align: middle; margin-right: 4px;">';
            }

            if (event.description) {
                const description = event.description.replace(/<[^>]*>/g, '');
                message += ': ' + (description.length > 100
                    ? description.substring(0, 100) + '...'
                    : description);
            }

            // showNotification() fires haptic feedback itself (rule 5) — no
            // separate hapticFeedback() call needed here.
            if (typeof window.showNotification === 'function') window.showNotification(message, 4000);
            if (typeof window.playNotificationSound === 'function') {
                window.playNotificationSound();
            }
        }, index * 300);
    });
}

// Экспортируем глобально
window.handleNewEvents = handleNewEvents;

