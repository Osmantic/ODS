import { constants } from "node:fs";
import { cp, chmod, mkdir, open, readFile, readdir, realpath, stat, writeFile } from "node:fs/promises";
import { homedir, userInfo } from "node:os";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { fileURLToPath } from "node:url";
import { createHash, randomBytes } from "node:crypto";
import { protectedHomeRuntimeMountRoot } from "./lib/systemd-paths.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const force = args.includes("--force");
const answersIndex = args.indexOf("--answers");
const answersPath = answersIndex >= 0 ? resolve(args[answersIndex + 1] ?? "") : null;
if (args.includes("--help") || args.includes("-h")) {
  console.log("Usage: ./pixel configure [--answers onboarding.json] [--force]");
  process.exit(0);
}
if (answersIndex >= 0 && !args[answersIndex + 1]) throw new Error("--answers requires a JSON file");

const home = homedir();
const serviceUser = text(userInfo().username, "serviceUser");
if (!/^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/.test(serviceUser)) throw new Error("Host username is not safe for systemd User=");
const release = JSON.parse(await readFile(join(root, "scripts/generated/release-constants.json"), "utf8"));
const version = text(release.pixel, "generated Pixel version");
let supplied = {};
if (answersPath) supplied = JSON.parse(await readFile(answersPath, "utf8"));
const interactive = !answersPath;
const prompt = interactive ? createInterface({ input: stdin, output: stdout }) : null;

async function answer(key, label, fallback) {
  if (supplied[key] !== undefined && supplied[key] !== null && supplied[key] !== "") return supplied[key];
  if (!prompt) return fallback;
  const value = await prompt.question(`${label} [${fallback}]: `);
  return value.trim() || fallback;
}

