// The run guard's wording guidance for a pending preview inspection
// (previewInspectionGuidance): the state before the click that the owner's own
// wording stated, kept on a turn that preserves the behavior, and whether one
// more failed call ends the response's tool use. Laptop round 100 (main
// b060c6ae): a create turn that says "Initially hide", then an update turn
// that preserves "the working Show sold out behavior".
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {controlLocator} from '../plugin/inspection-target.mjs';
import {publishedElementOutline} from '../plugin/requested-literals.mjs';

const LAPTOP = JSON.parse(fs.readFileSync(new URL('./inspection-transition-laptop-round100.json', import.meta.url), 'utf8'));
const SESSION = {sessionId: 'a7094eff-f48e-425d-a609-276b188e127d',
  sessionKey: 'agent:pixel:openai-user:ods-0fdc5c88412e45b221b82cb81b6b52defcdc7e7a4e5966956292889409ebf8ac'};
const relative = file => file.replace(/^\/workspace\//, '');
const text = value => ({content: [{type: 'text', text: value}]});
const envelope = (name, result) => ({content: [{type: 'text', text: JSON.stringify({tool: {id: `openclaw:pixel-ods:${name}`, name}, result})}],
  details: {tool: {id: `openclaw:pixel-ods:${name}`, source: 'openclaw', sourceName: 'pixel-ods', name}, result}});
function digest(files) {
  const hash = createHash('sha256');
  for (const [file, content] of Object.entries(files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)) {
    const name = Buffer.from(file), data = Buffer.from(content), a = Buffer.alloc(4), b = Buffer.alloc(8);
    a.writeUInt32BE(name.length); b.writeBigUInt64BE(BigInt(data.length));
    hash.update(a).update(name).update(b).update(data);
  }
  return hash.digest('hex');
}

// The recorded turns through the real guard (write, edit, read, publish);
// inspections are only started, so their guidance can be read while pending.
function replay(t, turns) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-guidance-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  let context;
  const invoke = (name, args, id, result) => {
    const ctx = {...context, toolName: name, toolCallId: id};
    const event = {toolName: name, runId: context.runId, toolCallId: id, params: args};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    guard.afterToolCall({...event, params: prepared?.params ?? args, result}, ctx);
    guard.toolResultPersist({toolName: name, toolCallId: id, message: {role: 'toolResult', toolName: name, toolCallId: id, ...result}}, ctx);
  };
  const files = {};
  const until = (turn, tool) => {
    context = {agentId: 'pixel', ...SESSION, runId: turn.runId};
    guard.observeRun(context, 'pixel', {prompt: turn.prompt}, {workspaceRoot: root});
    for (const call of turn.calls) {
      if (call.tool === tool) return call;
      const args = call.arguments.path ? {...call.arguments, path: relative(call.arguments.path)} : call.arguments;
      const file = path.join(root, args.path ?? '');
      if (call.tool === 'write' || call.tool === 'edit') {
        const content = call.tool === 'write' ? args.content
          : args.edits.reduce((value, edit) => value.replace(edit.oldText, () => edit.newText), fs.readFileSync(file, 'utf8'));
        files[args.path.slice(args.path.indexOf('/') + 1)] = content;
        fs.mkdirSync(path.dirname(file), {recursive: true});
        fs.writeFileSync(file, content);
        invoke(call.tool, args, call.id, text(`Successfully wrote ${args.path}`));
      } else if (call.tool === 'read') invoke('read', args, call.id, text(fs.readFileSync(file, 'utf8')));
      else if (call.tool === 'pixel_ods_workspace_preview') {
        assert.equal(digest(files), call.details.sha256, 'replayed bytes reproduce the host snapshot digest');
        const result = {...text('ODS independently published and read back the workspace static files.'), details: call.details};
        if (call.transport === 'tool_call') invoke('tool_call', {id: call.tool, args}, call.id, envelope(call.tool, result));
        else invoke(call.tool, args, call.id, result);
      } else assert.fail(call.tool);
    }
    return undefined;
  };
  // Starts an inspection as a Tool Search child of a pending tool_call.
  const pending = (args, id) => {
    const ctx = {...context, toolName: 'tool_call', toolCallId: id};
    assert.notEqual(guard.beforeToolCall({toolName: 'tool_call', runId: context.runId, toolCallId: id,
      params: {id: PREVIEW_INSPECTION_TOOL, args}}, ctx)?.block, true);
    return `tool_search_code:${id}:${PREVIEW_INSPECTION_TOOL}:1`;
  };
  const fail = (args, id) => {
    const child = pending(args, id), ctx = {...context, toolName: 'tool_call', toolCallId: id};
    const result = envelope(PREVIEW_INSPECTION_TOOL, {...text('Preview inspection request rejected before execution: invalid arguments.'),
      details: {status: 'failed', errorCode: 'invalid_request'}, isError: true});
    guard.afterToolCall({toolName: 'tool_call', runId: context.runId, toolCallId: id, params: {id: PREVIEW_INSPECTION_TOOL, args}, result}, ctx);
    guard.toolResultPersist({toolName: 'tool_call', toolCallId: id, message: {role: 'toolResult', toolName: 'tool_call', toolCallId: id, ...result}}, ctx);
    return child;
  };
  return {guard, until, pending, fail, files};
}

