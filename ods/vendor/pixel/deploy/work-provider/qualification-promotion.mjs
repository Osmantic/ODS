import { createHash } from "node:crypto";

import { canonical, validateWorkProviderRouterQualification } from "../../scripts/lib/work-contract.mjs";
import { QUALIFICATION_TASKS, OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS } from "./neutral-corpus.mjs";

// Sealed, deterministic promotion of a closed, fully-passing qualification trial into a
// hash-bound work-provider-router-qualification-v1 semantic-capability document that the
// production router can consume. The promotion never reuses the connectivity-smoke path; it
// emits a semantic-capability document only when every required repetition passed AND the
// trial run ledger is closed (status "completed"). A failed or uncertain trial emits null and
// no semantic qualification. All content remains content-free: only task classes, a
// conservative capability envelope, pricing budget estimates, and hashes are recorded.

export class WorkProviderQualificationPromotionError extends Error {}
function fail(message) { throw new WorkProviderQualificationPromotionError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function iso(value) { return value.toISOString().replace(/\.000Z$/u, "Z"); }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}

export const QUALIFICATION_SCHEMA = "https://osmantic.com/pixel/schemas/work-provider-router-qualification-v1.schema.json";
export const QUALIFICATION_BOUNDARY = "Content-free hash-bound provider capability qualification evidence. It attests an exact capability envelope and expiry for one provider without prompt, response, reasoning, tool arguments, credential, path, or execution authority. connectivity-smoke attests transport reachability only and is never semantic task qualification.";
// Bounded semantic-capability lifetime (7 days). This is within the contract ceiling of 30 days
// and is a conservative closed expiry; the router independently enforces its policy max-age.
export const QUALIFICATION_EXPIRY_MILLIS = 7 * 86400000;
// Closed conservative Moonshot budget-rate constants. These are documented budget estimates for
// deterministic routing, never a billing claim or an authoritative price.
export const MOONSHOT_INPUT_MICROS_PER_MILLION_TOKENS = 1000000;
export const MOONSHOT_OUTPUT_MICROS_PER_MILLION_TOKENS = 3000000;
export const MOONSHOT_FIXED_MICROS_PER_RUN = 50000;
// Conservative per-run capability cost ceiling used to bound the remote envelope; the local
// envelope uses the schema-minimum ceiling (1) because local pricing is zero.
export const CAPABILITY_COST_CEILING_MICROS = 1000000;
// Router task classes that a passing neutral corpus trial can truthfully attest. These are the
// task classes actually exercised by the fixed corpus cases (the tool-choice and long-context
// controls are not router task classes and are never attested).
export const ATTESTED_ROUTER_TASK_CLASSES = Object.freeze(["failure-triage", "patch-proposal", "structural-review"]);

