import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { mkdir, mkdtemp, readFile, readdir, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  createModelProxy, createModelProxyFromConfigFile, modelProxyContract, validateModelProxyConfig, WorkModelProxyError, writeModelProxyReport,
} from "../deploy/work-model-proxy/proxy.mjs";
import { inferencePolicySha256, validateExactInferencePolicy } from "../deploy/work-model-proxy/inference-policy.mjs";

function exactInference(overrides = {}) {
  return {
    enforcement: "exact-request-boundary-v1", wireApi: "openai-chat-completions",
    temperaturePermille: 700, topPPermille: 950, topK: 40, minPPermille: 50,
    repeatPenaltyPermille: 1100, seed: 42, reasoningEffort: "backend-default",
    reasoningVisibility: "hidden", stream: true, maxOutputTokens: 1024, toolEncoding: "function",
    ...overrides,
  };
}

async function listen(server) {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  return server.address().port;
}

async function close(server) {
  if (!server.listening) return;
  const closed = new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  server.closeAllConnections?.();
  await closed;
}

async function unusedPort() {
  const server = createServer();
  const port = await listen(server);
  await close(server);
  return port;
}

function config(backendPort, proxyPort, overrides = {}) {
  const { budgets: budgetOverrides = {}, ...configOverrides } = overrides;
  const value = {
    schemaVersion: 1,
    jobId: "work-1786366800000-abcdef123456",
    claimId: "workclaim-1786366800000-abcdef123456",
    planSha256: "a".repeat(64),
    provider: "llama.cpp",
    modelId: "local-fixture",
    contextWindow: 32768,
    supportsVision: false,
    backendOrigin: `http://127.0.0.1:${backendPort}`,
    listenHost: "127.0.0.1",
    listenPort: proxyPort,
    allowedClientIpv4: "127.0.0.1",
    allowedTools: ["glob", "grep", "read"],
    receiptPath: "/run/pixel-work-output/model-proxy-receipt.json",
    qualification: {
      qualificationId: "modelqual-1786366740000-abcdef123456",
      receiptSha256: "b".repeat(64), casesSha256: "c".repeat(64), evaluatorSha256: "d".repeat(64),
      profile: "scout", maxContextTokens: 32768, maxOutputTokens: 1024, exactUsage: true,
    },
    inference: null,
    budgets: {
      maxRuntimeSeconds: 30,
      maxModelRequests: 2,
      maxInputTokens: 65536,
      maxOutputTokens: 32,
      maxNetworkBytes: 1024 * 1024,
      maxRequestBytes: 128 * 1024,
      maxResponseBytes: 128 * 1024,
      maxRequestSeconds: 5,
    },
    ...configOverrides,
  };
  value.budgets = { ...value.budgets, ...budgetOverrides };
  if (value.provider === "vllm" && configOverrides.inference === undefined) value.inference = exactInference();
  return value;
}

function responsesBody(overrides = {}) {
  return {
    model: "local-fixture",
    stream: true,
    max_output_tokens: 1000,
    input: [{ role: "user", content: [{ type: "input_text", text: "Inspect the fixture." }] }],
    tools: ["read", "grep", "glob"].map((name) => ({ type: "function", name, description: name, parameters: { type: "object" } })),
    ...overrides,
  };
}

function chatBody(overrides = {}) {
  return {
    model: "local-fixture", stream: true, max_tokens: 1000,
    messages: [{ role: "user", content: "Inspect the fixture." }],
    tools: ["read", "grep", "glob"].map((name) => ({ type: "function", function: { name, description: name, parameters: { type: "object" } } })),
    ...overrides,
  };
}

async function post(base, path, body, headers = {}) {
  return fetch(`${base}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
}

async function status(promise) {
  const response = await promise;
  await response.arrayBuffer();
  return response.status;
}

async function withProxy(handler, options, run) {
  const backend = createServer(handler);
  const backendPort = await listen(backend);
  const proxyPort = await unusedPort();
  const proxy = createModelProxy(config(backendPort, proxyPort, options));
  await proxy.listen();
  try {
    await run({ proxy, base: `http://127.0.0.1:${proxyPort}` });
  } finally {
    await proxy.close();
    await close(backend);
  }
}

