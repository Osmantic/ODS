import { createHash, randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { constants } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, open, readFile, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { performance } from "node:perf_hooks";

import { compileScout } from "../deploy/work-broker/broker.mjs";
import { appendCheckpoint, checkpointSha256, recoverCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import { goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";
import { runGoalCycle } from "../deploy/work-controller/goal-runtime.mjs";
import { validateJsonSchema } from "./lib/json-schema.mjs";

const sourceRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const scriptPath = fileURLToPath(import.meta.url);
const schemaUrl = new URL("../schemas/deep-work-endurance-evidence-v1.schema.json", import.meta.url);
const COMMIT_RE = /^[a-f0-9]{40}$/u;
const MAX_FIXTURE_BYTES = 16 * 1024 * 1024;
const boundary = "Credential-free disposable event-horizon qualification evidence only. Dependency depth, durable transitions, real process termination, and restart recovery grant no work, lease, credential, provider, network, external-effect, deployment, or release authority.";

export class DeepWorkEnduranceError extends Error {}

function fail(message) { throw new DeepWorkEnduranceError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : JSON.stringify(value)).digest("hex"); }
function suffix() { return randomBytes(6).toString("hex"); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) fail(`${label} shape is invalid`);
}
function integer(value, minimum, maximum, label) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum || String(parsed) !== String(value)) fail(`${label} is invalid`);
  return parsed;
}
function sleep(milliseconds) { return new Promise((accept) => setTimeout(accept, milliseconds)); }

async function privateDirectory(path, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  return path;
}

function parseProbeArguments(argv) {
  if (!Array.isArray(argv) || argv.length !== 10) fail("Usage: deep-work-endurance-probe.mjs --output ABSOLUTE_NEW_FILE --source-commit COMMIT --source-tree TREE --milestones 4..64 --restart-delay-ms 10..1000");
  const values = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!["--output", "--source-commit", "--source-tree", "--milestones", "--restart-delay-ms"].includes(key) || !value || Object.hasOwn(values, key)) fail("endurance probe arguments are invalid");
    values[key] = value;
  }
  const output = resolve(values["--output"]);
  if (values["--output"] !== output || output === sourceRoot || output.startsWith(`${sourceRoot}${sep}`)) fail("endurance evidence must be an absolute path outside the source tree");
  if (!COMMIT_RE.test(values["--source-commit"] ?? "") || !COMMIT_RE.test(values["--source-tree"] ?? "")) fail("endurance source identity is invalid");
  return {
    output,
    sourceCommit: values["--source-commit"], sourceTree: values["--source-tree"],
    milestones: integer(values["--milestones"], 4, 64, "endurance milestone count"),
    restartDelayMs: integer(values["--restart-delay-ms"], 10, 1000, "endurance restart delay"),
  };
}

function budgets() {
  return {
    maxRuntimeSeconds: 3600, maxIterations: 2, maxToolCalls: 20, maxConcurrentSubagents: 1,
    maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1,
    maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576,
    maxFailures: 1, noProgressLimit: 1,
  };
}

