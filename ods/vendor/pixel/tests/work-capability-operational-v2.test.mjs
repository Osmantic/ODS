import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { chmod, lstat, mkdtemp, readFile, readdir, rm, symlink, writeFile, mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileCapabilityJobAuthorization, compileCapabilityOperationalV2 } from "../deploy/work-controller/capability-controller.mjs";
import { capabilityPackSha256 } from "../deploy/work-controller/capability-packs.mjs";
import { executeRuntimeDispatch } from "../deploy/work-controller/capability-runtime-dispatch.mjs";
import { recoverOperationalV2, statusOperationalV2, operationalBindingSha256 } from "../deploy/work-controller/capability-runtime-operational-v2.mjs";
import { createFileInWorkspace } from "../deploy/work-controller/capability-runtime-operational-v2.test-support.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const baseTime = Date.parse("2026-08-10T13:00:00Z");

const authority = { hostAccess: false, ambientCredentials: false, arbitraryNetwork: false, externalEffects: false, merge: false, deploy: false, publish: false, purchase: false, policyMutation: false, acceptanceCriteriaMutation: false, leaseExpansion: false };
const budgets = { maxRuntimeSeconds: 3600, maxIterations: 20, maxToolCalls: 2000, maxConcurrentSubagents: 4, maxModelRequests: 200, maxInputTokens: 1000000, maxOutputTokens: 200000, maxCpuCores: 4, maxMemoryMiB: 8192, maxDiskBytes: 10737418240, maxArtifactBytes: 1073741824, maxNetworkBytes: 104857600, maxFailures: 5, noProgressLimit: 3 };

const boundary = {
  plan: "Private immutable Deep Work plan. It authorizes no execution by itself and contains no credential, host path, provider secret, merge, deployment, publication, purchase, production, or external-effect authority.",
  lease: "This exact, expiring, single-use lease grants broad autonomy only inside the disposable job boundary. Pixel retains all authority over scope expansion, network brokers, credentials, external effects, acceptance criteria, merge, deployment, publication, purchase, and policy.",
  consumption: "Private immutable tombstone proving one lease was consumed once. It grants no replay, retry, scope expansion, credential, network, host, merge, deployment, publication, purchase, or external-effect authority.",
  checkpoint: "Private hash-bound recovery record. Contains only immutable digests, bounded counters, and transition facts. Cannot authorize or replay actions.",
  pack: "Admission-only signed broad-tools capability declaration v2. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, credential, egress, or completion authority.",
  policy: "Admission-only controller allowlist for exact signed v2 capability declarations. Policy alone grants no execution, credential, network, external-effect, scope-expansion, or completion authority; v2 runtime is not enabled.",
};
const packBoundary = "Admission-only signed broad-tools capability declaration v2. The declaration itself is disabled and grants no tool call, workspace, data, network, external-effect, credential, egress, or completion authority.";

function v2Pack(overrides = {}) {
  const inputSchema = { type: "object", additionalProperties: false, required: ["relativePath", "content"], properties: { relativePath: { type: "string" }, content: { type: "string" } } };
  const outputSchema = { type: "object", additionalProperties: false, required: ["length"], properties: { length: { type: "integer" } } };
  const pack = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-v2.schema.json", schemaVersion: 2,
    kind: "deep-work-capability-pack", id: "fixture-ws", version: "1.0.0", name: "Fixture workspace", description: "Deterministic bounded workspace tool.",
    provenance: { treeSha256: digest("a"), signerIdentity: "pixel-fixture", signatureNamespace: "pixel-work-capability-pack", admissionOnly: true },
    protocol: { name: "mcp", version: "2026-07-28", transport: "stdio-newline-jsonrpc" },
    adapter: { imageRef: `ghcr.io/osmantic/fixture-ws@sha256:${digest("b")}`, imageDigest: `sha256:${digest("b")}`, imageId: `sha256:${digest("d")}`, os: "linux", architecture: "amd64", executablePath: "/opt/fixture/bin/server", executableSha256: digest("c"), executableBytes: 4096, arguments: ["--stdio"], uid: 1000, gid: 1000, serverName: "fixture-ws", serverVersion: "1.0.0", workspace: "disposable-read-write" },
    tools: [{ name: "write", title: "Write bounded file", description: "Create one bounded UTF-8 file in the job workspace.", effectClass: "workspace", inputSchema, inputSchemaSha256: sha(inputSchema), outputSchema, outputSchemaSha256: sha(outputSchema), binding: null }],
    data: { acceptedClassifications: ["public", "internal"], returnedClassifications: ["public", "internal"], rawRetention: "job-only" },
    limits: { maxInputBytes: 8192, maxOutputBytes: 16384, maxFrameBytes: 16384, maxStdoutBytes: 65536, maxStderrBytes: 4096, maxRuntimeMs: 5000, shutdownTimeoutMs: 250, maxCalls: 1, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 262144 },
    lifecycle: { concurrency: 1, reset: "fresh-container-per-session", health: { probe: "mcp-discovery-and-tools-list", workspace: false, toolCalls: 0, maxConsecutiveFailures: 3 }, cleanup: { container: "force-remove-then-inspect-absent", workspace: "remove-then-inspect-absent" }, audit: { projection: "content-free", rawStdout: false, rawStderr: false, toolArguments: false, toolResults: false } },
    security: { networkMode: "none", networkDestinations: [], credentialRefs: [], hostFilesystem: false, dockerSocket: false, sshAgent: false, browser: false, externalEffects: false, admissionOnly: true },
    budgets: { perRun: { maxCalls: 1, maxBytes: 256 * 1024, maxDurationMs: 5000 }, cumulative: { maxCalls: 1, maxBytes: 256 * 1024, maxDurationMs: 5000 } },
    boundary: packBoundary,
  };
  return Object.assign(pack, overrides);
}

