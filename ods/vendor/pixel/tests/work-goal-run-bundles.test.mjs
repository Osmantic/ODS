import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import { executeBuilderIteration } from "../deploy/work-controller/builder-loop.mjs";
import { appendCheckpoint, checkpointSha256, initializeCheckpointLedger, recoverCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import { createGoalBuilderDriver } from "../deploy/work-controller/goal-builder-driver.mjs";
import { createGoalBuilderPreparer } from "../deploy/work-controller/goal-builder-preparer.mjs";
import { createGoalBuilderResolver } from "../deploy/work-controller/goal-builder-resolver.mjs";
import { runGoalCancelCommand } from "../deploy/work-controller/goal-cancel-cli.mjs";
import { runGoalCycleCommand } from "../deploy/work-controller/goal-cycle-cli.mjs";
import { runGoalCleanupCommand } from "../deploy/work-controller/goal-cleanup-cli.mjs";
import { runGoalCycle } from "../deploy/work-controller/goal-runtime.mjs";
import { publishGoalOperatorStatus } from "../deploy/work-controller/goal-operator-status.mjs";
import { renderGoalServiceBundle } from "../deploy/work-controller/goal-service-cli.mjs";
import { inspectGoalServiceBundle, verifyGoalServiceLiveBinding } from "../deploy/work-controller/goal-service-lifecycle.mjs";
import { readWorkOperatorStatus } from "../deploy/work-controller/operator-status.mjs";
import {
  admitGoalRunBundle, appendContinuationGoalRunBundle, initializeGoalRunBundle, recoverGoalRunBundles,
  refreshExpiredGoalRunBundle, refreshGoalRunBundle, resolveGoalRunBundle,
} from "../deploy/work-controller/goal-run-bundles.mjs";
import {
  cancelGoalAfterLeaseRevocation, dispatchGoalMilestone, goalSha256, initializeGoalLedger, pauseGoal, recoverGoalLedger,
} from "../deploy/work-controller/goals.mjs";
import {
  claimLease, createLeaseConsumption, createLeaseRevocation, inspectLeaseDisposition, revokeLease,
} from "../deploy/work-runner/runner-core.mjs";
import { canonical, validateWorkGoalRunBundle } from "../scripts/lib/work-contract.mjs";

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

function candidate() {
  return {
    durationMilliseconds: 1200,
    patch: { sha256: digest("2"), bytes: 100, changes: 1, value: {} }, evidence: { sha256: digest("3"), bytes: 200 },
    proxyReceipt: { modelRequests: 2, inputTokens: 100, outputTokens: 20, networkBytes: 1000 },
  };
}

function partialVerification(plan, claim, work) {
  const candidateSha256 = digest("4");
  return {
    evidence: {
      schemaVersion: 1, format: "pixel-independent-verification-v1", jobId: claim.jobId, claimId: claim.claimId,
      planSha256: claim.planSha256, patchSha256: work.patch.sha256, candidateSha256, status: "fail",
      checks: [
        { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0], status: "pass", candidateSha256, evidenceSha256: digest("5"), changes: 1, files: 1, bytes: 10 },
        { id: "fixed-test", kind: "command", criterionIndexes: [1], status: "fail", candidateSha256, evidenceSha256: digest("6"), runtimeMilliseconds: 12, exitCode: 1, signal: null, timedOut: false, outputLimitExceeded: false, spawnFailed: false, stdoutBytes: 0, stdoutSha256: digest("7"), stderrBytes: 0, stderrSha256: digest("8") },
      ],
      criteria: [{ index: 0, status: "pass", checkIds: ["patch-boundary"] }, { index: 1, status: "fail", checkIds: ["fixed-test"] }],
      network: "none", workerSelectedChecks: false, externalEffects: false,
      boundary: "Content-free independent verification evidence only. Worker output cannot select checks or grant merge, deployment, publication, external-effect, or policy authority.",
    },
    artifact: { path: "unused", bytes: 300, sha256: digest("9") },
  };
}

function passingVerification(plan, claim, work) {
  const result = partialVerification(plan, claim, work);
  result.evidence.status = "pass";
  result.evidence.checks[1].status = "pass";
  result.evidence.checks[1].exitCode = 0;
  result.evidence.criteria[1].status = "pass";
  return result;
}

async function fixture(t) {
  // The fixture installs rendered goal services into a root under ProtectHome, so the temp
  // root must never live under /home (a host with TMPDIR under /home would otherwise trip the
  // ProtectHome guard for an environment reason). Fall back to /tmp to keep the gate green.
  const forbiddenPrivateRoots = ["/home", "/root", "/run/user"];
  const base = forbiddenPrivateRoots.some((root) => tmpdir() === root || tmpdir().startsWith(`${root}/`)) ? "/tmp" : tmpdir();
  const root = await mkdtemp(join(base, "pixel-goal-run-bundles-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const content = Buffer.from("durable run bundle input\n");
  const contentSha256 = hash(content);
  const objectStore = join(root, "objects");
  await mkdir(objectStore, { mode: 0o700 });
  await writeFile(join(objectStore, `${contentSha256}.tar`), content, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(objectStore, 0o700);
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "builder",
    objective: "Build the restartable exact capability.", acceptanceCriteria: ["The patch boundary is valid", "The fixed verification command passes"], dataClassification: "internal",
    verification: {
      mode: "independent", checks: [
        { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] },
        { id: "fixed-test", kind: "command", criterionIndexes: [1], workingDirectory: "source", argv: ["/opt/node/bin/node", "--test"], timeoutSeconds: 60, maxOutputBytes: 65536 },
      ], immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
      boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
    },
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: content.length, classification: "internal" }],
    requestedCapabilities: { filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"], network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false },
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
    ...compiled, policy, bindings: { planSha256: compiled.planSha256, leaseSha256: checkpointSha256(compiled.lease), policySha256: compiled.policySha256, inputSetSha256: compiled.inputSetSha256 },
    workspace: { sha256: workspaceSnapshotSha256 },
  };
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "Complete one restartable exact Builder milestone.", dataClassification: "internal",
    milestones: [{ milestoneId: "build", jobId: job.jobId, jobSha256: goalSha256(job), profile: "builder", dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: 600, maxModelRequests: 10, maxInputTokens: 10000, maxOutputTokens: 2000, maxNetworkBytes: 1048576, maxArtifactBytes: 16777216, maxFailures: 2 },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  return { root, stateRoot, objectStore, job, jobs: [job], goal, policy, entries, compiled, prepared, workspaceSnapshotSha256 };
}

async function initialize(value, suffix = "000000000001") {
  return initializeGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    plan: value.compiled.plan, lease: value.compiled.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(baseTime + 1), suffix,
  });
}

