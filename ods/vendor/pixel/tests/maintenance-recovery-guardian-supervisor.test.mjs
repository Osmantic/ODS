import assert from "node:assert/strict";
import { chmod, lstat, mkdir, mkdtemp, readdir, readFile, rename, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  buildSystemdEnv,
  classifyIsEnabled,
  parseArgs,
  secureReadUnitFile,
  superviseInstall,
  superviseRemove,
  superviseReview,
  systemdShowUnit,
  validatePriorGuardianUnit,
  validateUnitName,
  writeUnitFileAtomically,
} from "../deploy/work-controller/maintenance-recovery-guardian-supervisor.mjs";
import { renderGuardianUnit } from "../deploy/work-controller/maintenance-recovery-guardian-unit.mjs";
import { withMaintenanceCustody } from "../deploy/work-controller/maintenance-custody.mjs";
import { createRecoveryJournal, inspectActiveRecoveryJournal } from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import { deriveGuardianLeasePath } from "../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";
import { modelQualificationMaintenanceBoundaries } from "../deploy/work-controller/model-qualification-maintenance.mjs";

const ROOT = resolve(new URL("..", import.meta.url).pathname);
const GUARDIAN = join(ROOT, "deploy/work-controller/maintenance-recovery-guardian.mjs");
const digest = (character) => character.repeat(64);
const uid = process.geteuid?.() ?? 1000;
// The supervisor resolves the trusted node executable from its fixed production
// default (/usr/bin/node), never from an ambient PATH or process.execPath. The
// fixture render must bind the same path so its exact prior-unit and render-SHA
// contracts remain valid when CI launches the suite from a private toolchain.
const NODE_PATH = "/usr/bin/node";

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function fixture(t, { unsafePath = null } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-supervisor-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const journalDir = join(root, "journal");
  const stateRoot = unsafePath ?? join(root, "state");
  await Promise.all([mkdir(journalDir, { recursive: true }), mkdir(stateRoot, { recursive: true })]);
  const configPath = join(root, "maintenance.json");
  const dockerQualPath = join(root, "docker.json");
  const backendConfigPath = join(root, "backend.json");
  const environmentPath = join(root, "env.json");
  const custodyLockPath = join(journalDir, "guardian.lock");
  await writeFile(custodyLockPath, "", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(custodyLockPath, 0o600);
  const unitDir = join(root, "systemd", "user");
  await mkdir(unitDir, { recursive: true });
  if (process.platform !== "win32") await chmod(unitDir, 0o700);
  const unitFile = join(unitDir, "pixel-maintenance-recovery.service");
  const production = {
    containerName: "pixel-production-model", containerId: digest("1"), imageDigest: `sha256:${digest("2")}`,
    expectedStartedAt: "2026-08-13T12:00:00Z", expectedRestartCount: 0, stopTimeoutSeconds: 120,
    restoreTimeoutSeconds: 7200, probeIntervalMilliseconds: 1000, readinessOrigin: "http://127.0.0.1:8000",
    readinessModelId: "DeepSeek-V4-Flash-0731", maxResponseBytes: 1048576,
  };
  await privateWrite(configPath, {
    $schema: "https://osmantic.com/pixel/schemas/work-model-qualification-maintenance-v1.schema.json",
    schemaVersion: 1, qualificationDockerConfigPath: dockerQualPath,
    custody: { kind: "advisory-flock", lockPath: custodyLockPath, acquireTimeoutSeconds: 30 }, production,
    boundary: modelQualificationMaintenanceBoundaries.configuration,
  });
  await privateWrite(dockerQualPath, {
    $schema: "https://osmantic.com/pixel/schemas/work-model-qualification-docker-v1.schema.json",
    schemaVersion: 1, backendConfigPath, qualificationConfigPath: join(root, "q.json"),
    runnerImageDigest: `sha256:${digest("3")}`, evidenceRoot: join(root, "ev"),
    boundary: "Owner-private exact local-model qualification inputs only. Configuration grants no model start, container, network, device, credential, external-effect, completion, publication, deployment, acceptance, or promotion authority.",
  });
  await privateWrite(backendConfigPath, {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json",
    schemaVersion: 1, environmentPath, modelSource: { kind: "file", path: "/srv/pixel/models/model.gguf" },
    runtimeCacheSeed: null, backendNetwork: { subnet: "172.31.255.0/29" }, containerUser: { uid: 10001, gid: 10001 },
    publishLoopbackPort: null, resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 128 },
    readiness: { startupTimeoutSeconds: 10, probeIntervalMilliseconds: 5000 }, accelerator: { class: "cpu", count: 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: 0, flashAttention: false },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  });
  await privateWrite(environmentPath, {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-environment-v1.schema.json",
    schemaVersion: 1, stateRoot, policyPath: join(root, "p.json"), objectStore: join(root, "ob"),
    workspaceRoot: join(root, "ws"), executorPath: "/opt/pixel-work/bin/omp", modelBackendLaunchPath: join(root, "bl.json"),
    archiveLimits: { maxEntries: 10000, maxFileBytes: 1073741824 },
    runtime: { dockerPath: "/usr/bin/docker", backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model", networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11", uid: 10001, gid: 10001 },
    boundary: "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.",
  });
  const render = await renderGuardianUnit({ configPath, nodePath: NODE_PATH, guardianPath: GUARDIAN, expectedOwnerUid: uid });
  return { root, configPath, custodyLockPath, journalDir, stateRoot, unitDir, unitFile, render };
}