test("model proxy config binds one private peer, backend alias, claim, and budget", async () => {
  const value = config(8080, 8081);
  assert.equal(validateModelProxyConfig(value).backendOrigin, "http://127.0.0.1:8080");
  const extended = config(8080, 8081, { allowedTools: ["glob", "grep", "pixel_cap_fixture_analyze_12345678", "read"] });
  assert.deepEqual(validateModelProxyConfig(extended).allowedTools, extended.allowedTools);
  const delegated = config(8080, 8081, { allowedTools: ["read", "task", "yield"] });
  assert.deepEqual(validateModelProxyConfig(delegated).allowedTools, delegated.allowedTools);
  const assistant = config(8080, 8081, {
    allowedTools: ["pixel_calendar_list", "pixel_gmail_search", "pixel_limb_status"],
    qualification: { ...config(8080, 8081).qualification, profile: "assistant" },
  });
  assert.deepEqual(validateModelProxyConfig(assistant).allowedTools, assistant.allowedTools);
  assert.equal(modelProxyContract.maxCapabilityTools, 16);
  assert.equal(modelProxyContract.maxPixelPluginTools, 128);
  for (const name of ["apply_patch", "exec", "process", "sessions_spawn", "memory_search", "web_search"]) {
    assert.ok(modelProxyContract.tools.includes(name), name);
  }
  assert.match("pixel_frontier_plan_review", new RegExp(modelProxyContract.pixelPluginToolPattern, "u"));
  const exact = validateModelProxyConfig(config(8080, 8081, { provider: "vllm" }));
  assert.equal(exact.provider, "vllm");
  assert.equal(exact.inferencePolicySha256, inferencePolicySha256(exact.inference));
  for (const mutate of [
    (copy) => { copy.backendOrigin = "https://api.example.com"; },
    (copy) => { copy.backendOrigin = "http://api.example.com:8080"; },
    (copy) => { copy.backendOrigin = "http://8.8.8.8:8080"; },
    (copy) => { copy.allowedClientIpv4 = "8.8.8.8"; },
    (copy) => { copy.allowedTools = ["read", "bash"]; },
    (copy) => { copy.allowedTools = ["read", "task"]; },
    (copy) => { copy.allowedTools = ["read", "yield"]; },
    (copy) => { copy.allowedTools = ["glob", "grep", "pixel_cap_Fixture", "read"]; },
    (copy) => { copy.allowedTools = ["glob", "grep", ...Array.from({ length: 17 }, (_, index) => `pixel_cap_fixture_${String(index).padStart(2, "0")}`), "read"]; },
    (copy) => { copy.allowedTools = ["pixel_Gmail_search"]; },
    (copy) => { copy.allowedTools = ["calendar_list"]; },
    (copy) => { copy.allowedTools = [`pixel_${"a".repeat(64)}`]; },
    (copy) => { copy.allowedTools = Array.from({ length: 129 }, (_, index) => `pixel_fixture_tool_${String(index).padStart(3, "0")}`); },
    (copy) => { copy.budgets.maxRequestBytes = copy.budgets.maxNetworkBytes + 1; },
    (copy) => { copy.credential = "forbidden"; },
    (copy) => { copy.receiptPath = "/tmp/attacker-controlled.json"; },
    (copy) => { copy.provider = "openai"; },
  ]) {
    const hostile = structuredClone(value);
    mutate(hostile);
    assert.throws(() => validateModelProxyConfig(hostile), WorkModelProxyError);
  }
  assert.throws(() => validateModelProxyConfig(config(8080, 8081, { provider: "vllm", inference: null })), WorkModelProxyError);
  assert.throws(() => validateModelProxyConfig(config(8080, 8081, { inference: exactInference() })), WorkModelProxyError);
  assert.throws(() => validateModelProxyConfig(config(8080, 8081, { provider: "vllm", inference: exactInference({ topPPermille: 1001 }) })), WorkModelProxyError);
});

