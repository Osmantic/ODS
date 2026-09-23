import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, readdir, unlink } from "node:fs/promises";
import { basename, join, posix, resolve } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { loadGoalFleetCycleConfiguration } from "./goal-fleet-cycle-cli.mjs";
import { goalFleetSha256, recoverGoalFleetLedger } from "./goal-fleet.mjs";
import { recoverGoalLedger } from "./goals.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const FLEET_RE = /^workfleet-[0-9]{13}-[a-f0-9]{12}$/u;
const UNIT_RE = /^pixel-work-workfleet-[0-9]{13}-[a-f0-9]{12}\.(?:service|timer)$/u;
const LEGACY_RE = /^pixel-work-workgoal-[0-9]{13}-[a-f0-9]{12}\.(?:service|timer)$/u;
const ACCOUNT_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const MAX_UNIT_BYTES = 512 * 1024;
const renderBoundary = "Private atomic fleet-systemd render bundle only. Rendering grants no work execution, installation, service enablement, lease, external effect, or completion authority.";
const renderAuthority = Object.freeze({ grantsExecution: false, grantsInstall: false, grantsEnable: false, grantsLease: false, grantsExternalEffects: false });

export class GoalFleetServiceLifecycleError extends Error {}

function fail(message) { throw new GoalFleetServiceLifecycleError(message); }
function sha(value) { return createHash("sha256").update(value).digest("hex"); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

async function checkedDirectory(path, label, expectedUid, privateMode) {
  const absolute = resolve(path);
  if (absolute !== path) fail(`${label} is not an absolute normalized path`);
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== expectedUid || (info.mode & (privateMode ? 0o077 : 0o022)) !== 0)) fail(`${label} ownership or mode is invalid`);
  return absolute;
}

async function readExactFile(path, maximum, label, expectedUid, privateMode = true) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedUid || (details.mode & (privateMode ? 0o077 : 0o022)) !== 0)) fail(`${label} ownership, mode, or link count is invalid`);
  return bytes;
}

function checkedManifest(value) {
  exactKeys(value, [
    "schemaVersion", "operation", "fleetId", "fleetSha256", "configSha256", "serviceName", "serviceSha256",
    "timerName", "timerSha256", "legacyUnitNames", "authority", "boundary",
  ], "fleet service manifest");
  exactKeys(value.authority, Object.keys(renderAuthority), "fleet service manifest authority");
  if (
    value.schemaVersion !== 1 || value.operation !== "pixel-work-goal-fleet-service-render" || !FLEET_RE.test(value.fleetId ?? "")
    || !SHA_RE.test(value.fleetSha256 ?? "") || !SHA_RE.test(value.configSha256 ?? "")
    || value.serviceName !== `pixel-work-${value.fleetId}.service` || value.timerName !== `pixel-work-${value.fleetId}.timer`
    || !UNIT_RE.test(value.serviceName) || !UNIT_RE.test(value.timerName) || !SHA_RE.test(value.serviceSha256 ?? "") || !SHA_RE.test(value.timerSha256 ?? "")
    || !Array.isArray(value.legacyUnitNames) || value.legacyUnitNames.length < 2 || value.legacyUnitNames.length > 128
    || new Set(value.legacyUnitNames).size !== value.legacyUnitNames.length || value.legacyUnitNames.some((name) => !LEGACY_RE.test(name))
    || canonical(value.authority) !== canonical(renderAuthority) || value.boundary !== renderBoundary
  ) fail("fleet service manifest contract is invalid");
  return value;
}

function exactUnitAccount(unit, directive) {
  const matches = [...unit.matchAll(new RegExp(`^${directive}=([^\\r\\n]+)$`, "gmu"))];
  if (matches.length !== 1 || !ACCOUNT_RE.test(matches[0][1]) || matches[0][1] === "root") fail(`fleet service ${directive} identity is invalid`);
  return matches[0][1];
}

function exactUnitConfigPath(unit) {
  const matches = [...unit.matchAll(/^ExecStart=[^\r\n]+\/goal-fleet-supervised-cycle-cli\.mjs --config (\/[A-Za-z0-9._/-]+)$/gmu)];
  if (matches.length !== 1 || posix.normalize(matches[0][1]) !== matches[0][1]) fail("fleet service live configuration path is invalid");
  return matches[0][1];
}

