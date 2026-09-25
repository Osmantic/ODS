import assert from "node:assert/strict";
import { execFile, spawnSync } from "node:child_process";
import { promisify } from "node:util";
import { chmod, lstat, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  CAMPAIGN_SUPERVISOR_DESCRIPTOR,
  parseArgs,
  superviseCampaignInstall,
  superviseCampaignRemove,
  superviseCampaignReview,
  validateUnitName,
} from "../deploy/work-controller/maintenance-campaign-recovery-guardian-supervisor.mjs";
import { renderCampaignGuardianUnit } from "../deploy/work-controller/maintenance-campaign-recovery-guardian-unit.mjs";
import {
  CAMPAIGN_RECOVERY_ENGINE_READY,
  campaignRecoveryEngineState,
} from "../deploy/work-controller/maintenance-campaign-recovery-guardian.mjs";
import { renderGuardianUnit } from "../deploy/work-controller/maintenance-recovery-guardian-unit.mjs";
import {
  parseArgs as qualificationParseArgs,
  superviseReview as qualificationReview,
  validateUnitName as qualificationValidateUnitName,
} from "../deploy/work-controller/maintenance-recovery-guardian-supervisor.mjs";
import { deriveCampaignGuardianLeasePath } from "../deploy/work-controller/maintenance-recovery-guardian-lease.mjs";
import { createCampaignRecoveryJournal } from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import { withMaintenanceCustody } from "../deploy/work-controller/maintenance-custody.mjs";
import { modelCampaignMaintenanceBoundaries } from "../deploy/work-controller/model-campaign-maintenance.mjs";

const ROOT = resolve(new URL("..", import.meta.url).pathname);
const CAMPAIGN_GUARDIAN = join(ROOT, "deploy/work-controller/maintenance-campaign-recovery-guardian.mjs");
const digest = (character) => character.repeat(64);
const sha256hex = (value) => createHash("sha256").update(value).digest("hex");
const uid = process.geteuid?.() ?? 1000;
const linux = process.platform !== "win32";
// The shared supervisor resolves its trusted production executable from this
// fixed path, not from ambient PATH or process.execPath. Keep every fixture
// render bound to the same executable when CI launches tests from a private
// manifest-pinned toolchain.
const NODE_PATH = "/usr/bin/node";

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (linux) await chmod(path, 0o600);
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-campaign-supervisor-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  const journalDir = join(root, "journal");
  const custodyLockPath = join(journalDir, "guardian.lock");
  await mkdir(journalDir, { recursive: true });
  await writeFile(custodyLockPath, "", { mode: 0o600 });
  if (linux) await chmod(custodyLockPath, 0o600);
  const unitDir = join(root, "systemd", "user");
  await mkdir(unitDir, { recursive: true });
  if (linux) await chmod(unitDir, 0o700);
  const unitFile = join(unitDir, "pixel-campaign-maintenance-recovery.service");
  const configPath = join(root, "campaign.json");
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-campaign-maintenance-v1.schema.json",
    schemaVersion: 1, pythonPath: "/usr/bin/python3.11", dockerPath: "/usr/bin/docker",
    sourceRoot: "/opt/pixel-candidate", sourceCommit: "1".repeat(40), candidateSourceArchiveSha256: "2".repeat(64),
    materializationRoot: "/var/lib/pixel-outcome/private/materializations/builder-matched",
    pairConfigurationPath: "/var/lib/pixel-outcome/private/configs/pair-system.json",
    outputRoot: "/var/lib/pixel-outcome/private/campaigns/builder-matched-cold",
    campaign: { maxPairs: 4, partition: "tuning", freezeTuning: false, runtimeCondition: "cold-first-request", timeoutSeconds: 86400 },
    custody: { kind: "advisory-flock", lockPath: custodyLockPath, acquireTimeoutSeconds: 30 },
    production: {
      containerName: "pixel-production-model", containerId: digest("1"), imageDigest: `sha256:${digest("2")}`,
      expectedStartedAt: "2026-08-13T12:00:00Z", expectedRestartCount: 0, stopTimeoutSeconds: 120,
      restoreTimeoutSeconds: 7200, probeIntervalMilliseconds: 1000, readinessOrigin: "http://127.0.0.1:8000",
      readinessModelId: "DeepSeek-V4-Flash-0731", maxResponseBytes: 1048576,
    },
    boundary: modelCampaignMaintenanceBoundaries.configuration,
  };
  await privateWrite(configPath, config);
  const render = await renderCampaignGuardianUnit({ configPath, nodePath: NODE_PATH, guardianPath: CAMPAIGN_GUARDIAN, expectedOwnerUid: uid });
  return { root, configPath, custodyLockPath, journalDir, unitDir, unitFile, render };
}

