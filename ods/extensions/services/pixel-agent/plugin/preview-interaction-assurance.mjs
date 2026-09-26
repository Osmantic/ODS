import { isDeepStrictEqual } from 'node:util';
import {
  hasVisibilityTransitionPlan,
  inspectionControls,
  inspectionPageErrors,
  normalizeWorkspacePreviewInspectionParams,
  validateIncompleteInspectionReceipt,
  validateWorkspacePreviewInspectionReceipt,
} from './workspace-preview-inspect.mjs';
export { hasVisibilityTransitionPlan } from './workspace-preview-inspect.mjs';
import { canonicalText } from './requested-literals.mjs';

export const PREVIEW_INSPECTION_TOOL = 'pixel_ods_workspace_preview_inspect';

// This capability checks visibility transitions. Do not turn arbitrary form,
// navigation, animation or visual requests into checks the tool cannot perform.
export function requestsVisibilityInteraction(text) {
  return String(text ?? '').split(/[!?;\n]+|\.(?=\s|$)/).some(clause =>
    !/^\s*(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|explain|describe|example)\b/i.test(clause) &&
    /\b(?:clicks?|buttons?|toggles?|expands?|collapses?)\b/i.test(clause) &&
    /\b(?:shows?|hides?|hidden|reveals?|toggles?|expands?|collapses?|visible)\b/i.test(clause));
}

// The owner's own wording of a requested show/hide change, used only to name
// the likely affected element and control in an inspection's corrective steps
// (laptop round 100: the model asserted the button and a post-click class,
// never the card). It is never evidence that the change works. The target is
// an owner-required literal (requested-literals.mjs) inside a show/hide clause
// other than the control's name; the control is a quoted name directly after
// "button" (or link/tab/switch). Only an explicit initially-hidden or hide-on-
// click wording decides the direction; otherwise the target starts hidden.
const QUOTED = /"[^"\n]*"|“[^”\n]*”|«[^»\n]*»|‘[^’\n]*’|(?<![\p{L}\p{N}])'[^'\n]*'(?![\p{L}\p{N}])/gu;
// "a visible footer" describes content, not a change of visibility.
const SHOW_HIDE_CLAUSE = /\b(?:shows?|shown|showing|hides?|hidden|hiding|reveals?|revealed|revealing|toggles?|toggled|expands?|expanded|collapses?|collapsed|appears?|disappears?)\b|\b(?:becomes?|made|makes?|turns?)\s+(?:in)?visible\b/i;
const STARTS_HIDDEN = /\b(?:initially|at\s+first|by\s+default|on\s+(?:page\s+)?load)\b[^.!?;\n]{0,40}\b(?:hide|hidden|invisible)\b|\b(?:hide|hidden|invisible)\b[^.!?;\n]{0,80}\b(?:initially|at\s+first|by\s+default|on\s+(?:page\s+)?load|until)\b|\bstarts?\s+(?:out\s+)?(?:hidden|collapsed|invisible)\b/i;
const CLICK_REVEALS = /\b(?:reveals?|shows?|expands?|opens?|displays?|unhides?)\b/i;
const CLICK_CONCEALS = /\b(?:hides?|collapses?|dismiss(?:es)?|closes?)\b/i;
const CONTROL_ROLES = {button: 'button', toggle: 'button', link: 'link', tab: 'tab', switch: 'switch'};
const foldedText = value => canonicalText(value).toLowerCase();
const escapeRegExp = value => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const phraseIn = (text, phrase) => Boolean(phrase) &&
  new RegExp(`(?<![\\p{L}\\p{N}])${escapeRegExp(phrase)}(?![\\p{L}\\p{N}])`, 'u').test(text);

