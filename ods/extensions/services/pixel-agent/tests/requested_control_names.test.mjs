// Replays tower2 round 100 (Qwen3-Coder-Next, ODS main b060c6ae): the owner
// asked for "an accessible button named exactly "Show sold out"". The button's
// text was right, but script.js ran
// setAttribute('aria-label', 'Show the sold out midnight concert card') on load.
// The model's exact-name click matched nothing, the inspector blamed hidden
// elements, the model switched to a CSS selector, that inspection passed, and
// Pixel certified the site. The fleet's getByRole('button',
// {name: 'Show sold out', exact: true}) click then failed. Every inspection now
// reports the load-time accessible names of buttons and links (computed after
// the page scripts ran), and a requested control name that is not among them
// is a repair step on that inspection's result and withholds certification.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard, WORKSPACE_PREVIEW_COMPLETE_REASON} from '../plugin/tool-loop-guard.mjs';
import {extractRequestedControlNames, extractRequestedLiterals, requestedControlNameCheck, requestedControlSources,
  requestedControlNameInstruction, requestedControlNameRevisionInstruction, requestedTextDeliveryNote,
  REQUESTED_CONTROL_NAME_REVISION_INSTRUCTION, MAX_REQUESTED_CONTROL_NAMES} from '../plugin/requested-literals.mjs';
import {PREVIEW_INSPECTION_TOOL, boundInspectionControls} from '../plugin/preview-interaction-assurance.mjs';
import {createWorkspacePreviewInspectTool, normalizeWorkspacePreviewInspectionParams, inspectionPlanHash}
  from '../plugin/workspace-preview-inspect.mjs';

const TOWER2 = JSON.parse(fs.readFileSync(new URL('./control-names-tower2-round100.json', import.meta.url), 'utf8'));
const FIXTURE = new URL('../../../../tests/fixtures/preview-controls/tower2-r100/', import.meta.url);
const FILES = Object.fromEntries(['index.html', 'script.js', 'styles.css']
  .map(name => [name, fs.readFileSync(new URL(name, FIXTURE), 'utf8')]));
const REPAIRED = {...FILES, 'script.js': FILES['script.js'].replace(TOWER2.repair.remove, '')};
// The repaired page with only the button's markup changed: correct, accessible
// buttons whose hidden decorations (an aria-hidden icon or chevron, a hidden
// alternate label, a display:none badge) are no part of the accessible name.
const VARIANTS = Object.keys(TOWER2.variants.markup);
function variantFiles(name) {
  assert.equal(REPAIRED['index.html'].split(TOWER2.variants.button).length, 2);
  return {...REPAIRED, 'index.html': REPAIRED['index.html'].replace(TOWER2.variants.button, () => TOWER2.variants.markup[name])};
}
const RECEIPT = TOWER2.publicationReceipt;
const DIRECTORY = RECEIPT.relativeDirectory;
const [ROLE_CALL, CSS_CALL] = TOWER2.calls;
const NAMES = extractRequestedControlNames(TOWER2.prompt);
const WANT = [{text: 'Show sold out', role: 'button', match: 'exact'}];
// What the model now reads on both round 100 inspections of that snapshot.
const REPAIR = 'The owner requested a button named exactly "Show sold out", but after the page scripts ran no button ' +
  'has that accessible name: the button whose text is "Show sold out" is named "Show the sold out midnight concert card" ' +
  'by an aria-label that a published script sets when the page loads (["script.js"]), which replaces its text as the ' +
  'accessible name. Remove that override or make it exactly "Show sold out", republish, then inspect the new snapshot.';
const NEXT = `[ODS Pixel next step] ${REPAIR}`;
const FAILURE_NOTE = 'The published page has no button named exactly "Show sold out" after its scripts run ' +
  '(the button with that text is named "Show the sold out midnight concert card"). ' +
  'The preview is available, but that requirement is not met.';

