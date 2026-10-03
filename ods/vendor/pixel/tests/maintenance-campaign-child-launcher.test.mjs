import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";

import {
  WorkCampaignChildLaunchError,
  deriveCampaignChildUnitName,
  launchCampaignChildSupervised,
  validateCampaignChildNonce,
} from "../deploy/work-controller/maintenance-campaign-child-launcher.mjs";
import {
  advanceCampaignRecoveryJournal,
  createCampaignRecoveryJournal,
  deriveCampaignJournalIdentity,
  inspectActiveCampaignRecoveryJournal,
} from "../deploy/work-controller/maintenance-recovery-journal.mjs";
import { buildSystemdEnv } from "../deploy/work-controller/maintenance-recovery-guardian-supervisor.mjs";

const ROOT = resolve(new URL("..", import.meta.url).pathname);
const SUPERVISOR = join(ROOT, "deploy/work-controller/maintenance-campaign-child-supervisor.mjs");
const FIXTURE = join(ROOT, "tests/fixtures/campaign-child-fixture.mjs");
const digest = (c) => c.repeat(64);
const sha256 = (v) => createHash("sha256").update(v).digest("hex");
const uid = process.geteuid?.() ?? 1000;
const linux = process.platform !== "win32";
const BOOT_ID = "01234567-89ab-cdef-0123-456789abcdef";

const progressStdout = JSON.stringify({
  operation: "pixel-portal-outcome-battery-campaign-progress",
  campaignId: "outcomebattery-" + "a".repeat(24),
  profile: "builder", evaluationRegime: "matched-budget", partition: "tuning",
  runtimeCondition: "cold-first-request", status: "pass",
  requiredPairs: 1, completedPairs: 1,
  tuningBaselineFrozen: false, tuningBaselineFreezeSha256: null,
});

// P0: every pre-terminal or unexpected error class must be converted to a
// content-free WorkCampaignChildLaunchError, so the controller holds production.
async function assertLaunchHeld(promise, phase, { launch = true } = {}) {
  await assert.rejects(promise, (error) => {
    assert.ok(error instanceof WorkCampaignChildLaunchError, `expected WorkCampaignChildLaunchError, got ${error?.constructor?.name}: ${error?.message}`);
    assert.equal(JSON.stringify(error.message).includes("campaign child launch did not reach"), true, "launch error must be content-free");
    return true;
  });
  const inspected = await inspectActiveCampaignRecoveryJournal(phase.lockPath, uid);
  assert.equal(inspected.journal.phase, phase.expected, `journal must be retained at ${phase.expected}`);
}

async function privateWrite(path, value, mode = 0o600) {
  await writeFile(path, typeof value === "string" ? value : `${JSON.stringify(value, null, 2)}\n`, { mode });
  if (linux) await chmod(path, mode);
}

async function journalFixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-child-launcher-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  if (linux) await chmod(root, 0o700);
  const journalDir = join(root, "journal");
  await mkdir(journalDir, { recursive: true });
  const custodyLockPath = join(journalDir, "guardian.lock");
  await writeFile(custodyLockPath, "", { mode: 0o600 });
  if (linux) await chmod(custodyLockPath, 0o600);
  const record = {
    schemaVersion: 1,
    kind: "pixel-campaign-maintenance-recovery-journal",
    operation: "pixel-work-model-campaign-maintenance",
    phase: "prepared",
    campaignChildNonce: null,
    campaignChild: null,
    guardianStartedAt: null,
    guardianStartNonce: null,
    guardianStartEndpointIdentity: null,
    guardianStartReceipt: null,
    readinessEvidence: null,
    campaignOutcome: null,
    maintenanceOperationSha256: digest("a"),
    campaignOperationSha256: digest("b"),
    custodyIdentitySha256: digest("c"),
    configurationSha256: digest("d"),
    ownerUid: uid,
    production: {
      containerName: "pixel-production-model", containerId: digest("1"),
      imageDigest: `sha256:${digest("2")}`,
      expectedStartedAt: "2026-08-13T12:00:00.123456789Z",
      expectedRestartCount: 0,
    },
    comparison: {
      materializationSha256: digest("1"), pairConfigurationSha256: digest("2"),
      preflightSha256: digest("3"), acceleratorStateSha256: digest("4"),
    },
  };
  const created = await createCampaignRecoveryJournal(custodyLockPath, uid, record);
  const journalIdentity = deriveCampaignJournalIdentity(created.journal);
  await advanceCampaignRecoveryJournal(custodyLockPath, uid, "production-stopped", journalIdentity);
  return { root, journalDir, custodyLockPath, journalIdentity, record };
}

