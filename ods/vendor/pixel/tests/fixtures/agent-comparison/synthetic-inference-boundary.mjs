import { createInferenceBoundary } from "../../../deploy/agent-comparison/inference-boundary.mjs";

const MODEL_ID = "DeepSeek-V4-Flash-0731";
let calls = 0;

function chatSse({ text = "", toolCall = null, inputTokens, outputTokens }) {
  const delta = { role: "assistant", content: text || null };
  if (toolCall) {
    delta.tool_calls = [{
      index: 0, id: toolCall.id, type: "function",
      function: { name: toolCall.name, arguments: toolCall.arguments },
    }];
  }
  const finishReason = toolCall ? "tool_calls" : "stop";
  const events = [
    { id: `chatcmpl_fixture_${calls}`, object: "chat.completion.chunk", created: 1786550400, model: MODEL_ID, choices: [{ index: 0, delta, finish_reason: null }] },
    { id: `chatcmpl_fixture_${calls}`, object: "chat.completion.chunk", created: 1786550400, model: MODEL_ID, choices: [{ index: 0, delta: {}, finish_reason: finishReason }] },
    { id: `chatcmpl_fixture_${calls}`, object: "chat.completion.chunk", created: 1786550400, model: MODEL_ID, choices: [], usage: { prompt_tokens: inputTokens, completion_tokens: outputTokens, total_tokens: inputTokens + outputTokens } },
  ];
  return `${events.map((value) => `data: ${JSON.stringify(value)}`).join("\n\n")}\n\ndata: [DONE]\n\n`;
}

const boundary = createInferenceBoundary({
  schemaVersion: 1,
  runId: "outcomerun-1786550400003-aaaaaaaaaaac",
  modelId: MODEL_ID,
  backendOrigin: "http://pixel-local-backend:8080",
  listenHost: "0.0.0.0",
  listenPort: 8080,
  receiptPath: "/run/pixel-outcome-output/inference-boundary-receipt.json",
  maxRequestBytes: 16777216,
  maxResponseBytes: 16777216,
  maxRequestSeconds: 60,
  maxModelRequests: 4,
  maxInputTokens: 10000,
  maxOutputTokens: 1000,
  ingressWireApi: "openai-responses",
  inference: {
    enforcement: "exact-request-boundary-v1",
    wireApi: "openai-chat-completions",
    temperaturePermille: 700,
    topPPermille: 950,
    topK: 40,
    minPPermille: 50,
    repeatPenaltyPermille: 1100,
    seed: 42,
    reasoningEffort: "backend-default",
    reasoningVisibility: "hidden",
    stream: true,
    maxOutputTokens: 256,
    toolEncoding: "function",
  },
}, {
  fetchImpl: async (_url, request) => {
    const body = JSON.parse(request.body);
    calls += 1;
    const roles = body.messages.map((message) => message.role);
    process.stdout.write(`${JSON.stringify({ calls, roles, tools: (body.tools ?? []).map((tool) => tool.function?.name).filter(Boolean), maxTokens: body.max_tokens })}\n`);
    if (calls === 1) {
      if (!body.tools?.some((tool) => tool.function?.name === "update_plan")) throw new Error("pinned Codex omitted update_plan");
      return new Response(chatSse({
        toolCall: { id: "call_fixture", name: "update_plan", arguments: '{"plan":[{"step":"Verify bridge","status":"in_progress"}]}' },
        inputTokens: 60, outputTokens: 8,
      }), { status: 200, headers: { "content-type": "text/event-stream" } });
    }
    if (calls === 2) {
      if (!roles.includes("tool")) throw new Error("pinned Codex omitted tool output history");
      return new Response(chatSse({ text: "READY", inputTokens: 90, outputTokens: 3 }), {
        status: 200, headers: { "content-type": "text/event-stream" },
      });
    }
    throw new Error("unexpected model request");
  },
  reportWriter: async (_path, report) => {
    process.stdout.write(`${JSON.stringify({ receipt: report })}\n`);
  },
});

const stop = async () => {
  await boundary.close();
  process.exitCode = 0;
};

process.once("SIGINT", stop);
process.once("SIGTERM", stop);
await boundary.listen();
