// Replays fleet qualification round 114 (Qwen3.5-9B, ODS main 5afd46cf): the
// owner listed three event cards and asked for the Midnight card to start
// hidden behind a "Show sold out" button. The model published that hidden card
// and a full copy of it in the section the button revealed, both
// h2.event-title "Midnight Sold-Out Concert". Every listed name was an exact
// heading, so the requested-text check passed; the show/hide inspection of the
// hidden card passed; the answer said the site was fully functional; the fleet
// check (each requested card heading exactly once) failed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {extractRequestedLiterals, missingRequestedText, requestedTextCheck, requestedTextInstruction,
  requestedTextRevisionInstruction, requestedTextDeliveryNote, REQUESTED_DUPLICATE_HEADING_REVISION_INSTRUCTION}
  from '../plugin/requested-literals.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {inspectionPlanHash, normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';

const ROUND = JSON.parse(fs.readFileSync(new URL('./requested-duplicate-heading-round114.json', import.meta.url), 'utf8'));
const [WRITE, PUBLISH, INSPECT] = ROUND.calls;
const DIRECTORY = PUBLISH.arguments.relativeDirectory;
const INDEX = WRITE.arguments.path;
const ORIGINAL = WRITE.arguments.content;
// The repair: drop the copy and the script lines that showed it; the button
// still reveals the one hidden card.
const COPY = /\n[ \t]*<div id="reveal-section" class="hidden-section">[\s\S]*?<\/article>\n[ \t]*<\/div>\n/;
const REPAIRED = ORIGINAL.replace(COPY, '\n').replace(/^.*revealSection.*\n/gm, '');
const NOTE = 'Requested names head more than one item, while each other listed item has one heading: ' +
  '"Midnight sold-out concert" (2 headings). Keep one item per name and remove the extra copies (a control that ' +
  'reveals a hidden item must show that item, not a second copy), then republish and re-inspect.';
const REVISION = 'Requested names still head more than one item on the published page: ["Midnight sold-out concert"]. ' +
  'Keep exactly one item for each requested name and remove the extra copies (a control that reveals a hidden item ' +
  'must show that item, not a second copy), republish with pixel_ods_workspace_preview, and keep everything else unchanged.';
const FAILURE_NOTE = 'The published page shows requested items more than once, while each other listed item appears once: ' +
  '"Midnight sold-out concert" (2 headings). The preview is available, but that requirement is not met.';

function snapshot(html) {
  const name = Buffer.from('index.html'), data = Buffer.from(html), a = Buffer.alloc(4), b = Buffer.alloc(8);
  a.writeUInt32BE(name.length); b.writeBigUInt64BE(BigInt(data.length));
  const sha256 = createHash('sha256').update(a).update(name).update(b).update(data).digest('hex');
  const siteId = `site-${sha256.slice(0, 24)}`;
  return {...ROUND.publication.details, siteId, sha256, entrySha256: createHash('sha256').update(data).digest('hex'),
    bytes: data.length, url: `http://${siteId}.localhost:9437/${siteId}/`};
}

const literals = extractRequestedLiterals(ROUND.prompt);
const items = literals.filter(literal => literal.match === 'item');
const card = (name, heading = '<h2 class="event-title">') =>
  `<article class="event-card">${heading}${name}</${heading.slice(1, 3)}><p>Details.</p></article>`;
const CARDS = ['Dawn jazz', 'River lantern walk', 'Midnight sold-out concert'].map(name => card(name)).join('\n');
const page = (...parts) => [{path: 'index.html', text: `<main>${[CARDS, ...parts].join('\n')}</main>`}];
const duplicates = (list, files) => missingRequestedText(list, files).filter(miss => miss.duplicated);

