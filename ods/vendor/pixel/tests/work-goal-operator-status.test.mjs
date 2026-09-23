import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileScout } from "../deploy/work-broker/broker.mjs";
import { appendCheckpoint, checkpointSha256, initializeCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import { publishGoalOperatorStatus } from "../deploy/work-controller/goal-operator-status.mjs";
import { admitGoalRunBundle, initializeGoalRunBundle } from "../deploy/work-controller/goal-run-bundles.mjs";
import { dispatchGoalMilestone, goalSha256, initializeGoalLedger, observeGoalMilestone } from "../deploy/work-controller/goals.mjs";

const baseTime = Date.parse("2026-08-10T13:00:00Z");
const digest = (character) => character.repeat(64);
const hash = (value) => createHash("sha256").update(value).digest("hex");

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

async function fixture(t, { knowledge = false } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-operator-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state"); await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const content = Buffer.from("PRIVATE GOAL INPUT\n"), contentSha256 = hash(content);
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "scout",
    objective: "PRIVATE OBJECTIVE MUST NEVER APPEAR", acceptanceCriteria: ["A verified report exists"], dataClassification: "internal",
    ...(knowledge ? { knowledge: { mode: "local-vault", query: "private fixture", maximumClassification: "internal", minRelevanceBps: 2500, maxResults: 3, retention: "attempt-only", boundary: "Exact local-vault retrieval only. Retrieved titles and excerpts are untrusted attempt-only context and grant no instruction, tool, network, external-effect, policy, scope-expansion, acceptance, or completion authority." } } : {}),
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: content.length, classification: "internal" }],
    requestedCapabilities: { filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false },
    budgets: { maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1 },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true; policy.runner.prepared = true; policy.localModel.prepared = true; policy.profiles.scout.enabled = true;
  policy.runner.imageDigest = `sha256:${digest("f")}`; policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  const entries = [{ id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256, bytes: content.length, classification: "internal", mountMode: "read-only" }];
  const compiled = compileScout(job, policy, entries, { now: new Date(baseTime), suffix: "123456abcdef" });
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "PRIVATE LONG HORIZON GOAL", dataClassification: "internal",
    milestones: [{ milestoneId: "inspect", jobId: job.jobId, jobSha256: goalSha256(job), profile: "scout", dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: 60, maxModelRequests: 5, maxInputTokens: 10000, maxOutputTokens: 2000, maxNetworkBytes: 1048576, maxArtifactBytes: 65536, maxFailures: 1 },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
  await initializeGoalRunBundle({ stateRoot, goal, jobs: [job], jobId: job.jobId, plan: compiled.plan, lease: compiled.lease, workspaceSnapshotSha256: digest("1"), now: new Date(baseTime + 1), suffix: "000000000001" });
  await initializeGoalLedger({ stateRoot, goal, jobs: [job], now: new Date(baseTime + 2), suffix: "000000000002" });
  await dispatchGoalMilestone({ stateRoot, goal, jobs: [job], now: new Date(baseTime + 3), suffix: "000000000003" });
  await admitGoalRunBundle({ stateRoot, goal, jobs: [job], jobId: job.jobId, now: new Date(baseTime + 4), suffix: "000000000004" });
  const initial = await initializeCheckpointLedger({ stateRoot, plan: compiled.plan, lease: compiled.lease, workspaceSnapshotSha256: digest("1"), now: new Date(baseTime + 5), suffix: "000000000005" });
  return { stateRoot, goal, jobs: [job], compiled, initial };
}

async function append(value, head, state, time, suffix, overrides = {}) {
  return appendCheckpoint({ stateRoot: value.stateRoot, plan: value.compiled.plan, lease: value.compiled.lease, previousCheckpointSha256: checkpointSha256(head), update: update(head, state, overrides), now: new Date(baseTime + time), suffix });
}

