import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { chmod, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

import {
  createLoopbackModelQualificationInvoker, fixedModelQualificationCases,
  fixedModelQualificationCasesSha256, fixedModelQualificationEvaluatorSha256, main,
  runFixedModelQualification, validateFixedModelQualificationConfig,
} from "../deploy/work-controller/model-qualification-runner.mjs";
import { modelCapabilityReceiptSha256 } from "../deploy/work-controller/model-qualification.mjs";
import { canonical, validateWorkModelCapabilityReceipt, validateWorkPolicy } from "../scripts/lib/work-contract.mjs";
import { fixtureVllmInferencePolicy } from "./fixtures/work/inference-policy.mjs";

const digest = (character) => character.repeat(64);
const run = promisify(execFile);
const runnerPath = fileURLToPath(new URL("../deploy/work-controller/model-qualification-runner.mjs", import.meta.url));
const model = () => ({
  provider: "llama.cpp", id: "qualified-local-model", modelArtifactSha256: digest("a"),
  backendImageDigest: `sha256:${digest("b")}`, backendVersion: "b6123", acceleratorClass: "nvidia-cuda",
  promptContractSha256: digest("c"), toolSchemaSha256: digest("d"), contextWindow: 131072, supportsVision: false,
});
const baseConfig = (origin = "http://127.0.0.1:8080") => ({
  schemaVersion: 1, backendOrigin: origin, model: model(), timeoutMs: 5000,
  maxResponseBytes: 1048576, qualificationLifetimeSeconds: 604800,
});

function toolCall(name, arguments_) {
  return { role: "assistant", content: null, tool_calls: [{ id: "call_pixel_qualification", type: "function", function: { name, arguments: JSON.stringify(arguments_) } }] };
}

function expectedMessage(id) {
  const expected = new Map([
    ["structured-json", { role: "assistant", content: JSON.stringify({ status: "ok", sequence: 17 }) }],
    ["sustained-structured-output", { role: "assistant", content: JSON.stringify({ sequence: Array.from({ length: 4096 }, (_, index) => index) }) }],
    ["context-sentinel", { role: "assistant", content: "PIXEL_CONTEXT_SENTINEL_7f3a19" }],
    ["recovery-no-repair", { role: "assistant", content: "RETRY_REQUIRED" }],
    ["usage-accounting", { role: "assistant", content: "USAGE_OK" }],
    ["assistant-tool-selection", toolCall("pixel_calendar_list", { timeMin: "2026-08-14T09:00:00-04:00", timeMax: "2026-08-14T17:00:00-04:00" })],
    ["assistant-argument-fidelity", toolCall("pixel_calendar_propose_delete", { eventId: "evt_017", expectedEtag: "etag-42", sendUpdates: "none" })],
    ["scout-tool-selection", toolCall("read", { path: "evidence.txt" })],
    ["scout-argument-fidelity", toolCall("search", { pattern: "alpha.*omega", path: "src" })],
    ["builder-tool-selection", toolCall("bash", { command: "printf pixel" })],
    ["builder-argument-fidelity", toolCall("edit", { path: "src/app.js", old: "OLD_VALUE", replacement: "NEW_VALUE" })],
    ["data-tool-selection", toolCall("read", { path: "datasets/input.csv" })],
    ["data-argument-fidelity", toolCall("write", { path: "artifacts/summary.txt", content: "rows=17" })],
    ["research-tool-selection", toolCall("pixel_research", { query: "Pixel release notes" })],
    ["research-argument-fidelity", toolCall("read", { path: "sources/source-017.txt" })],
  ]);
  return structuredClone(expected.get(id));
}

function passingResult(id) {
  return {
    body: {
      choices: [{ index: 0, finish_reason: expectedMessage(id).tool_calls ? "tool_calls" : "stop", message: expectedMessage(id) }],
      usage: { prompt_tokens: id === "context-sentinel" ? 32768 : 128, completion_tokens: id === "sustained-structured-output" ? 8192 : 16 },
    },
    latencyMs: 25,
  };
}

function caseIdFromRequest(body) {
  const user = String(body?.messages?.[1]?.content ?? "");
  if (user.includes("0 through 4095")) return "sustained-structured-output";
  if (body.response_format) return "structured-json";
  if (user.includes("PIXEL_CONTEXT_SENTINEL_7f3a19")) return "context-sentinel";
  if (user.includes("malformed tool request") || user.includes("tool request arrived")) return "recovery-no-repair";
  if (user.includes("required token")) return "usage-accounting";
  if (user.includes("List calendar events")) return "assistant-tool-selection";
  if (user.includes("evt_017")) return "assistant-argument-fidelity";
  if (user.includes("evidence.txt")) return "scout-tool-selection";
  if (user.includes("alpha.*omega")) return "scout-argument-fidelity";
  if (user.includes("printf pixel")) return "builder-tool-selection";
  if (user.includes("OLD_VALUE")) return "builder-argument-fidelity";
  if (user.includes("datasets/input.csv")) return "data-tool-selection";
  if (user.includes("rows=17")) return "data-argument-fidelity";
  if (user.includes("Research the public query")) return "research-tool-selection";
  if (user.includes("sources/source-017.txt")) return "research-argument-fidelity";
  throw new Error("unknown fixed qualification request");
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-qualification-"));
  if (process.platform !== "win32") await chmod(root, 0o700);
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test("fixed benchmark qualifies only exact observed behavior and publishes stable bindings", async () => {
  const seen = [];
  const receipt = await runFixedModelQualification({
    model: model(), observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => { seen.push(metadata.id); return passingResult(metadata.id); },
  });
  assert.deepEqual(seen, fixedModelQualificationCases().map((entry) => entry.id));
  assert.deepEqual(validateWorkModelCapabilityReceipt(receipt), []);
  assert.equal(receipt.status, "qualified");
  assert.deepEqual(receipt.envelope.eligibleProfiles, ["assistant", "scout", "builder", "data-lab", "researcher"]);
  assert.equal(receipt.envelope.maxContextTokens, 32768);
  assert.equal(receipt.envelope.maxOutputTokens, 8192);
  assert.equal(receipt.suite.casesSha256, fixedModelQualificationCasesSha256());
  assert.equal(receipt.suite.evaluatorSha256, await fixedModelQualificationEvaluatorSha256());
  assert.doesNotMatch(JSON.stringify(receipt), /PIXEL_CONTEXT_SENTINEL|printf pixel|OLD_VALUE/u);
});

test("fixed benchmark conservatively caps proven context at the advertised runtime envelope", async () => {
  const identity = model(); identity.contextWindow = 30000;
  const receipt = await runFixedModelQualification({
    model: identity, observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => passingResult(metadata.id),
  });
  assert.equal(receipt.status, "qualified");
  assert.equal(receipt.envelope.maxContextTokens, 30000);
});

test("profile regression, missing usage, recovery mutation, and invocation failure fail closed without retry", async () => {
  let calls = 0;
  const profileRegression = await runFixedModelQualification({
    model: model(), observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => {
      calls += 1;
      if (metadata.id === "builder-tool-selection") return { ...passingResult(metadata.id), body: { choices: [{ message: { role: "assistant", content: "I cannot choose." } }], usage: { prompt_tokens: 128, completion_tokens: 4 } } };
      return passingResult(metadata.id);
    },
  });
  assert.equal(calls, 15);
  assert.equal(profileRegression.status, "qualified");
  assert.deepEqual(profileRegression.envelope.eligibleProfiles, ["assistant", "scout", "data-lab", "researcher"]);
  assert.equal(profileRegression.observations.caseResults.find((entry) => entry.id === "builder-tool-selection")?.failureClass, "finish-reason-mismatch");

  const assistantRegression = await runFixedModelQualification({
    model: model(), observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => metadata.id === "assistant-tool-selection"
      ? { ...passingResult(metadata.id), body: { choices: [{ message: { role: "assistant", content: "I cannot choose." } }], usage: { prompt_tokens: 128, completion_tokens: 4 } } }
      : passingResult(metadata.id),
  });
  assert.equal(assistantRegression.status, "qualified");
  assert.equal(assistantRegression.observations.profilePass.assistant, false);
  assert.deepEqual(assistantRegression.envelope.eligibleProfiles, ["scout", "builder", "data-lab", "researcher"]);

  const argumentRegression = await runFixedModelQualification({
    model: model(), observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => metadata.id === "assistant-argument-fidelity"
      ? { ...passingResult(metadata.id), body: { choices: [{ finish_reason: "tool_calls", message: toolCall("pixel_calendar_propose_delete", { eventId: "evt_changed", expectedEtag: "etag-42", sendUpdates: "none" }) }], usage: { prompt_tokens: 128, completion_tokens: 16 } } }
      : passingResult(metadata.id),
  });
  assert.equal(argumentRegression.observations.caseResults.find((entry) => entry.id === "assistant-argument-fidelity")?.failureClass, "tool-arguments-mismatch");
  assert.equal(argumentRegression.observations.intentMutationObserved, true);

  const noUsage = await runFixedModelQualification({
    model: model(), observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => metadata.id === "usage-accounting" ? { body: { choices: [{ finish_reason: "stop", message: expectedMessage(metadata.id) }] }, latencyMs: 1 } : passingResult(metadata.id),
  });
  assert.equal(noUsage.status, "failed");
  assert.equal(noUsage.observations.usageSource, "estimated");
  assert.equal(noUsage.observations.caseResults.find((entry) => entry.id === "usage-accounting")?.failureClass, "usage-missing");
  assert.equal(noUsage.observations.caseResults.find((entry) => entry.id === "usage-accounting")?.usageSource, "estimated");

  const mutation = await runFixedModelQualification({
    model: model(), observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => metadata.id === "recovery-no-repair" ? { ...passingResult(metadata.id), body: { choices: [{ message: toolCall("bash", { command: "rm -rf workspace" }) }], usage: { prompt_tokens: 128, completion_tokens: 8 } } } : passingResult(metadata.id),
  });
  assert.equal(mutation.status, "failed");
  assert.equal(mutation.observations.intentMutationObserved, true);
  assert.equal(mutation.observations.caseResults.find((entry) => entry.id === "recovery-no-repair")?.failureClass, "finish-reason-mismatch");

  calls = 0;
  const interrupted = await runFixedModelQualification({
    model: model(), observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => { calls += 1; if (metadata.id === "context-sentinel") throw new Error("private backend detail"); return passingResult(metadata.id); },
  });
  assert.equal(calls, 15);
  assert.equal(interrupted.status, "failed");
  assert.equal(interrupted.observations.caseResults.find((entry) => entry.id === "context-sentinel")?.failureClass, "invocation-failed");
  assert.doesNotMatch(JSON.stringify(interrupted), /private backend detail/u);
});

test("loopback invoker rejects credential, redirect, encoding, size, and non-loopback widening", async () => {
  for (const origin of ["https://127.0.0.1:8080", "http://localhost:8080", "http://10.0.0.2:8080", "http://user:secret@127.0.0.1:8080", "http://127.0.0.1:8080/path"]) {
    assert.throws(() => validateFixedModelQualificationConfig(baseConfig(origin)), /loopback origin/);
  }
  const zero = baseConfig(); zero.model.modelArtifactSha256 = digest("0");
  assert.throws(() => validateFixedModelQualificationConfig(zero), /identity/);
  for (const provider of ["llama.cpp", "ollama", "vllm", "openai-compatible-local"]) {
    const candidate = baseConfig(); candidate.model.provider = provider;
    assert.equal(validateFixedModelQualificationConfig(candidate).model.provider, provider);
  }
  const remoteProvider = baseConfig(); remoteProvider.model.provider = "openai";
  assert.throws(() => validateFixedModelQualificationConfig(remoteProvider), /identity/);
  const runtimePolicy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  runtimePolicy.localModel.provider = "vllm";
  runtimePolicy.localModel.imageRef = `local/pixel-vllm@${runtimePolicy.localModel.imageDigest}`;
  runtimePolicy.localModel.inference = fixtureVllmInferencePolicy();
  assert.deepEqual(validateWorkPolicy(runtimePolicy), []);
  const calls = [];
  const invoke = createLoopbackModelQualificationInvoker(baseConfig(), async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(passingResult("usage-accounting").body), { status: 200, headers: { "content-type": "application/json" } });
  });
  const result = await invoke({ model: model().id, messages: [], stream: false, max_tokens: 1 });
  assert.equal(result.body.usage.prompt_tokens, 128);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "http://127.0.0.1:8080/v1/chat/completions");
  assert.equal(calls[0].options.redirect, "error");
  assert.equal(Object.keys(calls[0].options.headers).some((name) => /auth|cookie|key/u.test(name)), false);

  const compressed = createLoopbackModelQualificationInvoker(baseConfig(), async () => new Response("{}", { status: 200, headers: { "content-type": "application/json", "content-encoding": "gzip" } }));
  await assert.rejects(compressed({}), /encoding/);
  const oversized = createLoopbackModelQualificationInvoker({ ...baseConfig(), maxResponseBytes: 1024 }, async () => new Response("{}", { status: 200, headers: { "content-type": "application/json", "content-length": "1025" } }));
  await assert.rejects(oversized({}), /length/);
  const streamedOversize = createLoopbackModelQualificationInvoker({ ...baseConfig(), maxResponseBytes: 1024 }, async () => new Response("x".repeat(2048), { status: 200, headers: { "content-type": "application/json" } }));
  await assert.rejects(streamedOversize({}), /byte limit/);
  const redirected = createLoopbackModelQualificationInvoker(baseConfig(), async () => new Response(null, { status: 302, headers: { location: "http://127.0.0.1:9999/" } }));
  await assert.rejects(redirected({}), /request failed/);
  const timed = createLoopbackModelQualificationInvoker({ ...baseConfig(), timeoutMs: 100 }, async (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true })));
  await assert.rejects(timed({}), /aborted/);
});

