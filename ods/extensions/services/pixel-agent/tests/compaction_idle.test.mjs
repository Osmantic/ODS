// Qualifies the reviewed SDK RPC without changing the installed package and
// without invoking a model, touching a real transcript, or aborting any run.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import {createHash} from 'node:crypto';
const manifest=JSON.parse(fs.readFileSync(new URL('../host/openclaw-compaction-idle.json',import.meta.url)));
const runtime=process.env.PIXEL_OPENCLAW_RUNTIME;
const sha=value=>createHash('sha256').update(value).digest('hex');
const helper=manifest.replacements[0][1].replace(manifest.replacements[0][0],'');

test('idle compaction guard never aborts, including native pre-admission runs',()=>{
  for(const tracked of [false,true]) for(const embedded of [false,true]) {
    const scope=vm.createContext({hasTrackedActiveSessionRun:()=>tracked,isEmbeddedAgentRunActive:()=>embedded,
      resolveDefaultAgentId:()=> 'pixel',errorShape:(code,message)=>({code,message}),ErrorCodes:{UNAVAILABLE:'UNAVAILABLE'}});
    vm.runInContext(`${helper};globalThis.guard=refuseActiveSessionCompaction;`,scope);
    const result=scope.guard({sessionId:'session-id',context:{getRuntimeConfig:()=>({})}});
    assert.equal(result.interrupted,false);assert.equal(Boolean(result.error),tracked||embedded);
  }
  assert.doesNotMatch(helper,/abortEmbeddedAgentRun|chat\.abort|clearSessionQueues|waitForEmbeddedAgentRunEnd/);
});

test('reviewed installed RPC refuses active races and still summarizes idle sessions',{skip:!runtime},async t=>{
  const pkg=JSON.parse(fs.readFileSync(path.join(runtime,'package.json')));
  assert.equal(pkg.name,'openclaw');assert.equal(pkg.version,manifest.version);
  let source=fs.readFileSync(path.join(runtime,'dist/sessions-KE_Xmzwf.js'),'utf8');
  assert.ok([manifest.sourceSha256,manifest.patchedSha256].includes(sha(source)));
  if(sha(source)===manifest.patchedSha256) for(const [old,replacement] of [...manifest.replacements].reverse())source=source.replace(replacement,old);
  assert.equal(sha(source),manifest.sourceSha256);
  const original=source;
  for(const [old,replacement] of manifest.replacements){assert.equal(source.split(old).length,2);source=source.replace(old,replacement);}
  assert.equal(sha(source),manifest.patchedSha256);
  const before=original.slice(0,original.indexOf('\t"sessions.compact":')).replace(manifest.replacements[0][0],manifest.replacements[0][1]);
  assert.equal(source.slice(0,source.indexOf('\t"sessions.compact":')),before,'other RPC handlers preserve their interruption behavior');
  const handlerSource=source.slice(source.indexOf('\t"sessions.compact":'),source.lastIndexOf('\n};'));
  assert.doesNotMatch(handlerSource,/interruptSessionRunIfActive|abortEmbeddedAgentRun|chat\.abort/);
  function harness({active=false,lateActive=false}={}) {
    let running=active,compactions=0,trims=0;
    const stored={sessionId:'one',model:'test',compactionCount:2,inputTokens:9000,outputTokens:100,totalTokens:9100};
    const store={['agent:pixel:openai-user:ods-'+ 'a'.repeat(64)]:stored};const key=Object.keys(store)[0];
    const context={getRuntimeConfig:()=>({})};let response;
    const env={fs:{existsSync:()=>true},Date,validateSessionsCompactParams:{},assertValidParams:()=>true,
      requireSessionKey:value=>value,rejectWebchatSessionMutation:()=>false,
      resolveRequestedGlobalAgentId:()=>({ok:true,agentId:'pixel'}),
      resolveGatewaySessionTargetFromKey:()=>({target:{canonicalKey:key,agentId:'pixel'},storePath:'isolated'}),
      updateSessionStore:async(_path,fn)=>{const result=fn(store);if(lateActive)running=true;return result;},
      migrateAndPruneGatewaySessionStoreKey:()=>({entry:stored,primaryKey:key}),
      resolveSessionTranscriptCandidates:()=>['isolated-transcript'],
      hasTrackedActiveSessionRun:()=>running,isEmbeddedAgentRunActive:()=>running,resolveDefaultAgentId:()=> 'pixel',
      errorShape:(code,message)=>({code,message}),ErrorCodes:{UNAVAILABLE:'UNAVAILABLE'},
      resolveSessionModelRef:()=>({provider:'test',model:'test'}),normalizeOptionalString:()=>undefined,
      resolveAgentWorkspaceDir:()=>'/isolated',randomUUID:()=> 'operation',emitSessionOperation:()=>{},emitSessionsChanged:()=>{},normalizeThinkLevel:()=>undefined,normalizeReasoningLevel:()=>undefined,
      compactEmbeddedAgentSession:async()=>{compactions++;return {ok:true,compacted:true,result:{tokensBefore:9100,tokensAfter:600,sessionId:'rotated'}};},
      preflightSessionTranscriptForManualCompact:async()=>({compacted:true}),
      trimSessionTranscriptForManualCompact:async()=>{trims++;return {compacted:true,kept:1};},
      formatErrorMessage:()=> 'error'};
    const scope=vm.createContext(env);vm.runInContext(`${helper};globalThis.handler=({${handlerSource}})['sessions.compact'];`,scope);
    return {stored,get compactions(){return compactions;},get trims(){return trims;},
      async run(params={}){await scope.handler({req:{},params:{key,...params},context,client:{},isWebchatConnect:()=>false,
        respond:(ok,value,error)=>{response={ok,value,error};}});return response;}};
  }
  await t.test('idle request uses native LLM compact, retaining native checkpoint metadata',async()=>{
    const h=harness(),value=await h.run();assert.equal(value.ok,true);assert.equal(h.compactions,1);assert.equal(h.trims,0);
    assert.equal(h.stored.sessionId,'rotated');assert.equal(h.stored.compactionCount,3);assert.equal(h.stored.totalTokens,600);assert.equal(h.stored.totalTokensFresh,true);
  });
  for(const options of [{active:true},{lateActive:true}])await t.test(`active race is refused ${JSON.stringify(options)}`,async()=>{
    const h=harness(options),value=await h.run();assert.equal(value.ok,false);assert.equal(h.compactions,0);assert.equal(h.trims,0);assert.equal(h.stored.compactionCount,2);
  });
  await t.test('legacy maxLines also refuses active session without truncation',async()=>{
    const h=harness({lateActive:true}),value=await h.run({maxLines:1});assert.equal(value.ok,false);assert.equal(h.trims,0);assert.equal(h.compactions,0);
  });
});
