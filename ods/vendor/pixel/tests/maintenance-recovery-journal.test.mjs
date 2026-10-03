import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  CAMPAIGN_RECOVERY_PHASES,
  RECOVERY_PHASES,
  advanceCampaignRecoveryJournal,
  advanceRecoveryJournal,
  assertNoActiveRecoveryJournal,
  cancelCampaignRecoveryJournal,
  createCampaignRecoveryJournal,
  createRecoveryJournal,
  deriveCampaignRecoveryJournalPath,
  deriveCampaignRecoveryReceiptPath,
  deriveRecoveryJournalPath,
  deriveRecoveryReceiptPath,
  hasActiveCampaignRecoveryJournal,
  hasActiveRecoveryJournal,
  inspectActiveCampaignRecoveryJournal,
  inspectActiveRecoveryJournal,
  deriveCampaignOutcome,
  deriveCampaignErrorOutcome,
  deriveCampaignOutcomeSha256,
  deriveTerminalCampaignStatusFromOutcome,
  readCampaignSettlementReceipt,
  validateCampaignOutcome,
  settleCampaignRecoveryJournal,
  settleRecoveryJournal,
  validateCampaignRecoveryJournal,
  validateRecoveryJournal,
} from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import { validateProcessIdentity } from "../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";

const uid = process.geteuid?.() ?? 1000;
const digest = (character) => character.repeat(64);

function identity(overrides = {}) {
  return {
    maintenanceOperationSha256: digest("a"),
    qualificationOperationSha256: digest("b"),
    custodyIdentitySha256: digest("c"),
    configurationSha256: digest("d"),
    ownerUid: uid,
    production: {
      containerName: "pixel-production-model",
      containerId: digest("1"),
      imageDigest: `sha256:${digest("2")}`,
      expectedStartedAt: "2026-08-13T12:00:00.123456789Z",
      expectedRestartCount: 0,
    },
    qualification: {
      backendContainerName: "pixel-qualification-backend",
      backendNetworkName: "pixel-qualification-network",
      qualificationContainerName: "pixel-model-qualification-333333333333",
    },
    ...overrides,
  };
}

function record(overrides = {}) {
  return { schemaVersion: 1, kind: "pixel-maintenance-recovery-journal", operation: "pixel-work-model-qualification-maintenance", guardianStartedAt: null, guardianStartNonce: null, guardianStartEndpointIdentity: null, guardianStartReceipt: null, ...identity(), ...overrides };
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-recovery-journal-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const lockPath = join(root, "guardian.lock");
  await writeFile(lockPath, "", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(lockPath, 0o600);
  return { root, lockPath, uid, journalPath: deriveRecoveryJournalPath(lockPath), campaignJournalPath: deriveCampaignRecoveryJournalPath(lockPath) };
}

function campaignIdentity(overrides = {}) {
  return {
    maintenanceOperationSha256: digest("a"),
    campaignOperationSha256: digest("b"),
    custodyIdentitySha256: digest("c"),
    configurationSha256: digest("d"),
    ownerUid: uid,
    production: {
      containerName: "pixel-production-model",
      containerId: digest("1"),
      imageDigest: `sha256:${digest("2")}`,
      expectedStartedAt: "2026-08-13T12:00:00.123456789Z",
      expectedRestartCount: 0,
    },
    comparison: {
      materializationSha256: digest("1"),
      pairConfigurationSha256: digest("2"),
      preflightSha256: digest("3"),
      acceleratorStateSha256: digest("4"),
    },
    ...overrides,
  };
}

function campaignRecord(overrides = {}) {
  return {
    schemaVersion: 1,
    kind: "pixel-campaign-maintenance-recovery-journal",
    operation: "pixel-work-model-campaign-maintenance",
    campaignChildNonce: null,
    campaignChild: null,
    guardianStartedAt: null,
    guardianStartNonce: null,
    guardianStartEndpointIdentity: null,
    guardianStartReceipt: null,
    readinessEvidence: null,
    campaignOutcome: null,
    ...campaignIdentity(),
    ...overrides,
  };
}

function child(overrides = {}) {
  return {
    bootId: "12345678-1234-1234-1234-123456789abc",
    pid: 4242,
    startTicks: 128000,
    exe: "/usr/bin/python3",
    argv: ["/usr/bin/python3", "--root", "/srv"],
    cgroup: ["0::/system.slice/pixel-campaign.scope"],
    uid: String(uid),
    ...overrides,
  };
}

function readiness(overrides = {}) {
  return {
    productionStartedAt: "2026-08-13T14:00:00.000000000Z",
    provenAt: "2026-08-13T14:00:00.000000000Z",
    status: 200,
    latencyMilliseconds: 7,
    responseSha256: digest("f"),
    ...overrides,
  };
}

function campaignOutcome(overrides = {}) {
  const status = overrides.status ?? "pass";
  if (status === "error") {
    return deriveCampaignErrorOutcome({
      failureClass: overrides.failureClass ?? "child-exit-2",
      diagnosticSha256: overrides.diagnosticSha256 ?? digest("d"),
      completedPairs: overrides.completedPairs ?? 0,
      requiredPairs: overrides.requiredPairs ?? 8,
      campaignId: overrides.campaignId ?? null,
      resultSha256: overrides.resultSha256 ?? null,
      exitCode: overrides.exitCode ?? null,
      ...overrides,
    });
  }
  const requiredPairs = overrides.requiredPairs ?? (status === "tuning-frozen" ? 1 : 8);
  const completedPairs = overrides.completedPairs ?? (status === "pass" ? requiredPairs : 1);
  const exitCode = overrides.exitCode ?? (status === "pass" || status === "tuning-frozen" ? 0 : 3);
  return deriveCampaignOutcome({ status, campaignId: "outcomebattery-" + "a".repeat(24), resultSha256: digest("9"), exitCode, completedPairs, requiredPairs, ...overrides });
}

async function advanceCampaignToReadinessProven(fx, outcome = null) {
  const id = campaignIdentity();
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  const startedAt = "2026-08-13T14:00:00.000000000Z";
  const nonce = digest("b");
  const endpoint = digest("e");
  const childLive = child();
  // The no-outcome recovery path (outcome === null) skips campaign-outcome-bound
  // entirely, matching the single-guardian contract: a journal that never bound
  // an outcome may still proceed through readiness-proven but must never fabricate
  // pass. The outcome-bound path includes campaign-outcome-bound with a non-null
  // outcome.
  const phases = outcome
    ? ["production-stopped", "campaign-child-authorized", "campaign-child-active", "comparison-cleanup-pending", "campaign-outcome-bound", "isolation-clean", "production-start-authorized", "production-started", "readiness-proven"]
    : ["production-stopped", "campaign-child-authorized", "campaign-child-active", "comparison-cleanup-pending", "isolation-clean", "production-start-authorized", "production-started", "readiness-proven"];
  const fieldsByPhase = {
    "campaign-child-authorized": { campaignChildNonce: nonce },
    "campaign-child-active": { campaignChild: childLive },
    "campaign-outcome-bound": { campaignOutcome: outcome },
    "production-start-authorized": { guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint },
    "production-started": { guardianStartedAt: startedAt, guardianStartReceipt: { nonce, containerId: id.production.containerId, endpointIdentity: endpoint, status: 204, startedAt } },
    "readiness-proven": { readinessEvidence: { productionStartedAt: startedAt, provenAt: startedAt, status: 200, latencyMilliseconds: 7, responseSha256: digest("f") } },
  };
  for (const phase of phases) {
    await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, phase, id, fieldsByPhase[phase] ?? {});
  }
  return { id, nonce, endpoint, startedAt };
}

test("journal is atomically created in the prepared phase with exact bound identity", async (t) => {
  const fx = await fixture(t);
  const created = await createRecoveryJournal(fx.lockPath, fx.uid, record());
  assert.equal(created.journal.phase, "prepared");
  assert.equal(await hasActiveRecoveryJournal(fx.lockPath, fx.uid), true);
  const inspected = await inspectActiveRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(inspected.path, fx.journalPath);
  assert.equal(inspected.journal.phase, "prepared");
  assert.deepEqual(inspected.journal.production, identity().production);
  assert.deepEqual(inspected.journal.qualification, identity().qualification);
  assert.equal(inspected.journal.ownerUid, uid);
  assert.match(inspected.journal.configurationSha256, /^[a-f0-9]{64}$/u);
});

