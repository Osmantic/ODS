import { execFile } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, rename, rm, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, posix, relative, resolve, sep } from "node:path";

import { loadCampaign } from "../../scripts/deep-work-multi-day-soak.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { renderDeepWorkSoakServiceUnits } from "./deep-work-soak-service-units.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const CAMPAIGN_RE = /^deepworksoak-[0-9]{13}-[a-f0-9]{12}$/u;
const UNIT_RE = /^pixel-deep-work-deepworksoak-[0-9]{13}-[a-f0-9]{12}\.(?:service|timer)$/u;
const ACCOUNT_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const MAX_UNIT_BYTES = 256 * 1024;
const renderBoundary = "Private atomic multi-day qualification systemd bundle only. Rendering grants no installation, scheduling, work, lease, provider, network, credential, external effect, deployment, publication, or release authority.";
const renderAuthority = Object.freeze({ grantsInstall: false, grantsScheduling: false, grantsWork: false, grantsLease: false, grantsExternalEffects: false, grantsRelease: false });

export class DeepWorkSoakServiceLifecycleError extends Error {}

function fail(message) { throw new DeepWorkSoakServiceLifecycleError(message); }
function sha(value) { return createHash("sha256").update(value).digest("hex"); }
function within(path, root) { const difference = relative(root, path); return difference === "" || difference !== ".." && !difference.startsWith(`..${sep}`) && !isAbsolute(difference); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) fail(`${label} shape is invalid`);
}
async function checkedDirectory(path, label, expectedUid, privateMode, create = false) {
  if (resolve(path) !== path) fail(`${label} is not an absolute normalized path`);
  if (create) await mkdir(path, { mode: privateMode ? 0o700 : 0o755 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== expectedUid || (info.mode & (privateMode ? 0o077 : 0o022)) !== 0)) fail(`${label} ownership or mode is invalid`);
  return path;
}
async function readExactFile(path, maximum, label, expectedUid, privateMode = true) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedUid || (details.mode & (privateMode ? 0o077 : 0o022)) !== 0)) fail(`${label} ownership, mode, or link count is invalid`);
  return bytes;
}
async function writeFileExclusive(path, bytes, mode) {
  const handle = await open(path, "wx", mode);
  try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
}
async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}
function checkedManifest(value) {
  exactKeys(value, [
    "schemaVersion", "operation", "campaignId", "configSha256", "sourceCommit", "sourceTree", "programSha256", "runtimeSnapshotSha256",
    "serviceName", "serviceSha256", "timerName", "timerSha256", "authority", "boundary",
  ], "multi-day soak service manifest");
  exactKeys(value.authority, Object.keys(renderAuthority), "multi-day soak service manifest authority");
  if (
    value.schemaVersion !== 1 || value.operation !== "pixel-deep-work-multi-day-soak-service-render"
    || !CAMPAIGN_RE.test(value.campaignId ?? "") || !SHA_RE.test(value.configSha256 ?? "")
    || !/^[a-f0-9]{40}$/u.test(value.sourceCommit ?? "") || !/^[a-f0-9]{40}$/u.test(value.sourceTree ?? "")
    || !SHA_RE.test(value.programSha256 ?? "") || !SHA_RE.test(value.runtimeSnapshotSha256 ?? "")
    || value.serviceName !== `pixel-deep-work-${value.campaignId}.service` || value.timerName !== `pixel-deep-work-${value.campaignId}.timer`
    || !UNIT_RE.test(value.serviceName) || !UNIT_RE.test(value.timerName)
    || !SHA_RE.test(value.serviceSha256 ?? "") || !SHA_RE.test(value.timerSha256 ?? "")
    || JSON.stringify(value.authority) !== JSON.stringify(renderAuthority) || value.boundary !== renderBoundary
  ) fail("multi-day soak service manifest contract is invalid");
  return value;
}
function exactUnitAccount(unit, directive) {
  const matches = [...unit.matchAll(new RegExp(`^${directive}=([^\\r\\n]+)$`, "gmu"))];
  if (matches.length !== 1 || !ACCOUNT_RE.test(matches[0][1]) || matches[0][1] === "root") fail(`multi-day soak service ${directive} identity is invalid`);
  return matches[0][1];
}
function exactUnitConfigBinding(unit) {
  const matches = [...unit.matchAll(/^ExecStart=[^\r\n]+\/deep-work-multi-day-soak\.mjs cycle --config (\/[A-Za-z0-9._/-]+) --confirm-config-sha256 ([a-f0-9]{64})$/gmu)];
  if (matches.length !== 1 || posix.normalize(matches[0][1]) !== matches[0][1]) fail("multi-day soak service live configuration binding is invalid");
  return { path: matches[0][1], sha256: matches[0][2] };
}

