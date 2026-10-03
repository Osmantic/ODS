import assert from "node:assert/strict";
import { chmod, link, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  buildWorkOperatorStatus, publishWorkOperatorStatus, readWorkOperatorStatus, WorkOperatorStatusError,
} from "../deploy/work-controller/operator-status.mjs";
import { validateWorkOperatorStatus } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T16:00:00Z");

function session(overrides = {}) {
  return {
    jobId: "work-1786377600000-abcdef123456", current: true, mode: "builder", state: "verifying",
    startedAt: new Date(baseTime).toISOString(), updatedAt: new Date(baseTime + 2000).toISOString(),
    progress: { criteriaTotal: 3, criteriaPassing: 2, criteriaFailing: 1, iteration: 2, maxIterations: 5, noProgressCount: 0 },
    usage: { runtimeSeconds: 90, modelRequests: 4, inputTokens: 1200, outputTokens: 240, networkBytes: 0, artifactBytes: 2048, failures: 0 },
    artifacts: { count: 2, totalBytes: 2048, kinds: [{ kind: "test-evidence", count: 1 }, { kind: "patch", count: 1 }] },
    verification: "pending", capability: { state: "not-configured", toolCount: 0, singleUseCalls: true, networkAccess: false, externalEffects: false },
    boundaryState: { state: "within-authority", requestedExpansion: "none" },
    activity: [
      { identity: "verify-1", at: new Date(baseTime + 2000).toISOString(), category: "verification", summaryCode: "verification-started", outcome: "pending" },
      { identity: "artifact-1", at: new Date(baseTime + 1000).toISOString(), category: "workspace", summaryCode: "artifact-recorded", outcome: "succeeded" },
    ],
    ...overrides,
  };
}

function goal(overrides = {}) {
  return {
    state: "running", updatedAt: new Date(baseTime + 2000).toISOString(),
    progress: { milestonesTotal: 4, milestonesCompleted: 1, jobsStarted: 2, failures: 0 },
    usage: { runtimeSeconds: 90, modelRequests: 4, inputTokens: 1200, outputTokens: 240, networkBytes: 0, artifactBytes: 2048, failures: 0 },
    budgets: {
      accounting: "settled-plus-active-observed",
      used: { jobs: 2, runtimeSeconds: 180, modelRequests: 8, inputTokens: 2400, outputTokens: 480, networkBytes: 0, artifactBytes: 4096, failures: 0 },
      limits: { jobs: 4, runtimeSeconds: 1000, modelRequests: 20, inputTokens: 20000, outputTokens: 5000, networkBytes: 1048576, artifactBytes: 131072, failures: 5 },
      remaining: { jobs: 2, runtimeSeconds: 820, modelRequests: 12, inputTokens: 17600, outputTokens: 4520, networkBytes: 1048576, artifactBytes: 126976, failures: 5 },
    },
    continuity: { checkpointSequence: 7, restartSafe: true, completionRequiresIndependentVerification: true, progressModel: "durable-events", watchdogRole: "liveness-only" },
    nextAction: "recover-or-continue-child", ...overrides,
  };
}

function status(overrides = {}) {
  const { goal: goalOverride, sessions: sessionOverride, ...rest } = overrides;
  const projectedGoal = goalOverride ?? goal(), projectedSessions = sessionOverride ?? [session()];
  if (goalOverride === undefined) {
    const current = projectedSessions.filter((value) => value.current);
    const used = { jobs: projectedGoal.progress.jobsStarted, ...projectedGoal.usage };
    if (current.length === 1) for (const field of Object.keys(current[0].usage)) used[field] += current[0].usage[field];
    projectedGoal.budgets.used = used;
    projectedGoal.budgets.remaining = Object.fromEntries(Object.keys(used).map((field) => [field, projectedGoal.budgets.limits[field] - used[field]]));
  }
  return buildWorkOperatorStatus({
    goal: projectedGoal,
    sessions: projectedSessions,
    services: [
      { id: "verifier", state: "busy", observedAt: new Date(baseTime + 2000).toISOString() },
      { id: "controller", state: "ready", observedAt: new Date(baseTime + 2000).toISOString() },
      { id: "worker", state: "ready", observedAt: new Date(baseTime + 2000).toISOString() },
    ],
    controllerState: "busy", secret: Buffer.alloc(32, 19), now: new Date(baseTime + 3000), suffix: "000000000003", ...rest,
  });
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-operator-status-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  return root;
}

