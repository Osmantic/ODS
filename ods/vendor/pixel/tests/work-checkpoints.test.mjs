import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, mkdir, mkdtemp, readFile, readdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import {
  executeBuilderIteration, recordInterruptedBuilderFailure, resumeBuilderVerification,
} from "../deploy/work-controller/builder-loop.mjs";
import { issueContinuationLease, readContinuationLease } from "../deploy/work-controller/continuation.mjs";
import {
  appendCheckpoint, checkpointSha256, initializeCheckpointLedger, recoverCheckpointLedger, WorkCheckpointError,
} from "../deploy/work-controller/checkpoints.mjs";
import {
  createFailureDiagnostic, readFailureDiagnostic, retainFailureDiagnostic,
} from "../deploy/work-controller/failure-diagnostics.mjs";
import { claimLease, createLeaseConsumption } from "../deploy/work-runner/runner-core.mjs";
import { validatePlanLease } from "../scripts/lib/work-contract.mjs";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const digest = (character) => character.repeat(64);
const baseTime = Date.parse("2026-08-10T13:00:00Z");

test("failure diagnostics reject content-addressed tampering", async (t) => {
  const stateRoot = await mkdtemp(join(tmpdir(), "pixel-failure-diagnostic-"));
  t.after(() => rm(stateRoot, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const diagnostic = createFailureDiagnostic({
    jobId: "work-1787731384815-6139ba88020a",
    iteration: 2,
    stage: "worker",
    failureStage: "rpc-execution",
    error: { name: "Error", message: "bounded RPC failure" },
    usage: {
      runtimeSeconds: 102, modelRequests: 35, inputTokens: 1781098, outputTokens: 13421,
      networkBytes: 0, artifactBytes: 0, failures: 1,
    },
  });
  const retained = await retainFailureDiagnostic({ stateRoot, diagnostic });
  assert.deepEqual(await readFailureDiagnostic({
    stateRoot, jobId: diagnostic.jobId, fingerprint: retained.fingerprint,
  }), diagnostic);
  const tampered = structuredClone(diagnostic);
  tampered.error.message = "different failure";
  await writeFile(retained.path, `${JSON.stringify(tampered)}\n`, "utf8");
  await assert.rejects(readFailureDiagnostic({
    stateRoot, jobId: diagnostic.jobId, fingerprint: retained.fingerprint,
  }), /content-addressed identity/);
});

function fakeCandidate() {
  return {
    durationMilliseconds: 1200,
    patch: { sha256: digest("2"), bytes: 100, changes: 1, value: {} },
    evidence: { sha256: digest("3"), bytes: 200 },
    proxyReceipt: { modelRequests: 2, inputTokens: 100, outputTokens: 20, networkBytes: 1000 },
  };
}

function fakeVerification(plan, claim, candidate, pass) {
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
  const evidence = {
    schemaVersion: 1, format: "pixel-independent-verification-v1", jobId: claim.jobId, claimId: claim.claimId,
    planSha256: claim.planSha256, patchSha256: candidate.patch.sha256, candidateSha256, status: pass ? "pass" : "fail",
    checks,
    criteria: [
      { index: 0, status: "pass", checkIds: ["patch-boundary"] },
      { index: 1, status: pass ? "pass" : "fail", checkIds: ["fixed-test"] },
    ],
    network: "none", workerSelectedChecks: false, externalEffects: false,
    boundary: "Content-free independent verification evidence only. Worker output cannot select checks or grant merge, deployment, publication, external-effect, or policy authority.",
  };
  return { evidence, artifact: { path: "unused", bytes: 300, sha256: digest("9") } };
}

function budgets(overrides = {}) {
  return {
    maxRuntimeSeconds: 600, maxIterations: 4, maxToolCalls: 200, maxConcurrentSubagents: 1,
    maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000,
    maxCpuCores: 2, maxMemoryMiB: 2048, maxDiskBytes: 1073741824,
    maxArtifactBytes: 16777216, maxNetworkBytes: 10485760, maxFailures: 2, noProgressLimit: 2,
    ...overrides,
  };
}

async function fixture(t, budgetOverrides = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-work-checkpoints-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const content = Buffer.from("checkpoint fixture\n");
  const contentSha256 = hash(content);
  const job = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456", createdAt: "2026-08-10T13:00:00Z", requester: "pixel", profile: "builder",
    objective: "Repair the fixture through independently checked iterations.",
    acceptanceCriteria: ["The fixture is repaired", "The fixed test passes"], dataClassification: "internal",
    verification: {
      mode: "independent",
      checks: [
        { id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] },
        { id: "fixed-test", kind: "command", criterionIndexes: [1], workingDirectory: "source", argv: ["/opt/node/bin/node", "--test"], timeoutSeconds: 60, maxOutputBytes: 65536 },
      ],
      immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
      boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
    },
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256, maxBytes: content.length, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
      network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only",
      hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: budgets(budgetOverrides), outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
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
  const entries = [{
    id: "source", kind: "repository-snapshot", objectName: `${contentSha256}.tar`, contentSha256,
    bytes: content.length, classification: "internal", mountMode: "read-only",
  }];
  const compiled = compileBuilder(job, policy, entries, { now: new Date(baseTime), suffix: "123456abcdef" });
  return { root, stateRoot, policy, ...compiled };
}

function update(head, state, overrides = {}) {
  const field = (name) => Object.hasOwn(overrides, name) ? overrides[name] : head[name];
  return {
    state,
    iteration: overrides.iteration ?? head.iteration,
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

async function append(value, head, state, tick, suffix, overrides = {}) {
  return appendCheckpoint({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, previousCheckpointSha256: checkpointSha256(head),
    update: update(head, state, overrides), now: new Date(baseTime + tick), suffix,
  });
}

test("checkpoint initialization publishes one complete ledger under contention", async (t) => {
  const value = await fixture(t);
  const attempts = await Promise.allSettled(Array.from({ length: 32 }, (_, index) => initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: index.toString(16).padStart(12, "0"),
  })));
  assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 1);
  for (const rejected of attempts.filter((attempt) => attempt.status === "rejected")) {
    assert.match(rejected.reason.message, /already initialized/);
  }
  const recovered = await recoverCheckpointLedger(value);
  assert.equal(recovered.checkpoints.length, 1);
  assert.equal(recovered.head.state, "authorized");
  assert.deepEqual(await readdir(join(value.stateRoot, "checkpoints")), [value.plan.jobId]);
});

