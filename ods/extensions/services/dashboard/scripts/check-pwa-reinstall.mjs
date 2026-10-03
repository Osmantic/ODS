// Optional Chromium check: npm install --no-save playwright; npx playwright install chromium.
// Run from the dashboard directory. No app is installed and no existing profile is used.
/* global localStorage, window, requestAnimationFrame */
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const entry = `import React from 'react';import {createRoot} from 'react-dom/client';
import Banner from '/src/components/InstallPromptBanner.jsx';import '/src/index.css';
createRoot(document.getElementById('root')).render(React.createElement(Banner));
navigator.serviceWorker.register('/__pwa_regression_sw.js');`
const server = await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:0},plugins:[{
  name:'pwa-regression',resolveId:id=>id==='pwa-regression'?id:null,load:id=>id==='pwa-regression'?entry:null,
}]})
server.middlewares.use(async(request,response,next)=>{
  if(request.url==='/__pwa_regression_sw.js') {
    response.setHeader('Content-Type','text/javascript')
    response.end("self.addEventListener('fetch',()=>{});self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));")
    return
  }
  if(request.url!=='/')return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/','<!doctype html><html><head><link rel="manifest" href="/manifest.webmanifest"><title>Private ODS PWA test</title></head><body><h1>Private PWA test</h1><div id="root"></div><script type="module" src="/@id/pwa-regression"></script></body></html>'))
})
const receipts=[], errors=[]
let context
try {
  await server.listen()
  for(const dismissed of [false,true]) {
    // Incognito contexts cannot receive a real install offer. Playwright owns
    // and removes this temporary persistent profile when the context closes.
    context=await chromium.launchPersistentContext('',{channel:'chromium',headless:true,args:['--bypass-app-banner-engagement-checks']})
    const page=context.pages()[0]
    page.on('pageerror',error=>errors.push(error.message))
    await page.addInitScript(dismissed=>{
      localStorage.setItem('ods-pwa-installed','1')
      localStorage.setItem('ods-pwa-visit-count','5')
      if(dismissed)localStorage.setItem('ods-pwa-prompt-dismissed','1')
      window.promptEvents=[]
      window.addEventListener('beforeinstallprompt',event=>window.promptEvents.push(event))
    },dismissed)
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/`)
    await page.getByRole('heading',{name:'Private PWA test',exact:true}).click()
    await page.waitForFunction(()=>window.promptEvents.length>0,{},{timeout:15000})
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
    const session=await context.newCDPSession(page)
    receipts.push({dismissed,browser:context.browser().version(),
      events:await page.evaluate(()=>window.promptEvents.map(event=>({trusted:event.isTrusted,prevented:event.defaultPrevented}))),
      visible:await page.getByRole('dialog',{name:'Add ODS to your home screen'}).isVisible(),
      marker:await page.evaluate(()=>localStorage.getItem('ods-pwa-installed')),
      installability:await session.send('Page.getInstallabilityErrors'),
    })
    await context.close();context=null
  }
  console.log(JSON.stringify({receipts,errors},null,2))
  assert.deepEqual(errors,[])
  for(const receipt of receipts) {
    assert.deepEqual(receipt.installability.installabilityErrors,[])
    assert.ok(receipt.events.some(event=>event.trusted && event.prevented))
    assert.equal(receipt.marker,'0')
    assert.equal(receipt.visible,!receipt.dismissed)
  }
} finally {await context?.close();await server.close()}
