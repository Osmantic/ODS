import { createHash } from "node:crypto";
import { lstat, mkdir, realpath } from "node:fs/promises";
import { basename, join, resolve, sep } from "node:path";

import { canonical, validateWorkCapabilityOperationalGrantV2, validateWorkCapabilityOperationalRuntimeRequestV2, validateWorkCapabilitySshApprovalV2 } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { writeOwnerPrivateCreateNoClobber, fsyncDirectory } from "./maintenance-secure-files.mjs";
import { CapabilityOperationalV2Error, createFileInWorkspace, fail } from "./capability-runtime-operational-v2-workspace.mjs";
import { executePixelResearchTool } from "../work-runner/research-tool.mjs";
import { executeCapabilitySshHostnameV2 } from "./capability-ssh-runtime-v2.mjs";

// ---------------------------------------------------------------------------
// Pixel v2 OPERATIONAL runtime — the single internally-complete vertical slice.
//
// Authority is the controller-compiled operational grant (schemaVersion 2).
// The runtime derives its lane and effective scope ONLY from that grant. The
// untrusted request is never an authority; the controller-compiled runtime
// request must match the grant binding exactly. Trusted host configuration
// resolves the controller-owned workspace root and may only narrow grant scope,
// never widen it.
//
// Three signed lanes are operational: one bounded create-only file in the exact
// job workspace, one bounded public-retrieval call through the trusted Web
// Courier queue, and one owner-signed SSH connection whose remote command is
// fixed server-side to hostname. Generic shell, loopback, and external-effect
// targets remain non-operational and are rejected before custody here.
//
// Runtime authority checks (issue/expiry via trusted clock, exact content
// binding) run BEFORE any custody is claimed. Custody is single-winner: one
// atomic create-only claim, one atomic create-only terminal (settled /
// uncertain-rejected); active custody is never recreated beside a terminal.
// Receipt publication and custody settlement are crash-recoverable without
// blind replay.
// ---------------------------------------------------------------------------

export { CapabilityOperationalV2Error };
function schema(label, errors) { if (errors.length) fail(`${label} is invalid: ${errors[0]}`); }

function sha(value) {
  return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex");
}
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function validTimestamp(value) {
  return typeof value === "string" && value.length > 0 && Number.isFinite(Date.parse(value));
}
function checkedTime(config, label) {
  const value = (config.clock ?? (() => new Date()))();
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`operational v2 ${label} time is invalid`);
  return value;
}
function timestamp(config, label) { return checkedTime(config, label).toISOString(); }
async function exists(path) { return lstat(path).then(() => true, () => false); }

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return resolve(path);
}

async function storeRoot(config) {
  const root = await privateDirectory(config.stateRoot, "operational v2 state root", true);
  const runtime = await privateDirectory(join(root, "v2-runtime"), "operational v2 runtime root", true);
  const custody = await privateDirectory(join(runtime, "custody"), "operational v2 custody root", true);
  const receipts = await privateDirectory(join(runtime, "receipts"), "operational v2 receipt root", true);
  return { root, runtime, custody, receipts };
}

const OPERATIONAL_AUTHORITY = Object.freeze({
  grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false,
  grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false,
});
const OPERATIONAL_BOUNDARY = "Honest operational v2 runtime result for one exact executed job-workspace file effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion.";
const PUBLIC_RETRIEVAL_BOUNDARY = "Honest operational v2 runtime result for one exact executed public-retrieval effect through the Web Courier queue. Returned evidence is untrusted data and the result grants no replay, future execution, credential, direct network, external effect, scope expansion, or completion.";
const SSH_HOSTNAME_BOUNDARY = "Honest operational v2 runtime result for one exact owner-signed SSH forced-command hostname operation. The returned hostname is untrusted data and the result grants no replay, future execution, generic shell, command or destination selection, credential disclosure, external effect, scope expansion, or completion.";
const RECEIPT_BOUNDARY = "Content-free operational v2 receipt recorded only after a completed workspace effect. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion.";
const CUSTODY_BOUNDARY = "Private content-free single-winner custody for one exact operational v2 workspace effect. It proves one claimant acquired the operation before any effect and grants no replay, future execution, credential, network, external effect, scope expansion, or completion.";
const RECEIPT_OPERATION = "pixel-work-capability-runtime-v2-receipt";
const CUSTODY_OPERATION = "pixel-work-capability-runtime-v2-custody";

// Immutable operational v2 custody/receipt contract constants and helpers shared
// with the closed controller queue so the two never drift apart. Both sides must
// consume the exact same operation strings, boundaries, binding digest, and
// persisted record shapes. Both the filesystem and public-retrieval lanes use
// the same custody/receipt operations and boundaries.
export const capabilityOperationalV2ReceiptOperation = RECEIPT_OPERATION;
export const capabilityOperationalV2CustodyOperation = CUSTODY_OPERATION;
export const capabilityOperationalV2ReceiptBoundary = RECEIPT_BOUNDARY;
export const capabilityOperationalV2CustodyBoundary = CUSTODY_BOUNDARY;
export const capabilityOperationalV2Boundary = OPERATIONAL_BOUNDARY;
export const capabilityOperationalV2ReceiptKeys = Object.freeze(["schemaVersion", "operation", "operationId", "lane", "status", "startedAt", "completedAt", "failureClass", "outputSha256", "outputBytes", "externalEffects", "authority", "boundary"]);
export const capabilityOperationalV2SettledCustodyKeys = Object.freeze(["schemaVersion", "operation", "operationId", "lane", "state", "grantId", "bindingSha256", "claimedAt", "receiptSha256", "settledAt", "boundary"]);