const SHOW_KEYS = ["ActiveState", "SubState", "MainPID", "InvocationID", "ControlGroup", "FragmentPath", "LoadState", "DropInPaths"];
// Filesystem-aware fake systemctl: a present unit file is "loaded"; once the
// file is removed the same unit is "not-found". This lets the supervisor's
// install/remove state machine observe the real file lifecycle without any
// hidden production env/executable override.
function makeSystemctl(behavior = {}, unitPath) {
  const counts = {};
  let stopped = false;
  let started = false;
  return async function fakeRun(args) {
    const cmd = args[1];
    counts[cmd] = (counts[cmd] || 0) + 1;
    if (cmd === "stop") stopped = true;
    if (cmd === "start") started = true;
    let present = false;
    if (unitPath) { try { await lstat(unitPath); present = true; } catch {} }
    if (behavior.fail?.[cmd]) return { code: 5, stdout: "", stderr: `fake ${cmd} failed` };
    if (behavior.failNth?.[cmd] === counts[cmd]) return { code: 5, stdout: "", stderr: `fake ${cmd} failed on call ${counts[cmd]}` };
    const loadedFacts = (state) => ({
      LoadState: "loaded",
      ActiveState: state.state ?? behavior.state ?? "active",
      SubState: state.sub ?? behavior.sub ?? "running",
      MainPID: state.pid ?? behavior.pid ?? "12345",
      FragmentPath: state.fragmentPath ?? behavior.fragmentPath ?? unitPath ?? "",
      InvocationID: "0".repeat(32),
      ControlGroup: "/user.slice/user-1000.slice/user@1000.service/app.slice",
      DropInPaths: state.dropIns ?? behavior.dropIns ?? "",
    });
    const notFoundFacts = { LoadState: "not-found", ActiveState: "inactive", SubState: "dead", MainPID: "0", FragmentPath: "", InvocationID: "", ControlGroup: "", DropInPaths: "" };
    if (cmd === "show") {
      const propArg = args.find((a) => a.startsWith("--property="));
      const keys = propArg ? propArg.slice("--property=".length).split(",") : SHOW_KEYS;
      let facts;
      if (!present) facts = notFoundFacts;
      else if (started && behavior.postStart) facts = loadedFacts(behavior.postStart);
      else if (stopped && behavior.afterStop === false) facts = loadedFacts(behavior);
      else if (stopped) facts = loadedFacts(behavior.afterStop ?? { state: "inactive", sub: "dead", pid: "0" });
      else facts = loadedFacts(behavior);
      return { code: 0, stdout: `${keys.map((k) => `${k}=${facts[k]}`).join("\n")}\n`, stderr: "" };
    }
    if (cmd === "is-enabled") {
      if (!present) return { code: 4, stdout: "not-found\n", stderr: "" };
      const enabled = behavior.enabled ?? false;
      return { code: enabled ? 0 : 1, stdout: `${enabled ? "enabled" : "disabled"}\n`, stderr: "" };
    }
    return { code: 0, stdout: "", stderr: "" };
  };
}
const fakeCustody = (binding, expectedOwnerUid, operation) => operation("custody-identity-sha");
const validateFakeSystemdRuntime = async (expectedOwnerUid) => {
  assert.equal(expectedOwnerUid, uid, "the injected runtime seam must remain bound to the test owner");
};

function installOptions(fx, behavior) {
  return { configPath: fx.configPath, userSystemdDir: fx.unitDir, confirmInstallSha256: null, withCustody: fakeCustody, runSystemctl: makeSystemctl(behavior, fx.unitFile), validateSystemdRuntime: validateFakeSystemdRuntime };
}
function removeOptions(fx, behavior) {
  return { configPath: fx.configPath, userSystemdDir: fx.unitDir, confirmRemoveSha256: null, withCustody: fakeCustody, runSystemctl: makeSystemctl(behavior, fx.unitFile), validateSystemdRuntime: validateFakeSystemdRuntime, leaseWaitSeconds: 1, leasePollMs: 20 };
}

test("prior-unit parser rejects every adversarial shape while accepting byte-equal render", async (t) => {
  const fx = await fixture(t);
  const renderUnit = fx.render.unit;
  const argv = { nodePath: NODE_PATH, guardianPath: GUARDIAN, configPath: fx.configPath, renderUnit };
  assert.equal(validatePriorGuardianUnit(renderUnit, argv), true, "byte-equal render is the idempotent case");
  assert.equal(validatePriorGuardianUnit(renderUnit.replace("RestartSec=30", "RestartSec=45"), argv), true, "benign RestartSec change is a valid prior unit");
  const reject = (content, label) => {
    try { validatePriorGuardianUnit(content, argv); assert.fail(`${label} must be rejected`); }
    catch (error) { assert.match(error.message, /prior unit|unit|ExecStart|ReadWritePaths/u, label); }
  };
  reject("# Pixel maintenance recovery guardian\n" + renderUnit, "leading comment trick");
  reject(renderUnit.replace("Type=exec", "Type=simple"), "alternate service type");
  reject(renderUnit.replace("ExecStart=", "ExecStart=/bin/sh -c "), "shell interpreter in ExecStart");
  reject(renderUnit.replace("watch --config", "watch --config2"), "alternate config argv");
  reject(renderUnit.replace(NODE_PATH, "/usr/bin/alternate-node"), "alternate node path");
  reject(renderUnit.replace(GUARDIAN, "/opt/pixel/other-guardian.mjs"), "alternate guardian module");
  reject(renderUnit + "[Service]\nExecStartPre=/bin/true\n", "extra execution hook section");
  reject(renderUnit.replace("NoNewPrivileges=true", "AmbientCapabilities=CAP_NET_ADMIN\nNoNewPrivileges=true"), "privilege-expanding directive");
  reject(renderUnit.replace("ReadWritePaths=", "ReadWritePaths=/ /"), "broadened ReadWritePaths");
  reject(renderUnit.replace("RestrictSUIDSGID=true", "RestrictSUIDSGID=true\nExecStop=/bin/true"), "extra ExecStop hook");
  reject(renderUnit.replace("[Unit]", "[Unit]\nCustomDirective=1"), "unknown directive");
  reject(renderUnit.replace("Description=", "Description=\nDescription="), "duplicate directive");
  reject(renderUnit.replace("RestartSec=30", "RestartSec=0"), "non-positive RestartSec");
});