async function buildCtx(fx, overrides = {}) {
  const childDir = join(fx.root, "child");
  return {
    expectedOwnerUid: uid,
    custodyLockPath: fx.custodyLockPath,
    journalIdentity: fx.journalIdentity,
    campaignOperationSha256: digest("b"),
    innerPython: {
      path: process.execPath,
      argv: [FIXTURE, "progress"],
      cwd: ROOT,
      env: { PATH: process.env.PATH ?? "", HOME: process.env.HOME ?? "/tmp" },
      timeoutMilliseconds: 5000,
      maxStdoutBytes: 1024 * 1024,
      maxStderrBytes: 1024 * 1024,
    },
    supervisorPath: SUPERVISOR,
    supervisorSha256: sha256(await readFile(SUPERVISOR)),
    nodePath: process.execPath,
    outDir: childDir,
    systemdEnv: buildSystemdEnv(uid),
    expectedBootId: BOOT_ID,
    configSha256: digest("e"),
    leaseMaxAgeMs: undefined,
    leaseBootId: undefined,
    guardianUnit: "pixel-campaign-maintenance-recovery.service",
    configPath: join(fx.root, "config.json"),
    custodyIdentitySha256: digest("c"),
    guardianModuleSha256: digest("f"),
    guardianModulePath: join(ROOT, "deploy/work-controller/maintenance-campaign-recovery-guardian.mjs"),
    guardianUnitSha256: digest("g"),
    journalIdentitySha256: digest("h"),
    ...overrides,
  };
}

// Stateful fake systemd + process identity. runSystemdRun starts the unit and
// asynchronously publishes the supervisor receipt/result/diagnostic artifacts
// (as a real bounded supervisor would), transitioning the fake unit from
// active/running to inactive/dead once the artifacts are published.
function makeFakes(fx, { nonce, innerPython, nodePath, supervisorPath, paths, behavior = {} } = {}) {
  const cgroup = `/user.slice/user-${uid}.slice/user@${uid}.service/app.slice/pixel-campaign-child-${nonce}.service`;
  const invocationId = behavior.invocationId ?? "abcdef0123456789abcdef0123456789";
  let started = false;
  let postStartShows = 0;
  const argv = [nodePath, supervisorPath, "run", "--nonce", nonce, "--contract", paths.contractPath, "--receipt", paths.receiptPath];
  const processIdentity = {
    bootId: BOOT_ID, pid: 4242, startTicks: 12345,
    exe: nodePath, argv, cgroup: [`0::${cgroup}`], uid: String(uid),
  };
  const resultBytes = Buffer.from(progressStdout, "utf8");
  const diagnosticBytes = Buffer.alloc(0);
  const receipt = {
    schemaVersion: 1, kind: "pixel-campaign-child-supervisor-receipt", operation: "pixel-work-model-campaign-maintenance",
    nonce, campaignOperationSha256: digest("b"),
    innerIdentity: { executableSha256: digest("1"), argvSha256: digest("2"), contractSha256: digest("3") },
    outcome: { exitCode: 0, signal: null, timedOut: false, outputOverflow: null, spawnError: false },
    stdout: { bytes: resultBytes.length, sha256: sha256(resultBytes) },
    stderr: { bytes: 0, sha256: sha256(Buffer.alloc(0)) },
    terminalTime: "2026-08-20T19:00:00.000Z",
  };
  return {
    cgroup, invocationId, processIdentity, receipt, paths,
    showUnit: async (unit) => {
      if (started) postStartShows += 1;
      if (behavior.showUnitOverride) return behavior.showUnitOverride(unit, { started, postStartShows });
      if (!started) return { ok: true, facts: { LoadState: "not-found", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: "", ControlGroup: "", FragmentPath: "", DropInPaths: [] } };
      if (postStartShows === 1) {
        return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: String(processIdentity.pid), InvocationID: invocationId, ControlGroup: behavior.controlGroup ?? cgroup, FragmentPath: "", DropInPaths: [] } };
      }
      return { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: invocationId, ControlGroup: behavior.controlGroup ?? cgroup, FragmentPath: "", DropInPaths: [] } };
    },
    deriveProcessIdentity: async (pid) => {
      if (behavior.deriveProcessIdentityOverride) return behavior.deriveProcessIdentityOverride(pid, processIdentity);
      return { ...processIdentity, pid };
    },
    runSystemdRun: async (unit, contract, ctx) => {
      if (behavior.runSystemdRunOverride) return behavior.runSystemdRunOverride(unit, contract, ctx, { receipt, resultBytes, diagnosticBytes, started, setStarted: (v) => { started = v; } });
      started = true;
      await privateWrite(contract.receiptPath, receipt);
      await privateWrite(contract.resultPath, resultBytes.toString("utf8"), 0o600);
      await privateWrite(contract.diagnosticPath, "", 0o600);
      return { code: 0, stdout: "", stderr: "" };
    },
    isProcessAlive: async (pid) => Boolean(behavior.processAlive),
    proveCgroupEmpty: async () => { if (behavior.cgroupHasLive) throw new Error("cgroup live"); },
    classifySupervised: async (supervised) => ({
      ok: true,
      result: { campaignId: "outcomebattery-" + "a".repeat(24), campaignStatus: "pass", exitCode: 0, resultSha256: sha256(resultBytes), completedPairs: 1, requiredPairs: 1 },
    }),
    requireLease: async () => ({ pid: process.pid, unit: "pixel-campaign-maintenance-recovery.service", refreshedAt: new Date().toISOString() }),
    randomNonce: async () => nonce,
    advanceJournal: advanceCampaignRecoveryJournal,
    readInvocationId: async (pid) => invocationId,
  };
}

