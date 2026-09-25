import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard, WORKSPACE_PREVIEW_COMPLETE_REASON} from '../plugin/tool-loop-guard.mjs';
import {extractRequestedLiterals, missingRequestedText, requestedTextCheck, requestedTextInstruction,
  requestedTextRevisionInstruction, requestedTextDeliveryNote, MAX_REQUESTED_LITERALS, requestedLoadTexts,
  withHiddenRequestedText, REQUESTED_VISIBLE_TEXT_REVISION_INSTRUCTION} from '../plugin/requested-literals.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash, normalizeWorkspacePreviewInspectionParams}
  from '../plugin/workspace-preview-inspect.mjs';

// Owner prompts from ODS main qualification r049 (tower2 round 058).
const FLEET_CREATE = 'Create a polished responsive static event website in a new workspace directory fleet-qualification-4704eac67f72. Actually write files and publish a verified Pixel workspace preview. Page title and one h1 must be exactly "Night Garden FLEET-068aa1af7e". Include three event cards: Dawn jazz, River lantern walk, and Midnight sold-out concert. Initially hide the entire Midnight sold-out concert card. Provide an accessible button named exactly "Show sold out" that reveals that card when clicked. Mobile width 375px must not overflow horizontally. Use semantic HTML, attractive CSS, working JavaScript, no external libraries, no localStorage dependency. Include the preview URL in your final response. Do the work now.';
const FLEET_EDIT = 'Update that same website: change both page title and h1 to exactly "Night Garden FLEET-068aa1af7e Revised", change the accent to amber, add a visible footer "FLEET-068aa1af7e edited successfully". Preserve all three event cards and the working Show sold out behavior. Publish the updated preview and provide its new URL.';
const PT_CREATE = 'Crie um site de eventos responsivo no novo diretório jardim-noturno e publique a prévia. O título da página e um h1 devem ser exatamente “Jardim Noturno”. Inclua três cartões de evento: Jazz ao amanhecer, Caminhada das lanternas e Show esgotado da meia-noite. Adicione um botão chamado “Mostrar esgotados”.';
const DIRECTORY = 'fleet-qualification-4704eac67f72';

