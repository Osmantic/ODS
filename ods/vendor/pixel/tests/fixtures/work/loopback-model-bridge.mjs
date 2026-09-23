#!/usr/bin/env node
import { createServer } from "node:http";

const MAX_REQUEST_BYTES = 4 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const FIXED_ORIGIN = "http://pixel-local-model:8080";

function reject(response, status, message) {
  const body = Buffer.from(`${message}\n`, "utf8");
  response.writeHead(status, {
    "content-type": "text/plain; charset=utf-8",
    "content-length": String(body.length),
    "cache-control": "no-store",
  });
  response.end(body);
}

export function createLoopbackModelBridge(origin = process.env.PIXEL_MODEL_ORIGIN) {
  if (origin !== FIXED_ORIGIN) throw new Error("fixed private model origin is required");
  return createServer(async (request, response) => {
  if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
    reject(response, 404, "not found");
    return;
  }
  if (request.headers.authorization !== undefined || request.headers.cookie !== undefined) {
    reject(response, 400, "credentials are forbidden");
    return;
  }
  const declared = Number(request.headers["content-length"] ?? 0);
  if (!Number.isSafeInteger(declared) || declared < 1 || declared > MAX_REQUEST_BYTES) {
    reject(response, 413, "request size is invalid");
    return;
  }
  const chunks = [];
  let bytes = 0;
  for await (const chunk of request) {
    bytes += chunk.length;
    if (bytes > MAX_REQUEST_BYTES) {
      reject(response, 413, "request is too large");
      return;
    }
    chunks.push(chunk);
  }
  const body = Buffer.concat(chunks);
  if (body.length !== declared) {
    reject(response, 400, "request length differs");
    return;
  }
  let upstream;
  try {
    upstream = await fetch(`${origin}/v1/chat/completions`, {
      method: "POST",
      headers: { "content-type": "application/json", "content-length": String(body.length) },
      body,
      redirect: "error",
      signal: AbortSignal.timeout(120000),
    });
  } catch {
    reject(response, 502, "private model request failed");
    return;
  }
  const encoding = upstream.headers.get("content-encoding");
  const contentType = upstream.headers.get("content-type") ?? "";
  const upstreamLength = Number(upstream.headers.get("content-length") ?? 0);
  if (encoding || !/^application\/json(?:;|$)/iu.test(contentType) || !Number.isSafeInteger(upstreamLength) || upstreamLength < 1 || upstreamLength > MAX_RESPONSE_BYTES) {
    await upstream.body?.cancel().catch(() => {});
    reject(response, 502, "private model response contract failed");
    return;
  }
  const responseBytes = Buffer.from(await upstream.arrayBuffer());
  if (responseBytes.length !== upstreamLength || responseBytes.length > MAX_RESPONSE_BYTES) {
    reject(response, 502, "private model response size differs");
    return;
  }
  response.writeHead(upstream.status, {
    "content-type": "application/json",
    "content-length": String(responseBytes.length),
    "cache-control": "no-store",
  });
  response.end(responseBytes);
  });
}

export async function startLoopbackModelBridge({ origin = process.env.PIXEL_MODEL_ORIGIN, host = "127.0.0.1", port = 0 } = {}) {
  const server = createLoopbackModelBridge(origin);
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, resolve);
  });
  return server;
}