test("launcher derives the exact full-nonce unit name and rejects reuse/zero", () => {
  const nonce = digest("a");
  assert.equal(deriveCampaignChildUnitName(nonce), `pixel-campaign-child-${nonce}.service`);
  assert.throws(() => validateCampaignChildNonce("0".repeat(64)), /nonzero/);
  assert.throws(() => validateCampaignChildNonce(digest("a") + "0"), /nonce/);
});

test("happy path: production-stopped -> authorized -> active -> comparison-cleanup-pending with exact identity", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const paths = {
    contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`),
    receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`),
    resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`),
    diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`),
  };
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths });
  const outcome = await launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised });
  assert.equal(outcome.unitName, `pixel-campaign-child-${nonce}.service`);
  assert.equal(outcome.result.campaignStatus, "pass");
  assert.equal(outcome.childIdentity.pid, 4242);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "comparison-cleanup-pending");
  assert.equal(journal.campaignChildNonce, nonce);
  assert.equal(journal.campaignChild.pid, 4242);
  assert.equal(journal.campaignChild.uid, String(uid));
  assert.deepEqual(journal.campaignChild.argv, [ctx.nodePath, ctx.supervisorPath, "run", "--nonce", nonce, "--contract", paths.contractPath, "--receipt", paths.receiptPath]);
  assert.equal(journal.campaignChild.bootId, BOOT_ID);
  assert.equal(journal.campaignChild.startTicks, 12345);
  assert.equal(journal.campaignChild.exe, ctx.nodePath);
});

test("authorize phase failure (deleted journal) prevents any launch and retains no launch effect", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  let launched = false;
  fakes.runSystemdRun = async () => { launched = true; return { code: 0, stdout: "", stderr: "" }; };
  fakes.advanceJournal = async (path, owner, phase) => {
    if (phase === "campaign-child-authorized") throw new Error("journal deleted");
    return advanceCampaignRecoveryJournal(path, owner, phase, fx.journalIdentity, {});
  };
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  assert.equal(launched, false, "no launch when authorize fails");
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "production-stopped");
});

test("reassert-before-start failure (substituted journal) prevents launch and journal stays at authorized", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  let launched = false;
  let authorizeCount = 0;
  fakes.runSystemdRun = async () => { launched = true; return { code: 0, stdout: "", stderr: "" }; };
  fakes.advanceJournal = async (path, owner, phase, identity, fields = {}) => {
    if (phase === "campaign-child-authorized") {
      authorizeCount += 1;
      if (authorizeCount === 2) throw new Error("substituted journal identity");
    }
    return advanceCampaignRecoveryJournal(path, owner, phase, identity, fields);
  };
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  assert.equal(launched, false, "no launch when reassert fails");
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-authorized");
  assert.equal(journal.campaignChildNonce, nonce);
});

