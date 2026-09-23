import { readFile, readdir, mkdir, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";

const required = (name) => { const value = process.env[name]; if (!value) throw new Error(`${name} is required`); return value; };
const number = (name) => Number(required(name));
const home = resolve(required("OPENCLAW_HOME"));
const sourcePath = join(home, "openclaw.json");
const outputPath = resolve(process.argv[2] ?? join(dirname(fileURLToPath(import.meta.url)), "..", "dist", "openclaw.json"));
let current = {};
try { current = JSON.parse(await readFile(sourcePath, "utf8")); } catch (error) { if (error.code !== "ENOENT") throw error; }

const provider = required("PIXEL_MODEL_PROVIDER");
const modelId = required("PIXEL_MODEL_ID");
const gatewayToken = required("PIXEL_GATEWAY_TOKEN");
let agentSkills;
try { agentSkills = JSON.parse(process.env.PIXEL_AGENT_SKILLS ?? "[]"); }
catch { throw new Error("PIXEL_AGENT_SKILLS must be valid JSON"); }
if (!Array.isArray(agentSkills) || agentSkills.some((name) => typeof name !== "string" || !/^[a-z0-9][a-z0-9._-]{0,127}$/.test(name)) || new Set(agentSkills).size !== agentSkills.length) {
  throw new Error("PIXEL_AGENT_SKILLS must be a unique array of installed skill names");
}
let agentToolAllowlist = null;
if (process.env.PIXEL_AGENT_TOOL_ALLOWLIST !== undefined) {
  try { agentToolAllowlist = JSON.parse(process.env.PIXEL_AGENT_TOOL_ALLOWLIST); }
  catch { throw new Error("PIXEL_AGENT_TOOL_ALLOWLIST must be valid JSON"); }
  if (
    !Array.isArray(agentToolAllowlist) || agentToolAllowlist.length < 1 || agentToolAllowlist.length > 256
    || agentToolAllowlist.some((name) => typeof name !== "string" || !/^[a-z][a-z0-9_]{1,63}$/u.test(name))
    || new Set(agentToolAllowlist).size !== agentToolAllowlist.length
    || JSON.stringify(agentToolAllowlist) !== JSON.stringify([...agentToolAllowlist].sort())
  ) throw new Error("PIXEL_AGENT_TOOL_ALLOWLIST must be a sorted unique bounded tool list");
}
let gatewayExtensions;
try { gatewayExtensions = JSON.parse(process.env.PIXEL_GATEWAY_EXTENSIONS ?? "[]"); }
catch { throw new Error("PIXEL_GATEWAY_EXTENSIONS must be valid JSON"); }
if (!Array.isArray(gatewayExtensions) || gatewayExtensions.some((extension) =>
  typeof extension !== "object" || extension === null || Array.isArray(extension) ||
  typeof extension.id !== "string" || !/^[a-z][a-z0-9-]{1,62}$/.test(extension.id) ||
  (extension.path !== undefined && (!extension.path.startsWith("/") || resolve(extension.path) === "/" || !/^[0-9a-f]{64}$/.test(extension.sha256 ?? ""))) ||
  (extension.path === undefined && (extension.sha256 !== undefined || extension.tools !== undefined)) ||
  (extension.tools !== undefined && (!Array.isArray(extension.tools) || extension.tools.length < 1 || extension.tools.length > 24 || extension.tools.some((name) => typeof name !== "string" || !/^pixel_[a-z0-9_]{2,63}$/.test(name))))
)) throw new Error("PIXEL_GATEWAY_EXTENSIONS must contain {id} or {id, absolute path, sha256, optional tools} entries");
if (new Set(gatewayExtensions.map(({ id }) => id)).size !== gatewayExtensions.length) throw new Error("PIXEL_GATEWAY_EXTENSIONS contains a duplicate plugin ID");
const extensionTools = gatewayExtensions.flatMap(({ tools = [] }) => tools);
if (new Set(extensionTools).size !== extensionTools.length) throw new Error("PIXEL_GATEWAY_EXTENSIONS contains a duplicate custom tool name");
if (extensionTools.some((name) => name === "pixel_limb_status" || /^pixel_(?:gmail|calendar|social|web|ops|frontier)_/.test(name))) {
  throw new Error("PIXEL_GATEWAY_EXTENSIONS cannot override a Pixel-managed tool namespace");
}
const pinnedGatewayExtensionIds = new Set(["discord"]);
if (gatewayExtensions.some(({ id, path }) => path === undefined && !pinnedGatewayExtensionIds.has(id))) {
  throw new Error("ID-only gateway extensions must be in Pixel's pinned extension catalog");
}
const extensionIds = gatewayExtensions.map(({ id }) => id);
const extensionPaths = [];
async function digestTree(root) {
  const files = [];
  async function walk(directory, relative = "") {
    for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const childRelative = relative ? `${relative}/${entry.name}` : entry.name;
      const child = join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error(`Gateway extension contains a symbolic link: ${childRelative}`);
      if (entry.isDirectory()) await walk(child, childRelative);
      else if (entry.isFile()) files.push([childRelative, child]);
      else throw new Error(`Gateway extension contains a non-regular file: ${childRelative}`);
    }
  }
  await walk(root);
  const hash = createHash("sha256");
  for (const [relative, path] of files) {
    const content = await readFile(path);
    hash.update(relative); hash.update("\0"); hash.update(String(content.length)); hash.update("\0"); hash.update(content); hash.update("\0");
  }
  return hash.digest("hex");
}
for (const extension of gatewayExtensions) {
  if (!extension.path) continue;
  let manifest;
  try { manifest = JSON.parse(await readFile(join(extension.path, "openclaw.plugin.json"), "utf8")); }
  catch (error) { throw new Error(`Cannot verify gateway extension ${extension.id}: ${error.message}`); }
  if (manifest.id !== extension.id) throw new Error(`Gateway extension path for ${extension.id} declares ${manifest.id ?? "no plugin ID"}`);
  if (extension.tools !== undefined) {
    const declared = manifest.contracts?.tools;
    if (!Array.isArray(declared) || new Set(declared).size !== declared.length || JSON.stringify([...declared].sort()) !== JSON.stringify([...extension.tools].sort())) {
      throw new Error(`Gateway extension ${extension.id} tool allowlist differs from its plugin manifest contract`);
    }
  }
  const observedDigest = await digestTree(extension.path);
  if (observedDigest !== extension.sha256) throw new Error(`Gateway extension ${extension.id} differs from its approved SHA-256 tree digest`);
  extensionPaths.push(extension.path);
}
const webSearchProvider = process.env.PIXEL_WEB_SEARCH_PROVIDER ?? "searxng";
if (!["searxng", "parallel-free"].includes(webSearchProvider)) {
  throw new Error("PIXEL_WEB_SEARCH_PROVIDER must be searxng or parallel-free");
}
if (webSearchProvider === "parallel-free") {
  const parallelExt = gatewayExtensions.find(({ id }) => id === "parallel");
  if (!parallelExt || !parallelExt.path || !parallelExt.path.startsWith("/")) {
    throw new Error("PIXEL_WEB_SEARCH_PROVIDER=parallel-free requires a parallel gateway extension with an absolute path");
  }
}
const configuredModelKey = required("PIXEL_MODEL_API_KEY");
const modelApiKey = configuredModelKey === "preserve-existing" ? current.models?.providers?.[provider]?.apiKey : configuredModelKey;
if (!modelApiKey) throw new Error("PIXEL_MODEL_API_KEY=preserve-existing requires an existing key for the configured provider");
const existingTimeout = current.models?.providers?.[provider]?.timeoutSeconds;
const providerCompatibility = Number.isInteger(existingTimeout) && existingTimeout >= 1 && existingTimeout <= 600
  ? { timeoutSeconds: existingTimeout } : {};
