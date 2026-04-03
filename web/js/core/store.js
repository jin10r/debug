// js/core/store.js - Centralized reactive state management

class ReactiveStore {
    constructor() {
        // Initial state
        this.state = {
            events: { 
                type: 'FeatureCollection', 
                features: [] 
            },
            eventsUpdatedAt: 0,
            currentTimeFilter: 30,
            activeLayers: new Set(['pig', 'cops', 'bus']),
            isSyncInProgress: false,
            lastStatusCheckAt: 0,
            consecutiveNetworkErrors: 0,
            updateInterval: 15000,
            isNetworkErrorDisplayed: false
        };
        
        // Subscribers to state changes
        this.subscribers = [];
        
        // Action handlers
        this.actionHandlers = {
            SET_EVENTS: this.setEvents.bind(this),
            ADD_EVENTS: this.addEvents.bind(this),
            UPDATE_CURRENT_TIME_FILTER: this.updateCurrentTimeFilter.bind(this),
            TOGGLE_LAYER: this.toggleLayer.bind(this),
            CLEAR_EVENTS: this.clearEvents.bind(this)
        };
    }
    
    /**
     * Subscribe to state changes
     * @param {Function} subscriber - Callback function to be called on state changes
     * @returns {Function} Unsubscribe function
     */
    subscribe(subscriber) {
        this.subscribers.push(subscriber);
        
        // Return unsubscribe function
        return () => {
            const index = this.subscribers.indexOf(subscriber);
            if (index > -1) {
                this.subscribers.splice(index, 1);
            }
        };
    }
    
    /**
     * Notify all subscribers about state changes
     */
    notifySubscribers() {
        const state = this.getState();

        // Debug logging disabled to reduce console noise
        // console.log('[store.notifySubscribers] Notifying subscribers:', {
        //     events_count: state.events.features.length,
        //     currentTimeFilter: state.currentTimeFilter,
        //     activeLayers: Array.from(state.activeLayers)
        // });

        // Create a copy of subscribers to avoid issues if subscribers modify the list
        const subscribersCopy = [...this.subscribers];
        subscribersCopy.forEach(subscriber => {
            try {
                subscriber(state);
            } catch (error) {
                console.error('Error in subscriber:', error);
            }
        });

        // Синхронизируем с APP_STATE для обратной совместимости
        if (window.APP_STATE) {
            window.APP_STATE.currentTimeFilter = state.currentTimeFilter;
            window.APP_STATE.activeLayers = state.activeLayers;
            window.APP_STATE.events = state.events;
        }
    }
    
    /**
     * Get current state
     * @returns {Object} Current state
     */
    getState() {
        return { ...this.state };
    }
    
    /**
     * Dispatch an action to update state
     * @param {Object} action - Action object with type and payload
     */
    dispatch(action) {
        if (this.actionHandlers[action.type]) {
            // Create a deep copy of the current state to avoid mutations
            const prevState = JSON.parse(JSON.stringify(this.state));
            
            // Execute the action handler
            this.actionHandlers[action.type](action.payload);
            
            // Check if state actually changed
            const stateChanged = JSON.stringify(prevState) !== JSON.stringify(this.state);
            
            if (stateChanged) {
                this.notifySubscribers();
            }
        } else {
            console.warn('Unknown action type:', action.type);
        }
    }
    
    // Action handlers
    setEvents(payload) {
        this.state.events = payload.events || { type: 'FeatureCollection', features: [] };
        this.state.eventsUpdatedAt = Date.now();
    }
    
    addEvents(payload) {
        const newFeatures = payload.events || [];
        this.state.events.features = [...this.state.events.features, ...newFeatures];
        this.state.eventsUpdatedAt = Date.now();
    }
    
    updateCurrentTimeFilter(payload) {
        this.state.currentTimeFilter = payload.minutes;
    }
    
    toggleLayer(payload) {
        const layer = payload.layer;
        if (this.state.activeLayers.has(layer)) {
            this.state.activeLayers.delete(layer);
        } else {
            this.state.activeLayers.add(layer);
        }
    }
    
    clearEvents() {
        this.state.events = { type: 'FeatureCollection', features: [] };
    }
    
    // Selector functions
    getFilteredItems(state = this.getState()) {
        const { events, currentTimeFilter, activeLayers } = state;
        const filterMs = currentTimeFilter * 60 * 1000;
        const now = Date.now();
        
        let filteredCount = 0;
        let timeFilteredCount = 0;
        let layerFilteredCount = 0;

        // Filter events by time and active layers
        const filteredFeatures = events.features.filter(feature => {
            // Check layer filter first (faster)
            const eventLayer = feature.properties?.layer || feature.properties?.type || 'unknown';
            if (!activeLayers.has(eventLayer)) {
                layerFilteredCount++;
                return false;
            }

            // Check time filter
            const eventTime = this.getEventTime(feature);
            if (eventTime) {
                const ageMs = now - eventTime.getTime();

                // Отклоняем будущие события (больше 5 минут вперед)
                if (ageMs < -5 * 60 * 1000) {
                    return false;
                }

                // Отклоняем слишком старые события
                if (ageMs > filterMs) {
                    timeFilteredCount++;
                    return false;
                }
            }

            filteredCount++;
            return true;
        });

        // Debug logging disabled to reduce console noise
        // console.log('[store.getFilteredItems] Filter stats:', {
        //     total: events.features.length,
        //     filtered: filteredCount,
        //     time_filtered_out: timeFilteredCount,
        //     layer_filtered_out: layerFilteredCount,
        //     currentTimeFilter,
        //     activeLayers: Array.from(activeLayers)
        // });

        return {
            type: 'FeatureCollection',
            features: filteredFeatures
        };
    }
    
    getEventTime(event) {
        if (!event || !event.properties) return null;
        
        // Try different possible time fields
        const timeStr = event.properties.time || event.properties.created_at || event.properties.timestamp;
        if (!timeStr) return null;
        
        try {
            return new Date(timeStr);
        } catch (e) {
            console.error('Error parsing event time:', timeStr, e);
            return null;
        }
    }
    
    getEventId(event) {
        if (!event || !event.properties) return null;
        return event.properties.id || event.properties.event_id || event.properties._id || event.properties.uid || null;
    }
    
    getMaxEventId(state = this.getState()) {
        let maxId = 0;
        for (const feature of state.events.features) {
            const eventId = this.getEventId(feature);
            if (eventId && eventId > maxId) {
                maxId = eventId;
            }
        }
        return maxId;
    }
    
    getMaxEventTime(state = this.getState()) {
        let maxTime = null;
        for (const feature of state.events.features) {
            const eventTime = this.getEventTime(feature);
            if (eventTime && (!maxTime || eventTime > maxTime)) {
                maxTime = eventTime;
            }
        }
        return maxTime;
    }
}

// Create and export singleton instance
window.store = new ReactiveStore();

console.log('✅ Reactive store initialized with state management and selectors');