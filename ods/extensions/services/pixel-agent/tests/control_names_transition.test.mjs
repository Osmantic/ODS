// The fleet prompt asks for both checks at once: "Initially hide the entire
// Midnight sold-out concert card. Provide an accessible button named exactly
// "Show sold out" that reveals that card when clicked." Two inspection
// results can now steer the model on the same call: an untested requested
// show/hide change is INCOMPLETE with ready corrected steps, and the
// load-time accessible names show whether the requested control name exists.
// These replay the tower2 round 100 snapshot bytes (and the repaired and
// hidden-decoration variants) through the real guard and inspection tool,
// with the after_tool_call event OpenClaw 2026.6.33 builds: a direct call
// whose result is an error carries `error` (handleToolExecutionEnd), a Tool
// Search call's projected envelope does not.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import * as fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createToolLoopGuard, WORKSPACE_PREVIEW_COMPLETE_REASON} from '../plugin/tool-loop-guard.mjs';
import {REQUESTED_CONTROL_NAME_REVISION_INSTRUCTION} from '../plugin/requested-literals.mjs';
import {PREVIEW_INSPECTION_TOOL, boundInspectionControls, boundInspectionPageErrors, preservesVisibilityInspection,
  boundVisibilityInspection} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, TRANSITION_UNTESTED, createWorkspacePreviewInspectTool, inspectionPlanHash,
  validateIncompleteInspectionReceipt, normalizeWorkspacePreviewInspectionParams} from '../plugin/workspace-preview-inspect.mjs';

const TOWER2 = JSON.parse(fs.readFileSync(new URL('./control-names-tower2-round100.json', import.meta.url), 'utf8'));
const FIXTURE = new URL('../../../../tests/fixtures/preview-controls/tower2-r100/', import.meta.url);
const FILES = Object.fromEntries(['index.html', 'script.js', 'styles.css']
  .map(name => [name, fs.readFileSync(new URL(name, FIXTURE), 'utf8')]));
const REPAIRED = {...FILES, 'script.js': FILES['script.js'].replace(TOWER2.repair.remove, '')};
const VARIANTS = Object.keys(TOWER2.variants.markup);
const variantFiles = name => ({...REPAIRED,
  'index.html': REPAIRED['index.html'].replace(TOWER2.variants.button, () => TOWER2.variants.markup[name])});
const RECEIPT = TOWER2.publicationReceipt;
const DIRECTORY = RECEIPT.relativeDirectory;
const VIEWPORT = {width: 375, height: 667};
const CARD = {selector: '#midnight-concert'};
const BUTTON_CSS = {selector: '#show-sold-out-btn'};
const BUTTON = {role: 'button', name: 'Show sold out', exact: true};
const HEADING = {role: 'heading', name: 'Midnight Sold-Out Concert', exact: true};
const INCOMPLETE = 'Preview inspection INCOMPLETE - not verified.';
const NAME_REPAIR = /no button has that accessible name|\[ODS Pixel next step\] The owner requested a button/;
const NEXT = '[ODS Pixel next step] The owner requested a button named exactly "Show sold out", but after the page ' +
  'scripts ran no button has that accessible name: the button whose text is "Show sold out" is named "Show the sold out ' +
  'midnight concert card" by an aria-label that a published script sets when the page loads (["script.js"]), which ' +
  'replaces its text as the accessible name. Remove that override or make it exactly "Show sold out", republish, then ' +
  'inspect the new snapshot.';
const FAILURE_NOTE = 'The published page has no button named exactly "Show sold out" after its scripts run ' +
  '(the button with that text is named "Show the sold out midnight concert card").';

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

// Each page as the capsule measures it: the card starts hidden and the button
// click reveals it. Role/name steps match Chromium's own name verbatim
// (test_preview_inspection.py): the replaced name on the round 100 bytes, and
// " Show sold out" beside the aria-hidden icon, where getByRole (and the
// load-time `controls`, which follow it) still reads "Show sold out".
const PAGES = {
  tower2: {files: FILES, chromiumName: 'Show the sold out midnight concert card'},
  'tower2-repaired': {files: REPAIRED, chromiumName: 'Show sold out'},
  ...Object.fromEntries(VARIANTS.map(name => [name, {files: variantFiles(name),
    chromiumName: name === 'aria-hidden-icon' ? ' Show sold out' : 'Show sold out'}])),
};
const shown = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible',
  opacity: '1', hidden: false, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