async function controllerConfiguration(value, name) {
  const paths = {
    goalPath: join(value.root, `${name}-goal.json`), jobsPath: join(value.root, `${name}-jobs.json`),
    policyPath: join(value.root, `${name}-policy.json`), configPath: join(value.root, `${name}-controller.json`),
  };
  await writeFile(paths.goalPath, `${JSON.stringify(value.goal)}\n`, { mode: 0o600 });
  await writeFile(paths.jobsPath, `${JSON.stringify(value.jobs)}\n`, { mode: 0o600 });
  await writeFile(paths.policyPath, `${JSON.stringify(value.policy)}\n`, { mode: 0o600 });
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-v1.schema.json", schemaVersion: 1, enabled: true,
    stateRoot: value.stateRoot, goalPath: paths.goalPath, jobsPath: paths.jobsPath, policyPath: paths.policyPath,
    objectStore: join(value.root, "objects"), workspaceRoot: join(value.root, "workspaces"), executorPath: join(value.root, "omp"),
    archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
    runtime: {
      dockerPath: join(value.root, "docker"), backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-model",
      networkSubnet: "172.29.1.0/29", workerIp: "172.29.1.2", proxyIp: "172.29.1.3", uid: 1000, gid: 1000,
    },
    controller: { maxTransitions: 1 },
  };
  await writeFile(paths.configPath, `${JSON.stringify(config)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await Promise.all(Object.values(paths).map((path) => chmod(path, 0o600)));
  return { paths, config };
}

test("goal run bundle initialization is atomic, private, exact, and contention-safe", async (t) => {
  const value = await fixture(t);
  const attempts = await Promise.allSettled(Array.from({ length: 32 }, (_, index) => initialize(value, index.toString(16).padStart(12, "0"))));
  assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 1);
  const recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.equal(recovered.bundles.length, 1);
  assert.deepEqual(validateWorkGoalRunBundle(recovered.head), []);
  assert.equal(recovered.head.authority.containsExactLease, true);
  assert.equal(recovered.head.authority.grantsBeyondEmbeddedLease, false);
  assert.deepEqual(await resolveGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    now: new Date(baseTime + 2), suffix: "00000000000b",
  }), {
    plan: value.compiled.plan, lease: value.compiled.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
  });
  assert.deepEqual((await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId })).bundles.map((bundle) => bundle.purpose), ["initial", "admission"]);
  assert.deepEqual(await readdir(join(value.stateRoot, "goal-runs", value.goal.goalId)), [value.job.jobId]);
});

test("a fresh controller process resolves custody and completes the recorded Builder child", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "010000000001" });
  let runtimeTick = baseTime + 3;
  const dispatchOnly = await runGoalCycle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs,
    resolveChildRun: async () => { throw new Error("dispatch-only cycle must not resolve a run bundle"); },
    driveChild: async () => { throw new Error("dispatch-only cycle must not drive a child"); },
    maxControllerTransitions: 1, clock: () => new Date(runtimeTick++), suffix: () => "010000000002",
  });
  assert.equal(dispatchOnly.action, "controller-yield");
  assert.equal((await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs })).head.state, "running");

  let driverTick = baseTime + 100;
  let launches = 0;
  const restartedDriver = createGoalBuilderDriver({
    stateRoot: value.stateRoot,
    prepare: async () => value.prepared,
    clock: () => new Date(driverTick++),
    suffixes: () => ({ claim: "010000000003", running: "010000000004", verifying: "010000000005", verified: "010000000006", terminal: "010000000007" }),
    discard: async () => {},
    candidateRunner: async () => { launches += 1; return candidate(); },
    verifier: async (_prepared, claim, work) => passingVerification(value.compiled.plan, claim, work),
  });
  let resolverTick = baseTime + 40;
  let resolverSuffix = 0x110000000000n;
  const restartedResolver = createGoalBuilderResolver({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, policy: value.policy, objectStore: value.objectStore,
    clock: () => new Date(resolverTick++), suffix: () => (resolverSuffix++).toString(16),
  });
  runtimeTick = baseTime + 50;
  const result = await runGoalCycle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs,
    resolveChildRun: restartedResolver,
    driveChild: restartedDriver,
    clock: () => new Date(runtimeTick++),
    suffix: (() => { let counter = 0x100000000008n; return () => (counter++).toString(16); })(),
  });
  assert.equal(result.goalState, "completed");
  assert.equal(launches, 1);
});

test("durable Builder resolver recovers and custodies continuation authority without in-memory lease state", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "120000000000" });
  let driverTick = baseTime + 100;
  let launches = 0;
  const preparations = [];
  const preparer = createGoalBuilderPreparer({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, policy: value.policy,
    objectStore: join(value.root, "objects"), workspaceRoot: join(value.root, "workspaces"),
    executorPath: join(value.root, "omp"), archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
    clock: () => new Date(driverTick),
    builderRun: async (options) => {
      preparations.push(options);
      return {
        ...value.prepared, lease: options.lease,
        bindings: { ...value.prepared.bindings, leaseSha256: checkpointSha256(options.lease) },
      };
    },
  });
  const driver = createGoalBuilderDriver({
    stateRoot: value.stateRoot,
    prepare: preparer,
    clock: () => new Date(driverTick++),
    suffixes: ({ context }) => {
      const base = context.lease.iteration === 1 ? 0x130000000000n : 0x140000000000n;
      const at = (offset) => (base + BigInt(offset)).toString(16);
      return { claim: at(1), running: at(2), verifying: at(3), verified: at(4), terminal: at(5), continuation: at(6) };
    },
    discard: async () => {},
    candidateRunner: async () => { launches += 1; return candidate(); },
    verifier: async (prepared, claim, work) => prepared.lease.iteration === 1
      ? partialVerification(value.compiled.plan, claim, work) : passingVerification(value.compiled.plan, claim, work),
  });
  let resolverTick = baseTime + 300;
  let resolverSuffix = 0x150000000000n;
  const resolver = createGoalBuilderResolver({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, policy: value.policy, objectStore: value.objectStore,
    clock: () => new Date(resolverTick++), suffix: () => (resolverSuffix++).toString(16),
  });
  let runtimeTick = baseTime + 300;
  let runtimeSuffix = 0x160000000000n;
  const cycle = () => runGoalCycle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs,
    resolveChildRun: resolver, driveChild: driver,
    clock: () => new Date(runtimeTick++),
    suffix: () => (runtimeSuffix++).toString(16),
  });
  let result = await cycle();
  assert.equal(result.action, "child-authority-not-yet-valid");
  runtimeTick = baseTime + 1000;
  driverTick = baseTime + 1000;
  result = await cycle();
  assert.equal(result.goalState, "completed");
  assert.equal(launches, 2);
  assert.equal(preparations.length, 2);
  assert.equal(preparations[0].continuation, undefined);
  assert.equal(preparations[1].continuation.previousLease.iteration, 1);
  assert.equal(preparations[1].continuation.previousConsumption.leaseId, preparations[1].continuation.previousLease.leaseId);
  assert.equal(preparations[1].continuation.checkpoint.iteration, 1);
  assert.equal(preparations[1].continuation.patchPath, join(
    value.stateRoot, "results", preparations[1].continuation.previousConsumption.claimId, "builder-patch.json",
  ));
  const recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.deepEqual(recovered.bundles.map((bundle) => bundle.purpose), ["initial", "admission", "continuation"]);
  assert.equal(recovered.head.lease.iteration, 2);
});

test("durable Builder preparer reconstructs cleanup and verification recovery only from custody", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "170000000001" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "170000000002" });
  await resolveGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    now: new Date(baseTime + 4), suffix: "170000000003",
  });
  const initialized = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease,
    workspaceSnapshotSha256: value.workspaceSnapshotSha256, now: new Date(baseTime + 5), suffix: "170000000004",
  });
  const consumption = createLeaseConsumption(value.prepared, { now: new Date(baseTime + 6), suffix: "170000000005" });
  await claimLease(value.stateRoot, consumption);
  const update = (head, state, overrides = {}) => ({
    state, iteration: overrides.iteration ?? head.iteration,
    usage: { ...head.usage, ...(overrides.usage ?? {}) }, progress: { ...head.progress, ...(overrides.progress ?? {}) },
    workspaceSnapshotSha256: overrides.workspaceSnapshotSha256 ?? head.workspaceSnapshotSha256,
    artifactManifestSha256: Object.hasOwn(overrides, "artifactManifestSha256") ? overrides.artifactManifestSha256 : head.artifactManifestSha256,
    workerSessionSha256: Object.hasOwn(overrides, "workerSessionSha256") ? overrides.workerSessionSha256 : head.workerSessionSha256,
    verificationEvidenceSha256: Object.hasOwn(overrides, "verificationEvidenceSha256") ? overrides.verificationEvidenceSha256 : head.verificationEvidenceSha256,
    authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false,
  });
  const append = (head, state, tick, recordSuffix, overrides) => appendCheckpoint({
    stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease,
    previousCheckpointSha256: checkpointSha256(head), update: update(head, state, overrides),
    now: new Date(baseTime + tick), suffix: recordSuffix,
  });
  const running = await append(initialized.checkpoint, "running", 7, "170000000006", {
    iteration: 1, workerSessionSha256: checkpointSha256(consumption),
  });
  const calls = [];
  const preparer = createGoalBuilderPreparer({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, policy: value.policy,
    objectStore: join(value.root, "objects"), workspaceRoot: join(value.root, "workspaces"),
    executorPath: join(value.root, "omp"), archiveLimits: { maxEntries: 100, maxFileBytes: 1024 * 1024 },
    clock: () => new Date(baseTime + 8),
    cleanupRecovery: async (options) => { calls.push({ kind: "cleanup", options }); return { kind: "cleanup" }; },
    verificationRecovery: async (options) => { calls.push({ kind: "verify", options }); return { kind: "verify" }; },
  });
  const goalCheckpoint = (await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs })).head;
  let child = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease });
  const context = {
    job: value.job, plan: value.compiled.plan, lease: value.compiled.lease,
    workspaceSnapshotSha256: value.workspaceSnapshotSha256, goalCheckpoint,
    childCheckpoint: child.head, childAction: child.action,
  };
  assert.deepEqual(await preparer({ mode: "cleanup", context }), { kind: "cleanup" });
  assert.deepEqual(calls[0].options.recovery, { consumption, checkpoint: running.checkpoint });
  await assert.rejects(preparer({ mode: "cleanup", context: { ...context, patchPath: "caller-controlled" } }), /context shape/);

  await append(running.checkpoint, "verifying", 9, "170000000007", {
    usage: { runtimeSeconds: 1, modelRequests: 1, inputTokens: 1, outputTokens: 1, networkBytes: 0, artifactBytes: 1 },
    artifactManifestSha256: digest("a"),
  });
  child = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease });
  const verificationContext = { ...context, childCheckpoint: child.head, childAction: child.action };
  assert.deepEqual(await preparer({ mode: "verify", context: verificationContext }), { kind: "verify" });
  assert.deepEqual(calls[1].options.recovery, { consumption, checkpoint: child.head });
  assert.equal(calls[1].options.now.toISOString(), new Date(baseTime + 8).toISOString());
});

test("supervisor stop cleanup removes only the exact interrupted child and closes it durably", async (t) => {
  const value = await fixture(t);
  await initialize(value, "190000000001");
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "190000000002" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "190000000003" });
  await resolveGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    now: new Date(baseTime + 4), suffix: "190000000004",
  });
  const initialized = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease,
    workspaceSnapshotSha256: value.workspaceSnapshotSha256, now: new Date(baseTime + 5), suffix: "190000000005",
  });
  const consumption = createLeaseConsumption(value.prepared, { now: new Date(baseTime + 6), suffix: "190000000006" });
  await claimLease(value.stateRoot, consumption);
  const running = await appendCheckpoint({
    stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease,
    previousCheckpointSha256: checkpointSha256(initialized.checkpoint), now: new Date(baseTime + 7), suffix: "190000000007",
    update: {
      state: "running", iteration: 1, usage: initialized.checkpoint.usage, progress: initialized.checkpoint.progress,
      workspaceSnapshotSha256: initialized.checkpoint.workspaceSnapshotSha256, artifactManifestSha256: null,
      workerSessionSha256: checkpointSha256(consumption), verificationEvidenceSha256: null,
      authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false,
    },
  });
  const { paths } = await controllerConfiguration(value, "cleanup");
  let cleanups = 0;
  const cleaned = await runGoalCleanupCommand(["--config", paths.configPath], {
    cleanup: async (prepared, claim, lifecycle) => {
      cleanups += 1;
      assert.equal(prepared.cleanupOnly, true);
      assert.deepEqual(claim, consumption);
      assert.equal(lifecycle.stateRoot, value.stateRoot);
      return {
        resourcesRemoved: true,
        usage: { runtimeSeconds: 1, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 1 },
      };
    },
  });
  assert.equal(cleaned.action, "interrupted-child-cleaned");
  assert.equal(cleaned.childState, "failed");
  assert.equal(cleanups, 1);
  assert.equal((await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease })).head.state, "failed");
  const repeated = await runGoalCleanupCommand(["--config", paths.configPath], { cleanup: async () => { throw new Error("must not replay cleanup"); } });
  assert.equal(repeated.action, "cleanup-not-required");
  assert.equal(cleanups, 1);
  await assert.rejects(runGoalCancelCommand([
    "--config", paths.configPath, "--confirm-goal-sha256", digest("e"),
  ]), /confirmation differs/);
  const cancelReview = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  assert.equal(cancelReview.action, "confirmation-required");
  assert.equal(cancelReview.cancellation.mode, "supervised-child-after-cleanup");
  const cancelled = await runGoalCancelCommand([
    "apply", "--config", paths.configPath, "--confirm-review-sha256", cancelReview.confirmation.sha256,
  ]);
  assert.equal(cancelled.status, "cancelled");
  assert.equal(cancelled.action, "cancelled-after-cleanup");
  const already = await runGoalCancelCommand([
    "--config", paths.configPath, "--confirm-goal-sha256", goalSha256(value.goal),
  ]);
  assert.equal(already.action, "already-cancelled");
  const reconciled = await runGoalCycleCommand(["--config", paths.configPath]);
  assert.equal(reconciled.status, "cancelled");
  assert.equal(reconciled.action, "terminal");
  assert.doesNotMatch(JSON.stringify(cleaned), /pixel-model|\\Users\\|\/tmp\//u);
  assert.notEqual(checkpointSha256(running.checkpoint), cleaned.childCheckpointSha256);
});

test("checkpoint-bound cancellation revokes an unlaunched child and closes both ledgers without execution", async (t) => {
  const value = await fixture(t);
  await initialize(value, "1a0000000001");
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "1a0000000002" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "1a0000000003" });
  const { paths } = await controllerConfiguration(value, "cancel-unlaunched");
  const review = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  assert.equal(review.action, "confirmation-required");
  assert.equal(review.cancellation.mode, "unlaunched-child-revocation");
  assert.equal(review.cancellation.childState, null);
  await assert.rejects(runGoalCancelCommand([
    "--config", paths.configPath, "--confirm-goal-sha256", goalSha256(value.goal),
  ]), /requires checkpoint-bound review\/apply/);
  const cancelled = await runGoalCancelCommand([
    "apply", "--config", paths.configPath, "--confirm-review-sha256", review.confirmation.sha256,
  ], {
    now: new Date(baseTime + 10), suffix: "1a0000000010", revocationSuffix: "1a0000000011",
    admissionSuffix: "1a0000000012", childInitializeSuffix: "1a0000000013", childCancelSuffix: "1a0000000014",
    operatorNow: new Date(baseTime + 20), operatorSuffix: "1a0000000015", secret: Buffer.alloc(32, 31),
  });
  assert.equal(cancelled.action, "cancelled-before-launch");
  assert.equal(cancelled.cancellationMode, "unlaunched-child-revocation");
  const run = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.equal(run.head.purpose, "admission");
  const disposition = await inspectLeaseDisposition(value.stateRoot, run.head.lease);
  assert.equal(disposition.kind, "revoked");
  const child = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: run.head.plan, lease: run.head.lease });
  assert.deepEqual(child.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "cancelled"]);
  assert.equal(child.head.iteration, 0);
  assert.ok(Object.values(child.head.usage).every((amount) => amount === 0));
  const parent = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs });
  assert.equal(parent.head.state, "cancelled");
  assert.equal(parent.head.observation.childState, "cancelled");
  await assert.rejects(claimLease(value.stateRoot, createLeaseConsumption(value.prepared, {
    now: new Date(baseTime + 11), suffix: "1a0000000016",
  })), /already consumed or revoked/);
  const terminal = await runGoalCycle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs,
    resolveChildRun: async () => { throw new Error("cancelled goal must not resolve"); },
    driveChild: async () => { throw new Error("cancelled goal must not drive"); },
  });
  assert.equal(terminal.action, "terminal");
});

test("unlaunched cancellation recovers after a crash immediately following lease revocation", async (t) => {
  const value = await fixture(t);
  await initialize(value, "1b0000000001");
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "1b0000000002" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "1b0000000003" });
  const { paths } = await controllerConfiguration(value, "cancel-revocation-crash");
  const review = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  const parent = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs });
  const run = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  const revocation = createLeaseRevocation({
    plan: run.head.plan, lease: run.head.lease, goalId: value.goal.goalId,
    goalCheckpointSha256: parent.headSha256, reviewedChildCheckpointSha256: null,
    cancellationReviewSha256: review.confirmation.sha256,
  }, { now: new Date(baseTime + 10), suffix: "1b0000000010" });
  await revokeLease(value.stateRoot, revocation);

  const recovered = await runGoalCancelCommand([
    "apply", "--config", paths.configPath, "--confirm-review-sha256", review.confirmation.sha256,
  ], {
    now: new Date(baseTime + 11), suffix: "1b0000000011", revocationSuffix: "1b0000000012",
    admissionSuffix: "1b0000000013", childInitializeSuffix: "1b0000000014", childCancelSuffix: "1b0000000015",
  });
  assert.equal(recovered.action, "cancelled-before-launch");
  assert.equal((await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs })).head.state, "cancelled");
});

test("a pause after revocation can be freshly reviewed, completed, and replay-recovered", async (t) => {
  const value = await fixture(t);
  await initialize(value, "1b1000000001");
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "1b1000000002" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "1b1000000003" });
  const { paths } = await controllerConfiguration(value, "cancel-revocation-pause");
  const firstReview = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  const parent = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs });
  const run = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  await revokeLease(value.stateRoot, createLeaseRevocation({
    plan: run.head.plan, lease: run.head.lease, goalId: value.goal.goalId,
    goalCheckpointSha256: parent.headSha256, reviewedChildCheckpointSha256: null,
    cancellationReviewSha256: firstReview.confirmation.sha256,
  }, { now: new Date(baseTime + 10), suffix: "1b1000000010" }));
  await pauseGoal({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 11), suffix: "1b1000000011" });
  const pausedReview = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  assert.equal(pausedReview.status, "paused");
  assert.notEqual(pausedReview.confirmation.sha256, firstReview.confirmation.sha256);
  const argv = ["apply", "--config", paths.configPath, "--confirm-review-sha256", pausedReview.confirmation.sha256];
  const cancelled = await runGoalCancelCommand(argv, {
    now: new Date(baseTime + 12), suffix: "1b1000000012", revocationSuffix: "1b1000000013",
    admissionSuffix: "1b1000000014", childInitializeSuffix: "1b1000000015", childCancelSuffix: "1b1000000016",
  });
  assert.equal(cancelled.action, "cancelled-before-launch");
  const repeated = await runGoalCancelCommand(argv, { operatorNow: new Date(baseTime + 20), operatorSuffix: "1b1000000017" });
  assert.equal(repeated.action, "already-cancelled");
  const final = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs });
  assert.deepEqual(final.checkpoints.map((checkpoint) => checkpoint.state), ["ready", "running", "paused", "cancelled"]);
});

test("concurrent pre-launch cancellation applies converge on one revocation, child, and parent", async (t) => {
  const value = await fixture(t);
  await initialize(value, "1c0000000001");
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "1c0000000002" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "1c0000000003" });
  const { paths } = await controllerConfiguration(value, "cancel-unlaunched-race");
  const review = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  const argv = ["apply", "--config", paths.configPath, "--confirm-review-sha256", review.confirmation.sha256];
  const results = await Promise.all(Array.from({ length: 16 }, (_, index) => runGoalCancelCommand(argv, {
    now: new Date(baseTime + 10 + index), suffix: (0x1c0000000010n + BigInt(index)).toString(16),
    revocationSuffix: (0x1c0000000110n + BigInt(index)).toString(16),
    admissionSuffix: (0x1c0000000210n + BigInt(index)).toString(16),
    childInitializeSuffix: (0x1c0000000310n + BigInt(index)).toString(16),
    childCancelSuffix: (0x1c0000000410n + BigInt(index)).toString(16),
    operatorNow: new Date(baseTime + 100 + index), operatorSuffix: (0x1c0000000510n + BigInt(index)).toString(16),
  })));
  assert.equal(results.filter((result) => result.action === "cancelled-before-launch").length, 1);
  assert.equal(results.filter((result) => result.action === "already-cancelled").length, 15);
  const run = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  const child = await recoverCheckpointLedger({ stateRoot: value.stateRoot, plan: run.head.plan, lease: run.head.lease });
  assert.equal((await inspectLeaseDisposition(value.stateRoot, run.head.lease)).kind, "revoked");
  assert.deepEqual(child.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "cancelled"]);
  assert.deepEqual((await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs })).checkpoints.map((checkpoint) => checkpoint.state), ["ready", "running", "cancelled"]);
});

test("lease consumption versus reviewed cancellation has one safe winner", async (t) => {
  const value = await fixture(t);
  await initialize(value, "1d0000000001");
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "1d0000000002" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "1d0000000003" });
  const { paths } = await controllerConfiguration(value, "cancel-consume-race");
  const review = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  const claim = createLeaseConsumption(value.prepared, { now: new Date(baseTime + 10), suffix: "1d0000000010" });
  const cancellationArgv = ["apply", "--config", paths.configPath, "--confirm-review-sha256", review.confirmation.sha256];
  const contenders = [claimLease(value.stateRoot, claim), ...Array.from({ length: 16 }, (_, index) => runGoalCancelCommand(cancellationArgv, {
    now: new Date(baseTime + 11 + index), suffix: (0x1d0000000110n + BigInt(index)).toString(16),
    revocationSuffix: (0x1d0000000210n + BigInt(index)).toString(16),
    admissionSuffix: (0x1d0000000310n + BigInt(index)).toString(16),
    childInitializeSuffix: (0x1d0000000410n + BigInt(index)).toString(16),
    childCancelSuffix: (0x1d0000000510n + BigInt(index)).toString(16),
    operatorNow: new Date(baseTime + 100 + index), operatorSuffix: (0x1d0000000610n + BigInt(index)).toString(16),
  }))];
  const settled = await Promise.allSettled(contenders);
  const run = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  const disposition = await inspectLeaseDisposition(value.stateRoot, run.head.lease);
  const parent = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs });
  if (disposition.kind === "consumed") {
    assert.equal(settled[0].status, "fulfilled");
    assert.ok(settled.slice(1).every((result) => result.status === "rejected"));
    assert.equal(parent.head.state, "running");
    const blocked = await runGoalCancelCommand(["review", "--config", paths.configPath]);
    assert.equal(blocked.action, "child-stop-cleanup-required");
    assert.equal(blocked.confirmation, null);
  } else {
    assert.equal(disposition.kind, "revoked");
    assert.equal(settled[0].status, "rejected");
    assert.ok(settled.slice(1).some((result) => result.status === "fulfilled"));
    assert.equal(parent.head.state, "cancelled");
    assert.equal(parent.head.observation.childState, "cancelled");
  }
  assert.equal((await readdir(join(value.stateRoot, "claims"))).filter((name) => name === `${run.head.lease.leaseId}.json`).length, 1);
});

test("admission rejects a schema-valid revocation substituted from another plan", async (t) => {
  const value = await fixture(t);
  await initialize(value, "1e0000000001");
  const revocation = createLeaseRevocation({
    plan: value.compiled.plan, lease: value.compiled.lease, goalId: value.goal.goalId,
    goalCheckpointSha256: digest("a"), reviewedChildCheckpointSha256: null,
    cancellationReviewSha256: digest("b"),
  }, { now: new Date(baseTime + 2), suffix: "1e0000000002" });
  revocation.planSha256 = digest("e");
  await revokeLease(value.stateRoot, revocation);
  await assert.rejects(admitGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    now: new Date(baseTime + 3), suffix: "1e0000000003",
  }), /invalid revocation/);
  const recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.equal(recovered.head.purpose, "initial");
  assert.equal(recovered.childLedgerPresent, false);
});

test("parent cancellation rejects a valid-looking revocation that was never durably stored", async (t) => {
  const value = await fixture(t);
  await initialize(value, "1e1000000001");
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "1e1000000002" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "1e1000000003" });
  const parent = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs });
  const run = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  const unstored = createLeaseRevocation({
    plan: run.head.plan, lease: run.head.lease, goalId: value.goal.goalId,
    goalCheckpointSha256: parent.headSha256, reviewedChildCheckpointSha256: null,
    cancellationReviewSha256: digest("f"),
  }, { now: new Date(baseTime + 4), suffix: "1e1000000004" });
  await assert.rejects(cancelGoalAfterLeaseRevocation({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs,
    childPlan: run.head.plan, childLease: run.head.lease, leaseRevocation: unstored,
    now: new Date(baseTime + 5), suffix: "1e1000000005",
  }), /not the exact durable lease disposition/);
  assert.equal((await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs })).head.state, "running");
});

test("refresh and cancellation interleaving preserves custody and forces review of the replacement lease", async (t) => {
  const value = await fixture(t);
  await initialize(value, "1f0000000001");
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "1f0000000002" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "1f0000000003" });
  const { paths } = await controllerConfiguration(value, "cancel-refresh-race");
  const oldReview = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  const parent = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs });
  const oldRun = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  const refreshTime = Date.parse(oldRun.head.lease.expiresAt) + 1;
  const refreshed = compileBuilder(value.job, value.policy, value.entries, { now: new Date(refreshTime), suffix: "1f0000000010" });
  const oldRevocation = createLeaseRevocation({
    plan: oldRun.head.plan, lease: oldRun.head.lease, goalId: value.goal.goalId,
    goalCheckpointSha256: parent.headSha256, reviewedChildCheckpointSha256: null,
    cancellationReviewSha256: oldReview.confirmation.sha256,
  }, { now: new Date(refreshTime + 1), suffix: "1f0000000011" });
  await refreshGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    plan: refreshed.plan, lease: refreshed.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(refreshTime + 2), suffix: "1f0000000012",
    beforeAppend: async ({ plan, lease }) => {
      assert.equal(checkpointSha256(plan), checkpointSha256(oldRun.head.plan));
      assert.equal(checkpointSha256(lease), checkpointSha256(oldRun.head.lease));
      await revokeLease(value.stateRoot, oldRevocation);
    },
  });
  let recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.equal(recovered.head.purpose, "pre-admission-refresh");
  assert.equal((await inspectLeaseDisposition(value.stateRoot, oldRun.head.lease)).kind, "revoked");
  assert.equal(await inspectLeaseDisposition(value.stateRoot, recovered.head.lease), null);
  await assert.rejects(runGoalCancelCommand([
    "apply", "--config", paths.configPath, "--confirm-review-sha256", oldReview.confirmation.sha256,
  ]), /confirmation differs from the current review/);

  const currentReview = await runGoalCancelCommand(["review", "--config", paths.configPath]);
  assert.notEqual(currentReview.confirmation.sha256, oldReview.confirmation.sha256);
  const cancelled = await runGoalCancelCommand([
    "apply", "--config", paths.configPath, "--confirm-review-sha256", currentReview.confirmation.sha256,
  ], {
    now: new Date(refreshTime + 3), suffix: "1f0000000013", revocationSuffix: "1f0000000014",
    admissionSuffix: "1f0000000015", childInitializeSuffix: "1f0000000016", childCancelSuffix: "1f0000000017",
  });
  assert.equal(cancelled.action, "cancelled-before-launch");
  recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.equal((await inspectLeaseDisposition(value.stateRoot, recovered.head.lease)).kind, "revoked");
  assert.equal((await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs })).head.state, "cancelled");
});

test("one-cycle controller reads only private configuration and stops cleanly while paused", async (t) => {
  const value = await fixture(t);
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "180000000001" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "180000000002" });
  await pauseGoal({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 4), suffix: "180000000003" });
  const { paths, config } = await controllerConfiguration(value, "cycle");
  const receipt = await runGoalCycleCommand(["--config", paths.configPath]);
  assert.equal(receipt.status, "paused");
  assert.equal(receipt.action, "paused");
  assert.equal(receipt.schedulingEffect, "event-noop");
  assert.deepEqual(Object.values(receipt.authority), [false, false, false, false, false, false]);
  assert.doesNotMatch(JSON.stringify(receipt), /restartable exact Builder|pixel-model|\\Users\\|\/tmp\//u);
  const heartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
  assert.equal(heartbeat.controllerState, "ready");
  assert.deepEqual(heartbeat.sessions, []);
  assert.deepEqual(heartbeat.services.map((service) => [service.id, service.state]), [["controller", "ready"], ["capability-adapter", "disabled"]]);
  assert.doesNotMatch(JSON.stringify(heartbeat), /restartable exact Builder|pixel-model|\\Users\\|\/tmp\//u);

  if (process.platform === "linux") {
    const outputParent = join(value.root, "rendered");
    const output = join(outputParent, "goal-service");
    const installRoot = join(value.root, "installed");
    await mkdir(outputParent, { mode: 0o700 });
    await mkdir(installRoot, { mode: 0o700 });
    await chmod(outputParent, 0o700);
    const rendered = await renderGoalServiceBundle([
      "render", "--config", paths.configPath, "--output", output, "--install-root", installRoot,
      "--confirm-config-sha256", hash(canonical(config)), "--confirm-goal-sha256", goalSha256(value.goal),
      "--node", "/usr/bin/node", "--user", "nobody", "--group", "nogroup", "--docker-group", "nogroup",
    ]);
    assert.equal(rendered.goalId, value.goal.goalId);
    assert.deepEqual((await readdir(output)).sort(), [rendered.serviceName, "service-bundle.json", rendered.timerName, rendered.pathName].sort());
    const manifest = JSON.parse(await readFile(join(output, "service-bundle.json"), "utf8"));
    assert.equal(manifest.serviceSha256, rendered.serviceSha256);
    assert.equal(hash(await readFile(join(output, "service-bundle.json"))), rendered.manifestSha256);
    assert.deepEqual(Object.values(manifest.authority), [false, false, false, false, false]);
    assert.doesNotMatch(JSON.stringify(rendered), /\/tmp\/|restartable exact Builder/u);
    const inspected = await inspectGoalServiceBundle(output, { expectedOwnerUid: process.geteuid() });
    assert.deepEqual(await verifyGoalServiceLiveBinding(inspected, process.geteuid()), {
      configSha256: manifest.configSha256, goalSha256: manifest.goalSha256,
      capability: { state: "disabled", jobCount: 0, packCount: 0, toolCount: 0 },
    });
    const changed = structuredClone(config);
    changed.runtime.backendContainerName = "changed-model";
    await writeFile(paths.configPath, `${JSON.stringify(changed)}\n`, { mode: 0o600 });
    await assert.rejects(verifyGoalServiceLiveBinding(inspected, process.geteuid()), /differ from the exact rendered manifest/);
    await writeFile(paths.configPath, `${JSON.stringify(config)}\n`, { mode: 0o600 });
    await assert.rejects(renderGoalServiceBundle([
      "render", "--config", paths.configPath, "--output", join(outputParent, "wrong-confirmation"), "--install-root", installRoot,
      "--confirm-config-sha256", digest("0"), "--confirm-goal-sha256", goalSha256(value.goal),
      "--node", "/usr/bin/node", "--user", "nobody", "--group", "nogroup", "--docker-group", "nogroup",
    ]), /configuration differs from its exact confirmation/);
    assert.deepEqual((await readdir(outputParent)).sort(), ["goal-service"]);
    await assert.rejects(renderGoalServiceBundle([
      "render", "--config", paths.configPath, "--output", output, "--install-root", installRoot,
      "--node", "/usr/bin/node", "--user", "nobody", "--group", "nogroup", "--docker-group", "nogroup",
    ]), /already exists/);
  }

  const widened = structuredClone(config);
  widened.runtime.environment = { HOME: value.root };
  await writeFile(paths.configPath, `${JSON.stringify(widened)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalCycleCommand(["--config", paths.configPath]), /unexpected property|invalid/);
  config.enabled = false;
  await writeFile(paths.configPath, `${JSON.stringify(config)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalCycleCommand(["--config", paths.configPath]), /invalid/);
  config.enabled = true;
  config.runtime.workerIp = "172.29.1.9";
  await writeFile(paths.configPath, `${JSON.stringify(config)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalCycleCommand(["--config", paths.configPath]), /distinct hosts/);
  await assert.rejects(runGoalCycleCommand(["--config", paths.configPath, "--extra", "x"]), /Usage/);
});

