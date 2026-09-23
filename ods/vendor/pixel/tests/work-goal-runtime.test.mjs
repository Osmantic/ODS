import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, open, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileScout } from "../deploy/work-broker/broker.mjs";
import { appendCheckpoint, checkpointSha256, recoverCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import { dispatchGoalMilestone, goalSha256, initializeGoalLedger, pauseGoal, recoverGoalLedger, resumeGoal } from "../deploy/work-controller/goals.mjs";
import { runGoalContinuousCommand } from "../deploy/work-controller/goal-continuous-cli.mjs";
import { runGoalCycle } from "../deploy/work-controller/goal-runtime.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const hash = (value) => createHash("sha256").update(value).digest("hex");
const baseTime = Date.parse("2026-08-10T13:00:00Z");

function budgets() {
  return {
    maxRuntimeSeconds: 60, maxIterations: 2, maxToolCalls: 20, maxConcurrentSubagents: 1,
    maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1,
    maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576,
    maxFailures: 1, noProgressLimit: 1,
  };
}

function scoutJob(jobId, objective, contentSha256, bytes) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "scout", objective,
    acceptanceCriteria: ["A bounded independently verified finding report exists"], dataClassification: "internal",
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

async function fixture(t, jobCount = 2) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-runtime-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const content = Buffer.from("goal runtime input\n");
  const contentSha256 = hash(content);
  const jobs = Array.from({ length: jobCount }, (_, index) => scoutJob(
    `work-1786366800000-${(0xabcdef123456n + BigInt(index)).toString(16)}`,
    `Inspect exact milestone ${index + 1}.`, contentSha256, content.length,
  ));
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${digest("f")}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true;
  const entries = [{
    id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256,
    bytes: content.length, classification: "internal", mountMode: "read-only",
  }];
  const runs = new Map(jobs.map((job, index) => {
    const compiled = compileScout(job, policy, entries, {
      now: new Date(baseTime), suffix: (0x123456abcde0n + BigInt(index)).toString(16),
    });
    return [job.jobId, { plan: compiled.plan, lease: compiled.lease, workspaceSnapshotSha256: digest(((index % 15) + 1).toString(16)) }];
  }));
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: `Continuously complete ${jobCount} bounded local milestones.`, dataClassification: "internal",
    milestones: jobs.map((job, index) => ({
      milestoneId: `milestone-${String(index + 1).padStart(2, "0")}`, jobId: job.jobId, jobSha256: goalSha256(job), profile: "scout",
      dependsOn: index === 0 ? [] : [`milestone-${String(index).padStart(2, "0")}`],
    })),
    budgets: {
      maxJobs: jobCount, maxRuntimeSeconds: 60 * jobCount, maxModelRequests: 5 * jobCount,
      maxInputTokens: 10000 * jobCount, maxOutputTokens: 2000 * jobCount,
      maxNetworkBytes: 1048576 * jobCount, maxArtifactBytes: 65536 * jobCount, maxFailures: jobCount,
    },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  await initializeGoalLedger({ stateRoot, goal, jobs, now: new Date(baseTime + 2), suffix: "000000000001" });
  let suffixCounter = 0x100;
  let time = baseTime + 3;
  return {
    root, stateRoot, goal, jobs, runs, policy, entries,
    resolveChildRun: async ({ job }) => runs.get(job.jobId),
    suffix: () => (suffixCounter++).toString(16).padStart(12, "0"),
    clock: () => new Date(time++),
  };
}

function update(head, state, overrides = {}) {
  const field = (name) => Object.hasOwn(overrides, name) ? overrides[name] : head[name];
  return {
    state, iteration: overrides.iteration ?? head.iteration,
    usage: { ...head.usage, ...(overrides.usage ?? {}) }, progress: { ...head.progress, ...(overrides.progress ?? {}) },
    workspaceSnapshotSha256: field("workspaceSnapshotSha256"), artifactManifestSha256: field("artifactManifestSha256"),
    workerSessionSha256: field("workerSessionSha256"), verificationEvidenceSha256: field("verificationEvidenceSha256"),
    authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false,
  };
}