function capsule(name) {
  const {chromiumName} = PAGES[name], controls = TOWER2.controls[name];
  assert.ok(controls, name);
  return request => {
    let clicked = false;
    // A role/name locator matches a hidden element only for assert-hidden.
    const measure = (locator, action) => locator.selector === CARD.selector ? shown(clicked)
      : locator.selector === BUTTON_CSS.selector ? shown(true)
        : locator.role === 'button' && locator.name === chromiumName ? shown(true)
          : locator.role === 'heading' && locator.name === HEADING.name && (clicked || action === 'assert-hidden')
            ? shown(clicked) : {count: 0};
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
      diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], controls, scope: INSPECTION_SCOPE};
  };
}

const envelope = (name, result) => ({content: [{type: 'text', text: JSON.stringify({tool: {id: `openclaw:pixel-ods:${name}`, name}, result})}],
  details: {tool: {id: `openclaw:pixel-ods:${name}`, source: 'openclaw', sourceName: 'pixel-ods', name}, result}});
const joined = (persisted, result) => (persisted?.message?.content ?? result.content ?? []).map(block => block.text).join('\n');
const nextArgs = text => {
  const match = /with exactly these args: (\{.*?\}) The target /.exec(text);
  assert.ok(match, text);
  return JSON.parse(match[1]);
};

const context = {agentId: 'pixel', runId: 'chatcmpl_2d78192e-0cbb-4f31-8ffb-6e210749a4d5',
  sessionId: '1b442bc3-2184-437f-80da-0eae779f0307', sessionKey: 'agent:pixel:openai-user:ods-7ac9ec11486d47a5acde'};

function fixture(t, prompt = TOWER2.prompt) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'pixel-names-transition-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true, abortRun: () => true});
  guard.observeRun(context, 'pixel', {prompt}, {workspaceRoot: root});
  let page;
  const tool = createWorkspacePreviewInspectTool({request: async request => capsule(page)(request),
    transitionRequirement: (toolCallId, params) => guard.previewInspectionTransition(toolCallId, params)});
  const invoke = (name, args, id, result) => {
    const ctx = {...context, toolName: name, toolCallId: id};
    const event = {toolName: name, runId: context.runId, toolCallId: id, params: args};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    guard.afterToolCall({...event, params: prepared?.params ?? args, result}, ctx);
    return joined(guard.toolResultPersist({toolName: name, toolCallId: id,
      message: {role: 'toolResult', toolName: name, toolCallId: id, ...result}}, ctx), result);
  };
  const publish = (name, id) => {
    page = name;
    const files = PAGES[name].files;
    fs.mkdirSync(path.join(root, DIRECTORY), {recursive: true});
    for (const [file, text] of Object.entries(files)) {
      fs.writeFileSync(path.join(root, DIRECTORY, file), text);
      invoke('write', {path: `${DIRECTORY}/${file}`, content: text}, `${id}-${file}`,
        {content: [{type: 'text', text: `Successfully wrote ${Buffer.byteLength(text)} bytes to ${DIRECTORY}/${file}`}]});
    }
    const receipt = snapshot(files);
    invoke('pixel_ods_workspace_preview', {relativeDirectory: DIRECTORY}, id,
      {content: [{type: 'text', text: `ODS independently published and read back 3 workspace static files. Verified browser URL: ${receipt.url}.`}],
        details: receipt});
    return receipt;
  };
  // A direct call's error result reaches after_tool_call with `error` set, as
  // OpenClaw 2026.6.33 does; a Tool Search envelope is projected without it.
  const inspect = async (receipt, steps, id, transport = 'direct') => {
    const args = {siteId: receipt.siteId, sha256: receipt.sha256, viewport: VIEWPORT, steps};
    if (transport === 'direct') {
      const ctx = {...context, toolName: PREVIEW_INSPECTION_TOOL, toolCallId: id};
      const event = {toolName: PREVIEW_INSPECTION_TOOL, runId: context.runId, toolCallId: id, params: args};
      assert.notEqual(guard.beforeToolCall(event, ctx)?.block, true);
      const result = await tool.execute(id, args);
      guard.afterToolCall({...event, result, ...(result.isError ? {error: result.content[0].text} : {})}, ctx);
      const text = joined(guard.toolResultPersist({toolName: PREVIEW_INSPECTION_TOOL, toolCallId: id,
        message: {role: 'toolResult', toolName: PREVIEW_INSPECTION_TOOL, toolCallId: id, ...result}}, ctx), result);
      return {result, own: result.content[0].text, text};
    }
    const outer = {id: PREVIEW_INSPECTION_TOOL, args};
    const ctx = {...context, toolName: 'tool_call', toolCallId: id};
    const event = {toolName: 'tool_call', runId: context.runId, toolCallId: id, params: outer};
    const prepared = guard.beforeToolCall(event, ctx);
    assert.notEqual(prepared?.block, true, prepared?.blockReason);
    const result = await tool.execute(`tool_search_code:${id}:${PREVIEW_INSPECTION_TOOL}:1`, args);
    const wrapped = envelope(PREVIEW_INSPECTION_TOOL, result);
    guard.afterToolCall({...event, params: prepared?.params ?? outer, result: wrapped}, ctx);
    const text = joined(guard.toolResultPersist({toolName: 'tool_call', toolCallId: id,
      message: {role: 'toolResult', toolName: 'tool_call', toolCallId: id, ...wrapped}}, ctx), wrapped);
    return {result, own: result.content[0].text, text};
  };
  const finalize = answer => guard.beforeAgentFinalize({lastAssistantMessage: answer}, context);
  const delivered = () => guard.deliveryVerificationForRun(context.runId);
  return {publish, inspect, finalize, delivered};
}

