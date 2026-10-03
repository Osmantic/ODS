import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileScout } from "../deploy/work-broker/broker.mjs";
import { runGoalControllerPrepareCommand } from "../deploy/work-controller/goal-controller-prepare-cli.mjs";
import { runGoalPrepareCommand } from "../deploy/work-controller/goal-prepare-cli.mjs";
import { runGoalStageCommand } from "../deploy/work-controller/goal-stage-cli.mjs";
import { recoverGoalRunBundles } from "../deploy/work-controller/goal-run-bundles.mjs";
import { recoverGoalLedger } from "../deploy/work-controller/goals.mjs";
import { canonical, validateWorkGoalStage } from "../scripts/lib/work-contract.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";

const baseTime = Date.parse("2026-08-11T12:00:00Z");
const hash = (value) => createHash("sha256").update(value).digest("hex");
const declarationBoundary = "Owner-authored long-horizon structure only. Preparation may derive immutable hashes and aggregate budgets but grants no execution, lease, retry, credential, scope expansion, external effect, or completion authority.";
const environmentBoundary = "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.";

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

function job(index, archiveSha256, archiveBytes) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: `work-${String(baseTime + index).padStart(13, "0")}-${(0xabcdef123450n + BigInt(index)).toString(16)}`,
    createdAt: new Date(baseTime + index).toISOString(), requester: "pixel", profile: "scout",
    objective: `Inspect bounded local source ${index}.`, acceptanceCriteria: [`Finding report ${index} is independently verified`], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: archiveSha256, maxBytes: archiveBytes, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: {
      maxRuntimeSeconds: 60, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1, maxModelRequests: 5,
      maxInputTokens: 10000, maxOutputTokens: 2000, maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 1048576,
      maxArtifactBytes: 65536, maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1,
    },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-stage-")); t.after(() => rm(root, { recursive: true, force: true }));
  const paths = Object.fromEntries(["state", "objects", "workspaces", "bin", "compiled"].map((name) => [name, join(root, name)]));
  await Promise.all(Object.values(paths).map((path) => mkdir(path, { mode: 0o700 })));
  if (process.platform !== "win32") { await chmod(root, 0o700); await Promise.all(Object.values(paths).map((path) => chmod(path, 0o700))); }
  const archiveBytes = archive("source.txt", "bounded stage fixture\n"), archiveSha256 = hash(archiveBytes);
  await writeFile(join(paths.objects, `${archiveSha256}.tar`), archiveBytes, { mode: 0o600 });
  const executor = Buffer.from("pinned stage omp\n"), docker = Buffer.from("pinned stage docker\n");
  const executorPath = join(paths.bin, "omp"), dockerPath = join(paths.bin, "docker");
  await writeFile(executorPath, executor, { mode: 0o500 }); await writeFile(dockerPath, docker, { mode: 0o500 });
  if (process.platform !== "win32") { await chmod(executorPath, 0o500); await chmod(dockerPath, 0o500); }
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.enabled = true; policy.runner.prepared = true; policy.localModel.prepared = true; policy.profiles.scout.enabled = true;
  policy.runner.imageDigest = `sha256:${"f".repeat(64)}`; policy.runner.imageRef = `ghcr.io/osmantic/pixel-work-runner@${policy.runner.imageDigest}`; policy.executor.sha256 = hash(executor);
  await installFixtureModelQualification(policy, root, new Date(baseTime));
  const policyPath = join(root, "policy.json"); await writeFile(policyPath, `${JSON.stringify(policy)}\n`, { mode: 0o600 });
  const jobs = [job(0, archiveSha256, archiveBytes.length), job(1, archiveSha256, archiveBytes.length)];
  const declaration = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-declaration-v1.schema.json", schemaVersion: 1,
    objective: "Complete both bounded inspections across durable restarts.", dataClassification: "internal",
    milestones: [
      { milestoneId: "first", jobId: jobs[0].jobId, dependsOn: [] },
      { milestoneId: "second", jobId: jobs[1].jobId, dependsOn: ["first"] },
    ], boundary: declarationBoundary,
  };
  const declarationPath = join(root, "declaration.json"), jobsPath = join(root, "jobs-source.json"), goalBundle = join(root, "goal-bundle");
  await writeFile(declarationPath, `${JSON.stringify(declaration)}\n`, { mode: 0o600 }); await writeFile(jobsPath, `${JSON.stringify(jobs)}\n`, { mode: 0o600 });
  await runGoalPrepareCommand(["--declaration", declarationPath, "--jobs", jobsPath, "--output", goalBundle], { now: new Date(baseTime + 10), suffix: "a10000000001" });
  const environment = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-environment-v1.schema.json", schemaVersion: 1,
    stateRoot: paths.state, policyPath, objectStore: paths.objects, workspaceRoot: paths.workspaces, executorPath,
    archiveLimits: { maxEntries: 100, maxFileBytes: 1048576 },
    runtime: { dockerPath, backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model", networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11", uid: process.geteuid?.() ?? 10001, gid: process.getegid?.() ?? 10001 },
    boundary: environmentBoundary,
  };
  const environmentPath = join(root, "environment.json"), controllerBundle = join(root, "controller-bundle");
  await writeFile(environmentPath, `${JSON.stringify(environment)}\n`, { mode: 0o600 });
  await runGoalControllerPrepareCommand(["--goal-bundle", goalBundle, "--environment", environmentPath, "--output", controllerBundle]);
  const entries = jobs.map((child) => [{ id: "source", kind: "repository-snapshot", objectName: `${archiveSha256}.tar`, contentSha256: archiveSha256, bytes: archiveBytes.length, classification: "internal", mountMode: "read-only" }]);
  for (let index = 0; index < jobs.length; index += 1) {
    const compiled = compileScout(jobs[index], policy, entries[index], { now: new Date(baseTime + 20 + index), suffix: `a2000000000${index + 1}` });
    const directory = join(paths.compiled, jobs[index].jobId); await mkdir(directory, { mode: 0o700 });
    await writeFile(join(directory, "plan.json"), `${JSON.stringify(compiled.plan, null, 2)}\n`, { mode: 0o600 });
    await writeFile(join(directory, "lease.json"), `${JSON.stringify(compiled.lease, null, 2)}\n`, { mode: 0o600 });
  }
  const controllerManifest = JSON.parse(await readFile(join(controllerBundle, "controller-bundle.json"), "utf8"));
  return { root, paths, jobs, controllerBundle, confirmation: hash(canonical(controllerManifest)), archiveBytes, archiveSha256 };
}