function text(value, name) {
  const result = String(value);
  if (!result || /[\0\r\n]/.test(result)) throw new Error(`${name} must be one non-empty line`);
  return result;
}
function absolute(value, name) {
  const result = text(value, name);
  if (!isAbsolute(result) || resolve(result) === resolve("/")) throw new Error(`${name} must be an absolute, non-root path`);
  return result;
}
function url(value, name) {
  const result = text(value, name);
  const parsed = new URL(result);
  if (!["http:", "https:"].includes(parsed.protocol)) throw new Error(`${name} must use http or https`);
  return result.replace(/\/$/, "");
}
function integer(value, name, minimum) {
  const result = Number(value);
  if (!Number.isSafeInteger(result) || result < minimum) throw new Error(`${name} must be an integer >= ${minimum}`);
  return result;
}
function boolean(value, name) {
  if (typeof value === "boolean") return value;
  const normalized = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  throw new Error(`${name} must be true or false`);
}
function shellQuote(value) { return `'${String(value).replace(/'/g, `'\\''`)}'`; }

const deploymentProfile = text(await answer("deploymentProfile", "Deployment profile (prepared/reference)", "prepared"), "deploymentProfile");
if (!["prepared", "reference"].includes(deploymentProfile)) throw new Error("deploymentProfile must be prepared or reference");
const capabilityProfile = text(await answer("capabilityProfile", "Capability profile (minimal/chief-of-staff/research/engineering-operator)", "chief-of-staff"), "capabilityProfile");
if (!/^[a-z][a-z0-9-]{1,63}$/.test(capabilityProfile)) throw new Error("capabilityProfile has an unsafe name");
let capabilityDefaults;
try { capabilityDefaults = JSON.parse(await readFile(join(root, "profiles", "capabilities", `${capabilityProfile}.json`), "utf8")); }
catch (error) { throw new Error(`Unknown capabilityProfile ${capabilityProfile}: ${error.message}`); }
for (const limb of ["email", "calendar", "social", "web", "operations", "frontier"]) {
  if (typeof capabilityDefaults?.limbs?.[limb] !== "boolean") throw new Error(`Capability profile ${capabilityProfile} is missing boolean limb ${limb}`);
}
const ownerName = text(await answer("ownerName", "Owner name", "Client Name"), "ownerName");
const organization = text(await answer("organization", "Organization", "Client Organization"), "organization");
const deploymentName = text(await answer("deploymentName", "Deployment name", "primary"), "deploymentName");
const timeZone = text(await answer("timeZone", "Timezone", "America/New_York"), "timeZone");
try { new Intl.DateTimeFormat("en-US", { timeZone }).format(); } catch { throw new Error(`Invalid IANA timezone: ${timeZone}`); }
const agentId = text(await answer("agentId", "Agent ID", "pixel"), "agentId");
if (!/^[a-z][a-z0-9-]{1,62}$/.test(agentId)) throw new Error("agentId must match ^[a-z][a-z0-9-]{1,62}$");
const agentName = text(await answer("agentName", "Agent name", "Pixel"), "agentName");
const openclawBin = absolute(await answer("openclawBin", "OpenClaw executable", join(home, ".npm-global", "bin", "openclaw")), "openclawBin");
const openclawHome = absolute(await answer("openclawHome", "OpenClaw home", join(home, ".openclaw")), "openclawHome");
const installDir = absolute(await answer("installDir", "Stable Pixel install directory", join(home, ".local", "share", "pixel")), "installDir");
const workspace = absolute(await answer("workspace", "Pixel workspace", join(openclawHome, "workspace-pixel")), "workspace");
const modelProvider = text(await answer("modelProvider", "Model provider ID", "local"), "modelProvider");
const modelId = text(await answer("modelId", "Model ID", "assistant-model"), "modelId");
const modelName = text(await answer("modelName", "Model display name", "Local Assistant Model"), "modelName");
const modelBaseUrl = url(await answer("modelBaseUrl", "OpenAI-compatible model URL", "http://127.0.0.1:8000/v1"), "modelBaseUrl");
const modelPrivateHosts = supplied.modelPrivateHosts ?? [];
if (!Array.isArray(modelPrivateHosts) || modelPrivateHosts.some((host) => typeof host !== "string" || !/^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$/.test(host))) {
  throw new Error("modelPrivateHosts must be an array of exact private-model hostnames or IP addresses");
}
if (new Set(modelPrivateHosts.map((host) => host.toLowerCase())).size !== modelPrivateHosts.length) throw new Error("modelPrivateHosts contains a duplicate host");
const modelApiKey = text(await answer("modelApiKey", "Model API key or local-no-auth", "local-no-auth"), "modelApiKey");
const modelReasoning = boolean(await answer("modelReasoning", "Model supports reasoning", true), "modelReasoning");
const modelContextWindow = integer(await answer("modelContextWindow", "Model context window", 131072), "modelContextWindow", 4096);
const modelMaxTokens = integer(await answer("modelMaxTokens", "Maximum output tokens", 4096), "modelMaxTokens", 256);
if (modelMaxTokens > modelContextWindow) throw new Error("modelMaxTokens cannot exceed modelContextWindow");
const webSearchProvider = text(await answer("webSearchProvider", "Web search provider (searxng|parallel-free)", "searxng"), "webSearchProvider");
if (!["searxng", "parallel-free"].includes(webSearchProvider)) throw new Error("webSearchProvider must be searxng or parallel-free");
const searxngBaseUrl = webSearchProvider === "searxng" ? url(await answer("searxngBaseUrl", "Private SearXNG URL", "http://127.0.0.1:8890"), "searxngBaseUrl") : "";
const embeddingModel = text(await answer("embeddingModel", "Embedding model", "embeddinggemma-300m-qat-Q8_0.gguf"), "embeddingModel");
const embeddingCache = absolute(await answer("embeddingCache", "Embedding cache", join(home, ".cache", "openclaw", "embeddings")), "embeddingCache");
const xdgConfigHome = process.env.XDG_CONFIG_HOME
  ? absolute(process.env.XDG_CONFIG_HOME, "XDG_CONFIG_HOME") : join(home, ".config");
const privateDeploymentDir = join(xdgConfigHome, "pixel-deployment");
const privateOnboardingPath = answersPath ? join(privateDeploymentDir, "onboarding.json") : "";
const releaseOperator = supplied.releaseOperator === undefined ? {} : supplied.releaseOperator;
if (!releaseOperator || typeof releaseOperator !== "object" || Array.isArray(releaseOperator)) {
  throw new Error("releaseOperator must be an object");
}
const releaseOperatorEnabled = boolean(releaseOperator.enabled ?? false, "releaseOperator.enabled");
const releaseOperatorUser = text(releaseOperator.user ?? "pixel-release-transport", "releaseOperator.user");
if (!/^[a-z_][a-z0-9_-]{0,31}$/.test(releaseOperatorUser)) {
  throw new Error("releaseOperator.user must be a safe system user");
}
const releaseOperatorKey = absolute(
  releaseOperator.key ?? join(privateDeploymentDir, "release-operator.key"),
  "releaseOperator.key",
);
const googleAccount = text(await answer("googleAccount", "Google Workspace account", "user@example.com"), "googleAccount");
if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(googleAccount)) throw new Error("googleAccount must be an email address");
const calendarId = text(await answer("calendarId", "Google Calendar ID", "primary"), "calendarId");
const gatewayPort = integer(await answer("gatewayPort", "OpenClaw gateway port", 18789), "gatewayPort", 1024);
if (gatewayPort > 65535) throw new Error("gatewayPort must be <= 65535");
const gatewayExtensions = supplied.gatewayExtensions ?? [];
const pinnedGatewayExtensionIds = new Set(["discord"]);
if (!Array.isArray(gatewayExtensions) || gatewayExtensions.some((extension) =>
  typeof extension !== "object" || extension === null || Array.isArray(extension) ||
  typeof extension.id !== "string" || !/^[a-z][a-z0-9-]{1,62}$/.test(extension.id) ||
  (extension.path !== undefined && (typeof extension.path !== "string" || !isAbsolute(extension.path) || resolve(extension.path) === resolve("/") || !/^[0-9a-f]{64}$/.test(extension.sha256 ?? ""))) ||
  (extension.path === undefined && (extension.sha256 !== undefined || extension.tools !== undefined)) ||
  (extension.tools !== undefined && (!Array.isArray(extension.tools) || extension.tools.length < 1 || extension.tools.length > 24 || extension.tools.some((name) => typeof name !== "string" || !/^pixel_[a-z0-9_]{2,63}$/.test(name))))
)) throw new Error("gatewayExtensions must contain {id} for bundled plugins or {id, absolute path, sha256, optional tools} for custom plugins");
if (new Set(gatewayExtensions.map(({ id }) => id)).size !== gatewayExtensions.length) throw new Error("gatewayExtensions contains a duplicate plugin ID");
const declaredExtensionTools = gatewayExtensions.flatMap(({ tools = [] }) => tools);
if (new Set(declaredExtensionTools).size !== declaredExtensionTools.length) throw new Error("gatewayExtensions contains a duplicate custom tool name");
if (declaredExtensionTools.some((name) => name === "pixel_limb_status" || /^pixel_(?:gmail|calendar|social|ops|frontier)_/.test(name))) {
  throw new Error("gatewayExtensions cannot override a Pixel-managed tool namespace");
}
if (gatewayExtensions.some(({ id, path }) => path === undefined && !pinnedGatewayExtensionIds.has(id))) {
  throw new Error("ID-only gatewayExtensions must be in Pixel's pinned extension catalog; custom extensions require an absolute path and SHA-256 tree digest");
}
if (webSearchProvider === "parallel-free") {
  const parallelExt = gatewayExtensions.find(({ id }) => id === "parallel");
  if (!parallelExt || parallelExt.path === undefined) {
    throw new Error("webSearchProvider=parallel-free requires a gateway extension with id=parallel and an absolute path");
  }
  if (!isAbsolute(parallelExt.path) || !/^[0-9a-f]{64}$/.test(parallelExt.sha256)) {
    throw new Error("parallel gateway extension requires an absolute path and valid sha256");
  }
}
const managedPluginIds = new Set(["searxng", "llama-cpp", "pixel-source-broker", "pixel-operations-broker", "pixel-frontier-broker"]);
if (gatewayExtensions.some(({ id }) => managedPluginIds.has(id))) throw new Error("gatewayExtensions cannot override a Pixel-managed plugin");
const pathWithin = (path, parent) => resolve(path) === resolve(parent) || resolve(path).startsWith(`${resolve(parent)}/`);
if (gatewayExtensions.some(({ path }) => path && [openclawHome, workspace, embeddingCache].some((writable) => pathWithin(path, writable)))) {
  throw new Error("Custom gateway extensions cannot be loaded from gateway-writable state, workspace, or cache paths");
}
async function boundedNoFollow(path, maximum, label) {
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await handle.stat();
    if (!info.isFile() || info.nlink !== 1 || info.size < 2 || info.size > maximum) throw new Error(`${label} must be a bounded single-link file`);
    return await handle.readFile();
  } finally { await handle.close(); }
}
const signedPolicySourceCache = new Map();
async function signedPolicyReceipt(spec, label, category, kind, schemaUrl) {
  if (!spec || typeof spec !== "object" || Array.isArray(spec) || typeof spec.file !== "string" || !isAbsolute(spec.file) || !/^[a-f0-9]{64}$/.test(spec.sha256 ?? "") || !/^[a-z][a-z0-9-]{1,62}$/.test(spec.policyPackId ?? "")) {
    throw new Error(`${label} must contain an absolute file, SHA-256, policyPackId, and signed limb provenance`);
  }
  const source = spec.sourceLimbPack;
  if (!source || typeof source !== "object" || Array.isArray(source) || JSON.stringify(Object.keys(source).sort()) !== '["id","treeSha256","version"]' || !/^[a-z][a-z0-9-]{1,17}$/.test(source.id ?? "") || !/^(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})$/.test(source.version ?? "") || !/^[a-f0-9]{64}$/.test(source.treeSha256 ?? "")) {
    throw new Error(`${label} has invalid signed limb provenance`);
  }
  const extension = gatewayExtensions.find((item) => item.id === source.id && item.path);
  if (!extension) throw new Error(`${label} source limb is not an enabled signed gateway extension`);
  const expectedFile = resolve(extension.path, "packs", `${spec.policyPackId}.json`);
  if (resolve(spec.file) !== expectedFile || !pathWithin(expectedFile, resolve(extension.path, "packs"))) throw new Error(`${label} escaped its signed limb pack directory`);
  const cacheKey = extension.path;
  let sourceContract = signedPolicySourceCache.get(cacheKey);
  if (!sourceContract) {
    const manifest = JSON.parse((await boundedNoFollow(resolve(extension.path, "pixel-limb.json"), 262144, `${label} limb manifest`)).toString("utf8"));
    const lock = JSON.parse((await boundedNoFollow(resolve(extension.path, "pixel-pack.lock.json"), 2097152, `${label} limb lock`)).toString("utf8"));
    if (manifest?.schemaVersion !== 1 || manifest?.kind !== "projection-limb" || manifest.id !== source.id || manifest.version !== source.version || lock?.packId !== source.id || lock?.packVersion !== source.version || lock?.treeSha256 !== source.treeSha256) {
      throw new Error(`${label} signed limb provenance differs from its manifest or tree lock`);
    }
    sourceContract = { manifest, lock, extension };
    signedPolicySourceCache.set(cacheKey, sourceContract);
  }
  const relative = `packs/${spec.policyPackId}.json`;
  if (!Array.isArray(sourceContract.manifest?.extensions?.[category]) || !sourceContract.manifest.extensions[category].includes(relative)) {
    throw new Error(`${label} is not declared in its signed limb manifest`);
  }
  const payload = await boundedNoFollow(spec.file, 262144, label);
  if (createHash("sha256").update(payload).digest("hex") !== spec.sha256) throw new Error(`${label} differs from its onboarding SHA-256 receipt`);
  const value = JSON.parse(payload.toString("utf8"));
  if (value?.$schema !== schemaUrl || value?.schemaVersion !== 1 || value?.kind !== kind || value?.id !== spec.policyPackId || value?.version !== source.version) {
    throw new Error(`${label} schema identity differs from its signed receipt`);
  }
  return { value, sourceContract };
}
const localCapabilitySpecs = supplied.localCapabilityPacks ?? [];
if (!Array.isArray(localCapabilitySpecs) || localCapabilitySpecs.length > 256) throw new Error("localCapabilityPacks must be a bounded signed-receipt array");
const localCapabilityPacks = [];
const localCapabilityIds = new Set();
for (const [index, spec] of localCapabilitySpecs.entries()) {
  if (JSON.stringify(Object.keys(spec ?? {}).sort()) !== '["file","policyPackId","sha256","sourceLimbPack"]') throw new Error(`localCapabilityPacks[${index}] has unknown fields`);
  const { value, sourceContract } = await signedPolicyReceipt(spec, `localCapabilityPacks[${index}]`, "localCapabilities", "local-capability", "https://osmantic.com/pixel/schemas/local-capability-pack-v1.schema.json");
  const identity = `${spec.sourceLimbPack.id}:${value.id}`;
  if (localCapabilityIds.has(identity)) throw new Error("localCapabilityPacks contains a duplicate signed capability");
  localCapabilityIds.add(identity);
  if (!Array.isArray(value.tools) || value.tools.length < 1 || value.tools.some((tool) => !sourceContract.extension.tools?.includes(tool)) || value.trust !== "untrusted-projection" || value.authority !== "observe-only" || value.rawContentStored !== false) {
    throw new Error(`localCapabilityPacks[${index}] widens the signed local projection boundary`);
  }
  localCapabilityPacks.push({ sourceLimbPack: spec.sourceLimbPack.id, id: value.id, name: value.name, tools: value.tools, classifications: value.classifications, retentionDays: value.retentionDays });
}
let existingGatewayToken = "";
try {
  const existing = JSON.parse(await readFile(join(openclawHome, "openclaw.json"), "utf8"));
  existingGatewayToken = existing?.gateway?.auth?.token ?? existing?.gateway?.token ?? "";
} catch (error) {
  if (error.code !== "ENOENT") throw new Error(`Cannot inspect existing OpenClaw gateway authentication: ${error.message}`);
}
const gatewayToken = existingGatewayToken || randomBytes(32).toString("hex");
const agentSkills = supplied.agentSkills ?? [];
if (!Array.isArray(agentSkills) || agentSkills.some((name) => typeof name !== "string" || !/^[a-z0-9][a-z0-9._-]{0,127}$/.test(name))) {
  throw new Error("agentSkills must be an array of explicit installed skill names");
}
if (new Set(agentSkills).size !== agentSkills.length) throw new Error("agentSkills contains a duplicate name");
const legacyWebEnabled = supplied.webCourierEnabled;
const emailLimbEnabled = boolean(await answer("emailLimbEnabled", "Enable email limb", capabilityDefaults.limbs.email), "emailLimbEnabled");
const calendarLimbEnabled = boolean(await answer("calendarLimbEnabled", "Enable Calendar limb", capabilityDefaults.limbs.calendar), "calendarLimbEnabled");
const calendarDirectEnabled = calendarLimbEnabled && boolean(await answer("calendarDirectEnabled", "Directly apply bounded private creates and time-only reschedules", true), "calendarDirectEnabled");
const socialLimbEnabled = boolean(await answer("socialLimbEnabled", "Enable social limb", capabilityDefaults.limbs.social), "socialLimbEnabled");
const webLimbEnabled = boolean(await answer("webLimbEnabled", "Enable hardened Web limb", legacyWebEnabled ?? capabilityDefaults.limbs.web), "webLimbEnabled");
const operationsLimbEnabled = boolean(await answer("operationsLimbEnabled", "Enable isolated Operations limb", capabilityDefaults.limbs.operations), "operationsLimbEnabled");
const frontierLimbEnabled = boolean(await answer("frontierLimbEnabled", "Enable privacy-compiled Frontier review limb", capabilityDefaults.limbs.frontier), "frontierLimbEnabled");
const frontierAuthPreference = supplied.frontierAuthMode;
if (frontierAuthPreference !== undefined && !["api-key", "chatgpt"].includes(frontierAuthPreference)) {
  throw new Error("frontierAuthMode must be chatgpt or api-key so subscription access and API billing remain distinct");
}
const frontierBudgetPreference = supplied.frontierBudgetProfile;
if (frontierBudgetPreference !== undefined && !["starter", "balanced", "expanded", "custom"].includes(frontierBudgetPreference)) {
  throw new Error("frontierBudgetProfile must be starter, balanced, expanded, or custom");
}
const frontierPolicyApi = join(root, "deploy", "frontier-broker", "policy.example.json");
const frontierPolicyChatgpt = join(root, "deploy", "frontier-broker", "policy.chatgpt.example.json");
const managedFrontierPolicies = new Set([resolve(frontierPolicyApi), resolve(frontierPolicyChatgpt)]);
const requestedFrontierPolicy = supplied.frontierPolicyFile;
const frontierPolicyDefault = frontierAuthPreference === "chatgpt" ? frontierPolicyChatgpt : frontierPolicyApi;
const frontierPolicyChoice = frontierAuthPreference !== undefined && (
  requestedFrontierPolicy === undefined || managedFrontierPolicies.has(resolve(requestedFrontierPolicy))
) ? frontierPolicyDefault : requestedFrontierPolicy;
const frontierPolicyFile = absolute(frontierLimbEnabled
  ? (frontierPolicyChoice ?? await answer("frontierPolicyFile", "Frontier policy file", frontierPolicyDefault))
  : (frontierPolicyChoice ?? frontierPolicyDefault), "frontierPolicyFile");
const frontierPolicyManaged = managedFrontierPolicies.has(resolve(frontierPolicyFile));
let frontierPolicy;
try { frontierPolicy = JSON.parse(await readFile(frontierPolicyFile, "utf8")); }
catch (error) {
  if (frontierLimbEnabled) throw new Error(`Cannot read frontierPolicyFile: ${error.message}`);
  frontierPolicy = JSON.parse(await readFile(frontierPolicyDefault, "utf8"));
}
if (![1, 2].includes(frontierPolicy?.schemaVersion) || !["codex", "mock"].includes(frontierPolicy?.provider?.kind) || typeof frontierPolicy?.taskClasses !== "object") {
  throw new Error("frontierPolicyFile must contain a supported schemaVersion 1 or 2 policy with a provider and taskClasses");
}
if (frontierPolicy.provider.kind !== "codex" || typeof frontierPolicy.provider.codexBinary !== "string") {
  throw new Error("frontierPolicyFile must configure a Codex provider with an absolute executable path");
}
const frontierCodexBinary = absolute(frontierPolicy.provider.codexBinary, "frontierPolicy.provider.codexBinary");
if (frontierPolicy.schemaVersion === 2 && frontierPolicy.provider.authMode === undefined) {
  throw new Error("frontierPolicy.provider.authMode must be explicit in schemaVersion 2 so subscription and API billing cannot be confused");
}
const frontierAuthMode = frontierPolicy.provider.authMode ?? "api-key";
if (!["api-key", "chatgpt"].includes(frontierAuthMode)) throw new Error("frontierPolicy.provider.authMode must be api-key or chatgpt");
if (frontierLimbEnabled && frontierAuthPreference !== undefined && frontierAuthMode !== frontierAuthPreference) {
  throw new Error("frontierAuthMode differs from the selected custom Frontier policy; change the private policy or the public billing choice");
}
frontierPolicy.provider.authMode = frontierAuthMode;
const frontierBudgetPresets = {
  starter: { windowSeconds: 86400, maxJobs: 5, maxInputTokens: 50000, maxOutputTokens: 10000, maxFailures: 2, maxEstimatedCostMicros: null },
  balanced: { windowSeconds: 86400, maxJobs: 20, maxInputTokens: 200000, maxOutputTokens: 40000, maxFailures: 5, maxEstimatedCostMicros: null },
  expanded: { windowSeconds: 86400, maxJobs: 50, maxInputTokens: 500000, maxOutputTokens: 100000, maxFailures: 10, maxEstimatedCostMicros: null },
};
if (frontierLimbEnabled && frontierPolicyManaged && frontierBudgetPreference === "custom") {
  throw new Error("frontierBudgetProfile custom requires a private custom Frontier policy");
}
if (frontierLimbEnabled && !frontierPolicyManaged && frontierBudgetPreference !== undefined && frontierBudgetPreference !== "custom") {
  throw new Error("frontierBudgetProfile differs from the selected custom Frontier policy; edit the private policy or select custom");
}
const frontierBudgetProfile = frontierPolicyManaged ? (frontierBudgetPreference ?? "balanced") : "custom";
if (frontierPolicyManaged) frontierPolicy.budgets = { ...frontierBudgetPresets[frontierBudgetProfile] };
const frontierTaskSpecs = supplied.frontierTaskPacks ?? [];
if (!Array.isArray(frontierTaskSpecs) || frontierTaskSpecs.length > 32) throw new Error("frontierTaskPacks must be a bounded signed-receipt array");
const frontierTaskPacks = [];
const frontierTaskClasses = new Set();
for (const [index, spec] of frontierTaskSpecs.entries()) {
  if (JSON.stringify(Object.keys(spec ?? {}).sort()) !== '["file","policyPackId","sha256","sourceLimbPack"]') throw new Error(`frontierTaskPacks[${index}] has unknown fields`);
  const { value, sourceContract } = await signedPolicyReceipt(spec, `frontierTaskPacks[${index}]`, "frontierTaskPacks", "frontier-task-pack", "https://osmantic.com/pixel/schemas/frontier-task-pack-v1.schema.json");
  if (value.mode !== "restrict" || !["plan_review", "failure_triage"].includes(value.taskClass) || frontierTaskClasses.has(value.taskClass)) throw new Error(`frontierTaskPacks[${index}] must uniquely restrict one supported typed task`);
  frontierTaskClasses.add(value.taskClass);
  if (!Array.isArray(value.localTools) || value.localTools.length < 1 || value.localTools.some((tool) => !sourceContract.extension.tools?.includes(tool))) throw new Error(`frontierTaskPacks[${index}] references a tool outside its signed local limb`);
  const baseTask = frontierPolicy.taskClasses[value.taskClass];
  const restriction = value.policy;
  if (!baseTask || !restriction || typeof restriction !== "object" || Array.isArray(restriction) || JSON.stringify(Object.keys(restriction).sort()) !== '["allowedClassifications","enabled","maxInputTokens","maxOutputTokens","rehydrate"]') {
    throw new Error(`frontierTaskPacks[${index}] cannot add a task absent from the typed Frontier broker`);
  }
  if (typeof restriction.enabled !== "boolean" || typeof restriction.rehydrate !== "boolean" || (restriction.enabled && baseTask.enabled !== true) || (restriction.rehydrate && baseTask.rehydrate !== true)) {
    throw new Error(`frontierTaskPacks[${index}] attempts to enable behavior disabled by the base policy`);
  }
  if (!Array.isArray(restriction.allowedClassifications) || restriction.allowedClassifications.length < 1 || new Set(restriction.allowedClassifications).size !== restriction.allowedClassifications.length || restriction.allowedClassifications.some((classification) => !baseTask.allowedClassifications.includes(classification) || !frontierPolicy.dataPolicy.allowedClassifications.includes(classification))) {
    throw new Error(`frontierTaskPacks[${index}] attempts to widen Frontier data classification policy`);
  }
  if (!Number.isInteger(restriction.maxInputTokens) || !Number.isInteger(restriction.maxOutputTokens) || restriction.maxInputTokens < 128 || restriction.maxOutputTokens < 64 || restriction.maxInputTokens > baseTask.maxInputTokens || restriction.maxOutputTokens > baseTask.maxOutputTokens) {
    throw new Error(`frontierTaskPacks[${index}] attempts to widen Frontier token budgets`);
  }
  frontierPolicy.taskClasses[value.taskClass] = { ...restriction };
  frontierTaskPacks.push({ sourceLimbPack: spec.sourceLimbPack.id, id: value.id, name: value.name, taskClass: value.taskClass, localTools: value.localTools });
}
const frontierCredentialDefault = frontierAuthMode === "chatgpt"
  ? "/secure/client-config/codex-auth.json"
  : "/secure/client-config/openai-api-key";
const frontierCredentialFile = absolute(frontierLimbEnabled
  ? await answer("frontierCredentialFile", "Frontier provider credential file", frontierCredentialDefault)
  : (supplied.frontierCredentialFile ?? frontierCredentialDefault), "frontierCredentialFile");
frontierPolicy = { ...frontierPolicy, deployment: deploymentName };
const primaryModelHost = new URL(modelBaseUrl).hostname.replace(/^\[|\]$/g, "").toLowerCase();
const primaryModelOctets = primaryModelHost.split(".").map(Number);
const primaryModelPrivate = primaryModelHost === "localhost" || primaryModelHost === "::1" || primaryModelHost.endsWith(".local") || (
  primaryModelOctets.length === 4 && primaryModelOctets.every((part) => Number.isInteger(part) && part >= 0 && part <= 255) && (
    primaryModelOctets[0] === 10 || primaryModelOctets[0] === 127 ||
    (primaryModelOctets[0] === 192 && primaryModelOctets[1] === 168) ||
    (primaryModelOctets[0] === 172 && primaryModelOctets[1] >= 16 && primaryModelOctets[1] <= 31)
  )
);
const primaryModelOperatorAttested = modelPrivateHosts.some((allowed) => allowed.toLowerCase() === primaryModelHost);
if (frontierLimbEnabled) {
  if (!(primaryModelPrivate || primaryModelOperatorAttested)) {
    throw new Error("Frontier mode requires the primary Pixel model to use loopback, RFC1918, .local, or an exact modelPrivateHosts entry");
  }
}
const operationsPolicyFile = absolute(await answer("operationsPolicyFile", "Operations policy file", join(root, "deploy", "ops-broker", "policy.example.json")), "operationsPolicyFile");
let operationsPolicy;
try { operationsPolicy = JSON.parse(await readFile(operationsPolicyFile, "utf8")); }
catch (error) {
  if (operationsLimbEnabled) throw new Error(`Cannot read operationsPolicyFile: ${error.message}`);
  operationsPolicy = JSON.parse(await readFile(join(root, "deploy", "ops-broker", "policy.example.json"), "utf8"));
}
const operationsActionPackFiles = supplied.operationsActionPackFiles ?? [];
if (!Array.isArray(operationsActionPackFiles) || operationsActionPackFiles.some((item) => typeof item !== "string" || !isAbsolute(item))) {
  throw new Error("operationsActionPackFiles must be an array of absolute paths");
}
if (supplied.operationsActionPacks !== undefined && supplied.operationsActionPackFiles !== undefined) throw new Error("Use operationsActionPacks or legacy operationsActionPackFiles, not both");
const operationsActionPacks = supplied.operationsActionPacks ?? operationsActionPackFiles.map((file) => ({ file, targets: {} }));
if (!Array.isArray(operationsActionPacks) || operationsActionPacks.some((item) =>
  typeof item !== "object" || item === null || Array.isArray(item) ||
  Object.keys(item).some((key) => !["file", "targets", "skipActions", "sha256", "policyPackId", "sourceLimbPack"].includes(key)) ||
  typeof item.file !== "string" || !isAbsolute(item.file) ||
  typeof (item.targets ?? {}) !== "object" || Array.isArray(item.targets) || !Array.isArray(item.skipActions ?? []) ||
  (item.sha256 !== undefined && !/^[a-f0-9]{64}$/.test(item.sha256)) ||
  ((item.policyPackId !== undefined || item.sourceLimbPack !== undefined) && (!item.policyPackId || !item.sourceLimbPack || !item.sha256))
)) {
  throw new Error("operationsActionPacks must be an array of {file:absolutePath,targets:{placeholder:[targetIds]},skipActions:[]}");
}
if (operationsActionPacks.length > 0 && operationsPolicy?.schemaVersion !== 2) {
  throw new Error("operationsActionPacks require an operations schemaVersion 2 base policy");
}
for (const [index, actionPackSpec] of operationsActionPacks.entries()) {
  const actionPackFile = actionPackSpec.file;
  let pack;
  try {
    if (actionPackSpec.sourceLimbPack) {
      ({ value: pack } = await signedPolicyReceipt(actionPackSpec, `operationsActionPacks[${index}]`, "operationsActionPacks", "operations-action-pack", "https://osmantic.com/pixel/schemas/operations-action-pack-v1.schema.json"));
    } else {
      const payload = await boundedNoFollow(actionPackFile, 2097152, `operationsActionPacks[${index}]`);
      if (actionPackSpec.sha256 && createHash("sha256").update(payload).digest("hex") !== actionPackSpec.sha256) throw new Error("file differs from its SHA-256 receipt");
      pack = JSON.parse(payload.toString("utf8"));
    }
  }
  catch (error) { throw new Error(`Cannot read operationsActionPacks[${index}]: ${error.message}`); }
  if (pack?.schemaVersion !== 1 || typeof pack.actions !== "object" || !Array.isArray(pack.authorityGrants ?? [])) {
    throw new Error(`operationsActionPacks[${index}] must contain schemaVersion 1, actions, and optional authorityGrants`);
  }
  const targetMap = actionPackSpec.targets ?? {};
  const skipped = new Set(actionPackSpec.skipActions ?? []);
  if ([...skipped].some((name) => typeof name !== "string" || !Object.hasOwn(pack.actions, name))) {
    throw new Error(`operationsActionPacks[${index}] contains an unknown skipped action`);
  }
  if ((pack.authorityGrants ?? []).some((grant) => (grant.actions ?? []).some((name) => skipped.has(name)))) {
    throw new Error(`operationsActionPacks[${index}] cannot skip an action referenced by a pack authority grant`);
  }
  for (const [placeholder, replacements] of Object.entries(targetMap)) {
    if (!/^[a-z][a-z0-9_-]{1,63}$/.test(placeholder) || (pack.targetPlaceholder !== undefined && placeholder !== pack.targetPlaceholder) || !Array.isArray(replacements) || replacements.length === 0 || replacements.some((target) => typeof target !== "string" || !Object.hasOwn(operationsPolicy.targets, target))) {
      throw new Error(`operationsActionPacks[${index}] contains an invalid target replacement`);
    }
  }
  const replaceTargets = (targets) => {
    const replaced = [...new Set((targets ?? ["*"]).flatMap((target) => targetMap[target] ?? [target]))];
    if (replaced.some((target) => target !== "*" && !Object.hasOwn(operationsPolicy.targets, target))) throw new Error(`operationsActionPacks[${index}] references an unknown target after mapping`);
    return replaced;
  };
  for (const [name, action] of Object.entries(pack.actions)) {
    if (skipped.has(name)) continue;
    if (Object.hasOwn(operationsPolicy.actions, name)) throw new Error(`Duplicate Operations action from action pack: ${name}`);
    operationsPolicy.actions[name] = { ...action, targets: replaceTargets(action.targets) };
  }
  operationsPolicy.authority ??= { defaultLevel: "propose", grants: [] };
  operationsPolicy.authority.grants ??= [];
  operationsPolicy.authority.grants.push(...(pack.authorityGrants ?? []).map((grant) => ({ ...grant, targets: replaceTargets(grant.targets) })));
}
const signedOperationsActionPacks = operationsActionPacks
  .filter((spec) => spec.sourceLimbPack)
  .map((spec) => ({ sourceLimbPack: spec.sourceLimbPack.id, id: spec.policyPackId }));
if (operationsLimbEnabled) {
  if (![1, 2].includes(operationsPolicy?.schemaVersion) || typeof operationsPolicy.targets !== "object" || typeof operationsPolicy.actions !== "object") throw new Error("operationsPolicyFile must contain a schemaVersion 1 or 2 policy with targets and actions");
  if (Object.values(operationsPolicy.targets).some((target) => target?.enabled !== false && String(target?.expectedHostname ?? "").startsWith("REPLACE_WITH_"))) throw new Error("operationsPolicyFile still contains an enabled placeholder hostname");
}
prompt?.close();

const googleDir = join(home, ".config", "pixel-google-workspace");
const sourceBrokerInstallDir = "/opt/pixel-source-broker";
const sourceBrokerStateDir = "/var/lib/pixel-source-broker";
const sourceProjectionDir = join(sourceBrokerStateDir, "projection");
const actionProposalDir = join(sourceBrokerStateDir, "proposals");
const actionResultDir = join(sourceBrokerStateDir, "results");
const sourceTokenPath = join(sourceBrokerStateDir, "private", "google-token.json");
const sourceBrokerEnabled = emailLimbEnabled || calendarLimbEnabled || socialLimbEnabled;
const opsBrokerInstallDir = "/opt/pixel-ops-broker";
const opsBrokerStateDir = "/var/lib/pixel-ops-broker";
const opsPolicyPath = "/etc/pixel-ops-broker/policy.json";
const opsRequestDir = join(opsBrokerStateDir, "requests");
const opsResultDir = join(opsBrokerStateDir, "results");
const opsEventDir = join(opsBrokerStateDir, "events");
const opsCancelDir = join(opsBrokerStateDir, "cancel");
const frontierBrokerInstallDir = "/opt/pixel-frontier-broker";
const frontierBrokerStateDir = "/var/lib/pixel-frontier-broker";
const frontierPolicyPath = "/etc/pixel-frontier-broker/policy.json";
const frontierCredentialPath = frontierAuthMode === "chatgpt"
  ? join(frontierBrokerStateDir, "private", "codex-auth", "auth.json")
  : join(frontierBrokerStateDir, "private", "provider-key");
const frontierRequestDir = join(frontierBrokerStateDir, "requests");
const frontierResultDir = join(frontierBrokerStateDir, "results");
const frontierEventDir = join(frontierBrokerStateDir, "events");
const frontierCancelDir = join(frontierBrokerStateDir, "cancel");
const frontierFeedbackDir = join(frontierBrokerStateDir, "feedback");
const webCourierLog = join(openclawHome, "logs", "web-courier.jsonl");
const webCourierBrowserPath = join(installDir, "browsers");
const webCourierWheelhouse = join(installDir, "bootstrap", "web-courier", "wheelhouse");
const values = {
  PIXEL_DEPLOYMENT_PROFILE: deploymentProfile,
  PIXEL_CAPABILITY_PROFILE: capabilityProfile,
  PIXEL_RELEASE_VERSION: version,
  PIXEL_OPENCLAW_VERSION: release.openclaw,
  PIXEL_DISCORD_PLUGIN_VERSION: release.openclawPlugins["@openclaw/discord"],
  PIXEL_SEARXNG_PLUGIN_VERSION: release.openclawPlugins["@openclaw/searxng-plugin"],
  PIXEL_LLAMA_CPP_PLUGIN_VERSION: release.openclawPlugins["@openclaw/llama-cpp-provider"],
  OPENCLAW_BIN: openclawBin,
  OPENCLAW_HOME: openclawHome,
  PIXEL_INSTALL_DIR: installDir,
  PIXEL_SYSTEMD_UNIT: "openclaw-gateway.service",
  PIXEL_GATEWAY_SYSTEMD_DIR: "/etc/systemd/system",
  PIXEL_RELEASE_OPERATOR_ENABLED: releaseOperatorEnabled ? "1" : "0",
  PIXEL_RELEASE_OPERATOR_USER: releaseOperatorUser,
  PIXEL_RELEASE_OPERATOR_KEY: releaseOperatorKey,
  PIXEL_GATEWAY_EXTENSIONS: JSON.stringify(gatewayExtensions),
  PIXEL_GATEWAY_TOKEN: gatewayToken,
  PIXEL_AGENT_SKILLS: JSON.stringify(agentSkills),
  PIXEL_PRIVATE_ONBOARDING_PATH: privateOnboardingPath,
  PIXEL_LIMB_EMAIL_ENABLED: emailLimbEnabled ? "1" : "0",
  PIXEL_LIMB_CALENDAR_ENABLED: calendarLimbEnabled ? "1" : "0",
  PIXEL_LIMB_SOCIAL_ENABLED: socialLimbEnabled ? "1" : "0",
  PIXEL_LIMB_WEB_ENABLED: webLimbEnabled ? "1" : "0",
  PIXEL_LIMB_OPERATIONS_ENABLED: operationsLimbEnabled ? "1" : "0",
  PIXEL_LIMB_FRONTIER_ENABLED: frontierLimbEnabled ? "1" : "0",
  PIXEL_WEB_COURIER_ENABLED: webLimbEnabled ? "1" : "0",
  PIXEL_WEB_COURIER_UNIT: "pixel-web-courier.service",
  PIXEL_COURIER_SYSTEMD_DIR: "/etc/systemd/system",
  PIXEL_WEB_COURIER_LOG_PATH: webCourierLog,
  PIXEL_WEB_COURIER_BROWSER_PATH: webCourierBrowserPath,
  PIXEL_WEB_COURIER_WHEELHOUSE: webCourierWheelhouse,
  PIXEL_WEB_COURIER_NAV_TIMEOUT_MS: "30000",
  PIXEL_WEB_COURIER_TEXT_CAP: String(5 * 1024 * 1024),
  PIXEL_WEB_COURIER_SCREENSHOT_CAP: String(20 * 1024 * 1024),
  PIXEL_WEB_COURIER_DOMAIN_INTERVAL: "3",
  PIXEL_WEB_COURIER_ALLOWED_PORTS: "80,443",
  PIXEL_GATEWAY_PORT: gatewayPort,
  PIXEL_AGENT_ID: agentId,
  PIXEL_AGENT_NAME: agentName,
  PIXEL_WORKSPACE: workspace,
  PIXEL_MODEL_PROVIDER: modelProvider,
  PIXEL_MODEL_ID: modelId,
  PIXEL_MODEL_NAME: modelName,
  PIXEL_MODEL_BASE_URL: modelBaseUrl,
  PIXEL_MODEL_API_KEY: modelApiKey,
  PIXEL_MODEL_REASONING: modelReasoning ? "1" : "0",
  PIXEL_MODEL_CONTEXT_WINDOW: modelContextWindow,
  PIXEL_MODEL_MAX_TOKENS: modelMaxTokens,
  PIXEL_WEB_SEARCH_PROVIDER: webSearchProvider,
  PIXEL_SEARXNG_BASE_URL: searxngBaseUrl,
  PIXEL_SEARXNG_IMAGE: release.referenceImages.searxng,
  PIXEL_EMBEDDING_MODEL: embeddingModel,
  PIXEL_EMBEDDING_CACHE: embeddingCache,
  PIXEL_SANDBOX_IMAGE: release.sandboxImage,
  PIXEL_ENABLE_REFERENCE_MODEL: "0",
  PIXEL_LLAMA_IMAGE: release.referenceImages.llamaCpp,
  PIXEL_REFERENCE_MODEL_FILE: "/srv/pixel/models/model.gguf",
  PIXEL_REFERENCE_MODEL_PORT: "8000",
  PIXEL_GOOGLE_ACCOUNT: googleAccount,
  PIXEL_TIME_ZONE: timeZone,
  PIXEL_CALENDAR_ID: calendarId,
  PIXEL_GOOGLE_CONFIG_DIR: googleDir,
  PIXEL_GOOGLE_CLIENT_FILE: join(googleDir, "client.json"),
  PIXEL_GOOGLE_TOKEN_PATH: join(googleDir, "token.json"),
  PIXEL_OAUTH_REDIRECT_URI: "http://127.0.0.1:8765",
  PIXEL_SOURCE_BROKER_UNIT: "pixel-source-broker.service",
  PIXEL_SOURCE_BROKER_ENABLED: sourceBrokerEnabled ? "1" : "0",
  PIXEL_SOURCE_BROKER_TIMER: "pixel-source-broker.timer",
  PIXEL_SOURCE_ACTION_UNIT: "pixel-source-action@.service",
  PIXEL_SOURCE_RECONCILE_UNIT: "pixel-source-reconcile@.service",
  PIXEL_SOURCE_DIRECT_UNIT: "pixel-source-direct.service",
  PIXEL_SOURCE_DIRECT_PATH_UNIT: "pixel-source-direct.path",
  PIXEL_SOURCE_BROKER_SYSTEMD_DIR: "/etc/systemd/system",
  PIXEL_SOURCE_BROKER_ENV: "/etc/pixel-source-broker.env",
  PIXEL_SOURCE_BROKER_INSTALL_DIR: sourceBrokerInstallDir,
  PIXEL_SOURCE_BROKER_STATE_DIR: sourceBrokerStateDir,
  PIXEL_SOURCE_BROKER_USER: "pixel-source-broker",
  PIXEL_SOURCE_READER_USER: serviceUser,
  PIXEL_SOURCE_TOKEN_PATH: sourceTokenPath,
  PIXEL_SOURCE_PROJECTION_DIR: sourceProjectionDir,
  PIXEL_ACTION_PROPOSAL_DIR: actionProposalDir,
  PIXEL_ACTION_RESULT_DIR: actionResultDir,
  PIXEL_CALENDAR_DIRECT_ENABLED: calendarDirectEnabled ? "1" : "0",
  PIXEL_CALENDAR_DIRECT_MAX_PER_HOUR: "20",
  PIXEL_SOURCE_GMAIL_QUERY: "in:inbox",
  PIXEL_SOURCE_GMAIL_PAGE_SIZE: "100",
  PIXEL_SOURCE_GMAIL_MAX_PAGES: "1000",
  PIXEL_SOURCE_GMAIL_SENT_QUERY: "in:sent",
  PIXEL_SOURCE_GMAIL_SENT_PAGE_SIZE: "100",
  PIXEL_SOURCE_GMAIL_SENT_MAX_PAGES: "1000",
  PIXEL_SOURCE_CALENDAR_PAST_DAYS: "7",
  PIXEL_SOURCE_CALENDAR_FUTURE_DAYS: "90",
  PIXEL_SOURCE_STALE_AFTER_MS: String(3 * 60 * 1000),
  PIXEL_OPS_BROKER_ENABLED: operationsLimbEnabled ? "1" : "0",
  PIXEL_OPS_BROKER_UNIT: "pixel-ops-broker.service",
  PIXEL_OPS_BROKER_SYSTEMD_DIR: "/etc/systemd/system",
  PIXEL_OPS_BROKER_ENV: "/etc/pixel-ops-broker.env",
  PIXEL_OPS_BROKER_INSTALL_DIR: opsBrokerInstallDir,
  PIXEL_OPS_BROKER_STATE_DIR: opsBrokerStateDir,
  PIXEL_OPS_BROKER_USER: "pixel-ops-broker",
  PIXEL_OPS_BROKER_GROUP: "pixel-ops",
  PIXEL_OPS_READER_USER: serviceUser,
  PIXEL_OPS_POLICY_PATH: opsPolicyPath,
  PIXEL_OPS_STATE_DIR: opsBrokerStateDir,
  PIXEL_OPS_REQUEST_DIR: opsRequestDir,
  PIXEL_OPS_RESULT_DIR: opsResultDir,
  PIXEL_OPS_EVENT_DIR: opsEventDir,
  PIXEL_OPS_CANCEL_DIR: opsCancelDir,
  PIXEL_OPS_INVENTORY_PATH: join(opsBrokerStateDir, "inventory.json"),
  PIXEL_FRONTIER_BROKER_ENABLED: frontierLimbEnabled ? "1" : "0",
  PIXEL_FRONTIER_BROKER_UNIT: "pixel-frontier-broker.service",
  PIXEL_FRONTIER_BROKER_SYSTEMD_DIR: "/etc/systemd/system",
  PIXEL_FRONTIER_BROKER_ENV: "/etc/pixel-frontier-broker.env",
  PIXEL_FRONTIER_BROKER_INSTALL_DIR: frontierBrokerInstallDir,
  PIXEL_FRONTIER_BROKER_STATE_DIR: frontierBrokerStateDir,
  PIXEL_FRONTIER_BROKER_USER: "pixel-frontier-broker",
  PIXEL_FRONTIER_BROKER_GROUP: "pixel-frontier",
  PIXEL_FRONTIER_READER_USER: serviceUser,
  PIXEL_FRONTIER_AUTH_MODE: frontierAuthMode,
  PIXEL_FRONTIER_POLICY_PATH: frontierPolicyPath,
  PIXEL_FRONTIER_CREDENTIAL_SOURCE: frontierCredentialFile,
  PIXEL_FRONTIER_CREDENTIAL_PATH: frontierCredentialPath,
  PIXEL_FRONTIER_STATE_DIR: frontierBrokerStateDir,
  PIXEL_FRONTIER_REQUEST_DIR: frontierRequestDir,
  PIXEL_FRONTIER_RESULT_DIR: frontierResultDir,
  PIXEL_FRONTIER_EVENT_DIR: frontierEventDir,
  PIXEL_FRONTIER_CANCEL_DIR: frontierCancelDir,
  PIXEL_FRONTIER_FEEDBACK_DIR: frontierFeedbackDir,
  PIXEL_SKIP_ENDPOINT_CHECKS: "0",
  PIXEL_SKIP_NPM_CI: "0",
  APPLY_CONFIG: "0",
};

const envPath = join(root, ".env");
const generated = join(root, ".generated");
if (!force) {
  for (const path of [envPath, generated]) {
    try { await stat(path); throw new Error(`${path} exists; use --force to replace generated configuration`); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
  }
}
await mkdir(generated, { recursive: true, mode: 0o700 });
if (answersPath) {
  await mkdir(privateDeploymentDir, { recursive: true, mode: 0o700 });
  if (resolve(answersPath) !== resolve(privateOnboardingPath)) await cp(answersPath, privateOnboardingPath);
  await chmod(privateOnboardingPath, 0o600);
}
const envText = ["# Generated by ./pixel configure. This file is ignored by Git.", ...Object.entries(values).map(([key, value]) => `${key}=${shellQuote(value)}`), ""].join("\n");
await writeFile(envPath, envText, { mode: 0o600 });
await chmod(envPath, 0o600);

const gatewayValues = {
  OPENCLAW_STATE_DIR: openclawHome,
  OPENCLAW_CONFIG_PATH: join(openclawHome, "openclaw.json"),
  PIXEL_AGENT_ID: agentId,
  PIXEL_SOURCE_PROJECTION_DIR: sourceProjectionDir,
  PIXEL_ACTION_PROPOSAL_DIR: actionProposalDir,
  PIXEL_ACTION_RESULT_DIR: actionResultDir,
  PIXEL_SOURCE_STALE_AFTER_MS: values.PIXEL_SOURCE_STALE_AFTER_MS,
  PIXEL_CALENDAR_DIRECT_ENABLED: values.PIXEL_CALENDAR_DIRECT_ENABLED,
  PIXEL_LIMB_EMAIL_ENABLED: values.PIXEL_LIMB_EMAIL_ENABLED,
  PIXEL_LIMB_CALENDAR_ENABLED: values.PIXEL_LIMB_CALENDAR_ENABLED,
  PIXEL_LIMB_SOCIAL_ENABLED: values.PIXEL_LIMB_SOCIAL_ENABLED,
  PIXEL_LIMB_OPERATIONS_ENABLED: values.PIXEL_LIMB_OPERATIONS_ENABLED,
  PIXEL_LIMB_FRONTIER_ENABLED: values.PIXEL_LIMB_FRONTIER_ENABLED,
  PIXEL_OPS_STATE_DIR: opsBrokerStateDir,
  PIXEL_OPS_REQUEST_DIR: opsRequestDir,
  PIXEL_OPS_RESULT_DIR: opsResultDir,
  PIXEL_OPS_EVENT_DIR: opsEventDir,
  PIXEL_OPS_CANCEL_DIR: opsCancelDir,
  PIXEL_OPS_INVENTORY_PATH: join(opsBrokerStateDir, "inventory.json"),
  PIXEL_FRONTIER_STATE_DIR: frontierBrokerStateDir,
  PIXEL_FRONTIER_REQUEST_DIR: frontierRequestDir,
  PIXEL_FRONTIER_RESULT_DIR: frontierResultDir,
  PIXEL_FRONTIER_EVENT_DIR: frontierEventDir,
  PIXEL_FRONTIER_CANCEL_DIR: frontierCancelDir,
  PIXEL_FRONTIER_FEEDBACK_DIR: frontierFeedbackDir,
  SEARXNG_BASE_URL: searxngBaseUrl,
};
await writeFile(join(generated, "gateway.env"), `${Object.entries(gatewayValues).map(([key, value]) => `${key}=${shellQuote(value)}`).join("\n")}\n`, { mode: 0o600 });

const webCourierValues = {
  PIXEL_WEB_COURIER_WORKSPACES: workspace,
  PIXEL_WEB_COURIER_LOG_PATH: webCourierLog,
  PIXEL_WEB_COURIER_NAV_TIMEOUT_MS: values.PIXEL_WEB_COURIER_NAV_TIMEOUT_MS,
  PIXEL_WEB_COURIER_TEXT_CAP: values.PIXEL_WEB_COURIER_TEXT_CAP,
  PIXEL_WEB_COURIER_SCREENSHOT_CAP: values.PIXEL_WEB_COURIER_SCREENSHOT_CAP,
  PIXEL_WEB_COURIER_DOMAIN_INTERVAL: values.PIXEL_WEB_COURIER_DOMAIN_INTERVAL,
  PIXEL_WEB_COURIER_ALLOWED_PORTS: values.PIXEL_WEB_COURIER_ALLOWED_PORTS,
  PLAYWRIGHT_BROWSERS_PATH: webCourierBrowserPath,
  PYTHONDONTWRITEBYTECODE: "1",
};
await writeFile(join(generated, "web-courier.env"), `${Object.entries(webCourierValues).map(([key, value]) => `${key}=${shellQuote(value)}`).join("\n")}\n`, { mode: 0o600 });

const sourceBrokerValues = {
  PIXEL_SOURCE_TOKEN_PATH: sourceTokenPath,
  PIXEL_SOURCE_PROJECTION_DIR: sourceProjectionDir,
  PIXEL_ACTION_PROPOSAL_DIR: actionProposalDir,
  PIXEL_ACTION_RESULT_DIR: actionResultDir,
  PIXEL_SOURCE_CALENDAR_ID: calendarId,
  PIXEL_SOURCE_TIME_ZONE: timeZone,
  PIXEL_SOURCE_GMAIL_QUERY: values.PIXEL_SOURCE_GMAIL_QUERY,
  PIXEL_SOURCE_GMAIL_PAGE_SIZE: values.PIXEL_SOURCE_GMAIL_PAGE_SIZE,
  PIXEL_SOURCE_GMAIL_MAX_PAGES: values.PIXEL_SOURCE_GMAIL_MAX_PAGES,
  PIXEL_SOURCE_GMAIL_SENT_QUERY: values.PIXEL_SOURCE_GMAIL_SENT_QUERY,
  PIXEL_SOURCE_GMAIL_SENT_PAGE_SIZE: values.PIXEL_SOURCE_GMAIL_SENT_PAGE_SIZE,
  PIXEL_SOURCE_GMAIL_SENT_MAX_PAGES: values.PIXEL_SOURCE_GMAIL_SENT_MAX_PAGES,
  PIXEL_SOURCE_CALENDAR_PAST_DAYS: values.PIXEL_SOURCE_CALENDAR_PAST_DAYS,
  PIXEL_SOURCE_CALENDAR_FUTURE_DAYS: values.PIXEL_SOURCE_CALENDAR_FUTURE_DAYS,
  PIXEL_LIMB_EMAIL_ENABLED: values.PIXEL_LIMB_EMAIL_ENABLED,
  PIXEL_LIMB_CALENDAR_ENABLED: values.PIXEL_LIMB_CALENDAR_ENABLED,
  PIXEL_LIMB_SOCIAL_ENABLED: values.PIXEL_LIMB_SOCIAL_ENABLED,
  PIXEL_CALENDAR_DIRECT_ENABLED: values.PIXEL_CALENDAR_DIRECT_ENABLED,
  PIXEL_CALENDAR_DIRECT_MAX_PER_HOUR: values.PIXEL_CALENDAR_DIRECT_MAX_PER_HOUR,
  PYTHONDONTWRITEBYTECODE: "1",
};
await writeFile(join(generated, "source-broker.env"), `${Object.entries(sourceBrokerValues).map(([key, value]) => `${key}=${shellQuote(value)}`).join("\n")}\n`, { mode: 0o600 });

const opsBrokerValues = {
  PIXEL_OPS_POLICY_PATH: opsPolicyPath,
  PIXEL_OPS_STATE_DIR: opsBrokerStateDir,
  PYTHONDONTWRITEBYTECODE: "1",
};
await writeFile(join(generated, "ops-broker.env"), `${Object.entries(opsBrokerValues).map(([key, value]) => `${key}=${shellQuote(value)}`).join("\n")}\n`, { mode: 0o600 });
if (operationsPolicy) await writeFile(join(generated, "ops-policy.json"), `${JSON.stringify(operationsPolicy, null, 2)}\n`, { mode: 0o600 });

const frontierBrokerValues = {
  PIXEL_FRONTIER_POLICY_PATH: frontierPolicyPath,
  PIXEL_FRONTIER_STATE_DIR: frontierBrokerStateDir,
  PIXEL_FRONTIER_AUTH_MODE: frontierAuthMode,
  PIXEL_FRONTIER_CREDENTIAL_PATH: frontierCredentialPath,
  PIXEL_FRONTIER_FEEDBACK_DIR: frontierFeedbackDir,
  PYTHONDONTWRITEBYTECODE: "1",
};
await writeFile(join(generated, "frontier-broker.env"), `${Object.entries(frontierBrokerValues).map(([key, value]) => `${key}=${shellQuote(value)}`).join("\n")}\n`, { mode: 0o600 });
await writeFile(join(generated, "frontier-policy.json"), `${JSON.stringify(frontierPolicy, null, 2)}\n`, { mode: 0o600 });

const systemdDirectory = join(home, ".config", "pixel-agent");
const systemdQuote = (value) => `"${String(value).replace(/%/g, "%%").replace(/([\\"])/g, "\\$1")}"`;
const systemdPath = (value) => String(value).replace(/%/g, "%%").replace(/\\/g, "\\x5c").replace(/ /g, "\\x20").replace(/\t/g, "\\x09");
const servicePath = [...new Set([dirname(openclawBin), dirname(process.execPath), "/usr/local/bin", "/usr/bin", "/bin"])].join(":");
const resolvedOpenclawBin = await realpath(openclawBin);
// ProtectHome=tmpfs hides every home directory. Bind only the runtime prefixes
// required to execute Node/OpenClaw, including a runtime installed beside a
// deployment-specific HOME and an npm launcher whose target is elsewhere.
const gatewayReadOnlyHomeRoots = [...new Set([
  protectedHomeRuntimeMountRoot(home, openclawBin),
  protectedHomeRuntimeMountRoot(home, resolvedOpenclawBin),
  protectedHomeRuntimeMountRoot(home, process.execPath),
].filter(Boolean))];
const gatewayReadOnlyPaths = [...new Set([...gatewayReadOnlyHomeRoots, installDir, ...gatewayExtensions.map(({ path }) => path).filter(Boolean)])].map(systemdPath).join(" ");
const gatewayBindPaths = [...new Set([openclawHome, workspace, embeddingCache])].map(systemdPath).join(" ");
const gatewayWritableSystemPaths = [
  ...(sourceBrokerEnabled ? [actionProposalDir] : []),
  ...(operationsLimbEnabled ? [opsRequestDir, opsCancelDir] : []),
  ...(frontierLimbEnabled ? [frontierRequestDir, frontierCancelDir, frontierFeedbackDir] : []),
].map(systemdPath).join(" ");
const service = `[Unit]
Description=OpenClaw Gateway - ${agentName}
After=network-online.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
User=${serviceUser}
ExecStart=${systemdQuote(openclawBin)} gateway --bind loopback --auth token --port ${gatewayPort}
Restart=always
RestartSec=5
RestartPreventExitStatus=78
TimeoutStopSec=30
TimeoutStartSec=30
SuccessExitStatus=0 143
OOMPolicy=stop
KillMode=control-group
Environment=HOME=${systemdQuote(home)}
Environment=TMPDIR=/tmp
Environment=PATH=${systemdQuote(servicePath)}
EnvironmentFile=${systemdPath(join(systemdDirectory, "gateway.env"))}
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=tmpfs
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectProc=invisible
ProcSubset=pid
ProtectSystem=strict
BindReadOnlyPaths=${gatewayReadOnlyPaths}
BindPaths=${gatewayBindPaths}
ReadOnlyPaths=${systemdPath(join(openclawHome, "npm"))}
${gatewayWritableSystemPaths ? `ReadWritePaths=${gatewayWritableSystemPaths}` : ""}
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
SystemCallArchitectures=native
UMask=0077
MemoryMax=4G
TasksMax=2048

[Install]
WantedBy=default.target
`;
await writeFile(join(generated, "openclaw-gateway.service"), service, { mode: 0o600 });

