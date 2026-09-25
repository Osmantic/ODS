import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createWorkspacePreviewInspectTool,normalizeWorkspacePreviewInspectionParams as normalize,validateWorkspacePreviewInspectionReceipt as validate,inspectionPlanHash,INSPECTION_KIND,INSPECTION_SCOPE} from '../extensions/services/pixel-agent/plugin/workspace-preview-inspect.mjs';
const params=()=>({siteId:'site-'+'a'.repeat(24),sha256:'a'.repeat(64),viewport:{width:375,height:812},steps:[{action:'assert-hidden',locator:{selector:'#card'}},{action:'click',locator:{role:'button',name:'Mostrar próximos eventos',exact:true}},{action:'assert-visible',locator:{selector:'#card'}}]});
const state=visible=>({count:1,visible,display:visible?'block':'none',visibility:'visible',opacity:'1',hidden:!visible,hiddenUntilFound:false,rectCount:visible?1:0});
function receipt(request) {return {schemaVersion:1,kind:INSPECTION_KIND,status:'passed',siteId:request.siteId,sha256:request.sha256,planSha256:inspectionPlanHash(request),viewport:request.viewport,steps:request.steps.map((s,index)=>({index,...s,before:state(index!==0),stable:true,status:'passed',...(s.action==='click'?{after:state(true)}:{})})),diagnostics:{renderedHiddenAttributeCount:0,hiddenUntilFoundCount:0},blockedRequests:[],scope:INSPECTION_SCOPE};}
test('Unicode semantic locator and exact hash match Python canonical contract',()=>{
 const request=normalize(params());assert.equal(request.steps[1].locator.name,'Mostrar próximos eventos');
 assert.equal(inspectionPlanHash(request),'156ae762e4eb8cbec17063823bfb5a61705c2917251da8cdff43af2c9ffbc06c');
});
test('reject unsupported authority and malformed plans',()=>{
 for(const extra of [{url:'http://localhost'},{script:'alert(1)'},{dockerArgs:[]}]) assert.throws(()=>normalize({...params(),...extra}));
 for(const selector of ['xpath=//*','a >> b','x\u0000y','\u202Eattack']) {const p=params();p.steps[0].locator={selector};assert.throws(()=>normalize(p));}
 const p=params();p.steps[1].locator.exact=false;assert.throws(()=>normalize(p));
});
test('receipt requires exact site, plan and assertion evidence',()=>{
 const request=normalize(params()),good=receipt(request);assert.equal(validate(good,request),good);
 for(const change of [r=>r.sha256='b'.repeat(64),r=>r.planSha256='b'.repeat(64),r=>r.steps.pop(),r=>r.steps[0].before.visible=true,r=>r.steps[1].after=undefined,r=>r.steps[2].stable=false,r=>r.steps[0].locator.selector='#different',r=>r.blockedRequests=['navigation']]) {const bad=structuredClone(good);change(bad);assert.throws(()=>validate(bad,request));}
});
test('unavailable inspection never turns publication into behavior proof',async()=>{
 const tool=createWorkspacePreviewInspectTool({request:async()=>{throw Error('no image')}});
 const result=await tool.execute('test',params());assert.equal(result.isError,true);assert.equal(result.details.status,'failed');assert.equal(result.details.errorCode,'unavailable');
});

test('invalid selector receipt is a bound terminal failure without invented observations', async () => {
 const p=params();p.steps[0].locator={selector:"article:contains('private fixture')"};
 const request=normalize(p), bad=receipt(request);
 bad.status='failed';bad.steps=[{index:0,...request.steps[0],stable:false,status:'failed',errorCode:'invalid_selector'}];
 assert.equal(validate(bad,request),bad);
 for(const change of [r=>r.steps[0].before={count:0},r=>r.steps[0].after=state(true),r=>r.steps[0].stable=true,
   r=>r.steps[0].status='passed',r=>r.status='passed',r=>r.steps[0].index=1,
   r=>r.steps[0].locator.selector='#different',r=>r.steps.push(receipt(request).steps[1])]) {
  const forged=structuredClone(bad);change(forged);assert.throws(()=>validate(forged,request));
 }
 const tool=createWorkspacePreviewInspectTool({request:async()=>bad});
 const result=await tool.execute('syntax',p);
 assert.equal(result.isError,true);assert.equal(result.details.status,'failed');
 assert.match(result.content[0].text,/Step 1 has invalid CSS selector syntax/);
 assert.match(result.content[0].text,/same affected element around the control click/);
 assert.match(result.content[0].text,/same published snapshot/);
 assert.doesNotMatch(result.content[0].text,/inspection unavailable/);
 const semantic=params(), semanticRequest=normalize(semantic), forged=receipt(semanticRequest);
 forged.status='failed';forged.steps=forged.steps.slice(0,2);
 forged.steps[1]={index:1,...semanticRequest.steps[1],stable:false,status:'failed',errorCode:'invalid_selector'};
 assert.throws(()=>validate(forged,semanticRequest));
});

test('only actual DOM SyntaxError is classified by the production isolated-world function', () => {
 const source=fs.readFileSync(new URL('../extensions/services/pixel-agent/host/preview_inspection_capsule.py',import.meta.url),'utf8');
 const expression=source.match(/SELECTOR_COUNT = r"""([\s\S]+?)"""/)[1];
 for(const error of [new Error('SyntaxError'), new DOMException('denied','SecurityError')]) {
  const fn=vm.runInNewContext('('+expression+')',{DOMException,document:{querySelectorAll(){throw error;}}});
  assert.throws(()=>fn('#fixture'),value=>value===error);
 }
 const fn=vm.runInNewContext('('+expression+')',{DOMException,document:{querySelectorAll(){throw new DOMException('invalid','SyntaxError');}}});
 assert.equal(fn('[').invalidSelector,true);
});
test('only exact valid evidence returned by tool',async()=>{
 const tool=createWorkspacePreviewInspectTool({request:async r=>receipt(r)});const result=await tool.execute('test',params());assert.equal(result.details.status,'passed');assert.equal(result.details.steps.length,3);assert.match(result.content[0].text,/not pixel paint/);
});


test('native inspection fixes Python environment and cwd while retaining exact request binding', async () => {
 const {default: childProcess} = await import('node:child_process');
 const {syncBuiltinESMExports} = await import('node:module');
 const originalExec = childProcess.execFile, platform = Object.getOwnPropertyDescriptor(process, 'platform');
 let called = false;
 try {
  Object.defineProperty(process, 'platform', {...platform, value:'darwin'});
  childProcess.execFile = (file,args,options,callback) => {
   called=true;
   assert.equal(file,'/usr/bin/python3');
   assert.deepEqual(args,['-E','-s','-B','/usr/local/libexec/ods-pixel-services/helpers/preview_inspection.py','request']);
   assert.equal(options.cwd,'/'); assert.deepEqual(options.env,{PATH:'/usr/bin:/bin',HOME:'/var/empty'});
   return {stdin:{on(){},end(body){queueMicrotask(()=>callback(null,JSON.stringify(receipt(JSON.parse(body)))+'\n'));}}};
  };
  syncBuiltinESMExports();
  const result=await createWorkspacePreviewInspectTool({transport:'native'}).execute('native-test',params());
  assert.equal(called,true); assert.equal(result.details.status,'passed');
 } finally {childProcess.execFile=originalExec;syncBuiltinESMExports();Object.defineProperty(process,'platform',platform);}
});
