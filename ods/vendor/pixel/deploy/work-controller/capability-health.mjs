import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readFile, readdir, realpath, rename, unlink } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";

import { assertJsonSchema } from "../../scripts/lib/json-schema.mjs";
import { canonical } from "../../scripts/lib/work-contract.mjs";
import { loadCapabilityImageAdmission } from "./capability-image-admission.mjs";
import {
  acquireCapabilityPackOperation, CapabilityPackOperationError,
  loadCapabilityPackOperation, releaseCapabilityPackOperation,
} from "./capability-pack-operation.mjs";
import { buildCapabilityHealthDockerCommands, capabilityPackSha256 } from "./capability-packs.mjs";
import { probeCapabilityMcpWithTrustedLaunch } from "./mcp-stdio-client.mjs";

const DOCKER_PATH = "/usr/bin/docker";
const SHA_RE = /^[a-f0-9]{64}$/u;
const ID_RE = /^[a-z][a-z0-9-]{1,62}$/u;
const VERSION_RE = /^(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})$/u;
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{1,127}$/u;
const ATTEMPT_RE = /^(\d{6})-([a-f0-9]{64})\.json$/u;
const ATTEMPT_STAGE_RE = /^\.attempt-[a-f0-9]{16}$/u;
const authority = Object.freeze({ grantsHealthProbe: false, grantsExecution: false, grantsToolUse: false, grantsDataAccess: false, grantsImagePull: false, grantsNetwork: false, grantsExternalEffects: false, grantsControllerRegistration: false, grantsCompletion: false });
const boundary = "Content-free evidence from one explicitly confirmed, networkless, workspace-free MCP discovery and tools-list probe. The probe made no tool call and the receipt grants no future health probe, execution, tool use, data access, image pull, network, external effect, controller registration, or completion authority.";
const cleanupBoundary = "Cleanup-only custody for one explicitly confirmed capability health container. It grants only force-removal and exact absence inspection for the named container and no image pull, tool call, data access, network, external effect, controller registration, or completion authority.";
const healthSchema = JSON.parse(await readFile(new URL("../../schemas/work-capability-health-v1.schema.json", import.meta.url), "utf8"));

export class CapabilityHealthError extends Error {}
function fail(message) { throw new CapabilityHealthError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
async function operationCall(promise) { try { return await promise; } catch (error) { if (error instanceof CapabilityPackOperationError) fail(error.message); throw error; } }

function timestamp(value, label) {
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`);
  return value;
}

async function privateDirectory(path, label, create = false) {
  if (!isAbsolute(path) || resolve(path) === dirname(resolve(path))) fail(`${label} must be an absolute non-root directory`);
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || await realpath(path) !== resolve(path)) fail(`${label} must be a real unlinked directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-private`);
  return resolve(path);
}

async function privateRegular(path, maximumBytes, label) {
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)); }
  catch { fail(`${label} must be a descriptor-bound regular file`); }
  try {
    const info = await handle.stat(), current = await lstat(path);
    if (!info.isFile() || info.nlink !== 1 || info.size < 1 || info.size > maximumBytes || current.isSymbolicLink() || current.dev !== info.dev || current.ino !== info.ino) fail(`${label} must be a bounded regular single-link file`);
    if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-private`);
    return await handle.readFile();
  } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

async function writeExclusive(path, payload) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(payload); await handle.sync(); } finally { await handle.close(); }
}

async function healthDirectories(admitted, create) {
  const base = await privateDirectory(join(admitted.installed.root, "capability-pack-health"), "capability health root", create);
  const identity = await privateDirectory(join(base, admitted.receipt.pack.id), "capability health identity root", create);
  const version = await privateDirectory(join(identity, `${admitted.receipt.pack.version}-${admitted.receipt.pack.packSha256}`), "capability health version root", create);
  const attempts = await privateDirectory(join(version, "attempts"), "capability health attempts root", create);
  return { base, identity, version, attempts };
}

async function cleanupDirectory(admitted, create) {
  const base = await privateDirectory(join(admitted.installed.root, "capability-pack-health-cleanups"), "capability health cleanup root", create);
  const identity = await privateDirectory(join(base, admitted.receipt.pack.id), "capability health cleanup identity root", create);
  return privateDirectory(join(identity, `${admitted.receipt.pack.version}-${admitted.receipt.pack.packSha256}`), "capability health cleanup version root", create);
}

