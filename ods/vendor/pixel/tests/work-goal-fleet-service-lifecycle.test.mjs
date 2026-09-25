import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, rm, unlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  activateGoalFleetServiceBundle, inspectGoalFleetServiceBundle, installGoalFleetServiceBundle, removeGoalFleetServiceBundle,
} from "../deploy/work-controller/goal-fleet-service-lifecycle.mjs";
import { runGoalFleetServiceLifecycleCommand } from "../deploy/work-controller/goal-fleet-service-lifecycle-cli.mjs";
import { renderGoalFleetServiceUnits } from "../deploy/work-controller/goal-fleet-service-units.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(value).digest("hex");
const goalId = "workgoal-1786366800001-abcdef123456";
const fleetId = "workfleet-1786366801000-abcdef123456";
const renderBoundary = "Private atomic fleet-systemd render bundle only. Rendering grants no work execution, installation, service enablement, lease, external effect, or completion authority.";
const renderAuthority = { grantsExecution: false, grantsInstall: false, grantsEnable: false, grantsLease: false, grantsExternalEffects: false };
const liveBindingVerifier = async () => Object.freeze({ configSha256: digest("c"), fleetSha256: digest("b") });

function inputs() {
  const controllerConfigPath = "/etc/pixel-work/goals/controller.json";
  const hostProbe = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-host-probe-v1.schema.json", schemaVersion: 1,
    fleetPath: "/etc/pixel-work/fleet.json", disposableDiskPath: "/var/lib/pixel-work/disposable",
    systemReserve: { cpuCores: 2, memoryMiB: 4096, diskBytes: 1073741824 },
    sharedServices: [{ serviceId: "local-model", unitName: "pixel-model.service", maxCpuCores: 2, maxMemoryMiB: 4096, maxDiskBytes: 1073741824, diskLimitEvidenceSha256: digest("d") }],
    evidenceLifetimeSeconds: 3600,
    boundary: "Owner-reviewed live host probe configuration. It names exact shared services and conservative reserves but grants no execution, lease, service mutation, scope expansion, external effect, or completion authority.",
  };
  const config = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-controller-v2.schema.json", schemaVersion: 2, enabled: true,
    stateRoot: "/var/lib/pixel-work/state", fleetPath: "/etc/pixel-work/fleet.json", hostProbePath: "/etc/pixel-work/host-probe.json",
    hostProbeSha256: createHash("sha256").update(canonical(hostProbe)).digest("hex"), hostEvidenceDirectory: "/var/lib/pixel-work/state/host-evidence", hostEvidenceGenesisSha256: digest("e"), goals: [{ goalId, controllerConfigPath }],
    boundary: "Owner-private fleet wiring only. It binds one immutable fleet and exact live host probe to exact per-goal controller configurations and grants no execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
  const fleet = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-v1.schema.json", schemaVersion: 1, fleetId, createdAt: "2026-08-10T13:00:01.000Z",
    discipline: { mode: "durable-round-robin", maxConcurrentTurns: 1, crashPolicy: "recover-active-before-next" },
    hostCapacity: { maxTurnSeconds: 3600, maxCpuCores: 8, maxMemoryMiB: 16384, maxDiskBytes: 10737418240, maxRetainedArtifactBytes: 16777216 },
    goals: [{
      goalId, goalSha256: digest("a"), jobsSha256: digest("b"), controllerConfigSha256: digest("c"),
      resourceEnvelope: { maxTurnSeconds: 960, maxWorkerSeconds: 600, maxVerifierSeconds: 60, maxCleanupSeconds: 300, maxCpuCores: 4, maxMemoryMiB: 4096, maxDiskBytes: 1073741824, maxRetainedArtifactBytes: 16777216 },
    }],
    authority: { grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable host-fleet schedule. It serializes bounded goal turns against exact resource envelopes but grants no job execution, lease, replay, scope expansion, external effect, or completion authority.",
  };
  const controller = {
    enabled: true, stateRoot: config.stateRoot, goalPath: "/etc/pixel-work/goals/goal.json", jobsPath: "/etc/pixel-work/goals/jobs.json",
    policyPath: "/etc/pixel-work/goals/policy.json", objectStore: "/var/lib/pixel-work/objects", workspaceRoot: "/var/lib/pixel-work/workspaces/one",
    executorPath: "/opt/omp/bin/omp", controller: { maxTransitions: 1 }, runtime: { dockerPath: "/usr/bin/docker" },
  };
  return { configPath: "/etc/pixel-work/fleet-controller.json", config, fleet, hostProbe, goalControllers: [{ goalId, configPath: controllerConfigPath, config: controller }], installRoot: "/opt/pixel" };
}

