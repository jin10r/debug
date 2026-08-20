"""Tests for core/db/db_spatial.py — SpatialOperations."""
from unittest.mock import AsyncMock, MagicMock

import pytest

try:
    from core.db.db_spatial import SpatialOperations
    _IMPORT_OK = True
    _IMPORT_ERR = None
except Exception as e:
    _IMPORT_OK = False
    _IMPORT_ERR = repr(e)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK, reason=f"db_spatial import unavailable: {_IMPORT_ERR}"
)


# ============================================================
# Helpers
# ============================================================

class _AsyncCtx:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, exc_type, exc, tb):
        return None


def _mock_connection():
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    return conn


class FakeDb:
    def __init__(self):
        self.pool = MagicMock()
        self.pool.acquire = MagicMock(return_value=_AsyncCtx(_mock_connection()))
        self.fetchval = AsyncMock(return_value=None)
        self.fetch = AsyncMock(return_value=[])


# ============================================================
# SpatialOperations
# ============================================================

class TestSpatialOperations:
    @pytest.fixture
    def ops(self):
        return SpatialOperations(db=FakeDb())

    @pytest.mark.asyncio
    async def test_get_geo_intersection_returns_geojson(self, ops):
        ops.db.fetchval = AsyncMock(return_value={"type": "Point", "coordinates": [0, 0]})
        result = await ops.get_geo_intersection(1, 2)
        assert result == {"type": "Point", "coordinates": [0, 0]}

    @pytest.mark.asyncio
    async def test_get_geo_intersection_returns_none_on_error(self, ops):
        ops.db.fetchval = AsyncMock(side_effect=Exception("DB error"))
        result = await ops.get_geo_intersection(1, 2)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_geo_nearby_intersection_within_distance(self, ops):
        ops.db.fetchval = AsyncMock(return_value={"type": "Point", "coordinates": [0, 0]})
        result = await ops.get_geo_nearby_intersection(1, 2, max_distance_m=100)
        assert result == {"type": "Point", "coordinates": [0, 0]}
        args = ops.db.fetchval.call_args[0]
        assert args[2] == 2  # geo_id2
        assert args[3] == 100  # max_distance_m

    @pytest.mark.asyncio
    async def test_get_geo_nearby_intersection_returns_none_when_far(self, ops):
        ops.db.fetchval = AsyncMock(return_value=None)
        result = await ops.get_geo_nearby_intersection(1, 2, max_distance_m=10)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_batch_intersections_returns_empty_for_less_than_two(self, ops):
        result = await ops.get_batch_intersections([1])
        assert result == []

    @pytest.mark.asyncio
    async def test_get_batch_intersections_truncates_to_max_geo_ids(self, ops):
        ops.db.fetch = AsyncMock(return_value=[])
        geo_ids = list(range(25))
        await ops.get_batch_intersections(geo_ids, max_distance_m=100)
        args = ops.db.fetch.call_args[0]
        assert len(args[1]) == 20  # truncated to _MAX_GEO_IDS

    @pytest.mark.asyncio
    async def test_get_batch_intersections_returns_dict_list(self, ops):
        mock_rows = [
            {"id1": 1, "id2": 2, "geom": {"type": "Point"}, "is_real": True},
            {"id1": 2, "id2": 3, "geom": {"type": "Point"}, "is_real": False},
        ]
        ops.db.fetch = AsyncMock(return_value=mock_rows)
        result = await ops.get_batch_intersections([1, 2, 3], max_distance_m=100)
        assert len(result) == 2
        assert result[0]["id1"] == 1

    @pytest.mark.asyncio
    async def test_get_max_distance_in_polygon_returns_distance(self, ops):
        ops.db.fetchval = AsyncMock(return_value=150.5)
        result = await ops.get_max_distance_in_polygon("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        assert result == 150.5

    @pytest.mark.asyncio
    async def test_get_max_distance_in_polygon_returns_none_on_error(self, ops):
        ops.db.fetchval = AsyncMock(side_effect=Exception("DB error"))
        result = await ops.get_max_distance_in_polygon("POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        assert result is None