test("checkpoint failure diagnostics preserve legacy records and admit only bounded stages", async (t) => {
  const value = await fixture(t);
  const initial = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "010000000001",
  });
  assert.equal(Object.hasOwn(initial.checkpoint.progress, "failureStage"), false);
  const running = await append(value, initial.checkpoint, "running", 2, "010000000002", { iteration: 1, workerSessionSha256: digest("2") });
  const failed = await append(value, running.checkpoint, "failed", 3, "010000000003", {
    usage: { failures: 1 }, progress: { noProgressCount: 1, failureFingerprintSha256: digest("3"), failureStage: "proposal-parse" },
  });
  assert.equal((await recoverCheckpointLedger(value)).head.progress.failureStage, "proposal-parse");
  assert.doesNotMatch(JSON.stringify(failed.checkpoint), /prompt|provider|message/u);

  const invalid = await fixture(t);
  const invalidInitial = await initializeCheckpointLedger({
    stateRoot: invalid.stateRoot, plan: invalid.plan, lease: invalid.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "020000000001",
  });
  const invalidRunning = await append(invalid, invalidInitial.checkpoint, "running", 2, "020000000002", { iteration: 1, workerSessionSha256: digest("2") });
  await assert.rejects(append(invalid, invalidRunning.checkpoint, "failed", 3, "020000000003", {
    usage: { failures: 1 }, progress: { noProgressCount: 1, failureFingerprintSha256: digest("3"), failureStage: "raw-provider-output" },
  }), /checkpoint contract is invalid|differs from the immutable plan/);
});

