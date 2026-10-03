import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { inspectWorkProviderCredentialCustody } from "../deploy/work-provider/credential-custody.mjs";
import { executeMoonshotChatTurn, moonshotTransportTestOnly, MoonshotWorkProviderTransportError, parseMoonshotHttpResponse } from "../deploy/work-provider/moonshot-transport.mjs";
import { resolveWorkProvider } from "../deploy/work-provider/provider-registry.mjs";
import { claimWorkProviderRequest, createConnectivitySmokeBinding, createWorkProviderRunLedger, providerIdempotencyKey, workProviderInputSha256 } from "../deploy/work-provider/run-ledger.mjs";

const boundary = "Owner-private remote work-provider policy. It may enable only the exact selected provider, credential custody, egress hosts, data classes, and budgets; it grants no merge, push, deployment, publication, external-message, production-credential, cloud-fallback, policy-mutation, verifier-selection, completion, or security-testing authority.";
const policy = {
  $schema: "https://osmantic.com/pixel/schemas/work-provider-private-policy-v1.schema.json", schemaVersion: 1,
  policyId: "workproviderpolicy-1787328000000-abcdef123456", createdAt: "2026-08-21T16:00:00Z", providerId: "moonshot-kimi", enabled: true,
  credentialCustody: { credentialId: "moonshot-test", fileName: "provider-key", maxBytes: 8192 },
  transport: { allowedHosts: ["api.moonshot.ai:443"], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
  dataPolicy: { allowedClassifications: ["internal-source", "owner-approved-source", "public"], neverEgressCategories: ["authentication-material", "credentials", "private-keys", "regulated-records", "session-tokens", "unapproved-owner-data"] },
  budgets: { maxRequestsPerRun: 4, maxInputTokensPerRun: 50000, maxOutputTokensPerRun: 10000, maxNetworkBytesPerRun: 16777216, maxRequestSeconds: 120, maxEstimatedCostMicrosPerRun: 5000000, maxEstimatedCostMicrosPerDay: 20000000 },
  fallback: { mode: "local-only", cloudToCloudAllowed: false, reconcileBeforeRetry: true }, verification: { independentLocalVerifierRequired: true, verifierMayBeWorker: false, failClosedIfUnavailable: true },
  authority: { merge: false, push: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false }, boundary,
};
const provider = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });

