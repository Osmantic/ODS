import { createHash, randomBytes } from "node:crypto";

import {
  canonical,
  validatePlanLease,
  validateWorkCapabilityControllerPolicy,
  validateWorkCapabilityControllerPolicyV2,
  validateWorkCapabilityJobAuthorization,
  validateWorkCapabilityOperationalGrantV2,
  validateWorkCapabilityOperationalRuntimeRequestV2,
  validateWorkCapabilityPack,
  validateWorkCapabilityPackV2,
  validateWorkCapabilitySshApprovalV2,
  validateWorkCapabilityToolRequest,
  validateWorkCheckpoint,
  validateWorkConsumption,
} from "../../scripts/lib/work-contract.mjs";
import { validateJsonSchema } from "../../scripts/lib/json-schema.mjs";
import { capabilityPackSha256, issueCapabilityGrant } from "./capability-packs.mjs";
import { assessWatchdog } from "./watchdog.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const MAX_REQUEST_AGE_MS = 120000;
const CLOCK_SKEW_MS = 30000;
const limitFields = Object.freeze(["maxInputBytes", "maxOutputBytes", "maxRuntimeMs", "maxCalls", "maxMemoryMiB", "maxCpuCores", "maxPids", "maxWorkspaceBytes"]);
const authority = Object.freeze({ grantsToolCall: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const requestAuthority = Object.freeze({ directExecution: false, credentials: false, network: false, externalEffects: false, scopeExpansion: false, completion: false });
const authorizationBoundary = "Exact job, consumed lease, running checkpoint, signed pack, tool, classification, resource, and watchdog envelope. It grants no call by itself; each call still requires a fresh single-use grant and continue decision.";
const resultBoundary = "Transient controller result for one checkpoint event. Only a continue result carries one exact single-use runtime request; neither result grants scope expansion, external effects, or completion.";

export class CapabilityControllerError extends Error {}

function fail(message) { throw new CapabilityControllerError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} is invalid: ${errors[0]}`); }
function timestamp(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`);
  return value;
}
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function exactPack(pack) {
  return { id: pack.id, version: pack.version, packSha256: capabilityPackSha256(pack), treeSha256: pack.provenance.treeSha256 };
}
function checkedInput(value, maxBytes) {
  const seen = new WeakSet(); let nodes = 0;
  function visit(current, depth) {
    nodes += 1;
    if (nodes > 100000 || depth > 64) fail("capability request arguments exceed structural limits");
    if (current === null || typeof current === "string" || typeof current === "boolean") return;
    if (typeof current === "number") { if (!Number.isFinite(current)) fail("capability request arguments are not finite JSON"); return; }
    if (typeof current !== "object") fail("capability request arguments are not JSON");
    if (seen.has(current)) fail("capability request arguments contain a cycle");
    seen.add(current);
    if (!Array.isArray(current) && ![Object.prototype, null].includes(Object.getPrototypeOf(current))) fail("capability request arguments have a non-JSON prototype");
    for (const child of Array.isArray(current) ? current : Object.values(current)) visit(child, depth + 1);
    seen.delete(current);
  }
  visit(value, 0);
  const encoded = canonical(value), bytes = Buffer.byteLength(encoded);
  if (bytes > maxBytes) fail("capability request arguments exceed their byte ceiling");
  return bytes;
}
function assertPlanCustody({ plan, lease, consumption, checkpoint }) {
  schema("capability plan/lease", validatePlanLease(plan, lease));
  schema("capability lease consumption", validateWorkConsumption(consumption));
  schema("capability checkpoint", validateWorkCheckpoint(checkpoint));
  const planSha256 = sha(plan), leaseSha256 = sha(lease), consumptionSha256 = sha(consumption), checkpointSha256 = sha(checkpoint);
  if (
    consumption.jobId !== plan.jobId || consumption.leaseId !== lease.leaseId
    || consumption.planSha256 !== planSha256 || consumption.leaseSha256 !== leaseSha256
    || consumption.policySha256 !== plan.policySha256 || consumption.inputSetSha256 !== plan.inputSetSha256
    || canonical(consumption.executor) !== canonical(plan.executor) || canonical(consumption.model) !== canonical(plan.model)
    || consumption.runnerImageDigest !== plan.isolation.runnerImageDigest
  ) fail("capability lease consumption differs from the exact plan and lease");
  if (
    checkpoint.jobId !== plan.jobId || checkpoint.planSha256 !== planSha256
    || checkpoint.inputSetSha256 !== plan.inputSetSha256 || checkpoint.objectiveSha256 !== sha(plan.objective)
    || checkpoint.acceptanceCriteriaSha256 !== sha(plan.acceptanceCriteria)
    || checkpoint.state !== "running" || checkpoint.iteration !== lease.iteration
    || checkpoint.workerSessionSha256 !== consumptionSha256
  ) fail("capability checkpoint is not the exact running checkpoint for the consumed lease");
  if (checkpoint.authorityExpansionObserved || checkpoint.acceptanceCriteriaMutationObserved || checkpoint.externalEffectsObserved) fail("capability checkpoint records a safety incident");
  return { planSha256, leaseSha256, consumptionSha256, checkpointSha256 };
}
function assertPack(pack, expectedPackSha256) {
  schema("capability pack", validateWorkCapabilityPack(pack));
  if (!SHA_RE.test(expectedPackSha256 ?? "") || expectedPackSha256 !== capabilityPackSha256(pack)) fail("capability pack differs from its trusted signed-tree binding");
  return exactPack(pack);
}
function policyPack(policy, binding) {
  const found = policy.packs.find((candidate) => candidate.id === binding.id && candidate.version === binding.version);
  if (!found || found.packSha256 !== binding.packSha256 || found.treeSha256 !== binding.treeSha256) fail("capability pack is not admitted by the exact controller policy");
  return found;
}
function assertLimits(limits, ceilings, pack, plan) {
  exactKeys(limits, limitFields, "capability limits");
  for (const field of limitFields) if (limits[field] > ceilings[field] || limits[field] > pack.limits[field]) fail(`capability request widens ${field}`);
  if (limits.maxCalls !== 1) fail("capability sessions must remain single-call");
  if (limits.maxRuntimeMs > plan.budgets.maxRuntimeSeconds * 1000) fail("capability runtime exceeds the job runtime budget");
  if (limits.maxMemoryMiB > plan.budgets.maxMemoryMiB || limits.maxCpuCores > plan.budgets.maxCpuCores) fail("capability resources exceed the job compute budget");
  if (limits.maxWorkspaceBytes > plan.budgets.maxDiskBytes || limits.maxOutputBytes > plan.budgets.maxArtifactBytes) fail("capability storage exceeds the job budget");
  if (pack.adapter.workspace === "none" && limits.maxWorkspaceBytes !== 0) fail("capability limits add an undeclared workspace");
}
function assertAggregateSessionBudget(maxSessions, limits, plan) {
  const retainedBytes = maxSessions * (limits.maxInputBytes + 2 * limits.maxOutputBytes + 131072);
  if (maxSessions * limits.maxRuntimeMs > plan.budgets.maxRuntimeSeconds * 1000) fail("capability session envelope exceeds the aggregate job runtime budget");
  if (maxSessions * limits.maxOutputBytes > plan.budgets.maxArtifactBytes || retainedBytes > plan.budgets.maxDiskBytes) fail("capability session envelope exceeds aggregate retained storage budgets");
}
function assertToolEnvelope(plan, pack, admitted, selected) {
  if (!selected.length) fail("capability selection has no tools");
  const packTools = new Map(pack.tools.map((tool) => [tool.name, tool]));
  const admittedTools = new Map(admitted.tools.map((tool) => [tool.name, tool]));
  for (const tool of selected) {
    const declared = packTools.get(tool.name), policy = admittedTools.get(tool.name);
    if (!declared || !policy || tool.effectClass !== declared.effectClass || tool.effectClass !== policy.effectClass) fail(`capability tool ${tool.name ?? "unknown"} differs from its signed policy envelope`);
    if (tool.effectClass === "read-only" && !plan.grantedCapabilities.tools.includes("read")) fail("read-only capability lacks job read authority");
    if (tool.effectClass === "workspace" && (plan.isolation.workspaceMount !== "disposable-read-write" || !plan.grantedCapabilities.tools.some((name) => ["write", "edit", "bash"].includes(name)))) fail("workspace capability lacks disposable job write authority");
  }
}