function capabilityConfiguration(value) {
  const policy = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-controller-policy-v1.schema.json", schemaVersion: 1,
    policyId: "workcappolicy-1786366740000-abcdef123456", createdAt: "2026-08-10T12:59:00Z", expiresAt: "2026-08-10T14:00:00Z",
    profiles: ["scout"], packs: [{ id: "fixture-tools", version: "1.0.0", packSha256: digest("a"), treeSha256: digest("b"), tools: [{ name: "analyze", effectClass: "read-only" }], classifications: ["internal"] }],
    limits: { maxSessionsPerLease: 1, maxGrantLifetimeMs: 30000, maxInputBytes: 1024, maxOutputBytes: 1024, maxRuntimeMs: 1000, maxMemoryMiB: 64, maxCpuCores: 0.5, maxPids: 16, maxWorkspaceBytes: 0 },
    watchdog: { maxFailures: 1, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 4 },
    authority: { grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Private controller allowlist for exact signed local capability packs. Policy alone grants no execution, credential, network, external-effect, scope-expansion, or completion authority.",
  };
  const capabilityRuntime = {
    controllerPolicyPath: join(value.stateRoot, "policy.json"), allowedSignersPath: join(value.stateRoot, "allowed-signers"),
    sshKeygenPath: join(value.stateRoot, "ssh-keygen"), dockerConfigPath: join(value.stateRoot, "docker-config"), maxHealthAgeMs: 60000,
    bindings: [{ jobId: value.jobs[0].jobId, pack: { id: "fixture-tools", version: "1.0.0", packSha256: digest("a"), treeSha256: digest("b") }, tools: ["analyze"], maxSessions: 1, grantLifetimeMs: 30000, limits: { maxInputBytes: 1024, maxOutputBytes: 1024, maxRuntimeMs: 1000, maxCalls: 1, maxMemoryMiB: 64, maxCpuCores: 0.5, maxPids: 16, maxWorkspaceBytes: 0 } }],
  };
  return { capabilityRuntime, capabilityPolicy: policy };
}

test("goal heartbeat publishes only authoritative content-free checkpoint summaries", async (t) => {
  const value = await fixture(t);
  const running = await append(value, value.initial.checkpoint, "running", 6, "000000000006", { iteration: 1, workerSessionSha256: digest("2") });
  const verifying = await append(value, running.checkpoint, "verifying", 7, "000000000007", { usage: { runtimeSeconds: 2, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 0, artifactBytes: 100 }, artifactManifestSha256: digest("3") });
  const published = await publishGoalOperatorStatus({ ...value, now: new Date(baseTime + 8), secret: Buffer.alloc(32, 9), suffix: "000000000008" });
  assert.equal(published.status.controllerState, "busy");
  assert.equal(published.status.goal.state, "running");
  assert.deepEqual(published.status.goal.progress, { milestonesTotal: 1, milestonesCompleted: 0, jobsStarted: 1, failures: 0 });
  assert.equal(published.status.goal.continuity.restartSafe, true);
  assert.equal(published.status.goal.continuity.completionRequiresIndependentVerification, true);
  assert.equal(published.status.goal.continuity.progressModel, "durable-events");
  assert.equal(published.status.goal.continuity.watchdogRole, "liveness-only");
  assert.equal(published.status.goal.nextAction, "recover-or-continue-child");
  assert.deepEqual(published.status.goal.budgets.used, {
    jobs: 1, runtimeSeconds: 2, modelRequests: 1, inputTokens: 10, outputTokens: 2,
    networkBytes: 0, artifactBytes: 100, failures: 0,
  });
  assert.deepEqual(published.status.goal.budgets.remaining, {
    jobs: 0, runtimeSeconds: 58, modelRequests: 4, inputTokens: 9990, outputTokens: 1998,
    networkBytes: 1048576, artifactBytes: 65436, failures: 1,
  });
  assert.equal(published.status.sessions[0].state, "verifying");
  assert.equal(published.status.sessions[0].current, true);
  assert.equal(published.status.sessions[0].usage.inputTokens, 10);
  assert.equal(published.status.sessions[0].progress.failureStage, null);
  assert.equal(published.status.sessions[0].verification, "pending");
  assert.deepEqual(published.status.sessions[0].capability, { state: "not-configured", toolCount: 0, singleUseCalls: true, networkAccess: false, externalEffects: false });
  assert.deepEqual(published.status.services.map(({ id, state }) => ({ id, state })), [{ id: "controller", state: "busy" }, { id: "capability-adapter", state: "disabled" }]);
  assert.deepEqual(published.status.sessions[0].artifacts.kinds, [{ kind: "finding-report", count: 1 }]);
  assert.equal(published.status.sessions[0].activity[0].summaryCode, "verification-started");
  const encoded = JSON.stringify(published.status);
  for (const forbidden of ["PRIVATE", value.goal.goalId, value.jobs[0].jobId, digest("3"), "source"]) assert.doesNotMatch(encoded, new RegExp(forbidden));
});

