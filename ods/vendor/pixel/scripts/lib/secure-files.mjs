import { constants } from "node:fs";
import { open } from "node:fs/promises";

const READ_FLAGS = constants.O_RDONLY
  | (constants.O_NOFOLLOW ?? 0)
  | (constants.O_CLOEXEC ?? 0)
  | (constants.O_NONBLOCK ?? 0);

export async function readBoundedRegularFile(path, maximum, label = "file") {
  if (!Number.isSafeInteger(maximum) || maximum < 1) throw new Error("maximum file size is invalid");
  const handle = await open(path, READ_FLAGS);
  try {
    const details = await handle.stat();
    if (!details.isFile() || details.size < 0 || details.size > maximum) {
      throw new Error(`${label} is not a bounded regular file: ${path}`);
    }
    const chunks = [];
    let total = 0;
    while (total <= maximum) {
      const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, maximum + 1 - total));
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      chunks.push(buffer.subarray(0, bytesRead));
      total += bytesRead;
    }
    if (total > maximum) throw new Error(`${label} exceeded its size limit: ${path}`);
    const afterDetails = await handle.stat();
    return { bytes: Buffer.concat(chunks, total), details, afterDetails };
  } finally {
    await handle.close();
  }
}

export async function readBoundedRegularText(path, maximum, label = "file") {
  const result = await readBoundedRegularFile(path, maximum, label);
  return { text: result.bytes.toString("utf8"), details: result.details, afterDetails: result.afterDetails };
}

export function parseStrictJson(text, label = "JSON") {
  if (typeof text !== "string") throw new Error(`${label} is not strict JSON`);
  let parsed;
  try { parsed = JSON.parse(text); } catch { throw new Error(`${label} is not strict JSON`); }
  let position = 0;
  const fail = (message = "is not strict JSON") => { throw new Error(`${label} ${message}`); };
  const whitespace = () => { while (/\s/u.test(text[position] ?? "")) position += 1; };
  const stringToken = () => {
    if (text[position] !== '"') fail();
    const start = position++;
    while (position < text.length) {
      const character = text[position++];
      if (character === '"') {
        try { return JSON.parse(text.slice(start, position)); } catch { fail(); }
      }
      if (character === "\\") position += 1;
    }
    fail();
  };
  const value = () => {
    whitespace();
    if (text[position] === "{") {
      position += 1; whitespace();
      const keys = new Set();
      if (text[position] === "}") { position += 1; return; }
      while (position < text.length) {
        const key = stringToken();
        if (keys.has(key)) fail("contains a duplicate object key");
        keys.add(key); whitespace();
        if (text[position++] !== ":") fail();
        value(); whitespace();
        const separator = text[position++];
        if (separator === "}") return;
        if (separator !== ",") fail();
        whitespace();
      }
      fail();
    } else if (text[position] === "[") {
      position += 1; whitespace();
      if (text[position] === "]") { position += 1; return; }
      while (position < text.length) {
        value(); whitespace();
        const separator = text[position++];
        if (separator === "]") return;
        if (separator !== ",") fail();
      }
      fail();
    } else if (text[position] === '"') stringToken();
    else {
      const start = position;
      while (position < text.length && !/[\s,}\]]/u.test(text[position])) position += 1;
      if (position === start) fail();
    }
  };
  value(); whitespace();
  if (position !== text.length) fail();
  return parsed;
}
