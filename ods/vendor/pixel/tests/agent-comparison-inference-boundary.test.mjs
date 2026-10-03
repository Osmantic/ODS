import assert from "node:assert/strict";
import test from "node:test";

import { chatSseToResponses, createInferenceBoundary, responsesRequestToChat } from "../deploy/agent-comparison/inference-boundary.mjs";
import { inferencePolicySha256 } from "../deploy/work-model-proxy/inference-policy.mjs";

function createTestBoundary(rawConfig, options) {
  return createInferenceBoundary(
    { ...rawConfig, listenPort: 0 },
    { ...options, testOnlyAllowEphemeralPort: true },
  );
}

async function listenTestBoundary(boundary) {
  const port = await boundary.listen();
  assert.ok(Number.isSafeInteger(port) && port >= 1 && port <= 65535);
  return port;
}

function inference() {
  return {
    enforcement: "exact-request-boundary-v1", wireApi: "openai-chat-completions",
    temperaturePermille: 700, topPPermille: 950, topK: 40, minPPermille: 50,
    repeatPenaltyPermille: 1100, seed: 42, reasoningEffort: "backend-default",
    reasoningVisibility: "hidden", stream: true, maxOutputTokens: 4096, toolEncoding: "function",
  };
}

function config(port, overrides = {}) {
  return {
    schemaVersion: 1, runId: "outcomerun-1786550400003-aaaaaaaaaaac", modelId: "DeepSeek-V4-Flash-0731",
    backendOrigin: "http://pixel-local-backend:8080", listenHost: "0.0.0.0", listenPort: port,
    receiptPath: "/run/pixel-outcome-output/inference-boundary-receipt.json",
    maxRequestBytes: 1048576, maxResponseBytes: 1048576, maxRequestSeconds: 30,
    maxModelRequests: 20, maxInputTokens: 100000, maxOutputTokens: 20000,
    ingressWireApi: "openai-responses", inference: inference(), ...overrides,
  };
}

function responsesBody(overrides = {}) {
  return {
    model: "DeepSeek-V4-Flash-0731", instructions: "Pinned Codex system instructions.",
    input: [{ type: "message", id: "msg_fixture", role: "user", content: [{ type: "input_text", text: "private fixture must not enter receipts" }] }],
    tools: [], tool_choice: "auto", parallel_tool_calls: true, reasoning: { summary: "auto" },
    store: false, stream: true, include: ["reasoning.encrypted_content"], prompt_cache_key: "fixture-session",
    client_metadata: { thread_id: "fixture-thread" }, ...overrides,
  };
}

function chatSse({ text = "ok", toolCalls = [], inputTokens = 8, outputTokens = 2, finishReason = null } = {}) {
  const delta = { role: "assistant", content: text || null };
  if (toolCalls.length) delta.tool_calls = toolCalls.map((call, index) => ({
    index, id: call.id, type: "function", function: { name: call.name, arguments: call.arguments },
  }));
  const finish = finishReason ?? (toolCalls.length ? "tool_calls" : "stop");
  return [
    `data: ${JSON.stringify({ id: "chatcmpl_fixture", object: "chat.completion.chunk", created: 1786550400, model: "DeepSeek-V4-Flash-0731", choices: [{ index: 0, delta, finish_reason: null }] })}`,
    `data: ${JSON.stringify({ id: "chatcmpl_fixture", object: "chat.completion.chunk", created: 1786550400, model: "DeepSeek-V4-Flash-0731", choices: [{ index: 0, delta: {}, finish_reason: finish }] })}`,
    `data: ${JSON.stringify({ id: "chatcmpl_fixture", object: "chat.completion.chunk", created: 1786550400, model: "DeepSeek-V4-Flash-0731", choices: [], usage: { prompt_tokens: inputTokens, completion_tokens: outputTokens, total_tokens: inputTokens + outputTokens } })}`,
    "data: [DONE]", "",
  ].join("\n\n");
}

test("comparison boundary reserves ephemeral listeners for injected tests", () => {
  assert.throws(() => createInferenceBoundary(config(0), { reportWriter: async () => {} }));
  assert.throws(() => createInferenceBoundary(config(0), {
    testOnlyAllowEphemeralPort: true,
    fetchImpl: async () => new Response(),
  }));
  assert.throws(() => createInferenceBoundary(config(1), {
    testOnlyAllowEphemeralPort: "true",
    reportWriter: async () => {},
  }));
});