function v2Policy(pack) {
  const packSha256 = capabilityPackSha256(pack);
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-controller-policy-v2.schema.json", schemaVersion: 2,
    policyId: "workcappolicy-1786363200000-abcdef123456", createdAt: "2026-08-10T12:00:00Z", expiresAt: "2026-08-10T15:00:00Z",
    profiles: ["builder"],
    packs: [{ id: pack.id, version: pack.version, packSha256, treeSha256: pack.provenance.treeSha256, schemaVersion: 2, tools: [{ name: "write", effectClass: "workspace", bindingSummary: { targetClass: "local-filesystem", inputClassification: "internal", outputClassification: "internal" } }], classifications: ["public", "internal"] }],
    limits: { maxSessionsPerLease: 8, maxGrantLifetimeMs: 30000, maxInputBytes: 8192, maxOutputBytes: 16384, maxRuntimeMs: 5000, maxMemoryMiB: 128, maxCpuCores: 1, maxPids: 32, maxWorkspaceBytes: 262144 },
    watchdog: { maxFailures: 2, maxRepeatedEquivalent: 2, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 },
    authority: { grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: boundary.policy,
  };
}

function custody(pack) {
  const executor = { id: "omp", version: "17.2.12", sourceCommit: "45e12e5bb758198a920c6070e7e64cb33b21beac", license: "MIT", artifactSha256: digest("e"), rpcProtocolVersion: 2 };
  const model = { route: "local-only", provider: "llama.cpp", id: "assistant-model", backendImageDigest: `sha256:${digest("9")}`, contextWindow: 131072, supportsVision: false, endpointContract: "llama.cpp-discovery-and-openai-stream-v1" };
  const isolation = { mode: "hardened-container", runnerImageDigest: `sha256:${digest("f")}`, freshHome: true, inheritEnvironment: false, inheritFileDescriptors: false, inputMount: "read-only", workspaceMount: "disposable-read-write", artifactMount: "write-only-staging", destroyAfterRun: true };
  const grantedCapabilities = { tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"], network: { mode: "brokered", services: ["local-model"] } };
  const outputGate = { allowedKinds: ["patch", "test-evidence"], independentVerificationRequired: true, automaticMerge: false, automaticDeployment: false };
  const verification = { mode: "independent", checks: [{ id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] }], immutablePathPrefixes: [], maxRuntimeSeconds: 300, maxOutputBytes: 1048576, network: "none", boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules." };
  const plan = {
    $schema: "https://osmantic.com/pixel/schemas/work-plan-v1.schema.json", schemaVersion: 1, planId: "workplan-1786366800000-abcdef123456", jobId: "work-1786366800000-abcdef123456", compiledAt: "2026-08-10T13:00:00Z", requestSha256: digest("1"), policySha256: digest("2"), inputSetSha256: sha([]), profile: "builder", dataClassification: "internal", objective: "Repair the fixture until its exact checks pass.", acceptanceCriteria: ["The independently verified patch passes"], verification, inputs: [], executor, model, isolation, grantedCapabilities, budgets: { ...budgets }, outputGate, authority: { ...authority }, status: "compiled", boundary: boundary.plan,
  };
  const lease = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-lease-v1.schema.json", schemaVersion: 1, leaseId: "worklease-1786366800000-abcdef123456", jobId: plan.jobId, issuedAt: "2026-08-10T13:00:00Z", expiresAt: "2026-08-10T14:00:00Z", singleUse: true, iteration: 1, continuation: null, planSha256: sha(plan), inputSetSha256: plan.inputSetSha256, policySha256: plan.policySha256, executor: structuredClone(executor), model: structuredClone(model), isolation: structuredClone(isolation), grantedCapabilities: structuredClone(grantedCapabilities), budgets: { ...budgets }, authority: { ...authority }, outputGate: structuredClone(outputGate), boundary: boundary.lease,
  };
  const consumption = {
    $schema: "https://osmantic.com/pixel/schemas/work-lease-consumption-v1.schema.json", schemaVersion: 1, claimId: "workclaim-1786366860000-abcdef123456", jobId: plan.jobId, leaseId: lease.leaseId, claimedAt: "2026-08-10T13:01:00Z", planSha256: sha(plan), leaseSha256: sha(lease), policySha256: plan.policySha256, inputSetSha256: plan.inputSetSha256, workspaceSha256: digest("7"), executor: structuredClone(executor), model: structuredClone(model), runnerImageDigest: isolation.runnerImageDigest, singleUse: true, status: "consumed", externalEffects: false, authority: { ...authority }, boundary: boundary.consumption,
  };
  const checkpoint = {
    $schema: "https://osmantic.com/pixel/schemas/work-checkpoint-v1.schema.json", schemaVersion: 1, checkpointId: "workcheckpoint-1786366920000-abcdef123456", jobId: plan.jobId, sequence: 1, createdAt: "2026-08-10T13:02:00Z", previousCheckpointSha256: digest("8"), planSha256: sha(plan), inputSetSha256: plan.inputSetSha256, objectiveSha256: sha(plan.objective), acceptanceCriteriaSha256: sha(plan.acceptanceCriteria), state: "running", iteration: 1, usage: { runtimeSeconds: 0, modelRequests: 0, inputTokens: 0, outputTokens: 0, networkBytes: 0, artifactBytes: 0, failures: 0 }, progress: { criteriaTotal: 1, criteriaPassing: 0, criteriaFailing: 1, noProgressCount: 0, failureFingerprintSha256: null }, workspaceSnapshotSha256: digest("6"), artifactManifestSha256: null, workerSessionSha256: sha(consumption), verificationEvidenceSha256: null, authorityExpansionObserved: false, acceptanceCriteriaMutationObserved: false, externalEffectsObserved: false, boundary: boundary.checkpoint,
  };
  return { plan, lease, consumption, checkpoint, pack, policy: v2Policy(pack), expectedPackSha256: capabilityPackSha256(pack) };
}

