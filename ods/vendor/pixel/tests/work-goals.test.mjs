import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import {
  appendCheckpoint as appendWorkCheckpoint, checkpointSha256 as workCheckpointSha256,
  initializeCheckpointLedger,
} from "../deploy/work-controller/checkpoints.mjs";
import {
  cancelGoalAfterCleanup, cancelReadyGoal, completeGoal, decideGoalAction, dispatchGoalMilestone, goalCheckpointSha256, goalSha256,
  initializeGoalLedger, observeGoalMilestone, pauseGoal, recoverGoalLedger, resumeGoal,
} from "../deploy/work-controller/goals.mjs";
import { validateWorkGoal, validateWorkGoalCheckpoint } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const hash = (value) => createHash("sha256").update(value).digest("hex");
const baseTime = Date.parse("2026-08-10T13:00:00Z");

function jobBudgets(overrides = {}) {
  return {
    maxRuntimeSeconds: 600, maxIterations: 4, maxToolCalls: 200, maxConcurrentSubagents: 1,
    maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000,
    maxCpuCores: 2, maxMemoryMiB: 2048, maxDiskBytes: 1073741824,
    maxArtifactBytes: 16777216, maxNetworkBytes: 10485760, maxFailures: 2, noProgressLimit: 2,
    ...overrides,
  };
}

function job(jobId, objective, contentSha256, bytes) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "builder", objective,
    acceptanceCriteria: ["The milestone output is structurally valid", "The fixed verification command passes"], dataClassification: "internal",
    verification: {
      mode: "independent",
      checks: [
        { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] },
        { id: "fixed-test", kind: "command", criterionIndexes: [1], workingDirectory: "source", argv: ["/opt/node/bin/node", "--test"], timeoutSeconds: 60, maxOutputBytes: 65536 },
      ],
      immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
      boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
    },
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: bytes, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
      network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only",
      hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: jobBudgets(), outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

function goal(jobs) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "Build and independently qualify a durable two-milestone local capability.", dataClassification: "internal",
    milestones: [
      { milestoneId: "build-core", jobId: jobs[0].jobId, jobSha256: goalSha256(jobs[0]), profile: "builder", dependsOn: [] },
      { milestoneId: "verify-hardening", jobId: jobs[1].jobId, jobSha256: goalSha256(jobs[1]), profile: "builder", dependsOn: ["build-core"] },
    ],
    budgets: {
      maxJobs: 2, maxRuntimeSeconds: 1200, maxModelRequests: 40, maxInputTokens: 200000,
      maxOutputTokens: 40000, maxNetworkBytes: 20971520, maxArtifactBytes: 33554432, maxFailures: 4,
    },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-work-goal-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const content = Buffer.from("durable goal fixture\n");
  const contentSha256 = hash(content);
  const jobs = [
    job("work-1786366800000-abcdef123456", "Build the exact local capability.", contentSha256, content.length),
    job("work-1786366800000-abcdef123457", "Stress and independently verify the capability.", contentSha256, content.length),
  ];
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${digest("f")}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true;
  policy.profiles.builder.enabled = true;
  policy.verifier.enabled = true;
  const entries = [{
    id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256,
    bytes: content.length, classification: "internal", mountMode: "read-only",
  }];
  const compiled = jobs.map((value, index) => compileBuilder(value, policy, entries, { now: new Date(baseTime), suffix: `123456abcde${index}` }));
  return {
    root, stateRoot, jobs, goal: goal(jobs),
    plans: compiled.map((value) => value.plan), leases: compiled.map((value) => value.lease),
  };
}

function childUpdate(head, state, overrides = {}) {
  const field = (name) => Object.hasOwn(overrides, name) ? overrides[name] : head[name];
  return {
    state, iteration: overrides.iteration ?? head.iteration,
    usage: { ...head.usage, ...(overrides.usage ?? {}) },
    progress: { ...head.progress, ...(overrides.progress ?? {}) },
    workspaceSnapshotSha256: field("workspaceSnapshotSha256"),
    artifactManifestSha256: field("artifactManifestSha256"),
    workerSessionSha256: field("workerSessionSha256"),
    verificationEvidenceSha256: field("verificationEvidenceSha256"),
    authorityExpansionObserved: overrides.authorityExpansionObserved ?? false,
    acceptanceCriteriaMutationObserved: overrides.acceptanceCriteriaMutationObserved ?? false,
    externalEffectsObserved: overrides.externalEffectsObserved ?? false,
  };
}