export function compileCapabilityJobAuthorization({ plan, lease, consumption, checkpoint, pack, expectedPackSha256, policy, selection, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const bindings = assertPlanCustody({ plan, lease, consumption, checkpoint }), packBinding = assertPack(pack, expectedPackSha256);
  schema("capability controller policy", validateWorkCapabilityControllerPolicy(policy));
  exactKeys(selection, ["tools", "dataClassification", "maxSessions", "grantLifetimeMs", "limits"], "capability selection");
  if (!SUFFIX_RE.test(suffix)) fail("capability authorization identity is invalid");
  const authorizedAt = timestamp(now, "capability authorization time"), at = authorizedAt.getTime();
  if (at < Date.parse(policy.createdAt) || at >= Date.parse(policy.expiresAt) || at < Date.parse(consumption.claimedAt) || at < Date.parse(checkpoint.createdAt) || at >= Date.parse(lease.expiresAt)) fail("capability authorization is outside its policy, checkpoint, or consumed lease lifetime");
  if (!policy.profiles.includes(plan.profile)) fail("capability policy does not admit this job profile");
  const admitted = policyPack(policy, packBinding);
  if (selection.dataClassification !== plan.dataClassification || !pack.data.acceptedClassifications.includes(selection.dataClassification) || !admitted.classifications.includes(selection.dataClassification)) fail("capability classification differs from the exact job and pack policy");
  if (!Array.isArray(selection.tools) || new Set(selection.tools.map((tool) => tool?.name)).size !== selection.tools.length || canonical(selection.tools.map((tool) => tool.name)) !== canonical(selection.tools.map((tool) => tool.name).sort())) fail("capability tools must be uniquely sorted");
  assertToolEnvelope(plan, pack, admitted, selection.tools);
  if (!Number.isSafeInteger(selection.maxSessions) || selection.maxSessions < 1 || selection.maxSessions > policy.limits.maxSessionsPerLease || selection.maxSessions > lease.budgets.maxToolCalls) fail("capability session budget widens the job or controller policy");
  if (policy.watchdog.maxFailures > lease.budgets.maxFailures) fail("capability watchdog widens the lease failure budget");
  if (!Number.isSafeInteger(selection.grantLifetimeMs) || selection.grantLifetimeMs < 100 || selection.grantLifetimeMs > policy.limits.maxGrantLifetimeMs || selection.grantLifetimeMs > pack.limits.maxRuntimeMs + 60000) fail("capability grant lifetime widens policy");
  assertLimits(selection.limits, policy.limits, pack, plan);
  assertAggregateSessionBudget(selection.maxSessions, selection.limits, plan);
  const expiresAt = new Date(Math.min(Date.parse(policy.expiresAt), Date.parse(lease.expiresAt)));
  if (expiresAt.getTime() - at < 100) fail("capability authorization has no usable grant lifetime");
  const authorization = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-job-authorization-v1.schema.json", schemaVersion: 1,
    authorizationId: `workcapauth-${String(at).padStart(13, "0")}-${suffix}`, jobId: plan.jobId,
    authorizedAt: authorizedAt.toISOString(), expiresAt: expiresAt.toISOString(),
    ...bindings, controllerPolicySha256: sha(policy), pack: packBinding,
    tools: selection.tools.map((tool) => ({ ...tool })), dataClassification: selection.dataClassification,
    maxSessions: selection.maxSessions, grantLifetimeMs: selection.grantLifetimeMs, limits: { ...selection.limits },
    watchdog: { maxToolCalls: selection.maxSessions, ...policy.watchdog }, authority: { ...authority }, boundary: authorizationBoundary,
  };
  schema("capability job authorization", validateWorkCapabilityJobAuthorization(authorization));
  return Object.freeze(authorization);
}