// The exact durable binding the operational runtime creates custody with.
// The controller queue reuses this same digest to prove planted custody is
// bound to the exact prior grant. Discriminated by lane: filesystem binds
// content/action/path; public-retrieval binds the retrieval input digest.
export function operationalBindingSha256(grant) {
  if (grant.operation.lane === "filesystem") {
    return sha({ grantId: grant.grantId, operationId: grant.operation.id, action: grant.operation.action, relativePath: grant.operation.relativePath, contentSha256: grant.contentSha256, maxFileBytes: grant.budgets.perRun.maxBytes });
  }
  if (grant.operation.lane === "public-retrieval") {
    const ri = grant.retrievalInput;
    return sha({ grantId: grant.grantId, operationId: grant.operation.id, querySha256: ri.querySha256, sourceTypes: ri.sourceTypes, domains: ri.domains, maxResults: ri.maxResults, maxSourcesToFetch: ri.maxSourcesToFetch, maxSourceBytes: ri.maxSourceBytes });
  }
  if (grant.operation.lane === "ssh-hostname") {
    return sha({
      grantId: grant.grantId, operationId: grant.operation.id,
      requestId: grant.operation.requestId, destinationAlias: grant.operation.destinationAlias,
      commandId: grant.operation.commandId, approvalSha256: grant.approvalSha256,
      credentialRef: grant.credentialRefs[0], maxOutputBytes: grant.budgets.perRun.maxBytes,
      maxRuntimeMs: grant.budgets.perRun.maxDurationMs,
    });
  }
  fail("operational v2 binding SHA requires a known lane");
}

// The single shared recovery/queue validation of a durable settled custody and
// receipt. The queue and the public recovery diagnostic must consume the EXACT
// same shape/schema/operation/boundary/binding/content checks so the two can
// never drift apart. Both validators throw (fail closed) on any drift, extra or
// missing key, wrong schemaVersion/operation/boundary, wrong lane/state/status,
// mismatched grant/binding/content digest/bytes, invalid timestamps, a claimed
// external effect, or any forbidden authority flag.
// Supports both filesystem and public-retrieval lanes.
export function validateOperationalSettledCustody(record, { grant, operationId, bindingSha256 }) {
  exactKeys(record, capabilityOperationalV2SettledCustodyKeys, "operational v2 settled custody");
  if (record.schemaVersion !== 1 || record.operation !== CUSTODY_OPERATION || record.boundary !== CUSTODY_BOUNDARY) fail("operational v2 settled custody does not authenticate to the operational contract");
  if (record.operationId !== operationId || record.state !== "settled") fail("operational v2 settled custody does not authenticate to the operation");
  if (record.lane !== grant.operation.lane) fail("operational v2 settled custody lane differs from the expected grant");
  if (typeof record.grantId !== "string" || record.grantId !== grant.grantId) fail("operational v2 settled custody is not bound to the expected grant");
  if (typeof record.bindingSha256 !== "string" || record.bindingSha256 !== bindingSha256) fail("operational v2 settled custody binding differs from the expected grant");
  if (!/^[a-f0-9]{64}$/u.test(record.receiptSha256 ?? "")) fail("operational v2 settled custody lacks an authentic receipt hash");
  if (!validTimestamp(record.claimedAt) || !validTimestamp(record.settledAt)) fail("operational v2 settled custody timestamps are invalid");
  return record;
}

export function validateOperationalReceipt(receipt, { grant, operationId }) {
  exactKeys(receipt, capabilityOperationalV2ReceiptKeys, "operational v2 receipt");
  if (receipt.schemaVersion !== 1 || receipt.operation !== RECEIPT_OPERATION || receipt.boundary !== RECEIPT_BOUNDARY) fail("operational v2 receipt does not authenticate to the operational contract");
  if (receipt.operationId !== operationId || receipt.lane !== grant.operation.lane || receipt.status !== "succeeded" || receipt.failureClass !== null) fail("operational v2 receipt does not authenticate to the operation");
  if (typeof receipt.outputSha256 !== "string" || !/^[a-f0-9]{64}$/u.test(receipt.outputSha256)) fail("operational v2 receipt output hash is invalid");
  if (grant.operation.lane === "filesystem" && receipt.outputSha256 !== grant.contentSha256) fail("operational v2 receipt content does not authenticate to the expected grant");
  if (!Number.isSafeInteger(receipt.outputBytes) || receipt.outputBytes < 0 || receipt.outputBytes > grant.budgets.perRun.maxBytes) fail("operational v2 receipt output bytes are invalid");
  if (receipt.externalEffects !== false) fail("operational v2 receipt claims an external effect");
  const auth = receipt.authority ?? {};
  if (auth.grantsCompletion !== false || auth.grantsReplay !== false || auth.grantsFutureExecution !== false || auth.grantsCredentials !== false || auth.grantsNetwork !== false || auth.grantsExternalEffects !== false || auth.grantsScopeExpansion !== false) fail("operational v2 receipt grants forbidden authority");
  if (!validTimestamp(receipt.startedAt) || !validTimestamp(receipt.completedAt)) fail("operational v2 receipt timestamps are invalid");
  return receipt;
}

