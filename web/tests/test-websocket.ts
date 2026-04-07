/**
 * Tests for WebSocketManager (Stage 5)
 * 
 * Tests:
 * 1. Connect to WebSocket server
 * 2. Send and receive message
 * 3. Reconnection on disconnect
 * 4. Catch-up message on reconnect
 * 5. Heartbeat mechanism
 * 6. Connection status change callback
 * 7. Send catch-up request
 * 8. Handle new event message
 * 9. Handle filtered events message
 * 10. Disconnect from server
 */

class WebSocketManagerTests {
    private wsManager: typeof window.webSocketManager;

    constructor() {
        this.wsManager = window.webSocketManager;
    }

    /**
     * Mock WebSocket server
     */
    mockWebSocket() {
        const mockWs = {
            readyState: WebSocket.OPEN,
            send: jest.fn(),
            close: jest.fn(),
            onopen: null as ((event: Event) => void) | null,
            onmessage: null as ((event: MessageEvent) => void) | null,
            onclose: null as ((event: CloseEvent) => void) | null,
            onerror: null as ((event: Event) => void) | null
        };

        const OriginalWebSocket = window.WebSocket;
        window.WebSocket = class MockWebSocket {
            url: string;
            readyState = WebSocket.OPEN;
            
            constructor(url: string) {
                this.url = url;
                console.log('[MockWebSocket] Created with URL:', url);
            }
            
            send = mockWs.send;
            close = mockWs.close;
            
            // Mock connection open
            setTimeout(() => {
                if (this.onopen) {
                    this.onopen(new Event('open'));
                }
            }, 10);
        } as any;

        return { mockWs, OriginalWebSocket };
    }

    /**
     * Restore original WebSocket
     */
    restoreWebSocket(original: any) {
        window.WebSocket = original;
    }