function assertAuthorization({ authorization, plan, lease, consumption, checkpoint, pack, expectedPackSha256, policy }) {
  schema("capability job authorization", validateWorkCapabilityJobAuthorization(authorization));
  const bindings = assertPlanCustody({ plan, lease, consumption, checkpoint }), packBinding = assertPack(pack, expectedPackSha256);
  schema("capability controller policy", validateWorkCapabilityControllerPolicy(policy));
  if (
    authorization.jobId !== plan.jobId || authorization.planSha256 !== bindings.planSha256 || authorization.leaseSha256 !== bindings.leaseSha256
    || authorization.consumptionSha256 !== bindings.consumptionSha256 || authorization.checkpointSha256 !== bindings.checkpointSha256
    || authorization.controllerPolicySha256 !== sha(policy) || canonical(authorization.pack) !== canonical(packBinding)
  ) fail("capability job authorization differs from current immutable custody");
  const admitted = policyPack(policy, packBinding);
  assertToolEnvelope(plan, pack, admitted, authorization.tools);
  assertLimits(authorization.limits, policy.limits, pack, plan);
  const authorizedAt = Date.parse(authorization.authorizedAt), expiresAt = Date.parse(authorization.expiresAt);
  if (
    authorizedAt < Date.parse(policy.createdAt) || authorizedAt < Date.parse(consumption.claimedAt) || authorizedAt < Date.parse(checkpoint.createdAt)
    || expiresAt > Date.parse(policy.expiresAt) || expiresAt > Date.parse(lease.expiresAt)
  ) fail("capability job authorization widens its policy or consumed lease lifetime");
  if (
    !policy.profiles.includes(plan.profile) || authorization.dataClassification !== plan.dataClassification
    || !pack.data.acceptedClassifications.includes(authorization.dataClassification) || !admitted.classifications.includes(authorization.dataClassification)
  ) fail("capability job authorization widens its profile or data classification");
  if (authorization.maxSessions > policy.limits.maxSessionsPerLease || authorization.maxSessions > lease.budgets.maxToolCalls) fail("capability job authorization widens its session budget");
  if (authorization.grantLifetimeMs > policy.limits.maxGrantLifetimeMs || authorization.grantLifetimeMs > pack.limits.maxRuntimeMs + 60000) fail("capability job authorization widens its grant lifetime");
  for (const field of ["maxFailures", "maxRepeatedEquivalent", "maxRepeatedFailure", "maxEventsWithoutVerifiedProgress"]) if (authorization.watchdog[field] > policy.watchdog[field]) fail(`capability job authorization widens watchdog ${field}`);
  if (authorization.watchdog.maxFailures > lease.budgets.maxFailures) fail("capability job authorization widens the lease failure budget");
  assertAggregateSessionBudget(authorization.maxSessions, authorization.limits, plan);
  return { bindings, packBinding };
}

export function authorizeCapabilityToolRequest({ authorization, request, plan, lease, consumption, checkpoint, pack, expectedPackSha256, policy, events, now = new Date(), grantSuffix = randomBytes(6).toString("hex"), watchdogSuffix = randomBytes(6).toString("hex") }) {
  const { bindings, packBinding } = assertAuthorization({ authorization, plan, lease, consumption, checkpoint, pack, expectedPackSha256, policy });
  schema("capability tool request", validateWorkCapabilityToolRequest(request));
  if (!SUFFIX_RE.test(grantSuffix) || !SUFFIX_RE.test(watchdogSuffix)) fail("capability decision identity is invalid");
  const decidedAt = timestamp(now, "capability decision time"), at = decidedAt.getTime(), requestedAt = Date.parse(request.createdAt);
  if (at < Date.parse(authorization.authorizedAt) || at >= Date.parse(authorization.expiresAt) || requestedAt < Date.parse(authorization.authorizedAt) || requestedAt >= Date.parse(authorization.expiresAt) || requestedAt - at > CLOCK_SKEW_MS || at - requestedAt > MAX_REQUEST_AGE_MS) fail("capability request is stale or outside its authorization lifetime");
  if (
    request.jobId !== plan.jobId || request.authorizationSha256 !== sha(authorization)
    || request.planSha256 !== bindings.planSha256 || request.leaseSha256 !== bindings.leaseSha256
    || request.checkpointSha256 !== bindings.checkpointSha256 || canonical(request.pack) !== canonical(packBinding)
    || request.dataClassification !== authorization.dataClassification
  ) fail("capability request differs from its exact job authorization");
  const authorizedTool = authorization.tools.find((tool) => tool.name === request.tool), declaredTool = pack.tools.find((tool) => tool.name === request.tool);
  if (!authorizedTool || !declaredTool || request.effectClass !== authorizedTool.effectClass || request.effectClass !== declaredTool.effectClass) fail("capability request tool or effect is not authorized");
  assertLimits(request.limits, authorization.limits, pack, plan);
  const inputBytes = checkedInput(request.arguments, request.limits.maxInputBytes);
  if (validateJsonSchema(request.arguments, declaredTool.inputSchema).length) fail("capability request arguments fail the signed tool schema");
  const tool = `mcp.${pack.id}.${sha(request.tool).slice(0, 32)}`;
  const preflightDecision = assessWatchdog({
    jobId: plan.jobId, checkpointSha256: bindings.checkpointSha256,
    expectedEventHeadSha256: request.expectedEventHeadSha256, events,
    proposal: { tool, arguments: request.arguments, effectClass: request.effectClass },
    limits: authorization.watchdog, allowedEffectClasses: [...new Set(authorization.tools.map((entry) => entry.effectClass))],
    now: decidedAt, suffix: watchdogSuffix,
  });
  if (preflightDecision.decision !== "continue") return Object.freeze({ schemaVersion: 1, status: "stopped", grant: null, preflightDecision, runtimeRequest: null, authority: { ...authority }, boundary: resultBoundary });
  const expiry = new Date(Math.min(at + authorization.grantLifetimeMs, Date.parse(authorization.expiresAt)));
  const grant = issueCapabilityGrant(pack, {
    expectedPackSha256, jobId: plan.jobId, checkpointSha256: bindings.checkpointSha256,
    tools: [request.tool], dataClassification: request.dataClassification, limits: request.limits,
    now: decidedAt, expiresAt: expiry, suffix: grantSuffix,
  });
  return Object.freeze({
    schemaVersion: 1, status: "continue", grant, preflightDecision,
    runtimeRequest: { toolName: request.tool, input: structuredClone(request.arguments) },
    authority: { ...authority }, boundary: resultBoundary,
  });
}

