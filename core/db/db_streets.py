"""
Street-related database operations.
Загрузка улиц из CSV БЕЗ векторизации (используем pg_trgm).
"""

import csv
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class StreetOperations:
    """Handles street-related database operations."""

    def __init__(self, db):
        self.db = db

    async def load_initial_data(self) -> None:
        """Загрузить данные улиц из CSV (без векторизации).
        
        ПРИМЕЧАНИЕ: Эта функция больше не используется - данные загружаются
        через PostgreSQL контейнер (02_load_data.sh). Оставлена для совместимости.
        """
        streets_csv_path = Path('/project_map/geo/streets.csv')
        if not streets_csv_path.exists():
            logger.warning(f"Street data file not found at {streets_csv_path}. Data should be loaded by PostgreSQL container.")
            return

        try:
            async with self.db.pool.acquire() as connection:
                # Проверяем существует ли таблица
                table_exists = await connection.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = 'streets'
                    )
                """)
                
                if not table_exists:
                    logger.warning("Streets table does not exist yet. Skipping data load (waiting for init.sql).")
                    return

                async with connection.transaction():
                    # Проверяем, есть ли уже данные
                    total_rows = await connection.fetchval('SELECT COUNT(*) FROM streets')
                    if total_rows > 0:
                        logger.info(f"Street data already exists ({total_rows} records). Skipping initial load.")
                        return

                    logger.info("Streets table is empty. Loading initial data from CSV...")
                    logger.info(f"CSV file path: {streets_csv_path}")

                    # Чтение CSV и вставка в БД
                    with open(streets_csv_path, 'r', encoding='utf-8') as f:
                        reader = csv.reader(f)
                        next(reader)  # Skip header

                        insert_query = """
                            INSERT INTO streets (name, geom)
                            VALUES ($1, ST_SetSRID(ST_GeomFromText($2), 4326))
                        """
                        records_to_insert = []

                        for row in reader:
                            try:
                                name, coordinates_str = row
                                coordinates = json.loads(coordinates_str)

                                if not coordinates:
                                    logger.warning(f"Skipping row with empty coordinates: {row}")
                                    continue

                                geometry = self._create_geometry(coordinates)
                                if geometry:
                                    # geometry уже строка WKT, не нужно вызывать .wkt
                                    records_to_insert.append((name, geometry))

                            except (json.JSONDecodeError, ValueError, IndexError) as e:
                                logger.warning(f"Skipping invalid row: {row}. Error: {e}")

                        if records_to_insert:
                            await connection.executemany(insert_query, records_to_insert)
                            logger.info(f"✓ Successfully loaded {len(records_to_insert)} street records")
                        else:
                            logger.warning("No valid records to insert from CSV file!")

                    # Проверяем результат
                    final_count = await connection.fetchval('SELECT COUNT(*) FROM streets')
                    if final_count > 0:
                        logger.info(f"✓ Street data loaded successfully. Total streets: {final_count}")
                    else:
                        logger.error("❌ CRITICAL: Streets table is still empty after load attempt!")

        except Exception as e:
            logger.error(f"Failed to load initial street data: {e}", exc_info=True)
            raise

    @staticmethod
    def _create_geometry(coordinates: List) -> Optional[str]:
        """Создать WKT геометрию из координат."""
        try:
            if len(coordinates) >= 4 and coordinates[0] == coordinates[-1]:
                # Polygon
                coords_str = ', '.join(f"{lon} {lat}" for lon, lat in coordinates)
                return f"POLYGON(({coords_str}))"
            elif len(coordinates) > 1:
                # LineString
                coords_str = ', '.join(f"{lon} {lat}" for lon, lat in coordinates)
                return f"LINESTRING({coords_str})"
            elif len(coordinates) == 1:
                # Point
                lon, lat = coordinates[0]
                return f"POINT({lon} {lat})"
            return None
        except Exception as e:
            logger.warning(f"Could not create geometry: {e}")
            return None

    async def get_all_streets(self) -> List[Dict]:
        """Get all streets from the database."""
        query = """
            SELECT id, name, coordinates, ST_AsGeoJSON(geom) as geom
            FROM streets
        """
        try:
            streets = await self.db.fetch(query)
            for street in streets:
                if street.get('geom'):
                    try:
                        street['geom'] = json.loads(street['geom']) if isinstance(street['geom'], str) else street['geom']
                    except json.JSONDecodeError:
                        street['geom'] = None
            return streets
        except Exception as e:
            logger.error(f"Failed to fetch streets: {e}", exc_info=True)
            return []

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
