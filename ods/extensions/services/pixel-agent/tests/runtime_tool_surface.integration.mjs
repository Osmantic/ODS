// Exercise the actual reviewed package module and its catalog implementation.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {registerHooks} from 'node:module';
import { createHash, randomUUID } from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { createPublicWebExtractTool } from '../plugin/web-extract.mjs';
import { createWorkspacePreviewInspectTool, INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash } from '../plugin/workspace-preview-inspect.mjs';
import { createConnectionsStatusTool } from '../plugin/connections-status.mjs';
import {createIntegrationTools} from '../plugin/integrations.mjs';
import {withPiToolErrorContract} from '../plugin/pi-tool-result.mjs';
import {failedToolOutcome} from '../plugin/run-progress-budget.mjs';

const file = process.env.OPENCLAW_TOOL_SEARCH_MODULE;
const manifest = JSON.parse(readFileSync(new URL('../host/openclaw-image-envelope.json', import.meta.url)));
assert.equal(createHash('sha256').update(readFileSync(file)).digest('hex'), manifest.patchedSha256);
const { u: apply, m: createRef, h: createControls, v: resolveExact } = await import(pathToFileURL(file));
const { t: filterByPolicy } = await import(pathToFileURL(join(dirname(file), 'agent-tools.policy-Dyn4VuLn.js')));
const {o:setPluginToolMeta} = await import(pathToFileURL(join(dirname(file),'tools-D5HS8Q_I.js')));
const tool = name => ({ name, label: name, description: name, parameters: { type: 'object', properties: {} }, execute: async () => ({ content: [] }) });
const controls = ['tool_search', 'tool_describe', 'tool_call'].map(tool);

function connectionStatusTool(reads) {
  const unknownSource=()=>({lastObservedState:'unknown',recent:null,lastSuccessAt:null,complete:null,error:null});
  return createConnectionsStatusTool({read:async()=>{
    reads.push('fixed-status-read');
    return {schemaVersion:1,kind:'ods-connection-agent-status',scope:'reviewed-google',
      observedAt:'2026-10-29T00:00:00Z',available:false,reason:'not-connected',
      recordConfigured:false,localSavedSignIn:false,connectionState:'not-connected',
      ownerEnabled:{gmailRead:false,calendarRead:false},
      providerPermissionObserved:{gmailRead:null,calendarRead:null},sourceCheckedAt:null,
      reads:{email:unknownSource(),calendar:unknownSource()},providerCheckedNow:false};
  }});
}
function run(tools, extra = {}) {
  const catalogRef = createRef();
  const result = apply({ tools: [...controls, ...tools], agentId: 'pixel',
    runId: randomUUID(), catalogRef,
    config: { tools: { toolSearch: { enabled: true, mode: 'tools' } } }, ...extra });
  return { ...result, catalogRef };
}

let previousRuntime;
async function runC14(tools,extra={}) {
  if(!previousRuntime) {
    const digest=value=>createHash('sha256').update(value).digest('hex');
    const predecessor='c14f943442d87dd4e3a9005636b182e4de1527cf2ce29490799ce9b774e903b3';
    let source=readFileSync(file,'utf8');
    for(const [old,replacement] of [...manifest.replacements].reverse()) {
      assert.equal(source.split(replacement).length,2);source=source.replace(replacement,old);
    }
    assert.equal(digest(source),manifest.sourceSha256);
    for(const [old,replacement] of manifest.previousReplacements[predecessor]) {
      assert.equal(source.split(old).length,2);source=source.replace(old,replacement);
    }
    assert.equal(digest(source),predecessor);
    // Load the exact verified predecessor under a distinct module URL. Its
    // relative imports use the same reviewed SDK; no files or SDK mutate.
    const previousUrl=pathToFileURL(file).href+'?ods-reviewed-c14-surface';
    const loader=registerHooks({load(url,context,next){return url===previousUrl
      ? {format:'module',source,shortCircuit:true}:next(url,context);}});
    try {previousRuntime=await import(previousUrl);} finally {loader.deregister();}
  }
  const catalogRef=previousRuntime.m();
  const result=previousRuntime.u({tools:[...controls,...tools],agentId:'pixel',runId:randomUUID(),catalogRef,
    config:{tools:{toolSearch:{enabled:true,mode:'tools'}}},...extra});
  return {...result,catalogRef};
}

function integrationTools(submissions=[]) {
  return createIntegrationTools({submit:async request=>{
    submissions.push(request);
    if(request.action==='notes-read')return {ok:true,path:'Example.md',text:'Invented note.'};
    if(request.action==='notify')return {ok:true,sent:true};
    return {ok:true,servers:[]};
  }}).map(tool=>{
    const wrapped=withPiToolErrorContract(tool);
    setPluginToolMeta(wrapped,{pluginId:'pixel-ods'});
    return wrapped;
  });
}

