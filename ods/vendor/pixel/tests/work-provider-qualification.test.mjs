import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, open, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { MoonshotWorkProviderTransportError } from "../deploy/work-provider/moonshot-transport.mjs";
import {
  admitNeutralCorpusRequest, buildNeutralCorpus, findNeutralCorpusCase,
  LONG_CONTEXT_MIN_INPUT_TOKENS, LONG_CONTEXT_REQUIRED_REPLY, LONG_CONTEXT_SENTINEL,
  matchesNeutralToolArguments, NEUTRAL_CORPUS_VERSION, QUALIFICATION_LANES, QUALIFICATION_TASKS,
  OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS, OUTPUT_CAPACITY_REPLY, OUTPUT_CAPACITY_REPORTED_TOKENS,
  TOOL_CHOICE_FINAL_REPLY, TOOL_CHOICE_SYNTHETIC_RESULT, TOOL_CHOICE_TOOL_ARGUMENTS, TOOL_CHOICE_TOOL_NAME,
  validateNeutralToolChoiceReplay,
} from "../deploy/work-provider/neutral-corpus.mjs";
import { PATCH_SUBMISSION_TOOL, PATCH_SUBMISSION_TOOL_CHOICE, PATCH_SUBMISSION_TOOL_NAME } from "../deploy/work-provider/patch-extraction.mjs";
import { workProviderQualificationTestSeam } from "../deploy/work-provider/qualification-runner.mjs";
import { gradeDeterministic } from "../deploy/work-provider/grading.mjs";
import { promoteQualificationTrial, ATTESTED_ROUTER_TASK_CLASSES } from "../deploy/work-provider/qualification-promotion.mjs";
import { validateQualification } from "../deploy/work-provider-router/qualification.mjs";
import { routeWorkProvider } from "../deploy/work-provider-router/router.mjs";
import { canonical, validateWorkProviderRouterQualification } from "../scripts/lib/work-contract.mjs";
import { makePrivatePolicy, makeRequest, makeRouterPolicy, sha } from "./fixtures/work-provider-router.mjs";

const LOCAL_MODEL = "DeepSeek-V4-Flash-0731";
const K3_MODEL = "kimi-k3";
const REASONING = "K3_REASONING_VERBATIM_0123456789abcdef";
// Realistic long-context reported usage: the corpus input is above the required floor.
const LONG_CONTEXT_REPORTED_INPUT_TOKENS = 38000;

const PATCH = "diff --git a/fixture.mjs b/fixture.mjs\n--- a/fixture.mjs\n+++ b/fixture.mjs\n@@ -1,3 +1,3 @@\n export function add(a, b) {\n-  return a - b;\n+  return a + b;\n }\n";

const privatePolicy = {
  $schema: "https://osmantic.com/pixel/schemas/work-provider-private-policy-v1.schema.json", schemaVersion: 1,
  policyId: "workproviderpolicy-1787328000000-abcdef123456", createdAt: "2026-08-21T16:00:00Z", providerId: "moonshot-kimi", enabled: true,
  credentialCustody: { credentialId: "moonshot-test", fileName: "provider-key", maxBytes: 8192 },
  transport: { allowedHosts: ["api.moonshot.ai:443"], proxyRequired: true, directNetworkAllowed: false, denyIpLiterals: true, denyPrivateAddressResolution: true, denyPlainHttp: true, logRequestBodies: false, logResponseBodies: false },
  dataPolicy: { allowedClassifications: ["internal-source", "owner-approved-source", "public"], neverEgressCategories: ["authentication-material", "credentials", "private-keys", "regulated-records", "session-tokens", "unapproved-owner-data"] },
  budgets: { maxRequestsPerRun: 40, maxInputTokensPerRun: 400000, maxOutputTokensPerRun: 40000, maxNetworkBytesPerRun: 2097152, maxRequestSeconds: 60, maxEstimatedCostMicrosPerRun: 1000000, maxEstimatedCostMicrosPerDay: 5000000 },
  fallback: { mode: "local-only", cloudToCloudAllowed: false, reconcileBeforeRetry: true }, verification: { independentLocalVerifierRequired: true, verifierMayBeWorker: false, failClosedIfUnavailable: true },
  authority: { merge: false, push: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false },
  boundary: "Owner-private remote work-provider policy. It may enable only the exact selected provider, credential custody, egress hosts, data classes, and budgets; it grants no merge, push, deployment, publication, external-message, production-credential, cloud-fallback, policy-mutation, verifier-selection, completion, or security-testing authority.",
};

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "pixel-qualification-"));
  const ledgerRoot = join(root, "ledger");
  await mkdir(ledgerRoot, { mode: 0o700 });
  return { root, ledgerRoot };
}

async function moonshotFixture() {
  const root = await mkdtemp(join(tmpdir(), "pixel-qualification-ms-"));
  const credentialDirectory = join(root, "credential"), credentialPath = join(credentialDirectory, "provider-key"), policyPath = join(root, "policy.json"), ledgerRoot = join(root, "ledger");
  await mkdir(credentialDirectory, { mode: 0o700 }); await mkdir(ledgerRoot, { mode: 0o700 });
  await writeFile(credentialPath, "temporary-test-key-abcdefghijklmnopqrstuvwxyz", { mode: 0o600 });
  const text = canonical(privatePolicy); await writeFile(policyPath, `${text}\n`, { mode: 0o600 });
  await chmod(root, 0o700); await chmod(credentialDirectory, 0o700); await chmod(credentialPath, 0o600); await chmod(policyPath, 0o600);
  const sha256 = createHash("sha256").update(text).digest("hex");
  return { root, ledgerRoot, policyPath, sha256, credentialPath };
}

function toolChoiceFirstResponse(lane) {
  const callId = "k3tool_0001";
  const toolCall = { id: callId, name: TOOL_CHOICE_TOOL_NAME, arguments: TOOL_CHOICE_TOOL_ARGUMENTS };
  if (lane === "moonshot-kimi") {
    return {
      message: {
        role: "assistant", content: null, toolCalls: [toolCall],
        providerState: { protocol: "openai-chat-completions", assistantMessage: { role: "assistant", content: null, tool_calls: [{ id: callId, type: "function", function: { name: TOOL_CHOICE_TOOL_NAME, arguments: TOOL_CHOICE_TOOL_ARGUMENTS } }], reasoning_content: REASONING }, finishReason: "tool_calls" },
      },
      finishReason: "tool_calls", usage: { inputTokens: 40, outputTokens: 9, totalTokens: 49 }, providerModel: K3_MODEL,
    };
  }
  return {
    message: {
      role: "assistant", content: null, toolCalls: [toolCall],
      providerState: { protocol: "local-openai-compatible", assistantMessage: { role: "assistant", content: null, tool_calls: [{ id: callId, type: "function", function: { name: TOOL_CHOICE_TOOL_NAME, arguments: TOOL_CHOICE_TOOL_ARGUMENTS } }] }, finishReason: "tool_calls" },
    },
    finishReason: "tool_calls", usage: { inputTokens: 40, outputTokens: 9, totalTokens: 49 }, providerModel: LOCAL_MODEL,
  };
}

