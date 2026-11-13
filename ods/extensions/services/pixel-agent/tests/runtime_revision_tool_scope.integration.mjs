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
for(const [name,digest] of Object.entries(vectors.reviewedReadDependencies??{}))
  assert.equal(createHash('sha256').update(readFileSync(path.join(sdk,'dist',name))).digest('hex'),digest,name);
const state=mkdtempSync(path.join(tmpdir(),'ods-sdk-tool-scope-'));
process.env.OPENCLAW_STATE_DIR=state;
process.env.OPENCLAW_CONFIG_PATH=path.join(state,'openclaw.json');
writeFileSync(process.env.OPENCLAW_CONFIG_PATH,'{}\n');
const scopeApi=await import(pathToFileURL(path.join(sdk,'dist/lifecycle-hook-helpers-Ck-WCTzd.js')));
const {odsAcquireRetryToolScope:acquire,odsCaptureRetryToolOrigins:capture,
  odsAcceptRetryToolsAllow:accept,odsAuthorizeRetryTool:authorize,
  odsFilterRetryToolScope:filter,odsRetryToolScopeVersion:version,
  odsRetryToolScopeState:scopeState,
  odsRetryToolChoiceVersion:choiceVersion,odsAcceptRetryToolChoice:acceptChoice,
  odsRetryToolChoiceState:choiceState,odsWrapRetryToolChoiceStream:choiceStream,
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

test('native admission metadata is immutable, owner-bound and truthful about active final tools',()=>{
  const w=world();
  assert.deepEqual(scopeState(w.scope,w.tools,w.getMeta),{version:1,ownerRunId:identity.runId,active:false,toolsAllow:null});
  accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);
  const scoped=w.narrowed();
  const proof=scopeState(w.scope,scoped,w.getMeta);
  assert.equal(proof.ownerRunId,identity.runId);
  assert.equal(proof.active,true);
  assert.deepEqual(proof.toolsAllow,['pixel_ods_notes','pixel_ods_mcp']);
  assert.equal(Object.isFrozen(proof),true);
  assert.equal(Object.isFrozen(proof.toolsAllow),true);
  assert.throws(()=>proof.toolsAllow.push('exec'));
  assert.equal(scopeState(w.scope,[...scoped,w.tool('exec',null)],w.getMeta),undefined);
  assert.throws(()=>w.narrowed());
});

test('cleared custody exposes no old proof; a fresh native object reports inactive instead of pretending to retain a cap',()=>{
  const old=world();accept(old.scope,['pixel_ods_notes','pixel_ods_mcp']);old.revoke();
  assert.equal(scopeState(old.scope,old.tools,old.getMeta),undefined);
  const fresh=world();
  assert.equal(scopeState(fresh.scope,fresh.tools,fresh.getMeta).active,false);
});

test('actual SDK admission receives native scope metadata without moving admission or altering model inputs',async()=>{
  const w=world();accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);
  const source=readFileSync(path.join(sdk,'dist/selection-BEwSQKM-.js'),'utf8');
  const from=source.indexOf('beforeRunResult = await hookRunner.runBeforeAgentRun({');
  const to=source.indexOf('}, hookCtx);',from)+'}, hookCtx);'.length;
  const promptHook=source.indexOf('await resolvePromptBuildHookResult(');
  const submission=source.indexOf('await promptActiveSession(',from);
  assert.ok(promptHook>0 && promptHook<from && from<submission);
  const history=[{role:'user',content:'Exact current request'}];
  let captured;
  const execute=vm.runInNewContext('(async function(){let beforeRunResult;'+source.slice(from,to)+'})',{
    hookRunner:{runBeforeAgentRun:async event=>{captured=event;return {decision:{outcome:'pass'}};}},
    odsRetryToolScopeState:scopeState,params:{odsRetryToolScope:w.scope},allCustomTools:w.narrowed(),getPluginToolMeta:w.getMeta,
    odsRetryToolChoiceState:choiceState,odsNativeRetryToolChoiceSupport:undefined,
    promptForModel:'Exact current prompt',systemPromptForHook:'Exact original bootstrap',beforeRunMessages:history,hookCtx:{...identity},
  });
  await execute();
  assert.equal(captured.prompt,'Exact current prompt');
  assert.equal(captured.systemPrompt,'Exact original bootstrap');
  assert.equal(captured.messages,history);
  assert.equal(captured.nativeRetryToolsAllowState.active,true);
  assert.deepEqual(captured.nativeRetryToolsAllowState.toolsAllow,['pixel_ods_notes','pixel_ods_mcp']);
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
    {odsRejectRetryToolScope:reject,odsAcceptRetryToolsAllow:accept,odsAcceptRetryToolChoice:acceptChoice});
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

