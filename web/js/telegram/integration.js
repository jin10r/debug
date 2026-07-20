/**
 * Telegram Mini Apps Integration Module
 * Based on: https://core.telegram.org/bots/webapps
 * Implements all major features of Telegram Mini Apps API
 */

class TelegramIntegration {
    constructor() {
        this.tg = window.Telegram?.WebApp;
        this.isInitialized = false;
        this.callbacks = {};
    }

    /**
     * Initialize Telegram Mini App
     * Implements Bot API 8.0+ features
     */
    init() {
        if (!this.tg) {
            console.warn('Telegram WebApp SDK not available');
            return false;
        }

        // ==========================================
        // БАЗОВАЯ ИНИЦИАЛИЗАЦИЯ
        // ==========================================
        
        // Bot API 6.1+: Готовность приложения
        this.tg.ready();
        
        // Bot API 6.1+: Разворачиваем на весь экран
        this.tg.expand();
        
        // Bot API 6.2+: Включаем подтверждение закрытия при необходимости
        // (будем включать динамически при важных действиях)
        try {
            if (this.tg.disableClosingConfirmation && this.tg.isVersionAtLeast && this.tg.isVersionAtLeast('6.2')) {
                this.tg.disableClosingConfirmation();
            }
        } catch (e) {
            // ignore
        }
        
        // Bot API 7.7+: Включаем вертикальные свайпы для лучшего UX
        if (this.tg.isVersionAtLeast('7.7')) {
            this.tg.enableVerticalSwipes();
        }

        // ==========================================
        // ТЕМА И СТИЛИ
        // ==========================================
        
        // Применяем тему Telegram
        this.applyTheme();
        
        // Слушаем изменения темы (Bot API 6.1+)
        this.tg.onEvent('themeChanged', () => {
            console.log('Telegram theme changed');
            this.applyTheme();
        });

        // Bot API 6.9+: Устанавливаем цвет header bar
        try {
            if (this.tg.setHeaderColor && this.tg.isVersionAtLeast && this.tg.isVersionAtLeast('6.9')) {
                this.tg.setHeaderColor('secondary_bg_color');
            }
        } catch (e) {
            // ignore
        }

        // Bot API 7.10+: Устанавливаем цвет bottom bar
        try {
            if (this.tg.setBottomBarColor && this.tg.isVersionAtLeast && this.tg.isVersionAtLeast('7.10')) {
                this.tg.setBottomBarColor('secondary_bg_color');
            }
        } catch (e) {
            // ignore
        }

        // ==========================================
        // VIEWPORT И SAFE AREA (Bot API 8.0+)
        // ==========================================
        
        // Учитываем safe area для вырезов экрана
        if (this.tg.safeAreaInset) {
            this.applySafeArea();
            
            this.tg.onEvent('safeAreaChanged', () => {
                this.applySafeArea();
            });
        }

        // Учитываем content safe area
        if (this.tg.contentSafeAreaInset) {
            this.applyContentSafeArea();
            
            this.tg.onEvent('contentSafeAreaChanged', () => {
                this.applyContentSafeArea();
            });
        }

        // ==========================================
        // LIFECYCLE EVENTS (Bot API 8.0+)
        // ==========================================
        
        if (this.tg.isVersionAtLeast('8.0')) {
            this.tg.onEvent('activated', () => {
                console.log('Mini App activated');
                // Можно обновить данные
                if (this.callbacks.onActivated) {
                    this.callbacks.onActivated();
                }
            });

            this.tg.onEvent('deactivated', () => {
                console.log('Mini App deactivated');
                // Можно приостановить тяжелые операции
                if (this.callbacks.onDeactivated) {
                    this.callbacks.onDeactivated();
                }
            });
        }

        // ==========================================
        // VIEWPORT CHANGES
        // ==========================================
        
        this.tg.onEvent('viewportChanged', (event) => {
            console.log('Viewport changed:', event);
            if (this.callbacks.onViewportChanged) {
                this.callbacks.onViewportChanged(event);
            }
        });

        this.isInitialized = true;
        
        // Removed excessive logging - only basic info in production
        if (window.location.hostname === 'localhost') {
            console.log('✅ Telegram Mini App initialized', {
                version: this.tg.version,
                platform: this.tg.platform,
                colorScheme: this.tg.colorScheme
            });
        }

        return true;
    }

