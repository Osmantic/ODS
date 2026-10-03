import { execFile } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import {
  lstat, mkdir, open, readdir, rm,
} from "node:fs/promises";
import { posix } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { inferencePolicySha256 } from "../work-model-proxy/inference-policy.mjs";
import { validateBuilderPatchForApplication } from "./builder-volume.mjs";
import { withModelBackendCoordination } from "./model-backend-coordination.mjs";
import {
  buildBuilderNetworkCreate,
  buildDataLabNetworkCreate,
  buildModelBackendConnect,
  buildModelProxyConfig,
  buildModelProxyDockerCommand,
  buildScoutNetworkCreate,
  buildResearcherNetworkCreate,
  validateImageInspect,
  validateModelBackendInspect,
  validateModelBackendNetworkBindingLabels,
  validateModelBackendNetworkInspect,
  validateModelProxyInspect,
} from "./docker-boundary.mjs";
import {
  buildDataLabDockerCommand,
  buildDataLabExportDockerCommand,
  buildDataLabInventoryDockerCommand,
  buildDataLabPrompt,
  buildDataLabReplayDockerCommand,
  buildDataLabReplayKeeperCommand,
  buildDataLabReplayVolumeCreate,
  buildDataLabVolumeCreate,
  buildDataLabVolumeKeeperCommand,
  validateDataLabImageRuntime,
  validateDataLabKeeperInspect,
  validateDataLabVolumeInspect,
} from "./docker-data-lab.mjs";
import {
  BUILDER_EVIDENCE_RESERVE_BYTES,
  buildBuilderAgentConfig,
  buildBuilderContinuationApplyDockerCommand,
  buildBuilderDockerCommand,
  buildBuilderExportDockerCommand,
  buildBuilderInitDockerCommand,
  buildBuilderPrompt,
  buildBuilderVolumeCreate,
  buildBuilderVolumeKeeperCommand,
  validateBuilderPatchArtifact,
  validateBuilderCleanupRuntime,
  validateBuilderContinuationReceipt,
  validateBuilderVolumeInspect,
  validateBuilderVolumeKeeperInspect,
} from "./docker-builder.mjs";
import { buildScoutDockerCommand, buildScoutPrompt, validateScoutNetworkInspect } from "./docker-scout.mjs";
import { finalizeScoutReport, parseScoutReportProposal } from "./scout-report.mjs";
import {
  buildResearcherDockerCommand,
  buildResearcherCriticDockerCommand,
  buildResearcherInitDockerCommand,
  buildResearcherPrompt,
  buildResearcherRevisionPrompt,
  buildResearcherVolumeCreate,
  buildResearcherVolumeKeeperCommand,
  validateResearcherVolumeInspect,
  validateResearcherVolumeKeeperInspect,
} from "./docker-researcher.mjs";
import { runRpcSession } from "./rpc-client.mjs";
import { validateRpcLoopGuardReceipt } from "./rpc-loop-guard.mjs";
import { buildOmpModelRegistry } from "./omp-model-registry.mjs";
import { runIndependentBuilderVerification } from "./verifier-supervisor.mjs";
import { serveResearchToolQueue } from "../work-research-broker/research-service.mjs";
import { finalizeResearchReport, parseResearchReportProposal } from "../work-research-broker/report-finalizer.mjs";
import { verifyResearchReport } from "../work-research-broker/citation-verifier.mjs";
import { validateLocalResearchEndpoint } from "../work-research-broker/search.mjs";
import { finalizeDataReport, verifyDataReplay } from "./data-artifacts.mjs";
import { deriveProfileCapabilityRuntime } from "./profile-capability.mjs";
import {
  capabilityProfileServiceBoundary, cleanupCapabilityProfileService, initializeCapabilityProfileService, serveCapabilityProfileQueue,
} from "../work-controller/capability-profile-service.mjs";
import { buildResearchRevisionHistory, executeResearchRevisionReview, runResearchRevisionLoop } from "../work-controller/research-revision-review.mjs";