// Laptop round 100's plan shape: the button, a click, the card afterwards; the
// card is never asserted before the click.
const UNTESTED = [{action: 'assert-visible', locator: BUTTON_CSS}, {action: 'click', locator: BUTTON_CSS},
  {action: 'assert-visible', locator: CARD}];
const TRANSITION = [{action: 'assert-hidden', locator: CARD}, {action: 'click', locator: BUTTON_CSS},
  {action: 'assert-visible', locator: CARD}];

// `own` is the inspection tool's text; `text` is everything the model reads
// (for tool_call, the envelope plus the guard's appended steps).
function assertIncomplete({result, own}, receipt) {
  assert.ok(own.startsWith(INCOMPLETE), own);
  assert.equal(result.isError, true);
  assert.deepEqual({status: result.details.status, errorCode: result.details.errorCode},
    {status: 'incomplete', errorCode: TRANSITION_UNTESTED});
  const args = nextArgs(own);
  assert.deepEqual(args, {siteId: receipt.siteId, sha256: receipt.sha256, viewport: VIEWPORT, steps: TRANSITION});
  return args;
}

for (const transport of ['direct', 'tool_call']) {
  test(`exact name and show/hide on correct pages: INCOMPLETE without a name repair, then certified (${transport})`, async t => {
    for (const name of ['tower2-repaired', ...VARIANTS]) {
      await t.test(name, async st => {
        const f = fixture(st);
        const receipt = f.publish(name, 'publish');
        const incomplete = await f.inspect(receipt, UNTESTED, 'untested', transport);
        const args = assertIncomplete(incomplete, receipt);
        assert.doesNotMatch(incomplete.text, NAME_REPAIR, 'a correct name is never reported missing');
        assert.equal(f.delivered().status, 'failed', 'the show/hide change is still untested');
        assert.doesNotMatch(f.delivered().text, /has no button named/);
        const corrected = await f.inspect(receipt, args.steps, 'corrected', transport);
        assert.equal(corrected.result.details.status, 'passed');
        assert.doesNotMatch(corrected.text, NAME_REPAIR);
        assert.ok(corrected.text.includes(`[ODS Pixel next step] ${WORKSPACE_PREVIEW_COMPLETE_REASON}`), corrected.text);
        assert.equal(f.finalize(TOWER2.finalAnswer), undefined);
        const outcome = f.delivered();
        assert.equal(outcome.status, 'passed', outcome.text);
        assert.equal(outcome.preview.sha256, receipt.sha256);
      });
    }
  });

  test(`exact name and show/hide on the replaced-name snapshot: the INCOMPLETE result also carries the name repair (${transport})`, async t => {
    const f = fixture(t);
    const receipt = f.publish('tower2', 'publish');
    assert.equal(receipt.sha256, RECEIPT.sha256, 'the recorded round 100 snapshot');
    const incomplete = await f.inspect(receipt, UNTESTED, 'untested', transport);
    assertIncomplete(incomplete, receipt);
    assert.ok(incomplete.text.includes(NEXT), incomplete.text);
    // The recorded answer: one bounded revision, then the honest failure.
    assert.deepEqual(f.finalize(TOWER2.finalAnswer), {action: 'revise',
      reason: 'Pixel has not completed every owner-requested verified step.',
      retry: {instruction: REQUESTED_CONTROL_NAME_REVISION_INSTRUCTION.join('["Show sold out"]'),
        idempotencyKey: 'pixel-ods-workspace-preview-control-name', maxAttempts: 1}});
    assert.ok(f.delivered().text.startsWith(FAILURE_NOTE), f.delivered().text);
    assert.equal(f.delivered().status, 'failed');
    // The repaired snapshot needs its own show/hide check; the same untested
    // plan is INCOMPLETE again, now without a name repair.
    const repaired = f.publish('tower2-repaired', 'republish');
    const again = await f.inspect(repaired, UNTESTED, 'untested-repaired', transport);
    const args = assertIncomplete(again, repaired);
    assert.doesNotMatch(again.text, NAME_REPAIR, again.text);
    await f.inspect(repaired, args.steps, 'corrected', transport);
    const outcome = f.delivered();
    assert.equal(outcome.status, 'passed', outcome.text);
    assert.equal(outcome.preview.sha256, repaired.sha256);
  });
}

