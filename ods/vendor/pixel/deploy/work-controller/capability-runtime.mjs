import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readFile, readdir, realpath, rename, unlink } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";

import { assertJsonSchema } from "../../scripts/lib/json-schema.mjs";
import { canonical, validateWorkCapabilityGrant, validateWorkWatchdogDecision } from "../../scripts/lib/work-contract.mjs";
import { loadCapabilityImageAdmission } from "./capability-image-admission.mjs";
import { loadPassingCapabilityHealth, statusCapabilityHealth } from "./capability-health.mjs";
import {
  acquireCapabilityPackOperation, CapabilityPackOperationError,
  loadCapabilityPackOperation, releaseCapabilityPackOperation,
} from "./capability-pack-operation.mjs";
import {
  buildCapabilityAdapterCleanupDockerCommands, buildCapabilityAdapterDockerCommand,
  buildCapabilityWorkspaceDockerCommands, capabilityPackSha256, claimCapabilityGrant,
  loadCapabilityGrantClaim, validateCapabilityWorkspaceVolume,
} from "./capability-packs.mjs";
import { runCapabilityMcpToolWithTrustedLaunch } from "./mcp-stdio-client.mjs";

const DOCKER_PATH = "/usr/bin/docker";
const SHA_RE = /^[a-f0-9]{64}$/u;
const GRANT_RE = /^workcapgrant-[0-9]{13}-[a-f0-9]{12}$/u;
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{1,127}$/u;
const RECEIPT_RE = /^(workcapgrant-[0-9]{13}-[a-f0-9]{12})-([a-f0-9]{64})\.json$/u;
const RECEIPT_STAGE_RE = /^\.receipt-[a-f0-9]{16}$/u;
const authority = Object.freeze({ grantsFutureExecution: false, grantsReplay: false, grantsImagePull: false, grantsNetwork: false, grantsHostAccess: false, grantsCredentials: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsControllerRegistration: false, grantsCompletion: false });
const boundary = "Content-free terminal evidence for one exact single-use capability grant. Structured output crossed only the live caller boundary and is not retained here; the receipt grants no replay, future execution, image pull, network, host access, credential, external effect, scope expansion, controller registration, or completion authority.";
const cleanupBoundary = "Cleanup-only custody for one exact single-use capability execution. It may inspect and remove only the named claim-bound container and disposable workspace and grants no tool replay, image pull, data access, network, host access, credential, external effect, scope expansion, controller registration, or completion authority.";
const runtimeSchema = JSON.parse(await readFile(new URL("../../schemas/work-capability-runtime-v1.schema.json", import.meta.url), "utf8"));