async function appendNext(run, stateRoot, head, state, suffix, overrides = {}) {
  return appendCheckpoint({
    stateRoot, plan: run.plan, lease: run.lease, previousCheckpointSha256: checkpointSha256(head),
    update: update(head, state, overrides), now: new Date(Date.parse(head.createdAt) + 1), suffix,
  });
}

async function completeDriver(value) {
  const run = value.runs.get(value.job.jobId);
  const running = await appendNext(run, value.stateRoot, value.childCheckpoint, "running", "a00000000001", { iteration: 1, workerSessionSha256: digest("3") });
  const verifying = await appendNext(run, value.stateRoot, running.checkpoint, "verifying", "a00000000002", {
    usage: { runtimeSeconds: 2, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 100, artifactBytes: 20 }, artifactManifestSha256: digest("4"),
  });
  const verified = await appendNext(run, value.stateRoot, verifying.checkpoint, "verified", "a00000000003", {
    progress: { criteriaPassing: 1, criteriaFailing: 0 }, verificationEvidenceSha256: digest("5"),
  });
  await appendNext(run, value.stateRoot, verified.checkpoint, "completed", "a00000000004");
}

test("restartable goal runtime drives dependency-ordered children to independently evidenced completion", async (t) => {
  const value = await fixture(t);
  let calls = 0;
  const result = await runGoalCycle({ ...value, driveChild: async (context) => { calls += 1; await completeDriver({ ...value, ...context }); } });
  assert.equal(result.action, "terminal");
  assert.equal(result.goalState, "completed");
  assert.equal(result.milestonesCompleted, 2);
  assert.equal(result.replaysChild, false);
  assert.equal(result.grantsExecution, false);
  assert.equal(calls, 2);
  const recovered = await recoverGoalLedger(value);
  assert.deepEqual(recovered.checkpoints.map((checkpoint) => checkpoint.state), ["ready", "running", "ready", "running", "ready", "completed"]);
});

test("continuous activation completes a many-stage goal without any time-based scheduling gap", async (t) => {
  const value = await fixture(t, 16);
  let drives = 0;
  let monotonicReads = 0;
  const receipt = await runGoalContinuousCommand(["--config", "/private/controller.json"], {
    loadConfiguration: async () => ({
      config: { stateRoot: value.stateRoot, controller: { maxTransitions: 1 } },
      goal: value.goal, jobs: value.jobs, policy: value.policy,
    }),
    routerFactory: () => ({
      resolveChildRun: value.resolveChildRun,
      driveChild: async (context) => { drives += 1; await completeDriver({ ...value, ...context }); },
    }),
    cycleRunner: runGoalCycle,
    statusPublisher: async () => {},
    clock: value.clock,
    suffix: value.suffix,
    monotonicNow: () => monotonicReads++,
  });
  assert.equal(receipt.stopReason, "terminal");
  assert.equal(receipt.status, "completed");
  assert.equal(receipt.reconciliations, 33);
  assert.equal(receipt.durableProgressReconciliations, 33);
  assert.equal(drives, 16);
  assert.equal(monotonicReads, 33);
  const recovered = await recoverGoalLedger(value);
  assert.equal(recovered.head.progress.jobsStarted, 16);
  assert.equal(recovered.head.progress.milestonesCompleted, 16);
});

test("runtime crash after durable dispatch resumes the same child without incrementing goal jobs", async (t) => {
  const value = await fixture(t);
  await assert.rejects(runGoalCycle({ ...value, driveChild: async () => { throw new Error("synthetic controller loss"); } }), /synthetic controller loss/);
  let recovered = await recoverGoalLedger(value);
  assert.equal(recovered.head.state, "running");
  assert.equal(recovered.head.progress.jobsStarted, 1);
  const result = await runGoalCycle({ ...value, driveChild: async (context) => completeDriver({ ...value, ...context }) });
  assert.equal(result.goalState, "completed");
  recovered = await recoverGoalLedger(value);
  assert.equal(recovered.head.progress.jobsStarted, 2);
});

