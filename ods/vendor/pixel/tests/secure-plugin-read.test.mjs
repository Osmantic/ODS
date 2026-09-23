import assert from "node:assert/strict";
import { mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { readBoundedText as readFrontier } from "../plugin-frontier/secure-read.js";
import { readBoundedText as readOperations } from "../plugin-ops/secure-read.js";

for (const [name, readBoundedText] of [["Frontier", readFrontier], ["Operations", readOperations]]) {
  test(`${name} plugin uses descriptor-bound bounded reads`, async (context) => {
    const root = await mkdtemp(join(tmpdir(), "pixel-plugin-read-"));
    try {
      const target = join(root, "target.json");
      const link = join(root, "link.json");
      await writeFile(target, '{"ok":true}\n');
      assert.equal(await readBoundedText(target, 64), '{"ok":true}\n');
      await assert.rejects(readBoundedText(target, 4), /invalid|large/);
      try { await symlink(target, link); }
      catch { context.skip("symlinks are unavailable on this host"); return; }
      await assert.rejects(readBoundedText(link, 64));
    } finally {
      await rm(root, { recursive: true, force: true });
    }
  });
}