async function initializeChild(value, index, tick, suffix) {
  return initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plans[index], lease: value.leases[index], workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + tick), suffix,
  });
}

async function appendChild(value, index, head, state, tick, suffix, overrides = {}) {
  return appendWorkCheckpoint({
    stateRoot: value.stateRoot, plan: value.plans[index], lease: value.leases[index],
    previousCheckpointSha256: workCheckpointSha256(head), update: childUpdate(head, state, overrides),
    now: new Date(baseTime + tick), suffix,
  });
}

async function completeChild(value, index, head, tick, marker, usage = {}) {
  const running = await appendChild(value, index, head, "running", tick, `${marker}00000000001`, { iteration: 1, workerSessionSha256: digest("3") });
  const verifying = await appendChild(value, index, running.checkpoint, "verifying", tick + 1, `${marker}00000000002`, {
    usage: {
      runtimeSeconds: usage.runtimeSeconds ?? 10, modelRequests: usage.modelRequests ?? 2,
      inputTokens: usage.inputTokens ?? 100, outputTokens: usage.outputTokens ?? 20,
      networkBytes: usage.networkBytes ?? 1000, artifactBytes: usage.artifactBytes ?? 200, failures: 0,
    },
    artifactManifestSha256: digest("2"),
  });
  const verified = await appendChild(value, index, verifying.checkpoint, "verified", tick + 2, `${marker}00000000003`, {
    progress: { criteriaPassing: 2, criteriaFailing: 0, noProgressCount: 0 }, verificationEvidenceSha256: digest("4"),
  });
  return appendChild(value, index, verified.checkpoint, "completed", tick + 3, `${marker}00000000004`);
}

test("goal initialization publishes one complete ledger under contention", async (t) => {
  const value = await fixture(t);
  const attempts = await Promise.allSettled(Array.from({ length: 32 }, (_, index) => initializeGoalLedger({
    ...value, now: new Date(baseTime + 2), suffix: index.toString(16).padStart(12, "0"),
  })));
  assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 1);
  for (const rejected of attempts.filter((attempt) => attempt.status === "rejected")) {
    assert.match(rejected.reason.message, /already initialized/);
  }
  const recovered = await recoverGoalLedger(value);
  assert.equal(recovered.checkpoints.length, 1);
  assert.equal(recovered.head.state, "ready");
  assert.deepEqual(await readdir(join(value.stateRoot, "goal-checkpoints")), [value.goal.goalId]);
});

test("durable goal contracts bind an acyclic exact child-job graph with no authority", async (t) => {
  const value = await fixture(t);
  assert.deepEqual(validateWorkGoal(value.goal), []);
  const initialized = await initializeGoalLedger({ ...value, now: new Date(baseTime + 2), suffix: "000000000001" });
  assert.deepEqual(validateWorkGoalCheckpoint(initialized.checkpoint), []);
  assert.deepEqual(decideGoalAction(initialized.checkpoint, value.goal), {
    action: "dispatch-child", state: "ready", milestoneId: "build-core", jobId: value.jobs[0].jobId, replaysChild: false,
  });
  assert.equal(initialized.checkpoint.authorityExpansionObserved, false);
  assert.equal(value.goal.authority.grantsCompletion, false);
});

