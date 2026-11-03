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
// Portal's current hook first applies withPixelCronJobAdmission: a new detached
// agent job must explicitly declare its future tools and delivery. This legacy
// delivery default still applies to its existing callers. For private one-offs,
// also default deleteAfterRun to false: Portal needs the job to look up its
// run history after the chat closes. Explicit deletion is respected.
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
const FLAT_PAYLOAD_FIELDS = Object.freeze(['message','text','model','fallbacks','toolsAllow',
  'thinking','timeoutSeconds','lightContext','allowUnsafeExternalContent']);
const CRON_POLICY_GUIDE = 'Declare the minimal future tools explicitly in payload.toolsAllow; [] disables all tools. '
  + 'Set delivery: {mode: "none"} to keep the result in Portal. Announce delivery needs an explicit channel '
  + 'and recipient; webhook delivery needs an explicit destination. Keep the execution instructions and '
  + 'owner restrictions in payload.message. No scheduled task was created by this call.';

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

// Read only the pinned tool's structural payload-kind selection so other native
// payload types keep their original path. This mirrors its data/job precedence,
// padded known keys, namePayload alias and typed flat-field precedence; it does
// not normalize or replace the submitted job. Agent jobs still require the
// unambiguous canonical fields below, then the actual native normalizer runs.
function nativeCronPayloadView(host) {
  const supplied=host.job;
  const recover=!supplied || isPlainObject(supplied) && Object.keys(supplied).length===0;
  const outer=recover?host:supplied;
  if (!isPlainObject(outer)) return {};
  const source=recover?outer:isPlainObject(outer.data)?outer.data:isPlainObject(outer.job)?outer.job:outer;
  const value={...source};
  const known=[...JOB_FIELDS,...FLAT_PAYLOAD_FIELDS,'kind','namePayload','scheduleKind','sessionTargetName'];
  for (const key of Object.keys(value)) if (key!==key.trim() && known.includes(key.trim()) && !hasOwn(value,key.trim()))
    value[key.trim()]=value[key];
  const payload={...(isPlainObject(value.payload)?value.payload:isPlainObject(value.namePayload)?value.namePayload:{})};
  for (const key of FLAT_PAYLOAD_FIELDS) if (value[key]!==undefined) payload[key]=value[key];
  const recognized=kind=>kind==='agentTurn' || kind==='systemEvent';
  if (!recognized(payload.kind) && recognized(value.kind)) payload.kind=value.kind;
  const nonblank=entry=>typeof entry==='string' && entry.trim().length>0;
  const stringsOrNull=entry=>entry===null || Array.isArray(entry) && entry.every(item=>typeof item==='string');
  if (!recognized(payload.kind)) {
    if (nonblank(payload.message) || nonblank(payload.model) || payload.model===null || nonblank(payload.thinking)
      || typeof payload.timeoutSeconds==='number' || typeof payload.lightContext==='boolean'
      || typeof payload.allowUnsafeExternalContent==='boolean'
      || payload.fallbacks!==undefined && stringsOrNull(payload.fallbacks)
      || payload.toolsAllow!==undefined && stringsOrNull(payload.toolsAllow)) payload.kind='agentTurn';
    else if (nonblank(payload.text)) payload.kind='systemEvent';
  }
  return {kind:payload.kind,sessionTarget:value.sessionTarget};
}

