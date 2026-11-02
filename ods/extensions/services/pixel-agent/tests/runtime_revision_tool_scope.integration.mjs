// Run against a fresh, privately repaired pinned SDK; never a live installation.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,mkdtempSync,writeFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import vm from 'node:vm';

const sdk=process.env.ODS_REVIEW_TOOL_SCOPE_SDK;
const vectorFile=process.env.ODS_REVIEW_TOOL_SCOPE_VECTORS;
assert.ok(sdk && vectorFile,'Select the private pinned SDK and reviewed vector artifact explicitly.');
const vectors=JSON.parse(readFileSync(vectorFile,'utf8'));
for(const [name,value] of Object.entries(vectors.modules))
  assert.equal(createHash('sha256').update(readFileSync(path.join(sdk,'dist',name))).digest('hex'),value.patchedSha256,name);
const state=mkdtempSync(path.join(tmpdir(),'ods-sdk-tool-scope-'));
process.env.OPENCLAW_STATE_DIR=state;
process.env.OPENCLAW_CONFIG_PATH=path.join(state,'openclaw.json');
writeFileSync(process.env.OPENCLAW_CONFIG_PATH,'{}\n');
const scopeApi=await import(pathToFileURL(path.join(sdk,'dist/lifecycle-hook-helpers-Ck-WCTzd.js')));
const {odsAcquireRetryToolScope:acquire,odsCaptureRetryToolOrigins:capture,
  odsAcceptRetryToolsAllow:accept,odsAuthorizeRetryTool:authorize,
  odsFilterRetryToolScope:filter,odsRetryToolScopeVersion:version,
  odsTransferRetryToolDefinitions:transfer,odsRejectRetryToolScope:reject,
  odsCloseRetryToolScope:close}=scopeApi;
const {t:splitSdkTools}=await import(pathToFileURL(path.join(sdk,'dist/tool-split-C61cf48Y.js')));
const catalogApi=await import(pathToFileURL(path.join(sdk,'dist/tool-search-BInRpkE3.js')));

const identity={runId:'owned-run',sessionId:'owned-session',sessionKey:'agent:pixel:owned',agentId:'pixel',lifecycleGeneration:'owned-generation'};
function world() {
  const owner={sessionId:identity.sessionId,sessionKey:identity.sessionKey,lifecycleGeneration:identity.lifecycleGeneration};
  let current=owner,aborted=false;
  const scope=acquire(owner,identity,()=>current===owner && !aborted);
  const metadata=new WeakMap();
  const calls=[];
  function tool(name,pluginId='pixel-ods') {
    const value={name,description:name,parameters:{type:'object'},execute:async(...args)=>{calls.push({name,args});return name;}};
    if(pluginId!==null)metadata.set(value,{pluginId,optional:false});
    return value;
  }
  const getMeta=value=>metadata.get(value);
  const copyMeta=(source,target)=>{if(metadata.has(source))metadata.set(target,metadata.get(source));};
  const tools=[tool('exec',null),tool('pixel_ods_notes'),tool('pixel_ods_mcp'),tool('pixel_ods_notify'),tool('tool_search',null)];
  capture(scope,tools,getMeta);
  return {scope,owner,tools,tool,getMeta,copyMeta,calls,
    abort:()=>{aborted=true;},revoke:()=>{current={...owner};},
    narrowed:values=>filter(scope,values??tools,getMeta,copyMeta,()=>!aborted)};
}

test('normal first pass returns the exact array and objects without wrappers or grants',async()=>{
  const w=world();
  assert.equal(version(w.scope),1);
  assert.equal(w.narrowed(),w.tools);
  assert.equal(w.scope.cap,null);
  await w.tools[0].execute('ordinary',{});
  assert.equal(w.calls.length,1);
});

test('corrective set is a native subset with original metadata and unchanged arguments',async()=>{
  const w=world(),cap=['pixel_ods_notes','pixel_ods_mcp'];
  accept(w.scope,cap);cap.push('exec');
  const narrowed=w.narrowed();
  assert.deepEqual(narrowed.map(value=>value.name),['pixel_ods_notes','pixel_ods_mcp']);
  assert.equal(w.getMeta(narrowed[0]).pluginId,'pixel-ods');
  const args={path:'invented.md',text:'Current owned line'};
  await narrowed[0].execute('call',args);
  assert.equal(w.calls[0].args[1],args);
  assert.equal(w.calls.length,1);
});

