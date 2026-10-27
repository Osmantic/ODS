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
  return String(text ?? '').split(/[!?;\n]+|\.(?=\s|$)/).some(clause => {
    if (/^\s*(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|explain|describe|example)\b/i.test(clause) ||
        !/\b(?:clicks?|buttons?|toggles?|expands?|collapses?)\b/i.test(clause)) return false;
    // Correcting an existing control's initial state does not request a new
    // content transition. Preserve new controls, quoted targets and mixed duties.
    const initialControlState = /\b(?:starts?|begins?|defaults?)\b/i.test(clause) &&
      /\b(?:aria-(?:pressed|expanded|hidden|checked|selected)|pressed|unpressed|expanded|collapsed|checked|unchecked|selected)\b/i.test(clause) &&
      !/\b(?:add|create|implement)\b/i.test(clause);
    const bare = initialControlState ? clause.replace(QUOTED, (name, offset) =>
      /\b(?:button|toggle|link|tab|switch)\s*,?\s*(?:(?:named|called|labelled|labeled)\s*)?$/i.test(clause.slice(0, offset)) ||
      /^\s*,?\s*(?:button|toggle|link|tab|switch)\b/i.test(clause.slice(offset + name.length)) ? ' ' : name)
      .replace(/\b(?:its|their|the|this|that|a|an|my|your|our)\s+(?:show|hide|reveal|expand|collapse)(?:[ \t]+[\p{L}\p{N}_-]+){0,4}[ \t]+(?:button|toggle|link|tab|switch)(?=[ \t]+(?:starts?|begins?|defaults?)\b)/giu, ' control ') : clause;
    if (/\b(?:hides?|hidden|reveals?|expands?|collapses?|visibility)\b/i.test(bare)) return true;
    // Static page contents can be shown or visible beside a form control.
    // Bind those ambiguous words to the control's action or show-control name.
    return /\bclick(?:s|ed|ing)?\b[^.!?;\n]{0,100}\bshows?\b/i.test(bare) ||
      /\b(?:buttons?|toggles?)\b(?:(?!\band\b)[^,.!?;\n]){0,80}\bshows?\b/i.test(bare) ||
      /\bshows?\b[^,.!?;\n]{0,80}\b(?:with|using|via|when|after|on)\b[^.!?;\n]{0,40}\b(?:buttons?|toggles?|click(?:s|ed|ing)?)\b/i.test(bare) ||
      /\bshow(?:[ \t]+(?!and\b)[\p{L}\p{N}_-]+){0,4}["”»’']?[ \t]+(?:buttons?|toggles?)\b/iu.test(bare) ||
      /\b(?:makes?|becomes?|turns?)\b[^.!?;\n]{0,40}\b(?:in)?visible\b/i.test(bare);
  });
}

