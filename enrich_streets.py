#!/usr/bin/env python3
"""Анализ событий для обогащения базы улиц."""

import asyncio
import asyncpg
import re
from collections import Counter


async def analyze_events():
    conn = await asyncpg.connect(
        host='postgres',
        port=5432,
        database='postgres',
        user='postgres',
        password='postgres'
    )
    
    # Получаем все события
    events = await conn.fetch("""
        SELECT id, description, layer, strategy, matches
        FROM events 
        WHERE description IS NOT NULL 
        AND strategy != 'random'
        ORDER BY event_time DESC
    """)
    
    # Извлекаем слова с заглавной буквы
    cap_words = []
    for ev in events:
        desc = ev['description']
        # Все слова с заглавной буквы (минимум 3 символа)
        words = re.findall(r'\b([А-Я][А-Яа-яA-Za-zЁё]{2,})\b', desc)
        cap_words.extend(words)
    
    # Считаем частоту
    word_freq = Counter(cap_words)
    
    # Получаем существующие улицы
    streets = await conn.fetch("SELECT id, names FROM streets")
    existing_names = set()
    for st in streets:
        for name in st['names']:
            existing_names.add(name.lower())
            # Добавляем возможные падежные формы
            existing_names.add(name.lower() + 'ой')
            existing_names.add(name.lower() + 'ской')
            existing_names.add(name.lower() + 'скую')
            existing_names.add(name.lower() + 'ской')
    
    # Фильтруем: оставляем только те, которых нет в базе
    print("="*70)
    print("ПОТЕНЦИАЛЬНЫЕ УЛИЦЫ ДЛЯ ДОБАВЛЕНИЯ")
    print("="*70)
    print(f"\nВсего слов с заглавной буквы: {len(cap_words)}")
    print(f"Уникальных слов: {len(word_freq)}")
    print(f"Уже есть в базе (с падежами): {len(existing_names)}")
    
    # Исключения (не улицы)
    exclusions = {
        'Сообщить', 'Заявка', 'Подписка', 'Помочь', 'каналу', 'видео',
        'Официально', 'Восстановление', 'работы', 'приложения', 'Резерв',
        'Ожидается', 'Высоцкого', 'Бреуса', 'Глушко', 'АГА', 'Эльдорадо',
        'ТЦК', 'Украина', 'Россия', 'Киев', 'Одесса',
        'Форда', 'Транзит', 'Рено', 'Вито', 'Мерседес', 'Тойота',
        'Вольво', 'Скания', 'МАН', 'ДАФ', 'Ивеко',
    }
    
    potential_streets = []
    for word, freq in word_freq.most_common(100):
        if word.lower() not in existing_names and word not in exclusions and len(word) >= 4:
            potential_streets.append((word, freq))
    
    print(f"\nПотенциальные новые улицы (топ-50):")
    print("-"*70)
    for word, freq in potential_streets[:50]:
        print(f"  {word:<30} → {freq} упоминаний")
    
    # Анализируем падежные формы существующих улиц
    print("\n" + "="*70)
    print("ПАДЕЖНЫЕ ФОРМЫ СУЩЕСТВУЮЩИХ УЛИЦ")
    print("="*70)
    
    # Ищем в текстах падежные формы
    case_forms = {}
    for ev in events:
        desc = ev['description']
        for st in streets:
            for name in st['names']:
                # Ищем основную форму
                if name in desc:
                    continue
                # Ищем падежные формы (окончания)
                patterns = [
                    f'{name[:-1]}ой',  # родительный/дательный/предложный
                    f'{name[:-1]}ую',  # винительный
                    f'{name[:-1]}ой',  # творительный
                ]
                for pattern in patterns:
                    if pattern in desc:
                        if name not in case_forms:
                            case_forms[name] = set()
                        case_forms[name].add(pattern)
    
    print("\nНайденные падежные формы:")
    for street_name, forms in list(case_forms.items())[:20]:
        print(f"  {street_name:<30} → {', '.join(forms)}")
    
    # Генерируем SQL для обновления
    print("\n" + "="*70)
    print("SQL ДЛЯ ОБНОВЛЕНИЯ БАЗЫ")
    print("="*70)
    
    print("\n-- 1. Добавить потенциальные улицы")
    for word, freq in potential_streets[:20]:
        if freq >= 2:
            print(f"INSERT INTO streets (names, geom) VALUES (ARRAY['{word}'], ST_SetSRID(ST_MakePoint(30.8, 46.5), 4326)) ON CONFLICT DO NOTHING;")
    
    print("\n-- 2. Добавить падежные формы к существующим")
    for street_name, forms in list(case_forms.items())[:20]:
        forms_list = ', '.join([f"'{f}'" for f in forms])
        print(f"UPDATE streets SET names = names || ARRAY[{forms_list}] WHERE '{street_name}' = ANY(names);")
    
    await conn.close()


if __name__ == '__main__':
    asyncio.run(analyze_events())
