/**
 * WebSocketManager — Real-time per-feature streaming with catch-up support.
 *
 * Protocol (client → server):
 *   {type: "auth", token_type: "bearer", token: "..."}
 *   {type: "auth", token_type: "telegram_init_data", init_data: "..."}
 *   {type: "get_events", since_timestamp: "<ISO>" | null}
 *   {type: "ping"}
 *
 * Protocol (server → client):
 *   {type: "auth_ok"}
 *   {type: "feature",        data: GeoJSON Feature}
 *   {type: "events_cleaned", data: {...}}
 *   {type: "pong",           timestamp: "..."}
 */

import { EventFeature } from '../types/geojson';

export class WebSocketManager {
    private ws: WebSocket | null = null;
    public isConnected = false;

    private reconnectAttempts = 0;
    private readonly maxReconnectAttempts = 10;
    private readonly baseReconnectDelay = 1000;
    private readonly reconnectMultiplier = 1.5;
    private reconnectTimer: number | null = null;

    // Heartbeat — ping every 25 s, expect pong within 15 s
    private readonly PING_INTERVAL_MS = 25_000;
    private readonly PONG_TIMEOUT_MS  = 15_000;
    private pingTimer:  number | null = null;
    private pongTimer:  number | null = null;
    private missedPongs = 0;
    private readonly maxMissedPongs = 2;

    /** Called once per live-pushed GeoJSON Feature (after the snapshot). */
    public onFeature: ((feature: EventFeature) => void) | null = null;

    /** Called once with the full batch when an event snapshot completes. */
    public onSnapshot: ((features: EventFeature[]) => void) | null = null;

    /** Called when connection status changes */
    public onConnectionStatusChange: ((connected: boolean) => void) | null = null;

    // Snapshot state — features between get_events and events_snapshot_end are
    // a batch sync (silent); features outside that window are live pushes.
    private receivingSnapshot = false;
    private snapshotBuffer: EventFeature[] = [];
    private snapshotTimer: number | null = null;
    private readonly SNAPSHOT_TIMEOUT_MS = 10_000;

    // ------------------------------------------------------------------ connect

    connect(): void {
        if (this.isConnected || this.ws?.readyState === WebSocket.CONNECTING) {
            console.log('[WS] Already connecting/connected, skipping');
            return;
        }

        const accessToken = sessionStorage.getItem('access_token');
        const initData    = window.Telegram?.WebApp?.initData;

        if (!accessToken && !initData) {
            console.error('[WS] No auth credentials available');
            return;
        }

        try {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const { hostname, port } = new URL(window.location.href);
            const wsUrl = `${protocol}//${hostname}${port ? ':' + port : ''}/ws`;

            console.log('[WS] Connecting to', wsUrl);
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen    = () => this.handleOpen();
            this.ws.onmessage = (e) => this.handleMessage(e);
            this.ws.onclose   = (e) => this.handleClose(e);
            this.ws.onerror   = (e) => this.handleError(e);
        } catch (err) {
            console.error('[WS] Failed to create WebSocket:', err);
            this.scheduleReconnect();
        }
    }

    // --------------------------------------------------------------- handleOpen

    private handleOpen(): void {
        console.log('[WS] Connected');
        this.isConnected      = true;
        this.reconnectAttempts = 0;
        this.missedPongs      = 0;

        this.sendAuth();
        this.startHeartbeat();
        this.onConnectionStatusChange?.(true);
        // get_events is sent after the server responds with auth_ok (see handleMessage)
    }

    /** Request events from server — called after auth_ok is received */
    private requestEvents(): void {
        const since = window.store?.getState?.().getLatestTimestamp?.() ?? null;
        console.log('[WS] Requesting events since:', since ?? 'initial load');

        // Enter snapshot mode: features until events_snapshot_end are a batch
        // sync and must not raise per-event notifications.
        this.receivingSnapshot = true;
        this.snapshotBuffer = [];
        if (this.snapshotTimer !== null) window.clearTimeout(this.snapshotTimer);
        this.snapshotTimer = window.setTimeout(
            () => this.finishSnapshot(),
            this.SNAPSHOT_TIMEOUT_MS
        );

        this.sendMessage({ type: 'get_events', since_timestamp: since });
    }

