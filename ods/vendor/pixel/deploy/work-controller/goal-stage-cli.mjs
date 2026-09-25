import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, rename, rm } from "node:fs/promises";
import { basename, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  canonical, validateJobPlanLease, validateWorkGoal, validateWorkGoalControllerBundleManifest,
  validateWorkGoalStage, validateWorkJob,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import {
  discardPreparedRun, prepareBuilderRun, prepareDataLabRun, prepareResearcherRun, prepareScoutRun,
} from "../work-runner/runner-core.mjs";
import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { goalCheckpointSha256, goalSha256, initializeGoalLedger, recoverGoalLedger } from "./goals.mjs";
import { initializeGoalRunBundle, recoverGoalRunBundles } from "./goal-run-bundles.mjs";

const MAX_JSON_BYTES = 8 * 1024 * 1024;
const SHA_RE = /^[a-f0-9]{64}$/u;
const CONTROLLER_FILES = Object.freeze(["controller-bundle.json", "controller.json", "goal.json", "jobs.json"]);
const CAPABILITY_CONTROLLER_FILES = Object.freeze([...CONTROLLER_FILES, "capability-policy.json"].sort());
const COMPILED_FILES = Object.freeze(["lease.json", "plan.json"]);
const authority = Object.freeze({ containsExactChildLeases: true, createsReadyGoalState: true, grantsExecution: false, activatesService: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Owner-confirmed dormant goal custody only. Staging stores exact expiring child leases and a ready goal checkpoint but starts no worker, schedules no timer, activates no service, and grants no scope expansion, external effect, or completion authority.";

export class GoalStageCliError extends Error {}

function fail(message) { throw new GoalStageCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 6) fail("Usage: goal-stage-cli.mjs --controller-bundle DIR --compiled-jobs DIR --confirm-controller-sha256 HASH");
  const allowed = new Set(["--controller-bundle", "--compiled-jobs", "--confirm-controller-sha256"]), values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("goal staging arguments are invalid, unknown, or duplicated");
    values[key] = key === "--confirm-controller-sha256" ? value : resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`goal staging is missing ${key}`);
  if (!SHA_RE.test(values["--confirm-controller-sha256"])) fail("goal staging requires the exact controller-bundle SHA-256 confirmation");
  return { controllerPath: values["--controller-bundle"], compiledPath: values["--compiled-jobs"], confirmation: values["--confirm-controller-sha256"] };
}

async function privateDirectory(path, label, expectedOwnerUid, create = false) {
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
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

function exactFileSet(names, expected, label) {
  if (canonical([...names].sort()) !== canonical(expected)) fail(`${label} file set is invalid`);
}

function checkedControllerBundle({ manifest, config, goal, jobs, policy, capabilityPolicy, controllerPath, confirmation }) {
  const manifestErrors = validateWorkGoalControllerBundleManifest(manifest), goalErrors = validateWorkGoal(goal);
  if (manifestErrors.length || goalErrors.length || !Array.isArray(jobs) || jobs.length !== goal?.milestones?.length) fail(`goal staging controller bundle is invalid: ${manifestErrors[0] ?? goalErrors[0] ?? "child set differs"}`);
  if (sha(manifest) !== confirmation) fail("goal staging confirmation differs from the exact controller bundle");
  if (
    config.goalPath !== join(controllerPath, "goal.json") || config.jobsPath !== join(controllerPath, "jobs.json")
    || manifest.goalId !== goal.goalId || manifest.configSha256 !== sha(config) || manifest.goalSha256 !== sha(goal)
    || manifest.jobsSha256 !== sha(jobs) || manifest.policySha256 !== sha(policy)
  ) fail("goal staging controller bundle bindings differ");
  if (config.capabilityRuntime) {
    if (!capabilityPolicy || config.capabilityRuntime.controllerPolicyPath !== join(controllerPath, "capability-policy.json") || manifest.capabilityPolicySha256 !== sha(capabilityPolicy) || manifest.capabilityBindingsSha256 !== sha(config.capabilityRuntime.bindings)) fail("goal staging capability policy bindings differ");
  } else if (capabilityPolicy !== null || manifest.capabilityPolicySha256 !== undefined || manifest.capabilityBindingsSha256 !== undefined) fail("goal staging has unexpected capability policy custody");
  const byId = new Map();
  for (const job of jobs) {
    const errors = validateWorkJob(job);
    if (errors.length || byId.has(job.jobId)) fail(`goal staging child registry is invalid: ${errors[0] ?? "duplicate job"}`);
    byId.set(job.jobId, job);
  }
  for (const milestone of goal.milestones) {
    const job = byId.get(milestone.jobId);
    if (!job || milestone.jobSha256 !== sha(job) || milestone.profile !== job.profile || job.dataClassification !== goal.dataClassification) fail("goal staging child registry differs from the immutable goal");
  }
  const profiles = [...new Set(jobs.map((job) => job.profile))].sort();
  if (canonical(manifest.profiles) !== canonical(profiles) || manifest.executorSha256 !== policy.executor.sha256) fail("goal staging controller profile or executor binding differs");
}

async function loadCompiledChildren(compiledPath, jobs, expectedOwnerUid) {
  await privateDirectory(compiledPath, "compiled goal children", expectedOwnerUid);
  const expectedNames = jobs.map((job) => job.jobId).sort();
  exactFileSet(await readdir(compiledPath), expectedNames, "compiled goal child directory");
  const children = [];
  for (const jobId of expectedNames) {
    const directory = join(compiledPath, jobId);
    await privateDirectory(directory, `compiled goal child ${jobId}`, expectedOwnerUid);
    exactFileSet(await readdir(directory), COMPILED_FILES, `compiled goal child ${jobId}`);
    const [plan, lease] = await Promise.all([
      readPrivateJson(join(directory, "plan.json"), MAX_JSON_BYTES, `compiled plan ${jobId}`, expectedOwnerUid),
      readPrivateJson(join(directory, "lease.json"), MAX_JSON_BYTES, `compiled lease ${jobId}`, expectedOwnerUid),
    ]);
    const job = jobs.find((entry) => entry.jobId === jobId), errors = validateJobPlanLease(job, plan, lease);
    if (errors.length || plan.jobId !== jobId || plan.requestSha256 !== sha(job)) fail(`compiled goal child ${jobId} differs from its immutable job: ${errors[0] ?? "binding differs"}`);
    children.push({ job, plan, lease, planSha256: sha(plan), leaseSha256: sha(lease) });
  }
  return children;
}

function prepareFunction(profile) {
  if (profile === "scout") return prepareScoutRun;
  if (profile === "builder") return prepareBuilderRun;
  if (profile === "researcher") return prepareResearcherRun;
  if (profile === "data-lab") return prepareDataLabRun;
  fail("goal staging child profile is unsupported");
}

async function deriveWorkspaceSnapshots(children, config, policy, now) {
  const preparedChildren = [];
  for (const child of children) {
    let prepared;
    try {
      prepared = await prepareFunction(child.job.profile)({
        plan: child.plan, lease: child.lease, policy, objectStore: config.objectStore, workspaceRoot: config.workspaceRoot,
        executorPath: config.executorPath, archiveLimits: config.archiveLimits, now,
      });
      if (!SHA_RE.test(prepared?.workspace?.sha256 ?? "")) fail(`goal staging child ${child.job.jobId} produced an invalid workspace fingerprint`);
      preparedChildren.push({ ...child, workspaceSnapshotSha256: prepared.workspace.sha256 });
    } finally {
      if (prepared) await discardPreparedRun(prepared);
    }
  }
  return preparedChildren;
}

async function initializeExactChild({ config, goal, jobs, child, now, suffix }) {
  try {
    const initialized = await initializeGoalRunBundle({
      stateRoot: config.stateRoot, goal, jobs, jobId: child.job.jobId, plan: child.plan, lease: child.lease,
      workspaceSnapshotSha256: child.workspaceSnapshotSha256, now, suffix,
    });
    return initialized.sha256;
  } catch (error) {
    if (!/already initialized/u.test(error?.message ?? "")) throw error;
    const recovered = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: child.job.jobId });
    if (
      recovered.bundles.length !== 1 || recovered.head.purpose !== "initial" || canonical(recovered.head.plan) !== canonical(child.plan)
      || canonical(recovered.head.lease) !== canonical(child.lease) || recovered.head.workspaceSnapshotSha256 !== child.workspaceSnapshotSha256
    ) fail(`existing goal child custody ${child.job.jobId} differs from the confirmed staging input`);
    return recovered.headSha256;
  }
}

