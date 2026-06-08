// Validation gate (was inline in index.html — externalized for strict CSP).
(async function() {
    const statusEl = document.getElementById('status');
    const spinnerEl = document.getElementById('spinner');
    const errorContainer = document.getElementById('error-container');

    let redirectUrl = 'https://github.com/404';

    function redirectTo(url) {
        console.log('[Gate] Redirecting to:', url);
        window.location.replace(url);
    }

    async function loadConfig() {
        try {
            const response = await fetch('/api/validation-config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });
            if (!response.ok) throw new Error('Config load failed');
            return await response.json();
        } catch (e) {
            console.error('[Gate] Config load error:', e);
            // Dev mode defaults: validation disabled, redirect to GitHub 404
            return { telegram_validation_enabled: false, redirect_url: null };
        }
    }

    async function validateAndAuth(initData) {
        try {
            const response = await fetch('/api/validate-init', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ init_data: initData })
            });
            const result = await response.json();

            if (result.valid && result.access_token) {
                // Store tokens in sessionStorage
                sessionStorage.setItem('access_token', result.access_token);
                sessionStorage.setItem('refresh_token', result.refresh_token);
                sessionStorage.setItem('user', JSON.stringify(result.user));
                return true;
            }
            return false;
        } catch (e) {
            console.error('[Gate] Auth error:', e);
            return false;
        }
    }

    try {
        // Load configuration
        statusEl.textContent = 'Загрузка конфигурации...';
        const config = await loadConfig();

        // Set redirect URL (fallback to GitHub 404)
        redirectUrl = config.redirect_url || 'https://github.com/404';

        console.log('[Gate] Config loaded:', {
            validationEnabled: config.telegram_validation_enabled,
            redirectUrl: redirectUrl
        });

        // Check if validation is disabled (dev mode)
        if (!config.telegram_validation_enabled) {
            console.log('[Gate] Validation disabled (development mode)');
            statusEl.textContent = 'Режим разработки...';

            // Call validate-init to get dev tokens
            try {
                const response = await fetch('/api/validate-init', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ init_data: '' })
                });
                const result = await response.json();

                if (result.valid && result.access_token) {
                    sessionStorage.setItem('access_token', result.access_token);
                    sessionStorage.setItem('refresh_token', result.refresh_token);
                    sessionStorage.setItem('user', JSON.stringify(result.user));
                }
            } catch (e) {
                console.error('[Gate] Failed to get dev tokens:', e);
            }

            sessionStorage.setItem('dev_mode', 'true');

            setTimeout(() => redirectTo('/map.html'), 500);
            return;
        }

        // Check Telegram WebApp
        if (!window.Telegram || !window.Telegram.WebApp) {
            console.warn('[Gate] Not Telegram WebApp');
            statusEl.textContent = 'Перенаправление...';
            setTimeout(() => redirectTo(redirectUrl), 100);
            return;
        }

        const tg = window.Telegram.WebApp;
        const initData = tg.initData;

        if (!initData) {
            console.warn('[Gate] No initData');
            statusEl.textContent = 'Перенаправление...';
            setTimeout(() => redirectTo(redirectUrl), 100);
            return;
        }

        // Validate and get tokens
        statusEl.textContent = 'Вход в систему...';
        const isValid = await validateAndAuth(initData);

        if (!isValid) {
            console.warn('[Gate] Validation failed');
            statusEl.textContent = 'Перенаправление...';
            setTimeout(() => redirectTo(redirectUrl), 100);
            return;
        }

        // Success - redirect to map
        console.log('[Gate] Validation successful');
        statusEl.textContent = 'Готово!';
        tg.ready();
        tg.expand();
        setTimeout(() => redirectTo('/map.html'), 300);

    } catch (e) {
        console.error('[Gate] Error:', e);
        statusEl.textContent = 'Перенаправление...';
        setTimeout(() => redirectTo(redirectUrl), 100);
    }
})();
