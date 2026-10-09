import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {claimsNativeDelegation,requestsNativeDelegation} from '../plugin/native-delegation-claims.mjs';
import {createSubagentDelivery} from '../plugin/subagent-delivery.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';

const request='Please delegate two short independent read-only reviews using your native subagent tools, then wait for both and combine their findings. Both should inspect Playground/example/index.html. Do not edit files or publish previews.';
const claim="I've delegated two independent read-only review tasks via native subagents:\n1. Keyboard accessibility\n2. Counter logic\nBoth subagents will return their findings in the next turn.";
const user='ods-'+'a'.repeat(64),id='chatcmpl_11111111-2222-4333-8444-555555555555';
const owner={agentId:'pixel',runId:id,sessionId:'owner-session',sessionKey:'agent:pixel:openai-user:'+user,trigger:'user'};
const child='agent:pixel:subagent:22222222-2222-4333-8444-555555555555',childRun='33333333-2222-4333-8444-555555555555';

for(const value of [request,'Spawn two native subagents for a review.','Could you please use native subagent tools to review the file?','Use sessions_spawn to delegate the review.'])
test(`explicit native request: ${value}`,()=>assert.equal(requestsNativeDelegation(value),true));

for(const value of ['Explain how native subagents are spawned.','Do not spawn native subagents.','Never delegate this to native subagents.',
  'Print "Please delegate to native subagents".','Translate: "Spawn two native subagents".','> Please delegate to native subagents.',
  '```text\nPlease delegate to native subagents.\n```','What happens when I use native subagent tools?','Please review this file yourself.'])
test(`ordinary discussion or exclusion is not delegation: ${value}`,()=>assert.equal(requestsNativeDelegation(value),false));

for(const value of [claim,'Both subagents have been spawned successfully.','The native subagents are now running.','I spawned two subagents.'])
test(`affirmative native claim: ${value}`,()=>assert.equal(claimsNativeDelegation(value),true));

for(const value of ['I have not delegated the task.','I delegated no work.','I have delegated nothing.','Both subagents were not spawned.',
  'No native subagents were started.','I will delegate when tools are available.','I started reading the file myself.',
  'I started two native subagents yesterday.','Both subagents were started in the previous request.',
  'I started reading about native subagents.','The phrase "I have delegated two tasks" is only an example.',
  '> Both subagents have been spawned successfully.','```text\nI have delegated two tasks.\n```',
  'If both subagents have been spawned, wait for their results.','How were both subagents spawned?'])
test(`quote denial or explanation is not an affirmative native claim: ${value}`,()=>assert.equal(claimsNativeDelegation(value),false));

function fixture(prompt=request) {
  const guard=createToolLoopGuard();guard.observeRun(owner,'pixel',{prompt});
  // Exercise the actual index.js registration, including its owner-intent
  // callback, rather than substituting a model-authored evidence predicate.
  const source=fs.readFileSync(new URL('../plugin/index.js',import.meta.url),'utf8');
  const start=source.indexOf('    const delegationDelivery = subagentDeliveryFor(');
  const end=source.indexOf("    api.on('subagent_spawned'",start);
  assert.ok(start>=0 && end>start);
  const registry=vm.runInNewContext(source.slice(start,end)+'\ndelegationDelivery;',{
    AGENT_ID:'pixel',toolLoopGuard:guard,extractAssistantVisibleText:message=>message.content.map(block=>block.text).join('\n'),
    subagentDeliveryFor:(_guard,options)=>createSubagentDelivery({...options,
      accessIdentity:()=> 'current',resolveOwnerSession:()=>({sessionId:owner.sessionId})}),
  });
  registry.observe({prompt},owner);
  return {registry,guard};
}

for(const evidence of ['none','literal-echo','failed-spawn','untrusted-accepted','native-without-accepted-reply'])
test(`unverified native delegation claim fails delivery with ${evidence}`,()=>{
  const {registry}=fixture();
  if(evidence!=='none') {
    const echo=evidence==='literal-echo';
    const ctx={...owner,toolName:echo?'exec':'sessions_spawn',toolCallId:'call'};
    registry.before({params:echo?{command:"echo 'Review the file in another agent'"}:{runtime:'subagent',mode:'run'}},ctx);
    if(evidence==='native-without-accepted-reply')registry.nativeSpawn({runId:childRun,childSessionKey:child},
      {runId:childRun,childSessionKey:child,requesterSessionKey:owner.sessionKey});
    else registry.after(echo?{result:{content:[{type:'text',text:'Printed task label'}]}}:
      evidence==='failed-spawn'?{error:'spawn rejected'}:{result:{details:{status:'accepted',childSessionKey:child,runId:childRun}}},ctx);
  }
  registry.finalize({lastAssistantMessage:claim},owner);
  const value=registry.read(user,id);assert.equal(value.status,'interrupted');assert.ok(!('text' in value));
});

test('accepted current native child substantiates the spawn claim without skipping later delivery checks',()=>{
  const {registry}=fixture(),ctx={...owner,toolName:'sessions_spawn',toolCallId:'spawn'};
  registry.before({params:{runtime:'subagent',mode:'run'}},ctx);
  registry.nativeSpawn({runId:childRun,childSessionKey:child},{runId:childRun,childSessionKey:child,requesterSessionKey:owner.sessionKey});
  registry.after({result:{details:{status:'accepted',runId:childRun,childSessionKey:child}}},ctx);
  registry.finalize({lastAssistantMessage:'I started a native subagent.'},owner);
  assert.equal(registry.admission(owner),undefined);
});

test('echo remains harmless and allowed; honest inability and ordinary discussion remain deliverable',()=>{
  for(const [prompt,text] of [[request,'I could not start the native subagents; no review was delegated.'],
    ['Explain native delegation.',claim]]) {
    const {registry}=fixture(prompt),ctx={...owner,toolName:'exec',toolCallId:'echo'};
    assert.equal(registry.blocked(ctx,{params:{command:"echo '[Subagent Task] example'"}}),undefined);
    registry.finalize({lastAssistantMessage:text},owner);
    assert.equal(registry.read(user,id).status,'not-delegated');
  }
});

test('same-run revision cannot erase the owner request; a fresh direct turn does not inherit it',()=>{
  const {registry,guard}=fixture();
  guard.observeRun(owner,'pixel',{prompt:'Finalization guidance only.'});
  registry.observe({prompt:'Finalization guidance only.'},owner);
  registry.finalize({lastAssistantMessage:claim},owner,{action:'revise'});
  assert.equal(registry.admission(owner),undefined,'another existing guard may still revise the provisional answer');
  registry.finalize({lastAssistantMessage:claim},owner);
  assert.equal(registry.read(user,id).status,'interrupted');
  const next={...owner,runId:id.replace('11111111','aaaaaaaa')};
  guard.observeRun(next,'pixel',{prompt:'Explain what native delegation means.'});
  registry.observe({},next);registry.finalize({lastAssistantMessage:claim},next);
  assert.equal(registry.read(user,next.runId).status,'not-delegated');
});