test('Coffee schemas are statically direct at qualified32K including provider-only64K, never above an8K cap',async()=>{
  const cases=[
    {window:65536,explicit:undefined,visible:true},
    {window:32768,explicit:undefined,visible:true},
    {window:65536,explicit:8192,visible:false},
    {window:8192,explicit:65536,visible:false},
    {window:8192,explicit:undefined,visible:false},
    {window:65536,explicit:32767,visible:false},
    {window:undefined,explicit:32768,visible:true},
    {window:undefined,explicit:undefined,visible:false},
    {window:65536,explicit:65536,agentId:'other-agent',visible:false},
  ];
  for(const candidate of cases) {
    const submissions=[], originals=integrationTools(submissions);
    const config={agents:{defaults:{model:{primary:'fixture/model'}},list:[{id:'pixel'}]},
      tools:{toolSearch:{enabled:true,mode:'tools'}}};
    if(candidate.explicit!==undefined)config.agents.list[0].contextTokens=candidate.explicit;
    const agentId=candidate.agentId??'pixel';
    const result=run(originals,{config,agentId,modelContextWindowTokens:candidate.window});
    assert.equal(result.catalogToolCount,originals.length,'keep every permitted implementation in discovery');
    for(const original of originals) {
      assert.equal(result.tools.includes(original),candidate.visible,JSON.stringify(candidate)+' '+original.name);
      if(candidate.visible)assert.equal(result.tools.find(tool=>tool.name===original.name),original);
    }
    if(!candidate.visible) {
      const baseline=await runC14(originals,{config,agentId,modelContextWindowTokens:candidate.window});
      assert.deepEqual(result.tools,baseline.tools,'smaller/unknown contexts and other agents preserve c14 surface');
      assert.equal(result.catalogToolCount,baseline.catalogToolCount);
    }
    const api=Object.fromEntries(createControls({agentId,catalogRef:result.catalogRef,config}).map(tool=>[tool.name,tool]));
    const ids=[];
    for(const original of originals) {
      const found=await api.tool_search.execute('find',{query:original.name,limit:10});
      const hit=found.details.find(tool=>tool.name===original.name);
      assert.ok(hit,'direct and deferred schemas remain discoverable');
      assert.equal(hit.id,`openclaw:pixel-ods:${original.name}`);
      ids.push(hit.id);
      assert.deepEqual((await api.tool_describe.execute('describe',{id:hit.id})).details.parameters,original.parameters);
    }
    assert.equal(new Set(ids).size,3,'retain the three distinct canonical IDs');
    const inputs=[{action:'read',path:'Example.md'},{message:'Invented notification.'},{action:'list'}];
    for(const [index,id] of ids.entries()) {
      await api.tool_call.execute('call',{id,args:inputs[index]});
      if(agentId==='pixel') {
        const previousCalls=submissions.length;
        let rejected=false, invalid;
        try {invalid=await api.tool_call.execute('invalid',{id,args:{unexpected:'invalid'}});} catch {rejected=true;}
        assert.ok(rejected || invalid?.isError===true || invalid?.details?.ok===false,'actual native/Pi error contract');
        assert.equal(submissions.length,previousCalls,'invalid schema never reaches the backend');
      }
    }
    assert.equal(submissions.length,3,'invalid schemas never reach the unchanged backend');
  }
});

test('Coffee direct offer preserves policy denial, ambiguous-name deferral and nonPortal modes',async()=>{
  const config={agents:{list:[{id:'pixel',contextTokens:65536}]},tools:{toolSearch:{enabled:true,mode:'tools'}}};
  for(const deniedName of ['pixel_ods_notes','pixel_ods_notify','pixel_ods_mcp']) {
    const submissions=[], originals=integrationTools(submissions);
    const permitted=filterByPolicy(originals,{deny:[deniedName]});
    const result=run(permitted,{config});
    assert.equal(result.catalogToolCount,2);
    assert.ok(result.tools.every(tool=>tool.name!==deniedName));
    const context={agentId:'pixel',catalogRef:result.catalogRef,config};
    assert.equal(resolveExact(context,deniedName),undefined);
    const api=Object.fromEntries(createControls(context).map(tool=>[tool.name,tool]));
    await assert.rejects(api.tool_call.execute('denied',{id:`openclaw:pixel-ods:${deniedName}`,args:{}}));
    assert.deepEqual(submissions,[]);
    const duplicate=run([...originals,...integrationTools().filter(tool=>tool.name===deniedName)],{config});
    assert.equal(duplicate.catalogToolCount,4);
    assert.ok(duplicate.tools.every(tool=>tool.name!==deniedName),'never select one ambiguous implementation');
    assert.ok(originals.filter(tool=>tool.name!==deniedName).every(tool=>duplicate.tools.includes(tool)));
  }
  for(const mode of ['code','directory']) {
    const originals=integrationTools(), candidate={...config,tools:{toolSearch:{enabled:true,mode}}};
    const result=run(originals,{config:candidate});
    const baseline=await runC14(originals,{config:candidate});
    if(mode==='code' && !process.allowedNodeEnvironmentFlags.has('--permission')) {
      const toolsConfig={...config,tools:{toolSearch:{enabled:true,mode:'tools'}}};
      assert.deepEqual(baseline.tools,(await runC14(originals,{config:toolsConfig})).tools,'c14 falls back to tools mode');
      assert.deepEqual(result.tools,run(originals,{config:toolsConfig}).tools,'preserve the exact upstream tools fallback');
    } else assert.deepEqual(result.tools,baseline.tools,'preserve actual c14 code/directory behavior');
  }
});

test('actual native discovery and deferred MCP dispatch preserve child error and success semantics', async () => {
  const submissions=[];
  let failed=false;
  const original=withPiToolErrorContract(createIntegrationTools({submit:async request=>{
    submissions.push(request);
    return {ok:true,text:'Invented MCP response',isError:failed};
  }}).find(tool=>tool.name==='pixel_ods_mcp'));
  const result=run([original]);
  assert.ok(!result.tools.includes(original),'MCP remains permission-filtered native discovery, not a new direct tool');
  const config={tools:{toolSearch:{enabled:true,mode:'tools'}}};
  const ctx={agentId:'pixel',catalogRef:result.catalogRef,config};
  const controls=Object.fromEntries(createControls(ctx).map(tool=>[tool.name,tool]));
  const found=await controls.tool_search.execute('find-mcp',{query:original.name,limit:10});
  const entry=found.details.find(tool=>tool.name===original.name);
  assert.ok(entry);
  assert.deepEqual((await controls.tool_describe.execute('describe-mcp',{id:entry.id})).details.parameters,original.parameters);
  for (const expected of [false,true]) {
    failed=expected;
    const receipt=await controls.tool_call.execute('mcp-'+expected,{id:entry.id,args:{
      action:'call',server:'invented',tool:'fixture',arguments:{limit:1},
    }});
    assert.equal(receipt.isError===true,expected);
    assert.equal(receipt.details.ok===false,expected);
    assert.equal(receipt.details.result.isError===true,expected);
    assert.equal(failedToolOutcome({result:receipt}),expected);
    assert.equal(receipt.details.tool.name,original.name);
    assert.match(receipt.details.result.content[0].text,/Invented MCP response/);
  }
  assert.deepEqual(submissions,[false,true].map(()=>({action:'mcp-call',server:'invented',tool:'fixture',arguments:{limit:1}})));
});

