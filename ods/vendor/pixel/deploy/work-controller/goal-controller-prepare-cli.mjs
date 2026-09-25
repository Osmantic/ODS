import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, rename, rm } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

import {
  canonical, validateWorkGoal, validateWorkGoalBundleManifest, validateWorkGoalControllerBundleManifest,
  validateWorkGoalControllerEnvironment, validateWorkJob, validateWorkModelBackendLaunch, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { validateWorkJobAgainstPolicy } from "../work-broker/broker.mjs";
import { modelBackendIdentityLabels, modelBackendNetworkIdentityLabels } from "../work-runner/docker-boundary.mjs";
import { validateGoalCycleControllerConfiguration } from "./goal-cycle-cli.mjs";
import { validateGoalCapabilityBindings } from "./goal-capability-runtime.mjs";
import { validateGoalKnowledgeBindings } from "./goal-knowledge-runtime.mjs";
import { auditKnowledgeVault, knowledgeVaultHead, loadKnowledgeVaultKeyCredential } from "./knowledge-vault.mjs";
import { modelBackendServerArgumentsFromLaunch } from "./model-backend-launch.mjs";

const MAX_JSON_BYTES = 8 * 1024 * 1024;
const MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const EXPECTED_SOURCE_FILES = Object.freeze(["goal-bundle.json", "goal.json", "jobs.json"]);
const authority = Object.freeze({ grantsExecution: false, grantsLease: false, grantsScheduling: false, grantsServiceActivation: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Private atomic controller bundle only. Preparation proves exact goal, policy, executor, storage, and runtime wiring but creates no ledger or lease, starts no worker or service, and grants no execution, scheduling, activation, scope expansion, external effect, or completion authority.";

export class GoalControllerPrepareCliError extends Error {}

function fail(message) { throw new GoalControllerPrepareCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 6) fail("Usage: goal-controller-prepare-cli.mjs --goal-bundle DIR --environment FILE --output NEW_PRIVATE_DIR");
  const allowed = new Set(["--goal-bundle", "--environment", "--output"]), values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("goal controller preparation arguments are invalid, unknown, or duplicated");
    values[key] = resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`goal controller preparation is missing ${key}`);
  return { sourcePath: values["--goal-bundle"], environmentPath: values["--environment"], outputPath: values["--output"] };
}

async function privateDirectory(path, label, expectedOwnerUid) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return path;
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail(`${label} is not strict UTF-8`);
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

async function exactExecutableSha256(path, label) {
  if (resolve(path) !== path) fail(`${label} path is not absolute and normalized`);
  const before = await lstat(path).catch(() => null);
  if (!before?.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size < 1 || before.size > MAX_EXECUTABLE_BYTES) fail(`${label} is not a bounded single-link regular file`);
  if (process.platform !== "win32" && ((before.mode & 0o111) === 0 || (before.mode & 0o022) !== 0)) fail(`${label} is not executable or is group/world writable`);
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.size !== before.size || opened.dev !== before.dev || opened.ino !== before.ino) fail(`${label} changed during inspection`);
    const digest = createHash("sha256"), buffer = Buffer.allocUnsafe(1024 * 1024);
    let total = 0;
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      total += bytesRead;
      if (total > MAX_EXECUTABLE_BYTES) fail(`${label} exceeded its byte ceiling`);
      digest.update(buffer.subarray(0, bytesRead));
    }
    const after = await handle.stat();
    if (total !== opened.size || after.size !== opened.size || after.mtimeMs !== opened.mtimeMs || after.ctimeMs !== opened.ctimeMs) fail(`${label} changed during inspection`);
    return digest.digest("hex");
  } finally { await handle.close(); }
}

function isWithin(path, root) {
  const difference = relative(root, path);
  return difference === "" || difference !== ".." && !difference.startsWith(`..${sep}`) && !isAbsolute(difference);
}