test("pre-existing unit prevents launch and never advances past authorized", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  let launched = false;
  fakes.runSystemdRun = async () => { launched = true; return { code: 0, stdout: "", stderr: "" }; };
  fakes.showUnit = async (unit) => ({ ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: "999", InvocationID: "0".repeat(32), ControlGroup: "/c.service", FragmentPath: "", DropInPaths: [] } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  assert.equal(launched, false);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-authorized");
});

test("start response ambiguity (unit never becomes active) leaves journal at authorized, never relaunches", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  let started = false;
  fakes.runSystemdRun = async () => { started = true; return { code: 0, stdout: "", stderr: "" }; };
  fakes.showUnit = async (unit) => ({ ok: true, facts: { LoadState: "not-found", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: "", ControlGroup: "", FragmentPath: "", DropInPaths: [] } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  assert.equal(started, true);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-authorized");
});

test("wrong MainPID/argv/cgroup/owner in /proc identity fails identity proof", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  fakes.deriveProcessIdentity = async (pid) => ({ ...fakes.processIdentity, argv: ["/usr/bin/evil"] });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-authorized");
});

test("wrong InvocationID in unit facts fails identity proof", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  fakes.readInvocationId = async () => "ffffffffffffffffffffffffffffffff";
  fakes.invocationId = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-authorized");
});

test("wrong boot id in /proc identity fails identity proof (journal never claims active)", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  fakes.deriveProcessIdentity = async (pid) => ({ ...fakes.processIdentity, bootId: "ffffffff-ffff-ffff-ffff-ffffffffffff" });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-authorized");
});

test("supervisor still running at terminal proof blocks cleanup-pending advance", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  fakes.isProcessAlive = async () => true;
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-active");
});

test("live cgroup descendant blocks cleanup-pending advance", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  fakes.proveCgroupEmpty = async () => { throw new Error("cgroup live"); };
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-active");
});

test("unit identity substituted after completion blocks cleanup-pending", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { showUnitOverride: async (unit, { started, postStartShows }) => {
    if (!started) return { ok: true, facts: { LoadState: "not-found", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: "", ControlGroup: "", FragmentPath: "", DropInPaths: [] } };
    if (postStartShows === 1) return { ok: true, facts: { LoadState: "loaded", ActiveState: "active", SubState: "running", MainPID: "4242", InvocationID: fakes.invocationId, ControlGroup: fakes.cgroup, FragmentPath: "", DropInPaths: [] } };
    return { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: "99999999999999999999999999999999", ControlGroup: fakes.cgroup, FragmentPath: "", DropInPaths: [] } };
  } } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-active");
});

test("malformed/symlinked/oversized receipt blocks classification and holds journal at active", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { runSystemdRunOverride: async (unit, contract, ctx, helpers) => {
    helpers.setStarted(true);
    const target = join(fx.root, "real-receipt.json");
    await privateWrite(target, fakes.receipt);
    await symlink(target, contract.receiptPath);
    return { code: 0, stdout: "", stderr: "" };
  } } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised, receiptPollMs: 20, receiptWaitMs: 200 }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-active");
});

test("stdout not coherent with receipt (substituted result) blocks classification", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { runSystemdRunOverride: async (unit, contract, ctx, helpers) => {
    helpers.setStarted(true);
    await privateWrite(contract.receiptPath, fakes.receipt);
    await privateWrite(contract.resultPath, "substituted-not-the-same-bytes");
    await privateWrite(contract.diagnosticPath, "");
    return { code: 0, stdout: "", stderr: "" };
  } } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised, receiptPollMs: 20, receiptWaitMs: 200 }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-active");
});

// ---------------------------------------------------------------------------
// M5 repair tests: P1 absent-vs-tampered receipt polling, P0 no-clobber
// contract publication, P0 live-descendant cgroup refusal, P1 result/diagnostic
// owner-private validation.
// ---------------------------------------------------------------------------

test("P1 missing receipt times out and holds the journal at campaign-child-active (absent retried, never tampered)", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { runSystemdRunOverride: async (unit, contract, ctx, helpers) => {
    helpers.setStarted(true);
    // Never write a receipt; only advance to active.
    return { code: 0, stdout: "", stderr: "" };
  } } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised, receiptPollMs: 10, receiptWaitMs: 60 }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  const journal = inspected.journal;
  assert.equal(journal.phase, "campaign-child-active");
});