test("checkpoint ledger durably chains worker, verifier, continuation, and completion", async (t) => {
  const value = await fixture(t);
  const initial = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000001",
  });
  assert.equal(initial.checkpoint.state, "authorized");
  assert.equal((await recoverCheckpointLedger(value)).action.action, "dispatch-worker");

  const running1 = await append(value, initial.checkpoint, "running", 2, "000000000002", {
    iteration: 1, workerSessionSha256: digest("2"),
  });
  const verifying1 = await append(value, running1.checkpoint, "verifying", 3, "000000000003", {
    workspaceSnapshotSha256: digest("3"), artifactManifestSha256: digest("4"),
  });
  assert.deepEqual((await recoverCheckpointLedger(value)).action, { action: "resume-independent-verifier", state: "verifying", replayLease: false });
  const verified1 = await append(value, verifying1.checkpoint, "verified", 4, "000000000004", {
    usage: { runtimeSeconds: 10, modelRequests: 2, inputTokens: 100, outputTokens: 20, networkBytes: 1000, artifactBytes: 200, failures: 0 },
    progress: { criteriaPassing: 1, criteriaFailing: 1, noProgressCount: 0 },
    verificationEvidenceSha256: digest("8"),
  });
  assert.deepEqual((await recoverCheckpointLedger(value)).action, { action: "issue-continuation-lease", state: "verified", replayLease: false });
  const running2 = await append(value, verified1.checkpoint, "running", 5, "000000000005", {
    iteration: 2, workerSessionSha256: digest("a"), artifactManifestSha256: null, verificationEvidenceSha256: null,
  });
  const verifying2 = await append(value, running2.checkpoint, "verifying", 6, "000000000006", {
    workspaceSnapshotSha256: digest("5"), artifactManifestSha256: digest("6"), workerSessionSha256: digest("7"),
  });
  const verified2 = await append(value, verifying2.checkpoint, "verified", 7, "000000000007", {
    usage: { runtimeSeconds: 20, modelRequests: 4, inputTokens: 200, outputTokens: 40, networkBytes: 2000, artifactBytes: 400, failures: 0 },
    progress: { criteriaPassing: 2, criteriaFailing: 0, noProgressCount: 0 },
    verificationEvidenceSha256: digest("9"),
  });
  assert.deepEqual((await recoverCheckpointLedger(value)).action, { action: "record-completed", state: "verified", replayLease: false });
  const completed = await append(value, verified2.checkpoint, "completed", 8, "000000000008");
  const recovered = await recoverCheckpointLedger(value);
  assert.equal(recovered.checkpoints.length, 8);
  assert.deepEqual(recovered.head, completed.checkpoint);
  assert.deepEqual(recovered.action, { action: "terminal", state: "completed", replayLease: false });
  assert.ok(recovered.checkpoints.every((checkpoint, index) => index === 0
    ? checkpoint.previousCheckpointSha256 === null
    : checkpoint.previousCheckpointSha256 === checkpointSha256(recovered.checkpoints[index - 1])));
  const stored = await readFile(completed.path, "utf8");
  assert.doesNotMatch(stored, /(?:checkpoint fixture|Repair the fixture|\\Users\\|\/home\/|credential|authority token)/i);
});

test("checkpoint recovery never replays a consumed running lease and ignores inert crash temporaries", async (t) => {
  const value = await fixture(t);
  const initial = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000011",
  });
  await append(value, initial.checkpoint, "running", 2, "000000000012", { iteration: 1, workerSessionSha256: digest("2") });
  await writeFile(join(initial.jobRoot, "tmp", ".checkpoint-crash-remnant"), "partial sensitive bytes that are never authoritative", { mode: 0o600 });
  const recovered = await recoverCheckpointLedger(value);
  assert.deepEqual(recovered.action, { action: "fail-interrupted-worker-after-cleanup", state: "running", replayLease: false });
  assert.equal(recovered.checkpoints.length, 2);
});

test("checkpoint ledger stops repeated non-progress and exhausted budgets", async (t) => {
  const value = await fixture(t);
  const initial = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000021",
  });
  const running1 = await append(value, initial.checkpoint, "running", 2, "000000000022", { iteration: 1, workerSessionSha256: digest("2") });
  const verifying1 = await append(value, running1.checkpoint, "verifying", 3, "000000000023", {
    usage: { modelRequests: 1 }, artifactManifestSha256: digest("3"),
  });
  const verified1 = await append(value, verifying1.checkpoint, "verified", 4, "000000000024", {
    progress: { noProgressCount: 1 }, verificationEvidenceSha256: digest("5"),
  });
  const running2 = await append(value, verified1.checkpoint, "running", 5, "000000000025", {
    iteration: 2, workerSessionSha256: digest("a"), artifactManifestSha256: null, verificationEvidenceSha256: null,
  });
  const verifying2 = await append(value, running2.checkpoint, "verifying", 6, "000000000026", {
    usage: { modelRequests: 2 }, artifactManifestSha256: digest("4"),
  });
  const verified2 = await append(value, verifying2.checkpoint, "verified", 7, "000000000027", {
    progress: { noProgressCount: 2 }, verificationEvidenceSha256: digest("6"),
  });
  assert.equal((await recoverCheckpointLedger(value)).action.action, "record-no-progress");
  const stopped = await append(value, verified2.checkpoint, "no-progress", 8, "000000000028");
  assert.deepEqual((await recoverCheckpointLedger(value)).action, { action: "terminal", state: "no-progress", replayLease: false });
  assert.equal(stopped.checkpoint.progress.noProgressCount, 2);

  const budgetValue = await fixture(t, { maxRuntimeSeconds: 10 });
  const budgetInitial = await initializeCheckpointLedger({
    stateRoot: budgetValue.stateRoot, plan: budgetValue.plan, lease: budgetValue.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000031",
  });
  const budgetRunning = await append(budgetValue, budgetInitial.checkpoint, "running", 2, "000000000032", { iteration: 1, workerSessionSha256: digest("2") });
  await append(budgetValue, budgetRunning.checkpoint, "budget-exhausted", 3, "000000000033", { usage: { runtimeSeconds: 10 } });
  assert.deepEqual((await recoverCheckpointLedger(budgetValue)).action, { action: "terminal", state: "budget-exhausted", replayLease: false });
});