test("expired unclaimed pre-admission authority refreshes without widening the child", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  const refreshTime = Date.parse(value.compiled.lease.expiresAt) + 1;
  const refreshed = compileBuilder(value.job, value.policy, value.entries, { now: new Date(refreshTime), suffix: "234567abcdef" });
  await refreshGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    plan: refreshed.plan, lease: refreshed.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(refreshTime + 1), suffix: "000000000002",
  });
  const recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.deepEqual(recovered.bundles.map((bundle) => bundle.purpose), ["initial", "pre-admission-refresh"]);
  assert.notEqual(recovered.bundles[0].plan.planId, recovered.head.plan.planId);
  assert.equal(recovered.head.workspaceSnapshotSha256, recovered.bundles[0].workspaceSnapshotSha256);
  await assert.rejects(refreshGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    plan: refreshed.plan, lease: refreshed.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(refreshTime + 2), suffix: "000000000003",
  }), /before prior authority expired/);
});

test("production Builder resolver renews an untouched milestone after multiple virtual days", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 2), suffix: "510000000001" });
  await dispatchGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, now: new Date(baseTime + 3), suffix: "510000000002" });
  const goalLedger = await recoverGoalLedger({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs });
  let time = baseTime + 3 * 24 * 60 * 60 * 1000;
  let record = 0x510000000010n;
  const resolver = createGoalBuilderResolver({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, policy: value.policy, objectStore: value.objectStore,
    clock: () => new Date(time++), suffix: () => (record++).toString(16),
  });
  const run = await resolver({ job: value.job, goalCheckpoint: goalLedger.head, childCheckpoint: null, childAction: null });
  const recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.deepEqual(recovered.bundles.map((bundle) => bundle.purpose), ["initial", "pre-admission-refresh", "admission"]);
  assert.equal(Date.parse(run.plan.compiledAt), baseTime + 3 * 24 * 60 * 60 * 1000);
  assert.equal(run.plan.requestSha256, value.compiled.plan.requestSha256);
  assert.deepEqual(run.plan.inputs, value.compiled.plan.inputs);
  assert.deepEqual(run.plan.objective, value.compiled.plan.objective);
  assert.deepEqual(run.plan.acceptanceCriteria, value.compiled.plan.acceptanceCriteria);
  assert.notEqual(run.lease.leaseId, value.compiled.lease.leaseId);
  assert.deepEqual(run.lease.authority, value.compiled.lease.authority);
});