function validateReceipt(receipt, admitted, previous, name) {
  try { assertJsonSchema(receipt, healthSchema, "capability health evidence"); } catch { fail("capability health evidence is invalid"); }
  if (canonical(receipt.pack) !== canonical(admitted.receipt.pack) || receipt.admissionReceiptSha256 !== sha(admitted.receipt) || receipt.container.reference !== admitted.installed.pack.adapter.imageRef || receipt.container.id !== admitted.installed.pack.adapter.imageId) fail("capability health evidence differs from its admission");
  if (receipt.attempt.sequence !== (previous?.attempt.sequence ?? 0) + 1 || receipt.attempt.previousReceiptSha256 !== (previous ? sha(previous) : null)) fail("capability health evidence chain is invalid");
  if (name !== undefined && name !== `${String(receipt.attempt.sequence).padStart(6, "0")}-${sha(receipt)}.json`) fail("capability health evidence filename is invalid");
  if (Date.parse(receipt.completedAt) < Date.parse(receipt.startedAt)) fail("capability health evidence time is reversed");
  if (receipt.result.status === "passed") {
    if (receipt.result.failureClass !== null || receipt.result.protocolVersion !== "2026-07-28" || !SHA_RE.test(receipt.result.toolSurfaceSha256 ?? "") || receipt.result.frames < 2 || receipt.health.status !== "passing" || receipt.health.consecutiveFailures !== 0 || receipt.container.startState !== "started") fail("passing capability health evidence is incoherent");
  } else {
    const expectedFailures = Math.min((previous?.health.consecutiveFailures ?? 0) + 1, 3);
    if (receipt.result.failureClass === null || receipt.health.consecutiveFailures !== expectedFailures || receipt.health.status !== (expectedFailures >= 3 ? "quarantined" : "failing")) fail("failed capability health evidence is incoherent");
  }
  if (receipt.result.toolCalls !== 0 || receipt.result.dataBytes !== 0 || canonical(receipt.authority) !== canonical(authority) || receipt.enabled !== false || receipt.boundary !== boundary) fail("capability health evidence widens authority");
  return receipt;
}

async function readAttempts(admitted, create = false, allowStaging = false) {
  if (!create) {
    const basePath = join(admitted.installed.root, "capability-pack-health");
    const info = await lstat(basePath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (!info) return { roots: null, receipts: [], latest: null };
  }
  const roots = await healthDirectories(admitted, create), names = (await readdir(roots.attempts)).sort(), stagingNames = names.filter((name) => ATTEMPT_STAGE_RE.test(name)), receiptNames = names.filter((name) => ATTEMPT_RE.test(name));
  if (receiptNames.length > 999999 || receiptNames.length + stagingNames.length !== names.length || stagingNames.length > 1 || stagingNames.length && !allowStaging) fail("capability health attempts root contains an unsafe or recovery-required entry");
  const receipts = [];
  for (const name of receiptNames) {
    let receipt;
    try { receipt = JSON.parse((await privateRegular(join(roots.attempts, name), 512 * 1024, "capability health evidence")).toString("utf8")); }
    catch (error) { if (error instanceof CapabilityHealthError) throw error; fail("capability health evidence is not JSON"); }
    receipts.push(validateReceipt(receipt, admitted, receipts.at(-1) ?? null, name));
  }
  return { roots, receipts, latest: receipts.at(-1) ?? null, stagingNames };
}

function checkedCleanupIntent(value, admitted, operation, name) {
  const keys = ["authority", "boundary", "containerName", "dockerConfigPath", "operationBindingSha256", "operationToken", "pack", "schemaVersion"];
  const expectedAuthority = { removeExactContainer: true, inspectExactContainerAbsence: true, imagePull: false, toolUse: false, dataAccess: false, network: false, externalEffects: false, controllerRegistration: false, completion: false };
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys) || value.schemaVersion !== 1 || canonical(value.pack) !== canonical(admitted.receipt.pack) || !NAME_RE.test(value.containerName ?? "") || `${value.containerName}.json` !== name || !isAbsolute(value.dockerConfigPath ?? "") || value.operationToken !== operation.token || value.operationBindingSha256 !== operation.bindingSha256 || canonical(value.authority) !== canonical(expectedAuthority) || value.boundary !== cleanupBoundary) fail("capability health cleanup intent is invalid");
  return value;
}

