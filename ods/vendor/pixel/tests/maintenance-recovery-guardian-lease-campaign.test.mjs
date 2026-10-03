import assert from "node:assert/strict";
import { execFile as execFileCb, spawnSync } from "node:child_process";
import { promisify } from "node:util";
import { acquireGuardianUnitLock, releaseGuardianUnitLock } from "./fixtures/guardian-unit-lock.mjs";
import { chmod, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { createHash, randomBytes } from "node:crypto";
import test from "node:test";

import {
  GUARDIAN_UNIT,
  deriveCampaignGuardianLeasePath,
  deriveGuardianLeasePath,
  deriveProcessIdentity,
  refreshCampaignGuardianLease,
  refreshGuardianLease,
  removeCampaignGuardianLease,
  removeGuardianLease,
  requireLiveCampaignGuardianLease,
  requireLiveGuardianLease,
  validateCampaignGuardianLease,
} from "../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";
import {
  CAMPAIGN_RECOVERY_PHASES,
  createCampaignRecoveryJournal,
  deriveCampaignJournalIdentity,
  deriveCampaignJournalIdentitySha256,
} from "../deploy/work-controller/maintenance-recovery-journal.mjs";

const execFile = promisify(execFileCb);
const digest = (character) => character.repeat(64);
const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const CAMPAIGN_WATCHER = join(ROOT, "tests/fixtures/guardian-campaign-lease-watcher.mjs");
const EXPECTED_NODE = realpathSync(process.execPath);
const linux = process.platform === "linux";
const uid = process.geteuid?.() ?? 1000;

function systemdUserAvailable() {
  if (!linux) return false;
  try {
    const { status, stdout } = spawnSync("systemctl", ["--user", "is-system-running"], { encoding: "utf8", timeout: 10000 });
    const state = String(stdout ?? "").trim();
    return (state === "running" && status === 0) || (state === "degraded" && status === 1);
  }
  catch { return false; }
}
const hasSystemd = systemdUserAvailable();

const SYSTEMD_USER_DIR = join(homedir(), ".config/systemd/user");
const PRODUCTION_UNIT = GUARDIAN_UNIT;
const PRODUCTION_FRAGMENT = join(SYSTEMD_USER_DIR, PRODUCTION_UNIT);
let systemdUnitInstalled = false;

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-campaign-lease-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const lockPath = join(root, "guardian.lock");
  await writeFile(lockPath, "", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(lockPath, 0o600);
  return {
    root, lockPath, uid,
    configSha: digest("a"), custodySha: digest("b"), moduleSha: digest("c"),
    campaignOpSha: digest("d"), journalSha: digest("e"),
  };
}

function campaignBindingOptions(fx, overrides = {}) {
  return {
    configSha256: fx.configSha,
    unit: GUARDIAN_UNIT,
    custodyIdentitySha256: fx.custodySha,
    guardianModuleSha256: fx.moduleSha,
    expectedUnitSha256: digest("1"),
    expectedNodePath: EXPECTED_NODE,
    campaignOperationSha256: fx.campaignOpSha,
    journalIdentitySha256: fx.journalSha,
    configPath: join(fx.root, "maintenance.json"),
    guardianModulePath: CAMPAIGN_WATCHER,
    ...overrides,
  };
}

function fakeSystemdFacts(pid, overrides = {}) {
  return {
    unit: GUARDIAN_UNIT,
    activeState: "active",
    subState: "running",
    mainPid: pid,
    invocationId: "0123456789abcdef0123456789abcdef",
    controlGroup: `/user.slice/user-1000.slice/user@1000.service/app.slice/${GUARDIAN_UNIT}`,
    fragmentPath: join(SYSTEMD_USER_DIR, GUARDIAN_UNIT),
    fragmentSha256: digest("1"),
    dropInPaths: [],
    ...overrides,
  };
}
async function fakeReadInvocation() { return "0123456789abcdef0123456789abcdef"; }

function seamBase(fx) {
  return {
    custodyIdentitySha256: fx.custodySha,
    guardianModuleSha256: fx.moduleSha,
    expectedUnitSha256: digest("1"),
    expectedNodePath: EXPECTED_NODE,
    campaignOperationSha256: fx.campaignOpSha,
    journalIdentitySha256: fx.journalSha,
    systemd: { facts: async () => fakeSystemdFacts(process.pid) },
    readInvocationIdFromProc: fakeReadInvocation,
  };
}

async function writeLeaseAt(path, fx, lease) {
  await writeFile(path, `${JSON.stringify(lease, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

function campaignLeaseJson(identity, fx, overrides = {}) {
  const now = new Date().toISOString();
  return {
    schemaVersion: 1,
    kind: "pixel-campaign-maintenance-recovery-guardian-lease",
    operation: "pixel-work-model-campaign-maintenance-recovery",
    configSha256: fx.configSha,
    custodyIdentitySha256: fx.custodySha,
    guardianModuleSha256: fx.moduleSha,
    expectedUnitSha256: digest("1"),
    expectedNodePath: EXPECTED_NODE,
    invocationId: "0123456789abcdef0123456789abcdef",
    unit: GUARDIAN_UNIT,
    unitSha256: digest("1"),
    installedFragmentSha256: digest("1"),
    fragmentPath: join(SYSTEMD_USER_DIR, GUARDIAN_UNIT),
    controlGroup: `/user.slice/user-1000.slice/user@1000.service/app.slice/${GUARDIAN_UNIT}`,
    activeState: "active",
    subState: "running",
    mainPid: identity.pid,
    bootId: identity.bootId,
    pid: identity.pid,
    startTicks: identity.startTicks,
    exe: identity.exe,
    argv: identity.argv,
    cgroup: identity.cgroup,
    uid: identity.uid,
    campaignOperationSha256: fx.campaignOpSha,
    journalIdentitySha256: fx.journalSha,
    ready: true,
    readyAt: now,
    createdAt: now,
    refreshedAt: now,
    ...overrides,
  };
}

// ---- Real user-systemd campaign proof ------------------------------------
function uniqueProofUnit() {
  return `pixel-campaign-guardian-proof-${randomBytes(6).toString("hex")}.service`;
}

async function snapshotProductionUnit() {
  let fragmentHash = null;
  try { fragmentHash = createHash("sha256").update(await readFile(PRODUCTION_FRAGMENT)).digest("hex"); } catch {}
  let state = null;
  try { const { stdout } = await execFile("systemctl", ["--user", "show", PRODUCTION_UNIT, "--property=ActiveState,SubState,FragmentPath"], { encoding: "utf8", timeout: 10000 }); state = stdout.trim(); } catch {}
  return Object.freeze({ fragmentHash, state });
}

async function assertProductionUnitUnchanged(before) {
  const after = await snapshotProductionUnit();
  assert.deepEqual(after, before, "the production guardian unit identity/state/hash must be unchanged by a non-production proof");
}

async function installThrowawayUnit(unitFile, unit) {
  await writeFile(join(SYSTEMD_USER_DIR, unit), unitFile, { mode: 0o600 });
  systemdUnitInstalled = true;
  await execFile("systemctl", ["--user", "daemon-reload"], { encoding: "utf8", timeout: 20000 });
}
async function removeThrowawayUnit(unit = GUARDIAN_UNIT) {
  try {
    await execFile("systemctl", ["--user", "stop", unit], { encoding: "utf8", timeout: 20000 }).catch(() => {});
    if (systemdUnitInstalled) await execFile("systemctl", ["--user", "reset-failed", unit], { encoding: "utf8", timeout: 20000 }).catch(() => {});
    await rm(join(SYSTEMD_USER_DIR, unit), { force: true });
    systemdUnitInstalled = false;
    await execFile("systemctl", ["--user", "daemon-reload"], { encoding: "utf8", timeout: 20000 }).catch(() => {});
  } catch { /* best effort */ }
}

async function waitForMainPid(unit, t) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try {
      const { stdout } = await execFile("systemctl", ["--user", "show", unit, "--property=MainPID,ActiveState"], { encoding: "utf8", timeout: 10000 });
      const active = /ActiveState=active/u.test(stdout);
      const pidMatch = stdout.match(/MainPID=([0-9]+)/u);
      if (active && pidMatch && Number(pidMatch[1]) > 0) return Number(pidMatch[1]);
    } catch {}
    await delay(100);
  }
  throw new Error("throwaway campaign systemd watcher did not become active");
}

test("a real separate-process campaign watcher lease under a live user systemd unit is accepted and bound to the exact campaign authority", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const opts = campaignBindingOptions(fx);
  const unit = uniqueProofUnit();
  opts.unit = unit;
  const productionBefore = await snapshotProductionUnit();
  const expectedShaFile = join(fx.root, "expected-unit.sha");
  const unitFile = `[Unit]\nDescription=pixel unique non-production campaign guardian lease proof\n[Service]\nType=simple\nEnvironment=CUSTODY_LOCK_PATH=${fx.lockPath}\nEnvironment=UID=${fx.uid}\nEnvironment=CONFIG_SHA=${opts.configSha256}\nEnvironment=UNIT=${unit}\nEnvironment=CUSTODY_IDENTITY_SHA=${opts.custodyIdentitySha256}\nEnvironment=GUARDIAN_MODULE_SHA=${opts.guardianModuleSha256}\nEnvironment=EXPECTED_UNIT_SHA_FILE=${expectedShaFile}\nEnvironment=EXPECTED_NODE=${opts.expectedNodePath}\nEnvironment=CAMPAIGN_OPERATION_SHA=${fx.campaignOpSha}\nEnvironment=JOURNAL_IDENTITY_SHA=${fx.journalSha}\nExecStart=${process.execPath} ${CAMPAIGN_WATCHER} watch --config ${opts.configPath}\nRestart=no\n[Install]\n`;
  await acquireGuardianUnitLock();
  t.after(async () => {
    try { await removeThrowawayUnit(unit); }
    finally {
      await releaseGuardianUnitLock();
      await assertProductionUnitUnchanged(productionBefore);
    }
  });
  opts.expectedUnitSha256 = createHash("sha256").update(unitFile, "utf8").digest("hex");
  await writeFile(expectedShaFile, `${opts.expectedUnitSha256}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(expectedShaFile, 0o600);
  await removeThrowawayUnit(unit);
  await installThrowawayUnit(unitFile, unit);
  await execFile("systemctl", ["--user", "start", unit], { encoding: "utf8", timeout: 30000 });
  const pid = await waitForMainPid(unit, t);
  const deadline = Date.now() + 15000;
  let lease = null;
  while (Date.now() < deadline) {
    try { lease = await requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts); break; }
    catch { await delay(100); }
  }
  assert.ok(lease, "campaign watcher did not publish a verifiable ready campaign lease under systemd");
  assert.equal(lease.pid, pid);
  assert.equal(lease.unit, opts.unit, "the live campaign proof must bind a unique non-production proof unit, never the production unit");
  assert.notEqual(lease.unit, GUARDIAN_UNIT, "the campaign proof must never use the production guardian unit name");
  assert.equal(lease.kind, "pixel-campaign-maintenance-recovery-guardian-lease");
  assert.equal(lease.operation, "pixel-work-model-campaign-maintenance-recovery");
  assert.equal(lease.campaignOperationSha256, fx.campaignOpSha);
  assert.equal(lease.journalIdentitySha256, fx.journalSha);
  assert.equal(lease.ready, true);
  await assertProductionUnitUnchanged(productionBefore);
});

