import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { compileWorkCodexPreview } from "../deploy/work-codex-provider/compiler.mjs";
import { decodeWorkCodexContainerFrame, encodeWorkCodexContainerFrame, WorkCodexContainerProtocolError } from "../deploy/work-codex-provider/container-protocol.mjs";
import { canonical } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T23:00:00Z"), sha = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");
const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));
const outputSchema = JSON.parse(await readFile(new URL("../schemas/work-codex-output-v1.schema.json", import.meta.url), "utf8"));

function payload(api = false) {
  const policy = structuredClone(policyTemplate); policy.enabled = true; policy.taskClasses["structural-review"].enabled = true;
  if (api) {
    policy.provider.authMode = "api-key"; policy.transport.allowedHosts = ["api.openai.com"];
    policy.credentialCustody = { mode: "broker-private-api-key-file", credentialId: "codex-api-key", maxBytes: 8192, mutableRefresh: false, runtimeAccess: "stdin-only-ephemeral-login", materialProjectedToPixel: false, ambientEnvironment: false };
    policy.provider.billing = { mode: "metered", boundary: "Separately billed OpenAI API Platform usage; ChatGPT subscription access does not cover this route.", currency: "USD", inputMicrosPerMillionTokens: 1, outputMicrosPerMillionTokens: 2, source: "https://openai.com/api/pricing/", asOf: "2026-08-10", maxEstimatedCostMicros: 1000000 };
  }
  const content = "A sanitized structural fixture.";
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-request-v1.schema.json", schemaVersion: 1,
    requestId: `workcodexrequest-${baseTime}-000000000001`, createdAt: new Date(baseTime).toISOString(), expiresAt: new Date(baseTime + 20 * 60000).toISOString(),
    jobId: `work-${baseTime}-000000000002`, checkpointSha256: sha("checkpoint"), ownerId: "owner", clientId: "client", taskClass: "structural-review", egressMode: "structural-only", classification: "internal", dataCategories: ["structural"],
    localAttempt: { attempts: 2, outcome: "completed-needs-review", reasonCodes: ["quality-check"], receiptSha256: sha("attempt") }, objective: "Review the fixture.", constraints: ["Advisory only."], acceptanceCriteria: ["Return one result."], sensitiveTerms: [],
    documents: [{ documentId: "private-document", kind: "structure", language: "text", content, contentSha256: sha(content) }], maxOutputTokens: 1024,
    boundary: "Private local request for one sanitized Codex work proposal. Compilation does not authorize or invoke a provider, expose credentials, or grant any external effect.",
  };
  const { plan } = compileWorkCodexPreview({ request, policy, now: new Date(baseTime + 1000), suffix: "000000000003", mappingNonce: sha("mapping") });
  return { capsule: plan.capsule, outputSchema, provider: plan.provider, limits: plan.limits, planSha256: sha(plan) };
}

test("container frame separates bounded ChatGPT and API credentials from canonical task data", () => {
  for (const [api, credential] of [[false, '{"auth_mode":"chatgpt","tokens":{"fixture":"private"}}'], [true, "sk-fixture_abcdefghijklmnopqrstuvwxyz123456"]]) {
    const value = payload(api), frame = encodeWorkCodexContainerFrame({ payload: value, credential }), decoded = decodeWorkCodexContainerFrame(frame);
    assert.equal(decoded.authMode, api ? "api-key" : "chatgpt"); assert.deepEqual(decoded.payload, value); assert.equal(decoded.credential.toString("utf8"), credential);
    const headerEnd = frame.indexOf(0x0a, Buffer.byteLength("PIXEL-CODEX-CONTAINER-V1\n"));
    assert.equal(frame.subarray(0, headerEnd).toString("utf8").includes(credential), false);
  }
});

test("container frame rejects mode confusion, noncanonical tasks, malformed credentials, and 1,024 length mutations", () => {
  const value = payload(), credential = '{"auth_mode":"chatgpt","fixture":"private"}', original = encodeWorkCodexContainerFrame({ payload: value, credential });
  for (const invalid of ["{}", '{"auth_mode":"api-key"}', Buffer.alloc(262145, 65)]) assert.throws(() => encodeWorkCodexContainerFrame({ payload: value, credential: invalid }), WorkCodexContainerProtocolError);
  const api = payload(true); assert.throws(() => encodeWorkCodexContainerFrame({ payload: api, credential: "contains whitespace and is long enough" }), /one-line/u);
  const magic = Buffer.from("PIXEL-CODEX-CONTAINER-V1\n"), headerEnd = original.indexOf(0x0a, magic.length), header = JSON.parse(original.subarray(magic.length, headerEnd).toString("utf8")), body = original.subarray(headerEnd + 1);
  for (let index = 0; index < 1024; index += 1) {
    const mutatedHeader = { ...header };
    if (index % 2 === 0) mutatedHeader.credentialBytes += index + 1; else mutatedHeader.payloadBytes += index + 1;
    const mutated = Buffer.concat([magic, Buffer.from(`${canonical(mutatedHeader)}\n`), body]);
    assert.throws(() => decodeWorkCodexContainerFrame(mutated), WorkCodexContainerProtocolError, `length mutation ${index}`);
  }
  const credentialBytes = body.subarray(0, header.credentialBytes), payloadBytes = body.subarray(header.credentialBytes), widenedPayload = Buffer.concat([Buffer.from(" "), payloadBytes]);
  const noncanonicalHeader = Buffer.from(`${canonical({ ...header, payloadBytes: widenedPayload.length })}\n`);
  assert.throws(() => decodeWorkCodexContainerFrame(Buffer.concat([magic, noncanonicalHeader, credentialBytes, widenedPayload])), /not canonical/u);
});
