import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard, EDIT_EARLY_NOTICE, EDIT_EARLY_NOTICES, EDIT_EARLY_READS} from '../plugin/tool-loop-guard.mjs';
import {contractSize, longTaskContract, ODS_LONG_TASK_CONTRACT, ODS_CONVERSATION_CONTRACT,
  promptContractForAgent} from '../plugin/prompt-contract.mjs';
import {readOnlyLookup} from '../plugin/run-progress-budget.mjs';
import {leakedExecGuidance} from '../plugin/exec-guidance-provenance.mjs';
import {realPromptBuild} from './prompt-build-harness.mjs';

// On the laptop (2026-10-30) a 227-call elf-of-doom run read all of ui.js
// about nine times and never edited: the tool-result projection had already
// dropped its earlier reads. Large-context runs get one fixed line about what
// stays in view, and after many reads with no file change one notice asks the
// model to record what it needs and start changing files. Each case replays
// OpenClaw's hook sequence for one model round and one call, in both the
// direct and the Tool Search transport.
const prompt = 'In elf-of-doom, big fights lag. Make normal enemies draw from a sprite cache instead.';
const printed = n => ({content:[{type:'text', text:`${n}: const x = ${n};`}], details:{status:'completed', exitCode:0, durationMs:3}});
const edited = {content:[{type:'text', text:'Successfully replaced 1 block(s) in Playground/elf-of-doom/ui.js.'}], details:{}};
const minutes = value => value * 60000;

// A Portal chat turn, which arms the 30-minute run deadline (run_deadline_delivery.test.mjs).
const OWNER_TURN = {agentId:'pixel', runId:'chatcmpl_3d1f6c0a-6b1e-4f7a-9a52-2e6f0c7d1a30', sessionId:'spark-session',
  sessionKey:`agent:pixel:openai-user:ods-${'a'.repeat(64)}`, trigger:'user'};

function harness({wrapped = false, event = {prompt}, capabilities, context = {agentId:'pixel', runId:'run',
  sessionId:'owner-session', sessionKey:'owner-key'}} = {}) {
  let clock = 0;
  const guard = createToolLoopGuard({now: () => clock});
  guard.observeRun(context, 'pixel', event, capabilities);
  let sequence = 0;
  function step(name, args, inner, id = `${name}-${++sequence}`) {
    guard.observeModelCall({}, context);
    const toolName = wrapped ? 'tool_call' : name;
    const params = wrapped ? {id:`openclaw:core:${name}`, args} : args;
    const ctx = {...context, toolName, toolCallId:id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId:id}, ctx);
    assert.notEqual(decision?.block, true, decision?.blockReason);
    const executed = wrapped ? decision?.params ?? params : {...params, ...decision?.params};
    const envelope = {tool:{id:`openclaw:core:${name}`, name, source:'openclaw', sourceName:'core'}, result:inner};
    const result = wrapped ? {content:[{type:'text', text:JSON.stringify(envelope)}], details:envelope} : inner;
    guard.afterToolCall({toolName, params:executed, toolCallId:id, result}, ctx);
    const message = {role:'toolResult', toolName, toolCallId:id, isError:false, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName, toolCallId:id, message}, ctx)?.message ?? message;
    return persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n');
  }
  const read = n => step('read', {path:`Playground/elf-of-doom/ui.js`, offset:n * 40 + 1, limit:40}, printed(n));
  const command = (n, text) => step('exec', {command:text, workdir:'/workspace/Playground/elf-of-doom'}, printed(n));
  const exec = n => command(n, `grep -n "name${n}" ui.js`);
  const edit = n => step('edit', {path:'Playground/elf-of-doom/ui.js', edits:[{oldText:`a${n}`, newText:`b${n}`}]}, edited);
  return {guard, read, exec, command, edit, step, at: value => { clock = minutes(value); }};
}
const noticed = text => text.endsWith(`\n${EDIT_EARLY_NOTICE}`);
const teamTurn = role => ({messages:[{role:'user', content:`You are the ${role} in the owner's Portal team. Your assignment: x`}]});