test("install rolls back the exact prior unit on every post-write failure boundary", async (t) => {
  // Each mutation-time failure uses failNth so only the install's own step
  // fails; the rollback's compensation must succeed and prove exact restore.
  const boundaries = [
    { name: "daemon-reload", behavior: { failNth: { "daemon-reload": 1 } } },
    { name: "enable", behavior: { failNth: { enable: 1 } } },
    { name: "start", behavior: { failNth: { start: 1 } } },
    { name: "enable-verify", behavior: { enabled: false } },
  ];
  for (const { name, behavior } of boundaries) {
    const fx = await fixture(t);
    await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
    await chmod(fx.unitFile, 0o600);
    const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
    const options = installOptions(fx, behavior);
    options.confirmInstallSha256 = review.installConfirmSha256;
    await assert.rejects(superviseInstall(options), /exact prior unit and full prior state were restored/u, name);
    assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, `${name} must restore the exact prior bytes`);
  }
});

test("install fails when the started unit is not active/running with MainPID > 0 and rolls back exactly", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, { state: "active", sub: "running", pid: "12345", enabled: true });
  options.confirmInstallSha256 = review.installConfirmSha256;
  let startCount = 0;
  const original = options.runSystemctl;
  options.runSystemctl = async function (args) {
    if (args[1] === "start") startCount += 1;
    const result = await original(args);
    if (args[1] === "show" && result.stdout && startCount === 1) {
      // Sabotage only the install's first post-start verification (MainPID=0),
      // leaving the rollback's restart proof intact.
      result.stdout = result.stdout.replace(/MainPID=\d+/u, "MainPID=0");
    }
    return result;
  };
  await assert.rejects(superviseInstall(options), /exact prior unit and full prior state were restored/u);
  assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, "rollback must restore the exact prior bytes");
});

test("install rolls back exactly when an atomic writer replaces the destination and then throws", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, { state: "active", sub: "running", pid: "12345", enabled: true });
  options.confirmInstallSha256 = review.installConfirmSha256;
  let writes = 0;
  options.writeUnitFile = async (path, bytes, owner) => {
    writes += 1;
    if (writes === 1) {
      // The atomic writer replaced the destination and then threw during
      // post-write fsync/verification. The supervisor must treat this as
      // potentially mutated and roll back exactly, never skip rollback.
      await writeFile(path, "MUTATED-THEN-THREW\n", { mode: 0o600 });
      await chmod(path, 0o600);
      throw new Error("post-write fsync/verify failure");
    }
    // The rollback restore write succeeds with the exact prior bytes.
    await writeFile(path, bytes, { mode: 0o600 });
    await chmod(path, 0o600);
  };
  await assert.rejects(superviseInstall(options), /exact prior unit and full prior state were restored/u);
  assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, "the mutated destination must be rolled back to the exact prior bytes");
});

test("remove rolls back the exact prior unit on every post-disable failure boundary", async (t) => {
  const boundaries = [
    { name: "disable", behavior: { fail: { disable: true } } },
    { name: "stop", behavior: { fail: { stop: true } } },
    { name: "lease-wait", behavior: { leasePresent: true } },
    { name: "unit-removal", behavior: { fail: { remove: true } } },
    { name: "reload-after-removal", behavior: { failNth: { "daemon-reload": 1 } } },
    { name: "postflight-state", behavior: { state: "active", sub: "running", pid: "999", afterStop: false } },
  ];
  for (const { name, behavior } of boundaries) {
    const fx = await fixture(t);
    await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
    await chmod(fx.unitFile, 0o600);
    const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
    // postStart lets the rollback prove an active restart after the remove
    // flow's stop (a real restart gets a fresh positive MainPID).
    const options = removeOptions(fx, { enabled: true, state: "active", sub: "running", pid: "12345", postStart: { state: "active", sub: "running", pid: "67890" }, ...behavior });
    options.confirmRemoveSha256 = review.removeConfirmSha256;
    if (behavior.leasePresent) {
      options.waitForLeaseDisappearance = async () => false;
    }
    if (behavior.fail?.remove) {
      options.durableUnlink = async () => { throw new Error("durable unlink failed"); };
    }
    await assert.rejects(superviseRemove(options), /exact prior unit and full prior state were restored/u, name);
    assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, `${name} must restore the exact prior unit bytes`);
  }
});

