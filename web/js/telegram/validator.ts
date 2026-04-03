/**
 * Telegram WebApp Validation Module (TypeScript)
 *
 * Validates Telegram initData and redirects if validation fails.
 * Must be called before any app initialization.
 *
 * Flow:
 * 1. Load config from /api/validation-config
 * 2. Check if window.Telegram.WebApp is available
 * 3. If telegram_validation_enabled=false → skip validation (dev mode)
 * 4. If telegram_validation_enabled=true → validate initData
 * 5. If validation fails → redirect to redirect_url or https://github.com/404
 * 6. If validation passes → continue initialization
 */

interface AppConfig {
    telegram_validation_enabled: boolean;
    redirect_url: string | null;
    validation_redirect_url: string;
    // Центр карты (хардкод)
    map_center_lat: number;
    map_center_lng: number;
    map_default_zoom: number;
    [key: string]: unknown;
}

export class TelegramValidator {
    private tg: typeof window.Telegram.WebApp | null = null;
    private userId: number | null = null;
    private userName: string | null = null;
    private isValidated = false;
    private validationInProgress = false;
    private redirectUrl: string | null = null;
    private validationEnabled: boolean | null = null;
    private configLoaded = false;

    constructor() {
        this.tg = window.Telegram?.WebApp || null;
    }

    /**
     * Normalize boolean value from various formats (string, boolean, etc.)
     */
    private normalizeBoolean(value: unknown): boolean {
        if (typeof value === 'boolean') {
            return value;
        }
        if (typeof value === 'string') {
            return value.toLowerCase() === 'true' || value === '1';
        }
        if (typeof value === 'number') {
            return value === 1;
        }
        return Boolean(value);
    }

