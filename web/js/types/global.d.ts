/**
 * Global Type Declarations for Survival Map Application
 * 
 * This file extends the Window interface with our application's globals
 */

import { EventFeature, EventFeatureCollection, AppConfig, StoreState } from './geojson';

/**
 * Leaflet type declarations (simplified)
 */
declare namespace L {
    export class Map {
        constructor(element: string | HTMLElement, options?: any);
        setView(latlng: [number, number], zoom: number): this;
        removeLayer(layer: any): this;
        // ... other leaflet methods
    }
    
    export class MarkerClusterGroup {
        constructor(options?: any);
        addLayer(layer: any): this;
        addLayers(layers: any[]): this;
        clearLayers(): this;
    }
    
    export class LayerGroup {
        constructor(layers?: any[]);
        addTo(map: Map): this;
        clearLayers(): this;
        addLayer(layer: any): this;
        removeLayer(layer: any): this;
    }
    
    export function markerClusterGroup(options?: any): MarkerClusterGroup;
    export function layerGroup(layers?: any[]): LayerGroup;
}

/**
 * Extended Window interface
 */
declare global {
    interface Window {
        // ==================== APP CONFIGURATION ====================
        APP_CONFIG: AppConfig;
        
        APP_STATE: {
            currentTimeFilter: number;
            activeLayers: Set<string>;
            events: EventFeatureCollection;
        };
        
        // ==================== CORE MODULES ====================
        
        /** localStorage persistence adapter for the event store */
        localCache: {
            loadEvents(): Promise<void>;
            startPersisting(): void;
            stopPersisting(): void;
        };

        /** Reactive store (zustand vanilla) — see js/core/store.ts */
        store: {
            getState(): any;
            setState(partial: any): void;
            subscribe(listener: () => void): () => void;
            getInitialState(): any;
        };
        
        /** WebSocket manager */
        webSocketManager: {
            connect(): void;
            disconnect(): void;
            sendMessage(message: Record<string, unknown>): void;
            onFeature: ((feature: EventFeature) => void) | null;
            onSnapshot: ((features: EventFeature[]) => void) | null;
            onConnectionStatusChange: ((isConnected: boolean) => void) | null;
            isConnected: boolean;
        };
        
        /** Event manager — store subscription that drives map rendering */
        eventManager: {
            render(): void;
        };
        
        // ==================== TELEGRAM ====================
        
        Telegram: {
            WebApp: {
                initData: string;
                initDataUnsafe: {
                    user?: {
                        id: number;
                        first_name?: string;
                        last_name?: string;
                        username?: string;
                        language_code?: string;
                    };
                };
                version: string;
                platform: string;
                colorScheme: 'light' | 'dark';
                ready: () => void;
                expand: () => void;
                showAlert: (message: string) => void;
                HapticFeedback?: {
                    impactOccurred: (style: 'light' | 'medium' | 'heavy') => void;
                    notificationOccurred: (type: 'success' | 'warning' | 'error') => void;
                    selectionChanged: () => void;
                };
                DeviceStorage?: {
                    getItem: (key: string, callback: (err: string | null, value: string | null) => void) => void;
                    setItem: (key: string, value: string, callback: (err: string | null) => void) => void;
                    removeItem: (key: string, callback: (err: string | null) => void) => void;
                    getKeys: (callback: (err: string | null, keys: string[]) => void) => void;
                };
            };
        };
        
        
        // ==================== UTILITY FUNCTIONS ====================
        
        // ==================== SERVER CLOCK SYNC ====================

        /** Offset in ms between the server (Kiev) clock and the device clock */
        serverClockOffsetMs: number;
        /** Current time anchored to the server (Kiev) clock, immune to device clock/timezone */
        serverNow: () => number;

        updateEventsInStore: (events: EventFeatureCollection) => void;
        getFilteredDataForRendering: () => EventFeatureCollection;
        renderDataOnMap: () => void;
        initializeWebSocket: () => void;
        bootstrapUI: () => void;
        getAuthHeaders: () => Record<string, string>;
        updateOnlineStatus: (isOnline: boolean) => void;
        
        // ==================== EVENT TRACKING ====================
        
        eventTracker: {
            checkForNewEvents(events: Array<{ id: string | number }>): Array<{ id: string | number }>;
        };
        
        // ==================== MAP INSTANCES ====================
        
        currentMapInstance: L.Map | null;
        markerClusterGroup: L.MarkerClusterGroup | null;
        geometryLayerGroup: L.LayerGroup | null;
        randomMarkersGroup: L.LayerGroup | null;

        autoRefreshInterval?: number;
        
        // ==================== CONSTANTS ====================
        
        DEFAULT_TIME_FILTER: number;
        DEFAULT_POPUP_OPTIONS: Record<string, unknown>;
        
        // ==================== HELPER FUNCTIONS ====================
        
        setMapInstance: (map: L.Map) => void;
        updateTimeFilter: (minutes: number) => void;
        toggleLayerInStore: (layer: string) => void;
        createPopupContent: (properties: Record<string, unknown>) => string;
        processTelegramHTML: (html: string) => string;
        formatDateTime: (dateString: string) => string;
        hapticFeedback: (type?: 'light' | 'medium' | 'heavy' | 'success' | 'warning' | 'error' | 'selection_changed') => void;
        showNotification: (message: string, duration?: number, type?: 'info' | 'warning' | 'error' | 'success') => void;
        playNotificationSound: () => boolean;
        handleNewEvents: (events: Array<Record<string, unknown>>) => void;
        renderFromCache: () => void;
        initializeMap: () => void;
        
        // ==================== DEBUG ====================
        
        debugManager?: {
            getStats(): Record<string, unknown>;
            printReport(): void;
        };
    }
}

// Export for module usage
export {};