export function createCapabilityToolRequest({ authorization, tool, effectClass, arguments: input, limits = authorization?.limits, expectedEventHeadSha256 = null, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  schema("capability job authorization", validateWorkCapabilityJobAuthorization(authorization));
  const createdAt = timestamp(now, "capability request time");
  if (!SUFFIX_RE.test(suffix)) fail("capability request identity is invalid");
  checkedInput(input, limits?.maxInputBytes);
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-request-v1.schema.json", schemaVersion: 1,
    requestId: `workcaprequest-${String(createdAt.getTime()).padStart(13, "0")}-${suffix}`,
    jobId: authorization.jobId, createdAt: createdAt.toISOString(), authorizationSha256: sha(authorization),
    planSha256: authorization.planSha256, leaseSha256: authorization.leaseSha256, checkpointSha256: authorization.checkpointSha256,
    pack: { ...authorization.pack }, tool, effectClass, arguments: structuredClone(input), dataClassification: authorization.dataClassification,
    limits: { ...limits }, expectedEventHeadSha256, authority: { ...requestAuthority },
    boundary: "Private untrusted request for one exact local capability tool call. Arguments remain job-scoped data; this request grants no execution, credential, network, external-effect, scope-expansion, or completion authority.",
  };
  schema("capability tool request", validateWorkCapabilityToolRequest(request));
  return request;
}


const operationalGrantBoundary = "Operational single-use v2 grant for one exact job-workspace file effect. It grants one create-only bounded file inside the exact job workspace and no credential, network, external effect, scope expansion, replay, or completion.";

function v2PolicyPack(policy, packBinding) {
  const found = policy.packs.find((candidate) =>
    candidate.id === packBinding.id && candidate.version === packBinding.version
    && candidate.packSha256 === packBinding.packSha256 && candidate.treeSha256 === packBinding.treeSha256
    && candidate.schemaVersion === 2);
  if (!found) fail("capability v2 policy does not admit this signed pack");
  return found;
}

function v2PackSecurityOk(pack) {
  const security = pack.security;
  return pack.adapter.workspace === "disposable-read-write"
    && security.networkMode === "none" && security.credentialRefs.length === 0
    && security.hostFilesystem === false && security.dockerSocket === false
    && security.sshAgent === false && security.browser === false
    && security.externalEffects === false && security.admissionOnly === true;
}

function narrowInteger(value, ceiling, label) {
  if (!Number.isSafeInteger(value) || value < 1) fail(`capability v2 ${label} is invalid`);
  return ceiling == null ? value : Math.min(value, ceiling);
}

