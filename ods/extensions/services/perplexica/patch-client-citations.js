"use strict";

// Vane v1.12.2 rewrites every bracket pair before Markdown rendering. On a
// research answer this turns Python lists inside code fences into citations.
// Patch only the exact pinned client expression; an unknown bundle must not
// silently serve the broken renderer.
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { renderCitations } = require("./citation-renderer");

const DEFAULT_ROOT = "/home/vane/public/_next/static/chunks";
const KNOWN_CHUNK = "1220-5cd2adbf287bf784.js";
const KNOWN_SHA256 = "24028fd3cee772aabfb5f836f8ced901a6a155c3c302b898401e3f863eef801d";
const TRUSTED_CHUNKS = Object.freeze({ [KNOWN_CHUNK]: KNOWN_SHA256 });
const STRICT_HEADER = '"use strict";';
const ORIGINAL = 's=l.length>0?s.replace(/\\[([^\\]]+)\\]/g,(e,t)=>t.split(",").map(e=>e.trim()).map(e=>{let t=parseInt(e);if(isNaN(t)||t<=0)return`[${e}]`;let r=l[t-1],a=r?.metadata?.url;return a?`<citation href="${a}">${e}</citation>`:""}).join("")):s.replace(d,"")';
const CALL = "s=self.__odsVaneCitationRender20261003(s,l)";
const PRELUDE_START = "self.__odsVaneCitationRender20261003=";
const PRELUDE_END = "/* ods-vane-citation-prelude-end:20261003 */\n";
const PRELUDE = `${PRELUDE_START}${renderCitations.toString()};\n${PRELUDE_END}`;

function occurrences(text, fragment) {
  return text.split(fragment).length - 1;
}

function sha256(text) {
  return crypto.createHash("sha256").update(text).digest("hex");
}

function patchClientChunk(root = DEFAULT_ROOT, trustedChunks = TRUSTED_CHUNKS) {
  const candidates = [];
  for (const name of fs.readdirSync(root)) {
    if (!name.endsWith(".js")) continue;
    const file = path.join(root, name);
    if (!fs.lstatSync(file).isFile()) continue;
    const text = fs.readFileSync(file, "utf8");
    if (text.includes(ORIGINAL) || text.includes(CALL) || text.includes("__odsVaneCitationRender20261003")) {
      candidates.push({ file, name, text });
    }
  }
  if (candidates.length !== 1) {
    throw new Error(`expected one Vane citation chunk in ${root}; found ${candidates.length}`);
  }

  const { file, name, text } = candidates[0];
  const expectedHash = trustedChunks[name];
  if (!expectedHash) throw new Error(`unqualified Vane citation chunk: ${file}`);
  const originalCount = occurrences(text, ORIGINAL);
  const patchedCount = occurrences(text, CALL);
  let original = text;
  if (text.startsWith(STRICT_HEADER + PRELUDE_START) && originalCount === 0 && patchedCount === 1) {
    // The end marker allows a later renderer revision to recover the audited
    // original even when Compose restarts this container without recreating it.
    const marker = text.indexOf(PRELUDE_END, STRICT_HEADER.length + PRELUDE_START.length);
    if (marker < 0 || occurrences(text, PRELUDE_END) !== 1) {
      throw new Error(`Vane citation renderer has an unknown or partial shape in ${file}`);
    }
    const preludeEnd = marker + PRELUDE_END.length;
    // Discard the prior renderer only after proving the remaining complete
    // client bundle reconstructs to the exact upstream bytes. It cannot run
    // before this patch finishes and the Vane server starts.
    original = STRICT_HEADER + text.slice(preludeEnd).replace(CALL, ORIGINAL);
    if (sha256(original) !== expectedHash) throw new Error(`patched Vane client hash mismatch in ${file}`);
    if (occurrences(original, ORIGINAL) !== 1 || occurrences(original, CALL) !== 0) {
      throw new Error(`Vane citation renderer has an unknown or partial shape in ${file}`);
    }
    if (text.slice(STRICT_HEADER.length, preludeEnd) === PRELUDE) {
      return { file, changed: false };
    }
  } else {
    if (!text.startsWith(STRICT_HEADER) || originalCount !== 1 || patchedCount !== 0 || text.includes("__odsVaneCitationRender20261003")) {
      throw new Error(`Vane citation renderer has an unknown or partial shape in ${file}`);
    }
    if (sha256(text) !== expectedHash) throw new Error(`pinned Vane client hash mismatch in ${file}`);
  }

  const patched = STRICT_HEADER + PRELUDE + original.slice(STRICT_HEADER.length).replace(ORIGINAL, CALL);
  const temporary = `${file}.ods-${process.pid}.tmp`;
  try {
    fs.writeFileSync(temporary, patched, { mode: fs.statSync(file).mode & 0o777 });
    fs.renameSync(temporary, file);
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
  return { file, changed: true };
}

if (require.main === module) {
  try {
    const result = patchClientChunk(process.argv[2] || DEFAULT_ROOT);
    process.stderr.write(`[ods-perplexica] client citations ${result.changed ? "patched" : "already patched"}: ${result.file}\n`);
  } catch (error) {
    process.stderr.write(`[ods-perplexica] ERROR: client citation patch failed: ${error.message}\n`);
    process.exitCode = 1;
  }
}

module.exports = { patchClientChunk, ORIGINAL, CALL, PRELUDE, KNOWN_CHUNK, KNOWN_SHA256 };