const LOCAL_MODEL={api:'openai-completions',provider:'ods-gateway',id:'ods/current',name:'Owned fixture',
  baseUrl:'http://127.0.0.1:18091/v1',reasoning:false,input:['text'],contextWindow:65536,maxTokens:8192,
  cost:{input:0,output:0,cacheRead:0,cacheWrite:0}};
const STRATEGY='boundary-aware:openai-completions';
const {t:buildNativeCompletionsParams}=await import(pathToFileURL(path.join(sdk,'dist/openai-transport-stream-P3cLoEh2.js')));
function requiredWorld() {
  const w=world();accept(w.scope,['pixel_ods_notes','pixel_ods_mcp']);acceptChoice(w.scope,'required');
  const model={...LOCAL_MODEL};
  const tools=w.narrowed(),requests=[];
  const context={systemPrompt:'Unchanged bootstrap',messages:[{role:'user',content:'Current exact request',timestamp:0}],
    tools:tools.map(({name,description,parameters})=>({name,description,parameters}))};
  const underlying=async(model,ctx,options)=>{
    let payload=buildNativeCompletionsParams(model,ctx,options);
    const replacement=await options?.onPayload?.(payload,model);
    if(replacement!==undefined)payload=replacement;
    requests.push(payload);return {content:'Invented response'};
  };
  const wrapped=choiceStream(w.scope,underlying,choiceVersion(w.scope,model,STRATEGY),tools,w.getMeta,model);
  return {...w,model,tools,context,requests,underlying,wrapped};
}

test('required-choice capability is genuine local native completions only, independent of model prose',()=>{
  const w=world();assert.equal(choiceVersion(w.scope,LOCAL_MODEL,STRATEGY),1);
  for(const model of [{...LOCAL_MODEL,api:'anthropic-messages'},{...LOCAL_MODEL,provider:'foreign'},
    {...LOCAL_MODEL,id:'other'},{...LOCAL_MODEL,baseUrl:'https://api.example.invalid/v1'},
    {...LOCAL_MODEL,compat:{supportsTools:false}},
    {...LOCAL_MODEL,baseUrl:'http://127.0.0.1:18091/v1?override=x'}])
    assert.equal(choiceVersion(w.scope,model,STRATEGY),undefined);
  for(const strategy of [undefined,'provider','session-custom','stream-simple'])
    assert.equal(choiceVersion(w.scope,LOCAL_MODEL,strategy),undefined);
  w.revoke();assert.equal(choiceVersion(w.scope,LOCAL_MODEL,STRATEGY),undefined);
});

test('ordinary stream keeps exact context and options references and no required marker',async()=>{
  const w=world(),context={tools:w.tools},options={temperature:0.37},calls=[];
  const wrapped=choiceStream(w.scope,(...args)=>{calls.push(args);return 'ordinary';},1,w.tools,w.getMeta,LOCAL_MODEL);
  assert.equal(wrapped(LOCAL_MODEL,context,options),'ordinary');
  assert.equal(calls[0][1],context);assert.equal(calls[0][2],options);assert.equal(choiceState(w.scope,1),undefined);
});

test('real native parameter conversion emits required once; execute entry then permits auto without widening',async()=>{
  const w=requiredWorld(),original=JSON.stringify(w.context),options={maxTokens:8192,temperature:0.2};
  const armed=choiceState(w.scope,1);
  assert.deepEqual(armed,{version:1,ownerRunId:identity.runId,mode:'required-first',phase:'armed'});
  assert.equal(Object.isFrozen(armed),true);
  await w.wrapped(LOCAL_MODEL,w.context,options);
  assert.equal(w.requests[0].tool_choice,'required');assert.equal(w.scope.choice,'issued');
  assert.equal(choiceState(w.scope,1),undefined);
  await w.tools[0].execute('actual-native-attempt',{path:'fixture.md',text:'Exact line'});
  assert.equal(w.scope.choice,'tool-started');
  await w.wrapped(LOCAL_MODEL,w.context,options);
  assert.equal(w.requests[1].tool_choice,'auto');
  for(const body of w.requests)assert.deepEqual(body.tools.map(v=>v.function.name).sort(),['pixel_ods_mcp','pixel_ods_notes']);
  assert.deepEqual(w.requests[0].tools,w.requests[1].tools);assert.deepEqual(w.requests[0].messages,w.requests[1].messages);
  assert.equal(JSON.stringify(w.context),original);assert.deepEqual(options,{maxTokens:8192,temperature:0.2});
  acceptChoice(w.scope,'required');assert.equal(w.scope.choice,'tool-started');
  assert.equal(choiceState(w.scope,1).phase,'tool-started');
});