// Shape of the delivered tower2 artifact: renamed first card, comments that
// still carry the owner's wording, and a separate script.
function eventPage({title = 'Night Garden FLEET-068aa1af7e', h1 = title, dawn = 'Jazz at Dawn',
  river = 'River Lantern Walk', midnight = 'Midnight Sold-Out Concert', button = 'Show sold out', footer = '',
  comment = 'Dawn Jazz Event'} = {}) {
  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>${title}</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body>
  <header class="hero"><h1>${h1}</h1></header>
  <main>
    <!-- ${comment} -->
    <article class="event-card"><span class="event-tag">Dawn</span>
      <h3 class="event-title">${dawn}</h3>
      <button aria-label="Register for ${dawn}">Register</button></article>
    <!-- River Lantern Walk Event -->
    <article class="event-card"><h3 class="event-title">${river}</h3></article>
    <article class="event-card" id="midnight-concert-card" hidden><h3 class="event-title">${midnight}</h3></article>
    <button id="show-sold-out-btn" aria-expanded="false">
        ${button}
    </button>
  </main>
  <footer><p>&copy; 2026 Night Garden. ${footer}</p></footer>
  <script src="script.js"></script>
</body>
</html>
`;
}
const SCRIPT = `/**
 * Night Garden FLEET-068aa1af7e
 */
(function () {
  const button = document.getElementById('show-sold-out-btn');
  button.addEventListener('click', () => { button.textContent = 'Hide sold out'; });
})();
`;
const site = (html, script = SCRIPT) => [{path: 'index.html', text: html}, {path: 'script.js', text: script}];

test('fleet create prompt yields the quoted exact strings and the counted card names', () => {
  assert.deepEqual(extractRequestedLiterals(FLEET_CREATE).map(literal => ({...literal})), [
    {text: 'Night Garden FLEET-068aa1af7e', match: 'exact', targets: ['page title', 'h1']},
    {text: 'Show sold out', match: 'exact', targets: []},
    {text: 'Dawn jazz', match: 'item', targets: ['heading'], list: 0},
    {text: 'River lantern walk', match: 'item', targets: ['heading'], list: 0},
    {text: 'Midnight sold-out concert', match: 'item', targets: ['heading'], list: 0},
  ]);
});

test('fleet edit prompt binds the revised title to both title and h1, and the footer text', () => {
  assert.deepEqual(extractRequestedLiterals(FLEET_EDIT).map(literal => ({...literal})), [
    {text: 'Night Garden FLEET-068aa1af7e Revised', match: 'exact', targets: ['page title', 'h1']},
    {text: 'FLEET-068aa1af7e edited successfully', match: 'caseless', targets: []},
  ]);
});

test('Portuguese owner wording is extracted the same way', () => {
  assert.deepEqual(extractRequestedLiterals(PT_CREATE).map(literal => ({...literal})), [
    {text: 'Jardim Noturno', match: 'exact', targets: ['page title', 'h1']},
    {text: 'Mostrar esgotados', match: 'caseless', targets: []},
    {text: 'Jazz ao amanhecer', match: 'item', targets: ['heading'], list: 0},
    {text: 'Caminhada das lanternas', match: 'item', targets: ['heading'], list: 0},
    {text: 'Show esgotado da meia-noite', match: 'item', targets: ['heading'], list: 0},
  ]);
  const page = '<title>Jardim Noturno</title><h1>Jardim Noturno</h1><h3>Jazz ao Amanhecer</h3>' +
    '<h3>Caminhada das Lanternas</h3><h3>Show da meia-noite</h3><button>Mostrar esgotados</button>';
  assert.deepEqual(missingRequestedText(extractRequestedLiterals(PT_CREATE), [{path: 'index.html', text: page}]),
    [{text: 'Show esgotado da meia-noite'}]);
});

test('unquoted, example, code, negated and non-page quotations are not requirements', () => {
  for (const prompt of [
    'Create a landing page with a hero, pricing and contact sections and publish it.',
    'Create a landing page with a title like "Welcome" and a hero button, e.g. "Buy now".',
    'Publish a page whose heading is something like "Hello there" or similar.',
    'Build a page with a banner (for example "Spring sale").',
    'Publish a website. Example heading: "Grow local".',
    'Build a page.\n```html\n<h1>named exactly "Hello"</h1>\n```\nUse `button named "Go"` in the code.',
    'Build a page.\n> The title must be exactly "Quoted mail"',
    'Reply with exactly "DONE". Create a file named exactly "notes.txt" and a directory called "my-site".',
    'Create a page in a workspace directory named exactly "night-garden".',
    'Input header must be exactly "category,amount". The marker must be exactly "FLEET-1".',
    'Do not call the page "Untitled". Change the heading from "Old heading" to something else.',
    'Replace the text "Foo" on the page.',
    'Give the page a heading "Home" or "Start".',
    'Add a link to "https://example.com/docs" and a button with id "save-button".',
    'Include three sections: hero, pricing, and contact.',
    'Include three cards: a photo, a price, and a map.',
    'Include three cards: photo, price, and map.',
    "Don't add a button labeled \"Delete\".",
    'Add a page button that saves a file named exactly "Quarterly Report".',
    'The page must print exactly "Hello World" to the console.',
    'Follow three steps: Plan, Build, Publish.',
    'Include three event cards: Dawn jazz, River walk.',
    'Include two cards: Alpha or Beta.',
    'Create a CSV expense CLI; e.g. food 0.10+0.20 yields {"food":"0.30"}. Exact JSON schema: {"observedAt":"UTC ISO timestamp"}.',
    'Do not include three cards: Alpha, Beta, and Gamma.',
  ]) assert.deepEqual(extractRequestedLiterals(prompt), [], prompt);
});

test('extraction is bounded in count and length', () => {
  const many = Array.from({length: 20}, (_, index) => `Add a button named "Action ${index}".`).join(' ');
  assert.equal(extractRequestedLiterals(many).length, MAX_REQUESTED_LITERALS);
  assert.deepEqual(extractRequestedLiterals(`The h1 must be exactly "${'Long '.repeat(40)}".`), []);
  assert.deepEqual(extractRequestedLiterals(`Include three cards: ${'Very long name '.repeat(8)}, Beta, and Gamma.`), []);
});

test('the delivered fleet page misses only the renamed card; comments and aria labels do not count', () => {
  const literals = extractRequestedLiterals(FLEET_CREATE);
  assert.deepEqual(missingRequestedText(literals, site(eventPage())), [{text: 'Dawn jazz'}]);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({dawn: 'Dawn Jazz Session'}))), [{text: 'Dawn jazz'}],
    'a longer heading that merely contains the name is a renaming');
  assert.deepEqual(missingRequestedText(literals, site(eventPage({dawn: '🎷 Dawn jazz'}))), []);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({dawn: 'Dawn jazz'}))), []);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({comment: 'Dawn jazz'}))), [{text: 'Dawn jazz'}]);
});

test('exact strings are case-sensitive; card names are not', () => {
  const literals = extractRequestedLiterals(FLEET_CREATE);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({dawn: 'DAWN JAZZ', button: 'Show Sold Out'}))),
    [{text: 'Show sold out'}]);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({dawn: 'Dawn jazz', title: 'Night garden FLEET-068aa1af7e'}))),
    [{text: 'Night Garden FLEET-068aa1af7e', target: 'page title'}, {text: 'Night Garden FLEET-068aa1af7e', target: 'h1'}]);
});

test('edit round: title changed but h1 left behind is reported against the h1', () => {
  const literals = extractRequestedLiterals(FLEET_EDIT);
  const revised = 'Night Garden FLEET-068aa1af7e Revised';
  assert.deepEqual(missingRequestedText(literals, site(eventPage({title: revised, h1: 'Night Garden FLEET-068aa1af7e',
    footer: 'FLEET-068aa1af7e edited successfully'}))), [{text: revised, target: 'h1'}]);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({title: revised, footer: 'FLEET-068aa1af7e edited successfully'}))), []);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({title: revised}))), [{text: 'FLEET-068aa1af7e edited successfully'}]);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({title: revised, h1: 'Night Garden<br>FLEET-068aa1af7e <em>Revised</em>',
    footer: 'FLEET-068aa1af7e edited <b>successfully</b>'}))), []);
});

test('text a script may render at runtime is never reported missing', () => {
  const literals = extractRequestedLiterals(FLEET_CREATE);
  const rendered = eventPage({dawn: ''}).replace('<h3 class="event-title"></h3>', '');
  assert.deepEqual(missingRequestedText(literals, site(rendered, `const events = [{title: 'Dawn jazz'}];${SCRIPT}`)), []);
  assert.deepEqual(missingRequestedText(literals, [{path: 'index.html', text: rendered.replace('<script src="script.js"></script>',
    '<script>document.title = "x"; render("Dawn jazz");</script>')}]), []);
  assert.deepEqual(missingRequestedText(literals, [...site(rendered), {path: 'events.json', text: '[{"name":"Dawn jazz"}]'}]), []);
  // Whole-line script and CSS comments are not rendered text.
  assert.deepEqual(missingRequestedText(literals, site(rendered, `// Dawn jazz card\n/* Dawn jazz\n */\n${SCRIPT}`)), [{text: 'Dawn jazz'}]);
  assert.deepEqual(missingRequestedText(literals, [...site(rendered), {path: 'styles.css', text: '/* Dawn jazz */ .card{}'}]), [{text: 'Dawn jazz'}]);
  assert.deepEqual(missingRequestedText(literals, site(eventPage({dawn: 'Dawn &amp; jazz'}))), [{text: 'Dawn jazz'}]);
  assert.deepEqual(missingRequestedText(extractRequestedLiterals('Add a button named "Rock & Roll".'),
    [{path: 'index.html', text: '<button>Rock &amp; Roll</button>'}]), []);
});

