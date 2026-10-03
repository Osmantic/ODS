// Replays tower2 round 082 (Qwen3-Coder-Next, ODS main 771ee3b4): the owner
// listed three event cards by name; the model titled the first card
// "Sunrise Sessions" and kept "Dawn jazz" only as its badge, beside exact
// h2.event-title headings for the other two cards. The name was on the page,
// so the requested-text check passed, and the fleet check (a heading named
// exactly "Dawn jazz") failed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {extractRequestedLiterals, missingRequestedText, requestedTextCheck, requestedTextInstruction,
  requestedTextRevisionInstruction, requestedTextDeliveryNote, REQUESTED_ITEM_HEADING_REVISION_INSTRUCTION}
  from '../plugin/requested-literals.mjs';

const TOWER2 = JSON.parse(fs.readFileSync(new URL('./requested-heading-tower2-round082.json', import.meta.url), 'utf8'));
const DIRECTORY = 'fleet-qualification-1d43828fc26e';
const FILES = TOWER2.files;
const ORIGINAL = FILES['index.html'];
const BADGE = '<div class="event-badge">Dawn jazz</div>';
const RENAMED = '<h2 class="event-title">Sunrise Sessions</h2>';
const NOTE = 'Requested names are on the page but not as headings, while other listed items are: ["Dawn jazz"]. ' +
  'Use each exact name as the heading of its item, then republish and re-inspect.';
const REVISION = 'Requested names are still not headings on the published page: ["Dawn jazz"]. ' +
  'Use each exact name as the whole heading of its item, with the same heading element as the other listed items, ' +
  'put extra detail in body text, republish with pixel_ods_workspace_preview, and keep everything else unchanged.';
const FAILURE_NOTE = 'The published page shows requested names, but not as headings like the other listed items: "Dawn jazz". ' +
  'The preview is available, but that requirement is not met.';

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
  return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory: DIRECTORY, siteId, sha256,
    entryFile: 'index.html', entrySha256: createHash('sha256').update(files['index.html']).digest('hex'),
    files: entries.length, bytes, port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200,
    readbackVerified: true, executable: false, overwritten: false,
    publishedPaths: entries.map(([name]) => name), publishedPathsOmitted: 0};
}

const literals = extractRequestedLiterals(TOWER2.prompt);
const site = (html, extra = {}) => Object.entries({...FILES, 'index.html': html, ...extra}).map(([name, text]) => ({path: name, text}));
const cards = heading => [RENAMED, '<h2 class="event-title">River lantern walk</h2>',
  '<h2 class="event-title">Midnight sold-out concert</h2>'].reduce((html, from) => html.replace(from, heading(from)), ORIGINAL);

test('round 082 page: a card name kept only as a badge beside exact sibling card headings is a heading miss', () => {
  assert.deepEqual(literals.filter(literal => literal.match === 'item').map(literal => ({...literal})), [
    {text: 'Dawn jazz', match: 'item', targets: ['heading'], list: 0},
    {text: 'River lantern walk', match: 'item', targets: ['heading'], list: 0},
    {text: 'Midnight sold-out concert', match: 'item', targets: ['heading'], list: 0},
  ]);
  const preview = snapshot(FILES);
  assert.equal(preview.sha256, TOWER2.manifest.sha256, 'the fixture bytes reproduce the host snapshot digest');
  const tracked = new Map(Object.entries(FILES).map(([name, content]) => [`${DIRECTORY}/${name}`, content]));
  const check = requestedTextCheck(literals, preview, {receipt: preview, trackedContent: tracked});
  assert.deepEqual({...check, missing: [...check.missing]},
    {siteId: preview.siteId, sha256: preview.sha256, missing: [{text: 'Dawn jazz', unheaded: true}]});
  assert.equal(requestedTextInstruction(preview, check), NOTE);
  assert.equal(requestedTextRevisionInstruction(preview, check), REVISION);
  assert.equal(requestedTextRevisionInstruction(preview, check), REQUESTED_ITEM_HEADING_REVISION_INSTRUCTION.join('["Dawn jazz"]'));
  assert.equal(requestedTextDeliveryNote(preview, check), FAILURE_NOTE);
});

test('comments, scripts and styles never make a card name a heading', () => {
  // A comment inside the renamed heading is not its text.
  assert.deepEqual(missingRequestedText(literals, site(ORIGINAL.replace(RENAMED,
    '<h2 class="event-title"><!-- Dawn jazz -->Sunrise Sessions</h2>'))), [{text: 'Dawn jazz', unheaded: true}]);
  // Inline style comments and whole-line script comments are not rendered.
  assert.deepEqual(missingRequestedText(literals, site(ORIGINAL.replace('</head>', '<style>/* Dawn jazz */ .event-badge{}</style></head>'),
    {'script.js': `// Dawn jazz card\n${FILES['script.js']}`})), [{text: 'Dawn jazz', unheaded: true}]);
  // With the badge gone the name is only in the "Dawn Jazz Card" comment: still missing text.
  assert.ok(ORIGINAL.includes('<!-- Dawn Jazz Card -->'));
  assert.deepEqual(missingRequestedText(literals, site(ORIGINAL.replace(BADGE, ''))), [{text: 'Dawn jazz'}]);
  // Script string content may render the heading at runtime: never a verified miss.
  assert.deepEqual(missingRequestedText(literals, site(ORIGINAL, {'script.js': "render({title: 'Dawn jazz'});"})), []);
});