// Compile the one exact operational v2 grant and its matching private runtime
// request. Only this controller path may compile authority for the operational
// workspace slice; the signed v2 pack and v2 controller policy are declarations
// that grant nothing alone. Every custody and signed binding is validated here
// (plan/consumed lease/running checkpoint, exact pack SHA/tree/signing, tool,
// effect, workspace, security, classification, budgets, issue/expiry lifetime).
export function compileCapabilityOperationalV2({ plan, lease, consumption, checkpoint, pack, expectedPackSha256, policy, tool, relativePath, content, now = new Date(), grantSuffix = randomBytes(6).toString("hex"), operationSuffix = randomBytes(8).toString("hex") }) {
  schema("capability v2 controller policy", validateWorkCapabilityControllerPolicyV2(policy));
  schema("capability v2 pack", validateWorkCapabilityPackV2(pack));
  if (!SHA_RE.test(expectedPackSha256 ?? "") || expectedPackSha256 !== capabilityPackSha256(pack)) fail("capability v2 pack differs from its trusted signed-tree binding");
  const bindings = assertPlanCustody({ plan, lease, consumption, checkpoint });
  if (!SUFFIX_RE.test(grantSuffix) || !/^[a-f0-9]{16}$/u.test(operationSuffix)) fail("capability v2 operational identity is invalid");
  const issued = timestamp(now, "capability v2 operational issue time"), at = issued.getTime();
  if (at < Date.parse(policy.createdAt) || at < Date.parse(consumption.claimedAt) || at < Date.parse(checkpoint.createdAt) || at >= Date.parse(lease.expiresAt) || at >= Date.parse(policy.expiresAt)) fail("capability v2 operational grant is outside its policy, checkpoint, or consumed lease lifetime");
  if (!policy.profiles.includes(plan.profile)) fail("capability v2 policy does not admit this job profile");
  const packBinding = { id: pack.id, version: pack.version, packSha256: capabilityPackSha256(pack), treeSha256: pack.provenance.treeSha256, schemaVersion: 2 };
  const admitted = v2PolicyPack(policy, packBinding);
  if (!v2PackSecurityOk(pack)) fail("capability v2 pack is not the bounded workspace slice; network/credential/host/external must all be absent");
  if (!plan.dataClassification || !pack.data.acceptedClassifications.includes(plan.dataClassification) || !admitted.classifications.includes(plan.dataClassification)) fail("capability v2 operational classification is not admitted by the pack or policy");
  const declaredTool = pack.tools.find((entry) => entry.name === tool);
  const admittedTool = admitted.tools.find((entry) => entry.name === tool);
  if (!declaredTool || declaredTool.effectClass !== "workspace" || declaredTool.binding !== null) fail("capability v2 operational tool is not the signed workspace effect");
  if (!admittedTool || admittedTool.effectClass !== "workspace" || admittedTool.bindingSummary.targetClass !== "local-filesystem") fail("capability v2 policy does not admit the workspace tool binding");
  if (typeof relativePath !== "string" || relativePath.length === 0 || relativePath.includes("\0") || relativePath.includes("\\")) fail("capability v2 operational relative path is invalid");
  if (relativePath.split("/").some((segment) => segment === "" || segment === "." || segment === "..")) fail("capability v2 operational relative path escapes the job workspace");
  if (typeof content !== "string") fail("capability v2 operational content is invalid");
  const contentBytes = Buffer.byteLength(content, "utf8");
  if (contentBytes < 1) fail("capability v2 operational content is empty");
  if (contentBytes > pack.budgets.perRun.maxBytes) fail("capability v2 operational content exceeds the signed pack per-run byte budget");
  if (pack.budgets.perRun.maxCalls !== 1) fail("capability v2 operational slice must be single-use per run");
  if (validateJsonSchema({ relativePath, content }, declaredTool.inputSchema).length) fail("capability v2 operational operation fails the signed tool schema");

  const limits = {
    maxInputBytes: narrowInteger(pack.limits.maxInputBytes, policy.limits.maxInputBytes, "input limit"),
    maxOutputBytes: narrowInteger(pack.limits.maxOutputBytes, policy.limits.maxOutputBytes, "output limit"),
    maxRuntimeMs: narrowInteger(pack.limits.maxRuntimeMs, policy.limits.maxRuntimeMs, "runtime limit"),
    maxCalls: 1,
    maxMemoryMiB: narrowInteger(pack.limits.maxMemoryMiB, policy.limits.maxMemoryMiB, "memory limit"),
    maxCpuCores: narrowInteger(pack.limits.maxCpuCores, policy.limits.maxCpuCores, "CPU limit"),
    maxPids: narrowInteger(pack.limits.maxPids, policy.limits.maxPids, "process limit"),
    maxWorkspaceBytes: narrowInteger(pack.limits.maxWorkspaceBytes, policy.limits.maxWorkspaceBytes, "workspace limit"),
  };
  const perRunMaxBytes = Math.min(pack.budgets.perRun.maxBytes, limits.maxWorkspaceBytes);
  const perRun = { maxCalls: 1, maxBytes: perRunMaxBytes, maxDurationMs: Math.min(pack.budgets.perRun.maxDurationMs, limits.maxRuntimeMs) };
  const cumulative = {
    maxCalls: narrowInteger(pack.budgets.cumulative.maxCalls, null, "cumulative call budget"),
    maxBytes: Math.min(pack.budgets.cumulative.maxBytes, limits.maxWorkspaceBytes),
    maxDurationMs: Math.min(pack.budgets.cumulative.maxDurationMs, limits.maxRuntimeMs),
  };
  if (contentBytes > perRunMaxBytes) fail("capability v2 operational content exceeds the compiled per-run workspace byte budget");

  const grantLifetimeMs = Math.min(policy.limits.maxGrantLifetimeMs, pack.limits.maxRuntimeMs + 60000);
  const expiresAt = new Date(Math.min(at + grantLifetimeMs, Date.parse(policy.expiresAt), Date.parse(lease.expiresAt)));
  if (expiresAt.getTime() - at < 100) fail("capability v2 operational grant has no usable lifetime");
  const operationId = `workcapv2-${String(at).padStart(13, "0")}-${operationSuffix}`;
  const grantId = `workcapgrant-${String(at).padStart(13, "0")}-${grantSuffix}`;
  const contentSha256 = sha(content);
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json", schemaVersion: 2,
    grantId, jobId: plan.jobId, issuedAt: issued.toISOString(), expiresAt: expiresAt.toISOString(),
    singleUse: true, checkpointSha256: bindings.checkpointSha256,
    pack: { id: pack.id, version: pack.version, packSha256: capabilityPackSha256(pack), treeSha256: pack.provenance.treeSha256, schemaVersion: 2, signerIdentity: pack.provenance.signerIdentity },
    tool: { name: tool, effectClass: "workspace", targetClass: "local-filesystem" },
    operation: { id: operationId, lane: "filesystem", action: "write", scope: "job-workspace", relativePath },
    contentSha256, dataClassification: plan.dataClassification,
    classification: { input: plan.dataClassification, output: plan.dataClassification },
    limits, budgets: { perRun, cumulative },
    approvalMode: "none", egress: "none", credentialRefs: [],
    idempotencyKey: operationId,
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: operationalGrantBoundary,
  };
  schema("operational v2 grant", validateWorkCapabilityOperationalGrantV2(grant));
  const runtimeRequest = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-request-v2.schema.json", schemaVersion: 2,
    grantId, operationId, lane: "filesystem",
    binding: { action: "write", scope: "job-workspace", relativePath, contentSha256, maxFileBytes: perRunMaxBytes },
    content, trustRoot: "workspace",
  };
  schema("operational v2 runtime request", validateWorkCapabilityOperationalRuntimeRequestV2(runtimeRequest));
  return Object.freeze({ grant: Object.freeze(grant), runtimeRequest: Object.freeze(runtimeRequest) });
}

// Security check for public-retrieval v2 packs: brokered network only,
// Web Courier is the exact and only permitted broker endpoint, no direct egress or credentials,
// no external effects, no host access, no workspace. The pack's own
// tool targetClass and input/output classifications are bound here too.
function v2PackSecurityRetrievalOk(pack, toolName) {
  const security = pack.security;
  const tool = pack.tools.find((entry) => entry.name === toolName);
  return pack.adapter.workspace === "none"
    && security.networkMode === "brokered"
    && canonical(security.networkDestinations) === canonical([])
    && security.credentialRefs.length === 0
    && security.hostFilesystem === false
    && security.dockerSocket === false
    && security.sshAgent === false
    && security.browser === false
    && security.externalEffects === false
    && tool?.binding?.scope?.mode === "public"
    && canonical(tool.binding.scope.endpoints) === canonical(["https://web-courier.local:8080/search"])
    && canonical(tool.binding.scope.destinations) === canonical([])
    && tool.binding.egress?.mode === "none";
}

const SOURCE_ORDER = Object.freeze(["web", "news", "academic", "forum"]);
const DOMAIN_RE = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))+$/u;
const operationalRetrievalGrantBoundary = "Operational single-use v2 grant for one exact public-retrieval operation through the Web Courier queue. It grants one bounded public search/retrieval and no credential, direct network, external effect, scope expansion, replay, or completion.";

