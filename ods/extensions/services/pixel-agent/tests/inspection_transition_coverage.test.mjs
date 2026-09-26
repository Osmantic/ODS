// Replays two fleet runs in which the owner asked for the Midnight sold-out
// concert card to start hidden and a button named exactly "Show sold out" to
// reveal it, and the model's passing inspection never asserted one locator on
// both sides of the click:
// - laptop round 100 (Qwen3.5-9B, main b060c6ae, Tool Search transport):
//   assert-visible(button), click(button), assert-visible(".event-card.sold-out.revealed");
// - tower2 round 102 (Qwen3-Coder-Next, main b060c6ae, direct calls):
//   assert-hidden(".sold-out-card.hidden"), click(button), assert-visible(".sold-out-card:not(.hidden)").
// The tool answered "Preview inspection passed" with a caveat, each model
// claimed the card was verified, and finalization failed the delivery.
// OpenClaw 2026.6.33 drops before_agent_finalize revisions after a plugin
// tool call, so the inspection result itself must say what to run next.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {extractRequestedLiterals, publishedElementOutline} from '../plugin/requested-literals.mjs';
import {PREVIEW_INSPECTION_TOOL, boundVisibilityInspection, requestedVisibilityTransition, inheritedVisibilityTransition}
  from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, TRANSITION_UNTESTED, createWorkspacePreviewInspectTool, inspectionPlanHash,
  normalizeWorkspacePreviewInspectionParams, transitionCorrection} from '../plugin/workspace-preview-inspect.mjs';
import {STATE_CLASS_WORDS, chooseTransitionTarget, stateFreeSelector} from '../plugin/inspection-target.mjs';

const load = name => JSON.parse(fs.readFileSync(new URL(`./${name}`, import.meta.url), 'utf8'));
const LAPTOP = load('inspection-transition-laptop-round100.json');
const TOWER2 = load('inspection-transition-tower2-round102.json');
const calls = (fixture, tool) => fixture.turns.flatMap(turn => turn.calls.filter(call => call.tool === tool));
const [LAPTOP_PUBLISH, LAPTOP_PUBLISH_UPDATE] = calls(LAPTOP, 'pixel_ods_workspace_preview');
const [LAPTOP_INSPECT, LAPTOP_INSPECT_UPDATE] = calls(LAPTOP, PREVIEW_INSPECTION_TOOL);
const [, TOWER2_PUBLISH, TOWER2_PUBLISH_UPDATE] = calls(TOWER2, 'pixel_ods_workspace_preview');
const [TOWER2_NO_MATCH, TOWER2_INSPECT, TOWER2_UPDATE_NO_MATCH, TOWER2_INSPECT_UPDATE] = calls(TOWER2, PREVIEW_INSPECTION_TOOL);
const OWNER_PHRASE = 'Midnight sold-out concert';
const BUTTON = {role: 'button', name: 'Show sold out', exact: true};
const HEADING = {role: 'heading', name: 'Midnight Sold-Out Concert', exact: true};
const LAPTOP_TARGET = {selector: '.event-card.sold-out'};
const TOWER2_TARGET = {selector: '#midnight-card'};
const transition = target => [{action: 'assert-hidden', locator: target}, {action: 'click', locator: BUTTON},
  {action: 'assert-visible', locator: target}];
