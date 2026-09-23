import assert from "node:assert/strict";
import { execFile as execFileCb, execFileSync, spawn, spawnSync } from "node:child_process";
import { promisify } from "node:util";
import { acquireGuardianUnitLock, releaseGuardianUnitLock } from "./fixtures/guardian-unit-lock.mjs";
import { chmod, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { assertOwnerPrivateAckPath, renderAckEnvironment } from "./fixtures/guardian-ack-contract.mjs";
import { realpathSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";
import { createHash, randomBytes } from "node:crypto";
import test from "node:test";

import {
  DEFAULT_GUARDIAN_LEASE_TTL_MS,
  GUARDIAN_UNIT,
  deriveGuardianLeasePath,
  deriveProcessIdentity,
  readCurrentBootId,
  refreshGuardianLease,
  removeGuardianLease,
  requireLiveGuardianLease,
  validateGuardianLease,
} from "../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";

const execFile = promisify(execFileCb);
const digest = (character) => character.repeat(64);
const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const WATCHER = join(ROOT, "tests/fixtures/guardian-lease-watcher.mjs");
const REFRESH_FAIL_WATCHER = join(ROOT, "tests/fixtures/guardian-refresh-fail-watcher.mjs");
const EXPECTED_NODE = realpathSync(process.execPath);
const linux = process.platform === "linux";

function systemdUserAvailable() {
  if (!linux) return false;
  try {
    const { status, stdout } = spawnSync("systemctl", ["--user", "is-system-running"], { encoding: "utf8", timeout: 10000 });
    const state = String(stdout ?? "").trim();
    // systemd reports a degraded-but-functional user manager with exit code 1
    // ("degraded"). The unique non-production proof still runs correctly under a
    // degraded manager, so both "running" and "degraded" are accepted.
    return (state === "running" && status === 0) || (state === "degraded" && status === 1);
  }
  catch { return false; }
}
const hasSystemd = systemdUserAvailable();

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-guardian-lease-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const lockPath = join(root, "guardian.lock");
  await writeFile(lockPath, "", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(lockPath, 0o600);
  const uid = process.geteuid?.() ?? 1000;
  return { root, lockPath, uid, configSha: digest("a"), custodySha: digest("b"), moduleSha: digest("c") };
}

function bindingOptions(fx, overrides = {}) {
  return {
    configSha256: fx.configSha,
    unit: GUARDIAN_UNIT,
    custodyIdentitySha256: fx.custodySha,
    guardianModuleSha256: fx.moduleSha,
    expectedUnitSha256: digest("1"),
    expectedNodePath: EXPECTED_NODE,
    configPath: join(fx.root, "maintenance.json"),
    guardianModulePath: WATCHER,
    ...overrides,
  };
}

test("owner-private ack path and environment rendering contract", async (t) => {
  const root = join(tmpdir(), "pixel-ack-root-contract");
  // A canonical ack path beneath the owner-private root is accepted.
  assert.equal(assertOwnerPrivateAckPath(join(root, "ready-ack"), root), join(root, "ready-ack"));
  // The existing random fixture root (a mkdtemp-style absolute path) renders safely.
  assert.match(renderAckEnvironment("ACK_FILE", join(root, "ready-ack")), /^Environment=ACK_FILE=/u);
  // Whitespace or newline injection in the ack path is rejected.
  assert.throws(() => assertOwnerPrivateAckPath(join(root, "bad path"), root), /unsafe or noncanonical/u);
  assert.throws(() => assertOwnerPrivateAckPath(join(root, "bad\npath"), root), /unsafe or noncanonical/u);
  assert.throws(() => renderAckEnvironment("ACK_FILE", join(root, "bad path")), /whitespace/u);
  assert.throws(() => renderAckEnvironment("ACK_FILE", join(root, "bad\npath")), /unsafe/u);
  // A relative, noncanonical, or root-equal/outside-root ack path is rejected.
  assert.throws(() => assertOwnerPrivateAckPath("ready-ack", root), /noncanonical/u);
  assert.throws(() => assertOwnerPrivateAckPath(root, root), /not beneath/u);
  assert.throws(() => assertOwnerPrivateAckPath(join(root, "..", "outside-ack"), root), /not beneath/u);
  // An invalid environment name is rejected.
  assert.throws(() => renderAckEnvironment("BAD NAME", "x"), /invalid/u);
});

// ---- Real user-systemd watcher proof -------------------------------------
// Runs the watcher under a cryptographically unique NON-production user service
// so the lease is a genuine systemd invocation proof (real /proc cgroup, real
// INVOCATION_ID, real systemctl facts) that never touches the production
// pixel-maintenance-recovery.service unit. Every proof verifies the production
// unit's fragment bytes and systemd state are unchanged before/after.
const SYSTEMD_USER_DIR = join(homedir(), ".config/systemd/user");
const PRODUCTION_UNIT = GUARDIAN_UNIT;
const PRODUCTION_FRAGMENT = join(SYSTEMD_USER_DIR, PRODUCTION_UNIT);
let systemdUnitInstalled = false;

function uniqueProofUnit() {
  return `pixel-guardian-proof-${randomBytes(6).toString("hex")}.service`;
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

async function startWatcher(t, fx, overrides = {}) {
  const opts = bindingOptions(fx, overrides);
  const unit = typeof overrides?.unit === "string" ? overrides.unit : uniqueProofUnit();
  opts.unit = unit;
  const productionBefore = await snapshotProductionUnit();
  const expectedShaFile = join(fx.root, "expected-unit.sha");
  const unitFile = `[Unit]\nDescription=pixel unique non-production guardian lease proof\n[Service]\nType=simple\nEnvironment=CUSTODY_LOCK_PATH=${fx.lockPath}\nEnvironment=UID=${fx.uid}\nEnvironment=CONFIG_SHA=${opts.configSha256}\nEnvironment=UNIT=${unit}\nEnvironment=CUSTODY_IDENTITY_SHA=${opts.custodyIdentitySha256}\nEnvironment=GUARDIAN_MODULE_SHA=${opts.guardianModuleSha256}\nEnvironment=EXPECTED_UNIT_SHA_FILE=${expectedShaFile}\nEnvironment=EXPECTED_NODE=${opts.expectedNodePath}\nExecStart=${process.execPath} ${WATCHER} watch --config ${opts.configPath}\nRestart=no\n[Install]\n`;
  await acquireGuardianUnitLock();
  t.after(async () => {
    try { await removeThrowawayUnit(unit); }
    finally {
      await releaseGuardianUnitLock();
      // Clean proof artifacts even on failure and prove the production unit is
      // byte/state identical after the whole proof lifecycle.
      await assertProductionUnitUnchanged(productionBefore);
    }
  });
  // The reviewed expected render for this proof is the exact throwaway unit bytes.
  opts.expectedUnitSha256 = createHash("sha256").update(unitFile, "utf8").digest("hex");
  await writeFile(expectedShaFile, `${opts.expectedUnitSha256}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(expectedShaFile, 0o600);
  await removeThrowawayUnit(unit);
  await installThrowawayUnit(unitFile, unit);
  await execFile("systemctl", ["--user", "start", unit], { encoding: "utf8", timeout: 30000 });
  const pid = await waitForMainPid(unit, t);
  // Wait until the watcher has durably published its ready lease so the proof
  // is verifiable before any tamper/acceptance assertion.
  const deadline = Date.now() + 15000;
  let lease = null;
  while (Date.now() < deadline) {
    try { lease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts); break; }
    catch { await delay(100); }
  }
  if (!lease) throw new Error("watcher did not publish a verifiable ready lease under systemd");
  await assertProductionUnitUnchanged(productionBefore);
  return { child: null, pid, opts, lease };
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
  throw new Error("throwaway systemd watcher did not become active");
}

// ---- In-process seams for deterministic storage/negative tests -------------
function fakeSystemdFacts(pid, overrides = {}) {
  const now = new Date().toISOString();
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
async function fakeReadInvocation(pid) { return "0123456789abcdef0123456789abcdef"; }

function leaseJson(identity, fx, overrides = {}) {
  const now = new Date().toISOString();
  return {
    schemaVersion: 1,
    kind: "pixel-maintenance-recovery-guardian-lease",
    operation: "pixel-work-model-qualification-maintenance-recovery",
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
    ready: true,
    readyAt: now,
    createdAt: now,
    refreshedAt: now,
    ...overrides,
  };
}

async function writeLease(fx, lease) {
  await writeFile(deriveGuardianLeasePath(fx.lockPath), `${JSON.stringify(lease, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(deriveGuardianLeasePath(fx.lockPath), 0o600);
}

test("a real separate-process watcher lease under a live user systemd unit is accepted", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  const lease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  assert.equal(lease.pid, pid);
  assert.equal(lease.unit, opts.unit, "the live proof must bind a unique non-production proof unit, never the production unit");
  assert.notEqual(lease.unit, GUARDIAN_UNIT, "the proof must never use the production guardian unit name");
  assert.equal(lease.guardianModuleSha256, fx.moduleSha);
  assert.equal(lease.custodyIdentitySha256, fx.custodySha);
  assert.match(lease.invocationId, /^[0-9a-f]{32}$/u, "the lease must bind a nonempty exact systemd invocation id");
});

test("a plain lease without exact readiness is not sufficient (ready=false is rejected)", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  const lease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  assert.equal(lease.ready, true);
  await writeLease(fx, { ...lease, ready: false, readyAt: null });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /not proven ready/u);
});

test("live-require is blocked when the live systemd facts report a missing or non-empty drop-in set, before the lease can authorize maintenance", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  // Baseline: exact empty drop-in facts authorize the live lease.
  const ok = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  assert.equal(ok.ready, true);
  const factsWithDropIn = () => ({
    unit: lease.unit, activeState: "active", subState: "running",
    mainPid: pid, invocationId: lease.invocationId,
    controlGroup: lease.controlGroup, fragmentPath: lease.fragmentPath,
    fragmentSha256: lease.unitSha256, dropInPaths: ["/tmp/pixel-maintenance-recovery.service.d/override.conf"],
  });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...opts, systemd: { facts: factsWithDropIn } }), /exact empty systemd drop-in set/u);
  const missing = () => { const f = factsWithDropIn(); delete f.dropInPaths; return f; };
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...opts, systemd: { facts: missing } }), /exact empty systemd drop-in set/u);
  const malformed = () => ({ ...factsWithDropIn(), dropInPaths: "not-an-array" });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...opts, systemd: { facts: malformed } }), /exact empty systemd drop-in set/u);
});