test('connection status has a stable direct32K surface and native discovery at smaller contexts', async () => {
  const name='pixel_ods_connections_status';
  for(const contextTokens of [4096,8192,16384,32767,32768,65536]) {
    const reads=[], original=connectionStatusTool(reads), existing=tool('pixel_gmail_read');
    const config={agents:{defaults:{contextTokens:65536},list:[{id:'pixel',contextTokens}]},
      tools:{toolSearch:{enabled:true,mode:'tools'}}};
    const result=run([original,existing],{config});
    assert.equal(result.tools.includes(original),contextTokens>=32768);
    assert.equal(result.catalogToolCount,2,'keep both permitted implementations in the catalog');
    const ctx={agentId:'pixel',catalogRef:result.catalogRef,config};
    const api=Object.fromEntries(createControls(ctx).map(value=>[value.name,value]));
    const found=await api.tool_search.execute('find',{query:name,limit:10});
    const hit=found.details.find(value=>value.name===name);
    assert.ok(hit);
    assert.deepEqual((await api.tool_describe.execute('describe',{id:hit.id})).details.parameters,
      {type:'object',additionalProperties:false,properties:{}});
    assert.equal(resolveExact(ctx,original.name),original);
    await api.tool_call.execute('status',{id:hit.id,args:{}});
    assert.deepEqual(reads,['fixed-status-read']);
    await assert.rejects(api.tool_call.execute('override',{id:hit.id,args:{url:'https://invalid.test'}}));
    assert.deepEqual(reads,['fixed-status-read'],'invalid arguments never enter the status reader');
  }
});

test('status visibility does not bypass policy or select duplicate implementations', async () => {
  const reads=[], original=connectionStatusTool(reads), existing=tool('read');
  const config={agents:{list:[{id:'pixel',contextTokens:32768}]},tools:{toolSearch:{enabled:true,mode:'tools'}}};
  const denied=run(filterByPolicy([original,existing],{deny:[original.name]}),{config});
  assert.ok(!denied.tools.includes(original));
  const ctx={agentId:'pixel',catalogRef:denied.catalogRef,config};
  assert.equal(resolveExact(ctx,original.name),undefined);
  const api=Object.fromEntries(createControls(ctx).map(value=>[value.name,value]));
  assert.ok(!(await api.tool_search.execute('find',{query:original.name})).details.some(value=>value.name===original.name));
  await assert.rejects(api.tool_describe.execute('denied',{id:original.name}));
  await assert.rejects(api.tool_call.execute('denied',{id:original.name,args:{}}));
  assert.deepEqual(reads,[]);
  const duplicate=run([original,connectionStatusTool(reads),existing],{config});
  assert.equal(duplicate.catalogToolCount,3);
  assert.ok(duplicate.tools.every(value=>value.name!==original.name),'ambiguous names remain deferred');
  assert.deepEqual(run([original],{config,agentId:'another-agent'}).tools,controls);
  assert.deepEqual(run([original]).tools,controls,'unknown context does not assume a qualified32K profile');
});

test('resolved native model context fills absent config and never widens an explicit cap', () => {
  for (const [modelWindow,explicit,visible] of [
    [65536,undefined,true],[8192,undefined,false],
    [65536,8192,false],[8192,65536,false],[65536,32768,true],
  ]) {
    const status=connectionStatusTool([]);
    const config={agents:{defaults:{model:{primary:'fixture/model'}},list:[{id:'pixel'}]},
      models:{providers:{fixture:{models:[{id:'model',contextWindow:modelWindow}]}}},
      tools:{toolSearch:{enabled:true,mode:'tools'}}};
    if(explicit!==undefined)config.agents.list[0].contextTokens=explicit;
    assert.equal(run([status],{config,modelContextWindowTokens:modelWindow}).tools.includes(status),visible);
  }
});

test('the new runtime model-window fallback does not change other agents', () => {
  const config={agents:{defaults:{},list:[{id:'another-agent'}]},
    tools:{toolSearch:{enabled:true,mode:'tools'}}};
  const originals=[tool('pixel_gmail_read'),connectionStatusTool([])];
  assert.deepEqual(run(originals,{config,agentId:'another-agent',modelContextWindowTokens:8192}).tools,controls);
});

test('working tools are direct while specialist tools remain in the real catalog', () => {
  const read = tool('read'), status = tool('pixel_ods_extension_request_status');
  const advance = tool('pixel_ods_extension_request_advance');
  const prepare = tool('pixel_ods_extension_request_prepare');
  const retry = tool('pixel_ods_extension_request_retry');
  const proposal = tool('pixel_ods_source_proposal');
  const advanced = tool('pixel_ods_extension_proposal');
  const library = tool('pixel_ods_python_library_proposal');
  const preview = tool('pixel_ods_workspace_preview');
  const result = run([read, status, prepare, advance, retry, proposal, library, preview, advanced]);
  assert.deepEqual(result.tools, [...controls, read, status, preview]);
  assert.equal(result.catalogToolCount, 9);
  assert.equal(result.catalogRegistered, true);
  assert.ok(result.catalogRef.current);
});

test('the ordinary direct surface stays small while every specialist stays catalogued', () => {
  const nativeNames = ['read', 'write', 'edit', 'apply_patch', 'exec', 'process',
    'web_fetch', 'web_search', 'pixel_ods_web_extract', 'pixel_ods_skill', 'pixel_ods_ask_user',
    'pixel_ods_extensions', 'pixel_ods_extension_request_status', 'pixel_ods_workspace_preview', 'pixel_ods_workspace_preview_inspect'];
  const specialistNames = ['pixel_ods_extension_request_prepare', 'pixel_ods_extension_request_advance',
    'pixel_ods_extension_request_retry', 'pixel_ods_source_proposal',
    'pixel_ods_python_library_proposal', 'pixel_ods_extension_proposal'];
  const result = run([...nativeNames, ...specialistNames].map(tool));
  assert.deepEqual(result.tools.map(t => t.name), [...controls.map(t => t.name), ...nativeNames]);
  assert.equal(result.tools.length, 18); // Fifteen native tools plus the three search controls.
  assert.equal(result.catalogToolCount, nativeNames.length + specialistNames.length);
});

test('policy-denied tools are not synthesized and duplicate names stay deferred', () => {
  const result = run([tool('read'), tool('read'), tool('web_fetch')]);
  assert.deepEqual(result.tools.map(t => t.name), [...controls.map(t => t.name), 'web_fetch']);
  assert.equal(result.catalogToolCount, 3);
  assert.ok(!result.tools.some(t => t.name === 'exec'));
});

