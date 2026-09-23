import { createServer } from "node:http";
import { writeFileSync } from "node:fs";

const MODEL_ID = "DeepSeek-V4-Flash-0731";
const CAPTURE_PATH = "/output/codex-surface.json";
const EXPECTED_FUNCTIONS = ["exec_command", "request_user_input", "update_plan", "view_image", "write_stdin"];
const EXPECTED_DELEGATION = ["close_agent", "resume_agent", "send_input", "spawn_agent", "wait_agent"];

function sendJson(response, status, value) {
  const payload = Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
  response.writeHead(status, { "content-type": "application/json", "content-length": String(payload.length), connection: "close" });
  response.end(payload);
}

function finalEvents(text) {
  const item = { type: "message", id: "msg_codex_surface", role: "assistant", status: "in_progress", content: [] };
  const completed = { ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] };
  return [
    { type: "response.created", response: { id: "resp_codex_surface", status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.content_part.added", output_index: 0, item_id: item.id, content_index: 0, part: { type: "output_text", text: "", annotations: [] } },
    { type: "response.output_text.delta", output_index: 0, item_id: item.id, content_index: 0, delta: text },
    { type: "response.output_text.done", output_index: 0, item_id: item.id, content_index: 0, text },
    { type: "response.output_item.done", output_index: 0, item: completed },
    {
      type: "response.completed",
      response: {
        id: "resp_codex_surface", status: "completed", output: [completed],
        usage: {
          input_tokens: 32, output_tokens: 4, total_tokens: 36,
          input_tokens_details: { cached_tokens: 0 }, output_tokens_details: { reasoning_tokens: 0 },
        },
      },
    },
    "[DONE]",
  ];
}

function sendEvents(response, events) {
  const payload = Buffer.from(`${events.map((value) => `data: ${typeof value === "string" ? value : JSON.stringify(value)}`).join("\n\n")}\n\n`, "utf8");
  response.writeHead(200, { "content-type": "text/event-stream", "content-length": String(payload.length), connection: "close" });
  response.end(payload);
}

const server = createServer({ maxHeaderSize: 8192, requestTimeout: 10000, headersTimeout: 5000 }, async (request, response) => {
  if (request.method === "GET" && request.url === "/healthz") return sendJson(response, 200, { status: "ready" });
  if (request.method !== "POST" || request.url !== "/v1/responses") return sendJson(response, 404, { error: "route denied" });
  const chunks = []; let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > 4 * 1024 * 1024) return sendJson(response, 413, { error: "request too large" });
    chunks.push(chunk);
  }
  let body;
  try { body = JSON.parse(Buffer.concat(chunks, total).toString("utf8")); } catch { return sendJson(response, 400, { error: "invalid JSON" }); }
  const tools = Array.isArray(body.tools) ? body.tools : [];
  const functions = tools.filter((tool) => tool?.type === "function").map((tool) => tool.name).sort();
  const custom = tools.find((tool) => tool?.type === "custom");
  const namespace = tools.find((tool) => tool?.type === "namespace");
  const delegation = Array.isArray(namespace?.tools) ? namespace.tools.map((tool) => tool?.name).sort() : [];
  const valid = body.model === MODEL_ID && body.stream === true && body.store === false
    && JSON.stringify(functions) === JSON.stringify(EXPECTED_FUNCTIONS)
    && custom?.name === "apply_patch" && custom?.format?.type === "grammar" && custom?.format?.syntax === "lark"
    && typeof custom?.format?.definition === "string" && custom.format.definition.length > 0
    && namespace?.name === "multi_agent_v1"
    && JSON.stringify(delegation) === JSON.stringify(EXPECTED_DELEGATION);
  if (!valid) return sendJson(response, 400, { error: "Codex comparison tool surface differs from its exact contract" });
  writeFileSync(CAPTURE_PATH, `${JSON.stringify({
    schemaVersion: 1, operation: "pixel-codex-comparison-surface-qualified", modelId: body.model,
    functionTools: functions, customTool: custom.name, customFormat: `${custom.format.type}:${custom.format.syntax}`,
    namespace: namespace.name, namespaceTools: delegation, toolCount: tools.length,
    contentStored: false, credentialUsed: false, providerCalled: false, externalEffects: false,
  }, null, 2)}\n`, { encoding: "utf8", flag: "wx", mode: 0o600 });
  return sendEvents(response, finalEvents("PIXEL_CODEX_SURFACE_GREEN"));
});

server.maxRequestsPerSocket = 4;
server.on("upgrade", (_request, socket) => socket.destroy());
server.on("connect", (_request, socket) => socket.destroy());
server.listen(8080, "0.0.0.0");
