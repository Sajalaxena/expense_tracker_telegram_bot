(function () {
  'use strict';

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js').catch(function (err) {
        console.warn('Service worker registration failed:', err);
      });
    });
  }

  // Any element with [data-install-app] stays hidden until Chrome says the
  // app is installable, then triggers the native install prompt on click.
  var deferredPrompt = null;

  function setInstallVisible(visible) {
    var buttons = document.querySelectorAll('[data-install-app]');
    for (var i = 0; i < buttons.length; i++) buttons[i].hidden = !visible;
  }

  function isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
    if (!isStandalone()) setInstallVisible(true);
  });

  window.addEventListener('appinstalled', function () {
    deferredPrompt = null;
    setInstallVisible(false);
  });

  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('[data-install-app]');
    if (!btn || !deferredPrompt) return;
    deferredPrompt.prompt();
    deferredPrompt.userChoice.finally(function () {
      deferredPrompt = null;
      setInstallVisible(false);
    });
  });
})();