    /**
     * Применяет тему Telegram к приложению
     */
    applyTheme() {
        const { themeParams, colorScheme } = this.tg;
        const root = document.documentElement;

        // Основные цвета темы
        if (themeParams.bg_color) {
            root.style.setProperty('--tg-bg-color', themeParams.bg_color);
        }
        if (themeParams.text_color) {
            root.style.setProperty('--tg-text-color', themeParams.text_color);
        }
        if (themeParams.hint_color) {
            root.style.setProperty('--tg-hint-color', themeParams.hint_color);
        }
        if (themeParams.link_color) {
            root.style.setProperty('--tg-link-color', themeParams.link_color);
        }
        if (themeParams.button_color) {
            root.style.setProperty('--tg-button-color', themeParams.button_color);
        }
        if (themeParams.button_text_color) {
            root.style.setProperty('--tg-button-text-color', themeParams.button_text_color);
        }
        if (themeParams.secondary_bg_color) {
            root.style.setProperty('--tg-secondary-bg-color', themeParams.secondary_bg_color);
        }

        // Bot API 7.0+: Дополнительные цвета
        if (themeParams.header_bg_color) {
            root.style.setProperty('--tg-header-bg-color', themeParams.header_bg_color);
        }
        if (themeParams.accent_text_color) {
            root.style.setProperty('--tg-accent-text-color', themeParams.accent_text_color);
        }
        if (themeParams.section_bg_color) {
            root.style.setProperty('--tg-section-bg-color', themeParams.section_bg_color);
        }
        if (themeParams.section_header_text_color) {
            root.style.setProperty('--tg-section-header-text-color', themeParams.section_header_text_color);
        }
        if (themeParams.subtitle_text_color) {
            root.style.setProperty('--tg-subtitle-text-color', themeParams.subtitle_text_color);
        }
        if (themeParams.destructive_text_color) {
            root.style.setProperty('--tg-destructive-text-color', themeParams.destructive_text_color);
        }

        // Bot API 7.6+: Section separator
        if (themeParams.section_separator_color) {
            root.style.setProperty('--tg-section-separator-color', themeParams.section_separator_color);
        }

        // Bot API 7.10+: Bottom bar
        if (themeParams.bottom_bar_bg_color) {
            root.style.setProperty('--tg-bottom-bar-bg-color', themeParams.bottom_bar_bg_color);
        }

        // Устанавливаем класс для темной/светлой темы
        document.body.classList.toggle('dark-theme', colorScheme === 'dark');
        document.body.classList.toggle('light-theme', colorScheme === 'light');

        // Обновляем meta theme-color
        const metaThemeColor = document.querySelector('meta[name="theme-color"]');
        if (metaThemeColor && themeParams.bg_color) {
            metaThemeColor.setAttribute('content', themeParams.bg_color);
        }

        console.log('🎨 Telegram theme applied:', colorScheme);
    }

    /**
     * Bot API 8.0+: Применяет safe area insets
     */
    applySafeArea() {
        const { safeAreaInset } = this.tg;
        if (!safeAreaInset) return;

        const root = document.documentElement;
        root.style.setProperty('--tg-safe-area-inset-top', `${safeAreaInset.top}px`);
        root.style.setProperty('--tg-safe-area-inset-right', `${safeAreaInset.right}px`);
        root.style.setProperty('--tg-safe-area-inset-bottom', `${safeAreaInset.bottom}px`);
        root.style.setProperty('--tg-safe-area-inset-left', `${safeAreaInset.left}px`);

        console.log('📐 Safe area applied:', safeAreaInset);
    }

