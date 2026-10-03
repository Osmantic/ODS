import assert from "node:assert/strict";
import { mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { parseStrictJson, readBoundedRegularFile, readBoundedRegularText } from "./lib/secure-files.mjs";

test("descriptor-bound reads accept only bounded regular files", async () => {
  const root = await mkdtemp(join(tmpdir(), "pixel-secure-read-"));
  try {
    const path = join(root, "record.json");
    await writeFile(path, '{"ok":true}\n');
    assert.equal((await readBoundedRegularText(path, 64, "record")).text, '{"ok":true}\n');
    await assert.rejects(readBoundedRegularFile(path, 4, "record"), /size limit|bounded regular file/);
    await assert.rejects(readBoundedRegularFile(root, 64, "record"), /bounded regular file/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("strict JSON rejects duplicate decoded keys at every depth", () => {
  assert.deepEqual(parseStrictJson('{"outer":{"a":1},"items":[true,null,3]}', "fixture"), { outer: { a: 1 }, items: [true, null, 3] });
  assert.throws(() => parseStrictJson('{"a":1,"\\u0061":2}', "fixture"), /duplicate object key/u);
  assert.throws(() => parseStrictJson('{"outer":{"a":1,"a":2}}', "fixture"), /duplicate object key/u);
});

test("descriptor-bound reads refuse symlinks", async (context) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-secure-link-"));
  try {
    const target = join(root, "target.json");
    const link = join(root, "link.json");
    await writeFile(target, '{"secret":true}\n');
    try { await symlink(target, link); }
    catch { context.skip("symlinks are unavailable on this host"); return; }
    await assert.rejects(readBoundedRegularFile(link, 64, "record"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
