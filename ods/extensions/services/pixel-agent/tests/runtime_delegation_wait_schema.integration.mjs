// Execute the exact pinned native yield factory and ODS Tool Search catalog in
// memory. Only the onYield callback is a fixture; no gateway/model is started.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {createRequire} from 'node:module';
import {isAbsolute,join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const root=process.env.OPENCLAW_PACKAGE_DIR;
assert.ok(root && isAbsolute(root),'provide the pinned OpenClaw package explicitly');
assert.equal(JSON.parse(readFileSync(join(root,'package.json'))).version,'2026.6.33');
const sha=s=>createHash('sha256').update(s).digest('hex');
const require=createRequire(join(root,'package.json'));
const {Type}=await import(pathToFileURL(require.resolve('typebox')).href);
const {b:readStringParam,l:jsonResult}=await import(pathToFileURL(join(root,'dist/common-Ctn7ag2d.js')).href);
const native=readFileSync(join(root,'dist/openclaw-tools-iHHy99PD.js'),'utf8');
const marker='//#region src/agents/tools/sessions-yield-tool.ts';
assert.equal(native.split(marker).length,2);
const factorySource=native.split(marker)[1].split('//#endregion')[0];
assert.equal(sha(factorySource),'3214db73ef93201c76288d40512d605c2b977ffd5bf68e2ffb9f94f0aac34e05');
const createYield=new Function('Type','readStringParam','jsonResult',factorySource+'\nreturn createSessionsYieldTool;')(Type,readStringParam,jsonResult);
const manifest=JSON.parse(readFileSync(new URL('../host/openclaw-image-envelope.json',import.meta.url)));
let source=readFileSync(join(root,'dist/tool-search-BInRpkE3.js'),'utf8');
assert.ok([manifest.sourceSha256,manifest.patchedSha256].includes(sha(source)),'reject unknown catalog bytes');
if(sha(source)===manifest.sourceSha256)for(const [before,after] of manifest.replacements){
  assert.equal(source.split(before).length,2);source=source.replace(before,()=>after);
}
assert.equal(sha(source),manifest.patchedSha256);
source=source.replace(/from "(\.\/[^"\n]+)"/g,(_all,name)=>`from "${pathToFileURL(join(root,'dist',name)).href}"`)
  .replace('from "typebox"',`from "${pathToFileURL(require.resolve('typebox')).href}"`);
const {h:createControls,u:applyCatalog}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const ctx={agentId:'pixel',runId:'yield-guidance-run',sessionId:'yield-guidance-session',sessionKey:'agent:pixel:owner'};
const guard=createToolLoopGuard();guard.observeRun(ctx,'pixel',{prompt:'Delegate two native reviews and combine their results.'});
const refusal=guard.beforeToolCall({toolName:'process',params:{action:'yield'},toolCallId:'refused'}, {...ctx,toolName:'process',toolCallId:'refused'},'pixel',true);
let fixtureSequence=0;

function fixture(options={}){
  const yields=[];
  const config={tools:{toolSearch:{enabled:true,mode:'tools'}}};
  const context={...ctx,runId:ctx.runId+'-'+(++fixtureSequence),config};
  const yieldTool=createYield({sessionId:ctx.sessionId,onYield:async value=>{yields.push(value);},...options});
  const controls=createControls(context);
  const catalog=applyCatalog({...context,tools:[...controls,yieldTool]});
  const byName=Object.fromEntries(catalog.tools.map(tool=>[tool.name,tool]));
  return {yields,yieldTool,catalog,byName};
}

test('refusal gives an invocation valid for the actual deferred native yield schema',async()=>{
  const f=fixture();assert.equal(refusal.block,true);assert.deepEqual(f.yields,[],'refusal must not execute a yield');
  assert.ok(!f.catalog.tools.some(tool=>tool.name==='sessions_yield'),'ODS defers this tool rather than exposing it directly');
  const search=await f.byName.tool_search.execute('search',{query:'sessions_yield'});
  const selected=search.details.find(tool=>tool.name==='sessions_yield');assert.ok(selected);
  const describeMatch=refusal.blockReason.match(/tool_describe with (\{[^\n]+?\})/);
  const callMatch=refusal.blockReason.match(/tool_call with (\{[^\n]+?\}\})/);
  assert.ok(describeMatch && callMatch,'guidance must give concrete discovery and native call arguments');
  const describe=JSON.parse(describeMatch[1]);
  const call=JSON.parse(callMatch[1]);
  assert.equal(describe.id,selected.id);assert.equal(call.id,selected.id);assert.deepEqual(call.args,{});
  const described=await f.byName.tool_describe.execute('describe',describe);
  assert.equal(described.details.id,selected.id);
  assert.equal(described.details.parameters.type,'object');
  assert.deepEqual(described.details.parameters.required??[],[]);
  assert.deepEqual(Object.keys(described.details.parameters.properties),['message']);
  assert.equal(described.details.parameters.properties.message.type,'string');
  assert.deepEqual(f.yields,[],'discovery must not execute a yield');
  const result=await f.byName.tool_call.execute('explicit-native-yield',call);
  assert.equal(result.details.result.details.status,'yielded');
  assert.deepEqual(f.yields,['Turn yielded.'],'only the explicit catalog call runs the native callback once');
});

test('native yield still reports unsupported or missing context without a fake handoff',async()=>{
  for(const options of [{sessionId:undefined},{onYield:undefined}]){
    const f=fixture(options);
    const result=await f.byName.tool_call.execute('unsupported',{id:'openclaw:core:sessions_yield',args:{}});
    assert.equal(result.details.result.details.status,'error');assert.deepEqual(f.yields,[]);
  }
});