async function privateDir(t, label) {
  const dir = await mkdtemp(join(tmpdir(), label));
  t.after(() => rm(dir, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(dir, 0o700);
  return dir;
}

function operationalConfig({ stateRoot, workspaceRoot, now = baseTime + 184000 }) {
  return { stateRoot, workspaceRoot, clock: () => new Date(now) };
}

function compile({ value, now = baseTime + 183000, grantSuffix = randomBytes(6).toString("hex"), operationSuffix = randomBytes(8).toString("hex"), content = "one bounded file", relativePath = "a/b.txt" }) {
  return compileCapabilityOperationalV2({ ...value, tool: "write", relativePath, content, now: new Date(now), grantSuffix, operationSuffix });
}

const CUSTODY_BOUNDARY = "Private content-free single-winner custody for one exact operational v2 workspace effect. It proves one claimant acquired the operation before any effect and grants no replay, future execution, credential, network, external effect, scope expansion, or completion.";
const RECEIPT_BOUNDARY = "Content-free operational v2 receipt recorded only after a completed workspace effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion.";
const RECEIPT_AUTHORITY = { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false };

function settledFor(grant, receiptSha256, overrides = {}) {
  const now = new Date(baseTime + 200000).toISOString();
  return {
    schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-custody", operationId: grant.operation.id,
    lane: "filesystem", state: "settled", grantId: grant.grantId, bindingSha256: operationalBindingSha256(grant),
    claimedAt: now, receiptSha256, settledAt: now, boundary: CUSTODY_BOUNDARY, ...overrides,
  };
}

function receiptFor(grant, overrides = {}) {
  const now = new Date(baseTime + 200000).toISOString();
  return {
    schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-receipt", operationId: grant.operation.id,
    lane: "filesystem", status: "succeeded", startedAt: now, completedAt: now, failureClass: null,
    outputSha256: sha("one bounded file"), outputBytes: 16, externalEffects: false,
    authority: { ...RECEIPT_AUTHORITY }, boundary: RECEIPT_BOUNDARY, ...overrides,
  };
}

async function plantOperational(stateRoot, { settled = null, receipt = null } = {}) {
  const custodyDir = join(stateRoot, "v2-runtime", "custody");
  const receiptsDir = join(stateRoot, "v2-runtime", "receipts");
  await mkdir(custodyDir, { recursive: true, mode: 0o700 });
  await mkdir(receiptsDir, { recursive: true, mode: 0o700 });
  if (settled) await writeFile(join(custodyDir, `${settled.operationId}.settled.json`), JSON.stringify(settled), { mode: 0o600 });
  if (receipt) await writeFile(join(receiptsDir, `${receipt.operationId}.json`), JSON.stringify(receipt), { mode: 0o600 });
}

test("production controller compiles the one exact v2 grant and runtime request", async (t) => {
  const value = custody(v2Pack());
  const { grant, runtimeRequest } = compile({ value });
  assert.equal(grant.schemaVersion, 2); assert.equal(runtimeRequest.schemaVersion, 2);
  assert.equal(grant.contentSha256, sha("one bounded file"));
  assert.equal(runtimeRequest.binding.contentSha256, grant.contentSha256);
  assert.equal(runtimeRequest.grantId, grant.grantId);
  assert.equal(grant.operation.action, "write"); assert.equal(grant.operation.scope, "job-workspace");
  assert.equal(grant.approvalMode, "none"); assert.equal(grant.egress, "none"); assert.equal(grant.credentialRefs.length, 0);
  assert.equal(grant.authority.grantsToolCall, true); assert.equal(grant.authority.grantsNetwork, false);
});

test("controller-compiled v2 grant reaches the workspace slice through the production dispatcher", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-opws-"), stateRoot = await privateDir(t, "pixel-v2c-opstate-");
  const { grant, runtimeRequest } = compile({ value, relativePath: "note.txt" });
  const out = await executeRuntimeDispatch({ grant, runtimeRequest, v2: { config: operationalConfig({ stateRoot, workspaceRoot: workspace }) } });
  assert.equal(out.schemaVersion, 2); assert.equal(out.status, "succeeded"); assert.equal(out.enabled, true); assert.equal(out.effect, "workspace-write");
  assert.equal(await readFile(join(workspace, "note.txt"), "utf8"), "one bounded file");
  const status = await statusOperationalV2({ stateRoot });
  assert.equal(status.settledCustody, 1); assert.equal(status.activeCustody, 0); assert.equal(status.receipts, 1);
});

test("runtime rejects expired and not-yet-valid grants before custody using the trusted clock", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-exp-"), stateRoot = await privateDir(t, "pixel-v2c-expstate-");
  const { grant, runtimeRequest } = compile({ value, now: baseTime + 183000 });
  await assert.rejects(
    () => executeRuntimeDispatch({ grant, runtimeRequest, v2: { config: operationalConfig({ stateRoot, workspaceRoot: workspace, now: baseTime + 999999999 } ) } }),
    /expired/,
  );
  await assert.rejects(
    () => executeRuntimeDispatch({ grant, runtimeRequest, v2: { config: operationalConfig({ stateRoot, workspaceRoot: workspace, now: baseTime + 100 } ) } }),
    /not yet valid/,
  );
  assert.equal((await readdir(join(stateRoot, "v2-runtime", "custody")).catch(() => [])).length, 0, "no custody may be created for an invalid grant");
});

