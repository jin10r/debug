#!/usr/bin/env node
/**
 * Automated test runner using Puppeteer
 * Run: node run-tests.js
 */

const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

const TEST_URL = 'http://localhost/tests/test-runner.html';
const RESULTS_FILE = path.join(__dirname, 'test-results.json');
const REPORT_FILE = path.join(__dirname, 'test-report.md');

async function runTests() {
    console.log('🚀 Starting automated tests...\n');
    
    let browser;
    try {
        browser = await puppeteer.launch({
            headless: 'new',
            args: ['--no-sandbox', '--disable-setuid-sandbox']
        });

        const page = await browser.newPage();
        
        // Capture console logs
        const logs = [];
        page.on('console', msg => {
            logs.push({
                type: msg.type(),
                text: msg.text()
            });
        });

        // Capture errors
        const errors = [];
        page.on('pageerror', err => {
            errors.push(err.message);
        });

        console.log(`📖 Loading ${TEST_URL}...`);
        await page.goto(TEST_URL, { 
            waitUntil: 'networkidle0',
            timeout: 30000
        });

        // Wait for tests to complete (max 60 seconds)
        console.log('⏳ Running tests...\n');
        await page.waitForFunction(() => {
            return window.testResults && window.testResults.total > 0;
        }, { timeout: 60000 });

        // Get results
        const results = await page.evaluate(() => window.testResults);
        
        // Generate report
        console.log('📊 Generating report...\n');
        await generateReport(results, logs, errors);
        
        // Save results
        fs.writeFileSync(RESULTS_FILE, JSON.stringify(results, null, 2));
        console.log(`💾 Results saved to ${RESULTS_FILE}`);

        // Print summary
        printSummary(results);

        // Exit with error code if tests failed
        if (results.failed > 0) {
            console.log('\n❌ Some tests failed!');
            process.exit(1);
        } else {
            console.log('\n✅ All tests passed!');
            process.exit(0);
        }

    } catch (error) {
        console.error('❌ Test run failed:', error.message);
        process.exit(1);
    } finally {
        if (browser) {
            await browser.close();
        }
    }
}

function printSummary(results) {
    console.log('═'.repeat(50));
    console.log('📋 TEST SUMMARY');
    console.log('═'.repeat(50));
    console.log(`Total:  ${results.total}`);
    console.log(`✅ Pass: ${results.passed}`);
    console.log(`❌ Fail: ${results.failed}`);
    console.log('═'.repeat(50));
    
    if (results.suites) {
        console.log('\n📦 SUITE RESULTS:');
        results.suites.forEach(suite => {
            const status = suite.failed === 0 ? '✅' : '❌';
            console.log(`  ${status} ${suite.name}: ${suite.passed}/${suite.total}`);
        });
    }
}

async function generateReport(results, logs, errors) {
    const report = `# Test Report

Generated: ${new Date().toISOString()}

## Summary

| Metric | Value |
|--------|-------|
| Total Tests | ${results.total} |
| Passed | ${results.passed} |
| Failed | ${results.failed} |
| Success Rate | ${((results.passed / results.total) * 100).toFixed(1)}% |

## Suite Results

| Suite | Passed | Failed | Total |
|-------|--------|--------|-------|
${results.suites.map(s => `| ${s.name} | ${s.passed} | ${s.failed} | ${s.total} |`).join('\n')}

## Failed Tests

${results.suites.filter(s => s.failed > 0).map(suite => `
### ${suite.name}
- Failed: ${suite.failed}/${suite.total}
`).join('\n') || 'No failed tests ✅'}

## Console Logs

\`\`\`
${logs.slice(-100).map(l => `[${l.type}] ${l.text}`).join('\n')}
\`\`\`

## Errors

${errors.length > 0 ? errors.map(e => `- ${e}`).join('\n') : 'No errors ✅'}
`;

    fs.writeFileSync(REPORT_FILE, report);
    console.log(`📄 Report saved to ${REPORT_FILE}`);
}

// Run tests
runTests();