async function resolveWorkspace(config) {
  const value = config.workspaceRoot;
  if (typeof value !== "string" || value.length === 0 || !value.startsWith("/")) fail("operational v2 requires a trusted controller-owned absolute workspaceRoot");
  // Reject a replaced/symlinked root at the trusted path itself BEFORE resolving
  // it: a realpath-first check would silently follow a symlink and redirect the
  // effect outside the controller-owned root.
  const entry = await lstat(value).catch(() => fail("operational v2 workspace root is not a real directory"));
  if (!entry.isDirectory() || entry.isSymbolicLink()) fail("operational v2 workspace root is not a real directory");
  const actual = await realpath(value).catch(() => fail("operational v2 workspace root is not a real directory"));
  const info = await lstat(actual);
  if (!info.isDirectory() || info.isSymbolicLink()) fail("operational v2 workspace root is not a real directory");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("operational v2 workspace root is not owner-only");
  // Return the trusted root's real path together with its pre-open identity. The
  // descriptor-bound write binds an opened directory descriptor to this exact
  // (dev,ino) so a same-UID swap of the root between check and effect is rejected
  // rather than redirecting the write.
  return { real: actual, identity: { dev: info.dev, ino: info.ino } };
}

function containedPath(workspaceReal, relativePath) {
  if (typeof relativePath !== "string" || relativePath.length === 0 || relativePath.includes("\0") || relativePath.includes("\\")) fail("operational v2 workspace relative path is invalid");
  const segments = relativePath.split("/");
  if (segments.some((segment) => segment === "" || segment === "." || segment === "..")) fail("operational v2 workspace relative path escapes the workspace");
  const target = resolve(workspaceReal, relativePath);
  if (target !== workspaceReal && !target.startsWith(workspaceReal + sep)) fail("operational v2 workspace path escapes the workspace");
  return { target, segments };
}

async function claimCustody(config, operationId, grantId, lane, bindingSha256) {
  const store = await storeRoot(config);
  const path = join(store.custody, `${operationId}.json`);
  const settled = join(store.custody, `${operationId}.settled.json`);
  const uncertain = join(store.custody, `${operationId}.uncertain-rejected.json`);
  if (await exists(path) || await exists(settled) || await exists(uncertain)) fail("operational v2 operation already has custody or a terminal; no blind replay");
  const record = {
    schemaVersion: 1, operation: CUSTODY_OPERATION, operationId,
    lane, state: "in-progress", grantId,
    bindingSha256, claimedAt: timestamp(config, "claim"),
    boundary: CUSTODY_BOUNDARY,
  };
  try {
    await writeOwnerPrivateCreateNoClobber(path, `${JSON.stringify(record, null, 2)}\n`, process.geteuid());
  } catch (error) {
    if (error?.message?.includes("already exists")) fail("operational v2 operation already has custody; no blind replay");
    throw error;
  }
  // Close the settle/claim race: if a terminal appeared while we claimed, we must
  // never leave active custody beside it. Remove our claim and fail closed. The
  // effect write is create-only, so even under this race no second effect can be
  // durably created by us.
  if (await exists(settled) || await exists(uncertain)) {
    await import("node:fs/promises").then(({ unlink }) => unlink(path)).catch(() => {});
    fail("operational v2 operation reached a terminal while claiming; no blind replay");
  }
  return { store, path, record };
}

async function readJson(path, maximum, label) {
  let observed;
  try { observed = await readBoundedRegularText(path, maximum, label); } catch (error) { fail(`${label} is unreadable`, error); }
  try { return JSON.parse(observed.text); } catch { fail(`${label} is not JSON`); }
}

async function writeReceipt(config, store, receipt) {
  const path = join(store.receipts, `${receipt.operationId}.json`);
  if (await exists(path)) fail("operational v2 operation already has a receipt");
  await writeOwnerPrivateCreateNoClobber(path, `${JSON.stringify(receipt, null, 2)}\n`, process.geteuid());
  await fsyncDirectory(store.receipts);
  return sha(receipt);
}

// Atomic single terminal winner: the settled marker is created with create-only
// no-clobber (exactly one process can publish it), then active custody is
// removed. Recovery detects the settled terminal before any claim so active
// custody is never recreated beside a terminal.
async function settleCustody(config, store, operationId, record, receiptSha256) {
  const active = join(store.custody, `${operationId}.json`);
  const terminal = join(store.custody, `${operationId}.settled.json`);
  const terminalRecord = { ...record, state: "settled", receiptSha256, settledAt: timestamp(config, "settle"), boundary: CUSTODY_BOUNDARY };
  try {
    await writeOwnerPrivateCreateNoClobber(terminal, `${JSON.stringify(terminalRecord, null, 2)}\n`, process.geteuid());
  } catch (error) {
    if (error?.message?.includes("already exists")) fail("operational v2 operation already settled; exactly one terminal winner");
    throw error;
  }
  await fsyncDirectory(store.custody);
  await import("node:fs/promises").then(({ unlink }) => unlink(active)).catch(() => {});
  await fsyncDirectory(store.custody);
}