test("an absent guardian lease fails closed", async (t) => {
  const fx = await fixture(t);
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, digest("a"), bindingOptions(fx)), /could not be opened safely|not owner-private|not strict JSON/u);
});

test("an unrelated current live PID with the expected unit string is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const unrelated = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000)"], { stdio: "ignore" });
  t.after(() => { try { unrelated.kill("SIGKILL"); } catch {} });
  await delay(50);
  const identity = await deriveProcessIdentity(unrelated.pid);
  await writeLease(fx, leaseJson(identity, fx));
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, bindingOptions(fx)), /argv|module|cgroup|invocation|MainPID|ControlGroup|FragmentPath/u);
});

test("a missing invocation id lease is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, invocationId: null });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /invocation id/u);
});

test("a mismatched invocation id is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, invocationId: "ffffffffffffffffffffffffffffffff" });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /invocation id/u);
});

test("a session-scoped cgroup (no .service path) is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, cgroup: ["0::/user.slice/user-1000.slice/session-1.scope"] });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /cgroup|session-scoped|unit identity|ControlGroup/u);
});

test("a wrong UID is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, uid: "99999" });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /UID/u);
});

test("a PID-start mismatch is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, startTicks: lease.startTicks + 1 });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /start identity/u);
});

test("a wrong executable identity is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, exe: "/usr/bin/other" });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /executable/u);
});

