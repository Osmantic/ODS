// Optional native-dialog regression: install Playwright/Chromium, then run with Node.
/* global localStorage, document */
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom'; import {ThemeProvider} from '/src/contexts/ThemeContext.jsx';
import Pixel from '/src/pages/Pixel.jsx'; import Navigation from '/src/components/PixelConversationNavigation.jsx';
import '/src/index.css';
createRoot(document.getElementById('root')).render(React.createElement(MemoryRouter,null,
  React.createElement(ThemeProvider,null,React.createElement(Navigation,{collapsed:false}),React.createElement(Pixel))));`
const server = await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:0},plugins:[{
  name:'deletion-focus-regression',resolveId:id => id === 'deletion-focus-regression' ? id : null,
  load:id => id === 'deletion-focus-regression' ? entry : null,
}]})
server.middlewares.use(async (request,response,next) => {
  if (request.url !== '/__deletion_focus') return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/__deletion_focus','<!doctype html><html><body><div id="root"></div><script type="module" src="/@id/deletion-focus-regression"></script></body></html>'))
})
let browser
const receipts = [], errors = []
try {
  await server.listen()
  browser = await chromium.launch({headless:true})
  for (const selection of ['last','current','inactive']) {
    const page = await browser.newPage()
    page.on('pageerror',error => errors.push(error.message))
    await page.route('**/api/**',route => route.fulfill({json:{available:true}}))
    await page.addInitScript(selection => {
      const chat = (chatId,text) => ({schema:1,chatId,messages:[{role:'user',content:text}],persistenceVersion:2,draft:''})
      const remove = chat('remove','Remove this chat'), keep = chat('keep','Keep this chat')
      localStorage.setItem('ods.pixel.chat.v1',JSON.stringify(selection === 'inactive' ? keep : remove))
      localStorage.setItem('ods.pixel.conversations.v1',JSON.stringify(selection === 'last' ? [remove] : [remove,keep]))
    },selection)
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__deletion_focus`)
    await page.getByText('Available',{exact:true}).waitFor()
    const opener = page.getByRole('button',{name:'Delete chat: Remove this chat',exact:true})
    await opener.focus(); await page.keyboard.press('Enter')
    await page.getByRole('dialog',{name:'Delete this chat?'}).waitFor()
    await page.keyboard.press('Escape')
    assert.ok(await opener.evaluate(element => element === document.activeElement))
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog',{name:'Delete this chat?'})
    await dialog.waitFor()
    await dialog.getByRole('button',{name:'Cancel',exact:true}).focus()
    await page.keyboard.press('Tab'); await page.keyboard.press('Enter')
    await opener.waitFor({state:'detached'})
    const focus = await page.evaluate(() => ({tag:document.activeElement.tagName,text:document.activeElement.textContent.trim()}))
    const remaining = await page.evaluate(() => JSON.parse(localStorage.getItem('ods.pixel.conversations.v1')).map(item => item.chatId))
    receipts.push({selection,focus,remaining})
    await page.close()
  }
  console.log(JSON.stringify({browser:browser.version(),receipts,errors},null,2))
  assert.deepEqual(errors,[])
  for (const receipt of receipts) {
    assert.deepEqual(receipt.remaining,receipt.selection === 'last' ? [] : ['keep'])
    assert.deepEqual(receipt.focus,{tag:'BUTTON',text:'New task'})
  }
} finally {await browser?.close();await server.close()}