test("request/grant content hash drift fails closed before any effect", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-hash-"), stateRoot = await privateDir(t, "pixel-v2c-hashstate-");
  const { grant, runtimeRequest } = compile({ value, content: "original" });
  const tampered = { ...runtimeRequest, content: "original-tampered" };
  await assert.rejects(() => executeRuntimeDispatch({ grant, runtimeRequest: tampered, v2: { config: operationalConfig({ stateRoot, workspaceRoot: workspace }) } }), /content differs from its grant binding/);
  const driftBinding = { ...runtimeRequest, binding: { ...runtimeRequest.binding, contentSha256: digest("f") } };
  await assert.rejects(() => executeRuntimeDispatch({ grant, runtimeRequest: driftBinding, v2: { config: operationalConfig({ stateRoot, workspaceRoot: workspace }) } }), /binding content hash differs from its grant/);
});

test("expiry, job, checkpoint, classification, and budget drift are rejected by the controller", async (t) => {
  const value = custody(v2Pack());
  assert.throws(() => compile({ value, relativePath: "../escape.txt" }), /escapes/);
  assert.throws(() => compile({ value, relativePath: "a/../b.txt" }), /escapes/);
  const badJob = { ...value, plan: { ...value.plan, jobId: "work-0000000000000-000000000000" } };
  assert.throws(() => compile({ value: badJob }), /differ/);
  const badCheckpoint = { ...value, checkpoint: { ...value.checkpoint, planSha256: digest("0") } };
  assert.throws(() => compile({ value: badCheckpoint }), /not the exact running checkpoint|differ/);
  const badClassPolicy = { ...value, policy: { ...value.policy, packs: value.policy.packs.map((pk) => ({ ...pk, classifications: ["public"] })) } };
  assert.throws(() => compile({ value: badClassPolicy }), /classification/);
  const badBudgetPack = v2Pack(); badBudgetPack.budgets.perRun.maxBytes = 16;
  assert.throws(() => compile({ value: custody(badBudgetPack), content: "x".repeat(1000) }), /exceeds|budget/);
});

test("pack SHA/tree/tool/effect/workspace/security drift is rejected by the controller", async (t) => {
  const value = custody(v2Pack());
  const wrongSha = { ...value, expectedPackSha256: digest("f") };
  assert.throws(() => compile({ value: wrongSha }), /differs from its trusted signed-tree binding/);
  // Tree drift while the trusted policy/expected SHA stay bound to the original pack.
  const wrongTreePack = { ...value.pack, provenance: { ...value.pack.provenance, treeSha256: digest("e") } };
  assert.throws(() => compile({ value: { ...value, pack: wrongTreePack } }), /does not admit this signed pack|differs from its trusted signed-tree binding/);
  const wrongToolPack = v2Pack(); wrongToolPack.tools = [{ ...wrongToolPack.tools[0], name: "other", effectClass: "read-only", binding: null }];
  assert.throws(() => compile({ value: custody(wrongToolPack), tool: "other" }), /workspace effect/);
  const nonWorkspace = v2Pack(); nonWorkspace.adapter.workspace = "none"; nonWorkspace.security.networkMode = "public"; nonWorkspace.security.credentialRefs = ["secret"];
  assert.throws(() => compile({ value: custody(nonWorkspace) }), /workspace tool requires a disposable workspace adapter|not the bounded workspace slice/);
});

