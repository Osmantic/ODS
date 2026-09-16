// Optional: install Playwright + Chromium, then run this file with Node.
/* global localStorage */
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../',import.meta.url))
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom'; import {ThemeProvider} from '/src/contexts/ThemeContext.jsx';
import Pixel from '/src/pages/Pixel.jsx'; import '/src/index.css';
createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,
  React.createElement(ThemeProvider,null,React.createElement(Pixel))));`
const server = await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:0},plugins:[{
  name:'unavailable-draft-regression',resolveId:id=>id==='unavailable-draft-regression'?id:null,
  load:id=>id==='unavailable-draft-regression'?entry:null,
}]})
server.middlewares.use(async(request,response,next)=>{
  if (request.url !== '/__draft') return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/__draft','<!doctype html><html><body><div id="root"></div><script type="module" src="/@id/unavailable-draft-regression"></script></body></html>'))
})
let browser
const receipts = [], errors = []
try {
  await server.listen()
  browser = await chromium.launch({headless:true})
  for (const mode of ['unreachable','switching']) {
    const page = await browser.newPage()
    let ready = false
    const requests = []
    page.on('pageerror',error=>errors.push(error.message))
    await page.route('**/api/**',route=>{
      const url = route.request().url()
      if (url.endsWith('/api/pixel/chat/stream')) {
        requests.push(route.request().postDataJSON())
        return route.fulfill({contentType:'text/event-stream',body:'data: {"choices":[{"delta":{"content":"Explicit send received"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'})
      }
      if (url.endsWith('/api/pixel/status') && !ready && mode==='unreachable') return route.abort('connectionfailed')
      return route.fulfill({json:{available:ready,...(!ready?{state:'model_switching'}:{})}})
    })
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__draft`)
    await page.getByText(mode==='switching'?'Switching model...':'Degraded',{exact:true}).waitFor()
    const input = page.locator('textarea.pixel-composer-input')
    const disabled = await input.isDisabled()
    if (disabled) {receipts.push({mode,disabled}); await page.close(); continue}
    const draft = 'Prepare a local note – tiếng Việt'
    await input.fill(draft)
    await input.press('Enter')
    await page.waitForFunction(text=>JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))?.draft===text,draft)
    assert.equal(await page.getByTitle('Send',{exact:true}).isDisabled(),true)
    assert.equal(requests.length,0)
    await page.reload()
    await page.getByText(mode==='switching'?'Switching model...':'Degraded',{exact:true}).waitFor()
    const reloadedDraft = await input.inputValue()
    ready = true
    await page.getByText('Available',{exact:true}).waitFor()
    assert.equal(await page.getByTitle('Send',{exact:true}).isEnabled(),true)
    const beforeExplicitSend = requests.length
    await page.getByTitle('Send',{exact:true}).click()
    await page.getByText('Explicit send received',{exact:true}).waitFor()
    receipts.push({mode,disabled,reloadedDraft,beforeExplicitSend,posts:requests.length,sent:requests[0]?.messages.at(-1).content})
    await page.close()
  }
  console.log(JSON.stringify({browser:browser.version(),receipts,errors},null,2))
  assert.deepEqual(errors,[])
  for (const receipt of receipts) {
    assert.equal(receipt.disabled,false)
    assert.equal(receipt.reloadedDraft,'Prepare a local note – tiếng Việt')
    assert.equal(receipt.beforeExplicitSend,0)
    assert.equal(receipt.posts,1)
    assert.equal(receipt.sent,receipt.reloadedDraft)
  }
} finally {await browser?.close();await server.close()}
