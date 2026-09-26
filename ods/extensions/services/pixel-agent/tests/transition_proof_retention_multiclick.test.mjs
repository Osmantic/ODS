// Which later passing plans keep a show/hide proof (#6754 review). Real guard,
// real registered inspection tool (with the guard's #6740 transition
// requirement), and a simulated page: each receipt is measured from a page
// state machine, so a plan passes only if the page really behaves as asserted.
// Every page here breaks the requested change at the later plan's width, and
// the later plan passes while showing it; the proof must not survive that.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createToolLoopGuard} from '../plugin/tool-loop-guard.mjs';
import {PREVIEW_INSPECTION_TOOL} from '../plugin/preview-interaction-assurance.mjs';
import {INSPECTION_KIND, INSPECTION_SCOPE, inspectionPlanHash, createWorkspacePreviewInspectTool} from '../plugin/workspace-preview-inspect.mjs';

const context = {agentId: 'pixel', runId: 'run', sessionId: 'session', sessionKey: 'opaque-key'};
const key = locator => JSON.stringify(Object.keys(locator).sort().map(name => [name, locator[name]]));

function call(guard, name, params, id, result, rc = context) {
  const ctx = {...rc, toolName: name, toolCallId: id};
  const event = {toolName: name, runId: rc.runId, toolCallId: id, params};
  const prepared = guard.beforeToolCall(event, ctx);
  assert.notEqual(prepared?.block, true, prepared?.blockReason);
  event.params = prepared?.params ?? params;
  if (result) guard.afterToolCall({...event, result}, ctx);
  return {event, ctx};
}
function publish(guard, content, id = 'publish', dir) {
  const write = call(guard, 'write', {path: (dir ?? 'site') + '/index.html', content}, 'write-' + id,
    {content: [{type: 'text', text: 'Successfully wrote file.'}]}).event.params;
  guard.toolResultPersist({toolName: 'write', toolCallId: 'write-' + id, message: {role: 'toolResult', toolName: 'write',
    toolCallId: 'write-' + id, content: [{type: 'text', text: 'Successfully wrote file.'}]}}, {...context, toolName: 'write', toolCallId: 'write-' + id});
  const relativeDirectory = write.path.replace(/\/index.html$/, '');
  const name = Buffer.from('index.html'), data = Buffer.from(content), a = Buffer.alloc(4), b = Buffer.alloc(8);
  a.writeUInt32BE(name.length); b.writeBigUInt64BE(BigInt(data.length));
  const sha256 = createHash('sha256').update(a).update(name).update(b).update(data).digest('hex');
  const siteId = 'site-' + sha256.slice(0, 24);
  const preview = {schemaVersion: 1, kind: 'ods-pixel-workspace-preview', status: 'succeeded', relativeDirectory,
    siteId, sha256, entryFile: 'index.html', entrySha256: createHash('sha256').update(data).digest('hex'), files: 1, bytes: data.length,
    port: 9437, url: `http://${siteId}.localhost:9437/${siteId}/`, httpStatus: 200, readbackVerified: true, executable: false, overwritten: false};
  call(guard, 'pixel_ods_workspace_preview', {relativeDirectory}, id, {details: preview});
  return preview;
}
function setup(owner, content = '<!doctype html><h1>Site</h1><button>Show details</button><p id="details" hidden>Details</p>') {
  const guard = createToolLoopGuard({workspacePreviewInspectionAvailable: true});
  guard.observeRun(context, 'pixel', {prompt: owner});
  return {guard, preview: publish(guard, content)};
}
const shown = visible => ({count: 1, visible, display: visible ? 'block' : 'none', visibility: 'visible',
  opacity: '1', hidden: false, hiddenUntilFound: false, rectCount: visible ? 1 : 0});
