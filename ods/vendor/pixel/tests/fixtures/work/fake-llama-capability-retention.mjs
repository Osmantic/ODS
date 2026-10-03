import { createServer } from "node:http";

const MAX_BODY_BYTES = 1024 * 1024;
const MODEL_ID = process.env.PIXEL_FAKE_MODEL_ID ?? "assistant-model";
const MAIN_MARKER = "PIXEL_CAPABILITY_RETENTION_OBJECTIVE";
const SUBAGENT_MARKER = "PIXEL_SUBAGENT_ASSIGNMENT";
const HUB_TOKEN = "PIXEL_HUB_TOKEN";
const ALLOWED_TOOLS = ["bash", "debug", "edit", "eval", "glob", "grep", "hub", "lsp", "read", "task", "todo", "write"];
const SUBAGENT_TOOLS = ["bash", "debug", "edit", "eval", "glob", "grep", "hub", "lsp", "read", "write", "yield"];
let mainStep = 0;
let subagentStep = 0;
let taskStatus = "missing";
let hubStatus = "missing";

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

function finalEvents(id, text) {
  const item = { type: "message", id: `msg_${id}`, role: "assistant", status: "in_progress", content: [] };
  return [
    { type: "response.created", response: { id: `resp_${id}`, status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.content_part.added", output_index: 0, item_id: item.id, content_index: 0, part: { type: "output_text", text: "" } },
    { type: "response.output_text.delta", output_index: 0, item_id: item.id, content_index: 0, delta: text },
    { type: "response.output_item.done", output_index: 0, item: { ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] } },
    {
      type: "response.completed",
      response: {
        id: `resp_${id}`, status: "completed",
        output: [{ ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] }],
        usage: { input_tokens: 48, output_tokens: 16, total_tokens: 64, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

function red(response, reason) {
  return sendEvents(response, finalEvents("retention_red", `PIXEL_CAPABILITY_RETENTION_RED: ${reason}`));
}

function mainResponse(response, body) {
  const output = latestToolOutput(body);
  mainStep += 1;
  if (mainStep === 1) return sendEvents(response, toolCallEvents("retention_read", "read", { path: "fixture.txt" }));
  if (mainStep === 2) return output.includes("PIXEL_READ_TOKEN")
    ? sendEvents(response, toolCallEvents("retention_grep", "grep", { pattern: "PIXEL_SEARCH_TOKEN", path: "nested", case: true }))
    : red(response, "read did not expose its fixture token");
  if (mainStep === 3) return output.includes("PIXEL_SEARCH_TOKEN")
    ? sendEvents(response, toolCallEvents("retention_glob", "glob", { path: "nested/*.flag", limit: 20 }))
    : red(response, "grep did not find its fixture token");
  if (mainStep === 4) return output.includes("discovery.flag")
    ? sendEvents(response, toolCallEvents("retention_edit_read", "read", { path: "edit.txt" }))
    : red(response, `glob did not discover its fixture file (${output.slice(0, 240)})`);
  if (mainStep === 5) {
    const snapshot = /\[(?:\.\/)?edit\.txt#([A-Fa-f0-9]{4,64})\]/u.exec(output);
    return snapshot
      ? sendEvents(response, toolCallEvents("retention_edit", "edit", { input: `[edit.txt#${snapshot[1]}]\nPUT 1:\n+EDIT_AFTER` }))
      : red(response, `read did not return a bound edit snapshot (${output.slice(0, 240)})`);
  }
  if (mainStep === 6) return sendEvents(response, toolCallEvents("retention_write", "write", { path: "proofs/write.txt", content: "WRITE_OK\n" }));
  if (mainStep === 7) return sendEvents(response, toolCallEvents("retention_bash", "bash", {
    command: "test \"$(cat edit.txt)\" = EDIT_AFTER && test \"$(cat proofs/write.txt)\" = WRITE_OK && printf 'BASH_OK\\n' | tee proofs/bash.txt",
  }));
  if (mainStep === 8) return output.includes("BASH_OK")
    ? sendEvents(response, toolCallEvents("retention_lsp", "lsp", { action: "hover", file: "app.py", line: 1, symbol: "add", timeout: 20 }))
    : red(response, "bash did not validate the edit and write");
  if (mainStep === 9) return !output.includes("No hover information") && output.includes("add")
    ? sendEvents(response, toolCallEvents("retention_semantic_proof", "write", {
      path: "proofs/semantic.txt", content: "READ_OK\nSEARCH_OK\nGLOB_OK\nLSP_OK\n",
    }))
    : red(response, `LSP did not expose semantic hover data (${output.slice(0, 240)})`);
  if (mainStep === 10) return sendEvents(response, toolCallEvents("retention_debug_launch", "debug", {
    action: "launch", adapter: "debugpy", program: "debug_target.py", timeout: 20,
  }));
  if (mainStep === 11) return output.includes("Adapter: debugpy") && output.includes("Status: stopped")
    ? sendEvents(response, toolCallEvents("retention_debug_stack", "debug", { action: "stack_trace", levels: 8, timeout: 20 }))
    : red(response, "debugpy did not launch and stop the fixture");
  if (mainStep === 12) return output.includes("debug_target.py")
    ? sendEvents(response, toolCallEvents("retention_debug_continue", "debug", { action: "continue", timeout: 20 }))
    : red(response, "debugger did not expose the fixture stack");
  if (mainStep === 13) return sendEvents(response, toolCallEvents("retention_eval", "eval", {
    language: "py", timeout: 20,
    code: "from pathlib import Path\nPath('proofs').mkdir(exist_ok=True)\nPath('proofs/eval.txt').write_text('EVAL_OK\\n', encoding='utf-8')\nprint('PIXEL_EVAL_TOKEN')",
  }));
  if (mainStep === 14) return output.includes("PIXEL_EVAL_TOKEN")
    ? sendEvents(response, toolCallEvents("retention_task", "task", {
      context: "Public synthetic capability-retention fixture. No credentials, network expansion, or external effects.",
      tasks: [{ name: "RetentionAgent", agent: "task", task: `${SUBAGENT_MARKER}: Send ${HUB_TOKEN} to Main through hub, then report PIXEL_SUBAGENT_TOKEN.` }],
    }))
    : red(response, "local evaluation did not execute its Python cell");
  if (mainStep === 15) {
    taskStatus = output.slice(0, 400);
    return sendEvents(response, toolCallEvents("retention_hub_wait", "hub", { op: "wait", timeoutMs: 20000 }));
  }
  if (mainStep === 16 && !output.includes(HUB_TOKEN)) {
    hubStatus = output.slice(0, 400);
    return sendEvents(response, toolCallEvents("retention_hub_inbox", "hub", { op: "inbox" }));
  }
  if ((mainStep === 16 || mainStep === 17) && output.includes(HUB_TOKEN)) return sendEvents(response, toolCallEvents("retention_coordination_proof", "write", {
    path: "proofs/coordination.txt", content: "SUBAGENT_OK\nHUB_OK\n",
  }));
  if ((mainStep === 17 || mainStep === 18) && output.includes("coordination.txt")) return sendEvents(response, finalEvents(
    "retention_green",
    "PIXEL_CAPABILITY_RETENTION_GREEN: all eleven synthetic coding capabilities completed under one objective without a mid-job operator prompt.",
  ));
  return red(response, `coordination proof was not established at step ${mainStep}; task=${taskStatus}; wait=${hubStatus}; current=${output.slice(0, 240)}`);
}

function subagentResponse(response, body) {
  const names = toolNames(body);
  if (JSON.stringify(names) !== JSON.stringify(SUBAGENT_TOOLS)) return sendJson(response, 409, { error: `subagent tool contract mismatch (${names.join(",")})` });
  subagentStep += 1;
  if (subagentStep === 1) return sendEvents(response, toolCallEvents("retention_subagent_send", "hub", {
    op: "send", to: "Main", message: HUB_TOKEN,
  }));
  return sendEvents(response, finalEvents("retention_subagent_final", "PIXEL_SUBAGENT_TOKEN"));
}

const server = createServer({ maxHeaderSize: 8192, requestTimeout: 30000, headersTimeout: 3000 }, async (request, response) => {
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
  if (body.model !== MODEL_ID || body.stream !== true) return sendJson(response, 422, { error: "model contract mismatch" });
  if (serialized.includes(MAIN_MARKER)) {
    const names = toolNames(body);
    const fullCatalog = JSON.stringify(names) === JSON.stringify(ALLOWED_TOOLS);
    const exactInitialGrounding = mainStep === 0 && JSON.stringify(names) === JSON.stringify(["read"]);
    if (!fullCatalog && !exactInitialGrounding) return sendJson(response, 409, { error: "main tool contract mismatch" });
    return mainResponse(response, body);
  }
  if (serialized.includes(SUBAGENT_MARKER)) return subagentResponse(response, body);
  return sendJson(response, 400, { error: "unknown synthetic benchmark session" });
});

server.maxRequestsPerSocket = 64;
server.on("upgrade", (request, socket) => socket.destroy());
server.listen(8080, "0.0.0.0");

const stop = () => server.close(() => { process.exitCode = 0; });
process.once("SIGINT", stop);
process.once("SIGTERM", stop);