export async function inspectGoalFleetServiceBundle(bundlePath, options = {}) {
  const expectedUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedUid) || expectedUid < 0) fail("fleet service bundle owner is invalid");
  const root = await checkedDirectory(resolve(bundlePath), "fleet service bundle", expectedUid, true);
  const manifestBytes = await readExactFile(join(root, "fleet-service-bundle.json"), MAX_UNIT_BYTES, "fleet service manifest", expectedUid);
  let manifest;
  try { manifest = checkedManifest(JSON.parse(manifestBytes.toString("utf8"))); } catch (error) {
    if (error instanceof GoalFleetServiceLifecycleError) throw error;
    fail("fleet service manifest is not JSON");
  }
  const expectedNames = [manifest.serviceName, "fleet-service-bundle.json", manifest.timerName].sort();
  const names = (await readdir(root)).sort();
  if (canonical(names) !== canonical(expectedNames) || names.some((name) => basename(name) !== name)) fail("fleet service bundle file set is invalid");
  const serviceBytes = await readExactFile(join(root, manifest.serviceName), MAX_UNIT_BYTES, "fleet service unit", expectedUid);
  const timerBytes = await readExactFile(join(root, manifest.timerName), MAX_UNIT_BYTES, "fleet service timer", expectedUid);
  if (sha(serviceBytes) !== manifest.serviceSha256 || sha(timerBytes) !== manifest.timerSha256) fail("fleet service bundle content differs from its manifest");
  const service = serviceBytes.toString("utf8"), timer = timerBytes.toString("utf8");
  if (
    !service.includes("goal-fleet-supervised-cycle-cli.mjs --config ") || !service.includes("goal-fleet-cleanup-cli.mjs --config ")
    || !service.includes("NoNewPrivileges=true") || !service.includes("ProtectSystem=strict")
    || !service.includes("BindReadOnlyPaths=/run/docker.sock") || service.includes("EnvironmentFile=")
    || !timer.includes(`Unit=${manifest.serviceName}`) || !timer.includes("OnUnitInactiveSec=")
    || manifest.legacyUnitNames.some((name) => !service.includes(`ConditionPathExists=!/etc/systemd/system/${name}`))
  ) fail("fleet service bundle lost a required supervision boundary");
  return Object.freeze({
    root, manifest, manifestSha256: sha(manifestBytes), serviceUser: exactUnitAccount(service, "User"),
    serviceGroup: exactUnitAccount(service, "Group"), dockerGroup: exactUnitAccount(service, "SupplementaryGroups"),
    configPath: exactUnitConfigPath(service), service: Object.freeze({ path: join(root, manifest.serviceName), bytes: serviceBytes }),
    timer: Object.freeze({ path: join(root, manifest.timerName), bytes: timerBytes }),
  });
}

export async function verifyGoalFleetServiceLiveBinding(bundle, expectedOwnerUid) {
  let loaded;
  try { loaded = await loadGoalFleetCycleConfiguration(bundle.configPath, { expectedOwnerUid }); }
  catch { fail("fleet service live private contracts are unavailable or invalid"); }
  if (
    sha(Buffer.from(canonical(loaded.config))) !== bundle.manifest.configSha256
    || goalFleetSha256(loaded.fleet) !== bundle.manifest.fleetSha256 || loaded.fleet.fleetId !== bundle.manifest.fleetId
  ) fail("fleet service live private contracts differ from the exact rendered manifest");
  const expectedLegacyUnits = loaded.fleet.goals.flatMap((goal) => [`pixel-work-${goal.goalId}.service`, `pixel-work-${goal.goalId}.timer`]);
  if (canonical(expectedLegacyUnits) !== canonical(bundle.manifest.legacyUnitNames)) fail("fleet service legacy exclusions differ from the exact registered goals");
  const registrations = loaded.entries.map(({ goal, jobs, config }) => ({ goal, jobs, config }));
  try {
    await recoverGoalFleetLedger({ stateRoot: loaded.config.stateRoot, fleet: loaded.fleet, registrations });
    for (const entry of loaded.entries) await recoverGoalLedger({ stateRoot: loaded.config.stateRoot, goal: entry.goal, jobs: entry.jobs });
  } catch { fail("fleet service durable goal or fleet ledgers are unavailable or invalid"); }
  return Object.freeze({ configSha256: bundle.manifest.configSha256, fleetSha256: bundle.manifest.fleetSha256 });
}

