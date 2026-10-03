import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, readdir, realpath, rename, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve, sep } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { writeOwnerPrivateCreateNoClobber, fsyncDirectory } from "./maintenance-secure-files.mjs";
import { executePixelResearchTool } from "../work-runner/research-tool.mjs";

// ---------------------------------------------------------------------------
// Pixel v2 runtime — integrated vertical slice (replaces the rejected
// standalone direct-fetch facade).
//
// Reused seams (no parallel schema/broker/auth invented here):
//   * Web Courier public retrieval  -> ../work-runner/research-tool.mjs
//     (hash-bound research-broker queue + receipts; NO global fetch, NO
//     origin allowlist boundary).
//   * secure-files primitives       -> scripts/lib/secure-files.mjs and
//     ./maintenance-secure-files.mjs (owner-private create-only durable
//     publication used for custody claims and workspace writes).
//   * durable single-winner custody -> create-only wx claim under
//     <stateRoot>/v2-runtime/custody, acquired BEFORE any effect and moved to
//     one durable terminal state (settled / uncertain-rejected) on outcome,
//     recovered fail-closed (never blind replay of an uncertain prior attempt).
//   * approval seam                 -> injected external decision provider
//     (never a caller-supplied symmetric string / self-computable token).
//
// Default is disabled. release-manifest runtimeEnabled remains false. Nothing
// here is production-enabled: the durable queue/profile service must route a
// version-bound request into these lanes through its injected runtime seam.
// ---------------------------------------------------------------------------

export class CapabilityRuntimeV2Error extends Error {}
function fail(message) { throw new CapabilityRuntimeV2Error(message); }

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) {
    fail(`${label} shape is invalid`);
  }
}

function timestamp(config, label) {
  const value = (config.clock ?? (() => new Date()))();
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`v2 runtime ${label} time is invalid`);
  return value.toISOString();
}