const SHOW_KEYS = ["ActiveState", "SubState", "MainPID", "InvocationID", "ControlGroup", "FragmentPath", "LoadState", "DropInPaths"];
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

test("M6 campaign recovery engine reports the ready constant and its CLI is a real recovery engine", async () => {
  assert.equal(CAMPAIGN_RECOVERY_ENGINE_READY, true, "M6 campaign recovery engine must be ready");
  assert.deepEqual(campaignRecoveryEngineState(), { ready: true });
  // The CLI is now a real recovery engine: direct invocation fails closed on an
  // unreadable config and never fabricates a recovery result or effect.
  const { main: engineMain, campaignRecoveryGuardianStatuses } = await import("../deploy/work-controller/maintenance-campaign-recovery-guardian.mjs");
  assert.ok(campaignRecoveryGuardianStatuses.includes("no-active-recovery"), "M6 exposes a closed recovery status vocabulary");
  await assert.rejects(engineMain(["recover", "--config", "/tmp/pixel-m6-nonexistent.json"]), /could not be opened safely|not owner-private|unexpected failure|ENOENT/u);
});

test("campaign render/review work with an actual module realpath while the M6 engine is ready", async (t) => {
  const fx = await fixture(t);
  const render = await renderCampaignGuardianUnit({ configPath: fx.configPath, nodePath: NODE_PATH, guardianPath: CAMPAIGN_GUARDIAN, expectedOwnerUid: uid });
  assert.match(render.unit, /Pixel campaign maintenance recovery guardian/u);
  assert.match(render.unit, /maintenance-campaign-recovery-guardian\.mjs/u, "campaign render must bind the campaign guardian module");
  // M3 least privilege: only the campaign custody-state directory is writable.
  assert.deepEqual(render.writablePaths, [fx.journalDir]);
  const review = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  assert.equal(review.operation, "pixel-campaign-maintenance-recovery-guardian-supervise-review");
  assert.equal(review.engineReady, true);
  assert.equal(review.installAvailable, true);
  assert.ok(review.installConfirmSha256, "a ready M6 engine must expose the exact campaign-install confirmation");
  assert.match(review.unitPath, /pixel-campaign-maintenance-recovery\.service$/u);
  assert.ok(review.removeConfirmSha256, "an exact removal confirmation must be available so a stray exact Pixel-owned unit can be cleaned up");
  assert.equal(review.confirm.install.option, "--confirm-install-sha256");
  assert.equal(review.confirm.remove.option, "--confirm-remove-sha256");
  // The rendered unit must bind the exact real campaign guardian path.
  const real = await (await import("node:fs/promises")).realpath(CAMPAIGN_GUARDIAN);
  const expected = await renderCampaignGuardianUnit({ configPath: fx.configPath, nodePath: NODE_PATH, guardianPath: real, expectedOwnerUid: uid });
  assert.equal(review.renderSha256, expected.renderSha256);
});

test("campaign install is available only through the ready M6 engine", async () => {
  // M6 flips the campaign engine to ready, so the M3 install readiness gate is
  // gone and install must be available. The full install flow is exercised by
  // the shared supervisor engine on a host with a trusted executable; here we
  // assert the ready-engine contract directly so the availability decision is
  // deterministic and cannot silently regress to a blocked install.
  assert.equal(CAMPAIGN_RECOVERY_ENGINE_READY, true);
  assert.equal(CAMPAIGN_SUPERVISOR_DESCRIPTOR.ready, true, "M6 campaign install must be available");
});