test("traversal, symlink, hardlink, and overwrite attacks fail closed in the workspace write", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-atk-"), stateRoot = await privateDir(t, "pixel-v2c-atkstate-");
  const config = operationalConfig({ stateRoot, workspaceRoot: workspace });

  // Nested relative paths (including a symlinked parent) are rejected before any
  // custody/effect; only a single create-only filename is supported.
  await mkdir(join(workspace, "real"));
  await symlink(join(workspace, "real"), join(workspace, "link"));
  const linkGrant = { ...value, ...(await compile({ value, relativePath: "link/x.txt" })) };
  await assert.rejects(() => executeRuntimeDispatch({ grant: linkGrant.grant, runtimeRequest: linkGrant.runtimeRequest, v2: { config } }), /single create-only filename|nested relative paths are rejected/);

  // Overwrite attack: a pre-existing target must never be replaced.
  const second = await compile({ value, relativePath: "c.txt" });
  await writeFile(join(workspace, "c.txt"), "preexisting", { flag: "w" });
  await assert.rejects(() => executeRuntimeDispatch({ grant: second.grant, runtimeRequest: second.runtimeRequest, v2: { config } }), /already exists|no overwrite/);

  // Hardlink target attack: the create-only primitive must not overwrite a linked target.
  await writeFile(join(workspace, "d.txt"), "source");
  await (await import("node:fs/promises")).link(join(workspace, "d.txt"), join(workspace, "d-hard.txt"));
  const third = await compile({ value, relativePath: "d.txt" });
  await assert.rejects(() => executeRuntimeDispatch({ grant: third.grant, runtimeRequest: third.runtimeRequest, v2: { config } }), /already exists|no overwrite/);
});

test("duplicate operation identity has exactly one durable winner", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-dup-"), stateRoot = await privateDir(t, "pixel-v2c-dupstate-");
  const first = await compile({ value, relativePath: "e.txt" });
  const config = operationalConfig({ stateRoot, workspaceRoot: workspace });
  await executeRuntimeDispatch({ grant: first.grant, runtimeRequest: first.runtimeRequest, v2: { config } });
  await assert.rejects(() => executeRuntimeDispatch({ grant: first.grant, runtimeRequest: first.runtimeRequest, v2: { config } }), /already has custody|already settled|no blind replay/);
  const status = await statusOperationalV2({ stateRoot });
  assert.equal(status.settledCustody, 1); assert.equal(status.activeCustody, 0);
});

test("crash at each durable phase is recoverable without blind replay", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-rec-"), stateRoot = await privateDir(t, "pixel-v2c-recstate-");
  const { grant, runtimeRequest } = await compile({ value, relativePath: "r.txt" });
  const config = operationalConfig({ stateRoot, workspaceRoot: workspace });
  const out = await executeRuntimeDispatch({ grant, runtimeRequest, v2: { config } });
  // Completed operation recovers as completed with the same receipt hash.
  const recovered = await recoverOperationalV2({ stateRoot }, grant.operation.id, grant);
  assert.equal(recovered.status, "completed"); assert.equal(recovered.receiptSha256, out.receiptSha256);
  assert.equal(recovered.uncertain, false);
  // Active custody with no receipt recovers as uncertain-rejected (fail closed).
  const orphan = `workcapv2-${Date.now()}-${"c".repeat(16)}`;
  await mkdir(join(stateRoot, "v2-runtime", "custody"), { recursive: true });
  await writeFile(join(stateRoot, "v2-runtime", "custody", `${orphan}.json`), JSON.stringify({ operationId: orphan, state: "in-progress" }));
  const rec = await recoverOperationalV2({ stateRoot }, orphan);
  assert.equal(rec.status, "uncertain-rejected"); assert.equal(rec.uncertain, true); assert.equal(rec.failClosed, true);
  const status = await statusOperationalV2({ stateRoot });
  assert.equal(status.uncertainCustody, 1);
});

test("admission-only declarations alone grant nothing (no custody, no authority)", async (t) => {
  const value = custody(v2Pack());
  const { pack, policy, expectedPackSha256 } = value;
  // A signed v2 pack + v2 controller policy are declarations that grant nothing;
  // the controller refuses to compile an operational grant without the exact
  // plan/consumed-lease/running-checkpoint custody and signed bindings.
  assert.throws(() => compileCapabilityOperationalV2({ pack, policy, expectedPackSha256, tool: "write", relativePath: "a.txt", content: "x", now: new Date(baseTime + 183000) }), /invalid|outside|no .*lifetime/);
  const partial = { ...value, checkpoint: undefined };
  assert.throws(() => compile({ value: partial }), /invalid|undefined/);
});

