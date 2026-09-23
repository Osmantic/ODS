import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  activateGoalServiceBundle, inspectGoalServiceBundle, installGoalServiceBundle, removeGoalServiceBundle,
} from "../deploy/work-controller/goal-service-lifecycle.mjs";
import { runGoalServiceLifecycleCommand } from "../deploy/work-controller/goal-service-lifecycle-cli.mjs";
import { renderGoalServiceUnits } from "../deploy/work-controller/goal-service-units.mjs";

const digest = (character) => character.repeat(64);
const sha = (value) => createHash("sha256").update(value).digest("hex");
const renderBoundary = "Private atomic systemd render bundle only. Rendering grants no work execution, installation, service enablement, lease, external effect, or completion authority.";
const renderAuthority = { grantsExecution: false, grantsInstall: false, grantsEnable: false, grantsLease: false, grantsExternalEffects: false };
const liveBindingVerifier = async () => Object.freeze({ configSha256: digest("c"), goalSha256: digest("b"), capability: Object.freeze({ state: "disabled", jobCount: 0, packCount: 0, toolCount: 0 }) });

function goal() {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: "workgoal-1786366800001-abcdef123456", createdAt: "2026-08-10T13:00:00.001Z", requester: "pixel",
    objective: "Complete the lifecycle fixture.", dataClassification: "internal",
    milestones: [{ milestoneId: "build", jobId: "work-1786366800000-abcdef123456", jobSha256: digest("a"), profile: "builder", dependsOn: [] }],
    budgets: { maxJobs: 1, maxRuntimeSeconds: 600, maxModelRequests: 10, maxInputTokens: 10000, maxOutputTokens: 2000, maxNetworkBytes: 1048576, maxArtifactBytes: 16777216, maxFailures: 2 },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.",
  };
}

function config() {
  return {
    enabled: true, stateRoot: "/var/lib/pixel-work/state", workspaceRoot: "/var/lib/pixel-work/workspaces",
    goalPath: "/etc/pixel-work/goal.json", jobsPath: "/etc/pixel-work/jobs.json", policyPath: "/etc/pixel-work/policy.json",
    objectStore: "/var/lib/pixel-work/objects", executorPath: "/opt/pixel-work/omp", controller: { maxTransitions: 1 },
    runtime: { dockerPath: "/usr/bin/docker" },
  };
}

