import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readFile, realpath } from "node:fs/promises";
import { dirname, join, posix, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { renderGoalServiceBundle } from "../deploy/work-controller/goal-service-cli.mjs";
import { runGoalPauseCommand } from "../deploy/work-controller/goal-pause-cli.mjs";
import { goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";
import { readWorkOperatorStatus } from "../deploy/work-controller/operator-status.mjs";
import {
  canonical, validateWorkGoal, validateWorkGoalController, validateWorkJob, validateWorkPolicy,
} from "./lib/work-contract.mjs";

const sourceRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const HASH_RE = /^[a-f0-9]{64}$/u;
const COMMIT_RE = /^[a-f0-9]{40}$/u;
const ACCOUNT_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const GOAL_RE = /^workgoal-[0-9]{13}-[a-f0-9]{12}$/u;
const boundary = "Credential-free supported-host supervisor qualification only. It proves exact systemd installation, event wakeup, terminal convergence, and removal for a deliberately paused goal; it grants no worker execution, lease, model, provider, external-effect, deployment, or semantic capability authority.";

export class DeepWorkServiceQualificationError extends Error {}

function fail(message) { throw new DeepWorkServiceQualificationError(message); }
function sha(value) { return createHash("sha256").update(Buffer.isBuffer(value) || typeof value === "string" ? value : canonical(value)).digest("hex"); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

function parse(argv) {
  if (!Array.isArray(argv) || !["prepare", "inspect"].includes(argv[0]) || (argv.length - 1) % 2 !== 0) fail("Usage: deep-work-service-qualification.mjs prepare --root DIR --install-root DIR --service-user USER --service-group GROUP --docker-group GROUP --source-commit COMMIT --source-tree TREE | inspect --root DIR --expected-state paused|cancelled");
  const operation = argv[0], allowed = operation === "prepare"
    ? new Set(["--root", "--install-root", "--service-user", "--service-group", "--docker-group", "--source-commit", "--source-tree"])
    : new Set(["--root", "--expected-state"]);
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("supervisor qualification arguments are invalid, unknown, or duplicated");
    values[key] = value;
  }
  if (Object.keys(values).length !== allowed.size || [...allowed].some((key) => !values[key])) fail("supervisor qualification arguments are incomplete");
  if (operation === "inspect" && !["paused", "cancelled"].includes(values["--expected-state"])) fail("supervisor qualification expected state is invalid");
  return { operation, values };
}

async function privateDirectory(path, label, create = false) {
  if (resolve(path) !== path || path === "/") fail(`${label} path is invalid`);
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return path;
}

async function immutableInstallRoot(path) {
  if (resolve(path) !== path || path !== sourceRoot || await realpath(path) !== path || !path.startsWith("/opt/")) fail("supervisor qualification install root is not the running immutable source");
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== 0 || (info.mode & 0o022) !== 0)) fail("supervisor qualification install root is not root-held and immutable");
}

async function executableSha256(path, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size < 1 || info.size > 1024 * 1024 * 1024 || process.platform !== "win32" && ((info.mode & 0o111) === 0 || (info.mode & 0o022) !== 0)) fail(`${label} is not a bounded immutable executable`);
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== info.dev || opened.ino !== info.ino || opened.size !== info.size) fail(`${label} changed during validation`);
    const digest = createHash("sha256"), buffer = Buffer.allocUnsafe(1024 * 1024);
    for (;;) {
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (!bytesRead) break;
      digest.update(buffer.subarray(0, bytesRead));
    }
    return digest.digest("hex");
  } finally { await handle.close(); }
}

async function writePrivate(path, value) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
}