test("continuous runtime absorbs a post-commit terminal child error but preserves cleanup-ambiguous rejection", async (t) => {
  const terminal = await fixture(t, 1);
  let terminalDrives = 0;
  const receipt = await runGoalContinuousCommand(["--config", "/private/controller.json"], {
    loadConfiguration: async () => ({
      config: { stateRoot: terminal.stateRoot, controller: { maxTransitions: 1 } },
      goal: terminal.goal, jobs: terminal.jobs, policy: terminal.policy,
    }),
    routerFactory: () => ({
      resolveChildRun: terminal.resolveChildRun,
      driveChild: async (context) => {
        terminalDrives += 1;
        const run = terminal.runs.get(context.job.jobId);
        const running = await appendNext(run, terminal.stateRoot, context.childCheckpoint, "running", "a10000000001", {
          iteration: 1, workerSessionSha256: digest("3"),
        });
        await appendNext(run, terminal.stateRoot, running.checkpoint, "failed", "a10000000002", {
          usage: { runtimeSeconds: 2, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 100, artifactBytes: 0, failures: 1 },
          progress: { noProgressCount: 1, failureFingerprintSha256: digest("9") },
        });
        throw new Error("synthetic error after durable terminal close");
      },
    }),
    cycleRunner: runGoalCycle, statusPublisher: async () => {}, clock: terminal.clock, suffix: terminal.suffix,
    monotonicNow: (() => { let tick = 0; return () => tick++; })(),
  });
  assert.equal(receipt.status, "failed");
  assert.equal(receipt.stopReason, "terminal");
  assert.equal(receipt.finalAction, "terminal");
  assert.equal(terminalDrives, 1);

  const ambiguous = await fixture(t, 1);
  await assert.rejects(runGoalCycle({
    ...ambiguous,
    driveChild: async (context) => {
      const run = ambiguous.runs.get(context.job.jobId);
      const running = await appendNext(run, ambiguous.stateRoot, context.childCheckpoint, "running", "a20000000001", {
        iteration: 1, workerSessionSha256: digest("3"),
      });
      await appendNext(run, ambiguous.stateRoot, running.checkpoint, "cleanup-failed", "a20000000002", {
        usage: { runtimeSeconds: 2, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 100, artifactBytes: 0, failures: 1 },
        progress: { noProgressCount: 1, failureFingerprintSha256: digest("8") },
      });
      throw new Error("synthetic cleanup remains ambiguous");
    },
  }), /synthetic cleanup remains ambiguous/);
  const run = ambiguous.runs.get(ambiguous.jobs[0].jobId);
  assert.equal((await recoverCheckpointLedger({ stateRoot: ambiguous.stateRoot, plan: run.plan, lease: run.lease })).head.state, "cleanup-failed");
});

test("runtime yields when an asynchronous child made no durable transition", async (t) => {
  const value = await fixture(t);
  const result = await runGoalCycle({ ...value, driveChild: async () => {} });
  assert.equal(result.action, "child-in-progress");
  assert.equal(result.childState, "authorized");
  assert.equal((await recoverGoalLedger(value)).head.state, "running");
  const run = value.runs.get(value.jobs[0].jobId);
  assert.equal((await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: run.plan, lease: run.lease })).head.state, "authorized");
});

test("a concurrent pause is surfaced even when the child made no durable transition", async (t) => {
  const value = await fixture(t, 1);
  let drives = 0;
  const result = await runGoalCycle({
    ...value,
    driveChild: async () => {
      drives += 1;
      await pauseGoal({ ...value, now: new Date(baseTime + 10), suffix: "900000000001" });
    },
  });
  assert.equal(result.action, "paused");
  assert.equal(result.goalState, "paused");
  assert.equal(result.childState, "authorized");
  assert.equal(drives, 1);
});