function validatePathCustody(config, outputPath) {
  const writable = [config.stateRoot, config.workspaceRoot, ...(config.researchRuntime ? [config.researchRuntime.researchCourierQueueRoot] : []), ...(config.knowledgeRuntime ? [config.knowledgeRuntime.vaultRoot] : [])];
  for (let left = 0; left < writable.length; left += 1) for (let right = left + 1; right < writable.length; right += 1) {
    if (isWithin(writable[left], writable[right]) || isWithin(writable[right], writable[left])) fail("goal controller writable roots overlap");
  }
  const protectedPaths = [outputPath, config.policyPath, config.objectStore, config.executorPath, config.runtime.dockerPath,
    ...(config.modelBackendLaunchPath ? [config.modelBackendLaunchPath] : []),
    ...(config.capabilityRuntime ? [config.capabilityRuntime.allowedSignersPath, config.capabilityRuntime.sshKeygenPath, config.capabilityRuntime.dockerConfigPath] : []),
    ...(config.knowledgeRuntime ? [config.knowledgeRuntime.credentialSourcePath] : [])];
  for (const protectedPath of protectedPaths) for (const writableRoot of writable) {
    if (isWithin(protectedPath, writableRoot)) fail("goal controller immutable input is inside a controller-writable root");
  }
  for (const writableRoot of writable) if (isWithin(config.objectStore, writableRoot) || isWithin(writableRoot, config.objectStore)) fail("goal controller object store overlaps a writable root");
}

function exactArgumentValue(args, name, label) {
  const indexes = args.flatMap((value, index) => value === name ? [index] : []);
  if (indexes.length !== 1 || indexes[0] + 1 >= args.length) fail(`exact model backend launch ${label} is ambiguous`);
  return args[indexes[0] + 1];
}

function validateExactPixelLabels(args, expected, label) {
  const observed = {};
  for (let index = 0; index < args.length; index += 1) if (args[index] === "--label") {
    const value = args[index + 1] ?? "", separator = value.indexOf("=");
    if (separator < 1) fail(`exact model backend ${label} label vector is malformed`);
    const name = value.slice(0, separator), content = value.slice(separator + 1);
    if (name === "com.osmantic.pixel.work-role" || name.startsWith("com.osmantic.pixel.work-model-")) {
      if (Object.hasOwn(observed, name)) fail(`exact model backend ${label} label vector is duplicated`);
      observed[name] = content;
    }
  }
  if (canonical(observed) !== canonical(expected)) fail(`exact model backend ${label} labels differ from its reviewed bindings`);
}

function validateExactModelBackendLaunchVector(launch, environment, policy) {
  const containerArgs = launch.container.args, networkArgs = launch.network.args;
  if (containerArgs[0] !== "container" || containerArgs[1] !== "create"
    || exactArgumentValue(containerArgs, "--name", "container name") !== environment.runtime.backendContainerName) fail("exact model backend launch container target differs from controller runtime");
  let serverArgs;
  try { serverArgs = modelBackendServerArgumentsFromLaunch({ launch, policy }); }
  catch (error) { fail(`exact model backend launch image or provider wrapper differs from controller policy: ${error.message}`); }
  if (policy.localModel.provider === "llama.cpp") {
    if (serverArgs[0] !== "--model" || serverArgs[1] !== "/models/model.gguf"
      || exactArgumentValue(serverArgs, "--alias", "model alias") !== policy.localModel.id) fail("exact llama.cpp model backend arguments differ from controller policy");
  } else if (policy.localModel.provider === "vllm") {
    if (serverArgs[0] !== "/models/model"
      || exactArgumentValue(serverArgs, "--served-model-name", "served model name") !== policy.localModel.id) fail("exact vLLM model backend arguments differ from controller policy");
  } else fail("exact model backend launch provider is unsupported");
  if (networkArgs[0] !== "network" || networkArgs[1] !== "create" || networkArgs.at(-1) !== environment.runtime.backendNetworkName) fail("exact model backend launch network target differs from controller runtime");
  const prepared = { policy, bindings: launch.bindings };
  validateExactPixelLabels(containerArgs, modelBackendIdentityLabels(prepared), "container");
  validateExactPixelLabels(networkArgs, modelBackendNetworkIdentityLabels(prepared), "network");
}

function validateGoalBundle(goal, jobs, manifest) {
  const goalErrors = validateWorkGoal(goal), manifestErrors = validateWorkGoalBundleManifest(manifest);
  if (goalErrors.length || manifestErrors.length || !Array.isArray(jobs) || jobs.length !== goal?.milestones?.length) fail(`prepared goal bundle is invalid: ${goalErrors[0] ?? manifestErrors[0] ?? "child set differs"}`);
  if (manifest.goalId !== goal.goalId || manifest.goalSha256 !== sha(goal) || manifest.jobsSha256 !== sha(jobs) || manifest.milestones !== jobs.length) fail("prepared goal bundle hashes or identity differ");
  const jobsById = new Map();
  for (const job of jobs) {
    const errors = validateWorkJob(job);
    if (errors.length || jobsById.has(job.jobId)) fail(`prepared goal child registry is invalid: ${errors[0] ?? "duplicate job"}`);
    jobsById.set(job.jobId, job);
  }
  if (goal.milestones.some((milestone, index) => {
    const job = jobsById.get(milestone.jobId);
    return !job || jobs[index]?.jobId !== job.jobId || milestone.jobSha256 !== sha(job) || milestone.profile !== job.profile || job.dataClassification !== goal.dataClassification;
  })) fail("prepared goal child registry differs from the immutable goal");
}

