/**
 * EventManager - Centralized event management for display and notifications
 * 
 * Features:
 * - Reactive rendering based on store changes
 * - Optimized subscription to store
 * - Render only on actual changes (events count or filter)
 * - Notification system for new events
 * 
 * @example
 * ```typescript
 * eventManager.render();
 * eventManager.notify([event1, event2]);
 * ```
 */

import { EventFeature, EventFeatureCollection } from '../types/geojson';

/**
 * EventManager interface
 */
export interface EventManager {
    /**
     * Add new events to the map and show notifications
     */
    addNewEvents: (events: EventFeature[]) => void;
    
    /**
     * Update all events (full reload without notifications)
     */
    updateAllEvents: (eventsData: EventFeatureCollection) => void;
    
    /**
     * Render events on the map
     */
    render: () => void;
    
    /**
     * Show notifications for new events
     */
    notify: (events: EventFeature[]) => void;
    
    /**
     * Last time filter (for optimization)
     */
    _lastTimeFilter: number;
    
    /**
     * Last events count (for optimization)
     */
    _lastEventsCount: number;
}

/**
 * Create EventManager instance
 */
export function createEventManager(): EventManager {
    const eventManager: EventManager = {
        _lastTimeFilter: 30,
        _lastEventsCount: 0,

        /**
         * Add new events to the map and show notifications
         */
        addNewEvents: function(events: EventFeature[]): void {
            if (!events || !events.length) {
                console.log('[EventManager] No events to add');
                return;
            }

            console.log('[EventManager] Adding', events.length, 'new events');

            // Update and notify
            requestAnimationFrame(() => {
                this.render();
                this.notify(events);
            });
        },

        /**
         * Update all events (full reload without notifications)
         */
        updateAllEvents: function(eventsData: EventFeatureCollection): void {
            console.log('[EventManager] Updating all events:', eventsData.features.length, 'events');

            // Update events in store
            window.updateEventsInStore?.(eventsData);

            // Update display with new data
            requestAnimationFrame(() => {
                this.render();
            });
        },

        /**
         * Render events on the map
         */
        render: function(): void {
            console.log('[EventManager] Rendering...');

            if (typeof window.renderDataOnMap === 'function') {
                // Use requestAnimationFrame for smooth rendering
                requestAnimationFrame(() => {
                    window.renderDataOnMap();
                });
            } else {
                console.error('[EventManager] renderDataOnMap function not found');
            }
        },

        /**
         * Show notifications for new events
         */
        notify: function(events: EventFeature[]): void {
            if (!events.length || typeof window.handleNewEvents !== 'function' || !window.eventTracker) {
                return;
            }

            const eventObjects = events.map(event => ({
                id: event.properties.id,
                layer: event.properties.layer || event.properties.type || 'unknown',
                description: event.properties.description || event.properties.name || 'Новое событие',
                ...event.properties
            }));

            const newEvents = window.eventTracker.checkForNewEvents(eventObjects);
            
            if (newEvents.length > 0) {
                console.log('[EventManager] Notifying about', newEvents.length, 'new events');
                window.handleNewEvents(newEvents);
            }
        }
    };

    return eventManager;
}

/**
 * Initialize EventManager with store subscription
 */
export function initializeEventManager(): EventManager {
    const eventManager = createEventManager();

    // Subscribe to store changes for reactive rendering
    window.store?.subscribe((state) => {
        const currentTimeFilter = state.currentTimeFilter;
        const eventsCount = state.events.features.length;

        console.log('[EventManager] Store notification:', {
            currentTimeFilter,
            eventsCount,
            lastTimeFilter: eventManager._lastTimeFilter,
            lastEventsCount: eventManager._lastEventsCount
        });

        // Render only if filter changed OR events count changed
        if (currentTimeFilter !== eventManager._lastTimeFilter ||
            eventsCount !== eventManager._lastEventsCount) {
            
            console.log('[EventManager] Change detected, scheduling render');
            
            eventManager._lastTimeFilter = currentTimeFilter;
            eventManager._lastEventsCount = eventsCount;
            
            requestAnimationFrame(() => {
                eventManager.render();
            });
        } else {
            console.log('[EventManager] No changes, skipping render');
        }
    });

    console.log('[EventManager] Initialized with reactive store subscription');

    // Start auto-refresh interval (every 5 seconds)
    window.autoRefreshInterval = setInterval(() => {
        requestAnimationFrame(() => {
            eventManager.render();
        });
    }, 5000);

    console.log('[EventManager] Auto-refresh started (5 seconds)');

    return eventManager;
}

// Create and export singleton instance
window.eventManager = initializeEventManager();

console.log('✅ EventManager initialized with reactive rendering');
