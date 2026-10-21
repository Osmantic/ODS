import { validateHeaderValue } from 'node:http'
import { isIP } from 'node:net'

const LOOPBACK_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])
const API_PREFIX = '/api'

function loopbackPeer(address) {
  if (address === '::1') return true
  const ipv4 = address?.replace(/^::ffff:/i, '')
  return isIP(ipv4 || '') === 4 && ipv4.startsWith('127.')
}

function localRequest(req) {
  if (!loopbackPeer(req.socket.remoteAddress)) return false
  // Use the actual listener, including a CLI port override.
  const host = req.headers.host
  const authority = typeof host === 'string' && /^(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?$/i.exec(host)
  if (!authority || Number(authority[2] || 80) !== req.socket.localPort) return false
  const counts = new Map()
  for (let i = 0; i < req.rawHeaders.length; i += 2) {
    const name = req.rawHeaders[i].toLowerCase()
    counts.set(name, (counts.get(name) || 0) + 1)
  }
  if (['host', 'origin', 'sec-fetch-site'].some(name => counts.get(name) > 1)) return false
  if (Object.keys(req.headers).some(name => name === 'forwarded' || name === 'via'
    || name === 'x-real-ip' || name.startsWith('x-forwarded-'))) return false
  const site = req.headers['sec-fetch-site']
  if (site !== undefined && site !== 'same-origin' && site !== 'none') return false
  const origin = req.headers.origin
  const port = req.socket.localPort === 80 ? '' : `:${req.socket.localPort}`
  return origin === undefined || origin === `http://${authority[1].toLowerCase()}${port}`
}

function assertLocalConfig(config) {
  const server = config.server
  if (!LOOPBACK_HOSTS.has(server.host || 'localhost') || server.https || server.middlewareMode) {
    throw new Error('ODS development API credentials require a loopback HTTP listener without middlewareMode.')
  }
  const options = server.proxy?.[API_PREFIX]
  let target
  try { target = new URL(options?.target) } catch { /* rejected below */ }
  if (!target || target.protocol !== 'http:' || !LOOPBACK_HOSTS.has(target.hostname)
    || target.username || target.password || target.pathname !== '/' || target.search || target.hash
    || options.changeOrigin !== false || options.ws || options.followRedirects) {
    throw new Error('ODS development API credentials require a loopback HTTP API target with Host preserved and redirects/websockets disabled.')
  }
}

function publicSessionRoute(url) {
  let path
  try { path = decodeURIComponent(url.split('?')[0]) } catch { return true }
  return path.startsWith('/api/talk/') || /^\/api\/auth\/dashboard-session\/(login|logout)\/?$/.test(path)
}

/** Server-only equivalent of nginx's local dashboard credential boundary. */
export function createDevApiAccess(apiKey) {
  const configured = apiKey !== undefined && apiKey !== ''
  if (configured) {
    try {
      if (typeof apiKey !== 'string') throw new Error()
      validateHeaderValue('Authorization', `Bearer ${apiKey}`)
    } catch {
      // Node's original error can contain the offending value; never expose it.
      throw new Error('DASHBOARD_API_KEY is not a valid HTTP header value.')
    }
  }
  const admitted = new WeakSet()
  return {
    plugin: {
      name: 'ods-development-api-access',
      apply: 'serve',
      configResolved(config) {
        if (configured) assertLocalConfig(config)
      },
      configureServer(server) {
        if (!configured) {
          server.config.logger.warn('ODS development API requests need DASHBOARD_API_KEY in the Vite server environment. The browser never receives this key.')
          return
        }
        // A direct hook runs before Vite's proxy middleware. Match the same
        // prefix as Vite, including /api itself, for GET as well as mutations.
        server.middlewares.use((req, res, next) => {
          if (!req.url?.startsWith(API_PREFIX)) return next()
          if (!localRequest(req)) {
            res.writeHead(403, { 'content-type': 'application/json', 'cache-control': 'no-store' })
            res.end(JSON.stringify({ detail: 'Local same-origin development API request required.' }))
            return
          }
          admitted.add(req)
          next()
        })
      },
    },
    configureProxy(proxy) {
      proxy.on('proxyReq', (proxyReq, req) => {
        if (!configured || !admitted.has(req)) return
        // Talk and dashboard sign-in have their own session authentication,
        // just as in production nginx. They never receive the admin key.
        if (publicSessionRoute(req.url)) proxyReq.removeHeader('authorization')
        else proxyReq.setHeader('authorization', `Bearer ${apiKey}`)
      })
    },
  }
}
