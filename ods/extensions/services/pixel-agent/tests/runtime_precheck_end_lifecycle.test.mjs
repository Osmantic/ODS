import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Execute the exact native recipe blocks, including the outer finally flush.
// The real gateway/provider integration separately proves their call ordering.
const recipe=name=>JSON.parse(readFileSync(new URL(`../host/openclaw-${name}.json`,import.meta.url)));
const attempt=recipe('compaction-budget').replacements.find(([before])=>before.includes('if (!beforeAgentFinalizeRevisionReason) runAgentEndSideEffects'));
assert.ok(attempt);
const outer=recipe('yield-usage').replacements;
const setter=outer.find(([before])=>before.includes('const rawAttempt = await runEmbeddedAttemptWithBackend'))[1]
  .match(/deferPreflightAgentEnd: ([^\n]+),/)[1];
const flush=outer.find(([before])=>before.startsWith('\t\t\t} finally {'))[1];
const flushBody=flush.slice(flush.indexOf('const emitPreflightFailure'),flush.indexOf('\n\t\t\t\tif (params.isFinalFallbackAttempt'));

function operation({callback=true,source=process.env.ODS_PRECHECK_BASELINE==='1'?attempt[0]:attempt[1]}={}) {
  const emitted=[];
  const context=vm.createContext({emitted,Date:{now:()=>200},runAgentEndSideEffects:value=>emitted.push(value),
    formatErrorMessage:error=>error.message,freezeDiagnosticTraceContext:value=>value,
    buildAgentHookContextChannelFields:()=>({}),buildAgentHookContextIdentityFields:()=>({})});
  vm.runInContext('let deferredPreflightAgentEnd; globalThis.defer = '+setter+';',context);
  function end(overrides={}) {
    Object.assign(context,{beforeAgentFinalizeRevisionReason:undefined,messagesSnapshot:[{role:'assistant',stopReason:'stop'}],
      aborted:false,promptError:new Error('native initial context precheck'),promptErrorSource:'precheck',
      preflightRecovery:{route:'compact_only'},promptStartedAt:100,diagnosticTrace:{fixture:true},hookAgentId:'pixel',hookRunner:{},
      params:{runId:'owned-run',sessionId:'owned-session',sessionKey:'owned-key',...(callback?{deferPreflightAgentEnd:context.defer}:{})},...overrides});
    vm.runInContext(source,context);
  }
  return {emitted,end,flush:()=>vm.runInContext('{'+flushBody+'}',context),context};
}

for(const route of ['compact_only','compact_then_truncate'])test(`initial ${route} precheck waits for recovery, then successful end replaces it`,()=>{
  const run=operation();run.end({preflightRecovery:{route}});assert.equal(run.emitted.length,0);
  run.end({promptError:null,promptErrorSource:null,preflightRecovery:undefined});
  run.flush();assert.equal(run.emitted.length,1);assert.equal(run.emitted[0].event.success,true);
  assert.equal(run.emitted[0].ctx.runId,'owned-run');
});

test('terminal recovery exit flushes the original failed end once',()=>{
  const run=operation();run.end();assert.equal(run.emitted.length,0);
  run.context.promptError=new Error('later incidental error');run.context.Date.now=()=>900;
  run.flush();run.flush();assert.equal(run.emitted.length,1);
  assert.equal(run.emitted[0].event.success,false);assert.equal(run.emitted[0].event.error,'native initial context precheck');
  assert.equal(run.emitted[0].event.durationMs,100,'snapshot remains bound to the failed native attempt');
});

test('multiple recoverable prechecks retain only the latest failed attempt if recovery never settles',()=>{
  const run=operation();run.end();run.end({promptError:new Error('latest precheck')});run.flush();
  assert.equal(run.emitted.length,1);assert.equal(run.emitted[0].event.error,'latest precheck');
});

for(const [name,overrides] of [
  ['real abort',{aborted:true}],
  ['provider exception',{promptErrorSource:'prompt'}],
  ['compaction exception',{promptErrorSource:'compaction'}],
  ['unrelated tool-policy precheck',{preflightRecovery:undefined}],
  ['mid-turn recovery',{preflightRecovery:{route:'compact_only',source:'mid-turn'}}],
  ['unknown recovery source',{preflightRecovery:{route:'compact_only',source:'unknown'}}],
  ['unknown recovery route',{preflightRecovery:{route:'unknown'}}],
])test(`${name} remains immediate and supersedes any pending failed precheck`,()=>{
  const run=operation();run.end();assert.equal(run.emitted.length,0);
  run.end(overrides);assert.equal(run.emitted.length,1);assert.equal(run.emitted[0].event.success,false);
  run.flush();assert.equal(run.emitted.length,1);
});

test('callers without the outer recovery callback retain the existing immediate error',()=>{
  const run=operation({callback:false});run.end();assert.equal(run.emitted.length,1);run.flush();assert.equal(run.emitted.length,1);
});
test('native provider-error assistant keeps the original terminal message for failure guards',()=>{
  const run=operation();run.end();run.end({promptError:null,promptErrorSource:'prompt',messagesSnapshot:[{role:'assistant',stopReason:'error'}]});
  assert.equal(run.emitted.length,1);assert.equal(run.emitted[0].event.messages[0].stopReason,'error');run.flush();assert.equal(run.emitted.length,1);
});
test('revision attempts do not invent completion or clear a pending failure',()=>{
  const run=operation();run.end();run.end({beforeAgentFinalizeRevisionReason:'review required',promptError:null});
  assert.equal(run.emitted.length,0);run.flush();assert.equal(run.emitted[0].event.success,false);
});
test('separate outer runs never share deferred events or owner identity',()=>{
  const first=operation(),second=operation();first.end();second.end({promptError:null,promptErrorSource:null});
  second.flush();assert.equal(first.emitted.length,0);assert.equal(second.emitted.length,1);
  first.flush();assert.equal(first.emitted.length,1);assert.equal(first.emitted[0].event.success,false);
});

test('native hook context values and field order are unchanged by deferring the event',()=>{
  const before=operation({callback:false,source:attempt[0]}),after=operation({callback:false,source:attempt[1]});
  const params={runId:'native-run',sessionId:'native-session',sessionKey:'native-key',workspaceDir:'/fixture/workspace',
    trigger:'subagent',senderId:'fixture-sender',chatId:'fixture-chat',channelContext:{kind:'fixture'},
    config:{fixture:true},messageChannel:'fixture-channel'};
  for(const run of [before,after]){
    run.context.buildAgentHookContextChannelFields=value=>({messageChannel:value.messageChannel});
    // Native identity resolution does not need to return trigger. The explicit
    // top-level trigger must survive independently of that helper's output.
    run.context.buildAgentHookContextIdentityFields=value=>({senderId:value.senderId,chatId:value.chatId,channelContext:value.channelContext});
    run.end({params,promptError:null,promptErrorSource:null,preflightRecovery:undefined});
  }
  const original=before.emitted[0].ctx,candidate=after.emitted[0].ctx;
  assert.equal(JSON.stringify(candidate),JSON.stringify(original),'all values and object field order match the original native emission');
  assert.equal(candidate.trigger,'subagent');assert.equal(candidate.senderId,params.senderId);assert.equal(candidate.chatId,params.chatId);
  assert.equal(candidate.channelContext,params.channelContext);assert.equal(candidate.messageChannel,params.messageChannel);
  assert.equal(candidate.config,params.config);
});