    /**
     * Bot API 8.0+: Применяет content safe area insets
     */
    applyContentSafeArea() {
        const { contentSafeAreaInset } = this.tg;
        if (!contentSafeAreaInset) return;

        const root = document.documentElement;
        root.style.setProperty('--tg-content-safe-area-inset-top', `${contentSafeAreaInset.top}px`);
        root.style.setProperty('--tg-content-safe-area-inset-right', `${contentSafeAreaInset.right}px`);
        root.style.setProperty('--tg-content-safe-area-inset-bottom', `${contentSafeAreaInset.bottom}px`);
        root.style.setProperty('--tg-content-safe-area-inset-left', `${contentSafeAreaInset.left}px`);

        console.log('📐 Content safe area applied:', contentSafeAreaInset);
    }

    /**
     * Bot API 6.1+: Haptic Feedback
     * @param {string} type - 'light', 'medium', 'heavy', 'success', 'warning', 'error', 'selection_changed'
     */
    hapticFeedback(type = 'medium') {
        if (!this.tg?.HapticFeedback) return false;
        // API доступен с v6.1; в v6.0 объект существует, но методы кидают.
        const versionOk = !!(this.tg?.isVersionAtLeast && this.tg.isVersionAtLeast('6.1'));
        if (!versionOk) return false;

        try {
            switch (type) {
                case 'light':
                case 'medium':
                case 'heavy':
                    this.tg.HapticFeedback.impactOccurred(type);
                    return true;
                case 'success':
                case 'warning':
                case 'error':
                    this.tg.HapticFeedback.notificationOccurred(type);
                    return true;
                case 'selection_changed':
                    this.tg.HapticFeedback.selectionChanged();
                    return true;
                default:
                    this.tg.HapticFeedback.impactOccurred('medium');
                    return true;
            }
        } catch (e) {
            console.warn('Haptic feedback failed:', e);
            return false;
        }
    }

    /**
     * Bot API 6.2+: Показать popup
     */
    showPopup(message, buttons = [{ type: 'ok' }]) {
        const minOk = !!(this.tg?.isVersionAtLeast && this.tg.isVersionAtLeast('6.2'));
        if (!this.tg?.showPopup || !minOk) {
            alert(message);
            return Promise.resolve();
        }

        return new Promise((resolve) => {
            try {
                this.tg.showPopup({
                    message,
                    buttons
                }, (buttonId) => {
                    resolve(buttonId);
                });
            } catch (e) {
                alert(message);
                resolve();
            }
        });
    }

    /**
     * Bot API 6.2+: Показать alert
     */
    showAlert(message) {
        const minOk = !!(this.tg?.isVersionAtLeast && this.tg.isVersionAtLeast('6.2'));
        if (!this.tg?.showAlert || !minOk) {
            alert(message);
            return Promise.resolve();
        }

        return new Promise((resolve) => {
            try {
                this.tg.showAlert(message, resolve);
            } catch (e) {
                alert(message);
                resolve();
            }
        });
    }

    /**
     * Bot API 6.2+: Показать confirm
     */
    showConfirm(message) {
        if (!this.tg?.showConfirm) {
            return Promise.resolve(confirm(message));
        }

        return new Promise((resolve) => {
            this.tg.showConfirm(message, resolve);
        });
    }

    /**
     * Bot API 6.2+: Включить/выключить подтверждение закрытия
     */
    setClosingConfirmation(enabled) {
        if (!this.tg) return;

        if (enabled) {
            this.tg.enableClosingConfirmation();
        } else {
            this.tg.disableClosingConfirmation();
        }
    }

    /**
     * Bot API 6.4+: Читать текст из буфера обмена
     */
    async readTextFromClipboard() {
        if (!this.tg?.readTextFromClipboard) {
            // Fallback для браузеров
            try {
                return await navigator.clipboard.readText();
            } catch (e) {
                console.warn('Clipboard read failed:', e);
                return null;
            }
        }

        return new Promise((resolve) => {
            this.tg.readTextFromClipboard((text) => {
                resolve(text || null);
            });
        });
    }

