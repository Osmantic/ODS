import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { constants } from "node:fs";
import { chmod, lstat, mkdir, open, readdir } from "node:fs/promises";
import { isAbsolute, join, resolve } from "node:path";
import { promisify } from "node:util";

import { canonical } from "../work-broker/broker.mjs";
import { materializeUstar } from "../work-runner/safe-tar.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";

const executeFile = promisify(execFile);
const SHA_RE = /^[a-f0-9]{64}$/u;
const RUN_RE = /^outcomerun-([0-9]{13})-([a-f0-9]{12})$/u;
const MAX_JSON_BYTES = 16 * 1024 * 1024;
const MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024;
const EXECUTABLE_READ_FLAGS = constants.O_RDONLY
  | (constants.O_NOFOLLOW ?? 0)
  | (constants.O_CLOEXEC ?? 0)
  | (constants.O_NONBLOCK ?? 0);
const CHAT_PATH_KEYS = new Set([
  "PIXEL_SOURCE_PROJECTION_DIR", "PIXEL_ACTION_PROPOSAL_DIR", "PIXEL_ACTION_RESULT_DIR",
  "PIXEL_OPS_STATE_DIR", "PIXEL_OPS_REQUEST_DIR", "PIXEL_OPS_RESULT_DIR", "PIXEL_OPS_EVENT_DIR",
  "PIXEL_OPS_CANCEL_DIR", "PIXEL_OPS_INVENTORY_PATH", "PIXEL_FRONTIER_STATE_DIR",
  "PIXEL_FRONTIER_REQUEST_DIR", "PIXEL_FRONTIER_RESULT_DIR", "PIXEL_FRONTIER_EVENT_DIR",
  "PIXEL_FRONTIER_CANCEL_DIR", "PIXEL_FRONTIER_FEEDBACK_DIR",
]);
const CHAT_FLAG_KEYS = new Set([
  "PIXEL_CALENDAR_DIRECT_ENABLED", "PIXEL_LIMB_EMAIL_ENABLED", "PIXEL_LIMB_CALENDAR_ENABLED",
  "PIXEL_LIMB_SOCIAL_ENABLED", "PIXEL_LIMB_OPERATIONS_ENABLED", "PIXEL_LIMB_FRONTIER_ENABLED",
]);
const CHAT_KEYS = new Set([...CHAT_PATH_KEYS, ...CHAT_FLAG_KEYS, "PIXEL_SOURCE_STALE_AFTER_MS", "SEARXNG_BASE_URL"]);
const TEMPLATE_BOUNDARY = "Owner-private credential-free preparation inputs for one disposable Pixel portal Assistant comparison runtime. The template grants no model start, task execution, provider credential, external effect, policy change, acceptance, publication, deployment, or promotion authority.";
export const ASSISTANT_PREPARATION_BOUNDARY = "Private disposable Pixel portal Assistant preparation only. It binds one exact admitted source, request, DSV4 runtime, inference contract, qualification receipt, OpenClaw tool lease, local loopback proxy, and owner-private workspace; it grants no host access, ambient credential, direct network, package installation, external effect, merge, deployment, policy mutation, completion, publication, acceptance, or promotion authority.";