test('card-heading misses stay near zero false positives', () => {
  // Every listed card is an exact heading, in any letter case.
  for (const heading of ['<h2 class="event-title">Dawn jazz</h2>', '<h2 class="event-title">Dawn Jazz</h2>']) {
    assert.deepEqual(missingRequestedText(literals, site(ORIGINAL.replace(RENAMED, heading))), [], heading);
  }
  // No headings on the cards at all: the layout does not title cards with headings.
  assert.deepEqual(missingRequestedText(literals, site(cards(from => from.replace(/h2/g, 'p')))), []);
  // Sibling card headings exist, but no listed item is one of them (each name is a badge).
  const badges = cards(from => from.replace(/>(River lantern walk|Midnight sold-out concert)</, (_, name) => `>${name.split(' ')[0]} event<`))
    .replace('<div class="event-badge event-badge--soldout">SOLD OUT</div>', '<div class="event-badge">Midnight sold-out concert</div>');
  assert.deepEqual(missingRequestedText(literals, site(badges)), []);
  // A lone exact heading is not a set of sibling card headings.
  assert.deepEqual(missingRequestedText(literals, site(cards(from =>
    from.includes('River') ? from : from.replace(/h2/g, 'p')))), []);
  // Card headings are grouped by tag and class list: two exact h3.event-title
  // headings still title the cards, but one-member classes do not.
  assert.deepEqual(missingRequestedText(literals, site(cards(from =>
    from === RENAMED ? from : from.replace(/h2 class="event-title"/, 'h3 class="event-title"').replace('</h2>', '</h3>')))),
  [{text: 'Dawn jazz', unheaded: true}]);
  assert.deepEqual(missingRequestedText(literals, site(cards(from =>
    from === RENAMED ? from : from.replace('event-title', from.includes('River') ? 'river-title' : 'midnight-title')))), []);
  // The owner did not ask for cards or headings: listed names need only be present.
  for (const noun of ['events', 'workshops', 'tabs', 'products']) {
    const prompt = TOWER2.prompt.replace('three event cards', `three ${noun}`);
    const found = extractRequestedLiterals(prompt).filter(literal => literal.match === 'item');
    assert.equal(found.length, 3, prompt);
    assert.ok(found.every(literal => literal.targets.length === 0), noun);
    assert.deepEqual(missingRequestedText(extractRequestedLiterals(prompt), site(ORIGINAL)), [], noun);
  }
  // A heading that contains the name keeps the existing longer-heading report.
  assert.deepEqual(missingRequestedText(literals, site(ORIGINAL.replace(RENAMED, '<h2 class="event-title">Dawn jazz at sunrise</h2>'))),
    [{text: 'Dawn jazz', heading: 'Dawn jazz at sunrise'}]);
});

// Review reproducers for PR #6710.
test('sibling-heading evidence comes only from the same list, and "with titles" is not a headed list', () => {
  const page = html => [{path: 'index.html', text: html}];
  const check = (prompt, html) => missingRequestedText(extractRequestedLiterals(prompt), page(html));
  // Section headings (one list) never make dish cards (another list) unheaded.
  const cafe = 'Build a cafe page. Include three section headings: About, Menu, Visit. ' +
    'In the menu, add four dish cards: Pho, Banh mi, Spring rolls, Iced coffee.';
  assert.deepEqual(extractRequestedLiterals(cafe).map(literal => `${literal.text}:${literal.list}`),
    ['About:0', 'Menu:0', 'Visit:0', 'Pho:1', 'Banh mi:1', 'Spring rolls:1', 'Iced coffee:1']);
  assert.deepEqual(check(cafe, `<title>Cafe</title><h1>Cafe</h1>
    <section><h2 class="section-title">About</h2><p>Family run.</p></section>
    <section><h2 class="section-title">Menu</h2>
      <div class="card"><p class="dish-name">Pho</p></div><div class="card"><p class="dish-name">Banh mi</p></div>
      <div class="card"><p class="dish-name">Spring rolls</p></div><div class="card"><p class="dish-name">Iced coffee</p></div></section>
    <section><h2 class="section-title">Visit</h2></section>`), []);
  // Event cards titled by h3.card-title do not make speaker names unheaded.
  assert.deepEqual(check('Add three event cards: Dawn jazz, River walk, Midnight concert. Add two speaker cards: Ana Lima, Bo Chen.',
    `<article><h3 class="card-title">Dawn jazz</h3></article><article><h3 class="card-title">River walk</h3></article>
     <article><h3 class="card-title">Midnight concert</h3></article>
     <article><img alt="Ana Lima"><h3 class="card-title">Keynote</h3><p>Ana Lima</p></article>
     <article><img alt="Bo Chen"><h3 class="card-title">Panel</h3><p>Bo Chen</p></article>`), []);
  // Words after the noun do not make tabs headed items.
  const tabs = 'Add three tabs with titles: Overview, Pricing, FAQ.';
  assert.deepEqual(extractRequestedLiterals(tabs).map(literal => literal.targets.length), [0, 0, 0]);
  assert.deepEqual(check(tabs, `<div role="tablist"><button>Overview</button><button>Pricing</button><button>FAQ</button></div>
    <h2 class="panel-title">Overview</h2><h2 class="panel-title">Pricing</h2><h2 class="panel-title">Frequently asked questions</h2>`), []);
  // Still reported: two unheaded cards beside one exact heading, and a renamed
  // card whose heading uses another element than its siblings.
  const events = 'Include three event cards: Dawn jazz, River lantern walk, and Midnight sold-out concert.';
  assert.deepEqual(check(events, `<article><p>Dawn jazz</p><h2 class="event-title">Sunrise Sessions</h2></article>
    <article><p>River lantern walk</p><h2 class="event-title">Lantern stroll</h2></article>
    <article><h2 class="event-title">Midnight sold-out concert</h2></article>`),
  [{text: 'Dawn jazz', unheaded: true}, {text: 'River lantern walk', unheaded: true}]);
  assert.deepEqual(check(events, `<article><p>Dawn jazz</p><h3>Sunrise Sessions</h3></article>
    <article><h2>River lantern walk</h2></article><article><h2>Midnight sold-out concert</h2></article>`),
  [{text: 'Dawn jazz', unheaded: true}]);
  // The class list is read as an attribute, never from text inside another attribute.
  assert.deepEqual(check('Add three pricing cards: Free, Pro, Team.',
    `<div><h3 title="x class=a" class="plan">Free</h3></div><div><h3 title="x class=b" class="plan">Pro</h3></div>
     <div><strong>Team</strong><h3 class="plan">Contact us</h3></div>`), [{text: 'Team', unheaded: true}]);
});