test('deferred specialists remain searchable, describable and callable through the normal dispatcher', async () => {
  const names = ['pixel_ods_extension_request_prepare', 'pixel_ods_extension_request_advance',
    'pixel_ods_extension_request_retry', 'pixel_ods_source_proposal',
    'pixel_ods_python_library_proposal', 'pixel_ods_extension_proposal', 'pixel_ods_workspace_preview'];
  const specialists = names.map(tool);
  const { catalogRef } = run(specialists);
  const dispatched = [];
  const ctx = { agentId: 'pixel', catalogRef,
    config: { tools: { toolSearch: { enabled: true, mode: 'tools' } } },
    executeTool: async params => {
      dispatched.push(params);
      return { content: [{ type: 'text', text: 'dispatcher-receipt' }] };
    },
  };
  const controlsByName = Object.fromEntries(createControls(ctx).map(t => [t.name, t]));
  for (const specialist of specialists) {
    assert.equal(resolveExact(ctx, specialist.name), specialist);
    const search = await controlsByName.tool_search.execute('search', { query: specialist.name, limit: 100 });
    assert.ok(search.details.some(entry => entry.name === specialist.name));
    const described = await controlsByName.tool_describe.execute('describe', { id: specialist.name });
    assert.equal(described.details.name, specialist.name);
    assert.deepEqual(described.details.parameters, specialist.parameters);
    await controlsByName.tool_call.execute('invoke', { id: specialist.name, args: { marker: 'test-only' } });
    assert.equal(dispatched.at(-1).tool, specialist);
    assert.deepEqual(dispatched.at(-1).input, { marker: 'test-only' });
  }
  assert.equal(dispatched.length, specialists.length);
  await assert.rejects(controlsByName.tool_call.execute('denied', { id: 'policy_denied_tool', args: {} }));
  assert.equal(dispatched.length, specialists.length);
});

test('other agents retain the original catalog behavior', () => {
  const read = tool('read');
  assert.deepEqual(run([read], { agentId: 'another-agent' }).tools, controls);
  assert.deepEqual(run([read], { agentId: 'another-agent', isVisibleCatalogTool: () => true }).tools, [...controls, read]);
});

test('8K tools remain discoverable and dispatch the original policy-filtered objects', async () => {
  const deferred=['read','write','edit','apply_patch','exec','process','web_search','web_fetch','pixel_ods_web_extract','pixel_ods_skill','pixel_ods_ask_user','pixel_ods_extensions','pixel_ods_workspace_preview','pixel_ods_workspace_preview_inspect','pixel_ods_extension_request_status'];
  const originals=deferred.map(tool);
  const config={agents:{defaults:{contextTokens:65536},list:[{id:'pixel',contextTokens:8192}]},
    tools:{toolSearch:{enabled:true,mode:'tools'}}};
  const {tools:visible,catalogRef,catalogToolCount}=run(originals,{config});
  assert.deepEqual(visible,controls);
  assert.equal(catalogToolCount,originals.length);
  const dispatched=[];
  const ctx={agentId:'pixel',catalogRef,config,executeTool:async p=>{dispatched.push(p);return {content:[{type:'text',text:'receipt'}]};}};
  const api=Object.fromEntries(createControls(ctx).map(t=>[t.name,t]));
  for(const name of deferred){
    const original=originals.find(t=>t.name===name);
    const found=await api.tool_search.execute('search',{query:name,limit:10});
    assert.ok(found.details.some(t=>t.name===name));
    const described=await api.tool_describe.execute('describe',{id:name});
    assert.deepEqual(described.details.parameters,original.parameters);
    await api.tool_call.execute('call',{id:name,args:{marker:'8k'}});
    assert.equal(dispatched.at(-1).tool,original);
    assert.deepEqual(dispatched.at(-1).input,{marker:'8k'});
  }
  await assert.rejects(api.tool_call.execute('denied',{id:'policy_denied_tool',args:{}}));
  assert.equal(dispatched.length,deferred.length);
  config.agents.list[0].contextTokens=65536;
  assert.deepEqual(run(originals,{config}).tools,[...controls,...originals]);
});

test('disabled search preserves the input tools', () => {
  const read = tool('read');
  const result = run([read], { config: { tools: { toolSearch: { enabled: false, mode: 'tools' } } } });
  assert.equal(result.compacted, false);
  assert.deepEqual(result.tools, [...controls, read]);
});

test('8K connected reads expose their original schemas without exposing source writes', async () => {
  const names=['pixel_gmail_search','pixel_gmail_read','pixel_calendar_list','pixel_calendar_get'];
  const reads=names.map(name=>({...tool(name),parameters:{type:'object',properties:{marker:{type:'string'}},required:['marker']},
    execute:async(_id,args)=>({content:[{type:'text',text:name+':'+args.marker}]})}));
  const write=tool('pixel_calendar_propose_create');
  const config={agents:{defaults:{contextTokens:65536},list:[{id:'pixel',contextTokens:8192}]},
    tools:{toolSearch:{enabled:true,mode:'tools'}}};
  const result=run([...reads,write],{config});
  assert.deepEqual(result.tools,[...controls,...reads]);
  assert.equal(result.catalogToolCount,reads.length+1,'keep every permitted tool in native discovery');
  for(const original of reads) {
    const direct=result.tools.find(value=>value.name===original.name);
    assert.equal(direct,original,'preserve the exact policy-filtered implementation and schema');
    assert.equal((await direct.execute('read',{marker:'fresh-fact'})).content[0].text,original.name+':fresh-fact');
  }
  const api=Object.fromEntries(createControls({agentId:'pixel',catalogRef:result.catalogRef,config}).map(t=>[t.name,t]));
  const found=await api.tool_search.execute('find',{query:write.name});
  assert.ok(found.details.some(value=>value.name===write.name),'a permitted write remains discoverable');
  for(const contextTokens of [4096,8191,16384]) {
    config.agents.list[0].contextTokens=contextTokens;
    assert.deepEqual(run(reads,{config}).tools,controls,'retain the existing surface outside the qualified 8K profile');
  }
});