// page = {initial(width) -> Map(key -> 'visible'|'hidden'), click(state, locatorKey, width) -> Map}
function capsule(page, extra = {}) {
  return request => {
    let state = page.initial(request.viewport.width);
    const measure = (locator, action) => {
      const s = state.get(key(locator));
      return s === undefined || (locator.role !== undefined && s === 'hidden' && action !== 'assert-hidden') ? {count: 0} : shown(s === 'visible');
    };
    const steps = [];
    for (const [index, step] of request.steps.entries()) {
      const item = {index, ...step, before: measure(step.locator, step.action), stable: true, status: 'failed'};
      if (item.before.count !== 1) item.errorCode = 'no_match';
      else if (step.action === 'click') {
        if (item.before.visible) { state = page.click(state, key(step.locator), request.viewport.width);
          Object.assign(item, {after: measure(step.locator, 'click'), status: 'passed'}); }
        else item.errorCode = 'click_failed';
      } else if (item.before.visible === (step.action === 'assert-visible')) item.status = 'passed';
      else item.errorCode = 'visibility_mismatch';
      steps.push(item);
      if (item.status === 'failed') break;
    }
    return {schemaVersion: 1, kind: INSPECTION_KIND,
      status: steps.length === request.steps.length && steps.every(step => step.status === 'passed') ? 'passed' : 'failed',
      siteId: request.siteId, sha256: request.sha256, planSha256: inspectionPlanHash(request), viewport: request.viewport, steps,
      diagnostics: {renderedHiddenAttributeCount: 0, hiddenUntilFoundCount: 0}, blockedRequests: [], scope: INSPECTION_SCOPE, ...extra};
  };
}
const envelope = (name, result) => ({content: [{type: 'text', text: JSON.stringify({tool: {id: `openclaw:pixel-ods:${name}`, name}, result})}],
  details: {tool: {id: `openclaw:pixel-ods:${name}`, source: 'openclaw', sourceName: 'pixel-ods', name}, result}});
// transport: 'direct' | 'tool_call' | 'nested' (tool_call whose Tool Search child also emits hooks)
async function inspect(guard, preview, page, steps, id, {width = 800, transport = 'direct', request, mutate = r => r} = {}) {
  const args = {siteId: preview.siteId, sha256: preview.sha256, viewport: {width, height: 720}, steps};
  const tool = createWorkspacePreviewInspectTool({request: request ?? (async r => capsule(page)(r)),
    transitionRequirement: (toolCallId, p) => guard.previewInspectionTransition(toolCallId, p)});
  if (transport === 'direct') {
    const {event, ctx} = call(guard, PREVIEW_INSPECTION_TOOL, args, id);
    const result = mutate(await tool.execute(id, args));
    guard.afterToolCall({...event, result}, ctx);
    return result;
  }
  const outer = {id: PREVIEW_INSPECTION_TOOL, args};
  const {event, ctx} = call(guard, 'tool_call', outer, id);
  const child = `tool_search_code:${id}:${PREVIEW_INSPECTION_TOOL}:1`;
  const childCtx = {...context, toolName: PREVIEW_INSPECTION_TOOL, toolCallId: child};
  const childEvent = {toolName: PREVIEW_INSPECTION_TOOL, runId: context.runId, toolCallId: child, params: args};
  if (transport === 'nested') assert.notEqual(guard.beforeToolCall(childEvent, childCtx)?.block, true);
  const inner = mutate(await tool.execute(child, args));
  if (transport === 'nested') guard.afterToolCall({...childEvent, result: inner}, childCtx);
  guard.afterToolCall({...event, result: envelope(PREVIEW_INSPECTION_TOOL, inner)}, ctx);
  return inner;
}
const status = guard => guard.verificationForRun('run').status;
const text = guard => String(guard.verificationForRun('run').text ?? '').slice(0, 70).replace(/\n/g, ' ');
const step = (action, locator) => ({action, locator});
const sel = selector => ({selector});
const SHOW = {role: 'button', name: 'Show details', exact: true}, D = sel('#details');
const set = (state, pairs) => { const next = new Map(state); for (const [l, v] of pairs) next.set(key(l), v); return next; };