test('round 114 page: a requested card published twice under identical headings is a duplicate miss', () => {
  assert.deepEqual(items.map(literal => ({...literal})), [
    {text: 'Dawn jazz', match: 'item', targets: ['heading'], list: 0},
    {text: 'River lantern walk', match: 'item', targets: ['heading'], list: 0},
    {text: 'Midnight sold-out concert', match: 'item', targets: ['heading'], list: 0},
  ]);
  const preview = ROUND.publication.details;
  assert.equal(snapshot(ORIGINAL).sha256, preview.sha256, 'the recorded write reproduces the host snapshot digest');
  assert.equal(preview.sha256, ROUND.manifest.sha256);
  const check = requestedTextCheck(literals, preview, {receipt: preview, trackedContent: new Map([[INDEX, ORIGINAL]])});
  // The owner asked for this card to start hidden; the hidden card and the
  // copy the click reveals are both counted, since both are published.
  assert.deepEqual({...check, missing: [...check.missing]},
    {siteId: preview.siteId, sha256: preview.sha256, missing: [{text: 'Midnight sold-out concert', duplicated: 2}]});
  assert.equal(requestedTextInstruction(preview, check), NOTE);
  assert.equal(requestedTextRevisionInstruction(preview, check), REVISION);
  assert.equal(requestedTextRevisionInstruction(preview, check),
    REQUESTED_DUPLICATE_HEADING_REVISION_INSTRUCTION.join('["Midnight sold-out concert"]'));
  assert.equal(requestedTextDeliveryNote(preview, check), FAILURE_NOTE);
  assert.deepEqual(missingRequestedText(literals, [{path: 'index.html', text: REPAIRED}]), []);
});

test('duplicate headings are reported only for identical markup beside exactly-once siblings', () => {
  assert.deepEqual(duplicates(items, page()), []);
  assert.deepEqual(duplicates(items, page(`<section hidden>${card('Midnight sold-out concert')}</section>`)),
    [{text: 'Midnight sold-out concert', duplicated: 2}]);
  assert.deepEqual(duplicates(items, page(card('Midnight Sold-Out Concert'), card('Midnight sold-out concert!'))),
    [{text: 'Midnight sold-out concert', duplicated: 3}], 'letter case and edge punctuation, as for every heading name');
  // A heading in a nav or a teaser with another tag or class is not a copy of the card.
  assert.deepEqual(duplicates(items, page('<nav><h3 class="nav-title">Midnight sold-out concert</h3></nav>')), []);
  assert.deepEqual(duplicates(items, page(card('Midnight sold-out concert', '<h3 class="event-title">'))), []);
  assert.deepEqual(duplicates(items, [{path: 'index.html', text: ['Dawn jazz', 'River lantern walk', 'Midnight sold-out concert']
    .map(name => `<nav><h3 class="nav-title">${name}</h3></nav>${card(name)}`).join('\n')}]), [], 'a nav entry for every card');
  // Mobile and desktop variants of the same card carry different classes.
  assert.deepEqual(duplicates(items, [{path: 'index.html', text: [card('Dawn jazz'), card('River lantern walk'),
    card('Midnight sold-out concert', '<h2 class="event-title only-mobile">'),
    card('Midnight sold-out concert', '<h2 class="event-title only-desktop">')].join('\n')}]), []);
  // Every listed card repeated (a schedule and the cards, carousel clones): no exactly-once sibling.
  assert.deepEqual(duplicates(items, page(CARDS)), []);
  // A sibling that is not exactly one heading leaves only its own miss.
  assert.deepEqual(missingRequestedText(items, [{path: 'index.html', text: [card('River lantern walk'),
    card('Midnight sold-out concert'), card('Midnight sold-out concert')].join('\n')}]), [{text: 'Dawn jazz'}]);
  // A longer heading, a comment or an attribute is not a copy.
  assert.deepEqual(duplicates(items, page(card('Midnight sold-out concert tickets'),
    '<!-- <h2 class="event-title">Midnight sold-out concert</h2> -->',
    '<img alt="Midnight sold-out concert" src="midnight.png">')), []);
  // Names that were not listed as cards or headings need only be present.
  const events = extractRequestedLiterals(ROUND.prompt.replace('three event cards', 'three events'));
  assert.ok(events.filter(literal => literal.match === 'item').every(literal => literal.targets.length === 0));
  assert.deepEqual(duplicates(events, page(card('Midnight sold-out concert'))), []);
});