test("explicit OpenClaw and Pixel Assistant tools reach inference while an unlisted valid tool is denied", async () => {
  let backendRequests = 0;
  const allowedTools = [
    "apply_patch", "edit", "exec", "memory_get", "memory_search", "pixel_calendar_list",
    "pixel_gmail_search", "pixel_limb_status", "process", "read", "sessions_history",
    "sessions_list", "sessions_send", "sessions_spawn", "sessions_yield", "subagents", "web_fetch",
    "web_search", "write",
  ];
  await withProxy(async (request, response) => {
    backendRequests += 1;
    for await (const chunk of request) { /* drain */ }
    const payload = [
      'data: {"choices":[{"delta":{"content":"ASSISTANT_OK"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, {
    allowedTools,
    qualification: { ...config(8080, 8081).qualification, profile: "assistant" },
  }, async ({ base, proxy }) => {
    const tools = allowedTools.map((name) => ({
      type: "function", function: { name, description: name, parameters: { type: "object" } },
    }));
    assert.equal(await status(post(base, "/v1/chat/completions", chatBody({ tools }))), 200);
    assert.equal(backendRequests, 1);
    assert.equal(proxy.report().lastRequestToolCount, allowedTools.length);
    assert.equal(
      proxy.report().lastRequestToolsSha256,
      createHash("sha256").update(allowedTools.join("\n"), "utf8").digest("hex"),
    );
    const widened = [{
      type: "function", function: { name: "pixel_social_feed", description: "unlisted", parameters: { type: "object" } },
    }];
    assert.equal(await status(post(base, "/v1/chat/completions", chatBody({ tools: widened }))), 400);
    assert.equal(backendRequests, 1);
    assert.equal(proxy.report().modelRequests, 1);
    assert.equal(proxy.report().deniedRequests, 1);
  });
});

test("file-backed model proxy startup consumes one normalized validation result", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-proxy-config-"));
  const path = join(root, "model-proxy.json");
  const proxyPort = await unusedPort();
  await writeFile(path, `${JSON.stringify(config(8080, proxyPort))}\n`, { mode: 0o600 });
  const proxy = await createModelProxyFromConfigFile(path, { reportWriter: async () => {} });
  await proxy.listen();
  try {
    const response = await fetch(`http://127.0.0.1:${proxyPort}/healthz`);
    assert.equal(response.status, 200);
    assert.equal((await response.json()).status, "ready");
  } finally {
    await proxy.close();
  }
});

