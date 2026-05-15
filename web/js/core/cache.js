// js/core/cache.js - LocalStorage cache utility with TTL

class CacheUtility {
    constructor() {
        this.TTL_MS = 60 * 60 * 1000; // 60 minutes TTL
    }

    /**
     * Load data from localStorage
     * @param {string} key - Cache key
     * @returns {Promise<any>} Cached data or null
     */
    async loadFromCache(key) {
        try {
            const stored = localStorage.getItem(key);
            if (stored) {
                const parsed = JSON.parse(stored);
                // Check if data is still valid (not expired)
                if (parsed.timestamp && (Date.now() - parsed.timestamp) < this.TTL_MS) {
                    return parsed.data;
                } else {
                    localStorage.removeItem(key);
                }
            }
        } catch (error) {
            console.warn('Error loading from localStorage:', error);
        }
        return null;
    }

    /**
     * Save data to localStorage
     * @param {string} key - Cache key
     * @param {any} data - Data to cache
     * @returns {Promise<boolean>} Success status
     */
    async saveToCache(key, data) {
        const cacheEntry = {
            data: data,
            timestamp: Date.now()
        };

        try {
            localStorage.setItem(key, JSON.stringify(cacheEntry));
            return true;
        } catch (error) {
            console.warn('Error saving to localStorage:', error);
            return false;
        }
    }

    /**
     * Clear data from localStorage
     * @param {string} key - Cache key to remove
     * @returns {Promise<boolean>} Success status
     */
    async clearFromCache(key) {
        try {
            localStorage.removeItem(key);
            return true;
        } catch (error) {
            console.warn('Error removing from localStorage:', error);
            return false;
        }
    }

    /**
     * Get authentication headers with JWT token or tg.initData
     * @returns {Object} Headers object with auth information
     */
    getAuthHeaders() {
        const headers = {
            'Content-Type': 'application/json'
        };

        // Try to get JWT access token first
        const accessToken = sessionStorage.getItem('access_token');
        if (accessToken) {
            headers['Authorization'] = `Bearer ${accessToken}`;
        }
        // Fallback to Telegram initData if available
        else if (window.Telegram?.WebApp?.initData) {
            headers['X-Telegram-Init-Data'] = window.Telegram.WebApp.initData;
        }

        return headers;
    }
}

// Create and export singleton instance
window.cacheUtility = new CacheUtility();

console.log('✅ Cache utility initialized with localStorage support');