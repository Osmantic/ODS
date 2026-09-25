import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import { checkpointSha256, recoverCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import { readContinuationLease } from "../deploy/work-controller/continuation.mjs";
import { createGoalBuilderDriver } from "../deploy/work-controller/goal-builder-driver.mjs";
import { runGoalCycle } from "../deploy/work-controller/goal-runtime.mjs";
import { goalSha256, initializeGoalLedger, recoverGoalLedger } from "../deploy/work-controller/goals.mjs";
import { recoverLeaseConsumption } from "../deploy/work-runner/runner-core.mjs";

const digest = (character) => character.repeat(64);
const hash = (value) => createHash("sha256").update(value).digest("hex");
const baseTime = Date.parse("2026-08-10T13:00:00Z");

function budgets() {
  return {
    maxRuntimeSeconds: 600, maxIterations: 2, maxToolCalls: 100, maxConcurrentSubagents: 1,
    maxModelRequests: 10, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 2,
    maxMemoryMiB: 2048, maxDiskBytes: 1073741824, maxArtifactBytes: 16777216,
    maxNetworkBytes: 1048576, maxFailures: 2, noProgressLimit: 1,
  };
}

function fakeCandidate() {
  return {
    durationMilliseconds: 1200,
    patch: { sha256: digest("2"), bytes: 100, changes: 1, value: { privateWorkerText: "must-not-cross-driver" } },
    evidence: { sha256: digest("3"), bytes: 200 },
    proxyReceipt: { modelRequests: 2, inputTokens: 100, outputTokens: 20, networkBytes: 1000 },
  };
}

function fakeVerification(plan, claim, candidate, pass = true) {
  const candidateSha256 = digest("4");
  const checks = [
    {
      id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0], status: "pass",
      candidateSha256, evidenceSha256: digest("5"), changes: 1, files: 1, bytes: 10,
    },
    {
      id: "fixed-test", kind: "command", criterionIndexes: [1], status: pass ? "pass" : "fail",
      candidateSha256, evidenceSha256: digest("6"), runtimeMilliseconds: 12, exitCode: pass ? 0 : 1,
      signal: null, timedOut: false, outputLimitExceeded: false, spawnFailed: false,
      stdoutBytes: 0, stdoutSha256: digest("7"), stderrBytes: 0, stderrSha256: digest("8"),
    },
  ];
  return {
    evidence: {
      schemaVersion: 1, format: "pixel-independent-verification-v1", jobId: claim.jobId, claimId: claim.claimId,
      planSha256: claim.planSha256, patchSha256: candidate.patch.sha256, candidateSha256, status: pass ? "pass" : "fail", checks,
      criteria: [{ index: 0, status: "pass", checkIds: ["patch-boundary"] }, { index: 1, status: pass ? "pass" : "fail", checkIds: ["fixed-test"] }],
      network: "none", workerSelectedChecks: false, externalEffects: false,
      boundary: "Content-free independent verification evidence only. Worker output cannot select checks or grant merge, deployment, publication, external-effect, or policy authority.",
    },
    artifact: { path: "unused", bytes: 300, sha256: digest("9") },
  };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-builder-driver-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const content = Buffer.from("goal Builder input\n");
  const contentSha256 = hash(content);
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "builder",
    objective: "Build the exact durable local capability.",
    acceptanceCriteria: ["The patch boundary is valid", "The fixed verification command passes"], dataClassification: "internal",
    verification: {
      mode: "independent", checks: [
        { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] },
        { id: "fixed-test", kind: "command", criterionIndexes: [1], workingDirectory: "source", argv: ["/opt/node/bin/node", "--test"], timeoutSeconds: 60, maxOutputBytes: 65536 },
      ], immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
      boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
    },
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: content.length, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
      network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false,
      ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: budgets(), outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true;
  policy.runner.prepared = true;
  policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${digest("f")}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true;
  policy.profiles.builder.enabled = true;
  policy.verifier.enabled = true;
  const entries = [{ id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256, bytes: content.length, classification: "internal", mountMode: "read-only" }];
  const compiled = compileBuilder(job, policy, entries, { now: new Date(baseTime), suffix: "123456abcdef" });
  const workspaceSnapshotSha256 = digest("1");
  const prepared = {
    ...compiled, policy,
    bindings: {
      planSha256: compiled.planSha256, leaseSha256: checkpointSha256(compiled.lease),
      policySha256: compiled.policySha256, inputSetSha256: compiled.inputSetSha256,
    },
    workspace: { sha256: workspaceSnapshotSha256 },
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "Complete one durable local Builder milestone.", dataClassification: "internal",
    milestones: [{ milestoneId: "build", jobId: job.jobId, jobSha256: goalSha256(job), profile: "builder", dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: 600, maxModelRequests: 10, maxInputTokens: 10000, maxOutputTokens: 2000, maxNetworkBytes: 1048576, maxArtifactBytes: 16777216, maxFailures: 2 },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  await initializeGoalLedger({ stateRoot, goal, jobs: [job], now: new Date(baseTime + 2), suffix: "000000000001" });
  return { root, stateRoot, job, goal, compiled, prepared, workspaceSnapshotSha256 };
}

test("durable goal drives the existing Builder loop and receives only content-free evidence", async (t) => {
  const value = await fixture(t);
  let driverTick = baseTime + 100;
  let runtimeTick = baseTime + 3;
  let preparedCalls = 0;
  let launches = 0;
  let resolvedCheckpoint = null;
  let capturedContext;
  let driverReceipt;
  const driver = createGoalBuilderDriver({
    stateRoot: value.stateRoot,
    prepare: async ({ mode, context }) => {
      preparedCalls += 1;
      assert.equal(mode, "execute");
      assert.equal(context.job.jobId, value.job.jobId);
      return value.prepared;
    },
    clock: () => new Date(driverTick++),
    suffixes: () => ({
      claim: "100000000001", running: "100000000002", verifying: "100000000003",
      verified: "100000000004", terminal: "100000000005",
    }),
    discard: async () => {},
    lifecycleOptions: { inert: true, capabilityResolver: async ({ mode, checkpoint }) => { assert.equal(mode, "execute"); resolvedCheckpoint = checkpoint; return { fixture: true }; } },
    candidateRunner: async (_prepared, _claim, options) => { launches += 1; assert.deepEqual(options, { inert: true, capability: { fixture: true } }); return fakeCandidate(); },
    verifier: async (_prepared, claim, candidate) => fakeVerification(value.compiled.plan, claim, candidate),
  });
  const result = await runGoalCycle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: [value.job],
    resolveChildRun: async () => ({
      plan: value.compiled.plan, lease: value.compiled.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    }),
    driveChild: async (context) => {
      capturedContext = structuredClone(context);
      driverReceipt = await driver(context);
    },
    clock: () => new Date(runtimeTick++),
    suffix: (() => { let counter = 0x200n; return () => (counter++).toString(16).padStart(12, "0"); })(),
  });
  assert.equal(result.goalState, "completed");
  assert.equal(preparedCalls, 1);
  assert.equal(launches, 1);
  assert.equal(resolvedCheckpoint.state, "running");
  assert.deepEqual(driverReceipt, {
    action: "completed", childState: "completed", childCheckpointSha256: driverReceipt.childCheckpointSha256,
    grantsExecution: false, grantsCompletion: false, containsWorkerOutput: false,
  });
  assert.match(driverReceipt.childCheckpointSha256, /^[a-f0-9]{64}$/u);
  assert.doesNotMatch(JSON.stringify(driverReceipt), /must-not-cross-driver/);
  assert.equal((await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: [value.job] })).head.state, "completed");

  const mutations = [
    (context) => { context.plan.objective = "substituted goal work"; },
    (context) => { context.job.acceptanceCriteria[0] = "worker-selected criterion"; },
    (context) => { context.lease.budgets.maxModelRequests += 1; },
    (context) => { context.workspaceSnapshotSha256 = digest("a"); },
    (context) => { context.childCheckpoint.planSha256 = digest("b"); },
    (context) => { context.childAction.action = "record-completed"; },
    (context) => { context.goalCheckpoint.active.jobSha256 = digest("c"); },
    (context) => { context.extraAuthority = true; },
  ];
  for (const mutate of mutations) {
    const substituted = structuredClone(capturedContext);
    mutate(substituted);
    await assert.rejects(driver(substituted), /shape|contract|differs|action/);
  }
  assert.equal(preparedCalls, 1);
});

