// A control tool named as a tool_call id. OpenClaw 2026.6.33 keeps
// tool_search, tool_describe, tool_call and tool_search_code out of the Tool
// Search catalog (tool-search TOOL_SEARCH_CONTROL_TOOL_NAMES), so
// tool_call {id:"tool_describe"} always fails with "Unknown tool id:
// tool_describe. Did you mean: exec, ...", and Pixel charged each one as a
// tool failure. Replays strixy round 107 (main d4a61f33): after an invalid
// wrapped inspection, the model sent two such calls, and the next invalid
// inspection became the fourth consecutive failure that stopped the website
// journey. The guard now answers them itself with the direct route, free at
// most FREE_CORRECTIONS_PER_KIND times per run, like a phantom process call,
// and only where it would otherwise have passed the call to OpenClaw: every
// refusal and terminal fuse keeps precedence.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard, FREE_CORRECTIONS_PER_KIND, OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE,
  OPERATIONS_REQUIRES_BROKER_REASON, CLIENT_CANCELLED_REASON, CODING_LOOP_ABORT_REASON, CANCELLABLE_EXEC_UNAVAILABLE_REASON,
  OPERATIONS_CONTINUATION_REQUIRES_STATUS_REASON, OPERATIONS_LOOP_ABORT_REASON, EXACT_DOWNLOAD_REQUIRES_BROKER_REASON,
  EXACT_DOWNLOAD_LOOP_ABORT_REASON, PRIVATE_URL_REQUEST_REASON, PRIVATE_NETWORK_LOOP_ABORT_REASON,
  WORKSPACE_PREVIEW_FORBIDDEN_REASON} from '../plugin/tool-loop-guard.mjs';
import {RUN_PROGRESS_LIMITS, RUN_PROGRESS_STOP_REASON} from '../plugin/run-progress-budget.mjs';
import {PROGRESS_FINALIZATION_INSTRUCTION} from '../plugin/progress-finalization.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {createWorkspacePreviewInspectTool, normalizeWorkspacePreviewInspectionParams,
  correctedInspectionArgs} from '../plugin/workspace-preview-inspect.mjs';

const STRIXY = JSON.parse(fs.readFileSync(new URL('./wrapped-control-tool-strixy-round107.json', import.meta.url), 'utf8'));
const [TURN] = STRIXY.turns;
const CALLS = TURN.calls;
const at = n => CALLS[n - 1];
const PUBLISH = at(7).details;
const STOPPED = [PROGRESS_FINALIZATION_INSTRUCTION, RUN_PROGRESS_STOP_REASON];
const LIMIT = RUN_PROGRESS_LIMITS.consecutiveFailures;

// The model's own call 8 plan, with each locator nested and exact:true on
// its role/name: nothing else changed, so it is ready to send.
const READY = {siteId: PUBLISH.siteId, sha256: PUBLISH.sha256, viewport: {width: 375, height: 667}, steps: [
  {action: 'assert-hidden', locator: {selector: '#midnight-card'}},
  {action: 'click', locator: {role: 'button', name: 'Show sold out', exact: true}},
  {action: 'assert-visible', locator: {selector: '#midnight-card'}},
]};
// Before any inspection attempt, only the shape, with placeholder locators.
const INSPECT_EXAMPLE = {siteId: PUBLISH.siteId, sha256: PUBLISH.sha256, viewport: {width: 375, height: 667}, steps: [
  {action: 'assert-hidden', locator: {selector: '#affected-element-id'}},
  {action: 'click', locator: {role: 'button', name: 'Exact control name', exact: true}},
  {action: 'assert-visible', locator: {selector: '#affected-element-id'}},
]};
const DESCRIBE = 'tool_describe is its own tool, not a tool_call id. ';
const CONTROL_ID = 'Control tools cannot be described or called by id; tool_describe and tool_call take only the id of a tool that tool_search found. ';
const INSPECT_ROUTE = 'pixel_ods_workspace_preview_inspect is directly available in your tool list; call pixel_ods_workspace_preview_inspect itself. ';
const READY_ROUTE = INSPECT_ROUTE + "Your last inspection's arguments were invalid only in the form of its locators: " +
  'each belongs inside locator, and a role and name need exact:true. ' +
  `Send it directly with exactly these args, your own steps with each locator in that form: ${JSON.stringify(READY)}.`;