export class AssistantPreparationError extends Error {}
function fail(message) { throw new AssistantPreparationError(message); }
function hash(value) { return createHash("sha256").update(Buffer.isBuffer(value) || typeof value === "string" ? value : canonical(value)).digest("hex"); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} fields are invalid`);
  return value;
}
function absolute(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an exact absolute path`);
  return value;
}
async function privateDirectory(path, label, { empty = false } = {}) {
  absolute(path, label);
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is unavailable or linked`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  if (empty && (await readdir(path)).length !== 0) fail(`${label} is not empty`);
  return path;
}
async function privateFile(path, label, { executable = false } = {}) {
  absolute(path, label);
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1) fail(`${label} is unavailable, linked, or multiply linked`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o022) !== 0 || executable && (info.mode & 0o111) === 0)) fail(`${label} has unsafe ownership or mode`);
  return path;
}
async function trustedExecutable(path, label) {
  absolute(path, label);
  const handle = await open(path, EXECUTABLE_READ_FLAGS).catch(() => fail(`${label} is unavailable or linked`));
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size < 1 || before.size > MAX_EXECUTABLE_BYTES) {
      fail(`${label} is unavailable, linked, multiply linked, or unbounded`);
    }
    if (process.platform !== "win32") {
      const trustedOwner = before.uid === process.geteuid() || before.uid === 0;
      if (!trustedOwner || (before.mode & 0o022) !== 0 || (before.mode & 0o111) === 0) {
        fail(`${label} has unsafe ownership or mode`);
      }
    }
    const digest = createHash("sha256");
    let bytes = 0;
    while (bytes <= MAX_EXECUTABLE_BYTES) {
      const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, MAX_EXECUTABLE_BYTES + 1 - bytes));
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      digest.update(buffer.subarray(0, bytesRead));
      bytes += bytesRead;
    }
    const after = await handle.stat();
    if (
      bytes !== before.size || bytes > MAX_EXECUTABLE_BYTES
      || after.dev !== before.dev || after.ino !== before.ino || after.size !== before.size
      || after.mtimeMs !== before.mtimeMs || after.ctimeMs !== before.ctimeMs
    ) fail(`${label} changed while its identity was measured`);
    return Object.freeze({ path, bytes, sha256: digest.digest("hex") });
  } finally {
    await handle.close();
  }
}
async function privateJson(path, label) {
  await privateFile(path, label);
  let record;
  try { record = await readBoundedRegularFile(path, MAX_JSON_BYTES, label); } catch { fail(`${label} could not be read safely`); }
  try { return parseStrictJson(record.bytes.toString("utf8"), label); } catch { fail(`${label} is not strict JSON`); }
}
async function writePrivateNew(path, value) {
  const handle = await open(path, "wx", 0o600).catch(() => fail("Assistant private output already exists or cannot be created"));
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`); await handle.sync(); } finally { await handle.close(); }
  if (process.platform !== "win32") await chmod(path, 0o600);
}
function validateTemplate(value) {
  exactKeys(value, ["$schema", "schemaVersion", "openclawBinaryPath", "sandboxImage", "embeddingModel", "embeddingCachePath", "searxngBaseUrl", "proxyListenPort", "chatEnvironment", "actionJournalRoots", "boundary"], "Assistant runtime template");
  if (
    value.$schema !== "https://osmantic.com/pixel/schemas/portal-outcome-assistant-template-v1.schema.json"
    || value.schemaVersion !== 1 || !Number.isSafeInteger(value.proxyListenPort) || value.proxyListenPort < 1 || value.proxyListenPort > 65535
    || value.boundary !== TEMPLATE_BOUNDARY
    || ![value.sandboxImage, value.embeddingModel].every((child) => typeof child === "string" && child.length >= 1 && child.length <= 512 && !/[\0\r\n]/u.test(child))
    || !value.chatEnvironment || typeof value.chatEnvironment !== "object" || Array.isArray(value.chatEnvironment)
    || !Array.isArray(value.actionJournalRoots) || new Set(value.actionJournalRoots).size !== value.actionJournalRoots.length
  ) fail("Assistant runtime template is invalid");
  for (const field of ["openclawBinaryPath", "embeddingCachePath"]) absolute(value[field], `Assistant template ${field}`);
  for (const path of value.actionJournalRoots) absolute(path, "Assistant action journal root");
  for (const [name, child] of Object.entries(value.chatEnvironment)) {
    if (!CHAT_KEYS.has(name) || typeof child !== "string") fail("Assistant chat environment is invalid");
    if (CHAT_PATH_KEYS.has(name)) absolute(child, `Assistant chat environment ${name}`);
    else if (CHAT_FLAG_KEYS.has(name) && !["0", "1"].includes(child)) fail("Assistant chat environment flag is invalid");
    else if (name === "PIXEL_SOURCE_STALE_AFTER_MS" && (!/^[1-9][0-9]{0,9}$/u.test(child) || Number(child) > 86400000)) fail("Assistant source freshness bound is invalid");
    else if (name === "SEARXNG_BASE_URL") validateHttpOrigin(child, "Assistant chat search origin");
  }
  validateHttpOrigin(value.searxngBaseUrl, "Assistant search origin");
  return value;
}
function validateHttpOrigin(value, label) {
  let parsed;
  try { parsed = new URL(value); } catch { fail(`${label} is invalid`); }
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password || parsed.search || parsed.hash) fail(`${label} is invalid`);
  return value;
}
function safeInteger(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}
function renderEnvironment({ root, openclawHome, workspace, template, execution, modelContract, inferenceContract }) {
  const limbs = execution.plan.limbs;
  return {
    PATH: process.env.PATH ?? "/usr/local/bin:/usr/bin:/bin", LANG: "C.UTF-8", NODE_ENV: "production",
    OPENCLAW_HOME: openclawHome,
    PIXEL_GATEWAY_TOKEN: hash(`pixel-assistant-gateway:${execution.plan.requestSha256}`),
    PIXEL_GATEWAY_EXTENSIONS: "[]", PIXEL_AGENT_SKILLS: "[]",
    PIXEL_MODEL_PROVIDER: "local", PIXEL_MODEL_ID: modelContract.modelId,
    PIXEL_MODEL_API_KEY: "local-no-auth", PIXEL_MODEL_BASE_URL: `http://127.0.0.1:${template.proxyListenPort}/v1`,
    PIXEL_MODEL_NAME: modelContract.modelId, PIXEL_MODEL_REASONING: "1",
    PIXEL_MODEL_CONTEXT_WINDOW: String(modelContract.runtime.contextWindow),
    PIXEL_MODEL_MAX_TOKENS: String(inferenceContract.request.maxOutputTokens),
    PIXEL_AGENT_ID: "pixel", PIXEL_AGENT_NAME: "Pixel", PIXEL_WORKSPACE: workspace,
    PIXEL_AGENT_TOOL_ALLOWLIST: JSON.stringify(execution.plan.allowedTools),
    PIXEL_SANDBOX_IMAGE: template.sandboxImage,
    PIXEL_EMBEDDING_MODEL: template.embeddingModel, PIXEL_EMBEDDING_CACHE: template.embeddingCachePath,
    PIXEL_SEARXNG_BASE_URL: template.searxngBaseUrl,
    PIXEL_LIMB_EMAIL_ENABLED: limbs.email ? "1" : "0", PIXEL_LIMB_CALENDAR_ENABLED: limbs.calendar ? "1" : "0",
    PIXEL_LIMB_SOCIAL_ENABLED: "0", PIXEL_LIMB_WEB_ENABLED: limbs.web ? "1" : "0",
    PIXEL_LIMB_OPERATIONS_ENABLED: limbs.operations ? "1" : "0", PIXEL_LIMB_FRONTIER_ENABLED: "0",
    PIXEL_PLUGIN_PATH: join(root, "plugin"), PIXEL_OPS_PLUGIN_PATH: join(root, "plugin-ops"),
    PIXEL_FRONTIER_PLUGIN_PATH: join(root, "plugin-frontier"),
    ...Object.fromEntries(["SystemRoot", "WINDIR"].filter((name) => process.env[name]).map((name) => [name, process.env[name]])),
  };
}

