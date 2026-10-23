// @vitest-environment node
//
// Focused regression for the real ODS Vite dev proxy. Loads the real
// vite.config.js factory, starts a real Vite dev server on a reserved
// loopback port, and points /api at a disposable recorder. Verifies the
// server-side key-injection helper: credentialless browser requests get the
// admin key injected upstream, unsafe requests are rejected with 403 before
// reaching the backend, and public endpoints never receive the key.
//
// Live API authentication is checked separately against the installed backend.

import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import http from 'node:http'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve, dirname, basename } from 'node:path'
import { Buffer } from 'node:buffer'
import { createServer as createViteServer } from 'vite'
import viteConfig, { createDashboardConfig } from '../../vite.config.js'
import { createDevApiAccess } from '../../dev-api-access.js'

// Proxy tests must not rewrite a contributor's running Vite dependency cache.
const temporaryCaches = []
afterAll(async () => {
  for (const cache of temporaryCaches) {
    if (dirname(resolve(cache)) !== resolve(tmpdir()) || !basename(cache).startsWith('ods-vite-proxy-')) {
      throw new Error('Unexpected test cache path')
    }
    await rm(cache, {recursive:true, force:true})
  }
})

const REQUEST_TIMEOUT_MS = 5000
const CLOSE_TIMEOUT_MS = 5000
const SYNTHETIC_KEY = 'synthetic-test-admin-key-do-not-use'

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      server.removeListener('error', reject)
      resolve(server.address())
    })
  })
}

function closeServer(server) {
  return new Promise((resolve) => {
    if (!server || !server.listening) return resolve()
    const timer = setTimeout(() => {
      try { server.closeAllConnections?.() } catch { /* ignore */ }
      resolve()
    }, CLOSE_TIMEOUT_MS)
    server.close(() => { clearTimeout(timer); resolve() })
  })
}

function createRecorder() {
  const observed = []
  const server = http.createServer((req, res) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      observed.push({
        method: req.method,
        url: req.url,
        host: req.headers.host ?? null,
        origin: req.headers.origin ?? null,
        authorization: req.headers.authorization ?? null,
        cookie: req.headers.cookie ?? null,
        secFetchSite: req.headers['sec-fetch-site'] ?? null,
        via: req.headers.via ?? null,
        forwarded: req.headers.forwarded ?? null,
        xForwardedFor: req.headers['x-forwarded-for'] ?? null,
        xForwardedHost: req.headers['x-forwarded-host'] ?? null,
        xForwardedProto: req.headers['x-forwarded-proto'] ?? null,
        body: Buffer.concat(chunks).toString('utf8'),
      })
      const status = /^\/api\/status\/(401|403)$/.exec(req.url)?.[1]
      res.writeHead(status ? Number(status) : 200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ ok: true }))
    })
  })
  return { server, observed }
}

function request(url, { method = 'GET', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? undefined : JSON.stringify(body)
    const req = http.request(url, {
      method,
      headers: { ...(data === undefined ? {} : { 'content-type': 'application/json' }), ...headers },
    }, res => {
      const chunks = []
      res.on('data', chunk => chunks.push(chunk))
      res.on('error', reject)
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        let parsed = text
        try { parsed = JSON.parse(text) } catch { /* ordinary source/HTML */ }
        resolve({ status: res.statusCode, body: parsed, headers: res.headers })
      })
    })
    const timer = setTimeout(() => req.destroy(new Error('request timed out')), REQUEST_TIMEOUT_MS)
    req.on('error', reject)
    req.on('close', () => clearTimeout(timer))
    req.end(data)
  })
}

async function reservePort() {
  const s = http.createServer()
  const addr = await listen(s)
  await closeServer(s)
  return addr.port
}

async function startVite(upstreamPort, { withKey = true } = {}) {
  const cacheDir = await mkdtemp(join(tmpdir(), 'ods-vite-proxy-'))
  temporaryCaches.push(cacheDir)
  const frontendPort = await reservePort()
  const base = withKey
    ? createDashboardConfig(SYNTHETIC_KEY)
    : createDashboardConfig(undefined)
  const realProxy = base.server?.proxy?.['/api']
  if (!realProxy) throw new Error('config missing server.proxy[/api]')

  const vite = await createViteServer({
    ...base,
    cacheDir,
    optimizeDeps: {noDiscovery:true, include:[]},
    configFile: false,
    logLevel: 'silent',
    server: {
      ...base.server,
      host: '127.0.0.1',
      port: frontendPort,
      strictPort: true,
      hmr: false,
      proxy: {
        ...base.server.proxy,
        '/api': { ...realProxy, target: `http://127.0.0.1:${upstreamPort}` },
      },
    },
  })
  await vite.listen()
  const addr = vite.httpServer.address()
  return { vite, origin: `http://127.0.0.1:${addr.port}`, port: addr.port }
}