test('the owner\'s stated direction is named for exactly the pending call, and a preserve turn keeps it', t => {
  const r = replay(t, LAPTOP.turns);
  const [create, update] = LAPTOP.turns;
  const inspect = r.until(create, PREVIEW_INSPECTION_TOOL);
  const child = r.pending(inspect.arguments, 'outer');
  assert.deepEqual(r.guard.previewInspectionGuidance(child, inspect.arguments), {direction: 'hidden'});
  assert.equal(r.guard.previewInspectionGuidance(child, {...inspect.arguments, viewport: {width: 800, height: 600}}), undefined,
    'different arguments are a different call');
  assert.equal(r.guard.previewInspectionGuidance('unknown', inspect.arguments), undefined);
  // "Preserve ... the working Show sold out behavior" names no direction itself.
  const kept = r.until(update, PREVIEW_INSPECTION_TOOL);
  assert.match(update.prompt, /Preserve all three event cards and the working Show sold out behavior/);
  const later = r.pending(kept.arguments, 'update-outer');
  assert.equal(r.guard.previewInspectionTransition(later, kept.arguments).initiallyHidden, true);
  assert.deepEqual(r.guard.previewInspectionGuidance(later, kept.arguments), {direction: 'hidden'});
});

test('a toggle request has a requirement but no stated direction', t => {
  const [create] = LAPTOP.turns;
  const prompt = create.prompt.replace(/Initially hide[^.]*\. Provide an accessible button named exactly "Show sold out" that reveals that card when clicked\./,
    'Provide an accessible button named exactly "Show sold out" that toggles the Midnight sold-out concert card.');
  assert.notEqual(prompt, create.prompt);
  const r = replay(t, [{...create, prompt}]);
  const inspect = r.until({...create, prompt}, PREVIEW_INSPECTION_TOOL);
  const child = r.pending(inspect.arguments, 'outer');
  assert.equal(r.guard.previewInspectionTransition(child, inspect.arguments).initiallyHidden, true, 'the default order');
  assert.deepEqual(r.guard.previewInspectionGuidance(child, inspect.arguments), {});
});

test('finalFailure is set only for the call whose failure ends the response\'s tool use', t => {
  const r = replay(t, LAPTOP.turns);
  const inspect = r.until(LAPTOP.turns[0], PREVIEW_INSPECTION_TOOL);
  const bad = {...inspect.arguments, steps: [{action: 'assert-hidden', selector: '.card'}]};
  for (let failures = 0; failures < 3; failures++) {
    const child = r.pending(bad, `probe-${failures}`);
    assert.equal(r.guard.previewInspectionGuidance(child, bad).finalFailure, undefined, `after ${failures} failures`);
    r.fail(bad, `bad-${failures}`);
  }
  const child = r.pending(bad, 'fourth');
  assert.equal(r.guard.previewInspectionGuidance(child, bad).finalFailure, true);
});

test('the published outline names controls, and a control that matched nothing by name gets its CSS locator', () => {
  const html = '<main><section id="events"><h2>Events</h2><article class="event-card sold-out hidden"><h3>Midnight</h3></article></section>' +
    '<button id="showSoldOutBtn" class="show-sold-out-btn">Show sold out</button><a href="#top" class="top">Back to top</a>' +
    '<div role="button" class="chip">Filter</div><button class="twin">Twin</button><button class="twin">twin</button></main>';
  const outline = publishedElementOutline('Midnight', {relativeDirectory: 'site', files: 1, sha256: digest({'index.html': html}),
    bytes: Buffer.byteLength(html)}, {trackedContent: new Map([['site/index.html', html]])});
  const named = outline.elements.filter(element => element.role).map(({tag, role, name}) => [tag, role, name]);
  assert.deepEqual(named, [['button', 'button', 'Show sold out'], ['a', 'link', 'Back to top'], ['div', 'button', 'Filter'],
    ['button', 'button', 'Twin'], ['button', 'button', 'twin']]);
  assert.equal(outline.elements[outline.headingIndex].name, 'Midnight', 'headings are unchanged');
  assert.deepEqual(controlLocator(outline, {role: 'button', name: 'SHOW SOLD OUT', exact: true}), {selector: '#showSoldOutBtn'});
  assert.deepEqual(controlLocator(outline, {role: 'button', name: 'Filter', exact: true}), {selector: 'div.chip'});
  assert.deepEqual(controlLocator(outline, {role: 'link', name: 'back to top', exact: true}), {selector: 'a.top'});
  assert.equal(controlLocator(outline, {role: 'button', name: 'Twin', exact: true}), undefined, 'two controls share the name');
  assert.equal(controlLocator(outline, {role: 'button', name: 'Missing', exact: true}), undefined);
});
