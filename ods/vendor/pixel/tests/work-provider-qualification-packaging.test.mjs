import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

const run = promisify(execFile);
const repo = dirname(dirname(fileURLToPath(import.meta.url)));

test("the sealed qualification runner, promotion, and schemas are registered in the release manifest", async () => {
  const manifest = JSON.parse(await readFile(join(repo, "RELEASE-MANIFEST.json"), "utf8"));
  const wp = manifest.workProvider;
  assert.equal(wp.qualificationRunner, "sealed-neutral-corpus-qualification-trial-local-moonshot-openai-anthropic-openrouter");
  assert.equal(wp.neutralCorpusSchema, "./schemas/work-provider-neutral-corpus-v1.schema.json");
  assert.equal(wp.qualificationTrialEvidenceSchema, "./schemas/work-provider-qualification-trial-evidence-v1.schema.json");
  assert.equal(wp.qualificationPromotion, "sealed-deterministic-semantic-capability-promotion");
  assert.equal(wp.qualificationPromotionModule, "./deploy/work-provider/qualification-promotion.mjs");
  for (const path of [wp.neutralCorpusSchema, wp.qualificationTrialEvidenceSchema, "./schemas/work-provider-run-ledger-v1.schema.json"]) {
    const text = await readFile(join(repo, path), "utf8");
    JSON.parse(text);
  }
  // The promotion module is source (not JSON); it must exist and be readable.
  assert.ok((await readFile(join(repo, wp.qualificationPromotionModule), "utf8")).length > 0);
});

test("the release contract check passes with the qualification-trial and promotion integration", async () => {
  const { stdout } = await run(process.execPath, ["scripts/check-release-contract.mjs"], { cwd: repo });
  assert.match(stdout, /internally consistent/u);
});

test("the sealed qualification runner only uses closed transports and persists no provider content", async () => {
  const source = await readFile(join(repo, "deploy/work-provider/qualification-runner.mjs"), "utf8");
  assert.match(source, /resolveWorkProviderTransport/);
  assert.doesNotMatch(source, /async function runQualificationTrial\([^)]*(execute|transport)[^)]*\)/u);
  assert.doesNotMatch(source, /\{ execute:/u);
  assert.match(source, /return runQualificationTrialCore\(argv, null\);/u);
  assert.match(source, /export const workProviderQualificationTestSeam/u);
  assert.doesNotMatch(source, /workProviderQualificationTestSeam\./u);
  assert.match(source, /runQualificationTrial\(\)/u);
  assert.doesNotMatch(source, /workProviderExecutorTestOnly/);
  assert.doesNotMatch(source, /workProviderHarnessTestOnly/);
  assert.match(source, /TRIALS_MIN = 3/);
  assert.match(source, /TRIALS_MAX = 5/);
  assert.doesNotMatch(source, /from "node:(http|net|tls)"/u);
  assert.doesNotMatch(source, /readWorkProviderCredential/);
  assert.match(source, /providerContentIncluded: false/);
  assert.match(source, /credentialIncluded: false/);
  assert.doesNotMatch(source, /writeFile\(.*(prompt|response|reasoning|credential)/u);
  assert.match(source, /PROXY_ROUTES = \["loopback", "container"\]/);
});

test("the promotion step is sealed, deterministic, and distinct from connectivity-smoke", async () => {
  const source = await readFile(join(repo, "deploy/work-provider/qualification-promotion.mjs"), "utf8");
  // The promotion emits a semantic-capability document only for a fully-passing, closed trial.
  assert.match(source, /attestation\.status !== "qualified" \|\| ledger\.status !== "completed"/u);
  assert.match(source, /attestationKind: "semantic-capability"/u);
  // The promotion never emits a connectivity-smoke attestation kind.
  assert.doesNotMatch(source, /attestationKind: "connectivity-smoke"/u);
  assert.match(source, /qualificationSha256 = sha\(withoutSha\)/u);
  assert.match(source, /validateWorkProviderRouterQualification\(doc\)/u);
  assert.match(source, /evidenceSha256: sha\(attestation\)/u);
  // Conservative, content-free capability envelope.
  assert.match(source, /visionNeed: false/u);
  assert.match(source, /contextNeed: "workspace"/u);
  assert.match(source, /toolsNeed: "standard"/u);
});