test('copies are counted within one HTML document, under one parent element tag', () => {
  // A second page that repeats a listed card (a waitlist or detail page) is not a copy.
  assert.deepEqual(duplicates(items, [...page(), {path: 'waitlist.html',
    text: `<main><h1>Join the waitlist</h1>${card('Midnight sold-out concert')}<form></form></main>`}]), []);
  assert.deepEqual(duplicates(items, [...page(), {path: 'midnight.html',
    text: '<main><h1>Tickets</h1><h2 class="event-title">Midnight sold-out concert</h2></main>'}]), []);
  assert.deepEqual(duplicates(items, [...page(), ...['dawn', 'river', 'midnight'].map((slug, index) => ({path: `${slug}.html`,
    text: card(['Dawn jazz', 'River lantern walk', 'Midnight sold-out concert'][index])}))]), [], 'a detail page per card');
  // A copy inside one document is still reported, with that document's count.
  assert.deepEqual(duplicates(items, [...page(card('Midnight sold-out concert')), {path: 'waitlist.html',
    text: card('Midnight sold-out concert')}]), [{text: 'Midnight sold-out concert', duplicated: 2}]);
  assert.deepEqual(duplicates(items, [{path: 'about.html', text: '<h2 class="event-title">Dawn jazz</h2>'},
    ...page(card('Midnight sold-out concert'))]), [{text: 'Midnight sold-out concert', duplicated: 2}],
  'siblings are counted in the document of the copies');
  // Headings without classes: a footer or dialog list is not a copy of the card headings.
  const bare = name => `<article><h3>${name}</h3><p>Details.</p></article>`;
  const bareCards = ['Dawn jazz', 'River lantern walk', 'Midnight sold-out concert'].map(bare).join('');
  assert.deepEqual(duplicates(items, [{path: 'index.html',
    text: `<main>${bareCards}</main><footer><h3>Midnight sold-out concert</h3></footer>`}]), []);
  assert.deepEqual(duplicates(items, page('<dialog id="details"><h2 class="event-title">Midnight sold-out concert</h2></dialog>')), []);
  assert.deepEqual(duplicates(items, [{path: 'index.html',
    text: `<main>${bareCards}</main><section hidden>${bare('Midnight sold-out concert')}</section>`}]),
  [{text: 'Midnight sold-out concert', duplicated: 2}], 'a copied card under the same parent tag');
});

test('template, noscript and script-rendered copies are never duplicate misses', () => {
  assert.deepEqual(duplicates(items, page(`<template id="sold-out">${card('Midnight sold-out concert')}</template>`)), []);
  assert.deepEqual(duplicates(items, page(`<noscript>${card('Midnight sold-out concert')}</noscript>`)), []);
  // A script that may render the name keeps every check for it silent.
  assert.deepEqual(duplicates(items, page(card('Midnight sold-out concert'),
    `<script>const soldOut = {title: 'Midnight sold-out concert'};</script>`)), []);
  assert.deepEqual(duplicates(items, [...page(card('Midnight sold-out concert')),
    {path: 'cards.js', text: "grid.append(render('Midnight sold-out concert'));"}]), []);
  // Whole-line script comments are not rendered, so they do not silence it.
  assert.deepEqual(duplicates(items, page(card('Midnight sold-out concert'),
    '<script>\n// Midnight sold-out concert card\nshow();\n</script>')), [{text: 'Midnight sold-out concert', duplicated: 2}]);
});

test('an owner request to feature or repeat content permits a repeated card heading', () => {
  const repeated = page(card('Midnight sold-out concert'));
  for (const extra of [' Also feature the Midnight sold-out concert card at the top of the page.',
    ' Show Dawn jazz again in a highlights strip.', ' Repeat the sold-out card in the footer.',
    ' The Midnight card should also appear in a pinned banner.']) {
    const cued = extractRequestedLiterals(`${ROUND.prompt}${extra}`);
    assert.ok(cued.filter(literal => literal.match === 'item').every(literal => literal.repeats === true), extra);
    assert.deepEqual(duplicates(cued, repeated), [], extra);
  }
  const portuguese = extractRequestedLiterals('Inclua três cartões de eventos: Jazz, Rio, Meia-noite. Repita o cartão Jazz no rodapé.');
  assert.deepEqual(portuguese.map(literal => [literal.text, literal.repeats]), [['Jazz', true], ['Rio', true], ['Meia-noite', true]]);
  assert.deepEqual(duplicates(literals, repeated), [{text: 'Midnight sold-out concert', duplicated: 2}], 'the recorded request');
});