const httpEndpoints = {};
for (const name of ["chatCompletions", "responses"]) {
  const enabledValue = current.gateway?.http?.endpoints?.[name]?.enabled;
  if (typeof enabledValue === "boolean") httpEndpoints[name] = { enabled: enabledValue };
}
const pluginId = "pixel-source-broker";
const opsPluginId = "pixel-operations-broker";
const frontierPluginId = "pixel-frontier-broker";
const allSourceTools = [
  "pixel_limb_status",
  "pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search", "pixel_gmail_read", "pixel_gmail_thread",
  "pixel_calendar_list", "pixel_calendar_get", "pixel_calendar_propose_create",
  "pixel_calendar_propose_update", "pixel_calendar_propose_delete",
  "pixel_social_feed", "pixel_social_search", "pixel_web_browse",
];
const emailTools = allSourceTools.filter((name) => name.startsWith("pixel_gmail_"));
const calendarTools = allSourceTools.filter((name) => name.startsWith("pixel_calendar_"));
const socialTools = allSourceTools.filter((name) => name.startsWith("pixel_social_"));
const webTools = allSourceTools.filter((name) => name.startsWith("pixel_web_"));
const sourceStatusTools = ["pixel_limb_status"];
const enabled = (name, fallback) => !["0", "false", "off", "no"].includes(String(process.env[name] ?? fallback).toLowerCase());
const emailEnabled = enabled("PIXEL_LIMB_EMAIL_ENABLED", "1");
const calendarEnabled = enabled("PIXEL_LIMB_CALENDAR_ENABLED", "1");
const socialEnabled = enabled("PIXEL_LIMB_SOCIAL_ENABLED", "0");
const webEnabled = enabled("PIXEL_LIMB_WEB_ENABLED", "1");
const operationsEnabled = enabled("PIXEL_LIMB_OPERATIONS_ENABLED", "0");
const frontierEnabled = enabled("PIXEL_LIMB_FRONTIER_ENABLED", "0");
const sourcePluginEnabled = emailEnabled || calendarEnabled || socialEnabled || webEnabled;
const sourceTools = [...(sourcePluginEnabled ? sourceStatusTools : []), ...(emailEnabled ? emailTools : []), ...(calendarEnabled ? calendarTools : []), ...(socialEnabled ? socialTools : []), ...(webEnabled ? webTools : [])];
const opsTools = [
  "pixel_ops_inventory", "pixel_ops_run", "pixel_ops_workflow_submit", "pixel_ops_download_stage",
  "pixel_ops_artifact_transfer", "pixel_ops_shell_propose", "pixel_ops_job_get", "pixel_ops_job_wait", "pixel_ops_job_events", "pixel_ops_job_cancel",
];
const frontierTools = [
  "pixel_frontier_plan_review", "pixel_frontier_failure_triage", "pixel_frontier_job_get",
  "pixel_frontier_job_wait", "pixel_frontier_job_events", "pixel_frontier_job_cancel", "pixel_frontier_usage",
  "pixel_frontier_finalize",
];
const activeTools = [...sourceTools, ...extensionTools, ...(operationsEnabled ? opsTools : []), ...(frontierEnabled ? frontierTools : [])];
const inactiveTools = [...allSourceTools.filter((name) => !sourceTools.includes(name)), ...(operationsEnabled ? [] : opsTools), ...(frontierEnabled ? [] : frontierTools), ...(webEnabled ? [] : ["web_fetch", "web_search"] )];
const sandboxTools = [
  "exec", "process", "read", "write", "edit", "apply_patch", "image",
  "debug", "eval", "glob", "grep", "hub", "lsp", "task", "todo", "yield",
  "sessions_list", "sessions_history", "sessions_send", "sessions_spawn", "sessions_yield", "subagents", "session_status",
  "memory_search", "memory_get",
  ...(webEnabled ? ["web_search", "web_fetch"] : []),
  ...activeTools,
];
const availableToolSet = new Set(sandboxTools);
if (agentToolAllowlist !== null && agentToolAllowlist.some((name) => !availableToolSet.has(name))) {
  throw new Error("PIXEL_AGENT_TOOL_ALLOWLIST requests a tool outside the rendered Pixel capability surface");
}
const leasedTools = agentToolAllowlist ?? sandboxTools;
const leaseDeniedTools = sandboxTools.filter((name) => !leasedTools.includes(name));
const treeSessionTools = ["sessions_list", "sessions_history", "sessions_send"];
const requiredPlugins = [...extensionIds, ...(webSearchProvider === "searxng" ? ["searxng"] : []), "llama-cpp", ...(sourcePluginEnabled ? [pluginId] : []), ...(operationsEnabled ? [opsPluginId] : []), ...(frontierEnabled ? [frontierPluginId] : [])];
const pluginPath = process.env.PIXEL_PLUGIN_PATH ?? resolve(join(dirname(fileURLToPath(import.meta.url)), "..", "plugin"));
const opsPluginPath = process.env.PIXEL_OPS_PLUGIN_PATH ?? resolve(join(dirname(fileURLToPath(import.meta.url)), "..", "plugin-ops"));
const frontierPluginPath = process.env.PIXEL_FRONTIER_PLUGIN_PATH ?? resolve(join(dirname(fileURLToPath(import.meta.url)), "..", "plugin-frontier"));
const existingAgents = current.agents?.list ?? [];
const existingPixel = existingAgents.find((agent) => agent.id === required("PIXEL_AGENT_ID")) ?? {};
const pixel = {
  id: required("PIXEL_AGENT_ID"), name: required("PIXEL_AGENT_NAME"),
  workspace: required("PIXEL_WORKSPACE"), model: `${provider}/${modelId}`,
  heartbeat: { every: "0m" }, skills: agentSkills, skillsLimits: { maxSkillsPromptChars: 16000 }, tools: {
    deny: [...new Set([...(existingPixel.tools?.deny ?? []).filter((name) => !activeTools.includes(name) && !treeSessionTools.includes(name)), "discord", "message", ...inactiveTools, ...leaseDeniedTools])],
  },
};
const existingEntries = current.plugins?.entries ?? {};
const extensionEntries = Object.fromEntries(gatewayExtensions.map(({ id }) => [id, { ...(existingEntries[id] ?? {}), enabled: true }]));
const extensionChannels = Object.fromEntries(extensionIds.filter((id) => current.channels?.[id] !== undefined).map((id) => [id, current.channels[id]]));
const config = {
  channels: extensionChannels,
  messages: { suppressToolErrors: current.messages?.suppressToolErrors === true },
  session: { dmScope: "per-account-channel-peer" },
  gateway: { mode: "local", bind: "loopback", auth: { token: gatewayToken }, http: { endpoints: httpEndpoints } },
  models: {
    mode: "merge",
    providers: {
      [provider]: {
        ...providerCompatibility,
        baseUrl: required("PIXEL_MODEL_BASE_URL"), apiKey: modelApiKey, api: "openai-completions",
        models: [{ id: modelId, name: required("PIXEL_MODEL_NAME"), reasoning: enabled("PIXEL_MODEL_REASONING", "1"), input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: number("PIXEL_MODEL_CONTEXT_WINDOW"), maxTokens: number("PIXEL_MODEL_MAX_TOKENS") }],
      },
    },
  },
  agents: {
    defaults: {
      sandbox: {
        mode: "all", scope: "agent", workspaceAccess: "rw",
        docker: {
          image: required("PIXEL_SANDBOX_IMAGE"), containerPrefix: "pixel-sbx-", workdir: "/workspace",
          network: "none", user: "sandbox", readOnlyRoot: true,
          tmpfs: ["/tmp:rw,nosuid,nodev,size=256m", "/var/tmp:rw,nosuid,nodev,size=128m", "/run:rw,nosuid,nodev,size=64m"],
          capDrop: ["ALL"], env: { LANG: "C.UTF-8" }, pidsLimit: 1024, memory: "8g", memorySwap: "8g",
          ulimits: { nofile: { soft: 4096, hard: 4096 }, nproc: { soft: 1024, hard: 1024 } },
        },
      },
      memorySearch: { enabled: true, provider: "local", model: required("PIXEL_EMBEDDING_MODEL"), local: { modelCacheDir: required("PIXEL_EMBEDDING_CACHE") }, extraPaths: ["daily-notes", "context", "library", "reflections"] },
      bootstrapMaxChars: 32000, bootstrapTotalMaxChars: 96000,
    },
    list: [pixel],
  },
  tools: {
    profile: "coding",
    alsoAllow: [...new Set(activeTools)],
    sandbox: { tools: { allow: [...leasedTools] } }, sessions: { visibility: "tree" }, fs: { workspaceOnly: true },
    web: { search: { provider: webSearchProvider } },
  },
  plugins: {
    allow: [...new Set(requiredPlugins)],
    entries: {
      ...extensionEntries,
      ...(webSearchProvider === "searxng" ? { searxng: { ...(current.plugins?.entries?.searxng ?? {}), enabled: true, config: { webSearch: { baseUrl: required("PIXEL_SEARXNG_BASE_URL") } } } } : {}),
      "llama-cpp": { ...(current.plugins?.entries?.["llama-cpp"] ?? {}), enabled: true },
      ...(sourcePluginEnabled ? { [pluginId]: { enabled: true } } : {}),
      ...(operationsEnabled ? { [opsPluginId]: { enabled: true } } : {}),
      ...(frontierEnabled ? { [frontierPluginId]: { enabled: true } } : {}),
    },
    load: { paths: [...new Set([...extensionPaths, ...(sourcePluginEnabled ? [pluginPath] : []), ...(operationsEnabled ? [opsPluginPath] : []), ...(frontierEnabled ? [frontierPluginPath] : [])])] },
  },
};
await mkdir(dirname(outputPath), { recursive: true });
await writeFile(outputPath, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600 });
console.log(outputPath);