// The pinned cron tool repairs/merges flat fields before applying two unsafe
// defaults for this Portal path: announce delivery and all creator tools.
// Require an unambiguous canonical agentTurn job BEFORE that normalization.
// This reads structure only: no owner-text classification or payload rewriting.
export function withPixelCronJobAdmission(guardResult,event,context,configuredPixelId) {
  if (context?.agentId !== configuredPixelId || guardResult?.block === true) return guardResult;
  const toolName = context?.toolName ?? event?.toolName;
  const base = isPlainObject(guardResult) && guardResult.params !== undefined ? guardResult.params : event?.params;
  if (!isPlainObject(base)) return guardResult;
  const wrapped = toolName === WRAPPER_TOOL_NAME && CRON_TOOL_NAMES.has(base.id);
  if (!CRON_TOOL_NAMES.has(toolName) && !wrapped) return guardResult;
  const host = wrapped ? base.args : base;
  if (!isPlainObject(host) || typeof host.action !== 'string' || host.action.trim() !== 'add') return guardResult;
  const refuse = field => ({block:true,blockReason:`Portal scheduled task needs ${field}. ${CRON_POLICY_GUIDE}`});
  const nativeView=nativeCronPayloadView(host);
  // Other native payload types and main-session validation are unchanged.
  if (nativeView.kind==='systemEvent' || typeof nativeView.kind==='string' && nativeView.kind.trim().toLowerCase()==='command'
      || typeof nativeView.sessionTarget==='string' && nativeView.sessionTarget.trim().toLowerCase()==='main') return guardResult;
  const nested = hasOwn(host,'job');
  const job = nested ? host.job : host;
  if (!isPlainObject(job)) return refuse('one canonical job object with explicit payload and delivery fields');
  // Native recovery also accepts inner data/job wrappers, namePayload and
  // padded keys. Do not guess a future policy across these competing shapes.
  if (['data','job','namePayload'].some(key=>hasOwn(job,key)) || Object.keys(job).some(key=>
    key !== key.trim() && [...JOB_FIELDS,...FLAT_PAYLOAD_FIELDS,'kind'].includes(key.trim())))
    return refuse('one canonical job object with explicit payload and delivery fields');
  const payload = isPlainObject(job.payload) ? job.payload : undefined;
  if (!payload || payload.kind !== 'agentTurn') return refuse('payload.kind: "agentTurn" in the canonical job payload');
  if (FLAT_PAYLOAD_FIELDS.some(key=>hasOwn(job,key)) || hasOwn(job,'kind') ||
      nested && (FLAT_PAYLOAD_FIELDS.some(key=>hasOwn(host,key)) || JOB_FIELDS.some(key=>hasOwn(host,key)
        && (!hasOwn(job,key) || !deeplyEqual(host[key],job[key])))))
    return refuse('unambiguous job fields; put future-tool and instruction fields only inside payload');
  if (!hasOwn(payload,'toolsAllow') || !Array.isArray(payload.toolsAllow) ||
      !payload.toolsAllow.every(name=>typeof name === 'string' && name.trim().length > 0))
    return refuse('an explicit payload.toolsAllow array');
  const delivery = job.delivery;
  if (!hasOwn(job,'delivery') || !isPlainObject(delivery) || !hasOwn(delivery,'mode') || !['none','announce','webhook'].includes(delivery.mode))
    return refuse('an explicit normalized delivery.mode (none, announce or webhook)');
  const nonblank = value => typeof value === 'string' && value.trim().length > 0;
  if (delivery.mode === 'announce' && (!nonblank(delivery.channel) || delivery.channel.trim().toLowerCase() === 'last' || !nonblank(delivery.to)))
    return refuse('delivery.channel and delivery.to for an explicit announce destination');
  if (delivery.mode === 'webhook' && !nonblank(delivery.to)) return refuse('delivery.to for an explicit webhook destination');
  return guardResult; // Native schemas, channel validation and creator-cap intersection still run.
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
  const defaultDelivery = !hasOwn(target, "delivery") && !hasOwn(host, "delivery");
  const localDelivery = defaultDelivery || resolveField(target, host, "delivery")?.mode === "none";
  const schedule = resolveField(target, host, "schedule");
  const retainOneOff = localDelivery && typeof schedule?.kind === "string" &&
    schedule.kind.trim().toLowerCase() === "at" &&
    !hasOwn(target, "deleteAfterRun") && !hasOwn(host, "deleteAfterRun");
  if (!defaultDelivery && !retainOneOff) return undefined;

  const nextTarget = { ...target };
  if (defaultDelivery) nextTarget.delivery = CRON_DELIVERY_NONE;
  if (retainOneOff) nextTarget.deleteAfterRun = false;
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
