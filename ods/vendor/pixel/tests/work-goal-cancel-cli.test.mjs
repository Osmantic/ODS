import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runGoalCancelCommand } from "../deploy/work-controller/goal-cancel-cli.mjs";
import { readWorkOperatorStatus } from "../deploy/work-controller/operator-status.mjs";
import { dispatchGoalMilestone, goalSha256, initializeGoalLedger, pauseGoal, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";

const baseTime = Date.parse("2026-08-10T13:00:00Z");
const digest = (character) => character.repeat(64);

async function fixture(t, name = "cancel") {
  const root = await mkdtemp(join(tmpdir(), `pixel-goal-${name}-`));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state"), goalPath = join(root, "goal.json"), jobsPath = join(root, "jobs.json");
  const policyPath = join(root, "policy.json"), configPath = join(root, "controller.json");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "scout",
    objective: "PRIVATE_CANCEL_CHILD_CANARY", acceptanceCriteria: ["A verified local report exists"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest("a"), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: { filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false },
    budgets: { maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1 },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "PRIVATE_CANCEL_GOAL_CANARY", dataClassification: "internal",
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
    now: new Date(baseTime + 10 + index), operatorNow: new Date(baseTime + 100 + index), secret: Buffer.alloc(32, 29),
    suffix: (0x700000000000n + BigInt(index)).toString(16),
    operatorSuffix: (0x800000000000n + BigInt(index)).toString(16),
  };
}

test("inactive cancellation review is inert and exact apply ends the goal without stopping a worker", async (t) => {
  const value = await fixture(t);
  const review = await runGoalCancelCommand(["review", "--config", value.configPath]);
  assert.equal(review.status, "ready");
  assert.equal(review.action, "confirmation-required");
  assert.deepEqual(review.cancellation, { mode: "inactive-goal", recordsTerminalState: true, childState: null });
  assert.equal(review.schedulingEffect, "none-until-confirmed");
  assert.equal(review.stopsWorker, false);
  await assert.rejects(readWorkOperatorStatus({ stateRoot: value.stateRoot }), /ENOENT|operator status/u);
  await assert.rejects(runGoalCancelCommand([
    "apply", "--config", value.configPath, "--confirm-review-sha256", digest("e"),
  ], dependencies()), /confirmation differs/);
  assert.equal((await recoverGoalLedger(value)).head.state, "ready");

  const applied = await runGoalCancelCommand([
    "apply", "--config", value.configPath, "--confirm-review-sha256", review.confirmation.sha256,
  ], dependencies());
  assert.equal(applied.status, "cancelled");
  assert.equal(applied.action, "cancelled-inactive");
  assert.equal(applied.cancellationMode, "inactive-goal");
  assert.equal(applied.stopsWorker, false);
  assert.deepEqual(Object.values(applied.authority), [false, false, false, false, false, false, false]);
  const heartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
  assert.equal(heartbeat.goal.state, "cancelled");
  assert.equal(heartbeat.goal.nextAction, "terminal");
  for (const encoded of [JSON.stringify(review), JSON.stringify(applied), JSON.stringify(heartbeat)]) {
    assert.doesNotMatch(encoded, /PRIVATE_CANCEL|controller\.json|\\Users\\|\/tmp\//u);
  }
});

test("concurrent cancellation applies converge and a repeat binds the same review", async (t) => {
  const value = await fixture(t, "cancel-race");
  await pauseGoal({ ...value, now: new Date(baseTime + 3), suffix: "600000000001" });
  const review = await runGoalCancelCommand(["review", "--config", value.configPath]);
  const argv = ["apply", "--config", value.configPath, "--confirm-review-sha256", review.confirmation.sha256];
  const results = await Promise.all(Array.from({ length: 16 }, (_, index) => runGoalCancelCommand(argv, dependencies(index))));
  assert.equal(results.filter((result) => result.action === "cancelled-inactive").length, 1);
  assert.equal(results.filter((result) => result.action === "already-cancelled").length, 15);
  const ledger = await recoverGoalLedger(value);
  assert.deepEqual(ledger.checkpoints.map((checkpoint) => checkpoint.state), ["ready", "paused", "cancelled"]);
  assert.equal(ledger.head.progress.jobsStarted, 0);
});

test("stale reviews and active children without terminal cleanup evidence fail closed", async (t) => {
  const staleValue = await fixture(t, "cancel-stale");
  const stale = await runGoalCancelCommand(["review", "--config", staleValue.configPath]);
  await pauseGoal({ ...staleValue, now: new Date(baseTime + 3), suffix: "600000000010" });
  await assert.rejects(runGoalCancelCommand([
    "apply", "--config", staleValue.configPath, "--confirm-review-sha256", stale.confirmation.sha256,
  ], dependencies()), /confirmation differs/);
  assert.equal((await recoverGoalLedger(staleValue)).head.state, "paused");

  const active = await fixture(t, "cancel-active");
  await dispatchGoalMilestone({ ...active, now: new Date(baseTime + 3), suffix: "600000000020" });
  const blocked = await runGoalCancelCommand(["review", "--config", active.configPath]);
  assert.equal(blocked.action, "child-terminal-evidence-required");
  assert.equal(blocked.confirmation, null);
  assert.equal(blocked.stopsWorker, false);
  await assert.rejects(runGoalCancelCommand([
    "apply", "--config", active.configPath, "--confirm-review-sha256", digest("d"),
  ], dependencies()), /blocked/);
  assert.equal((await recoverGoalLedger(active)).head.state, "running");
});

test("legacy exact-goal confirmation remains compatible for inactive goals and malformed journeys reject", async (t) => {
  const value = await fixture(t, "cancel-legacy");
  await assert.rejects(runGoalCancelCommand([
    "--config", value.configPath, "--confirm-goal-sha256", digest("f"),
  ], dependencies()), /confirmation differs/);
  const cancelled = await runGoalCancelCommand([
    "--config", value.configPath, "--confirm-goal-sha256", goalSha256(value.goal),
  ], dependencies());
  assert.equal(cancelled.action, "cancelled-inactive");
  const terminalReview = await runGoalCancelCommand(["review", "--config", value.configPath]);
  assert.equal(terminalReview.action, "already-cancelled");
  assert.equal(terminalReview.confirmation, null);
  await assert.rejects(runGoalCancelCommand(["apply", "--config", value.configPath]), /Usage/);
  const changed = JSON.parse(await readFile(value.configPath, "utf8"));
  changed.extra = true;
  await writeFile(value.configPath, `${JSON.stringify(changed)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(value.configPath, 0o600);
  await assert.rejects(runGoalCancelCommand(["review", "--config", value.configPath]), /invalid/);
});
