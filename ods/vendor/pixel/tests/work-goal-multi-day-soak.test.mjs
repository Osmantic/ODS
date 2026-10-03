import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";

import {
  campaignStatus, finalizeCampaign, initializeCampaign, loadCampaign, main, runCampaignCycle, soakRuntimeRoots,
  validateMultiDaySoakEvidence,
} from "../scripts/deep-work-multi-day-soak.mjs";
import { validateJsonSchema } from "../scripts/lib/json-schema.mjs";

const repo = resolve(new URL("..", import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/u, (value) => value.slice(1)));
const commit = "1".repeat(40), tree = "2".repeat(40);
const bootA = "00000000-0000-4000-8000-000000000001";
const bootB = "00000000-0000-4000-8000-000000000002";

async function privateRoot(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-multi-day-soak-test-"));
  if (process.platform !== "win32") await chmod(root, 0o700);
  t.after(async () => { const { rm } = await import("node:fs/promises"); await rm(root, { recursive: true, force: true }); });
  return root;
}

function invocationId(sequence) { return sequence.toString(16).padStart(32, "0"); }
function cgroup(campaignId) { return `0::/system.slice/pixel-deep-work-${campaignId}.service\n`; }

test("soak runtime snapshot includes every reviewed execution root", () => {
  assert.deepEqual([...soakRuntimeRoots], [
    "scripts/lib", "deploy/work-broker", "deploy/work-controller", "deploy/work-model-proxy",
    "deploy/work-runner", "deploy/work-research-broker", "schemas",
  ]);
});

test("49 fresh systemd reconciliations qualify one goal across 48 hours and a controlled reboot", { timeout: 120000 }, async (t) => {
  const parent = await privateRoot(t), root = join(parent, "campaign");
  const initializedAt = new Date("2026-01-01T00:00:00.000Z");
  let sourceVerified = false;
  const receipt = await initializeCampaign({ root, sourceCommit: commit, sourceTree: tree }, {
    now: initializedAt, identitySuffix: "abcdef123456",
    sourceIdentityVerifier: async (observedCommit, observedTree) => { assert.equal(observedCommit, commit); assert.equal(observedTree, tree); sourceVerified = true; },
  });
  const configPath = join(root, "campaign.json"), output = join(parent, "evidence.json");
  assert.equal(receipt.status, "initialized");
  assert.equal(receipt.expectedCycles, 49);
  assert.equal(receipt.minimumElapsedSeconds, 172800);
  assert.equal(sourceVerified, true);
  const ownerUid = process.geteuid?.() ?? 0;
  assert.equal((await loadCampaign(configPath, { expectedOwnerUid: ownerUid })).config.campaignId, receipt.campaignId);
  if (process.platform !== "win32") await assert.rejects(loadCampaign(configPath, { expectedOwnerUid: ownerUid + 1 }), /owner-private/u);
  await assert.rejects(main(["cycle", "--config", configPath]), /confirm-config-sha256/u);
  await assert.rejects(main(["cycle", "--config", configPath, "--confirm-config-sha256", "bad"]), /confirmation is invalid/u);
  await assert.rejects(runCampaignCycle(configPath, { expectedConfigSha256: "f".repeat(64) }), /exact service confirmation/u);
  let earlyStatus = await campaignStatus(configPath);
  assert.equal(earlyStatus.completedCycles, 0);
  assert.equal(earlyStatus.readyToFinalize, false);
  await assert.rejects(finalizeCampaign(configPath, output), /exact invocation sequence/u);

  for (let sequence = 0; sequence < 49; sequence += 1) {
    const startedAt = new Date(initializedAt.getTime() + (sequence + 1) * 3600 * 1000);
    const cycle = await runCampaignCycle(configPath, {
      startedAt, finishedAt: new Date(startedAt.getTime() + 100), invocationId: invocationId(sequence + 1),
      bootId: sequence < 24 ? bootA : bootB, cgroup: cgroup(receipt.campaignId),
    });
    assert.equal(cycle.sequence, sequence);
    assert.equal(cycle.milestonesCompleted, Math.floor((sequence + 1) / 2));
    assert.equal(cycle.action, sequence === 48 ? "terminal" : "controller-yield");
    assert.equal(cycle.providerCalls, 0);
  }

  const status = await campaignStatus(configPath);
  assert.equal(status.state, "completed");
  assert.equal(status.completedCycles, 49);
  assert.equal(status.milestonesCompleted, 24);
  assert.equal(status.bootSessionsObserved, 2);
  assert.equal(status.readyToFinalize, true);
  const terminalNoop = await runCampaignCycle(configPath, {
    startedAt: new Date(initializedAt.getTime() + 50 * 3600 * 1000), finishedAt: new Date(initializedAt.getTime() + 50 * 3600 * 1000 + 100),
    invocationId: invocationId(50), bootId: bootB,
  });
  assert.equal(terminalNoop.status, "terminal-noop");
  assert.equal((await campaignStatus(configPath)).completedCycles, 49);

  const finalized = await finalizeCampaign(configPath, output);
  const evidence = JSON.parse(await readFile(output, "utf8"));
  const schema = JSON.parse(await readFile(join(repo, "schemas", "deep-work-multi-day-soak-evidence-v1.schema.json"), "utf8"));
  assert.equal(finalized.status, "pass");
  assert.equal(finalized.elapsedSeconds, 172800);
  assert.equal(finalized.bootSessions, 2);
  assert.deepEqual(validateJsonSchema(evidence, schema), []);
  assert.deepEqual(validateMultiDaySoakEvidence(evidence), []);
  assert.deepEqual(evidence.observed, {
    completedCycles: 49, uniqueInvocationIds: 49, bootSessions: 2,
    minimumGapSeconds: 3600, maximumGapSeconds: 3600, elapsedSeconds: 172800, preAdmissionLeaseRefreshes: 24,
  });
  assert.deepEqual(evidence.result, {
    goalState: "completed", milestonesCompleted: 24, jobsStarted: 24, modelRequests: 24,
    parentCheckpointRecords: 50, childCheckpointRecords: 120, runBundleRecords: 72,
    duplicateMilestoneCredit: false, duplicateUsageCredit: false, replayedChildExecution: false,
    goalCheckpointSha256: evidence.result.goalCheckpointSha256, invocationLedgerSha256: evidence.result.invocationLedgerSha256,
  });
  assert.deepEqual(evidence.privacy, { providerCalls: 0, credentialInputs: 0, networkRequests: 0, externalEffects: 0, productionDeploymentsTouched: 0 });
  assert.equal(JSON.stringify(evidence).includes(root), false);
  if (process.platform !== "win32") assert.equal((await stat(output)).mode & 0o077, 0);
  await assert.rejects(finalizeCampaign(configPath, output), /new absolute file/u);

  const missingReboot = structuredClone(evidence);
  missingReboot.observed.bootSessions = 1;
  assert.match(validateMultiDaySoakEvidence(missingReboot)[0], /reboot/u);
  const short = structuredClone(evidence);
  short.observed.elapsedSeconds = 172799;
  assert.match(validateMultiDaySoakEvidence(short).find((error) => /48 real hours/u.test(error)), /48 real hours/u);
  const replay = structuredClone(evidence);
  replay.result.modelRequests = 25;
  assert.match(validateMultiDaySoakEvidence(replay).at(-1), /accounting/u);
});

