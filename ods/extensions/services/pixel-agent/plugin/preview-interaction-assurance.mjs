import { isDeepStrictEqual } from 'node:util';
import {
  normalizeWorkspacePreviewInspectionParams,
  validateWorkspacePreviewInspectionReceipt,
} from './workspace-preview-inspect.mjs';

export const PREVIEW_INSPECTION_TOOL = 'pixel_ods_workspace_preview_inspect';

// This capability checks visibility transitions. Do not turn arbitrary form,
// navigation, animation or visual requests into checks the tool cannot perform.
export function requestsVisibilityInteraction(text) {
  return String(text ?? '').split(/[!?;\n]+|\.(?=\s|$)/).some(clause =>
    !/^\s*(?:please\s+)?(?:do\s+not|don['’]t|never|avoid|skip|explain|describe|example)\b/i.test(clause) &&
    /\b(?:clicks?|buttons?|toggles?|expands?|collapses?)\b/i.test(clause) &&
    /\b(?:shows?|hides?|hidden|reveals?|toggles?|expands?|collapses?|visible)\b/i.test(clause));
}

export function hasVisibilityTransitionPlan(request) {
  // A click dispatch or an unchanged button does not prove its effect.
  return request.steps.some((step, index) => step.action === 'click' &&
    request.steps.slice(0, index).some(before => before.action.startsWith('assert-') &&
      request.steps.slice(index + 1).some(after =>
        after.action.startsWith('assert-') && after.action !== before.action &&
        isDeepStrictEqual(before.locator, after.locator))));
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
  return `The published files are verified, but the requested show/hide interaction is not. Before claiming it works, call ${PREVIEW_INSPECTION_TOOL} for siteId ${JSON.stringify(preview.siteId)} and sha256 ${JSON.stringify(preview.sha256)}. Choose the actual requested control and affected element from your source: assert the element's initial visibility, click the control using its exact accessible role/name when available, then assert the opposite visibility of that same element. Do not substitute an unrelated passing interaction. If a check fails, repair the relevant files, republish, and inspect that new snapshot within the existing turn budget. If inspection is unavailable or unfinished, retain the preview and report the requested interaction as unverified. These checks cover only listed CSS layout visibility transitions, not overall correctness.`;
}