test("comparison boundary forces exact DSV4 inference and records only content-free evidence", async () => {
  let port;
  let observed; const receipts = [];
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async (_url, request) => {
      observed = { headers: request.headers, body: JSON.parse(request.body) };
      return new Response(chatSse(), { status: 200, headers: { "content-type": "text/event-stream" } });
    },
    reportWriter: async (_path, report) => { receipts.push(structuredClone(report)); },
  });
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json", authorization: "Bearer must-not-forward" },
      body: JSON.stringify(responsesBody({ max_output_tokens: 99999 })),
    });
    assert.equal(response.status, 200);
    assert.match(await response.text(), /response\.output_text\.delta/);
  } finally { await boundary.close(); }
  assert.deepEqual({
    temperature: observed.body.temperature, top_p: observed.body.top_p, top_k: observed.body.top_k,
    min_p: observed.body.min_p, repetition_penalty: observed.body.repetition_penalty, seed: observed.body.seed,
    max_tokens: observed.body.max_tokens, include_reasoning: observed.body.include_reasoning,
  }, { temperature: 0.7, top_p: 0.95, top_k: 40, min_p: 0.05, repetition_penalty: 1.1, seed: 42, max_tokens: 4096, include_reasoning: false });
  for (const field of ["instructions", "input", "include", "reasoning", "prompt_cache_key", "client_metadata"]) assert.equal(observed.body[field], undefined);
  assert.deepEqual(observed.body.messages, [
    { role: "system", content: "Pinned Codex system instructions." },
    { role: "user", content: "private fixture must not enter receipts" },
  ]);
  assert.equal(observed.headers.authorization, undefined);
  const receipt = receipts.at(-1);
  assert.equal(receipt.requests, 1);
  assert.equal(receipt.inputTokens, 8);
  assert.equal(receipt.outputTokens, 2);
  assert.equal(receipt.ingressWireApi, "openai-responses");
  assert.equal(receipt.backendWireApi, "openai-chat-completions");
  assert.equal(receipt.adapter, "responses-to-chat-v2");
  assert.ok(receipt.responseBytes > 0);
  assert.ok(receipt.backendResponseBytes > 0);
  assert.equal(receipt.inferencePolicySha256, inferencePolicySha256(inference()));
  assert.equal(receipt.contentStored, false);
  assert.doesNotMatch(JSON.stringify(receipts), /private fixture|must-not-forward/);
});

test("comparison boundary enforces aggregate request and token budgets before disclosing a response", async () => {
  let port; let backendRequests = 0;
  const boundary = createTestBoundary(config(0, {
    maxModelRequests: 1, maxInputTokens: 1000, maxOutputTokens: 3,
  }), {
    fetchImpl: async () => {
      backendRequests += 1;
      return new Response(chatSse(), {
        status: 200, headers: { "content-type": "text/event-stream" },
      });
    },
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    const first = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(responsesBody({ max_output_tokens: 3 })),
    });
    assert.equal(first.status, 200); await first.arrayBuffer();
    const second = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(responsesBody()),
    });
    assert.equal(second.status, 429); await second.arrayBuffer();
  } finally { await boundary.close(); }
  assert.equal(backendRequests, 1);
  assert.deepEqual(
    { requests: boundary.report().requests, inputTokens: boundary.report().inputTokens, outputTokens: boundary.report().outputTokens },
    { requests: 1, inputTokens: 8, outputTokens: 2 },
  );
});

