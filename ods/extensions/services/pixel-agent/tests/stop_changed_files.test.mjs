import test from 'node:test';
import assert from 'node:assert/strict';
import {changedFilesNote, composeProgressFinalization, MAX_CHANGED_FILES_LISTED, MAX_LISTED_PATH_CHARS, PROGRESS_FINALIZATION_NOTE,
  PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE} from '../plugin/progress-finalization.mjs';
import {createToolLoopGuard, WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX,
  WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';

// A stopped response says which files it left changed, from the guard's
// receipts rather than the model's memory. On the laptop (2026-10-30) a stop
// came in the middle of a chars.js change, and the stop answer before it
// described files as "verified" from reads the model could no longer see.

test('the note names the changed files, counts what it does not list, and is empty without changes', () => {
  assert.equal(changedFilesNote([]), '');
  assert.equal(changedFilesNote(undefined), '');
  assert.equal(changedFilesNote(['Playground/elf-of-doom/chars.js']),
    'Files changed in this response: `Playground/elf-of-doom/chars.js`.');
  const many = Array.from({length: MAX_CHANGED_FILES_LISTED + 3}, (_, i) => `game/part-${i}.js`);
  assert.equal(changedFilesNote(many), `Files changed in this response: ${many.slice(0, MAX_CHANGED_FILES_LISTED)
    .map(file => `\`${file}\``).join(', ')}, and 3 more.`);
  // Names that would break the line are counted, not printed.
  assert.equal(changedFilesNote(['ok.js', 'bad`name.js', 'line\nbreak.js']), 'Files changed in this response: `ok.js`, and 2 more.');
  assert.equal(changedFilesNote(['bad`name.js']), '1 file was changed in this response.');
  assert.equal(changedFilesNote(['a`b', 'c`d']), '2 files were changed in this response.');
  // So are names past the length the list prints.
  assert.equal(changedFilesNote(['ok.js', `${'x'.repeat(MAX_LISTED_PATH_CHARS - 3)}.js`, `${'x'.repeat(MAX_LISTED_PATH_CHARS - 2)}.js`]),
    `Files changed in this response: \`ok.js\`, \`${'x'.repeat(MAX_LISTED_PATH_CHARS - 3)}.js\`, and 1 more.`);
});

test('a tool-limit answer gets the note right after the stop note', () => {
  const text = composeProgressFinalization('Done so far: the cache is written; the blit is not.', {
    refusedToolCalls: true, changedFiles: ['Playground/elf-of-doom/chars.js']});
  assert.equal(text, 'Done so far: the cache is written; the blit is not.\n\n' +
    `${PROGRESS_FINALIZATION_NOTE}\n\n${PROGRESS_FINALIZATION_REFUSED_CALLS_NOTE}\n\n` +
    'Files changed in this response: `Playground/elf-of-doom/chars.js`.');
  assert.ok(!composeProgressFinalization('Nothing was changed in this run.', {}).includes('Files changed'));
});

test('the tool-limit stop lists the files this response changed, once each, in order', () => {
  const guard = createToolLoopGuard();
  const context = {agentId:'pixel', runId:'run', sessionId:'owner-session', sessionKey:'owner-key'};
  guard.observeRun(context, 'pixel', {prompt:'Fix the enemy drawing in Playground/game.'});
  let sequence = 0;
  const step = (toolName, params, result) => {
    const toolCallId = `${toolName}-${++sequence}`, ctx = {...context, toolName, toolCallId};
    guard.observeModelCall({}, context);
    const decision = guard.beforeToolCall({toolName, params, toolCallId}, ctx);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    guard.afterToolCall({toolName, params:{...params, ...decision?.params}, toolCallId, result,
      ...(result.isError ? {error:result.content[0].text} : {})}, ctx);
    guard.toolResultPersist({toolName, toolCallId, message:{role:'toolResult', toolName, toolCallId, ...structuredClone(result)}}, ctx);
  };
  const edited = {content:[{type:'text', text:'Successfully replaced 1 block(s).'}], details:{}};
  step('edit', {path:'Playground/game/chars.js', edits:[{oldText:'a', newText:'b'}]}, edited);
  step('write', {path:'Playground/game/sprites.js', content:'export const SPRITES = new Map();\n'},
    {content:[{type:'text', text:'Successfully wrote 33 bytes.'}], details:{}});
  step('edit', {path:'Playground/game/chars.js', edits:[{oldText:'c', newText:'d'}]}, edited);
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    step('read', {path:`Playground/game/missing-${i}.js`}, {isError:true, content:[{type:'text', text:'ENOENT'}], details:{status:'error'}});
  }
  const delivery = guard.deliveryVerificationForRun('run');
  assert.equal(delivery.status, 'failed');
  assert.ok(delivery.text.startsWith(RUN_PROGRESS_STOP_REASON), delivery.text);
  assert.match(delivery.text, /\n\nFiles changed in this response: `Playground\/game\/chars\.js`, `Playground\/game\/sprites\.js`\./);
});