test("vLLM qualification uses non-thinking structured output and isolates argument fidelity from tool selection", async () => {
  const identity = model(); identity.provider = "vllm";
  identity.backendVersion = "0.11.2.dev280+gilded.gnosis.v20.vllm1e9c9c3.sieec30ff.fi801d57a.cu132.20260731.r16";
  const requests = [];
  const receipt = await runFixedModelQualification({
    model: identity, observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (request, metadata) => { requests.push(request); return passingResult(metadata.id); },
  });
  assert.equal(receipt.status, "qualified");
  assert.equal(receipt.model.provider, "vllm");
  assert.equal(requests.length, 15);
  assert.ok(requests.every((request) => request.max_tokens >= 128 && request.include_reasoning === false));
  const structuredRequests = requests.filter((request) => request.response_format);
  assert.equal(structuredRequests.length, 2);
  assert.ok(structuredRequests.every((request) => request.reasoning_effort === "none"));
  const reasoningRequests = requests.filter((request) => !request.response_format);
  assert.ok(reasoningRequests.every((request) => request.reasoning_effort === "high" && request.max_tokens === 8192));
  assert.ok(reasoningRequests.length > 0);
  assert.ok(requests.every((request) => request.thinking_token_budget === undefined && request.chat_template === undefined && request.chat_template_kwargs === undefined));
  const recoveryRequest = requests.find((request) => String(request.messages[1].content).includes("tool request arrived"));
  assert.equal(recoveryRequest?.tool_choice, "auto");
  const selectionRequests = requests.filter((request) => request.tool_choice === "required");
  assert.equal(selectionRequests.length, 5);
  const argumentRequests = requests.filter((request) => request.tool_choice?.type === "function");
  assert.equal(argumentRequests.length, 5);
  assert.deepEqual(argumentRequests.map((request) => request.tool_choice.function.name).sort(), ["edit", "pixel_calendar_propose_delete", "read", "search", "write"]);
  for (const request of argumentRequests) {
    assert.equal(request.tool_choice.function.name, request.tools.find((tool) => tool.function.name === request.tool_choice.function.name)?.function.name);
  }
});