async function markUncertainRejected(config, store, operationId, record) {
  const active = join(store.custody, `${operationId}.json`);
  const terminal = join(store.custody, `${operationId}.uncertain-rejected.json`);
  const terminalRecord = { ...record, state: "uncertain-rejected", reason: "crash-before-receipt", rejectedAt: timestamp(config, "recover"), boundary: CUSTODY_BOUNDARY };
  try {
    await writeOwnerPrivateCreateNoClobber(terminal, `${JSON.stringify(terminalRecord, null, 2)}\n`, process.geteuid());
  } catch (error) {
    if (error?.message?.includes("already exists")) fail("operational v2 operation already has an uncertain terminal; no blind replay");
    throw error;
  }
  await fsyncDirectory(store.custody);
  await import("node:fs/promises").then(({ unlink }) => unlink(active)).catch(() => {});
  await fsyncDirectory(store.custody);
}

function checkConfig(config) {
  if (!config || typeof config !== "object" || Array.isArray(config)) fail("operational v2 runtime requires a trusted config seam");
  if (typeof config.stateRoot !== "string") fail("operational v2 runtime requires a trusted stateRoot");
  // courierQueueRoot is only validated in the public-retrieval lane.
  return config;
}

function assertRuntimeValid(grant, config) {
  const now = checkedTime(config, "runtime authority");
  const issued = Date.parse(grant.issuedAt), expires = Date.parse(grant.expiresAt);
  if (!Number.isFinite(issued) || !Number.isFinite(expires) || issued > expires) fail("operational v2 grant issue/expiry is invalid");
  if (now.getTime() < issued) fail("operational v2 grant is not yet valid");
  if (now.getTime() >= expires) fail("operational v2 grant has expired");
}

// Execute the filesystem lane: descriptor-bound create-only write.
async function executeFilesystemLane(grant, request, config, startedAt, bindingSha256) {
  if (grant.tool.targetClass !== "local-filesystem" || grant.operation.action !== "write" || grant.operation.scope !== "job-workspace") fail("operational v2 target is not the authorized local-filesystem workspace effect");
  if (grant.egress !== "none") fail("operational v2 filesystem grant must not grant egress");
  // Bind the runtime request exactly to the grant: the grant's declared content
  // hash, the request binding's content hash, and the actual request content must
  // all agree (grant.contentSha256 === binding.contentSha256 === sha(content)).
  if (request.binding.action !== grant.operation.action || request.binding.scope !== grant.operation.scope || request.binding.relativePath !== grant.operation.relativePath) fail("operational v2 runtime request binding differs from its grant");
  if (request.binding.contentSha256 !== grant.contentSha256) fail("operational v2 runtime request binding content hash differs from its grant");
  if (sha(request.content) !== grant.contentSha256) fail("operational v2 runtime request content differs from its grant binding");
  if (request.binding.maxFileBytes > grant.budgets.perRun.maxBytes) fail("operational v2 runtime request exceeds its grant budget");
  const workspace = await resolveWorkspace(config);
  const { segments } = containedPath(workspace.real, request.binding.relativePath);
  const content = request.content;
  const maxBytes = grant.budgets.perRun.maxBytes;
  if (typeof content !== "string" || Buffer.byteLength(content, "utf8") > maxBytes) fail("operational v2 workspace write content is invalid or exceeds its ceiling");
  if (segments.length !== 1) {
    fail("operational v2 supports only a single create-only filename; nested relative paths are rejected before custody");
  }
  const targetName = basename(segments[0]);
  const { store, record } = await claimCustody(config, grant.operation.id, grant.grantId, "filesystem", bindingSha256);
  await createFileInWorkspace(workspace.real, workspace.identity, targetName, content, maxBytes);
  const completedAt = timestamp(config, "workspace");
  const digest = sha(content);
  const receipt = Object.freeze({
    schemaVersion: 1, operation: RECEIPT_OPERATION, operationId: grant.operation.id,
    lane: "filesystem", status: "succeeded", startedAt, completedAt, failureClass: null,
    outputSha256: digest, outputBytes: Buffer.byteLength(content, "utf8"), externalEffects: false,
    authority: { ...OPERATIONAL_AUTHORITY }, boundary: RECEIPT_BOUNDARY,
  });
  const receiptSha256 = await writeReceipt(config, store, receipt);
  await settleCustody(config, store, grant.operation.id, record, receiptSha256);
  const structuredContent = {
    operationId: grant.operation.id, lane: "filesystem", status: "succeeded", path: request.binding.relativePath,
    outputSha256: digest, outputBytes: receipt.outputBytes, receiptSha256,
  };
  return Object.freeze({
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
    schemaVersion: 2, grantId: grant.grantId, operationId: grant.operation.id, lane: "filesystem",
    status: "succeeded", enabled: true, effect: "workspace-write", receiptSha256,
    outputSha256: digest, outputBytes: receipt.outputBytes, structuredContent,
    authority: { ...OPERATIONAL_AUTHORITY }, boundary: OPERATIONAL_BOUNDARY,
  });
}

