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
//   "a timing issue".
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
import {PREVIEW_INSPECTION_TOOL, boundVisibilityInspection, requestedVisibilityTransition} from '../plugin/preview-interaction-assurance.mjs';
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
    transitionRequirement: (toolCallId, params) => guard.previewInspectionTransition(toolCallId, params)});
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
// repair can fix; the guard-bound requirement names the published heading.
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
  assert.deepEqual(args, {...params, steps: [{action: 'assert-hidden', locator: OWNER_HEADING}, {action: 'click', locator: BUTTON},
    {action: 'assert-visible', locator: OWNER_HEADING}]});
  assert.ok(text.includes(' The target is the heading "Midnight sold-out concert" of the requested "Midnight sold-out concert" element, read from the published source;'), text);
  assert.equal(r.run.plans.length, 0);
  const corrected = await r.inspect(args, 'article-corrected', 'tool_call');
  assert.equal(corrected.details.status, 'passed');
  assert.equal(r.verification().status, 'passed');
});

// Laptop's first page (site-ba3eb2f6): <article class="event-card sold-out">
// is never hidden (.event-card.hidden only fades), and the handler removes a
// .hidden class the card never had.
const LAPTOP_CARD = {selector: '.event-card.sold-out'}, LAPTOP_CONTROL = {selector: '.show-sold-out-btn'};
const LAPTOP_PAGE = {
  before: page([[{selector: 'h1'}, 'visible'], [LAPTOP_CARD, 'visible'], [LAPTOP_CONTROL, 'visible'], [BUTTON, 'visible']]),
  after: page([[{selector: 'h1'}, 'visible'], [LAPTOP_CARD, 'visible'], [LAPTOP_CONTROL, 'visible']]),
};
const laptopPlan = snapshot => ({siteId: snapshot.siteId, sha256: snapshot.sha256, viewport: {width: 375, height: 667},
  steps: [{action: 'assert-hidden', locator: LAPTOP_CARD}, {action: 'click', locator: LAPTOP_CONTROL}, {action: 'assert-visible', locator: LAPTOP_CARD}]});

test('laptop round 107: call 13 gets ready args for the stable card locator; the page then gets a measured diagnosis', async t => {
  const r = replay(t, LAPTOP, LAPTOP_PAGE);
  const publish = call(LAPTOP, 3).details;
  // Call 4 (row 11): the button locator without exact:true, repaired losslessly.
  const lossless = readyArgs(assertNotPassing(await r.through(4)));
  assert.deepEqual(lossless.steps[2], {action: 'click', locator: BUTTON});
  // Call 5 (row 13): ".event-card.sold-out.hidden" matched nothing on site-ba3eb2f6.
  const recorded = call(LAPTOP, 5);
  const failed = await r.through(5);
  const text = assertNotPassing(failed, recordedReceipt(recorded));
  assert.ok(summaryOf(text).startsWith(summaryOf(recorded.text)), 'the recorded locator feedback is kept');
  const args = readyArgs(text);
  assert.deepEqual(args, laptopPlan(publish));
  assert.ok(text.includes('The target ".event-card.sold-out" is your locators ".event-card.sold-out.hidden" and ' +
    '".event-card.sold-out:not(.hidden)" with state qualifiers removed; it matches exactly one element in the published source; ' +
    'it contains the requested "Midnight sold-out concert" heading.'), text);
  assert.ok(!JSON.stringify(args).includes('.event-card.sold-out.hidden'), 'the unmatched locator is never reused');
  assert.ok(normalizeWorkspacePreviewInspectionParams(args));
  // Sending them measures the real defect: the card is visible as the page loads.
  const measured = await r.inspect(args, 'laptop-corrected', 'tool_call');
  const diagnosis = assertNotPassing(measured, measured.details);
  assert.equal(measured.details.steps[0].errorCode, 'visibility_mismatch');
  assert.ok(diagnosis.startsWith('Preview inspection failed. Step 1 (assert-hidden) ".event-card.sold-out" measured display "block", ' +
    'visibility "visible", opacity "1" and rectCount 1 as the page loaded, before any click, so it is visible where this step expects it hidden. ' +
    `The element is visible as the page loads; the owner asked for it hidden initially. ${FACTS}`), diagnosis);
  assert.match(diagnosis, /Next step: repair the source so it is hidden as the page loads .*, republish, then inspect the new snapshot with the same steps\./);
  assert.equal(READY.exec(diagnosis), null, 'no args: the repair changes the snapshot');
  assert.equal(r.verification().status, 'failed');
});

