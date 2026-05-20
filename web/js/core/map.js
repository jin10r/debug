// map.js — popup-helpers and icon registry for the MapLibre renderer.
//
// MapLibre is data-driven (single GeoJSON source per layer category) — мы не
// создаём по-фичевые объекты, как это делал Leaflet. Этот модуль теперь
// маленький: только id иконок (для symbol-слоёв) и сборка HTML попапа.

const ICON_NAMES = {
    cops: 'cops-icon',
    bus: 'bus-icon',
    pig: 'pig-icon'
};
window.ICON_NAMES = ICON_NAMES;

const ICON_URLS = {
    cops: '/assets/images/cops.png',
    bus: '/assets/images/bus.png',
    pig: '/assets/images/pig.png'
};
window.ICON_URLS = ICON_URLS;

/**
 * Сборка HTML попапа события. Описание прогоняется через
 * window.processTelegramHTML (HTML-экранирование), photo_url подставляется
 * как обычный URL с onerror — если файл уже удалён pg_cron'ом или nginx
 * вернул HTML-фоллбэк, картинка молча скрывается.
 */
window.createPopupContent = function(properties) {
    if (!properties) return '';

    const time = properties.time ? window.formatDateTime(properties.time) : '';
    const description = properties.description
        ? `<span style="color: var(--tg-text-color, #000000);">${window.processTelegramHTML(properties.description)}</span>`
        : '';

    const photoUrl = properties.photo_url;
    const photoHtml = photoUrl
        ? `<div style="margin-top: 8px;">`
          + `<img src="${photoUrl}"`
          + ` style="width: auto; max-width: 100%; height: auto; max-height: 80vh; border-radius: 8px;"`
          + ` alt="Event photo"`
          + ` onerror="this.style.display='none'; const p=this.parentElement; if(p)p.style.display='none';">`
          + `</div>`
        : '';

    const timeHtml = time
        ? `<span style="font-weight: bold; display: block; margin-bottom: 4px;">${time}</span>`
        : '';

    return `<div class="photo-container" style="text-align: center; max-width: 360px; color: var(--tg-text-color, #000000); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">`
         + `${timeHtml}${photoHtml}${description}`
         + `</div>`;
};

console.log('✅ Map helpers loaded (MapLibre)');