test("vLLM adapter enforces exact sampling, hidden reasoning, and usage controls without exposing Responses API", async () => {
  const observed = [];
  await withProxy(async (request, response) => {
    const chunks = []; for await (const chunk of request) chunks.push(chunk);
    observed.push({ path: request.url, body: JSON.parse(Buffer.concat(chunks).toString("utf8")) });
    const payload = [
      'data: {"choices":[{"delta":{"content":"VLLM_OK"},"finish_reason":"stop"}],"usage":{"prompt_tokens":16,"completion_tokens":5}}\n\n',
      'data: {"choices":[],"usage":{"prompt_tokens":16,"completion_tokens":5}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { provider: "vllm", budgets: { maxModelRequests: 2 } }, async ({ base, proxy }) => {
    const response = await post(base, "/v1/chat/completions", chatBody({
      reasoning_effort: "max", include_reasoning: true, thinking_token_budget: 500,
      temperature: 2, top_p: 0.1, top_k: 1, min_p: 0.9, repetition_penalty: 9, seed: 999,
      max_completion_tokens: 9999, presence_penalty: 2, frequency_penalty: -2, n: 8, best_of: 8,
      use_beam_search: true, ignore_eos: true, min_tokens: 99, stop: ["DRIFT"], logit_bias: { 1: 100 },
      structured_outputs: { json: { type: "object" } }, guided_json: { type: "string" },
      logits_processors: ["hostile"], priority: -1,
      chat_template: "untrusted", chat_template_kwargs: { widen: true }, stream_options: { include_usage: false },
    }));
    assert.equal(response.status, 200);
    await response.arrayBuffer();
    assert.equal(await status(post(base, "/responses", responsesBody())), 404);
    const structuredResponse = await post(base, "/v1/chat/completions", chatBody({ response_format: { type: "json_object" } }));
    assert.equal(structuredResponse.status, 200);
    await structuredResponse.arrayBuffer();
    assert.equal(observed.length, 2);
    assert.equal(observed[0].path, "/v1/chat/completions");
    assert.equal(observed[0].body.temperature, 0.7);
    assert.equal(observed[0].body.top_p, 0.95);
    assert.equal(observed[0].body.top_k, 40);
    assert.equal(observed[0].body.min_p, 0.05);
    assert.equal(observed[0].body.repetition_penalty, 1.1);
    assert.equal(observed[0].body.seed, 42);
    assert.equal(observed[0].body.presence_penalty, 0);
    assert.equal(observed[0].body.frequency_penalty, 0);
    assert.equal(observed[0].body.n, 1);
    assert.equal(observed[0].body.ignore_eos, false);
    assert.equal(observed[0].body.min_tokens, 0);
    assert.equal(observed[0].body.reasoning_effort, undefined);
    assert.equal(observed[1].body.reasoning_effort, "none");
    assert.deepEqual(observed[1].body.response_format, { type: "json_object" });
    assert.equal(observed[0].body.include_reasoning, false);
    assert.equal(observed[0].body.max_tokens, 32);
    assert.deepEqual(observed[0].body.stream_options, { include_usage: true });
    for (const field of ["thinking_token_budget", "max_completion_tokens", "best_of", "use_beam_search", "stop", "logit_bias", "chat_template", "chat_template_kwargs", "structured_outputs", "guided_json", "logits_processors", "priority"]) assert.equal(observed[0].body[field], undefined);
    assert.equal(proxy.report().inferencePolicySha256, inferencePolicySha256(exactInference()));
  });
});

test("vLLM adapter rejects a backend that ignores the exact streaming contract", async () => {
  await withProxy((_request, response) => {
    const payload = JSON.stringify({ choices: [], usage: { prompt_tokens: 1, completion_tokens: 1 } });
    response.writeHead(200, { "content-type": "application/json", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { provider: "vllm" }, async ({ base, proxy }) => {
    assert.equal(await status(post(base, "/v1/chat/completions", chatBody())), 502);
    assert.equal(proxy.report().backendFailures, 1);
    assert.equal(proxy.report().lastFailureCode, "invalid-backend-response");
  });
});

test("vLLM adapter never discloses a truncated length completion as success", async () => {
  await withProxy(async (_request, response) => {
    for await (const chunk of _request) { /* drain */ }
    const payload = [
      'data: {"choices":[{"delta":{"content":"PARTIAL"},"finish_reason":"length"}],"usage":{"prompt_tokens":16,"completion_tokens":5}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { provider: "vllm", budgets: { maxModelRequests: 2 } }, async ({ proxy, base }) => {
    const response = await post(base, "/v1/chat/completions", chatBody());
    assert.equal(response.status, 502);
    await response.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, "backend-completion-truncated");
    assert.equal(proxy.report().backendFailures, 1);
    assert.equal(proxy.report().inputTokens, 16);
    assert.equal(proxy.report().outputTokens, 5);
  });
});

test("Responses adapter withholds an incomplete backend completion as success", async () => {
  await withProxy(async (_request, response) => {
    for await (const chunk of _request) { /* drain */ }
    const payload = [
      'data: {"type":"response.completed","response":{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"usage":{"input_tokens":7,"output_tokens":2}}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { budgets: { maxModelRequests: 2 } }, async ({ proxy, base }) => {
    const response = await post(base, "/responses", responsesBody());
    assert.equal(response.status, 502);
    await response.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, "backend-completion-truncated");
    assert.equal(proxy.report().backendFailures, 1);
    assert.equal(proxy.report().inputTokens, 7);
    assert.equal(proxy.report().outputTokens, 2);
  });
});

test("Responses adapter withholds an incomplete JSON completion as success", async () => {
  await withProxy(async (_request, response) => {
    for await (const chunk of _request) { /* drain */ }
    const payload = JSON.stringify({ id: "resp_1", status: "incomplete", incomplete_details: { reason: "max_output_tokens" }, usage: { input_tokens: 7, output_tokens: 3 } });
    response.writeHead(200, { "content-type": "application/json", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { budgets: { maxModelRequests: 2 } }, async ({ proxy, base }) => {
    const response = await post(base, "/responses", responsesBody());
    assert.equal(response.status, 502);
    await response.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, "backend-completion-truncated");
    assert.equal(proxy.report().backendFailures, 1);
    assert.equal(proxy.report().inputTokens, 7);
    assert.equal(proxy.report().outputTokens, 3);
  });
});

test("Responses adapter withholds a non-token incomplete completion as incomplete", async () => {
  await withProxy(async (_request, response) => {
    for await (const chunk of _request) { /* drain */ }
    const payload = [
      'data: {"type":"response.completed","response":{"status":"incomplete","incomplete_details":{"reason":"content_filter"},"usage":{"input_tokens":7,"output_tokens":2}}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { budgets: { maxModelRequests: 2 } }, async ({ proxy, base }) => {
    const response = await post(base, "/responses", responsesBody());
    assert.equal(response.status, 502);
    await response.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, "backend-completion-incomplete");
    assert.equal(proxy.report().backendFailures, 1);
    assert.equal(proxy.report().inputTokens, 7);
    assert.equal(proxy.report().outputTokens, 2);
  });
});

test("split-event completion resolves to truncated when a later event reports a token reason", async () => {
  await withProxy(async (_request, response) => {
    for await (const chunk of _request) { /* drain */ }
    const payload = [
      'data: {"type":"response.completed","response":{"status":"incomplete","incomplete_details":{"reason":"content_filter"},"usage":{"input_tokens":7,"output_tokens":2}}}\n\n',
      'data: {"type":"response.completed","response":{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"usage":{"input_tokens":7,"output_tokens":2}}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { budgets: { maxModelRequests: 2 } }, async ({ proxy, base }) => {
    const response = await post(base, "/responses", responsesBody());
    assert.equal(response.status, 502);
    await response.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, "backend-completion-truncated");
    assert.equal(proxy.report().backendFailures, 1);
    assert.equal(proxy.report().inputTokens, 7);
    assert.equal(proxy.report().outputTokens, 2);
  });
});

test("Responses adapter withholds failed and cancelled terminal completions under a distinct code", async () => {
  let terminal = "failed";
  await withProxy(async (_request, response) => {
    for await (const chunk of _request) { /* drain */ }
    const payload = [
      `data: {"type":"response.failed","response":{"status":"${terminal}","usage":{"input_tokens":7,"output_tokens":2}}}\n\n`,
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { budgets: { maxModelRequests: 2 } }, async ({ proxy, base }) => {
    const failed = await post(base, "/responses", responsesBody());
    assert.equal(failed.status, 502);
    await failed.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, "backend-completion-failed");
    terminal = "cancelled";
    const cancelled = await post(base, "/responses", responsesBody());
    assert.equal(cancelled.status, 502);
    await cancelled.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, "backend-completion-failed");
    assert.equal(proxy.report().backendFailures, 2);
    assert.equal(proxy.report().inputTokens, 14);
    assert.equal(proxy.report().outputTokens, 4);
  });
});

test("exact inference policy rejects an inadmissible hidden-reasoning budget at config time", () => {
  const insufficient = (policy) => {
    try { validateExactInferencePolicy(policy); } catch (error) { return error.code; }
    return null;
  };
  assert.equal(insufficient(exactInference({ reasoningEffort: "high", maxOutputTokens: 8192 })), null);
  assert.equal(insufficient(exactInference({ reasoningEffort: "high", maxOutputTokens: 1087 })), "inference-budget-insufficient");
  assert.equal(insufficient(exactInference({ reasoningEffort: "max", maxOutputTokens: 2111 })), "inference-budget-insufficient");
  assert.equal(insufficient(exactInference({ reasoningEffort: "high", maxOutputTokens: 1088 })), null);
});

test("repeated truncated completions charge the lease and exhaust the output budget", async () => {
  await withProxy(async (_request, response) => {
    for await (const chunk of _request) { /* drain */ }
    const payload = [
      'data: {"choices":[{"delta":{"content":"PARTIAL"},"finish_reason":"length"}],"usage":{"prompt_tokens":5,"completion_tokens":20}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { provider: "vllm", budgets: { maxModelRequests: 3, maxOutputTokens: 40 } }, async ({ proxy, base }) => {
    assert.equal(await status(post(base, "/v1/chat/completions", chatBody())), 502);
    assert.equal(proxy.report().lastFailureCode, "backend-completion-truncated");
    assert.equal(proxy.report().backendFailures, 1);
    assert.equal(proxy.report().inputTokens, 5);
    assert.equal(proxy.report().outputTokens, 20);
    assert.equal(await status(post(base, "/v1/chat/completions", chatBody())), 502);
    assert.equal(proxy.report().lastFailureCode, "backend-completion-truncated");
    assert.equal(proxy.report().backendFailures, 2);
    assert.equal(proxy.report().inputTokens, 10);
    assert.equal(proxy.report().outputTokens, 40);
    assert.equal(await status(post(base, "/v1/chat/completions", chatBody())), 429);
    assert.equal(proxy.report().lastFailureCode, "token-budget-exhausted");
    assert.equal(proxy.report().backendFailures, 2);
    assert.equal(proxy.report().modelRequests, 2);
    assert.equal(proxy.report().inputTokens, 10);
    assert.equal(proxy.report().outputTokens, 40);
  });
});

test("truncated completion failure counters and usage are durably persisted", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-receipt-persist-"));
  const receiptPath = join(root, "receipt.json");
  const backend = createServer(async (request, response) => {
    for await (const chunk of request) { /* drain */ }
    const payload = [
      'data: {"choices":[{"delta":{"content":"PARTIAL"},"finish_reason":"length"}],"usage":{"prompt_tokens":6,"completion_tokens":4}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  });
  const backendPort = await listen(backend);
  const proxyPort = await unusedPort();
  const proxy = createModelProxy(config(backendPort, proxyPort, { provider: "vllm", budgets: { maxModelRequests: 2 } }), {
    reportWriter: async (_path, report) => writeModelProxyReport(receiptPath, report),
  });
  await proxy.listen();
  try {
    const response = await post(`http://127.0.0.1:${proxyPort}`, "/v1/chat/completions", chatBody());
    assert.equal(response.status, 502);
    await response.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, "backend-completion-truncated");
  } finally {
    await proxy.close();
    await close(backend);
  }
  const persisted = JSON.parse(await readFile(receiptPath, "utf8"));
  assert.equal(persisted.lastFailureCode, "backend-completion-truncated");
  assert.equal(persisted.backendFailures, 1);
  assert.equal(persisted.deniedRequests, 1);
  assert.equal(persisted.inputTokens, 6);
  assert.equal(persisted.outputTokens, 4);
  assert.doesNotMatch(JSON.stringify(persisted), /PARTIAL/u);
  });

test("hidden reasoning requests require an adequate output budget and cannot widen it", async () => {
  let backendRequests = 0;
  await withProxy(async (request, response) => {
    backendRequests += 1;
    response.writeHead(500).end();
  }, {
    provider: "vllm",
    inference: exactInference({ reasoningEffort: "high", maxOutputTokens: 4096 }),
    qualification: { ...config(8080, 8081).qualification, maxOutputTokens: 4096 },
    budgets: { maxModelRequests: 2 },
  }, async ({ proxy, base }) => {
    const response = await post(base, "/v1/chat/completions", chatBody({ max_tokens: 1000 }));
    assert.equal(response.status, 400);
    await response.arrayBuffer();
    assert.equal(backendRequests, 0);
    assert.equal(proxy.report().lastFailureCode, "inference-budget-insufficient");
    assert.equal(proxy.report().backendFailures, 0);
    assert.equal(proxy.report().deniedRequests, 1);
    assert.equal(proxy.report().modelRequests, 0);
  });
});

test("hidden reasoning requests with an adequate budget pass through unchanged", async () => {
  await withProxy(async (_request, response) => {
    for await (const chunk of _request) { /* drain */ }
    const payload = [
      'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2}}\n\n',
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, {
    provider: "vllm",
    inference: exactInference({ reasoningEffort: "high", maxOutputTokens: 8192 }),
    qualification: { ...config(8080, 8081).qualification, maxOutputTokens: 8192 },
    budgets: { maxModelRequests: 2, maxOutputTokens: 8192 },
  }, async ({ proxy, base }) => {
    const response = await post(base, "/v1/chat/completions", chatBody({ max_tokens: 8192 }));
    assert.equal(response.status, 200);
    await response.arrayBuffer();
    assert.equal(proxy.report().lastFailureCode, null);
    assert.equal(proxy.report().backendFailures, 0);
    assert.equal(proxy.report().inputTokens, 5);
    assert.equal(proxy.report().outputTokens, 2);
  });
});

test("backend status failures remain content-free but diagnostically distinct", async () => {
  await withProxy(async (_request, response) => {
    response.writeHead(418, { "content-type": "application/json" });
    response.end(JSON.stringify({ error: "sensitive local backend detail" }));
  }, {}, async ({ base, proxy }) => {
    const response = await post(base, "/responses", responsesBody());
    assert.equal(response.status, 502);
    assert.equal(proxy.report().lastFailureCode, "backend-status-418");
    assert.doesNotMatch(JSON.stringify(proxy.report()), /sensitive local backend detail/u);
  });
});

test("delegated model requests may narrow a task lease and use only its completion primitive", async () => {
  let backendRequests = 0;
  await withProxy(async (request, response) => {
    backendRequests += 1;
    for await (const chunk of request) { /* drain */ }
    const payload = "data: {\"response\":{\"usage\":{\"input_tokens\":1,\"output_tokens\":1}}}\n\ndata: [DONE]\n\n";
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { allowedTools: ["read", "task", "yield"] }, async ({ base }) => {
    const tools = ["read", "yield"].map((name) => ({ type: "function", name, description: name, parameters: { type: "object" } }));
    assert.equal(await status(post(base, "/responses", responsesBody({ tools }))), 200);
    assert.equal(await status(post(base, "/responses", responsesBody({ tools: [...tools, { type: "function", name: "write" }] }))), 400);
    assert.equal(backendRequests, 1);
  });
});

test("model proxy receipt is atomic, private, and content-free", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-receipt-"));
  const directory = join(root, "private");
  const path = join(directory, "receipt.json");
  await mkdir(directory, { mode: 0o700 });
  const report = {
    schemaVersion: 1, jobId: "work-1786366800000-abcdef123456", modelRequests: 1,
    inputTokens: 7, outputTokens: 2, contentStored: false, credentialsExposed: false,
  };
  await writeModelProxyReport(path, report);
  assert.deepEqual(JSON.parse(await readFile(path, "utf8")), report);
  if (process.platform !== "win32") assert.equal((await stat(path)).mode & 0o777, 0o600);
  assert.deepEqual(await readdir(directory), ["receipt.json"]);
});

test("discovery is synthesized from the lease and never forwarded", async () => {
  let backendRequests = 0;
  await withProxy((request, response) => {
    backendRequests += 1;
    response.writeHead(500).end();
  }, {}, async ({ base }) => {
    const models = await (await fetch(`${base}/models`)).json();
    const props = await (await fetch(`${base}/props`)).json();
    assert.deepEqual(models.data.map((item) => item.id), ["local-fixture"]);
    assert.equal(models.data[0].meta.n_ctx, 32768);
    assert.equal(props.default_generation_settings.n_ctx, 32768);
    assert.equal(props.modalities.vision, false);
    assert.equal(backendRequests, 0);
  });
});

test("bounded inference strips client headers, clamps output, and records content-free usage", async () => {
  let observed;
  await withProxy(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    observed = { path: request.url, headers: request.headers, body: JSON.parse(Buffer.concat(chunks).toString("utf8")) };
    const payload = [
      "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"fixture result\"}\n\n",
      "event: response.completed\ndata: {\"type\":\"response.completed\",\"response\":{\"usage\":{\"input_tokens\":7,\"output_tokens\":2}}}\n\n",
      "data: [DONE]\n\n",
    ].join("");
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, {}, async ({ proxy, base }) => {
    const response = await post(base, "/responses", responsesBody(), { "x-client-note": "not-forwarded" });
    assert.equal(response.status, 200);
    assert.match(await response.text(), /fixture result/);
    assert.equal(observed.path, "/responses");
    assert.equal(observed.body.model, "local-fixture");
    assert.equal(observed.body.max_output_tokens, 32);
    assert.equal(observed.body.store, false);
    assert.equal(observed.headers["x-client-note"], undefined);
    assert.equal(observed.headers.authorization, undefined);
    assert.equal(observed.headers["accept-encoding"], "identity");
    assert.deepEqual(proxy.report(), {
      schemaVersion: 1,
      jobId: "work-1786366800000-abcdef123456",
      claimId: "workclaim-1786366800000-abcdef123456",
      planSha256: "a".repeat(64),
      qualificationId: "modelqual-1786366740000-abcdef123456",
      qualificationReceiptSha256: "b".repeat(64),
      inferencePolicySha256: null,
      modelIdSha256: createHash("sha256").update("local-fixture", "utf8").digest("hex"),
      proxyConfigSha256: proxy.report().proxyConfigSha256,
      allowedToolsSha256: createHash("sha256").update("glob\ngrep\nread", "utf8").digest("hex"),
      startedAt: proxy.report().startedAt,
      modelRequests: 1,
      inputTokens: 7,
      outputTokens: 2,
      networkBytes: proxy.report().networkBytes,
      deniedRequests: 0,
      backendFailures: 0,
      activeInference: false,
      lastFailureCode: null,
      lastRequestToolCount: 3,
      lastRequestToolsSha256: createHash("sha256").update("glob\ngrep\nread", "utf8").digest("hex"),
      contentStored: false,
      credentialsExposed: false,
      arbitraryNetwork: false,
      externalEffects: false,
    });
    assert.doesNotMatch(JSON.stringify(proxy.report()), /fixture result|Inspect the fixture|x-client-note/);
  });
});

test("qualified envelope clamps output and rejects missing or over-context usage before disclosure", async () => {
  let mode = "valid", observedOutput;
  await withProxy(async (request, response) => {
    const chunks = []; for await (const chunk of request) chunks.push(chunk);
    observedOutput = JSON.parse(Buffer.concat(chunks).toString("utf8")).max_output_tokens;
    const payload = mode === "missing"
      ? "data: {\"type\":\"response.completed\"}\n\ndata: [DONE]\n\n"
      : `data: {"response":{"usage":{"input_tokens":${mode === "context" ? 32769 : 1},"output_tokens":1}}}\n\ndata: [DONE]\n\n`;
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { qualification: { ...config(8080, 8081).qualification, maxOutputTokens: 8 }, budgets: { maxModelRequests: 3 } }, async ({ base }) => {
    assert.equal(await status(post(base, "/responses", responsesBody({ max_output_tokens: 1000 }))), 200);
    assert.equal(observedOutput, 8);
    mode = "missing";
    assert.equal(await status(post(base, "/responses", responsesBody({ max_output_tokens: 1 }))), 502);
    mode = "context";
    assert.equal(await status(post(base, "/responses", responsesBody({ max_output_tokens: 1 }))), 502);
  });
});

test("proxy denies route, credential, model, retention, and tool widening before inference", async () => {
  let backendRequests = 0;
  await withProxy((request, response) => {
    backendRequests += 1;
    response.writeHead(500).end();
  }, {}, async ({ proxy, base }) => {
    assert.equal(await status(fetch(`${base}/v1/models`)), 404);
    assert.equal(await status(post(base, "/responses", responsesBody(), { authorization: "Bearer not-a-secret" })), 400);
    assert.equal(await status(post(base, "/responses", responsesBody({ model: "other" }))), 400);
    assert.equal(await status(post(base, "/responses", responsesBody({ store: true }))), 400);
    assert.equal(await status(post(base, "/responses", responsesBody({ tools: [{ type: "function", name: "bash" }] }))), 400);
    assert.equal(backendRequests, 0);
    assert.equal(proxy.report().modelRequests, 0);
    assert.equal(proxy.report().deniedRequests, 5);
  });
});

test("one active inference and request, token, response, and redirect budgets fail closed", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let mode = "gate";
  await withProxy(async (request, response) => {
    for await (const chunk of request) { /* drain */ }
    if (mode === "gate") await gate;
    if (mode === "redirect") {
      response.writeHead(302, { location: "/responses" }).end();
      return;
    }
    const payload = mode === "oversize" ? "x".repeat(2048) : "data: {\"response\":{\"usage\":{\"input_tokens\":1,\"output_tokens\":1}}}\n\ndata: [DONE]\n\n";
    response.writeHead(200, { "content-type": "text/event-stream", "content-length": Buffer.byteLength(payload) });
    response.end(payload);
  }, { budgets: { maxModelRequests: 3, maxInputTokens: 2048, maxOutputTokens: 3, maxResponseBytes: 1024 } }, async ({ proxy, base }) => {
    const first = post(base, "/responses", responsesBody({ max_output_tokens: 1 }));
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(await status(post(base, "/responses", responsesBody({ max_output_tokens: 1 }))), 409);
    release();
    assert.equal(await status(first), 200);
    mode = "redirect";
    assert.equal(await status(post(base, "/responses", responsesBody({ max_output_tokens: 1 }))), 502);
    mode = "oversize";
    assert.equal(await status(post(base, "/responses", responsesBody({ max_output_tokens: 1 }))), 502);
    assert.equal(proxy.report().backendFailures, 2);
    assert.equal(proxy.report().activeInference, false);
  });
});