// Runs the proof, then the later plan, which must itself pass on the page, and
// checks the run's final verification status.
async function scenario(label, {owner, page, proof, proofWidth = 800, later, laterWidth = 375, transport, content, expect}) {
  const {guard, preview} = setup(owner, content);
  const p = await inspect(guard, preview, page, proof, 'proof', {width: proofWidth, transport});
  assert.equal(p.details.status, 'passed', `${label}: the proof passes on this page: ${p.content[0].text.slice(0, 200)}`);
  assert.equal(status(guard), 'passed', `${label}: the proof is recorded`);
  const l = await inspect(guard, preview, page, later, 'later', {width: laterWidth, transport});
  assert.equal(l.details.status, 'passed', `${label}: the later plan passes on this page: ${l.content[0].text.slice(0, 200)}`);
  assert.equal(status(guard), expect, `${label} [${transport}]: ${text(guard)}`);
}

const TRANSPORTS = ['direct', 'tool_call', 'nested'];

// A. Preparatory transition recorded instead of the requested one.
// The page: a cookie banner covers the page until "Accept"; "Show details"
// reveals #details at >= 768px only (the narrow handler is broken).
const COOKIE = sel('#cookie'), ACCEPT = sel('#accept');
const cookiePage = {
  initial: () => new Map([[key(sel('h1')), 'visible'], [key(COOKIE), 'visible'], [key(ACCEPT), 'visible'], [key(SHOW), 'visible'], [key(D), 'hidden']]),
  click: (s, k, w) => k === key(ACCEPT) ? set(s, [[COOKIE, 'hidden'], [ACCEPT, 'hidden']])
    : k === key(SHOW) && w >= 768 ? set(s, [[D, 'visible']]) : s,
};
const OWNER_REVEAL = 'Create and publish a website in a new workspace directory site. Add a "Show details" button that reveals the hidden details. Include a cookie banner with an Accept button.';
for (const transport of TRANSPORTS) {
  test(`A1 cookie-banner proof at 800, then exact-locator check at 375 shows Show details leaves #details hidden (${transport})`, async () => {
    await scenario('A1-cookie-proof/exact-later', {expect: 'failed', owner: OWNER_REVEAL, page: cookiePage, transport,
      proof: [step('assert-visible', COOKIE), step('assert-hidden', D), step('click', ACCEPT), step('assert-hidden', COOKIE), step('click', SHOW), step('assert-visible', D)],
      later: [step('assert-hidden', D), step('click', SHOW), step('assert-hidden', D)]});
  });
  test(`A2 cookie-banner proof, then later dismisses the banner and shows #details stays hidden (${transport})`, async () => {
    await scenario('A2-cookie-proof/dismiss-then-show', {expect: 'failed', owner: OWNER_REVEAL, page: cookiePage, transport,
      proof: [step('assert-visible', COOKIE), step('assert-hidden', D), step('click', ACCEPT), step('assert-hidden', COOKIE), step('click', SHOW), step('assert-visible', D)],
      later: [step('click', ACCEPT), step('click', SHOW), step('assert-hidden', D)]});
  });
  test(`A3 control: same page, proof without the banner assertion (PR's preparatory-click shape) (${transport})`, async () => {
    await scenario('A3-control-no-banner-assert', {expect: 'failed', owner: OWNER_REVEAL, page: cookiePage, transport,
      proof: [step('click', ACCEPT), step('assert-hidden', D), step('click', SHOW), step('assert-visible', D)],
      later: [step('assert-hidden', D), step('click', SHOW), step('assert-hidden', D)]});
  });
}

