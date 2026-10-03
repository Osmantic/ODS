import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readFile, readdir, rename, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { isIP } from "node:net";
import { fileURLToPath } from "node:url";

import {
  canonical, validateWorkCapabilityJobAuthorizationV2, validateWorkCapabilityOperationalGrantV2,
  validateWorkCapabilityOperationalRuntimeResultV2, validateWorkCapabilityQueueRequestV2,
  validateWorkCapabilityQueueResponseV2, validateWorkCapabilityPackV2, validateWorkCapabilityControllerPolicyV2,
  validateWorkCapabilitySshApprovalV2, validateWorkPlan, validateWorkLease, validateWorkConsumption, validateWorkCheckpoint,
} from "../../scripts/lib/work-contract.mjs";
import { capabilityPackSha256 } from "./capability-packs.mjs";
import { compileCapabilityOperationalV2, compileCapabilityOperationalV2SshHostname, compileCapabilityOperationalV2PublicRetrieval } from "./capability-controller.mjs";
import { verifyCapabilitySshApprovalV2 } from "./capability-ssh-approval-v2.mjs";
import { executeRuntimeDispatch } from "./capability-runtime-dispatch.mjs";
import { readOwnerPrivateBoundedFile, writeOwnerPrivateCreateNoClobber, fsyncDirectory } from "./maintenance-secure-files.mjs";
import {
  capabilityOperationalV2ReceiptOperation, capabilityOperationalV2CustodyOperation,
  capabilityOperationalV2ReceiptBoundary, capabilityOperationalV2CustodyBoundary,
  capabilityOperationalV2ReceiptKeys, capabilityOperationalV2SettledCustodyKeys,
  operationalBindingSha256, validateOperationalSettledCustody, validateOperationalReceipt,
} from "./capability-runtime-operational-v2.mjs";

const REQUEST_RE = /^workcaprequest-[0-9]{13}-[a-f0-9]{12}$/u;
const OPERATION_RE = /^workcapv2-[0-9]{13}-[a-f0-9]{16}$/u;
const QUEUE_ID_RE = /^workcapqueuev2-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const RECORD_RE = /^(0{0,6}[0-9]{1,7})\.json$/u;
const MAX_QUEUE = 64;
const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const MAX_RECORD_BYTES = 64 * 1024;
const MAX_RESPONSE_BYTES = 18 * 1024 * 1024;
const MAX_OPERATIONAL_RECEIPT_BYTES = 64 * 1024;

