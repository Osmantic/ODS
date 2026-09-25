/* Real-browser policy regression. Test-only fixture bytes, never a Pixel artifact.
 * Run: node --test preview_browser.test.cjs
 * Requires Playwright + Chromium; optional PIXEL_TEST_CHROMIUM_EXECUTABLE.
 * Servers bind loopback ephemeral ports, never ODS's live service ports.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { chromium } = require('playwright');

const services = path.resolve(__dirname, '../..');
const pixelSource = fs.readFileSync(path.join(services, 'dashboard/src/pages/Pixel.jsx'), 'utf8').replace(/\r\n/g, '\n');
const resolverSource = pixelSource.match(/export function resolvePreviewAccess\([\s\S]+?\n\}/)?.[0];
assert.ok(resolverSource, 'use the actual production iframe resolver');
const resolvePreviewAccess = vm.runInNewContext(resolverSource.replace('export ', '') + '\nresolvePreviewAccess');
function pythonStringConstant(file, name) {
  const source = fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
  const lines = source.match(new RegExp(`^${name} = \\(\\n([\\s\\S]+?)\\n\\)`, 'm'))?.[1];
  assert.ok(lines, `missing actual production CSP ${name}`);
  return lines.split('\n').map(line => JSON.parse(line.trim())).join('');
}
const localCsp = pythonStringConstant(path.join(services, 'pixel-agent/host/workspace_preview.py'), 'CSP');
const remoteCsp = pythonStringConstant(path.join(services, 'pixel-edge/pixel_edge.py'), '_REMOTE_PREVIEW_CSP');
const edgeSource = fs.readFileSync(path.join(services, 'pixel-edge/pixel_edge.py'), 'utf8');
const remoteCorp = edgeSource.match(/"Cross-Origin-Resource-Policy": "([^"]+)"/)?.[1];
assert.ok(remoteCorp, 'use the actual preview resource policy');
async function listen(handler) {
  const server = http.createServer(handler);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return server;
}
const origin = server => `http://127.0.0.1:${server.address().port}`;

function fixture(external) {
  return `<!doctype html><meta charset="utf-8"><title>Preview policy fixture</title>
<link rel="stylesheet" href="fixture.css"><script src="fixture.js" defer></script>
<output id="asset">unloaded</output>
<form id="client"><label>Habit<input id="habit" required></label><button>Add habit</button></form>
<ul id="items"></ul><output id="added">0</output>
<form id="networkForm" action="${external}/submitted" method="post"><input name="test" value="fixture-only"><button>External submit</button></form>
<button id="export">Export CSV</button><button id="fetch">External fetch</button>
<button id="escape">Try frame escape</button><output id="isolation"></output>
<script>
window.violations=[]; window.forms=0;
addEventListener('securitypolicyviolation', e=>violations.push(e.violatedDirective));
document.querySelector('#client').addEventListener('submit', e=>{
 e.preventDefault(); forms++;
 const item=document.createElement('li'); item.textContent=document.querySelector('#habit').value;
 document.querySelector('#items').append(item); document.querySelector('#added').textContent=String(forms);
});
document.querySelector('#export').onclick=()=>{
 const blob=new Blob(['habit\\nRead a book\\n'],{type:'text/csv'});
 const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='habits.csv';
 document.body.append(link);link.click();link.remove();
};
document.querySelector('#fetch').onclick=()=>fetch('${external}/fetch').catch(()=>{});
document.querySelector('#escape').onclick=()=>{
 let dom=false,topDenied=false;
 try {parent.document.querySelector('#canary').textContent='changed';dom=true;} catch {}
 try {top.location.href='${external}/escape';} catch {topDenied=true;}
 const popup=window.open('${external}/popup');
 document.querySelector('#isolation').textContent=JSON.stringify({dom,topDenied,popup:!!popup});
};
</script>`;
}

test('embedded preview forms/exports work with network and parent boundaries intact', async t => {
  const browser = await chromium.launch({ headless:true, chromiumSandbox:true,
    ...(process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE} : {}) });
  t.after(()=>browser.close());
  let externalHits=0, parentPosts=0;
  const external = await listen((_req,res)=>{externalHits++;res.end('must not be reached');});
  const bytes=fixture(origin(external));
  const dedicated = await listen((_req,res)=>{res.writeHead(200,{'content-type':'text/html','content-security-policy':localCsp});res.end(bytes);});
  let selected;
  const dashboard = await listen((req,res)=>{
    if(req.method==='POST') parentPosts++;
    if(req.url.startsWith('/pixel-preview/')) {
      const asset = req.url.endsWith('/fixture.js') ? ['text/javascript', "document.querySelector('#asset').textContent='loaded'"]
        : req.url.endsWith('/fixture.css') ? ['text/css', '#asset { color: rgb(1, 2, 3); }']
          : ['text/html', bytes];
      res.writeHead(200,{'content-type':asset[0],'content-security-policy':remoteCsp,
        'cross-origin-resource-policy':remoteCorp});res.end(asset[1]);return;
    }
    res.writeHead(200,{'content-type':'text/html'});
    res.end(`<p id="canary">unchanged</p><iframe title="Policy fixture" src="${selected.url}" sandbox="${selected.sandbox}"></iframe>`);
  });
  t.after(()=>{for(const server of [external,dedicated,dashboard]) {server.closeAllConnections();server.close();}});
  const siteId='site-'+'a'.repeat(24);
  const receipt={siteId,url:`http://${siteId}.localhost:${dedicated.address().port}/${siteId}/`};
  for(const remote of [false,true]) {
    await t.test(remote?'opaque authenticated-relay policy':'dedicated preview origin policy',async()=>{
      selected=remote ? resolvePreviewAccess(receipt,{hostname:'127.0.0.1',protocol:'http:'})
        : {url:receipt.url,sandbox:'allow-scripts allow-same-origin allow-forms allow-downloads'};
      assert.ok(selected.sandbox.includes('allow-forms'));
      assert.ok(selected.sandbox.includes('allow-downloads'));
      assert.equal(selected.sandbox.includes('allow-same-origin'),!remote);
      assert.ok(!selected.sandbox.includes('allow-popups')&&!selected.sandbox.includes('allow-top-navigation'));
      const context=await browser.newContext({acceptDownloads:true});
      try {
        const page=await context.newPage();
        await page.goto(origin(dashboard),{waitUntil:'load'});
        const frame=page.frames().find(f=>f.parentFrame());
        assert.ok(frame);
        if(remote) {
          await frame.waitForFunction(()=>document.querySelector('#asset').textContent==='loaded', null, {timeout: 3000});
          assert.equal(await frame.locator('#asset').evaluate(el=>getComputedStyle(el).color), 'rgb(1, 2, 3)');
        }
        await frame.getByRole('button',{name:'Add habit',exact:true}).click();
        assert.equal(await frame.locator('#added').textContent(),'0','required validation still applies');
        await frame.getByLabel('Habit',{exact:true}).fill('Read a book');
        await frame.getByRole('button',{name:'Add habit',exact:true}).click();
        await frame.waitForFunction(()=>document.querySelector('#added').textContent==='1');
        assert.equal(await frame.locator('#items').textContent(),'Read a book');
        const downloadPromise=page.waitForEvent('download');
        await frame.getByRole('button',{name:'Export CSV',exact:true}).click();
        const download=await downloadPromise;
        assert.equal(download.suggestedFilename(),'habits.csv');
        const stream=await download.createReadStream();let exported='';
        for await(const chunk of stream) exported+=chunk;
        assert.equal(exported,'habit\nRead a book\n');
        await frame.getByRole('button',{name:'External submit',exact:true}).click();
        await frame.waitForFunction(()=>violations.some(d=>d==='form-action'));
        await frame.getByRole('button',{name:'External fetch',exact:true}).click();
        await frame.waitForFunction(()=>violations.some(d=>d==='connect-src'));
        await frame.getByRole('button',{name:'Try frame escape',exact:true}).click();
        const isolation=JSON.parse(await frame.locator('#isolation').textContent());
        assert.deepEqual(isolation,{dom:false,topDenied:true,popup:false});
        assert.equal(await page.locator('#canary').textContent(),'unchanged');
        assert.equal(context.pages().length,1);
        assert.equal(page.url(),origin(dashboard)+'/');
        if(remote) {
          assert.equal(await frame.evaluate(()=>{try {localStorage.setItem('test','x');return false;} catch{return true;}}),true,'remote sandbox remains opaque');
        }
        assert.equal(externalHits,0,'no external submission, fetch, popup, or navigation reached the sink');
        assert.equal(parentPosts,0);
      } finally {await context.close();}
    });
  }
});

// Synthetic application code, not a runtime shim or model-authored artifact.
// The negative control matches a storage read before game handlers register.
function storageFixture(guarded, storageName, external) {
  return `<!doctype html><meta charset="utf-8"><title>Optional storage fixture</title>
<button id="start">Start game</button><canvas id="game" width="32" height="32" tabindex="0" aria-label="Game"></canvas>
<output id="phase">not booted</output><output id="score">0</output>
<button id="save">Save score</button><output id="saved">not attempted</output>
<button id="boundaries">Check boundaries</button><output id="isolation"></output>
<script>
window.violations=[];
addEventListener('securitypolicyviolation',e=>violations.push(e.violatedDirective));
let state={running:false,score:0};
${guarded ? `try {
 const saved=JSON.parse(window[${JSON.stringify(storageName)}].getItem('score'));
 if(Number.isInteger(saved)&&saved>=0)state.score=saved;
} catch { /* Optional persistence; keep working in memory. */ }`
    : `state.score=Number(window[${JSON.stringify(storageName)}].getItem('score'))||0;`}
const canvas=document.querySelector('#game'),ctx=canvas.getContext('2d');
function draw(){
 document.querySelector('#phase').textContent=state.running?'running':'paused';
 document.querySelector('#score').textContent=String(state.score);
 ctx.fillStyle=state.running?'rgb(0,128,0)':'rgb(128,0,0)';ctx.fillRect(0,0,32,32);
}
document.querySelector('#start').onclick=()=>{state.running=true;draw();};
canvas.onkeydown=e=>{if(e.code==='Space'){e.preventDefault();state.running=!state.running;draw();}};
document.querySelector('#save').onclick=()=>{
 state.score+=7;
 for(const name of ['localStorage','sessionStorage']){
  try {window[name].setItem('score',JSON.stringify(state.score));}
  catch { /* The working score must survive this failed saving attempt. */ }
 }
 document.querySelector('#saved').textContent='Memory only; reload resets';draw();
};
document.querySelector('#boundaries').onclick=()=>{
 let dom=false,storage=false;
 try {parent.document.querySelector('#canary').textContent='changed';dom=true;}catch{}
 try {parent.localStorage.setItem('preview-parent-canary','changed');storage=true;}catch{}
 document.querySelector('#isolation').textContent=JSON.stringify({dom,storage});
 fetch('${external}/must-not-fetch').catch(()=>{});
};
draw();window.booted=true;
</script>`;
}

