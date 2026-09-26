// A control tool named as a tool_call id. OpenClaw 2026.6.33 keeps
// tool_search, tool_describe, tool_call and tool_search_code out of the Tool
// Search catalog (tool-search TOOL_SEARCH_CONTROL_TOOL_NAMES), so
// tool_call {id:"tool_describe"} always fails with "Unknown tool id:
// tool_describe. Did you mean: exec, ...", and Pixel charged each one as a
// tool failure. Replays strixy round 107 (main d4a61f33): after an invalid
// wrapped inspection, the model sent two such calls, and the next invalid
// inspection became the fourth consecutive failure that stopped the website
// journey. The guard now answers them itself with the direct route, free at
// most FREE_CORRECTIONS_PER_KIND times per run, like a phantom process call.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard, FREE_CORRECTIONS_PER_KIND, OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {createWorkspacePreviewInspectTool, normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';

const STRIXY = JSON.parse(fs.readFileSync(new URL('./wrapped-control-tool-strixy-round107.json', import.meta.url), 'utf8'));
const [TURN] = STRIXY.turns;
const CALLS = TURN.calls;
const at = n => CALLS[n - 1];
const PUBLISH = at(7).details;
const STOPPED = [PROGRESS_FINALIZATION_INSTRUCTION, RUN_PROGRESS_STOP_REASON];
const LIMIT = RUN_PROGRESS_LIMITS.consecutiveFailures;

// The exact answers the model receives for recorded calls 9 and 10: fixed
// text, the model's own tool id, and this run's bound publication.
const INSPECT_EXAMPLE = {siteId: PUBLISH.siteId, sha256: PUBLISH.sha256, viewport: {width: 375, height: 667}, steps: [
  {action: 'assert-hidden', locator: {selector: '#affected-element-id'}},
  {action: 'click', locator: {role: 'button', name: 'Exact control name', exact: true}},
  {action: 'assert-visible', locator: {selector: '#affected-element-id'}},
]};
const DESCRIBE_INSPECT_ANSWER = 'tool_describe is its own tool, not a tool_call id. ' +
  'pixel_ods_workspace_preview_inspect is directly available in your tool list; call pixel_ods_workspace_preview_inspect itself. ' +
  'Each step is {action, locator}; a locator is {selector} or {role, name, exact:true}. ' +
  `Example of the exact shape: ${JSON.stringify(INSPECT_EXAMPLE)}. ` +
  'Replace the locators with the requested control and affected element from your source.';
const DESCRIBE_SELF_ANSWER = 'tool_describe is its own tool, not a tool_call id. Call tool_describe directly with {"id":"tool_describe"}.';

const text = value => ({content: [{type: 'text', text: value}]});
const envelope = (name, result) => ({content: [{type: 'text', text: JSON.stringify({tool: {id: `openclaw:pixel-ods:${name}`, name}, result})}],
  details: {tool: {id: `openclaw:pixel-ods:${name}`, source: 'openclaw', sourceName: 'pixel-ods', name}, result}});
// The SDK's standard veto result for a before_tool_call block.
const vetoed = reason => ({content: [{type: 'text', text: reason}],
  details: {status: 'blocked', deniedReason: 'plugin-before-tool-call', reason}});
const missingFile = {isError: true, content: [{type: 'text', text: 'ENOENT'}], details: {status: 'error'}};