test("production custody completes a dependency chain across repeated virtual-day restarts", async (t) => {
  const value = await fixture(t);
  const jobs = Array.from({ length: 5 }, (_, index) => ({
    ...structuredClone(value.job),
    jobId: `work-1786366800000-${(0xabcde0000000n + BigInt(index)).toString(16)}`,
    objective: `Complete durable stage ${index + 1}.`,
  }));
  const compiled = new Map(jobs.map((job, index) => [job.jobId, compileBuilder(job, value.policy, value.entries, {
    now: new Date(baseTime), suffix: (0x560000000000n + BigInt(index)).toString(16),
  })]));
  const goal = {
    ...structuredClone(value.goal), goalId: "workgoal-1786366800001-abcde0000000",
    objective: "Complete a five-stage durable dependency chain across days.",
    milestones: jobs.map((job, index) => ({
      milestoneId: `stage-${index + 1}`, jobId: job.jobId, jobSha256: goalSha256(job), profile: "builder",
      dependsOn: index === 0 ? [] : [`stage-${index}`],
    })),
    budgets: {
      maxJobs: 5, maxRuntimeSeconds: 3000, maxModelRequests: 50, maxInputTokens: 50000,
      maxOutputTokens: 10000, maxNetworkBytes: 5242880, maxArtifactBytes: 83886080, maxFailures: 10,
    },
  };
  for (let index = 0; index < jobs.length; index += 1) {
    const job = jobs[index];
    const run = compiled.get(job.jobId);
    await initializeGoalRunBundle({
      stateRoot: value.stateRoot, goal, jobs, jobId: job.jobId, plan: run.plan, lease: run.lease,
      workspaceSnapshotSha256: value.workspaceSnapshotSha256, now: new Date(baseTime + 1 + index),
      suffix: (0x570000000000n + BigInt(index)).toString(16),
    });
  }
  await initializeGoalLedger({ stateRoot: value.stateRoot, goal, jobs, now: new Date(baseTime + 10), suffix: "580000000000" });
  let sequence = 0x590000000000n;
  const nextSuffix = () => (sequence++).toString(16);
  let launches = 0;
  let result;
  const heartbeatTimes = [];
  for (let cycle = 0; cycle < 20; cycle += 1) {
    let tick = baseTime + (cycle + 2) * 24 * 60 * 60 * 1000;
    const clock = () => new Date(tick++);
    const resolver = createGoalBuilderResolver({
      stateRoot: value.stateRoot, goal, jobs, policy: value.policy, objectStore: value.objectStore,
      clock, suffix: nextSuffix,
    });
    const driver = createGoalBuilderDriver({
      stateRoot: value.stateRoot,
      prepare: async ({ context }) => ({
        plan: context.plan, lease: context.lease, policy: value.policy,
        bindings: {
          planSha256: checkpointSha256(context.plan), leaseSha256: checkpointSha256(context.lease),
          policySha256: context.plan.policySha256, inputSetSha256: context.plan.inputSetSha256,
        },
        workspace: { sha256: context.workspaceSnapshotSha256 },
      }),
      clock, suffixes: () => ({
        claim: nextSuffix(), running: nextSuffix(), verifying: nextSuffix(), verified: nextSuffix(), terminal: nextSuffix(), continuation: nextSuffix(),
      }),
      discard: async () => {},
      candidateRunner: async () => { launches += 1; return candidate(); },
      verifier: async (prepared, claim, work) => passingVerification(prepared.plan, claim, work),
    });
    result = await runGoalCycle({
      stateRoot: value.stateRoot, goal, jobs, resolveChildRun: resolver, driveChild: driver,
      maxControllerTransitions: 1, clock, suffix: nextSuffix,
    });
    const heartbeatNow = new Date(baseTime + (cycle + 3) * 24 * 60 * 60 * 1000);
    await publishGoalOperatorStatus({
      stateRoot: value.stateRoot, goal, jobs, now: heartbeatNow,
      secret: Buffer.alloc(32, 11), suffix: nextSuffix(),
    });
    const heartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
    heartbeatTimes.push(Date.parse(heartbeat.generatedAt));
    assert.equal(heartbeat.sessions.length <= jobs.length, true);
    assert.equal(heartbeat.sessions.every((session) => session.controls.browserCanResume === false), true);
    assert.doesNotMatch(JSON.stringify(heartbeat), /Complete durable stage|five-stage durable dependency chain/u);
    if (result.goalState === "completed") break;
  }
  assert.equal(result?.goalState, "completed");
  assert.equal(launches, jobs.length);
  const parent = await recoverGoalLedger({ stateRoot: value.stateRoot, goal, jobs });
  assert.equal(parent.head.progress.milestonesCompleted, jobs.length);
  assert.equal(parent.head.progress.jobsStarted, jobs.length);
  assert.equal(heartbeatTimes.every((time, index) => index === 0 || time > heartbeatTimes[index - 1]), true);
  const finalHeartbeat = await readWorkOperatorStatus({ stateRoot: value.stateRoot });
  assert.equal(finalHeartbeat.controllerState, "ready");
  assert.equal(finalHeartbeat.sessions.length, jobs.length);
  for (const job of jobs) {
    const custody = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal, jobs, jobId: job.jobId });
    assert.deepEqual(custody.bundles.map((bundle) => bundle.purpose), ["initial", "pre-admission-refresh", "admission"]);
  }
});