export function execFleetLifecycleCommand(command, args) {
  return new Promise((done) => {
    execFile(command, args, {
      cwd: "/", env: { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8" }, encoding: "utf8",
      timeout: 300000, maxBuffer: 4 * 1024 * 1024, windowsHide: true, shell: false,
    }, (error, stdout, stderr) => done({
      code: typeof error?.code === "number" ? error.code : error ? 1 : 0,
      executionError: error && typeof error?.code !== "number" ? "process-failure" : null,
      stdout: stdout ?? "", stderr: stderr ?? "",
    }));
  });
}

function completed(result, label) {
  if (!result || !Number.isSafeInteger(result.code) || typeof result.stdout !== "string" || typeof result.stderr !== "string" || result.executionError !== undefined && result.executionError !== null) fail(`${label} failed closed`);
  return result;
}
function succeeded(result, label) { completed(result, label); if (result.code !== 0) fail(`${label} failed closed`); return result; }
function notSuccessful(result, label) { completed(result, label); if (result.code === 0) fail(`${label} failed closed`); return result; }

async function writeInstalled(path, bytes) {
  const handle = await open(path, "wx", 0o644);
  try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
}
async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

async function installedState(bundle, systemdDirectory, expectedUid) {
  const directory = await checkedDirectory(resolve(systemdDirectory), "systemd unit directory", expectedUid, false);
  const targets = { service: join(directory, bundle.manifest.serviceName), timer: join(directory, bundle.manifest.timerName) };
  const result = {};
  for (const [kind, path] of Object.entries(targets)) {
    const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (info === null) { result[kind] = "absent"; continue; }
    const bytes = await readExactFile(path, MAX_UNIT_BYTES, `installed fleet service ${kind}`, expectedUid, false);
    result[kind] = Buffer.compare(bytes, bundle[kind].bytes) === 0 ? "exact" : "different";
  }
  return { directory, targets, ...result };
}

async function rejectLegacyUnits(bundle, directory) {
  for (const name of bundle.manifest.legacyUnitNames) {
    if (await lstat(join(directory, name)).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("a legacy per-goal service unit must be removed before fleet activation");
  }
}

function confirmed(bundle, confirmation) {
  if (!SHA_RE.test(confirmation ?? "") || confirmation !== bundle.manifestSha256) fail("fleet service lifecycle confirmation differs from the exact manifest");
}
function lifecycleReceipt(operation, bundle, state) {
  return {
    schemaVersion: 1, operation: `pixel-work-goal-fleet-service-${operation}`, fleetId: bundle.manifest.fleetId,
    manifestSha256: bundle.manifestSha256, serviceName: bundle.manifest.serviceName, timerName: bundle.manifest.timerName,
    state, authority: { grantsWorkLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, schedulesCycles: operation === "activate", removesUnits: operation === "remove" },
    boundary: "Content-free exact-manifest fleet-service lifecycle receipt. Installation, activation, and removal create no work authority; activation only schedules serialized, separately lease-gated local cycles.",
  };
}

export async function installGoalFleetServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid, expectedSystemUid = 0,
  commandRunner = execFleetLifecycleCommand, liveBindingVerifier = verifyGoalFleetServiceLiveBinding,
  systemdAnalyzePath = "/usr/bin/systemd-analyze", systemctlPath = "/usr/bin/systemctl",
} = {}) {
  const bundle = await inspectGoalFleetServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid });
  confirmed(bundle, confirmation);
  if (typeof commandRunner !== "function" || typeof liveBindingVerifier !== "function") fail("fleet service lifecycle dependency is invalid");
  await liveBindingVerifier(bundle, expectedBundleUid);
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if ([installed.service, installed.timer].includes("different")) fail("a different fleet service unit is already installed");
  const created = [];
  try {
    if (installed.service === "absent") { await writeInstalled(installed.targets.service, bundle.service.bytes); created.push(installed.targets.service); }
    if (installed.timer === "absent") { await writeInstalled(installed.targets.timer, bundle.timer.bytes); created.push(installed.targets.timer); }
    await syncDirectory(installed.directory);
    succeeded(await commandRunner(systemdAnalyzePath, ["verify", installed.targets.service, installed.targets.timer]), "fleet service systemd verification");
    succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "fleet service daemon reload");
  } catch (error) {
    for (const path of created.reverse()) await unlink(path).catch(() => {});
    await syncDirectory(installed.directory).catch(() => {});
    await commandRunner(systemctlPath, ["daemon-reload"]).catch(() => {});
    throw error;
  }
  return lifecycleReceipt("install", bundle, "installed-inactive");
}

