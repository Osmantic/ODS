// Replays fleet runs in which a non-passing preview inspection gave the model
// no usable next call, so it spent its failure budget on the inspector itself:
// - strixy round 107 (Qwen3.6-35B-A3B, main d4a61f33): flattened steps were
//   rejected with a hint to call tool_describe; the model called
//   tool_call {id:"tool_describe"} twice and was stopped;
// - laptop round 107 (Qwen3.5-9B, main d4a61f33): a missing exact:true, a
//   state-qualified locator on a page that never hid the card, then a real
//   click-handler bug read as "the inspector snapshots before JavaScript";
// - mac-mini round 106 (main b060c6ae): four locator failures on a working
//   page tripped the consecutive-failure fuse;
// - mac-mini round 107 (main d4a61f33): a broken inline-style toggle called
//   "a timing issue";
// - tower2 round 107 (Qwen3-Coder-Next, main d4a61f33, passing): steps that
//   already showed the card change, then a failing extra check of a button.
// OpenClaw 2026.6.33 drops before_agent_finalize revisions after a plugin tool
// call, so the inspection result is the only place a fix can be delivered.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {extractRequestedLiterals, publishedElementOutline} from '../plugin/requested-literals.mjs';
import {PREVIEW_INSPECTION_TOOL, boundVisibilityInspection, requestedVisibilityTransition, statedVisibilityDirection}
  from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, createWorkspacePreviewInspectTool, inspectionPlanHash,
  normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';

const load = name => JSON.parse(fs.readFileSync(new URL(`./${name}`, import.meta.url), 'utf8'));
const STRIXY = load('inspection-recovery-strixy-round107.json');
const LAPTOP = load('inspection-recovery-laptop-round107.json');
const MAC106 = load('inspection-recovery-mac-round106.json');
const MAC107 = load('inspection-recovery-mac-round107.json');
const call = (fixture, number) => fixture.turns[0].calls.find(item => item.call === number);
const BUTTON = {role: 'button', name: 'Show sold out', exact: true};
const OWNER_HEADING = {role: 'heading', name: 'Midnight sold-out concert', exact: true};
const FACTS = 'The inspector loads the published page with its scripts running and dispatches a real click; in Evidence, before is ' +
  'the measurement taken at that step, not a pre-script snapshot.';