test("a wrong argv identity is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, argv: [lease.argv[0], basename(WATCHER), "watch", "--config", join(fx.root, "maintenance.json")] });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /argv|module|config/u);
});

test("a wrong cgroup identity is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, cgroup: ["0::/user.slice/other.service"] });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /cgroup|unit identity|ControlGroup/u);
});

test("a substituted unit fragment sha is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, unitSha256: digest("f"), installedFragmentSha256: digest("f") });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /installed fragment sha|unit bytes|reviewed expected render/u);
});

test("a wrong-code guardian module is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, guardianModuleSha256: digest("f") });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /different guardian code version/u);
});

test("a wrong custody identity is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts, lease } = await startWatcher(t, fx);
  await writeLease(fx, { ...lease, custodyIdentitySha256: digest("e") });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /custody identity differs/u);
});

test("a wrong-config lease fails closed", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, digest("b"), { ...opts, configSha256: digest("b") }), /different maintenance configuration/u);
});

test("a stale lease fails closed", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  const lease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  await writeLease(fx, { ...lease, refreshedAt: new Date(Date.now() - 60 * 60 * 1000).toISOString() });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...opts, maxAgeMs: DEFAULT_GUARDIAN_LEASE_TTL_MS }), /stale|incoherent/u);
});

test("a far-future freshness bypass is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  const lease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  await writeLease(fx, { ...lease, refreshedAt: new Date(Date.now() + 60 * 60 * 1000).toISOString() });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /stale|incoherent/u);
});

