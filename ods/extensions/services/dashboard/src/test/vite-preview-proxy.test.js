// @vitest-environment node
//
// Regression for the real ODS Vite dev preview proxy. Loads the real
// vite.config.js factory, starts a real Vite dev server on a reserved
// loopback port, and points /pixel-preview at a disposable recorder that
// stands in for the installed nginx on 127.0.0.1:3001. Verifies the preview
// gate: canonical path validation, GET/HEAD only, opaque Origin:null snapshot
// reads, same-origin source reads, no credential injection, and that
// malformed/traversal paths never reach any upstream.

import { describe, it, expect, beforeAll, afterAll } from 'vitest'
import http from 'node:http'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve, dirname, basename } from 'node:path'
import { Buffer } from 'node:buffer'
import { createServer as createViteServer, preview as createVitePreview } from 'vite'
import { createDashboardConfig } from '../../vite.config.js'

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
const SITE_ID = 'site-0123456789abcdef01234567'
const SOURCE_ID = 'source-0123456789abcdef01234567'

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
      const status = /^\/pixel-preview\/site-[a-f0-9]{24}\/status\/(401|404)\.json$/.exec(req.url)?.[1]
      if (status) {
        res.writeHead(Number(status), { 'content-type': 'application/json' })
        res.end(JSON.stringify({ ok: false, status: Number(status) }))
        return
      }
      if (req.url?.endsWith('.csv')) {
        res.writeHead(200, {
          'content-type': 'text/csv',
          'content-length': '5',
          'content-security-policy': "sandbox allow-scripts; default-src 'self'",
          'cross-origin-resource-policy': 'cross-origin',
          'access-control-allow-origin': '*',
          'x-preview-sha256': 'a'.repeat(64),
        })
        res.end('a,b\n1')
        return
      }
      if (req.url?.includes('/__ods_source__/')) {
        res.writeHead(200, {
          'content-type': 'application/json',
          'content-security-policy': "sandbox; default-src 'none'; frame-ancestors 'none'",
          'cross-origin-resource-policy': 'same-origin',
        })
        res.end(JSON.stringify({ source: true }))
        return
      }
      if (req.url?.endsWith('.html') || req.url?.endsWith('/')) {
        res.writeHead(200, {
          'content-type': 'text/html',
          'content-security-policy': "sandbox allow-scripts; default-src 'self'",
          'cross-origin-resource-policy': 'cross-origin',
          'access-control-allow-origin': '*',
          'x-preview-sha256': 'b'.repeat(64),
        })
        res.end('<!doctype html><html><body>snapshot</body></html>')
        return
      }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ ok: true }))
    })
  })
  return { server, observed }
}

