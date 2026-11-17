import test from 'node:test';
import assert from 'node:assert/strict';
import {createCompletionAssurance} from '../plugin/completion-assurance.mjs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS} from '../plugin/run-progress-budget.mjs';

const prompt = "Use the read tool to read /workspace/.ods-fleet-no-tools-read-v1/fixture.json and reply with the file's exact JSON content only. No markdown or other text.";
const answer = '{"error":false,"message":"This is a test. The file does not exist. Try /workspace/.ods-fleet-no-tools-read-v1/fixture.json"}';
const retryKey = 'ods-unattempted-file-read';
const allowed = {allowUnattemptedFileReadRecovery:true};
const user = 'ods-' + 'e'.repeat(64);
function assurance(owner = prompt) {
  const guard = createCompletionAssurance(); guard.begin(owner); return guard;
}
function turn(owner = prompt, overrides = {}) {
  const guard = createToolLoopGuard({abortRunAndDrain:async()=>({aborted:true,drained:true,forceCleared:false})});
  const context = {agentId:'pixel',runId:'read-run',sessionId:'read-session',
    sessionKey:'agent:pixel:openai-user:'+user,trigger:'user',...overrides};
  guard.observeRun(context,'pixel',{prompt:owner});
  const finalize = (text = answer, ctx = context, event = {}) => guard.beforeAgentFinalize({
    ...context,lastAssistantMessage:text,messages:[{role:'assistant',content:[{type:'text',text}]}],...event},ctx);
  return {guard,context,finalize};
}

test('exact live missing-file fabrication gets one recovery and truthful delivery', () => {
  const f = turn();
  const decision = f.finalize();
  assert.equal(decision?.retry?.idempotencyKey,retryKey);
  assert.equal(decision.retry.maxAttempts,1);
  assert.match(decision.retry.instruction,/read tool/);
  assert.equal(f.guard.deliveryVerificationForRun(f.context.runId).status,'failed');
  const delivered = f.guard.replyPayloadSending({runId:f.context.runId,kind:'final',payload:{text:answer}});
  assert.match(delivered?.payload?.text,/file read was not performed/);
  assert.doesNotMatch(delivered?.payload?.text,/does not exist/);
  assert.equal(f.finalize()?.action,'finalize');
});

for (const missing of [
  '{"error":{"message":"Path not found: /workspace/.ods-fleet-no-tools-read-v1/fixture.json"}}',
  'The path does not exist.',
  'No such file or directory: /workspace/report.json',
]) test(`missing-path variants require evidence too: ${missing}`, () => {
  const f=turn();
  assert.equal(f.finalize(missing)?.retry?.idempotencyKey,retryKey);
  assert.match(f.guard.deliveryVerificationForRun(f.context.runId).text,/file read was not performed/);
});

for (const limitation of ['Permission denied.', 'I cannot access that path.', 'The read tool is unavailable.',
  'If the file does not exist, it cannot be read.', 'I do not know whether the path does not exist.',
  'Permission denied; I cannot check whether there is no such file or directory.']) {
  test(`a capability refusal does not create missing-path recovery: ${limitation}`, () => {
    assert.notEqual(turn().finalize(limitation)?.retry?.idempotencyKey,retryKey);
  });
}

for (const rephrased of ['The file was not found.', '{"invented":true}', 'I will read it now.']) {
  test(`an unattempted recovery cannot clear failure by rephrasing: ${rephrased}`, () => {
    const guard = assurance();
    assert.equal(guard.finalize(answer,allowed)?.action,'revise');
    assert.equal(guard.finalize(rephrased,allowed)?.action,'finalize');
    assert.match(guard.terminal,/file read was not performed/);
    assert.equal(guard.terminalStatus,'failed');
  });
}

test('an armed failure remains truthful when another revision is no longer allowed', () => {
  const guard = assurance(); guard.finalize(answer,allowed);
  assert.equal(guard.finalize(answer)?.action,'finalize');
  assert.match(guard.terminal,/file read was not performed/);
});

for (const [label,event] of [
  ['success',{result:{content:[{type:'text',text:'{"actual":42}'}]}}],
  ['error',{error:'denied'}], ['missing result',{}],
  ['tool failure',{result:{isError:true,content:[{type:'text',text:'ENOENT'}]}}],
  ['unavailable',{result:{details:{status:'unavailable'}}}],
]) test(`an actual attempt suppresses recovery: ${label}`, () => {
  for (const afterRevision of [false,true]) {
    const guard = assurance();
    if (afterRevision) assert.equal(guard.finalize(answer,allowed)?.action,'revise');
    guard.observe('read',event);
    assert.notEqual(guard.finalize(answer,allowed)?.retry?.idempotencyKey,retryKey);
    assert.equal(guard.terminal,undefined);
  }
});

test('completed or failed unrelated work is never repeated by this recovery', () => {
  for (const tool of ['write','exec','tool_call','tool_search']) {
    const guard = assurance(); guard.observe(tool,{error:'failed'});
    assert.notEqual(guard.finalize(answer,allowed)?.retry?.idempotencyKey,retryKey);
  }
});

