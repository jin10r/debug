#!/usr/bin/env python3
"""Пакетный повторный анализ всех событий НОВЫМ пайплайном (матчер v2 + арбитр v2).

Сравнение стратегий: сохранённые в БД (старый код — Tier-2 мёртв) vs новый код
(Tier-2 жив, NOISE-gap, len-guard 25%, суффиксы порядковых; SQL: anti-list
3000м, wc-scatter по якорям пар). Источник — живая БД events.

Запуск (в контейнере processor, где rapidfuzz/pymorphy3 и доступ к БД):
  docker cp scripts/reanalyze_events.py processor:/tmp/
  docker exec processor python /tmp/reanalyze_events.py
"""
import asyncio
import sys

sys.path.insert(0, '/app')  # контейнер: WORKDIR=/app

from core.db.db_adapter import DBAdapter
from core.settings import settings
from core.utils.text_preprocessor import is_promotional, truncate_for_geo
from processor.geo_matcher import GeoMatcher
from processor.morphology import Morphology
from processor.phonetic_index import PhoneticIndex
from processor.word_tokenizer import tokenize

TOP_N = 5  # R-PR10: Top-5 кандидатов в process_candidates_v2


async def main() -> None:
    db = DBAdapter()
    if not await db.connect():
        print('DB connect failed')
        return
    pool = db.pool

    morph = Morphology()
    index = PhoneticIndex(morph)
    matcher = GeoMatcher(morph, index)
    if not await matcher.initialize(pool):
        print('GeoMatcher init failed')
        return

    rows = await pool.fetch(
        "SELECT id, description, strategy FROM events ORDER BY id"
    )
    print(f'Событий: {len(rows)}')

    stats_old: dict = {}
    stats_new: dict = {}
    changed = []

    for r in rows:
        old_strategy = r['strategy']
        stats_old[old_strategy] = stats_old.get(old_strategy, 0) + 1

        raw = truncate_for_geo(
            r['description'] or '', settings.parser.max_text_length
        )
        tokens = tokenize(raw)
        lemmas = morph.lemmatize_tokens(tokens)

        if is_promotional(raw) or not raw:
            new_strategy = 'random'
        else:
            entities = await matcher.find_geo(
                tokens=tokens, lemmas=lemmas, text=raw
            )
            geo_ids, geo_scores, geo_texts = [], [], []
            for ent in entities:
                if ent['geo_id'] not in geo_ids:
                    geo_ids.append(ent['geo_id'])
                    geo_scores.append(ent['score'])
                    geo_texts.append(ent['text'])
            if not geo_ids:
                new_strategy = 'random'
            else:
                geo_ids, geo_scores, geo_texts = (
                    geo_ids[:TOP_N], geo_scores[:TOP_N], geo_texts[:TOP_N]
                )
                async with pool.acquire() as conn:
                    pc = await conn.fetchrow(
                        """SELECT result_strategy AS strat
                           FROM process_candidates_v2($1::int[], $2::float[],
                                                       $3::text[], $4::varchar)""",
                        geo_ids,
                        [float(s) for s in geo_scores],
                        geo_texts,
                        None,
                    )
                new_strategy = pc['strat']

        stats_new[new_strategy] = stats_new.get(new_strategy, 0) + 1
        if old_strategy != new_strategy:
            changed.append(
                (r['id'], old_strategy, new_strategy, r['description'])
            )

    print('\n=== СТРАТЕГИИ: было (live) vs стало (новый пайплайн) ===')
    print(f'{"стратегия":18s} {"было":>6s} {"стало":>6s} {"дельта":>7s}')
    for k in sorted(set(stats_old) | set(stats_new)):
        print(f'{k:18s} {stats_old.get(k, 0):6d} {stats_new.get(k, 0):6d} '
              f'{stats_new.get(k, 0) - stats_old.get(k, 0):+7d}')

    print(f'\n=== Изменения стратегий: {len(changed)} ===')
    for eid, old, new, desc in sorted(changed):
        print(f'  [{eid:>4}] {old:16s} -> {new:16s} | {(desc or "")[:55]}')

    await db.close()


if __name__ == '__main__':
    asyncio.run(main())