test("checkpoint append is single-winner and rejects stale, widened, regressive, and fake-success updates", async (t) => {
  const value = await fixture(t);
  const initial = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000041",
  });
  const concurrent = await Promise.allSettled([
    append(value, initial.checkpoint, "running", 2, "000000000042", { iteration: 1, workerSessionSha256: digest("2") }),
    append(value, initial.checkpoint, "running", 2, "000000000043", { iteration: 1, workerSessionSha256: digest("2") }),
  ]);
  assert.equal(concurrent.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(concurrent.filter((item) => item.status === "rejected").length, 1);
  const recovered = await recoverCheckpointLedger(value);
  const running = recovered.head;
  await assert.rejects(appendCheckpoint({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, previousCheckpointSha256: digest("9"),
    update: update(running, "verifying", { artifactManifestSha256: digest("3") }), now: new Date(baseTime + 3), suffix: "000000000044",
  }), /stale head/);
  await assert.rejects(appendCheckpoint({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, previousCheckpointSha256: checkpointSha256(running),
    update: { ...update(running, "verifying", { artifactManifestSha256: digest("3") }), prompt: "rewrite the goal" },
    now: new Date(baseTime + 3), suffix: "000000000045",
  }), /shape/);
  await assert.rejects(append(value, running, "completed", 3, "000000000046", {
    progress: { criteriaPassing: 2, criteriaFailing: 0 }, artifactManifestSha256: digest("3"), verificationEvidenceSha256: digest("4"),
  }), /transition|contract/);
  await assert.rejects(append(value, running, "verifying", 3, "000000000047", {
    usage: { modelRequests: value.plan.budgets.maxModelRequests + 1 }, artifactManifestSha256: digest("3"),
  }), /exceeds modelRequests/);
  await assert.rejects(append(value, running, "verifying", 3, "000000000048", {
    progress: { criteriaPassing: 1, criteriaFailing: 1, noProgressCount: 0 }, artifactManifestSha256: digest("3"),
  }), /pending verification/);
});

test("checkpoint recovery tolerates an interrupted publish, rejects tampering, gaps, alias links, and symlinks", async (t) => {
  // A committed record hardlinked into the ledger's own tmp/ is the residue of an interrupted
  // publish (a crash between writeRecord's link() of the record and the unlink() of its staging
  // twin). Recovery reads the durable record instead of wedging the ledger permanently.
  const orphanValue = await fixture(t);
  const orphanInitial = await initializeCheckpointLedger({
    stateRoot: orphanValue.stateRoot, plan: orphanValue.plan, lease: orphanValue.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000051",
  });
  await link(orphanInitial.path, join(orphanInitial.jobRoot, "tmp", ".checkpoint-0-orphan"));
  assert.equal((await recoverCheckpointLedger(orphanValue)).head.sequence, 0);

  // A record aliased anywhere other than the ledger's own tmp/ has no matching staging twin, so it
  // is genuine tampering and stays rejected as non-single-link.
  const aliasValue = await fixture(t);
  const aliasInitial = await initializeCheckpointLedger({
    stateRoot: aliasValue.stateRoot, plan: aliasValue.plan, lease: aliasValue.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000055",
  });
  await link(aliasInitial.path, join(aliasValue.stateRoot, "external-alias"));
  await assert.rejects(recoverCheckpointLedger(aliasValue), /single-link/);

  const tamperValue = await fixture(t);
  const tamperInitial = await initializeCheckpointLedger({
    stateRoot: tamperValue.stateRoot, plan: tamperValue.plan, lease: tamperValue.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000052",
  });
  const tampered = JSON.parse(await readFile(tamperInitial.path, "utf8"));
  tampered.objectiveSha256 = digest("9");
  await writeFile(tamperInitial.path, `${JSON.stringify(tampered)}\n`, { mode: 0o600 });
  await assert.rejects(recoverCheckpointLedger(tamperValue), /immutable bindings/);

  const gapValue = await fixture(t);
  const gapInitial = await initializeCheckpointLedger({
    stateRoot: gapValue.stateRoot, plan: gapValue.plan, lease: gapValue.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000053",
  });
  await writeFile(join(gapInitial.jobRoot, "records", "0000002.json"), "{}\n", { mode: 0o600 });
  await assert.rejects(recoverCheckpointLedger(gapValue), /sequence is incomplete/);

  if (process.platform !== "win32") {
    const symlinkValue = await fixture(t);
    const symlinkInitial = await initializeCheckpointLedger({
      stateRoot: symlinkValue.stateRoot, plan: symlinkValue.plan, lease: symlinkValue.lease, workspaceSnapshotSha256: digest("1"),
      now: new Date(baseTime + 1), suffix: "000000000054",
    });
    await rm(symlinkInitial.path);
    await symlink(join(symlinkInitial.jobRoot, "tmp", "missing"), symlinkInitial.path);
    await assert.rejects(recoverCheckpointLedger(symlinkValue));
  }
});