test('opaque preview storage cannot abort guarded startup, click, Space or saving', {timeout:60000}, async t => {
  const browser=await chromium.launch({headless:true,chromiumSandbox:true,
    ...(process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE ? {executablePath:process.env.PIXEL_TEST_CHROMIUM_EXECUTABLE} : {})});
  t.after(()=>browser.close());
  let externalHits=0,parentPosts=0,bytes='';
  const external=await listen((_req,res)=>{externalHits++;res.end('must not be reached');});
  const siteId='site-'+'b'.repeat(24);
  const selected=resolvePreviewAccess({siteId,url:`http://${siteId}.localhost:9437/${siteId}/`});
  assert.equal(selected.sandbox,'allow-scripts allow-forms allow-downloads');
  assert.match(remoteCsp,/sandbox allow-scripts allow-forms allow-downloads;/);
  assert.doesNotMatch(remoteCsp,/allow-same-origin/);
  const dashboard=await listen((req,res)=>{
    if(req.method==='POST')parentPosts++;
    if(req.url===selected.frameUrl){
      res.writeHead(200,{'content-type':'text/html; charset=utf-8',
        'content-security-policy':remoteCsp,'cross-origin-resource-policy':remoteCorp});
      res.end(bytes);return;
    }
    res.writeHead(200,{'content-type':'text/html; charset=utf-8'});
    res.end(`<p id="canary">unchanged</p><iframe title="Storage fixture" src="${selected.frameUrl}" sandbox="${selected.sandbox}"></iframe>`);
  });
  t.after(()=>{for(const server of [external,dashboard]){server.closeAllConnections();server.close();}});
  for(const storageName of ['localStorage','sessionStorage']){
    for(const guarded of [false,true]){
      await t.test(`${storageName}: ${guarded?'guarded memory fallback':'unsafe startup fails'}`,async()=>{
        bytes=storageFixture(guarded,storageName,origin(external));
        const context=await browser.newContext();
        try {
          const page=await context.newPage(),errors=[];
          page.on('pageerror',error=>errors.push({name:error.name,message:error.message}));
          await page.goto(origin(dashboard),{waitUntil:'load'});
          await page.evaluate(()=>localStorage.setItem('preview-parent-canary','unchanged'));
          const frame=page.frames().find(f=>f.parentFrame());assert.ok(frame);
          assert.equal(frame.url(),origin(dashboard)+selected.frameUrl);
          // Exercise real browser getters under the actual production CSP,
          // without mocking Storage, changing permissions or adding a shim.
          assert.deepEqual(await frame.evaluate(()=>['localStorage','sessionStorage'].map(name=>{
            try {void window[name];return 'allowed';}catch(error){return error.name;}
          })),['SecurityError','SecurityError']);
          await frame.getByRole('button',{name:'Start game',exact:true}).click();
          if(!guarded){
            await frame.locator('#game').press('Space');
            assert.equal(await frame.locator('#phase').textContent(),'not booted');
            assert.equal(await frame.evaluate(()=>window.booted),undefined);
            assert.equal(errors.length,1);
            assert.equal(errors[0].name,'SecurityError');
            assert.ok(errors[0].message.includes(storageName));
          }else{
            assert.equal(await frame.evaluate(()=>window.booted),true);
            assert.equal(await frame.locator('#phase').textContent(),'running');
            const pixel=()=>frame.locator('#game').evaluate(el=>Array.from(el.getContext('2d').getImageData(0,0,1,1).data));
            assert.deepEqual(await pixel(),[0,128,0,255]);
            await frame.locator('#game').press('Space');
            assert.equal(await frame.locator('#phase').textContent(),'paused');
            assert.deepEqual(await pixel(),[128,0,0,255]);
            await frame.getByRole('button',{name:'Save score',exact:true}).click();
            assert.equal(await frame.locator('#saved').textContent(),'Memory only; reload resets');
            assert.equal(await frame.locator('#score').textContent(),'7');
            await frame.locator('#game').press('Space');
            assert.equal(await frame.locator('#phase').textContent(),'running','continue after failed saving');
            assert.equal(await frame.locator('#score').textContent(),'7');
            await frame.getByRole('button',{name:'Check boundaries',exact:true}).click();
            await frame.waitForFunction(()=>violations.includes('connect-src'),null,{timeout:3000});
            assert.deepEqual(JSON.parse(await frame.locator('#isolation').textContent()),{dom:false,storage:false});
            assert.equal(await page.locator('#canary').textContent(),'unchanged');
            assert.equal(await page.evaluate(()=>localStorage.getItem('preview-parent-canary')),'unchanged');
            assert.equal(externalHits,0);assert.equal(parentPosts,0);
            await page.reload({waitUntil:'load'});
            const reloaded=page.frames().find(f=>f.parentFrame());
            assert.equal(await reloaded.locator('#score').textContent(),'0','no durable persistence claim');
            assert.equal(await reloaded.evaluate(()=>window.booted),true);
            assert.deepEqual(errors,[],'guarded storage must not leak an uncaught initialization/save error');
          }
        }finally{await context.close();}
      });
    }
  }
});