const READY = /Next step: call pixel_ods_workspace_preview_inspect \(a tool in your list; call it by name\) with exactly these args: (\{.*?\})(?:; these are your own identifiers and locators in the required shape\.| The target )/;
const readyArgs = text => { const match = READY.exec(text); assert.ok(match, text); return JSON.parse(match[1]); };
const summaryOf = text => text.slice(0, text.indexOf(` ${INSPECTION_SCOPE}`));
const recordedReceipt = recorded => { const {ok, ...receipt} = structuredClone(recorded.details); return receipt; };
const relative = file => file.replace(/^\/?workspace\//, '');

function digest(files) {
  const hash = createHash('sha256');
  for (const [file, content] of Object.entries(files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) {
    const name = Buffer.from(file), data = Buffer.from(content), a = Buffer.alloc(4), b = Buffer.alloc(8);
    a.writeUInt32BE(name.length); b.writeBigUInt64BE(BigInt(data.length));
    hash.update(a).update(name).update(b).update(data);
  }
  return hash.digest('hex');
}
// The published bytes up to a recorded publication, bound to its digest.
function published(fixture, number) {
  const files = {};
  for (const item of fixture.turns[0].calls) {
    if (item.call === number) break;
    const file = relative(item.arguments.path ?? '').split('/').slice(1).join('/');
    if (item.tool === 'write') files[file] = item.arguments.content;
    if (item.tool === 'edit') files[file] = item.arguments.edits.reduce((content, edit) => content.replace(edit.oldText, () => edit.newText), files[file]);
  }
  const receipt = call(fixture, number).details;
  assert.equal(digest(files), receipt.sha256, 'replayed bytes reproduce the host snapshot digest');
  return {files, receipt};
}
// The guard's own derivation of the owner's requirement for that snapshot.
function ownerRequirement(fixture, number) {
  const {prompt} = fixture.turns[0];
  const intent = requestedVisibilityTransition(prompt, extractRequestedLiterals(prompt));
  const {files, receipt} = published(fixture, number);
  const preview = {relativeDirectory: receipt.relativeDirectory, files: Object.keys(files).length, sha256: receipt.sha256,
    bytes: Object.values(files).reduce((sum, content) => sum + Buffer.byteLength(content), 0)};
  const outline = publishedElementOutline(intent.target, preview, {trackedContent: new Map(Object.entries(files)
    .map(([file, content]) => [`${preview.relativeDirectory}/${file}`, content]))});
  assert.ok(outline, 'the outline is read from bytes that reproduce the digest');
  return Object.freeze({target: intent.target, control: intent.control, initiallyHidden: intent.initiallyHidden, outline});
}

// A page as the capsule measures it before and after its control is clicked.
// A role/name locator matches a hidden element only for assert-hidden.
// Recorded plans return their recorded capsule receipt.
const key = locator => JSON.stringify(Object.keys(locator).sort().map(name => [name, locator[name]]));
const page = entries => new Map(entries.map(([locator, state]) => [key(locator), state]));
const shown = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible',
  opacity: '1', hidden: false, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
function capsule(fixture, model) {
  const recorded = fixture.turns[0].calls.filter(item => item.tool === PREVIEW_INSPECTION_TOOL && item.details?.planSha256);
  const plans = [];
  const run = request => {
    plans.push(request);
    const receipt = recorded.find(item => item.details.planSha256 === inspectionPlanHash(request));
    if (receipt) return recordedReceipt(receipt);
    let clicked = false;
    const measure = (locator, action) => {
      const state = (clicked ? model.after : model.before).get(key(locator));
      return state === undefined || (locator.role !== undefined && state === 'hidden' && action !== 'assert-hidden')
        ? {count: 0} : shown(state === 'visible');
    };
    const steps = [];
    for (const [index, step] of request.steps.entries()) {
      const item = {index, ...step, before: measure(step.locator, step.action), stable: true, status: 'failed'};
      if (item.before.count !== 1) item.errorCode = 'no_match';
      else if (step.action === 'click') { clicked = true; Object.assign(item, {after: measure(step.locator, 'click'), status: 'passed'}); }
      else if (item.before.visible === (step.action === 'assert-visible')) item.status = 'passed';
      else item.errorCode = 'visibility_mismatch';
      steps.push(item);
      if (item.status === 'failed') break;
    }
    return {schemaVersion: 1, kind: INSPECTION_KIND,
      status: steps.length === request.steps.length && steps.every(step => step.status === 'passed') ? 'passed' : 'failed',
      siteId: request.siteId, sha256: request.sha256, planSha256: inspectionPlanHash(request), viewport: request.viewport, steps,
      diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE};
  };
  return Object.assign(run, {plans});
}

const envelope = (name, result) => ({content: [{type: 'text', text: JSON.stringify({tool: {id: `openclaw:pixel-ods:${name}`, name}, result})}],
  details: {tool: {id: `openclaw:pixel-ods:${name}`, source: 'openclaw', sourceName: 'pixel-ods', name}, result}});

// The recorded run through the real guard and tool, as in
// inspection_transition_coverage.test.mjs: files are written to a workspace;
// publication receipts and capsule receipts are the recorded ones.
function replay(t, fixture, model) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-recovery-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  const run = capsule(fixture, model);
  const tool = createWorkspacePreviewInspectTool({request: async request => run(request),
    transitionRequirement: (toolCallId, params) => guard.previewInspectionTransition(toolCallId, params),
    guidance: (toolCallId, params) => guard.previewInspectionGuidance(toolCallId, params)});
  const [turn] = fixture.turns;
  const context = {agentId: 'pixel', ...fixture.session, runId: turn.runId};
  guard.observeRun(context, 'pixel', {prompt: turn.prompt}, {workspaceRoot: root});
  const text = value => ({content: [{type: 'text', text: value}]});
  const invoke = (name, args, id, result) => {
    const ctx = {...context, toolName: name, toolCallId: id};
    const event = {toolName: name, runId: context.runId, toolCallId: id, params: args};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    guard.afterToolCall({...event, params: prepared?.params ?? args, result}, ctx);
    guard.toolResultPersist({toolName: name, toolCallId: id, message: {role: 'toolResult', toolName: name, toolCallId: id, ...result}}, ctx);
  };
  const inspect = async (args, id, transport) => {
    if (transport !== 'tool_call') {
      const ctx = {...context, toolName: PREVIEW_INSPECTION_TOOL, toolCallId: id};
      const event = {toolName: PREVIEW_INSPECTION_TOOL, runId: context.runId, toolCallId: id, params: args};
      assert.notEqual(guard.beforeToolCall(event, ctx)?.block, true);
      const result = await tool.execute(id, args);
      guard.afterToolCall({...event, result}, ctx);
      guard.toolResultPersist({toolName: PREVIEW_INSPECTION_TOOL, toolCallId: id,
        message: {role: 'toolResult', toolName: PREVIEW_INSPECTION_TOOL, toolCallId: id, ...result}}, ctx);
      return result;
    }
    const outer = {id: PREVIEW_INSPECTION_TOOL, args};
    const ctx = {...context, toolName: 'tool_call', toolCallId: id};
    const event = {toolName: 'tool_call', runId: context.runId, toolCallId: id, params: outer};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    const inner = await tool.execute(`tool_search_code:${id}:${PREVIEW_INSPECTION_TOOL}:1`, args);
    const result = envelope(PREVIEW_INSPECTION_TOOL, inner);
    guard.afterToolCall({...event, params: prepared?.params ?? outer, result}, ctx);
    guard.toolResultPersist({toolName: 'tool_call', toolCallId: id, message: {role: 'toolResult', toolName: 'tool_call', toolCallId: id, ...result}}, ctx);
    return inner;
  };
  const files = {};
  const disk = (file, content) => {
    files[file.slice(file.indexOf('/') + 1)] = content;
    fs.mkdirSync(path.dirname(path.join(root, file)), {recursive: true});
    fs.writeFileSync(path.join(root, file), content);
  };
  const step = async item => {
    const args = item.arguments.path ? {...item.arguments, path: relative(item.arguments.path)} : item.arguments;
    if (item.tool === 'write') {
      disk(args.path, args.content);
      invoke('write', args, item.id, text(`Successfully wrote ${Buffer.byteLength(args.content)} bytes to ${args.path}`));
    } else if (item.tool === 'read') {
      invoke('read', args, item.id, text(fs.readFileSync(path.join(root, args.path), 'utf8')));
    } else if (item.tool === 'edit') {
      disk(args.path, args.edits.reduce((content, edit) => content.replace(edit.oldText, () => edit.newText), fs.readFileSync(path.join(root, args.path), 'utf8')));
      invoke('edit', args, item.id, text(`Successfully replaced ${args.edits.length} block(s) in ${args.path}.`));
    } else if (item.tool === 'exec') {
      invoke('exec', args, item.id, {...text(item.details.aggregated), details: item.details});
    } else if (item.tool === 'pixel_ods_workspace_preview') {
      assert.equal(digest(files), item.details.sha256, 'replayed bytes reproduce the host snapshot digest');
      const result = {...text('ODS independently published and read back the workspace static files.'), details: item.details};
      if (item.transport === 'tool_call') invoke('tool_call', {id: item.tool, args}, item.id, envelope(item.tool, result));
      else invoke(item.tool, args, item.id, result);
    } else if (item.tool === PREVIEW_INSPECTION_TOOL) {
      return inspect(args, item.id, item.transport);
    } else assert.fail(item.tool);
    return undefined;
  };
  // Replays the recorded calls up to and including the given call number.
  const through = async number => {
    let last;
    for (const item of turn.calls) { if (item.call > number) break; last = await step(item); }
    return last;
  };
  return {guard, run, through, inspect, verification: () => guard.verificationForRun(context.runId)};
}

// Every non-passing result: an error, the unchanged capsule receipt (or the
// invalid_request shape), and no success wording before the scope.
function assertNotPassing(result, receipt) {
  assert.equal(result.isError, true);
  if (receipt) assert.deepEqual(result.details, receipt, 'details stay the capsule receipt');
  else assert.equal(result.details.errorCode, 'invalid_request');
  const text = result.content[0].text;
  assert.doesNotMatch(receipt ? summaryOf(text) : text, /\bpassed\b|\bverified\b|INCOMPLETE/);
  assert.doesNotMatch(text, /tool_describe|through tool_call|timing/);
  return text;
}

// Strixy's page: <article id="midnight-card" hidden>, no display override for
// .card, and app.js removes the hidden attribute when #revealBtn is clicked.
const STRIXY_PAGE = {
  before: page([[BUTTON, 'visible'], [{selector: '#midnight-card'}, 'hidden'], [OWNER_HEADING, 'hidden']]),
  after: page([[{selector: '#midnight-card'}, 'visible'], [OWNER_HEADING, 'visible']]),
};
const STRIXY_PLAN = snapshot => ({siteId: snapshot.siteId, sha256: snapshot.sha256, viewport: {width: 375, height: 667},
  steps: [{action: 'assert-hidden', locator: {selector: '#midnight-card'}}, {action: 'click', locator: BUTTON},
    {action: 'assert-visible', locator: {selector: '#midnight-card'}}]});

for (const transport of ['direct', 'tool_call']) {
  test(`strixy round 107: the rejected flattened call names ready args that pass and bind the proof (next call ${transport})`, async t => {
    const r = replay(t, STRIXY, STRIXY_PAGE);
    const publish = call(STRIXY, 7).details;
    const rejected = await r.through(8);
    assert.equal(r.run.plans.length, 0, 'the inspector was not contacted');
    const text = assertNotPassing(rejected);
    assert.ok(text.includes('At step 1, key "selector": Each step needs only a supported action and locator.'), text);
    const args = readyArgs(text);
    assert.deepEqual(args, STRIXY_PLAN(publish));
    assert.equal(r.verification().status, 'failed');
    // The model's next call is exactly those args; the recorded page passes.
    const corrected = await r.inspect(args, 'corrected', transport === 'tool_call' ? 'tool_call' : undefined);
    assert.equal(corrected.isError, undefined);
    assert.equal(corrected.details.status, 'passed');
    assert.match(corrected.content[0].text, /^Preview inspection passed\. These steps tested opposite visibility states of the same element around a click\./);
    assert.ok(boundVisibilityInspection(args, corrected, publish), 'the visibility proof binds to the published snapshot');
    assert.equal(r.verification().status, 'passed');
  });
}

test('strixy round 107 call 11: the resent flattened steps get the same ready args', async t => {
  const r = replay(t, STRIXY, STRIXY_PAGE);
  await r.through(7);
  const resent = call(STRIXY, 11);
  const result = await r.inspect(resent.arguments, resent.id, 'tool_call');
  assert.deepEqual(readyArgs(assertNotPassing(result)), STRIXY_PLAN(call(STRIXY, 7).details));
});

// Tower2 round 094 asserted the card by role "article", which no lossless
// repair can fix; the guard-bound requirement names the published item as a
// CSS target, which is measured whether or not it is rendered.
test('strixy round 107 snapshot: an unrepairable role gets the guard-bound requirement plan', async t => {
  const r = replay(t, STRIXY, STRIXY_PAGE);
  await r.through(7);
  const publish = call(STRIXY, 7).details;
  const article = {role: 'article', name: 'Midnight sold-out concert', exact: true};
  const params = {siteId: publish.siteId, sha256: publish.sha256, viewport: {width: 375, height: 667},
    steps: [{action: 'assert-hidden', locator: article}, {action: 'click', locator: BUTTON}, {action: 'assert-visible', locator: article}]};
  const text = assertNotPassing(await r.inspect(params, 'article-role', 'tool_call'));
  assert.ok(text.includes('At step 1, key "locator.role": role "article" is not one of '), text);
  const args = readyArgs(text);
  assert.deepEqual(args, STRIXY_PLAN(publish));
  assert.ok(text.includes(' The target "#midnight-card" is the requested "Midnight sold-out concert" heading or the element around it, ' +
    'read from the published source; it holds no other heading and not the control.'), text);
  assert.equal(r.run.plans.length, 0);
  const corrected = await r.inspect(args, 'article-corrected', 'tool_call');
  assert.equal(corrected.details.status, 'passed');
  assert.equal(r.verification().status, 'passed');
});

// Strixy round 107 calls 8 to 11: the failure that ends the response's tool
// use offers no call (the next one is refused); it says to answer instead.
// Integration with #6749: the rejection at the fuse (again-3) is correctable,
// so its charge waits for one corrected attempt and it keeps its ready call;
// the next failure (again-4) is the one that ends tool use.
test('strixy round 107: the failure that ends tool use names no further call', async t => {
  const r = replay(t, STRIXY, STRIXY_PAGE);
  const first = assertNotPassing(await r.through(8));
  assert.ok(readyArgs(first));
  const resent = call(STRIXY, 11).arguments;
  for (const id of ['again-1', 'again-2', 'again-3']) assert.ok(readyArgs(assertNotPassing(await r.inspect(resent, id, 'tool_call'))));
  const last = assertNotPassing(await r.inspect(resent, 'again-4', 'tool_call'));
  assert.ok(last.endsWith(' Requested behavior remains unverified. This failed call was the last one this response allows, so no further tool call ' +
    'can run: do not call any tool. Answer the owner now from the results already returned, and report the requested behavior as unverified.'), last);
  assert.doesNotMatch(last, /Next step|exactly these args|in this shape/);
  const ctx = {agentId: 'pixel', ...STRIXY.session, runId: STRIXY.turns[0].runId, toolName: 'tool_call', toolCallId: 'refused'};
  assert.equal(r.guard.beforeToolCall({toolName: 'tool_call', runId: ctx.runId, toolCallId: 'refused',
    params: {id: PREVIEW_INSPECTION_TOOL, args: resent}}, ctx)?.block, true, 'the guard refuses the next call');
});

// Laptop's first page (site-ba3eb2f6): <article class="event-card sold-out">
// is never hidden (.event-card.hidden only fades), and the handler removes a
// .hidden class the card never had.
const LAPTOP_CARD = {selector: '.event-card.sold-out'}, LAPTOP_CONTROL = {selector: '.show-sold-out-btn'};
const LAPTOP_PAGE = {
  before: page([[{selector: 'h1'}, 'visible'], [LAPTOP_CARD, 'visible'], [LAPTOP_CONTROL, 'visible'], [BUTTON, 'visible']]),
  after: page([[{selector: 'h1'}, 'visible'], [LAPTOP_CARD, 'visible'], [LAPTOP_CONTROL, 'visible']]),
};
const planOn = (snapshot, target, control) => ({siteId: snapshot.siteId, sha256: snapshot.sha256, viewport: {width: 375, height: 667},
  steps: [{action: 'assert-hidden', locator: target}, {action: 'click', locator: control}, {action: 'assert-visible', locator: target}]});
const stepsAfterRepair = text => {
  const match = /republish, then inspect the new snapshot with these steps, using siteId and sha256 from its publication receipt: (\[.*?\])\./.exec(text);
  assert.ok(match, text);
  return JSON.parse(match[1]);
};

test('laptop round 107 call 4: a lossless repair that tests no change gets the owner-requirement plan', async t => {
  const r = replay(t, LAPTOP, LAPTOP_PAGE);
  const publish = call(LAPTOP, 3).details;
  const text = assertNotPassing(await r.through(4));
  assert.ok(text.includes('At step 3, key "locator.exact": '), text);
  // The repaired call-4 steps asserted ".event-card.sold-out.hidden" visible
  // before the click; the owner's plan asserts the card on both sides of it.
  const args = readyArgs(text);
  assert.deepEqual(args, planOn(publish, LAPTOP_CARD, BUTTON));
  assert.doesNotMatch(text, /these are your own identifiers/);
  // Sending them measures the real defect; the owner's wording says hidden first.
  const measured = await r.inspect(args, 'laptop-corrected', 'tool_call');
  const diagnosis = assertNotPassing(measured, measured.details);
  assert.equal(measured.details.steps[0].errorCode, 'visibility_mismatch');
  assert.ok(diagnosis.startsWith('Preview inspection failed. Step 1 (assert-hidden) ".event-card.sold-out" measured display "block", ' +
    'visibility "visible", opacity "1" and rectCount 1 as the page loaded, before any click, so it is visible where this step expects it hidden. ' +
    `The element is visible as the page loads, but the owner's request has it hidden until the click. ${FACTS}`), diagnosis);
  assert.match(diagnosis, /Next step: repair the source so it is hidden as the page loads \(for example, a class or attribute in the published HTML that the CSS hides and the click handler changes\)/);
  assert.deepEqual(stepsAfterRepair(diagnosis), args.steps, 'the steps for the new snapshot, never state-qualified locators');
  assert.equal(READY.exec(diagnosis), null, 'no args: the repair changes the snapshot');
  assert.equal(r.verification().status, 'failed');
});

test('laptop round 107 call 5: the unmatched state-qualified locator gets the stable card locator', async t => {
  const r = replay(t, LAPTOP, LAPTOP_PAGE);
  const recorded = call(LAPTOP, 5);
  const text = assertNotPassing(await r.through(5), recordedReceipt(recorded));
  assert.ok(summaryOf(text).startsWith(summaryOf(recorded.text)), 'the recorded locator feedback is kept');
  const args = readyArgs(text);
  assert.deepEqual(args, planOn(call(LAPTOP, 3).details, LAPTOP_CARD, LAPTOP_CONTROL));
  assert.ok(text.includes('The target ".event-card.sold-out" is your locators ".event-card.sold-out.hidden" and ' +
    '".event-card.sold-out:not(.hidden)" with state qualifiers removed; it matches exactly one element in the published source; ' +
    'it contains the requested "Midnight sold-out concert" heading.'), text);
  assert.ok(!JSON.stringify(args).includes('.event-card.sold-out.hidden'), 'the unmatched locator is never reused');
  assert.ok(normalizeWorkspacePreviewInspectionParams(args));
});

// Call 13 on site-c54d9d87: <article class="event-card sold-out" hidden>, and
// the handler removes a class, not the attribute. The owner-named item as a
// CSS target measures the broken toggle; a role/name heading asserted visible
// would only have matched nothing.
const LAPTOP_ITEM = {selector: 'article.event-card.sold-out'};
test('laptop round 107 call 13: a CSS item target turns the broken toggle into a measured repair', async t => {
  const r = replay(t, LAPTOP, {
    before: page([[{selector: 'h1'}, 'visible'], [LAPTOP_ITEM, 'hidden'], [LAPTOP_CONTROL, 'visible']]),
    after: page([[{selector: 'h1'}, 'visible'], [LAPTOP_ITEM, 'hidden'], [LAPTOP_CONTROL, 'visible']]),
  });
  const recorded = call(LAPTOP, 13), publish = call(LAPTOP, 12).details;
  const text = assertNotPassing(await r.through(13), recordedReceipt(recorded));
  assert.ok(summaryOf(text).startsWith(summaryOf(recorded.text)), 'the recorded locator feedback is kept');
  const args = readyArgs(text);
  assert.deepEqual(args, planOn(publish, LAPTOP_ITEM, LAPTOP_CONTROL));
  assert.ok(text.includes(' The target "article.event-card.sold-out" is the requested "Midnight sold-out concert" heading or the element around it,'), text);
  const measured = await r.inspect(args, 'item', 'tool_call');
  const diagnosis = assertNotPassing(measured, measured.details);
  assert.deepEqual(measured.details.steps.map(step => [step.status, step.errorCode]), [['passed', undefined], ['passed', undefined], ['failed', 'visibility_mismatch']]);
  assert.ok(diagnosis.startsWith('Preview inspection failed. Step 3 (assert-visible) "article.event-card.sold-out" measured display "none", ' +
    'visibility "visible", opacity "1" and rectCount 0 after the click at step 2 (".show-sold-out-btn"), so it is hidden where this step expects it visible. ' +
    FACTS), diagnosis);
  assert.match(diagnosis, /Next step: repair the source \(for example, have the click handler change the same class or attribute that the CSS uses to hide it\)/);
  assert.deepEqual(stepsAfterRepair(diagnosis), args.steps);
});

// Call 24 on site-1f5f2cf8: the visible button is "SHOW SOLD OUT" to the
// capsule (.show-sold-out-btn {text-transform: uppercase}), so the exact
// role/name click matched nothing. The published id replaces it.
test('laptop round 107 call 24: a role/name click that matched no rendered control gets its published id', async t => {
  const r = replay(t, LAPTOP, LAPTOP_PAGE);
  const recorded = call(LAPTOP, 24), publish = call(LAPTOP, 23).details;
  assert.match(recorded.text, /For click, role\/name locators match only rendered elements, so a hidden element is not matched\./);
  const text = assertNotPassing(await r.through(24), recordedReceipt(recorded));
  // The recorded receipt has no load-time names (an older capsule), so hiding is not the only cause named.
  assert.equal(recorded.details.controls, undefined);
  assert.ok(text.startsWith('Preview inspection failed. Step 3 (click) matched no element, so nothing was measured and later steps did not run. ' +
    'For click, role/name locators match only rendered elements, so a hidden element is not matched. It may instead be rendered under another ' +
    'name: the accessible name comes from the rendered text, so CSS text-transform (such as uppercase) changes it. The published source has ' +
    'one button with that name ignoring case: "#showSoldOutBtn".'), text);
  assert.deepEqual(readyArgs(text), planOn(publish, {selector: '.sold-out-card'}, {selector: '#showSoldOutBtn'}));
});

// Call 26 (row 55, result row 56) on site-1f5f2cf8: the handler removes
// .hidden, but .sold-out-card {display:none} hides the card. The repair the
// text names may remove .sold-out-card itself, so the steps for the new
// snapshot name the card by its other classes (laptop call 38 did exactly that,
// then re-sent ".sold-out-card" at call 40, which matched nothing).
test('laptop round 107 row 56: the post-click display:none gets a repair and repair-stable steps', async t => {
  const recorded = call(LAPTOP, 26), receipt = recordedReceipt(recorded);
  assert.match(recorded.text, /^Preview inspection failed\. Requested behavior remains unverified; a failed inspection does not establish a visibility transition\./);
  const r = replay(t, LAPTOP, LAPTOP_PAGE);
  await r.through(23);
  const text = assertNotPassing(await r.inspect(recorded.arguments, recorded.id, 'tool_call'), receipt);
  assert.ok(text.startsWith('Preview inspection failed. Step 4 (assert-visible) ".sold-out-card" measured display "none", visibility "visible", ' +
    'opacity "1" and rectCount 0 after the click at step 3 (".show-sold-out-btn"), so it is hidden where this step expects it visible. ' +
    `${FACTS} Requested behavior remains unverified; a failed inspection does not establish a visibility transition. ` +
    'Next step: repair the source (for example, have the click handler change the same class or attribute that the CSS uses to hide it), ' +
    'republish, then inspect the new snapshot with these steps, using siteId and sha256 from its publication receipt: '), text);
  assert.deepEqual(stepsAfterRepair(text), [{action: 'assert-hidden', locator: LAPTOP_ITEM}, {action: 'click', locator: LAPTOP_CONTROL},
    {action: 'assert-visible', locator: LAPTOP_ITEM}]);
  // Evidence and palette are unchanged.
  assert.equal(text.slice(text.indexOf(` ${INSPECTION_SCOPE}`)), recorded.text.slice(recorded.text.indexOf(` ${INSPECTION_SCOPE}`)));
  // Without a bound requirement the steps are not guessed: ".sold-out-card" may be what the repair removes.
  const plain = createWorkspacePreviewInspectTool({request: async () => structuredClone(receipt)});
  assert.ok((await plain.execute('row-56', recorded.arguments)).content[0].text.includes('Next step: repair the source (for example, ' +
    'have the click handler change the same class or attribute that the CSS uses to hide it), republish, then inspect the new snapshot, ' +
    `asserting one unchanging locator of the element, such as an id, before the click and after it. ${INSPECTION_SCOPE}`));
});
// Mac round 106: <article class="event-card hidden"> (display:none) with the
// script removing .hidden on the .reveal-btn click; the heading plan passes.
const MAC106_PAGE = {
  before: page([[{selector: '.reveal-btn'}, 'visible'], [BUTTON, 'visible'], [OWNER_HEADING, 'hidden']]),
  after: page([[{selector: '.reveal-btn'}, 'visible'], [OWNER_HEADING, 'visible']]),
};
test('mac round 106: calls 9-12 each get ready args for the published heading, never the failing locator', async () => {
  const requirement = ownerRequirement(MAC106, 8), publish = call(MAC106, 8).details;
  assert.deepEqual({target: requirement.target, control: requirement.control, initiallyHidden: requirement.initiallyHidden},
    {target: 'Midnight sold-out concert', control: {role: 'button', name: 'Show sold out'}, initiallyHidden: true});
  const model = capsule(MAC106, MAC106_PAGE);
  for (const [number, control] of [[9, BUTTON], [10, {selector: '.reveal-btn'}], [11, {selector: '.reveal-btn'}], [12, {selector: '.reveal-btn'}]]) {
    const recorded = call(MAC106, number), receipt = recordedReceipt(recorded);
    const failing = receipt.steps.at(-1);
    assert.ok(['no_match', 'invalid_selector'].includes(failing.errorCode));
    const asked = [];
    const tool = createWorkspacePreviewInspectTool({request: async request => model(request),
      transitionRequirement: (id, params) => { asked.push([id, params]); return requirement; }});
    const result = await tool.execute(`call-${number}`, recorded.arguments);
    assert.deepEqual(asked, [[`call-${number}`, recorded.arguments]]);
    const text = assertNotPassing(result, receipt);
    const args = readyArgs(text);
    assert.deepEqual(args, {siteId: publish.siteId, sha256: publish.sha256, viewport: recorded.arguments.viewport,
      steps: [{action: 'assert-hidden', locator: OWNER_HEADING}, {action: 'click', locator: control}, {action: 'assert-visible', locator: OWNER_HEADING}]});
    assert.ok(text.includes('The target is the heading "Midnight sold-out concert" of the requested "Midnight sold-out concert" element, ' +
      'read from the published source;'), text);
    assert.ok(!JSON.stringify(args.steps).includes(JSON.stringify(failing.locator)), 'the failing locator is never repeated');
    assert.notDeepEqual(args.steps, recorded.arguments.steps);
    // The heading plan passes on the recorded page.
    const next = await tool.execute(`next-${number}`, args);
    assert.equal(next.details.status, 'passed', next.content[0].text);
  }
  // Call 10's attribute-equals class selector also gets the whole-string explanation.
  const tool = createWorkspacePreviewInspectTool({request: async request => model(request)});
  const plain = (await tool.execute('call-10', call(MAC106, 10).arguments)).content[0].text;
  assert.ok(plain.includes('An attribute selector [class="..."] matches only an element whose whole class attribute is exactly that string; ' +
    'for an element with classes a and b use .a.b, or an id.'), plain);
  assert.equal(READY.exec(plain), null, 'without the owner requirement there is no ready plan');
});

// Mac round 107: the handler tests hiddenCard.style.display === 'none', empty
// on load, so the first click sets display:none.
test('mac round 107: the recorded call-8 receipt names step 8 after the click on step 7', async () => {
  const requirement = ownerRequirement(MAC107, 2);
  for (const number of [3, 8]) {
    const recorded = call(MAC107, number), receipt = recordedReceipt(recorded);
    for (const bound of [false, true]) {
      const tool = createWorkspacePreviewInspectTool({request: async () => structuredClone(receipt),
        ...(bound ? {transitionRequirement: () => requirement, guidance: () => ({direction: 'hidden'})} : {})});
      const text = assertNotPassing(await tool.execute(`call-${number}`, recorded.arguments), receipt);
      assert.ok(text.startsWith('Preview inspection failed. Step 8 (assert-visible) ".event-card.hidden" measured display "none", ' +
        'visibility "visible", opacity "1" and rectCount 0 after the click at step 7 (".reveal-btn"), so it is hidden where this step expects it visible. ' +
        FACTS), text);
      assert.match(text, /have the click handler change the same class or attribute that the CSS uses to hide it\), republish/);
      // ".event-card" alone names all three cards: without the published outline no steps are guessed.
      if (!bound) assert.match(text, /republish, then inspect the new snapshot, asserting one unchanging locator of the element, such as an id, before the click and after it\./);
      else assert.deepEqual(stepsAfterRepair(text), [{action: 'assert-hidden', locator: {role: 'heading', name: 'Midnight Sold-out Concert', exact: true}},
        {action: 'click', locator: {selector: '.reveal-btn'}}, {action: 'assert-visible', locator: {role: 'heading', name: 'Midnight Sold-out Concert', exact: true}}]);
    }
  }
  assert.match(call(MAC107, 8).arguments.steps[5].action, /assert-hidden/, 'the card was hidden before the click');
});

