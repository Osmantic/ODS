import { createServer } from "node:http";

function shape(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return typeof value;
  return Object.keys(value).sort();
}

const server = createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  let body;
  try { body = JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { body = null; }
  process.stdout.write(`${JSON.stringify({
    method: request.method,
    url: request.url,
    keys: shape(body),
    reasoning: body?.reasoning ?? null,
    text: body?.text ?? null,
    tools: Array.isArray(body?.tools) ? body.tools.map((tool) => ({ type: tool?.type, keys: shape(tool) })) : null,
    input: Array.isArray(body?.input) ? body.input.map((item) => ({
      type: item?.type, role: item?.role, keys: shape(item),
      content: Array.isArray(item?.content) ? item.content.map((part) => ({ type: part?.type, keys: shape(part) })) : null,
    })) : null,
  })}\n`);
  const payload = Buffer.from('{"error":{"message":"shape captured"}}\n', "utf8");
  response.writeHead(400, { "content-type": "application/json", "content-length": String(payload.length), connection: "close" });
  response.end(payload);
});

server.listen(8080, "0.0.0.0");
