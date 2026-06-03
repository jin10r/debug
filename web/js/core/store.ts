/**
 * Reactive store — single source of truth for events and filters.
 *
 * Built on zustand/vanilla. The store owns the master event collection as a
 * Map<id, EventFeature>; localStorage (local_cache.ts) is only a persistence
 * adapter, and WebSocket pushes events straight into the store. This removes
 * the old aliasing between localCache.masterGeoJSON and the store state.
 *
 * Change detection: every data mutation bumps `revision`; a 30 s tick bumps
 * `clockTick` (and prunes expired events). Subscribers re-render on any change
 * — the incremental map renderer diffs what actually changed.
 */

import { createStore } from 'zustand/vanilla';
import { EventFeature, EventFeatureCollection, EventLayer } from '../types/geojson';

/** Event time-to-live: 60 minutes from the event's own timestamp. */
const TTL_MS = 60 * 60 * 1000;
/** Tolerance for events slightly ahead of the server clock. */
const FUTURE_TOLERANCE_MS = 5 * 60 * 1000;
/** TTL prune / clock re-evaluation interval. */
const TICK_INTERVAL_MS = 30 * 1000;

export type TimeFilter = 15 | 30 | 60;

const DEFAULT_LAYERS: EventLayer[] = ['pig', 'cops', 'bus', 'traffic'];

/** Store state and actions. */
export interface SurvivalState {
    /** Master events keyed by id — O(1) upsert/dedup. */
    eventsById: Map<string | number, EventFeature>;
    /** Active view window in minutes. */
    currentTimeFilter: TimeFilter;
    /** Enabled event layers. */
    activeLayers: Set<EventLayer>;
    /** Monotonic counter — bumped on every data mutation. */
    revision: number;
    /** Monotonic counter — bumped every tick so time-based filtering re-runs. */
    clockTick: number;

    // ---- actions ----
    /** Replace all events (e.g. hydrate from localStorage). */
    setEvents: (features: EventFeature[]) => void;
    /** Upsert a single event; returns true if it was new. */
    addEvent: (feature: EventFeature) => boolean;
    /** Upsert many events at once; returns the count of newly added ids. */
    addEvents: (features: EventFeature[]) => number;
    /** Change the time-filter window. */
    updateTimeFilter: (minutes: TimeFilter) => void;
    /** Toggle a layer on/off. */
    toggleLayer: (layer: EventLayer) => void;
    /** Drop events past their TTL; returns the count removed. */
    pruneExpired: () => number;
    /** Remove all events. */
    clearEvents: () => void;
    /** Advance the clock tick (re-evaluates time-based filtering). */
    tickClock: () => void;

    // ---- selectors ----
    /** Events passing the current layer + time filter, as a FeatureCollection. */
    getFilteredItems: () => EventFeatureCollection;
    /** Every stored event as a FeatureCollection (for persistence). */
    getAllEvents: () => EventFeatureCollection;
    /** ISO-8601 timestamp of the newest event, or null when empty. */
    getLatestTimestamp: () => string | null;
}

/** Extract a stable id from a feature. */
export function getEventId(feature: EventFeature): string | number | null {
    const p = feature?.properties;
    if (!p) return null;
    const id = p.id ?? p.event_id ?? p._id ?? p.uid ?? null;
    return id as string | number | null;
}