test("goal Builder driver rejects non-callable internal boundaries", () => {
  assert.throws(() => createGoalBuilderDriver({
    stateRoot: "/private/state", prepare: async () => ({}), candidateRunner: "worker-selected",
  }), /candidateRunner callback is invalid/);
  assert.throws(() => createGoalBuilderDriver({
    stateRoot: "/private/state", prepare: async () => ({}), lifecycleOptions: [],
  }), /lifecycle options are invalid/);
});

test("durable goal preserves verified-candidate lineage across a Builder continuation", async (t) => {
  const value = await fixture(t);
  let driverTick = baseTime + 100;
  let runtimeTick = baseTime + 3;
  let currentLease = value.compiled.lease;
  let launches = 0;
  const prepareIterations = [];
  const driver = createGoalBuilderDriver({
    stateRoot: value.stateRoot,
    prepare: async ({ mode, context }) => {
      assert.equal(mode, "execute");
      prepareIterations.push(context.lease.iteration);
      return {
        ...value.prepared, lease: context.lease,
        bindings: { ...value.prepared.bindings, leaseSha256: checkpointSha256(context.lease) },
      };
    },
    clock: () => new Date(driverTick++),
    suffixes: ({ context }) => {
      const base = context.lease.iteration === 1 ? 0x300000000000n : 0x400000000000n;
      const suffix = (offset) => (base + BigInt(offset)).toString(16).padStart(12, "0");
      return { claim: suffix(1), running: suffix(2), verifying: suffix(3), verified: suffix(4), terminal: suffix(5), continuation: suffix(6) };
    },
    discard: async () => {},
    candidateRunner: async () => { launches += 1; return fakeCandidate(); },
    verifier: async (prepared, claim, candidate) => fakeVerification(value.compiled.plan, claim, candidate, prepared.lease.iteration === 2),
  });
  const cycle = () => runGoalCycle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: [value.job],
    resolveChildRun: async ({ childCheckpoint, childAction }) => {
      if (childAction?.action === "issue-continuation-lease" && currentLease.iteration === 1) {
        const previousConsumption = await recoverLeaseConsumption(value.stateRoot, currentLease);
        const persisted = await readContinuationLease({
          stateRoot: value.stateRoot, plan: value.compiled.plan, previousLease: currentLease, policy: value.prepared.policy,
          previousConsumption, checkpoint: childCheckpoint, iteration: 2, now: new Date(baseTime + 200),
        });
        currentLease = persisted.lease;
      }
      return { plan: value.compiled.plan, lease: currentLease, workspaceSnapshotSha256: value.workspaceSnapshotSha256 };
    },
    driveChild: driver,
    clock: () => new Date(runtimeTick++),
    suffix: (() => { let counter = 0x500n; return () => (counter++).toString(16).padStart(12, "0"); })(),
  });
  let result = await cycle();
  if (result.action === "child-authority-not-yet-valid") {
    runtimeTick = baseTime + 1000;
    result = await cycle();
  }
  assert.equal(result.goalState, "completed");
  assert.deepEqual(prepareIterations, [1, 2]);
  assert.equal(launches, 2);
  const child = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.compiled.plan, lease: currentLease });
  assert.deepEqual(child.checkpoints.map((checkpoint) => checkpoint.state), [
    "authorized", "running", "verifying", "verified", "running", "verifying", "verified", "completed",
  ]);
  assert.notEqual(child.checkpoints[3].workspaceSnapshotSha256, value.workspaceSnapshotSha256);
  assert.equal(child.head.progress.criteriaPassing, 2);
});