const SHAPE_ROUTE = INSPECT_ROUTE + 'Each step is {action, locator}; a locator is {selector} or {role, name, exact:true}. ' +
  `Shape only; these placeholder locators match nothing: ${JSON.stringify(INSPECT_EXAMPLE)}. ` +
  'Replace both locators with the requested control and affected element from your source.';
// The exact answers the model receives for recorded calls 9 and 10: both
// route to the inspection tool with the model's own plan, ready to send.
const DESCRIBE_INSPECT_ANSWER = DESCRIBE + READY_ROUTE;
const DESCRIBE_SELF_ANSWER = DESCRIBE + CONTROL_ID + READY_ROUTE;
// The same call 10 before any inspection attempt of the run (after call 7).
const DESCRIBE_SELF_BEFORE_INSPECTION = DESCRIBE + CONTROL_ID + SHAPE_ROUTE;

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
    transitionRequirement: (toolCallId, params) => guard.previewInspectionTransition(toolCallId, params),
    currentPublication: (toolCallId, params) => guard.previewInspectionPublication(toolCallId, params)});
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
  const inspectText = n => answered(n).result.details.result.content[0].text;
  // Call 8 is the recorded invalid wrapped inspection: an ordinary charged
  // failure. The tool's own result no longer says "call tool_describe ...
  // then retry through tool_call" (the recorded text before call 9); it gives
  // the direct route and the model's own plan with each locator in the
  // accepted form, since its identifiers are this run's current publication.
  assert.equal(answered(8).result.details.result.details.errorCode, 'invalid_request');
  assert.match(at(8).text, /Call tool_describe with id "pixel_ods_workspace_preview_inspect", then retry through tool_call/);
  assert.deepEqual(correctedInspectionArgs(at(8).arguments), READY);
  assert.ok(inspectText(8).includes('Next step: call pixel_ods_workspace_preview_inspect directly with exactly these args, ' +
    `your own steps with each locator in that form: ${JSON.stringify(READY)}.`), inspectText(8));
  assert.doesNotMatch(inspectText(8), /tool_describe|tool_call/);
  assert.match(at(9).seen, /Unknown tool id: tool_describe\. Did you mean: exec/, 'OpenClaw could only reject call 9');
  assert.deepEqual(answered(9).decision, {block: true, blockReason: DESCRIBE_INSPECT_ANSWER});
  assert.equal(answered(9).text, DESCRIBE_INSPECT_ANSWER);
  // Call 10 asked to describe tool_describe itself. OpenClaw cannot describe a
  // control tool either, so the answer names the inspection tool instead.
  assert.deepEqual(answered(10).decision, {block: true, blockReason: DESCRIBE_SELF_ANSWER});
  // Call 11 runs instead of meeting the stop and returns its own argument
  // error, again with the model's plan ready to send.
  assert.notEqual(answered(11).decision?.block, true);
  assert.equal(answered(11).result.details.result.details.errorCode, 'invalid_request');
  assert.ok(inspectText(11).includes(JSON.stringify(READY)), inspectText(11));
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

test('the inspection answers carry only accepted shapes and this run\'s identifiers', async t => {
  assert.doesNotThrow(() => normalizeWorkspacePreviewInspectionParams(INSPECT_EXAMPLE));
  assert.doesNotThrow(() => normalizeWorkspacePreviewInspectionParams(READY));
  // The recorded call 8 plan used flat {action, selector} steps.
  assert.throws(() => normalizeWorkspacePreviewInspectionParams(at(8).arguments), /invalid inspection step/);
  // Before any publication of this run, the example carries placeholders only.
  const r = replay(t);
  await r.through(6);
  const {decision} = await r.hooks('tool_call', {id: 'tool_describe', args: {id: PREVIEW_INSPECTION_TOOL}}, 'early', () => assert.fail('must not run'));
  assert.ok(decision.blockReason.includes('Shape only; these placeholder locators match nothing: '), decision.blockReason);
  assert.ok(decision.blockReason.includes('"siteId":"SITE_ID","sha256":"SHA256"'), decision.blockReason);
  assert.ok(decision.blockReason.endsWith('and SITE_ID and SHA256 with the identifiers from the publication receipt.'), decision.blockReason);
  assert.doesNotMatch(decision.blockReason, /[a-f0-9]{24}/);
  // After publication and before any inspection attempt: this run's
  // identifiers with placeholder locators, never a plan presented as ready.
  await r.through(7);
  const published = await r.hooks('tool_call', {id: 'tool_describe', args: {id: PREVIEW_INSPECTION_TOOL}}, 'published', () => assert.fail('must not run'));
  assert.equal(published.decision.blockReason, DESCRIBE + SHAPE_ROUTE);
});

