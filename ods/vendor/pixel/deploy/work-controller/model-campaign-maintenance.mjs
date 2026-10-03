import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { productionMaintenancePrimitives } from "./model-qualification-maintenance.mjs";
import { inspectMaintenanceCustodyLock, validateMaintenanceCustodyBinding, withMaintenanceCustody } from "./maintenance-custody.mjs";
import { requireLiveCampaignGuardianLease } from "./maintenance-recovery-guardian-lease.mjs";
import {
  advanceCampaignRecoveryJournal,
  assertNoActiveCampaignRecoveryJournal,
  cancelCampaignRecoveryJournal,
  createCampaignRecoveryJournal,
  deriveCampaignJournalIdentitySha256,
  deriveCampaignOutcome,
  deriveCampaignErrorOutcome,
  deriveCampaignRecoveryReceiptPath,
  deriveTerminalCampaignStatusFromOutcome,
  hasActiveCampaignRecoveryJournal,
  readCampaignSettlementReceipt,
  validateCampaignOutcome,
  CAMPAIGN_OUTCOME_FAILURE_CLASSES,
} from "./maintenance-recovery-journal.mjs";
import { renderCampaignGuardianUnit } from "./maintenance-campaign-recovery-guardian-unit.mjs";
import { buildSystemdEnv } from "./maintenance-recovery-guardian-supervisor.mjs";
import { launchCampaignChildSupervised } from "./maintenance-campaign-child-launcher.mjs";

const execute = promisify(execFile);
const {
  DOCKER_ENV,
  readPrivateJsonRecord,
  validateProduction,
  dockerInspectMaybe,
  validateProductionMaintenanceBinding,
} = productionMaintenancePrimitives;

const MAX_JSON_BYTES = 4 * 1024 * 1024;
const MAX_SOURCE_BYTES = 4 * 1024 * 1024;
const SHA_RE = /^[a-f0-9]{64}$/u;
const ZERO_SHA256 = "0".repeat(64);
const COMMIT_RE = /^[a-f0-9]{40}$/u;
const DOCKER_IDENTITY_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$/u;
const CAMPAIGN_ID_RE = /^outcomebattery-[a-f0-9]{24}$/u;
const CONFIG_SCHEMA = "https://osmantic.com/pixel/schemas/work-model-campaign-maintenance-v1.schema.json";
const PAIR_CONFIG_SCHEMA = "https://osmantic.com/pixel/schemas/portal-outcome-pair-system-v1.schema.json";
const DIGEST_IMAGE_RE = /^sha256:[a-f0-9]{64}$/u;
const RESOURCE_PREFIXES = Object.freeze(["pixel-outcome-", "pixel-work-"]);
const CAMPAIGN_FAILURE_CLASSES = new Set(["timeout", "child-exit-2", "child-exit-other", "invalid-strict-result"]);
const CONFIG_BOUNDARY = "Owner-private exact maintenance binding for one production container and one bounded resumable local DSV4 comparison campaign batch. Configuration grants no stop, start, task, model, tool, credential, external network, external effect, deployment, completion, publication, acceptance, or promotion authority.";
const PAIR_CONFIG_BOUNDARY = "Owner-private exact Pixel/Codex pair execution configuration only. It grants no provider, credential, external-effect, merge, deployment, publication, completion, acceptance, or promotion authority.";
const REVIEW_BOUNDARY = "Content-free review of one exact bounded local DSV4 comparison campaign maintenance operation. Confirmation may stop only the bound running production container, execute at most the bound number of exact local paired tasks, require exclusive accelerator custody and complete comparison-resource cleanup, restart the same container, and perform one credential-free loopback readiness probe; it grants no other execution, network, credential, external effect, deployment, completion, publication, acceptance, or promotion authority.";
const RESULT_BOUNDARY = "Content-free result for one exact bounded local DSV4 comparison campaign maintenance operation. It records campaign progress, restoration, accelerator custody, and isolation state without exposing task content, model content, prompts, responses, paths, or credentials and grants no further execution, network, external effect, deployment, completion, publication, acceptance, or promotion authority.";
const CAMPAIGN_SCRIPT_RELATIVE = join("scripts", "portal_outcome_battery_campaign.py");
const CAMPAIGN_GUARDIAN_UNIT = "pixel-campaign-maintenance-recovery.service";
const CAMPAIGN_GUARDIAN_MODULE_URL = new URL("./maintenance-campaign-recovery-guardian.mjs", import.meta.url);
const CAMPAIGN_GUARDIAN_MODULE_PATH = fileURLToPath(CAMPAIGN_GUARDIAN_MODULE_URL);
const CAMPAIGN_CHILD_SUPERVISOR_MODULE_URL = new URL("./maintenance-campaign-child-supervisor.mjs", import.meta.url);
const CAMPAIGN_CHILD_SUPERVISOR_MODULE_PATH = fileURLToPath(CAMPAIGN_CHILD_SUPERVISOR_MODULE_URL);

export class WorkModelCampaignMaintenanceError extends Error {}

export class CampaignFailure extends Error {
  constructor(failureClass, { stderr } = {}) {
    if (!CAMPAIGN_FAILURE_CLASSES.has(failureClass)) throw new WorkModelCampaignMaintenanceError("campaign failure class is invalid");
    super(`campaign failure: ${failureClass}`);
    this.name = "CampaignFailure";
    this.failureClass = failureClass;
    const diagnosticBytes = Buffer.from(typeof stderr === "string" ? stderr : "", "utf8");
    this.diagnosticSha256 = sha(diagnosticBytes);
    Object.defineProperty(this, "diagnosticBytes", { value: diagnosticBytes, enumerable: false });
  }
}

function fail(message) { throw new WorkModelCampaignMaintenanceError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}
function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}
function inside(parent, child) {
  const value = relative(parent, child);
  return value === "" || value !== ".." && !value.startsWith(`..${sep}`) && !isAbsolute(value);
}

function campaignFailureDiagnosticPath(loaded, failure) {
  return join(
    dirname(loaded.configuration.outputRoot),
    `.pixel-campaign-failure-${loaded.operationSha256}-${failure.diagnosticSha256}.stderr`,
  );
}

async function persistCampaignFailureDiagnostic(loaded, failure) {
  const bytes = failure?.diagnosticBytes;
  if (!Buffer.isBuffer(bytes) || bytes.length === 0 || bytes.length > MAX_JSON_BYTES) return false;
  const path = campaignFailureDiagnosticPath(loaded, failure);
  const flags = constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL
    | (constants.O_NOFOLLOW ?? 0) | (constants.O_CLOEXEC ?? 0);
  let handle;
  try {
    handle = await open(path, flags, 0o600);
    await handle.writeFile(bytes);
    await handle.sync();
    const details = await handle.stat();
    if (!details.isFile() || details.nlink !== 1 || process.platform !== "win32" && (details.uid !== loaded.expectedOwnerUid || (details.mode & 0o077) !== 0)) return false;
    return true;
  } catch (error) {
    if (error?.code !== "EEXIST") return false;
    try {
      const observed = await readBoundedRegularFile(path, MAX_JSON_BYTES, "private campaign failure diagnostic");
      return observed.details.isFile() && observed.details.nlink === 1
        && (process.platform === "win32" || observed.details.uid === loaded.expectedOwnerUid && (observed.details.mode & 0o077) === 0)
        && observed.bytes.equals(bytes);
    } catch { return false; }
  } finally {
    try { await handle?.close(); } catch { /* Diagnostic persistence must never block production restoration. */ }
  }
}