test("a dead guardian lease fails closed", { skip: !linux ? "process identity proof is Linux-only" : false }, async (t) => {
  const fx = await fixture(t);
  const bootId = await readCurrentBootId();
  await writeLease(fx, leaseJson({ bootId: bootId ?? "unused", pid: 99999999, startTicks: 1, exe: "/nonexistent", argv: ["/usr/bin/node", WATCHER, "watch", "--config", join(fx.root, "maintenance.json")], cgroup: ["0::/user.slice/pixel-maintenance-recovery.service"], uid: String(fx.uid) }, fx));
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, bindingOptions(fx)), /process is not alive/u);
});

test("a substituted unit contract fails closed", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx, { unit: "other.service" });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, bindingOptions(fx)), /does not match the expected supervised unit contract|does not bind the reviewed expected unit render/u);
});

test("boot identity read failure fails closed", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...opts, readBootId: async () => null }), /boot identity cannot be proven/u);
});

test("a different boot identity is rejected", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  const lease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  await writeLease(fx, { ...lease, bootId: "00000000-0000-0000-0000-000000000001" });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /boot identity/u);
});

test("lease storage refuses a precreated temp link or file at the exact predicted temp name (O_EXCL/no-follow)", { skip: !linux ? "secure lease storage is Linux-only" : false }, async (t) => {
  const fx = await fixture(t);
  const identity = await deriveProcessIdentity(process.pid);
  const leasePath = deriveGuardianLeasePath(fx.lockPath);
  const tempSuffix = "0".repeat(32);
  const tempName = `.${basename(leasePath)}.tmp-${process.pid}-${identity.startTicks}-${tempSuffix}`;
  const systemd = { facts: async () => fakeSystemdFacts(process.pid) };
  const base = { custodyIdentitySha256: fx.custodySha, guardianModuleSha256: fx.moduleSha, expectedUnitSha256: digest("1"), expectedNodePath: EXPECTED_NODE, systemd, readInvocationIdFromProc: fakeReadInvocation };
  // A precreated symlink at the exact predicted temp name must fail closed (EEXIST).
  const target = join(fx.root, "attacker-target");
  await writeFile(target, "owned\n", { mode: 0o600 });
  await symlink(target, join(dirname(leasePath), tempName));
  await assert.rejects(refreshGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...base, tempSuffix }), /EEXIST|already exists|temporary/u);
  assert.equal(await readFile(target, "utf8"), "owned\n", "the precreated symlink must never be followed or truncated");
  // A precreated regular file at the exact predicted temp name must also fail closed.
  const tempFile = join(dirname(leasePath), tempName);
  await rm(tempFile, { force: true });
  await writeFile(tempFile, "occupied\n", { mode: 0o600 });
  await assert.rejects(refreshGuardianLease(fx.lockPath, fx.uid, fx.configSha, { ...base, tempSuffix }), /EEXIST|already exists|temporary/u);
  assert.equal(await readFile(tempFile, "utf8"), "occupied\n", "a precreated temp file must never be truncated or renamed over");
});

