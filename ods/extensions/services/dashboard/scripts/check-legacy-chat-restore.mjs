// Optional browser regression: install Playwright and Chromium, then run this file with Node.
/* global localStorage, sessionStorage */
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom'; import {ThemeProvider} from '/src/contexts/ThemeContext.jsx';
import Pixel from '/src/pages/Pixel.jsx'; import '/src/index.css';
createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter, null,
  React.createElement(ThemeProvider, null, React.createElement(Pixel))));`
const server = await createServer({root, appType:'custom', server:{host:'127.0.0.1',port:0}, plugins:[{
  name:'legacy-chat-regression', resolveId:id => id === 'legacy-chat-regression' ? id : null,
  load:id => id === 'legacy-chat-regression' ? entry : null,
}]})
server.middlewares.use(async (request,response,next) => {
  if (request.url !== '/__legacy_chat') return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/__legacy_chat', '<!doctype html><html><body><div id="root"></div><script type="module" src="/@id/legacy-chat-regression"></script></body></html>'))
})
let browser
const receipts = [], errors = []
try {
  await server.listen()
  browser = await chromium.launch({headless:true})
  for (const legacy of [true,false]) {
    const page = await browser.newPage()
    page.on('pageerror',error => errors.push(error.message))
    await page.route('**/api/**',route => route.fulfill({json:{available:true}}))
    await page.addInitScript(legacy => {
      if (sessionStorage.getItem('fixture-seeded')) return
      const record = (text,draft) => ({schema:1,chatId:'retained-chat',messages:[{role:'user',content:text}],draft,preview:null})
      const latest = record('Latest retained message','Latest retained draft')
      const old = record('Old pointer message','Old pointer draft')
      localStorage.setItem('ods.pixel.chat.v1',JSON.stringify(legacy ? old : {...latest,persistenceVersion:2}))
      localStorage.setItem('ods.pixel.conversations.v1',JSON.stringify([legacy ? latest : old]))
      sessionStorage.setItem('fixture-seeded','yes')
    },legacy)
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__legacy_chat`)
    for (const phase of ['mount','reload']) {
      if (phase === 'reload') await page.reload()
      await page.getByText('Available',{exact:true}).waitFor()
      const draft = await page.getByPlaceholder(/^Message .+\.\.\.$/).inputValue()
      const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('ods.pixel.chat.v1')))
      receipts.push({legacy,phase,draft,savedDraft:stored.draft,savedText:stored.messages[0].content})
    }
    await page.close()
  }
  console.log(JSON.stringify({browser:browser.version(),receipts,errors},null,2))
  assert.deepEqual(errors,[])
  for (const receipt of receipts) {
    assert.equal(receipt.draft,'Latest retained draft')
    assert.equal(receipt.savedDraft,'Latest retained draft')
    assert.equal(receipt.savedText,'Latest retained message')
  }
} finally {await browser?.close(); await server.close()}