test('large-context runs get one fixed long-task line; small contracts and reviewers do not', () => {
  for (const [options, context, size] of [
    [{}, {agentId:'pixel'}, 'full'],
    [{configuredContextWindow:131072}, {agentId:'pixel'}, 'full'],
    [{configuredContextWindow:32769}, {agentId:'pixel'}, 'full'],
    [{configuredContextWindow:32768}, {agentId:'pixel'}, 'compact'],
    [{configuredContextWindow:131072, configuredLeanPrompt:true}, {agentId:'pixel'}, 'compact'],
    [{configuredContextWindow:131072}, {agentId:'pixel', contextTokenBudget:16384}, 'compact'],
    [{configuredContextWindow:8192}, {agentId:'pixel'}, 'tiny'],
    [{configuredContextWindow:131072, configuredLeanPrompt:true}, {agentId:'pixel', contextWindowReferenceTokens:8192}, 'tiny'],
  ]) {
    assert.equal(contractSize(context, options), size, JSON.stringify([options, context]));
    assert.equal(longTaskContract(context, undefined, options), size === 'full' ? ODS_LONG_TASK_CONTRACT : '');
  }
  // The conversation contract itself, and with it the compact and tiny ones, is unchanged.
  assert.equal(promptContractForAgent({agentId:'pixel'}, 'pixel').appendSystemContext, ODS_CONVERSATION_CONTRACT);
  assert.ok(!ODS_CONVERSATION_CONTRACT.includes(ODS_LONG_TASK_CONTRACT));
  assert.match(ODS_LONG_TASK_CONTRACT, /in a long response Portal replaces older tool output with a short notice/);
  assert.match(ODS_LONG_TASK_CONTRACT, /explanation of update_plan/);
  assert.match(ODS_LONG_TASK_CONTRACT, /make the first change as soon as you know enough for it/);
  // Team roles that plan or review do not change files.
  for (const role of ['Coordinator', 'Reviewer']) assert.equal(longTaskContract({agentId:'pixel'}, teamTurn(role)), '', role);
  assert.equal(longTaskContract({agentId:'pixel'}, teamTurn('Builder')), ODS_LONG_TASK_CONTRACT);
});

test('the entry adds the long-task line to ordinary turns only', async t => {
  const build = await realPromptBuild(t);
  assert.ok((await build(prompt)).appendSystemContext.includes(` ${ODS_LONG_TASK_CONTRACT} `));
  // Operations and exact downloads keep their own tool boundaries, which refuse update_plan.
  for (const turn of ['Check the ODS host hostname.', 'Download the exact bytes of the remote file https://example.com/report.pdf.']) {
    assert.ok(!(await build(turn)).appendSystemContext.includes(ODS_LONG_TASK_CONTRACT), turn);
  }
});

test('only commands that read files count as reads', () => {
  for (const command of [
    'cd /workspace/Playground/elf-of-doom && sed -n 200,420p engine.js',
    'grep -n "draw\\|blit\\|cache" engine.js | head -80',
    'grep -n "^function " chars.js | head -60 && echo ---- && sed -n 360,420p chars.js',
    'rg -n drawEnemyEnt',
    // The second review's probes, and the 2026-10-30 run's own shapes.
    'cat ui.js', 'cat -n ui.js | sed -n 100,200p', 'head -n 200 ui.js', 'tail -n +100 ui.js', 'head -60 README.md',
    'nl -ba ui.js | sed -n 1,80p', "sed -n '/function draw/,/^}/p' ui.js", 'grep -n draw ui.js 2>&1',
    `grep -n "" ui.js | sed -n '1597,1712p'`, 'wc -l *.js',
  ]) assert.equal(readOnlyLookup(command), true, command);
  for (const command of [
    "sed -i 's/a/b/' ui.js", "sed -n '/x/w out.txt' ui.js", "sed -n '1,5p;w out.txt' ui.js", 'node --check ui.js',
    'node bench.mjs', 'echo ===', 'ls -la', 'grep -n x ui.js > found.txt', 'cat ui.js > copy.js', 'head -n 5 ui.js | tee x',
    'tail -f server.log', 'sort -o out.txt ui.js', 'cat ui.js | sort -o out.txt', 'cp ui.js ui.bak', 'python3 tools/bake.py',
    'cat', undefined,
  ]) assert.equal(readOnlyLookup(command), false, String(command));
});