export function validateModelCampaignMaintenanceConfiguration(value) {
  exactKeys(value, ["$schema", "schemaVersion", "pythonPath", "dockerPath", "sourceRoot", "sourceCommit", "candidateSourceArchiveSha256", "materializationRoot", "pairConfigurationPath", "outputRoot", "campaign", "custody", "production", "boundary"], "campaign maintenance configuration");
  if (value.$schema !== CONFIG_SCHEMA || value.schemaVersion !== 1 || value.boundary !== CONFIG_BOUNDARY) fail("campaign maintenance configuration contract is invalid");
  for (const [field, label] of [
    ["pythonPath", "campaign Python executable"], ["dockerPath", "campaign Docker executable"],
    ["sourceRoot", "campaign source root"], ["materializationRoot", "campaign materialization root"],
    ["pairConfigurationPath", "campaign pair configuration"], ["outputRoot", "campaign output root"],
  ]) absolutePath(value[field], label);
  validateMaintenanceCustodyBinding(value.custody);
  if (!COMMIT_RE.test(value.sourceCommit ?? "") || value.sourceCommit === "0".repeat(40) || !SHA_RE.test(value.candidateSourceArchiveSha256 ?? "") || value.candidateSourceArchiveSha256 === "0".repeat(64)) fail("campaign source identity is invalid");
  exactKeys(value.campaign, ["maxPairs", "partition", "freezeTuning", "runtimeCondition", "timeoutSeconds"], "campaign maintenance batch");
  integer(value.campaign.maxPairs, 1, 500, "campaign maintenance pair ceiling");
  integer(value.campaign.timeoutSeconds, 300, 604800, "campaign maintenance timeout");
  if (!new Set(["tuning", "held-out"]).has(value.campaign.partition) || typeof value.campaign.freezeTuning !== "boolean" || !new Set(["cold-first-request", "warm-neutral-probe"]).has(value.campaign.runtimeCondition)) fail("campaign maintenance batch mode is invalid");
  if (value.campaign.partition === "held-out" && value.campaign.freezeTuning) fail("held-out campaign maintenance cannot rewrite the tuning freeze");
  validateProductionMaintenanceBinding(value.production, "campaign maintenance");
  return structuredClone(value);
}

export function validateCampaignPairConfiguration(value, candidateSourceArchiveSha256) {
  exactKeys(value, [
    "$schema", "schemaVersion", "candidateSourceArchiveSha256", "pixelSystemConfigPath", "codexRunnerImage",
    "codexBoundaryImage", "codexSurfaceQualificationPath", "modelArtifactPath", "modelArtifactManifestPath",
    "launchArgumentsPath", "capabilityRetentionEvidencePath", "preflightPath", "boundary",
  ], "campaign pair configuration");
  if (value.$schema !== PAIR_CONFIG_SCHEMA || value.schemaVersion !== 1 || value.boundary !== PAIR_CONFIG_BOUNDARY) fail("campaign pair configuration contract is invalid");
  if (!SHA_RE.test(value.candidateSourceArchiveSha256 ?? "") || value.candidateSourceArchiveSha256 !== candidateSourceArchiveSha256) fail("campaign pair configuration differs from the exact candidate source");
  if (!DIGEST_IMAGE_RE.test(value.codexRunnerImage ?? "") || !DIGEST_IMAGE_RE.test(value.codexBoundaryImage ?? "")) fail("campaign pair configuration image identity is invalid");
  for (const [field, label] of [
    ["pixelSystemConfigPath", "campaign Pixel system configuration"],
    ["codexSurfaceQualificationPath", "campaign Codex surface qualification"],
    ["modelArtifactPath", "campaign model artifact"],
    ["modelArtifactManifestPath", "campaign model artifact manifest"],
    ["launchArgumentsPath", "campaign launch arguments"],
    ["capabilityRetentionEvidencePath", "campaign capability-retention evidence"],
    ["preflightPath", "campaign pair preflight path"],
  ]) absolutePath(value[field], label);
  return structuredClone(value);
}

async function secureDirectory(path, label, expectedOwnerUid, { privateDirectory }) {
  absolutePath(path, label);
  let actual, info;
  try { [actual, info] = await Promise.all([realpath(path), lstat(path)]); } catch { fail(`${label} could not be opened safely`); }
  const forbidden = privateDirectory ? 0o077 : 0o022;
  if (!samePath(actual, path) || !info.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & forbidden) !== 0)) fail(`${label} is not a real ${privateDirectory ? "owner-private" : "non-writable"} directory`);
  return path;
}

async function secureExecutable(path, label, expectedOwnerUid) {
  absolutePath(path, label);
  let actual, info;
  try { [actual, info] = await Promise.all([realpath(path), lstat(path)]); } catch { fail(`${label} could not be opened safely`); }
  if (!samePath(actual, path) || !info.isFile() || info.isSymbolicLink() || process.platform !== "win32" && (!new Set([0, expectedOwnerUid]).has(info.uid) || (info.mode & 0o022) !== 0 || (info.mode & 0o111) === 0)) fail(`${label} is not an immutable executable`);
  return path;
}

async function sourceBytes(path, label, expectedOwnerUid) {
  absolutePath(path, label);
  let actual, record;
  try { [actual, record] = await Promise.all([realpath(path), readBoundedRegularFile(path, MAX_SOURCE_BYTES, label)]); } catch { fail(`${label} could not be opened safely`); }
  const info = record.details;
  if (!samePath(actual, path) || !info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o022) !== 0)) fail(`${label} is not an immutable source file`);
  return record.bytes;
}

async function inspectSourceDefault(sourceRoot) {
  let head, status;
  try {
    ({ stdout: head } = await execute("/usr/bin/git", ["-C", sourceRoot, "rev-parse", "HEAD"], { encoding: "utf8", windowsHide: true, timeout: 30000, maxBuffer: 1024 * 1024, env: DOCKER_ENV }));
    ({ stdout: status } = await execute("/usr/bin/git", ["-C", sourceRoot, "status", "--porcelain=v1", "--untracked-files=all"], { encoding: "utf8", windowsHide: true, timeout: 30000, maxBuffer: 4 * 1024 * 1024, env: DOCKER_ENV }));
  } catch { fail("campaign source Git identity could not be inspected"); }
  return { commit: head.trim(), clean: status === "" };
}

function parseLines(stdout, label) {
  if (typeof stdout !== "string" || stdout.length > MAX_JSON_BYTES) fail(`${label} output is invalid`);
  const values = stdout.split(/\r?\n/u).filter(Boolean);
  if (values.some((value) => !DOCKER_IDENTITY_RE.test(value))) fail(`${label} returned an invalid Docker identity`);
  return [...new Set(values)].sort();
}

function requestsGpu(container) {
  const requests = container?.HostConfig?.DeviceRequests;
  return Array.isArray(requests) && requests.some((request) => {
    const capabilities = Array.isArray(request?.Capabilities) ? request.Capabilities.flat(Infinity) : [];
    return capabilities.includes("gpu") || request?.Driver === "nvidia" || Array.isArray(request?.DeviceIDs) && request.DeviceIDs.length > 0;
  });
}

async function dockerList(dockerPath, args, label) {
  let stdout;
  try { ({ stdout } = await execute(dockerPath, args, { encoding: "utf8", windowsHide: true, timeout: 30000, maxBuffer: MAX_JSON_BYTES, env: DOCKER_ENV })); }
  catch { fail(`${label} could not be inspected`); }
  return parseLines(stdout, label);
}

async function inventoryDefault(dockerPath) {
  const [containers, networks, volumes, running] = await Promise.all([
    dockerList(dockerPath, ["container", "ls", "-a", "--format", "{{.Names}}"], "campaign container inventory"),
    dockerList(dockerPath, ["network", "ls", "--format", "{{.Name}}"], "campaign network inventory"),
    dockerList(dockerPath, ["volume", "ls", "--format", "{{.Name}}"], "campaign volume inventory"),
    dockerList(dockerPath, ["container", "ls", "--format", "{{.ID}}"], "campaign running-container inventory"),
  ]);
  const inspected = await Promise.all(running.map((id) => dockerInspectMaybe(dockerPath, "container", id)));
  return {
    prefixContainers: containers.filter((name) => RESOURCE_PREFIXES.some((prefix) => name.startsWith(prefix))),
    prefixNetworks: networks.filter((name) => RESOURCE_PREFIXES.some((prefix) => name.startsWith(prefix))),
    prefixVolumes: volumes.filter((name) => RESOURCE_PREFIXES.some((prefix) => name.startsWith(prefix))),
    runningGpuContainerIds: inspected.filter(requestsGpu).map((container) => container.Id).sort(),
  };
}

function exactInventory(value, label) {
  exactKeys(value, ["prefixContainers", "prefixNetworks", "prefixVolumes", "runningGpuContainerIds"], label);
  for (const field of Object.keys(value)) {
    if (!Array.isArray(value[field]) || value[field].some((item) => typeof item !== "string") || canonical(value[field]) !== canonical([...new Set(value[field])].sort())) fail(`${label} is invalid`);
  }
  return value;
}

export function requireCleanInventory(value, expectedGpuIds, label) {
  exactInventory(value, label);
  if (value.prefixContainers.length || value.prefixNetworks.length || value.prefixVolumes.length || canonical(value.runningGpuContainerIds) !== canonical([...expectedGpuIds].sort())) fail(`${label} is not an exclusive clean comparison window`);
  return value;
}

