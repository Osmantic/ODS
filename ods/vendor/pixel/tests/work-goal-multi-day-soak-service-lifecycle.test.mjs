import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, link, lstat, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  activateDeepWorkSoakServiceBundle, deepWorkSoakServiceRenderAuthority, deepWorkSoakServiceRenderBoundary,
  inspectDeepWorkSoakServiceBundle, installDeepWorkSoakServiceBundle, removeDeepWorkSoakServiceBundle,
} from "../deploy/work-controller/deep-work-soak-service-lifecycle.mjs";
import { runDeepWorkSoakServiceCommand } from "../deploy/work-controller/deep-work-soak-service-cli.mjs";
import { renderDeepWorkSoakServiceUnits } from "../deploy/work-controller/deep-work-soak-service-units.mjs";

const sha = (value) => createHash("sha256").update(value).digest("hex");
const digest = (character) => character.repeat(64);

function config() {
  const root = "/var/lib/pixel-deep-work/campaign";
  return {
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak", campaignId: "deepworksoak-1786366800001-abcdef123456",
    schedule: { intervalSeconds: 3600, minimumGapSeconds: 3300, maximumGapSeconds: 7200, minimumElapsedSeconds: 172800, milestones: 24, expectedCycles: 49 },
    paths: { root, stateRoot: `${root}/state`, objectStore: `${root}/objects`, goalPath: `${root}/contracts/goal.json`, jobsPath: `${root}/contracts/jobs.json`, policyPath: `${root}/contracts/policy.json`, invocationLedgerRoot: `${root}/invocations` },
  };
}