function responseFor(model, caseEntry, overrides = {}) {
  switch (caseEntry.task) {
    case "structural-review":
      return { message: { role: "assistant", content: '{"exportCount":3,"defectFunction":"add","defectKind":"wrong-operator"}' }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 5, totalTokens: 25 }, providerModel: model, ...overrides };
    case "failure-triage":
      return { message: { role: "assistant", content: '{"classification":"transient","retried":true,"resolution":"retry-succeeded"}' }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 5, totalTokens: 25 }, providerModel: model, ...overrides };
    case "patch-proposal":
      if (model === K3_MODEL) {
        const callId = "k3patch_0001";
        const call = { id: callId, name: PATCH_SUBMISSION_TOOL_NAME, arguments: JSON.stringify({ patch: PATCH }) };
        return {
          message: {
            role: "assistant", content: null, toolCalls: [call],
            providerState: { protocol: "openai-chat-completions", assistantMessage: { role: "assistant", content: null, tool_calls: [{ id: callId, type: "function", function: { name: call.name, arguments: call.arguments } }], reasoning_content: REASONING }, finishReason: "tool_calls" },
          },
          finishReason: "tool_calls", usage: { inputTokens: 30, outputTokens: 8, totalTokens: 38 }, providerModel: model, ...overrides,
        };
      }
      return { message: { role: "assistant", content: PATCH }, finishReason: "stop", usage: { inputTokens: 30, outputTokens: 8, totalTokens: 38 }, providerModel: model, ...overrides };
    case "long-context-sentinel":
      return { message: { role: "assistant", content: LONG_CONTEXT_REQUIRED_REPLY }, finishReason: "stop", usage: { inputTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS, outputTokens: 3, totalTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS + 3 }, providerModel: model, ...overrides };
    case "output-capacity":
      return { message: { role: "assistant", content: OUTPUT_CAPACITY_REPLY }, finishReason: "stop", usage: { inputTokens: 40, outputTokens: OUTPUT_CAPACITY_REPORTED_TOKENS, totalTokens: 40 + OUTPUT_CAPACITY_REPORTED_TOKENS }, providerModel: model, ...overrides };
    default:
      throw new Error("unknown single-request task");
  }
}

function passingTransport(lane, model) {
  return async ({ input }) => {
    const admitted = admitNeutralCorpusRequest({ lane, model, input });
    if (!admitted) throw new Error("transport received an input outside the neutral corpus");
    if (admitted.kind === "tool-choice-replay") {
      return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: model }, providerRequestId: "qual-tool-2", networkBytes: 200 };
    }
    const caseEntry = findNeutralCorpusCase({ lane, model, input });
    if (caseEntry.task === "tool-choice-continuation") {
      return { assistant: toolChoiceFirstResponse(lane), providerRequestId: "qual-tool-1", networkBytes: 200 };
    }
    return { assistant: responseFor(model, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
  };
}

function localArgv(root, trials) {
  const argv = ["--lane", "local-only", "--corpus-version", NEUTRAL_CORPUS_VERSION, "--ledger-root", root, "--qualification-report", join(root, "..", "qualification-artifact.json")];
  if (trials !== undefined) argv.push("--trials", String(trials));
  return argv;
}

function moonshotArgv(value, trials) {
  const argv = ["--lane", "moonshot-kimi", "--corpus-version", NEUTRAL_CORPUS_VERSION, "--policy", value.policyPath, "--sha256", value.sha256, "--credential", value.credentialPath, "--proxy-route", "loopback", "--ledger-root", value.ledgerRoot, "--qualification-report", join(value.root, "qualification-artifact.json")];
  if (trials !== undefined) argv.push("--trials", String(trials));
  return argv;
}

test("the neutral corpus is versioned, lane-bound, and the long-context case proves retrieval", () => {
  assert.equal(NEUTRAL_CORPUS_VERSION, "8");
  assert.deepEqual([...QUALIFICATION_LANES], ["local-only", "moonshot-kimi", "openai", "anthropic", "openrouter"]);
  assert.deepEqual([...QUALIFICATION_TASKS], ["structural-review", "patch-proposal", "tool-choice-continuation", "long-context-sentinel", "failure-triage", "output-capacity"]);
  const corpus = buildNeutralCorpus({ lane: "moonshot-kimi", model: K3_MODEL });
  const tasks = corpus.cases.map((entry) => entry.task);
  for (const required of ["structural-review", "patch-proposal", "tool-choice-continuation", "long-context-sentinel", "failure-triage", "output-capacity"]) {
    assert.ok(tasks.includes(required), `corpus must include ${required}`);
  }
  for (const entry of corpus.cases) {
    const requestHash = entry.requestCount === 1 ? entry.inputSha256 : entry.firstRequestSha256;
    assert.match(requestHash, /^[a-f0-9]{64}$/u);
  }
  const longContext = corpus.cases.find((entry) => entry.task === "long-context-sentinel");
  const content = longContext.input.messages[0].content;
  assert.equal((content.match(LONG_CONTEXT_SENTINEL) ?? []).length, 1, "the unique sentinel must appear exactly once near the beginning");
  assert.ok(content.indexOf(LONG_CONTEXT_SENTINEL) < content.length / 4, "the sentinel must appear near the beginning");
  // The end instruction must refer to the sentinel without repeating its value.
  assert.equal(content.includes(LONG_CONTEXT_REQUIRED_REPLY), false, "the instruction must not repeat the sentinel value");
  const estTokens = Math.ceil(Buffer.byteLength(JSON.stringify(longContext.input), "utf8") / 4);
  assert.ok(estTokens >= Math.ceil(LONG_CONTEXT_MIN_INPUT_TOKENS * 1.5), "long-context input must retain estimator margin above the real-token floor");
  const toolChoice = corpus.cases.find((entry) => entry.task === "tool-choice-continuation");
  assert.equal(toolChoice.firstRequest.toolChoice, "auto", "tool selection must be explicitly enabled without forcing one tool");
  assert.equal(toolChoice.followUpUser.includes(TOOL_CHOICE_FINAL_REPLY), false, "the continuation prompt must not disclose the expected semantic answer");
  assert.equal(toolChoice.followUpUser.includes(TOOL_CHOICE_SYNTHETIC_RESULT), false, "the continuation prompt must require deriving the prior tool result");
  const localCorpus = buildNeutralCorpus({ lane: "local-only", model: LOCAL_MODEL });
  for (const entry of localCorpus.cases) {
    const request = entry.requestCount === 1 ? entry.input : entry.firstRequest;
    assert.equal(typeof request.temperature, "number", `${entry.task} must bind its local sampling temperature explicitly`);
  }
  for (const entry of corpus.cases) {
    const request = entry.requestCount === 1 ? entry.input : entry.firstRequest;
    assert.equal(Object.hasOwn(request, "temperature"), false, `${entry.task} must omit K3's fixed temperature`);
  }
  const localPatch = localCorpus.cases.find((entry) => entry.task === "patch-proposal").input;
  const k3Patch = corpus.cases.find((entry) => entry.task === "patch-proposal").input;
  assert.equal(localPatch.reasoningEffort, "none", "local patch proof must disable hidden reasoning");
  assert.equal(localPatch.temperature, 0, "local patch proof must request deterministic sampling");
  assert.equal(localPatch.tools, undefined, "local patch proof must keep the deterministic raw-diff request with no tools");
  assert.equal(k3Patch.reasoningEffort, "high", "K3 patch proof must use its supported higher reasoning effort for strict diff construction");
  assert.equal(Object.hasOwn(k3Patch, "temperature"), false, "K3 fixed-temperature API must not receive a temperature override");
  assert.deepEqual(k3Patch.tools, [PATCH_SUBMISSION_TOOL], "K3 patch proof must present the exact closed submit_patch tool");
  assert.equal(k3Patch.toolChoice, PATCH_SUBMISSION_TOOL_CHOICE, "K3 patch proof must use its supported auto tool selection with one available tool");
  const k3PatchPrompt = k3Patch.messages[0].content;
  assert.ok(k3PatchPrompt.includes("diff --git a/fixture.mjs b/fixture.mjs"), "K3 patch prompt must teach the exact header shape");
  assert.ok(k3PatchPrompt.includes("@@ -1,3 +1,3 @@"), "K3 patch prompt must teach the single-hunk shape");
  assert.ok(k3PatchPrompt.includes("line 5 must begin with one ASCII space"), "K3 patch prompt must bind the opening context-line prefix");
  assert.ok(k3PatchPrompt.includes("line 8 must begin with one ASCII space"), "K3 patch prompt must bind the closing context-line prefix");
  assert.ok(k3PatchPrompt.includes(PATCH_SUBMISSION_TOOL_NAME), "K3 patch prompt must direct the exact submit_patch tool call");
  assert.equal(k3PatchPrompt.includes(PATCH), false, "K3 patch prompt must not embed the exact expected diff");
  const localOutput = localCorpus.cases.find((entry) => entry.task === "output-capacity").input;
  const k3Output = corpus.cases.find((entry) => entry.task === "output-capacity").input;
  assert.equal(localOutput.reasoningEffort, "none", "local usable-output proof must not spend its ceiling on hidden reasoning");
  assert.equal(localOutput.maxOutputTokens, 2048, "local usable-output proof retains its proven bounded ceiling");
  assert.equal(k3Output.reasoningEffort, "low", "K3 must retain its supported explicit reasoning effort");
  assert.equal(k3Output.maxOutputTokens, 4096, "K3 needs bounded headroom for preserved-thinking variance");
  const k3MaxOutputTokensAtFiveTrials = corpus.cases.reduce((total, entry) => total
    + (entry.requestCount === 1 ? entry.input.maxOutputTokens : entry.firstRequest.maxOutputTokens + entry.firstRequest.maxOutputTokens), 0) * 5;
  assert.equal(k3MaxOutputTokensAtFiveTrials, 48640);
  assert.ok(k3MaxOutputTokensAtFiveTrials <= 50000, "the five-trial K3 corpus must fit the bound owner policy");
  const k3RequiredRequestsAtFiveTrials = corpus.cases.reduce((total, entry) => total + entry.requestCount, 0) * 5;
  const k3EstimatedInitialInputTokensAtFiveTrials = corpus.cases.reduce((total, entry) => total
    + workProviderQualificationTestSeam.internals.estimateInputTokens(entry.requestCount === 1 ? entry.input : entry.firstRequest) * entry.requestCount, 0) * 5;
  assert.equal(k3RequiredRequestsAtFiveTrials, 35);
  assert.equal(k3EstimatedInitialInputTokensAtFiveTrials, 290000);
  assert.ok(k3EstimatedInitialInputTokensAtFiveTrials <= 500000, "the five-trial K3 corpus must fit the bound owner input policy with margin");
});