// Tower2 round 107 (passing) call 20: steps 4, 6 and 7 already showed the card
// hidden, then the click, then visible; step 8 was the model's own extra check
// of the renamed button. The passing steps are offered, never a site repair.
const TOWER2 = load('inspection-recovery-tower2-round107.json');
const tower2Call = number => TOWER2.turns[0].calls.find(item => item.call === number);
const TOWER2_REQUIREMENT = Object.freeze({target: 'Midnight sold-out concert', control: {role: 'button', name: 'Show sold out'}, initiallyHidden: true});
test('tower2 round 107 call 20: a failing extra check after a proven change gets the passing steps', async () => {
  const recorded = tower2Call(20), receipt = recordedReceipt(recorded);
  assert.match(recorded.text, /^Preview inspection failed\. Requested behavior remains unverified;/);
  for (const guidance of [{direction: 'hidden'}, {}]) {
    const tool = createWorkspacePreviewInspectTool({request: async () => structuredClone(receipt),
      transitionRequirement: () => TOWER2_REQUIREMENT, guidance: () => guidance});
    const text = assertNotPassing(await tool.execute('call-20', recorded.arguments), receipt);
    assert.ok(text.includes('Step 8 (assert-hidden) button "Hide sold out" measured display "flex", visibility "visible", opacity "1" and rectCount 1 ' +
      'after the click at step 6 (button "Show sold out"), so it is visible where this step expects it hidden. Steps 4, 6 and 7 before it already ' +
      'measured "#midnight-concert" hidden before the click and visible after it; step 8 checks another element.'), text);
    assert.doesNotMatch(text, /repair the source/);
    const match = /with exactly these args: (\{.*?\}) These are your steps 1 to 7, unchanged\. Change the site only if the owner asked for button "Hide sold out" to be hidden after the click\./.exec(text);
    assert.ok(match, text);
    const args = JSON.parse(match[1]);
    assert.deepEqual(args, {...recorded.arguments, steps: recorded.arguments.steps.slice(0, 7)});
    // Those steps passed on this snapshot: the same capsule measurements, minus step 8.
    const passing = {...receipt, status: 'passed', planSha256: inspectionPlanHash(normalizeWorkspacePreviewInspectionParams(args)),
      steps: receipt.steps.slice(0, 7)};
    const next = await createWorkspacePreviewInspectTool({request: async () => structuredClone(passing)}).execute('next', args);
    assert.equal(next.details.status, 'passed');
    assert.ok(boundVisibilityInspection(args, next, {siteId: args.siteId, sha256: args.sha256}));
  }
});