function campaignOperation(configuration, bindings, fingerprint, inventory, custodyIdentitySha256) {
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-work-model-campaign-maintenance",
    configurationSha256: bindings.configurationSha256,
    sourceCommit: configuration.sourceCommit,
    candidateSourceArchiveSha256: configuration.candidateSourceArchiveSha256,
    campaignScriptSha256: bindings.campaignScriptSha256,
    materializationSha256: bindings.materializationSha256,
    pairConfigurationSha256: bindings.pairConfigurationSha256,
    preflightSha256: bindings.preflightSha256,
    campaign: configuration.campaign,
    productionBindingSha256: sha({ production: configuration.production, fingerprint }),
    acceleratorStateSha256: sha(inventory),
    custodyIdentitySha256,
  });
}

// The exact deterministic child execution contract bound by the distinct
// campaign operation hash. It is derived from the same reviewed inputs that
// produce the real campaign child (see executeCampaignChild) using canonical
// structured hashing, never delimiter concatenation, so that any change to the
// exact Python path, ordered argv, cwd, fixed environment, timeout/max-buffer
// contract, campaign script/raw-config/materialization/pair/preflight/source
// identity, partition, runtime condition, or output authority changes the
// campaign operation identity.
function campaignExecutionContract(configuration, bindings) {
  return {
    schemaVersion: 1,
    operation: "pixel-work-model-campaign-child-execution",
    pythonPath: configuration.pythonPath,
    argv: campaignArguments(configuration),
    cwd: configuration.sourceRoot,
    env: { ...DOCKER_ENV, PYTHONDONTWRITEBYTECODE: "1" },
    timeoutMilliseconds: configuration.campaign.timeoutSeconds * 1000,
    maxBufferBytes: MAX_JSON_BYTES,
    campaignScriptSha256: bindings.campaignScriptSha256,
    configurationSha256: bindings.configurationSha256,
    materializationSha256: bindings.materializationSha256,
    pairConfigurationSha256: bindings.pairConfigurationSha256,
    preflightSha256: bindings.preflightSha256,
    sourceCommit: configuration.sourceCommit,
    candidateSourceArchiveSha256: configuration.candidateSourceArchiveSha256,
    partition: configuration.campaign.partition,
    runtimeCondition: configuration.campaign.runtimeCondition,
    outputRoot: configuration.outputRoot,
  };
}

async function readCampaignGuardianModuleSha() {
  return sha(await readFile(CAMPAIGN_GUARDIAN_MODULE_PATH));
}

async function loadMaintenance(configPath, dependencies = {}) {
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail("campaign maintenance requires a non-root service identity");
  const configurationRecord = await readPrivateJsonRecord(configPath, "private campaign maintenance configuration", expectedOwnerUid);
  const configuration = validateModelCampaignMaintenanceConfiguration(configurationRecord.value);
  await assertNoActiveCampaignRecoveryJournal(configuration.custody.lockPath, expectedOwnerUid);
  await Promise.all([
    secureExecutable(configuration.pythonPath, "campaign Python executable", expectedOwnerUid),
    secureExecutable(configuration.dockerPath, "campaign Docker executable", expectedOwnerUid),
    secureDirectory(configuration.sourceRoot, "campaign source root", expectedOwnerUid, { privateDirectory: false }),
    secureDirectory(configuration.materializationRoot, "campaign materialization root", expectedOwnerUid, { privateDirectory: true }),
  ]);
  if (inside(configuration.sourceRoot, configuration.outputRoot)) fail("campaign maintenance output must remain outside the exact source root");
  const outputParent = resolve(configuration.outputRoot, "..");
  await secureDirectory(outputParent, "campaign output parent", expectedOwnerUid, { privateDirectory: true });
  let outputExists = true;
  try { await lstat(configuration.outputRoot); }
  catch (error) {
    if (error?.code !== "ENOENT") fail("campaign output root could not be inspected safely");
    outputExists = false;
  }
  if (outputExists) await secureDirectory(configuration.outputRoot, "campaign output root", expectedOwnerUid, { privateDirectory: true });
  return finishLoad({ configuration, configurationRecord, expectedOwnerUid, dependencies });
}

async function finishLoad({ configuration, configurationRecord, expectedOwnerUid, dependencies }) {
  const campaignScriptPath = join(configuration.sourceRoot, CAMPAIGN_SCRIPT_RELATIVE);
  const materializationPath = join(configuration.materializationRoot, "materialization.json");
  const [script, materializationRecord, pairRecord, source, custody] = await Promise.all([
    sourceBytes(campaignScriptPath, "campaign driver source", expectedOwnerUid),
    readPrivateJsonRecord(materializationPath, "private campaign materialization", expectedOwnerUid),
    readPrivateJsonRecord(configuration.pairConfigurationPath, "private campaign pair configuration", expectedOwnerUid),
    (dependencies.inspectSource ?? inspectSourceDefault)(configuration.sourceRoot),
    inspectMaintenanceCustodyLock(configuration.custody, expectedOwnerUid),
  ]);
  if (!source || source.commit !== configuration.sourceCommit || source.clean !== true) fail("campaign source differs from the exact clean reviewed commit");
  const materialization = materializationRecord.value;
  const pair = validateCampaignPairConfiguration(pairRecord.value, configuration.candidateSourceArchiveSha256);
  if (materialization?.operation !== "pixel-portal-outcome-battery-materialization" || !new Set(["assistant", "builder", "controller", "researcher"]).has(materialization.profile) || !new Set(["matched-budget", "maximum-quality"]).has(materialization.evaluationRegime) || !Array.isArray(materialization.tasks) || materialization.tasks.length < 1) fail("campaign materialization identity is invalid");
  const preflightRecord = await readPrivateJsonRecord(pair.preflightPath, "private campaign pair preflight", expectedOwnerUid);
  const preflight = preflightRecord.value;
  if (preflight?.operation !== "pixel-portal-outcome-pair-preflight" || preflight.status !== "ready" || preflight.configurationSha256 !== sha(pairRecord.bytes) || preflight.profile !== materialization.profile || preflight.modelContractSha256 !== materialization.modelContractSha256 || preflight.inferenceContractSha256 !== materialization.inferenceContractSha256) fail("campaign preflight differs from its materialization or pair configuration");
  const inspectContainer = dependencies.inspectContainer ?? ((target, dockerPath) => dockerInspectMaybe(dockerPath, "container", target));
  const inventory = dependencies.inventory ?? inventoryDefault;
  const before = await inspectContainer(configuration.production.containerId, configuration.dockerPath);
  const fingerprint = validateProduction(before, configuration.production, { running: true, requireReviewedStart: true });
  const initialInventory = requireCleanInventory(await inventory(configuration.dockerPath), [configuration.production.containerId], "campaign maintenance review inventory");
  const after = await inspectContainer(configuration.production.containerId, configuration.dockerPath);
  if (canonical(validateProduction(after, configuration.production, { running: true, requireReviewedStart: true })) !== canonical(fingerprint)) fail("production changed while preparing campaign maintenance review");
  const repeatedInventory = requireCleanInventory(await inventory(configuration.dockerPath), [configuration.production.containerId], "campaign maintenance repeated inventory");
  if (canonical(initialInventory) !== canonical(repeatedInventory)) fail("accelerator or comparison-resource state changed while preparing campaign maintenance review");
  const bindings = {
    configurationSha256: sha(configurationRecord.bytes),
    campaignScriptSha256: sha(script),
    materializationSha256: sha(materializationRecord.bytes),
    pairConfigurationSha256: sha(pairRecord.bytes),
    preflightSha256: sha(preflightRecord.bytes),
  };
  const operation = campaignOperation(configuration, bindings, fingerprint, initialInventory, custody.identitySha256);
  const campaignOperationSha256 = sha(campaignExecutionContract(configuration, bindings));
  return { expectedOwnerUid, configuration, configBytes: configurationRecord.bytes, materialization, bindings, fingerprint, initialInventory, custody, operation, operationSha256: sha(operation), campaignOperationSha256, inspectContainer, inventory };
}

