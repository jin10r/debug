"""
Street-related database operations.
Данные улиц грузит postgres-контейнер (init-scripts/04-load-data.sql); здесь —
только чтение (count, geojson, время обновления).
"""

import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class StreetOperations:
    """Handles street-related database operations."""

    def __init__(self, db):
        self.db = db

    async def get_streets_count(self) -> int:
        """Get the total count of streets."""
        try:
            return await self.db.fetchval("SELECT COUNT(*) FROM streets")
        except Exception as e:
            logger.error(f"Failed to get streets count: {e}")
            return 0

    async def get_latest_update_time(self) -> Optional[Any]:
        """Get the timestamp of the last update for the streets table."""
        try:
            query = "SELECT last_updated FROM table_updates WHERE table_name = 'streets'"
            return await self.db.fetchval(query)
        except Exception as e:
            logger.error(f"Failed to get latest street update time: {e}")
            return None

    async def get_all_streets_as_geojson(self) -> str:
        """Fetch all streets as a GeoJSON FeatureCollection."""
        query = """
            SELECT json_build_object(
                'type', 'FeatureCollection',
                'features', COALESCE(json_agg(
                    json_build_object(
                        'type', 'Feature',
                        'geometry', ST_AsGeoJSON(geom)::json,
                        'properties', json_build_object(
                            'name', array_to_string(names, '|'),
                            'id', id
                        )
                    )
                ), '[]'::json)
            )
            FROM streets
            WHERE ST_IsValid(geom);
        """
        try:
            async with self.db.pool.acquire() as connection:
                result = await connection.fetchval(query)
            return result if result else '{"type": "FeatureCollection", "features": []}'
        except Exception as e:
            logger.error(f"Failed to fetch streets as GeoJSON: {e}")
            return '{"type": "FeatureCollection", "features": []}'