test("settle vs recovery never recreates active custody beside a terminal", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-svr-"), stateRoot = await privateDir(t, "pixel-v2c-svrstate-");
  const { grant, runtimeRequest } = await compile({ value, relativePath: "svr.txt" });
  const config = operationalConfig({ stateRoot, workspaceRoot: workspace });
  await executeRuntimeDispatch({ grant, runtimeRequest, v2: { config } });
  // After the terminal is settled, recovery reports completed and never recreates
  // an active custody record; a fresh claim for the same operation fails closed.
  const recovered = await recoverOperationalV2({ stateRoot }, grant.operation.id, grant);
  assert.equal(recovered.status, "completed");
  assert.equal((await readdir(join(stateRoot, "v2-runtime", "custody")).catch(() => [])).filter((name) => name === `${grant.operation.id}.json`).length, 0, "active custody must not be recreated beside the settled terminal");
  await assert.rejects(() => executeRuntimeDispatch({ grant, runtimeRequest, v2: { config } }), /already has custody|already settled|no blind replay/);
});

test("receipt tampering fails closed on recovery", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-tamper-"), stateRoot = await privateDir(t, "pixel-v2c-tamperstate-");
  const { grant, runtimeRequest } = await compile({ value, relativePath: "t.txt" });
  await executeRuntimeDispatch({ grant, runtimeRequest, v2: { config: operationalConfig({ stateRoot, workspaceRoot: workspace }) } });
  const receiptPath = join(stateRoot, "v2-runtime", "receipts", `${grant.operation.id}.json`);
  await writeFile(receiptPath, "not valid json");
  await assert.rejects(() => recoverOperationalV2({ stateRoot }, grant.operation.id, grant), /not JSON/);
});

test("a standalone forged receipt is never promoted to completed and no settled custody is fabricated", async (t) => {
  const value = custody(v2Pack());
  const stateRoot = await privateDir(t, "pixel-v2c-forgedrecstate-");
  const { grant } = await compile({ value, relativePath: "fr.txt" });
  const now = new Date(baseTime + 200000).toISOString();
  const receiptsDir = join(stateRoot, "v2-runtime", "receipts");
  await mkdir(receiptsDir, { recursive: true, mode: 0o700 });
  const receipt = {
    schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-receipt", operationId: grant.operation.id,
    lane: "filesystem", status: "succeeded", startedAt: now, completedAt: now, failureClass: null,
    outputSha256: sha("one bounded file"), outputBytes: 16, externalEffects: false,
    authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Content-free operational v2 receipt recorded only after a completed workspace effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion.",
  };
  await writeFile(join(receiptsDir, `${grant.operation.id}.json`), JSON.stringify(receipt), { mode: 0o600 });
  const recovered = await recoverOperationalV2({ stateRoot }, grant.operation.id, grant);
  assert.equal(recovered.status, "uncertain"); assert.equal(recovered.uncertain, true); assert.equal(recovered.noReplay, true);
  const custodyDir = join(stateRoot, "v2-runtime", "custody");
  await mkdir(custodyDir, { recursive: true, mode: 0o700 });
  assert.equal((await readdir(custodyDir)).filter((name) => name === `${grant.operation.id}.settled.json`).length, 0, "receipt-only evidence must never fabricate settled custody");
  // A forged settled custody carrying the fabricated null grant binding fails
  // closed rather than ever being reported completed.
  const forgedSettled = {
    schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-custody", operationId: grant.operation.id,
    lane: "filesystem", state: "settled", grantId: null, bindingSha256: null, claimedAt: now,
    receiptSha256: sha(receipt), settledAt: now,
    boundary: "Private content-free single-winner custody for one exact operational v2 workspace effect. It proves one claimant acquired the operation before any effect and grants no replay, future execution, credential, network, external effect, scope expansion, or completion.",
  };
  await writeFile(join(custodyDir, `${grant.operation.id}.settled.json`), JSON.stringify(forgedSettled), { mode: 0o600 });
  await assert.rejects(() => recoverOperationalV2({ stateRoot }, grant.operation.id, grant), /not bound to the expected grant|fail closed/);
});

test("same-UID workspace root swap between validation and effect is rejected (descriptor-bound)", async (t) => {
  const real = await privateDir(t, "pixel-v2c-pin-real-");
  const attacker = await privateDir(t, "pixel-v2c-pin-attack-");
  const identity = { dev: (await lstat(real)).dev, ino: (await lstat(real)).ino };
  // The correct pre-open identity binding writes into the validated root.
  await createFileInWorkspace(real, identity, "ok.txt", "hello", 65536);
  assert.equal(await readFile(join(real, "ok.txt"), "utf8"), "hello");
  // A same-UID actor replaces the root with a real owner-only directory that
  // passes the "real directory" check but differs in identity: the descriptor
  // binding rejects the mismatch and never writes outside the validated root.
  await assert.rejects(() => createFileInWorkspace(attacker, identity, "esc.txt", "x", 65536), /changed identity between validation and open/);
  assert.equal(await readFile(join(attacker, "esc.txt"), "utf8").then(() => true, () => false), false, "no escape write into the swapped root");
});