const webCourierService = `[Unit]
Description=Pixel Web Courier - policy-enforced browser bridge
After=network-online.target
Wants=network-online.target
StartLimitBurst=5
StartLimitIntervalSec=60

[Service]
Type=simple
User=${serviceUser}
ExecStart=${systemdQuote(join(installDir, "current", "web-courier", ".venv", "bin", "python"))} ${systemdQuote(join(installDir, "current", "web-courier", "courier.py"))}
WorkingDirectory=${systemdPath(workspace)}
Restart=always
RestartSec=5
TimeoutStopSec=30
TimeoutStartSec=90
KillMode=control-group
OOMPolicy=stop
Environment=HOME=${systemdQuote(home)}
Environment=TMPDIR=/tmp
Environment=XDG_CACHE_HOME=/tmp/pixel-web-courier-cache
Environment=PATH=${systemdQuote(servicePath)}
EnvironmentFile=${systemdPath(join(systemdDirectory, "web-courier.env"))}
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=tmpfs
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectProc=invisible
ProcSubset=pid
ProtectSystem=strict
BindReadOnlyPaths=${systemdPath(installDir)}
BindPaths=${systemdPath(workspace)} ${systemdPath(dirname(webCourierLog))}
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
MemoryMax=1G
TasksMax=512

[Install]
WantedBy=multi-user.target
`;
await writeFile(join(generated, "pixel-web-courier.service"), webCourierService, { mode: 0o600 });