function scoutJob(jobId, createdAt, contentSha256, bytes, index) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt, requester: "pixel", profile: "scout", objective: `Qualify durable milestone ${index + 1}.`,
    acceptanceCriteria: ["A bounded independently verified qualification record exists"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: bytes, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: budgets(), outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

function milestoneId(index) { return `milestone-${String(index + 1).padStart(2, "0")}`; }

export function buildEnduranceGraph(milestones) {
  if (!Number.isSafeInteger(milestones) || milestones < 4 || milestones > 64) fail("endurance graph milestone count is invalid");
  const dependencies = Array.from({ length: milestones }, (_, index) => {
    if (index === 0) return [];
    const position = (index - 1) % 3;
    const anchor = Math.floor((index - 1) / 3) * 3;
    return position < 2 ? [milestoneId(anchor)] : [milestoneId(index - 2), milestoneId(index - 1)];
  });
  const outgoing = Array.from({ length: milestones }, () => 0);
  const depth = Array.from({ length: milestones }, () => 0);
  for (let index = 0; index < dependencies.length; index += 1) {
    for (const dependency of dependencies[index]) {
      const dependencyIndex = Number(dependency.slice(-2)) - 1;
      outgoing[dependencyIndex] += 1;
      depth[index] = Math.max(depth[index], depth[dependencyIndex] + 1);
    }
  }
  return {
    dependencies,
    metrics: {
      kind: "repeated-diamond", rootMilestones: dependencies.filter((entry) => entry.length === 0).length,
      dependencyEdges: dependencies.reduce((total, entry) => total + entry.length, 0),
      branchPoints: outgoing.filter((count) => count > 1).length,
      convergenceMilestones: dependencies.filter((entry) => entry.length > 1).length,
      maximumDepth: Math.max(...depth), maxDependenciesPerMilestone: Math.max(...dependencies.map((entry) => entry.length)),
    },
  };
}

async function buildFixture(root, milestones) {
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const started = Date.now(), createdAt = new Date(started).toISOString();
  const content = Buffer.from("pixel deep work endurance fixture\n", "utf8"), contentSha256 = sha(content);
  const epoch = String(started).padStart(13, "0");
  const jobs = Array.from({ length: milestones }, (_, index) => scoutJob(
    `work-${epoch}-${(0x100000000000n + BigInt(index)).toString(16)}`, createdAt, contentSha256, content.length, index,
  ));
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true;
  const entries = [{
    id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256,
    bytes: content.length, classification: "internal", mountMode: "read-only",
  }];
  const runs = jobs.map((job, index) => {
    const compiled = compileScout(job, policy, entries, {
      now: new Date(started), suffix: (0x200000000000n + BigInt(index)).toString(16),
    });
    return { jobId: job.jobId, plan: compiled.plan, lease: compiled.lease, workspaceSnapshotSha256: sha(`workspace:${job.jobId}`) };
  });
  const graph = buildEnduranceGraph(milestones);
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: `workgoal-${epoch}-300000000000`, createdAt, requester: "pixel",
    objective: `Qualify ${milestones} durable credential-free milestones across real process loss.`, dataClassification: "internal",
    milestones: jobs.map((job, index) => ({
      milestoneId: milestoneId(index), jobId: job.jobId, jobSha256: goalSha256(job), profile: "scout",
      dependsOn: graph.dependencies[index],
    })),
    budgets: {
      maxJobs: milestones, maxRuntimeSeconds: 3600 * milestones, maxModelRequests: 5 * milestones,
      maxInputTokens: 10000 * milestones, maxOutputTokens: 2000 * milestones,
      maxNetworkBytes: 1048576 * milestones, maxArtifactBytes: 65536 * milestones, maxFailures: milestones,
    },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  await initializeGoalLedger({ stateRoot, goal, jobs, now: new Date(started + 1), suffix: "000000000001" });
  return { schemaVersion: 1, operation: "pixel-deep-work-endurance-fixture", stateRoot, goal, jobs, runs };
}

async function readFixture(path) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > MAX_FIXTURE_BYTES) fail("endurance worker fixture is not a bounded regular file");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("endurance worker fixture is not owner-private");
  let value;
  try { value = JSON.parse(await readFile(path, "utf8")); } catch { fail("endurance worker fixture is not JSON"); }
  exactKeys(value, ["schemaVersion", "operation", "stateRoot", "goal", "jobs", "runs"], "endurance worker fixture");
  if (value.schemaVersion !== 1 || value.operation !== "pixel-deep-work-endurance-fixture" || resolve(value.stateRoot) !== value.stateRoot) fail("endurance worker fixture binding is invalid");
  if (!Array.isArray(value.jobs) || !Array.isArray(value.runs) || value.jobs.length !== value.runs.length) fail("endurance worker fixture registry is invalid");
  const runs = new Map();
  for (const run of value.runs) {
    exactKeys(run, ["jobId", "plan", "lease", "workspaceSnapshotSha256"], "endurance child run");
    if (runs.has(run.jobId)) fail("endurance child run is duplicated");
    runs.set(run.jobId, { plan: run.plan, lease: run.lease, workspaceSnapshotSha256: run.workspaceSnapshotSha256 });
  }
  return { ...value, runs };
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

