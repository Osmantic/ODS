// Drop into ods/extensions/services/pixel-agent/tests, or set ODS_PLUGIN_DIR.
// Pure hook replay: these callbacks do not execute a tool or contact a model.
import test from 'node:test';
import assert from 'node:assert/strict';
import {resolve} from 'node:path';
import {pathToFileURL} from 'node:url';

const plugin = process.env.ODS_PLUGIN_DIR
  ? pathToFileURL(resolve(process.env.ODS_PLUGIN_DIR, 'tool-loop-guard.mjs'))
  : new URL('../plugin/tool-loop-guard.mjs', import.meta.url);
const {createToolLoopGuard, userMessageRequestsWorkspaceVisualContinuation, workspacePreviewMode} = await import(plugin.href);
const owner = 'Make our existing synthetic service demo usable. Work only in a NEW folder service-copy under your workspace. ' +
  'Copy source-demo/index.html into it first; leave previous projects unchanged. Keep its styling and FAQ. ' +
  'Repair the appointment form: require name and email, show inline errors, and keep submission local. ' +
  'Publish this folder to the private preview. Complete the work now.';
const promise = "Now I'll copy the file to the new folder and then make the repairs.";
const sourcePublish = /Call tool_call now with id pixel_ods_workspace_preview and args \{"relativeDirectory":"source-demo"\}/;

function replay(prompt, {reads = ['source-demo/index.html'], write, edit, readFailed = false, nativeRevision = false, revisionWrite, probePreview = false, ending = promise} = {}) {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel', trigger:'user', runId:'copy-order-run', sessionId:'copy-order-session',
    sessionKey:'agent:pixel:openai-user:ods-' + 'a'.repeat(64)};
  guard.observeRun(context, 'pixel', {prompt}, {executionHost:'sandbox', workspaceRoot:'/home/owner/.openclaw/workspace-pixel'});
  const calls = [];
  function observe(toolName, path, failed = false) {
    const toolCallId = 'fixture-' + calls.length;
    const ctx = {...context, toolName, toolCallId};
    const params = toolName === 'write' ? {path, content:'<!doctype html><title>Copy</title><form></form>'} :
      toolName === 'edit' ? {path, edits:[{oldText:'Original',newText:'Repaired copy'}]} : {path};
    const admitted = guard.beforeToolCall({runId:context.runId, toolName, toolCallId, params}, ctx, 'pixel');
    calls.push({toolName, path, admitted});
    if (admitted?.block) return;
    const result = {content:[{type:'text',text:failed ? 'File not found' :
      toolName === 'write' ? 'Successfully wrote file' : '<!doctype html><title>Original</title><form></form>'}],
      ...(failed ? {isError:true} : {})};
    guard.afterToolCall({runId:context.runId, toolName, toolCallId, params, result}, ctx, 'pixel');
    guard.toolResultPersist({toolCallId, message:{role:'toolResult',toolName,toolCallId,...result}}, ctx, 'pixel');
  }
  for (const path of reads) observe('read', path, readFailed);
  if (write) observe('write', write);
  if (edit) observe('edit', edit);
  const finalize = () => guard.beforeAgentFinalize({runId:context.runId, lastAssistantMessage:ending,
    messages:[{role:'user',content:[{type:'text',text:prompt}]},
      {role:'assistant',content:[{type:'text',text:ending}],stopReason:'stop'}]}, context, 'pixel');
  const firstDecision = finalize();
  if (!nativeRevision) return {calls, decision:firstDecision};
  assert.equal(firstDecision?.action, 'revise');
  const revision = 'Before accepting the previous final answer, apply this revision request and produce the revised final answer. ' +
    'Do not repeat completed work or rerun tools unless the request explicitly requires it.\n\n' +
    firstDecision.reason + '\n\n' + firstDecision.retry.instruction;
  guard.observeRun(context, 'pixel', {prompt:revision,messages:[{role:'user',content:[{type:'text',text:prompt}]}]},
    {executionHost:'sandbox',workspaceRoot:'/home/owner/.openclaw/workspace-pixel'});
  if (revisionWrite) observe('write', revisionWrite);
  const previewAdmission = probePreview ? guard.beforeToolCall({runId:context.runId,
    toolName:'pixel_ods_workspace_preview',toolCallId:'preview-after-revision',params:{}},
    {...context,toolName:'pixel_ods_workspace_preview',toolCallId:'preview-after-revision'},'pixel') : undefined;
  return {calls, firstDecision, decision:finalize(), previewAdmission};
}

