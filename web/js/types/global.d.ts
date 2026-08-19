/**
 * Global Type Declarations for Survival Map Application
 * 
 * This file extends the Window interface with our application's globals
 */

import { EventFeature, EventFeatureCollection, AppConfig, StoreState } from './geojson';
import { TelegramAPI } from './telegram';

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
            invalidate(): Promise<void>;
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
        // Общий тип вынесен в js/types/telegram.ts (см. TelegramAPI)
        Telegram: TelegramAPI;

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

        // ==================== MAP INSTANCES ====================
        
        currentMapInstance: L.Map | null;
        markerClusterGroup: L.MarkerClusterGroup | null;
        geometryLayerGroup: L.LayerGroup | null;
        randomMarkersGroup: L.LayerGroup | null;

        // ==================== GEOMETRY HELPERS (js/core/map.ts) ====================

        createIcon: (layer: string) => L.Icon | L.DivIcon;
        createMarker: (map: L.Map, latLng: L.LatLng, properties: Record<string, unknown>) => L.Marker;
        createCircle: (map: L.Map, coords: number[], properties: Record<string, unknown>, strategy?: string) => L.Layer[];
        getPolylineMidpoint: (latLngs: L.LatLng[]) => L.LatLng | null;
        createPolyline: (map: L.Map, coords: [number, number][], properties: Record<string, unknown>) => L.Layer[];
        createPolygon: (map: L.Map, coords: [number, number][][], properties: Record<string, unknown>) => L.Layer[];
        createMultiPolygon: (map: L.Map, coords: [number, number][][][], properties: Record<string, unknown>) => L.Layer[];
        createMultiLineString: (map: L.Map, coords: number[][][], properties: Record<string, unknown>) => L.Layer[];
        createMultiPoint: (map: L.Map, coords: number[][], properties: Record<string, unknown>) => L.Layer[];
        // coords: any[] — элементы GeometryCollection разнородны (Point/
        // LineString/...), каждая ветка кастует свои coordinates самостоятельно.
        createGeometryCollection: (map: L.Map, coords: any[], properties: Record<string, unknown>) => L.Layer[];
        createTelegramPopup: (content: string, customOptions?: Record<string, unknown>) => L.Popup;
        adSquares: Record<string, unknown>;
        switchTileLayer: (tileKey: string) => void;

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
        // type: string — реальные вызовы передают произвольные строки
        // (например HAPTIC_BY_TYPE[type] в common.ts), строгий union здесь
        // приводил к TS2345.
        hapticFeedback: (type?: string) => void;
        showNotification: (message: string, duration?: number, type?: 'info' | 'warning' | 'error' | 'success', iconSrc?: string) => void;
        playNotificationSound: () => boolean;
        handleNewEvents: (events: any[]) => void;
        renderFromCache: () => void;
        initializeMap: () => void;
        
        // ==================== TELEGRAM INTEGRATION (js/telegram/integration.ts) ====================

        __hapticDebugOnce?: boolean;
        telegramIntegration: {
            init(): boolean;
            hapticFeedback(type?: string): boolean;
            showPopup(message: string, buttons?: Array<{ type: string }>): Promise<string>;
            showAlert(message: string): Promise<void>;
            showConfirm(message: string): Promise<boolean>;
        };

        // ==================== TOKEN MANAGER (js/core/token-manager.ts) ====================

        tokenManager: {
            init(): Promise<boolean>;
            getAccessToken(): string | null;
            getRefreshToken(): string | null;
            getValidToken(): Promise<string | null>;
            storeTokens(accessToken: string, refreshToken?: string): void;
            clearTokens(): void;
            isTokenExpired(token: string, thresholdMs?: number): boolean;
            getTokenExpiration(token: string): Date | null;
            refreshAccessToken(): Promise<string | null>;
            scheduleRefresh(): void;
            destroy(): void;
        };

        // ==================== DEBUG ====================
        
        debugManager?: {
            getStats(): Record<string, unknown>;
            printReport(): void;
        };
    }
}

// Export for module usage
export {};