async function initializeExactGoal({ config, goal, jobs, now, suffix }) {
  try { return (await initializeGoalLedger({ stateRoot: config.stateRoot, goal, jobs, now, suffix })).sha256; }
  catch (error) {
    if (!/already initialized/u.test(error?.message ?? "")) throw error;
    const recovered = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
    if (recovered.checkpoints.length !== 1 || recovered.head.state !== "ready" || recovered.head.active !== null) fail("existing goal state is not an exact dormant staging checkpoint");
    return recovered.headSha256;
  }
}

function stableStage(record) {
  const value = structuredClone(record); delete value.stageId; delete value.stagedAt; return value;
}

async function readStageDirectory(path, expectedOwnerUid) {
  await privateDirectory(path, "staged goal marker", expectedOwnerUid);
  exactFileSet(await readdir(path), ["stage.json"], "staged goal marker");
  const record = await readPrivateJson(join(path, "stage.json"), MAX_JSON_BYTES, "staged goal record", expectedOwnerUid);
  const errors = validateWorkGoalStage(record); if (errors.length) fail(`staged goal record is invalid: ${errors[0]}`);
  return record;
}

async function publishStageRecord({ stateRoot, record, expectedOwnerUid }) {
  const root = join(stateRoot, "goal-staging"); await privateDirectory(root, "goal staging root", expectedOwnerUid, true);
  const destination = join(root, record.goalId), existing = await lstat(destination).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (existing) return { record: await readStageDirectory(destination, expectedOwnerUid), created: false };
  const temporary = join(root, `.stage-${randomBytes(8).toString("hex")}`);
  await mkdir(temporary, { mode: 0o700 });
  try {
    const handle = await open(join(temporary, "stage.json"), "wx", 0o600);
    try { await handle.writeFile(`${JSON.stringify(record, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
    if (process.platform !== "win32") { const handle = await open(temporary, constants.O_RDONLY); try { await handle.sync(); } finally { await handle.close(); } }
    await rename(temporary, destination);
    if (process.platform !== "win32") { const handle = await open(root, constants.O_RDONLY); try { await handle.sync(); } finally { await handle.close(); } }
    return { record, created: true };
  } catch (error) {
    await rm(temporary, { recursive: true, force: true }).catch(() => {});
    if (!["EEXIST", "ENOTEMPTY", "EPERM"].includes(error?.code)) throw error;
    return { record: await readStageDirectory(destination, expectedOwnerUid), created: false };
  }
}

function receipt(record, action) {
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-stage", action, goalId: record.goalId,
    controllerBundleSha256: record.controllerBundleSha256, goalCheckpointSha256: record.goalCheckpointSha256,
    children: record.children.length, state: record.state, authority: { ...authority }, boundary,
  });
}

async function validateExistingStageState({ record, config, goal, jobs, children }) {
  const goalLedger = await recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs });
  if (!goalLedger.checkpoints.length || goalCheckpointSha256(goalLedger.checkpoints[0]) !== record.goalCheckpointSha256) fail("existing staged goal initial checkpoint differs from its live ledger");
  const recordsByJob = new Map(record.children.map((child) => [child.jobId, child]));
  for (const child of children) {
    const expected = recordsByJob.get(child.job.jobId);
    const custody = await recoverGoalRunBundles({ stateRoot: config.stateRoot, goal, jobs, jobId: child.job.jobId });
    const initial = custody.bundles[0];
    if (
      !expected || !initial || initial.purpose !== "initial" || sha(initial) !== expected.custodyBundleSha256
      || sha(initial.plan) !== child.planSha256 || sha(initial.lease) !== child.leaseSha256
      || initial.workspaceSnapshotSha256 !== expected.workspaceSnapshotSha256
    ) fail(`existing staged goal child ${child.job.jobId} differs from its live custody`);
  }
}

export async function runGoalStageCommand(argv, dependencies = {}) {
  const options = parseArguments(argv), expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("goal staging owner is invalid");
  if (dependencies.afterChildInitialized !== undefined && typeof dependencies.afterChildInitialized !== "function") fail("goal staging child-initialization hook is invalid");
  await privateDirectory(options.controllerPath, "goal controller bundle", expectedOwnerUid);
  const controllerFiles = await readdir(options.controllerPath);
  if (![CONTROLLER_FILES, CAPABILITY_CONTROLLER_FILES].some((expected) => canonical([...controllerFiles].sort()) === canonical([...expected].sort()))) fail("goal controller bundle file set is invalid");
  const manifest = await readPrivateJson(join(options.controllerPath, "controller-bundle.json"), MAX_JSON_BYTES, "goal controller bundle manifest", expectedOwnerUid);
  const { config, goal, jobs, policy, capabilityPolicy } = await loadGoalCycleConfiguration(join(options.controllerPath, "controller.json"), { expectedOwnerUid });
  exactFileSet(controllerFiles, config.capabilityRuntime ? CAPABILITY_CONTROLLER_FILES : CONTROLLER_FILES, "goal controller bundle");
  checkedControllerBundle({ manifest, config, goal, jobs, policy, capabilityPolicy, controllerPath: options.controllerPath, confirmation: options.confirmation });
  const children = await loadCompiledChildren(options.compiledPath, jobs, expectedOwnerUid);
  const stagingRoot = join(config.stateRoot, "goal-staging"), stagedPath = join(stagingRoot, goal.goalId);
  const existing = await lstat(stagedPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (existing) {
    const record = await readStageDirectory(stagedPath, expectedOwnerUid);
    const expectedChildren = children.map((child) => ({ jobId: child.job.jobId, planSha256: child.planSha256, leaseSha256: child.leaseSha256 }));
    if (
      record.controllerBundleSha256 !== options.confirmation || record.configSha256 !== sha(config) || record.goalSha256 !== goalSha256(goal)
      || record.jobsSha256 !== sha(jobs) || record.policySha256 !== sha(policy)
      || canonical(record.children.map(({ jobId, planSha256, leaseSha256 }) => ({ jobId, planSha256, leaseSha256 }))) !== canonical(expectedChildren)
    ) fail("existing staged goal differs from the confirmed controller and child custody");
    await validateExistingStageState({ record, config, goal, jobs, children });
    return receipt(record, "already-staged");
  }
  const now = dependencies.now ?? new Date();
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < 0) fail("goal staging time is invalid");
  const prepared = await deriveWorkspaceSnapshots(children, config, policy, now);
  const custody = [];
  for (let index = 0; index < prepared.length; index += 1) {
    const suffix = dependencies.childSuffix?.(prepared[index].job.jobId, index) ?? randomBytes(6).toString("hex");
    if (!/^[a-f0-9]{12}$/u.test(suffix)) fail("goal staging child suffix is invalid");
    const custodyBundleSha256 = await initializeExactChild({ config, goal, jobs, child: prepared[index], now, suffix });
    custody.push({
      jobId: prepared[index].job.jobId, planSha256: prepared[index].planSha256, leaseSha256: prepared[index].leaseSha256,
      workspaceSnapshotSha256: prepared[index].workspaceSnapshotSha256, custodyBundleSha256,
    });
    if (dependencies.afterChildInitialized) await dependencies.afterChildInitialized({ jobId: prepared[index].job.jobId, index });
  }
  custody.sort((left, right) => left.jobId.localeCompare(right.jobId));
  const goalSuffix = dependencies.goalSuffix ?? randomBytes(6).toString("hex");
  const stageSuffix = dependencies.stageSuffix ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(goalSuffix) || !/^[a-f0-9]{12}$/u.test(stageSuffix)) fail("goal staging identity suffix is invalid");
  const goalCheckpointSha256 = await initializeExactGoal({ config, goal, jobs, now, suffix: goalSuffix });
  const record = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-stage-v1.schema.json", schemaVersion: 1,
    stageId: `workgoalstage-${String(now.getTime()).padStart(13, "0")}-${stageSuffix}`, stagedAt: now.toISOString(), goalId: goal.goalId,
    controllerBundleSha256: options.confirmation, configSha256: sha(config), goalSha256: goalSha256(goal), jobsSha256: sha(jobs),
    policySha256: sha(policy), children: custody, goalCheckpointSha256, state: "staged-inactive", authority: { ...authority }, boundary,
  };
  const errors = validateWorkGoalStage(record); if (errors.length) fail(`staged goal record is invalid: ${errors[0]}`);
  const published = await publishStageRecord({ stateRoot: config.stateRoot, record, expectedOwnerUid });
  if (canonical(stableStage(published.record)) !== canonical(stableStage(record))) fail("concurrent staged goal differs from the confirmed custody");
  return receipt(published.record, published.created ? "staged" : "already-staged");
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalStageCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-goal-stage: ${error instanceof GoalStageCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