test("Moonshot transport uses one claimed turn, proxy metadata, and zeroes credential bytes", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-moonshot-transport-")); const directory = join(root, "credential"); const credentialPath = join(directory, "provider-key");
  await mkdir(directory, { mode: 0o700 }); await writeFile(credentialPath, "temporary-test-key-abcdefghijklmnopqrstuvwxyz", { mode: 0o600 }); await chmod(directory, 0o700); await chmod(credentialPath, 0o600);
  try {
    const custody = await inspectWorkProviderCredentialCustody({ resolvedProvider: provider, policy, credentialPath, now: new Date("2026-08-21T16:30:00Z"), suffix: "abcdef123456" });
    const smokeInput = { schemaVersion: 1, model: "kimi-k3", messages: [{ role: "user", content: "Return exactly PIXEL_K3_SMOKE_OK and nothing else." }], maxOutputTokens: 256, reasoningEffort: "low" };
    const binding = createConnectivitySmokeBinding({ purpose: "moonshot-connectivity-smoke-v1", requestSha256: workProviderInputSha256(smokeInput), model: "kimi-k3" });
    const initial = createWorkProviderRunLedger({ resolvedProvider: provider, policy, taskId: "pixel-k3-transport", model: "kimi-k3", binding, now: new Date("2026-08-21T16:30:01Z"), suffix: "123456abcdef" });
    const idempotencyKey = providerIdempotencyKey({ task: "pixel-k3-transport", turn: 1 });
    const ledger = claimWorkProviderRequest({ ledger: initial, policy, resolvedProvider: provider, idempotencyKey, inputSha256: workProviderInputSha256(smokeInput), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:30:02Z") });
    let credentialReference, observedCredential, observedRequest;
    const responseValue = {
      id: "chatcmpl-moonshot-fixture", object: "chat.completion", created: 1, model: "kimi-k3",
      choices: [{ index: 0, finish_reason: "tool_calls", logprobs: null, message: { role: "assistant", content: null, reasoning_content: "private-state", tool_calls: [{ id: "call_1", type: "function", function: { name: "read_file", arguments: "{\"path\":\"README.md\"}" } }] } }],
      usage: { prompt_tokens: 80, completion_tokens: 20, total_tokens: 100 },
    };
    const result = await moonshotTransportTestOnly.executeMoonshotChatTurnWithExchange({
      resolvedProvider: provider, policy, credentialHandle: custody.handle, ledger, idempotencyKey,
      input: smokeInput,
    }, async (request) => {
        credentialReference = request.credential; observedCredential = request.credential.toString("ascii"); observedRequest = JSON.parse(request.body.toString("utf8"));
        const body = Buffer.from(JSON.stringify(responseValue));
        return { statusCode: 200, headers: { "x-request-id": "moonshot-request-fixture" }, body, networkBytes: request.body.length + body.length };
    });
    assert.equal(observedCredential, "temporary-test-key-abcdefghijklmnopqrstuvwxyz"); assert.equal(credentialReference.every((byte) => byte === 0), true);
    assert.equal(observedRequest.model, "kimi-k3"); assert.equal(observedRequest.max_completion_tokens, 256); assert.equal(Object.hasOwn(observedRequest, "max_tokens"), false);
    assert.equal(observedRequest.reasoning_effort, "low"); assert.equal(Object.hasOwn(observedRequest, "temperature"), false);
    assert.equal(result.assistant.message.content, null); assert.equal(result.assistant.message.providerState.assistantMessage.reasoning_content, "private-state");
    assert.equal(result.assistant.usage.totalTokens, 100); assert.equal(result.providerRequestId, "moonshot-request-fixture");
    assert.equal(JSON.stringify(result).includes("temporary-test-key"), false);

    const arbitraryInput = { ...smokeInput, messages: [{ role: "user", content: "perform arbitrary semantic work" }] };
    const arbitraryInitial = createWorkProviderRunLedger({ resolvedProvider: provider, policy, taskId: "pixel-k3-smoke-abuse", model: "kimi-k3", binding, now: new Date("2026-08-21T16:31:01Z"), suffix: "abcdef654321" });
    const arbitraryKey = providerIdempotencyKey({ task: "pixel-k3-smoke-abuse", turn: 1 });
    const arbitraryLedger = claimWorkProviderRequest({ ledger: arbitraryInitial, policy, resolvedProvider: provider, idempotencyKey: arbitraryKey, inputSha256: workProviderInputSha256(arbitraryInput), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:31:02Z") });
    await assert.rejects(moonshotTransportTestOnly.executeMoonshotChatTurnWithExchange({
      resolvedProvider: provider, policy, credentialHandle: custody.handle, ledger: arbitraryLedger,
      idempotencyKey: arbitraryKey, input: arbitraryInput,
    }, async () => { throw new Error("must not be invoked"); }), /escaped its fixed non-semantic probe/u);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Moonshot transport classifies deterministic preflight and provider HTTP errors as failed-known", async () => {
  const deterministic = new MoonshotWorkProviderTransportError("fixture", "failed-known");
  const ambiguous = new MoonshotWorkProviderTransportError("fixture", "uncertain");
  assert.equal(deterministic.outcome, "failed-known");
  assert.equal(ambiguous.outcome, "uncertain");
  assert.throws(() => new MoonshotWorkProviderTransportError("fixture", "invalid"), TypeError);
});

test("Moonshot HTTP parser accepts bounded JSON and rejects ambiguous framing", () => {
  const body = Buffer.from("{\"ok\":true}");
  const fixed = Buffer.concat([Buffer.from(`HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n\r\n`), body]);
  assert.equal(parseMoonshotHttpResponse(fixed, 1024).body.toString("utf8"), "{\"ok\":true}");
  const chunked = Buffer.from("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\nA\r\n{\"x\":true}\r\n0\r\n\r\n", "ascii");
  assert.equal(parseMoonshotHttpResponse(chunked, 1024).body.toString("utf8"), "{\"x\":true}");
  const ambiguous = Buffer.from("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}", "ascii");
  assert.throws(() => parseMoonshotHttpResponse(ambiguous, 1024), /conflicting/u);
  const repeatedUnconsumed = Buffer.concat([Buffer.from(`HTTP/1.1 200 OK\r\nDate: Thu, 21 Aug 2026 20:00:00 GMT\r\nDate: Thu, 21 Aug 2026 20:00:01 GMT\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n\r\n`), body]);
  assert.equal(parseMoonshotHttpResponse(repeatedUnconsumed, 1024).headers.date, "Thu, 21 Aug 2026 20:00:00 GMT");
  for (const header of ["Content-Length: 11", "Transfer-Encoding: chunked", "Content-Type: application/json", "Content-Encoding: identity", "X-Request-Id: second"]) {
    const name = header.slice(0, header.indexOf(":"));
    const first = name.toLowerCase() === "transfer-encoding" ? "Transfer-Encoding: chunked" : name.toLowerCase() === "content-length" ? `Content-Length: ${body.length}` : `${name}: ${name.toLowerCase() === "x-request-id" ? "first" : name.toLowerCase() === "content-encoding" ? "identity" : "application/json"}`;
    const duplicate = Buffer.concat([Buffer.from(`HTTP/1.1 200 OK\r\n${first}\r\n${header}\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n\r\n`), body]);
    assert.throws(() => parseMoonshotHttpResponse(duplicate, 1024), /duplicate singleton header/u);
  }
  const earlyHints = Buffer.concat([Buffer.from("HTTP/1.1 103 Early Hints\r\nLink: </asset>; rel=preload\r\n\r\n", "ascii"), fixed]);
  assert.throws(() => parseMoonshotHttpResponse(earlyHints, 1024));
});

test("Moonshot transport enforces the exact provider-reported model and tolerates a missing model only for smoke", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-moonshot-model-"));
  const directory = join(root, "credential"); const credentialPath = join(directory, "provider-key");
  await mkdir(directory, { mode: 0o700 }); await writeFile(credentialPath, "temporary-test-key-abcdefghijklmnopqrstuvwxyz", { mode: 0o600 }); await chmod(directory, 0o700); await chmod(credentialPath, 0o600);
  try {
    const custody = await inspectWorkProviderCredentialCustody({ resolvedProvider: provider, policy, credentialPath, now: new Date("2026-08-21T16:40:00Z"), suffix: "abcdef123456" });
    const smokeInput = { schemaVersion: 1, model: "kimi-k3", messages: [{ role: "user", content: "Return exactly PIXEL_K3_SMOKE_OK and nothing else." }], maxOutputTokens: 256, reasoningEffort: "low" };
    const makeClaim = (suffix) => {
      const binding = createConnectivitySmokeBinding({ purpose: "moonshot-connectivity-smoke-v1", requestSha256: workProviderInputSha256(smokeInput), model: "kimi-k3" });
      const initial = createWorkProviderRunLedger({ resolvedProvider: provider, policy, taskId: "pixel-k3-model", model: "kimi-k3", binding, now: new Date("2026-08-21T16:40:01Z"), suffix });
      const idempotencyKey = providerIdempotencyKey({ task: "pixel-k3-model", turn: 1 });
      const ledger = claimWorkProviderRequest({ ledger: initial, policy, resolvedProvider: provider, idempotencyKey, inputSha256: workProviderInputSha256(smokeInput), estimatedInputTokens: 100, maxOutputTokens: 256, maxEstimatedCostMicros: 1000, now: new Date("2026-08-21T16:40:02Z") });
      return { ledger, idempotencyKey };
    };
    const respond = async (request, value) => { const body = Buffer.from(JSON.stringify(value)); return { statusCode: 200, headers: {}, body, networkBytes: request.body.length + body.length }; };
    // A mismatched/alias provider model fails known.
    const mismatchClaim = makeClaim("aaaaaaaa1111");
    const alias = { id: "m", object: "chat.completion", created: 1, model: "kimi-k3-alias", choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content: "PIXEL_K3_SMOKE_OK" } }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } };
    await assert.rejects(moonshotTransportTestOnly.executeMoonshotChatTurnWithExchange({ resolvedProvider: provider, policy, credentialHandle: custody.handle, ledger: mismatchClaim.ledger, idempotencyKey: mismatchClaim.idempotencyKey, input: smokeInput }, (r) => respond(r, alias)), (error) => error instanceof MoonshotWorkProviderTransportError && error.outcome === "failed-known" && /differs from the pinned/.test(error.message));
    // A missing model is tolerated for a connectivity smoke probe.
    const missingClaim = makeClaim("bbbbbbcc2222");
    const missing = { id: "m", object: "chat.completion", created: 1, choices: [{ index: 0, finish_reason: "stop", logprobs: null, message: { role: "assistant", content: "PIXEL_K3_SMOKE_OK" } }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } };
    const result = await moonshotTransportTestOnly.executeMoonshotChatTurnWithExchange({ resolvedProvider: provider, policy, credentialHandle: custody.handle, ledger: missingClaim.ledger, idempotencyKey: missingClaim.idempotencyKey, input: smokeInput }, (r) => respond(r, missing));
    assert.equal(result.assistant.providerModel, null);
    assert.equal(result.assistant.message.content, "PIXEL_K3_SMOKE_OK");
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("production Moonshot transport rejects a caller-supplied exchange and never invokes it", async () => {
  let invoked = false;
  await assert.rejects(() => executeMoonshotChatTurn({ resolvedProvider: {}, exchange: async () => { invoked = true; } }), /cannot accept a caller-supplied exchange/u);
  await assert.rejects(() => executeMoonshotChatTurn({ resolvedProvider: {}, testSeam: true }), /cannot accept a caller-supplied exchange/u);
  assert.equal(invoked, false);
});
