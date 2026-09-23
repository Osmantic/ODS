import { createServer } from "node:http";

const MAX_REQUEST_BYTES = 4 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
export const MODEL_QUALIFICATION_UPSTREAM = "http://pixel-local-model:8080";

export class WorkModelQualificationBridgeError extends Error {}

function fail(message) {
  throw new WorkModelQualificationBridgeError(message);
}

function reject(response, status, message) {
  const body = Buffer.from(`${message}\n`, "utf8");
  response.writeHead(status, {
    "content-type": "text/plain; charset=utf-8",
    "content-length": String(body.length),
    "cache-control": "no-store",
  });
  response.end(body);
}

async function boundedBody(stream, maximum) {
  const chunks = [];
  let total = 0;
  for await (const chunk of stream) {
    total += chunk.length;
    if (total > maximum) fail("private model response exceeded its byte limit");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks, total);
}

export function createModelQualificationBridge({
  origin = MODEL_QUALIFICATION_UPSTREAM,
  fetchImpl = globalThis.fetch,
} = {}) {
  if (origin !== MODEL_QUALIFICATION_UPSTREAM || typeof fetchImpl !== "function") {
    fail("fixed private model origin is required");
  }
  return createServer(async (request, response) => {
    try {
      if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
        reject(response, 404, "not found");
        return;
      }
      if (request.headers.authorization !== undefined || request.headers.cookie !== undefined || request.headers["proxy-authorization"] !== undefined) {
        reject(response, 400, "credentials are forbidden");
        return;
      }
      const requestContentType = (request.headers["content-type"] ?? "").split(";", 1)[0].trim().toLowerCase();
      if (requestContentType !== "application/json" || request.headers["content-encoding"] !== undefined) {
        reject(response, 415, "request content contract is invalid");
        return;
      }
      const declaredText = request.headers["content-length"];
      if (typeof declaredText !== "string" || !/^[1-9][0-9]*$/u.test(declaredText)) {
        reject(response, 411, "request length is required");
        return;
      }
      const declared = Number(declaredText);
      if (!Number.isSafeInteger(declared) || declared > MAX_REQUEST_BYTES) {
        reject(response, 413, "request size is invalid");
        return;
      }
      const body = await boundedBody(request, MAX_REQUEST_BYTES);
      if (body.length !== declared) {
        reject(response, 400, "request length differs");
        return;
      }
      let upstream;
      try {
        upstream = await fetchImpl(`${origin}/v1/chat/completions`, {
          method: "POST",
          headers: { "content-type": "application/json", "content-length": String(body.length), "accept-encoding": "identity", "connection": "close" },
          body,
          redirect: "error",
          signal: AbortSignal.timeout(300000),
        });
      } catch {
        reject(response, 502, "private model request failed");
        return;
      }
      const contentType = (upstream.headers.get("content-type") ?? "").split(";", 1)[0].trim().toLowerCase();
      if (upstream.status !== 200 || contentType !== "application/json" || upstream.headers.get("content-encoding")) {
        await upstream.body?.cancel().catch(() => {});
        reject(response, 502, "private model response contract failed");
        return;
      }
      let responseBytes;
      try {
        responseBytes = upstream.body ? await boundedBody(upstream.body, MAX_RESPONSE_BYTES) : null;
      } catch {
        reject(response, 502, "private model response exceeded its byte limit");
        return;
      }
      if (!responseBytes || responseBytes.length < 2) {
        reject(response, 502, "private model response is missing");
        return;
      }
      const upstreamLength = upstream.headers.get("content-length");
      if (upstreamLength !== null && (!/^(?:0|[1-9][0-9]*)$/u.test(upstreamLength) || Number(upstreamLength) !== responseBytes.length)) {
        reject(response, 502, "private model response size differs");
        return;
      }
      response.writeHead(200, {
        "content-type": "application/json",
        "content-length": String(responseBytes.length),
        "cache-control": "no-store",
      });
      response.end(responseBytes);
    } catch {
      if (!response.headersSent) reject(response, 502, "private model bridge failed closed");
      else response.destroy();
    }
  });
}

export async function startModelQualificationBridge({
  origin = MODEL_QUALIFICATION_UPSTREAM,
  host = "127.0.0.1",
  port = 18081,
  fetchImpl = globalThis.fetch,
} = {}) {
  if (host !== "127.0.0.1" || port !== 18081) fail("fixed qualification loopback listener is required");
  const server = createModelQualificationBridge({ origin, fetchImpl });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  return server;
}