test("operator projection is deterministic, useful, and content-free", () => {
  const value = status();
  assert.deepEqual(validateWorkOperatorStatus(value), []);
  assert.equal(value.sessions[0].sessionHandle.startsWith("workdisplay-"), true);
  assert.deepEqual(value.services.map((service) => service.id), ["controller", "worker", "verifier"]);
  assert.deepEqual(value.sessions[0].artifacts.kinds.map((item) => item.kind), ["patch", "test-evidence"]);
  assert.equal(value.goal.progress.milestonesCompleted, 1);
  assert.equal(value.goal.continuity.restartSafe, true);
  assert.equal(value.goal.continuity.progressModel, "durable-events");
  assert.equal(value.goal.continuity.watchdogRole, "liveness-only");
  assert.equal(value.goal.usage.inputTokens, 1200);
  assert.equal(value.goal.budgets.remaining.inputTokens, 17600);
  assert.equal(value.goal.budgets.accounting, "settled-plus-active-observed");
  assert.equal(value.sessions[0].usage.outputTokens, 240);
  assert.equal(value.sessions[0].progress.failureStage, null);
  assert.deepEqual(value.sessions[0].capability, { state: "not-configured", toolCount: 0, singleUseCalls: true, networkAccess: false, externalEffects: false });
  assert.equal(value.sessions[0].controls.browserCanExpandBoundary, false);
  const encoded = JSON.stringify(value);
  for (const forbidden of ["work-1786377600000-abcdef123456", "verify-1", "PRIVATE OBJECTIVE", "artifactPath", "sha256:"]) assert.doesNotMatch(encoded, new RegExp(forbidden));
  assert.equal(value.privacy.toolArguments, false);
});

test("operator status makes cleanup and recovery uncertainty explicit without exposing content", () => {
  for (const state of ["cleanup-failed", "recovery-inconclusive"]) {
    const blocked = session({
      state, verification: "not-started", boundaryState: { state: "blocked", requestedExpansion: "none" },
      usage: { runtimeSeconds: 600, modelRequests: 10, inputTokens: 1200, outputTokens: 240, networkBytes: 0, artifactBytes: 2048, failures: 1 },
      activity: [{
        identity: `${state}-1`, at: new Date(baseTime + 2000).toISOString(), category: "controller",
        summaryCode: state, outcome: "failed",
      }],
    });
    blocked.progress.failureStage = "cleanup";
    const value = status({ sessions: [blocked] });
    assert.deepEqual(validateWorkOperatorStatus(value), []);
    assert.equal(value.sessions[0].state, state);
    assert.equal(value.sessions[0].activity[0].summaryCode, state);
    assert.equal(value.sessions[0].activity[0].outcome, "failed");
    assert.equal(value.sessions[0].progress.failureStage, "cleanup");
    assert.equal(value.sessions[0].controls.browserCanResume, false);
    assert.doesNotMatch(JSON.stringify(value), /cleanup-failed-1|recovery-inconclusive-1/u);
  }
  assert.throws(() => status({ sessions: [session({
    state: "cleanup-failed", verification: "not-started",
    activity: [{
      identity: "misleading-1", at: new Date(baseTime + 2000).toISOString(), category: "controller",
      summaryCode: "work-started", outcome: "succeeded",
    }],
  })] }), /recovery state requires a matching latest failed event/);
});

test("operator status publishes atomically and rejects corruption and linked state", async (t) => {
  const root = await fixture(t), value = status();
  await publishWorkOperatorStatus({ stateRoot: root, status: value });
  assert.deepEqual(await readWorkOperatorStatus({ stateRoot: root }), value);
  const path = join(root, "operator-status", "status.json");
  if (process.platform !== "win32") {
    const linked = join(root, "operator-status", "linked.json"); await link(path, linked);
    await assert.rejects(() => readWorkOperatorStatus({ stateRoot: root }), /private and single-link/);
    await assert.rejects(() => publishWorkOperatorStatus({ stateRoot: root, status: value }), /existing operator status is unsafe/);
    await rm(linked);
  }
  const tampered = JSON.parse(await readFile(path, "utf8")); tampered.sessions[0].controls.browserCanExpandBoundary = true;
  await writeFile(path, `${JSON.stringify(tampered, null, 2)}\n`, { mode: 0o600 });
  await assert.rejects(() => readWorkOperatorStatus({ stateRoot: root }), WorkOperatorStatusError);
});

test("concurrent operator heartbeat replacement converges without partial or abandoned state", async (t) => {
  const root = await fixture(t);
  const values = Array.from({ length: 96 }, (_, index) => status({
    now: new Date(baseTime + 3000 + index), suffix: (0x300000000000n + BigInt(index)).toString(16),
  }));
  await publishWorkOperatorStatus({ stateRoot: root, status: values[0] });
  await Promise.all(Array.from({ length: 12 }, (_, writer) => (async () => {
    for (let offset = writer; offset < values.length; offset += 12) {
      await publishWorkOperatorStatus({ stateRoot: root, status: values[offset] });
    }
  })()));
  const recovered = await readWorkOperatorStatus({ stateRoot: root });
  assert.deepEqual(validateWorkOperatorStatus(recovered), []);
  assert.ok(values.some((value) => value.projectionId === recovered.projectionId));
  assert.deepEqual((await readdir(join(root, "operator-status"))).sort(), ["status.json"]);
});

