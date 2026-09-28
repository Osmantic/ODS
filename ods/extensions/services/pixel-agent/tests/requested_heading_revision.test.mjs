// Replays tower2 round 073 (Qwen3-Coder-Next, ODS main 67cb2ac0): the owner
// listed three event cards by name; the model kept "Dawn jazz" only as a badge
// and titled the card "Dawn Jazz at the Rose Pavilion". The requested-text
// check passed on the badge, the answer claimed all three cards, and the fleet
// check (a heading named exactly "Dawn jazz") failed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {extractRequestedLiterals, missingRequestedText, requestedTextInstruction, requestedTextRevisionInstruction,
  requestedTextDeliveryNote, REQUESTED_HEADING_REVISION_INSTRUCTION, REQUESTED_TEXT_REVISION_INSTRUCTION}
  from '../plugin/requested-literals.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash, normalizeWorkspacePreviewInspectionParams}
  from '../plugin/workspace-preview-inspect.mjs';

const TOWER2 = JSON.parse(fs.readFileSync(new URL('./requested-heading-tower2-round073.json', import.meta.url), 'utf8'));
const WRITES = TOWER2.calls.filter(call => call.tool === 'write');
const [PUBLISH] = TOWER2.calls.filter(call => call.tool === 'pixel_ods_workspace_preview');
const [INSPECT] = TOWER2.calls.filter(call => call.tool === PREVIEW_INSPECTION_TOOL);
const DIRECTORY = PUBLISH.arguments.relativeDirectory;
const FILES = Object.fromEntries(WRITES.map(call => [call.arguments.path.slice(DIRECTORY.length + 1), call.arguments.content]));
const ORIGINAL = FILES['index.html'];
const RENAMED = '<h2>Dawn Jazz at the Rose Pavilion</h2>';
const USER = 'ods-423791fe2a84e4b2d2f73b7beed1d9b0b4e0f448be1f414344b2cd5a5a521347';
const context = {agentId: 'pixel', runId: 'chatcmpl_90dab8fd-475e-40f8-9539-8997d5965958',
  sessionId: '73b446fc-41d8-4cf7-b328-6c015c4c0170', sessionKey: `agent:pixel:openai-user:${USER}`};
const NOTE = '[ODS Pixel next step] "Dawn jazz" appears only inside a longer heading ("Dawn Jazz at the Rose Pavilion"); ' +
  'use the exact name as the heading, then republish and re-inspect.';
const REVISION = '[ODS Pixel next step] Requested names still appear only inside longer headings on the published page: ["Dawn jazz"]. ' +
  'Use each exact name as the whole heading of its item and put extra detail in body text, ' +
  'republish with pixel_ods_workspace_preview, and keep everything else unchanged.';
const REVISION_START = 'Requested names still appear only inside longer headings';
const FAILURE_NOTE = 'The published page uses requested names only inside longer headings: ' +
  '"Dawn jazz" ("Dawn Jazz at the Rose Pavilion"). The preview is available, but that requirement is not met.';

