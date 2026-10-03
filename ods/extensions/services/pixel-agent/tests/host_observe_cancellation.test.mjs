import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import {
  createHostObserveTool, createHostCommandProposeTool, createExtensionReadTool,
} from "../plugin/host-observe.mjs";

const cases = [
  ["observe", createHostObserveTool, { actions: ["host.uptime"] }],
  ["command proposal", createHostCommandProposeTool, { command: "uptime" }],
  ["extension read", createExtensionReadTool, { action: "list" }],
];

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "pixel-broker-cancel-"));
  const requestDir = join(root, "requests"), resultDir = join(root, "results");
  await mkdir(requestDir); await mkdir(resultDir);
  return { root, requestDir, resultDir };
}

for (const [name, factory, params] of cases) {
  test(name + " does not submit an already-cancelled call", async () => {
    const paths = await fixture();
    try {
      const controller = new AbortController(); controller.abort();
      const tool = factory({ ...paths, timeoutMs: 30, pollIntervalMs: 5 });
      const result = await tool.execute("cancelled", params, controller.signal);
      assert.equal(result.isError, true);
      assert.equal(result.details.jobId, undefined);
      assert.deepEqual(await readdir(paths.requestDir), []);
    } finally { await rm(paths.root, { recursive: true, force: true }); }
  });

  test(name + " stops waiting while preserving the submitted job", async () => {
    const paths = await fixture();
    let pending;
    try {
      const controller = new AbortController();
      const tool = factory({ ...paths, timeoutMs: 2000, pollIntervalMs: 1000 });
      pending = tool.execute("running", params, controller.signal);
      let names = [];
      for (let i = 0; i < 100 && !names.length; i++) {
        names = (await readdir(paths.requestDir)).filter(name => name.endsWith(".json"));
        if (!names.length) await delay(5);
      }
      assert.equal(names.length, 1);
      const requestText = await readFile(join(paths.requestDir, names[0]), "utf8");
      const request = JSON.parse(requestText);
      const start = performance.now();
      controller.abort();
      const result = await pending;
      assert.ok(performance.now() - start < 750, "cancellation must interrupt the poll delay");
      assert.equal(result.details.jobId, request.jobId);
      assert.equal(result.details.waitCancelled, true);
      assert.equal(result.details.waitTimedOut, false);
      assert.notEqual(result.details.status, "cancelled");
      assert.match(result.details.next, /pixel_ops_job_get/);
      assert.match(result.details.next, /not cancel/i);
      assert.equal(await readFile(join(paths.requestDir, names[0]), "utf8"), requestText);
      assert.deepEqual(await readdir(paths.resultDir), []);
    } finally {
      await pending;
      await rm(paths.root, { recursive: true, force: true });
    }
  });
}