// Execute the public-retrieval lane: controller-authorized retrieval through
// the Web Courier / research-broker queue. The untrusted request must never
// carry queue paths; those come from trusted controller config only.
// The runtime returns bounded rendered public evidence in an exact trust
// wrapper: {trust:"untrusted", authority:"none", text:...}. The wrapper SHA
// and byte count are bound in the receipt and result schema.
async function executePublicRetrievalLane(grant, request, config, startedAt, bindingSha256) {
  if (grant.tool.effectClass !== "brokered-network" || grant.tool.targetClass !== "public-api") fail("operational v2 public-retrieval grant must use brokered-network effect targeting public-api");
  if (grant.dataClassification !== "public") fail("operational v2 public-retrieval is public-only classification");
  if (grant.egress !== "public") fail("operational v2 public-retrieval grant must declare public egress");
  if (grant.retrievalInput === undefined) fail("operational v2 public-retrieval grant must carry retrievalInput");
  const ri = grant.retrievalInput;
  // Validate the runtime request byte-for-byte against the grant.
  if (request.trustRoot !== "courier") fail("operational v2 public-retrieval runtime request must trust courier");
  if (!request.retrievalBinding) fail("operational v2 public-retrieval runtime request must carry retrievalBinding");
  const rb = request.retrievalBinding;
  if (rb.querySha256 !== ri.querySha256) fail("operational v2 public-retrieval request query hash differs from its grant");
  if (canonical(rb.sourceTypes) !== canonical(ri.sourceTypes)) fail("operational v2 public-retrieval request sourceTypes differ from its grant");
  if (canonical(rb.domains) !== canonical(ri.domains)) fail("operational v2 public-retrieval request domains differ from its grant");
  if (rb.maxResults !== ri.maxResults || rb.maxSourcesToFetch !== ri.maxSourcesToFetch || rb.maxSourceBytes !== ri.maxSourceBytes) fail("operational v2 public-retrieval request budgets differ from its grant");
  // Validate that the query content matches the grant binding.
  if (sha(request.query) !== ri.querySha256) fail("operational v2 public-retrieval query content differs from its grant binding");
  // Validate domains are within signed binding scope.
  for (const domain of rb.domains) {
    if (!declaredDomainsOk(domain, grant)) fail("operational v2 public-retrieval domain is not in the signed binding");
  }
  // Bound the Courier timeout to the exact grant runtime and per-run duration
  // ceilings, not merely the global 180-second config limit.
  const courierQueueRoot = config.courierQueueRoot;
  const timeoutMilliseconds = config.timeoutMilliseconds ?? 120000;
  const pollMilliseconds = config.pollMilliseconds ?? 50;
  if (typeof courierQueueRoot !== "string" || !courierQueueRoot.startsWith("/")) fail("operational v2 public-retrieval requires a trusted courierQueueRoot");
  if (!Number.isSafeInteger(timeoutMilliseconds) || timeoutMilliseconds < 1 || timeoutMilliseconds > 180000) fail("operational v2 public-retrieval timeout is invalid");
  if (!Number.isSafeInteger(pollMilliseconds) || pollMilliseconds < 1 || pollMilliseconds > 1000) fail("operational v2 public-retrieval poll interval is invalid");
  // Reject before custody when the trusted config timeout exceeds the grant.
  const grantPerRunDuration = grant.budgets.perRun.maxDurationMs;
  const grantLimitRuntime = grant.limits.maxRuntimeMs;
  const effectiveCeiling = Math.min(grantPerRunDuration, grantLimitRuntime);
  if (timeoutMilliseconds > effectiveCeiling) fail("operational v2 public-retrieval timeout exceeds grant runtime ceiling");
  // Claim durable custody before queueing.
  const { store, record } = await claimCustody(config, grant.operation.id, grant.grantId, "public-retrieval", bindingSha256);
  // Call executePixelResearchTool with ONLY trusted config values.
  const toolParams = {
    query: request.query,
    sourceTypes: rb.sourceTypes,
    domains: rb.domains,
    maxResults: rb.maxResults,
    maxSourcesToFetch: rb.maxSourcesToFetch,
    maxSourceBytes: rb.maxSourceBytes,
  };
  let toolResult;
  try {
    toolResult = await executePixelResearchTool(toolParams, null, { queueRoot: courierQueueRoot, timeoutMilliseconds, pollMilliseconds });
  } catch (error) {
    await markUncertainRejected(config, store, grant.operation.id, record);
    fail("operational v2 public-retrieval courier call failed; custody uncertain-rejected");
  }
  if (!toolResult || toolResult.isError) {
    await markUncertainRejected(config, store, grant.operation.id, record);
    fail("operational v2 public-retrieval returned an error; custody uncertain-rejected");
  }
  // Validate the tool result after custody. Any failure here must durably
  // become uncertain-rejected before returning.
  try {
    validatePostCustodyToolResult(toolResult, grant.budgets.perRun.maxBytes);
  } catch (validationError) {
    await markUncertainRejected(config, store, grant.operation.id, record);
    fail("operational v2 public-retrieval tool output invalid; custody uncertain-rejected: " + validationError.message);
  }
  const contentBlock = toolResult.content[0];
  // Compute the exact canonical wrapper bytes FIRST and reject when the
  // wrapper exceeds the grant's output/per-run ceilings — not merely the raw
  // content bytes. Near the ceiling the wrapper overhead would cause the
  // returned/receipted output to exceed the grant.
  const evidenceWrapper = { trust: "untrusted", authority: "none", text: contentBlock.text };
  const wrapperText = JSON.stringify(evidenceWrapper);
  const wrapperBytes = Buffer.byteLength(wrapperText, "utf8");
  if (wrapperBytes > grant.budgets.perRun.maxBytes) {
    await markUncertainRejected(config, store, grant.operation.id, record);
    fail("operational v2 public-retrieval evidence wrapper exceeds grant budget");
  }
  const outputSha256 = sha(wrapperText);
  // The receipt SHA covers the evidence wrapper, not raw content.
  const completedAt = timestamp(config, "retrieval");
  const receipt = Object.freeze({
    schemaVersion: 1, operation: RECEIPT_OPERATION, operationId: grant.operation.id,
    lane: "public-retrieval", status: "succeeded", startedAt, completedAt, failureClass: null,
    outputSha256, outputBytes: wrapperBytes, externalEffects: false,
    authority: { ...OPERATIONAL_AUTHORITY }, boundary: RECEIPT_BOUNDARY,
  });
  const receiptSha256 = await writeReceipt(config, store, receipt);
  await settleCustody(config, store, grant.operation.id, record, receiptSha256);
  const structuredContent = {
    operationId: grant.operation.id, lane: "public-retrieval", status: "succeeded",
    outputSha256, outputBytes: wrapperBytes, receiptSha256,
    evidenceWrapper,
  };
  return Object.freeze({
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
    schemaVersion: 2, grantId: grant.grantId, operationId: grant.operation.id, lane: "public-retrieval",
    status: "succeeded", enabled: true, effect: "public-retrieval", receiptSha256,
    outputSha256, outputBytes: wrapperBytes, structuredContent,
    authority: { ...OPERATIONAL_AUTHORITY }, boundary: PUBLIC_RETRIEVAL_BOUNDARY,
  });
}

