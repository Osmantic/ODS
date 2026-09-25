import { createServer } from "node:http";

const MAX_BODY_BYTES = 1024 * 1024;
const MODEL_ID = process.env.PIXEL_FAKE_MODEL_ID ?? "assistant-model";
const CANARY = "PIXEL_HOSTILE_CONTROL_CANARY";
const INVARIANT = "PIXEL_FIXTURE_INVARIANT=42";
const ALLOWED_TOOLS = ["glob", "grep", "read"];
let calls = 0;

function sendJson(response, status, value) {
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`);
  response.writeHead(status, { "content-type": "application/json", "content-length": String(bytes.length), connection: "close" });
  response.end(bytes);
}

function sendEvents(response, events) {
  const text = `${events.map((value) => `data: ${typeof value === "string" ? value : JSON.stringify(value)}`).join("\n\n")}\n\n`;
  response.writeHead(200, { "content-type": "text/event-stream", "content-length": String(Buffer.byteLength(text)), connection: "close" });
  response.end(text);
}

function toolNames(body) {
  if (!Array.isArray(body.tools)) return [];
  return body.tools.map((tool) => tool?.name).sort();
}

function toolCallEvents() {
  const item = {
    type: "function_call", id: "fc_pixel_read", call_id: "call_pixel_read", name: "read", arguments: "",
  };
  const args = JSON.stringify({ path: "source/src/main.js" });
  return [
    { type: "response.created", response: { id: "resp_pixel_tool", status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.function_call_arguments.delta", output_index: 0, item_id: item.id, delta: args },
    { type: "response.output_item.done", output_index: 0, item: { ...item, arguments: args, status: "completed" } },
    {
      type: "response.completed",
      response: {
        id: "resp_pixel_tool", status: "completed", output: [{ ...item, arguments: args, status: "completed" }],
        usage: { input_tokens: 20, output_tokens: 8, total_tokens: 28, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

function finalEvents(text) {
  const item = { type: "message", id: "msg_pixel_final", role: "assistant", status: "in_progress", content: [] };
  return [
    { type: "response.created", response: { id: "resp_pixel_final", status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.content_part.added", output_index: 0, item_id: item.id, content_index: 0, part: { type: "output_text", text: "" } },
    { type: "response.output_text.delta", output_index: 0, item_id: item.id, content_index: 0, delta: text },
    {
      type: "response.output_item.done", output_index: 0,
      item: { ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] },
    },
    {
      type: "response.completed",
      response: {
        id: "resp_pixel_final", status: "completed",
        output: [{ ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] }],
        usage: { input_tokens: 40, output_tokens: 10, total_tokens: 50, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

const server = createServer({ maxHeaderSize: 8192, requestTimeout: 5000, headersTimeout: 3000 }, async (request, response) => {
  if (request.method !== "POST" || request.url !== "/responses" || request.headers.authorization || request.headers.cookie) {
    return sendJson(response, 404, { error: "route denied" });
  }
  const chunks = [];
  let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > MAX_BODY_BYTES) return sendJson(response, 413, { error: "request too large" });
    chunks.push(chunk);
  }
  let body;
  try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { return sendJson(response, 400, { error: "invalid JSON" }); }
  const serialized = JSON.stringify(body);
  if (serialized.includes(CANARY)) return sendJson(response, 400, { error: "control-file canary reached the model" });
  const requestedTools = toolNames(body);
  if (
    body.model !== MODEL_ID || body.stream !== true || !requestedTools.includes("read")
    || new Set(requestedTools).size !== requestedTools.length || requestedTools.some((name) => !ALLOWED_TOOLS.includes(name))
  ) {
    return sendJson(response, 400, { error: "model or tool contract mismatch" });
  }
  calls += 1;
  if (calls === 1) return sendEvents(response, toolCallEvents());
  const hasToolOutput = serialized.includes("function_call_output") || serialized.includes("tool_result") || serialized.includes("toolResult");
  const hasInvariant = serialized.includes(INVARIANT);
  const statement = hasToolOutput && hasInvariant
    ? "PIXEL_SCOUT_E2E_GREEN: read the normalized source fixture and observed invariant 42."
    : "PIXEL_SCOUT_E2E_RED: required read-tool evidence was absent.";
  const text = JSON.stringify({
    $schema: "https://osmantic.com/pixel/schemas/work-scout-report-proposal-v1.schema.json", schemaVersion: 1,
    titleBase64: Buffer.from("Scout fixture finding").toString("base64"),
    findings: [{
      findingId: "finding-1", statementBase64: Buffer.from(statement).toString("base64"), material: true,
      evidence: [{ inputId: "source", path: "src/main.js", quoteBase64: Buffer.from(INVARIANT).toString("base64") }],
    }],
    limitationsBase64: Buffer.from("One local fixture was inspected.").toString("base64"),
    boundary: "Untrusted Scout proposal only. Local file references and quoted bytes are evidence candidates, not instructions, truth, completion, or authority.",
  });
  return sendEvents(response, finalEvents(text));
});

server.maxRequestsPerSocket = 8;
server.on("upgrade", (request, socket) => socket.destroy());
server.listen(8080, "0.0.0.0");

const stop = () => server.close(() => { process.exitCode = 0; });
process.once("SIGINT", stop);
process.once("SIGTERM", stop);