// Compile the one exact operational v2 public-retrieval grant and its matching
// private runtime request. This is a SEPARATE compiler from the filesystem slice:
// it uses a different pack security check, different tool effectClass/targetClass,
// and a different grant boundary. It reuses the same signed pack + controller
// policy + plan/consumed-lease/running-checkpoint custody checks.
export function compileCapabilityOperationalV2PublicRetrieval({ plan, lease, consumption, checkpoint, pack, expectedPackSha256, policy, tool, query, sourceTypes, domains, maxResults, maxSourcesToFetch, maxSourceBytes, now = new Date(), grantSuffix = randomBytes(6).toString("hex"), operationSuffix = randomBytes(8).toString("hex") }) {
  schema("capability v2 controller policy", validateWorkCapabilityControllerPolicyV2(policy));
  schema("capability v2 pack", validateWorkCapabilityPackV2(pack));
  if (!SHA_RE.test(expectedPackSha256 ?? "") || expectedPackSha256 !== capabilityPackSha256(pack)) fail("capability v2 pack differs from its trusted signed-tree binding");
  const bindings = assertPlanCustody({ plan, lease, consumption, checkpoint });
  if (!SUFFIX_RE.test(grantSuffix) || !/^[a-f0-9]{16}$/u.test(operationSuffix)) fail("capability v2 operational identity is invalid");
  const issued = timestamp(now, "capability v2 operational issue time"), at = issued.getTime();
  if (at < Date.parse(policy.createdAt) || at < Date.parse(consumption.claimedAt) || at < Date.parse(checkpoint.createdAt) || at >= Date.parse(lease.expiresAt) || at >= Date.parse(policy.expiresAt)) fail("capability v2 operational grant is outside its policy, checkpoint, or consumed lease lifetime");
  if (!policy.profiles.includes(plan.profile)) fail("capability v2 policy does not admit this job profile");
  const packBinding = { id: pack.id, version: pack.version, packSha256: capabilityPackSha256(pack), treeSha256: pack.provenance.treeSha256, schemaVersion: 2 };
  const admitted = v2PolicyPack(policy, packBinding);

  // Public-retrieval security: brokered network, exact Web Courier endpoint, no direct egress/credentials/external effects.
  if (!v2PackSecurityRetrievalOk(pack, tool)) fail("capability v2 pack is not the bounded public-retrieval slice; it must use the exact Web Courier broker endpoint with no direct egress");

  // Public-only classification for this first slice.
  if (plan.dataClassification !== "public") fail("capability v2 public-retrieval is public-only classification for this slice");
  if (!pack.data.acceptedClassifications.includes("public") || !admitted.classifications.includes("public")) fail("capability v2 public-retrieval classification is not admitted by the pack or policy");

  // Tool must be brokered-network effect targeting public-api.
  const declaredTool = pack.tools.find((entry) => entry.name === tool);
  const admittedTool = admitted.tools.find((entry) => entry.name === tool);
  if (!declaredTool || declaredTool.effectClass !== "brokered-network") fail("capability v2 public-retrieval tool is not the signed brokered-network effect");
  if (declaredTool.binding === null) fail("capability v2 public-retrieval tool must have a non-null binding");
  if (declaredTool.binding.targetClass !== "public-api") fail("capability v2 public-retrieval tool binding targetClass must be public-api");
  if (declaredTool.binding.inputClassification !== "public") fail("capability v2 public-retrieval tool binding inputClassification must be public");
  if (declaredTool.binding.outputClassification !== "public") fail("capability v2 public-retrieval tool binding outputClassification must be public");
  if (!admittedTool || admittedTool.effectClass !== "brokered-network" || admittedTool.bindingSummary.targetClass !== "public-api" || admittedTool.bindingSummary.inputClassification !== "public" || admittedTool.bindingSummary.outputClassification !== "public") fail("capability v2 policy does not admit the brokered-network public-api binding with exact signed classifications");

  // Validate retrieval input.
  if (typeof query !== "string" || query.length < 3 || query.length > 500 || Buffer.byteLength(query, "utf8") > 2000) fail("capability v2 public-retrieval query is not bounded text");
  if (query.normalize("NFKC") !== query || query.trim() !== query || /\s{2,}|[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/u.test(query)) fail("capability v2 public-retrieval query must be canonical visible public text");
  if (!Array.isArray(sourceTypes) || sourceTypes.length < 1 || sourceTypes.length > 4 || new Set(sourceTypes).size !== sourceTypes.length) fail("capability v2 public-retrieval sourceTypes must contain one to four unique supported values");
  if (sourceTypes.some((item) => !SOURCE_ORDER.includes(item))) fail("capability v2 public-retrieval sourceTypes contains an unsupported value");
  const sourceTypeCanonical = [...sourceTypes].sort((left, right) => SOURCE_ORDER.indexOf(left) - SOURCE_ORDER.indexOf(right));
  if (JSON.stringify(sourceTypes) !== JSON.stringify(sourceTypeCanonical)) fail("capability v2 public-retrieval sourceTypes must use canonical order");
  if (!Array.isArray(domains) || domains.length > 32 || new Set(domains).size !== domains.length) fail("capability v2 public-retrieval domains must be a unique bounded array");
  if (domains.some((domain) => typeof domain !== "string" || !DOMAIN_RE.test(domain))) fail("capability v2 public-retrieval domains contains a non-canonical public DNS name");
  const domainCanonical = [...domains].sort();
  if (JSON.stringify(domains) !== JSON.stringify(domainCanonical)) fail("capability v2 public-retrieval domains must be sorted");
  if (!Number.isSafeInteger(maxResults) || maxResults < 1 || maxResults > 20) fail("capability v2 public-retrieval maxResults is outside the tool ceiling");
  if (!Number.isSafeInteger(maxSourcesToFetch) || maxSourcesToFetch < 1 || maxSourcesToFetch > 5 || maxSourcesToFetch > maxResults) fail("capability v2 public-retrieval maxSourcesToFetch is invalid");
  if (!Number.isSafeInteger(maxSourceBytes) || maxSourceBytes < 1024 || maxSourceBytes > 262144) fail("capability v2 public-retrieval maxSourceBytes is outside the tool ceiling");

  const limits = {
    maxInputBytes: narrowInteger(pack.limits.maxInputBytes, policy.limits.maxInputBytes, "input limit"),
    maxOutputBytes: narrowInteger(pack.limits.maxOutputBytes, policy.limits.maxOutputBytes, "output limit"),
    maxRuntimeMs: narrowInteger(pack.limits.maxRuntimeMs, policy.limits.maxRuntimeMs, "runtime limit"),
    maxCalls: 1,
    maxMemoryMiB: narrowInteger(pack.limits.maxMemoryMiB, policy.limits.maxMemoryMiB, "memory limit"),
    maxCpuCores: narrowInteger(pack.limits.maxCpuCores, policy.limits.maxCpuCores, "CPU limit"),
    maxPids: narrowInteger(pack.limits.maxPids, policy.limits.maxPids, "process limit"),
    maxWorkspaceBytes: 0,
  };
  const perRunMaxBytes = Math.min(pack.budgets.perRun.maxBytes, policy.limits.maxOutputBytes);
  const perRun = { maxCalls: 1, maxBytes: perRunMaxBytes, maxDurationMs: Math.min(pack.budgets.perRun.maxDurationMs, limits.maxRuntimeMs) };
  const cumulative = {
    maxCalls: narrowInteger(pack.budgets.cumulative.maxCalls, null, "cumulative call budget"),
    maxBytes: Math.min(pack.budgets.cumulative.maxBytes, policy.limits.maxOutputBytes),
    maxDurationMs: Math.min(pack.budgets.cumulative.maxDurationMs, limits.maxRuntimeMs),
  };

  const grantLifetimeMs = Math.min(policy.limits.maxGrantLifetimeMs, pack.limits.maxRuntimeMs + 60000);
  const expiresAt = new Date(Math.min(at + grantLifetimeMs, Date.parse(policy.expiresAt), Date.parse(lease.expiresAt)));
  if (expiresAt.getTime() - at < 100) fail("capability v2 operational grant has no usable lifetime");
  const operationId = `workcapv2-${String(at).padStart(13, "0")}-${operationSuffix}`;
  const grantId = `workcapgrant-${String(at).padStart(13, "0")}-${grantSuffix}`;
  const querySha256 = sha(query);

  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json", schemaVersion: 2,
    grantId, jobId: plan.jobId, issuedAt: issued.toISOString(), expiresAt: expiresAt.toISOString(),
    singleUse: true, checkpointSha256: bindings.checkpointSha256,
    pack: { id: pack.id, version: pack.version, packSha256: capabilityPackSha256(pack), treeSha256: pack.provenance.treeSha256, schemaVersion: 2, signerIdentity: pack.provenance.signerIdentity },
    tool: { name: tool, effectClass: "brokered-network", targetClass: "public-api" },
    operation: { id: operationId, lane: "public-retrieval" },
    dataClassification: "public",
    classification: { input: "public", output: "public" },
    limits, budgets: { perRun, cumulative },
    approvalMode: "none", egress: "public", credentialRefs: [],
    retrievalInput: { querySha256, sourceTypes: sourceTypeCanonical, domains: domainCanonical, maxResults, maxSourcesToFetch, maxSourceBytes },
    idempotencyKey: operationId,
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: operationalRetrievalGrantBoundary,
  };
  schema("operational v2 grant", validateWorkCapabilityOperationalGrantV2(grant));

  const runtimeRequest = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-request-v2.schema.json", schemaVersion: 2,
    grantId, operationId, lane: "public-retrieval",
    retrievalBinding: { querySha256, sourceTypes: sourceTypeCanonical, domains: domainCanonical, maxResults, maxSourcesToFetch, maxSourceBytes },
    query, trustRoot: "courier",
  };
  schema("operational v2 runtime request", validateWorkCapabilityOperationalRuntimeRequestV2(runtimeRequest));
  return Object.freeze({ grant: Object.freeze(grant), runtimeRequest: Object.freeze(runtimeRequest) });
}

