"""Database connection and operations facade.

This module provides a unified interface for database operations,
delegating to specialized modules for different concerns:
- db_base: Connection pooling and low-level operations
- db_streets: Street data operations
- db_events: Event management
- db_spatial: PostGIS spatial operations
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime

from core.db.db_base import Database
from core.db.db_streets import StreetOperations
from core.db.db_events import EventOperations
from core.db.db_spatial import SpatialOperations


logger = logging.getLogger(__name__)

class Request:
    """Unified database operations facade.

    Delegates operations to specialized modules while maintaining
    backward compatibility with existing code.
    """

    def __init__(self, db: Database):
        self.db = db
        # Initialize specialized operation handlers
        self.streets = StreetOperations(db)
        self.events = EventOperations(db)
        self.spatial = SpatialOperations(db)
        # Для совместимости с parser
        self.settings = None  # settings импортируется глобально в parser

    async def init_tables(self) -> None:
        """
        Database schema is now initialized via the /postgres/init.sql script.
        This function is kept for compatibility but does not perform any operations.
        """
        logger.info("Skipping database table initialization. This is now handled by init.sql.")
        pass

    async def load_initial_data(self) -> None:
        """Load initial street data from CSV."""
        return await self.streets.load_initial_data()

    async def get_streets_intersection(self, street_id1: int, street_id2: int) -> Optional[Dict[str, Any]]:
        """Calculate intersection of two streets using PostGIS."""
        return await self.spatial.get_streets_intersection(street_id1, street_id2)

    async def get_streets_nearby_intersection(self, street_id1: int, street_id2: int, max_distance_m: int = 100) -> Optional[Dict[str, Any]]:
        """Find midpoint between two nearby streets."""
        return await self.spatial.get_streets_nearby_intersection(street_id1, street_id2, max_distance_m)

    async def get_batch_intersections(self, street_ids: List[int], max_distance_m: int = 100) -> List[Dict[str, Any]]:
        """Get batch intersections between multiple streets."""
        return await self.spatial.get_batch_intersections(street_ids, max_distance_m)

    async def get_max_distance_in_polygon(self, polygon_wkt: str) -> Optional[float]:
        """Calculate maximum distance in polygon."""
        return await self.spatial.get_max_distance_in_polygon(polygon_wkt)

    async def get_all_streets(self) -> List[Dict]:
        """Get all streets from the database."""
        return await self.streets.get_all_streets()

    async def get_streets_count(self) -> int:
        """Get the total count of streets."""
        return await self.streets.get_streets_count()

    async def get_latest_street_update_time(self) -> Optional[Any]:
        """Get the timestamp of the last update for the streets table."""
        return await self.streets.get_latest_update_time()

    async def add_event(
        self, description: str, layer: str, strategy: str,
        geometry: Dict, matches: List[Dict], event_time: datetime, photo_url: Optional[str] = None
    ) -> Optional[int]:
        """Add a new event to the database and return its ID."""
        return await self.events.add_event(
            description, layer, strategy, geometry, matches, event_time, photo_url
        )

    async def get_all_streets_as_geojson(self) -> str:
        """Fetch all streets as GeoJSON."""
        return await self.streets.get_all_streets_as_geojson()

    async def get_filtered_events_as_geojson(self, time_interval_minutes: int, layers: Optional[List[str]] = None, since_timestamp: Optional[str] = None) -> Dict:
        """Fetch filtered events as GeoJSON."""
        return await self.events.get_filtered_events_as_geojson(time_interval_minutes, layers, since_timestamp)

    async def get_incremental_events(
        self,
        since: datetime,
        time_interval_minutes: int,
        layers: Optional[List[str]] = None
    ) -> Dict:
        """Fetch incremental events created after 'since' timestamp."""
        return await self.events.get_incremental_events(since, time_interval_minutes, layers)

    async def get_latest_events_update_time(self) -> Optional[Any]:
        """Get the timestamp of the last update for the events table."""
        return await self.events.get_latest_update_time()

    async def delete_old_events(self, time_interval_minutes: int) -> None:
        """Delete old events from the database."""
        await self.events.delete_old_events(time_interval_minutes)

    async def get_events_meta(self) -> Dict[str, Any]:
        """Get events synchronization metadata."""
        return await self.events.get_events_meta()

    async def get_events_min_id(self) -> int:
        """Get minimum event id currently present in DB."""
        return await self.events.get_events_min_id()

    async def get_events_updates_as_geojson(self, after_id: int, limit: int = 2000) -> Dict:
        """Fetch incremental updates by id > after_id (last 60 minutes)."""
        return await self.events.get_events_updates_as_geojson(after_id, limit)

    async def get_events_snapshot_as_geojson(self, limit: int = 5000) -> Dict:
        """Fetch snapshot of last 60 minutes events."""
        return await self.events.get_events_snapshot_as_geojson(limit)

    async def get_latest_event_time(self) -> Optional[datetime]:
        """Get the timestamp of the latest event."""
        return await self.events.get_latest_update_time()

    # ========================================================================
    # Parser compatibility methods
    # ========================================================================

    async def call_process_event(
        self,
        timestamp: datetime,
        description: str,
        photo_url: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Вызвать SQL функцию process_event().
        
        Args:
            timestamp: Время события
            description: Описание события
            photo_url: URL фото (опционально)
            
        Returns:
            Dict с event_id, layer, strategy, geom или None
        """
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM process_event($1, $2, $3)",
                    timestamp, description, photo_url
                )
                
                if row:
                    return {
                        'event_id': row['event_id'],
                        'layer': row['layer'],
                        'strategy': row['strategy'],
                        'geom': row['geom']
                    }
                return None
                
        except Exception as e:
            logger.error(f"call_process_event error: {e}", exc_info=True)
            return None