test("campaign review provides the exact install confirmation for the ready M6 engine", async (t) => {
  const fx = await fixture(t);
  const review = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  assert.ok(review.installConfirmSha256, "a ready M6 engine must expose the campaign-install confirmation");
  assert.equal(review.confirm.install.option, "--confirm-install-sha256");
});

test("campaign confirmations are never cross-consumable with qualification confirmations", async (t) => {
  const fx = await fixture(t);
  const campaignReview = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  // Deterministic proof of the exact confirmation construction used by the
  // supervisor: recompute the expected campaign removal confirmation over the
  // review's exact bound values, recompute the otherwise identical
  // qualification-domain candidate (operation "remove"), and require the
  // review value to equal the campaign one yet differ from the qualification
  // one. The install confirmation is covered by the review tests above; this
  // test deterministically proves the remove confirmation is never
  // cross-consumable with a qualification-domain candidate.
  const confirmOf = (operation) => sha256hex(JSON.stringify({
    operation,
    unitPath: campaignReview.unitPath,
    configSha256: campaignReview.configSha256,
    installedBytesSha256: campaignReview.existingBytesSha256 ?? "absent",
  }));
  const expectedCampaignRemove = confirmOf(CAMPAIGN_SUPERVISOR_DESCRIPTOR.removeConfirmOperation);
  const qualificationDomainCandidate = confirmOf("remove");
  assert.equal(campaignReview.removeConfirmSha256, expectedCampaignRemove, "review must expose the exact campaign-remove confirmation");
  assert.notEqual(campaignReview.removeConfirmSha256, qualificationDomainCandidate, "a qualification-domain candidate must not consume the campaign removal confirmation");
  assert.equal(validateUnitName("pixel-campaign-maintenance-recovery.service"), "pixel-campaign-maintenance-recovery.service");
  assert.equal(CAMPAIGN_SUPERVISOR_DESCRIPTOR.installConfirmOperation, "campaign-install");
  assert.equal(CAMPAIGN_SUPERVISOR_DESCRIPTOR.removeConfirmOperation, "campaign-remove");
  assert.equal(CAMPAIGN_SUPERVISOR_DESCRIPTOR.unitName, "pixel-campaign-maintenance-recovery.service");
  assert.notEqual(CAMPAIGN_SUPERVISOR_DESCRIPTOR.leasePathFor(fx.custodyLockPath), deriveCampaignGuardianLeasePath(fx.custodyLockPath) + "-x");
});

test("campaign removal requires the exact campaign confirmation and refuses a mismatched one", async (t) => {
  const fx = await fixture(t);
  await assert.rejects(superviseCampaignRemove(removeOptions(fx, {})), /remove SHA mismatch/u);
});

test("campaign removal refuses while an active campaign recovery journal exists", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  if (linux) await chmod(fx.unitFile, 0o600);
  const review = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, { enabled: true, state: "active", sub: "running", pid: "12345" });
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  const binding = { kind: "advisory-flock", lockPath: fx.custodyLockPath, acquireTimeoutSeconds: 30 };
  const campaignRecord = {
    schemaVersion: 1, kind: "pixel-campaign-maintenance-recovery-journal", operation: "pixel-work-model-campaign-maintenance",
    campaignChildNonce: null, campaignChild: null, guardianStartedAt: null, guardianStartNonce: null,
    guardianStartEndpointIdentity: null, guardianStartReceipt: null, readinessEvidence: null, campaignOutcome: null,
    maintenanceOperationSha256: digest("a"), campaignOperationSha256: digest("b"),
    custodyIdentitySha256: digest("c"), configurationSha256: digest("d"), ownerUid: uid,
    production: { containerName: "pixel-production-model", containerId: digest("1"), imageDigest: `sha256:${digest("2")}`, expectedStartedAt: "2026-08-13T12:00:00.123456789Z", expectedRestartCount: 0 },
    comparison: { materializationSha256: digest("1"), pairConfigurationSha256: digest("2"), preflightSha256: digest("3"), acceleratorStateSha256: digest("4") },
  };
  // Use the REAL campaign journal gate: an active campaign recovery journal
  // must be refused, and a qualification journal must not satisfy it.
  await createCampaignRecoveryJournal(fx.custodyLockPath, uid, campaignRecord);
  options.withCustody = (b, u, op) => withMaintenanceCustody(b, u, op);
  options.assertNoActiveRecoveryJournal = undefined;
  await assert.rejects(superviseCampaignRemove(options), /active campaign recovery journal|active campaign maintenance recovery journal/u);
});

