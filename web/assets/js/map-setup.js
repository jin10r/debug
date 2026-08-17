// Synchronous setup: auth headers, default config, user info
// (was inline in map.html — externalized for strict CSP, script-src 'self').

// JWT auth headers for API requests.
window.getAuthHeaders = function() {
  const headers = { 'Content-Type': 'application/json' };
  const token = sessionStorage.getItem('access_token');
  if (token) {
    headers['Authorization'] = 'Bearer ' + token;
  }
  return headers;
};

// Default config — overridden by /api/config once the session is confirmed.
window.APP_CONFIG = {
  map_center_lat: 46.4825,
  map_center_lng: 30.7233,
  map_default_zoom: 12,
  enable_random_points: true
};

// User info from sessionStorage.
try {
  const userData = sessionStorage.getItem('user');
  if (userData) {
    const user = JSON.parse(userData);
    window.currentUserId = user.id;
    window.currentUserName = user.first_name || user.username;
  }
} catch (e) {
  console.error('Failed to parse user data:', e);
}
if (sessionStorage.getItem('dev_mode') === 'true') {
  window.currentUserId = window.currentUserId || 'dev_user';
  window.currentUserName = window.currentUserName || 'Dev User';
}
window.isAdmin = false;

// Online/offline status indicator.
window.addEventListener('online', () => {
  if (window.updateOnlineStatus) window.updateOnlineStatus(true);
});
window.addEventListener('offline', () => {
  if (window.updateOnlineStatus) window.updateOnlineStatus(false);
});
