/**
 * Storage Adapter for localStorage with Promise API
 * 
 * Provides a unified interface for local storage with:
 * - Promise-based API
 * - Error handling (quota exceeded, private mode)
 * - JSON serialization/deserialization
 * - TTL support (optional)
 * 
 * @example
 * ```typescript
 * const storage = new StorageAdapter();
 * await storage.setItem('key', 'value');
 * const value = await storage.getItem('key');
 * ```
 */

import { AsyncStorage } from '../types/geojson';

/**
 * Storage error types
 */
export enum StorageErrorType {
    /** Storage quota exceeded */
    QUOTA_EXCEEDED = 'QUOTA_EXCEEDED',
    /** Storage not available (private mode, disabled) */
    NOT_AVAILABLE = 'NOT_AVAILABLE',
    /** Invalid key or value */
    INVALID_ARGUMENT = 'INVALID_ARGUMENT',
    /** Unknown error */
    UNKNOWN = 'UNKNOWN'
}

/**
 * Storage error class
 */
export class StorageError extends Error {
    constructor(
        public type: StorageErrorType,
        message: string,
        public originalError?: Error
    ) {
        super(message);
        this.name = 'StorageError';
    }
}

/**
 * Storage adapter implementation
 */
export class StorageAdapter implements AsyncStorage {
    private prefix: string;
    private isAvailable: boolean = true;

    /**
     * Create storage adapter
     * @param prefix - Key prefix for namespacing (default: 'app_')
     */
    constructor(prefix: string = 'app_') {
        this.prefix = prefix;
        this.checkAvailability();
    }

    /**
     * Check if localStorage is available
     */
    private checkAvailability(): void {
        try {
            const testKey = '__storage_test__';
            localStorage.setItem(testKey, testKey);
            localStorage.removeItem(testKey);
            this.isAvailable = true;
        } catch (error) {
            this.isAvailable = false;
            console.warn('[Storage] localStorage is not available:', error);
        }
    }

    /**
     * Get full key with prefix
     */
    private getFullKey(key: string): string {
        return `${this.prefix}${key}`;
    }

    /**
     * Handle storage errors
     */
    private handleError(error: unknown, operation: string, key?: string): never {
        const keyInfo = key ? ` for key "${key}"` : '';
        
        if (error instanceof StorageError) {
            throw error;
        }

        if (error instanceof DOMException) {
            if (error.name === 'QuotaExceededError') {
                throw new StorageError(
                    StorageErrorType.QUOTA_EXCEEDED,
                    `Storage quota exceeded${keyInfo}. Consider clearing old data.`,
                    error
                );
            }
            if (error.name === 'SecurityError') {
                throw new StorageError(
                    StorageErrorType.NOT_AVAILABLE,
                    `Storage is not available${keyInfo}. May be in private mode.`,
                    error
                );
            }
        }

        if (error instanceof Error) {
            throw new StorageError(
                StorageErrorType.UNKNOWN,
                `Unknown error ${operation}${keyInfo}: ${error.message}`,
                error
            );
        }

        throw new StorageError(
            StorageErrorType.UNKNOWN,
            `Unknown error ${operation}${keyInfo}`
        );
    }

    /**
     * Get item from storage
     * 
     * @param key - Storage key
     * @returns Stored value or null if not found
     * 
     * @example
     * ```typescript
     * const value = await storage.getItem('user_settings');
     * ```
     */
    async getItem(key: string): Promise<string | null> {
        if (!key || typeof key !== 'string') {
            throw new StorageError(
                StorageErrorType.INVALID_ARGUMENT,
                'Invalid key: key must be a non-empty string'
            );
        }

        try {
            const fullKey = this.getFullKey(key);
            const value = localStorage.getItem(fullKey);
            
            console.log(`[Storage] getItem("${key}"): ${value ? 'found' : 'not found'}`);
            
            return value;
        } catch (error) {
            this.handleError(error, 'getting item', key);
        }
    }

    /**
     * Set item in storage
     * 
     * @param key - Storage key
     * @param value - Value to store (string)
     * 
     * @example
     * ```typescript
     * await storage.setItem('user_settings', JSON.stringify(settings));
     * ```
     */
    async setItem(key: string, value: string): Promise<void> {
        if (!key || typeof key !== 'string') {
            throw new StorageError(
                StorageErrorType.INVALID_ARGUMENT,
                'Invalid key: key must be a non-empty string'
            );
        }

        if (value === undefined) {
            throw new StorageError(
                StorageErrorType.INVALID_ARGUMENT,
                `Invalid value for key "${key}": value cannot be undefined`
            );
        }

        try {
            const fullKey = this.getFullKey(key);
            localStorage.setItem(fullKey, value);
            
            console.log(`[Storage] setItem("${key}"): saved (${value.length} bytes)`);
        } catch (error) {
            this.handleError(error, 'setting item', key);
        }
    }

