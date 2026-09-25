import assert from "node:assert/strict";
import test from "node:test";

import { RpcFrameDecoder, RpcFramingError, RpcJsonlDecoder } from "../deploy/work-runner/rpc-framing.mjs";

const line = (value) => Buffer.from(JSON.stringify(value), "utf8");
const chunk = (id, index, count, bytes, total = bytes.length) => ({
  type: "rpc_chunk", chunkId: id, index, count, byteLength: total, data: bytes.toString("base64"),
});

test("RPC decoder accepts a bounded physical frame", () => {
  const decoder = new RpcFrameDecoder();
  assert.deepEqual(decoder.pushLine(line({ type: "ready", protocolVersion: 1, supportedProtocolVersions: [1, 2] })), [
    { type: "ready", protocolVersion: 1, supportedProtocolVersions: [1, 2] },
  ]);
  decoder.end();
});

test("RPC v2 chunks reassemble strict UTF-8 JSON losslessly", () => {
  const decoder = new RpcFrameDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 4096 });
  const logical = Buffer.from(JSON.stringify({ type: "message_end", message: "snowman ☃" }), "utf8");
  const split = Math.floor(logical.length / 2);
  assert.deepEqual(decoder.pushLine(line(chunk("rpc-1", 0, 2, logical.subarray(0, split), logical.length))), []);
  assert.deepEqual(decoder.pushLine(line(chunk("rpc-1", 1, 2, logical.subarray(split), logical.length))), [
    { type: "message_end", message: "snowman ☃" },
  ]);
  decoder.end();
});

test("RPC decoder fails closed on interrupted, reordered, and malformed chunks", () => {
  const logical = Buffer.from('{"type":"agent_end"}', "utf8");
  for (const run of [
    () => {
      const decoder = new RpcFrameDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 4096 });
      decoder.pushLine(line(chunk("rpc-1", 0, 2, logical.subarray(0, 5), logical.length)));
      decoder.pushLine(line({ type: "agent_end" }));
    },
    () => {
      const decoder = new RpcFrameDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 4096 });
      decoder.pushLine(line(chunk("rpc-1", 1, 2, logical, logical.length)));
    },
    () => {
      const decoder = new RpcFrameDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 4096 });
      decoder.pushLine(line({ ...chunk("rpc-1", 0, 1, logical, logical.length), data: "!!!!" }));
    },
    () => {
      const decoder = new RpcFrameDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 4096 });
      decoder.pushLine(line({ ...chunk("rpc-1", 0, 1, logical, logical.length), surprise: true }));
    },
  ]) assert.throws(run, RpcFramingError);
});

test("RPC JSONL decoder handles fragmented transport and rejects truncation", () => {
  const decoder = new RpcJsonlDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 4096, maxStreamBytes: 8192 });
  const bytes = Buffer.from(`${JSON.stringify({ type: "ready" })}\n${JSON.stringify({ type: "agent_end" })}\n`, "utf8");
  assert.deepEqual(decoder.push(bytes.subarray(0, 7)), []);
  assert.deepEqual(decoder.push(bytes.subarray(7)), [{ type: "ready" }, { type: "agent_end" }]);
  decoder.end();

  const truncated = new RpcJsonlDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 4096, maxStreamBytes: 8192 });
  truncated.push('{"type":"ready"}');
  assert.throws(() => truncated.end(), /unterminated/);
});

test("RPC framing enforces physical, logical, and total output ceilings", () => {
  const physical = new RpcFrameDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 2048 });
  assert.throws(() => physical.pushLine(Buffer.alloc(1025, 0x20)), /oversized/);
  const logical = new RpcFrameDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 2048 });
  assert.throws(() => logical.pushLine(line(chunk("rpc-1", 0, 1, Buffer.from("{}"), 2049))), /oversized/);
  const total = new RpcJsonlDecoder({ maxFrameBytes: 1024, maxReassembledBytes: 2048, maxStreamBytes: 1024 });
  assert.throws(() => total.push(Buffer.alloc(1025, 0x20)), /total byte ceiling/);
});
