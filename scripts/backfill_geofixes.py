#!/usr/bin/env python3
"""Backfill исторических событий: пересчёт геометрии/стратегии через новую
process_candidates из сохранённых matches + свежее решение SemanticResolver.

Обрабатываются события с 2+ matches (включая все intersection/midpoint) —
именно их затрагивали баги ST_ConvexHull и мёртвого pre-filter'а.
Одно-матчевые single_match и random не трогаются.

SemanticMatcher (rubert-tiny2) НЕ подключается — соответствует текущему
проду (settings.similarity.semantic_enabled=False, см. docs §13): слабые
кандидаты (< 0.85) отклоняет детерминированный порог в process_candidates.

Запуск (в контейнере processor, где есть deps + доступ к БД):
  docker cp scripts/backfill_geofixes.py processor:/tmp/
  docker exec processor python /tmp/backfill_geofixes.py           # dry-run
  docker exec processor python /tmp/backfill_geofixes.py --apply   # запись в БД
"""
import asyncio
import json
import sys

sys.path.insert(0, '/app')

from core.db.db_adapter import DBAdapter
from core.settings import settings
from processor.geo_matcher import GeoMatcher
from processor.morphology import Morphology
from processor.phonetic_index import PhoneticIndex
from processor.word_tokenizer import tokenize


def _loads(matches):
    if isinstance(matches, str):
        try:
            return json.loads(matches)
        except Exception:
            return []
    return matches or []


async def main(apply: bool) -> None:
    db = DBAdapter()
    if not await db.connect():
        print('DB connect failed')
        return
    pool = db.pool

    morph = Morphology()
    index = PhoneticIndex(morph)
    matcher = GeoMatcher(morph, index)
    await matcher.initialize(pool)

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, description, matches, strategy AS old_strategy
            FROM events
            WHERE jsonb_array_length(matches) >= 2
               OR strategy IN ('intersection', 'midpoint')
            ORDER BY id
        """)
    print(f'Кандидатов: {len(rows)} (mode={"APPLY" if apply else "DRY-RUN"})')

    changes = []
    async with pool.acquire() as conn:
        for r in rows:
            eid = r['id']
            matches = _loads(r['matches'])
            if not matches:
                continue
            geo_ids = [int(m['geo_id']) for m in matches]
            scores = [float(m.get('similarity', 1.0)) for m in matches]
            texts = [str(m.get('matched_text') or '') for m in matches]
            desc = r['description'] or ''

            pc = await conn.fetchrow(
                """SELECT result_geom, result_strategy, result_matches
                   FROM process_candidates_v2($1::int[], $2::float[], $3::text[], $4::varchar)""",
                geo_ids, scores, texts, None)

            new_strategy = pc['result_strategy']
            old_strategy = r['old_strategy']
            changes.append(
                (eid, old_strategy, new_strategy, len(geo_ids), (desc or '')[:45]))

            if apply and pc['result_geom'] is not None:
                await conn.execute(
                    "UPDATE events SET geom=$2, strategy=$3, matches=$4, confidence=$5, geo_diagnostics=jsonb_set(COALESCE(geo_diagnostics, '{}'::jsonb), '{backfilled}', 'true'::jsonb) WHERE id=$1",
                    eid, pc['result_geom'], new_strategy, pc['result_matches'], pc['result_confidence'])

    if apply:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE events_meta SET version = version + 1, updated_at = now() WHERE id = 1")

    print(f'\n=== Изменения ({len(changes)}) ===')
    for eid, old, new, n, desc in changes:
        mark = '  <-- СМЕНА СТРАТЕГИИ' if old != new else ''
        print(f'  [{eid:>4}] {old:12s} -> {new:12s} ({n} матчей){mark} | {desc}')

    if not apply:
        print('\nDRY-RUN — запись не производилась. Для применения: --apply')
    await db.close()


if __name__ == '__main__':
    asyncio.run(main('--apply' in sys.argv))