test('laptop round 107: the lossless call-4 args lead to the same ready args', async t => {
  const r = replay(t, LAPTOP, LAPTOP_PAGE);
  const lossless = readyArgs((await r.through(4)).content[0].text);
  const failed = await r.inspect(lossless, 'lossless', 'tool_call');
  assert.equal(failed.details.steps.at(-1).errorCode, 'no_match');
  assert.deepEqual(readyArgs(failed.content[0].text), {...laptopPlan(call(LAPTOP, 3).details),
    steps: [{action: 'assert-hidden', locator: LAPTOP_CARD}, {action: 'click', locator: BUTTON}, {action: 'assert-visible', locator: LAPTOP_CARD}]});
});

// Call 26 (row 55, result row 56) on site-1f5f2cf8: the handler removes .hidden,
// but .sold-out-card {display:none} hides the card.
test('laptop round 107 row 56: the recorded receipt names the post-click display:none and the repair', async () => {
  const recorded = call(LAPTOP, 26), receipt = recordedReceipt(recorded);
  let consulted = 0;
  const tool = createWorkspacePreviewInspectTool({request: async () => structuredClone(receipt), transitionRequirement: () => { consulted += 1; }});
  const result = await tool.execute('row-56', recorded.arguments);
  const text = assertNotPassing(result, receipt);
  assert.match(recorded.text, /^Preview inspection failed\. Requested behavior remains unverified; a failed inspection does not establish a visibility transition\./);
  assert.ok(text.startsWith('Preview inspection failed. Step 4 (assert-visible) ".sold-out-card" measured display "none", visibility "visible", ' +
    'opacity "1" and rectCount 0 after the click at step 3 (".show-sold-out-btn"), so it is hidden where this step expects it visible. ' +
    `${FACTS} Requested behavior remains unverified; a failed inspection does not establish a visibility transition. ` +
    'Next step: repair the source (for example, have the click handler change the same class or attribute that the CSS uses to hide it), ' +
    `republish, then inspect the new snapshot with the same steps. ${INSPECTION_SCOPE}`), text);
  assert.equal(consulted, 0, 'a post-click measurement needs no owner requirement');
  // Evidence and palette are unchanged.
  assert.equal(text.slice(text.indexOf(` ${INSPECTION_SCOPE}`)), recorded.text.slice(recorded.text.indexOf(` ${INSPECTION_SCOPE}`)));
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
  for (const number of [3, 8]) {
    const recorded = call(MAC107, number), receipt = recordedReceipt(recorded);
    const tool = createWorkspacePreviewInspectTool({request: async () => structuredClone(receipt)});
    const text = assertNotPassing(await tool.execute(`call-${number}`, recorded.arguments), receipt);
    assert.ok(text.startsWith('Preview inspection failed. Step 8 (assert-visible) ".event-card.hidden" measured display "none", ' +
      'visibility "visible", opacity "1" and rectCount 0 after the click at step 7 (".reveal-btn"), so it is hidden where this step expects it visible. ' +
      FACTS), text);
    assert.match(text, /have the click handler change the same class or attribute that the CSS uses to hide it\), republish/);
  }
  assert.match(call(MAC107, 8).arguments.steps[5].action, /assert-hidden/, 'the card was hidden before the click');
});