test("campaign live-require is blocked when the live systemd facts report a missing or non-empty drop-in set, before the campaign lease can authorize maintenance", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const opts = campaignBindingOptions(fx);
  const unit = uniqueProofUnit();
  opts.unit = unit;
  const productionBefore = await snapshotProductionUnit();
  const expectedShaFile = join(fx.root, "expected-unit.sha");
  const unitFile = `[Unit]\nDescription=pixel unique non-production campaign guardian lease proof\n[Service]\nType=simple\nEnvironment=CUSTODY_LOCK_PATH=${fx.lockPath}\nEnvironment=UID=${fx.uid}\nEnvironment=CONFIG_SHA=${opts.configSha256}\nEnvironment=UNIT=${unit}\nEnvironment=CUSTODY_IDENTITY_SHA=${opts.custodyIdentitySha256}\nEnvironment=GUARDIAN_MODULE_SHA=${opts.guardianModuleSha256}\nEnvironment=EXPECTED_UNIT_SHA_FILE=${expectedShaFile}\nEnvironment=EXPECTED_NODE=${opts.expectedNodePath}\nEnvironment=CAMPAIGN_OPERATION_SHA=${fx.campaignOpSha}\nEnvironment=JOURNAL_IDENTITY_SHA=${fx.journalSha}\nExecStart=${process.execPath} ${CAMPAIGN_WATCHER} watch --config ${opts.configPath}\nRestart=no\n[Install]\n`;
  await acquireGuardianUnitLock();
  t.after(async () => {
    try { await removeThrowawayUnit(unit); }
    finally {
      await releaseGuardianUnitLock();
      await assertProductionUnitUnchanged(productionBefore);
    }
  });
  opts.expectedUnitSha256 = createHash("sha256").update(unitFile, "utf8").digest("hex");
  await writeFile(expectedShaFile, `${opts.expectedUnitSha256}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(expectedShaFile, 0o600);
  await removeThrowawayUnit(unit);
  await installThrowawayUnit(unitFile, unit);
  await execFile("systemctl", ["--user", "start", unit], { encoding: "utf8", timeout: 30000 });
  const pid = await waitForMainPid(unit, t);
  let lease = null;
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    try { lease = await requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts); break; }
    catch { await delay(100); }
  }
  assert.ok(lease, "campaign watcher did not publish a verifiable ready campaign lease under systemd");
  // Baseline: exact empty drop-in facts authorize the live campaign lease.
  const ok = await requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  assert.equal(ok.ready, true);
  const factsWithDropIn = () => ({
    unit: lease.unit, activeState: "active", subState: "running",
    mainPid: pid, invocationId: lease.invocationId,
    controlGroup: lease.controlGroup, fragmentPath: lease.fragmentPath,
    fragmentSha256: lease.unitSha256, dropInPaths: ["/tmp/pixel-campaign-maintenance-recovery.service.d/override.conf"],
  });
  await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...opts, systemd: { facts: factsWithDropIn } }), /exact empty systemd drop-in set/u);
  const missing = () => { const f = factsWithDropIn(); delete f.dropInPaths; return f; };
  await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...opts, systemd: { facts: missing } }), /exact empty systemd drop-in set/u);
});

// ---- Seam-based round-trip / write / read / validate -----------------------
test("campaign lease round-trips under the shared engine seam: write, read, and strict validate", async (t) => {
  const fx = await fixture(t);
  const written = await refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...seamBase(fx), ready: true });
  assert.equal(written.lease.kind, "pixel-campaign-maintenance-recovery-guardian-lease");
  assert.equal(written.lease.operation, "pixel-work-model-campaign-maintenance-recovery");
  assert.equal(written.lease.campaignOperationSha256, fx.campaignOpSha);
  assert.equal(written.lease.journalIdentitySha256, fx.journalSha);
  const raw = await readFile(written.path, "utf8");
  const parsed = validateCampaignGuardianLease(JSON.parse(raw));
  assert.equal(parsed.journalIdentitySha256, fx.journalSha);
  // The campaign suffix is distinct from the qualification suffix.
  assert.equal(written.path, deriveCampaignGuardianLeasePath(fx.lockPath));
  assert.notEqual(deriveCampaignGuardianLeasePath(fx.lockPath), deriveGuardianLeasePath(fx.lockPath));
});

// ---- Immutable authority hash rejection ------------------------------------
test("missing, zero, malformed, or unknown campaign authority hashes are rejected at validate/refresh/live-require", async (t) => {
  const fx = await fixture(t);
  const identity = await deriveProcessIdentity(process.pid);
  const base = campaignLeaseJson(identity, fx);
  for (const key of ["campaignOperationSha256", "journalIdentitySha256"]) {
    const missing = { ...base }; delete missing[key];
    assert.throws(() => validateCampaignGuardianLease(missing), /shape is invalid/u, `${key} missing must fail the exact-key shape`);
    const zero = { ...base, [key]: "0".repeat(64) };
    assert.throws(() => validateCampaignGuardianLease(zero), new RegExp(`${key === "campaignOperationSha256" ? "campaign operation hash" : "immutable journal identity hash"}`, "u"), `${key} zero must be rejected`);
    const malformed = { ...base, [key]: "abc" };
    assert.throws(() => validateCampaignGuardianLease(malformed), /is invalid/u, `${key} malformed must be rejected`);
    // refresh refuses a missing/zero authority hash in options.
    const opts = { ...seamBase(fx), [key]: undefined };
    await assert.rejects(refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /requires the exact nonzero/u, `${key} missing options must be rejected`);
    await assert.rejects(refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...seamBase(fx), [key]: "0".repeat(64) }), /requires the exact nonzero/u, `${key} zero options must be rejected`);
    // live-require refuses a missing/zero authority hash in options.
    await writeLeaseAt(deriveCampaignGuardianLeasePath(fx.lockPath), fx, base);
    await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...campaignBindingOptions(fx), [key]: undefined }), /requires the exact nonzero/u);
    await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...campaignBindingOptions(fx), [key]: "0".repeat(64) }), /requires the exact nonzero/u);
  }
});

test("a campaign live-require with a wrong reviewed campaign operation or journal identity is rejected", async (t) => {
  const fx = await fixture(t);
  const identity = await deriveProcessIdentity(process.pid);
  await writeLeaseAt(deriveCampaignGuardianLeasePath(fx.lockPath), fx, campaignLeaseJson(identity, fx));
  await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...campaignBindingOptions(fx), campaignOperationSha256: digest("f") }), /different reviewed campaign operation/u);
  await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...campaignBindingOptions(fx), journalIdentitySha256: digest("f") }), /different immutable campaign journal identity/u);
});

// ---- Cross-operation substitution (both directions) ------------------------
test("qualification-as-campaign and campaign-as-qualification substitution both fail closed", async (t) => {
  const fx = await fixture(t);
  const identity = await deriveProcessIdentity(process.pid);
  const qual = { ...campaignLeaseJson(identity, fx), kind: "pixel-maintenance-recovery-guardian-lease", operation: "pixel-work-model-qualification-maintenance-recovery" };
  delete qual.campaignOperationSha256;
  delete qual.journalIdentitySha256;
  await writeLeaseAt(deriveCampaignGuardianLeasePath(fx.lockPath), fx, qual);
  await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, campaignBindingOptions(fx)), /campaign guardian lease (shape is invalid|kind or operation is invalid)/u);
  // The qualification path must not have been touched by the campaign substitution.
  assert.rejects(() => readFile(deriveGuardianLeasePath(fx.lockPath), "utf8"), /ENOENT/u);

  const fx2 = await fixture(t);
  await writeLeaseAt(deriveGuardianLeasePath(fx2.lockPath), fx2, campaignLeaseJson(identity, fx2));
  await assert.rejects(requireLiveGuardianLease(fx2.lockPath, fx2.uid, fx2.configSha, {
    configSha256: fx2.configSha, unit: GUARDIAN_UNIT, custodyIdentitySha256: fx2.custodySha, guardianModuleSha256: fx2.moduleSha, expectedUnitSha256: digest("1"), expectedNodePath: EXPECTED_NODE,
  }), /guardian lease (shape is invalid|kind or operation is invalid)/u);
  // The campaign path must not have been touched by the qualification substitution.
  assert.rejects(() => readFile(deriveCampaignGuardianLeasePath(fx2.lockPath), "utf8"), /ENOENT/u);

  // Distinct operation/kind bindings are enforced directly at the validator: a
  // lease with the exact target key shape but the wrong operation/kind is
  // rejected with the explicit kind-or-operation contract error.
  const fx3 = await fixture(t);
  const campaignShaped = campaignLeaseJson(identity, fx3);
  assert.throws(() => validateCampaignGuardianLease({ ...campaignShaped, kind: "pixel-maintenance-recovery-guardian-lease", operation: "pixel-work-model-qualification-maintenance-recovery" }), /campaign guardian lease kind or operation is invalid/u);
  const qualShaped = { ...campaignLeaseJson(identity, fx3), kind: "pixel-maintenance-recovery-guardian-lease", operation: "pixel-work-model-qualification-maintenance-recovery" };
  delete qualShaped.campaignOperationSha256;
  delete qualShaped.journalIdentitySha256;
  assert.throws(() => validateCampaignGuardianLease({ ...qualShaped, kind: "pixel-campaign-maintenance-recovery-guardian-lease", operation: "pixel-work-model-campaign-maintenance-recovery" }), /campaign guardian lease shape is invalid/u);
});

// ---- Distinct paths and isolated removal -----------------------------------
test("qualification and campaign lease paths are distinct and removal of one leaves the other byte-identical", async (t) => {
  const fx = await fixture(t);
  const qualPath = deriveGuardianLeasePath(fx.lockPath);
  const campPath = deriveCampaignGuardianLeasePath(fx.lockPath);
  assert.notEqual(qualPath, campPath);
  await refreshGuardianLease(fx.lockPath, fx.uid, fx.configSha, { custodyIdentitySha256: fx.custodySha, guardianModuleSha256: fx.moduleSha, expectedUnitSha256: digest("1"), expectedNodePath: EXPECTED_NODE, systemd: { facts: async () => fakeSystemdFacts(process.pid) }, readInvocationIdFromProc: fakeReadInvocation });
  await refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...seamBase(fx), ready: true });
  const campBytes = await readFile(campPath, "utf8");
  const qualBytes = await readFile(qualPath, "utf8");
  // Removing the campaign lease leaves the qualification lease byte-identical.
  await removeCampaignGuardianLease(fx.lockPath, fx.uid);
  assert.rejects(() => readFile(campPath, "utf8"), /ENOENT/u);
  assert.equal(await readFile(qualPath, "utf8"), qualBytes);
  // A second removal of the already-absent campaign lease is ENOENT-only (no-op).
  await removeCampaignGuardianLease(fx.lockPath, fx.uid);
  assert.equal(await readFile(qualPath, "utf8"), qualBytes);
  // Removing the qualification lease leaves the (now recreated) campaign intact.
  await refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...seamBase(fx), ready: true });
  const campBytes2 = await readFile(campPath, "utf8");
  await removeGuardianLease(fx.lockPath, fx.uid);
  assert.rejects(() => readFile(qualPath, "utf8"), /ENOENT/u);
  assert.equal(await readFile(campPath, "utf8"), campBytes2);
});

// ---- Refresh under an immutable identity change ----------------------------
test("campaign refresh with a changed immutable identity is rejected without replacing the prior valid lease", async (t) => {
  const fx = await fixture(t);
  const campPath = deriveCampaignGuardianLeasePath(fx.lockPath);
  await refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...seamBase(fx), ready: true });
  const prior = await readFile(campPath, "utf8");
  await assert.rejects(refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...seamBase(fx), journalIdentitySha256: digest("f") }), /immutable identity has changed|refusing to replace the prior valid lease/u);
  await assert.rejects(refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...seamBase(fx), campaignOperationSha256: digest("f") }), /immutable identity has changed|refusing to replace the prior valid lease/u);
  assert.equal(await readFile(campPath, "utf8"), prior, "the prior valid campaign lease must not be replaced");
});

test("a corrupt/truncated existing campaign lease blocks refresh and remains byte-identical", async (t) => {
  const fx = await fixture(t);
  const campPath = deriveCampaignGuardianLeasePath(fx.lockPath);
  // An owner-private, corrupt/truncated prior campaign lease must fail closed:
  // the existing-lease authority read is mandatory, so refresh must never
  // silently replace it with a fresh lease.
  const corrupt = `{"schemaVersion":1,"kind":"pixel-campaign-maintenance-recov`;
  await writeFile(campPath, corrupt, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(campPath, 0o600);
  await assert.rejects(refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, seamBase(fx)), /could not be opened safely|not strict JSON|not owner-private|shape is invalid/u);
  assert.equal(await readFile(campPath, "utf8"), corrupt, "the corrupt campaign lease must remain byte-identical, never silently replaced");
});

// ---- Drop-in closure on the lease-publisher systemd-facts path ------------
// The separate lease-publisher facts path (deriveSystemdUnitFacts ->
// validateSystemdFacts) must require the exact empty drop-in set before any
// lease write, refresh, or live-require can authorize maintenance. Both
// descriptor lanes (qualification and campaign) inherit the same check, and
// the drop-in proof fact is never persisted into the lease schema.
function qualBase(fx, facts = () => fakeSystemdFacts(process.pid)) {
  return {
    custodyIdentitySha256: fx.custodySha,
    guardianModuleSha256: fx.moduleSha,
    expectedUnitSha256: digest("1"),
    expectedNodePath: EXPECTED_NODE,
    systemd: { facts },
    readInvocationIdFromProc: fakeReadInvocation,
  };
}
function campBase(fx, facts = () => fakeSystemdFacts(process.pid)) {
  return { ...seamBase(fx), systemd: { facts } };
}

test("missing, non-empty, and malformed drop-in facts prevent qualification and campaign lease refresh/write before any lease can authorize maintenance", async (t) => {
  const fx = await fixture(t);
  const qualPath = deriveGuardianLeasePath(fx.lockPath);
  const campPath = deriveCampaignGuardianLeasePath(fx.lockPath);
  const badFacts = [
    ["missing", () => { const f = fakeSystemdFacts(process.pid); delete f.dropInPaths; return f; }],
    ["one non-empty", () => fakeSystemdFacts(process.pid, { dropInPaths: ["/tmp/x.service.d/a.conf"] })],
    ["multiple non-empty", () => fakeSystemdFacts(process.pid, { dropInPaths: ["/tmp/x.service.d/a.conf", "/tmp/x.service.d/b.conf"] })],
    ["malformed string", () => fakeSystemdFacts(process.pid, { dropInPaths: "not-an-array" })],
    ["malformed non-empty array", () => fakeSystemdFacts(process.pid, { dropInPaths: [123] })],
  ];
  for (const [label, facts] of badFacts) {
    await assert.rejects(refreshGuardianLease(fx.lockPath, fx.uid, fx.configSha, qualBase(fx, facts)), /exact empty systemd drop-in set/u, `qualification refresh/write must reject ${label} drop-in facts`);
    await assert.rejects(refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...campBase(fx, facts), ready: true }), /exact empty systemd drop-in set/u, `campaign refresh/write must reject ${label} drop-in facts`);
  }
  // Neither lane may have written a lease while drop-in facts were reported.
  await assert.rejects(readFile(qualPath, "utf8"), /ENOENT/u);
  await assert.rejects(readFile(campPath, "utf8"), /ENOENT/u);
});