test("phase advances monotonically through the strict enum", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  for (const phase of ["production-stopped", "qualification-active", "restore-pending"]) {
    const advanced = await advanceRecoveryJournal(fx.lockPath, fx.uid, phase, identity());
    assert.equal(advanced.advanced, true);
    assert.equal(advanced.journal.phase, phase);
  }
  const inspected = await inspectActiveRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(inspected.journal.phase, "restore-pending");
});

test("a repeated same-phase update is idempotent and an identity mismatch is rejected", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  const first = await advanceRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", identity());
  const second = await advanceRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", identity());
  assert.equal(second.advanced, false);
  assert.deepEqual(second.journal, first.journal);
  await assert.rejects(advanceRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", identity({ custodyIdentitySha256: digest("f") })), /identity does not match/u);
});

test("phase regression is rejected without mutating the journal", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity());
  await assert.rejects(advanceRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", identity()), /phase regression/u);
  const inspected = await inspectActiveRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(inspected.journal.phase, "restore-pending");
});

test("an invalid target phase is rejected", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await assert.rejects(advanceRecoveryJournal(fx.lockPath, fx.uid, "not-a-phase", identity()), /target phase is invalid/u);
});

test("tampering with a bound identity is rejected on advance and unknown fields are rejected on read", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  const tampered = record({ production: { ...identity().production, containerId: digest("9") } });
  await writeFile(fx.journalPath, `${JSON.stringify({ ...tampered, phase: "restore-pending" }, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(fx.journalPath, 0o600);
  await assert.rejects(advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity()), /identity does not match/u);
  await writeFile(fx.journalPath, `${JSON.stringify({ ...record({ extraField: "shadow" }), phase: "prepared" }, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(fx.journalPath, 0o600);
  await assert.rejects(inspectActiveRecoveryJournal(fx.lockPath, fx.uid), /shape is invalid/u);
});

test("duplicate JSON keys in the journal are rejected", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  const single = record();
  const payload = `{\n"schemaVersion":1,\n"schemaVersion":1,\n"kind":"pixel-maintenance-recovery-journal",\n"operation":"pixel-work-model-qualification-maintenance",\n"phase":"prepared",\n"maintenanceOperationSha256":"${single.maintenanceOperationSha256}",\n"qualificationOperationSha256":"${single.qualificationOperationSha256}",\n"custodyIdentitySha256":"${single.custodyIdentitySha256}",\n"configurationSha256":"${single.configurationSha256}",\n"ownerUid":${uid},\n"production":${JSON.stringify(single.production)},\n"qualification":${JSON.stringify(single.qualification)}\n}\n`;
  await writeFile(fx.journalPath, payload, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(fx.journalPath, 0o600);
  await assert.rejects(inspectActiveRecoveryJournal(fx.lockPath, fx.uid), /duplicate object key|not strict JSON/u);
});

test("a symlink journal is rejected as not a singular real file", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await rm(fx.journalPath);
  const elsewhere = join(fx.root, "elsewhere.json");
  await writeFile(elsewhere, `${JSON.stringify({ ...record(), phase: "prepared" }, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(elsewhere, 0o600);
  await symlink(elsewhere, fx.journalPath);
  await assert.rejects(inspectActiveRecoveryJournal(fx.lockPath, fx.uid), /not owner-private, singular, and real|not a singular real file/u);
});

test("a permissive-mode journal is rejected on non-Windows", { skip: process.platform === "win32" }, async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await chmod(fx.journalPath, 0o644);
  await assert.rejects(inspectActiveRecoveryJournal(fx.lockPath, fx.uid), /not owner-private, singular, and real/u);
});

test("a hard-linked journal (links>1) is rejected", { skip: process.platform === "win32" }, async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await rm(fx.journalPath);
  const twin = join(fx.root, "twin.json");
  await writeFile(twin, `${JSON.stringify({ ...record(), phase: "prepared" }, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(twin, 0o600);
  const { link } = await import("node:fs/promises");
  await link(twin, fx.journalPath);
  await assert.rejects(inspectActiveRecoveryJournal(fx.lockPath, fx.uid), /not owner-private, singular, and real/u);
});

test("a non-owner journal is rejected on non-Windows", { skip: process.platform === "win32" || uid === 0 }, async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await chmod(fx.journalPath, 0o600);
  await assert.rejects(inspectActiveRecoveryJournal(fx.lockPath, 99999), /owner is invalid or mismatched|not owner-private, singular, and real/u);
});

test("an active nonterminal journal blocks new admission until settled", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await assert.rejects(assertNoActiveRecoveryJournal(fx.lockPath, fx.uid), /recovery is required/u);
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity());
  await settleRecoveryJournal(fx.lockPath, fx.uid, { status: "qualified-production-restored", expectedIdentity: identity() });
  await assertNoActiveRecoveryJournal(fx.lockPath, fx.uid);
});

test("settling publishes a terminal content-free receipt and leaves no active journal", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity());
  const settled = await settleRecoveryJournal(fx.lockPath, fx.uid, { status: "qualified-production-restored", expectedIdentity: identity() });
  assert.equal(await hasActiveRecoveryJournal(fx.lockPath, fx.uid), false);
  const parsed = JSON.parse(await readFile(settled.receiptPath, "utf8"));
  assert.equal(parsed.kind, "pixel-maintenance-recovery-receipt");
  assert.equal(parsed.status, "qualified-production-restored");
  assert.equal(parsed.settled, true);
  assert.equal(parsed.maintenanceOperationSha256, identity().maintenanceOperationSha256);
});

test("manual-attention statuses are never settled and retain active recovery state", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity());
  await assert.rejects(settleRecoveryJournal(fx.lockPath, fx.uid, { status: "manual-attention-production-held", expectedIdentity: identity() }), /settled/u);
  assert.equal(await hasActiveRecoveryJournal(fx.lockPath, fx.uid), true);
  const inspected = await inspectActiveRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(inspected.journal.phase, "restore-pending");
});

test("settlement is idempotent when a terminal receipt already exists (receipt replay)", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity());
  const first = await settleRecoveryJournal(fx.lockPath, fx.uid, { status: "qualified-production-restored", expectedIdentity: identity() });
  assert.equal(first.replayed, false);
  assert.equal(await hasActiveRecoveryJournal(fx.lockPath, fx.uid), false);
  // Replay after a crash between receipt creation and journal unlink.
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity());
  const second = await settleRecoveryJournal(fx.lockPath, fx.uid, { status: "qualified-production-restored", expectedIdentity: identity() });
  assert.equal(second.replayed, true, "replaying an existing matching receipt must be idempotent");
  assert.equal(await hasActiveRecoveryJournal(fx.lockPath, fx.uid), false, "replay must still clear the active journal");
});

test("a tampered terminal receipt at the same path fails closed on settlement", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity());
  await settleRecoveryJournal(fx.lockPath, fx.uid, { status: "qualified-production-restored", expectedIdentity: identity() });
  const receiptPath = deriveRecoveryReceiptPath(fx.lockPath, "qualified-production-restored", identity().maintenanceOperationSha256);
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  receipt.configurationSha256 = digest("e");
  await writeFile(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(receiptPath, 0o600);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "restore-pending", identity());
  await assert.rejects(
    settleRecoveryJournal(fx.lockPath, fx.uid, { status: "qualified-production-restored", expectedIdentity: identity() }),
    /recovery settlement receipt already exists with a different status or identity/u,
  );
});

