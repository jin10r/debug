#!/usr/bin/env python3
"""Export events table to CSV file."""
import asyncio
import csv
import logging
import os
import sys
from datetime import datetime

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def export_events_to_csv():
    """Export all events from the database to CSV."""
    db_host = os.getenv('DB_HOST', 'localhost')
    db_port = int(os.getenv('DB_PORT', 5432))
    db_name = os.getenv('DB_NAME', 'postgres')
    db_user = os.getenv('DB_USER', 'postgres')
    db_password = os.getenv('DB_PASSWORD', 'postgres')

    output_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'events_export.csv'
    )

    # Try docker host first, then fallback to localhost
    hosts_to_try = [db_host, 'localhost', '127.0.0.1']
    conn = None

    for host in hosts_to_try:
        dsn = f"postgresql://{db_user}:{db_password}@{host}:{db_port}/{db_name}"
        logger.info(f"Connecting to database at {host}:{db_port}...")

        try:
            conn = await asyncpg.connect(dsn)
            logger.info(f"Connected to database successfully at {host}.")
            break
        except Exception as e:
            logger.warning(f"Failed to connect to {host}: {e}")
            conn = None

    if not conn:
        logger.error("Could not connect to database. Is PostgreSQL running?")
        sys.exit(1)

    try:
        logger.info("Fetching events from database...")
        rows = await conn.fetch("""
            SELECT
                id,
                event_time,
                description,
                photo_url,
                layer,
                matches,
                strategy,
                ST_AsText(geom) as geom_wkt
            FROM events
            ORDER BY id
        """)

        if not rows:
            logger.warning("No events found in the database.")
            await conn.close()
            return

        logger.info(f"Found {len(rows)} events.")

        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'id',
                'event_time',
                'description',
                'photo_url',
                'layer',
                'matches',
                'strategy',
                'geom_wkt'
            ])

            for row in rows:
                writer.writerow([
                    row['id'],
                    row['event_time'].isoformat() if row['event_time'] else '',
                    row['description'],
                    row['photo_url'] or '',
                    row['layer'],
                    row['matches'] if row['matches'] else '',
                    row['strategy'],
                    row['geom_wkt'] or ''
                ])

        logger.info(f"Successfully exported {len(rows)} events to {output_file}")

    except asyncpg.PostgresError as e:
        logger.error(f"Database error: {e}")
        sys.exit(1)
    except IOError as e:
        logger.error(f"File I/O error: {e}")
        sys.exit(1)
    finally:
        try:
            await conn.close()
            logger.info("Database connection closed.")
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(export_events_to_csv())
