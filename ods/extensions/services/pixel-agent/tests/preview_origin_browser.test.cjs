/* Exercise the production origin handshake with Chromium and real HTTP.
 * Framework-shaped exports use root assets, lazy modules and nested routes.
 * No live ODS service or user project is modified.
 */
const test=require('node:test'),assert=require('node:assert/strict'),http=require('node:http'),path=require('node:path'),fs=require('node:fs'),crypto=require('node:crypto');
const {chromium}=require('playwright'),{buildSync}=require('esbuild');
const services=path.resolve(__dirname,'../..');
const bundle=buildSync({entryPoints:[path.join(services,'dashboard/src/lib/previewOrigin.js')],bundle:true,write:false,format:'iife',globalName:'previewOrigin',platform:'browser',nodePaths:(process.env.NODE_PATH||'').split(path.delimiter)}).outputFiles[0].text;
const source=fs.readFileSync(path.join(services,'pixel-agent/host/workspace_preview.py'),'utf8').replace(/\r\n/g,'\n');
const csp=source.match(/^CSP = \(\n([\s\S]+?)\n\)/m)[1].split('\n').map(line=>JSON.parse(line.trim())).join('');
const dashboardCsp=fs.readFileSync(path.join(services,'dashboard/nginx.conf'),'utf8').match(/add_header Content-Security-Policy "([^"]+)" always/)[1];
const hash=text=>crypto.createHash('sha256').update(text).digest('hex');
async function listen(handler){const server=http.createServer(handler);await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));return server;}
test('verified origins render Next/Vite root assets, nested routes and lazy modules without Dashboard access',async t=>{
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE?{executablePath:process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE}:{})});t.after(()=>browser.close());
 for(const framework of ['next','vite'])await t.test(framework,async t=>{
  const folder=framework==='next'?'_next/static':'assets';
  const html=`<!doctype html><link rel="stylesheet" href="/${folder}/app.css"><script type="module" src="/${folder}/app.js"></script><h1>Arcade</h1><a href="/games/snake/">Snake</a><button id="play">Play</button><output id="score">Ready</output><output id="isolation"></output>`;
  const assets={'index.html':html,'games/snake/index.html':html,[`${folder}/app.css`]:'body{background:rgb(10,10,11)}h1{color:rgb(230,230,230)}',
   [`${folder}/app.js`]:`document.querySelector('#play').onclick=async()=>{const m=await import('/${folder}/lazy.js');document.querySelector('#score').textContent=m.score};try{parent.document.querySelector('#canary').textContent='escaped'}catch{document.querySelector('#isolation').textContent='isolated'}`,
   [`${folder}/lazy.js`]:'export const score="Playing"'};
  const sha='a'.repeat(64),siteId=`site-${sha.slice(0,24)}`,files=Object.entries(assets).map(([p,text])=>({path:p,bytes:Buffer.byteLength(text),sha256:hash(text)})),bytes=files.reduce((n,f)=>n+f.bytes,0);
  const manifest={schemaVersion:1,siteId,sha256:sha,bytes,files};let publicCookies=0,dashboardRootRequests=0;
  function serve(req,res,root){
   const relative=req.url.replace(root,'').split('?')[0];
   if(relative==='__ods_manifest__.json'){res.writeHead(200,{'content-type':'application/json','access-control-allow-origin':'*'});res.end(JSON.stringify(manifest));return;}
   const name=relative==='__ods_view__.html'?'index.html':relative.endsWith('/')?relative+'index.html':relative||'index.html',body=assets[name];
   res.writeHead(body?200:404,{'content-type':name.endsWith('.css')?'text/css':name.endsWith('.js')?'text/javascript':'text/html','access-control-allow-origin':'*','cross-origin-resource-policy':'cross-origin','content-security-policy':csp});res.end(body||'missing');
  }
  const dedicated=await listen((req,res)=>{if(req.headers.cookie)publicCookies++;serve(req,res,req.url.startsWith(`/${siteId}/`)?`/${siteId}/`:'/');});
  const preview={siteId,sha256:sha,entrySha256:hash(html),port:dedicated.address().port,files:files.length,bytes,url:`http://${siteId}.localhost:${dedicated.address().port}/${siteId}/`};
  const dashboard=await listen((req,res)=>{
   if(req.url.startsWith(`/pixel-preview/${siteId}/`)){serve(req,res,`/pixel-preview/${siteId}/`);return;}
   if(req.url==='/probe.js'){res.writeHead(200,{'content-type':'text/javascript'});res.end(bundle);return;}
   if(req.url==='/favicon.ico'){res.writeHead(204);res.end();return;}
   if(req.url!=='/'){dashboardRootRequests++;res.writeHead(404);res.end();return;}
   res.writeHead(200,{'content-type':'text/html','content-security-policy':dashboardCsp.replaceAll('__PIXEL_PREVIEW_PORT__',String(dedicated.address().port))});res.end(`<p id="canary">unchanged</p><script src="/probe.js"></script>`);
  });t.after(()=>{for(const server of [dedicated,dashboard]){server.closeAllConnections();server.close();}});
  const page=await browser.newPage();t.after(()=>page.close());await page.goto(`http://127.0.0.1:${dashboard.address().port}/`);
  const access=await page.evaluate(async preview=>{
   const fallback={url:`/pixel-preview/${preview.siteId}/`,frameUrl:`/pixel-preview/${preview.siteId}/__ods_view__.html`,sandbox:'allow-scripts allow-forms allow-downloads',route:'private-dashboard'};
   const access=await window.previewOrigin.resolveVerifiedPreview(preview,fallback,new AbortController().signal);
   const frame=document.createElement('iframe');frame.title='Arcade';frame.src=access.frameUrl;frame.setAttribute('sandbox',access.sandbox);document.body.append(frame);return access;
  },preview);
  assert.equal(access.route,'verified-site-origin');assert.ok(!access.sandbox.includes('allow-same-origin'));
  assert.equal(await page.evaluate(async url=>{try{await fetch(url);return true;}catch{return false;}},`http://${siteId}.localhost:${dashboard.address().port}/`),false,'Dashboard CSP must still reject other localhost ports');
  const frame=page.frameLocator('iframe');await frame.locator('#isolation').filter({hasText:'isolated'}).waitFor();await frame.locator('#play').click();await frame.locator('#score').filter({hasText:'Playing'}).waitFor();
  assert.equal(await frame.locator('body').evaluate(node=>getComputedStyle(node).backgroundColor),'rgb(10, 10, 11)');
  await frame.getByRole('link',{name:'Snake'}).click();await frame.locator('#isolation').filter({hasText:'isolated'}).waitFor();await frame.locator('#play').click();await frame.locator('#score').filter({hasText:'Playing'}).waitFor();
  assert.equal(await frame.locator('#isolation').textContent(),'isolated');assert.equal(await page.locator('#canary').textContent(),'unchanged');
  assert.equal(publicCookies,0);assert.equal(dashboardRootRequests,0);
 });
});
