#!/usr/bin/env python3
"""Сравнение стратегий геометрии «до/после» фиксов (P0-1/P0-2/P1-1/P2-2).

Прогоняет те же сообщения из events_export1.csv через НОВЫЙ пайплайн
(geo_matcher + semantic_resolver + process_candidates) и сравнивает
результирующую стратегию с сохранённой в экспорте (старый код).

SemanticMatcher (rubert-tiny2) НЕ подключается — соответствует текущему
проду, где он отключён (settings.similarity.semantic_enabled=False, см.
docs/GEOMETRY_ANALYSIS.md §13).

Запуск (в контейнере processor, где есть rapidfuzz/pymorphy3 и доступ к БД):
  docker cp scripts/compare_geofixes.py processor:/tmp/
  docker cp events_export1.csv processor:/tmp/
  docker exec processor python /tmp/compare_geofixes.py /tmp/events_export1.csv
"""
import asyncio
import csv
import json
import struct
import sys

sys.path.insert(0, '/app')  # контейнер: WORKDIR=/app (core/ processor/ там)

from core.db.db_adapter import DBAdapter
from core.utils.text_preprocessor import is_promotional
from processor.geo_matcher import GeoMatcher
from processor.morphology import Morphology
from processor.phonetic_index import PhoneticIndex
from processor.word_tokenizer import tokenize


def gtype_from_wkb_hex(hexstr: str) -> str:
    """Тип геометрии из WKB-hex (с SRID-флагом)."""
    if not hexstr:
        return 'NULL'
    b = bytes.fromhex(hexstr)
    t = struct.unpack('<I', b[1:5])[0] & 0x0FFFFFFF
    return {1: 'POINT', 2: 'LINESTRING', 3: 'POLYGON', 4: 'MULTIPOINT',
            5: 'MULTILINESTRING', 6: 'MULTIPOLYGON'}.get(t, f'T:{t}')


async def main(csv_path: str) -> None:
    rows = list(csv.DictReader(open(csv_path, encoding='utf-8')))
    print(f'Загружено событий: {len(rows)}')

    db = DBAdapter()
    if not await db.connect():
        print('DB connect failed')
        return
    pool = db.pool

    # Инициализация пайплайна — зеркалит main.py._init_nlp()
    morph = Morphology()
    index = PhoneticIndex(morph)
    matcher = GeoMatcher(morph, index)
    if not await matcher.initialize(pool):
        print('GeoMatcher init failed')
        return

    from core.settings import settings
    qo = settings.question_overlay
    center_lon, center_lat, radius = qo.center_lon, qo.center_lat, qo.radius
    max_text_length = settings.parser.max_text_length

    stats_old: dict = {}
    stats_new: dict = {}
    results = {}  # id -> {old_strat, new_strat, new_geom}

    for r in rows:
        eid = r['id']
        old_strategy = r['strategy']
        stats_old[old_strategy] = stats_old.get(old_strategy, 0) + 1

        raw_text = r['description'] or ''
        tokens = tokenize(raw_text)
        lemmas = morph.lemmatize_tokens(tokens)

        promotional = is_promotional(raw_text)
        if promotional or not raw_text or len(raw_text) > max_text_length:
            new_strategy, new_geom = 'random', 'POINT'
        else:
            entities = await matcher.find_geo(
                tokens=tokens, lemmas=lemmas, text=raw_text)
            geo_ids, geo_scores, geo_texts = [], [], []
            for ent in entities:
                if ent['geo_id'] not in geo_ids:
                    geo_ids.append(ent['geo_id'])
                    geo_scores.append(ent['score'])
                    geo_texts.append(ent['text'])
            if not geo_ids:
                new_strategy, new_geom = 'random', 'POINT'
            else:
                async with pool.acquire() as conn:
                    pc = await conn.fetchrow(
                        """SELECT ST_GeometryType(result_geom) AS gtype,
                                  result_strategy AS strat
                           FROM process_candidates_v2($1::int[], $2::float[],
                                                       $3::text[], $4::varchar)""",
                        geo_ids, [float(s) for s in geo_scores], geo_texts,
                        None)
                new_strategy = pc['strat']
                new_geom = (pc['gtype'] or '?').replace('ST_', '') if pc['gtype'] else '?'

        stats_new[new_strategy] = stats_new.get(new_strategy, 0) + 1
        results[eid] = {
            'old': old_strategy, 'new': new_strategy,
            'new_geom': new_geom, 'desc': (r['description'] or '')[:60],
        }

    print('\n=== СТРАТЕГИИ: ДО (старый код) vs ПОСЛЕ (новый код) ===')
    print(f'{"стратегия":15s} {"до":>6s} {"после":>6s} {"дельта":>7s}')
    for k in sorted(set(stats_old) | set(stats_new)):
        print(f'{k:15s} {stats_old.get(k, 0):6d} {stats_new.get(k, 0):6d} '
              f'{stats_new.get(k, 0) - stats_old.get(k, 0):+7d}')

    changed = [v for v in results.values() if v['old'] != v['new']]
    print(f'\n=== Изменения стратегий: {len(changed)} ===')
    for v in sorted(changed, key=lambda x: results.keys()):
        print(f"  {v['desc'][:50]:50s} {v['old']:12s} -> {v['new']:12s} ({v['new_geom']})")

    multi_old = sum(
        1 for r in rows
        if r['strategy'] == 'single_match'
        and (lambda m: len(m) >= 2)(json.loads(r['matches'] or '[]'))
    )
    multi_new = sum(1 for v in results.values() if v['old'] == 'single_match'
                    and v['new'] in ('intersection', 'midpoint'))
    print(f'\n=== single_match с 2+ матчами ===')
    print(f'  было: {multi_old}')
    print(f'  из них перешли в intersection/midpoint: {multi_new}')

    print('\n=== intersection: геометрия ДО (из экспорта) vs ПОСЛЕ (симуляция) ===')
    for r in rows:
        if r['strategy'] == 'intersection':
            old_g = gtype_from_wkb_hex(r['geom'])
            v = results.get(r['id'], {})
            new_g = v.get('new_geom', '?')
            new_s = v.get('new', '?')
            print(f"  [{r['id']:>4}] {old_g:12s} -> {new_g:12s} "
                  f"(стратегия {new_s}) {r['description'][:45]}")

    await db.close()


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else 'events_export1.csv'))
