// Actual native registry/cache/policy and actual Source read factories. No execution.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {registerHooks} from 'node:module';
const pkg=process.env.OPENCLAW_PACKAGE_DIR,box=process.env.PIXEL_TEST_TYPEBOX_DIR;
assert.ok(pkg&&box,'exact copied SDK and locked Source TypeBox required');
const sha=x=>createHash('sha256').update(x).digest('hex');
const recipes=Object.fromEntries(['image-envelope','plugin-tool-version'].map(n=>[n,JSON.parse(fs.readFileSync(new URL('../host/openclaw-'+n+'.json',import.meta.url)))]));
for(const [r,m]of [['image-envelope','tool-search-BInRpkE3.js'],['plugin-tool-version','tools-D5HS8Q_I.js']])assert.equal(sha(fs.readFileSync(path.join(pkg,'dist',m))),recipes[r].patchedSha256);
const root=fs.mkdtempSync(path.join(os.tmpdir(),'ods-provider-admission-'));fs.chmodSync(root,0o700);
process.env.OPENCLAW_STATE_DIR=path.join(root,'state');process.env.OPENCLAW_CONFIG_PATH=path.join(root,'config.json');
test.after(()=>fs.rmSync(root,{recursive:true,force:true}));
const source=fileURLToPath(new URL('../../../../vendor/pixel/plugin/',import.meta.url));
const aliases={typebox:pathToFileURL(path.join(box,'build/index.mjs')).href,'typebox/value':pathToFileURL(path.join(box,'build/value/index.mjs')).href,
 'openclaw/plugin-sdk/plugin-entry':pathToFileURL(path.join(pkg,'dist/plugin-sdk/plugin-entry.js')).href};