describe('vite dev proxy /api key injection and CSRF gate', () => {
  let recorder
  let upstreamAddr
  let vite
  let origin
  let frontendPort

  beforeAll(async () => {
    recorder = createRecorder()
    upstreamAddr = await listen(recorder.server)
    const started = await startVite(upstreamAddr.port, { withKey: true })
    vite = started.vite
    origin = started.origin
    frontendPort = started.port
  }, 20000)

  afterAll(async () => {
    try { await vite?.close() } finally { await closeServer(recorder?.server) }
  }, 20000)

  it('credentialless same-origin POST gets key injected; body/Host/Origin unchanged', async () => {
    const before = recorder.observed.length
    const payload = { probe: 'inject', n: 1 }
    const res = await request(`${origin}/api/probe?x=1`, {
      method: 'POST',
      headers: { origin, 'sec-fetch-site': 'same-origin' },
      body: payload,
    })
    expect(res.status).toBe(200)
    expect(recorder.observed.length).toBe(before + 1)
    const seen = recorder.observed[before]
    expect(seen.host).toBe(`127.0.0.1:${frontendPort}`)
    expect(seen.origin).toBe(origin)
    expect(seen.authorization).toBe(`Bearer ${SYNTHETIC_KEY}`)
    expect(seen.method).toBe('POST')
    expect(seen.url).toBe('/api/probe?x=1')
    expect(JSON.parse(seen.body)).toEqual(payload)
    expect(seen.via).toBeNull()
    expect(seen.forwarded).toBeNull()
    expect(seen.xForwardedFor).toBeNull()
    expect(seen.xForwardedHost).toBeNull()
    expect(seen.xForwardedProto).toBeNull()
  })

  it('GET with Sec-Fetch-Site same-origin is gated and injected', async () => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/probe`, {
      headers: { origin, 'sec-fetch-site': 'same-origin' },
    })
    expect(res.status).toBe(200)
    expect(recorder.observed.length).toBe(before + 1)
    expect(recorder.observed[before].authorization).toBe(`Bearer ${SYNTHETIC_KEY}`)
  })

  it('missing Origin allowed for local CLI; key injected', async () => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/probe`, {
      method: 'POST',
      headers: { 'sec-fetch-site': 'none' },
      body: { probe: 'cli' },
    })
    expect(res.status).toBe(200)
    expect(recorder.observed.length).toBe(before + 1)
    expect(recorder.observed[before].authorization).toBe(`Bearer ${SYNTHETIC_KEY}`)
  })

  it('public /api/talk/ does not receive injected key', async () => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/talk/`, {
      method: 'POST',
      headers: { origin, 'sec-fetch-site': 'same-origin', cookie: 'ods-session=synthetic-session', authorization: 'Bearer caller-key' },
      body: { probe: 'talk' },
    })
    expect(res.status).toBe(200)
    expect(recorder.observed.length).toBe(before + 1)
    expect(recorder.observed[before].authorization).toBeNull()
    expect(recorder.observed[before].cookie).toBe('ods-session=synthetic-session')
  })

  it('public /api/auth/dashboard-session/login does not receive injected key', async () => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/auth/dashboard-session/login`, {
      method: 'POST',
      headers: { origin, 'sec-fetch-site': 'same-origin' },
      body: { probe: 'login' },
    })
    expect(res.status).toBe(200)
    expect(recorder.observed[before].authorization).toBeNull()
  })

  it('replaces caller Authorization only on admitted admin requests', async () => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/probe`, {
      method: 'POST', headers: { origin, authorization: 'Bearer caller-key' }, body: {},
    })
    expect(res.status).toBe(200)
    expect(recorder.observed[before].authorization).toBe(`Bearer ${SYNTHETIC_KEY}`)
  })

  it('public /api/auth/dashboard-session/logout does not receive injected key', async () => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/auth/dashboard-session/logout`, {
      method: 'POST',
      headers: { origin, 'sec-fetch-site': 'same-origin' },
      body: { probe: 'logout' },
    })
    expect(res.status).toBe(200)
    expect(recorder.observed[before].authorization).toBeNull()
  })

  it('non-API asset remains accessible', async () => {
    const res = await request(`${origin}/`)
    expect(res.status).toBe(200)
    expect(res.body).toContain('<html')
  })

  it('client env / static headers do not leak the key', async () => {
    const res = await request(`${origin}/@vite/client`)
    expect(res.status).toBe(200)
    const text = typeof res.body === 'string' ? res.body : JSON.stringify(res.body)
    expect(text).not.toContain(SYNTHETIC_KEY)
    for (const v of Object.values(res.headers)) {
      expect(String(v)).not.toContain(SYNTHETIC_KEY)
    }
  })

  // Table of unsafe requests: each must 403 and never reach the backend.
  const unsafeCases = [
    { name: 'invalid Origin', headers: { origin: 'http://evil.example.test', 'sec-fetch-site': 'same-origin' } },
    { name: 'cross-site Sec-Fetch-Site', headers: { 'sec-fetch-site': 'cross-site' } },
    { name: 'Host missing port', headers: {}, overrideHost: '127.0.0.1' },
    { name: 'Host wrong port', headers: {}, overrideHost: '127.0.0.1:1' },
    { name: 'same-site Fetch-Site', headers: { 'sec-fetch-site': 'same-site' } },
    { name: 'null Origin', headers: { origin: 'null' } },
    { name: 'HTTPS Origin', headers: { origin: 'https://127.0.0.1' } },
    { name: 'forwarded real IP', headers: { 'x-real-ip': '127.0.0.1' } },
    { name: 'unknown forwarded header', headers: { 'x-forwarded-port': '1' } },
    { name: 'credentialed Host', headers: {}, overrideHost: 'owner@127.0.0.1:1' },
    { name: 'Host malformed', headers: {}, overrideHost: 'not a host' },
    { name: 'forwarded Via', headers: { 'sec-fetch-site': 'same-origin', via: '1.1 proxy' } },
    { name: 'forwarded X-Forwarded-For', headers: { 'sec-fetch-site': 'same-origin', 'x-forwarded-for': '10.0.0.1' } },
    { name: 'forwarded X-Forwarded-Host', headers: { 'sec-fetch-site': 'same-origin', 'x-forwarded-host': 'evil.test' } },
    { name: 'forwarded X-Forwarded-Proto', headers: { 'sec-fetch-site': 'same-origin', 'x-forwarded-proto': 'https' } },
    { name: 'forwarded Forwarded', headers: { 'sec-fetch-site': 'same-origin', forwarded: 'for=10.0.0.1' } },
  ]

  for (const c of unsafeCases) {
    it.each(['GET', 'POST'])(`rejects ${c.name} on %s without reaching the backend`, async method => {
      const before = recorder.observed.length
      const headers = { origin, 'sec-fetch-site': 'same-origin', ...c.headers }
      if (c.overrideHost) headers.host = c.overrideHost
      const res = await request(`${origin}/api/probe`, {
        method,
        headers,
        body: method === 'POST' ? { probe: c.name } : undefined,
      })
      expect(res.status).toBe(403)
      expect(recorder.observed.length).toBe(before)
      // Generic error body: no key, no internal detail.
      const text = typeof res.body === 'string' ? res.body : JSON.stringify(res.body)
      expect(text).not.toContain(SYNTHETIC_KEY)
    })
  }

  it.each([401, 403])('preserves upstream %i responses', async status => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/status/${status}`, {
      method: 'POST', headers: { origin, 'sec-fetch-site': 'same-origin' }, body: {},
    })
    expect(recorder.observed.length).toBe(before + 1)
    expect(res.status).toBe(status)
    expect(res.body).toEqual({ ok: true })
  })

})

describe('vite dev proxy without configured key', () => {
  let recorder
  let upstreamAddr
  let vite
  let origin

  beforeAll(async () => {
    recorder = createRecorder()
    upstreamAddr = await listen(recorder.server)
    const started = await startVite(upstreamAddr.port, { withKey: false })
    vite = started.vite
    origin = started.origin
  }, 20000)

  afterAll(async () => {
    try { await vite?.close() } finally { await closeServer(recorder?.server) }
  }, 20000)

  it('existing Authorization passes through unchanged', async () => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/probe`, {
      method: 'POST',
      headers: {
        origin,
        'sec-fetch-site': 'same-origin',
        authorization: 'Bearer caller-supplied',
      },
      body: { probe: 'passthrough' },
    })
    expect(res.status).toBe(200)
    expect(recorder.observed.length).toBe(before + 1)
    expect(recorder.observed[before].authorization).toBe('Bearer caller-supplied')
  })

  it('missing Authorization stays absent', async () => {
    const before = recorder.observed.length
    const res = await request(`${origin}/api/probe`, {
      method: 'POST',
      headers: { origin, 'sec-fetch-site': 'same-origin' },
      body: { probe: 'no-auth' },
    })
    expect(res.status).toBe(200)
    expect(recorder.observed[before].authorization).toBeNull()
  })
})

