import { createHmac, randomBytes } from "node:crypto";

const TOOL_RE = /^[A-Za-z][A-Za-z0-9_.:-]{0,127}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const DEFAULT_LIMITS = Object.freeze({
  maxJsonDepth: 64,
  maxJsonNodes: 65536,
  maxRepeatedEquivalentOutcome: 3,
  maxRepeatedEquivalentFailure: 2,
  oscillationWindow: 6,
});
const RECEIPT_POLICY = "pixel-rpc-observation-loop-guard-v1";

export class RpcLoopGuardError extends Error {}

function fail(message) { throw new RpcLoopGuardError(message); }
function checkedLimits(overrides = {}) {
  if (!overrides || typeof overrides !== "object" || Array.isArray(overrides)) fail("RPC loop-guard limits are invalid");
  const limits = { ...DEFAULT_LIMITS, ...overrides };
  if (Object.keys(overrides).some((key) => !Object.hasOwn(DEFAULT_LIMITS, key))) fail("RPC loop-guard limits contain an unsupported field");
  for (const [field, minimum, maximum] of [
    ["maxJsonDepth", 1, 256],
    ["maxJsonNodes", 16, 1000000],
    ["maxRepeatedEquivalentOutcome", 2, 100],
    ["maxRepeatedEquivalentFailure", 1, 100],
    ["oscillationWindow", 6, 32],
  ]) if (!Number.isSafeInteger(limits[field]) || limits[field] < minimum || limits[field] > maximum) fail(`RPC loop-guard ${field} is invalid`);
  if (limits.oscillationWindow % 2 !== 0) fail("RPC loop-guard oscillation window must be even");
  return Object.freeze(limits);
}

function assertJsonValue(value, limits, label) {
  const pending = [{ value, depth: 0 }];
  let nodes = 0;
  while (pending.length) {
    const entry = pending.pop();
    nodes += 1;
    if (nodes > limits.maxJsonNodes) fail(`${label} exceeds the RPC loop-guard JSON node ceiling`);
    if (entry.depth > limits.maxJsonDepth) fail(`${label} exceeds the RPC loop-guard JSON depth ceiling`);
    const current = entry.value;
    if (current === null || typeof current === "string" || typeof current === "boolean") continue;
    if (typeof current === "number" && Number.isFinite(current)) continue;
    if (!current || typeof current !== "object") fail(`${label} is not a JSON value`);
    if (Array.isArray(current)) {
      for (let index = current.length - 1; index >= 0; index -= 1) pending.push({ value: current[index], depth: entry.depth + 1 });
      continue;
    }
    for (const key of Object.keys(current)) pending.push({ value: current[key], depth: entry.depth + 1 });
  }
}

function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
}

function fingerprint(value, limits, label, digest) {
  assertJsonValue(value, limits, label);
  return digest(canonical(value));
}

function repeatedAtTail(values, candidate) {
  let count = 1;
  for (let index = values.length - 1; index >= 0 && values[index] === candidate; index -= 1) count += 1;
  return count;
}

function oscillates(values, window) {
  if (values.length < window) return false;
  const tail = values.slice(-window);
  const left = tail[0];
  const right = tail[1];
  if (left === right) return false;
  return tail.every((value, index) => value === (index % 2 === 0 ? left : right));
}

export class RpcObservationLoopGuard {
  #active = new Map();
  #completedAsyncTasks = new Map();
  #completedCount = 0;
  #eventHeadSha256 = null;
  #fingerprintKey;
  #historyLimit;
  #limits;
  #recent = [];
  #seen = new Set();
  #stoppedReason = null;

