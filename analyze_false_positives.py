#!/usr/bin/env python3
"""Детальный анализ ложных срабатываний в таблице events."""

import asyncio
import asyncpg
import json
from typing import Dict, List, Tuple


async def analyze_false_positives():
    conn = await asyncpg.connect(
        host='postgres',
        port=5432,
        database='postgres',
        user='postgres',
        password='postgres'
    )

    print('='*80)
    print('АНАЛИЗ ЛОЖНЫХ СРАБАТЫВАНИЙ В TABLE events')
    print('='*80)

    # Получаем все события
    events = await conn.fetch('''
        SELECT 
            id, 
            event_time, 
            description, 
            layer, 
            strategy,
            matches,
            CASE WHEN geom IS NOT NULL THEN ST_AsText(geom) ELSE NULL END as geom_text
        FROM events
        ORDER BY id
    ''')

    false_positives = []
    true_positives = []
    ambiguous = []

    print('\n📊 ПОДРОБНЫЙ АНАЛИЗ КАЖДОГО СОБЫТИЯ\n')

    for ev in events:
        desc = ev['description']
        matches = ev['matches']
        
        if isinstance(matches, str):
            try:
                matches = json.loads(matches)
            except:
                matches = []
        
        print(f'\n{"="*80}')
        print(f'СОБЫТИЕ #{ev["id"]} | Слой: {ev["layer"]} | Стратегия: {ev["strategy"]}')
        print(f'{"="*80}')
        print(f'Текст: "{desc}"')
        
        if not matches or matches == []:
            print(f'❌ СОВПАДЕНИЙ НЕ НАЙДЕНО → random стратегия')
            false_positives.append({
                'id': ev['id'],
                'desc': desc,
                'reason': 'no_matches',
                'layer': ev['layer']
            })
            continue

        # Анализируем каждое совпадение
        print(f'\nНайдено совпадений: {len(matches)}')
        
        fp_count = 0
        tp_count = 0
        
        for m in matches:
            name = m.get('name', 'N/A')
            similarity = m.get('similarity', 0)
            
            # Проверяем, есть ли слово из matches в исходном тексте
            desc_lower = desc.lower()
            name_lower = name.lower()
            
            # Извлекаем ключевые слова из имени
            name_words = set(name_lower.split())
            desc_words = set(desc_lower.split())
            
            # Проверяем перекрытие слов
            overlap = name_words & desc_words
            overlap_ratio = len(overlap) / len(name_words) if name_words else 0
            
            # Определяем тип совпадения
            is_false_positive = False
            fp_reason = ''
            
            # 1. Проверка: слово вообще не присутствует в тексте
            if overlap_ratio < 0.3 and similarity < 0.75:
                is_false_positive = True
                fp_reason = 'low_overlap_low_similarity'
            
            # 2. Проверка: очень низкое сходство (<0.70)
            elif similarity < 0.70:
                is_false_positive = True
                fp_reason = 'very_low_similarity'
            
            # 3. Проверка: короткое слово с низким сходством
            elif len(name) < 6 and similarity < 0.80:
                is_false_positive = True
                fp_reason = 'short_word_low_similarity'
            
            if is_false_positive:
                fp_count += 1
                print(f'  ❌ FP: {name} (сходство: {similarity:.2f}, причина: {fp_reason})')
            else:
                tp_count += 1
                print(f'  ✅ TP: {name} (сходство: {similarity:.2f})')
        
        # Классифицируем событие
        if fp_count > 0 and tp_count == 0:
            false_positives.append({
                'id': ev['id'],
                'desc': desc,
                'matches': matches,
                'reason': 'all_false_positives',
                'layer': ev['layer']
            })
        elif fp_count > 0:
            ambiguous.append({
                'id': ev['id'],
                'desc': desc,
                'matches': matches,
                'fp_count': fp_count,
                'tp_count': tp_count,
                'layer': ev['layer']
            })
        else:
            true_positives.append({
                'id': ev['id'],
                'desc': desc,
                'matches': matches,
                'layer': ev['layer']
            })

    # Итоговая статистика
    print('\n\n' + '='*80)
    print('📈 ИТОГОВАЯ СТАТИСТИКА')
    print('='*80)
    
    total = len(events)
    tp_events = len(true_positives)
    fp_events = len(false_positives)
    amb_events = len(ambiguous)
    
    print(f'\nВсего событий: {total}')
    print(f'✅ Чистые true positives: {tp_events} ({tp_events/total*100:.1f}%)')
    print(f'❌ False positives: {fp_events} ({fp_events/total*100:.1f}%)')
    print(f'⚠️  Смешанные (частично FP): {amb_events} ({amb_events/total*100:.1f}%)')

    # Анализ причин ложных срабатываний
    print('\n\n' + '='*80)
    print('🔍 АНАЛИЗ ПРИЧИН ЛОЖНЫХ СРАБАТЫВАНИЙ')
    print('='*80)

    # Группируем по причинам
    fp_reasons = {}
    for fp in false_positives:
        reason = fp.get('reason', 'unknown')
        if reason not in fp_reasons:
            fp_reasons[reason] = []
        fp_reasons[reason].append(fp)

    for reason, fps in fp_reasons.items():
        print(f'\n\n📌 Причина: {reason}')
        print(f'   Количество: {len(fps)}')
        print(f'   Примеры:')
        for fp in fps[:5]:
            print(f'   - #{fp["id"]}: "{fp["desc"][:60]}..."')
            if 'matches' in fp:
                for m in fp['matches']:
                    print(f'     → {m.get("name")} (сходство: {m.get("similarity", 0):.2f})')

    # Детальный анализ смешанных событий
    if ambiguous:
        print('\n\n' + '='*80)
        print('⚠️  СМЕШАННЫЕ СОБЫТИЯ (есть и TP, и FP)')
        print('='*80)
        
        for amb in ambiguous:
            print(f'\nСобытие #{amb["id"]}: "{amb["desc"][:80]}..."')
            print(f'  TP: {amb["tp_count"]}, FP: {amb["fp_count"]}')
            for m in amb['matches']:
                name = m.get('name', 'N/A')
                sim = m.get('similarity', 0)
                desc_lower = amb['desc'].lower()
                name_lower = name.lower()
                
                # Проверяем наличие
                if name_lower in desc_lower or any(w in desc_lower for w in name_lower.split()):
                    print(f'  ✅ {name} ({sim:.2f})')
                else:
                    print(f'  ❌ {name} ({sim:.2f}) — FALSE POSITIVE')

    # Рекомендации по улучшению
    print('\n\n' + '='*80)
    print('💡 РЕКОМЕНДАЦИИ ПО УЛУЧШЕНИЮ')
    print('='*80)

    recommendations = []

    # 1. Анализ стоп-слов
    print('\n1. СТОП-СЛОВА')
    print('   Проблема: некоторые слова в стоп-словах — реальные локации')
    print('   Решение: удалить "таврия", "бульвар", "аллея", "дорога" из стоп-слов')
    
    # 2. Порог сходства
    print('\n2. ПОРОГ СХОДСТВА')
    print('   Проблема: низкий порог (0.55) пропускает ложные совпадения')
    print('   Решение: поднять до 0.65-0.70 для уменьшения FP')
    
    # 3. Контекстная валидация
    print('\n3. КОНТЕКСТНАЯ ВАЛИДАЦИЯ')
    print('   Проблема: fuzzy matching находит слова, которых нет в тексте')
    print('   Решение: проверять overlap слов перед добавлением matches')
    
    # 4. Длина слова
    print('\n4. ДЛИНА СЛОВА')
    print('   Проблема: короткие слова (<5 символов) дают много FP')
    print('   Решение: увеличить min_word_length до 4-5')
    
    # 5. Layer detection
    print('\n5. ОПРЕДЕЛЕНИЕ СЛОЯ')
    print('   Проблема: много событий в слое "pig" с найденными улицами')
    print('   Решение: если найдены улицы, не ставить слой "pig"')

    await conn.close()


if __name__ == '__main__':
    asyncio.run(analyze_false_positives())