export async function activateGoalFleetServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid, expectedSystemUid = 0,
  commandRunner = execFleetLifecycleCommand, systemctlPath = "/usr/bin/systemctl", liveBindingVerifier = verifyGoalFleetServiceLiveBinding,
} = {}) {
  const bundle = await inspectGoalFleetServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid });
  confirmed(bundle, confirmation);
  if (typeof liveBindingVerifier !== "function") fail("fleet service lifecycle dependency is invalid");
  await liveBindingVerifier(bundle, expectedBundleUid);
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if (installed.service !== "exact" || installed.timer !== "exact") fail("exact fleet service units are not installed");
  await rejectLegacyUnits(bundle, installed.directory);
  succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "fleet service daemon reload");
  try {
    succeeded(await commandRunner(systemctlPath, ["enable", "--now", bundle.manifest.timerName]), "fleet service timer activation");
    succeeded(await commandRunner(systemctlPath, ["is-enabled", "--quiet", bundle.manifest.timerName]), "fleet service timer enable verification");
    succeeded(await commandRunner(systemctlPath, ["is-active", "--quiet", bundle.manifest.timerName]), "fleet service timer activity verification");
  } catch (error) {
    await commandRunner(systemctlPath, ["disable", "--now", bundle.manifest.timerName]).catch(() => {});
    throw error;
  }
  return lifecycleReceipt("activate", bundle, "active");
}

export async function removeGoalFleetServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid, expectedSystemUid = 0,
  commandRunner = execFleetLifecycleCommand, systemctlPath = "/usr/bin/systemctl",
} = {}) {
  const bundle = await inspectGoalFleetServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid });
  confirmed(bundle, confirmation);
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if ([installed.service, installed.timer].includes("different")) fail("refusing to remove a different installed fleet service");
  if (installed.service === "absent" && installed.timer === "absent") return lifecycleReceipt("remove", bundle, "removed-private-state-retained");
  succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "fleet service pre-removal daemon reload");
  if (installed.timer === "exact") {
    succeeded(await commandRunner(systemctlPath, ["disable", "--now", bundle.manifest.timerName]), "fleet service timer disable");
    notSuccessful(await commandRunner(systemctlPath, ["is-enabled", "--quiet", bundle.manifest.timerName]), "fleet service timer disable verification");
    notSuccessful(await commandRunner(systemctlPath, ["is-active", "--quiet", bundle.manifest.timerName]), "fleet service timer inactivity verification");
  }
  if (installed.service === "exact") {
    succeeded(await commandRunner(systemctlPath, ["stop", bundle.manifest.serviceName]), "fleet service stop and cleanup");
    notSuccessful(await commandRunner(systemctlPath, ["is-active", "--quiet", bundle.manifest.serviceName]), "fleet service stop and cleanup verification");
  }
  const removed = [];
  try {
    for (const [kind, path] of Object.entries(installed.targets)) {
      if (installed[kind] !== "exact") continue;
      await unlink(path); removed.push([path, bundle[kind].bytes]);
    }
    await syncDirectory(installed.directory);
    succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "fleet service removal daemon reload");
  } catch (error) {
    for (const [path, bytes] of removed.reverse()) await writeInstalled(path, bytes);
    await syncDirectory(installed.directory).catch(() => {});
    await commandRunner(systemctlPath, ["daemon-reload"]).catch(() => {});
    throw error;
  }
  return lifecycleReceipt("remove", bundle, "removed-private-state-retained");
}