test("single-level workspace write lands descriptor-bound through the real runtime flow", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-single-"), stateRoot = await privateDir(t, "pixel-v2c-singlestate-");
  const { grant, runtimeRequest } = await compile({ value, relativePath: "single.txt" });
  const out = await executeRuntimeDispatch({ grant, runtimeRequest, v2: { config: operationalConfig({ stateRoot, workspaceRoot: workspace }) } });
  assert.equal(out.status, "succeeded");
  assert.equal(await readFile(join(workspace, "single.txt"), "utf8"), "one bounded file");
  const status = await statusOperationalV2({ stateRoot });
  assert.equal(status.settledCustody, 1); assert.equal(status.activeCustody, 0);
});

test("same-UID forged settled custody + matching receipt never reports completed without an authentic expected grant", async (t) => {
  const value = custody(v2Pack());
  const stateRoot = await privateDir(t, "pixel-v2c-forgesettledstate-");
  const { grant } = await compile({ value, relativePath: "fs.txt" });
  const now = new Date(baseTime + 200000).toISOString();
  const receiptsDir = join(stateRoot, "v2-runtime", "receipts");
  const custodyDir = join(stateRoot, "v2-runtime", "custody");
  await mkdir(receiptsDir, { recursive: true, mode: 0o700 });
  await mkdir(custodyDir, { recursive: true, mode: 0o700 });
  // A same-UID forger fabricates a settled custody carrying ANY nonempty grantId,
  // ANY 64-hex binding, and a matching forged receipt hash.
  const forgedReceipt = {
    schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-receipt", operationId: grant.operation.id,
    lane: "filesystem", status: "succeeded", startedAt: now, completedAt: now, failureClass: null,
    outputSha256: digest("f"), outputBytes: 1, externalEffects: false,
    authority: { grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false },
    boundary: "Content-free operational v2 receipt recorded only after a completed workspace effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion.",
  };
  const forgedSettled = {
    schemaVersion: 1, operation: "pixel-work-capability-runtime-v2-custody", operationId: grant.operation.id,
    lane: "filesystem", state: "settled", grantId: "forged-grant-0001", bindingSha256: digest("b"), claimedAt: now,
    receiptSha256: sha(forgedReceipt), settledAt: now,
    boundary: "Private content-free single-winner custody for one exact operational v2 workspace effect. It proves one claimant acquired the operation before any effect and grants no replay, future execution, credential, network, external effect, scope expansion, or completion.",
  };
  await writeFile(join(receiptsDir, `${grant.operation.id}.json`), JSON.stringify(forgedReceipt), { mode: 0o600 });
  await writeFile(join(custodyDir, `${grant.operation.id}.settled.json`), JSON.stringify(forgedSettled), { mode: 0o600 });
  // Without an authentic expected grant, public recovery must NEVER report
  // completed: it reports an unverified/uncertain state instead.
  const unverified = await recoverOperationalV2({ stateRoot }, grant.operation.id);
  assert.equal(unverified.status, "uncertain"); assert.equal(unverified.uncertain, true); assert.equal(unverified.unverified, true); assert.equal(unverified.noReplay, true);
  // With an authentic expected grant that does not match the forged record, it
  // fails closed rather than ever reporting completed.
  await assert.rejects(() => recoverOperationalV2({ stateRoot }, grant.operation.id, grant), /not bound to the expected grant|binding differs|fail closed/);
});

test("nested relative-path grant/request is rejected before any custody, receipt, or filesystem effect", async (t) => {
  const value = custody(v2Pack());
  const workspace = await privateDir(t, "pixel-v2c-nest-"), stateRoot = await privateDir(t, "pixel-v2c-neststate-");
  const config = operationalConfig({ stateRoot, workspaceRoot: workspace });
  const { grant, runtimeRequest } = await compile({ value, relativePath: "nested/dir.txt" });
  await assert.rejects(() => executeRuntimeDispatch({ grant, runtimeRequest, v2: { config } }), /single create-only filename|nested relative paths are rejected/);
  const status = await statusOperationalV2({ stateRoot });
  assert.equal(status.activeCustody, 0); assert.equal(status.settledCustody, 0); assert.equal(status.uncertainCustody, 0); assert.equal(status.receipts, 0);
  assert.equal(await readFile(join(workspace, "nested", "dir.txt"), "utf8").then(() => true, () => false), false, "no nested filesystem effect");
});

test("createFileInWorkspace test seam enforces a single filename component (no traversal)", async (t) => {
  const real = await privateDir(t, "pixel-v2c-trav-");
  const identity = { dev: (await lstat(real)).dev, ino: (await lstat(real)).ino };
  await createFileInWorkspace(real, identity, "ok.txt", "hello", 65536);
  assert.equal(await readFile(join(real, "ok.txt"), "utf8"), "hello");
  for (const bad of ["../escape.txt", "a/b.txt", ".", "..", "a\\b.txt", "nul\0.txt"]) {
    await assert.rejects(() => createFileInWorkspace(real, identity, bad, "x", 65536), /single filename component/);
  }
  assert.equal((await readdir(real)).filter((name) => name === "escape.txt" || name === "b.txt").length, 0, "no traversal file created");
  assert.equal(await readFile(join(real, "a", "b.txt"), "utf8").then(() => true, () => false), false, "no nested file created");
});

