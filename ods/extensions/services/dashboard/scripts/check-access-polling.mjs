// Optional real-HTTP Chromium regression. Run from the dashboard directory
// with Playwright and its Chromium browser installed. Uses a temporary profile.
/* global document */
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {setTimeout, clearTimeout} from 'node:timers'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const verified = {available:true,surface:'linux-systemd',configured_mode:'sandboxed',
  effective_mode:'sandboxed',runtime_verified:true,revision:'a'.repeat(64),busy:false,pending:false}
const entry = `import React from 'react';import {createRoot} from 'react-dom/client';
import Card from '/src/components/settings/PixelAccessCard.jsx';
createRoot(document.getElementById('root')).render(React.createElement(Card));`
const requests = [], timers = new Set(), errors = []
const server = await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:0},plugins:[{
  name:'access-polling-check',resolveId:id=>id==='access-polling-check'?id:null,
  load:id=>id==='access-polling-check'?entry:null,
  configureServer(server) {
    server.middlewares.use((request,response,next)=>{
      if(request.url!=='/api/pixel/access-mode')return next()
      requests.push(request.method)
      response.writeHead(200,{'Content-Type':'application/json'})
      if(requests.length===1) {response.end(JSON.stringify({...verified,pending:true}));return}
      // Real response headers arrive immediately; JSON parsing takes six seconds.
      response.flushHeaders()
      const timer=setTimeout(()=>{timers.delete(timer);response.end(JSON.stringify(verified))},6000)
      timers.add(timer)
    })
  },
}]})
server.middlewares.use(async(request,response,next)=>{
  if(request.url!=='/')return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/','<!doctype html><html><head><title>Access polling check</title></head><body><div id="root"></div><script type="module" src="/@id/access-polling-check"></script></body></html>'))
})
let browser
try {
  await server.listen()
  browser=await chromium.launch({headless:true})
  const page=await browser.newPage()
  page.on('pageerror',error=>errors.push(error.message))
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/`)
  await page.getByText(/transition is unfinished/).waitFor()
  let timedOut=false
  try {
    await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(button=>button.textContent==='Enable Full Access')?.disabled,{}, {timeout:14000})
  } catch(error) {if(error.name!=='TimeoutError')throw error;timedOut=true}
  const receipt={browser:browser.version(),timedOut,requests,
    effective:await page.getByText('Effective',{exact:true}).evaluate(element=>element.nextElementSibling.textContent),errors}
  console.log(JSON.stringify(receipt,null,2))
  assert.deepEqual(errors,[])
  assert.equal(timedOut,false)
  assert.equal(receipt.effective,'Safer mode')
  assert.deepEqual(requests,['GET','GET'])
} finally {
  for(const timer of timers)clearTimeout(timer)
  await browser?.close()
  await server.close()
}