const sourceBrokerService = `[Unit]
Description=Pixel Source Broker - isolated Gmail and Calendar ingestion
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${values.PIXEL_SOURCE_BROKER_USER}
Group=${values.PIXEL_SOURCE_BROKER_USER}
ExecStart=${systemdQuote(join(sourceBrokerInstallDir, "broker.py"))}
EnvironmentFile=${systemdPath(values.PIXEL_SOURCE_BROKER_ENV)}
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectProc=invisible
ProcSubset=pid
ProtectSystem=strict
ReadOnlyPaths=${systemdPath(sourceBrokerInstallDir)} ${systemdPath(join(sourceBrokerStateDir, "private"))}
ReadWritePaths=${systemdPath(sourceProjectionDir)}
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0027
MemoryMax=512M
TasksMax=128

[Install]
WantedBy=multi-user.target
`;
await writeFile(join(generated, "pixel-source-broker.service"), sourceBrokerService, { mode: 0o600 });

const sourceBrokerTimer = `[Unit]
Description=Refresh Pixel source projections

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
RandomizedDelaySec=5s
Persistent=true
Unit=${values.PIXEL_SOURCE_BROKER_UNIT}

[Install]
WantedBy=timers.target
`;
await writeFile(join(generated, "pixel-source-broker.timer"), sourceBrokerTimer, { mode: 0o600 });

