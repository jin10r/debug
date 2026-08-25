// Telegram WebApp initialization (externalized to satisfy strict CSP)
(function() {
    if (window.Telegram && window.Telegram.WebApp) {
        Telegram.WebApp.ready();
        Telegram.WebApp.expand();
    }
})();