// Call 21 clicked the button by its old name after the first click renamed it.
test('tower2 round 107 call 21: a control renamed by an earlier click gets that cause', async () => {
  const recorded = tower2Call(21), receipt = recordedReceipt(recorded);
  const text = assertNotPassing(await createWorkspacePreviewInspectTool({request: async () => structuredClone(receipt)})
    .execute('call-21', recorded.arguments), receipt);
  assert.ok(text.startsWith('Preview inspection failed. Step 8 (click) matched no element, so nothing was measured and later steps did not run. ' +
    'For click, role/name locators match only rendered elements, so a hidden element is not matched. It may instead be rendered under another ' +
    'name: the accessible name comes from the rendered text, so CSS text-transform (such as uppercase), or an earlier click that changed its text, ' +
    'changes it. Copy the exact role and accessible name'), text);
});

// The owner's stated direction decides only where the owner's own wording
// fixed it (reviewer probes P4, P5 and P9 of PR #6748).
test('only the owner\'s explicit wording states which state comes first', () => {
  const fleet = STRIXY.turns[0].prompt;
  assert.equal(statedVisibilityDirection(fleet), 'hidden');
  assert.equal(statedVisibilityDirection('Provide an accessible button named exactly "Hide details" that hides the Details panel when clicked.'), 'visible');
  assert.equal(statedVisibilityDirection('Add a FAQ where clicking a question expands its answer.'), 'hidden');
  for (const open of ['Build a page with a "Menu" button that toggles the navigation panel.',
    'Clicking the button shows the panel; clicking again hides it.', 'Add a button that shows or hides the details.',
    'Create a static page with a heading titled "Hello".']) assert.equal(statedVisibilityDirection(open), undefined, open);
  // requestedVisibilityTransition keeps its hidden-first default for all of them.
  for (const prompt of ['Build a page with a "Menu" button that toggles the navigation panel.', fleet])
    assert.equal(requestedVisibilityTransition(prompt, extractRequestedLiterals(prompt)).initiallyHidden, true);
});