const sourceActionService = `[Unit]
Description=Pixel Calendar actuator for approved proposal %i
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${values.PIXEL_SOURCE_BROKER_USER}
Group=${values.PIXEL_SOURCE_BROKER_USER}
ExecStart=${systemdQuote(join(sourceBrokerInstallDir, "broker.py"))} --approve %i
EnvironmentFile=${systemdPath(values.PIXEL_SOURCE_BROKER_ENV)}
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectSystem=strict
ReadOnlyPaths=${systemdPath(sourceBrokerInstallDir)} ${systemdPath(join(sourceBrokerStateDir, "private"))} ${systemdPath(actionProposalDir)}
ReadWritePaths=${systemdPath(actionResultDir)}
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0027
MemoryMax=256M
TasksMax=64
`;
await writeFile(join(generated, "pixel-source-action@.service"), sourceActionService, { mode: 0o600 });

const sourceReconcileService = sourceActionService
  .replace("Pixel Calendar actuator for approved proposal %i", "Pixel Calendar fail-closed reconciliation for proposal %i")
  .replace(" --approve %i", " --reconcile %i");
await writeFile(join(generated, "pixel-source-reconcile@.service"), sourceReconcileService, { mode: 0o600 });

const sourceDirectService = `[Unit]
Description=Pixel bounded direct Calendar actuator queue
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${values.PIXEL_SOURCE_BROKER_USER}
Group=${values.PIXEL_SOURCE_BROKER_USER}
ExecStart=${systemdQuote(join(sourceBrokerInstallDir, "broker.py"))} --drain-direct
EnvironmentFile=${systemdPath(values.PIXEL_SOURCE_BROKER_ENV)}
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectSystem=strict
ReadOnlyPaths=${systemdPath(sourceBrokerInstallDir)} ${systemdPath(join(sourceBrokerStateDir, "private"))} ${systemdPath(actionProposalDir)}
ReadWritePaths=${systemdPath(actionResultDir)}
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0077
MemoryMax=256M
TasksMax=64

[Install]
WantedBy=multi-user.target
`;
await writeFile(join(generated, "pixel-source-direct.service"), sourceDirectService, { mode: 0o600 });

