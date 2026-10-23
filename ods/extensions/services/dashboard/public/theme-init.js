// Prevent a flash of the wrong theme: runs synchronously in <head>, before
// React and before first paint. Kept as a same-origin file because the
// dashboard CSP (script-src 'self' ...) blocks inline scripts.
document.documentElement.setAttribute('data-theme', 'ods');
