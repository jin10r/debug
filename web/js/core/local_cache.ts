/**
 * LocalCache - Local storage for GeoJSON events with TTL
 * 
 * CORRECTED LOGIC:
 * - localStorage is ONLY for offline display during connection loss
 * - Store is populated ONLY via WebSocket (not from localStorage)
 * - On reconnect, WebSocket sends fresh data from backend
 * 
 * @example
 * ```typescript
 * const cache = new LocalCache(storageAdapter);
 * await cache.loadFromCache(); // For offline display only
 * cache.addEvent(event);       // From WebSocket
 * ```
 */

import { StorageAdapter } from './storage';
import { EventFeature, EventFeatureCollection } from '../types/geojson';

/**
 * LocalCache class
 */
export class LocalCache {
    /** Maximum event lifetime: 60 minutes */
    public readonly TTL_MS = 60 * 60 * 1000;
    
    /** Master GeoJSON collection */
    public masterGeoJSON: EventFeatureCollection;
    
    /** Fast lookup map by event ID */
    public eventsById: Map<string | number, EventFeature>;
    
    /** Cleanup timer interval */
    private cleanupTimer: number | null = null;
    
    /** Storage adapter instance */
    private storage: StorageAdapter;
    
    /** Cache key for localStorage */
    private readonly CACHE_KEY = 'events_geojson';
    
    /** Track if WebSocket has sent initial data */
    private hasReceivedWebSocketData = false;

    /**
     * Create LocalCache instance
     * @param storage - Storage adapter for persistence
     */
    constructor(storage?: StorageAdapter) {
        this.storage = storage || new StorageAdapter();
        
        this.masterGeoJSON = {
            type: 'FeatureCollection',
            features: []
        };
        
        this.eventsById = new Map();
        
        this.startCleanupTimer();
        this.loadFromCache();
        
        console.log('[LocalCache] Initialized with TTL:', this.TTL_MS / 1000 / 60, 'minutes');
    }

    /**
     * Load events from localStorage
     * 
     * NOTE: This only loads into localCache for offline display.
     * It does NOT sync with store - store is populated only via WebSocket.
     */
    async loadFromCache(): Promise<void> {
        try {
            console.log('[LocalCache] Loading from localStorage (offline cache)...');
            
            const cachedData = await this.storage.getItemJSON<EventFeatureCollection>(this.CACHE_KEY);
            
            if (this.isValidGeoJSON(cachedData)) {
                this.masterGeoJSON = cachedData!;
                this.rebuildEventsByIdMap();

                // Remove expired events before showing
                const cleaned = this.cleanupExpiredEvents();
                console.log(`[LocalCache] Loaded ${this.masterGeoJSON.features.length} events from localStorage, cleaned ${cleaned} expired`);

                // Sync to store immediately so the map renders from localStorage while offline.
                // WebSocket will merge fresh features on top via addEvent() once connected.
                if (this.masterGeoJSON.features.length > 0) {
                    this.syncWithStore();
                    console.log('[LocalCache] Offline cache synced to store for immediate display');
                }
            } else {
                console.log('[LocalCache] No valid cache found, initializing empty');
                this.clear();
            }
        } catch (error) {
            console.error('[LocalCache] Error loading from cache:', error);
            this.clear();
        }
    }

    /**
     * Validate GeoJSON structure
     */
    private isValidGeoJSON(data: unknown): data is EventFeatureCollection {
        return (
            data !== null &&
            typeof data === 'object' &&
            'type' in data &&
            (data as EventFeatureCollection).type === 'FeatureCollection' &&
            'features' in data &&
            Array.isArray((data as EventFeatureCollection).features)
        );
    }

    /**
     * Rebuild eventsById map from masterGeoJSON
     */
    private rebuildEventsByIdMap(): void {
        this.eventsById.clear();
        
        for (const feature of this.masterGeoJSON.features) {
            const eventId = this.getEventId(feature);
            if (eventId) {
                this.eventsById.set(eventId, feature);
            }
        }
        
        console.log('[LocalCache] Rebuilt eventsById map:', this.eventsById.size, 'events');
    }