test("comparison boundary preserves Codex function tools and normalizes vLLM stop-with-tool-call responses", async () => {
  let port; let observed;
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async (_url, request) => {
      observed = JSON.parse(request.body);
      return new Response(chatSse({
        text: "", outputTokens: 7,
        toolCalls: [{ id: "call_next", name: "update_plan", arguments: '{"plan":[{"step":"Verify","status":"in_progress"}]}' }],
        finishReason: "stop",
      }), { status: 200, headers: { "content-type": "text/event-stream" } });
    },
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(responsesBody({
        input: [
          { type: "message", id: "msg_user", role: "user", content: [{ type: "input_text", text: "Continue." }] },
          { type: "message", id: "msg_assistant", role: "assistant", status: "completed", content: [{ type: "output_text", text: "I will update the plan.", annotations: [] }] },
          { type: "function_call", id: "fc_previous", call_id: "call_previous", name: "update_plan", arguments: '{"plan":[]}' },
          { type: "function_call_output", id: "fco_previous", call_id: "call_previous", output: "Plan updated" },
        ],
        tools: [{ type: "function", name: "update_plan", description: "Update the plan.", strict: false, parameters: { type: "object", properties: {}, additionalProperties: false } }],
      })),
    });
    assert.equal(response.status, 200);
    const payload = await response.text();
    assert.match(payload, /response\.function_call_arguments\.done/);
    assert.match(payload, /call_next/);
  } finally { await boundary.close(); }
  assert.deepEqual(observed.messages.slice(-4), [
    { role: "user", content: "Continue." },
    { role: "assistant", content: "I will update the plan." },
    { role: "assistant", content: null, tool_calls: [{ id: "call_previous", type: "function", function: { name: "update_plan", arguments: '{"plan":[]}' } }] },
    { role: "tool", tool_call_id: "call_previous", content: "Plan updated" },
  ]);
  assert.deepEqual(observed.tools, [{
    type: "function",
    function: { name: "update_plan", description: "Update the plan.", parameters: { type: "object", properties: {}, additionalProperties: false }, strict: false },
  }]);
  assert.equal(boundary.report().backendFailures, 0);
});

test("comparison boundary round-trips Codex apply_patch as a custom tool", async () => {
  let port; let observed;
  const patchInput = "*** Begin Patch\n*** Add File: result.txt\n+safe\n*** End Patch";
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async (_url, request) => {
      observed = JSON.parse(request.body);
      return new Response(chatSse({
        text: "", outputTokens: 6,
        toolCalls: [{ id: "call_patch_next", name: "pixel_custom_apply_patch", arguments: JSON.stringify({ input: patchInput }) }],
      }), { status: 200, headers: { "content-type": "text/event-stream" } });
    },
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(responsesBody({
        input: [
          { type: "message", role: "user", content: [{ type: "input_text", text: "Patch the fixture." }] },
          { type: "custom_tool_call", id: "ctc_previous", call_id: "call_patch_previous", name: "apply_patch", input: patchInput, status: "completed" },
          { type: "custom_tool_call_output", id: "ctco_previous", call_id: "call_patch_previous", output: "Done!" },
        ],
        tools: [{
          type: "custom", name: "apply_patch", description: "Apply an exact patch.",
          format: { type: "grammar", syntax: "lark", definition: "start: /[\\s\\S]+/" },
        }],
      })),
    });
    assert.equal(response.status, 200);
    const payload = await response.text();
    assert.match(payload, /response\.custom_tool_call_input\.done/);
    assert.match(payload, /"type":"custom_tool_call"/);
    assert.match(payload, /"name":"apply_patch"/);
    assert.doesNotMatch(payload, /pixel_custom_apply_patch/);
  } finally { await boundary.close(); }
  assert.equal(observed.tools[0].function.name, "pixel_custom_apply_patch");
  assert.deepEqual(observed.messages.slice(-2), [
    { role: "assistant", content: null, tool_calls: [{ id: "call_patch_previous", type: "function", function: { name: "pixel_custom_apply_patch", arguments: JSON.stringify({ input: patchInput }) } }] },
    { role: "tool", tool_call_id: "call_patch_previous", content: "Done!" },
  ]);
});