for (const owner of [
  'Translate: '+prompt, 'Explain how to '+prompt, 'If permitted, '+prompt,
  '> '+prompt, '```text\n'+prompt+'\n```', '"'+prompt+'"',
  'The documentation says "'+prompt+'". Explain it.',
  'Do not '+prompt.toLowerCase(), prompt+' Do not use tools.',
  prompt+' Only if I approve.', prompt+' Unless permission is required.',
  'Use the read tool to read https://example.org/file.json',
  'Use the read tool to read report.json if needed.',
  'Use the read tool to read report.json?',
  'Use the read tool to read the sentence aloud.',
  'Use the read tool to read report.json; do not actually read it yet.',
]) test(`quoted, qualified or non-executable request stays unchanged: ${owner}`, () => {
  const guard = assurance(owner);
  assert.notEqual(guard.finalize(answer,allowed)?.retry?.idempotencyKey,retryKey);
  assert.doesNotMatch(guard.terminal ?? '',/file read was not performed/);
});

test('generic capability denial and default-disabled recovery stay unchanged', () => {
  const guard = assurance();
  assert.equal(guard.finalize(answer),undefined);
  assert.equal(guard.finalize('I cannot access the filesystem.',allowed),undefined);
  assert.equal(guard.terminal,undefined);
});

test('cancel clears an already armed replacement', () => {
  const guard = assurance(); guard.finalize(answer,allowed); guard.cancel();
  assert.equal(guard.finalize(answer,allowed),undefined);
  assert.equal(guard.terminal,undefined);
});

test('actual read admission and result satisfy a recovery without changing tool policy', () => {
  const f = turn(); assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey);
  f.guard.observeRun(f.context,'pixel',{prompt:'Continue from the previous attempt.'});
  const context = {...f.context,toolName:'read',toolCallId:'read-1'};
  const params = {path:'/workspace/.ods-fleet-no-tools-read-v1/fixture.json'};
  const admitted = f.guard.beforeToolCall({toolName:'read',params},context);
  assert.notEqual(admitted?.block,true,admitted?.blockReason);
  f.guard.afterToolCall({toolName:'read',params,result:{content:[{type:'text',text:'{"actual":42}'}]}},context);
  assert.notEqual(f.finalize('{"actual":42}')?.retry?.idempotencyKey,retryKey);
  assert.notEqual(f.guard.deliveryVerificationForRun(f.context.runId).status,'failed');
});

test('cancelled, superseded, ended and foreign turns cannot spend a read revision', async () => {
  const f = turn();
  for (const key of ['runId','sessionId','sessionKey']) {
    assert.notEqual(f.finalize(answer,{...f.context,[key]:'other'})?.retry?.idempotencyKey,retryKey);
    assert.notEqual(f.finalize(answer,f.context,{[key]:'other'})?.retry?.idempotencyKey,retryKey);
  }
  assert.equal(f.finalize()?.retry?.idempotencyKey,retryKey);
  const cancelled=turn(); assert.equal(await cancelled.guard.abortUserRun(user),true);
  assert.notEqual(cancelled.finalize()?.retry?.idempotencyKey,retryKey);
  const ended=turn(); ended.guard.observeAgentEnd({success:true},ended.context);
  assert.notEqual(ended.finalize()?.retry?.idempotencyKey,retryKey);
  const superseded=turn(); superseded.guard.observeRun({...superseded.context,runId:'next'},'pixel',{prompt:'Hello'});
  assert.notEqual(superseded.finalize()?.retry?.idempotencyKey,retryKey);
});

test('noninteractive, no-tools and exhausted turns cannot request another tool call', () => {
  for (const f of [turn(prompt,{trigger:'cron'}), turn(prompt,{sessionKey:'agent:pixel:main'}),
    turn(prompt+' Do not use tools.')]) assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
  const f=turn();
  for(let i=0;i<RUN_PROGRESS_LIMITS.consecutiveFailures;i++) f.guard.toolResultPersist({toolCallId:'failed-'+i,
    message:{role:'toolResult',toolName:'read',isError:true,content:[]}}, {...f.context,toolName:'read',toolCallId:'failed-'+i});
  assert.notEqual(f.finalize()?.retry?.idempotencyKey,retryKey);
});

test('a new owner turn has independent attempt state and its own read obligation', () => {
  const f=turn('Read-only discussion of a previous file.');
  f.guard.afterToolCall({toolName:'read',result:{isError:true}},
    {...f.context,toolName:'read',toolCallId:'old-read'});
  const next={...f.context,runId:'new-owner-read'};
  f.guard.observeRun(next,'pixel',{prompt,messages:[{role:'user',content:'Do not use tools.'}]});
  const decision=f.guard.beforeAgentFinalize({...next,lastAssistantMessage:answer},next);
  assert.equal(decision?.retry?.idempotencyKey,retryKey);
});

test('old read instructions cannot create an obligation for a new conversational turn', () => {
  const f=turn(), next={...f.context,runId:'new-owner-discussion'};
  f.guard.observeRun(next,'pixel',{prompt:'Explain this example without using tools.',
    messages:[{role:'user',content:prompt},{role:'assistant',content:answer}]});
  assert.notEqual(f.guard.beforeAgentFinalize({...next,lastAssistantMessage:answer},next)?.retry?.idempotencyKey,retryKey);
});