test("qualification accepts a complete vLLM tool call reported with stop finish semantics", async () => {
  const identity = model(); identity.provider = "vllm";
  identity.backendVersion = "0.11.2.dev280+gilded.gnosis.v20.vllm1e9c9c3.sieec30ff.fi801d57a.cu132.20260731.r16";
  const receipt = await runFixedModelQualification({
    model: identity, observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (_request, metadata) => {
      const result = passingResult(metadata.id);
      if (metadata.category === "argument-fidelity") result.body.choices[0].finish_reason = "stop";
      return result;
    },
  });
  assert.equal(receipt.status, "qualified");
  assert.ok(receipt.observations.caseResults.filter((entry) => entry.category === "argument-fidelity").every((entry) => entry.passed));
});

test("vLLM requests carry a deterministic case-derived unsigned 32-bit seed and non-vLLM carry none", async () => {
  const seedOf = (entry) => parseInt(createHash("sha256").update(canonical(entry)).digest("hex").slice(0, 8), 16);
  const collect = async () => {
    const identity = model(); identity.provider = "vllm";
    identity.backendVersion = "0.11.2.dev280+gilded.gnosis.v20.vllm1e9c9c3.sieec30ff.fi801d57a.cu132.20260731.r16";
    const requests = [];
    await runFixedModelQualification({
      model: identity, observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
      invoke: async (request, metadata) => { requests.push(request); return passingResult(metadata.id); },
    });
    return requests;
  };
  const first = await collect(), second = await collect();
  assert.equal(first.length, 15);
  assert.equal(second.length, 15);
  assert.ok(first.every((request) => Number.isInteger(request.seed) && request.seed >= 0 && request.seed <= 4294967295));
  const seedById = (requests) => new Map(requests.map((request) => [caseIdFromRequest(request), request.seed]));
  const firstById = seedById(first), secondById = seedById(second);
  for (const entry of fixedModelQualificationCases()) {
    const expected = seedOf(entry);
    assert.equal(firstById.get(entry.id), expected, `first-run ${entry.id} seed matches documented derivation`);
    assert.equal(secondById.get(entry.id), expected, `independent run ${entry.id} seed is stable`);
  }
  assert.equal(new Set(firstById.values()).size, 15, "distinct cases must have distinct seeds");

  const nonVllmRequests = [];
  await runFixedModelQualification({
    model: model(), observedAt: new Date("2026-08-11T12:00:00Z"), expiresAt: new Date("2026-08-18T12:00:00Z"), suffix: "abcdef123456",
    invoke: async (request, metadata) => { nonVllmRequests.push(request); return passingResult(metadata.id); },
  });
  assert.equal(nonVllmRequests.length, 15);
  assert.ok(nonVllmRequests.every((request) => request.seed === undefined));
});