export async function renderDeepWorkSoakServiceBundle({
  configPath, outputPath, installRoot, expectedOwnerUid = process.geteuid?.() ?? 0,
  serviceUser = "pixel-work", serviceGroup = "pixel-work", nodePath = "/usr/bin/node", flockPath = "/usr/bin/flock", envPath = "/usr/bin/env",
} = {}) {
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0 || resolve(configPath) !== configPath || resolve(outputPath) !== outputPath || !/^[A-Za-z0-9._-]{1,128}$/u.test(basename(outputPath))) fail("multi-day soak service rendering arguments are invalid");
  await checkedDirectory(dirname(outputPath), "multi-day soak service output parent", expectedOwnerUid, true);
  if (await lstat(outputPath).then(() => true, () => false)) fail("multi-day soak service output already exists");
  const campaign = await loadCampaign(configPath);
  if (within(outputPath, campaign.config.paths.root) || within(campaign.config.paths.root, outputPath)) fail("multi-day soak service bundle and campaign custody must not overlap");
  const units = renderDeepWorkSoakServiceUnits({ configPath, config: campaign.config, configSha256: campaign.configSha256, installRoot, serviceUser, serviceGroup, nodePath, flockPath, envPath });
  const serviceBytes = Buffer.from(units.service), timerBytes = Buffer.from(units.timer);
  const manifest = {
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak-service-render", campaignId: campaign.config.campaignId,
    configSha256: campaign.configSha256, sourceCommit: campaign.config.source.commit, sourceTree: campaign.config.source.tree,
    programSha256: campaign.config.source.programSha256, runtimeSnapshotSha256: campaign.config.source.runtimeSnapshotSha256,
    serviceName: units.serviceName, serviceSha256: sha(serviceBytes),
    timerName: units.timerName, timerSha256: sha(timerBytes), authority: { ...renderAuthority }, boundary: renderBoundary,
  };
  checkedManifest(manifest);
  const staging = join(dirname(outputPath), `.deep-work-soak-service-${randomBytes(8).toString("hex")}`);
  await mkdir(staging, { mode: 0o700 });
  try {
    await writeFileExclusive(join(staging, units.serviceName), serviceBytes, 0o600);
    await writeFileExclusive(join(staging, units.timerName), timerBytes, 0o600);
    await writeFileExclusive(join(staging, "service-bundle.json"), Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`), 0o600);
    await syncDirectory(staging); await rename(staging, outputPath); await syncDirectory(dirname(outputPath));
  } catch (error) {
    await rm(staging, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
  const inspected = await inspectDeepWorkSoakServiceBundle(outputPath, { expectedOwnerUid });
  return Object.freeze({
    schemaVersion: 1, operation: manifest.operation, campaignId: manifest.campaignId,
    manifestSha256: inspected.manifestSha256, serviceName: manifest.serviceName, timerName: manifest.timerName,
    state: "rendered-inactive", authority: { ...renderAuthority }, boundary: renderBoundary,
  });
}

export async function inspectDeepWorkSoakServiceBundle(bundlePath, options = {}) {
  const expectedUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedUid) || expectedUid < 0) fail("multi-day soak service bundle owner is invalid");
  const root = await checkedDirectory(resolve(bundlePath), "multi-day soak service bundle", expectedUid, true);
  const manifestBytes = await readExactFile(join(root, "service-bundle.json"), MAX_UNIT_BYTES, "multi-day soak service manifest", expectedUid);
  let manifest;
  try { manifest = checkedManifest(JSON.parse(manifestBytes.toString("utf8"))); } catch (error) { if (error instanceof DeepWorkSoakServiceLifecycleError) throw error; fail("multi-day soak service manifest is not JSON"); }
  const expectedNames = [manifest.serviceName, "service-bundle.json", manifest.timerName].sort();
  const names = (await readdir(root)).sort();
  if (JSON.stringify(names) !== JSON.stringify(expectedNames) || names.some((name) => basename(name) !== name)) fail("multi-day soak service bundle file set is invalid");
  const serviceBytes = await readExactFile(join(root, manifest.serviceName), MAX_UNIT_BYTES, "multi-day soak service unit", expectedUid);
  const timerBytes = await readExactFile(join(root, manifest.timerName), MAX_UNIT_BYTES, "multi-day soak service timer", expectedUid);
  if (sha(serviceBytes) !== manifest.serviceSha256 || sha(timerBytes) !== manifest.timerSha256) fail("multi-day soak service bundle content differs from its manifest");
  const service = serviceBytes.toString("utf8"), timer = timerBytes.toString("utf8");
  for (const anchor of ["/env -i HOME=/nonexistent PATH=/usr/bin:/bin LANG=C.UTF-8 INVOCATION_ID=${INVOCATION_ID}", "NoNewPrivileges=true", "PrivateNetwork=true", "ProtectClock=true", "ProtectSystem=strict", "RestrictAddressFamilies=AF_UNIX", "CapabilityBoundingSet=", "DevicePolicy=closed", "deep-work-multi-day-soak.mjs cycle --config "]) if (!service.includes(anchor)) fail("multi-day soak service bundle lost a required isolation boundary");
  for (const anchor of ["OnCalendar=*-*-* *:00:00 UTC", "Persistent=true", "RandomizedDelaySec=0", `Unit=${manifest.serviceName}`]) if (!timer.includes(anchor)) fail("multi-day soak timer lost its exact real-time cadence");
  if (service.includes("EnvironmentFile=") || service.includes("SupplementaryGroups=") || service.includes("BindReadOnlyPaths=")) fail("multi-day soak service gained a credential or privileged socket surface");
  const configBinding = exactUnitConfigBinding(service);
  if (configBinding.sha256 !== manifest.configSha256) fail("multi-day soak service configuration confirmation differs from its manifest");
  return Object.freeze({
    root, manifest, manifestSha256: sha(manifestBytes), serviceUser: exactUnitAccount(service, "User"),
    serviceGroup: exactUnitAccount(service, "Group"), configPath: configBinding.path,
    service: Object.freeze({ path: join(root, manifest.serviceName), bytes: serviceBytes }),
    timer: Object.freeze({ path: join(root, manifest.timerName), bytes: timerBytes }),
  });
}

export async function verifyDeepWorkSoakServiceLiveBinding(bundle, options = {}) {
  const expectedOwnerUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  let campaign;
  try { campaign = await loadCampaign(bundle.configPath, { expectedOwnerUid }); } catch { fail("multi-day soak live private campaign is unavailable or invalid"); }
  if (
    campaign.configSha256 !== bundle.manifest.configSha256 || campaign.config.campaignId !== bundle.manifest.campaignId
    || campaign.config.source.commit !== bundle.manifest.sourceCommit || campaign.config.source.tree !== bundle.manifest.sourceTree
    || campaign.config.source.programSha256 !== bundle.manifest.programSha256
    || campaign.config.source.runtimeSnapshotSha256 !== bundle.manifest.runtimeSnapshotSha256
  ) fail("multi-day soak live campaign differs from the rendered manifest");
  return Object.freeze({ campaignId: campaign.config.campaignId, configSha256: campaign.configSha256 });
}

export function execSoakLifecycleCommand(command, args) {
  return new Promise((done) => execFile(command, args, {
    cwd: "/", env: { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8" }, encoding: "utf8",
    timeout: 300000, maxBuffer: 4 * 1024 * 1024, windowsHide: true, shell: false,
  }, (error, stdout, stderr) => done({ code: typeof error?.code === "number" ? error.code : error ? 1 : 0, executionError: error && typeof error?.code !== "number" ? "process-failure" : null, stdout: stdout ?? "", stderr: stderr ?? "" })));
}
function completed(result, label) {
  if (!result || !Number.isSafeInteger(result.code) || typeof result.stdout !== "string" || typeof result.stderr !== "string" || result.executionError !== undefined && result.executionError !== null) fail(`${label} failed closed`);
  return result;
}
function succeeded(result, label) { completed(result, label); if (result.code !== 0) fail(`${label} failed closed`); return result; }
function notSuccessful(result, label) { completed(result, label); if (result.code === 0) fail(`${label} failed closed`); return result; }
function confirmed(bundle, confirmation) { if (!SHA_RE.test(confirmation ?? "") || confirmation !== bundle.manifestSha256) fail("multi-day soak lifecycle confirmation differs from the exact manifest"); }
async function installedState(bundle, systemdDirectory, expectedUid) {
  const directory = await checkedDirectory(resolve(systemdDirectory), "systemd unit directory", expectedUid, false);
  const targets = { service: join(directory, bundle.manifest.serviceName), timer: join(directory, bundle.manifest.timerName) }, result = {};
  for (const [kind, path] of Object.entries(targets)) {
    const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (info === null) { result[kind] = "absent"; continue; }
    const bytes = await readExactFile(path, MAX_UNIT_BYTES, `installed multi-day soak ${kind}`, expectedUid, false);
    result[kind] = Buffer.compare(bytes, bundle[kind].bytes) === 0 ? "exact" : "different";
  }
  return { directory, targets, ...result };
}
function lifecycleReceipt(operation, bundle, state) {
  return Object.freeze({
    schemaVersion: 1, operation: `pixel-deep-work-multi-day-soak-service-${operation}`, campaignId: bundle.manifest.campaignId,
    manifestSha256: bundle.manifestSha256, serviceName: bundle.manifest.serviceName, timerName: bundle.manifest.timerName, state,
    authority: { schedulesQualificationCycles: operation === "activate", removesUnits: operation === "remove", grantsWork: false, grantsLease: false, grantsExternalEffects: false, grantsRelease: false },
    boundary: "Content-free exact-manifest qualification service lifecycle receipt. Scheduling invokes only the credential-free soak controller and grants no work, provider, network, deployment, publication, or release authority.",
  });
}

export async function installDeepWorkSoakServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid,
  expectedSystemUid = 0, commandRunner = execSoakLifecycleCommand, liveBindingVerifier = verifyDeepWorkSoakServiceLiveBinding,
  systemdAnalyzePath = "/usr/bin/systemd-analyze", systemctlPath = "/usr/bin/systemctl",
} = {}) {
  const bundle = await inspectDeepWorkSoakServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid }); confirmed(bundle, confirmation);
  if (typeof commandRunner !== "function" || typeof liveBindingVerifier !== "function") fail("multi-day soak lifecycle dependency is invalid");
  await liveBindingVerifier(bundle, { expectedOwnerUid: expectedBundleUid });
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if ([installed.service, installed.timer].includes("different")) fail("a different multi-day soak unit is already installed");
  const created = [];
  try {
    if (installed.service === "absent") { await writeFileExclusive(installed.targets.service, bundle.service.bytes, 0o644); created.push(installed.targets.service); }
    if (installed.timer === "absent") { await writeFileExclusive(installed.targets.timer, bundle.timer.bytes, 0o644); created.push(installed.targets.timer); }
    await syncDirectory(installed.directory);
    succeeded(await commandRunner(systemdAnalyzePath, ["verify", installed.targets.service, installed.targets.timer]), "multi-day soak systemd verification");
    succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "multi-day soak daemon reload");
  } catch (error) {
    for (const path of created.reverse()) await unlink(path).catch(() => {});
    await syncDirectory(installed.directory).catch(() => {}); await commandRunner(systemctlPath, ["daemon-reload"]).catch(() => {}); throw error;
  }
  return lifecycleReceipt("install", bundle, "installed-inactive");
}

export async function activateDeepWorkSoakServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid,
  expectedSystemUid = 0, commandRunner = execSoakLifecycleCommand, liveBindingVerifier = verifyDeepWorkSoakServiceLiveBinding,
  systemctlPath = "/usr/bin/systemctl",
} = {}) {
  const bundle = await inspectDeepWorkSoakServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid }); confirmed(bundle, confirmation); await liveBindingVerifier(bundle, { expectedOwnerUid: expectedBundleUid });
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if (installed.service !== "exact" || installed.timer !== "exact") fail("exact multi-day soak units are not installed");
  succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "multi-day soak daemon reload");
  try {
    succeeded(await commandRunner(systemctlPath, ["enable", "--now", bundle.manifest.timerName]), "multi-day soak timer activation");
    succeeded(await commandRunner(systemctlPath, ["is-enabled", "--quiet", bundle.manifest.timerName]), "multi-day soak timer enable verification");
    succeeded(await commandRunner(systemctlPath, ["is-active", "--quiet", bundle.manifest.timerName]), "multi-day soak timer activity verification");
  } catch (error) { await commandRunner(systemctlPath, ["disable", "--now", bundle.manifest.timerName]).catch(() => {}); throw error; }
  return lifecycleReceipt("activate", bundle, "active");
}

export async function removeDeepWorkSoakServiceBundle({
  bundlePath, confirmation, systemdDirectory = "/etc/systemd/system", expectedBundleUid,
  expectedSystemUid = 0, commandRunner = execSoakLifecycleCommand, systemctlPath = "/usr/bin/systemctl",
} = {}) {
  const bundle = await inspectDeepWorkSoakServiceBundle(resolve(bundlePath), { expectedOwnerUid: expectedBundleUid }); confirmed(bundle, confirmation);
  const installed = await installedState(bundle, systemdDirectory, expectedSystemUid);
  if ([installed.service, installed.timer].includes("different")) fail("refusing to remove a different multi-day soak unit");
  if (installed.service === "absent" && installed.timer === "absent") return lifecycleReceipt("remove", bundle, "removed-private-campaign-retained");
  succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "multi-day soak pre-removal daemon reload");
  if (installed.timer === "exact") {
    succeeded(await commandRunner(systemctlPath, ["disable", "--now", bundle.manifest.timerName]), "multi-day soak timer disable");
    notSuccessful(await commandRunner(systemctlPath, ["is-enabled", "--quiet", bundle.manifest.timerName]), "multi-day soak timer disable verification");
    notSuccessful(await commandRunner(systemctlPath, ["is-active", "--quiet", bundle.manifest.timerName]), "multi-day soak timer inactivity verification");
  }
  if (installed.service === "exact") {
    succeeded(await commandRunner(systemctlPath, ["stop", bundle.manifest.serviceName]), "multi-day soak service stop");
    notSuccessful(await commandRunner(systemctlPath, ["is-active", "--quiet", bundle.manifest.serviceName]), "multi-day soak service inactivity verification");
  }
  const removed = [];
  try {
    for (const [kind, path] of Object.entries(installed.targets)) if (installed[kind] === "exact") { await unlink(path); removed.push([path, bundle[kind].bytes]); }
    await syncDirectory(installed.directory); succeeded(await commandRunner(systemctlPath, ["daemon-reload"]), "multi-day soak removal daemon reload");
  } catch (error) {
    for (const [path, bytes] of removed.reverse()) await writeFileExclusive(path, bytes, 0o644);
    await syncDirectory(installed.directory).catch(() => {}); await commandRunner(systemctlPath, ["daemon-reload"]).catch(() => {}); throw error;
  }
  return lifecycleReceipt("remove", bundle, "removed-private-campaign-retained");
}

export const deepWorkSoakServiceRenderBoundary = renderBoundary;
export const deepWorkSoakServiceRenderAuthority = renderAuthority;
