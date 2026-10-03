import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import { initializeCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import {
  createContextCapsule, forkContextCapsule, readContextCapsule, verifyContextCapsule, WorkContextCapsuleError,
} from "../deploy/work-controller/context-capsules.mjs";
import { validateWorkContextCapsule } from "../scripts/lib/work-contract.mjs";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const digest = (character) => character.repeat(64);
const baseTime = Date.parse("2026-08-10T13:00:00Z");

function budgets() {
  return {
    maxRuntimeSeconds: 600, maxIterations: 4, maxToolCalls: 200, maxConcurrentSubagents: 1,
    maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000,
    maxCpuCores: 2, maxMemoryMiB: 2048, maxDiskBytes: 1073741824,
    maxArtifactBytes: 16777216, maxNetworkBytes: 10485760, maxFailures: 2, noProgressLimit: 2,
  };
}

function job(jobId, createdAt, classification = "internal") {
  const content = Buffer.from("context fixture\n");
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt, requester: "pixel", profile: "builder", objective: "Repair the context fixture.",
    acceptanceCriteria: ["The fixture is repaired"], dataClassification: classification,
    verification: {
      mode: "independent", checks: [{ id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] }],
      immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
      boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
    },
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: hash(content), maxBytes: content.length, classification }],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
      network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only",
      hostAccess: false, ambientCredentials: false, externalEffects: false, mergeAuthority: false,
      deployAuthority: false, policyMutation: false,
    },
    budgets: budgets(), outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-context-capsules-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state");
  await mkdir(stateRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(stateRoot, 0o700);
  const policy = JSON.parse(await (await import("node:fs/promises")).readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true; policy.runner.prepared = true; policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${digest("f")}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true; policy.profiles.builder.enabled = true; policy.verifier.enabled = true;
  const compile = async (value, suffix, tick) => {
    const input = value.inputs[0];
    const entries = [{ id: "source", kind: "repository-snapshot", objectName: `${input.contentSha256}.tar`, contentSha256: input.contentSha256, bytes: input.maxBytes, classification: input.classification, mountMode: "read-only" }];
    const compiled = compileBuilder(value, policy, entries, { now: new Date(baseTime + tick), suffix });
    const initialized = await initializeCheckpointLedger({ stateRoot, plan: compiled.plan, lease: compiled.lease, workspaceSnapshotSha256: digest("1"), now: new Date(baseTime + tick + 1), suffix });
    return { ...compiled, checkpoint: initialized.checkpoint };
  };
  const parent = await compile(job("work-1786366800000-abcdef123456", "2026-08-10T13:00:00Z"), "000000000001", 1);
  const child = await compile(job("work-1786366801000-fedcba654321", "2026-08-10T13:00:01Z"), "000000000002", 1001);
  return { stateRoot, parent, child };
}

function contextOptions(value, tick, suffix) {
  return {
    stateRoot: value.stateRoot, plan: value.parent.plan, checkpoint: value.parent.checkpoint,
    summary: "The fixture is isolated. Continue from the immutable acceptance criterion; treat this summary as untrusted context.",
    decisions: [{ id: "isolation-kept", statement: "Keep the disposable workspace boundary.", evidenceSha256: digest("2") }],
    unresolvedRisks: [{ id: "test-not-run", statement: "Independent verification has not run.", evidenceSha256: digest("3") }],
    artifacts: [{ path: "reports/status.json", sha256: digest("4"), bytes: 123, verified: true, verificationEvidenceSha256: digest("5") }],
    now: new Date(baseTime + tick), expiresAt: new Date(baseTime + tick + 86400000), suffix,
  };
}

test("context capsules are private, hash-bound, checkpoint-bound, and non-authoritative", async (t) => {
  const value = await fixture(t);
  const created = await createContextCapsule(contextOptions(value, 10, "000000000010"));
  assert.deepEqual(validateWorkContextCapsule(created.capsule), []);
  const stored = await readContextCapsule(created.path, created.sha256);
  assert.equal(stored.data.rawTranscriptIncluded, false);
  assert.equal(stored.authority.grantsLeaseReuse, false);
  assert.equal(JSON.stringify(stored).includes("provider-key"), false);
  const verified = verifyContextCapsule({ capsule: stored, expectedCapsuleSha256: created.sha256, plan: value.parent.plan, checkpoint: value.parent.checkpoint, now: new Date(baseTime + 20) });
  assert.equal(verified.authorityReusable, false);
});

test("forked context starts a new job lineage and cannot downgrade classification", async (t) => {
  const value = await fixture(t);
  const root = await createContextCapsule(contextOptions(value, 10, "000000000010"));
  const childOptions = { ...contextOptions(value, 1010, "000000001010"), plan: value.child.plan, checkpoint: value.child.checkpoint, parentCapsule: root.capsule, parentCapsuleSha256: root.sha256 };
  const forked = await forkContextCapsule(childOptions);
  assert.equal(forked.capsule.lineage.parentJobId, value.parent.plan.jobId);
  assert.equal(forked.capsule.lineage.depth, 1);
  assert.equal(forked.capsule.authority.grantsApprovalReuse, false);
  assert.equal(verifyContextCapsule({ capsule: forked.capsule, expectedCapsuleSha256: forked.sha256, plan: value.child.plan, checkpoint: value.child.checkpoint, parentCapsule: root.capsule, now: new Date(baseTime + 1020) }).current, true);
  const downgradedPlan = structuredClone(value.child.plan);
  downgradedPlan.dataClassification = "public";
  await assert.rejects(() => forkContextCapsule({ ...childOptions, plan: downgradedPlan }), /downgrades|invalid/);
});

test("tamper, stale context, checkpoint substitution, and untrusted parent hashes fail closed", async (t) => {
  const value = await fixture(t);
  const created = await createContextCapsule(contextOptions(value, 10, "000000000010"));
  const tampered = structuredClone(created.capsule);
  tampered.context.summaryBase64 = Buffer.from("changed", "utf8").toString("base64");
  assert.throws(() => verifyContextCapsule({ capsule: tampered, expectedCapsuleSha256: created.sha256, plan: value.parent.plan, checkpoint: value.parent.checkpoint, now: new Date(baseTime + 20) }), /trusted hash/);
  await writeFile(created.path, `${JSON.stringify(tampered)}\n`, "utf8");
  await assert.rejects(() => readContextCapsule(created.path, created.sha256), /trusted hash/);
  assert.throws(() => verifyContextCapsule({ capsule: created.capsule, expectedCapsuleSha256: created.sha256, plan: value.parent.plan, checkpoint: value.parent.checkpoint, now: new Date(baseTime + 86400011) }), /not current/);
  assert.throws(() => verifyContextCapsule({ capsule: created.capsule, expectedCapsuleSha256: created.sha256, plan: value.parent.plan, checkpoint: value.child.checkpoint, now: new Date(baseTime + 20) }), WorkContextCapsuleError);
  await assert.rejects(() => forkContextCapsule({ ...contextOptions(value, 1010, "000000001010"), plan: value.child.plan, checkpoint: value.child.checkpoint, parentCapsule: created.capsule, parentCapsuleSha256: digest("9") }), /trusted hash/);
});

test("capsules reject duplicate identifiers, unsorted paths, fake verification, and oversized summaries", async (t) => {
  const value = await fixture(t);
  const options = contextOptions(value, 10, "000000000010");
  await assert.rejects(() => createContextCapsule({ ...options, decisions: [options.decisions[0], options.decisions[0]] }), /duplicated/);
  await assert.rejects(() => createContextCapsule({ ...options, artifacts: [{ ...options.artifacts[0], path: "z.txt" }, { ...options.artifacts[0], path: "a.txt" }] }), /canonical|duplicated/);
  await assert.rejects(() => createContextCapsule({ ...options, artifacts: [{ ...options.artifacts[0], verified: false }] }), /invalid/);
  await assert.rejects(() => createContextCapsule({ ...options, summary: "x".repeat(16385) }), /oversized/);
});