export function requestedVisibilityTransition(ownerText, literals) {
  const prose = String(ownerText ?? '').slice(0, 12000)
    .replace(/```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)/g, '\n').replace(/^[ \t]*>[^\n]*/gm, '\n');
  const clauses = prose.split(/[!?;\n]+|\.(?=\s|$)/).map(clause => ({clause, bare: clause.replace(QUOTED, ' ')}))
    .filter(({bare}) => !/^\s*(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|explain|describe|example)\b/i.test(bare) &&
      SHOW_HIDE_CLAUSE.test(bare));
  if (!clauses.length) return undefined;
  const text = Array.isArray(literals) ? literals.filter(literal => literal && literal.match !== 'file' &&
    typeof literal.text === 'string' && !literal.targets?.some(target => target === 'page title' || target === 'h1')) : [];
  let control;
  for (const literal of text.filter(literal => literal.match !== 'item')) {
    const cue = new RegExp(`\\b(button|toggle|link|tab|switch)\\b[^"“”«»‘’.!?;\\n]{0,48}["“«‘']\\s?${escapeRegExp(literal.text)}\\s?["”»’']`, 'iu')
      .exec(canonicalText(prose));
    if (cue) { control = Object.freeze({role: CONTROL_ROLES[cue[1].toLowerCase()], name: literal.text}); break; }
  }
  const candidates = text.filter(literal => literal.text !== control?.name);
  const target = [...candidates.filter(literal => literal.match === 'item'), ...candidates.filter(literal => literal.match !== 'item')]
    .find(literal => clauses.some(({clause}) => phraseIn(foldedText(clause), foldedText(literal.text))))?.text;
  const hidesOnClick = clauses.some(({bare}) => /\b(?:click(?:s|ed|ing)?|press(?:es|ed)?|tap(?:s|ped)?|buttons?|toggles?)\b/i.test(bare) &&
    CLICK_CONCEALS.test(bare) && !CLICK_REVEALS.test(bare));
  const initiallyHidden = clauses.some(({bare}) => STARTS_HIDDEN.test(bare)) || !hidesOnClick;
  return Object.freeze({...(target ? {target} : {}), ...(control ? {control} : {}), initiallyHidden});
}

// A later turn that preserves the bound behavior keeps the earlier wording
// for whatever the current message does not name itself.
export function inheritedVisibilityTransition(current, inherited) {
  if (!inherited) return current;
  if (current?.target) return current.control || !inherited.control ? current : Object.freeze({...current, control: inherited.control});
  return Object.freeze({...inherited, ...(current?.control ? {control: current.control} : {})});
}

// This does not identify an interaction or establish that one works. It only
// recognizes an owner's explicit request to preserve a previously bound duty.
// Callers must supply current owner prose, not assistant text or quoted examples.
export function requestsBehaviorPreservation(text) {
  return String(text ?? '').split(/[.!?;\n]+/).some(clause =>
    !/\b(?:do\s+not|don['’]t|never|avoid|skip|explain|describe|example)\b/i.test(clause) &&
    /\b(?:preserve|retain|keep|maintain)\b[^.!?;\n]{0,160}\b(?:behaviou?r|functionality|interactions?)\b/i.test(clause));
}

// `incomplete` also accepts an untested-transition result and yields the
// capsule receipt it carries; only load-time evidence readers pass it.
function boundReceipt(params, result, preview, {incomplete = false} = {}) {
  if (!preview || !result?.details) return undefined;
  const request = normalizeWorkspacePreviewInspectionParams(params);
  if (request.siteId !== preview.siteId || request.sha256 !== preview.sha256) return undefined;
  return {request, receipt: incomplete && result.details.status === 'incomplete'
    ? validateIncompleteInspectionReceipt(result.details, request)
    : validateWorkspacePreviewInspectionReceipt(result.details, request)};
}

// Passing steps on a page that threw uncaught script errors are not verified
// interactions; they neither establish nor preserve interaction evidence.
function boundInspection(params, result, preview, acceptsPlan) {
  try {
    if (result?.isError) return undefined;
    const {request, receipt} = boundReceipt(params, result, preview) ?? {};
    if (receipt?.status !== 'passed' || inspectionPageErrors(receipt) || !acceptsPlan(request)) return undefined;
    return Object.freeze({siteId: receipt.siteId, sha256: receipt.sha256, planSha256: receipt.planSha256,
      transitions: oneClickTransitions(request)});
  } catch { return undefined; }
}

// The changes a passing plan observed from exactly one click: a locator
// asserted before any click and in the other state before any second click,
// with the state that click left it in. A change seen only after a
// preparatory or second click is not recorded.
function oneClickTransitions(request) {
  const clicks = request.steps.flatMap((step, index) => step.action === 'click' ? [index] : []);
  if (clicks.length !== 1) return Object.freeze([]);
  const atLoad = request.steps.slice(0, clicks[0]);
  return Object.freeze(request.steps.slice(clicks[0] + 1, clicks[1]).filter(after => atLoad.some(before =>
    before.action !== after.action && isDeepStrictEqual(before.locator, after.locator)))
    .map(after => Object.freeze({locator: structuredClone(after.locator), result: after.action})));
}

// Whether a later passing plan may have seen a proved change not happen. A
// plan without a click cannot (#6626). A plan with more than one click can: a
// toggle's second click or a preparatory click changes what the next
// assertion means. With one click, each assertion must name a proved target
// exactly: before the click in the state the proof saw at load, after it in
// the state the proved change produced (tower2 round 108 re-asserted the
// proved heading after clicking its button by id). Any other locator may be
// the proved target under another name, or another element the requested
// change was meant to reach, so it keeps nothing; the proof is then taken
// again, as before retention existed. A proof without a one-click change
// keeps nothing across a click.
function mayContradictTransitions(request, transitions) {
  const clicks = request.steps.filter(step => step.action === 'click').length;
  if (!clicks) return false;
  if (clicks > 1 || !transitions.length) return true;
  let clicked = false;
  return request.steps.some(step => {
    if (step.action === 'click') { clicked = true; return false; }
    const proved = transitions.find(change => isDeepStrictEqual(change.locator, step.locator));
    return clicked ? proved?.result !== step.action : Boolean(proved) && proved.result === step.action;
  });
}

// Identifies only that this snapshot's latest bound receipt recorded page
// errors, to select the repair instruction. It never grants any evidence.
export function boundInspectionPageErrors(params, result, preview) {
  try {
    const {receipt} = boundReceipt(params, result, preview) ?? {};
    return inspectionPageErrors(receipt) ? Object.freeze({siteId: receipt.siteId, sha256: receipt.sha256}) : undefined;
  } catch { return undefined; }
}

// The load-time control names of a valid receipt bound to this snapshot,
// passed, failed or incomplete (an untested requested show/hide change; its
// capsule receipt passed): they are observed before any step runs, so a
// failed step or a missing transition does not void them. `controls` is
// undefined when the receipt carries none (an older capsule or a transport
// failure). Never interaction evidence.
export function boundInspectionControls(params, result, preview) {
  try {
    const {receipt} = boundReceipt(params, result, preview, {incomplete: true}) ?? {};
    return receipt ? Object.freeze({siteId: receipt.siteId, sha256: receipt.sha256,
      controls: inspectionControls(receipt)}) : undefined;
  } catch { return undefined; }
}

export function boundVisibilityInspection(params, result, preview) {
  return boundInspection(params, result, preview, hasVisibilityTransitionPlan);
}

// A later passing inspection of the proof's snapshot keeps that proof unless an
// assertion after a click may show a proved change not happening. Tower2 round
// 108 passed assert-hidden(heading), click("#soldOutBtn"), assert-visible(heading),
// then assert-visible("h1"), click("#soldOutBtn"), assert-visible(heading), and
// the second pass dropped the first. A plan without a click keeps it (#6626).
// It never establishes a proof; a failed or incomplete receipt, another
// snapshot or page errors keep none.
export function preservesVisibilityInspection(proof, params, result, preview) {
  return visibilityInspectionMatches(proof, preview) && Boolean(boundInspection(params, result, preview,
    request => !mayContradictTransitions(request, proof.transitions ?? [])));
}

export function visibilityInspectionMatches(proof, preview) {
  return Boolean(proof && preview && proof.siteId === preview.siteId && proof.sha256 === preview.sha256);
}

export const PAGE_ERROR_REPAIR_INSTRUCTION = `The published preview is available, but the latest browser inspection of this snapshot recorded uncaught page script errors, so its interactions are not verified. Fix the script so it does not throw (for example, guard every localStorage/sessionStorage access with try/catch and an in-memory fallback, per the preview storage contract), republish, then call ${PREVIEW_INSPECTION_TOOL} on the new snapshot with the same checks. Do not claim the interactions work while the page throws.`;

// Stable text (no identifiers or counts) so per-slot coaching dedupe applies.
export function pageErrorRepairInstruction(preview, pageErrors) {
  return visibilityInspectionMatches(pageErrors, preview) ? PAGE_ERROR_REPAIR_INSTRUCTION : undefined;
}

export function visibilityInspectionInstruction(preview, pageErrors) {
  return pageErrorRepairInstruction(preview, pageErrors) ?? `The published files are verified, but the requested show/hide interaction is not. Before replying, call ${PREVIEW_INSPECTION_TOOL} directly with siteId ${JSON.stringify(preview.siteId)}, sha256 ${JSON.stringify(preview.sha256)}, viewport {width,height}, and steps. Use its offered schema. If the tool is deferred, call tool_describe with its exact id, then tool_call with the returned id and args. Copy these exact publication identifiers; do not guess or shorten them. Choose the actual requested control and affected element from your source: assert the element's initial visibility, click the control using its exact supported accessible role/name when available, then assert the opposite visibility of that same element. Use a stable CSS selector for an element without a supported semantic locator. Do not substitute an unrelated passing interaction. If arguments are rejected, correct them from the schema and this receipt. If a browser check fails, repair the relevant files, republish, and inspect that new snapshot within the existing turn budget. If inspection is unavailable or unfinished, retain the preview and report the requested interaction as unverified. These checks cover only listed CSS layout visibility transitions, not overall correctness.`;
}
