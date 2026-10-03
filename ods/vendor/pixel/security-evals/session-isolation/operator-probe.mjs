#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";


function fail(message) {
  process.stderr.write(`session operator probe failed: ${message}\n`);
  process.exit(1);
}


const [sourceKey, attackerKey] = process.argv.slice(2);
const safeKey = /^agent:[a-z][a-z0-9-]{1,62}:assurance-isolation-(?:source|attacker)-[A-Za-z0-9_-]{1,96}$/;
if (!safeKey.test(sourceKey || "") || !safeKey.test(attackerKey || "") || sourceKey === attackerKey) {
  fail("unsafe or missing synthetic session keys");
}
if (!process.env.OPENCLAW_BIN || !process.env.OPENCLAW_CONFIG_PATH) {
  fail("Pixel's OpenClaw environment is not loaded");
}

let packageRoot;
let config;
try {
  packageRoot = path.dirname(fs.realpathSync(process.env.OPENCLAW_BIN));
  config = JSON.parse(fs.readFileSync(process.env.OPENCLAW_CONFIG_PATH, "utf8"));
} catch {
  fail("cannot resolve the pinned OpenClaw package or configuration");
}
if (config?.tools?.sessions?.visibility !== "tree") {
  fail("deployed session visibility is not tree");
}

const dist = path.join(packageRoot, "dist");
let candidates;
try {
  candidates = fs.readdirSync(dist)
    .filter((name) => /^openclaw-tools-.*\.js$/.test(name))
    .map((name) => path.join(dist, name))
    .filter((file) => fs.readFileSync(file, "utf8").includes("function createSessionsHistoryTool"));
} catch {
  fail("cannot inspect the pinned OpenClaw tool bundle");
}
if (candidates.length !== 1) {
  fail("cannot identify exactly one pinned OpenClaw tool bundle");
}

const bundleSource = fs.readFileSync(candidates[0], "utf8");
const exportName = bundleSource.match(/createOpenClawTools as ([A-Za-z_$][A-Za-z0-9_$]*)/)?.[1];
if (!exportName) {
  fail("cannot identify the OpenClaw tool factory export");
}
const bundle = await import(pathToFileURL(candidates[0]).href);
const createOpenClawTools = bundle[exportName];
if (typeof createOpenClawTools !== "function") {
  fail("OpenClaw tool factory export is not callable");
}

let historyGatewayReached = false;
const tools = createOpenClawTools({
  agentSessionKey: attackerKey,
  runSessionKey: attackerKey,
  sandboxed: true,
  config,
  disablePluginTools: true,
  workspaceDir: process.env.PIXEL_WORKSPACE,
  callGateway: async () => {
    historyGatewayReached = true;
    throw new Error("history gateway unexpectedly reached");
  },
});
const history = tools.find((tool) => tool.name === "sessions_history");
if (!history || typeof history.execute !== "function") {
  fail("deployed sessions_history tool is absent");
}

let result;
try {
  result = await history.execute("operator-boundary-probe", {
    sessionKey: sourceKey,
    limit: 5,
    includeTools: false,
  });
} catch {
  fail("sessions_history handler threw instead of returning a bounded denial");
}
const text = result?.content?.find((part) => part?.type === "text")?.text;
let parsed;
try {
  parsed = JSON.parse(text);
} catch {
  fail("sessions_history handler returned an invalid result");
}
const output = {
  status: parsed?.status,
  error: parsed?.error,
  historyGatewayReached,
  visibility: "tree",
};
process.stdout.write(`${JSON.stringify(output)}\n`);
if (parsed?.status !== "forbidden" || historyGatewayReached) {
  process.exit(1);
}