function args(value) { return ["--controller-bundle", value.controllerBundle, "--compiled-jobs", value.paths.compiled, "--confirm-controller-sha256", value.confirmation]; }
function dependencies() { return { now: new Date(baseTime + 30), childSuffix: (_jobId, index) => `a3000000000${index + 1}`, goalSuffix: "a40000000001", stageSuffix: "a50000000001" }; }

test("goal staging creates exact dormant child custody and remains idempotent without execution", async (t) => {
  const value = await fixture(t), receipt = await runGoalStageCommand(args(value), dependencies());
  assert.equal(receipt.action, "staged"); assert.equal(receipt.state, "staged-inactive"); assert.equal(receipt.children, 2);
  assert.deepEqual(Object.values(receipt.authority), [true, true, false, false, false, false, false]);
  assert.ok(!JSON.stringify(receipt).includes(value.root)); assert.ok(!JSON.stringify(receipt).includes(value.jobs[0].objective));
  const goal = JSON.parse(await readFile(join(value.controllerBundle, "goal.json"), "utf8"));
  const jobs = JSON.parse(await readFile(join(value.controllerBundle, "jobs.json"), "utf8"));
  const goalLedger = await recoverGoalLedger({ stateRoot: value.paths.state, goal, jobs });
  assert.equal(goalLedger.head.state, "ready"); assert.equal(goalLedger.checkpoints.length, 1);
  for (const child of value.jobs) {
    const custody = await recoverGoalRunBundles({ stateRoot: value.paths.state, goal, jobs, jobId: child.jobId });
    assert.equal(custody.bundles.length, 1); assert.equal(custody.head.purpose, "initial");
  }
  assert.deepEqual(await readdir(value.paths.workspaces), []);
  const stage = JSON.parse(await readFile(join(value.paths.state, "goal-staging", goal.goalId, "stage.json"), "utf8"));
  assert.deepEqual(validateWorkGoalStage(stage), []);
  const repeated = await runGoalStageCommand(args(value), dependencies()); assert.equal(repeated.action, "already-staged");
  await rm(join(value.paths.state, "goal-checkpoints"), { recursive: true, force: true });
  await assert.rejects(runGoalStageCommand(args(value), dependencies()), /goal state root|goal checkpoint|directory/u);
});