test("a durable pause observed during resolution prevents a new child drive until exact resume", async (t) => {
  const value = await fixture(t, 1);
  await dispatchGoalMilestone({ ...value, now: new Date(baseTime + 3), suffix: "800000000000" });
  let paused = false;
  let drives = 0;
  const stopped = await runGoalCycle({
    ...value,
    resolveChildRun: async ({ job }) => {
      if (!paused) {
        paused = true;
        await pauseGoal({ ...value, now: new Date(baseTime + 4), suffix: "800000000001" });
      }
      return value.runs.get(job.jobId);
    },
    driveChild: async () => { drives += 1; },
  });
  assert.equal(stopped.action, "paused");
  assert.equal(stopped.goalState, "paused");
  assert.equal(drives, 0);
  const stillPaused = await runGoalCycle({ ...value, driveChild: async () => { drives += 1; } });
  assert.equal(stillPaused.action, "paused");
  assert.equal(drives, 0);

  await resumeGoal({ ...value, now: new Date(baseTime + 5), suffix: "800000000002" });
  const completed = await runGoalCycle({
    ...value, driveChild: async (context) => { drives += 1; await completeDriver({ ...value, ...context }); },
  });
  assert.equal(completed.goalState, "completed");
  assert.equal(drives, 1);
});

test("a pause between milestones prevents dispatch until resume without consuming a job", async (t) => {
  const value = await fixture(t, 1);
  await pauseGoal({ ...value, now: new Date(baseTime + 3), suffix: "800000000010" });
  let drives = 0;
  const paused = await runGoalCycle({ ...value, driveChild: async () => { drives += 1; } });
  assert.equal(paused.action, "paused");
  assert.equal(paused.goalState, "paused");
  assert.equal(paused.milestonesCompleted, 0);
  assert.equal(drives, 0);
  assert.equal((await recoverGoalLedger(value)).head.progress.jobsStarted, 0);
  await resumeGoal({ ...value, now: new Date(baseTime + 4), suffix: "800000000011" });
  const completed = await runGoalCycle({
    ...value, driveChild: async (context) => { drives += 1; await completeDriver({ ...value, ...context }); },
  });
  assert.equal(completed.goalState, "completed");
  assert.equal(drives, 1);
  assert.equal((await recoverGoalLedger(value)).head.progress.jobsStarted, 1);
});

test("a child finishing after pause is checkpointed once and reconciled only after resume", async (t) => {
  const value = await fixture(t, 1);
  let drives = 0;
  let announceStarted;
  let releaseDrive;
  const started = new Promise((resolve) => { announceStarted = resolve; });
  const held = new Promise((resolve) => { releaseDrive = resolve; });
  const cycling = runGoalCycle({
    ...value,
    driveChild: async (context) => {
      drives += 1;
      announceStarted();
      await held;
      await completeDriver({ ...value, ...context });
    },
  });
  await started;
  await pauseGoal({ ...value, now: new Date(baseTime + 20), suffix: "800000000020" });
  releaseDrive();
  const pausedResult = await cycling;
  assert.equal(pausedResult.action, "paused");
  assert.equal(pausedResult.goalState, "paused");
  assert.equal(pausedResult.childState, "completed");
  assert.equal(drives, 1);
  let parent = await recoverGoalLedger(value);
  assert.equal(parent.head.progress.jobsStarted, 1);
  assert.equal(parent.head.progress.milestonesCompleted, 0);

  const stillPaused = await runGoalCycle({ ...value, driveChild: async () => { drives += 1; } });
  assert.equal(stillPaused.action, "paused");
  assert.equal(drives, 1);

  await resumeGoal({ ...value, now: new Date(baseTime + 30), suffix: "800000000021" });
  const completed = await runGoalCycle({ ...value, driveChild: async () => { drives += 1; } });
  assert.equal(completed.action, "terminal");
  assert.equal(completed.goalState, "completed");
  assert.equal(drives, 1);
  parent = await recoverGoalLedger(value);
  assert.equal(parent.head.progress.jobsStarted, 1);
  assert.equal(parent.head.progress.milestonesCompleted, 1);
});

