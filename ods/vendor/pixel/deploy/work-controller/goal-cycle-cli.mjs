import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { lstat, readdir } from "node:fs/promises";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { validateJsonSchema } from "../../scripts/lib/json-schema.mjs";
import { validateWorkGoalController } from "../../scripts/lib/work-contract.mjs";
import { runGoalCycle } from "./goal-runtime.mjs";
import { createGoalProfileRouter } from "./goal-profile-router.mjs";
import { publishGoalOperatorStatus } from "./goal-operator-status.mjs";
import { validateLocalResearchEndpoint } from "../work-research-broker/search.mjs";
import { validateGoalCapabilityBindings } from "./goal-capability-runtime.mjs";
import { validateGoalKnowledgeBindings } from "./goal-knowledge-runtime.mjs";
import { auditKnowledgeVault, knowledgeVaultHead, loadKnowledgeVaultKeyCredential } from "./knowledge-vault.mjs";

const MAX_CONFIG_BYTES = 256 * 1024;
const MAX_GOAL_BYTES = 512 * 1024;
const MAX_JOBS_BYTES = 8 * 1024 * 1024;
const MAX_POLICY_BYTES = 2 * 1024 * 1024;
const configSchemaUrl = new URL("../../schemas/work-goal-controller-v1.schema.json", import.meta.url);
const authority = Object.freeze({
  grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false,
  grantsExternalEffects: false, grantsCompletion: false,
});
const boundary = "Content-free result of one supervised local reconciliation cycle. Scheduling another cycle grants no execution, lease, replay, scope expansion, external effect, or completion authority.";

export class GoalCycleCliError extends Error {}

function fail(message) { throw new GoalCycleCliError(message); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 2 || argv[0] !== "--config" || typeof argv[1] !== "string" || !argv[1]) fail("Usage: goal-cycle-cli.mjs --config FILE");
  return resolve(argv[1]);
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid = process.geteuid?.() ?? 0) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail(`${label} is not strict UTF-8`);
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

async function readInstalledJson(path, maximum, label) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1) fail(`${label} is not single-link`);
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail(`${label} is not strict UTF-8`);
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

function exactIpv4(value, label) {
  const parts = typeof value === "string" ? value.split(".") : [];
  if (parts.length !== 4 || parts.some((part) => !/^(?:0|[1-9][0-9]{0,2})$/u.test(part) || Number(part) > 255)) fail(`${label} is invalid`);
  return parts.map(Number);
}

export function validateGoalCycleControllerConfiguration(config, schema = null) {
  const errors = schema === null ? validateWorkGoalController(config) : validateJsonSchema(config, schema);
  if (errors.length) fail(`private goal controller configuration is invalid: ${errors[0]}`);
  for (const [field, value] of Object.entries({
    stateRoot: config.stateRoot, goalPath: config.goalPath, jobsPath: config.jobsPath, policyPath: config.policyPath,
    objectStore: config.objectStore, workspaceRoot: config.workspaceRoot, executorPath: config.executorPath,
    dockerPath: config.runtime.dockerPath,
    ...(config.researchRuntime ? { researchCourierQueueRoot: config.researchRuntime.researchCourierQueueRoot } : {}),
    ...(config.capabilityRuntime ? {
      capabilityControllerPolicyPath: config.capabilityRuntime.controllerPolicyPath,
      capabilityAllowedSignersPath: config.capabilityRuntime.allowedSignersPath,
      capabilitySshKeygenPath: config.capabilityRuntime.sshKeygenPath,
      capabilityDockerConfigPath: config.capabilityRuntime.dockerConfigPath,
    } : {}),
    ...(config.knowledgeRuntime ? { knowledgeVaultRoot: config.knowledgeRuntime.vaultRoot, knowledgeCredentialSourcePath: config.knowledgeRuntime.credentialSourcePath } : {}),
  })) if (resolve(value) !== value) fail(`private goal controller ${field} is not an absolute normalized path`);
  if (config.researchRuntime) {
    try { validateLocalResearchEndpoint(config.researchRuntime.researchEndpoint); } catch { fail("private goal controller research endpoint is invalid"); }
  }
  const [address, prefixText] = config.runtime.networkSubnet.split("/");
  const subnet = exactIpv4(address, "private goal controller network subnet");
  const worker = exactIpv4(config.runtime.workerIp, "private goal controller worker IP");
  const proxy = exactIpv4(config.runtime.proxyIp, "private goal controller proxy IP");
  const prefix = Number(prefixText);
  if (prefix < 24 || prefix > 30) fail("private goal controller network prefix is outside the isolated boundary");
  const ipNumber = (parts) => parts.reduce((total, part) => total * 256 + part, 0) >>> 0;
  const mask = (0xffffffff << (32 - prefix)) >>> 0;
  const subnetNumber = ipNumber(subnet);
  const network = (subnetNumber & mask) >>> 0;
  const broadcast = (network | (~mask >>> 0)) >>> 0;
  const workerNumber = ipNumber(worker);
  const proxyNumber = ipNumber(proxy);
  if (
    subnetNumber !== network || workerNumber === proxyNumber
    || workerNumber <= network || workerNumber >= broadcast || proxyNumber <= network || proxyNumber >= broadcast
    || ((workerNumber & mask) >>> 0) !== network || ((proxyNumber & mask) >>> 0) !== network
  ) fail("private goal controller runtime addresses are not distinct hosts in the declared subnet");
  return config;
}