const withPrompt = (fixture, prompt) => ({...fixture, turns: [{...fixture.turns[0], prompt}]});
const STRIXY_REVEAL = 'Initially hide the entire Midnight sold-out concert card. Provide an accessible button named exactly "Show sold out" that reveals that card when clicked.';
test('a hide-on-click owner gets a repair, never the reverse plan, on a page that starts hidden (P5)', async t => {
  const prompt = STRIXY.turns[0].prompt.replace(STRIXY_REVEAL,
    'Show the entire Midnight sold-out concert card on load. Provide an accessible button named exactly "Show sold out" that hides that card when clicked.');
  assert.notEqual(prompt, STRIXY.turns[0].prompt);
  const r = replay(t, withPrompt(STRIXY, prompt), STRIXY_PAGE);
  await r.through(7);
  const publish = call(STRIXY, 7).details;
  const owner = {...STRIXY_PLAN(publish), steps: [{action: 'assert-visible', locator: {selector: '#midnight-card'}}, {action: 'click', locator: BUTTON},
    {action: 'assert-hidden', locator: {selector: '#midnight-card'}}]};
  const result = await r.inspect(owner, 'owner-direction', 'tool_call');
  const text = assertNotPassing(result, result.details);
  assert.ok(text.includes('so it is hidden where this step expects it visible. The element is hidden as the page loads, but the owner\'s request has it ' +
    'visible until the click.'), text);
  assert.match(text, /Next step: repair the source so it is visible as the page loads/);
  assert.equal(READY.exec(text), null, 'the hidden-first plan is not offered');
  assert.equal(r.verification().status, 'failed');
});

