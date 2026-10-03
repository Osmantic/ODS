import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runGoalAssembleCommand } from "../deploy/work-controller/goal-assemble-cli.mjs";
import { runGoalDraftCommand } from "../deploy/work-controller/goal-draft-cli.mjs";
import { loadGoalCycleConfiguration } from "../deploy/work-controller/goal-cycle-cli.mjs";
import { canonical, validateWorkGoalAssembly } from "../scripts/lib/work-contract.mjs";

const draftTime = new Date("2026-08-11T17:00:00.000Z");
const assemblyTime = new Date("2026-08-11T17:00:01.000Z");
const nonce = "abcdefabcdefabcdefabcdefabcdefab";
const BRIEF_BOUNDARY = "Owner-authored plain-language goal structure and selected local input references only. Drafting derives bounded job proposals from private policy but grants no execution, lease, retry, scheduling, credential, scope expansion, external effect, or completion authority.";
const CATALOG_BOUNDARY = "Owner-selected content-addressed local input references only. The catalog grants no read, execution, lease, network, credential, external-effect, scope-expansion, or completion authority.";
const ENVIRONMENT_BOUNDARY = "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.";
const privateCanary = /PRIVATE_ASSEMBLY_OBJECTIVE|(?:^|["'\s])(?:\/tmp\/|[A-Za-z]:\\)/u;
const sha = (value) => createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-assemble-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const paths = Object.fromEntries(["objects", "state", "workspaces", "bin"].map((name) => [name, join(root, name)]));
  await Promise.all(Object.values(paths).map((path) => mkdir(path, { mode: 0o700 })));
  if (process.platform !== "win32") {
    await chmod(root, 0o700); await Promise.all(Object.values(paths).map((path) => chmod(path, 0o700)));
  }
  const executor = Buffer.from("pinned assembly OMP fixture\n"), docker = Buffer.from("pinned assembly Docker fixture\n");
  const executorPath = join(paths.bin, "omp"), dockerPath = join(paths.bin, "docker");
  await writeFile(executorPath, executor, { mode: 0o500 }); await writeFile(dockerPath, docker, { mode: 0o500 });
  if (process.platform !== "win32") { await chmod(executorPath, 0o500); await chmod(dockerPath, 0o500); }
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true; policy.runner.prepared = true; policy.localModel.prepared = true; policy.verifier.enabled = true;
  policy.profiles.scout.enabled = true; policy.runner.imageDigest = `sha256:${"f".repeat(64)}`;
  policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`;
  policy.executor.sha256 = sha(executor);
  const policyPath = join(root, "policy.json"); await writeFile(policyPath, `${JSON.stringify(policy)}\n`, { mode: 0o600 });
  const brief = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-brief-v1.schema.json", schemaVersion: 1,
    objective: "PRIVATE_ASSEMBLY_OBJECTIVE survives many bounded controller turns.", dataClassification: "internal",
    milestones: [{ milestoneId: "inspect", kind: "inspect", objective: "Inspect the bounded source over time.", doneWhen: ["Return an independently checked finding report"], dependsOn: [], inputIds: [], effort: "quick" }],
    boundary: BRIEF_BOUNDARY,
  };
  const catalog = {
    $schema: "https://osmantic.com/pixel/schemas/work-input-catalog-v1.schema.json", schemaVersion: 1,
    catalogId: "inputcatalog-1786467600000-abcdef123456", createdAt: draftTime.toISOString(), entries: [], boundary: CATALOG_BOUNDARY,
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
    runtime: {
      dockerPath, backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model",
      networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11",
      uid: process.geteuid?.() ?? 10001, gid: process.getegid?.() ?? 10001,
    },
    boundary: ENVIRONMENT_BOUNDARY,
  };
  const environmentPath = join(root, "environment.json"); await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  return { root, paths, brief, draftPath, draftReceipt, environmentPath };
}

function args(value, output) {
  return ["--draft", value.draftPath, "--environment", value.environmentPath, "--confirm-draft-sha256", value.draftReceipt.draftSha256, "--output", output];
}

test("one exact confirmation atomically assembles an inert draft and controller with final path bindings", async (t) => {
  const value = await fixture(t), output = join(value.root, "assembled");
  const receipt = await runGoalAssembleCommand(args(value, output), { now: assemblyTime, suffix: "123456abcdef" });
  assert.equal(receipt.state, "assembled-inert"); assert.equal(receipt.milestones, 1);
  assert.ok(Object.values(receipt.authority).every((entry) => entry === false));
  assert.doesNotMatch(JSON.stringify(receipt), privateCanary);
  assert.deepEqual((await readdir(output)).sort(), ["assembly.json", "controller", "goal", "review.json"]);
  const assembly = JSON.parse(await readFile(join(output, "assembly.json"), "utf8"));
  assert.deepEqual(validateWorkGoalAssembly(assembly), []);
  const widened = structuredClone(assembly); widened.authority.grantsExecution = true;
  assert.ok(validateWorkGoalAssembly(widened).some((error) => /grantsExecution/u.test(error)));
  assert.equal(assembly.bindings.draftSha256, value.draftReceipt.draftSha256);
  assert.equal(sha(JSON.parse(await readFile(join(output, "review.json"), "utf8"))), value.draftReceipt.draftSha256);
  const controllerManifest = JSON.parse(await readFile(join(output, "controller", "controller-bundle.json"), "utf8"));
  assert.equal(assembly.bindings.controllerBundleSha256, sha(controllerManifest));
  const loaded = await loadGoalCycleConfiguration(join(output, "controller", "controller.json"));
  assert.equal(loaded.config.goalPath, join(output, "controller", "goal.json"));
  assert.equal(loaded.config.jobsPath, join(output, "controller", "jobs.json"));
  assert.deepEqual(await readdir(value.paths.state), [], "inert assembly must not create controller custody");
  if (process.platform !== "win32") {
    assert.equal((await lstat(output)).mode & 0o077, 0);
    assert.equal((await lstat(join(output, "assembly.json"))).mode & 0o077, 0);
  }
});

test("assembly rejects stale review, draft mutation, links, unexpected files, and path overlap", async (t) => {
  const stale = await fixture(t);
  await assert.rejects(runGoalAssembleCommand([
    "--draft", stale.draftPath, "--environment", stale.environmentPath, "--confirm-draft-sha256", "0".repeat(64), "--output", join(stale.root, "stale"),
  ]), /confirmation differs/);
  const draft = JSON.parse(await readFile(join(stale.draftPath, "goal-draft.json"), "utf8")); draft.goal.objective = "substituted";
  await writeFile(join(stale.draftPath, "goal-draft.json"), `${JSON.stringify(draft)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalAssembleCommand(args(stale, join(stale.root, "mutated"))), /confirmation differs/);

  const unexpected = await fixture(t); await writeFile(join(unexpected.draftPath, "extra.json"), "{}\n", { mode: 0o600 });
  await assert.rejects(runGoalAssembleCommand(args(unexpected, join(unexpected.root, "unexpected"))), /file set is invalid/);
  const overlap = await fixture(t);
  await assert.rejects(runGoalAssembleCommand(args(overlap, join(overlap.draftPath, "assembled"))), /overlap/);
  if (process.platform !== "win32") {
    const linked = await fixture(t), original = join(linked.draftPath, "goal-declaration.json"), secondLink = join(linked.root, "second-declaration-link.json");
    await link(original, secondLink);
    await assert.rejects(runGoalAssembleCommand(args(linked, join(linked.root, "linked-output"))), /single-link/);
  }
});

