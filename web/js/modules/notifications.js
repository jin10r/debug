// modules/notifications.js - Уведомления о новых событиях
// Оптимизированная версия - использует функции из common.js

/**
 * Трекер новых событий
 */
class EventTracker {
    constructor() {
        this.knownEventIds = new Set();
        this.isFirstLoad = true;
    }

    checkForNewEvents(events) {
        if (this.isFirstLoad) {
            events.forEach(event => {
                if (event.id) this.knownEventIds.add(event.id);
            });
            this.isFirstLoad = false;
            return [];
        }

        const newEvents = [];
        events.forEach(event => {
            if (event.id && !this.knownEventIds.has(event.id)) {
                newEvents.push(event);
                this.knownEventIds.add(event.id);
            }
        });

        return newEvents;
    }

    cleanup(currentEventIds) {
        const currentIds = new Set(currentEventIds);
        this.knownEventIds = new Set(
            [...this.knownEventIds].filter(id => currentIds.has(id))
        );
    }
}

const eventTracker = new EventTracker();

/**
 * Обрабатывает новые события: показывает уведомление и вибрацию
 */
function handleNewEvents(events) {
    events.forEach((event, index) => {
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
window.eventTracker = eventTracker;
window.handleNewEvents = handleNewEvents;

