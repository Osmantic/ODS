import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, readdir, unlink } from "node:fs/promises";
import { basename, join, posix, resolve } from "node:path";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { canonical } from "../../scripts/lib/work-contract.mjs";
import { verifyGoalCapabilityRuntimeReadiness } from "./goal-capability-runtime.mjs";
import { loadGoalCycleConfiguration } from "./goal-cycle-cli.mjs";
import { goalSha256 } from "./goals.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const GOAL_RE = /^workgoal-[0-9]{13}-[a-f0-9]{12}$/u;
const UNIT_RE = /^pixel-work-workgoal-[0-9]{13}-[a-f0-9]{12}\.(?:service|timer|path)$/u;
const ACCOUNT_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const MAX_UNIT_BYTES = 256 * 1024;
const renderBoundary = "Private atomic systemd render bundle only. Rendering grants no work execution, installation, service enablement, lease, external effect, or completion authority.";
const renderAuthority = Object.freeze({ grantsExecution: false, grantsInstall: false, grantsEnable: false, grantsLease: false, grantsExternalEffects: false });

export class GoalServiceLifecycleError extends Error {}

function fail(message) { throw new GoalServiceLifecycleError(message); }
function sha(value) { return createHash("sha256").update(value).digest("hex"); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) fail(`${label} shape is invalid`);
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
  if (
    details.nlink !== 1
    || process.platform !== "win32" && (details.uid !== expectedUid || (details.mode & (privateMode ? 0o077 : 0o022)) !== 0)
  ) fail(`${label} ownership, mode, or link count is invalid`);
  return bytes;
}

function checkedManifest(value) {
  exactKeys(value, [
    "schemaVersion", "operation", "goalId", "goalSha256", "configSha256", "serviceName", "serviceSha256",
    "timerName", "timerSha256", "pathName", "pathSha256", "authority", "boundary",
  ], "goal service manifest");
  exactKeys(value.authority, Object.keys(renderAuthority), "goal service manifest authority");
  if (
    value.schemaVersion !== 1 || value.operation !== "pixel-work-goal-service-render" || !GOAL_RE.test(value.goalId ?? "")
    || !SHA_RE.test(value.goalSha256 ?? "") || !SHA_RE.test(value.configSha256 ?? "")
    || value.serviceName !== `pixel-work-${value.goalId}.service` || value.timerName !== `pixel-work-${value.goalId}.timer`
    || value.pathName !== `pixel-work-${value.goalId}.path`
    || !UNIT_RE.test(value.serviceName) || !UNIT_RE.test(value.timerName) || !UNIT_RE.test(value.pathName)
    || !SHA_RE.test(value.serviceSha256 ?? "") || !SHA_RE.test(value.timerSha256 ?? "") || !SHA_RE.test(value.pathSha256 ?? "")
    || JSON.stringify(value.authority) !== JSON.stringify(renderAuthority) || value.boundary !== renderBoundary
  ) fail("goal service manifest contract is invalid");
  return value;
}

function exactUnitAccount(unit, directive) {
  const matches = [...unit.matchAll(new RegExp(`^${directive}=([^\\r\\n]+)$`, "gmu"))];
  if (matches.length !== 1 || !ACCOUNT_RE.test(matches[0][1]) || matches[0][1] === "root") fail(`goal service ${directive} identity is invalid`);
  return matches[0][1];
}

function exactUnitConfigPath(unit) {
  const matches = [...unit.matchAll(/^ExecStart=[^\r\n]+\/goal-continuous-cli\.mjs --config (\/[A-Za-z0-9._/-]+)$/gmu)];
  if (matches.length !== 1 || posix.normalize(matches[0][1]) !== matches[0][1]) fail("goal service live configuration path is invalid");
  return matches[0][1];
}