test('pre-issuance reconstruction retains armed; post-issuance outer reconstruction cannot send again',async()=>{
  const w=requiredWorld();assert.equal(acquire(w.owner,identity,()=>true),w.scope);
  assert.equal(choiceState(w.scope,1).phase,'armed');
  await w.wrapped(LOCAL_MODEL,w.context,{});
  const rebuilt=choiceStream(acquire(w.owner,identity,()=>true),w.underlying,1,w.tools,w.getMeta,LOCAL_MODEL);
  assert.throws(()=>rebuilt(LOCAL_MODEL,w.context,{}));assert.equal(w.requests.length,1);
  assert.equal(w.scope.closed,true);
});

test('prose, raw function text, incomplete arguments and provider failure do not consume or rearm',async()=>{
  for(const failure of ['prose','<tool_call>raw invented markup</tool_call>','schema-refused','provider-error']) {
    const w=requiredWorld();
    const wrapped=choiceStream(w.scope,()=>{if(failure==='provider-error')throw Error('Invented provider error');return failure;},1,w.tools,w.getMeta,LOCAL_MODEL);
    if(failure==='provider-error')assert.throws(()=>wrapped(LOCAL_MODEL,w.context,{}));
    else assert.equal(wrapped(LOCAL_MODEL,w.context,{}),failure);
    assert.equal(w.scope.choice,'issued');assert.equal(w.calls.length,0);
    assert.throws(()=>wrapped(LOCAL_MODEL,w.context,{}));assert.equal(w.scope.closed,true);
  }
});

test('a native refused or failed adapter attempt consumes required without claiming a backend effect',async()=>{
  const w=requiredWorld();w.tools[0]=w.tool('pixel_ods_notes');
  w.tools[0].execute=async()=>{throw Error('Invented ordinary admission refusal');};
  const narrowed=w.narrowed(w.tools);
  await w.wrapped(LOCAL_MODEL,w.context,{});
  await assert.rejects(()=>narrowed[0].execute('native-refused',{}));
  assert.equal(w.scope.choice,'tool-started');assert.equal(w.calls.length,0);
  await w.wrapped(LOCAL_MODEL,w.context,{});assert.equal(w.requests[1].tool_choice,'auto');
});

test('captured execution cannot consume armed choice without an issued stream or after cancellation',async()=>{
  const w=requiredWorld();await assert.rejects(()=>w.tools[0].execute('forged-early',{}));
  assert.equal(w.scope.choice,'armed');assert.equal(w.calls.length,0);assert.equal(w.scope.closed,true);
  const cancelled=requiredWorld();await cancelled.wrapped(LOCAL_MODEL,cancelled.context,{});cancelled.abort();
  await assert.rejects(()=>cancelled.tools[0].execute('stale',{}));assert.equal(cancelled.scope.choice,'issued');
  assert.equal(cancelled.calls.length,0);
});

test('post-issuance payload changes cannot drop required or widen the final native menu',async()=>{
  for(const mutate of [body=>{delete body.tool_choice;},body=>{body.tool_choice='auto';},
    body=>{body.tools.push({type:'function',function:{name:'exec'}});},body=>{body.tools=[];},
    body=>{body.tools[0].function.description='Tampered descriptor';},
    body=>{body.tools[0].function.parameters.properties={injected:{type:'string'}};}]) {
    const w=requiredWorld();
    await assert.rejects(()=>w.wrapped(LOCAL_MODEL,w.context,{onPayload:body=>{mutate(body);}}));
    assert.equal(w.requests.length,0);assert.equal(w.scope.closed,true);assert.equal(w.calls.length,0);
  }
});

test('accepted serialized schemas are detached from callback-owned aliases before submission',async()=>{
  const w=requiredWorld();let alias;
  await w.wrapped(LOCAL_MODEL,w.context,{onPayload:body=>{alias=body;return body;}});
  const original=JSON.stringify(w.requests[0].tools);
  alias.tools[0].function.description='Late callback mutation';
  alias.tools[0].function.parameters.properties={injected:{type:'string'}};
  alias.tool_choice='auto';alias.model='foreign';alias.tools=[];
  assert.equal(JSON.stringify(w.requests[0].tools),original);
  assert.equal(w.requests[0].tool_choice,'required');assert.equal(w.requests[0].model,'ods/current');
});