test("continuation leases are fresh, checkpoint-bound, single-winner, and budget-reducing", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  const consumption = createLeaseConsumption(prepared, { now: new Date(baseTime + 1), suffix: "000000000061" });
  const initial = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: digest("1"),
    now: new Date(baseTime + 1), suffix: "000000000062",
  });
  const running = await append(value, initial.checkpoint, "running", 2, "000000000063", {
    iteration: 1, workerSessionSha256: checkpointSha256(consumption),
  });
  const verifying = await append(value, running.checkpoint, "verifying", 3, "000000000064", {
    usage: { runtimeSeconds: 10, modelRequests: 2, inputTokens: 100, outputTokens: 20, networkBytes: 1000, artifactBytes: 200 },
    artifactManifestSha256: digest("2"), workspaceSnapshotSha256: digest("3"),
  });
  const verified = await append(value, verifying.checkpoint, "verified", 4, "000000000065", {
    progress: { criteriaPassing: 1, criteriaFailing: 1 }, verificationEvidenceSha256: digest("4"),
  });
  await assert.rejects(issueContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: consumption, checkpoint: verified.checkpoint, now: new Date(baseTime + 4), suffix: "000000000060",
  }), /predates/);
  const issued = await issueContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: consumption, checkpoint: verified.checkpoint, now: new Date(baseTime + 5), suffix: "000000000066",
  });
  assert.equal(issued.lease.iteration, 2);
  assert.equal(issued.lease.continuation.previousCheckpointSha256, checkpointSha256(verified.checkpoint));
  assert.equal(issued.lease.budgets.maxRuntimeSeconds, value.plan.budgets.maxRuntimeSeconds - 10);
  assert.equal(issued.lease.budgets.maxModelRequests, value.plan.budgets.maxModelRequests - 2);
  assert.equal(issued.lease.budgets.maxDiskBytes, value.plan.budgets.maxDiskBytes);
  assert.deepEqual(validatePlanLease(value.plan, issued.lease), []);
  await assert.rejects(readContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: consumption, checkpoint: verified.checkpoint, iteration: 2, now: new Date(baseTime + 4),
  }), /not yet valid/);
  const preCustody = await readContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: consumption, checkpoint: verified.checkpoint, iteration: 2, now: new Date(baseTime + 4),
    allowNotYetValid: true,
  });
  assert.deepEqual(preCustody.lease, issued.lease);
  const recovered = await readContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: consumption, checkpoint: verified.checkpoint, iteration: 2, now: new Date(baseTime + 6),
  });
  assert.deepEqual(recovered.lease, issued.lease);
  const tampered = structuredClone(issued.lease);
  tampered.budgets.maxRuntimeSeconds -= 1;
  await chmod(issued.path, 0o600);
  await writeFile(issued.path, `${JSON.stringify(tampered, null, 2)}\n`, { mode: 0o600 });
  await assert.rejects(readContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: consumption, checkpoint: verified.checkpoint, iteration: 2, now: new Date(baseTime + 6),
  }), /differs from the recovery checkpoint/);
  await writeFile(issued.path, `${JSON.stringify(issued.lease, null, 2)}\n`, { mode: 0o600 });
  await assert.rejects(issueContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: consumption, checkpoint: verified.checkpoint, now: new Date(baseTime + 7), suffix: "000000000067",
  }), /already exists/);
  const substitutedConsumption = structuredClone(consumption);
  substitutedConsumption.leaseId = "worklease-1786366800000-000000000099";
  await assert.rejects(readContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: substitutedConsumption, checkpoint: verified.checkpoint, iteration: 2, now: new Date(baseTime + 6),
  }), /differs|contracts/);
  // Crash-orphan tolerance: a continuation lease hardlinked to a co-located staging twin (the
  // residue of a publish crash between its link() and unlink()) is still read on recovery, not
  // wedged as non-single-link.
  await link(issued.path, join(issued.path, "..", ".continuation-2-orphan"));
  const orphanHealed = await readContinuationLease({
    stateRoot: value.stateRoot, plan: value.plan, previousLease: value.lease, policy: value.policy,
    previousConsumption: consumption, checkpoint: verified.checkpoint, iteration: 2, now: new Date(baseTime + 6),
  });
  assert.deepEqual(orphanHealed.lease, issued.lease);
});