test("P1 a receipt that appears then becomes tampered fails immediately, not at the deadline", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { runSystemdRunOverride: async (unit, contract, ctx, helpers) => {
    helpers.setStarted(true);
    await privateWrite(contract.receiptPath, "not-json{{");
    return { code: 0, stdout: "", stderr: "" };
  } } });
  const started = Date.now();
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised, receiptPollMs: 10, receiptWaitMs: 5000 }), WorkCampaignChildLaunchError);
  const elapsed = Date.now() - started;
  assert.ok(elapsed < 2000, `tampered receipt must fail immediately, not at the deadline: ${elapsed}ms`);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-active");
});

test("P0 contract publication never replaces a preexisting contract target", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const contractPath = join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`);
  // Pre-create the contract target as an attacker file.
  await mkdir(ctx.outDir, { recursive: true });
  if (linux) await chmod(ctx.outDir, 0o700);
  await privateWrite(contractPath, { attacker: true });
  const before = await readFile(contractPath, "utf8");
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath, receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  const after = await readFile(contractPath, "utf8");
  assert.equal(after, before, "the preexisting contract target must remain unchanged");
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-authorized");
});

test("P0 live cgroup descendant (exact path) blocks cleanup-pending advance", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { cgroupHasLive: true } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-active");
});

test("P1 wrong-owner result artifact blocks classification and holds journal at active", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { runSystemdRunOverride: async (unit, contract, ctx, helpers) => {
    helpers.setStarted(true);
    await privateWrite(contract.receiptPath, fakes.receipt);
    await privateWrite(contract.resultPath, progressStdout, 0o644);
    await privateWrite(contract.diagnosticPath, "");
    return { code: 0, stdout: "", stderr: "" };
  } } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised, receiptPollMs: 10, receiptWaitMs: 200 }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-active");
});

test("P1 hardlinked diagnostic artifact blocks classification and holds journal at active", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { runSystemdRunOverride: async (unit, contract, ctx, helpers) => {
    helpers.setStarted(true);
    await privateWrite(contract.receiptPath, fakes.receipt);
    await privateWrite(contract.resultPath, progressStdout);
    // Create a hardlink to another file as the diagnostic.
    const other = join(fx.root, "other-diagnostic");
    await privateWrite(other, "linked-bytes");
    const { link } = await import("node:fs/promises");
    await link(other, contract.diagnosticPath);
    return { code: 0, stdout: "", stderr: "" };
  } } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised, receiptPollMs: 10, receiptWaitMs: 200 }), WorkCampaignChildLaunchError);
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-active");
});

// ---------------------------------------------------------------------------
// P0 adversarial: real non-launcher error classes at several pre-terminal seams
// must be converted to a content-free WorkCampaignChildLaunchError, so the
// controller holds production and never restores. The error classes here are
// deliberately NOT WorkCampaignChildLaunchError (contract publication ->
// WorkMaintenanceSecureFileError; systemd start -> plain Error; receipt
// validation -> WorkCampaignChildSupervisorError; artifact read ->
// WorkMaintenanceSecureFileError; terminal proof -> plain Error).
// ---------------------------------------------------------------------------

test("P0 contract publication WorkMaintenanceSecureFileError is converted to a content-free launch error", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const contractPath = join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`);
  await mkdir(ctx.outDir, { recursive: true });
  if (linux) await chmod(ctx.outDir, 0o700);
  await privateWrite(contractPath, { attacker: true });
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath, receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), (error) => {
    assert.ok(error instanceof WorkCampaignChildLaunchError, `expected WorkCampaignChildLaunchError, got ${error?.constructor?.name}`);
    assert.ok(!JSON.stringify(error.message).includes("already exists"), "must be content-free");
    return true;
  });
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-authorized");
});

test("P0 systemd start plain Error is converted to a content-free launch error", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  fakes.runSystemdRun = async () => { throw new Error("systemd start exploded"); };
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), (error) => {
    assert.ok(error instanceof WorkCampaignChildLaunchError, `expected WorkCampaignChildLaunchError, got ${error?.constructor?.name}`);
    assert.ok(!JSON.stringify(error.message).includes("systemd start exploded"), "must be content-free");
    return true;
  });
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-authorized");
});

