import assert from "node:assert/strict";
import { execFile, execFileSync } from "node:child_process";
import { chmod, lstat, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { promisify } from "node:util";
import { createHash } from "node:crypto";
import test from "node:test";

import { modelQualificationMaintenanceBoundaries } from "../deploy/work-controller/model-qualification-maintenance.mjs";
import { renderGuardianUnit } from "../deploy/work-controller/maintenance-recovery-guardian-unit.mjs";
import { createRecoveryJournal } from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import { deriveGuardianLeasePath } from "../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";
import {
  superviseInstall,
  superviseRemove,
  superviseReview,
} from "../deploy/work-controller/maintenance-recovery-guardian-supervisor.mjs";

const execute = promisify(execFile);
const ROOT = resolve(new URL("..", import.meta.url).pathname);
const UNIT_TEMPLATE = join(ROOT, "deploy/work-controller/maintenance-recovery-guardian@.service");
const SUPERVISE = join(ROOT, "deploy/work-controller/maintenance-recovery-guardian-supervise.sh");
const GUARDIAN = join(ROOT, "deploy/work-controller/maintenance-recovery-guardian.mjs");

const linux = process.platform === "linux";
const digest = (character) => character.repeat(64);
const uid = process.geteuid?.() ?? 1000;

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function fixture(t, { unsafePath = null } = {}) {
  const root = await mkdtemp(join(tmpdir(), "pixel-guardian-systemd-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const journalDir = join(root, "journal");
  const stateRoot = unsafePath ?? join(root, "state");
  await Promise.all([mkdir(journalDir, { recursive: true }), mkdir(stateRoot, { recursive: true })]);
  const configPath = join(root, "maintenance.json");
  const dockerQualPath = join(root, "qualification-docker.json");
  const backendConfigPath = join(root, "backend.json");
  const environmentPath = join(root, "environment.json");
  const custodyLockPath = join(journalDir, "guardian.lock");
  await writeFile(custodyLockPath, "", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(custodyLockPath, 0o600);
  const production = {
    containerName: "pixel-production-model",
    containerId: digest("1"),
    imageDigest: `sha256:${digest("2")}`,
    expectedStartedAt: "2026-08-13T12:00:00Z",
    expectedRestartCount: 0,
    stopTimeoutSeconds: 120,
    restoreTimeoutSeconds: 7200,
    probeIntervalMilliseconds: 1000,
    readinessOrigin: "http://127.0.0.1:8000",
    readinessModelId: "DeepSeek-V4-Flash-0731",
    maxResponseBytes: 1048576,
  };
  const maintenance = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-qualification-maintenance-v1.schema.json",
    schemaVersion: 1,
    qualificationDockerConfigPath: dockerQualPath,
    custody: { kind: "advisory-flock", lockPath: custodyLockPath, acquireTimeoutSeconds: 30 },
    production,
    boundary: modelQualificationMaintenanceBoundaries.configuration,
  };
  const dockerQual = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-qualification-docker-v1.schema.json",
    schemaVersion: 1,
    backendConfigPath,
    qualificationConfigPath: join(root, "qualification-config.json"),
    runnerImageDigest: `sha256:${digest("3")}`,
    evidenceRoot: join(root, "evidence"),
    boundary: "Owner-private exact local-model qualification inputs only. Configuration grants no model start, container, network, device, credential, external-effect, completion, publication, deployment, acceptance, or promotion authority.",
  };
  const backend = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-backend-config-v1.schema.json",
    schemaVersion: 1,
    environmentPath,
    modelSource: { kind: "file", path: "/srv/pixel/models/model.gguf" },
    runtimeCacheSeed: null,
    backendNetwork: { subnet: "172.31.255.0/29" },
    containerUser: { uid: 10001, gid: 10001 },
    publishLoopbackPort: null,
    resources: { memoryMiB: 4096, cpuCores: 2, pids: 256, nofile: 1024, tmpfsMiB: 128, cacheMiB: 64, sharedMemoryMiB: 128 },
    readiness: { startupTimeoutSeconds: 10, probeIntervalMilliseconds: 5000 },
    accelerator: { class: "cpu", count: 0, deviceIds: [] },
    providerOptions: { provider: "llama.cpp", threads: 2, parallel: 1, gpuLayers: 0, flashAttention: false },
    boundary: "Owner-private contained model backend configuration only. It selects exact local bytes and bounded runtime resources but grants no hashing result, policy mutation, model qualification, execution, container or network creation, image pull, device access, credential, external-effect, or completion authority.",
  };
  const environment = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-controller-environment-v1.schema.json",
    schemaVersion: 1,
    stateRoot,
    policyPath: join(root, "policy.json"),
    objectStore: join(root, "objects"),
    workspaceRoot: join(root, "workspaces"),
    executorPath: "/opt/pixel-work/bin/omp",
    modelBackendLaunchPath: join(root, "backend-launch.json"),
    archiveLimits: { maxEntries: 10000, maxFileBytes: 1073741824 },
    runtime: { dockerPath: "/usr/bin/docker", backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model", networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11", uid: 10001, gid: 10001 },
    boundary: "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.",
  };
  await privateWrite(configPath, maintenance);
  await privateWrite(dockerQualPath, dockerQual);
  await privateWrite(backendConfigPath, backend);
  await privateWrite(environmentPath, environment);
  return { root, configPath, custodyLockPath, journalDir, stateRoot, dockerQualPath, backendConfigPath, environmentPath };
}