function chatEnvironment(template, execution) {
  const retained = Object.fromEntries(Object.entries(template.chatEnvironment).filter(([name]) => !CHAT_FLAG_KEYS.has(name) && name !== "SEARXNG_BASE_URL"));
  return Object.freeze({
    ...retained,
    PIXEL_CALENDAR_DIRECT_ENABLED: "0",
    PIXEL_LIMB_EMAIL_ENABLED: execution.plan.limbs.email ? "1" : "0",
    PIXEL_LIMB_CALENDAR_ENABLED: execution.plan.limbs.calendar ? "1" : "0",
    PIXEL_LIMB_SOCIAL_ENABLED: "0",
    PIXEL_LIMB_OPERATIONS_ENABLED: execution.plan.limbs.operations ? "1" : "0",
    PIXEL_LIMB_FRONTIER_ENABLED: "0",
    ...(execution.plan.limbs.web ? { SEARXNG_BASE_URL: template.searxngBaseUrl } : {}),
  });
}

async function defaultRender({ nodeBinary, rendererPath, outputPath, environment }) {
  try {
    await executeFile(nodeBinary, [rendererPath, outputPath], {
      env: environment, timeout: 120000, windowsHide: true, maxBuffer: 1024 * 1024,
    });
  } catch { fail("Assistant OpenClaw configuration rendering failed"); }
}

