"""WebSocket API handlers for real-time event updates"""
import asyncio
import json
import logging
from typing import Dict, Set
from datetime import datetime
from aiohttp import web, WSMsgType
from core.db.dbconnect import Request

logger = logging.getLogger(__name__)

class WebSocketManager:
    """Manages WebSocket connections and broadcasts events to clients."""

    def __init__(self, db_request: Request, cache_manager=None):
        self.db_request = db_request
        self.cache_manager = cache_manager
        self.connections: Set[web.WebSocketResponse] = set()
        self.last_event_id = 0
        self.broadcast_lock = asyncio.Lock()

    async def register_connection(self, ws: web.WebSocketResponse):
        """Register a new WebSocket connection."""
        self.connections.add(ws)
        logger.info(f"WebSocket connection registered. Total connections: {len(self.connections)}")

        # Send initial data to the newly connected client
        await self.send_initial_data(ws)

    async def unregister_connection(self, ws: web.WebSocketResponse):
        """Unregister a WebSocket connection."""
        self.connections.discard(ws)
        logger.info(f"WebSocket connection unregistered. Total connections: {len(self.connections)}")

    async def send_initial_data(self, ws: web.WebSocketResponse, time_filter: int = 60):
        """Send initial data to a newly connected client."""
        try:
            # Get recent events based on time filter
            events_data = await self.db_request.get_filtered_events_as_geojson(
                time_interval_minutes=time_filter
            )

            initial_message = {
                'type': 'initial_data',
                'data': events_data,
                'timestamp': datetime.utcnow().isoformat()
            }

            await ws.send_str(json.dumps(initial_message))
            logger.info(f"Initial data sent to new connection")

        except Exception as e:
            logger.error(f"Error sending initial data: {e}")

    async def broadcast_event(self, event_data: Dict):
        """Broadcast a new event to all connected clients in parallel."""
        if not self.connections:
            return

        try:
            message = {
                'type': 'new_event',
                'data': event_data,
                'timestamp': datetime.utcnow().isoformat()
            }

            # Use lock to prevent concurrent writes to the same connection
            async with self.broadcast_lock:
                # Параллельная отправка всем клиентам через asyncio.gather
                async def send_to_client(ws: web.WebSocketResponse):
                    try:
                        await ws.send_str(json.dumps(message))
                        return True
                    except Exception as e:
                        logger.debug(f"Error broadcasting to client: {e}")
                        return False
                
                # Создаём задачи для всех подключений
                tasks = [send_to_client(ws) for ws in self.connections.copy()]
                
                # Выполняем параллельно и собираем результаты
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Считаем успешные отправки и неудачи
                disconnected = set()
                success_count = 0
                
                for ws, result in zip(self.connections.copy(), results):
                    if result is True:
                        success_count += 1
                    else:
                        disconnected.add(ws)

                # Remove disconnected clients
                for ws in disconnected:
                    await self.unregister_connection(ws)

            logger.info(
                f"Event broadcasted: {success_count}/{len(self.connections)} clients "
                f"({len(disconnected)} disconnected)"
            )

        except Exception as e:
            logger.error(f"Error broadcasting event: {e}", exc_info=True)

    async def broadcast_filtered_events(self, events_data: Dict, time_filter: int, client_ws: web.WebSocketResponse = None):
        """Broadcast filtered events to clients in parallel."""
        try:
            message = {
                'type': 'filtered_events',
                'data': events_data,
                'time_filter': time_filter,
                'timestamp': datetime.utcnow().isoformat()
            }

            # If client_ws is specified, send only to that client
            if client_ws:
                await client_ws.send_str(json.dumps(message))
                logger.info(f"Filtered events sent to specific client")
            else:
                # Broadcast to all clients in parallel
                async with self.broadcast_lock:
                    async def send_to_client(ws: web.WebSocketResponse):
                        try:
                            await ws.send_str(json.dumps(message))
                            return True
                        except Exception as e:
                            logger.debug(f"Error broadcasting filtered events to client: {e}")
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
                        f"Filtered events broadcasted: {success_count}/{len(self.connections)} clients "
                        f"({len(disconnected)} disconnected)"
                    )

        except Exception as e:
            logger.error(f"Error broadcasting filtered events: {e}", exc_info=True)

    async def broadcast_events_cleaned(self, data: Dict):
        """Broadcast events cleaned notification to all connected clients in parallel."""
        if not self.connections:
            return

        try:
            message = {
                'type': 'events_cleaned',
                'data': data,
                'timestamp': datetime.utcnow().isoformat()
            }

            async with self.broadcast_lock:
                async def send_to_client(ws: web.WebSocketResponse):
                    try:
                        await ws.send_str(json.dumps(message))
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
                    f"Events cleaned broadcasted: {success_count}/{len(self.connections)} clients "
                    f"({len(disconnected)} disconnected)"
                )

        except Exception as e:
            logger.error(f"Error broadcasting events_cleaned: {e}", exc_info=True)


async def websocket_handler(request: web.Request):
    """WebSocket endpoint for real-time event updates."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    # Get WebSocket manager from app
    ws_manager = request.app.get('websocket_manager')
    if not ws_manager:
        logger.error("WebSocket manager not found in app")
        await ws.close()
        return ws

    # Register the connection
    await ws_manager.register_connection(ws)

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    message_type = data.get('type')

                    if message_type == 'ping':
                        # Respond to ping
                        await ws.send_str(json.dumps({'type': 'pong', 'timestamp': datetime.utcnow().isoformat()}))

                    elif message_type == 'change_time_filter':
                        # Handle time filter change
                        time_filter = data.get('time_filter', 60)
                        layer_filters = data.get('layers', [])

                        # Get filtered events from database
                        events_data = await request.app['db'].get_filtered_events_as_geojson(
                            time_interval_minutes=time_filter,
                            layers=layer_filters if layer_filters else None
                        )

                        # Send filtered events back to the requesting client only
                        await ws_manager.broadcast_filtered_events(
                            events_data=events_data,
                            time_filter=time_filter,
                            client_ws=ws
                        )

                    elif message_type == 'subscribe_to_layer':
                        # Handle layer subscription
                        layer = data.get('layer')
                        # Could implement layer-specific subscriptions here

                except json.JSONDecodeError:
                    logger.warning("Invalid JSON received from client")
                except Exception as e:
                    logger.error(f"Error processing WebSocket message: {e}")

            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket connection error: {ws.exception()}")
                break

    finally:
        # Unregister the connection when client disconnects
        await ws_manager.unregister_connection(ws)

    return ws