test("real loopback CLI run writes one private content-free receipt and makes exactly fifteen calls", async (t) => {
  const root = await fixture(t), requests = [];
  let forceFailure = false;
  const server = createServer(async (request, response) => {
    const chunks = []; for await (const chunk of request) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    requests.push({ url: request.url, method: request.method, authorization: request.headers.authorization, body });
    const id = caseIdFromRequest(body), result = passingResult(id);
    if (forceFailure && id === "structured-json") result.body.choices[0].message.content = JSON.stringify({ status: "no", sequence: 17 });
    const payload = JSON.stringify(result.body);
    response.writeHead(200, { "content-type": "application/json", "content-length": String(Buffer.byteLength(payload)) });
    response.end(payload);
  });
  await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const address = server.address(); assert.ok(address && typeof address === "object");
  const configPath = join(root, "qualification-config.json"), output = join(root, "qualification-receipt.json");
  await writeFile(configPath, `${JSON.stringify(baseConfig(`http://127.0.0.1:${address.port}`))}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(configPath, 0o600);
  const result = await main(["--config", configPath, "--output", output]);
  const receiptText = await readFile(output, "utf8"), receipt = JSON.parse(receiptText);
  assert.equal(requests.length, 15);
  assert.ok(requests.every((entry) => entry.url === "/v1/chat/completions" && entry.method === "POST" && entry.authorization === undefined));
  assert.equal(result.status, "qualified");
  assert.equal(result.receiptSha256, modelCapabilityReceiptSha256(receipt));
  assert.deepEqual(validateWorkModelCapabilityReceipt(receipt), []);
  assert.doesNotMatch(JSON.stringify(result), /127\.0\.0\.1|qualified-local-model|PIXEL_CONTEXT/u);
  assert.doesNotMatch(receiptText, /<context>|printf pixel|OLD_VALUE/u);
  if (process.platform !== "win32") assert.equal((await stat(output)).mode & 0o077, 0);
  await assert.rejects(main(["--config", configPath, "--output", output]), /new private file/);

  forceFailure = true;
  const failedOutput = join(root, "qualification-failed.json");
  await assert.rejects(run(process.execPath, [runnerPath, "--config", configPath, "--output", failedOutput], { timeout: 30000, maxBuffer: 1024 * 1024 }), (error) => {
    assert.equal(error.code, 2);
    const summary = JSON.parse(error.stdout.trim());
    assert.equal(summary.status, "failed");
    assert.doesNotMatch(error.stdout + error.stderr, /qualified-local-model|127\.0\.0\.1|PIXEL_CONTEXT/u);
    return true;
  });
  assert.equal(JSON.parse(await readFile(failedOutput, "utf8")).status, "failed");
  assert.equal(requests.length, 30);
});