for(const bad of [undefined,null,[],['*'],['group:all'],['notes/mcp'],['pixel_ods_notes','pixel_ods_notes'],
  ['unknown'],['pixel_ods_notes','unknown'],Array.from({length:33},(_,index)=>'a'+index)])
  test('invalid/empty/unknown cap refuses without broader fallback: '+JSON.stringify(bad),()=>{
    const w=world();assert.throws(()=>accept(w.scope,bad));assert.equal(w.scope.cap,null);assert.equal(w.calls.length,0);
    assert.throws(()=>acquire(w.owner,identity,()=>true));
    assert.throws(()=>filter(w.scope,w.tools,w.getMeta,w.copyMeta,()=>true));
    assert.throws(()=>authorize(w.scope,w.tools[0],w.getMeta,()=>true));
    assert.equal(version(w.scope),undefined);
  });

test('origin admission cannot be widened by later policy/factory additions',()=>{
  const w=world();
  const later=[...w.tools,w.tool('newly_allowed')];
  capture(w.scope,later,w.getMeta);
  assert.throws(()=>accept(w.scope,['newly_allowed']));
  const current=world();accept(current.scope,['pixel_ods_notes','pixel_ods_mcp']);
  assert.throws(()=>accept(current.scope,['pixel_ods_notes','newly_allowed']));
  assert.deepEqual(current.scope.cap,['pixel_ods_notes','pixel_ods_mcp']);
  assert.throws(()=>current.narrowed());
});

test('legitimate rebuilt functions keep stable native provenance; substituted/ambiguous owners refuse',()=>{
  const w=world();accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);
  assert.equal(w.narrowed([w.tool('pixel_ods_notes'),w.tool('pixel_ods_mcp')]).length,2);
  assert.throws(()=>w.narrowed([w.tool('pixel_ods_notes','other-plugin'),w.tool('pixel_ods_mcp')]));
  assert.throws(()=>w.narrowed([w.tool('pixel_ods_notes'),w.tool('pixel_ods_notes'),w.tool('pixel_ods_mcp')]));
  assert.throws(()=>w.narrowed([w.tool('pixel_ods_notes')]));
});

test('same native context retains the cap across a fresh outer fallback; no registry by free-form run id',()=>{
  const w=world();accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);
  const fallback=acquire(w.owner,{...identity},()=>true);
  assert.equal(fallback,w.scope);
  assert.deepEqual(fallback.cap,['pixel_ods_notes','pixel_ods_mcp']);
  assert.throws(()=>acquire(w.owner,{...identity,sessionKey:'other-session'},()=>true));
  const otherOwner={...w.owner};
  const other=acquire(otherOwner,{...identity},()=>true);
  assert.notEqual(other,w.scope);
  assert.equal(other.origin,null);
});

test('native lifecycle replacement cannot reset an active cap or create a broader future fallback',()=>{
  const w=world();accept(w.scope,['pixel_ods_notes']);
  const replacement={...w.owner,lifecycleGeneration:'new-generation'};
  const changed={...identity,lifecycleGeneration:'new-generation'};
  assert.throws(()=>acquire(replacement,changed,()=>true,w.owner));
  assert.throws(()=>acquire(replacement,changed,()=>true));
  assert.equal(version(w.scope),undefined);
  const ordinary=world(),next={...ordinary.owner,lifecycleGeneration:'new-generation'};
  assert.equal(acquire(next,changed,()=>true,ordinary.owner),undefined);
  assert.equal(acquire(next,changed,()=>true),undefined);
});

test('native source kind cannot collide with a plugin whose id happens to be core',()=>{
  const w=world();accept(w.scope,['exec']);
  assert.throws(()=>w.narrowed([w.tool('exec','core')]));
});

test('an originally ambiguous flat name never gains either plugin authority',()=>{
  const owner={sessionId:identity.sessionId,sessionKey:identity.sessionKey,lifecycleGeneration:identity.lifecycleGeneration};
  const scope=acquire(owner,{...identity},()=>true);
  const w=world();capture(scope,[w.tool('same','first'),w.tool('same','second')],w.getMeta);
  assert.throws(()=>accept(scope,['same']));
  assert.throws(()=>acquire(owner,identity,()=>true));
});