test("P0 receipt validation WorkCampaignChildSupervisorError is converted to a content-free launch error", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { runSystemdRunOverride: async (unit, contract, ctx, helpers) => {
    helpers.setStarted(true);
    // Publish a receipt that fails strict validation (contradictory timeout+exit).
    await privateWrite(contract.receiptPath, { ...fakes.receipt, outcome: { exitCode: 1, signal: null, timedOut: true, outputOverflow: null, spawnError: false } });
    await privateWrite(contract.resultPath, progressStdout);
    await privateWrite(contract.diagnosticPath, "");
    return { code: 0, stdout: "", stderr: "" };
  } } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised, receiptPollMs: 10, receiptWaitMs: 200 }), (error) => {
    assert.ok(error instanceof WorkCampaignChildLaunchError, `expected WorkCampaignChildLaunchError, got ${error?.constructor?.name}`);
    assert.ok(!JSON.stringify(error.message).includes("contradictory"), "must be content-free");
    return true;
  });
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-active");
});

test("P0 artifact read WorkMaintenanceSecureFileError is converted to a content-free launch error", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) }, behavior: { runSystemdRunOverride: async (unit, contract, ctx, helpers) => {
    helpers.setStarted(true);
    await privateWrite(contract.receiptPath, fakes.receipt);
    // resultPath is a symlink -> readOwnerPrivateBoundedFile throws WorkMaintenanceSecureFileError.
    await privateWrite(contract.diagnosticPath, "");
    const { symlink } = await import("node:fs/promises");
    const real = join(fx.root, "real-result");
    await privateWrite(real, progressStdout);
    await symlink(real, contract.resultPath);
    return { code: 0, stdout: "", stderr: "" };
  } } });
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised, receiptPollMs: 10, receiptWaitMs: 200 }), (error) => {
    assert.ok(error instanceof WorkCampaignChildLaunchError, `expected WorkCampaignChildLaunchError, got ${error?.constructor?.name}`);
    assert.ok(!JSON.stringify(error.message).includes("could not be opened safely"), "must be content-free");
    return true;
  });
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-active");
});

test("P0 terminal proof plain Error is converted to a content-free launch error", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  fakes.proveCgroupEmpty = async () => { throw new Error("cgroup probe failed unexpectedly"); };
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised: fakes.classifySupervised }), (error) => {
    assert.ok(error instanceof WorkCampaignChildLaunchError, `expected WorkCampaignChildLaunchError, got ${error?.constructor?.name}`);
    assert.ok(!JSON.stringify(error.message).includes("cgroup probe failed"), "must be content-free");
    return true;
  });
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-active");
});

// ---------------------------------------------------------------------------
// P1 direct primitive tests: exact cgroup matching versus prefix/suffix/
// substring attacks, runtime-bound derivation, and terminal-state
// substitutions.
// ---------------------------------------------------------------------------

test("P1 exact cgroup matching rejects prefix, suffix, and substring attacks", async () => {
  const { exactCgroupLineMatches } = await import("../deploy/work-controller/maintenance-campaign-child-launcher.mjs");
  const cgroup = "/user.slice/user-1000.slice/user@1000.service/app.slice/pixel-campaign-child-abc.service";
  const line = `0::${cgroup}`;
  assert.equal(exactCgroupLineMatches(line, cgroup), true, "the exact cgroup path must match");
  assert.equal(exactCgroupLineMatches(`0::${cgroup}-evil`, cgroup), false, "a suffix attack must not match");
  assert.equal(exactCgroupLineMatches(`0::evil${cgroup}`, cgroup), false, "a prefix attack must not match");
  assert.equal(exactCgroupLineMatches(`0::${cgroup.slice(0, 20)}`, cgroup), false, "a truncated prefix must not match");
  assert.equal(exactCgroupLineMatches(`0::${cgroup}extra`, cgroup), false, "a substring-plus-suffix attack must not match");
  assert.equal(exactCgroupLineMatches("garbage", cgroup), false, "malformed input must not match");
});

