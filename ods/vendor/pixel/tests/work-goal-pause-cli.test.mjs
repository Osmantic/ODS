import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runGoalPauseCommand } from "../deploy/work-controller/goal-pause-cli.mjs";
import { readWorkOperatorStatus } from "../deploy/work-controller/operator-status.mjs";
import { cancelReadyGoal, dispatchGoalMilestone, goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";

const baseTime = Date.parse("2026-08-10T13:00:00Z");
const digest = (character) => character.repeat(64);

async function fixture(t, name = "pause") {
  const root = await mkdtemp(join(tmpdir(), `pixel-goal-${name}-`));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state"), goalPath = join(root, "goal.json"), jobsPath = join(root, "jobs.json");
  const policyPath = join(root, "policy.json"), configPath = join(root, "controller.json");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "scout",
    objective: "PRIVATE_PAUSE_CHILD_CANARY", acceptanceCriteria: ["A verified local report exists"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest("a"), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: { filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false },
    budgets: { maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1 },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "PRIVATE_PAUSE_GOAL_CANARY", dataClassification: "internal",
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
    if (process.platform !== "win32") await chmod(path, 0o600);
  }
  await initializeGoalLedger({ stateRoot, goal, jobs: [job], now: new Date(baseTime + 2), suffix: "000000000001" });
  return { root, stateRoot, goal, jobs: [job], configPath };
}

function dependencies(index = 0) {
  return {
    now: new Date(baseTime + 3), operatorNow: new Date(baseTime + 4), secret: Buffer.alloc(32, 17),
    suffix: (0x100000000000n + BigInt(index)).toString(16),
    operatorSuffix: (0x200000000000n + BigInt(index)).toString(16),
  };
}

test("trusted pause command stops future cycles between milestones and publishes a content-free heartbeat", async (t) => {
  const value = await fixture(t);
  const receipt = await runGoalPauseCommand(["--config", value.configPath], dependencies());
  assert.equal(receipt.status, "paused");
  assert.equal(receipt.action, "paused");
  assert.equal(receipt.progress.jobsStarted, 0);
  assert.equal(receipt.currentBoundedStepMayFinish, false);
  assert.deepEqual(Object.values(receipt.authority), [false, false, false, false, false, false, false]);
  const heartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
  assert.equal(heartbeat.goal.state, "paused");
  assert.equal(heartbeat.goal.nextAction, "paused");
  assert.equal(heartbeat.goal.progress.jobsStarted, 0);
  assert.equal(heartbeat.controllerState, "ready");
  assert.deepEqual(heartbeat.sessions, []);
  for (const encoded of [JSON.stringify(receipt), JSON.stringify(heartbeat)]) {
    assert.doesNotMatch(encoded, /PRIVATE_PAUSE|controller\.json|\\Users\\|\/tmp\//u);
  }
});

test("concurrent and repeated pause commands converge without duplicate goal transitions", async (t) => {
  const value = await fixture(t, "pause-race");
  const results = await Promise.all(Array.from({ length: 16 }, (_, index) => runGoalPauseCommand(
    ["--config", value.configPath], dependencies(index),
  )));
  assert.equal(results.filter((result) => result.action === "paused").length, 1);
  assert.equal(results.filter((result) => result.action === "already-paused").length, 15);
  assert.equal(new Set(results.map((result) => result.checkpointSha256)).size, 1);
  const ledger = await recoverGoalLedger(value);
  assert.equal(ledger.head.state, "paused");
  assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), ["ready", "paused"]);
  assert.equal(ledger.head.progress.jobsStarted, 0);
});

test("pause reports an already-bound child without claiming that its started step was killed", async (t) => {
  const value = await fixture(t, "pause-active");
  await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "300000000001" });
  const receipt = await runGoalPauseCommand(["--config", value.configPath], {
    ...dependencies(), now: new Date(baseTime + 4), operatorNow: new Date(baseTime + 5),
  });
  assert.equal(receipt.status, "paused");
  assert.equal(receipt.currentBoundedStepMayFinish, true);
  assert.equal(receipt.progress.jobsStarted, 1);
  assert.match(receipt.boundary, /may finish and checkpoint/u);
});

test("pause rejects terminal goals and malformed command input without writing a heartbeat", async (t) => {
  const value = await fixture(t, "pause-terminal");
  await cancelReadyGoal({ ...value, now: new Date(baseTime + 3), suffix: "400000000001" });
  await assert.rejects(runGoalPauseCommand(["--config", value.configPath], dependencies()), /schedulable goal/);
  await assert.rejects(runGoalPauseCommand(["--config", value.configPath, "--extra", "x"], dependencies()), /Usage/);
  await assert.rejects(readWorkOperatorStatus({ stateRoot: value.stateRoot }), /ENOENT|operator status/u);
  const changed = JSON.parse(await readFile(value.configPath, "utf8"));
  changed.extra = true;
  await writeFile(value.configPath, `${JSON.stringify(changed)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(value.configPath, 0o600);
  await assert.rejects(runGoalPauseCommand(["--config", value.configPath], dependencies()), /invalid/);
});