test('permitted count tools stay deferred, searchable and policy-filtered across context profiles', async () => {
  const counts = [
    {...tool('pixel_gmail_count'), parameters: {type:'object',additionalProperties:false,
      properties:{query:{type:'string',maxLength:1000}}}},
    {...tool('pixel_calendar_count'), parameters: {type:'object',additionalProperties:false,required:['window'],
      properties:{window:{type:'object',additionalProperties:false,required:['kind','days'],
        properties:{kind:{const:'rolling'},days:{type:'integer',minimum:1,maximum:730}}}}}},
  ];
  // Synthetic implementations isolate catalog/dispatcher behavior; Source
  // contract and returned-data privacy are qualified by Source plugin tests.
  const existing = [tool('pixel_gmail_read'),tool('pixel_calendar_list'),tool('pixel_calendar_propose_create')];
  for (const contextTokens of [8192,32768,65536]) {
    const config={agents:{list:[{id:'pixel',contextTokens}]},tools:{toolSearch:{enabled:true,mode:'tools'}}};
    const originals=[...counts,...existing], result=run(originals,{config});
    assert.equal(result.catalogToolCount,originals.length,'retain every permitted count and existing tool');
    assert.ok(counts.every(count=>!result.tools.includes(count)),'counts add no direct-schema surface');
    const dispatched=[];
    const ctx={agentId:'pixel',catalogRef:result.catalogRef,config,executeTool:async request=>{
      dispatched.push(request);return {content:[{type:'text',text:'synthetic-count-receipt'}]};
    }};
    const api=Object.fromEntries(createControls(ctx).map(value=>[value.name,value]));
    for(const [index,original] of counts.entries()) {
      const found=await api.tool_search.execute('find',{query:original.name,limit:10});
      const hit=found.details.find(value=>value.name===original.name);
      assert.ok(hit);
      const described=await api.tool_describe.execute('describe',{id:hit.id});
      assert.deepEqual(described.details.parameters,original.parameters);
      const args=index===0?{}:{window:{kind:'rolling',days:7}};
      await api.tool_call.execute('count',{id:hit.id,args});
      assert.equal(dispatched.at(-1).tool,original);
      assert.deepEqual(dispatched.at(-1).input,args);
    }
    const permitted=filterByPolicy(originals,{deny:['pixel_calendar_count']});
    const restricted=run(permitted,{config}), restrictedCtx={...ctx,catalogRef:restricted.catalogRef};
    assert.equal(restricted.catalogToolCount,originals.length-1);
    assert.equal(resolveExact(restrictedCtx,'pixel_calendar_count'),undefined);
    const deniedApi=Object.fromEntries(createControls(restrictedCtx).map(value=>[value.name,value]));
    await assert.rejects(deniedApi.tool_call.execute('denied',{id:'pixel_calendar_count',
      args:{window:{kind:'rolling',days:7}}}));
    assert.equal(dispatched.length,counts.length,'denied count never reaches the dispatcher');
    assert.ok(existing.every(value=>resolveExact(restrictedCtx,value.name)===value));
  }
});

test('8K read visibility never restores a denied tool or selects an ambiguous implementation', () => {
  const names=['pixel_gmail_search','pixel_gmail_read','pixel_calendar_list','pixel_calendar_get'];
  const originals=names.map(tool);
  const permitted=filterByPolicy(originals,{deny:[names[0]]});
  const config={agents:{list:[{id:'pixel',contextTokens:8192}]},tools:{toolSearch:{enabled:true,mode:'tools'}}};
  const denied=run(permitted,{config});
  assert.deepEqual(denied.tools,[...controls,...originals.slice(1)]);
  assert.equal(resolveExact({agentId:'pixel',catalogRef:denied.catalogRef},names[0]),undefined);
  const ambiguous=run([...permitted,tool(names[1])],{config});
  assert.deepEqual(ambiguous.tools,[...controls,...originals.slice(2)]);
  assert.deepEqual(run(originals,{config,agentId:'another-agent'}).tools,controls);
});

test('discovery bounds long manuals while describe and dispatch retain the complete original tool', async () => {
  const description='Calendar read summary. '+ 'Detailed operating instructions. '.repeat(160)
    + 'Final approval rule and distinctive-calendar-needle.';
  const original={...tool('calendar_specialist'),description,
    parameters:{type:'object',required:['date'],properties:{date:{type:'string',description:'Use the full date.'}}}};
  const {catalogRef}=run([original]);
  const dispatched=[];
  const ctx={agentId:'pixel',catalogRef,config:{tools:{toolSearch:{enabled:true,mode:'tools'}}},
    executeTool:async params=>{dispatched.push(params);return {content:[{type:'text',text:'original-read-receipt'}]};}};
  const api=Object.fromEntries(createControls(ctx).map(value=>[value.name,value]));
  // Matching still uses the full description, including text outside the summary.
  const found=await api.tool_search.execute('find',{query:'distinctive-calendar-needle'});
  assert.equal(found.details.length,1);
  const entry=found.details[0];
  assert.equal(entry.name,original.name);
  assert.ok(entry.description.length<=180,'discovery must not inject the complete manual');
  assert.ok(entry.description.endsWith('...'),'a shortened summary must be recognizable');
  assert.deepEqual(JSON.parse(found.content[0].text),found.details,'the bounded search reply stays valid JSON');
  const described=await api.tool_describe.execute('describe',{id:entry.id});
  assert.equal(described.details.description,description,'full instructions remain available through describe');
  assert.deepEqual(described.details.parameters,original.parameters);
  for(const key of ['id','source','sourceName','name','label'])assert.equal(entry[key],described.details[key]);
  const args={date:'2026-10-29'};
  await api.tool_call.execute('read',{id:entry.id,args});
  assert.equal(dispatched.length,1);
  assert.equal(dispatched[0].tool,original);
  assert.deepEqual(dispatched[0].input,args);
  assert.equal(original.description,description);
});

