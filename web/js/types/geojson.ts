/**
 * GeoJSON Types for Survival Map Application
 * 
 * Based on RFC 7946 GeoJSON specification
 * @see https://tools.ietf.org/html/rfc7946
 */

import {
    Feature,
    FeatureCollection,
    Point,
    LineString,
    Polygon
} from 'geojson';

/**
 * Event layer types
 */
export type EventLayer = 'pig' | 'cops' | 'bus' | 'traffic' | 'unknown';

/**
 * Event strategy types
 * - 'exact': Exact location (shows circle + marker)
 * - 'random': Random location in area (shows marker only)
 */
export type EventStrategy = 'exact' | 'random' | 'unknown';

/**
 * Event properties interface
 */
export interface EventProperties {
    /** Unique event ID */
    id: string | number;
    
    /** Event description */
    description: string;
    
    /** Event layer type */
    layer: EventLayer;
    
    /** Location strategy */
    strategy: EventStrategy;
    
    /** Photo URL (optional) */
    photo_url?: string;
    
    /** Event time (ISO 8601 format) */
    time?: string;
    
    /** Created at timestamp (ISO 8601 format) */
    created_at?: string;
    
    /** Unix timestamp in milliseconds */
    timestamp?: number;
    
    /** AI matches for classification */
    matches?: Array<{
        type: string;
        confidence: number;
        [key: string]: unknown;
    }>;
    
    /** Additional properties (flexible) */
    [key: string]: unknown;
}

/**
 * Event Feature type
 */
export type EventFeature = Feature<Point | LineString | Polygon, EventProperties>;

/**
 * Event Feature Collection type
 */
export type EventFeatureCollection = FeatureCollection<Point | LineString | Polygon, EventProperties>;

/**
 * Cache entry interface for localStorage
 */
export interface CacheEntry {
    /** The GeoJSON feature */
    feature: EventFeature;
    
    /** When the event was received (Unix timestamp ms) */
    receivedAt: number;
    
    /** When the event expires (Unix timestamp ms) */
    expiresAt: number;
}

/**
 * Store state interface
 */
export interface StoreState {
    /** All events */
    events: EventFeatureCollection;

    /** Current time filter in minutes (15, 30, or 60) */
    currentTimeFilter: 15 | 30 | 60;

    /** Active layer filters */
    activeLayers: Set<EventLayer>;
}

/**
 * WebSocket message types
 */
export type WebSocketMessageType =
    | 'feature'
    | 'pong'
    | 'events_cleaned';

/**
 * WebSocket message interface
 */
export interface WebSocketMessage {
    /** Message type */
    type: string;

    /** Single GeoJSON feature (for 'feature' messages) */
    data?: EventFeature | EventFeatureCollection;

    /** Since timestamp for catch-up response */
    since?: string;
}

/**
 * App configuration interface
 */
export interface AppConfig {
    /** Map center latitude */
    map_center_lat: number;
    
    /** Map center longitude */
    map_center_lng: number;
    
    /** Default map zoom level */
    map_default_zoom: number;
    
    /** Enable random points fallback */
    enable_random_points: boolean;
    
    /** Validation redirect URL */
    validation_redirect_url: string;
    
    /** Stopwords for text processing */
    stopwords?: string[];
    
    /** Layer keywords for classification */
    layer_keywords?: {
        pig: string[];
        cops: string[];
        bus: string[];
    };
    
    /** Additional config properties */
    [key: string]: unknown;
}

/**
 * Storage adapter interface
 */
export interface AsyncStorage {
    getItem(key: string): Promise<string | null>;
    setItem(key: string, value: string): Promise<void>;
    removeItem(key: string): Promise<void>;
    getKeys(): Promise<string[]>;
}

/**
 * Event tracker interface
 */
export interface EventTracker {
    checkForNewEvents(events: Array<{ id: string | number }>): Array<{ id: string | number }>;
}

/**
 * Global window interface extension
 */
declare global {
    interface Window {
        // App config
        APP_CONFIG: AppConfig;
        
        // App state
        APP_STATE: {
            currentTimeFilter: number;
            activeLayers: Set<string>;
            events: EventFeatureCollection;
        };
        
        // Core modules — see js/core/store.ts and js/core/local_cache.ts
        localCache: {
            loadEvents(): Promise<void>;
            startPersisting(): void;
            stopPersisting(): void;
        };

        store: {
            getState(): any;
            setState(partial: any): void;
            subscribe(listener: () => void): () => void;
            getInitialState(): any;
        };
        
        webSocketManager: {
            connect(): void;
            disconnect(): void;
            sendMessage(message: Record<string, unknown>): void;
            onFeature: ((feature: EventFeature) => void) | null;
            onSnapshot: ((features: EventFeature[]) => void) | null;
            onConnectionStatusChange: ((isConnected: boolean) => void) | null;
            isConnected: boolean;
        };
        
        eventManager: {
            render(): void;
        };
        
        // Telegram
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
        
        // Validator
        telegramValidator: {
            validateAndInit(): Promise<boolean>;
            getUserId(): number | null;
            getUserName(): string | null;
            isValid(): boolean;
            getTelegram(): unknown;
        };
        
        // Utility functions
        updateEventsInStore: (events: EventFeatureCollection) => void;
        getFilteredDataForRendering: () => EventFeatureCollection;
        updateOnlineStatus: (isOnline: boolean) => void;
        renderDataOnMap: () => void;
        initializeWebSocket: () => void;
        bootstrapUI: () => void;
        getAuthHeaders: () => Record<string, string>;
        
        // Event tracking
        eventTracker: EventTracker;
        
        // Map instances
        currentMapInstance: any; // L.Map from leaflet
        markerClusterGroup: any; // L.MarkerClusterGroup
        geometryLayerGroup: any; // L.LayerGroup
        randomMarkersGroup: any; // L.LayerGroup
        
        // Default constants
        DEFAULT_TIME_FILTER: number;
        DEFAULT_POPUP_OPTIONS: Record<string, unknown>;
    }
}

// Export for module usage
export {};