function snapshot(files, relativeDirectory = DIRECTORY) {
  const entries = Object.entries(files).sort(([a], [b]) => a < b ? -1 : 1);
  const digest = createHash('sha256');
  let bytes = 0;
  for (const [name, content] of entries) {
    const encodedName = Buffer.from(name), data = Buffer.from(content), a = Buffer.alloc(4), b = Buffer.alloc(8);
    a.writeUInt32BE(encodedName.length); b.writeBigUInt64BE(BigInt(data.length));
    digest.update(a).update(encodedName).update(b).update(data);
    bytes += data.length;
  }
  const sha256 = digest.digest('hex'), siteId = `site-${sha256.slice(0, 24)}`;
  return {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory, siteId, sha256,
    entryFile: 'index.html', entrySha256: createHash('sha256').update(files['index.html']).digest('hex'),
    files: entries.length, bytes, port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200,
    readbackVerified: true, executable: false, overwritten: false,
    boundary: 'Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority.',
    publishedPaths: entries.map(([name]) => name), publishedPathsOmitted: 0};
}

function workspace(files, relativeDirectory = DIRECTORY) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-literals-'));
  for (const [name, content] of Object.entries(files)) {
    const file = path.join(root, relativeDirectory, name);
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, content);
  }
  return root;
}

