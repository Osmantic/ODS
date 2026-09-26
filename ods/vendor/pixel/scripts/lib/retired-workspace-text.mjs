// Remove text that an earlier workspace template shipped and a later template retired.
//
// Only exact shipped copies are removed. Each retired block is identified by the
// SHA-256 of its canonical text (LF line endings, one final newline), so the
// retired wording is not republished here. Matching ignores only CRLF versus LF
// line endings and the number of empty lines that trail a section. Everything
// else, including owner edits anywhere in the file, stays byte-for-byte intact,
// and a block that differs by a single byte is left alone.
import { createHash, randomUUID } from "node:crypto";
import { chmod, lstat, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { join } from "node:path";

// Shipped in workspace-template/AGENTS.md and MEMORY.md from ODS #6156 (2026-09-22)
// until this template retired them; every ODS Pixel bundle in that range carried the
// same bytes. AGENTS.md: the owner-specific operating-contract section.
// MEMORY.md: the matching dated standing-decision entry.
export const RETIRED_WORKSPACE_TEXT = Object.freeze({
  "AGENTS.md": Object.freeze({
    sections: Object.freeze(["a79b56d5d9f5d76a1bb643bc53d37b97104ccc628182ec013edde8ed865b683f"]),
    lines: Object.freeze([]),
    emptyHeadings: Object.freeze([]),
  }),
  "MEMORY.md": Object.freeze({
    sections: Object.freeze([]),
    lines: Object.freeze(["73195176060dcedc9a4ac7f0abf1590f3c275a9c743aa7bf562303207819ccf3"]),
    // Removed together with a retired entry only when nothing else remains under it.
    emptyHeadings: Object.freeze(["## Standing operating decisions"]),
  }),
});

const BOM = Buffer.from([0xef, 0xbb, 0xbf]);
const NEWLINE = Buffer.from("\n");

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function splitLines(bytes, offset) {
  const lines = [];
  let start = offset;
  while (start < bytes.length) {
    const newline = bytes.indexOf(0x0a, start);
    const next = newline < 0 ? bytes.length : newline + 1;
    let end = newline < 0 ? bytes.length : newline;
    if (end > start && bytes[end - 1] === 0x0d) end -= 1;
    lines.push({ start, next, text: bytes.subarray(start, end) });
    start = next;
  }
  return lines;
}

// Level-one and level-two headings bound a section; "### " subsections do not.
function isHeading(line) {
  const text = line.text;
  return text[0] === 0x23 && (text[1] === 0x20 || (text[1] === 0x23 && text[2] === 0x20));
}

function isBlank(line) {
  return line.text.every(byte => byte === 0x20 || byte === 0x09);
}

function nextHeading(lines, after) {
  for (let index = after + 1; index < lines.length; index += 1) if (isHeading(lines[index])) return index;
  return lines.length;
}

function canonicalSection(lines, from, to) {
  let last = to;
  while (last > from + 1 && lines[last - 1].text.length === 0) last -= 1;
  const parts = [];
  for (let index = from; index < last; index += 1) parts.push(lines[index].text, NEWLINE);
  return Buffer.concat(parts);
}

/**
 * Remove every exact retired block from one workspace file's bytes.
 * Returns `{ bytes, removed }`; `bytes` is the input Buffer itself when nothing matched.
 */
export function removeRetiredWorkspaceText(name, input, table = RETIRED_WORKSPACE_TEXT) {
  const bytes = Buffer.isBuffer(input) ? input : Buffer.from(input);
  const retired = Object.hasOwn(table, name) ? table[name] : null;
  if (!retired) return { bytes, removed: 0 };
  const bom = bytes.subarray(0, 3).equals(BOM);
  const lines = splitLines(bytes, bom ? 3 : 0);
  const drop = new Array(lines.length).fill(false);
  let removed = 0;

  if (retired.sections.length > 0) {
    for (let index = 0; index < lines.length; index += 1) {
      if (!isHeading(lines[index])) continue;
      const end = nextHeading(lines, index);
      if (retired.sections.includes(sha256(canonicalSection(lines, index, end)))) {
        drop.fill(true, index, end);
        removed += 1;
      }
      index = end - 1;
    }
  }

  const removedLines = [];
  if (retired.lines.length > 0) {
    for (let index = 0; index < lines.length; index += 1) {
      if (!drop[index] && retired.lines.includes(sha256(lines[index].text))) {
        drop[index] = true;
        removedLines.push(index);
        removed += 1;
      }
    }
  }
  const emptyHeadings = retired.emptyHeadings.map(heading => Buffer.from(heading, "utf8"));
  for (const index of removedLines) {
    let heading = index - 1;
    while (heading >= 0 && !isHeading(lines[heading])) heading -= 1;
    if (heading < 0 || drop[heading] || !emptyHeadings.some(value => value.equals(lines[heading].text))) continue;
    const end = nextHeading(lines, heading);
    let empty = true;
    for (let line = heading + 1; line < end; line += 1) if (!drop[line] && !isBlank(lines[line])) empty = false;
    if (empty) drop.fill(true, heading, end);
  }

  if (removed === 0) return { bytes, removed: 0 };
  // A block removed from the end of the file must not leave trailing blank lines behind.
  if (drop[lines.length - 1]) {
    for (let index = lines.length - 1; index >= 0; index -= 1) {
      if (drop[index]) continue;
      if (!isBlank(lines[index])) break;
      drop[index] = true;
    }
  }
  const parts = bom ? [BOM] : [];
  for (let index = 0; index < lines.length; index += 1) {
    if (!drop[index]) parts.push(bytes.subarray(lines[index].start, lines[index].next));
  }
  return { bytes: Buffer.concat(parts), removed };
}

const MAX_FILE_BYTES = 8 * 1024 * 1024;

async function removeFromFile(workspace, name, table) {
  const path = join(workspace, name);
  let entry;
  try {
    entry = await lstat(path);
  } catch (error) {
    if (error?.code === "ENOENT") return { status: "missing" };
    throw error;
  }
  // Symlinks, hard links and special files may point outside the workspace.
  if (!entry.isFile() || entry.nlink !== 1 || entry.size > MAX_FILE_BYTES) return { status: "skipped-unsafe" };
  const original = await readFile(path);
  const { bytes, removed } = removeRetiredWorkspaceText(name, original, table);
  if (removed === 0) return { status: "current" };

  // One backup per distinct original, never overwritten; a rerun on the same
  // original (for example after an apply rollback) reuses it.
  const backupName = `${name}.before-retired-text-removal.${sha256(original).slice(0, 12)}.bak`;
  const backup = join(workspace, backupName);
  let createdBackup = false;
  try {
    await writeFile(backup, original, { flag: "wx", mode: 0o600 });
    createdBackup = true;
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
    const existing = await lstat(backup);
    if (!existing.isFile() || existing.nlink !== 1 || !(await readFile(backup)).equals(original)) {
      return { status: "skipped-backup-conflict", backup: backupName };
    }
  }
  const temporary = join(workspace, `.${name}.${randomUUID()}.tmp`);
  try {
    await writeFile(temporary, bytes, { flag: "wx", mode: entry.mode & 0o777 });
    await chmod(temporary, entry.mode & 0o777);
    // Do not replace a concurrent owner or agent write made after the read above.
    if (!(await readFile(path)).equals(original)) {
      if (createdBackup) await unlink(backup);
      return { status: "skipped-changed-during-removal" };
    }
    await rename(temporary, path);
  } finally {
    await unlink(temporary).catch(error => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
  return { status: "removed", removed, backup: backupName };
}

/**
 * Remove retired shipped text from the workspace files named in `table`.
 * Returns one `{ name, status, ... }` record per file; a per-file failure is
 * reported as `status: "failed"` with its error code and does not stop the others.
 */
export async function removeRetiredWorkspaceFiles(workspace, table = RETIRED_WORKSPACE_TEXT) {
  const directory = await lstat(workspace);
  if (!directory.isDirectory() || directory.isSymbolicLink()) throw new Error("Pixel workspace is not a directory");
  const results = [];
  for (const name of Object.keys(table)) {
    try {
      results.push({ name, ...(await removeFromFile(workspace, name, table)) });
    } catch (error) {
      results.push({ name, status: "failed", code: typeof error?.code === "string" ? error.code : "error" });
    }
  }
  return results;
}
