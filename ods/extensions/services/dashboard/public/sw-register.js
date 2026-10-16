// Register the PWA service worker after the page loads so it doesn't delay
// first paint. Kept as a same-origin file because the dashboard CSP blocks
// inline scripts. Browsers only allow registration in secure contexts
// (HTTPS or localhost); the PWA install affordance has the same gate.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/sw.js').catch(function (err) {
      // Non-fatal: without a service worker the dashboard simply is not
      // installable as a PWA; it works the same otherwise.
      console.warn('ODS service worker registration failed', err);
    });
  });
}