test("the drop-in closure proof fact is never persisted into the qualification or campaign lease schema", async (t) => {
  const fx = await fixture(t);
  const qualPath = deriveGuardianLeasePath(fx.lockPath);
  const campPath = deriveCampaignGuardianLeasePath(fx.lockPath);
  // Valid leases written with the exact empty drop-in set.
  await refreshGuardianLease(fx.lockPath, fx.uid, fx.configSha, qualBase(fx));
  await refreshCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...campBase(fx), ready: true });
  const qualJson = JSON.parse(await readFile(qualPath, "utf8"));
  const campJson = JSON.parse(await readFile(campPath, "utf8"));
  assert.equal(Object.prototype.hasOwnProperty.call(qualJson, "dropInPaths"), false, "qualification lease schema must not persist drop-in facts");
  assert.equal(Object.prototype.hasOwnProperty.call(campJson, "dropInPaths"), false, "campaign lease schema must not persist drop-in facts");
});

function campJournalIdentityObj(overrides = {}) {
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

test("mutable journal phase/child/guardian/readiness state does not change the immutable identity hash; tampering with any immutable component does", async (t) => {
  const fx = await fixture(t);
  const base = campJournalIdentityObj();
  const baseHash = deriveCampaignJournalIdentitySha256(base);
  // The helper returns exactly the accepted immutable projection keys.
  assert.deepEqual(deriveCampaignJournalIdentity(base), {
    maintenanceOperationSha256: digest("a"),
    campaignOperationSha256: digest("b"),
    custodyIdentitySha256: digest("c"),
    configurationSha256: digest("d"),
    ownerUid: uid,
    production: base.production,
    comparison: base.comparison,
  });
  // Adding every mutable consequence field must not change the identity hash.
  const withMutable = {
    ...base,
    phase: "campaign-child-active",
    campaignChildNonce: digest("9"),
    campaignChild: { bootId: "12345678-1234-1234-1234-123456789abc", pid: 4242, startTicks: 128000, exe: "/usr/bin/python3", argv: ["/usr/bin/python3"], cgroup: ["0::/system.slice/pixel-campaign.scope"], uid: String(uid) },
    guardianStartedAt: "2026-08-13T14:00:00.000000000Z",
    guardianStartNonce: digest("8"),
    guardianStartEndpointIdentity: digest("7"),
    guardianStartReceipt: { nonce: digest("8"), containerId: digest("1"), endpointIdentity: digest("7"), status: 204, startedAt: "2026-08-13T14:00:00.000000000Z" },
    readinessEvidence: { productionStartedAt: "2026-08-13T14:00:00.000000000Z", provenAt: "2026-08-13T14:00:00.000000000Z", status: 200, latencyMilliseconds: 7, responseSha256: digest("6") },
    campaignOutcome: null,
  };
  assert.equal(deriveCampaignJournalIdentitySha256(withMutable), baseHash, "mutable state must not change the immutable identity hash");
  // Tampering with any immutable component must change the hash.
  for (const [key, value] of [
    ["maintenanceOperationSha256", digest("f")],
    ["campaignOperationSha256", digest("f")],
    ["custodyIdentitySha256", digest("f")],
    ["configurationSha256", digest("f")],
    ["ownerUid", uid + 1],
    ["production", { ...base.production, containerId: digest("f") }],
    ["comparison", { ...base.comparison, materializationSha256: digest("f") }],
  ]) {
    assert.notEqual(deriveCampaignJournalIdentitySha256({ ...base, [key]: value }), baseHash, `tampering with immutable component ${key} must change the identity hash`);
  }
  // A real validated campaign journal hashes to the same deterministic identity.
  const campaign = await createCampaignRecoveryJournal(fx.lockPath, fx.uid, {
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
    ...campJournalIdentityObj(),
  });
  assert.equal(deriveCampaignJournalIdentitySha256(campaign.journal), deriveCampaignJournalIdentitySha256(campJournalIdentityObj()));
  assert.equal(campaign.journal.phase, "prepared");
});

test("mutating the returned campaign identity projection cannot mutate the source journal or a later identity/hash", async (t) => {
  const base = campJournalIdentityObj();
  const sourceHash = deriveCampaignJournalIdentitySha256(base);
  const projection = deriveCampaignJournalIdentity(base);
  // Mutate nested fields on the returned projection.
  projection.production.containerId = digest("f");
  projection.comparison.materializationSha256 = digest("f");
  projection.ownerUid = uid + 1;
  projection.configurationSha256 = digest("f");
  // The source journal must be unchanged.
  assert.equal(base.production.containerId, digest("1"), "source production must not be mutated through the returned projection");
  assert.equal(base.comparison.materializationSha256, digest("1"), "source comparison must not be mutated through the returned projection");
  assert.equal(base.ownerUid, uid, "source owner must not be mutated through the returned projection");
  assert.equal(base.configurationSha256, digest("d"), "source configuration must not be mutated through the returned projection");
  // A fresh projection and hash must be unchanged.
  assert.equal(deriveCampaignJournalIdentitySha256(base), sourceHash, "a later identity hash must be unaffected by mutating a returned projection");
  const fresh = deriveCampaignJournalIdentity(base);
  assert.equal(fresh.production.containerId, digest("1"), "a fresh projection must not retain the mutation");
  assert.equal(fresh.comparison.materializationSha256, digest("1"), "a fresh projection comparison must not retain the mutation");
});

// ---- Shared engine hardening applies to the campaign descriptor -----------
test("shared engine hardening applies to the campaign lease (duplicate key, symlink, substituted parent)", async (t) => {
  const fx = await fixture(t);
  const identity = await deriveProcessIdentity(process.pid);
  const campPath = deriveCampaignGuardianLeasePath(fx.lockPath);
  // Duplicate JSON keys are rejected as not strict JSON (shared read path).
  const lease = campaignLeaseJson(identity, fx);
  const dupPayload = `{${Object.entries(lease).map(([k, v]) => `"${k}":${JSON.stringify(v)}`).join(",")},${'"schemaVersion":1'}}`;
  await writeFile(campPath, dupPayload, { mode: 0o600 });
  await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, campaignBindingOptions(fx)), /could not be opened safely|not strict JSON/u);
  // A symlinked campaign lease is rejected as not a singular real file.
  await rm(campPath, { force: true });
  const target = join(fx.root, "campaign-target");
  await writeFile(target, JSON.stringify(lease), { mode: 0o600 });
  await symlink(target, campPath);
  await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, campaignBindingOptions(fx)), /could not be opened safely|not owner-private, singular, and real|not a singular real file/u);
  // A substituted campaign lease parent symlink fails closed on refresh.
  await rm(campPath, { force: true });
  await rm(target, { force: true });
  const swappedRoot = await mkdtemp(join(tmpdir(), "pixel-campaign-swap-"));
  t.after(() => rm(swappedRoot, { recursive: true, force: true }));
  await writeFile(join(swappedRoot, "guardian.lock"), "", { mode: 0o600 });
  const symParent = join(fx.root, "parent-link");
  await symlink(swappedRoot, symParent);
  await assert.rejects(refreshCampaignGuardianLease(join(symParent, "guardian.lock"), fx.uid, fx.configSha, seamBase(fx)), /substituted symlink|not the exact intended real directory|not accessible|parent/u);
});

test("absent campaign lease fails closed on live-require", async (t) => {
  const fx = await fixture(t);
  await assert.rejects(requireLiveCampaignGuardianLease(fx.lockPath, fx.uid, fx.configSha, campaignBindingOptions(fx)), /could not be opened safely|not owner-private|not strict JSON/u);
});