const ROOT = '/home/ods/.openclaw/workspace';
const ok = {content:[{type:'text', text:'Success.'}], details:{}};

for (const host of ['gateway', 'sandbox']) {
  test(`${host}: each changed file is listed once, by its workspace path`, () => {
    const guard = createToolLoopGuard();
    const context = {agentId:'pixel', runId:'run', sessionId:'owner-session', sessionKey:'owner-key'};
    const root = host === 'sandbox' ? '/workspace' : ROOT;
    guard.observeRun(context, 'pixel', {prompt:'Fix the enemy drawing in Playground/game.'},
      {runTimeoutSeconds:1800, executionHost:host, workspaceRoot:root});
    let sequence = 0;
    const step = (toolName, params, result) => {
      const toolCallId = `${toolName}-${++sequence}`, ctx = {...context, toolName, toolCallId};
      guard.observeModelCall({}, context);
      const decision = guard.beforeToolCall({toolName, params, toolCallId}, ctx);
      assert.notEqual(decision?.block, true, decision?.blockReason);
      guard.afterToolCall({toolName, params:{...params, ...decision?.params}, toolCallId, result,
        ...(result.isError ? {error:result.content[0].text} : {})}, ctx);
    };
    step('edit', {path:'Playground/game/chars.js', edits:[{oldText:'a', newText:'b'}]}, ok);
    // The same file by its absolute path, and another with a "./" segment.
    step('apply_patch', {input:`*** Begin Patch\n*** Update File: ${root}/Playground/game/chars.js\n@@\n-c\n+d\n` +
      '*** Update File: Playground/game/./ui.js\n@@\n-e\n+f\n*** End Patch'}, ok);
    for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
      step('read', {path:`Playground/game/missing-${i}.js`}, {isError:true, content:[{type:'text', text:'ENOENT'}], details:{status:'error'}});
    }
    const text = guard.deliveryVerificationForRun('run').text;
    assert.match(text, /\n\nFiles changed in this response: `Playground\/game\/chars\.js`, `Playground\/game\/ui\.js`\.(?:\n|$)/, text);
  });
}

// A website build that stops before any preview, after writing its CSS and
// JavaScript: the owner must not read "didn't create the website files"
// beside the list of files it saved.
const STOPWATCH = 'Make me a simple stopwatch web page with start, stop and lap buttons';
const SAVED = 'Files changed in this response: `stopwatch/style.css`, `stopwatch/app.js`.';
const OWNER = {agentId:'pixel', runId:'chatcmpl_5f0a7c11-2d3e-4f50-8a61-7b8c9d0e1f2a', sessionId:'stopwatch-session',
  sessionKey:`agent:pixel:openai-user:ods-${'5f0'.repeat(21)}a`, trigger:'user'};

function stopwatchBuild({now} = {}) {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable:true, abortRun:() => true, now,
    abortRunAndDrain:async () => ({aborted:true, drained:true})});
  guard.observeRun(OWNER, 'pixel', {prompt:STOPWATCH}, {runTimeoutSeconds:1800, executionHost:'sandbox', workspaceRoot:'/workspace'});
  let sequence = 0;
  const call = (toolName, params, result) => {
    guard.observeModelCall({}, OWNER);
    const ctx = {...OWNER, toolName, toolCallId:`call-${++sequence}`};
    const decision = guard.beforeToolCall({toolName, params, toolCallId:ctx.toolCallId}, ctx);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    guard.afterToolCall({toolName, params:decision?.params ?? params, toolCallId:ctx.toolCallId, result,
      ...(result.isError ? {error:result.content[0].text} : {})}, ctx);
    guard.observeModelEnd({}, OWNER);
  };
  call('write', {path:'stopwatch/style.css', content:'body{font-family:sans-serif}\n'}, ok);
  call('write', {path:'stopwatch/app.js', content:'let t = 0;\n'}, ok);
  return {guard, call};
}