async function makeUserUnitDir(t) {
  const dir = await mkdtemp(join(tmpdir(), "pixel-guardian-user-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(dir, 0o700);
  const systemdDir = join(dir, "systemd", "user");
  await mkdir(systemdDir, { recursive: true });
  if (process.platform !== "win32") await chmod(systemdDir, 0o700);
  return { userDir: dir, unitDir: systemdDir, unitFile: join(systemdDir, "pixel-maintenance-recovery.service") };
}

function createRecoveryJournalRecord(fx) {
  return {
    schemaVersion: 1,
    kind: "pixel-maintenance-recovery-journal",
    operation: "pixel-work-model-qualification-maintenance",
    maintenanceOperationSha256: digest("a"),
    qualificationOperationSha256: digest("b"),
    custodyIdentitySha256: digest("c"),
    configurationSha256: digest("d"),
    ownerUid: uid,
    production: { containerName: "pixel-production-model", containerId: digest("1"), imageDigest: `sha256:${digest("2")}`, expectedStartedAt: "2026-08-13T12:00:00Z", expectedRestartCount: 0 },
    qualification: { backendContainerName: "pixel-qualification-backend", backendNetworkName: "pixel-qualification-network", qualificationContainerName: "pixel-model-qualification-333333333333" },
    guardianStartedAt: null,
    guardianStartNonce: null,
    guardianStartEndpointIdentity: null,
    guardianStartReceipt: null,
  };
}

// Filesystem-aware fake systemctl for dependency-injected lifecycle tests (no
// hidden production CLI flags or env overrides).
function makeSystemctl(behavior = {}, unitPath) {
  let stopped = false;
  let started = false;
  return async function fakeRun(args) {
    const cmd = args[1];
    if (cmd === "stop") stopped = true;
    if (cmd === "start") started = true;
    let present = false;
    if (unitPath) { try { await lstat(unitPath); present = true; } catch {} }
    if (behavior.fail?.[cmd]) return { code: 5, stdout: "", stderr: `fake ${cmd} failed` };
    const notFoundFacts = { LoadState: "not-found", ActiveState: "inactive", SubState: "dead", MainPID: "0", FragmentPath: "", InvocationID: "", ControlGroup: "", DropInPaths: "" };
    const loadedFacts = (state) => ({
      LoadState: "loaded",
      ActiveState: state.state ?? behavior.state ?? "active",
      SubState: state.sub ?? behavior.sub ?? "running",
      MainPID: state.pid ?? behavior.pid ?? "12345",
      FragmentPath: state.fragmentPath ?? unitPath ?? "",
      InvocationID: "0".repeat(32),
      ControlGroup: "/user.slice/user-1000.slice/user@1000.service/app.slice",
      DropInPaths: state.dropIns ?? behavior.dropIns ?? "",
    });
    if (cmd === "show") {
      const keys = (args.find((a) => a.startsWith("--property="))?.slice("--property=".length).split(",")) ?? Object.keys(notFoundFacts);
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

function installOptions(fx, unitFile, behavior) {
  return { configPath: fx.configPath, userSystemdDir: dirname(unitFile), confirmInstallSha256: null, withCustody: fakeCustody, runSystemctl: makeSystemctl(behavior, unitFile), validateSystemdRuntime: validateFakeSystemdRuntime };
}
function removeOptions(fx, unitFile, behavior) {
  return { configPath: fx.configPath, userSystemdDir: dirname(unitFile), confirmRemoveSha256: null, withCustody: fakeCustody, runSystemctl: makeSystemctl(behavior, unitFile), validateSystemdRuntime: validateFakeSystemdRuntime, leaseWaitSeconds: 1, leasePollMs: 20 };
}

function reviewOptions(fx, unitFile, behavior = {}) {
  return { configPath: fx.configPath, userSystemdDir: dirname(unitFile), runSystemctl: makeSystemctl(behavior, unitFile) };
}

test("shipped systemd user unit template is a persistent watcher with correct unescaped specifier", { skip: !linux ? "systemd user units are Linux-only" : false }, async () => {
  const unit = await readFile(UNIT_TEMPLATE, "utf8");
  assert.match(unit, /Type=exec/u);
  assert.match(unit, /Restart=always/u);
  assert.match(unit, /watch --config %I/u, "the template must use the unescaped %I specifier, not %i");
  assert.doesNotMatch(unit, /--config %i/u);
  assert.match(unit, /ProtectSystem=strict/u);
  assert.match(unit, /ProtectHome=read-only/u);
  assert.doesNotMatch(unit, /^[ \t]*TimeoutStartSec=/mu);
  assert.doesNotMatch(unit, /Type=oneshot/u);
});

test("shipped guardian unit template is syntactically valid", { skip: !linux ? "systemd units are Linux-only" : false }, async () => {
  const unit = await readFile(UNIT_TEMPLATE, "utf8");
  const lines = unit.split("\n");
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("[") || trimmed.startsWith(".")) continue;
    assert.match(trimmed, /^[A-Za-z][A-Za-z0-9]*=/, `unit directive must be well-formed: ${line}`);
  }
  assert.doesNotMatch(unit, /NotifyAccess=all watcher/u);
});

test("supervised render produces a concrete unit with exact read/write paths", { skip: !linux ? "supervised systemd launch is Linux-only" : false }, async (t) => {
  const fx = await fixture(t);
  const { stdout } = await execute(SUPERVISE, ["--render", "--config", fx.configPath], { encoding: "utf8" });
  assert.match(stdout, /Type=exec/u);
  assert.match(stdout, /Restart=always/u);
  assert.match(stdout, new RegExp(`watch --config ${fx.configPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "u"));
  assert.doesNotMatch(stdout, /TimeoutStartSec/u);
  assert.match(stdout, /ProtectSystem=strict/u);
  assert.match(stdout, /ProtectHome=read-only/u);
  assert.ok(stdout.includes(`ReadWritePaths=${fx.journalDir} ${fx.stateRoot}`) || stdout.includes(`ReadWritePaths=${fx.stateRoot} ${fx.journalDir}`));
});

test("the CLI rejects the old --user-systemd-dir override", { skip: !linux ? "supervised systemd launch is Linux-only" : false }, async (t) => {
  const fx = await fixture(t);
  await assert.rejects(execute(SUPERVISE, ["--review", "--config", fx.configPath, "--user-systemd-dir", "/tmp/pixel-dir"], { encoding: "utf8" }), /unexpected argument --user-systemd-dir/u);
});

test("the CLI ignores hostile ambient systemd-routing and loader environment", { skip: !linux ? "supervised systemd launch is Linux-only" : false }, async (t) => {
  const fx = await fixture(t);
  const clean = (await execute(SUPERVISE, ["--render", "--config", fx.configPath], { encoding: "utf8" })).stdout;
  const hostile = {
    ...process.env,
    PIXEL_SUPERVISE_SYSTEMCTL: "/tmp/evil-systemctl",
    PIXEL_SUPERVISE_LEASE_WAIT_SECONDS: "9999",
    PIXEL_GUARDIAN_LEASE_WAIT_SECONDS: "9999",
    DBUS_SESSION_BUS_ADDRESS: "unix:path=/tmp/evil/bus",
    XDG_RUNTIME_DIR: "/tmp/evil",
    SYSTEMD_UNIT_PATH: "/tmp/evil/units",
    // PATH keeps bash runnable but is polluted first; HOME is hostile. The CLI
    // must never consult ambient PATH/HOME for node/systemctl/unit authority.
    PATH: `/tmp/evil:${process.env.PATH}`,
    HOME: "/tmp/evil",
  };
  const polluted = (await execute(SUPERVISE, ["--render", "--config", fx.configPath], { encoding: "utf8", env: hostile })).stdout;
  assert.equal(polluted, clean, "hostile ambient routing/loader variables must not influence the CLI");
});

test("inert review emits exact confirmation values via exported functions (no CLI override)", async (t) => {
  const fx = await fixture(t);
  const { unitDir, unitFile } = await makeUserUnitDir(t);
  const review = await superviseReview(reviewOptions(fx, unitFile));
  assert.equal(review.operation, "pixel-maintenance-recovery-guardian-supervise-review");
  assert.equal(review.unitPath, unitFile);
  assert.match(review.renderSha256, /^[a-f0-9]{64}$/u);
  assert.equal(review.existingBytesSha256, null);
  const configShaReview = createHash("sha256").update(await readFile(fx.configPath)).digest("hex");
  assert.equal(review.removeConfirmSha256, createHash("sha256").update(JSON.stringify({ operation: "remove", unitPath: unitFile, configSha256: configShaReview, installedBytesSha256: "absent" })).digest("hex"));
  assert.equal(review.confirm.remove.option, "--confirm-remove-sha256");
  assert.equal(review.confirm.install.option, "--confirm-install-sha256");
  const installed = "SOME INSTALLED UNIT BYTES\n";
  await writeFile(unitFile, installed, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(unitFile, 0o600);
  const review2 = await superviseReview(reviewOptions(fx, unitFile));
  const configSha = createHash("sha256").update(await readFile(fx.configPath)).digest("hex");
  const installedSha = createHash("sha256").update(installed).digest("hex");
  assert.equal(review2.existingBytesSha256, installedSha);
  assert.equal(review2.removeConfirmSha256, createHash("sha256").update(JSON.stringify({ operation: "remove", unitPath: unitFile, configSha256: configSha, installedBytesSha256: installedSha })).digest("hex"));
});

test("install requires an exact render confirmation and refuses without it (DI)", async (t) => {
  const fx = await fixture(t);
  const { unitDir, unitFile } = await makeUserUnitDir(t);
  await assert.rejects(superviseInstall({ ...installOptions(fx, unitFile, {}) }), /install requires an exact confirmation|install SHA mismatch/u);
  const wrong = createHash("sha256").update("not the rendered unit").digest("hex");
  await assert.rejects(superviseInstall({ ...installOptions(fx, unitFile, {}), confirmInstallSha256: wrong }), /install SHA mismatch/u);
});

test("install refuses to replace unrelated existing content even with an exact confirm (DI)", async (t) => {
  const fx = await fixture(t);
  const { unitDir, unitFile } = await makeUserUnitDir(t);
  const rendered = (await execute(SUPERVISE, ["--render", "--config", fx.configPath], { encoding: "utf8" })).stdout;
  const renderSha = createHash("sha256").update(rendered).digest("hex");
  const configSha = createHash("sha256").update(await readFile(fx.configPath)).digest("hex");
  const existing = "UNRELATED EXISTING CONTENT\n";
  await writeFile(unitFile, existing, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(unitFile, 0o600);
  const existingSha = createHash("sha256").update(existing).digest("hex");
  const confirm = createHash("sha256").update(JSON.stringify({ operation: "install", unitPath: unitFile, configSha256: configSha, existingBytesSha256: existingSha, renderSha256: renderSha })).digest("hex");
  await assert.rejects(superviseInstall({ ...installOptions(fx, unitFile, {}), confirmInstallSha256: confirm }), /not a valid Pixel-owned prior unit|refuse to replace/u);
  assert.equal(await readFile(unitFile, "utf8"), existing, "unrelated owner content must never be overwritten");
});

test("a valid Pixel prior unit may be upgraded only with an exact confirmation (DI)", async (t) => {
  const fx = await fixture(t);
  const { unitDir, unitFile } = await makeUserUnitDir(t);
  const rendered = (await execute(SUPERVISE, ["--render", "--config", fx.configPath], { encoding: "utf8" })).stdout;
  const renderSha = createHash("sha256").update(rendered).digest("hex");
  const configSha = createHash("sha256").update(await readFile(fx.configPath)).digest("hex");
  const prior = rendered.replace("RestartSec=30", "RestartSec=45");
  await writeFile(unitFile, prior, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(unitFile, 0o600);
  const existingSha = createHash("sha256").update(prior).digest("hex");
  const wrong = createHash("sha256").update("not the exact install").digest("hex");
  const behavior = { state: "active", sub: "running", pid: "12345", enabled: true };
  await assert.rejects(superviseInstall({ ...installOptions(fx, unitFile, behavior), confirmInstallSha256: wrong }), /install SHA mismatch/u);
  assert.equal(await readFile(unitFile, "utf8"), prior);
  const confirm = createHash("sha256").update(JSON.stringify({ operation: "install", unitPath: unitFile, configSha256: configSha, existingBytesSha256: existingSha, renderSha256: renderSha })).digest("hex");
  await superviseInstall({ ...installOptions(fx, unitFile, behavior), confirmInstallSha256: confirm });
  assert.equal(createHash("sha256").update(await readFile(unitFile, "utf8")).digest("hex"), renderSha, "install must install the exact new render bytes");
});

test("review -> install -> review -> remove lifecycle through the injected fake systemctl (DI)", async (t) => {
  const fx = await fixture(t);
  const { unitDir, unitFile } = await makeUserUnitDir(t);
  const review1 = await superviseReview(reviewOptions(fx, unitFile));
  assert.equal(review1.existingBytesSha256, null);
  const installBehavior = { state: "active", sub: "running", pid: "12345", enabled: true };
  await superviseInstall({ ...installOptions(fx, unitFile, installBehavior), confirmInstallSha256: review1.installConfirmSha256 });
  const installedSha = createHash("sha256").update(await readFile(unitFile, "utf8")).digest("hex");
  assert.equal(installedSha, review1.renderSha256);
  const review2 = await superviseReview(reviewOptions(fx, unitFile));
  assert.equal(review2.existingBytesSha256, installedSha);
  assert.equal(review2.removeConfirmSha256, createHash("sha256").update(JSON.stringify({ operation: "remove", unitPath: unitFile, configSha256: review2.configSha256, installedBytesSha256: installedSha })).digest("hex"));
  const removeBehavior = { state: "active", sub: "running", pid: "12345", enabled: true };
  await superviseRemove({ ...removeOptions(fx, unitFile, removeBehavior), confirmRemoveSha256: review2.removeConfirmSha256 });
  await assert.rejects(readFile(unitFile, "utf8"), /ENOENT|no such file/u);
});

test("remove refuses while an active maintenance recovery journal exists and leaves the unit running (DI)", async (t) => {
  const fx = await fixture(t);
  const { unitDir, unitFile } = await makeUserUnitDir(t);
  const installed = (await execute(SUPERVISE, ["--render", "--config", fx.configPath], { encoding: "utf8" })).stdout;
  await writeFile(unitFile, installed, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(unitFile, 0o600);
  const installedSha = createHash("sha256").update(installed).digest("hex");
  const configSha = createHash("sha256").update(await readFile(fx.configPath)).digest("hex");
  const confirm = createHash("sha256").update(JSON.stringify({ operation: "remove", unitPath: unitFile, configSha256: configSha, installedBytesSha256: installedSha })).digest("hex");
  await createRecoveryJournal(fx.custodyLockPath, uid, createRecoveryJournalRecord(fx));
  const behavior = { state: "active", sub: "running", pid: "12345", enabled: true };
  await assert.rejects(superviseRemove({ ...removeOptions(fx, unitFile, behavior), confirmRemoveSha256: confirm }), /active maintenance recovery journal|active recovery journal|recovery journal/u);
  assert.equal(await readFile(unitFile, "utf8"), installed, "removal must be refused while an active recovery journal exists");
});

test("remove retains the unit when the guardian lease does not disappear after stop (DI)", async (t) => {
  const fx = await fixture(t);
  const { unitDir, unitFile } = await makeUserUnitDir(t);
  const installed = (await execute(SUPERVISE, ["--render", "--config", fx.configPath], { encoding: "utf8" })).stdout;
  await writeFile(unitFile, installed, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(unitFile, 0o600);
  const installedSha = createHash("sha256").update(installed).digest("hex");
  const configSha = createHash("sha256").update(await readFile(fx.configPath)).digest("hex");
  const confirm = createHash("sha256").update(JSON.stringify({ operation: "remove", unitPath: unitFile, configSha256: configSha, installedBytesSha256: installedSha })).digest("hex");
  await writeFile(deriveGuardianLeasePath(fx.custodyLockPath), "{}", { mode: 0o600 });
  if (process.platform !== "win32") await chmod(deriveGuardianLeasePath(fx.custodyLockPath), 0o600);
  const options = removeOptions(fx, unitFile, { state: "active", sub: "running", pid: "12345", enabled: true });
  options.confirmRemoveSha256 = confirm;
  options.waitForLeaseDisappearance = async () => false;
  await assert.rejects(superviseRemove(options), /guardian lease did not disappear|manual attention/u);
  assert.equal(await readFile(unitFile, "utf8"), installed, "removal must retain the unit when the guardian lease cannot be removed");
});

test("remove of an already-absent unit is idempotent (DI)", async (t) => {
  const fx = await fixture(t);
  const { unitDir, unitFile } = await makeUserUnitDir(t);
  const review = await superviseReview(reviewOptions(fx, unitFile));
  assert.ok(review.removeConfirmSha256, "absent removal must still require an exact confirmation");
  await superviseRemove({ ...removeOptions(fx, unitFile, {}), confirmRemoveSha256: review.removeConfirmSha256 });
  await assert.rejects(readFile(unitFile, "utf8"), /ENOENT|no such file/u);
});

function systemdAnalyzeAvailable() {
  try {
    execFileSync("systemd-analyze", ["--version"], { stdio: "ignore", timeout: 10000 });
    return true;
  } catch { return false; }
}
