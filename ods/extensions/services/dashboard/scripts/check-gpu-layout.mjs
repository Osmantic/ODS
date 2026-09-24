// From the dashboard directory: npm install --no-save --package-lock=false playwright@1.63.0
// Then: npx playwright install chromium && node scripts/check-gpu-layout.mjs
/* global document */
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const gpus = Array.from({length: 8}, (_, index) => ({
  index, uuid: `GPU-11111111-2222-3333-4444-00000000000${index}`,
  name: 'NVIDIA RTX 4090', memory_total_mb: 24576, memory_used_mb: 12288,
  utilization_percent: 50, temperature_c: 65, power_w: 210,
  assigned_services: ['llama-server'],
}))
const topology = {
  gpu_count: gpus.length, vendor: 'nvidia', driver_version: '580.126.09',
  gpus: gpus.map(gpu => ({...gpu, memory_gb: 24})),
  links: gpus.flatMap(a => gpus.filter(b => b.index > a.index).map(b => ({
    gpu_a: a.index, gpu_b: b.index, link_type: 'PHB', rank: 20,
  }))),
}
const detailed = {
  gpus, gpu_count: gpus.length, backend: 'nvidia',
  aggregate: {name: '8× NVIDIA RTX 4090', utilization_percent: 50,
    memory_used_mb: 98304, memory_total_mb: 196608, memory_percent: 50,
    temperature_c: 65, power_w: 1680},
  split_mode: 'layer', tensor_split: '1,1,1,1,1,1,1,1',
  assignment: {version: '1.0', strategy: 'dedicated', services: {
    'llama-server': {gpus: gpus.map(gpu => gpu.uuid), parallelism: {
      mode: 'tensor', tensor_parallel_size: 8, gpu_memory_utilization: 0.9,
    }},
  }},
}
const history = {timestamps: ['2026-09-14T00:00:00Z', '2026-09-14T00:00:05Z'],
  gpus: Object.fromEntries(gpus.map(gpu => [String(gpu.index), {
    utilization: [40, 50], memory_percent: [45, 50], temperature: [60, 65], power_w: [200, 210],
  }]))}
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import GPUMonitor from '/src/pages/GPUMonitor.jsx'; import '/src/index.css';
createRoot(document.getElementById('root')).render(React.createElement(GPUMonitor));`
const server = await createServer({root, appType: 'custom', server: {host: '127.0.0.1', port: 0}, plugins: [{
  name: 'gpu-layout-regression',
  resolveId: id => id === 'gpu-layout-regression' ? id : null,
  load: id => id === 'gpu-layout-regression' ? entry : null,
}]})
server.middlewares.use(async (request, response, next) => {
  if (request.url !== '/__gpu_layout') return next()
  response.setHeader('Content-Type', 'text/html')
  response.end(await server.transformIndexHtml('/__gpu_layout', '<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><div id="root"></div><script type="module" src="/@id/gpu-layout-regression"></script></body></html>'))
})
let browser
const errors = []
const receipts = []
try {
  await server.listen()
  browser = await chromium.launch({headless: true})
  for (const width of [320, 390, 768, 1280]) {
    const page = await browser.newPage({viewport: {width, height: 900}})
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/gpu/*', route => route.fulfill({json: {
      '/api/gpu/detailed': detailed, '/api/gpu/topology': topology, '/api/gpu/history': history,
    }[new URL(route.request().url()).pathname]}))
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__gpu_layout`)
    await page.getByRole('heading', {name: 'GPU Assignment', exact: true}).waitFor()
    for (const tab of ['Per-GPU', 'History']) {
      await page.getByRole('button', {name: tab, exact: true}).click()
      const layout = await page.evaluate(() => {
        const viewport = document.documentElement.clientWidth
        const elements = [...document.querySelectorAll('p, h1, h3, button, span[title^="GPU-"]')]
        const outside = elements.filter(element => {
          const box = element.getBoundingClientRect()
          return box.width > 0 && (box.left < -1 || box.right > viewport + 1)
        }).map(element => element.textContent)
        return {viewport, scrollWidth: document.documentElement.scrollWidth, outside}
      })
      receipts.push({width, tab, ...layout})
    }
    await page.getByRole('button', {name: 'Per-GPU', exact: true}).click()
    const matrix = page.getByRole('region', {name: 'GPU interconnect matrix'})
    assert.equal(await matrix.count(), 1, 'The matrix must expose a keyboard-accessible scroll region')
    if (await matrix.evaluate(element => element.scrollWidth > element.clientWidth)) {
      await matrix.focus()
      await page.keyboard.press('ArrowRight')
      await page.waitForFunction(() => document.querySelector('[aria-label="GPU interconnect matrix"]').scrollLeft > 0)
      receipts.push({width, keyboardScroll: await matrix.evaluate(element => element.scrollLeft)})
    }
    await page.close()
  }
  console.log(JSON.stringify({browser: browser.version(), receipts, errors}, null, 2))
  assert.deepEqual(errors, [], 'The GPU page must render without errors')
  for (const receipt of receipts.filter(item => item.tab)) {
    assert.ok(receipt.scrollWidth <= receipt.viewport + 1, JSON.stringify(receipt))
    assert.deepEqual(receipt.outside, [], JSON.stringify(receipt))
  }
} finally {
  await browser?.close()
  await server.close()
}