test("goal staging rejects confirmation, compiled authority, and object substitution before state creation", async (t) => {
  const value = await fixture(t), wrong = [...args(value)]; wrong[5] = "0".repeat(64);
  await assert.rejects(runGoalStageCommand(wrong, dependencies()), /confirmation differs/);
  const leasePath = join(value.paths.compiled, value.jobs[0].jobId, "lease.json"), exactLease = await readFile(leasePath);
  const lease = JSON.parse(exactLease.toString("utf8")); lease.budgets.maxRuntimeSeconds += 1;
  await writeFile(leasePath, `${JSON.stringify(lease)}\n`, { mode: 0o600 });
  await assert.rejects(runGoalStageCommand(args(value), dependencies()), /compiled goal child.*differs/);
  await writeFile(leasePath, exactLease, { mode: 0o600 });
  await writeFile(join(value.paths.objects, `${value.archiveSha256}.tar`), Buffer.concat([value.archiveBytes, Buffer.from("tamper")]), { mode: 0o600 });
  await assert.rejects(runGoalStageCommand(args(value), dependencies()), /input object.*changed|SHA-256|byte|archive must be a bounded/u);
  await writeFile(join(value.paths.objects, `${value.archiveSha256}.tar`), value.archiveBytes, { mode: 0o600 });
  const planPath = join(value.paths.compiled, value.jobs[0].jobId, "plan.json"), linkedPlan = join(value.root, "linked-plan.json");
  await link(planPath, linkedPlan);
  await assert.rejects(runGoalStageCommand(args(value), dependencies()), /single-link/);
  await unlink(linkedPlan);
  await assert.rejects(runGoalStageCommand(args(value), { ...dependencies(), now: new Date(baseTime + 7200000) }), /currently valid/u);
  assert.deepEqual(await readdir(value.paths.state), []); assert.deepEqual(await readdir(value.paths.workspaces), []);
});

test("goal staging resumes an interrupted partial custody set without widening or replay", async (t) => {
  const value = await fixture(t), options = dependencies(); let interrupted = false;
  await assert.rejects(runGoalStageCommand(args(value), {
    ...options, afterChildInitialized: ({ index }) => { if (index === 0 && !interrupted) { interrupted = true; throw new Error("synthetic staging loss"); } },
  }), /synthetic staging loss/);
  assert.ok((await readdir(value.paths.state)).includes("goal-runs")); assert.ok(!(await readdir(value.paths.state)).includes("goal-checkpoints"));
  const recovered = await runGoalStageCommand(args(value), options); assert.equal(recovered.action, "staged");
});

test("competing goal staging commands converge on one exact dormant record", async (t) => {
  const value = await fixture(t), attempts = await Promise.allSettled(Array.from({ length: 8 }, () => runGoalStageCommand(args(value), dependencies())));
  assert.equal(attempts.filter((attempt) => attempt.status === "rejected").length, 0);
  assert.equal(attempts.filter((attempt) => attempt.value.action === "staged").length, 1);
  assert.equal(attempts.filter((attempt) => attempt.value.action === "already-staged").length, 7);
  assert.deepEqual(await readdir(value.paths.workspaces), []);
  assert.ok((await readdir(join(value.paths.state, "goal-staging"))).every((name) => !name.startsWith(".stage-")));
});