test("qualification diagnostics map fixed transport failures to closed content-free codes", () => {
  const classify = workProviderQualificationTestSeam.internals.contentFreeFailureCode;
  const cases = [
    ["Moonshot proxy connection failed", "proxy-connection"],
    ["Moonshot proxy refused the exact provider tunnel", "proxy-refused"],
    ["Moonshot TLS transport failed", "tls"],
    ["Moonshot response parsing failed", "response-framing"],
    ["provider response has invalid chunk framing", "response-framing"],
    ["provider response is missing its final chunk", "response-framing"],
    ["provider HTTP response content length is invalid", "response-framing"],
    ["provider HTTP response headers are invalid", "response-metadata"],
    ["Moonshot response body is not strict UTF-8", "response-utf8"],
    ["Moonshot response body is not strict JSON: duplicate key", "response-json"],
    ["Moonshot response adapter validation failed: unsupported field provider-controlled", "response-adapter"],
    ["Moonshot response omitted exact token usage", "response-usage"],
    ["Moonshot provider returned a model that differs from the pinned run model", "response-model"],
    ["Moonshot request identifier is invalid", "response-identifier"],
    ["Moonshot proxy response metadata is invalid", "response-metadata"],
    ["Moonshot provider returned HTTP 429", "provider-http"],
    ["Moonshot proxy request timed out", "transport-timeout"],
    ["opaque provider-controlled detail", "transport-unknown"],
  ];
  for (const [message, expected] of cases) assert.equal(classify(new Error(message)), expected);
  assert.equal(classify(new MoonshotWorkProviderTransportError("opaque", "uncertain", "response-metadata")), "response-metadata", "typed transport categories must take precedence over message guessing");
});

