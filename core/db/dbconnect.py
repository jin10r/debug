"""Database connection and operations facade.

This module provides a unified interface for database operations,
delegating to specialized modules for different concerns:
- db_base: Connection pooling and low-level operations
- db_geo: Geo data operations
- db_events: Event management
- db_spatial: PostGIS spatial operations
"""

from typing import List, Dict, Any, Optional
from datetime import datetime

from core.db.db_base import Database
from core.db.db_geo import GeoOperations
from core.db.db_events import EventOperations
from core.db.db_spatial import SpatialOperations


class Request:
    """Unified database operations facade.

    Delegates operations to specialized modules while maintaining
    backward compatibility with existing code.
    """

    def __init__(self, db: Database):
        """Инициализирует фасад БД с делегированием специализированным модулям."""
        self.db = db
        # Initialize specialized operation handlers
        self.geo = GeoOperations(db)
        self.events = EventOperations(db)
        self.spatial = SpatialOperations(db)

    async def get_geo_intersection(self, geo_id1: int, geo_id2: int) -> Optional[Dict[str, Any]]:
        """Calculate intersection of two geo records using PostGIS."""
        return await self.spatial.get_geo_intersection(geo_id1, geo_id2)

    async def get_geo_nearby_intersection(self, geo_id1: int, geo_id2: int, max_distance_m: int = 100) -> Optional[Dict[str, Any]]:
        """Find midpoint between two nearby geo records."""
        return await self.spatial.get_geo_nearby_intersection(geo_id1, geo_id2, max_distance_m)

    async def get_batch_intersections(self, geo_ids: List[int], max_distance_m: int = 100) -> List[Dict[str, Any]]:
        """Get batch intersections between multiple geo records."""
        return await self.spatial.get_batch_intersections(geo_ids, max_distance_m)

    async def get_max_distance_in_polygon(self, polygon_wkt: str) -> Optional[float]:
        """Calculate maximum distance in polygon."""
        return await self.spatial.get_max_distance_in_polygon(polygon_wkt)

    async def get_geo_count(self) -> int:
        """Get the total count of geo records."""
        return await self.geo.get_geo_count()

    async def get_latest_geo_update_time(self) -> Optional[Any]:
        """Get the timestamp of the last update for the geo table."""
        return await self.geo.get_latest_update_time()

    async def get_all_geo_as_geojson(self) -> str:
        """Fetch all geo records as GeoJSON."""
        return await self.geo.get_all_geo_as_geojson()

    async def get_filtered_events_as_geojson(self, time_interval_minutes: int, layers: Optional[List[str]] = None, since_timestamp: Optional[str] = None, after_id: Optional[int] = None) -> Dict:
        """Fetch filtered events as GeoJSON."""
        return await self.events.get_filtered_events_as_geojson(time_interval_minutes, layers, since_timestamp, after_id)

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
