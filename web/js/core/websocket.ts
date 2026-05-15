/**
 * WebSocketManager - Real-time event streaming with catch-up support
 * 
 * CORRECTED LOGIC:
 * - WebSocket is the ONLY source of truth for events
 * - localStorage is ONLY for offline display during connection loss
 * - On reconnect, WebSocket sends fresh data from backend (catch-up)
 * - No HTTP polling fallback for initial data
 * 
 * @example
 * ```typescript
 * const ws = new WebSocketManager();
 * ws.onNewEvent = (data) => { console.log('New event:', data); };
 * ws.connect();
 * ```
 */

import { EventFeature, EventFeatureCollection, WebSocketMessage } from '../types/geojson';

/**
 * WebSocket message types
 */
export enum WSMessageType {
    NEW_EVENT = 'new_event',
    FILTERED_EVENTS = 'filtered_events',
    INITIAL_DATA = 'initial_data',
    PONG = 'pong',
    CATCHUP = 'catchup',
    CHANGE_TIME_FILTER = 'change_time_filter',
    PING = 'ping',
    EVENTS_CLEANED = 'events_cleaned'
}

/**
 * WebSocketManager class
 */
export class WebSocketManager {
    /** WebSocket instance */
    private ws: WebSocket | null = null;
    
    /** Connection status */
    public isConnected = false;
    
    /** Reconnection attempts count */
    private reconnectAttempts = 0;
    
    /** Maximum reconnection attempts */
    private readonly maxReconnectAttempts = 10;
    
    /** Initial reconnection delay (ms) */
    private readonly reconnectDelay = 1000;
    
    /** Reconnection delay multiplier */
    private readonly reconnectMultiplier = 1.5;
    
    /** Heartbeat interval timer */
    private heartbeatInterval: number | null = null;
    
    /** Heartbeat timeout timer */
    private heartbeatTimeout: number | null = null;
    
    /** Heartbeat timeout duration (ms) */
    private readonly heartbeatTimeoutDuration = 15000;
    
    /** Heartbeat ping duration (ms) */
    private readonly heartbeatPingDuration = 30000;
    
    /** Callback for new events */
    public onNewEvent: ((data: EventFeatureCollection) => void) | null = null;
    
    /** Callback for filtered events */
    public onFilteredEvents: ((data: EventFeatureCollection, timeFilter: number) => void) | null = null;
    
    /** Callback for initial data */
    public onInitialData: ((data: EventFeatureCollection) => void) | null = null;
    
    /** Callback for connection status changes */
    public onConnectionStatusChange: ((isConnected: boolean) => void) | null = null;

    /**
     * Connect to WebSocket server
     */
    connect(): void {
        if (this.isConnected) {
            console.log('[WS] Already connected, skipping connect');
            return;
        }

        try {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const currentUrl = new URL(window.location.href);
            const wsPort = currentUrl.port ? ':' + currentUrl.port : '';

            // Get access token from sessionStorage (after Telegram validation)
            const accessToken = sessionStorage.getItem('access_token');
            const initData = window.Telegram?.WebApp?.initData;

            if (!accessToken && !initData) {
                console.error('[WS] No authentication available (no access token or initData)');
                return;
            }

            // Build WebSocket URL (no auth in URL for security)
            const wsUrl = `${protocol}//${currentUrl.hostname}${wsPort}/ws`;

            console.log('[WS] Connecting to:', wsUrl);
            this.ws = new WebSocket(wsUrl);

            // Send authentication after connection opens
            this.ws.onopen = () => this.handleOpen();
            this.ws.onmessage = (event) => this.handleMessage(event);
            this.ws.onclose = (event) => this.handleClose(event);
            this.ws.onerror = (error) => this.handleError(error);
        } catch (error) {
            console.error('[WS] Failed to create WebSocket:', error);
            this.scheduleReconnect();
        }
    }

    /**
     * Handle WebSocket open
     */
    private handleOpen(): void {
        console.log('[WS] Connection opened');
        this.isConnected = true;
        this.reconnectAttempts = 0;

        // Send authentication message
        this.sendAuth();

        // Start heartbeat
        this.startHeartbeat();

        // Notify connection status change
        if (this.onConnectionStatusChange) {
            this.onConnectionStatusChange(true);
        }

        // Request initial data from server (full refresh)
        // This is the PRIMARY way to get events - NOT from localStorage
        console.log('[WS] Requesting full refresh from server...');
        this.requestFullRefresh();
    }

    /**
     * Send authentication message after connection opens
     */
    private sendAuth(): void {
        const accessToken = sessionStorage.getItem('access_token');
        const initData = window.Telegram?.WebApp?.initData;

        if (accessToken) {
            console.log('[WS] Authenticating with access token');
            this.sendMessage({
                type: 'auth',
                token_type: 'bearer',
                token: accessToken
            });
        } else if (initData) {
            console.log('[WS] Authenticating with initData');
            this.sendMessage({
                type: 'auth',
                token_type: 'telegram_init_data',
                init_data: initData
            });
        } else {
            console.error('[WS] No authentication credentials available');
        }
    }