function digest(files) {
  const hash = createHash('sha256');
  for (const [file, content] of Object.entries(files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) {
    const name = Buffer.from(file), data = Buffer.from(content), a = Buffer.alloc(4), b = Buffer.alloc(8);
    a.writeUInt32BE(name.length); b.writeBigUInt64BE(BigInt(data.length));
    hash.update(a).update(name).update(b).update(data);
  }
  return hash.digest('hex');
}

// Replays OpenClaw's hook sequence for each recorded call, one model round per
// call as recorded: before_tool_call, the executed (or vetoed) result,
// after_tool_call and tool_result_persist. Files land in a real workspace;
// inspections run the real tool, whose argument check rejects the recorded
// plans before any inspector is contacted; a tool_call that the guard lets
// through to a control-tool id gets OpenClaw's recorded "Unknown tool id".
function replay(t, {prompt = TURN.prompt} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-wrapped-control-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  const inspector = createWorkspacePreviewInspectTool({request: async () => { throw Error('the inspector must not be contacted'); },
    transitionRequirement: (toolCallId, params) => guard.previewInspectionTransition(toolCallId, params)});
  const context = {agentId: 'pixel', runId: TURN.runId, sessionId: STRIXY.sessionId, sessionKey: STRIXY.sessionKey};
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root});
  const files = {};
  const disk = (file, content) => {
    files[file.slice(file.indexOf('/') + 1)] = content;
    fs.mkdirSync(path.dirname(path.join(root, file)), {recursive: true});
    fs.writeFileSync(path.join(root, file), content);
  };
  // One hook sequence; `execute` returns the executed result from the params
  // the guard admitted. Returns the decision and the persisted model text.
  const hooks = async (toolName, params, id, execute) => {
    guard.observeModelCall({}, context);
    const ctx = {...context, toolName, toolCallId: id};
    const decision = guard.beforeToolCall({toolName, params, toolCallId: id}, ctx);
    const admitted = decision?.block ? params : toolName === 'tool_call' ? decision?.params ?? params : {...params, ...decision?.params};
    const result = decision?.block ? vetoed(decision.blockReason) : await execute(admitted);
    const isError = decision?.block === true || result.isError === true;
    guard.afterToolCall({toolName, params: admitted, toolCallId: id, result, ...(isError ? {error: result.content[0].text} : {})}, ctx);
    const message = {role: 'toolResult', toolName, toolCallId: id, isError, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message;
    return {decision, result, text: persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n')};
  };
  const run = async call => {
    const args = call.arguments;
    if (call.tool === 'write') {
      return hooks('write', args, call.id, () => {
        disk(args.path, args.content);
        return text(`Successfully wrote ${Buffer.byteLength(args.content)} bytes to ${args.path}`);
      });
    }
    if (call.tool === 'read') return hooks('read', args, call.id, () => text(fs.readFileSync(path.join(root, args.path), 'utf8')));
    if (call.tool === 'edit') {
      return hooks('edit', args, call.id, () => {
        const before = fs.readFileSync(path.join(root, args.path), 'utf8');
        disk(args.path, args.edits.reduce((content, edit) => content.replace(edit.oldText, () => edit.newText), before));
        return text(`Successfully replaced ${args.edits.length} block(s) in ${args.path}.`);
      });
    }
    if (call.tool === 'exec') return hooks('exec', args, call.id, () => ({...text(call.details.aggregated), details: call.details}));
    assert.equal(call.transport, 'tool_call');
    const outer = {id: call.tool, args};
    if (call.tool === 'pixel_ods_workspace_preview') {
      assert.equal(digest(files), call.details.sha256, 'replayed bytes reproduce the host snapshot digest');
      return hooks('tool_call', outer, call.id, () =>
        envelope(call.tool, {...text('ODS independently published and read back the workspace static files.'), details: call.details}));
    }
    if (call.tool === PREVIEW_INSPECTION_TOOL) {
      return hooks('tool_call', outer, call.id, async admitted =>
        envelope(call.tool, await inspector.execute(`tool_search_code:${call.id}:${call.tool}:1`, admitted.args)));
    }
    assert.equal(call.tool, 'tool_describe');
    return hooks('tool_call', outer, call.id, () => ({...text(call.seen), details: call.details, isError: true}));
  };
  // Replays the recorded calls up to and including call `last`, continuing
  // from the previous position.
  let next = 0;
  const through = async last => {
    const results = [];
    for (; next < last; next++) results.push(await run(CALLS[next]));
    return results;
  };
  let failures = 0;
  const fail = () => hooks('read', {path: `fleet-qualification-d16deca55032/missing-${++failures}.txt`}, `failure-${failures}`, () => missingFile);
  const exhausted = () => STOPPED.includes(guard.beforeToolCall({toolName: 'read', params: {path: 'README.md'}, toolCallId: 'probe'},
    {...context, toolName: 'read', toolCallId: 'probe'})?.blockReason);
  // Consecutive failures charged so far: the real failures still needed to stop the run.
  const consecutive = async () => {
    let needed = 0;
    while (!exhausted() && needed <= LIMIT) { needed += 1; await fail(); }
    return LIMIT - needed;
  };
  return {guard, context, hooks, run, through, fail, exhausted, consecutive};
}

// Each measurement consumes the run, so it replays from the start.
async function consecutiveAfter(t, last) {
  const r = replay(t);
  await r.through(last);
  return r.consecutive();
}

test('strixy round 107: the recorded next steps name the direct tools, never the tool_call route', async t => {
  const r = replay(t);
  const [write] = await r.through(1);
  assert.match(at(1).seen, /Call tool_call with id pixel_ods_workspace_preview/, 'the recorded next step');
  assert.ok(write.text.includes('Call pixel_ods_workspace_preview with args {"relativeDirectory":"fleet-qualification-d16deca55032"}. '), write.text);
  assert.doesNotMatch(write.text, /tool_call/);
  const publication = (await r.through(7)).at(-1).text;
  assert.match(at(7).seen, /If the tool is deferred, call tool_describe with its exact id/, 'the recorded inspection step');
  assert.ok(publication.includes(`call ${PREVIEW_INSPECTION_TOOL} directly with siteId "${PUBLISH.siteId}"`), publication);
  assert.doesNotMatch(publication, /tool_describe|tool_call with/);
});

test('strixy round 107: wrapped tool_describe calls 9 and 10 get the direct route free, and the fuse still holds', async t => {
  const r = replay(t);
  const results = await r.through(13);
  const answered = n => results[n - 1];
  // Call 8 is the recorded invalid wrapped inspection: an ordinary charged failure.
  assert.equal(answered(8).result.details.result.details.errorCode, 'invalid_request');
  assert.match(at(9).seen, /Unknown tool id: tool_describe\. Did you mean: exec/, 'OpenClaw could only reject call 9');
  assert.deepEqual(answered(9).decision, {block: true, blockReason: DESCRIBE_INSPECT_ANSWER});
  assert.equal(answered(9).text, DESCRIBE_INSPECT_ANSWER);
  assert.deepEqual(answered(10).decision, {block: true, blockReason: DESCRIBE_SELF_ANSWER});
  // Call 11 runs instead of meeting the stop and returns its own argument error.
  assert.notEqual(answered(11).decision?.block, true);
  assert.equal(answered(11).result.details.result.details.errorCode, 'invalid_request');
  assert.doesNotMatch(answered(11).text, /This response was stopped/);
  // The recorded calls 12 and 13 are still invalid; they reach the unchanged
  // consecutive fuse at call 13, and nothing after it runs.
  for (const n of [12, 13]) {
    assert.notEqual(answered(n).decision?.block, true, `call ${n}`);
    assert.equal(answered(n).result.details.result.details.errorCode, 'invalid_request', `call ${n}`);
  }
  assert.equal(r.exhausted(), true);
});

test('strixy round 107: the budget stays at one failure through calls 9 and 10 and reaches two at call 11', async t => {
  assert.equal(await consecutiveAfter(t, 8), 1);
  assert.equal(await consecutiveAfter(t, 9), 1);
  assert.equal(await consecutiveAfter(t, 10), 1);
  assert.equal(await consecutiveAfter(t, 11), 2);
  assert.equal(await consecutiveAfter(t, 12), 3);
});

test('the inspection example has the exact accepted shape and only this run\'s identifiers', async t => {
  assert.doesNotThrow(() => normalizeWorkspacePreviewInspectionParams(INSPECT_EXAMPLE));
  // The recorded call 8 plan used flat {action, selector} steps.
  assert.throws(() => normalizeWorkspacePreviewInspectionParams(at(8).arguments), /invalid inspection step/);
  // Before any publication of this run, the example carries placeholders only.
  const r = replay(t);
  await r.through(6);
  const {decision} = await r.hooks('tool_call', {id: 'tool_describe', args: {id: PREVIEW_INSPECTION_TOOL}}, 'early', () => assert.fail('must not run'));
  assert.ok(decision.blockReason.includes('"siteId":"SITE_ID","sha256":"SHA256"'), decision.blockReason);
  assert.ok(decision.blockReason.endsWith('and SITE_ID and SHA256 with the identifiers from the publication receipt.'), decision.blockReason);
  assert.doesNotMatch(decision.blockReason, /[a-f0-9]{24}/);
});

test('a third wrapped control-tool answer is charged, so repetition still ends the run', async t => {
  const r = replay(t);
  await r.through(7);
  for (let i = 0; i < LIMIT - 1; i++) await r.fail();
  assert.equal(r.exhausted(), false);
  for (let i = 1; i <= FREE_CORRECTIONS_PER_KIND; i++) {
    const {decision} = await r.hooks('tool_call', {id: 'tool_describe', args: {id: 'tool_describe'}}, `free-${i}`, () => assert.fail('must not run'));
    assert.equal(decision.blockReason, DESCRIBE_SELF_ANSWER);
    assert.equal(r.exhausted(), false, `free answer ${i} was charged`);
  }
  const {decision} = await r.hooks('tool_call', {id: 'tool_describe', args: {id: 'tool_describe'}}, 'charged', () => assert.fail('must not run'));
  assert.equal(decision.blockReason, DESCRIBE_SELF_ANSWER);
  assert.equal(r.exhausted(), true);
});

test('wrapped tool_search is answered free with its direct route', async t => {
  const r = replay(t);
  await r.through(7);
  for (let i = 0; i < LIMIT - 1; i++) await r.fail();
  const {decision, text: answer} = await r.hooks('tool_call', {id: 'tool_search', args: {query: 'preview inspection'}}, 'search',
    () => assert.fail('must not run'));
  assert.deepEqual(decision, {block: true,
    blockReason: 'tool_search is its own tool, not a tool_call id. Call tool_search directly with the same args.'});
  assert.equal(answer, decision.blockReason);
  assert.equal(r.exhausted(), false);
});

test('each control tool and inner id gets one fixed direct route', async t => {
  const r = replay(t);
  await r.through(3);
  const answer = async (id, args) => (await r.hooks('tool_call', args === undefined ? {id} : {id, args}, `${id}-${JSON.stringify(args)}`,
    () => assert.fail('must not run'))).decision.blockReason;
  assert.equal(await answer('tool_describe', {id: 'openclaw:core:read'}),
    'tool_describe is its own tool, not a tool_call id. read is directly available in your tool list; call read itself.');
  // The publication step names only the directory the publication check accepts.
  assert.equal(await answer('tool_describe', {id: 'pixel_ods_workspace_preview'}),
    'tool_describe is its own tool, not a tool_call id. pixel_ods_workspace_preview is directly available in your tool list; ' +
    'call pixel_ods_workspace_preview itself with args {"relativeDirectory":"fleet-qualification-d16deca55032"}.');
  assert.equal(await answer('tool_call', {id: 'pixel_ops_inventory', args: {}}),
    'tool_call is its own tool, not a tool_call id. Call tool_call directly with the inner id and args.');
  assert.equal(await answer('tool_search_code', {code: 'return 1'}),
    'tool_search_code is its own tool, not a tool_call id. Call tool_search_code directly with the same args.');
  // An id that is not a bounded tool name is never echoed back.
  assert.equal(await answer('tool_describe', {id: 'x'.repeat(200)}),
    'tool_describe is its own tool, not a tool_call id. Call tool_describe directly with the same args.');
  assert.equal(await answer('tool_describe'),
    'tool_describe is its own tool, not a tool_call id. Call tool_describe directly with the same args.');
});

test('a run that did not request a preview gets no publication arguments', () => {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: false});
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1', toolName: 'tool_call'};
  guard.observeRun(context, 'pixel', {prompt: 'Run the existing Python unit tests and report the result.'});
  const answer = (id, callId) => guard.beforeToolCall({toolName: 'tool_call', params: {id: 'tool_describe', args: {id}}, toolCallId: callId},
    {...context, toolCallId: callId})?.blockReason;
  assert.equal(answer('pixel_ods_workspace_preview', 'publish'),
    'tool_describe is its own tool, not a tool_call id. pixel_ods_workspace_preview is directly available in your tool list; call pixel_ods_workspace_preview itself.');
  // Without an installed inspector, the inspection tool is never called directly available.
  assert.equal(answer(PREVIEW_INSPECTION_TOOL, 'inspect'),
    `tool_describe is its own tool, not a tool_call id. Call tool_describe directly with {"id":"${PREVIEW_INSPECTION_TOOL}"}.`);
});