const sourceDirectPath = `[Unit]
Description=Watch for Pixel bounded direct Calendar proposals

[Path]
PathChanged=${systemdPath(actionProposalDir)}
Unit=${values.PIXEL_SOURCE_DIRECT_UNIT}

[Install]
WantedBy=multi-user.target
`;
await writeFile(join(generated, "pixel-source-direct.path"), sourceDirectPath, { mode: 0o600 });

const localWritableRoots = operationsPolicy ? Object.values(operationsPolicy.targets ?? {}).filter((target) => target?.enabled !== false && target?.backend === "local").flatMap((target) => Array.isArray(target.writableRoots) ? target.writableRoots : []) : [];
const opsArtifactRoot = operationsPolicy?.download?.stagingRoot ?? join(opsBrokerStateDir, "artifacts");
for (const path of [...localWritableRoots, opsArtifactRoot]) absolute(path, "operations policy writableRoot");
const opsReadWritePaths = [opsBrokerStateDir, ...new Set([...localWritableRoots, opsArtifactRoot])].map(systemdPath).join(" ");
const opsBrokerService = `[Unit]
Description=Pixel Operations Broker - isolated fleet execution and workflow service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${values.PIXEL_OPS_BROKER_USER}
Group=${values.PIXEL_OPS_BROKER_GROUP}
ExecStart=${systemdQuote(join(opsBrokerInstallDir, "broker.py"))}
WorkingDirectory=${systemdPath(opsBrokerStateDir)}
EnvironmentFile=${systemdPath(values.PIXEL_OPS_BROKER_ENV)}
Restart=always
RestartSec=2
TimeoutStopSec=30
KillMode=control-group
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectSystem=strict
ReadOnlyPaths=${systemdPath(opsBrokerInstallDir)} ${systemdPath(opsPolicyPath)}
ReadWritePaths=${opsReadWritePaths}
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0027
MemoryMax=2G
TasksMax=1024

[Install]
WantedBy=multi-user.target
`;
await writeFile(join(generated, "pixel-ops-broker.service"), opsBrokerService, { mode: 0o600 });