test('checks bind only to bytes that reproduce the published snapshot digest', t => {
  const literals = extractRequestedLiterals(FLEET_CREATE);
  const files = {'index.html': eventPage(), 'script.js': SCRIPT, 'logo.png': '\x89PNG'};
  const preview = snapshot(files);
  const tracked = new Map(Object.entries(files).map(([name, content]) => [`${DIRECTORY}/${name}`, content]));
  const fromTracked = requestedTextCheck(literals, preview, {trackedContent: tracked});
  assert.deepEqual({...fromTracked, missing: [...fromTracked.missing]},
    {siteId: preview.siteId, sha256: preview.sha256, missing: [{text: 'Dawn jazz'}]});
  const root = workspace(files);
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  assert.deepEqual([...requestedTextCheck(literals, preview, {receipt: preview, workspaceRoot: root}).missing], [{text: 'Dawn jazz'}]);
  // Stale tracked content falls back to the workspace; changed workspace bytes are not the snapshot.
  tracked.set(`${DIRECTORY}/index.html`, eventPage({dawn: 'Dawn jazz'}));
  assert.deepEqual([...requestedTextCheck(literals, preview, {receipt: preview, trackedContent: tracked, workspaceRoot: root}).missing],
    [{text: 'Dawn jazz'}]);
  fs.writeFileSync(path.join(root, DIRECTORY, 'index.html'), eventPage({dawn: 'Dawn jazz'}));
  assert.equal(requestedTextCheck(literals, preview, {receipt: preview, workspaceRoot: root}), undefined);
  assert.equal(requestedTextCheck(literals, preview, {receipt: {...preview, publishedPathsOmitted: 1}, workspaceRoot: root}), undefined);
  assert.equal(requestedTextCheck(literals, preview, {receipt: preview, workspaceRoot: 'relative/root'}), undefined);
  assert.equal(requestedTextCheck([], preview, {trackedContent: tracked}), undefined);
  const single = {'index.html': eventPage()};
  const singleRoot = workspace(single);
  t.after(() => fs.rmSync(singleRoot, {recursive: true, force: true}));
  const {publishedPaths, publishedPathsOmitted, ...legacy} = snapshot(single);
  assert.deepEqual([...requestedTextCheck(literals, legacy, {receipt: legacy, workspaceRoot: singleRoot}).missing], [{text: 'Dawn jazz'}]);
});

test('instruction and delivery note are snapshot-bound and byte-stable apart from the literals', () => {
  const preview = snapshot({'index.html': eventPage()});
  const check = {siteId: preview.siteId, sha256: preview.sha256,
    missing: [{text: 'Dawn jazz'}, {text: 'Night Garden FLEET-068aa1af7e Revised', target: 'h1'}]};
  assert.equal(requestedTextInstruction(preview, check),
    'Requested text not found: "Dawn jazz", "Night Garden FLEET-068aa1af7e Revised" (h1). Use the owner\'s exact wording, republish and re-inspect.');
  assert.match(requestedTextDeliveryNote(preview, check), /^The published page does not contain text the owner requested: "Dawn jazz"/);
  assert.equal(requestedTextInstruction({...preview, sha256: 'f'.repeat(64)}, check), undefined);
  assert.equal(requestedTextInstruction(preview, {...check, missing: []}), undefined);
  assert.equal(requestedTextDeliveryNote(undefined, check), undefined);
});

const context = {agentId: 'pixel', runId: 'run', sessionId: 'session', sessionKey: 'opaque-key'};
function call(guard, name, params, id, result, runContext = context) {
  const ctx = {...runContext, toolName: name, toolCallId: id};
  const event = {toolName: name, runId: runContext.runId, toolCallId: id, params};
  const prepared = guard.beforeToolCall(event, ctx);
  assert.notEqual(prepared?.block, true, prepared?.blockReason);
  event.params = prepared?.params ?? params;
  if (result) guard.afterToolCall({...event, result}, ctx);
  return {event, ctx};
}
const persist = (guard, name, id, result, runContext = context) => guard.toolResultPersist(
  {toolName: name, toolCallId: id, message: {role: 'toolResult', toolName: name, toolCallId: id, ...result}},
  {...runContext, toolName: name, toolCallId: id});
const text = persisted => (persisted?.message?.content ?? []).map(block => block.text).join('\n');
const written = {content: [{type: 'text', text: 'Successfully wrote file.'}]};
function publishSite(guard, files, id, runContext = context) {
  for (const [name, content] of Object.entries(files)) {
    call(guard, 'write', {path: `${DIRECTORY}/${name}`, content}, `${id}-write-${name}`, written, runContext);
  }
  const preview = snapshot(files);
  call(guard, 'pixel_ods_workspace_preview', {relativeDirectory: DIRECTORY}, id, {details: preview}, runContext);
  return preview;
}

