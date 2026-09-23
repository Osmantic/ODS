import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

import { validateJsonSchema } from "../scripts/lib/json-schema.mjs";
import { buildEnduranceGraph, validateEnduranceEvidence } from "../scripts/deep-work-endurance-probe.mjs";

const run = promisify(execFile);
const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const probe = join(repo, "scripts", "deep-work-endurance-probe.mjs");
const commit = "a".repeat(40), tree = "b".repeat(40);

async function privateRoot(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-real-crash-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  return root;
}

test("fresh controller processes recover every post-commit hard crash across an event-driven branch-and-converge horizon", { timeout: 60000 }, async (t) => {
  const root = await privateRoot(t), output = join(root, "endurance.json");
  const result = await run(process.execPath, [
    probe, "--output", output, "--source-commit", commit, "--source-tree", tree,
    "--milestones", "4", "--restart-delay-ms", "10",
  ], { cwd: repo, timeout: 55000, maxBuffer: 1024 * 1024 });
  assert.equal(result.stderr, "");
  const receipt = JSON.parse(result.stdout), evidence = JSON.parse(await readFile(output, "utf8"));
  const schema = JSON.parse(await readFile(join(repo, "schemas", "deep-work-endurance-evidence-v1.schema.json"), "utf8"));
  assert.deepEqual(validateJsonSchema(evidence, schema), []);
  assert.deepEqual(validateEnduranceEvidence(evidence), []);
  assert.equal(receipt.status, "pass");
  assert.equal(receipt.forcedAbruptExits, 16);
  assert.equal(receipt.processesLaunched, 25);
  assert.equal(evidence.campaign.freshProcessRecoveries, 16);
  assert.deepEqual(evidence.campaign.graph, {
    kind: "repeated-diamond", rootMilestones: 1, dependencyEdges: 4, branchPoints: 1,
    convergenceMilestones: 1, maximumDepth: 2, maxDependenciesPerMilestone: 2,
  });
  assert.deepEqual(evidence.campaign.crashStates, { running: 4, verifying: 4, verified: 4, completed: 4 });
  assert.equal(evidence.campaign.gracefulCycles, 9);
  assert.equal(evidence.result.parentCheckpointRecords, 10);
  assert.equal(evidence.result.childCheckpointRecords, 20);
  assert.equal(evidence.result.milestonesCompleted, 4);
  assert.equal(evidence.result.jobsStarted, 4);
  assert.equal(evidence.result.modelRequests, 4);
  assert.equal(evidence.result.duplicateMilestoneCredit, false);
  assert.equal(evidence.result.duplicateUsageCredit, false);
  assert.equal(evidence.result.replayedChildExecution, false);
  assert.deepEqual(evidence.privacy, { providerCalls: 0, credentialInputs: 0, networkRequests: 0, externalEffects: 0, productionDeploymentsTouched: 0 });
  assert.equal(JSON.stringify(evidence).includes(root), false);
  if (process.platform !== "win32") assert.equal((await stat(output)).mode & 0o077, 0);

  const falseRecovery = structuredClone(evidence);
  falseRecovery.campaign.freshProcessRecoveries -= 1;
  assert.match(validateEnduranceEvidence(falseRecovery)[0], /fresh-process recovery/u);
  const falseCadence = structuredClone(evidence);
  falseCadence.campaign.wallClockElapsedMs = 1;
  assert.match(validateEnduranceEvidence(falseCadence)[0], /shorter/u);
  const duplicateCredit = structuredClone(evidence);
  duplicateCredit.result.modelRequests += 1;
  assert.match(validateEnduranceEvidence(duplicateCredit)[0], /accounting/u);
  const falseGraph = structuredClone(evidence);
  falseGraph.campaign.graph.convergenceMilestones = 0;
  assert.match(validateEnduranceEvidence(falseGraph)[0], /dependency-event coverage/u);
});

test("maximum event horizon is one root followed by repeated independent branches and verified convergence", () => {
  const graph = buildEnduranceGraph(64);
  assert.deepEqual(graph.dependencies.slice(0, 7), [
    [], ["milestone-01"], ["milestone-01"], ["milestone-02", "milestone-03"],
    ["milestone-04"], ["milestone-04"], ["milestone-05", "milestone-06"],
  ]);
  assert.deepEqual(graph.metrics, {
    kind: "repeated-diamond", rootMilestones: 1, dependencyEdges: 84, branchPoints: 21,
    convergenceMilestones: 21, maximumDepth: 42, maxDependenciesPerMilestone: 2,
  });
});

test("endurance probe fails closed on unsafe scope, malformed bounds, and existing evidence", async (t) => {
  const root = await privateRoot(t), output = join(root, "existing.json");
  await writeFile(output, "retained\n", { mode: 0o600 });
  const cases = [
    ["--output", output, "--source-commit", commit, "--source-tree", tree, "--milestones", "4", "--restart-delay-ms", "10"],
    ["--output", join(root, "small.json"), "--source-commit", commit, "--source-tree", tree, "--milestones", "3", "--restart-delay-ms", "10"],
    ["--output", join(root, "fast.json"), "--source-commit", commit, "--source-tree", tree, "--milestones", "4", "--restart-delay-ms", "1"],
    ["--output", join(repo, "unsafe-evidence.json"), "--source-commit", commit, "--source-tree", tree, "--milestones", "4", "--restart-delay-ms", "10"],
  ];
  for (const args of cases) {
    await assert.rejects(run(process.execPath, [probe, ...args], { cwd: repo, timeout: 10000 }), /pixel-deep-work-endurance/u);
  }
  assert.equal(await readFile(output, "utf8"), "retained\n");
});