const context = {agentId: 'pixel', runId: 'chatcmpl_a59d688b-777f-401c-8103-b129094fc676', sessionId: 'session',
  sessionKey: 'agent:pixel:openai-user:owner'};
function publish(guard, files, id) {
  const invoke = (tool, params, callId, result) => {
    const ctx = {...context, toolName: tool, toolCallId: callId};
    const event = {toolName: tool, runId: context.runId, toolCallId: callId, params};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    guard.afterToolCall({...event, params: prepared?.params ?? params, result}, ctx);
    const persisted = guard.toolResultPersist({toolName: tool, toolCallId: callId,
      message: {role: 'toolResult', toolName: tool, toolCallId: callId, ...result}}, ctx);
    return (persisted?.message?.content ?? result.content).map(block => block.text).join('\n');
  };
  for (const [name, content] of Object.entries(files)) {
    invoke('write', {path: `${DIRECTORY}/${name}`, content}, `${id}-${name}`,
      {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(content)} bytes to ${DIRECTORY}/${name}`}]});
  }
  const receipt = snapshot(files);
  return {receipt, text: invoke('pixel_ods_workspace_preview', {relativeDirectory: DIRECTORY}, id,
    {content: [{type: 'text', text: 'published'}], details: receipt})};
}

test('tower2 round 082: the receipt carries the note, the recorded answer gets one revision, then the honest failure', () => {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: false});
  guard.observeRun(context, 'pixel', {prompt: TOWER2.prompt});
  const first = publish(guard, FILES, 'publish');
  assert.equal(first.receipt.sha256, TOWER2.manifest.sha256);
  assert.ok(first.text.includes(`[ODS Pixel next step] ${NOTE}`), first.text);
  assert.doesNotMatch(first.text, /Requested text not found/);
  assert.deepEqual(guard.beforeAgentFinalize({lastAssistantMessage: TOWER2.finalAnswer}, context), {action: 'revise',
    reason: 'Pixel has not completed every owner-requested verified step.',
    retry: {instruction: REVISION, idempotencyKey: 'pixel-ods-workspace-preview-requested-text', maxAttempts: 1}});
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: TOWER2.finalAnswer}, context)?.action, 'finalize');
  const outcome = guard.verificationForRun(context.runId);
  assert.equal(outcome.status, 'failed');
  assert.ok(outcome.text.startsWith(`${FAILURE_NOTE}\n\n`), outcome.text);
});

test('tower2 round 082: renaming the card heading to the exact name clears the miss', () => {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: false});
  guard.observeRun(context, 'pixel', {prompt: TOWER2.prompt});
  publish(guard, FILES, 'publish');
  const repaired = publish(guard, {...FILES, 'index.html': ORIGINAL.replace(RENAMED, '<h2 class="event-title">Dawn jazz</h2>')}, 'republish');
  assert.doesNotMatch(repaired.text, /Requested names|Requested text/);
  assert.equal(guard.beforeAgentFinalize({lastAssistantMessage: 'Renamed the card heading and republished.'}, context), undefined);
  assert.equal(guard.verificationForRun(context.runId).status, 'passed');
});