// A host publication receipt for these bytes (the digest the host computes).
function snapshot(files) {
  const names = Object.keys(files).sort(), digest = createHash('sha256');
  let bytes = 0;
  for (const name of names) {
    const encodedName = Buffer.from(name), data = Buffer.from(files[name]);
    const a = Buffer.alloc(4), b = Buffer.alloc(8);
    a.writeUInt32BE(encodedName.length); b.writeBigUInt64BE(BigInt(data.length));
    digest.update(a).update(encodedName).update(b).update(data);
    bytes += data.length;
  }
  const sha256 = digest.digest('hex'), siteId = `site-${sha256.slice(0, 24)}`;
  return {...RECEIPT, siteId, sha256, bytes, files: names.length, publishedPaths: names,
    entrySha256: createHash('sha256').update(files['index.html']).digest('hex'),
    url: `http://${siteId}.localhost:${RECEIPT.port}/${siteId}/`};
}
const evidence = (preview, controls) => ({siteId: preview.siteId, sha256: preview.sha256, controls});
const plain = value => JSON.parse(JSON.stringify(value));

test('round 100 prompt: the quoted button name is a control name as well as requested text', () => {
  assert.deepEqual(plain(NAMES), WANT);
  const literals = extractRequestedLiterals(TOWER2.prompt);
  assert.deepEqual(plain(literals.find(literal => literal.text === 'Show sold out')),
    {text: 'Show sold out', match: 'exact', targets: []}, 'the text literal keeps its shape');
  assert.ok(literals.every(literal => !Object.hasOwn(literal, 'control')));
});

test('control names need the control noun directly before a naming cue', () => {
  const names = prompt => plain(extractRequestedControlNames(prompt));
  assert.deepEqual(names('Add a link called "Docs" to the footer.'), [{text: 'Docs', role: 'link', match: 'caseless'}]);
  assert.deepEqual(names('Add a button labeled "Save".'), [{text: 'Save', role: 'button', match: 'caseless'}]);
  assert.deepEqual(names('Add a button, named exactly "Go".'), [{text: 'Go', role: 'button', match: 'exact'}]);
  assert.deepEqual(names('Add an icon button with the label "Close" in the header.'),
    [{text: 'Close', role: 'button', match: 'caseless'}]);
  assert.deepEqual(names('Adicione um botão chamado “Mostrar esgotados”.'),
    [{text: 'Mostrar esgotados', role: 'button', match: 'caseless'}]);
  for (const prompt of [
    'Add a "Buy now" button.',
    'Add a button with the text "Buy now".',
    'The button text must be exactly "Go".',
    "Don't add a button labeled \"Delete\".",
    'Add a button that saves a file named exactly "Quarterly Report".',
    'Add a button with id "save-button".',
    'Add a hero button, e.g. named "Buy now".',
    'Build a page.\n```html\n<button>named exactly "Hello"</button>\n```\nUse `a button named "Go"` in the code.',
    'Give the page a heading named "Home".',
  ]) assert.deepEqual(names(prompt), [], prompt);
  const many = Array.from({length: 12}, (_, index) => `Add a button named "Action ${index}".`).join(' ');
  assert.equal(extractRequestedControlNames(many).length, MAX_REQUESTED_CONTROL_NAMES);
});

test('the recorded bytes reproduce the published snapshot, and name provenance comes from them', () => {
  const preview = snapshot(FILES);
  assert.equal(preview.sha256, RECEIPT.sha256, 'the fixture is the exact round 100 snapshot');
  assert.equal(preview.entrySha256, RECEIPT.entrySha256);
  assert.equal(preview.bytes, RECEIPT.bytes);
  const tracked = new Map(Object.entries(FILES).map(([name, text]) => [`${DIRECTORY}/${name}`, text]));
  assert.deepEqual(plain(requestedControlSources(NAMES, RECEIPT, {receipt: RECEIPT, trackedContent: tracked})),
    {siteId: RECEIPT.siteId, sha256: RECEIPT.sha256, scripts: ['script.js'], markupLabels: ['Show sold out']});
  // Repaired: no script writes a name; the markup label remains.
  const repaired = snapshot(REPAIRED);
  const repairedTracked = new Map(Object.entries(REPAIRED).map(([name, text]) => [`${DIRECTORY}/${name}`, text]));
  assert.deepEqual(plain(requestedControlSources(NAMES, repaired, {receipt: repaired, trackedContent: repairedTracked})).scripts, []);
  // Bytes that do not reproduce the digest, or no requested names: nothing.
  assert.equal(requestedControlSources(NAMES, RECEIPT, {receipt: RECEIPT, trackedContent: repairedTracked}), undefined);
  assert.equal(requestedControlSources([], RECEIPT, {receipt: RECEIPT, trackedContent: tracked}), undefined);
});