const SAFE_PATH_RE = /^\/[A-Za-z0-9._/-]+$/;
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,127}$/;
const ID_RE = /^[a-f0-9]{64}$/;
const IMAGE_RE = /^(?:sha256:[a-f0-9]{64}|[a-z0-9][a-z0-9._/:+-]{1,255}@sha256:[a-f0-9]{64})$/;
const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/;
const CLAIM_RE = /^workclaim-[0-9]{13}-[a-f0-9]{12}$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const DOCKER_ENV = Object.freeze({ HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8", DOCKER_CONFIG: "/nonexistent" });
const HEALTH_SCRIPT = 'fetch("http://127.0.0.1:8080/healthz",{headers:{connection:"close"}}).then(async r=>{await r.arrayBuffer();if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))';
const BUILDER_EVIDENCE_BOUNDARY = "Untrusted local Builder output only. Applying, merging, deploying, or claiming completion requires a separate verifier and operator authority.";
const CAPABILITY_SERVICE_AUTHORITY = Object.freeze({ grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const SCOUT_FAILURE_STAGES = new Set(["rpc-execution", "proxy-receipt", "proposal-parse", "proposal-contract", "evidence-finalization", "artifact-retention", "candidate-contract", "cleanup"]);
const BUILDER_FAILURE_STAGES = new Set(["profile-execution", "rpc-execution", "proxy-receipt", "artifact-retention", "candidate-contract", "cleanup"]);

export class DockerSupervisorError extends Error {}

function fail(message) {
  throw new DockerSupervisorError(message);
}

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}

export function bindKnowledgeToWorkerPrompt(basePrompt, prepared, knowledge) {
  if (knowledge === undefined) return basePrompt;
  const keys = ["prompt", "promptBytes", "promptSha256", "retrievalId", "queryContractSha256", "vaultHeadSha256", "resultCount", "jobId", "planSha256", "checkpointSha256", "untrustedText", "persistedInStatus", "boundary"];
  if (!knowledge || typeof knowledge !== "object" || Array.isArray(knowledge) || canonical(Object.keys(knowledge).sort()) !== canonical(keys.sort())) fail("worker knowledge context shape is invalid");
  const bytes = Buffer.byteLength(knowledge.prompt ?? "", "utf8");
  if (
    knowledge.jobId !== prepared.plan.jobId || knowledge.planSha256 !== sha(prepared.plan)
    || !SHA_RE.test(knowledge.promptSha256 ?? "") || knowledge.promptSha256 !== sha(knowledge.prompt)
    || knowledge.promptBytes !== bytes || bytes < 1 || bytes > 32768
    || !SHA_RE.test(knowledge.queryContractSha256 ?? "") || !SHA_RE.test(knowledge.vaultHeadSha256 ?? "") || !SHA_RE.test(knowledge.checkpointSha256 ?? "")
    || !/^workknowledgeretrieval-[0-9]{13}-[a-f0-9]{12}$/u.test(knowledge.retrievalId ?? "")
    || !Number.isSafeInteger(knowledge.resultCount) || knowledge.resultCount < 0 || knowledge.resultCount > 20
    || knowledge.untrustedText !== true || knowledge.persistedInStatus !== false
    || typeof knowledge.boundary !== "string" || !knowledge.boundary.startsWith("One exact checkpoint-bound local-vault retrieval")
  ) fail("worker knowledge context differs from its exact local attempt");
  const prompt = `${basePrompt}\n\n${knowledge.prompt}`;
  if (Buffer.byteLength(prompt, "utf8") > 65536) fail("worker prompt plus private knowledge exceeds the RPC prompt ceiling");
  return prompt;
}

function checkedLoopGuardReceipt(execution) {
  const errors = validateRpcLoopGuardReceipt(execution?.loopGuard, execution?.toolCalls);
  if (errors.length) fail(`RPC observation loop-guard receipt is invalid: ${errors[0]}`);
  return structuredClone(execution.loopGuard);
}

function safePath(value, label) {
  if (typeof value !== "string" || !posix.isAbsolute(value) || !SAFE_PATH_RE.test(value) || value.includes("//") || posix.normalize(value) !== value) {
    fail(`${label} is not a canonical safe Linux path`);
  }
  return value;
}

function safeName(value, label) {
  if (!NAME_RE.test(value ?? "")) fail(`${label} is invalid`);
  return value;
}

function positiveId(value, label) {
  if (!Number.isSafeInteger(value) || value < 1 || value > 2147483647) fail(`${label} is invalid`);
  return value;
}

function imageName(value, label) {
  if (!IMAGE_RE.test(value ?? "")) fail(`${label} is invalid`);
  return value;
}

function under(root, path, label) {
  if (path === root || !path.startsWith(`${root}/`)) fail(`${label} escapes the private runtime root`);
  return path;
}

export function deriveScoutDockerRuntime(prepared, claim, options) {
  if (!JOB_RE.test(prepared?.plan?.jobId ?? "") || !CLAIM_RE.test(claim?.claimId ?? "") || claim.jobId !== prepared.plan.jobId) fail("Scout runtime claim is invalid");
  const dockerPath = safePath(options?.dockerPath, "Docker client path");
  const stateRoot = safePath(options?.stateRoot, "runner state root");
  const suffix = claim.claimId.slice(-12);
  const runRoot = posix.join(stateRoot, "runs", claim.claimId);
  const resultsRoot = posix.join(stateRoot, "results");
  const outputDirectory = posix.join(resultsRoot, claim.claimId);
  const capability = deriveProfileCapabilityRuntime(prepared, claim, runRoot, options?.capability);
  const runtime = {
    dockerPath,
    stateRoot,
    runRoot,
    resultsRoot,
    outputDirectory,
    networkName: `pixel-work-net-${suffix}`,
    networkSubnet: options.networkSubnet,
    workerIp: options.workerIp,
    proxyIp: options.proxyIp,
    modelProxyName: `pixel-work-model-${suffix}`,
    modelAlias: "pixel-model",
    backendNetworkName: safeName(options.backendNetworkName, "model backend network name"),
    backendContainerName: safeName(options.backendContainerName, "model backend container name"),
    backendImageIdentifier: imageName(options.backendImageIdentifier ?? prepared.policy.localModel.imageRef, "model backend image identifier"),
    backendAlias: "pixel-local-model",
    proxyCidFile: posix.join(runRoot, "cids", "proxy.cid"),
    cidFile: posix.join(runRoot, "cids", "worker.cid"),
    proxyConfigPath: posix.join(runRoot, "config", "model-proxy.json"),
    modelRegistryPath: posix.join(runRoot, "config", "models.yml"),
    proxyReceiptDirectory: posix.join(runRoot, "receipts"),
    containerName: `pixel-work-scout-${suffix}`,
    imageIdentifier: imageName(options.imageIdentifier ?? prepared.policy.runner.imageRef, "runner image identifier"),
    modelId: prepared.plan.model.id,
    uid: positiveId(options.uid, "runner UID"),
    gid: positiveId(options.gid, "runner GID"), capability,
  };
  for (const [label, path] of [
    ["run root", runRoot], ["results root", resultsRoot], ["output directory", outputDirectory],
    ["proxy CID file", runtime.proxyCidFile], ["worker CID file", runtime.cidFile],
    ["proxy config", runtime.proxyConfigPath], ["OMP model registry", runtime.modelRegistryPath], ["proxy receipt directory", runtime.proxyReceiptDirectory],
  ]) under(stateRoot, path, label);
  if (prepared.cleanupOnly !== true) buildModelProxyConfig(prepared, claim, runtime);
  return Object.freeze(runtime);
}

export function deriveBuilderDockerRuntime(prepared, claim, options) {
  if (prepared?.plan?.profile !== "builder" || !JOB_RE.test(prepared.plan.jobId ?? "") || !CLAIM_RE.test(claim?.claimId ?? "") || claim.jobId !== prepared.plan.jobId) fail("Builder runtime claim is invalid");
  const dockerPath = safePath(options?.dockerPath, "Docker client path");
  const stateRoot = safePath(options?.stateRoot, "runner state root");
  const suffix = claim.claimId.slice(-12);
  const runRoot = posix.join(stateRoot, "runs", claim.claimId);
  const resultsRoot = posix.join(stateRoot, "results");
  const outputDirectory = posix.join(resultsRoot, claim.claimId);
  const evidenceReserveBytes = BUILDER_EVIDENCE_RESERVE_BYTES;
  const maxResultBytes = Math.min(16 * 1024 * 1024, Math.floor((prepared.lease.budgets.maxArtifactBytes - evidenceReserveBytes) / 8));
  const patchArtifactLimitBytes = prepared.lease.budgets.maxArtifactBytes - evidenceReserveBytes - Math.ceil(maxResultBytes * 4 / 3);
  const capability = deriveProfileCapabilityRuntime(prepared, claim, runRoot, options?.capability);
  const runtime = {
    dockerPath,
    stateRoot,
    runRoot,
    resultsRoot,
    outputDirectory,
    networkName: `pixel-work-net-${suffix}`,
    networkSubnet: options.networkSubnet,
    workerIp: options.workerIp,
    proxyIp: options.proxyIp,
    modelProxyName: `pixel-work-model-${suffix}`,
    modelAlias: "pixel-model",
    backendNetworkName: safeName(options.backendNetworkName, "model backend network name"),
    backendContainerName: safeName(options.backendContainerName, "model backend container name"),
    backendImageIdentifier: imageName(options.backendImageIdentifier ?? prepared.policy.localModel.imageRef, "model backend image identifier"),
    backendAlias: "pixel-local-model",
    proxyCidFile: posix.join(runRoot, "cids", "proxy.cid"),
    cidFile: posix.join(runRoot, "cids", "worker.cid"),
    keeperCidFile: posix.join(runRoot, "cids", "keeper.cid"),
    proxyConfigPath: posix.join(runRoot, "config", "model-proxy.json"),
    agentConfigPath: posix.join(runRoot, "config", "builder-config.json"),
    modelRegistryPath: posix.join(runRoot, "config", "models.yml"),
    proxyReceiptDirectory: posix.join(runRoot, "receipts"),
    containerName: `pixel-work-builder-${suffix}`,
    keeperName: `pixel-work-builder-keep-${suffix}`,
    volumeName: `pixel-work-builder-${suffix}`,
    volumeSizeBytes: Math.floor(prepared.lease.budgets.maxDiskBytes / 2),
    maxResultBytes,
    patchArtifactLimitBytes,
    imageIdentifier: imageName(options.imageIdentifier ?? prepared.policy.runner.imageRef, "runner image identifier"),
    modelId: prepared.plan.model.id,
    uid: positiveId(options.uid, "runner UID"),
    gid: positiveId(options.gid, "runner GID"), capability,
  };
  for (const [label, path] of [
    ["run root", runRoot], ["results root", resultsRoot], ["output directory", outputDirectory],
    ["proxy CID file", runtime.proxyCidFile], ["worker CID file", runtime.cidFile], ["keeper CID file", runtime.keeperCidFile],
    ["proxy config", runtime.proxyConfigPath], ["Builder agent config", runtime.agentConfigPath], ["OMP model registry", runtime.modelRegistryPath],
    ["proxy receipt directory", runtime.proxyReceiptDirectory],
  ]) under(stateRoot, path, label);
  if (prepared.cleanupOnly !== true) buildModelProxyConfig(prepared, claim, runtime);
  if (prepared.cleanupOnly === true) {
    validateBuilderCleanupRuntime(prepared, claim, runtime);
  } else {
    buildBuilderVolumeCreate(prepared, claim, runtime);
  }
  return Object.freeze(runtime);
}

export function deriveResearcherDockerRuntime(prepared, claim, options) {
  if (prepared?.plan?.profile !== "researcher" || !JOB_RE.test(prepared.plan.jobId ?? "") || !CLAIM_RE.test(claim?.claimId ?? "") || claim.jobId !== prepared.plan.jobId) fail("Researcher runtime claim is invalid");
  const dockerPath = safePath(options?.dockerPath, "Docker client path");
  const stateRoot = safePath(options?.stateRoot, "runner state root");
  const suffix = claim.claimId.slice(-12);
  const runRoot = posix.join(stateRoot, "runs", claim.claimId);
  const researchRoot = posix.join(runRoot, "research");
  const resultsRoot = posix.join(stateRoot, "results");
  const outputDirectory = posix.join(resultsRoot, claim.claimId);
  const capability = deriveProfileCapabilityRuntime(prepared, claim, runRoot, options?.capability);
  const runtime = {
    dockerPath,
    stateRoot,
    runRoot,
    resultsRoot,
    outputDirectory,
    researchRoot,
    requestDirectory: posix.join(researchRoot, "requests"),
    responseDirectory: posix.join(researchRoot, "responses"),
    researchStateRoot: posix.join(researchRoot, "state"),
    researchObjectRoot: posix.join(researchRoot, "objects"),
    researchCourierQueueRoot: safePath(options?.researchCourierQueueRoot, "Web Courier research queue"),
    researchEndpoint: validateLocalResearchEndpoint(options?.researchEndpoint),
    networkName: `pixel-work-net-${suffix}`,
    networkSubnet: options.networkSubnet,
    workerIp: options.workerIp,
    proxyIp: options.proxyIp,
    modelProxyName: `pixel-work-model-${suffix}`,
    modelAlias: "pixel-model",
    backendNetworkName: safeName(options.backendNetworkName, "model backend network name"),
    backendContainerName: safeName(options.backendContainerName, "model backend container name"),
    backendImageIdentifier: imageName(options.backendImageIdentifier ?? prepared.policy.localModel.imageRef, "model backend image identifier"),
    backendAlias: "pixel-local-model",
    proxyCidFile: posix.join(runRoot, "cids", "proxy.cid"),
    cidFile: posix.join(runRoot, "cids", "worker.cid"),
    keeperCidFile: posix.join(runRoot, "cids", "keeper.cid"),
    proxyConfigPath: posix.join(runRoot, "config", "model-proxy.json"),
    modelRegistryPath: posix.join(runRoot, "config", "models.yml"),
    proxyReceiptDirectory: posix.join(runRoot, "receipts"),
    containerName: `pixel-work-researcher-${suffix}`,
    keeperName: `pixel-work-researcher-keep-${suffix}`,
    volumeName: `pixel-work-researcher-${suffix}`,
    volumeSizeBytes: Math.floor(prepared.lease.budgets.maxDiskBytes / 2),
    maxResultBytes: Math.min(prepared.lease.budgets.maxArtifactBytes, 4 * 1024 * 1024),
    imageIdentifier: imageName(options.imageIdentifier ?? prepared.policy.runner.imageRef, "runner image identifier"),
    modelId: prepared.plan.model.id,
    uid: positiveId(options.uid, "runner UID"),
    gid: positiveId(options.gid, "runner GID"), capability,
  };
  for (const [label, path] of [
    ["run root", runRoot], ["results root", resultsRoot], ["output directory", outputDirectory], ["research root", researchRoot],
    ["research request queue", runtime.requestDirectory], ["research response queue", runtime.responseDirectory],
    ["research state root", runtime.researchStateRoot], ["research object root", runtime.researchObjectRoot],
    ["proxy CID file", runtime.proxyCidFile], ["worker CID file", runtime.cidFile], ["keeper CID file", runtime.keeperCidFile],
    ["proxy config", runtime.proxyConfigPath], ["OMP model registry", runtime.modelRegistryPath], ["proxy receipt directory", runtime.proxyReceiptDirectory],
  ]) under(stateRoot, path, label);
  if (prepared.cleanupOnly !== true) buildModelProxyConfig(prepared, claim, runtime);
  if (prepared.cleanupOnly !== true) buildResearcherVolumeCreate(prepared, claim, runtime);
  return Object.freeze(runtime);
}

export function deriveDataLabDockerRuntime(prepared, claim, options) {
  if (prepared?.plan?.profile !== "data-lab" || !JOB_RE.test(prepared.plan.jobId ?? "") || !CLAIM_RE.test(claim?.claimId ?? "") || claim.jobId !== prepared.plan.jobId) fail("Data Lab runtime claim is invalid");
  const dockerPath = safePath(options?.dockerPath, "Docker client path");
  const stateRoot = safePath(options?.stateRoot, "runner state root");
  const suffix = claim.claimId.slice(-12);
  const runRoot = posix.join(stateRoot, "runs", claim.claimId);
  const resultsRoot = posix.join(stateRoot, "results");
  const outputDirectory = posix.join(resultsRoot, claim.claimId);
  const volumeSizeBytes = Math.floor(prepared.lease.budgets.maxDiskBytes / 2);
  const capability = deriveProfileCapabilityRuntime(prepared, claim, runRoot, options?.capability);
  const runtime = {
    dockerPath, stateRoot, runRoot, resultsRoot, outputDirectory,
    networkName: `pixel-work-net-${suffix}`, networkSubnet: options.networkSubnet, workerIp: options.workerIp, proxyIp: options.proxyIp,
    modelProxyName: `pixel-work-model-${suffix}`, modelAlias: "pixel-model",
    backendNetworkName: safeName(options.backendNetworkName, "model backend network name"),
    backendContainerName: safeName(options.backendContainerName, "model backend container name"),
    backendImageIdentifier: imageName(options.backendImageIdentifier ?? prepared.policy.localModel.imageRef, "model backend image identifier"),
    backendAlias: "pixel-local-model",
    proxyCidFile: posix.join(runRoot, "cids", "proxy.cid"), cidFile: posix.join(runRoot, "cids", "worker.cid"),
    keeperCidFile: posix.join(runRoot, "cids", "keeper.cid"), replayKeeperCidFile: posix.join(runRoot, "cids", "replay-keeper.cid"),
    proxyConfigPath: posix.join(runRoot, "config", "model-proxy.json"), modelRegistryPath: posix.join(runRoot, "config", "models.yml"), proxyReceiptDirectory: posix.join(runRoot, "receipts"),
    containerName: `pixel-work-data-${suffix}`, keeperName: `pixel-work-data-keep-${suffix}`, replayKeeperName: `pixel-work-data-replay-keep-${suffix}`,
    volumeName: `pixel-work-data-${suffix}`, replayVolumeName: `pixel-work-data-replay-${suffix}`,
    volumeSizeBytes, replayVolumeSizeBytes: volumeSizeBytes,
    maxResultBytes: Math.min(prepared.lease.budgets.maxArtifactBytes, 4 * 1024 * 1024),
    imageIdentifier: imageName(options.imageIdentifier ?? prepared.policy.runner.imageRef, "runner image identifier"),
    modelId: prepared.plan.model.id, uid: positiveId(options.uid, "runner UID"), gid: positiveId(options.gid, "runner GID"), capability,
  };
  for (const [label, path] of [
    ["run root", runRoot], ["results root", resultsRoot], ["output directory", outputDirectory],
    ["proxy CID file", runtime.proxyCidFile], ["worker CID file", runtime.cidFile], ["keeper CID file", runtime.keeperCidFile],
    ["replay keeper CID file", runtime.replayKeeperCidFile], ["proxy config", runtime.proxyConfigPath], ["OMP model registry", runtime.modelRegistryPath], ["proxy receipt directory", runtime.proxyReceiptDirectory],
  ]) under(stateRoot, path, label);
  if (prepared.cleanupOnly !== true) buildModelProxyConfig(prepared, claim, runtime);
  if (prepared.cleanupOnly !== true) {
    buildDataLabVolumeCreate(prepared, claim, runtime);
    buildDataLabReplayVolumeCreate(prepared, claim, runtime);
  }
  return Object.freeze(runtime);
}

export function execDocker(command, args, options = {}) {
  safePath(command, "Docker client path");
  if (!Array.isArray(args) || !args.every((item) => typeof item === "string" && !item.includes("\0"))) fail("Docker argument vector is invalid");
  const timeout = options.timeoutMs ?? 30000;
  const maxBuffer = options.maxBuffer ?? 2 * 1024 * 1024;
  return new Promise((resolve, reject) => {
    execFile(command, args, {
      cwd: "/",
      env: DOCKER_ENV,
      timeout,
      maxBuffer,
      windowsHide: true,
      encoding: "utf8",
      shell: false,
    }, (error, stdout, stderr) => {
      if (error) {
        const failure = new DockerSupervisorError("Docker operation failed closed");
        failure.cause = error;
        failure.dockerExitCode = error.code;
        failure.dockerStderr = typeof stderr === "string" ? stderr : "";
        reject(failure);
        return;
      }
      resolve({ stdout, stderr });
    });
  });
}

async function invoke(executor, command, args, options) {
  const result = await executor(command, args, options);
  if (!result || typeof result.stdout !== "string" || typeof result.stderr !== "string") fail("Docker executor returned an invalid result");
  return result;
}

async function inspectOne(executor, dockerPath, kind, target) {
  const { stdout } = await invoke(executor, dockerPath, [kind, "inspect", target], { timeoutMs: 30000, maxBuffer: 4 * 1024 * 1024 });
  let decoded;
  try { decoded = JSON.parse(stdout); } catch { fail(`Docker ${kind} inspection is not JSON`); }
  if (!Array.isArray(decoded) || decoded.length !== 1 || !decoded[0] || typeof decoded[0] !== "object") fail(`Docker ${kind} inspection is not singular`);
  return decoded[0];
}

async function inspectMaybe(executor, dockerPath, kind, target) {
  try { return await inspectOne(executor, dockerPath, kind, target); } catch (error) {
    const docker29MissingNetwork = kind === "network"
      && (error?.dockerStderr ?? "").trim() === `Error response from daemon: network ${target} not found`;
    if (
      error instanceof DockerSupervisorError
      && error.dockerExitCode === 1
      && (/no such (?:object|container|network|image|volume)/i.test(error.dockerStderr ?? "") || docker29MissingNetwork)
    ) return null;
    throw error;
  }
}

async function privateDirectory(path, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`private directory is invalid: ${path}`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`private directory is not owner-only: ${path}`);
  return path;
}

async function prepareRunDirectories(runtime) {
  await privateDirectory(runtime.stateRoot);
  const runs = posix.join(runtime.stateRoot, "runs");
  await mkdir(runs, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(runs);
  await mkdir(runtime.runRoot, { mode: 0o700 });
  for (const name of ["cids", "config", "receipts"]) await privateDirectory(posix.join(runtime.runRoot, name), true);
}

function configuredCapabilityBoundary(runtime, options) {
  if (!runtime.capability) return null;
  const configured = options?.capability;
  if (!configured || typeof configured !== "object" || Array.isArray(configured)) fail("profile capability configuration disappeared before setup");
  return Object.freeze({
    serviceRoot: runtime.capability.serviceRoot,
    authorization: configured.authorization, catalog: configured.catalog, pack: configured.pack,
    expectedPackSha256: configured.expectedPackSha256, requestContext: configured.requestContext, runtime: configured.runtime,
  });
}

async function prepareCapabilityProfileBoundary(boundary, options) {
  boundary.capability = configuredCapabilityBoundary(boundary.runtime, options);
  if (!boundary.capability) return null;
  return initializeCapabilityProfileService(boundary.capability);
}

async function prepareBuilderOutputDirectory(runtime) {
  const expectedResults = posix.join(runtime.stateRoot, "results");
  const expectedOutput = posix.join(expectedResults, runtime.outputDirectory.split("/").at(-1));
  if (runtime.resultsRoot !== expectedResults || runtime.outputDirectory !== expectedOutput) fail("Builder result path differs from the private runtime layout");
  await mkdir(runtime.resultsRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(runtime.resultsRoot);
  await privateDirectory(runtime.outputDirectory, true);
}

async function prepareScoutOutputDirectory(runtime) {
  const expectedResults = posix.join(runtime.stateRoot, "results");
  const expectedOutput = posix.join(expectedResults, runtime.outputDirectory.split("/").at(-1));
  if (runtime.resultsRoot !== expectedResults || runtime.outputDirectory !== expectedOutput) fail("Scout result path differs from the private runtime layout");
  await mkdir(runtime.resultsRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(runtime.resultsRoot);
  await privateDirectory(runtime.outputDirectory, true);
}

async function prepareDataLabOutputDirectory(runtime) {
  const expectedResults = posix.join(runtime.stateRoot, "results");
  const expectedOutput = posix.join(expectedResults, runtime.outputDirectory.split("/").at(-1));
  if (runtime.resultsRoot !== expectedResults || runtime.outputDirectory !== expectedOutput) fail("Data Lab result path differs from the private runtime layout");
  await mkdir(runtime.resultsRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(runtime.resultsRoot);
  await privateDirectory(runtime.outputDirectory, true);
}

async function prepareResearcherDirectories(runtime) {
  const expectedResults = posix.join(runtime.stateRoot, "results");
  const expectedOutput = posix.join(expectedResults, runtime.outputDirectory.split("/").at(-1));
  const expectedResearch = posix.join(runtime.runRoot, "research");
  if (runtime.resultsRoot !== expectedResults || runtime.outputDirectory !== expectedOutput || runtime.researchRoot !== expectedResearch) fail("Researcher private runtime layout is invalid");
  await mkdir(runtime.resultsRoot, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(runtime.resultsRoot);
  await privateDirectory(runtime.outputDirectory, true);
  await privateDirectory(runtime.researchRoot, true);
  for (const path of [runtime.requestDirectory, runtime.responseDirectory, runtime.researchStateRoot, runtime.researchObjectRoot]) await privateDirectory(path, true);
  await privateDirectory(runtime.researchCourierQueueRoot);
}

async function writePrivateJson(path, value) {
  const handle = await open(path, "wx", 0o600);
  try {
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
  if (process.platform !== "win32") {
    const directory = await open(posix.dirname(path), constants.O_RDONLY);
    try { await directory.sync(); } finally { await directory.close(); }
  }
}

async function readContainerId(path) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size < 64 || info.size > 65) fail("Docker CID file is invalid");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("Docker CID file is not owner-only");
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat();
    if (opened.dev !== info.dev || opened.ino !== info.ino || opened.size !== info.size || opened.nlink !== 1) fail("Docker CID file changed while opening");
    const value = (await handle.readFile("ascii")).trim();
    if (!ID_RE.test(value)) fail("Docker CID file does not contain a full container ID");
    return value;
  } finally {
    await handle.close();
  }
}

function exactLabels(object, claim, role) {
  const labels = object?.Config?.Labels ?? object?.Labels ?? {};
  return labels["com.osmantic.pixel.work-claim"] === claim.claimId
    && labels["com.osmantic.pixel.work-job"] === claim.jobId
    && labels["com.osmantic.pixel.work-role"] === role;
}

async function waitForProxy(executor, runtime, proxyId) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      await invoke(executor, runtime.dockerPath, [
        "container", "exec", "--user", `${runtime.uid}:${runtime.gid}`, proxyId,
        "/opt/node/bin/node", "-e", HEALTH_SCRIPT,
      ], { timeoutMs: 3000, maxBuffer: 65536 });
      return;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  fail("model proxy did not become ready inside its runtime ceiling");
}

async function readProxyReceipt(prepared, claim, runtime, options = {}) {
  const path = posix.join(runtime.proxyReceiptDirectory, "model-proxy-receipt.json");
  const { text, details, afterDetails } = await readBoundedRegularText(path, 65536, "model proxy receipt");
  if (options.__testAfterDescriptorRead !== undefined) {
    if (typeof options.__testAfterDescriptorRead !== "function") fail("model proxy receipt test hook is invalid");
    await options.__testAfterDescriptorRead(path);
  }
  const current = await lstat(path).catch(() => null);
  const privateFile = (value, allowUnlinked = false) => value?.isFile() && !value.isSymbolicLink()
    && (value.nlink === 1 || (allowUnlinked && value.nlink === 0))
    && (process.platform === "win32" || (value.uid === process.geteuid() && (value.mode & 0o077) === 0));
  const descriptorStable = details.dev === afterDetails.dev && details.ino === afterDetails.ino
    && details.size === afterDetails.size && details.mtimeMs === afterDetails.mtimeMs && details.ctimeMs === afterDetails.ctimeMs
    && details.mode === afterDetails.mode && details.uid === afterDetails.uid;
  const pathStillNamesDescriptor = current?.dev === afterDetails.dev && current?.ino === afterDetails.ino;
  if (!pathStillNamesDescriptor
    && descriptorStable
    && privateFile(details, true)
    && privateFile(afterDetails, true)
    && privateFile(current)) {
    fail("model proxy receipt changed during atomic replacement");
  }
  if (!descriptorStable || !pathStillNamesDescriptor
    || !privateFile(details) || !privateFile(afterDetails) || !privateFile(current)
    || details.size !== Buffer.byteLength(text, "utf8") || current.size !== details.size) {
    fail("model proxy receipt is not a stable private single-link file");
  }
  let value;
  try { value = JSON.parse(text); } catch { fail("model proxy receipt is not JSON"); }
  const proxyConfig = buildModelProxyConfig(prepared, claim, runtime);
  const expectedInferencePolicySha256 = proxyConfig.inference === null ? null : inferencePolicySha256(proxyConfig.inference);
  const mismatches = [];
  for (const [field, actual, expected] of [
    ["schemaVersion", value?.schemaVersion, 1], ["jobId", value?.jobId, claim.jobId],
    ["claimId", value?.claimId, claim.claimId], ["planSha256", value?.planSha256, claim.planSha256],
    ["contentStored", value?.contentStored, false], ["credentialsExposed", value?.credentialsExposed, false],
    ["arbitraryNetwork", value?.arbitraryNetwork, false], ["externalEffects", value?.externalEffects, false],
    ["inferencePolicySha256", value?.inferencePolicySha256, expectedInferencePolicySha256],
    ["activeInference", value?.activeInference, false],
  ]) if (actual !== expected) mismatches.push(field);
  if (mismatches.length > 0) fail(`model proxy receipt differs from the lease boundary (fields=${mismatches.sort().join(",")})`);
  for (const [field, maximum] of [
    ["modelRequests", prepared.lease.budgets.maxModelRequests], ["inputTokens", prepared.lease.budgets.maxInputTokens],
    ["outputTokens", prepared.lease.budgets.maxOutputTokens], ["networkBytes", prepared.lease.budgets.maxNetworkBytes],
  ]) if (!Number.isSafeInteger(value[field]) || value[field] < 0 || value[field] > maximum) fail(`model proxy receipt exceeds ${field}`);
  return value;
}

async function waitForQuiescentProxyReceipt(prepared, claim, runtime, options = {}) {
  const attempts = options.attempts ?? 300;
  const delayMilliseconds = options.delayMilliseconds ?? 100;
  if (!Number.isSafeInteger(attempts) || attempts < 1 || attempts > 3000
    || !Number.isSafeInteger(delayMilliseconds) || delayMilliseconds < 1 || delayMilliseconds > 1000) {
    fail("model proxy receipt quiescence boundary is invalid");
  }
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await readProxyReceipt(prepared, claim, runtime, {
        __testAfterDescriptorRead: options.__testAfterDescriptorRead,
      });
    } catch (error) {
      if (!(error instanceof DockerSupervisorError)
        || !new Set([
          "model proxy receipt differs from the lease boundary (fields=activeInference)",
          "model proxy receipt changed during atomic replacement",
        ]).has(error.message)
        || attempt + 1 >= attempts) throw error;
      await new Promise((resolve) => setTimeout(resolve, delayMilliseconds));
    }
  }
  fail("model proxy receipt did not become quiescent");
}

async function readPrivateJson(path, maximumBytes, label, expectedSha256 = null) {
  const { text, details } = await readBoundedRegularText(path, maximumBytes, label);
  if (details.nlink !== 1 || details.size !== Buffer.byteLength(text, "utf8") || (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0))) fail(`${label} is not private and single-link`);
  const digest = sha(text);
  if (expectedSha256 !== null && digest !== expectedSha256) fail(`${label} differs from its content-addressed receipt`);
  let value;
  try { value = JSON.parse(text); } catch { fail(`${label} is not JSON`); }
  return { value, text, bytes: details.size, sha256: digest };
}

async function removeExactContainer(executor, runtime, claim, target, role) {
  const inspected = await inspectMaybe(executor, runtime.dockerPath, "container", target);
  if (!inspected) return false;
  if (!ID_RE.test(inspected.Id ?? "") || !exactLabels(inspected, claim, role)) fail(`refusing to remove an unbound ${role} container`);
  await invoke(executor, runtime.dockerPath, ["container", "rm", "--force", inspected.Id], { timeoutMs: 15000, maxBuffer: 65536 });
  return true;
}

async function cleanupWorker(executor, runtime, claim, role = "scout-worker") {
  let id = null;
  try { id = await readContainerId(runtime.cidFile); } catch { /* the client may have died before publishing a CID */ }
  return removeExactContainer(executor, runtime, claim, id ?? runtime.containerName, role);
}

async function removeExactBuilderVolume(boundary) {
  const { executor, runtime, claim, prepared } = boundary;
  const volume = await inspectMaybe(executor, runtime.dockerPath, "volume", runtime.volumeName);
  if (!volume) return false;
  validateBuilderVolumeInspect(volume, prepared, claim, runtime);
  const attached = await invoke(executor, runtime.dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `volume=${runtime.volumeName}`], { timeoutMs: 15000, maxBuffer: 65536 });
  if (attached.stdout.trim() !== "") fail("refusing to remove an attached Builder workspace volume");
  await invoke(executor, runtime.dockerPath, ["volume", "rm", runtime.volumeName], { timeoutMs: 15000, maxBuffer: 65536 });
  return true;
}