test('ordinary discovery pages fit the result limit and keep upstream descriptions outside the Portal tools mode', async () => {
  const originals=Array.from({length:5},(_,index)=>({...tool('calendar_'+index),description:'Calendar manual. '+ 'Details. '.repeat(600)}));
  // Directory mode does not register search controls or a searchable catalog.
  for(const [agentId,mode] of [['pixel','tools'],['other-agent','tools'],['pixel','code']]) {
    const config={tools:{toolSearch:{enabled:true,mode}}};
    const catalogRef=createRef();
    const ctx={agentId,catalogRef,config,runId:randomUUID()};
    const nativeControls=createControls(ctx);
    const applied=apply({...ctx,tools:[...nativeControls,...originals]});
    assert.equal(applied.catalogRegistered,true,`${agentId}/${mode} must have a real catalog`);
    const api=Object.fromEntries(nativeControls.map(value=>[value.name,value]));
    const found=await api.tool_search.execute('find',{query:'calendar',limit:5});
    assert.equal(found.details.length,5);
    if(agentId==='pixel' && mode==='tools') {
      assert.ok(found.content[0].text.length<3000,'a normal five-result page must not be cut at the 4K tool-result limit');
      assert.deepEqual(JSON.parse(found.content[0].text),found.details);
    } else for(const entry of found.details)assert.equal(entry.description,originals[0].description);
  }
});

test('Portal discovery uses compact JSON without changing schema strings or structured metadata', async () => {
  const original={...tool('calendar_layout'),description:'Preserve line one.\n  Preserve line two: "ação".',
    parameters:{type:'object',required:['body'],properties:{body:{type:'string',
      description:'Literal indentation:\n  do not trim\n\nfinal line',default:'  keep these spaces  '}}}};
  for(const [agentId,mode] of [['pixel','tools'],['other-agent','tools'],['pixel','code']]) {
    const config={tools:{toolSearch:{enabled:true,mode}}};
    const catalogRef=createRef();
    const ctx={agentId,catalogRef,config,runId:randomUUID()};
    const nativeControls=createControls(ctx);
    apply({...ctx,tools:[...nativeControls,original]});
    const api=Object.fromEntries(nativeControls.map(value=>[value.name,value]));
    const found=await api.tool_search.execute('find',{query:original.name});
    const described=await api.tool_describe.execute('describe',{id:found.details[0].id});
    for(const reply of [found,described]) {
      assert.deepEqual(JSON.parse(reply.content[0].text),reply.details);
      const expected=agentId==='pixel' && mode==='tools'
        ? JSON.stringify(reply.details) : JSON.stringify(reply.details,null,2);
      assert.equal(reply.content[0].text,expected,`${agentId}/${mode} discovery serialization`);
    }
    assert.equal(described.details.description,original.description);
    assert.deepEqual(described.details.parameters,original.parameters);
  }
});

test('tool visibility is independent of owner language and request content', () => {
  const tools = [tool('read'), tool('pixel_ods_skill'), tool('specialist')];
  for (const prompt of ['instale a extensÃ£o', 'do not install; research only', 'sim', 'éŸ³æ¥½']) {
    assert.deepEqual(run(tools, { prompt }).tools, [...controls, ...tools.slice(0, 2)]);
  }
});


test('preview is directly callable through the exact policy-filtered object', async () => {
  const {createWorkspacePreviewTool} = await import('../plugin/workspace-preview.mjs');
  let calls=0;
  const preview=createWorkspacePreviewTool({request:async () => {calls++; return {status:'failed'};}});
  const result=run([preview]);
  const direct=result.tools.find(t => t.name === preview.name);
  assert.equal(direct,preview);
  assert.deepEqual(direct.parameters.required,['relativeDirectory']);
  assert.equal(direct.parameters.additionalProperties,false);
  // Direct exposure changes no execution/receipt policy: an unverified response
  // is still a failure, never a fabricated published URL.
  const response=await direct.execute('preview',{relativeDirectory:'Playground/example'});
  assert.equal(calls,1); assert.equal(response.isError,true);
  assert.equal(result.catalogToolCount,1);
});

test('denied and ambiguous preview tools remain unavailable directly', () => {
  assert.ok(!run([]).tools.some(t => t.name === 'pixel_ods_workspace_preview'));
  const result=run([tool('pixel_ods_workspace_preview'),tool('pixel_ods_workspace_preview')]);
  assert.deepEqual(result.tools,controls); assert.equal(result.catalogToolCount,2);
  assert.deepEqual(run([tool('pixel_ods_workspace_preview')],{agentId:'another-agent'}).tools,controls);
});

test('targeted public extraction is directly offered with its exact bounded schema', async () => {
  let calls = 0;
  const extractor = createPublicWebExtractTool({
    guardedFetch: async ({url}) => {
      calls++;
      return {response: new Response('Board power: contents\n' + 'Navigation\n'.repeat(1000) + '\nBoard power 250 W\n',
        {headers: {'Content-Type':'text/plain'}}), finalUrl:url, release() {}};
    },
    readResponseText: async response => ({text:await response.text(), truncated:false}),
    extractBasicHtmlContent: async () => {throw new Error('plain-text fixture');},
  });
  const direct = run([extractor]).tools.find(t => t.name === extractor.name);
  assert.equal(direct, extractor, 'retain the policy-filtered implementation object');
  assert.deepEqual(direct.parameters, {type:'object', additionalProperties:false, required:['url'], properties:{
    url:{type:'string', minLength:10, maxLength:1024}, query:{type:'string', minLength:2, maxLength:200},
    occurrence:{type:'integer', minimum:1, maximum:1000000},
  }});
  const result = await direct.execute('extract', {url:'https://docs.example.org/specs', query:'Board power', occurrence:2});
  assert.equal(calls, 1);
  assert.equal(result.details.matched, true);
  assert.equal(result.details.evidence_truncated_before, true);
  assert.equal(result.details.occurrence, 2);
  assert.equal(result.details.match_count, 2);
  assert.match(result.content[0].text, /Board power 250 W/);
  assert.match(result.content[0].text, /EXTERNAL_UNTRUSTED_CONTENT/);
  assert.equal((await direct.execute('private', {url:'http://127.0.0.1/specs'})).isError, true);
  assert.equal(calls, 1, 'native exposure cannot bypass public URL validation');
  for (const occurrence of [0, -1, 1.5, '2', null, 1000001]) {
    assert.equal((await direct.execute('invalid-occurrence', {
      url:'https://docs.example.org/specs', query:'Board power', occurrence,
    })).isError, true);
  }
  assert.equal(calls, 1, 'native exposure cannot bypass occurrence validation');
  const first = await direct.execute('first', {url:'https://docs.example.org/specs', query:'Board power', occurrence:1});
  assert.equal(first.details.occurrence, 1);
  assert.equal(first.details.next_occurrence, 2);
  assert.equal(calls, 2);
  const bounded = await direct.execute('upper-bound', {url:'https://docs.example.org/specs', query:'Board power', occurrence:1000000});
  assert.equal(calls, 3, 'the inclusive upper bound passes input validation');
  assert.equal(bounded.details.requested_occurrence, 1000000);
  assert.equal(bounded.details.match_count, 2);
  assert.equal(bounded.isError, true, 'out-of-range document selection is still an explicit error');
});