const INCOMPLETE = 'Preview inspection INCOMPLETE - not verified.';
const KEEP = 'Do not change the site only for this check, and do not say the interaction works until an inspection with these steps passes.';
const relative = file => file.replace(/^\/workspace\//, '');

function digest(files) {
  const hash = createHash('sha256');
  for (const [file, content] of Object.entries(files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) {
    const name = Buffer.from(file), data = Buffer.from(content), a = Buffer.alloc(4), b = Buffer.alloc(8);
    a.writeUInt32BE(name.length); b.writeBigUInt64BE(BigInt(data.length));
    hash.update(a).update(name).update(b).update(data);
  }
  return hash.digest('hex');
}

// Each recorded page as its capsule measured it, before and after the click:
// laptop hides the card with visibility:hidden until .revealed is added;
// tower2 hides it with .sold-out-card.hidden {display:none} until the script
// removes .hidden, and renames the button "Sold out revealed". A role/name
// locator matches a hidden element only for assert-hidden. Recorded plans
// return the recorded capsule receipt.
const key = locator => JSON.stringify(Object.keys(locator).sort().map(name => [name, locator[name]]));
const page = entries => new Map(entries.map(([locator, state]) => [key(locator), state]));
const LAPTOP_PAGE = {
  before: page([[BUTTON, 'visible'], [LAPTOP_TARGET, 'hidden'], [HEADING, 'hidden']]),
  after: page([[BUTTON, 'visible'], [LAPTOP_TARGET, 'visible'], [{selector: '.event-card.sold-out.revealed'}, 'visible'], [HEADING, 'visible']]),
};
const TOWER2_PAGE = {
  before: page([[BUTTON, 'visible'], [TOWER2_TARGET, 'hidden'], [{selector: '.sold-out-card'}, 'hidden'],
    [{selector: '.sold-out-card.hidden'}, 'hidden'], [HEADING, 'hidden']]),
  after: page([[TOWER2_TARGET, 'visible'], [{selector: '.sold-out-card'}, 'visible'],
    [{selector: '.sold-out-card:not(.hidden)'}, 'visible'], [HEADING, 'visible']]),
};
const shown = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible',
  opacity: '1', hidden: false, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
function capsule(fixture, model) {
  const recorded = calls(fixture, PREVIEW_INSPECTION_TOOL);
  return request => {
    const receipt = recorded.find(call => call.details.planSha256 === inspectionPlanHash(request));
    // A recorded failure carries ok:false from the plugin's Pi error contract
    // (pi-tool-result.mjs), added after the capsule; the capsule never sends it.
    if (receipt) { const {ok, ...details} = structuredClone(receipt.details); return details; }
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
      else if (step.action === 'click') {
        if (item.before.visible) { clicked = true; Object.assign(item, {after: measure(step.locator, 'click'), status: 'passed'}); }
        else item.errorCode = 'click_failed';
      } else if (item.before.visible === (step.action === 'assert-visible')) item.status = 'passed';
      else item.errorCode = 'visibility_mismatch';
      steps.push(item);
      if (item.status === 'failed') break;
    }
    return {schemaVersion: 1, kind: INSPECTION_KIND,
      status: steps.length === request.steps.length && steps.every(step => step.status === 'passed') ? 'passed' : 'failed',
      siteId: request.siteId, sha256: request.sha256, planSha256: inspectionPlanHash(request), viewport: request.viewport, steps,
      diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE};
  };
}

const envelope = (name, result) => ({content: [{type: 'text', text: JSON.stringify({tool: {id: `openclaw:pixel-ods:${name}`, name}, result})}],
  details: {tool: {id: `openclaw:pixel-ods:${name}`, source: 'openclaw', sourceName: 'pixel-ods', name}, result}});

// The recorded session through the real guard and tool: files are written to
// a workspace, publication receipts and capsule receipts are the recorded ones.
function replay(t, fixture, model, {nestedHooks = false, sessionId, sessionKey} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-transition-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  const tool = createWorkspacePreviewInspectTool({request: async request => capsule(fixture, model)(request),
    transitionRequirement: (toolCallId, params) => guard.previewInspectionTransition(toolCallId, params)});
  const files = {};
  let context;
  const invoke = (name, args, id, result, {blocked = false} = {}) => {
    const ctx = {...context, toolName: name, toolCallId: id};
    const event = {toolName: name, runId: context.runId, toolCallId: id, params: args};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.equal(prepared?.block === true, blocked, prepared?.blockReason);
    if (blocked) return prepared;
    guard.afterToolCall({...event, params: prepared?.params ?? args, result}, ctx);
    guard.toolResultPersist({toolName: name, toolCallId: id, message: {role: 'toolResult', toolName: name, toolCallId: id, ...result}}, ctx);
    return prepared;
  };
  const disk = (file, content) => {
    files[file.slice(file.indexOf('/') + 1)] = content;
    fs.mkdirSync(path.dirname(path.join(root, file)), {recursive: true});
    fs.writeFileSync(path.join(root, file), content);
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
    const child = `tool_search_code:${id}:${PREVIEW_INSPECTION_TOOL}:1`;
    const childCtx = {...context, toolName: PREVIEW_INSPECTION_TOOL, toolCallId: child};
    const childEvent = {toolName: PREVIEW_INSPECTION_TOOL, runId: context.runId, toolCallId: child, params: args};
    if (nestedHooks) assert.notEqual(guard.beforeToolCall(childEvent, childCtx)?.block, true);
    const inner = await tool.execute(child, args);
    if (nestedHooks) guard.afterToolCall({...childEvent, result: inner}, childCtx);
    const result = envelope(PREVIEW_INSPECTION_TOOL, inner);
    guard.afterToolCall({...event, params: prepared?.params ?? outer, result}, ctx);
    guard.toolResultPersist({toolName: 'tool_call', toolCallId: id, message: {role: 'toolResult', toolName: 'tool_call', toolCallId: id, ...result}}, ctx);
    return inner;
  };
  // Runs a recorded call; returns the tool result of a recorded inspection.
  const run = async call => {
    const args = call.arguments.path ? {...call.arguments, path: relative(call.arguments.path)} : call.arguments;
    const text = value => ({content: [{type: 'text', text: value}]});
    if (call.blocked) {
      const refused = invoke(call.tool, args, call.id, undefined, {blocked: true});
      assert.equal(refused.blockReason, call.text, 'the recorded refusal is reproduced');
      return undefined;
    }
    if (call.tool === 'write') {
      disk(args.path, args.content);
      invoke('write', args, call.id, text(`Successfully wrote ${Buffer.byteLength(args.content)} bytes to ${args.path}`));
    } else if (call.tool === 'read') {
      invoke('read', args, call.id, text(fs.readFileSync(path.join(root, args.path), 'utf8')));
    } else if (call.tool === 'edit') {
      const before = fs.readFileSync(path.join(root, args.path), 'utf8');
      disk(args.path, args.edits.reduce((content, edit) => content.replace(edit.oldText, () => edit.newText), before));
      invoke('edit', args, call.id, text(`Successfully replaced ${args.edits.length} block(s) in ${args.path}.`));
    } else if (call.tool === 'exec') {
      invoke('exec', args, call.id, {...text(call.details.aggregated), details: call.details});
    } else if (call.tool === 'pixel_ods_workspace_preview') {
      assert.equal(digest(files), call.details.sha256, 'replayed bytes reproduce the host snapshot digest');
      const result = {...text('ODS independently published and read back the workspace static files.'), details: call.details};
      if (call.transport === 'tool_call') invoke('tool_call', {id: call.tool, args}, call.id, envelope(call.tool, result));
      else invoke(call.tool, args, call.id, result);
    } else if (call.tool === PREVIEW_INSPECTION_TOOL) {
      return inspect(args, call.id, call.transport);
    } else assert.fail(call.tool);
    return undefined;
  };
  const begin = turn => {
    context = {agentId: 'pixel', sessionId, sessionKey, runId: turn.runId};
    guard.observeRun(context, 'pixel', {prompt: turn.prompt}, {workspaceRoot: root});
  };
  // Replays a turn up to (not including) the given recorded call.
  const until = async (turn, stop) => {
    begin(turn);
    for (const call of turn.calls) { if (call === stop) return; await run(call); }
  };
  const verification = () => guard.verificationForRun(context.runId);
  return {guard, begin, run, until, inspect, verification};
}
const LAPTOP_SESSION = {sessionId: 'a7094eff-f48e-425d-a609-276b188e127d',
  sessionKey: 'agent:pixel:openai-user:ods-0fdc5c88412e45b221b82cb81b6b52defcdc7e7a4e5966956292889409ebf8ac'};
const TOWER2_SESSION = {sessionId: '22641527-665b-4852-b95f-bc3a82539617',
  sessionKey: 'agent:pixel:openai-user:ods-804086a53d857fefdf02e21eeff894d04fdcfc1e40c17787155d76c39af1ef0f'};

// The model-visible corrective arguments, exactly as a small model would copy them.
const nextArgs = text => {
  const match = /with exactly these args: (\{.*?\}) The target /.exec(text);
  assert.ok(match, text);
  return JSON.parse(match[1]);
};
function assertIncomplete(result, recorded, publish, target) {
  const text = result.content[0].text;
  assert.match(recorded.text, /^Preview inspection passed\. Only the listed steps passed; no show\/hide transition was tested\./);
  assert.ok(text.startsWith(`${INCOMPLETE} The owner requested a show/hide change, but no single locator was asserted hidden ` +
    'before a click and visible after it, so the requested change was not tested.'), text);
  assert.doesNotMatch(text, /Preview inspection passed/);
  assert.equal(result.isError, true);
  assert.deepEqual({status: result.details.status, errorCode: result.details.errorCode, kind: result.details.kind},
    {status: 'incomplete', errorCode: TRANSITION_UNTESTED, kind: INSPECTION_KIND});
  assert.deepEqual(result.details.receipt, recorded.details, 'the capsule receipt is kept unchanged as evidence');
  assert.ok(text.includes(`"siteId":"${publish.details.siteId}","status":"incomplete","steps":[`), 'the evidence copy never reads passed');
  const args = nextArgs(text);
  assert.deepEqual(args, {siteId: publish.details.siteId, sha256: publish.details.sha256,
    viewport: recorded.arguments.viewport, steps: transition(target)});
  // One unchanged locator, hidden before and visible after the model's own click.
  assert.deepEqual(args.steps[0].locator, args.steps[2].locator);
  assert.deepEqual(args.steps[1], recorded.arguments.steps.find(step => step.action === 'click'));
  assert.ok(text.includes(`Keep this click step and use the same target locator in both assertions. ${KEEP}`), text);
  assert.equal(boundVisibilityInspection(recorded.arguments, result, publish.details), undefined);
  return {text, args};
}

for (const nestedHooks of [false, true]) {
  test(`laptop round 100: the recorded inspection is INCOMPLETE and names the exact next steps (nested hooks: ${nestedHooks})`, async t => {
    const r = replay(t, LAPTOP, LAPTOP_PAGE, {nestedHooks, ...LAPTOP_SESSION});
    const [create] = LAPTOP.turns;
    await r.until(create, LAPTOP_INSPECT);
    const {text, args} = assertIncomplete(await r.run(LAPTOP_INSPECT), LAPTOP_INSPECT, LAPTOP_PUBLISH, LAPTOP_TARGET);
    assert.ok(text.includes('Before the click, your steps asserted no affected element.'), text);
    assert.ok(text.includes('The target ".event-card.sold-out" is your locator ".event-card.sold-out.revealed" with state qualifiers ' +
      'removed; it matches exactly one element in the published source; it contains the requested "Midnight sold-out concert" heading.'), text);
    assert.equal(r.verification().status, 'failed');
    assert.match(r.verification().text, /show\/hide interaction/);
    // Sending exactly those args passes on the recorded page and verifies it.
    const corrected = await r.inspect(args, 'corrected', 'tool_call');
    assert.equal(corrected.isError, undefined);
    assert.equal(corrected.details.status, 'passed');
    assert.match(corrected.content[0].text, /^Preview inspection passed\. These steps tested opposite visibility states of the same element around a click\./);
    assert.equal(r.verification().status, 'passed');
    // A later read-only check of the same snapshot keeps that proof and is not incomplete.
    const footer = await r.inspect({...args, steps: [{action: 'assert-visible', locator: BUTTON}]}, 'static-after', 'tool_call');
    assert.equal(footer.details.status, 'passed');
    assert.doesNotMatch(footer.content[0].text, /INCOMPLETE/);
    assert.equal(r.verification().status, 'passed');
  });

  test(`laptop round 100 update turn: the preserved behavior keeps the card target (nested hooks: ${nestedHooks})`, async t => {
    const r = replay(t, LAPTOP, LAPTOP_PAGE, {nestedHooks, ...LAPTOP_SESSION});
    const [create, update] = LAPTOP.turns;
    await r.until(create, LAPTOP_INSPECT);
    await r.inspect({...LAPTOP_INSPECT.arguments, steps: transition(LAPTOP_TARGET)}, 'transition', 'tool_call');
    assert.equal(r.verification().status, 'passed');
    await r.until(update, LAPTOP_INSPECT_UPDATE);
    const {args} = assertIncomplete(await r.run(LAPTOP_INSPECT_UPDATE), LAPTOP_INSPECT_UPDATE, LAPTOP_PUBLISH_UPDATE, LAPTOP_TARGET);
    // The update message names no element; the create turn's wording still names the card.
    const unaimed = await r.inspect({...args, steps: [{action: 'assert-visible', locator: BUTTON}, {action: 'click', locator: BUTTON}]},
      'update-unaimed', 'tool_call');
    assert.deepEqual(nextArgs(unaimed.content[0].text).steps, transition(HEADING));
    assert.equal(r.verification().status, 'failed');
    await r.inspect(args, 'inspect-update-corrected', 'tool_call');
    assert.equal(r.verification().status, 'passed');
  });
}

test('tower2 round 102: state-qualified locators are INCOMPLETE and get one stable id locator', async t => {
  const r = replay(t, TOWER2, TOWER2_PAGE, TOWER2_SESSION);
  const [create] = TOWER2.turns;
  await r.until(create, TOWER2_NO_MATCH);
  // The first plan failed on the unrepaired page. The recorded failure is kept
  // byte for byte; the owner's show/hide requirement adds one ready call.
  const failed = await r.run(TOWER2_NO_MATCH);
  const failedText = failed.content[0].text;
  const ready = failedText.indexOf(' Next step: call pixel_ods_workspace_preview_inspect (a tool in your list; call it by name)');
  assert.equal(failedText.slice(0, ready) + failedText.slice(failedText.indexOf(` ${INSPECTION_SCOPE}`)), TOWER2_NO_MATCH.text,
    'byte-identical to the recorded failure apart from the ready call');
  assert.deepEqual(nextArgs(failedText).steps, transition(TOWER2_TARGET));
  assert.equal(failed.details.status, 'failed');
  for (const call of create.calls.slice(create.calls.indexOf(TOWER2_NO_MATCH) + 1, create.calls.indexOf(TOWER2_INSPECT))) await r.run(call);
  const {text, args} = assertIncomplete(await r.run(TOWER2_INSPECT), TOWER2_INSPECT, TOWER2_PUBLISH, TOWER2_TARGET);
  assert.ok(text.includes('Your assertions used different locators (".sold-out-card.hidden", ".sold-out-card:not(.hidden)"); ' +
    'a locator that includes the state it checks can match a different element before and after the click, ' +
    'so they do not show one element changing.'), text);
  assert.ok(text.includes('The target "#midnight-card" is the id, in the published source, of the element your locators ' +
    '".sold-out-card.hidden" and ".sold-out-card:not(.hidden)" name with state qualifiers removed; ' +
    'it contains the requested "Midnight sold-out concert" heading.'), text);
  assert.doesNotMatch(text, /Before the click, your steps asserted no affected element/);
  assert.equal(r.verification().status, 'failed');
  const corrected = await r.inspect(args, 'corrected');
  assert.equal(corrected.details.status, 'passed');
  assert.deepEqual(corrected.details.steps.map(step => [step.action, step.before.visible]),
    [['assert-hidden', false], ['click', true], ['assert-visible', true]]);
  assert.equal(r.verification().status, 'passed');
});

test('tower2 round 102 update turn: the refused edit, repairs and both plans replay; the passing plan is INCOMPLETE', async t => {
  const r = replay(t, TOWER2, TOWER2_PAGE, TOWER2_SESSION);
  const [create, update] = TOWER2.turns;
  await r.until(create, TOWER2_INSPECT);
  await r.inspect({...TOWER2_INSPECT.arguments, steps: transition(TOWER2_TARGET)}, 'transition');
  assert.equal(r.verification().status, 'passed');
  await r.until(update, TOWER2_UPDATE_NO_MATCH);
  const failed = await r.run(TOWER2_UPDATE_NO_MATCH);
  assert.equal(failed.content[0].text, TOWER2_UPDATE_NO_MATCH.text, 'byte-identical to the recorded failure');
  const {args} = assertIncomplete(await r.run(TOWER2_INSPECT_UPDATE), TOWER2_INSPECT_UPDATE, TOWER2_PUBLISH_UPDATE, TOWER2_TARGET);
  assert.equal(r.verification().status, 'failed');
  await r.inspect(args, 'update-corrected');
  assert.equal(r.verification().status, 'passed');
});

test('requests without show/hide behavior keep the ordinary passing inspection byte for byte', async t => {
  for (const [fixture, model, session, recorded] of [[LAPTOP, LAPTOP_PAGE, LAPTOP_SESSION, LAPTOP_INSPECT],
    [TOWER2, TOWER2_PAGE, TOWER2_SESSION, TOWER2_INSPECT]]) {
    const r = replay(t, fixture, model, session);
    const [create] = fixture.turns;
    await r.until({...create, prompt: create.prompt.replace(/ Initially hide[^.]*\. Provide[^.]*\./, '')}, recorded);
    const result = await r.run(recorded);
    assert.equal(result.isError, undefined);
    assert.equal(result.details.status, 'passed');
    assert.equal(result.content[0].text, recorded.text);
  }
});

test('the requirement binds only to the exact pending inspection call of the active run', async t => {
  const r = replay(t, LAPTOP, LAPTOP_PAGE, LAPTOP_SESSION);
  const [create] = LAPTOP.turns;
  await r.until(create, LAPTOP_INSPECT);
  const args = LAPTOP_INSPECT.arguments;
  assert.equal(r.guard.previewInspectionTransition('unknown', args), undefined);
  const ctx = {agentId: 'pixel', ...LAPTOP_SESSION, runId: create.runId, toolName: 'tool_call', toolCallId: 'outer'};
  r.guard.beforeToolCall({toolName: 'tool_call', runId: create.runId, toolCallId: 'outer',
    params: {id: PREVIEW_INSPECTION_TOOL, args}}, ctx);
  const child = `tool_search_code:outer:${PREVIEW_INSPECTION_TOOL}:1`;
  const requirement = r.guard.previewInspectionTransition(child, args);
  assert.deepEqual({...requirement, outline: undefined}, {target: OWNER_PHRASE, control: {role: 'button', name: 'Show sold out'},
    initiallyHidden: true, outline: undefined});
  assert.equal(requirement.outline.elements[requirement.outline.headingIndex].name, 'Midnight Sold-Out Concert');
  assert.equal(r.guard.previewInspectionTransition(child, {...args, viewport: {width: 800, height: 600}}), undefined,
    'different arguments are a different call');
  assert.equal(r.guard.previewInspectionTransition(`tool_search_code:other:${PREVIEW_INSPECTION_TOOL}:1`, args), undefined);
  // Another snapshot of the same run gets the owner's wording, not this snapshot's outline.
  const other = {...args, sha256: 'b'.repeat(64), siteId: `site-${'b'.repeat(24)}`};
  r.guard.beforeToolCall({toolName: PREVIEW_INSPECTION_TOOL, runId: create.runId, toolCallId: 'direct', params: other},
    {...ctx, toolName: PREVIEW_INSPECTION_TOOL, toolCallId: 'direct'});
  assert.equal(r.guard.previewInspectionTransition('direct', other).outline, undefined);
  assert.equal(r.guard.previewInspectionTransition('direct', other).target, OWNER_PHRASE);
  // Inspection unavailable on this host: no requirement at all.
  const off = createToolLoopGuard({workspacePreviewInspectionAvailable: false, abortRun: () => true});
  assert.equal(off.previewInspectionTransition('direct', other), undefined);
});

test('tool: genuine transitions and failures never consult the requirement', async () => {
  let consulted = 0;
  const run = capsule(LAPTOP, LAPTOP_PAGE);
  const tool = createWorkspacePreviewInspectTool({request: async request => run(request), transitionRequirement: () => {
    consulted += 1; return {target: OWNER_PHRASE, initiallyHidden: true}; }});
  const base = {siteId: LAPTOP_PUBLISH.details.siteId, sha256: LAPTOP_PUBLISH.details.sha256, viewport: {width: 375, height: 667}};
  const good = await tool.execute('good', {...base, steps: transition(HEADING)});
  assert.equal(good.details.status, 'passed');
  assert.equal(good.isError, undefined);
  const more = await tool.execute('more', {...base, steps: [...transition(LAPTOP_TARGET), {action: 'assert-visible', locator: BUTTON}]});
  assert.equal(more.details.status, 'passed');
  const failed = await tool.execute('failed', {...base, steps: [{action: 'assert-visible', locator: HEADING}]});
  assert.equal(failed.details.status, 'failed');
  assert.doesNotMatch(failed.content[0].text, /INCOMPLETE/);
  assert.equal(consulted, 0);
  // A throwing or absent requirement leaves the ordinary result unchanged.
  const throwing = createWorkspacePreviewInspectTool({request: async request => run(request), transitionRequirement: () => { throw Error('x'); }});
  assert.equal((await throwing.execute('x', LAPTOP_INSPECT.arguments)).content[0].text, LAPTOP_INSPECT.text);
  const plain = createWorkspacePreviewInspectTool({request: async request => run(request)});
  assert.equal((await plain.execute('x', LAPTOP_INSPECT.arguments)).content[0].text, LAPTOP_INSPECT.text);
});

// A one-file page bound to its own digest.
const pageOutline = html => publishedElementOutline(OWNER_PHRASE, {relativeDirectory: 'site', files: 1,
  sha256: digest({'index.html': html}), bytes: Buffer.byteLength(html)}, {trackedContent: new Map([['site/index.html', html]])});

// The published entry pages, bound to their recorded snapshot digests.
function outlineOf(fixture, publish, turnCount = 1) {
  const files = {};
  for (const call of fixture.turns.slice(0, turnCount).flatMap(turn => turn.calls)) {
    if (call === publish) break;
    const file = relative(call.arguments.path ?? '').split('/').slice(1).join('/');
    if (call.tool === 'write') files[file] = call.arguments.content;
    if (call.tool === 'edit' && !call.blocked) files[file] = call.arguments.edits.reduce((content, edit) => content.replace(edit.oldText, () => edit.newText), files[file]);
  }
  const preview = {relativeDirectory: publish.arguments.relativeDirectory, files: Object.keys(files).length, sha256: publish.details.sha256,
    bytes: Object.values(files).reduce((sum, content) => sum + Buffer.byteLength(content), 0)};
  assert.equal(digest(files), publish.details.sha256);
  return {files, preview, outline: publishedElementOutline(OWNER_PHRASE, preview,
    {trackedContent: new Map(Object.entries(files).map(([file, content]) => [`${preview.relativeDirectory}/${file}`, content]))})};
}

test('the published outline is digest-bound and names ids, classes, script state classes and the owner heading', t => {
  const {outline, preview, files} = outlineOf(TOWER2, TOWER2_PUBLISH);
  const card = outline.elements.findIndex(element => element.id === 'midnight-card');
  assert.deepEqual({tag: outline.elements[card].tag, classes: [...outline.elements[card].classes]},
    {tag: 'section', classes: ['event-card', 'sold-out-card', 'hidden']});
  const heading = outline.elements[outline.headingIndex];
  assert.deepEqual({tag: heading.tag, name: heading.name, parent: outline.elements[outline.elements[heading.parent].parent].id},
    {tag: 'h2', name: 'Midnight Sold-Out Concert', parent: 'midnight-card'});
  assert.deepEqual([...outline.stateClasses], ['hidden'], 'script.js removes .hidden');
  assert.deepEqual([...outlineOf(LAPTOP, LAPTOP_PUBLISH).outline.stateClasses], ['active', 'revealed'], 'the inline script');
  const tracked = new Map(Object.entries(files).map(([file, content]) => [`${preview.relativeDirectory}/${file}`, content]));
  tracked.set(`${preview.relativeDirectory}/index.html`, `${files['index.html']} `);
  assert.equal(publishedElementOutline(OWNER_PHRASE, preview, {trackedContent: tracked}), undefined,
    'bytes that do not reproduce the snapshot digest are never read');
  // The workspace copy is read only when it reproduces the digest.
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-outline-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  for (const [file, content] of Object.entries(files)) {
    fs.mkdirSync(path.join(root, preview.relativeDirectory), {recursive: true});
    fs.writeFileSync(path.join(root, preview.relativeDirectory, file), content);
  }
  assert.equal(publishedElementOutline(OWNER_PHRASE, {...preview, bytes: TOWER2_PUBLISH.details.bytes},
    {receipt: TOWER2_PUBLISH.details, workspaceRoot: root})?.headingIndex, outline.headingIndex);
  const single = pageOutline;
  const name = html => { const o = single(html); return o.headingIndex === undefined ? undefined : o.elements[o.headingIndex].name ?? null; };
  assert.equal(name('<h3 aria-label="Midnight concert (sold out)">Midnight sold-out concert</h3>'), 'Midnight concert (sold out)');
  assert.equal(name('<h3 aria-labelledby="x">Midnight sold-out concert</h3>'), null);
  assert.equal(name('<h3>Midnight   sold-out\n concert</h3>'), 'Midnight sold-out concert');
  assert.equal(name('<h3>Midnight sold-out concert</h3><h4>Midnight Sold-Out Concert</h4>'), undefined, 'two headings are not one');
  assert.equal(name('<p>Midnight sold-out concert</p>'), undefined);
  assert.equal(single(`<div>${'<i></i>'.repeat(4000)}</div>`), undefined, 'an oversized page has no outline');
});

test('state qualifiers are removed only from the subject compound', () => {
  const states = new Set(STATE_CLASS_WORDS);
  const free = selector => stateFreeSelector(selector, states)?.selector;
  assert.equal(free('.sold-out-card.hidden'), '.sold-out-card');
  assert.equal(free('.sold-out-card:not(.hidden)'), '.sold-out-card');
  assert.equal(free('.event-card.sold-out.revealed'), '.event-card.sold-out');
  assert.equal(free('section#details[hidden]'), 'section#details');
  assert.equal(free('#faq > .answer.is-open'), '#faq > .answer');
  assert.equal(free('.panel[aria-hidden="true"]'), '.panel');
  assert.equal(free('.hidden .card'), '.hidden .card', 'an ancestor qualifier is structure, not the subject state');
  assert.equal(free('.card:hover'), '.card:hover');
  assert.equal(free('.hidden'), undefined, 'nothing stable remains');
  assert.equal(free('.card, .hidden'), undefined, 'selector lists are not parsed');
  assert.equal(stateFreeSelector('.card.faded', new Set([...states, 'faded']))?.selector, '.card', 'script-toggled classes count');
  assert.equal(stateFreeSelector('.card.faded', states)?.stripped, 0);
});

test('the target is the model element that contains the owner heading, as its id when it has one', () => {
  const tower2 = outlineOf(TOWER2, TOWER2_PUBLISH).outline, laptop = outlineOf(LAPTOP, LAPTOP_PUBLISH).outline;
  const plan = steps => normalizeWorkspacePreviewInspectionParams({siteId: TOWER2_PUBLISH.details.siteId,
    sha256: TOWER2_PUBLISH.details.sha256, viewport: {width: 375, height: 667}, steps});
  const choose = (steps, outline, phrase = OWNER_PHRASE) => chooseTransitionTarget(plan(steps), {control: BUTTON, outline, phrase});
  const click = {action: 'click', locator: BUTTON};
  assert.deepEqual(choose(TOWER2_INSPECT.arguments.steps, tower2), {locator: TOWER2_TARGET, basis: 'id',
    members: [{selector: '.sold-out-card.hidden'}, {selector: '.sold-out-card:not(.hidden)'}]});
  assert.deepEqual(choose(TOWER2_INSPECT_UPDATE.arguments.steps, tower2).locator, TOWER2_TARGET, 'later assertions do not distract');
  assert.deepEqual(choose(LAPTOP_INSPECT.arguments.steps, laptop), {locator: LAPTOP_TARGET, basis: 'model',
    members: [{selector: '.event-card.sold-out.revealed'}]});
  // The laptop page also has a click-activated overlay: it is not the requested card.
  assert.deepEqual(choose([click, {action: 'assert-visible', locator: {selector: '.sold-out-overlay.active'}}], laptop).locator, HEADING);
  // A class shared by every card: its hidden state names one card, whose id is used.
  assert.deepEqual(choose([{action: 'assert-hidden', locator: {selector: '.event-card.hidden'}}, click,
    {action: 'assert-visible', locator: {selector: '.event-card:not(.hidden)'}}], tower2).locator, TOWER2_TARGET);
  // A state class known only from the published script is removed too.
  const faded = pageOutline('<section class="card faded" id="promo"><h2>Midnight sold-out concert</h2></section><button>Show sold out</button>' +
    '<script>document.querySelector("button").onclick = () => document.querySelector(".card").classList.remove("faded");</script>');
  assert.deepEqual([...faded.stateClasses], ['faded']);
  assert.deepEqual(choose([{action: 'assert-hidden', locator: {selector: '.card.faded'}}, click,
    {action: 'assert-visible', locator: {selector: '.card:not(.faded)'}}], faded), {locator: {selector: '#promo'}, basis: 'id',
    members: [{selector: '.card.faded'}, {selector: '.card:not(.faded)'}]});
  // Without a usable outline the model's own state-free locator is offered.
  assert.deepEqual(choose(TOWER2_INSPECT.arguments.steps, undefined), {locator: {selector: '.sold-out-card'}, basis: 'unverified',
    members: [{selector: '.sold-out-card.hidden'}, {selector: '.sold-out-card:not(.hidden)'}]});
  // A locator with no static match (script-rendered) yields to the published owner heading.
  assert.deepEqual(choose([click, {action: 'assert-visible', locator: {selector: '.rendered-later.is-open'}}], tower2).locator, HEADING);
  assert.equal(choose([click, {action: 'assert-visible', locator: {selector: '.rendered-later.is-open'}}], {...tower2, headingIndex: undefined}).basis, 'unverified');
  // No model locator at all: the published heading, else the owner's phrase.
  assert.deepEqual(choose([{action: 'assert-visible', locator: BUTTON}, click], tower2), {locator: HEADING, basis: 'heading'});
  assert.deepEqual(choose([{action: 'assert-visible', locator: BUTTON}, click], undefined),
    {locator: {role: 'heading', name: OWNER_PHRASE, exact: true}, basis: 'owner'});
  assert.equal(choose([click], undefined, null), undefined);
});

test('tool: corrective steps keep the click, the direction and the schema bounds', () => {
  const tower2 = outlineOf(TOWER2, TOWER2_PUBLISH).outline;
  const request = steps => normalizeWorkspacePreviewInspectionParams({siteId: TOWER2_PUBLISH.details.siteId,
    sha256: TOWER2_PUBLISH.details.sha256, viewport: {width: 375, height: 667}, steps});
  const recorded = request(TOWER2_INSPECT.arguments.steps);
  assert.deepEqual(transitionCorrection(recorded, {target: OWNER_PHRASE, outline: tower2, initiallyHidden: true}).args.steps,
    transition(TOWER2_TARGET));
  // Hide-on-click wording reverses the assertions.
  assert.deepEqual(transitionCorrection(recorded, {outline: tower2, initiallyHidden: false}).args.steps.map(step => step.action),
    ['assert-visible', 'click', 'assert-hidden']);
  // No click in the plan: the owner's quoted control.
  const staticPlan = request([{action: 'assert-visible', locator: BUTTON}]);
  assert.deepEqual(transitionCorrection(staticPlan, {target: OWNER_PHRASE, outline: tower2, control: {role: 'button', name: 'Show sold out'},
    initiallyHidden: true}).args.steps, transition(HEADING));
  // Without a control or a target there is nothing ready to send.
  assert.equal(transitionCorrection(staticPlan, {target: OWNER_PHRASE, initiallyHidden: true}).args, undefined);
  assert.equal(transitionCorrection(request([{action: 'click', locator: BUTTON}]), {initiallyHidden: true}).args, undefined);
  // An oversized name cannot become a locator.
  assert.equal(transitionCorrection(staticPlan, {target: 'x'.repeat(121), control: {role: 'button', name: 'Go'}, initiallyHidden: true}).args, undefined);
});

test('tool: an incomplete result without ready arguments still names the missing steps', async () => {
  const run = capsule(LAPTOP, LAPTOP_PAGE);
  const tool = createWorkspacePreviewInspectTool({request: async request => run(request), transitionRequirement: () => ({initiallyHidden: true})});
  const result = await tool.execute('static', {...LAPTOP_INSPECT.arguments, steps: [{action: 'assert-visible', locator: BUTTON}]});
  assert.equal(result.isError, true);
  assert.equal(result.details.status, 'incomplete');
  assert.match(result.content[0].text, /^Preview inspection INCOMPLETE - not verified\./);
  assert.match(result.content[0].text, /These steps contain no click\./);
  assert.match(result.content[0].text, /steps assert-hidden\(target\), click\(control\), assert-visible\(target\), using one unchanging target locator in both assertions/);
  assert.doesNotMatch(result.content[0].text, /exactly these args/);
});

test('owner wording names the affected element, the control and the direction', () => {
  const derive = prompt => requestedVisibilityTransition(prompt, extractRequestedLiterals(prompt));
  for (const fixture of [LAPTOP, TOWER2]) {
    const [create, update] = fixture.turns;
    assert.deepEqual(derive(create.prompt), {target: OWNER_PHRASE, control: {role: 'button', name: 'Show sold out'}, initiallyHidden: true});
    // "a visible footer" is content, and "Show sold out behavior" names no element.
    assert.deepEqual(derive(update.prompt), {initiallyHidden: true});
    assert.deepEqual(inheritedVisibilityTransition(derive(update.prompt), derive(create.prompt)), derive(create.prompt));
  }
  assert.deepEqual(derive('Build a page. Add a button named "Dismiss" that hides the banner when clicked.'),
    {control: {role: 'button', name: 'Dismiss'}, initiallyHidden: false});
  assert.deepEqual(derive('Add a link called "More" that reveals the details. Do not hide the "Contact us" heading.'),
    {control: {role: 'link', name: 'More'}, initiallyHidden: true});
  assert.equal(derive('Create a static page with a heading titled "Hello".'), undefined);
  const inherited = derive(LAPTOP.turns[0].prompt);
  assert.equal(inheritedVisibilityTransition(inherited, undefined), inherited);
});

test('the registered inspection tool asks the run guard about exactly its own call', async () => {
  // Source composition only: the registration block, without the OpenClaw SDK.
  const source = fs.readFileSync(new URL('../plugin/index.js', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  const start = source.indexOf('    if (["unix", "native"].includes(api.pluginConfig?.workspacePreviewInspectionTransport)) {');
  const close = '\n    }\n';
  const end = source.indexOf(close, start);
  assert.ok(start >= 0 && end > start, 'expected the inspection tool registration block');
  let registered;
  const asked = [];
  const run = capsule(TOWER2, TOWER2_PAGE);
  vm.runInNewContext(source.slice(start, end + close.length), {
    api: {pluginConfig: {workspacePreviewInspectionTransport: 'unix'}},
    registerTool: (_api, tool, options) => { registered = {tool, names: [...options.names]}; },
    createWorkspacePreviewInspectTool: options => createWorkspacePreviewInspectTool({...options, request: async request => run(request)}),
    toolLoopGuard: {previewInspectionTransition: (id, params) => {
      asked.push([id, params]);
      return {target: OWNER_PHRASE, outline: outlineOf(TOWER2, TOWER2_PUBLISH).outline, initiallyHidden: true};
    }},
  });
  assert.deepEqual(registered.names, [PREVIEW_INSPECTION_TOOL]);
  const result = await registered.tool.execute('call-1', TOWER2_INSPECT.arguments);
  assert.deepEqual(asked, [['call-1', TOWER2_INSPECT.arguments]]);
  assert.ok(result.content[0].text.startsWith(INCOMPLETE), result.content[0].text);
  assert.deepEqual(nextArgs(result.content[0].text).steps, transition(TOWER2_TARGET));
});
