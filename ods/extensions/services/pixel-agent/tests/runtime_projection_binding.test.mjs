import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { configFromEnv, readGatewayConfiguration, writeStatus } from "../host/pixel_ingress.mjs";

for (const selected of ["ods-policy/leader", "custom-remote/model", "ods-gateway/ods/current"]) {
  test(`unresolved configured binding ${selected} never becomes the unrelated local model`, async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "runtime-binding-"));
    try {
      const file = path.join(directory, "openclaw.json");
      fs.writeFileSync(file, JSON.stringify({
        gateway: { auth: { token: "test-only-private-token" } },
        agents: { list: [{ id: "pixel", model: selected }] },
        models: { providers: {} },
      }), { mode: 0o600 });
      const configured = readGatewayConfiguration(file);
      assert.equal(configured.runtimeAuthoritative, true);
      assert.equal(configured.runtime, null);
      const inspections = [];
      const projection = await writeStatus(true, 18999, path.join(directory, "status.json"), "2.6.0", {
        fetch: async () => ({ status: 200, body: { cancel: async () => {} } }),
        execFile: (_command, args, _options, callback) => {
          inspections.push(args);
          if (args[0] === "ps") {
            callback(null, `${JSON.stringify({ Names: "ods-llama-server", Status: "Up (healthy)" })}\n`, "");
          } else {
            callback(null, JSON.stringify(["--model", "/models/unrelated-local.gguf", "--ctx-size", "32768"]), "");
          }
        },
        setTimeout,
        clearTimeout,
      }, configFromEnv({}).appPorts, configured.runtime);
      assert.equal(projection.runtime, null);
      assert.equal(projection.docker, "ok");
      assert.equal(projection.online_apps, 1);
      assert.equal(projection.gateway_reachable, true);
      assert.deepEqual(inspections.map(args => args[0]), ["ps"]);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
}

test("legacy token-only status retains local Docker fallback when the router is unavailable", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "runtime-legacy-"));
  try {
    const file = path.join(directory, "gateway.env");
    fs.writeFileSync(file, "PIXEL_GATEWAY_TOKEN=test-only-private-token\n", { mode: 0o600 });
    const configured = readGatewayConfiguration(file);
    assert.equal(configured.runtimeAuthoritative, false);
    const inspections = [];
    const projection = await writeStatus(true, 18999, path.join(directory, "status.json"), "2.6.0", {
      fetch: async () => ({ status: 200, body: { cancel: async () => {} } }),
      execFile: (_command, args, _options, callback) => {
        inspections.push(args);
        if (args[0] === "ps") callback(null, "", "");
        else if (args[0] === "exec") callback(new Error("router unavailable"));
        else callback(null, JSON.stringify(["--model", "/models/legacy-local.gguf", "--ctx-size", "32768"]), "");
      },
      setTimeout,
      clearTimeout,
    }, configFromEnv({}).appPorts, configured.runtimeAuthoritative ? configured.runtime : undefined);
    assert.deepEqual(projection.runtime, { model: "legacy-local.gguf", context_length: 32768 });
    assert.deepEqual(inspections.map(args => args[0]), ["ps", "exec", "inspect"]);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