test("the test-only transport seam exists but is forbidden in production code paths", async () => {
  const runner = await readFile(join(repo, "deploy/work-provider/qualification-runner.mjs"), "utf8");
  const transportRegistry = await readFile(join(repo, "deploy/work-provider/transport-registry.mjs"), "utf8");
  const localTransport = await readFile(join(repo, "deploy/work-provider/local-transport.mjs"), "utf8");
  assert.match(runner, /workProviderQualificationTestSeam/);
  assert.doesNotMatch(transportRegistry, /TestSeam|TestOnly|testOnly/);
  assert.match(transportRegistry, /Object\.freeze\(/u);
  assert.match(transportRegistry, /assertClosedTransport/);
  assert.match(localTransport, /localProviderTransportTestOnly/u);
  assert.match(localTransport, /production local transport cannot accept a caller-supplied exchange/u);
  assert.doesNotMatch(transportRegistry, /localProviderTransportTestOnly/u);
});

test("the qualification-trial binding kind is distinct from semantic and connectivity-smoke in the ledger contract", async () => {
  const contract = await readFile(join(repo, "scripts/lib/work-contract.mjs"), "utf8");
  assert.match(contract, /\["semantic", "connectivity-smoke", "qualification-trial"\]/);
  const schema = await readFile(join(repo, "schemas/work-provider-run-ledger-v1.schema.json"), "utf8");
  const parsed = JSON.parse(schema);
  assert.deepEqual(parsed.properties.binding.properties.kind.enum, ["semantic", "connectivity-smoke", "qualification-trial"]);
  assert.deepEqual(parsed.properties.binding.properties.lane.enum, ["local-only", "moonshot-kimi", "openai", "anthropic", "openrouter"]);
});

test("the evidence schema binds per-request evidence and validates every SHA-256", async () => {
  const schema = JSON.parse(await readFile(join(repo, "schemas/work-provider-qualification-trial-evidence-v1.schema.json"), "utf8"));
  assert.equal(schema.properties.trials.minimum, 3);
  assert.equal(schema.properties.trials.maximum, 5);
  const perCase = schema.properties.perCase.items;
  for (const field of ["trialIndex", "caseSha256", "reasoningContentSha256", "requests"]) {
    assert.ok(perCase.required.includes(field), `perCase evidence must bind ${field}`);
  }
  // reasoningContentSha256 must be validated as sha256-or-null, not a loose string.
  assert.ok(perCase.properties.reasoningContentSha256.anyOf, "reasoningContentSha256 must be sha256-validated");
  assert.ok(perCase.properties.reasoningContentSha256.anyOf.some((entry) => entry.$ref === "#/$defs/sha256"));
  const requestItem = perCase.properties.requests.items;
  for (const field of ["requestIndex", "state", "inputSha256", "inputTokens", "outputTokens", "responseModel", "failureCode"]) {
    assert.ok(requestItem.required.includes(field), `per-request evidence must bind ${field}`);
  }
  assert.ok(requestItem.properties.inputSha256.$ref === "#/$defs/sha256");
  assert.ok(requestItem.properties.responseModel, "per-request evidence must bind the exact response model");
  assert.deepEqual(requestItem.properties.requestIndex.enum, [1, 2]);
  assert.deepEqual(requestItem.properties.state.enum, ["succeeded", "failed-known", "uncertain"]);
  // Counters are renamed so requiredCases/repeated trials/request counts cannot be misread.
  for (const field of ["distinctCaseCount", "requiredRepetitions", "passedRepetitions", "failedRepetitions", "uncertainRepetitions"]) {
    assert.ok(schema.required.includes(field), `evidence must bind ${field}`);
  }
  assert.equal(schema.required.includes("requiredCases"), false);
});

test("the neutral corpus schema enumerates the attested router task classes and their controls", async () => {
  const schema = JSON.parse(await readFile(join(repo, "schemas/work-provider-neutral-corpus-v1.schema.json"), "utf8"));
  const taskEnum = schema.properties.cases.items.properties.task.enum;
  for (const task of ["structural-review", "patch-proposal", "tool-choice-continuation", "long-context-sentinel", "failure-triage", "output-capacity"]) {
    assert.ok(taskEnum.includes(task), `corpus schema must enumerate ${task}`);
  }
});
