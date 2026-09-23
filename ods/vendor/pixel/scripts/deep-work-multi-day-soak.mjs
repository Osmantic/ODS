import { createHash, randomBytes } from "node:crypto";
import { execFile } from "node:child_process";
import { constants } from "node:fs";
import { chmod, link, lstat, mkdir, open, readFile, readdir, rename, rm, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { compileScout } from "../deploy/work-broker/broker.mjs";
import { appendCheckpoint, checkpointSha256, recoverCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import { createGoalCandidateResolver } from "../deploy/work-controller/goal-candidate-adapter.mjs";
import { initializeGoalRunBundle, recoverGoalRunBundles } from "../deploy/work-controller/goal-run-bundles.mjs";
import { goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";
import { runGoalCycle } from "../deploy/work-controller/goal-runtime.mjs";
import { canonical, validateWorkGoal, validateWorkJob, validateWorkPolicy } from "./lib/work-contract.mjs";
import { validateJsonSchema } from "./lib/json-schema.mjs";
import { readBoundedRegularFile } from "./lib/secure-files.mjs";

const sourceRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const scriptPath = fileURLToPath(import.meta.url);
const campaignSchemaUrl = new URL("../schemas/deep-work-multi-day-soak-v1.schema.json", import.meta.url);
const invocationSchemaUrl = new URL("../schemas/deep-work-multi-day-soak-invocation-v1.schema.json", import.meta.url);
const evidenceSchemaUrl = new URL("../schemas/deep-work-multi-day-soak-evidence-v1.schema.json", import.meta.url);
const COMMIT_RE = /^[a-f0-9]{40}$/u;
const SYSTEMD_INVOCATION_RE = /^[a-fA-F0-9]{32}$/u;
const BOOT_ID_RE = /^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$/u;
const OUTPUT_NAME_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const RECORD_RE = /^(0{0,3}[0-9]{1,4})\.json$/u;
const MAX_JSON_BYTES = 16 * 1024 * 1024;
const MAX_INVOCATION_BYTES = 64 * 1024;
const MAX_RUNTIME_FILE_BYTES = 64 * 1024 * 1024;
const MAX_RUNTIME_SNAPSHOT_BYTES = 512 * 1024 * 1024;
export const soakRuntimeRoots = Object.freeze([
  "scripts/lib", "deploy/work-broker", "deploy/work-controller", "deploy/work-model-proxy",
  "deploy/work-runner", "deploy/work-research-broker", "schemas",
]);
const runFile = promisify(execFile);
const schedule = Object.freeze({
  intervalSeconds: 3600,
  minimumGapSeconds: 3300,
  maximumGapSeconds: 7200,
  minimumElapsedSeconds: 172800,
  milestones: 24,
  expectedCycles: 49,
});
const privacy = Object.freeze({ providerCalls: 0, credentialInputs: 0, networkRequests: 0, externalEffects: 0, productionDeploymentsTouched: 0 });
const authority = Object.freeze({ grantsWork: false, grantsLease: false, grantsCredentials: false, grantsProviderUse: false, grantsExternalEffects: false, grantsDeployment: false, grantsRelease: false });
const campaignBoundary = "Credential-free disposable multi-day qualification only. Synthetic child transitions exercise durable production goal custody without invoking a worker, provider, network, credential, external effect, deployment, or release action.";
const invocationBoundary = "Content-free append-only systemd invocation fact. It grants no work, lease, replay, credential, provider, network, external effect, deployment, completion, or release authority.";
const evidenceBoundary = "Credential-free disposable qualification evidence only. It proves elapsed reconciliation, restart, reboot, lease-refresh, and exact durable accounting behavior but grants no work, provider, deployment, publication, or release authority.";

export class DeepWorkMultiDaySoakError extends Error {}

function fail(message) { throw new DeepWorkMultiDaySoakError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function suffix() { return randomBytes(6).toString("hex"); }
function recordName(sequence) { return `${String(sequence).padStart(4, "0")}.json`; }
function observedDate(value, label) {
  const date = value instanceof Date ? new Date(value.getTime()) : new Date(value);
  if (!Number.isSafeInteger(date.getTime()) || date.getTime() < 0) fail(`${label} is invalid`);
  return date;
}
function within(path, root) {
  const difference = relative(root, path);
  return difference === "" || difference !== ".." && !difference.startsWith(`..${sep}`) && !isAbsolute(difference);
}
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

async function privateDirectory(path, label, create = false, expectedOwnerUid = process.geteuid?.() ?? 0) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return resolve(path);
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid = process.geteuid?.() ?? 0) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

async function writePrivate(path, value) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(typeof value === "string" || Buffer.isBuffer(value) ? value : `${JSON.stringify(value, null, 2)}\n`); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

async function installedSchema(url) {
  return JSON.parse(await readFile(url, "utf8"));
}

async function walkRuntimeFiles(root, files) {
  const info = await lstat(root).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.mode & 0o022) !== 0) fail("soak runtime source directory is unsafe or writable by another identity");
  for (const entry of (await readdir(root, { withFileTypes: true })).sort((left, right) => left.name.localeCompare(right.name, "en"))) {
    const path = join(root, entry.name);
    if (entry.isDirectory()) await walkRuntimeFiles(path, files);
    else if (entry.isFile()) files.push(path);
    else fail("soak runtime source contains a link or unsupported entry");
  }
}

async function runtimeSnapshotSha256() {
  const files = [scriptPath];
  for (const relativeRoot of soakRuntimeRoots) await walkRuntimeFiles(join(sourceRoot, relativeRoot), files);
  const unique = [...new Set(files.map((path) => resolve(path)))].sort();
  const records = [];
  let total = 0;
  for (const path of unique) {
    const { bytes, details } = await readBoundedRegularFile(path, MAX_RUNTIME_FILE_BYTES, "soak runtime source file");
    total += bytes.length;
    if (total > MAX_RUNTIME_SNAPSHOT_BYTES || details.nlink !== 1 || process.platform !== "win32" && (details.mode & 0o022) !== 0) fail("soak runtime source snapshot is unsafe or oversized");
    records.push({ path: relative(sourceRoot, path).replaceAll("\\", "/"), bytes: bytes.length, sha256: sha(bytes) });
  }
  return sha({ schemaVersion: 1, files: records });
}

async function verifyGitSourceIdentity(sourceCommit, sourceTree) {
  const environment = {
    PATH: process.env.PATH ?? (process.platform === "win32" ? "" : "/usr/bin:/bin"), LANG: "C.UTF-8", LC_ALL: "C.UTF-8",
    GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: process.platform === "win32" ? "NUL" : "/dev/null",
    ...(process.platform === "win32" && process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {}),
  };
  const gitArguments = (arguments_) => ["-c", `safe.directory=${sourceRoot}`, ...arguments_];
  let status, commit, tree;
  try {
    status = await runFile("git", gitArguments(["status", "--porcelain=v1", "--untracked-files=all"]), { cwd: sourceRoot, env: environment, encoding: "utf8", timeout: 30000, maxBuffer: 1024 * 1024 });
    commit = await runFile("git", gitArguments(["rev-parse", "HEAD"]), { cwd: sourceRoot, env: environment, encoding: "utf8", timeout: 30000, maxBuffer: 1024 * 1024 });
    tree = await runFile("git", gitArguments(["rev-parse", "HEAD^{tree}"]), { cwd: sourceRoot, env: environment, encoding: "utf8", timeout: 30000, maxBuffer: 1024 * 1024 });
  } catch { fail("soak initialization requires an exact clean Git source"); }
  if (status.stdout !== "" || status.stderr !== "" || commit.stderr !== "" || tree.stderr !== "" || commit.stdout.trim() !== sourceCommit || tree.stdout.trim() !== sourceTree) fail("soak source commit/tree differs from the clean checked-out source");
}

function qualificationBudgets() {
  return {
    maxRuntimeSeconds: 900, maxIterations: 2, maxToolCalls: 20, maxConcurrentSubagents: 1,
    maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1,
    maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576,
    maxFailures: 1, noProgressLimit: 1,
  };
}

function qualificationJob(jobId, createdAt, contentSha256, bytes, index) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt, requester: "pixel", profile: "scout",
    objective: `Reconcile durable hourly qualification milestone ${index + 1}.`,
    acceptanceCriteria: ["A bounded independently verified durable qualification record exists"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: bytes, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: qualificationBudgets(), outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

function qualificationGoal(jobs, createdAt, epoch, goalSuffix) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: `workgoal-${epoch}-${goalSuffix}`, createdAt, requester: "pixel",
    objective: "Qualify one immutable durable goal across at least 48 real hours, hourly fresh-process reconciliation, delayed lease refresh, and a controlled host reboot.",
    dataClassification: "internal",
    milestones: jobs.map((job, index) => ({
      milestoneId: `milestone-${String(index + 1).padStart(2, "0")}`, jobId: job.jobId, jobSha256: goalSha256(job), profile: "scout",
      dependsOn: index === 0 ? [] : [`milestone-${String(index).padStart(2, "0")}`],
    })),
    budgets: {
      maxJobs: jobs.length, maxRuntimeSeconds: 900 * jobs.length, maxModelRequests: 5 * jobs.length,
      maxInputTokens: 10000 * jobs.length, maxOutputTokens: 2000 * jobs.length,
      maxNetworkBytes: 1048576 * jobs.length, maxArtifactBytes: 65536 * jobs.length, maxFailures: jobs.length,
    },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
}

function preparedPolicy(source) {
  const policy = structuredClone(source);
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true;
  policy.maxLeaseSeconds = 900;
  return policy;
}

function expectedPaths(root) {
  return {
    root,
    stateRoot: join(root, "state"),
    objectStore: join(root, "objects"),
    goalPath: join(root, "contracts", "goal.json"),
    jobsPath: join(root, "contracts", "jobs.json"),
    policyPath: join(root, "contracts", "policy.json"),
    invocationLedgerRoot: join(root, "invocations"),
  };
}

function validateCampaignPaths(config, configPath) {
  if (resolve(configPath) !== configPath || basename(configPath) !== "campaign.json" || dirname(configPath) !== config.paths.root) fail("soak campaign configuration path is invalid");
  const expected = expectedPaths(config.paths.root);
  if (canonical(config.paths) !== canonical(expected)) fail("soak campaign paths differ from their fixed private layout");
  if (within(config.paths.root, sourceRoot) || within(sourceRoot, config.paths.root)) fail("soak campaign and source tree overlap");
}

function parseInitialize(argv) {
  if (argv.length !== 6) fail("Usage: deep-work-multi-day-soak.mjs initialize --root ABSOLUTE_NEW_DIR --source-commit COMMIT --source-tree TREE");
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!["--root", "--source-commit", "--source-tree"].includes(key) || !value || Object.hasOwn(values, key)) fail("soak initialization arguments are invalid");
    values[key] = value;
  }
  if (resolve(values["--root"]) !== values["--root"] || !OUTPUT_NAME_RE.test(basename(values["--root"]))) fail("soak root must be a canonical absolute new directory");
  if (!COMMIT_RE.test(values["--source-commit"] ?? "") || !COMMIT_RE.test(values["--source-tree"] ?? "")) fail("soak source identity is invalid");
  return { root: values["--root"], sourceCommit: values["--source-commit"], sourceTree: values["--source-tree"] };
}