test("comparison boundary round-trips the admitted Codex delegation namespace", async () => {
  let port; let observed;
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async (_url, request) => {
      observed = JSON.parse(request.body);
      return new Response(chatSse({
        text: "", outputTokens: 5,
        toolCalls: [{ id: "call_spawn_next", name: "pixel_ns_multi_agent_v1__spawn_agent", arguments: '{"message":"Inspect bounded fixture"}' }],
      }), { status: 200, headers: { "content-type": "text/event-stream" } });
    },
    reportWriter: async () => {},
  });
  const namespace = {
    type: "namespace", name: "multi_agent_v1", description: "One bounded delegated worker.",
    tools: [{
      type: "function", name: "spawn_agent", description: "Spawn one worker.", strict: false,
      parameters: { type: "object", properties: { message: { type: "string" } }, required: ["message"], additionalProperties: false },
    }],
  };
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(responsesBody({
        input: [
          { type: "message", role: "user", content: [{ type: "input_text", text: "Delegate one inspection." }] },
          { type: "function_call", id: "fc_spawn_previous", call_id: "call_spawn_previous", namespace: "multi_agent_v1", name: "spawn_agent", arguments: '{"message":"Inspect"}' },
          { type: "function_call_output", id: "fco_spawn_previous", call_id: "call_spawn_previous", output: "worker started" },
        ],
        tools: [namespace],
      })),
    });
    assert.equal(response.status, 200);
    const payload = await response.text();
    assert.match(payload, /"namespace":"multi_agent_v1"/);
    assert.match(payload, /"name":"spawn_agent"/);
    assert.doesNotMatch(payload, /pixel_ns_multi_agent_v1__spawn_agent/);
  } finally { await boundary.close(); }
  assert.equal(observed.tools[0].function.name, "pixel_ns_multi_agent_v1__spawn_agent");
  assert.equal(observed.messages.at(-2).tool_calls[0].function.name, "pixel_ns_multi_agent_v1__spawn_agent");
});

test("comparison boundary rejects a backend tool call outside the exact admitted surface", async () => {
  let port;
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async () => new Response(chatSse({
      text: "", toolCalls: [{ id: "call_unknown", name: "pixel_ns_multi_agent_v1__spawn_agent", arguments: '{"message":"escape"}' }],
    }), { status: 200, headers: { "content-type": "text/event-stream" } }),
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(responsesBody()),
    });
    assert.equal(response.status, 502);
    assert.doesNotMatch(await response.text(), /spawn_agent|escape/);
  } finally { await boundary.close(); }
  assert.equal(boundary.report().lastFailureCode, "invalid-backend-response");
});

test("comparison boundary fails closed on malformed backend function arguments", async () => {
  let port;
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async () => new Response(chatSse({
      text: "", toolCalls: [{ id: "call_bad", name: "update_plan", arguments: "{" }],
    }), { status: 200, headers: { "content-type": "text/event-stream" } }),
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(responsesBody()),
    });
    assert.equal(response.status, 502);
    assert.doesNotMatch(await response.text(), /call_bad/);
  } finally { await boundary.close(); }
  assert.equal(boundary.report().backendFailures, 1);
  assert.equal(boundary.report().lastFailureCode, "invalid-backend-response");
});

test("comparison boundary withholds a backend response whose measured usage exceeds the budget", async () => {
  let port; let backendRequests = 0;
  const boundary = createTestBoundary(config(0, { maxInputTokens: 7, maxOutputTokens: 2 }), {
    fetchImpl: async () => {
      backendRequests += 1;
      return new Response(chatSse({ text: "must-not-leak", inputTokens: 8, outputTokens: 3 }), { status: 200, headers: { "content-type": "text/event-stream" } });
    },
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(responsesBody({ max_output_tokens: 2 })),
    });
    assert.equal(response.status, 502);
    assert.doesNotMatch(await response.text(), /must-not-leak/);
  } finally { await boundary.close(); }
  assert.equal(backendRequests, 1, "input budget must use measured tokens, not JSON byte length");
  assert.equal(boundary.report().inputTokens, 8);
  assert.equal(boundary.report().outputTokens, 3);
  assert.equal(boundary.report().lastFailureCode, "backend-usage-exceeded");
});