// B. Toggle. "Details" shows #details and a second click hides it; below
// 768px the second click never hides it.
const TOGGLE = {role: 'button', name: 'Details', exact: true};
const togglePage = {
  initial: () => new Map([[key(sel('h1')), 'visible'], [key(TOGGLE), 'visible'], [key(D), 'hidden']]),
  click: (s, k, w) => k !== key(TOGGLE) ? s : s.get(key(D)) === 'hidden' ? set(s, [[D, 'visible']]) : w >= 768 ? set(s, [[D, 'hidden']]) : s,
};
const OWNER_TOGGLE = 'Create and publish a website in a new workspace directory site. Add a "Details" button that toggles the hidden details panel: click to show it, click again to hide it.';
for (const transport of TRANSPORTS) {
  test(`B1 toggle proof (show then hide) at 800, then later shows a second click leaves #details visible at 375 (${transport})`, async () => {
    await scenario('B1-toggle-proof/second-click-no-hide', {expect: 'failed', owner: OWNER_TOGGLE, page: togglePage, transport,
      proof: [step('assert-hidden', D), step('click', TOGGLE), step('assert-visible', D), step('click', TOGGLE), step('assert-hidden', D)],
      later: [step('click', TOGGLE), step('click', TOGGLE), step('assert-visible', D)]});
  });
  test(`B2 one-click proof at 800, then later shows a second click leaves #details visible at 375 (${transport})`, async () => {
    await scenario('B2-reveal-proof/second-click-no-hide', {expect: 'failed', owner: OWNER_TOGGLE, page: togglePage, transport,
      proof: [step('assert-hidden', D), step('click', TOGGLE), step('assert-visible', D)],
      later: [step('assert-visible', sel('h1')), step('click', TOGGLE), step('click', TOGGLE), step('assert-visible', D)]});
  });
  test(`B3 control: a correct toggle-back check at 800 (itself a transition) (${transport})`, async () => {
    await scenario('B3-control-toggle-back-ok', {expect: 'passed', owner: OWNER_TOGGLE, page: togglePage, transport, laterWidth: 800,
      proof: [step('assert-hidden', D), step('click', TOGGLE), step('assert-visible', D)],
      later: [step('click', TOGGLE), step('assert-visible', D), step('click', TOGGLE), step('assert-hidden', D)]});
  });
}

// C. Hide-then-show mirror: a promo that "Close promo" hides and a second
// click shows again; at 1280 the re-show is broken.
const PROMO = sel('#promo'), CLOSE = {role: 'button', name: 'Close promo', exact: true};
const promoPage = {
  initial: () => new Map([[key(sel('h1')), 'visible'], [key(CLOSE), 'visible'], [key(PROMO), 'visible']]),
  click: (s, k, w) => k !== key(CLOSE) ? s : s.get(key(PROMO)) === 'visible' ? set(s, [[PROMO, 'hidden']]) : w < 1024 ? set(s, [[PROMO, 'visible']]) : s,
};
const OWNER_PROMO = 'Create and publish a website in a new workspace directory site. Add a "Close promo" button that hides the promo banner when clicked and shows it again on the next click.';
for (const transport of TRANSPORTS) {
  test(`C1 hide-then-show proof at 800, later at 1280 shows the second click leaves the promo hidden (${transport})`, async () => {
    await scenario('C1-hide-show-proof/second-click-no-show', {expect: 'failed', owner: OWNER_PROMO, page: promoPage, transport, laterWidth: 1280,
      proof: [step('assert-visible', PROMO), step('click', CLOSE), step('assert-hidden', PROMO), step('click', CLOSE), step('assert-visible', PROMO)],
      later: [step('click', CLOSE), step('click', CLOSE), step('assert-hidden', PROMO)]});
  });
}