test("just-in-time refresh is single-winner and rejects policy or input drift", async (t) => {
  const concurrent = await fixture(t);
  await initialize(concurrent);
  const observed = new Date(baseTime + 4 * 24 * 60 * 60 * 1000);
  const results = await Promise.all(Array.from({ length: 16 }, (_, index) => refreshExpiredGoalRunBundle({
    stateRoot: concurrent.stateRoot, goal: concurrent.goal, jobs: concurrent.jobs, jobId: concurrent.job.jobId,
    policy: concurrent.policy, objectStore: concurrent.objectStore, now: observed,
    compilerSuffix: (0x520000000000n + BigInt(index)).toString(16),
    suffix: (0x530000000000n + BigInt(index)).toString(16),
  })));
  assert.equal(results.every((result) => result.refreshed), true);
  assert.deepEqual((await recoverGoalRunBundles({
    stateRoot: concurrent.stateRoot, goal: concurrent.goal, jobs: concurrent.jobs, jobId: concurrent.job.jobId,
  })).bundles.map((bundle) => bundle.purpose), ["initial", "pre-admission-refresh"]);

  const resolverRace = await fixture(t);
  await initialize(resolverRace);
  await initializeGoalLedger({ stateRoot: resolverRace.stateRoot, goal: resolverRace.goal, jobs: resolverRace.jobs, now: new Date(baseTime + 2), suffix: "535000000000" });
  await dispatchGoalMilestone({ stateRoot: resolverRace.stateRoot, goal: resolverRace.goal, jobs: resolverRace.jobs, now: new Date(baseTime + 3), suffix: "535000000001" });
  const active = (await recoverGoalLedger({ stateRoot: resolverRace.stateRoot, goal: resolverRace.goal, jobs: resolverRace.jobs })).head;
  const resolverResults = await Promise.all(Array.from({ length: 16 }, (_, index) => {
    let localSuffix = 0x536000000000n + BigInt(index) * 4n;
    const resolver = createGoalBuilderResolver({
      stateRoot: resolverRace.stateRoot, goal: resolverRace.goal, jobs: resolverRace.jobs,
      policy: resolverRace.policy, objectStore: resolverRace.objectStore, clock: () => observed,
      suffix: () => (localSuffix++).toString(16),
    });
    return resolver({ job: resolverRace.job, goalCheckpoint: active, childCheckpoint: null, childAction: null });
  }));
  assert.equal(resolverResults.every((run) => run.plan.requestSha256 === resolverRace.compiled.plan.requestSha256), true);
  assert.deepEqual((await recoverGoalRunBundles({
    stateRoot: resolverRace.stateRoot, goal: resolverRace.goal, jobs: resolverRace.jobs, jobId: resolverRace.job.jobId,
  })).bundles.map((bundle) => bundle.purpose), ["initial", "pre-admission-refresh", "admission"]);

  const policyDrift = await fixture(t);
  await initialize(policyDrift);
  const changedPolicy = structuredClone(policyDrift.policy);
  changedPolicy.maxRequestAgeSeconds -= 1;
  await assert.rejects(refreshExpiredGoalRunBundle({
    stateRoot: policyDrift.stateRoot, goal: policyDrift.goal, jobs: policyDrift.jobs, jobId: policyDrift.job.jobId,
    policy: changedPolicy, objectStore: policyDrift.objectStore, now: observed,
    compilerSuffix: "540000000001", suffix: "540000000002",
  }), /changed immutable plan or lease authority/);

  const inputDrift = await fixture(t);
  await initialize(inputDrift);
  await writeFile(join(inputDrift.objectStore, inputDrift.entries[0].objectName), "tampered input\n", { mode: 0o600 });
  await assert.rejects(refreshExpiredGoalRunBundle({
    stateRoot: inputDrift.stateRoot, goal: inputDrift.goal, jobs: inputDrift.jobs, jobId: inputDrift.job.jobId,
    policy: inputDrift.policy, objectStore: inputDrift.objectStore, now: observed,
    compilerSuffix: "550000000001", suffix: "550000000002",
  }), /missing, linked, replaced|failed SHA-256/);
});

