// Hard validation gate + bootstrap (was inline in map.html — externalized for
// strict CSP, script-src 'self'). Loads /dist/js/* components via src= (covered
// by 'self'); runs after the leaflet/maplibre libs.
(async function() {
  // ====================================================================
  // Rule 2 — hard validation gate.
  // No frontend component (/dist/js/*) is injected until the backend
  // confirms a valid session. An invalid session bounces to the gate
  // page (/index.html), which redirects to REDIRECT_URL.
  // ====================================================================

  const token = sessionStorage.getItem('access_token');
  const devMode = sessionStorage.getItem('dev_mode') === 'true';

  // No credentials at all → back to the validation gate page.
  if (!token && !devMode) {
    window.location.replace('/index.html');
    return;
  }

  // Confirm the session with the backend before loading any component.
  // Returns true to proceed, false when redirecting/reloading.
  async function confirmSession() {
    let response;
    try {
      response = await fetch('/api/config', {
        method: 'POST',
        headers: window.getAuthHeaders(),
        body: JSON.stringify({})
      });
    } catch (err) {
      // Network error → offline. PWA rule 1: trust the prior session and
      // run from the service-worker shell + localStorage cache.
      console.warn('[GATE] Offline — proceeding from cache:', err);
      return true;
    }

    if (response.status === 401) {
      // Token expired/invalid — a single refresh attempt.
      const refreshToken = sessionStorage.getItem('refresh_token');
      if (refreshToken) {
        try {
          const r = await fetch('/api/auth/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken })
          });
          if (r.ok) {
            const d = await r.json();
            sessionStorage.setItem('access_token', d.access_token);
            location.reload();
            return false;
          }
        } catch (e) {
          console.error('[GATE] Refresh failed:', e);
        }
      }
      // Invalid session → gate page handles REDIRECT_URL.
      window.location.replace('/index.html');
      return false;
    }

    if (response.ok) {
      try {
        const serverConfig = await response.json();
        window.APP_CONFIG = { ...window.APP_CONFIG, ...serverConfig };
        console.log('[GATE] Session confirmed, config loaded');
      } catch (e) {
        console.warn('[GATE] Config parse failed, using defaults');
      }
      return true;
    }

    console.warn('[GATE] Unexpected /api/config status:', response.status);
    window.location.replace('/index.html');
    return false;
  }

  const sessionOk = await confirmSession();
  if (!sessionOk) return; // redirected or reloading — load nothing

  // ====================================================================
  // Session confirmed — load frontend components.
  // ====================================================================
  const loadScript = (src) => new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => resolve(true);
    s.onerror = () => reject(new Error('Failed to load ' + src));
    document.head.appendChild(s);
  });

  try {
    const fallbackV = String(Date.now());
    await loadScript(`/js/telegram/integration.js/__v__/${fallbackV}`);

    if (window.telegramIntegration) {
      window.telegramIntegration.init();
      window.telegramIntegration.on('onActivated', () => console.log('App activated'));
      window.telegramIntegration.on('onViewportChanged', (e) => console.log('Viewport changed:', e));
    }
    if (window.Telegram?.WebApp) {
      window.Telegram.WebApp.ready();
      window.Telegram.WebApp.expand();
    }

    const v = Date.now();
    await loadScript(`/dist/js/common.js?t=${v}`);
    await loadScript(`/dist/js/core/store.js?t=${v}`);
    await loadScript(`/dist/js/core/local_cache.js?t=${v}`);
    await loadScript(`/dist/js/core/websocket.js?t=${v}`);
    await loadScript(`/dist/js/core/event_manager.js?t=${v}`);
    await loadScript(`/dist/js/core/token-manager.js?t=${v}`);
    await loadScript(`/dist/js/core/map.js?t=${v}`);
    await loadScript(`/dist/js/core/data.js?t=${v}`);
    await loadScript(`/dist/js/modules/popups.js?t=${v}`);
    await loadScript(`/dist/js/modules/notifications.js?t=${v}`);
    await loadScript(`/dist/js/core/ui.js?t=${v}`);

    console.log('✅ All components loaded');

    if (window.tokenManager) {
      await window.tokenManager.init();
    }
    if (window.bootstrapUI) {
      window.bootstrapUI();
    } else {
      console.error('❌ bootstrapUI not found');
    }
  } catch (error) {
    console.error('❌ Failed to load app:', error);
    const msg = 'Ошибка загрузки приложения. ' +
      'Пожалуйста, закройте и заново откройте мини‑приложение.';
    if (window.telegramIntegration?.showAlert) {
      try { await window.telegramIntegration.showAlert(msg); } catch (e) { alert(msg); }
    } else if (window.Telegram?.WebApp?.showAlert) {
      try { await window.Telegram.WebApp.showAlert(msg); } catch (e) { alert(msg); }
    } else {
      alert(msg);
    }
  }

  // Register the service worker (PWA rule 1) after boot so it never
  // delays first paint.
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch((e) => {
      console.warn('[SW] registration failed:', e);
    });
  }
})();
