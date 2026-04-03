/**
 * Tests for ReactiveStore (Stage 4)
 * 
 * Tests:
 * 1. Set events
 * 2. Add events
 * 3. Update time filter
 * 4. Toggle layer
 * 5. Get filtered items (memoization)
 * 6. Subscribe to state changes
 * 7. Get max event ID and time
 * 8. Clear events
 * 9. Cache invalidation
 * 10. Get store statistics
 */

import { ReactiveStore, ActionType } from '../js/core/store';
import { EventFeature, StoreState } from '../js/types/geojson';

class ReactiveStoreTests {
    private store: ReactiveStore;
    private testPrefix = 'test_store_';

    constructor() {
        this.store = new ReactiveStore();
    }

    /**
     * Create test event
     */
    createTestEvent(id: number, time?: Date, layer: string = 'pig'): EventFeature {
        return {
            type: 'Feature',
            geometry: {
                type: 'Point',
                coordinates: [30.7233, 46.4825]
            },
            properties: {
                id: id,
                description: `Test event ${id}`,
                layer: layer as 'pig' | 'cops' | 'bus',
                strategy: 'exact',
                time: (time || new Date()).toISOString()
            }
        };
    }

    /**
     * Reset store state
     */
    resetStore(): void {
        this.store.dispatch({
            type: ActionType.CLEAR_EVENTS,
            payload: {}
        });
        this.store.dispatch({
            type: ActionType.UPDATE_CURRENT_TIME_FILTER,
            payload: { minutes: 30 }
        });
    }

    /**
     * Test 1: Set events
     */
    async test1_setEvents() {
        console.log('\n🧪 Test 1: Set events');
        
        this.resetStore();
        
        try {
            const events = {
                type: 'FeatureCollection' as const,
                features: [
                    this.createTestEvent(1),
                    this.createTestEvent(2),
                    this.createTestEvent(3)
                ]
            };
            
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: { events }
            });
            