test('a namespaced or nested control id keeps OpenClaw\'s own answer', async t => {
  const r = replay(t);
  await r.through(3);
  for (const id of ['openclaw:core:tool_describe', 'tool_describe ']) {
    const decision = r.guard.beforeToolCall({toolName: 'tool_call', params: {id, args: {id: 'read'}}, toolCallId: id},
      {...r.context, toolName: 'tool_call', toolCallId: id});
    assert.notEqual(decision?.block, true, id);
  }
});

test('a Tool Search child is answered but never receives the free allowance', async t => {
  const r = replay(t);
  await r.through(7);
  const child = 'tool_search_code:outer:tool_call:1';
  const nested = r.guard.beforeToolCall({toolName: 'tool_call', params: {id: 'tool_describe', args: {id: 'tool_describe'}}, toolCallId: child},
    {...r.context, toolName: 'tool_call', toolCallId: child});
  assert.deepEqual(nested, {block: true, blockReason: DESCRIBE_SELF_ANSWER});
  // The child consumed no allowance: both free answers remain for the run.
  for (let i = 0; i < LIMIT - 1; i++) await r.fail();
  for (let i = 1; i <= FREE_CORRECTIONS_PER_KIND; i++) {
    await r.hooks('tool_call', {id: 'tool_describe', args: {id: 'tool_describe'}}, `after-child-${i}`, () => assert.fail('must not run'));
  }
  assert.equal(r.exhausted(), false);
});

