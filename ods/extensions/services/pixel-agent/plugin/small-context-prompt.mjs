import {hostDateContext} from './completion-assurance.mjs';

// Only trusted runtime/configuration limits select this profile. Owner prose,
// tool results, and the leanPrompt preference cannot lower the context limit.
export function usesSmallContextPrompt(context, configuredContextWindow) {
  const limits = [configuredContextWindow, context?.contextTokenBudget,
    context?.contextWindowReferenceTokens].filter(value => Number.isInteger(value) && value > 0);
  return limits.length > 0 && Math.min(...limits) <= 8192;
}

// The conversation/security core and requested task supplements remain intact.
// Avoid repeating their instructions and defer tool argument detail to describe.
export function smallContextExecutionContext(now = new Date(), executionHost) {
  const namespace = executionHost === 'gateway'
    ? 'Native exec uses configured owner workspace; use relative operands, never /workspace. '
    : executionHost === 'sandbox' ? 'Sandbox paths are workspace-relative; do not use host-side paths. ' : '';
  return namespace + hostDateContext(now) +
    'Honor owner date/timezone; verify news publication dates. Act on requests/follow-ups. Exact files: write/readback; shell literal printf, not echo escapes; never claim mismatched bytes match. Tool Search discovers tools, not news/files: use returned IDs/schemas. Empty results prove neither absence nor future dates; try sources or state uncertainty. Discover pixel_ods_ask_user for 1–3 material preference questions in owner language, then wait; no routine approval questions or unrelated consent. ' +
    'Multi-step work: discover pixel_ods_activity; brief public owner-language update before action, then verified phase/finding/blocker. No secrets, raw output, private reasoning, repetition or false success. Updates do not execute. Skip simple chat; missing tool: continue, no guessing/retries. ' +
    'Host execution is not inference: loopback may be remote. Claim inference location/token ratios only with current route/usage evidence; otherwise unverified. Use selected model/configured tools, no invented hosts/quotas. Fleet needs owner request and configured capabilities, not examples.';
}

export const SMALL_CONTEXT_PROJECT_GUIDE = 'For npm/Python dependencies, tests or artifacts, discover pixel_ods_project_build and follow its full description/schema before acting. Use managed builds, not host installation. Availability grants no permission: honor controller denials, never bypass approval or resubmit an uncertain result.';