  constructor(options = {}) {
    if (!options || typeof options !== "object" || Array.isArray(options)) fail("RPC loop-guard options are invalid");
    const { fingerprintKey, ...limitOverrides } = options;
    if (fingerprintKey !== undefined && (!Buffer.isBuffer(fingerprintKey) || fingerprintKey.length !== 32)) {
      fail("RPC loop-guard fingerprint key is invalid");
    }
    this.#fingerprintKey = fingerprintKey === undefined ? randomBytes(32) : Buffer.from(fingerprintKey);
    this.#limits = checkedLimits(limitOverrides);
    this.#historyLimit = Math.max(
      this.#limits.maxRepeatedEquivalentOutcome,
      this.#limits.maxRepeatedEquivalentFailure,
      this.#limits.oscillationWindow,
    );
  }

  get activeCount() { return this.#active.size; }

  #digest(value) { return createHmac("sha256", this.#fingerprintKey).update(value).digest("hex"); }

  start({ toolCallId, toolName, args }) {
    if (this.#stoppedReason !== null) fail("RPC loop guard is already stopped");
    if (typeof toolCallId !== "string" || toolCallId.length < 1 || Buffer.byteLength(toolCallId, "utf8") > 256 || !TOOL_RE.test(toolName ?? "") || this.#seen.has(toolCallId)) {
      fail("RPC tool start identity is invalid");
    }
    const argumentsSha256 = fingerprint(args, this.#limits, "RPC tool arguments", (value) => this.#digest(value));
    const callFingerprintSha256 = this.#digest(canonical({ toolName, argumentsSha256 }));
    this.#seen.add(toolCallId);
    this.#active.set(toolCallId, Object.freeze({ toolName, argumentsSha256, callFingerprintSha256 }));
  }

  update({ toolCallId, toolName, args, partialResult }) {
    const active = this.#active.get(toolCallId);
    const emptyTaskUpdate = toolName === "task" && args && typeof args === "object" && !Array.isArray(args) && Object.keys(args).length === 0;
    if (!active) {
      const completedArgumentsSha256 = toolName === "task" ? this.#completedAsyncTasks.get(toolCallId) : undefined;
      if (!completedArgumentsSha256) fail("RPC tool update identity differs from its start");
      if (!emptyTaskUpdate) {
        const argumentsSha256 = fingerprint(args, this.#limits, "RPC completed-task update arguments", (value) => this.#digest(value));
        if (argumentsSha256 !== completedArgumentsSha256) fail("RPC completed-task update arguments differ from its start");
      }
      assertJsonValue(partialResult, this.#limits, "RPC tool partial result");
      return;
    }
    if (active.toolName !== toolName) fail("RPC tool update identity differs from its start (active-tool-mismatch)");
    if (!emptyTaskUpdate) {
      const argumentsSha256 = fingerprint(args, this.#limits, "RPC tool update arguments", (value) => this.#digest(value));
      if (argumentsSha256 !== active.argumentsSha256) fail("RPC tool update arguments differ from its start");
    }
    assertJsonValue(partialResult, this.#limits, "RPC tool partial result");
  }

  end({ toolCallId, toolName, result, isError = false }) {
    const active = this.#active.get(toolCallId);
    if (!active || active.toolName !== toolName || typeof isError !== "boolean") fail("RPC tool end identity differs from its start");
    const resultSha256 = fingerprint(result, this.#limits, "RPC tool result", (value) => this.#digest(value));
    this.#active.delete(toolCallId);
    if (toolName === "task") this.#completedAsyncTasks.set(toolCallId, active.argumentsSha256);
    const outcomeFingerprintSha256 = this.#digest(canonical({
      callFingerprintSha256: active.callFingerprintSha256,
      isError,
      resultSha256,
    }));
    const event = {
      sequence: this.#completedCount,
      outcomeFingerprintSha256,
      failureFingerprintSha256: isError ? outcomeFingerprintSha256 : null,
      previousEventSha256: this.#eventHeadSha256,
    };
    const eventRecordSha256 = this.#digest(canonical(event));
    this.#eventHeadSha256 = eventRecordSha256;
    this.#completedCount += 1;
    this.#recent.push(Object.freeze({ ...event, eventRecordSha256 }));
    if (this.#recent.length > this.#historyLimit) this.#recent.shift();

    const fingerprints = this.#recent.map((entry) => entry.outcomeFingerprintSha256);
    const repeatedOutcome = repeatedAtTail(fingerprints.slice(0, -1), outcomeFingerprintSha256);
    const failureFingerprints = this.#recent.map((entry) => entry.failureFingerprintSha256);
    const repeatedFailure = isError ? repeatedAtTail(failureFingerprints.slice(0, -1), outcomeFingerprintSha256) : 0;
    if (repeatedFailure >= this.#limits.maxRepeatedEquivalentFailure) this.#stoppedReason = "repeated-equivalent-failure";
    else if (repeatedOutcome >= this.#limits.maxRepeatedEquivalentOutcome) this.#stoppedReason = "repeated-equivalent-outcome";
    else if (oscillates(fingerprints, this.#limits.oscillationWindow)) this.#stoppedReason = "equivalent-outcome-oscillation";
    return this.receipt();
  }

  receipt() {
    return Object.freeze({
      schemaVersion: 1,
      policy: RECEIPT_POLICY,
      status: this.#stoppedReason === null ? "within-envelope" : "stopped",
      reason: this.#stoppedReason,
      completedToolCalls: this.#completedCount,
      eventHeadSha256: this.#eventHeadSha256,
    });
  }
}

export function validateRpcLoopGuardReceipt(value, expectedCompletedToolCalls) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return ["receipt must be an object"];
  const errors = [];
  const keys = ["completedToolCalls", "eventHeadSha256", "policy", "reason", "schemaVersion", "status"];
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(keys)) errors.push("receipt shape is invalid");
  if (value.schemaVersion !== 1 || value.policy !== RECEIPT_POLICY) errors.push("receipt policy is invalid");
  if (!Number.isSafeInteger(value.completedToolCalls) || value.completedToolCalls < 0 || value.completedToolCalls !== expectedCompletedToolCalls) errors.push("completed tool-call count is invalid");
  if (value.completedToolCalls === 0 ? value.eventHeadSha256 !== null : !SHA_RE.test(value.eventHeadSha256 ?? "")) errors.push("event head is invalid");
  if (value.status !== "within-envelope" || value.reason !== null) errors.push("successful receipt is not within its envelope");
  return errors;
}

export const rpcLoopGuardLimits = DEFAULT_LIMITS;