test("goal controller completes only after every dependency-ordered child independently verifies", async (t) => {
  const value = await fixture(t);
  await initializeGoalLedger({ ...value, now: new Date(baseTime + 2), suffix: "000000000011" });
  const firstDispatch = await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "000000000012" });
  assert.equal(firstDispatch.checkpoint.active.milestoneId, "build-core");
  const firstInitial = await initializeChild(value, 0, 4, "100000000001");
  const authorized = await observeGoalMilestone({ ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 4), suffix: "000000000013" });
  assert.equal(authorized.action, "start-exact-child");
  assert.equal(authorized.appended, false);
  await completeChild(value, 0, firstInitial.checkpoint, 5, "1");
  const firstComplete = await observeGoalMilestone({ ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 9), suffix: "000000000014" });
  assert.equal(firstComplete.action, "dispatch-next-child");
  const secondDispatch = await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 10), suffix: "000000000015" });
  assert.equal(secondDispatch.checkpoint.active.milestoneId, "verify-hardening");
  const secondInitial = await initializeChild(value, 1, 11, "200000000001");
  await completeChild(value, 1, secondInitial.checkpoint, 12, "2", { runtimeSeconds: 20, modelRequests: 3 });
  const secondComplete = await observeGoalMilestone({ ...value, childPlan: value.plans[1], childLease: value.leases[1], now: new Date(baseTime + 16), suffix: "000000000016" });
  assert.equal(secondComplete.action, "record-completed");
  const completed = await completeGoal({ ...value, now: new Date(baseTime + 17), suffix: "000000000017" });
  assert.equal(completed.checkpoint.state, "completed");
  assert.deepEqual(completed.checkpoint.completedMilestones, ["build-core", "verify-hardening"]);
  assert.equal(completed.checkpoint.usage.runtimeSeconds, 30);
  assert.equal(completed.checkpoint.usage.modelRequests, 5);
  const recovered = await recoverGoalLedger(value);
  assert.deepEqual(recovered.checkpoints.map((checkpoint) => checkpoint.state), ["ready", "running", "ready", "running", "ready", "completed"]);
  assert.deepEqual(recovered.action, { action: "terminal", state: "completed", replaysChild: false });
  assert.ok(recovered.checkpoints.every((checkpoint, index) => index === 0 || checkpoint.previousCheckpointSha256 === goalCheckpointSha256(recovered.checkpoints[index - 1])));
});

test("a recorded dispatch recovers without replay and concurrent controllers have one append winner", async (t) => {
  const value = await fixture(t);
  await initializeGoalLedger({ ...value, now: new Date(baseTime + 2), suffix: "000000000021" });
  const results = await Promise.allSettled(Array.from({ length: 32 }, (_, index) => dispatchGoalMilestone({
    ...value, now: new Date(baseTime + 3), suffix: (index + 0x22).toString(16).padStart(12, "0"),
  })));
  assert.equal(results.filter((result) => result.status === "fulfilled").length, 1);
  assert.equal(results.filter((result) => result.status === "rejected").length, 31);
  const recovered = await recoverGoalLedger(value);
  assert.deepEqual(recovered.action, {
    action: "recover-or-continue-child", state: "running", milestoneId: "build-core", jobId: value.jobs[0].jobId, replaysChild: false,
  });
  assert.equal(recovered.checkpoints.length, 2);
});