// Execute one owner-signed SSH connection whose target, credential reference,
// expected hostname, host key, key file, and server-side forced command all
// come from protected runtime configuration. The request carries no literal
// host, user, key path, or command text. Custody is claimed before signature
// consumption or network activity; every post-custody failure is durably
// uncertain-rejected so Pixel never blindly retries an ambiguous connection.
async function executeSshHostnameLane(grant, request, config, startedAt, bindingSha256) {
  if (grant.tool.effectClass !== "read-only" || grant.tool.targetClass !== "ssh") fail("operational v2 SSH grant must be a read-only SSH effect");
  if (grant.operation.commandId !== "hostname" || grant.egress !== "private-allowlist") fail("operational v2 SSH grant is not the forced hostname private-allowlist lane");
  if (grant.approvalMode !== "owner-signed" || grant.credentialRefs.length !== 1) fail("operational v2 SSH grant lacks its exact owner approval and credential reference");
  schema("operational v2 SSH approval", validateWorkCapabilitySshApprovalV2(request.approval));
  if (request.trustRoot !== "owner-signed-ssh") fail("operational v2 SSH runtime request must trust the owner-signed SSH root");
  if (sha(request.approval) !== grant.approvalSha256) fail("operational v2 SSH approval differs from its grant binding");
  const approval = request.approval;
  if (approval.requestId !== grant.operation.requestId || approval.destinationAlias !== grant.operation.destinationAlias || approval.commandId !== grant.operation.commandId || approval.credentialRef !== grant.credentialRefs[0]) fail("operational v2 SSH approval differs from the exact granted operation");
  if (grant.budgets.perRun.maxBytes > approval.maxOutputBytes || grant.limits.maxOutputBytes > approval.maxOutputBytes) fail("operational v2 SSH output authority exceeds its owner approval");
  if (grant.budgets.perRun.maxDurationMs > approval.timeoutMs || grant.limits.maxRuntimeMs > approval.timeoutMs) fail("operational v2 SSH runtime authority exceeds its owner approval");
  if (!config.ssh || typeof config.ssh !== "object" || Array.isArray(config.ssh)) fail("operational v2 SSH lane requires protected trusted SSH config");

  const { store, record } = await claimCustody(config, grant.operation.id, grant.grantId, "ssh-hostname", bindingSha256);
  let result;
  try {
    result = await executeCapabilitySshHostnameV2({
      config: { ...config.ssh, stateRoot: config.stateRoot, clock: config.clock },
      approval, signature: request.approvalSignature,
    });
    if (!result || result.status !== "succeeded" || result.destinationAlias !== grant.operation.destinationAlias || result.commandId !== "hostname") fail("operational v2 SSH runtime returned an unbound result");
    if (result.approvalSha256 !== grant.approvalSha256 || result.outputBytes > grant.budgets.perRun.maxBytes) fail("operational v2 SSH runtime exceeded or differed from its grant binding");
  } catch (error) {
    await markUncertainRejected(config, store, grant.operation.id, record);
    fail(`operational v2 SSH execution failed after custody; custody uncertain-rejected: ${error?.message ?? "unknown failure"}`);
  }

  const completedAt = timestamp(config, "SSH hostname");
  const receipt = Object.freeze({
    schemaVersion: 1, operation: RECEIPT_OPERATION, operationId: grant.operation.id,
    lane: "ssh-hostname", status: "succeeded", startedAt, completedAt, failureClass: null,
    outputSha256: result.outputSha256, outputBytes: result.outputBytes, externalEffects: false,
    authority: { ...OPERATIONAL_AUTHORITY }, boundary: RECEIPT_BOUNDARY,
  });
  const receiptSha256 = await writeReceipt(config, store, receipt);
  await settleCustody(config, store, grant.operation.id, record, receiptSha256);
  const structuredContent = {
    operationId: grant.operation.id, lane: "ssh-hostname", status: "succeeded",
    destinationAlias: result.destinationAlias, commandId: result.commandId, hostname: result.hostname,
    outputSha256: result.outputSha256, outputBytes: result.outputBytes, receiptSha256,
    approvalSha256: result.approvalSha256, signatureSha256: result.signatureSha256,
    approvalConsumptionSha256: result.approvalConsumptionSha256,
  };
  return Object.freeze({
    $schema: "https://osmantic.com/pixel/schemas/work-capability-operational-runtime-result-v2.schema.json",
    schemaVersion: 2, grantId: grant.grantId, operationId: grant.operation.id, lane: "ssh-hostname",
    status: "succeeded", enabled: true, effect: "ssh-forced-hostname", receiptSha256,
    outputSha256: result.outputSha256, outputBytes: result.outputBytes, structuredContent,
    authority: { ...OPERATIONAL_AUTHORITY }, boundary: SSH_HOSTNAME_BOUNDARY,
  });
}