test('a ready plan is offered only when nesting alone repairs the model\'s last plan for this publication', async t => {
  // A plan that ran and failed on its locators is not re-offered as ready.
  const r = replay(t);
  await r.through(7);
  const valid = {...READY, steps: [...READY.steps.slice(0, 2), {action: 'assert-visible', locator: {selector: '#missing'}}]};
  const ran = await r.hooks('tool_call', {id: PREVIEW_INSPECTION_TOOL, args: valid}, 'ran', () => envelope(PREVIEW_INSPECTION_TOOL,
    {isError: true, content: [{type: 'text', text: 'Preview inspection failed.'}], details: {status: 'failed'}}));
  assert.notEqual(ran.decision?.block, true);
  const afterRun = await r.hooks('tool_call', {id: 'tool_describe', args: {id: PREVIEW_INSPECTION_TOOL}}, 'after-run', () => assert.fail('must not run'));
  assert.equal(afterRun.decision.blockReason, DESCRIBE + SHAPE_ROUTE);
  // A flat plan for another snapshot is not offered for this one.
  const other = {...at(8).arguments, siteId: `site-${'a'.repeat(24)}`, sha256: 'a'.repeat(64)};
  const inspector = createWorkspacePreviewInspectTool({request: async () => assert.fail('must not run')});
  const rejected = await r.hooks('tool_call', {id: PREVIEW_INSPECTION_TOOL, args: other}, 'other', async admitted =>
    envelope(PREVIEW_INSPECTION_TOOL, await inspector.execute('other', admitted.args)));
  assert.notEqual(rejected.decision?.block, true);
  const afterOther = await r.hooks('tool_call', {id: 'tool_describe', args: {id: PREVIEW_INSPECTION_TOOL}}, 'after-other', () => assert.fail('must not run'));
  assert.equal(afterOther.decision.blockReason, DESCRIBE + SHAPE_ROUTE);
  // The same flat plan for this publication is offered ready to send.
  await r.hooks('tool_call', {id: PREVIEW_INSPECTION_TOOL, args: at(8).arguments}, 'bound', async admitted =>
    envelope(PREVIEW_INSPECTION_TOOL, await inspector.execute('bound', admitted.args)));
  const afterBound = await r.hooks('tool_call', {id: 'tool_describe', args: {id: PREVIEW_INSPECTION_TOOL}}, 'after-bound', () => assert.fail('must not run'));
  assert.equal(afterBound.decision.blockReason, DESCRIBE_INSPECT_ANSWER);
});

test('a third wrapped control-tool answer is charged, so repetition still ends the run', async t => {
  const r = replay(t);
  await r.through(7);
  for (let i = 0; i < LIMIT - 1; i++) await r.fail();
  assert.equal(r.exhausted(), false);
  for (let i = 1; i <= FREE_CORRECTIONS_PER_KIND; i++) {
    const {decision} = await r.hooks('tool_call', {id: 'tool_describe', args: {id: 'tool_describe'}}, `free-${i}`, () => assert.fail('must not run'));
    assert.equal(decision.blockReason, DESCRIBE_SELF_BEFORE_INSPECTION);
    assert.equal(r.exhausted(), false, `free answer ${i} was charged`);
  }
  const {decision} = await r.hooks('tool_call', {id: 'tool_describe', args: {id: 'tool_describe'}}, 'charged', () => assert.fail('must not run'));
  assert.equal(decision.blockReason, DESCRIBE_SELF_BEFORE_INSPECTION);
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
  // Tool Search mode "tools" never offers tool_search_code to the model.
  assert.equal(await answer('tool_search_code', {code: 'return 1'}),
    'tool_search_code is its own tool, not a tool_call id. It is also not offered in this Tool Search mode. ' +
    'Find a tool with tool_search, then call it directly by its own name, or through tool_call with the exact id tool_search returned.');
  // An id that is not a bounded tool name is never echoed back.
  assert.equal(await answer('tool_describe', {id: 'x'.repeat(200)}),
    'tool_describe is its own tool, not a tool_call id. Call tool_describe directly with the same args.');
  assert.equal(await answer('tool_describe'),
    'tool_describe is its own tool, not a tool_call id. Call tool_describe directly with the same args.');
});

