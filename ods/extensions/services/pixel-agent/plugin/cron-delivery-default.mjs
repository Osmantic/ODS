// Pixel-only cron delivery default.
//
// OpenClaw 2026.6.33 normalizeCronJobCreate defaults an omitted `delivery` on
// background agentTurn cron jobs to `announce`, which fails with channel-required
// errors in ODS native chat. This helper gives the Pixel agent an ODS-safe
// default of `{ mode: "none" }` for exactly that shape, composing AFTER the
// tool loop guard so guard block results are preserved exactly.
//
// Pure and copy-on-write: inputs are never mutated; a no-op returns the guard
// result with its original identity. Explicit `delivery` (any value, including
// malformed/null) is left untouched so the original validator decides.
//
// Documented limitations (initial revision):
// - The flat message shorthand (`message` without an explicit `payload`
//   object) is left unchanged because matching the canonical SDK shape for it
//   cannot be done safely here; it is never silently guessed.
// - An omitted `sessionTarget` with an explicit `payload.kind: "agentTurn"`
//   is treated as isolated because that is the canonical SDK default
//   (normalizeCronJobCreate), i.e. the exact bug shape; an explicit
//   main sessionTarget is always respected. Current and explicit session targets
//   also use detached delivery in the pinned SDK.

const CRON_DELIVERY_NONE = Object.freeze({ mode: "none" });
const CRON_TOOL_NAMES = Object.freeze(new Set(["cron", "openclaw:core:cron"]));
const WRAPPER_TOOL_NAME = "tool_call";
// Job-related fields whose flat-vs-nested disagreement makes a call ambiguous.
const JOB_FIELDS = Object.freeze([
  "name",
  "description",
  "schedule",
  "payload",
  "sessionTarget",
  "delivery",
  "agentId",
  "enabled",
  "deleteAfterRun",
  "wakeMode",
  "failureAlert",
  "sessionKey",
  "contextMessages",
]);

function isPlainObject(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stableValue(value) {
  if (Array.isArray(value)) return value.map(stableValue);
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stableValue(value[key])]),
    );
  }
  return value;
}

function deeplyEqual(a, b) {
  return JSON.stringify(stableValue(a)) === JSON.stringify(stableValue(b));
}

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key);
}

// Resolves a field that may live on the nested job, the flat args, or both.
// Returns undefined when the two explicit values disagree (ambiguous shape).
function resolveField(target, host, key) {
  const nested = hasOwn(target, key) ? target[key] : undefined;
  const flat = hasOwn(host, key) ? host[key] : undefined;
  if (hasOwn(target, key) && hasOwn(host, key) && !deeplyEqual(nested, flat)) {
    return undefined;
  }
  return hasOwn(target, key) ? nested : flat;
}

// Returns the replacement params object, or undefined when no change applies.
function cronDeliveryDefaultFor(base, toolName, configuredPixelId) {
  if (!isPlainObject(base)) return undefined;
  const direct = toolName === "cron" || toolName === "openclaw:core:cron";
  const wrapped =
    toolName === WRAPPER_TOOL_NAME &&
    typeof base.id === "string" &&
    CRON_TOOL_NAMES.has(base.id);
  if (!direct && !wrapped) return undefined;
  // The wrapped dispatcher form requires an args object; anything else is left
  // for the original validator.
  if (wrapped && !isPlainObject(base.args)) return undefined;

  const host = wrapped ? base.args : base;

  const hasNested = hasOwn(host, "job");
  if (hasNested && !isPlainObject(host.job)) return undefined;
  if (hasNested) {
    const flatJobFields = JOB_FIELDS.filter((field) => hasOwn(host, field));
    const conflicts = flatJobFields.some(
      (field) =>
        hasOwn(host.job, field) && !deeplyEqual(host.job[field], host[field]),
    );
    if (conflicts) return undefined;
  }
  const target = hasNested ? host.job : host;

  if (host.action !== "add") return undefined;
  const sessionTarget = resolveField(target, host, "sessionTarget");
  if (sessionTarget === null) return undefined;
  const backgroundTarget = sessionTarget === undefined ||
    sessionTarget === "isolated" || sessionTarget === "current" ||
    (typeof sessionTarget === "string" && sessionTarget.startsWith("session:") &&
      sessionTarget.slice("session:".length).trim().length > 0);
  if (!backgroundTarget) {
    return undefined;
  }
  const payload = resolveField(target, host, "payload");
  if (!isPlainObject(payload) || payload.kind !== "agentTurn") return undefined;
  const agentId = resolveField(target, host, "agentId");
  if (agentId !== undefined && agentId !== configuredPixelId) return undefined;
  if (hasOwn(target, "delivery") || hasOwn(host, "delivery")) return undefined;

  const nextTarget = { ...target, delivery: CRON_DELIVERY_NONE };
  if (!hasNested) return wrapped ? { ...base, args: nextTarget } : nextTarget;
  const nextHost = { ...host, job: nextTarget };
  return wrapped ? { ...base, args: nextHost } : nextHost;
}

// Composition entry used by the plugin's before_tool_call hook. Accepts the
// guard result so block results are returned untouched (same identity) and
// guard-provided params become the effective base for the default.
export function withPixelCronDeliveryDefault(
  guardResult,
  event,
  context,
  configuredPixelId,
) {
  if (context?.agentId !== configuredPixelId) return guardResult;
  if (isPlainObject(guardResult) && guardResult.block === true) {
    return guardResult;
  }
  const toolName = context?.toolName ?? event?.toolName;
  const base =
    isPlainObject(guardResult) && guardResult.params !== undefined
      ? guardResult.params
      : event?.params;
  const next = cronDeliveryDefaultFor(base, toolName, configuredPixelId);
  if (next === undefined) return guardResult;
  if (!isPlainObject(guardResult)) return { params: next };
  return { ...guardResult, params: next };
}