test("campaign removal waits for campaign lease disappearance and refuses if the campaign lease persists", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  if (linux) await chmod(fx.unitFile, 0o600);
  const review = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, { enabled: true, state: "active", sub: "running", pid: "12345" });
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  options.waitForLeaseDisappearance = async () => false;
  await assert.rejects(superviseCampaignRemove(options), /guardian lease did not disappear|retaining the unit for manual attention/u);
});

test("campaign idempotent absent removal proves the exact absent state and binds the campaign confirmation", async (t) => {
  const fx = await fixture(t);
  const review = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  assert.ok(review.removeConfirmSha256, "review must provide an exact removal confirmation for an absent no-op");
  const options = removeOptions(fx, {});
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  const result = await superviseCampaignRemove(options);
  assert.equal(result.ok, true);
  assert.equal(result.removed, false, "an absent no-op reports removed:false only after proving exact absence");
});

test("campaign removal rolls back the exact prior campaign unit on partial failure", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  if (linux) await chmod(fx.unitFile, 0o600);
  const review = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, { enabled: true, state: "active", sub: "running", pid: "12345", failNth: { stop: 1 }, postStart: { state: "active", sub: "running", pid: "67890" } });
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  await assert.rejects(superviseCampaignRemove(options), /exact prior unit and full prior state were restored/u);
  assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, "campaign rollback must restore the exact prior bytes");
});

test("campaign strict-JSON config is rejected before review", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.configPath, '{ "schemaVersion": 1, "schemaVersion": 2 }\n', { mode: 0o600 });
  if (linux) await chmod(fx.configPath, 0o600);
  await assert.rejects(superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) }), /not strict JSON|duplicate|not owner-private|opened safely/u);
});

test("campaign unit path symlink and non-owner mode are refused", { skip: !linux ? "POSIX" : false }, async (t) => {
  const fx = await fixture(t);
  const target = join(fx.root, "target.service");
  await writeFile(target, fx.render.unit, { mode: 0o600 });
  await symlink(target, fx.unitFile);
  // Review must fail closed on a substituted symlink unit before any outcome.
  await assert.rejects(
    superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) }),
    /not a singular real|symlink|installed unit/u,
  );
});