function receipt(result, goalId) {
  const quiescent = ["terminal", "paused", "wait-for-child-authority"].includes(result.action);
  return {
    schemaVersion: 1,
    operation: "pixel-work-goal-cycle",
    status: result.goalState,
    goalId,
    checkpointSha256: result.goalCheckpointSha256,
    sequence: result.sequence,
    progress: { milestonesCompleted: result.milestonesCompleted, milestonesTotal: result.milestonesTotal },
    action: result.action,
    childState: result.childState ?? null,
    schedulingEffect: quiescent ? "event-noop" : "event-or-watchdog-recovery",
    authority: { ...authority },
    boundary,
  };
}

export async function runGoalCycleCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("goal cycle dependencies are invalid");
  if (dependencies.operatorNow !== undefined && !(dependencies.operatorNow instanceof Date) && !Number.isFinite(Number(dependencies.operatorNow))) fail("goal cycle operator projection time is invalid");
  const configPath = parseArguments(argv);
  const { config, goal, jobs, policy, capabilityPolicy, knowledgeMasterKey } = await loadGoalCycleConfiguration(configPath);
  const router = createGoalProfileRouter({
    config, goal, jobs, policy, capabilityPolicy, knowledgeMasterKey,
    ...(dependencies.builderOverrides ? { builderOverrides: dependencies.builderOverrides } : {}),
    ...(dependencies.candidateOverrides ? { candidateOverrides: dependencies.candidateOverrides } : {}),
  });
  const result = await runGoalCycle({
    stateRoot: config.stateRoot, goal, jobs, resolveChildRun: router.resolveChildRun, driveChild: router.driveChild,
    maxControllerTransitions: config.controller.maxTransitions,
    ...(dependencies.clock ? { clock: dependencies.clock } : {}),
    ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}),
  });
  await publishGoalOperatorStatus({
    stateRoot: config.stateRoot, goal, jobs, capabilityRuntime: config.capabilityRuntime ?? null, capabilityPolicy,
    knowledgeRuntime: config.knowledgeRuntime ?? null,
    ...(dependencies.operatorNow !== undefined ? { now: dependencies.operatorNow } : {}),
  });
  return receipt(result, goal.goalId);
}