async function fixture(t, name = "bundle") {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-fleet-service-lifecycle-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const bundle = join(root, name), systemd = join(root, "systemd");
  await mkdir(bundle, { mode: 0o700 }); await mkdir(systemd, { mode: 0o755 });
  if (process.platform !== "win32") { await chmod(bundle, 0o700); await chmod(systemd, 0o755); }
  const units = renderGoalFleetServiceUnits(inputs());
  const serviceBytes = Buffer.from(units.service), timerBytes = Buffer.from(units.timer);
  const manifest = {
    schemaVersion: 1, operation: "pixel-work-goal-fleet-service-render", fleetId,
    fleetSha256: digest("b"), configSha256: digest("c"), serviceName: units.serviceName, serviceSha256: sha(serviceBytes),
    timerName: units.timerName, timerSha256: sha(timerBytes), legacyUnitNames: [...units.legacyUnitNames], authority: renderAuthority, boundary: renderBoundary,
  };
  const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`);
  for (const [filename, bytes] of [[units.serviceName, serviceBytes], [units.timerName, timerBytes], ["fleet-service-bundle.json", manifestBytes]]) {
    await writeFile(join(bundle, filename), bytes, { mode: 0o600 });
    if (process.platform !== "win32") await chmod(join(bundle, filename), 0o600);
  }
  return { root, bundle, systemd, units, manifest, manifestSha256: sha(manifestBytes), uid: process.geteuid?.() ?? 0 };
}

function successfulRunner(calls) {
  let enabled = false, timerActive = false, serviceStopped = false;
  return async (command, args) => {
    calls.push([command, ...args]);
    if (args[0] === "enable") { enabled = true; timerActive = true; }
    if (args[0] === "disable") { enabled = false; timerActive = false; }
    if (args[0] === "stop") serviceStopped = true;
    if (args[0] === "is-enabled") return { code: enabled ? 0 : 1, stdout: "", stderr: "" };
    if (args[0] === "is-active") return { code: String(args.at(-1)).endsWith(".timer") ? timerActive ? 0 : 1 : serviceStopped ? 1 : 0, stdout: "", stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };
}

test("fleet lifecycle installs inactive, rejects legacy units, activates separately, and removes safely", async (t) => {
  const value = await fixture(t);
  const inspected = await inspectGoalFleetServiceBundle(value.bundle, { expectedOwnerUid: value.uid });
  assert.equal(inspected.manifestSha256, value.manifestSha256);
  const calls = [];
  const common = {
    bundlePath: value.bundle, confirmation: value.manifestSha256, systemdDirectory: value.systemd,
    expectedBundleUid: value.uid, expectedSystemUid: value.uid, commandRunner: successfulRunner(calls),
    systemdAnalyzePath: "/fixture/systemd-analyze", systemctlPath: "/fixture/systemctl", liveBindingVerifier,
  };
  assert.equal((await installGoalFleetServiceBundle(common)).state, "installed-inactive");
  assert.ok(!calls.some((call) => call.includes("enable")));
  const legacyPath = join(value.systemd, value.manifest.legacyUnitNames[0]);
  await writeFile(legacyPath, "legacy\n", { mode: 0o644 });
  await assert.rejects(activateGoalFleetServiceBundle(common), /legacy per-goal service unit/);
  assert.ok(!calls.some((call) => call.includes("enable")));
  await unlink(legacyPath);
  const activated = await activateGoalFleetServiceBundle(common);
  assert.equal(activated.state, "active");
  assert.equal(activated.authority.schedulesCycles, true);
  const removed = await removeGoalFleetServiceBundle(common);
  assert.equal(removed.state, "removed-private-state-retained");
  await assert.rejects(readFile(join(value.systemd, value.units.serviceName)));
  await assert.rejects(readFile(join(value.systemd, value.units.timerName)));
});

test("fleet lifecycle rejects linked, tampered, colliding, and unconfirmed bundles", async (t) => {
  const linked = await fixture(t, "linked");
  await link(join(linked.bundle, linked.units.serviceName), join(linked.root, "extra-link"));
  await assert.rejects(inspectGoalFleetServiceBundle(linked.bundle, { expectedOwnerUid: linked.uid }), /link count/);
  const tampered = await fixture(t, "tampered");
  await writeFile(join(tampered.bundle, tampered.units.timerName), "tampered\n", { mode: 0o600 });
  await assert.rejects(inspectGoalFleetServiceBundle(tampered.bundle, { expectedOwnerUid: tampered.uid }), /differs from its manifest/);
  const collision = await fixture(t, "collision");
  await writeFile(join(collision.systemd, collision.units.serviceName), "different\n", { mode: 0o644 });
  await assert.rejects(installGoalFleetServiceBundle({
    bundlePath: collision.bundle, confirmation: collision.manifestSha256, systemdDirectory: collision.systemd,
    expectedBundleUid: collision.uid, expectedSystemUid: collision.uid, commandRunner: successfulRunner([]), liveBindingVerifier,
  }), /different fleet service/);
  await assert.rejects(installGoalFleetServiceBundle({
    bundlePath: collision.bundle, confirmation: digest("e"), systemdDirectory: collision.systemd,
    expectedBundleUid: collision.uid, expectedSystemUid: collision.uid, commandRunner: successfulRunner([]), liveBindingVerifier,
  }), /confirmation differs/);
});

test("fleet lifecycle CLI separates inspection from Linux root mutations", async (t) => {
  const value = await fixture(t, "cli");
  const inspected = await runGoalFleetServiceLifecycleCommand(["inspect", "--bundle", value.bundle], { euid: value.uid, liveBindingVerifier });
  assert.equal(inspected.state, "bundle-verified-no-side-effects");
  assert.ok(!JSON.stringify(inspected).includes(value.root));
  await assert.rejects(runGoalFleetServiceLifecycleCommand([
    "install", "--bundle", value.bundle, "--confirm-manifest-sha256", value.manifestSha256,
  ], { platform: "linux", euid: 1000 }), /require Linux root/);
  await assert.rejects(runGoalFleetServiceLifecycleCommand([
    "install", "--bundle", value.bundle, "--confirm-manifest-sha256", value.manifestSha256,
  ], { platform: "linux", euid: 0, identityRunner: async () => ({ code: 0, stdout: `${value.uid + 1}\n`, stderr: "" }) }), /owned by the configured unprivileged service identity/);
});

test("fleet lifecycle CLI expected-owner-uid is root-only and drives inspect ownership", async (t) => {
  const value = await fixture(t, "cli");
  await assert.rejects(runGoalFleetServiceLifecycleCommand(
    ["inspect", "--bundle", value.bundle, "--expected-owner-uid", String(value.uid)],
    { euid: value.uid, liveBindingVerifier },
  ), /requires root authority/);
  const inspection = await runGoalFleetServiceLifecycleCommand(
    ["inspect", "--bundle", value.bundle, "--expected-owner-uid", String(value.uid)],
    { euid: 0, liveBindingVerifier },
  );
  assert.equal(inspection.state, "bundle-verified-no-side-effects");
  await assert.rejects(runGoalFleetServiceLifecycleCommand(
    ["inspect", "--bundle", value.bundle, "--expected-owner-uid", String(value.uid + 1)],
    { euid: 0 },
  ), /ownership or mode/);
});
