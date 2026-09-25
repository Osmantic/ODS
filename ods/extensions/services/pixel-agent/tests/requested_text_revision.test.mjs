// Replays tower1 round 067 (Qwen3.5-27B, ODS main 53b8a9b5): the published
// card said "Midnight Concert", the receipt noted the missing requested text,
// the model inspected that same snapshot and answered. The finalization
// revision was refused by the harness after potential side effects, so the
// one bounded revision is delivered on the model's next tool result instead.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {REQUESTED_TEXT_REVISION_INSTRUCTION, requestedTextRevisionInstruction} from '../plugin/requested-literals.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash, normalizeWorkspacePreviewInspectionParams}
  from '../plugin/workspace-preview-inspect.mjs';

const TOWER1 = JSON.parse(fs.readFileSync(new URL('./requested-text-tower1-round067.json', import.meta.url), 'utf8'));
const [WRITE, PUBLISH, INSPECT] = TOWER1.calls;
const DIRECTORY = PUBLISH.arguments.args.relativeDirectory;
const INDEX = `${DIRECTORY}/index.html`;
const ORIGINAL = WRITE.arguments.content;
const USER = 'ods-2ce2da95bd19e9b810d3ad202e70e941824860961eba94524725df9b7538fe76';
const context = {agentId: 'pixel', runId: 'chatcmpl_49c3a20e-2a5b-40a2-8093-80f9d1ede297',
  sessionId: 'ef81c43f-eb06-49cd-b7b9-cec9a3fec82a', sessionKey: `agent:pixel:openai-user:${USER}`};
const REVISION = '[ODS Pixel next step] Requested text is still missing from the published page: ["Midnight sold-out concert"]. ' +
  'Add the exact text as requested (for example, as the card heading if the owner described it as a card title, ' +
  'or as the page title or h1 if the owner named them), republish with pixel_ods_workspace_preview, and keep everything else unchanged.';
const NOTE = '[ODS Pixel next step] Requested text not found: "Midnight sold-out concert". Use the owner\'s exact wording, republish and re-inspect.';
// What the owner received in round 067, byte for byte.
const FAILURE = 'The published page does not contain text the owner requested: "Midnight sold-out concert". ' +
  'The preview is available, but that requirement is not met.\n\n' +
  'Browser inspection passed for the submitted show/hide checks only; this does not verify all requested behavior.\n\n' +
  'Your preview is ready.\n\n[Open preview](http://site-877a095eea047108badd1b3b.localhost:9437/site-877a095eea047108badd1b3b/)\n\n' +
  'Published from your workspace.';
// The closing provenance line depends on whether Tool Search's inner hook
// also reported the publication; it is independent of the requested text.
const body = text => text.replace(/\n\n(?:Created by Portal|Published from your workspace)\.$/, '');

