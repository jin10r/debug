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

    /**
     * Telegram message ID — стабильный идентификатор между рестартами БД:
     * события пере-вставляются с теми же message_id, тогда как id события
     * перезапускается. Первичный ключ dedup и водяной знак catch-up.
     */
    message_id?: string | number;

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
        /** Lemmatized matched surface (e.g. "нерубайской") used for popup highlighting */
        matched_text?: string;
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


