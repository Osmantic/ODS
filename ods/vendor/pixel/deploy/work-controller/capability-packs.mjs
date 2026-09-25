import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import {
  canonical, validateWorkCapabilityConsumption, validateWorkCapabilityGrant, validateWorkCapabilityPack,
} from "../../scripts/lib/work-contract.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const IMAGE_IDENTIFIER_RE = /^(?:[a-z0-9][a-z0-9._/:+-]{1,255}@)?sha256:[a-f0-9]{64}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{1,127}$/u;
const ABS_RE = /^\/[A-Za-z0-9._+-]+(?:\/[A-Za-z0-9._+-]+)*$/u;
const WINDOWS_ABS_RE = /^[A-Za-z]:\\[A-Za-z0-9._+-]+(?:\\[A-Za-z0-9._+-]+)*$/u;
const SAFE_IMAGE_ENV = Object.freeze(["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"]);
const DOCKER_PATH = "/usr/bin/docker";
const classificationRank = Object.freeze({ public: 0, internal: 1, confidential: 2, restricted: 3 });
const grantBoundary = "Exact expiring single-use grant for one isolated capability-pack session. It grants only the named local tool calls inside the disposable boundary and no host, credential, network, external-effect, scope-expansion, or completion authority.";
const claimBoundary = "Immutable replay tombstone for one isolated capability grant. It contains no tool arguments, result content, credential, network, external-effect, scope-expansion, or completion authority.";

export class WorkCapabilityPackError extends Error {}