test("Builder loop durably records candidate, verifier, and completion boundaries", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000081",
  });
  let tick = 10;
  const result = await executeBuilderIteration({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(baseTime + tick++),
    suffixes: {
      claim: "000000000082", running: "000000000083", verifying: "000000000084",
      verified: "000000000085", terminal: "000000000086",
    },
    candidateRunner: async () => fakeCandidate(),
    verifier: async (_prepared, claim, candidate) => fakeVerification(value.plan, claim, candidate, true),
  });
  assert.equal(result.action, "completed");
  assert.equal(result.nextLease, null);
  const recovered = await recoverCheckpointLedger(value);
  assert.deepEqual(recovered.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "verifying", "verified", "completed"]);
  assert.equal(recovered.head.progress.criteriaPassing, 2);
  assert.equal(recovered.head.usage.modelRequests, 2);
  assert.equal(recovered.head.usage.artifactBytes, 600);
  assert.equal(recovered.head.verificationEvidenceSha256, digest("9"));
});

test("a job may execute its declared final iteration but cannot continue beyond it", async (t) => {
  const value = await fixture(t, { maxIterations: 1 });
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "00000000008e",
  });
  let tick = 10;
  const result = await executeBuilderIteration({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(baseTime + tick++),
    suffixes: {
      claim: "00000000008f", running: "000000000090", verifying: "000000000091",
      verified: "000000000092", terminal: "000000000093",
    },
    candidateRunner: async () => fakeCandidate(),
    verifier: async (_prepared, claim, candidate) => fakeVerification(value.plan, claim, candidate, true),
  });
  assert.equal(result.action, "completed");
  assert.equal(result.checkpoint.iteration, 1);
  assert.equal(result.nextLease, null);
});

test("Builder loop recovers one consumed pre-launch claim and has one launch winner", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000087",
  });
  const prelaunchClaim = createLeaseConsumption(prepared, { now: new Date(baseTime + 2), suffix: "000000000088" });
  await claimLease(value.stateRoot, prelaunchClaim);
  let launches = 0;
  const attempts = await Promise.allSettled(Array.from({ length: 16 }, (_, index) => {
    const prefix = 0xd00000000000n + BigInt(index * 16);
    const recordSuffix = (offset) => (prefix + BigInt(offset)).toString(16).padStart(12, "0");
    let tick = baseTime + 10 + index * 10;
    return executeBuilderIteration({
      stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(tick++),
      suffixes: {
        claim: recordSuffix(1), running: recordSuffix(2), verifying: recordSuffix(3),
        verified: recordSuffix(4), terminal: recordSuffix(5),
      },
      candidateRunner: async (_prepared, claim) => {
        launches += 1;
        assert.equal(claim.claimId, prelaunchClaim.claimId);
        return fakeCandidate();
      },
      verifier: async (_prepared, claim, candidate) => fakeVerification(value.plan, claim, candidate, true),
    });
  }));
  assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 1);
  assert.equal(launches, 1);
  const completed = attempts.find((attempt) => attempt.status === "fulfilled").value;
  assert.equal(completed.claim.claimId, prelaunchClaim.claimId);
  assert.deepEqual((await recoverCheckpointLedger(value)).checkpoints.map((checkpoint) => checkpoint.state), [
    "authorized", "running", "verifying", "verified", "completed",
  ]);
});

test("Builder loop never recovers a consumed pre-launch claim after lease expiry", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "00000000008a",
  });
  const prelaunchClaim = createLeaseConsumption(prepared, { now: new Date(baseTime + 2), suffix: "00000000008b" });
  await claimLease(value.stateRoot, prelaunchClaim);
  let launches = 0;
  await assert.rejects(executeBuilderIteration({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {},
    clock: () => new Date(Date.parse(value.lease.expiresAt) + 1),
    suffixes: { claim: "00000000008c", running: "00000000008d" },
    candidateRunner: async () => { launches += 1; return fakeCandidate(); },
    verifier: async () => { throw new Error("expired claim reached verifier"); },
  }), /expired before consumption/);
  assert.equal(launches, 0);
  assert.equal((await recoverCheckpointLedger(value)).head.state, "authorized");
});

test("Builder loop issues one reduced continuation lease after partial verified progress", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000091",
  });
  let tick = 20;
  const result = await executeBuilderIteration({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(baseTime + tick++),
    suffixes: {
      claim: "000000000092", running: "000000000093", verifying: "000000000094",
      verified: "000000000095", continuation: "000000000096",
    },
    candidateRunner: async () => fakeCandidate(),
    verifier: async (_prepared, claim, candidate) => fakeVerification(value.plan, claim, candidate, false),
  });
  assert.equal(result.action, "continue");
  assert.equal(result.checkpoint.state, "verified");
  assert.equal(result.checkpoint.progress.criteriaPassing, 1);
  assert.equal(result.checkpoint.progress.noProgressCount, 0);
  assert.equal(result.nextLease.lease.iteration, 2);
  assert.equal(result.nextLease.lease.budgets.maxModelRequests, value.plan.budgets.maxModelRequests - 2);
  assert.equal(result.nextLease.lease.budgets.maxArtifactBytes, value.plan.budgets.maxArtifactBytes - 600);
  assert.equal(result.nextLease.lease.budgets.maxFailures, value.plan.budgets.maxFailures - 1);
  assert.equal((await recoverCheckpointLedger(value)).action.action, "issue-continuation-lease");
});