test('held native payload callback cannot submit after cancellation, context clear, closure or generation change',async()=>{
  for(const change of ['abort-signal','abort-owner','clear-context','close','generation','phase']) {
    const w=requiredWorld(),controller=new AbortController();let release,entered;
    const waiting=new Promise(resolve=>{entered=resolve;});
    const pending=w.wrapped(LOCAL_MODEL,w.context,{signal:controller.signal,
      onPayload:()=>new Promise(resolve=>{release=resolve;entered();})});
    await waiting;
    assert.equal(w.scope.choice,'issued');
    if(change==='abort-signal')controller.abort();
    if(change==='abort-owner')w.abort();
    if(change==='clear-context')w.revoke();
    if(change==='close')close(w.scope);
    if(change==='generation')w.owner.lifecycleGeneration='foreign-generation';
    if(change==='phase')await w.tools[0].execute('concurrent-native-attempt',{});
    release();await assert.rejects(()=>pending,undefined,change);
    assert.equal(w.requests.length,0,change);
    assert.equal(choiceState(w.scope,1),undefined,change);
  }
});

test('post-tool summary payload also rechecks current native custody after an awaited callback',async()=>{
  const w=requiredWorld();await w.wrapped(LOCAL_MODEL,w.context,{});
  await w.tools[0].execute('native-attempt',{});let release,entered;
  const waiting=new Promise(resolve=>{entered=resolve;});
  const pending=w.wrapped(LOCAL_MODEL,w.context,{onPayload:()=>new Promise(resolve=>{release=resolve;entered();})});
  await waiting;w.revoke();release();await assert.rejects(()=>pending);
  assert.equal(w.requests.length,1);assert.equal(w.requests[0].tool_choice,'required');
});

test('payload callbacks cannot change captured model identity, local route or tool-support profile',async()=>{
  for(const [key,value] of [['id','foreign'],['api','openai-responses'],['provider','foreign'],
    ['baseUrl','http://127.0.0.1:18092/v1'],['compat',{supportsTools:false}]]) {
    const w=requiredWorld();let release,entered;
    const waiting=new Promise(resolve=>{entered=resolve;});
    const pending=w.wrapped(w.model,w.context,{onPayload:body=>new Promise(resolve=>{
      release=()=>{body.model=w.model.id;resolve(body);};entered();
    })});
    await waiting;w.model[key]=value;release();await assert.rejects(()=>pending,undefined,key);
    assert.equal(w.requests.length,0,key);assert.equal(w.calls.length,0,key);
  }
  const constructed=requiredWorld(),callCopy={...constructed.model};
  await assert.rejects(()=>constructed.wrapped(callCopy,constructed.context,{onPayload:body=>{
    constructed.model.baseUrl='http://127.0.0.1:18092/v1';return body;
  }}));
  assert.equal(constructed.requests.length,0);
});

test('native supplied callback model must match the original route; critical accessors are rejected',async()=>{
  const w=requiredWorld();
  const forged=choiceStream(w.scope,(model,ctx,options)=>options.onPayload(
    buildNativeCompletionsParams(model,ctx,options),{...model,id:'foreign'}),1,w.tools,w.getMeta,w.model);
  await assert.rejects(()=>forged(w.model,w.context,{}));assert.equal(w.requests.length,0);
  for(const key of ['model','tool_choice','tools']) {
    const current=requiredWorld();
    await assert.rejects(()=>current.wrapped(current.model,current.context,{onPayload:body=>{
      const value=body[key];Object.defineProperty(body,key,{enumerable:true,get:()=>value});return body;
    }}));
    assert.equal(current.requests.length,0,key);
  }
});

test('an awaited callback cannot replace the original cancellation authority',async()=>{
  const w=requiredWorld(),controller=new AbortController();let release,entered;
  const waiting=new Promise(resolve=>{entered=resolve;});
  const options={signal:controller.signal,onPayload:()=>new Promise(resolve=>{release=resolve;entered();})};
  const pending=w.wrapped(w.model,w.context,options);
  await waiting;controller.abort();options.signal=new AbortController().signal;release();
  await assert.rejects(()=>pending);assert.equal(w.requests.length,0);
});

