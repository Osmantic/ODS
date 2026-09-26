import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, cp, link, lstat, mkdtemp, readdir, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { removeRetiredWorkspaceFiles } from "../scripts/lib/retired-workspace-text.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const script = join(root, "scripts/migrate-retired-workspace-text.mjs");
const posix = process.platform !== "win32";
const sha256 = value => createHash("sha256").update(value).digest("hex");

// Synthetic retired blocks: the file handling is tested without the retired wording.
const SECTION = "## Retired sample section\n\nSample rule.\n\n### Detail\n1. Detail.\n";
const ENTRY = "- 2026-01-01: Retired sample entry.";
const TABLE = {
  "AGENTS.md": { sections: [sha256(SECTION)], lines: [], emptyHeadings: [] },
  "MEMORY.md": { sections: [], lines: [sha256(ENTRY)], emptyHeadings: ["## Standing operating decisions"] },
};
const AGENTS_BEFORE = `# Contract\n\nIntro.\n\n${SECTION}\n## Memory\n\nShort.\n`;
const AGENTS_AFTER = "# Contract\n\nIntro.\n\n## Memory\n\nShort.\n";
const MEMORY_BEFORE = `# Durable memory\n\nFacts only.\n\n## Standing operating decisions\n\n${ENTRY}\n`;
const MEMORY_AFTER = "# Durable memory\n\nFacts only.\n";

async function workspace(files = { "AGENTS.md": AGENTS_BEFORE, "MEMORY.md": MEMORY_BEFORE }) {
  const temporary = await mkdtemp(join(tmpdir(), "pixel-retired-text-"));
  const path = join(temporary, "workspace");
  await cp(join(root, "workspace-template"), path, { recursive: true });
  for (const [name, value] of Object.entries(files)) {
    if (value === null) await rm(join(path, name));
    else await writeFile(join(path, name), value, { mode: 0o600 });
  }
  return { temporary, path };
}

const backups = async path => (await readdir(path)).filter(name => name.endsWith(".bak")).sort();
const statuses = results => Object.fromEntries(results.map(result => [result.name, result.status]));