test('native extraction preserves actual runtime policy denials and ambiguity deferral', () => {
  const extractor = tool('pixel_ods_web_extract');
  const filtered = filterByPolicy([extractor, tool('web_fetch')], {deny:['pixel_ods_web_extract']});
  assert.deepEqual(filtered.map(t => t.name), ['web_fetch']);
  const denied = run(filtered);
  assert.deepEqual(denied.tools.map(t => t.name), [...controls.map(t => t.name), 'web_fetch']);
  assert.equal(resolveExact({agentId:'pixel', catalogRef:denied.catalogRef}, extractor.name), undefined);
  const ambiguous = run([extractor, tool(extractor.name)]);
  assert.deepEqual(ambiguous.tools, controls);
  assert.equal(ambiguous.catalogToolCount, 2);
  assert.deepEqual(run([extractor], {agentId:'another-agent'}).tools, controls);
});

test('native preview inspection retains exact snapshot validation and bounded broker execution', async () => {
  let calls = 0;
  const inspector = createWorkspacePreviewInspectTool({request: async request => {
    calls++;
    return {schemaVersion:1, kind:INSPECTION_KIND, status:'failed', errorCode:'unavailable',
      siteId:request.siteId, sha256:request.sha256, planSha256:inspectionPlanHash(request), scope:INSPECTION_SCOPE};
  }});
  const direct = run([inspector]).tools.find(tool => tool.name === inspector.name);
  assert.equal(direct, inspector);
  const args = {siteId:'site-' + 'a'.repeat(24), sha256:'a'.repeat(64), viewport:{width:800,height:600},
    steps:[{action:'assert-hidden',locator:{selector:'#details'}},
      {action:'click',locator:{role:'button',name:'Show details',exact:true}},
      {action:'assert-visible',locator:{selector:'#details'}}]};
  assert.equal((await direct.execute('wrong-snapshot',{...args,sha256:'b'.repeat(64)})).details.errorCode,'invalid_request');
  assert.equal(calls,0);
  const result = await direct.execute('inspect',args);
  assert.equal(calls,1);
  assert.equal(result.isError,true);
  assert.equal(result.details.errorCode,'unavailable');
  assert.match(result.content[0].text,/not pixel paint/);
});

test('native inspection cannot expose absent, denied or ambiguous capabilities', () => {
  const inspector = tool('pixel_ods_workspace_preview_inspect');
  assert.deepEqual(run([]).tools,controls);
  const filtered = filterByPolicy([inspector,tool('read')],{deny:[inspector.name]});
  assert.deepEqual(filtered.map(t=>t.name),['read']);
  const denied = run(filtered);
  assert.equal(resolveExact({agentId:'pixel',catalogRef:denied.catalogRef},inspector.name),undefined);
  assert.ok(!denied.tools.some(t=>t.name===inspector.name));
  assert.deepEqual(run([inspector,tool(inspector.name)]).tools,controls);
  assert.deepEqual(run([inspector],{agentId:'another-agent'}).tools,controls);
});

// pixel_ods_research is offered only while the owner's Perplexica is
// configured. It is deferred, so toggling it changes only the server-side
// catalog: the visible tools, and so the prompt, stay byte-identical.
test('offering or hiding the optional Perplexica tool leaves the visible surface unchanged', () => {
  const names = ['read', 'web_fetch', 'web_search', 'pixel_ods_web_extract'];
  const without = run(names.map(tool));
  const offered = run([...names, 'pixel_ods_research'].map(tool));
  assert.equal(JSON.stringify(offered.tools), JSON.stringify(without.tools));
  assert.equal(offered.catalogToolCount, without.catalogToolCount + 1);
  assert.equal(resolveExact({agentId:'pixel',catalogRef:offered.catalogRef},'pixel_ods_research')?.name,'pixel_ods_research');
  assert.equal(resolveExact({agentId:'pixel',catalogRef:without.catalogRef},'pixel_ods_research'),undefined);
});