function cleanupIntent(admitted, operation, runtime) {
  return {
    schemaVersion: 1, pack: { ...admitted.receipt.pack }, containerName: runtime.containerName, dockerConfigPath: runtime.dockerConfigPath,
    operationToken: operation.token, operationBindingSha256: operation.bindingSha256,
    authority: { removeExactContainer: true, inspectExactContainerAbsence: true, imagePull: false, toolUse: false, dataAccess: false, network: false, externalEffects: false, controllerRegistration: false, completion: false },
    boundary: cleanupBoundary,
  };
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

async function proveAbsent(dependencies, commands) {
  await invoke(dependencies, commands.remove, 65536, "capability health container cleanup");
  const inspected = await invoke(dependencies, commands.inspectAbsent, 65536, "capability health container absence inspection");
  if (inspected.status === 0 || inspected.stdout.toString("ascii").trim().length !== 0) fail("capability health container absence was not proven");
  const listed = await invoke(dependencies, commands.listAbsent, 65536, "capability health container absence listing");
  if (listed.status !== 0 || listed.stderr.length !== 0 || listed.stdout.toString("ascii").trim().length !== 0) fail("capability health container absence was not proven");
}

function failureClass(error) {
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  if (message.includes("runtime ceiling") || message.includes("timeout")) return "timeout";
  if (message.includes("could not be started") || message.includes("launch")) return "launch";
  if (message.includes("server identity") || message.includes("tool surface") || message.includes("schema")) return "identity-or-schema";
  if (message.includes("protocol") || message.includes("malformed") || message.includes("server request") || message.includes("incomplete")) return "protocol";
  return "transport";
}

function resultEvidence(evidence, error, recovered = false) {
  if (evidence) {
    const core = { protocolVersion: evidence.protocolVersion, toolSurfaceSha256: evidence.toolSurfaceSha256, instructionsSha256: evidence.instructionsSha256, frames: evidence.frames, stdoutBytes: evidence.stdoutBytes, stderrBytes: evidence.stderrBytes, stderrSha256: evidence.stderrSha256, toolCalls: 0, dataBytes: 0 };
    return { status: "passed", failureClass: null, ...core, evidenceSha256: sha(core) };
  }
  const kind = recovered ? "uncertain-recovered" : failureClass(error);
  const core = { status: "failed", failureClass: kind, protocolVersion: null, toolSurfaceSha256: null, instructionsSha256: null, frames: 0, stdoutBytes: 0, stderrBytes: 0, stderrSha256: sha(Buffer.alloc(0)), toolCalls: 0, dataBytes: 0 };
  return { ...core, evidenceSha256: sha({ failureClass: kind, toolCalls: 0, dataBytes: 0 }) };
}

async function publishReceipt({ admitted, operation, startedAt, completedAt, result, startState, afterStage }) {
  const history = await readAttempts(admitted, true), previous = history.latest;
  const sequence = (previous?.attempt.sequence ?? 0) + 1;
  if (sequence > 999999) fail("capability health attempt ceiling is exhausted");
  const consecutiveFailures = result.status === "passed" ? 0 : Math.min((previous?.health.consecutiveFailures ?? 0) + 1, 3);
  const receipt = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-health-v1.schema.json", schemaVersion: 1, operation: "pixel-work-capability-health",
    pack: { ...admitted.receipt.pack }, admissionReceiptSha256: sha(admitted.receipt),
    attempt: { sequence, previousReceiptSha256: previous ? sha(previous) : null }, operationCustody: { bindingSha256: operation.bindingSha256, token: operation.token },
    startedAt: startedAt.toISOString(), completedAt: completedAt.toISOString(), result,
    container: { reference: admitted.installed.pack.adapter.imageRef, id: admitted.installed.pack.adapter.imageId, startState, network: "none", workspace: false, removed: true, absenceProven: true },
    health: { status: result.status === "passed" ? "passing" : consecutiveFailures >= 3 ? "quarantined" : "failing", consecutiveFailures, quarantineThreshold: 3 },
    enabled: false, authority: { ...authority }, boundary,
  };
  validateReceipt(receipt, admitted, previous);
  const name = `${String(sequence).padStart(6, "0")}-${sha(receipt)}.json`, temporary = join(history.roots.attempts, `.attempt-${randomBytes(8).toString("hex")}`);
  await writeExclusive(temporary, `${JSON.stringify(receipt, null, 2)}\n`);
  if (afterStage) await afterStage();
  await rename(temporary, join(history.roots.attempts, name)); await syncDirectory(history.roots.attempts);
  return Object.freeze(receipt);
}

