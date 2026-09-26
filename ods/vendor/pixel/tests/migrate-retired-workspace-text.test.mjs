import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { chmod, cp, link, lstat, mkdir, mkdtemp, readdir, readFile, rm, stat, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { removeRetiredWorkspaceFiles } from "../scripts/lib/retired-workspace-text.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const script = join(root, "scripts/migrate-retired-workspace-text.mjs");
const posix = process.platform !== "win32";
const sha256 = value => createHash("sha256").update(value).digest("hex");

// Synthetic retired blocks: the file handling is tested without the retired wording.
const HEADING = "## Retired sample section";
const SECTION = `${HEADING}\n\nSample rule.\n\n### Detail\n1. Detail.\n`;
const ENTRY = "- 2026-01-01: Retired sample entry.";
const TABLE = {
  "AGENTS.md": { sections: [sha256(SECTION)], headings: [sha256(HEADING)], lines: [], emptyHeadings: [] },
  "MEMORY.md": { sections: [], headings: [], lines: [sha256(ENTRY)], emptyHeadings: ["## Standing operating decisions"] },
};
const AGENTS_BEFORE = `# Contract\n\nIntro.\n\n${SECTION}\n## Memory\n\nShort.\n`;
const AGENTS_AFTER = "# Contract\n\nIntro.\n\n## Memory\n\nShort.\n";
const MEMORY_BEFORE = `# Durable memory\n\nFacts only.\n\n## Standing operating decisions\n\n${ENTRY}\n`;
const MEMORY_AFTER = "# Durable memory\n\nFacts only.\n";

// Backups go to a not-yet-created directory under a separate state root, as in apply.sh.
async function workspace(files = { "AGENTS.md": AGENTS_BEFORE, "MEMORY.md": MEMORY_BEFORE }) {
  const temporary = await mkdtemp(join(tmpdir(), "pixel-retired-text-"));
  const path = join(temporary, "workspace");
  await cp(join(root, "workspace-template"), path, { recursive: true });
  for (const [name, value] of Object.entries(files)) {
    if (value === null) await rm(join(path, name));
    else await writeFile(join(path, name), value, { mode: 0o600 });
  }
  return { temporary, path, backups: join(temporary, "state/backups/retired-workspace-text") };
}

const backups = async path => (await readdir(path)).filter(name => name.endsWith(".bak")).sort();
const statuses = results => Object.fromEntries(results.map(result => [result.name, result.status]));
const migrate = (state, table = TABLE) => removeRetiredWorkspaceFiles(state.path, state.backups, table);

test("removes exact retired blocks, keeps one private backup outside the workspace, and is idempotent", async () => {
  const state = await workspace();
  try {
    if (posix) await chmod(join(state.path, "AGENTS.md"), 0o640);
    const first = await migrate(state);
    assert.deepEqual(statuses(first), { "AGENTS.md": "removed", "MEMORY.md": "removed" });
    assert.equal(await readFile(join(state.path, "AGENTS.md"), "utf8"), AGENTS_AFTER);
    assert.equal(await readFile(join(state.path, "MEMORY.md"), "utf8"), MEMORY_AFTER);
    assert.equal(first[0].backup, join(state.backups, `AGENTS.md.before-retired-text-removal.${sha256(AGENTS_BEFORE).slice(0, 12)}.bak`));
    assert.equal(first[0].modified, 0);
    assert.deepEqual((await backups(state.backups)).map(name => join(state.backups, name)), [first[0].backup, first[1].backup].sort());
    assert.equal(await readFile(first[0].backup, "utf8"), AGENTS_BEFORE);
    assert.equal(await readFile(first[1].backup, "utf8"), MEMORY_BEFORE);
    assert.deepEqual(await backups(state.path), [], "no backup in the agent workspace");
    if (posix) {
      assert.equal((await stat(join(state.path, "AGENTS.md"))).mode & 0o777, 0o640, "file mode preserved");
      assert.equal((await stat(first[0].backup)).mode & 0o777, 0o600, "backup is private");
      assert.equal((await stat(state.backups)).mode & 0o777, 0o700, "backup directory is private");
    }
    for (const directory of [state.path, state.backups]) {
      assert.deepEqual((await readdir(directory)).filter(name => name.endsWith(".tmp")), []);
    }
    const second = await migrate(state);
    assert.deepEqual(statuses(second), { "AGENTS.md": "current", "MEMORY.md": "current" });
    assert.equal((await backups(state.backups)).length, 2);
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("an edited retired section is kept and reported; missing files are left alone", async () => {
  const edited = AGENTS_BEFORE.replace("Sample rule.", "Sample rule, edited.");
  const state = await workspace({ "AGENTS.md": edited, "MEMORY.md": null });
  try {
    assert.deepEqual(statuses(await migrate(state)), { "AGENTS.md": "modified-retired-section", "MEMORY.md": "missing" });
    assert.equal(await readFile(join(state.path, "AGENTS.md"), "utf8"), edited);
    assert.deepEqual(await backups(state.backups), []);

    // An exact copy is still removed when an edited copy is also present.
    await writeFile(join(state.path, "AGENTS.md"), AGENTS_BEFORE + "\n" + edited);
    const [agents] = await migrate(state);
    assert.equal(agents.status, "removed");
    assert.equal(agents.modified, 1);
    assert.equal(await readFile(join(state.path, "AGENTS.md"), "utf8"), AGENTS_AFTER + "\n" + edited);

    // A section whose heading was also edited is the owner's own section.
    await writeFile(join(state.path, "AGENTS.md"), edited.replace(HEADING, "## Owner section"));
    assert.equal(statuses(await migrate(state))["AGENTS.md"], "current");
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
    assert.deepEqual(statuses(await migrate(state)), { "AGENTS.md": "removed", "MEMORY.md": "removed" });
    assert.deepEqual(await readFile(join(state.path, "AGENTS.md")), after);
    assert.deepEqual(await readFile(join(state.path, "MEMORY.md")), crlf(MEMORY_AFTER));
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("a rerun on restored original bytes reuses its backup; a conflicting backup blocks the change", async () => {
  const state = await workspace();
  try {
    await migrate(state);
    await writeFile(join(state.path, "AGENTS.md"), AGENTS_BEFORE);
    assert.equal(statuses(await migrate(state))["AGENTS.md"], "removed");
    assert.equal((await backups(state.backups)).length, 2);

    await writeFile(join(state.path, "MEMORY.md"), MEMORY_BEFORE);
    const [memoryBackup] = (await backups(state.backups)).filter(name => name.startsWith("MEMORY.md"));
    await writeFile(join(state.backups, memoryBackup), "not the original\n");
    const result = await migrate(state);
    assert.equal(statuses(result)["MEMORY.md"], "skipped-backup-conflict");
    assert.equal(await readFile(join(state.path, "MEMORY.md"), "utf8"), MEMORY_BEFORE);
    assert.equal(await readFile(join(state.backups, memoryBackup), "utf8"), "not the original\n");
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("an interrupted backup write leaves no backup behind and a rerun completes", { skip: !posix && "POSIX shell limits" }, async () => {
  const memory = MEMORY_BEFORE.replace("Facts only.", `Facts only. ${"x".repeat(8192)}`);
  const state = await workspace({ "MEMORY.md": memory });
  try {
    // A 1 KiB file-size limit stops the backup write part way through (EFBIG).
    const lib = pathToFileURL(join(root, "scripts/lib/retired-workspace-text.mjs")).href;
    const run = `import { removeRetiredWorkspaceFiles } from ${JSON.stringify(lib)};
const results = await removeRetiredWorkspaceFiles(${JSON.stringify(state.path)}, ${JSON.stringify(state.backups)}, ${JSON.stringify(TABLE)});
process.stdout.write(JSON.stringify(results));`;
    const interrupted = spawnSync("bash", ["-c", 'ulimit -f 1; exec "$0" --input-type=module -e "$1"', process.execPath, run],
      { cwd: state.temporary, encoding: "utf8" });
    assert.equal(interrupted.status, 0, interrupted.stderr);
    assert.deepEqual(JSON.parse(interrupted.stdout)[1], { name: "MEMORY.md", status: "failed", code: "EFBIG" });
    assert.deepEqual(await readdir(state.backups), [], "no partial backup or staging file");
    assert.equal(await readFile(join(state.path, "MEMORY.md"), "utf8"), memory);

    const result = await migrate(state);
    assert.equal(statuses(result)["MEMORY.md"], "removed");
    assert.equal(await readFile(result[1].backup, "utf8"), memory);
    assert.equal(await readFile(join(state.path, "MEMORY.md"), "utf8"),
      memory.replace(`\n## Standing operating decisions\n\n${ENTRY}\n`, ""));
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("a complete backup still linked to its staging file is reused", { skip: !posix && "POSIX links" }, async () => {
  const state = await workspace();
  try {
    await mkdir(state.backups, { recursive: true });
    const name = `AGENTS.md.before-retired-text-removal.${sha256(AGENTS_BEFORE).slice(0, 12)}.bak`;
    await writeFile(join(state.backups, name), AGENTS_BEFORE, { mode: 0o600 });
    await link(join(state.backups, name), join(state.backups, `.${name}.interrupted.tmp`));
    assert.equal(statuses(await migrate(state))["AGENTS.md"], "removed");
    assert.equal(await readFile(join(state.path, "AGENTS.md"), "utf8"), AGENTS_AFTER);
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("backups are refused inside the workspace", async () => {
  const state = await workspace();
  try {
    for (const location of [state.path, join(state.path, "backups")]) {
      await assert.rejects(removeRetiredWorkspaceFiles(state.path, location, TABLE), /outside the Pixel workspace/);
    }
    assert.equal(await readFile(join(state.path, "AGENTS.md"), "utf8"), AGENTS_BEFORE);
    assert.equal(await readFile(join(state.path, "MEMORY.md"), "utf8"), MEMORY_BEFORE);
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
    assert.deepEqual(statuses(await migrate(state)), { "AGENTS.md": "skipped-unsafe", "MEMORY.md": "skipped-unsafe" });
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
    const results = await migrate(state);
    assert.deepEqual(results[0], { name: "AGENTS.md", status: "failed", code: "EACCES" });
    assert.equal(results[1].status, "removed");
  } finally {
    await chmod(join(state.path, "AGENTS.md"), 0o600);
    await rm(state.temporary, { recursive: true, force: true });
  }
});

test("the command reports each file, flags anything to review, and leaves the current template unchanged", async () => {
  const state = await workspace({});
  try {
    const before = await readFile(join(state.path, "AGENTS.md"));
    assert.equal(execFileSync(process.execPath, [script, state.path, state.backups], { encoding: "utf8" }),
      "Retired workspace text: AGENTS.md current\nRetired workspace text: MEMORY.md current\n");
    assert.deepEqual(await readFile(join(state.path, "AGENTS.md")), before);
    assert.deepEqual(await backups(state.backups), []);
    await rm(join(state.path, "MEMORY.md"));
    await rm(join(state.path, "AGENTS.md"));
    await mkdir(join(state.path, "AGENTS.md"));
    const skipped = spawnSync(process.execPath, [script, state.path, state.backups], { encoding: "utf8" });
    assert.equal(skipped.status, 1);
    assert.equal(skipped.stdout, "Retired workspace text: AGENTS.md skipped-unsafe; review\nRetired workspace text: MEMORY.md missing\n");
    for (const args of [["relative/workspace", state.backups], [join(state.path, "SOUL.md"), state.backups],
      [state.path], [state.path, "relative/backups"], [state.path, state.path], []]) {
      assert.notEqual(spawnSync(process.execPath, [script, ...args], { encoding: "utf8" }).status, 0, String(args));
    }
  } finally {
    await rm(state.temporary, { recursive: true, force: true });
  }
});
