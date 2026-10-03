import assert from "node:assert/strict";
import { createServer } from "node:http";
import test from "node:test";

import {
  MODEL_QUALIFICATION_UPSTREAM, createModelQualificationBridge,
} from "../deploy/work-controller/model-qualification-bridge.mjs";
import {
  MODEL_QUALIFICATION_CONFIG_PATH, MODEL_QUALIFICATION_OUTPUT_PATH,
  runModelQualificationContainer,
} from "../deploy/work-controller/model-qualification-container.mjs";

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert.ok(address && typeof address === "object");
  return `http://127.0.0.1:${address.port}`;
}

async function close(server) {
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
}

test("qualification bridge exposes only the fixed credential-free chat route", async (t) => {
  const upstreamCalls = [];
  const bridge = createModelQualificationBridge({
    origin: MODEL_QUALIFICATION_UPSTREAM,
    fetchImpl: async (url, options) => {
      upstreamCalls.push({ url, options });
      return new Response(JSON.stringify({ choices: [], usage: {} }), {
        status: 200, headers: { "content-type": "application/json" },
      });
    },
  });
  const origin = await listen(bridge);
  t.after(() => close(bridge));
  const response = await fetch(`${origin}/v1/chat/completions`, {
    method: "POST", headers: { "content-type": "application/json" }, body: "{}",
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { choices: [], usage: {} });
  assert.equal(upstreamCalls.length, 1);
  assert.equal(upstreamCalls[0].url, `${MODEL_QUALIFICATION_UPSTREAM}/v1/chat/completions`);
  assert.equal(upstreamCalls[0].options.redirect, "error");
  assert.equal(upstreamCalls[0].options.headers.authorization, undefined);
  assert.equal((await fetch(`${origin}/health`)).status, 404);
  assert.equal((await fetch(`${origin}/v1/chat/completions`, {
    method: "POST", headers: { authorization: "Bearer secret", "content-type": "application/json" }, body: "{}",
  })).status, 400);
  assert.equal((await fetch(`${origin}/v1/chat/completions`, {
    method: "POST", headers: { "content-type": "text/plain" }, body: "{}",
  })).status, 415);
  assert.equal(upstreamCalls.length, 1);
});

test("qualification bridge refuses origin and listener widening", async () => {
  assert.throws(() => createModelQualificationBridge({ origin: "http://127.0.0.1:8080" }), /fixed private model origin/u);
});

test("qualification container binds fixed paths, closes its bridge, and returns content-free state", async () => {
  const calls = [];
  const bridge = {
    close(callback) { calls.push("close"); callback(); },
  };
  const result = await runModelQualificationContainer([], {
    async startBridge() { calls.push("start"); return bridge; },
    async runQualification(argv) {
      calls.push(argv);
      return {
        schemaVersion: 1, operation: "pixel-work-model-qualification", status: "qualified",
        qualificationId: "modelqual-1786622400000-abcdef123456", receiptSha256: "a".repeat(64),
        casesPassed: 13, casesFailed: 0, eligibleProfiles: ["scout", "builder"],
        authority: { grantsExecution: false }, boundary: "content-free fixture",
      };
    },
  });
  assert.deepEqual(calls, [
    "start",
    ["--config", MODEL_QUALIFICATION_CONFIG_PATH, "--output", MODEL_QUALIFICATION_OUTPUT_PATH],
    "close",
  ]);
  assert.equal(result.status, "qualified");
  assert.equal(result.privateNetwork, true);
  assert.equal(result.credentialsUsed, false);
  assert.equal(result.externalNetwork, false);
  await assert.rejects(runModelQualificationContainer(["--config", "other.json"]), /accepts no arguments/u);
});

test("qualification container closes its bridge after runner failure", async () => {
  let closed = false;
  await assert.rejects(runModelQualificationContainer([], {
    async startBridge() { return { close(callback) { closed = true; callback(); } }; },
    async runQualification() { throw new Error("fixture failure"); },
  }), /fixture failure/u);
  assert.equal(closed, true);
});