test('copy/edit: a source read must not cause an immediate publication instruction for the source', () => {
  const {calls,decision} = replay(owner);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].admitted?.block, undefined);
  assert.equal(decision?.action, 'revise');
  assert.doesNotMatch(decision.retry.instruction, sourcePublish,
    'The owner requested a NEW edited copy, but no write/edit has happened. Publication must not skip that work.');
});

test('explicit unchanged publication can still publish the inspected source', () => {
  const {decision} = replay('Publish the existing source-demo page unchanged to the private preview. Do not edit any files.',
    {ending:'The existing page is ready to publish.'});
  assert.match(decision.retry.instruction, sourcePublish);
  assert.equal(decision.retry.maxAttempts, 1);
});

test('an existing page publication does not require a gratuitous edit', () => {
  const {decision} = replay('Show me the existing source-demo/index.html in the private preview. Do not change any files.',
    {ending:'Ready to show the existing page.'});
  assert.match(decision.retry.instruction, sourcePublish);
  assert.doesNotMatch(decision.retry.instruction, /edit .*before/i);
});

test('the observed publication continuation retains its one-attempt budget', () => {
  const {decision} = replay(owner);
  assert.equal(decision.retry.maxAttempts, 1);
});

test('no-preview request does not prescribe publication', () => {
  const {decision} = replay('Copy source-demo/index.html into a NEW folder service-copy and repair the form. Do not publish a preview.');
  assert.doesNotMatch(decision?.retry?.instruction ?? '', /pixel_ods_workspace_preview/);
});

test('no-tools request blocks the simulated read and gives no tool retry', () => {
  const {calls,decision} = replay('Do not use any tools. Explain how copying and repairing a page would work.',
    {ending:'Copying duplicates a source file into another folder; repairing changes that copy while preserving the original.'});
  assert.equal(calls[0].admitted?.block, true);
  assert.notEqual(decision?.action, 'revise');
});

test('a failed source read cannot bind a publication directory', () => {
  const {decision} = replay(owner, {readFailed:true});
  assert.doesNotMatch(decision?.retry?.instruction ?? '', sourcePublish);
});

test('two inspected entries do not prescribe one arbitrary source', () => {
  const {decision} = replay(owner, {reads:['source-demo/index.html','other-demo/index.html']});
  assert.doesNotMatch(decision?.retry?.instruction ?? '', sourcePublish);
  assert.doesNotMatch(decision?.retry?.instruction ?? '', /Call tool_call now.*other-demo/);
});

test('reading source then writing the copy does not prescribe publishing the source', () => {
  const {calls,decision} = replay(owner, {write:'service-copy/index.html',
    ending:'Created the new page and fixed the form. I will publish the new folder.'});
  assert.equal(calls[1].admitted?.block, undefined);
  assert.doesNotMatch(decision?.retry?.instruction ?? '', sourcePublish);
  assert.match(decision.retry.instruction, /Call tool_call now.*"relativeDirectory":"service-copy"/);
});

test('no successful file evidence cannot prescribe the source directory', () => {
  const {decision} = replay(owner, {reads:[]});
  assert.doesNotMatch(decision?.retry?.instruction ?? '', sourcePublish);
});

test('ordinary new app build can publish an inspected browser entry without a dummy write', () => {
  const {decision} = replay('Build a new app using the existing design notes and publish its preview.',
    {ending:'The browser entry is ready.'});
  assert.match(decision.retry.instruction, sourcePublish);
});

test('a copy produced by another tool can be selected after reading and editing its entry', () => {
  const {calls,decision} = replay(owner, {reads:['source-demo/index.html','service-copy/index.html'],
    edit:'service-copy/index.html',ending:'The copied entry has been repaired.'});
  assert.equal(calls[2].admitted?.block, undefined);
  assert.match(decision.retry.instruction, /Call tool_call now.*"relativeDirectory":"service-copy"/);
});

test('a harness continuation cannot replace the original new-copy obligation', () => {
  const {decision} = replay(owner, {nativeRevision:true});
  assert.doesNotMatch(decision?.retry?.instruction ?? '', sourcePublish);
});

test('a native revision must not auto-fill the source for an empty preview call', () => {
  const {previewAdmission} = replay(owner, {nativeRevision:true,probePreview:true});
  assert.notEqual(previewAdmission?.params?.relativeDirectory, 'source-demo');
});