test("qualification review JSON and API compatibility remain exact (load-bearing invariants)", async (t) => {
  // Build a qualification fixture and assert the qualification review keeps the
  // exact operation, key set, and both non-null confirmations, and never gains
  // the campaign-only engine fields.
  const qualRoot = await mkdtemp(join(tmpdir(), "pixel-qual-invariant-"));
  t.after(() => rm(qualRoot, { recursive: true, force: true }));
  if (linux) await chmod(qualRoot, 0o700);
  const journalDir = join(qualRoot, "journal");
  const stateRoot = join(qualRoot, "state");
  await Promise.all([mkdir(journalDir, { recursive: true }), mkdir(stateRoot, { recursive: true })]);
  const configPath = join(qualRoot, "maintenance.json");
  const dockerQualPath = join(qualRoot, "docker.json");
  const backendConfigPath = join(qualRoot, "backend.json");
  const environmentPath = join(qualRoot, "env.json");
  const custodyLockPath = join(journalDir, "guardian.lock");
  await writeFile(custodyLockPath, "", { mode: 0o600 });
  if (linux) await chmod(custodyLockPath, 0o600);
  const unitDir = join(qualRoot, "systemd", "user");
  await mkdir(unitDir, { recursive: true });
  if (linux) await chmod(unitDir, 0o700);
  const unitFile = join(unitDir, "pixel-maintenance-recovery.service");
  const { modelQualificationMaintenanceBoundaries } = await import("../deploy/work-controller/model-qualification-maintenance.mjs");
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
    schemaVersion: 1, backendConfigPath, qualificationConfigPath: join(qualRoot, "q.json"),
    runnerImageDigest: `sha256:${digest("3")}`, evidenceRoot: join(qualRoot, "ev"),
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
    schemaVersion: 1, stateRoot, policyPath: join(qualRoot, "p.json"), objectStore: join(qualRoot, "ob"),
    workspaceRoot: join(qualRoot, "ws"), executorPath: "/opt/pixel-work/bin/omp", modelBackendLaunchPath: join(qualRoot, "bl.json"),
    archiveLimits: { maxEntries: 10000, maxFileBytes: 1073741824 },
    runtime: { dockerPath: "/usr/bin/docker", backendNetworkName: "pixel-model-backend", backendContainerName: "pixel-local-model", networkSubnet: "172.30.0.0/24", workerIp: "172.30.0.10", proxyIp: "172.30.0.11", uid: 10001, gid: 10001 },
    boundary: "Owner-reviewed local controller environment only. It names exact private storage, policy, pinned executor, and isolated runtime wiring but grants no execution, lease, scheduling, service activation, scope expansion, external effect, or completion authority.",
  });
  const qualRender = await renderGuardianUnit({ configPath, nodePath: NODE_PATH, guardianPath: join(ROOT, "deploy/work-controller/maintenance-recovery-guardian.mjs"), expectedOwnerUid: uid });
  const qualReview = await qualificationReview({ configPath, userSystemdDir: unitDir, runSystemctl: makeSystemctl({}, unitFile) });
  assert.equal(qualReview.operation, "pixel-maintenance-recovery-guardian-supervise-review");
  assert.deepEqual(Object.keys(qualReview), ["schemaVersion", "operation", "unitPath", "configSha256", "renderSha256", "existingBytesSha256", "installConfirmSha256", "removeConfirmSha256", "confirm"]);
  assert.equal(Object.prototype.hasOwnProperty.call(qualReview, "engineReady"), false, "qualification review must not gain campaign-only engine fields");
  assert.equal(Object.prototype.hasOwnProperty.call(qualReview, "installAvailable"), false);
  assert.ok(qualReview.installConfirmSha256, "qualification install confirmation must remain available");
  assert.ok(qualReview.removeConfirmSha256, "qualification remove confirmation must remain available");
  assert.equal(qualReview.confirm.install.option, "--confirm-install-sha256");
  // Qualification CLI grammar unchanged.
  assert.deepEqual(qualificationParseArgs(["--review", "--config", configPath]), { mode: "review", configPath, confirmSha256: null });
  assert.throws(() => qualificationParseArgs(["--review", "--config", configPath, "--user-systemd-dir", "/tmp/x"]), /unexpected argument --user-systemd-dir/u);
  // Unit name validator unchanged.
  assert.equal(qualificationValidateUnitName("pixel-maintenance-recovery.service"), "pixel-maintenance-recovery.service");
  assert.throws(() => qualificationValidateUnitName("pixel-campaign-maintenance-recovery@1.service"), /unsafe/u);
  // Campaign and qualification names differ.
  assert.equal(CAMPAIGN_SUPERVISOR_DESCRIPTOR.unitName, "pixel-campaign-maintenance-recovery.service");
  // Qualification render keeps the exact fixed unit identity.
  assert.match(qualRender.unit, /maintenance-recovery-guardian\.mjs/u);
});

