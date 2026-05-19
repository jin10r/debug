"""
Event-related database operations.
Handles event creation, querying, and cleanup.
"""

import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class EventOperations:
    """Handles event-related database operations."""

    def __init__(self, db):
        self.db = db
        self.websocket_manager = None  # Будет установлено извне

    async def add_event(
        self,
        description: str,
        layer: str,
        strategy: str,
        geometry: Dict,
        matches: List[Dict],
        event_time: datetime,
        photo_url: Optional[str] = None
    ) -> Optional[int]:
        """Add event using SQL function process_event()."""
        try:
            async with self.db.pool.acquire() as connection:
                row = await connection.fetchrow(
                    "SELECT * FROM process_event($1, $2, $3, $4)",
                    event_time, description, layer, photo_url
                )

                if row:
                    event_id = row['event_id']
                    logger.info(f"Added event {event_id}: {description[:50]}...")

                    # Broadcast через WebSocket
                    if self.websocket_manager:
                        event_geojson = {
                            'type': 'Feature',
                            'geometry': geometry,
                            'properties': {
                                'id': event_id,
                                'description': description,
                                'layer': row['layer'],
                                'strategy': row['strategy'],
                                'photo_url': photo_url,
                                'matches': matches,
                                'time': event_time.isoformat()
                            }
                        }
                        await self.websocket_manager.broadcast_event(event_geojson)

                    return event_id

        except Exception as e:
            logger.error(f"Failed to add event: {e}", exc_info=True)
            return None

    async def get_filtered_events_as_geojson(
        self,
        time_interval_minutes: int,
        layers: Optional[List[str]] = None,
        since_timestamp: Optional[str] = None
    ) -> Dict:
        """Вернуть последние события в формате GeoJSON FeatureCollection.

        Args:
            time_interval_minutes: Верхняя граница возраста событий (в минутах).
            layers: Опциональный фильтр по слоям.
            since_timestamp: ISO-8601 строка (например '2026-05-18T16:37:07.000Z').
                Если задана — возвращаются только события новее этого момента
                (catch-up после переподключения). Окно time_interval_minutes
                по-прежнему применяется как верхняя граница.
                Строка конвертируется в timezone-aware datetime перед передачей
                в asyncpg: колонка event_time имеет тип timestamptz.
        """
        base_query = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(json_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(geom)::json,
                    'properties', json_build_object(
                        'id', id,
                        'description', description,
                        'layer', layer,
                        'strategy', strategy,
                        'photo_url', photo_url,
                        'matches', matches,
                        'time', event_time
                    )
                ) ORDER BY event_time), '[]'::json)
            )
            FROM events
        """

        where_clauses = ["event_time >= NOW() - $1 * interval '1 minute'"]
        params: List[Any] = [time_interval_minutes]

        if since_timestamp:
            since_dt = since_timestamp
            if isinstance(since_timestamp, str):
                try:
                    since_dt = datetime.fromisoformat(since_timestamp.replace('Z', '+00:00'))
                except ValueError:
                    logger.warning(f"Invalid since_timestamp '{since_timestamp}', ignoring")
                    since_dt = None
            if since_dt is not None:
                params.append(since_dt)
                where_clauses.append(f"event_time > ${len(params)}")

        if layers:
            valid_layers = [layer for layer in layers if layer]
            if valid_layers:
                params.append(valid_layers)
                where_clauses.append(f"layer = ANY(${len(params)})")

        query = base_query + " WHERE " + " AND ".join(where_clauses)

        try:
            async with self.db.pool.acquire() as connection:
                result = await connection.fetchval(query, *params)
            return json.loads(result) if result else {'type': 'FeatureCollection', 'features': []}
        except Exception as e:
            logger.error(f"Failed to fetch filtered events as GeoJSON: {e}", exc_info=True)
            return {'type': 'FeatureCollection', 'features': []}

    async def delete_old_events(self, time_interval_minutes: int) -> None:
        """Delete events older than specified time interval."""
        query = """
            WITH deleted AS (
                DELETE FROM events
                WHERE event_time < NOW() - $1 * interval '1 minute'
                RETURNING id
            )
            SELECT count(*) FROM deleted;
        """
        try:
            async with self.db.pool.acquire() as connection:
                deleted_count = await connection.fetchval(query, time_interval_minutes)
            if deleted_count and deleted_count > 0:
                logger.info(f"Successfully deleted {deleted_count} old events.")
        except Exception as e:
            logger.error(f"Failed to delete old events: {e}", exc_info=True)

    async def get_latest_update_time(self) -> Optional[datetime]:
        """Get the timestamp of the last update for the events table."""
        try:
            async with self.db.pool.acquire() as connection:
                query = "SELECT last_updated FROM table_updates WHERE table_name = 'events'"
                result = await connection.fetchval(query)
                return result
        except Exception as e:
            logger.error(f"Failed to get latest events update time: {e}")
            return None

    async def get_incremental_events(
        self,
        since: datetime,
        time_interval_minutes: int,
        layers: Optional[List[str]] = None
    ) -> Dict:
        """Fetch events created after 'since' timestamp, filtered by time and layers."""
        base_query = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(json_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(geom)::json,
                    'properties', json_build_object(
                        'id', id,
                        'description', description,
                        'layer', layer,
                        'strategy', strategy,
                        'photo_url', photo_url,
                        'matches', matches,
                        'time', event_time
                    )
                )), '[]'::json)
            )
            FROM events
        """

        where_clauses = [
            "event_time >= $1",
            "event_time >= NOW() - $2 * interval '1 minute'"
        ]
        params: List[Any] = [since, time_interval_minutes]

        if layers:
            valid_layers = [layer for layer in layers if layer]
            if valid_layers:
                where_clauses.append(f"layer = ANY(${len(params) + 1})")
                params.append(valid_layers)

        query = base_query + " WHERE " + " AND ".join(where_clauses)

        try:
            async with self.db.pool.acquire() as connection:
                result = await connection.fetchval(query, *params)
            return json.loads(result) if result else {'type': 'FeatureCollection', 'features': []}
        except Exception as e:
            logger.error(f"Failed to fetch incremental events as GeoJSON: {e}", exc_info=True)
            return {'type': 'FeatureCollection', 'features': []}

    async def get_events_meta(self) -> Dict[str, Any]:
        """Get events synchronization metadata (version/max_event_id/updated_at)."""
        query = """
            SELECT version, updated_at, max_event_id
            FROM events_meta
            WHERE id = 1
        """
        try:
            async with self.db.pool.acquire() as connection:
                row = await connection.fetchrow(query)
            if not row:
                return {'version': 0, 'updated_at': None, 'max_event_id': 0}
            data = dict(row)
            return {
                'version': int(data.get('version') or 0),
                'updated_at': data.get('updated_at'),
                'max_event_id': int(data.get('max_event_id') or 0)
            }
        except Exception as e:
            logger.error(f"Failed to get events_meta: {e}", exc_info=True)
            return {'version': 0, 'updated_at': None, 'max_event_id': 0}

    async def get_events_min_id(self) -> int:
        """Get minimum event id currently present in DB (used to detect resync need)."""
        query = "SELECT COALESCE(MIN(id), 0) FROM events"
        try:
            async with self.db.pool.acquire() as connection:
                val = await connection.fetchval(query)
            return int(val or 0)
        except Exception as e:
            logger.error(f"Failed to get min events id: {e}", exc_info=True)
            return 0

    async def get_events_updates_as_geojson(self, after_id: int, limit: int = 2000) -> Dict:
        """Fetch events with id > after_id, limited to last 60 minutes, as GeoJSON."""
        query = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(
                    json_agg(
                        json_build_object(
                            'type', 'Feature',
                            'geometry', ST_AsGeoJSON(geom)::json,
                            'properties', json_build_object(
                                'id', id,
                                'description', description,
                                'layer', layer,
                                'strategy', strategy,
                                'photo_url', photo_url,
                                'matches', matches,
                                'time', event_time
                            )
                        )
                        ORDER BY id
                    ),
                    '[]'::json
                )
            )
            FROM (
                SELECT *
                FROM events
                WHERE id > $1
                  AND event_time >= NOW() - INTERVAL '60 minutes'
                ORDER BY id
                LIMIT $2
            ) e
        """
        try:
            async with self.db.pool.acquire() as connection:
                result = await connection.fetchval(query, after_id, limit)
            return json.loads(result) if result else {'type': 'FeatureCollection', 'features': []}
        except Exception as e:
            logger.error(f"Failed to fetch updates as GeoJSON: {e}", exc_info=True)
            return {'type': 'FeatureCollection', 'features': []}

    async def get_events_snapshot_as_geojson(self, limit: int = 5000) -> Dict:
        """Fetch snapshot of all events as GeoJSON (used for resync)."""
        query = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(
                    json_agg(
                        json_build_object(
                            'type', 'Feature',
                            'geometry', ST_AsGeoJSON(geom)::json,
                            'properties', json_build_object(
                                'id', id,
                                'description', description,
                                'layer', layer,
                                'strategy', strategy,
                                'photo_url', photo_url,
                                'matches', matches,
                                'time', event_time
                            )
                        )
                        ORDER BY id
                    ),
                    '[]'::json
                )
            )
            FROM (
                SELECT *
                FROM events
                ORDER BY id
                LIMIT $1
            ) e
        """
        try:
            async with self.db.pool.acquire() as connection:
                result = await connection.fetchval(query, limit)
            return json.loads(result) if result else {'type': 'FeatureCollection', 'features': []}
        except Exception as e:
            logger.error(f"Failed to fetch snapshot as GeoJSON: {e}", exc_info=True)
            return {'type': 'FeatureCollection', 'features': []}