test('a redundant tool_call around a core tool still unwraps and runs', async t => {
  const r = replay(t);
  await r.through(3);
  const params = {id: 'tool_call', args: {id: 'read', args: {path: 'fleet-qualification-d16deca55032/app.js'}}};
  const decision = r.guard.beforeToolCall({toolName: 'tool_call', params, toolCallId: 'unwrap'},
    {...r.context, toolName: 'tool_call', toolCallId: 'unwrap'});
  assert.notEqual(decision?.block, true, decision?.blockReason);
  assert.deepEqual(decision.params, {id: 'read', args: {path: 'fleet-qualification-d16deca55032/app.js'}});
});

test('an exhausted run returns the finalization refusal, not the free answer', async t => {
  const r = replay(t);
  await r.through(7);
  for (let i = 0; i < LIMIT; i++) await r.fail();
  const call = id => r.guard.beforeToolCall({toolName: 'tool_call', params: {id: 'tool_describe', args: {id: PREVIEW_INSPECTION_TOOL}}, toolCallId: id},
    {...r.context, toolName: 'tool_call', toolCallId: id})?.blockReason;
  assert.equal(call('after-stop'), PROGRESS_FINALIZATION_INSTRUCTION);
  // A tool call in the following answer turn is refused with the stop text.
  r.guard.observeModelCall({}, r.context);
  assert.equal(call('answer-turn'), RUN_PROGRESS_STOP_REASON);
});

test('the answer executes nothing, so a zero-submission run keeps clean-context replay eligibility', () => {
  for (const params of [
    {id: 'tool_call', args: {id: 'pixel_ops_run', args: {target: 'ods-host', action: 'host.kernel'}}},
    {id: 'tool_search_code', args: {code: 'return await openclaw.tools.call("pixel_ops_run", {})'}},
    {id: 'tool_describe', args: {id: 'pixel_ops_run'}},
  ]) {
    const guard = createToolLoopGuard();
    const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1', toolName: 'tool_call', toolCallId: 'wrapped'};
    guard.observeRun(context, 'pixel', {prompt: 'Use the Operations Broker to inspect the ODS host platform.'});
    assert.equal(guard.verificationForRun('run-1').code, OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE);
    const decision = guard.beforeToolCall({toolName: 'tool_call', params, toolCallId: 'wrapped'}, context);
    assert.equal(decision?.block, true, JSON.stringify(params));
    assert.match(decision.blockReason, new RegExp(`^${params.id} is its own tool, not a tool_call id\\. `));
    assert.equal(guard.verificationForRun('run-1').code, OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE, JSON.stringify(params));
  }
});
