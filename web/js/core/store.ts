/**
 * ReactiveStore - Centralized state management with memoized selectors
 * 
 * Features:
 * - Reactive state management with subscribers
 * - Memoized selectors for performance
 * - Cache invalidation on state changes
 * - Sync with APP_STATE for compatibility
 * 
 * @example
 * ```typescript
 * const store = new ReactiveStore();
 * store.subscribe((state) => { console.log('State changed:', state); });
 * store.dispatch({ type: 'SET_EVENTS', payload: { events } });
 * ```
 */

import { EventFeature, EventFeatureCollection, StoreState, EventLayer } from '../types/geojson';

/**
 * Action types
 */
export enum ActionType {
    SET_EVENTS = 'SET_EVENTS',
    ADD_EVENTS = 'ADD_EVENTS',
    UPDATE_CURRENT_TIME_FILTER = 'UPDATE_CURRENT_TIME_FILTER',
    TOGGLE_LAYER = 'TOGGLE_LAYER',
    CLEAR_EVENTS = 'CLEAR_EVENTS'
}

/**
 * Action interface
 */
export interface Action {
    type: ActionType;
    payload: unknown;
}

/**
 * Subscriber callback type
 */
type StateSubscriber = (state: StoreState) => void;

/**
 * Action handler type
 */
type ActionHandler = (payload: unknown) => void;

/**
 * Filter cache interface
 */
interface FilterCache {
    key: string;
    value: EventFeatureCollection;
    timestamp: number;
}

/**
 * ReactiveStore class
 */
export class ReactiveStore {
    /** Current state */
    private state: StoreState;
    
    /** Subscribers list */
    private subscribers: StateSubscriber[] = [];
    
    /** Filter cache for memoization */
    private filterCache: FilterCache | null = null;
    
    /** Action handlers */
    private actionHandlers: Record<ActionType, ActionHandler>;

    /**
     * Create ReactiveStore instance
     */
    constructor() {
        this.state = {
            events: {
                type: 'FeatureCollection',
                features: []
            },
            currentTimeFilter: 30,
            activeLayers: new Set<EventLayer>(['pig', 'cops', 'bus'])
        };

        // Bind action handlers
        this.actionHandlers = {
            [ActionType.SET_EVENTS]: this.setEvents.bind(this),
            [ActionType.ADD_EVENTS]: this.addEvents.bind(this),
            [ActionType.UPDATE_CURRENT_TIME_FILTER]: this.updateCurrentTimeFilter.bind(this),
            [ActionType.TOGGLE_LAYER]: this.toggleLayer.bind(this),
            [ActionType.CLEAR_EVENTS]: this.clearEvents.bind(this)
        };

        console.log('[Store] Initialized with default state');
    }

    /**
     * Subscribe to state changes
     * 
     * @param subscriber - Callback function
     * @returns Unsubscribe function
     */
    subscribe(subscriber: StateSubscriber): () => void {
        this.subscribers.push(subscriber);
        console.log('[Store] Subscriber added, total:', this.subscribers.length);

        // Return unsubscribe function
        return () => {
            const index = this.subscribers.indexOf(subscriber);
            if (index > -1) {
                this.subscribers.splice(index, 1);
                console.log('[Store] Subscriber removed, total:', this.subscribers.length);
            }
        };
    }

    /**
     * Notify all subscribers about state changes
     */
    notifySubscribers(): void {
        const state = this.getState();
        
        // Invalidate filter cache on state change
        this.filterCache = null;
        
        console.log('[Store] Notifying subscribers:', {
            events_count: state.events.features.length,
            currentTimeFilter: state.currentTimeFilter,
            activeLayers: Array.from(state.activeLayers)
        });

        // Create a copy of subscribers to avoid issues if subscribers modify the list
        const subscribersCopy = [...this.subscribers];
        
        subscribersCopy.forEach(subscriber => {
            try {
                subscriber(state);
            } catch (error) {
                console.error('[Store] Error in subscriber:', error);
            }
        });

        // Sync with APP_STATE for compatibility
        if (window.APP_STATE) {
            window.APP_STATE.currentTimeFilter = state.currentTimeFilter;
            window.APP_STATE.activeLayers = state.activeLayers as unknown as Set<string>;
            window.APP_STATE.events = state.events;
        }
    }