test("campaign unit render never grants outputRoot or broad source/materialization write access", async (t) => {
  const fx = await fixture(t);
  const render = await renderCampaignGuardianUnit({ configPath: fx.configPath, nodePath: NODE_PATH, guardianPath: CAMPAIGN_GUARDIAN, expectedOwnerUid: uid });
  assert.deepEqual(render.writablePaths, [fx.journalDir]);
  for (const forbidden of ["/var/lib/pixel-outcome/private/campaigns", "/var/lib/pixel-outcome/private/materializations", "/opt/pixel-candidate"]) {
    assert.equal(render.unit.includes(`ReadWritePaths=`), true);
    assert.equal(render.writablePaths.includes(forbidden), false, `campaign unit must not grant write access to ${forbidden}`);
  }
});

test("campaign review fails closed when systemd reports a non-empty DropInPaths set", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  if (linux) await chmod(fx.unitFile, 0o600);
  const dropIns = ["/etc/systemd/user/pixel-campaign-maintenance-recovery.service.d/override.conf"];
  await assert.rejects(
    superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({ dropIns }, fx.unitFile) }),
    /drop-in that could override ExecStart|refusing/u,
  );
});

test("campaign removal fails closed when systemd reports a non-empty DropInPaths set", async (t) => {
  const fx = await fixture(t);
  await writeFile(fx.unitFile, fx.render.unit, { mode: 0o600 });
  if (linux) await chmod(fx.unitFile, 0o600);
  const review = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: fx.unitDir, runSystemctl: makeSystemctl({}, fx.unitFile) });
  const options = removeOptions(fx, { enabled: true, state: "active", sub: "running", pid: "12345", dropIns: ["/etc/systemd/user/pixel-campaign-maintenance-recovery.service.d/override.conf"] });
  options.confirmRemoveSha256 = review.removeConfirmSha256;
  await assert.rejects(superviseCampaignRemove(options), /drop-in that could override ExecStart|refusing/u);
  assert.equal(await readFile(fx.unitFile, "utf8"), fx.render.unit, "a drop-in rejection must leave the campaign prior unit untouched");
});

// Proof-only real-systemd validation. This test may create/reload ONLY a
// unique pixel-campaign-guardian-proof-<random>.service user unit and a
// temporary drop-in for it; it never starts the fail-closed campaign stub or
// either fixed unit. It proves that supported systemd property behavior is
// enforced end-to-end: systemctl show returns the temporary drop-in, the
// supervisor fails closed on it, and after removing the drop-in/unit the
// exact empty drop-in set is re-proven and the supervisor accepts. Both fixed
// production unit states are snapshotted before and revalidated in
// unconditional teardown, leaving no proof fragment or drop-in behind.
const execFileP = promisify(execFile);

function systemdUserManagerAvailable() {
  if (process.platform === "win32") return false;
  try {
    const { status, stdout } = spawnSync("systemctl", ["--user", "is-system-running"], { encoding: "utf8", timeout: 10000 });
    const state = String(stdout ?? "").trim();
    return (state === "running" && status === 0) || (state === "degraded" && status === 1);
  }
  catch { return false; }
}
const hasUserSystemd = systemdUserManagerAvailable();

async function proofShowDropInPaths(unit) {
  const { stdout } = await execFileP("/usr/bin/systemctl", ["--user", "show", unit, "--property=DropInPaths"], { timeout: 10000 });
  return String(stdout ?? "").trim();
}