test("remove proves a journal cannot appear between the no-journal check and disable/stop because custody is held", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const binding = { kind: "advisory-flock", lockPath: fx.custodyLockPath, acquireTimeoutSeconds: 30 };
  let concurrentAcquired = false;
  const assertNoJournalUnderCustody = async (lockPath, expectedOwnerUid) => {
    try {
      await withMaintenanceCustody(binding, expectedOwnerUid, async () => { concurrentAcquired = true; });
    } catch { /* blocked as required */ }
    assert.equal(concurrentAcquired, false, "a second custody holder must be blocked while remove holds custody");
  };
  const options = removeOptions(fx, { enabled: true, state: "active", sub: "running", pid: "12345" });
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  options.withCustody = (b, u, op) => withMaintenanceCustody(b, u, op);
  options.assertNoActiveRecoveryJournal = assertNoJournalUnderCustody;
  await superviseRemove(options);
  assert.equal(concurrentAcquired, false);
  assert.equal(await inspectActiveRecoveryJournal(fx.custodyLockPath, uid), null, "no journal may appear across the removal");
});

test("absent remove no-op proves the exact absent systemd state and binds the confirmation", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  assert.ok(review.removeConfirmSha256, "review must provide an exact confirmation for an absent no-op");
  const options = removeOptions(fx, {});
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  const result = await superviseRemove(options);
  assert.equal(result.ok, true);
  assert.equal(result.removed, false, "an absent no-op reports removed:false only after proving exact absence");
});

test("absent remove refuses a confirmation mismatch before reporting a no-op", async (t) => {
  const fx = await fixture(t);
  await assert.rejects(superviseRemove(removeOptions(fx, {})), /remove SHA mismatch/u);
});

test("absent remove refuses when the unit reports loaded instead of exact not-found", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, {});
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  const original = options.runSystemctl;
  options.runSystemctl = async function (args) {
    const result = await original(args);
    if (args[1] === "show" && result.stdout) {
      // File is absent but show reports loaded with a fragment path.
      result.stdout = result.stdout.replace(/LoadState=not-found/u, "LoadState=loaded").replace(/FragmentPath=$/mu, `FragmentPath=${fx.unitFile}`);
    }
    return result;
  };
  await assert.rejects(superviseRemove(options), /does not reconcile|absent unit file/u);
});

test("absent remove refuses when systemctl show is a bus/show error instead of exact not-found", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, {});
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  const fallback = makeSystemctl({}, fx.unitFile);
  options.runSystemctl = async function (args) {
    if (args[1] === "show") return { code: 5, stdout: "", stderr: "bus error" };
    return fallback(args);
  };
  await assert.rejects(superviseRemove(options), /prior systemd state could not be captured|show failed/u);
});

test("absent remove refuses an unknown is-enabled classification", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, {});
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  const original = options.runSystemctl;
  options.runSystemctl = async function (args) {
    const result = await original(args);
    if (args[1] === "is-enabled") return { code: 5, stdout: "not-found\n", stderr: "" };
    return result;
  };
  await assert.rejects(superviseRemove(options), /unknown or unclosed|not reconcile|absent unit file/u);
});

test("install from exact absence rolls back by actively removing residual enablement and proving absent", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, { state: "active", sub: "running", pid: "12345", enabled: true, fail: { start: true } });
  options.confirmInstallSha256 = review.installConfirmSha256;
  let disableCalls = 0;
  const original = options.runSystemctl;
  options.runSystemctl = async function (args) {
    if (args[1] === "disable") disableCalls += 1;
    return original(args);
  };
  await assert.rejects(superviseInstall(options), /exact prior unit and full prior state were restored/u);
  assert.ok(disableCalls >= 1, "rollback must actively remove residual enablement");
  await assert.rejects(readFile(fx.unitFile, "utf8"), /ENOENT|no such file/u, "the partially-installed unit must be removed back to exact absence");
});

test("journal and lease absence distinguish ENOENT from permission/I/O errors", async (t) => {
  const fx = await fixture(t);
  assert.equal(await inspectActiveRecoveryJournal(fx.custodyLockPath, uid), null, "absent journal is ENOENT -> null");
  const root2 = await mkdtemp(join(tmpdir(), "pixel-eacces-"));
  t.after(() => rm(root2, { recursive: true, force: true }));
  const dir = join(root2, "locked");
  await mkdir(dir, { mode: 0o700 });
  await chmod(dir, 0o000);
  const lockPath2 = join(dir, "guardian.lock");
  await assert.rejects(inspectActiveRecoveryJournal(lockPath2, uid), /EACCES|not accessible|permission|opened safely|no such file/i);
  await chmod(dir, 0o700);
});

test("a symlinked or non-owner unit path is refused on install", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, {});
  options.confirmInstallSha256 = review.installConfirmSha256;
  const target = join(fx.root, "target.service");
  await writeFile(target, fx.render.unit, { mode: 0o600 });
  await symlink(target, fx.unitFile);
  await assert.rejects(superviseInstall(options), /not a singular real|installed unit|refuse|symlink/u);
});

test("install success proves exact loaded render, enabled, active/running, MainPID > 0 and exact fragment path", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const behavior = { state: "active", sub: "running", pid: "12345", enabled: true, fragmentPath: fx.unitFile };
  const options = installOptions(fx, behavior);
  options.confirmInstallSha256 = review.installConfirmSha256;
  const result = await superviseInstall(options);
  assert.equal(result.ok, true);
  assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit);
});