function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is outside the v2 runtime envelope`);
  return value;
}

function operationId(value) {
  if (typeof value !== "string" || !/^workcapv2-[0-9]{13}-[a-f0-9]{16}$/u.test(value)) fail("v2 operation id is invalid");
  return value;
}

function proposalId(value) {
  if (typeof value !== "string" || !/^workcapv2ext-[0-9]{13}-[a-f0-9]{16}$/u.test(value)) fail("v2 external-effect proposal id is invalid");
  return value;
}

function absoluteDirectory(value, label) {
  if (typeof value !== "string" || value.length === 0 || value.includes("\0") || !isAbsolute(value) || resolve(value) !== value || resolve(value) === dirname(resolve(value))) {
    fail(`${label} must be an absolute non-root directory`);
  }
  return value;
}

const CONFIG_KEYS = Object.freeze([
  "runtimeVersion", "runtimeEnabled", "lanes", "stateRoot", "workspaceRoot", "courierQueueRoot",
  "loopbackAllowlist", "timeoutMs", "maxOutputBytes", "maxWorkspaceFileBytes",
  "courierPollMilliseconds", "externalDecisionProvider", "fetchImpl", "clock",
]);
const CONFIG_REQUIRED = Object.freeze([
  "runtimeVersion", "runtimeEnabled", "lanes", "stateRoot", "timeoutMs", "maxOutputBytes",
  "maxWorkspaceFileBytes", "courierPollMilliseconds",
]);
const LANE_KEYS = Object.freeze(["publicRetrieval", "filesystem", "loopback", "ambiguousExternalEffect"]);

const AUTHORITY = Object.freeze({
  grantsFutureExecution: false, grantsReplay: false, grantsGenericShell: false, grantsRawNetwork: false,
  grantsCredentials: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false,
});
const BOUNDARY = "Content-free opt-in v2 runtime receipt for one exact single-use Tier-B operation under durable single-winner custody. It grants no replay, future execution, image pull, generic shell, raw network, credential, external effect, scope expansion, controller registration, or completion authority.";
const CUSTODY_BOUNDARY = "Private content-free single-winner custody for one exact v2 operation. It proves one claimant acquired the operation before any effect and grants no execution, replay, network, credential, external-effect, scope-expansion, or completion authority.";
const RECEIPT_BOUNDARY = "Content-free v2 runtime receipt recorded only after a completed effect. It proves completion custody and grants no replay, future execution, raw network, credential, external effect, scope expansion, or completion authority.";
const LOOPBACK_HOSTS = Object.freeze(new Set(["127.0.0.1", "localhost", "::1", "[::1]"]));

function boolFlag(value) { return value === true; }

function checkedConfig(config) {
  if (!config || typeof config !== "object" || Array.isArray(config)) fail("v2 runtime configuration is invalid");
  for (const key of Object.keys(config)) if (!CONFIG_KEYS.includes(key)) fail(`v2 runtime configuration contains an unknown key: ${key}`);
  for (const key of CONFIG_REQUIRED) if (config[key] === undefined) fail(`v2 runtime configuration is missing required key: ${key}`);
  const lanesRaw = config.lanes ?? {};
  if (typeof lanesRaw !== "object" || Array.isArray(lanesRaw)) fail("v2 runtime lanes are invalid");
  exactKeys(lanesRaw, LANE_KEYS, "v2 runtime lanes");
  const enabled = boolFlag(config.runtimeEnabled);
  const lanes = {
    publicRetrieval: boolFlag(lanesRaw.publicRetrieval) && enabled,
    filesystem: boolFlag(lanesRaw.filesystem) && enabled,
    loopback: boolFlag(lanesRaw.loopback) && enabled,
    ambiguousExternalEffect: boolFlag(lanesRaw.ambiguousExternalEffect) && enabled,
  };
  const stateRoot = absoluteDirectory(config.stateRoot, "v2 runtime stateRoot");
  const workspaceRoot = config.workspaceRoot == null ? null : absoluteDirectory(config.workspaceRoot, "v2 runtime workspaceRoot");
  const courierQueueRoot = config.courierQueueRoot == null ? null : absoluteDirectory(config.courierQueueRoot, "v2 runtime courierQueueRoot");
  const timeoutMs = integer(config.timeoutMs, 1, 60000, "timeoutMs");
  const maxOutputBytes = integer(config.maxOutputBytes, 1, 8 * 1024 * 1024, "maxOutputBytes");
  const maxWorkspaceFileBytes = integer(config.maxWorkspaceFileBytes, 1, 64 * 1024 * 1024, "maxWorkspaceFileBytes");
  const courierPollMilliseconds = integer(config.courierPollMilliseconds, 1, 1000, "courierPollMilliseconds");
  const loopbackAllowlist = Array.isArray(config.loopbackAllowlist) ? Object.freeze([...config.loopbackAllowlist]) : Object.freeze([]);
  if (loopbackAllowlist.some((entry) => typeof entry !== "string")) fail("v2 runtime loopbackAllowlist is invalid");
  const provider = config.externalDecisionProvider == null ? null : config.externalDecisionProvider;
  if (provider !== null && (typeof provider !== "object" || typeof provider.verify !== "function")) fail("v2 runtime externalDecisionProvider is invalid");
  if (config.runtimeVersion !== 2) fail("v2 runtime requires runtimeVersion 2");
  return Object.freeze({
    runtimeVersion: 2,
    runtimeEnabled: enabled,
    lanes: Object.freeze(lanes),
    stateRoot,
    workspaceRoot,
    courierQueueRoot,
    timeoutMs,
    maxOutputBytes,
    maxWorkspaceFileBytes,
    courierPollMilliseconds,
    loopbackAllowlist,
    externalDecisionProvider: provider,
    fetchImpl: typeof config.fetchImpl === "function" ? config.fetchImpl : globalThis.fetch,
    clock: typeof config.clock === "function" ? config.clock : () => new Date(),
  });
}

function assertEnabled(c, lane) {
  if (!c.runtimeEnabled || !c.lanes[lane]) fail(`v2 runtime lane ${lane} is not enabled`);
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return resolve(path);
}

async function storeRoot(c) {
  const root = await privateDirectory(c.stateRoot, "v2 state root", true);
  const runtime = await privateDirectory(join(root, "v2-runtime"), "v2 runtime root", true);
  const custody = await privateDirectory(join(runtime, "custody"), "v2 custody root", true);
  const receipts = await privateDirectory(join(runtime, "receipts"), "v2 receipt root", true);
  const external = await privateDirectory(join(runtime, "external"), "v2 external root", true);
  return { root, runtime, custody, receipts, external };
}

async function exists(path) { return lstat(path).then(() => true, () => false); }

async function claimCustody(c, operationId, intent) {
  const store = await storeRoot(c);
  const path = join(store.custody, `${operationId}.json`);
  const settled = join(store.custody, `${operationId}.settled.json`);
  const uncertain = join(store.custody, `${operationId}.uncertain-rejected.json`);
  const receipt = join(store.receipts, `${operationId}.json`);
  if (await exists(path) || await exists(settled) || await exists(uncertain) || await exists(receipt)) fail("v2 operation already has custody or a terminal; recover or settle it before continuing (no blind replay)");
  const claimedAt = timestamp(c, "claim");
  const record = {
    schemaVersion: 1,
    operation: "pixel-work-capability-runtime-v2-custody",
    operationId,
    lane: intent.lane,
    state: "in-progress",
    bindingSha256: sha({ operationId, lane: intent.lane, intent: intent.binding }),
    claimedAt,
    boundary: CUSTODY_BOUNDARY,
  };
  try {
    await writeOwnerPrivateCreateNoClobber(path, `${JSON.stringify(record, null, 2)}\n`, process.geteuid());
  } catch (error) {
    if (error?.message?.includes("already exists")) fail("v2 operation already has custody; recover or settle it before continuing (no blind replay)");
    throw error;
  }
  // Close the inspect/create race before any effect. A delayed claimant can
  // create the active name after the prior winner has renamed its claim to a
  // terminal; that terminal (or its already-durable receipt) must defeat the
  // new claim before the caller reaches a broker, file mutation, or fetch.
  if (await exists(settled) || await exists(uncertain) || await exists(receipt)) {
    await unlink(path).catch(() => {});
    await fsyncDirectory(store.custody);
    fail("v2 operation already has custody or reached a terminal while claiming; no blind replay");
  }
  return { store, path, record };
}

async function writeReceipt(c, store, receipt) {
  const path = join(store.receipts, `${receipt.operationId}.json`);
  if (await exists(path)) fail("v2 operation already has a receipt");
  await writeOwnerPrivateCreateNoClobber(path, `${JSON.stringify(receipt, null, 2)}\n`, process.geteuid());
  await fsyncDirectory(store.receipts);
  return sha(receipt);
}

async function settleCustody(store, operationId) {
  const from = join(store.custody, `${operationId}.json`);
  const to = join(store.custody, `${operationId}.settled.json`);
  if (await exists(to)) return;
  if (!(await exists(from))) return;
  await rename(from, to);
  await fsyncDirectory(store.custody);
}

async function statusStore(c) {
  const root = resolve(c.stateRoot);
  const runtime = join(root, "v2-runtime");
  const custody = join(runtime, "custody");
  const receipts = join(runtime, "receipts");
  const external = join(runtime, "external");
  return { root, runtime, custody, receipts, external };
}

function receiptFor({ c, operationId: id, lane, startedAt, completedAt, status, failureClass = null, outputSha256 = null, outputBytes = 0, externalEffects = false }) {
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-work-capability-runtime-v2-receipt",
    operationId: id,
    lane,
    status,
    startedAt,
    completedAt,
    failureClass,
    outputSha256,
    outputBytes,
    externalEffects,
    authority: { ...AUTHORITY },
    boundary: RECEIPT_BOUNDARY,
  });
}

// ---------------------------------------------------------------------------
// Web Courier public retrieval (hash-bound research-broker queue + receipts).
// ---------------------------------------------------------------------------
export async function v2ExecutePublicRetrieval(config, request) {
  const c = checkedConfig(config); assertEnabled(c, "publicRetrieval");
  const id = operationId(request.operationId);
  const startedAt = timestamp(c, "public-retrieval");
  const { store, record } = await claimCustody(c, id, { lane: "public-retrieval", binding: { operationId: id } });
  let response;
  try {
    if (c.courierQueueRoot === null) fail("v2 public retrieval requires a courierQueueRoot (Web Courier queue)");
    const result = await executePixelResearchTool({
      query: request.query,
      sourceTypes: request.sourceTypes,
      domains: request.domains,
      maxResults: request.maxResults,
      maxSourcesToFetch: request.maxSourcesToFetch,
      maxSourceBytes: request.maxSourceBytes,
    }, request.signal, {
      queueRoot: c.courierQueueRoot,
      timeoutMilliseconds: c.timeoutMs,
      pollMilliseconds: c.courierPollMilliseconds,
    });
    if (result?.isError) throw new CapabilityRuntimeV2Error(`public retrieval courier failed closed: ${result?.content?.[0]?.text ?? "unknown"}`);
    response = result;
  } catch (error) {
    // Uncertain state is preserved in custody; recovery never blind-replays.
    throw new CapabilityRuntimeV2Error(`public retrieval failed closed: ${error?.message ?? "unknown"}`);
  }
  const completedAt = timestamp(c, "public-retrieval");
  const digest = sha(JSON.stringify(response));
  const receipt = receiptFor({ c, operationId: id, lane: "public-retrieval", startedAt, completedAt, status: "succeeded", outputSha256: digest, outputBytes: Buffer.byteLength(JSON.stringify(response)), externalEffects: false });
  const receiptSha256 = await writeReceipt(c, store, receipt);
  await settleCustody(store, id);
  return Object.freeze({
    operationId: id,
    lane: "public-retrieval",
    status: "succeeded",
    response,
    receiptSha256,
    outputSha256: digest,
    authority: { ...AUTHORITY },
    boundary: BOUNDARY,
  });
}

// ---------------------------------------------------------------------------
// Filesystem lane — secure-files + workspace containment.
// ---------------------------------------------------------------------------
async function resolveWorkspace(c) {
  if (c.workspaceRoot === null) fail("v2 filesystem lane requires a workspaceRoot");
  const resolved = resolve(c.workspaceRoot);
  const actual = await realpath(c.workspaceRoot).catch(() => fail("v2 workspace root is not a real directory"));
  if (actual !== resolved) fail("v2 workspace root must be a real non-symlink directory");
  const info = await lstat(actual);
  if (!info.isDirectory() || info.isSymbolicLink()) fail("v2 workspace root is not a real directory");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("v2 workspace root is not owner-only");
  return actual;
}

function containedPath(workspaceReal, relativePath) {
  if (typeof relativePath !== "string" || relativePath.length === 0 || relativePath.includes("\0") || relativePath.includes("\\")) fail("v2 workspace relative path is invalid");
  const segments = relativePath.split("/");
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) fail("v2 workspace relative path escapes the workspace");
  const target = resolve(workspaceReal, relativePath);
  if (target !== workspaceReal && !target.startsWith(workspaceReal + sep)) fail("v2 workspace path escapes the workspace");
  return { target, segments };
}

async function ensureRealParent(workspaceReal, segments, create = false) {
  let current = workspaceReal;
  for (const segment of segments.slice(0, -1)) {
    current = join(current, segment);
    const info = await lstat(current).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (!info) {
      if (!create) fail("v2 workspace parent does not exist");
      await mkdir(current, { mode: 0o700 });
      continue;
    }
    if (!info.isDirectory() || info.isSymbolicLink()) fail("v2 workspace parent is not a real directory");
    if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("v2 workspace parent is not owner-only");
  }
  const parent = join(workspaceReal, ...segments.slice(0, -1));
  const parentReal = await realpath(parent);
  if (parentReal !== workspaceReal && !parentReal.startsWith(workspaceReal + sep)) fail("v2 workspace parent escapes the workspace");
  return parentReal;
}

export async function v2ExecuteFilesystem(config, request) {
  const c = checkedConfig(config); assertEnabled(c, "filesystem");
  const id = operationId(request.operationId);
  const startedAt = timestamp(c, "filesystem");
  const { store, record } = await claimCustody(c, id, { lane: "filesystem", binding: { operationId: id, operation: request.operation, relativePath: request.relativePath } });
  const workspaceReal = await resolveWorkspace(c);
  const { target, segments } = containedPath(workspaceReal, request.relativePath);
  if (request.operation === "read") {
    if (!(await exists(target))) fail("v2 workspace read target does not exist");
    const parentReal = await ensureRealParent(workspaceReal, segments, false);
    const readPath = join(parentReal, basename(segments[segments.length - 1]));
    if ((await lstat(readPath)).isSymbolicLink()) fail("v2 workspace read target must not be a symlink");
    const observed = await readBoundedRegularText(readPath, c.maxWorkspaceFileBytes, "v2 workspace file");
    const completedAt = timestamp(c, "filesystem");
    const digest = sha(observed.text);
    const outputBytes = Buffer.byteLength(observed.text, "utf8");
    const receipt = receiptFor({ c, operationId: id, lane: "filesystem", startedAt, completedAt, status: "succeeded", outputSha256: digest, outputBytes, externalEffects: false });
    const receiptSha256 = await writeReceipt(c, store, receipt);
    await settleCustody(store, id);
    return Object.freeze({ operationId: id, lane: "filesystem", status: "succeeded", content: observed.text, outputSha256: digest, outputBytes, receiptSha256, authority: { ...AUTHORITY }, boundary: BOUNDARY });
  }
  if (request.operation === "write") {
    const parentReal = await ensureRealParent(workspaceReal, segments, true);
    const content = request.content;
    if (typeof content !== "string" || Buffer.byteLength(content, "utf8") > c.maxWorkspaceFileBytes) fail("v2 workspace write content is invalid or exceeds its ceiling");
    const targetName = basename(segments[segments.length - 1]);
    const targetPath = join(parentReal, targetName);
    if (await exists(targetPath)) fail("v2 workspace write target already exists (create-only, no overwrite)");
    await writeOwnerPrivateCreateNoClobber(targetPath, content, process.geteuid());
    const completedAt = timestamp(c, "filesystem");
    const digest = sha(content);
    const receipt = receiptFor({ c, operationId: id, lane: "filesystem", startedAt, completedAt, status: "succeeded", outputSha256: digest, outputBytes: Buffer.byteLength(content, "utf8"), externalEffects: false });
    const receiptSha256 = await writeReceipt(c, store, receipt);
    await settleCustody(store, id);
    return Object.freeze({ operationId: id, lane: "filesystem", status: "succeeded", path: targetName, outputSha256: digest, outputBytes: Buffer.byteLength(content, "utf8"), receiptSha256, authority: { ...AUTHORITY }, boundary: BOUNDARY });
  }
  fail("v2 filesystem operation is unsupported");
}

// ---------------------------------------------------------------------------
// Loopback lane — allowlisted loopback, streaming bounded reader + abort.
// ---------------------------------------------------------------------------
function isLoopbackHost(host) {
  const normalized = host.startsWith("[") ? host.slice(1, -1) : host;
  return LOOPBACK_HOSTS.has(normalized) || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/u.test(normalized);
}

function assertLoopbackEndpoint(c, endpointText, method) {
  if (typeof endpointText !== "string" || method !== "GET") fail("v2 loopback endpoint is invalid: this lane is GET-only and grants no mutating effect");
  let url;
  try { url = new URL(endpointText); } catch { fail("v2 loopback endpoint is not a valid URL"); }
  if (!["http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) fail("v2 loopback endpoint must be a credential-free http URL without query or fragment");
  if (!isLoopbackHost(url.hostname)) fail("v2 loopback endpoint is not a loopback host");
  const canonical = `${url.origin}${url.pathname}`;
  const allowlist = Array.isArray(c.loopbackAllowlist) ? c.loopbackAllowlist : [];
  if (!allowlist.includes(canonical)) fail("v2 loopback endpoint is not allowlisted");
  return { url, canonical };
}

export async function v2ExecuteLoopback(config, request) {
  const c = checkedConfig(config); assertEnabled(c, "loopback");
  const id = operationId(request.operationId);
  const startedAt = timestamp(c, "loopback");
  const { store, record } = await claimCustody(c, id, { lane: "loopback", binding: { operationId: id, method: request.method, endpoint: request.endpoint } });
  const { url } = assertLoopbackEndpoint(c, request.endpoint, request.method);
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, c.timeoutMs);
  const init = { method: request.method, redirect: "manual", signal: controller.signal };
  let response;
  try {
    response = await c.fetchImpl(url.href, init);
  } catch (error) {
    clearTimeout(timer);
    if (timedOut || error?.name === "AbortError") fail("v2 loopback call timed out");
    fail("v2 loopback transport failed");
  }
  if (!response || typeof response.body?.getReader !== "function") { clearTimeout(timer); fail("v2 loopback response is not streamable"); }
  const reader = response.body.getReader();
  const chunks = [];
  let total = 0;
  let exceeded = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) { total += value.length; chunks.push(Buffer.from(value)); }
      if (total > c.maxOutputBytes) { clearTimeout(timer); controller.abort(); exceeded = true; break; }
    }
  } catch (error) {
    if (timedOut || error?.name === "AbortError") fail("v2 loopback call timed out");
    throw new CapabilityRuntimeV2Error("v2 loopback response read failed");
  } finally {
    clearTimeout(timer);
    reader.releaseLock?.();
  }
  if (exceeded) fail("v2 loopback response exceeded its size ceiling");
  const completedAt = timestamp(c, "loopback");
  const body = Buffer.concat(chunks).toString("utf8");
  const digest = sha(body);
  const receipt = receiptFor({ c, operationId: id, lane: "loopback", startedAt, completedAt, status: "succeeded", outputSha256: digest, outputBytes: total, externalEffects: false });
  const receiptSha256 = await writeReceipt(c, store, receipt);
  await settleCustody(store, id);
  return Object.freeze({ operationId: id, lane: "loopback", status: "succeeded", statusCode: response.status, body, outputSha256: digest, outputBytes: total, receiptSha256, authority: { ...AUTHORITY }, boundary: BOUNDARY });
}

// ---------------------------------------------------------------------------
// Ambiguous external effects — pending only; approval via injected external
// decision (never a caller-supplied symmetric string). Execution never granted.
// ---------------------------------------------------------------------------
async function externalStore(c) {
  const store = await storeRoot(c);
  return store;
}

export async function v2ExternalEffectPropose(config, proposal) {
  const c = checkedConfig(config); assertEnabled(c, "ambiguousExternalEffect");
  const id = proposalId(proposal.proposalId);
  const startedAt = timestamp(c, "external-effect");
  const store = await externalStore(c);
  const path = join(store.external, `${id}.json`);
  if (await exists(path)) fail("v2 external-effect proposal already exists (no replay)");
  if (typeof proposal.effect !== "string" || proposal.effect.length === 0 || proposal.effect.length > 4096) fail("v2 external-effect proposal is malformed");
  const proposalSha256 = sha(proposal);
  const record = Object.freeze({ proposalId: id, proposalSha256, state: "pending", zeroExecution: true, externalEffects: false, proposedAt: startedAt, effect: proposal.effect });
  await writeOwnerPrivateCreateNoClobber(path, `${JSON.stringify(record, null, 2)}\n`, process.geteuid());
  await fsyncDirectory(store.external);
  return Object.freeze({ operationId: `ext-${id}`, lane: "ambiguous-external-effect", status: "pending", zeroExecution: true, proposalSha256, proposalId: id, receiptSha256: sha(record) });
}

async function loadPendingExternal(c, id) {
  const store = await externalStore(c);
  const path = join(store.external, `${id}.json`);
  if (!(await exists(path))) fail("v2 runtime has no pending external-effect proposal");
  const observed = await readBoundedRegularText(path, 64 * 1024, "v2 external-effect record");
  let value;
  try { value = JSON.parse(observed.text); } catch { fail("v2 external-effect record is invalid"); }
  if (!value || value.state !== "pending") fail("v2 external-effect proposal is already reconciled");
  return { store, path, record: value };
}

async function terminalWinningExternal(store, id, record) {
  const terminal = join(store.external, `${id}.terminal.json`);
  try {
    await writeOwnerPrivateCreateNoClobber(terminal, `${JSON.stringify(record, null, 2)}\n`, process.geteuid());
  } catch (error) {
    if (error?.message?.includes("already exists")) fail("v2 external-effect proposal already settled; only one terminal winner");
    throw error;
  }
  await fsyncDirectory(store.external);
  await unlink(join(store.external, `${id}.json`)).catch(() => {});
  await fsyncDirectory(store.external);
}

export async function v2ExternalEffectApprove(config, approval) {
  const c = checkedConfig(config); assertEnabled(c, "ambiguousExternalEffect");
  const id = proposalId(approval.proposalId);
  const { store, record } = await loadPendingExternal(c, id);
  if (approval.proposalSha256 !== record.proposalSha256) fail("v2 external-effect approval binds the wrong proposal");
  if (c.externalDecisionProvider === null) fail("v2 external-effect approval requires an injected external decision provider");
  let decision;
  try { decision = await c.externalDecisionProvider.verify(approval.decision, { proposalId: id, proposalSha256: record.proposalSha256 }); }
  catch { fail("v2 external-effect approval is not a verified external decision"); }
  if (!decision || decision.approved !== true) fail("v2 external-effect approval was not granted; proposal remains pending");
  const approved = Object.freeze({ ...record, state: "approved", zeroExecution: true, externalEffects: false, approvedAt: timestamp(c, "external-effect"), approval: { mode: "external-verified", proposalSha256: record.proposalSha256 } });
  await terminalWinningExternal(store, id, approved);
  return Object.freeze({ operationId: `ext-${id}`, lane: "ambiguous-external-effect", status: "approved", zeroExecution: true, proposalSha256: record.proposalSha256, proposalId: id, receiptSha256: sha(approved) });
}

export async function v2ExternalEffectReconcile(config, reconciliation) {
  const c = checkedConfig(config); assertEnabled(c, "ambiguousExternalEffect");
  const id = proposalId(reconciliation.proposalId);
  const { store, record } = await loadPendingExternal(c, id);
  if (reconciliation.uncertainty !== true) fail("v2 external-effect reconciliation requires explicit uncertainty evidence");
  const rejected = Object.freeze({ ...record, state: "rejected", zeroExecution: true, externalEffects: false, reconciledAt: timestamp(c, "external-effect"), reason: "uncertain" });
  await terminalWinningExternal(store, id, rejected);
  return Object.freeze({ operationId: `ext-${id}`, lane: "ambiguous-external-effect", status: "rejected", failClosed: true, zeroExecution: true, proposalId: id, receiptSha256: sha(rejected) });
}

export async function v2ExternalEffectExecute(config, proposal) {
  const c = checkedConfig(config);
  fail("v2 runtime never executes ambiguous external effects; they remain pending or closed with zero execution");
}

// ---------------------------------------------------------------------------
// Recovery — never blind-replays an uncertain prior attempt.
// ---------------------------------------------------------------------------
export async function v2RecoverRuntime(config, requestedId) {
  const c = checkedConfig(config);
  const id = operationId(requestedId);
  const store = await storeRoot(c);
  const custodyPath = join(store.custody, `${id}.json`);
  const receiptPath = join(store.receipts, `${id}.json`);
  if (await exists(receiptPath)) {
    const observed = await readBoundedRegularText(receiptPath, 64 * 1024, "v2 receipt");
    let receipt;
    try { receipt = JSON.parse(observed.text); } catch { fail("v2 receipt is not valid JSON"); }
    return Object.freeze({ operationId: id, status: "completed", receiptSha256: sha(receipt), uncertain: false });
  }
  if (!(await exists(custodyPath))) return Object.freeze({ operationId: id, status: "absent", uncertain: false });
  // Custody exists without a receipt => the effect may or may not have run.
  // Fail closed: move custody to one durable terminal state and never replay.
  const terminalPath = join(store.custody, `${id}.uncertain-rejected.json`);
  if (!(await exists(terminalPath))) {
    await rename(custodyPath, terminalPath);
    await fsyncDirectory(store.custody);
  }
  return Object.freeze({ operationId: id, status: "uncertain-rejected", uncertain: true, failClosed: true });
}

// ---------------------------------------------------------------------------
// Status + version dispatch.
// ---------------------------------------------------------------------------
export async function v2RuntimeStatus(config) {
  const c = checkedConfig(config);
  const store = await statusStore(c);
  const receipts = (await readdir(store.receipts).catch(() => [])).filter((name) => name.endsWith(".json"));
  const custody = (await readdir(store.custody).catch(() => [])).filter((name) => /^workcapv2-[0-9]{13}-[a-f0-9]{16}\.json$/u.test(name));
  const settled = (await readdir(store.custody).catch(() => [])).filter((name) => name.endsWith(".settled.json"));
  const uncertain = (await readdir(store.custody).catch(() => [])).filter((name) => name.endsWith(".uncertain-rejected.json"));
  return Object.freeze({
    schemaVersion: 2,
    operation: "pixel-work-capability-runtime-v2-status",
    runtimeEnabled: c.runtimeEnabled,
    lanes: { ...c.lanes },
    receipts: receipts.length,
    activeCustody: custody.length,
    settledCustody: settled.length,
    uncertainCustody: uncertain.length,
    authority: { ...AUTHORITY },
    boundary: BOUNDARY,
  });
}

export function validateV2RuntimeConfig(config) {
  checkedConfig(config);
  return Object.freeze({ schemaVersion: 2, runtimeEnabled: config.runtimeEnabled === true, lanes: { ...(config.lanes ?? {}) } });
}

// Version dispatch: an injected runtime seam may call this to select the v2
// runtime for a version-bound request when enabled. v1 remains the default and
// is unchanged. When v2 is not enabled, or the lane is unknown, this fails
// closed before any effect. Nothing in this module, by itself, is a production
// entry point; the durable queue/profile service must route into it.
export async function dispatchV2Runtime(config, request) {
  const c = checkedConfig(config);
  if (!c.runtimeEnabled) fail("v2 runtime is disabled; refusing to dispatch");
  switch (request?.lane) {
    case "publicRetrieval": return v2ExecutePublicRetrieval(config, request);
    case "filesystem": return v2ExecuteFilesystem(config, request);
    case "loopback": return v2ExecuteLoopback(config, request);
    default: fail("v2 runtime has no such lane");
  }
}

export const capabilityRuntimeV2Boundary = BOUNDARY;
export const capabilityRuntimeV2Authority = AUTHORITY;