async function workerMain(fixturePath) {
  if (typeof process.send !== "function") fail("endurance worker requires a private parent IPC channel");
  const value = await readFixture(resolve(fixturePath));
  const driveChild = async ({ job, plan, lease, childCheckpoint }) => {
    let state, overrides;
    if (childCheckpoint.state === "authorized") {
      state = "running";
      overrides = { iteration: 1, workerSessionSha256: sha(`session:${job.jobId}`) };
    } else if (childCheckpoint.state === "running") {
      state = "verifying";
      overrides = {
        usage: { runtimeSeconds: 1, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 0, artifactBytes: 20 },
        artifactManifestSha256: sha(`artifact:${job.jobId}`),
      };
    } else if (childCheckpoint.state === "verifying") {
      state = "verified";
      overrides = { progress: { criteriaPassing: 1, criteriaFailing: 0 }, verificationEvidenceSha256: sha(`verification:${job.jobId}`) };
    } else if (childCheckpoint.state === "verified") {
      state = "completed";
      overrides = {};
    } else {
      fail("endurance worker reached an unsupported child state");
    }
    const record = await appendCheckpoint({
      stateRoot: value.stateRoot, plan, lease, previousCheckpointSha256: checkpointSha256(childCheckpoint),
      update: checkpointUpdate(childCheckpoint, state, overrides),
      now: new Date(Math.max(Date.now(), Date.parse(childCheckpoint.createdAt) + 1)), suffix: suffix(),
    });
    await new Promise((accept, reject) => process.send({ event: "durable-transition", state, checkpointSha256: record.sha256 }, (error) => error ? reject(error) : accept()));
    setInterval(() => {}, 60000);
    await new Promise(() => {});
  };
  const result = await runGoalCycle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs,
    resolveChildRun: async ({ job }) => value.runs.get(job.jobId), driveChild,
    maxControllerTransitions: 1,
  });
  await new Promise((accept, reject) => process.send({ event: "cycle-result", result }, (error) => error ? reject(error) : accept()));
  process.disconnect();
}

function minimalWorkerEnvironment() {
  return {
    LANG: "C.UTF-8", LC_ALL: "C.UTF-8",
    ...(process.platform === "win32" && process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {}),
  };
}

async function launchWorker(fixturePath) {
  const child = spawn(process.execPath, [scriptPath, "--worker", fixturePath], {
    cwd: sourceRoot, env: minimalWorkerEnvironment(), stdio: ["ignore", "ignore", "pipe", "ipc"], windowsHide: true,
  });
  let durable = null, result = null, workerError = null, stderr = "", killed = false, settled = false;
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => { if (stderr.length < 65536) stderr += chunk; });
  const timer = setTimeout(() => { if (!settled) child.kill("SIGKILL"); }, 15000);
  child.on("message", (message) => {
    if (message?.event === "durable-transition" && durable === null) {
      durable = message;
      killed = child.kill("SIGKILL");
    } else if (message?.event === "cycle-result" && result === null) {
      result = message.result;
    } else if (message?.event === "worker-error" && workerError === null) {
      workerError = message.error;
    }
  });
  const exit = await new Promise((accept, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => accept({ code, signal }));
  }).finally(() => { settled = true; clearTimeout(timer); });
  if (durable) {
    if (!killed || exit.code === 0 || result !== null || !["running", "verifying", "verified", "completed"].includes(durable.state)) fail("endurance worker was not abruptly terminated after its durable transition");
    return { kind: "abrupt", state: durable.state, pid: child.pid };
  }
  if (exit.code !== 0 || exit.signal !== null || !result || stderr || workerError) fail(`endurance worker cycle failed${workerError ? `: ${workerError}` : stderr ? `: ${stderr.trim().slice(0, 200)}` : ""}`);
  return { kind: "graceful", result, pid: child.pid };
}

async function validateCampaignState(fixture) {
  const goal = await recoverGoalLedger({ stateRoot: fixture.stateRoot, goal: fixture.goal, jobs: fixture.jobs });
  if (goal.head.progress.jobsStarted > fixture.jobs.length || goal.head.progress.milestonesCompleted > goal.head.progress.jobsStarted || new Set(goal.head.completedMilestones).size !== goal.head.completedMilestones.length) fail("endurance goal progress duplicated or moved beyond its immutable graph");
  let childCheckpointRecords = 0;
  for (const job of fixture.jobs.slice(0, goal.head.progress.jobsStarted)) {
    const run = fixture.runs.find((candidate) => candidate.jobId === job.jobId);
    const ledgerPath = join(fixture.stateRoot, "checkpoints", job.jobId);
    if (!await lstat(ledgerPath).then((info) => info.isDirectory() && !info.isSymbolicLink(), () => false)) continue;
    const child = await recoverCheckpointLedger({ stateRoot: fixture.stateRoot, plan: run.plan, lease: run.lease });
    childCheckpointRecords += child.checkpoints.length;
    if (child.checkpoints.length > 5) fail("endurance child was replayed after a durable transition");
  }
  return { goal, childCheckpointRecords };
}