// Recovery-time binding load used by the M6 campaign recovery guardian. Unlike
// the run/review load it does NOT require production to be running and does NOT
// assert a clean running inventory, because recovery happens after production
// has been stopped and while comparison resources may legitimately be absent.
// It reads every immutable authority input (config, source, script,
// materialization, pair, preflight, custody) and derives the exact raw config
// hash, bindings, custody identity, and the distinct campaign child operation
// hash so the guardian can revalidate the journal identity exactly and fail
// closed on any config/identity change.
export async function loadCampaignRecoveryBinding(configPath, dependencies = {}) {
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail("campaign maintenance requires a non-root service identity");
  const configurationRecord = await readPrivateJsonRecord(configPath, "private campaign maintenance configuration", expectedOwnerUid);
  const configuration = validateModelCampaignMaintenanceConfiguration(configurationRecord.value);
  await Promise.all([
    secureExecutable(configuration.pythonPath, "campaign Python executable", expectedOwnerUid),
    secureExecutable(configuration.dockerPath, "campaign Docker executable", expectedOwnerUid),
    secureDirectory(configuration.sourceRoot, "campaign source root", expectedOwnerUid, { privateDirectory: false }),
    secureDirectory(configuration.materializationRoot, "campaign materialization root", expectedOwnerUid, { privateDirectory: true }),
  ]);
  if (inside(configuration.sourceRoot, configuration.outputRoot)) fail("campaign maintenance output must remain outside the exact source root");
  const outputParent = resolve(configuration.outputRoot, "..");
  await secureDirectory(outputParent, "campaign output parent", expectedOwnerUid, { privateDirectory: true });
  const campaignScriptPath = join(configuration.sourceRoot, CAMPAIGN_SCRIPT_RELATIVE);
  const materializationPath = join(configuration.materializationRoot, "materialization.json");
  const [script, materializationRecord, pairRecord, source, custody] = await Promise.all([
    sourceBytes(campaignScriptPath, "campaign driver source", expectedOwnerUid),
    readPrivateJsonRecord(materializationPath, "private campaign materialization", expectedOwnerUid),
    readPrivateJsonRecord(configuration.pairConfigurationPath, "private campaign pair configuration", expectedOwnerUid),
    (dependencies.inspectSource ?? inspectSourceDefault)(configuration.sourceRoot),
    inspectMaintenanceCustodyLock(configuration.custody, expectedOwnerUid),
  ]);
  if (!source || source.commit !== configuration.sourceCommit || source.clean !== true) fail("campaign source differs from the exact clean reviewed commit");
  const materialization = materializationRecord.value;
  if (materialization?.operation !== "pixel-portal-outcome-battery-materialization" || !new Set(["assistant", "builder", "controller", "researcher"]).has(materialization.profile) || !new Set(["matched-budget", "maximum-quality"]).has(materialization.evaluationRegime) || !Array.isArray(materialization.tasks) || materialization.tasks.length < 1) fail("campaign materialization identity is invalid");
  const pair = validateCampaignPairConfiguration(pairRecord.value, configuration.candidateSourceArchiveSha256);
  const preflightRecord = await readPrivateJsonRecord(pair.preflightPath, "private campaign pair preflight", expectedOwnerUid);
  const preflight = preflightRecord.value;
  if (preflight?.operation !== "pixel-portal-outcome-pair-preflight" || preflight.status !== "ready" || preflight.configurationSha256 !== sha(pairRecord.bytes) || preflight.profile !== materialization.profile || preflight.modelContractSha256 !== materialization.modelContractSha256 || preflight.inferenceContractSha256 !== materialization.inferenceContractSha256) fail("campaign preflight differs from its materialization or pair configuration");
  const bindings = {
    configurationSha256: sha(configurationRecord.bytes),
    campaignScriptSha256: sha(script),
    materializationSha256: sha(materializationRecord.bytes),
    pairConfigurationSha256: sha(pairRecord.bytes),
    preflightSha256: sha(preflightRecord.bytes),
  };
  const configBytes = configurationRecord.bytes;
  const configBytesSha = sha(configBytes);
  const campaignOperationSha256 = sha(campaignExecutionContract(configuration, bindings));
  const inspectContainer = dependencies.inspectContainer ?? ((target, dockerPath) => dockerInspectMaybe(dockerPath, "container", target));
  const inventory = dependencies.inventory ?? inventoryDefault;
  return { expectedOwnerUid, configuration, configBytes, configBytesSha, bindings, custody, campaignOperationSha256, inspectContainer, inventory };
}

// Re-load the exact campaign maintenance configuration and every transitive
// authority-bearing input (source, script, materialization, pair, preflight,
// production, inventory, custody) under the acquired custody lock and compare
// every identity to the reviewed pre-lock snapshot. Any raw-byte, binding,
// custody, operation, child-operation, fingerprint, inventory, source, or
// executable/path substitution refuses before any journal creation or Docker
// mutation.
async function prepareCampaignMaintenanceUnderCustody(configPath, dependencies, preLock, custodyIdentitySha256) {
  const current = await loadMaintenance(configPath, dependencies);
  if (sha(current.configBytes) !== sha(preLock.configBytes)) fail("campaign maintenance configuration raw bytes changed while acquiring custody; refusing");
  if (current.custody.identitySha256 !== custodyIdentitySha256) fail("campaign maintenance custody identity changed under the lock");
  if (current.operationSha256 !== preLock.operationSha256) fail("campaign maintenance operation hash changed under the lock");
  if (canonical(current.operation) !== canonical(preLock.operation)) fail("campaign maintenance operation changed under the lock");
  if (current.campaignOperationSha256 !== preLock.campaignOperationSha256) fail("campaign maintenance child operation hash changed under the lock");
  for (const key of Object.keys(current.bindings)) {
    if (current.bindings[key] !== preLock.bindings[key]) fail(`campaign maintenance ${key} binding changed under the lock`);
  }
  if (canonical(current.fingerprint) !== canonical(preLock.fingerprint)) fail("campaign production fingerprint changed under the lock");
  if (canonical(current.initialInventory) !== canonical(preLock.initialInventory)) fail("campaign accelerator or comparison-resource state changed under the lock");
  if (!samePath(current.configuration.pythonPath, preLock.configuration.pythonPath)) fail("campaign python executable path changed under the lock");
  if (!samePath(current.configuration.dockerPath, preLock.configuration.dockerPath)) fail("campaign docker executable path changed under the lock");
  if (!samePath(current.configuration.sourceRoot, preLock.configuration.sourceRoot)) fail("campaign source root changed under the lock");
  if (!samePath(current.configuration.materializationRoot, preLock.configuration.materializationRoot)) fail("campaign materialization root changed under the lock");
  if (!samePath(current.configuration.pairConfigurationPath, preLock.configuration.pairConfigurationPath)) fail("campaign pair configuration path changed under the lock");
  if (!samePath(current.configuration.outputRoot, preLock.configuration.outputRoot)) fail("campaign output root changed under the lock");
  if (current.configuration.sourceCommit !== preLock.configuration.sourceCommit) fail("campaign source commit changed under the lock");
  return current;
}

// The exact accepted immutable campaign recovery-journal identity: outer
// maintenance operation hash, the distinct campaign operation hash, custody
// identity, raw configuration hash, owner UID, the exact five-field production
// identity, and the comparison materialization/pair/preflight/accelerator
// hashes. This is the same projection asserted by the campaign journal engine
// (see deriveCampaignJournalIdentity/campaignJournalIdentity); callers must
// never invent a second definition.
function campaignRecoveryJournalIdentity(loaded) {
  return {
    maintenanceOperationSha256: loaded.operationSha256,
    campaignOperationSha256: loaded.campaignOperationSha256,
    custodyIdentitySha256: loaded.custody.identitySha256,
    configurationSha256: sha(loaded.configBytes),
    ownerUid: loaded.expectedOwnerUid,
    production: {
      containerName: loaded.configuration.production.containerName,
      containerId: loaded.configuration.production.containerId,
      imageDigest: loaded.configuration.production.imageDigest,
      expectedStartedAt: loaded.configuration.production.expectedStartedAt,
      expectedRestartCount: loaded.configuration.production.expectedRestartCount,
    },
    comparison: {
      materializationSha256: loaded.bindings.materializationSha256,
      pairConfigurationSha256: loaded.bindings.pairConfigurationSha256,
      preflightSha256: loaded.bindings.preflightSha256,
      acceleratorStateSha256: loaded.operation.acceleratorStateSha256,
    },
  };
}