describe('resolved Vite configuration boundary', () => {
  const invalid = [
    ['wide listener', { host: '0.0.0.0' }],
    ['CLI --host', { host: true }],
    ['IPv6 wide listener', { host: '::' }],
    ['middleware mode', { middlewareMode: true }],
    ['HTTPS', { https: {} }],
    ...[
      ['remote target', { target: 'http://example.com:3002' }],
      ['credentialed target', { target: 'http://u:p@127.0.0.1:3002' }],
      ['path target', { target: 'http://127.0.0.1:3002/prefix' }],
      ['query target', { target: 'http://127.0.0.1:3002/?x=1' }],
      ['Host rewrite', { changeOrigin: true }],
      ['websocket', { ws: true }],
      ['redirect following', { followRedirects: true }],
    ].map(([name, options]) => [name, { proxy: options }]),
  ]
  it.each(invalid)('rejects %s before listening', async (_name, override) => {
    const config = createDashboardConfig(SYNTHETIC_KEY)
    const server = { ...config.server, ...override }
    server.proxy = { '/api': { ...config.server.proxy['/api'], ...override.proxy } }
    await expect(createViteServer({ ...config, configFile: false, logLevel: 'silent', server }))
      .rejects.toThrow(/ODS development API credentials require a loopback/)
  })

  it('build config needs no key and exposes no static credentials', () => {
    const config = viteConfig({ command: 'build' })
    expect(config.server.host).toBe('127.0.0.1')
    expect(config.server.strictPort).toBe(true)
    expect(config.server.proxy['/api'].changeOrigin).toBe(false)
    expect(config.server.proxy['/api'].target).toBe('http://127.0.0.1:3002')
    expect(JSON.stringify(createDashboardConfig(SYNTHETIC_KEY))).not.toContain(SYNTHETIC_KEY)
  })

  it('invalid key error does not reveal the offending key', () => {
    expect(() => createDashboardConfig('private-value\nheader: injected'))
      .toThrow('DASHBOARD_API_KEY is not a valid HTTP header value.')
  })
})