test("a consumed lease can never be replaced by a pre-admission refresh", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  const claim = createLeaseConsumption(value.prepared, { now: new Date(baseTime + 2), suffix: "100000000001" });
  await claimLease(value.stateRoot, claim);
  const refreshTime = Date.parse(value.compiled.lease.expiresAt) + 1;
  const refreshed = compileBuilder(value.job, value.policy, value.entries, { now: new Date(refreshTime), suffix: "345678abcdef" });
  await assert.rejects(refreshGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    plan: refreshed.plan, lease: refreshed.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(refreshTime + 1), suffix: "000000000004",
  }), /already consumed/);
});

test("durable admission and expiry-boundary refresh have one atomic winner", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  const expiresAt = Date.parse(value.compiled.lease.expiresAt);
  const refreshTime = expiresAt + 1;
  const refreshed = compileBuilder(value.job, value.policy, value.entries, { now: new Date(refreshTime), suffix: "456789abcdef" });
  const attempts = [];
  for (let index = 0; index < 16; index += 1) {
    attempts.push(resolveGoalRunBundle({
      stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
      now: new Date(expiresAt - 1), suffix: (0x300000000000n + BigInt(index)).toString(16),
    }));
    attempts.push(refreshGoalRunBundle({
      stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
      plan: refreshed.plan, lease: refreshed.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
      now: new Date(refreshTime + 1), suffix: (0x400000000000n + BigInt(index)).toString(16),
    }));
  }
  await Promise.allSettled(attempts);
  let recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.deepEqual(recovered.bundles.map((bundle) => bundle.purpose), recovered.head.purpose === "admission"
    ? ["initial", "admission"] : ["initial", "pre-admission-refresh"]);
  if (recovered.head.purpose !== "admission") {
    await resolveGoalRunBundle({
      stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
      now: new Date(refreshTime + 2), suffix: "500000000000",
    });
    recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  }
  assert.equal(recovered.head.purpose, "admission");
  assert.equal(recovered.bundles.filter((bundle) => bundle.purpose === "admission").length, 1);

  const laterTime = Date.parse(recovered.head.lease.expiresAt) + 1;
  const later = compileBuilder(value.job, value.policy, value.entries, { now: new Date(laterTime), suffix: "56789abcdef0" });
  await assert.rejects(refreshGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    plan: later.plan, lease: later.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(laterTime + 1), suffix: "500000000001",
  }), /after durable child admission/);
});