// Any plausible owner request for repetition keeps the check silent.
test('owner requests that may show a card twice keep the duplicate check silent', () => {
  const repeated = page(card('Dawn jazz'));
  const silent = extra => {
    const cued = extractRequestedLiterals(`${ROUND.prompt}${extra}`);
    assert.deepEqual(cued.filter(literal => literal.match === 'item').map(literal => literal.repeats),
      [true, true, true], extra);
    assert.deepEqual(duplicates(cued, repeated), [], extra);
  };
  for (const extra of [
    ' Show Dawn jazz in the hero too.', ' Put Dawn jazz in the sidebar too.', ' Put Dawn jazz in the header too, please.',
    ' Put Dawn jazz in the footer too and make it bold.', ' Add Dawn jazz to the hero as well.',
    ' Show Dawn jazz as well in the hero.', ' Also show Dawn jazz in a "Next up" banner at the top.',
    ' The hero should also show Dawn jazz.', ' Dawn jazz should also be listed under "Tonight".',
    ' Keep Dawn jazz in both places.', ' Show Dawn jazz both in the hero and in the grid.',
    ' Show Dawn jazz across both sections.', ' Show Dawn jazz in both the hero and the grid.',
    ' Show Dawn jazz again at the bottom.', ' Repeat it in the footer.', ' Mirror the Dawn jazz card in the sidebar.',
    ' List the first event a second time under "Tonight".', ' Show Dawn jazz once more in the footer.',
    ' Put a copy of the Dawn jazz card in the hero.', ' Show the next event at the top and bottom.',
    ' Show the cards in a carousel.', ' Add a slideshow of the events.', ' Run the event names in a marquee.',
  ]) silent(extra);
  for (const text of ['Inclua três cartões de eventos: Jazz, Rio, Meia-noite. Mostre o Jazz no topo também.',
    'Inclua três cartões de eventos: Jazz, Rio, Meia-noite. Também mostre o Jazz no rodapé.',
    'Inclua três cartões de eventos: Jazz, Rio, Meia-noite. Deixe o Jazz em ambos os lugares.',
    'Inclua três cartões de eventos: Jazz, Rio, Meia-noite. Espelhe o cartão Jazz na lateral.',
    'Inclua três cartões de eventos: Jazz, Rio, Meia-noite. Mostre os cartões em um carrossel.']) {
    assert.deepEqual(extractRequestedLiterals(text).map(literal => literal.repeats), [true, true, true], text);
  }
});

test('words that do not ask for repetition, and host route notes, leave the duplicate check on', () => {
  const repeated = page(card('Dawn jazz'));
  for (const extra of [' It should also work on mobile.', ' The hero should also be responsive.',
    ' Keep the descriptions from getting too long.', ' Do not make the cards too tall or too wide.',
    ' Include a header as well as a footer.', ' Use both a header and a footer.']) {
    const plain = extractRequestedLiterals(`${ROUND.prompt}${extra}`);
    assert.equal(plain.filter(literal => literal.match === 'item').length, 3, extra);
    assert.ok(plain.every(literal => !literal.repeats), extra);
    assert.deepEqual(duplicates(plain, repeated), [{text: 'Dawn jazz', duplicated: 2}], extra);
  }
  // Route notes the host appends to the owner's message (pixel-edge's delivery
  // requirement, workspace task route and command completion route) are not
  // owner words: "copies of existing files" and "do not call exec again".
  const routed = `${ROUND.prompt}\n\n[ODS Portal delivery requirement: Answer the owner's complete message above. ` +
    'If it asks for exact text, copy that full exact text. Do not answer with a generic acknowledgement. Do not output NO_REPLY.]' +
    '\n[ODS Portal workspace task route: Perform the requested workspace mutation before verification. When tool_call is ' +
    'visible, use it with id write and normal write args for every new file you author. edit cannot create a file and ' +
    'requires a non-empty oldText copied from an existing file. Use edit or apply_patch only after reading an existing file. ' +
    'Then use exec only for readback, tests, the requested digest, or copies of existing files: make copies and JSON maps ' +
    'of file contents with one short command that reads the real files, such as cp or python3 with json.dump, never by ' +
    're-typing them. Do not repeatedly list directories or hash proposed text instead of the created file.]' +
    '\n[ODS Portal command completion route: Call exec exactly once for the owner\'s command. If exec returns a running ' +
    'process session, do not call exec again. Use the visible tool_call control with id process and args containing action ' +
    'poll plus that exact returned sessionId, and keep polling only that session until its terminal output is available. ' +
    'Report the real terminal output; do not simulate, shorten, or restart the command.]';
  const guarded = extractRequestedLiterals(routed);
  assert.deepEqual(guarded.filter(literal => literal.match === 'item').map(literal => literal.repeats),
    [undefined, undefined, undefined]);
  assert.deepEqual(duplicates(guarded, repeated), [{text: 'Dawn jazz', duplicated: 2}]);
});

const context = {agentId: 'pixel', runId: 'run-round114', sessionId: 'session',
  sessionKey: 'agent:pixel:openai-user:owner'};