export async function inspectGoalServiceBundle(bundlePath, options = {}) {
  const expectedUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedUid) || expectedUid < 0) fail("goal service bundle owner is invalid");
  const root = await checkedDirectory(resolve(bundlePath), "goal service bundle", expectedUid, true);
  const manifestBytes = await readExactFile(join(root, "service-bundle.json"), MAX_UNIT_BYTES, "goal service manifest", expectedUid);
  let manifest;
  try { manifest = checkedManifest(JSON.parse(manifestBytes.toString("utf8"))); } catch (error) {
    if (error instanceof GoalServiceLifecycleError) throw error;
    fail("goal service manifest is not JSON");
  }
  const expectedNames = [manifest.serviceName, "service-bundle.json", manifest.timerName, manifest.pathName].sort();
  const names = (await readdir(root)).sort();
  if (JSON.stringify(names) !== JSON.stringify(expectedNames) || names.some((name) => basename(name) !== name)) fail("goal service bundle file set is invalid");
  const serviceBytes = await readExactFile(join(root, manifest.serviceName), MAX_UNIT_BYTES, "goal service unit", expectedUid);
  const timerBytes = await readExactFile(join(root, manifest.timerName), MAX_UNIT_BYTES, "goal service timer", expectedUid);
  const pathBytes = await readExactFile(join(root, manifest.pathName), MAX_UNIT_BYTES, "goal service path unit", expectedUid);
  if (sha(serviceBytes) !== manifest.serviceSha256 || sha(timerBytes) !== manifest.timerSha256 || sha(pathBytes) !== manifest.pathSha256) fail("goal service bundle content differs from its manifest");
  const service = serviceBytes.toString("utf8"), timer = timerBytes.toString("utf8"), path = pathBytes.toString("utf8");
  if (
    !service.includes("ExecStart=") || !service.includes("goal-continuous-cli.mjs --config ")
    || !service.includes("ExecStopPost=") || !service.includes("goal-cleanup-cli.mjs --config ")
    || !service.includes("NoNewPrivileges=true") || !service.includes("ProtectSystem=strict")
    || !service.includes("BindReadOnlyPaths=/run/docker.sock") || service.includes("EnvironmentFile=")
    || !timer.includes(`Unit=${manifest.serviceName}`) || !timer.includes("OnUnitInactiveSec=")
    || !path.includes(`Unit=${manifest.serviceName}`) || !path.includes("PathChanged=")
  ) fail("goal service bundle lost a required supervision boundary");
  return Object.freeze({
    root, manifest, manifestSha256: sha(manifestBytes),
    serviceUser: exactUnitAccount(service, "User"), serviceGroup: exactUnitAccount(service, "Group"),
    dockerGroup: exactUnitAccount(service, "SupplementaryGroups"), configPath: exactUnitConfigPath(service),
    service: Object.freeze({ path: join(root, manifest.serviceName), bytes: serviceBytes }),
    timer: Object.freeze({ path: join(root, manifest.timerName), bytes: timerBytes }),
    pathUnit: Object.freeze({ path: join(root, manifest.pathName), bytes: pathBytes }),
  });
}

export async function verifyGoalServiceLiveBinding(bundle, expectedOwnerUid) {
  let loaded;
  try { loaded = await loadGoalCycleConfiguration(bundle.configPath, { expectedOwnerUid, useKnowledgeCredentialSource: true }); }
  catch { fail("goal service live private contracts are unavailable or invalid"); }
  if (
    sha(canonical(loaded.config)) !== bundle.manifest.configSha256
    || goalSha256(loaded.goal) !== bundle.manifest.goalSha256
    || loaded.goal.goalId !== bundle.manifest.goalId
  ) fail("goal service live private contracts differ from the exact rendered manifest");
  let capability;
  try { capability = await verifyGoalCapabilityRuntimeReadiness({ config: loaded.config, jobs: loaded.jobs, policy: loaded.capabilityPolicy }); }
  catch { fail("goal service capability runtime is not release-ready"); }
  return Object.freeze({ configSha256: bundle.manifest.configSha256, goalSha256: bundle.manifest.goalSha256, capability });
}