function request(url, { method = 'GET', headers = {}, body, rawPath, rawHeaders } = {}) {
  return new Promise((resolve, reject) => {
    const data = body === undefined ? undefined : JSON.stringify(body)
    const parsed = new URL(url)
    const options = {
      method,
      host: parsed.hostname,
      port: parsed.port,
      path: rawPath !== undefined ? rawPath : parsed.pathname + parsed.search,
      headers: rawHeaders ?? { ...(data === undefined ? {} : { 'content-type': 'application/json' }), ...headers },
    }
    const req = http.request(options, res => {
      const chunks = []
      res.on('data', chunk => chunks.push(chunk))
      res.on('error', reject)
      res.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        let parsedBody = text
        try { parsedBody = JSON.parse(text) } catch { /* ordinary source/HTML */ }
        resolve({ status: res.statusCode, body: parsedBody, headers: res.headers })
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

async function startVite(apiPort, previewPort, { withKey = true, mode = 'dev' } = {}) {
  const cacheDir = await mkdtemp(join(tmpdir(), 'ods-vite-proxy-'))
  temporaryCaches.push(cacheDir)
  const frontendPort = await reservePort()
  const base = withKey
    ? createDashboardConfig(SYNTHETIC_KEY)
    : createDashboardConfig(undefined)
  const realPreview = base.server?.proxy?.['/pixel-preview']
  if (!realPreview) throw new Error('config missing server.proxy[/pixel-preview]')
  const realApi = base.server?.proxy?.['/api']
  if (!realApi) throw new Error('config missing server.proxy[/api]')

  const options = {
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
        '/api': { ...realApi, target: `http://127.0.0.1:${apiPort}` },
        '/pixel-preview': { ...realPreview, target: `http://127.0.0.1:${previewPort}` },
      },
    },
  }
  const vite = mode === 'preview'
    ? await createVitePreview({...options, preview: options.server})
    : await createViteServer(options)
  if (mode === 'dev') await vite.listen()
  const addr = vite.httpServer.address()
  return { vite, origin: `http://127.0.0.1:${addr.port}`, port: addr.port }
}


for (const [withKey, mode] of [[true, 'dev'], [false, 'dev'], [true, 'preview'], [false, 'preview']]) {
  describe(`real Vite ${mode} proxy (key configured: ${withKey})`, () => {
    let preview, api, vite, origin, port
    beforeAll(async () => {
      preview = createRecorder(); api = createRecorder()
      const previewAddress = await listen(preview.server)
      const apiAddress = await listen(api.server)
      ;({vite, origin, port} = await startVite(apiAddress.port, previewAddress.port, {withKey, mode}))
    }, 20000)
    afterAll(async () => {
      try {
        if (vite?.close) await vite.close()
        else await closeServer(vite?.httpServer)
      } finally {
        await closeServer(preview?.server)
        await closeServer(api?.server)
      }
    }, 20000)
    const basePath = `/pixel-preview/${SITE_ID}/`

    it.each(['', 'folder/', '__ods_view__.html', '__ods_manifest__.json',
      '__ods_changes__/initial.json', `__ods_changes__/${SITE_ID}.json`,
      '_next/static/app.js', 'img%5B1%5D.png?v=42'])('relays allowed snapshot path %s', async tail => {
      const before = preview.observed.length
      const result = await request(origin, {rawPath: basePath + tail})
      expect(result.status).toBe(200)
      expect(preview.observed).toHaveLength(before + 1)
      expect(preview.observed[before]).toMatchObject({url:basePath + tail, authorization:null,
        forwarded:null, via:null, xForwardedFor:null, xForwardedHost:null, xForwardedProto:null})
      if (!tail || tail.endsWith('.html') || tail.endsWith('/')) {
        expect(result.body).toContain('<body>snapshot</body>')
        expect(result.body).not.toContain('<div id="root">')
      }
    })

    it.each(['GET', 'HEAD'])('preserves immutable bytes and sandbox headers for %s', async method => {
      const result = await request(origin, {method, rawPath:basePath+'data.csv',
        headers:{origin:'null', 'sec-fetch-site':'cross-site'}})
      expect(result.status).toBe(200)
      expect(result.body).toBe(method === 'HEAD' ? '' : 'a,b\n1')
      expect(result.headers).toMatchObject({'content-type':'text/csv', 'content-length':'5',
        'content-security-policy':"sandbox allow-scripts; default-src 'self'",
        'cross-origin-resource-policy':'cross-origin', 'access-control-allow-origin':'*',
        'x-preview-sha256':'a'.repeat(64)})
    })

    it.each(['__ods_source__', '%5f%5fods_source__'])('keeps source review same-origin for %s', async source => {
      const path = basePath + source + '/' + SOURCE_ID + '.json'
      // Encoded aliases are decoded by nginx; source classification must happen before forwarding.
      const before = preview.observed.length
      for (const headers of [{origin:'null','sec-fetch-site':'cross-site'},
        {origin:'https://example.org'}, {'sec-fetch-site':'cross-site'}]) {
        expect((await request(origin,{rawPath:path,headers})).status).toBe(403)
      }
      expect(preview.observed).toHaveLength(before)
      if (source === '__ods_source__') {
        const result = await request(origin,{rawPath:path,headers:{origin,'sec-fetch-site':'same-origin'}})
        expect(result.status).toBe(200)
        expect(result.body).toEqual({source:true})
        expect(result.headers['cross-origin-resource-policy']).toBe('same-origin')
        expect(result.headers['access-control-allow-origin']).toBeUndefined()
        expect(result.headers['content-security-policy']).toBe("sandbox; default-src 'none'; frame-ancestors 'none'")
      }
    })

    it.each([401,404])('preserves upstream %s without SPA fallback',async status => {
      const before=preview.observed.length
      const result=await request(origin,{rawPath:basePath+`status/${status}.json`})
      expect(result.status).toBe(status)
      expect(result.body).toEqual({ok:false,status})
      expect(preview.observed).toHaveLength(before+1)
    })

    it.each(['POST','PUT','PATCH','DELETE','OPTIONS'])('rejects %s before upstream',async method=>{
      const before=preview.observed.length
      expect((await request(origin,{rawPath:basePath,method})).status).toBe(405)
      expect(preview.observed).toHaveLength(before)
    })

    it.each(['forwarded','via','x-real-ip','x-forwarded-for','x-forwarded-host','x-forwarded-proto'])(
      'rejects %s even for opaque assets',async header=>{
        const before=preview.observed.length
        expect((await request(origin,{rawPath:basePath,headers:{[header]:'untrusted',origin:'null'}})).status).toBe(403)
        expect(preview.observed).toHaveLength(before)
      })
    it('rejects wrong Host and duplicate security headers',async()=>{
      const before=preview.observed.length
      expect((await request(origin,{rawPath:basePath,headers:{host:'127.0.0.1:1'}})).status).toBe(403)
      for(const [name,value] of [['Host',`127.0.0.1:${port}`],['Origin',origin],['Sec-Fetch-Site','same-origin']]) {
        const rawHeaders=['Host',`127.0.0.1:${port}`,'Origin',origin,'Sec-Fetch-Site','same-origin',name,value]
        expect((await request(origin,{rawPath:basePath,rawHeaders})).status).toBe(403)
      }
      expect(preview.observed).toHaveLength(before)
    })

    const invalidPaths = [
      '/pixel-preview','/pixel-preview/','/pixel-preview-extra/'+SITE_ID+'/',
      '/pixel-preview/'+SITE_ID,'/pixel-preview/invalid/',
      ...['../api/probe','../../api/probe','..%2fapi/probe','%2e%2e/api',
        '%2e%2e%2fapi','a%5cb','%252e%252e/api','%252fapi','a//b',
        '__ods_manifest__.json/../api','__ods_source__/'+SOURCE_ID+'.json/../api',
        '__ods_source__/other.json','__ods_other.json','__pycache__/x',
        '.','..','%00','%c3%a9','%01','%7f','%c0%af','%zz','#fragment',
        'x'.repeat(129),Array(14).fill('x'.repeat(128)).join('/')].map(tail=>basePath+tail),
      basePath+'a'+String.fromCharCode(92)+'b',
    ]
    it.each(invalidPaths)('blocks raw malformed path %s from every upstream',async rawPath=>{
      const previewBefore=preview.observed.length, apiBefore=api.observed.length
      // Development's earlier URL middleware rejects a raw fragment with 400;
      // the preview server reaches our 404 gate. Neither may forward it.
      const expected = rawPath.includes('#') && mode === 'dev' ? 400 : 404
      expect((await request(origin,{rawPath})).status).toBe(expected)
      expect(preview.observed).toHaveLength(previewBefore)
      expect(api.observed).toHaveLength(apiBefore)
    })
    it('only the API proxy receives the configured key',async()=>{
      const apiBefore=api.observed.length, previewBefore=preview.observed.length
      expect((await request(origin,{rawPath:'/api/probe'})).status).toBe(200)
      expect(api.observed[apiBefore].authorization).toBe(withKey ? `Bearer ${SYNTHETIC_KEY}` : null)
      if (withKey) {
        expect((await request(origin,{rawPath:'/api/probe',headers:{origin:'https://example.org'}})).status).toBe(403)
        expect(api.observed).toHaveLength(apiBefore+1)
      }
      expect((await request(origin,{rawPath:basePath})).status).toBe(200)
      expect(preview.observed[previewBefore].authorization).toBeNull()
    })
  })
}

describe('preview configuration boundary without an API key',()=>{
  const invalid=[
    ['wide host',{host:'0.0.0.0'}],['middleware mode',{middlewareMode:true}],
    ['HTTPS',{https:{}}],['automatic port fallback',{strictPort:false}],['CORS reflection',{cors:true}],
    ['self proxy',{port:3001}],
    ...[{target:'http://example.org:3001'},{target:'http://user:secret@127.0.0.1:3001'},
      {target:'http://127.0.0.1:3001/path'},{target:'http://127.0.0.1:3001/?q=x'},
      {target:'http://127.0.0.1:3001/#fragment'},{target:'http://127.0.0.1:3002'},
      {changeOrigin:true},{ws:true},{followRedirects:true},{xfwd:true}]
      .map((options,i)=>['unsafe proxy '+i,{previewOptions:options}]),
  ]
  it.each(invalid)('rejects %s before listening',async(_name,override)=>{
    const config=createDashboardConfig(undefined)
    const {previewOptions,...serverOverride}=override
    const server={...config.server,...serverOverride,proxy:{...config.server.proxy,
      '/pixel-preview':{...config.server.proxy['/pixel-preview'],...previewOptions}}}
    await expect(createViteServer({...config,configFile:false,logLevel:'silent',server}))
      .rejects.toThrow(/ODS development preview/)
  })
})
