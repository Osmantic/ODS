import { executeProviderTurn, closeProviderRun } from "./executor.mjs";
import { gradeDeterministic, NEUTRAL_TASK_MANIFEST } from "./grading.mjs";
import { extractPatchProposal, verifyPatchString } from "./patch-extraction.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";

// The neutral-task harness runs a bound semantic lane (executor + closed transport via an
// explicit test seam) and grades the result with a deterministic, model-free verifier. It
// never claims real provider equivalence; it proves that the harness exercises the bound
// executor/transport path and that an independent verifier grades the outcome.

function parseStructuredJsonResponse(value, label) {
  if (typeof value !== "string") throw new Error(`${label} is not strict JSON`);
  let candidate = value.trim();
  const fenced = /^```json[\t ]*\r?\n([\s\S]*?)\r?\n```$/iu.exec(candidate);
  if (fenced) candidate = fenced[1].trim();
  return parseStrictJson(candidate, label);
}

function summarizeForTask(task, assistant, lane) {
  if (!assistant) return null;
  const content = assistant.message?.content ?? null;
  switch (task) {
    case "structured-output":
    case "structural-review":
    case "failure-triage":
      if (typeof content === "string") { try { return parseStructuredJsonResponse(content, `${task} response`); } catch { return { raw: content }; } }
      return content;
    case "disposable-coding-patch":
    case "patch-proposal":
      return extractPatchProposal({ task, lane, assistant });
    case "tool-choice-continuation":
      return {
        chosenTool: assistant.message?.toolCalls?.[0]?.name ?? null,
        continuation: typeof content === "string" ? content : null,
      };
    case "long-context-sentinel":
      return typeof content === "string" ? content : "";
    case "known-uncertain-recovery":
      return null;
    default:
      return content;
  }
}

async function runNeutralLaneCore({
  task, decision, request, routerPolicy, enabledPrivatePolicies, qualifications, resolvedProvider, policy, privatePolicy, qualification,
  model, input, idempotencyKey, credentialHandle, proxyRoute, runStoreRoot,
  estimatedInputTokens = 100, maxEstimatedCostMicros, now, suffix, expected, base,
}, executeTurn) {
  if (!NEUTRAL_TASK_MANIFEST.includes(task)) throw new Error(`unknown neutral task ${task}`);
  const estimatedCost = maxEstimatedCostMicros ?? (resolvedProvider.profile.remote ? Math.min(100000, policy.budgets.maxEstimatedCostMicrosPerRun) : 0);
  const lane = resolvedProvider.profile.id === "local" ? "local-only" : resolvedProvider.profile.id;
  const outcome = await executeTurn({
    decision, request, routerPolicy, enabledPrivatePolicies, qualifications, resolvedProvider, policy, privatePolicy, qualification,
    taskId: `neutral-${task}`, model, idempotencyKey, input, credentialHandle, proxyRoute, runStoreRoot,
    estimatedInputTokens, maxOutputTokens: input.maxOutputTokens ?? 1024, maxEstimatedCostMicros: estimatedCost,
    now, suffix,
  });
  let actual = task === "known-uncertain-recovery"
    ? { outcome: outcome.status === "uncertain" ? "uncertain" : "failed-known", rerouted: false }
    : (outcome.assistant ? summarizeForTask(task, outcome.assistant, lane) : null);
  if (task === "disposable-coding-patch" && outcome.status === "succeeded") {
    actual = await verifyPatchString({ patch: actual, base, expected });
  }
  const grade = gradeDeterministic({ task, actual, expected, base });
  // An uncertain outcome is never automatically retried or rerouted and stays unsettled
  // (open for an explicit owner-gated reconcile), so it cannot be closed.
  const closed = outcome.status === "uncertain" ? null : await closeProviderRun({
    ledger: outcome.ledger, runStoreRoot, expectedLedgerSha256: outcome.ledgerSha256 ?? undefined, now,
  });
  return Object.freeze({
    task,
    providerId: resolvedProvider.profile.id,
    model: outcome.ledger.model,
    status: outcome.status,
    outcome,
    ledger: closed?.ledger ?? null,
    ledgerSha256: closed?.ledgerSha256 ?? outcome.ledgerSha256 ?? null,
    actual,
    grade,
  });
}

export async function runNeutralLane(options) {
  if (!options || typeof options !== "object" || Array.isArray(options) || Object.hasOwn(options, "transport") || Object.hasOwn(options, "testSeam")) throw new Error("production neutral lane cannot accept a caller-supplied transport");
  return runNeutralLaneCore(options, executeProviderTurn);
}

export const workProviderHarnessTestOnly = Object.freeze({
  runNeutralLaneWithExecutor(options, executeTurn) {
    if (typeof executeTurn !== "function") throw new Error("test neutral-lane executor is invalid");
    return runNeutralLaneCore(options, executeTurn);
  },
});

export { NEUTRAL_TASK_MANIFEST };
