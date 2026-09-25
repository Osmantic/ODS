import assert from "node:assert/strict";
import { chmod, link, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runGoalCommand } from "../deploy/work-controller/goal-cli.mjs";
import { goalSha256 } from "../deploy/work-controller/goals.mjs";

const digest = (character) => character.repeat(64);

function fixtureValues() {
  const now = Date.now();
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: `work-${String(now - 1000).padStart(13, "0")}-abcdef123456`, createdAt: new Date(now - 1000).toISOString(),
    requester: "pixel", profile: "scout", objective: "Inspect the private fixture without revealing it.",
    acceptanceCriteria: ["A bounded finding report is produced"], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest("a"), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: {
      maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1,
      maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1,
      maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576,
      maxFailures: 1, noProgressLimit: 1,
    },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: `workgoal-${String(now - 500).padStart(13, "0")}-abcdef123456`, createdAt: new Date(now - 500).toISOString(), requester: "pixel",
    objective: "PRIVATE_GOAL_CANARY coordinate one bounded local inspection.", dataClassification: "internal",
    milestones: [{ milestoneId: "inspect", jobId: job.jobId, jobSha256: goalSha256(job), profile: "scout", dependsOn: [] }],
    budgets: {
      maxJobs: 1, maxRuntimeSeconds: 60, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000,
      maxNetworkBytes: 1048576, maxArtifactBytes: 65536, maxFailures: 1,
    },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  return { goal, jobs: [job] };
}

async function files(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-cli-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const state = join(root, "state");
  const goalPath = join(root, "goal.json");
  const jobsPath = join(root, "jobs.json");
  await mkdir(state, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(state, 0o700);
  const value = fixtureValues();
  await writeFile(goalPath, `${JSON.stringify(value.goal)}\n`, { mode: 0o600 });
  await writeFile(jobsPath, `${JSON.stringify(value.jobs)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await Promise.all([chmod(goalPath, 0o600), chmod(jobsPath, 0o600)]);
  const common = ["--state", state, "--goal", goalPath, "--jobs", jobsPath];
  return { root, state, goalPath, jobsPath, common, ...value };
}

test("goal CLI initializes, reports, and dispatches without leaking private objective text or granting execution", async (t) => {
  const value = await files(t);
  const initialized = await runGoalCommand(["init", ...value.common]);
  assert.equal(initialized.status, "ready");
  assert.equal(initialized.nextAction, "dispatch-child");
  assert.deepEqual(Object.values(initialized.authority), [false, false, false, false, false]);
  assert.doesNotMatch(JSON.stringify(initialized), /PRIVATE_GOAL_CANARY|private fixture/i);
  const status = await runGoalCommand(["status", ...value.common]);
  assert.equal(status.checkpointSha256, initialized.checkpointSha256);
  const dispatched = await runGoalCommand(["dispatch", ...value.common]);
  assert.equal(dispatched.status, "running");
  assert.equal(dispatched.progress.jobsStarted, 1);
  assert.equal(dispatched.nextAction, "recover-or-start-child");
  const recovered = await runGoalCommand(["status", ...value.common]);
  assert.equal(recovered.status, "running");
  assert.equal(recovered.nextAction, "recover-or-continue-child");
  const paused = await runGoalCommand(["pause", ...value.common]);
  assert.equal(paused.status, "paused");
  assert.equal(paused.schedulingEffect, "pause");
  await assert.rejects(runGoalCommand(["resume", ...value.common]), /exact goal SHA-256/);
  await assert.rejects(runGoalCommand(["resume", ...value.common, "--confirm-goal-sha256", digest("e")]), /differs/);
  const resumed = await runGoalCommand(["resume", ...value.common, "--confirm-goal-sha256", goalSha256(value.goal)]);
  assert.equal(resumed.status, "running");
  assert.equal(resumed.schedulingEffect, "resume");
  await assert.rejects(runGoalCommand(["dispatch", ...value.common]), /not ready/);
});

test("goal CLI cancellation is exact-confirmed and refuses to orphan an active child", async (t) => {
  const ready = await files(t);
  await runGoalCommand(["init", ...ready.common]);
  await assert.rejects(runGoalCommand(["cancel", ...ready.common]), /exact goal SHA-256/);
  const cancelled = await runGoalCommand(["cancel", ...ready.common, "--confirm-goal-sha256", goalSha256(ready.goal)]);
  assert.equal(cancelled.status, "cancelled");
  assert.equal(cancelled.nextAction, "terminal");
  assert.equal(cancelled.schedulingEffect, "cancel");

  const active = await files(t);
  await runGoalCommand(["init", ...active.common]);
  await runGoalCommand(["dispatch", ...active.common]);
  await assert.rejects(runGoalCommand(["cancel", ...active.common, "--confirm-goal-sha256", goalSha256(active.goal)]), /supervised child cleanup/);
});

test("goal CLI can pause safely between milestones and resumes without inventing a child", async (t) => {
  const value = await files(t);
  await runGoalCommand(["init", ...value.common]);
  const paused = await runGoalCommand(["pause", ...value.common]);
  assert.equal(paused.status, "paused");
  assert.equal(paused.progress.jobsStarted, 0);
  assert.equal(paused.nextAction, "paused");
  const status = await runGoalCommand(["status", ...value.common]);
  assert.equal(status.checkpointSha256, paused.checkpointSha256);
  const resumed = await runGoalCommand(["resume", ...value.common, "--confirm-goal-sha256", goalSha256(value.goal)]);
  assert.equal(resumed.status, "ready");
  assert.equal(resumed.progress.jobsStarted, 0);
  assert.equal(resumed.nextAction, "dispatch-child");
});

test("goal CLI rejects unknown, duplicate, missing, linked, and non-private inputs", async (t) => {
  const value = await files(t);
  await assert.rejects(runGoalCommand(["status", ...value.common, "--unknown", "x"]), /invalid/);
  await assert.rejects(runGoalCommand(["status", ...value.common, "--goal", value.goalPath]), /duplicated/);
  await assert.rejects(runGoalCommand(["observe", ...value.common]), /requires an exact child plan/);
  const linkPath = join(value.root, "goal-link.json");
  await link(value.goalPath, linkPath);
  const linked = value.common.map((entry) => entry === value.goalPath ? linkPath : entry);
  await assert.rejects(runGoalCommand(["status", ...linked]));
  if (process.platform !== "win32") {
    await chmod(value.goalPath, 0o644);
    await assert.rejects(runGoalCommand(["status", ...value.common]), /owner-private/);
  }
});