export class CapabilityRuntimeError extends Error {}
function fail(message) { throw new CapabilityRuntimeError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
async function operationCall(promise) { try { return await promise; } catch (error) { if (error instanceof CapabilityPackOperationError) fail(error.message); throw error; } }

function observed(clock, label) {
  if (typeof clock !== "function") fail(`${label} clock is invalid`);
  const value = clock();
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} time is invalid`);
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

async function roots(admitted, create = false) {
  const base = await privateDirectory(join(admitted.installed.root, "capability-pack-runtime"), "capability runtime root", create);
  const identity = await privateDirectory(join(base, admitted.receipt.pack.id), "capability runtime identity root", create);
  const version = await privateDirectory(join(identity, `${admitted.receipt.pack.version}-${admitted.receipt.pack.packSha256}`), "capability runtime version root", create);
  const receipts = await privateDirectory(join(version, "receipts"), "capability runtime receipt root", create);
  const cleanups = await privateDirectory(join(version, "cleanups"), "capability runtime cleanup root", create);
  const cids = await privateDirectory(join(version, "cids"), "capability runtime CID root", create);
  return { base, identity, version, receipts, cleanups, cids };
}

function checkedGrant(grant, admitted) {
  if (validateWorkCapabilityGrant(grant).length || grant.pack.id !== admitted.receipt.pack.id || grant.pack.version !== admitted.receipt.pack.version || grant.pack.packSha256 !== admitted.receipt.pack.packSha256 || grant.pack.treeSha256 !== admitted.receipt.pack.treeSha256) fail("capability runtime grant differs from the admitted pack");
  return grant;
}

function runtimeBinding({ pack, admissionReceiptSha256, healthReceiptSha256, grant, toolName, watchdogDecisionSha256, inputBytes, containerName, workspaceVolumeName, cidFile, dockerConfigPath }) {
  return sha({ operation: "pixel-work-capability-runtime", pack, admissionReceiptSha256, healthReceiptSha256, grantSha256: sha(grant), toolName, watchdogDecisionSha256, inputBytes, containerName, workspaceVolumeName, cidFile, dockerConfigPath });
}

function checkedIntent(value, admitted, operation, name) {
  const keys = ["admissionReceiptSha256", "boundary", "cidFile", "containerName", "dockerConfigPath", "grant", "healthReceiptSha256", "inputBytes", "operationBindingSha256", "operationToken", "pack", "schemaVersion", "toolName", "watchdogDecisionSha256", "workspaceVolumeName"];
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys) || value.schemaVersion !== 1 || value.boundary !== cleanupBoundary || canonical(value.pack) !== canonical(admitted.receipt.pack) || value.admissionReceiptSha256 !== sha(admitted.receipt) || !SHA_RE.test(value.healthReceiptSha256 ?? "") || !GRANT_RE.test(value.grant?.grantId ?? "") || `${value.grant.grantId}.json` !== name || !NAME_RE.test(value.containerName ?? "") || !isAbsolute(value.cidFile ?? "") || value.cidFile !== join(admitted.installed.root, "capability-pack-runtime", admitted.receipt.pack.id, `${admitted.receipt.pack.version}-${admitted.receipt.pack.packSha256}`, "cids", `${value.grant.grantId}.cid`) || !isAbsolute(value.dockerConfigPath ?? "") || value.operationBindingSha256 !== operation.bindingSha256 || value.operationToken !== operation.token || !SHA_RE.test(value.watchdogDecisionSha256 ?? "") || !Number.isSafeInteger(value.inputBytes) || value.inputBytes < 0 || value.inputBytes > 1048576 || typeof value.toolName !== "string" || value.workspaceVolumeName !== null && !NAME_RE.test(value.workspaceVolumeName ?? "")) fail("capability runtime cleanup intent is invalid");
  checkedGrant(value.grant, admitted);
  if (!value.grant.tools.includes(value.toolName) || (admitted.installed.pack.adapter.workspace === "disposable-read-write") !== (value.workspaceVolumeName !== null)) fail("capability runtime cleanup intent widens its grant");
  if (runtimeBinding(value) !== operation.bindingSha256) fail("capability runtime cleanup intent differs from operation custody");
  return value;
}

function validateReceipt(receipt, admitted, name) {
  try { assertJsonSchema(receipt, runtimeSchema, "capability runtime receipt"); } catch { fail("capability runtime receipt is invalid"); }
  if (canonical(receipt.pack) !== canonical(admitted.receipt.pack) || receipt.admissionReceiptSha256 !== sha(admitted.receipt) || receipt.container.reference !== admitted.installed.pack.adapter.imageRef || receipt.container.id !== admitted.installed.pack.adapter.imageId || canonical(receipt.authority) !== canonical(authority) || receipt.enabled !== false || receipt.boundary !== boundary) fail("capability runtime receipt differs from its admission or authority boundary");
  if (Date.parse(receipt.completedAt) < Date.parse(receipt.startedAt) || name !== undefined && name !== `${receipt.grant.grantId}-${sha(receipt)}.json`) fail("capability runtime receipt time or filename is invalid");
  if (receipt.result.status === "succeeded" && (receipt.result.failureClass !== null || receipt.result.protocolVersion !== "2026-07-28" || !SHA_RE.test(receipt.result.toolSurfaceSha256 ?? "") || receipt.result.frames < 3 || receipt.container.startState !== "started")) fail("successful capability runtime receipt is incoherent");
  if (receipt.result.status !== "succeeded" && (receipt.result.failureClass === null || receipt.result.outputBytes !== 0)) fail("failed capability runtime receipt is incoherent");
  return receipt;
}

async function readReceipts(admitted, create = false, allowStage = false) {
  if (!create) {
    const base = join(admitted.installed.root, "capability-pack-runtime");
    if (!(await lstat(base).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error)))) return { place: null, receipts: [], stages: [] };
  }
  const place = await roots(admitted, create), names = (await readdir(place.receipts)).sort();
  const stages = names.filter((name) => RECEIPT_STAGE_RE.test(name)), finals = names.filter((name) => RECEIPT_RE.test(name));
  if (finals.length > 999999 || finals.length + stages.length !== names.length || stages.length > 1 || stages.length && !allowStage) fail("capability runtime receipt root contains unsafe or recovery-required entries");
  const receipts = [];
  for (const name of finals) {
    let receipt;
    try { receipt = JSON.parse((await privateRegular(join(place.receipts, name), 512 * 1024, "capability runtime receipt")).toString("utf8")); } catch (error) { if (error instanceof CapabilityRuntimeError) throw error; fail("capability runtime receipt is not JSON"); }
    validateReceipt(receipt, admitted, name);
    if (receipts.some((entry) => entry.grant.grantId === receipt.grant.grantId)) fail("capability runtime contains duplicate terminal receipts");
    receipts.push(receipt);
  }
  return { place, receipts, stages };
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

function parseInspect(result, label) {
  if (result.status !== 0) {
    const absence = result.stdout.toString("ascii").trim();
    if (absence !== "" && absence !== "[]") fail(`${label} returned ambiguous absence evidence`);
    return null;
  }
  if (result.stderr.length !== 0) fail(`${label} returned unexpected diagnostic output`);
  let value;
  try { value = JSON.parse(result.stdout.toString("utf8")); } catch { fail(`${label} returned invalid JSON`); }
  if (!Array.isArray(value) || value.length !== 1 || !value[0] || typeof value[0] !== "object") fail(`${label} returned an ambiguous object set`);
  return value[0];
}

function validateOwnedContainer(value, admitted, claim, runtime) {
  if (value.Name !== `/${runtime.containerName}` || value.Image !== admitted.installed.pack.adapter.imageId || value.Config?.Image !== admitted.installed.pack.adapter.imageRef || value.Config?.Labels?.["com.osmantic.pixel.work-capability-claim"] !== claim.claimId || value.Config?.Labels?.["com.osmantic.pixel.work-job"] !== claim.jobId || value.Config?.Labels?.["com.osmantic.pixel.work-capability-pack"] !== admitted.installed.pack.id || value.HostConfig?.NetworkMode !== "none" || value.HostConfig?.Privileged !== false || value.HostConfig?.ReadonlyRootfs !== true) fail("capability runtime refuses to remove a container outside exact claim custody");
}

async function proveContainerAbsent(dependencies, admitted, grant, claim, runtime) {
  const commands = buildCapabilityAdapterCleanupDockerCommands({ pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), grant, claim, runtime });
  const owned = parseInspect(await invoke(dependencies, commands.inspectOwned, 512 * 1024, "capability runtime container ownership inspection"), "capability runtime container ownership inspection");
  if (owned) validateOwnedContainer(owned, admitted, claim, runtime);
  if (owned) await invoke(dependencies, commands.remove, 65536, "capability runtime container removal");
  const absent = await invoke(dependencies, commands.inspectAbsent, 65536, "capability runtime container absence inspection");
  if (absent.status === 0 || absent.stdout.toString("ascii").trim().length !== 0) fail("capability runtime container absence was not proven");
  const listed = await invoke(dependencies, commands.listAbsent, 65536, "capability runtime container absence listing");
  if (listed.status !== 0 || listed.stderr.length !== 0 || listed.stdout.toString("ascii").trim().length !== 0) fail("capability runtime container absence was not proven");
}

async function createWorkspace(dependencies, admitted, grant, claim, runtime) {
  if (runtime.workspaceVolumeName === null) return;
  const commands = buildCapabilityWorkspaceDockerCommands({ pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), grant, claim, runtime });
  const created = await invoke(dependencies, commands.create, 65536, "capability runtime workspace creation");
  if (created.status !== 0 || created.stderr.length !== 0 || created.stdout.toString("utf8").trim() !== runtime.workspaceVolumeName) fail("capability runtime workspace creation was not exact");
  const volume = parseInspect(await invoke(dependencies, commands.inspectOwned, 512 * 1024, "capability runtime workspace inspection"), "capability runtime workspace inspection");
  if (!volume) fail("capability runtime workspace was not created");
  validateCapabilityWorkspaceVolume(volume, admitted.installed.pack, grant, claim, runtime);
}

async function proveWorkspaceAbsent(dependencies, admitted, grant, claim, runtime) {
  if (runtime.workspaceVolumeName === null) return;
  const commands = buildCapabilityWorkspaceDockerCommands({ pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), grant, claim, runtime });
  const owned = parseInspect(await invoke(dependencies, commands.inspectOwned, 512 * 1024, "capability runtime workspace ownership inspection"), "capability runtime workspace ownership inspection");
  if (owned) validateCapabilityWorkspaceVolume(owned, admitted.installed.pack, grant, claim, runtime);
  if (owned) await invoke(dependencies, commands.remove, 65536, "capability runtime workspace removal");
  const absent = await invoke(dependencies, commands.inspectAbsent, 65536, "capability runtime workspace absence inspection");
  const absence = absent.stdout.toString("ascii").trim();
  if (absent.status === 0 || absence !== "" && absence !== "[]") fail("capability runtime workspace absence was not proven");
  const listed = await invoke(dependencies, commands.listAbsent, 65536, "capability runtime workspace absence listing");
  if (listed.status !== 0 || listed.stderr.length !== 0 || listed.stdout.toString("ascii").trim().length !== 0) fail("capability runtime workspace absence was not proven");
}

async function removeCid(path) {
  const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!info) return;
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > 256) fail("capability runtime CID file is unsafe");
  await unlink(path); await syncDirectory(dirname(path));
}

function classify(error, recovered = false) {
  if (recovered) return "uncertain-recovered";
  const message = error instanceof Error ? error.message.toLowerCase() : "";
  if (message.includes("runtime ceiling") || message.includes("timeout")) return "timeout";
  if (message.includes("could not be started") || message.includes("launch") || message.includes("workspace creation")) return "launch";
  if (message.includes("server identity") || message.includes("tool surface") || message.includes("schema")) return "identity-or-schema";
  if (message.includes("bounded error") || message.includes("tool failed")) return "tool-failure";
  if (message.includes("output schema") || message.includes("structured output") || message.includes("differs from structured")) return "output-validation";
  if (message.includes("protocol") || message.includes("malformed") || message.includes("server request") || message.includes("incomplete")) return "protocol";
  return "transport";
}

function resultEvidence(evidence, error, recovered = false) {
  if (evidence) return { status: "succeeded", failureClass: null, protocolVersion: evidence.protocolVersion, toolSurfaceSha256: evidence.toolSurfaceSha256, frames: evidence.frames, stdoutBytes: evidence.stdoutBytes, stderrBytes: evidence.stderrBytes, stderrSha256: evidence.stderrSha256, outputBytes: Buffer.byteLength(canonical(evidence.structuredContent)), externalEffects: false, authority: "none" };
  return { status: recovered ? "uncertain-recovered" : "failed", failureClass: classify(error, recovered), protocolVersion: null, toolSurfaceSha256: null, frames: 0, stdoutBytes: 0, stderrBytes: 0, stderrSha256: sha(Buffer.alloc(0)), outputBytes: 0, externalEffects: false, authority: "none" };
}

function receiptFor({ admitted, healthReceiptSha256, grant, claim, operation, toolName, inputBytes, watchdogDecisionSha256, startedAt, completedAt, result }) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-runtime-v1.schema.json", schemaVersion: 1, operation: "pixel-work-capability-runtime",
    pack: { ...admitted.receipt.pack }, admissionReceiptSha256: sha(admitted.receipt), healthReceiptSha256,
    grant: { grantId: grant.grantId, grantSha256: sha(grant), jobId: grant.jobId, checkpointSha256: grant.checkpointSha256, dataClassification: grant.dataClassification, tool: toolName },
    claim: { claimId: claim.claimId, claimSha256: sha(claim) }, operationCustody: { bindingSha256: operation.bindingSha256, token: operation.token },
    request: { inputBytes, watchdogDecisionSha256 }, startedAt: startedAt.toISOString(), completedAt: completedAt.toISOString(), result,
    container: { reference: admitted.installed.pack.adapter.imageRef, id: admitted.installed.pack.adapter.imageId, startState: result.status === "succeeded" ? "started" : "uncertain", network: "none", removed: true, absenceProven: true },
    workspace: { used: admitted.installed.pack.adapter.workspace === "disposable-read-write", removed: true, absenceProven: true }, enabled: false, authority: { ...authority }, boundary,
  };
}

async function publishReceipt({ admitted, receipt, afterStage }) {
  const history = await readReceipts(admitted, true);
  if (history.receipts.some((entry) => entry.grant.grantId === receipt.grant.grantId)) fail("capability runtime grant already has terminal evidence");
  validateReceipt(receipt, admitted);
  const temporary = join(history.place.receipts, `.receipt-${randomBytes(8).toString("hex")}`), destination = join(history.place.receipts, `${receipt.grant.grantId}-${sha(receipt)}.json`);
  await writeExclusive(temporary, `${JSON.stringify(receipt, null, 2)}\n`);
  if (afterStage) await afterStage();
  await rename(temporary, destination); await syncDirectory(history.place.receipts);
  return Object.freeze(receipt);
}

function projection(receipt, structuredContent) {
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-runtime-result", status: receipt.result.status === "succeeded" ? "succeeded-disabled" : `${receipt.result.status}-contained`, pack: { ...receipt.pack }, grantId: receipt.grant.grantId, receiptSha256: sha(receipt), dataClassification: receipt.grant.dataClassification, ...(structuredContent === undefined ? {} : { structuredContent, contentSha256: sha(structuredContent) }), enabled: false, authority: { ...authority }, boundary });
}

function checkedDependencies(dependencies) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies) || ["runDocker", "runMcp", "afterIntent", "afterClaim", "afterCleanup", "afterReceiptStage", "afterReceipt"].some((key) => dependencies[key] !== undefined && typeof dependencies[key] !== "function")) fail("capability runtime dependencies are invalid");
  return dependencies;
}

export async function executeCapabilityTool({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, dockerConfigPath, grant, toolName, input, preflightDecision, maxHealthAgeMs, claimSuffix = randomBytes(6).toString("hex"), clock = () => new Date(), dependencies = {} }) {
  checkedDependencies(dependencies);
  const startedAt = observed(clock, "capability runtime start"), admitted = await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  checkedGrant(grant, admitted);
  if (!grant.tools.includes(toolName) || validateWorkWatchdogDecision(preflightDecision).length) fail("capability runtime request is not bound to a granted tool and valid watchdog decision");
  const inputBytes = Buffer.byteLength(canonical(input));
  if (inputBytes > grant.limits.maxInputBytes) fail("capability runtime input exceeds its grant");
  if (typeof dockerConfigPath !== "string" || resolve(dockerConfigPath) !== dockerConfigPath) fail("capability runtime Docker configuration path must be absolute");
  const config = await privateDirectory(dockerConfigPath, "capability runtime Docker configuration");
  if ((await readdir(config)).length !== 0) fail("capability runtime Docker configuration must be empty and credential-free");
  const health = await loadPassingCapabilityHealth({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, now: startedAt, maxAgeMs: maxHealthAgeMs });
  const place = await roots(admitted, true), containerName = `pixel-cap-run-${randomBytes(8).toString("hex")}`, workspaceVolumeName = admitted.installed.pack.adapter.workspace === "disposable-read-write" ? `pixel-cap-work-${randomBytes(8).toString("hex")}` : null;
  const runtime = { dockerPath: DOCKER_PATH, dockerConfigPath: config, cidFile: join(place.cids, `${grant.grantId}.cid`), containerName, workspaceVolumeName, imageIdentifier: admitted.installed.pack.adapter.imageRef };
  const watchdogDecisionSha256 = sha(preflightDecision), bindingSha256 = runtimeBinding({ pack: admitted.receipt.pack, admissionReceiptSha256: sha(admitted.receipt), healthReceiptSha256: health.receiptSha256, grant, toolName, watchdogDecisionSha256, inputBytes, containerName, workspaceVolumeName, cidFile: runtime.cidFile, dockerConfigPath: config });
  const operation = await operationCall(acquireCapabilityPackOperation({ stateRoot: admitted.installed.root, pack: admitted.receipt.pack, kind: "tool-execution", bindingSha256 }));
  try {
    const currentHealth = await loadPassingCapabilityHealth({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, now: startedAt, maxAgeMs: maxHealthAgeMs });
    if (currentHealth.receiptSha256 !== health.receiptSha256) fail("capability health changed before runtime custody");
  } catch (error) { await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation })); throw error; }
  const intent = { schemaVersion: 1, pack: { ...admitted.receipt.pack }, admissionReceiptSha256: sha(admitted.receipt), healthReceiptSha256: health.receiptSha256, grant: structuredClone(grant), toolName, inputBytes, watchdogDecisionSha256, containerName, workspaceVolumeName, cidFile: runtime.cidFile, dockerConfigPath: config, operationBindingSha256: operation.bindingSha256, operationToken: operation.token, boundary: cleanupBoundary };
  const intentPath = join(place.cleanups, `${grant.grantId}.json`);
  try { await writeExclusive(intentPath, `${JSON.stringify(intent, null, 2)}\n`); await syncDirectory(place.cleanups); }
  catch (error) { await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation })); throw error; }
  if (dependencies.afterIntent) await dependencies.afterIntent();
  let claimed;
  try { claimed = await claimCapabilityGrant({ stateRoot: admitted.installed.root, pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), grant, now: observed(clock, "capability runtime claim"), suffix: claimSuffix }); }
  catch (error) { await unlink(intentPath); await syncDirectory(place.cleanups); await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation })); throw error; }
  if (dependencies.afterClaim) await dependencies.afterClaim();
  const claim = claimed.claim;
  let evidence = null, executionError = null;
  try {
    await createWorkspace(dependencies, admitted, grant, claim, runtime);
    const command = buildCapabilityAdapterDockerCommand({ pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), grant, claim, runtime });
    evidence = await (dependencies.runMcp ?? runCapabilityMcpToolWithTrustedLaunch)({ stateRoot: admitted.installed.root, pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), grant, claim, toolName, input, preflightDecision, command: command.command, args: command.args, cwd: admitted.installed.root, env: command.env, now: observed(clock, "capability runtime execution") });
  } catch (error) { executionError = error; }
  await proveContainerAbsent(dependencies, admitted, grant, claim, runtime);
  await proveWorkspaceAbsent(dependencies, admitted, grant, claim, runtime);
  await removeCid(runtime.cidFile);
  if (dependencies.afterCleanup) await dependencies.afterCleanup();
  const completedAt = observed(clock, "capability runtime completion");
  if (completedAt.getTime() < startedAt.getTime()) fail("capability runtime time is reversed");
  const receipt = await publishReceipt({ admitted, receipt: receiptFor({ admitted, healthReceiptSha256: health.receiptSha256, grant, claim, operation, toolName, inputBytes, watchdogDecisionSha256, startedAt, completedAt, result: resultEvidence(evidence, executionError) }), afterStage: dependencies.afterReceiptStage });
  if (dependencies.afterReceipt) await dependencies.afterReceipt();
  await unlink(intentPath); await syncDirectory(place.cleanups);
  await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
  return projection(receipt, evidence?.structuredContent);
}

async function readIntent(admitted, operation, place) {
  const names = (await readdir(place.cleanups)).sort();
  if (names.length > 1 || names.some((name) => !GRANT_RE.test(name.slice(0, -5)) || !name.endsWith(".json"))) fail("capability runtime cleanup custody is ambiguous");
  if (names.length === 0) return null;
  let intent;
  try { intent = JSON.parse((await privateRegular(join(place.cleanups, names[0]), 512 * 1024, "capability runtime cleanup intent")).toString("utf8")); } catch (error) { if (error instanceof CapabilityRuntimeError) throw error; fail("capability runtime cleanup intent is not JSON"); }
  return { intent: checkedIntent(intent, admitted, operation, names[0]), path: join(place.cleanups, names[0]) };
}

async function finalizeStagedReceipt(admitted, operation, history) {
  if (history.stages.length === 0) return null;
  const path = join(history.place.receipts, history.stages[0]); let receipt;
  try { receipt = JSON.parse((await privateRegular(path, 512 * 1024, "staged capability runtime receipt")).toString("utf8")); } catch (error) { if (error instanceof CapabilityRuntimeError) throw error; fail("staged capability runtime receipt is not JSON"); }
  validateReceipt(receipt, admitted);
  if (receipt.operationCustody.token !== operation.token || receipt.operationCustody.bindingSha256 !== operation.bindingSha256) fail("staged capability runtime receipt differs from current custody");
  if (history.receipts.some((entry) => entry.grant.grantId === receipt.grant.grantId)) fail("staged capability runtime receipt duplicates terminal evidence");
  await rename(path, join(history.place.receipts, `${receipt.grant.grantId}-${sha(receipt)}.json`)); await syncDirectory(history.place.receipts);
  return receipt;
}

export async function recoverCapabilityRuntime({ stateRoot, id, version, allowedSignersPath, sshKeygenPath, clock = () => new Date(), dependencies = {} }) {
  checkedDependencies(dependencies); const admitted = await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: admitted.installed.root, id, version, packSha256: admitted.receipt.pack.packSha256 }));
  if (operation.kind !== "tool-execution") fail("capability runtime recovery is not the current pack operation");
  const place = await roots(admitted, true), history = await readReceipts(admitted, true, true);
  let terminal = history.receipts.find((receipt) => receipt.operationCustody.token === operation.token && receipt.operationCustody.bindingSha256 === operation.bindingSha256) ?? await finalizeStagedReceipt(admitted, operation, history);
  const held = await readIntent(admitted, operation, place);
  if (!held) {
    if (!terminal) {
      await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
      return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-runtime-recovery", status: "aborted-before-custody", enabled: false, authority: { ...authority }, boundary });
    }
    await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
    return projection(terminal);
  }
  const { intent } = held, health = await statusCapabilityHealth({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  if (health.receiptSha256 !== intent.healthReceiptSha256) fail("capability runtime recovery health binding changed");
  const config = await privateDirectory(intent.dockerConfigPath, "capability runtime Docker configuration");
  if ((await readdir(config)).length !== 0) fail("capability runtime Docker configuration must remain empty and credential-free");
  const grant = checkedGrant(intent.grant, admitted), claim = await loadCapabilityGrantClaim({ stateRoot: admitted.installed.root, pack: admitted.installed.pack, expectedPackSha256: capabilityPackSha256(admitted.installed.pack), grant, optional: true });
  if (!claim) {
    await removeCid(intent.cidFile); await unlink(held.path); await syncDirectory(place.cleanups);
    await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
    return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-runtime-recovery", status: "aborted-before-claim", grantId: grant.grantId, enabled: false, authority: { ...authority }, boundary });
  }
  const runtime = { dockerPath: DOCKER_PATH, dockerConfigPath: config, cidFile: intent.cidFile, containerName: intent.containerName, workspaceVolumeName: intent.workspaceVolumeName, imageIdentifier: admitted.installed.pack.adapter.imageRef };
  await proveContainerAbsent(dependencies, admitted, grant, claim, runtime); await proveWorkspaceAbsent(dependencies, admitted, grant, claim, runtime); await removeCid(runtime.cidFile);
  if (!terminal) {
    const recoveredAt = observed(clock, "capability runtime recovery");
    terminal = await publishReceipt({ admitted, receipt: receiptFor({ admitted, healthReceiptSha256: intent.healthReceiptSha256, grant, claim, operation, toolName: intent.toolName, inputBytes: intent.inputBytes, watchdogDecisionSha256: intent.watchdogDecisionSha256, startedAt: recoveredAt, completedAt: recoveredAt, result: resultEvidence(null, null, true) }) });
  }
  await unlink(held.path); await syncDirectory(place.cleanups);
  await operationCall(releaseCapabilityPackOperation({ stateRoot: admitted.installed.root, operation }));
  return projection(terminal);
}

export async function statusCapabilityRuntime({ stateRoot, id, version, allowedSignersPath, sshKeygenPath }) {
  const admitted = await loadCapabilityImageAdmission({ stateRoot, id, version, allowedSignersPath, sshKeygenPath }), history = await readReceipts(admitted, false, true);
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: admitted.installed.root, id, version, packSha256: admitted.receipt.pack.packSha256, optional: true }));
  let cleanupRequired = 0;
  if (history.place) {
    const names = (await readdir(history.place.cleanups)).sort();
    if (names.length > 1 || names.some((name) => !name.endsWith(".json") || !GRANT_RE.test(name.slice(0, -5)))) fail("capability runtime cleanup root contains an unsafe entry");
    if (names.length && operation?.kind !== "tool-execution") fail("capability runtime cleanup custody lacks its exact operation");
    if (names.length) await readIntent(admitted, operation, history.place);
    if (history.stages.length && operation?.kind !== "tool-execution") fail("staged capability runtime evidence lacks its exact operation");
    cleanupRequired = Math.max(names.length, history.stages.length, operation?.kind === "tool-execution" ? 1 : 0);
  } else if (operation?.kind === "tool-execution") cleanupRequired = 1;
  const latest = history.receipts.at(-1) ?? null;
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-runtime-status", status: cleanupRequired ? "cleanup-required" : "disabled", receipts: history.receipts.length, latest: latest ? { grantId: latest.grant.grantId, result: latest.result.status, receiptSha256: sha(latest) } : null, cleanupRequired, enabled: false, authority: { ...authority }, boundary });
}

export const capabilityRuntimeBoundary = boundary;