/** Parse the event time from a feature. */
export function getEventTime(feature: EventFeature): Date | null {
    const p = feature?.properties;
    if (!p) return null;
    const raw = p.time ?? p.created_at ?? p.timestamp;
    if (!raw) return null;
    const d = new Date(raw as string | number);
    return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * An event is acceptable if it has no timestamp, or its age is within
 * [-FUTURE_TOLERANCE_MS, TTL_MS] relative to the server (Kiev) clock.
 */
function isAcceptable(feature: EventFeature): boolean {
    const t = getEventTime(feature);
    if (!t) return true;
    const age = window.serverNow() - t.getTime();
    return age <= TTL_MS && age >= -FUTURE_TOLERANCE_MS;
}

/** Memoized filtered-items result, keyed by every input that affects it. */
let filterMemo: { key: string; value: EventFeatureCollection } | null = null;

function computeFiltered(state: SurvivalState): EventFeatureCollection {
    const key = `${state.revision}|${state.currentTimeFilter}|`
        + `${[...state.activeLayers].sort().join(',')}|${state.clockTick}`;

    if (filterMemo && filterMemo.key === key) {
        return filterMemo.value;
    }

    const filterMs = state.currentTimeFilter * 60 * 1000;
    const now = window.serverNow();
    const features: EventFeature[] = [];

    for (const feature of state.eventsById.values()) {
        const layer = (feature.properties?.layer
            || feature.properties?.type
            || 'unknown') as EventLayer;
        if (!state.activeLayers.has(layer)) continue;

        const t = getEventTime(feature);
        if (t) {
            const age = now - t.getTime();
            if (age < -FUTURE_TOLERANCE_MS) continue;
            if (age > filterMs) continue;
        }
        features.push(feature);
    }

    const value: EventFeatureCollection = { type: 'FeatureCollection', features };
    filterMemo = { key, value };
    return value;
}

export const store = createStore<SurvivalState>()((set, get) => ({
    eventsById: new Map(),
    currentTimeFilter: 30,
    activeLayers: new Set<EventLayer>(DEFAULT_LAYERS),
    revision: 0,
    clockTick: 0,

    setEvents: (features) => {
        const next = new Map<string | number, EventFeature>();
        for (const f of features) {
            const id = getEventId(f);
            if (id == null || !isAcceptable(f)) continue;
            next.set(id, f);
        }
        set({ eventsById: next, revision: get().revision + 1 });
        console.log('[Store] setEvents:', next.size, 'events');
    },

    addEvent: (feature) => {
        const id = getEventId(feature);
        if (id == null || !isAcceptable(feature)) return false;
        const state = get();
        const isNew = !state.eventsById.has(id);
        const next = new Map(state.eventsById);
        next.set(id, feature);
        set({ eventsById: next, revision: state.revision + 1 });
        return isNew;
    },

    addEvents: (features) => {
        if (!features || features.length === 0) return 0;
        const state = get();
        const next = new Map(state.eventsById);
        let added = 0;
        let changed = false;
        for (const f of features) {
            const id = getEventId(f);
            if (id == null || !isAcceptable(f)) continue;
            if (!next.has(id)) {
                added++;
                changed = true;
            } else if (next.get(id) !== f) {
                changed = true;
            }
            next.set(id, f);
        }
        if (changed) {
            set({ eventsById: next, revision: state.revision + 1 });
        }
        console.log('[Store] addEvents:', added, 'new of', features.length);
        return added;
    },

    updateTimeFilter: (minutes) => {
        const state = get();
        if (state.currentTimeFilter === minutes) return;
        set({ currentTimeFilter: minutes, revision: state.revision + 1 });
        console.log('[Store] timeFilter:', minutes, 'min');
    },

    toggleLayer: (layer) => {
        const state = get();
        const next = new Set(state.activeLayers);
        if (next.has(layer)) next.delete(layer);
        else next.add(layer);
        set({ activeLayers: next, revision: state.revision + 1 });
        console.log('[Store] toggleLayer:', layer, '→', [...next].join(','));
    },

    pruneExpired: () => {
        const state = get();
        const next = new Map<string | number, EventFeature>();
        let removed = 0;
        for (const [id, f] of state.eventsById) {
            if (isAcceptable(f)) next.set(id, f);
            else removed++;
        }

        // Hard cap: memory safety guard. Если TTL-prune не успевает за
        // потоком (WebSocket reconnect повторно отдаёт snapshot, тонна
        // событий за раз), Map может вырасти неконтролируемо. На 5000
        // событиях принудительно убираем 10% самых старых (по времени).
        const HARD_MAX = 5000;
        if (next.size > HARD_MAX) {
            const overflow = next.size - Math.floor(HARD_MAX * 0.9);
            const sorted = [...next.entries()].sort((a, b) => {
                const ta = (a[1].properties as { time?: string })?.time ?? '';
                const tb = (b[1].properties as { time?: string })?.time ?? '';
                return ta.localeCompare(tb);
            });
            for (let i = 0; i < overflow && i < sorted.length; i++) {
                next.delete(sorted[i][0]);
                removed++;
            }
            console.warn(`[Store] hard cap kicked in: removed ${overflow} oldest events`);
        }

        if (removed > 0) {
            set({ eventsById: next, revision: state.revision + 1 });
            console.log('[Store] pruneExpired:', removed, 'expired');
        }
        return removed;
    },

    clearEvents: () => {
        set({ eventsById: new Map(), revision: get().revision + 1 });
    },

    tickClock: () => {
        set({ clockTick: get().clockTick + 1 });
    },

    getFilteredItems: () => computeFiltered(get()),

    getAllEvents: () => ({
        type: 'FeatureCollection',
        features: [...get().eventsById.values()]
    }),

    getLatestTimestamp: () => {
        let max: Date | null = null;
        for (const f of get().eventsById.values()) {
            const t = getEventTime(f);
            if (t && (!max || t > max)) max = t;
        }
        return max ? max.toISOString() : null;
    }
}));

// Periodic tick: prune expired events and advance the filtering clock so the
// 15/30/60-min window and the 60-min TTL stay correct without new events.
window.setInterval(() => {
    const s = store.getState();
    s.pruneExpired();
    s.tickClock();
}, TICK_INTERVAL_MS);

// Expose the zustand store globally for the non-module scripts.
window.store = store as unknown as Window['store'];

console.log('✅ ReactiveStore (zustand) initialized');