    /** Flush the buffered snapshot batch and leave snapshot mode. */
    private finishSnapshot(): void {
        if (this.snapshotTimer !== null) {
            window.clearTimeout(this.snapshotTimer);
            this.snapshotTimer = null;
        }
        if (!this.receivingSnapshot) return;
        this.receivingSnapshot = false;
        const batch = this.snapshotBuffer;
        this.snapshotBuffer = [];
        console.log('[WS] Snapshot complete:', batch.length, 'events (silent batch)');
        this.onSnapshot?.(batch);
    }

    // ------------------------------------------------------------- sendAuth

    private sendAuth(): void {
        const accessToken = sessionStorage.getItem('access_token');
        const initData    = window.Telegram?.WebApp?.initData;

        if (accessToken) {
            this.sendMessage({ type: 'auth', token_type: 'bearer', token: accessToken });
        } else if (initData) {
            this.sendMessage({ type: 'auth', token_type: 'telegram_init_data', init_data: initData });
        } else {
            console.error('[WS] No auth credentials to send');
        }
    }

    // ----------------------------------------------------------- handleMessage

    private handleMessage(event: MessageEvent): void {
        let data: Record<string, unknown>;
        try {
            data = JSON.parse(event.data as string);
        } catch {
            console.error('[WS] Invalid JSON from server');
            return;
        }

        // Anchor the filtering clock to the server (Kiev) time — every server
        // envelope carries `timestamp`. This keeps time filtering correct even
        // when the device clock or timezone is wrong.
        if (typeof data.timestamp === 'string') {
            const serverMs = Date.parse(data.timestamp);
            if (!Number.isNaN(serverMs)) {
                window.serverClockOffsetMs = serverMs - Date.now();
            }
        }

        const type = data.type as string;

        switch (type) {
            case 'feature': {
                const feature = data.data as EventFeature;
                if (feature?.type === 'Feature') {
                    if (this.receivingSnapshot) {
                        // Batch sync — buffer silently, flush on snapshot end.
                        this.snapshotBuffer.push(feature);
                    } else {
                        // Live push — a genuinely new event.
                        this.onFeature?.(feature);
                    }
                }
                break;
            }

            case 'events_snapshot_end':
                this.finishSnapshot();
                break;

            case 'auth_ok':
                console.log('[WS] Auth acknowledged by server');
                this.requestEvents();
                break;

            case 'pong':
                this.handlePong();
                break;

            case 'events_cleaned':
                console.log('[WS] events_cleaned notification');
                window.store?.getState?.().pruneExpired?.();
                break;

            default:
                console.log('[WS] Unhandled message type:', type);
        }
    }

    // ------------------------------------------------------------- handleClose