function projection(receipt, cleanupRequired = 0) {
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-health-status", status: cleanupRequired ? "cleanup-required" : receipt ? `${receipt.health.status}-disabled` : "not-probed", pack: receipt ? { ...receipt.pack } : null, receiptSha256: receipt ? sha(receipt) : null, attempts: receipt?.attempt.sequence ?? 0, health: receipt ? { ...receipt.health } : { status: "not-probed", consecutiveFailures: 0, quarantineThreshold: 3 }, cleanupRequired, enabled: false, authority: { ...authority }, boundary });
}

function checkedDependencies(dependencies) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies) || ["runDocker", "probeMcp", "afterCleanup", "afterAttemptStage", "afterReceipt"].some((key) => dependencies[key] !== undefined && typeof dependencies[key] !== "function")) fail("capability health dependencies are invalid");
  return dependencies;
}

export async function probeCapabilityHealth({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, dockerConfigPath, now = new Date(), completedAt = new Date(), dependencies = {} }) {
  checkedDependencies(dependencies); const started = timestamp(now, "capability health start time"), completed = timestamp(completedAt, "capability health completion time");
  if (completed.getTime() < started.getTime()) fail("capability health time is reversed");
  const admitted = await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  const history = await readAttempts(admitted, true);
  if (history.latest?.health.status === "quarantined") fail("capability health is quarantined for this signed version");
  const config = await privateDirectory(resolve(dockerConfigPath), "capability health Docker configuration");
  if ((await readdir(config)).length !== 0) fail("capability health Docker configuration must be empty and credential-free");
  const bindingSha256 = sha({ operation: "pixel-work-capability-health", pack: admitted.receipt.pack, admissionReceiptSha256: sha(admitted.receipt), nextSequence: (history.latest?.attempt.sequence ?? 0) + 1 });
  const operation = await operationCall(acquireCapabilityPackOperation({ stateRoot: admitted.installed.root, pack: admitted.receipt.pack, kind: "health-probe", bindingSha256 }));
  const runtime = { dockerPath: DOCKER_PATH, dockerConfigPath: config, containerName: `pixel-cap-health-${randomBytes(8).toString("hex")}`, imageIdentifier: admitted.installed.pack.adapter.imageRef };
  const commands = buildCapabilityHealthDockerCommands({ pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), runtime });
  const cleanupRoot = await cleanupDirectory(admitted, true), cleanupPath = join(cleanupRoot, `${runtime.containerName}.json`);
  try { await writeExclusive(cleanupPath, `${JSON.stringify(cleanupIntent(admitted, operation, runtime), null, 2)}\n`); await syncDirectory(cleanupRoot); }
  catch (error) { await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation })); throw error; }
  let evidence = null, probeError = null;
  try {
    evidence = await (dependencies.probeMcp ?? probeCapabilityMcpWithTrustedLaunch)({ pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), command: commands.run.command, args: commands.run.args, cwd: admitted.installed.root, env: commands.run.env });
  } catch (error) { probeError = error; }
  await proveAbsent(dependencies, commands);
  await unlink(cleanupPath); await syncDirectory(cleanupRoot);
  if (dependencies.afterCleanup) await dependencies.afterCleanup();
  const receipt = await publishReceipt({ admitted, operation, startedAt: started, completedAt: completed, result: resultEvidence(evidence, probeError), startState: evidence ? "started" : "uncertain", afterStage: dependencies.afterAttemptStage });
  if (dependencies.afterReceipt) await dependencies.afterReceipt();
  await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
  return projection(receipt);
}

