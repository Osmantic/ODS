// Optional browser regression: run with Playwright and Chromium installed.
// Exercises real HTTP bodies; the fixture never contacts a GPU or changes a route.
import assert from 'node:assert/strict'
import console from 'node:console'
import {createRequire} from 'node:module'
import {fileURLToPath, URL} from 'node:url'
import {setTimeout} from 'node:timers'
import {createServer} from 'vite'

const {chromium} = createRequire(import.meta.url)('playwright')
const root = fileURLToPath(new URL('../', import.meta.url))
const entry = "import React from 'react';import {createRoot} from 'react-dom/client';"
  + "import RemoteProvider from '/src/pages/RemoteProvider.jsx';"
  + "createRoot(document.getElementById('root')).render(React.createElement(RemoteProvider));"
const status = {status:'ready',routeState:{enabled:true,provider:{
  baseUrl:'https://saved.example/v1',model:'saved-model',contextLength:32768,maxTokens:4096,
}},capabilities:{odsPeerLifecycle:false},availableActions:{configure:true}}
let mode = 'truncated', posts = 0
const errors = [], receipts = []
const server = await createServer({root,appType:'custom',server:{host:'127.0.0.1',port:0},plugins:[{
  name:'remote-receipt-check',resolveId:id=>id==='remote-receipt-check'?id:null,
  load:id=>id==='remote-receipt-check'?entry:null,
  configureServer(server) {
    server.middlewares.use((request,response,next)=>{
      if(request.url==='/api/remote-provider/status'){
        response.setHeader('Content-Type','application/json')
        response.end(JSON.stringify(status));return
      }
      if(request.url!=='/api/remote-provider/apply')return next()
      posts++
      if(mode==='interrupted'){
        response.writeHead(200,{'Content-Type':'application/json','Content-Length':'9999'})
        response.flushHeaders();response.write('{"ok":')
        setTimeout(()=>response.destroy(),50)
      } else {
        response.writeHead(200,{'Content-Type':'application/json'})
        response.end('{"ok":')
      }
    })
  },
}]})
server.middlewares.use(async(request,response,next)=>{
  if(request.url!=='/')return next()
  response.setHeader('Content-Type','text/html')
  response.end(await server.transformIndexHtml('/','<!doctype html><html><head><title>Remote receipt check</title></head><body><div id="root"></div><script type="module" src="/@id/remote-receipt-check"></script></body></html>'))
})
let browser
try {
  await server.listen()
  browser=await chromium.launch({headless:true})
  for(const failure of ['truncated','interrupted']){
    mode=failure
    const page=await browser.newPage()
    page.on('pageerror',error=>errors.push(error.message))
    await page.goto('http://127.0.0.1:'+server.httpServer.address().port+'/')
    await page.getByLabel('Base URL',{exact:true}).fill('https://draft.example/v1')
    await page.getByLabel('Model',{exact:true}).fill('draft-model')
    await page.getByLabel('API key',{exact:true}).fill('browser-fixture-token')
    await page.getByRole('button',{name:'Configure',exact:true}).click()
    // Read the settled UI on the failing baseline too.
    await page.getByRole('button',{name:'Configuring',exact:true}).waitFor({state:'hidden'})
    const receipt={failure,keyRetained:await page.getByLabel('API key',{exact:true}).inputValue()==='browser-fixture-token',
      url:await page.getByLabel('Base URL',{exact:true}).inputValue(),
      visibleError:await page.getByText(/response could not be read/).count(),
      falseSuccess:await page.getByText('Unknown completed',{exact:true}).count()}
    receipts.push(receipt)
    await page.close()
  }
  console.log(JSON.stringify({browser:browser.version(),posts,receipts,errors},null,2))
  assert.deepEqual(errors,[])
  assert.equal(posts,2)
  for(const receipt of receipts){
    assert.equal(receipt.keyRetained,true)
    assert.equal(receipt.url,'https://draft.example/v1')
    assert.equal(receipt.visibleError,1)
    assert.equal(receipt.falseSuccess,0)
  }
} finally {
  await browser?.close()
  await server.close()
}