test("a child checkpoint ledger without a durable custody admission fails closed", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease,
    workspaceSnapshotSha256: value.workspaceSnapshotSha256, now: new Date(baseTime + 2), suffix: "600000000000",
  });
  await assert.rejects(recoverGoalRunBundles({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
  }), /no durable custody marker/);
});

test("verified continuation custody binds the prior claim, usage, checkpoint, and input snapshot", async (t) => {
  const value = await fixture(t);
  await initialize(value);
  await resolveGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    now: new Date(baseTime + 2), suffix: "200000000000",
  });
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(baseTime + 2), suffix: "200000000001",
  });
  let tick = baseTime + 10;
  const first = await executeBuilderIteration({
    stateRoot: value.stateRoot, prepared: value.prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
    suffixes: { claim: "200000000002", running: "200000000003", verifying: "200000000004", verified: "200000000005", continuation: "200000000006" },
    candidateRunner: async () => candidate(), verifier: async (_prepared, claim, work) => partialVerification(value.compiled.plan, claim, work),
  });
  assert.equal(first.action, "continue");
  await appendContinuationGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    plan: value.compiled.plan, lease: first.nextLease.lease, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(Date.parse(first.nextLease.lease.issuedAt) + 1), suffix: "000000000005",
  });
  const recovered = await recoverGoalRunBundles({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId });
  assert.deepEqual(recovered.bundles.map((bundle) => bundle.purpose), ["initial", "admission", "continuation"]);
  assert.equal(recovered.head.lease.iteration, 2);
  assert.equal(recovered.head.lease.continuation.previousCheckpointSha256, checkpointSha256(first.checkpoint));
  assert.equal(recovered.head.workspaceSnapshotSha256, value.workspaceSnapshotSha256);

  const substituted = structuredClone(first.nextLease.lease);
  substituted.continuation.cumulativeUsageSha256 = digest("a");
  await assert.rejects(appendContinuationGoalRunBundle({
    stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, jobId: value.job.jobId,
    plan: value.compiled.plan, lease: substituted, workspaceSnapshotSha256: value.workspaceSnapshotSha256,
    now: new Date(Date.parse(first.nextLease.lease.issuedAt) + 2), suffix: "000000000006",
  }), /continuation|plan\/lease|contract/);
});

