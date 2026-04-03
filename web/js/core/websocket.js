// websocket.js — управление WebSocket соединением для получения новых событий в реальном времени

class WebSocketManager {
    constructor() {
        this.ws = null;
        this.isConnected = false;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000; // начальная задержка переподключения
        this.reconnectMultiplier = 1.5; // множитель для экспоненциального роста задержки
        this.heartbeatInterval = null;
        this.heartbeatTimeout = null;
        this.heartbeatTimeoutDuration = 15000; // 15 секунд
        this.heartbeatPingDuration = 30000; // 30 секунд

        // Коллбэки для обработки сообщений
        this.onNewEvent = null;
        this.onFilteredEvents = null;
        this.onInitialData = null;
        this.onConnectionStatusChange = null;
        
        // Флаг для отслеживания первого подключения
        this.firstConnection = true;
    }

    /**
     * Подключиться к WebSocket серверу
     */
    connect() {
        if (this.isConnected) {
            return;
        }

        try {
            // Определяем URL для WebSocket соединения
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            // Используем тот же хост и порт, что и текущий URL (включая нестандартные порты)
            const currentUrl = new URL(window.location.href);
            const wsPort = currentUrl.port ? ':' + currentUrl.port : '';

            // Получаем токен доступа или initData для аутентификации
            const accessToken = sessionStorage.getItem('access_token');
            const initData = window.Telegram?.WebApp?.initData;
            const devMode = sessionStorage.getItem('dev_mode') === 'true';

            // Allow connection in dev mode without auth
            if (!accessToken && !initData && !devMode) {
                console.error('[WS] No authentication available (no access token or initData)');
                return;
            }

            // URL без параметров аутентификации (для безопасности)
            const wsUrl = `${protocol}//${currentUrl.hostname}${wsPort}/ws`;

            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                this.isConnected = true;
                this.reconnectAttempts = 0; // Сбросить счетчик попыток переподключения при успешном подключении

                // Отправить аутентификацию после подключения
                this.sendAuth();

                // Запустить проверку активности соединения
                this.startHeartbeat();

                // Уведомить об изменении статуса подключения
                if (this.onConnectionStatusChange) {
                    this.onConnectionStatusChange(true);
                }

                // Проверяем, пустой ли кеш (с защитой от неинициализированного localCache)
                if (!window.localCache || !window.localCache.masterGeoJSON) {
                    console.warn('[WS] localCache not initialized, requesting full refresh');
                    setTimeout(() => {
                        this.requestFullRefresh();
                    }, 100);
                    return;
                }

                if (window.localCache.masterGeoJSON.features.length === 0) {
                    // Если кеш пустой, запрашиваем все события за последние 60 минут
                    setTimeout(() => {
                        this.requestFullRefresh();
                    }, 100); // Небольшая задержка, чтобы дождаться полной инициализации
                } else {
                    // Если в кеше есть данные, запрашиваем только обновления после последнего события
                    setTimeout(() => {
                        this.requestUpdatesAfterLastEvent();
                    }, 100); // Небольшая задержка, чтобы дождаться полной инициализации
                }
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleMessage(data);
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
                }
            };