// Tool Search is the research tool's only path. Its tool_call result is one
// text block holding the catalog entry and the whole result, escaped again.
// OpenClaw keeps a block of at most the tool-result cap unchanged and cuts
// the middle of a longer one, which drops sources and the closing marker.
test('a Perplexica result fits the tool-result cap as the real tool_call returns it', async () => {
  const { createPerplexicaResearchTool, researchOutputChars } = await import('../plugin/perplexica-research.mjs');
  const { o: setPluginToolMeta } = await import(pathToFileURL(join(dirname(file), 'tools-D5HS8Q_I.js')));
  const configured = {values: {preferences: {defaultChatProvider: 'c', defaultChatModel: 'm',
    defaultEmbeddingProvider: 'e', defaultEmbeddingModel: 'x'}}};
  const sources = Array.from({length: 25}, (_, i) => ({
    content: 'The festival runs from "October 3" to October 12 at venues across Center City.\n'.repeat(6),
    metadata: {title: `Event listing ${i + 1} "official" ${'T'.repeat(120)}`,
      url: `https://www.visitphilly.com/events/2026/event-slug-${i + 1}/?utm_source=search&x="q"`}}));
  const answer = Array.from({length: 40}, (_, i) =>
    `- **Event ${i}**: A "description" [${(i % 25) + 1}]. See https://invented-${i}.example.org/events/${i}/details.`).join('\n');
  for (const cap of [4000, 8000, 12000]) {
    let requests = 0;
    const research = createPerplexicaResearchTool({env: {},
      outputChars: () => researchOutputChars({agents: {list: [{id: 'pixel', contextLimits: {toolResultMaxChars: cap}}]}}, 'pixel'),
      fetch: async () => ++requests === 1 ? Response.json(configured) : new Response([{type: 'sources', data: sources},
        {type: 'response', data: answer}, {type: 'done'}].map(event => JSON.stringify(event)).join('\n'))});
    // As in production: the plugin tool OpenClaw builds from its cached
    // descriptor, labelled with its name and owned by the pixel-ods plugin.
    const catalogued = {...research, label: research.name};
    setPluginToolMeta(catalogued, {pluginId: 'pixel-ods'});
    const { catalogRef } = run([catalogued]);
    const ctx = { agentId: 'pixel', catalogRef, config: { tools: { toolSearch: { enabled: true, mode: 'tools' } } } };
    const call = createControls(ctx).find(t => t.name === 'tool_call');
    const delivered = await call.execute('research', { id: 'pixel_ods_research', args: { query: 'Philadelphia public events' } });
    assert.equal(requests, 2);
    assert.equal(delivered.content.length, 1);
    const text = delivered.content[0].text;
    assert.ok(text.length <= cap, `${cap}: ${text.length} characters`);
    const payload = JSON.parse(text);
    assert.equal(payload.tool.id, 'openclaw:pixel-ods:pixel_ods_research');
    assert.equal(payload.tool.label, 'pixel_ods_research');
    assert.equal(payload.tool.description, research.description);
    assert.equal(Object.hasOwn(payload.result.details, 'unsourcedLinkHosts'), false);
    const inner = payload.result.content[0].text;
    assert.match(inner, /\n<\/perplexica_evidence_[0-9a-f]{24}>$/);
    const evidence = JSON.parse(inner.slice(inner.indexOf('>\n') + 2, inner.lastIndexOf('\n</perplexica_evidence_')));
    assert.ok(JSON.stringify(evidence.answer).length >= 900, `${cap}: answer ${evidence.answer.length}`);
  }
});


test('document delivery is discoverable and callable through actual pinned ToolSearch with exact policy-bound schema',async()=>{
 const {ARTIFACT_TOOL,ARTIFACT_BOUNDARY,createWorkspaceArtifactTool,createWorkspaceArtifactAdmission}=await import('../plugin/workspace-artifact.mjs');
 const {createToolLoopGuard}=await import('../plugin/tool-loop-guard.mjs');
 const owner={trigger:'user',agentId:'pixel',runId:'artifact-run',sessionId:'artifact-session',sessionKey:'agent:pixel:openai-user:ods-'+ 'a'.repeat(64),toolCallId:'deliver'};
 const guard=createToolLoopGuard();guard.observeRun(owner,'pixel',{prompt:'Deliver report.md'});
 const admission=createWorkspaceArtifactAdmission();let requests=0;
 const receipt={schemaVersion:1,kind:'ods-pixel-workspace-artifact',relativePath:'project/report.md',siteId:'site-'+ 'a'.repeat(24),sha256:'a'.repeat(64),file:{path:'report.md',bytes:4,sha256:'b'.repeat(64)}};
 const artifact=createWorkspaceArtifactTool(owner,{admission,reserve:s=>guard.reserveWorkspaceArtifact(s),accept:(s,r)=>guard.acceptWorkspaceArtifact(s,r),request:async()=>{requests++;return {...receipt,status:'succeeded',httpStatus:200,readbackVerified:true,executable:false,overwritten:false,boundary:ARTIFACT_BOUNDARY}}});
 const result=run(filterByPolicy([artifact],{allow:[ARTIFACT_TOOL]}));
 assert.deepEqual(result.tools,controls,'document delivery stays a specialist, not another permanent prompt tool');
 const ctx={agentId:'pixel',catalogRef:result.catalogRef,config:{tools:{toolSearch:{enabled:true,mode:'tools'}}},executeTool:async params=>params.tool.execute(`tool_search_code:deliver:${ARTIFACT_TOOL}:1`,params.input)};
 const byName=Object.fromEntries(createControls(ctx).map(t=>[t.name,t]));
 const found=await byName.tool_search.execute('find',{query:'pixel_ods_workspace_artifact',limit:5});assert.ok(found.details.some(x=>x.name===ARTIFACT_TOOL));
 const described=await byName.tool_describe.execute('describe',{id:ARTIFACT_TOOL});assert.deepEqual(described.details.parameters,{type:'object',additionalProperties:false,required:['relativePath'],properties:{relativePath:{type:'string',minLength:1,maxLength:512},sourcePath:{type:'string',minLength:1,maxLength:512}}});
 const args={relativePath:'project/report.md'};
 // Live cloud regression: the model searched the tool but guessed `path`.
 // Native argument validation must name the required field before any
 // executor or broker work, just as it does for a directly offered tool.
 admission.before({toolName:'tool_call',params:{id:ARTIFACT_TOOL,args:{path:args.relativePath}}},owner);
 await assert.rejects(byName.tool_call.execute('deliver',{id:ARTIFACT_TOOL,args:{path:args.relativePath}}),/Validation failed[\s\S]*relativePath/);
 assert.equal(requests,0);
 const toolContext={...owner,trigger:undefined,toolName:'tool_call'};
 const admittedEvent={toolName:'tool_call',toolCallId:owner.toolCallId,params:{id:ARTIFACT_TOOL,args}};
 const decision=guard.beforeToolCall(admittedEvent,toolContext);
 admission.before(admittedEvent,toolContext,decision);
 const delivered=await byName.tool_call.execute('deliver',{id:ARTIFACT_TOOL,args});assert.equal(delivered.isError,undefined);assert.equal(requests,1);assert.deepEqual(guard.deliveryVerificationForRun(owner.runId).artifacts,[receipt]);
 admission.before({toolName:'tool_call',params:{id:ARTIFACT_TOOL,args:{relativePath:'../secret.pdf'}}},owner);
 await byName.tool_call.execute('deliver',{id:ARTIFACT_TOOL,args:{relativePath:'../secret.pdf'}});assert.equal(requests,1);
 const denied=run(filterByPolicy([artifact],{deny:[ARTIFACT_TOOL]}));assert.equal(resolveExact({...ctx,catalogRef:denied.catalogRef},ARTIFACT_TOOL),undefined);
});
