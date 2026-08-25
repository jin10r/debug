// Telegram WebApp initialization (externalized to satisfy strict CSP)
(function() {
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
    }
})();
