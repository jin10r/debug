/**
 * Telegram WebApp type declarations (shared).
 *
 * База — пакет @twa-dev/types (классическая форма `window.Telegram.WebApp`,
 * покрывает Bot API до 7.10). Здесь — только расширения:
 *
 * 1. Методы Bot API 8.0+, которых нет в пакете (safeAreaInset,
 *    LocationManager, DeviceStorage, fullscreen и т.д.).
 * 2. Ослабление строгих generic-сигнатур пакета (onEvent/offEvent с union
 *    EventNames, showPopup с PopupButton, switchInlineQuery со строгими
 *    chatTypes, shareToStory с ShareStoryParams) — реальные вызовы в
 *    js/telegram/integration.ts используют произвольные события и свободные
 *    параметры, несовместимые с точными union'ами пакета.
 *
 * Форма сохранена: `Telegram: { WebApp: ... }` — на неё опирается
 * js/telegram/integration.ts (`Window['Telegram']['WebApp']`).
 */
import type { WebApp } from '@twa-dev/types';

/** Отступы безопасной зоны (Bot API 8.0+) */
export interface TelegramWebAppSafeArea {
    top: number;
    right: number;
    bottom: number;
    left: number;
}

/**
 * Расширенный тип WebApp.
 *
 * Omit<...> по строгим членам пакета — их сигнатуры переопределены ниже под
 * реальное использование в integration.ts (intersection не может
 * переопределить член с другим типом).
 */
export type ExtendedWebApp = Omit<
    WebApp,
    'onEvent' | 'offEvent' | 'showPopup' | 'switchInlineQuery' | 'shareToStory' | 'openLink'
> & {
    // События: пакет допускает только EventNames union, а integration.ts
    // подписывается на safeAreaChanged / contentSafeAreaChanged / activated /
    // deactivated / homeScreenAdded и т.д. (Bot API 8.0).
    onEvent: (event: string, callback: (...args: unknown[]) => void) => void;
    offEvent: (event: string, callback: (...args: unknown[]) => void) => void;

    // Попапы: пакетный PopupButton требует text для default/destructive,
    // integration.ts передаёт свободные Array<{ type: string }>.
    showPopup: (
        params: { message: string; buttons?: Array<{ type: string; id?: string; text?: string }> },
        callback?: (buttonId: string) => void
    ) => void;

    // switchInlineQuery: integration.ts передаёт chatTypes: string[].
    switchInlineQuery: (query: string, chatTypes: string[]) => void;

    // shareToStory: integration.ts передаёт options: Record<string, unknown>.
    shareToStory: (mediaUrl: string, options?: Record<string, unknown>) => void;

    // openLink: пакетный тип требует { try_instant_view: boolean },
    // integration.ts передаёт свободный options: Record<string, unknown>.
    openLink: (url: string, options?: Record<string, unknown>) => void;

    // ---- Bot API 8.0+ (отсутствуют в @twa-dev/types до 7.10) ----
    // 8.0-only: на старых клиентах undefined → опциональные.
    isFullscreen?: boolean;
    isActive?: boolean;
    safeAreaInset?: TelegramWebAppSafeArea;
    contentSafeAreaInset?: TelegramWebAppSafeArea;
    requestFullscreen?: () => void;
    exitFullscreen?: () => void;
    lockOrientation?: () => void;
    unlockOrientation?: () => void;
    LocationManager?: {
        isInited: boolean;
        isLocationAvailable: boolean;
        isAccessRequested: boolean;
        isAccessGranted: boolean;
        // init() обязателен перед getLocation(): официальный telegram-web-app.js
        // бросает WebAppLocationManagerNotInited при вызове getLocation без init.
        init: (callback?: () => void) => unknown;
        getLocation: (callback: (location: {
            latitude: number;
            longitude: number;
            altitude?: number | null;
            course?: number | null;
            speed?: number | null;
            horizontal_accuracy?: number | null;
            vertical_accuracy?: number | null;
            course_accuracy?: number | null;
            speed_accuracy?: number | null;
        } | null) => void) => unknown;
        openSettings: () => unknown;
    };
    DeviceStorage?: {
        getItem: (key: string, callback: (err: string | null, value: string | null) => void) => void;
        setItem: (key: string, value: string, callback: (err: string | null) => void) => void;
        removeItem: (key: string, callback: (err: string | null) => void) => void;
        getKeys: (callback: (err: string | null, keys: string[]) => void) => void;
    };
    downloadFile?: (params: { url: string; file_name: string }, callback: (result: { status?: string } | null) => void) => void;
    addToHomeScreen?: () => void;
    checkHomeScreenStatus?: (callback: (status: string) => void) => void;
    shareMessage?: (text: string, url: string | null, callback: (success: boolean) => void) => void;
};

/** Объект Telegram, доступный как `window.Telegram` (с расширенным WebApp) */
export interface TelegramAPI {
    WebApp: ExtendedWebApp;
}