    /**
     * Add single event to cache
     * 
     * @param event - GeoJSON feature to add
     * @returns true if new event was added, false if updated or rejected
     */
    addEvent(event: EventFeature): boolean {
        const eventId = this.getEventId(event);
        
        if (!eventId) {
            console.warn('[LocalCache] Event without ID, rejecting:', event);
            return false;
        }

        // Check TTL
        const eventTime = this.getEventTime(event);
        if (eventTime) {
            const eventAge = Date.now() - eventTime.getTime();

            // Reject events older than 60 minutes OR future events (more than 5 minutes ahead)
            if (eventAge > this.TTL_MS || eventAge < -5 * 60 * 1000) {
                console.log('[LocalCache] Event rejected due to age:', eventAge, 'ms');
                return false;
            }
        }

        const isNewEvent = !this.eventsById.has(eventId);

        if (this.eventsById.has(eventId)) {
            // Update existing event
            const existingIndex = this.masterGeoJSON.features.findIndex(
                (f: EventFeature) => this.getEventId(f) === eventId
            );
            
            if (existingIndex !== -1) {
                this.masterGeoJSON.features[existingIndex] = event;
                console.log('[LocalCache] Updated event:', eventId);
            }
        } else {
            // Add new event
            this.masterGeoJSON.features.push(event);
            this.eventsById.set(eventId, event);
            console.log('[LocalCache] Added new event:', eventId);
        }

        // Mark that we've received WebSocket data
        this.hasReceivedWebSocketData = true;

        // Sync with store for reactive rendering
        if (isNewEvent) {
            this.syncWithStore();
            
            // Notify for new event
            setTimeout(() => {
                window.eventManager?.notify?.([event]);
            }, 0);
        }

        // Async save to localStorage
        setTimeout(() => this.saveToCache(), 0);

        return isNewEvent;
    }

    /**
     * Add multiple events to cache
     * 
     * @param events - Array of GeoJSON features
     * @param suppressNotifications - Suppress notifications flag
     * @returns Number of events added
     */
    addEvents(events: EventFeature[], suppressNotifications = false): number {
        let addedCount = 0;
        const newEvents: EventFeature[] = [];

        for (const event of events) {
            const eventId = this.getEventId(event);
            const isNewEvent = eventId && !this.eventsById.has(eventId);

            if (this.addEvent(event)) {
                addedCount++;
                if (isNewEvent) {
                    newEvents.push(event);
                }
            }
        }

        // Mark that we've received WebSocket data
        if (addedCount > 0) {
            this.hasReceivedWebSocketData = true;
        }

        // Notify for new events
        if (newEvents.length > 0 && !suppressNotifications) {
            setTimeout(() => {
                window.eventManager?.notify?.(newEvents);
            }, 0);
        }

        // Async save
        setTimeout(() => this.saveToCache(), 0);

        return addedCount;
    }

    /**
     * Replace all events in cache (e.g., after WebSocket reconnect)
     * 
     * @param events - Array of GeoJSON features
     * @param suppressNotifications - Suppress notifications flag
     * @returns Number of events replaced
     */
    replaceAllEvents(events: EventFeature[], suppressNotifications = false): number {
        console.log('[LocalCache] replaceAllEvents: replacing with', events.length, 'events from WebSocket');
        
        const oldEventIds = new Set(this.eventsById.keys());
        
        // Clear current data
        this.clear();

        // Add new events
        const newEvents: EventFeature[] = [];
        
        for (const event of events) {
            const eventId = this.getEventId(event);
            if (eventId) {
                this.masterGeoJSON.features.push(event);
                this.eventsById.set(eventId, event);

                if (!oldEventIds.has(eventId)) {
                    newEvents.push(event);
                }
            }
        }

        // Clean up expired
        this.cleanupExpiredEvents();

        // Mark that we've received WebSocket data
        this.hasReceivedWebSocketData = true;

        // Sync with store - THIS IS THE PRIMARY WAY STORE GETS POPULATED
        this.syncWithStore();

        // Notify for new events
        if (newEvents.length > 0 && !suppressNotifications) {
            setTimeout(() => {
                window.eventManager?.notify?.(newEvents);
            }, 0);
        }

        // Async save
        setTimeout(() => this.saveToCache(), 0);

        return this.masterGeoJSON.features.length;
    }

    /**
     * Get all events from cache
     */
    getAllEvents(): EventFeatureCollection {
        return this.masterGeoJSON;
    }

    /**
     * Get events filtered by time
     * 
     * @param timeFilterMinutes - Time filter in minutes
     */
    getEventsByTimeFilter(timeFilterMinutes: number): EventFeatureCollection {
        const filterMs = timeFilterMinutes * 60 * 1000;
        const now = Date.now();

        const filteredFeatures = this.masterGeoJSON.features.filter((feature: EventFeature) => {
            const eventTime = this.getEventTime(feature);
            if (!eventTime) return true; // No time, include event

            const ageMs = now - eventTime.getTime();
            return ageMs >= 0 && ageMs <= filterMs;
        });

        return {
            type: 'FeatureCollection',
            features: filteredFeatures
        };
    }

    /**
     * Get event ID from feature
     */
    getEventId(event: EventFeature): string | number | null {
        if (!event?.properties) return null;

        const id =
            event.properties.id ??
            event.properties.event_id ??
            event.properties._id ??
            event.properties.uid ??
            null;

        return id as string | number | null;
    }

    /**
     * Get event time from feature
     */
    getEventTime(event: EventFeature): Date | null {
        if (!event?.properties) return null;

        const timeStr = event.properties.time ?? event.properties.created_at ?? event.properties.timestamp;
        if (!timeStr) return null;

        try {
            return new Date(timeStr);
        } catch (e) {
            console.error('[LocalCache] Error parsing event time:', timeStr, e);
            return null;
        }
    }

