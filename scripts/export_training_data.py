#!/usr/bin/env python3
"""Export training_examples table to CSV for GeoIntentSeq2Seq training."""

import asyncio
import csv
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.db.db_base import Database
from core.settings import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def main():
    db = Database()
    await db.connect(
        host=settings.db.host,
        port=settings.db.port,
        user=settings.db.user,
        password=settings.db.password,
        database=settings.db.name,
    )

    rows = await db.fetch("SELECT input_text, target_text FROM training_examples ORDER BY id")

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'training_examples.csv',
    )
    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(['input', 'target'])
        for r in rows:
            writer.writerow([r['input_text'], r['target_text']])

    logger.info(f"Exported {len(rows)} examples to {out_path}")


if __name__ == '__main__':
    asyncio.run(main())