// The accepted campaign recovery-journal record at creation: it carries the
// accepted kind/operation and the immutable identity, and all mutable
// consequence fields as null. Durable creation itself supplies the prepared
// phase (see createCampaignRecoveryJournal).
function campaignRecoveryJournalRecord(loaded) {
  return {
    schemaVersion: 1,
    kind: "pixel-campaign-maintenance-recovery-journal",
    operation: "pixel-work-model-campaign-maintenance",
    phase: "prepared",
    campaignChildNonce: null,
    campaignChild: null,
    guardianStartedAt: null,
    guardianStartNonce: null,
    guardianStartEndpointIdentity: null,
    guardianStartReceipt: null,
    readinessEvidence: null,
    campaignOutcome: null,
    ...campaignRecoveryJournalIdentity(loaded),
  };
}

export async function reviewModelCampaignMaintenance(configPath, dependencies = {}) {
  const loaded = await loadMaintenance(configPath, dependencies);
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-work-model-campaign-maintenance-review",
    status: "confirmation-required",
    maintenanceOperationSha256: loaded.operationSha256,
    profile: loaded.materialization.profile,
    evaluationRegime: loaded.materialization.evaluationRegime,
    partition: loaded.configuration.campaign.partition,
    runtimeCondition: loaded.configuration.campaign.runtimeCondition,
    maxPairs: loaded.configuration.campaign.maxPairs,
    timeoutSeconds: loaded.configuration.campaign.timeoutSeconds,
    confirmation: { option: "--confirm-campaign-maintenance-operation-sha256", sha256: loaded.operationSha256 },
    changes: { acquiresExactCustodyLock: true, stopsExactProductionContainer: true, runsBoundedLocalPairs: true, requiresExclusiveAcceleratorCustody: true, requiresComparisonIsolationAbsentBeforeRestore: true, restartsSameProductionContainer: true, performsCredentialFreeLoopbackReadinessProbe: true, usesExternalNetwork: false, usesCredentials: false, deploysCode: false },
    authority: { grantsUnboundedExecution: false, grantsProductionMutation: false, grantsCredentials: false, grantsExternalNetwork: false, grantsExternalEffects: false, grantsDeployment: false, grantsCompletion: false },
    boundary: REVIEW_BOUNDARY,
  });
}

export function campaignArguments(configuration) {
  const args = [
    join(configuration.sourceRoot, CAMPAIGN_SCRIPT_RELATIVE),
    "--root", configuration.sourceRoot,
    "--materialization", configuration.materializationRoot,
    "--pair-config", configuration.pairConfigurationPath,
    "--output", configuration.outputRoot,
    "--max-pairs", String(configuration.campaign.maxPairs),
    "--partition", configuration.campaign.partition,
    "--runtime-condition", configuration.campaign.runtimeCondition,
  ];
  if (configuration.campaign.freezeTuning) args.push("--freeze-tuning");
  return args;
}

export function validateCampaignResult(value, exitCode, loaded) {
  if (!value || typeof value !== "object" || Array.isArray(value) || !CAMPAIGN_ID_RE.test(value.campaignId ?? "")) fail("campaign maintenance result identity is invalid");
  const requiredForPartition = loaded.materialization.tasks.filter((item) => item.partition === loaded.configuration.campaign.partition).length;
  if (value.operation === "pixel-portal-outcome-tuning-baseline-freeze") {
    const heldOutCommitment = loaded.materialization.tasks
      .filter((item) => item.partition === "held-out")
      .map((item) => ({
        batteryTaskId: item.batteryTaskId,
        taskSha256: item.taskSha256,
        taskAdmissionSha256: item.taskAdmissionSha256,
        sourceSnapshotSha256: item.sourceSnapshotSha256,
        verifierSha256: item.verifierSha256,
        researchFixtureSha256: item.researchFixtureSha256,
      }));
    if (
      !loaded.configuration.campaign.freezeTuning || loaded.configuration.campaign.partition !== "tuning" || exitCode !== 0
      || !Array.isArray(value.tuningTasks) || value.tuningTasks.length !== requiredForPartition
      || value.heldOutTaskCount !== heldOutCommitment.length || value.heldOutTaskBytesOpened !== false
      || value.heldOutTaskSetSha256 !== sha(heldOutCommitment)
    ) fail("campaign maintenance tuning freeze is invalid");
    return { campaignId: value.campaignId, campaignStatus: "tuning-frozen", completedPairs: requiredForPartition, requiredPairs: requiredForPartition };
  }
  if (value.operation !== "pixel-portal-outcome-battery-campaign-progress" || value.profile !== loaded.materialization.profile || value.evaluationRegime !== loaded.materialization.evaluationRegime || value.partition !== loaded.configuration.campaign.partition || value.runtimeCondition !== loaded.configuration.campaign.runtimeCondition || !new Set(["pass", "in-progress", "blocked"]).has(value.status)) fail("campaign maintenance progress result is invalid");
  integer(value.requiredPairs, 1, 500, "campaign maintenance required pair count");
  integer(value.completedPairs, 0, value.requiredPairs, "campaign maintenance completed pair count");
  if (
    value.requiredPairs !== requiredForPartition
    || loaded.configuration.campaign.partition === "held-out" && (value.tuningBaselineFrozen !== true || !SHA_RE.test(value.tuningBaselineFreezeSha256 ?? ""))
    || loaded.configuration.campaign.partition === "tuning" && (value.tuningBaselineFrozen !== false || value.tuningBaselineFreezeSha256 !== null)
    || value.status === "pass" && value.completedPairs !== value.requiredPairs
    || value.status === "in-progress" && value.completedPairs >= value.requiredPairs
    || exitCode !== (value.status === "pass" ? 0 : 3)
  ) fail("campaign maintenance progress or exit status is inconsistent");
  return { campaignId: value.campaignId, campaignStatus: value.status, completedPairs: value.completedPairs, requiredPairs: value.requiredPairs };
}

export async function executeCampaignChild(loaded, run = execute) {
  let stdout, stderr = "", exitCode = 0;
  try {
    ({ stdout, stderr = "" } = await run(loaded.configuration.pythonPath, campaignArguments(loaded.configuration), {
      cwd: loaded.configuration.sourceRoot,
      encoding: "utf8",
      windowsHide: true,
      timeout: loaded.configuration.campaign.timeoutSeconds * 1000,
      maxBuffer: MAX_JSON_BYTES,
      env: { ...DOCKER_ENV, PYTHONDONTWRITEBYTECODE: "1" },
    }));
  } catch (error) {
    stderr = typeof error?.stderr === "string" ? error.stderr : "";
    if (error?.killed === true) throw new CampaignFailure("timeout", { stderr });
    if (error?.code === 2) throw new CampaignFailure("child-exit-2", { stderr });
    if (error?.code !== 3) throw new CampaignFailure("child-exit-other", { stderr });
    stdout = typeof error?.stdout === "string" ? error.stdout : "";
    exitCode = 3;
  }
  let value;
  try { value = parseStrictJson(stdout, "campaign maintenance progress result"); }
  catch { throw new CampaignFailure("invalid-strict-result", { stderr }); }
  try { return { ...validateCampaignResult(value, exitCode, loaded), exitCode, resultSha256: sha(Buffer.from(stdout, "utf8")) }; }
  catch { throw new CampaignFailure("invalid-strict-result", { stderr }); }
}

// M5 supervised campaign-child launch. The default (real) campaign path runs
// through the nonce-named transient user service + bounded supervisor instead of
// a direct execFile. All M4 injected behavior is preserved: when dependencies
// inject runCampaign, that exact injected behavior is used unchanged.
async function buildCampaignChildContext(loaded, extra) {
  const supervisorPath = await realpath(CAMPAIGN_CHILD_SUPERVISOR_MODULE_PATH);
  const supervisorSha256 = sha(await readFile(CAMPAIGN_CHILD_SUPERVISOR_MODULE_PATH));
  const custodyLockPath = loaded.configuration.custody.lockPath;
  return {
    expectedOwnerUid: loaded.expectedOwnerUid,
    custodyLockPath,
    journalIdentity: campaignRecoveryJournalIdentity(loaded),
    campaignOperationSha256: loaded.campaignOperationSha256,
    innerPython: {
      path: loaded.configuration.pythonPath,
      argv: campaignArguments(loaded.configuration),
      cwd: loaded.configuration.sourceRoot,
      env: { ...DOCKER_ENV, PYTHONDONTWRITEBYTECODE: "1" },
      timeoutMilliseconds: loaded.configuration.campaign.timeoutSeconds * 1000,
      maxStdoutBytes: MAX_JSON_BYTES,
      maxStderrBytes: MAX_JSON_BYTES,
    },
    supervisorPath,
    supervisorSha256,
    nodePath: extra.expectedNodePath,
    outDir: join(dirname(custodyLockPath), ".pixel-campaign-child"),
    systemdEnv: buildSystemdEnv(loaded.expectedOwnerUid),
    configSha256: extra.configSha256,
    leaseMaxAgeMs: extra.guardianLeaseMaxAgeMs,
    leaseBootId: extra.guardianLeaseBootId,
    guardianUnit: extra.guardianUnit ?? CAMPAIGN_GUARDIAN_UNIT,
    configPath: extra.configPath,
    custodyIdentitySha256: loaded.custody.identitySha256,
    guardianModuleSha256: extra.guardianModuleSha256,
    guardianModulePath: CAMPAIGN_GUARDIAN_MODULE_PATH,
    guardianUnitSha256: extra.expectedUnitSha256,
    journalIdentitySha256: extra.journalIdentitySha256,
  };
}

