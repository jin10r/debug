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
            if (event.description) {
                message = event.description.length > 100
                    ? event.description.substring(0, 100) + '...'
                    : event.description;
            }

            const iconSrc = event.layer === 'cops'
                ? '/assets/images/cops.png'
                : event.layer === 'bus'
                    ? '/assets/images/bus.png'
                    : '/assets/images/pig.png';

            // showNotification() fires haptic feedback itself (rule 5) — no
            // separate hapticFeedback() call needed here.
            if (typeof window.showNotification === 'function') window.showNotification(message, 4000, 'info', iconSrc);
            if (typeof window.playNotificationSound === 'function') {
                window.playNotificationSound();
            }
        }, index * 300);
    });
}

// Экспортируем глобально
window.handleNewEvents = handleNewEvents;

