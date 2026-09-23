import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, readdir, realpath } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { canonical, validateWorkModelCapabilityReceipt } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { prepareModelBackendLaunch } from "./model-backend-cli.mjs";
import { modelBackendLifecycleSha256, startModelBackend, stopModelBackend } from "./model-backend-lifecycle.mjs";
import { modelCapabilityReceiptSha256 } from "./model-qualification.mjs";
import {
  fixedModelQualificationCasesSha256, fixedModelQualificationEvaluatorSha256,
  validateFixedModelQualificationConfig,
} from "./model-qualification-runner.mjs";
import { MODEL_QUALIFICATION_CONFIG_PATH, MODEL_QUALIFICATION_OUTPUT_PATH } from "./model-qualification-container.mjs";
import { validateImageInspect } from "../work-runner/docker-boundary.mjs";
import { inspectModelBackendCoordinationRoot } from "../work-runner/model-backend-coordination.mjs";

const execute = promisify(execFile);
const MAX_JSON_BYTES = 1024 * 1024;
const IMAGE_RE = /^sha256:[a-f0-9]{64}$/u;
const DOCKER_ID_RE = /^[a-f0-9]{64}$/u;
const CONFIRMATION_RE = /^[a-f0-9]{64}$/u;
const CONFIG_SCHEMA = "https://osmantic.com/pixel/schemas/work-model-qualification-docker-v1.schema.json";
const CONFIG_BOUNDARY = "Owner-private exact local-model qualification inputs only. Configuration grants no model start, container, network, device, credential, external-effect, completion, publication, deployment, acceptance, or promotion authority.";
const REVIEW_BOUNDARY = "Content-free review of one exact private local-model qualification operation. Confirmation may start the reviewed contained model and one isolated synthetic qualification container, write one new private receipt, and remove both; it grants no credential, external network, external effect, completion, publication, deployment, acceptance, or promotion authority.";
const RESULT_BOUNDARY = "Content-free exact local-model Docker qualification result only. Private model identity and receipt remain in owner custody. It grants no execution, credential, external network, external effect, completion, publication, deployment, acceptance, or promotion authority.";
const OUTPUT_NAME = basename(MODEL_QUALIFICATION_OUTPUT_PATH);
const START_DIAGNOSTIC_NAME = "model-backend-start-diagnostic.json";
const START_DIAGNOSTIC_BOUNDARY = "Owner-private bounded diagnostic from one failed exact local-model backend start. It may contain backend startup paths and messages, never grants authority, and must remain in owner custody; capture is best-effort and can never delay or weaken exact rollback.";
const FAILURE_DIAGNOSTIC_NAME = "model-qualification-failure-diagnostic.json";
const FAILURE_DIAGNOSTIC_BOUNDARY = "Owner-private bounded diagnostic from one failed exact local-model qualification. It may contain local failure messages and paths, links any retained backend-start diagnostic by hash, records cleanup proof without granting authority, and must remain in owner custody.";
const CONTAINER_LABEL = "com.osmantic.pixel.work-model-qualification.operation-sha256";
const DOCKER_ENV = Object.freeze({ HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" });
const PREPARED_MAINTENANCE_OPERATION = Symbol("pixel.modelQualification.preparedMaintenanceOperation");

export class WorkModelQualificationDockerError extends Error {}

function fail(message) { throw new WorkModelQualificationDockerError(message); }
function withFailureStage(error, stage) {
  if (error && typeof error === "object") {
    try {
      if (typeof error.qualificationFailureStage !== "string") Object.defineProperty(error, "qualificationFailureStage", { value: stage, configurable: true });
      return error;
    } catch {}
  }
  const failure = new WorkModelQualificationDockerError("Docker qualification failed closed");
  Object.defineProperty(failure, "qualificationFailureStage", { value: stage, configurable: true });
  return failure;
}
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
  return value;
}
function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}

