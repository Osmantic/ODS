const RECOVERY_KEY = 'ods-dashboard-stale-asset-reload'
const RECOVERY_WINDOW_MS = 30_000

/**
 * Vite gives lazy chunks content-hashed names. A tab left open across an ODS
 * update can therefore ask the new server for a chunk from the previous build.
 * Recognize only the browser/bundler errors associated with that condition so
 * genuine application crashes remain visible to the error boundary.
 */
export function isStaleAssetError(error) {
  const description = `${error?.name || ''} ${error?.message || error || ''}`.toLowerCase()

  return [
    'chunkloaderror',
    'loading chunk',
    'failed to fetch dynamically imported module',
    'error loading dynamically imported module',
    'importing a module script failed',
    'unable to preload css',
  ].some((fragment) => description.includes(fragment))
}

/**
 * Reload at most once in a short window. If the new build also fails, callers
 * render a useful fallback instead of entering a reload loop.
 */
export function recoverFromStaleAsset(error, {
  storage = globalThis.sessionStorage,
  reload = () => globalThis.location.reload(),
  href = globalThis.location?.href || '',
  now = Date.now(),
} = {}) {
  if (!isStaleAssetError(error)) return false

  try {
    const previous = JSON.parse(storage.getItem(RECOVERY_KEY) || 'null')
    if (previous?.at && now - previous.at < RECOVERY_WINDOW_MS) return false

    storage.setItem(RECOVERY_KEY, JSON.stringify({ at: now, href }))
  } catch {
    // Do not risk an infinite loop when session storage is unavailable.
    return false
  }

  reload()
  return true
}

export function clearStaleAssetRecovery(storage = globalThis.sessionStorage) {
  try {
    storage.removeItem(RECOVERY_KEY)
  } catch {
    // Storage can be disabled without preventing normal dashboard use.
  }
}

export { RECOVERY_KEY, RECOVERY_WINDOW_MS }