const operationalSshHostnameGrantBoundary = "Operational single-use v2 grant binding one SSH forced-command hostname request to a fresh owner approval. The grant alone provides no signature verification, credential access, network access, generic shell, command or destination selection, replay, external effect, scope expansion, or completion authority.";

function v2PackSecuritySshHostnameOk(pack, toolName, approval) {
  const security = pack.security;
  const tool = pack.tools.find((entry) => entry.name === toolName);
  const binding = tool?.binding;
  return pack.adapter.workspace === "none"
    && security.networkMode === "private-allowlist"
    && canonical(security.networkDestinations) === canonical([approval.destinationAlias])
    && canonical(security.credentialRefs) === canonical([approval.credentialRef])
    && security.hostFilesystem === false && security.dockerSocket === false
    && security.sshAgent === false && security.browser === false
    && security.externalEffects === false && security.admissionOnly === true
    && binding?.targetClass === "ssh"
    && binding.scope?.mode === "private-allowlist"
    && canonical(binding.scope.endpoints) === canonical([])
    && canonical(binding.scope.destinations) === canonical([approval.destinationAlias])
    && binding.egress?.mode === "private-allowlist"
    && canonical(binding.egress.destinations) === canonical([approval.destinationAlias])
    && canonical(binding.credentials?.refs) === canonical([approval.credentialRef])
    && binding.approval?.required === true && binding.approval?.mode === "operator-approval"
    && binding.idempotency?.required === true
    && Array.isArray(binding.idempotency.keys) && binding.idempotency.keys.includes("requestId")
    && canonical(binding.neverEgress) === canonical(["requestId"]);
}