// A visual follow-up (a change to an app published earlier in the session)
// gets the long-task line, so its own tool boundary allows update_plan too.
test('a visual follow-up keeps update_plan for its notes', () => {
  const guard = createToolLoopGuard();
  const at = (runId, toolName, toolCallId) => ({agentId:'pixel', runId, sessionId:'session-1', toolName, toolCallId});
  guard.observeRun({agentId:'pixel', runId:'run-1', sessionId:'session-1'}, 'pixel', {prompt:'Build and show me an interactive website demo.'});
  const write = {path:'signal-garden/index.html', content:'<!doctype html><title>Signal Garden</title><p>slow</p>'};
  guard.beforeToolCall({toolName:'write', runId:'run-1', params:write}, at('run-1', 'write', 'w1'), 'pixel');
  guard.afterToolCall({toolName:'write', runId:'run-1', params:write, result:{details:{status:'completed'}}}, at('run-1', 'write', 'w1'), 'pixel');
  // The host's snapshot digest of signal-garden/ (one file), as the preview tool reports it.
  const length = (bytes, value) => { const buffer = Buffer.alloc(bytes); bytes === 4 ? buffer.writeUInt32BE(value) : buffer.writeBigUInt64BE(BigInt(value)); return buffer; };
  const sha256 = createHash('sha256').update(length(4, 10)).update('index.html').update(length(8, write.content.length)).update(write.content).digest('hex');
  const siteId = `site-${sha256.slice(0, 24)}`;
  const preview = {relativeDirectory:'signal-garden'};
  guard.beforeToolCall({toolName:'pixel_ods_workspace_preview', runId:'run-1', params:preview}, at('run-1', 'pixel_ods_workspace_preview', 'p1'), 'pixel');
  guard.afterToolCall({toolName:'pixel_ods_workspace_preview', runId:'run-1', params:preview, result:{details:{
    schemaVersion:1, kind:'ods-pixel-workspace-preview', status:'succeeded', relativeDirectory:'signal-garden', siteId, port:9437,
    url:`http://${siteId}.localhost:9437/${siteId}/`, files:1, bytes:write.content.length, sha256, entryFile:'index.html',
    entrySha256:createHash('sha256').update(write.content).digest('hex'), httpStatus:200, readbackVerified:true, executable:false, overwritten:false}}},
  at('run-1', 'pixel_ods_workspace_preview', 'p1'), 'pixel');
  guard.observeRun({agentId:'pixel', runId:'run-2', sessionId:'session-1'}, 'pixel', {prompt:'The button does not work. ' +
    'Investigate your existing artifact, fix that defect without starting over or using a template, republish the same artifact, ' +
    'and report only what the tools verify.'});
  assert.equal(guard.longTaskEligible('run-2'), true);
  const plan = {explanation:'ui.js:120 draws enemies', plan:[{step:'Edit draw', status:'in_progress'}]};
  assert.equal(guard.beforeToolCall({toolName:'update_plan', runId:'run-2', params:plan}, at('run-2', 'update_plan', 'plan-1'), 'pixel')?.block, undefined);
  assert.equal(guard.beforeToolCall({toolName:'tool_call', runId:'run-2', params:{id:'openclaw:core:update_plan', args:plan}},
    at('run-2', 'tool_call', 'plan-2'), 'pixel')?.block, undefined);
  // The lane still keeps other tools out.
  assert.equal(guard.beforeToolCall({toolName:'web_search', runId:'run-2', params:{query:'x'}}, at('run-2', 'web_search', 's-1'), 'pixel')?.block, true);
});