async function removeExactResearcherVolume(boundary) {
  const { executor, runtime, claim, prepared } = boundary;
  const volume = await inspectMaybe(executor, runtime.dockerPath, "volume", runtime.volumeName);
  if (!volume) return false;
  validateResearcherVolumeInspect(volume, prepared, claim, runtime);
  const attached = await invoke(executor, runtime.dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `volume=${runtime.volumeName}`], { timeoutMs: 15000, maxBuffer: 65536 });
  if (attached.stdout.trim() !== "") fail("refusing to remove an attached Researcher workspace volume");
  await invoke(executor, runtime.dockerPath, ["volume", "rm", runtime.volumeName], { timeoutMs: 15000, maxBuffer: 65536 });
  return true;
}

async function removeExactDataLabVolume(boundary, replay = false) {
  const { executor, runtime, claim, prepared } = boundary;
  const name = replay ? runtime.replayVolumeName : runtime.volumeName;
  const volume = await inspectMaybe(executor, runtime.dockerPath, "volume", name);
  if (!volume) return false;
  validateDataLabVolumeInspect(volume, prepared, claim, runtime, replay);
  const attached = await invoke(executor, runtime.dockerPath, ["container", "ls", "--all", "--quiet", "--filter", `volume=${name}`], { timeoutMs: 15000, maxBuffer: 65536 });
  if (attached.stdout.trim() !== "") fail("refusing to remove an attached Data Lab workspace volume");
  await invoke(executor, runtime.dockerPath, ["volume", "rm", name], { timeoutMs: 15000, maxBuffer: 65536 });
  return true;
}

async function cleanupBoundary(boundary) {
  const { executor, runtime, claim } = boundary;
  const isBuilder = boundary.profile === "builder";
  const isResearcher = boundary.profile === "researcher";
  const isDataLab = boundary.profile === "data-lab";
  const isScout = boundary.profile === "scout";
  const workerRole = isBuilder ? "builder-worker" : isResearcher ? "researcher-worker" : isDataLab ? "data-lab-worker" : "scout-worker";
  const networkRole = isBuilder ? "builder-network" : isResearcher ? "researcher-network" : isDataLab ? "data-lab-network" : "scout-network";
  const failures = [];
  if (!boundary.capability && await lstat(posix.join(runtime.runRoot, "capability")).then(() => true, () => false)) {
    fail("refusing cleanup because capability custody exists without its exact recovery authorization");
  }
  if (isBuilder) {
    try { await removeExactContainer(executor, runtime, claim, `${runtime.containerName}-init`, "builder-init"); } catch (error) { failures.push(error); }
    try { await removeExactContainer(executor, runtime, claim, `${runtime.containerName}-export`, "builder-export"); } catch (error) { failures.push(error); }
  }
  if (isResearcher) {
    try { await removeExactContainer(executor, runtime, claim, `${runtime.containerName}-init`, "researcher-init"); } catch (error) { failures.push(error); }
  }
  if (isDataLab) {
    for (const [suffix, role] of [["-export", "data-lab-export"], ["-replay", "data-lab-replay"], ["-inventory", "data-lab-inventory"]]) {
      try { await removeExactContainer(executor, runtime, claim, `${runtime.containerName}${suffix}`, role); } catch (error) { failures.push(error); }
    }
  }
  try { await cleanupWorker(executor, runtime, claim, workerRole); } catch (error) { failures.push(error); }
  if (isBuilder) {
    try { await removeExactContainer(executor, runtime, claim, boundary.keeperId ?? runtime.keeperName, "builder-volume-keeper"); } catch (error) { failures.push(error); }
  }
  if (isResearcher) {
    try { await removeExactContainer(executor, runtime, claim, boundary.keeperId ?? runtime.keeperName, "researcher-volume-keeper"); } catch (error) { failures.push(error); }
  }
  if (isDataLab) {
    try { await removeExactContainer(executor, runtime, claim, boundary.keeperId ?? runtime.keeperName, "data-lab-volume-keeper"); } catch (error) { failures.push(error); }
    try { await removeExactContainer(executor, runtime, claim, boundary.replayKeeperId ?? runtime.replayKeeperName, "data-lab-replay-keeper"); } catch (error) { failures.push(error); }
  }
  try { await removeExactContainer(executor, runtime, claim, boundary.proxyId ?? runtime.modelProxyName, "model-proxy"); } catch (error) { failures.push(error); }
  try {
    const network = await inspectMaybe(executor, runtime.dockerPath, "network", runtime.networkName);
    if (network) {
      if (!exactLabels(network, claim, networkRole) || Object.keys(network.Containers ?? {}).length !== 0) fail("refusing to remove a nonempty or unbound work network");
      await invoke(executor, runtime.dockerPath, ["network", "rm", network.Id], { timeoutMs: 15000, maxBuffer: 65536 });
    }
  } catch (error) { failures.push(error); }
  if (isBuilder) {
    try { await removeExactBuilderVolume(boundary); } catch (error) { failures.push(error); }
  }
  if (isResearcher) {
    try { await removeExactResearcherVolume(boundary); } catch (error) { failures.push(error); }
  }
  if (isDataLab) {
    try { await removeExactDataLabVolume(boundary, false); } catch (error) { failures.push(error); }
    try { await removeExactDataLabVolume(boundary, true); } catch (error) { failures.push(error); }
  }
  if (boundary.capability) {
    try { await cleanupCapabilityProfileService(boundary.capability); } catch (error) { failures.push(error); }
  }
  if (failures.length === 0 && boundary.removePrivateState !== false) {
    const expected = posix.join(runtime.stateRoot, "runs", claim.claimId);
    if (runtime.runRoot !== expected) fail("refusing to remove an unrecognized private run root");
    await rm(runtime.runRoot, { recursive: true, force: true });
    if (isBuilder && boundary.resultCreated === true && boundary.preserveResult !== true) {
      const expectedOutput = posix.join(runtime.stateRoot, "results", claim.claimId);
      if (runtime.outputDirectory !== expectedOutput) fail("refusing to remove an unrecognized Builder result root");
      await rm(runtime.outputDirectory, { recursive: true, force: true });
    }
    if (isResearcher && boundary.resultCreated === true && boundary.preserveResult !== true) {
      const expectedOutput = posix.join(runtime.stateRoot, "results", claim.claimId);
      if (runtime.outputDirectory !== expectedOutput) fail("refusing to remove an unrecognized Researcher result root");
      await rm(runtime.outputDirectory, { recursive: true, force: true });
    }
    if (isDataLab && boundary.resultCreated === true && boundary.preserveResult !== true) {
      const expectedOutput = posix.join(runtime.stateRoot, "results", claim.claimId);
      if (runtime.outputDirectory !== expectedOutput) fail("refusing to remove an unrecognized Data Lab result root");
      await rm(runtime.outputDirectory, { recursive: true, force: true });
    }
    if (isScout && boundary.resultCreated === true && boundary.preserveResult !== true) {
      const expectedOutput = posix.join(runtime.stateRoot, "results", claim.claimId);
      if (runtime.outputDirectory !== expectedOutput) fail("refusing to remove an unrecognized Scout result root");
      await rm(runtime.outputDirectory, { recursive: true, force: true });
    }
  }
  if (failures.length) throw new DockerSupervisorError(`Work cleanup failed closed at ${failures.length} boundary operation(s)`);
}

async function retryBoundedCleanup(operation, attempts = 3, delayMilliseconds = 100) {
  if (typeof operation !== "function" || !Number.isSafeInteger(attempts) || attempts < 1 || attempts > 5 || !Number.isSafeInteger(delayMilliseconds) || delayMilliseconds < 0 || delayMilliseconds > 1000) fail("cleanup retry policy is invalid");
  let failure;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      await operation();
      return true;
    } catch (error) {
      failure = error;
      if (attempt + 1 < attempts && delayMilliseconds > 0) await new Promise((resolveDelay) => setTimeout(resolveDelay, delayMilliseconds));
    }
  }
  throw failure;
}

export async function cleanupInterruptedProfileAttempt(prepared, claim, options) {
  const profile = prepared?.plan?.profile;
  const label = profile === "builder" ? "Builder" : profile === "researcher" ? "Researcher" : profile === "data-lab" ? "Data Lab" : profile === "scout" ? "Scout" : null;
  if (!label || prepared?.cleanupOnly !== true || !prepared.recoveryConsumption) fail("interrupted profile cleanup requires an exact cleanup-only preparation");
  if (canonical(claim) !== canonical(prepared.recoveryConsumption)) fail(`interrupted ${label} cleanup claim differs from its recovery preparation`);
  if (process.platform !== "linux" && options?.allowNonLinuxTests !== true) fail(`interrupted ${label} cleanup requires Linux`);
  if (process.platform === "linux" && (process.umask() & 0o077) !== 0o077) fail(`interrupted ${label} cleanup requires an owner-only process umask`);
  const executor = options?.executor ?? execDocker;
  let runtime;
  let proxyReceipt = null;
  let resultInfo;
  let retainedArtifacts = false;
  try {
    runtime = profile === "builder" ? deriveBuilderDockerRuntime(prepared, claim, options)
      : profile === "researcher" ? deriveResearcherDockerRuntime(prepared, claim, options)
        : profile === "data-lab" ? deriveDataLabDockerRuntime(prepared, claim, options)
          : deriveScoutDockerRuntime(prepared, claim, options);
    try { proxyReceipt = await readProxyReceipt(prepared, claim, runtime); } catch { /* missing usage is charged conservatively */ }
    resultInfo = runtime.outputDirectory ? await lstat(runtime.outputDirectory).catch((error) => {
      if (error?.code === "ENOENT") return null;
      throw error;
    }) : null;
    if (resultInfo) {
      if (!resultInfo.isDirectory() || resultInfo.isSymbolicLink()) fail("interrupted profile result root is unsafe");
      await privateDirectory(runtime.outputDirectory);
      retainedArtifacts = (await readdir(runtime.outputDirectory)).length > 0;
    }
  } catch (error) {
    const failure = new DockerSupervisorError(`interrupted ${label} recovery inspection failed closed`);
    failure.cause = error;
    failure.workRecoveryInconclusive = true;
    throw failure;
  }
  try {
    await cleanupBoundary({
      executor, runtime, prepared, claim, profile, proxyId: null, keeperId: null, replayKeeperId: null,
      capability: configuredCapabilityBoundary(runtime, options),
      removePrivateState: true, resultCreated: resultInfo !== null, preserveResult: retainedArtifacts,
    });
  } catch (error) {
    const failure = new DockerSupervisorError(`interrupted ${label} cleanup failed closed`);
    failure.cause = error;
    failure.workCleanupComplete = false;
    throw failure;
  }
  const elapsedSeconds = Math.ceil(Math.max(0, Date.now() - Date.parse(claim.claimedAt)) / 1000);
  return {
    resourcesRemoved: true,
    usage: {
      runtimeSeconds: Math.min(prepared.lease.budgets.maxRuntimeSeconds, elapsedSeconds),
      modelRequests: proxyReceipt?.modelRequests ?? prepared.lease.budgets.maxModelRequests,
      inputTokens: proxyReceipt?.inputTokens ?? prepared.lease.budgets.maxInputTokens,
      outputTokens: proxyReceipt?.outputTokens ?? prepared.lease.budgets.maxOutputTokens,
      networkBytes: proxyReceipt?.networkBytes ?? prepared.lease.budgets.maxNetworkBytes,
      artifactBytes: retainedArtifacts ? prepared.lease.budgets.maxArtifactBytes : 0,
      failures: 1,
    },
  };
}

export async function cleanupInterruptedBuilderAttempt(prepared, claim, options) {
  if (prepared?.plan?.profile !== "builder") fail("interrupted Builder cleanup requires a Builder preparation");
  return cleanupInterruptedProfileAttempt(prepared, claim, options);
}

function selectModelBackendPrepared(prepared, options) {
  const candidateBindings = options?.modelBackendBindings;
  if (candidateBindings !== undefined && (!candidateBindings || typeof candidateBindings !== "object" || Array.isArray(candidateBindings)
    || JSON.stringify(Object.keys(candidateBindings).sort()) !== JSON.stringify(["artifactSha256", "configurationSha256", "environmentSha256", "policySha256"].sort())
    || Object.values(candidateBindings).some((value) => !SHA_RE.test(value ?? ""))
    || candidateBindings.artifactSha256 !== prepared.policy.localModel.modelArtifactSha256)) fail("exact model backend runtime bindings are invalid");
  if (options?.requireExactModelBackendBindings === true && candidateBindings === undefined) fail("exact model backend runtime bindings are required");
  return candidateBindings === undefined ? prepared : { ...prepared, bindings: candidateBindings };
}

