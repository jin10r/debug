"""Database operations module.

Exports the Database class and its specialized operation modules.
Consumers access operations via: db.events.method(), db.geo.method(), etc.
"""

from core.db.db_base import Database
from core.db.db_geo import GeoOperations
from core.db.db_events import EventOperations
from core.db.db_spatial import SpatialOperations

__all__ = ['Database', 'GeoOperations', 'EventOperations', 'SpatialOperations']