const frontierCredentialReadOnly = frontierAuthMode === "api-key" ? ` ${systemdPath(frontierCredentialPath)}` : "";
const frontierBrokerService = `[Unit]
Description=Pixel Frontier Broker - privacy-compiled expert review service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${values.PIXEL_FRONTIER_BROKER_USER}
Group=${values.PIXEL_FRONTIER_BROKER_GROUP}
ExecStartPre=${systemdQuote(join(frontierBrokerInstallDir, "verify-codex.py"))} ${systemdQuote(frontierCodexBinary)}
ExecStart=${systemdQuote(join(frontierBrokerInstallDir, "broker.py"))} --policy ${systemdQuote(frontierPolicyPath)} --state ${systemdQuote(frontierBrokerStateDir)}
WorkingDirectory=${systemdPath(frontierBrokerStateDir)}
EnvironmentFile=${systemdPath(values.PIXEL_FRONTIER_BROKER_ENV)}
Restart=always
RestartSec=2
TimeoutStopSec=30
KillMode=control-group
NoNewPrivileges=true
PrivateDevices=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectProc=invisible
ProcSubset=pid
ProtectSystem=strict
InaccessiblePaths=-/srv -/mnt -/media
ReadOnlyPaths=${systemdPath(frontierBrokerInstallDir)} ${systemdPath(frontierPolicyPath)}${frontierCredentialReadOnly}
ReadWritePaths=${systemdPath(frontierBrokerStateDir)}
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
UMask=0027
MemoryMax=2G
TasksMax=512

[Install]
WantedBy=multi-user.target
`;
await writeFile(join(generated, "pixel-frontier-broker.service"), frontierBrokerService, { mode: 0o600 });