test("lease storage fails closed on a substituted parent symlink and a stale predictable temp cannot block a restart", { skip: !linux ? "secure lease storage is Linux-only" : false }, async (t) => {
  const fx = await fixture(t);
  const identity = await deriveProcessIdentity(process.pid);
  const leasePath = deriveGuardianLeasePath(fx.lockPath);
  const systemd = { facts: async () => fakeSystemdFacts(process.pid) };
  const base = { custodyIdentitySha256: fx.custodySha, guardianModuleSha256: fx.moduleSha, expectedUnitSha256: digest("1"), expectedNodePath: EXPECTED_NODE, systemd, readInvocationIdFromProc: fakeReadInvocation };
  // A substituted parent symlink (the parent path resolves elsewhere) must fail.
  const realParent = join(fx.root, "real-parent");
  await writeFile(join(fx.root, "guardian.lock"), "", { mode: 0o600 });
  const swappedRoot = await mkdtemp(join(tmpdir(), "pixel-guardian-swap-"));
  t.after(() => rm(swappedRoot, { recursive: true, force: true }));
  const oldLeasePath = deriveGuardianLeasePath(join(realParent, "guardian.lock"));
  const newLock = join(swappedRoot, "guardian.lock");
  await writeFile(newLock, "", { mode: 0o600 });
  // Point the lease parent at a symlink that resolves to a different real dir.
  const symParent = join(fx.root, "parent-link");
  await symlink(swappedRoot, symParent);
  await assert.rejects(refreshGuardianLease(join(symParent, "guardian.lock"), fx.uid, fx.configSha, base), /substituted symlink|not the exact intended real directory|not owner-private|not accessible|parent/u);
  // A stale temp left by a prior (killed) watcher at the OLD predictable name
  // (pid+startTicks only, no random suffix) must NOT block a restarted watcher:
  // the random suffix means the fresh temp name is unpredictable.
  const staleTemp = join(dirname(leasePath), `.${basename(leasePath)}.tmp-${process.pid}-${identity.startTicks}`);
  await writeFile(staleTemp, "stale\n", { mode: 0o600 });
  // refreshGuardianLease durably validates and writes its own fresh lease; the
  // stale predictable-name temp must not block it. (The test process is not the
  // supervised guardian watcher, so the live argv-contract proof is not applied
  // here; the storage-level write/read-back is what must not be blocked.)
  const written = await refreshGuardianLease(fx.lockPath, fx.uid, fx.configSha, base);
  assert.ok(written.lease, "a stale predictable-name temp must not block a fresh watcher lease");
  assert.equal(await readFile(staleTemp, "utf8"), "stale\n", "the stale temp must be left untouched");
});

