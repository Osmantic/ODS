// Optional: install Playwright + Chromium, then run with Node from dashboard.
/* global localStorage */
import assert from 'node:assert/strict'
import console from 'node:console'
import {readFile} from 'node:fs/promises'
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
const server = await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:0},plugins:[{
  name:'stale-chat-regression',resolveId:id=>id==='stale-chat-regression'?id:null,
  load:id=>id==='stale-chat-regression'?entry:null,
}]})
server.middlewares.use(async (request,response,next)=>{
  if (request.url !== '/__stale_chat') return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/__stale_chat','<!doctype html><html><body><div id="root"></div><script type="module" src="/@id/stale-chat-regression"></script></body></html>'))
})
let browser
const receipts = [], errors = []
try {
  await server.listen()
  browser = await chromium.launch({headless:true})
  const context = await browser.newContext({acceptDownloads:true})
  let posts = 0
  await context.route('**/api/**',route=>{
    if (route.request().url().endsWith('/api/pixel/chat/stream')) {
      posts++
      return route.fulfill({contentType:'text/event-stream',body:'data: {"choices":[{"delta":{"content":"Unexpected response"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'})
    }
    return route.fulfill({json:{available:true}})
  })
  const first = await context.newPage(), second = await context.newPage()
  for (const page of [first,second]) page.on('pageerror',error=>errors.push(error.message))
  await first.goto(`http://127.0.0.1:${server.httpServer.address().port}/__stale_chat`)
  await first.getByText('Available',{exact:true}).waitFor()
  await first.getByPlaceholder(/^Message .+\.\.\.$/).fill('Original saved draft')
  await first.waitForFunction(()=>JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))?.draft==='Original saved draft')
  await second.goto(first.url())
  await second.getByText('Available',{exact:true}).waitFor()
  await second.getByPlaceholder(/^Message .+\.\.\.$/).fill('Newer draft from second tab')
  await second.waitForFunction(()=>JSON.parse(localStorage.getItem('ods.pixel.chat.v1'))?.draft==='Newer draft from second tab')
  await first.getByPlaceholder(/^Message .+\.\.\.$/).fill('Unsaved draft from first tab')
  // A rendering checkpoint after the input event lets the persistence effect run.
  await first.getByText('28 / 16,384 chars',{exact:true}).waitFor()
  const saved = await first.evaluate(()=>JSON.parse(localStorage.getItem('ods.pixel.chat.v1')).draft)
  const recovery = first.getByRole('button',{name:'Download recovery copy'})
  let exportedDraft = null
  if (await recovery.count()) {
    const [download] = await Promise.all([first.waitForEvent('download'),recovery.click()])
    exportedDraft = JSON.parse(await readFile(await download.path(),'utf8')).conversation.draft
  }
  await first.getByTitle('Send',{exact:true}).click()
  await first.getByTitle('Send',{exact:true}).waitFor()
  receipts.push({saved,exportedDraft,posts})
  await first.reload()
  await first.getByText('Available',{exact:true}).waitFor()
  receipts.push({reloadedDraft:await first.getByPlaceholder(/^Message .+\.\.\.$/).inputValue()})
  console.log(JSON.stringify({browser:browser.version(),receipts,errors},null,2))
  assert.deepEqual(errors,[])
  assert.equal(saved,'Newer draft from second tab')
  assert.equal(exportedDraft,'Unsaved draft from first tab')
  assert.equal(posts,0)
  assert.equal(receipts[1].reloadedDraft,'Newer draft from second tab')
} finally {await browser?.close(); await server.close()}