async function fixture(t, name = "bundle") {
  const root = await mkdtemp(join(tmpdir(), "pixel-soak-service-lifecycle-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const bundle = join(root, name), systemd = join(root, "systemd");
  await mkdir(bundle, { mode: 0o700 }); await mkdir(systemd, { mode: 0o755 });
  if (process.platform !== "win32") { await chmod(bundle, 0o700); await chmod(systemd, 0o755); }
  const units = renderDeepWorkSoakServiceUnits({ configPath: `${config().paths.root}/campaign.json`, config: config(), configSha256: digest("a"), installRoot: "/opt/pixel" });
  const serviceBytes = Buffer.from(units.service), timerBytes = Buffer.from(units.timer);
  const manifest = {
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak-service-render", campaignId: config().campaignId,
    configSha256: digest("a"), sourceCommit: "b".repeat(40), sourceTree: "c".repeat(40), programSha256: digest("d"), runtimeSnapshotSha256: digest("e"),
    serviceName: units.serviceName, serviceSha256: sha(serviceBytes), timerName: units.timerName, timerSha256: sha(timerBytes),
    authority: { ...deepWorkSoakServiceRenderAuthority }, boundary: deepWorkSoakServiceRenderBoundary,
  };
  const manifestBytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`);
  for (const [filename, bytes] of [[units.serviceName, serviceBytes], [units.timerName, timerBytes], ["service-bundle.json", manifestBytes]]) {
    await writeFile(join(bundle, filename), bytes, { mode: 0o600 }); if (process.platform !== "win32") await chmod(join(bundle, filename), 0o600);
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

const liveBindingVerifier = async () => Object.freeze({ campaignId: config().campaignId, configSha256: digest("a") });

test("multi-day soak service lifecycle separates inspection, installation, activation, and exact removal", async (t) => {
  const value = await fixture(t), inspected = await inspectDeepWorkSoakServiceBundle(value.bundle, { expectedOwnerUid: value.uid });
  assert.equal(inspected.manifestSha256, value.manifestSha256);
  assert.equal(inspected.serviceUser, "pixel-work");
  const calls = [], common = {
    bundlePath: value.bundle, confirmation: value.manifestSha256, systemdDirectory: value.systemd,
    expectedBundleUid: value.uid, expectedSystemUid: value.uid, commandRunner: successfulRunner(calls),
    systemdAnalyzePath: "/fixture/systemd-analyze", systemctlPath: "/fixture/systemctl", liveBindingVerifier,
  };
  await assert.rejects(installDeepWorkSoakServiceBundle({ ...common, confirmation: digest("f") }), /confirmation differs/u);
  const installed = await installDeepWorkSoakServiceBundle(common);
  assert.equal(installed.state, "installed-inactive"); assert.equal(installed.authority.schedulesQualificationCycles, false);
  assert.deepEqual(await readFile(join(value.systemd, value.units.serviceName)), Buffer.from(value.units.service));
  assert.deepEqual(await readFile(join(value.systemd, value.units.timerName)), Buffer.from(value.units.timer));
  if (process.platform !== "win32") assert.equal((await lstat(join(value.systemd, value.units.serviceName))).mode & 0o777, 0o644);
  assert.ok(calls.some((call) => call[0] === "/fixture/systemd-analyze" && call[1] === "verify"));
  assert.ok(!calls.some((call) => call.includes("enable")));
  assert.equal((await installDeepWorkSoakServiceBundle(common)).state, "installed-inactive");
  const activated = await activateDeepWorkSoakServiceBundle(common);
  assert.equal(activated.state, "active"); assert.equal(activated.authority.schedulesQualificationCycles, true);
  const removed = await removeDeepWorkSoakServiceBundle(common);
  assert.equal(removed.state, "removed-private-campaign-retained"); assert.equal(removed.authority.removesUnits, true);
  await assert.rejects(readFile(join(value.systemd, value.units.serviceName))); await assert.rejects(readFile(join(value.systemd, value.units.timerName)));
  assert.equal((await removeDeepWorkSoakServiceBundle(common)).state, "removed-private-campaign-retained");
});

test("multi-day soak lifecycle rejects links, tampering, collisions, reload failure, stale owner, and non-root mutation", async (t) => {
  const linked = await fixture(t, "linked");
  await link(join(linked.bundle, linked.units.serviceName), join(linked.root, "extra-link"));
  await assert.rejects(inspectDeepWorkSoakServiceBundle(linked.bundle, { expectedOwnerUid: linked.uid }), /link count/u);
  const tampered = await fixture(t, "tampered");
  await writeFile(join(tampered.bundle, tampered.units.timerName), "tampered\n", { mode: 0o600 });
  await assert.rejects(inspectDeepWorkSoakServiceBundle(tampered.bundle, { expectedOwnerUid: tampered.uid }), /differs from its manifest/u);
  const collision = await fixture(t, "collision");
  await writeFile(join(collision.systemd, collision.units.serviceName), "different\n", { mode: 0o644 });
  await assert.rejects(installDeepWorkSoakServiceBundle({
    bundlePath: collision.bundle, confirmation: collision.manifestSha256, systemdDirectory: collision.systemd,
    expectedBundleUid: collision.uid, expectedSystemUid: collision.uid, commandRunner: successfulRunner([]), liveBindingVerifier,
  }), /different multi-day soak/u);
  const rollback = await fixture(t, "rollback"); let reloads = 0;
  const failedRunner = async (_command, args) => args[0] === "daemon-reload" && reloads++ === 0 ? { code: 1, stdout: "", stderr: "failed" } : { code: 0, stdout: "", stderr: "" };
  await assert.rejects(installDeepWorkSoakServiceBundle({
    bundlePath: rollback.bundle, confirmation: rollback.manifestSha256, systemdDirectory: rollback.systemd,
    expectedBundleUid: rollback.uid, expectedSystemUid: rollback.uid, commandRunner: failedRunner, liveBindingVerifier,
  }), /daemon reload/u);
  await assert.rejects(readFile(join(rollback.systemd, rollback.units.serviceName))); await assert.rejects(readFile(join(rollback.systemd, rollback.units.timerName)));
  assert.equal(reloads, 2);

  const cli = await fixture(t, "cli");
  const inspection = await runDeepWorkSoakServiceCommand(["inspect", "--bundle", cli.bundle], { euid: cli.uid, liveBindingVerifier });
  assert.equal(inspection.state, "bundle-verified-no-side-effects");
  await assert.rejects(runDeepWorkSoakServiceCommand(
    ["inspect", "--bundle", cli.bundle, "--expected-owner-uid", String(cli.uid)],
    { euid: cli.uid, liveBindingVerifier },
  ), /requires root authority/u);
  const rootInspection = await runDeepWorkSoakServiceCommand(
    ["inspect", "--bundle", cli.bundle, "--expected-owner-uid", String(cli.uid)],
    { euid: 0, liveBindingVerifier },
  );
  assert.equal(rootInspection.state, "bundle-verified-no-side-effects");
  await assert.rejects(runDeepWorkSoakServiceCommand(["install", "--bundle", cli.bundle, "--confirm-manifest-sha256", cli.manifestSha256], { platform: "linux", euid: 1000 }), /require Linux root/u);
  await assert.rejects(runDeepWorkSoakServiceCommand(["install", "--bundle", cli.bundle, "--confirm-manifest-sha256", cli.manifestSha256], {
    platform: "linux", euid: 0, identityRunner: async () => ({ code: 0, stdout: `${cli.uid + 1}\n`, stderr: "" }),
  }), /owned by its configured/u);
  let verifiedOwnerUid = null;
  const installed = await runDeepWorkSoakServiceCommand(["install", "--bundle", cli.bundle, "--confirm-manifest-sha256", cli.manifestSha256], {
    platform: "linux", euid: 0, identityRunner: async () => ({ code: 0, stdout: `${cli.uid}\n`, stderr: "" }),
    systemdDirectory: cli.systemd, expectedSystemUid: cli.uid, commandRunner: successfulRunner([]),
    systemdAnalyzePath: "/fixture/systemd-analyze", systemctlPath: "/fixture/systemctl",
    liveBindingVerifier: async (_bundle, options) => { verifiedOwnerUid = options.expectedOwnerUid; return liveBindingVerifier(); },
  });
  assert.equal(installed.state, "installed-inactive");
  assert.equal(verifiedOwnerUid, cli.uid);
});