test("an owner-private corrupt/truncated existing qualification lease is atomically replaced by a fresh valid M1-shape lease", { skip: !linux ? "secure lease storage is Linux-only" : false }, async (t) => {
  const fx = await fixture(t);
  const leasePath = deriveGuardianLeasePath(fx.lockPath);
  const systemd = { facts: async () => fakeSystemdFacts(process.pid) };
  const base = { custodyIdentitySha256: fx.custodySha, guardianModuleSha256: fx.moduleSha, expectedUnitSha256: digest("1"), expectedNodePath: EXPECTED_NODE, systemd, readInvocationIdFromProc: fakeReadInvocation };
  // An owner-private, corrupt/truncated prior qualification lease must not
  // block the guardian from publishing a fresh valid lease (M1 recovery).
  await writeFile(leasePath, `{"schemaVersion":1,"kind":"pixel-maintenance-recov`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(leasePath, 0o600);
  const written = await refreshGuardianLease(fx.lockPath, fx.uid, fx.configSha, base);
  // The corrupt lease was atomically replaced; the resulting serialized lease
  // retains the exact M1 qualification shape (no authority keys).
  const onDisk = JSON.parse(await readFile(leasePath, "utf8"));
  const validated = validateGuardianLease(written.lease);
  assert.deepEqual(onDisk, validated, "the on-disk lease must equal the freshly validated M1-shape lease");
  assert.equal(await readFile(leasePath, "utf8"), `${JSON.stringify(validated, null, 2)}\n`, "the on-disk bytes must be the exact serialized fresh lease");
  assert.ok(!("campaignOperationSha256" in onDisk) && !("journalIdentitySha256" in onDisk), "the M1 qualification lease must carry no campaign authority keys");
  assert.equal(onDisk.kind, "pixel-maintenance-recovery-guardian-lease");
  assert.equal(onDisk.operation, "pixel-work-model-qualification-maintenance-recovery");
});

test("the current watcher may remove its own lease", { skip: !linux ? "secure lease storage is Linux-only" : false }, async (t) => {
  const fx = await fixture(t);
  const systemd = { facts: async () => fakeSystemdFacts(process.pid) };
  await refreshGuardianLease(fx.lockPath, fx.uid, fx.configSha, { custodyIdentitySha256: fx.custodySha, guardianModuleSha256: fx.moduleSha, expectedUnitSha256: digest("1"), expectedNodePath: EXPECTED_NODE, systemd, readInvocationIdFromProc: fakeReadInvocation });
  await removeGuardianLease(fx.lockPath, fx.uid);
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, bindingOptions(fx)), /could not be opened safely/u);
});

test("a lease owned by another live watcher may not be removed", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  // A lease owned by a different live process must not be removable by this one.
  const { pid } = await startWatcher(t, fx);
  await assert.rejects(removeGuardianLease(fx.lockPath, fx.uid), /does not belong to the current watcher identity/u);
  assert.equal(pid > 0, true);
});

test("a substituted unit where lease and live systemd facts agree with each other but disagree with the reviewed expected render fails closed", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  const lease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  // Both the lease and the live systemd fragment agree on the SUBSTITUTED bytes,
  // but the reviewed expected render (what maintenance reviewed) differs.
  const substitutedSha = digest("9");
  await writeLease(fx, { ...lease, unitSha256: substitutedSha, installedFragmentSha256: substitutedSha, expectedUnitSha256: substitutedSha });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /reviewed expected render|does not bind the reviewed expected unit render|live unit bytes do not match/u);
});

test("an alternate absolute Node/runtime path is rejected even when lease and live facts agree", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const { pid, opts } = await startWatcher(t, fx);
  const lease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts);
  // The lease and live process agree on an alternate absolute node path that is
  // not the reviewed expected node real path.
  const alt = "/opt/pixel/bin/alt-node";
  await writeLease(fx, { ...lease, exe: alt, argv: [alt, ...lease.argv.slice(1)], expectedNodePath: alt });
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /expected node real path|does not bind the exact expected node|argv node path/u);
});