test('fixture pages: a replaced name is flagged, a correct or icon button with that name passes', () => {
  const preview = snapshot(FILES);
  const check = name => plain(requestedControlNameCheck(NAMES, preview, evidence(preview, TOWER2.controls[name])).missing);
  // tower2's page: the text is right, a script's aria-label replaced the name.
  assert.deepEqual(check('tower2'), [{...WANT[0], candidate: {role: 'button', name: 'Show the sold out midnight concert card',
    visible: true, source: 'aria-label', text: 'Show sold out'}}]);
  // Markup aria-label that differs from the text: the same miss.
  assert.deepEqual(check('mismatch'), [{...WANT[0], candidate: {role: 'button', name: 'Reveal the sold-out concert',
    visible: true, source: 'aria-label', text: 'Show sold out'}}]);
  // The name is right: from the text, from an icon button's aria-label, and
  // on tower2's page once the script no longer replaces it.
  for (const name of ['correct', 'icon', 'tower2-repaired']) assert.deepEqual(check(name), [], name);
  assert.deepEqual(TOWER2.controls.icon.items, [{role: 'button', name: 'Show sold out', visible: true, source: 'aria-label'}]);
  const sources = {siteId: preview.siteId, sha256: preview.sha256, scripts: [], markupLabels: ['Reveal the sold-out concert']};
  const mismatch = requestedControlNameCheck(NAMES, preview, evidence(preview, TOWER2.controls.mismatch));
  assert.match(requestedControlNameInstruction(preview, mismatch, sources),
    /is named "Reveal the sold-out concert" by its aria-label attribute in the HTML, which replaces its text/);
});

test('a correct control whose hidden decorations are no part of its name is not a missing name', () => {
  // The capsule names a rendered control as Chromium and the fleet's default
  // getByRole do; before that, these read "🎫 Show sold out", "Show sold
  // out▾", "Show sold out Hide sold out" and "Show sold out (1)".
  for (const name of VARIANTS) {
    const preview = snapshot(variantFiles(name));
    assert.deepEqual(TOWER2.controls[name].items.at(-1), {role: 'button', name: 'Show sold out', visible: true, source: 'content'});
    assert.deepEqual(plain(requestedControlNameCheck(NAMES, preview, evidence(preview, TOWER2.controls[name])).missing), [], name);
  }
  // A link whose arrow is aria-hidden.
  const preview = snapshot({'index.html': TOWER2.pages['arrow-link']});
  const link = extractRequestedControlNames('Add a link named exactly "All events".');
  assert.deepEqual(plain(requestedControlNameCheck(link, preview, evidence(preview, TOWER2.controls['arrow-link'])).missing), []);
});

test('only a complete list bound to this snapshot proves a missing name', () => {
  const preview = snapshot(FILES), controls = TOWER2.controls.tower2;
  const check = (items, count = items.length, names = NAMES) =>
    plain(requestedControlNameCheck(names, preview, evidence(preview, {count, items}))?.missing);
  // More buttons and links than listed: an absent name is unknown.
  assert.deepEqual(check(controls.items, 60), []);
  // Another snapshot's evidence, or no requested names: no check at all.
  assert.equal(requestedControlNameCheck(NAMES, preview, {...evidence(preview, controls), sha256: 'f'.repeat(64)}), undefined);
  assert.equal(requestedControlNameCheck([], preview, evidence(preview, controls)), undefined);
  // A control hidden at load still has its name (a closed dialog's button).
  assert.deepEqual(check([{role: 'button', name: 'Show sold out', visible: false, source: 'content'}]), []);
  // Letter case matters for "named exactly", not for "named".
  const cased = [{role: 'button', name: 'Show Sold Out', visible: true, source: 'content'}];
  assert.deepEqual(check(cased), [{...WANT[0], candidate: cased[0]}]);
  assert.deepEqual(check(cased, 1, [{...WANT[0], match: 'caseless'}]), []);
  // A link with the name is not the requested button.
  const link = [{role: 'link', name: 'Show sold out', visible: true, source: 'content'}];
  assert.deepEqual(check(link), [{...WANT[0], candidate: link[0]}]);
  assert.match(requestedControlNameInstruction(preview, requestedControlNameCheck(NAMES, preview, evidence(preview, {count: 1, items: link}))),
    /a link, not a button, is named "Show sold out"\. Make that control a real button \(a <button> element\)/);
  // No button at all.
  assert.deepEqual(check([]), [WANT[0]]);
  assert.match(requestedControlNameInstruction(preview, requestedControlNameCheck(NAMES, preview, evidence(preview, {count: 0, items: []}))),
    /no button has that accessible name\. Give the button exactly that accessible name/);
});

