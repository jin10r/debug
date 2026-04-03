/**
 * Tests for TelegramValidator (Stage 0)
 * 
 * Tests:
 * 1. Validation passes in Telegram Mini App
 * 2. Validation fails in browser (no Telegram)
 * 3. No redirect URL - continues without validation
 * 4. initData without user data
 * 5. Config loading from server
 */

class ValidatorTests {
    constructor() {
        this.tests = [];
        this.results = [];
    }

    /**
     * Mock Telegram WebApp
     */
    mockTelegramWebApp(user = null, initData = 'test_init_data') {
        window.Telegram = {
            WebApp: {
                initData: initData,
                initDataUnsafe: user ? { user } : null,
                ready: () => console.log('[Mock] tg.ready() called'),
                expand: () => console.log('[Mock] tg.expand() called'),
                version: '6.0',
                platform: 'test'
            }
        };
    }

    /**
     * Clear mock
     */
    clearMock() {
        window.Telegram = undefined;
        window.telegramValidator = undefined;
    }

    /**
     * Mock config response
     */
    mockConfig(validationRedirectUrl = '') {
        const originalFetch = window.fetch;
        window.fetch = (url, options) => {
            if (url === '/api/config') {
                return Promise.resolve({
                    ok: true,
                    json: () => Promise.resolve({
                        validation_redirect_url: validationRedirectUrl,
                        map_center_lat: 46.4825,
                        map_center_lng: 30.7233,
                        map_default_zoom: 10
                    })
                });
            }
            return originalFetch(url, options);
        };
    }

    /**
     * Test 1: Validation passes in Telegram Mini App
     */
    async test1_validationPasses() {
        console.log('\n🧪 Test 1: Validation passes in Telegram Mini App');
        
        this.clearMock();
        this.mockTelegramWebApp({
            id: 123456789,
            first_name: 'Test',
            username: 'testuser'
        });
        this.mockConfig('/access-denied.html');

        // Load validator module
        await this.loadValidator();

        const validator = window.telegramValidator;
        const result = await validator.validateAndInit();

        const passed = result === true && 
                       validator.isValid() === true && 
                       validator.getUserId() === 123456789;

        console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
        console.log(`  isValid: ${validator.isValid()}`);
        console.log(`  getUserId: ${validator.getUserId()}`);
        console.log(`  isRequired: ${validator.isValidationRequired()}`);

        this.results.push({ test: 'Validation passes', passed });
        return passed;
    }

    /**
     * Test 2: Validation fails in browser (no Telegram)
     */
    async test2_validationFails() {
        console.log('\n🧪 Test 2: Validation fails in browser (no Telegram)');
        
        this.clearMock();
        this.mockConfig('/access-denied.html');

        let redirected = false;
        const originalHref = window.location.href;
        
        // Mock location.href for redirect detection
        Object.defineProperty(window.location, 'href', {
            set: (value) => {
                redirected = true;
                console.log(`  Redirect detected: ${value}`);
            },
            configurable: true
        });

        await this.loadValidator();

        const validator = window.telegramValidator;
        
        // Start validation (should redirect)
        const validationPromise = validator.validateAndInit();
        
        // Wait a bit for redirect
        await new Promise(resolve => setTimeout(resolve, 100));

        const passed = redirected === true && 
                       validator.isValidationRequired() === true;

        console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
        console.log(`  Redirected: ${redirected}`);
        console.log(`  isRequired: ${validator.isValidationRequired()}`);

        // Restore location.href
        Object.defineProperty(window.location, 'href', {
            value: originalHref,
            configurable: true
        });

        this.results.push({ test: 'Validation fails (redirect)', passed });
        return passed;
    }

    /**
     * Test 3: No redirect URL - continues without validation
     */
    async test3_noRedirectUrl() {
        console.log('\n🧪 Test 3: No redirect URL - continues without validation');
        
        this.clearMock();
        this.mockConfig(''); // Empty redirect URL

        await this.loadValidator();

        const validator = window.telegramValidator;
        const result = await validator.validateAndInit();

        const passed = result === true && 
                       validator.isValidationRequired() === false &&
                       validator.isValid() === true;

        console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
        console.log(`  Result: ${result}`);
        console.log(`  isRequired: ${validator.isValidationRequired()}`);
        console.log(`  isValid: ${validator.isValid()}`);

        this.results.push({ test: 'No redirect URL', passed });
        return passed;
    }

    /**
     * Test 4: initData without user data
     */
    async test4_initDataWithoutUser() {
        console.log('\n🧪 Test 4: initData without user data');
        
        this.clearMock();
        this.mockTelegramWebApp(null); // No user
        this.mockConfig('/access-denied.html');

        let redirected = false;
        const originalHref = window.location.href;
        
        Object.defineProperty(window.location, 'href', {
            set: () => { redirected = true; },
            configurable: true
        });

        await this.loadValidator();

        const validator = window.telegramValidator;
        validator.validateAndInit();
        
        await new Promise(resolve => setTimeout(resolve, 100));

        const passed = redirected === true;

        console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
        console.log(`  Redirected: ${redirected}`);

        Object.defineProperty(window.location, 'href', {
            value: originalHref,
            configurable: true
        });

        this.results.push({ test: 'initData without user', passed });
        return passed;
    }

    /**
     * Test 5: Config loading from server
     */
    async test5_configLoading() {
        console.log('\n🧪 Test 5: Config loading from server');
        
        this.clearMock();
        this.mockConfig('/custom-denied.html');

        await this.loadValidator();

        const validator = window.telegramValidator;
        await validator.loadConfig();

        const passed = validator.getValidationRedirectUrl() === '/custom-denied.html' &&
                       validator.isValidationRequired() === true;

        console.log(`  Result: ${passed ? '✅ PASS' : '❌ FAIL'}`);
        console.log(`  Redirect URL: ${validator.getValidationRedirectUrl()}`);
        console.log(`  isRequired: ${validator.isValidationRequired()}`);

        this.results.push({ test: 'Config loading', passed });
        return passed;
    }

    /**
     * Load validator module
     */
    async loadValidator() {
        // Remove existing script if any
        const existing = document.querySelector('script[src*="validator.ts"]');
        if (existing) existing.remove();

        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = '/js/telegram/validator.ts';
            script.onload = () => resolve();
            script.onerror = (e) => reject(e);
            document.head.appendChild(script);
        });
    }

    /**
     * Run all tests
     */
    async runAll() {
        console.log('🚀 Running Validator Tests (Stage 0)');
        console.log('=====================================');

        const results = [];

        results.push(await this.test1_validationPasses());
        results.push(await this.test2_validationFails());
        results.push(await this.test3_noRedirectUrl());
        results.push(await this.test4_initDataWithoutUser());
        results.push(await this.test5_configLoading());

        const passed = results.filter(r => r).length;
        const total = results.length;

        console.log('\n=====================================');
        console.log(`📊 Results: ${passed}/${total} tests passed`);
        console.log('=====================================\n');

        return { passed, total, results };
    }
}

// Export for use
window.validatorTests = new ValidatorTests();

// Auto-run if called directly
if (window.location.href.includes('test-validator.html')) {
    (async () => {
        const results = await window.validatorTests.runAll();
        
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