test('a renamed card is reported in the publication result, gates the claim once, and clears after repair', () => {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: false});
  guard.observeRun(context, 'pixel', {prompt: FLEET_CREATE});
  const preview = publishSite(guard, {'index.html': eventPage(), 'script.js': SCRIPT}, 'publish');
  const instruction = '[ODS Pixel next step] Requested text not found: "Dawn jazz". Use the owner\'s exact wording, republish and re-inspect.';
  const published = persist(guard, 'pixel_ods_workspace_preview', 'publish', {content: [{type: 'text', text: 'published'}], details: preview});
  assert.equal(published.message.isError, undefined, 'publication itself is never turned into a failure');
  assert.ok(text(published).includes(instruction));
  const outcome = guard.verificationForRun('run');
  assert.equal(outcome.status, 'failed');
  assert.equal(outcome.preview.sha256, preview.sha256, 'the published preview stays available');
  assert.match(outcome.text, /does not contain text the owner requested: "Dawn jazz"/);
  const retry = guard.beforeAgentFinalize({}, context)?.retry;
  assert.equal(retry?.idempotencyKey, 'pixel-ods-workspace-preview-requested-text');
  assert.equal(retry?.maxAttempts, 1);
  assert.equal(retry?.instruction, requestedTextRevisionInstruction(preview, {siteId: preview.siteId, sha256: preview.sha256,
    missing: [{text: 'Dawn jazz'}]}));
  // Identical coaching is not repeated on the next results (coachingDue), and
  // the finalization request already spent this run's one revision.
  call(guard, 'read', {path: `${DIRECTORY}/index.html`}, 'read', {content: [{type: 'text', text: eventPage()}]});
  assert.doesNotMatch(text(persist(guard, 'read', 'read', {content: [{type: 'text', text: eventPage()}]})), /Requested text/);
  const repaired = publishSite(guard, {'index.html': eventPage({dawn: 'Dawn jazz'}), 'script.js': SCRIPT}, 'republish');
  assert.notEqual(repaired.sha256, preview.sha256);
  const republished = persist(guard, 'pixel_ods_workspace_preview', 'republish', {content: [{type: 'text', text: 'published'}], details: repaired});
  assert.ok(!text(republished).includes('Requested text not found'));
  assert.ok(text(republished).includes(WORKSPACE_PREVIEW_COMPLETE_REASON));
  assert.equal(guard.verificationForRun('run').status, 'passed');
  assert.equal(guard.beforeAgentFinalize({}, context), undefined);
});

test('the requested-text repair precedes interaction inspection coaching', () => {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true});
  guard.observeRun(context, 'pixel', {prompt: FLEET_CREATE});
  const preview = publishSite(guard, {'index.html': eventPage(), 'script.js': SCRIPT}, 'publish');
  const published = text(persist(guard, 'pixel_ods_workspace_preview', 'publish', {content: [{type: 'text', text: 'published'}], details: preview}));
  assert.match(published, /Requested text not found: "Dawn jazz"/);
  assert.doesNotMatch(published, /pixel_ods_workspace_preview_inspect directly/);
  assert.equal(guard.beforeAgentFinalize({}, context)?.retry?.idempotencyKey, 'pixel-ods-workspace-preview-requested-text');
});

test('edit round read from the workspace: title changed, h1 not', t => {
  const revised = 'Night Garden FLEET-068aa1af7e Revised';
  const original = {'index.html': eventPage({dawn: 'Dawn jazz'}), 'script.js': SCRIPT};
  const root = workspace(original);
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: false});
  guard.observeRun(context, 'pixel', {prompt: FLEET_CREATE}, {workspaceRoot: root});
  const first = publishSite(guard, original, 'publish');
  assert.equal(guard.verificationForRun('run').status, 'passed');
  const edit = {...context, runId: 'edit'};
  guard.observeRun(edit, 'pixel', {prompt: FLEET_EDIT}, {workspaceRoot: root});
  const indexPath = `${DIRECTORY}/index.html`;
  call(guard, 'read', {path: indexPath}, 'edit-read', {content: [{type: 'text', text: original['index.html']}]}, edit);
  const edited = original['index.html'].replace('<title>Night Garden FLEET-068aa1af7e</title>', `<title>${revised}</title>`)
    .replace('&copy; 2026 Night Garden. ', '&copy; 2026 Night Garden. FLEET-068aa1af7e edited successfully');
  call(guard, 'edit', {path: indexPath, edits: [{oldText: '<title>Night Garden FLEET-068aa1af7e</title>', newText: `<title>${revised}</title>`}]},
    'edit-edit', {content: [{type: 'text', text: 'Successfully edited file.'}]}, edit);
  fs.writeFileSync(path.join(root, DIRECTORY, 'index.html'), edited);
  const next = snapshot({...original, 'index.html': edited});
  assert.notEqual(next.sha256, first.sha256);
  call(guard, 'pixel_ods_workspace_preview', {relativeDirectory: DIRECTORY}, 'edit-publish', {details: next}, edit);
  const published = text(persist(guard, 'pixel_ods_workspace_preview', 'edit-publish', {content: [{type: 'text', text: 'published'}], details: next}, edit));
  assert.ok(published.includes(`Requested text not found: "${revised}" (h1). Use the owner's exact wording, republish and re-inspect.`));
  assert.equal(guard.verificationForRun('edit').status, 'failed');
  assert.equal(guard.beforeAgentFinalize({}, edit)?.retry?.idempotencyKey, 'pixel-ods-workspace-preview-requested-text');
});

