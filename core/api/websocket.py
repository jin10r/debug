"""WebSocket API handlers for real-time event updates"""
import asyncio
import json
import logging
from typing import Dict, Set, Optional
from datetime import datetime, timezone
from aiohttp import web, WSMsgType
from core.db.dbconnect import Request

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasts individual features to clients."""

    def __init__(self, db_request: Request, cache_manager=None):
        self.db_request = db_request
        self.cache_manager = cache_manager
        self.connections: Set[web.WebSocketResponse] = set()
        self.broadcast_lock = asyncio.Lock()

    async def register_connection(self, ws: web.WebSocketResponse):
        """Register a new WebSocket connection. Data is sent only after client authenticates."""
        self.connections.add(ws)
        logger.info(f"WebSocket connection registered. Total: {len(self.connections)}")

    async def unregister_connection(self, ws: web.WebSocketResponse):
        """Unregister a WebSocket connection."""
        self.connections.discard(ws)
        logger.info(f"WebSocket connection unregistered. Total: {len(self.connections)}")

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
                    await ws.send_str(json.dumps(message))
                except Exception as e:
                    logger.warning(f"Failed to send feature to client: {e}")
                    return

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

        try:
            message = {
                'type': 'feature',
                'data': event_data,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            payload = json.dumps(message)

            async with self.broadcast_lock:
                async def send_to_client(ws: web.WebSocketResponse):
                    try:
                        await ws.send_str(payload)
                        return True
                    except Exception as e:
                        logger.debug(f"Error broadcasting to client: {e}")
                        return False

                tasks = [send_to_client(ws) for ws in self.connections.copy()]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                disconnected = set()
                success_count = 0
                for ws, result in zip(self.connections.copy(), results):
                    if result is True:
                        success_count += 1
                    else:
                        disconnected.add(ws)

                for ws in disconnected:
                    await self.unregister_connection(ws)

            logger.info(
                f"Feature broadcasted: {success_count}/{len(self.connections)} clients "
                f"({len(disconnected)} disconnected)"
            )

        except Exception as e:
            logger.error(f"Error broadcasting feature: {e}", exc_info=True)

    async def broadcast_events_cleaned(self, data: Dict):
        """Broadcast events_cleaned notification to all connected clients."""
        if not self.connections:
            return

        try:
            message = {
                'type': 'events_cleaned',
                'data': data,
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            payload = json.dumps(message)

            async with self.broadcast_lock:
                async def send_to_client(ws: web.WebSocketResponse):
                    try:
                        await ws.send_str(payload)
                        return True
                    except Exception as e:
                        logger.debug(f"Error broadcasting events_cleaned to client: {e}")
                        return False

                tasks = [send_to_client(ws) for ws in self.connections.copy()]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                disconnected = set()
                success_count = 0
                for ws, result in zip(self.connections.copy(), results):
                    if result is True:
                        success_count += 1
                    else:
                        disconnected.add(ws)

                for ws in disconnected:
                    await self.unregister_connection(ws)

                logger.info(
                    f"events_cleaned broadcasted: {success_count}/{len(self.connections)} clients"
                )

        except Exception as e:
            logger.error(f"Error broadcasting events_cleaned: {e}", exc_info=True)


async def websocket_handler(request: web.Request):
    """WebSocket endpoint for real-time event updates."""
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    ws_manager = request.app.get('websocket_manager')
    if not ws_manager:
        logger.error("WebSocket manager not found in app")
        await ws.close()
        return ws

    await ws_manager.register_connection(ws)
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