test('later caps meet monotonically; disjoint/unknown caps cannot reset or union',()=>{
  const w=world();accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);
  accept(w.scope,['pixel_ods_notes']);
  accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);
  assert.deepEqual(w.scope.cap,['pixel_ods_notes']);
  assert.throws(()=>accept(w.scope,['pixel_ods_mcp']));
  assert.deepEqual(w.scope.cap,['pixel_ods_notes']);
});

for(const end of ['abort','revoke','close','generation'])test('captured direct/deferred closures refuse after '+end,async()=>{
  const w=world();accept(w.scope,['pixel_ods_notes']);
  const narrowed=w.narrowed();
  if(end==='abort')w.abort();
  if(end==='revoke')w.revoke();
  if(end==='close')close(w.scope);
  if(end==='generation')w.owner.lifecycleGeneration='replacement-generation';
  await assert.rejects(narrowed[0].execute('late',{}));
  assert.throws(()=>authorize(w.scope,w.tools[1],w.getMeta,()=>true));
  assert.equal(version(w.scope),undefined);
  assert.equal(w.calls.length,0);
});

test('opaque native scope cannot be forged or recreated after terminal closure',()=>{
  const w=world();
  assert.throws(()=>accept({...w.scope},['pixel_ods_notes']));
  accept(w.scope,['pixel_ods_notes']);
  close(w.scope);
  assert.throws(()=>acquire(w.owner,identity,()=>true));
  assert.equal(version(w.scope),undefined);
});

test('uncapped native identity rollover leaves the ordinary path unchanged but withdraws capability acknowledgment',()=>{
  const w=world();w.owner.sessionId='new-native-session';
  assert.equal(acquire(w.owner,{...identity,sessionId:'new-native-session'},()=>true),undefined);
  assert.equal(version(w.scope),undefined);
  assert.equal(filter(w.scope,w.tools,w.getMeta,w.copyMeta,()=>true),w.tools);
});

test('actual pinned splitSdkTools projection retains verified lineage and final client/forced additions cannot escape',async()=>{
  const w=world();accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);
  const capped=w.narrowed();
  const {customTools}=splitSdkTools({tools:capped});
  assert.equal(w.getMeta(customTools[0]),undefined); // Real adapter drops metadata.
  transfer(w.scope,capped,customTools,w.getMeta,w.copyMeta);
  const final=w.narrowed([...customTools,w.tool('message'),w.tool('client_tool','client')]);
  assert.deepEqual(final.map(value=>value.name),['pixel_ods_notes','pixel_ods_mcp']);
  await final[0].execute('native-projection',{path:'owned.md'});
  assert.equal(w.calls.length,1);
  const forged=splitSdkTools({tools:capped}).customTools;
  forged[0]={...forged[0],parameters:{...forged[0].parameters}};
  assert.throws(()=>transfer(w.scope,capped,forged,w.getMeta,w.copyMeta));
});

test('actual selection clear path retires a broad ToolSearch catalog before direct-only correction',()=>{
  const w=world();
  const config={tools:{toolSearch:{enabled:true,mode:'tools'}}};
  const catalogRef=catalogApi.m();
  const context={...identity,config,catalogRef};
  catalogApi.u({...context,tools:w.tools,toolHookContext:context});
  assert.ok(catalogApi.v(context,'exec'));
  accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);
  const source=readFileSync(path.join(sdk,'dist/selection-BEwSQKM-.js'),'utf8');
  const start=source.indexOf('\t\tconst odsScopedRetry =');
  const end=source.indexOf('\n\t\tconst toolSearch =',start);
  assert.ok(start>=0 && end>start);
  vm.runInNewContext(source.slice(start,end),{
    params:{...identity,odsRetryToolScope:w.scope},sandboxSessionKey:identity.sessionKey,
    sessionAgentId:'pixel',toolSearchCatalogRef:catalogRef,clearToolSearchCatalog:catalogApi.f,
  });
  assert.equal(catalogApi.v(context,'exec'),undefined);
  assert.equal(catalogApi.v(context,'pixel_ods_notify'),undefined);
  assert.deepEqual(w.narrowed().map(value=>value.name),['pixel_ods_notes','pixel_ods_mcp']);
});