function fixture() {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  guard.observeRun(context, 'pixel', {prompt: ROUND.prompt});
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
  const write = (html, id) => invoke('write', {path: INDEX, content: html}, id,
    {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(html)} bytes to ${INDEX}`}]});
  const publish = (html, id) => {
    const receipt = snapshot(html);
    const text = ROUND.publication.text.replaceAll(ROUND.publication.details.siteId, receipt.siteId)
      .replaceAll(ROUND.publication.details.sha256, receipt.sha256).replace('12146 bytes', `${receipt.bytes} bytes`);
    return {receipt, text: invoke(PUBLISH.name, {relativeDirectory: DIRECTORY}, id, {content: [{type: 'text', text}], details: receipt})};
  };
  // The recorded inspection plan and receipt, re-bound to the inspected snapshot.
  const inspect = (receipt, id) => {
    const params = {...INSPECT.arguments, siteId: receipt.siteId, sha256: receipt.sha256};
    const details = {...ROUND.inspection.details, siteId: receipt.siteId, sha256: receipt.sha256,
      planSha256: inspectionPlanHash(normalizeWorkspacePreviewInspectionParams(params))};
    return invoke(PREVIEW_INSPECTION_TOOL, params, id, {content: [{type: 'text', text: ROUND.inspection.text}], details});
  };
  const finalize = answer => guard.beforeAgentFinalize({lastAssistantMessage: answer}, context);
  const delivered = () => guard.deliveryVerificationForRun(context.runId);
  return {guard, write, publish, inspect, finalize, delivered};
}

// Round 114 as recorded: write, publish, inspect the hidden card's show/hide.
function round114() {
  const f = fixture();
  f.write(ORIGINAL, 'write');
  const first = f.publish(ORIGINAL, 'publish');
  assert.equal(first.receipt.sha256, ROUND.publication.details.sha256);
  assert.ok(first.text.includes(`[ODS Pixel next step] ${NOTE}`), first.text);
  assert.doesNotMatch(first.text, /Requested text not found|Requested names still/);
  const inspected = f.inspect(first.receipt, 'inspect');
  assert.equal(ROUND.inspection.details.planSha256,
    inspectionPlanHash(normalizeWorkspacePreviewInspectionParams(INSPECT.arguments)), 'the recorded plan hash');
  assert.match(inspected, /^Preview inspection passed\./);
  assert.ok(inspected.includes(`[ODS Pixel next step] ${REVISION}`), inspected);
  return {...f, first};
}

test('round 114: the receipt carries the note, the passing inspection the one revision, and the owner hears the requirement is unmet', () => {
  const f = round114();
  assert.deepEqual(f.finalize(ROUND.finalAnswer),
    {action: 'finalize', reason: 'Owner-requested text is still missing after the bounded revision.'});
  const outcome = f.delivered();
  assert.equal(outcome.status, 'failed');
  assert.ok(outcome.text.startsWith(`${FAILURE_NOTE}\n\n`), outcome.text);
  assert.match(outcome.text, /Browser inspection passed for the submitted show\/hide checks only/);
  // The recorded answer claimed a fully functional site; the owner gets the host's statement instead.
  assert.match(ROUND.finalAnswer, /fully functional/);
  const reply = f.guard.replyPayloadSending({kind: 'final', runId: context.runId, payload: {text: ROUND.finalAnswer}});
  assert.equal(reply.payload.text, outcome.text);
  assert.doesNotMatch(reply.payload.text, /fully functional/);
});

test('round 114: revealing the one hidden card instead of a copy clears the miss', () => {
  const f = round114();
  assert.equal((REPAIRED.match(/Midnight Sold-Out Concert<\/h2>/g) ?? []).length, 1);
  assert.match(REPAIRED, /midnightCardHidden\.classList\.remove\('hidden'\)/, 'the button still reveals the card');
  f.write(REPAIRED, 'repair');
  const second = f.publish(REPAIRED, 'republish');
  assert.notEqual(second.receipt.sha256, f.first.receipt.sha256);
  assert.doesNotMatch(second.text, /Requested names|Requested text/);
  assert.match(second.text, /requested show\/hide interaction is not/, 'the new snapshot needs its own interaction proof');
  assert.doesNotMatch(f.inspect(second.receipt, 'reinspect'), /Requested names|Requested text/);
  assert.equal(f.finalize('Removed the duplicate card and republished.'), undefined);
  const outcome = f.delivered();
  assert.equal(outcome.status, 'passed');
  assert.equal(outcome.preview.sha256, second.receipt.sha256);
});