    /**
     * Load configuration from server
     */
    async loadConfig(): Promise<void> {
        if (this.configLoaded) {
            return;
        }

        try {
            const response = await fetch('/api/validation-config', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({})
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const serverConfig = await response.json();

            // Merge with defaults (только validation, остальное хардкод)
            const config: AppConfig = {
                telegram_validation_enabled: false,
                redirect_url: null,
                validation_redirect_url: '',
                // Центр карты (хардкод)
                map_center_lat: 46.4825,
                map_center_lng: 30.7233,
                map_default_zoom: 10,
                ...serverConfig
            };

            // Хардкодим фронтенд-параметры (не из сервера)
            const MAP_CONFIG = {
                map_center_lat: 46.4825,
                map_center_lng: 30.7233,
                map_default_zoom: 10,
                enable_random_points: true
            };

            this.validationEnabled = this.normalizeBoolean(config.telegram_validation_enabled);
            this.redirectUrl = config.redirect_url || null;
            this.configLoaded = true;

            // Store globally for other modules (включаем хардкод)
            window.APP_CONFIG = { ...config, ...MAP_CONFIG };

            console.log('[Validator] Config loaded:', {
                validationEnabled: this.validationEnabled,
                redirectUrl: this.redirectUrl,
                rawValue: config.telegram_validation_enabled
            });
        } catch (error) {
            console.warn('[Validator] Failed to load config, using dev mode defaults:', error);
            // Dev mode defaults: validation disabled, redirect to GitHub 404
            this.validationEnabled = false;
            this.redirectUrl = null;
            this.configLoaded = true;
        }
    }

    /**
     * Get effective redirect URL (fallback to https://github.com/404)
     */
    private getRedirectUrl(): string {
        return this.redirectUrl || 'https://github.com/404';
    }

    /**
     * Redirect to fallback URL
     */
    private doRedirect(reason: string): void {
        const redirectUrl = this.getRedirectUrl();
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
    private async waitForConfig(maxWaitMs = 10000): Promise<boolean> {
        const startTime = Date.now();

        while (this.validationEnabled === null) {
            if (Date.now() - startTime > maxWaitMs) {
                console.warn('[Validator] Config load timeout, using dev mode defaults');
                this.validationEnabled = false;
                this.redirectUrl = null;
                return false;
            }
            await new Promise(resolve => setTimeout(resolve, 50));
        }

        return true;
    }

    /**
     * Initialize Telegram WebApp
     */
    private initTelegram(): void {
        if (window.Telegram?.WebApp) {
            this.tg = window.Telegram.WebApp;
            this.tg.ready();
            this.tg.expand();
            console.log('[Validator] Telegram WebApp initialized');
        }
    }

    /**
     * Extract user data from Telegram initData
     */
    private extractUserData(): boolean {
        if (!this.tg?.initDataUnsafe?.user) {
            return false;
        }

        const user = this.tg.initDataUnsafe.user;
        this.userId = user.id;
        this.userName = user.first_name || user.username || 'User';
        return true;
    }

    /**
     * Validate Telegram initData
     */
    private validateInitData(): boolean {
        console.log('[Validator] Validating initData, validationEnabled:', this.validationEnabled);

        // If validation is disabled (dev mode), skip validation
        if (this.validationEnabled === false) {
            console.log('[Validator] Validation disabled (development mode)');
            this.initTelegram();
            this.extractUserData();
            this.isValidated = true;
            return true;
        }

        // Check if Telegram WebApp is available
        if (!window.Telegram?.WebApp) {
            console.warn('[Validator] Telegram WebApp not available');
            return false;
        }

        this.tg = window.Telegram.WebApp;

        // Check if initData exists
        if (!this.tg.initData || !this.tg.initDataUnsafe) {
            console.warn('[Validator] Telegram initData not available');
            return false;
        }

        // Check if user data exists
        if (!this.tg.initDataUnsafe.user) {
            console.warn('[Validator] Telegram user data not available');
            return false;
        }

        // Success - extract user data
        this.initTelegram();
        if (this.extractUserData()) {
            this.isValidated = true;
            console.log('[Validator] Client-side validation successful, userId:', this.userId);
            return true;
        }

        console.warn('[Validator] Failed to extract user data');
        return false;
    }

    /**
     * Main validation flow
     */
    async validateAndInit(): Promise<boolean> {
        console.log('[Validator] Starting validation flow...');

        if (this.validationInProgress) {
            return this.isValidated;
        }

        if (this.isValidated) {
            return true;
        }

        this.validationInProgress = true;

        try {
            // Step 1: Load configuration
            await this.loadConfig();
            console.log('[Validator] Config loaded, validationEnabled:', this.validationEnabled);

            // Step 2: Skip validation if disabled (dev mode)
            if (this.validationEnabled === false) {
                console.log('[Validator] Validation disabled (development mode)');
                this.userId = 123456789;
                this.userName = 'Dev User';
                this.isValidated = true;
                this.initTelegram();
                return true;
            }

            // Step 3: Wait for config
            await this.waitForConfig();

            // Step 4: Client-side validation
            const isValid = this.validateInitData();

            if (!isValid) {
                console.warn('[Validator] Client-side validation failed');
                this.doRedirect('validation_failed');

                // Wait for redirect (never resolves)
                return new Promise(() => {
                    // Never resolves - redirect will happen
                });
            }

            // Step 5: Backend validation
            try {
                const response = await fetch('/api/validate-init', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ init_data: this.tg?.initData })
                });

                const result = await response.json();

                if (!result.valid) {
                    console.warn('[Validator] Backend validation failed:', result.error);
                    this.doRedirect('invalid_init_data');
                    return new Promise(() => {
                        // Never resolves - redirect will happen
                    });
                }

                this.userId = result.user?.id;
                this.userName = result.user?.first_name || result.user?.username || 'User';

                // Store tokens in sessionStorage
                if (result.access_token) {
                    sessionStorage.setItem('access_token', result.access_token);
                    sessionStorage.setItem('refresh_token', result.refresh_token);
                    sessionStorage.setItem('user', JSON.stringify(result.user));
                }

            } catch (error) {
                console.error('[Validator] Backend validation error:', error);
                this.doRedirect('validation_error');
                return new Promise(() => {
                    // Never resolves - redirect will happen
                });
            }

            // Step 6: Validation successful
            console.log('[Validator] Validation successful, userId:', this.userId);
            this.isValidated = true;
            return true;

        } finally {
            this.validationInProgress = false;
        }
    }

    /**
     * Get user ID
     */
    getUserId(): number | null {
        return this.userId;
    }

    /**
     * Get user name
     */
    getUserName(): string | null {
        return this.userName;
    }

    /**
     * Check if validation passed
     */
    isValid(): boolean {
        return this.isValidated;
    }

    /**
     * Get Telegram WebApp instance
     */
    getTelegram(): typeof window.Telegram.WebApp | null {
        return this.tg;
    }

    /**
     * Check if validation is enabled
     */
    isValidationEnabled(): boolean | null {
        return this.validationEnabled;
    }
}
