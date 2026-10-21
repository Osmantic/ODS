// @vitest-environment node
//
// Regression: with server.proxy['/api'].changeOrigin = true, Vite rewrites
// Host to the upstream target while the browser Origin stays on the frontend
// port, so the API's Origin-vs-Host same-origin check rejects every
// state-changing request with 403. This test loads the real vite.config.js,
// starts a real Vite dev server on an ephemeral loopback port, and points the
// /api proxy at a disposable Node server that only records what the proxy
// forwarded. It does not model API CSRF/auth; those are covered by the real
// API's own tests and a separate FastAPI/browser smoke.

import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import http from 'node:http'
import { Buffer } from 'node:buffer'
import { createServer as createViteServer } from 'vite'
import viteConfig from '../../vite.config.js'

const REQUEST_TIMEOUT_MS = 5000
const CLOSE_TIMEOUT_MS = 5000

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
    server.close(() => {
      clearTimeout(timer)
      resolve()
    })
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
        secFetchSite: req.headers['sec-fetch-site'] ?? null,
        body: Buffer.concat(chunks).toString('utf8'),
      })
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ ok: true }))
    })
  })
  return { server, observed }
}

async function postJson(url, headers, body) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS)
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...headers },
      body: JSON.stringify(body),
      signal: controller.signal,
    })
    const text = await res.text()
    let parsed = null
    try { parsed = JSON.parse(text) } catch { parsed = text }
    return { status: res.status, body: parsed }
  } finally {
    clearTimeout(timer)
  }
}

describe('vite dev proxy /api preserves frontend Host', () => {
  let recorder
  let upstreamAddr
  let vite
  let viteAddr
  let frontendOrigin

  beforeAll(async () => {
    recorder = createRecorder()
    upstreamAddr = await listen(recorder.server)
    // Vite treats port 0 as its default port. Reserve an OS-selected port,
    // then require that exact port so a collision fails instead of drifting.
    const reservation = http.createServer()
    const frontendPort = (await listen(reservation)).port
    await closeServer(reservation)

    const realProxy = viteConfig.server?.proxy?.['/api']
    if (!realProxy) throw new Error('vite.config.js is missing server.proxy[/api]')

    // Override only the disposable target and listener address; keep the real
    // changeOrigin value so a regression to true fails the Host assertion.
    vite = await createViteServer({
      ...viteConfig,
      configFile: false,
      logLevel: 'silent',
      server: {
        ...viteConfig.server,
        host: '127.0.0.1',
        port: frontendPort,
        strictPort: true,
        hmr: false,
        proxy: {
          ...viteConfig.server.proxy,
          '/api': {
            ...realProxy,
            target: `http://127.0.0.1:${upstreamAddr.port}`,
          },
        },
      },
    })
    await vite.listen()
    viteAddr = vite.httpServer.address()
    frontendOrigin = `http://127.0.0.1:${viteAddr.port}`
  }, 20000)

  afterAll(async () => {
    try { await vite?.close() } finally {
      await closeServer(recorder?.server)
    }
  }, 20000)

  it('same-origin POST forwards frontend Host, Origin, Authorization, method/path/body', async () => {
    const before = recorder.observed.length
    const payload = { probe: 'same-origin', n: 1 }
    const res = await postJson(`${frontendOrigin}/api/probe?x=1`, {
      origin: frontendOrigin,
      'sec-fetch-site': 'same-origin',
      authorization: 'Bearer synthetic-test-token',
    }, payload)
    expect(res.status).toBe(200)

    const seen = recorder.observed[before]
    expect(seen).toBeDefined()
    // Core regression: Host must be the frontend host, not the upstream target.
    expect(seen.host).toBe(`127.0.0.1:${viteAddr.port}`)
    expect(seen.origin).toBe(frontendOrigin)
    expect(seen.authorization).toBe('Bearer synthetic-test-token')
    expect(seen.secFetchSite).toBe('same-origin')
    expect(seen.method).toBe('POST')
    expect(seen.url).toBe('/api/probe?x=1')
    expect(JSON.parse(seen.body)).toEqual(payload)
  })

  it('mismatched Origin is forwarded unmodified (proxy does not rewrite it)', async () => {
    const before = recorder.observed.length
    const res = await postJson(`${frontendOrigin}/api/probe`, {
      origin: 'http://evil.example.test',
      'sec-fetch-site': 'same-origin',
      authorization: 'Bearer synthetic-test-token',
    }, { probe: 'mismatched-origin' })
    expect(res.status).toBe(200)

    const seen = recorder.observed[before]
    expect(seen).toBeDefined()
    expect(seen.host).toBe(`127.0.0.1:${viteAddr.port}`)
    expect(seen.origin).toBe('http://evil.example.test')
  })

  it('cross-site Sec-Fetch-Site is forwarded unmodified', async () => {
    const before = recorder.observed.length
    const res = await postJson(`${frontendOrigin}/api/probe`, {
      origin: frontendOrigin,
      'sec-fetch-site': 'cross-site',
      authorization: 'Bearer synthetic-test-token',
    }, { probe: 'cross-site' })
    expect(res.status).toBe(200)

    const seen = recorder.observed[before]
    expect(seen).toBeDefined()
    expect(seen.secFetchSite).toBe('cross-site')
    expect(seen.origin).toBe(frontendOrigin)
  })

  it('missing Authorization is forwarded as absent', async () => {
    const before = recorder.observed.length
    const res = await postJson(`${frontendOrigin}/api/probe`, {
      origin: frontendOrigin,
      'sec-fetch-site': 'same-origin',
    }, { probe: 'no-auth' })
    expect(res.status).toBe(200)

    const seen = recorder.observed[before]
    expect(seen).toBeDefined()
    expect(seen.authorization).toBeNull()
  })
})