            const state = this.store.getState();
            const passed = state.events.features.length === 3;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Total events: ${state.events.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 2: Add events
     */
    async test2_addEvents() {
        console.log('\n🧪 Test 2: Add events');
        
        this.resetStore();
        
        try {
            // Set initial events
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [this.createTestEvent(1)]
                    }
                }
            });
            
            // Add new events
            this.store.dispatch({
                type: ActionType.ADD_EVENTS,
                payload: {
                    events: [
                        this.createTestEvent(2),
                        this.createTestEvent(3)
                    ]
                }
            });
            
            const state = this.store.getState();
            const passed = state.events.features.length === 3;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Total events: ${state.events.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 3: Update time filter
     */
    async test3_updateTimeFilter() {
        console.log('\n🧪 Test 3: Update time filter');
        
        this.resetStore();
        
        try {
            this.store.dispatch({
                type: ActionType.UPDATE_CURRENT_TIME_FILTER,
                payload: { minutes: 15 }
            });
            
            const state = this.store.getState();
            const passed = state.currentTimeFilter === 15;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Time filter: ${state.currentTimeFilter} minutes`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 4: Toggle layer
     */
    async test4_toggleLayer() {
        console.log('\n🧪 Test 4: Toggle layer');
        
        this.resetStore();
        
        try {
            // Disable 'cops' layer
            this.store.dispatch({
                type: ActionType.TOGGLE_LAYER,
                payload: { layer: 'cops' }
            });
            
            let state = this.store.getState();
            const copsDisabled = !state.activeLayers.has('cops');
            
            // Re-enable 'cops' layer
            this.store.dispatch({
                type: ActionType.TOGGLE_LAYER,
                payload: { layer: 'cops' }
            });
            
            state = this.store.getState();
            const copsEnabled = state.activeLayers.has('cops');
            
            const passed = copsDisabled && copsEnabled;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Active layers: ${Array.from(state.activeLayers).join(', ')}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 5: Get filtered items (memoization)
     */
    async test5_getFilteredItemsMemoization() {
        console.log('\n🧪 Test 5: Get filtered items (memoization)');
        
        this.resetStore();
        
        try {
            // Set events
            const now = new Date();
            const recentTime = new Date(now.getTime() - 15 * 60 * 1000); // 15 min ago
            const oldTime = new Date(now.getTime() - 45 * 60 * 1000); // 45 min ago
            
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [
                            this.createTestEvent(1, recentTime),
                            this.createTestEvent(2, oldTime)
                        ]
                    }
                }
            });
            
            // Set filter to 30 minutes
            this.store.dispatch({
                type: ActionType.UPDATE_CURRENT_TIME_FILTER,
                payload: { minutes: 30 }
            });
            
            // First call (cache miss)
            const filtered1 = this.store.getFilteredItems();
            
            // Second call (cache hit)
            const filtered2 = this.store.getFilteredItems();
            
            // Should return cached result (same reference)
            const passed = filtered1.features.length === 1 && 
                          filtered1 === filtered2;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Filtered events: ${filtered1.features.length}`);
            console.log(`  Cache hit: ${filtered1 === filtered2}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 6: Subscribe to state changes
     */
    async test6_subscribeToStateChanges() {
        console.log('\n🧪 Test 6: Subscribe to state changes');
        
        this.resetStore();
        
        try {
            let notificationCount = 0;
            let lastState: StoreState | null = null;
            
            // Subscribe
            const unsubscribe = this.store.subscribe((state) => {
                notificationCount++;
                lastState = state;
            });
            
            // Dispatch action
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [this.createTestEvent(1)]
                    }
                }
            });
            
            // Unsubscribe
            unsubscribe();
            
            // Dispatch another action (should not notify)
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [this.createTestEvent(2)]
                    }
                }
            });
            
            const passed = notificationCount === 1 && lastState !== null;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Notifications: ${notificationCount}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 7: Get max event ID and time
     */
    async test7_getMaxEventIdAndTime() {
        console.log('\n🧪 Test 7: Get max event ID and time');
        
        this.resetStore();
        
        try {
            const now = new Date();
            const time1 = new Date(now.getTime() - 60 * 60 * 1000);
            const time2 = new Date(now.getTime() - 30 * 60 * 1000);
            
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [
                            this.createTestEvent(10, time1),
                            this.createTestEvent(20, time2)
                        ]
                    }
                }
            });
            
            const maxId = this.store.getMaxEventId();
            const maxTime = this.store.getMaxEventTime();
            
            const passed = maxId === 20 && maxTime?.getTime() === time2.getTime();
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Max ID: ${maxId}`);
            console.log(`  Max time: ${maxTime?.toISOString()}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 8: Clear events
     */
    async test8_clearEvents() {
        console.log('\n🧪 Test 8: Clear events');
        
        this.resetStore();
        
        try {
            // Set events
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [
                            this.createTestEvent(1),
                            this.createTestEvent(2)
                        ]
                    }
                }
            });
            
            // Clear
            this.store.dispatch({
                type: ActionType.CLEAR_EVENTS,
                payload: {}
            });
            
            const state = this.store.getState();
            const passed = state.events.features.length === 0;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Total events: ${state.events.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 9: Cache invalidation
     */
    async test9_cacheInvalidation() {
        console.log('\n🧪 Test 9: Cache invalidation');
        
        this.resetStore();
        
        try {
            // Set events and get filtered (cache)
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [this.createTestEvent(1)]
                    }
                }
            });
            
            const filtered1 = this.store.getFilteredItems();
            
            // Add new event (should invalidate cache)
            this.store.dispatch({
                type: ActionType.ADD_EVENTS,
                payload: {
                    events: [this.createTestEvent(2)]
                }
            });
            
            const filtered2 = this.store.getFilteredItems();
            
            // Cache should be invalidated, different result
            const passed = filtered1.features.length === 1 && 
                          filtered2.features.length === 2;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Before: ${filtered1.features.length} events`);
            console.log(`  After: ${filtered2.features.length} events`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Test 10: Get store statistics
     */
    async test10_getStoreStatistics() {
        console.log('\n🧪 Test 10: Get store statistics');
        
        this.resetStore();
        
        try {
            // Set events
            this.store.dispatch({
                type: ActionType.SET_EVENTS,
                payload: {
                    events: {
                        type: 'FeatureCollection' as const,
                        features: [
                            this.createTestEvent(1),
                            this.createTestEvent(2)
                        ]
                    }
                }
            });
            
            const stats = this.store.getStats();
            
            const passed = stats.totalEvents === 2 && 
                          stats.currentTimeFilter === 30 &&
                          stats.subscribers === 0;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Stats:`, stats);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            this.resetStore();
        }
    }

    /**
     * Run all tests
     */
    async runAll(): Promise<{ passed: number; total: number; results: boolean[] }> {
        console.log('🚀 Running ReactiveStore Tests (Stage 4)');
        console.log('========================================');

        const results = [];

        results.push(await this.test1_setEvents());
        results.push(await this.test2_addEvents());
        results.push(await this.test3_updateTimeFilter());
        results.push(await this.test4_toggleLayer());
        results.push(await this.test5_getFilteredItemsMemoization());
        results.push(await this.test6_subscribeToStateChanges());
        results.push(await this.test7_getMaxEventIdAndTime());
        results.push(await this.test8_clearEvents());
        results.push(await this.test9_cacheInvalidation());
        results.push(await this.test10_getStoreStatistics());

        const passed = results.filter(r => r).length;
        const total = results.length;

        console.log('\n========================================');
        console.log(`📊 Results: ${passed}/${total} tests passed`);
        console.log('========================================\n');

        return { passed, total, results };
    }
}

// Export for use
window.storeTests = new ReactiveStoreTests();

// Auto-run if in browser with test flag
if (typeof document !== 'undefined' && window.location.href.includes('test-store.html')) {
    (async () => {
        const results = await window.storeTests.runAll();
        
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