test("goal run bundle recovery rejects tampering, gaps, and linked records", async (t) => {
  const tampered = await fixture(t);
  const initialized = await initialize(tampered);
  const value = JSON.parse(await readFile(initialized.path, "utf8"));
  value.jobSha256 = digest("e");
  await writeFile(initialized.path, `${JSON.stringify(value, null, 2)}\n`);
  await assert.rejects(recoverGoalRunBundles({ stateRoot: tampered.stateRoot, goal: tampered.goal, jobs: tampered.jobs, jobId: tampered.job.jobId }), /differs|invalid/);

  // A committed record hardlinked into the ledger's own tmp/ is an interrupted-publish crash
  // orphan; recovery reads the durable record instead of wedging, whereas the external alias below
  // stays rejected.
  const orphan = await fixture(t);
  const orphanInitial = await initialize(orphan, "000000000009");
  await link(orphanInitial.path, join(orphanInitial.path, "..", "..", "tmp", ".bundle-0-orphan"));
  await assert.doesNotReject(recoverGoalRunBundles({ stateRoot: orphan.stateRoot, goal: orphan.goal, jobs: orphan.jobs, jobId: orphan.job.jobId }));

  const linked = await fixture(t);
  const linkedInitial = await initialize(linked, "000000000007");
  await link(linkedInitial.path, join(linked.root, "linked-bundle.json"));
  await assert.rejects(recoverGoalRunBundles({ stateRoot: linked.stateRoot, goal: linked.goal, jobs: linked.jobs, jobId: linked.job.jobId }), /single-link/);

  const gap = await fixture(t);
  await initialize(gap, "000000000008");
  await writeFile(join(gap.stateRoot, "goal-runs", gap.goal.goalId, gap.job.jobId, "records", "0002.json"), "{}\n");
  await assert.rejects(recoverGoalRunBundles({ stateRoot: gap.stateRoot, goal: gap.goal, jobs: gap.jobs, jobId: gap.job.jobId }), /sequence/);
});

test("goal run custody rejects self-consistent input and budget expansion", async (t) => {
  const inputExpansion = await fixture(t);
  const substitutedInput = structuredClone(inputExpansion.compiled);
  substitutedInput.plan.inputs[0].contentSha256 = digest("e");
  substitutedInput.plan.inputs[0].objectName = `${digest("e")}.tar`;
  substitutedInput.plan.inputSetSha256 = hash(canonical(substitutedInput.plan.inputs));
  substitutedInput.lease.inputSetSha256 = substitutedInput.plan.inputSetSha256;
  substitutedInput.lease.planSha256 = hash(canonical(substitutedInput.plan));
  await assert.rejects(initializeGoalRunBundle({
    stateRoot: inputExpansion.stateRoot, goal: inputExpansion.goal, jobs: inputExpansion.jobs, jobId: inputExpansion.job.jobId,
    plan: substitutedInput.plan, lease: substitutedInput.lease, workspaceSnapshotSha256: inputExpansion.workspaceSnapshotSha256,
    now: new Date(baseTime + 1), suffix: "000000000009",
  }), /job\/plan\/lease/);

  const budgetExpansion = await fixture(t);
  const substitutedBudget = structuredClone(budgetExpansion.compiled);
  substitutedBudget.plan.budgets.maxInputTokens += 1;
  substitutedBudget.lease.budgets.maxInputTokens += 1;
  substitutedBudget.lease.planSha256 = hash(canonical(substitutedBudget.plan));
  await assert.rejects(initializeGoalRunBundle({
    stateRoot: budgetExpansion.stateRoot, goal: budgetExpansion.goal, jobs: budgetExpansion.jobs, jobId: budgetExpansion.job.jobId,
    plan: substitutedBudget.plan, lease: substitutedBudget.lease, workspaceSnapshotSha256: budgetExpansion.workspaceSnapshotSha256,
    now: new Date(baseTime + 1), suffix: "00000000000a",
  }), /job\/plan\/lease/);
});
