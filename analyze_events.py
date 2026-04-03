#!/usr/bin/env python3
"""Анализ качества определения сущностей в таблице events."""

import asyncio
import asyncpg
import json


async def analyze_events():
    conn = await asyncpg.connect(
        host='postgres',
        port=5432,
        database='postgres',
        user='postgres',
        password='postgres'
    )
    
    print('=== ДЕТАЛЬНЫЙ АНАЛИЗ СОБЫТИЙ ===\n')
    
    events = await conn.fetch('''
        SELECT id, event_time, description, layer, strategy, 
               matches, 
               CASE WHEN geom IS NOT NULL THEN ST_AsText(geom) ELSE NULL END as geom_text
        FROM events 
        ORDER BY event_time DESC 
        LIMIT 10
    ''')
    
    for i, ev in enumerate(events, 1):
        print(f'\n{"="*60}')
        print(f'СОБЫТИЕ #{i} (ID: {ev["id"]})')
        print(f'{"="*60}')
        print(f'Время: {ev["event_time"]}')
        print(f'Слой: {ev["layer"]}')
        print(f'Стратегия: {ev["strategy"]}')
        print(f'\nТекст:')
        print(f'  "{ev["description"]}"')
        
        matches = ev['matches']
        if matches and matches != '[]':
            # matches может быть строкой JSON или списком dict
            if isinstance(matches, str):
                matches = json.loads(matches)
            print(f'\nНайденные совпадения ({len(matches)} шт):')
            for m in matches[:5]:
                if isinstance(m, dict):
                    print(f'  * {m.get("name", "N/A")} (ID: {m.get("street_id", "N/A")}, сходство: {m.get("similarity", 0):.2f})')
                    if 'matched_part' in m:
                        print(f'    -> matched: "{m["matched_part"]}"')
                else:
                    print(f'  * {m}')
        else:
            print('\n[!] Совпадения не найдены')
        
        geom = ev['geom_text']
        if geom:
            if geom.startswith('POINT'):
                coords = geom.replace('POINT(', '').replace(')', '').split()
                print(f'\nГеометрия: POINT(lon={float(coords[0]):.4f}, lat={float(coords[1]):.4f})')
            elif geom.startswith('LINESTRING') or geom.startswith('POLYGON'):
                print(f'\nГеометрия: {geom[:80]}...')
    
    print(f'\n\n{"="*60}')
    print('АНАЛИЗ ПО СЛОЯМ')
    print('='*60)
    
    layer_stats = await conn.fetch('''
        SELECT layer, 
               COUNT(*) as total,
               COUNT(*) FILTER (WHERE matches != '[]'::jsonb) as with_matches,
               COUNT(*) FILTER (WHERE strategy = 'random') as random,
               COUNT(*) FILTER (WHERE strategy = 'single_match') as single,
               COUNT(*) FILTER (WHERE strategy = 'centroid') as centroid
        FROM events 
        GROUP BY layer 
        ORDER BY total DESC
    ''')
    
    header = f'{"Слой":<10} | {"Всего":<6} | {"С совп.":<8} | {"Random":<7} | {"Single":<7} | {"Centroid":<9}'
    print(header)
    print('-' * 65)
    for row in layer_stats:
        print(f'{row["layer"]:<10} | {row["total"]:<6} | {row["with_matches"]:<8} | {row["random"]:<7} | {row["single"]:<7} | {row["centroid"]:<9}')
    
    await conn.close()


if __name__ == '__main__':
    asyncio.run(analyze_events())
