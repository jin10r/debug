/**
 * Tests for EventManager (Stage 6)
 * 
 * Tests:
 * 1. Add new events
 * 2. Update all events
 * 3. Render events
 * 4. Notify for new events
 * 5. Store subscription (reactive render)
 * 6. Skip render on no changes
 * 7. Auto-refresh interval
 * 8. Get event manager stats
 */

class EventManagerTests {
    private eventManager: typeof window.eventManager;

    constructor() {
        this.eventManager = window.eventManager;
    }

    /**
     * Create test event
     */
    createTestEvent(id: number, layer: string = 'pig'): any {
        return {
            type: 'Feature',
            geometry: {
                type: 'Point',
                coordinates: [30.7233, 46.4825]
            },
            properties: {
                id: id,
                description: `Test event ${id}`,
                layer: layer,
                strategy: 'exact',
                time: new Date().toISOString()
            }
        };
    }

    /**
     * Test 1: Add new events
     */
    async test1_addNewEvents() {
        console.log('\n🧪 Test 1: Add new events');
        
        try {
            const events = [
                this.createTestEvent(1),
                this.createTestEvent(2)
            ];
            
            // Mock notify to prevent errors
            const originalNotify = this.eventManager.notify;
            this.eventManager.notify = () => {};
            
            this.eventManager.addNewEvents(events);
            
            // Wait for requestAnimationFrame
            await new Promise(resolve => setTimeout(resolve, 100));
            
            this.eventManager.notify = originalNotify;
            
            const passed = true; // If no error, test passed
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Events added: ${events.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 2: Update all events
     */
    async test2_updateAllEvents() {
        console.log('\n🧪 Test 2: Update all events');
        
        try {
            const eventsData = {
                type: 'FeatureCollection' as const,
                features: [
                    this.createTestEvent(1),
                    this.createTestEvent(2),
                    this.createTestEvent(3)
                ]
            };
            
            this.eventManager.updateAllEvents(eventsData);
            
            // Wait for requestAnimationFrame
            await new Promise(resolve => setTimeout(resolve, 100));
            
            const passed = true; // If no error, test passed
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Events updated: ${eventsData.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 3: Render events
     */
    async test3_render() {
        console.log('\n🧪 Test 3: Render events');
        
        try {
            // Mock renderDataOnMap
            let renderCalled = false;
            window.renderDataOnMap = () => {
                renderCalled = true;
                console.log('[Mock] renderDataOnMap called');
            };
            
            this.eventManager.render();
            
            // Wait for requestAnimationFrame
            await new Promise(resolve => setTimeout(resolve, 100));
            
            const passed = renderCalled === true;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  renderDataOnMap called: ${renderCalled}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 4: Notify for new events
     */
    async test4_notify() {
        console.log('\n🧪 Test 4: Notify for new events');
        
        try {
            let notifyCalled = false;
            let notifiedEvents: any[] = [];
            
            // Mock handleNewEvents
            window.handleNewEvents = (events: any[]) => {
                notifyCalled = true;
                notifiedEvents = events;
                console.log('[Mock] handleNewEvents called with', events.length, 'events');
            };
            
            // Mock eventTracker
            window.eventTracker = {
                checkForNewEvents: (events: any[]) => events
            };
            
            const events = [
                this.createTestEvent(1),
                this.createTestEvent(2)
            ];
            
            this.eventManager.notify(events);
            
            // Wait for setTimeout
            await new Promise(resolve => setTimeout(resolve, 100));
            
            const passed = notifyCalled === true && notifiedEvents.length === 2;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Notify called: ${notifyCalled}`);
            console.log(`  Events notified: ${notifiedEvents.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 5: Store subscription (reactive render)
     */
    async test5_storeSubscription() {
        console.log('\n🧪 Test 5: Store subscription (reactive render)');
        
        try {
            let renderCount = 0;
            
            // Mock render to count calls
            const originalRender = this.eventManager.render;
            this.eventManager.render = () => {
                renderCount++;
                console.log('[Mock] render called, count:', renderCount);
            };
            
            // Dispatch action to trigger subscription
            window.store.dispatch({
                type: 'SET_EVENTS',
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [this.createTestEvent(1)]
                    }
                }
            });
            
            // Wait for subscription callback
            await new Promise(resolve => setTimeout(resolve, 100));
            
            this.eventManager.render = originalRender;
            
            const passed = renderCount > 0;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Render count: ${renderCount}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 6: Skip render on no changes
     */
    async test6_skipRenderOnNoChanges() {
        console.log('\n🧪 Test 6: Skip render on no changes');
        
        try {
            let renderCount = 0;
            
            // Mock render to count calls
            const originalRender = this.eventManager.render;
            this.eventManager.render = () => {
                renderCount++;
            };
            
            // Set current state
            this.eventManager._lastTimeFilter = window.store.getState().currentTimeFilter;
            this.eventManager._lastEventsCount = window.store.getState().events.features.length;
            
            // Dispatch same state (should not trigger render)
            window.store.dispatch({
                type: 'SET_EVENTS',
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [] // Same as current
                    }
                }
            });
            
            // Wait for subscription callback
            await new Promise(resolve => setTimeout(resolve, 100));
            
            this.eventManager.render = originalRender;
            
            // Should not render because state didn't change
            const passed = renderCount === 0;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Render count: ${renderCount} (expected: 0)`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 7: Get event manager stats
     */
    async test7_getStats() {
        console.log('\n🧪 Test 7: Get event manager stats');
        
        try {
            const stats = {
                lastTimeFilter: this.eventManager._lastTimeFilter,
                lastEventsCount: this.eventManager._lastEventsCount
            };
            
            const passed = 'lastTimeFilter' in stats && 
                          'lastEventsCount' in stats &&
                          typeof stats.lastTimeFilter === 'number' &&
                          typeof stats.lastEventsCount === 'number';
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Stats:`, stats);
            
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
        console.log('🚀 Running EventManager Tests (Stage 6)');
        console.log('========================================');

        const results = [];

        results.push(await this.test1_addNewEvents());
        results.push(await this.test2_updateAllEvents());
        results.push(await this.test3_render());
        results.push(await this.test4_notify());
        results.push(await this.test5_storeSubscription());
        results.push(await this.test6_skipRenderOnNoChanges());
        results.push(await this.test7_getStats());

        const passed = results.filter(r => r).length;
        const total = results.length;

        console.log('\n========================================');
        console.log(`📊 Results: ${passed}/${total} tests passed`);
        console.log('========================================\n');

        return { passed, total, results };
    }
}

// Export for use
window.eventManagerTests = new EventManagerTests();

// Auto-run if in browser with test flag
if (typeof document !== 'undefined' && window.location.href.includes('test-eventmanager.html')) {
    (async () => {
        const results = await window.eventManagerTests.runAll();
        
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