test("the strict phase enum and structural validation reject malformed journals", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  assert.deepEqual(Object.keys(RECOVERY_PHASES), ["prepared", "production-stopped", "qualification-active", "restore-pending", "isolation-clean", "production-start-authorized", "production-started"]);
  assert.throws(() => validateRecoveryJournal({ ...record(), phase: "unknown" }, uid), /phase is invalid/u);
  assert.throws(() => validateRecoveryJournal({ ...record(), phase: "prepared", maintenanceOperationSha256: "invalid" }, uid), /maintenance operation hash is invalid/u);
  assert.throws(() => validateRecoveryJournal(record({ phase: "prepared", ownerUid: 999 }), uid), /owner is invalid or mismatched/u);
});

test("guardian start authorization and exact start are durably recorded on the monotonic journal", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "isolation-clean", identity());
  const authorized = await advanceRecoveryJournal(fx.lockPath, fx.uid, "production-start-authorized", identity(), { guardianStartNonce: digest("b"), guardianStartEndpointIdentity: digest("e") });
  assert.equal(authorized.journal.phase, "production-start-authorized");
  assert.equal(authorized.journal.guardianStartedAt, null);
  assert.equal(authorized.journal.guardianStartEndpointIdentity, digest("e"));
  assert.equal(authorized.journal.guardianStartReceipt, null);
  const startedAt = "2026-08-13T14:00:00.000000000Z";
  const receipt = { nonce: digest("b"), containerId: identity().production.containerId, endpointIdentity: digest("e"), status: 204, startedAt };
  const started = await advanceRecoveryJournal(fx.lockPath, fx.uid, "production-started", identity(), { guardianStartedAt: startedAt, guardianStartReceipt: receipt });
  assert.equal(started.journal.phase, "production-started");
  assert.equal(started.journal.guardianStartedAt, startedAt);
  const inspected = await inspectActiveRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(inspected.journal.guardianStartedAt, startedAt);
  // An invalid guardian-start value fails closed.
  await assert.rejects(advanceRecoveryJournal(fx.lockPath, fx.uid, "production-started", identity(), { guardianStartedAt: "not-a-date", guardianStartReceipt: receipt }), /guardian start is invalid/u);
  // Unknown advance fields are rejected.
  await assert.rejects(advanceRecoveryJournal(fx.lockPath, fx.uid, "production-started", identity(), { other: 1 }), /advance fields are invalid/u);
});

test("journal coherence: guardian start is null before production-started and recorded at/after it", async (t) => {
  const before = validateRecoveryJournal(record({ phase: "isolation-clean", guardianStartedAt: null }), uid);
  assert.equal(before.phase, "isolation-clean");
  // Reject a journal that records a guardian start before production-started.
  assert.throws(() => validateRecoveryJournal(record({ phase: "production-start-authorized", guardianStartedAt: "2026-08-13T12:00:00Z" }), uid), /must be null before production-started/u);
  // Reject a journal at production-started that has no guardian start.
  assert.throws(() => validateRecoveryJournal(record({ phase: "production-started", guardianStartedAt: null }), uid), /must be recorded at or after production-started/u);
  const started = validateRecoveryJournal(record({ phase: "production-started", guardianStartedAt: "2026-08-13T12:00:00Z", guardianStartNonce: digest("b"), guardianStartEndpointIdentity: digest("e"), guardianStartReceipt: { nonce: digest("b"), containerId: identity().production.containerId, endpointIdentity: digest("e"), status: 204, startedAt: "2026-08-13T12:00:00Z" } }), uid);
  assert.equal(started.guardianStartedAt, "2026-08-13T12:00:00Z");
});

test("journal binds the authorized endpoint identity: null before authorization, exact 64-hex afterward, and the receipt must match it", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  const prepared = await inspectActiveRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(prepared.journal.guardianStartEndpointIdentity, null, "the endpoint identity must be null before authorization");
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "isolation-clean", identity());
  const authorized = await advanceRecoveryJournal(fx.lockPath, fx.uid, "production-start-authorized", identity(), { guardianStartNonce: digest("b"), guardianStartEndpointIdentity: digest("e") });
  assert.equal(authorized.journal.phase, "production-start-authorized");
  assert.equal(authorized.journal.guardianStartEndpointIdentity, digest("e"), "the exact authorized endpoint identity must be durable at production-start-authorized");
  // A receipt bound to a different endpoint identity than the authorized one is rejected.
  assert.throws(() => validateRecoveryJournal(record({ phase: "production-started", guardianStartedAt: "2026-08-13T12:00:00Z", guardianStartNonce: digest("b"), guardianStartEndpointIdentity: digest("e"), guardianStartReceipt: { nonce: digest("b"), containerId: identity().production.containerId, endpointIdentity: digest("f"), status: 204, startedAt: "2026-08-13T12:00:00Z" } }), uid), /not bound to the authorized endpoint/u);
  // A non-64-hex (merely truthy) authorized endpoint identity is rejected.
  assert.throws(() => validateRecoveryJournal(record({ phase: "production-start-authorized", guardianStartNonce: digest("b"), guardianStartEndpointIdentity: "unix:/var/run/docker.sock" }), uid), /endpoint identity is invalid/u);
  // The exact-authorized identity must be present at production-started.
  assert.throws(() => validateRecoveryJournal(record({ phase: "production-started", guardianStartedAt: "2026-08-13T12:00:00Z", guardianStartNonce: digest("b"), guardianStartEndpointIdentity: null, guardianStartReceipt: { nonce: digest("b"), containerId: identity().production.containerId, endpointIdentity: digest("e"), status: 204, startedAt: "2026-08-13T12:00:00Z" } }), uid), /authorized endpoint identity must be recorded|endpoint identity is invalid|must carry the durable authorized endpoint identity/u);
});

test("identity-checked cancellation removes only an inert prepared journal and never a substituted one", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  const { cancelRecoveryJournal } = await import("../deploy/work-controller/maintenance-recovery-journal.mjs");
  await assert.rejects(cancelRecoveryJournal(fx.lockPath, fx.uid, identity({ maintenanceOperationSha256: digest("f") })), /identity does not match/u);
  assert.equal(await hasActiveRecoveryJournal(fx.lockPath, fx.uid), true, "a wrong-identity cancel must not remove the journal");
  const cancelled = await cancelRecoveryJournal(fx.lockPath, fx.uid, identity());
  assert.equal(cancelled.cancelled, true);
  assert.equal(await hasActiveRecoveryJournal(fx.lockPath, fx.uid), false, "an identity-checked inert cancel must durably remove the journal");
});

test("cancellation refuses a journal that is no longer inert (a destructive action may have committed)", async (t) => {
  const fx = await fixture(t);
  await createRecoveryJournal(fx.lockPath, fx.uid, record());
  await advanceRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", identity());
  const { cancelRecoveryJournal } = await import("../deploy/work-controller/maintenance-recovery-journal.mjs");
  await assert.rejects(cancelRecoveryJournal(fx.lockPath, fx.uid, identity()), /no longer inert/u);
  assert.equal(await hasActiveRecoveryJournal(fx.lockPath, fx.uid), true, "a committed-phase journal must never be cancelled");
});

// ---------------------------------------------------------------------------
// Campaign recovery journal/record coverage (M1 generalization).
// ---------------------------------------------------------------------------
test("campaign journal is atomically created in the prepared phase with exact bound identity", async (t) => {
  const fx = await fixture(t);
  const created = await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  assert.equal(created.journal.phase, "prepared");
  assert.equal(await hasActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), true);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(inspected.journal.phase, "prepared");
  assert.deepEqual(inspected.journal.comparison, campaignIdentity().comparison);
  assert.equal(inspected.journal.campaignChildNonce, null);
  assert.equal(inspected.journal.campaignChild, null);
  assert.equal(inspected.journal.readinessEvidence, null);
});

test("the exact campaign phase enum is exposed with the required phases", async (t) => {
  assert.deepEqual(Object.keys(CAMPAIGN_RECOVERY_PHASES), ["prepared", "production-stop-authorized", "production-stopped", "campaign-child-authorized", "campaign-child-active", "comparison-cleanup-pending", "campaign-outcome-bound", "isolation-clean", "production-start-authorized", "production-started", "readiness-proven"]);
});

