import { createHash, randomBytes } from "node:crypto";

import { canonical, validateWorkWatchdogDecision } from "../../scripts/lib/work-contract.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/u;
const TOOL_RE = /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const outcomes = new Set(["success", "error", "timeout", "cancelled"]);
const effectClasses = new Set(["read-only", "workspace", "brokered-network", "external"]);
const boundary = "Content-free preflight watchdog decision bound to the checkpoint and normalized proposed event. Continue means only that loop limits have not stopped the call; the exact lease must still authorize it.";

export class WorkWatchdogError extends Error {}

function fail(message) { throw new WorkWatchdogError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}
function timestamp(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`);
  return value;
}

function checkedProposal(proposal, sequence) {
  if (!proposal || typeof proposal !== "object" || Array.isArray(proposal) || !TOOL_RE.test(proposal.tool ?? "") || !effectClasses.has(proposal.effectClass) || !proposal.arguments || typeof proposal.arguments !== "object" || Array.isArray(proposal.arguments)) fail("watchdog proposal is invalid");
  const argumentsSha256 = sha(proposal.arguments);
  const normalized = { sequence, tool: proposal.tool, argumentsSha256, effectClass: proposal.effectClass };
  return { ...normalized, eventFingerprintSha256: sha({ tool: normalized.tool, argumentsSha256, effectClass: normalized.effectClass }) };
}

function checkedEvents(events) {
  if (!Array.isArray(events) || events.length > 4096) fail("watchdog event window is invalid");
  let priorTime = -1;
  let priorProgress = 0;
  let priorRecordSha256 = null;
  return events.map((entry, index) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry) || JSON.stringify(Object.keys(entry).sort()) !== JSON.stringify(["effectClass", "eventFingerprintSha256", "failureFingerprintSha256", "observedAt", "outcome", "previousEventSha256", "recordSha256", "sequence", "tool", "verifiedProgressEpoch"].sort())) fail("watchdog event shape is invalid");
    const time = Date.parse(entry.observedAt);
    const { recordSha256, ...body } = entry;
    if (entry.sequence !== index || !Number.isFinite(time) || time <= priorTime || !TOOL_RE.test(entry.tool ?? "") || !effectClasses.has(entry.effectClass) || !outcomes.has(entry.outcome) || !SHA_RE.test(entry.eventFingerprintSha256 ?? "") || entry.failureFingerprintSha256 !== null && !SHA_RE.test(entry.failureFingerprintSha256 ?? "") || entry.previousEventSha256 !== priorRecordSha256 || !SHA_RE.test(recordSha256 ?? "") || sha(body) !== recordSha256) fail("watchdog event chain is invalid");
    integer(entry.verifiedProgressEpoch, 0, 1000000, "verified progress epoch");
    if (entry.verifiedProgressEpoch < priorProgress || entry.verifiedProgressEpoch > priorProgress + 1) fail("verified progress epoch is not monotonic");
    priorTime = time;
    priorProgress = entry.verifiedProgressEpoch;
    priorRecordSha256 = recordSha256;
    return structuredClone(entry);
  });
}

export function createWatchdogEvent({ decision, outcome, failureFingerprintSha256 = null, verifiedProgressEpoch, observedAt = new Date() }) {
  const errors = validateWorkWatchdogDecision(decision);
  if (errors.length || decision.decision !== "continue") fail("only a valid continue decision can be recorded as an event");
  if (!outcomes.has(outcome) || failureFingerprintSha256 !== null && !SHA_RE.test(failureFingerprintSha256 ?? "")) fail("watchdog outcome is invalid");
  integer(verifiedProgressEpoch, 0, 1000000, "verified progress epoch");
  const body = {
    sequence: decision.proposal.sequence,
    observedAt: timestamp(observedAt, "event observation time").toISOString(),
    tool: decision.proposal.tool,
    effectClass: decision.proposal.effectClass,
    eventFingerprintSha256: decision.proposal.eventFingerprintSha256,
    outcome,
    failureFingerprintSha256,
    verifiedProgressEpoch,
    previousEventSha256: decision.eventHeadSha256,
  };
  return { ...body, recordSha256: sha(body) };
}

function consecutiveEquivalent(events, fingerprint) {
  let count = 1;
  for (let index = events.length - 1; index >= 0 && events[index].eventFingerprintSha256 === fingerprint; index -= 1) count += 1;
  return count;
}

function repeatedFailure(events) {
  const fingerprint = [...events].reverse().find((entry) => entry.failureFingerprintSha256)?.failureFingerprintSha256;
  if (!fingerprint) return 0;
  let count = 0;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].failureFingerprintSha256 !== fingerprint) break;
    count += 1;
  }
  return count;
}

function stalledEvents(events) {
  if (!events.length) return 0;
  const epoch = events.at(-1).verifiedProgressEpoch;
  let count = 0;
  for (let index = events.length - 1; index >= 0 && events[index].verifiedProgressEpoch === epoch; index -= 1) count += 1;
  return count;
}

function oscillating(events, candidateFingerprint) {
  const tail = [...events.map((entry) => entry.eventFingerprintSha256), candidateFingerprint].slice(-6);
  return tail.length === 6 && tail[0] === tail[2] && tail[2] === tail[4] && tail[1] === tail[3] && tail[3] === tail[5] && tail[0] !== tail[1];
}

export function assessWatchdog({ jobId, checkpointSha256, expectedEventHeadSha256, events, proposal, limits, allowedEffectClasses, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  if (!JOB_RE.test(jobId ?? "") || !SHA_RE.test(checkpointSha256 ?? "") || !SUFFIX_RE.test(suffix)) fail("watchdog immutable binding is invalid");
  const checked = checkedEvents(events);
  if (checked.length ? !SHA_RE.test(expectedEventHeadSha256 ?? "") || expectedEventHeadSha256 !== checked.at(-1).recordSha256 : expectedEventHeadSha256 !== null) fail("watchdog event chain differs from its trusted head");
  const proposed = checkedProposal(proposal, checked.length);
  if (!limits || typeof limits !== "object" || Array.isArray(limits) || JSON.stringify(Object.keys(limits).sort()) !== JSON.stringify(["maxEventsWithoutVerifiedProgress", "maxFailures", "maxRepeatedEquivalent", "maxRepeatedFailure", "maxToolCalls"].sort())) fail("watchdog limits are invalid");
  integer(limits.maxToolCalls, 1, 1000000, "tool-call limit");
  integer(limits.maxFailures, 1, 1000000, "failure limit");
  integer(limits.maxRepeatedEquivalent, 2, 100, "equivalent-call limit");
  integer(limits.maxRepeatedFailure, 1, 100, "repeated-failure limit");
  integer(limits.maxEventsWithoutVerifiedProgress, 4, 1000000, "verified-progress event limit");
  if (!Array.isArray(allowedEffectClasses) || new Set(allowedEffectClasses).size !== allowedEffectClasses.length || allowedEffectClasses.some((value) => !effectClasses.has(value))) fail("watchdog effect grant is invalid");
  const failures = checked.filter((entry) => entry.outcome !== "success").length;
  const equivalent = consecutiveEquivalent(checked, proposed.eventFingerprintSha256);
  const repeated = repeatedFailure(checked);
  const stalled = stalledEvents(checked) + 1;
  const oscillationDetected = oscillating(checked, proposed.eventFingerprintSha256);
  let reason = "within-envelope";
  let terminalState = null;
  if (!allowedEffectClasses.includes(proposed.effectClass) || proposed.effectClass === "external") [reason, terminalState] = ["effect-not-granted", "safety-stopped"];
  else if (checked.length >= limits.maxToolCalls) [reason, terminalState] = ["tool-call-budget", "budget-exhausted"];
  else if (failures >= limits.maxFailures) [reason, terminalState] = ["failure-budget", "budget-exhausted"];
  else if (equivalent >= limits.maxRepeatedEquivalent) [reason, terminalState] = ["repeated-equivalent-call", "no-progress"];
  else if (repeated >= limits.maxRepeatedFailure) [reason, terminalState] = ["repeated-failure", "no-progress"];
  else if (oscillationDetected) [reason, terminalState] = ["oscillation", "no-progress"];
  else if (stalled > limits.maxEventsWithoutVerifiedProgress) [reason, terminalState] = ["verified-progress-stalled", "no-progress"];
  const created = timestamp(now, "watchdog decision time");
  if (checked.length && created.getTime() <= Date.parse(checked.at(-1).observedAt)) fail("watchdog decision time did not advance");
  const receipt = {
    $schema: "https://osmantic.com/pixel/schemas/work-watchdog-decision-v1.schema.json",
    schemaVersion: 1,
    decisionId: `watchdog-${String(created.getTime()).padStart(13, "0")}-${suffix}`,
    jobId,
    createdAt: created.toISOString(),
    checkpointSha256,
    eventHeadSha256: expectedEventHeadSha256,
    proposal: proposed,
    observed: { eventCount: checked.length, toolCalls: checked.length, failures, consecutiveEquivalent: equivalent, eventsWithoutVerifiedProgress: stalled, repeatedFailureCount: repeated, oscillationDetected },
    decision: terminalState === null ? "continue" : "stop",
    terminalState,
    reason,
    authority: { grantsToolCall: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary,
  };
  const errors = validateWorkWatchdogDecision(receipt);
  if (errors.length) fail(`watchdog decision is invalid: ${errors[0]}`);
  return receipt;
}