    /**
     * Bot API 6.7+: Переключиться в inline режим
     */
    switchInlineQuery(query, chatTypes = ['users', 'bots', 'groups', 'channels']) {
        if (!this.tg?.switchInlineQuery) {
            console.warn('switchInlineQuery not supported');
            return;
        }

        this.tg.switchInlineQuery(query, chatTypes);
    }

    /**
     * Bot API 6.9+: CloudStorage - сохранение данных
     */
    async cloudStorageSet(key, value) {
        if (!this.tg?.CloudStorage) {
            // Fallback на localStorage
            try {
                localStorage.setItem(key, value);
                return true;
            } catch (e) {
                console.warn('LocalStorage write failed:', e);
                return false;
            }
        }

        return new Promise((resolve) => {
            this.tg.CloudStorage.setItem(key, value, (error, success) => {
                if (error) {
                    console.error('CloudStorage set error:', error);
                    resolve(false);
                } else {
                    resolve(success);
                }
            });
        });
    }

    /**
     * Bot API 6.9+: CloudStorage - чтение данных
     */
    async cloudStorageGet(key) {
        if (!this.tg?.CloudStorage) {
            // Fallback на localStorage
            try {
                return localStorage.getItem(key);
            } catch (e) {
                console.warn('LocalStorage read failed:', e);
                return null;
            }
        }

        return new Promise((resolve) => {
            this.tg.CloudStorage.getItem(key, (error, value) => {
                if (error) {
                    console.error('CloudStorage get error:', error);
                    resolve(null);
                } else {
                    resolve(value || null);
                }
            });
        });
    }

    /**
     * Bot API 7.8+: Поделиться в историю
     */
    async shareToStory(mediaUrl, options = {}) {
        if (!this.tg?.shareToStory) {
            console.warn('shareToStory not supported');
            return false;
        }

        try {
            this.tg.shareToStory(mediaUrl, options);
            return true;
        } catch (e) {
            console.error('Share to story failed:', e);
            return false;
        }
    }

    /**
     * Bot API 8.0+: Поделиться сообщением
     */
    async shareMessage(text, url = null) {
        if (!this.tg?.shareMessage) {
            // Fallback на navigator.share
            if (navigator.share) {
                try {
                    await navigator.share({ text, url });
                    return true;
                } catch (e) {
                    console.warn('Navigator share failed:', e);
                    return false;
                }
            }
            console.warn('shareMessage not supported');
            return false;
        }

        return new Promise((resolve) => {
            this.tg.shareMessage(text, url, (success) => {
                resolve(success);
            });
        });
    }

    /**
     * Bot API 8.0+: Скачать файл
     */
    async downloadFile(url, filename) {
        if (!this.tg?.downloadFile) {
            // Fallback - создаём <a> и кликаем
            try {
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                a.click();
                return true;
            } catch (e) {
                console.error('Download failed:', e);
                return false;
            }
        }

        return new Promise((resolve) => {
            this.tg.downloadFile({ url, file_name: filename }, (result) => {
                resolve(result?.status === 'downloading');
            });
        });
    }

    /**
     * Bot API 8.0+: Добавить на главный экран
     */
    async addToHomeScreen() {
        if (!this.tg?.addToHomeScreen) {
            console.warn('addToHomeScreen not supported');
            return false;
        }

        return new Promise((resolve) => {
            this.tg.addToHomeScreen();
            
            // Слушаем результат
            const handler = () => {
                this.tg.offEvent('homeScreenAdded', handler);
                resolve(true);
            };
            
            this.tg.onEvent('homeScreenAdded', handler);
            
            // Таймаут на 5 секунд
            setTimeout(() => {
                this.tg.offEvent('homeScreenAdded', handler);
                resolve(false);
            }, 5000);
        });
    }

    /**
     * Bot API 8.0+: Проверить статус ярлыка на главном экране
     */
    async checkHomeScreenStatus() {
        if (!this.tg?.checkHomeScreenStatus) {
            return 'unsupported';
        }

        return new Promise((resolve) => {
            this.tg.checkHomeScreenStatus((status) => {
                resolve(status); // 'unsupported', 'unknown', 'added', 'missed'
            });
        });
    }