test("campaign phase advances monotonically through the exact lifecycle", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  const id = campaignIdentity();
  const startedAt = "2026-08-13T14:00:00.000000000Z";
  const nonce = digest("b");
  const endpoint = digest("e");
  const childLive = child();
  const expected = ["production-stop-authorized", "production-stopped", "campaign-child-authorized", "campaign-child-active", "comparison-cleanup-pending", "campaign-outcome-bound", "isolation-clean", "production-start-authorized", "production-started", "readiness-proven"];
  const fieldsByPhase = {
    "campaign-child-authorized": { campaignChildNonce: nonce },
    "campaign-child-active": { campaignChild: childLive },
    "campaign-outcome-bound": { campaignOutcome: campaignOutcome() },
    "production-start-authorized": { guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint },
    "production-started": { guardianStartedAt: startedAt, guardianStartReceipt: { nonce, containerId: id.production.containerId, endpointIdentity: endpoint, status: 204, startedAt } },
    "readiness-proven": { readinessEvidence: { productionStartedAt: startedAt, provenAt: startedAt, status: 200, latencyMilliseconds: 7, responseSha256: digest("f") } },
  };
  for (const phase of expected) {
    const advanced = await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, phase, id, fieldsByPhase[phase] ?? {});
    assert.equal(advanced.advanced, true);
    assert.equal(advanced.journal.phase, phase);
  }
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(inspected.journal.phase, "readiness-proven");
  assert.deepEqual(inspected.journal.campaignChild, childLive);
  assert.deepEqual(inspected.journal.readinessEvidence, { productionStartedAt: startedAt, provenAt: startedAt, status: 200, latencyMilliseconds: 7, responseSha256: digest("f") });
});

test("campaign phase regression is forbidden without mutating the journal", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", campaignIdentity());
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "prepared", campaignIdentity()), /phase regression/u);
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-authorized", campaignIdentity(), { campaignChildNonce: digest("b") });
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "production-stop-authorized", campaignIdentity()), /phase regression/u);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.lockPath, fx.uid);
  assert.equal(inspected.journal.phase, "campaign-child-authorized");
});

test("campaign repeated same-phase update is byte-idempotent and a changed field is rejected", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  const id = campaignIdentity();
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-authorized", id, { campaignChildNonce: digest("b") });
  const first = await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-authorized", id, { campaignChildNonce: digest("b") });
  assert.equal(first.advanced, false);
  assert.equal(first.journal.campaignChildNonce, digest("b"));
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-authorized", id, { campaignChildNonce: digest("c") }), /idempotent/u);
});

test("campaign child-active without prior authorization is rejected", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", campaignIdentity(), { campaignChild: child() }), /must carry the exact durable live identity/u);
});

test("campaign missing, zero, or mismatched child identity is rejected", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  const id = campaignIdentity();
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-authorized", id, { campaignChildNonce: digest("b") });
  // A zero launch nonce is a degenerate identity and is rejected.
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-authorized", id, { campaignChildNonce: digest("0") }), /child launch nonce is invalid/u);
  // Missing live child identity at active is rejected.
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", id), /must carry the exact durable live identity/u);
  // A zero pid is an invalid live identity.
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", id, { campaignChild: { ...child(), pid: 0 } }), /process identity pid is invalid/u);
  // A wall-clock timestamp is NOT accepted as process identity (exact keys only).
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", id, { campaignChild: { ...child(), startedAt: "2026-08-13T14:00:00Z" } }), /process identity shape is invalid/u);
  // A noncanonical relative executable is rejected.
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", id, { campaignChild: { ...child(), exe: "python3" } }), /process identity executable is invalid/u);
  // A zero startTicks is a degenerate start identity and is rejected.
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", id, { campaignChild: { ...child(), startTicks: 0 } }), /process identity start ticks is invalid/u);
  // A non-decimal uid is rejected.
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", id, { campaignChild: { ...child(), uid: "root" } }), /process identity uid is invalid/u);
  // A valid live child identity is accepted and remains separate from the immutable identity.
  const active = await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", id, { campaignChild: child() });
  assert.equal(active.journal.campaignChild.pid, child().pid);
});