// Verify that a domain is within the signed binding scope (domains must be
// in the grant's retrievalInput.domains list, which was validated by the
// controller against the signed pack binding).
function declaredDomainsOk(domain, grant) {
  if (grant.retrievalInput?.domains?.length === 0) return true;
  return grant.retrievalInput.domains.includes(domain);
}

// Post-custody tool result validator: extracts the exact content-block checks
// from the production runtime so they can be tested directly without requiring
// an executor injection surface. This rejects missing, extra, non-text, empty,
// or structurally-invalid blocks.
export function validatePostCustodyToolResult(toolResult, maxTextBytes = Number.MAX_SAFE_INTEGER) {
  if (!toolResult || typeof toolResult !== "object" || Array.isArray(toolResult)) fail("operational v2 public-retrieval tool result is missing");
  if (!Array.isArray(toolResult.content)) fail("operational v2 public-retrieval tool result has no content array");
  exactKeys(toolResult, ["content"], "operational v2 public-retrieval tool result");
  if (toolResult.content.length !== 1) fail("operational v2 public-retrieval must return exactly one content block");
  const block = toolResult.content[0];
  if (!block || typeof block !== "object" || Array.isArray(block)) fail("operational v2 public-retrieval content block is invalid");
  if (block.type !== "text") fail("operational v2 public-retrieval content block must be text");
  if (typeof block.text !== "string" || block.text.length === 0) fail("operational v2 public-retrieval content block must have non-empty text");
  exactKeys(block, ["type", "text"], "operational v2 public-retrieval content block");
  if (!Number.isSafeInteger(maxTextBytes) || maxTextBytes < 1 || Buffer.byteLength(block.text, "utf8") > maxTextBytes) fail("operational v2 public-retrieval content block exceeds its byte ceiling");
}

export async function executeOperationalV2(runtime) {
  const grant = runtime?.grant;
  const request = runtime?.runtimeRequest;
  const config = checkConfig(runtime?.config);
  schema("operational v2 grant", validateWorkCapabilityOperationalGrantV2(grant));
  schema("operational v2 runtime request", validateWorkCapabilityOperationalRuntimeRequestV2(request));

  // Runtime must reject an expired or not-yet-valid grant before custody using
  // the trusted clock. This is the last authority boundary before any effect.
  assertRuntimeValid(grant, config);

  // Bind the runtime request exactly to the grant.
  if (request.grantId !== grant.grantId || request.operationId !== grant.operation.id || request.lane !== grant.operation.lane) fail("operational v2 runtime request does not match its grant");
  if (grant.operation.lane !== "ssh-hostname" && (grant.approvalMode !== "none" || grant.credentialRefs.length !== 0)) fail("operational v2 grant requests an unsupported approval/credential scope");

  const startedAt = timestamp(config, "runtime");
  const bindingSha256 = operationalBindingSha256(grant);

  // Dispatch based on the grant's lane (never the untrusted request).
  if (grant.operation.lane === "filesystem") {
    return executeFilesystemLane(grant, request, config, startedAt, bindingSha256);
  }
  if (grant.operation.lane === "public-retrieval") {
    return executePublicRetrievalLane(grant, request, config, startedAt, bindingSha256);
  }
  if (grant.operation.lane === "ssh-hostname") {
    return executeSshHostnameLane(grant, request, config, startedAt, bindingSha256);
  }
  fail("operational v2 grant lane is not operational");
}

