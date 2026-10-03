import { createServer } from "node:http";

const MAX_BODY_BYTES = 2 * 1024 * 1024;
const MODEL_ID = process.env.PIXEL_FAKE_MODEL_ID ?? "assistant-model";
const MODE = process.env.PIXEL_FAKE_RESEARCHER_MODE ?? "success";
const ALLOWED_TOOLS = ["bash", "edit", "glob", "grep", "pixel_research", "read", "write"];
const EVIDENCE = "Pixel verified public evidence says the safety boundary is green.";
const PROPOSAL_BOUNDARY = "Untrusted public Researcher proposal only. It carries no verification, publication, action, policy, or completion authority and must be finalized and independently checked by Pixel.";
const AUTHORITY = {
  directNetwork: false, credentials: false, externalWrites: false, accounts: false, messages: false,
  publish: false, purchase: false, policyMutation: false, scopeExpansion: false,
};
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
  return Array.isArray(body.tools) ? body.tools.map((tool) => tool?.name).sort() : [];
}

function toolCallEvents() {
  const item = { type: "function_call", id: "fc_pixel_research", call_id: "call_pixel_research", name: "pixel_research", arguments: "" };
  const args = JSON.stringify({
    query: "public agent safety boundary evidence", sourceTypes: ["web"], domains: ["example.com"],
    maxResults: 1, maxSourcesToFetch: 1, maxSourceBytes: 65536,
  });
  return [
    { type: "response.created", response: { id: "resp_research_tool", status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.function_call_arguments.delta", output_index: 0, item_id: item.id, delta: args },
    { type: "response.output_item.done", output_index: 0, item: { ...item, arguments: args, status: "completed" } },
    {
      type: "response.completed",
      response: {
        id: "resp_research_tool", status: "completed", output: [{ ...item, arguments: args, status: "completed" }],
        usage: { input_tokens: 32, output_tokens: 16, total_tokens: 48, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

function finalEvents(text) {
  const item = { type: "message", id: "msg_research_final", role: "assistant", status: "in_progress", content: [] };
  return [
    { type: "response.created", response: { id: "resp_research_final", status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.content_part.added", output_index: 0, item_id: item.id, content_index: 0, part: { type: "output_text", text: "" } },
    { type: "response.output_text.delta", output_index: 0, item_id: item.id, content_index: 0, delta: text },
    { type: "response.output_item.done", output_index: 0, item: { ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] } },
    {
      type: "response.completed",
      response: {
        id: "resp_research_final", status: "completed",
        output: [{ ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] }],
        usage: { input_tokens: 64, output_tokens: 48, total_tokens: 112, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

function strings(value, output = []) {
  if (typeof value === "string") output.push(value);
  else if (Array.isArray(value)) for (const item of value) strings(item, output);
  else if (value && typeof value === "object") for (const item of Object.values(value)) strings(item, output);
  return output;
}

const server = createServer({ maxHeaderSize: 8192, requestTimeout: 10000, headersTimeout: 3000 }, async (request, response) => {
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
  const requestedTools = toolNames(body);
  if (
    body.model !== MODEL_ID || body.stream !== true || !requestedTools.includes("pixel_research")
    || new Set(requestedTools).size !== requestedTools.length || requestedTools.some((name) => !ALLOWED_TOOLS.includes(name))
  ) return sendJson(response, 400, { error: "model or tool contract mismatch" });
  calls += 1;
  if (calls === 1) return sendEvents(response, toolCallEvents());
  const evidenceText = strings(body).join("\n");
  const batchSha256 = /"batchSha256":"([a-f0-9]{64})"/u.exec(evidenceText)?.[1];
  const sourceId = /"sourceId":"(source-[a-f0-9]{16})"/u.exec(evidenceText)?.[1];
  if (!batchSha256 || !sourceId || !evidenceText.includes(EVIDENCE)) return sendEvents(response, finalEvents("PIXEL_RESEARCHER_E2E_RED"));
  if (MODE === "invalid-proposal") return sendEvents(response, finalEvents("not-json"));
  if (MODE !== "success") return sendJson(response, 500, { error: "unsupported fake Researcher mode" });
  const proposal = {
    $schema: "https://osmantic.com/pixel/schemas/work-research-report-proposal-v1.schema.json",
    schemaVersion: 1,
    title: "Public agent safety boundary result",
    batchSha256s: [batchSha256],
    findings: [{ statement: "The public safety-boundary fixture is green.", citations: [{ batchSha256, sourceId, evidence: EVIDENCE }] }],
    limitations: "This deterministic fixture proves the broker and citation path, not the truth of arbitrary public sources.",
    dataClassification: "public", privateDataIncluded: false, externalEffects: false,
    authority: AUTHORITY, boundary: PROPOSAL_BOUNDARY,
  };
  return sendEvents(response, finalEvents(JSON.stringify(proposal)));
});

server.maxRequestsPerSocket = 8;
server.on("upgrade", (_request, socket) => socket.destroy());
server.listen(8080, "0.0.0.0");

const stop = () => server.close(() => { process.exitCode = 0; });
process.once("SIGINT", stop);
process.once("SIGTERM", stop);