async function writeEvidence(path, evidence) {
  const schema = JSON.parse(await readFile(schemaUrl, "utf8"));
  const errors = validateJsonSchema(evidence, schema);
  if (errors.length) fail(`endurance evidence is invalid: ${errors[0]}`);
  const semanticErrors = validateEnduranceEvidence(evidence);
  if (semanticErrors.length) fail(`endurance evidence is incoherent: ${semanticErrors[0]}`);
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(evidence, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
  if (process.platform !== "win32") {
    const directory = await open(dirname(path), constants.O_RDONLY);
    try { await directory.sync(); } finally { await directory.close(); }
  }
  return sha(await readFile(path));
}

export function validateEnduranceEvidence(evidence) {
  const errors = [];
  const milestones = evidence?.campaign?.milestones;
  const processes = evidence?.campaign?.processesLaunched;
  const crashes = evidence?.campaign?.forcedAbruptExits;
  const delay = evidence?.campaign?.requestedRestartDelayMs;
  let expectedGraph = null;
  try { expectedGraph = buildEnduranceGraph(milestones).metrics; } catch { errors.push("$.campaign.graph: milestone count cannot form the bounded event graph"); }
  if (Date.parse(evidence?.finishedAt) < Date.parse(evidence?.startedAt)) errors.push("$.finishedAt: endurance evidence ends before it starts");
  if (evidence?.campaign?.freshProcessRecoveries !== crashes) errors.push("$.campaign.freshProcessRecoveries: every abrupt exit requires one fresh-process recovery");
  if (crashes !== milestones * 4) errors.push("$.campaign.forcedAbruptExits: every milestone requires four post-commit crashes");
  if (processes !== milestones * 6 + 1 || evidence?.campaign?.gracefulCycles !== milestones * 2 + 1) errors.push("$.campaign.processesLaunched: process cadence differs from the bounded recovery sequence");
  if (Object.values(evidence?.campaign?.crashStates ?? {}).some((count) => count !== milestones)) errors.push("$.campaign.crashStates: every durable child state must be crash-qualified once per milestone");
  if (expectedGraph && JSON.stringify(evidence?.campaign?.graph) !== JSON.stringify(expectedGraph)) errors.push("$.campaign.graph: dependency-event coverage differs from the repeated-diamond campaign");
  if (
    evidence?.result?.goalState !== "completed" || evidence?.result?.milestonesCompleted !== milestones
    || evidence?.result?.jobsStarted !== milestones || evidence?.result?.modelRequests !== milestones
    || evidence?.result?.parentCheckpointRecords !== milestones * 2 + 2
    || evidence?.result?.childCheckpointRecords !== milestones * 5
  ) errors.push("$.result: final goal accounting differs from the exact endurance campaign");
  if (
    evidence?.campaign?.minimumObservedRestartDelayMs < Math.floor(delay * 0.75)
    || evidence?.campaign?.wallClockElapsedMs < Math.floor(delay * (processes - 1) * 0.75)
  ) errors.push("$.campaign.wallClockElapsedMs: measured cadence is shorter than the declared restart campaign");
  return errors;
}

export async function runProbe(argv) {
  const options = parseProbeArguments(argv);
  await privateDirectory(dirname(options.output), "endurance evidence directory");
  const [realSource, realParent] = await Promise.all([realpath(sourceRoot), realpath(dirname(options.output))]);
  if (resolve(realParent) !== resolve(dirname(options.output)) || realParent === realSource || realParent.startsWith(`${realSource}${sep}`)) fail("endurance evidence directory must not traverse a link into the source tree");
  if (await lstat(options.output).then(() => true, () => false)) fail("endurance evidence path already exists");
  const workRoot = await mkdtemp(join(tmpdir(), "pixel-deep-work-endurance-"));
  if (process.platform !== "win32") await chmod(workRoot, 0o700);
  const startedAt = new Date(), campaignStart = performance.now();
  try {
    const fixture = await buildFixture(workRoot, options.milestones);
    const fixturePath = join(workRoot, "fixture.json");
    const serialized = `${JSON.stringify(fixture)}\n`;
    if (Buffer.byteLength(serialized) > MAX_FIXTURE_BYTES) fail("endurance fixture exceeds its byte ceiling");
    const fixtureHandle = await open(fixturePath, "wx", 0o600);
    try { await fixtureHandle.writeFile(serialized, "utf8"); await fixtureHandle.sync(); } finally { await fixtureHandle.close(); }
    const crashStates = { running: 0, verifying: 0, verified: 0, completed: 0 };
    let processesLaunched = 0, forcedAbruptExits = 0, gracefulCycles = 0, previousExit = null;
    const restartDelays = [], pids = new Set();
    const maximumProcesses = options.milestones * 7 + 16;
    for (; processesLaunched < maximumProcesses;) {
      if (previousExit !== null) {
        await sleep(options.restartDelayMs);
        restartDelays.push(performance.now() - previousExit);
      }
      const outcome = await launchWorker(fixturePath);
      processesLaunched += 1;
      pids.add(outcome.pid);
      if (outcome.kind === "abrupt") {
        forcedAbruptExits += 1;
        crashStates[outcome.state] += 1;
      } else {
        gracefulCycles += 1;
      }
      previousExit = performance.now();
      const current = await validateCampaignState(fixture);
      if (current.goal.head.state === "completed") break;
    }
    const final = await validateCampaignState(fixture);
    const expectedCrashes = options.milestones * 4;
    const expectedParentRecords = options.milestones * 2 + 2;
    const expectedChildRecords = options.milestones * 5;
    if (
      final.goal.head.state !== "completed" || final.goal.head.progress.milestonesCompleted !== options.milestones
      || final.goal.head.progress.jobsStarted !== options.milestones || final.goal.head.usage.modelRequests !== options.milestones
      || final.goal.checkpoints.length !== expectedParentRecords || final.childCheckpointRecords !== expectedChildRecords
      || forcedAbruptExits !== expectedCrashes || Object.values(crashStates).some((count) => count !== options.milestones)
      || processesLaunched !== expectedCrashes + options.milestones * 2 + 1 || gracefulCycles !== options.milestones * 2 + 1
      || pids.size < 2 || restartDelays.length !== processesLaunched - 1
    ) fail("endurance campaign did not converge through the exact real-process recovery sequence");
    const elapsedMs = Math.floor(performance.now() - campaignStart);
    const minimumObservedRestartDelayMs = Math.floor(Math.min(...restartDelays));
    if (minimumObservedRestartDelayMs < Math.floor(options.restartDelayMs * 0.75) || elapsedMs < Math.floor(options.restartDelayMs * restartDelays.length * 0.75)) fail("endurance elapsed restart cadence was not observed");
    const evidence = {
      $schema: "./schemas/deep-work-endurance-evidence-v1.schema.json", schemaVersion: 1,
      operation: "pixel-deep-work-real-process-endurance", status: "pass",
      sourceCommit: options.sourceCommit, sourceTree: options.sourceTree,
      startedAt: startedAt.toISOString(), finishedAt: new Date().toISOString(),
      campaign: {
        milestones: options.milestones, requestedRestartDelayMs: options.restartDelayMs,
        graph: buildEnduranceGraph(options.milestones).metrics,
        processesLaunched, freshProcessRecoveries: forcedAbruptExits, forcedAbruptExits, gracefulCycles,
        crashStates, minimumObservedRestartDelayMs, wallClockElapsedMs: elapsedMs,
      },
      result: {
        goalState: final.goal.head.state, milestonesCompleted: final.goal.head.progress.milestonesCompleted,
        jobsStarted: final.goal.head.progress.jobsStarted, modelRequests: final.goal.head.usage.modelRequests,
        parentCheckpointRecords: final.goal.checkpoints.length, childCheckpointRecords: final.childCheckpointRecords,
        duplicateMilestoneCredit: false, duplicateUsageCredit: false, replayedChildExecution: false,
        goalCheckpointSha256: final.goal.headSha256,
      },
      privacy: { providerCalls: 0, credentialInputs: 0, networkRequests: 0, externalEffects: 0, productionDeploymentsTouched: 0 },
      boundary,
    };
    const evidenceSha256 = await writeEvidence(options.output, evidence);
    return {
      schemaVersion: 1, operation: "pixel-deep-work-endurance-probe", status: "pass",
      milestones: options.milestones, forcedAbruptExits, processesLaunched, wallClockElapsedMs: elapsedMs,
      evidenceSha256, providerCalls: 0, credentialInputs: 0, externalEffects: 0,
      boundary: "Content-free event-horizon qualification receipt only. It grants no work, provider, deployment, or release authority.",
    };
  } finally {
    await rm(workRoot, { recursive: true, force: true });
  }
}

export async function main(argv = process.argv.slice(2)) {
  if (argv[0] === "--worker") {
    if (argv.length !== 2 || !argv[1]) fail("endurance worker arguments are invalid");
    await workerMain(argv[1]);
    return;
  }
  const receipt = await runProbe(argv);
  process.stdout.write(`${JSON.stringify(receipt)}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    if (typeof process.send === "function") process.send({ event: "worker-error", error: error instanceof Error ? error.message : "unexpected failure" });
    else process.stderr.write(`pixel-deep-work-endurance: ${error instanceof DeepWorkEnduranceError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