// D. Round-108-like page: the button hides itself; the card is revealed only
// below 768px. The proof's only transition is the button hiding itself.
const BTN = sel('#soldOutBtn'), CARD = sel('#midnightCard');
const selfHidePage = {
  initial: () => new Map([[key(sel('h1')), 'visible'], [key(BTN), 'visible'], [key(CARD), 'hidden']]),
  click: (s, k, w) => k !== key(BTN) ? s : set(s, [[BTN, 'hidden'], ...(w < 768 ? [[CARD, 'visible']] : [])]),
};
const OWNER_CARD = 'Create and publish a website in a new workspace directory site. The Midnight sold-out concert card starts hidden, and a button named exactly "Show sold out" reveals it.';
const CARD_HTML = '<!doctype html><h1>Events</h1><button id="soldOutBtn">Show sold out</button><article id="midnightCard" class="hidden"><h3>Midnight sold-out concert</h3></article>';
for (const transport of TRANSPORTS) {
  test(`D1 proof of the button hiding itself at 375, later at 1280 shows the card stays hidden (${transport})`, async () => {
    await scenario('D1-self-hide-proof/card-hidden-later', {expect: 'failed', owner: OWNER_CARD, content: CARD_HTML, page: selfHidePage, transport, proofWidth: 375, laterWidth: 1280,
      proof: [step('assert-visible', BTN), step('click', BTN), step('assert-hidden', BTN)],
      later: [step('assert-hidden', CARD), step('click', BTN), step('assert-hidden', CARD)]});
  });
  test(`D2 control: proof of the card at 375, later at 1280 shows the card stays hidden (${transport})`, async () => {
    await scenario('D2-control-card-proof', {expect: 'failed', owner: OWNER_CARD, content: CARD_HTML, page: selfHidePage, transport, proofWidth: 375, laterWidth: 1280,
      proof: [step('assert-hidden', CARD), step('click', BTN), step('assert-visible', CARD)],
      later: [step('assert-hidden', CARD), step('click', BTN), step('assert-hidden', CARD)]});
  });
}

// E. Two requested changes; the proof covers one. "Show details" should
// reveal #details and hide #summary; below 768px #summary stays visible.
const SUMMARY = sel('#summary');
const twoTargetPage = {
  initial: () => new Map([[key(sel('h1')), 'visible'], [key(SHOW), 'visible'], [key(D), 'hidden'], [key(SUMMARY), 'visible']]),
  click: (s, k, w) => k !== key(SHOW) ? s : set(s, [[D, 'visible'], ...(w >= 768 ? [[SUMMARY, 'hidden']] : [])]),
};
const OWNER_TWO = 'Create and publish a website in a new workspace directory site. Add a "Show details" button that reveals the hidden details and hides the summary.';
for (const transport of TRANSPORTS) {
  test(`E1 proof of the reveal at 800, later at 375 shows the summary not hidden (${transport})`, async () => {
    await scenario('E1-two-targets/summary-visible-later', {expect: 'failed', owner: OWNER_TWO, page: twoTargetPage, transport,
      proof: [step('assert-hidden', D), step('click', SHOW), step('assert-visible', D)],
      later: [step('click', SHOW), step('assert-visible', SUMMARY)]});
  });
}

// F. Mutually exclusive elements: "Pricing" tab shows the pricing panel and the
// features panel is hidden; at 375 the tab switch fails (features stays).
const PRICING = sel('#pricing'), FEATURES = sel('#features'), TAB = {role: 'tab', name: 'Pricing', exact: true};
const tabPage = {
  initial: () => new Map([[key(sel('h1')), 'visible'], [key(TAB), 'visible'], [key(PRICING), 'hidden'], [key(FEATURES), 'visible']]),
  click: (s, k, w) => k !== key(TAB) || w < 768 ? s : set(s, [[PRICING, 'visible'], [FEATURES, 'hidden']]),
};
const OWNER_TAB = 'Create and publish a website in a new workspace directory site. When the visitor clicks the "Pricing" tab, it shows the hidden pricing panel.';
for (const transport of TRANSPORTS) {
  test(`F1 proof of the tab at 800, later at 375 shows the features panel still visible after the tab click (${transport})`, async () => {
    await scenario('F1-tabs/exclusive-panel-visible', {expect: 'failed', owner: OWNER_TAB, page: tabPage, transport,
      proof: [step('assert-hidden', PRICING), step('click', TAB), step('assert-visible', PRICING)],
      later: [step('click', TAB), step('assert-visible', FEATURES)]});
  });
}

