import { canonical } from "../../scripts/lib/work-contract.mjs";
import { validateWorkCodexCliPayload } from "./codex-cli-qualification.mjs";

const MAGIC = Buffer.from("PIXEL-CODEX-CONTAINER-V1\n", "ascii");
const MAX_HEADER_BYTES = 4096;
const MAX_CREDENTIAL_BYTES = 262144;
const MAX_PAYLOAD_BYTES = 1048576;

export class WorkCodexContainerProtocolError extends Error {}
function fail(message) { throw new WorkCodexContainerProtocolError(message); }

function strictUtf8(bytes, label) {
  if (!Buffer.isBuffer(bytes) || bytes.includes(0)) fail(`${label} contains invalid bytes`);
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { fail(`${label} is not strict UTF-8`); }
}

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} has unsupported fields`);
}

function credentialBytes(value, authMode) {
  const bytes = Buffer.isBuffer(value) ? Buffer.from(value) : typeof value === "string" ? Buffer.from(value, "utf8") : null;
  if (!bytes || bytes.length < 1 || bytes.length > (authMode === "api-key" ? 8192 : MAX_CREDENTIAL_BYTES) || bytes.includes(0)) fail("Codex container credential has an invalid bounded shape");
  const text = strictUtf8(bytes, "Codex container credential");
  if (authMode === "api-key") {
    const key = text.endsWith("\n") ? text.slice(0, -1) : text;
    if (key.length < 20 || /\s|[\u0000-\u001f\u007f]/u.test(key) || text !== key && text !== `${key}\n`) fail("Codex container API credential has an invalid bounded one-line shape");
  } else {
    let parsed; try { parsed = JSON.parse(text); } catch { fail("Codex container ChatGPT cache is invalid JSON"); }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || parsed.auth_mode !== "chatgpt") fail("Codex container ChatGPT cache has the wrong authentication mode");
  }
  return bytes;
}

export function encodeWorkCodexContainerFrame({ payload, credential }) {
  validateWorkCodexCliPayload(payload);
  const authMode = payload.provider.authMode, secret = credentialBytes(credential, authMode);
  const payloadText = canonical(payload), payloadBytes = Buffer.from(payloadText, "utf8");
  if (payloadBytes.length < 2 || payloadBytes.length > MAX_PAYLOAD_BYTES) fail("Codex container payload exceeds its byte ceiling");
  const header = Buffer.from(`${canonical({ schemaVersion: 1, authMode, credentialBytes: secret.length, payloadBytes: payloadBytes.length })}\n`, "utf8");
  if (header.length > MAX_HEADER_BYTES) fail("Codex container frame header exceeds its byte ceiling");
  return Buffer.concat([MAGIC, header, secret, payloadBytes]);
}

export function decodeWorkCodexContainerFrame(frame) {
  if (!Buffer.isBuffer(frame) || frame.length < MAGIC.length + 3 || frame.length > MAGIC.length + MAX_HEADER_BYTES + MAX_CREDENTIAL_BYTES + MAX_PAYLOAD_BYTES || !frame.subarray(0, MAGIC.length).equals(MAGIC)) fail("Codex container frame has invalid magic or size");
  const headerEnd = frame.indexOf(0x0a, MAGIC.length);
  if (headerEnd < 0 || headerEnd - MAGIC.length > MAX_HEADER_BYTES) fail("Codex container frame header is invalid");
  let header; try { header = JSON.parse(strictUtf8(frame.subarray(MAGIC.length, headerEnd), "Codex container frame header")); } catch (error) { if (error instanceof WorkCodexContainerProtocolError) throw error; fail("Codex container frame header is invalid JSON"); }
  exactKeys(header, ["schemaVersion", "authMode", "credentialBytes", "payloadBytes"], "Codex container frame header");
  if (header.schemaVersion !== 1 || !["chatgpt", "api-key"].includes(header.authMode) || !Number.isSafeInteger(header.credentialBytes) || !Number.isSafeInteger(header.payloadBytes) || header.credentialBytes < 1 || header.credentialBytes > MAX_CREDENTIAL_BYTES || header.payloadBytes < 2 || header.payloadBytes > MAX_PAYLOAD_BYTES) fail("Codex container frame header values are invalid");
  const body = headerEnd + 1, expected = body + header.credentialBytes + header.payloadBytes;
  if (frame.length !== expected) fail("Codex container frame length differs from its header");
  const credential = credentialBytes(frame.subarray(body, body + header.credentialBytes), header.authMode);
  const payloadText = strictUtf8(frame.subarray(body + header.credentialBytes), "Codex container payload");
  let payload; try { payload = JSON.parse(payloadText); } catch { fail("Codex container payload is invalid JSON"); }
  if (payloadText !== canonical(payload)) fail("Codex container payload is not canonical JSON");
  validateWorkCodexCliPayload(payload);
  if (payload.provider.authMode !== header.authMode) fail("Codex container credential mode differs from its payload");
  return Object.freeze({ authMode: header.authMode, credential, payload });
}
