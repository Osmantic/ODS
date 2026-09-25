import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileBuilder } from "../deploy/work-broker/broker.mjs";
import { initializeCheckpointLedger } from "../deploy/work-controller/checkpoints.mjs";
import { runContextSessionCommand } from "../deploy/work-controller/context-session-cli.mjs";
import { runContextSessionGuide } from "../deploy/work-controller/context-session-guide.mjs";

const hash = (value) => createHash("sha256").update(value).digest("hex");
const digest = (character) => character.repeat(64);
const baseTime = Date.parse("2026-08-11T12:00:00Z");

function budgets() {
  return {
    maxRuntimeSeconds: 600, maxIterations: 4, maxToolCalls: 200, maxConcurrentSubagents: 1,
    maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000,
    maxCpuCores: 2, maxMemoryMiB: 2048, maxDiskBytes: 1073741824,
    maxArtifactBytes: 16777216, maxNetworkBytes: 10485760, maxFailures: 2, noProgressLimit: 2,
  };
}

function job(jobId, createdAt, classification = "internal") {
  const content = Buffer.from("context session fixture\n");
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt, requester: "pixel", profile: "builder", objective: "Continue the exact private project safely.",
    acceptanceCriteria: ["The private fixture is independently verified"], dataClassification: classification,
    verification: {
      mode: "independent", checks: [{ id: "patch-boundary", kind: "patch-integrity", criterionIndexes: [0] }],
      immutablePathPrefixes: [], maxRuntimeSeconds: 60, maxOutputBytes: 65536, network: "none",
      boundary: "Immutable controller-selected checks only. Worker output cannot alter criteria, commands, protected paths, budgets, network, or pass/fail rules.",
    },
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: hash(content), maxBytes: content.length, classification }],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools: ["read", "search", "write", "edit", "bash", "eval", "lsp", "debug", "task", "hub"],
      network: { mode: "brokered", services: ["local-model"] }, modelRoute: "local-only", hostAccess: false,
      ambientCredentials: false, externalEffects: false, mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: budgets(), outputs: { mode: "patch", requiredKinds: ["patch", "test-evidence"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

async function writePrivate(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-context-session-cli-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const stateRoot = join(root, "state"), files = join(root, "files");
  await mkdir(stateRoot, { mode: 0o700 }); await mkdir(files, { mode: 0o700 });
  if (process.platform !== "win32") { await chmod(stateRoot, 0o700); await chmod(files, 0o700); }
  const policy = JSON.parse(await (await import("node:fs/promises")).readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true; policy.runner.prepared = true; policy.localModel.prepared = true;
  policy.runner.imageDigest = `sha256:${digest("f")}`; policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.profiles.scout.enabled = true; policy.profiles.builder.enabled = true; policy.verifier.enabled = true;
  const compile = async (work, suffix, tick) => {
    const input = work.inputs[0], entries = [{ id: "source", kind: "repository-snapshot", objectName: `${input.contentSha256}.tar`, contentSha256: input.contentSha256, bytes: input.maxBytes, classification: input.classification, mountMode: "read-only" }];
    const compiled = compileBuilder(work, policy, entries, { now: new Date(baseTime + tick), suffix });
    const initialized = await initializeCheckpointLedger({ stateRoot, plan: compiled.plan, lease: compiled.lease, workspaceSnapshotSha256: digest("1"), now: new Date(baseTime + tick + 1), suffix });
    return { plan: compiled.plan, checkpoint: initialized.checkpoint };
  };
  const parent = await compile(job("work-1786449600000-abcdef123456", "2026-08-11T12:00:00Z"), "000000000001", 1);
  const child = await compile(job("work-1786449601000-fedcba654321", "2026-08-11T12:00:01Z"), "000000000002", 1001);
  const context = {
    $schema: "https://osmantic.com/pixel/schemas/work-context-session-input-v1.schema.json", schemaVersion: 1,
    summary: "Private Aurora detail remains local. Continue only from independently verified evidence.",
    decisions: [{ id: "keep-isolation", statement: "Keep the disposable workspace boundary.", evidenceSha256: digest("2") }],
    unresolvedRisks: [{ id: "verification-open", statement: "Independent semantic review is still required.", evidenceSha256: digest("3") }],
    artifacts: [{ path: "reports/status.json", sha256: digest("4"), bytes: 123, verified: true, verificationEvidenceSha256: digest("5") }],
  };
  const paths = {
    parentPlan: join(files, "parent-plan.json"), parentCheckpoint: join(files, "parent-checkpoint.json"),
    childPlan: join(files, "child-plan.json"), childCheckpoint: join(files, "child-checkpoint.json"), context: join(files, "context.json"),
  };
  await Promise.all([writePrivate(paths.parentPlan, parent.plan), writePrivate(paths.parentCheckpoint, parent.checkpoint), writePrivate(paths.childPlan, child.plan), writePrivate(paths.childCheckpoint, child.checkpoint), writePrivate(paths.context, context)]);
  return { root, stateRoot, paths, parent, child, context };
}

function createArgs(value, operation = "create-review") {
  return [operation, "--state-root", value.stateRoot, "--plan", value.paths.parentPlan, "--checkpoint", value.paths.parentCheckpoint, "--context", value.paths.context, "--expires-in-days", "30"];
}

function forkArgs(value, parent, operation = "fork-review") {
  return [operation, "--state-root", value.stateRoot, "--plan", value.paths.childPlan, "--checkpoint", value.paths.childCheckpoint, "--context", value.paths.context, "--expires-in-days", "30", "--parent-capsule", parent.path, "--parent-capsule-sha256", parent.sha256];
}

function removeArgs(value, capsule, operation = "remove-review") {
  return [operation, "--state-root", value.stateRoot, "--job-id", capsule.jobId, "--capsule-id", capsule.capsuleId, "--capsule-sha256", capsule.capsuleSha256];
}

test("owner can review, atomically create, find, inspect, and explicitly show one private session", async (t) => {
  const value = await fixture(t), review = await runContextSessionCommand(createArgs(value), { now: new Date(baseTime + 10) });
  assert.equal(review.status, "confirmation-required"); assert.equal(review.privateContentProjected, false);
  assert.doesNotMatch(JSON.stringify(review), /Aurora|disposable workspace|semantic review/u);
  const applyArgs = [...createArgs(value, "create-apply"), "--confirm-review-sha256", review.confirmation.sha256];
  const runs = await Promise.all(Array.from({ length: 8 }, () => runContextSessionCommand(applyArgs, { now: new Date(baseTime + 20), suffix: "000000000020" })));
  assert.equal(new Set(runs.map((entry) => entry.capsuleSha256)).size, 1);
  assert.equal(runs.filter((entry) => entry.action === "published").length, 1);
  assert.equal(runs.filter((entry) => entry.action === "already-published").length, 7);
  const applied = runs[0], path = join(value.stateRoot, "context-capsules", applied.jobId, `${applied.capsuleId}.json`);
  const listed = await runContextSessionCommand(["list", "--state-root", value.stateRoot], { now: new Date(baseTime + 30) });
  assert.equal(listed.sessions.length, 1); assert.equal(listed.sessions[0].state, "current"); assert.deepEqual(listed.removalRecovery, []); assert.doesNotMatch(JSON.stringify(listed), /Aurora|disposable workspace/u);
  const inspected = await runContextSessionCommand(["inspect", "--capsule", path, "--capsule-sha256", applied.capsuleSha256], { now: new Date(baseTime + 30) });
  assert.equal(inspected.privateContentProjected, false); assert.equal(inspected.inventory.artifacts, 1);
  const shown = await runContextSessionCommand(["show", "--capsule", path, "--capsule-sha256", applied.capsuleSha256], { now: new Date(baseTime + 30) });
  assert.equal(shown.privateContentProjected, true); assert.match(shown.privateContext.summary, /Aurora/u); assert.equal(shown.authority.grantsExecution, false);
  const removalReview = await runContextSessionCommand(removeArgs(value, applied), { now: new Date(baseTime + 40) });
  assert.equal(removalReview.status, "confirmation-required"); assert.equal(removalReview.secureErasureClaimed, false);
  const removalArgs = [...removeArgs(value, applied, "remove-apply"), "--confirm-review-sha256", removalReview.confirmation.sha256];
  const removals = await Promise.all(Array.from({ length: 8 }, () => runContextSessionCommand(removalArgs, { now: new Date(baseTime + 50) })));
  assert.ok(removals.every((entry) => entry.status === "removed" && entry.contentRetained === false && entry.secureErasureClaimed === false));
  assert.equal((await runContextSessionCommand(["list", "--state-root", value.stateRoot], { now: new Date(baseTime + 60) })).sessions.length, 0);
  await assert.rejects(() => runContextSessionCommand(["inspect", "--capsule", path, "--capsule-sha256", applied.capsuleSha256]), /ENOENT|read|regular file/u);
});

test("fork review creates new lineage without reusing authority and rejects stale confirmation or downgrade", async (t) => {
  const value = await fixture(t), rootReview = await runContextSessionCommand(createArgs(value), { now: new Date(baseTime + 10) });
  const rootApply = await runContextSessionCommand([...createArgs(value, "create-apply"), "--confirm-review-sha256", rootReview.confirmation.sha256], { now: new Date(baseTime + 20), suffix: "000000000020" });
  const parent = { path: join(value.stateRoot, "context-capsules", rootApply.jobId, `${rootApply.capsuleId}.json`), sha256: rootApply.capsuleSha256 };
  const review = await runContextSessionCommand(forkArgs(value, parent), { now: new Date(baseTime + 30) });
  assert.deepEqual(review.lineage, { action: "fork", parentJobId: value.parent.plan.jobId, depth: 1 });
  await assert.rejects(() => runContextSessionCommand([...forkArgs(value, parent, "fork-apply"), "--confirm-review-sha256", digest("9")], { now: new Date(baseTime + 40), suffix: "000000000040" }), /confirmation differs/u);
  const applied = await runContextSessionCommand([...forkArgs(value, parent, "fork-apply"), "--confirm-review-sha256", review.confirmation.sha256], { now: new Date(baseTime + 40), suffix: "000000000040" });
  assert.equal(applied.lineage.parentJobId, value.parent.plan.jobId); assert.equal(applied.lineage.depth, 1); assert.equal(applied.jobId, value.child.plan.jobId);
  const separateRoot = join(value.root, "separate-state"); await mkdir(separateRoot, { mode: 0o700 }); if (process.platform !== "win32") await chmod(separateRoot, 0o700);
  const crossRoot = forkArgs(value, parent); crossRoot[crossRoot.indexOf("--state-root") + 1] = separateRoot;
  await assert.rejects(() => runContextSessionCommand(crossRoot, { now: new Date(baseTime + 45) }), /not in this private state root/u);
  const downgraded = structuredClone(value.child.plan); downgraded.dataClassification = "public"; await writePrivate(value.paths.childPlan, downgraded);
  await assert.rejects(() => runContextSessionCommand(forkArgs(value, parent), { now: new Date(baseTime + 50) }), /classification|downgrade|checkpoint differs/u);
});

test("removal resumes after custody, live-unlink, and tombstone crash boundaries", async (t) => {
  for (const [index, hook] of ["afterCustody", "afterLiveUnlink", "afterTombstone"].entries()) {
    await t.test(hook, async (child) => {
      const value = await fixture(child), review = await runContextSessionCommand(createArgs(value), { now: new Date(baseTime + 100 + index) });
      const applied = await runContextSessionCommand([...createArgs(value, "create-apply"), "--confirm-review-sha256", review.confirmation.sha256], { now: new Date(baseTime + 200 + index), suffix: `00000000010${index}` });
      const removalReview = await runContextSessionCommand(removeArgs(value, applied), { now: new Date(baseTime + 300 + index) });
      const args = [...removeArgs(value, applied, "remove-apply"), "--confirm-review-sha256", removalReview.confirmation.sha256];
      await assert.rejects(() => runContextSessionCommand(args, { now: new Date(baseTime + 400 + index), [hook]: async () => { throw new Error(`forced-${hook}`); } }), new RegExp(`forced-${hook}`));
      const interrupted = await runContextSessionCommand(["list", "--state-root", value.stateRoot], { now: new Date(baseTime + 450 + index) });
      assert.equal(interrupted.status, "recovery-attention"); assert.equal(interrupted.removalRecovery.length, 1); assert.equal(interrupted.removalRecovery[0].capsuleSha256, applied.capsuleSha256);
      const recovered = await runContextSessionCommand(args, { now: new Date(baseTime + 500 + index) });
      assert.equal(recovered.status, "removed"); assert.equal(recovered.contentRetained, false);
      assert.equal((await runContextSessionCommand(["list", "--state-root", value.stateRoot], { now: new Date(baseTime + 600 + index) })).sessions.length, 0);
      const replay = await runContextSessionCommand(args, { now: new Date(baseTime + 700 + index) });
      assert.equal(replay.action, "already-removed");
    });
  }
});

test("hostile inputs, path substitution, corrupt inventories, and implicit content display fail closed", async (t) => {
  const value = await fixture(t), review = await runContextSessionCommand(createArgs(value), { now: new Date(baseTime + 10) });
  const changed = structuredClone(value.context); changed.summary = "Changed private context"; await writePrivate(value.paths.context, changed);
  await assert.rejects(() => runContextSessionCommand([...createArgs(value, "create-apply"), "--confirm-review-sha256", review.confirmation.sha256], { now: new Date(baseTime + 20), suffix: "000000000020" }), /confirmation differs/u);
  changed.unexpected = true; await writePrivate(value.paths.context, changed);
  await assert.rejects(() => runContextSessionCommand(createArgs(value), { now: new Date(baseTime + 30) }), /unexpected|additional/u);
  const missingArgs = createArgs(value); missingArgs[missingArgs.indexOf("--context") + 1] = join(value.root, "private-missing.json");
  let missingError; try { await runContextSessionCommand(missingArgs, { now: new Date(baseTime + 35) }); } catch (error) { missingError = error; }
  assert.match(missingError?.message ?? "", /could not be read safely/u); assert.doesNotMatch(missingError.message, new RegExp(value.root.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&"), "u"));
  if (process.platform !== "win32") {
    const linked = join(value.root, "linked-context.json"); await symlink(value.paths.context, linked);
    const args = createArgs(value); args[args.indexOf("--context") + 1] = linked;
    await assert.rejects(() => runContextSessionCommand(args, { now: new Date(baseTime + 40) }), /regular file|read/u);
  }
  await mkdir(join(value.stateRoot, "context-capsules"), { mode: 0o700 });
  if (process.platform !== "win32") await chmod(join(value.stateRoot, "context-capsules"), 0o700);
  await writePrivate(join(value.stateRoot, "context-capsules", "unexpected.json"), {});
  await assert.rejects(() => runContextSessionCommand(["list", "--state-root", value.stateRoot], { now: new Date(baseTime + 50) }), /unexpected entry/u);
  await assert.rejects(() => runContextSessionCommand(["show", "--capsule", value.paths.context, "--capsule-sha256", digest("0")]), /trusted hash/u);
});

test("plain-language guide shows a content-free review, requires the exact phrase, and can remove the result", async (t) => {
  const value = await fixture(t), output = [], options = createArgs(value).slice(1);
  const cancelled = await runContextSessionGuide(["create", ...options], { write: (text) => output.push(text), prompt: async () => "", contextDependencies: { now: new Date(baseTime + 800), suffix: "000000000800" } });
  assert.equal(cancelled.state, "cancelled"); assert.doesNotMatch(output.join(""), /Aurora|disposable workspace/u);
  const confirm = async (message) => message.match(/APPLY [a-f0-9]{12}/u)?.[0] ?? "";
  const created = await runContextSessionGuide(["create", ...options], { write: (text) => output.push(text), prompt: confirm, contextDependencies: { now: new Date(baseTime + 810), suffix: "000000000810" } });
  assert.equal(created.state, "completed"); assert.equal(created.result.status, "published");
  const removal = await runContextSessionGuide(["remove", ...removeArgs(value, created.result).slice(1)], { write: (text) => output.push(text), prompt: confirm, contextDependencies: { now: new Date(baseTime + 820) } });
  assert.equal(removal.result.status, "removed"); assert.match(output.join(""), /not a secure-erasure claim/u); assert.doesNotMatch(output.join(""), /Aurora|disposable workspace/u);
});
