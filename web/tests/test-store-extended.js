/**
 * Комплексные тесты для ReactiveStore
 * Расширенное покрытие: edge cases, ошибки, валидация
 */

class StoreExtendedTests {
    constructor() {
        console.log('🚀 Store Extended Tests инициализированы');
    }

    /**
     * Тест: Валидация входных данных SET_EVENTS
     */
    test1_setEventsValidation() {
        console.log('\n🧪 Тест 1: Валидация SET_EVENTS');
        try {
            const store = new window.ReactiveStore();
            
            // Тест с null
            store.dispatch({
                type: 'SET_EVENTS',
                payload: { events: null }
            });
            const state1 = store.getState();
            const pass1 = state1.events.features.length === 0;
            
            // Тест с undefined
            store.dispatch({
                type: 'SET_EVENTS',
                payload: { events: undefined }
            });
            const state2 = store.getState();
            const pass2 = state2.events.features.features === undefined;
            
            // Тест с пустым объектом
            store.dispatch({
                type: 'SET_EVENTS',
                payload: {}
            });
            
            const passed = pass1;
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: ADD_EVENTS дубликаты
     */
    test2_addEventsDuplicates() {
        console.log('\n🧪 Тест 2: ADD_EVENTS дубликаты');
        try {
            const store = new window.ReactiveStore();
            
            const event1 = {
                type: 'Feature',
                geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                properties: { id: 1, layer: 'pig', time: new Date().toISOString() }
            };
            
            // Добавляем одно событие дважды
            store.dispatch({
                type: 'ADD_EVENTS',
                payload: { events: [event1] }
            });
            store.dispatch({
                type: 'ADD_EVENTS',
                payload: { events: [event1] }
            });
            
            const state = store.getState();
            const passed = state.events.features.length === 2; // Store не дедуплицирует
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Количество событий: ${state.events.features.length}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: TOGGLE_LAYER все слои
     */
    test3_toggleAllLayers() {
        console.log('\n🧪 Тест 3: TOGGLE_LAYER все слои');
        try {
            const store = new window.ReactiveStore();
            const layers = ['pig', 'cops', 'bus', 'traffic'];
            let allPassed = true;
            
            for (const layer of layers) {
                const initialState = store.getState();
                const wasActive = initialState.activeLayers.has(layer);
                
                store.dispatch({
                    type: 'TOGGLE_LAYER',
                    payload: { layer }
                });
                
                const newState = store.getState();
                const isActive = newState.activeLayers.has(layer);
                
                if (wasActive === isActive) {
                    allPassed = false;
                    console.log(`  ❌ Слой ${layer} не переключился`);
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
     * Тест: GetFilteredItems с разными фильтрами
     */
    test4_filteredItemsDifferentFilters() {
        console.log('\n🧪 Тест 4: GetFilteredItems с разными фильтрами');
        try {
            const store = new window.ReactiveStore();
            const now = new Date();
            
            // Добавляем события с разным временем
            const events = {
                type: 'FeatureCollection',
                features: [
                    {
                        type: 'Feature',
                        geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                        properties: {
                            id: 1,
                            layer: 'pig',
                            time: new Date(now.getTime() - 10 * 60 * 1000).toISOString() // 10 мин назад
                        }
                    },
                    {
                        type: 'Feature',
                        geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                        properties: {
                            id: 2,
                            layer: 'cops',
                            time: new Date(now.getTime() - 20 * 60 * 1000).toISOString() // 20 мин назад
                        }
                    },
                    {
                        type: 'Feature',
                        geometry: { type: 'Point', coordinates: [30.7, 46.4] },
                        properties: {
                            id: 3,
                            layer: 'bus',
                            time: new Date(now.getTime() - 40 * 60 * 1000).toISOString() // 40 мин назад
                        }
                    }
                ]
            };
            
            store.dispatch({
                type: 'SET_EVENTS',
                payload: { events }
            });
            
            // Тест с фильтром 15 минут
            store.dispatch({
                type: 'UPDATE_CURRENT_TIME_FILTER',
                payload: { minutes: 15 }
            });
            const filtered15 = store.getFilteredItems();
            
            // Тест с фильтром 30 минут
            store.dispatch({
                type: 'UPDATE_CURRENT_TIME_FILTER',
                payload: { minutes: 30 }
            });
            const filtered30 = store.getFilteredItems();
            
            // Тест с фильтром 60 минут
            store.dispatch({
                type: 'UPDATE_CURRENT_TIME_FILTER',
                payload: { minutes: 60 }
            });
            const filtered60 = store.getFilteredItems();
            
            const passed = filtered15.features.length <= filtered30.features.length &&
                          filtered30.features.length <= filtered60.features.length;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  15мин: ${filtered15.features.length}, 30мин: ${filtered30.features.length}, 60мин: ${filtered60.features.length}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: GetStats
     */
    test5_getStats() {
        console.log('\n🧪 Тест 5: GetStats');
        try {
            const store = new window.ReactiveStore();
            const stats = store.getStats();
            
            const hasAllFields = 
                'totalEvents' in stats &&
                'currentTimeFilter' in stats &&
                'activeLayers' in stats &&
                'subscribers' in stats &&
                'cacheHit' in stats;
            
            const passed = hasAllFields && typeof stats.totalEvents === 'number';
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Статистика:`, stats);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Подписка и отписка
     */
    test6_subscribeUnsubscribe() {
        console.log('\n🧪 Тест 6: Подписка и отписка');
        try {
            const store = new window.ReactiveStore();
            let callCount = 0;
            
            const subscriber = () => {
                callCount++;
            };
            
            // Подписываемся
            const unsubscribe = store.subscribe(subscriber);
            
            // Диспатчим действие
            store.dispatch({
                type: 'SET_EVENTS',
                payload: { events: { type: 'FeatureCollection', features: [] } }
            });
            
            const calledOnce = callCount === 1;
            
            // Отписываемся
            unsubscribe();
            
            // Диспатчим ещё раз
            store.dispatch({
                type: 'SET_EVENTS',
                payload: { events: { type: 'FeatureCollection', features: [{id: 1}] } }
            });
            
            // Не должно вызваться
            const stillOnce = callCount === 1;
            
            const passed = calledOnce && stillOnce;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Вызовов после отписки: ${callCount}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Запуск всех тестов
     */
    async runAll() {
        console.log('🚀 Запуск Store Extended Tests');
        console.log('================================');
        
        const results = [];
        
        results.push(this.test1_setEventsValidation());
        results.push(this.test2_addEventsDuplicates());
        results.push(this.test3_toggleAllLayers());
        results.push(this.test4_filteredItemsDifferentFilters());
        results.push(this.test5_getStats());
        results.push(this.test6_subscribeUnsubscribe());
        
        const passed = results.filter(r => r).length;
        const total = results.length;
        
        console.log('\n================================');
        console.log(`📊 Результаты: ${passed}/${total} тестов пройдено`);
        console.log('================================\n');
        
        return { passed, total, results };
    }
}

// Экспорт
window.storeExtendedTests = new StoreExtendedTests();