test('a failed direct inspection with the harness error set still records the load-time names', async t => {
  // Tower2 round 100's first call: the exact-name click matched nothing. The
  // direct transport's after_tool_call event carries `error`.
  const f = fixture(t);
  const receipt = f.publish('tower2', 'publish');
  const failed = await f.inspect(receipt, [{action: 'assert-hidden', locator: CARD}, {action: 'click', locator: BUTTON},
    {action: 'assert-visible', locator: CARD}], 'role');
  assert.equal(failed.result.details.status, 'failed');
  assert.ok(failed.text.includes(NEXT), failed.text);
  assert.equal(f.delivered().status, 'failed');
  assert.ok(f.delivered().text.startsWith(FAILURE_NOTE), f.delivered().text);
});

test('the aria-hidden icon page: an exact-name click the inspector cannot match is no missing name', async t => {
  // A plan without a click gets the owner's exact name as the corrected click.
  // Chromium's own name keeps the space after the hidden icon, so that step
  // matches nothing, while the load-time names (getByRole's) show the button
  // is named exactly right: no name repair, the step is pointed at a CSS
  // locator (the recorded feedback blamed a hidden element and asked to copy
  // the same name again), and that plan certifies the page.
  for (const transport of ['direct', 'tool_call']) {
    await t.test(transport, async st => {
      const f = fixture(st);
      const receipt = f.publish('aria-hidden-icon', 'publish');
      const incomplete = await f.inspect(receipt, [{action: 'assert-hidden', locator: CARD}], 'no-click', transport);
      assert.ok(incomplete.own.startsWith(INCOMPLETE), incomplete.own);
      const args = nextArgs(incomplete.own);
      // Without a click the corrected control is the owner's exact name, and
      // the target the published heading of the requested card.
      assert.deepEqual(args.steps, [{action: 'assert-hidden', locator: HEADING}, {action: 'click', locator: BUTTON},
        {action: 'assert-visible', locator: HEADING}]);
      const unmatched = await f.inspect(receipt, args.steps, 'owner-name', transport);
      assert.equal(unmatched.result.details.status, 'failed');
      assert.equal(unmatched.result.details.steps[1].errorCode, 'no_match');
      assert.doesNotMatch(unmatched.text, NAME_REPAIR, unmatched.text);
      assert.doesNotMatch(unmatched.text, /the page does not meet it|a hidden element is not matched/, unmatched.text);
      assert.ok(unmatched.own.includes('At load, after the page scripts ran, a rendered button was named exactly ' +
        '"Show sold out", so that name is on the page;') &&
        unmatched.own.includes('Address that button with a CSS selector such as its id in this step, keep the other steps unchanged'),
      unmatched.own);
      assert.doesNotMatch(f.delivered().text, /has no button named/);
      const css = await f.inspect(receipt, args.steps.map(step => step.action === 'click' ? {...step, locator: BUTTON_CSS} : step),
        'css', transport);
      assert.equal(css.result.details.status, 'passed');
      assert.equal(f.delivered().status, 'passed', f.delivered().text);
    });
  }
});