export async function recoverCapabilityHealth({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, now = new Date(), dependencies = {} }) {
  checkedDependencies(dependencies); const recoveredAt = timestamp(now, "capability health recovery time");
  const admitted = await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: admitted.installed.root, id, version, packSha256: admitted.receipt.pack.packSha256 }));
  if (operation.kind !== "health-probe") fail("capability health recovery is not the current pack mutation");
  const cleanupRoot = await cleanupDirectory(admitted, true), names = (await readdir(cleanupRoot)).sort();
  if (names.length > 1 || names.some((name) => !name.endsWith(".json") || !NAME_RE.test(name.slice(0, -5)))) fail("capability health cleanup state is ambiguous");
  if (names.length === 1) {
    let intent;
    try { intent = checkedCleanupIntent(JSON.parse((await privateRegular(join(cleanupRoot, names[0]), 512 * 1024, "capability health cleanup intent")).toString("utf8")), admitted, operation, names[0]); }
    catch (error) { if (error instanceof CapabilityHealthError) throw error; fail("capability health cleanup intent is invalid"); }
    const config = await privateDirectory(intent.dockerConfigPath, "capability health Docker configuration");
    if ((await readdir(config)).length !== 0) fail("capability health Docker configuration must remain empty and credential-free");
    const runtime = { dockerPath: DOCKER_PATH, dockerConfigPath: config, containerName: intent.containerName, imageIdentifier: admitted.installed.pack.adapter.imageRef };
    await proveAbsent(dependencies, buildCapabilityHealthDockerCommands({ pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), runtime }));
    await unlink(join(cleanupRoot, names[0])); await syncDirectory(cleanupRoot);
  }
  let history = await readAttempts(admitted, true, true), receipt = history.latest;
  if (history.stagingNames.length === 1) {
    const stagedName = history.stagingNames[0], stagedPath = join(history.roots.attempts, stagedName); let staged;
    try { staged = JSON.parse((await privateRegular(stagedPath, 512 * 1024, "staged capability health evidence")).toString("utf8")); }
    catch (error) { if (error instanceof CapabilityHealthError) throw error; fail("staged capability health evidence is not JSON"); }
    validateReceipt(staged, admitted, history.latest);
    if (staged.operationCustody.token !== operation.token || staged.operationCustody.bindingSha256 !== operation.bindingSha256) fail("staged capability health evidence differs from current custody");
    const finalName = `${String(staged.attempt.sequence).padStart(6, "0")}-${sha(staged)}.json`;
    await rename(stagedPath, join(history.roots.attempts, finalName)); await syncDirectory(history.roots.attempts);
    history = await readAttempts(admitted, true); receipt = history.latest;
  }
  if (!receipt || receipt.operationCustody.token !== operation.token || receipt.operationCustody.bindingSha256 !== operation.bindingSha256) {
    receipt = await publishReceipt({ admitted, operation, startedAt: recoveredAt, completedAt: recoveredAt, result: resultEvidence(null, null, true), startState: "uncertain" });
  }
  await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
  return projection(receipt);
}

export async function statusCapabilityHealth({ stateRoot, id, version, allowedSignersPath, sshKeygenPath }) {
  if (!ID_RE.test(id ?? "") || !VERSION_RE.test(version ?? "")) fail("capability health identity is invalid");
  const admitted = await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  const history = await readAttempts(admitted, false, true);
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: admitted.installed.root, id, version, packSha256: admitted.receipt.pack.packSha256, optional: true }));
  const cleanupPath = join(admitted.installed.root, "capability-pack-health-cleanups", id, `${version}-${admitted.receipt.pack.packSha256}`), cleanupInfo = await lstat(cleanupPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  const names = cleanupInfo ? (await readdir(await cleanupDirectory(admitted, false))).sort() : [];
  if (names.some((name) => !name.endsWith(".json") || !NAME_RE.test(name.slice(0, -5)))) fail("capability health cleanup state is unsafe");
  const cleanupRequired = Math.max(names.length, history.stagingNames?.length ?? 0, operation?.kind === "health-probe" ? 1 : 0);
  return projection(history.latest, cleanupRequired);
}

export async function loadPassingCapabilityHealth({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, now = new Date(), maxAgeMs }) {
  const observed = timestamp(now, "capability health eligibility time");
  if (!Number.isSafeInteger(maxAgeMs) || maxAgeMs < 1000 || maxAgeMs > 86400000) fail("capability health maximum age is invalid");
  const status = await statusCapabilityHealth({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  if (status.status !== "passing-disabled" || status.cleanupRequired !== 0) fail("capability health is not passing and cleanup-complete");
  const admitted = await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  const history = await readAttempts(admitted, false, true), receipt = history.latest;
  const completed = Date.parse(receipt?.completedAt ?? "");
  if (!receipt || receipt.health.status !== "passing" || !Number.isFinite(completed) || completed > observed.getTime() || observed.getTime() - completed > maxAgeMs) fail("capability health evidence is missing, future-dated, or stale");
  return Object.freeze({ receipt: Object.freeze(structuredClone(receipt)), receiptSha256: sha(receipt), completedAt: receipt.completedAt });
}

export const capabilityHealthBoundary = boundary;