test('requests without required literals keep the existing publication outcome', () => {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: false});
  guard.observeRun(context, 'pixel', {prompt: `Create and publish a static event website in a new workspace directory ${DIRECTORY}. Make it colorful.`});
  const preview = publishSite(guard, {'index.html': eventPage(), 'script.js': SCRIPT}, 'publish');
  const published = text(persist(guard, 'pixel_ods_workspace_preview', 'publish', {content: [{type: 'text', text: 'published'}], details: preview}));
  assert.doesNotMatch(published, /Requested text/);
  assert.equal(guard.verificationForRun('run').status, 'passed');
  assert.equal(guard.beforeAgentFinalize({}, context), undefined);
});

// Browser inspection: requested text on the page but not visible when it loads.
test('only page copy the owner did not ask to start hidden is checked for visibility at load', () => {
  assert.deepEqual(requestedLoadTexts(FLEET_CREATE, extractRequestedLiterals(FLEET_CREATE)),
    ['Night Garden FLEET-068aa1af7e', 'Dawn jazz', 'River lantern walk'],
    'the card the owner asked to hide, and the button described by what it reveals, are not required at load');
  assert.deepEqual(requestedLoadTexts(FLEET_EDIT, extractRequestedLiterals(FLEET_EDIT)),
    ['Night Garden FLEET-068aa1af7e Revised', 'FLEET-068aa1af7e edited successfully']);
  const load = text => requestedLoadTexts(text, extractRequestedLiterals(text));
  assert.deepEqual(load('Set the page title to exactly "Tab Title Only".'), [], 'a browser tab title is never page copy');
  assert.deepEqual(load('Create a directory named site with index.html, app.js and styles.css.'), [], 'file names are not page copy');
  assert.deepEqual(load('Add a testimonial carousel with three testimonials: Ana, Bruno, Carla.'), []);
  assert.deepEqual(load('Crie um site com três cartões: Samba, Forró e Frevo. Esconda o cartão Frevo até clicar no botão.'), ['Samba', 'Forró']);
  assert.deepEqual(load('Add a banner that says "No hidden fees".'), ['No hidden fees'], 'words inside the literal are not cues');
  assert.deepEqual(load('Add an FAQ answer that says "Refunds take 5 days", collapsed until clicked.'), []);
  assert.deepEqual(requestedLoadTexts(FLEET_EDIT, []), []);
});

const HIDDEN_FOOTER = {text: 'FLEET-068aa1af7e edited successfully', status: 'hidden', element: 'p', reason: 'display-none',
  culprit: 'div#soldOutSection'};
