import { chmod, readFile, rename, writeFile } from "node:fs/promises";
import { isAbsolute, resolve } from "node:path";

const [configArgument, pluginArgument, legacyArgument] = process.argv.slice(2);
if (!configArgument || !pluginArgument) {
  throw new Error("Usage: migrate-source-broker-config.mjs OPENCLAW_CONFIG NEW_PLUGIN_PATH [LEGACY_PLUGIN_PATH]");
}
const configPath = resolve(configArgument);
const pluginPath = resolve(pluginArgument);
const legacyPath = legacyArgument ? resolve(legacyArgument) : null;
if (!isAbsolute(configArgument) || !isAbsolute(pluginArgument) || (legacyArgument && !isAbsolute(legacyArgument))) {
  throw new Error("all migration paths must be absolute");
}

const config = JSON.parse(await readFile(configPath, "utf8"));
config.plugins ??= {};
config.plugins.entries ??= {};
const legacyPluginIds = new Set(["pixel-google-workspace", "pixel-gmail-readonly"]);
const sourceTools = [
  "pixel_limb_status",
  "pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search", "pixel_gmail_read", "pixel_gmail_thread",
  "pixel_calendar_list", "pixel_calendar_get", "pixel_calendar_propose_create",
  "pixel_calendar_propose_update", "pixel_calendar_propose_delete",
  "pixel_social_feed", "pixel_social_search",
];
const removedSourceTools = new Set(["pixel_calendar_create", "pixel_calendar_update", "pixel_calendar_delete"]);
for (const legacyId of legacyPluginIds) delete config.plugins.entries[legacyId];
config.plugins.entries["pixel-source-broker"] = { enabled: true };
config.plugins.allow = [
  ...new Set((config.plugins.allow ?? []).filter((item) => !legacyPluginIds.has(item)).concat("pixel-source-broker")),
];
config.plugins.load ??= {};
config.plugins.load.paths = [
  ...new Set((config.plugins.load.paths ?? []).filter((item) => !legacyPath || resolve(item) !== legacyPath).concat(pluginPath)),
];
if (Array.isArray(config.tools?.sandbox?.tools?.allow)) {
  config.tools.sandbox.tools.allow = [
    ...new Set(config.tools.sandbox.tools.allow.filter((name) => !removedSourceTools.has(name)).concat(sourceTools)),
  ];
}
for (const agent of config.agents?.list ?? []) {
  if (agent.id === "pixel") continue;
  agent.tools ??= {};
  agent.tools.deny = [
    ...new Set((agent.tools.deny ?? []).filter((name) => !removedSourceTools.has(name)).concat(sourceTools)),
  ];
}

const temporary = `${configPath}.source-broker.${process.pid}.tmp`;
await writeFile(temporary, `${JSON.stringify(config, null, 2)}\n`, { mode: 0o600, flag: "wx" });
await chmod(temporary, 0o600);
await rename(temporary, configPath);
console.log(configPath);