test("static: production runtime does not export the traversal-capable workspace seam", async () => {
  const src = await readFile(new URL("../deploy/work-controller/capability-runtime-operational-v2.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(src, /export (async )?function createFileInWorkspace/, "createFileInWorkspace must not be exported from the production runtime");
  assert.match(src, /createFileInWorkspace/, "production runtime still consumes the internal workspace effect");
  const support = await readFile(new URL("../deploy/work-controller/capability-runtime-operational-v2.test-support.mjs", import.meta.url), "utf8");
  assert.match(support, /createFileInWorkspace/, "test-support must re-export the workspace effect");
});

test("static: production modules never import the operational v2 test-support module", async () => {
  const root = new URL("../deploy/work-controller/", import.meta.url);
  const files = (await readdir(root, { recursive: true })).filter((name) => name.endsWith(".mjs") && !name.endsWith(".test-support.mjs"));
  for (const file of files) {
    const content = await readFile(new URL(file, root), "utf8");
    assert.doesNotMatch(content, /capability-runtime-operational-v2\.test-support\.mjs/, `${file} must not import the operational v2 test-support module`);
  }
});

test("adversarial recovery: valid-looking settled custody with a missing receipt never reports completed", async (t) => {
  const value = custody(v2Pack());
  const stateRoot = await privateDir(t, "pixel-v2c-recv-missingrec-");
  const { grant } = await compile({ value, relativePath: "mr.txt" });
  // A settled custody with an arbitrary 64-hex receipt hash but NO receipt file.
  await plantOperational(stateRoot, { settled: settledFor(grant, digest("9")) });
  await assert.rejects(() => recoverOperationalV2({ stateRoot }, grant.operation.id, grant), /no receipt|fail closed/);
});

test("adversarial recovery: extra or missing settled-custody keys never report completed", async (t) => {
  const value = custody(v2Pack());
  const stateRoot = await privateDir(t, "pixel-v2c-recv-keys-");
  const { grant } = await compile({ value, relativePath: "k.txt" });
  const receipt = receiptFor(grant);
  const receiptSha256 = sha(receipt);
  // Extra key.
  await plantOperational(stateRoot, { settled: settledFor(grant, receiptSha256, { evil: true }), receipt });
  await assert.rejects(() => recoverOperationalV2({ stateRoot }, grant.operation.id, grant), /shape is invalid|fail closed/);
  // Missing key (drop grantId).
  const dropped = settledFor(grant, receiptSha256);
  delete dropped.grantId;
  const stateRoot2 = await privateDir(t, "pixel-v2c-recv-keys2-");
  await plantOperational(stateRoot2, { settled: dropped, receipt: receiptFor(grant) });
  await assert.rejects(() => recoverOperationalV2({ stateRoot: stateRoot2 }, grant.operation.id, grant), /shape is invalid|fail closed/);
});

test("adversarial recovery: wrong operation, boundary, or schemaVersion never report completed", async (t) => {
  const value = custody(v2Pack());
  const stateRoot = await privateDir(t, "pixel-v2c-recv-schema-");
  const { grant } = await compile({ value, relativePath: "s.txt" });
  const receipt = receiptFor(grant);
  const receiptSha256 = sha(receipt);
  // Wrong schemaVersion.
  await plantOperational(stateRoot, { settled: settledFor(grant, receiptSha256, { schemaVersion: 2 }), receipt });
  await assert.rejects(() => recoverOperationalV2({ stateRoot }, grant.operation.id, grant), /does not authenticate|shape is invalid|fail closed/);
  // Wrong operation constant.
  const stateRoot2 = await privateDir(t, "pixel-v2c-recv-schema2-");
  await plantOperational(stateRoot2, { settled: settledFor(grant, receiptSha256, { operation: "pixel-work-capability-runtime-v2-bogus" }), receipt: receiptFor(grant) });
  await assert.rejects(() => recoverOperationalV2({ stateRoot: stateRoot2 }, grant.operation.id, grant), /does not authenticate|shape is invalid|fail closed/);
  // Wrong boundary.
  const stateRoot3 = await privateDir(t, "pixel-v2c-recv-schema3-");
  await plantOperational(stateRoot3, { settled: settledFor(grant, receiptSha256, { boundary: "forged boundary" }), receipt: receiptFor(grant) });
  await assert.rejects(() => recoverOperationalV2({ stateRoot: stateRoot3 }, grant.operation.id, grant), /does not authenticate|shape is invalid|fail closed/);
});

test("adversarial recovery: a matching-hash forged receipt with forbidden authority never reports completed", async (t) => {
  const value = custody(v2Pack());
  const stateRoot = await privateDir(t, "pixel-v2c-recv-forgeauth-");
  const { grant } = await compile({ value, relativePath: "fa.txt" });
  // A receipt whose canonical SHA matches the settled custody hash but which
  // grants forbidden completion authority: hash equality alone must not pass.
  const forged = receiptFor(grant, { authority: { ...RECEIPT_AUTHORITY, grantsCompletion: true } });
  await plantOperational(stateRoot, { settled: settledFor(grant, sha(forged)), receipt: forged });
  await assert.rejects(() => recoverOperationalV2({ stateRoot }, grant.operation.id, grant), /forbidden authority|fail closed/);
});
