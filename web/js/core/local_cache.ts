/**
 * LocalCache — localStorage persistence adapter for the event store.
 *
 * The reactive store (store.ts) is the single source of truth. This module
 * only bridges it to localStorage:
 *   - on boot: hydrate the store from the last persisted snapshot, so the map
 *     renders offline before any WebSocket connects;
 *   - afterwards: persist the store to localStorage (debounced) on every change.
 *
 * It holds no event state of its own — that removes the old aliasing between
 * localCache.masterGeoJSON and the store.
 */

import { StorageAdapter } from './storage';
import { EventFeatureCollection } from '../types/geojson';

/** localStorage key for the persisted event snapshot. */
const CACHE_KEY = 'events_geojson';
/** Debounce window for writes — store changes can arrive in bursts. */
const SAVE_DEBOUNCE_MS = 1000;

export class LocalCache {
    private storage: StorageAdapter;
    private saveTimer: number | null = null;
    private unsubscribe: (() => void) | null = null;

    constructor(storage?: StorageAdapter) {
        this.storage = storage || new StorageAdapter();
    }

    /**
     * Hydrate the store from the last persisted snapshot.
     * Offline-first: the map can render from cache before the WebSocket opens.
     */
    async loadEvents(): Promise<void> {
        try {
            const data = await this.storage.getItemJSON<EventFeatureCollection>(CACHE_KEY);
            if (this.isValidGeoJSON(data) && data.features.length > 0) {
                window.store.getState().setEvents(data.features);
                console.log('[LocalCache] Hydrated store from cache:', data.features.length, 'events');
            } else {
                console.log('[LocalCache] No valid cached events');
            }
        } catch (error) {
            console.error('[LocalCache] loadEvents error:', error);
        }
    }

    /** Start persisting the store to localStorage on every change (debounced). */
    startPersisting(): void {
        if (this.unsubscribe) return;
        this.unsubscribe = window.store.subscribe(() => this.scheduleSave());
        console.log('[LocalCache] Persistence subscription active');
    }

    /** Stop persisting (cleanup). */
    stopPersisting(): void {
        if (this.unsubscribe) {
            this.unsubscribe();
            this.unsubscribe = null;
        }
        if (this.saveTimer !== null) {
            window.clearTimeout(this.saveTimer);
            this.saveTimer = null;
        }
    }

    private scheduleSave(): void {
        if (this.saveTimer !== null) return;
        this.saveTimer = window.setTimeout(() => {
            this.saveTimer = null;
            void this.save();
        }, SAVE_DEBOUNCE_MS);
    }

    private async save(): Promise<void> {
        try {
            const collection = window.store.getState().getAllEvents();
            await this.storage.setItemJSON(CACHE_KEY, collection);
            console.log('[LocalCache] Persisted', collection.features.length, 'events');
        } catch (error) {
            console.error('[LocalCache] save error:', error);
        }
    }

    private isValidGeoJSON(data: unknown): data is EventFeatureCollection {
        return !!data
            && typeof data === 'object'
            && (data as EventFeatureCollection).type === 'FeatureCollection'
            && Array.isArray((data as EventFeatureCollection).features);
    }
}

// Singleton — store.js has already run, so window.store exists.
const localCache = new LocalCache();
window.localCache = localCache as unknown as Window['localCache'];

// Hydrate from cache first, then persist subsequent store changes.
localCache.loadEvents().then(() => localCache.startPersisting());

console.log('✅ LocalCache initialized (localStorage persistence adapter)');