test("campaign rejects missing systemd identity, premature cadence, tampering, and existing evidence", { timeout: 30000 }, async (t) => {
  const parent = await privateRoot(t), root = join(parent, "campaign");
  const initializedAt = new Date("2026-02-01T00:00:00.000Z");
  const initialized = await initializeCampaign({ root, sourceCommit: commit, sourceTree: tree }, { now: initializedAt, identitySuffix: "abcdef654321", sourceIdentityVerifier: async () => {} });
  const configPath = join(root, "campaign.json");
  await assert.rejects(runCampaignCycle(configPath, { startedAt: new Date(initializedAt.getTime() + 3600 * 1000), invocationId: "", bootId: bootA }), /INVOCATION_ID/u);
  await assert.rejects(runCampaignCycle(configPath, {
    startedAt: new Date(initializedAt.getTime() + 3600 * 1000), invocationId: invocationId(9), bootId: bootA,
    cgroup: "0::/system.slice/not-the-campaign.service\n",
  }), /exact systemd service cgroup/u);
  await runCampaignCycle(configPath, {
    startedAt: new Date(initializedAt.getTime() + 3600 * 1000), finishedAt: new Date(initializedAt.getTime() + 3600 * 1000 + 10),
    invocationId: invocationId(1), bootId: bootA, cgroup: cgroup(initialized.campaignId),
  });
  await assert.rejects(runCampaignCycle(configPath, {
    startedAt: new Date(initializedAt.getTime() + 3600 * 1000 + 3299 * 1000), invocationId: invocationId(2), bootId: bootA,
  }), /real-time qualification cadence/u);
  await assert.rejects(runCampaignCycle(configPath, {
    startedAt: new Date(initializedAt.getTime() + 3 * 3600 * 1000), invocationId: invocationId(1), bootId: bootA, cgroup: cgroup(initialized.campaignId),
  }), /already recorded/u);

  const config = JSON.parse(await readFile(configPath, "utf8"));
  const original = await readFile(config.paths.goalPath, "utf8");
  const goal = JSON.parse(original); goal.objective = "tampered";
  await writeFile(config.paths.goalPath, `${JSON.stringify(goal)}\n`, { mode: 0o600 });
  await assert.rejects(campaignStatus(configPath), /contract hash differs/u);
  await writeFile(config.paths.goalPath, original, { mode: 0o600 });
  assert.equal((await campaignStatus(configPath)).completedCycles, 1);

  const output = join(parent, "existing.json");
  await writeFile(output, "retained\n", { mode: 0o600 });
  await assert.rejects(finalizeCampaign(configPath, output), /exact invocation sequence/u);
  assert.equal(await readFile(output, "utf8"), "retained\n");
});