test("goal pause and resume preserve every fact before or during a child while inactive cancellation is terminal", async (t) => {
  const between = await fixture(t);
  await initializeGoalLedger({ ...between, now: new Date(baseTime + 2), suffix: "00000000006a" });
  const betweenBefore = (await recoverGoalLedger(between)).head;
  const betweenPaused = await pauseGoal({ ...between, now: new Date(baseTime + 3), suffix: "00000000006b" });
  assert.equal(betweenPaused.checkpoint.state, "paused");
  assert.equal(betweenPaused.checkpoint.active, null);
  assert.deepEqual(validateWorkGoalCheckpoint(betweenPaused.checkpoint), []);
  assert.equal(betweenPaused.checkpoint.progress.jobsStarted, 0);
  const betweenResumed = await resumeGoal({ ...between, now: new Date(baseTime + 4), suffix: "00000000006c" });
  assert.equal(betweenResumed.checkpoint.state, "ready");
  assert.deepEqual(betweenResumed.checkpoint.progress, betweenBefore.progress);
  await dispatchGoalMilestone({ ...between, now: new Date(baseTime + 5), suffix: "00000000006d" });

  const value = await fixture(t);
  await initializeGoalLedger({ ...value, now: new Date(baseTime + 2), suffix: "000000000070" });
  await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "000000000071" });
  const before = (await recoverGoalLedger(value)).head;
  const pauseRace = await Promise.allSettled(Array.from({ length: 16 }, (_, index) => pauseGoal({
    ...value, now: new Date(baseTime + 4), suffix: (0x700000000000n + BigInt(index)).toString(16),
  })));
  assert.equal(pauseRace.filter((result) => result.status === "fulfilled").length, 1);
  const paused = pauseRace.find((result) => result.status === "fulfilled").value;
  assert.equal(paused.checkpoint.state, "paused");
  assert.deepEqual(decideGoalAction(paused.checkpoint, value.goal), { action: "paused", state: "paused", replaysChild: false });
  for (const field of ["completedMilestones", "active", "observation", "usage", "progress", "failureFingerprintSha256", "authorityExpansionObserved", "externalEffectsObserved"]) {
    assert.deepEqual(paused.checkpoint[field], before[field]);
  }
  const resumed = await resumeGoal({ ...value, now: new Date(baseTime + 5), suffix: "000000000073" });
  assert.equal(resumed.checkpoint.state, "running");
  assert.equal(resumed.checkpoint.active.jobId, before.active.jobId);
  await assert.rejects(cancelReadyGoal({ ...value, now: new Date(baseTime + 6), suffix: "000000000074" }), /supervised child cleanup/);

  const cancellable = await fixture(t);
  await initializeGoalLedger({ ...cancellable, now: new Date(baseTime + 2), suffix: "000000000075" });
  await pauseGoal({ ...cancellable, now: new Date(baseTime + 3), suffix: "000000000076" });
  const cancelled = await cancelReadyGoal({ ...cancellable, now: new Date(baseTime + 4), suffix: "00000000007d" });
  assert.equal(cancelled.checkpoint.state, "cancelled");
  assert.deepEqual((await recoverGoalLedger(cancellable)).action, { action: "terminal", state: "cancelled", replaysChild: false });
});

test("active goal cancellation requires exact terminal Builder cleanup evidence", async (t) => {
  const value = await fixture(t);
  await initializeGoalLedger({ ...value, now: new Date(baseTime + 2), suffix: "000000000077" });
  await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "000000000078" });
  const initial = await initializeChild(value, 0, 4, "700000000001");
  const running = await appendChild(value, 0, initial.checkpoint, "running", 5, "700000000002", {
    iteration: 1, workerSessionSha256: digest("3"),
  });
  await pauseGoal({ ...value, now: new Date(baseTime + 6), suffix: "000000000079" });
  await assert.rejects(cancelGoalAfterCleanup({
    ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 7), suffix: "00000000007a",
  }), /failed child cleanup evidence/);
  const failed = await appendChild(value, 0, running.checkpoint, "failed", 8, "700000000003", {
    usage: { runtimeSeconds: 1, failures: 1 }, progress: { noProgressCount: 1, failureFingerprintSha256: digest("7") },
  });
  const substituted = structuredClone(value.leases[0]);
  substituted.planSha256 = digest("e");
  await assert.rejects(cancelGoalAfterCleanup({
    ...value, childPlan: value.plans[0], childLease: substituted, now: new Date(baseTime + 9), suffix: "00000000007b",
  }));
  const cancelled = await cancelGoalAfterCleanup({
    ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 9), suffix: "00000000007c",
  });
  assert.equal(cancelled.checkpoint.state, "cancelled");
  assert.equal(cancelled.checkpoint.active, null);
  assert.equal(cancelled.checkpoint.observation.childCheckpointSha256, workCheckpointSha256(failed.checkpoint));
  assert.equal(cancelled.checkpoint.observation.childState, "failed");
  assert.equal(cancelled.checkpoint.usage.failures, 1);
  assert.equal(cancelled.checkpoint.progress.failures, 1);
  assert.deepEqual((await recoverGoalLedger(value)).action, { action: "terminal", state: "cancelled", replaysChild: false });
});

