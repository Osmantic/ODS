import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { inspectWorkProviderCredentialCustody } from "../deploy/work-provider/credential-custody.mjs";
import { resolveWorkProvider } from "../deploy/work-provider/provider-registry.mjs";
import {
  buildRemoteRequest, executeRemoteProviderTurn, parseRemoteHttpResponse, remoteProviderTransportTestOnly, REMOTE_WIRE_MAPPINGS, RemoteWorkProviderTransportError, remoteTransportInternals,
} from "../deploy/work-provider/generic-remote-transport.mjs";
import {
  claimWorkProviderRequest, createConnectivitySmokeBinding, createSemanticRunBinding, createWorkProviderRunLedger, providerIdempotencyKey, workProviderInputSha256,
} from "../deploy/work-provider/run-ledger.mjs";
import { routeWorkProvider } from "../deploy/work-provider-router/router.mjs";
import { makePrivatePolicy, makeQualification, makeRequest, makeRouterPolicy, makeLocalQualification } from "./fixtures/work-provider-router.mjs";
import { validateWorkProviderPrivatePolicy } from "../scripts/lib/work-contract.mjs";
import { NOW, SUFFIX } from "./fixtures/work-provider-exec.mjs";

const OWNER_PINNED_MODELS = Object.freeze({
  openrouter: "anthropic/claude-sonnet-4-5",
  together: "meta-llama/Llama-3.3-70B-Instruct-Turbo",
  fireworks: "accounts/fireworks/models/llama-v3p1-70b-instruct",
  groq: "llama-3.3-70b-versatile",
});

function modelFor(profile) {
  if (profile.modelSelection === "fixed") return profile.defaultModel;
  return OWNER_PINNED_MODELS[profile.id];
}

function privatePolicyFor(providerId, profile, model) {
  return makePrivatePolicy(providerId, {
    model,
    transport: { allowedHosts: profile.allowedHosts, proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
    budgets: {
      maxRequestsPerRun: Math.min(4, profile.budgets.maxRequestsPerRun),
      maxInputTokensPerRun: profile.budgets.maxInputTokensPerRun,
      maxOutputTokensPerRun: profile.budgets.maxOutputTokensPerRun,
      maxNetworkBytesPerRun: 1048576,
      maxRequestSeconds: 120,
      maxEstimatedCostMicrosPerRun: 1000000,
      maxEstimatedCostMicrosPerDay: 5000000,
    },
  });
}

async function setupProvider(providerId, root) {
  const resolvedProvider = resolveWorkProvider(providerId, { enabledRemoteProviders: [providerId] });
  const profile = resolvedProvider.profile;
  const model = modelFor(profile);
  const privatePolicy = privatePolicyFor(providerId, profile, model);
  const qualification = makeQualification(providerId, { model });
  const routerPolicy = makeRouterPolicy({ providerId, privatePolicy });
  const request = makeRequest({ mode: "explicit-provider", explicitProviderId: providerId });
  const enabledPrivatePolicies = { [providerId]: privatePolicy };
  const qualifications = { local: makeLocalQualification(), [providerId]: qualification };
  const { decision } = routeWorkProvider({ request, routerPolicy, enabledPrivatePolicies, qualifications, now: NOW, suffix: SUFFIX });
  const directory = join(root, providerId, "credential"); const credentialPath = join(directory, "provider-key");
  await mkdir(directory, { mode: 0o700, recursive: true }); await writeFile(credentialPath, "temporary-test-key-abcdefghijklmnopqrstuvwxyz", { mode: 0o600 });
  await chmod(directory, 0o700); await chmod(credentialPath, 0o600);
  const custody = await inspectWorkProviderCredentialCustody({ resolvedProvider, policy: privatePolicy, credentialPath, now: NOW, suffix: SUFFIX });
  const binding = createSemanticRunBinding({ decision, request, routerPolicy, enabledPrivatePolicies, qualifications, resolvedProvider, privatePolicy, qualification, model });
  const ledger = createWorkProviderRunLedger({ resolvedProvider, policy: privatePolicy, taskId: `pixel-${providerId}`, model, binding, now: NOW, suffix: SUFFIX });
  return { resolvedProvider, policy: privatePolicy, privatePolicy, qualification, model, credentialHandle: custody.handle, ledger, input: { schemaVersion: 1, model, messages: [{ role: "user", content: "task" }], maxOutputTokens: 256 } };
}

function responseFor(providerId, model, content = "{\"ok\":true}") {
  if (providerId === "openai") {
    return { id: "resp_fixture", object: "response", created_at: 1, completed_at: 2, status: "completed", error: null, incomplete_details: null, instructions: null, max_output_tokens: 256, model, output: [{ type: "message", id: "m1", role: "assistant", status: "completed", content: [{ type: "output_text", text: content }] }], parallel_tool_calls: false, previous_response_id: null, reasoning: {}, store: false, temperature: 1, text: {}, tool_choice: "auto", tools: [], top_p: 1, usage: { input_tokens: 10, output_tokens: 5, total_tokens: 15 }, metadata: {} };
  }
  if (providerId === "anthropic") {
    return { id: "msg_fixture", type: "message", role: "assistant", model, content: [{ type: "text", text: content }], stop_reason: "end_turn", stop_sequence: null, usage: { input_tokens: 10, output_tokens: 5 } };
  }
  return { id: "chatcmpl_fixture", object: "chat.completion", created: 1, model, choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content } }], usage: { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 } };
}

