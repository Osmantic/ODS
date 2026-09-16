// Installed-package qualification. No upstream implementation is vendored.
// Set PIXEL_OPENCLAW_RUNTIME and run node --experimental-vm-modules --test.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import vm from "node:vm";

const runtime = process.env.PIXEL_OPENCLAW_RUNTIME;
const manifest = JSON.parse(readFileSync(new URL("../host/openclaw-compaction-export.json", import.meta.url)));
const sha = (s) => createHash("sha256").update(s).digest("hex");

test("installed compaction export and persistent-count semantics", { skip: !runtime }, async (t) => {
  const pkg = JSON.parse(readFileSync(path.join(runtime, "package.json")));
  assert.equal(pkg.name, "openclaw");
  assert.equal(pkg.version, manifest.version);
  const [chunkName, chunkHash] = Object.entries(manifest.reviewedDependencies)[0];
  const chunk = readFileSync(path.join(runtime, "dist", chunkName), "utf8");
  assert.equal(sha(chunk), chunkHash);
  const current = readFileSync(path.join(runtime, "dist", "embedded-agent-subscribe.handlers.compaction.runtime.js"), "utf8");
  assert.ok([manifest.sourceSha256, manifest.patchedSha256].includes(sha(current)));
  const [[before, after]] = manifest.replacements;
  assert.equal(sha(before), manifest.sourceSha256);
  assert.equal(sha(after), manifest.patchedSha256);

  async function load(facade, entry) {
    const context = vm.createContext({});
    const writes = [];
    let stored = { ...entry };
    const store = new vm.SyntheticModule(["v"], function () {
      this.setExport("v", async ({ storePath, sessionKey, update }) => {
        assert.equal(storePath, "isolated-store");
        assert.equal(sessionKey, "canary-session");
        const patch = await update(stored);
        if (patch) { writes.push({ ...patch }); stored = { ...stored, ...patch }; }
        return stored;
      });
    }, { context });
    const paths = new vm.SyntheticModule(["d"], function () {
      this.setExport("d", (configStore, { agentId }) => {
        assert.equal(configStore, "isolated-config");
        assert.equal(agentId, "pixel");
        return "isolated-store";
      });
    }, { context });
    const sessions = new vm.SyntheticModule([], function () {}, { context });
    const implementation = new vm.SourceTextModule(chunk, { context });
    await implementation.link((specifier) => {
      const dependency = { "./store-wRjrToJU.js": store, "./paths-CHZBIGhF.js": paths, "./sessions-B-ODmdLS.js": sessions }[specifier];
      assert.ok(dependency, `unexpected dependency: ${specifier}`);
      return dependency;
    });
    const wrapper = new vm.SourceTextModule(facade, { context });
    await wrapper.link((specifier) => {
      assert.equal(specifier, `./${chunkName}`);
      return implementation;
    });
    await wrapper.evaluate();
    return { reconcile: wrapper.namespace.default, writes, stored: () => stored };
  }

  await t.test("baseline loses default and the repaired facade exposes it", async () => {
    assert.equal((await load(before, {})).reconcile, undefined);
    assert.equal(typeof (await load(after, {})).reconcile, "function");
  });
  const params = { sessionKey: "canary-session", agentId: "pixel", configStore: "isolated-config" };
  await t.test("count advances without reducing time or replacing other fields", async () => {
    const original = { compactionCount: 3, updatedAt: 900, sessionId: "preserve-me", model: "unchanged" };
    const result = await load(after, original);
    assert.equal(await result.reconcile({ ...params, observedCompactionCount: 5, now: 800 }), 5);
    assert.deepEqual(result.stored(), { ...original, compactionCount: 5 });
    assert.equal(result.writes.length, 1);
    assert.equal(await result.reconcile({ ...params, observedCompactionCount: 6, now: 1000 }), 6);
    assert.deepEqual(result.stored(), { ...original, compactionCount: 6, updatedAt: 1000 });
  });
  await t.test("replayed or lower counts do not rewrite the session", async () => {
    const original = { compactionCount: 4, updatedAt: 900, sessionId: "preserve-me" };
    const result = await load(after, original);
    for (const count of [4, 2, 4]) {
      assert.equal(await result.reconcile({ ...params, observedCompactionCount: count, now: 1000 }), 4);
    }
    assert.equal(result.writes.length, 0);
    assert.deepEqual(result.stored(), original);
  });
  await t.test("missing count initializes; invalid requests do not write", async () => {
    const result = await load(after, { sessionId: "preserve-me" });
    for (const extra of [{ sessionKey: null, observedCompactionCount: 1 }, { observedCompactionCount: 0 }]) {
      assert.equal(await result.reconcile({ ...params, ...extra }), undefined);
    }
    assert.equal(result.writes.length, 0);
    assert.equal(await result.reconcile({ ...params, observedCompactionCount: 1, now: 100 }), 1);
    assert.deepEqual(result.stored(), { sessionId: "preserve-me", compactionCount: 1, updatedAt: 100 });
  });
});