test("removes exact retired blocks, keeps one backup, and is idempotent", async () => {
  const state = await workspace();
  try {
    if (posix) await chmod(join(state.path, "AGENTS.md"), 0o640);
    const first = await removeRetiredWorkspaceFiles(state.path, TABLE);
    assert.deepEqual(statuses(first), { "AGENTS.md": "removed", "MEMORY.md": "removed" });
    assert.equal(await readFile(join(state.path, "AGENTS.md"), "utf8"), AGENTS_AFTER);
    assert.equal(await readFile(join(state.path, "MEMORY.md"), "utf8"), MEMORY_AFTER);
    assert.deepEqual(await backups(state.path), [first[0].backup, first[1].backup].sort());
    assert.equal(first[0].backup, `AGENTS.md.before-retired-text-removal.${sha256(AGENTS_BEFORE).slice(0, 12)}.bak`);
    assert.equal(await readFile(join(state.path, first[0].backup), "utf8"), AGENTS_BEFORE);
    assert.equal(await readFile(join(state.path, first[1].backup), "utf8"), MEMORY_BEFORE);
    if (posix) {
      assert.equal((await stat(join(state.path, "AGENTS.md"))).mode & 0o777, 0o640, "file mode preserved");
      assert.equal((await stat(join(state.path, first[0].backup))).mode & 0o777, 0o600, "backup is private");
    }
    assert.deepEqual((await readdir(state.path)).filter(name => name.endsWith(".tmp")), []);
    const second = await removeRetiredWorkspaceFiles(state.path, TABLE);
    assert.deepEqual(statuses(second), { "AGENTS.md": "current", "MEMORY.md": "current" });
    assert.equal((await backups(state.path)).length, 2);
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("owner-edited blocks and missing files are left alone", async () => {
  const edited = AGENTS_BEFORE.replace("Sample rule.", "Sample rule, edited.");
  const state = await workspace({ "AGENTS.md": edited, "MEMORY.md": null });
  try {
    assert.deepEqual(statuses(await removeRetiredWorkspaceFiles(state.path, TABLE)),
      { "AGENTS.md": "current", "MEMORY.md": "missing" });
    assert.equal(await readFile(join(state.path, "AGENTS.md"), "utf8"), edited);
    assert.deepEqual(await backups(state.path), []);
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("CRLF, BOM and non-UTF-8 bytes outside the block stay byte-exact", async () => {
  const crlf = value => Buffer.from(value.replace(/\n/g, "\r\n"));
  const before = Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from([0xfe, 0x0d, 0x0a]), crlf(AGENTS_BEFORE)]);
  const after = Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from([0xfe, 0x0d, 0x0a]), crlf(AGENTS_AFTER)]);
  const state = await workspace({ "AGENTS.md": before, "MEMORY.md": crlf(MEMORY_BEFORE) });
  try {
    assert.deepEqual(statuses(await removeRetiredWorkspaceFiles(state.path, TABLE)),
      { "AGENTS.md": "removed", "MEMORY.md": "removed" });
    assert.deepEqual(await readFile(join(state.path, "AGENTS.md")), after);
    assert.deepEqual(await readFile(join(state.path, "MEMORY.md")), crlf(MEMORY_AFTER));
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("a rerun on restored original bytes reuses its backup; a conflicting backup blocks the change", async () => {
  const state = await workspace();
  try {
    await removeRetiredWorkspaceFiles(state.path, TABLE);
    await writeFile(join(state.path, "AGENTS.md"), AGENTS_BEFORE);
    assert.equal(statuses(await removeRetiredWorkspaceFiles(state.path, TABLE))["AGENTS.md"], "removed");
    assert.equal((await backups(state.path)).length, 2);

    await writeFile(join(state.path, "MEMORY.md"), MEMORY_BEFORE);
    const [memoryBackup] = (await backups(state.path)).filter(name => name.startsWith("MEMORY.md"));
    await writeFile(join(state.path, memoryBackup), "not the original\n");
    const result = await removeRetiredWorkspaceFiles(state.path, TABLE);
    assert.equal(statuses(result)["MEMORY.md"], "skipped-backup-conflict");
    assert.equal(await readFile(join(state.path, "MEMORY.md"), "utf8"), MEMORY_BEFORE);
    assert.equal(await readFile(join(state.path, memoryBackup), "utf8"), "not the original\n");
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("symlinks and hard links are never followed or rewritten", { skip: !posix && "POSIX links" }, async () => {
  const state = await workspace();
  try {
    const outside = join(state.temporary, "outside.md");
    await writeFile(outside, AGENTS_BEFORE);
    await rm(join(state.path, "AGENTS.md"));
    await symlink(outside, join(state.path, "AGENTS.md"));
    await link(join(state.path, "MEMORY.md"), join(state.temporary, "memory-link.md"));
    assert.deepEqual(statuses(await removeRetiredWorkspaceFiles(state.path, TABLE)),
      { "AGENTS.md": "skipped-unsafe", "MEMORY.md": "skipped-unsafe" });
    assert.equal(await readFile(outside, "utf8"), AGENTS_BEFORE);
    assert.equal(await readFile(join(state.path, "MEMORY.md"), "utf8"), MEMORY_BEFORE);
    assert.ok((await lstat(join(state.path, "AGENTS.md"))).isSymbolicLink());
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("one unreadable file is reported and does not stop the other", {
  skip: (!posix || process.getuid?.() === 0) && "needs an unprivileged POSIX user",
}, async () => {
  const state = await workspace();
  try {
    await chmod(join(state.path, "AGENTS.md"), 0o000);
    const results = await removeRetiredWorkspaceFiles(state.path, TABLE);
    assert.deepEqual(results[0], { name: "AGENTS.md", status: "failed", code: "EACCES" });
    assert.equal(results[1].status, "removed");
  } finally {
    await chmod(join(state.path, "AGENTS.md"), 0o600);
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("the command reports each file and leaves the current template unchanged", async () => {
  const state = await workspace({});
  try {
    const before = await readFile(join(state.path, "AGENTS.md"));
    assert.equal(execFileSync(process.execPath, [script, state.path], { encoding: "utf8" }),
      "Retired workspace text: AGENTS.md current; MEMORY.md current\n");
    assert.deepEqual(await readFile(join(state.path, "AGENTS.md")), before);
    assert.deepEqual(await backups(state.path), []);
    for (const args of [["relative/workspace"], [join(state.path, "AGENTS.md")], []]) {
      assert.notEqual(spawnSync(process.execPath, [script, ...args], { encoding: "utf8" }).status, 0, String(args));
    }
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});