test("a real-process watcher whose subsequent refresh fails exits nonzero, its ready lease is durably absent, and requireLiveGuardianLease cannot authorize", { skip: !hasSystemd ? "user systemd is not available on this host (required supported-host gate)" : false }, async (t) => {
  const fx = await fixture(t);
  const opts = bindingOptions(fx);
  opts.guardianModulePath = REFRESH_FAIL_WATCHER;
  const unit = uniqueProofUnit();
  opts.unit = unit;
  const productionBefore = await snapshotProductionUnit();
  const expectedShaFile = join(fx.root, "expected-unit.sha");
  // Deterministic owner-private acknowledgement handshake: the ack file lives
  // inside the random owner-private fixture root and is only written by this
  // test once requireLiveGuardianLease has observed the ready lease. It is
  // passed to the throwaway unit as a safely bounded, whitespace-free value.
  const ackPath = join(fx.root, "ready-ack");
  const ackToken = digest("7");
  assertOwnerPrivateAckPath(ackPath, fx.root);
  const ackEnv = [
    renderAckEnvironment("ACK_FILE", ackPath),
    renderAckEnvironment("ACK_TOKEN", ackToken),
  ].join("\n");
  const unitFile = `[Unit]\nDescription=pixel unique non-production guardian refresh-fail proof\n[Service]\nType=simple\nEnvironment=CUSTODY_LOCK_PATH=${fx.lockPath}\nEnvironment=UID=${fx.uid}\nEnvironment=CONFIG_SHA=${opts.configSha256}\nEnvironment=UNIT=${unit}\nEnvironment=CUSTODY_IDENTITY_SHA=${opts.custodyIdentitySha256}\nEnvironment=GUARDIAN_MODULE_SHA=${opts.guardianModuleSha256}\nEnvironment=EXPECTED_UNIT_SHA_FILE=${expectedShaFile}\nEnvironment=EXPECTED_NODE=${opts.expectedNodePath}\nEnvironment=FAIL_ON_REFRESH=1\n${ackEnv}\nExecStart=${process.execPath} ${REFRESH_FAIL_WATCHER} watch --config ${opts.configPath}\nRestart=no\n[Install]\n`;
  opts.expectedUnitSha256 = createHash("sha256").update(unitFile, "utf8").digest("hex");
  await writeFile(expectedShaFile, `${opts.expectedUnitSha256}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(expectedShaFile, 0o600);
  await acquireGuardianUnitLock();
  t.after(async () => {
    try { await removeThrowawayUnit(unit); }
    finally {
      await releaseGuardianUnitLock();
      // Clean proof artifacts even on failure and prove the production unit is
      // byte/state identical after the whole proof lifecycle.
      await assertProductionUnitUnchanged(productionBefore);
    }
  });
  await removeThrowawayUnit(unit);
  await installThrowawayUnit(unitFile, unit);
  await execFile("systemctl", ["--user", "start", unit], { encoding: "utf8", timeout: 30000 });
  const pid = await waitForMainPid(unit, t);
  // The fixture publishes a durable ready lease; once observed and validated we
  // signal the singular owner-private ack so the injected refresh failure is
  // ordered deterministically rather than by timing luck.
  const readyDeadline = Date.now() + 30000;
  let readyLease = null;
  while (Date.now() < readyDeadline) {
    try { readyLease = await requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts); break; }
    catch { await delay(100); }
  }
  assert.ok(readyLease && readyLease.ready === true, "the fixture must publish a verifiable ready lease before the injected refresh failure");
  await writeFile(ackPath, `${ackToken}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(ackPath, 0o600);
  // Wait for the fixture's injected refresh failure to durably remove the lease
  // and exit nonzero (Restart=no so the unit leaves the active state).
  const exitDeadline = Date.now() + 30000;
  let exited = false;
  while (Date.now() < exitDeadline) {
    const { stdout } = await execFile("systemctl", ["--user", "show", unit, "--property=ActiveState,SubState,MainPID"], { encoding: "utf8", timeout: 10000 });
    if (/ActiveState=(inactive|failed)/u.test(stdout)) { exited = true; break; }
    await delay(100);
  }
  assert.ok(exited, "the unique proof unit must exit after the injected refresh failure");
  // The prior ready lease must be durably absent.
  const leasePath = deriveGuardianLeasePath(fx.lockPath);
  await assert.rejects(readFile(leasePath, "utf8"), /ENOENT|no such file/u);
  // requireLiveGuardianLease cannot authorize once the ready lease is gone.
  await assert.rejects(requireLiveGuardianLease(fx.lockPath, fx.uid, fx.configSha, opts), /guardian lease could not be opened safely|no such file|fails closed/u);
  await assertProductionUnitUnchanged(productionBefore);
});