test('after a click the page may have changed, so a later unmatched name keeps the ordinary feedback', async () => {
  const receipt = snapshot(variantFiles('aria-hidden-icon'));
  const args = {siteId: receipt.siteId, sha256: receipt.sha256, viewport: VIEWPORT,
    steps: [{action: 'click', locator: BUTTON_CSS}, {action: 'click', locator: BUTTON}]};
  const inner = capsule('aria-hidden-icon')(normalizeWorkspacePreviewInspectionParams(args));
  assert.equal(inner.steps[1].errorCode, 'no_match');
  const text = (await createWorkspacePreviewInspectTool({request: async () => inner}).execute('late', args)).content[0].text;
  assert.match(text, /^Preview inspection failed\. Step 2 \(click\) matched no element, so nothing was measured and later steps did not run\. For click, role\/name locators match only rendered elements/);
  assert.doesNotMatch(text, /so that name is on the page/);
});

test('an incomplete result yields its receipt\'s load-time names and never interaction evidence', async () => {
  const receipt = snapshot(REPAIRED);
  const args = {siteId: receipt.siteId, sha256: receipt.sha256, viewport: VIEWPORT, steps: UNTESTED};
  const request = normalizeWorkspacePreviewInspectionParams(args);
  const inner = capsule('tower2-repaired')(request);
  const tool = createWorkspacePreviewInspectTool({request: async () => inner,
    transitionRequirement: () => ({target: 'Midnight sold-out concert', initiallyHidden: true})});
  const result = await tool.execute('incomplete', args);
  assert.equal(result.details.status, 'incomplete');
  assert.deepEqual(validateIncompleteInspectionReceipt(result.details, request), inner);
  assert.deepEqual(JSON.parse(JSON.stringify(boundInspectionControls(args, result, receipt))),
    {siteId: receipt.siteId, sha256: receipt.sha256, controls: TOWER2.controls['tower2-repaired']});
  // A proof of this snapshot is not preserved by an incomplete result either.
  const preserves = (params, value, preview) =>
    preservesVisibilityInspection({siteId: preview.siteId, sha256: preview.sha256}, params, value, preview) || undefined;
  for (const bound of [boundVisibilityInspection, preserves, boundInspectionPageErrors]) {
    assert.equal(bound(args, result, receipt), undefined, bound.name);
    assert.equal(bound(args, {...result, isError: undefined}, receipt), undefined, `${bound.name} without isError`);
  }
  // Another snapshot, a wrapper that disagrees with its receipt, an extra
  // field, a failed or missing inner receipt: no names.
  const invalid = [
    {...result.details, sha256: 'f'.repeat(64)},
    {...result.details, planSha256: 'f'.repeat(64)},
    {...result.details, errorCode: 'unavailable'},
    {...result.details, extra: true},
    {...result.details, receipt: {...inner, status: 'failed'}},
    {...result.details, receipt: undefined},
  ];
  for (const details of invalid) assert.equal(boundInspectionControls(args, {...result, details}, receipt), undefined);
  assert.equal(boundInspectionControls(args, result, {...receipt, sha256: 'e'.repeat(64)}), undefined);
});
