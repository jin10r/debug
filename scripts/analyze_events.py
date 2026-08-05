#!/usr/bin/env python3
"""Analyze events table for strategy quality and anomalies."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db.db_base import Database
from core.settings import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class EventsAnalyzer:
    """Analyze events table for strategy quality and anomalies."""

    def __init__(self):
        self.db = Database()

    async def connect(self):
        """Connect to database."""
        await self.db.connect(
            host=settings.db.host,
            port=settings.db.port,
            database=settings.db.database,
            user=settings.db.user,
            password=settings.db.password,
            min_size=settings.db.pool_min_size,
            max_size=settings.db.pool_max_size
        )
        logger.info("Connected to database")

    async def close(self):
        """Close database connection."""
        await self.db.close()
        logger.info("Database connection closed")

    async def get_strategy_distribution(self) -> List[Dict[str, Any]]:
        """Get distribution of strategies in events table."""
        query = """
            SELECT 
                strategy,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
            FROM events
            GROUP BY strategy
            ORDER BY count DESC
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

    async def get_layer_distribution(self) -> List[Dict[str, Any]]:
        """Get distribution of layers in events table."""
        query = """
            SELECT 
                layer,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
            FROM events
            GROUP BY layer
            ORDER BY count DESC
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

    async def get_strategy_by_layer(self) -> List[Dict[str, Any]]:
        """Get strategy distribution by layer."""
        query = """
            SELECT 
                layer,
                strategy,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(PARTITION BY layer), 2) as layer_percentage
            FROM events
            GROUP BY layer, strategy
            ORDER BY layer, count DESC
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

    async def get_geometry_type_distribution(self) -> List[Dict[str, Any]]:
        """Get distribution of geometry types by strategy."""
        query = """
            SELECT 
                strategy,
                ST_GeometryType(geom) as geom_type,
                COUNT(*) as count
            FROM events
            GROUP BY strategy, ST_GeometryType(geom)
            ORDER BY strategy, count DESC
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

    async def get_events_with_null_geometry(self) -> List[Dict[str, Any]]:
        """Find events with null or empty geometry."""
        query = """
            SELECT id, description, strategy, layer, event_time
            FROM events
            WHERE geom IS NULL OR ST_IsEmpty(geom)
            ORDER BY event_time DESC
            LIMIT 20
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

    async def get_events_with_many_matches(self, threshold: int = 5) -> List[Dict[str, Any]]:
        """Find events with unusually many matches."""
        query = """
            SELECT 
                id, 
                description, 
                strategy, 
                jsonb_array_length(matches) as match_count,
                event_time
            FROM events
            WHERE jsonb_array_length(matches) > $1
            ORDER BY match_count DESC
            LIMIT 20
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query, threshold)
        return [dict(row) for row in rows]

    async def get_events_with_zero_matches(self) -> List[Dict[str, Any]]:
        """Find events with zero matches (random strategy)."""
        query = """
            SELECT 
                id, 
                description, 
                strategy, 
                jsonb_array_length(matches) as match_count,
                event_time
            FROM events
            WHERE jsonb_array_length(matches) = 0
            ORDER BY event_time DESC
            LIMIT 20
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

    async def get_time_distribution(self) -> List[Dict[str, Any]]:
        """Get event distribution by time periods."""
        query = """
            SELECT 
                DATE_TRUNC('hour', event_time) as hour,
                COUNT(*) as count
            FROM events
            WHERE event_time > NOW() - INTERVAL '24 hours'
            GROUP BY DATE_TRUNC('hour', event_time)
            ORDER BY hour DESC
        """
        async with self.db.pool.acquire() as conn:
            rows = await conn.fetch(query)
        return [dict(row) for row in rows]

    async def get_total_stats(self) -> Dict[str, Any]:
        """Get total statistics."""
        query = """
            SELECT 
                COUNT(*) as total_events,
                COUNT(DISTINCT layer) as unique_layers,
                COUNT(DISTINCT strategy) as unique_strategies,
                MIN(event_time) as first_event,
                MAX(event_time) as last_event,
                AVG(jsonb_array_length(matches)) as avg_matches
            FROM events
        """
        async with self.db.pool.acquire() as conn:
            row = await conn.fetchrow(query)
        return dict(row) if row else {}

    async def analyze(self):
        """Run full analysis."""
        logger.info("=" * 60)
        logger.info("EVENTS TABLE ANALYSIS")
        logger.info("=" * 60)

        # Total stats
        logger.info("\n📊 TOTAL STATISTICS")
        stats = await self.get_total_stats()
        for key, value in stats.items():
            logger.info(f"  {key}: {value}")

        # Strategy distribution
        logger.info("\n📈 STRATEGY DISTRIBUTION")
        strategies = await self.get_strategy_distribution()
        for row in strategies:
            logger.info(f"  {row['strategy']:25s}: {row['count']:5d} ({row['percentage']:5.2f}%)")

        # Layer distribution
        logger.info("\n📊 LAYER DISTRIBUTION")
        layers = await self.get_layer_distribution()
        for row in layers:
            logger.info(f"  {row['layer']:10s}: {row['count']:5d} ({row['percentage']:5.2f}%)")

        # Strategy by layer
        logger.info("\n📊 STRATEGY BY LAYER")
        strategy_by_layer = await self.get_strategy_by_layer()
        current_layer = None
        for row in strategy_by_layer:
            if row['layer'] != current_layer:
                current_layer = row['layer']
                logger.info(f"\n  Layer: {current_layer}")
            logger.info(f"    {row['strategy']:20s}: {row['count']:5d} ({row['layer_percentage']:5.2f}%)")

        # Geometry type distribution
        logger.info("\n📐 GEOMETRY TYPE DISTRIBUTION BY STRATEGY")
        geom_types = await self.get_geometry_type_distribution()
        current_strategy = None
        for row in geom_types:
            if row['strategy'] != current_strategy:
                current_strategy = row['strategy']
                logger.info(f"\n  Strategy: {current_strategy}")
            logger.info(f"    {row['geom_type']:20s}: {row['count']:5d}")

        # Anomalies
        logger.info("\n⚠️  ANOMALIES")

        # Null geometry
        null_geom = await self.get_events_with_null_geometry()
        if null_geom:
            logger.info(f"\n  Events with null/empty geometry: {len(null_geom)}")
            for row in null_geom[:5]:
                logger.info(f"    ID {row['id']}: {row['description'][:50]}... (strategy: {row['strategy']})")
        else:
            logger.info("\n  No events with null/empty geometry")

        # Many matches
        many_matches = await self.get_events_with_many_matches(threshold=5)
        if many_matches:
            logger.info(f"\n  Events with >5 matches: {len(many_matches)}")
            for row in many_matches[:5]:
                logger.info(f"    ID {row['id']}: {row['match_count']} matches - {row['description'][:50]}... (strategy: {row['strategy']})")
        else:
            logger.info("\n  No events with >5 matches")

        # Zero matches
        zero_matches = await self.get_events_with_zero_matches()
        if zero_matches:
            logger.info(f"\n  Events with zero matches: {len(zero_matches)}")
            for row in zero_matches[:5]:
                logger.info(f"    ID {row['id']}: {row['description'][:50]}... (strategy: {row['strategy']})")
        else:
            logger.info("\n  No events with zero matches")

        # Time distribution
        logger.info("\n⏰ EVENT DISTRIBUTION BY HOUR (LAST 24H)")
        time_dist = await self.get_time_distribution()
        for row in time_dist:
            logger.info(f"  {row['hour']}: {row['count']} events")

        # Quality assessment
        logger.info("\n🔍 QUALITY ASSESSMENT")
        total = stats.get('total_events', 0)
        random_count = next((r['count'] for r in strategies if r['strategy'] == 'random'), 0)
        random_pct = (random_count / total * 100) if total > 0 else 0

        logger.info(f"  Random strategy rate: {random_pct:.2f}%")
        if random_pct > 30:
            logger.warning("  ⚠️  High random rate (>30%) - may indicate missing geo data")
        elif random_pct > 20:
            logger.warning("  ⚠️  Elevated random rate (>20%) - review geo coverage")
        else:
            logger.info("  ✅ Random rate within acceptable range")

        single_match_count = next((r['count'] for r in strategies if r['strategy'] == 'single_match'), 0)
        single_match_pct = (single_match_count / total * 100) if total > 0 else 0
        logger.info(f"  Single match rate: {single_match_pct:.2f}%")

        intersection_count = sum(r['count'] for r in strategies if 'intersection' in r['strategy'])
        intersection_pct = (intersection_count / total * 100) if total > 0 else 0
        logger.info(f"  Intersection rate: {intersection_pct:.2f}%")

        logger.info("\n" + "=" * 60)


async def main():
    """Main entry point."""
    analyzer = EventsAnalyzer()
    try:
        await analyzer.connect()
        await analyzer.analyze()
    finally:
        await analyzer.close()


if __name__ == "__main__":
    asyncio.run(main())