for (const wrapped of [false, true]) {
  const transport = wrapped ? 'Tool Search' : 'direct';

  test(`${transport}: many reads without a file change bring one notice, at most twice`, () => {
    const run = harness({wrapped});
    const noticedAt = [];
    for (let n = 1; n <= 100; n++) if (noticed(n % 2 ? run.read(n) : run.exec(n))) noticedAt.push(n);
    assert.deepEqual(noticedAt, [EDIT_EARLY_READS, 2 * EDIT_EARLY_READS]);
    assert.equal(noticedAt.length, EDIT_EARLY_NOTICES);
  });

  test(`${transport}: once a file has changed, the response gets no notice`, () => {
    const run = harness({wrapped});
    let n = 0;
    for (; n < EDIT_EARLY_READS - 1; n++) assert.equal(noticed(run.read(n)), false);
    assert.equal(noticed(run.edit(0)), false);
    // The notice says no file has changed, so after an edit it would be wrong.
    for (; n < 3 * EDIT_EARLY_READS; n++) assert.equal(noticed(run.read(n)), false, `read ${n}`);
  });

  test(`${transport}: checks, scripts, listings and in-place edits are not counted as reads`, () => {
    const run = harness({wrapped});
    const others = [n => `node --check part${n}.js`, n => `node bench${n}.mjs`, n => `ls -la dir${n}`, n => `sed -i 's/a${n}/b/' ui.js`];
    for (let n = 0; n < 2 * EDIT_EARLY_READS; n++) assert.equal(noticed(run.command(n, others[n % others.length](n))), false, `command ${n}`);
    const noticedAt = [];
    for (let n = 1; n <= EDIT_EARLY_READS; n++) if (noticed(run.command(n, `sed -n '${n},${n + 40}p' ui.js`))) noticedAt.push(n);
    assert.deepEqual(noticedAt, [EDIT_EARLY_READS]);
  });
}

test('Operations, exact downloads and read-only team roles get no notice', () => {
  for (const [label, event] of [
    ['Operations', {prompt:'Check the ODS host hostname.'}],
    ['Reviewer', teamTurn('Reviewer')],
  ]) {
    const run = harness({event});
    assert.equal(run.guard.longTaskEligible('run'), false, label);
    for (let n = 0; n < 2 * EDIT_EARLY_READS + 1; n++) assert.equal(noticed(run.read(n)), false, `${label} read ${n}`);
  }
  // An exact download refuses reads outright; it is not long-task work either.
  const download = harness({event:{prompt:'Download the exact bytes of the remote file https://example.com/report.pdf.'}});
  assert.equal(download.guard.longTaskEligible('run'), false);
  assert.equal(harness().guard.longTaskEligible('run'), true);
  assert.equal(harness({event:teamTurn('Builder')}).guard.longTaskEligible('run'), true);
  assert.equal(harness().guard.longTaskEligible('another-run'), false);
});

test('no notice once a time-limit notice said to start no new work', () => {
  const run = harness({context:OWNER_TURN, capabilities:{runTimeoutSeconds:1800, executionHost:'sandbox', workspaceRoot:'/workspace'}});
  for (let n = 0; n < 10; n++) assert.equal(noticed(run.read(n)), false);
  run.at(26);
  assert.match(run.read(100), /\[ODS Pixel time limit\]/);
  for (let n = 101; n < 101 + 2 * EDIT_EARLY_READS; n++) assert.equal(noticed(run.read(n)), false, `read ${n}`);
});

test('the notice is fixed text in a block of its own after the result', () => {
  const run = harness();
  let text;
  for (let n = 1; n <= EDIT_EARLY_READS; n++) text = run.read(n);
  assert.equal(text, `${EDIT_EARLY_READS}: const x = ${EDIT_EARLY_READS};\n${EDIT_EARLY_NOTICE}`);
  assert.match(EDIT_EARLY_NOTICE, /^\[ODS Pixel next step\] /);
  assert.match(EDIT_EARLY_NOTICE, /If the owner asked only for an explanation or a review, answer from what you have read\.$/);
});

test('an exec completion note copied into the answer is still found under a trailing notice', () => {
  const note = '[ODS Pixel execution] Exec returned completed with exit code 0. This result has no background session ID. ' +
    'Use the returned output; do not invent a session ID or poll a PID.';
  for (const trailing of [[EDIT_EARLY_NOTICE], ['[ODS Pixel time limit] x'], ['[ODS Pixel failure warning] x', EDIT_EARLY_NOTICE]]) {
    const messages = [
      {role:'assistant', content:[{type:'toolCall', id:'exec-1', name:'exec', arguments:{command:'grep -n x ui.js'}}]},
      {role:'toolResult', toolName:'exec', toolCallId:'exec-1',
        content:[{type:'text', text:'12: x'}, {type:'text', text:note}, ...trailing.map(text => ({type:'text', text}))]},
    ];
    assert.deepEqual(leakedExecGuidance(`12: x\n${note}`, messages), {note, toolCallId:'exec-1'}, trailing.join(' | '));
    assert.equal(leakedExecGuidance('12: x', messages), undefined);
  }
});