test("goal graph pressure rejects 512 cyclic, unknown, duplicate, and noncanonical variants", () => {
  const milestones = Array.from({ length: 64 }, (_, index) => ({
    milestoneId: `milestone-${String(index).padStart(2, "0")}`,
    jobId: `work-${String(1786366800000 + index).padStart(13, "0")}-${index.toString(16).padStart(12, "0")}`,
    jobSha256: hash(`job-${index}`), profile: "builder",
    dependsOn: index === 0 ? [] : [`milestone-${String(index - 1).padStart(2, "0")}`],
  }));
  const maximum = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-000000000001", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "Exercise the maximum durable goal graph.", dataClassification: "internal", milestones,
    budgets: {
      maxJobs: 64, maxRuntimeSeconds: 31536000, maxModelRequests: 6400000, maxInputTokens: 128000000000,
      maxOutputTokens: 32000000000, maxNetworkBytes: 687194767360, maxArtifactBytes: 2199023255552, maxFailures: 64000,
    },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  assert.deepEqual(validateWorkGoal(maximum), []);
  for (let index = 0; index < 512; index += 1) {
    const hostile = structuredClone(maximum);
    if (index % 4 === 0) hostile.milestones[index % 64].dependsOn = ["unknown-milestone"];
    else if (index % 4 === 1) hostile.milestones[0].dependsOn = ["milestone-63"];
    else if (index % 4 === 2) hostile.milestones[1].jobId = hostile.milestones[0].jobId;
    else hostile.milestones.reverse();
    assert.ok(validateWorkGoal(hostile).length > 0, `hostile goal mutation ${index} passed`);
  }
});

test("goal wait and resume follow the child checkpoint without widening or redispatch", async (t) => {
  const value = await fixture(t);
  await initializeGoalLedger({ ...value, now: new Date(baseTime + 2), suffix: "000000000031" });
  await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "000000000032" });
  const initial = await initializeChild(value, 0, 4, "300000000001");
  const running = await appendChild(value, 0, initial.checkpoint, "running", 5, "300000000002", { iteration: 1, workerSessionSha256: digest("3") });
  const waitingCheckpoint = await appendChild(value, 0, running.checkpoint, "waiting-authority", 6, "300000000003");
  const waiting = await observeGoalMilestone({ ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 7), suffix: "000000000033" });
  assert.equal(waiting.action, "wait-for-child-authority");
  assert.equal((await recoverGoalLedger(value)).action.action, "wait-for-child-authority");
  const duplicate = await observeGoalMilestone({ ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 8), suffix: "000000000034" });
  assert.equal(duplicate.appended, false);
  await appendChild(value, 0, waitingCheckpoint.checkpoint, "running", 9, "300000000004", { workerSessionSha256: digest("5") });
  const resumed = await observeGoalMilestone({ ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 10), suffix: "000000000035" });
  assert.equal(resumed.action, "continue-or-recover-child");
  assert.equal((await recoverGoalLedger(value)).head.state, "running");
});