test("runtime rejects substituted child bundles and enforces a bounded controller horizon", async (t) => {
  const substituted = await fixture(t);
  await assert.rejects(runGoalCycle({
    ...substituted,
    resolveChildRun: async ({ job }) => {
      const value = structuredClone(substituted.runs.get(job.jobId));
      value.plan.objective = "substituted objective";
      return value;
    },
    driveChild: async () => {},
  }), /bundle/);

  const expandedInput = await fixture(t);
  await assert.rejects(runGoalCycle({
    ...expandedInput,
    resolveChildRun: async ({ job }) => {
      const value = structuredClone(expandedInput.runs.get(job.jobId));
      value.plan.inputs[0].contentSha256 = digest("e");
      value.plan.inputs[0].objectName = `${digest("e")}.tar`;
      value.plan.inputSetSha256 = hash(canonical(value.plan.inputs));
      value.lease.inputSetSha256 = value.plan.inputSetSha256;
      value.lease.planSha256 = hash(canonical(value.plan));
      return value;
    },
    driveChild: async () => {},
  }), /bundle/);

  const bounded = await fixture(t);
  const result = await runGoalCycle({ ...bounded, driveChild: async () => {}, maxControllerTransitions: 1 });
  assert.equal(result.action, "controller-yield");
  assert.equal(result.goalState, "running");
  assert.equal((await recoverGoalLedger(bounded)).head.progress.jobsStarted, 1);
});

test("runtime waits content-free for fresh just-in-time authority before child admission", async (t) => {
  const value = await fixture(t, 1);
  let time = baseTime + 61000;
  let calls = 0;
  const expired = await runGoalCycle({
    ...value, clock: () => new Date(time), driveChild: async () => { calls += 1; },
  });
  assert.equal(expired.action, "child-authority-expired");
  assert.equal(expired.goalState, "running");
  assert.equal(calls, 0);
  await assert.rejects(recoverCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.runs.get(value.jobs[0].jobId).plan, lease: value.runs.get(value.jobs[0].jobId).lease,
  }), /job checkpoint ledger/);

  const refreshed = compileScout(value.jobs[0], value.policy, value.entries, {
    now: new Date(time + 1), suffix: "deafbeef0001",
  });
  value.runs.set(value.jobs[0].jobId, {
    plan: refreshed.plan, lease: refreshed.lease, workspaceSnapshotSha256: digest("1"),
  });
  time += 2;
  const resumed = await runGoalCycle({
    ...value, clock: () => new Date(time++),
    driveChild: async (context) => { calls += 1; await completeDriver({ ...value, ...context }); },
  });
  assert.equal(resumed.goalState, "completed");
  assert.equal(calls, 1);
});

test("runtime never drives a nonterminal admitted child through expired authority", async (t) => {
  const value = await fixture(t);
  let calls = 0;
  let time = baseTime + 3;
  const admitted = await runGoalCycle({
    ...value, clock: () => new Date(time++), driveChild: async () => { calls += 1; },
  });
  assert.equal(admitted.action, "child-in-progress");
  assert.equal(admitted.childState, "authorized");
  assert.equal(calls, 1);

  time = baseTime + 61000;
  const expired = await runGoalCycle({
    ...value, clock: () => new Date(time++), driveChild: async () => { calls += 1; },
  });
  assert.equal(expired.action, "child-authority-expired");
  assert.equal(expired.childState, "authorized");
  assert.equal(calls, 1);
  assert.equal((await recoverGoalLedger(value)).head.state, "running");
});

