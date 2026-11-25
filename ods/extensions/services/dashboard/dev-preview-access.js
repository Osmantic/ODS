import { localRequest } from './dev-api-access.js'

const PREVIEW_PREFIX = '/pixel-preview'
const SNAPSHOT_SEGMENT = /^site-[a-f0-9]{24}$/
const SEGMENT = /^(?!__ods_)(?!__pycache__$)[A-Za-z0-9_[][A-Za-z0-9._[\]-]{0,127}$/
const SOURCE_PATH = /^__ods_source__\/source-[a-f0-9]{24}\.json$/
const CHANGES_PATH = /^__ods_changes__\/(initial|site-[a-f0-9]{24})\.json$/
const MAX_PATH_LENGTH = 1664

function decodeSegment(segment) {
  // Permit encoded brackets (e.g. `%5B`/`%5D`) by decoding each segment once.
  // Any remaining percent, slash, backslash, dot-segment, non-ASCII, or
  // control character is rejected.
  let decoded
  try { decoded = decodeURIComponent(segment) } catch { return null }
  if (decoded === '.' || decoded === '..') return null
  if (decoded.includes('/') || decoded.includes('\\')) return null
  if (decoded.includes('%')) return null
  if ([...decoded].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) > 126)) return null
  return decoded
}

/**
 * Validate a `/pixel-preview/...` request path.
 *
 * Validate before nginx can normalize the path into another privileged route.
 * Keep the allowed metadata paths aligned with pixel_edge._preview_upstream_path.
 */
export function parsePreviewPath(rawUrl) {
  if (typeof rawUrl !== 'string') return { ok: false }
  const hashIndex = rawUrl.indexOf('#')
  if (hashIndex !== -1) return { ok: false }
  const queryIndex = rawUrl.indexOf('?')
  const rawPath = queryIndex === -1 ? rawUrl : rawUrl.slice(0, queryIndex)
  if (!rawPath.startsWith(PREVIEW_PREFIX + '/')) return { ok: false }
  const rest = rawPath.slice(PREVIEW_PREFIX.length + 1)
  // Percent encoding can expand each allowed ASCII byte up to three bytes.
  if (rest.length > 30 + MAX_PATH_LENGTH * 3) return { ok: false }
  const rawSegments = rest.split('/')
  if (rawSegments.length < 2) return { ok: false }
  const siteId = rawSegments[0]
  if (!SNAPSHOT_SEGMENT.test(siteId)) return { ok: false }
  const tailSegments = rawSegments.slice(1)
  if (tailSegments.length === 1 && tailSegments[0] === '') return { ok: true, kind: 'static' }
  // Like the edge, a directory selects its index.html. Empty interior segments
  // remain invalid, including doubled slashes nginx could otherwise collapse.
  if (tailSegments.at(-1) === '') tailSegments[tailSegments.length - 1] = 'index.html'
  const decoded = []
  for (const segment of tailSegments) {
    if (segment === '') return { ok: false }
    const value = decodeSegment(segment)
    if (value === null) return { ok: false }
    decoded.push(value)
  }
  const tail = decoded.join('/')
  if (tail.length > MAX_PATH_LENGTH) return { ok: false }
  if (SOURCE_PATH.test(tail)) return { ok: true, kind: 'source' }
  if (tail === '__ods_manifest__.json' || tail === '__ods_view__.html' || CHANGES_PATH.test(tail)) {
    return { ok: true, kind: 'static' }
  }
  return decoded.every(part => SEGMENT.test(part)) ? { ok: true, kind: 'static' } : { ok: false }
}

function assertPreviewConfig(config) {
  const server = config.server
  if (!server || server.host !== '127.0.0.1' || server.https || server.middlewareMode) {
    throw new Error('ODS development preview requires a strict loopback HTTP listener on 127.0.0.1.')
  }
  if (server.strictPort !== true) {
    throw new Error('ODS development preview requires strictPort: true.')
  }
  if (server.cors !== false) {
    throw new Error('ODS development preview requires cors: false to preserve source responses lacking CORS.')
  }
  const options = server.proxy?.[PREVIEW_PREFIX]
  let target
  try { target = new URL(options?.target) } catch { /* rejected below */ }
  if (!target || target.protocol !== 'http:' || target.hostname !== '127.0.0.1'
    || target.username || target.password || target.pathname !== '/' || target.search || target.hash
    || options.changeOrigin !== false || options.ws || options.followRedirects
    || options.xfwd) {
    throw new Error('ODS development preview requires a loopback HTTP nginx target with Host preserved, no credentials, and redirects/websockets/xfwd disabled.')
  }
  const targetPort = Number(target.port || 80)
  const apiTarget = server.proxy?.['/api']?.target
  let apiPort = null
  if (apiTarget) {
    try { apiPort = Number(new URL(apiTarget).port || 80) } catch { /* ignore */ }
  }
  if (targetPort === Number(server.port) || (apiPort !== null && targetPort === apiPort)) {
    throw new Error('ODS development preview target must not be the Vite listener or the API port.')
  }
}

/**
 * Server-only preview gate. Always active on `serve`, independent of whether
 * DASHBOARD_API_KEY is configured. Only GET/HEAD are permitted. Requests must
 * pass the local boundary; opaque `Origin: null` cross-site snapshot reads are
 * allowed for static assets, but `__ods_source__/source-*.json` requires a
 * same-origin request. Non-canonical paths are rejected before any upstream
 * is contacted, and reserved prefixes are never handed to the SPA fallback.
 */
export function createDevPreviewAccess() {
  function configureAccess(server) {
    server.middlewares.use((req, res, next) => {
      const url = req.url
      if (typeof url !== 'string') return next()
      if (!url.startsWith(PREVIEW_PREFIX)) return next()
      // Reserve the prefix even for malformed requests so the SPA fallback
      // never serves HTML for a preview path.
      const reject = (status, detail) => {
        res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' })
        res.end(JSON.stringify({ detail }))
      }
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        return reject(405, 'Preview requests must be GET or HEAD.')
      }
      const parsed = parsePreviewPath(url)
      if (!parsed.ok) return reject(404, 'Preview path not found.')
      const sameOrigin = parsed.kind === 'source'
      if (!localRequest(req, { sameOrigin })) {
        return reject(403, 'Local development preview request required.')
      }
      next()
    })
  }
  return {
    plugin: {
      name: 'ods-development-preview-access',
      apply: 'serve',
      configResolved(config) {
        assertPreviewConfig(config)
        assertPreviewConfig({server: config.preview})
      },
      configureServer: configureAccess,
      configurePreviewServer: configureAccess,
    },
  }
}
