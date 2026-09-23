import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, rename, rm } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";

import {
  canonical, validateWorkCapabilityControllerPolicy, validateWorkCapabilityJobAuthorization,
  validateWorkCapabilityPack, validateWorkCapabilityToolCatalog,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { compileCapabilityJobAuthorization } from "./capability-controller.mjs";
import { loadPassingCapabilityHealth } from "./capability-health.mjs";
import { loadInstalledCapabilityPack } from "./capability-pack-installation.mjs";
import { capabilityPackSha256 } from "./capability-packs.mjs";
import { createCapabilityToolCatalog } from "./capability-tool-catalog.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/u;
const CLAIM_RE = /^workclaim-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const MAX_BUNDLE_BYTES = 8 * 1024 * 1024;
const authority = Object.freeze({ grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const boundary = "Durable content-free custody for one job capability authorization, signed declaration, controller policy, and OMP catalog. It contains no job input or tool arguments/results and grants no call, replay, scope expansion, external effect, or completion authority.";

export class GoalCapabilityRuntimeError extends Error {}

function fail(message, cause) { throw new GoalCapabilityRuntimeError(message, cause === undefined ? undefined : { cause }); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} is invalid: ${errors[0]}`); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

async function privateDirectory(path, label, create = false) {
  if (resolve(path) !== path) fail(`${label} path is not absolute and canonical`);
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return path;
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

function location(stateRoot, jobId, claimId) {
  if (!JOB_RE.test(jobId ?? "") || !CLAIM_RE.test(claimId ?? "")) fail("goal capability custody identity is invalid");
  const root = resolve(stateRoot), capabilities = join(root, "capability-job-authorizations"), jobs = join(capabilities, jobId), destination = join(jobs, claimId);
  return Object.freeze({ root, capabilities, jobs, destination, bundlePath: join(destination, "bundle.json") });
}

function checkedBinding(binding) {
  exactKeys(binding, ["jobId", "pack", "tools", "maxSessions", "grantLifetimeMs", "limits"], "goal capability binding");
  exactKeys(binding.pack, ["id", "version", "packSha256", "treeSha256"], "goal capability pack binding");
  exactKeys(binding.limits, ["maxInputBytes", "maxOutputBytes", "maxRuntimeMs", "maxCalls", "maxMemoryMiB", "maxCpuCores", "maxPids", "maxWorkspaceBytes"], "goal capability limits");
  if (!JOB_RE.test(binding.jobId ?? "") || !binding.pack || !SHA_RE.test(binding.pack.packSha256 ?? "") || !SHA_RE.test(binding.pack.treeSha256 ?? "")) fail("goal capability binding identity is invalid");
  if (!Array.isArray(binding.tools) || !binding.tools.length || new Set(binding.tools).size !== binding.tools.length || canonical(binding.tools) !== canonical([...binding.tools].sort())) fail("goal capability binding tools must be uniquely sorted");
  return binding;
}

export function validateGoalCapabilityBindings({ capabilityRuntime, jobs, policy }) {
  if (!capabilityRuntime || typeof capabilityRuntime !== "object" || Array.isArray(capabilityRuntime) || !Array.isArray(jobs) || !jobs.length) fail("goal capability configuration is invalid");
  schema("goal capability controller policy", validateWorkCapabilityControllerPolicy(policy));
  if (Date.parse(policy.createdAt) >= Date.parse(policy.expiresAt)) fail("goal capability controller policy lifetime is invalid");
  const jobMap = new Map(jobs.map((job) => [job.jobId, job]));
  if (jobMap.size !== jobs.length || !Array.isArray(capabilityRuntime.bindings) || !capabilityRuntime.bindings.length) fail("goal capability jobs or bindings are invalid");
  const ids = capabilityRuntime.bindings.map((binding) => checkedBinding(binding).jobId);
  if (new Set(ids).size !== ids.length || canonical(ids) !== canonical([...ids].sort())) fail("goal capability bindings must be uniquely sorted by job");
  const admittedPacks = new Set();
  for (const pack of policy.packs) {
    const key = `${pack.id}\0${pack.version}`;
    if (admittedPacks.has(key)) fail("goal capability policy repeats a pack identity");
    admittedPacks.add(key);
    const toolNames = pack.tools.map((tool) => tool.name);
    if (new Set(toolNames).size !== toolNames.length || canonical(toolNames) !== canonical([...toolNames].sort())) fail("goal capability policy tools must be uniquely sorted");
  }
  for (const binding of capabilityRuntime.bindings) {
    const job = jobMap.get(binding.jobId);
    if (!job) fail("goal capability binding names a job outside the immutable goal");
    const admitted = policy.packs.find((pack) => pack.id === binding.pack.id && pack.version === binding.pack.version);
    if (!admitted || admitted.packSha256 !== binding.pack.packSha256 || admitted.treeSha256 !== binding.pack.treeSha256) fail("goal capability binding differs from its controller policy");
    if (!policy.profiles.includes(job.profile) || !admitted.classifications.includes(job.dataClassification)) fail("goal capability policy does not admit the bound job profile or classification");
    const tools = new Map(admitted.tools.map((tool) => [tool.name, tool]));
    if (binding.tools.some((tool) => !tools.has(tool))) fail("goal capability binding names a tool outside its controller policy");
    for (const toolName of binding.tools) {
      const effectClass = tools.get(toolName).effectClass;
      if (effectClass === "read-only" && !job.requestedCapabilities?.tools?.includes("read")) fail("goal capability read-only tool lacks requested job read authority");
      if (effectClass === "workspace" && (job.requestedCapabilities?.filesystem !== "disposable-read-write" || !job.requestedCapabilities?.tools?.some((tool) => ["write", "edit", "bash"].includes(tool)))) fail("goal capability workspace tool lacks requested disposable write authority");
    }
    if (binding.maxSessions > policy.limits.maxSessionsPerLease || binding.grantLifetimeMs > policy.limits.maxGrantLifetimeMs) fail("goal capability binding widens the controller policy");
    for (const field of ["maxInputBytes", "maxOutputBytes", "maxRuntimeMs", "maxMemoryMiB", "maxCpuCores", "maxPids", "maxWorkspaceBytes"]) if (binding.limits[field] > policy.limits[field]) fail(`goal capability binding widens ${field}`);
    if (binding.limits.maxCalls !== 1) fail("goal capability binding must remain single-call");
    if (binding.maxSessions > job.budgets?.maxToolCalls || binding.maxSessions * binding.limits.maxRuntimeMs > job.budgets?.maxRuntimeSeconds * 1000 || binding.maxSessions * binding.limits.maxOutputBytes > job.budgets?.maxArtifactBytes) fail("goal capability binding exceeds aggregate job budgets");
    if (binding.limits.maxMemoryMiB > job.budgets?.maxMemoryMiB || binding.limits.maxCpuCores > job.budgets?.maxCpuCores || binding.limits.maxWorkspaceBytes > job.budgets?.maxDiskBytes || policy.watchdog.maxFailures > job.budgets?.maxFailures) fail("goal capability binding exceeds job resource or failure budgets");
  }
  return true;
}

export async function verifyGoalCapabilityRuntimeReadiness({ config, jobs, policy, now = new Date(), dependencies = {} } = {}) {
  if (!config?.capabilityRuntime) {
    if (policy !== null && policy !== undefined) fail("disabled goal capability runtime has unexpected policy");
    return Object.freeze({ state: "disabled", jobCount: 0, packCount: 0, toolCount: 0 });
  }
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || !dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("goal capability readiness context is invalid");
  validateGoalCapabilityBindings({ capabilityRuntime: config.capabilityRuntime, jobs, policy });
  if (now.getTime() < Date.parse(policy.createdAt) || now.getTime() >= Date.parse(policy.expiresAt)) fail("goal capability controller policy is outside its readiness lifetime");
  const loadInstalled = dependencies.loadInstalledCapabilityPack ?? loadInstalledCapabilityPack;
  const loadHealth = dependencies.loadPassingCapabilityHealth ?? loadPassingCapabilityHealth;
  if (typeof loadInstalled !== "function" || typeof loadHealth !== "function") fail("goal capability readiness dependency is invalid");
  const packs = new Map();
  for (const binding of config.capabilityRuntime.bindings) packs.set(`${binding.pack.id}\0${binding.pack.version}`, binding.pack);
  for (const packBinding of packs.values()) {
    const installed = await loadInstalled({
      stateRoot: config.stateRoot, id: packBinding.id, version: packBinding.version,
      allowedSignersPath: config.capabilityRuntime.allowedSignersPath, sshKeygenPath: config.capabilityRuntime.sshKeygenPath,
    });
    const pack = installed?.pack;
    if (!pack) fail("ready capability pack is unavailable");
    const expectedPackSha256 = capabilityPackSha256(pack);
    if (expectedPackSha256 !== packBinding.packSha256 || pack.provenance.treeSha256 !== packBinding.treeSha256) fail("ready capability pack differs from the exact goal binding");
    await loadHealth({
      stateRoot: config.stateRoot, id: pack.id, version: pack.version,
      allowedSignersPath: config.capabilityRuntime.allowedSignersPath, sshKeygenPath: config.capabilityRuntime.sshKeygenPath,
      now, maxAgeMs: config.capabilityRuntime.maxHealthAgeMs,
    });
  }
  return Object.freeze({
    state: "ready", jobCount: config.capabilityRuntime.bindings.length, packCount: packs.size,
    toolCount: config.capabilityRuntime.bindings.reduce((total, binding) => total + binding.tools.length, 0),
  });
}

function selection(binding, pack, policy, dataClassification) {
  const admitted = policy.packs.find((entry) => entry.id === binding.pack.id && entry.version === binding.pack.version);
  const declared = new Map(pack.tools.map((tool) => [tool.name, tool]));
  const policyTools = new Map(admitted?.tools.map((tool) => [tool.name, tool]) ?? []);
  const tools = binding.tools.map((name) => {
    const packTool = declared.get(name), policyTool = policyTools.get(name);
    if (!packTool || !policyTool || packTool.effectClass !== policyTool.effectClass) fail("goal capability signed tool surface differs from its binding");
    return { name, effectClass: packTool.effectClass };
  });
  return { tools, dataClassification, maxSessions: binding.maxSessions, grantLifetimeMs: binding.grantLifetimeMs, limits: structuredClone(binding.limits) };
}

function bundleFor({ binding, authorization, catalog, pack, policy }) {
  return {
    schemaVersion: 1, operation: "pixel-work-goal-capability-custody", bindingSha256: sha(binding),
    authorization: structuredClone(authorization), catalog: structuredClone(catalog), pack: structuredClone(pack), policy: structuredClone(policy),
    authority: { ...authority }, boundary,
  };
}

function verifyBundle(value, { binding, plan, lease, consumption, checkpoint, policy }) {
  exactKeys(value, ["schemaVersion", "operation", "bindingSha256", "authorization", "catalog", "pack", "policy", "authority", "boundary"], "goal capability custody");
  if (value.schemaVersion !== 1 || value.operation !== "pixel-work-goal-capability-custody" || value.bindingSha256 !== sha(binding) || canonical(value.authority) !== canonical(authority) || value.boundary !== boundary) fail("goal capability custody metadata differs from its exact binding");
  schema("custodied capability authorization", validateWorkCapabilityJobAuthorization(value.authorization));
  schema("custodied capability catalog", validateWorkCapabilityToolCatalog(value.catalog));
  schema("custodied capability pack", validateWorkCapabilityPack(value.pack));
  schema("custodied capability policy", validateWorkCapabilityControllerPolicy(value.policy));
  if (canonical(value.policy) !== canonical(policy)) fail("custodied capability policy differs from the immutable controller copy");
  const expectedPackSha256 = capabilityPackSha256(value.pack);
  if (expectedPackSha256 !== binding.pack.packSha256 || value.pack.provenance.treeSha256 !== binding.pack.treeSha256) fail("custodied capability pack differs from its immutable binding");
  const selected = selection(binding, value.pack, value.policy, plan.dataClassification);
  const suffix = value.authorization.authorizationId.slice(-12), authorizedAt = new Date(value.authorization.authorizedAt);
  const authorization = compileCapabilityJobAuthorization({ plan, lease, consumption, checkpoint, pack: value.pack, expectedPackSha256, policy: value.policy, selection: selected, now: authorizedAt, suffix });
  if (canonical(authorization) !== canonical(value.authorization)) fail("custodied capability authorization differs from current immutable attempt custody");
  const catalog = createCapabilityToolCatalog({ authorization, pack: value.pack, expectedPackSha256, now: new Date(value.catalog.createdAt) });
  if (canonical(catalog) !== canonical(value.catalog)) fail("custodied capability catalog differs from its exact derivation");
  return Object.freeze({ authorization: Object.freeze(structuredClone(authorization)), catalog: Object.freeze(structuredClone(catalog)), pack: Object.freeze(structuredClone(value.pack)), policy: Object.freeze(structuredClone(value.policy)), expectedPackSha256 });
}

async function readCustody(place, context, optional) {
  const info = await lstat(place.destination).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!info && optional) return null;
  await privateDirectory(place.destination, "goal capability custody");
  if (canonical((await readdir(place.destination)).sort()) !== canonical(["bundle.json"])) fail("goal capability custody contains unexpected state");
  let observed;
  try { observed = await readBoundedRegularText(place.bundlePath, MAX_BUNDLE_BYTES, "goal capability custody bundle"); } catch (error) { fail("goal capability custody bundle could not be read safely", error); }
  if (observed.details.nlink !== 1 || (process.platform !== "win32" && (observed.details.uid !== process.geteuid() || (observed.details.mode & 0o077) !== 0))) fail("goal capability custody bundle is not private and single-link");
  let value;
  try { value = JSON.parse(observed.text); } catch { fail("goal capability custody bundle is not JSON"); }
  return verifyBundle(value, context);
}

async function persistCustody({ stateRoot, binding, authorization, catalog, pack, policy, plan, lease, consumption, checkpoint }) {
  const place = location(stateRoot, plan.jobId, consumption.claimId);
  await privateDirectory(place.root, "goal capability state root");
  await privateDirectory(place.capabilities, "goal capability authorization root", true);
  await privateDirectory(place.jobs, "goal capability job root", true);
  const value = bundleFor({ binding, authorization, catalog, pack, policy });
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
  if (bytes.length > MAX_BUNDLE_BYTES) fail("goal capability custody bundle exceeds its byte ceiling");
  const staging = join(place.jobs, `.capability-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    const handle = await open(join(staging, "bundle.json"), "wx", 0o600);
    try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
    await syncDirectory(staging);
    await rename(staging, place.destination);
    await syncDirectory(place.jobs);
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    if (!["EEXIST", "ENOTEMPTY", "EPERM"].includes(error?.code)) throw error;
  }
  return readCustody(place, { binding, plan, lease, consumption, checkpoint, policy }, false);
}

async function loadCustody({ stateRoot, binding, plan, lease, consumption, checkpoint, policy, optional = false }) {
  return readCustody(location(stateRoot, plan.jobId, consumption.claimId), { binding, plan, lease, consumption, checkpoint, policy }, optional);
}

export async function inspectGoalCapabilityAttempt({ stateRoot, binding, plan, lease, consumption, checkpoint, policy } = {}) {
  checkedBinding(binding);
  schema("goal capability controller policy", validateWorkCapabilityControllerPolicy(policy));
  const retained = await loadCustody({ stateRoot, binding, plan, lease, consumption, checkpoint, policy, optional: true });
  return retained === null ? null : Object.freeze({
    state: "authorized", toolCount: retained.catalog.tools.length,
    singleUseCalls: retained.catalog.limits.maxCalls === 1,
    networkAccess: false, externalEffects: false,
  });
}

function capabilityOptions(config, value, plan, lease, consumption, checkpoint) {
  return Object.freeze({
    authorization: value.authorization, catalog: value.catalog, pack: value.pack, expectedPackSha256: value.expectedPackSha256,
    requestContext: Object.freeze({ plan, lease, consumption, checkpoint, pack: value.pack, expectedPackSha256: value.expectedPackSha256, policy: value.policy }),
    runtime: Object.freeze({
      stateRoot: config.stateRoot, id: value.pack.id, version: value.pack.version,
      allowedSignersPath: config.capabilityRuntime.allowedSignersPath, sshKeygenPath: config.capabilityRuntime.sshKeygenPath,
      dockerConfigPath: config.capabilityRuntime.dockerConfigPath, maxHealthAgeMs: config.capabilityRuntime.maxHealthAgeMs,
    }),
  });
}

export function createGoalCapabilityResolver({ config, jobs, policy, now = () => new Date(), dependencies = {} } = {}) {
  if (!config?.capabilityRuntime) fail("goal capability resolver requires an enabled capability runtime");
  if (typeof now !== "function" || !dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("goal capability resolver dependencies are invalid");
  validateGoalCapabilityBindings({ capabilityRuntime: config.capabilityRuntime, jobs, policy });
  const bindings = new Map(config.capabilityRuntime.bindings.map((binding) => [binding.jobId, binding]));
  const loadInstalled = dependencies.loadInstalledCapabilityPack ?? loadInstalledCapabilityPack;
  const loadHealth = dependencies.loadPassingCapabilityHealth ?? loadPassingCapabilityHealth;
  const persist = dependencies.persistCustody ?? persistCustody;
  const load = dependencies.loadCustody ?? loadCustody;
  for (const callback of [loadInstalled, loadHealth, persist, load]) if (typeof callback !== "function") fail("goal capability resolver callback is invalid");
  return async ({ mode, prepared, claim, checkpoint }) => {
    if (!["execute", "cleanup"].includes(mode) || !prepared?.plan || !prepared?.lease || !claim || !checkpoint) fail("goal capability attempt context is invalid");
    const binding = bindings.get(prepared.plan.jobId);
    if (!binding) return null;
    if (claim.jobId !== prepared.plan.jobId || checkpoint.jobId !== prepared.plan.jobId || checkpoint.state !== "running" || checkpoint.iteration !== prepared.lease.iteration) fail("goal capability resolver did not receive the exact running attempt");
    if (mode === "cleanup") {
      const recovered = await load({ stateRoot: config.stateRoot, binding, plan: prepared.plan, lease: prepared.lease, consumption: claim, checkpoint, policy, optional: true });
      return recovered === null ? null : capabilityOptions(config, recovered, prepared.plan, prepared.lease, claim, checkpoint);
    }
    const installed = await loadInstalled({ stateRoot: config.stateRoot, id: binding.pack.id, version: binding.pack.version, allowedSignersPath: config.capabilityRuntime.allowedSignersPath, sshKeygenPath: config.capabilityRuntime.sshKeygenPath });
    const pack = installed?.pack, expectedPackSha256 = capabilityPackSha256(pack);
    if (expectedPackSha256 !== binding.pack.packSha256 || pack.provenance.treeSha256 !== binding.pack.treeSha256) fail("installed capability pack differs from the exact goal binding");
    const observed = now();
    if (!(observed instanceof Date) || !Number.isSafeInteger(observed.getTime())) fail("goal capability health observation time is invalid");
    if (observed.getTime() < Date.parse(checkpoint.createdAt) || observed.getTime() >= Math.min(Date.parse(prepared.lease.expiresAt), Date.parse(policy.expiresAt))) fail("goal capability health observation is outside the exact running authority lifetime");
    await loadHealth({ stateRoot: config.stateRoot, id: pack.id, version: pack.version, allowedSignersPath: config.capabilityRuntime.allowedSignersPath, sshKeygenPath: config.capabilityRuntime.sshKeygenPath, now: observed, maxAgeMs: config.capabilityRuntime.maxHealthAgeMs });
    const selected = selection(binding, pack, policy, prepared.plan.dataClassification);
    const authorizedAt = new Date(checkpoint.createdAt);
    const suffix = sha({ binding, planSha256: sha(prepared.plan), leaseSha256: sha(prepared.lease), consumptionSha256: sha(claim), checkpointSha256: sha(checkpoint) }).slice(0, 12);
    const authorization = compileCapabilityJobAuthorization({ plan: prepared.plan, lease: prepared.lease, consumption: claim, checkpoint, pack, expectedPackSha256, policy, selection: selected, now: authorizedAt, suffix });
    const catalog = createCapabilityToolCatalog({ authorization, pack, expectedPackSha256, now: authorizedAt });
    const retained = await persist({ stateRoot: config.stateRoot, binding, authorization, catalog, pack, policy, plan: prepared.plan, lease: prepared.lease, consumption: claim, checkpoint });
    return capabilityOptions(config, retained, prepared.plan, prepared.lease, claim, checkpoint);
  };
}

export async function resolveAttemptLifecycleOptions(lifecycleOptions, context) {
  if (!lifecycleOptions || typeof lifecycleOptions !== "object" || Array.isArray(lifecycleOptions)) fail("attempt lifecycle options are invalid");
  const { capabilityResolver, knowledgeResolver, ...base } = lifecycleOptions;
  if (capabilityResolver !== undefined && (typeof capabilityResolver !== "function" || Object.hasOwn(base, "capability"))) fail("attempt capability resolver configuration is invalid");
  if (knowledgeResolver !== undefined && (typeof knowledgeResolver !== "function" || Object.hasOwn(base, "knowledge"))) fail("attempt knowledge resolver configuration is invalid");
  if (capabilityResolver === undefined && knowledgeResolver === undefined) return lifecycleOptions;
  const [capability, knowledge] = await Promise.all([
    capabilityResolver === undefined ? null : capabilityResolver(context),
    knowledgeResolver === undefined ? null : knowledgeResolver(context),
  ]);
  return Object.freeze({ ...base, ...(capability === null ? {} : { capability }), ...(knowledge === null ? {} : { knowledge }) });
}

export const goalCapabilityRuntimeBoundary = boundary;