// Reuses the existing strict result/failure classification over the separately
// bounded private stdout and diagnostic bytes delivered by the supervisor.
async function classifySupervisedChild({ stdoutBytes, stderrBytes, exitCode, signal, timedOut, outputOverflow, spawnError }, loaded) {
  const stderr = stderrBytes.toString("utf8");
  let failureClass = null;
  if (timedOut) failureClass = "timeout";
  else if (spawnError) failureClass = "child-exit-other";
  else if (outputOverflow) failureClass = "child-exit-other";
  else if (signal) failureClass = "child-exit-other";
  else if (exitCode === 2) failureClass = "child-exit-2";
  else if (exitCode !== 0 && exitCode !== 3) failureClass = "child-exit-other";
  if (failureClass) return { ok: false, failure: new CampaignFailure(failureClass, { stderr }) };
  const stdout = stdoutBytes.toString("utf8");
  let value;
  try { value = parseStrictJson(stdout, "campaign maintenance progress result"); }
  catch { return { ok: false, failure: new CampaignFailure("invalid-strict-result", { stderr }) }; }
  try {
    const validated = validateCampaignResult(value, exitCode, loaded);
    return { ok: true, result: { ...validated, exitCode, resultSha256: sha(Buffer.from(stdout, "utf8")) } };
  } catch {
    return { ok: false, failure: new CampaignFailure("invalid-strict-result", { stderr }) };
  }
}

async function runCampaignDefault(loaded, extra) {
  const ctx = await buildCampaignChildContext(loaded, extra);
  const launchResult = await launchCampaignChildSupervised(ctx, {
    advanceJournal: extra.advanceJournal,
    requireLease: extra.requireCampaignLease,
    classifySupervised: (supervised) => classifySupervisedChild(supervised, loaded),
  });
  return launchResult.result;
}