    /**
     * Set JSON item in storage
     * 
     * @param key - Storage key
     * @param value - Value to serialize and store
     * 
     * @example
     * ```typescript
     * await storage.setItemJSON('user_settings', settings);
     * ```
     */
    async setItemJSON<T>(key: string, value: T): Promise<void> {
        try {
            const jsonString = JSON.stringify(value);
            await this.setItem(key, jsonString);
        } catch (error) {
            if (error instanceof StorageError && error.type === StorageErrorType.INVALID_ARGUMENT) {
                throw error;
            }
            throw new StorageError(
                StorageErrorType.UNKNOWN,
                `Failed to serialize value for key "${key}"`,
                error instanceof Error ? error : undefined
            );
        }
    }

    /**
     * Get JSON item from storage
     * 
     * @param key - Storage key
     * @returns Parsed value or null if not found
     * 
     * @example
     * ```typescript
     * const settings = await storage.getItemJSON<UserSettings>('user_settings');
     * ```
     */
    async getItemJSON<T>(key: string): Promise<T | null> {
        const value = await this.getItem(key);
        
        if (value === null) {
            return null;
        }

        try {
            return JSON.parse(value) as T;
        } catch (error) {
            throw new StorageError(
                StorageErrorType.UNKNOWN,
                `Failed to parse JSON for key "${key}"`,
                error instanceof Error ? error : undefined
            );
        }
    }

    /**
     * Remove item from storage
     * 
     * @param key - Storage key
     * 
     * @example
     * ```typescript
     * await storage.removeItem('user_settings');
     * ```
     */
    async removeItem(key: string): Promise<void> {
        if (!key || typeof key !== 'string') {
            throw new StorageError(
                StorageErrorType.INVALID_ARGUMENT,
                'Invalid key: key must be a non-empty string'
            );
        }

        try {
            const fullKey = this.getFullKey(key);
            localStorage.removeItem(fullKey);
            
            console.log(`[Storage] removeItem("${key}"): removed`);
        } catch (error) {
            this.handleError(error, 'removing item', key);
        }
    }

    /**
     * Get all keys from storage
     * 
     * @returns Array of keys (without prefix)
     * 
     * @example
     * ```typescript
     * const keys = await storage.getKeys();
     * // ['user_settings', 'cache', 'events_geojson']
     * ```
     */
    async getKeys(): Promise<string[]> {
        try {
            const keys: string[] = [];
            const prefixLength = this.prefix.length;

            for (let i = 0; i < localStorage.length; i++) {
                const key = localStorage.key(i);
                if (key && key.startsWith(this.prefix)) {
                    // Remove prefix from key
                    keys.push(key.substring(prefixLength));
                }
            }

            console.log(`[Storage] getKeys(): found ${keys.length} keys`);
            return keys;
        } catch (error) {
            this.handleError(error, 'getting keys');
        }
    }

    /**
     * Clear all items with current prefix
     * 
     * @example
     * ```typescript
     * await storage.clear();
     * ```
     */
    async clear(): Promise<void> {
        try {
            const keys = await this.getKeys();
            
            for (const key of keys) {
                await this.removeItem(key);
            }

            console.log(`[Storage] clear(): removed ${keys.length} items`);
        } catch (error) {
            this.handleError(error, 'clearing storage');
        }
    }

    /**
     * Check if storage has item
     * 
     * @param key - Storage key
     * @returns true if item exists
     * 
     * @example
     * ```typescript
     * const exists = await storage.has('user_settings');
     * ```
     */
    async has(key: string): Promise<boolean> {
        const value = await this.getItem(key);
        return value !== null;
    }

    /**
     * Get storage usage information
     * 
     * @returns Storage usage stats
     * 
     * @example
     * ```typescript
     * const stats = await storage.getUsage();
     * // { keys: 5, totalBytes: 1024 }
     * ```
     */
    async getUsage(): Promise<{ keys: number; totalBytes: number }> {
        try {
            const keys = await this.getKeys();
            let totalBytes = 0;

            for (const key of keys) {
                const value = await this.getItem(key);
                if (value) {
                    totalBytes += key.length + value.length;
                }
            }

            return {
                keys: keys.length,
                totalBytes
            };
        } catch (error) {
            this.handleError(error, 'getting usage');
            return { keys: 0, totalBytes: 0 };
        }
    }

    /**
     * Check if storage is available
     */
    isStorageAvailable(): boolean {
        return this.isAvailable;
    }

    /**
     * Get storage prefix
     */
    getPrefix(): string {
        return this.prefix;
    }
}

// Export singleton instance with default prefix
export const storageAdapter = new StorageAdapter();

console.log('✅ Storage Adapter initialized');