async function writePrivate(path, value) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

export async function runGoalControllerPrepareCommand(argv, dependencies = {}) {
  const options = parseArguments(argv), expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("goal controller preparation owner is invalid");
  if (dependencies.bindingOutputPath !== undefined && typeof dependencies.bindingOutputPath !== "string") fail("goal controller binding output path is invalid");
  const bindingOutputPath = dependencies.bindingOutputPath === undefined ? options.outputPath : resolve(dependencies.bindingOutputPath);
  if (dependencies.bindingOutputPath !== undefined && dependencies.bindingOutputPath !== bindingOutputPath) fail("goal controller binding output path is invalid");
  const outputName = basename(options.outputPath);
  if (!OUTPUT_RE.test(outputName) || join(dirname(options.outputPath), outputName) !== options.outputPath) fail("goal controller output directory name is invalid");
  if (basename(bindingOutputPath) !== outputName || join(dirname(bindingOutputPath), outputName) !== bindingOutputPath) fail("goal controller binding output directory is invalid");
  await privateDirectory(options.sourcePath, "prepared goal bundle", expectedOwnerUid);
  await privateDirectory(dirname(options.outputPath), "goal controller output parent", expectedOwnerUid);
  if (
    isWithin(options.outputPath, options.sourcePath) || isWithin(options.sourcePath, options.outputPath)
    || isWithin(bindingOutputPath, options.sourcePath) || isWithin(options.sourcePath, bindingOutputPath)
  ) fail("goal controller output and prepared source bundle overlap");
  if (canonical((await readdir(options.sourcePath)).sort()) !== canonical(EXPECTED_SOURCE_FILES)) fail("prepared goal bundle file set is invalid");
  if (await lstat(options.outputPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("goal controller output already exists");
  const [goal, jobs, sourceManifest, environment] = await Promise.all([
    readPrivateJson(join(options.sourcePath, "goal.json"), MAX_JSON_BYTES, "prepared work goal", expectedOwnerUid),
    readPrivateJson(join(options.sourcePath, "jobs.json"), MAX_JSON_BYTES, "prepared goal child jobs", expectedOwnerUid),
    readPrivateJson(join(options.sourcePath, "goal-bundle.json"), MAX_JSON_BYTES, "prepared goal manifest", expectedOwnerUid),
    readPrivateJson(options.environmentPath, MAX_JSON_BYTES, "private goal controller environment", expectedOwnerUid),
  ]);
  validateGoalBundle(goal, jobs, sourceManifest);
  const environmentErrors = validateWorkGoalControllerEnvironment(environment);
  if (environmentErrors.length) fail(`private goal controller environment is invalid: ${environmentErrors[0]}`);
  if (process.platform !== "win32" && environment.runtime.uid !== expectedOwnerUid) {
    fail("private goal controller worker UID must equal the owner UID of its private executor and workspaces");
  }
  if (environment.modelBackendLaunchPath) for (const writableRoot of [environment.stateRoot, environment.workspaceRoot, ...(environment.researchRuntime ? [environment.researchRuntime.researchCourierQueueRoot] : []), ...(environment.knowledgeRuntime ? [environment.knowledgeRuntime.vaultRoot] : [])]) {
    if (isWithin(environment.modelBackendLaunchPath, writableRoot) || isWithin(writableRoot, environment.modelBackendLaunchPath)) fail("exact model backend launch overlaps a controller-writable root");
  }
  for (const path of [environment.stateRoot, environment.policyPath, environment.objectStore, environment.workspaceRoot, environment.executorPath, environment.runtime.dockerPath,
    ...(environment.modelBackendLaunchPath ? [environment.modelBackendLaunchPath] : []),
    ...(environment.researchRuntime ? [environment.researchRuntime.researchCourierQueueRoot] : []),
    ...(environment.capabilityRuntime ? [environment.capabilityRuntime.controllerPolicyPath, environment.capabilityRuntime.allowedSignersPath, environment.capabilityRuntime.sshKeygenPath, environment.capabilityRuntime.dockerConfigPath] : []),
    ...(environment.knowledgeRuntime ? [environment.knowledgeRuntime.vaultRoot, environment.knowledgeRuntime.credentialSourcePath] : [])]) {
    if (resolve(path) !== path) fail("private goal controller environment paths must be absolute and normalized");
  }
  const requiresResearch = jobs.some((job) => job.profile === "researcher");
  if (requiresResearch !== Boolean(environment.researchRuntime)) fail("goal controller research runtime presence differs from its exact jobs");
  const requiresKnowledge = jobs.some((job) => job.knowledge !== undefined);
  if (requiresKnowledge !== Boolean(environment.knowledgeRuntime)) fail("goal controller knowledge runtime presence differs from its exact jobs");
  await Promise.all([
    privateDirectory(environment.stateRoot, "goal controller state root", expectedOwnerUid),
    privateDirectory(environment.objectStore, "goal controller object store", expectedOwnerUid),
    privateDirectory(environment.workspaceRoot, "goal controller workspace root", expectedOwnerUid),
    ...(environment.researchRuntime ? [privateDirectory(environment.researchRuntime.researchCourierQueueRoot, "goal controller research queue", expectedOwnerUid)] : []),
    ...(environment.knowledgeRuntime ? [privateDirectory(environment.knowledgeRuntime.vaultRoot, "goal controller knowledge vault", expectedOwnerUid)] : []),
  ]);
  const policy = await readPrivateJson(environment.policyPath, MAX_JSON_BYTES, "private work policy", expectedOwnerUid);
  const policyErrors = validateWorkPolicy(policy);
  if (policyErrors.length) fail(`private work policy is invalid: ${policyErrors[0]}`);
  for (const job of jobs) {
    try { validateWorkJobAgainstPolicy(policy, job); } catch (error) { fail(`goal child differs from the private work policy: ${error.message}`); }
  }
  let modelBackendLaunch = null;
  if (environment.modelBackendLaunchPath) {
    modelBackendLaunch = await readPrivateJson(environment.modelBackendLaunchPath, MAX_JSON_BYTES, "private exact model backend launch", expectedOwnerUid);
    const launchErrors = validateWorkModelBackendLaunch(modelBackendLaunch);
    if (launchErrors.length) fail(`private exact model backend launch is invalid: ${launchErrors[0]}`);
    if (modelBackendLaunch.bindings.policySha256 !== sha(policy) || modelBackendLaunch.bindings.environmentSha256 !== sha(environment)
      || modelBackendLaunch.bindings.artifactSha256 !== policy.localModel.modelArtifactSha256
      || modelBackendLaunch.container.command !== environment.runtime.dockerPath || modelBackendLaunch.network.command !== environment.runtime.dockerPath
    ) fail("exact model backend launch differs from controller policy, environment, or runtime");
    validateExactModelBackendLaunchVector(modelBackendLaunch, environment, policy);
  }
  let capabilityPolicy = null;
  if (environment.capabilityRuntime) {
    capabilityPolicy = await readPrivateJson(environment.capabilityRuntime.controllerPolicyPath, MAX_JSON_BYTES, "private capability controller policy", expectedOwnerUid);
    validateGoalCapabilityBindings({ capabilityRuntime: environment.capabilityRuntime, jobs, policy: capabilityPolicy });
    const signers = await readBoundedRegularFile(environment.capabilityRuntime.allowedSignersPath, 1048576, "private capability allowed signers");
    if (signers.details.nlink !== 1 || signers.details.size < 1 || process.platform !== "win32" && (signers.details.uid !== expectedOwnerUid || (signers.details.mode & 0o077) !== 0)) fail("private capability allowed signers are not owner-private and single-link");
    await privateDirectory(environment.capabilityRuntime.dockerConfigPath, "empty capability Docker configuration", expectedOwnerUid);
    if ((await readdir(environment.capabilityRuntime.dockerConfigPath)).length !== 0) fail("capability Docker configuration must be empty and credential-free");
  }
  const [executorSha256, dockerSha256] = await Promise.all([
    exactExecutableSha256(environment.executorPath, "pinned OMP executor"),
    exactExecutableSha256(environment.runtime.dockerPath, "Docker executable"),
  ]);
  if (environment.capabilityRuntime) await exactExecutableSha256(environment.capabilityRuntime.sshKeygenPath, "capability signature verifier");
  if (environment.knowledgeRuntime) {
    validateGoalKnowledgeBindings({ config: { knowledgeRuntime: environment.knowledgeRuntime }, jobs });
    const knowledgeMasterKey = await loadKnowledgeVaultKeyCredential({ credentialPath: environment.knowledgeRuntime.credentialSourcePath, expectedName: environment.knowledgeRuntime.credentialName });
    await auditKnowledgeVault({ root: environment.knowledgeRuntime.vaultRoot, vaultId: environment.knowledgeRuntime.vaultId, masterKey: knowledgeMasterKey, deep: true });
    await knowledgeVaultHead({ root: environment.knowledgeRuntime.vaultRoot, vaultId: environment.knowledgeRuntime.vaultId, masterKey: knowledgeMasterKey, ownerId: environment.knowledgeRuntime.ownerId, clientId: environment.knowledgeRuntime.clientId });
  }
  if (executorSha256 !== policy.executor.sha256) fail("pinned OMP executor differs from the private policy");
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-v1.schema.json", schemaVersion: 1, enabled: true,
    stateRoot: environment.stateRoot, goalPath: join(bindingOutputPath, "goal.json"), jobsPath: join(bindingOutputPath, "jobs.json"),
    policyPath: environment.policyPath, objectStore: environment.objectStore, workspaceRoot: environment.workspaceRoot,
    executorPath: environment.executorPath, archiveLimits: structuredClone(environment.archiveLimits), runtime: {
      ...structuredClone(environment.runtime),
      ...(modelBackendLaunch ? { modelBackendBindings: structuredClone(modelBackendLaunch.bindings), requireExactModelBackendBindings: true } : {}),
    },
    ...(environment.researchRuntime ? { researchRuntime: structuredClone(environment.researchRuntime) } : {}),
    ...(environment.knowledgeRuntime ? { knowledgeRuntime: structuredClone(environment.knowledgeRuntime) } : {}),
    ...(environment.capabilityRuntime ? { capabilityRuntime: {
      ...structuredClone(environment.capabilityRuntime), controllerPolicyPath: join(bindingOutputPath, "capability-policy.json"),
    } } : {}),
    controller: { maxTransitions: 1 },
  };
  validateGoalCycleControllerConfiguration(config);
  validatePathCustody(config, bindingOutputPath);
  const profiles = [...new Set(jobs.map((job) => job.profile))].sort();
  const manifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-bundle-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-goal-controller-prepare", goalId: goal.goalId, goalSha256: sha(goal), jobsSha256: sha(jobs),
    sourceBundleManifestSha256: sha(sourceManifest), environmentSha256: sha(environment), configSha256: sha(config),
    policySha256: sha(policy), executorSha256, profiles, custody: "not-initialized", authority: { ...authority }, boundary,
    dockerSha256,
    ...(modelBackendLaunch ? { modelBackendLaunchSha256: sha(modelBackendLaunch) } : {}),
    ...(capabilityPolicy ? { capabilityPolicySha256: sha(capabilityPolicy), capabilityBindingsSha256: sha(config.capabilityRuntime.bindings) } : {}),
  };
  const manifestErrors = validateWorkGoalControllerBundleManifest(manifest);
  if (manifestErrors.length) fail(`prepared goal controller manifest is invalid: ${manifestErrors[0]}`);
  const staging = join(dirname(options.outputPath), `.pixel-work-controller-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    await writePrivate(join(staging, "goal.json"), goal);
    await writePrivate(join(staging, "jobs.json"), jobs);
    await writePrivate(join(staging, "controller.json"), config);
    if (capabilityPolicy) await writePrivate(join(staging, "capability-policy.json"), capabilityPolicy);
    await writePrivate(join(staging, "controller-bundle.json"), manifest);
    await syncDirectory(staging); await rename(staging, options.outputPath); await syncDirectory(dirname(options.outputPath));
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    const published = await lstat(options.outputPath).catch((lookupError) => lookupError?.code === "ENOENT" ? null : Promise.reject(lookupError));
    if (published || error?.code === "EEXIST" || error?.code === "ENOTEMPTY") fail("goal controller output already exists");
    throw error;
  }
  return Object.freeze({
    schemaVersion: 1, operation: manifest.operation, goalId: manifest.goalId, controllerBundleSha256: sha(manifest), configSha256: manifest.configSha256,
    policySha256: manifest.policySha256, executorSha256, dockerSha256, profiles: [...profiles], custody: manifest.custody,
    outputName, authority: { ...authority }, boundary,
    ...(modelBackendLaunch ? { modelBackendLaunchSha256: manifest.modelBackendLaunchSha256 } : {}),
    ...(capabilityPolicy ? { capabilityPolicySha256: manifest.capabilityPolicySha256, capabilityBindingsSha256: manifest.capabilityBindingsSha256 } : {}),
  });
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalControllerPrepareCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-controller-prepare: ${error instanceof GoalControllerPrepareCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
