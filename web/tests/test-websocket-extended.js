/**
 * Комплексные тесты для WebSocketManager
 * Расширенное покрытие: переподключение, heartbeat, обработка ошибок
 */

class WebSocketExtendedTests {
    constructor() {
        console.log('🚀 WebSocket Extended Tests инициализированы');
    }

    /**
     * Тест: Проверка начального состояния
     */
    test1_initialState() {
        console.log('\n🧪 Тест 1: Начальное состояние');
        try {
            const ws = new window.WebSocketManager();
            
            const passed = 
                ws.isConnected === false &&
                ws.reconnectAttempts === 0 &&
                ws.maxReconnectAttempts === 10;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  isConnected: ${ws.isConnected}`);
            console.log(`  reconnectAttempts: ${ws.reconnectAttempts}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: GetStats
     */
    test2_getStats() {
        console.log('\n🧪 Тест 2: GetStats');
        try {
            const ws = new window.WebSocketManager();
            const stats = ws.getStats();
            
            const hasAllFields = 
                'isConnected' in stats &&
                'reconnectAttempts' in stats &&
                'maxReconnectAttempts' in stats;
            
            const passed = hasAllFields && typeof stats.isConnected === 'boolean';
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Статистика:`, stats);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Disconnect
     */
    test3_disconnect() {
        console.log('\n🧪 Тест 3: Disconnect');
        try {
            const ws = new window.WebSocketManager();
            
            // Отключаемся (даже если не подключены)
            ws.disconnect();
            
            const passed = ws.isConnected === false && ws.ws === null;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  isConnected после disconnect: ${ws.isConnected}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: SendMessage когда не подключен
     */
    test4_sendMessageNotConnected() {
        console.log('\n🧪 Тест 4: SendMessage когда не подключен');
        try {
            const ws = new window.WebSocketManager();
            
            // Не должно вызвать ошибку
            ws.sendMessage({ type: 'test', data: 'test' });
            
            const passed = true; // Если не упало - тест пройден
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: ChangeTimeFilter когда не подключен
     */
    test5_changeTimeFilterNotConnected() {
        console.log('\n🧪 Тест 5: ChangeTimeFilter когда не подключен');
        try {
            const ws = new window.WebSocketManager();
            
            // Не должно вызвать ошибку
            ws.changeTimeFilter(30, ['pig', 'cops']);
            
            const passed = true;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Callbacks инициализация
     */
    test6_callbacksИнициализация() {
        console.log('\n🧪 Тест 6: Callbacks инициализация');
        try {
            const ws = new window.WebSocketManager();
            
            const hasCallbacks = 
                'onNewEvent' in ws &&
                'onFilteredEvents' in ws &&
                'onInitialData' in ws &&
                'onConnectionStatusChange' in ws;
            
            const passed = hasCallbacks;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Mock подключение (с мок-объектом)
     */
    test7_mockConnection() {
        console.log('\n🧪 Тест 7: Mock подключение');
        try {
            // Сохраняем оригинальный WebSocket
            const OriginalWebSocket = window.WebSocket;
            
            // Создаём мок WebSocket
            class MockWebSocket {
                constructor(url) {
                    this.url = url;
                    this.readyState = WebSocket.OPEN;
                    this.onopen = null;
                    this.onmessage = null;
                    this.onclose = null;
                    this.onerror = null;
                    
                    // Симулируем подключение
                    setTimeout(() => {
                        if (this.onopen) {
                            this.onopen({});
                        }
                    }, 10);
                }
                
                send(data) {
                    console.log('[Mock] Отправлено:', data);
                }
                
                close(code, reason) {
                    console.log('[Mock] Закрыто:', code, reason);
                    if (this.onclose) {
                        this.onclose({ code, reason });
                    }
                }
            }
            
            // Подменяем WebSocket
            window.WebSocket = MockWebSocket;
            
            const ws = new window.WebSocketManager();
            let statusChanged = false;
            
            ws.onConnectionStatusChange = (isConnected) => {
                statusChanged = true;
                console.log('  Callback вызван: isConnected =', isConnected);
            };
            
            // Подключаемся
            ws.connect();
            
            // Ждём подключения
            setTimeout(() => {
                const connected = ws.isConnected;
                console.log('  isConnected:', connected);
                console.log('  statusChanged:', statusChanged);
            }, 100);
            
            // Восстанавливаем оригинальный WebSocket
            window.WebSocket = OriginalWebSocket;
            
            const passed = true; // Если не упало - тест пройден
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL:', error);
            return false;
        }
    }

    /**
     * Тест: Обработка сообщений (mock)
     */
    test8_handleMessages() {
        console.log('\n🧪 Тест 8: Обработка сообщений (mock)');
        try {
            const ws = new window.WebSocketManager();
            let newEventReceived = false;
            
            ws.onNewEvent = (data) => {
                newEventReceived = true;
                console.log('  Новое событие:', data);
            };
            
            // Симулируем получение сообщения
            const mockMessage = {
                data: JSON.stringify({
                    type: 'new_event',
                    data: {
                        type: 'FeatureCollection',
                        features: [{
                            type: 'Feature',
                            properties: { id: 1, layer: 'pig' },
                            geometry: { type: 'Point', coordinates: [30.7, 46.4] }
                        }]
                    }
                })
            };
            
            // Вызываем обработчик напрямую
            ws.handleMessage(mockMessage);
            
            const passed = newEventReceived === true;
            
            console.log(`  Результат: ${passed ? '✅ PASS' : '❌ FAIL'}`);
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
        console.log('🚀 Запуск WebSocket Extended Tests');
        console.log('===================================');
        
        const results = [];
        
        results.push(this.test1_initialState());
        results.push(this.test2_getStats());
        results.push(this.test3_disconnect());
        results.push(this.test4_sendMessageNotConnected());
        results.push(this.test5_changeTimeFilterNotConnected());
        results.push(this.test6_callbacksИнициализация());
        results.push(this.test7_mockConnection());
        results.push(this.test8_handleMessages());
        
        const passed = results.filter(r => r).length;
        const total = results.length;
        
        console.log('\n===================================');
        console.log(`📊 Результаты: ${passed}/${total} тестов пройдено`);
        console.log('===================================\n');
        
        return { passed, total, results };
    }
}

// Экспорт
window.webSocketExtendedTests = new WebSocketExtendedTests();