test("CLI rejects the old --user-systemd-dir production override", () => {
  assert.throws(() => parseArgs(["--review", "--config", "/tmp/x.json", "--user-systemd-dir", "/tmp/dir"]), /unexpected argument --user-systemd-dir/u);
  assert.throws(() => parseArgs(["--install", "--config", "/tmp/x.json", "--user-systemd-dir", "/tmp/dir", "--confirm-install-sha256", "0".repeat(64)]), /unexpected argument --user-systemd-dir/u);
  assert.throws(() => parseArgs(["--user-systemd-dir", "/tmp/dir"]), /unexpected argument --user-systemd-dir/u);
});

test("enabled classification is closed and never collapses a failed command into disabled or absent", () => {
  assert.equal(classifyIsEnabled({ code: 0, state: "enabled" }), "enabled");
  assert.equal(classifyIsEnabled({ code: 1, state: "disabled" }), "disabled");
  assert.equal(classifyIsEnabled({ code: 4, state: "not-found" }), "not-found");
  // Mismatched accepted-word pairs are rejected.
  assert.throws(() => classifyIsEnabled({ code: 0, state: "disabled" }), /unknown or unclosed/u);
  assert.throws(() => classifyIsEnabled({ code: 1, state: "enabled" }), /unknown or unclosed/u);
  assert.throws(() => classifyIsEnabled({ code: 4, state: "disabled" }), /unknown or unclosed/u);
  assert.throws(() => classifyIsEnabled({ code: 0, state: "not-found" }), /unknown or unclosed/u);
  assert.throws(() => classifyIsEnabled({ code: 1, state: "not-found" }), /unknown or unclosed/u);
  assert.throws(() => classifyIsEnabled({ code: 4, state: "enabled" }), /unknown or unclosed/u);
  // Arbitrary nonzero error/spawn/timeout codes never collapse into an accepted
  // word, even when stdout carries one.
  for (const code of [2, 42, 127, 5, 7, 143]) {
    assert.throws(() => classifyIsEnabled({ code, state: "disabled" }), /unknown or unclosed/u, `exit ${code}/disabled must be rejected`);
    assert.throws(() => classifyIsEnabled({ code, state: "not-found" }), /unknown or unclosed/u, `exit ${code}/not-found must be rejected`);
  }
  assert.throws(() => classifyIsEnabled({ code: 5, state: "" }), /unknown or unclosed/u);
  assert.throws(() => classifyIsEnabled({ code: 1, state: "masked" }), /unknown or unclosed/u);
});

test("systemctl show exit 0 with LoadState=not-found is a real absent unit; nonzero exits are errors", async (t) => {
  const fx = await fixture(t);
  const notFoundRun = async () => ({ code: 0, stdout: ["ActiveState=inactive", "SubState=dead", "MainPID=0", "InvocationID=", "ControlGroup=", "FragmentPath=", "LoadState=not-found", "DropInPaths="].join("\n") + "\n", stderr: "" });
  const loadedRun = async () => ({ code: 0, stdout: ["ActiveState=active", "SubState=running", "MainPID=123", "InvocationID=0".repeat(32), "ControlGroup=/x.slice", "FragmentPath=/tmp/x.service", "LoadState=loaded", "DropInPaths="].join("\n") + "\n", stderr: "" });
  const showNotFound = await systemdShowUnit("x.service", { systemctlPath: "/usr/bin/systemctl", expectedOwnerUid: uid }, notFoundRun);
  assert.equal(showNotFound.ok, true);
  assert.equal(showNotFound.facts.LoadState, "not-found");
  const showLoaded = await systemdShowUnit("x.service", { systemctlPath: "/usr/bin/systemctl", expectedOwnerUid: uid }, loadedRun);
  assert.equal(showLoaded.ok, true);
  assert.equal(showLoaded.facts.LoadState, "loaded");
  const errorRun = async () => ({ code: 5, stdout: "", stderr: "permission denied" });
  const showError = await systemdShowUnit("x.service", { systemctlPath: "/usr/bin/systemctl", expectedOwnerUid: uid }, errorRun);
  assert.equal(showError.ok, false, "a nonzero systemctl show must be an error, never an absent unit");
  const unknownRun = async () => ({ code: 0, stdout: "ActiveState=inactive\nSubState=dead\nMainPID=0\nInvocationID=\nControlGroup=\nFragmentPath=\nLoadState=masked\nDropInPaths=\n", stderr: "" });
  const showUnknown = await systemdShowUnit("x.service", { systemctlPath: "/usr/bin/systemctl", expectedOwnerUid: uid }, unknownRun);
  assert.equal(showUnknown.ok, false, "an unknown LoadState must be rejected");
});

test("a present unit file with a not-found unit is rejected as an inconsistent prior state", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, {});
  options.confirmInstallSha256 = review.installConfirmSha256;
  // Force the unit to report not-found while the file is present (inconsistent).
  options.runSystemctl = makeSystemctl({ forceNotFound: true }, fx.unitFile);
  const original = options.runSystemctl;
  options.runSystemctl = async function (args) {
    const result = await original(args);
    if (args[1] === "show" && result.stdout) {
      result.stdout = result.stdout.replace(/LoadState=loaded/u, "LoadState=not-found").replace(/ActiveState=active/u, "ActiveState=inactive").replace(/SubState=running/u, "SubState=dead").replace(/MainPID=12345/u, "MainPID=0");
    }
    return result;
  };
  await assert.rejects(superviseInstall(options), /does not reconcile|not in the exact loaded state/u);
});