function snapshot(html) {
  const name = Buffer.from('index.html'), data = Buffer.from(html), a = Buffer.alloc(4), b = Buffer.alloc(8);
  a.writeUInt32BE(name.length); b.writeBigUInt64BE(BigInt(data.length));
  const sha256 = createHash('sha256').update(a).update(name).update(b).update(data).digest('hex');
  const siteId = `site-${sha256.slice(0, 24)}`;
  return {...TOWER1.publicationReceipt, siteId, sha256, entrySha256: createHash('sha256').update(data).digest('hex'),
    bytes: data.length, url: `http://${siteId}.localhost:9437/${siteId}/`};
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-literal-revise-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  guard.observeRun(context, 'pixel', {prompt: TOWER1.prompt}, {workspaceRoot: root});
  const results = [];
  // Hook order as in the gateway: before_tool_call, after_tool_call, then
  // tool_result_persist, whose text the model reads on its next call.
  // `during` runs while the tool executes (an owner cancellation, say).
  const invoke = (tool, args, id, result, during) => {
    const ctx = {...context, toolName: tool, toolCallId: id};
    const event = {toolName: tool, runId: context.runId, toolCallId: id, params: args};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    during?.();
    guard.afterToolCall({...event, params: prepared?.params ?? args, result}, ctx);
    const persisted = guard.toolResultPersist({toolName: tool, toolCallId: id,
      message: {role: 'toolResult', toolName: tool, toolCallId: id, ...result}}, ctx);
    const text = (persisted?.message?.content ?? result.content ?? []).map(block => block.text).join('\n');
    results.push(text);
    return text;
  };
  const wrapped = (name, inner) => ({content: inner.content,
    details: {tool: {id: `openclaw:pixel-ods:${name}`, name, source: 'openclaw', sourceName: 'pixel-ods'}, result: inner}});
  const write = (html, id) => {
    fs.mkdirSync(path.join(root, DIRECTORY), {recursive: true});
    fs.writeFileSync(path.join(root, INDEX), html);
    return invoke('write', {path: INDEX, content: html}, id,
      {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(html)} bytes to ${INDEX}`}]});
  };
  const edit = (html, from, to, id) => {
    invoke('read', {path: INDEX}, `${id}-read`, {content: [{type: 'text', text: html}]});
    const next = html.replace(from, to);
    fs.writeFileSync(path.join(root, INDEX), next);
    invoke('edit', {path: INDEX, edits: [{oldText: from, newText: to}]}, id,
      {content: [{type: 'text', text: `Successfully replaced 1 block(s) in ${INDEX}.`}]});
    return next;
  };
  const publish = (html, id) => {
    const receipt = snapshot(html);
    const text = invoke('tool_call', PUBLISH.arguments, id, wrapped('pixel_ods_workspace_preview',
      {content: [{type: 'text', text: `ODS independently published and read back 1 workspace static files. Verified browser URL: ${receipt.url}.`}],
        details: receipt}));
    return {receipt, text};
  };
  // The model's own inspection plan, re-bound to the snapshot it inspects.
  const inspect = (receipt, id, rename = name => name, during) => {
    const params = {...INSPECT.arguments.args, siteId: receipt.siteId, sha256: receipt.sha256,
      steps: INSPECT.arguments.args.steps.map(step => ({...step, locator: {...step.locator, name: rename(step.locator.name)}}))};
    const request = normalizeWorkspacePreviewInspectionParams(params);
    const state = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible', opacity: '1',
      hidden: false, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
    const details = {schemaVersion: 1, kind: INSPECTION_KIND, status: 'passed', siteId: params.siteId, sha256: params.sha256,
      planSha256: inspectionPlanHash(request), viewport: params.viewport,
      steps: params.steps.map((step, index) => ({index, ...step, before: state(step.action !== 'assert-hidden'),
        ...(step.action === 'click' ? {after: state(true)} : {}), stable: true, status: 'passed'})),
      diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE};
    return invoke('tool_call', {id: PREVIEW_INSPECTION_TOOL, args: params}, id, wrapped(PREVIEW_INSPECTION_TOOL,
      {content: [{type: 'text', text: 'Preview inspection passed.'}], details}), during);
  };
  const finalize = answer => guard.beforeAgentFinalize({lastAssistantMessage: answer}, context);
  const delivered = () => guard.deliveryVerificationForRun(context.runId);
  const revisions = () => results.filter(text => text.includes(REVISION_START)).length;
  return {guard, write, edit, publish, inspect, finalize, delivered, revisions, results};
}
const REVISION_START = 'Requested text is still missing from the published page';

// Round 067 as recorded: write, publish (note), inspect the same snapshot.
function tower1Prefix(t) {
  const f = fixture(t);
  f.write(ORIGINAL, 'write');
  const first = f.publish(ORIGINAL, 'publish');
  assert.equal(first.receipt.sha256, TOWER1.publicationReceipt.sha256, 'replayed bytes reproduce the host snapshot digest');
  assert.ok(first.text.includes(NOTE), 'the publication receipt carries the requested-text note');
  assert.doesNotMatch(first.text, new RegExp(REVISION_START), 'the receipt itself never spends the revision');
  const inspected = f.inspect(first.receipt, 'inspect');
  assert.ok(inspected.includes(REVISION), inspected);
  assert.doesNotMatch(inspected, /Requested text not found/);
  return {...f, first};
}

test('fixed revision instruction names the missing literals once, as a JSON list', () => {
  const preview = snapshot(ORIGINAL);
  const check = {siteId: preview.siteId, sha256: preview.sha256, missing: [{text: 'Midnight sold-out concert'},
    {text: 'Night Garden', target: 'page title'}, {text: 'Night Garden', target: 'h1'}]};
  assert.equal(requestedTextRevisionInstruction(preview, check),
    REQUESTED_TEXT_REVISION_INSTRUCTION.join('["Midnight sold-out concert","Night Garden"]'));
  assert.equal(requestedTextRevisionInstruction({...preview, sha256: 'f'.repeat(64)}, check), undefined);
  assert.equal(requestedTextRevisionInstruction(preview, {...check, missing: []}), undefined);
});

test('tower1 round 067: the revision fires once and a republish with the literal is verified', t => {
  const f = tower1Prefix(t);
  const repaired = f.edit(ORIGINAL, '<h2>Midnight Concert</h2>', '<h2>Midnight sold-out concert</h2>', 'repair');
  const second = f.publish(repaired, 'republish');
  assert.notEqual(second.receipt.sha256, f.first.receipt.sha256);
  assert.doesNotMatch(second.text, /Requested text/);
  assert.match(second.text, /pixel_ods_workspace_preview_inspect directly/, 'the new snapshot needs its own interaction proof');
  const inspected = f.inspect(second.receipt, 'reinspect', name => name === 'Midnight Concert' ? 'Midnight sold-out concert' : name);
  assert.doesNotMatch(inspected, /Requested text/);
  assert.equal(f.finalize('Fixed the card title and republished.'), undefined, 'nothing left to revise');
  assert.equal(f.revisions(), 1);
  const outcome = f.delivered();
  assert.equal(outcome.status, 'passed');
  assert.doesNotMatch(outcome.text, /does not contain text the owner requested/);
  assert.equal(outcome.preview.sha256, second.receipt.sha256);
});

test('tower1 round 067: a second miss ends in the unchanged honest failure', t => {
  const f = tower1Prefix(t);
  const retried = f.edit(ORIGINAL, '<h2>Midnight Concert</h2>', '<h2>Midnight Sold Out Show</h2>', 'retry');
  const second = f.publish(retried, 'republish');
  assert.ok(second.text.includes(NOTE), 'each publication still reports its own misses');
  const inspected = f.inspect(second.receipt, 'reinspect', name => name === 'Midnight Concert' ? 'Midnight Sold Out Show' : name);
  assert.doesNotMatch(inspected, new RegExp(REVISION_START), 'no second revision on the new snapshot');
  assert.deepEqual(f.finalize('Updated and republished.'),
    {action: 'finalize', reason: 'Owner-requested text is still missing after the bounded revision.'});
  assert.equal(f.revisions(), 1);
  const outcome = f.delivered();
  assert.equal(outcome.status, 'failed');
  assert.equal(body(outcome.text), body(FAILURE).replaceAll(f.first.receipt.url, second.receipt.url));
});

test('tower1 round 067: answering after the revision keeps the recorded failure delivery', t => {
  const f = tower1Prefix(t);
  assert.deepEqual(f.finalize(TOWER1.finalAnswer),
    {action: 'finalize', reason: 'Owner-requested text is still missing after the bounded revision.'});
  assert.equal(f.finalize(TOWER1.finalAnswer)?.action, 'finalize', 'no loop on a repeated finalization');
  assert.equal(f.revisions(), 1);
  assert.deepEqual({status: f.delivered().status, text: body(f.delivered().text)}, {status: 'failed', text: body(FAILURE)});
  // Further tool results for that snapshot may repeat the note, never the revision.
  assert.doesNotMatch(f.inspect(f.first.receipt, 'inspect-again'), new RegExp(REVISION_START));
  assert.equal(f.revisions(), 1);
});

test('an answer straight after the receipt gets the same revision once at finalization', t => {
  const f = fixture(t);
  f.write(ORIGINAL, 'write');
  const {text} = f.publish(ORIGINAL, 'publish');
  assert.ok(text.includes(NOTE));
  const decision = f.finalize(TOWER1.finalAnswer);
  assert.deepEqual(decision, {action: 'revise', reason: 'Pixel has not completed every owner-requested verified step.',
    retry: {instruction: REVISION.replace('[ODS Pixel next step] ', ''),
      idempotencyKey: 'pixel-ods-workspace-preview-requested-text', maxAttempts: 1}});
  // The revision pass answers without repairing; the honest failure stands.
  assert.equal(f.finalize(TOWER1.finalAnswer)?.action, 'finalize');
  assert.equal(f.inspect(f.publish(ORIGINAL, 'same-bytes').receipt, 'inspect').includes(REVISION_START), false,
    'the finalization revision was the one revision');
  assert.equal(f.delivered().status, 'failed');
  assert.match(f.delivered().text, /^The published page does not contain text the owner requested: "Midnight sold-out concert"\./);
});

test('no revision after the owner cancelled', async t => {
  const f = fixture(t);
  f.write(ORIGINAL, 'write');
  const {receipt} = f.publish(ORIGINAL, 'publish');
  // The owner cancels while the inspection is running.
  let cancelled;
  const inspected = f.inspect(receipt, 'inspect', undefined, () => { cancelled = f.guard.abortUserRun(USER); });
  assert.equal(await cancelled, true);
  assert.doesNotMatch(inspected, new RegExp(REVISION_START));
  assert.equal(f.finalize(TOWER1.finalAnswer), undefined, 'a cancelled run is never revised');
  assert.equal(f.revisions(), 0);
});