test("campaign immutable identity tamper is rejected on advance", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  const tampered = campaignRecord({ comparison: { ...campaignIdentity().comparison, materializationSha256: digest("9") } });
  await writeFile(fx.campaignJournalPath, `${JSON.stringify({ ...tampered, phase: "production-stopped" }, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(fx.campaignJournalPath, 0o600);
  await assert.rejects(advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", campaignIdentity()), /identity does not match/u);
  // Mutating mutable child state must NOT break the immutable identity assertion.
  const fx2 = await fixture(t);
  await createCampaignRecoveryJournal(fx2.lockPath, fx2.uid, campaignRecord());
  await advanceCampaignRecoveryJournal(fx2.lockPath, fx2.uid, "campaign-child-authorized", campaignIdentity(), { campaignChildNonce: digest("b") });
  await advanceCampaignRecoveryJournal(fx2.lockPath, fx2.uid, "campaign-child-active", campaignIdentity(), { campaignChild: child() });
  await inspectActiveCampaignRecoveryJournal(fx2.lockPath, fx2.uid);
});

test("campaign start authorization, receipt, and readiness coherence", async (t) => {
  const id = campaignIdentity();
  const startedAt = "2026-08-13T14:00:00Z";
  const nonce = digest("b");
  const endpoint = digest("e");
  const receipt = { nonce, containerId: id.production.containerId, endpointIdentity: endpoint, status: 204, startedAt };
  // Authorization records nonce+endpoint without claiming a receipt or a start.
  const authorized = validateCampaignRecoveryJournal(campaignRecord({ phase: "production-start-authorized", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint }), uid);
  assert.equal(authorized.guardianStartReceipt, null);
  assert.equal(authorized.guardianStartedAt, null);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "production-start-authorized", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartReceipt: receipt }), uid), /without a receipt|not bound to the exact post-start StartedAt/u);
  // production-started requires the bound 204 receipt + StartedAt.
  const started = validateCampaignRecoveryJournal(campaignRecord({ phase: "production-started", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartedAt: startedAt, guardianStartReceipt: receipt }), uid);
  assert.equal(started.guardianStartReceipt.status, 204);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "production-started", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartedAt: startedAt }), uid), /must carry a durable 204 receipt/u);
  // readiness-proven requires the same bound start plus explicit evidence.
  const proven = validateCampaignRecoveryJournal(campaignRecord({ phase: "readiness-proven", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartedAt: startedAt, guardianStartReceipt: receipt, readinessEvidence: { productionStartedAt: startedAt, provenAt: startedAt, status: 200, latencyMilliseconds: 3, responseSha256: digest("f") } }), uid);
  assert.equal(proven.readinessEvidence.status, 200);
  // productionStartedAt must equal the exact guardian-started post-start time.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "readiness-proven", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartedAt: startedAt, guardianStartReceipt: receipt, readinessEvidence: { productionStartedAt: "2026-08-13T15:00:00Z", provenAt: startedAt, status: 200, latencyMilliseconds: 3, responseSha256: digest("f") } }), uid), /not bound to the exact post-start production start/u);
  // provenAt cannot precede productionStartedAt.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "readiness-proven", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartedAt: startedAt, guardianStartReceipt: receipt, readinessEvidence: { productionStartedAt: startedAt, provenAt: "2026-08-13T13:00:00Z", status: 200, latencyMilliseconds: 3, responseSha256: digest("f") } }), uid), /proof time precedes production start/u);
  // A zero response hash is not a real post-start proof.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "readiness-proven", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartedAt: startedAt, guardianStartReceipt: receipt, readinessEvidence: { productionStartedAt: startedAt, provenAt: startedAt, status: 200, latencyMilliseconds: 3, responseSha256: digest("0") } }), uid), /response identity is invalid/u);
  // readiness evidence must stay null before readiness-proven.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "production-started", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartedAt: startedAt, guardianStartReceipt: receipt, readinessEvidence: { productionStartedAt: startedAt, provenAt: startedAt, status: 200, latencyMilliseconds: 3, responseSha256: digest("f") } }), uid), /must be null before readiness-proven/u);
});

test("campaign process identity bounds are enforced on the shared validator", async (t) => {
  const valid = validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child() }), uid);
  assert.equal(valid.campaignChild.pid, child().pid);
  // Overlong argv count (>= 257 elements) is rejected.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ argv: Array.from({ length: 257 }, () => "x") }) }), uid), /process identity argv is invalid/u);
  // An overlong single argv element byte length is rejected.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ argv: ["x".repeat(9000)] }) }), uid), /process identity argv is invalid/u);
  // A NUL byte in argv is rejected.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ argv: ["ok", "bad\u0000arg"] }) }), uid), /process identity argv is invalid/u);
  // cgroup must be an array of bounded nonempty lines with no CR/LF/NUL.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ cgroup: "0::/system.slice/x" }) }), uid), /process identity cgroup is invalid/u);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ cgroup: ["line1", "line2\n"] }) }), uid), /process identity cgroup is invalid/u);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ cgroup: [] }) }), uid), /process identity cgroup is invalid/u);
  // A wall-clock timestamp cannot substitute for the exact process identity keys.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: { ...child(), startedAt: "2026-08-13T14:00:00Z" } }), uid), /process identity shape is invalid/u);
  // Unknown fields on the child identity are rejected by the exact-keys validator.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: { ...child(), extra: "shadow" } }), uid), /process identity shape is invalid/u);
  // bootId must be the canonical lowercase UUID emitted by /proc boot_id.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ bootId: "12345678-1234-1234-1234-123456789ABC" }) }), uid), /process identity boot id is invalid/u);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ bootId: "12345678-1234-1234-1234-123456789ab" }) }), uid), /process identity boot id is invalid/u);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ bootId: "12345678\n1234-1234-1234-123456789abc" }) }), uid), /process identity boot id is invalid/u);
  // exe byte length (UTF-8) is bounded, not JS character length, and CR/LF/NUL are rejected.
  const multibyteExe = "/usr/bin/" + "\u00e9".repeat(4096);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ exe: "/usr/bin/" + "x".repeat(4096) }) }), uid), /process identity executable is invalid/u);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ exe: "/usr/bin/" + "\u00e9".repeat(4090) }) }), uid), /process identity executable is invalid/u);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ exe: "/usr/bin/prog\n" }) }), uid), /process identity executable is invalid/u);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ exe: "/usr/bin/prog\r" }) }), uid), /process identity executable is invalid/u);
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ exe: "/usr/bin/prog\u0000" }) }), uid), /process identity executable is invalid/u);
  // cgroup line byte length (UTF-8) is bounded.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ cgroup: ["0::/system.slice/" + "\u00e9".repeat(1020)] }) }), uid), /process identity cgroup is invalid/u);
  // uid must be canonical decimal; leading-zero aliases are rejected.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ uid: "01000" }) }), uid), /process identity uid is invalid/u);
test("campaign child custody is bound to the exact expected owner uid", async (t) => {
  // A structurally valid foreign-UID process must never satisfy the custody journal.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ uid: String(uid + 1) }) }), uid), /child owner is invalid or mismatched/u);
  // A structurally valid root-UID process is rejected for the non-root expected owner.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child({ uid: "0" }) }), uid), /child owner is invalid or mismatched/u);
  // The exact expected owner is accepted and preserved.
  const ok = validateCampaignRecoveryJournal(campaignRecord({ phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child() }), uid);
  assert.equal(ok.campaignChild.uid, String(uid));
});

  // The generic validator preserves the valid root uid "0"; the journal's owner
  // match independently rejects a non-root expected owner (see foreign-uid test).
  assert.equal(validateProcessIdentity(child({ uid: "0" })).uid, "0");
});

test("degenerate campaign hashes are rejected (accelerator, custody, and receipt identities)", async (t) => {
  const childActive = { phase: "campaign-child-active", campaignChildNonce: digest("b"), campaignChild: child() };
  // acceleratorStateSha256 is sha(inventory) and can never legitimately be zero.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ ...childActive, comparison: { ...campaignIdentity().comparison, acceleratorStateSha256: digest("0") } }), uid), /accelerator state identity is invalid/u);
  // custodyIdentitySha256 is a real derived identity and must be nonzero.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ ...childActive, custodyIdentitySha256: digest("0") }), uid), /custody identity hash is invalid/u);
  // Every campaign receipt identity hash must be nonzero.
  const baseReceipt = { schemaVersion: 1, kind: "pixel-campaign-maintenance-recovery-receipt", operation: "pixel-work-model-campaign-maintenance", status: "campaign-pass-production-restored", settled: true, ownerUid: uid, maintenanceOperationSha256: digest("a"), campaignOperationSha256: digest("b"), custodyIdentitySha256: digest("c"), configurationSha256: digest("d"), campaignId: "outcomebattery-" + "a".repeat(24), campaignOutcomeSha256: digest("e") };
  const { validateCampaignRecoveryReceipt } = await import("../deploy/work-controller/maintenance-recovery-journal.mjs");
  for (const key of ["maintenanceOperationSha256", "campaignOperationSha256", "custodyIdentitySha256", "configurationSha256", "campaignOutcomeSha256"]) {
    assert.throws(() => validateCampaignRecoveryReceipt({ ...baseReceipt, [key]: digest("0") }, uid), /receipt identity is invalid/u, key);
  }
  assert.deepEqual(validateCampaignRecoveryReceipt(baseReceipt, uid).status, "campaign-pass-production-restored");
});

test("readiness evidence requires the exact post-start proof keys and rejects unknown fields", async (t) => {
  const id = campaignIdentity();
  const startedAt = "2026-08-13T14:00:00Z";
  const nonce = digest("b");
  const endpoint = digest("e");
  const receipt = { nonce, containerId: id.production.containerId, endpointIdentity: endpoint, status: 204, startedAt };
  const base = { phase: "readiness-proven", campaignChildNonce: nonce, campaignChild: child(), guardianStartNonce: nonce, guardianStartEndpointIdentity: endpoint, guardianStartedAt: startedAt, guardianStartReceipt: receipt };
  const good = readiness({ productionStartedAt: startedAt });
  // Old wall-clock-only shape is rejected (exact keys required).
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ ...base, readinessEvidence: { at: startedAt, status: 200, latencyMilliseconds: 3 } }), uid), /shape is invalid/u);
  // Unknown fields are rejected.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ ...base, readinessEvidence: { ...good, shadow: 1 } }), uid), /shape is invalid/u);
  // provenAt strictly after productionStartedAt is accepted.
  const later = validateCampaignRecoveryJournal(campaignRecord({ ...base, readinessEvidence: { ...good, provenAt: "2026-08-13T14:00:05Z" } }), uid);
  assert.equal(later.readinessEvidence.provenAt, "2026-08-13T14:00:05Z");
  // A zero response hash is rejected.
  assert.throws(() => validateCampaignRecoveryJournal(campaignRecord({ ...base, readinessEvidence: { ...good, responseSha256: digest("0") } }), uid), /response identity is invalid/u);
});

test("campaign settlement requires readiness-proven plus a bound outcome and publishes a terminal content-free receipt", async (t) => {
  const fx = await fixture(t);
  const outcome = campaignOutcome();
  await advanceCampaignToReadinessProven(fx, outcome);
  const settled = await settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: campaignIdentity() });
  assert.equal(await hasActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), false);
  const parsed = JSON.parse(await readFile(settled.receiptPath, "utf8"));
  assert.equal(parsed.kind, "pixel-campaign-maintenance-recovery-receipt");
  assert.equal(parsed.operation, "pixel-work-model-campaign-maintenance");
  assert.equal(parsed.settled, true);
  assert.equal(parsed.status, "campaign-pass-production-restored");
  assert.equal(parsed.campaignOperationSha256, campaignIdentity().campaignOperationSha256);
  assert.equal(parsed.campaignOutcomeSha256, outcome.outcomeSha256);
  assert.equal(parsed.campaignId, outcome.campaignId);
  assert.equal(parsed.ownerUid, uid);
  assert.equal(parsed.campaignChild, undefined, "the receipt is content-free and must not carry mutable child state");
});

test("campaign settlement is forbidden before readiness-proven and without an outcome", async (t) => {
  // Settling before readiness-proven (even with an outcome recorded) is forbidden
  // by phase authority and never removes the active journal.
  const prePhase = await fixture(t);
  await createCampaignRecoveryJournal(prePhase.lockPath, prePhase.uid, campaignRecord());
  await advanceCampaignRecoveryJournal(prePhase.lockPath, prePhase.uid, "production-stopped", campaignIdentity());
  await assert.rejects(
    settleCampaignRecoveryJournal(prePhase.lockPath, prePhase.uid, { expectedIdentity: campaignIdentity() }),
    /requires the exact readiness-proven phase/u,
  );
  assert.ok(await hasActiveCampaignRecoveryJournal(prePhase.lockPath, prePhase.uid), "a failed settlement must never remove the active journal");
  // Readiness-proven without any durably bound outcome cannot settle.
  const noOutcome = await fixture(t);
  await advanceCampaignToReadinessProven(noOutcome, null);
  await assert.rejects(
    settleCampaignRecoveryJournal(noOutcome.lockPath, noOutcome.uid, { expectedIdentity: campaignIdentity() }),
    /requires a durably bound campaign outcome/u,
  );
  assert.ok(await hasActiveCampaignRecoveryJournal(noOutcome.lockPath, noOutcome.uid), "a failed settlement must never remove the active journal");
});

test("campaign settlement derives the terminal status mechanically from the outcome", async (t) => {
  for (const [status, terminal] of [
    ["pass", "campaign-pass-production-restored"],
    ["in-progress", "campaign-in-progress-production-restored"],
    ["blocked", "campaign-blocked-production-restored"],
    ["error", "campaign-error-production-restored"],
    ["tuning-frozen", "tuning-frozen-production-restored"],
  ]) {
    assert.equal(deriveTerminalCampaignStatusFromOutcome(campaignOutcome({ status })), terminal);
  }
});

test("campaign-outcome-bound requires a non-null outcome and earlier phases forbid one (outcome phase exactness)", async (t) => {
  const id = campaignIdentity();
  // Advancing to campaign-outcome-bound with a null outcome is rejected: a
  // caller must never reach the named bound phase with null.
  const noOutcome = await fixture(t);
  await createCampaignRecoveryJournal(noOutcome.lockPath, noOutcome.uid, campaignRecord());
  await advanceCampaignRecoveryJournal(noOutcome.lockPath, noOutcome.uid, "production-stopped", id);
  await advanceCampaignRecoveryJournal(noOutcome.lockPath, noOutcome.uid, "campaign-child-authorized", id, { campaignChildNonce: digest("b") });
  await advanceCampaignRecoveryJournal(noOutcome.lockPath, noOutcome.uid, "campaign-child-active", id, { campaignChild: child() });
  await advanceCampaignRecoveryJournal(noOutcome.lockPath, noOutcome.uid, "comparison-cleanup-pending", id, { campaignChild: child() });
  await assert.rejects(
    advanceCampaignRecoveryJournal(noOutcome.lockPath, noOutcome.uid, "campaign-outcome-bound", id, {}),
    /campaign outcome is required at campaign-outcome-bound/u,
  );
  // Earlier phases forbid a non-null outcome.
  const early = await fixture(t);
  await createCampaignRecoveryJournal(early.lockPath, early.uid, campaignRecord());
  await advanceCampaignRecoveryJournal(early.lockPath, early.uid, "production-stopped", id);
  await assert.rejects(
    advanceCampaignRecoveryJournal(early.lockPath, early.uid, "production-stopped", id, { campaignOutcome: campaignOutcome() }),
    /campaign outcome must be null before campaign-outcome-bound/u,
  );
});

test("a predecessor journal without campaignOutcome is rejected, never silently accepted as a new pass-capable journal", async (t) => {
  // Old schema bytes (no campaignOutcome field) must not be accepted as a new
  // pass-capable journal. Rejection is safe; fabricated success is not.
  const legacy = { ...campaignRecord(), phase: "prepared" };
  delete legacy.campaignOutcome;
  assert.throws(() => validateCampaignRecoveryJournal(legacy, uid), /campaign recovery journal shape is invalid/u);
  // A new journal that explicitly records a null outcome (no usable child
  // outcome) is valid and never pass-capable; settlement holds manual.
  assert.equal(validateCampaignRecoveryJournal({ ...campaignRecord(), phase: "prepared" }, uid).campaignOutcome, null);
});

test("a later advance cannot replace a durably bound campaign outcome (preserve exactly)", async (t) => {
  const fx = await fixture(t);
  const id = campaignIdentity();
  const outcomeA = campaignOutcome({ campaignId: "outcomebattery-" + "a".repeat(24) });
  const outcomeB = campaignOutcome({ campaignId: "outcomebattery-" + "b".repeat(24) });
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", id);
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-authorized", id, { campaignChildNonce: digest("b") });
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-child-active", id, { campaignChild: child() });
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "comparison-cleanup-pending", id, { campaignChild: child() });
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "campaign-outcome-bound", id, { campaignOutcome: outcomeA });
  // Replacing the outcome on a later advance (isolation-clean) is rejected.
  await assert.rejects(
    advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "isolation-clean", id, { campaignOutcome: outcomeB }),
    /campaign outcome cannot be replaced on a later advance/u,
  );
  // Advancing with the SAME outcome is permitted and preserves it exactly.
  const advanced = await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "isolation-clean", id, { campaignOutcome: outcomeA });
  assert.equal(advanced.advanced, true);
  assert.equal(advanced.journal.campaignOutcome.outcomeSha256, outcomeA.outcomeSha256);
});

test("campaign outcome is strictly validated and bound to its deterministic canonical hash", async (t) => {
  const outcome = campaignOutcome();
  assert.equal(validateCampaignOutcome(outcome).outcomeSha256, deriveCampaignOutcomeSha256(outcome));
  assert.equal(outcome.outcomeSha256, deriveCampaignOutcomeSha256(outcome));
  // Reject a tampered outcome hash (mismatch with the canonical content hash).
  assert.throws(() => validateCampaignOutcome({ ...outcome, outcomeSha256: digest("f") }), /not bound to its deterministic canonical hash/u);
  // Reject a zero result identity and a zero outcome identity.
  assert.throws(() => validateCampaignOutcome({ ...outcome, resultSha256: digest("0") }), /result identity is invalid/u);
  assert.throws(() => validateCampaignOutcome({ ...outcome, outcomeSha256: digest("0") }), /outcome identity is invalid/u);
  // Reject unknown/extra fields and malformed identities.
  assert.throws(() => validateCampaignOutcome({ ...outcome, shadow: 1 }), /shape is invalid/u);
  assert.throws(() => validateCampaignOutcome({ ...outcome, campaignId: "not-a-campaign" }), /campaign identity is invalid/u);
  assert.throws(() => validateCampaignOutcome({ ...outcome, status: "made-up" }), /status is invalid/u);
  assert.throws(() => validateCampaignOutcome({ ...outcome, schemaVersion: 2 }), /schema is invalid/u);
  // The derived content-free outcome never carries raw semantic bytes.
  assert.deepEqual(Object.keys(outcome).sort(), ["campaignId", "completedPairs", "diagnosticSha256", "exitCode", "failureClass", "kind", "outcomeSha256", "requiredPairs", "resultSha256", "schemaVersion", "status"].sort());
});

test("campaign outcome is an exact discriminated union with strict nullability and coherence invariants", async (t) => {
  // Genuine pass outcome: real identity/exit code, failure fields exactly null.
  const genuine = campaignOutcome();
  assert.equal(genuine.kind, "genuine");
  assert.equal(genuine.campaignId, "outcomebattery-" + "a".repeat(24));
  assert.equal(genuine.failureClass, null);
  assert.equal(genuine.diagnosticSha256, null);
  assert.throws(() => validateCampaignOutcome({ ...genuine, failureClass: "timeout" }), /must not carry a failure class/u);
  assert.throws(() => validateCampaignOutcome({ ...genuine, diagnosticSha256: digest("f") }), /must not carry a diagnostic identity/u);
  assert.throws(() => validateCampaignOutcome({ ...genuine, status: "error" }), /genuine outcome cannot carry an error status/u);
  // Genuine coherence: pass requires exitCode 0 and complete pairs.
  assert.throws(() => campaignOutcome({ exitCode: 3 }), /pass outcome exit code is invalid/u);
  assert.throws(() => campaignOutcome({ completedPairs: 1, requiredPairs: 8 }), /pass outcome pair counts are inconsistent/u);
  // Classified error outcome: exact null identity unless truly observed, an
  // allowlisted failure class, and a diagnostic identity; never raw content.
  const err = campaignOutcome({ status: "error" });
  assert.equal(err.kind, "error");
  assert.equal(err.status, "error");
  assert.equal(err.campaignId, null);
  assert.equal(err.resultSha256, null);
  assert.equal(err.exitCode, null);
  assert.throws(() => validateCampaignOutcome({ ...err, failureClass: "not-allowlisted" }), /failure class is invalid/u);
  assert.throws(() => validateCampaignOutcome({ ...err, status: "blocked" }), /error outcome must carry the error status/u);
  assert.throws(() => validateCampaignOutcome({ ...err, diagnosticSha256: digest("0") }), /diagnostic identity is invalid/u);
});

test("campaign genuine statuses enforce their exact truthful invariants", async (t) => {
  // pass: exitCode exactly 0 and completedPairs exactly requiredPairs.
  assert.ok(campaignOutcome({ status: "pass", exitCode: 0, completedPairs: 8, requiredPairs: 8 }));
  assert.throws(() => campaignOutcome({ status: "pass", exitCode: 3 }), /pass outcome exit code is invalid/u);
  assert.throws(() => campaignOutcome({ status: "pass", completedPairs: 1, requiredPairs: 8 }), /pass outcome pair counts are inconsistent/u);
  // tuning-frozen: exitCode exactly 0 and completedPairs exactly requiredPairs.
  assert.ok(campaignOutcome({ status: "tuning-frozen", exitCode: 0, completedPairs: 1, requiredPairs: 1 }));
  assert.throws(() => campaignOutcome({ status: "tuning-frozen", exitCode: 3 }), /tuning-frozen outcome exit code is invalid/u);
  assert.throws(() => campaignOutcome({ status: "tuning-frozen", exitCode: 0, completedPairs: 1, requiredPairs: 8 }), /tuning-frozen outcome pair counts are inconsistent/u);
  // in-progress: exitCode exactly 3 and completedPairs strictly less than requiredPairs.
  assert.ok(campaignOutcome({ status: "in-progress", exitCode: 3, completedPairs: 1, requiredPairs: 8 }));
  assert.throws(() => campaignOutcome({ status: "in-progress", exitCode: 0 }), /in-progress outcome exit code is invalid/u);
  assert.throws(() => campaignOutcome({ status: "in-progress", exitCode: 3, completedPairs: 8, requiredPairs: 8 }), /in-progress outcome pair counts are inconsistent/u);
  // blocked: exitCode exactly 3 and any already-valid bounded count, including equal to requiredPairs.
  assert.ok(campaignOutcome({ status: "blocked", exitCode: 3, completedPairs: 1, requiredPairs: 8 }));
  assert.ok(campaignOutcome({ status: "blocked", exitCode: 3, completedPairs: 8, requiredPairs: 8 }));
  assert.throws(() => campaignOutcome({ status: "blocked", exitCode: 0 }), /blocked outcome exit code is invalid/u);
});

test("campaign classified errors keep exit code coherent with the allowlisted failure class", async (t) => {
  // A classified error may truthfully carry no exit code at all.
  for (const failureClass of ["child-exit-2", "child-exit-other", "invalid-strict-result"]) {
    assert.equal(campaignOutcome({ status: "error", failureClass, exitCode: null }).exitCode, null);
  }
  // child-exit-2 => exit code exactly 2.
  assert.equal(campaignOutcome({ status: "error", failureClass: "child-exit-2", exitCode: 2 }).exitCode, 2);
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "child-exit-2", exitCode: 0 }), /child-exit-2 outcome exit code is incoherent/u);
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "child-exit-2", exitCode: 3 }), /child-exit-2 outcome exit code is incoherent/u);
  // child-exit-other => anything except 0, 2, or 3.
  assert.ok(campaignOutcome({ status: "error", failureClass: "child-exit-other", exitCode: 1 }));
  assert.ok(campaignOutcome({ status: "error", failureClass: "child-exit-other", exitCode: 4 }));
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "child-exit-other", exitCode: 0 }), /child-exit-other outcome exit code is incoherent/u);
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "child-exit-other", exitCode: 2 }), /child-exit-other outcome exit code is incoherent/u);
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "child-exit-other", exitCode: 3 }), /child-exit-other outcome exit code is incoherent/u);
  // invalid-strict-result => 0 or 3.
  assert.ok(campaignOutcome({ status: "error", failureClass: "invalid-strict-result", exitCode: 0 }));
  assert.ok(campaignOutcome({ status: "error", failureClass: "invalid-strict-result", exitCode: 3 }));
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "invalid-strict-result", exitCode: 1 }), /invalid-strict-result outcome exit code is incoherent/u);
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "invalid-strict-result", exitCode: 2 }), /invalid-strict-result outcome exit code is incoherent/u);
  // timeout and controller-error must keep exitCode null.
  assert.equal(campaignOutcome({ status: "error", failureClass: "timeout", exitCode: null }).exitCode, null);
  assert.equal(campaignOutcome({ status: "error", failureClass: "controller-error", exitCode: null }).exitCode, null);
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "timeout", exitCode: 1 }), /timeout outcome must keep a null exit code/u);
  assert.throws(() => campaignOutcome({ status: "error", failureClass: "controller-error", exitCode: 2 }), /controller-error outcome must keep a null exit code/u);
});

test("campaign settlement cannot alias different outcomes that map to the same terminal status", async (t) => {
  // Two distinct outcomes (different campaign identity) both map to the same
  // terminal status and therefore the same receipt path, so a second settlement
  // with a different outcome must fail closed with conflict rejection.
  const fx = await fixture(t);
  const outcomeA = campaignOutcome({ campaignId: "outcomebattery-" + "a".repeat(24) });
  const outcomeB = campaignOutcome({ campaignId: "outcomebattery-" + "b".repeat(24) });
  assert.notEqual(outcomeA.outcomeSha256, outcomeB.outcomeSha256, "distinct outcomes must not alias");
  assert.equal(deriveTerminalCampaignStatusFromOutcome(outcomeA), deriveTerminalCampaignStatusFromOutcome(outcomeB), "both settle to the same terminal status");
  await advanceCampaignToReadinessProven(fx, outcomeA);
  await settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: campaignIdentity() });
  await advanceCampaignToReadinessProven(fx, outcomeB);
  await assert.rejects(
    settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: campaignIdentity() }),
    /already exists with a different status or identity/u,
  );
  assert.ok(await hasActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), "a conflicting outcome must not remove the active journal");
});

test("campaign settlement receipt replay is idempotent", async (t) => {
  const fx = await fixture(t);
  const outcome = campaignOutcome();
  await advanceCampaignToReadinessProven(fx, outcome);
  const first = await settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: campaignIdentity() });
  assert.equal(first.replayed, false);
  await advanceCampaignToReadinessProven(fx, outcome);
  const second = await settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: campaignIdentity() });
  assert.equal(second.replayed, true);
  assert.equal(await hasActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), false);
});

test("campaign tampered terminal receipt fails closed on settlement", async (t) => {
  const fx = await fixture(t);
  const outcome = campaignOutcome();
  await advanceCampaignToReadinessProven(fx, outcome);
  await settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: campaignIdentity() });
  const receiptPath = deriveCampaignRecoveryReceiptPath(fx.lockPath, "campaign-pass-production-restored", campaignIdentity().maintenanceOperationSha256);
  const receipt = JSON.parse(await readFile(receiptPath, "utf8"));
  receipt.configurationSha256 = digest("9");
  await writeFile(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(receiptPath, 0o600);
  await advanceCampaignToReadinessProven(fx, outcome);
  await assert.rejects(
    settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: campaignIdentity() }),
    /campaign recovery settlement receipt already exists with a different status or identity/u,
  );
});

test("readCampaignSettlementReceipt securely validates the exact receipt and fails closed on forged/symlinked/malformed receipts", async (t) => {
  const id = campaignIdentity();
  const outcome = campaignOutcome();
  const expected = {
    status: "campaign-pass-production-restored",
    maintenanceOperationSha256: id.maintenanceOperationSha256,
    campaignOperationSha256: id.campaignOperationSha256,
    custodyIdentitySha256: id.custodyIdentitySha256,
    configurationSha256: id.configurationSha256,
    campaignOutcome: outcome,
  };
  const fx = await fixture(t);
  // Absent receipt returns null (the controller keeps polling), never success.
  assert.equal(await readCampaignSettlementReceipt(fx.lockPath, fx.uid, expected), null);
  // A valid settled receipt is read and validated.
  await advanceCampaignToReadinessProven(fx, outcome);
  await settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: id });
  const receipt = await readCampaignSettlementReceipt(fx.lockPath, fx.uid, expected);
  assert.equal(receipt.status, "campaign-pass-production-restored");
  assert.equal(receipt.campaignOutcomeSha256, outcome.outcomeSha256);
  // A forged receipt (wrong campaign outcome identity) must fail closed.
  const fx2 = await fixture(t);
  const outcome2 = campaignOutcome({ campaignId: "outcomebattery-" + "b".repeat(24) });
  await advanceCampaignToReadinessProven(fx2, outcome2);
  await settleCampaignRecoveryJournal(fx2.lockPath, fx2.uid, { expectedIdentity: id });
  await assert.rejects(readCampaignSettlementReceipt(fx2.lockPath, fx2.uid, expected), /identity does not equal/u);
  // A malformed receipt (wrong status) must fail closed.
  const fx3 = await fixture(t);
  await advanceCampaignToReadinessProven(fx3, outcome);
  await settleCampaignRecoveryJournal(fx3.lockPath, fx3.uid, { expectedIdentity: id });
  const receiptPath = deriveCampaignRecoveryReceiptPath(fx3.lockPath, "campaign-pass-production-restored", id.maintenanceOperationSha256);
  const tampered = JSON.parse(await readFile(receiptPath, "utf8"));
  tampered.status = "campaign-in-progress-production-restored";
  await writeFile(receiptPath, `${JSON.stringify(tampered, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(receiptPath, 0o600);
  await assert.rejects(readCampaignSettlementReceipt(fx3.lockPath, fx3.uid, expected), /status does not equal/u);
  // A symlinked receipt must fail closed as not a singular real file.
  const fx4 = await fixture(t);
  await advanceCampaignToReadinessProven(fx4, outcome);
  await settleCampaignRecoveryJournal(fx4.lockPath, fx4.uid, { expectedIdentity: id });
  const realPath = deriveCampaignRecoveryReceiptPath(fx4.lockPath, "campaign-pass-production-restored", id.maintenanceOperationSha256);
  const linkPath = `${realPath}.lnk`;
  await rm(realPath, { force: true });
  await symlink(linkPath, realPath);
  await assert.rejects(readCampaignSettlementReceipt(fx4.lockPath, fx4.uid, expected), /not a singular real file/u);
});

test("readCampaignSettlementReceipt binds the complete error outcome hash with a nullable campaignId and never a fabricated identity", async (t) => {
  const id = campaignIdentity();
  const outcome = campaignOutcome({ status: "error", failureClass: "child-exit-2", diagnosticSha256: digest("e"), completedPairs: 0, requiredPairs: 8 });
  assert.equal(outcome.campaignId, null, "a classified error outcome must never carry a fabricated campaign identity");
  const expected = {
    status: "campaign-error-production-restored",
    maintenanceOperationSha256: id.maintenanceOperationSha256,
    campaignOperationSha256: id.campaignOperationSha256,
    custodyIdentitySha256: id.custodyIdentitySha256,
    configurationSha256: id.configurationSha256,
    campaignOutcome: outcome,
  };
  const fx = await fixture(t);
  await advanceCampaignToReadinessProven(fx, outcome);
  await settleCampaignRecoveryJournal(fx.lockPath, fx.uid, { expectedIdentity: id });
  const receipt = await readCampaignSettlementReceipt(fx.lockPath, fx.uid, expected);
  assert.equal(receipt.status, "campaign-error-production-restored");
  assert.equal(receipt.campaignOutcomeSha256, outcome.outcomeSha256, "the receipt must bind the complete outcome hash");
  assert.equal(receipt.campaignId, null, "the receipt must carry the nullable campaignId consistently with the error outcome");
});

test("campaign cancellation only removes an inert prepared journal and never a substituted one", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await assert.rejects(cancelCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignIdentity({ maintenanceOperationSha256: digest("9") })), /identity does not match/u);
  assert.equal(await hasActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), true);
  const cancelled = await cancelCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignIdentity());
  assert.equal(cancelled.cancelled, true);
  assert.equal(await hasActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), false);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await advanceCampaignRecoveryJournal(fx.lockPath, fx.uid, "production-stopped", campaignIdentity());
  await assert.rejects(cancelCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignIdentity()), /no longer inert/u);
  assert.equal(await hasActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), true);
});

