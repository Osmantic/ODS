import {
  hasVisibilityTransitionPlan,
  normalizeWorkspacePreviewInspectionParams,
  validateWorkspacePreviewInspectionReceipt,
} from './workspace-preview-inspect.mjs';
export { hasVisibilityTransitionPlan } from './workspace-preview-inspect.mjs';

export const PREVIEW_INSPECTION_TOOL = 'pixel_ods_workspace_preview_inspect';

// This capability checks visibility transitions. Do not turn arbitrary form,
// navigation, animation or visual requests into checks the tool cannot perform.
export function requestsVisibilityInteraction(text) {
  return String(text ?? '').split(/[!?;\n]+|\.(?=\s|$)/).some(clause =>
    !/^\s*(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|explain|describe|example)\b/i.test(clause) &&
    /\b(?:clicks?|buttons?|toggles?|expands?|collapses?)\b/i.test(clause) &&
    /\b(?:shows?|hides?|hidden|reveals?|toggles?|expands?|collapses?|visible)\b/i.test(clause));
}

// This does not identify an interaction or establish that one works. It only
// recognizes an owner's explicit request to preserve a previously bound duty.
// Callers must supply current owner prose, not assistant text or quoted examples.
export function requestsBehaviorPreservation(text) {
  return String(text ?? '').split(/[.!?;\n]+/).some(clause =>
    !/\b(?:do\s+not|don['’]t|never|avoid|skip|explain|describe|example)\b/i.test(clause) &&
    /\b(?:preserve|retain|keep|maintain)\b[^.!?;\n]{0,160}\b(?:behaviou?r|functionality|interactions?)\b/i.test(clause));
}

export function boundVisibilityInspection(params, result, preview) {
  try {
    if (!preview || result?.isError || !result?.details) return undefined;
    const request = normalizeWorkspacePreviewInspectionParams(params);
    if (request.siteId !== preview.siteId || request.sha256 !== preview.sha256) return undefined;
    const receipt = validateWorkspacePreviewInspectionReceipt(result.details, request);
    if (receipt.status !== 'passed' || !hasVisibilityTransitionPlan(request)) return undefined;
    return Object.freeze({siteId: receipt.siteId, sha256: receipt.sha256, planSha256: receipt.planSha256});
  } catch { return undefined; }
}

export function visibilityInspectionMatches(proof, preview) {
  return Boolean(proof && preview && proof.siteId === preview.siteId && proof.sha256 === preview.sha256);
}

export function visibilityInspectionInstruction(preview) {
  return `The published files are verified, but the requested show/hide interaction is not. Before replying, call ${PREVIEW_INSPECTION_TOOL} directly with siteId ${JSON.stringify(preview.siteId)}, sha256 ${JSON.stringify(preview.sha256)}, viewport {width,height}, and steps. Use its offered schema. If the tool is deferred, call tool_describe with its exact id, then tool_call with the returned id and args. Copy these exact publication identifiers; do not guess or shorten them. Choose the actual requested control and affected element from your source: assert the element's initial visibility, click the control using its exact supported accessible role/name when available, then assert the opposite visibility of that same element. Use a stable CSS selector for an element without a supported semantic locator. Do not substitute an unrelated passing interaction. If arguments are rejected, correct them from the schema and this receipt. If a browser check fails, repair the relevant files, republish, and inspect that new snapshot within the existing turn budget. If inspection is unavailable or unfinished, retain the preview and report the requested interaction as unverified. These checks cover only listed CSS layout visibility transitions, not overall correctness.`;
}