async function normalized(result,runId) {
  return await scopeApi.i({event:{runId,sessionId:'owned-session',stopHookActive:false},ctx:{...identity,runId},
    hookRunner:{hasHooks:()=>true,runBeforeAgentFinalize:async()=>result}});
}

test('actual native hook normalizer meets all candidate caps before budget choice and preserves ordinary retries',async()=>{
  const ordinary=await normalized({action:'revise',retry:{instruction:'Ordinary native retry',maxAttempts:1}},'ordinary-normalizer');
  assert.deepEqual(ordinary,{action:'revise',reason:'Ordinary native retry'});
  const result=await normalized({action:'revise',retryCandidates:[
    {instruction:'First unscoped native retry',idempotencyKey:'first',maxAttempts:1},
    {instruction:'Scoped native retry',toolsAllow:['pixel_ods_notes','pixel_ods_mcp']},
    {instruction:'Narrower native retry',toolsAllow:['pixel_ods_notes']},
  ]},'candidate-cap-meet');
  assert.equal(result.action,'revise');
  assert.deepEqual(result.revisionToolsAllow,['pixel_ods_notes']);
  assert.equal(result.reason,'First unscoped native retry');
});

test('a malformed or disjoint candidate terminates instead of selecting a broader candidate',async()=>{
  for(const [index,bad] of [[],undefined,['*'],['group:all']].entries()) {
    const result=await normalized({action:'revise',retryCandidates:[
      {instruction:'Unscoped alternative'}, {instruction:'Invalid scoped retry',toolsAllow:bad},
    ]},'bad-candidate-'+index);
    assert.equal(result.action,'finalize');
    assert.equal(result.revisionToolsInvalid,true);
  }
  const disjoint=await normalized({action:'revise',retryCandidates:[
    {instruction:'Notes',toolsAllow:['pixel_ods_notes']},{instruction:'MCP',toolsAllow:['pixel_ods_mcp']},
  ]},'disjoint-candidate');
  assert.equal(disjoint.action,'finalize');
});

test('actual hook normalization and actual selection decision poison malformed scope before a fresh outer fallback',async()=>{
  const w=world();
  const outcome=await normalized({action:'revise',retry:{instruction:'Malformed scope',toolsAllow:[]}},'malformed-chain');
  const source=readFileSync(path.join(sdk,'dist/selection-BEwSQKM-.js'),'utf8');
  const start=source.indexOf('\t\t\t\tif (outcome.revisionToolsInvalid === true)');
  const end=source.indexOf('\n\t\t\t\tif (event.hadDeterministicSideEffect)',start);
  assert.ok(start>=0 && end>start);
  const execute=vm.runInNewContext('(function(outcome,params){'+source.slice(start,end)+'})',
    {odsRejectRetryToolScope:reject,odsAcceptRetryToolsAllow:accept});
  assert.throws(()=>execute(outcome,{odsRetryToolScope:w.scope}));
  assert.equal(w.scope.restricted,true);
  assert.equal(w.scope.closed,true);
  assert.throws(()=>acquire(w.owner,identity,()=>true));
  assert.throws(()=>authorize(w.scope,w.tools[0],w.getMeta,()=>true));
  assert.equal(w.calls.length,0);
});

test('an exhausted scoped candidate still cannot widen an eligible unscoped alternative',async()=>{
  const result={action:'revise',retryCandidates:[
    {instruction:'Scoped once',idempotencyKey:'once',maxAttempts:1,toolsAllow:['pixel_ods_notes']},
    {instruction:'Unscoped alternative',idempotencyKey:'alternative',maxAttempts:3},
  ]};
  assert.equal((await normalized(result,'exhausted-cap')).action,'revise');
  const second=await normalized(result,'exhausted-cap');
  assert.equal(second.reason,'Unscoped alternative');
  assert.deepEqual(second.revisionToolsAllow,['pixel_ods_notes']);
});

test('an entirely exhausted retry retains the restrictive native marker before action dispatch',async()=>{
  const result={action:'revise',retry:{instruction:'One scoped retry',idempotencyKey:'once',maxAttempts:1,toolsAllow:['pixel_ods_notes']}};
  await normalized(result,'all-exhausted');
  const exhausted=await normalized(result,'all-exhausted');
  assert.equal(exhausted.action,'continue');
  assert.deepEqual(exhausted.revisionToolsAllow,['pixel_ods_notes']);
});