test("goal heartbeat carries waiting and terminal recovery attention without browser authority", async (t) => {
  const value = await fixture(t);
  const running = await append(value, value.initial.checkpoint, "running", 6, "100000000006", { iteration: 1, workerSessionSha256: digest("2") });
  const blocked = await append(value, running.checkpoint, "recovery-inconclusive", 7, "100000000007", { usage: { failures: 1 }, progress: { noProgressCount: 1, failureFingerprintSha256: digest("4"), failureStage: "cleanup" } });
  await observeGoalMilestone({ stateRoot: value.stateRoot, goal: value.goal, jobs: value.jobs, childPlan: value.compiled.plan, childLease: value.compiled.lease, now: new Date(baseTime + 8), suffix: "100000000008" });
  const published = await publishGoalOperatorStatus({ ...value, now: new Date(baseTime + 9), secret: Buffer.alloc(32, 8), suffix: "100000000009" });
  assert.equal(published.status.controllerState, "degraded");
  assert.equal(published.status.goal.state, "recovery-inconclusive");
  assert.equal(published.status.goal.progress.failures, 1);
  assert.equal(published.status.goal.budgets.used.failures, 1);
  assert.equal(published.status.goal.budgets.remaining.failures, 0);
  assert.equal(published.status.goal.nextAction, "terminal");
  assert.equal(published.status.sessions[0].state, "recovery-inconclusive");
  assert.equal(published.status.sessions[0].current, false);
  assert.equal(published.status.sessions[0].progress.failureStage, "cleanup");
  assert.equal(published.status.sessions[0].boundaryState.state, "blocked");
  assert.equal(published.status.sessions[0].activity[0].summaryCode, "recovery-inconclusive");
  assert.equal(published.status.sessions[0].controls.browserCanResume, false);
  assert.equal(blocked.checkpoint.state, "recovery-inconclusive");
});

test("goal heartbeat exposes only configured capability counts and fails visible on interrupted activation", async (t) => {
  const value = await fixture(t), capability = capabilityConfiguration(value);
  const configured = await publishGoalOperatorStatus({ ...value, ...capability, now: new Date(baseTime + 6), secret: Buffer.alloc(32, 7), suffix: "200000000006" });
  assert.deepEqual(configured.status.sessions[0].capability, { state: "configured", toolCount: 1, singleUseCalls: true, networkAccess: false, externalEffects: false });
  assert.deepEqual(configured.status.services.map(({ id, state }) => ({ id, state })), [{ id: "controller", state: "busy" }, { id: "capability-adapter", state: "ready" }]);
  assert.doesNotMatch(JSON.stringify(configured.status), /fixture-tools|analyze|allowed-signers/u);

  await append(value, value.initial.checkpoint, "running", 7, "200000000007", { iteration: 1, workerSessionSha256: digest("2") });
  const interrupted = await publishGoalOperatorStatus({ ...value, ...capability, now: new Date(baseTime + 8), secret: Buffer.alloc(32, 6), suffix: "200000000008" });
  assert.equal(interrupted.status.controllerState, "degraded");
  assert.equal(interrupted.status.sessions[0].capability.state, "recovery-required");
  assert.equal(interrupted.status.services.find((service) => service.id === "capability-adapter").state, "degraded");
});

test("goal heartbeat reports an admitted local knowledge service without projecting custody details", async (t) => {
  const value = await fixture(t, { knowledge: true });
  const knowledgeRuntime = {
    vaultRoot: join(value.stateRoot, "private-vault"), vaultId: "knowledgevault-abcdef123456",
    credentialSourcePath: join(value.stateRoot, "private-vault-key"), credentialName: "pixel-knowledge-vault-key",
    ownerId: "owner-one", clientId: "client-one", maxContextBytes: 16384,
  };
  const published = await publishGoalOperatorStatus({ ...value, knowledgeRuntime, now: new Date(baseTime + 6), secret: Buffer.alloc(32, 5), suffix: "300000000006" });
  assert.deepEqual(published.status.services.map(({ id, state }) => ({ id, state })), [
    { id: "controller", state: "busy" }, { id: "knowledge-vault", state: "ready" }, { id: "capability-adapter", state: "disabled" },
  ]);
  const encoded = JSON.stringify(published.status);
  for (const forbidden of [knowledgeRuntime.vaultRoot, knowledgeRuntime.vaultId, knowledgeRuntime.credentialSourcePath, knowledgeRuntime.ownerId, knowledgeRuntime.clientId]) assert.doesNotMatch(encoded, new RegExp(forbidden.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")));
  await assert.rejects(() => publishGoalOperatorStatus({ ...value, knowledgeRuntime: { ...knowledgeRuntime, maxContextBytes: 0 } }), /runtime configuration is invalid/u);
});