test("P1 runtime-bound derivation is exact and bounded", async () => {
  const { deriveCampaignChildRuntimeMaxSec, CAMPAIGN_CHILD_RUNTIME_GRACE_SECONDS } = await import("../deploy/work-controller/maintenance-campaign-child-launcher.mjs");
  assert.equal(deriveCampaignChildRuntimeMaxSec(1000), 1 + CAMPAIGN_CHILD_RUNTIME_GRACE_SECONDS);
  assert.equal(deriveCampaignChildRuntimeMaxSec(3000), 3 + CAMPAIGN_CHILD_RUNTIME_GRACE_SECONDS);
  assert.equal(deriveCampaignChildRuntimeMaxSec(999), 1 + CAMPAIGN_CHILD_RUNTIME_GRACE_SECONDS, "sub-second bounds round up");
  assert.throws(() => deriveCampaignChildRuntimeMaxSec(0), /invalid/);
  assert.throws(() => deriveCampaignChildRuntimeMaxSec(-5), /invalid/);
  assert.throws(() => deriveCampaignChildRuntimeMaxSec(1.5), /invalid/);
});

test("P1 terminal-state substitution is rejected: only exact loaded/inactive+dead is accepted", async () => {
  const { isAcceptedTerminalState } = await import("../deploy/work-controller/maintenance-campaign-child-launcher.mjs");
  const base = { ok: true, facts: { LoadState: "loaded", ActiveState: "inactive", SubState: "dead", MainPID: "0", InvocationID: "a".repeat(32), ControlGroup: "/c.service", FragmentPath: "", DropInPaths: [] } };
  assert.equal(isAcceptedTerminalState(base), true);
  assert.equal(isAcceptedTerminalState({ ...base, facts: { ...base.facts, ActiveState: "active" } }), false, "active is not terminal");
  assert.equal(isAcceptedTerminalState({ ...base, facts: { ...base.facts, SubState: "running" } }), false, "running is not terminal");
  assert.equal(isAcceptedTerminalState({ ...base, facts: { ...base.facts, LoadState: "not-found" } }), false, "not-found is handled separately, not as an accepted terminal state here");
  assert.equal(isAcceptedTerminalState({ ...base, facts: { ...base.facts, ActiveState: "failed" } }), false, "failed is not an accepted terminal state");
});

// ---------------------------------------------------------------------------
// P0: no string-spoofed CampaignFailure bypass. A pre-terminal ordinary Error
// whose `name` is set to "CampaignFailure" must NOT be preserved/restored by
// name alone. It must be converted to a content-free WorkCampaignChildLaunchError,
// remain at the truthful journal phase, and never reach classification.
// ---------------------------------------------------------------------------
test("P0 name-spoofed ordinary Error before terminal proof is converted to a content-free launch error and never reaches classification", async (t) => {
  const fx = await journalFixture(t);
  const nonce = digest("a");
  const ctx = await buildCtx(fx);
  const fakes = makeFakes(fx, { nonce, nodePath: ctx.nodePath, supervisorPath: ctx.supervisorPath, paths: { contractPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.contract.json`), receiptPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.receipt.json`), resultPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.result`), diagnosticPath: join(ctx.outDir, `pixel-campaign-child-${nonce}.diagnostic`) } });
  // A pre-terminal operation throws an ordinary Error that spoofs the
  // CampaignFailure name. It must be converted to a content-free
  // WorkCampaignChildLaunchError and must never reach classification.
  fakes.proveCgroupEmpty = async () => {
    const spoofed = new Error("spoofed campaign failure content");
    spoofed.name = "CampaignFailure";
    throw spoofed;
  };
  let classificationReached = false;
  const classifySupervised = async () => { classificationReached = true; return { ok: true, result: {} }; };
  await assert.rejects(launchCampaignChildSupervised(ctx, { ...fakes, classifySupervised }), (error) => {
    assert.ok(error instanceof WorkCampaignChildLaunchError, `expected WorkCampaignChildLaunchError, got ${error?.constructor?.name}`);
    assert.ok(!(error instanceof Error) || error.name !== "CampaignFailure", "a spoofed name must not survive as a CampaignFailure");
    assert.ok(!JSON.stringify(error.message).includes("spoofed campaign failure content"), "must be content-free");
    return true;
  });
  assert.equal(classificationReached, false, "classification must never be reached for a pre-terminal spoofed error");
  const inspected = await inspectActiveCampaignRecoveryJournal(fx.custodyLockPath, uid);
  assert.equal(inspected.journal.phase, "campaign-child-active", "journal must remain at the truthful pre-terminal phase");
});