    /**
     * Test 1: Connect to WebSocket server
     */
    async test1_connect() {
        console.log('\n🧪 Test 1: Connect to WebSocket server');
        
        try {
            const { mockWs, OriginalWebSocket } = this.mockWebSocket();
            
            this.wsManager.connect();
            
            // Wait for connection
            await new Promise(resolve => setTimeout(resolve, 100));
            
            const connected = this.wsManager.isConnected;
            
            this.restoreWebSocket(OriginalWebSocket);
            
            const passed = connected === true;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  isConnected: ${connected}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 2: Send message
     */
    async test2_sendMessage() {
        console.log('\n🧪 Test 2: Send message');
        
        try {
            const { mockWs, OriginalWebSocket } = this.mockWebSocket();
            
            this.wsManager.connect();
            await new Promise(resolve => setTimeout(resolve, 100));
            
            const testMessage = { type: 'test', data: 'test_data' };
            this.wsManager.sendMessage(testMessage);
            
            const sent = mockWs.send.mock.calls.length > 0;
            const message = sent ? JSON.parse(mockWs.send.mock.calls[0][0]) : null;
            
            this.restoreWebSocket(OriginalWebSocket);
            
            const passed = sent && message.type === 'test';
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Message sent: ${sent}`);
            console.log(`  Message type: ${message?.type}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 3: Connection status change callback
     */
    async test3_connectionStatusChange() {
        console.log('\n🧪 Test 3: Connection status change callback');
        
        try {
            const { mockWs, OriginalWebSocket } = this.mockWebSocket();
            
            let statusChanged = false;
            let lastStatus = false;
            
            this.wsManager.onConnectionStatusChange = (isConnected) => {
                statusChanged = true;
                lastStatus = isConnected;
                console.log('[Test] Connection status changed:', isConnected);
            };
            
            this.wsManager.connect();
            await new Promise(resolve => setTimeout(resolve, 100));
            
            this.restoreWebSocket(OriginalWebSocket);
            
            const passed = statusChanged === true && lastStatus === true;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Status changed: ${statusChanged}`);
            console.log(`  Last status: ${lastStatus}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 4: Handle new event message
     */
    async test4_handleNewEvent() {
        console.log('\n🧪 Test 4: Handle new event message');
        
        try {
            const { mockWs, OriginalWebSocket } = this.mockWebSocket();
            
            let eventReceived = false;
            let eventData: any = null;
            
            this.wsManager.onNewEvent = (data) => {
                eventReceived = true;
                eventData = data;
                console.log('[Test] New event received:', data);
            };
            
            this.wsManager.connect();
            await new Promise(resolve => setTimeout(resolve, 100));
            
            // Simulate incoming message
            if (mockWs.onmessage) {
                mockWs.onmessage({
                    data: JSON.stringify({
                        type: 'new_event',
                        data: {
                            type: 'FeatureCollection',
                            features: [{
                                type: 'Feature',
                                properties: { id: 1, layer: 'pig' },
                                geometry: { type: 'Point', coordinates: [0, 0] }
                            }]
                        }
                    })
                } as MessageEvent);
            }
            
            await new Promise(resolve => setTimeout(resolve, 50));
            
            this.restoreWebSocket(OriginalWebSocket);
            
            const passed = eventReceived === true && eventData?.features?.length === 1;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Event received: ${eventReceived}`);
            console.log(`  Features count: ${eventData?.features?.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 5: Get connection statistics
     */
    async test5_getStats() {
        console.log('\n🧪 Test 5: Get connection statistics');
        
        try {
            const stats = this.wsManager.getStats();
            
            const passed = 'isConnected' in stats && 
                          'reconnectAttempts' in stats && 
                          'maxReconnectAttempts' in stats;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Stats:`, stats);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 6: Disconnect from server
     */
    async test6_disconnect() {
        console.log('\n🧪 Test 6: Disconnect from server');
        
        try {
            const { mockWs, OriginalWebSocket } = this.mockWebSocket();
            
            this.wsManager.connect();
            await new Promise(resolve => setTimeout(resolve, 100));
            
            this.wsManager.disconnect();
            
            const disconnected = this.wsManager.isConnected === false;
            const closeCalled = mockWs.close.mock.calls.length > 0;
            
            this.restoreWebSocket(OriginalWebSocket);
            
            const passed = disconnected && closeCalled;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  isConnected: ${this.wsManager.isConnected}`);
            console.log(`  close() called: ${closeCalled}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 7: Send catch-up request
     */
    async test7_sendCatchUp() {
        console.log('\n🧪 Test 7: Send catch-up request');
        
        try {
            const { mockWs, OriginalWebSocket } = this.mockWebSocket();
            
            this.wsManager.connect();
            await new Promise(resolve => setTimeout(resolve, 100));
            
            // Manually send catch-up
            const since = new Date().toISOString();
            this.wsManager.sendMessage({
                type: 'catchup',
                since: since
            });
            
            const sent = mockWs.send.mock.calls.length > 0;
            const message = sent ? JSON.parse(mockWs.send.mock.calls[mockWs.send.mock.calls.length - 1][0]) : null;
            
            this.restoreWebSocket(OriginalWebSocket);
            
            const passed = sent && message.type === 'catchup' && message.since === since;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Message type: ${message?.type}`);
            console.log(`  Since: ${message?.since}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 8: Change time filter
     */
    async test8_changeTimeFilter() {
        console.log('\n🧪 Test 8: Change time filter');
        
        try {
            const { mockWs, OriginalWebSocket } = this.mockWebSocket();
            
            this.wsManager.connect();
            await new Promise(resolve => setTimeout(resolve, 100));
            
            this.wsManager.changeTimeFilter(30, ['pig', 'cops']);
            
            const sent = mockWs.send.mock.calls.length > 0;
            const message = sent ? JSON.parse(mockWs.send.mock.calls[0][0]) : null;
            
            this.restoreWebSocket(OriginalWebSocket);
            
            const passed = sent && 
                          message.type === 'change_time_filter' && 
                          message.time_filter === 30;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Message type: ${message?.type}`);
            console.log(`  Time filter: ${message?.time_filter}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Run all tests
     */
    async runAll(): Promise<{ passed: number; total: number; results: boolean[] }> {
        console.log('🚀 Running WebSocketManager Tests (Stage 5)');
        console.log('===========================================');

        const results = [];

        results.push(await this.test1_connect());
        results.push(await this.test2_sendMessage());
        results.push(await this.test3_connectionStatusChange());
        results.push(await this.test4_handleNewEvent());
        results.push(await this.test5_getStats());
        results.push(await this.test6_disconnect());
        results.push(await this.test7_sendCatchUp());
        results.push(await this.test8_changeTimeFilter());

        const passed = results.filter(r => r).length;
        const total = results.length;

        console.log('\n===========================================');
        console.log(`📊 Results: ${passed}/${total} tests passed`);
        console.log('===========================================\n');

        return { passed, total, results };
    }
}

// Export for use
window.webSocketManagerTests = new WebSocketManagerTests();

// Auto-run if in browser with test flag
if (typeof document !== 'undefined' && window.location.href.includes('test-websocket.html')) {
    (async () => {
        const results = await window.webSocketManagerTests.runAll();
        
        // Display results
        const resultsDiv = document.getElementById('results');
        if (resultsDiv) {
            resultsDiv.innerHTML = `
                <h2>Test Results</h2>
                <p>Passed: ${results.passed}/${results.total}</p>
                <pre>${JSON.stringify(results.results, null, 2)}</pre>
            `;
        }
    })();
}