test('repair, revision and delivery texts are bound to the snapshot', () => {
  const preview = snapshot(FILES);
  const check = requestedControlNameCheck(NAMES, preview, evidence(preview, TOWER2.controls.tower2));
  const sources = {siteId: preview.siteId, sha256: preview.sha256, scripts: ['script.js'], markupLabels: ['Show sold out']};
  assert.equal(requestedControlNameInstruction(preview, check, sources), REPAIR);
  // Without provenance of this snapshot the origin stays general.
  assert.match(requestedControlNameInstruction(preview, check, {...sources, sha256: 'f'.repeat(64)}),
    /by its aria-label attribute \(in the HTML or set by a script\), which replaces its text/);
  assert.equal(requestedControlNameRevisionInstruction(preview, check),
    REQUESTED_CONTROL_NAME_REVISION_INSTRUCTION.join('["Show sold out"]'));
  assert.equal(requestedTextDeliveryNote(preview, undefined, check), FAILURE_NOTE);
  const other = {...preview, sha256: 'f'.repeat(64)};
  for (const text of [requestedControlNameInstruction(other, check, sources), requestedControlNameRevisionInstruction(other, check),
    requestedTextDeliveryNote(other, undefined, check)]) assert.equal(text, undefined);
  // Missing text and a missing control name share one note.
  const textCheck = {siteId: preview.siteId, sha256: preview.sha256, missing: [{text: 'Dawn jazz'}]};
  assert.equal(requestedTextDeliveryNote(preview, textCheck, check),
    'The published page does not contain text the owner requested: "Dawn jazz". ' + FAILURE_NOTE);
});

// The round 100 receipt, as a capsule with load-time names returns it.
function inspection(call, controls, preview = RECEIPT) {
  const params = {...call.arguments, siteId: preview.siteId, sha256: preview.sha256};
  const request = normalizeWorkspacePreviewInspectionParams(params);
  const receipt = {...structuredClone(call.receipt), siteId: preview.siteId, sha256: preview.sha256,
    planSha256: inspectionPlanHash(request), ...(controls ? {controls} : {})};
  return {params, receipt};
}

