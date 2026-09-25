const DEFAULT_MAX_FRAME_BYTES = 1024 * 1024;
const DEFAULT_MAX_REASSEMBLED_BYTES = 64 * 1024 * 1024;
const DEFAULT_MAX_STREAM_BYTES = 256 * 1024 * 1024;
const MAX_CHUNKS = 4096;
const CHUNK_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const BASE64_RE = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

export class RpcFramingError extends Error {}

function fail(message) {
  throw new RpcFramingError(message);
}

function object(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${label} must be a JSON object`);
  return value;
}

function strictJson(bytes, label) {
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    fail(`${label} is not strict UTF-8`);
  }
  try {
    return object(JSON.parse(text), label);
  } catch (error) {
    if (error instanceof RpcFramingError) throw error;
    fail(`${label} is not JSON`);
  }
}

function strictBase64(value) {
  if (typeof value !== "string" || value.length === 0 || value.length % 4 !== 0 || !BASE64_RE.test(value)) {
    fail("rpc_chunk data is not canonical base64");
  }
  const bytes = Buffer.from(value, "base64");
  if (bytes.toString("base64") !== value) fail("rpc_chunk data is not canonical base64");
  return bytes;
}

export class RpcFrameDecoder {
  #active = null;
  #maxFrameBytes;
  #maxReassembledBytes;

  constructor(options = {}) {
    this.#maxFrameBytes = options.maxFrameBytes ?? DEFAULT_MAX_FRAME_BYTES;
    this.#maxReassembledBytes = options.maxReassembledBytes ?? DEFAULT_MAX_REASSEMBLED_BYTES;
    if (!Number.isSafeInteger(this.#maxFrameBytes) || this.#maxFrameBytes < 1024) fail("invalid RPC frame ceiling");
    if (!Number.isSafeInteger(this.#maxReassembledBytes) || this.#maxReassembledBytes < this.#maxFrameBytes) fail("invalid RPC reassembly ceiling");
  }

  pushLine(input) {
    let bytes = Buffer.isBuffer(input) ? input : Buffer.from(input);
    if (bytes.at(-1) === 0x0d) bytes = bytes.subarray(0, -1);
    if (bytes.length === 0 || bytes.length > this.#maxFrameBytes) fail("RPC physical frame is empty or oversized");
    const frame = strictJson(bytes, "RPC frame");
    if (frame.type !== "rpc_chunk") {
      if (this.#active) fail("rpc_chunk sequence was interrupted");
      return [frame];
    }
    if (JSON.stringify(Object.keys(frame).sort()) !== JSON.stringify(["byteLength", "chunkId", "count", "data", "index", "type"])) {
      fail("rpc_chunk has unsupported fields");
    }
    if (
      !CHUNK_ID_RE.test(frame.chunkId ?? "")
      || !Number.isSafeInteger(frame.index)
      || !Number.isSafeInteger(frame.count)
      || !Number.isSafeInteger(frame.byteLength)
      || frame.count < 1
      || frame.count > MAX_CHUNKS
      || frame.index < 0
      || frame.index >= frame.count
      || frame.byteLength < 2
      || frame.byteLength > this.#maxReassembledBytes
    ) fail("rpc_chunk metadata is invalid or oversized");
    const decoded = strictBase64(frame.data);
    if (!this.#active) {
      if (frame.index !== 0) fail("rpc_chunk sequence must start at index zero");
      this.#active = { chunkId: frame.chunkId, count: frame.count, byteLength: frame.byteLength, next: 0, bytes: 0, chunks: [] };
    }
    const active = this.#active;
    if (
      active.chunkId !== frame.chunkId
      || active.count !== frame.count
      || active.byteLength !== frame.byteLength
      || active.next !== frame.index
    ) fail("rpc_chunk sequence is interleaved, reordered, or inconsistent");
    active.bytes += decoded.length;
    if (active.bytes > active.byteLength || active.bytes > this.#maxReassembledBytes) fail("rpc_chunk sequence exceeds its byte ceiling");
    active.chunks.push(decoded);
    active.next += 1;
    if (active.next !== active.count) return [];
    this.#active = null;
    if (active.bytes !== active.byteLength) fail("rpc_chunk sequence byte length is inconsistent");
    const reconstructed = strictJson(Buffer.concat(active.chunks, active.bytes), "reassembled RPC frame");
    if (reconstructed.type === "rpc_chunk") fail("nested rpc_chunk frames are forbidden");
    return [reconstructed];
  }

  end() {
    if (this.#active) fail("RPC stream ended during rpc_chunk sequence");
  }
}

export class RpcJsonlDecoder {
  #buffer = Buffer.alloc(0);
  #frames;
  #maxFrameBytes;
  #maxStreamBytes;
  #streamBytes = 0;

  constructor(options = {}) {
    this.#maxFrameBytes = options.maxFrameBytes ?? DEFAULT_MAX_FRAME_BYTES;
    this.#maxStreamBytes = options.maxStreamBytes ?? DEFAULT_MAX_STREAM_BYTES;
    if (!Number.isSafeInteger(this.#maxStreamBytes) || this.#maxStreamBytes < this.#maxFrameBytes) fail("invalid RPC stream ceiling");
    this.#frames = new RpcFrameDecoder(options);
  }

  push(input) {
    const bytes = Buffer.isBuffer(input) ? input : Buffer.from(input);
    this.#streamBytes += bytes.length;
    if (this.#streamBytes > this.#maxStreamBytes) fail("RPC stdout exceeded its total byte ceiling");
    this.#buffer = this.#buffer.length === 0 ? Buffer.from(bytes) : Buffer.concat([this.#buffer, bytes]);
    const output = [];
    for (;;) {
      const newline = this.#buffer.indexOf(0x0a);
      if (newline === -1) break;
      const line = this.#buffer.subarray(0, newline);
      this.#buffer = this.#buffer.subarray(newline + 1);
      output.push(...this.#frames.pushLine(line));
    }
    if (this.#buffer.length > this.#maxFrameBytes) fail("RPC physical frame exceeded its byte ceiling before newline");
    return output;
  }

  end() {
    if (this.#buffer.length !== 0) fail("RPC stream ended with an unterminated frame");
    this.#frames.end();
  }
}

export const rpcFramingLimits = Object.freeze({
  maxFrameBytes: DEFAULT_MAX_FRAME_BYTES,
  maxReassembledBytes: DEFAULT_MAX_REASSEMBLED_BYTES,
  maxStreamBytes: DEFAULT_MAX_STREAM_BYTES,
});
