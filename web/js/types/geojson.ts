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
import type { SurvivalState } from '../core/store';

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

        // Server clock sync (common.ts)
        serverClockOffsetMs: number;
        serverNow: () => number;

        // Core modules
        localCache: {
            loadEvents(): Promise<void>;
            startPersisting(): void;
            stopPersisting(): void;
        };

        store: {
            getState(): SurvivalState;
            setState(partial: Partial<SurvivalState>): void;
            subscribe(listener: () => void): () => void;
            getInitialState(): SurvivalState;
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
                showAlert: (message: string, callback?: () => void) => void;
                showPopup?: (options: { message: string; buttons: Array<{ type: string }> }, callback?: (buttonId: string) => void) => void;
                showConfirm?: (message: string, callback: (confirmed: boolean) => void) => void;
                isVersionAtLeast?: (version: string) => boolean;
                onEvent: (event: string, callback: (...args: unknown[]) => void) => void;
                offEvent: (event: string, callback: (...args: unknown[]) => void) => void;
                themeParams: {
                    bg_color?: string;
                    text_color?: string;
                    hint_color?: string;
                    link_color?: string;
                    button_color?: string;
                    button_text_color?: string;
                    secondary_bg_color?: string;
                    header_bg_color?: string;
                    accent_text_color?: string;
                    section_bg_color?: string;
                    section_header_text_color?: string;
                    subtitle_text_color?: string;
                    destructive_text_color?: string;
                    section_separator_color?: string;
                    bottom_bar_bg_color?: string;
                };
                isExpanded: boolean;
                viewportHeight: number;
                viewportStableHeight: number;
                isFullscreen?: boolean;
                isActive?: boolean;
                setHeaderColor?: (color: string) => void;
                setBottomBarColor?: (color: string) => void;
                enableClosingConfirmation?: () => void;
                disableClosingConfirmation?: () => void;
                enableVerticalSwipes?: () => void;
                HapticFeedback?: {
                    impactOccurred: (style: 'light' | 'medium' | 'heavy') => void;
                    notificationOccurred: (type: 'success' | 'warning' | 'error') => void;
                    selectionChanged: () => void;
                };
                CloudStorage?: {
                    getItem: (key: string, callback: (err: string | null, value: string | null) => void) => void;
                    setItem: (key: string, value: string, callback: (err: string | null) => void) => void;
                    removeItem: (key: string, callback: (err: string | null) => void) => void;
                    getKeys: (callback: (err: string | null, keys: string[]) => void) => void;
                };
                LocationManager?: {
                    getLocation: (callback: (location: { latitude: number; longitude: number } | null) => void) => void;
                };
                readTextFromClipboard?: (callback: (text: string | null) => void) => void;
                switchInlineQuery?: (query: string, chatTypes?: string[]) => void;
                shareToStory?: (mediaUrl: string, options?: Record<string, unknown>) => void;
                shareMessage?: (text: string, url: string | null, callback?: (success: boolean) => void) => void;
                downloadFile?: (options: { url: string; file_name: string }, callback?: (result: { status: string }) => void) => void;
                addToHomeScreen?: () => void;
                checkHomeScreenStatus?: (callback: (status: string) => void) => void;
                requestFullscreen?: () => void;
                exitFullscreen?: () => void;
                lockOrientation?: (orientation?: string) => void;
                unlockOrientation?: () => void;
                openTelegramLink?: (url: string) => void;
                openLink?: (url: string, options?: { try_instant_view?: boolean }) => void;
                close?: () => void;
                safeAreaInset?: { top: number; right: number; bottom: number; left: number };
                contentSafeAreaInset?: { top: number; right: number; bottom: number; left: number };
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

        // TelegramIntegration singleton
        telegramIntegration: {
            init(): boolean;
            applyTheme(): void;
            hapticFeedback(type?: string): boolean;
            showPopup(message: string, buttons?: Array<{ type: string }>): Promise<string>;
            showAlert(message: string): Promise<void>;
            showConfirm(message: string): Promise<boolean>;
            setClosingConfirmation(enabled: boolean): void;
            readTextFromClipboard(): Promise<string | null>;
            switchInlineQuery(query: string, chatTypes?: string[]): void;
            cloudStorageSet(key: string, value: string): Promise<boolean>;
            cloudStorageGet(key: string): Promise<string | null>;
            shareToStory(mediaUrl: string, options?: Record<string, unknown>): Promise<boolean>;
            shareMessage(text: string, url?: string | null): Promise<boolean>;
            downloadFile(url: string, filename: string): Promise<boolean>;
            addToHomeScreen(): Promise<boolean>;
            checkHomeScreenStatus(): Promise<string>;
            requestFullscreen(): boolean;
            exitFullscreen(): boolean;
            lockOrientation(orientation?: string): void;
            unlockOrientation(): void;
            requestLocation(): Promise<{ latitude: number; longitude: number; altitude?: number; accuracy?: number }>;
            openTelegramLink(url: string): void;
            openLink(url: string, options?: Record<string, unknown>): void;
            close(): void;
            on(event: string, callback: (...args: unknown[]) => void): void;
            getPlatformInfo(): Record<string, unknown>;
        };

        // Common functions (common.ts)
        hapticFeedback: (type?: string) => void;
        playNotificationSound: () => boolean;
        showNotification: (message: string, duration?: number, type?: string) => void;
        formatDateTime: (dateTimeStr: string) => string;
        processTelegramHTML: (text: string) => string;
        __hapticDebugOnce?: boolean;

        // Data helpers (data.ts)
        DEFAULT_TIME_FILTER: number;
        updateTimeFilter: (minutes: number) => void;
        toggleLayerInStore: (layer: string) => void;
        setMapInstance: (map: L.Map) => void;
        getFilteredDataForRendering: () => EventFeatureCollection;
        renderDataOnMap: () => void;

        // Map geometry (map.ts)
        createIcon: (layer: string) => L.Icon | L.DivIcon;
        createMarker: (map: L.Map, latLng: L.LatLng, properties: Record<string, unknown>) => L.Marker;
        createCircle: (map: L.Map, coords: [number, number], properties: Record<string, unknown>, strategy?: string) => L.Layer[];
        getPolylineMidpoint: (latLngs: L.LatLng[]) => L.LatLng | null;
        createPolyline: (map: L.Map, coords: [number, number][], properties: Record<string, unknown>) => L.Layer[];
        createPolygon: (map: L.Map, coords: [number, number][][] , properties: Record<string, unknown>) => L.Layer[];
        createMultiPolygon: (map: L.Map, coords: [number, number][][][], properties: Record<string, unknown>) => L.Layer[];
        createPopupContent: (properties: Record<string, unknown>) => string;
        createTelegramPopup: (content: string, options?: Record<string, unknown>) => L.Popup;

        // UI (ui.ts)
        adSquares: Record<string, unknown>;
        switchTileLayer: (tileKey: string) => void;
        initializeMap: () => void;
        updateOnlineStatus: (isOnline: boolean) => void;
        renderFromCache: () => void;
        bootstrapUI: () => void;

        // Popups (popups.ts)
        showCenterPopup: (content: string) => void;
        hideCenterPopup: () => void;
        copyToClipboard: (text: string) => Promise<boolean>;
        showLegendPopup: () => void;

        // Notifications (notifications.ts)
        handleNewEvents: (events: Array<{ id: string | number; layer: string; description?: string }>) => void;

        // Token manager (token-manager.ts)
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

        // Initialize functions
        initializeWebSocket: () => void;
        getAuthHeaders: () => Record<string, string>;

        // Map instances
        currentMapInstance: L.Map | null;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        markerClusterGroup: any; // L.MarkerClusterGroup from leaflet.markercluster
        geometryLayerGroup: L.LayerGroup | null;
        randomMarkersGroup: L.LayerGroup | null;
    }
}

// Export for module usage
export {};