function validateStartDiagnostic(value, loaded) {
  exactKeys(value, ["schemaVersion", "operation", "launchBundleSha256", "containerId", "failure", "state", "logs", "privacy", "authority", "boundary"], "backend start diagnostic");
  if (value.schemaVersion !== 1 || value.operation !== "pixel-work-model-backend-start-diagnostic" || value.launchBundleSha256 !== loaded.prepared.launchBundleSha256 || !DOCKER_ID_RE.test(value.containerId ?? "") || value.boundary !== START_DIAGNOSTIC_BOUNDARY) fail("backend start diagnostic identity is invalid");
  exactKeys(value.failure, ["class", "message"], "backend start diagnostic failure");
  exactKeys(value.state, ["status", "running", "restarting", "oomKilled", "exitCode", "error", "startedAt", "finishedAt"], "backend start diagnostic state");
  exactKeys(value.logs, ["captureStatus", "tailLines", "stdout", "stderr"], "backend start diagnostic logs");
  if (![value.failure.class, value.failure.message, value.state.status, value.state.error, value.state.startedAt, value.state.finishedAt].every((entry) => typeof entry === "string")
    || ![value.state.running, value.state.restarting, value.state.oomKilled].every((entry) => typeof entry === "boolean")
    || !(value.state.exitCode === null || Number.isSafeInteger(value.state.exitCode))
    || !["captured", "unavailable"].includes(value.logs.captureStatus) || value.logs.tailLines !== 400) fail("backend start diagnostic state is invalid");
  for (const name of ["stdout", "stderr"]) {
    const stream = value.logs[name];
    exactKeys(stream, ["text", "bytes", "sha256", "truncated"], `backend start diagnostic ${name}`);
    if (typeof stream.text !== "string" || !Number.isSafeInteger(stream.bytes) || stream.bytes < 0 || stream.bytes > 128 * 1024 || stream.bytes !== Buffer.byteLength(stream.text, "utf8") || stream.sha256 !== sha(stream.text) || typeof stream.truncated !== "boolean") fail(`backend start diagnostic ${name} is invalid`);
  }
  if (canonical(value.privacy) !== canonical({ ownerPrivateRequired: true, mayContainPaths: true, mayContainBackendMessages: true, promptsOrResponsesExpected: false, credentialsExpected: false })
    || canonical(value.authority) !== canonical({ grantsExecution: false, grantsNetwork: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false })) fail("backend start diagnostic privacy or authority is invalid");
  return structuredClone(value);
}

async function writeStartDiagnostic(loaded, rawDiagnostic) {
  const diagnostic = validateStartDiagnostic(rawDiagnostic, loaded);
  const path = join(loaded.configuration.evidenceRoot, START_DIAGNOSTIC_NAME);
  await ownerPrivate(path, "private backend start diagnostic", { absent: true, expectedOwnerUid: loaded.expectedOwnerUid });
  const bytes = Buffer.from(`${JSON.stringify(diagnostic, null, 2)}\n`, "utf8");
  if (bytes.length > MAX_JSON_BYTES) fail("private backend start diagnostic exceeds its size limit");
  const flags = constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_CLOEXEC ?? 0) | (constants.O_NOFOLLOW ?? 0);
  const handle = await open(path, flags, 0o600);
  try {
    await handle.writeFile(bytes); await handle.sync();
    const info = await handle.stat();
    if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || process.platform !== "win32" && (info.uid !== loaded.expectedOwnerUid || (info.mode & 0o077) !== 0)) fail("private backend start diagnostic was not retained safely");
  } finally { await handle.close(); }
  await ownerPrivate(path, "private backend start diagnostic", { expectedOwnerUid: loaded.expectedOwnerUid });
  return sha(bytes);
}

async function retainStartDiagnostic(error, loaded) {
  if (!error?.modelBackendStartDiagnostic) return error;
  try {
    const diagnosticSha256 = await writeStartDiagnostic(loaded, error.modelBackendStartDiagnostic);
    Object.defineProperty(error, "qualificationDiagnosticSha256", { value: diagnosticSha256, configurable: true });
    return error;
  } catch {
    return withFailureStage(new WorkModelQualificationDockerError("qualification backend start failed and its bounded private diagnostic could not be retained"), "backend-start");
  }
}

function carryDiagnosticSha256(error, source) {
  if (!CONFIRMATION_RE.test(source?.qualificationDiagnosticSha256 ?? "")) return error;
  try { Object.defineProperty(error, "qualificationDiagnosticSha256", { value: source.qualificationDiagnosticSha256, configurable: true }); } catch {}
  return error;
}

