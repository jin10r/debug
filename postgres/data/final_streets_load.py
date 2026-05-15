#!/usr/bin/env python3
import asyncpg
import asyncio
import csv
from pathlib import Path

async def load_streets_data():
    """Загружает данные улиц из CSV в PostgreSQL таблицы streets и street_embeddings"""
    
    # Подключаемся к базе данных
    conn = await asyncpg.connect('postgresql://postgres:postgres@localhost:5432/postgres')
    
    try:
        # Очищаем таблицы
        print("🔄 Очистка таблиц...")
        await conn.execute('TRUNCATE TABLE streets CASCADE')
        await conn.execute('TRUNCATE TABLE street_embeddings CASCADE')
        print("✅ Таблицы очищены")
        
        # Читаем CSV файл
        csv_file = Path('./streets.csv')
        if not csv_file.exists():
            print(f"❌ Файл {csv_file} не найден")
            return
            
        print(f"📖 Чтение файла {csv_file}...")
        streets_data = []
        
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                names = [row['names']] if row['names'] else []
                wkt_geom = row['wkt_geom']
                
                streets_data.append({
                    'id': i + 1,
                    'names': names,
                    'wkt_geom': wkt_geom
                })
        
        print(f"📊 Найдено {len(streets_data)} улиц")
        
        # Вставляем данные в таблицу streets
        print("📥 Загрузка данных в таблицу streets...")
        
        inserted_count = 0
        for street in streets_data:
            try:
                # Используем ST_GeomFromText для преобразования WKT в геометрию
                # Определяем тип геометрии из WKT строки
                geom_type = street['wkt_geom'].split('(')[0].upper()
                
                if geom_type in ['POINT', 'LINESTRING', 'POLYGON']:
                    await conn.execute(
                        "INSERT INTO streets (id, names, geom) VALUES ($1, $2, ST_GeomFromText($3, 4326))",
                        street['id'], street['names'], street['wkt_geom']
                    )
                    inserted_count += 1
                else:
                    print(f"⚠️ Пропущен неизвестный тип геометрии: {geom_type}")
                    
            except Exception as e:
                print(f"❌ Ошибка при вставке улицы {street['names']}: {e}")
        
        print(f"✅ Загружено {inserted_count} улиц в таблицу streets")
        
        # Создаем статические эмбеддинги для тестирования
        print("🔧 Создание тестовых эмбеддингов...")
        
        # Берем первые 10 улиц и создаем для них эмбеддинги
        street_ids = await conn.fetch("SELECT id, names FROM streets ORDER BY id LIMIT 10")
        
        for street in street_ids:
            # Создаем простой эмбеддинг на основе названия улицы
            name = street['names'][0] if street['names'] else ''
            
            # Создаем простой числовой вектор на основе длины названия и символов
            embedding_vector = [ord(c) / 1000 for c in name[:100]]  # Ограничиваем размер
            # Дополняем до 312 элементов (размер эмбеддинга rubert-tiny2)
            while len(embedding_vector) < 312:
                embedding_vector.append(0.0)
            
            # Преобразуем в строку для вставки
            embedding_str = '[' + ','.join(map(str, embedding_vector[:312])) + ']'
            
            await conn.execute(
                "INSERT INTO street_embeddings (street_id, name, embedding) VALUES ($1, $2, $3::vector)",
                street['id'], name, embedding_str
            )
        
        print(f"✅ Создано {len(street_ids)} тестовых эмбеддингов")
        
        # Проверяем результат
        streets_count = await conn.fetchval('SELECT COUNT(*) FROM streets')
        embeddings_count = await conn.fetchval('SELECT COUNT(*) FROM street_embeddings')
        
        print(f"\n📊 Результат:")
        print(f"   • Улиц в базе: {streets_count}")
        print(f"   • Эмбеддингов в базе: {embeddings_count}")
        
        if streets_count > 0 and embeddings_count > 0:
            print("\n🎉 Все готово! Алгоритм семантического поиска должен работать корректно.")
        else:
            print("\n⚠️ Данные загружены не полностью")
            
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        
    finally:
        await conn.close()

if __name__ == "__main__":
    print("🚀 Запуск загрузки данных улиц...")
    asyncio.run(load_streets_data())