async function attachModelProxyToBackend(boundary, runnerImage, options, { networkCommand, exactNetworkRole = null, invalidNetworkIdMessage = null, networkMismatchMessage = null }) {
  const { executor, runtime, prepared, claim } = boundary;
  const coordinator = options?.modelBackendCoordinator ?? withModelBackendCoordination;
  if (typeof coordinator !== "function") fail("model backend coordination is unavailable");
  const candidateBindings = options?.modelBackendBindings;
  const backendPrepared = selectModelBackendPrepared(prepared, options);
  return coordinator(runtime.stateRoot, async () => {
    const backendImage = await inspectOne(executor, runtime.dockerPath, "image", runtime.backendImageIdentifier);
    validateImageInspect(backendImage, runtime.backendImageIdentifier, prepared.plan.model.backendImageDigest);
    const backend = await inspectOne(executor, runtime.dockerPath, "container", runtime.backendContainerName);
    validateModelBackendInspect(backend, backendImage, backendPrepared, runtime);
    const backendNetwork = await inspectOne(executor, runtime.dockerPath, "network", runtime.backendNetworkName);
    validateModelBackendNetworkInspect(backendNetwork, runtime, [{ id: backend.Id, name: runtime.backendContainerName }]);
    if (candidateBindings !== undefined) validateModelBackendNetworkBindingLabels(backendNetwork, backendPrepared);

    const createdNetwork = await invoke(executor, networkCommand.command, networkCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
    const createdNetworkId = exactNetworkRole === null ? null : createdNetwork.stdout.trim();
    if (exactNetworkRole !== null && !ID_RE.test(createdNetworkId)) fail(invalidNetworkIdMessage);
    const proxyCommand = buildModelProxyDockerCommand(prepared, claim, runtime);
    const launched = await invoke(executor, proxyCommand.command, proxyCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
    const stdoutId = launched.stdout.trim();
    const cidId = await readContainerId(runtime.proxyCidFile);
    if (!ID_RE.test(stdoutId) || stdoutId !== cidId) fail("model proxy container identifiers differ");
    boundary.proxyId = cidId;
    const proxy = await inspectOne(executor, runtime.dockerPath, "container", cidId);
    validateModelProxyInspect(proxy, runnerImage, prepared, claim, runtime, false);
    const jobNetwork = await inspectOne(executor, runtime.dockerPath, "network", runtime.networkName);
    validateScoutNetworkInspect(jobNetwork, runtime);
    if (exactNetworkRole !== null && (jobNetwork.Id !== createdNetworkId || !exactLabels(jobNetwork, claim, exactNetworkRole))) fail(networkMismatchMessage);
    await waitForProxy(executor, runtime, cidId);
    const connect = buildModelBackendConnect(cidId, runtime);
    await invoke(executor, connect.command, connect.args, { timeoutMs: 30000, maxBuffer: 65536 });
    const connectedProxy = await inspectOne(executor, runtime.dockerPath, "container", cidId);
    validateModelProxyInspect(connectedProxy, runnerImage, prepared, claim, runtime, true);
    const connectedBackendNetwork = await inspectOne(executor, runtime.dockerPath, "network", runtime.backendNetworkName);
    validateModelBackendNetworkInspect(connectedBackendNetwork, runtime, [
      { id: backend.Id, name: runtime.backendContainerName }, { id: cidId, name: runtime.modelProxyName },
    ]);
    await readProxyReceipt(prepared, claim, runtime);
    return Object.freeze({ backendImage, backend });
  }, options?.modelBackendCoordinationOptions ?? {});
}

export async function setupScoutDockerBoundary(prepared, claim, options) {
  if (process.platform !== "linux" && options?.allowNonLinuxTests !== true) fail("Docker Scout supervisor requires Linux");
  if (process.platform === "linux" && (process.umask() & 0o077) !== 0o077) fail("Docker Scout supervisor requires an owner-only process umask");
  const executor = options?.executor ?? execDocker;
  const runtime = deriveScoutDockerRuntime(prepared, claim, options);
  const boundary = {
    executor, runtime, prepared, claim, proxyId: null, profile: "scout",
    removePrivateState: true, preserveResult: false, resultCreated: false,
  };
  try {
    await prepareRunDirectories(runtime);
    await prepareCapabilityProfileBoundary(boundary, options);
    await prepareScoutOutputDirectory(runtime);
    boundary.resultCreated = true;
    const config = buildModelProxyConfig(prepared, claim, runtime);
    await writePrivateJson(runtime.proxyConfigPath, config);
    await writePrivateJson(runtime.modelRegistryPath, buildOmpModelRegistry(prepared, runtime));

    const runnerImage = await inspectOne(executor, runtime.dockerPath, "image", runtime.imageIdentifier);
    validateImageInspect(runnerImage, runtime.imageIdentifier, prepared.plan.isolation.runnerImageDigest);
    const networkCommand = buildScoutNetworkCreate(claim, runtime);
    const { backendImage, backend } = await attachModelProxyToBackend(boundary, runnerImage, options, { networkCommand });
    return Object.freeze({
      ...boundary, runnerImage, backendImage, backend,
      retainResult: () => { boundary.preserveResult = true; },
      cleanup: () => cleanupBoundary(boundary),
    });
  } catch (error) {
    let cleanupComplete = false;
    try {
      await cleanupBoundary(boundary);
      cleanupComplete = true;
    } catch { /* exact leaked resources retain evidence for cleanup-only recovery */ }
    const failure = error instanceof Error ? error : new DockerSupervisorError("Scout setup failed closed");
    failure.workCleanupComplete = cleanupComplete;
    throw failure;
  }
}

function parseExactJson(text, keys, label) {
  let value;
  try { value = JSON.parse(text); } catch { fail(`${label} is not JSON`); }
  if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) fail(`${label} shape is invalid`);
  return value;
}

function checkedCapabilityServiceReceipt(value, configured = null) {
  if (value === null) {
    if (configured) fail("configured capability service has no lifecycle receipt");
    return null;
  }
  const keys = ["schemaVersion", "operation", "status", "authorizationSha256", "catalogSha256", "settled", "events", "terminal", "enabled", "authority", "boundary"];
  if (
    !value || typeof value !== "object" || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(keys.sort())
    || value.schemaVersion !== 1 || value.operation !== "pixel-work-capability-profile-service" || value.status !== "stopped-disabled"
    || !SHA_RE.test(value.authorizationSha256 ?? "") || !SHA_RE.test(value.catalogSha256 ?? "")
    || !Number.isSafeInteger(value.settled) || value.settled < 0 || !Number.isSafeInteger(value.events) || value.events < 0
    || typeof value.terminal !== "boolean" || value.enabled !== false
    || canonical(value.authority) !== canonical(CAPABILITY_SERVICE_AUTHORITY) || value.boundary !== capabilityProfileServiceBoundary
  ) fail("capability service lifecycle receipt is invalid or not content-free");
  if (
    configured
    && (value.authorizationSha256 !== sha(configured.authorization) || value.catalogSha256 !== sha(configured.catalog))
  ) fail("capability service lifecycle receipt differs from the configured job authorization");
  return value;
}

async function readBuilderPatch(prepared, claim, runtime, exportReceipt) {
  const entries = await readdir(runtime.outputDirectory);
  if (JSON.stringify(entries) !== JSON.stringify(["builder-patch.json"])) fail("Builder exporter wrote an unexpected output set");
  const path = posix.join(runtime.outputDirectory, "builder-patch.json");
  const { text, details } = await readBoundedRegularText(path, runtime.patchArtifactLimitBytes, "Builder patch artifact");
  if (process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0 || details.nlink !== 1)) fail("Builder patch artifact is not private and single-link");
  const bytes = Buffer.byteLength(text, "utf8");
  const sha256 = createHash("sha256").update(text, "utf8").digest("hex");
  if (bytes !== details.size || exportReceipt.bytes !== bytes || exportReceipt.sha256 !== sha256) fail("Builder patch artifact differs from its exporter receipt");
  const value = parseExactJson(text, ["schemaVersion", "format", "jobId", "claimId", "planSha256", "workspaceSha256", "changes", "summary", "authority", "boundary"], "Builder patch artifact");
  validateBuilderPatchArtifact(value, prepared, claim, runtime, bytes);
  if (exportReceipt.changes !== value.changes.length) fail("Builder exporter receipt change count differs from the patch");
  return { path, bytes, sha256, changes: value.changes.length, summary: value.summary, value };
}

export async function recoverBuilderCandidate(prepared, claim, options) {
  if (prepared?.verificationOnly !== true || process.platform !== "linux" && options?.allowNonLinuxTests !== true) fail("Builder candidate recovery is verification-only and requires Linux");
  const stateRoot = safePath(options?.stateRoot, "runner state root");
  const outputDirectory = posix.join(stateRoot, "results", claim.claimId);
  await privateDirectory(posix.join(stateRoot, "results"));
  await privateDirectory(outputDirectory);
  const entries = (await readdir(outputDirectory)).sort();
  const expected = ["builder-evidence.json", "builder-patch.json"];
  const withVerification = [...expected, "independent-verification.json"].sort();
  if (JSON.stringify(entries) !== JSON.stringify(expected) && JSON.stringify(entries) !== JSON.stringify(withVerification)) {
    fail("retained Builder candidate contains an unexpected artifact set");
  }
  const evidencePath = posix.join(outputDirectory, "builder-evidence.json");
  const { text: evidenceText, details: evidenceDetails } = await readBoundedRegularText(
    evidencePath, prepared.lease.budgets.maxArtifactBytes, "retained Builder evidence",
  );
  if (
    evidenceDetails.nlink !== 1 || evidenceDetails.size !== Buffer.byteLength(evidenceText, "utf8")
    || (process.platform !== "win32" && (evidenceDetails.uid !== process.geteuid() || (evidenceDetails.mode & 0o077) !== 0))
  ) fail("retained Builder evidence is not private and single-link");
  let evidenceHeader;
  try { evidenceHeader = JSON.parse(evidenceText); } catch { fail("retained Builder evidence is not JSON"); }
  const capabilityEvidence = evidenceHeader?.format === "pixel-builder-evidence-v2";
  const evidence = parseExactJson(evidenceText, [
    "schemaVersion", "format", "jobId", "claimId", "planSha256", "workspaceSha256", "patch", "execution",
    "modelUsage", ...(capabilityEvidence ? ["capabilityService"] : []), "verification", "authority", "boundary",
  ], "retained Builder evidence");
  if (
    evidence.schemaVersion !== 1 || !["pixel-builder-evidence-v1", "pixel-builder-evidence-v2"].includes(evidence.format) || evidence.jobId !== claim.jobId
    || evidence.claimId !== claim.claimId || evidence.planSha256 !== claim.planSha256 || evidence.workspaceSha256 !== claim.workspaceSha256
    || JSON.stringify(evidence.verification) !== JSON.stringify({ required: true, status: "pending" })
    || JSON.stringify(evidence.authority) !== JSON.stringify({ sourceMutation: false, merge: false, deploy: false, externalEffects: false })
    || evidence.boundary !== BUILDER_EVIDENCE_BOUNDARY
  ) fail("retained Builder evidence binding is invalid");
  checkedCapabilityServiceReceipt(capabilityEvidence ? evidence.capabilityService : null, capabilityEvidence ? undefined : null);
  const patchReceipt = evidence.patch;
  if (
    !patchReceipt || JSON.stringify(Object.keys(patchReceipt).sort()) !== JSON.stringify(["bytes", "changes", "sha256"].sort())
    || !SHA_RE.test(patchReceipt.sha256 ?? "") || !Number.isSafeInteger(patchReceipt.bytes) || patchReceipt.bytes < 2
    || !Number.isSafeInteger(patchReceipt.changes) || patchReceipt.changes < 0 || patchReceipt.changes > 100000
  ) fail("retained Builder patch receipt is invalid");
  const execution = evidence.execution;
  if (!execution || JSON.stringify(Object.keys(execution).sort()) !== JSON.stringify([
    "reportBase64", "reportBytes", "reportSha256", "toolCalls", "observedTools", "protocolVersion",
    "durationMilliseconds", "stderrBytes", "stderrSha256", "loopGuard",
  ].sort())) fail("retained Builder execution receipt shape is invalid");
  let report;
  try {
    report = Buffer.from(execution.reportBase64, "base64");
    if (report.toString("base64") !== execution.reportBase64) throw new Error("noncanonical");
  } catch { fail("retained Builder report encoding is invalid"); }
  if (
    report.length !== execution.reportBytes || report.length > 16 * 1024 * 1024 || sha(report) !== execution.reportSha256
    || !Number.isSafeInteger(execution.toolCalls) || execution.toolCalls < 0 || execution.toolCalls > prepared.lease.budgets.maxToolCalls
    || !Array.isArray(execution.observedTools) || execution.observedTools.length > prepared.lease.budgets.maxToolCalls
    || !execution.observedTools.every((tool) => typeof tool === "string" && tool.length > 0 && tool.length <= 64)
    || execution.protocolVersion !== 2 || !Number.isSafeInteger(execution.durationMilliseconds) || execution.durationMilliseconds < 0
    || execution.durationMilliseconds > prepared.lease.budgets.maxRuntimeSeconds * 1000 + 30000
    || !Number.isSafeInteger(execution.stderrBytes) || execution.stderrBytes < 0 || execution.stderrBytes > 4 * 1024 * 1024
    || !SHA_RE.test(execution.stderrSha256 ?? "")
  ) fail("retained Builder execution receipt is invalid");
  checkedLoopGuardReceipt(execution);
  const modelUsage = evidence.modelUsage;
  if (!modelUsage || JSON.stringify(Object.keys(modelUsage).sort()) !== JSON.stringify(["inputTokens", "modelRequests", "networkBytes", "outputTokens"].sort())) fail("retained Builder model receipt shape is invalid");
  for (const [field, limit] of [
    ["modelRequests", "maxModelRequests"], ["inputTokens", "maxInputTokens"], ["outputTokens", "maxOutputTokens"], ["networkBytes", "maxNetworkBytes"],
  ]) if (!Number.isSafeInteger(modelUsage[field]) || modelUsage[field] < 0 || modelUsage[field] > prepared.lease.budgets[limit]) fail("retained Builder model usage exceeds its lease");

  const patchPath = posix.join(outputDirectory, "builder-patch.json");
  const { text: patchText, details: patchDetails } = await readBoundedRegularText(
    patchPath, prepared.lease.budgets.maxArtifactBytes, "retained Builder patch",
  );
  if (
    patchDetails.nlink !== 1 || patchDetails.size !== Buffer.byteLength(patchText, "utf8")
    || (process.platform !== "win32" && (patchDetails.uid !== process.geteuid() || (patchDetails.mode & 0o077) !== 0))
    || patchDetails.size !== patchReceipt.bytes || sha(patchText) !== patchReceipt.sha256
  ) fail("retained Builder patch differs from its evidence");
  let patch;
  try { patch = JSON.parse(patchText); } catch { fail("retained Builder patch is not JSON"); }
  validateBuilderPatchForApplication(patch, {
    jobId: claim.jobId, claimId: claim.claimId, planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256,
  }, {
    maxTreeBytes: Math.floor(prepared.lease.budgets.maxDiskBytes / 2),
    maxArtifactBytes: prepared.lease.budgets.maxArtifactBytes,
    immutablePathPrefixes: prepared.plan.verification.immutablePathPrefixes,
  });
  if (patch.changes.length !== patchReceipt.changes || patchDetails.size + evidenceDetails.size > prepared.lease.budgets.maxArtifactBytes) {
    fail("retained Builder candidate exceeds its artifact boundary");
  }
  return {
    text: report.toString("utf8"),
    durationMilliseconds: execution.durationMilliseconds,
    toolCalls: execution.toolCalls,
    observedTools: [...execution.observedTools],
    protocolVersion: execution.protocolVersion,
    stderrBytes: execution.stderrBytes,
    stderrSha256: execution.stderrSha256,
    loopGuard: structuredClone(execution.loopGuard),
    proxyReceipt: { ...modelUsage },
    capabilityService: capabilityEvidence ? structuredClone(evidence.capabilityService) : null,
    patch: { path: patchPath, bytes: patchDetails.size, sha256: patchReceipt.sha256, changes: patch.changes.length, summary: patch.summary, value: patch },
    evidence: { path: evidencePath, bytes: evidenceDetails.size, sha256: sha(evidenceText) },
    verificationArtifactPresent: entries.includes("independent-verification.json"),
  };
}