test('the inspection tool explains an exact-name miss with the load-time names', async () => {
  const {params, receipt} = inspection(ROLE_CALL, TOWER2.controls.tower2);
  assert.equal(inspectionPlanHash(normalizeWorkspacePreviewInspectionParams(ROLE_CALL.arguments)), ROLE_CALL.receipt.planSha256,
    'the recorded plan hash');
  const result = await createWorkspacePreviewInspectTool({request: async () => receipt}).execute('role', params);
  const text = result.content[0].text;
  assert.equal(result.isError, true);
  assert.ok(text.startsWith('Preview inspection failed. Step 2 (click) matched no element, so nothing was measured and ' +
    'later steps did not run. At load, after the page scripts ran, the rendered button whose text is "Show sold out" has ' +
    'the accessible name "Show the sold out midnight concert card", set by its aria-label attribute, which replaces its ' +
    'text as the name. Role/name locators match the accessible name, not the text. If the owner required that exact name, ' +
    'the page does not meet it: correct the markup or script so the control\'s accessible name is exactly "Show sold out", ' +
    'republish, and inspect the new snapshot.'), text);
  // Round 100 read "a hidden element is not matched"; the button was rendered.
  assert.doesNotMatch(text, /a hidden element is not matched|Do not change the site only to satisfy a locator/);
  assert.equal(JSON.parse(text.slice(text.indexOf(' Evidence: ') + 11)).controls, undefined, 'the evidence copy omits the list');
  assert.deepEqual(result.details.controls, TOWER2.controls.tower2);
  // An older capsule (no names) keeps the recorded locator feedback.
  const older = inspection(ROLE_CALL);
  const recorded = (await createWorkspacePreviewInspectTool({request: async () => older.receipt}).execute('old', older.params)).content[0].text;
  assert.match(recorded, /For click, role\/name locators match only rendered elements, so a hidden element is not matched\./);
  // A page whose rendered button is named right at load (the aria-hidden icon
  // variant: Chromium's own name keeps the icon's space): the name is not
  // missing, and the step needs a CSS locator, never a site change.
  const named = inspection(ROLE_CALL, TOWER2.controls['aria-hidden-icon']);
  const present = (await createWorkspacePreviewInspectTool({request: async () => named.receipt}).execute('named', named.params)).content[0].text;
  assert.ok(present.startsWith('Preview inspection failed. Step 2 (click) matched no element, so nothing was measured and later ' +
    'steps did not run. At load, after the page scripts ran, a rendered button was named exactly "Show sold out", so that name ' +
    'is on the page; this inspector\'s role/name matching compares the browser\'s own name verbatim, which can keep extra ' +
    'spacing (for example beside an aria-hidden icon). Address that button with a CSS selector such as its id in this step, ' +
    'keep the other steps unchanged, and retry the inspection on the same published snapshot. Do not change the site only ' +
    'to satisfy a locator.'), present);
  assert.doesNotMatch(present, /hidden element is not matched|the page does not meet it/);
  // Letter case only.
  const cased = inspection(ROLE_CALL, {count: 1, items: [{role: 'button', name: 'Show Sold Out', visible: true, source: 'content'}]});
  assert.match((await createWorkspacePreviewInspectTool({request: async () => cased.receipt}).execute('case', cased.params)).content[0].text,
    /At load, a button is named "Show Sold Out"; accessible names match case-sensitively\./);
});

test('bound load-time names come from any valid receipt of this snapshot, never another', () => {
  const {params, receipt} = inspection(CSS_CALL, TOWER2.controls.tower2);
  assert.deepEqual(plain(boundInspectionControls(params, {details: receipt}, RECEIPT)),
    {siteId: RECEIPT.siteId, sha256: RECEIPT.sha256, controls: TOWER2.controls.tower2});
  const failed = inspection(ROLE_CALL, TOWER2.controls.tower2);
  assert.deepEqual(plain(boundInspectionControls(failed.params, {details: failed.receipt, isError: true}, RECEIPT)).controls,
    TOWER2.controls.tower2, 'names precede the failed step');
  const older = inspection(CSS_CALL);
  assert.deepEqual(plain(boundInspectionControls(older.params, {details: older.receipt}, RECEIPT)),
    {siteId: RECEIPT.siteId, sha256: RECEIPT.sha256});
  assert.equal(boundInspectionControls(params, {details: receipt}, {...RECEIPT, sha256: 'f'.repeat(64)}), undefined);
  for (const controls of [{count: 0, items: TOWER2.controls.correct.items}, {count: 1, items: [{...TOWER2.controls.correct.items[0], role: 'tab'}]}]) {
    assert.equal(boundInspectionControls(params, {details: {...receipt, controls}}, RECEIPT), undefined, 'an invalid list voids the receipt');
  }
});

const USER = 'ods-7ac9ec11486d47a5acde';
const context = {agentId: 'pixel', runId: 'chatcmpl_2d78192e-0cbb-4f31-8ffb-6e210749a4d5',
  sessionId: '1b442bc3-2184-437f-80da-0eae779f0307', sessionKey: `agent:pixel:openai-user:${USER}`};

