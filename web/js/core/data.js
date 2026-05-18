// data.js — thin helpers bridging the UI to the reactive store.
// The store (store.ts) is the single source of truth; these wrappers keep the
// non-module UI scripts decoupled from the zustand API surface.

// Default time-filter window in minutes.
window.DEFAULT_TIME_FILTER = 30;

// Change the time-filter window (15/30/60 min). The store change reactively
// triggers a re-render via the event_manager subscription.
window.updateTimeFilter = function(minutes) {
    window.store.getState().updateTimeFilter(minutes);
};

// Toggle an event layer (pig/cops/bus) on or off.
window.toggleLayerInStore = function(layer) {
    window.store.getState().toggleLayer(layer);
};

// Store the Leaflet map instance globally.
window.setMapInstance = function(map) {
    window.currentMapInstance = map;
};

// Events passing the current layer + time filter, ready for rendering.
window.getFilteredDataForRendering = function() {
    return window.store.getState().getFilteredItems();
};

// Trigger an incremental map render.
window.renderDataOnMap = function() {
    if (typeof window.renderFromCache === 'function') {
        window.renderFromCache();
    }
};

console.log('✅ data.js initialized (store helpers)');