test("Builder loop resumes verification from retained artifacts without replaying the worker", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  const initialized = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000101",
  });
  const claim = createLeaseConsumption(prepared, { now: new Date(baseTime + 1), suffix: "000000000102" });
  await claimLease(value.stateRoot, claim);
  const running = await append(value, initialized.checkpoint, "running", 2, "000000000103", {
    iteration: 1, workerSessionSha256: checkpointSha256(claim),
  });
  const verifying = await append(value, running.checkpoint, "verifying", 3, "000000000104", {
    usage: { runtimeSeconds: 2, modelRequests: 2, inputTokens: 100, outputTokens: 20, networkBytes: 1000, artifactBytes: 300 },
    artifactManifestSha256: digest("2"),
  });
  const recoveryPrepared = { ...prepared, verificationOnly: true, recoveryConsumption: claim };
  const candidate = { ...fakeCandidate(), verificationArtifactPresent: true };
  const verification = fakeVerification(value.plan, claim, candidate, true);
  let tick = 10;
  const result = await resumeBuilderVerification({
    stateRoot: value.stateRoot, prepared: recoveryPrepared, lifecycleOptions: {}, clock: () => new Date(baseTime + tick++),
    suffixes: { verified: "000000000105", terminal: "000000000106" },
    candidateRecovery: async () => candidate,
    verificationRecovery: async () => verification,
    verifier: async () => { throw new Error("worker-selected verifier replayed"); },
  });
  assert.equal(result.action, "completed");
  assert.deepEqual((await recoverCheckpointLedger(value)).checkpoints.map((checkpoint) => checkpoint.state), [
    "authorized", "running", "verifying", "verified", "completed",
  ]);
  assert.equal(result.checkpoint.usage.artifactBytes, 600);
});

test("Builder loop durably fails closed when a consumed worker attempt errors", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000111",
  });
  let tick = 10;
  const workerError = new Error("synthetic worker failure");
  workerError.workUsage = {
    runtimeSeconds: 3, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 100,
    artifactBytes: 0, failures: 1,
  };
  workerError.workCleanupComplete = true;
  await assert.rejects(executeBuilderIteration({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(baseTime + tick++),
    suffixes: { claim: "000000000112", running: "000000000113", failed: "000000000114" },
    candidateRunner: async () => { throw workerError; },
    verifier: async () => { throw new Error("verifier must not run"); },
  }), /durably closed/);
  const recovered = await recoverCheckpointLedger(value);
  assert.deepEqual(recovered.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "failed"]);
  assert.equal(recovered.head.usage.runtimeSeconds, 3);
  assert.equal(recovered.head.usage.modelRequests, 1);
  assert.equal(recovered.head.usage.failures, 1);
  assert.equal(recovered.head.progress.noProgressCount, 1);
  assert.match(recovered.head.progress.failureFingerprintSha256, /^[a-f0-9]{64}$/);
  const diagnostic = await readFailureDiagnostic({
    stateRoot: value.stateRoot,
    jobId: value.plan.jobId,
    fingerprint: recovered.head.progress.failureFingerprintSha256,
  });
  assert.equal(diagnostic.stage, "worker");
  assert.equal(diagnostic.failureStage, "profile-execution");
  assert.deepEqual(diagnostic.error, { name: "Error", message: "synthetic worker failure" });
  assert.deepEqual(diagnostic.usage, recovered.head.usage);
});

test("Builder loop records an unclean worker as cleanup-failed for exact recovery", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000115",
  });
  const workerError = new Error("synthetic cleanup failure");
  workerError.workCleanupComplete = false;
  await assert.rejects(executeBuilderIteration({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(baseTime + 10),
    suffixes: { claim: "000000000116", running: "000000000117", failed: "000000000118" },
    candidateRunner: async () => { throw workerError; },
    verifier: async () => { throw new Error("verifier must not run"); },
  }), /cleanup-failed/);
  const recovered = await recoverCheckpointLedger(value);
  assert.deepEqual(recovered.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "cleanup-failed"]);
  assert.equal(recovered.action.action, "fail-interrupted-worker-after-cleanup");
  assert.equal(recovered.action.state, "cleanup-failed");
  assert.equal(recovered.head.usage.runtimeSeconds, value.lease.budgets.maxRuntimeSeconds);
  assert.match(recovered.head.progress.failureFingerprintSha256, /^[a-f0-9]{64}$/);
  const diagnostic = await readFailureDiagnostic({
    stateRoot: value.stateRoot,
    jobId: value.plan.jobId,
    fingerprint: recovered.head.progress.failureFingerprintSha256,
  });
  assert.equal(diagnostic.stage, "worker-cleanup");
  assert.equal(diagnostic.failureStage, "cleanup");
  assert.equal(diagnostic.error.message, "synthetic cleanup failure");
  assert.deepEqual(diagnostic.usage, recovered.head.usage);
});