export async function loadGoalCycleConfiguration(configPath, options = {}) {
  const expectedOwnerUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("private goal controller owner is invalid");
  const requireResearchRuntimeDirectory = options.requireResearchRuntimeDirectory ?? true;
  if (typeof requireResearchRuntimeDirectory !== "boolean") fail("private goal controller research runtime validation mode is invalid");
  const requireCapabilityRuntime = options.requireCapabilityRuntime ?? true;
  if (typeof requireCapabilityRuntime !== "boolean") fail("private goal controller capability runtime validation mode is invalid");
  const useKnowledgeCredentialSource = options.useKnowledgeCredentialSource ?? false;
  if (typeof useKnowledgeCredentialSource !== "boolean") fail("private goal controller knowledge credential mode is invalid");
  if (options.knowledgeCredentialPath !== undefined && (typeof options.knowledgeCredentialPath !== "string" || resolve(options.knowledgeCredentialPath) !== options.knowledgeCredentialPath)) fail("private goal controller knowledge credential override is invalid");
  const [config, schema] = await Promise.all([
    readPrivateJson(configPath, MAX_CONFIG_BYTES, "private goal controller configuration", expectedOwnerUid),
    readInstalledJson(configSchemaUrl, MAX_CONFIG_BYTES, "installed goal controller schema"),
  ]);
  validateGoalCycleControllerConfiguration(config, schema);
  const [goal, jobs, policy, capabilityPolicy] = await Promise.all([
    readPrivateJson(config.goalPath, MAX_GOAL_BYTES, "private work goal", expectedOwnerUid),
    readPrivateJson(config.jobsPath, MAX_JOBS_BYTES, "private goal child jobs", expectedOwnerUid),
    readPrivateJson(config.policyPath, MAX_POLICY_BYTES, "private work policy", expectedOwnerUid),
    config.capabilityRuntime ? readPrivateJson(config.capabilityRuntime.controllerPolicyPath, MAX_POLICY_BYTES, "private capability controller policy", expectedOwnerUid) : null,
  ]);
  if (!Array.isArray(jobs) || jobs.some((job) => !["scout", "builder", "researcher", "data-lab"].includes(job?.profile))) fail("the supervised goal cycle contains an unsupported child profile");
  const requiresResearch = jobs.some((job) => job.profile === "researcher");
  if (requiresResearch !== Boolean(config.researchRuntime)) fail("private goal controller research runtime presence differs from its exact jobs");
  if (requiresResearch && requireResearchRuntimeDirectory) {
    const info = await lstat(config.researchRuntime.researchCourierQueueRoot).catch(() => null);
    if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail("private goal controller research courier queue is not owner-private");
  }
  if (config.capabilityRuntime) {
    validateGoalCapabilityBindings({ capabilityRuntime: config.capabilityRuntime, jobs, policy: capabilityPolicy });
    if (requireCapabilityRuntime) {
      const [signers, sshKeygen, dockerConfig] = await Promise.all([
        lstat(config.capabilityRuntime.allowedSignersPath).catch(() => null),
        lstat(config.capabilityRuntime.sshKeygenPath).catch(() => null),
        lstat(config.capabilityRuntime.dockerConfigPath).catch(() => null),
      ]);
      if (!signers?.isFile() || signers.isSymbolicLink() || signers.nlink !== 1 || signers.size < 1 || signers.size > 1048576 || process.platform !== "win32" && (signers.uid !== expectedOwnerUid || (signers.mode & 0o077) !== 0)) fail("private capability allowed-signers file is unavailable or unsafe");
      if (!sshKeygen?.isFile() || sshKeygen.isSymbolicLink() || sshKeygen.nlink !== 1 || process.platform !== "win32" && ((sshKeygen.mode & 0o111) === 0 || (sshKeygen.mode & 0o022) !== 0)) fail("private capability signature verifier is unavailable or unsafe");
      if (!dockerConfig?.isDirectory() || dockerConfig.isSymbolicLink() || process.platform !== "win32" && (dockerConfig.uid !== expectedOwnerUid || (dockerConfig.mode & 0o077) !== 0) || (await readdir(config.capabilityRuntime.dockerConfigPath)).length !== 0) fail("private capability Docker configuration must be an empty owner-only directory");
    }
  }
  validateGoalKnowledgeBindings({ config, jobs });
  let knowledgeMasterKey = null;
  if (config.knowledgeRuntime) {
    const credentialPath = options.knowledgeCredentialPath ?? (useKnowledgeCredentialSource ? config.knowledgeRuntime.credentialSourcePath : (() => {
      const directory = process.env.CREDENTIALS_DIRECTORY;
      if (typeof directory !== "string" || resolve(directory) !== directory) fail("private goal controller systemd credential directory is unavailable");
      return join(directory, config.knowledgeRuntime.credentialName);
    })());
    knowledgeMasterKey = await loadKnowledgeVaultKeyCredential({ credentialPath, expectedName: config.knowledgeRuntime.credentialName });
    await auditKnowledgeVault({ root: config.knowledgeRuntime.vaultRoot, vaultId: config.knowledgeRuntime.vaultId, masterKey: knowledgeMasterKey, deep: true });
    await knowledgeVaultHead({ root: config.knowledgeRuntime.vaultRoot, vaultId: config.knowledgeRuntime.vaultId, masterKey: knowledgeMasterKey, ownerId: config.knowledgeRuntime.ownerId, clientId: config.knowledgeRuntime.clientId });
  }
  return Object.freeze({ config, goal, jobs, policy, capabilityPolicy, knowledgeMasterKey });
}

export async function main(argv = process.argv.slice(2)) {
  const value = await runGoalCycleCommand(argv);
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-cycle: ${error instanceof GoalCycleCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
