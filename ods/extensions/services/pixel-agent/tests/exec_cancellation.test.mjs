import test from "node:test";
import assert from "node:assert/strict";
import { copyFileSync, chmodSync, mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { execFile, spawn } from "node:child_process";
import { promisify } from "node:util";
import { createExecCancellationControl } from "../plugin/tool-loop-guard.mjs";

const run = promisify(execFile);
const linux = process.platform === "linux";
function fixture() {
  // Retain separate evidence for every run, including unusual valid host paths.
  const parent = mkdtempSync(path.join(tmpdir(), "pixel-cancel-"));
  const root = path.join(parent, "owner control's directory");
  mkdirSync(root, { mode: 0o700 });
  const wrapper = path.join(root, "cancellable-exec.sh");
  copyFileSync(new URL("../host/cancellable-exec.sh", import.meta.url), wrapper);
  chmodSync(wrapper, 0o500);
  return { root, wrapper };
}

test("execution host only accepts the two explicit modes", () => {
  for (const executionHost of ["constructor", "__proto__", "toString", "full", "", null, true, {}]) {
    assert.throws(() => createExecCancellationControl({ executionHost }), /invalid Pixel execution control host mode/);
  }
});

test("sandbox preparation validates the host file and retains its mount path", { skip: !linux }, () => {
  const { root, wrapper } = fixture();
  const control = createExecCancellationControl({ root });
  assert.match(control.prepare("sandbox", "printf ready"), /^\/run\/pixel-ods-control\/cancellable-exec\.sh [0-9a-f]{64} /);
  chmodSync(wrapper, 0o700);
  assert.throws(() => control.prepare("sandbox", "printf ready"), /unsafe Pixel execution control root/);
});

test("gateway execution handles spaces and apostrophes and preserves command exits", { skip: !linux }, async () => {
  const { root } = fixture();
  const control = createExecCancellationControl({ root, executionHost: "gateway" });
  const result = await run("sh", ["-c", control.prepare("normal", "printf 'actual gateway output'")], { timeout: 5000 });
  assert.equal(result.stdout, "actual gateway output");
  await assert.rejects(run("sh", ["-c", control.prepare("nonzero", "exit 42")], { timeout: 5000 }), { code: 42 });
});

test("gateway cancellation stops the actual child process group", { skip: !linux, timeout: 10000 }, async () => {
  const { root } = fixture();
  const control = createExecCancellationControl({ root, executionHost: "gateway" });
  const command = "sleep 30 & child=$!; printf '%s\\n' \"$$ $child\"; wait \"$child\"";
  const child = spawn("sh", ["-c", control.prepare("cancel-tree", command)], { timeout: 6000 });
  const closed = new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", (code, signal) => resolve({ code, signal }));
  });
  let output = "";
  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => { output += chunk; });
  try {
    const deadline = Date.now() + 3000;
    while (!/^\d+ \d+\n/.test(output) && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
    assert.match(output, /^\d+ \d+\n/, "the real shell and its child started");
    const pids = output.trim().split(" ").map(Number);
    assert.equal(control.signal("cancel-tree"), true);
    assert.deepEqual(await closed, { code: 130, signal: null });
    for (const pid of pids) {
      try {
        const stat = readFileSync(`/proc/${pid}/stat`, "utf8");
        assert.match(stat.slice(stat.lastIndexOf(")") + 2), /^[ZX] /, `process ${pid} must no longer run`);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
    control.clear("cancel-tree");
  } finally {
    if (child.exitCode === null && child.signalCode === null) child.kill("SIGTERM");
    await closed;
  }
});