export async function setupBuilderDockerBoundary(prepared, claim, options) {
  if (prepared?.verificationOnly === true || prepared?.cleanupOnly === true) fail("recovery-only Builder preparation cannot launch a worker");
  if (process.platform !== "linux" && options?.allowNonLinuxTests !== true) fail("Docker Builder supervisor requires Linux");
  if (process.platform === "linux" && (process.umask() & 0o077) !== 0o077) fail("Docker Builder supervisor requires an owner-only process umask");
  const executor = options?.executor ?? execDocker;
  const runtime = deriveBuilderDockerRuntime(prepared, claim, options);
  const boundary = {
    executor, runtime, prepared, claim, proxyId: null, keeperId: null, profile: "builder",
    removePrivateState: true, preserveResult: false, resultCreated: false,
  };
  try {
    await prepareRunDirectories(runtime);
    await prepareCapabilityProfileBoundary(boundary, options);
    await prepareBuilderOutputDirectory(runtime);
    boundary.resultCreated = true;
    const config = buildModelProxyConfig(prepared, claim, runtime);
    await writePrivateJson(runtime.proxyConfigPath, config);
    await writePrivateJson(runtime.agentConfigPath, buildBuilderAgentConfig(prepared, claim, runtime));
    await writePrivateJson(runtime.modelRegistryPath, buildOmpModelRegistry(prepared, runtime));

    const runnerImage = await inspectOne(executor, runtime.dockerPath, "image", runtime.imageIdentifier);
    validateImageInspect(runnerImage, runtime.imageIdentifier, prepared.plan.isolation.runnerImageDigest);
    const volumeCommand = buildBuilderVolumeCreate(prepared, claim, runtime);
    const createdVolume = await invoke(executor, volumeCommand.command, volumeCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
    if (createdVolume.stdout.trim() !== runtime.volumeName) fail("Docker created a different Builder workspace volume");
    const volume = await inspectOne(executor, runtime.dockerPath, "volume", runtime.volumeName);
    validateBuilderVolumeInspect(volume, prepared, claim, runtime);

    const keeperCommand = buildBuilderVolumeKeeperCommand(prepared, claim, runtime);
    const keeperLaunch = await invoke(executor, keeperCommand.command, keeperCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
    const keeperStdoutId = keeperLaunch.stdout.trim();
    const keeperCidId = await readContainerId(runtime.keeperCidFile);
    if (!ID_RE.test(keeperStdoutId) || keeperStdoutId !== keeperCidId) fail("Builder volume keeper identifiers differ");
    boundary.keeperId = keeperCidId;
    const keeper = await inspectOne(executor, runtime.dockerPath, "container", keeperCidId);
    validateBuilderVolumeKeeperInspect(keeper, runnerImage, prepared, claim, runtime);

    let initReceipt = null;
    let continuationReceipt = null;
    if (prepared.continuationBase) {
      const continuationCommand = buildBuilderContinuationApplyDockerCommand(prepared, claim, runtime);
      const applied = await invoke(executor, continuationCommand.command, continuationCommand.args, { timeoutMs: 120000, maxBuffer: 65536 });
      continuationReceipt = parseExactJson(
        applied.stdout.trim(), ["schemaVersion", "operation", "changes", "files", "bytes", "candidateSha256"], "Builder continuation receipt",
      );
      validateBuilderContinuationReceipt(continuationReceipt, prepared, claim, runtime);
    } else {
      const initCommand = buildBuilderInitDockerCommand(prepared, claim, runtime);
      const initialized = await invoke(executor, initCommand.command, initCommand.args, { timeoutMs: 120000, maxBuffer: 65536 });
      initReceipt = parseExactJson(initialized.stdout.trim(), ["schemaVersion", "operation", "copiedBytes", "copiedEntries"], "Builder initialization receipt");
      if (
        initReceipt.schemaVersion !== 1 || initReceipt.operation !== "builder-volume-init"
        || !Number.isSafeInteger(initReceipt.copiedBytes) || initReceipt.copiedBytes < 0 || initReceipt.copiedBytes > runtime.volumeSizeBytes
        || !Number.isSafeInteger(initReceipt.copiedEntries) || initReceipt.copiedEntries < 0 || initReceipt.copiedEntries > 100000
      ) fail("Builder initialization receipt exceeds its boundary");
    }
    const initializedVolume = await inspectOne(executor, runtime.dockerPath, "volume", runtime.volumeName);
    validateBuilderVolumeInspect(initializedVolume, prepared, claim, runtime);

    const networkCommand = buildBuilderNetworkCreate(claim, runtime);
    const { backendImage, backend } = await attachModelProxyToBackend(boundary, runnerImage, options, { networkCommand });
    return Object.freeze({
      ...boundary, runnerImage, backendImage, backend, volume, keeper, initReceipt, continuationReceipt,
      retainResult: () => { boundary.preserveResult = true; },
      cleanup: () => cleanupBoundary(boundary),
    });
  } catch (error) {
    let cleanupComplete = false;
    try {
      await cleanupBoundary(boundary);
      cleanupComplete = true;
    } catch { /* exact leaked resources retain evidence for cleanup-only recovery */ }
    const failure = error instanceof Error ? error : new DockerSupervisorError("Builder setup failed closed");
    failure.workCleanupComplete = cleanupComplete;
    if (!BUILDER_FAILURE_STAGES.has(failure.workFailureStage)) failure.workFailureStage = cleanupComplete ? "profile-execution" : "cleanup";
    throw failure;
  }
}

export async function setupDataLabDockerBoundary(prepared, claim, options) {
  if (process.platform !== "linux" && options?.allowNonLinuxTests !== true) fail("Docker Data Lab supervisor requires Linux");
  if (process.platform === "linux" && (process.umask() & 0o077) !== 0o077) fail("Docker Data Lab supervisor requires an owner-only process umask");
  const executor = options?.executor ?? execDocker;
  const runtime = deriveDataLabDockerRuntime(prepared, claim, options);
  const boundary = {
    executor, runtime, prepared, claim, proxyId: null, keeperId: null, replayKeeperId: null, profile: "data-lab",
    removePrivateState: true, preserveResult: false, resultCreated: false,
  };
  try {
    await prepareRunDirectories(runtime);
    await prepareCapabilityProfileBoundary(boundary, options);
    await prepareDataLabOutputDirectory(runtime);
    boundary.resultCreated = true;
    await writePrivateJson(runtime.proxyConfigPath, buildModelProxyConfig(prepared, claim, runtime));
    await writePrivateJson(runtime.modelRegistryPath, buildOmpModelRegistry(prepared, runtime));

    const runnerImage = await inspectOne(executor, runtime.dockerPath, "image", runtime.imageIdentifier);
    validateImageInspect(runnerImage, runtime.imageIdentifier, prepared.plan.isolation.runnerImageDigest);
    validateDataLabImageRuntime(runnerImage, prepared);
    for (const replay of [false, true]) {
      const volumeCommand = replay ? buildDataLabReplayVolumeCreate(prepared, claim, runtime) : buildDataLabVolumeCreate(prepared, claim, runtime);
      const name = replay ? runtime.replayVolumeName : runtime.volumeName;
      const created = await invoke(executor, volumeCommand.command, volumeCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
      if (created.stdout.trim() !== name) fail("Docker created a different Data Lab workspace volume");
      const volume = await inspectOne(executor, runtime.dockerPath, "volume", name);
      validateDataLabVolumeInspect(volume, prepared, claim, runtime, replay);
      const keeperCommand = replay ? buildDataLabReplayKeeperCommand(prepared, claim, runtime) : buildDataLabVolumeKeeperCommand(prepared, claim, runtime);
      const launched = await invoke(executor, keeperCommand.command, keeperCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
      const cidPath = replay ? runtime.replayKeeperCidFile : runtime.keeperCidFile;
      const stdoutId = launched.stdout.trim();
      const cidId = await readContainerId(cidPath);
      if (!ID_RE.test(stdoutId) || stdoutId !== cidId) fail("Data Lab volume keeper identifiers differ");
      if (replay) boundary.replayKeeperId = cidId; else boundary.keeperId = cidId;
      const keeper = await inspectOne(executor, runtime.dockerPath, "container", cidId);
      validateDataLabKeeperInspect(keeper, runnerImage, prepared, claim, runtime, replay);
    }

    const networkCommand = buildDataLabNetworkCreate(claim, runtime);
    const { backendImage, backend } = await attachModelProxyToBackend(boundary, runnerImage, options, {
      networkCommand, exactNetworkRole: "data-lab-network",
      invalidNetworkIdMessage: "Docker returned an invalid Data Lab network identifier",
      networkMismatchMessage: "Data Lab job network differs from its exact claim",
    });
    return Object.freeze({
      ...boundary, runnerImage, backendImage, backend,
      retainResult: () => { boundary.preserveResult = true; },
      cleanup: () => cleanupBoundary(boundary),
    });
  } catch (error) {
    const failure = error instanceof Error ? error : new DockerSupervisorError("Data Lab setup failed closed");
    try {
      await retryBoundedCleanup(() => cleanupBoundary(boundary));
      failure.workCleanupComplete = true;
    } catch (cleanupFailure) {
      failure.workCleanupComplete = false;
      failure.workCleanupFailure = cleanupFailure;
    }
    throw failure;
  }
}

export async function setupResearcherDockerBoundary(prepared, claim, options) {
  if (process.platform !== "linux" && options?.allowNonLinuxTests !== true) fail("Docker Researcher supervisor requires Linux");
  if (process.platform === "linux" && (process.umask() & 0o077) !== 0o077) fail("Docker Researcher supervisor requires an owner-only process umask");
  const executor = options?.executor ?? execDocker;
  const runtime = deriveResearcherDockerRuntime(prepared, claim, options);
  const boundary = {
    executor, runtime, prepared, claim, proxyId: null, keeperId: null, profile: "researcher",
    removePrivateState: true, preserveResult: false, resultCreated: false,
  };
  try {
    await prepareRunDirectories(runtime);
    await prepareCapabilityProfileBoundary(boundary, options);
    await prepareResearcherDirectories(runtime);
    boundary.resultCreated = true;
    const config = buildModelProxyConfig(prepared, claim, runtime);
    await writePrivateJson(runtime.proxyConfigPath, config);
    await writePrivateJson(runtime.modelRegistryPath, buildOmpModelRegistry(prepared, runtime));

    const runnerImage = await inspectOne(executor, runtime.dockerPath, "image", runtime.imageIdentifier);
    validateImageInspect(runnerImage, runtime.imageIdentifier, prepared.plan.isolation.runnerImageDigest);
    const volumeCommand = buildResearcherVolumeCreate(prepared, claim, runtime);
    const createdVolume = await invoke(executor, volumeCommand.command, volumeCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
    if (createdVolume.stdout.trim() !== runtime.volumeName) fail("Docker created a different Researcher workspace volume");
    const volume = await inspectOne(executor, runtime.dockerPath, "volume", runtime.volumeName);
    validateResearcherVolumeInspect(volume, prepared, claim, runtime);

    const keeperCommand = buildResearcherVolumeKeeperCommand(prepared, claim, runtime);
    const keeperLaunch = await invoke(executor, keeperCommand.command, keeperCommand.args, { timeoutMs: 30000, maxBuffer: 65536 });
    const keeperStdoutId = keeperLaunch.stdout.trim();
    const keeperCidId = await readContainerId(runtime.keeperCidFile);
    if (!ID_RE.test(keeperStdoutId) || keeperStdoutId !== keeperCidId) fail("Researcher volume keeper identifiers differ");
    boundary.keeperId = keeperCidId;
    const keeper = await inspectOne(executor, runtime.dockerPath, "container", keeperCidId);
    validateResearcherVolumeKeeperInspect(keeper, runnerImage, prepared, claim, runtime);

    const initCommand = buildResearcherInitDockerCommand(prepared, claim, runtime);
    const initialized = await invoke(executor, initCommand.command, initCommand.args, { timeoutMs: 120000, maxBuffer: 65536 });
    const initReceipt = parseExactJson(initialized.stdout.trim(), ["schemaVersion", "operation", "copiedBytes", "copiedEntries"], "Researcher initialization receipt");
    if (
      initReceipt.schemaVersion !== 1 || initReceipt.operation !== "builder-volume-init"
      || !Number.isSafeInteger(initReceipt.copiedBytes) || initReceipt.copiedBytes < 0 || initReceipt.copiedBytes > runtime.volumeSizeBytes
      || !Number.isSafeInteger(initReceipt.copiedEntries) || initReceipt.copiedEntries < 0 || initReceipt.copiedEntries > 100000
    ) fail("Researcher initialization receipt exceeds its boundary");
    const initializedVolume = await inspectOne(executor, runtime.dockerPath, "volume", runtime.volumeName);
    validateResearcherVolumeInspect(initializedVolume, prepared, claim, runtime);

    const networkCommand = buildResearcherNetworkCreate(claim, runtime);
    const { backendImage, backend } = await attachModelProxyToBackend(boundary, runnerImage, options, {
      networkCommand, exactNetworkRole: "researcher-network",
      invalidNetworkIdMessage: "Docker returned an invalid Researcher network identifier",
      networkMismatchMessage: "Researcher job network differs from its exact claim",
    });
    return Object.freeze({
      ...boundary, runnerImage, backendImage, backend, volume, keeper, initReceipt,
      retainResult: () => { boundary.preserveResult = true; },
      cleanup: () => cleanupBoundary(boundary),
    });
  } catch (error) {
    const failure = error instanceof Error ? error : new DockerSupervisorError("Researcher setup failed closed");
    try {
      await retryBoundedCleanup(() => cleanupBoundary(boundary));
      failure.workCleanupComplete = true;
    } catch (cleanupFailure) {
      failure.workCleanupComplete = false;
      failure.workCleanupFailure = cleanupFailure;
    }
    throw failure;
  }
}

async function runRpcWithCapability(boundary, options, workerRole, operation) {
  if (typeof operation !== "function") fail("profile RPC operation is invalid");
  if (!boundary.capability) return { execution: await operation(), capabilityService: null };
  const serviceRunner = options?.capabilityService ?? serveCapabilityProfileQueue;
  if (typeof serviceRunner !== "function") fail("profile capability service runner is invalid");
  const controller = new AbortController();
  const servicePromise = Promise.resolve().then(() => serviceRunner({
    ...boundary.capability, signal: controller.signal,
    pollMilliseconds: options?.capabilityPollMilliseconds ?? 25,
    dependencies: options?.capabilityDependencies ?? {},
  }));
  const rpcPromise = Promise.resolve().then(operation);
  const first = await Promise.race([
    rpcPromise.then((value) => ({ kind: "rpc", value }), (error) => ({ kind: "rpc-error", error })),
    servicePromise.then((value) => ({ kind: "service-exit", value }), (error) => ({ kind: "service-error", error })),
  ]);
  if (first.kind === "service-exit" || first.kind === "service-error") {
    controller.abort();
    await cleanupWorker(boundary.executor, boundary.runtime, boundary.claim, workerRole).catch(() => {});
    await rpcPromise.catch(() => {});
    if (first.kind === "service-error") throw first.error;
    fail("capability service exited before its OMP worker");
  }
  controller.abort();
  let capabilityService;
  try { capabilityService = await servicePromise; } catch (error) {
    if (first.kind === "rpc-error") throw first.error;
    throw error;
  }
  if (first.kind === "rpc-error") throw first.error;
  return { execution: first.value, capabilityService: checkedCapabilityServiceReceipt(capabilityService, boundary.capability) };
}

export async function runBuilderCandidateLifecycle(prepared, claim, options) {
  const boundary = await setupBuilderDockerBoundary(prepared, claim, options);
  const startedAt = Date.now();
  let result;
  let primaryError;
  let failureStage = "rpc-execution";
  try {
    const command = buildBuilderDockerCommand(prepared, claim, boundary.runtime);
    const worker = await runRpcWithCapability(boundary, options, "builder-worker", () => runRpcSession({
      command: command.command,
      args: command.args,
      cwd: "/",
      env: command.env,
      prompt: bindKnowledgeToWorkerPrompt(buildBuilderPrompt(prepared.plan), prepared, options?.knowledge),
      allowedTools: command.allowedTools,
      requiredInitialTool: "read",
      maxDeferredToolRecoveries: 3,
      allowEmptyResult: true,
      maxPromptBytes: 65536,
      maxRuntimeMs: prepared.lease.budgets.maxRuntimeSeconds * 1000,
      maxToolCalls: prepared.lease.budgets.maxToolCalls,
      maxResultBytes: boundary.runtime.maxResultBytes,
      maxStderrBytes: 4 * 1024 * 1024,
      terminationTimeoutMs: 10000,
      onTerminate: () => cleanupWorker(boundary.executor, boundary.runtime, claim, "builder-worker"),
    }));
    result = { ...worker.execution, capabilityService: worker.capabilityService };
    failureStage = "proxy-receipt";
    const proxyReceipt = await waitForQuiescentProxyReceipt(prepared, claim, boundary.runtime);
    failureStage = "artifact-retention";
    const exportCommand = buildBuilderExportDockerCommand(prepared, claim, boundary.runtime);
    const exported = await invoke(boundary.executor, exportCommand.command, exportCommand.args, { timeoutMs: 120000, maxBuffer: 65536 });
    const exportReceipt = parseExactJson(exported.stdout.trim(), ["schemaVersion", "operation", "bytes", "sha256", "changes"], "Builder export receipt");
    if (
      exportReceipt.schemaVersion !== 1 || exportReceipt.operation !== "builder-volume-export"
      || !Number.isSafeInteger(exportReceipt.bytes) || exportReceipt.bytes < 2 || exportReceipt.bytes > boundary.runtime.patchArtifactLimitBytes
      || !SHA_RE.test(exportReceipt.sha256 ?? "")
      || !Number.isSafeInteger(exportReceipt.changes) || exportReceipt.changes < 0 || exportReceipt.changes > 100000
    ) fail("Builder export receipt exceeds its boundary");
    failureStage = "candidate-contract";
    const patch = await readBuilderPatch(prepared, claim, boundary.runtime, exportReceipt);
    failureStage = "artifact-retention";
    const resultBytes = Buffer.from(result.text, "utf8");
    const evidence = {
      schemaVersion: 1,
      format: result.capabilityService ? "pixel-builder-evidence-v2" : "pixel-builder-evidence-v1",
      jobId: claim.jobId,
      claimId: claim.claimId,
      planSha256: claim.planSha256,
      workspaceSha256: claim.workspaceSha256,
      patch: { sha256: patch.sha256, bytes: patch.bytes, changes: patch.changes },
      execution: {
        reportBase64: resultBytes.toString("base64"), reportBytes: resultBytes.length,
        reportSha256: createHash("sha256").update(resultBytes).digest("hex"),
        toolCalls: result.toolCalls, observedTools: result.observedTools, protocolVersion: result.protocolVersion,
        durationMilliseconds: result.durationMilliseconds,
        stderrBytes: result.stderrBytes, stderrSha256: result.stderrSha256,
        loopGuard: checkedLoopGuardReceipt(result),
      },
      modelUsage: {
        modelRequests: proxyReceipt.modelRequests, inputTokens: proxyReceipt.inputTokens,
        outputTokens: proxyReceipt.outputTokens, networkBytes: proxyReceipt.networkBytes,
      },
      ...(result.capabilityService ? { capabilityService: result.capabilityService } : {}),
      verification: { required: true, status: "pending" },
      authority: { sourceMutation: false, merge: false, deploy: false, externalEffects: false },
      boundary: "Untrusted local Builder output only. Applying, merging, deploying, or claiming completion requires a separate verifier and operator authority.",
    };
    const evidenceText = `${JSON.stringify(evidence, null, 2)}\n`;
    const evidenceBytes = Buffer.byteLength(evidenceText, "utf8");
    if (patch.bytes + evidenceBytes > prepared.lease.budgets.maxArtifactBytes) fail("Builder patch and test evidence exceed the aggregate artifact budget");
    const evidencePath = posix.join(boundary.runtime.outputDirectory, "builder-evidence.json");
    await writePrivateJson(evidencePath, evidence);
    boundary.retainResult();
    result = { ...result, proxyReceipt, patch, evidence: { path: evidencePath, bytes: evidenceBytes, sha256: createHash("sha256").update(evidenceText, "utf8").digest("hex") } };
  } catch (error) {
    primaryError = error instanceof Error ? error : new DockerSupervisorError("Builder candidate failed closed");
    if (!BUILDER_FAILURE_STAGES.has(primaryError.workFailureStage)) primaryError.workFailureStage = failureStage;
    let proxyReceipt = null;
    try { proxyReceipt = await readProxyReceipt(prepared, claim, boundary.runtime); } catch { /* absent usage is handled conservatively by the controller */ }
    primaryError.workUsage = {
      runtimeSeconds: Math.min(prepared.lease.budgets.maxRuntimeSeconds, Math.ceil(Math.max(0, Date.now() - startedAt) / 1000)),
      modelRequests: proxyReceipt?.modelRequests ?? prepared.lease.budgets.maxModelRequests,
      inputTokens: proxyReceipt?.inputTokens ?? prepared.lease.budgets.maxInputTokens,
      outputTokens: proxyReceipt?.outputTokens ?? prepared.lease.budgets.maxOutputTokens,
      networkBytes: proxyReceipt?.networkBytes ?? prepared.lease.budgets.maxNetworkBytes,
      artifactBytes: 0,
      failures: 1,
    };
  }
  try {
    await boundary.cleanup();
    if (primaryError) primaryError.workCleanupComplete = true;
  } catch (cleanupError) {
    if (!primaryError) primaryError = cleanupError instanceof Error ? cleanupError : new DockerSupervisorError("Builder cleanup failed closed");
    primaryError.workCleanupComplete = false;
    primaryError.workFailureStage = "cleanup";
  }
  if (primaryError) throw primaryError;
  return result;
}

export async function verifyBuilderCandidate(prepared, claim, result, options) {
  if (!result?.patch?.value || !result?.evidence) fail("Builder candidate is incomplete");
  const verification = await runIndependentBuilderVerification(prepared, claim, result.patch.value, result.patch, {
    ...options, executor: options?.executor ?? execDocker,
  });
  if (result.patch.bytes + result.evidence.bytes + verification.artifact.bytes > prepared.lease.budgets.maxArtifactBytes) {
    fail("Builder and independent verification artifacts exceed the aggregate artifact budget");
  }
  return verification;
}

export async function runBuilderDockerLifecycle(prepared, claim, options) {
  const result = await runBuilderCandidateLifecycle(prepared, claim, options);
  const verification = await verifyBuilderCandidate(prepared, claim, result, options);
  return { ...result, verification };
}

async function writeDataLabResult(boundary, result) {
  const { runtime, prepared, claim } = boundary;
  const reportText = `${JSON.stringify(result.report, null, 2)}\n`;
  const verificationText = `${JSON.stringify(result.verification, null, 2)}\n`;
  const evidence = {
    schemaVersion: 1,
    format: result.capabilityService ? "pixel-data-lab-evidence-v2" : "pixel-data-lab-evidence-v1",
    jobId: claim.jobId,
    claimId: claim.claimId,
    planSha256: claim.planSha256,
    workspaceSha256: claim.workspaceSha256,
    manifest: { sha256: result.manifestRecord.sha256, bytes: result.manifestRecord.bytes },
    recipe: { sha256: result.manifest.recipe.sha256, bytes: result.manifest.recipe.bytes, runtimeContract: result.manifest.recipe.runtimeContract },
    report: { sha256: sha(reportText), bytes: Buffer.byteLength(reportText, "utf8") },
    verification: { sha256: sha(verificationText), bytes: Buffer.byteLength(verificationText, "utf8"), status: result.verification.status },
    replayInventory: { sha256: result.inventoryRecord.sha256, bytes: result.inventoryRecord.bytes },
    execution: {
      toolCalls: result.execution.toolCalls,
      observedTools: result.execution.observedTools,
      protocolVersion: result.execution.protocolVersion,
      durationMilliseconds: result.execution.durationMilliseconds,
      stderrBytes: result.execution.stderrBytes,
      stderrSha256: result.execution.stderrSha256,
      loopGuard: checkedLoopGuardReceipt(result.execution),
    },
    modelUsage: {
      modelRequests: result.proxyReceipt.modelRequests,
      inputTokens: result.proxyReceipt.inputTokens,
      outputTokens: result.proxyReceipt.outputTokens,
      networkBytes: result.proxyReceipt.networkBytes,
    },
    ...(result.capabilityService ? { capabilityService: result.capabilityService } : {}),
    rawInputsReadOnly: true,
    replayNetwork: "none",
    freshReplayWorkspace: true,
    semanticAccuracyVerified: false,
    externalEffects: false,
    authority: { rawInputMutation: false, hostAccess: false, network: false, credentials: false, externalEffects: false, publish: false, policyMutation: false, scopeExpansion: false },
    boundary: "Local Data Lab artifacts passed exact networkless replay only. This evidence proves reproducibility and integrity, not semantic truth, publication authority, external-action authority, policy authority, or completion authority.",
  };
  const evidenceText = `${JSON.stringify(evidence, null, 2)}\n`;
  const retainedBytes = result.manifest.totals.bytes + result.manifest.recipe.bytes + result.manifestRecord.bytes + result.inventoryRecord.bytes
    + Buffer.byteLength(reportText, "utf8") + Buffer.byteLength(verificationText, "utf8") + Buffer.byteLength(evidenceText, "utf8");
  if (retainedBytes > prepared.lease.budgets.maxArtifactBytes) fail("Data Lab retained artifacts exceed the aggregate artifact budget");
  await writePrivateJson(posix.join(runtime.outputDirectory, "data-report.json"), result.report);
  await writePrivateJson(posix.join(runtime.outputDirectory, "data-verification.json"), result.verification);
  await writePrivateJson(posix.join(runtime.outputDirectory, "data-evidence.json"), evidence);
  return {
    report: { path: posix.join(runtime.outputDirectory, "data-report.json"), bytes: Buffer.byteLength(reportText, "utf8"), sha256: sha(reportText) },
    verification: { path: posix.join(runtime.outputDirectory, "data-verification.json"), bytes: Buffer.byteLength(verificationText, "utf8"), sha256: sha(verificationText) },
    evidence: { path: posix.join(runtime.outputDirectory, "data-evidence.json"), bytes: Buffer.byteLength(evidenceText, "utf8"), sha256: sha(evidenceText) },
    manifest: { path: posix.join(runtime.outputDirectory, "artifact-manifest.json"), bytes: result.manifestRecord.bytes, sha256: result.manifestRecord.sha256 },
    recipe: { path: posix.join(runtime.outputDirectory, "recipe.py"), bytes: result.manifest.recipe.bytes, sha256: result.manifest.recipe.sha256 },
    derivedDirectory: posix.join(runtime.outputDirectory, "artifacts"),
    totalBytes: retainedBytes,
  };
}

export async function runDataLabDockerLifecycle(prepared, claim, options) {
  const boundary = await setupDataLabDockerBoundary(prepared, claim, options);
  let result;
  let primaryError;
  try {
    const command = buildDataLabDockerCommand(prepared, claim, boundary.runtime);
    const worker = await runRpcWithCapability(boundary, options, "data-lab-worker", () => (options?.rpcRunner ?? runRpcSession)({
      command: command.command,
      args: command.args,
      cwd: "/",
      env: command.env,
      prompt: bindKnowledgeToWorkerPrompt(buildDataLabPrompt(prepared.plan), prepared, options?.knowledge),
      allowedTools: command.allowedTools,
      requiredInitialTool: "read",
      maxDeferredToolRecoveries: 3,
      maxPromptBytes: 65536,
      maxRuntimeMs: prepared.lease.budgets.maxRuntimeSeconds * 1000,
      maxToolCalls: prepared.lease.budgets.maxToolCalls,
      maxResultBytes: boundary.runtime.maxResultBytes,
      maxStderrBytes: 4 * 1024 * 1024,
      terminationTimeoutMs: 10000,
      onTerminate: () => cleanupWorker(boundary.executor, boundary.runtime, claim, "data-lab-worker"),
    }));
    const execution = worker.execution;
    let proposal;
    try { proposal = JSON.parse(execution.text); } catch { fail("Data Lab worker proposal is not JSON"); }
    const proxyReceipt = await waitForQuiescentProxyReceipt(prepared, claim, boundary.runtime);
    const clock = options?.clock ?? (() => new Date());
    const exportNow = clock();
    if (!(exportNow instanceof Date) || !Number.isSafeInteger(exportNow.getTime())) fail("Data Lab controller clock is invalid");
    const exportCommand = buildDataLabExportDockerCommand(prepared, claim, boundary.runtime, {
      now: exportNow, suffix: options?.manifestSuffix ?? randomBytes(6).toString("hex"),
    });
    const exported = await invoke(boundary.executor, exportCommand.command, exportCommand.args, { timeoutMs: 120000, maxBuffer: 65536 });
    const exportReceipt = parseExactJson(exported.stdout.trim(), ["schemaVersion", "operation", "manifestBytes", "manifestSha256", "recipeBytes", "recipeSha256", "artifactFiles", "artifactBytes"], "Data Lab export receipt");
    if (exportReceipt.schemaVersion !== 1 || exportReceipt.operation !== "data-lab-export" || !SHA_RE.test(exportReceipt.manifestSha256 ?? "") || !SHA_RE.test(exportReceipt.recipeSha256 ?? "")
      || !Number.isSafeInteger(exportReceipt.manifestBytes) || exportReceipt.manifestBytes < 2 || exportReceipt.manifestBytes > prepared.lease.budgets.maxArtifactBytes
      || !Number.isSafeInteger(exportReceipt.recipeBytes) || exportReceipt.recipeBytes < 1 || exportReceipt.recipeBytes > prepared.lease.budgets.maxArtifactBytes
      || !Number.isSafeInteger(exportReceipt.artifactFiles) || exportReceipt.artifactFiles < 1 || exportReceipt.artifactFiles > prepared.plan.data.maxArtifactFiles
      || !Number.isSafeInteger(exportReceipt.artifactBytes) || exportReceipt.artifactBytes < 1 || exportReceipt.artifactBytes > prepared.plan.data.maxArtifactBytes) fail("Data Lab export receipt exceeds its boundary");
    const manifestRecord = await readPrivateJson(posix.join(boundary.runtime.outputDirectory, "artifact-manifest.json"), prepared.lease.budgets.maxArtifactBytes, "Data Lab artifact manifest", exportReceipt.manifestSha256);
    const manifest = manifestRecord.value;
    if (manifestRecord.bytes !== exportReceipt.manifestBytes || manifest.recipe?.sha256 !== exportReceipt.recipeSha256 || manifest.recipe?.bytes !== exportReceipt.recipeBytes || manifest.totals?.files !== exportReceipt.artifactFiles || manifest.totals?.bytes !== exportReceipt.artifactBytes) fail("Data Lab manifest differs from its export receipt");

    const replayCommand = buildDataLabReplayDockerCommand(prepared, claim, boundary.runtime);
    await invoke(boundary.executor, replayCommand.command, replayCommand.args, { timeoutMs: prepared.lease.budgets.maxRuntimeSeconds * 1000, maxBuffer: 65536 });
    const inventoryCommand = buildDataLabInventoryDockerCommand(prepared, claim, boundary.runtime);
    const inventoried = await invoke(boundary.executor, inventoryCommand.command, inventoryCommand.args, { timeoutMs: 120000, maxBuffer: 65536 });
    const inventoryReceipt = parseExactJson(inventoried.stdout.trim(), ["schemaVersion", "operation", "inventoryBytes", "inventorySha256", "artifactFiles", "artifactBytes"], "Data Lab replay inventory receipt");
    if (inventoryReceipt.schemaVersion !== 1 || inventoryReceipt.operation !== "data-lab-replay-inventory" || !SHA_RE.test(inventoryReceipt.inventorySha256 ?? "")
      || !Number.isSafeInteger(inventoryReceipt.inventoryBytes) || inventoryReceipt.inventoryBytes < 2 || inventoryReceipt.inventoryBytes > prepared.lease.budgets.maxArtifactBytes
      || !Number.isSafeInteger(inventoryReceipt.artifactFiles) || inventoryReceipt.artifactFiles < 1 || inventoryReceipt.artifactFiles > prepared.plan.data.maxArtifactFiles
      || !Number.isSafeInteger(inventoryReceipt.artifactBytes) || inventoryReceipt.artifactBytes < 1 || inventoryReceipt.artifactBytes > prepared.plan.data.maxArtifactBytes) fail("Data Lab replay inventory receipt exceeds its boundary");
    const inventoryRecord = await readPrivateJson(posix.join(boundary.runtime.outputDirectory, "replay-inventory.json"), prepared.lease.budgets.maxArtifactBytes, "Data Lab replay inventory", inventoryReceipt.inventorySha256);
    if (inventoryRecord.bytes !== inventoryReceipt.inventoryBytes || !Array.isArray(inventoryRecord.value) || inventoryRecord.value.length !== inventoryReceipt.artifactFiles || inventoryRecord.value.reduce((total, artifact) => total + artifact.bytes, 0) !== inventoryReceipt.artifactBytes) fail("Data Lab replay inventory differs from its receipt");

    const verificationNow = clock();
    if (!(verificationNow instanceof Date) || !Number.isSafeInteger(verificationNow.getTime())) fail("Data Lab verifier clock is invalid");
    const verification = verifyDataReplay(manifest, manifestRecord.sha256, inventoryRecord.value, { recipeExitCode: 0, timedOut: false, outputLimitExceeded: false }, {
      jobId: claim.jobId, claimId: claim.claimId, planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256,
      dataClassification: prepared.plan.dataClassification,
      datasets: prepared.datasets.map((dataset) => ({ datasetId: dataset.datasetId, format: dataset.format, contentSha256: dataset.contentSha256, bytes: dataset.bytes })),
    }, { now: verificationNow, suffix: options?.verificationSuffix ?? randomBytes(6).toString("hex") });
    if (verification.status !== "exact-replay-pass") fail("Data Lab exact replay did not pass");
    const reportNow = clock();
    if (!(reportNow instanceof Date) || !Number.isSafeInteger(reportNow.getTime())) fail("Data Lab report clock is invalid");
    const report = finalizeDataReport(proposal, manifest, manifestRecord.sha256, {
      jobId: claim.jobId, claimId: claim.claimId, planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256,
      dataClassification: prepared.plan.dataClassification,
      datasets: prepared.datasets.map((dataset) => ({ datasetId: dataset.datasetId, format: dataset.format, contentSha256: dataset.contentSha256, bytes: dataset.bytes })),
    }, { now: reportNow, suffix: options?.reportSuffix ?? randomBytes(6).toString("hex") });
    result = { execution, capabilityService: worker.capabilityService, proxyReceipt, proposal, manifest, manifestRecord, inventoryRecord, verification, report };
    result.artifacts = await writeDataLabResult(boundary, result);
    boundary.retainResult();
  } catch (error) {
    primaryError = error instanceof Error ? error : new DockerSupervisorError("Data Lab lifecycle failed closed");
  }
  try {
    await retryBoundedCleanup(() => boundary.cleanup());
    if (primaryError) primaryError.workCleanupComplete = true;
    else result.cleanupComplete = true;
  } catch (cleanupError) {
    if (primaryError) {
      primaryError.workCleanupComplete = false;
      primaryError.workCleanupFailure = cleanupError;
    } else primaryError = cleanupError;
  }
  if (primaryError) throw primaryError;
  return result;
}

async function runResearcherWorker(boundary, options = {}, attempt = {}) {
  const { prepared, claim, runtime } = boundary;
  if (!attempt || typeof attempt !== "object" || Array.isArray(attempt)) fail("Researcher worker attempt is invalid");
  const prompt = attempt.prompt ?? bindKnowledgeToWorkerPrompt(buildResearcherPrompt(prepared.plan), prepared, options?.knowledge);
  const maxPromptBytes = attempt.maxPromptBytes ?? 65536;
  const maxToolCalls = attempt.maxToolCalls ?? prepared.lease.budgets.maxToolCalls;
  const maxLeaseRuntimeMs = prepared.lease.budgets.maxRuntimeSeconds * 1000;
  const maxRuntimeMs = attempt.maxRuntimeMs ?? (typeof options.remainingRuntimeMilliseconds === "function" ? options.remainingRuntimeMilliseconds() : maxLeaseRuntimeMs);
  if (typeof prompt !== "string" || Buffer.byteLength(prompt, "utf8") < 1 || !Number.isSafeInteger(maxPromptBytes)
    || maxPromptBytes < 1024 || maxPromptBytes > 4 * 1024 * 1024 || Buffer.byteLength(prompt, "utf8") > maxPromptBytes
    || !Number.isSafeInteger(maxToolCalls) || maxToolCalls < 1 || maxToolCalls > prepared.lease.budgets.maxToolCalls
    || !Number.isSafeInteger(maxRuntimeMs) || maxRuntimeMs < 1 || maxRuntimeMs > maxLeaseRuntimeMs) fail("Researcher worker attempt exceeds its lease-bound RPC envelope");
  const serviceRunner = options.researchService ?? serveResearchToolQueue;
  const rpcRunner = options.rpcRunner ?? runRpcSession;
  const controller = new AbortController();
  const batches = [];
  const servicePromise = Promise.resolve().then(() => serviceRunner({
    requestDirectory: runtime.requestDirectory,
    responseDirectory: runtime.responseDirectory,
    plan: prepared.plan,
    lease: prepared.lease,
    claim,
    stateRoot: runtime.researchStateRoot,
    courierQueueRoot: runtime.researchCourierQueueRoot,
    objectRoot: runtime.researchObjectRoot,
    endpoint: runtime.researchEndpoint,
    signal: controller.signal,
    pipelineOptions: options.researchPipelineOptions ?? {},
    onCompleted: (entry) => { batches.push(entry.batch); },
  }));
  const command = buildResearcherDockerCommand(prepared, claim, runtime);
  const rpcPromise = runRpcWithCapability(boundary, options, "researcher-worker", () => rpcRunner({
    command: command.command,
    args: command.args,
    cwd: "/",
    env: command.env,
    prompt,
    allowedTools: command.allowedTools,
    requiredInitialTool: "pixel_research",
    maxDeferredToolRecoveries: 3,
    maxPromptBytes,
    maxRuntimeMs,
    maxToolCalls,
    maxResultBytes: runtime.maxResultBytes,
    maxStderrBytes: 4 * 1024 * 1024,
    terminationTimeoutMs: 10000,
    onTerminate: () => cleanupWorker(boundary.executor, runtime, claim, "researcher-worker"),
  }));
  const first = await Promise.race([
    rpcPromise.then((value) => ({ kind: "rpc", value }), (error) => ({ kind: "rpc-error", error })),
    servicePromise.then((value) => ({ kind: "service-exit", value }), (error) => ({ kind: "service-error", error })),
  ]);
  if (first.kind === "service-exit" || first.kind === "service-error") {
    controller.abort();
    await cleanupWorker(boundary.executor, runtime, claim, "researcher-worker").catch(() => {});
    await rpcPromise.catch(() => {});
    if (first.kind === "service-error") throw first.error;
    fail("Research Broker service exited before the Researcher worker");
  }
  controller.abort();
  let serviceReceipt;
  try { serviceReceipt = await servicePromise; } catch (error) {
    if (first.kind === "rpc-error") throw first.error;
    throw error;
  }
  if (first.kind === "rpc-error") throw first.error;
  await cleanupWorker(boundary.executor, runtime, claim, "researcher-worker");
  await rm(runtime.cidFile, { force: true });
  const contextIsolationReceiptSha256 = sha({
    schemaVersion: 1, role: "researcher-worker", commandSha256: sha({ command: command.command, args: command.args, env: command.env }),
    promptSha256: sha(prompt), noSession: command.args.includes("--no-session"), noExtensions: command.args.includes("--no-extensions"),
    noSkills: command.args.includes("--no-skills"), noRules: command.args.includes("--no-rules"), requiredInitialTool: "pixel_research",
    executionLoopGuard: checkedLoopGuardReceipt(first.value.execution), completedBatchSha256s: batches.map((batch) => sha(batch)),
    residualContainerRemoved: true, cidFileRemoved: true,
  });
  return { execution: first.value.execution, capabilityService: first.value.capabilityService, serviceReceipt, batches, contextIsolationReceiptSha256 };
}

function parseResearchCriticOutput(text, label) {
  if (typeof text !== "string" || !text || Buffer.byteLength(text, "utf8") > 1024 * 1024 || !Buffer.from(text, "utf8").toString("utf8").endsWith(text)) fail(`${label} output is invalid`);
  let value;
  try { value = JSON.parse(text); } catch { fail(`${label} output is not JSON`); }
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(["disposition", "findings"])) fail(`${label} output shape is invalid`);
  return value;
}

async function runResearcherCriticPass(boundary, request, options = {}) {
  const { prepared, claim, runtime } = boundary;
  const command = buildResearcherCriticDockerCommand(prepared, claim, runtime, request.role);
  const prompt = [
    "Assess every finding in the exact input below. Return only a JSON object with keys findings and disposition.",
    "Each finding must have exactly findingId, semanticSupport, sourcePreference, freshnessLabelAssessment, confidencePermille, uncertaintyAcknowledged, and requiredRevisionBase64.",
    "Use null requiredRevisionBase64 only when no revision is required; otherwise encode a concise UTF-8 revision instruction as canonical base64.",
    "Allowed semanticSupport values: supported, partially-supported, unsupported, unknown.",
    "Allowed sourcePreference values: primary-preferred, primary-unavailable-disclosed, secondary-only-undisclosed, unknown.",
    "Allowed freshnessLabelAssessment values: label-correct, label-missing, label-unsupported, unknown.",
    "Allowed disposition values: revise, no-revision-requested, unable-to-assess.",
    canonical(request.input),
  ].join("\n");
  if (Buffer.byteLength(prompt, "utf8") > 1024 * 1024) fail("Researcher critic prompt exceeds its local inference envelope");
  const rpcRunner = options.criticRpcRunner ?? runRpcSession;
  const maxLeaseRuntimeMs = prepared.lease.budgets.maxRuntimeSeconds * 1000;
  const maxRuntimeMs = typeof options.remainingRuntimeMilliseconds === "function" ? options.remainingRuntimeMilliseconds() : maxLeaseRuntimeMs;
  if (!Number.isSafeInteger(maxRuntimeMs) || maxRuntimeMs < 1 || maxRuntimeMs > maxLeaseRuntimeMs) fail(`Researcher ${request.role} has no remaining lifecycle runtime`);
  const execution = await rpcRunner({
    command: command.command, args: command.args, cwd: "/", env: command.env, prompt,
    allowedTools: command.allowedTools, maxDeferredToolRecoveries: 0,
    maxPromptBytes: 1024 * 1024, maxRuntimeMs,
    maxToolCalls: 1, maxResultBytes: 1024 * 1024, maxStderrBytes: 1024 * 1024,
    terminationTimeoutMs: 10000,
    onTerminate: () => removeExactContainer(boundary.executor, runtime, claim, command.containerName, command.containerRole),
  });
  if (!execution || !Array.isArray(execution.observedTools) || execution.observedTools.length !== 0 || execution.toolCalls !== 0) fail(`Researcher ${request.role} used a tool despite its review-only boundary`);
  const parsed = parseResearchCriticOutput(execution.text, `Researcher ${request.role}`);
  const cleanup = options.criticCleanup ?? (() => removeExactContainer(boundary.executor, runtime, claim, command.containerName, command.containerRole));
  const residualContainerRemoved = await cleanup({ boundary, command, request, execution });
  if (typeof residualContainerRemoved !== "boolean") fail(`Researcher ${request.role} cleanup receipt is invalid`);
  const receiptReader = options.criticProxyReceipt ?? (() => waitForQuiescentProxyReceipt(prepared, claim, runtime));
  const proxyReceipt = await receiptReader({ boundary, command, request, execution });
  if (!proxyReceipt || !Number.isSafeInteger(proxyReceipt.modelRequests) || proxyReceipt.modelRequests < 1) fail(`Researcher ${request.role} inference receipt is absent`);
  return {
    contextIsolationReceiptSha256: sha({
      schemaVersion: 1, role: request.role, containerName: command.containerName, containerRole: command.containerRole,
      commandSha256: sha({ command: command.command, args: command.args, env: command.env }),
      inputSha256: request.inputSha256,
      noSession: command.args.includes("--no-session"), workspaceMounted: command.args.some((value) => value.includes(runtime.volumeName)),
      workerTranscriptIncluded: false, observedTools: execution.observedTools, toolCalls: execution.toolCalls,
      protocolVersion: execution.protocolVersion, frames: execution.frames, residualContainerRemoved,
    }),
    inferenceReceiptSha256: sha(proxyReceipt), findings: parsed.findings, disposition: parsed.disposition,
  };
}

async function finalizeResearcherCandidate(boundary, worker, batches, options = {}, minimumReportTime = -1) {
  const { prepared, claim } = boundary;
  const latestBatchTime = latestResearchBatchTime(batches);
  const proposal = parseResearchReportProposal(worker.execution.text);
  const clock = options?.clock ?? (() => new Date());
  const proposedTime = clock();
  if (!(proposedTime instanceof Date) || !Number.isSafeInteger(proposedTime.getTime())) fail("Researcher controller clock is invalid");
  const reportNow = new Date(Math.max(proposedTime.getTime(), latestBatchTime + 1, minimumReportTime + 1));
  const report = finalizeResearchReport({
    proposal, batches, plan: prepared.plan, lease: prepared.lease, claim, now: reportNow,
    suffix: options?.reportSuffix ?? randomBytes(6).toString("hex"),
  });
  const verificationTime = clock();
  if (!(verificationTime instanceof Date) || !Number.isSafeInteger(verificationTime.getTime())) fail("Researcher verifier clock is invalid");
  const verification = await verifyResearchReport({
    report, batches, plan: prepared.plan, claim,
    stateRoot: boundary.runtime.researchStateRoot, objectRoot: boundary.runtime.researchObjectRoot,
    now: new Date(Math.max(verificationTime.getTime(), Date.parse(report.createdAt) + 1)),
    suffix: options?.verificationSuffix ?? randomBytes(6).toString("hex"),
  });
  return { proposal, report, verification, batches };
}

function aggregateResearcherExecutions(workers, maximumToolCalls) {
  if (!Array.isArray(workers) || workers.length < 1 || workers.length > 5) fail("Researcher execution attempt inventory is invalid");
  const contexts = workers.map((worker) => worker.contextIsolationReceiptSha256);
  if (contexts.some((value) => !SHA_RE.test(value ?? "")) || new Set(contexts).size !== contexts.length) fail("Researcher worker attempts reused or omitted a clean-context receipt");
  const receipts = workers.map((worker) => checkedLoopGuardReceipt(worker.execution));
  const toolCalls = workers.reduce((total, worker) => total + worker.execution.toolCalls, 0);
  if (!Number.isSafeInteger(toolCalls) || toolCalls < 1 || toolCalls > maximumToolCalls) fail("Researcher execution attempts exceed the aggregate tool-call lease");
  const protocolVersions = new Set(workers.map((worker) => worker.execution.protocolVersion));
  if (protocolVersions.size !== 1) fail("Researcher execution attempts changed RPC protocol version");
  const execution = {
    ...workers.at(-1).execution,
    toolCalls,
    observedTools: workers.flatMap((worker) => worker.execution.observedTools),
    frames: workers.reduce((total, worker) => total + (worker.execution.frames ?? 0), 0),
    durationMilliseconds: workers.reduce((total, worker) => total + worker.execution.durationMilliseconds, 0),
    stderrBytes: workers.reduce((total, worker) => total + worker.execution.stderrBytes, 0),
    stderrSha256: sha(receipts.map((receipt, index) => ({ receipt, stderrSha256: workers[index].execution.stderrSha256 }))),
    deferredToolRecoveries: workers.reduce((total, worker) => total + (worker.execution.deferredToolRecoveries ?? 0), 0),
    loopGuard: {
      schemaVersion: 1, policy: "pixel-rpc-observation-loop-guard-v1", status: "within-envelope", reason: null,
      completedToolCalls: toolCalls, eventHeadSha256: sha(receipts),
    },
  };
  checkedLoopGuardReceipt(execution);
  return { execution, receipts };
}

async function writeResearcherResult(boundary, result) {
  const { runtime, prepared, claim } = boundary;
  const reportText = `${JSON.stringify(result.report, null, 2)}\n`;
  const verificationText = `${JSON.stringify(result.verification, null, 2)}\n`;
  const revisionReviewText = result.revisionReview ? `${JSON.stringify(result.revisionReview, null, 2)}\n` : null;
  const revisionHistoryText = result.revisionHistory ? `${JSON.stringify(result.revisionHistory, null, 2)}\n` : null;
  const evidence = {
    schemaVersion: 1,
    format: result.capabilityService ? "pixel-researcher-evidence-v2" : "pixel-researcher-evidence-v1",
    jobId: claim.jobId,
    claimId: claim.claimId,
    planSha256: claim.planSha256,
    workspaceSha256: claim.workspaceSha256,
    report: { sha256: sha(reportText), bytes: Buffer.byteLength(reportText, "utf8") },
    verification: { sha256: sha(verificationText), bytes: Buffer.byteLength(verificationText, "utf8"), status: result.verification.status },
    ...(revisionReviewText ? { revisionReview: {
      sha256: sha(revisionReviewText), bytes: Buffer.byteLength(revisionReviewText, "utf8"),
      outcome: result.revisionReview.outcome, independentlyScored: false, backendOutputUsedAsScore: false,
    } } : {}),
    ...(revisionHistoryText ? { revisionHistory: {
      sha256: sha(revisionHistoryText), bytes: Buffer.byteLength(revisionHistoryText, "utf8"),
      status: result.revisionHistory.status, revisionRounds: result.revisionHistory.revisionRounds,
      attempts: result.revisionHistory.attempts.length, independentlyScored: false, backendOutputUsedAsScore: false,
    } } : {}),
    execution: {
      toolCalls: result.execution.toolCalls,
      observedTools: result.execution.observedTools,
      protocolVersion: result.execution.protocolVersion,
      durationMilliseconds: result.execution.durationMilliseconds,
      stderrBytes: result.execution.stderrBytes,
      stderrSha256: result.execution.stderrSha256,
      loopGuard: checkedLoopGuardReceipt(result.execution),
      attempts: structuredClone(result.executionReceipts ?? [checkedLoopGuardReceipt(result.execution)]),
      workerContextIsolationReceipts: structuredClone(result.workerContextIsolationReceipts ?? []),
    },
    modelUsage: {
      modelRequests: result.proxyReceipt.modelRequests,
      inputTokens: result.proxyReceipt.inputTokens,
      outputTokens: result.proxyReceipt.outputTokens,
      networkBytes: result.proxyReceipt.networkBytes,
    },
    ...(result.capabilityService ? { capabilityService: result.capabilityService } : {}),
    ...(result.capabilityServices ? { capabilityServices: result.capabilityServices } : {}),
    researchService: result.serviceReceipt,
    ...(result.serviceReceipts ? { researchServices: result.serviceReceipts } : {}),
    sourceObjectRetention: "job-only-until-successful-cleanup",
    citedEvidenceRetention: "retained-in-public-report",
    externalEffects: false,
    authority: { directNetwork: false, credentials: false, externalWrites: false, publish: false, purchase: false, merge: false, deploy: false },
    boundary: "Public Researcher artifacts and content-free execution evidence only. Source objects were job-scoped; publication, action, and completion still require separate authority.",
  };
  const evidenceText = `${JSON.stringify(evidence, null, 2)}\n`;
  const totalBytes = Buffer.byteLength(reportText, "utf8") + Buffer.byteLength(verificationText, "utf8")
    + (revisionReviewText ? Buffer.byteLength(revisionReviewText, "utf8") : 0)
    + (revisionHistoryText ? Buffer.byteLength(revisionHistoryText, "utf8") : 0) + Buffer.byteLength(evidenceText, "utf8");
  if (totalBytes > prepared.lease.budgets.maxArtifactBytes) fail("Researcher artifacts exceed the aggregate artifact budget");
  await writePrivateJson(posix.join(runtime.outputDirectory, "research-report.json"), result.report);
  await writePrivateJson(posix.join(runtime.outputDirectory, "research-verification.json"), result.verification);
  if (result.revisionReview) await writePrivateJson(posix.join(runtime.outputDirectory, "research-revision-review.json"), result.revisionReview);
  if (result.revisionHistory) await writePrivateJson(posix.join(runtime.outputDirectory, "research-revision-history.json"), result.revisionHistory);
  await writePrivateJson(posix.join(runtime.outputDirectory, "research-evidence.json"), evidence);
  return {
    report: { path: posix.join(runtime.outputDirectory, "research-report.json"), bytes: Buffer.byteLength(reportText, "utf8"), sha256: sha(reportText) },
    verification: { path: posix.join(runtime.outputDirectory, "research-verification.json"), bytes: Buffer.byteLength(verificationText, "utf8"), sha256: sha(verificationText) },
    ...(revisionReviewText ? { revisionReview: { path: posix.join(runtime.outputDirectory, "research-revision-review.json"), bytes: Buffer.byteLength(revisionReviewText, "utf8"), sha256: sha(revisionReviewText) } } : {}),
    ...(revisionHistoryText ? { revisionHistory: { path: posix.join(runtime.outputDirectory, "research-revision-history.json"), bytes: Buffer.byteLength(revisionHistoryText, "utf8"), sha256: sha(revisionHistoryText) } } : {}),
    evidence: { path: posix.join(runtime.outputDirectory, "research-evidence.json"), bytes: Buffer.byteLength(evidenceText, "utf8"), sha256: sha(evidenceText) },
    totalBytes,
  };
}

function latestResearchBatchTime(batches) {
  if (!Array.isArray(batches) || batches.length < 1) fail("Researcher completed without any broker-verified source batch");
  const timestamps = batches.map((batch) => Date.parse(batch?.createdAt));
  if (timestamps.some((value) => !Number.isSafeInteger(value))) fail("Researcher source batch time is invalid");
  return Math.max(...timestamps);
}

export async function runResearcherDockerLifecycle(prepared, claim, options) {
  const monotonic = options?.monotonic ?? (() => performance.now());
  if (typeof monotonic !== "function") fail("Researcher lifecycle monotonic clock is unavailable");
  const lifecycleStarted = monotonic();
  if (!Number.isFinite(lifecycleStarted) || lifecycleStarted < 0) fail("Researcher lifecycle monotonic clock is invalid");
  const boundary = await setupResearcherDockerBoundary(prepared, claim, options);
  const lifecycleMaximumMs = prepared.lease.budgets.maxRuntimeSeconds * 1000;
  let lastMonotonic = lifecycleStarted;
  const remainingRuntimeMilliseconds = () => {
    const current = monotonic();
    if (!Number.isFinite(current) || current < lastMonotonic) fail("Researcher lifecycle monotonic clock moved backwards");
    lastMonotonic = current;
    return Math.floor(lifecycleMaximumMs - (current - lifecycleStarted));
  };
  const boundedOptions = { ...options, remainingRuntimeMilliseconds };
  let result;
  let primaryError;
  try {
    const clock = options?.clock ?? (() => new Date());
    const workers = [await runResearcherWorker(boundary, boundedOptions)];
    const initial = await finalizeResearcherCandidate(boundary, workers[0], workers[0].batches, boundedOptions);
    let proposal = initial.proposal, report = initial.report, verification = initial.verification;
    let batches = initial.batches, revisionReview = null, revisionHistory = null;
    if (options?.researchRevisionReview === true) {
      const configuredRounds = options?.researchMaxRevisionRounds ?? Math.min(2, Math.max(0, prepared.lease.budgets.maxIterations - 1));
      if (!Number.isInteger(configuredRounds) || configuredRounds < 0 || configuredRounds > 4 || configuredRounds >= prepared.lease.budgets.maxIterations) fail("Researcher revision round budget is invalid");
      const proposals = new Map([[sha(initial.report), initial.proposal]]), attempts = [];
      const loop = await runResearchRevisionLoop({
        initialCandidate: { report: initial.report, verification: initial.verification, batches: initial.batches },
        maxRevisionRounds: configuredRounds,
        async reviewCandidate({ candidate, round }) {
          const reviewTime = clock();
          if (!(reviewTime instanceof Date) || !Number.isSafeInteger(reviewTime.getTime())) fail("Researcher revision-review clock is invalid");
          const review = await executeResearchRevisionReview({
            plan: prepared.plan, report: candidate.report, verification: candidate.verification, batches: candidate.batches,
            modelContractSha256: options.modelContractSha256, inferenceContractSha256: options.inferenceContractSha256,
            now: new Date(Math.max(reviewTime.getTime(), Date.parse(candidate.verification.createdAt) + 1)),
            suffix: options.reviewSuffix ?? randomBytes(6).toString("hex"),
            criticRunner: (request) => runResearcherCriticPass(boundary, request, boundedOptions),
          });
          const exactProposal = proposals.get(sha(candidate.report));
          if (!exactProposal) fail("Researcher revision history lost its exact proposal");
          const candidateWorker = workers[round];
          if (!candidateWorker || !SHA_RE.test(candidateWorker.contextIsolationReceiptSha256 ?? "")) fail("Researcher revision history lost its clean worker context receipt");
          attempts.push(Object.freeze({
            round, workerContextIsolationReceiptSha256: candidateWorker.contextIsolationReceiptSha256,
            proposal: structuredClone(exactProposal), report: structuredClone(candidate.report),
            verification: structuredClone(candidate.verification), review: structuredClone(review),
          }));
          return review;
        },
        async reviseCandidate({ candidate, review, round }) {
          const usedToolCalls = workers.reduce((total, worker) => total + worker.execution.toolCalls, 0);
          const remainingToolCalls = prepared.lease.budgets.maxToolCalls - usedToolCalls;
          if (remainingToolCalls < 1) fail("Researcher revision has no remaining tool-call budget");
          const priorProposal = proposals.get(sha(candidate.report));
          if (!priorProposal) fail("Researcher revision lost its exact prior proposal");
          const prompt = buildResearcherRevisionPrompt(prepared.plan, {
            round, priorProposal, priorReport: candidate.report, revisionReview: review,
          });
          const worker = await runResearcherWorker(boundary, boundedOptions, {
            prompt, maxPromptBytes: 4 * 1024 * 1024, maxToolCalls: remainingToolCalls,
          });
          latestResearchBatchTime(worker.batches);
          workers.push(worker);
          const next = await finalizeResearcherCandidate(
            boundary, worker, [...candidate.batches, ...worker.batches], boundedOptions, Date.parse(review.createdAt),
          );
          proposals.set(sha(next.report), next.proposal);
          return { report: next.report, verification: next.verification, batches: next.batches };
        },
      });
      proposal = proposals.get(sha(loop.candidate.report));
      if (!proposal) fail("Researcher revision loop lost its final proposal");
      ({ report, verification, batches } = loop.candidate);
      revisionReview = loop.review;
      revisionHistory = buildResearchRevisionHistory({
        status: loop.status, maxRevisionRounds: configuredRounds, revisionRounds: loop.revisionRounds, attempts,
        suffix: options.historySuffix ?? randomBytes(6).toString("hex"),
      });
    }
    const aggregate = aggregateResearcherExecutions(workers, prepared.lease.budgets.maxToolCalls);
    const proxyReceipt = await waitForQuiescentProxyReceipt(prepared, claim, boundary.runtime);
    result = {
      ...workers.at(-1), execution: aggregate.execution, executionReceipts: aggregate.receipts,
      serviceReceipts: workers.map((worker) => structuredClone(worker.serviceReceipt)),
      workerContextIsolationReceipts: workers.map((worker) => worker.contextIsolationReceiptSha256),
      capabilityServices: workers.filter((worker) => worker.capabilityService).map((worker) => structuredClone(worker.capabilityService)),
      proposal, report, verification, batches, proxyReceipt,
      ...(revisionReview ? { revisionReview, revisionHistory } : {}),
    };
    result.artifacts = await writeResearcherResult(boundary, result);
    boundary.retainResult();
  } catch (error) {
    primaryError = error instanceof Error ? error : new DockerSupervisorError("Researcher lifecycle failed closed");
  }
  try {
    await retryBoundedCleanup(() => boundary.cleanup());
    if (primaryError) primaryError.workCleanupComplete = true;
    else result.cleanupComplete = true;
  } catch (cleanupError) {
    if (primaryError) {
      primaryError.workCleanupComplete = false;
      primaryError.workCleanupFailure = cleanupError;
    } else primaryError = cleanupError;
  }
  if (primaryError) throw primaryError;
  return result;
}

async function writeScoutResult(boundary, result) {
  const { runtime, prepared, claim } = boundary;
  const reportText = `${JSON.stringify(result.report, null, 2)}\n`;
  const verificationText = `${JSON.stringify(result.verification, null, 2)}\n`;
  const evidence = {
    schemaVersion: 1, format: result.capabilityService ? "pixel-scout-evidence-v2" : "pixel-scout-evidence-v1", jobId: claim.jobId, claimId: claim.claimId,
    planSha256: claim.planSha256, workspaceSha256: claim.workspaceSha256,
    report: { sha256: sha(reportText), bytes: Buffer.byteLength(reportText, "utf8") },
    verification: { sha256: sha(verificationText), bytes: Buffer.byteLength(verificationText, "utf8"), status: result.verification.status },
    execution: {
      toolCalls: result.execution.toolCalls, observedTools: result.execution.observedTools,
      protocolVersion: result.execution.protocolVersion, durationMilliseconds: result.execution.durationMilliseconds,
      stderrBytes: result.execution.stderrBytes, stderrSha256: result.execution.stderrSha256,
      loopGuard: checkedLoopGuardReceipt(result.execution),
    },
    modelUsage: {
      modelRequests: result.proxyReceipt.modelRequests, inputTokens: result.proxyReceipt.inputTokens,
      outputTokens: result.proxyReceipt.outputTokens, networkBytes: result.proxyReceipt.networkBytes,
    },
    ...(result.capabilityService ? { capabilityService: result.capabilityService } : {}),
    semanticEntailmentVerified: false, externalEffects: false,
    authority: { hostAccess: false, credentials: false, network: false, externalEffects: false, publish: false, merge: false, deploy: false, policyMutation: false, scopeExpansion: false },
    boundary: "Local Scout artifacts with deterministic exact-file evidence only. They grant no truth, completion, execution, external effect, publication, merge, deployment, policy, or scope authority.",
  };
  const evidenceText = `${JSON.stringify(evidence, null, 2)}\n`;
  const totalBytes = Buffer.byteLength(reportText, "utf8") + Buffer.byteLength(verificationText, "utf8") + Buffer.byteLength(evidenceText, "utf8");
  if (totalBytes > prepared.lease.budgets.maxArtifactBytes) fail("Scout retained artifacts exceed the aggregate artifact budget");
  await writePrivateJson(posix.join(runtime.outputDirectory, "scout-report.json"), result.report);
  await writePrivateJson(posix.join(runtime.outputDirectory, "scout-verification.json"), result.verification);
  await writePrivateJson(posix.join(runtime.outputDirectory, "scout-evidence.json"), evidence);
  return {
    report: { path: posix.join(runtime.outputDirectory, "scout-report.json"), bytes: Buffer.byteLength(reportText, "utf8"), sha256: sha(reportText) },
    verification: { path: posix.join(runtime.outputDirectory, "scout-verification.json"), bytes: Buffer.byteLength(verificationText, "utf8"), sha256: sha(verificationText) },
    evidence: { path: posix.join(runtime.outputDirectory, "scout-evidence.json"), bytes: Buffer.byteLength(evidenceText, "utf8"), sha256: sha(evidenceText) },
    totalBytes,
  };
}

export async function runScoutDockerLifecycle(prepared, claim, options) {
  const boundary = await setupScoutDockerBoundary(prepared, claim, options);
  let result;
  let primaryError;
  let failureStage = "rpc-execution";
  const startedAt = Date.now();
  try {
    const command = buildScoutDockerCommand(prepared, claim, boundary.runtime);
    const worker = await runRpcWithCapability(boundary, options, "scout-worker", () => (options?.rpcRunner ?? runRpcSession)({
      command: command.command,
      args: command.args,
      cwd: "/",
      env: command.env,
      prompt: bindKnowledgeToWorkerPrompt(buildScoutPrompt(prepared.plan), prepared, options?.knowledge),
      allowedTools: command.allowedTools,
      requiredInitialTool: "read",
      maxDeferredToolRecoveries: 3,
      maxPromptBytes: 65536,
      maxRuntimeMs: prepared.lease.budgets.maxRuntimeSeconds * 1000,
      maxToolCalls: prepared.lease.budgets.maxToolCalls,
      maxResultBytes: Math.min(prepared.lease.budgets.maxArtifactBytes, 16 * 1024 * 1024),
      maxStderrBytes: 1024 * 1024,
      terminationTimeoutMs: 10000,
      onTerminate: () => cleanupWorker(boundary.executor, boundary.runtime, claim),
    }));
    const execution = worker.execution;
    failureStage = "proxy-receipt";
    const proxyReceipt = await waitForQuiescentProxyReceipt(prepared, claim, boundary.runtime);
    failureStage = "proposal-parse";
    const proposal = parseScoutReportProposal(execution.text);
    const clock = options?.clock ?? (() => new Date());
    const now = clock();
    failureStage = "evidence-finalization";
    const finalized = await finalizeScoutReport({
      proposal, plan: prepared.plan, claim, workspacePath: prepared.workspace.path, now,
      reportSuffix: options?.reportSuffix, verificationSuffix: options?.verificationSuffix,
    });
    result = { ...execution, execution, capabilityService: worker.capabilityService, proxyReceipt, proposal, ...finalized };
    failureStage = "artifact-retention";
    result.artifacts = await writeScoutResult(boundary, result);
    boundary.retainResult();
  } catch (error) {
    primaryError = error instanceof Error ? error : new DockerSupervisorError("Scout lifecycle failed closed");
    if (!SCOUT_FAILURE_STAGES.has(primaryError.workFailureStage)) primaryError.workFailureStage = failureStage;
    let proxyReceipt = null;
    try { proxyReceipt = await readProxyReceipt(prepared, claim, boundary.runtime); } catch { /* absent usage is charged conservatively by the controller */ }
    primaryError.workUsage = {
      runtimeSeconds: Math.min(prepared.lease.budgets.maxRuntimeSeconds, Math.ceil(Math.max(0, Date.now() - startedAt) / 1000)),
      modelRequests: proxyReceipt?.modelRequests ?? prepared.lease.budgets.maxModelRequests,
      inputTokens: proxyReceipt?.inputTokens ?? prepared.lease.budgets.maxInputTokens,
      outputTokens: proxyReceipt?.outputTokens ?? prepared.lease.budgets.maxOutputTokens,
      networkBytes: proxyReceipt?.networkBytes ?? prepared.lease.budgets.maxNetworkBytes,
      artifactBytes: 0, failures: 1,
    };
  }
  try {
    await retryBoundedCleanup(() => boundary.cleanup());
    if (primaryError) primaryError.workCleanupComplete = true;
    else result.cleanupComplete = true;
  } catch (cleanupError) {
    if (primaryError) {
      primaryError.workCleanupComplete = false;
      primaryError.workCleanupFailure = cleanupError;
      primaryError.workFailureStage = "cleanup";
    } else {
      primaryError = cleanupError;
      if (primaryError instanceof Error) primaryError.workFailureStage = "cleanup";
    }
  }
  if (primaryError) throw primaryError;
  return result;
}

export const dockerSupervisorInternals = Object.freeze({ readContainerId, readProxyReceipt, waitForQuiescentProxyReceipt, readBuilderPatch, cleanupBoundary, retryBoundedCleanup, latestResearchBatchTime, checkedCapabilityServiceReceipt, runRpcWithCapability, runResearcherCriticPass, aggregateResearcherExecutions, selectModelBackendPrepared });