function httpResponse(responseJson) {
  const body = Buffer.from(JSON.stringify(responseJson), "utf8");
  return { statusCode: 200, headers: { "content-type": "application/json", "content-length": String(body.length), "x-request-id": "req-fixture" }, body, networkBytes: 100 + body.length };
}

const PROVIDERS = ["openai", "anthropic", "openrouter", "together", "fireworks", "groq"];

test("every remote provider has a closed static wire mapping", () => {
  for (const providerId of PROVIDERS) {
    const wire = REMOTE_WIRE_MAPPINGS[providerId];
    assert.ok(wire);
    assert.equal(typeof wire.host, "string");
    assert.equal(wire.port, 443);
    assert.match(wire.path, /^\//u);
    assert.equal(typeof wire.authHeader, "string");
    assert.equal(typeof wire.outputTokenField, "string");
  }
});

test("connectivity smoke remains non-semantic and buildable by every remote adapter", () => {
  for (const providerId of PROVIDERS) {
    const resolvedProvider = resolveWorkProvider(providerId, { enabledRemoteProviders: [providerId] });
    const wire = REMOTE_WIRE_MAPPINGS[providerId];
    const probe = remoteTransportInternals.smokeProbe(wire, modelFor(resolvedProvider.profile));
    assert.equal(Object.hasOwn(probe, "reasoningEffort"), false);
    assert.doesNotThrow(() => resolvedProvider.adapter.buildRequest(probe, { outputTokenField: wire.outputTokenField }));
  }
});

test("hermetic connectivity smoke executes every remote adapter and requires the exact sentinel", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-smoke-"));
  await chmod(root, 0o700);
  try {
    for (const providerId of PROVIDERS) {
      const { resolvedProvider, policy, model, credentialHandle } = await setupProvider(providerId, root);
      const wire = REMOTE_WIRE_MAPPINGS[providerId];
      const input = remoteTransportInternals.smokeProbe(wire, model);
      const claimedSmoke = (taskId) => {
        const binding = createConnectivitySmokeBinding({ purpose: `${providerId}-connectivity-smoke-v1`, requestSha256: workProviderInputSha256(input), model });
        const ledger = createWorkProviderRunLedger({ resolvedProvider, policy, taskId, model, binding, now: NOW, suffix: SUFFIX });
        const idempotencyKey = providerIdempotencyKey({ taskId, turn: 1, inputSha256: workProviderInputSha256(input) });
        return {
          idempotencyKey,
          ledger: claimWorkProviderRequest({ ledger, policy, resolvedProvider, idempotencyKey, inputSha256: workProviderInputSha256(input), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000, now: NOW }),
        };
      };
      const good = claimedSmoke(`pixel-${providerId}-smoke-good`);
      const result = await remoteProviderTransportTestOnly.executeRemoteProviderTurnWithExchange({
        resolvedProvider, policy, credentialHandle, ledger: good.ledger, idempotencyKey: good.idempotencyKey, input,
      }, async () => httpResponse(responseFor(providerId, model, remoteTransportInternals.SMOKE_OK)));
      assert.equal(result.assistant.message.content, remoteTransportInternals.SMOKE_OK);

      const bad = claimedSmoke(`pixel-${providerId}-smoke-bad`);
      await assert.rejects(() => remoteProviderTransportTestOnly.executeRemoteProviderTurnWithExchange({
        resolvedProvider, policy, credentialHandle, ledger: bad.ledger, idempotencyKey: bad.idempotencyKey, input,
      }, async () => httpResponse(responseFor(providerId, model, "wrong smoke response"))), /exact sentinel/u);

      if (providerId === "openrouter") {
        const withTool = claimedSmoke("pixel-openrouter-smoke-tool");
        const toolResponse = responseFor(providerId, model, remoteTransportInternals.SMOKE_OK);
        toolResponse.choices[0].message.tool_calls = [{ id: "call_fixture", type: "function", function: { name: "unexpected", arguments: "{}" } }];
        await assert.rejects(() => remoteProviderTransportTestOnly.executeRemoteProviderTurnWithExchange({
          resolvedProvider, policy, credentialHandle, ledger: withTool.ledger, idempotencyKey: withTool.idempotencyKey, input,
        }, async () => httpResponse(toolResponse)), /exact sentinel/u);
      }
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("hermetic wire exchange: exact CONNECT target, TLS SNI, method/path, auth, fixed headers, adapter shape, model, usage", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-wire-"));
  await chmod(root, 0o700);
  try {
    for (const providerId of PROVIDERS) {
      const setup = await setupProvider(providerId, root);
      const { resolvedProvider, policy, model, credentialHandle, ledger, input } = setup;
      const idem = providerIdempotencyKey({ taskId: `pixel-${providerId}`, turn: 1, inputSha256: workProviderInputSha256(input) });
      const claimed = claimWorkProviderRequest({ ledger, policy, resolvedProvider, idempotencyKey: idem, inputSha256: workProviderInputSha256(input), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000, now: NOW });
      let captured = null; let credentialBuffer = null;
      const result = await remoteProviderTransportTestOnly.executeRemoteProviderTurnWithExchange({
        resolvedProvider, policy, credentialHandle, ledger: claimed, idempotencyKey: idem, input,
      }, async (req) => {
          captured = req; credentialBuffer = req.credential;
          return httpResponse(responseFor(providerId, model));
      });
      const wire = REMOTE_WIRE_MAPPINGS[providerId];
      assert.equal(captured.host, wire.host);
      assert.equal(captured.port, 443);
      assert.equal(captured.path, wire.path);
      assert.equal(captured.wire, providerId);
      // Credential bytes are zeroed by the transport after the exchange.
      assert.equal(credentialBuffer.every((byte) => byte === 0), true);
      const requestBytes = buildRemoteRequest({ wire: providerId, host: wire.host, path: wire.path, credential: Buffer.from("temporary-test-key-abcdefghijklmnopqrstuvwxyz", "ascii"), body: captured.body });
      const headerText = requestBytes.subarray(0, requestBytes.indexOf("\r\n\r\n")).toString("ascii");
      assert.ok(headerText.startsWith(`POST ${wire.path} HTTP/1.1`));
      assert.ok(headerText.includes(`Host: ${wire.host}`));
      assert.ok(headerText.includes("Content-Length:"));
      assert.ok(headerText.includes("Accept-Encoding: identity"));
      if (providerId === "anthropic") {
        assert.ok(headerText.includes("x-api-key: temporary-test-key-abcdefghijklmnopqrstuvwxyz"));
        assert.ok(headerText.includes("anthropic-version: 2023-06-01"));
      } else {
        assert.ok(headerText.toLowerCase().includes(`authorization: bearer temporary-test-key-abcdefghijklmnopqrstuvwxyz`));
      }
      const providerRequest = JSON.parse(captured.body.toString("utf8"));
      assert.equal(providerRequest.model, model);
      if (providerId === "openai") assert.ok(Array.isArray(providerRequest.input));
      else assert.equal(typeof providerRequest.messages, "object");
      // Adapter selection matches the profile protocol.
      assert.equal(resolvedProvider.profile.protocol, wire.protocol);
      assert.equal(result.assistant.providerModel, model);
      assert.deepEqual(result.assistant.usage, { inputTokens: 10, outputTokens: 5, totalTokens: 15 });
      assert.equal(result.networkBytes >= captured.body.length, true);
    }
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("hermetic adversarial: wrong host/path/model, missing usage/model, and off-mapping request builder fail closed", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-adversarial-"));
  await chmod(root, 0o700);
  try {
    const setup = await setupProvider("openai", root);
    const { resolvedProvider, policy, model, credentialHandle, ledger, input } = setup;
    const idem = providerIdempotencyKey({ taskId: "pixel-openai", turn: 2, inputSha256: workProviderInputSha256(input) });
    const claimed = claimWorkProviderRequest({ ledger, policy, resolvedProvider, idempotencyKey: idem, inputSha256: workProviderInputSha256(input), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000, now: NOW });
    const respond = (json) => remoteProviderTransportTestOnly.executeRemoteProviderTurnWithExchange({ resolvedProvider, policy, credentialHandle, ledger: claimed, idempotencyKey: idem, input }, async (req) => httpResponse(json));
    const base = responseFor("openai", model);
    // Missing response model fails known.
    const noModel = structuredClone(base); delete noModel.model;
    await assert.rejects(() => respond(noModel), RemoteWorkProviderTransportError);
    // Wrong response model fails known.
    await assert.rejects(() => respond({ ...base, model: "wrong-model" }), /differs from the pinned run model/u);
    // Missing usage fails known.
    const noUsage = structuredClone(base); delete noUsage.usage;
    await assert.rejects(() => respond(noUsage), /exact token usage/u);
    // Off-mapping request construction fails closed.
    assert.throws(() => buildRemoteRequest({ wire: "openai", host: "evil.example.com", path: "/v1/responses", credential: Buffer.from("key"), body: Buffer.from("{}") }), /off-mapping/u);
    assert.throws(() => buildRemoteRequest({ wire: "openai", host: "api.openai.com", path: "/v1/evil", credential: Buffer.from("key"), body: Buffer.from("{}") }), /off-mapping/u);
    // Wrong input model is rejected before any network exchange.
    const badInput = { ...input, model: "wrong-model" };
    const badIdem = providerIdempotencyKey({ taskId: "pixel-openai", turn: 3, inputSha256: workProviderInputSha256(badInput) });
    const badClaimed = claimWorkProviderRequest({ ledger, policy, resolvedProvider, idempotencyKey: badIdem, inputSha256: workProviderInputSha256(badInput), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000, now: NOW });
    let exchanged = false;
    await assert.rejects(() => remoteProviderTransportTestOnly.executeRemoteProviderTurnWithExchange({ resolvedProvider, policy, credentialHandle, ledger: badClaimed, idempotencyKey: badIdem, input: badInput }, async () => { exchanged = true; return httpResponse(base); }), /model, or claim/u);
    assert.equal(exchanged, false);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("hermetic adversarial: owner-pinned placeholder model is forbidden and non-2xx is failed-known", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-provider-ownerpinned-"));
  await chmod(root, 0o700);
  try {
    const openrouter = resolveWorkProvider("openrouter", { enabledRemoteProviders: ["openrouter"] });
    const placeholderPolicy = privatePolicyFor("openrouter", openrouter.profile, "provider-selected-model");
    assert.ok(validateWorkProviderPrivatePolicy(placeholderPolicy).some((error) => error.includes("model")));
    const autoPolicy = privatePolicyFor("openrouter", openrouter.profile, "auto");
    assert.ok(validateWorkProviderPrivatePolicy(autoPolicy).some((error) => error.includes("model")));
    const setup = await setupProvider("openrouter", root);
    const { resolvedProvider, policy, credentialHandle, ledger, input } = setup;
    const idem = providerIdempotencyKey({ taskId: "pixel-openrouter", turn: 1, inputSha256: workProviderInputSha256(input) });
    const claimed = claimWorkProviderRequest({ ledger, policy, resolvedProvider, idempotencyKey: idem, inputSha256: workProviderInputSha256(input), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000, now: NOW });
    await assert.rejects(() => remoteProviderTransportTestOnly.executeRemoteProviderTurnWithExchange({ resolvedProvider, policy, credentialHandle, ledger: claimed, idempotencyKey: idem, input }, async (req) => ({ statusCode: 429, headers: { "content-type": "application/json", "content-length": "2" }, body: Buffer.from("{}"), networkBytes: req.body.length + 2 })), /HTTP 429/u);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("production remote transport rejects a caller-supplied exchange and never invokes it", async () => {
  let invoked = false;
  await assert.rejects(() => executeRemoteProviderTurn({ resolvedProvider: {}, exchange: async () => { invoked = true; } }), /cannot accept a caller-supplied exchange/u);
  await assert.rejects(() => executeRemoteProviderTurn({ resolvedProvider: {}, testSeam: true }), /cannot accept a caller-supplied exchange/u);
  assert.equal(invoked, false);
});

test("adversarial: HTTP response framing fails closed unless exactly one supported mechanism is present", () => {
  const body = Buffer.from('{"ok":true}', "utf8");
  const noFraming = Buffer.concat([Buffer.from("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n", "ascii"), body]);
  assert.throws(() => parseRemoteHttpResponse(noFraming, 1024), /neither Content-Length nor Transfer-Encoding/u);
  const conflicting = Buffer.concat([Buffer.from(`HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\nTransfer-Encoding: chunked\r\n\r\n`, "ascii"), body]);
  assert.throws(() => parseRemoteHttpResponse(conflicting, 1024), /conflicting body framing/u);
  const wrongLength = Buffer.concat([Buffer.from("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5\r\n\r\n", "ascii"), body]);
  assert.throws(() => parseRemoteHttpResponse(wrongLength, 1024), /content length is invalid/u);
  const good = Buffer.concat([Buffer.from(`HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n\r\n`, "ascii"), body]);
  const parsed = parseRemoteHttpResponse(good, 1024);
  assert.equal(parsed.statusCode, 200);
  assert.equal(parsed.body.toString("utf8"), '{"ok":true}');
  const repeatedUnconsumed = Buffer.concat([Buffer.from(`HTTP/1.1 200 OK\r\nVia: edge-a\r\nVia: edge-b\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n\r\n`, "ascii"), body]);
  assert.equal(parseRemoteHttpResponse(repeatedUnconsumed, 1024).headers.via, "edge-a");
  for (const header of ["Content-Length: 11", "Transfer-Encoding: chunked", "Content-Type: application/json", "Content-Encoding: identity", "X-Request-Id: second"]) {
    const name = header.slice(0, header.indexOf(":"));
    const first = name.toLowerCase() === "transfer-encoding" ? "Transfer-Encoding: chunked" : name.toLowerCase() === "content-length" ? `Content-Length: ${body.length}` : `${name}: ${name.toLowerCase() === "x-request-id" ? "first" : name.toLowerCase() === "content-encoding" ? "identity" : "application/json"}`;
    const duplicate = Buffer.concat([Buffer.from(`HTTP/1.1 200 OK\r\n${first}\r\n${header}\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n\r\n`, "ascii"), body]);
    assert.throws(() => parseRemoteHttpResponse(duplicate, 1024), /duplicate singleton header/u);
  }
  const earlyHints = Buffer.concat([Buffer.from("HTTP/1.1 103 Early Hints\r\nLink: </asset>; rel=preload\r\n\r\n", "ascii"), good]);
  assert.throws(() => parseRemoteHttpResponse(earlyHints, 1024));
});