    private handleClose(event: CloseEvent): void {
        console.log('[WS] Closed:', event.code, event.reason);
        this.isConnected = false;
        this.stopHeartbeat();
        this.onConnectionStatusChange?.(false);

        if (event.code !== 1000 && this.reconnectAttempts < this.maxReconnectAttempts) {
            this.scheduleReconnect();
        } else if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.error('[WS] Max reconnect attempts reached — offline mode');
        }
    }

    private handleError(error: Event): void {
        console.error('[WS] Error:', error);
    }

    // --------------------------------------------------------- scheduleReconnect

    private scheduleReconnect(): void {
        if (this.reconnectTimer !== null) return;

        this.reconnectAttempts++;
        const delay = Math.min(
            this.baseReconnectDelay * Math.pow(this.reconnectMultiplier, this.reconnectAttempts - 1),
            30_000
        );

        console.log(`[WS] Reconnect in ${delay}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
        this.reconnectTimer = window.setTimeout(() => {
            this.reconnectTimer = null;
            this.connect();
        }, delay);
    }

    // --------------------------------------------------------------- heartbeat

    /**
     * Heartbeat strategy:
     *   - Send ping every PING_INTERVAL_MS.
     *   - After each ping, start a pong timeout of PONG_TIMEOUT_MS.
     *   - On pong: reset timeout, reset missedPongs counter.
     *   - If pong timeout fires: increment missedPongs, close the socket
     *     after maxMissedPongs consecutive misses (triggers reconnect).
     *   - The server also sends its own heartbeat frame (heartbeat=30 in aiohttp),
     *     so the connection is kept alive from both sides.
     */
    private startHeartbeat(): void {
        this.stopHeartbeat();

        this.pingTimer = window.setInterval(() => {
            if (!this.isConnected) return;

            console.log('[WS] → ping');
            this.sendMessage({ type: 'ping' });

            // Arm pong timeout
            this.pongTimer = window.setTimeout(() => {
                this.missedPongs++;
                console.warn(`[WS] Pong timeout (missed: ${this.missedPongs}/${this.maxMissedPongs})`);
                if (this.missedPongs >= this.maxMissedPongs) {
                    console.error('[WS] Too many missed pongs — forcing reconnect');
                    this.ws?.close(4000, 'heartbeat timeout');
                }
            }, this.PONG_TIMEOUT_MS);

        }, this.PING_INTERVAL_MS);
    }

    private handlePong(): void {
        console.log('[WS] ← pong');
        if (this.pongTimer !== null) {
            window.clearTimeout(this.pongTimer);
            this.pongTimer = null;
        }
        this.missedPongs = 0;
    }

    private stopHeartbeat(): void {
        if (this.pingTimer !== null) {
            window.clearInterval(this.pingTimer);
            this.pingTimer = null;
        }
        if (this.pongTimer !== null) {
            window.clearTimeout(this.pongTimer);
            this.pongTimer = null;
        }
    }

    // ---------------------------------------------------------------- sendMessage

    sendMessage(message: Record<string, unknown>): void {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        } else {
            console.warn('[WS] Cannot send — not connected:', message.type);
        }
    }

    // ---------------------------------------------------------------- disconnect

    disconnect(): void {
        this.stopHeartbeat();
        if (this.reconnectTimer !== null) {
            window.clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.snapshotTimer !== null) {
            window.clearTimeout(this.snapshotTimer);
            this.snapshotTimer = null;
        }
        this.receivingSnapshot = false;
        this.snapshotBuffer = [];
        this.reconnectAttempts = this.maxReconnectAttempts; // prevent auto-reconnect
        this.ws?.close(1000, 'client disconnect');
        this.ws = null;
        this.isConnected = false;
    }

    getStats() {
        return {
            isConnected: this.isConnected,
            reconnectAttempts: this.reconnectAttempts,
            missedPongs: this.missedPongs
        };
    }
}

// ------------------------------------------------------------------- singleton

window.webSocketManager = new WebSocketManager();

/** Show a notification for a genuinely new live event (rule 4 + rule 5). */
function notifyNewEvent(feature: EventFeature): void {
    if (typeof window.handleNewEvents !== 'function') return;
    const p: any = feature.properties || {};
    window.handleNewEvents([{
        id: p.id,
        layer: p.layer || p.type || 'unknown',
        description: p.description || p.name || 'Новое событие'
    }]);
}

function initializeWebSocket(): void {
    console.log('[WS] Initializing...');

    // Live push — append to the store; notify only if the event is new.
    window.webSocketManager.onFeature = (feature: EventFeature) => {
        const isNew = window.store.getState().addEvent(feature);
        if (isNew) {
            notifyNewEvent(feature);
        }
    };

    // Snapshot batch (initial load or reconnect catch-up) — append silently.
    window.webSocketManager.onSnapshot = (features: EventFeature[]) => {
        window.store.getState().addEvents(features);
    };

    window.webSocketManager.onConnectionStatusChange = (connected: boolean) => {
        if (typeof window.updateOnlineStatus === 'function') {
            window.updateOnlineStatus(connected);
        }
        console.log(connected ? '[WS] ✅ Live' : '[WS] ⚠️  Offline — serving localStorage');
    };

    window.webSocketManager.connect();
}

window.initializeWebSocket = initializeWebSocket;

console.log('✅ WebSocketManager initialized (per-feature protocol, reliable heartbeat)');