test("goal controller propagates child no-progress and rejects worker-only fake completion", async (t) => {
  const value = await fixture(t);
  await initializeGoalLedger({ ...value, now: new Date(baseTime + 2), suffix: "000000000041" });
  await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "000000000042" });
  const initial = await initializeChild(value, 0, 4, "400000000001");
  const running = await appendChild(value, 0, initial.checkpoint, "running", 5, "400000000002", { iteration: 1, workerSessionSha256: digest("3") });
  const verifying = await appendChild(value, 0, running.checkpoint, "verifying", 6, "400000000003", { artifactManifestSha256: digest("2") });
  const workerOnly = await appendChild(value, 0, verifying.checkpoint, "verified", 7, "400000000004", {
    progress: { criteriaPassing: 2, criteriaFailing: 0 }, verificationEvidenceSha256: digest("4"),
  });
  const notTerminal = await observeGoalMilestone({ ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 8), suffix: "000000000043" });
  assert.equal(notTerminal.action, "continue-or-recover-child");
  assert.equal(notTerminal.appended, false);
  await assert.rejects(completeGoal({ ...value, now: new Date(baseTime + 9), suffix: "000000000044" }), /complete independently verified/);
  const substitutedLease = structuredClone(value.leases[0]);
  substitutedLease.planSha256 = digest("e");
  await assert.rejects(observeGoalMilestone({ ...value, childPlan: value.plans[0], childLease: substitutedLease, now: new Date(baseTime + 9), suffix: "000000000045" }));
  await appendChild(value, 0, workerOnly.checkpoint, "completed", 10, "400000000005");
  assert.equal((await observeGoalMilestone({ ...value, childPlan: value.plans[0], childLease: value.leases[0], now: new Date(baseTime + 11), suffix: "000000000046" })).action, "dispatch-next-child");

  const stalled = await fixture(t);
  await initializeGoalLedger({ ...stalled, now: new Date(baseTime + 2), suffix: "000000000051" });
  await dispatchGoalMilestone({ ...stalled, now: new Date(baseTime + 3), suffix: "000000000052" });
  const stalledInitial = await initializeChild(stalled, 0, 4, "500000000001");
  const running1 = await appendChild(stalled, 0, stalledInitial.checkpoint, "running", 5, "500000000002", { iteration: 1, workerSessionSha256: digest("3") });
  const verifying1 = await appendChild(stalled, 0, running1.checkpoint, "verifying", 6, "500000000003", { usage: { modelRequests: 1 }, artifactManifestSha256: digest("2") });
  const verified1 = await appendChild(stalled, 0, verifying1.checkpoint, "verified", 7, "500000000004", { usage: { failures: 1 }, progress: { noProgressCount: 1 }, verificationEvidenceSha256: digest("4") });
  const running2 = await appendChild(stalled, 0, verified1.checkpoint, "running", 8, "500000000005", { iteration: 2, workerSessionSha256: digest("5"), artifactManifestSha256: null, verificationEvidenceSha256: null });
  const verifying2 = await appendChild(stalled, 0, running2.checkpoint, "verifying", 9, "500000000006", { usage: { modelRequests: 2 }, artifactManifestSha256: digest("6") });
  const verified2 = await appendChild(stalled, 0, verifying2.checkpoint, "verified", 10, "500000000007", { usage: { failures: 2 }, progress: { noProgressCount: 2 }, verificationEvidenceSha256: digest("7") });
  await appendChild(stalled, 0, verified2.checkpoint, "no-progress", 11, "500000000008");
  const stopped = await observeGoalMilestone({ ...stalled, childPlan: stalled.plans[0], childLease: stalled.leases[0], now: new Date(baseTime + 12), suffix: "000000000053" });
  assert.equal(stopped.action, "no-progress");
  assert.equal(stopped.checkpoint.failureFingerprintSha256.length, 64);
  assert.deepEqual((await recoverGoalLedger(stalled)).action, { action: "terminal", state: "no-progress", replaysChild: false });
  await assert.rejects(completeGoal({ ...stalled, now: new Date(baseTime + 13), suffix: "000000000054" }), /complete independently verified/);
});

test("goal controller propagates terminal recovery-inconclusive without dispatching another child", async (t) => {
  const value = await fixture(t);
  await initializeGoalLedger({ ...value, now: new Date(baseTime + 2), suffix: "000000000061" });
  await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "000000000062" });
  const initial = await initializeChild(value, 0, 4, "600000000001");
  const running = await appendChild(value, 0, initial.checkpoint, "running", 5, "600000000002", {
    iteration: 1, workerSessionSha256: digest("3"),
  });
  await appendChild(value, 0, running.checkpoint, "recovery-inconclusive", 6, "600000000003", {
    usage: { failures: 1 }, progress: { noProgressCount: 1, failureFingerprintSha256: digest("8") },
  });
  const observed = await observeGoalMilestone({
    ...value, childPlan: value.plans[0], childLease: value.leases[0],
    now: new Date(baseTime + 7), suffix: "000000000063",
  });
  assert.equal(observed.action, "recovery-inconclusive");
  assert.equal(observed.checkpoint.active, null);
  assert.equal(observed.checkpoint.observation.childState, "recovery-inconclusive");
  assert.deepEqual((await recoverGoalLedger(value)).action, {
    action: "terminal", state: "recovery-inconclusive", replaysChild: false,
  });
});

