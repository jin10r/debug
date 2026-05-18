/**
 * EventManager — drives the incremental map render reactively.
 *
 * A single subscription to the store: any store change (new event, time
 * filter, layer toggle, TTL prune, clock tick) schedules one incremental
 * renderFromCache() on the next animation frame. The diff-renderer in ui.js
 * decides what actually changed — there is no count-based gating and no
 * timing-based forced render.
 */

/** Public surface kept for compatibility with callers of render(). */
export interface EventManager {
    render: () => void;
}

/** Coalesce bursts of store changes into one render per animation frame. */
let rafPending = false;

function scheduleRender(): void {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => {
        rafPending = false;
        if (typeof window.renderDataOnMap === 'function') {
            window.renderDataOnMap();
        }
    });
}

function initializeEventManager(): EventManager {
    if (window.store && typeof window.store.subscribe === 'function') {
        // The ONLY store subscription that drives rendering.
        window.store.subscribe(() => scheduleRender());
        console.log('[EventManager] Subscribed to store — reactive incremental rendering');
    } else {
        console.error('[EventManager] window.store not available');
    }

    return { render: scheduleRender };
}

window.eventManager = initializeEventManager();

console.log('✅ EventManager initialized (reactive incremental rendering)');