test("maximum-size goal survives a crash after every durable child transition", async (t) => {
  const value = await fixture(t, 64);
  let childSuffix = 0xb00000000000n;
  let crashes = 0;
  const driveChild = async (context) => {
    const run = value.runs.get(context.job.jobId);
    const suffix = () => (childSuffix++).toString(16).padStart(12, "0");
    if (context.childCheckpoint.state === "authorized") {
      await appendNext(run, value.stateRoot, context.childCheckpoint, "running", suffix(), {
        iteration: 1, workerSessionSha256: digest("3"),
      });
    } else if (context.childCheckpoint.state === "running") {
      await appendNext(run, value.stateRoot, context.childCheckpoint, "verifying", suffix(), {
        usage: { runtimeSeconds: 2, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 100, artifactBytes: 20 },
        artifactManifestSha256: digest("4"),
      });
    } else if (context.childCheckpoint.state === "verifying") {
      await appendNext(run, value.stateRoot, context.childCheckpoint, "verified", suffix(), {
        progress: { criteriaPassing: 1, criteriaFailing: 0 }, verificationEvidenceSha256: digest("5"),
      });
    } else if (context.childCheckpoint.state === "verified") {
      await appendNext(run, value.stateRoot, context.childCheckpoint, "completed", suffix());
    } else {
      throw new Error(`unexpected stress child state ${context.childCheckpoint.state}`);
    }
    crashes += 1;
    throw new Error("synthetic post-commit controller crash");
  };

  let result = null;
  for (let restart = 0; restart < 600; restart += 1) {
    try {
      result = await runGoalCycle({ ...value, driveChild, maxControllerTransitions: 1 });
    } catch (error) {
      assert.match(error.message, /synthetic post-commit controller crash/);
    }
    const recovered = await recoverGoalLedger(value);
    assert.equal(recovered.head.progress.jobsStarted <= 64, true);
    assert.equal(recovered.head.progress.milestonesCompleted <= recovered.head.progress.jobsStarted, true);
    if (recovered.head.state === "completed") break;
  }

  const recovered = await recoverGoalLedger(value);
  assert.equal(recovered.head.state, "completed");
  assert.equal(recovered.head.progress.jobsStarted, 64);
  assert.equal(recovered.head.progress.milestonesCompleted, 64);
  assert.equal(recovered.head.usage.modelRequests, 64);
  assert.equal(crashes, 256);
  assert.equal(result?.goalState, "completed");
  for (const job of value.jobs) {
    const run = value.runs.get(job.jobId);
    const child = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: run.plan, lease: run.lease });
    assert.equal(child.head.state, "completed");
    assert.equal(child.checkpoints.length, 5);
  }
});

test("competing controllers compose with a single-use execution claim", async (t) => {
  const value = await fixture(t);
  const launches = new Set();
  let childSuffix = 0xc00000000000n;
  const driveChild = async (context) => {
    const claimPath = join(value.root, `${context.job.jobId}.execution-claim`);
    try {
      const claim = await open(claimPath, "wx", 0o600);
      await claim.close();
      launches.add(context.job.jobId);
    } catch (error) {
      if (error?.code !== "EEXIST") throw error;
    }
    const run = value.runs.get(context.job.jobId);
    const suffix = () => (childSuffix++).toString(16).padStart(12, "0");
    if (context.childCheckpoint.state === "authorized") {
      await appendNext(run, value.stateRoot, context.childCheckpoint, "running", suffix(), {
        iteration: 1, workerSessionSha256: digest("3"),
      });
    } else if (context.childCheckpoint.state === "running") {
      await appendNext(run, value.stateRoot, context.childCheckpoint, "verifying", suffix(), {
        usage: { runtimeSeconds: 2, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 100, artifactBytes: 20 },
        artifactManifestSha256: digest("4"),
      });
    } else if (context.childCheckpoint.state === "verifying") {
      await appendNext(run, value.stateRoot, context.childCheckpoint, "verified", suffix(), {
        progress: { criteriaPassing: 1, criteriaFailing: 0 }, verificationEvidenceSha256: digest("5"),
      });
    } else if (context.childCheckpoint.state === "verified") {
      await appendNext(run, value.stateRoot, context.childCheckpoint, "completed", suffix());
    }
  };

  for (let wave = 0; wave < 20; wave += 1) {
    const attempts = await Promise.allSettled(Array.from({ length: 24 }, () => runGoalCycle({
      ...value,
      driveChild,
      maxControllerTransitions: 2,
      suffix: () => (childSuffix++).toString(16).padStart(12, "0"),
    })));
    for (const rejected of attempts.filter((attempt) => attempt.status === "rejected")) {
      assert.match(rejected.reason.message, /(?:stale head|already appended|already exists|not ready|observable active child|time did not advance|transition .* is not allowed)/);
    }
    const recovered = await recoverGoalLedger(value);
    assert.equal(recovered.head.progress.jobsStarted <= 2, true);
    assert.equal(new Set(recovered.head.completedMilestones).size, recovered.head.completedMilestones.length);
    if (recovered.head.state === "completed") break;
  }

  const recovered = await recoverGoalLedger(value);
  assert.equal(recovered.head.state, "completed");
  assert.equal(recovered.head.progress.jobsStarted, 2);
  assert.equal(recovered.head.progress.milestonesCompleted, 2);
  assert.deepEqual(launches, new Set(value.jobs.map((job) => job.jobId)));
});
