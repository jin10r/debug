/**
 * Комплексные тесты для LocalCache
 * Расширенное покрытие: TTL, очистка, валидация, edge cases
 */

class LocalCacheExtendedTests {
    constructor() {
        console.log('🚀 LocalCache Extended Tests инициализированы');
    }

    /**
     * Тест: Добавление события с TTL
     */
    test1_addEventWithTTL() {
        console.log('\n🧪 Тест 1: Добавление события с TTL');
        try {
            const cache = new window.LocalCache();
            const now = new Date();
            
            const event = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: {
                    id: 1,
                    layer: 'pig',
                    time: now.toISOString()
                }
            };
            
            const added = cache.addEvent(event);
            const stats = cache.getStats();
            
            const passed = added === true && stats.totalEvents === 1;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Добавлено: ${added}, Всего: ${stats.totalEvents}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Отклонение старых событий
     */
    test2_rejectOldEvents() {
        console.log('\n🧪 Тест 2: Отклонение старых событий (>60 мин)');
        try {
            const cache = new window.LocalCache();
            const oldTime = new Date(Date.now() - 70 * 60 * 1000); // 70 минут назад
            
            const oldEvent = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: {
                    id: 1,
                    layer: 'pig',
                    time: oldTime.toISOString()
                }
            };
            
            const added = cache.addEvent(oldEvent);
            const passed = added === false;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Старое событие отклонено: ${added === false}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Отклонение будущих событий
     */
    test3_rejectFutureEvents() {
        console.log('\n🧪 Тест 3: Отклонение будущих событий (>5 мин)');
        try {
            const cache = new window.LocalCache();
            const futureTime = new Date(Date.now() + 10 * 60 * 1000); // 10 минут в будущем
            
            const futureEvent = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: {
                    id: 1,
                    layer: 'pig',
                    time: futureTime.toISOString()
                }
            };
            
            const added = cache.addEvent(futureEvent);
            const passed = added === false;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Будущее событие отклонено: ${added === false}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Обновление существующего события
     */
    test4_updateExistingEvent() {
        console.log('\n🧪 Тест 4: Обновление существующего события');
        try {
            const cache = new window.LocalCache();
            const now = new Date();
            
            const event1 = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: {
                    id: 1,
                    layer: 'pig',
                    time: now.toISOString(),
                    description: 'Original'
                }
            };
            
            const event2 = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: {
                    id: 1,
                    layer: 'cops',
                    time: now.toISOString(),
                    description: 'Updated'
                }
            };
            
            cache.addEvent(event1);
            const addedSecond = cache.addEvent(event2);
            
            // Должно обновить, а не добавить
            const stats = cache.getStats();
            const passed = addedSecond === false && stats.totalEvents === 1;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Всего событий: ${stats.totalEvents}, Обновлено: ${addedSecond === false}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Очистка истёкших событий
     */
    test5_cleanupExpiredEvents() {
        console.log('\n🧪 Тест 5: Очистка истёкших событий');
        try {
            const cache = new window.LocalCache();
            const now = new Date();
            const oldTime = new Date(Date.now() - 70 * 60 * 1000);
            
            // Добавляем валидное событие
            const validEvent = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: {
                    id: 1,
                    layer: 'pig',
                    time: now.toISOString()
                }
            };
            
            // Добавляем истёкшее событие
            const expiredEvent = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: {
                    id: 2,
                    layer: 'cops',
                    time: oldTime.toISOString()
                }
            };
            
            cache.addEvent(validEvent);
            cache.addEvent(expiredEvent);
            
            const beforeCleanup = cache.getStats().totalEvents;
            const cleaned = cache.cleanupExpiredEvents();
            const afterCleanup = cache.getStats().totalEvents;
            
            const passed = cleaned === 1 && afterCleanup === 1;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  До: ${beforeCleanup}, Удалено: ${cleaned}, После: ${afterCleanup}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: ReplaceAllEvents
     */
    test6_replaceAllEvents() {
        console.log('\n🧪 Тест 6: ReplaceAllEvents');
        try {
            const cache = new window.LocalCache();
            
            const events = [
                {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                    properties: { id: 1, layer: 'pig', time: new Date().toISOString() }
                },
                {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                    properties: { id: 2, layer: 'cops', time: new Date().toISOString() }
                },
                {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                    properties: { id: 3, layer: 'bus', time: new Date().toISOString() }
                }
            ];
            
            const replaced = cache.replaceAllEvents(events);
            const stats = cache.getStats();
            
            const passed = replaced === 3 && stats.totalEvents === 3;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Заменено: ${replaced}, Всего: ${stats.totalEvents}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: GetEventsByTimeFilter
     */
    test7_getEventsByTimeFilter() {
        console.log('\n🧪 Тест 7: GetEventsByTimeFilter');
        try {
            const cache = new window.LocalCache();
            const now = Date.now();
            
            const events = [
                {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                    properties: { id: 1, layer: 'pig', time: new Date(now - 10 * 60 * 1000).toISOString() }
                },
                {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                    properties: { id: 2, layer: 'cops', time: new Date(now - 30 * 60 * 1000).toISOString() }
                },
                {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                    properties: { id: 3, layer: 'bus', time: new Date(now - 50 * 60 * 1000).toISOString() }
                }
            ];
            
            cache.replaceAllEvents(events);
            
            // Фильтр 15 минут
            const filtered15 = cache.getEventsByTimeFilter(15);
            // Фильтр 60 минут
            const filtered60 = cache.getEventsByTimeFilter(60);
            
            const passed = filtered15.features.length <= filtered60.features.length;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  15мин: ${filtered15.features.length}, 60мин: ${filtered60.features.length}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Clear
     */
    test8_clear() {
        console.log('\n🧪 Тест 8: Clear');
        try {
            const cache = new window.LocalCache();
            
            const event = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: { id: 1, layer: 'pig', time: new Date().toISOString() }
            };
            
            cache.addEvent(event);
            cache.clear();
            
            const stats = cache.getStats();
            const passed = stats.totalEvents === 0 && stats.eventsById === 0;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Событий после очистки: ${stats.totalEvents}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Валидация GeoJSON
     */
    test9_validateGeoJSON() {
        console.log('\n🧪 Тест 9: Валидация GeoJSON');
        try {
            const cache = new window.LocalCache();
            
            // Невалидные данные
            const invalidData = { foo: 'bar' };
            const isValid = cache.isValidGeoJSON(invalidData);
            
            const passed = isValid === false;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Невалидные данные отклонены: ${isValid === false}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: GetEventId с разными полями
     */
    test10_getEventIdVariants() {
        console.log('\n🧪 Тест 10: GetEventId варианты');
        try {
            const cache = new window.LocalCache();
            
            const testCases = [
                { properties: { id: 1 }, expected: 1 },
                { properties: { event_id: 2 }, expected: 2 },
                { properties: { _id: 3 }, expected: 3 },
                { properties: { uid: 4 }, expected: 4 },
                { properties: {}, expected: null },
                { properties: null, expected: null }
            ];
            
            let allPassed = true;
            
            for (const testCase of testCases) {
                const event = {
                    type: 'Feature',
                    geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                    properties: testCase.properties
                };
                
                const result = cache.getEventId(event);
                if (result !== testCase.expected) {
                    allPassed = false;
                    console.log(`  ❌ Провал для ${JSON.stringify(testCase.properties)}: ${result} !== ${testCase.expected}`);
                }
            }
            
            console.log(`  Результат: ${allPassed ? '✅ PASS' : '❌ FAIL'}`);
            return allPassed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Запуск всех тестов
     */
    async runAll() {
        console.log('🚀 Запуск LocalCache Extended Tests');
        console.log('====================================');
        
        const results = [];
        
        results.push(this.test1_addEventWithTTL());
        results.push(this.test2_rejectOldEvents());
        results.push(this.test3_rejectFutureEvents());
        results.push(this.test4_updateExistingEvent());
        results.push(this.test5_cleanupExpiredEvents());
        results.push(this.test6_replaceAllEvents());
        results.push(this.test7_getEventsByTimeFilter());
        results.push(this.test8_clear());
        results.push(this.test9_validateGeoJSON());
        results.push(this.test10_getEventIdVariants());
        
        const passed = results.filter(r => r).length;
        const total = results.length;
        
        console.log('\n====================================');
        console.log(`📊 Результаты: ${passed}/${total} тестов пройдено`);
        console.log('====================================\n');
        
        return { passed, total, results };
    }
}

// Экспорт
window.localCacheExtendedTests = new LocalCacheExtendedTests();