// Revalidates the complete sealed attestation invariants directly against the closed run ledger
// instead of trusting attestation.status. Returns false (fail closed, no router qualification)
// for any tampered, incomplete, failed, uncertain, or inconsistent trial.
function revalidateAttestation(attestation, ledger) {
  if (!attestation || typeof attestation !== "object" || Array.isArray(attestation)) return false;
  if (!ledger || typeof ledger !== "object" || Array.isArray(ledger)) return false;
  if (attestation.status !== "qualified" || ledger.status !== "completed") return false;
  if (!Number.isSafeInteger(attestation.distinctCaseCount) || !Number.isSafeInteger(attestation.trials) || attestation.trials < 1) return false;
  const requiredRepetitions = attestation.distinctCaseCount * attestation.trials;
  if (attestation.requiredRepetitions !== requiredRepetitions) return false;
  if (attestation.passedRepetitions !== requiredRepetitions || attestation.failedRepetitions !== 0 || attestation.uncertainRepetitions !== 0) return false;
  if (!Array.isArray(attestation.perCase) || attestation.perCase.length !== requiredRepetitions) return false;
  if (attestation.evidenceSha256 !== sha(attestation.perCase)) return false;
  if (attestation.ledgerRunId !== ledger.runId || attestation.ledgerSha256 !== sha(ledger)) return false;
  if (attestation.providerId !== ledger.providerId || attestation.model !== ledger.model) return false;
  if (!ledger.binding || ledger.binding.kind !== "qualification-trial") return false;
  if (attestation.corpusVersion !== ledger.binding.corpusVersion || attestation.corpusSha256 !== ledger.binding.corpusSha256 || attestation.lane !== ledger.binding.lane) return false;
  const expectedTasks = new Set(QUALIFICATION_TASKS);
  const seen = new Set();
  for (const entry of attestation.perCase) {
    if (entry.status !== "passed" || entry.gradePass !== true) return false;
    if (!Number.isSafeInteger(entry.trialIndex) || entry.trialIndex < 1 || entry.trialIndex > attestation.trials) return false;
    if (!expectedTasks.has(entry.task)) return false;
    const key = `${entry.task}:${entry.trialIndex}`;
    if (seen.has(key)) return false;
    seen.add(key);
    if (!Array.isArray(entry.requests) || entry.requests.length < 1) return false;
    for (const request of entry.requests) {
      if (request.state !== "succeeded" || request.responseModel !== attestation.model || request.failureCode !== null) return false;
      if (!Number.isSafeInteger(request.inputTokens) || !Number.isSafeInteger(request.outputTokens)) return false;
      if (typeof request.inputSha256 !== "string" || request.inputSha256.length !== 64) return false;
    }
  }
  if (seen.size !== requiredRepetitions) return false;
  for (let trialIndex = 1; trialIndex <= attestation.trials; trialIndex += 1) {
    const tasks = new Set();
    for (const entry of attestation.perCase) if (entry.trialIndex === trialIndex) tasks.add(entry.task);
    if (tasks.size !== expectedTasks.size || ![...expectedTasks].every((task) => tasks.has(task))) return false;
  }
  if (!Array.isArray(ledger.requests) || !ledger.totals || typeof ledger.totals !== "object") return false;
  const totalInput = ledger.requests.reduce((sum, request) => sum + (request.inputTokens ?? 0), 0);
  const totalOutput = ledger.requests.reduce((sum, request) => sum + (request.outputTokens ?? 0), 0);
  const uncertainCount = ledger.requests.filter((request) => request.state === "uncertain").length;
  if (ledger.totals.requests !== ledger.requests.length || ledger.totals.inputTokens !== totalInput
    || ledger.totals.outputTokens !== totalOutput || ledger.totals.uncertain !== uncertainCount) return false;
  if (uncertainCount !== 0 || ledger.requests.some((request) => request.state !== "succeeded")) return false;
  // Every evidence request is paired in order with the exact ledger request it claims
  // (the runner emits evidence and claims ledger requests in identical case -> trial ->
  // request order, covering the two-request tool-choice case and the repeated input hashes
  // across trials). Each pair must agree on input hash, exact usage, and response model so
  // promotion never trusts only aggregate counters.
  const evidenceRequests = [];
  for (const entry of attestation.perCase) for (const request of entry.requests) evidenceRequests.push(request);
  if (evidenceRequests.length !== ledger.requests.length) return false;
  for (let index = 0; index < evidenceRequests.length; index += 1) {
    const evidence = evidenceRequests[index], actual = ledger.requests[index];
    if (actual.state !== "succeeded" || typeof actual.inputSha256 !== "string" || actual.inputSha256.length !== 64) return false;
    if (actual.inputSha256 !== evidence.inputSha256) return false;
    if (actual.inputTokens !== evidence.inputTokens || actual.outputTokens !== evidence.outputTokens) return false;
    if (actual.responseModel !== evidence.responseModel || actual.responseModel !== attestation.model) return false;
  }
  return true;
}

// Builds the conservative semantic capability envelope directly from the content-free per-request
// trial evidence: workspace context, standard tools, no vision, inputTokenMax bounded by both the
// actual minimum reported long-context count and the corpus target, and outputTokenMax derived
// from the minimum reported output-capacity count capped at the output floor.
function capabilityFromEvidence(perCase, corpusTargetInputTokens, costMicrosPerRunMax) {
  const longContextInputs = perCase
    .filter((entry) => entry.task === "long-context-sentinel")
    .flatMap((entry) => entry.requests.map((request) => request.inputTokens));
  if (longContextInputs.length === 0) fail("semantic promotion requires exercised long-context evidence");
  const minimumReportedLongContext = Math.min(...longContextInputs.map((value) => (Number.isSafeInteger(value) ? value : Infinity)));
  if (!Number.isSafeInteger(minimumReportedLongContext)) fail("semantic promotion requires a provider-reported long-context input count");
  const outputCapacityOutputs = perCase
    .filter((entry) => entry.task === "output-capacity")
    .flatMap((entry) => entry.requests.map((request) => request.outputTokens))
    .filter((value) => Number.isSafeInteger(value));
  if (outputCapacityOutputs.length === 0) fail("semantic promotion requires exercised output-capacity evidence");
  const minimumReportedOutputCapacity = Math.min(...outputCapacityOutputs);
  if (minimumReportedOutputCapacity < OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS) fail("semantic promotion requires every output-capacity repetition at or above the output floor");
  const inputTokenMax = Math.min(minimumReportedLongContext, corpusTargetInputTokens);
  // outputTokenMax is the minimum actual reported output-token count across every passing
  // output-capacity repetition, capped at the output floor, never the small maximum of the
  // short semantic cases.
  const outputTokenMax = Math.min(OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS, minimumReportedOutputCapacity);
  return deepFreeze({
    taskClasses: [...ATTESTED_ROUTER_TASK_CLASSES],
    contextNeed: "workspace",
    toolsNeed: "standard",
    visionNeed: false,
    inputTokenMax,
    outputTokenMax,
    costMicrosPerRunMax,
  });
}