function fixture(t, {inspectionAvailable = true} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-control-names-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: inspectionAvailable, abortRun: () => true});
  guard.observeRun(context, 'pixel', {prompt: TOWER2.prompt}, {workspaceRoot: root});
  // Hook order as in the gateway; the model reads the persisted text.
  const invoke = (tool, args, id, result) => {
    const ctx = {...context, toolName: tool, toolCallId: id};
    const event = {toolName: tool, runId: context.runId, toolCallId: id, params: args};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    guard.afterToolCall({...event, params: prepared?.params ?? args, result}, ctx);
    const persisted = guard.toolResultPersist({toolName: tool, toolCallId: id,
      message: {role: 'toolResult', toolName: tool, toolCallId: id, ...result}}, ctx);
    return (persisted?.message?.content ?? result.content ?? []).map(block => block.text).join('\n');
  };
  const write = (files, id) => {
    fs.mkdirSync(path.join(root, DIRECTORY), {recursive: true});
    for (const [name, text] of Object.entries(files)) {
      fs.writeFileSync(path.join(root, DIRECTORY, name), text);
      invoke('write', {path: `${DIRECTORY}/${name}`, content: text}, `${id}-${name}`,
        {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(text)} bytes to ${DIRECTORY}/${name}`}]});
    }
  };
  const publish = (files, id) => {
    const receipt = snapshot(files);
    const text = invoke('pixel_ods_workspace_preview', {relativeDirectory: DIRECTORY}, id,
      {content: [{type: 'text', text: `ODS independently published and read back 3 workspace static files. Verified browser URL: ${receipt.url}.`}],
        details: receipt});
    return {receipt, text};
  };
  // The model's own round 100 plans, with the load-time names the capsule
  // reports for those bytes, through the real inspection tool.
  const inspect = async (call, controls, preview, id) => {
    const {params, receipt} = inspection(call, controls, preview);
    const result = await createWorkspacePreviewInspectTool({request: async () => receipt}).execute(id, params);
    return invoke(PREVIEW_INSPECTION_TOOL, params, id, result);
  };
  const finalize = answer => guard.beforeAgentFinalize({lastAssistantMessage: answer}, context);
  const delivered = () => guard.deliveryVerificationForRun(context.runId);
  return {write, publish, inspect, finalize, delivered};
}

async function round100(t) {
  const f = fixture(t);
  f.write(FILES, 'write');
  const published = f.publish(FILES, 'publish');
  assert.equal(published.receipt.sha256, RECEIPT.sha256);
  assert.match(published.text, /the requested show\/hide interaction is not/, 'publication still asks for the interaction check');
  assert.doesNotMatch(published.text, /accessible name/, 'publication renders nothing, so it states no name verdict');
  return {...f, published};
}

test('tower2 round 100: both inspections of the replaced-name snapshot carry the precise repair step', async t => {
  const f = await round100(t);
  // Recorded call 1: the exact-name click matched nothing (a failed result).
  const role = await f.inspect(ROLE_CALL, TOWER2.controls.tower2, RECEIPT, 'inspect-role');
  assert.match(role, /At load, after the page scripts ran, the rendered button whose text is "Show sold out"/);
  assert.ok(role.includes(NEXT), role);
  // Recorded call 2: the CSS plan passed the show/hide transition.
  const css = await f.inspect(CSS_CALL, TOWER2.controls.tower2, RECEIPT, 'inspect-css');
  assert.match(css, /^Preview inspection passed\. These steps tested opposite visibility states/);
  assert.ok(css.includes(NEXT), css);
  assert.ok(!css.includes(WORKSPACE_PREVIEW_COMPLETE_REASON), 'never "give the concise final result" over a failing name');
  // The recorded answer claimed full verification: one bounded revision,
  // then the honest failure stands.
  assert.deepEqual(f.finalize(TOWER2.finalAnswer), {action: 'revise',
    reason: 'Pixel has not completed every owner-requested verified step.',
    retry: {instruction: REQUESTED_CONTROL_NAME_REVISION_INSTRUCTION.join('["Show sold out"]'),
      idempotencyKey: 'pixel-ods-workspace-preview-control-name', maxAttempts: 1}});
  assert.deepEqual(f.finalize(TOWER2.finalAnswer),
    {action: 'finalize', reason: 'Owner-requested control names are still not met after the bounded revision.'});
  const outcome = f.delivered();
  assert.equal(outcome.status, 'failed');
  assert.ok(outcome.text.startsWith(`${FAILURE_NOTE}\n\nBrowser inspection passed for the submitted show/hide checks only;`), outcome.text);
  assert.equal(outcome.preview.sha256, RECEIPT.sha256);
});

test('tower2 round 100: removing the script override, republishing and inspecting certifies the site', async t => {
  const f = await round100(t);
  assert.ok((await f.inspect(CSS_CALL, TOWER2.controls.tower2, RECEIPT, 'inspect-css')).includes(NEXT));
  f.write({'script.js': REPAIRED['script.js']}, 'repair');
  const republished = f.publish(REPAIRED, 'republish');
  assert.notEqual(republished.receipt.sha256, RECEIPT.sha256);
  assert.doesNotMatch(republished.text, /no button has that accessible name/, 'the old snapshot\'s verdict does not carry over');
  const css = await f.inspect(CSS_CALL, TOWER2.controls['tower2-repaired'], republished.receipt, 'reinspect-css');
  assert.doesNotMatch(css, /accessible name/);
  assert.ok(css.includes(`[ODS Pixel next step] ${WORKSPACE_PREVIEW_COMPLETE_REASON}`), css);
  assert.equal(f.finalize('Removed the aria-label override and republished.'), undefined);
  const outcome = f.delivered();
  assert.equal(outcome.status, 'passed', outcome.text);
  assert.doesNotMatch(outcome.text, /has no button named/);
  assert.equal(outcome.preview.sha256, republished.receipt.sha256);
});

// The CSS plan's passed receipt with the click addressed by the exact role
// and name instead: the model's own exact-name check, which passes on these
// pages in the capsule (test_preview_inspection.py). On the icon variant it
// matches nothing, as recorded in round 100's first call: Chromium's own name
// keeps the space after the aria-hidden icon.
const ROLE_PASS_CALL = {arguments: ROLE_CALL.arguments, receipt: {...structuredClone(CSS_CALL.receipt),
  steps: CSS_CALL.receipt.steps.map((step, index) => ({...step, locator: ROLE_CALL.arguments.steps[index].locator}))}};
const CONTROL_NAME_REPAIR = /no button has that accessible name|\[ODS Pixel next step\] The owner requested a button/;

test('round 100 with a correct button that carries hidden decorations: no name repair, and delivery passes', async t => {
  for (const name of VARIANTS) {
    await t.test(name, async st => {
      const f = fixture(st), files = variantFiles(name);
      f.write(files, 'write');
      const {receipt} = f.publish(files, 'publish');
      const role = await f.inspect(name === 'aria-hidden-icon' ? ROLE_CALL : ROLE_PASS_CALL, TOWER2.controls[name], receipt,
        'inspect-role');
      assert.doesNotMatch(role, CONTROL_NAME_REPAIR, role);
      assert.doesNotMatch(role, /whose text is "Show sold out" has the accessible name/, role);
      if (name !== 'aria-hidden-icon') assert.match(role, /^Preview inspection passed\. These steps tested opposite visibility states/);
      const css = await f.inspect(CSS_CALL, TOWER2.controls[name], receipt, 'inspect-css');
      assert.match(css, /^Preview inspection passed\. These steps tested opposite visibility states/);
      assert.doesNotMatch(css, CONTROL_NAME_REPAIR, css);
      // The first passed show/hide inspection says to give the final result.
      assert.ok((name === 'aria-hidden-icon' ? css : role).includes(`[ODS Pixel next step] ${WORKSPACE_PREVIEW_COMPLETE_REASON}`),
        `${role}
${css}`);
      assert.equal(f.finalize(TOWER2.finalAnswer), undefined);
      const outcome = f.delivered();
      assert.equal(outcome.status, 'passed', outcome.text);
      assert.equal(outcome.preview.sha256, receipt.sha256);
    });
  }
});

test('an inspection without load-time names (older capsule) changes no verdict and asks for no more checks', async t => {
  const f = await round100(t);
  const css = await f.inspect(CSS_CALL, undefined, RECEIPT, 'inspect-css');
  assert.ok(css.includes(`[ODS Pixel next step] ${WORKSPACE_PREVIEW_COMPLETE_REASON}`), css);
  assert.equal(f.finalize(TOWER2.finalAnswer), undefined);
  assert.equal(f.delivered().status, 'passed', 'unknown is not a failure');
});

test('without the inspector no control name is required', async t => {
  const f = fixture(t, {inspectionAvailable: false});
  f.write(FILES, 'write');
  const {text} = f.publish(FILES, 'publish');
  assert.doesNotMatch(text, /accessible name|pixel_ods_workspace_preview_inspect on this snapshot/);
  assert.equal(f.delivered().status, 'passed');
});

test('a control name without a show/hide request asks for an inspection of the snapshot', async t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-control-name-only-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  const prompt = `Create a static page in a new workspace directory ${DIRECTORY}. Add a button named exactly "Show sold out". ` +
    'Publish a verified Pixel workspace preview.';
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root});
  const html = TOWER2.pages.mismatch;
  const call = (tool, args, id, result) => {
    const ctx = {...context, toolName: tool, toolCallId: id};
    const event = {toolName: tool, runId: context.runId, toolCallId: id, params: args};
    guard.beforeToolCall(event, ctx);
    guard.afterToolCall({...event, result}, ctx);
    return (guard.toolResultPersist({toolName: tool, toolCallId: id, message: {role: 'toolResult', toolName: tool, toolCallId: id,
      ...result}}, ctx)?.message?.content ?? []).map(block => block.text).join('\n');
  };
  fs.mkdirSync(path.join(root, DIRECTORY), {recursive: true});
  fs.writeFileSync(path.join(root, DIRECTORY, 'index.html'), html);
  call('write', {path: `${DIRECTORY}/index.html`, content: html}, 'write',
    {content: [{type: 'text', text: `Successfully wrote ${html.length} bytes to ${DIRECTORY}/index.html`}]});
  const receipt = snapshot({'index.html': html});
  const published = call('pixel_ods_workspace_preview', {relativeDirectory: DIRECTORY}, 'publish',
    {content: [{type: 'text', text: 'ODS independently published and read back 1 workspace static files.'}], details: receipt});
  assert.match(published, /\[ODS Pixel next step\] The owner requested a button named exactly "Show sold out"\. Publication does not render the page/);
  assert.ok(published.includes('{"action":"assert-visible","locator":{"role":"button","name":"Show sold out","exact":true}}'), published);
  // The model's check by that exact name: no match, and the load-time names say why.
  const params = {siteId: receipt.siteId, sha256: receipt.sha256, viewport: {width: 375, height: 667},
    steps: [{action: 'assert-visible', locator: {role: 'button', name: 'Show sold out', exact: true}}]};
  const request = normalizeWorkspacePreviewInspectionParams(params);
  const details = {schemaVersion: 1, kind: 'ods-pixel-preview-inspection', status: 'failed', siteId: receipt.siteId,
    sha256: receipt.sha256, planSha256: inspectionPlanHash(request), viewport: params.viewport,
    steps: [{index: 0, ...params.steps[0], before: {count: 0}, stable: true, status: 'failed', errorCode: 'no_match'}],
    diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [],
    controls: TOWER2.controls.mismatch, scope: ROLE_CALL.receipt.scope};
  const result = await createWorkspacePreviewInspectTool({request: async () => details}).execute('inspect', params);
  const inspected = call(PREVIEW_INSPECTION_TOOL, params, 'inspect', result);
  assert.ok(inspected.includes('[ODS Pixel next step] The owner requested a button named exactly "Show sold out", but after ' +
    'the page scripts ran no button has that accessible name: the button whose text is "Show sold out" is named ' +
    '"Reveal the sold-out concert" by its aria-label attribute in the HTML, which replaces its text as the accessible name.'),
    inspected);
  assert.equal(guard.deliveryVerificationForRun(context.runId).status, 'failed');
});
