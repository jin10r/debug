"""WebSocket API handlers for real-time event updates"""
import asyncio
import json
import logging
from typing import Dict, Set, Optional
from datetime import datetime, timezone
from aiohttp import web, WSMsgType
from core.db.dbconnect import Request

logger = logging.getLogger(__name__)

# Верхняя граница одновременных WS-соединений — защита от утечки/перегрузки.
MAX_CONNECTIONS = 1000
# Таймаут отправки одному клиенту: зависший клиент не должен тормозить
# рассылку остальным (asyncio.gather ждёт все корутины).
SEND_TIMEOUT = 5.0


class WebSocketManager:
    """Manages WebSocket connections and broadcasts individual features to clients."""

    def __init__(self, db_request: Request, cache_manager=None):
        self.db_request = db_request
        self.cache_manager = cache_manager
        self.connections: Set[web.WebSocketResponse] = set()
        self.broadcast_lock = asyncio.Lock()

    async def register_connection(self, ws: web.WebSocketResponse) -> bool:
        """Register a new WebSocket connection.

        Returns False if the connection limit is reached — caller must close ws.
        Data is sent only after the client authenticates.
        """
        if len(self.connections) >= MAX_CONNECTIONS:
            logger.warning(f"WebSocket connection rejected: limit {MAX_CONNECTIONS} reached")
            return False
        self.connections.add(ws)
        logger.info(f"WebSocket connection registered. Total: {len(self.connections)}")
        return True

    async def unregister_connection(self, ws: web.WebSocketResponse):
        """Unregister a WebSocket connection."""
        self.connections.discard(ws)
        logger.debug(f"WebSocket connection unregistered. Total: {len(self.connections)}")

    async def close_all(self) -> None:
        """Close all active WebSocket connections (called during server shutdown)."""
        for ws in list(self.connections):
            try:
                await asyncio.wait_for(
                    ws.close(code=1001, message=b'server shutdown'), timeout=2.0
                )
            except Exception:
                pass
        self.connections.clear()

    async def _broadcast_payload(self, payload: str) -> int:
        """Send payload string to all connected clients; remove dead ones. Returns success count."""
        snapshot = list(self.connections)
        if not snapshot:
            return 0

        async def _send(ws: web.WebSocketResponse) -> bool:
            try:
                await asyncio.wait_for(ws.send_str(payload), timeout=SEND_TIMEOUT)
                return True
            except Exception as e:
                logger.debug(f"Broadcast send error/timeout: {e}")
                return False

        async with self.broadcast_lock:
            results = await asyncio.gather(*[_send(ws) for ws in snapshot], return_exceptions=True)

        success = 0
        for ws, ok in zip(snapshot, results):
            if ok is True:
                success += 1
            else:
                await self.unregister_connection(ws)
        return success

    async def send_events_since(
        self,
        ws: web.WebSocketResponse,
        since_timestamp: Optional[str] = None
    ):
        """
        Send individual GeoJSON features to a client.
        If since_timestamp is None — send all events from last 60 min (initial load).
        If set — send only events newer than that timestamp (catch-up after reconnect).
        """
        try:
            events_data = await self.db_request.get_filtered_events_as_geojson(
                time_interval_minutes=60,
                since_timestamp=since_timestamp
            )

            features = events_data.get('features', [])
            logger.info(
                f"Sending {len(features)} features to client "
                f"(since={since_timestamp or 'initial'})"
            )

            for feature in features:
                message = {
                    'type': 'feature',
                    'data': feature,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
                try:
                    await asyncio.wait_for(
                        ws.send_str(json.dumps(message)), timeout=SEND_TIMEOUT
                    )
                except Exception as e:
                    logger.warning(f"Failed to send feature to client: {e}")
                    return

            # Terminal marker for the batch. The client treats every feature
            # received before this as a silent snapshot (initial load or
            # reconnect catch-up); only live pushes after it raise per-event
            # notifications.
            try:
                await ws.send_str(json.dumps({
                    'type': 'events_snapshot_end',
                    'count': len(features),
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }))
            except Exception as e:
                logger.warning(f"Failed to send events_snapshot_end: {e}")

        except Exception as e:
            logger.error(f"Error sending events to client: {e}", exc_info=True)

    async def broadcast_event(self, event_data: Dict):
        """
        Broadcast a single GeoJSON feature to all connected clients.
        event_data must be a GeoJSON Feature dict (not a FeatureCollection).
        """
        if not self.connections:
            return

        # Normalise: if the parser sent a FeatureCollection, extract the first feature
        if event_data.get('type') == 'FeatureCollection':
            features = event_data.get('features', [])
            if not features:
                logger.warning("broadcast_event: empty FeatureCollection, skipping")
                return
            event_data = features[0]

        if event_data.get('type') != 'Feature':
            logger.warning(f"broadcast_event: unexpected data type: {event_data.get('type')}")
            return

        payload = json.dumps({
            'type': 'feature',
            'data': event_data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

        success = await self._broadcast_payload(payload)
        logger.info(f"Feature broadcasted: {success}/{len(self.connections)} clients")

    async def broadcast_events_cleaned(self, data: Dict):
        """Broadcast events_cleaned notification to all connected clients."""
        if not self.connections:
            return

        payload = json.dumps({
            'type': 'events_cleaned',
            'data': data,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })

        success = await self._broadcast_payload(payload)
        logger.info(f"events_cleaned broadcasted: {success}/{len(self.connections)} clients")


async def websocket_handler(request: web.Request):
    """WebSocket endpoint for real-time event updates."""
    ws = web.WebSocketResponse(heartbeat=120)
    await ws.prepare(request)

    ws_manager = request.app.get('websocket_manager')
    if not ws_manager:
        logger.error("WebSocket manager not found in app")
        await ws.close()
        return ws

    if not await ws_manager.register_connection(ws):
        await ws.close(code=1013, message=b'server busy')  # 1013 = Try Again Later
        return ws
    authenticated = False

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    message_type = data.get('type')

                    if message_type == 'ping':
                        await ws.send_str(json.dumps({
                            'type': 'pong',
                            'timestamp': datetime.now(timezone.utc).isoformat()
                        }))

                    elif message_type == 'auth':
                        # Minimal auth acknowledgement — JWT validation happens at HTTP level.
                        # The token is already verified by jwt_auth_middleware for the upgrade
                        # request. Here we just mark the WS session as authorised.
                        authenticated = True
                        logger.info("WebSocket client authenticated")
                        await ws.send_str(json.dumps({'type': 'auth_ok'}))

                    elif message_type == 'get_events':
                        if not authenticated:
                            await ws.send_str(json.dumps({'type': 'error', 'message': 'not authenticated'}))
                            continue

                        since_timestamp = data.get('since_timestamp')  # ISO string or null
                        await ws_manager.send_events_since(ws, since_timestamp)

                except json.JSONDecodeError:
                    logger.warning("Invalid JSON received from WebSocket client")
                except Exception as e:
                    logger.error(f"Error processing WebSocket message: {e}", exc_info=True)

            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket connection error: {ws.exception()}")
                break

    finally:
        await ws_manager.unregister_connection(ws)

    return ws