function boundedPrivateText(value, maximum) {
  const original = Buffer.from(typeof value === "string" ? value : "", "utf8");
  let start = Math.max(0, original.length - maximum);
  while (start < original.length && (original[start] & 0xc0) === 0x80) start += 1;
  return original.subarray(start).toString("utf8");
}

async function retainQualificationFailureDiagnostic(error, loaded, {
  primaryFailure = error, containerCleanupFailure = false, backendCleanupFailure = false,
} = {}) {
  if (CONFIRMATION_RE.test(error?.qualificationDiagnosticSha256 ?? "") && !containerCleanupFailure && !backendCleanupFailure) return error;
  try {
    const path = join(loaded.configuration.evidenceRoot, FAILURE_DIAGNOSTIC_NAME);
    await ownerPrivate(path, "private qualification failure diagnostic", { absent: true, expectedOwnerUid: loaded.expectedOwnerUid });
    const value = {
      schemaVersion: 1,
      operation: "pixel-work-model-qualification-failure-diagnostic",
      qualificationOperationSha256: loaded.operationSha256,
      failureStage: typeof error?.qualificationFailureStage === "string" ? error.qualificationFailureStage : "qualification-preflight",
      primaryFailure: {
        class: boundedPrivateText(primaryFailure?.name, 128) || "Error",
        message: boundedPrivateText(primaryFailure?.message, 4096) || "local model qualification failed",
        stage: typeof primaryFailure?.qualificationFailureStage === "string" ? primaryFailure.qualificationFailureStage : null,
        backendStartDiagnosticSha256: CONFIRMATION_RE.test(primaryFailure?.qualificationDiagnosticSha256 ?? "") ? primaryFailure.qualificationDiagnosticSha256 : null,
      },
      cleanup: {
        qualificationContainerCleanupProven: !containerCleanupFailure,
        backendCleanupProven: !backendCleanupFailure,
      },
      privacy: { ownerPrivateRequired: true, mayContainPaths: true, mayContainFailureMessages: true, promptsOrResponsesExpected: false, credentialsExpected: false },
      authority: { grantsExecution: false, grantsNetwork: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false },
      boundary: FAILURE_DIAGNOSTIC_BOUNDARY,
    };
    const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    if (bytes.length > MAX_JSON_BYTES) return error;
    const flags = constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_CLOEXEC ?? 0) | (constants.O_NOFOLLOW ?? 0);
    const handle = await open(path, flags, 0o600);
    try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
    await ownerPrivate(path, "private qualification failure diagnostic", { expectedOwnerUid: loaded.expectedOwnerUid });
    Object.defineProperty(error, "qualificationDiagnosticSha256", { value: sha(bytes), configurable: true });
  } catch {}
  return error;
}