    /**
     * Get maximum event ID
     */
    getMaxEventId(): number {
        let maxId = 0;
        
        for (const feature of this.masterGeoJSON.features) {
            const eventId = this.getEventId(feature);
            if (eventId && typeof eventId === 'number' && eventId > maxId) {
                maxId = eventId;
            }
        }
        
        return maxId;
    }

    /**
     * Get the ISO-8601 timestamp of the newest event in the cache.
     * Used by WebSocketManager to request only missed events on reconnect.
     * Returns null when the cache is empty (triggers a full initial load).
     */
    getLatestTimestamp(): string | null {
        let maxTime: Date | null = null;

        for (const feature of this.masterGeoJSON.features) {
            const t = this.getEventTime(feature);
            if (t && (!maxTime || t > maxTime)) {
                maxTime = t;
            }
        }

        return maxTime ? maxTime.toISOString() : null;
    }

    /**
     * Get maximum event time
     */
    getMaxEventTime(): Date | null {
        let maxTime: Date | null = null;
        
        for (const feature of this.masterGeoJSON.features) {
            const eventTime = this.getEventTime(feature);
            if (eventTime && (!maxTime || eventTime > maxTime)) {
                maxTime = eventTime;
            }
        }
        
        return maxTime;
    }

    /**
     * Clean up expired events
     * 
     * @returns Number of events removed
     */
    cleanupExpiredEvents(): number {
        const now = Date.now();
        const validFeatures: EventFeature[] = [];
        const removedIds: Array<string | number> = [];

        for (const feature of this.masterGeoJSON.features) {
            const eventTime = this.getEventTime(feature);
            const eventAge = eventTime ? now - eventTime.getTime() : 0;

            // Keep events without time or not expired
            if (!eventTime || (eventAge >= 0 && eventAge <= this.TTL_MS)) {
                const eventId = this.getEventId(feature);
                if (eventId) {
                    validFeatures.push(feature);
                }
            } else {
                // Remove expired event from map
                const eventId = this.getEventId(feature);
                if (eventId) {
                    this.eventsById.delete(eventId);
                    removedIds.push(eventId);
                }
            }
        }

        this.masterGeoJSON.features = validFeatures;

        if (removedIds.length > 0) {
            console.log(`[LocalCache] Cleaned up ${removedIds.length} expired events`);
        }

        return removedIds.length;
    }

    /**
     * Save events to localStorage
     */
    private async saveToCache(): Promise<void> {
        try {
            // Clean up before saving
            this.cleanupExpiredEvents();
            
            await this.storage.setItemJSON(this.CACHE_KEY, this.masterGeoJSON);
            console.log('[LocalCache] Saved to localStorage:', this.masterGeoJSON.features.length, 'events');
        } catch (error) {
            console.error('[LocalCache] Error saving to cache:', error);
        }
    }

    /**
     * Sync with reactive store
     * THIS IS THE ONLY WAY STORE GETS UPDATED (from WebSocket data)
     */
    private syncWithStore(): void {
        console.log('[LocalCache] syncWithStore: syncing', this.masterGeoJSON.features.length, 'events to store');
        
        if (typeof window.updateEventsInStore === 'function') {
            console.log('[LocalCache] Calling updateEventsInStore...');
            window.updateEventsInStore(this.masterGeoJSON);
        } else {
            console.warn('[LocalCache] updateEventsInStore not available');
        }
    }

    /**
     * Start cleanup timer
     */
    private startCleanupTimer(): void {
        if (this.cleanupTimer) {
            clearInterval(this.cleanupTimer);
        }

        // Run cleanup every 5 minutes
        this.cleanupTimer = window.setInterval(() => {
            this.cleanupExpiredEvents();
        }, 5 * 60 * 1000);
        
        console.log('[LocalCache] Started cleanup timer (5 minutes)');
    }

    /**
     * Stop cleanup timer
     */
    stopCleanupTimer(): void {
        if (this.cleanupTimer) {
            clearInterval(this.cleanupTimer);
            this.cleanupTimer = null;
            console.log('[LocalCache] Stopped cleanup timer');
        }
    }

    /**
     * Clear all data from cache
     */
    clear(): void {
        this.masterGeoJSON = {
            type: 'FeatureCollection',
            features: []
        };
        this.eventsById.clear();
        
        // DO NOT save empty to localStorage - keep old data for offline display
        // this.saveToCache();
        
        console.log('[LocalCache] Cleared all data from memory (localStorage preserved)');
    }

    /**
     * Get cache statistics
     */
    getStats(): { totalEvents: number; eventsById: number; ttlMinutes: number; hasWebSocketData: boolean } {
        return {
            totalEvents: this.masterGeoJSON.features.length,
            eventsById: this.eventsById.size,
            ttlMinutes: this.TTL_MS / 1000 / 60,
            hasWebSocketData: this.hasReceivedWebSocketData
        };
    }
}

// Create and export singleton instance
window.localCache = new LocalCache();

console.log('✅ LocalCache initialized (localStorage for offline only, store via WebSocket)');