function snapshot(files) {
  const entries = Object.entries(files).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0);
  const digest = createHash('sha256');
  let bytes = 0;
  for (const [name, content] of entries) {
    const encodedName = Buffer.from(name), data = Buffer.from(content), a = Buffer.alloc(4), b = Buffer.alloc(8);
    a.writeUInt32BE(encodedName.length); b.writeBigUInt64BE(BigInt(data.length));
    digest.update(a).update(encodedName).update(b).update(data);
    bytes += data.length;
  }
  const sha256 = digest.digest('hex'), siteId = `site-${sha256.slice(0, 24)}`;
  return {...TOWER2.publicationReceipt, siteId, sha256, bytes, files: entries.length,
    entrySha256: createHash('sha256').update(files['index.html']).digest('hex'),
    url: `http://${siteId}.localhost:9437/${siteId}/`, publishedPaths: entries.map(([name]) => name)};
}

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-heading-revise-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  guard.observeRun(context, 'pixel', {prompt: TOWER2.prompt}, {workspaceRoot: root});
  const results = [];
  const invoke = (tool, args, id, result) => {
    const ctx = {...context, toolName: tool, toolCallId: id};
    const event = {toolName: tool, runId: context.runId, toolCallId: id, params: args};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    guard.afterToolCall({...event, params: prepared?.params ?? args, result}, ctx);
    const persisted = guard.toolResultPersist({toolName: tool, toolCallId: id,
      message: {role: 'toolResult', toolName: tool, toolCallId: id, ...result}}, ctx);
    const text = (persisted?.message?.content ?? result.content ?? []).map(block => block.text).join('\n');
    results.push(text);
    return text;
  };
  const write = (name, content, id) => {
    const file = `${DIRECTORY}/${name}`;
    fs.mkdirSync(path.join(root, DIRECTORY), {recursive: true});
    fs.writeFileSync(path.join(root, file), content);
    return invoke('write', {path: file, content}, id,
      {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(content)} bytes to ${file}`}]});
  };
  const edit = (html, from, to, id) => {
    const file = `${DIRECTORY}/index.html`;
    invoke('read', {path: file}, `${id}-read`, {content: [{type: 'text', text: html}]});
    const next = html.replace(from, to);
    fs.writeFileSync(path.join(root, file), next);
    invoke('edit', {path: file, edits: [{oldText: from, newText: to}]}, id,
      {content: [{type: 'text', text: `Successfully replaced 1 block(s) in ${file}.`}]});
    return next;
  };
  const publish = (html, id) => {
    const receipt = snapshot({...FILES, 'index.html': html});
    const text = invoke('pixel_ods_workspace_preview', PUBLISH.arguments, id, {content: [{type: 'text',
      text: `ODS independently published and read back 3 workspace static files (${receipt.bytes} bytes). Verified browser URL: ${receipt.url}.`}],
      details: receipt});
    return {receipt, text};
  };
  // The model's own inspection plan, re-bound to the snapshot it inspects.
  const inspect = (receipt, id) => {
    const params = {...INSPECT.arguments, siteId: receipt.siteId, sha256: receipt.sha256};
    const request = normalizeWorkspacePreviewInspectionParams(params);
    const state = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible', opacity: '1',
      hidden: false, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
    const details = {schemaVersion: 1, kind: INSPECTION_KIND, status: 'passed', siteId: params.siteId, sha256: params.sha256,
      planSha256: inspectionPlanHash(request), viewport: params.viewport,
      steps: params.steps.map((step, index) => ({index, ...step, before: state(step.action !== 'assert-hidden'),
        ...(step.action === 'click' ? {after: state(true)} : {}), stable: true, status: 'passed'})),
      diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE};
    return invoke(PREVIEW_INSPECTION_TOOL, params, id, {content: [{type: 'text', text: 'Preview inspection passed.'}], details});
  };
  const finalize = answer => guard.beforeAgentFinalize({lastAssistantMessage: answer}, context);
  const delivered = () => guard.deliveryVerificationForRun(context.runId);
  const revisions = () => results.filter(text => text.includes(REVISION_START)).length;
  return {write, edit, publish, inspect, finalize, delivered, revisions};
}

// Round 073 as recorded: three writes, publish (note), inspect the same snapshot.
function tower2Prefix(t) {
  const f = fixture(t);
  for (const [index, call] of WRITES.entries()) f.write(call.arguments.path.slice(DIRECTORY.length + 1), call.arguments.content, `write-${index}`);
  const first = f.publish(ORIGINAL, 'publish');
  assert.equal(first.receipt.sha256, TOWER2.publicationReceipt.sha256, 'replayed bytes reproduce the host snapshot digest');
  assert.ok(first.text.includes(NOTE), first.text);
  assert.doesNotMatch(first.text, /Requested text not found/, 'the name is on the page, so it is not reported missing');
  assert.doesNotMatch(first.text, new RegExp(REVISION_START), 'the receipt itself never spends the revision');
  const inspected = f.inspect(first.receipt, 'inspect');
  assert.ok(inspected.includes(REVISION), inspected);
  return {...f, first};
}

const literals = extractRequestedLiterals(TOWER2.prompt);
const site = (html, extra = {}) => Object.entries({...FILES, 'index.html': html, ...extra}).map(([name, text]) => ({path: name, text}));

test('round 073 page: the badge-only card name is reported against its longer heading', () => {
  assert.deepEqual(literals.filter(literal => literal.match === 'item').map(literal => literal.text),
    ['Dawn jazz', 'River lantern walk', 'Midnight sold-out concert']);
  assert.deepEqual(missingRequestedText(literals, site(ORIGINAL)), [{text: 'Dawn jazz', heading: 'Dawn Jazz at the Rose Pavilion'}]);
  // Each exact card heading passes, in any letter case, as the fleet check does.
  for (const heading of ['<h2>Dawn jazz</h2>', '<h2>Dawn Jazz</h2>', '<h2>\n  Dawn   jazz\n</h2>']) {
    assert.deepEqual(missingRequestedText(literals, site(ORIGINAL.replace(RENAMED, heading))), [], heading);
  }
  // An exact heading elsewhere satisfies the name; the longer heading is then not a renaming.
  assert.deepEqual(missingRequestedText(literals, site(ORIGINAL.replace(RENAMED, `<h2>Dawn jazz</h2><h3>At the Rose Pavilion</h3>`))), []);
  // A name absent from every element is still reported as missing text, not as a heading.
  assert.deepEqual(missingRequestedText(literals, site(ORIGINAL.replace('<div class="event-badge">Dawn jazz</div>', ''))),
    [{text: 'Dawn jazz'}]);
});

test('heading-only reports stay near zero false positives', () => {
  // Every case below does extract listed items, so each negative is a real one.
  const items = prompt => {
    const found = extractRequestedLiterals(prompt);
    assert.ok(found.filter(literal => literal.match === 'item').length >= 2, prompt);
    return found;
  };
  const page = html => [{path: 'index.html', text: html}];
  const cards = items('Include three event cards: Dawn jazz, River lantern walk, and Midnight sold-out concert.');
  // No other listed item is an exact heading: the page does not title items with headings.
  assert.deepEqual(missingRequestedText(items('Include three items: Tea, Coffee, and Juice.'),
    page('<h1>Tea and Coffee Menu</h1><ul><li>Tea</li><li>Coffee</li><li>Juice</li></ul>')), []);
  assert.deepEqual(missingRequestedText(cards, page('<span>Dawn jazz</span><h2>Dawn Jazz at Sunrise</h2>' +
    '<span>River lantern walk</span><h2>Lanterns</h2><span>Midnight sold-out concert</span><h2>Late show</h2>')), []);
  // Sibling items are exact headings of another level only.
  assert.deepEqual(missingRequestedText(items('Add three tabs: Home, About, and Contact.'),
    page('<nav><a>Home</a><a>About</a><a>Contact</a></nav><h1>Welcome Home</h1><h2>About</h2><h2>Contact</h2>')), []);
  // A longer heading that names another listed item belongs to that item.
  assert.deepEqual(missingRequestedText(items('Include three products: Tea, Tea latte, and Coffee.'),
    page('<span>Tea</span><h3>Tea latte</h3><h3>Coffee</h3><h3>Tea latte and Coffee bundle</h3>')), []);
  // Only whole words count.
  assert.deepEqual(missingRequestedText(items('Include three workshops: Art, Music, and Dance.'),
    page('<span>Art</span><h3>Party Artists</h3><h3>Music</h3><h3>Dance</h3>')), []);
  // A script may render the heading at runtime.
  assert.deepEqual(missingRequestedText(cards, site(ORIGINAL, {'script.js': "render({title: 'Dawn jazz'});"})), []);
  // Quoted names are not listed items and are never checked against headings.
  const quoted = extractRequestedLiterals('Add a card titled "Dawn jazz" and a card titled "River lantern walk".');
  assert.deepEqual(quoted.map(literal => literal.match), ['caseless', 'caseless']);
  assert.deepEqual(missingRequestedText(quoted,
    page('<span>Dawn jazz</span><h2>Dawn Jazz at the Rose Pavilion</h2><h2>River lantern walk</h2>')), []);
  // The same shape at h3, with the name at the end of the heading, is reported.
  assert.deepEqual(missingRequestedText(items('Include three workshops: Art, Music, and Dance.'),
    page('<span>Art</span><h3>Evening Art</h3><h3>Music</h3><h3>Dance</h3>')), [{text: 'Art', heading: 'Evening Art'}]);
  const long = `Dawn jazz ${'and more '.repeat(20)}`.trim();
  const [miss] = missingRequestedText(cards, site(ORIGINAL.replace(RENAMED, `<h2>${long}</h2>`)));
  assert.equal(Array.from(miss.heading).length, 120);
  assert.ok(miss.heading.endsWith('…') && long.startsWith(miss.heading.slice(0, -1)));
});

test('note, revision and delivery text are fixed around the names and the snapshot heading', () => {
  const preview = snapshot(FILES);
  const check = {siteId: preview.siteId, sha256: preview.sha256, missing: missingRequestedText(literals, site(ORIGINAL))};
  assert.equal(`[ODS Pixel next step] ${requestedTextInstruction(preview, check)}`, NOTE);
  assert.equal(`[ODS Pixel next step] ${requestedTextRevisionInstruction(preview, check)}`, REVISION);
  assert.equal(requestedTextRevisionInstruction(preview, check), REQUESTED_HEADING_REVISION_INSTRUCTION.join('["Dawn jazz"]'));
  assert.equal(requestedTextDeliveryNote(preview, check), FAILURE_NOTE);
  // Absent text keeps its existing wording, ahead of the heading report.
  const mixed = {...check, missing: [{text: 'Show sold out'}, ...check.missing]};
  assert.equal(requestedTextInstruction(preview, mixed), 'Requested text not found: "Show sold out". ' +
    'Use the owner\'s exact wording, republish and re-inspect. ' + NOTE.replace('[ODS Pixel next step] ', ''));
  assert.equal(requestedTextRevisionInstruction(preview, mixed), `${REQUESTED_TEXT_REVISION_INSTRUCTION.join('["Show sold out"]')} ` +
    REQUESTED_HEADING_REVISION_INSTRUCTION.join('["Dawn jazz"]'));
  assert.equal(requestedTextDeliveryNote(preview, mixed), 'The published page does not contain text the owner requested: "Show sold out". ' +
    FAILURE_NOTE);
  assert.equal(requestedTextInstruction({...preview, sha256: 'f'.repeat(64)}, check), undefined);
});

test('tower2 round 073: the revision fires once and an exact card heading is verified', t => {
  const f = tower2Prefix(t);
  const repaired = f.edit(ORIGINAL, RENAMED, '<h2>Dawn jazz</h2>\n      <p class="event-venue">At the Rose Pavilion</p>', 'repair');
  const second = f.publish(repaired, 'republish');
  assert.notEqual(second.receipt.sha256, f.first.receipt.sha256);
  assert.doesNotMatch(second.text, /longer heading|Requested text/);
  assert.match(second.text, /pixel_ods_workspace_preview_inspect directly/, 'the new snapshot needs its own interaction proof');
  assert.doesNotMatch(f.inspect(second.receipt, 'reinspect'), /longer heading|Requested/);
  assert.equal(f.finalize('Renamed the card heading and republished.'), undefined, 'nothing left to revise');
  assert.equal(f.revisions(), 1);
  const outcome = f.delivered();
  assert.equal(outcome.status, 'passed');
  assert.doesNotMatch(outcome.text, /longer headings/);
  assert.equal(outcome.preview.sha256, second.receipt.sha256);
});

test('tower2 round 073: the recorded answer after the revision gets the honest delivery note', t => {
  const f = tower2Prefix(t);
  assert.deepEqual(f.finalize(TOWER2.finalAnswer),
    {action: 'finalize', reason: 'Owner-requested text is still missing after the bounded revision.'});
  assert.equal(f.revisions(), 1);
  const outcome = f.delivered();
  assert.equal(outcome.status, 'failed');
  assert.ok(outcome.text.startsWith(`${FAILURE_NOTE}\n\n`), outcome.text);
  assert.doesNotMatch(f.inspect(f.first.receipt, 'inspect-again'), new RegExp(REVISION_START), 'never a second revision');
  assert.equal(f.revisions(), 1);
});

test('tower2 round 073: an answer straight after the receipt gets the same revision once at finalization', t => {
  const f = fixture(t);
  for (const [index, call] of WRITES.entries()) f.write(call.arguments.path.slice(DIRECTORY.length + 1), call.arguments.content, `write-${index}`);
  assert.ok(f.publish(ORIGINAL, 'publish').text.includes(NOTE));
  assert.deepEqual(f.finalize(TOWER2.finalAnswer), {action: 'revise', reason: 'Pixel has not completed every owner-requested verified step.',
    retry: {instruction: REVISION.replace('[ODS Pixel next step] ', ''),
      idempotencyKey: 'pixel-ods-workspace-preview-requested-text', maxAttempts: 1}});
  assert.equal(f.finalize(TOWER2.finalAnswer)?.action, 'finalize');
  assert.ok(f.delivered().text.startsWith(FAILURE_NOTE));
});