test("Builder recovery cleans an interrupted consumed worker before durably closing it", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  const initialized = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000121",
  });
  const claim = createLeaseConsumption(prepared, { now: new Date(baseTime + 2), suffix: "000000000122" });
  await claimLease(value.stateRoot, claim);
  await append(value, initialized.checkpoint, "running", 3, "000000000123", {
    iteration: 1, workerSessionSha256: checkpointSha256(claim),
  });
  prepared.cleanupOnly = true;
  prepared.recoveryConsumption = claim;
  let cleanupCalls = 0;
  const result = await recordInterruptedBuilderFailure({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(baseTime + 4),
    checkpointSuffix: "000000000124",
    cleanup: async (_prepared, recoveredClaim) => {
      cleanupCalls += 1;
      assert.deepEqual(recoveredClaim, claim);
      return {
        resourcesRemoved: true,
        usage: {
          runtimeSeconds: 4, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 100,
          artifactBytes: 0, failures: 1,
        },
      };
    },
  });
  assert.equal(cleanupCalls, 1);
  assert.equal(result.action, "failed");
  const recovered = await recoverCheckpointLedger(value);
  assert.deepEqual(recovered.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "failed"]);
  assert.deepEqual(recovered.head.usage, {
    runtimeSeconds: 4, modelRequests: 1, inputTokens: 10, outputTokens: 2, networkBytes: 100,
    artifactBytes: 0, failures: 1,
  });
  const diagnostic = await readFailureDiagnostic({
    stateRoot: value.stateRoot,
    jobId: value.plan.jobId,
    fingerprint: recovered.head.progress.failureFingerprintSha256,
  });
  assert.equal(diagnostic.failureStage, "interrupted");
  assert.equal(diagnostic.error.message, "interrupted worker attempt");
  assert.deepEqual(diagnostic.usage, recovered.head.usage);
});

test("Builder recovery records failed cleanup and retries without double charging", async (t) => {
  const value = await fixture(t);
  const prepared = {
    plan: value.plan, lease: value.lease, policy: value.policy,
    bindings: {
      planSha256: value.planSha256, leaseSha256: checkpointSha256(value.lease),
      policySha256: value.policySha256, inputSetSha256: value.inputSetSha256,
    },
    workspace: { sha256: digest("1") },
  };
  const initialized = await initializeCheckpointLedger({
    stateRoot: value.stateRoot, plan: value.plan, lease: value.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
    now: new Date(baseTime + 1), suffix: "000000000131",
  });
  const claim = createLeaseConsumption(prepared, { now: new Date(baseTime + 2), suffix: "000000000132" });
  await claimLease(value.stateRoot, claim);
  await append(value, initialized.checkpoint, "running", 3, "000000000133", {
    iteration: 1, workerSessionSha256: checkpointSha256(claim),
  });
  prepared.cleanupOnly = true;
  prepared.recoveryConsumption = claim;
  await assert.rejects(recordInterruptedBuilderFailure({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(baseTime + 4),
    checkpointSuffix: "000000000134",
    cleanup: async () => ({ resourcesRemoved: false, usage: null }),
  }), /cleanup-failed/);
  let recovered = await recoverCheckpointLedger(value);
  assert.deepEqual(recovered.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "cleanup-failed"]);
  assert.equal(recovered.action.action, "fail-interrupted-worker-after-cleanup");
  const charged = structuredClone(recovered.head.usage);
  const closed = await recordInterruptedBuilderFailure({
    stateRoot: value.stateRoot, prepared, lifecycleOptions: {}, clock: () => new Date(baseTime + 5),
    checkpointSuffix: "000000000135",
    cleanup: async () => ({
      resourcesRemoved: true,
      usage: { runtimeSeconds: 1, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 1 },
    }),
  });
  assert.equal(closed.action, "failed");
  recovered = await recoverCheckpointLedger(value);
  assert.deepEqual(recovered.checkpoints.map((checkpoint) => checkpoint.state), ["authorized", "running", "cleanup-failed", "failed"]);
  assert.deepEqual(recovered.head.usage, charged);
});