test('a website build stopped at the tool limit says its files were saved', () => {
  const {guard, call} = stopwatchBuild();
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    call('exec', {command:`npx serve stopwatch --port ${3000 + i}`},
      {isError:true, details:{status:'completed', exitCode:127}, content:[{type:'text', text:'npx: not found'}]});
  }
  const delivery = guard.deliveryVerificationForRun(OWNER.runId);
  assert.equal(delivery.status, 'failed');
  assert.ok(delivery.text.includes(SAVED), delivery.text);
  assert.ok(delivery.text.includes(WORKSPACE_PREVIEW_UNVERIFIED_DELIVERY_PREFIX), delivery.text);
  assert.ok(!delivery.text.includes(WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX), delivery.text);
});

test('a website build stopped by the run deadline says its files were saved', () => {
  let clock = 0;
  const {guard} = stopwatchBuild({now:() => clock});
  clock = 30 * 60000;
  guard.observeAgentEnd({success:false, durationMs:clock, messages:[
    {role:'user', content:[{type:'text', text:STOPWATCH}]}, {role:'assistant', stopReason:'aborted', content:[]}]}, OWNER);
  const delivery = guard.deliveryVerificationForRun(OWNER.runId);
  assert.equal(delivery.status, 'failed');
  assert.ok(delivery.text.includes(SAVED), delivery.text);
  assert.ok(!delivery.text.includes(WORKSPACE_PREVIEW_NOT_CREATED_DELIVERY_PREFIX), delivery.text);
});

// A near-longest stop: a 20,000-character tool-limit answer citing five unread
// 2 KB links (each repeated in the host's unverified-links line) in a research
// run that also changed eight files with the longest names the list prints.
// The ingress takes 32 KiB of text; it arrives whole. (A failed text past that
// is cut to the limit on delivery, so the owner always gets a reply.)
test('a long tool-limit stop with changed files arrives whole within the ingress text limit', () => {
  const research = 'Compare NVIDIA GeForce RTX 5070 versus AMD Radeon RX 9070 for a US buyer playing PC games at 2560x1440. ' +
    'Actually search the live web and open sources, and save your notes under research/.';
  const context = {agentId:'pixel', runId:'run', sessionId:'owner-session', sessionKey:'owner-key'};
  const guard = createToolLoopGuard({abortRun:() => true});
  guard.observeRun(context, 'pixel', {prompt:research});
  let sequence = 0;
  const call = (toolName, params) => {
    const toolCallId = `${toolName}-${++sequence}`;
    return {toolCallId, ctx:{...context, toolName, toolCallId},
      decision:guard.beforeToolCall({toolName, params, toolCallId}, {...context, toolName, toolCallId})};
  };
  guard.observeModelCall({}, context);
  for (let i = 0; i < 8; i++) {
    const params = {path:`research/${'n'.repeat(MAX_LISTED_PATH_CHARS - 14)}-${i}.md`, content:'notes\n'};
    const {toolCallId, ctx, decision} = call('write', params);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    guard.afterToolCall({toolName:'write', params, toolCallId, result:ok}, ctx);
  }
  for (let i = 0; i < RUN_PROGRESS_LIMITS.consecutiveFailures; i++) {
    const {toolCallId, ctx} = call('web_fetch', {url:`https://example.com/missing-${i}`});
    guard.toolResultPersist({toolCallId, message:{role:'toolResult', toolName:'web_fetch', toolCallId, isError:true,
      content:[{type:'text', text:'Web fetch failed (403)'}]}}, ctx);
  }
  guard.observeModelEnd({}, context);
  // The one tool-free answer turn (progress_finalization.test.mjs enterAnswerTurn).
  guard.observeModelCall({}, context);
  call('web_search', {query:'again'});
  guard.observeModelEnd({}, context);
  guard.observeModelCall({}, context);
  guard.observeModelEnd({}, context);
  const links = Array.from({length:5}, (_, i) => `https://example.com/${'p'.repeat(2026)}/${i}`);
  const answer = `${links.join('\n')}\n${'Findings so far. '.repeat(1200)}`.slice(0, 20000);
  guard.beforeAgentFinalize({lastAssistantMessage:answer}, context);
  const delivery = guard.deliveryVerificationForRun('run');
  assert.equal(delivery.status, 'failed');
  assert.ok(delivery.text.startsWith(answer), 'the answer stays whole');
  assert.match(delivery.text, /Files changed in this response: `research\/n+-0\.md`, .*`research\/n+-7\.md`\./);
  assert.ok(delivery.text.endsWith(`${links.map(url => `<${url}>`).join(', ')}.`), 'the last host fact is whole');
  assert.ok(delivery.text.length <= 32 * 1024, `${delivery.text.length} characters`);
});