    /**
     * Get current state
     */
    getState(): StoreState {
        return { ...this.state };
    }

    /**
     * Dispatch an action to update state
     *
     * @param action - Action object with type and payload
     */
    dispatch(action: Action): void {
        const handler = this.actionHandlers[action.type];

        if (handler) {
            const prevEventsCount = this.state.events.features.length;
            const prevFilter = this.state.currentTimeFilter;

            console.log('[Store] Dispatch:', action.type, 'prev count:', prevEventsCount);

            // Execute action handler
            handler(action.payload);

            const newEventsCount = this.state.events.features.length;
            const newFilter = this.state.currentTimeFilter;

            // Only notify if state actually changed
            const stateChanged = prevEventsCount !== newEventsCount || prevFilter !== newFilter;

            console.log('[Store] After dispatch:', {
                newCount: newEventsCount,
                newFilter: newFilter,
                stateChanged: stateChanged
            });

            if (stateChanged) {
                console.log('[Store] State changed, notifying subscribers...');
                this.notifySubscribers();
            } else {
                console.log('[Store] State unchanged, skipping notification');
            }
        } else {
            console.warn('[Store] Unknown action type:', action.type);
        }
    }

    /**
     * Set events action handler
     */
    private setEvents(payload: { events: EventFeatureCollection }): void {
        this.state.events = payload.events || {
            type: 'FeatureCollection',
            features: []
        };
        console.log('[Store] SET_EVENTS:', this.state.events.features.length, 'events');
    }

    /**
     * Add events action handler
     */
    private addEvents(payload: { events: EventFeature[] }): void {
        const newFeatures = payload.events || [];
        this.state.events.features = [...this.state.events.features, ...newFeatures];
        console.log('[Store] ADD_EVENTS:', newFeatures.length, 'new events');
    }

    /**
     * Update current time filter action handler
     */
    private updateCurrentTimeFilter(payload: { minutes: 15 | 30 | 60 }): void {
        this.state.currentTimeFilter = payload.minutes;
        console.log('[Store] UPDATE_CURRENT_TIME_FILTER:', payload.minutes, 'minutes');
    }

    /**
     * Toggle layer action handler
     */
    private toggleLayer(payload: { layer: string }): void {
        const layer = payload.layer as EventLayer;
        
        if (this.state.activeLayers.has(layer)) {
            this.state.activeLayers.delete(layer);
            console.log('[Store] TOGGLE_LAYER:', layer, '→ disabled');
        } else {
            this.state.activeLayers.add(layer);
            console.log('[Store] TOGGLE_LAYER:', layer, '→ enabled');
        }
    }

    /**
     * Clear events action handler
     */
    private clearEvents(): void {
        this.state.events = {
            type: 'FeatureCollection',
            features: []
        };
        console.log('[Store] CLEAR_EVENTS');
    }

    /**
     * Get filtered items with memoization
     * 
     * Memoization key: currentTimeFilter-eventsCount-activeLayers
     * Cache invalidates on any state change
     * 
     * @returns Filtered EventFeatureCollection
     */
    getFilteredItems(): EventFeatureCollection {
        const { events, currentTimeFilter, activeLayers } = this.state;
        const filterMs = currentTimeFilter * 60 * 1000;
        const now = Date.now();

        // Generate cache key
        const cacheKey = `${currentTimeFilter}-${events.features.length}-${Array.from(activeLayers).sort().join(',')}`;

        // Check cache
        if (this.filterCache && this.filterCache.key === cacheKey) {
            console.log('[Store] getFilteredItems: cache hit');
            return this.filterCache.value;
        }

        console.log('[Store] getFilteredItems: cache miss, computing...');

        // Compute filtered items
        const result = this._computeFilteredItems(events, filterMs, now, activeLayers);

        // Store in cache
        this.filterCache = {
            key: cacheKey,
            value: result,
            timestamp: Date.now()
        };

        return result;
    }

