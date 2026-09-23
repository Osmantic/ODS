import { createServer } from "node:http";

const MAX_BODY_BYTES = 2 * 1024 * 1024;
const MODEL_ID = process.env.PIXEL_FAKE_MODEL_ID ?? "assistant-model";
const MODE = process.env.PIXEL_FAKE_DATA_LAB_MODE ?? "success";
const ALLOWED_TOOLS = ["bash", "edit", "glob", "grep", "read", "write"];
const PROPOSAL_BOUNDARY = "Untrusted local Data Lab proposal only. It grants no truth, verification, publication, action, policy, or completion authority and must be bound to exact artifacts and independently replayed by Pixel.";
const AUTHORITY = {
  rawInputMutation: false, hostAccess: false, network: false, credentials: false,
  externalEffects: false, publish: false, policyMutation: false, scopeExpansion: false,
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

function toolCallEvents(id, name, value) {
  const item = { type: "function_call", id: `fc_${id}`, call_id: `call_${id}`, name, arguments: "" };
  const args = JSON.stringify(value);
  return [
    { type: "response.created", response: { id: `resp_${id}`, status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.function_call_arguments.delta", output_index: 0, item_id: item.id, delta: args },
    { type: "response.output_item.done", output_index: 0, item: { ...item, arguments: args, status: "completed" } },
    {
      type: "response.completed",
      response: {
        id: `resp_${id}`, status: "completed", output: [{ ...item, arguments: args, status: "completed" }],
        usage: { input_tokens: 32, output_tokens: 16, total_tokens: 48, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

function finalEvents(text) {
  const item = { type: "message", id: "msg_data_final", role: "assistant", status: "in_progress", content: [] };
  return [
    { type: "response.created", response: { id: "resp_data_final", status: "in_progress", output: [] } },
    { type: "response.output_item.added", output_index: 0, item },
    { type: "response.content_part.added", output_index: 0, item_id: item.id, content_index: 0, part: { type: "output_text", text: "" } },
    { type: "response.output_text.delta", output_index: 0, item_id: item.id, content_index: 0, delta: text },
    { type: "response.output_item.done", output_index: 0, item: { ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] } },
    {
      type: "response.completed",
      response: {
        id: "resp_data_final", status: "completed",
        output: [{ ...item, status: "completed", content: [{ type: "output_text", text, annotations: [] }] }],
        usage: { input_tokens: 64, output_tokens: 48, total_tokens: 112, input_tokens_details: { cached_tokens: 0 } },
      },
    },
    "[DONE]",
  ];
}

function recipe() {
  const replayMismatch = MODE === "replay-mismatch";
  return `import argparse\nimport csv\nimport os\nimport duckdb\nimport polars as pl\n\nparser = argparse.ArgumentParser()\nparser.add_argument("--input-root", required=True)\nparser.add_argument("--output-root", required=True)\nargs = parser.parse_args()\nsource = os.path.join(args.input_root, "sales.csv")\nframe = pl.read_csv(source)\npolars_rows = frame.group_by("region").agg(pl.col("revenue").sum()).sort("region").rows()\nconnection = duckdb.connect(":memory:")\nconnection.execute("CREATE TABLE sales AS SELECT * FROM read_csv_auto(?)", [source])\nduck_rows = connection.execute("SELECT region, sum(revenue) FROM sales GROUP BY region ORDER BY region").fetchall()\nassert polars_rows == duck_rows\nrows = list(duck_rows)\nif ${replayMismatch ? "True" : "False"} and os.path.abspath(args.output_root).startswith("/replay/"):\n    rows[0] = (rows[0][0], rows[0][1] + 1)\nos.makedirs(args.output_root, exist_ok=True)\nwith open(os.path.join(args.output_root, "summary.csv"), "w", encoding="utf-8", newline="") as handle:\n    writer = csv.writer(handle, lineterminator="\\n")\n    writer.writerow(["region", "revenue"])\n    writer.writerows(rows)\nwith open(os.path.join(args.output_root, "summary.md"), "w", encoding="utf-8", newline="\\n") as handle:\n    handle.write("# Regional revenue\\n\\n" + "\\n".join(f"- {region}: {revenue}" for region, revenue in rows) + "\\n")\nprint("PIXEL_DATA_LAB_TOOL_GREEN")\n`;
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
    body.model !== MODEL_ID || body.stream !== true || !requestedTools.includes("read")
    || new Set(requestedTools).size !== requestedTools.length || requestedTools.some((name) => !ALLOWED_TOOLS.includes(name))
  ) return sendJson(response, 400, { error: "model or tool contract mismatch" });
  calls += 1;
  if (calls === 1) return sendEvents(response, toolCallEvents("data_initial_read", "read", { path: "/inputs/sales.csv" }));
  if (calls === 2) return sendEvents(response, toolCallEvents("data_recipe_write", "write", { path: "recipe.py", content: recipe() }));
  if (calls === 3) return sendEvents(response, toolCallEvents("data_recipe_run", "bash", { command: "python3 recipe.py --input-root /inputs --output-root artifacts" }));
  if (!['success', 'replay-mismatch'].includes(MODE)) return sendJson(response, 500, { error: "unsupported fake Data Lab mode" });
  const proposal = {
    $schema: "https://osmantic.com/pixel/schemas/work-data-report-proposal-v1.schema.json",
    schemaVersion: 1,
    title: "Regional revenue result",
    summary: "The local fixture contains two deterministic regional totals.",
    methodology: "A pinned Polars aggregation was cross-checked with pinned DuckDB, then serialized by a saved deterministic Python recipe.",
    artifacts: [
      { path: "artifacts/summary.csv", purpose: "Machine-readable grouped regional totals" },
      { path: "artifacts/summary.md", purpose: "Human-readable grouped regional totals" },
    ],
    findings: [{ statement: "The fixture has exact grouped totals for east and west.", evidencePaths: ["artifacts/summary.csv", "artifacts/summary.md"] }],
    limitations: "This synthetic fixture tests local reproducibility and does not establish real-world business truth.",
    dataClassification: "confidential", privateDataIncluded: true, externalEffects: false,
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
