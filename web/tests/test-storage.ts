/**
 * Tests for StorageAdapter (Stage 2)
 * 
 * Tests:
 * 1. Set and get item
 * 2. Get non-existent item
 * 3. Remove item
 * 4. Get all keys
 * 5. JSON serialization
 * 6. Clear all items
 * 7. Error handling (quota exceeded simulation)
 * 8. Storage availability check
 */

import { StorageAdapter, StorageError, StorageErrorType } from '../js/core/storage';

class StorageAdapterTests {
    private storage: StorageAdapter;
    private testPrefix = 'test_';

    constructor() {
        this.storage = new StorageAdapter(this.testPrefix);
    }

    /**
     * Clean up test data
     */
    async cleanup(): Promise<void> {
        const keys = await this.storage.getKeys();
        for (const key of keys) {
            await this.storage.removeItem(key);
        }
    }

    /**
     * Test 1: Set and get item
     */
    async test1_setAndGetItem() {
        console.log('\n🧪 Test 1: Set and get item');
        
        await this.cleanup();
        
        try {
            await this.storage.setItem('test_key', 'test_value');
            const value = await this.storage.getItem('test_key');
            
            const passed = value === 'test_value';
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Expected: "test_value"`);
            console.log(`  Got: "${value}"`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 2: Get non-existent item
     */
    async test2_getNonExistentItem() {
        console.log('\n🧪 Test 2: Get non-existent item');
        
        try {
            const value = await this.storage.getItem('non_existent_key');
            
            const passed = value === null;
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Expected: null`);
            console.log(`  Got: ${value}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 3: Remove item
     */
    async test3_removeItem() {
        console.log('\n🧪 Test 3: Remove item');
        
        await this.cleanup();
        
        try {
            await this.storage.setItem('to_remove', 'value');
            let value = await this.storage.getItem('to_remove');
            console.log(`  Before remove: ${value}`);
            
            await this.storage.removeItem('to_remove');
            value = await this.storage.getItem('to_remove');
            console.log(`  After remove: ${value}`);
            
            const passed = value === null;
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
     * Test 4: Get all keys
     */
    async test4_getKeys() {
        console.log('\n🧪 Test 4: Get all keys');
        
        await this.cleanup();
        
        try {
            await this.storage.setItem('key1', 'value1');
            await this.storage.setItem('key2', 'value2');
            await this.storage.setItem('key3', 'value3');
            
            const keys = await this.storage.getKeys();
            const expectedKeys = ['key1', 'key2', 'key3'];
            
            const passed = keys.length === 3 && 
                          expectedKeys.every(k => keys.includes(k));
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Expected keys: ${expectedKeys.join(', ')}`);
            console.log(`  Got keys: ${keys.join(', ')}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 5: JSON serialization
     */
    async test5_jsonSerialization() {
        console.log('\n🧪 Test 5: JSON serialization');
        
        await this.cleanup();
        
        try {
            const testData = {
                name: 'Test User',
                age: 30,
                settings: {
                    theme: 'dark',
                    notifications: true
                }
            };
            
            await this.storage.setItemJSON('user_data', testData);
            const retrieved = await this.storage.getItemJSON<typeof testData>('user_data');
            
            const passed = JSON.stringify(retrieved) === JSON.stringify(testData);
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Original:`, testData);
            console.log(`  Retrieved:`, retrieved);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 6: Clear all items
     */
    async test6_clear() {
        console.log('\n🧪 Test 6: Clear all items');
        
        await this.cleanup();
        
        try {
            await this.storage.setItem('clear1', 'value1');
            await this.storage.setItem('clear2', 'value2');
            
            let keys = await this.storage.getKeys();
            console.log(`  Before clear: ${keys.length} keys`);
            
            await this.storage.clear();
            
            keys = await this.storage.getKeys();
            console.log(`  After clear: ${keys.length} keys`);
            
            const passed = keys.length === 0;
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
     * Test 7: Error handling (invalid key)
     */
    async test7_errorHandling() {
        console.log('\n🧪 Test 7: Error handling (invalid key)');
        
        try {
            // @ts-ignore - testing invalid input
            await this.storage.setItem('', 'value');
            console.log('  ❌ FAIL: Should have thrown error for empty key');
            return false;
        } catch (error) {
            const passed = error instanceof StorageError && 
                          error.type === StorageErrorType.INVALID_ARGUMENT;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Error type: ${(error as StorageError).type}`);
            console.log(`  Error message: ${(error as StorageError).message}`);
            
            return passed;
        }
    }

    /**
     * Test 8: Storage availability check
     */
    async test8_storageAvailability() {
        console.log('\n🧪 Test 8: Storage availability check');
        
        try {
            const isAvailable = this.storage.isStorageAvailable();
            const passed = isAvailable === true;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Storage available: ${isAvailable}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        }
    }

    /**
     * Test 9: Get usage information
     */
    async test9_getUsage() {
        console.log('\n🧪 Test 9: Get usage information');
        
        await this.cleanup();
        
        try {
            await this.storage.setItem('usage1', 'value1');
            await this.storage.setItem('usage2', 'value2');
            
            const usage = await this.storage.getUsage();
            
            const passed = usage.keys === 2 && usage.totalBytes > 0;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  Keys: ${usage.keys}`);
            console.log(`  Total bytes: ${usage.totalBytes}`);
            
            return passed;
        } catch (error) {
            console.error('  ❌ FAIL: Error during test', error);
            return false;
        } finally {
            await this.cleanup();
        }
    }

    /**
     * Test 10: Has method
     */
    async test10_hasMethod() {
        console.log('\n🧪 Test 10: Has method');
        
        await this.cleanup();
        
        try {
            await this.storage.setItem('exists', 'value');
            
            const hasExists = await this.storage.has('exists');
            const hasNotExists = await this.storage.has('not_exists');
            
            const passed = hasExists === true && hasNotExists === false;
            
            console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
            console.log(`  has('exists'): ${hasExists}`);
            console.log(`  has('not_exists'): ${hasNotExists}`);
            
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
        console.log('🚀 Running StorageAdapter Tests (Stage 2)');
        console.log('==========================================');

        const results = [];

        results.push(await this.test1_setAndGetItem());
        results.push(await this.test2_getNonExistentItem());
        results.push(await this.test3_removeItem());
        results.push(await this.test4_getKeys());
        results.push(await this.test5_jsonSerialization());
        results.push(await this.test6_clear());
        results.push(await this.test7_errorHandling());
        results.push(await this.test8_storageAvailability());
        results.push(await this.test9_getUsage());
        results.push(await this.test10_hasMethod());

        const passed = results.filter(r => r).length;
        const total = results.length;

        console.log('\n==========================================');
        console.log(`📊 Results: ${passed}/${total} tests passed`);
        console.log('==========================================\n');

        return { passed, total, results };
    }
}

// Export for use
window.storageAdapterTests = new StorageAdapterTests();

// Auto-run if in browser with test flag
if (typeof document !== 'undefined' && window.location.href.includes('test-storage.html')) {
    (async () => {
        const results = await window.storageAdapterTests.runAll();
        
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