test("completion, progress, boundary, service, and activity inconsistencies fail closed", () => {
  assert.throws(() => status({ sessions: [session({ state: "completed", verification: "pending" })] }), /completion requires/);
  assert.throws(() => status({ sessions: [session({ state: "completed", verification: "pass" })] }), /completion requires every criterion/);
  const badProgress = session(); badProgress.progress.criteriaPassing = 3;
  assert.throws(() => status({ sessions: [badProgress] }), /progress is incoherent/);
  assert.throws(() => status({ sessions: [session({ boundaryState: { state: "waiting-approval", requestedExpansion: "none" } })] }), /boundary request is incoherent/);
  assert.throws(() => status({ sessions: [session({ capability: { state: "configured", toolCount: 0, singleUseCalls: true, networkAccess: false, externalEffects: false } })] }), /capability summary is incoherent/);
  assert.throws(() => status({ sessions: [session({ capability: { state: "authorized", toolCount: 1, singleUseCalls: true, networkAccess: true, externalEffects: false } })] }), /capability summary is incoherent/);
  assert.throws(() => status({ sessions: [session({ capability: { state: "authorized", toolCount: 1, singleUseCalls: true, networkAccess: false, externalEffects: false } })] }), /active capability differs/);
  const invalidFailureStage = session(); invalidFailureStage.progress.failureStage = "raw-provider-error";
  assert.throws(() => status({ sessions: [invalidFailureStage] }), /failure stage is invalid/);
  assert.throws(() => status({ services: [{ id: "capability-adapter", state: "busy", observedAt: new Date(baseTime + 2000).toISOString() }] }), /unsupported activity/);
  assert.throws(() => status({ services: [{ id: "controller", state: "ready", observedAt: new Date(baseTime).toISOString() }, { id: "controller", state: "ready", observedAt: new Date(baseTime).toISOString() }] }), /service identities/);
  assert.throws(() => status({ sessions: [session(), session({ jobId: "work-1786377600001-abcdef123456" })] }), /current session differs/);
  const reversed = session(); reversed.activity.reverse();
  const projected = status({ sessions: [reversed] });
  assert.ok(projected.sessions[0].activity[0].at > projected.sessions[0].activity[1].at);
  const duplicateActivity = session(); duplicateActivity.activity[1].identity = duplicateActivity.activity[0].identity;
  assert.throws(() => status({ sessions: [duplicateActivity] }), /event identities must be unique/);
  const futureActivity = session(); futureActivity.activity[0].at = new Date(baseTime + 3000).toISOString();
  assert.throws(() => status({ sessions: [futureActivity] }), /outside the session time boundary/);
  assert.throws(() => status({ services: [{ id: "controller", state: "ready", observedAt: new Date(baseTime + 4000).toISOString() }] }), /observation follows snapshot generation/);
  const incompleteCompletion = goal({ state: "completed", nextAction: "terminal" });
  incompleteCompletion.budgets.used = { jobs: incompleteCompletion.progress.jobsStarted, ...incompleteCompletion.usage };
  incompleteCompletion.budgets.remaining = Object.fromEntries(Object.keys(incompleteCompletion.budgets.used).map((field) => [field, incompleteCompletion.budgets.limits[field] - incompleteCompletion.budgets.used[field]]));
  assert.throws(() => status({ goal: incompleteCompletion, sessions: [session({ current: false })] }), /completed goal lacks every milestone/);
  assert.throws(() => status({ goal: goal({ state: "paused" }) }), /action differs from durable goal state/);
  const impossibleProgress = goal({ progress: { milestonesTotal: 4, milestonesCompleted: 2, jobsStarted: 1, failures: 0 } });
  impossibleProgress.budgets.used.jobs = 1; impossibleProgress.budgets.remaining.jobs = 3;
  assert.throws(() => status({ goal: impossibleProgress }), /durable goal progress is incoherent/);
  const badRemaining = goal(); badRemaining.budgets.remaining.inputTokens += 1;
  assert.throws(() => status({ goal: badRemaining }), /remaining budget is incoherent/);
  const behindLedger = goal(); behindLedger.budgets.used.inputTokens = 1199; behindLedger.budgets.remaining.inputTokens = 18801;
  assert.throws(() => status({ goal: behindLedger }), /moved behind its durable ledger/);
  const omittedActiveUsage = goal(); omittedActiveUsage.budgets.used = { jobs: omittedActiveUsage.progress.jobsStarted, ...omittedActiveUsage.usage };
  omittedActiveUsage.budgets.remaining = Object.fromEntries(Object.keys(omittedActiveUsage.budgets.used).map((field) => [field, omittedActiveUsage.budgets.limits[field] - omittedActiveUsage.budgets.used[field]]));
  assert.throws(() => status({ goal: omittedActiveUsage }), /differs from its settled and current session evidence/);
  const failedGoal = goal({ state: "failed", nextAction: "terminal" });
  failedGoal.budgets.used = { jobs: failedGoal.progress.jobsStarted, ...failedGoal.usage };
  failedGoal.budgets.remaining = Object.fromEntries(Object.keys(failedGoal.budgets.used).map((field) => [field, failedGoal.budgets.limits[field] - failedGoal.budgets.used[field]]));
  assert.throws(() => status({ goal: failedGoal, sessions: [session({ current: false })] }), /cannot be presented as healthy/);
  assert.throws(() => status({ controllerState: "busy", goal: goal({ state: "paused", nextAction: "paused" }) }), /busy controller requires a running/);
});
