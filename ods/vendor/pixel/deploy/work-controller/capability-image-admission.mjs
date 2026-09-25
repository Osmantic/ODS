import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readFile, readdir, realpath, rename, rmdir, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";

import { assertJsonSchema } from "../../scripts/lib/json-schema.mjs";
import { canonical } from "../../scripts/lib/work-contract.mjs";
import { capabilityPackSha256, validateCapabilityAdapterImage } from "./capability-packs.mjs";
import { loadInstalledCapabilityPack } from "./capability-pack-installation.mjs";
import {
  acquireCapabilityPackOperation, CapabilityPackOperationError,
  loadCapabilityPackOperation, releaseCapabilityPackOperation,
} from "./capability-pack-operation.mjs";

const DOCKER_PATH = "/usr/bin/docker";
const SHA_RE = /^[a-f0-9]{64}$/u;
const ID_RE = /^[a-z][a-z0-9-]{1,62}$/u;
const VERSION_RE = /^(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})$/u;
const IDENTITY_RE = /^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$/u;
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{1,127}$/u;
const VERSION_PACK_RE = /^((?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8}))-([a-f0-9]{64})$/u;
const REVOCATION_STAGE_RE = /^\.revocation-((?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8}))-([a-f0-9]{64})-([a-f0-9]{16})$/u;
const authority = Object.freeze({ grantsHealthProbe: false, grantsToolUse: false, grantsDataAccess: false, grantsImagePull: false, grantsExecution: false, grantsNetwork: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Host-verified admission of one already-local exact image and executable. Admission pulls and executes no image, registers no tool, and grants no health probe, tool use, data access, network, external effect, or completion authority.";
const revokeBoundary = "Content-free revocation of Pixel's admission for one unowned local image. The shared image is retained; revocation grants no deletion, trust, health probe, tool use, execution, reinstall, external effect, or completion authority.";
const reviewBoundary = "Content-free review of one exact image-admission revocation. Review grants no revocation, image deletion, health probe, tool use, execution, reinstall, external effect, or completion authority.";
const cleanupBoundary = "Private cleanup-only custody for one never-started image-inspection container. It grants only exact removal and absence inspection for the named container and no image pull, image execution, tool use, data access, network, external effect, or completion authority.";
const admissionSchema = JSON.parse(await readFile(new URL("../../schemas/work-capability-image-admission-v1.schema.json", import.meta.url), "utf8"));
const revocationSchema = JSON.parse(await readFile(new URL("../../schemas/work-capability-image-revocation-v1.schema.json", import.meta.url), "utf8"));

export class CapabilityImageAdmissionError extends Error {}

function fail(message) { throw new CapabilityImageAdmissionError(message); }
function sha(payload) { return createHash("sha256").update(typeof payload === "string" || Buffer.isBuffer(payload) ? payload : canonical(payload)).digest("hex"); }
async function operationCall(promise) { try { return await promise; } catch (error) { if (error instanceof CapabilityPackOperationError) fail(error.message); throw error; } }

async function privateDirectory(path, label, create = false) {
  if (!isAbsolute(path) || resolve(path) === dirname(resolve(path))) fail(`${label} must be an absolute non-root directory`);
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || await realpath(path) !== resolve(path)) fail(`${label} must be a real unlinked directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-private`);
  return resolve(path);
}

async function privateRegular(path, maximumBytes, label, normalizeMode = false) {
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)); }
  catch { fail(`${label} must be a descriptor-bound regular file`); }
  try {
    if (normalizeMode && process.platform !== "win32") await handle.chmod(0o600);
    const info = await handle.stat(), current = await lstat(path);
    if (!info.isFile() || info.nlink !== 1 || info.size < 1 || info.size > maximumBytes || current.isSymbolicLink() || current.dev !== info.dev || current.ino !== info.ino) fail(`${label} must be a bounded regular single-link file`);
    if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-private`);
    return { payload: await handle.readFile(), info };
  } finally { await handle.close(); }
}

async function writeExclusive(path, payload) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(payload); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

function safeRuntime(runtime) {
  if (!runtime || typeof runtime !== "object" || Array.isArray(runtime) || runtime.dockerPath !== DOCKER_PATH || !isAbsolute(runtime.dockerConfigPath ?? "") || !NAME_RE.test(runtime.containerName ?? "")) fail("capability image runtime is invalid");
  return runtime;
}

export function buildCapabilityImageAdmissionDockerCommands(pack, expectedPackSha256, runtime) {
  if (capabilityPackSha256(pack) !== expectedPackSha256 || !SHA_RE.test(expectedPackSha256 ?? "")) fail("capability image pack binding is invalid");
  safeRuntime(runtime);
  const prefix = ["--config", runtime.dockerConfigPath];
  const env = Object.freeze({ HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C" });
  return Object.freeze({
    inspectImage: { command: DOCKER_PATH, args: [...prefix, "image", "inspect", pack.adapter.imageRef], env },
    createContainer: { command: DOCKER_PATH, args: [...prefix, "container", "create", "--name", runtime.containerName, "--pull", "never", "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--privileged=false", "--ipc", "none", "--cgroupns", "private", "--pids-limit", String(Math.min(pack.limits.maxPids, 64)), "--memory", `${Math.min(pack.limits.maxMemoryMiB, 256)}m`, "--memory-swap", `${Math.min(pack.limits.maxMemoryMiB, 256)}m`, "--cpus", String(Math.min(pack.limits.maxCpuCores, 1)), "--user", `${pack.adapter.uid}:${pack.adapter.gid}`, "--env", "HOME=/home/pixel", "--env", "LANG=C.UTF-8", "--entrypoint", pack.adapter.executablePath, pack.adapter.imageRef, ...pack.adapter.arguments], env },
    copyExecutable: { command: DOCKER_PATH, args: [...prefix, "container", "cp", `${runtime.containerName}:${pack.adapter.executablePath}`, "-"], env },
    removeContainer: { command: DOCKER_PATH, args: [...prefix, "container", "rm", "--force", runtime.containerName], env },
    inspectAbsent: { command: DOCKER_PATH, args: [...prefix, "container", "inspect", "--format", "{{.Id}}", runtime.containerName], env },
    listAbsent: { command: DOCKER_PATH, args: [...prefix, "container", "ls", "--all", "--no-trunc", "--quiet", "--filter", `name=^/${runtime.containerName}$`], env },
  });
}

function tarOctal(field, label) {
  const value = field.toString("ascii").replace(/\0.*$/u, "").trim();
  if (!/^[0-7]+$/u.test(value)) fail(`capability executable archive ${label} is invalid`);
  const parsed = Number.parseInt(value, 8);
  if (!Number.isSafeInteger(parsed)) fail(`capability executable archive ${label} is invalid`);
  return parsed;
}

function tarText(field, label) {
  const zero = field.indexOf(0), end = zero === -1 ? field.length : zero;
  if (zero !== -1 && field.subarray(zero).some((byte) => byte !== 0)) fail(`capability executable archive ${label} is invalid`);
  const value = field.subarray(0, end).toString("ascii");
  if (!value || /[^A-Za-z0-9._/+:-]/u.test(value) || value.split("/").includes("..")) fail(`capability executable archive ${label} is invalid`);
  return value;
}

export function extractCapabilityExecutableArchive(archive, executablePath, expectedBytes) {
  if (!Buffer.isBuffer(archive) || !isAbsolute(executablePath ?? "") || !Number.isSafeInteger(expectedBytes) || expectedBytes < 1 || expectedBytes > 67108864) fail("capability executable archive contract is invalid");
  const expectedLength = 512 + Math.ceil(expectedBytes / 512) * 512 + 1024;
  if (archive.length !== expectedLength) fail("capability executable archive size is invalid");
  const header = archive.subarray(0, 512);
  if (header.every((byte) => byte === 0)) fail("capability executable archive has no regular file");
  const storedChecksum = tarOctal(header.subarray(148, 156), "checksum");
  let computedChecksum = 0;
  for (let index = 0; index < header.length; index += 1) computedChecksum += index >= 148 && index < 156 ? 32 : header[index];
  if (storedChecksum !== computedChecksum) fail("capability executable archive checksum is invalid");
  const name = tarText(header.subarray(0, 100), "name"), prefix = header[345] === 0 ? "" : tarText(header.subarray(345, 500), "prefix");
  const fullName = prefix ? `${prefix}/${name}` : name;
  if (basename(fullName) !== basename(executablePath) || ![0, 48].includes(header[156]) || header.subarray(157, 257).some((byte) => byte !== 0)) fail("capability executable archive entry is not the exact regular file");
  if (tarOctal(header.subarray(124, 136), "size") !== expectedBytes) fail("capability executable archive differs from the signed byte size");
  const paddedEnd = 512 + Math.ceil(expectedBytes / 512) * 512;
  if (archive.subarray(512 + expectedBytes, paddedEnd).some((byte) => byte !== 0) || archive.subarray(paddedEnd).some((byte) => byte !== 0)) fail("capability executable archive contains extra data or entries");
  return Buffer.from(archive.subarray(512, 512 + expectedBytes));
}

function defaultRun(command, maximumBytes) {
  const result = spawnSync(command.command, command.args, { env: command.env, encoding: null, windowsHide: true, shell: false, timeout: 30000, maxBuffer: maximumBytes });
  return { status: result.status, error: result.error, signal: result.signal, stdout: result.stdout ?? Buffer.alloc(0), stderr: result.stderr ?? Buffer.alloc(0) };
}

async function invoke(dependencies, command, maximumBytes, label) {
  const result = await (dependencies.runDocker ?? defaultRun)(command, maximumBytes);
  if (!result || typeof result !== "object" || !Number.isInteger(result.status) || !Buffer.isBuffer(result.stdout) || !Buffer.isBuffer(result.stderr) || result.stdout.length > maximumBytes || result.stderr.length > 65536 || result.error || result.signal) fail(`${label} failed or exceeded its bounded process contract`);
  return result;
}

async function cleanupDirectory(root, id, version, packSha256, create) {
  const base = await privateDirectory(join(root, "capability-pack-image-cleanups"), "capability image cleanup root", create);
  const identity = await privateDirectory(join(base, id), "capability image cleanup identity root", create);
  return privateDirectory(join(identity, `${version}-${packSha256}`), "capability image cleanup version root", create);
}

function cleanupIntent(installed, runtime, operation) {
  return {
    schemaVersion: 1, operation: "pixel-work-capability-image-cleanup-intent",
    pack: { id: installed.pack.id, version: installed.pack.version, packSha256: installed.verification.pack.packSha256 },
    containerName: runtime.containerName, dockerConfigPath: runtime.dockerConfigPath, operationToken: operation.token, operationBindingSha256: operation.bindingSha256,
    authority: { removeExactContainer: true, inspectExactContainerAbsence: true, imagePull: false, imageExecution: false, toolUse: false, dataAccess: false, network: false, externalEffects: false, completion: false },
    boundary: cleanupBoundary,
  };
}

function checkedPackBinding(value, label) {
  const keys = ["id", "packSha256", "signerIdentity", "treeSha256", "version"];
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys) || !ID_RE.test(value.id ?? "") || !VERSION_RE.test(value.version ?? "") || !SHA_RE.test(value.packSha256 ?? "") || !SHA_RE.test(value.treeSha256 ?? "") || !IDENTITY_RE.test(value.signerIdentity ?? "")) fail(`${label} pack binding is invalid`);
  return value;
}

function checkedCleanupIntent(value) {
  const keys = ["authority", "boundary", "containerName", "dockerConfigPath", "operation", "operationBindingSha256", "operationToken", "pack", "schemaVersion"];
  const expectedAuthority = { removeExactContainer: true, inspectExactContainerAbsence: true, imagePull: false, imageExecution: false, toolUse: false, dataAccess: false, network: false, externalEffects: false, completion: false };
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys) || value.schemaVersion !== 1 || value.operation !== "pixel-work-capability-image-cleanup-intent" || !ID_RE.test(value.pack?.id ?? "") || !VERSION_RE.test(value.pack?.version ?? "") || !SHA_RE.test(value.pack?.packSha256 ?? "") || canonical(Object.keys(value.pack ?? {}).sort()) !== canonical(["id", "packSha256", "version"]) || !NAME_RE.test(value.containerName ?? "") || !isAbsolute(value.dockerConfigPath ?? "") || !SHA_RE.test(value.operationToken ?? "") || !SHA_RE.test(value.operationBindingSha256 ?? "") || canonical(value.authority) !== canonical(expectedAuthority) || value.boundary !== cleanupBoundary) fail("capability image cleanup intent is invalid");
  return value;
}

async function proveContainerAbsent(dependencies, commands) {
  await invoke(dependencies, commands.removeContainer, 65536, "capability image inspection-container cleanup");
  const absent = await invoke(dependencies, commands.inspectAbsent, 65536, "capability image inspection-container absence check");
  if (absent.status === 0 || absent.stdout.toString("ascii").trim().length !== 0) fail("capability image inspection container absence was not proven");
  const listed = await invoke(dependencies, commands.listAbsent, 65536, "capability image inspection-container absence listing");
  if (listed.status !== 0 || listed.stderr.length !== 0 || listed.stdout.toString("ascii").trim().length !== 0) fail("capability image inspection container absence was not proven");
}

function admissionReceipt(installed, image, executable, inspectedAt) {
  if (!(inspectedAt instanceof Date) || !Number.isSafeInteger(inspectedAt.getTime())) fail("capability image inspection time is invalid");
  const receipt = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-image-admission-v1.schema.json",
    schemaVersion: 1, operation: "pixel-work-capability-image-admission", pack: { ...installed.verification.pack },
    installationReceiptSha256: sha(installed.receipt),
    image: { reference: installed.pack.adapter.imageRef, digest: installed.pack.adapter.imageDigest, id: installed.pack.adapter.imageId, inspectSha256: sha(image), treeSha256: installed.pack.provenance.treeSha256, executableSha256: executable.sha256, executableBytes: executable.bytes, metadataValidated: true, executableValidated: true, pulledByPixel: false, executed: false },
    inspectedAt: inspectedAt.toISOString(), enabled: false, health: { status: "not-probed", evidenceSha256: null }, authority: { ...authority }, boundary,
  };
  try { assertJsonSchema(receipt, admissionSchema, "capability image admission"); } catch { fail("capability image admission receipt is invalid"); }
  return receipt;
}

export async function loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath }) {
  const installed = await loadInstalledCapabilityPack({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  const directory = join(installed.root, "capability-pack-image-admissions", id, version);
  await privateDirectory(directory, "capability image admission directory");
  if (canonical((await readdir(directory)).sort()) !== canonical(["admission.json"])) fail("capability image admission directory contains unknown files");
  let receipt;
  try { receipt = JSON.parse((await privateRegular(join(directory, "admission.json"), 512 * 1024, "capability image admission receipt")).payload.toString("utf8")); } catch (error) { if (error instanceof CapabilityImageAdmissionError) throw error; fail("capability image admission receipt is not JSON"); }
  try { assertJsonSchema(receipt, admissionSchema, "capability image admission"); } catch { fail("capability image admission receipt is invalid"); }
  if (canonical(receipt.pack) !== canonical(installed.verification.pack) || receipt.installationReceiptSha256 !== sha(installed.receipt) || receipt.image.reference !== installed.pack.adapter.imageRef || receipt.image.digest !== installed.pack.adapter.imageDigest || receipt.image.id !== installed.pack.adapter.imageId || receipt.image.treeSha256 !== installed.pack.provenance.treeSha256 || receipt.image.executableSha256 !== installed.pack.adapter.executableSha256 || receipt.image.executableBytes !== installed.pack.adapter.executableBytes) fail("capability image admission differs from its installed pack");
  return { installed, directory, receipt };
}

export async function admitCapabilityImage({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, dockerConfigPath, now = new Date(), dependencies = {} }) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies) || dependencies.runDocker !== undefined && typeof dependencies.runDocker !== "function") fail("capability image admission dependencies are invalid");
  const installed = await loadInstalledCapabilityPack({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  if (installed.pack.schemaVersion !== 1) fail("v2 capability runtime is not enabled; image admission is denied before inspection, pull, or admission");
  const config = await privateDirectory(resolve(dockerConfigPath), "capability Docker configuration");
  if ((await readdir(config)).length !== 0) fail("capability Docker configuration must be empty and credential-free");
  const admissionRoot = await privateDirectory(join(installed.root, "capability-pack-image-admissions"), "capability image admission root", true);
  const identityRoot = await privateDirectory(join(admissionRoot, id), "capability image admission identity root", true);
  const destination = join(identityRoot, version);
  if (await lstat(destination).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("capability image is already admitted or has incomplete admission state");
  const revokedIdentity = join(installed.root, "capability-pack-image-revocations", "finalized", id);
  const revoked = await lstat(revokedIdentity).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (revoked) {
    await privateDirectory(revokedIdentity, "capability image finalized revocation identity root");
    if ((await readdir(revokedIdentity)).some((name) => name.startsWith(`${version}-`))) fail("capability image admission was revoked for this version; publish a new signed version");
  }
  const cleanupRoot = await cleanupDirectory(installed.root, id, version, installed.verification.pack.packSha256, true);
  if ((await readdir(cleanupRoot)).length !== 0) fail("capability image admission has cleanup-required state; recover it before another inspection");
  const operationBindingSha256 = sha({ operation: "pixel-work-capability-image-admission-inspection", pack: installed.verification.pack, imageRef: installed.pack.adapter.imageRef, imageDigest: installed.pack.adapter.imageDigest, imageId: installed.pack.adapter.imageId, executableSha256: installed.pack.adapter.executableSha256, executableBytes: installed.pack.adapter.executableBytes });
  const operation = await operationCall(acquireCapabilityPackOperation({ stateRoot: installed.root, pack: installed.verification.pack, kind: "image-admission-inspection", bindingSha256: operationBindingSha256 }));
  if ((await readdir(cleanupRoot)).length !== 0) {
    await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
    fail("capability image admission has cleanup-required state; recover it before another inspection");
  }
  const runtime = { dockerPath: DOCKER_PATH, dockerConfigPath: config, containerName: `pixel-cap-image-${randomBytes(8).toString("hex")}` };
  const commands = buildCapabilityImageAdmissionDockerCommands(installed.pack, capabilityPackSha256(installed.pack), runtime);
  const cleanupPath = join(cleanupRoot, `${runtime.containerName}.json`);
  try { await writeExclusive(cleanupPath, `${JSON.stringify(cleanupIntent(installed, runtime, operation), null, 2)}\n`); await syncDirectory(cleanupRoot); }
  catch (error) { await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation })); throw error; }
  let createAttempted = false, primaryError = null, image, executable;
  try {
    const inspected = await invoke(dependencies, commands.inspectImage, 1024 * 1024, "capability image inspection");
    if (inspected.status !== 0 || inspected.stderr.length !== 0) fail("capability image is not already available by its exact digest");
    let values;
    try { values = JSON.parse(inspected.stdout.toString("utf8")); } catch { fail("capability image inspection is not JSON"); }
    if (!Array.isArray(values) || values.length !== 1) fail("capability image inspection returned an ambiguous image set");
    image = values[0]; validateCapabilityAdapterImage(image, installed.pack, capabilityPackSha256(installed.pack));
    createAttempted = true;
    const createdResult = await invoke(dependencies, commands.createContainer, 65536, "capability image inspection-container creation");
    if (createdResult.status !== 0 || createdResult.stderr.length !== 0 || !/^[a-f0-9]{64}\r?\n$/u.test(createdResult.stdout.toString("ascii"))) fail("capability image inspection container was not created exactly");
    const copied = await invoke(dependencies, commands.copyExecutable, installed.pack.adapter.executableBytes + 2048, "capability executable extraction");
    if (copied.status !== 0 || copied.stderr.length !== 0) fail("capability executable extraction failed closed");
    const payload = extractCapabilityExecutableArchive(copied.stdout, installed.pack.adapter.executablePath, installed.pack.adapter.executableBytes);
    executable = { bytes: payload.length, sha256: sha(payload) };
    if (executable.bytes !== installed.pack.adapter.executableBytes || executable.sha256 !== installed.pack.adapter.executableSha256) fail("capability executable differs from the signed pack");
  } catch (error) { primaryError = error; }
  let cleanupError = null;
  if (createAttempted) {
    try { await proveContainerAbsent(dependencies, commands); } catch (error) { cleanupError = error; }
  }
  if (!cleanupError) { await unlink(cleanupPath); await syncDirectory(cleanupRoot); }
  if (cleanupError) throw cleanupError;
  if (primaryError) { await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation })); throw primaryError; }
  const receipt = admissionReceipt(installed, image, executable, now);
  let destinationCreated = false;
  try {
    await mkdir(destination, { mode: 0o700 });
    destinationCreated = true;
    await writeExclusive(join(destination, "admission.json"), `${JSON.stringify(receipt, null, 2)}\n`);
    await syncDirectory(destination); await syncDirectory(identityRoot);
  } catch (error) {
    if (destinationCreated) { await unlink(join(destination, "admission.json")).catch(() => {}); await rmdir(destination).catch(() => {}); }
    await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
    throw error;
  }
  await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
  return Object.freeze({ schemaVersion: 1, operation: receipt.operation, status: "image-admitted-disabled", pack: { ...receipt.pack }, image: { digest: receipt.image.digest, executableSha256: receipt.image.executableSha256, executableBytes: receipt.image.executableBytes, pulledByPixel: false, executed: false }, enabled: false, health: { ...receipt.health }, authority: { ...authority }, boundary });
}

export async function recoverCapabilityImageCleanup({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, dependencies = {} }) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies) || dependencies.runDocker !== undefined && typeof dependencies.runDocker !== "function") fail("capability image cleanup dependencies are invalid");
  const installed = await loadInstalledCapabilityPack({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: installed.root, id, version, packSha256: installed.verification.pack.packSha256 }));
  if (operation.kind !== "image-admission-inspection") fail("capability image cleanup is not the current pack mutation");
  const root = await cleanupDirectory(installed.root, id, version, installed.verification.pack.packSha256, false);
  const names = (await readdir(root)).sort();
  if (names.some((name) => !name.endsWith(".json") || !NAME_RE.test(name.slice(0, -5)))) fail("capability image cleanup root has no exact recoverable intent");
  let recovered = 0;
  for (const name of names) {
    let intent;
    try { intent = checkedCleanupIntent(JSON.parse((await privateRegular(join(root, name), 512 * 1024, "capability image cleanup intent")).payload.toString("utf8"))); } catch (error) { if (error instanceof CapabilityImageAdmissionError) throw error; fail("capability image cleanup intent is invalid"); }
    if (intent.pack.id !== id || intent.pack.version !== version || intent.pack.packSha256 !== installed.verification.pack.packSha256 || intent.operationToken !== operation.token || intent.operationBindingSha256 !== operation.bindingSha256 || `${intent.containerName}.json` !== name) fail("capability image cleanup intent differs from its installed pack, operation, or filename");
    const config = await privateDirectory(intent.dockerConfigPath, "capability Docker configuration");
    if ((await readdir(config)).length !== 0) fail("capability Docker configuration must remain empty and credential-free");
    const runtime = { dockerPath: DOCKER_PATH, dockerConfigPath: config, containerName: intent.containerName };
    const commands = buildCapabilityImageAdmissionDockerCommands(installed.pack, capabilityPackSha256(installed.pack), runtime);
    await proveContainerAbsent(dependencies, commands);
    await unlink(join(root, name)); await syncDirectory(root); recovered += 1;
  }
  await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-image-cleanup", status: "cleanup-complete", pack: { ...installed.verification.pack }, recovered, residue: { inspectionContainers: false }, enabled: false, authority: { ...authority }, boundary: cleanupBoundary });
}

export async function reviewCapabilityImageRevocation(options) {
  const admitted = await loadCapabilityImageAdmission(options);
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: admitted.installed.root, id: admitted.receipt.pack.id, version: admitted.receipt.pack.version, packSha256: admitted.receipt.pack.packSha256, optional: true }));
  if (operation) fail(`capability image admission has an in-progress ${operation.kind} mutation; recover it before revocation`);
  const review = { schemaVersion: 1, operation: "pixel-work-capability-image-revocation-review", status: "review-required", pack: { ...admitted.receipt.pack }, admissionReceiptSha256: sha(admitted.receipt), effect: { revokePixelAdmission: true, deleteImage: false, stopContainer: false, changeController: false }, enabled: false, authority: { ...authority }, boundary: reviewBoundary };
  return Object.freeze({ ...review, confirmationSha256: sha(review) });
}

function checkedIntent(value) {
  const keys = ["admissionReceiptSha256", "authority", "boundary", "enabled", "operation", "pack", "reviewSha256", "schemaVersion"];
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys) || value.schemaVersion !== 1 || value.operation !== "pixel-work-capability-image-revocation-intent" || !SHA_RE.test(value.reviewSha256 ?? "") || !SHA_RE.test(value.admissionReceiptSha256 ?? "") || canonical(value.authority) !== canonical(authority) || value.boundary !== reviewBoundary || value.enabled !== false) fail("capability image revocation intent is invalid");
  checkedPackBinding(value.pack, "capability image revocation intent");
  return value;
}

function revocationReceipt(intent, now) {
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime())) fail("capability image revocation time is invalid");
  const receipt = { $schema: "https://osmantic.com/pixel/schemas/work-capability-image-revocation-v1.schema.json", schemaVersion: 1, operation: "pixel-work-capability-image-revocation", pack: { ...intent.pack }, admissionReceiptSha256: intent.admissionReceiptSha256, revokedAt: now.toISOString(), enabled: false, image: { ownedByPixel: false, pulledByPixel: false, retainedOnHost: true, executedByAdmission: false }, residue: { admission: false, controllerRegistration: false, healthAuthority: false }, authority: { ...authority }, boundary: revokeBoundary };
  try { assertJsonSchema(receipt, revocationSchema, "capability image revocation"); } catch { fail("capability image revocation receipt is invalid"); }
  return receipt;
}

async function revocationRoots(root, id, create) {
  const base = await privateDirectory(join(root, "capability-pack-image-revocations"), "capability image revocation root", create);
  const progress = await privateDirectory(join(base, "in-progress"), "capability image in-progress revocation root", create);
  const finalized = await privateDirectory(join(base, "finalized"), "capability image finalized revocation root", create);
  const progressId = await privateDirectory(join(progress, id), "capability image revocation identity root", create);
  const finalizedId = await privateDirectory(join(finalized, id), "capability image finalized revocation identity root", create);
  return { base, progress, finalized, progressId, finalizedId };
}

async function finalizeRevocation({ transaction, roots, now }) {
  await privateDirectory(transaction, "capability image revocation transaction");
  const names = (await readdir(transaction)).sort();
  if (canonical(names) !== canonical(["intent.json", "payload"]) && canonical(names) !== canonical(["intent.json", "payload", "revocation.json"])) fail("capability image revocation transaction contains unknown files");
  let intent;
  try { intent = checkedIntent(JSON.parse((await privateRegular(join(transaction, "intent.json"), 512 * 1024, "capability image revocation intent")).payload.toString("utf8"))); } catch (error) { if (error instanceof CapabilityImageAdmissionError) throw error; fail("capability image revocation intent is invalid"); }
  const payload = join(transaction, "payload"); await privateDirectory(payload, "revoked capability image admission");
  if (canonical((await readdir(payload)).sort()) !== canonical(["admission.json"])) fail("revoked capability image admission contains unknown files");
  let admission;
  try { admission = JSON.parse((await privateRegular(join(payload, "admission.json"), 512 * 1024, "revoked capability image admission receipt")).payload.toString("utf8")); } catch { fail("revoked capability image admission receipt is invalid"); }
  try { assertJsonSchema(admission, admissionSchema, "revoked capability image admission"); } catch { fail("revoked capability image admission receipt is invalid"); }
  if (sha(admission) !== intent.admissionReceiptSha256 || canonical(admission.pack) !== canonical(intent.pack)) fail("revoked capability image admission differs from its intent");
  let receipt;
  if (names.includes("revocation.json")) {
    try { receipt = JSON.parse((await privateRegular(join(transaction, "revocation.json"), 512 * 1024, "capability image revocation receipt")).payload.toString("utf8")); } catch { fail("capability image revocation receipt is invalid"); }
    try { assertJsonSchema(receipt, revocationSchema, "capability image revocation"); } catch { fail("capability image revocation receipt is invalid"); }
    if (canonical(receipt) !== canonical(revocationReceipt(intent, new Date(receipt.revokedAt)))) fail("capability image revocation receipt differs from its intent");
  } else {
    receipt = revocationReceipt(intent, now); await writeExclusive(join(transaction, "revocation.json"), `${JSON.stringify(receipt, null, 2)}\n`); await syncDirectory(transaction);
  }
  const destination = join(roots.finalizedId, `${intent.pack.version}-${intent.pack.packSha256}`);
  if (await lstat(destination).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("capability image revocation is already finalized");
  await rename(transaction, destination); await syncDirectory(roots.progressId); await syncDirectory(roots.finalizedId);
  return Object.freeze({ schemaVersion: 1, operation: receipt.operation, status: "image-admission-revoked", pack: { ...receipt.pack }, revocationReceiptSha256: sha(receipt), image: { ...receipt.image }, residue: { ...receipt.residue }, enabled: false, authority: { ...authority }, boundary: revokeBoundary });
}

export async function revokeCapabilityImageAdmission({ stateRoot, id, version, confirmReviewSha256, allowedSignersPath, sshKeygenPath, now = new Date(), dependencies = {} }) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies) || ["afterIntent", "afterMove", "afterFinalize"].some((key) => dependencies[key] !== undefined && typeof dependencies[key] !== "function")) fail("capability image revocation dependencies are invalid");
  const review = await reviewCapabilityImageRevocation({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  if (!SHA_RE.test(confirmReviewSha256 ?? "") || confirmReviewSha256 !== review.confirmationSha256) fail("capability image revocation confirmation differs from the current review");
  const operation = await operationCall(acquireCapabilityPackOperation({ stateRoot, pack: review.pack, kind: "image-admission-revocation", bindingSha256: review.confirmationSha256 }));
  let admitted;
  try { admitted = await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath }); }
  catch (error) { await operationCall(releaseCapabilityPackOperation({ stateRoot, operation })); throw error; }
  if (sha(admitted.receipt) !== review.admissionReceiptSha256) {
    await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
    fail("capability image admission changed after revocation review");
  }
  let roots;
  try { roots = await revocationRoots(admitted.installed.root, id, true); }
  catch (error) { await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation })); throw error; }
  const transaction = join(roots.progressId, `${version}-${review.pack.packSha256}`);
  const intent = { schemaVersion: 1, operation: "pixel-work-capability-image-revocation-intent", reviewSha256: review.confirmationSha256, pack: { ...review.pack }, admissionReceiptSha256: review.admissionReceiptSha256, enabled: false, authority: { ...authority }, boundary: reviewBoundary };
  const staging = join(roots.progressId, `.revocation-${version}-${review.pack.packSha256}-${randomBytes(8).toString("hex")}`);
  let published = false, moved = false;
  try {
    await mkdir(staging, { mode: 0o700 });
    await writeExclusive(join(staging, "intent.json"), `${JSON.stringify(intent, null, 2)}\n`); await syncDirectory(staging);
    await rename(staging, transaction); published = true; await syncDirectory(roots.progressId);
  } catch (error) {
    await unlink(join(staging, "intent.json")).catch(() => {});
    await rmdir(staging).catch(() => {});
    if (published) { await unlink(join(transaction, "intent.json")).catch(() => {}); await rmdir(transaction).catch(() => {}); }
    await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
    throw error;
  }
  if (dependencies.afterIntent) await dependencies.afterIntent();
  try {
    await rename(admitted.directory, join(transaction, "payload")); moved = true;
    await syncDirectory(dirname(admitted.directory)); await syncDirectory(transaction);
  } catch (error) {
    if (!moved) {
      await unlink(join(staging, "intent.json")).catch(() => {});
      await rmdir(staging).catch(() => {});
      if (published) {
        await unlink(join(transaction, "intent.json")).catch(() => {});
        await rmdir(transaction).catch(() => {});
      }
      await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
    }
    throw error;
  }
  if (dependencies.afterMove) await dependencies.afterMove();
  const result = await finalizeRevocation({ transaction, roots, now });
  if (dependencies.afterFinalize) await dependencies.afterFinalize();
  await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
  return result;
}

async function finalizedRevocationResult({ transaction, id, version, packSha256, confirmReviewSha256 }) {
  await privateDirectory(transaction, "capability finalized image revocation transaction");
  if (canonical((await readdir(transaction)).sort()) !== canonical(["intent.json", "payload", "revocation.json"])) fail("capability finalized image revocation transaction contains unknown files");
  let intent, admission, receipt;
  try { intent = checkedIntent(JSON.parse((await privateRegular(join(transaction, "intent.json"), 512 * 1024, "capability image revocation intent")).payload.toString("utf8"))); } catch (error) { if (error instanceof CapabilityImageAdmissionError) throw error; fail("capability image revocation intent is invalid"); }
  if (intent.pack.id !== id || intent.pack.version !== version || intent.pack.packSha256 !== packSha256 || intent.reviewSha256 !== confirmReviewSha256) fail("capability finalized image revocation differs from its recovery binding");
  const payload = await privateDirectory(join(transaction, "payload"), "revoked capability image admission");
  if (canonical((await readdir(payload)).sort()) !== canonical(["admission.json"])) fail("revoked capability image admission contains unknown files");
  try { admission = JSON.parse((await privateRegular(join(payload, "admission.json"), 512 * 1024, "revoked capability image admission receipt")).payload.toString("utf8")); } catch { fail("revoked capability image admission receipt is invalid"); }
  try { assertJsonSchema(admission, admissionSchema, "revoked capability image admission"); } catch { fail("revoked capability image admission receipt is invalid"); }
  if (sha(admission) !== intent.admissionReceiptSha256 || canonical(admission.pack) !== canonical(intent.pack)) fail("revoked capability image admission differs from its intent");
  try { receipt = JSON.parse((await privateRegular(join(transaction, "revocation.json"), 512 * 1024, "capability image revocation receipt")).payload.toString("utf8")); } catch { fail("capability image revocation receipt is invalid"); }
  try { assertJsonSchema(receipt, revocationSchema, "capability image revocation"); } catch { fail("capability image revocation receipt is invalid"); }
  if (canonical(receipt) !== canonical(revocationReceipt(intent, new Date(receipt.revokedAt)))) fail("capability image revocation receipt differs from its intent");
  return Object.freeze({ schemaVersion: 1, operation: receipt.operation, status: "image-admission-revoked", pack: { ...receipt.pack }, revocationReceiptSha256: sha(receipt), image: { ...receipt.image }, residue: { ...receipt.residue }, enabled: false, authority: { ...authority }, boundary: revokeBoundary });
}

async function readRevocationIntent(transaction) {
  try { return checkedIntent(JSON.parse((await privateRegular(join(transaction, "intent.json"), 512 * 1024, "capability image revocation intent")).payload.toString("utf8"))); }
  catch (error) { if (error instanceof CapabilityImageAdmissionError) throw error; fail("capability image revocation intent is invalid"); }
}

async function auditRevocationIdentity(root, id, version) {
  const basePath = join(root, "capability-pack-image-revocations"), baseInfo = await lstat(basePath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!baseInfo) return { recoveryRequired: 0, revoked: 0 };
  const base = await privateDirectory(basePath, "capability image revocation root");
  if (canonical((await readdir(base)).sort()) !== canonical(["finalized", "in-progress"])) fail("capability image revocation root contains unknown entries");
  const progress = await privateDirectory(join(base, "in-progress"), "capability image in-progress revocation root"), finalized = await privateDirectory(join(base, "finalized"), "capability image finalized revocation root");
  let recoveryRequired = 0, revoked = 0;
  const progressIdPath = join(progress, id), progressIdInfo = await lstat(progressIdPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (progressIdInfo) {
    const progressId = await privateDirectory(progressIdPath, "capability image in-progress revocation identity root");
    for (const name of (await readdir(progressId)).sort()) {
      const staged = REVOCATION_STAGE_RE.exec(name), ordinary = VERSION_PACK_RE.exec(name);
      if (!staged && !ordinary) fail("capability image in-progress revocation identity root contains an unsafe entry");
      const transaction = await privateDirectory(join(progressId, name), staged ? "capability image revocation staging transaction" : "capability image revocation transaction"), names = (await readdir(transaction)).sort();
      if (staged) {
        if (names.length !== 0 && canonical(names) !== canonical(["intent.json"])) fail("capability image revocation staging transaction contains unknown files");
        if (names.length) {
          const intent = await readRevocationIntent(transaction);
          if (intent.pack.id !== id || intent.pack.version !== staged[1] || intent.pack.packSha256 !== staged[2]) fail("capability image revocation staging identity differs from its directory");
        }
        if (staged[1] === version) recoveryRequired += 1;
      } else {
        if (![ ["intent.json"], ["intent.json", "payload"], ["intent.json", "payload", "revocation.json"] ].some((value) => canonical(names) === canonical(value))) fail("capability image revocation transaction contains unknown files");
        const intent = await readRevocationIntent(transaction);
        if (intent.pack.id !== id || intent.pack.version !== ordinary[1] || intent.pack.packSha256 !== ordinary[2]) fail("capability image revocation identity differs from its directory");
        if (ordinary[1] === version) recoveryRequired += 1;
      }
    }
  }
  const finalizedIdPath = join(finalized, id), finalizedIdInfo = await lstat(finalizedIdPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (finalizedIdInfo) {
    const finalizedId = await privateDirectory(finalizedIdPath, "capability image finalized revocation identity root");
    for (const name of (await readdir(finalizedId)).sort()) {
      const ordinary = VERSION_PACK_RE.exec(name);
      if (!ordinary) fail("capability image finalized revocation identity root contains an unsafe entry");
      const transaction = join(finalizedId, name), intent = await readRevocationIntent(await privateDirectory(transaction, "capability finalized image revocation transaction"));
      await finalizedRevocationResult({ transaction, id, version: ordinary[1], packSha256: ordinary[2], confirmReviewSha256: intent.reviewSha256 });
      if (ordinary[1] === version) revoked += 1;
    }
  }
  return { recoveryRequired, revoked };
}

export async function recoverCapabilityImageRevocation({ stateRoot, id, version, packSha256, confirmReviewSha256, now = new Date() }) {
  if (!ID_RE.test(id ?? "") || !VERSION_RE.test(version ?? "") || !SHA_RE.test(packSha256 ?? "") || !SHA_RE.test(confirmReviewSha256 ?? "")) fail("capability image revocation recovery binding is invalid");
  const root = await privateDirectory(resolve(stateRoot), "capability state root");
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: root, id, version, packSha256 }));
  if (operation.kind !== "image-admission-revocation" || operation.bindingSha256 !== confirmReviewSha256) fail("capability image revocation recovery differs from its transaction or mutation custody");
  const roots = await revocationRoots(root, id, true);
  const transaction = join(roots.progressId, `${version}-${packSha256}`); let transactionInfo = await lstat(transaction).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!transactionInfo) {
    const finalized = join(roots.finalizedId, `${version}-${packSha256}`), finalizedInfo = await lstat(finalized).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (finalizedInfo) {
      const settled = await finalizedRevocationResult({ transaction: finalized, id, version, packSha256, confirmReviewSha256 });
      await operationCall(releaseCapabilityPackOperation({ stateRoot: root, operation }));
      return settled;
    }
    const stagingPrefix = `.revocation-${version}-${packSha256}-`, stagingNames = (await readdir(roots.progressId)).filter((name) => name.startsWith(stagingPrefix));
    if (stagingNames.length > 1 || stagingNames.some((name) => !new RegExp(`^\\.revocation-${version.replaceAll(".", "\\.")}-${packSha256}-[a-f0-9]{16}$`, "u").test(name))) fail("capability image revocation has ambiguous staging custody");
    if (stagingNames.length === 1) {
      const staging = await privateDirectory(join(roots.progressId, stagingNames[0]), "capability image revocation staging transaction"), names = (await readdir(staging)).sort();
      if (names.length === 0) { await rmdir(staging); await syncDirectory(roots.progressId); }
      else {
        if (canonical(names) !== canonical(["intent.json"])) fail("capability image revocation staging transaction contains unknown files");
        let stagedIntent;
        try { stagedIntent = checkedIntent(JSON.parse((await privateRegular(join(staging, "intent.json"), 512 * 1024, "capability image revocation intent")).payload.toString("utf8"))); } catch (error) { if (error instanceof CapabilityImageAdmissionError) throw error; fail("capability image revocation intent is invalid"); }
        if (stagedIntent.pack.id !== id || stagedIntent.pack.version !== version || stagedIntent.pack.packSha256 !== packSha256 || stagedIntent.reviewSha256 !== confirmReviewSha256) fail("capability image revocation staging differs from its recovery binding");
        await rename(staging, transaction); await syncDirectory(roots.progressId); transactionInfo = await lstat(transaction);
      }
    }
    if (!transactionInfo) {
      const admission = join(root, "capability-pack-image-admissions", id, version); await privateDirectory(admission, "capability image admission directory");
      let receipt;
      try { receipt = JSON.parse((await privateRegular(join(admission, "admission.json"), 512 * 1024, "capability image admission receipt")).payload.toString("utf8")); } catch { fail("capability image admission receipt is invalid"); }
      try { assertJsonSchema(receipt, admissionSchema, "capability image admission"); } catch { fail("capability image admission receipt is invalid"); }
      if (canonical(receipt.pack) !== canonical(operation.pack)) fail("capability image revocation no-change state differs from its mutation custody");
      await operationCall(releaseCapabilityPackOperation({ stateRoot: root, operation }));
      return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-image-revocation-recovery", status: "aborted-no-change", pack: { ...operation.pack }, active: true, enabled: false, authority: { ...authority }, boundary: reviewBoundary });
    }
  }
  await privateDirectory(transaction, "capability image revocation transaction");
  let intent;
  try { intent = checkedIntent(JSON.parse((await privateRegular(join(transaction, "intent.json"), 512 * 1024, "capability image revocation intent")).payload.toString("utf8"))); } catch (error) { if (error instanceof CapabilityImageAdmissionError) throw error; fail("capability image revocation intent is invalid"); }
  if (intent.pack.id !== id || intent.pack.version !== version || intent.pack.packSha256 !== packSha256 || intent.reviewSha256 !== confirmReviewSha256) fail("capability image revocation recovery differs from its transaction");
  if (canonical((await readdir(transaction)).sort()) === canonical(["intent.json"])) {
    const admission = join(root, "capability-pack-image-admissions", id, version);
    await privateDirectory(admission, "capability image admission directory");
    let receipt;
    try { receipt = JSON.parse((await privateRegular(join(admission, "admission.json"), 512 * 1024, "capability image admission receipt")).payload.toString("utf8")); } catch { fail("capability image admission receipt is invalid"); }
    try { assertJsonSchema(receipt, admissionSchema, "capability image admission"); } catch { fail("capability image admission receipt is invalid"); }
    if (sha(receipt) !== intent.admissionReceiptSha256 || canonical(receipt.pack) !== canonical(intent.pack)) fail("capability image admission differs from its revocation intent");
    await rename(admission, join(transaction, "payload")); await syncDirectory(dirname(admission)); await syncDirectory(transaction);
  }
  const result = await finalizeRevocation({ transaction, roots, now });
  await operationCall(releaseCapabilityPackOperation({ stateRoot: root, operation }));
  return result;
}

export async function statusCapabilityImages({ stateRoot, id, version, allowedSignersPath, sshKeygenPath }) {
  const installed = await loadInstalledCapabilityPack({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: installed.root, id, version, packSha256: installed.verification.pack.packSha256, optional: true }));
  const admission = join(installed.root, "capability-pack-image-admissions", id, version);
  const active = await lstat(admission).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  let activeReceipt = null;
  if (active) activeReceipt = (await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath })).receipt;
  const revocations = await auditRevocationIdentity(installed.root, id, version), transactionRecoveryRequired = revocations.recoveryRequired, revoked = revocations.revoked;
  const cleanupPath = join(installed.root, "capability-pack-image-cleanups", id, `${version}-${installed.verification.pack.packSha256}`);
  const cleanupInfo = await lstat(cleanupPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  let cleanupIntents = 0;
  if (cleanupInfo) {
    const cleanupRoot = await privateDirectory(cleanupPath, "capability image cleanup version root"), names = (await readdir(cleanupRoot)).sort(); cleanupIntents = names.length;
    if (names.some((name) => !name.endsWith(".json") || !NAME_RE.test(name.slice(0, -5)))) fail("capability image cleanup root contains an unsafe entry");
    for (const name of names) {
      let intent;
      try { intent = checkedCleanupIntent(JSON.parse((await privateRegular(join(cleanupRoot, name), 512 * 1024, "capability image cleanup intent")).payload.toString("utf8"))); } catch (error) { if (error instanceof CapabilityImageAdmissionError) throw error; fail("capability image cleanup intent is invalid"); }
      if (intent.pack.id !== id || intent.pack.version !== version || intent.pack.packSha256 !== installed.verification.pack.packSha256 || `${intent.containerName}.json` !== name) fail("capability image cleanup intent differs from its installed pack or filename");
    }
  }
  const cleanupRequired = Math.max(cleanupIntents, operation?.kind === "image-admission-inspection" ? 1 : 0);
  const recoveryRequired = Math.max(transactionRecoveryRequired, operation?.kind === "health-probe" || operation?.kind === "tool-execution" || operation?.kind === "image-admission-revocation" || operation?.kind === "pack-removal" ? 1 : 0);
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-image-status", status: cleanupRequired ? "cleanup-required" : recoveryRequired ? "recovery-required" : activeReceipt ? "admitted-disabled" : revoked ? "revoked" : "not-admitted", pack: { ...installed.verification.pack }, admissionReceiptSha256: activeReceipt ? sha(activeReceipt) : null, active: Boolean(activeReceipt), cleanupRequired, recoveryRequired, revoked: Boolean(revoked), mutation: operation ? { kind: operation.kind, bindingSha256: operation.bindingSha256 } : null, enabled: false, health: activeReceipt ? { ...activeReceipt.health } : { status: "unavailable", evidenceSha256: null }, authority: { ...authority }, boundary });
}

export const capabilityImageAdmissionDockerPath = DOCKER_PATH;
