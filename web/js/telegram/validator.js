/**
 * Telegram WebApp Validation Module
 *
 * Validates Telegram initData and redirects if validation fails.
 * Must be called before any app initialization.
 *
 * Flow:
 * 1. Load config from /api/validation-config (gets redirectUrl)
 * 2. Check if window.Telegram.WebApp is available
 * 3. If TELEGRAM_VALIDATION_ENABLED=false → skip validation (dev mode)
 * 4. If TELEGRAM_VALIDATION_ENABLED=true → validate initData
 * 5. If validation fails → redirect to redirectUrl or https://github.com/404
 * 6. If validation passes → continue initialization
 */

(function() {
    'use strict';

    // Private state
    let _userId = null;
    let _userName = null;
    let _validated = false;
    let _validationInProgress = false;
    let _redirectUrl = null;
    let _validationEnabled = null;

    /**
     * Load validation configuration from server
     */
    async function loadConfig() {
        if (_validationEnabled !== null) {
            return; // Already loaded
        }

        try {
            const response = await fetch('/api/validation-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const config = await response.json();
            _validationEnabled = config.telegram_validation_enabled !== false;
            _redirectUrl = config.redirect_url || null;

            console.log('[Validator] Config loaded:', {
                validationEnabled: _validationEnabled,
                redirectUrl: _redirectUrl
            });
        } catch (error) {
            console.warn('[Validator] Failed to load config, using dev mode defaults:', error);
            // Dev mode defaults: validation disabled, redirect to GitHub 404
            _validationEnabled = false;
            _redirectUrl = null;
        }
    }

    /**
     * Get effective redirect URL (fallback to https://github.com/404)
     */
    function getRedirectUrl() {
        return _redirectUrl || 'https://github.com/404';
    }

    /**
     * Immediate redirect - replaces page content and navigates
     */
    function doRedirect(reason) {
        const redirectUrl = getRedirectUrl();
        console.log(`[Validator] Invalid access (${reason}), redirecting to: ${redirectUrl}`);

        // Show loading message during redirect
        document.body.innerHTML = `
            <div style="display: flex; justify-content: center; align-items: center;
                        height: 100vh; background: #1a1a1a; color: #fff;
                        font-family: system-ui, sans-serif; text-align: center;">
                <div>
                    <div style="width: 50px; height: 50px; border: 3px solid rgba(255,255,255,0.1);
                                border-top-color: #0088cc; border-radius: 50%;
                                animation: spin 1s linear infinite; margin: 0 auto 20px;"></div>
                    <p>Перенаправление...</p>
                </div>
            </div>
            <style>
                @keyframes spin {
                    to { transform: rotate(360deg); }
                }
            </style>
        `;

        // Use replace to prevent back button
        setTimeout(() => {
            window.location.replace(redirectUrl);
        }, 100);
    }

    /**
     * Wait for config to be loaded
     */
    async function waitForConfig(maxWaitMs = 10000) {
        const startTime = Date.now();

        while (_validationEnabled === null) {
            if (Date.now() - startTime > maxWaitMs) {
                console.warn('[Validator] Config load timeout, using dev mode defaults');
                _validationEnabled = false;
                _redirectUrl = null;
                return false;
            }
            await new Promise(resolve => setTimeout(resolve, 50));
        }

        return true;
    }

    /**
     * Main validation function
     */
    async function performValidation() {
        if (_validationInProgress) {
            // Wait for validation to complete
            while (_validationInProgress) {
                await new Promise(resolve => setTimeout(resolve, 50));
            }
            return _validated;
        }

        if (_validated) {
            return true;
        }

        _validationInProgress = true;

        try {
            // Step 1: Load configuration (no auth required for validate-init)
            // Config is loaded AFTER validation to avoid circular dependency

            console.log('[Validator] Validation enabled:', _validationEnabled);

            // Step 2: Skip validation if disabled (dev mode)
            if (!_validationEnabled) {
                console.log('[Validator] Validation disabled (development mode)');
                _userId = 123456789;  // Number, not string
                _userName = 'Dev User';
                _validated = true;

                if (window.Telegram?.WebApp) {
                    window.Telegram.WebApp.ready();
                    window.Telegram.WebApp.expand();
                }
                return true;
            }

            // Step 3: Wait for config to be fully loaded
            await waitForConfig();

            // Step 4: Check Telegram WebApp availability
            if (!window.Telegram || !window.Telegram.WebApp) {
                console.warn('[Validator] Telegram WebApp not available');
                doRedirect('not_telegram');
                return false;
            }

            const tg = window.Telegram.WebApp;
            const initData = tg.initData;

            // Step 5: Check initData availability
            if (!initData) {
                console.warn('[Validator] Telegram initData not available');
                doRedirect('no_init_data');
                return false;
            }

            // Step 6: Backend validation
            try {
                const response = await fetch('/api/validate-init', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ init_data: initData })
                });

                const result = await response.json();

                if (!result.valid) {
                    console.warn('[Validator] Backend validation failed:', result.error);
                    doRedirect('invalid_init_data');
                    return false;
                }

                _userId = result.user?.id;
                _userName = result.user?.first_name || result.user?.username || 'User';

                // Store tokens in sessionStorage for future API calls
                if (result.access_token) {
                    sessionStorage.setItem('access_token', result.access_token);
                    sessionStorage.setItem('refresh_token', result.refresh_token);
                    sessionStorage.setItem('user', JSON.stringify(result.user));
                }

            } catch (error) {
                console.error('[Validator] Backend validation error:', error);
                doRedirect('validation_error');
                return false;
            }

            // Step 7: Initialize Telegram WebApp
            tg.ready();
            tg.expand();

            _validated = true;
            console.log('[Validator] Validation successful, userId:', _userId);
            return true;

        } finally {
            _validationInProgress = false;
        }
    }

    // Export public API
    window.telegramValidator = {
        /**
         * Check if validation passed
         */
        isValidated: () => _validated,

        /**
         * Get user ID
         */
        getUserId: () => _userId,

        /**
         * Get user name
         */
        getUserName: () => _userName,

        /**
         * Get Telegram WebApp instance
         */
        getTelegram: () => window.Telegram?.WebApp,

        /**
         * Check if validation is enabled
         */
        isValidationEnabled: () => _validationEnabled,

        /**
         * Get redirect URL
         */
        getRedirectUrl: () => getRedirectUrl(),

        /**
         * Main validation entry point
         * Call this from map.html initialization
         */
        validateAndInit: async function() {
            return await performValidation();
        },

        /**
         * Force reload validation state (for testing)
         */
        reset: function() {
            _userId = null;
            _userName = null;
            _validated = false;
            _validationInProgress = false;
            _validationEnabled = null;
            _redirectUrl = null;
        }
    };

    console.log('[Validator] Module loaded');
})();
