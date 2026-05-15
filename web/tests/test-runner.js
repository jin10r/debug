// test-runner.js - Test runner for browser

(function() {
    const results = {
        total: 0,
        passed: 0,
        failed: 0,
        suites: []
    };

    const logElement = document.getElementById('log');
    const suitesElement = document.getElementById('suites');
    const totalElement = document.getElementById('total');
    const passElement = document.getElementById('pass');
    const failElement = document.getElementById('fail');

    function log(message, type = 'info') {
        const entry = document.createElement('div');
        entry.className = `log-entry log-${type}`;
        entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
        logElement.appendChild(entry);
        logElement.scrollTop = logElement.scrollHeight;
    }

    function runTest(suiteName, test) {
        const testElement = document.createElement('div');
        testElement.className = 'test pending';
        testElement.innerHTML = `<span>${test.name}</span><span class="status">⏳ Running</span>`;
        suitesElement.appendChild(testElement);

        try {
            test.run();
            testElement.className = 'test pass';
            testElement.querySelector('.status').textContent = '✅ Pass';
            results.passed++;
            log(`✅ ${suiteName}: ${test.name}`, 'success');
        } catch (error) {
            testElement.className = 'test fail';
            testElement.querySelector('.status').textContent = '❌ Fail';
            results.failed++;
            log(`❌ ${suiteName}: ${test.name} - ${error.message}`, 'error');
        }

        results.total++;
        updateSummary();
    }

    function runSuite(suite) {
        log(`\n📦 Running suite: ${suite.name}`, 'info');
        
        const suiteElement = document.createElement('div');
        suiteElement.className = 'test-suite';
        suiteElement.innerHTML = `<h2>📋 ${suite.name}</h2>`;
        suitesElement.appendChild(suiteElement);

        let suitePassed = 0;
        let suiteFailed = 0;

        suite.tests.forEach(test => {
            const testElement = document.createElement('div');
            testElement.className = 'test pending';
            testElement.innerHTML = `<span>${test.name}</span><span class="status">⏳ Running</span>`;
            suiteElement.appendChild(testElement);

            try {
                test.run();
                testElement.className = 'test pass';
                testElement.querySelector('.status').textContent = '✅ Pass';
                suitePassed++;
                results.passed++;
                log(`✅ ${suite.name}: ${test.name}`, 'success');
            } catch (error) {
                testElement.className = 'test fail';
                testElement.querySelector('.status').textContent = '❌ Fail';
                suiteFailed++;
                results.failed++;
                log(`❌ ${suite.name}: ${test.name} - ${error.message}`, 'error');
            }

            results.total++;
        });

        results.suites.push({
            name: suite.name,
            passed: suitePassed,
            failed: suiteFailed,
            total: suite.tests.length
        });

        updateSummary();
    }

    function updateSummary() {
        totalElement.textContent = results.total;
        passElement.textContent = results.passed;
        failElement.textContent = results.failed;
    }

    function saveResults() {
        const json = JSON.stringify(results, null, 2);
        localStorage.setItem('test-results', json);
        log('\n💾 Results saved to localStorage', 'info');
        
        // Also create downloadable file
        const blob = new Blob([json], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `test-results-${new Date().toISOString().slice(0,10)}.json`;
        a.click();
        URL.revokeObjectURL(url);
    }

    async function runAllTests() {
        log('🚀 Starting test run...', 'info');
        
        const suites = [
            CommonTests,
            StoreTests,
            LocalCacheTests,
            NotificationsTests,
            TimeFilteringTests,
            TelegramIntegrationTests
        ];

        // Wait for DOM to be ready
        await new Promise(resolve => setTimeout(resolve, 100));

        for (const suite of suites) {
            runSuite(suite);
            await new Promise(resolve => setTimeout(resolve, 50));
        }

        log(`\n🏁 Test run completed: ${results.passed}/${results.total} passed`, results.failed === 0 ? 'success' : 'error');
        saveResults();

        if (results.failed > 0) {
            log(`\n⚠️ ${results.failed} test(s) failed. Check the results above.`, 'error');
        }
    }

    // Start tests when page loads
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', runAllTests);
    } else {
        runAllTests();
    }

    // Expose for debugging
    window.testResults = results;
})();