function fail(message) { throw new WorkCapabilityPackError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
export function capabilityPackSha256(pack) { return sha(pack); }
export function capabilityGrantSha256(grant) { return sha(grant); }
function timestamp(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`);
  return value;
}
function safeInteger(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}
function safeNumber(value, minimum, maximum, label) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}

function checkedPack(pack, expectedPackSha256) {
  const errors = validateWorkCapabilityPack(pack);
  if (errors.length) fail(`capability pack is invalid: ${errors[0]}`);
  if (!SHA_RE.test(expectedPackSha256 ?? "") || expectedPackSha256 !== sha(pack)) fail("capability pack differs from its trusted signed-tree binding");
  return pack;
}

function checkedGrant(pack, grant) {
  const errors = validateWorkCapabilityGrant(grant);
  if (errors.length) fail(`capability grant is invalid: ${errors[0]}`);
  if (grant.pack.id !== pack.id || grant.pack.version !== pack.version || grant.pack.packSha256 !== sha(pack) || grant.pack.treeSha256 !== pack.provenance.treeSha256) fail("capability grant differs from its pack");
  const tools = new Set(pack.tools.map((tool) => tool.name));
  if (grant.tools.some((tool) => !tools.has(tool))) fail("capability grant names an undeclared tool");
  if (!pack.data.acceptedClassifications.includes(grant.dataClassification)) fail("capability grant data classification is not accepted by the pack");
  for (const field of ["maxInputBytes", "maxOutputBytes", "maxRuntimeMs", "maxCalls", "maxMemoryMiB", "maxCpuCores", "maxPids", "maxWorkspaceBytes"]) if (grant.limits[field] > pack.limits[field]) fail(`capability grant widens ${field}`);
  if (pack.adapter.workspace === "none" && grant.limits.maxWorkspaceBytes !== 0) fail("capability grant adds an undeclared workspace");
  return grant;
}

export function issueCapabilityGrant(pack, options) {
  checkedPack(pack, options?.expectedPackSha256);
  if (!JOB_RE.test(options.jobId ?? "") || !SHA_RE.test(options.checkpointSha256 ?? "") || !SUFFIX_RE.test(options.suffix ?? "")) fail("capability grant immutable binding is invalid");
  const issued = timestamp(options.now, "capability grant issue time");
  const expiry = timestamp(options.expiresAt, "capability grant expiry time");
  if (expiry.getTime() <= issued.getTime() || expiry.getTime() - issued.getTime() > Math.min(pack.limits.maxRuntimeMs + 60000, 3600000)) fail("capability grant lifetime is invalid");
  if (!Array.isArray(options.tools) || options.tools.length < 1 || new Set(options.tools).size !== options.tools.length || canonical(options.tools) !== canonical([...options.tools].sort())) fail("capability grant tools are invalid");
  if (!Object.hasOwn(classificationRank, options.dataClassification)) fail("capability grant classification is invalid");
  if (!options.limits || typeof options.limits !== "object" || Array.isArray(options.limits)) fail("capability grant limits are missing");
  const limits = {
    maxInputBytes: safeInteger(options.limits.maxInputBytes, 1, 1048576, "grant input limit"),
    maxOutputBytes: safeInteger(options.limits.maxOutputBytes, 1, 16777216, "grant output limit"),
    maxRuntimeMs: safeInteger(options.limits.maxRuntimeMs, 100, 3600000, "grant runtime limit"),
    maxCalls: safeInteger(options.limits.maxCalls, 1, 1, "grant call limit"),
    maxMemoryMiB: safeInteger(options.limits.maxMemoryMiB, 64, 8192, "grant memory limit"),
    maxCpuCores: safeNumber(options.limits.maxCpuCores, 0.1, 8, "grant CPU limit"),
    maxPids: safeInteger(options.limits.maxPids, 16, 512, "grant process limit"),
    maxWorkspaceBytes: safeInteger(options.limits.maxWorkspaceBytes, 0, 34359738368, "grant workspace limit"),
  };
  const grant = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-grant-v1.schema.json", schemaVersion: 1,
    grantId: `workcapgrant-${String(issued.getTime()).padStart(13, "0")}-${options.suffix}`,
    jobId: options.jobId, issuedAt: issued.toISOString(), expiresAt: expiry.toISOString(), singleUse: true,
    checkpointSha256: options.checkpointSha256,
    pack: { id: pack.id, version: pack.version, packSha256: sha(pack), treeSha256: pack.provenance.treeSha256 },
    tools: [...options.tools], dataClassification: options.dataClassification, limits,
    authority: { hostAccess: false, credentials: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: grantBoundary,
  };
  checkedGrant(pack, grant);
  return grant;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
}

async function privateRegularFile(path, label, optional = false) {
  const info = await lstat(path).catch((error) => {
    if (optional && error?.code === "ENOENT") return null;
    fail(`${label} is not a real file`);
  });
  if (info === null) return null;
  if (!info.isFile() || info.isSymbolicLink()) fail(`${label} is not a real file`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return info;
}

async function readPrivateJson(path, label, optional = false) {
  const info = await privateRegularFile(path, label, optional);
  if (info === null) return null;
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail(`${label} could not be opened safely`));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== info.nlink || opened.dev !== info.dev || opened.ino !== info.ino || opened.size !== info.size) fail(`${label} changed during validation`);
    try { return JSON.parse(await handle.readFile("utf8")); } catch { fail(`${label} is not valid JSON`); }
  } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const directory = await open(path, constants.O_RDONLY);
  try { await directory.sync(); } finally { await directory.close(); }
}

export async function claimCapabilityGrant({ stateRoot, pack, expectedPackSha256, grant, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  checkedPack(pack, expectedPackSha256);
  checkedGrant(pack, grant);
  const claimed = timestamp(now, "capability claim time");
  if (claimed.getTime() < Date.parse(grant.issuedAt) || claimed.getTime() >= Date.parse(grant.expiresAt) || !SUFFIX_RE.test(suffix)) fail("capability grant is not current or claim identity is invalid");
  const claim = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-consumption-v1.schema.json", schemaVersion: 1,
    claimId: `workcapclaim-${String(claimed.getTime()).padStart(13, "0")}-${suffix}`,
    grantId: grant.grantId, jobId: grant.jobId, claimedAt: claimed.toISOString(), grantSha256: sha(grant),
    packSha256: sha(pack), checkpointSha256: grant.checkpointSha256, status: "consumed", externalEffects: false,
    boundary: claimBoundary,
  };
  const errors = validateWorkCapabilityConsumption(claim);
  if (errors.length) fail(`capability claim is invalid: ${errors[0]}`);
  const root = resolve(stateRoot);
  await privateDirectory(root, "capability state root");
  const claims = join(root, "capability-claims");
  await mkdir(claims, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(claims, "capability claim root");
  const temporary = join(claims, `.claim-${randomBytes(8).toString("hex")}`);
  const destination = join(claims, `${grant.grantId}.json`);
  const serialized = `${JSON.stringify(claim, null, 2)}\n`;
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, destination); await unlink(temporary);
    await syncDirectory(claims);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("capability grant was already consumed");
    throw error;
  }
  return { claim, sha256: sha(claim), path: destination };
}

export async function loadCapabilityGrantClaim({ stateRoot, pack, expectedPackSha256, grant, optional = false }) {
  checkedPack(pack, expectedPackSha256); checkedGrant(pack, grant);
  const root = resolve(stateRoot);
  await privateDirectory(root, "capability state root");
  const source = join(root, "capability-claims", `${grant.grantId}.json`);
  const claim = await readPrivateJson(source, "capability claim", optional);
  if (claim === null) return null;
  checkedClaim(pack, grant, claim);
  return Object.freeze({ ...claim });
}

export async function beginCapabilityExecution({ stateRoot, pack, expectedPackSha256, grant, claim, now = new Date() }) {
  checkedPack(pack, expectedPackSha256); checkedClaim(pack, grant, claim);
  const started = timestamp(now, "capability execution start time");
  if (started.getTime() < Date.parse(claim.claimedAt) || started.getTime() >= Date.parse(grant.expiresAt)) fail("capability grant expired before execution start");
  const root = resolve(stateRoot);
  await privateDirectory(root, "capability state root");
  const claims = join(root, "capability-claims");
  await privateDirectory(claims, "capability claim root");
  const source = join(claims, `${grant.grantId}.json`);
  const stored = await readPrivateJson(source, "capability claim");
  if (canonical(stored) !== canonical(claim)) fail("capability claim file differs from execution claim");
  const executions = join(root, "capability-executions");
  await mkdir(executions, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  await privateDirectory(executions, "capability execution root");
  const destination = join(executions, `${grant.grantId}.json`);
  try {
    await link(source, destination);
    const executionStored = await readPrivateJson(destination, "capability execution tombstone");
    if (canonical(executionStored) !== canonical(claim)) fail("capability execution tombstone differs from claim");
    await syncDirectory(executions);
  } catch (error) {
    if (error?.code === "EEXIST") fail("capability execution was already started");
    throw error;
  }
  return { path: destination, claimSha256: sha(claim), startedAt: started.toISOString() };
}

function checkedClaim(pack, grant, claim) {
  checkedGrant(pack, grant);
  const errors = validateWorkCapabilityConsumption(claim);
  if (errors.length) fail(`capability claim is invalid: ${errors[0]}`);
  if (claim.grantId !== grant.grantId || claim.jobId !== grant.jobId || claim.grantSha256 !== sha(grant) || claim.packSha256 !== sha(pack) || claim.checkpointSha256 !== grant.checkpointSha256 || Date.parse(claim.claimedAt) < Date.parse(grant.issuedAt) || Date.parse(claim.claimedAt) >= Date.parse(grant.expiresAt)) fail("capability claim differs from its grant");
  return claim;
}

function safePath(value, label) { if (!ABS_RE.test(value ?? "") && !WINDOWS_ABS_RE.test(value ?? "") || basename(value) === "..") fail(`${label} is invalid`); return value; }

export function buildCapabilityAdapterDockerCommand({ pack, expectedPackSha256, grant, claim, runtime }) {
  checkedPack(pack, expectedPackSha256); checkedClaim(pack, grant, claim);
  if (!runtime || typeof runtime !== "object" || Array.isArray(runtime)) fail("capability runtime is missing");
  safePath(runtime.dockerPath, "capability Docker path"); safePath(runtime.cidFile, "capability CID path"); safePath(runtime.dockerConfigPath, "capability Docker configuration path");
  if (!NAME_RE.test(runtime.containerName ?? "") || !IMAGE_IDENTIFIER_RE.test(runtime.imageIdentifier ?? "") || runtime.imageIdentifier !== pack.adapter.imageRef) fail("capability runtime identity is invalid");
  const workspace = pack.adapter.workspace === "disposable-read-write";
  if (workspace !== (typeof runtime.workspaceVolumeName === "string") || workspace && !NAME_RE.test(runtime.workspaceVolumeName)) fail("capability workspace runtime is invalid");
  const args = [
    "--config", runtime.dockerConfigPath, "run", "--rm", "-i", "--pull", "never", "--name", runtime.containerName, "--cidfile", runtime.cidFile,
    "--label", `com.osmantic.pixel.work-capability-claim=${claim.claimId}`,
    "--label", `com.osmantic.pixel.work-job=${claim.jobId}`,
    "--label", `com.osmantic.pixel.work-capability-pack=${pack.id}`,
    "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--privileged=false",
    "--network", "none", "--ipc", "none", "--cgroupns", "private", "--log-driver", "none",
    "--pids-limit", String(grant.limits.maxPids), "--memory", `${grant.limits.maxMemoryMiB}m`,
    "--memory-swap", `${grant.limits.maxMemoryMiB}m`, "--cpus", String(grant.limits.maxCpuCores),
    "--user", `${pack.adapter.uid}:${pack.adapter.gid}`, "--env", "HOME=/home/pixel", "--env", "LANG=C.UTF-8",
    "--env", "TZ=UTC", "--env", "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777", "--tmpfs", `/home/pixel:rw,nosuid,nodev,noexec,size=16m,mode=0700,uid=${pack.adapter.uid},gid=${pack.adapter.gid}`,
  ];
  if (workspace) args.push("--mount", `type=volume,source=${runtime.workspaceVolumeName},target=/workspace`, "--workdir", "/workspace");
  else args.push("--workdir", "/home/pixel");
  args.push("--entrypoint", pack.adapter.executablePath, runtime.imageIdentifier, ...pack.adapter.arguments);
  return { command: runtime.dockerPath, args, env: {} };
}

export function buildCapabilityAdapterCleanupDockerCommands({ pack, expectedPackSha256, grant, claim, runtime }) {
  checkedPack(pack, expectedPackSha256); checkedClaim(pack, grant, claim);
  if (!runtime || typeof runtime !== "object" || Array.isArray(runtime)) fail("capability runtime is missing");
  safePath(runtime.dockerPath, "capability Docker path"); safePath(runtime.dockerConfigPath, "capability Docker configuration path");
  if (!NAME_RE.test(runtime.containerName ?? "")) fail("capability container identity is invalid");
  const prefix = ["--config", runtime.dockerConfigPath, "container"];
  return {
    inspectOwned: { command: runtime.dockerPath, args: [...prefix, "inspect", runtime.containerName], env: {} },
    remove: { command: runtime.dockerPath, args: [...prefix, "rm", "--force", runtime.containerName], env: {} },
    inspectAbsent: { command: runtime.dockerPath, args: [...prefix, "inspect", "--format", "{{.Id}}", runtime.containerName], env: {} },
    listAbsent: { command: runtime.dockerPath, args: [...prefix, "ls", "--all", "--no-trunc", "--quiet", "--filter", `name=^/${runtime.containerName}$`], env: {} },
  };
}

export function buildCapabilityHealthDockerCommands({ pack, expectedPackSha256, runtime }) {
  checkedPack(pack, expectedPackSha256);
  if (!runtime || typeof runtime !== "object" || Array.isArray(runtime)) fail("capability health runtime is missing");
  safePath(runtime.dockerPath, "capability health Docker path"); safePath(runtime.dockerConfigPath, "capability health Docker configuration path");
  if (runtime.dockerPath !== DOCKER_PATH || !NAME_RE.test(runtime.containerName ?? "") || runtime.imageIdentifier !== pack.adapter.imageRef) fail("capability health runtime identity is invalid");
  const prefix = ["--config", runtime.dockerConfigPath];
  const args = [
    ...prefix, "run", "--rm", "-i", "--pull", "never", "--name", runtime.containerName,
    "--label", `com.osmantic.pixel.work-capability-health=${pack.id}`,
    "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--privileged=false",
    "--network", "none", "--ipc", "none", "--cgroupns", "private", "--log-driver", "none",
    "--pids-limit", String(Math.min(pack.limits.maxPids, 64)), "--memory", `${Math.min(pack.limits.maxMemoryMiB, 256)}m`,
    "--memory-swap", `${Math.min(pack.limits.maxMemoryMiB, 256)}m`, "--cpus", String(Math.min(pack.limits.maxCpuCores, 1)),
    "--user", `${pack.adapter.uid}:${pack.adapter.gid}`, "--env", "HOME=/home/pixel", "--env", "LANG=C.UTF-8",
    "--env", "TZ=UTC", "--env", `PATH=${SAFE_IMAGE_ENV[0].slice(5)}`,
    "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777", "--tmpfs", `/home/pixel:rw,nosuid,nodev,noexec,size=16m,mode=0700,uid=${pack.adapter.uid},gid=${pack.adapter.gid}`,
    "--workdir", "/home/pixel", "--entrypoint", pack.adapter.executablePath, pack.adapter.imageRef, ...pack.adapter.arguments,
  ];
  return Object.freeze({
    run: { command: DOCKER_PATH, args, env: {} },
    remove: { command: DOCKER_PATH, args: [...prefix, "container", "rm", "--force", runtime.containerName], env: {} },
    inspectAbsent: { command: DOCKER_PATH, args: [...prefix, "container", "inspect", "--format", "{{.Id}}", runtime.containerName], env: {} },
    listAbsent: { command: DOCKER_PATH, args: [...prefix, "container", "ls", "--all", "--no-trunc", "--quiet", "--filter", `name=^/${runtime.containerName}$`], env: {} },
  });
}

export function validateCapabilityAdapterImage(image, pack, expectedPackSha256) {
  checkedPack(pack, expectedPackSha256);
  const localImageIdentity = pack.adapter.imageRef === pack.adapter.imageId && pack.adapter.imageDigest === pack.adapter.imageId;
  const repositoryIdentity = Array.isArray(image?.RepoDigests) && image.RepoDigests.includes(pack.adapter.imageRef);
  if (!image || typeof image !== "object" || Array.isArray(image) || image.Id !== pack.adapter.imageId || image.Os !== pack.adapter.os || image.Architecture !== pack.adapter.architecture || !localImageIdentity && !repositoryIdentity) fail("capability adapter image identity is invalid");
  const labels = image.Config?.Labels ?? {};
  if (labels["org.osmantic.pixel.capability-tree"] !== pack.provenance.treeSha256 || labels["org.osmantic.pixel.capability-executable"] !== pack.adapter.executableSha256 || labels["org.osmantic.pixel.mcp-version"] !== "2026-07-28") fail("capability adapter image labels differ from the signed pack");
  if (canonical(image.Config?.Env ?? []) !== canonical(SAFE_IMAGE_ENV) || Object.keys(image.Config?.Volumes ?? {}).length !== 0 || (image.Config?.OnBuild ?? []).length !== 0 || image.Config?.Healthcheck != null) fail("capability adapter image carries unsafe environment, volumes, build hooks, or health checks");
  return true;
}

export function validateCapabilityWorkspaceVolume(volume, pack, grant, claim, runtime) {
  checkedClaim(pack, grant, claim);
  if (pack.adapter.workspace !== "disposable-read-write" || grant.limits.maxWorkspaceBytes < 1) fail("capability pack has no workspace volume");
  const option = `nosuid,nodev,noexec,size=${grant.limits.maxWorkspaceBytes},mode=0700,uid=${pack.adapter.uid},gid=${pack.adapter.gid}`;
  if (!volume || volume.Name !== runtime.workspaceVolumeName || volume.Driver !== "local" || volume.Scope !== "local" || volume.Labels?.["com.osmantic.pixel.work-capability-claim"] !== claim.claimId || volume.Labels?.["com.osmantic.pixel.work-job"] !== claim.jobId || volume.Options?.device !== "tmpfs" || volume.Options?.type !== "tmpfs" || volume.Options?.o !== option) fail("capability workspace volume differs from its grant");
  return true;
}

export function buildCapabilityWorkspaceDockerCommands({ pack, expectedPackSha256, grant, claim, runtime }) {
  checkedPack(pack, expectedPackSha256); checkedClaim(pack, grant, claim);
  if (!runtime || typeof runtime !== "object" || Array.isArray(runtime)) fail("capability runtime is missing");
  safePath(runtime.dockerPath, "capability Docker path"); safePath(runtime.dockerConfigPath, "capability Docker configuration path");
  if (pack.adapter.workspace !== "disposable-read-write" || grant.limits.maxWorkspaceBytes < 1 || !NAME_RE.test(runtime.workspaceVolumeName ?? "")) fail("capability pack has no valid disposable workspace grant");
  const option = `nosuid,nodev,noexec,size=${grant.limits.maxWorkspaceBytes},mode=0700,uid=${pack.adapter.uid},gid=${pack.adapter.gid}`;
  const prefix = ["--config", runtime.dockerConfigPath, "volume"];
  return {
    create: {
      command: runtime.dockerPath,
      args: [...prefix, "create", "--driver", "local", "--label", `com.osmantic.pixel.work-capability-claim=${claim.claimId}`, "--label", `com.osmantic.pixel.work-job=${claim.jobId}`, "--opt", "type=tmpfs", "--opt", "device=tmpfs", "--opt", `o=${option}`, runtime.workspaceVolumeName],
      env: {},
    },
    inspectOwned: { command: runtime.dockerPath, args: [...prefix, "inspect", runtime.workspaceVolumeName], env: {} },
    remove: { command: runtime.dockerPath, args: [...prefix, "rm", runtime.workspaceVolumeName], env: {} },
    inspectAbsent: { command: runtime.dockerPath, args: [...prefix, "inspect", runtime.workspaceVolumeName], env: {} },
    listAbsent: { command: runtime.dockerPath, args: [...prefix, "ls", "--quiet", "--filter", `name=^${runtime.workspaceVolumeName}$`], env: {} },
  };
}