// The owner wants the page's data to outlast a reload or a later visit. Portal
// shows previews with an opaque origin, in its card and in a new tab alike
// (the relay's CSP sandbox), so storage reads and writes throw and a guarded
// page keeps its data only in memory. No check can establish it (Workstation06,
// 2026-10-27: "make the ticks survive a page reload" ended "Everything checks out").
const KEEPS = /\b(?:survives?|surviving|keeps?|kept|saves?|saved|stores?|stored|retains?|retained|remembers?|remembered|persists?|persisted)\b/i;
const LATER = /\b(?:reloads?|reloading|refresh(?:es|ed|ing)?|restarts?|sessions?|visits?|re-?open(?:s|ed|ing)?|clos(?:e|es|ed|ing)|come\s+back|tomorrow|next\s+time)\b/i;
export function requestsReloadPersistence(text) {
  return String(text ?? '').split(/[!?;\n]+|\.(?=\s|$)/).some(clause => {
    if (/^\s*(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|explain|describe|example)\b/i.test(clause) ||
        /\b(?:do\s+not|don['’]t|never|without|no)\s+(?:use\s+)?(?:any\s+)?(?:local\s*storage|session\s*storage|indexed\s*db|persistence)\b/i.test(clause)) return false;
    return /\b(?:local|session)\s*storage\b|\bindexed\s*db\b|\bpersist(?:ent|ence)\b/i.test(clause) ||
      /\bremembers?\s+(?:my|our|your|their|the|them|it|everything|all)\b/i.test(clause) ||
      (KEEPS.test(clause) && LATER.test(clause));
  });
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
    return Object.freeze({siteId: receipt.siteId, sha256: receipt.sha256, planSha256: receipt.planSha256});
  } catch { return undefined; }
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

// Any valid passed inspection establishes this limited browser-load evidence.
// The receipt protocol omits pageErrors when none were observed. It does not
// certify other interactions or errors outside this inspection's time window.
export function boundRenderedPreviewInspection(params, result, preview) {
  return boundInspection(params, result, preview, () => true);
}

export function renderedInspectionInstruction(preview, pageErrors) {
  return pageErrorRepairInstruction(preview, pageErrors) ??
    `The published files passed HTTP readback, but the page has not passed browser inspection. Before replying, call ${PREVIEW_INSPECTION_TOOL} with siteId ${JSON.stringify(preview.siteId)}, sha256 ${JSON.stringify(preview.sha256)}, viewport {width,height}, and steps that check the actual page. Inspect the controls and results needed for the owner's request; a heading-only assertion checks page load only, not functionality. If the page throws, fix the script, republish, and inspect the new snapshot. If inspection is unavailable or unfinished, keep the preview and report the browser check as unverified.`;
}

export function boundVisibilityInspection(params, result, preview) {
  return boundInspection(params, result, preview, hasVisibilityTransitionPlan);
}

// A read-only inspection may preserve existing interaction evidence. It cannot
// establish that evidence, and a click plan without a transition is not static.
// assert-painted only observes, so a paint-only plan is read-only.
export function boundStaticPreviewInspection(params, result, preview) {
  return boundInspection(params, result, preview,
    request => request.steps.every(step => !['click', 'fill', 'select-option', 'download', 'press'].includes(step.action)));
}

const BEHAVIOR_ACTIONS = ['fill', 'click', 'select-option', 'press'];
// assert-painted is a postcondition too: press, then a painted canvas.
const BEHAVIOR_ASSERTIONS = ['assert-text', 'assert-visible', 'assert-hidden', 'assert-painted'];

// Remember the first bounded action plan, even if its locator schema was bad.
// This is the model's attempted check, not a natural-language interpretation
// of the owner's entire request. It grants no execution or passing evidence.
export function attemptedPreviewBehavior(params, preview) {
  if (!preview || params?.siteId !== preview.siteId || params?.sha256 !== preview.sha256 ||
      !Array.isArray(params.steps) || params.steps.length < 1 || params.steps.length > 12 ||
      params.steps.some(step => step?.action === 'download')) return undefined;
  // The model's first plan can contain wrong action types or too many actions
  // to leave room for postconditions. Remember a duty to test an interaction,
  // not an obligation to repeat those mistakes. This is only an evidence floor.
  return params.steps.some(step => BEHAVIOR_ACTIONS.includes(step?.action))
    ? Object.freeze({requiresInteraction: true}) : undefined;
}

// The step that keeps an action plan from counting as behavior evidence: the
// first click followed directly by another action (that action is `next`),
// else a last action with no assertion after it. Fill/select can prepare a
// click, but each click needs a postcondition before another action. A value
// change without a click needs one too. Key presses may chain (Space to start,
// then ArrowRight); the last action before the end still needs a postcondition.
export function unassertedBehaviorStep(steps) {
  let pending;
  for (const [index, step] of steps.entries()) {
    if (BEHAVIOR_ASSERTIONS.includes(step.action)) pending = undefined;
    else if (BEHAVIOR_ACTIONS.includes(step.action)) {
      if (pending?.action === 'click') return Object.freeze({...pending, next: Object.freeze({index, action: step.action})});
      pending = {index, action: step.action};
    }
  }
  return pending ? Object.freeze(pending) : undefined;
}

export function boundPreviewBehavior(params, result, preview, attempted) {
  if (!attempted) return undefined;
  return boundInspection(params, result, preview, request =>
    !request.steps.some(step => step.action === 'download') &&
    request.steps.some(step => BEHAVIOR_ACTIONS.includes(step.action)) &&
    !unassertedBehaviorStep(request.steps));
}

// `gap` is the unassertedBehaviorStep of this snapshot's latest passing
// inspection. That inspection's own result says "passed", and the generic
// instruction below usually went out already with the publication, so the
// step to correct is named first. Never evidence.
function unassertedStepLine(preview, gap) {
  if (!visibilityInspectionMatches(gap, preview)) return '';
  const lead = 'The latest inspection of this snapshot passed, but ';
  return gap.next
    ? `${lead}step ${gap.index + 1} (click) is followed directly by step ${gap.next.index + 1} (${gap.next.action}) with no assertion of the click's own result, so this plan does not count as interaction evidence. Insert an assertion of that click's visible result before step ${gap.next.index + 1} and retry the same published snapshot. `
    : `${lead}no assertion follows its last action, step ${gap.index + 1} (${gap.action}), so this plan does not count as interaction evidence. Add an assertion of that action's visible result after it and retry the same published snapshot. `;
}

export function previewBehaviorInstruction(preview, pageErrors, gap) {
  return pageErrorRepairInstruction(preview, pageErrors) ?? unassertedStepLine(preview, gap) +
    'The preview interaction check remains unverified. Test the actual controls and visible results needed for the owner\'s request. ' +
    `Read the actual controls and affected elements from the source; do not replace the behavior checks with a heading-only assertion. ` +
    `Call ${PREVIEW_INSPECTION_TOOL} with siteId ${JSON.stringify(preview.siteId)}, sha256 ${JSON.stringify(preview.sha256)}, viewport {width,height}, and valid steps. ` +
    'Use {selector:"#actual-id"} or {role:"button",name:"Exact accessible name",exact:true}. Every assert-text needs both locator and expectedText. ' +
    'After filling or selecting, click the relevant control and assert its visible result before another action. Each click needs a postcondition; a value change without a click needs one too. Key presses (press) may run in a row, but the last one needs a postcondition. ' +
    'Correct mistaken locators or action types from the source and tool schema; use click for a checkbox, and select-option only for a native select. Do not preserve a mistaken action just because it appeared in an earlier plan. Keep each plan within the tool limits, with at least one interaction and its postcondition. If a page check fails, first compare the plan\'s actions and expectation with the owner\'s request and the source. Correct a mistaken plan on the same published snapshot; do not change the site to satisfy a mistaken expectation. When the intended actions and expected outcome are right, repair the page, republish and rerun the intended checks within the existing turn budget. ' +
    'If unfinished or unavailable, keep the preview and report these checks as unverified. A pass covers only the submitted checks, not all requested behavior.';
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
  return pageErrorRepairInstruction(preview, pageErrors) ?? `The published files are verified, but the requested show/hide interaction is not. Before replying, call ${PREVIEW_INSPECTION_TOOL} directly with siteId ${JSON.stringify(preview.siteId)}, sha256 ${JSON.stringify(preview.sha256)}, viewport {width,height}, and steps. Use its offered schema. If the tool is deferred, call tool_describe with its exact id, then tool_call with the returned id and args. Copy these exact publication identifiers; do not guess or shorten them. Choose the actual requested control and affected element from your source: assert the element's initial visibility, click the control using its exact supported accessible role/name when available, then assert the opposite visibility of that same element. assert-visible and assert-hidden report whether the matched element is actually visible or hidden; they do not treat the disappearance of an attribute selector (for example an aria-pressed attribute) as visibility evidence. Use a stable CSS selector for an element without a supported semantic locator. Do not substitute an unrelated passing interaction. If arguments are rejected, correct them from the schema and this receipt. If a browser check fails, repair the relevant files, republish, and inspect that new snapshot within the existing turn budget. If inspection is unavailable or unfinished, retain the preview and report the requested interaction as unverified. These checks cover only listed CSS layout visibility transitions, not overall correctness.`;
}