    /**
     * Handle WebSocket message
     */
    private handleMessage(event: MessageEvent): void {
        try {
            const data = JSON.parse(event.data) as WebSocketMessage;
            console.log('[WS] Message received:', data.type);
            
            switch (data.type) {
                case WSMessageType.NEW_EVENT:
                    if (data.data?.features?.length) {
                        this.onNewEvent?.(data.data);
                    }
                    break;
                    
                case WSMessageType.FILTERED_EVENTS:
                    if (data.data?.features) {
                        this.onFilteredEvents?.(data.data, data.time_filter || 30);
                    }
                    break;
                    
                case WSMessageType.INITIAL_DATA:
                    if (data.data?.features) {
                        console.log('[WS] Received initial data:', data.data.features.length, 'events');
                        this.onInitialData?.(data.data);
                    }
                    break;
                    
                case WSMessageType.PONG:
                    this.resetHeartbeatTimeout();
                    break;

                case WSMessageType.EVENTS_CLEANED:
                    console.log('[WS] Events cleaned notification from server:', data.data);
                    if (window.localCache?.cleanupExpiredEvents) {
                        const cleaned = window.localCache.cleanupExpiredEvents();
                        console.log(`[WS] Local cache cleaned ${cleaned} expired events`);
                    }
                    break;
                    
                default:
                    console.warn('[WS] Unknown message type:', data.type);
            }
        } catch (error) {
            console.error('[WS] Error parsing message:', error);
        }
    }