function boundPricing(pricing) {
  if (!pricing || typeof pricing !== "object" || Array.isArray(pricing)) return null;
  const { inputMicrosPerMillionTokens, outputMicrosPerMillionTokens, fixedMicrosPerRun } = pricing;
  const fields = [["inputMicrosPerMillionTokens", inputMicrosPerMillionTokens], ["outputMicrosPerMillionTokens", outputMicrosPerMillionTokens], ["fixedMicrosPerRun", fixedMicrosPerRun]];
  for (const [name, value] of fields) {
    if (!Number.isSafeInteger(value) || value < 0 || value > 1000000000) return null;
  }
  return deepFreeze({ inputMicrosPerMillionTokens, outputMicrosPerMillionTokens, fixedMicrosPerRun });
}

// Deterministic, sealed pricing source per lane. Moonshot and local-only carry closed sealed
// defaults; the openai/anthropic/openrouter lanes carry no invented per-token rate and instead
// require an exact owner-private-policy pricing binding. When no binding exists the cost
// evidence is unavailable and the promotion fails closed rather than emitting a live price claim.
function pricingForLane(lane, privatePolicy = null) {
  if (lane === "local-only") return deepFreeze({ inputMicrosPerMillionTokens: 0, outputMicrosPerMillionTokens: 0, fixedMicrosPerRun: 0 });
  if (lane === "moonshot-kimi") {
    return deepFreeze({ inputMicrosPerMillionTokens: MOONSHOT_INPUT_MICROS_PER_MILLION_TOKENS, outputMicrosPerMillionTokens: MOONSHOT_OUTPUT_MICROS_PER_MILLION_TOKENS, fixedMicrosPerRun: MOONSHOT_FIXED_MICROS_PER_RUN });
  }
  return boundPricing(privatePolicy?.pricing);
}

export function promoteQualificationTrial({ attestation, ledger, privatePolicy = null, corpusTargetInputTokens }) {
  if (!attestation || typeof attestation !== "object" || Array.isArray(attestation)) fail("semantic promotion requires a complete trial attestation");
  if (!ledger || typeof ledger !== "object" || Array.isArray(ledger)) fail("semantic promotion requires the trial run ledger");
  if (!Number.isSafeInteger(corpusTargetInputTokens) || corpusTargetInputTokens < 1) fail("semantic promotion requires an exact corpus target input count");
  // A semantic capability is emitted only after the complete sealed attestation revalidates
  // against the closed run ledger. Any tampered, failed, uncertain, or incomplete trial returns
  // null and never emits a router qualification.
  if (!revalidateAttestation(attestation, ledger)) return null;
  if (attestation.lane !== "local-only") {
    if (!privatePolicy || typeof privatePolicy !== "object" || Array.isArray(privatePolicy)) fail("remote semantic promotion requires the bound owner private policy");
  }

  // Cost evidence must be deterministic and fail closed when unavailable. A lane without a
  // sealed pricing source (or without an exact owner-private pricing binding) emits no router
  // qualification rather than an invented price claim.
  const pricing = pricingForLane(attestation.lane, privatePolicy);
  if (pricing === null) return null;

  const remote = attestation.lane !== "local-only";
  const costMicrosPerRunMax = remote
    ? Math.min(CAPABILITY_COST_CEILING_MICROS, privatePolicy.budgets.maxEstimatedCostMicrosPerRun)
    : 1; // local: zero pricing, schema-minimum per-run capability ceiling
  const capability = capabilityFromEvidence(attestation.perCase, corpusTargetInputTokens, costMicrosPerRunMax);

  const attestedAt = attestation.recordedAt;
  const attestedMs = Date.parse(attestedAt);
  if (!Number.isSafeInteger(attestedMs)) fail("semantic promotion requires a valid trial attestation time");
  const expiresAt = iso(new Date(attestedMs + QUALIFICATION_EXPIRY_MILLIS));
  // Deterministic sealed identity derived from the trial attestation evidence hash.
  const suffix = sha(attestation).slice(0, 12);
  const qualificationId = `workproviderqualification-${String(attestedMs).padStart(13, "0")}-${suffix}`;

  const doc = {
    $schema: QUALIFICATION_SCHEMA,
    schemaVersion: 1,
    qualificationId,
    providerId: attestation.providerId,
    model: attestation.model,
    attestedAt,
    expiresAt,
    attestationKind: "semantic-capability",
    capability,
    pricing,
    evidenceSha256: sha(attestation),
    boundary: QUALIFICATION_BOUNDARY,
  };
  const { qualificationSha256: _ignored, ...withoutSha } = doc;
  doc.qualificationSha256 = sha(withoutSha);
  const errors = validateWorkProviderRouterQualification(doc);
  if (errors.length) fail(`semantic promotion produced an invalid qualification: ${errors.join("; ")}`);
  return deepFreeze(doc);
}

export const workProviderQualificationPromotionInternals = Object.freeze({ capabilityFromEvidence, pricingForLane });