test('a toggle owner gets both orders, and the owner is never said to have asked for one (P9)', async t => {
  const prompt = STRIXY.turns[0].prompt.replace(STRIXY_REVEAL, 'Provide an accessible button named exactly "Show sold out" that toggles the Midnight sold-out concert card.');
  const r = replay(t, withPrompt(STRIXY, prompt), STRIXY_PAGE);
  await r.through(7);
  const publish = call(STRIXY, 7).details;
  const visibleFirst = {...STRIXY_PLAN(publish), steps: [{action: 'assert-visible', locator: {selector: '#midnight-card'}}, {action: 'click', locator: BUTTON},
    {action: 'assert-hidden', locator: {selector: '#midnight-card'}}]};
  const result = await r.inspect(visibleFirst, 'toggle', 'tool_call');
  const text = assertNotPassing(result, result.details);
  assert.ok(text.includes('The element is hidden as the page loads; the owner\'s request does not say whether it starts hidden or visible.'), text);
  assert.doesNotMatch(text, /the owner's request has it|the owner asked for it hidden initially/);
  assert.match(text, /Next step: if the owner asked for it visible until the click, repair the source so it is visible as the page loads/);
  const otherwise = /Otherwise the page may start hidden: call pixel_ods_workspace_preview_inspect \(a tool in your list; call it by name\) with exactly these args: (\{.*?\}) The target /.exec(text);
  assert.deepEqual(JSON.parse(otherwise[1]), STRIXY_PLAN(publish));
});

test('a hide-on-click owner whose page starts visible gets the owner-order plan on the same snapshot (P4)', async () => {
  const params = {siteId: 'site-' + 'a'.repeat(24), sha256: 'a'.repeat(64), viewport: {width: 375, height: 667},
    steps: [{action: 'assert-hidden', locator: {selector: '#details'}}, {action: 'click', locator: {role: 'button', name: 'Hide details', exact: true}},
      {action: 'assert-visible', locator: {selector: '#details'}}]};
  const model = capsule(STRIXY, {before: page([[{selector: '#details'}, 'visible'], [{role: 'button', name: 'Hide details', exact: true}, 'visible']]),
    after: page([[{selector: '#details'}, 'hidden']])});
  const requirement = {control: {role: 'button', name: 'Hide details'}, initiallyHidden: false};
  const tool = createWorkspacePreviewInspectTool({request: async request => model(request), transitionRequirement: () => requirement,
    guidance: () => ({direction: 'visible'})});
  const result = await tool.execute('p4', params);
  const text = assertNotPassing(result, result.details);
  assert.ok(text.includes('The element is visible as the page loads, as the owner\'s request has it before the click, so these steps assert the reverse order; ' +
    'the site needs no change for this.'), text);
  const args = readyArgs(text);
  assert.deepEqual(args.steps.map(step => step.action), ['assert-visible', 'click', 'assert-hidden']);
  assert.equal((await tool.execute('p4-next', args)).details.status, 'passed');
});

// With load-time control names (PR #6741's capsule), laptop round 107's
// repaired page reports the rendered button named exactly "Show sold out"
// while the role/name click matched nothing (text-transform: uppercase): the
// same steps are offered with the button's published id. Names that differ
// keep #6741's repair-or-rename feedback and get no ready call.
test('laptop round 107: load-time names that show the requested name get the same steps with the published control', async () => {
  const {files, receipt: publish} = published(LAPTOP, 3);
  const html = files['index.html'].replace('<article class="event-card sold-out">', '<article class="event-card sold-out hidden">');
  assert.notEqual(html, files['index.html']);
  const sha256 = digest({'index.html': html}), preview = {relativeDirectory: publish.relativeDirectory, files: 1, sha256, bytes: Buffer.byteLength(html)};
  const outline = publishedElementOutline('Midnight sold-out concert', preview,
    {trackedContent: new Map([[`${publish.relativeDirectory}/index.html`, html]])});
  const requirement = {target: 'Midnight sold-out concert', control: {role: 'button', name: 'Show sold out'}, initiallyHidden: true, outline};
  const params = {siteId: `site-${sha256.slice(0, 24)}`, sha256, viewport: {width: 375, height: 667},
    steps: [{action: 'assert-hidden', locator: LAPTOP_CARD}, {action: 'click', locator: BUTTON}, {action: 'assert-visible', locator: LAPTOP_CARD}]};
  const request = normalizeWorkspacePreviewInspectionParams(structuredClone(params));
  const failed = controls => ({schemaVersion: 1, kind: INSPECTION_KIND, status: 'failed', siteId: params.siteId, sha256, planSha256: inspectionPlanHash(request),
    viewport: params.viewport, steps: [{index: 0, ...params.steps[0], before: shown(false), stable: true, status: 'passed'},
      {index: 1, ...params.steps[1], before: {count: 0}, stable: true, status: 'failed', errorCode: 'no_match'}],
    diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], controls, scope: INSPECTION_SCOPE});
  const run = async controls => (await createWorkspacePreviewInspectTool({request: async () => failed(controls),
    transitionRequirement: () => requirement, guidance: () => ({direction: 'hidden'})}).execute('names', params)).content[0].text;
  const present = await run({count: 1, items: [{role: 'button', name: 'Show sold out', visible: true, source: 'content'}]});
  assert.ok(present.includes('a rendered button was named exactly "Show sold out", so that name is on the page;'), present);
  const match = /with exactly these args: (\{.*?\}) These are your steps with button "Show sold out" replaced by "#showSoldOutBtn", the published CSS locator of that button\./.exec(present);
  assert.ok(match, present);
  assert.deepEqual(JSON.parse(match[1]), {...params, steps: [params.steps[0], {action: 'click', locator: {selector: '#showSoldOutBtn'}}, params.steps[2]]});
  const renamed = await run({count: 1, items: [{role: 'button', name: 'Reveal the sold-out card', visible: true, source: 'aria-label', text: 'Show sold out'}]});
  assert.match(renamed, /If the owner required that exact name, the page does not meet it:/);
  assert.equal(READY.exec(renamed), null);
});