test("an absent unit file with a loaded unit is rejected as an inconsistent prior state", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, {});
  options.confirmInstallSha256 = review.installConfirmSha256;
  // Force the unit to report loaded while the file is absent (inconsistent).
  const original = options.runSystemctl;
  options.runSystemctl = async function (args) {
    const result = await original(args);
    if (args[1] === "show" && result.stdout) {
      result.stdout = result.stdout.replace(/LoadState=not-found/u, "LoadState=loaded").replace(/FragmentPath=$/mu, `FragmentPath=${fx.unitFile}`);
    }
    if (args[1] === "is-enabled" && result.stdout) {
      result.stdout = "enabled\n"; result.code = 0;
    }
    return result;
  };
  await assert.rejects(superviseInstall(options), /does not reconcile|absent unit file/u);
});

test("a failed or transitional prior service is rejected before mutation", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  for (const bad of [
    { state: "failed", sub: "failed", pid: "0", enabled: true },
    { state: "activating", sub: "start", pid: "12345", enabled: true },
    { state: "active", sub: "running", pid: "0", enabled: true },
  ]) {
    const options = installOptions(fx, bad);
    options.confirmInstallSha256 = review.installConfirmSha256;
    await assert.rejects(superviseInstall(options), /transitional, failed, or ambiguous|not in the exact loaded state/u);
  }
});

test("rollback proves exact active and inactive prior systemd state", async (t) => {
  for (const active of [true, false]) {
    const fx = await fixture(t);
    await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
    await chmod(fx.unitFile, 0o600);
    const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
    const prior = active
      ? { state: "active", sub: "running", pid: "12345", enabled: true }
      : { state: "inactive", sub: "dead", pid: "0", enabled: false };
    const postStart = active
      ? { state: "active", sub: "running", pid: "67890" }
      : { state: "inactive", sub: "dead", pid: "0" };
    const options = installOptions(fx, { ...prior, postStart, failNth: { "daemon-reload": 1 } });
    options.confirmInstallSha256 = review.installConfirmSha256;
    await assert.rejects(superviseInstall(options), /exact prior unit and full prior state were restored/u, active ? "active prior" : "inactive prior");
    assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, "bytes must be restored exactly");
  }
});

test("active rollback proves a positive restored MainPID rather than exact integer equality", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, {
    state: "active", sub: "running", pid: "12345", enabled: true,
    postStart: { state: "active", sub: "running", pid: "99999" },
    failNth: { "daemon-reload": 1 },
  });
  options.confirmInstallSha256 = review.installConfirmSha256;
  await assert.rejects(superviseInstall(options), /exact prior unit and full prior state were restored/u);
  assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, "bytes must be restored exactly");
});

test("rollback verification failure reports incomplete rollback and does not claim recovery capability retained", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, {});
  options.confirmInstallSha256 = review.installConfirmSha256;
  // Fail the mutation-time daemon-reload once to force a rollback, then corrupt
  // only the post-rollback show so the restore proof cannot be completed.
  const original = options.runSystemctl;
  let reloadFailed = false;
  options.runSystemctl = async function (args) {
    if (args[1] === "daemon-reload") {
      if (!reloadFailed) { reloadFailed = true; return { code: 5, stdout: "", stderr: "reload failed" }; }
      return { code: 0, stdout: "", stderr: "" };
    }
    const result = await original(args);
    if (args[1] === "show" && reloadFailed) {
      result.stdout = result.stdout.replace(/LoadState=loaded/u, "LoadState=not-found").replace(/ActiveState=active/u, "ActiveState=inactive").replace(/SubState=running/u, "SubState=dead").replace(/MainPID=12345/u, "MainPID=0");
    }
    return result;
  };
  await assert.rejects(superviseInstall(options), /rollback is incomplete|manual attention required and recovery capability is not proven retained/u);
});

test("secureReadUnitFile fails closed on a path swapped between open and read identity", { skip: process.platform === "win32" ? "POSIX" : false }, async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, "ORIGINAL BYTES\n", { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const substitute = join(fx.root, "substitute");
  await writeFile(substitute, "SUBSTITUTED AFTER OPEN\n", { mode: 0o600 });
  await chmod(substitute, 0o600);
  let swapped = false;
  await assert.rejects(secureReadUnitFile(fx.unitFile, uid, {
    __testAfterOpen: async () => {
      if (swapped) return;
      swapped = true;
      // rename atomically replaces the path with a distinct inode (no reuse).
      await rename(substitute, fx.unitFile);
    },
  }), /no longer names the opened inode|changed during read|real path differs/u);
  assert.equal(swapped, true, "the after-open swap seam must run");
});

test("atomic write removes its exact temporary file on every write/sync/close/rename failure", { skip: process.platform === "win32" ? "POSIX" : false }, async (t) => {
  const fx = await fixture(t);
  const bytes = Buffer.from("NEW UNIT BYTES\n", "utf8");
  for (const stage of ["write", "sync", "close", "rename"]) {
    await writeFile(fx.unitFile, "PRIOR\n", { mode: 0o600 });
    await chmod(fx.unitFile, 0o600);
    const before = await readdir(fx.unitDir);
    await assert.rejects(writeUnitFileAtomically(fx.unitFile, bytes, uid, { failAt: stage }), new RegExp(`injected atomic ${stage} failure`, "u"));
    const after = await readdir(fx.unitDir);
    assert.deepEqual(after.sort(), before.sort(), `${stage} failure must leave no temporary residue`);
    assert.equal(await readFile(fx.unitFile, "utf8"), "PRIOR\n", `${stage} failure must not touch the prior unit`);
  }
});