test("assembly closes same-user draft edits between confirmation and preparation", async (t) => {
  const value = await fixture(t), output = join(value.root, "raced");
  await assert.rejects(runGoalAssembleCommand(args(value, output), {
    now: assemblyTime, suffix: "456789abcdef", afterDraftValidated: async () => {
      const path = join(value.draftPath, "goal-declaration.json"), declaration = JSON.parse(await readFile(path, "utf8"));
      declaration.objective = "same-user mutation after exact review";
      await writeFile(path, `${JSON.stringify(declaration)}\n`, { mode: 0o600 });
    },
  }), /prepared goal differs from the exact confirmed draft/);
  assert.equal(await lstat(output).catch(() => null), null);
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-work-assembly-")), []);
});

test("assembly rejects post-preparation controller substitution before publication", async (t) => {
  const value = await fixture(t), output = join(value.root, "controller-raced");
  await assert.rejects(runGoalAssembleCommand(args(value, output), {
    now: assemblyTime, suffix: "56789abcdef0", afterControllerPrepared: async () => {
      const staging = (await readdir(value.root)).find((name) => name.startsWith(".pixel-work-assembly-"));
      const path = join(value.root, staging, "controller", "controller.json"), config = JSON.parse(await readFile(path, "utf8"));
      config.controller.maxTransitions = 2;
      await writeFile(path, `${JSON.stringify(config)}\n`, { mode: 0o600 });
    },
  }), /prepared controller differs from the exact confirmed goal/);
  assert.equal(await lstat(output).catch(() => null), null);
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-work-assembly-")), []);
});

test("assembly removes interrupted staging and concurrent attempts publish one complete bundle", async (t) => {
  const value = await fixture(t), interrupted = join(value.root, "interrupted");
  await assert.rejects(runGoalAssembleCommand(args(value, interrupted), {
    now: assemblyTime, suffix: "234567abcdef", afterGoalPrepared: () => { throw new Error("forced assembly interruption"); },
  }), /forced assembly interruption/);
  assert.equal(await lstat(interrupted).catch(() => null), null);
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-work-assembly-")), []);

  const output = join(value.root, "race");
  const outcomes = await Promise.allSettled(Array.from({ length: 16 }, () => runGoalAssembleCommand(args(value, output), { now: assemblyTime, suffix: "345678abcdef" })));
  assert.equal(outcomes.filter((entry) => entry.status === "fulfilled").length, 1);
  assert.ok(outcomes.filter((entry) => entry.status === "rejected").every((entry) => /already exists/u.test(entry.reason.message)));
  assert.deepEqual((await readdir(output)).sort(), ["assembly.json", "controller", "goal", "review.json"]);
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-work-assembly-")), []);
});