const generatedWorkspace = join(generated, "workspace");
await cp(join(root, "workspace-template"), generatedWorkspace, { recursive: true, force: true });
const replacements = new Map([
  ["{{OWNER_NAME}}", ownerName], ["{{ORGANIZATION}}", organization],
  ["{{TIME_ZONE}}", timeZone], ["{{DEPLOYMENT_NAME}}", deploymentName],
]);
async function customize(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) { await chmod(path, 0o700); await customize(path); continue; }
    let body = await readFile(path, "utf8");
    for (const [token, value] of replacements) body = body.replaceAll(token, value);
    await writeFile(path, body, { mode: path.includes(`${join("workspace", "scripts")}`) ? 0o700 : 0o600 });
  }
}
await customize(generatedWorkspace);

const limbs = { email: emailLimbEnabled, calendar: calendarLimbEnabled, social: socialLimbEnabled, web: webLimbEnabled, operations: operationsLimbEnabled, frontier: frontierLimbEnabled };
const deploymentRecord = { version, deploymentProfile, capabilityProfile, ownerName, organization, deploymentName, timeZone, agentId, agentName, openclawBin, openclawHome, installDir, workspace, modelProvider, modelId, modelName, modelBaseUrl, modelPrivateHosts, modelReasoning, modelContextWindow, modelMaxTokens, webSearchProvider, searxngBaseUrl, embeddingModel, embeddingCache, googleAccount, calendarId, gatewayPort, gatewayExtensions, agentSkills, localCapabilityPacks, signedOperationsActionPacks, frontierTaskPacks, limbs, releaseOperator: { enabled: releaseOperatorEnabled }, sourceBroker: { enabled: sourceBrokerEnabled, systemUser: values.PIXEL_SOURCE_BROKER_USER, projectionDir: sourceProjectionDir, rawContentStored: false }, operationsBroker: { enabled: operationsLimbEnabled, systemUser: values.PIXEL_OPS_BROKER_USER, policyPath: opsPolicyPath, credentialsVisibleToGateway: false }, frontierBroker: { enabled: frontierLimbEnabled, systemUser: values.PIXEL_FRONTIER_BROKER_USER, policyPath: frontierPolicyPath, authMode: frontierAuthMode, budgetProfile: frontierBudgetProfile, credentialVisibleToGateway: false, primaryModelPrivate, primaryModelOperatorAttested }, calendarMutationPolicy: { boundedDirectEnabled: calendarDirectEnabled, directShapes: ["private-create-without-attendees", "etag-bound-time-only-update"], separateApprovalForConsequentialChanges: true }, messagingToolsDenied: true, sessionVisibility: "tree", gatewayRunsAsHardenedSystemService: true };
let generatedAt = new Date().toISOString();
try {
  const previous = JSON.parse(await readFile(join(generated, "deployment.json"), "utf8"));
  if (previous && typeof previous === "object" && !Array.isArray(previous)) {
    const { generatedAt: previousGeneratedAt, ...previousRecord } = previous;
    const parsed = typeof previousGeneratedAt === "string" ? Date.parse(previousGeneratedAt) : NaN;
    if (Number.isFinite(parsed) && new Date(parsed).toISOString() === previousGeneratedAt && JSON.stringify(previousRecord) === JSON.stringify(deploymentRecord)) {
      generatedAt = previousGeneratedAt;
    }
  }
} catch (error) {
  if (error?.code !== "ENOENT" && !(error instanceof SyntaxError)) throw error;
}
const { version: deploymentVersion, ...deploymentRest } = deploymentRecord;
const record = { version: deploymentVersion, generatedAt, ...deploymentRest };
await writeFile(join(generated, "deployment.json"), `${JSON.stringify(record, null, 2)}\n`, { mode: 0o600 });
console.log(`Generated ${basename(envPath)}, modular limb services, and a customized workspace for ${agentName}.`);
console.log("No OAuth token, Google client secret, session, or personal memory was written to Git-tracked paths.");