test("a passing local-only trial bootstraps per-request evidence and a sealed semantic qualification", async () => {
  const value = await fixture();
  try {
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), passingTransport("local-only", LOCAL_MODEL));
    assert.equal(receipt.status, "qualified"); assert.equal(receipt.qualified, true);
    assert.equal(receipt.distinctCaseCount, 6); assert.equal(receipt.requiredRepetitions, 6 * 3);
    assert.equal(receipt.passedRepetitions, 6 * 3); assert.equal(receipt.failedRepetitions, 0); assert.equal(receipt.uncertainRepetitions, 0);
    assert.equal(receipt.lane, "local-only"); assert.equal(receipt.model, LOCAL_MODEL);
    assert.equal(receipt.trials, 3);
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.status, "completed"); assert.equal(ledger.binding.kind, "qualification-trial");
    // 3 trials x (1+1+2+1+1+1) requests
    assert.equal(ledger.requests.length, 3 * 7);
    assert.equal(ledger.requests.every((request) => request.state === "succeeded"), true);
    assert.equal(ledger.requests.every((request) => request.attempts === 1), true);
    const ledgerText = JSON.stringify(ledger);
    assert.equal(ledgerText.includes("return a - b"), false);
    assert.equal(ledgerText.includes(LONG_CONTEXT_SENTINEL), false);
    assert.equal(ledgerText.includes("reasoning_content"), false);
    assert.equal(receipt.attestation.perCase.length, 6 * 3);
    const keys = new Set(receipt.attestation.perCase.map((e) => `${e.caseId}:${e.trialIndex}`));
    assert.equal(keys.size, 18);
    for (const evidence of receipt.attestation.perCase) {
      assert.equal(evidence.status, "passed"); assert.equal(evidence.gradePass, true);
      assert.match(evidence.caseSha256, /^[a-f0-9]{64}$/u);
      assert.ok(evidence.requests.length >= 1 && evidence.requests.length <= 2);
      for (const request of evidence.requests) {
        assert.match(request.inputSha256, /^[a-f0-9]{64}$/u);
        assert.equal(request.state, "succeeded");
        assert.equal(typeof request.requestIndex, "number");
      }
      if (evidence.task === "long-context-sentinel") {
        assert.ok(evidence.requests[0].inputTokens >= LONG_CONTEXT_MIN_INPUT_TOKENS);
      }
      if (evidence.task === "tool-choice-continuation") {
        assert.equal(evidence.requests.length, 2);
        assert.deepEqual(evidence.requests.map((r) => r.requestIndex), [1, 2]);
      }
    }
    // Sealed semantic qualification is produced, validates, and drives the router.
    const qual = receipt.semanticQualification;
    assert.ok(qual, "a qualified closed trial must promote a semantic-capability document");
    assert.equal(qual.attestationKind, "semantic-capability");
    assert.deepEqual(validateWorkProviderRouterQualification(qual), []);
    validateQualification(qual, new Date());
    assert.equal(qual.evidenceSha256, createHash("sha256").update(canonical(receipt.attestation)).digest("hex"));
    const { qualificationSha256, ...withoutSha } = qual;
    assert.equal(qual.qualificationSha256, sha(withoutSha));
    assert.ok(Date.parse(qual.expiresAt) > Date.parse(qual.attestedAt));
    assert.ok(Date.parse(qual.expiresAt) - Date.parse(qual.attestedAt) <= 30 * 86400000);
    assert.deepEqual([...qual.capability.taskClasses], [...ATTESTED_ROUTER_TASK_CLASSES]);
    assert.equal(qual.capability.visionNeed, false);
    assert.equal(qual.capability.toolsNeed, "standard");
    assert.equal(qual.capability.contextNeed, "workspace");
    const lcMin = Math.min(...receipt.attestation.perCase.filter((e) => e.task === "long-context-sentinel").map((e) => e.requests[0].inputTokens));
    assert.ok(qual.capability.inputTokenMax <= lcMin, "inputTokenMax must be no more than the minimum reported long-context count");
    const outputCapacityMins = receipt.attestation.perCase.filter((e) => e.task === "output-capacity").map((e) => e.requests[0].outputTokens);
    assert.ok(outputCapacityMins.every((value) => value >= OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS), "every output-capacity repetition must reach the output floor");
    const outputCapacityMin = Math.min(...outputCapacityMins);
    assert.equal(qual.capability.outputTokenMax, Math.min(OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS, outputCapacityMin), "outputTokenMax must be the minimum output-capacity count capped at the floor");
    assert.equal(qual.capability.costMicrosPerRunMax, 1);
    assert.deepEqual(qual.pricing, { inputMicrosPerMillionTokens: 0, outputMicrosPerMillionTokens: 0, fixedMicrosPerRun: 0 });
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("a qualified trial drives the production local-first router", async () => {
  const value = await fixture();
  try {
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), passingTransport("local-only", LOCAL_MODEL));
    const qual = receipt.semanticQualification;
    const request = makeRequest({ mode: "local-only", taskClass: "structural-review", costCeilingMicros: 0, outputTokenEstimate: 5, inputTokenEstimate: 100 });
    const policy = makeRouterPolicy({ mode: "local-only", preference: ["local"], enabled: [] });
    const routed = routeWorkProvider({ request, routerPolicy: policy, enabledPrivatePolicies: {}, qualifications: { local: qual }, now: new Date(), suffix: "aaaaaaaaaaaa" });
    assert.equal(routed.decision.selectionKind, "local");
    assert.equal(routed.decision.selectedProviderId, "local");
    assert.equal(routed.decision.qualificationSha256, sha(qual));
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("a passing moonshot-kimi trial preserves reasoning and promotes a bounded conservative qualification", async () => {
  const value = await moonshotFixture();
  try {
    const seenReplay = [];
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "moonshot-kimi", model: K3_MODEL, input });
      if (!admitted) throw new Error("outside corpus");
      if (admitted.kind === "tool-choice-replay") {
        const assistant = input.messages[1];
        const replayed = assistant.providerState.assistantMessage.reasoning_content;
        assert.ok(typeof replayed === "string" && replayed.length > 0, "K3 reasoning replay must be complete");
        assert.equal(replayed, REASONING, "K3 reasoning_content must be preserved verbatim");
        seenReplay.push(replayed);
        return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: K3_MODEL }, providerRequestId: "qual-tool-2", networkBytes: 200 };
      }
      const caseEntry = findNeutralCorpusCase({ lane: "moonshot-kimi", model: K3_MODEL, input });
      if (caseEntry.task === "tool-choice-continuation") {
        return { assistant: toolChoiceFirstResponse("moonshot-kimi"), providerRequestId: "qual-tool-1", networkBytes: 200 };
      }
      return { assistant: responseFor(K3_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(moonshotArgv(value), execute);
    assert.ok(seenReplay.length === 3, "each K3 trial must replay the returned reasoning_content verbatim");
    assert.equal(receipt.status, "qualified"); assert.equal(receipt.passedRepetitions, 6 * 3);
    assert.equal(receipt.lane, "moonshot-kimi"); assert.equal(receipt.model, K3_MODEL);
    const toolChoices = receipt.attestation.perCase.filter((e) => e.task === "tool-choice-continuation");
    for (const evidence of toolChoices) {
      assert.match(evidence.reasoningContentSha256, /^[a-f0-9]{64}$/u);
      const expected = createHash("sha256").update(JSON.stringify(REASONING)).digest("hex");
      assert.equal(evidence.reasoningContentSha256, expected);
    }
    const qual = receipt.semanticQualification;
    assert.equal(qual.attestationKind, "semantic-capability");
    validateQualification(qual, new Date());
    assert.equal(qual.providerId, "moonshot-kimi"); assert.equal(qual.model, K3_MODEL);
    // Conservative pricing must never exceed the owner private-policy ceiling.
    assert.ok(qual.capability.costMicrosPerRunMax <= privatePolicy.budgets.maxEstimatedCostMicrosPerRun);
    assert.equal(qual.capability.costMicrosPerRunMax, privatePolicy.budgets.maxEstimatedCostMicrosPerRun);
    assert.deepEqual(qual.pricing, { inputMicrosPerMillionTokens: 1000000, outputMicrosPerMillionTokens: 3000000, fixedMicrosPerRun: 50000 });
    assert.equal(qual.capability.costMicrosPerRunMax, 1000000);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("a qualified moonshot trial drives the production router through remote selection", async () => {
  const value = await moonshotFixture();
  try {
    const execute = passingTransport("moonshot-kimi", K3_MODEL);
    const receipt = await workProviderQualificationTestSeam.runWithTransport(moonshotArgv(value), execute);
    const qual = receipt.semanticQualification;
    const privatePolicyDoc = makePrivatePolicy("moonshot-kimi");
    const request = makeRequest({ mode: "policy-router", taskClass: "failure-triage", costCeilingMicros: 100000, outputTokenEstimate: 5, inputTokenEstimate: 100 });
    const policy = makeRouterPolicy({ providerId: "moonshot-kimi", mode: "policy-router", privatePolicy: privatePolicyDoc });
    const routed = routeWorkProvider({ request, routerPolicy: policy, enabledPrivatePolicies: { "moonshot-kimi": privatePolicyDoc }, qualifications: { "moonshot-kimi": qual }, now: new Date(), suffix: "aaaaaaaaaaaa" });
    assert.equal(routed.decision.selectionKind, "remote");
    assert.equal(routed.decision.selectedProviderId, "moonshot-kimi");
    assert.equal(routed.decision.qualificationSha256, sha(qual));
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("--trials is a closed integer 3..5 with distinct idempotency keys per repetition", async () => {
  const value = await fixture();
  try {
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport([...localArgv(value.ledgerRoot), "--trials", "2"], passingTransport("local-only", LOCAL_MODEL)), /--trials must be between 3 and 5/u);
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport([...localArgv(value.ledgerRoot), "--trials", "6"], passingTransport("local-only", LOCAL_MODEL)), /--trials must be between 3 and 5/u);
    const receipt = await workProviderQualificationTestSeam.runWithTransport([...localArgv(value.ledgerRoot), "--trials", "5"], passingTransport("local-only", LOCAL_MODEL));
    assert.equal(receipt.status, "qualified"); assert.equal(receipt.trials, 5); assert.equal(receipt.passedRepetitions, 6 * 5);
    assert.equal(receipt.requiredRepetitions, 6 * 5);
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.requests.length, 5 * 7);
    assert.equal(new Set(ledger.requests.map((r) => r.idempotencyKey)).size, 35);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("tool-choice requires the provider to independently choose edit_file with exact bounded args", async () => {
  const value = await fixture();
  try {
    let calls = 0;
    const execute = async ({ input }) => {
      calls += 1;
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "tool-choice-continuation") {
        return { assistant: { message: { role: "assistant", content: null, toolCalls: [{ id: "wrong", name: "read_file", arguments: '{"path":"file.txt"}' }], providerState: { protocol: "local-openai-compatible", assistantMessage: { role: "assistant", content: null, tool_calls: [{ id: "wrong", type: "function", function: { name: "read_file", arguments: '{"path":"file.txt"}' } }] }, finishReason: "tool_calls" } }, finishReason: "tool_calls", usage: { inputTokens: 5, outputTokens: 5, totalTokens: 10 }, providerModel: LOCAL_MODEL }, providerRequestId: "wrong-tool", networkBytes: 100 };
      }
      if (caseEntry.task === "long-context-sentinel") return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: "lc", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "not-qualified");
    assert.equal(receipt.semanticQualification, null);
    const toolEvidence = receipt.attestation.perCase.filter((e) => e.task === "tool-choice-continuation");
    assert.equal(toolEvidence.length, 3);
    for (const evidence of toolEvidence) { assert.equal(evidence.status, "failed"); assert.equal(evidence.requests.length, 1); }
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("an uncertain case is non-retryable, non-closing, and never promotes semantics", async () => {
  const value = await fixture();
  try {
    let calls = 0;
    const execute = async ({ input }) => {
      calls += 1;
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.caseId === "structural-review-1") throw new MoonshotWorkProviderTransportError("Moonshot response parsing failed", "uncertain");
      if (caseEntry.task === "long-context-sentinel") return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: "lc", networkBytes: 100 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "uncertain"); assert.equal(receipt.qualified, false);
    assert.equal(receipt.uncertainRepetitions, 1);
    assert.equal(receipt.semanticQualification, null);
    assert.equal(calls, 1, "an uncertain case must never be retried");
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.status, "uncertain"); assert.equal(ledger.requests[0].state, "uncertain");
    assert.equal(ledger.requests[0].attempts, 1);
    assert.equal(ledger.requests.length, 1, "an uncertain case must not proceed to later cases or trials");
    // The attempted uncertain request must still be bound into the content-free evidence with its
    // exact request hash and terminal state (item 1: no uncertain evidence loss).
    assert.equal(receipt.attestation.perCase.length, 1);
    const uncertainEvidence = receipt.attestation.perCase[0];
    assert.equal(uncertainEvidence.task, "structural-review");
    assert.equal(uncertainEvidence.trialIndex, 1);
    assert.equal(uncertainEvidence.status, "uncertain");
    assert.equal(uncertainEvidence.gradePass, false);
    assert.equal(uncertainEvidence.requests.length, 1);
    assert.equal(uncertainEvidence.requests[0].state, "uncertain");
    assert.equal(uncertainEvidence.requests[0].requestIndex, 1);
    const structuralCase = buildNeutralCorpus({ lane: "local-only", model: LOCAL_MODEL }).cases.find((entry) => entry.task === "structural-review");
    assert.equal(uncertainEvidence.requests[0].inputSha256, structuralCase.inputSha256);
    assert.equal(uncertainEvidence.requests[0].responseModel, null);
    assert.equal(uncertainEvidence.requests[0].failureCode, "response-framing");
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("a graded failure closes the run without promoting semantics", async () => {
  const value = await fixture();
  try {
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      if (admitted.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "t2", networkBytes: 100 };
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "structural-review") return { assistant: { message: { role: "assistant", content: '{"exportCount":2,"defectFunction":null,"defectKind":null}' }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 5, totalTokens: 25 }, providerModel: LOCAL_MODEL }, providerRequestId: "bad", networkBytes: 100 };
      if (caseEntry.task === "long-context-sentinel") return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: "lc", networkBytes: 100 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "not-qualified"); assert.equal(receipt.qualified, false);
    assert.equal(receipt.failedRepetitions, 3); assert.equal(receipt.passedRepetitions, 15);
    assert.equal(receipt.semanticQualification, null);
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.status, "completed");
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("an invalid untrusted patch is a recorded semantic failure while verifier infrastructure remains fail-closed", async () => {
  const value = await fixture();
  try {
    const reportPath = join(value.root, "invalid-patch-report.json");
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      if (admitted?.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "t2", networkBytes: 100 };
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "patch-proposal") return { assistant: { ...responseFor(LOCAL_MODEL, caseEntry), message: { role: "assistant", content: "not a unified diff" } }, providerRequestId: "invalid-patch", networkBytes: 100 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport([...localArgv(value.ledgerRoot), "--report", reportPath], execute);
    assert.equal(receipt.status, "not-qualified");
    assert.equal(receipt.qualified, false);
    assert.equal(receipt.failedRepetitions, 3);
    assert.equal(receipt.passedRepetitions, 15);
    assert.equal(receipt.semanticQualification, null);
    const patchEvidence = receipt.attestation.perCase.filter((entry) => entry.task === "patch-proposal");
    assert.equal(patchEvidence.length, 3);
    assert.equal(patchEvidence.every((entry) => entry.status === "failed" && entry.requests[0].state === "succeeded"), true);
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.status, "completed");
    assert.equal(ledger.requests.length, 21);
    assert.equal(JSON.stringify(receipt).includes("not a unified diff"), false);
    const report = JSON.parse(await readFile(reportPath, "utf8"));
    assert.equal(report.status, "not-qualified");
    assert.equal(JSON.stringify(report).includes("not a unified diff"), false);
    await assert.rejects(stat(join(value.root, "qualification-artifact.json")), { code: "ENOENT" });
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("long-context requires byte-exact content and the provider-reported token floor", async () => {
  const value = await fixture();
  try {
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      if (admitted.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "t2", networkBytes: 100 };
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "long-context-sentinel") return { assistant: { message: { role: "assistant", content: `${LONG_CONTEXT_SENTINEL} NOT_EXACT` }, finishReason: "stop", usage: { inputTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS, outputTokens: 3, totalTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS + 3 }, providerModel: LOCAL_MODEL }, providerRequestId: "lc-bad", networkBytes: 100 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "not-qualified");
    const lc = receipt.attestation.perCase.filter((e) => e.task === "long-context-sentinel");
    for (const evidence of lc) assert.equal(evidence.status, "failed");
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("adversarial: long-context rejects surrounding whitespace (byte-exact, not trim-based)", async () => {
  const value = await fixture();
  try {
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      if (admitted.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "t2", networkBytes: 100 };
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "long-context-sentinel") return { assistant: { message: { role: "assistant", content: `  ${LONG_CONTEXT_REQUIRED_REPLY}  ` }, finishReason: "stop", usage: { inputTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS, outputTokens: 3, totalTokens: LONG_CONTEXT_REPORTED_INPUT_TOKENS + 3 }, providerModel: LOCAL_MODEL }, providerRequestId: "lc-ws", networkBytes: 100 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "not-qualified");
    assert.equal(receipt.semanticQualification, null);
    const lc = receipt.attestation.perCase.filter((e) => e.task === "long-context-sentinel");
    for (const evidence of lc) assert.equal(evidence.status, "failed");
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("adversarial: long-context fails when provider-reported inputTokens are below the floor", async () => {
  const value = await fixture();
  try {
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      if (admitted.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "t2", networkBytes: 100 };
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "long-context-sentinel") return { assistant: { message: { role: "assistant", content: LONG_CONTEXT_REQUIRED_REPLY }, finishReason: "stop", usage: { inputTokens: LONG_CONTEXT_MIN_INPUT_TOKENS - 1, outputTokens: 3, totalTokens: LONG_CONTEXT_MIN_INPUT_TOKENS + 2 }, providerModel: LOCAL_MODEL }, providerRequestId: "lc-low", networkBytes: 100 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "not-qualified");
    assert.equal(receipt.semanticQualification, null);
    const lc = receipt.attestation.perCase.filter((e) => e.task === "long-context-sentinel");
    for (const evidence of lc) assert.equal(evidence.status, "failed");
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("structural-review and failure-triage are genuine derived semantic fixtures, not supplied answers", async () => {
  const corpus = buildNeutralCorpus({ lane: "local-only", model: LOCAL_MODEL });
  const structural = corpus.cases.find((e) => e.task === "structural-review");
  const structuralPrompt = structural.input.messages[0].content;
  assert.ok(structuralPrompt.includes("export function add(a, b)"), "structural-review must embed a small neutral module");
  assert.ok(structuralPrompt.includes("exportCount") && structuralPrompt.includes("defectFunction"), "structural-review must derive exact structured facts");
  assert.equal(structuralPrompt.includes('"ok"'), false, "structural-review must not supply the answer");
  assert.deepEqual(structural.expected, { exportCount: 3, defectFunction: "add", defectKind: "wrong-operator" });
  const triage = corpus.cases.find((e) => e.task === "failure-triage");
  const triagePrompt = triage.input.messages[0].content;
  assert.ok(triagePrompt.includes("connection-timeout"), "failure-triage must embed a fixed neutral log");
  assert.ok(triagePrompt.includes("retryable"), "failure-triage log must carry enough evidence to derive a retry");
  assert.equal(triagePrompt.includes('{"classification":"transient","retried":true,"resolution":"retry-succeeded"}'), false, "failure-triage must not supply the answer");
  assert.deepEqual(triage.expected, { classification: "transient", retried: true, resolution: "retry-succeeded" });
  // The deterministic grader parses and compares the expected structured facts exactly.
  const derived = { task: "structural-review", actual: { exportCount: 3, defectFunction: "add", defectKind: "wrong-operator" }, expected: structural.expected };
  assert.equal(gradeDeterministic(derived).pass, true);
  assert.equal(gradeDeterministic({ ...derived, actual: { exportCount: 2, defectFunction: null, defectKind: null } }).pass, false);
  const triageDerived = { task: "failure-triage", actual: { classification: "transient", retried: true, resolution: "retry-succeeded" }, expected: triage.expected };
  assert.equal(gradeDeterministic(triageDerived).pass, true);
  assert.equal(gradeDeterministic({ ...triageDerived, actual: { classification: "permanent", retried: false, resolution: "no-retry" } }).pass, false);
});

test("output-capacity control requires both the reported token floor and the neutral word floor", async () => {
  const value = await fixture();
  try {
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      if (admitted.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "t2", networkBytes: 100 };
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "output-capacity") return { assistant: { message: { role: "assistant", content: OUTPUT_CAPACITY_REPLY }, finishReason: "stop", usage: { inputTokens: 40, outputTokens: OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS - 1, totalTokens: 40 + OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS }, providerModel: LOCAL_MODEL }, providerRequestId: "oc-low", networkBytes: 4000 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "not-qualified");
    assert.equal(receipt.semanticQualification, null);
    const oc = receipt.attestation.perCase.filter((e) => e.task === "output-capacity");
    for (const evidence of oc) assert.equal(evidence.status, "failed");
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("output-capacity rejects any non-neutral word even at the output floor", async () => {
  const value = await fixture();
  try {
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      if (admitted.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "t2", networkBytes: 100 };
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "output-capacity") return { assistant: { message: { role: "assistant", content: `${OUTPUT_CAPACITY_REPLY} world` }, finishReason: "stop", usage: { inputTokens: 40, outputTokens: OUTPUT_CAPACITY_REPORTED_TOKENS, totalTokens: 40 + OUTPUT_CAPACITY_REPORTED_TOKENS }, providerModel: LOCAL_MODEL }, providerRequestId: "oc-extra", networkBytes: 4000 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "not-qualified");
    assert.equal(receipt.semanticQualification, null);
    const oc = receipt.attestation.perCase.filter((e) => e.task === "output-capacity");
    for (const evidence of oc) assert.equal(evidence.status, "failed");
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("output-capacity accepts non-brittle whitespace only when every word is neutral", () => {
  const corpus = buildNeutralCorpus({ lane: "local-only", model: LOCAL_MODEL });
  const output = corpus.cases.find((entry) => entry.task === "output-capacity");
  assert.equal(gradeDeterministic({ task: output.task, actual: OUTPUT_CAPACITY_REPLY.replaceAll(" ", " \n"), expected: output.expected }).pass, true);
  assert.equal(gradeDeterministic({ task: output.task, actual: OUTPUT_CAPACITY_REPLY.replace("hello", "world"), expected: output.expected }).pass, false);
});

test("a mismatched response model fails closed at settlement and never qualifies", async () => {
  const value = await fixture();
  try {
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      if (admitted.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: "other-alias" }, providerRequestId: "t2", networkBytes: 100 };
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      if (caseEntry.task === "tool-choice-continuation") return { assistant: { message: { role: "assistant", content: null, toolCalls: [{ id: "ok", name: TOOL_CHOICE_TOOL_NAME, arguments: TOOL_CHOICE_TOOL_ARGUMENTS }], providerState: { protocol: "local-openai-compatible", assistantMessage: { role: "assistant", content: null, tool_calls: [{ id: "ok", type: "function", function: { name: TOOL_CHOICE_TOOL_NAME, arguments: TOOL_CHOICE_TOOL_ARGUMENTS } }] }, finishReason: "tool_calls" } }, finishReason: "tool_calls", usage: { inputTokens: 40, outputTokens: 9, totalTokens: 49 }, providerModel: "other-alias" }, providerRequestId: "t1", networkBytes: 100 };
      return { assistant: { ...responseFor(LOCAL_MODEL, caseEntry), providerModel: "other-alias" }, providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    // A successful qualification-trial settlement must bind the exact ledger model; a
    // mismatched provider model is rejected at settlement and the trial fails closed.
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute), /differs from the pinned run model/u);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("transport guards reject inputs that escape the fixed neutral corpus", async () => {
  const value = await fixture();
  try {
    const seen = [];
    const execute = async ({ input }) => {
      const admitted = admitNeutralCorpusRequest({ lane: "local-only", model: LOCAL_MODEL, input });
      assert.ok(admitted, "runner must only invoke exact corpus requests");
      const caseEntry = findNeutralCorpusCase({ lane: "local-only", model: LOCAL_MODEL, input });
      seen.push(caseEntry ? `${caseEntry.caseId}:1` : `replay:2`);
      if (admitted.kind === "tool-choice-replay") return { assistant: { message: { role: "assistant", content: TOOL_CHOICE_FINAL_REPLY }, finishReason: "stop", usage: { inputTokens: 20, outputTokens: 4, totalTokens: 24 }, providerModel: LOCAL_MODEL }, providerRequestId: "t2", networkBytes: 100 };
      if (caseEntry.task === "tool-choice-continuation") return { assistant: toolChoiceFirstResponse("local-only"), providerRequestId: "t1", networkBytes: 100 };
      return { assistant: responseFor(LOCAL_MODEL, caseEntry), providerRequestId: `qual-${caseEntry.caseId}`, networkBytes: 200 };
    };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.equal(receipt.status, "qualified");
    assert.equal(seen.length, 21);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("strict CLI arguments reject an ambiguous lane, missing remote custody, invalid trials, and local-only remote flags", async () => {
  const value = await fixture();
  try {
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport(["--lane", "cloud", "--corpus-version", NEUTRAL_CORPUS_VERSION, "--ledger-root", value.ledgerRoot], passingTransport("local-only", LOCAL_MODEL)), /lane is not in the closed registry/u);
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport(["--lane", "moonshot-kimi", "--corpus-version", NEUTRAL_CORPUS_VERSION, "--ledger-root", value.ledgerRoot], passingTransport("moonshot-kimi", K3_MODEL)), /requires --policy/u);
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport([...localArgv(value.ledgerRoot), "--proxy-route", "loopback"], passingTransport("local-only", LOCAL_MODEL)), /local-only qualification-trial cannot accept --proxy-route/u);
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport(["--lane", "local-only", "--corpus-version", "9", "--ledger-root", value.ledgerRoot], passingTransport("local-only", LOCAL_MODEL)), /corpus version must be the bound neutral corpus version 8/u);
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport([...localArgv(value.ledgerRoot), "--trials", "abc"], passingTransport("local-only", LOCAL_MODEL)), /--trials must be an integer/u);
    const artifactPath = join(value.root, "qualification-artifact.json");
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport([...localArgv(value.ledgerRoot), "--report", artifactPath], passingTransport("local-only", LOCAL_MODEL)), /paths must differ/u);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("strict CLI rejects an arbitrary proxy route outside the closed enum", async () => {
  const value = await moonshotFixture();
  const argv = ["--lane", "moonshot-kimi", "--corpus-version", NEUTRAL_CORPUS_VERSION, "--policy", value.policyPath, "--sha256", value.sha256, "--credential", value.credentialPath, "--proxy-route", "127.0.0.2:8080", "--ledger-root", value.ledgerRoot];
  try {
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport(argv, passingTransport("moonshot-kimi", K3_MODEL)), /proxy route is invalid/u);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("adversarial: a connectivity smoke probe is never a neutral semantic corpus case", async () => {
  const corpus = buildNeutralCorpus({ lane: "moonshot-kimi", model: K3_MODEL });
  assert.equal(corpus.cases.some((entry) => entry.task === "connectivity-smoke"), false);
  const smokeInput = { schemaVersion: 1, model: K3_MODEL, messages: [{ role: "user", content: "Return exactly PIXEL_K3_SMOKE_OK and nothing else." }], maxOutputTokens: 256, reasoningEffort: "low" };
  assert.equal(findNeutralCorpusCase({ lane: "moonshot-kimi", model: K3_MODEL, input: smokeInput }), null);
  assert.equal(admitNeutralCorpusRequest({ lane: "moonshot-kimi", model: K3_MODEL, input: smokeInput }), null);
});

test("promotion revalidates the complete sealed attestation and fails closed on tampering", async () => {
  const value = await fixture();
  try {
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), passingTransport("local-only", LOCAL_MODEL));
    assert.equal(receipt.status, "qualified");
    const qual = receipt.semanticQualification;
    assert.ok(qual, "a valid qualified closed trial must promote a semantic-capability document");
    const files = await readdir(value.ledgerRoot); assert.equal(files.length, 1);
    const ledger = JSON.parse(await readFile(join(value.ledgerRoot, files[0]), "utf8"));
    assert.equal(ledger.status, "completed");
    const attestation = structuredClone(receipt.attestation);
    // Non-qualified, open-ledger, and uncertain trials emit null.
    assert.equal(promoteQualificationTrial({ attestation: { ...attestation, status: "failed" }, ledger, corpusTargetInputTokens: 38000 }), null);
    assert.equal(promoteQualificationTrial({ attestation: { ...attestation, status: "uncertain" }, ledger: { ...ledger, status: "uncertain" }, corpusTargetInputTokens: 38000 }), null);
    assert.equal(promoteQualificationTrial({ attestation: attestation, ledger: { ...ledger, status: "open" }, corpusTargetInputTokens: 38000 }), null);
    // A tampered qualified attestation fails closed and never emits a router qualification.
    const tamperedState = structuredClone(attestation); tamperedState.perCase[0].requests[0].state = "uncertain";
    assert.equal(promoteQualificationTrial({ attestation: tamperedState, ledger, corpusTargetInputTokens: 38000 }), null);
    const tamperedModel = structuredClone(attestation); tamperedModel.perCase[0].requests[0].responseModel = "other-model";
    assert.equal(promoteQualificationTrial({ attestation: tamperedModel, ledger, corpusTargetInputTokens: 38000 }), null);
    const tamperedRow = structuredClone(attestation); tamperedRow.perCase[0].gradePass = false;
    assert.equal(promoteQualificationTrial({ attestation: tamperedRow, ledger, corpusTargetInputTokens: 38000 }), null);
    const tamperedSha = structuredClone(attestation); tamperedSha.evidenceSha256 = "f".repeat(64);
    assert.equal(promoteQualificationTrial({ attestation: tamperedSha, ledger, corpusTargetInputTokens: 38000 }), null);
    const tamperedLedger = structuredClone(ledger); tamperedLedger.totals = { ...tamperedLedger.totals, inputTokens: tamperedLedger.totals.inputTokens + 1 };
    assert.equal(promoteQualificationTrial({ attestation, ledger: tamperedLedger, corpusTargetInputTokens: 38000 }), null);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("adversarial: K3 tool-choice replay requires a complete verbatim reasoning_content", async () => {
  const { moonshotTransportInternals } = await import("../deploy/work-provider/moonshot-transport.mjs");
  const { resolveWorkProvider } = await import("../deploy/work-provider/provider-registry.mjs");
  const resolvedProvider = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });
  const corpus = buildNeutralCorpus({ lane: "moonshot-kimi", model: K3_MODEL });
  const caseEntry = corpus.cases.find((entry) => entry.task === "tool-choice-continuation");
  const firstAssistant = toolChoiceFirstResponse("moonshot-kimi").message;
  const replay = {
    schemaVersion: 1, model: K3_MODEL,
    messages: [caseEntry.firstRequest.messages[0], firstAssistant, { role: "tool", toolCallId: firstAssistant.toolCalls[0].id, content: caseEntry.syntheticToolResult }, { role: "user", content: caseEntry.followUpUser }],
    maxOutputTokens: caseEntry.firstRequest.maxOutputTokens, reasoningEffort: caseEntry.firstRequest.reasoningEffort,
  };
  assert.equal(validateNeutralToolChoiceReplay({ lane: "moonshot-kimi", model: K3_MODEL, input: replay }), true);
  assert.equal(matchesNeutralToolArguments('{"path": "file.txt"}\n'), true);
  assert.equal(matchesNeutralToolArguments('{"path":"other.txt"}'), false);
  assert.equal(matchesNeutralToolArguments('{"path":"file.txt","path":"file.txt"}'), false);
  assert.doesNotThrow(() => moonshotTransportInternals.assertK3ReplayComplete(replay));
  const wrongEffort = structuredClone(replay); wrongEffort.reasoningEffort = "none";
  assert.equal(validateNeutralToolChoiceReplay({ lane: "moonshot-kimi", model: K3_MODEL, input: wrongEffort }), false);
  const extraField = structuredClone(replay); extraField.temperature = 0;
  assert.equal(validateNeutralToolChoiceReplay({ lane: "moonshot-kimi", model: K3_MODEL, input: extraField }), false);
  const tampered = structuredClone(replay);
  tampered.messages[1].providerState.assistantMessage.reasoning_content = "";
  assert.throws(() => moonshotTransportInternals.assertK3ReplayComplete(tampered), /reasoning_content replay is incomplete or truncated/u);
  const missing = structuredClone(replay);
  delete missing.messages[1].providerState.assistantMessage.reasoning_content;
  assert.throws(() => moonshotTransportInternals.assertK3ReplayComplete(missing), /reasoning_content/u);
  assert.throws(() => moonshotTransportInternals.assertQualificationTrialInput({
    ledger: { binding: { kind: "qualification-trial" } }, resolvedProvider,
    input: { schemaVersion: 1, model: K3_MODEL, messages: [{ role: "user", content: "not a corpus case" }], maxOutputTokens: 256, reasoningEffort: "low" },
  }), /escaped its fixed neutral corpus/u);
});

test("the qualification artifact binds the promoted semantic qualification with content-free provenance at mode 0600", async () => {
  const value = await fixture();
  try {
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), passingTransport("local-only", LOCAL_MODEL));
    assert.equal(receipt.status, "qualified");
    const artifactPath = join(value.root, "qualification-artifact.json");
    const artifactHandle = await open(artifactPath, "r");
    let artifact;
    try {
      const details = await artifactHandle.stat();
      if (process.platform !== "win32") assert.equal((details.mode & 0o077), 0, "qualification artifact must be owner-private");
      artifact = JSON.parse(await artifactHandle.readFile("utf8"));
    } finally {
      await artifactHandle.close();
    }
    assert.equal(artifact.kind, "work-provider-qualification-artifact");
    assert.equal(artifact.status, "qualified");
    assert.equal(artifact.qualified, true);
    assert.equal(artifact.lane, "local-only");
    assert.equal(artifact.providerId, "local");
    assert.equal(artifact.model, LOCAL_MODEL);
    assert.equal(artifact.corpusVersion, NEUTRAL_CORPUS_VERSION);
    assert.match(artifact.corpusSha256, /^[a-f0-9]{64}$/u);
    assert.equal(artifact.trials, receipt.trials);
    assert.match(artifact.evidenceSha256, /^[a-f0-9]{64}$/u);
    assert.equal(artifact.evidenceSha256, receipt.attestation.evidenceSha256);
    assert.equal(artifact.ledgerRunId, receipt.attestation.ledgerRunId);
    assert.match(artifact.ledgerSha256, /^[a-f0-9]{64}$/u);
    assert.equal(artifact.qualificationSha256, receipt.semanticQualification.qualificationSha256);
    // The artifact carries the exact promoted semantic qualification.
    assert.deepEqual(artifact.semanticQualification, receipt.semanticQualification);
    const { artifactSha256, ...without } = artifact;
    assert.equal(artifactSha256, sha(without));
    // The artifact is content-free: no prompt, response, reasoning, credential, or path.
    const serialized = JSON.stringify(artifact);
    assert.equal(serialized.includes("temporary-test-key"), false);
    assert.equal(serialized.includes("reasoning_content"), false);
    assert.equal(serialized.includes("PIXEL_K3_SMOKE_OK"), false);
    assert.equal(serialized.includes("artifact.json"), false);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("a qualified campaign without --qualification-report fails closed and writes nothing", async () => {
  const value = await fixture();
  try {
    const argv = ["--lane", "local-only", "--corpus-version", NEUTRAL_CORPUS_VERSION, "--ledger-root", value.ledgerRoot];
    await assert.rejects(workProviderQualificationTestSeam.runWithTransport(argv, passingTransport("local-only", LOCAL_MODEL)), /requires --qualification-report/u);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("a qualification artifact is created once and never follows or overwrites an existing path", async () => {
  const value = await fixture();
  try {
    const artifactPath = join(value.root, "qualification-artifact.json");
    const sentinel = "owner-existing-artifact\n";
    await writeFile(artifactPath, sentinel, { mode: 0o600 });
    await assert.rejects(
      workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), passingTransport("local-only", LOCAL_MODEL)),
      /could not be created once/u,
    );
    assert.equal(await readFile(artifactPath, "utf8"), sentinel);
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("a non-qualified campaign with --qualification-report writes no artifact", async () => {
  const value = await fixture();
  try {
    const execute = async (options) => { throw new Error("transport failed"); };
    const receipt = await workProviderQualificationTestSeam.runWithTransport(localArgv(value.ledgerRoot), execute);
    assert.notEqual(receipt.status, "qualified");
    assert.equal(receipt.semanticQualification, null);
    await assert.rejects(stat(join(value.root, "qualification-artifact.json")), { code: "ENOENT" });
  } finally { await rm(value.root, { recursive: true, force: true }); }
});
