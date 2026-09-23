import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

import { readWorkOperatorStatus } from "../deploy/work-controller/operator-status.mjs";
import { goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";

const run = promisify(execFile);
const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const pixel = join(repo, "pixel");
const digest = (character) => character.repeat(64);
const privateCanary = /PRIVATE_TERMINAL_JOURNEY|controller\.json|(?:^|["'\s])(?:\/tmp\/|[A-Za-z]:\\)/u;

async function invoke(args) {
  const result = await run(pixel, args, {
    cwd: repo,
    env: { ...process.env, LANG: "C.UTF-8" },
    timeout: 30000,
    maxBuffer: 1024 * 1024,
  });
  assert.equal(result.stderr, "");
  const value = JSON.parse(result.stdout);
  assert.doesNotMatch(result.stdout, privateCanary);
  return value;
}

async function rejected(args, pattern) {
  let failure = null;
  try { await invoke(args); } catch (error) { failure = error; }
  assert.ok(failure, "the real Pixel command unexpectedly succeeded");
  assert.match(failure.stderr ?? failure.message, pattern);
  assert.doesNotMatch(`${failure.stdout ?? ""}\n${failure.stderr ?? ""}`, privateCanary);
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-terminal-journey-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  const goalPath = join(root, "goal.json");
  const jobsPath = join(root, "jobs.json");
  const policyPath = join(root, "policy.json");
  const configPath = join(root, "controller.json");
  await mkdir(stateRoot, { mode: 0o700 });
  await chmod(root, 0o700);
  await chmod(stateRoot, 0o700);

  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "scout",
    objective: "PRIVATE_TERMINAL_JOURNEY_CHILD_CANARY", acceptanceCriteria: ["A verified local report exists"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest("a"), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: { filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false },
    budgets: { maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1 },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "PRIVATE_TERMINAL_JOURNEY_GOAL_CANARY", dataClassification: "internal",
    milestones: [{ milestoneId: "inspect", jobId: job.jobId, jobSha256: goalSha256(job), profile: "scout", dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: 60, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxNetworkBytes: 1048576, maxArtifactBytes: 65536, maxFailures: 1 },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-v1.schema.json", schemaVersion: 1, enabled: true,
    stateRoot, goalPath, jobsPath, policyPath, objectStore: join(root, "objects"), workspaceRoot: join(root, "workspace"), executorPath: join(root, "executor"),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1048576 },
    runtime: { dockerPath: join(root, "docker"), backendNetworkName: "pixel-test", backendContainerName: "pixel-model", networkSubnet: "172.30.10.0/29", workerIp: "172.30.10.2", proxyIp: "172.30.10.3", uid: 1000, gid: 1000 },
    controller: { maxTransitions: 1 },
  };
  for (const [path, value] of [[goalPath, goal], [jobsPath, [job]], [policyPath, {}], [configPath, config]]) {
    await writeFile(path, `${JSON.stringify(value)}\n`, { mode: 0o600 });
    await chmod(path, 0o600);
  }
  await initializeGoalLedger({ stateRoot, goal, jobs: [job], now: new Date("2026-08-10T13:00:00.002Z"), suffix: "000000000001" });
  return { stateRoot, goal, jobs: [job], configPath };
}

test("real Pixel terminal commands survive process restarts and converge under pause, resume, and cancel races", {
  skip: process.platform !== "linux" ? "supported-host process and private-file qualification requires Linux" : false,
}, async (t) => {
  const value = await fixture(t);
  const configArgs = ["--config", value.configPath];
  const firstPause = await invoke(["work-pause", ...configArgs]);
  assert.equal(firstPause.action, "paused");
  assert.equal(firstPause.currentBoundedStepMayFinish, false);

  const resumeReview = await invoke(["work-resume", "review", ...configArgs]);
  assert.equal(resumeReview.action, "confirmation-required");
  assert.deepEqual(resumeReview.transition, { from: "paused", to: "ready", preservesExactActiveChild: false });
  await rejected(["work-resume", "apply", ...configArgs, "--confirm-review-sha256", digest("f")], /confirmation differs/u);
  assert.equal((await recoverGoalLedger(value)).head.state, "paused");

  const resumeArgs = ["work-resume", "apply", ...configArgs, "--confirm-review-sha256", resumeReview.confirmation.sha256];
  const resumeRace = await Promise.all(Array.from({ length: 8 }, () => invoke(resumeArgs)));
  assert.equal(resumeRace.filter((receipt) => receipt.action === "resumed").length, 1);
  assert.equal(resumeRace.filter((receipt) => receipt.action === "already-resumed").length, 7);
  assert.equal(resumeRace.every((receipt) => receipt.startsWorkImmediately === false), true);

  await new Promise((resolve) => setTimeout(resolve, 5));
  const pauseRace = await Promise.all(Array.from({ length: 8 }, () => invoke(["work-pause", ...configArgs])));
  assert.equal(pauseRace.filter((receipt) => receipt.action === "paused").length, 1);
  assert.equal(pauseRace.filter((receipt) => receipt.action === "already-paused").length, 7);
  await rejected(resumeArgs, /confirmation differs|exact review/u);
  assert.equal((await recoverGoalLedger(value)).head.state, "paused");

  const cancelReview = await invoke(["work-cancel", "review", ...configArgs]);
  assert.equal(cancelReview.action, "confirmation-required");
  assert.deepEqual(cancelReview.cancellation, { mode: "inactive-goal", recordsTerminalState: true, childState: null });
  await rejected(["work-cancel", "apply", ...configArgs, "--confirm-review-sha256", digest("e")], /confirmation differs/u);
  assert.equal((await recoverGoalLedger(value)).head.state, "paused");

  const cancelArgs = ["work-cancel", "apply", ...configArgs, "--confirm-review-sha256", cancelReview.confirmation.sha256];
  const cancelRace = await Promise.all(Array.from({ length: 8 }, () => invoke(cancelArgs)));
  assert.equal(cancelRace.filter((receipt) => receipt.action === "cancelled-inactive").length, 1);
  assert.equal(cancelRace.filter((receipt) => receipt.action === "already-cancelled").length, 7);
  assert.equal(cancelRace.every((receipt) => receipt.stopsWorker === false), true);

  const ledger = await recoverGoalLedger(value);
  assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), ["ready", "paused", "ready", "paused", "cancelled"]);
  assert.equal(ledger.head.progress.jobsStarted, 0);
  assert.equal(ledger.head.active, null);
  const heartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
  assert.equal(heartbeat.goal.state, "cancelled");
  assert.equal(heartbeat.goal.nextAction, "terminal");
  assert.doesNotMatch(JSON.stringify(heartbeat), privateCanary);

  const terminalReview = await invoke(["work-cancel", "review", ...configArgs]);
  assert.equal(terminalReview.action, "already-cancelled");
  assert.equal(terminalReview.confirmation, null);
  await rejected(["work-pause", ...configArgs], /only a schedulable goal/u);
  await rejected(["work-resume", "review", ...configArgs], /only a paused goal/u);
});