// G. Lifecycle checks on a proof whose change followed a preparatory click:
// it keeps nothing across a later click, and failed, unavailable, page errors,
// INCOMPLETE-shaped results, republish and another session revoke it too.
for (const transport of TRANSPORTS) {
  test(`G lifecycle revocations after a cookie-banner proof (${transport})`, async () => {
    const proof = [step('assert-visible', COOKIE), step('assert-hidden', D), step('click', ACCEPT), step('assert-hidden', COOKIE), step('click', SHOW), step('assert-visible', D)];
    const later = [step('assert-visible', sel('h1')), step('click', SHOW), step('assert-hidden', sel('#toast'))];
    const out = [];
    for (const fault of ['none', 'failed', 'unavailable', 'page-errors', 'incomplete', 'republish', 'session']) {
      const {guard, preview} = setup(OWNER_REVEAL);
      await inspect(guard, preview, cookiePage, proof, 'proof', {transport});
      const start = status(guard);
      let target = preview;
      const page = {...cookiePage, initial: w => set(cookiePage.initial(w), [[sel('#toast'), 'hidden']])};
      let request, mutate;
      if (fault === 'failed') request = async r => { const v = capsule(page)(r); v.status = 'failed'; v.steps.at(-1).status = 'failed'; v.steps.at(-1).errorCode = 'visibility_mismatch'; return v; };
      if (fault === 'unavailable') request = async () => { throw Error('down'); };
      if (fault === 'page-errors') request = async r => capsule(page, {pageErrors: {count: 1, messages: ['TypeError: x is undefined']}})(r);
      // A proof suppresses the tool's own INCOMPLETE, so craft the exact shape it returns.
      if (fault === 'incomplete') mutate = r => ({content: r.content, isError: true, details: {schemaVersion: 1, kind: INSPECTION_KIND,
        status: 'incomplete', errorCode: 'transition_untested', siteId: r.details.siteId, sha256: r.details.sha256,
        planSha256: r.details.planSha256, scope: INSPECTION_SCOPE, receipt: r.details}});
      if (fault === 'republish') target = publish(guard, '<!doctype html><h1>Site v2</h1><button>Show details</button><p id="details" hidden>Details</p>', 'republish', preview.relativeDirectory);
      if (fault === 'session') guard.observeRun({...context, sessionId: 'other'}, 'pixel', {prompt: OWNER_REVEAL});
      if (fault !== 'session') await inspect(guard, target, page, fault === 'incomplete' ? [step('assert-visible', sel('h1'))] : later, 'later', {transport, request, mutate});
      out.push(`${fault}: start=${start} final=${status(guard)}`);
    }
    assert.deepEqual(out, ['none', 'failed', 'unavailable', 'page-errors', 'incomplete', 'republish', 'session']
      .map(fault => `${fault}: start=passed final=failed`));
  });
}

// H. Static and round-108-shaped later plans keep the new proof shapes (no
// regression of the fix itself).
for (const transport of TRANSPORTS) {
  test(`H keeps: round-108 shape and static plans after a one-click proof (${transport})`, async () => {
    await scenario('H1-r108-shape', {expect: 'passed', owner: OWNER_TOGGLE, page: togglePage, transport, laterWidth: 1280,
      proof: [step('assert-hidden', D), step('click', TOGGLE), step('assert-visible', D)],
      later: [step('assert-visible', sel('h1')), step('click', TOGGLE), step('assert-visible', D)]});
    await scenario('H2-static-later', {expect: 'passed', owner: OWNER_TOGGLE, page: togglePage, transport, laterWidth: 1280,
      proof: [step('assert-hidden', D), step('click', TOGGLE), step('assert-visible', D)],
      later: [step('assert-visible', sel('h1')), step('assert-hidden', D)]});
  });
}