test("campaign duplicate-key journal reuse is rejected as not strict JSON", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  const single = campaignRecord();
  const payload = `{\n"schemaVersion":1,\n"schemaVersion":1,\n"kind":"pixel-campaign-maintenance-recovery-journal",\n"operation":"pixel-work-model-campaign-maintenance",\n"phase":"prepared",\n"maintenanceOperationSha256":"${single.maintenanceOperationSha256}",\n"campaignOperationSha256":"${single.campaignOperationSha256}",\n"custodyIdentitySha256":"${single.custodyIdentitySha256}",\n"configurationSha256":"${single.configurationSha256}",\n"ownerUid":${uid},\n"production":${JSON.stringify(single.production)},\n"comparison":${JSON.stringify(single.comparison)}\n}\n`;
  await writeFile(fx.campaignJournalPath, payload, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(fx.campaignJournalPath, 0o600);
  await assert.rejects(inspectActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), /duplicate object key|not strict JSON/u);
});

test("campaign symlinked journal is rejected as not a singular real file", async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await rm(fx.campaignJournalPath);
  const elsewhere = join(fx.root, "elsewhere-campaign.json");
  await writeFile(elsewhere, `${JSON.stringify({ ...campaignRecord(), phase: "prepared" }, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(elsewhere, 0o600);
  await symlink(elsewhere, fx.campaignJournalPath);
  await assert.rejects(inspectActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), /not owner-private, singular, and real|not a singular real file/u);
});

test("campaign hard-linked journal (links>1) is rejected", { skip: process.platform === "win32" }, async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await rm(fx.campaignJournalPath);
  const twin = join(fx.root, "twin-campaign.json");
  await writeFile(twin, `${JSON.stringify({ ...campaignRecord(), phase: "prepared" }, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(twin, 0o600);
  const { link } = await import("node:fs/promises");
  await link(twin, fx.campaignJournalPath);
  await assert.rejects(inspectActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), /not owner-private, singular, and real/u);
});

test("campaign permissive-mode journal is rejected on non-Windows", { skip: process.platform === "win32" }, async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await chmod(fx.campaignJournalPath, 0o644);
  await assert.rejects(inspectActiveCampaignRecoveryJournal(fx.lockPath, fx.uid), /not owner-private, singular, and real/u);
});

test("campaign non-owner journal is rejected on non-Windows", { skip: process.platform === "win32" || uid === 0 }, async (t) => {
  const fx = await fixture(t);
  await createCampaignRecoveryJournal(fx.lockPath, fx.uid, campaignRecord());
  await chmod(fx.campaignJournalPath, 0o600);
  await assert.rejects(inspectActiveCampaignRecoveryJournal(fx.lockPath, 99999), /owner is invalid or mismatched|not owner-private, singular, and real/u);
});