    /**
     * Bot API 8.0+: Полноэкранный режим
     */
    requestFullscreen() {
        if (!this.tg?.requestFullscreen) {
            // Fallback на браузерный fullscreen
            if (document.documentElement.requestFullscreen) {
                document.documentElement.requestFullscreen();
                return true;
            }
            return false;
        }

        this.tg.requestFullscreen();
        return true;
    }

    /**
     * Bot API 8.0+: Выход из полноэкранного режима
     */
    exitFullscreen() {
        if (!this.tg?.exitFullscreen) {
            // Fallback на браузерный fullscreen
            if (document.exitFullscreen && document.fullscreenElement) {
                document.exitFullscreen();
                return true;
            }
            return false;
        }

        this.tg.exitFullscreen();
        return true;
    }

    /**
     * Bot API 8.0+: Заблокировать ориентацию экрана
     */
    lockOrientation(orientation = 'portrait') {
        if (!this.tg?.lockOrientation) {
            // Fallback на Screen Orientation API
            if (screen.orientation && screen.orientation.lock) {
                screen.orientation.lock(orientation).catch(e => {
                    console.warn('Orientation lock failed:', e);
                });
            }
            return;
        }

        this.tg.lockOrientation();
    }

    /**
     * Bot API 8.0+: Разблокировать ориентацию экрана
     */
    unlockOrientation() {
        if (!this.tg?.unlockOrientation) {
            // Fallback на Screen Orientation API
            if (screen.orientation && screen.orientation.unlock) {
                screen.orientation.unlock();
            }
            return;
        }

        this.tg.unlockOrientation();
    }

    /**
     * Bot API 8.0+: Geolocation Manager
     */
    async requestLocation() {
        if (!this.tg?.LocationManager) {
            // Fallback на Geolocation API
            if (navigator.geolocation) {
                return new Promise((resolve, reject) => {
                    navigator.geolocation.getCurrentPosition(
                        (position) => {
                            resolve({
                                latitude: position.coords.latitude,
                                longitude: position.coords.longitude,
                                altitude: position.coords.altitude,
                                accuracy: position.coords.accuracy
                            });
                        },
                        (error) => {
                            reject(error);
                        }
                    );
                });
            }
            throw new Error('Geolocation not supported');
        }

        return new Promise((resolve, reject) => {
            this.tg.LocationManager.getLocation((location) => {
                if (location) {
                    resolve(location);
                } else {
                    reject(new Error('Location denied'));
                }
            });
        });
    }

    /**
     * Открыть ссылку в Telegram
     */
    openTelegramLink(url) {
        if (!this.tg?.openTelegramLink) {
            window.open(url, '_blank');
            return;
        }

        this.tg.openTelegramLink(url);
    }

    /**
     * Открыть внешнюю ссылку
     */
    openLink(url, options = {}) {
        if (!this.tg?.openLink) {
            window.open(url, '_blank');
            return;
        }

        // Bot API 6.4+: Поддержка опций (try_instant_view)
        this.tg.openLink(url, options);
    }

    /**
     * Закрыть Mini App
     */
    close() {
        if (!this.tg?.close) {
            window.close();
            return;
        }

        this.tg.close();
    }

    /**
     * Регистрация колбэков
     */
    on(event, callback) {
        this.callbacks[event] = callback;
    }

    /**
     * Получить информацию о платформе
     */
    getPlatformInfo() {
        if (!this.tg) return {};

        return {
            platform: this.tg.platform,
            version: this.tg.version,
            colorScheme: this.tg.colorScheme,
            isExpanded: this.tg.isExpanded,
            viewportHeight: this.tg.viewportHeight,
            viewportStableHeight: this.tg.viewportStableHeight,
            isFullscreen: this.tg.isFullscreen,
            isActive: this.tg.isActive
        };
    }
}

// Export singleton instance
window.telegramIntegration = new TelegramIntegration();