function parseConfigOnly(argv, command) {
  if (argv.length !== 2 || argv[0] !== "--config" || resolve(argv[1]) !== argv[1]) fail(`Usage: deep-work-multi-day-soak.mjs ${command} --config ABSOLUTE_CAMPAIGN_FILE`);
  return argv[1];
}

function parseCycle(argv) {
  if (argv.length !== 4) fail("Usage: deep-work-multi-day-soak.mjs cycle --config ABSOLUTE_CAMPAIGN_FILE --confirm-config-sha256 HASH");
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!["--config", "--confirm-config-sha256"].includes(key) || !value || Object.hasOwn(values, key)) fail("soak cycle arguments are invalid");
    values[key] = value;
  }
  if (resolve(values["--config"]) !== values["--config"] || !/^[a-f0-9]{64}$/u.test(values["--confirm-config-sha256"] ?? "")) fail("soak cycle configuration confirmation is invalid");
  return { configPath: values["--config"], configSha256: values["--confirm-config-sha256"] };
}

function parseFinalize(argv) {
  if (argv.length !== 4) fail("Usage: deep-work-multi-day-soak.mjs finalize --config ABSOLUTE_CAMPAIGN_FILE --output ABSOLUTE_NEW_FILE");
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!["--config", "--output"].includes(key) || !value || Object.hasOwn(values, key) || resolve(value) !== value) fail("soak finalization arguments are invalid");
    values[key] = value;
  }
  return { configPath: values["--config"], output: values["--output"] };
}