// Compile a grant that Binds but does not itself authorize one SSH hostname
// operation. The supplied owner approval and signature are retained in the
// private runtime request; the operational runtime revalidates the detailed
// approval schema and OpenSSH signature against its trusted allowed-signers
// root, atomically consumes the nonce, and only then obtains credential/network
// authority from its separately protected trusted destination config.
export function compileCapabilityOperationalV2SshHostname({ plan, lease, consumption, checkpoint, pack, expectedPackSha256, policy, tool, approval, approvalSignature, now = new Date(), grantSuffix = randomBytes(6).toString("hex"), operationSuffix = randomBytes(8).toString("hex") }) {
  schema("capability v2 controller policy", validateWorkCapabilityControllerPolicyV2(policy));
  schema("capability v2 pack", validateWorkCapabilityPackV2(pack));
  schema("capability v2 SSH approval", validateWorkCapabilitySshApprovalV2(approval));
  if (!SHA_RE.test(expectedPackSha256 ?? "") || expectedPackSha256 !== capabilityPackSha256(pack)) fail("capability v2 pack differs from its trusted signed-tree binding");
  const bindings = assertPlanCustody({ plan, lease, consumption, checkpoint });
  if (!SUFFIX_RE.test(grantSuffix) || !/^[a-f0-9]{16}$/u.test(operationSuffix)) fail("capability v2 operational identity is invalid");
  if (typeof approvalSignature !== "string" || approvalSignature.length < 32 || approvalSignature.length > 65536 || !approvalSignature.startsWith("-----BEGIN SSH SIGNATURE-----") || !approvalSignature.trimEnd().endsWith("-----END SSH SIGNATURE-----")) fail("capability v2 SSH approval signature envelope is invalid");
  const issued = timestamp(now, "capability v2 operational issue time"), at = issued.getTime();
  if (at < Date.parse(policy.createdAt) || at < Date.parse(consumption.claimedAt) || at < Date.parse(checkpoint.createdAt) || at >= Date.parse(lease.expiresAt) || at >= Date.parse(policy.expiresAt)) fail("capability v2 operational grant is outside its policy, checkpoint, or consumed lease lifetime");
  if (at < Date.parse(approval.issuedAt) || at >= Date.parse(approval.expiresAt)) fail("capability v2 SSH approval is not valid at grant issue time");
  if (!policy.profiles.includes(plan.profile)) fail("capability v2 policy does not admit this job profile");
  const packBinding = { id: pack.id, version: pack.version, packSha256: capabilityPackSha256(pack), treeSha256: pack.provenance.treeSha256, schemaVersion: 2 };
  const admitted = v2PolicyPack(policy, packBinding);
  if (!v2PackSecuritySshHostnameOk(pack, tool, approval)) fail("capability v2 pack is not the bounded owner-approved SSH hostname slice");
  if (!plan.dataClassification || !pack.data.acceptedClassifications.includes(plan.dataClassification) || !admitted.classifications.includes(plan.dataClassification)) fail("capability v2 SSH classification is not admitted by the pack or policy");
  const declaredTool = pack.tools.find((entry) => entry.name === tool);
  const admittedTool = admitted.tools.find((entry) => entry.name === tool);
  if (!declaredTool || declaredTool.effectClass !== "read-only" || declaredTool.binding?.targetClass !== "ssh") fail("capability v2 SSH tool is not the signed read-only SSH effect");
  if (!admittedTool || admittedTool.effectClass !== "read-only" || admittedTool.bindingSummary.targetClass !== "ssh" || admittedTool.bindingSummary.inputClassification !== plan.dataClassification || admittedTool.bindingSummary.outputClassification !== plan.dataClassification) fail("capability v2 policy does not admit the SSH hostname binding with exact classifications");
  if (declaredTool.binding.inputClassification !== plan.dataClassification || declaredTool.binding.outputClassification !== plan.dataClassification) fail("capability v2 SSH pack classifications differ from the plan");
  if (validateJsonSchema({ destinationAlias: approval.destinationAlias, commandId: approval.commandId, requestId: approval.requestId }, declaredTool.inputSchema).length) fail("capability v2 SSH request fails the signed tool schema");

  const limits = {
    maxInputBytes: narrowInteger(pack.limits.maxInputBytes, policy.limits.maxInputBytes, "input limit"),
    maxOutputBytes: Math.min(narrowInteger(pack.limits.maxOutputBytes, policy.limits.maxOutputBytes, "output limit"), approval.maxOutputBytes),
    maxRuntimeMs: Math.min(narrowInteger(pack.limits.maxRuntimeMs, policy.limits.maxRuntimeMs, "runtime limit"), approval.timeoutMs),
    maxCalls: 1,
    maxMemoryMiB: narrowInteger(pack.limits.maxMemoryMiB, policy.limits.maxMemoryMiB, "memory limit"),
    maxCpuCores: narrowInteger(pack.limits.maxCpuCores, policy.limits.maxCpuCores, "CPU limit"),
    maxPids: narrowInteger(pack.limits.maxPids, policy.limits.maxPids, "process limit"),
    maxWorkspaceBytes: 0,
  };
  const perRun = { maxCalls: 1, maxBytes: Math.min(pack.budgets.perRun.maxBytes, limits.maxOutputBytes), maxDurationMs: Math.min(pack.budgets.perRun.maxDurationMs, limits.maxRuntimeMs) };
  const cumulative = { maxCalls: 1, maxBytes: perRun.maxBytes, maxDurationMs: perRun.maxDurationMs };
  if (perRun.maxBytes < 1 || perRun.maxDurationMs < 100) fail("capability v2 SSH compiled budget has no usable output or runtime");
  const grantLifetimeMs = Math.min(policy.limits.maxGrantLifetimeMs, pack.limits.maxRuntimeMs + 60000);
  const expiresAt = new Date(Math.min(at + grantLifetimeMs, Date.parse(policy.expiresAt), Date.parse(lease.expiresAt), Date.parse(approval.expiresAt)));
  if (expiresAt.getTime() - at < 100) fail("capability v2 operational grant has no usable lifetime");
  const operationId = `workcapv2-${String(at).padStart(13, "0")}-${operationSuffix}`;
  const grantId = `workcapgrant-${String(at).padStart(13, "0")}-${grantSuffix}`;
  const approvalSha256 = sha(approval);
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-grant-v2.schema.json", schemaVersion: 2,
    grantId, jobId: plan.jobId, issuedAt: issued.toISOString(), expiresAt: expiresAt.toISOString(), singleUse: true,
    checkpointSha256: bindings.checkpointSha256,
    pack: { id: pack.id, version: pack.version, packSha256: capabilityPackSha256(pack), treeSha256: pack.provenance.treeSha256, schemaVersion: 2, signerIdentity: pack.provenance.signerIdentity },
    tool: { name: tool, effectClass: "read-only", targetClass: "ssh" },
    operation: { id: operationId, lane: "ssh-hostname", requestId: approval.requestId, destinationAlias: approval.destinationAlias, commandId: "hostname" },
    approvalSha256, dataClassification: plan.dataClassification,
    classification: { input: plan.dataClassification, output: plan.dataClassification },
    limits, budgets: { perRun, cumulative }, approvalMode: "owner-signed", egress: "private-allowlist",
    credentialRefs: [approval.credentialRef], idempotencyKey: operationId,
    authority: { grantsToolCall: true, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: operationalSshHostnameGrantBoundary,
  };
  schema("operational v2 SSH grant", validateWorkCapabilityOperationalGrantV2(grant));
  const runtimeRequest = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-request-v2.schema.json", schemaVersion: 2,
    grantId, operationId, lane: "ssh-hostname", approval: structuredClone(approval), approvalSignature, trustRoot: "owner-signed-ssh",
  };
  schema("operational v2 SSH runtime request", validateWorkCapabilityOperationalRuntimeRequestV2(runtimeRequest));
  return Object.freeze({ grant: Object.freeze(grant), runtimeRequest: Object.freeze(runtimeRequest) });
}

export const capabilityControllerBoundary = resultBoundary;
