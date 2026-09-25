import assert from "node:assert/strict";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { compileGoalDeclaration, runGoalPrepareCommand } from "../deploy/work-controller/goal-prepare-cli.mjs";
import { validateWorkGoal, validateWorkGoalBundleManifest, validateWorkGoalDeclaration } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-11T12:00:00Z");
const digest = (character) => character.repeat(64);
const declarationBoundary = "Owner-authored long-horizon structure only. Preparation may derive immutable hashes and aggregate budgets but grants no execution, lease, retry, credential, scope expansion, external effect, or completion authority.";

function job(index) {
  const createdAt = new Date(baseTime + index).toISOString();
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId: `work-${String(baseTime + index).padStart(13, "0")}-${(0xabcde0000000n + BigInt(index)).toString(16)}`,
    createdAt, requester: "pixel", profile: "scout", objective: `Inspect bounded milestone ${index}.`,
    acceptanceCriteria: [`Milestone ${index} produces a bounded finding report`], dataClassification: "internal",
    inputs: [{ id: "source", kind: "repository-snapshot", mountMode: "read-only", contentSha256: digest(String(index + 1)), maxBytes: 1024, classification: "internal" }],
    requestedCapabilities: {
      filesystem: "read-only-workspace", tools: ["read", "search"], network: { mode: "brokered", services: ["local-model"] },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: {
      maxRuntimeSeconds: 60 + index, maxIterations: 1, maxToolCalls: 20, maxConcurrentSubagents: 1,
      maxModelRequests: 5 + index, maxInputTokens: 10000 + index, maxOutputTokens: 2000 + index,
      maxCpuCores: 1, maxMemoryMiB: 1536, maxDiskBytes: 1048576, maxArtifactBytes: 65536,
      maxNetworkBytes: 1048576, maxFailures: 1, noProgressLimit: 1,
    },
    outputs: { mode: "analysis", requiredKinds: ["finding-report"] },
    boundary: "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.",
  };
}

function declaration(jobs) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-declaration-v1.schema.json", schemaVersion: 1,
    objective: "Coordinate several bounded inspections over time without expanding authority.", dataClassification: "internal",
    milestones: [
      { milestoneId: "first", jobId: jobs[0].jobId, dependsOn: [] },
      { milestoneId: "second", jobId: jobs[1].jobId, dependsOn: ["first"] },
      { milestoneId: "third", jobId: jobs[2].jobId, dependsOn: ["second"] },
    ],
    boundary: declarationBoundary,
  };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-prepare-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const jobs = [job(0), job(1), job(2)], source = declaration(jobs);
  const declarationPath = join(root, "declaration.json"), jobsPath = join(root, "jobs-source.json");
  await writeFile(declarationPath, `${JSON.stringify(source)}\n`, { mode: 0o600 });
  await writeFile(jobsPath, `${JSON.stringify([jobs[2], jobs[0], jobs[1]])}\n`, { mode: 0o600 });
  return { root, jobs, declaration: source, declarationPath, jobsPath };
}

test("goal preparation derives exact hashes, dependency order, and aggregate budgets without authority", async (t) => {
  const value = await fixture(t), output = join(value.root, "prepared");
  assert.deepEqual(validateWorkGoalDeclaration(value.declaration), []);
  const receipt = await runGoalPrepareCommand([
    "--declaration", value.declarationPath, "--jobs", value.jobsPath, "--output", output,
  ], { now: new Date(baseTime + 100), suffix: "100000000001" });
  assert.equal(receipt.milestones, 3); assert.ok(!JSON.stringify(receipt).includes(value.declaration.objective));
  assert.deepEqual(Object.values(receipt.authority), [false, false, false, false, false, false, false]);
  assert.deepEqual((await readdir(output)).sort(), ["goal-bundle.json", "goal.json", "jobs.json"]);
  const goal = JSON.parse(await readFile(join(output, "goal.json"), "utf8"));
  const jobs = JSON.parse(await readFile(join(output, "jobs.json"), "utf8"));
  const manifest = JSON.parse(await readFile(join(output, "goal-bundle.json"), "utf8"));
  assert.deepEqual(validateWorkGoal(goal), []); assert.deepEqual(jobs.map((entry) => entry.jobId), value.jobs.map((entry) => entry.jobId));
  assert.deepEqual(validateWorkGoalBundleManifest(manifest), []);
  const widenedManifest = structuredClone(manifest); widenedManifest.authority.grantsScheduling = true;
  assert.ok(validateWorkGoalBundleManifest(widenedManifest).some((error) => /grantsScheduling/u.test(error)));
  assert.deepEqual(goal.budgets, {
    maxJobs: 3, maxRuntimeSeconds: 183, maxModelRequests: 18, maxInputTokens: 30003,
    maxOutputTokens: 6003, maxNetworkBytes: 3145728, maxArtifactBytes: 196608, maxFailures: 3,
  });
  if (process.platform !== "win32") {
    assert.equal((await lstat(output)).mode & 0o077, 0);
    for (const name of await readdir(output)) assert.equal((await lstat(join(output, name))).mode & 0o077, 0);
  }
  await assert.rejects(runGoalPrepareCommand(["--declaration", value.declarationPath, "--jobs", value.jobsPath, "--output", output]), /already exists/);
});