const authority = Object.freeze({ grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const responseAuthority = Object.freeze({ grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const operationalResultBoundary = "Honest operational v2 runtime result for one exact executed job-workspace file effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion.";
const configBoundary = "Controller-owned immutable v2 queue configuration. It binds the exact signed v2 pack SHA/tree/signer, v2 policy, plan, consumed lease, running checkpoint, job, limits/budgets/watchdog, trusted workspace root/state root, and controller compiler path, and the exact controller-owned enabled boolean. The config alone grants no execution, credential, network, external-effect, scope-expansion, or completion authority.";
const responseBoundary = "Private job-scoped v2 queue response for one durably reconciled controller-admitted operation. Structured content is untrusted data and grants no replay, future execution, credential, network, external-effect, scope-expansion, or completion authority.";
const custodyBoundary = "Private content-free single-winner v2 queue custody chain for one controller-admitted operation. It proves one claimant acquired the request before any effect and grants no replay, future execution, credential, network, external-effect, scope-expansion, or completion authority.";
const decisionBoundary = "Private exact controller decision for one claimed v2 queue request. The runtime may consume only its one single-use expiring operational grant; the decision grants no replay, scope expansion, external effect, credential, or completion.";
const sshApprovalBoundary = "Fresh owner signature for one exact single-use SSH forced-command request. It grants one connection to a trusted destination alias using a separately custodied credential and grants no generic shell, command or destination selection, credential disclosure, replay, external effect, scope expansion, or completion.";
const sshSignatureBoundary = "Private verified owner-signature submission for one exact durable SSH approval proposal. It grants no execution by itself, credential disclosure, generic shell, command or destination selection, replay, external effect, scope expansion, or completion.";
const sshApprovalAuthority = Object.freeze({ grantsOneSshConnection: true, grantsGenericShell: false, grantsCommandSelection: false, grantsDestinationSelection: false, grantsCredentialDisclosure: false, grantsReplay: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const SSH_APPROVAL_LIFETIME_MS = 5 * 60 * 1000;
const COMPILER_PATH = fileURLToPath(new URL("./capability-controller.mjs", import.meta.url));

// The ONLY typed collision outcome emitted by the atomic claim primitive. It is
// produced exclusively for EEXIST/ELOOP (a pre-existing or concurrent winner);
// no other failure may ever carry this marker.
const CLAIM_COLLISION = Symbol("capabilityV2QueueClaimCollision");
// The operational runtime is the single producer of the durable custody/receipt
// contract. These imported constants/helpers are the SAME immutable values the
// runtime creates and validates with, so the queue can never drift from the
// producer's exact strings, boundaries, binding digest, and record shapes.

// Production-isolation boundary (do not overclaim): owner-only mode (0o600/0o700)
// and singular-file checks are enforced against the effective uid of the running
// process, and the queue holds no credential/network/external-effect authority.
// That is NOT a sandbox against another process that runs as the same dedicated
// service user. Full cross-user isolation still depends on the dedicated
// service-user boundary (a non-shared OS account for this controller), which
// remains an operational deployment requirement and is not guaranteed here.

export class CapabilityToolQueueV2Error extends Error {}

function fail(message, cause) { throw new CapabilityToolQueueV2Error(message, cause === undefined ? undefined : { cause }); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} is invalid: ${errors[0]}`); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function observed(clock, label) {
  const value = clock();
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} time is invalid`);
  return value;
}
function monotonicProductionClock() {
  let last = -1;
  return () => {
    const observed = Date.now();
    if (!Number.isSafeInteger(observed) || observed < 0) fail("capability v2 queue production clock observation is invalid");
    last = Math.max(last + 1, observed);
    return new Date(last);
  };
}
function suffix(value) {
  const result = value ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(result)) fail("capability v2 queue record identity is invalid");
  return result;
}
async function exists(path) { return lstat(path).then(() => true, () => false); }

async function compilerSha256() {
  const bytes = await readFile(COMPILER_PATH).catch((error) => fail("capability v2 queue production controller is unreadable", error));
  return sha(bytes);
}

async function privateDirectory(path, label, create = false) {
  if (resolve(path) !== path) fail(`${label} path must be absolute`);
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return path;
}

async function readJson(path, maximum, label) {
  const read = await readOwnerPrivateBoundedFile(path, maximum, label, process.geteuid());
  try { return JSON.parse(read.bytes.toString("utf8")); } catch { fail(`${label} is not JSON`); }
}
async function privateJson(path, maximum, label) {
  try { return await readJson(path, maximum, label); } catch (error) { fail(`${label} is unreadable`, error); }
}

async function writeAtomic(path, value, maximum = MAX_RECORD_BYTES) {
  if (await exists(path)) fail(`queue destination already exists: ${basename(path)}`);
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  if (bytes.length > maximum) fail("capability v2 queue object exceeds its byte ceiling");
  await writeOwnerPrivateCreateNoClobber(path, bytes, process.geteuid());
  await fsyncDirectory(dirname(path));
}

// Genuine atomic create-only CLAIM primitive (not rename-overwrite): opens the
// final path directly with O_CREAT|O_EXCL|O_NOFOLLOW (the "wx" create) so exactly
// one claimant can ever publish it; a pre-existing entry (file, symlink,
// hardlink, or concurrent race) fails with EEXIST and is never overwritten or
// followed. That EEXIST/ELOOP is surfaced as the ONLY typed collision outcome
// (CLAIM_COLLISION); every other failure (write, fsync, permission, read-back,
// identity) propagates unchanged so a caller can never mistake it for a
// concurrent winner. On success the original descriptor is kept open through the
// directory fsync and the no-follow read-back, and its fstat (dev, ino, file
// type, owner, mode, single-link count) is compared against the read-back
// descriptor so a same-UID unlink-and-identical-recreate between creation and
// read-back fails closed. The optional testHook runs after creation/fsync and
// before read-back for deterministic inode-swap tests; production never passes
// one.
async function writeClaimExclusive(path, value, maximum = MAX_RECORD_BYTES, testHook) {
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  if (bytes.length > maximum) fail("capability v2 queue claim exceeds its byte ceiling");
  let handle;
  try {
    handle = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0) | (constants.O_CLOEXEC ?? 0), 0o600);
  } catch (error) {
    if (error?.code === "EEXIST" || error?.code === "ELOOP") return CLAIM_COLLISION;
    throw error;
  }
  try {
    const original = await handle.stat();
    await handle.writeFile(bytes);
    await handle.sync();
    await fsyncDirectory(dirname(path));
    if (testHook) await testHook();
    const readback = await readOwnerPrivateBoundedFile(path, maximum, "capability v2 queue claim", process.geteuid());
    const rs = readback.details;
    const sameKind = original.isFile() === rs.isFile() && original.isDirectory() === rs.isDirectory() && original.isSymbolicLink() === rs.isSymbolicLink();
    const identityMatches = original.dev === rs.dev && original.ino === rs.ino && sameKind && rs.nlink === 1 &&
      (process.platform === "win32" || (original.uid === rs.uid && (original.mode & 0o777) === (rs.mode & 0o777)));
    if (!identityMatches) fail("capability v2 queue claim read-back identity mismatch");
    if (!readback.bytes.equals(bytes)) fail("capability v2 queue claim read-back identity mismatch");
  } finally {
    await handle.close();
  }
  return null;
}

// Controller-owned immutable v2 queue configuration. The request never carries
// a runtime version, pack, policy, adapter, grant, workspace root, state root,
// authority, approval, credentials, network, external effect, budget,
// completion, or security mode; all of those are bound here and revalidated on
// every enqueue/process/read against the persisted config.
const queueConfigKeys = ["schemaVersion", "queueId", "authorization", "plan", "lease", "consumption", "checkpoint", "pack", "expectedPackSha256", "policy", "workspaceRoot", "stateRoot", "compilerPath", "compilerSha256", "enabled", "boundary"];

function exactQueueConfigKeys(value) {
  const keys = queueConfigKeys
    .concat(value?.ssh === undefined ? [] : ["ssh"])
    .concat(value?.research === undefined ? [] : ["research"]);
  exactKeys(value, keys, "capability v2 queue config");
}

function validateResearchQueueConfig(value) {
  if (value === undefined) return;
  exactKeys(value, ["courierQueueRoot", "timeoutMilliseconds", "pollMilliseconds"], "capability v2 queue research config");
  if (typeof value.courierQueueRoot !== "string" || !value.courierQueueRoot.startsWith("/") || resolve(value.courierQueueRoot) !== value.courierQueueRoot) fail("capability v2 queue research courierQueueRoot is not an absolute canonical path");
  if (!Number.isSafeInteger(value.timeoutMilliseconds) || value.timeoutMilliseconds < 1 || value.timeoutMilliseconds > 180000) fail("capability v2 queue research timeoutMilliseconds is outside its bound");
  if (!Number.isSafeInteger(value.pollMilliseconds) || value.pollMilliseconds < 1 || value.pollMilliseconds > 1000) fail("capability v2 queue research pollMilliseconds is outside its bound");
}

function validateSshQueueConfig(value) {
  if (value === undefined) return;
  exactKeys(value, ["sshBinary", "allowedSignersPath", "operatorIdentity", "destinations"], "capability v2 queue SSH config");
  for (const [name, path] of Object.entries({ sshBinary: value.sshBinary, allowedSignersPath: value.allowedSignersPath })) {
    if (typeof path !== "string" || !path.startsWith("/") || resolve(path) !== path) fail(`capability v2 queue SSH ${name} path is invalid`);
  }
  if (basename(value.sshBinary) !== "ssh" || !/^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$/u.test(value.operatorIdentity ?? "")) fail("capability v2 queue SSH binary or operator identity is invalid");
  if (!value.destinations || typeof value.destinations !== "object" || Array.isArray(value.destinations)) fail("capability v2 queue SSH destinations are invalid");
  const entries = Object.entries(value.destinations);
  if (entries.length < 1 || entries.length > 32) fail("capability v2 queue SSH destination count is invalid");
  for (const [alias, destination] of entries) {
    if (!/^[a-z][a-z0-9-]{1,62}$/u.test(alias)) fail("capability v2 queue SSH destination alias is invalid");
    exactKeys(destination, ["enabled", "host", "port", "user", "credentialRef", "identityFile", "knownHostsFile", "commandId", "expectedHostname"], "capability v2 queue SSH destination");
    if (destination.enabled !== true || isIP(destination.host) === 0 || !Number.isSafeInteger(destination.port) || destination.port < 1 || destination.port > 65535) fail("capability v2 queue SSH destination endpoint is invalid");
    if (!/^[A-Za-z_][A-Za-z0-9_.-]{0,31}$/u.test(destination.user ?? "") || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/u.test(destination.credentialRef ?? "") || destination.commandId !== "hostname" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$/u.test(destination.expectedHostname ?? "")) fail("capability v2 queue SSH destination binding is invalid");
    for (const [name, path] of Object.entries({ identityFile: destination.identityFile, knownHostsFile: destination.knownHostsFile })) if (typeof path !== "string" || !path.startsWith("/") || resolve(path) !== path) fail(`capability v2 queue SSH destination ${name} is invalid`);
  }
}

async function validateConfig(config) {
  if (!QUEUE_ID_RE.test(config.queueId ?? "")) fail("capability v2 queue identity is invalid");
  schema("capability v2 queue authorization", validateWorkCapabilityJobAuthorizationV2(config.authorization));
  schema("capability v2 queue plan", validateWorkPlan(config.plan));
  schema("capability v2 queue lease", validateWorkLease(config.lease));
  schema("capability v2 queue consumption", validateWorkConsumption(config.consumption));
  schema("capability v2 queue checkpoint", validateWorkCheckpoint(config.checkpoint));
  schema("capability v2 queue pack", validateWorkCapabilityPackV2(config.pack));
  schema("capability v2 queue policy", validateWorkCapabilityControllerPolicyV2(config.policy));
  const jobId = config.authorization.jobId;
  for (const [name, value] of Object.entries({ plan: config.plan, lease: config.lease, consumption: config.consumption, checkpoint: config.checkpoint })) {
    if (value.jobId !== jobId) fail(`capability v2 queue ${name} differs from the authorized job`);
  }
  const packSha = capabilityPackSha256(config.pack);
  if (!SHA_RE.test(config.expectedPackSha256 ?? "") || config.expectedPackSha256 !== packSha) fail("capability v2 queue expected pack SHA differs from the signed pack");
  if (config.authorization.pack.packSha256 !== packSha || config.authorization.pack.treeSha256 !== config.pack.provenance.treeSha256 || config.authorization.pack.schemaVersion !== 2) fail("capability v2 queue authorization pack binding differs from the signed pack");
  if (config.authorization.pack.id !== config.pack.id || config.authorization.pack.version !== config.pack.version) fail("capability v2 queue authorization pack identity differs");
  if (config.authorization.controllerPolicySha256 !== sha(config.policy)) fail("capability v2 queue authorization policy hash differs");
  if (config.authorization.planSha256 !== sha(config.plan) || config.authorization.leaseSha256 !== sha(config.lease) || config.authorization.consumptionSha256 !== sha(config.consumption) || config.authorization.checkpointSha256 !== sha(config.checkpoint)) fail("capability v2 queue authorization custody hashes differ");
  if (config.authorization.dataClassification !== config.plan.dataClassification) fail("capability v2 queue authorization classification differs from the plan");
  if (typeof config.workspaceRoot !== "string" || config.workspaceRoot.length === 0 || !config.workspaceRoot.startsWith("/")) fail("capability v2 queue workspace root is not a trusted absolute path");
  if (typeof config.stateRoot !== "string" || config.stateRoot.length === 0 || !config.stateRoot.startsWith("/")) fail("capability v2 queue state root is not a trusted absolute path");
  if (config.compilerPath !== COMPILER_PATH) fail("capability v2 queue compiler path differs from the production controller");
  if (!SHA_RE.test(config.compilerSha256 ?? "") || config.compilerSha256 !== await compilerSha256()) fail("capability v2 queue compiler byte identity differs from the production controller");
  validateSshQueueConfig(config.ssh);
  validateResearchQueueConfig(config.research);
  if (config.enabled !== true && config.enabled !== false) fail("capability v2 queue enabled flag is invalid");
  if (config.boundary !== configBoundary) fail("capability v2 queue config boundary is invalid");
}

async function config(queueRoot) {
  await privateDirectory(queueRoot, "capability v2 queue root");
  const value = await privateJson(join(queueRoot, "config.json"), MAX_RECORD_BYTES, "capability v2 queue config");
  exactQueueConfigKeys(value);
  if (value.schemaVersion !== 2 || value.boundary !== configBoundary) fail("capability v2 queue config version or boundary is invalid");
  await validateConfig(value);
  const paths = {
    root: queueRoot, requests: join(queueRoot, "requests"), active: join(queueRoot, "active"),
    responses: join(queueRoot, "responses"), history: join(queueRoot, "history"),
    current: join(queueRoot, "current.json"), terminal: join(queueRoot, "terminal.json"),
  };
  if (!(await exists(paths.active))) await mkdir(paths.active, { mode: 0o700 });
  for (const name of ["requests", "active", "responses", "history"]) await privateDirectory(paths[name], `capability v2 queue ${name}`);
  return { config: value, paths };
}

export async function initializeCapabilityToolQueueV2({ queueRoot, config: candidate }) {
  exactQueueConfigKeys(candidate);
  if (candidate.schemaVersion !== 2 || candidate.boundary !== configBoundary) fail("capability v2 queue config version or boundary is invalid");
  await validateConfig(candidate);
  await privateDirectory(dirname(queueRoot), "capability v2 queue parent");
  if (!(await exists(queueRoot))) await mkdir(queueRoot, { mode: 0o700 });
  await privateDirectory(queueRoot, "capability v2 queue root");
  const configPath = join(queueRoot, "config.json");
  if (!(await exists(configPath))) await writeAtomic(configPath, candidate);
  else {
    const current = await privateJson(configPath, MAX_RECORD_BYTES, "capability v2 queue config");
    if (canonical(current) !== canonical(candidate)) fail("capability v2 queue root belongs to another immutable configuration");
  }
  for (const name of ["requests", "active", "responses", "history"]) {
    const path = join(queueRoot, name);
    if (!(await exists(path))) await mkdir(path, { mode: 0o700 });
    await privateDirectory(path, `capability v2 queue ${name}`);
  }
  await fsyncDirectory(queueRoot);
  return Object.freeze({ schemaVersion: 2, status: candidate.enabled ? "initialized-enabled" : "initialized-disabled", queueId: candidate.queueId, enabled: candidate.enabled, authority: { ...authority }, boundary: configBoundary });
}

export async function enqueueCapabilityToolRequestV2({ queueRoot, config: candidate, request }) {
  const { config: cfg, paths } = await config(queueRoot);
  if (candidate !== undefined && canonical(candidate) !== canonical(cfg)) fail("capability v2 queue config seam differs from the immutable root");
  if (await exists(paths.terminal)) fail("capability v2 queue is terminal");
  schema("capability v2 queue request", validateWorkCapabilityQueueRequestV2(request));
  if (request.authorizationSha256 !== sha(cfg.authorization) || request.jobId !== cfg.authorization.jobId || request.planSha256 !== sha(cfg.plan) || request.leaseSha256 !== sha(cfg.lease) || request.checkpointSha256 !== sha(cfg.checkpoint) || request.packSha256 !== cfg.expectedPackSha256) fail("capability v2 queue request differs from its immutable queue configuration");
  if (request.dataClassification !== cfg.plan.dataClassification) fail("capability v2 queue request classification differs from the plan");
  requestLane(request, cfg);
  const names = await readdir(paths.requests);
  if (names.length >= MAX_QUEUE || names.some((name) => !/^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(name))) fail("capability v2 queue request shape or capacity is invalid");
  const destination = join(paths.requests, `${request.requestId}.json`);
  if (await exists(destination)) {
    const existing = await privateJson(destination, MAX_REQUEST_BYTES, "capability v2 queued request");
    schema("capability v2 queued request", validateWorkCapabilityQueueRequestV2(existing));
    if (canonical(existing) !== canonical(request)) fail("capability v2 queued request differs from the exact retry");
    return Object.freeze({ schemaVersion: 2, status: cfg.enabled ? "already-queued-enabled" : "already-queued-disabled", requestId: request.requestId, requestSha256: sha(request), enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
  }
  await writeAtomic(destination, request, MAX_REQUEST_BYTES);
  return Object.freeze({ schemaVersion: 2, status: cfg.enabled ? "queued-enabled" : "queued-disabled", requestId: request.requestId, requestSha256: sha(request), enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
}

async function loadCustody(paths, request, cfg) {
  const directory = join(paths.active, "custody");
  await privateDirectory(directory, "capability v2 active custody");
  const names = (await readdir(directory)).sort(), records = []; let prior = null, priorTime = -1;
  for (const [index, name] of names.entries()) {
    const match = RECORD_RE.exec(name);
    if (!match || Number(match[1]) !== index) fail("capability v2 queue custody is not contiguous");
    const record = await privateJson(join(directory, name), MAX_RECORD_BYTES, "capability v2 queue custody record");
    if (record.schemaVersion !== 2 || record.sequence !== index || record.previousRecordSha256 !== prior || Date.parse(record.recordedAt) <= priorTime || record.requestId !== request.requestId || record.jobId !== cfg.authorization.jobId || record.requestSha256 !== sha(request) || record.authorizationSha256 !== sha(cfg.authorization) || record.checkpointSha256 !== sha(cfg.checkpoint)) fail("capability v2 queue custody chain differs from active work");
    if (record.operationId !== null && !OPERATION_RE.test(record.operationId)) fail("capability v2 queue custody operation identity is invalid");
    records.push(record); prior = record.recordSha256; priorTime = Date.parse(record.recordedAt);
  }
  return { directory, records, head: records.at(-1) ?? null };
}

async function appendCustody(paths, request, cfg, fields, clock, suffixValue) {
  const chain = await loadCustody(paths, request, cfg), time = observed(clock, "capability v2 queue custody");
  if (chain.head && time.getTime() <= Date.parse(chain.head.recordedAt)) fail("capability v2 queue custody time did not advance");
  const body = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-queue-custody-v2.schema.json", schemaVersion: 2,
    recordId: `workcapv2custody-${String(time.getTime()).padStart(13, "0")}-${suffix(suffixValue)}`,
    sequence: chain.records.length, recordedAt: time.toISOString(), requestId: request.requestId, jobId: cfg.authorization.jobId,
    requestSha256: sha(request), authorizationSha256: sha(cfg.authorization), checkpointSha256: sha(cfg.checkpoint),
    operationId: null, phase: null, decisionSha256: null, grantId: null, grantSha256: null, receiptSha256: null, outcome: null,
    previousRecordSha256: chain.head?.recordSha256 ?? null, authority: { ...authority }, boundary: custodyBoundary, ...fields,
  };
  const record = { ...body, recordSha256: sha(body) };
  await writeAtomic(join(chain.directory, `${String(record.sequence).padStart(7, "0")}.json`), record);
  return record;
}

async function readCurrent(paths) {
  const current = await privateJson(paths.current, MAX_RECORD_BYTES, "capability v2 queue current claim");
  exactKeys(current, ["schemaVersion", "requestId", "boundary"], "capability v2 queue current claim");
  if (current.schemaVersion !== 2 || !REQUEST_RE.test(current.requestId ?? "") || current.boundary !== custodyBoundary) fail("capability v2 queue current claim is invalid");
  return current;
}

// Bring the claimed request to a validated, custody-initialized active state.
// The request must already be present either in active/request.json (a prior
// claimant) or in requests (a claim this process just won). This helper never
// invents an active request that is not present somewhere durable.
async function readyActiveRequest(paths, cfg, requestId, clock, suffixes) {
  if (!(await exists(paths.active))) await mkdir(paths.active, { mode: 0o700 });
  await privateDirectory(paths.active, "capability v2 active request dir");
  const custodyDir = join(paths.active, "custody");
  if (!(await exists(custodyDir))) await mkdir(custodyDir, { mode: 0o700 });
  await privateDirectory(custodyDir, "capability v2 active custody dir");
  const requestPath = join(paths.active, "request.json");
  if (!(await exists(requestPath))) {
    const source = join(paths.requests, `${requestId}.json`);
    if (!(await exists(source))) fail("capability v2 current claim has neither queued nor active request");
    await rename(source, requestPath); await fsyncDirectory(paths.requests); await fsyncDirectory(paths.active);
  }
  const request = await privateJson(requestPath, MAX_REQUEST_BYTES, "capability v2 active request");
  schema("capability v2 active request", validateWorkCapabilityQueueRequestV2(request));
  if (request.requestId !== requestId || request.authorizationSha256 !== sha(cfg.authorization) || request.jobId !== cfg.authorization.jobId || request.planSha256 !== sha(cfg.plan) || request.leaseSha256 !== sha(cfg.lease) || request.checkpointSha256 !== sha(cfg.checkpoint) || request.packSha256 !== cfg.expectedPackSha256 || request.dataClassification !== cfg.plan.dataClassification) fail("capability v2 active request differs from its immutable queue configuration");
  const head = (await loadCustody(paths, request, cfg)).head;
  if (!head) await appendCustody(paths, request, cfg, { phase: "claimed", operationId: null }, clock, suffixes.claimed);
  return { request };
}

async function claimedRequest(paths, cfg) {
  // An observed current.json is NOT proof the active request is ready. Validate
  // the claim and require a matching ready active request; a torn claim (current
  // present but active request absent or mismatched) fails closed rather than
  // reading a missing active request just because another claimant published
  // current first.
  const current = await readCurrent(paths);
  const requestPath = join(paths.active, "request.json");
  if (!(await exists(requestPath))) fail("capability v2 queue current claim is torn: no ready active request; fail closed");
  const active = await privateJson(requestPath, MAX_REQUEST_BYTES, "capability v2 active request");
  if (!REQUEST_RE.test(active.requestId ?? "") || active.requestId !== current.requestId) fail("capability v2 queue current claim does not match the ready active request");
  return current.requestId;
}

async function acquire(paths, cfg, clock, suffixes, deps) {
  if (await exists(paths.current)) {
    const requestId = await claimedRequest(paths, cfg);
    const { request } = await readyActiveRequest(paths, cfg, requestId, clock, suffixes);
    return { busy: false, request };
  }
  const names = (await readdir(paths.requests)).sort();
  if (!names.length) return null;
  if (names.length > MAX_QUEUE || names.some((name) => !/^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(name))) fail("capability v2 request queue contains an unsafe entry");
  const requestId = names[0].slice(0, -5);
  const candidateSource = join(paths.requests, `${requestId}.json`);
  if (!(await exists(candidateSource))) fail("capability v2 request queue candidate is missing its queued request");
  const candidateRequest = await privateJson(candidateSource, MAX_REQUEST_BYTES, "capability v2 queued candidate request");
  schema("capability v2 queued candidate request", validateWorkCapabilityQueueRequestV2(candidateRequest));
  if (candidateRequest.requestId !== requestId || candidateRequest.authorizationSha256 !== sha(cfg.authorization) || candidateRequest.jobId !== cfg.authorization.jobId || candidateRequest.planSha256 !== sha(cfg.plan) || candidateRequest.leaseSha256 !== sha(cfg.lease) || candidateRequest.checkpointSha256 !== sha(cfg.checkpoint) || candidateRequest.packSha256 !== cfg.expectedPackSha256 || candidateRequest.dataClassification !== cfg.plan.dataClassification) fail("capability v2 queued candidate request differs from its immutable queue configuration");
  requestLane(candidateRequest, cfg);
  // The atomic claim is the single-winner publication of current.json. Exactly
  // one claimant can create it; every other contender receives the typed
  // CLAIM_COLLISION outcome and defers to the surviving claim (fail-closed on a
  // torn claim). Every other failure propagates unchanged.
  const outcome = await (deps.writeClaim ?? writeClaimExclusive)(paths.current, { schemaVersion: 2, requestId, boundary: custodyBoundary });
  if (outcome === CLAIM_COLLISION) {
    // Only the unmistakable typed collision outcome (EEXIST/ELOOP) defers to a
    // surviving claim. Write, fsync, permission, read-back, and identity
    // failures are never mistaken for a concurrent winner.
    if (!(await exists(paths.current))) throw new Error("capability v2 queue claim could not be published");
    const survivingId = await claimedRequest(paths, cfg);
    const { request } = await readyActiveRequest(paths, cfg, survivingId, clock, suffixes);
    return { busy: false, request };
  }
  const { request } = await readyActiveRequest(paths, cfg, requestId, clock, suffixes);
  return { busy: false, request };
}

function requestLane(request, cfg) {
  const hasFilesystem = request.relativePath !== undefined || request.content !== undefined;
  const hasSsh = request.destinationAlias !== undefined || request.commandId !== undefined;
  const hasRetrieval = request.query !== undefined || request.sourceTypes !== undefined || request.domains !== undefined || request.maxResults !== undefined || request.maxSourcesToFetch !== undefined || request.maxSourceBytes !== undefined;
  if ([hasFilesystem, hasSsh, hasRetrieval].filter(Boolean).length !== 1) fail("capability v2 queue request mixes or omits lane-selecting fields");
  const declared = cfg.pack.tools.find((entry) => entry.name === request.tool);
  const authorized = cfg.authorization.tools.find((entry) => entry.name === request.tool);
  if (!declared || !authorized || declared.effectClass !== authorized.effectClass) fail("capability v2 queue request tool differs from the immutable signed authorization");
  if (request.relativePath !== undefined) {
    if (declared.effectClass !== "workspace" || declared.binding !== null || authorized.bindingSummary.targetClass !== "local-filesystem") fail("capability v2 queue filesystem request does not select the signed filesystem tool");
    return "filesystem";
  }
  if (request.destinationAlias !== undefined) {
    if (declared.effectClass !== "read-only" || declared.binding?.targetClass !== "ssh" || authorized.bindingSummary.targetClass !== "ssh") fail("capability v2 queue SSH request does not select the signed SSH tool");
    return "ssh-hostname";
  }
  if (request.query !== undefined) {
    if (request.relativePath !== undefined || request.content !== undefined || request.destinationAlias !== undefined || request.commandId !== undefined) fail("capability v2 queue public-retrieval request mixes filesystem or SSH fields");
    if (!cfg.research) fail("capability v2 queue public-retrieval request has no protected research runtime configuration");
    if (declared.effectClass !== "brokered-network" || declared.binding?.targetClass !== "public-api"
      || declared.binding.inputClassification !== "public" || declared.binding.outputClassification !== "public"
      || authorized.bindingSummary.targetClass !== "public-api"
      || authorized.bindingSummary.inputClassification !== "public" || authorized.bindingSummary.outputClassification !== "public") fail("capability v2 queue public-retrieval request does not select the signed brokered-network public-api tool");
    return "public-retrieval";
  }
  fail("capability v2 queue request has no supported controller-selected lane");
}

function sshApprovalFor(request, cfg, now, nonceValue) {
  if (requestLane(request, cfg) !== "ssh-hostname" || !cfg.ssh) fail("capability v2 queue SSH request has no protected runtime configuration");
  const declared = cfg.pack.tools.find((entry) => entry.name === request.tool);
  const binding = declared.binding;
  const destination = cfg.ssh.destinations[request.destinationAlias];
  if (!destination || request.commandId !== "hostname" || destination.commandId !== request.commandId) fail("capability v2 queue SSH request differs from its protected destination binding");
  if (!binding.scope?.destinations?.includes(request.destinationAlias) || !binding.egress?.destinations?.includes(request.destinationAlias) || !binding.credentials?.refs?.includes(destination.credentialRef)) fail("capability v2 queue SSH request is outside the signed destination or credential binding");
  const nonce = nonceValue ?? randomBytes(32).toString("hex");
  if (!SHA_RE.test(nonce)) fail("capability v2 queue SSH approval nonce is invalid");
  const issuedAt = now.getTime();
  const expiresAt = Math.min(issuedAt + SSH_APPROVAL_LIFETIME_MS, Date.parse(cfg.authorization.expiresAt), Date.parse(cfg.policy.expiresAt), Date.parse(cfg.lease.expiresAt));
  if (!Number.isSafeInteger(expiresAt) || expiresAt - issuedAt < 100) fail("capability v2 queue SSH approval has no usable lifetime");
  const maxOutputBytes = Math.min(4096, cfg.pack.limits.maxOutputBytes, cfg.policy.limits.maxOutputBytes, cfg.authorization.limits.maxOutputBytes);
  const timeoutMs = Math.min(30000, cfg.pack.limits.maxRuntimeMs, cfg.policy.limits.maxRuntimeMs, cfg.authorization.limits.maxRuntimeMs);
  if (!Number.isSafeInteger(maxOutputBytes) || maxOutputBytes < 1 || !Number.isSafeInteger(timeoutMs) || timeoutMs < 100) fail("capability v2 queue SSH approval budget is unusable");
  const approval = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-ssh-approval-v2.schema.json", schemaVersion: 2,
    operation: "pixel-work-capability-ssh-approval-v2", requestId: request.requestId,
    destinationAlias: request.destinationAlias, commandId: "hostname", credentialRef: destination.credentialRef,
    issuedAt: now.toISOString(), expiresAt: new Date(expiresAt).toISOString(), nonce, maxOutputBytes, timeoutMs,
    authority: { ...sshApprovalAuthority }, boundary: sshApprovalBoundary,
  };
  schema("capability v2 queue SSH approval", validateWorkCapabilitySshApprovalV2(approval));
  return approval;
}

async function loadSshApproval(paths, request, cfg) {
  const approval = await privateJson(join(paths.active, "approval.json"), MAX_RECORD_BYTES, "capability v2 queue SSH approval");
  schema("capability v2 queue SSH approval", validateWorkCapabilitySshApprovalV2(approval));
  const expected = sshApprovalFor(request, cfg, new Date(approval.issuedAt), approval.nonce);
  if (canonical(approval) !== canonical(expected)) fail("capability v2 queue SSH approval differs from its immutable request and configuration");
  return approval;
}

async function loadSshSignatureRecord(paths, request, approval, required = false) {
  const path = join(paths.active, "approval-signature.json");
  if (!(await exists(path))) {
    if (required) fail("capability v2 queue SSH owner signature is missing");
    return null;
  }
  const record = await privateJson(path, MAX_RECORD_BYTES, "capability v2 queue SSH signature record");
  exactKeys(record, ["schemaVersion", "requestId", "approvalSha256", "signatureSha256", "signature", "signerIdentity", "verifiedAt", "boundary"], "capability v2 queue SSH signature record");
  if (record.schemaVersion !== 2 || record.requestId !== request.requestId || record.approvalSha256 !== sha(approval) || !SHA_RE.test(record.signatureSha256 ?? "") || record.signatureSha256 !== sha(record.signature) || record.boundary !== sshSignatureBoundary) fail("capability v2 queue SSH signature record differs from its approval");
  return record;
}

export async function readCapabilityToolApprovalV2({ queueRoot, config: candidate, requestId }) {
  if (!REQUEST_RE.test(requestId ?? "")) fail("capability v2 approval request identity is invalid");
  const { config: cfg, paths } = await config(queueRoot);
  if (candidate !== undefined && canonical(candidate) !== canonical(cfg)) fail("capability v2 queue config seam differs from the immutable root");
  const current = await readCurrent(paths);
  if (current.requestId !== requestId) fail("capability v2 approval request is not the active request");
  const request = await privateJson(join(paths.active, "request.json"), MAX_REQUEST_BYTES, "capability v2 active SSH request");
  if (request.requestId !== requestId || requestLane(request, cfg) !== "ssh-hostname") fail("capability v2 approval request is not the active SSH request");
  return Object.freeze(structuredClone(await loadSshApproval(paths, request, cfg)));
}

export async function submitCapabilityToolApprovalSignatureV2Core({ queueRoot, config: candidate, requestId, approvalSha256, signature, clock = () => new Date() }) {
  if (!REQUEST_RE.test(requestId ?? "") || !SHA_RE.test(approvalSha256 ?? "") || typeof signature !== "string") fail("capability v2 SSH signature submission is invalid");
  const { config: cfg, paths } = await config(queueRoot);
  if (candidate !== undefined && canonical(candidate) !== canonical(cfg)) fail("capability v2 queue config seam differs from the immutable root");
  const current = await readCurrent(paths);
  if (current.requestId !== requestId) fail("capability v2 SSH signature request is not active");
  const request = await privateJson(join(paths.active, "request.json"), MAX_REQUEST_BYTES, "capability v2 active SSH request");
  const approval = await loadSshApproval(paths, request, cfg);
  if (sha(approval) !== approvalSha256) fail("capability v2 SSH signature approval hash differs");
  const chain = await loadCustody(paths, request, cfg);
  if (chain.head?.phase !== "awaiting-owner-signature") fail("capability v2 SSH signature is not awaiting owner approval");
  const existing = await loadSshSignatureRecord(paths, request, approval);
  const signatureSha256 = sha(signature);
  if (existing) {
    if (existing.signatureSha256 !== signatureSha256 || existing.signature !== signature) fail("capability v2 SSH signature differs from the exact submitted owner signature");
    return Object.freeze({ schemaVersion: 2, status: "already-submitted", requestId, approvalSha256, signatureSha256, authority: { ...authority }, boundary: sshSignatureBoundary });
  }
  if (!cfg.ssh) fail("capability v2 SSH signature submission has no protected signer configuration");
  const verifiedAt = observed(clock, "capability v2 SSH signature verification");
  const verified = await verifyCapabilitySshApprovalV2({ approval, signature, allowedSignersPath: cfg.ssh.allowedSignersPath, identity: cfg.ssh.operatorIdentity, clock: () => verifiedAt });
  if (verified.approvalSha256 !== approvalSha256 || verified.signatureSha256 !== signatureSha256) fail("capability v2 SSH verified signature differs from its submission");
  const record = { schemaVersion: 2, requestId, approvalSha256, signatureSha256, signature, signerIdentity: verified.signerIdentity, verifiedAt: verifiedAt.toISOString(), boundary: sshSignatureBoundary };
  try { await writeAtomic(join(paths.active, "approval-signature.json"), record, MAX_RECORD_BYTES); }
  catch (error) {
    const raced = await loadSshSignatureRecord(paths, request, approval).catch(() => null);
    if (!raced || canonical(raced) !== canonical(record)) throw error;
  }
  return Object.freeze({ schemaVersion: 2, status: "submitted", requestId, approvalSha256, signatureSha256, authority: { ...authority }, boundary: sshSignatureBoundary });
}

// The public signature submission surface always observes the host clock. The
// deterministic clock seam exists only on the explicitly test-only wrapper, so
// an untrusted caller cannot backdate signature verification for an expired
// approval record.
export async function submitCapabilityToolApprovalSignatureV2({ queueRoot, config, requestId, approvalSha256, signature }) {
  return submitCapabilityToolApprovalSignatureV2Core({ queueRoot, config, requestId, approvalSha256, signature, clock: () => new Date() });
}

function validateGrant(decision, request, cfg, clock, { requireCurrent = true } = {}) {
  const grant = decision.grant;
  const lane = requestLane(request, cfg);
  schema("capability v2 queue decision grant", validateWorkCapabilityOperationalGrantV2(grant));
  const now = observed(clock, "capability v2 queue decision clock").getTime();
  const issued = Date.parse(grant.issuedAt), expires = Date.parse(grant.expiresAt);
  if (!Number.isFinite(issued) || !Number.isFinite(expires) || issued > expires) fail("capability v2 queue decision grant issue/expiry is invalid");
  if (requireCurrent && now < issued) fail("capability v2 queue decision grant is not yet valid");
  if (requireCurrent && now >= expires) fail("capability v2 queue decision grant has expired");
  if (grant.jobId !== cfg.authorization.jobId || grant.checkpointSha256 !== sha(cfg.checkpoint)) fail("capability v2 queue decision grant job/checkpoint drift");
  if (grant.pack.packSha256 !== cfg.expectedPackSha256 || grant.pack.treeSha256 !== cfg.pack.provenance.treeSha256 || grant.pack.signerIdentity !== cfg.pack.provenance.signerIdentity) fail("capability v2 queue decision grant pack drift");
  if (grant.tool.name !== request.tool || grant.operation.lane !== lane) fail("capability v2 queue decision grant tool or lane drift");
  if (lane === "filesystem") {
    if (grant.tool.effectClass !== "workspace" || grant.tool.targetClass !== "local-filesystem" || grant.operation.action !== "write" || grant.operation.scope !== "job-workspace" || grant.operation.relativePath !== request.relativePath) fail("capability v2 queue decision grant filesystem operation drift");
    if (grant.contentSha256 !== sha(request.content) || grant.approvalMode !== "none" || grant.egress !== "none" || grant.credentialRefs.length !== 0) fail("capability v2 queue decision grant filesystem security/content drift");
  } else if (lane === "ssh-hostname") {
    const approval = decision.runtimeRequest?.approval;
    if (grant.tool.effectClass !== "read-only" || grant.tool.targetClass !== "ssh" || grant.operation.requestId !== request.requestId || grant.operation.destinationAlias !== request.destinationAlias || grant.operation.commandId !== request.commandId) fail("capability v2 queue decision grant SSH operation drift");
    if (grant.approvalMode !== "owner-signed" || grant.egress !== "private-allowlist" || grant.credentialRefs.length !== 1 || !approval || sha(approval) !== grant.approvalSha256 || approval.credentialRef !== grant.credentialRefs[0]) fail("capability v2 queue decision grant SSH approval/credential drift");
  } else if (lane === "public-retrieval") {
    if (grant.tool.effectClass !== "brokered-network" || grant.tool.targetClass !== "public-api") fail("capability v2 queue decision grant public-retrieval tool drift");
    if (grant.dataClassification !== "public" || canonical(grant.classification) !== canonical({ input: "public", output: "public" })) fail("capability v2 queue decision grant public-retrieval classification drift");
    if (grant.approvalMode !== "none" || grant.egress !== "public" || grant.credentialRefs.length !== 0) fail("capability v2 queue decision grant public-retrieval security drift");
    const retrievalInput = grant.retrievalInput;
    const runtimeRequest = decision.runtimeRequest;
    if (!retrievalInput || !runtimeRequest) fail("capability v2 queue decision grant public-retrieval binding is missing");
    if (retrievalInput.querySha256 !== sha(request.query)) fail("capability v2 queue decision grant public-retrieval query drift");
    if (canonical(retrievalInput.sourceTypes) !== canonical(request.sourceTypes) || canonical(retrievalInput.domains) !== canonical(request.domains)) fail("capability v2 queue decision grant public-retrieval source/domain drift");
    if (retrievalInput.maxResults !== request.maxResults || retrievalInput.maxSourcesToFetch !== request.maxSourcesToFetch || retrievalInput.maxSourceBytes !== request.maxSourceBytes) fail("capability v2 queue decision grant public-retrieval limits drift");
    if (runtimeRequest.grantId !== grant.grantId || runtimeRequest.operationId !== grant.operation.id || runtimeRequest.lane !== "public-retrieval") fail("capability v2 queue decision grant public-retrieval runtime binding drift");
    if (runtimeRequest.query !== request.query || runtimeRequest.trustRoot !== "courier") fail("capability v2 queue decision grant public-retrieval runtime query/trust drift");
    if (canonical(runtimeRequest.retrievalBinding) !== canonical(retrievalInput)) fail("capability v2 queue decision grant public-retrieval retrieval binding drift");
  } else {
    fail("capability v2 queue decision grant lane is unsupported");
  }
  if (grant.dataClassification !== cfg.plan.dataClassification) fail("capability v2 queue decision grant classification drift");
  const byteCeiling = lane === "filesystem" ? cfg.authorization.limits.maxWorkspaceBytes : cfg.authorization.limits.maxOutputBytes;
  if (grant.budgets?.perRun?.maxCalls !== 1 || grant.budgets?.perRun?.maxBytes > byteCeiling || grant.budgets?.perRun?.maxDurationMs > cfg.authorization.limits.maxRuntimeMs) fail("capability v2 queue decision grant budget drift");
  if (grant.authority.grantsCredentials !== false || grant.authority.grantsNetwork !== false || grant.authority.grantsExternalEffects !== false || grant.authority.grantsScopeExpansion !== false || grant.authority.grantsCompletion !== false) fail("capability v2 queue decision grant authority drift");
  return { grant, operationId: grant.operation.id, runtimeRequest: decision.runtimeRequest };
}

function validateRuntimeResult(result, decision, cfg) {
  const lane = decision.grant.operation.lane;
  schema("capability v2 queue runtime result", validateWorkCapabilityOperationalRuntimeResultV2(result));
  if (result.grantId !== decision.grant.grantId || result.operationId !== decision.grant.operation.id || result.lane !== lane) fail("capability v2 queue runtime result differs from its decision grant");
  const expectedEffectByLane = { filesystem: "workspace-write", "ssh-hostname": "ssh-forced-hostname", "public-retrieval": "public-retrieval" };
  const expectedEffect = expectedEffectByLane[lane];
  if (expectedEffect === undefined) fail("capability v2 queue runtime result lane has no allowed effect");
  if (result.status !== "succeeded" || result.enabled !== true || result.effect !== expectedEffect) fail("capability v2 queue runtime result is not the honest operational success");
  if (!SHA_RE.test(result.receiptSha256 ?? "") || !SHA_RE.test(result.outputSha256 ?? "")) fail("capability v2 queue runtime result output/receipt binding differs from the grant");
  if (lane === "filesystem" && result.outputSha256 !== decision.grant.contentSha256) fail("capability v2 queue filesystem result content differs from the grant");
  if (lane === "ssh-hostname" && (result.structuredContent.destinationAlias !== decision.grant.operation.destinationAlias || result.structuredContent.commandId !== "hostname" || result.structuredContent.approvalSha256 !== decision.grant.approvalSha256)) fail("capability v2 queue SSH result differs from the exact owner-approved operation");
  if (lane === "public-retrieval") {
    const sc = result.structuredContent;
    if (!sc || typeof sc !== "object" || Array.isArray(sc)) fail("capability v2 queue public-retrieval result is missing structured content");
    if (sc.operationId !== result.operationId || sc.lane !== "public-retrieval" || sc.status !== "succeeded" || sc.outputSha256 !== result.outputSha256 || sc.outputBytes !== result.outputBytes || sc.receiptSha256 !== result.receiptSha256) fail("capability v2 queue public-retrieval result structured content is not exact-bound to the result and grant");
    const evidenceWrapper = sc.evidenceWrapper;
    exactKeys(evidenceWrapper, ["trust", "authority", "text"], "capability v2 queue public-retrieval evidence wrapper");
    if (evidenceWrapper.trust !== "untrusted" || evidenceWrapper.authority !== "none" || typeof evidenceWrapper.text !== "string" || evidenceWrapper.text.length === 0) fail("capability v2 queue public-retrieval evidence wrapper is invalid");
    const evidenceString = JSON.stringify(evidenceWrapper);
    if (sha(evidenceString) !== result.outputSha256 || Buffer.byteLength(evidenceString, "utf8") !== result.outputBytes) fail("capability v2 queue public-retrieval evidence wrapper does not bind to the output identity");
  }
  if (result.authority?.grantsCompletion !== false || result.authority?.grantsReplay !== false || result.authority?.grantsFutureExecution !== false) fail("capability v2 queue runtime result grants forbidden authority");
  return { result, outcome: "success" };
}

function responseFor({ request, cfg, lane, status, operationId = null, receiptSha256 = null, outputSha256 = null, outputBytes = null, structuredContent = null, now }) {
  const response = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-queue-response-v2.schema.json", schemaVersion: 2,
    requestId: request.requestId, jobId: cfg.authorization.jobId, createdAt: now.toISOString(),
    authorizationSha256: sha(cfg.authorization), checkpointSha256: sha(cfg.checkpoint),
    status, operationId, lane, receiptSha256, outputSha256, outputBytes,
    structuredContent: structuredContent === null ? null : structuredClone(structuredContent),
    authority: { ...responseAuthority }, boundary: responseBoundary,
  };
  schema("capability v2 queue response", validateWorkCapabilityQueueResponseV2(response));
  return response;
}

async function loadDecision(paths, request, cfg) {
  const value = await privateJson(join(paths.active, "decision.json"), MAX_RECORD_BYTES, "capability v2 queue decision");
  exactKeys(value, ["schemaVersion", "grant", "runtimeRequest", "boundary"], "capability v2 queue decision");
  if (value.schemaVersion !== 2 || value.boundary !== decisionBoundary) fail("capability v2 queue decision version or boundary is invalid");
  schema("capability v2 queue decision grant", validateWorkCapabilityOperationalGrantV2(value.grant));
  return value;
}

async function loadRuntimeResult(paths, decision, cfg) {
  const value = await privateJson(join(paths.active, "runtime-result.json"), MAX_RESPONSE_BYTES, "capability v2 queue runtime result");
  schema("capability v2 queue runtime result", validateWorkCapabilityOperationalRuntimeResultV2(value));
  if (value.grantId !== decision.grant.grantId || value.operationId !== decision.grant.operation.id || value.lane !== decision.grant.operation.lane) fail("capability v2 queue runtime result binding drift");
  if (value.lane === "filesystem" && value.outputSha256 !== decision.grant.contentSha256) fail("capability v2 queue filesystem runtime result binding drift");
  return value;
}

// Authenticate a standalone operational receipt against the queue's authentic
// prior grant/decision (exact keys/schema/boundary/operation + exact content
// digest). A receipt alone proves nothing: completion additionally requires the
// durably settled runtime custody bound to the same grant (see recoveryOutcome).
async function authenticatedReceipt(cfg, decision, operationId) {
  const receiptPath = join(cfg.stateRoot, "v2-runtime", "receipts", `${operationId}.json`);
  if (!(await exists(receiptPath))) return null;
  let receipt;
  try { receipt = await readJson(receiptPath, MAX_OPERATIONAL_RECEIPT_BYTES, "operational v2 receipt"); }
  catch (error) { fail("capability v2 queue receipt is unreadable or invalid", error); }
  return validateOperationalReceipt(receipt, { grant: decision.grant, operationId });
}

// Validate the operational runtime's durable settled custody and bind it to the
// exact prior decision grant. Returns the validated settled record or null if no
// settled custody exists. A forged or drifted settled custody fails closed.
async function validateSettledCustody(cfg, decision, operationId) {
  const settledPath = join(cfg.stateRoot, "v2-runtime", "custody", `${operationId}.settled.json`);
  if (!(await exists(settledPath))) return null;
  let record;
  try { record = await readJson(settledPath, MAX_RECORD_BYTES, "operational v2 settled custody"); }
  catch (error) { fail("capability v2 queue settled custody is unreadable or invalid", error); }
  return validateOperationalSettledCustody(record, { grant: decision.grant, operationId, bindingSha256: operationalBindingSha256(decision.grant) });
}

// Determine the durable outcome of a launch from the operational runtime's own
// custody and receipts, cross-bound to the queue's authentic decision grant.
// Returns a reconciled result { outcome, receipt }: "completed-with-proof",
// "uncertain", or "no-effect". A receipt alone is never proof of completion: it
// must be bound to the exact prior grant AND a durably settled runtime custody
// that hash-links to that same receipt. The validated receipt is returned with
// the outcome so the caller can build the recovered result from that one
// already-validated immutable reconciliation rather than re-reading the mutable
// receipt/custody files after the proof decision.
async function recoveryOutcome(cfg, decision, operationId) {
  const custodyDir = join(cfg.stateRoot, "v2-runtime", "custody");
  const settled = await validateSettledCustody(cfg, decision, operationId);
  const uncertain = join(custodyDir, `${operationId}.uncertain-rejected.json`);
  const active = join(custodyDir, `${operationId}.json`);
  const receiptPath = join(cfg.stateRoot, "v2-runtime", "receipts", `${operationId}.json`);
  if (settled) {
    const receipt = await authenticatedReceipt(cfg, decision, operationId);
    if (!receipt || sha(receipt) !== settled.receiptSha256) fail("capability v2 queue settled custody receipt tampering; fail closed");
    // The SSH receipt intentionally omits hostname and approval-consumption
    // details.  If the detailed runtime result was not staged before a crash,
    // do not fabricate those fields from mutable config and do not replay the
    // already-settled connection: report the honest ambiguous terminal.
    // The public-retrieval receipt is likewise content-free (brokered retrieval
    // result is not reconstructable from the receipt alone), so it is held to
    // the same fail-closed rule: an unstaged detailed result is uncertain.
    if (decision.grant.operation.lane === "ssh-hostname") return { outcome: "uncertain", receipt: null };
    if (decision.grant.operation.lane === "public-retrieval") return { outcome: "uncertain", receipt: null };
    return { outcome: "completed-with-proof", receipt };
  }
  // A receipt without durable settled custody is not proof of completion, but it
  // must still authenticate to the exact grant (a forged receipt fails closed).
  if (await exists(receiptPath)) {
    await authenticatedReceipt(cfg, decision, operationId);
    return { outcome: "uncertain", receipt: null };
  }
  if (await exists(uncertain) || await exists(active)) return { outcome: "uncertain", receipt: null };
  return { outcome: "no-effect", receipt: null };
}

async function buildRecoveredResult(cfg, decision, operationId, outcome, receipt) {
  if (outcome === "completed-with-proof") {
    if (decision.grant.operation.lane !== "filesystem") fail("capability v2 queue cannot reconstruct a non-filesystem detailed result after launch");
    const receiptSha256 = sha(receipt);
    const structuredContent = { operationId, lane: "filesystem", status: "succeeded", path: decision.grant.operation.relativePath, outputSha256: receipt.outputSha256, outputBytes: receipt.outputBytes, receiptSha256 };
    return Object.freeze({
      $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
      schemaVersion: 2, grantId: decision.grant.grantId, operationId, lane: "filesystem", status: "succeeded", enabled: true,
      effect: "workspace-write", receiptSha256, outputSha256: receipt.outputSha256, outputBytes: receipt.outputBytes,
      structuredContent, authority: { ...responseAuthority }, boundary: operationalResultBoundary,
    });
  }
  return Object.freeze({ schemaVersion: 2, status: outcome === "uncertain" ? "uncertain-recovered" : "no-effect", operationId });
}

function responseWithoutCreatedAt(value) {
  const copy = structuredClone(value);
  delete copy.createdAt;
  return copy;
}

async function stageResponse(paths, response) {
  const stage = join(paths.active, "response.stage.json");
  if (!(await exists(stage))) await writeAtomic(stage, response, MAX_RESPONSE_BYTES);
  else {
    const existing = await privateJson(stage, MAX_RESPONSE_BYTES, "capability v2 queue staged response");
    schema("capability v2 queue staged response", validateWorkCapabilityQueueResponseV2(existing));
    if (canonical(responseWithoutCreatedAt(existing)) !== canonical(responseWithoutCreatedAt(response))) fail("capability v2 queue staged response differs from settlement");
    return existing;
  }
  return response;
}

async function settledResponse(paths, request, cfg, head, lane) {
  const response = await privateJson(join(paths.active, "response.stage.json"), MAX_RESPONSE_BYTES, "capability v2 queue staged settled response");
  schema("capability v2 queue staged settled response", validateWorkCapabilityQueueResponseV2(response));
  if (response.requestId !== request.requestId || response.jobId !== cfg.authorization.jobId || response.authorizationSha256 !== sha(cfg.authorization) || response.checkpointSha256 !== sha(cfg.checkpoint) || response.lane !== lane) fail("capability v2 queue staged settled response binding drift");
  if (response.status !== head.outcome || response.operationId !== head.operationId || response.receiptSha256 !== head.receiptSha256) fail("capability v2 queue staged settled response differs from durable custody");
  return response;
}

async function archive(paths, request, cfg, response) {
  response = await stageResponse(paths, response);
  const stage = join(paths.active, "response.stage.json");
  const destination = join(paths.responses, `${request.requestId}.json`);
  const published = await readJson(destination, MAX_RESPONSE_BYTES, "capability v2 queue response").catch(() => null);
  if (!published) { await rename(stage, destination); await fsyncDirectory(paths.responses); }
  else if (canonical(published) !== canonical(response)) fail("capability v2 queue published response differs from settlement");
  const history = join(paths.history, request.requestId);
  if (!(await exists(history))) await rename(paths.active, history);
  await fsyncDirectory(paths.history);
  await unlink(paths.current).catch(() => {});
  await fsyncDirectory(paths.root);
  return response;
}

function dependencies(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("capability v2 queue dependencies are invalid");
  for (const name of ["executeRuntime", "afterTransition", "writeClaim"]) if (value[name] !== undefined && typeof value[name] !== "function") fail("capability v2 queue dependency is invalid");
  return { executeRuntime: value.executeRuntime ?? executeRuntimeDispatch, afterTransition: value.afterTransition ?? (async () => {}), writeClaim: value.writeClaim ?? undefined };
}

async function processCore({ queueRoot, config: candidate, clock = () => new Date(), suffixes = {}, deps }) {
  const { config: cfg, paths } = await config(queueRoot);
  if (candidate !== undefined && canonical(candidate) !== canonical(cfg)) fail("capability v2 queue config seam differs from the immutable root");
  if (await exists(paths.terminal)) return Object.freeze({ schemaVersion: 2, status: "stopped", enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
  const acquired = await acquire(paths, cfg, clock, suffixes, deps);
  if (!acquired) return Object.freeze({ schemaVersion: 2, status: "idle", enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
  const request = acquired.request;
  const lane = requestLane(request, cfg);
  const runtime = { v2: { config: { stateRoot: cfg.stateRoot, workspaceRoot: cfg.workspaceRoot, clock, ...(lane === "ssh-hostname" && cfg.ssh ? { ssh: structuredClone(cfg.ssh) } : {}), ...(lane === "public-retrieval" && cfg.research ? { courierQueueRoot: cfg.research.courierQueueRoot, timeoutMilliseconds: cfg.research.timeoutMilliseconds, pollMilliseconds: cfg.research.pollMilliseconds } : {}) } } };
  let chain = await loadCustody(paths, request, cfg), head = chain.head;
  if (head.phase === "claimed" && lane === "ssh-hostname") {
    const approvalPath = join(paths.active, "approval.json");
    if (!(await exists(approvalPath))) {
      const approval = sshApprovalFor(request, cfg, observed(clock, "capability v2 queue SSH approval"), suffixes.approvalNonce);
      await writeAtomic(approvalPath, approval, MAX_RECORD_BYTES);
      await deps.afterTransition("approval-proposed");
    } else await loadSshApproval(paths, request, cfg);
    head = await appendCustody(paths, request, cfg, { phase: "awaiting-owner-signature", operationId: null }, clock, suffixes.awaitingOwnerSignature);
    await deps.afterTransition("awaiting-owner-signature");
  }
  if (head.phase === "awaiting-owner-signature") {
    const approval = await loadSshApproval(paths, request, cfg);
    const now = observed(clock, "capability v2 queue SSH approval wait");
    if (now.getTime() >= Date.parse(approval.expiresAt)) {
      const response = await stageResponse(paths, responseFor({ request, cfg, lane, status: "failed-contained", now: observed(clock, "capability v2 queue expired SSH response") }));
      head = await appendCustody(paths, request, cfg, { phase: "settled", operationId: null, outcome: "failed-contained" }, clock, suffixes.settled);
      await deps.afterTransition("settled");
      const published = await archive(paths, request, cfg, response);
      return Object.freeze({ schemaVersion: 2, status: published.status, response: published, enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
    }
    const signatureRecord = await loadSshSignatureRecord(paths, request, approval);
    if (!signatureRecord) return Object.freeze({ schemaVersion: 2, status: "awaiting-owner-signature", requestId: request.requestId, approvalSha256: sha(approval), enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
    if (!cfg.ssh) fail("capability v2 queue SSH decision has no protected runtime config");
    const verified = await verifyCapabilitySshApprovalV2({ approval, signature: signatureRecord.signature, allowedSignersPath: cfg.ssh.allowedSignersPath, identity: cfg.ssh.operatorIdentity, clock: () => now });
    if (verified.approvalSha256 !== signatureRecord.approvalSha256 || verified.signatureSha256 !== signatureRecord.signatureSha256 || verified.signerIdentity !== signatureRecord.signerIdentity) fail("capability v2 queue SSH signature record is no longer cryptographically valid");
    const decisionPath = join(paths.active, "decision.json");
    let decision;
    if (await exists(decisionPath)) {
      decision = await loadDecision(paths, request, cfg);
      validateGrant(decision, request, cfg, () => now);
      if (canonical(decision.runtimeRequest?.approval) !== canonical(approval) || decision.runtimeRequest?.approvalSignature !== signatureRecord.signature) fail("capability v2 queue staged SSH decision differs from the verified owner signature");
    } else {
      decision = { schemaVersion: 2, grant: null, runtimeRequest: null, boundary: decisionBoundary };
      let compiled;
      try {
        compiled = compileCapabilityOperationalV2SshHostname({
          plan: cfg.plan, lease: cfg.lease, consumption: cfg.consumption, checkpoint: cfg.checkpoint,
          pack: cfg.pack, expectedPackSha256: cfg.expectedPackSha256, policy: cfg.policy,
          tool: request.tool, approval, approvalSignature: signatureRecord.signature,
          now, grantSuffix: suffix(suffixes.grant), operationSuffix: suffixes.operation ?? randomBytes(8).toString("hex"),
        });
      } catch (error) {
        fail("capability v2 queue production controller rejected the owner-signed SSH request before operational custody or effect", error);
      }
      decision.grant = compiled.grant; decision.runtimeRequest = compiled.runtimeRequest;
      validateGrant(decision, request, cfg, () => now);
      await writeAtomic(decisionPath, decision, MAX_RECORD_BYTES);
      await deps.afterTransition("decided");
    }
    head = await appendCustody(paths, request, cfg, { phase: "authorized", operationId: decision.grant.operation.id, decisionSha256: sha(decision), grantId: decision.grant.grantId, grantSha256: sha(decision.grant) }, clock, suffixes.authorized);
    await deps.afterTransition("authorized");
  }
  if (head.phase === "claimed" && lane === "public-retrieval") {
    const decisionPath = join(paths.active, "decision.json");
    let decision;
    if (await exists(decisionPath)) {
      decision = await loadDecision(paths, request, cfg);
      validateGrant(decision, request, cfg, clock);
    } else {
      decision = { schemaVersion: 2, grant: null, runtimeRequest: null, boundary: decisionBoundary };
      let compiled;
      try {
        compiled = compileCapabilityOperationalV2PublicRetrieval({
          plan: cfg.plan, lease: cfg.lease, consumption: cfg.consumption, checkpoint: cfg.checkpoint,
          pack: cfg.pack, expectedPackSha256: cfg.expectedPackSha256, policy: cfg.policy,
          tool: request.tool, query: request.query, sourceTypes: request.sourceTypes, domains: request.domains,
          maxResults: request.maxResults, maxSourcesToFetch: request.maxSourcesToFetch, maxSourceBytes: request.maxSourceBytes,
          now: observed(clock, "capability v2 queue public-retrieval decision"), grantSuffix: suffix(suffixes.grant), operationSuffix: suffixes.operation ?? randomBytes(8).toString("hex"),
        });
      } catch (error) {
        fail("capability v2 queue production controller rejected the request before custody or effect", error);
      }
      decision.grant = compiled.grant; decision.runtimeRequest = compiled.runtimeRequest;
      validateGrant(decision, request, cfg, clock);
      await writeAtomic(decisionPath, decision, MAX_RECORD_BYTES);
      await deps.afterTransition("decided");
    }
    head = await appendCustody(paths, request, cfg, { phase: "authorized", operationId: decision.grant.operation.id, decisionSha256: sha(decision), grantId: decision.grant.grantId, grantSha256: sha(decision.grant) }, clock, suffixes.authorized);
    await deps.afterTransition("authorized");
  }
  if (head.phase === "claimed" && lane === "filesystem") {
    const decisionPath = join(paths.active, "decision.json");
    let decision;
    if (await exists(decisionPath)) {
      decision = await loadDecision(paths, request, cfg);
      validateGrant(decision, request, cfg, clock);
    } else {
      decision = { schemaVersion: 2, grant: null, runtimeRequest: null, boundary: decisionBoundary };
      let compiled;
      try {
        compiled = compileCapabilityOperationalV2({
          plan: cfg.plan, lease: cfg.lease, consumption: cfg.consumption, checkpoint: cfg.checkpoint,
          pack: cfg.pack, expectedPackSha256: cfg.expectedPackSha256, policy: cfg.policy,
          tool: request.tool, relativePath: request.relativePath, content: request.content,
          now: observed(clock, "capability v2 queue decision"), grantSuffix: suffix(suffixes.grant), operationSuffix: suffixes.operation ?? randomBytes(8).toString("hex"),
        });
      } catch (error) {
        fail("capability v2 queue production controller rejected the request before custody or effect", error);
      }
      decision.grant = compiled.grant; decision.runtimeRequest = compiled.runtimeRequest;
      validateGrant(decision, request, cfg, clock);
      await writeAtomic(decisionPath, decision, MAX_RECORD_BYTES);
      await deps.afterTransition("decided");
    }
    head = await appendCustody(paths, request, cfg, { phase: "authorized", operationId: decision.grant.operation.id, decisionSha256: sha(decision), grantId: decision.grant.grantId, grantSha256: sha(decision.grant) }, clock, suffixes.authorized);
    await deps.afterTransition("authorized");
  }
  if (head.phase === "settled") {
    const response = await settledResponse(paths, request, cfg, head, lane);
    const published = await archive(paths, request, cfg, response);
    return Object.freeze({ schemaVersion: 2, status: published.status, response: published, enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
  }
  const decision = await loadDecision(paths, request, cfg);
  const { grant, operationId, runtimeRequest } = validateGrant(decision, request, cfg, clock, { requireCurrent: head.phase === "authorized" });
  if (head.phase === "authorized") {
    head = await appendCustody(paths, request, cfg, { phase: "launching", operationId, decisionSha256: sha(decision), grantId: grant.grantId, grantSha256: sha(grant) }, clock, suffixes.launching);
    await deps.afterTransition("launching");
    let result;
    try {
      result = await deps.executeRuntime({ ...runtime, grant, runtimeRequest });
    } catch (error) {
      fail("capability v2 queue runtime did not return a settleable result; recovery is required", error);
    }
    const checked = validateRuntimeResult(result, decision, cfg);
    const receipt = await authenticatedReceipt(cfg, decision, operationId);
    if (!receipt || sha(receipt) !== checked.result.receiptSha256) fail("capability v2 queue runtime result receipt is not durably authenticated");
    await writeAtomic(join(paths.active, "runtime-result.json"), checked.result, MAX_RESPONSE_BYTES);
    await deps.afterTransition("runtime-result-staged");
    head = await appendCustody(paths, request, cfg, { phase: "runtime-returned", operationId, decisionSha256: sha(decision), grantId: grant.grantId, grantSha256: sha(grant), receiptSha256: checked.result.receiptSha256, outcome: "success" }, clock, suffixes.runtimeReturned);
    await deps.afterTransition("runtime-returned");
  }
  if (head.phase === "launching") {
    const resultPath = join(paths.active, "runtime-result.json");
    if (!(await exists(resultPath))) {
      // Reconcile ONCE against the runtime's durable custody/receipts (cross-bound
      // to the exact decision grant) and build the recovered result from that one
      // already-validated immutable result. The completed-with-proof branch
      // consumes the validated receipt returned by recoveryOutcome and never
      // re-reads the mutable receipt/custody files after the proof decision.
      const reconciled = await recoveryOutcome(cfg, decision, operationId);
      await writeAtomic(resultPath, await buildRecoveredResult(cfg, decision, operationId, reconciled.outcome, reconciled.receipt), MAX_RESPONSE_BYTES);
    }
    const recovered = await privateJson(resultPath, MAX_RESPONSE_BYTES, "capability v2 queue recovered result");
    const outcome = recovered.status === "uncertain-recovered" ? "uncertain" : recovered.status === "no-effect" ? "no-effect" : "completed-with-proof";
    head = await appendCustody(paths, request, cfg, { phase: "runtime-returned", operationId, decisionSha256: sha(decision), grantId: grant.grantId, grantSha256: sha(grant), receiptSha256: outcome === "completed-with-proof" ? recovered.receiptSha256 : null, outcome }, clock, suffixes.runtimeReturned);
    await deps.afterTransition("runtime-returned");
  }
  if (head.phase === "runtime-returned") {
    const raw = await privateJson(join(paths.active, "runtime-result.json"), MAX_RESPONSE_BYTES, "capability v2 queue runtime result");
    let status, operationIdOut, receiptSha256 = null, outputSha256 = null, outputBytes = null, structuredContent = null;
    if (raw.status === "succeeded") {
      status = "succeeded"; operationIdOut = raw.operationId; receiptSha256 = raw.receiptSha256; outputSha256 = raw.outputSha256; outputBytes = raw.outputBytes; structuredContent = raw.structuredContent;
    } else if (raw.status === "uncertain-recovered") {
      status = "uncertain-no-replay"; operationIdOut = raw.operationId;
    } else {
      status = "failed-contained"; operationIdOut = raw.operationId;
    }
    const response = await stageResponse(paths, responseFor({ request, cfg, lane, status, operationId: operationIdOut, receiptSha256, outputSha256, outputBytes, structuredContent, now: observed(clock, "capability v2 queue response") }));
    head = await appendCustody(paths, request, cfg, { phase: "settled", operationId, decisionSha256: sha(decision), grantId: grant.grantId, grantSha256: sha(grant), receiptSha256, outcome: status }, clock, suffixes.settled);
    await deps.afterTransition("settled");
    const published = await archive(paths, request, cfg, response);
    return Object.freeze({ schemaVersion: 2, status: published.status, response: published, enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
  }
  fail("capability v2 queue reached an unsupported custody phase");
}

// Internal-only seam: the full durable processing implementation is exported
// from this INTERNAL module and is imported ONLY by the closed production wrapper
// and the test-support module. It accepts an optional deps seam (executeRuntime /
// afterTransition) for deterministic crash/fault tests.
export async function processCapabilityToolQueueV2Core({ queueRoot, config: candidate, clock = () => new Date(), suffixes = {}, deps = {} }) {
  const resolved = dependencies(deps);
  return processCore({ queueRoot, config: candidate, clock, suffixes, deps: resolved });
}

// Closed production processing path. Accepts ONLY an immutable queue config; the
// release-manifest enabled gate is enforced here against the immutable root. This
// exposes no clock, suffix, dependency, runtime, transition, or executor seam, so
// production importers can never reach an injected runtime. It loads the immutable
// config through the existing config seam, verifies the candidate is canonically
// identical, requires enabled === true, then runs the private processCore with a
// fresh production clock and empty suffixes so only real production dependencies
// are used.
export async function processCapabilityToolQueueV2ProductionClosed({ queueRoot, config: candidate }) {
  const { config: cfg } = await config(queueRoot);
  if (candidate !== undefined && canonical(candidate) !== canonical(cfg)) fail("capability v2 queue config seam differs from the immutable root");
  if (cfg.enabled !== true) fail("capability v2 queue is not enabled for production closure");
  const resolved = dependencies({});
  return processCore({ queueRoot, config: cfg, clock: monotonicProductionClock(), suffixes: {}, deps: resolved });
}

export async function statusCapabilityToolQueueV2({ queueRoot, config: candidate }) {
  const { config: cfg, paths } = await config(queueRoot);
  if (candidate !== undefined && canonical(candidate) !== canonical(cfg)) fail("capability v2 queue config seam differs from the immutable root");
  const requests = await readdir(paths.requests), responses = await readdir(paths.responses), history = await readdir(paths.history);
  const active = await exists(paths.current), terminal = await exists(paths.terminal);
  if (requests.length > MAX_QUEUE || requests.some((name) => !/^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(name)) || responses.some((name) => !/^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(name)) || history.some((name) => !REQUEST_RE.test(name))) fail("capability v2 queue status exceeds its configuration or contains an unsafe entry");
  if (terminal) {
    const value = await privateJson(paths.terminal, MAX_RECORD_BYTES, "capability v2 queue terminal record");
    exactKeys(value, ["schemaVersion", "requestId", "authorizationSha256", "responseSha256", "terminalState", "reason", "boundary"], "capability v2 queue terminal record");
    if (value.schemaVersion !== 2 || !REQUEST_RE.test(value.requestId ?? "") || value.authorizationSha256 !== sha(cfg.authorization) || !SHA_RE.test(value.responseSha256 ?? "") || value.boundary !== custodyBoundary) fail("capability v2 queue terminal record is invalid");
  }
  return Object.freeze({ schemaVersion: 2, operation: "pixel-work-capability-tool-queue-v2-status", status: `${active ? "active" : terminal ? "stopped" : "idle"}-${cfg.enabled ? "enabled" : "disabled"}`, queued: requests.length, settled: responses.length, active, terminal, enabled: cfg.enabled, authority: { ...authority }, boundary: custodyBoundary });
}

export async function readCapabilityToolResponseV2({ queueRoot, config: candidate, requestId }) {
  if (!REQUEST_RE.test(requestId ?? "")) fail("capability v2 response identity is invalid");
  const { config: cfg, paths } = await config(queueRoot);
  if (candidate !== undefined && canonical(candidate) !== canonical(cfg)) fail("capability v2 queue config seam differs from the immutable root");
  const response = await privateJson(join(paths.responses, `${requestId}.json`), MAX_RESPONSE_BYTES, "capability v2 queue response");
  schema("capability v2 queue response", validateWorkCapabilityQueueResponseV2(response));
  if (response.requestId !== requestId || response.jobId !== cfg.authorization.jobId || response.authorizationSha256 !== sha(cfg.authorization) || response.checkpointSha256 !== sha(cfg.checkpoint)) fail("capability v2 queue response differs from its queue configuration");
  return response;
}

export const capabilityToolQueueV2Boundary = custodyBoundary;
export const capabilityToolQueueV2Authority = authority;
export const capabilityToolQueueV2ConfigBoundary = configBoundary;

// Internal import surface for the closed production wrapper only. These are NOT
// part of the public v2 queue API; production modules other than the wrapper must
// never import this core module (a static gate forbids it).
export { config, exists, fail, authority, custodyBoundary, writeClaimExclusive, CLAIM_COLLISION };
