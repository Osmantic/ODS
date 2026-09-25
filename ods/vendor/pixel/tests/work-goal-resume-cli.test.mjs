import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runGoalResumeCommand } from "../deploy/work-controller/goal-resume-cli.mjs";
import { readWorkOperatorStatus } from "../deploy/work-controller/operator-status.mjs";
import {
  cancelReadyGoal, dispatchGoalMilestone, goalSha256, initializeGoalLedger, pauseGoal,
  recoverGoalLedger, resumeGoal,
} from "../deploy/work-controller/goals.mjs";

const baseTime = Date.parse("2026-08-10T13:00:00Z");
const digest = (character) => character.repeat(64);

async function fixture(t, name = "resume") {
  const root = await mkdtemp(join(tmpdir(), `pixel-goal-${name}-`));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state"), goalPath = join(root, "goal.json"), jobsPath = join(root, "jobs.json");
  const policyPath = join(root, "policy.json"), configPath = join(root, "controller.json");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "scout",
    objective: "PRIVATE_RESUME_CHILD_CANARY", acceptanceCriteria: ["A verified local report exists"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest("a"), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: { filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false },
    budgets: { maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1 },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "PRIVATE_RESUME_GOAL_CANARY", dataClassification: "internal",
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
    now: new Date(baseTime + 10 + index), operatorNow: new Date(baseTime + 100 + index), secret: Buffer.alloc(32, 23),
    suffix: (0x500000000000n + BigInt(index)).toString(16),
    operatorSuffix: (0x600000000000n + BigInt(index)).toString(16),
  };
}

test("resume review is inert and exact confirmation enables later cycles without launching work", async (t) => {
  const value = await fixture(t);
  await pauseGoal({ ...value, now: new Date(baseTime + 3), suffix: "400000000001" });
  const review = await runGoalResumeCommand(["review", "--config", value.configPath]);
  assert.equal(review.status, "paused");
  assert.equal(review.action, "confirmation-required");
  assert.deepEqual(review.transition, { from: "paused", to: "ready", preservesExactActiveChild: false });
  assert.equal(review.schedulingEffect, "none-until-confirmed");
  await assert.rejects(readWorkOperatorStatus({ stateRoot: value.stateRoot }), /ENOENT|operator status/u);
  await assert.rejects(runGoalResumeCommand([
    "apply", "--config", value.configPath, "--confirm-review-sha256", digest("e"),
  ], dependencies()), /confirmation differs/);
  assert.equal((await recoverGoalLedger(value)).head.state, "paused");

  const applied = await runGoalResumeCommand([
    "apply", "--config", value.configPath, "--confirm-review-sha256", review.confirmation.sha256,
  ], dependencies());
  assert.equal(applied.status, "ready");
  assert.equal(applied.action, "resumed");
  assert.equal(applied.nextAction, "dispatch-child");
  assert.equal(applied.startsWorkImmediately, false);
  assert.deepEqual(Object.values(applied.authority), [false, false, false, false, false, false, false]);
  const heartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
  assert.equal(heartbeat.goal.state, "ready");
  assert.equal(heartbeat.goal.nextAction, "dispatch-child");
  for (const encoded of [JSON.stringify(review), JSON.stringify(applied), JSON.stringify(heartbeat)]) {
    assert.doesNotMatch(encoded, /PRIVATE_RESUME|controller\.json|\\Users\\|\/tmp\//u);
  }
});

test("concurrent and repeated exact resume applies converge on one transition", async (t) => {
  const value = await fixture(t, "resume-race");
  await pauseGoal({ ...value, now: new Date(baseTime + 3), suffix: "400000000010" });
  const review = await runGoalResumeCommand(["review", "--config", value.configPath]);
  const argv = ["apply", "--config", value.configPath, "--confirm-review-sha256", review.confirmation.sha256];
  const results = await Promise.all(Array.from({ length: 16 }, (_, index) => runGoalResumeCommand(argv, dependencies(index))));
  assert.equal(results.filter((result) => result.action === "resumed").length, 1);
  assert.equal(results.filter((result) => result.action === "already-resumed").length, 15);
  const ledger = await recoverGoalLedger(value);
  assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), ["ready", "paused", "ready"]);
  assert.equal(ledger.head.progress.jobsStarted, 0);
});

test("active-child resume preserves the same custody and launches nothing", async (t) => {
  const value = await fixture(t, "resume-active");
  await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "400000000020" });
  const before = (await recoverGoalLedger(value)).head.active;
  await pauseGoal({ ...value, now: new Date(baseTime + 4), suffix: "400000000021" });
  const review = await runGoalResumeCommand(["review", "--config", value.configPath]);
  assert.deepEqual(review.transition, { from: "paused", to: "running", preservesExactActiveChild: true });
  const applied = await runGoalResumeCommand([
    "apply", "--config", value.configPath, "--confirm-review-sha256", review.confirmation.sha256,
  ], dependencies());
  const ledger = await recoverGoalLedger(value);
  assert.equal(applied.status, "running");
  assert.equal(applied.nextAction, "recover-or-continue-child");
  assert.equal(applied.preservesExactActiveChild, true);
  assert.deepEqual(ledger.head.active, before);
  assert.equal(ledger.head.progress.jobsStarted, 1);
});

test("stale, terminal, never-paused, and malformed resume journeys fail closed", async (t) => {
  const value = await fixture(t, "resume-stale");
  await assert.rejects(runGoalResumeCommand(["review", "--config", value.configPath]), /only a paused goal/);
  await pauseGoal({ ...value, now: new Date(baseTime + 3), suffix: "400000000030" });
  const stale = await runGoalResumeCommand(["review", "--config", value.configPath]);
  await resumeGoal({ ...value, now: new Date(baseTime + 4), suffix: "400000000031" });
  await pauseGoal({ ...value, now: new Date(baseTime + 5), suffix: "400000000032" });
  await assert.rejects(runGoalResumeCommand([
    "apply", "--config", value.configPath, "--confirm-review-sha256", stale.confirmation.sha256,
  ], dependencies()), /confirmation differs/);
  await assert.rejects(runGoalResumeCommand(["review", "--config", value.configPath, "--extra", "x"]), /does not accept|Usage/);

  const terminal = await fixture(t, "resume-terminal");
  await cancelReadyGoal({ ...terminal, now: new Date(baseTime + 3), suffix: "400000000040" });
  await assert.rejects(runGoalResumeCommand(["review", "--config", terminal.configPath]), /only a paused goal/);
  await assert.rejects(runGoalResumeCommand(["apply", "--config", terminal.configPath]), /exact review|Usage/);
  assert.equal((await recoverGoalLedger(value)).head.state, "paused");
});
