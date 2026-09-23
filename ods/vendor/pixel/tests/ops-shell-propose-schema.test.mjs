import assert from "node:assert/strict";
import { copyFile, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

const AGENT_ID = "pixel";

// Live-exposed raw-shell default-cwd fix: the public plugin-ops schema for
// pixel_ops_shell_propose must no longer require cwd, so omission delegates to
// the broker-reviewed target default working directory instead of forcing the
// model to guess a cwd (which previously got a break-glass plan rejected before
// binding). This test loads the real plugin-ops/index.js with hermetic stubs
// (typebox + plugin-sdk), registers the tools, and inspects the emitted schema.
//
// The typebox stub mirrors the JSON Schema that the pinned typebox emits for
// Type.Object/Type.Optional/Type.String/Type.Integer/Type.Record/Type.Array:
// an Optional property is present in `properties` but excluded from `required`.
const stubTypebox = `
const optional = (value) => ({ __optional: true, value });
const Type = {
  Object: (props, opts = {}) => ({
    type: "object",
    required: Object.entries(props).filter(([, v]) => !v.__optional).map(([k]) => k),
    properties: Object.fromEntries(Object.entries(props).map(([k, v]) => [k, v.__optional ? v.value : v])),
    ...opts,
  }),
  String: (constraints = {}) => ({ type: "string", ...constraints }),
  Integer: (constraints = {}) => ({ type: "integer", ...constraints }),
  Array: (item, opts = {}) => ({ type: "array", items: item, ...opts }),
  Record: (_key, value, opts = {}) => ({ type: "object", additionalProperties: value, ...opts }),
  Optional: optional,
};
export { Type };
`;

const stubPluginEntry = `
export const definePluginEntry = (value) => value;
`;

function assertSchema(pluginEntry, expectedRequired, optionalNames, names) {
  const registered = [];
  pluginEntry.register({ registerTool: (factory) => registered.push(factory) });
  const factory = registered.find((item) => item && item({ agentId: AGENT_ID }) && item({ agentId: AGENT_ID }).name === names);
  assert.ok(factory, `expected tool ${names} to be registered`);
  const tool = factory({ agentId: AGENT_ID });
  for (const name of expectedRequired) assert.ok(tool.parameters.required.includes(name), `${names} must require ${name}`);
  for (const name of optionalNames) assert.ok(!tool.parameters.required.includes(name), `${names} must NOT require ${name}`);
  for (const name of [...expectedRequired, ...optionalNames]) {
    assert.ok(name in tool.parameters.properties, `${names} schema must declare ${name}`);
  }
  return tool;
}

function isToolResultError(result) {
  const details = result && typeof result === "object" && result.details && typeof result.details === "object" && !Array.isArray(result.details)
    ? result.details : undefined;
  const status = typeof details?.status === "string" ? details.status.toLowerCase() : "";
  const explicitlySuccessful = details?.ok === true || details?.success === true;
  if (details?.ok === false || details?.success === false) return true;
  if (["error", "failed", "failure", "timeout", "timed_out", "blocked", "denied", "forbidden", "unavailable",
    "approval-unavailable", "disabled", "aborted", "cancelled", "canceled", "killed", "invalid"].includes(status)
    && !explicitlySuccessful) return true;
  if (details?.timedOut === true || Boolean(details?.error)) return true;
  return typeof details?.exitCode === "number" && Number.isFinite(details.exitCode) && details.exitCode !== 0;
}

test("plugin-ops preserves shell schema and treats broker terminal states as readable results", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-ops-schema-"));
  t.after(async () => { await rm(root, { recursive: true, force: true }); });
  const previousResultDir = process.env.PIXEL_OPS_RESULT_DIR;
  process.env.PIXEL_OPS_RESULT_DIR = join(root, "results");
  t.after(() => {
    if (previousResultDir === undefined) delete process.env.PIXEL_OPS_RESULT_DIR;
    else process.env.PIXEL_OPS_RESULT_DIR = previousResultDir;
  });

  // Copy only production source files, never the worktree's node_modules. A
  // locally installed dependency must not be able to make this clean-CI test
  // pass while a hosted checkout fails.
  await mkdir(join(root, "plugin-ops"), { recursive: true });
  for (const name of ["index.js", "chat-audit.js", "publish-json.js", "secure-read.js", "package.json"]) {
    await copyFile(join(process.cwd(), "plugin-ops", name), join(root, "plugin-ops", name));
  }
  await mkdir(join(root, "plugin-ops", "node_modules", "typebox"), { recursive: true });
  await mkdir(join(root, "plugin-ops", "node_modules", "openclaw", "plugin-sdk"), { recursive: true });
  await mkdir(join(root, "results"), { recursive: true });
  await writeFile(join(root, "plugin-ops", "node_modules", "typebox", "package.json"),
    JSON.stringify({ type: "module", main: "index.mjs", exports: { ".": "./index.mjs" } }) + "\n");
  await writeFile(join(root, "plugin-ops", "node_modules", "typebox", "index.mjs"), stubTypebox);
  // The `openclaw` bare specifier resolves to node_modules/openclaw/package.json;
  // its exports map routes the plugin-sdk subpath to the stub entry module.
  await writeFile(join(root, "plugin-ops", "node_modules", "openclaw", "package.json"),
    JSON.stringify({ type: "module", exports: { "./plugin-sdk/plugin-entry": "./plugin-sdk/plugin-entry.js" } }) + "\n");
  await writeFile(join(root, "plugin-ops", "node_modules", "openclaw", "plugin-sdk", "plugin-entry.js"), stubPluginEntry);

  const pluginEntry = (await import(pathToFileURL(join(root, "plugin-ops", "index.js")))).default;
  const tool = assertSchema(pluginEntry,
    ["target", "command", "reason"],
    ["cwd", "timeoutSeconds"],
    "pixel_ops_shell_propose");
  // cwd remains declared (as a validated string) but optional, so a caller may
  // omit it and the broker falls back to the reviewed target defaultCwd.
  assert.equal(tool.parameters.properties.cwd.type, "string");

  // A broker job outcome is domain state, not a failed tool transport. Mirror
  // OpenClaw's classifier so cancelled/failed records cannot silently regress
  // to isError=true and make a reconciliation loop retry a completed read.
  const jobGet = assertSchema(pluginEntry, ["jobId"], [], "pixel_ops_job_get");
  const jobWait = assertSchema(pluginEntry, ["jobId"], ["timeoutSeconds"], "pixel_ops_job_wait");
  for (const [offset, status] of ["succeeded", "failed", "rejected", "cancelled", "awaiting-approval"].entries()) {
    const jobId = `ops-${1787791974000 + offset}-012345abcde${offset}`;
    const error = ["failed", "rejected", "cancelled"].includes(status) ? `${status} broker outcome` : undefined;
    await writeFile(join(root, "results", `${jobId}.json`), JSON.stringify({
      schemaVersion: 2,
      jobId,
      status,
      updatedAt: "2026-08-27T00:52:54.854685Z",
      approvalRequired: status === "awaiting-approval",
      ...(error ? { error } : {}),
    }) + "\n");
    for (const [tool, suffix] of [[jobGet, "get"], [jobWait, "wait"]]) {
      const observed = await tool.execute(`tool-call-${suffix}-${status}`, { jobId, timeoutSeconds: 1 });
      assert.equal(observed.details.status, status);
      assert.equal(observed.details.ok, true);
      assert.equal("error" in observed.details, false);
      assert.equal(observed.isError, undefined);
      assert.equal(isToolResultError(observed), false, `${suffix} ${status} is domain state, not a tool error`);
      assert.equal(observed.content[0].text.includes('"status": "' + status + '"'), true);
      if (error) assert.match(observed.content[0].text, new RegExp(error, "u"));
    }
  }

  const missing = await jobGet.execute("tool-call-missing", { jobId: "ops-1787791974001-fedcba654321" });
  assert.equal(missing.isError, true);
  assert.match(missing.content[0].text, /Pixel Operations Broker error/u);
});