test('a copy written in the native revision remains the selected output', () => {
  const {decision} = replay(owner, {nativeRevision:true,revisionWrite:'service-copy/index.html'});
  assert.match(decision.retry.instruction, /Call tool_call now.*"relativeDirectory":"service-copy"/);
});

const nestedOwner = 'Copy source-demo/index.html into a NEW folder source-demo/copy and repair the form. Publish the repaired copy in the private preview.';
test('editing a nested copy must not make the input parent eligible for publication', () => {
  const {decision} = replay(nestedOwner, {edit:'source-demo/copy/index.html'});
  assert.doesNotMatch(decision?.retry?.instruction ?? '', sourcePublish);
});

test('reading and editing a nested copy selects that copy alone', () => {
  const {decision} = replay(nestedOwner, {reads:['source-demo/index.html','source-demo/copy/index.html'],edit:'source-demo/copy/index.html'});
  assert.match(decision.retry.instruction, /Call tool_call now.*"relativeDirectory":"source-demo\/copy"/);
});

test('an explicit backup before an in-place repair keeps existing-app continuation', () => {
  const request='Fix the existing app. Copy app/index.html into a new folder app-backup first as a backup, then repair the form in place and republish the existing app.';
  assert.equal(userMessageRequestsWorkspaceVisualContinuation([], request), true);
  assert.equal(workspacePreviewMode([],request), 'continuation');
});

for (const request of [
  'Work only in a NEW folder service-copy. Copy source-demo/index.html into it first; keep the original unchanged as a backup and do not edit it in place. Repair the form. Publish the repaired copy in the private preview.',
  'Copy source-demo/index.html into a new folder service-copy and repair the form there. The original stays as a backup; never modify it in-place. Publish the repaired copy in the private preview.',
  'Copy source-demo/index.html into a new folder service-copy as a backup. Do not repair the original in place. Repair the copy and publish it in the private preview.',
]) test('preserving an original backup does not override the new work target: '+request, () => {
  assert.doesNotMatch(replay(request).decision?.retry?.instruction ?? '', sourcePublish);
  const retry=replay(request,{nativeRevision:true,probePreview:true});
  assert.notEqual(retry.previewAdmission?.params?.relativeDirectory,'source-demo');
  assert.doesNotMatch(retry.decision?.retry?.instruction ?? '', sourcePublish);
  assert.match(replay(request,{write:'service-copy/index.html'}).decision.retry.instruction,
    /Call tool_call now.*"relativeDirectory":"service-copy"/);
});

test('a new-copy request does not inherit the existing app visual-continuation scope', () => {
  assert.equal(userMessageRequestsWorkspaceVisualContinuation([], 'Improve the existing app and publish its preview.'), true);
  assert.equal(userMessageRequestsWorkspaceVisualContinuation([], 'Improve the existing app. Work only in a NEW folder service-copy. Copy source-demo/index.html into it first; publish the copy.'), false);
});

for (const instruction of [
  'Copy source-demo/index.html into a new folder service-copy and repair the form.',
  'Please copy source-demo/index.html to the new directory service-copy and repair the form.',
  'Work only in a NEW folder service-copy. Copy source-demo/index.html into it first; repair the form.',
]) test('new-copy directive keeps source reads out of publication: ' + instruction, () => {
  const {decision} = replay(instruction + ' Publish the repaired copy in the private preview.');
  assert.equal(decision.action, 'revise');
  assert.equal(decision.retry.maxAttempts, 1);
  assert.doesNotMatch(decision.retry.instruction, sourcePublish);
});

for (const example of [
  '"Work only in a NEW folder service-copy. Copy source-demo/index.html into it first."',
  '“Work only in a NEW folder service-copy. Copy source-demo/index.html into it first.”',
  '```text\nWork only in a NEW folder service-copy. Copy source-demo/index.html into it first.\n```',
  '> Work only in a NEW folder service-copy.\n> Copy source-demo/index.html into it first.',
  'Do not copy source-demo/index.html into a new folder service-copy.',
  'Do not work in a new folder service-copy. Do not copy source-demo/index.html.',
]) test('example or negated copying does not force new output: ' + example, () => {
  const {decision} = replay('Publish the existing source-demo page unchanged to the private preview. Do not edit any files.\n' + example,
    {ending:'The existing page is ready to publish.'});
  assert.match(decision.retry.instruction, sourcePublish);
});
