import test from 'node:test';
import assert from 'node:assert/strict';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {leakedExecGuidance, EXEC_GUIDANCE_LEAK_DELIVERY} from '../plugin/exec-guidance-provenance.mjs';

const note = '[ODS Pixel execution] Exec returned completed with exit code 0. This result has no background session ID. Use the returned output; do not invent a session ID or poll a PID.';
const owner = 'Without using tools, what exact stdout did the last terminal command return? Answer with just that number.';
const text = value => [{type:'text',text:value}];
function history(stdout = '2537') {
  return [
    {role:'user', content:text('Use the terminal tool to execute printf 2537 and report only the observed stdout.')},
    {role:'assistant', content:[{type:'toolCall',id:'exec0',name:'exec',arguments:{command:'printf 2537'}}]},
    {role:'toolResult',toolName:'exec',toolCallId:'exec0',content:[...text(stdout),...text(note)]},
    {role:'assistant',content:text(stdout)},
  ];
}
function fixture(prompt = owner, prior = history(), runId = 'recall-run') {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel',trigger:'user',runId,sessionId:'session-1',
    sessionKey:'agent:pixel:openai-user:ods-'+'a'.repeat(64)};
  guard.observeRun(context,'pixel',{prompt,messages:prior});
  const event = reply => ({...context,lastAssistantMessage:reply,
    messages:[...prior,{role:'user',content:text(prompt)},{role:'assistant',content:text(reply)}]});
  return {guard,context,event};
}

test('historical native exec guidance revises once; clean revision passes and tools cannot replay', () => {
  const {guard,context,event}=fixture();
  const decision=guard.beforeAgentFinalize(event('2537\n'+note),context);
  assert.equal(decision?.action,'revise');
  assert.equal(decision.retry.maxAttempts,1);
  assert.match(decision.retry.instruction,/Do not call tools/);
  assert.equal(guard.deliveryVerificationForRun(context.runId).status,'failed');
  assert.equal(guard.beforeToolCall({toolName:'exec',params:{command:'printf 2537'}},{...context,toolName:'exec',toolCallId:'replay'})?.block,true);
  assert.equal(guard.beforeAgentFinalize(event('2537'),context),undefined);
  assert.notEqual(guard.deliveryVerificationForRun(context.runId).status,'failed');
});

test('ignored or refused revision fails at the owner delivery boundary', () => {
  const {guard,context,event}=fixture();
  guard.beforeAgentFinalize(event('2537\n'+note),context);
  // Even if the SDK refuses a revision, the first final is already unverified.
  assert.equal(guard.replyPayloadSending({runId:context.runId,kind:'final',payload:{text:'2537\n'+note}})?.payload.text,EXEC_GUIDANCE_LEAK_DELIVERY);
  assert.equal(guard.beforeAgentFinalize(event('2537\n'+note),context)?.action,'finalize');
  assert.equal(guard.deliveryVerificationForRun(context.runId).text,EXEC_GUIDANCE_LEAK_DELIVERY);
});

test('another owner run has its own correction budget', () => {
  const {guard,context,event}=fixture();
  guard.beforeAgentFinalize(event('2537\n'+note),context);
  const next={...context,runId:'next-run'};
  guard.observeRun(next,'pixel',{prompt:owner,messages:history()});
  assert.equal(guard.beforeAgentFinalize({...event('2537\n'+note),runId:next.runId},next)?.action,'revise');
});

test('ordinary stdout and deliberate quotations are preserved', () => {
  for (const [prompt,prior,reply] of [
    [owner,history(),'2537'],
    [owner,history('literal '+note),'literal '+note],
    ['Repeat exactly: '+note,history(),note],
    ['Explain the internal execution guidance',history(),note],
  ]) {
    const {guard,context,event}=fixture(prompt,prior);
    assert.equal(guard.beforeAgentFinalize(event(reply),context),undefined);
    assert.notEqual(guard.deliveryVerificationForRun(context.runId).status,'failed');
  }
});

test('native result pairing and block boundaries are required', () => {
  const variants=[
    h=>{h[1].content[0].name='read';},
    h=>{h[2].toolCallId='unpaired';},
    h=>{h[2].content=text('2537\n'+note);},
    h=>{h[2].content[1].text+=' altered';},
    h=>{[h[1],h[2]]=[h[2],h[1]];},
  ];
  for(const mutate of variants) {
    const h=history();mutate(h);
    assert.equal(leakedExecGuidance('2537\n'+note,h,owner),undefined);
  }
});

test('a final hook with another run or session cannot arm a correction', () => {
  for(const key of ['runId','sessionId','sessionKey']) {
    const {guard,context,event}=fixture();
    assert.equal(guard.beforeAgentFinalize({...event('2537\n'+note),[key]:'other'},context),undefined);
    assert.notEqual(guard.deliveryVerificationForRun(context.runId).status,'failed');
  }
});

test('a fresh SDK-shaped history with no result details still identifies the note', () => {
  const h=history();h[2].details=null;
  assert.equal(leakedExecGuidance('2537\n'+note,h,owner)?.toolCallId,'exec0');
  h[2].content.unshift(...text('earlier stdout block'));
  assert.equal(leakedExecGuidance('2537\n'+note,h,owner)?.note,note);
});

test('the copied block is detected before or after the answer', () => {
  for (const reply of [note+'\n2537','2537\n'+note+'\nThat was the previous result.']) {
    assert.equal(leakedExecGuidance(reply,history(),owner)?.note,note);
  }
});

test('failure delivery leaves the historical stdout and owner text unchanged', () => {
  const {guard,context,event}=fixture();
  const final=event('2537\n'+note), before=structuredClone(final);
  guard.beforeAgentFinalize(final,context);
  guard.replyPayloadSending({runId:context.runId,kind:'final',payload:{text:final.lastAssistantMessage}});
  assert.deepEqual(final,before);
  assert.equal(final.messages[2].content[0].text,'2537');
});

test('another run may still execute tools after the earlier correction', () => {
  const {guard,context,event}=fixture();
  guard.beforeAgentFinalize(event('2537\n'+note),context);
  const next={...context,runId:'independent-run'};
  guard.observeRun(next,'pixel',{prompt:'Use the terminal tool to execute printf 11.',messages:history()});
  const result=guard.beforeToolCall({toolName:'exec',params:{command:'printf 11'}},{...next,toolName:'exec',toolCallId:'independent-exec'});
  assert.notEqual(result?.block,true,result?.blockReason);
});