// Crash-recoverable custody settlement without blind replay. Completion is ONLY
// reported when the durable settled custody is bound to an AUTHENTIC prior grant
// supplied by the caller and validated here (exact schema-valid grant, exact
// operation id, exact operationalBindingSha256, exact grantId, and a receipt that
// authenticates to that grant). Without such an authentic expected grant the
// public diagnostic NEVER claims completion: a settled record is reported only as
// an unverified/uncertain state, and the queue's grant-bound reconciliation
// remains the only completion authority. An active claim with no receipt is moved
// to one durable uncertain-rejected terminal and never replayed.
// Supports both filesystem and public-retrieval lanes.
export async function recoverOperationalV2(config, requestedOperationId, expectedGrant) {
  if (typeof requestedOperationId !== "string" || !/^workcapv2-[0-9]{13}-[a-f0-9]{16}$/u.test(requestedOperationId)) fail("operational v2 operation id is invalid");
  // Completion requires an authentic expected grant/decision. A same-UID forger
  // can fabricate any settled record, nonempty grantId, 64-hex binding, and
  // matching receipt; only a caller-supplied schema-valid grant bound to this
  // exact operation provides the authentic comparison.
  let expectedBinding = null;
  if (expectedGrant !== undefined) {
    if (!expectedGrant || typeof expectedGrant !== "object" || Array.isArray(expectedGrant)) fail("operational v2 recovery requires an authentic expected grant");
    schema("operational v2 recovery grant", validateWorkCapabilityOperationalGrantV2(expectedGrant));
    if (expectedGrant.operation.id !== requestedOperationId) fail("operational v2 recovery grant does not match the operation id");
    expectedBinding = operationalBindingSha256(expectedGrant);
  }
  const c = checkConfig(config);
  const store = await storeRoot(c);
  const active = join(store.custody, `${requestedOperationId}.json`);
  const settled = join(store.custody, `${requestedOperationId}.settled.json`);
  const uncertain = join(store.custody, `${requestedOperationId}.uncertain-rejected.json`);
  const receiptPath = join(store.receipts, `${requestedOperationId}.json`);
  const settledRecord = (await exists(settled)) ? await readJson(settled, 64 * 1024, "operational v2 settled custody") : null;
  if (settledRecord) {
    // Without an authentic expected grant, a settled record is UNVERIFIED: a
    // same-UID forger can fabricate any settled record, nonempty grantId,
    // 64-hex binding, and matching receipt, so completion is never claimed.
    if (expectedBinding === null) {
      return Object.freeze({ operationId: requestedOperationId, status: "uncertain", uncertain: true, unverified: true, noReplay: true });
    }
    // Only a settled custody bound to the EXACT authentic expected grant proves
    // completion. The shared validator enforces the exact keys, schemaVersion,
    // operation constant, boundary, lane/state, grant/binding digest, terminal
    // receipt hash, and timestamps; any tamper or drift fails closed rather than
    // trusting a forged record.
    validateOperationalSettledCustody(settledRecord, { grant: expectedGrant, operationId: requestedOperationId, bindingSha256: expectedBinding });
    // Completion requires a present, securely-read receipt whose canonical SHA
    // exactly matches the settled custody hash. A settled marker with an
    // arbitrary receipt hash and no receipt is never completion proof.
    if (!(await exists(receiptPath))) fail("operational v2 settled custody has no receipt; no completion proof; fail closed");
    const receipt = await readJson(receiptPath, 64 * 1024, "operational v2 receipt");
    validateOperationalReceipt(receipt, { grant: expectedGrant, operationId: requestedOperationId });
    if (sha(receipt) !== settledRecord.receiptSha256) fail("operational v2 receipt tampering detected; fail closed");
    if (await exists(active)) await import("node:fs/promises").then(({ unlink }) => unlink(active)).catch(() => {});
    return Object.freeze({ operationId: requestedOperationId, status: "completed", receiptSha256: settledRecord.receiptSha256, uncertain: false });
  }
  if (await exists(receiptPath)) {
    // A standalone receipt WITHOUT authentic settled custody bound to the exact
    // expected grant is never proof of completion. We never fabricate settled
    // custody from a receipt and never replay; report explicit uncertain/no-replay.
    await readJson(receiptPath, 64 * 1024, "operational v2 receipt");
    return Object.freeze({ operationId: requestedOperationId, status: "uncertain", uncertain: true, noReplay: true });
  }
  if (await exists(uncertain)) return Object.freeze({ operationId: requestedOperationId, status: "uncertain-rejected", uncertain: true, failClosed: true });
  if (await exists(active)) {
    const activeRecord = await readJson(active, 64 * 1024, "operational v2 active custody");
    await markUncertainRejected(c, store, requestedOperationId, activeRecord);
    return Object.freeze({ operationId: requestedOperationId, status: "uncertain-rejected", uncertain: true, failClosed: true });
  }
  return Object.freeze({ operationId: requestedOperationId, status: "absent", uncertain: false });
}

export async function statusOperationalV2(config) {
  const c = checkConfig(config);
  const store = await storeRoot(c);
  const custody = await import("node:fs/promises").then(({ readdir }) => readdir(store.custody)).catch(() => []);
  const receipts = await import("node:fs/promises").then(({ readdir }) => readdir(store.receipts)).catch(() => []);
  return Object.freeze({
    schemaVersion: 2, operation: "pixel-work-capability-runtime-v2-operational-status",
    activeCustody: custody.filter((name) => /^workcapv2-[0-9]{13}-[a-f0-9]{16}\.json$/u.test(name)).length,
    settledCustody: custody.filter((name) => name.endsWith(".settled.json")).length,
    uncertainCustody: custody.filter((name) => name.endsWith(".uncertain-rejected.json")).length,
    receipts: receipts.filter((name) => name.endsWith(".json")).length,
    authority: { ...OPERATIONAL_AUTHORITY }, boundary: OPERATIONAL_BOUNDARY,
  });
}

export const capabilityOperationalV2Authority = OPERATIONAL_AUTHORITY;
