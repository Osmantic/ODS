import { createHash } from "node:crypto";
import { canonical } from "../../scripts/lib/work-contract.mjs";

export const sha = (value) => createHash("sha256").update(canonical(value)).digest("hex");

const REQUEST_BOUNDARY = "Content-free local-first router request. It declares only task class, data classification, sensitive categories, context/tool/vision need, input/output token estimates, and a cost ceiling. It carries no prompt, response, reasoning, tool arguments, credential, path, or owner content.";
const PRIVATE_BOUNDARY = "Owner-private remote work-provider policy. It may enable only the exact selected provider, credential custody, egress hosts, data classes, and budgets; it grants no merge, push, deployment, publication, external-message, production-credential, cloud-fallback, policy-mutation, verifier-selection, completion, or security-testing authority.";
const POLICY_BOUNDARY = "Owner-private content-free local-first work-provider router policy. It pins the ordered provider preference and the exact enabled remote private-policy SHA-256 set; remote providers stay disabled by default. It grants no execution, credential, network, merge, deploy, publish, external-message, security-testing, or policy-mutation authority.";
const QUALIFICATION_BOUNDARY = "Content-free hash-bound provider capability qualification evidence. It attests an exact capability envelope and expiry for one provider without prompt, response, reasoning, tool arguments, credential, path, or execution authority. connectivity-smoke attests transport reachability only and is never semantic task qualification.";

function identity(prefix, isoTime, suffix = "aaaaaaaaaaaa") {
  return `${prefix}-${String(Date.parse(isoTime)).padStart(13, "0")}-${suffix}`;
}

export function makeRequest(overrides = {}) {
  const createdAt = overrides.createdAt ?? "2026-08-21T12:00:00Z";
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-router-request-v1.schema.json",
    schemaVersion: 1,
    requestId: identity("workproviderrequest", createdAt),
    createdAt,
    mode: "policy-router",
    taskClass: "structural-review",
    dataClassification: "internal",
    sensitiveCategories: [],
    contextNeed: "workspace",
    toolsNeed: "standard",
    visionNeed: false,
    inputTokenEstimate: 10000,
    outputTokenEstimate: 2000,
    costCeilingMicros: 500000,
    boundary: REQUEST_BOUNDARY,
    ...overrides,
  };
}

export function makePrivatePolicy(providerId, overrides = {}) {
  const createdAt = overrides.createdAt ?? "2026-08-21T00:00:00Z";
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-private-policy-v1.schema.json",
    schemaVersion: 1,
    policyId: identity("workproviderpolicy", createdAt),
    createdAt,
    providerId,
    enabled: true,
    credentialCustody: { credentialId: "provider-key-id", fileName: "provider-key", maxBytes: 2048 },
    transport: { allowedHosts: ["api.moonshot.ai:443"], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
    dataPolicy: { allowedClassifications: ["internal-source", "public"], neverEgressCategories: ["authentication-material", "credentials", "private-keys", "regulated-records", "session-tokens", "unapproved-owner-data"] },
    budgets: { maxRequestsPerRun: 10, maxInputTokensPerRun: 2000000, maxOutputTokensPerRun: 400000, maxNetworkBytesPerRun: 1048576, maxRequestSeconds: 300, maxEstimatedCostMicrosPerRun: 1000000, maxEstimatedCostMicrosPerDay: 5000000 },
    fallback: { mode: "local-only", cloudToCloudAllowed: false, reconcileBeforeRetry: true },
    verification: { independentLocalVerifierRequired: true, verifierMayBeWorker: false, failClosedIfUnavailable: true },
    authority: { merge: false, push: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false },
    boundary: PRIVATE_BOUNDARY,
    ...overrides,
  };
}

export function makeRouterPolicy({ providerId = "moonshot-kimi", enabled = null, preference = null, mode = "policy-router", allowedModes = ["explicit-provider", "local-only", "policy-router"], qualification = null, privatePolicy = null, overrides = {} } = {}) {
  const privatePolicyDoc = privatePolicy ?? makePrivatePolicy(providerId);
  const effectiveProviderId = privatePolicy ? privatePolicy.providerId : providerId;
  const entries = enabled ?? [{ providerId: effectiveProviderId, privatePolicySha256: sha(privatePolicyDoc) }];
  const createdAt = overrides.createdAt ?? "2026-08-21T00:00:00Z";
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-router-policy-v1.schema.json",
    schemaVersion: 1,
    routerPolicyId: identity("workproviderrouterpolicy", createdAt),
    createdAt,
    mode,
    allowedModes,
    preference: preference ?? ["local", providerId],
    enabledRemoteProviders: entries,
    qualification: qualification ?? { required: true, maxAgeSeconds: 86400, semanticTaskClasses: ["structural-review", "patch-proposal", "failure-triage"] },
    authority: { execution: false, credential: false, network: false, merge: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false, policyMutation: false },
    boundary: POLICY_BOUNDARY,
    ...overrides,
  };
}

const DEFAULT_QUALIFIED_MODEL = Object.freeze({ "moonshot-kimi": "kimi-k3", local: "DeepSeek-V4-Flash-0731" });

export function makeQualification(providerId, overrides = {}) {
  const attestedAt = overrides.attestedAt ?? "2026-08-21T00:00:00Z";
  const doc = {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-router-qualification-v1.schema.json",
    schemaVersion: 1,
    qualificationId: identity("workproviderqualification", attestedAt),
    providerId,
    model: DEFAULT_QUALIFIED_MODEL[providerId] ?? "kimi-k3",
    attestedAt,
    expiresAt: "2026-08-22T00:00:00Z",
    attestationKind: "semantic-capability",
    capability: {
      taskClasses: ["failure-triage", "patch-proposal", "structural-review"],
      contextNeed: "codebase",
      toolsNeed: "compute",
      visionNeed: true,
      inputTokenMax: 8000000,
      outputTokenMax: 2000000,
      costMicrosPerRunMax: 1000000000,
    },
    pricing: { inputMicrosPerMillionTokens: 1000000, outputMicrosPerMillionTokens: 3000000, fixedMicrosPerRun: 50000 },
    evidenceSha256: "a".repeat(64),
    boundary: QUALIFICATION_BOUNDARY,
    ...overrides,
    capability: {
      taskClasses: ["failure-triage", "patch-proposal", "structural-review"],
      contextNeed: "codebase",
      toolsNeed: "compute",
      visionNeed: true,
      inputTokenMax: 8000000,
      outputTokenMax: 2000000,
      costMicrosPerRunMax: 1000000000,
      ...(overrides.capability ?? {}),
    },
    pricing: {
      inputMicrosPerMillionTokens: 1000000,
      outputMicrosPerMillionTokens: 3000000,
      fixedMicrosPerRun: 50000,
      ...(overrides.pricing ?? {}),
    },
    qualificationSha256: "b".repeat(64),
  };
  const { qualificationSha256, ...withoutSha } = doc;
  doc.qualificationSha256 = sha(withoutSha);
  return doc;
}

export function makeLocalQualification(overrides = {}) {
  return makeQualification("local", {
    capability: {
      contextNeed: "codebase",
      toolsNeed: "compute",
      visionNeed: true,
      inputTokenMax: 5000000,
      outputTokenMax: 1000000,
      costMicrosPerRunMax: 1000000000,
    },
    pricing: { inputMicrosPerMillionTokens: 0, outputMicrosPerMillionTokens: 0, fixedMicrosPerRun: 0 },
    ...overrides,
  });
}
