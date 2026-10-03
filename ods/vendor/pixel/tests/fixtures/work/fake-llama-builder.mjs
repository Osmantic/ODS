import { createServer } from "node:http";

const MAX_BODY_BYTES = 1024 * 1024;
const MODEL_ID = process.env.PIXEL_FAKE_MODEL_ID ?? "assistant-model";
const CANARY = "PIXEL_HOSTILE_CONTROL_CANARY";
const ALLOWED_TOOLS = ["bash", "debug", "edit", "eval", "glob", "grep", "hub", "lsp", "read", "task", "todo", "write"];
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

function latestToolOutput(body) {
  if (!Array.isArray(body.input)) return "";
  const outputs = body.input.filter((item) => item?.type === "function_call_output" && typeof item.output === "string");
  return outputs.at(-1)?.output ?? "";
}

function toolCallEvents(id, name, args) {
  const item = { type: "function_call", id: `fc_${id}`, call_id: `call_${id}`, name, arguments: "" };
  const encoded = JSON.stringify(args);
  return [
    { type: "response.created", response: { id: `resp_${id}`, status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.function_call_arguments.delta", output_index: 0, item_id: item.id, delta: encoded },
    { type: "response.output_item.done", output_index: 0, item: { ...item, arguments: encoded, status: "completed" } },
    {
      type: "response.completed",
      response: {
        id: `resp_${id}`, status: "completed", output: [{ ...item, arguments: encoded, status: "completed" }],
        usage: { input_tokens: 24, output_tokens: 12, total_tokens: 36, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

function finalEvents(text) {
  const item = { type: "message", id: "msg_builder_final", role: "assistant", status: "in_progress", content: [] };
  return [
    { type: "response.created", response: { id: "resp_builder_final", status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.content_part.added", output_index: 0, item_id: item.id, content_index: 0, part: { type: "output_text", text: "" } },
    { type: "response.output_text.delta", output_index: 0, item_id: item.id, content_index: 0, delta: text },
    { type: "response.output_item.done", output_index: 0, item: { ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] } },
    {
      type: "response.completed",
      response: {
        id: "resp_builder_final", status: "completed",
        output: [{ ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] }],
        usage: { input_tokens: 48, output_tokens: 16, total_tokens: 64, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

const server = createServer({ maxHeaderSize: 8192, requestTimeout: 5000, headersTimeout: 3000 }, async (request, response) => {
  if (request.method === "GET" && request.url === "/models" && !request.headers.authorization && !request.headers.cookie) return sendJson(response, 200, {
    object: "list", data: [{ id: MODEL_ID, object: "model", owned_by: "pixel-test", meta: { n_ctx: 32768, n_ctx_train: 32768 }, architecture: { input_modalities: ["text"] } }],
  });
  if (request.method === "GET" && request.url === "/props" && !request.headers.authorization && !request.headers.cookie) return sendJson(response, 200, {
    n_ctx: 32768, modalities: { vision: false }, default_generation_settings: { n_ctx: 32768, params: { max_tokens: -1, n_predict: -1 } },
  });
  if (request.method !== "POST" || request.url !== "/responses" || request.headers.authorization || request.headers.cookie) return sendJson(response, 404, { error: "route denied" });
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
  const toolOutput = latestToolOutput(body);
  if (serialized.includes(CANARY)) return sendJson(response, 400, { error: "control-file canary reached the model" });
  const requestedTools = toolNames(body);
  if (
    body.model !== MODEL_ID || body.stream !== true || !requestedTools.includes("read")
    || new Set(requestedTools).size !== requestedTools.length || requestedTools.some((name) => !ALLOWED_TOOLS.includes(name))
  ) return sendJson(response, 400, { error: "model or tool contract mismatch" });
  calls += 1;
  if (calls === 1) return sendEvents(response, toolCallEvents("builder_initial_read", "read", { path: "app.py" }));
  if (calls === 2) return sendEvents(response, toolCallEvents("builder_lsp_hover", "lsp", {
    action: "hover", file: "app.py", line: 1, symbol: "add", timeout: 20,
  }));
  if (calls === 3) return sendEvents(response, !toolOutput.includes("No hover information") && toolOutput.includes("add")
    ? toolCallEvents("builder_debug_launch", "debug", { action: "launch", adapter: "debugpy", program: "debug_target.py", timeout: 20 })
    : finalEvents(`PIXEL_BUILDER_E2E_RED: LSP did not return semantic hover data (${toolOutput.slice(0, 400)}).`));
  if (calls === 4) return sendEvents(response, toolOutput.includes("Adapter: debugpy") && toolOutput.includes("Status: stopped")
    ? toolCallEvents("builder_debug_stack", "debug", { action: "stack_trace", levels: 8, timeout: 20 })
    : finalEvents("PIXEL_BUILDER_E2E_RED: debugpy did not launch and stop the fixture."));
  if (calls === 5) return sendEvents(response, toolOutput.includes("debug_target.py")
    ? toolCallEvents("builder_debug_continue", "debug", { action: "continue", timeout: 20 })
    : finalEvents("PIXEL_BUILDER_E2E_RED: the debugger did not expose the fixture stack."));
  if (calls === 6) return sendEvents(response, toolCallEvents("builder_write", "write", {
    path: "src/main.js",
    content: "export const invariant = 'PIXEL_FIXTURE_INVARIANT=43';\n",
  }));
  if (calls === 7) return sendEvents(response, toolCallEvents("builder_bash", "bash", {
    command: "grep -q 'PIXEL_FIXTURE_INVARIANT=43' src/main.js && test \"$(cat debug-proof.txt)\" = 43 && printf 'PIXEL_BUILDER_TEST_%s\\n' 'GREEN'",
  }));
  const green = toolOutput.includes("PIXEL_BUILDER_TEST_GREEN");
  return sendEvents(response, finalEvents(green
    ? "PIXEL_BUILDER_E2E_GREEN: obtained semantic LSP hover data, inspected and continued the fixture through debugpy, changed invariant 42 to 43, and observed PIXEL_BUILDER_TEST_GREEN."
    : "PIXEL_BUILDER_E2E_RED: required edit and test evidence was absent."));
});

server.maxRequestsPerSocket = 8;
server.on("upgrade", (request, socket) => socket.destroy());
server.listen(8080, "0.0.0.0");

const stop = () => server.close(() => { process.exitCode = 0; });
process.once("SIGINT", stop);
process.once("SIGTERM", stop);