function boundedMs(value, fallback, minimum, maximum, label) {
  if (value === undefined) return fallback;
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is out of range`);
  return value;
}

async function sleep(ms) { return new Promise((resolveSleep) => setTimeout(resolveSleep, ms)); }

// M8 content-free outcome derivation. Only the minimal fields needed to derive
// a terminal status and a deterministic audit identity are retained; never raw
// prompts, model output, stderr, diagnostic text, secrets, or other sensitive
// content. A classified error never fabricates a campaign identity/result/exit
// code; those fields stay null unless truly observed. A genuine result binds
// the exact validated campaign identity, result identity, exit code, and
// truthful pair counts.
function deriveContentFreeOutcome(loaded, campaign) {
  const status = campaign?.campaignStatus;
  if (!["pass", "in-progress", "blocked", "error", "tuning-frozen"].includes(status)) fail("campaign maintenance cannot bind a nonterminal or fabricated outcome");
  if (status === "error") {
    const failureClass = campaign.campaignFailureClass;
    if (!CAMPAIGN_OUTCOME_FAILURE_CLASSES.includes(failureClass ?? "")) fail("campaign maintenance classified error failure class is invalid");
    const diagnosticSha256 = campaign.campaignFailureDiagnosticSha256;
    if (!SHA_RE.test(diagnosticSha256 ?? "") || diagnosticSha256 === ZERO_SHA256) fail("campaign maintenance classified error diagnostic identity is invalid");
    return deriveCampaignErrorOutcome({
      failureClass,
      diagnosticSha256,
      completedPairs: campaign.completedPairs ?? 0,
      requiredPairs: campaign.requiredPairs ?? 0,
    });
  }
  const campaignId = campaign.campaignId;
  const resultSha256 = campaign.resultSha256;
  if (!CAMPAIGN_ID_RE.test(campaignId ?? "")) fail("campaign maintenance genuine result campaign identity is invalid");
  if (!SHA_RE.test(resultSha256 ?? "") || resultSha256 === ZERO_SHA256) fail("campaign maintenance genuine result identity is invalid");
  if (!Number.isSafeInteger(campaign.exitCode)) fail("campaign maintenance genuine result exit code is invalid");
  return deriveCampaignOutcome({
    status,
    campaignId,
    resultSha256,
    exitCode: campaign.exitCode,
    completedPairs: campaign.completedPairs,
    requiredPairs: campaign.requiredPairs,
  });
}

// Project the exact validated campaign outcome into the controller result
// campaign fields. The terminal result fields MUST come from this real
// in-memory validated outcome for the current synchronous run (never a
// fabricated campaign identity/result/exit code), and classified errors remain
// truthful.
function outcomeCampaignProjection(outcome, campaign) {
  const o = validateCampaignOutcome(outcome);
  return {
    // Every outcome-derived field comes from the real in-memory validated
    // campaign outcome for this synchronous run.
    campaignId: o.campaignId,
    campaignStatus: o.status,
    exitCode: o.exitCode,
    resultSha256: o.resultSha256,
    campaignFailureClass: o.failureClass,
    campaignFailureDiagnosticSha256: o.diagnosticSha256,
    completedPairs: o.completedPairs,
    requiredPairs: o.requiredPairs,
    // Diagnostic availability is a controller-side persistence fact not carried
    // by the content-free outcome; it reflects whether the diagnostic artifact
    // was durably persisted for this run (null when there is no diagnostic).
    campaignFailureDiagnosticAvailable: campaign?.campaignFailureDiagnosticAvailable ?? null,
  };
}

const HANDOFF_TIMEOUT_STATUS = "campaign-maintenance-handoff-timeout";
const HANDOFF_RECEIPT_INVALID_STATUS = "campaign-maintenance-receipt-invalid";
const DEFAULT_GUARDIAN_SETTLEMENT_TIMEOUT_MS = 60000;
const DEFAULT_GUARDIAN_SETTLEMENT_POLL_MS = 200;

// Bounded settlement wait AFTER custody is released. The controller never holds
// custody while waiting for the single guardian to converge (M6->M7->M8) and
// settle. A truthful terminal status is returned ONLY when the guardian's
// durable settlement receipt has been securely read and strictly validated to
// equal the exact expected terminal outcome/status and the active journal has
// been removed. It never trusts a bare lstat file-exists (a forged/symlinked/
// malformed receipt must never produce a terminal result): every receipt is
// read through the shared owner-private bounded/no-symlink/strict-UTF-8/strict-
// JSON machinery and its shape, owner, status, operation/config/custody/
// campaign identities and campaignOutcomeSha256 are required to equal the exact
// expected values. A missing journal + missing receipt is still a transient
// (receipt-first-then-unlink), so it keeps polling; a malformed/mismatched
// receipt fails closed immediately. On timeout or invalid receipt it returns a
// precise nonterminal result that can never be mistaken for success.
async function waitForCampaignSettlementDefault(handoff, dependencies = {}) {
  const timeoutMs = boundedMs(dependencies.guardianSettlementTimeoutMs, DEFAULT_GUARDIAN_SETTLEMENT_TIMEOUT_MS, 1, 3600000, "campaign guardian settlement timeout");
  const pollMs = boundedMs(dependencies.guardianSettlementPollMs, DEFAULT_GUARDIAN_SETTLEMENT_POLL_MS, 10, 10000, "campaign guardian settlement poll");
  const { loaded, outcome } = handoff;
  const status = deriveTerminalCampaignStatusFromOutcome(outcome);
  const expected = {
    status,
    maintenanceOperationSha256: loaded.operationSha256,
    campaignOperationSha256: loaded.campaignOperationSha256,
    custodyIdentitySha256: loaded.custody.identitySha256,
    configurationSha256: sha(loaded.configBytes),
    campaignOutcome: outcome,
  };
  const lockPath = loaded.configuration.custody.lockPath;
  const receiptPath = deriveCampaignRecoveryReceiptPath(lockPath, status, loaded.operationSha256);
  const deadline = Date.now() + timeoutMs;
  while (true) {
    const active = await hasActiveCampaignRecoveryJournal(lockPath, loaded.expectedOwnerUid);
    if (!active) {
      let receipt;
      try { receipt = await readCampaignSettlementReceipt(lockPath, loaded.expectedOwnerUid, expected); }
      catch { return { settled: false, status: HANDOFF_RECEIPT_INVALID_STATUS, receiptPath: null }; }
      if (receipt) return { settled: true, status, receiptPath };
    }
    if (Date.now() >= deadline) return { settled: false, status: HANDOFF_TIMEOUT_STATUS, receiptPath: null };
    await sleep(pollMs);
  }
}

function result(loaded, status, campaign, state) {
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-work-model-campaign-maintenance-result",
    status,
    maintenanceOperationSha256: loaded.operationSha256,
    campaignId: campaign.campaignId,
    campaignStatus: campaign.campaignStatus,
    campaignExitCode: campaign.exitCode,
    campaignResultSha256: campaign.resultSha256,
    campaignFailureClass: campaign.campaignFailureClass ?? null,
    campaignFailureDiagnosticSha256: campaign.campaignFailureDiagnosticSha256 ?? null,
    campaignFailureDiagnosticAvailable: campaign.campaignFailureDiagnosticAvailable ?? null,
    completedPairs: campaign.completedPairs,
    requiredPairs: campaign.requiredPairs,
    productionRestored: state.productionRestored,
    productionReady: state.productionReady,
    comparisonIsolationAbsent: state.comparisonIsolationAbsent,
    exclusiveAcceleratorCustody: state.exclusiveAcceleratorCustody,
    credentialsUsed: false,
    externalNetworkUsed: false,
    authority: { grantsUnboundedExecution: false, grantsProductionMutation: false, grantsCredentials: false, grantsExternalNetwork: false, grantsExternalEffects: false, grantsDeployment: false, grantsCompletion: false },
    boundary: RESULT_BOUNDARY,
  });
}

export async function runModelCampaignMaintenance(configPath, confirmation, dependencies = {}) {
  const preLock = await loadMaintenance(configPath, dependencies);
  if (!SHA_RE.test(confirmation ?? "") || confirmation !== preLock.operationSha256) fail("confirmation differs from the exact campaign maintenance operation");
  const withCustody = dependencies.withCustody ?? ((binding, operation) => withMaintenanceCustody(binding, preLock.expectedOwnerUid, operation));
  const handoff = await withCustody(preLock.configuration.custody, async (custodyIdentitySha256 = preLock.custody.identitySha256) => {
  if (custodyIdentitySha256 !== preLock.custody.identitySha256) fail("campaign maintenance custody identity differs from the reviewed operation");
  // The pre-lock load is only a routing/review binding. Securely reread the raw
  // configuration and every authority-bearing input under the lock; every
  // journal record, lease check, and action below derives from this load.
  const loaded = await prepareCampaignMaintenanceUnderCustody(configPath, dependencies, preLock, custodyIdentitySha256);
  const production = loaded.configuration.production;
  const inspectContainer = loaded.inspectContainer;
  const inventory = loaded.inventory;
  const stopProduction = dependencies.stopProduction ?? ((target) => execute(loaded.configuration.dockerPath, ["container", "stop", "--time", String(production.stopTimeoutSeconds), target], { encoding: "utf8", windowsHide: true, timeout: (production.stopTimeoutSeconds + 30) * 1000, maxBuffer: 1024 * 1024, env: DOCKER_ENV }));
  let productionStopped = false;
  let campaign = { campaignId: null, campaignStatus: "not-run", exitCode: null, resultSha256: null, completedPairs: 0, requiredPairs: loaded.materialization.tasks.filter((item) => item.partition === loaded.configuration.campaign.partition).length, campaignFailureClass: null, campaignFailureDiagnosticSha256: null, campaignFailureDiagnosticAvailable: null };
  // M4 durable journal/lease/stop-authorization sequence. The lease binds the
  // exact raw configuration hash, custody identity, guardian module, rendered
  // campaign unit bytes, real Node path, the distinct campaign operation hash,
  // and the immutable campaign journal identity, and is required twice: after
  // the prepared journal is created and its identity derived, and again
  // immediately before the stop, so an absent, dead, stale, or substituted
  // guardian fails closed before production is ever touched.
  const journalLockPath = loaded.configuration.custody.lockPath;
  const journalIdentity = campaignRecoveryJournalIdentity(loaded);
  const journalRecord = campaignRecoveryJournalRecord(loaded);
  const createJournal = dependencies.createCampaignRecoveryJournal ?? createCampaignRecoveryJournal;
  const advanceJournal = dependencies.advanceCampaignRecoveryJournal ?? advanceCampaignRecoveryJournal;
  const cancelJournal = dependencies.cancelCampaignRecoveryJournal ?? cancelCampaignRecoveryJournal;
  const requireCampaignLease = dependencies.requireCampaignLease ?? requireLiveCampaignGuardianLease;
  const configSha256 = sha(loaded.configBytes);
  const guardianModuleSha256 = await readCampaignGuardianModuleSha();
  const expectedNodePath = await realpath(process.execPath);
  const rendered = await renderCampaignGuardianUnit({ configPath, nodePath: expectedNodePath, guardianPath: CAMPAIGN_GUARDIAN_MODULE_PATH, expectedOwnerUid: loaded.expectedOwnerUid });
  const expectedUnitSha256 = rendered.renderSha256;
  let journalCreated = false;
  let stopAuthorized = false;
  let stopInvoked = false;
  let journalIdentitySha256 = null;
  try {
    const preStop = await inspectContainer(production.containerId, loaded.configuration.dockerPath);
    if (canonical(validateProduction(preStop, production, { running: true, requireReviewedStart: true })) !== canonical(loaded.fingerprint)) fail("production changed after campaign maintenance review");
    const preInventory = requireCleanInventory(await inventory(loaded.configuration.dockerPath), [production.containerId], "campaign maintenance final pre-stop inventory");
    if (canonical(preInventory) !== canonical(loaded.initialInventory)) fail("accelerator state changed after campaign maintenance review");
    const created = await createJournal(journalLockPath, loaded.expectedOwnerUid, journalRecord);
    journalCreated = true;
    journalIdentitySha256 = deriveCampaignJournalIdentitySha256(created.journal);
    const guardLease = () => requireCampaignLease(journalLockPath, loaded.expectedOwnerUid, configSha256, {
      maxAgeMs: dependencies.guardianLeaseMaxAgeMs,
      bootId: dependencies.guardianLeaseBootId,
      unit: dependencies.guardianUnit ?? CAMPAIGN_GUARDIAN_UNIT,
      configPath,
      custodyIdentitySha256: loaded.custody.identitySha256,
      guardianModuleSha256,
      guardianModulePath: CAMPAIGN_GUARDIAN_MODULE_PATH,
      expectedUnitSha256,
      expectedNodePath,
      campaignOperationSha256: loaded.campaignOperationSha256,
      journalIdentitySha256,
    });
    await guardLease();
    await advanceJournal(journalLockPath, loaded.expectedOwnerUid, "production-stop-authorized", journalIdentity);
    stopAuthorized = true;
    await guardLease();
    // Re-assert the exact authorized journal (same phase, exact identity)
    // immediately before the stop so a journal deleted, substituted,
    // wrong-phase, or malformed after the second lease check can never permit a
    // stop that lacks a current recoverable journal.
    await advanceJournal(journalLockPath, loaded.expectedOwnerUid, "production-stop-authorized", journalIdentity);
    stopInvoked = true;
    await stopProduction(production.containerId);
    const stopped = await inspectContainer(production.containerId, loaded.configuration.dockerPath);
    validateProduction(stopped, production, { running: false, requireReviewedStart: true });
    await advanceJournal(journalLockPath, loaded.expectedOwnerUid, "production-stopped", journalIdentity);
    productionStopped = true;
  } catch (error) {
    const observed = await inspectContainer(production.containerId, loaded.configuration.dockerPath).catch(() => null);
    let confirmedStopped = false;
    try { validateProduction(observed, production, { running: false, requireReviewedStart: true }); confirmedStopped = true; } catch {}
    if (confirmedStopped && journalCreated) {
      // A stop was proven; never cancel. Retain and durably record the
      // production-stopped phase.
      await advanceJournal(journalLockPath, loaded.expectedOwnerUid, "production-stopped", journalIdentity);
      productionStopped = true;
    } else if (stopInvoked) {
      // A stop was invoked but its outcome could not be confirmed or production
      // has since restarted. Never infer "no destructive action" from a later
      // running state: retain the journal as honest, recoverable mutation state.
      const ambiguous = new WorkModelCampaignMaintenanceError("campaign production stop outcome is unconfirmed or production restarted; campaign recovery journal retained");
      throw ambiguous;
    } else if (journalCreated && !stopAuthorized) {
      // An inert prepared journal exists with no stop authorization and no
      // stop invocation/destructive consequence committed; cancel it through
      // the exact identity-checked durable primitive so a failed guardian proof
      // does not leave a blocking active journal and no substituted path is
      // ever unlinked.
      await cancelJournal(journalLockPath, loaded.expectedOwnerUid, journalIdentity);
      throw error;
    } else {
      // After production-stop-authorized (or with any stop invocation), never
      // cancel; retain the journal for recovery.
      throw error;
    }
  }
  // M5 supervised child-launch seam: the default campaign child runs through
  // the nonce-named transient user service + bounded supervisor. Injected
  // dependencies.runCampaign is honored unchanged (M4 behavior preserved).
  const runCampaign = dependencies.runCampaign ?? ((l) => runCampaignDefault(l, {
    advanceJournal,
    requireCampaignLease,
    configSha256,
    guardianModuleSha256,
    expectedNodePath,
    expectedUnitSha256,
    journalIdentitySha256,
    guardianLeaseMaxAgeMs: dependencies.guardianLeaseMaxAgeMs,
    guardianLeaseBootId: dependencies.guardianLeaseBootId,
    guardianUnit: dependencies.guardianUnit ?? CAMPAIGN_GUARDIAN_UNIT,
    configPath,
  }));
  try {
    requireCleanInventory(await inventory(loaded.configuration.dockerPath), [], "campaign maintenance post-stop inventory");
    campaign = await runCampaign(loaded);
    validateProduction(await inspectContainer(production.containerId, loaded.configuration.dockerPath), production, { running: false, requireReviewedStart: true });
  } catch (error) {
    if (!productionStopped) throw error;
    // P0: any error after production stopped that is NOT an explicitly
    // classified CampaignFailure must hold production. A CampaignFailure can be
    // thrown only after the launcher has proven the positive terminal/cgroup
    // boundary and durably advanced to comparison-cleanup-pending, so it is the
    // ONLY error that may proceed to the production-restore path. Every other
    // class (WorkCampaignChildLaunchError, WorkMaintenanceSecureFileError,
    // ordinary Error, guardian/journal/systemd errors, or any unexpected class)
    // holds production: never call startProduction and never classify it as a
    // restorable campaign failure. The recovery journal is retained at its
    // truthful last phase.
    if (!(error instanceof CampaignFailure)) {
      campaign = { ...campaign, campaignStatus: "error", campaignFailureClass: "controller-error", campaignFailureDiagnosticSha256: sha(Buffer.alloc(0)), campaignFailureDiagnosticAvailable: false };
      return result(loaded, "manual-attention-production-held", campaign, { productionRestored: false, productionReady: false, comparisonIsolationAbsent: false, exclusiveAcceleratorCustody: false });
    }
    const diagnosticAvailable = await persistCampaignFailureDiagnostic(loaded, error);
    campaign = { ...campaign, campaignStatus: "error", campaignFailureClass: error.failureClass, campaignFailureDiagnosticSha256: error.diagnosticSha256, campaignFailureDiagnosticAvailable: diagnosticAvailable };
  }
  // M8: durably bind the content-free campaign outcome before any semantic
  // information can be lost, then release custody and hand off to the single
  // campaign guardian (M6->M7->M8 convergence + settlement). The controller no
  // longer performs its own Docker start/readiness shortcut and never holds
  // custody while waiting for the guardian.
  let outcome;
  try { outcome = deriveContentFreeOutcome(loaded, campaign); }
  catch { return result(loaded, "manual-attention-production-held", campaign, { productionRestored: false, productionReady: false, comparisonIsolationAbsent: false, exclusiveAcceleratorCustody: false }); }
  try {
    // Durable same-boundary advance to campaign-outcome-bound binds the outcome
    // in the recovery journal before the controller releases custody.
    await advanceJournal(journalLockPath, loaded.expectedOwnerUid, "campaign-outcome-bound", journalIdentity, { campaignOutcome: outcome });
  } catch {
    return result(loaded, "manual-attention-production-held", campaign, { productionRestored: false, productionReady: false, comparisonIsolationAbsent: false, exclusiveAcceleratorCustody: false });
  }
  // Return the handoff so the withCustody wrapper releases the custody lock
  // before the settlement wait below.
  return { handoff: true, loaded, campaign, outcome };
  });
  // After custody is released: bounded settlement wait (never under custody) for
  // the single guardian to converge and settle exactly once. A truthful terminal
  // result is returned only when the guardian settles; otherwise a precise
  // nonterminal/manual/timeout result is returned that can never be mistaken for
  // campaign success.
  if (!handoff.handoff) return handoff;
  const waitForSettlement = dependencies.waitForCampaignSettlement ?? waitForCampaignSettlementDefault;
  const waitResult = await waitForSettlement(handoff, dependencies);
  if (waitResult.settled) {
    const terminal = deriveTerminalCampaignStatusFromOutcome(handoff.outcome);
    // The terminal result fields come from the real in-memory validated
    // campaign outcome for this synchronous run (exact outcome hash/status and
    // nullable campaignId), never from fabricated campaign identity/result/exit
    // code fields. Truthful custody reporting after release: the controller
    // releases custody to the single guardian before waiting, and the guardian
    // settles and releases its own custody/lease. exclusiveAcceleratorCustody
    // is therefore reported false at the terminal result — it is never claimed
    // merely because a receipt file exists, and never because the custody lock
    // is still held (it is not).
    return result(handoff.loaded, terminal, outcomeCampaignProjection(handoff.outcome, handoff.campaign), { productionRestored: true, productionReady: true, comparisonIsolationAbsent: true, exclusiveAcceleratorCustody: false });
  }
  return result(handoff.loaded, waitResult.status ?? HANDOFF_TIMEOUT_STATUS, handoff.campaign, { productionRestored: false, productionReady: false, comparisonIsolationAbsent: false, exclusiveAcceleratorCustody: false });
}

function parseArguments(argv) {
  if (!Array.isArray(argv) || !["review", "run"].includes(argv[0])) fail("Usage: model-campaign-maintenance.mjs review --config PRIVATE_JSON | run --config PRIVATE_JSON --confirm-campaign-maintenance-operation-sha256 HASH");
  if (argv[0] === "review" && argv.length === 3 && argv[1] === "--config" && argv[2]) return { operation: "review", configPath: resolve(argv[2]), confirmation: null };
  if (argv[0] === "run" && argv.length === 5 && argv[1] === "--config" && argv[2] && argv[3] === "--confirm-campaign-maintenance-operation-sha256" && argv[4]) return { operation: "run", configPath: resolve(argv[2]), confirmation: argv[4] };
  fail("campaign maintenance arguments are incomplete, unknown, or duplicated");
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  const value = options.operation === "review" ? await reviewModelCampaignMaintenance(options.configPath) : await runModelCampaignMaintenance(options.configPath, options.confirmation);
  process.stdout.write(`${JSON.stringify(value)}\n`);
  if (value.operation.endsWith("-result") && !value.status.endsWith("-production-restored")) process.exitCode = 3;
  else if (value.operation.endsWith("-result") && ["campaign-error-production-restored", "campaign-blocked-production-restored"].includes(value.status)) process.exitCode = 2;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-model-campaign-maintenance: ${error instanceof WorkModelCampaignMaintenanceError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const modelCampaignMaintenanceBoundaries = Object.freeze({ configuration: CONFIG_BOUNDARY, pairConfiguration: PAIR_CONFIG_BOUNDARY, review: REVIEW_BOUNDARY, result: RESULT_BOUNDARY });