async function ownerPrivate(path, label, { directory = false, absent = false, expectedOwnerUid = process.geteuid?.() ?? 0 } = {}) {
  absolutePath(path, label);
  const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (absent) {
    if (info !== null) fail(`${label} must not already exist`);
    return null;
  }
  const actual = await realpath(path).catch(() => null);
  if (!info || !samePath(actual, path) || info.isSymbolicLink() || (directory ? !info.isDirectory() : !info.isFile() || info.nlink !== 1)) fail(`${label} is not real, singular, and of the expected type`);
  if (process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return info;
}

async function readPrivateJson(path, label, expectedOwnerUid = process.geteuid?.() ?? 0) {
  await ownerPrivate(path, label, { expectedOwnerUid });
  let record;
  try { record = await readBoundedRegularFile(path, MAX_JSON_BYTES, label); } catch { fail(`${label} could not be opened safely`); }
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${label} is not strict UTF-8`);
  let value;
  try { value = parseStrictJson(text, label); } catch { fail(`${label} is not strict JSON`); }
  return { value, bytes: record.bytes };
}

export function validateDockerQualificationConfiguration(value) {
  exactKeys(value, ["$schema", "schemaVersion", "backendConfigPath", "qualificationConfigPath", "runnerImageDigest", "evidenceRoot", "boundary"], "Docker qualification configuration");
  if (value.$schema !== CONFIG_SCHEMA || value.schemaVersion !== 1 || value.boundary !== CONFIG_BOUNDARY || !IMAGE_RE.test(value.runnerImageDigest ?? "")) fail("Docker qualification configuration contract is invalid");
  for (const field of ["backendConfigPath", "qualificationConfigPath", "evidenceRoot"]) absolutePath(value[field], `Docker qualification ${field}`);
  return structuredClone(value);
}

function modelIdentity(policy) {
  const local = policy?.localModel;
  if (!local || typeof local !== "object" || Array.isArray(local)) fail("private model policy identity is unavailable");
  return {
    provider: local.provider, id: local.id, modelArtifactSha256: local.modelArtifactSha256,
    backendImageDigest: local.imageDigest, backendVersion: local.backendVersion,
    acceleratorClass: local.acceleratorClass, promptContractSha256: local.promptContractSha256,
    toolSchemaSha256: local.toolSchemaSha256, contextWindow: local.contextWindow,
    supportsVision: local.supportsVision,
  };
}

export function validateDockerQualificationBinding(prepared, rawQualification) {
  const qualification = validateFixedModelQualificationConfig(rawQualification);
  const configured = prepared?.configuration;
  const artifact = prepared?.artifactManifest;
  if (
    qualification.backendOrigin !== "http://127.0.0.1:18081"
    || canonical(qualification.model) !== canonical(modelIdentity(prepared?.policy ?? {}))
    || artifact?.artifactSha256 !== qualification.model.modelArtifactSha256
    || configured?.publishLoopbackPort !== null || configured?.restartPolicy !== "no"
    || prepared?.policy?.localModel?.provider !== "vllm"
    || !(prepared?.policy?.runner?.imageRef === prepared?.policy?.runner?.imageDigest
      || prepared?.policy?.runner?.imageRef?.endsWith(`@${prepared?.policy?.runner?.imageDigest}`))
    || configured?.accelerator?.class !== qualification.model.acceleratorClass
  ) fail("qualification inputs differ from the exact private model backend");
  return qualification;
}

export function dockerQualificationOperation(prepared, dockerConfigSha256, qualificationConfigSha256, runnerImageDigest) {
  if (![dockerConfigSha256, qualificationConfigSha256, prepared?.launchBundleSha256].every((value) => CONFIRMATION_RE.test(value ?? "")) || !IMAGE_RE.test(runnerImageDigest ?? "")) fail("qualification operation binding is invalid");
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-model-qualification-docker",
    dockerConfigSha256,
    launchBundleSha256: prepared.launchBundleSha256,
    qualificationConfigSha256,
    runnerImageDigest,
    outputName: OUTPUT_NAME,
  });
}

export function dockerQualificationOperationSha256(prepared, dockerConfigSha256, qualificationConfigSha256, runnerImageDigest) {
  return sha(dockerQualificationOperation(prepared, dockerConfigSha256, qualificationConfigSha256, runnerImageDigest));
}

export function buildModelQualificationDockerArguments({ prepared, configuration, uid, gid, operationSha256 }) {
  if (!Number.isSafeInteger(uid) || uid < 1 || !Number.isSafeInteger(gid) || gid < 1 || !CONFIRMATION_RE.test(operationSha256 ?? "")) fail("qualification container identity is invalid");
  const network = prepared?.environment?.runtime?.backendNetworkName;
  if (typeof network !== "string" || !/^[a-z0-9][a-z0-9_.-]{0,127}$/u.test(network)) fail("qualification backend network is invalid");
  const name = `pixel-model-qualification-${operationSha256.slice(0, 12)}`;
  return [
    "run", "--rm", "--pull", "never", "--name", name,
    "--label", `${CONTAINER_LABEL}=${operationSha256}`,
    "--network", network, "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
    "--pids-limit", "64", "--memory", "512m", "--memory-swap", "512m", "--cpus", "2",
    "--ulimit", "nofile=128:128", "--ipc", "none", "--cgroupns", "private", "--stop-timeout", "3",
    "--log-driver", "none", "--restart", "no", "--user", `${uid}:${gid}`, "--workdir", "/tmp",
    "--tmpfs", `/tmp:rw,nosuid,nodev,noexec,size=128m,mode=0700,uid=${uid},gid=${gid}`,
    "--mount", `type=bind,src=${configuration.qualificationConfigPath},dst=${MODEL_QUALIFICATION_CONFIG_PATH},readonly`,
    "--mount", `type=bind,src=${configuration.evidenceRoot},dst=${dirname(MODEL_QUALIFICATION_OUTPUT_PATH)}`,
    "--entrypoint", "/opt/node/bin/node", configuration.runnerImageDigest,
    "/opt/pixel/deploy/work-controller/model-qualification-container.mjs",
  ];
}

async function dockerInspectMaybe(dockerPath, kind, target) {
  let stdout;
  try {
    ({ stdout } = await execute(dockerPath, [kind, "inspect", target], { encoding: "utf8", windowsHide: true, timeout: 15000, maxBuffer: 4 * 1024 * 1024, env: DOCKER_ENV }));
  } catch (error) {
    if (error?.code === 1 && /(?:no such (?:object|container|network|image)|(?:container|network|image) .* not found)/iu.test(error?.stderr ?? "")) return null;
    throw error;
  }
  let result;
  try { result = parseStrictJson(stdout, "qualification Docker inspection"); } catch { fail("qualification Docker inspection is invalid"); }
  if (!Array.isArray(result) || result.length !== 1) fail("qualification Docker inspection is invalid");
  return result[0];
}

async function dockerInspect(dockerPath, kind, target) {
  const result = await dockerInspectMaybe(dockerPath, kind, target);
  if (result === null) fail(`qualification Docker ${kind} is unavailable`);
  return result;
}

function validateQualificationContainerInspect(container, loaded) {
  const name = `pixel-model-qualification-${loaded.operationSha256.slice(0, 12)}`;
  if (
    !container || !DOCKER_ID_RE.test(container.Id ?? "") || container.Name !== `/${name}`
    || container.Image !== loaded.configuration.runnerImageDigest
    || container.Config?.Labels?.[CONTAINER_LABEL] !== loaded.operationSha256
    || container.HostConfig?.NetworkMode !== loaded.prepared.environment.runtime.backendNetworkName
  ) fail("qualification container cleanup identity differs from the exact operation");
  return container.Id;
}

async function dockerQualification(dockerPath, args) {
  try {
    return await execute(dockerPath, args, { encoding: "utf8", windowsHide: true, timeout: 4000000, maxBuffer: 1024 * 1024, env: DOCKER_ENV });
  } catch (error) {
    if (error?.code === 2 && typeof error.stdout === "string") return { stdout: error.stdout, stderr: error.stderr ?? "", qualificationFailed: true };
    throw error;
  }
}

function parseSummary(stdout) {
  const lines = String(stdout).split(/\r?\n/u).filter(Boolean);
  if (lines.length !== 1) fail("qualification container output is not one content-free result");
  let value;
  try { value = parseStrictJson(lines[0], "qualification container result"); } catch { fail("qualification container output is invalid"); }
  if (!value || !["qualified", "degraded", "failed"].includes(value.status) || !CONFIRMATION_RE.test(value.receiptSha256 ?? "")) fail("qualification container result is invalid");
  return value;
}

async function loadOperation(configPath, dependencies = {}) {
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail("qualification requires a non-root service identity");
  const loaded = await readPrivateJson(configPath, "private Docker qualification configuration", expectedOwnerUid);
  const configuration = validateDockerQualificationConfiguration(loaded.value);
  await Promise.all([
    ownerPrivate(configuration.backendConfigPath, "private model backend configuration", { expectedOwnerUid }),
    ownerPrivate(configuration.qualificationConfigPath, "private model qualification configuration", { expectedOwnerUid }),
    ownerPrivate(configuration.evidenceRoot, "private model qualification evidence root", { directory: true, expectedOwnerUid }),
    ownerPrivate(join(configuration.evidenceRoot, OUTPUT_NAME), "private model qualification output", { absent: true, expectedOwnerUid }),
  ]);
  if ((await readdir(configuration.evidenceRoot)).length !== 0) fail("private model qualification evidence root is not empty");
  const prepare = dependencies.prepareModelBackendLaunch ?? prepareModelBackendLaunch;
  const prepared = await prepare(configuration.backendConfigPath, expectedOwnerUid, dependencies.prepareDependencies ?? {});
  const inspectCoordinationRoot = dependencies.inspectCoordinationRoot ?? inspectModelBackendCoordinationRoot;
  if (typeof inspectCoordinationRoot !== "function") fail("qualification coordination-root preflight is unavailable");
  await inspectCoordinationRoot(prepared?.environment?.stateRoot, expectedOwnerUid);
  const qualificationLoaded = await readPrivateJson(configuration.qualificationConfigPath, "private model qualification configuration", expectedOwnerUid);
  const qualification = validateDockerQualificationBinding(prepared, qualificationLoaded.value);
  if (prepared.policy.runner.imageDigest !== configuration.runnerImageDigest) fail("qualification runner differs from the exact Work runner policy");
  const dockerConfigSha256 = sha(loaded.bytes);
  const qualificationConfigSha256 = sha(qualificationLoaded.bytes);
  const operation = dockerQualificationOperation(prepared, dockerConfigSha256, qualificationConfigSha256, configuration.runnerImageDigest);
  return { expectedOwnerUid, configuration, prepared, qualification, dockerConfigSha256, qualificationConfigSha256, operation, operationSha256: sha(operation) };
}

export async function prepareDockerModelQualificationMaintenance(configPath, dependencies = {}) {
  const loaded = await loadOperation(configPath, dependencies);
  const preparation = {
    schemaVersion: 1,
    qualificationOperationSha256: loaded.operationSha256,
    dockerPath: loaded.prepared.environment.runtime.dockerPath,
    backendContainerName: loaded.prepared.environment.runtime.backendContainerName,
    backendNetworkName: loaded.prepared.environment.runtime.backendNetworkName,
    qualificationContainerName: `pixel-model-qualification-${loaded.operationSha256.slice(0, 12)}`,
  };
  // Keep the fully measured preparation in-process for the immediately
  // confirmed maintenance run.  The symbol is module-private and
  // non-enumerable, so it cannot widen or leak through the public review.
  Object.defineProperty(preparation, PREPARED_MAINTENANCE_OPERATION, { value: loaded });
  return Object.freeze(preparation);
}

function lifecycleOptions(prepared, dependencies, intent, backendConfigPath) {
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  const prepare = dependencies.prepareModelBackendLaunch ?? prepareModelBackendLaunch;
  return {
    ...(dependencies.lifecycleOptions ?? {}),
    confirmation: modelBackendLifecycleSha256(intent, prepared.launchBundleSha256),
    // Start follows the full byte measurement performed immediately before
    // production maintenance and uses read-only model/seed mounts.  Repeating
    // that 150+ GiB read while service is stopped adds no mutation boundary;
    // the stop path still performs the full postflight before cleanup.
    postflight: intent === "start"
      ? async () => ({ launchBundleSha256: prepared.launchBundleSha256 })
      : () => prepare(backendConfigPath, expectedOwnerUid, dependencies.prepareDependencies ?? {}),
    coordinationOptions: { expectedOwnerUid },
  };
}

export async function reviewDockerModelQualification(configPath, dependencies = {}) {
  const loaded = await loadOperation(configPath, dependencies);
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-model-qualification-docker-review", status: "confirmation-required",
    qualificationOperationSha256: loaded.operationSha256,
    confirmation: { option: "--confirm-qualification-operation-sha256", sha256: loaded.operationSha256 },
    changes: { startsExactModelBackend: true, startsSyntheticQualificationContainer: true, writesNewPrivateReceipt: true, removesQualificationContainer: true, removesExactModelBackend: true, usesCredentials: false, usesExternalNetwork: false, changesPolicy: false, enablesDeepWork: false },
    authority: { grantsExecution: false, grantsCredentials: false, grantsExternalNetwork: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: REVIEW_BOUNDARY,
  });
}

export async function runDockerModelQualification(configPath, confirmation, dependencies = {}) {
  const reviewed = dependencies.reviewedMaintenancePreparation?.[PREPARED_MAINTENANCE_OPERATION] ?? null;
  const loaded = reviewed ?? await loadOperation(configPath, dependencies);
  if (!CONFIRMATION_RE.test(confirmation ?? "") || confirmation !== loaded.operationSha256) fail("confirmation differs from the exact qualification operation");
  const dockerPath = loaded.prepared.environment.runtime.dockerPath;
  const inspect = dependencies.inspectImage ?? ((image) => dockerInspect(dockerPath, "image", image));
  const image = await inspect(loaded.configuration.runnerImageDigest);
  validateImageInspect(image, loaded.configuration.runnerImageDigest, loaded.configuration.runnerImageDigest);
  const containerName = `pixel-model-qualification-${loaded.operationSha256.slice(0, 12)}`;
  const inspectContainer = dependencies.inspectContainer ?? ((target) => dockerInspectMaybe(dockerPath, "container", target));
  const inspectNetwork = dependencies.inspectNetwork ?? ((target) => dockerInspectMaybe(dockerPath, "network", target));
  if (await inspectContainer(containerName) !== null) fail("exact-named qualification container already exists");
  const [existingBackend, existingNetwork] = await Promise.all([
    inspectContainer(loaded.prepared.environment.runtime.backendContainerName),
    inspectNetwork(loaded.prepared.environment.runtime.backendNetworkName),
  ]);
  if (existingBackend !== null || existingNetwork !== null) fail("qualification requires an absent exact backend and private network");
  const uid = dependencies.expectedOwnerUid ?? process.geteuid?.();
  const gid = dependencies.expectedOwnerGid ?? process.getgid?.();
  const args = buildModelQualificationDockerArguments({ prepared: loaded.prepared, configuration: loaded.configuration, uid, gid, operationSha256: loaded.operationSha256 });
  const start = dependencies.startModelBackend ?? startModelBackend;
  const stop = dependencies.stopModelBackend ?? stopModelBackend;
  const invoke = dependencies.runQualificationContainer ?? ((commandArgs) => dockerQualification(dockerPath, commandArgs));
  const removeContainer = dependencies.removeQualificationContainer ?? (async (containerId) => {
    await execute(dockerPath, ["container", "rm", "--force", containerId], { encoding: "utf8", windowsHide: true, timeout: 60000, maxBuffer: 1024 * 1024, env: DOCKER_ENV });
  });
  let startAttempted = false;
  let qualificationAttempted = false;
  let result;
  let primaryFailure = null;
  let containerCleanupFailure = false;
  let backendCleanupFailure = false;
  let failureStage = "pre-start-guard";
  try {
    if (dependencies.preStartGuard !== undefined) {
      if (typeof dependencies.preStartGuard !== "function") fail("qualification pre-start guard is invalid");
      await dependencies.preStartGuard();
    }
    failureStage = "backend-start";
    startAttempted = true;
    const receipt = await start(loaded.prepared, lifecycleOptions(loaded.prepared, dependencies, "start", loaded.configuration.backendConfigPath));
    if (receipt?.state === "ready-already-running") fail("qualification model backend was not freshly started because it was already running");
    if (receipt?.state !== "ready-started" || receipt?.checks?.readinessPassed !== true) fail("qualification model backend was not freshly started");
    qualificationAttempted = true;
    failureStage = "qualification-runner";
    const execution = await invoke(args);
    const summary = parseSummary(execution.stdout);
    failureStage = "evidence-verification";
    const outputPath = join(loaded.configuration.evidenceRoot, OUTPUT_NAME);
    const receiptLoaded = await readPrivateJson(outputPath, "private model qualification receipt", loaded.expectedOwnerUid);
    const receiptErrors = validateWorkModelCapabilityReceipt(receiptLoaded.value);
    if (
      receiptErrors.length || canonical(receiptLoaded.value.model) !== canonical(loaded.qualification.model)
      || receiptLoaded.value.suite.casesSha256 !== fixedModelQualificationCasesSha256()
      || receiptLoaded.value.suite.evaluatorSha256 !== await fixedModelQualificationEvaluatorSha256()
      || modelCapabilityReceiptSha256(receiptLoaded.value) !== summary.receiptSha256
      || receiptLoaded.value.status !== summary.status
    ) fail("private model qualification receipt differs from the exact operation result");
    result = Object.freeze({
      schemaVersion: 1, operation: "pixel-work-model-qualification-docker-result", status: summary.status,
      qualificationOperationSha256: loaded.operationSha256, receiptSha256: summary.receiptSha256,
      casesPassed: receiptLoaded.value.observations.casesPassed, casesFailed: receiptLoaded.value.observations.casesFailed,
      eligibleProfiles: [...receiptLoaded.value.envelope.eligibleProfiles], exactUsage: receiptLoaded.value.envelope.exactUsage,
      backendFresh: true, privateNetwork: true, credentialsUsed: false, externalNetwork: false,
      authority: { grantsExecution: false, grantsCredentials: false, grantsExternalNetwork: false, grantsExternalEffects: false, grantsCompletion: false },
      boundary: RESULT_BOUNDARY,
    });
  } catch (error) {
    primaryFailure = withFailureStage(error, failureStage);
    if (failureStage === "backend-start") primaryFailure = await retainStartDiagnostic(primaryFailure, loaded);
  }
  if (qualificationAttempted) {
    try {
      const remaining = await inspectContainer(containerName);
      if (remaining) {
        const containerId = validateQualificationContainerInspect(remaining, loaded);
        await removeContainer(containerId);
        if (await inspectContainer(containerId) !== null) fail("qualification container remains after exact cleanup");
      }
    } catch { containerCleanupFailure = true; }
  }
  if (startAttempted) {
    try {
      const stopped = await stop(loaded.prepared, lifecycleOptions(loaded.prepared, dependencies, "stop", loaded.configuration.backendConfigPath));
      if (!["removed", "already-absent"].includes(stopped?.state)) fail("qualification model backend cleanup did not complete");
    } catch { backendCleanupFailure = true; }
  }
  if (containerCleanupFailure && backendCleanupFailure) throw await retainQualificationFailureDiagnostic(carryDiagnosticSha256(withFailureStage(new WorkModelQualificationDockerError("qualification container and exact model backend cleanup could not be proved"), "qualification-cleanup"), primaryFailure), loaded, { primaryFailure, containerCleanupFailure, backendCleanupFailure });
  if (containerCleanupFailure) throw await retainQualificationFailureDiagnostic(carryDiagnosticSha256(withFailureStage(new WorkModelQualificationDockerError("qualification container cleanup could not be proved"), "qualification-cleanup"), primaryFailure), loaded, { primaryFailure, containerCleanupFailure, backendCleanupFailure });
  if (backendCleanupFailure) throw await retainQualificationFailureDiagnostic(carryDiagnosticSha256(withFailureStage(new WorkModelQualificationDockerError("qualification failed and exact model backend cleanup could not be proved"), "qualification-cleanup"), primaryFailure), loaded, { primaryFailure, containerCleanupFailure, backendCleanupFailure });
  if (primaryFailure) throw await retainQualificationFailureDiagnostic(primaryFailure, loaded);
  return result;
}

function parseArguments(argv) {
  if (!Array.isArray(argv) || !["review", "run"].includes(argv[0])) fail("Usage: model-qualification-docker.mjs review --config PRIVATE_JSON | run --config PRIVATE_JSON --confirm-qualification-operation-sha256 HASH");
  const operation = argv[0];
  if (operation === "review" && argv.length === 3 && argv[1] === "--config" && argv[2]) return { operation, configPath: resolve(argv[2]), confirmation: null };
  if (operation === "run" && argv.length === 5 && argv[1] === "--config" && argv[2] && argv[3] === "--confirm-qualification-operation-sha256" && argv[4]) return { operation, configPath: resolve(argv[2]), confirmation: argv[4] };
  fail("Docker qualification arguments are incomplete, unknown, or duplicated");
}

export async function main(argv = process.argv.slice(2)) {
  const options = parseArguments(argv);
  const result = options.operation === "review"
    ? await reviewDockerModelQualification(options.configPath)
    : await runDockerModelQualification(options.configPath, options.confirmation);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (result.operation.endsWith("-result") && result.status !== "qualified") process.exitCode = 2;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-model-qualification-docker: ${error instanceof WorkModelQualificationDockerError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const modelQualificationDockerBoundaries = Object.freeze({ configuration: CONFIG_BOUNDARY, review: REVIEW_BOUNDARY, result: RESULT_BOUNDARY, startDiagnostic: START_DIAGNOSTIC_BOUNDARY, failureDiagnostic: FAILURE_DIAGNOSTIC_BOUNDARY });
export const modelQualificationDockerStartDiagnosticName = START_DIAGNOSTIC_NAME;
export const modelQualificationDockerFailureDiagnosticName = FAILURE_DIAGNOSTIC_NAME;
