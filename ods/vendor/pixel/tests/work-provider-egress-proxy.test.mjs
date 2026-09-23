import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { canonical } from "../scripts/lib/work-contract.mjs";
import { isPublicWorkProviderEgressAddress, parseBoundWorkProviderPrivatePolicy, parseWorkProviderConnectRequest, WorkProviderEgressProxyError } from "../deploy/work-provider/egress-proxy.mjs";

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

test("provider proxy binds canonical private policy to its startup digest", () => {
  const text = canonical(policy); const digest = createHash("sha256").update(text).digest("hex");
  const parsed = parseBoundWorkProviderPrivatePolicy(Buffer.from(text), digest);
  assert.equal(parsed.policy.providerId, "moonshot-kimi"); assert.equal(parsed.resolvedProvider.profile.baseUrl, "https://api.moonshot.ai/v1");
  assert.throws(() => parseBoundWorkProviderPrivatePolicy(Buffer.from(`${text}\n `), digest), WorkProviderEgressProxyError);
});

test("provider proxy permits only canonical CONNECT to the exact profile host", () => {
  const good = Buffer.from("CONNECT api.moonshot.ai:443 HTTP/1.1\r\nHost: api.moonshot.ai:443\r\n\r\n", "ascii");
  assert.deepEqual(parseWorkProviderConnectRequest(good, policy), { host: "api.moonshot.ai", port: 443 });
  const nodeClient = Buffer.from("CONNECT api.moonshot.ai:443 HTTP/1.1\r\nHost: api.moonshot.ai:443\r\nConnection: close\r\n\r\n", "ascii");
  assert.deepEqual(parseWorkProviderConnectRequest(nodeClient, policy), { host: "api.moonshot.ai", port: 443 });
  assert.throws(() => parseWorkProviderConnectRequest(Buffer.from("CONNECT api.moonshot.ai:443 HTTP/1.1\r\nHost: api.moonshot.ai:443\r\nConnection: keep-alive\r\n\r\n", "ascii"), policy), /fixed close/u);
  for (const target of ["example.com:443", "127.0.0.1:443", "api.moonshot.ai:80"]) {
    const request = Buffer.from(`CONNECT ${target} HTTP/1.1\r\nHost: ${target}\r\n\r\n`, "ascii");
    assert.throws(() => parseWorkProviderConnectRequest(request, policy), /not allowlisted/u);
  }
});

test("provider proxy DNS boundary rejects private, special-use, and documentation addresses", () => {
  for (const address of ["127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.1.1", "203.0.113.4", "2001::1", "2001:2::1", "2001:10::1", "2001:20::1", "2001:db8::1", "2002::1", "3fff::1", "::ffff:8.8.8.8"]) assert.equal(isPublicWorkProviderEgressAddress(address), false);
  assert.equal(isPublicWorkProviderEgressAddress("8.8.8.8"), true);
  assert.equal(isPublicWorkProviderEgressAddress("2606:4700:4700::1111"), true);
});