async function fixture(t, name = "bundle") {
  const root = await mkdtemp(join(tmpdir(), "pixel-goal-service-lifecycle-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const bundle = join(root, name), systemd = join(root, "systemd");
  await mkdir(bundle, { mode: 0o700 });
  await mkdir(systemd, { mode: 0o755 });
  if (process.platform !== "win32") { await chmod(bundle, 0o700); await chmod(systemd, 0o755); }
  const units = renderGoalServiceUnits({ configPath: "/etc/pixel-work/controller.json", config: config(), goal: goal(), installRoot: "/opt/pixel" });
  const serviceBytes = Buffer.from(units.service), timerBytes = Buffer.from(units.timer), pathBytes = Buffer.from(units.path);
  const manifest = {
    schemaVersion: 1, operation: "pixel-work-goal-service-render", goalId: goal().goalId,
    goalSha256: digest("b"), configSha256: digest("c"), serviceName: units.serviceName,
    serviceSha256: sha(serviceBytes), timerName: units.timerName, timerSha256: sha(timerBytes),
    pathName: units.pathName, pathSha256: sha(pathBytes),
    authority: renderAuthority, boundary: renderBoundary,
  };
  const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`);
  for (const [filename, bytes] of [[units.serviceName, serviceBytes], [units.timerName, timerBytes], [units.pathName, pathBytes], ["service-bundle.json", manifestBytes]]) {
    await writeFile(join(bundle, filename), bytes, { mode: 0o600 });
    if (process.platform !== "win32") await chmod(join(bundle, filename), 0o600);
  }
  return { root, bundle, systemd, units, manifest, manifestSha256: sha(manifestBytes), uid: process.geteuid?.() ?? 0 };
}

function successfulRunner(calls) {
  const enabled = new Set(), active = new Set();
  let serviceStopped = false;
  return async (command, args) => {
    calls.push([command, ...args]);
    if (args[0] === "enable") for (const unit of args.slice(2)) { enabled.add(unit); active.add(unit); }
    if (args[0] === "disable") for (const unit of args.slice(2)) { enabled.delete(unit); active.delete(unit); }
    if (args[0] === "stop") serviceStopped = true;
    if (args[0] === "is-enabled") return { code: enabled.has(args.at(-1)) ? 0 : 1, stdout: "", stderr: "" };
    if (args[0] === "is-active") {
      const isActive = /\.(?:timer|path)$/u.test(String(args.at(-1))) ? active.has(args.at(-1)) : !serviceStopped;
      return { code: isActive ? 0 : 1, stdout: "", stderr: "" };
    }
    return { code: 0, stdout: "", stderr: "" };
  };
}

test("goal service lifecycle inspects, installs inactive, activates separately, and removes exact units", async (t) => {
  const value = await fixture(t);
  const inspected = await inspectGoalServiceBundle(value.bundle, { expectedOwnerUid: value.uid });
  assert.equal(inspected.manifestSha256, value.manifestSha256);
  const calls = [];
  const common = {
    bundlePath: value.bundle, confirmation: value.manifestSha256, systemdDirectory: value.systemd,
    expectedBundleUid: value.uid, expectedSystemUid: value.uid, commandRunner: successfulRunner(calls),
    systemdAnalyzePath: "/fixture/systemd-analyze", systemctlPath: "/fixture/systemctl",
    liveBindingVerifier,
  };
  await assert.rejects(installGoalServiceBundle({ ...common, confirmation: digest("e") }), /confirmation differs/);
  const installed = await installGoalServiceBundle(common);
  assert.equal(installed.state, "installed-inactive");
  assert.equal(installed.authority.schedulesCycles, false);
  assert.deepEqual(await readFile(join(value.systemd, value.units.serviceName)), Buffer.from(value.units.service));
  assert.deepEqual(await readFile(join(value.systemd, value.units.timerName)), Buffer.from(value.units.timer));
  assert.deepEqual(await readFile(join(value.systemd, value.units.pathName)), Buffer.from(value.units.path));
  if (process.platform !== "win32") assert.equal((await lstat(join(value.systemd, value.units.serviceName))).mode & 0o777, 0o644);
  assert.ok(calls.some((call) => call[0] === "/fixture/systemd-analyze" && call[1] === "verify" && call[2] === join(value.systemd, value.units.serviceName) && call[3] === join(value.systemd, value.units.timerName) && call[4] === join(value.systemd, value.units.pathName)));
  assert.ok(!calls.some((call) => call.includes("enable")));
  assert.equal((await installGoalServiceBundle(common)).state, "installed-inactive");

  const activated = await activateGoalServiceBundle(common);
  assert.equal(activated.state, "active");
  assert.equal(activated.authority.schedulesCycles, true);
  assert.ok(calls.some((call) => call.includes("enable") && call.includes("--now")));
  assert.ok(calls.some((call) => call[1] === "start" && call[2] === "--no-block" && call[3] === value.units.serviceName));

  const removed = await removeGoalServiceBundle(common);
  assert.equal(removed.state, "removed-private-state-retained");
  assert.equal(removed.authority.removesUnits, true);
  await assert.rejects(readFile(join(value.systemd, value.units.serviceName)));
  await assert.rejects(readFile(join(value.systemd, value.units.timerName)));
  await assert.rejects(readFile(join(value.systemd, value.units.pathName)));
  assert.equal((await removeGoalServiceBundle(common)).state, "removed-private-state-retained");
});

test("goal service lifecycle rejects linked, tampered, colliding, and failed-reload states", async (t) => {
  const linked = await fixture(t, "linked");
  await link(join(linked.bundle, linked.units.serviceName), join(linked.root, "extra-link"));
  await assert.rejects(inspectGoalServiceBundle(linked.bundle, { expectedOwnerUid: linked.uid }), /link count/);

  const tampered = await fixture(t, "tampered");
  await writeFile(join(tampered.bundle, tampered.units.timerName), "tampered\n", { mode: 0o600 });
  await assert.rejects(inspectGoalServiceBundle(tampered.bundle, { expectedOwnerUid: tampered.uid }), /differs from its manifest/);

  const staleBinding = await fixture(t, "stale-binding");
  let staleCommands = 0;
  await assert.rejects(installGoalServiceBundle({
    bundlePath: staleBinding.bundle, confirmation: staleBinding.manifestSha256, systemdDirectory: staleBinding.systemd,
    expectedBundleUid: staleBinding.uid, expectedSystemUid: staleBinding.uid,
    liveBindingVerifier: async () => { throw new Error("binding refused"); },
    commandRunner: async () => { staleCommands += 1; return { code: 0, stdout: "", stderr: "" }; },
  }), /binding refused/);
  assert.equal(staleCommands, 0);

  const collision = await fixture(t, "collision");
  await writeFile(join(collision.systemd, collision.units.serviceName), "different\n", { mode: 0o644 });
  await assert.rejects(installGoalServiceBundle({
    bundlePath: collision.bundle, confirmation: collision.manifestSha256, systemdDirectory: collision.systemd,
    expectedBundleUid: collision.uid, expectedSystemUid: collision.uid, commandRunner: successfulRunner([]),
    liveBindingVerifier,
  }), /different goal service/);

  const rollback = await fixture(t, "rollback");
  let reloads = 0;
  const failedRunner = async (_command, args) => {
    if (args[0] === "daemon-reload" && reloads++ === 0) return { code: 1, stdout: "", stderr: "failed" };
    return { code: 0, stdout: "", stderr: "" };
  };
  await assert.rejects(installGoalServiceBundle({
    bundlePath: rollback.bundle, confirmation: rollback.manifestSha256, systemdDirectory: rollback.systemd,
    expectedBundleUid: rollback.uid, expectedSystemUid: rollback.uid, commandRunner: failedRunner,
    liveBindingVerifier,
  }), /daemon reload/);
  await assert.rejects(readFile(join(rollback.systemd, rollback.units.serviceName)));
  await assert.rejects(readFile(join(rollback.systemd, rollback.units.timerName)));
  await assert.rejects(readFile(join(rollback.systemd, rollback.units.pathName)));
  assert.equal(reloads, 2);

  const removalRollback = await fixture(t, "removal-rollback");
  const installCalls = [];
  const lifecycle = {
    bundlePath: removalRollback.bundle, confirmation: removalRollback.manifestSha256,
    systemdDirectory: removalRollback.systemd, expectedBundleUid: removalRollback.uid,
    expectedSystemUid: removalRollback.uid, commandRunner: successfulRunner(installCalls),
    liveBindingVerifier,
  };
  await installGoalServiceBundle(lifecycle);
  let removalReloads = 0;
  const removalRunner = async (_command, args) => {
    if (args[0] === "is-active" || args[0] === "is-enabled") return { code: 1, stdout: "", stderr: "" };
    if (args[0] === "daemon-reload" && ++removalReloads === 2) return { code: 1, stdout: "", stderr: "failed" };
    return { code: 0, stdout: "", stderr: "" };
  };
  await assert.rejects(removeGoalServiceBundle({ ...lifecycle, commandRunner: removalRunner }), /removal daemon reload/);
  assert.deepEqual(await readFile(join(removalRollback.systemd, removalRollback.units.serviceName)), Buffer.from(removalRollback.units.service));
  assert.deepEqual(await readFile(join(removalRollback.systemd, removalRollback.units.timerName)), Buffer.from(removalRollback.units.timer));
  assert.deepEqual(await readFile(join(removalRollback.systemd, removalRollback.units.pathName)), Buffer.from(removalRollback.units.path));
  assert.equal(removalReloads, 3);

  const statusFailure = await fixture(t, "status-failure");
  const statusLifecycle = {
    bundlePath: statusFailure.bundle, confirmation: statusFailure.manifestSha256,
    systemdDirectory: statusFailure.systemd, expectedBundleUid: statusFailure.uid,
    expectedSystemUid: statusFailure.uid, commandRunner: successfulRunner([]),
    liveBindingVerifier,
  };
  await installGoalServiceBundle(statusLifecycle);
  const brokenStatusRunner = async (_command, args) => ({
    code: args[0] === "is-enabled" || args[0] === "is-active" ? 1 : 0,
    executionError: args[0] === "is-enabled" ? "process-failure" : null, stdout: "", stderr: "",
  });
  await assert.rejects(removeGoalServiceBundle({ ...statusLifecycle, commandRunner: brokenStatusRunner }), /disable verification failed closed/);
  assert.deepEqual(await readFile(join(statusFailure.systemd, statusFailure.units.serviceName)), Buffer.from(statusFailure.units.service));
  assert.deepEqual(await readFile(join(statusFailure.systemd, statusFailure.units.timerName)), Buffer.from(statusFailure.units.timer));
  assert.deepEqual(await readFile(join(statusFailure.systemd, statusFailure.units.pathName)), Buffer.from(statusFailure.units.path));
});

test("goal service lifecycle CLI separates inspection, root installation, activation, and removal", async (t) => {
  const value = await fixture(t, "cli");
  const inspection = await runGoalServiceLifecycleCommand(["inspect", "--bundle", value.bundle], { euid: value.uid, liveBindingVerifier });
  assert.equal(inspection.state, "bundle-verified-no-side-effects");
  assert.equal(inspection.executionModel, "durable-event-driven");
  assert.equal(inspection.watchdogRole, "liveness-only");
  assert.deepEqual(inspection.capability, { state: "disabled", jobCount: 0, packCount: 0, toolCount: 0 });
  assert.equal(inspection.authority.grantsInstall, false);
  assert.ok(!JSON.stringify(inspection).includes(value.root));
  await assert.rejects(
    runGoalServiceLifecycleCommand(["install", "--bundle", value.bundle, "--confirm-manifest-sha256", value.manifestSha256], { platform: "linux", euid: 1000 }),
    /require Linux root/,
  );
  await assert.rejects(
    runGoalServiceLifecycleCommand(["install", "--bundle", value.bundle, "--confirm-manifest-sha256", value.manifestSha256], {
      platform: "linux", euid: 0, identityRunner: async () => ({ code: 0, stdout: `${value.uid + 1}\n`, stderr: "" }),
    }),
    /owned by the configured unprivileged service identity/,
  );
  await assert.rejects(
    runGoalServiceLifecycleCommand(["inspect", "--bundle", value.bundle, "--confirm-manifest-sha256", value.manifestSha256], { euid: value.uid }),
    /arguments are invalid/,
  );
  const calls = [], commandRunner = successfulRunner(calls);
  const runtime = {
    platform: "linux", euid: 0, systemdDirectory: value.systemd,
    expectedSystemUid: value.uid, commandRunner, systemdAnalyzePath: "/fixture/systemd-analyze", systemctlPath: "/fixture/systemctl",
    liveBindingVerifier,
    identityRunner: async (command, args) => {
      assert.deepEqual([command, ...args], ["/usr/bin/id", "-u", "pixel-work"]);
      return { code: 0, stdout: `${value.uid}\n`, stderr: "" };
    },
  };
  const argumentsFor = (operation) => [operation, "--bundle", value.bundle, "--confirm-manifest-sha256", value.manifestSha256];
  assert.equal((await runGoalServiceLifecycleCommand(argumentsFor("install"), runtime)).state, "installed-inactive");
  assert.equal((await runGoalServiceLifecycleCommand(argumentsFor("activate"), runtime)).state, "active");
  assert.equal((await runGoalServiceLifecycleCommand(argumentsFor("remove"), runtime)).state, "removed-private-state-retained");
});

test("goal service lifecycle CLI expected-owner-uid is root-only and drives inspect ownership", async (t) => {
  const value = await fixture(t, "cli");
  // Non-root effective user may not pass the root-only expected-owner override.
  await assert.rejects(
    runGoalServiceLifecycleCommand(["inspect", "--bundle", value.bundle, "--expected-owner-uid", String(value.uid)], { euid: value.uid, liveBindingVerifier }),
    /requires root authority/,
  );
  // Root may inspect a bundle owned by the configured unprivileged service identity.
  const inspection = await runGoalServiceLifecycleCommand(
    ["inspect", "--bundle", value.bundle, "--expected-owner-uid", String(value.uid)],
    { euid: 0, liveBindingVerifier },
  );
  assert.equal(inspection.state, "bundle-verified-no-side-effects");
  // A root-only expected-owner that does not match the real bundle owner fails closed.
  await assert.rejects(
    runGoalServiceLifecycleCommand(["inspect", "--bundle", value.bundle, "--expected-owner-uid", String(value.uid + 1)], { euid: 0 }),
    /owner/,
  );
});