test('a control tool named as the inner id is never offered as something to describe or call', async t => {
  // OpenClaw cannot describe or call a control tool by id either (strixy
  // round 107 call 10). Before any publication, no inspection is pending.
  for (const [id, args] of [['tool_describe', {id: 'tool_describe'}], ['tool_describe', {id: 'tool_search_code'}],
    ['tool_call', {id: 'tool_search', args: {query: 'preview'}}]]) {
    const r = replay(t);
    await r.through(3);
    const {decision} = await r.hooks('tool_call', {id, args}, `${id}-inner`, () => assert.fail('must not run'));
    assert.deepEqual(decision, {block: true, blockReason: `${id} is its own tool, not a tool_call id. ${CONTROL_ID}` +
      'Call the tool you need directly by its own name from your tool list, or find it with tool_search.'});
  }
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
  assert.deepEqual(nested, {block: true, blockReason: DESCRIBE_SELF_BEFORE_INSPECTION});
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

test('a wrapped control tool executes nothing, so a zero-submission run keeps clean-context replay eligibility', () => {
  // The Operations boundary keeps precedence over the answer; only discovery
  // (tool_describe) passes that boundary and so reaches it.
  for (const [params, blockReason] of [
    [{id: 'tool_call', args: {id: 'pixel_ops_run', args: {target: 'ods-host', action: 'host.kernel'}}}, OPERATIONS_REQUIRES_BROKER_REASON],
    [{id: 'tool_search_code', args: {code: 'return await openclaw.tools.call("pixel_ops_run", {})'}}, OPERATIONS_REQUIRES_BROKER_REASON],
    [{id: 'tool_describe', args: {id: 'pixel_ops_run'}}, 'tool_describe is its own tool, not a tool_call id. Call tool_describe directly with {"id":"pixel_ops_run"}.'],
  ]) {
    const guard = createToolLoopGuard();
    const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1', toolName: 'tool_call', toolCallId: 'wrapped'};
    guard.observeRun(context, 'pixel', {prompt: 'Use the Operations Broker to inspect the ODS host platform.'});
    assert.equal(guard.verificationForRun('run-1').code, OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE);
    const decision = guard.beforeToolCall({toolName: 'tool_call', params, toolCallId: 'wrapped'}, context);
    assert.deepEqual(decision, {block: true, blockReason}, JSON.stringify(params));
    assert.equal(guard.verificationForRun('run-1').code, OPERATIONS_UNAVAILABLE_ZERO_SUBMISSIONS_CODE, JSON.stringify(params));
  }
});

// Every earlier refusal and terminal fuse keeps precedence: the answer
// replaces only a call that the guard would have passed to OpenClaw, where it
// could only fail as an unknown tool id. Each case gives the refusal and the
// abort count that the same wrapped call got before the answer existed.
test('owner cancellation and terminal fuses keep precedence over the wrapped control answer', async () => {
  const describeRead = {id: 'tool_describe', args: {id: 'read'}};
  const searchCode = {id: 'tool_search_code', args: {code: 'return 1'}};
  const cases = [
    ['owner cancellation', async () => {
      const guard = createToolLoopGuard({abortRunAndDrain: async () => ({aborted: true, drained: true}), abortRun: () => true});
      const user = `ods-${'e'.repeat(64)}`;
      const context = {agentId: 'pixel', runId: 'run-live', sessionId: 'session-live', sessionKey: `agent:pixel:openai-user:${user}`};
      guard.observeRun(context, 'pixel', {prompt: TURN.prompt});
      assert.equal(await guard.abortUserRun(user), true);
      return {guard, context};
    }, [[describeRead, CLIENT_CANCELLED_REASON, 0], [{id: 'tool_search', args: {query: 'x'}}, CLIENT_CANCELLED_REASON, 0]]],
    ['coding-loop abort', async aborts => {
      const guard = createToolLoopGuard({execControl: {prepare: () => { throw Error('missing control mount'); }, signal: () => true},
        abortRun: id => { aborts.push(id); return true; }});
      const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
      const prepared = guard.beforeToolCall({toolName: 'exec', params: {command: 'true'}, toolCallId: 'exec'}, {...context, toolName: 'exec', toolCallId: 'exec'});
      assert.equal(prepared.blockReason, CANCELLABLE_EXEC_UNAVAILABLE_REASON);
      return {guard, context};
    }, [[describeRead, CODING_LOOP_ABORT_REASON, 1]]],
    ['Operations continuation', async aborts => {
      const guard = createToolLoopGuard({abortRun: id => { aborts.push(id); return true; }});
      const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
      guard.observeRun(context, 'pixel', {prompt: `The administrator approved job ops-1234567890124-fedcba654321 with plan SHA-256 ${'d'.repeat(64)}. ` +
        'Check that exact job and report only the host-authoritative status.'});
      return {guard, context};
    }, [[describeRead, OPERATIONS_CONTINUATION_REQUIRES_STATUS_REASON, 0], [describeRead, OPERATIONS_LOOP_ABORT_REASON, 1]]],
    ['exact download', async aborts => {
      const guard = createToolLoopGuard({abortRun: id => { aborts.push(id); return true; }});
      const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
      const url = 'https://raw.githubusercontent.com/psf/requests/dae7ef63b4df6eded86637f251fc4e3a06c3b479/src/requests/api.py';
      guard.observeRun(context, 'pixel', {prompt: `Download the public source file ${url} into release-2663/requests-source/api.py without changing its bytes. ` +
        'Use the local file to report the exact get() and request() signatures and their starting line numbers in release-2663/requests-source/review.md. ' +
        'Verify quotations from the saved source. Do not execute repository code or install dependencies. ' +
        'Keep existing projects untouched; if exact-byte download is unavailable, say so rather than manufacturing a substitute.'});
      return {guard, context};
    }, [[searchCode, EXACT_DOWNLOAD_REQUIRES_BROKER_REASON, 0], [searchCode, EXACT_DOWNLOAD_LOOP_ABORT_REASON, 1]]],
    ['private network', async aborts => {
      const guard = createToolLoopGuard({abortRun: id => { aborts.push(id); return true; }});
      const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
      guard.observeRun(context, 'pixel', {messages: [{role: 'user', content: 'Inspect http://127.0.0.1:3000 now'}]});
      return {guard, context};
    }, [[describeRead, PRIVATE_URL_REQUEST_REASON, 0], [describeRead, PRIVATE_NETWORK_LOOP_ABORT_REASON, 1]]],
  ];
  for (const [label, setup, calls] of cases) {
    const aborts = [];
    const {guard, context} = await setup(aborts);
    for (const [index, [params, reason, abortCount]] of calls.entries()) {
      const id = `${label}-${index}`;
      const decision = guard.beforeToolCall({toolName: 'tool_call', params, toolCallId: id}, {...context, toolName: 'tool_call', toolCallId: id});
      assert.deepEqual(decision, {block: true, blockReason: reason}, `${label} call ${index}`);
      assert.equal(aborts.length, abortCount, `${label} call ${index}`);
    }
  }
});

// Reviewer probe rr6747-extra: the full exact-download request. Wrapped
// discovery is answered with its direct route and aborts nothing; wrapped
// execution keeps the exact-download refusal and then its loop abort.
test('an exact-download request answers wrapped discovery and keeps refusing wrapped execution', () => {
  const url = 'https://raw.githubusercontent.com/psf/requests/dae7ef63b4df6eded86637f251fc4e3a06c3b479/src/requests/api.py';
  const prompt = `Download the public source file ${url} into release-2663/requests-source/api.py without changing its bytes. ` +
    'Use the local file to report the exact get() and request() signatures and their starting line numbers in release-2663/requests-source/review.md. ' +
    'Verify quotations from the saved source. Do not execute repository code or install dependencies. ' +
    'Keep existing projects untouched; if exact-byte download is unavailable, say so rather than manufacturing a substitute.';
  const refused = [EXACT_DOWNLOAD_REQUIRES_BROKER_REASON, EXACT_DOWNLOAD_LOOP_ABORT_REASON, EXACT_DOWNLOAD_LOOP_ABORT_REASON];
  for (const [label, params, expected, abortCount] of [
    ['describe read', {id: 'tool_describe', args: {id: 'read'}}, null, 0],
    ['search', {id: 'tool_search', args: {query: 'x'}}, null, 0],
    ['search code', {id: 'tool_search_code', args: {code: 'return 1'}}, refused, 2],
    ['nested call', {id: 'tool_call', args: {id: 'tool_call', args: {id: 'read', args: {path: 'x'}}}}, refused, 2],
  ]) {
    const aborts = [];
    const guard = createToolLoopGuard({abortRun: id => { aborts.push(id); return true; }});
    const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1'};
    guard.observeRun(context, 'pixel', {prompt});
    for (let i = 0; i < 3; i++) {
      const decision = guard.beforeToolCall({toolName: 'tool_call', params, toolCallId: `c${i}`},
        {...context, toolName: 'tool_call', toolCallId: `c${i}`});
      assert.equal(decision?.block, true, `${label} ${i}`);
      if (expected) assert.equal(decision.blockReason, expected[i], `${label} ${i}`);
      else assert.match(decision.blockReason, / is its own tool, not a tool_call id\. /, `${label} ${i}`);
    }
    assert.equal(aborts.length, abortCount, label);
  }
});

test('a tool that the owner\'s request excludes is never offered as a direct route', () => {
  const guard = createToolLoopGuard();
  const context = {agentId: 'pixel', runId: 'run-1', sessionId: 'session-1', toolName: 'tool_call'};
  guard.observeRun(context, 'pixel', {prompt: 'Create a small static website in fleet-site with an index.html. Do not publish or display a preview.'});
  const ask = (id, callId) => guard.beforeToolCall({toolName: 'tool_call', params: {id: 'tool_describe', args: {id}}, toolCallId: callId},
    {...context, toolCallId: callId})?.blockReason;
  assert.equal(ask('pixel_ods_workspace_preview', 'publish'), DESCRIBE + WORKSPACE_PREVIEW_FORBIDDEN_REASON);
  // The direct call gets the same refusal.
  assert.equal(guard.beforeToolCall({toolName: 'pixel_ods_workspace_preview', params: {relativeDirectory: 'fleet-site'}, toolCallId: 'direct'},
    {...context, toolName: 'pixel_ods_workspace_preview', toolCallId: 'direct'})?.blockReason, WORKSPACE_PREVIEW_FORBIDDEN_REASON);
  // An owner-excluded command tool gets the owner's exclusion, not a route.
  const restricted = createToolLoopGuard();
  restricted.observeRun(context, 'pixel', {prompt: 'Publish the existing site/index.html as a Workbench preview. Do not run any shell commands.'});
  const excluded = restricted.beforeToolCall({toolName: 'tool_call', params: {id: 'tool_describe', args: {id: 'exec'}}, toolCallId: 'exec'},
    {...context, toolCallId: 'exec'})?.blockReason;
  const direct = restricted.beforeToolCall({toolName: 'exec', params: {command: 'ls'}, toolCallId: 'exec-direct'},
    {...context, toolName: 'exec', toolCallId: 'exec-direct'})?.blockReason;
  assert.equal(typeof direct, 'string');
  assert.equal(excluded, DESCRIBE + direct);
});

// Re-verification of #6747 at 65412f21: after a republish, a flat inspection
// plan with the earlier snapshot's identifiers came back from the tool as
// "call ... with exactly these args" carrying those stale identifiers, which
// the guard's own route answer already refused to offer. The tool now asks
// the guard for this call's current publication and offers a plan as ready
// only with its identifiers; otherwise the receipt caveat.
test('rejected inspection arguments for an earlier snapshot are never offered back as ready to send', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-stale-inspection-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  const inspector = createWorkspacePreviewInspectTool({request: async () => { throw Error('the inspector must not be contacted'); },
    transitionRequirement: (toolCallId, params) => guard.previewInspectionTransition(toolCallId, params),
    currentPublication: (toolCallId, params) => guard.previewInspectionPublication(toolCallId, params)});
  const context = {agentId: 'pixel', runId: 'stale-run', sessionId: 'stale-session', sessionKey: 'agent:pixel:main'};
  guard.observeRun(context, 'pixel', {prompt: 'Create a small static website in fleet-site with an index.html. It has a "Show sold out" button that reveals a hidden card. Publish it as a Workbench preview and check the show/hide interaction.'}, {workspaceRoot: root});
  let sequence = 0;
  const hooks = async (toolName, params, execute) => {
    const id = `stale-${++sequence}`, ctx = {...context, toolName, toolCallId: id};
    guard.observeModelCall({}, context);
    const decision = guard.beforeToolCall({toolName, params, toolCallId: id}, ctx);
    const admitted = decision?.block ? params : {...params, ...decision?.params};
    const result = decision?.block ? vetoed(decision.blockReason) : await execute(admitted, id);
    const isError = decision?.block === true || result.isError === true;
    guard.afterToolCall({toolName, params: admitted, toolCallId: id, result, ...(isError ? {error: result.content[0].text} : {})}, ctx);
    const message = {role: 'toolResult', toolName, toolCallId: id, isError, ...structuredClone(result)};
    const persisted = guard.toolResultPersist({toolName, toolCallId: id, message}, ctx)?.message ?? message;
    return {decision, result, text: persisted.content.filter(block => block.type === 'text').map(block => block.text).join('\n')};
  };
  const receipt = content => {
    const sha256 = digest({'index.html': content}), siteId = `site-${sha256.slice(0, 24)}`;
    return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: 'fleet-site', siteId, sha256,
      entryFile: 'index.html', entrySha256: createHash('sha256').update(content).digest('hex'), files: 1, bytes: Buffer.byteLength(content),
      port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true, executable: false,
      overwritten: false, publishedPaths: ['index.html'], publishedPathsOmitted: 0};
  };
  const publish = async content => {
    fs.mkdirSync(path.join(root, 'fleet-site'), {recursive: true});
    fs.writeFileSync(path.join(root, 'fleet-site/index.html'), content);
    await hooks('write', {path: 'fleet-site/index.html', content}, () => text('Successfully wrote'));
    const details = receipt(content);
    await hooks('pixel_ods_workspace_preview', {relativeDirectory: 'fleet-site'}, () => ({...text('published'), details}));
    return details;
  };
  const first = await publish('<!doctype html><title>t</title><button id="b">Show sold out</button><div id="c" hidden>x</div>');
  const current = await publish('<!doctype html><title>t</title><button id="b">Show sold out</button><div id="c" hidden>y</div>');
  assert.notEqual(first.sha256, current.sha256);
  const flat = ({siteId, sha256}) => ({siteId, sha256, viewport: {width: 375, height: 667}, steps: [
    {action: 'assert-hidden', selector: '#c'}, {action: 'click', role: 'button', name: 'Show sold out'}, {action: 'assert-visible', selector: '#c'}]});
  const inspect = params => hooks(PREVIEW_INSPECTION_TOOL, params, (admitted, id) => inspector.execute(id, admitted));
  // The earlier snapshot's plan: the receipt caveat, never its identifiers.
  const stale = await inspect(flat(first));
  assert.equal(stale.result.details.errorCode, 'invalid_request');
  const staleText = stale.result.content[0].text;
  assert.ok(!staleText.includes(first.siteId) && !staleText.includes(first.sha256), staleText);
  assert.doesNotMatch(staleText, /exactly these args/);
  assert.ok(staleText.includes('with the exact published siteId and full sha256 from the latest publication receipt'), staleText);
  assert.match(staleText, /Do not guess snapshot identifiers\./);
  // The guard's route answer agrees: placeholders with the current identifiers.
  const afterStale = await hooks('tool_call', {id: 'tool_describe', args: {id: PREVIEW_INSPECTION_TOOL}}, () => assert.fail('must not run'));
  assert.ok(afterStale.decision.blockReason.includes('Shape only; these placeholder locators match nothing: '), afterStale.decision.blockReason);
  assert.ok(!afterStale.decision.blockReason.includes(first.siteId), afterStale.decision.blockReason);
  // The same plan for the current publication is offered ready to send.
  const ready = correctedInspectionArgs(flat(current));
  const bound = await inspect(flat(current));
  assert.ok(bound.result.content[0].text.includes(`with exactly these args, your own steps with each locator in that form: ${JSON.stringify(ready)}.`),
    bound.result.content[0].text);
  // An unbound call (not this run's pending inspection) has no current publication.
  assert.equal(guard.previewInspectionPublication('unbound', flat(current)), undefined);
});