test("atomic write removes only its own temporary and never an unrelated path on rename failure", { skip: process.platform === "win32" ? "POSIX" : false }, async (t) => {
  const fx = await fixture(t);
  // Make the destination path a non-empty directory so rename fails cleanly.
  await rm(fx.unitFile, { force: true });
  await mkdir(fx.unitFile);
  await writeFile(join(fx.unitFile, "keep"), "unrelated", { mode: 0o600 });
  const before = await readdir(fx.unitDir);
  await assert.rejects(writeUnitFileAtomically(fx.unitFile, Buffer.from("X\n"), uid, { failAt: "rename" }), /injected atomic rename failure/u);
  const after = await readdir(fx.unitDir);
  assert.deepEqual(after.sort(), before.sort(), "only the exact temporary file may be removed");
  assert.equal(await readFile(join(fx.unitFile, "keep"), "utf8"), "unrelated", "unrelated content under the destination must never be deleted");
});

test("directory substitution after review but before custody is rejected on install", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, {});
  options.confirmInstallSha256 = review.installConfirmSha256;
  const hostile = join(fx.root, "hostile");
  await mkdir(hostile, { recursive: true });
  await chmod(hostile, 0o700);
  await rm(fx.unitDir, { recursive: true, force: true });
  await symlink(hostile, fx.unitDir);
  await assert.rejects(superviseInstall(options), /not a real directory|substituted symlink|not owned|group\/world writable/u);
});

test("the fixed production unit name rejects path separators, traversal, instances, and arbitrary names", () => {
  assert.equal(validateUnitName("pixel-maintenance-recovery.service"), "pixel-maintenance-recovery.service");
  assert.throws(() => validateUnitName("pixel-maintenance-recovery@1.service"), /unsafe/u);
  assert.throws(() => validateUnitName("../escape.service"), /unsafe/u);
  assert.throws(() => validateUnitName("a/b.service"), /unsafe/u);
  assert.throws(() => validateUnitName("a\\b.service"), /unsafe/u);
  assert.throws(() => validateUnitName(""), /unsafe/u);
  assert.throws(() => validateUnitName("not-a-unit"), /unsafe/u);
  assert.throws(() => validateUnitName("x.service\0extra"), /unsafe/u);
});

test("guardian module path resolves to its exact real path before rendering", async (t) => {
  const fx = await fixture(t);
  const link = join(fx.root, "guardian-link.mjs");
  await symlink(GUARDIAN, link);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, guardianPath: link, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const realGuardian = await (await import("node:fs/promises")).realpath(GUARDIAN);
  assert.match(review.unitPath, /pixel-maintenance-recovery\.service$/u);
  const expected = await renderGuardianUnit({ configPath: fx.configPath, nodePath: NODE_PATH, guardianPath: realGuardian, expectedOwnerUid: uid });
  assert.equal(review.renderSha256, expected.renderSha256, "the rendered unit must bind the exact real guardian path, not a symlink leaf");
});

test("the fixed systemd environment carries no ambient SYSTEMD/XDG/DBUS/loader influence", () => {
  const env = buildSystemdEnv(uid);
  assert.equal(env.XDG_RUNTIME_DIR, `/run/user/${uid}`);
  assert.equal(env.DBUS_SESSION_BUS_ADDRESS, `unix:path=/run/user/${uid}/bus`);
  assert.ok(env.HOME.startsWith("/"), "home is passwd-derived");
  assert.ok(env.PATH.includes("/usr/bin"), "a fixed safe PATH is set");
  assert.ok(env.LANG && env.LC_ALL, "a fixed locale is set");
  for (const key of Object.keys(env)) {
    assert.ok(!key.startsWith("LD_"), `no loader influence may be present: ${key}`);
    assert.ok(!/^XDG_/.test(key) || key === "XDG_RUNTIME_DIR", `only the fixed XDG_RUNTIME_DIR may be set: ${key}`);
    assert.ok(!key.startsWith("SYSTEMD_") || ["SYSTEMD_COLORS", "SYSTEMD_PAGER", "SYSTEMD_LOG_LEVEL"].includes(key), `only fixed SYSTEMD_* keys may be set: ${key}`);
  }
  assert.equal(Object.prototype.hasOwnProperty.call(env, "PIXEL_SUPERVISE_SYSTEMCTL"), false);
});

test("qualification review fails closed when systemd reports a non-empty DropInPaths set", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const dropIns = ["/etc/systemd/user/pixel-maintenance-recovery.service.d/override.conf"];
  await assert.rejects(
    superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({ dropIns }, fx.unitFile) }),
    /drop-in that could override ExecStart|refusing/u,
  );
});

test("qualification install fails closed before mutation when systemd reports a non-empty DropInPaths set", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, { dropIns: ["/etc/systemd/user/pixel-maintenance-recovery.service.d/override.conf"] });
  options.confirmInstallSha256 = review.installConfirmSha256;
  // The absent unit's drop-in is only visible to systemd once the unit is
  // started, so install fails closed after mutation and must roll back exactly
  // to the absent state, leaving no unit file behind.
  await assert.rejects(superviseInstall(options), /drop-in that could override ExecStart|refusing|could not be shown after start/u);
  await assert.rejects(readFile(fx.unitFile, "utf8"), /ENOENT|no such file/u);
});