const HIDDEN_NOTE = '"FLEET-068aa1af7e edited successfully" in p (display:none on div#soldOutSection)';
test('hidden requested text is a snapshot-bound miss with which text, which element and why', () => {
  const preview = {siteId: `site-${'a'.repeat(24)}`, sha256: 'a'.repeat(64), relativeDirectory: DIRECTORY};
  const renamed = {siteId: preview.siteId, sha256: preview.sha256, missing: [{text: 'Dawn jazz'}]};
  const check = withHiddenRequestedText(renamed, preview, [HIDDEN_FOOTER]);
  assert.deepEqual(check.missing, [{text: 'Dawn jazz'}, {text: HIDDEN_FOOTER.text, hidden: HIDDEN_FOOTER}]);
  assert.equal(requestedTextInstruction(preview, check),
    "Requested text not found: \"Dawn jazz\". Use the owner's exact wording, republish and re-inspect. " +
    `Requested text is on the page but not visible when it loads: ${HIDDEN_NOTE}. Make each one visible without a click ` +
    '(not inside hidden, collapsed or transparent content, and not the color of its background), then republish and re-inspect.');
  assert.equal(requestedTextRevisionInstruction(preview, withHiddenRequestedText(undefined, preview, [HIDDEN_FOOTER])),
    REQUESTED_VISIBLE_TEXT_REVISION_INSTRUCTION.join('["FLEET-068aa1af7e edited successfully"]'));
  assert.equal(requestedTextDeliveryNote(preview, withHiddenRequestedText(undefined, preview, [HIDDEN_FOOTER])),
    `The published page contains requested text that is not visible when it loads: ${HIDDEN_NOTE}. ` +
    'The preview is available, but that requirement is not met.');
  // A later inspection of the same snapshot replaces the hidden misses only.
  assert.deepEqual(withHiddenRequestedText(check, preview, []).missing, [{text: 'Dawn jazz'}]);
  assert.equal(withHiddenRequestedText(undefined, preview, []), undefined);
  assert.equal(withHiddenRequestedText(renamed, preview, undefined), renamed, 'no evidence changes nothing');
  // Evidence for another snapshot never attaches to a stale check.
  const other = {...preview, sha256: 'b'.repeat(64), siteId: `site-${'b'.repeat(24)}`};
  assert.deepEqual(withHiddenRequestedText(renamed, other, [HIDDEN_FOOTER]),
    {siteId: other.siteId, sha256: other.sha256, missing: [{text: HIDDEN_FOOTER.text, hidden: HIDDEN_FOOTER}]});
  assert.equal(requestedTextInstruction(preview, withHiddenRequestedText(renamed, other, [HIDDEN_FOOTER])), undefined);
  for (const [entry, why] of [
    [{reason: 'same-color', colors: ['#ffffff', '#ffffff'], culprit: 'body'}, 'text color #ffffff on the same background #ffffff of body'],
    [{reason: 'transparent', culprit: 'section.reveal'}, 'fully transparent, opacity on section.reveal'],
    [{reason: 'clipped'}, 'clipped away by overflow or clip on the element itself'],
    [{reason: 'off-page', culprit: 'nav'}, 'positioned outside the page (nav)'],
    [{reason: 'zero-size'}, 'rendered with no size'],
    [{reason: 'visibility-hidden', culprit: 'div'}, 'visibility:hidden on div'],
    [{reason: 'content-hidden', culprit: 'details'}, 'inside collapsed content (details)'],
    [{reason: 'transparent-text'}, 'transparent text color'],
  ]) {
    const note = requestedTextDeliveryNote(preview, withHiddenRequestedText(undefined, preview,
      [{text: 'X', status: 'hidden', element: 'p', ...entry}]));
    assert.ok(note.includes(`"X" in p (${why})`), note);
  }
});

// Fleet round 087 (tower1, ODS 074db9bf): the edit run put the requested footer
// inside the sold-out section, display:none until "Show sold out" is clicked.
// The inspection passed its show/hide steps and the run was delivered as done.
const PLAN = [{action: 'assert-hidden', locator: {selector: '#midnight-concert-card'}},
  {action: 'click', locator: {role: 'button', name: 'Show sold out', exact: true}},
  {action: 'assert-visible', locator: {selector: '#midnight-concert-card'}}];
function inspection(guard, preview, id, texts, runContext) {
  const params = {siteId: preview.siteId, sha256: preview.sha256, viewport: {width: 375, height: 812}, steps: PLAN};
  const {event, ctx} = call(guard, PREVIEW_INSPECTION_TOOL, params, id, undefined, runContext);
  const sent = guard.requestedTextsForInspection(structuredClone(params));
  const request = {...normalizeWorkspacePreviewInspectionParams(params), ...(sent.length ? {texts: sent} : {})};
  const observed = state => ({count: 1, visible: state, display: state ? 'block' : 'none', visibility: 'visible', opacity: '1',
    hidden: !state, hiddenUntilFound: false, rectCount: state ? 1 : 0});
  const details = {schemaVersion: 1, kind: INSPECTION_KIND, status: 'passed', siteId: preview.siteId, sha256: preview.sha256,
    planSha256: inspectionPlanHash(request), viewport: params.viewport,
    steps: PLAN.map((step, index) => ({index, ...step, before: observed(index !== 0), stable: true, status: 'passed',
      ...(step.action === 'click' ? {after: observed(true)} : {})})),
    diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [],
    requestedText: {viewport: {width: 1280, height: 720}, scrolled: true, texts: sent.map(text => texts[text] ?? {text, status: 'visible'})},
    scope: INSPECTION_SCOPE};
  const result = {content: [{type: 'text', text: 'Preview inspection passed.'}], details};
  guard.afterToolCall({...event, result}, ctx);
  return {sent, note: text(persist(guard, PREVIEW_INSPECTION_TOOL, id, result, runContext))};
}

