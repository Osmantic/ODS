// Optional native-dialog check: install Playwright/Chromium, then run with Node.
/* global localStorage, document */
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const entry = `import React from 'react'; import {createRoot} from 'react-dom/client';
import Library from '/src/components/PixelPromptLibrary.jsx'; import '/src/index.css';
createRoot(document.getElementById('root')).render(React.createElement(Library,{input:'Draft',disabled:false,onInsert:()=>{}}));`
const server = await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:0},plugins:[{
  name:'prompt-focus-check',resolveId:id => id === 'prompt-focus-check' ? id : null,
  load:id => id === 'prompt-focus-check' ? entry : null,
}]})
server.middlewares.use(async (request,response,next) => {
  if (request.url !== '/__prompt_focus') return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/__prompt_focus','<!doctype html><html><body><div id="root"></div><script type="module" src="/@id/prompt-focus-check"></script></body></html>'))
})
let browser
try {
  await server.listen()
  browser = await chromium.launch({headless:true})
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror',error => errors.push(error.message))
  await page.addInitScript(() => localStorage.setItem('ods.pixel.saved-prompts.v1',JSON.stringify([{id:'review',title:'Review',text:'Evidence'}])))
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/__prompt_focus`)
  async function press(name) {
    await page.getByRole('button',{name,exact:true}).focus()
    await page.keyboard.press('Enter')
  }
  async function focused(name) {
    await page.waitForFunction(name => document.activeElement?.getAttribute('aria-label') === name || document.activeElement?.textContent === name, name)
  }
  await press('Saved prompts')
  await press('Edit prompt: Review')
  await press('Cancel edit')
  await focused('Edit prompt: Review')
  await press('Edit prompt: Review')
  await press('Save prompt')
  await focused('Edit prompt: Review')
  await press('Delete prompt: Review')
  await press('Keep prompt')
  await focused('Delete prompt: Review')
  await press('Delete prompt: Review')
  await press('Delete prompt')
  await focused('Save a new prompt')
  await page.keyboard.press('Escape')
  await focused('Saved prompts')
  assert.deepEqual(errors,[])
  console.log(JSON.stringify({browser:browser.version(),checks:5,errors},null,2))
} finally {await browser?.close();await server.close()}
