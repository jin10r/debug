/**
 * Tests for LocalCache (Stage 3)
 * 
 * Tests:
 * 1. Add and get event
 * 2. Add event without ID (should reject)
 * 3. Add expired event (should reject)
 * 4. Update existing event
 * 5. Add multiple events
 * 6. Replace all events
 * 7. Get events by time filter
 * 8. Cleanup expired events
 * 9. Get max event ID and time
 * 10. Clear cache
 */

import { LocalCache } from '../js/core/local_cache';
import { StorageAdapter } from '../js/core/storage';
import { EventFeature } from '../js/types/geojson';

class LocalCacheTests {
    private cache: LocalCache;
    private storage: StorageAdapter;
    private testPrefix = 'test_cache_';

    constructor() {
        this.storage = new StorageAdapter(this.testPrefix);
        this.cache = new LocalCache(this.storage);
    }

    /**
     * Create test event
     */
    createTestEvent(id: number, time?: Date): EventFeature {
        return {
            type: 'Feature',
            geometry: {
                type: 'Point',
                coordinates: [30.7233, 46.4825]
            },
            properties: {
                id: id,
                description: `Test event ${id}`,
                layer: 'pig',
                strategy: 'exact',
                time: (time || new Date()).toISOString()
            }
        };
    }

    /**
     * Clean up test data
     */
    async cleanup(): Promise<void> {
        this.cache.clear();
        await this.storage.clear();
    }