const loader=registerHooks({resolve(s,c,next){return c.parentURL?.startsWith(pathToFileURL(source).href)&&aliases[s]?{url:aliases[s],shortCircuit:true}:next(s,c);}});
test.after(()=>loader.deregister());
Object.assign(process.env,{PIXEL_AGENT_ID:'pixel',PIXEL_LIMB_EMAIL_ENABLED:'1',PIXEL_LIMB_CALENDAR_ENABLED:'1',PIXEL_LIMB_CALENDAR_WRITE_ENABLED:'0',PIXEL_LIMB_EMAIL_DRAFTS_ENABLED:'0',PIXEL_LIMB_WEB_ENABLED:'0',PIXEL_LIMB_SOCIAL_ENABLED:'0'});
const names=['pixel_gmail_inbox','pixel_gmail_sent','pixel_gmail_search','pixel_gmail_read','pixel_gmail_thread','pixel_gmail_count','pixel_calendar_list','pixel_calendar_get','pixel_calendar_count'];
const factories=[];const {default:entry}=await import(pathToFileURL(path.join(source,'index.js')));
entry.register({config:{agents:{defaults:{userTimezone:'America/Sao_Paulo'}}},registerTool(factory,{names:declared}){if(names.includes(declared[0]))factories.push(factory);}});
assert.equal(factories.length,9);
const {a:resolveTools,i:getMeta,o:setMeta,n:copyMeta,s:resetCache}=await import(pathToFileURL(path.join(pkg,'dist/tools-D5HS8Q_I.js')));
const {u:apply,m:createRef}=await import(pathToFileURL(path.join(pkg,'dist/tool-search-BInRpkE3.js')));
let priorImage=fs.readFileSync(path.join(pkg,'dist/tool-search-BInRpkE3.js'),'utf8');
for(const [before,after]of [...recipes['image-envelope'].replacements].reverse()){assert.equal(priorImage.split(after).length,2);priorImage=priorImage.replace(after,()=>before);}
assert.equal(sha(priorImage),recipes['image-envelope'].sourceSha256);
const priorHash='d31ad2f589de1a916626b62d0b87d58ee235f13bd040ecd99de77dcdc539465a';
for(const [before,after]of recipes['image-envelope'].previousReplacements[priorHash]){assert.equal(priorImage.split(before).length,2);priorImage=priorImage.replace(before,()=>after);}
assert.equal(sha(priorImage),priorHash);
const priorUrl=pathToFileURL(path.join(pkg,'dist/tool-search-BInRpkE3.js')).href+'?ods-prior-provider-admission';
const priorLoader=registerHooks({load(url,c,next){return url===priorUrl?{format:'module',source:priorImage,shortCircuit:true}:next(url,c);}});test.after(()=>priorLoader.deregister());
const {u:priorApply,m:priorRef}=await import(priorUrl);
const {t:filterByPolicy}=await import(pathToFileURL(path.join(pkg,'dist/agent-tools.policy-Dyn4VuLn.js')));
const simple=name=>({name,description:name,parameters:{type:'object',properties:{}},execute:async()=>{throw Error('no business execution');}});
const controls=['tool_search','tool_describe','tool_call'].map(simple);
function offer(tools,{context=65536,model=65536,agentId='pixel',mode='tools'}={},old=false){
 return (old?priorApply:apply)({tools:[...controls,...tools,simple('specialist')],agentId,config:{agents:{list:[{id:agentId,contextTokens:context}]},tools:{toolSearch:{enabled:true,mode}}},
 modelContextWindowTokens:model,runId:randomUUID(),catalogRef:(old?priorRef:createRef)()}).tools;
}
function liveAndCached(version){
 resetCache();const dir=path.join(root,version??'unknown');fs.mkdirSync(dir);const file=path.join(dir,'index.mjs');fs.writeFileSync(file,'export default {register(){}};');
 fs.writeFileSync(path.join(dir,'package.json'),JSON.stringify({name:'pixel-source-broker',version:version??'0.0.0',type:'module',openclaw:{extensions:['./index.mjs']}}));
 fs.writeFileSync(path.join(dir,'openclaw.plugin.json'),JSON.stringify({id:'pixel-source-broker',...(version?{version}:{}),activation:{onStartup:true},contracts:{tools:names},configSchema:{type:'object',properties:{}}}));
 const config={agents:{defaults:{workspace:root,userTimezone:'America/Sao_Paulo'}},plugins:{allow:['pixel-source-broker'],load:{paths:[dir]},entries:{'pixel-source-broker':{enabled:true}}}};
 const context={agentId:'pixel',config,workspaceDir:root,sessionKey:'agent:pixel:fixture-'+version};let factoryCalls=0;
 const registry={tools:[{pluginId:'pixel-source-broker',source:file,names,declaredNames:names,optional:false,
 factory:ctx=>{factoryCalls++;return factories.map(fn=>fn(ctx));}}],diagnostics:[]};
 const first=resolveTools({context,runtimeRegistry:registry,toolAllowlist:names});assert.equal(first.length,9);assert.equal(factoryCalls,1);
 const second=resolveTools({context,runtimeRegistry:registry,toolAllowlist:names});assert.equal(second.length,9);assert.equal(factoryCalls,1,'actual second resolution uses captured descriptor cache');
 return {first,second};
}
test('actual live factory, cached descriptor and wrapper metadata bind exact manifest version',()=>{
 for(const version of ['4.3.44','4.3.45','4.3.46']){
  const {first,second}=liveAndCached(version);
  for(const tools of [first,second])for(const tool of tools){assert.equal(getMeta(tool).pluginId,'pixel-source-broker');assert.equal(getMeta(tool).pluginVersion,version);
   const wrapped={...tool};copyMeta(tool,wrapped);assert.deepEqual(getMeta(wrapped),getMeta(tool));}
  assert.equal(offer(first).filter(t=>names.includes(t.name)).length,version==='4.3.45'?9:0);
  assert.equal(offer(second).filter(t=>names.includes(t.name)).length,version==='4.3.45'?9:0);
 }
});
test('missing or unknown version and wrong provenance keep the whole family deferred',()=>{
 const tools=factories.map(fn=>fn({agentId:'pixel'}));
 for(const version of [undefined,'unknown','4.3.44','4.3.46']){
  for(const t of tools)setMeta(t,{pluginId:'pixel-source-broker',...(version?{pluginVersion:version}:{})});
  assert.equal(offer(tools).filter(t=>names.includes(t.name)).length,0);
 }
 for(const t of tools)setMeta(t,{pluginId:'another-plugin',pluginVersion:'4.3.45'});
 assert.equal(offer(tools).filter(t=>names.includes(t.name)).length,0);
});
test('Source45 direct family retains original post-policy objects and exact schemas within8192bytes',()=>{
 const tools=factories.map(fn=>fn({agentId:'pixel'}));for(const t of tools)setMeta(t,{pluginId:'pixel-source-broker',pluginVersion:'4.3.45'});
 const offered=offer(tools);for(const t of tools)assert.ok(offered.includes(t));
 assert.equal(offered.filter(t=>names.includes(t.name)).length,9);assert.ok(!offered.some(t=>t.name==='specialist'));
 const bytes=Buffer.byteLength(JSON.stringify(tools.map(t=>({type:'function',function:{name:t.name,description:t.description,parameters:t.parameters}}))));assert.ok(bytes<=8192);
 const allowed=filterByPolicy(tools,{deny:['pixel_calendar_count']});assert.ok(!offer(allowed).some(t=>t.name==='pixel_calendar_count'));
 const duplicate={...tools[0]};setMeta(duplicate,getMeta(tools[0]));assert.ok(!offer([...tools,duplicate]).some(t=>t.name===duplicate.name));
 for(const config of [{context:8192},{context:32768},{model:8192},{model:32768},{agentId:'other'},{mode:'directory'},{mode:'code'}]){
  const result=offer(tools,config),before=offer(tools,config,true);assert.deepEqual(result.map(t=>({name:t.name,parameters:t.parameters})),before.map(t=>({name:t.name,parameters:t.parameters})));
 }
 console.log(JSON.stringify({sourceReadSchemaBytes:bytes}));
});
test('oversize and unserializable Source45 family atomically preserve deferred fallback',()=>{
 for(const extra of [{description:'x'.repeat(8192)},{parameters:(()=>{const s={};s.self=s;return s;})()}]){
  const tools=factories.map(fn=>fn({agentId:'pixel'}));tools[0]={...tools[0],...extra};for(const t of tools)setMeta(t,{pluginId:'pixel-source-broker',pluginVersion:'4.3.45'});
  assert.equal(offer(tools).filter(t=>names.includes(t.name)).length,0);
 }
});