export function buildQualificationContracts({ root, now, suffix, executorSha256, uid, gid, policyTemplate } = {}) {
  if (
    typeof root !== "string" || !root.startsWith("/var/lib/") || posix.normalize(root) !== root
    || !(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || !/^[a-f0-9]{12}$/u.test(suffix ?? "")
    || !HASH_RE.test(executorSha256 ?? "") || !Number.isSafeInteger(uid) || uid < 1 || !Number.isSafeInteger(gid) || gid < 1
    || !policyTemplate || typeof policyTemplate !== "object" || Array.isArray(policyTemplate)
  ) fail("supervisor qualification contract input is invalid");
  const epoch = String(now.getTime()).padStart(13, "0"), jobId = `work-${epoch}-${suffix}`;
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt: now.toISOString(), requester: "pixel", profile: "scout",
    objective: "Qualify the dormant event-driven supervisor boundary without launching a worker.",
    acceptanceCriteria: ["The exact paused goal is installed, event-observed, and removed without worker execution"],
    dataClassification: "internal", inputs: [{
      id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: "a".repeat(64),
      maxBytes: 1024, classification: "internal",
    }],
    requestedCapabilities: {
      filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: {
      maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 10, maxConcurrentSubagents: 1,
      maxModelRequests: 2, maxInputTokens: 1000, maxOutputTokens: 500, maxCpuCores: 1, maxMemoryMiB: 1536,
      maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1,
    },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: `workgoal-${String(now.getTime() + 1).padStart(13, "0")}-${suffix}`,
    createdAt: new Date(now.getTime() + 1).toISOString(), requester: "pixel",
    objective: "Qualify exact event-driven supervisor mechanics on a disposable supported host.", dataClassification: "internal",
    milestones: [{ milestoneId: "supervisor-boundary", jobId, jobSha256: goalSha256(job), profile: "scout", dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: 60, maxModelRequests: 2, maxInputTokens: 1000, maxOutputTokens: 500, maxNetworkBytes: 1048576, maxArtifactBytes: 65536, maxFailures: 1 },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  const privateRoot = posix.join(root, "private"), stateRoot = posix.join(root, "state"), objectStore = posix.join(root, "objects"), workspaceRoot = posix.join(root, "workspaces");
  const policy = structuredClone(policyTemplate);
  policy.enabled = true;
  policy.executor.sha256 = executorSha256;
  policy.runner.prepared = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  policy.runner.imageRef = `local/pixel-supervisor-qualification@${policy.runner.imageDigest}`;
  policy.localModel.prepared = true;
  policy.profiles.scout.enabled = true;
  policy.localModel.qualification.receiptPath = posix.join(privateRoot, "model-qualification.json");
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-v1.schema.json", schemaVersion: 1, enabled: true,
    stateRoot, goalPath: posix.join(privateRoot, "goal.json"), jobsPath: posix.join(privateRoot, "jobs.json"),
    policyPath: posix.join(privateRoot, "policy.json"), objectStore, workspaceRoot, executorPath: "/usr/bin/true",
    archiveLimits: { maxEntries: 100, maxFileBytes: 1048576 },
    runtime: {
      dockerPath: "/usr/bin/docker", backendNetworkName: "pixel-qualification-model", backendContainerName: "pixel-qualification-model",
      networkSubnet: "172.29.253.0/29", workerIp: "172.29.253.2", proxyIp: "172.29.253.3", uid, gid,
    },
    controller: { maxTransitions: 1 },
  };
  const errors = [...validateWorkJob(job), ...validateWorkGoal(goal), ...validateWorkPolicy(policy), ...validateWorkGoalController(config)];
  if (errors.length) fail(`supervisor qualification contracts are invalid: ${errors[0]}`);
  return Object.freeze({ job, goal, policy, config });
}

async function prepare(values) {
  if (process.platform !== "linux" || process.geteuid?.() === 0 || !Number.isSafeInteger(process.geteuid?.())) fail("supervisor qualification preparation requires a non-root Linux service identity");
  const root = resolve(values["--root"]), installRoot = resolve(values["--install-root"]);
  const serviceUser = values["--service-user"], serviceGroup = values["--service-group"], dockerGroup = values["--docker-group"];
  if (![serviceUser, serviceGroup, dockerGroup].every((value) => ACCOUNT_RE.test(value)) || serviceUser === "root" || serviceGroup === "root" || dockerGroup === "root") fail("supervisor qualification service identities are invalid");
  if (process.env.USER !== serviceUser || process.env.LOGNAME !== serviceUser) fail("supervisor qualification process identity differs from the declared service user");
  if (!COMMIT_RE.test(values["--source-commit"]) || !COMMIT_RE.test(values["--source-tree"])) fail("supervisor qualification source identity is invalid");
  await privateDirectory(root, "supervisor qualification root");
  await immutableInstallRoot(installRoot);
  const dockerSocket = await lstat("/run/docker.sock").catch(() => null);
  if (!dockerSocket?.isSocket() || dockerSocket.isSymbolicLink()) fail("supervisor qualification Docker socket is unavailable");
  const [executorSha256] = await Promise.all([
    executableSha256("/usr/bin/true", "supervisor qualification inert executor"),
    executableSha256("/usr/bin/docker", "supervisor qualification Docker client"),
    executableSha256("/usr/bin/flock", "supervisor qualification lock client"),
    executableSha256(process.execPath, "supervisor qualification Node runtime"),
  ]);
  const directories = ["private", "state", "objects", "workspaces", "services"];
  for (const name of directories) await privateDirectory(join(root, name), `supervisor qualification ${name}`, true);
  const now = new Date(), suffix = randomBytes(6).toString("hex");
  const policyTemplate = JSON.parse(await readFile(join(installRoot, "deploy/work-broker/policy.example.json"), "utf8"));
  const contracts = buildQualificationContracts({ root, now, suffix, executorSha256, uid: process.geteuid(), gid: process.getgid(), policyTemplate });
  await Promise.all([
    writePrivate(contracts.config.goalPath, contracts.goal), writePrivate(contracts.config.jobsPath, [contracts.job]),
    writePrivate(contracts.config.policyPath, contracts.policy), writePrivate(join(root, "private/controller.json"), contracts.config),
  ]);
  await initializeGoalLedger({ stateRoot: contracts.config.stateRoot, goal: contracts.goal, jobs: [contracts.job], now: new Date(now.getTime() + 2), suffix: randomBytes(6).toString("hex") });
  const paused = await runGoalPauseCommand(["--config", join(root, "private/controller.json")], { now: new Date(now.getTime() + 3), suffix: randomBytes(6).toString("hex") });
  if (paused.status !== "paused" || paused.progress.jobsStarted !== 0) fail("supervisor qualification goal did not enter its safe paused state");
  const bundlePath = join(root, "services/supervisor");
  const rendered = await renderGoalServiceBundle([
    "render", "--config", join(root, "private/controller.json"), "--output", bundlePath, "--install-root", installRoot,
    "--confirm-config-sha256", sha(contracts.config), "--confirm-goal-sha256", goalSha256(contracts.goal),
    "--node", process.execPath, "--flock", "/usr/bin/flock", "--user", serviceUser, "--group", serviceGroup,
    "--docker-group", dockerGroup, "--interval", "3600",
  ]);
  const qualification = {
    schemaVersion: 1, sourceCommit: values["--source-commit"], sourceTree: values["--source-tree"],
    installRoot, goalId: contracts.goal.goalId, configPath: join(root, "private/controller.json"), bundlePath, manifestSha256: rendered.manifestSha256,
    serviceName: rendered.serviceName, timerName: rendered.timerName, pathName: rendered.pathName,
  };
  await writePrivate(join(root, "qualification.json"), qualification);
  process.stdout.write(`${JSON.stringify({ status: "prepared-paused", executionModel: "durable-event-driven", watchdogRole: "liveness-only", jobsStarted: 0, authority: { grantsExecution: false, grantsLease: false, grantsExternalEffects: false }, boundary })}\n`);
}

async function inspect(values) {
  const root = await privateDirectory(resolve(values["--root"]), "supervisor qualification root");
  let qualification;
  try { qualification = JSON.parse(await readFile(join(root, "qualification.json"), "utf8")); } catch { fail("supervisor qualification private receipt is unavailable"); }
  exactKeys(qualification, [
    "schemaVersion", "sourceCommit", "sourceTree", "installRoot", "goalId", "configPath", "bundlePath",
    "manifestSha256", "serviceName", "timerName", "pathName",
  ], "supervisor qualification private receipt");
  if (
    qualification.schemaVersion !== 1 || !COMMIT_RE.test(qualification.sourceCommit ?? "") || !COMMIT_RE.test(qualification.sourceTree ?? "")
    || !HASH_RE.test(qualification.manifestSha256 ?? "") || !GOAL_RE.test(qualification.goalId ?? "")
    || qualification.installRoot !== sourceRoot || qualification.configPath !== join(root, "private/controller.json")
    || qualification.bundlePath !== join(root, "services/supervisor")
    || qualification.serviceName !== `pixel-work-${qualification.goalId}.service`
    || qualification.timerName !== `pixel-work-${qualification.goalId}.timer`
    || qualification.pathName !== `pixel-work-${qualification.goalId}.path`
  ) fail("supervisor qualification private receipt is invalid");
  await immutableInstallRoot(qualification.installRoot);
  const config = JSON.parse(await readFile(qualification.configPath, "utf8"));
  const goal = JSON.parse(await readFile(config.goalPath, "utf8")), jobs = JSON.parse(await readFile(config.jobsPath, "utf8"));
  const [ledger, operator] = await Promise.all([recoverGoalLedger({ stateRoot: config.stateRoot, goal, jobs }), readWorkOperatorStatus({ stateRoot: config.stateRoot })]);
  const expected = values["--expected-state"];
  if (
    ledger.head.state !== expected || operator.goal.state !== expected || ledger.head.progress.jobsStarted !== 0
    || ledger.head.progress.milestonesCompleted !== 0 || ledger.head.active !== null || ledger.head.authorityExpansionObserved
    || ledger.head.externalEffectsObserved
  ) fail("supervisor qualification durable state differs from its non-executing boundary");
  process.stdout.write(`${JSON.stringify({ status: "pass", goalState: expected, checkpoints: ledger.checkpoints.length, jobsStarted: 0, milestonesCompleted: 0, authorityExpansionObserved: false, externalEffectsObserved: false, boundary })}\n`);
}

export async function runDeepWorkServiceQualification(argv) {
  const request = parse(argv);
  if (request.operation === "prepare") return prepare(request.values);
  return inspect(request.values);
}

export async function main(argv = process.argv.slice(2)) {
  await runDeepWorkServiceQualification(argv);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    const expected = error instanceof DeepWorkServiceQualificationError;
    process.stderr.write(`pixel-deep-work-service-qualification: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const deepWorkServiceQualificationBoundary = boundary;
