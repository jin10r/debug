"""
Spatial database operations using PostGIS.
Handles geometric calculations and intersections.
"""

import logging
import json
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class SpatialOperations:
    """Handles PostGIS spatial operations."""
    
    def __init__(self, db):
        self.db = db

    async def get_streets_intersection(
        self,
        street_id1: int,
        street_id2: int
    ) -> Optional[Dict[str, Any]]:
        """
        Calculate intersection of two streets using PostGIS.
        Returns GeoJSON point or None.
        """
        query = """
            WITH
            geom1 AS (SELECT geom FROM streets WHERE id = $1),
            geom2 AS (SELECT geom FROM streets WHERE id = $2),
            intersection AS (
                SELECT ST_Intersection(g1.geom, g2.geom) AS geom
                FROM geom1 g1, geom2 g2
            )
            SELECT ST_AsGeoJSON(
                CASE
                    WHEN ST_GeometryType(geom) = 'ST_Point' THEN geom
                    ELSE ST_Centroid(geom)
                END
            )::jsonb AS geojson
            FROM intersection
            WHERE geom IS NOT NULL AND NOT ST_IsEmpty(geom);
        """
        try:
            result = await self.db.fetchval(query, street_id1, street_id2)
            return result if result else None
        except Exception as e:
            logger.error(
                f"PostGIS intersection query failed for streets {street_id1}, {street_id2}: {e}",
                exc_info=True
            )
            return None

    async def get_streets_nearby_intersection(
        self,
        street_id1: int,
        street_id2: int,
        max_distance_m: int = 100
    ) -> Optional[Dict[str, Any]]:
        """
        Find midpoint on shortest line between two streets if within max_distance_m.
        Returns GeoJSON Point or None.
        """
        query = """
            WITH
                g1 AS (SELECT geom FROM streets WHERE id = $1),
                g2 AS (SELECT geom FROM streets WHERE id = $2),
                shortest AS (
                    SELECT ST_ShortestLine(g1.geom, g2.geom) AS geom
                    FROM g1, g2
                ),
                dist AS (
                    SELECT ST_Length(ST_Transform(geom, 3857)) AS dist_m, geom 
                    FROM shortest
                )
            SELECT CASE
                WHEN dist_m <= $3 THEN ST_AsGeoJSON(ST_LineInterpolatePoint(geom, 0.5))::jsonb
                ELSE NULL
            END AS geojson
            FROM dist;
        """
        try:
            result = await self.db.fetchval(query, street_id1, street_id2, max_distance_m)
            return result if result else None
        except Exception as e:
            logger.error(
                f"PostGIS nearby-intersection query failed for streets {street_id1}, {street_id2}: {e}",
                exc_info=True
            )
            return None

    async def get_batch_intersections(self, street_ids: List[int], max_distance_m: int = 100) -> List[Dict[str, Any]]:
        """
        Оптимизированный batch-запрос для получения всех пересечений между парами улиц.
        Использует PostGIS для вычисления пересечений и псевдо-пересечений за один запрос.
        
        Возвращает список словарей с:
        - id1, id2: ID улиц
        - geom: GeoJSON точки пересечения
        - is_real: true для реального пересечения, false для псевдо-пересечения
        """
        if len(street_ids) < 2:
            return []
        
        query = """
            WITH street_pairs AS (
                -- Генерируем все пары улиц без повторений
                SELECT s1.id AS id1, s2.id AS id2, s1.geom AS geom1, s2.geom AS geom2
                FROM streets s1
                CROSS JOIN streets s2
                WHERE s1.id = ANY($1::int[]) 
                  AND s2.id = ANY($1::int[])
                  AND s1.id < s2.id
            ),
            real_intersections AS (
                -- Реальные пересечения
                SELECT 
                    id1, 
                    id2,
                    ST_AsGeoJSON(
                        CASE
                            WHEN ST_GeometryType(ST_Intersection(geom1, geom2)) = 'ST_Point' 
                                THEN ST_Intersection(geom1, geom2)
                            ELSE ST_Centroid(ST_Intersection(geom1, geom2))
                        END
                    )::jsonb AS geom,
                    true AS is_real
                FROM street_pairs
                WHERE ST_Intersects(geom1, geom2) 
                  AND NOT ST_IsEmpty(ST_Intersection(geom1, geom2))
            ),
            nearby_intersections AS (
                -- Псевдо-пересечения (близкие улицы)
                SELECT 
                    sp.id1,
                    sp.id2,
                    ST_AsGeoJSON(
                        ST_LineInterpolatePoint(ST_ShortestLine(sp.geom1, sp.geom2), 0.5)
                    )::jsonb AS geom,
                    false AS is_real
                FROM street_pairs sp
                WHERE NOT EXISTS (
                    SELECT 1 FROM real_intersections ri 
                    WHERE ri.id1 = sp.id1 AND ri.id2 = sp.id2
                )
                AND ST_Length(ST_Transform(ST_ShortestLine(sp.geom1, sp.geom2), 3857)) <= $2
            )
            SELECT * FROM real_intersections
            UNION ALL
            SELECT * FROM nearby_intersections
            ORDER BY is_real DESC, id1, id2;
        """
        try:
            results = await self.db.fetch(query, street_ids, max_distance_m)
            return [dict(row) for row in results]
        except Exception as e:
            logger.error(f"Batch intersection query failed for street IDs {street_ids}: {e}", exc_info=True)
            return []

    async def get_max_distance_in_polygon(self, polygon_wkt: str) -> Optional[float]:
        """
        Calculate maximum distance in meters between any two vertices of a polygon.
        Uses ST_LongestLine and ST_Length.
        """
        query = """
            SELECT ST_Length(
                ST_LongestLine(
                    ST_GeomFromText($1, 4326),
                    ST_GeomFromText($1, 4326)
                )::geography
            );
        """
        try:
            distance_in_meters = await self.db.fetchval(query, polygon_wkt)
            return distance_in_meters
        except Exception as e:
            logger.error(f"Failed to calculate max distance for polygon: {e}", exc_info=True)
            return None

