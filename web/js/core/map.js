// map.js — функции создания геометрии на карте
// Архитектурно оптимизированная версия для Telegram Mini Apps

// Глобальные функции для создания геометрии
// Эти функции будут доступны глобально после загрузки скрипта

const ICON_CONFIG = {
    cops: { url: '/assets/images/cops.png', size: [25, 25] },
    bus: { url: '/assets/images/bus.png', size: [25, 25] },
    pig: { url: '/assets/images/pig.png', size: [25, 25] }
};

// Слой traffic не имеет PNG-иконки — рендерим эмодзи ⛔ через L.divIcon,
// согласовано с легендой и чекбоксом в #layerControls.
window.createIcon = function(layer) {
    if (layer === 'traffic') {
        return L.divIcon({
            html: '<span style="font-size:22px;line-height:25px;">⛔</span>',
            className: 'traffic-emoji-icon',
            iconSize: [25, 25],
            iconAnchor: [12.5, 12.5],
            popupAnchor: [0, -20]
        });
    }
    const config = ICON_CONFIG[layer] || ICON_CONFIG.pig;
    return L.icon({
        iconUrl: config.url,
        iconSize: config.size,
        iconAnchor: [12.5, 12.5],
        popupAnchor: [0, -20]
    });
};

window.createMarker = function(map, latLng, properties) {
    const marker = L.marker(latLng, { icon: window.createIcon(properties.layer) });
    marker.bindPopup(window.createPopupContent(properties));
    return marker;
};

window.createCircle = function(map, coords, properties, strategy) {
    const latLng = L.latLng(coords[1], coords[0]);
    const marker = window.createMarker(map, latLng, properties);

    // Стратегия 'random' — событие без точной привязки к местности.
    // Такие точки НЕ должны иметь радиуса (круга): радиус подразумевает
    // точность, которой у случайной точки нет. Только маркер.
    if (strategy === 'random') {
        return [marker]; // Только маркер, без круга
    }

    // Для маркеров с точным местоположением добавляем круг радиусом 200м
    const circle = L.circle(latLng, {
        color: 'red',
        fillColor: '#f03',
        fillOpacity: 0.5,
        radius: 200,
        weight: 0
    });
    circle.bindPopup(window.createPopupContent(properties));

    return [circle, marker];
};

window.getPolylineMidpoint = function(latLngs) {
    if (!latLngs?.length) return null;
    if (latLngs.length === 1) return latLngs[0];

    let totalDistance = 0;
    const distances = [];

    for (let i = 0; i < latLngs.length - 1; i++) {
        const dist = latLngs[i].distanceTo(latLngs[i + 1]);
        distances.push(dist);
        totalDistance += dist;
    }

    const midDistance = totalDistance / 2;
    let distanceCovered = 0;

    for (let i = 0; i < distances.length; i++) {
        if (distanceCovered + distances[i] >= midDistance) {
            const ratio = (midDistance - distanceCovered) / distances[i];
            const p1 = latLngs[i];
            const p2 = latLngs[i + 1];
            return L.latLng(
                p1.lat + (p2.lat - p1.lat) * ratio,
                p1.lng + (p2.lng - p1.lng) * ratio
            );
        }
        distanceCovered += distances[i];
    }

    return latLngs[latLngs.length - 1];
};

window.createPolyline = function(map, coords, properties) {
    const latLngs = coords.map(c => L.latLng(c[1], c[0]));
    const polyline = L.polyline(latLngs, { color: 'blue', weight: 3 });
    polyline.bindPopup(window.createPopupContent(properties));

    const markerPosition = window.getPolylineMidpoint(latLngs);
    const marker = window.createMarker(map, markerPosition, properties);

    return [polyline, marker];
};

window.createPolygon = function(map, coords, properties) {
    const latLngs = coords[0].map(c => L.latLng(c[1], c[0]));
    const polygon = L.polygon(latLngs, {
        color: 'red',
        weight: 2,
        fillColor: '#f03',
        fillOpacity: 0.2
    });
    polygon.bindPopup(window.createPopupContent(properties));

    const marker = window.createMarker(map, L.latLngBounds(latLngs).getCenter(), properties);
    return [polygon, marker];
};

// Оборачивает слова matched_part в <strong> в уже HTML-экранированном тексте.
// matched_part — лемматизированный n-грамм ("молодёжный виноградово").
// Стемный regex: первые max(4, len-2) символов слова леммы + любой суффикс
// → "молодёжн" совпадает с "молодёжной" в description.
function _highlightMatchedParts(escapedText, matches) {
    if (!matches || !matches.length) return escapedText;
    const parts = [...new Set(
        matches.map(m => m.matched_part).filter(p => p && p.trim().length > 1)
    )];
    let result = escapedText;
    for (const part of parts) {
        for (const word of part.split(/\s+/)) {
            if (word.length < 4) continue;
            const stem = word.slice(0, Math.max(word.length - 2, 4));
            const stemEsc = stem.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const regex = new RegExp(
                '(?<![а-яёА-ЯЁa-zA-Z0-9])' + stemEsc + '[а-яёА-ЯЁa-zA-Z0-9]*(?![а-яёА-ЯЁa-zA-Z0-9])',
                'gi'
            );
            result = result.replace(regex, m => `<strong>${m}</strong>`);
        }
    }
    return result;
}

window.createPopupContent = function(properties) {
    if (!properties) return '';

    const time = properties.time ? window.formatDateTime(properties.time) : '';
    const description = properties.description ? (() => {
        const escaped = window.processTelegramHTML(properties.description);
        const highlighted = _highlightMatchedParts(escaped, properties.matches);
        return `<span style="color: var(--tg-text-color, #ffffff);">${highlighted}</span>`;
    })() : '';

    const photoUrl = properties.photo_url;
    const photoHtml = photoUrl ?
        `<div style="margin-top: 8px;"><img src="${photoUrl}" style="width: auto; max-width: 100%; height: auto; max-height: 80vh; border-radius: 8px;" alt="Event photo"></div>` : '';

    const timeHtml = time ? `<span style="font-weight: bold; display: block; margin-bottom: 4px;">${time}</span>` : '';

    return `<div class="photo-container" style="text-align: center; max-width: 360px; color: var(--tg-text-color, #000000); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;">
        ${timeHtml}
        ${photoHtml}
        ${description}
    </div>`;
};

window.createTelegramPopup = function(content, customOptions = {}) {
    const popup = L.popup({ 
        minWidth: 200,
        maxWidth: 400,
        closeButton: true,
        autoClose: false,
        closeOnEscapeKey: true,
        closeOnClick: true,
        className: 'tg-styled-popup',
        offset: [0, 0],
        autoPanPadding: [50, 50],
        ...customOptions
    });
    popup.setContent(content);
    return popup;
};