describe('socket and duplicate-header boundary', () => {
  function runGate(remoteAddress, host, origin, extraRawHeaders = []) {
    const access = createDevApiAccess(SYNTHETIC_KEY)
    let gate
    access.plugin.configureServer({ middlewares: { use(fn) { gate = fn } } })
    const result = { next: false, status: null }
    gate({ url: '/api/probe', socket: { remoteAddress, localPort: 3001 },
      headers: { host, origin }, rawHeaders: ['Host', host, 'Origin', origin, ...extraRawHeaders],
    }, { writeHead(status) { result.status = status }, end() {} }, () => { result.next = true })
    return result
  }

  it('rejects a non-loopback socket even with matching local headers', () => {
    expect(runGate('192.168.1.9', '127.0.0.1:3001', 'http://127.0.0.1:3001'))
      .toEqual({ next: false, status: 403 })
  })
  it('accepts IPv6 loopback with bracketed matching authority', () => {
    expect(runGate('::1', '[::1]:3001', 'http://[::1]:3001'))
      .toEqual({ next: true, status: null })
  })
  it('accepts an IPv4-mapped loopback peer', () => {
    expect(runGate('::ffff:127.0.0.1', 'localhost:3001', 'http://localhost:3001'))
      .toEqual({ next: true, status: null })
  })
  it.each(['Host', 'Origin', 'Sec-Fetch-Site'])('rejects duplicate %s fields', name => {
    const extras = [name, 'ignored']
    if (name === 'Sec-Fetch-Site') extras.push(name, 'same-origin')
    expect(runGate('127.0.0.1', 'localhost:3001', 'http://localhost:3001', extras))
      .toEqual({ next: false, status: 403 })
  })
})