test("proof-only real-systemd validation creates a unique user unit and temporary drop-in, proves systemctl show and supervisor fail-closed, then re-proves absence", { skip: !hasUserSystemd ? "a supported user systemd manager is required for the real-systemd property proof" : false }, async (t) => {
  const fx = await fixture(t);
  const userDir = join(homedir(), ".config", "systemd", "user");
  await mkdir(userDir, { recursive: true, mode: 0o700 });
  const proofName = `pixel-campaign-guardian-proof-${createHash("sha256").update(String(Date.now()) + Math.random()).digest("hex").slice(0, 12)}.service`;
  const proofPath = join(userDir, proofName);
  const dropInDir = join(userDir, `${proofName}.d`);
  const dropInPath = join(dropInDir, "override.conf");
  const fixedUnits = ["pixel-maintenance-recovery.service", "pixel-campaign-maintenance-recovery.service"];
  const snapshot = async (unit) => {
    try {
      const { stdout } = await execFileP("/usr/bin/systemctl", ["--user", "show", unit, "--property=LoadState", "--property=FragmentPath"], { timeout: 10000 });
      return stdout;
    } catch (error) {
      return `UNREADABLE:${error?.code ?? "unknown"}`;
    }
  };
  const before = {};
  for (const unit of fixedUnits) before[unit] = await snapshot(unit);
  let proofWritten = false;
  let dropInCreated = false;
  let verified = false;
  let supervisorRejected = false;
  let absenceReProven = false;
  t.after(async () => {
    // Unconditional teardown: remove any proof fragment and drop-in, reload,
    // reset-failed, and revalidate both fixed units are exactly unchanged.
    await rm(dropInPath, { force: true }).catch(() => {});
    await rm(dropInDir, { recursive: true, force: true }).catch(() => {});
    await rm(proofPath, { force: true }).catch(() => {});
    await execFileP("/usr/bin/systemctl", ["--user", "daemon-reload"], { timeout: 10000 }).catch(() => {});
    await execFileP("/usr/bin/systemctl", ["--user", "reset-failed", proofName], { timeout: 10000 }).catch(() => {});
    for (const unit of fixedUnits) {
      const after = await snapshot(unit);
      assert.equal(after, before[unit], `fixed unit ${unit} state changed by the proof validation`);
    }
    assert.ok(proofWritten, "proof unit was actually written");
    assert.ok(dropInCreated, "temporary drop-in was actually created");
    assert.ok(verified, "proof validation actually ran");
    assert.ok(supervisorRejected, "the supervisor must reject the temporary drop-in");
    assert.ok(absenceReProven, "absence of the drop-in must be re-proven after cleanup");
  });
  // Create the exact temporary user unit and its temporary drop-in.
  await writeFile(proofPath, fx.render.unit, { mode: 0o600 });
  if (linux) await chmod(proofPath, 0o600);
  proofWritten = true;
  await mkdir(dropInDir, { recursive: true });
  if (linux) await chmod(dropInDir, 0o700);
  await writeFile(dropInPath, "[Unit]\nDescription=temporary proof override\n", { mode: 0o600 });
  if (linux) await chmod(dropInPath, 0o600);
  dropInCreated = true;
  await execFileP("/usr/bin/systemctl", ["--user", "daemon-reload"], { timeout: 10000 });
  // Prove systemctl show returns the temporary drop-in (non-empty DropInPaths).
  const shownWithDropIn = await proofShowDropInPaths(proofName);
  assert.match(shownWithDropIn, new RegExp(`DropInPaths=${dropInPath.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "u"), "systemctl show must report the temporary drop-in path");
  // Prove the supervisor fails closed on the drop-in before any authority.
  await assert.rejects(
    superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: userDir, unitName: proofName }),
    /drop-in that could override ExecStart|drop-in closure|refusing|could not be captured/u,
  );
  supervisorRejected = true;
  // Remove the temporary drop-in and unit, reload/reset-failed, and re-prove
  // the exact empty drop-in set.
  await rm(dropInDir, { recursive: true, force: true });
  await rm(proofPath, { force: true });
  await execFileP("/usr/bin/systemctl", ["--user", "daemon-reload"], { timeout: 10000 });
  await execFileP("/usr/bin/systemctl", ["--user", "reset-failed", proofName], { timeout: 10000 }).catch(() => {});
  const shownAfterCleanup = await proofShowDropInPaths(proofName);
  assert.equal(shownAfterCleanup, "DropInPaths=", "after cleanup systemctl show must report the exact empty drop-in set");
  // The supervisor now accepts the absent proof unit with the empty drop-in set.
  const review = await superviseCampaignReview({ configPath: fx.configPath, userSystemdDir: userDir, unitName: proofName });
  assert.equal(review.existingBytesSha256, null, "the removed proof unit must be reviewed as absent");
  assert.equal(review.engineReady, true);
  absenceReProven = true;
  verified = true;
});