export function execLifecycleCommand(command, args) {
  return new Promise((done) => {
    execFile(command, args, {
      cwd: "/", env: { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8" },
      encoding: "utf8", timeout: 300000, maxBuffer: 4 * 1024 * 1024, windowsHide: true, shell: false,
    }, (error, stdout, stderr) => done({
      code: typeof error?.code === "number" ? error.code : error ? 1 : 0,
      executionError: error && typeof error?.code !== "number" ? "process-failure" : null,
      stdout: stdout ?? "", stderr: stderr ?? "",
    }));
  });
}

function completed(result, label) {
  if (
    !result || !Number.isSafeInteger(result.code) || typeof result.stdout !== "string" || typeof result.stderr !== "string"
    || result.executionError !== undefined && result.executionError !== null
  ) fail(`${label} failed closed`);
  return result;
}

function succeeded(result, label) {
  completed(result, label);
  if (result.code !== 0) fail(`${label} failed closed`);
  return result;
}

function notSuccessful(result, label) {
  completed(result, label);
  if (result.code === 0) fail(`${label} failed closed`);
  return result;
}

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
  const targets = {
    service: join(directory, bundle.manifest.serviceName), timer: join(directory, bundle.manifest.timerName),
    pathUnit: join(directory, bundle.manifest.pathName),
  };
  const result = {};
  for (const [kind, path] of Object.entries(targets)) {
    const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (info === null) { result[kind] = "absent"; continue; }
    const bytes = await readExactFile(path, MAX_UNIT_BYTES, `installed goal service ${kind}`, expectedUid, false);
    result[kind] = Buffer.compare(bytes, bundle[kind].bytes) === 0 ? "exact" : "different";
  }
  return { directory, targets, ...result };
}

function confirmed(bundle, confirmation) {
  if (!SHA_RE.test(confirmation ?? "") || confirmation !== bundle.manifestSha256) fail("goal service lifecycle confirmation differs from the exact manifest");
}

function lifecycleReceipt(operation, bundle, state) {
  return {
    schemaVersion: 1, operation: `pixel-work-goal-service-${operation}`, goalId: bundle.manifest.goalId,
    manifestSha256: bundle.manifestSha256, serviceName: bundle.manifest.serviceName, timerName: bundle.manifest.timerName,
    pathName: bundle.manifest.pathName,
    state, authority: {
      grantsWorkLease: false, grantsScopeExpansion: false, grantsExternalEffects: false,
      schedulesCycles: operation === "activate", removesUnits: operation === "remove",
    },
    boundary: "Content-free exact-manifest service lifecycle receipt. Unit installation, activation, and removal never create work authority; activation only schedules separately lease-gated local cycles.",
  };
}

export async function installGoalServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid,
  expectedSystemUid = 0, commandRunner = execLifecycleCommand,
  liveBindingVerifier = verifyGoalServiceLiveBinding,
  systemdAnalyzePath = "/usr/bin/systemd-analyze", systemctlPath = "/usr/bin/systemctl",
} = {}) {
  const bundle = await inspectGoalServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid });
  confirmed(bundle, confirmation);
  if (typeof commandRunner !== "function" || typeof liveBindingVerifier !== "function") fail("goal service lifecycle dependency is invalid");
  await liveBindingVerifier(bundle, expectedBundleUid);
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if ([installed.service, installed.timer, installed.pathUnit].includes("different")) fail("a different goal service unit is already installed");
  const created = [];
  try {
    if (installed.service === "absent") { await writeInstalled(installed.targets.service, bundle.service.bytes); created.push(installed.targets.service); }
    if (installed.timer === "absent") { await writeInstalled(installed.targets.timer, bundle.timer.bytes); created.push(installed.targets.timer); }
    if (installed.pathUnit === "absent") { await writeInstalled(installed.targets.pathUnit, bundle.pathUnit.bytes); created.push(installed.targets.pathUnit); }
    await syncDirectory(installed.directory);
    // Verify the exact root-controlled destination bytes. Verifying user-owned
    // bundle paths before copying would leave a check/use race.
    succeeded(await commandRunner(systemdAnalyzePath, ["verify", installed.targets.service, installed.targets.timer, installed.targets.pathUnit]), "goal service systemd verification");
    succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "goal service daemon reload");
  } catch (error) {
    for (const path of created.reverse()) await unlink(path).catch(() => {});
    await syncDirectory(installed.directory).catch(() => {});
    await commandRunner(systemctlPath, ["daemon-reload"]).catch(() => {});
    throw error;
  }
  return lifecycleReceipt("install", bundle, "installed-inactive");
}