test("goal preparation rejects graph, child-set, classification, time, and aggregate widening", async () => {
  const jobs = [job(0), job(1), job(2)], source = declaration(jobs);
  const cycle = structuredClone(source); cycle.milestones[0].dependsOn = ["third"];
  assert.ok(validateWorkGoalDeclaration(cycle).some((error) => /cycle/u.test(error)));
  assert.throws(() => compileGoalDeclaration({ declaration: source, jobs: jobs.slice(0, 2), now: new Date(baseTime + 100), suffix: "200000000001" }), /incomplete or widened/);
  const changedClassification = structuredClone(jobs); changedClassification[1].dataClassification = "confidential"; changedClassification[1].inputs[0].classification = "confidential";
  assert.throws(() => compileGoalDeclaration({ declaration: source, jobs: changedClassification, now: new Date(baseTime + 100), suffix: "200000000002" }), /classification differs/);
  assert.throws(() => compileGoalDeclaration({ declaration: source, jobs, now: new Date(baseTime - 1), suffix: "200000000003" }), /created after/);
  const overBudget = structuredClone(jobs); overBudget[0].budgets.maxRuntimeSeconds = 604801;
  assert.throws(() => compileGoalDeclaration({ declaration: source, jobs: overBudget, now: new Date(baseTime + 100), suffix: "200000000004" }), /goal child job is invalid/);
  const amplifiedJobs = Array.from({ length: 64 }, (_, index) => {
    const child = job(index); child.inputs[0].contentSha256 = digest("a"); child.budgets.maxRuntimeSeconds = 604800; return child;
  });
  const amplified = {
    ...source,
    milestones: amplifiedJobs.map((child, index) => ({ milestoneId: `m${String(index).padStart(2, "0")}`, jobId: child.jobId, dependsOn: [] })),
  };
  assert.throws(() => compileGoalDeclaration({ declaration: amplified, jobs: amplifiedJobs, now: new Date(baseTime + 100), suffix: "200000000005" }), /prepared goal is invalid.*maxRuntimeSeconds/);
});

test("goal preparation rejects linked inputs, argument smuggling, and unsafe output targets", async (t) => {
  const value = await fixture(t), linked = join(value.root, "linked-declaration.json");
  await link(value.declarationPath, linked);
  await assert.rejects(runGoalPrepareCommand([
    "--declaration", linked, "--jobs", value.jobsPath, "--output", join(value.root, "linked-output"),
  ]), /single-link/);
  await assert.rejects(runGoalPrepareCommand([
    "--declaration", value.declarationPath, "--jobs", value.jobsPath, "--declaration", value.declarationPath,
  ]), /duplicated/);
  await assert.rejects(runGoalPrepareCommand([
    "--declaration", value.declarationPath, "--jobs", value.jobsPath, "--output", join(value.root, "invalid output"),
  ]), /directory name is invalid/);
  assert.deepEqual((await readdir(value.root)).filter((name) => name.startsWith(".pixel-work-goal-")), []);
});

test("competing goal preparations publish one complete immutable bundle", async (t) => {
  const value = await fixture(t), output = join(value.root, "race");
  const attempts = await Promise.allSettled(Array.from({ length: 16 }, (_, index) => runGoalPrepareCommand([
    "--declaration", value.declarationPath, "--jobs", value.jobsPath, "--output", output,
  ], { now: new Date(baseTime + 200), suffix: (0x300000000000n + BigInt(index)).toString(16) })));
  assert.equal(attempts.filter((attempt) => attempt.status === "fulfilled").length, 1);
  assert.ok(attempts.filter((attempt) => attempt.status === "rejected").every((attempt) => /already exists/u.test(attempt.reason.message)));
  assert.deepEqual((await readdir(output)).sort(), ["goal-bundle.json", "goal.json", "jobs.json"]);
  assert.ok((await readdir(value.root)).every((name) => !name.startsWith(".pixel-work-goal-")));
});