    /**
     * Test 1: Add and get event
     */
    async test1_addAndGetEvent() {
        console.log('\n🧪 Test 1: Add and get event');
        
        await this.cleanup();
        
        try {
            const event = this.createTestEvent(1);
            const added = this.cache.addEvent(event);
            
            const allEvents = this.cache.getAllEvents();
            const passed = added === true && allEvents.features.length === 1;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Added: ${added}`);
            console.log(`  Total events: ${allEvents.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 2: Add event without ID (should reject)
     */
    async test2_addEventWithoutId() {
        console.log('\n🧪 Test 2: Add event without ID (should reject)');
        
        await this.cleanup();
        
        try {
            const event: EventFeature = {
                type: 'Feature',
                geometry: {
                    type: 'Point',
                    coordinates: [30.7233, 46.4825]
                },
                properties: {
                    // No ID field
                    description: 'Event without ID',
                    layer: 'pig'
                }
            };
            
            const added = this.cache.addEvent(event);
            const passed = added === false;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Added: ${added} (expected: false)`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 3: Add expired event (should reject)
     */
    async test3_addExpiredEvent() {
        console.log('\n🧪 Test 3: Add expired event (should reject)');
        
        await this.cleanup();
        
        try {
            // Event from 2 hours ago (expired)
            const expiredTime = new Date(Date.now() - 2 * 60 * 60 * 1000);
            const event = this.createTestEvent(1, expiredTime);
            
            const added = this.cache.addEvent(event);
            const passed = added === false;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Added: ${added} (expected: false)`);
            console.log(`  Event time: ${expiredTime.toISOString()}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 4: Update existing event
     */
    async test4_updateExistingEvent() {
        console.log('\n🧪 Test 4: Update existing event');
        
        await this.cleanup();
        
        try {
            const event1 = this.createTestEvent(1);
            this.cache.addEvent(event1);
            
            const event2 = this.createTestEvent(1);
            event2.properties.description = 'Updated event';
            
            const added = this.cache.addEvent(event2);
            const allEvents = this.cache.getAllEvents();
            
            // Should update, not add new (added = false for update)
            const passed = added === false && allEvents.features.length === 1;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Added: ${added} (expected: false for update)`);
            console.log(`  Total events: ${allEvents.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 5: Add multiple events
     */
    async test5_addMultipleEvents() {
        console.log('\n🧪 Test 5: Add multiple events');
        
        await this.cleanup();
        
        try {
            const events: EventFeature[] = [
                this.createTestEvent(1),
                this.createTestEvent(2),
                this.createTestEvent(3)
            ];
            
            const addedCount = this.cache.addEvents(events);
            const allEvents = this.cache.getAllEvents();
            
            const passed = addedCount === 3 && allEvents.features.length === 3;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Added count: ${addedCount}`);
            console.log(`  Total events: ${allEvents.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 6: Replace all events
     */
    async test6_replaceAllEvents() {
        console.log('\n🧪 Test 6: Replace all events');
        
        await this.cleanup();
        
        try {
            // Add initial events
            this.cache.addEvents([
                this.createTestEvent(1),
                this.createTestEvent(2)
            ]);
            
            // Replace with new events
            const newEvents: EventFeature[] = [
                this.createTestEvent(10),
                this.createTestEvent(11),
                this.createTestEvent(12),
                this.createTestEvent(13)
            ];
            
            const count = this.cache.replaceAllEvents(newEvents);
            const allEvents = this.cache.getAllEvents();
            
            const passed = count === 4 && allEvents.features.length === 4;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Replaced count: ${count}`);
            console.log(`  Total events: ${allEvents.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 7: Get events by time filter
     */
    async test7_getEventsByTimeFilter() {
        console.log('\n🧪 Test 7: Get events by time filter');
        
        await this.cleanup();
        
        try {
            const now = new Date();
            const oldTime = new Date(now.getTime() - 45 * 60 * 1000); // 45 min ago
            const recentTime = new Date(now.getTime() - 15 * 60 * 1000); // 15 min ago
            
            this.cache.addEvent(this.createTestEvent(1, oldTime));
            this.cache.addEvent(this.createTestEvent(2, recentTime));
            
            // Filter for last 30 minutes
            const filtered = this.cache.getEventsByTimeFilter(30);
            
            const passed = filtered.features.length === 1 && 
                          filtered.features[0].properties.id === 2;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Filter: 30 minutes`);
            console.log(`  Filtered events: ${filtered.features.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 8: Cleanup expired events
     */
    async test8_cleanupExpiredEvents() {
        console.log('\n🧪 Test 8: Cleanup expired events');
        
        await this.cleanup();
        
        try {
            const now = new Date();
            const expiredTime = new Date(now.getTime() - 90 * 60 * 1000); // 90 min ago (expired)
            const validTime = new Date(now.getTime() - 30 * 60 * 1000); // 30 min ago (valid)
            
            this.cache.addEvent(this.createTestEvent(1, expiredTime));
            this.cache.addEvent(this.createTestEvent(2, validTime));
            
            console.log(`  Before cleanup: ${this.cache.getAllEvents().features.length} events`);
            
            const cleaned = this.cache.cleanupExpiredEvents();
            
            const allEvents = this.cache.getAllEvents();
            const passed = cleaned === 1 && allEvents.features.length === 1;
            
            console.log(`  After cleanup: ${allEvents.features.length} events`);
            console.log(`  Cleaned: ${cleaned}`);
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 9: Get max event ID and time
     */
    async test9_getMaxEventIdAndTime() {
        console.log('\n🧪 Test 9: Get max event ID and time');
        
        await this.cleanup();
        
        try {
            const now = new Date();
            const time1 = new Date(now.getTime() - 60 * 60 * 1000);
            const time2 = new Date(now.getTime() - 30 * 60 * 1000);
            
            this.cache.addEvent(this.createTestEvent(10, time1));
            this.cache.addEvent(this.createTestEvent(20, time2));
            
            const maxId = this.cache.getMaxEventId();
            const maxTime = this.cache.getMaxEventTime();
            
            const passed = maxId === 20 && maxTime?.getTime() === time2.getTime();
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Max ID: ${maxId} (expected: 20)`);
            console.log(`  Max time: ${maxTime?.toISOString()} (expected: ${time2.toISOString()})`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 10: Clear cache
     */
    async test10_clearCache() {
        console.log('\n🧪 Test 10: Clear cache');
        
        await this.cleanup();
        
        try {
            this.cache.addEvents([
                this.createTestEvent(1),
                this.createTestEvent(2),
                this.createTestEvent(3)
            ]);
            
            console.log(`  Before clear: ${this.cache.getAllEvents().features.length} events`);
            
            this.cache.clear();
            
            const allEvents = this.cache.getAllEvents();
            const passed = allEvents.features.length === 0;
            
            console.log(`  After clear: ${allEvents.features.length} events`);
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Run all tests
     */
    async runAll(): Promise<{ passed: number; total: number; results: boolean[] }> {
        console.log('🚀 Running LocalCache Tests (Stage 3)');
        console.log('======================================');

        const results = [];

        results.push(await this.test1_addAndGetEvent());
        results.push(await this.test2_addEventWithoutId());
        results.push(await this.test3_addExpiredEvent());
        results.push(await this.test4_updateExistingEvent());
        results.push(await this.test5_addMultipleEvents());
        results.push(await this.test6_replaceAllEvents());
        results.push(await this.test7_getEventsByTimeFilter());
        results.push(await this.test8_cleanupExpiredEvents());
        results.push(await this.test9_getMaxEventIdAndTime());
        results.push(await this.test10_clearCache());

        const passed = results.filter(r => r).length;
        const total = results.length;

        console.log('\n======================================');
        console.log(`📊 Results: ${passed}/${total} tests passed`);
        console.log('======================================\n');

        return { passed, total, results };
    }
}

// Export for use
window.localCacheTests = new LocalCacheTests();

// Auto-run if in browser with test flag
if (typeof document !== 'undefined' && window.location.href.includes('test-localcache.html')) {
    (async () => {
        const results = await window.localCacheTests.runAll();
        
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