test('round 087: requested text hidden at load is reported by inspection, withheld once, and cleared by a visible republish', t => {
  const revised = 'Night Garden FLEET-068aa1af7e Revised', footer = 'FLEET-068aa1af7e edited successfully';
  const original = {'index.html': eventPage({dawn: 'Dawn jazz'}), 'script.js': SCRIPT};
  const root = workspace(original);
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true});
  guard.observeRun(context, 'pixel', {prompt: FLEET_CREATE}, {workspaceRoot: root});
  const first = publishSite(guard, original, 'publish');
  assert.deepEqual(inspection(guard, first, 'inspect-create', {}).sent,
    ['Night Garden FLEET-068aa1af7e', 'Dawn jazz', 'River lantern walk'], 'the card the owner asked to hide is not sent');
  const edit = {...context, runId: 'edit'};
  guard.observeRun(edit, 'pixel', {prompt: FLEET_EDIT}, {workspaceRoot: root});
  const indexPath = `${DIRECTORY}/index.html`;
  call(guard, 'read', {path: indexPath}, 'edit-read', {content: [{type: 'text', text: original['index.html']}]}, edit);
  const page = footerHtml => eventPage({dawn: 'Dawn jazz', title: revised})
    .replace('<h3 class="event-title">Midnight Sold-Out Concert</h3>', `<h3 class="event-title">Midnight Sold-Out Concert</h3>${footerHtml}`);
  const hiddenPage = page(`<footer class="site-footer"><p>${footer}</p></footer>`);
  call(guard, 'write', {path: indexPath, content: hiddenPage}, 'edit-write', written, edit);
  const hidden = snapshot({...original, 'index.html': hiddenPage});
  call(guard, 'pixel_ods_workspace_preview', {relativeDirectory: DIRECTORY}, 'edit-publish', {details: hidden}, edit);
  const published = text(persist(guard, 'pixel_ods_workspace_preview', 'edit-publish', {content: [{type: 'text', text: 'published'}], details: hidden}, edit));
  assert.doesNotMatch(published, /Requested text/, 'the bytes contain every requested text');
  const entry = {text: footer, status: 'hidden', element: 'p', reason: 'display-none', culprit: 'article#midnight-concert-card'};
  const {sent, note} = inspection(guard, hidden, 'edit-inspect', {[footer]: entry}, edit);
  assert.deepEqual(sent, [revised, footer]);
  assert.ok(note.includes('[ODS Pixel next step] Requested text is on the page but not visible when it loads: ' +
    `"${footer}" in p (display:none on article#midnight-concert-card). Make each one visible without a click`), note);
  const withheld = guard.verificationForRun('edit');
  assert.equal(withheld.status, 'failed', 'no completion claim while the requested footer is invisible');
  assert.equal(withheld.preview.sha256, hidden.sha256, 'the published preview stays available');
  assert.match(withheld.text, /contains requested text that is not visible when it loads: "FLEET-068aa1af7e edited successfully" in p/);
  const retry = guard.beforeAgentFinalize({}, edit)?.retry;
  assert.equal(retry?.idempotencyKey, 'pixel-ods-workspace-preview-requested-text');
  assert.equal(retry?.instruction, REQUESTED_VISIBLE_TEXT_REVISION_INSTRUCTION.join(JSON.stringify([footer])));
  // The repair: the same footer outside the hidden card, then a new inspection.
  const visiblePage = page('').replace('</main>', `</main><footer class="site-footer"><p>${footer}</p></footer>`);
  call(guard, 'write', {path: indexPath, content: visiblePage}, 'repair-write', written, edit);
  const repaired = snapshot({...original, 'index.html': visiblePage});
  call(guard, 'pixel_ods_workspace_preview', {relativeDirectory: DIRECTORY}, 'repair-publish', {details: repaired}, edit);
  persist(guard, 'pixel_ods_workspace_preview', 'repair-publish', {content: [{type: 'text', text: 'published'}], details: repaired}, edit);
  const after = inspection(guard, repaired, 'repair-inspect', {}, edit);
  assert.doesNotMatch(after.note, /not visible when it loads/);
  assert.equal(guard.verificationForRun('edit').status, 'passed');
  assert.equal(guard.beforeAgentFinalize({}, edit), undefined);
  // Only the newest pending call with exactly these arguments gets texts.
  assert.deepEqual(guard.requestedTextsForInspection({siteId: repaired.siteId, sha256: repaired.sha256}), []);
});