test("goal admission rejects graph, job, classification, criteria, and aggregate-budget substitution", async (t) => {
  const value = await fixture(t);
  const cycle = structuredClone(value.goal);
  cycle.milestones[0].dependsOn = ["verify-hardening"];
  assert.ok(validateWorkGoal(cycle).some((error) => error.includes("cycle")));
  const reordered = structuredClone(value.goal);
  reordered.milestones.reverse();
  assert.ok(validateWorkGoal(reordered).some((error) => error.includes("canonical")));
  const unknown = structuredClone(value.goal);
  unknown.milestones[1].dependsOn = ["missing"];
  assert.ok(validateWorkGoal(unknown).some((error) => error.includes("unknown")));
  const changedJob = structuredClone(value.jobs[0]);
  changedJob.acceptanceCriteria[0] = "Worker says it is done";
  await assert.rejects(initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: [changedJob, value.jobs[1]], now: new Date(baseTime + 2), suffix: "000000000051" }), /exact child job/);
  const downgraded = structuredClone(value.jobs[0]);
  downgraded.dataClassification = "public";
  downgraded.inputs[0].classification = "public";
  const downgradedGoal = structuredClone(value.goal);
  downgradedGoal.milestones[0].jobSha256 = goalSha256(downgraded);
  await assert.rejects(initializeGoalLedger({ stateRoot: value.stateRoot, goal: downgradedGoal, jobs: [downgraded, value.jobs[1]], now: new Date(baseTime + 2), suffix: "000000000052" }), /classification/);
  const underfunded = structuredClone(value.goal);
  underfunded.budgets.maxRuntimeSeconds -= 1;
  await assert.rejects(initializeGoalLedger({ stateRoot: value.stateRoot, goal: underfunded, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "000000000053" }), /aggregate runtimeSeconds/);
});

test("goal recovery tolerates an interrupted publish, rejects tampering, gaps, and alias links", async (t) => {
  const tampered = await fixture(t);
  const initial = await initializeGoalLedger({ ...tampered, now: new Date(baseTime + 2), suffix: "000000000061" });
  const changed = JSON.parse(await readFile(initial.path, "utf8"));
  changed.goalSha256 = digest("9");
  await writeFile(initial.path, `${JSON.stringify(changed)}\n`, { mode: 0o600 });
  await assert.rejects(recoverGoalLedger(tampered), /immutable binding/);

  const gap = await fixture(t);
  const gapInitial = await initializeGoalLedger({ ...gap, now: new Date(baseTime + 2), suffix: "000000000062" });
  await writeFile(join(gapInitial.goalRoot, "records", "0000002.json"), "{}\n", { mode: 0o600 });
  await assert.rejects(recoverGoalLedger(gap), /sequence is incomplete/);

  // A committed record hardlinked into the goal ledger's own tmp/ is the residue of an interrupted
  // publish (a crash between writeRecord's link() of the record and the unlink() of its staging
  // twin); recovery reads the durable record instead of wedging the goal permanently.
  const orphan = await fixture(t);
  const orphanInitial = await initializeGoalLedger({ ...orphan, now: new Date(baseTime + 2), suffix: "000000000063" });
  await link(orphanInitial.path, join(orphanInitial.goalRoot, "tmp", ".goal-checkpoint-0-orphan"));
  await assert.doesNotReject(recoverGoalLedger(orphan));

  // A record aliased anywhere other than the ledger's own tmp/ has no matching staging twin, so it
  // stays rejected as non-single-link tampering.
  const alias = await fixture(t);
  const aliasInitial = await initializeGoalLedger({ ...alias, now: new Date(baseTime + 2), suffix: "000000000064" });
  await link(aliasInitial.path, join(aliasInitial.goalRoot, "external-alias"));
  await assert.rejects(recoverGoalLedger(alias), /single-link/);
});