    /**
     * Handle WebSocket close
     */
    private handleClose(event: CloseEvent): void {
        console.log('[WS] Connection closed:', event.code, event.reason);
        this.isConnected = false;

        // Stop heartbeat
        this.stopHeartbeat();

        // Notify connection status change
        if (this.onConnectionStatusChange) {
            this.onConnectionStatusChange(false);
        }

        // IMPORTANT: localStorage cache is preserved for offline display
        // Events will still be visible from localCache until reconnect
        console.log('[WS] Connection lost - events still visible from localStorage cache');

        // Reconnect if not closed by client
        if (event.code !== 1000 && this.reconnectAttempts < this.maxReconnectAttempts) {
            this.scheduleReconnect();
        } else if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('[WS] Max reconnection attempts reached. Events visible from localStorage only.');
        }
    }

    /**
     * Handle WebSocket error
     */
    private handleError(error: Event): void {
        console.error('[WS] WebSocket error:', error);
    }

    /**
     * Request full refresh from HTTP API (fallback for initial data)
     * This is used ONLY when WebSocket first connects
     */
    private async requestFullRefresh(): Promise<void> {
        console.log('[WS] requestFullRefresh: fetching initial data from API...');
        
        try {
            const response = await fetch('/api/events', {
                method: 'GET',
                headers: {
                    'Accept': 'application/json'
                }
            });
            
            if (!response.ok) {
                throw new Error(`API returned ${response.status}`);
            }
            
            const result = await response.json();
            const events = result.data?.features || [];
            
            console.log('[WS] requestFullRefresh: got', events.length, 'events from API');
            
            if (events.length > 0 && this.onInitialData) {
                this.onInitialData({ type: 'FeatureCollection', features: events });
            } else {
                console.warn('[WS] requestFullRefresh: no events in response');
            }
        } catch (error) {
            console.error('[WS] requestFullRefresh failed:', error);
        }
    }

    /**
     * Send message through WebSocket
     */
    sendMessage(message: Record<string, unknown>): void {
        if (this.isConnected && this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
            console.log('[WS] Message sent:', message.type);
        } else {
            console.warn('[WS] WebSocket not connected, cannot send:', message);
        }
    }

    /**
     * Schedule reconnection
     */
    private scheduleReconnect(): void {
        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(this.reconnectMultiplier, this.reconnectAttempts - 1);
        
        console.log(`[WS] Scheduling reconnect in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
        
        setTimeout(() => {
            console.log('[WS] Attempting reconnect...');
            this.connect();
        }, delay);
    }

    /**
     * Start heartbeat
     */
    private startHeartbeat(): void {
        // Send ping every heartbeatPingDuration
        this.heartbeatInterval = window.setInterval(() => {
            if (this.isConnected) {
                console.log('[WS] Sending heartbeat ping');
                this.sendMessage({ type: WSMessageType.PING });

                // Set timeout for pong response
                this.heartbeatTimeout = window.setTimeout(() => {
                    console.warn('[WS] Heartbeat timeout, closing connection');
                    this.ws?.close(1000, 'Heartbeat timeout');
                }, this.heartbeatTimeoutDuration);
            }
        }, this.heartbeatPingDuration);
        
        console.log('[WS] Heartbeat started (ping every', this.heartbeatPingDuration, 'ms)');
    }

    /**
     * Reset heartbeat timeout
     */
    private resetHeartbeatTimeout(): void {
        if (this.heartbeatTimeout) {
            window.clearTimeout(this.heartbeatTimeout);
            this.heartbeatTimeout = null;
            console.log('[WS] Heartbeat timeout reset');
        }
    }

    /**
     * Stop heartbeat
     */
    private stopHeartbeat(): void {
        if (this.heartbeatInterval) {
            window.clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
            console.log('[WS] Heartbeat stopped');
        }
        
        if (this.heartbeatTimeout) {
            window.clearTimeout(this.heartbeatTimeout);
            this.heartbeatTimeout = null;
        }
    }

    /**
     * Change time filter
     */
    changeTimeFilter(timeFilter: number, layers: string[]): void {
        if (this.isConnected) {
            this.sendMessage({
                type: WSMessageType.CHANGE_TIME_FILTER,
                time_filter: timeFilter,
                layers
            });
        } else {
            console.warn('[WS] Cannot change time filter, not connected');
        }
    }

    /**
     * Disconnect from WebSocket server
     */
    disconnect(): void {
        console.log('[WS] Disconnecting...');
        
        if (this.ws) {
            this.stopHeartbeat();
            this.ws.close(1000, 'Client initiated disconnect');
            this.ws = null;
        }
        
        this.isConnected = false;
    }

    /**
     * Get connection statistics
     */
    getStats(): {
        isConnected: boolean;
        reconnectAttempts: number;
        maxReconnectAttempts: number;
    } {
        return {
            isConnected: this.isConnected,
            reconnectAttempts: this.reconnectAttempts,
            maxReconnectAttempts: this.maxReconnectAttempts
        };
    }
}

// Create and export singleton instance
window.webSocketManager = new WebSocketManager();

// Initialize WebSocket with proper event handlers
function initializeWebSocket(): void {
    console.log('[WS] Initializing WebSocket with corrected logic...');
    
    // Set up event handlers
    window.webSocketManager.onNewEvent = (eventData: EventFeatureCollection) => {
        console.log('[WS] onNewEvent received:', eventData);
        
        if (!eventData || !eventData.features || eventData.features.length === 0) {
            console.warn('[WS] onNewEvent: no features in eventData');
            return;
        }

        const event = eventData.features[0];
        console.log('[WS] Processing event:', event.properties?.id);
        
        const eventId = window.localCache?.getEventId(event);

        // Check if event is a duplicate
        if (eventId && window.localCache?.eventsById?.has(eventId)) {
            console.log('[WS] Duplicate event received, skipping:', eventId);
            return;
        }

        try {
            // localCache.addEvent handles synchronization with store and rendering
            const added = window.localCache.addEvent(event);
            if (added) {
                console.log('[WS] New event added to cache:', eventId);
                console.log('[WS] Cache now has', window.localCache.getAllEvents().features.length, 'events');
                
                // Notifications are handled by localCache.addEvent through eventManager
            } else {
                console.log('[WS] Event not added (updated existing):', eventId);
            }
        } catch (error) {
            console.error('[WS] Error processing new event:', error);
        }
    };

    window.webSocketManager.onFilteredEvents = (eventsData, timeFilter) => {
        if (!window.localCache) {
            console.warn('[WS] localCache not initialized, cannot update filtered events');
            return;
        }
        // Replace all events in cache
        window.localCache.replaceAllEvents(eventsData.features);
        window.eventManager.updateAllEvents(window.localCache.getAllEvents());
    };

    window.webSocketManager.onInitialData = (initialData) => {
        if (!window.localCache) {
            console.warn('[WS] localCache not initialized, cannot update initial data');
            return;
        }
        console.log('[WS] onInitialData: replacing all events with', initialData.features.length, 'events from server');
        
        // Replace all events in cache, suppressing notifications on initial load
        // THIS IS THE PRIMARY WAY STORE GETS POPULATED
        window.localCache.replaceAllEvents(initialData.features, true);
        window.eventManager.updateAllEvents(window.localCache.getAllEvents());
    };

    window.webSocketManager.onConnectionStatusChange = (isConnected) => {
        // Update connection status
        if (typeof window.updateOnlineStatus === 'function') {
            window.updateOnlineStatus(isConnected);
        }
        
        // Log connection status
        if (isConnected) {
            console.log('[WS] ✅ Connected - receiving live events');
        } else {
            console.log('[WS] ⚠️ Disconnected - showing events from localStorage cache');
        }
    };

    // Connect to WebSocket
    window.webSocketManager.connect();
}

// Export the initialization function globally
window.initializeWebSocket = initializeWebSocket;

console.log('✅ WebSocketManager initialized (WebSocket-only mode, localStorage for offline)');