export async function prepareAssistantRuntime(options, dependencies = {}) {
  const {
    root, runRoot, runId, sourceArchivePath, sourceReference, assistantTemplatePath, execution,
    modelContract, inferenceContract, qualification, backendOrigin, nodeBinary, rendererPath, proxyLauncherPath,
    archiveLimits,
  } = options ?? {};
  absolute(root, "Pixel source root"); absolute(runRoot, "Assistant system run root");
  const match = RUN_RE.exec(runId ?? ""); if (!match) fail("Assistant run identity is invalid");
  await privateDirectory(runRoot, "Assistant system run root");
  await privateFile(sourceArchivePath, "Assistant source archive");
  await privateFile(assistantTemplatePath, "Assistant runtime template");
  const nodeIdentity = await trustedExecutable(nodeBinary, "Assistant Node executable");
  await privateFile(rendererPath, "Assistant config renderer"); await privateFile(proxyLauncherPath, "Assistant proxy launcher");
  const template = validateTemplate(await privateJson(assistantTemplatePath, "Assistant runtime template"));
  const openclawIdentity = await trustedExecutable(template.openclawBinaryPath, "Assistant OpenClaw executable");
  await privateDirectory(template.embeddingCachePath, "Assistant embedding cache");
  for (const journalRoot of template.actionJournalRoots) await privateDirectory(journalRoot, "Assistant action journal root");
  if (
    !sourceReference || sourceReference.mediaType !== "application/x-tar" || !SHA_RE.test(sourceReference.sha256 ?? "")
    || !Number.isSafeInteger(sourceReference.bytes) || sourceReference.bytes < 1536
    || !execution?.request || !execution?.plan || execution.request.source.sha256 !== sourceReference.sha256
    || execution.request.source.bytes !== sourceReference.bytes || execution.plan.profile !== "assistant"
    || execution.request.modelContractSha256 !== hash(modelContract) || execution.request.inferenceContractSha256 !== hash(inferenceContract)
  ) fail("Assistant preparation differs from the exact admitted execution contract");
  exactKeys(qualification, ["qualificationId", "receiptSha256", "casesSha256", "evaluatorSha256", "profile", "maxContextTokens", "maxOutputTokens", "exactUsage"], "Assistant qualification binding");
  if (qualification.profile !== "assistant" || qualification.exactUsage !== true) fail("Assistant model is not profile-exact qualified");
  let backend;
  try { backend = new URL(backendOrigin); } catch { fail("Assistant backend origin is invalid"); }
  if (backend.protocol !== "http:" || backend.hostname !== "127.0.0.1" || backend.pathname !== "/" || backend.search || backend.hash || backend.username || backend.password) fail("Assistant backend origin is not exact loopback HTTP");

  const assistantRoot = join(runRoot, "assistant");
  await mkdir(assistantRoot, { mode: 0o700 }); if (process.platform !== "win32") await chmod(assistantRoot, 0o700);
  const openclawHome = join(assistantRoot, "openclaw-home"), workspace = join(assistantRoot, "workspace");
  await mkdir(openclawHome, { mode: 0o700 }); if (process.platform !== "win32") await chmod(openclawHome, 0o700);
  const materialize = dependencies.materializeUstar ?? materializeUstar;
  let materialized;
  try {
    materialized = await materialize(sourceArchivePath, workspace, {
      maxArchiveBytes: sourceReference.bytes,
      maxExtractedBytes: safeInteger(execution.plan.limits.maxDiskBytes, 1048576, 1099511627776, "Assistant extracted-byte ceiling"),
      maxEntries: safeInteger(archiveLimits?.maxEntries, 1, 100000, "Assistant archive entry ceiling"),
      maxFileBytes: safeInteger(archiveLimits?.maxFileBytes, 1, 4294967296, "Assistant archive file ceiling"),
      expectedSha256: sourceReference.sha256,
    });
  } catch (error) {
    if (error instanceof AssistantPreparationError) throw error;
    fail("Assistant source materialization failed");
  }
  const sourceInventoryPath = join(assistantRoot, "source-inventory.json");
  await writePrivateNew(sourceInventoryPath, {
    schemaVersion: 1, archiveSha256: materialized.archiveSha256, archiveBytes: materialized.archiveBytes,
    extractedBytes: materialized.extractedBytes, entries: materialized.entries, treeSha256: materialized.treeSha256,
  });
  const openclawConfigPath = join(openclawHome, "openclaw.json");
  const environment = renderEnvironment({ root, openclawHome, workspace, template, execution, modelContract, inferenceContract });
  const render = dependencies.renderConfig ?? defaultRender;
  await render({ nodeBinary, rendererPath, outputPath: openclawConfigPath, environment });
  const rendered = await privateJson(openclawConfigPath, "rendered Assistant OpenClaw configuration");
  if (canonical(rendered?.tools?.sandbox?.tools?.allow) !== canonical(execution.plan.allowedTools)) fail("rendered Assistant tool lease differs from the admitted plan");
  const agent = rendered?.agents?.list?.find?.((candidate) => candidate?.id === "pixel");
  if (!agent || agent.workspace !== workspace || agent.model !== `local/${modelContract.modelId}`) fail("rendered Assistant agent differs from the exact workspace or model");

  const onboardingPath = join(assistantRoot, "onboarding.json");
  await writePrivateNew(onboardingPath, {
    openclawBin: template.openclawBinaryPath, openclawHome, agentId: "pixel",
    modelProvider: "local", modelId: modelContract.modelId,
  });
  const proxyConfigPath = join(assistantRoot, "model-proxy.json"), proxyReceiptPath = join(assistantRoot, "model-proxy-receipt.json");
  const proxyBudgets = {
    maxRuntimeSeconds: safeInteger(execution.plan.limits.maxRuntimeSeconds, 1, 86400, "Assistant proxy runtime ceiling"),
    maxModelRequests: safeInteger(execution.plan.limits.maxModelRequests, 1, 1000000, "Assistant model request ceiling"),
    maxInputTokens: safeInteger(execution.plan.limits.maxInputTokens, 1, 2000000000, "Assistant input-token ceiling"),
    maxOutputTokens: safeInteger(execution.plan.limits.maxOutputTokens, 1, 500000000, "Assistant output-token ceiling"),
    maxNetworkBytes: Math.max(1048576, safeInteger(execution.plan.limits.maxNetworkBytes, 0, 10737418240, "Assistant proxy byte ceiling")),
    maxRequestBytes: Math.min(268435456, Math.max(131072, execution.plan.limits.maxInputTokens * 8)),
    maxResponseBytes: Math.min(268435456, Math.max(131072, execution.plan.limits.maxOutputTokens * 8)),
    maxRequestSeconds: Math.min(3600, execution.plan.limits.maxRuntimeSeconds),
  };
  proxyBudgets.maxNetworkBytes = Math.max(proxyBudgets.maxNetworkBytes, proxyBudgets.maxRequestBytes, proxyBudgets.maxResponseBytes);
  const proxyConfig = {
    schemaVersion: 1, jobId: `work-${match[1]}-${match[2]}`, claimId: `workclaim-${match[1]}-${match[2]}`,
    planSha256: hash(execution.plan), provider: "vllm", modelId: modelContract.modelId,
    contextWindow: modelContract.runtime.contextWindow, supportsVision: false, backendOrigin,
    listenHost: "127.0.0.1", listenPort: template.proxyListenPort, allowedClientIpv4: "127.0.0.1",
    allowedTools: execution.plan.allowedTools, receiptPath: "/run/pixel-work-output/model-proxy-receipt.json",
    qualification, inference: {
      enforcement: "exact-request-boundary-v1", wireApi: inferenceContract.request.wireApi,
      temperaturePermille: inferenceContract.sampling.temperaturePermille, topPPermille: inferenceContract.sampling.topPPermille,
      topK: inferenceContract.sampling.topK, minPPermille: inferenceContract.sampling.minPPermille,
      repeatPenaltyPermille: inferenceContract.sampling.repeatPenaltyPermille, seed: inferenceContract.sampling.seed,
      reasoningEffort: inferenceContract.sampling.reasoningEffort, reasoningVisibility: inferenceContract.sampling.reasoningVisibility,
      stream: inferenceContract.request.stream, maxOutputTokens: inferenceContract.request.maxOutputTokens,
      toolEncoding: inferenceContract.request.toolEncoding,
    }, budgets: proxyBudgets,
  };
  await writePrivateNew(proxyConfigPath, proxyConfig);
  const admittedChatEnvironment = chatEnvironment(template, execution);
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-portal-outcome-assistant-preparation", runId,
    requestSha256: execution.request.requestSha256, planSha256: hash(execution.plan),
    assistantRootPath: assistantRoot, workspacePath: workspace, workspaceTreeSha256: materialized.treeSha256,
    sourceInventoryPath,
    openclawHomePath: openclawHome, onboardingPath, proxyConfigPath, proxyReceiptPath,
    openclawBinaryPath: template.openclawBinaryPath,
    openclawBinarySha256: openclawIdentity.sha256, openclawBinaryBytes: openclawIdentity.bytes,
    nodeBinaryPath: nodeBinary, nodeBinarySha256: nodeIdentity.sha256, nodeBinaryBytes: nodeIdentity.bytes,
    proxyLauncherPath,
    allowedTools: execution.plan.allowedTools, chatEnvironment: admittedChatEnvironment,
    actionJournalRoots: template.actionJournalRoots, externalEffects: false,
    boundary: ASSISTANT_PREPARATION_BOUNDARY,
  });
}