test("comparison boundary rejects model, route, retention, and policy widening before backend use", async () => {
  let port; let backendRequests = 0;
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async () => { backendRequests += 1; return new Response("", { status: 500 }); },
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    for (const [path, body] of [
      ["/v1/chat/completions", responsesBody()],
      ["/v1/responses", responsesBody({ model: "other" })],
      ["/v1/responses", responsesBody({ store: true })],
      ["/v1/responses", responsesBody({ previous_response_id: "resp_forbidden" })],
    ]) {
      const response = await fetch(`http://127.0.0.1:${port}${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
      assert.ok(response.status >= 400); await response.arrayBuffer();
    }
  } finally { await boundary.close(); }
  assert.equal(backendRequests, 0);
  assert.throws(() => createInferenceBoundary(config(port, { inference: { ...inference(), seed: -1 } }), { reportWriter: async () => {} }));
});

test("comparison boundary rejects unsafe JSON structure and non-streaming backend responses", async () => {
  let port; let backendRequests = 0;
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async () => {
      backendRequests += 1;
      return new Response('{"choices":[]}', { status: 200, headers: { "content-type": "application/json" } });
    },
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    let nested = "leaf";
    for (let index = 0; index < 70; index += 1) nested = { nested };
    for (const body of [
      { model: "DeepSeek-V4-Flash-0731", stream: true, messages: [nested] },
      JSON.parse('{"model":"DeepSeek-V4-Flash-0731","stream":true,"messages":[],"__proto__":{}}'),
    ]) {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
      });
      assert.equal(response.status, 400); await response.arrayBuffer();
    }
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(responsesBody()),
    });
    assert.equal(response.status, 502); await response.arrayBuffer();
  } finally { await boundary.close(); }
  assert.equal(backendRequests, 1);
  assert.equal(boundary.report().backendFailures, 1);
});

test("Responses translation preserves structured output and rejects hosted or malformed tools", () => {
  const translated = responsesRequestToChat(responsesBody({
    max_output_tokens: 321,
    text: { format: { type: "json_schema", name: "result", description: "Machine result", strict: true, schema: { type: "object", properties: { ok: { type: "boolean" } }, required: ["ok"], additionalProperties: false } } },
    tool_choice: { type: "function", name: "update_plan" },
    tools: [{ type: "function", name: "update_plan", parameters: { type: "object", properties: {}, additionalProperties: false } }],
  }), 17);
  assert.equal(translated.body.max_tokens, 17);
  assert.deepEqual(translated.body.tool_choice, { type: "function", function: { name: "update_plan" } });
  assert.deepEqual(translated.body.response_format, {
    type: "json_schema",
    json_schema: { name: "result", description: "Machine result", strict: true, schema: { type: "object", properties: { ok: { type: "boolean" } }, required: ["ok"], additionalProperties: false } },
  });
  for (const tool of [
    { type: "web_search_preview" },
    { type: "function", name: "bad name", parameters: {} },
    { type: "function", name: "update_plan", parameters: {}, unexpected: "blocked" },
  ]) assert.throws(() => responsesRequestToChat(responsesBody({ tools: [tool] }), 16));
  assert.throws(() => responsesRequestToChat(responsesBody({
    input: [{ type: "message", role: "user", content: [{ type: "input_text", text: "x", annotations: [{ secret: true }] }] }],
  }), 16));
});

test("Responses translation treats a JSON null reasoning exactly like an omitted optional reasoning request", () => {
  // The real DSV4 client serializes reasoning:null; it must be accepted as
  // unspecified and dropped from the forwarded chat request.
  const translated = responsesRequestToChat(responsesBody({ reasoning: null }), 16);
  assert.equal(translated.body.reasoning, undefined, "null reasoning must be treated as unspecified");
  assert.equal(JSON.stringify(translated.body).includes("reasoning"), false, "null reasoning must not be forwarded");
  // Omitting reasoning entirely must produce the identical forwarded request.
  const omitted = responsesRequestToChat(responsesBody({ reasoning: undefined }), 16);
  assert.deepEqual(translated.body, omitted.body, "null and omitted reasoning must translate identically");
});

test("Responses translation retains strict validation for every non-null reasoning value", () => {
  // A valid non-null reasoning object is still accepted.
  const ok = responsesRequestToChat(responsesBody({ reasoning: { effort: "low", summary: "concise" } }), 16);
  assert.equal(ok.body.reasoning, undefined, "reasoning is not forwarded to the chat backend");
  // Malformed non-null reasoning values remain rejected.
  for (const bad of [
    { effort: "turbo" },
    { summary: "verbose" },
    { effort: "low", unexpected: "blocked" },
    "auto",
    42,
    [],
    true,
  ]) assert.throws(() => responsesRequestToChat(responsesBody({ reasoning: bad }), 16), /reasoning/u, `reasoning ${JSON.stringify(bad)} must be rejected`);
});

test("comparison boundary runs strict structured output in DSV4 non-thinking mode", async () => {
  let port; let observed;
  const boundary = createTestBoundary(config(0), {
    fetchImpl: async (_url, request) => {
      observed = JSON.parse(request.body);
      return new Response(chatSse({ text: '{"ok":true}', outputTokens: 5 }), { status: 200, headers: { "content-type": "text/event-stream" } });
    },
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(responsesBody({
        text: { format: { type: "json_schema", name: "result", strict: true, schema: { type: "object", properties: { ok: { type: "boolean" } }, required: ["ok"], additionalProperties: false } } },
      })),
    });
    assert.equal(response.status, 200);
    assert.ok((await response.text()).includes('{\\"ok\\":true}'));
  } finally { await boundary.close(); }
  assert.equal(observed.reasoning_effort, "none");
  assert.equal(observed.include_reasoning, false);
  assert.equal(observed.response_format.type, "json_schema");
});

test("Chat translation joins fragmented tool calls and validates detailed usage", () => {
  const chunks = [
    { id: "chatcmpl_frag", created: 1786550400, model: "DeepSeek-V4-Flash-0731", choices: [{ index: 0, delta: { role: "assistant", tool_calls: [{ index: 0, id: "call_frag", type: "function", function: { name: "update_", arguments: "{\"plan\":" } }] }, finish_reason: null }] },
    { id: "chatcmpl_frag", created: 1786550400, model: "DeepSeek-V4-Flash-0731", choices: [{ index: 0, delta: { tool_calls: [{ index: 0, function: { name: "plan", arguments: "[]}" } }] }, finish_reason: null }] },
    { id: "chatcmpl_frag", created: 1786550400, model: "DeepSeek-V4-Flash-0731", choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }] },
    { id: "chatcmpl_frag", created: 1786550400, model: "DeepSeek-V4-Flash-0731", choices: [], usage: { prompt_tokens: 11, completion_tokens: 4, prompt_tokens_details: { cached_tokens: 3 }, completion_tokens_details: { reasoning_tokens: 1 } } },
  ];
  const payload = `${chunks.map((value) => `data: ${JSON.stringify(value)}`).join("\n\n")}\n\ndata: [DONE]\n\n`;
  const result = chatSseToResponses(Buffer.from(payload), { model: "DeepSeek-V4-Flash-0731" });
  const text = result.bytes.toString("utf8");
  assert.match(text, /"name":"update_plan"/);
  assert.match(text, /"arguments":"\{\\"plan\\":\[\]\}"/);
  assert.match(text, /"cached_tokens":3/);
  assert.match(text, /"reasoning_tokens":1/);
  assert.deepEqual(result.usage, { inputTokens: 11, outputTokens: 4 });

  const invalidUsage = payload.replace('"cached_tokens":3', '"cached_tokens":12');
  assert.throws(() => chatSseToResponses(Buffer.from(invalidUsage), { model: "DeepSeek-V4-Flash-0731" }));
  const widened = payload.replace('"function":{"name":"update_"', '"leak":"forbidden","function":{"name":"update_"');
  assert.throws(() => chatSseToResponses(Buffer.from(widened), { model: "DeepSeek-V4-Flash-0731" }));
});

test("comparison boundary withholds translated SSE amplification beyond the byte ceiling", async () => {
  let port;
  const boundary = createTestBoundary(config(0, { maxResponseBytes: 1024 }), {
    fetchImpl: async () => new Response(chatSse({ text: "x".repeat(80), inputTokens: 8, outputTokens: 2 }), { status: 200, headers: { "content-type": "text/event-stream" } }),
    reportWriter: async () => {},
  });
  port = await listenTestBoundary(boundary);
  try {
    const response = await fetch(`http://127.0.0.1:${port}/v1/responses`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(responsesBody()),
    });
    assert.equal(response.status, 502);
    assert.doesNotMatch(await response.text(), /x{20}/);
  } finally { await boundary.close(); }
  assert.equal(boundary.report().lastFailureCode, "response-too-large");
});