export async function activateGoalServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid,
  expectedSystemUid = 0, commandRunner = execLifecycleCommand, systemctlPath = "/usr/bin/systemctl",
  liveBindingVerifier = verifyGoalServiceLiveBinding,
} = {}) {
  const bundle = await inspectGoalServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid });
  confirmed(bundle, confirmation);
  if (typeof liveBindingVerifier !== "function") fail("goal service lifecycle dependency is invalid");
  await liveBindingVerifier(bundle, expectedBundleUid);
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if (installed.service !== "exact" || installed.timer !== "exact" || installed.pathUnit !== "exact") fail("exact goal service units are not installed");
  succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "goal service daemon reload");
  try {
    succeeded(await commandRunner(systemctlPath, ["enable", "--now", bundle.manifest.pathName, bundle.manifest.timerName]), "goal service event and watchdog activation");
    for (const unit of [bundle.manifest.pathName, bundle.manifest.timerName]) {
      succeeded(await commandRunner(systemctlPath, ["is-enabled", "--quiet", unit]), "goal service supervisor enable verification");
      succeeded(await commandRunner(systemctlPath, ["is-active", "--quiet", unit]), "goal service supervisor activity verification");
    }
    succeeded(await commandRunner(systemctlPath, ["start", "--no-block", bundle.manifest.serviceName]), "goal service immediate activation");
  } catch (error) {
    await commandRunner(systemctlPath, ["disable", "--now", bundle.manifest.pathName, bundle.manifest.timerName]).catch(() => {});
    await commandRunner(systemctlPath, ["stop", bundle.manifest.serviceName]).catch(() => {});
    throw error;
  }
  return lifecycleReceipt("activate", bundle, "active");
}

export async function removeGoalServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid,
  expectedSystemUid = 0, commandRunner = execLifecycleCommand, systemctlPath = "/usr/bin/systemctl",
} = {}) {
  const bundle = await inspectGoalServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid });
  confirmed(bundle, confirmation);
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if ([installed.service, installed.timer, installed.pathUnit].includes("different")) fail("refusing to remove a different installed goal service");
  if (installed.service === "absent" && installed.timer === "absent" && installed.pathUnit === "absent") return lifecycleReceipt("remove", bundle, "removed-private-state-retained");
  succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "goal service pre-removal daemon reload");
  for (const [kind, unit] of [["pathUnit", bundle.manifest.pathName], ["timer", bundle.manifest.timerName]]) {
    if (installed[kind] !== "exact") continue;
    succeeded(await commandRunner(systemctlPath, ["disable", "--now", unit]), "goal service supervisor disable");
    notSuccessful(await commandRunner(systemctlPath, ["is-enabled", "--quiet", unit]), "goal service supervisor disable verification");
    notSuccessful(await commandRunner(systemctlPath, ["is-active", "--quiet", unit]), "goal service supervisor inactivity verification");
  }
  if (installed.service === "exact") {
    succeeded(await commandRunner(systemctlPath, ["stop", bundle.manifest.serviceName]), "goal service stop and cleanup");
    notSuccessful(await commandRunner(systemctlPath, ["is-active", "--quiet", bundle.manifest.serviceName]), "goal service stop and cleanup verification");
  }
  const removed = [];
  try {
    for (const [kind, path] of Object.entries(installed.targets)) {
      if (installed[kind] !== "exact") continue;
      await unlink(path);
      removed.push([path, bundle[kind].bytes]);
    }
    await syncDirectory(installed.directory);
    succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "goal service removal daemon reload");
  } catch (error) {
    // Restore the exact, already-inspected unit bytes but leave event and watchdog units disabled. A
    // failed removal must never create a half-present unit pair or restart work.
    for (const [path, bytes] of removed.reverse()) await writeInstalled(path, bytes);
    await syncDirectory(installed.directory).catch(() => {});
    await commandRunner(systemctlPath, ["daemon-reload"]).catch(() => {});
    throw error;
  }
  return lifecycleReceipt("remove", bundle, "removed-private-state-retained");
}