test("qualification remove fails closed when systemd reports a non-empty DropInPaths set", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, { enabled: true, state: "active", sub: "running", pid: "12345", dropIns: ["/etc/systemd/user/pixel-maintenance-recovery.service.d/override.conf"] });
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  await assert.rejects(superviseRemove(options), /drop-in that could override ExecStart|refusing/u);
  assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, "a drop-in rejection must leave the prior unit untouched");
});

// The supervisor's closed systemd-show parser must treat DropInPaths as an
// exact mandatory fact: only a single empty "DropInPaths=" line is the empty
// set. Missing, duplicate/ambiguous, malformed, one non-empty, and multiple
// non-empty representations all fail closed.
function showLines(extra) {
  const base = [
    "ActiveState=active",
    "SubState=running",
    "MainPID=123",
    "InvocationID=0".repeat(32),
    "ControlGroup=/user.slice/x.service",
    "FragmentPath=/tmp/x.service",
    "LoadState=loaded",
  ];
  return `${[...base, ...extra].join("\n")}\n`;
}

test("supervisor systemd-show DropInPaths is an exact mandatory fact that fails closed on every non-empty, malformed, missing, or ambiguous shape", async () => {
  const empty = async () => ({ code: 0, stdout: showLines(["DropInPaths="]), stderr: "" });
  const facts = await systemdShowUnit("x.service", {}, empty);
  assert.deepEqual(facts.facts.DropInPaths, []);
  // Missing property must fail closed (never defaulted to an empty set).
  const missing = async () => ({ code: 0, stdout: showLines([]), stderr: "" });
  assert.equal((await systemdShowUnit("x.service", {}, missing)).ok, false);
  // One non-empty path fails closed.
  const one = async () => ({ code: 0, stdout: showLines(["DropInPaths=/tmp/x.service.d/a.conf"]), stderr: "" });
  assert.equal((await systemdShowUnit("x.service", {}, one)).ok, false);
  // Multiple non-empty paths (space-separated single line) fail closed.
  const multiple = async () => ({ code: 0, stdout: showLines(["DropInPaths=/tmp/x.service.d/a.conf /tmp/x.service.d/b.conf"]), stderr: "" });
  assert.equal((await systemdShowUnit("x.service", {}, multiple)).ok, false);
  // Duplicate/ambiguous representation (two lines) fails closed.
  const duplicate = async () => ({ code: 0, stdout: showLines(["DropInPaths=", "DropInPaths="]), stderr: "" });
  assert.equal((await systemdShowUnit("x.service", {}, duplicate)).ok, false);
  // A malformed empty-plus-nonempty pairing fails closed.
  const ambiguous = async () => ({ code: 0, stdout: showLines(["DropInPaths=", "DropInPaths=/tmp/x.service.d/a.conf"]), stderr: "" });
  assert.equal((await systemdShowUnit("x.service", {}, ambiguous)).ok, false);
});

// A drop-in introduced during rollback must never let rollback report the exact
// prior state restored; it must fall through to the manual-attention path.
test("install rollback fails closed (manual attention) when the post-restoration fact response introduces a drop-in", async (t) => {
  const fx = await fixture(t);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = installOptions(fx, {});
  options.confirmInstallSha256 = review.installConfirmSha256;
  // Every systemd show after the initial absent capture reports a drop-in: the
  // post-start verification fails (triggering rollback) and the post-restoration
  // fact response also carries a drop-in, so restoreUnitState cannot prove the
  // exact empty drop-in set and must fall through to the manual-attention path.
  let showCalls = 0;
  const original = options.runSystemctl;
  options.runSystemctl = async function (args) {
    if (args[1] === "show") showCalls += 1;
    const result = await original(args);
    if (args[1] === "show" && showCalls >= 2) {
      return { code: 0, stdout: result.stdout.replace("DropInPaths=", "DropInPaths=/tmp/pixel-maintenance-recovery.service.d/override.conf"), stderr: "" };
    }
    return result;
  };
  await assert.rejects(superviseInstall(options), /rollback is incomplete|manual attention|not proven retained/u);
});

test("remove rollback fails closed (manual attention) when the post-restoration fact response introduces a drop-in", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  await chmod(fx.unitFile, 0o600);
  const review = await superviseReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, { state: "active", sub: "running", pid: "12345", enabled: true });
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  options.waitForLeaseDisappearance = async () => true;
  // Every systemd show after the initial present capture reports a drop-in: the
  // post-stop verification fails (triggering rollback) and the post-restoration
  // fact response also carries a drop-in, so restoreUnitState cannot prove the
  // exact empty drop-in set and must fall through to the manual-attention path.
  let showCalls = 0;
  const original = options.runSystemctl;
  options.runSystemctl = async function (args) {
    if (args[1] === "show") showCalls += 1;
    const result = await original(args);
    if (args[1] === "show" && showCalls >= 2) {
      return { code: 0, stdout: result.stdout.replace("DropInPaths=", "DropInPaths=/tmp/pixel-maintenance-recovery.service.d/override.conf"), stderr: "" };
    }
    return result;
  };
  await assert.rejects(superviseRemove(options), /rollback is incomplete|manual attention|not proven retained/u);
});
