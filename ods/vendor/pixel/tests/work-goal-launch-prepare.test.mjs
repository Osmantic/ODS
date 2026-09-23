import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runGoalDraftCommand } from "../deploy/work-controller/goal-draft-cli.mjs";
import { runGoalLaunchCommand, runGoalLaunchPrepareCommand } from "../deploy/work-controller/goal-launch-prepare-cli.mjs";
import { loadGoalCycleConfiguration } from "../deploy/work-controller/goal-cycle-cli.mjs";
import { canonical, validateWorkGoalLaunchPreparation } from "../scripts/lib/work-contract.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";

const draftTime = new Date("2026-08-11T18:00:00.000Z");
const launchTime = new Date("2026-08-11T18:00:02.000Z");
const stageTime = new Date("2026-08-11T18:00:03.000Z");
const nonce = "abcdefabcdefabcdefabcdefabcdefab";
const BRIEF_BOUNDARY = "Owner-authored plain-language goal structure and selected local input references only. Drafting derives bounded job proposals from private policy but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.";
const CATALOG_BOUNDARY = "Owner-selected content-addressed local input references only. The catalog grants no read, execution, lease, network, credential, external-effect, scope-expansion, or completion authority.";
const ENVIRONMENT_BOUNDARY = "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.";
const privateCanary = /PRIVATE_LAUNCH_OBJECTIVE|(?:^|["'\s])(?:\/tmp\/|[A-Za-z]:\\)/u;
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");

function octal(value, length) { return `${value.toString(8).padStart(length - 1, "0")}\0`; }
function header(name, size) {
  const value = Buffer.alloc(512); value.write(name, 0, 100, "utf8"); value.write(octal(0o644, 8), 100, 8, "ascii");
  value.write(octal(0, 8), 108, 8, "ascii"); value.write(octal(0, 8), 116, 8, "ascii"); value.write(octal(size, 12), 124, 12, "ascii");
  value.write(octal(0, 12), 136, 12, "ascii"); value.fill(0x20, 148, 156); value[156] = 0x30;
  value.write("ustar\0", 257, 6, "latin1"); value.write("00", 263, 2, "ascii");
  let checksum = 0; for (const byte of value) checksum += byte;
  value.write(`${checksum.toString(8).padStart(6, "0")}\0 `, 148, 8, "ascii"); return value;
}
function archive(name, text) {
  const content = Buffer.from(text), padding = Buffer.alloc((512 - (content.length % 512)) % 512);
  return Buffer.concat([header(name, content.length), content, padding, Buffer.alloc(1024)]);
}

async function fixture(t, { milestones = 2 } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-launch-")); t.after(() => rm(root, { recursive: true, force: true }));
  const paths = Object.fromEntries(["objects", "state", "workspaces", "bin"].map((name) => [name, join(root, name)]));
  await Promise.all(Object.values(paths).map((path) => mkdir(path, { mode: 0o700 })));
  if (process.platform !== "win32") { await chmod(root, 0o700); await Promise.all(Object.values(paths).map((path) => chmod(path, 0o700))); }
  const executor = Buffer.from("pinned launch OMP fixture\n"), docker = Buffer.from("pinned launch Docker fixture\n");
  const executorPath = join(paths.bin, "omp"), dockerPath = join(paths.bin, "docker");
  await writeFile(executorPath, executor, { mode: 0o500 }); await writeFile(dockerPath, docker, { mode: 0o500 });
  if (process.platform !== "win32") { await chmod(executorPath, 0o500); await chmod(dockerPath, 0o500); }
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true; policy.runner.prepared = true; policy.localModel.prepared = true; policy.verifier.enabled = true;
  policy.profiles.scout.enabled = true; policy.profiles.builder.enabled = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`; policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.executor.sha256 = sha(executor);
  await installFixtureModelQualification(policy, root, draftTime);
  const policyPath = join(root, "policy.json"); await writeFile(policyPath, `${JSON.stringify(policy)}\n`, { mode: 0o600 });
  const object = archive("source.txt", "bounded launch preparation fixture\n"), objectSha256 = sha(object);
  await writeFile(join(paths.objects, `${objectSha256}.tar`), object, { mode: 0o600 });
  const milestoneSet = milestones === 2 ? [
    { milestoneId: "assess", kind: "inspect", objective: "Inspect the exact local source.", doneWhen: ["Return independently checked findings"], dependsOn: [], inputIds: ["project"], effort: "quick" },
    { milestoneId: "build", kind: "build", objective: "Implement the reviewed improvement.", doneWhen: ["Return a bounded patch", "Pass independent checks"], dependsOn: ["assess"], inputIds: ["project"], effort: "quick" },
  ] : Array.from({ length: milestones }, (_, index) => ({
    milestoneId: `step-${String(index).padStart(2, "0")}`, kind: "inspect", objective: `Inspect bounded stage ${index}.`,
    doneWhen: [`Return independently checked findings for stage ${index}`],
    dependsOn: index === 0 ? [] : [`step-${String(index - 1).padStart(2, "0")}`], inputIds: ["project"], effort: "quick",
  }));
  const brief = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-brief-v1.schema.json", schemaVersion: 1,
    objective: "PRIVATE_LAUNCH_OBJECTIVE requires repeated inspected and implemented progress.", dataClassification: "internal",
    milestones: milestoneSet, boundary: BRIEF_BOUNDARY,
  };
  const catalog = {
    $schema: "https://osmantic.com/pixel/schemas/work-input-catalog-v1.schema.json", schemaVersion: 1,
    catalogId: "inputcatalog-1786471200000-abcdef123456", createdAt: draftTime.toISOString(),
    entries: [{ id: "project", kind: "repository-snapshot", objectName: `${objectSha256}.tar`, contentSha256: objectSha256, bytes: object.length, classification: "internal", mountMode: "read-only" }],
    boundary: CATALOG_BOUNDARY,
  };
  const briefPath = join(root, "brief.json"), catalogPath = join(root, "catalog.json"), draftPath = join(root, "draft");
  await writeFile(briefPath, `${JSON.stringify(brief)}\n`, { mode: 0o600 }); await writeFile(catalogPath, `${JSON.stringify(catalog)}\n`, { mode: 0o600 });
  const draftReceipt = await runGoalDraftCommand([
    "--brief", briefPath, "--policy", policyPath, "--input-catalog", catalogPath,
    "--object-store", paths.objects, "--output", draftPath,
  ], { now: draftTime, nonce });
  const environment = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-environment-v1.schema.json", schemaVersion: 1,
    stateRoot: paths.state, policyPath, objectStore: paths.objects, workspaceRoot: paths.workspaces, executorPath,
    archiveLimits: { maxEntries: 1000, maxFileBytes: 1048576 },
    runtime: { dockerPath, backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model", networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11", uid: process.geteuid?.() ?? 10001, gid: process.getegid?.() ?? 10001 },
    boundary: ENVIRONMENT_BOUNDARY,
  };
  const environmentPath = join(root, "environment.json"); await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  return { root, paths, policyPath, policy, objectSha256, object, draftPath, draftReceipt, environmentPath };
}

function args(value, output) {
  return ["--draft", value.draftPath, "--environment", value.environmentPath, "--confirm-draft-sha256", value.draftReceipt.draftSha256, "--output", output];
}
function dependencies(extra = {}) {
  return { now: launchTime, goalSuffix: "111111111111", childSuffix: (_jobId, index) => (index + 1).toString(16).padStart(12, "0"), ...extra };
}

test("one reviewed draft atomically prepares every child and remains inactive until separate staging", async (t) => {
  const value = await fixture(t), output = join(value.root, "launch");
  const receipt = await runGoalLaunchCommand(["prepare", ...args(value, output)], dependencies());
  assert.equal(receipt.state, "prepared-inactive"); assert.equal(receipt.children, 2);
  assert.deepEqual(receipt.profiles, ["builder", "scout"]); assert.equal(receipt.authority.containsExpiringChildLeases, true);
  assert.ok(Object.entries(receipt.authority).filter(([key]) => key !== "containsExpiringChildLeases").every(([, entry]) => entry === false));
  assert.doesNotMatch(JSON.stringify(receipt), privateCanary);
  assert.deepEqual((await readdir(output)).sort(), ["assembly", "compiled-jobs", "launch-preparation.json"]);
  const preparation = JSON.parse(await readFile(join(output, "launch-preparation.json"), "utf8"));
  assert.deepEqual(validateWorkGoalLaunchPreparation(preparation), []);
  const widened = structuredClone(preparation); widened.authority.grantsExecution = true;
  assert.ok(validateWorkGoalLaunchPreparation(widened).some((error) => /grantsExecution/u.test(error)));
  const expired = structuredClone(preparation); expired.children[0].expiresAt = expired.preparedAt;
  assert.ok(validateWorkGoalLaunchPreparation(expired).some((error) => /expire after preparation/u.test(error)));
  assert.equal(preparation.bindings.compiledJobsSha256, receipt.compiledJobsSha256);
  assert.equal(receipt.manifestSha256, sha(await readFile(join(output, "launch-preparation.json"))));
  const compiledNames = (await readdir(join(output, "compiled-jobs"))).sort();
  assert.deepEqual(compiledNames, preparation.children.map((child) => child.jobId));
  for (const jobId of compiledNames) assert.deepEqual((await readdir(join(output, "compiled-jobs", jobId))).sort(), ["lease.json", "plan.json"]);
  assert.deepEqual(await readdir(value.paths.state), [], "launch preparation must create no goal custody");
  const loaded = await loadGoalCycleConfiguration(join(output, "assembly", "controller", "controller.json"));
  assert.equal(loaded.config.goalPath, join(output, "assembly", "controller", "goal.json"));
  const inspected = await runGoalLaunchCommand(["inspect", "--bundle", output]);
  assert.equal(inspected.state, "package-verified"); assert.equal(inspected.manifestSha256, receipt.manifestSha256);
  assert.match(inspected.boundary, /does not assert current goal or service state/u);
  await assert.rejects(runGoalLaunchCommand(["stage", "--bundle", output, "--confirm-manifest-sha256", "0".repeat(64)]), /confirmation differs/);
  assert.deepEqual(await readdir(value.paths.state), []);
  const stageDependencies = { now: stageTime, childSuffix: (_jobId, index) => `3${index}`.padEnd(12, "3"), goalSuffix: "444444444444", stageSuffix: "555555555555" };
  const staged = await runGoalLaunchCommand(["stage", "--bundle", output, "--confirm-manifest-sha256", inspected.manifestSha256], { stage: stageDependencies });
  assert.equal(staged.state, "staged-inactive"); assert.equal(staged.children, 2);
  const repeated = await runGoalLaunchCommand(["stage", "--bundle", output, "--confirm-manifest-sha256", inspected.manifestSha256], { stage: stageDependencies });
  assert.equal(repeated.action, "already-staged");
  assert.deepEqual(await readdir(value.paths.workspaces), []);
});

test("launch preparation preserves deterministic nested assembly errors", async (t) => {
  const value = await fixture(t), environment = JSON.parse(await readFile(value.environmentPath, "utf8"));
  environment.executorPath = join(value.root, "missing-omp");
  await writeFile(value.environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  await assert.rejects(
    runGoalLaunchPrepareCommand(args(value, join(value.root, "nested-assembly-error")), dependencies()),
    /goal launch assembly failed: pinned OMP executor is not a bounded single-link regular file/u,
  );
});

test("published launch inspection rejects package, policy, and input drift before staging", async (t) => {
  const planDrift = await fixture(t), planOutput = join(planDrift.root, "plan-drift");
  await runGoalLaunchPrepareCommand(args(planDrift, planOutput), dependencies());
  const first = (await readdir(join(planOutput, "compiled-jobs"))).sort()[0];
  const planPath = join(planOutput, "compiled-jobs", first, "plan.json"), plan = JSON.parse(await readFile(planPath, "utf8"));
  plan.objective = "published substitution"; await writeFile(planPath, `${JSON.stringify(plan)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalLaunchCommand(["inspect", "--bundle", planOutput]), /compiled child.*differs before publication/);

  const policyDrift = await fixture(t), policyOutput = join(policyDrift.root, "published-policy-drift");
  await runGoalLaunchPrepareCommand(args(policyDrift, policyOutput), dependencies());
  policyDrift.policy.maxLeaseSeconds -= 1; await writeFile(policyDrift.policyPath, `${JSON.stringify(policyDrift.policy)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalLaunchCommand(["inspect", "--bundle", policyOutput]), /policy differs from the published package/);

  const inputDrift = await fixture(t), inputOutput = join(inputDrift.root, "published-input-drift");
  await runGoalLaunchPrepareCommand(args(inputDrift, inputOutput), dependencies());
  await writeFile(join(inputDrift.paths.objects, `${inputDrift.objectSha256}.tar`), Buffer.concat([inputDrift.object, Buffer.from("tamper")]), { mode: 0o600 });
  await assert.rejects(runGoalLaunchCommand(["inspect", "--bundle", inputOutput]), /published input verification failed/);
});

test("launch preparation rejects stale confirmation, policy drift, object replacement, and prepared-byte tamper", async (t) => {
  const stale = await fixture(t), wrong = [...args(stale, join(stale.root, "wrong"))]; wrong[5] = "0".repeat(64);
  await assert.rejects(runGoalLaunchPrepareCommand(wrong, dependencies()), /confirmation differs/);

  const policyDrift = await fixture(t); policyDrift.policy.maxLeaseSeconds -= 1;
  await writeFile(policyDrift.policyPath, `${JSON.stringify(policyDrift.policy)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalLaunchPrepareCommand(args(policyDrift, join(policyDrift.root, "policy-drift")), dependencies()), /policy differs|hash differs/);

  const objectDrift = await fixture(t); await writeFile(join(objectDrift.paths.objects, `${objectDrift.objectSha256}.tar`), Buffer.concat([objectDrift.object, Buffer.from("tamper")]), { mode: 0o600 });
  await assert.rejects(runGoalLaunchPrepareCommand(args(objectDrift, join(objectDrift.root, "object-drift")), dependencies()), /input verification failed/);

  const preparedTamper = await fixture(t), tamperedOutput = join(preparedTamper.root, "prepared-tamper");
  await assert.rejects(runGoalLaunchPrepareCommand(args(preparedTamper, tamperedOutput), dependencies({
    afterAssembly: async ({ assemblyPath }) => {
      const path = join(assemblyPath, "controller", "controller.json"), config = JSON.parse(await readFile(path, "utf8"));
      config.controller.maxTransitions = 2; await writeFile(path, `${JSON.stringify(config)}\n`, { mode: 0o600 });
    },
  })), /prepared assembly is invalid|bindings differ/);
  assert.equal(await lstat(tamperedOutput).catch(() => null), null);

  const compiledTamper = await fixture(t), compiledOutput = join(compiledTamper.root, "compiled-tamper");
  await assert.rejects(runGoalLaunchPrepareCommand(args(compiledTamper, compiledOutput), dependencies({
    afterChildCompiled: async ({ index }) => {
      if (index !== 1) return;
      const temporary = (await readdir(compiledTamper.root)).find((name) => name.startsWith(".pixel-work-launch-"));
      const first = (await readdir(join(compiledTamper.root, temporary, "compiled-jobs"))).sort()[0];
      const path = join(compiledTamper.root, temporary, "compiled-jobs", first, "plan.json"), plan = JSON.parse(await readFile(path, "utf8"));
      plan.objective = "same-user temporary substitution"; await writeFile(path, `${JSON.stringify(plan)}\n`, { mode: 0o600 });
    },
  })), /compiled child.*differs before publication/);
  assert.equal(await lstat(compiledOutput).catch(() => null), null);

  const latePolicy = await fixture(t), lateOutput = join(latePolicy.root, "late-policy");
  await assert.rejects(runGoalLaunchPrepareCommand(args(latePolicy, lateOutput), dependencies({
    afterChildCompiled: async ({ index }) => {
      if (index !== 1) return;
      latePolicy.policy.maxLeaseSeconds -= 1;
      await writeFile(latePolicy.policyPath, `${JSON.stringify(latePolicy.policy)}\n`, { mode: 0o600 });
    },
  })), /policy changed before publication/);
  assert.equal(await lstat(lateOutput).catch(() => null), null);

  if (process.platform !== "win32") {
    const linked = await fixture(t), linkedOutput = join(linked.root, "linked-plan");
    await assert.rejects(runGoalLaunchPrepareCommand(args(linked, linkedOutput), dependencies({
      afterChildCompiled: async ({ index }) => {
        if (index !== 1) return;
        const temporary = (await readdir(linked.root)).find((name) => name.startsWith(".pixel-work-launch-"));
        const first = (await readdir(join(linked.root, temporary, "compiled-jobs"))).sort()[0];
        await link(join(linked.root, temporary, "compiled-jobs", first, "plan.json"), join(linked.root, "second-plan-link.json"));
      },
    })), /single-link/);
    assert.equal(await lstat(linkedOutput).catch(() => null), null);
  }
});

test("maximum-size guided preparation compiles a 64-stage continuous objective without scheduling", async (t) => {
  const value = await fixture(t, { milestones: 64 }), output = join(value.root, "maximum-launch");
  const receipt = await runGoalLaunchPrepareCommand(args(value, output), dependencies());
  assert.equal(receipt.children, 64); assert.deepEqual(receipt.profiles, ["scout"]);
  assert.equal((await readdir(join(output, "compiled-jobs"))).length, 64);
  const preparation = JSON.parse(await readFile(join(output, "launch-preparation.json"), "utf8"));
  assert.deepEqual(validateWorkGoalLaunchPreparation(preparation), []);
  assert.equal(preparation.children.length, 64); assert.deepEqual(await readdir(value.paths.state), []);
});

test("launch preparation cleans interrupted work and concurrent attempts publish one complete package", async (t) => {
  const value = await fixture(t), interrupted = join(value.root, "interrupted");
  await assert.rejects(runGoalLaunchPrepareCommand(args(value, interrupted), dependencies({
    afterChildCompiled: ({ index }) => { if (index === 0) throw new Error("forced launch preparation interruption"); },
  })), /forced launch preparation interruption/);
  assert.equal(await lstat(interrupted).catch(() => null), null);
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-work-launch-")), []);

  const output = join(value.root, "race");
  const outcomes = await Promise.allSettled(Array.from({ length: 8 }, () => runGoalLaunchPrepareCommand(args(value, output), dependencies())));
  assert.equal(outcomes.filter((entry) => entry.status === "fulfilled").length, 1);
  assert.ok(outcomes.filter((entry) => entry.status === "rejected").every((entry) => /already exists/u.test(entry.reason.message)));
  assert.deepEqual((await readdir(output)).sort(), ["assembly", "compiled-jobs", "launch-preparation.json"]);
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-work-launch-")), []);
});