    /**
     * Compute filtered items (internal)
     */
    private _computeFilteredItems(
        events: EventFeatureCollection,
        filterMs: number,
        now: number,
        activeLayers: Set<EventLayer>
    ): EventFeatureCollection {
        let filteredCount = 0;
        let timeFilteredCount = 0;
        let layerFilteredCount = 0;

        const filteredFeatures = events.features.filter(feature => {
            // Check layer filter first (faster)
            const eventLayer = (feature.properties?.layer || feature.properties?.type || 'unknown') as EventLayer;
            
            if (!activeLayers.has(eventLayer)) {
                layerFilteredCount++;
                return false;
            }

            // Check time filter
            const eventTime = this.getEventTime(feature);
            
            if (eventTime) {
                const ageMs = now - eventTime.getTime();

                // Reject future events (more than 5 minutes ahead)
                if (ageMs < -5 * 60 * 1000) {
                    return false;
                }

                // Reject too old events
                if (ageMs > filterMs) {
                    timeFilteredCount++;
                    return false;
                }
            }

            filteredCount++;
            return true;
        });

        console.log('[Store] Filter stats:', {
            total: events.features.length,
            filtered: filteredCount,
            time_filtered_out: timeFilteredCount,
            layer_filtered_out: layerFilteredCount,
            currentTimeFilter: this.state.currentTimeFilter,
            activeLayers: Array.from(activeLayers)
        });

        return {
            type: 'FeatureCollection',
            features: filteredFeatures
        };
    }

    /**
     * Get event time from feature
     */
    getEventTime(feature: EventFeature): Date | null {
        if (!feature?.properties) return null;

        const timeStr = feature.properties.time ?? feature.properties.created_at ?? feature.properties.timestamp;
        if (!timeStr) return null;

        try {
            return new Date(timeStr);
        } catch (e) {
            console.error('[Store] Error parsing event time:', timeStr, e);
            return null;
        }
    }

    /**
     * Get event ID from feature
     */
    getEventId(feature: EventFeature): string | number | null {
        if (!feature?.properties) return null;

        const id =
            feature.properties.id ??
            feature.properties.event_id ??
            feature.properties._id ??
            feature.properties.uid ??
            null;

        return id as string | number | null;
    }

    /**
     * Get maximum event ID
     */
    getMaxEventId(state?: StoreState): number {
        const currentState = state || this.state;
        let maxId = 0;
        
        for (const feature of currentState.events.features) {
            const eventId = this.getEventId(feature);
            if (eventId && typeof eventId === 'number' && eventId > maxId) {
                maxId = eventId;
            }
        }
        
        return maxId;
    }

    /**
     * Get maximum event time
     */
    getMaxEventTime(state?: StoreState): Date | null {
        const currentState = state || this.state;
        let maxTime: Date | null = null;
        
        for (const feature of currentState.events.features) {
            const eventTime = this.getEventTime(feature);
            if (eventTime && (!maxTime || eventTime > maxTime)) {
                maxTime = eventTime;
            }
        }
        
        return maxTime;
    }

    /**
     * Get store statistics
     */
    getStats(): {
        totalEvents: number;
        currentTimeFilter: number;
        activeLayers: string[];
        subscribers: number;
        cacheHit: boolean;
    } {
        return {
            totalEvents: this.state.events.features.length,
            currentTimeFilter: this.state.currentTimeFilter,
            activeLayers: Array.from(this.state.activeLayers),
            subscribers: this.subscribers.length,
            cacheHit: this.filterCache !== null
        };
    }

    /**
     * Clear filter cache (force recomputation)
     */
    clearFilterCache(): void {
        this.filterCache = null;
        console.log('[Store] Filter cache cleared');
    }
}

// Create and export singleton instance
window.store = new ReactiveStore();

console.log('✅ ReactiveStore initialized with memoized selectors');
