// ODS dashboard service worker.
//
// Minimal shell — registers the dashboard as a PWA so users can install it
// to their phone's home screen. We deliberately do NOT cache API responses
// or vendored chunks — the dashboard is a live system-status surface, and a
// stale cache would mask the actual state of the running stack.
//
// What this gets us:
//   - "Add to Home Screen" prompt fires on iOS / Android
//   - Standalone display mode (no browser chrome) when launched from icon
//   - Splash screen using theme_color + name from manifest.webmanifest
//
// What's intentionally NOT done here:
//   - No precache. The shell is fast enough fresh.
//   - No runtime caching of /api/* — that would silently mask down services.
//   - No background sync. The dashboard polls when open; idle = no work.
//
// If we later want offline support for the chat surface, that lives in
// Open WebUI's own service worker, not here.

const VERSION = 'ods-dashboard-sw-v1';
const CACHE_PREFIX = 'ods-dashboard-sw-';

self.addEventListener('install', (event) => {
  // Skip the waiting-for-existing-pages dance — install immediately.
  // The dashboard is fine if the worker swaps mid-session; nothing is cached.
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', (event) => {
  // Claim all open tabs so the new worker controls them right away, and clear
  // caches created by previous dashboard workers without touching unrelated
  // applications sharing this origin.
  event.waitUntil(Promise.all([
    self.clients.claim(),
    caches.keys().then((keys) => Promise.all(
      keys
        .filter((key) => key.startsWith(CACHE_PREFIX) && key !== VERSION)
        .map((key) => caches.delete(key)),
    )),
  ]));
});

// No fetch handler — let the network handle everything. Adding a no-op
// handler would still serialize requests through the worker; omitting it
// keeps the dashboard at native speed.