test('actual native OpenAI client serialization cannot invoke a callback-owned toJSON override',async()=>{
  const {default:OpenAI}=await import(pathToFileURL(path.join(sdk,'node_modules/openai/index.mjs')));
  const sent=[];
  const client=new OpenAI({apiKey:'invented-fixture-only',baseURL:LOCAL_MODEL.baseUrl,maxRetries:0,
    fetch:async(_url,init)=>{
      sent.push(JSON.parse(init.body));
      return new Response('data: [DONE]\n\n',{status:200,headers:{'content-type':'text/event-stream'}});
    }});
  const send=async body=>{for await(const _ of await client.chat.completions.create(body)){ /* Invented local transport only. */ }};
  const w=requiredWorld();
  const override=body=>{body.toJSON=()=>({...body,model:'foreign',tool_choice:'auto',tools:[]});return body;};
  // Establish the real serializer counterexample without executing any tool.
  await send(override(buildNativeCompletionsParams(w.model,w.context,{toolChoice:'required'})));
  assert.equal(sent[0].model,'foreign');assert.equal(sent[0].tool_choice,'auto');assert.deepEqual(sent[0].tools,[]);
  const guarded=choiceStream(w.scope,async(model,ctx,options)=>{
    const raw=buildNativeCompletionsParams(model,ctx,options);
    const replacement=await options.onPayload(raw,model);
    await send(replacement===undefined?raw:replacement);
  },1,w.tools,w.getMeta,w.model);
  await assert.rejects(()=>guarded(w.model,w.context,{onPayload:override}));
  assert.equal(sent.length,1);assert.equal(w.calls.length,0);
});

test('explicit native none or named restrictions never become a broader required/auto request',async()=>{
  for(const explicit of ['none',{type:'function',function:{name:'pixel_ods_notes'}}]) {
    const armed=requiredWorld();assert.throws(()=>armed.wrapped(LOCAL_MODEL,armed.context,{toolChoice:explicit}));
    assert.equal(armed.requests.length,0);assert.equal(armed.scope.choice,'armed');
    const started=requiredWorld();await started.wrapped(LOCAL_MODEL,started.context,{});
    await started.tools[0].execute('native-attempt',{});
    assert.throws(()=>started.wrapped(LOCAL_MODEL,started.context,{toolChoice:explicit}));
    assert.equal(started.requests.length,1);assert.equal(started.scope.choice,'tool-started');
  }
  const started=requiredWorld();await started.wrapped(LOCAL_MODEL,started.context,{});
  await started.tools[0].execute('native-attempt',{});
  assert.throws(()=>started.wrapped(LOCAL_MODEL,started.context,{toolChoice:'required'}));
  assert.equal(started.requests.length,1);
});

test('unsupported transports, changed tools or foreign model cannot silently degrade required',()=>{
  const w=requiredWorld();assert.throws(()=>choiceStream(w.scope,w.underlying,undefined,w.tools,w.getMeta,LOCAL_MODEL));
  const changed=requiredWorld();assert.throws(()=>changed.wrapped(LOCAL_MODEL,{...changed.context,tools:[]},{}));
  const foreign=requiredWorld();assert.throws(()=>foreign.wrapped({...LOCAL_MODEL,api:'openai-responses'},foreign.context,{}));
  for(const item of [w,changed,foreign]){assert.equal(item.calls.length,0);assert.equal(item.requests.length,0);}
});

test('actual normalizer preserves required across budget candidates and rejects malformed or missing caps',async()=>{
  const accepted=await normalized({action:'revise',retry:{instruction:'Same instruction',
    toolsAllow:['pixel_ods_notes','pixel_ods_mcp'],toolChoice:'required'}},'required-valid');
  assert.equal(accepted.revisionToolChoiceRequired,true);
  for(const [index,choice] of [undefined,null,'auto','none',{},'REQUIRED'].entries()) {
    const invalid=await normalized({action:'revise',retry:{instruction:'Same instruction',
      toolsAllow:['pixel_ods_notes','pixel_ods_mcp'],toolChoice:choice}},'required-invalid-'+index);
    assert.equal(invalid.revisionToolsInvalid,true);assert.equal(invalid.action,'finalize');
  }
  for(const [index,toolsAllow] of [undefined,['pixel_ods_notes'],['exec','pixel_ods_notes']].entries()) {
    const invalid=await normalized({action:'revise',retry:{instruction:'Same instruction',toolsAllow,toolChoice:'required'}},'required-cap-'+index);
    assert.equal(invalid.revisionToolsInvalid,true);
  }
  const once={action:'revise',retry:{instruction:'Same instruction',idempotencyKey:'required-once',maxAttempts:1,
    toolsAllow:['pixel_ods_notes','pixel_ods_mcp'],toolChoice:'required'}};
  await normalized(once,'required-exhausted');
  const exhausted=await normalized(once,'required-exhausted');
  assert.equal(exhausted.action,'continue');assert.equal(exhausted.revisionToolChoiceRequired,true);
});