export async function initializeCampaign(options, dependencies = {}) {
  exactKeys(options, ["root", "sourceCommit", "sourceTree"], "soak initialization options");
  const root = resolve(options.root);
  if (root !== options.root || !OUTPUT_NAME_RE.test(basename(root)) || !COMMIT_RE.test(options.sourceCommit ?? "") || !COMMIT_RE.test(options.sourceTree ?? "")) fail("soak initialization options are invalid");
  if (within(root, sourceRoot) || within(sourceRoot, root)) fail("soak campaign and source tree overlap");
  const sourceIdentityVerifier = dependencies.sourceIdentityVerifier ?? verifyGitSourceIdentity;
  if (typeof sourceIdentityVerifier !== "function") fail("soak source identity verifier is invalid");
  await sourceIdentityVerifier(options.sourceCommit, options.sourceTree);
  await privateDirectory(dirname(root), "soak campaign parent");
  if (await lstat(root).then(() => true, () => false)) fail("soak campaign root already exists");
  const now = observedDate(dependencies.now ?? new Date(), "soak initialization time");
  const epoch = String(now.getTime()).padStart(13, "0");
  const identitySuffix = dependencies.identitySuffix ?? suffix();
  if (!/^[a-f0-9]{12}$/u.test(identitySuffix)) fail("soak campaign identity suffix is invalid");
  const staging = join(dirname(root), `.deep-work-soak-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    for (const path of ["contracts", "objects", "state", "invocations", "invocations/records", "invocations/tmp"]) await mkdir(join(staging, path), { mode: 0o700 });
    const content = Buffer.from("pixel deep work multi-day credential-free qualification input\n", "utf8");
    const contentSha256 = sha(content);
    await writePrivate(join(staging, "objects", `${contentSha256}.tar`), content);
    const createdAt = now.toISOString();
    const jobs = Array.from({ length: schedule.milestones }, (_, index) => qualificationJob(
      `work-${epoch}-${(0x100000000000n + BigInt(index)).toString(16)}`, createdAt, contentSha256, content.length, index,
    ));
    const goal = qualificationGoal(jobs, createdAt, epoch, identitySuffix);
    const sourcePolicy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
    const policy = preparedPolicy(sourcePolicy);
    if (validateWorkGoal(goal).length || jobs.some((job) => validateWorkJob(job).length) || validateWorkPolicy(policy).length) fail("generated soak contracts are invalid");
    const entries = [{ id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256, bytes: content.length, classification: "internal", mountMode: "read-only" }];
    const compileTime = new Date(now.getTime() + 2);
    const runs = jobs.map((job, index) => ({
      job,
      compiled: compileScout(job, policy, entries, { now: compileTime, suffix: (0x200000000000n + BigInt(index)).toString(16) }),
      workspaceSnapshotSha256: sha(`workspace:${job.jobId}`),
    }));
    await writePrivate(join(staging, "contracts", "goal.json"), goal);
    await writePrivate(join(staging, "contracts", "jobs.json"), jobs);
    await writePrivate(join(staging, "contracts", "policy.json"), policy);
    await initializeGoalLedger({ stateRoot: join(staging, "state"), goal, jobs, now: new Date(now.getTime() + 1), suffix: "000000000001" });
    for (let index = 0; index < runs.length; index += 1) {
      const run = runs[index];
      await initializeGoalRunBundle({
        stateRoot: join(staging, "state"), goal, jobs, jobId: run.job.jobId,
        plan: run.compiled.plan, lease: run.compiled.lease, workspaceSnapshotSha256: run.workspaceSnapshotSha256,
        now: new Date(now.getTime() + 3), suffix: (0x300000000000n + BigInt(index)).toString(16),
      });
    }
    const paths = expectedPaths(root);
    const config = {
      $schema: "./schemas/deep-work-multi-day-soak-v1.schema.json", schemaVersion: 1,
      operation: "pixel-deep-work-multi-day-soak", campaignId: `deepworksoak-${epoch}-${identitySuffix}`, createdAt,
      source: {
        commit: options.sourceCommit, tree: options.sourceTree, programSha256: sha(await readFile(scriptPath)),
        runtimeSnapshotSha256: await runtimeSnapshotSha256(),
      },
      schedule: { ...schedule }, paths,
      bindings: { goalSha256: sha(goal), jobsSha256: sha(jobs), policySha256: sha(policy), inputObjectSha256: contentSha256, inputObjectBytes: content.length },
      privacy: { ...privacy }, authority: { ...authority }, boundary: campaignBoundary,
    };
    const errors = validateJsonSchema(config, await installedSchema(campaignSchemaUrl));
    if (errors.length) fail(`generated soak campaign is invalid: ${errors[0]}`);
    await writePrivate(join(staging, "campaign.json"), config);
    await syncDirectory(staging);
    await rename(staging, root).catch((error) => { if (["EEXIST", "ENOTEMPTY", "EPERM"].includes(error?.code)) fail("soak campaign root already exists"); throw error; });
    await syncDirectory(dirname(root));
    return Object.freeze({
      schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak-initialize", status: "initialized",
      campaignId: config.campaignId, configPath: join(root, "campaign.json"), expectedCycles: schedule.expectedCycles,
      minimumElapsedSeconds: schedule.minimumElapsedSeconds, providerCalls: 0, credentialInputs: 0, externalEffects: 0,
      boundary: "Content-free initialization receipt only. It grants no schedule, worker, provider, deployment, or release authority.",
    });
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
}

export async function loadCampaign(configPath, options = {}) {
  const expectedOwnerUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("soak campaign owner identity is invalid");
  const path = resolve(configPath);
  const config = await readPrivateJson(path, MAX_JSON_BYTES, "soak campaign configuration", expectedOwnerUid);
  const errors = validateJsonSchema(config, await installedSchema(campaignSchemaUrl));
  if (errors.length) fail(`soak campaign configuration is invalid: ${errors[0]}`);
  validateCampaignPaths(config, path);
  for (const [candidate, label] of [[config.paths.root, "soak campaign root"], [config.paths.stateRoot, "soak state root"], [config.paths.objectStore, "soak object store"], [config.paths.invocationLedgerRoot, "soak invocation ledger"], [join(config.paths.invocationLedgerRoot, "records"), "soak invocation records"], [join(config.paths.invocationLedgerRoot, "tmp"), "soak invocation temporary directory"]]) await privateDirectory(candidate, label, false, expectedOwnerUid);
  if (sha(await readFile(scriptPath)) !== config.source.programSha256) fail("soak controller program differs from initialized exact source");
  if (await runtimeSnapshotSha256() !== config.source.runtimeSnapshotSha256) fail("soak runtime component set differs from initialized exact source");
  const [goal, jobs, policy] = await Promise.all([
    readPrivateJson(config.paths.goalPath, MAX_JSON_BYTES, "soak immutable goal", expectedOwnerUid),
    readPrivateJson(config.paths.jobsPath, MAX_JSON_BYTES, "soak immutable jobs", expectedOwnerUid),
    readPrivateJson(config.paths.policyPath, MAX_JSON_BYTES, "soak immutable policy", expectedOwnerUid),
  ]);
  if (sha(goal) !== config.bindings.goalSha256 || sha(jobs) !== config.bindings.jobsSha256 || sha(policy) !== config.bindings.policySha256) fail("soak immutable contract hash differs from its campaign binding");
  if (validateWorkGoal(goal).length || !Array.isArray(jobs) || jobs.length !== schedule.milestones || jobs.some((job) => validateWorkJob(job).length) || validateWorkPolicy(policy).length) fail("soak immutable contracts are invalid");
  const objectPath = join(config.paths.objectStore, `${config.bindings.inputObjectSha256}.tar`);
  const { bytes, details } = await readBoundedRegularFile(objectPath, 1048576, "soak immutable input object");
  if (details.nlink !== 1 || bytes.length !== config.bindings.inputObjectBytes || sha(bytes) !== config.bindings.inputObjectSha256 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail("soak immutable input object differs from its private binding");
  return Object.freeze({ configPath: path, config, configSha256: sha(config), goal, jobs, policy });
}

function projection(ledger) {
  return {
    state: ledger.head.state, sequence: ledger.head.sequence, checkpointSha256: ledger.headSha256,
    milestonesCompleted: ledger.head.progress.milestonesCompleted, jobsStarted: ledger.head.progress.jobsStarted,
    modelRequests: ledger.head.usage.modelRequests,
  };
}

function validateInvocationSemantics(record, campaign, previous, ids) {
  if (record.campaignId !== campaign.config.campaignId || record.source.commit !== campaign.config.source.commit || record.source.tree !== campaign.config.source.tree || record.source.programSha256 !== campaign.config.source.programSha256 || record.source.runtimeSnapshotSha256 !== campaign.config.source.runtimeSnapshotSha256 || record.source.configSha256 !== campaign.configSha256) fail("soak invocation source binding differs from the campaign");
  if (Date.parse(record.finishedAt) < Date.parse(record.startedAt) || Date.parse(record.startedAt) < Date.parse(campaign.config.createdAt)) fail("soak invocation timing is invalid");
  if (record.sequence === 0 ? record.previousInvocationSha256 !== null : record.previousInvocationSha256 !== sha(previous)) fail("soak invocation hash chain is invalid");
  if (ids.has(record.invocationIdSha256)) fail("soak systemd invocation identifier was reused");
  ids.add(record.invocationIdSha256);
  if (record.before.sequence !== record.sequence || record.after.sequence !== record.sequence + 1) fail("soak goal reconciliation did not advance exactly once");
  if (record.sequence > 0 && canonical(record.before) !== canonical(previous.after)) fail("soak invocation goal custody is discontinuous");
  if (record.sequence < schedule.expectedCycles - 1) {
    const dispatch = record.sequence % 2 === 0;
    const completed = Math.floor(record.sequence / 2);
    if (dispatch) {
      if (record.before.state !== "ready" || record.after.state !== "running" || record.before.milestonesCompleted !== completed || record.after.milestonesCompleted !== completed || record.before.jobsStarted !== completed || record.after.jobsStarted !== completed + 1 || record.cycle.childState !== null) fail("soak dispatch cadence is invalid");
    } else if (record.before.state !== "running" || record.after.state !== "ready" || record.before.milestonesCompleted !== completed || record.after.milestonesCompleted !== completed + 1 || record.before.jobsStarted !== completed + 1 || record.after.jobsStarted !== completed + 1 || record.cycle.childState !== "completed") fail("soak child completion cadence is invalid");
    if (record.cycle.action !== "controller-yield") fail("soak bounded controller did not yield after one transition");
  } else if (record.before.state !== "ready" || record.after.state !== "completed" || record.before.milestonesCompleted !== schedule.milestones || record.after.milestonesCompleted !== schedule.milestones || record.cycle.action !== "terminal" || record.cycle.childState !== null) fail("soak terminal cadence is invalid");
  if (record.before.modelRequests !== record.before.milestonesCompleted || record.after.modelRequests !== record.after.milestonesCompleted) fail("soak invocation usage differs from verified milestone credit");
  if (previous) {
    const gap = (Date.parse(record.startedAt) - Date.parse(previous.startedAt)) / 1000;
    if (!Number.isInteger(gap) || gap < schedule.minimumGapSeconds || gap > schedule.maximumGapSeconds) fail("soak invocation cadence is outside the real-time qualification window");
  }
}

async function recoverInvocationLedger(campaign) {
  const recordsRoot = join(campaign.config.paths.invocationLedgerRoot, "records");
  const names = (await readdir(recordsRoot)).sort();
  if (names.length > schedule.expectedCycles) fail("soak invocation ledger exceeds its exact cycle ceiling");
  const schema = await installedSchema(invocationSchemaUrl), records = [], ids = new Set();
  for (let sequence = 0; sequence < names.length; sequence += 1) {
    if (names[sequence] !== recordName(sequence) || !RECORD_RE.test(names[sequence]) || basename(names[sequence]) !== names[sequence]) fail("soak invocation record sequence is incomplete or unsafe");
    const record = await readPrivateJson(join(recordsRoot, names[sequence]), MAX_INVOCATION_BYTES, "soak invocation record");
    const errors = validateJsonSchema(record, schema);
    if (errors.length || record.sequence !== sequence) fail(`soak invocation record is invalid: ${errors[0] ?? "filename differs"}`);
    validateInvocationSemantics(record, campaign, records.at(-1) ?? null, ids);
    records.push(record);
  }
  return { records, head: records.at(-1) ?? null, headSha256: records.length ? sha(records.at(-1)) : null };
}

async function appendInvocation(campaign, record) {
  const ledger = await recoverInvocationLedger(campaign);
  if (ledger.records.length !== record.sequence || record.previousInvocationSha256 !== ledger.headSha256) fail("soak invocation append is based on stale custody");
  const errors = validateJsonSchema(record, await installedSchema(invocationSchemaUrl));
  if (errors.length) fail(`soak invocation append is invalid: ${errors[0]}`);
  validateInvocationSemantics(record, campaign, ledger.head, new Set(ledger.records.map((item) => item.invocationIdSha256)));
  const temporary = join(campaign.config.paths.invocationLedgerRoot, "tmp", `.invocation-${record.sequence}-${randomBytes(8).toString("hex")}`);
  const destination = join(campaign.config.paths.invocationLedgerRoot, "records", recordName(record.sequence));
  const serialized = `${JSON.stringify(record, null, 2)}\n`;
  if (Buffer.byteLength(serialized) > MAX_INVOCATION_BYTES) fail("soak invocation record exceeds its byte ceiling");
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, destination); await unlink(temporary); await syncDirectory(dirname(destination));
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("soak invocation sequence was already appended");
    throw error;
  }
  return { sha256: sha(record), path: destination };
}

function checkpointUpdate(head, state, overrides = {}) {
  const field = (name) => Object.hasOwn(overrides, name) ? overrides[name] : head[name];
  return {
    state, iteration: overrides.iteration ?? head.iteration,
    usage: { ...head.usage, ...(overrides.usage ?? {}) }, progress: { ...head.progress, ...(overrides.progress ?? {}) },
    workspaceSnapshotSha256: field("workspaceSnapshotSha256"), artifactManifestSha256: field("artifactManifestSha256"),
    workerSessionSha256: field("workerSessionSha256"), verificationEvidenceSha256: field("verificationEvidenceSha256"),
    authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false,
  };
}

async function driveSyntheticChild({ stateRoot, job, plan, lease, childCheckpoint, startedAt }) {
  let head = childCheckpoint;
  while (head.state !== "completed") {
    let state, overrides;
    if (head.state === "authorized") {
      state = "running"; overrides = { iteration: 1, workerSessionSha256: sha(`session:${job.jobId}`) };
    } else if (head.state === "running") {
      state = "verifying"; overrides = { usage: { runtimeSeconds: 1, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 0, artifactBytes: 20 }, artifactManifestSha256: sha(`artifact:${job.jobId}`) };
    } else if (head.state === "verifying") {
      state = "verified"; overrides = { progress: { criteriaPassing: 1, criteriaFailing: 0 }, verificationEvidenceSha256: sha(`verification:${job.jobId}`) };
    } else if (head.state === "verified") {
      state = "completed"; overrides = {};
    } else {
      fail("soak synthetic qualification child reached an unsupported state");
    }
    const now = new Date(Math.max(startedAt.getTime(), Date.parse(head.createdAt) + 1));
    const appended = await appendCheckpoint({
      stateRoot, plan, lease, previousCheckpointSha256: checkpointSha256(head), update: checkpointUpdate(head, state, overrides), now, suffix: suffix(),
    });
    head = appended.checkpoint;
  }
  return head;
}

async function systemdIdentity(dependencies, campaignId) {
  const invocationId = dependencies.invocationId ?? process.env.INVOCATION_ID;
  if (!SYSTEMD_INVOCATION_RE.test(invocationId ?? "")) fail("soak cycle requires a valid systemd INVOCATION_ID");
  const bootId = dependencies.bootId ?? (process.platform === "linux" ? (await readFile("/proc/sys/kernel/random/boot_id", "utf8")).trim() : null);
  if (!BOOT_ID_RE.test(bootId ?? "")) fail("soak cycle requires the Linux boot identifier");
  const cgroup = dependencies.cgroup ?? (process.platform === "linux" ? await readFile("/proc/self/cgroup", "utf8") : null);
  const expectedUnit = `pixel-deep-work-${campaignId}.service`;
  if (typeof cgroup !== "string" || Buffer.byteLength(cgroup) > 65536 || !cgroup.split(/\r?\n/u).some((line) => line.endsWith(`/system.slice/${expectedUnit}`))) fail("soak cycle is not running inside its exact systemd service cgroup");
  return { invocationIdSha256: sha(invocationId.toLowerCase()), bootIdSha256: sha(bootId.toLowerCase()) };
}

export async function runCampaignCycle(configPath, dependencies = {}) {
  const campaign = await loadCampaign(configPath);
  if (dependencies.expectedConfigSha256 !== undefined && dependencies.expectedConfigSha256 !== campaign.configSha256) fail("soak cycle configuration differs from its exact service confirmation");
  const invocationLedger = await recoverInvocationLedger(campaign);
  const goalBefore = await recoverGoalLedger({ stateRoot: campaign.config.paths.stateRoot, goal: campaign.goal, jobs: campaign.jobs });
  if (goalBefore.head.state === "completed") return Object.freeze({
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak-cycle", status: "terminal-noop", campaignId: campaign.config.campaignId,
    completedCycles: invocationLedger.records.length, milestonesCompleted: goalBefore.head.progress.milestonesCompleted,
    providerCalls: 0, credentialInputs: 0, externalEffects: 0, boundary: "Content-free terminal no-op; no durable record or authority was added.",
  });
  if (invocationLedger.records.length >= schedule.expectedCycles) fail("soak cycle ceiling was reached before goal completion");
  const startedAt = observedDate(dependencies.startedAt ?? new Date(), "soak cycle start time");
  if (invocationLedger.head) {
    const gap = (startedAt.getTime() - Date.parse(invocationLedger.head.startedAt)) / 1000;
    if (!Number.isInteger(gap) || gap < schedule.minimumGapSeconds || gap > schedule.maximumGapSeconds) fail("soak cycle start is outside the real-time qualification cadence");
  }
  const identity = await systemdIdentity(dependencies, campaign.config.campaignId);
  if (invocationLedger.records.some((record) => record.invocationIdSha256 === identity.invocationIdSha256)) fail("soak cycle systemd invocation was already recorded");
  const resolveChildRun = createGoalCandidateResolver({
    stateRoot: campaign.config.paths.stateRoot, goal: campaign.goal, jobs: campaign.jobs, policy: campaign.policy,
    objectStore: campaign.config.paths.objectStore, clock: () => new Date(startedAt), suffix,
  });
  let childCompleted = false;
  const result = await runGoalCycle({
    stateRoot: campaign.config.paths.stateRoot, goal: campaign.goal, jobs: campaign.jobs, resolveChildRun,
    driveChild: async (context) => {
      const head = await driveSyntheticChild({ ...context, stateRoot: campaign.config.paths.stateRoot, startedAt });
      childCompleted = head.state === "completed";
    },
    maxControllerTransitions: 1, clock: () => new Date(startedAt), suffix,
  });
  const goalAfter = await recoverGoalLedger({ stateRoot: campaign.config.paths.stateRoot, goal: campaign.goal, jobs: campaign.jobs });
  const requestedFinish = observedDate(dependencies.finishedAt ?? new Date(), "soak cycle finish time");
  const finishedAt = new Date(Math.max(requestedFinish.getTime(), startedAt.getTime(), Date.parse(goalAfter.head.createdAt)));
  const record = {
    $schema: "./schemas/deep-work-multi-day-soak-invocation-v1.schema.json", schemaVersion: 1,
    operation: "pixel-deep-work-multi-day-soak-cycle", campaignId: campaign.config.campaignId,
    sequence: invocationLedger.records.length, startedAt: startedAt.toISOString(), finishedAt: finishedAt.toISOString(),
    previousInvocationSha256: invocationLedger.headSha256, ...identity,
    source: { commit: campaign.config.source.commit, tree: campaign.config.source.tree, programSha256: campaign.config.source.programSha256, runtimeSnapshotSha256: campaign.config.source.runtimeSnapshotSha256, configSha256: campaign.configSha256 },
    before: projection(goalBefore), after: projection(goalAfter),
    cycle: { action: result.action, childState: childCompleted ? "completed" : null, replaysChild: false, grantsExecution: false },
    privacy: { ...privacy }, boundary: invocationBoundary,
  };
  const appended = await appendInvocation(campaign, record);
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak-cycle", status: goalAfter.head.state,
    campaignId: campaign.config.campaignId, sequence: record.sequence, invocationSha256: appended.sha256,
    milestonesCompleted: goalAfter.head.progress.milestonesCompleted, milestonesTotal: schedule.milestones,
    action: result.action, nextEligibleAt: record.sequence + 1 < schedule.expectedCycles ? new Date(startedAt.getTime() + schedule.minimumGapSeconds * 1000).toISOString() : null,
    providerCalls: 0, credentialInputs: 0, externalEffects: 0,
    boundary: "Content-free qualification cycle receipt only. It grants no schedule, worker, provider, deployment, or release authority.",
  });
}

async function inspectFinalState(campaign) {
  const goalLedger = await recoverGoalLedger({ stateRoot: campaign.config.paths.stateRoot, goal: campaign.goal, jobs: campaign.jobs });
  let childCheckpointRecords = 0, runBundleRecords = 0, refreshes = 0;
  for (const job of campaign.jobs) {
    const bundles = await recoverGoalRunBundles({ stateRoot: campaign.config.paths.stateRoot, goal: campaign.goal, jobs: campaign.jobs, jobId: job.jobId });
    runBundleRecords += bundles.bundles.length;
    refreshes += bundles.bundles.filter((bundle) => bundle.purpose === "pre-admission-refresh").length;
    if (canonical(bundles.bundles.map((bundle) => bundle.purpose)) !== canonical(["initial", "pre-admission-refresh", "admission"])) fail("soak child lease custody differs from initial, delayed refresh, and exact admission");
    const child = await recoverCheckpointLedger({ stateRoot: campaign.config.paths.stateRoot, plan: bundles.head.plan, lease: bundles.head.lease });
    if (child.head.state !== "completed" || child.checkpoints.length !== 5 || child.head.usage.modelRequests !== 1) fail("soak child checkpoint accounting is invalid");
    childCheckpointRecords += child.checkpoints.length;
  }
  return { goalLedger, childCheckpointRecords, runBundleRecords, refreshes };
}

export function validateMultiDaySoakEvidence(evidence) {
  const errors = [];
  const gapCount = evidence?.observed?.completedCycles - 1;
  if (Date.parse(evidence?.finishedAt) < Date.parse(evidence?.startedAt)) errors.push("$.finishedAt: soak evidence ends before it starts");
  if (evidence?.observed?.uniqueInvocationIds !== evidence?.observed?.completedCycles) errors.push("$.observed.uniqueInvocationIds: every cycle requires a unique systemd invocation");
  if (evidence?.observed?.bootSessions < 2) errors.push("$.observed.bootSessions: controlled reboot was not observed");
  if (evidence?.observed?.minimumGapSeconds < schedule.minimumGapSeconds || evidence?.observed?.maximumGapSeconds > schedule.maximumGapSeconds) errors.push("$.observed: invocation cadence is outside the qualification window");
  if (evidence?.observed?.elapsedSeconds < schedule.minimumElapsedSeconds || gapCount !== 48) errors.push("$.observed.elapsedSeconds: campaign did not span at least 48 real hours across 49 cycles");
  if (evidence?.observed?.preAdmissionLeaseRefreshes !== schedule.milestones) errors.push("$.observed.preAdmissionLeaseRefreshes: every delayed child must receive one exact refresh");
  if (
    evidence?.result?.goalState !== "completed" || evidence?.result?.milestonesCompleted !== schedule.milestones
    || evidence?.result?.jobsStarted !== schedule.milestones || evidence?.result?.modelRequests !== schedule.milestones
    || evidence?.result?.parentCheckpointRecords !== schedule.milestones * 2 + 2
    || evidence?.result?.childCheckpointRecords !== schedule.milestones * 5
    || evidence?.result?.runBundleRecords !== schedule.milestones * 3
  ) errors.push("$.result: final durable accounting differs from the exact campaign");
  return errors;
}

export async function campaignStatus(configPath) {
  const campaign = await loadCampaign(configPath);
  const invocations = await recoverInvocationLedger(campaign);
  const goal = await recoverGoalLedger({ stateRoot: campaign.config.paths.stateRoot, goal: campaign.goal, jobs: campaign.jobs });
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak-status", campaignId: campaign.config.campaignId,
    state: goal.head.state, completedCycles: invocations.records.length, expectedCycles: schedule.expectedCycles,
    milestonesCompleted: goal.head.progress.milestonesCompleted, milestonesTotal: schedule.milestones,
    bootSessionsObserved: new Set(invocations.records.map((record) => record.bootIdSha256)).size,
    startedAt: invocations.records[0]?.startedAt ?? null, lastCycleAt: invocations.head?.startedAt ?? null,
    nextEligibleAt: invocations.head && invocations.records.length < schedule.expectedCycles ? new Date(Date.parse(invocations.head.startedAt) + schedule.minimumGapSeconds * 1000).toISOString() : null,
    readyToFinalize: goal.head.state === "completed" && invocations.records.length === schedule.expectedCycles,
    providerCalls: 0, credentialInputs: 0, externalEffects: 0,
    boundary: "Content-free read-only soak status. It grants no cycle, worker, provider, deployment, or release authority.",
  });
}

export async function finalizeCampaign(configPath, output) {
  const campaign = await loadCampaign(configPath);
  const invocations = await recoverInvocationLedger(campaign);
  if (invocations.records.length !== schedule.expectedCycles) fail("soak campaign has not completed its exact invocation sequence");
  const final = await inspectFinalState(campaign);
  if (final.goalLedger.head.state !== "completed") fail("soak campaign goal is not completed");
  const starts = invocations.records.map((record) => Date.parse(record.startedAt));
  const gaps = starts.slice(1).map((value, index) => Math.floor((value - starts[index]) / 1000));
  const elapsedSeconds = Math.floor((starts.at(-1) - starts[0]) / 1000);
  const evidence = {
    $schema: "./schemas/deep-work-multi-day-soak-evidence-v1.schema.json", schemaVersion: 1,
    operation: "pixel-deep-work-multi-day-soak-evidence", status: "pass", campaignId: campaign.config.campaignId,
    source: { commit: campaign.config.source.commit, tree: campaign.config.source.tree, programSha256: campaign.config.source.programSha256, runtimeSnapshotSha256: campaign.config.source.runtimeSnapshotSha256, configSha256: campaign.configSha256 },
    startedAt: invocations.records[0].startedAt, finishedAt: invocations.records.at(-1).finishedAt,
    schedule: { ...schedule },
    observed: {
      completedCycles: invocations.records.length,
      uniqueInvocationIds: new Set(invocations.records.map((record) => record.invocationIdSha256)).size,
      bootSessions: new Set(invocations.records.map((record) => record.bootIdSha256)).size,
      minimumGapSeconds: Math.min(...gaps), maximumGapSeconds: Math.max(...gaps), elapsedSeconds,
      preAdmissionLeaseRefreshes: final.refreshes,
    },
    result: {
      goalState: final.goalLedger.head.state, milestonesCompleted: final.goalLedger.head.progress.milestonesCompleted,
      jobsStarted: final.goalLedger.head.progress.jobsStarted, modelRequests: final.goalLedger.head.usage.modelRequests,
      parentCheckpointRecords: final.goalLedger.checkpoints.length, childCheckpointRecords: final.childCheckpointRecords,
      runBundleRecords: final.runBundleRecords, duplicateMilestoneCredit: false, duplicateUsageCredit: false,
      replayedChildExecution: false, goalCheckpointSha256: final.goalLedger.headSha256,
      invocationLedgerSha256: sha(invocations.records),
    },
    privacy: { ...privacy }, boundary: evidenceBoundary,
  };
  const schemaErrors = validateJsonSchema(evidence, await installedSchema(evidenceSchemaUrl));
  const semanticErrors = validateMultiDaySoakEvidence(evidence);
  if (schemaErrors.length || semanticErrors.length) fail(`soak evidence is invalid: ${schemaErrors[0] ?? semanticErrors[0]}`);
  const destination = resolve(output);
  if (destination !== output || within(destination, sourceRoot) || await lstat(destination).then(() => true, () => false)) fail("soak evidence output must be a new absolute file outside the source tree");
  await privateDirectory(dirname(destination), "soak evidence output parent");
  await writePrivate(destination, evidence); await syncDirectory(dirname(destination));
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak-finalize", status: "pass",
    campaignId: campaign.config.campaignId, completedCycles: schedule.expectedCycles, elapsedSeconds,
    bootSessions: evidence.observed.bootSessions, evidenceSha256: sha(await readFile(destination)),
    providerCalls: 0, credentialInputs: 0, externalEffects: 0,
    boundary: "Content-free qualification receipt only. It grants no provider, deployment, publication, or release authority.",
  });
}

export async function main(argv = process.argv.slice(2)) {
  const [command, ...rest] = argv;
  if (command === "initialize") return initializeCampaign(parseInitialize(rest));
  if (command === "cycle") { const options = parseCycle(rest); return runCampaignCycle(options.configPath, { expectedConfigSha256: options.configSha256 }); }
  if (command === "status") return campaignStatus(parseConfigOnly(rest, "status"));
  if (command === "finalize") { const options = parseFinalize(rest); return finalizeCampaign(options.configPath, options.output); }
  fail("Usage: deep-work-multi-day-soak.mjs <initialize|cycle|status|finalize> ...");
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().then((value) => process.stdout.write(`${JSON.stringify(value)}\n`)).catch((error) => {
    process.stderr.write(`pixel-deep-work-multi-day-soak: ${error instanceof DeepWorkMultiDaySoakError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