            this.ws.onclose = (event) => {
                this.isConnected = false;

                // Остановить проверку активности
                this.stopHeartbeat();

                // Уведомить об изменении статуса подключения
                if (this.onConnectionStatusChange) {
                    this.onConnectionStatusChange(false);
                }

                // Попробовать переподключиться, если соединение закрыто не по инициативе клиента
                if (event.code !== 1000 && this.reconnectAttempts < this.maxReconnectAttempts) {
                    this.scheduleReconnect();
                }
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        } catch (error) {
            console.error('Failed to create WebSocket connection:', error);
            this.scheduleReconnect();
        }
    }

    /**
     * Отправить аутентификацию после подключения
     */
    sendAuth() {
        const accessToken = sessionStorage.getItem('access_token');
        const initData = window.Telegram?.WebApp?.initData;
        const devMode = sessionStorage.getItem('dev_mode') === 'true';

        if (accessToken) {
            console.log('[WS] Authenticating with access token');
            this.ws.send(JSON.stringify({
                type: 'auth',
                token_type: 'bearer',
                token: accessToken
            }));
        } else if (initData) {
            console.log('[WS] Authenticating with initData');
            this.ws.send(JSON.stringify({
                type: 'auth',
                token_type: 'telegram_init_data',
                init_data: initData
            }));
        } else if (devMode) {
            // Dev mode: connect without auth
            console.log('[WS] Dev mode - connecting without authentication');
            this.ws.send(JSON.stringify({
                type: 'auth',
                token_type: 'none',
                dev_mode: true
            }));
        } else {
            console.error('[WS] No authentication credentials available');
        }
    }

    /**
     * Запросить полное обновление данных (все события за последние 60 минут)
     */
    async requestFullRefresh() {
        try {
            console.log('[WS] Requesting full refresh from /api/events/snapshot');
            const devMode = sessionStorage.getItem('dev_mode') === 'true';
            console.log('[WS] Dev mode:', devMode);

            // Запрашиваем все события за последние 60 минут
            const response = await fetch('/api/events/snapshot', {
                method: 'GET',
                headers: window.cacheUtility.getAuthHeaders() // Используем аутентификацию из cache utility
            });

            console.log('[WS] Response status:', response.status);

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();
            console.log('[WS] Received data:', {
                version: result.version,
                max_event_id: result.max_event_id,
                updated_at: result.updated_at,
                features_count: result.data?.features?.length || 0
            });

            // Заменяем все события в кеше, подавляя уведомления при начальной загрузке
            if (result.data && result.data.features && window.localCache) {
                const count = window.localCache.replaceAllEvents(result.data.features, true);
                console.log('[WS] Events replaced in localCache:', count);

                // Обновляем отображение
                window.eventManager.updateAllEvents(window.localCache.getAllEvents());
                console.log('[WS] UI updated with', window.localCache.getAllEvents().features.length, 'events');
            } else {
                console.warn('[WS] No data to update or localCache not available');
            }
        } catch (error) {
            console.error('[WS] Error during full refresh:', error);
        }
    }

    /**
     * Запросить обновления событий после последнего события в кеше
     */
    async requestUpdatesAfterLastEvent() {
        if (!window.localCache) {
            console.warn('[WS] localCache not initialized, cannot request updates');
            return;
        }

        try {
            // Получаем максимальное время из кеша
            const maxEventTime = window.localCache.getMaxEventTime();
            
            // Запрашиваем обновления после последнего времени
            const response = await fetch('/api/events', {
                method: 'POST',
                headers: window.cacheUtility.getAuthHeaders(), // Используем аутентификацию из cache utility
                body: JSON.stringify({
                    since: maxEventTime ? maxEventTime.toISOString() : new Date().toISOString()
                })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();
            
            // Добавляем новые события в кеш, подавляя уведомления при начальной загрузке
            if (result.data && result.data.features) {
                const addedCount = window.localCache.addEvents(result.data.features, true);
                
                // Обновляем отображение
                window.eventManager.updateAllEvents(window.localCache.getAllEvents());
            }
        } catch (error) {
            console.error('Error during updates request:', error);
        }
    }

    /**
     * Обработать входящее сообщение
     */
    handleMessage(data) {
        switch (data.type) {
            case 'new_event':
                if (data.data && data.data.features && data.data.features.length > 0) {
                    const event = data.data.features[0];
                }
                if (this.onNewEvent) {
                    this.onNewEvent(data.data);
                }
                break;

            case 'filtered_events':
                if (this.onFilteredEvents) {
                    this.onFilteredEvents(data.data, data.time_filter);
                }
                break;

            case 'initial_data':
                if (data.data?.features?.length > 0) {
                    const firstEvent = data.data.features[0];
                }
                if (this.onInitialData) {
                    this.onInitialData(data.data);
                }
                break;

            case 'pong':
                // Ответ на ping, сбросить таймаут
                this.resetHeartbeatTimeout();
                break;

            default:
        }
    }

    /**
     * Отправить сообщение через WebSocket
     */
    sendMessage(message) {
        if (this.isConnected && this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        } else {
            console.warn('WebSocket is not connected, cannot send message:', message);
        }
    }

    /**
     * Запланировать переподключение
     */
    scheduleReconnect() {
        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(this.reconnectMultiplier, this.reconnectAttempts - 1);


        setTimeout(() => {
            this.connect();
        }, delay);
    }

    /**
     * Запустить проверку активности соединения
     */
    startHeartbeat() {
        // Отправляем ping каждые 30 секунд
        this.heartbeatInterval = setInterval(() => {
            if (this.isConnected) {
                this.sendMessage({ type: 'ping' });

                // Установить таймаут ожидания pong
                this.heartbeatTimeout = setTimeout(() => {
                    if (this.ws) {
                        this.ws.close(1000, 'Heartbeat timeout');
                    }
                }, this.heartbeatTimeoutDuration);
            }
        }, this.heartbeatPingDuration);
    }

    /**
     * Сбросить таймаут проверки активности
     */
    resetHeartbeatTimeout() {
        if (this.heartbeatTimeout) {
            clearTimeout(this.heartbeatTimeout);
            this.heartbeatTimeout = null;
        }
    }

    /**
     * Остановить проверку активности соединения
     */
    stopHeartbeat() {
        if (this.heartbeatInterval) {
            clearInterval(this.heartbeatInterval);
            this.heartbeatInterval = null;
        }

        if (this.heartbeatTimeout) {
            clearTimeout(this.heartbeatTimeout);
            this.heartbeatTimeout = null;
        }
    }

    /**
     * Изменить фильтр времени
     */
    changeTimeFilter(timeFilter, layers = []) {
        if (this.isConnected) {
            this.sendMessage({
                type: 'change_time_filter',
                time_filter: timeFilter,
                layers: layers
            });
        }
    }

    /**
     * Отключиться от WebSocket сервера
     */
    disconnect() {
        if (this.ws) {
            this.stopHeartbeat();
            this.ws.close(1000, 'Client initiated disconnect');
        }
    }
}

// Создаем глобальный экземпляр WebSocketManager
window.webSocketManager = new WebSocketManager();

// Функция для инициализации WebSocket соединения
window.initializeWebSocket = function() {
    // Устанавливаем обработчики
    window.webSocketManager.onNewEvent = (eventData) => {
        console.log('[WS] onNewEvent received:', eventData);

        if (!eventData) {
            console.warn('[WS] onNewEvent: empty eventData');
            return;
        }

        let event = null;
        if (eventData.type === 'Feature' && eventData.properties) {
            event = eventData;
        } else if (Array.isArray(eventData.features) && eventData.features.length > 0) {
            event = eventData.features[0];
        }

        if (!event) {
            console.warn('[WS] onNewEvent: could not normalize eventData to Feature');
            return;
        }

        console.log('[WS] Processing event:', event.properties?.id);

        try {
            // localCache.addEvent handles synchronization with store, rendering, and notifications
            const added = window.localCache.addEvent(event);
            if (added) {
                console.log('[WS] New event added to cache:', event.properties?.id);
                console.log('[WS] Cache now has', window.localCache.getAllEvents().features.length, 'events');
            } else {
                console.log('[WS] Event updated in cache:', event.properties?.id);
            }
        } catch (error) {
            console.error('[WS] Error processing new event:', error);
        }
    };

    window.webSocketManager.onFilteredEvents = (eventsData, timeFilter) => {
        if (!window.localCache) {
            console.warn('[WS] localCache not initialized, cannot update filtered events');
            return;
        }
        // Заменяем все события в кеше
        window.localCache.replaceAllEvents(eventsData.features);
        window.eventManager.updateAllEvents(window.localCache.getAllEvents());
        
        // Сбрасываем флаг первой загрузки для трекера событий
        if (window.eventTracker) {
            window.eventTracker.isFirstLoad = false;
        }
    };

    window.webSocketManager.onInitialData = (initialData) => {
        if (!window.localCache) {
            console.warn('[WS] localCache not initialized, cannot update initial data');
            return;
        }
        // Заменяем все события в кеше, подавляя уведомления при начальной загрузке
        window.localCache.replaceAllEvents(initialData.features, true);
        window.eventManager.updateAllEvents(window.localCache.getAllEvents());
        
        // Сбрасываем флаг первой загрузки для трекера событий
        if (window.eventTracker) {
            window.eventTracker.isFirstLoad = false;
            console.log('[WS] Initial data loaded, eventTracker ready for notifications');
        }
    };

    window.webSocketManager.onConnectionStatusChange = (isConnected) => {
        // Обновляем статус соединения
        if (typeof window.updateOnlineStatus === 'function') {
            window.updateOnlineStatus(isConnected);
        }
    };

    // Подключаемся к WebSocket
    window.webSocketManager.connect();
};

// Останавливаем старый polling, так как теперь используем только WebSocket
if (window.pollingInterval) {
    clearInterval(window.pollingInterval);
}

// Обработчик видимости вкладки для Telegram WebView
// При возврате из фона принудительно обновляем отображение
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        console.log('[WS] Tab became visible, forcing render');
        // Принудительно обновляем отображение при возврате из фона
        if (typeof window.eventManager !== 'undefined' && typeof window.eventManager.render === 'function') {
            window.eventManager.render();
        }
    }
});

// Обработчик активации окна (для iOS Safari и старых браузеров)
window.addEventListener('focus', () => {
    console.log('[WS] Window focused, forcing render');
    if (typeof window.eventManager !== 'undefined' && typeof window.eventManager.render === 'function') {
        window.eventManager.render();
    }
});

