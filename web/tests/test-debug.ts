/**
 * Tests for DebugManager (Stage 7)
 * 
 * Tests:
 * 1. Get statistics
 * 2. Collect stats automatically
 * 3. Print report
 * 4. Get stats history
 * 5. Clear history
 * 6. Export to JSON
 * 7. Benchmark filter
 * 8. Check system health
 */

class DebugManagerTests {
    private debugManager: typeof window.debugManager;

    constructor() {
        this.debugManager = window.debugManager;
    }

    /**
     * Test 1: Get statistics
     */
    async test1_getStatistics() {
        console.log('\n🧪 Test 1: Get statistics');
        
        try {
            const stats = this.debugManager.getStats();
            
            const passed = 'localCache' in stats &&
                          'store' in stats &&
                          'websocket' in stats &&
                          'eventManager' in stats &&
                          'performance' in stats &&
                          'timestamp' in stats;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Stats keys:`, Object.keys(stats));
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 2: Collect stats automatically
     */
    async test2_collectStatsAutomatic() {
        console.log('\n🧪 Test 2: Collect stats automatically');
        
        try {
            const initialHistory = this.debugManager.getHistory().length;
            
            // Wait for automatic collection (5 seconds)
            await new Promise(resolve => setTimeout(resolve, 6000));
            
            const newHistory = this.debugManager.getHistory().length;
            
            const passed = newHistory > initialHistory;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Initial history: ${initialHistory}`);
            console.log(`  New history: ${newHistory}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 3: Print report
     */
    async test3_printReport() {
        console.log('\n🧪 Test 3: Print report');
        
        try {
            // Mock console.group to capture output
            let groupCalled = false;
            const originalGroup = console.group;
            console.group = () => { groupCalled = true; };
            
            this.debugManager.printReport();
            
            console.group = originalGroup;
            
            const passed = groupCalled === true;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  console.group called: ${groupCalled}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 4: Get stats history
     */
    async test4_getStatsHistory() {
        console.log('\n🧪 Test 4: Get stats history');
        
        try {
            const history = this.debugManager.getHistory();
            
            const passed = Array.isArray(history) && history.length >= 0;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  History length: ${history.length}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 5: Clear history
     */
    async test5_clearHistory() {
        console.log('\n🧪 Test 5: Clear history');
        
        try {
            const initialLength = this.debugManager.getHistory().length;
            
            this.debugManager.clearHistory();
            
            const newLength = this.debugManager.getHistory().length;
            
            const passed = newLength === 0 && initialLength >= 0;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Initial length: ${initialLength}`);
            console.log(`  New length: ${newLength}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 6: Export to JSON
     */
    async test6_exportToJson() {
        console.log('\n🧪 Test 6: Export to JSON');
        
        try {
            const json = this.debugManager.exportToJson();
            
            const parsed = JSON.parse(json);
            
            const passed = 'current' in parsed && 'history' in parsed;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  JSON valid: ${!!parsed}`);
            console.log(`  Has current: ${'current' in parsed}`);
            console.log(`  Has history: ${'history' in parsed}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 7: Benchmark filter
     */
    async test7_benchmarkFilter() {
        console.log('\n🧪 Test 7: Benchmark filter');
        
        try {
            const result = await this.debugManager.benchmarkFilter(5);
            
            const passed = 'avgTime' in result && 
                          'minTime' in result && 
                          'maxTime' in result &&
                          typeof result.avgTime === 'number';
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Avg time: ${result.avgTime.toFixed(2)} ms`);
            console.log(`  Min time: ${result.minTime.toFixed(2)} ms`);
            console.log(`  Max time: ${result.maxTime.toFixed(2)} ms`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 8: Check system health
     */
    async test8_checkSystemHealth() {
        console.log('\n🧪 Test 8: Check system health');
        
        try {
            const health = this.debugManager.checkHealth();
            
            const passed = 'healthy' in health && 
                          'issues' in health &&
                          Array.isArray(health.issues);
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Healthy: ${health.healthy}`);
            console.log(`  Issues: ${health.issues.length}`);
            if (health.issues.length > 0) {
                console.log(`  Issue list:`, health.issues);
            }
            
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
        console.log('🚀 Running DebugManager Tests (Stage 7)');
        console.log('========================================');

        const results = [];

        results.push(await this.test1_getStatistics());
        results.push(await this.test2_collectStatsAutomatic());
        results.push(await this.test3_printReport());
        results.push(await this.test4_getStatsHistory());
        results.push(await this.test5_clearHistory());
        results.push(await this.test6_exportToJson());
        results.push(await this.test7_benchmarkFilter());
        results.push(await this.test8_checkSystemHealth());

        const passed = results.filter(r => r).length;
        const total = results.length;

        console.log('\n========================================');
        console.log(`📊 Results: ${passed}/${total} tests passed`);
        console.log('========================================\n');

        return { passed, total, results };
    }
}

// Export for use
window.debugManagerTests = new DebugManagerTests();

// Auto-run if in browser with test flag
if (typeof document !== 'undefined' && window.location.href.includes('test-debug.html')) {
    (async () => {
        const results = await window.debugManagerTests.runAll();
        
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
