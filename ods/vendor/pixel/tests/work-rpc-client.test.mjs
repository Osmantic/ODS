import assert from "node:assert/strict";
import { resolve } from "node:path";
import test from "node:test";

import { RpcClientError, runRpcSession } from "../deploy/work-runner/rpc-client.mjs";
import { RpcObservationLoopGuard, validateRpcLoopGuardReceipt } from "../deploy/work-runner/rpc-loop-guard.mjs";

const fake = resolve(import.meta.dirname, "fixtures/work/fake-omp-rpc.mjs");
const transport = Object.freeze({ maxFrameBytes: 1024, maxReassembledBytes: 4096, maxStreamBytes: 16384 });

function environment() {
  const value = {};
  for (const key of ["PATH", "SystemRoot", "WINDIR"]) if (process.env[key]) value[key] = process.env[key];
  return value;
}

function options(scenario, overrides = {}) {
  return {
    command: process.execPath,
    args: [fake, scenario, String(transport.maxFrameBytes), String(transport.maxReassembledBytes)],
    cwd: import.meta.dirname,
    env: environment(),
    prompt: "Inspect the immutable fixture.",
    allowedTools: ["read", "grep", "glob"],
    maxRuntimeMs: 3000,
    maxToolCalls: 10,
    maxResultBytes: 4096,
    maxStderrBytes: 4096,
    transport,
    ...overrides,
  };
}

test("bounded RPC client negotiates v2, proves tools, and returns final text", async () => {
  for (const scenario of ["success", "events-first", "builtin-widget", "builtin-catalog", "valid-update"]) {
    const result = await runRpcSession(options(scenario));
    assert.equal(result.text, "The fixture invariant is 42.");
    assert.equal(result.protocolVersion, 2);
    assert.equal(result.toolCalls, 1);
    assert.deepEqual(result.observedTools, ["read"]);
    assert.match(result.stderrSha256, /^[a-f0-9]{64}$/);
    assert.deepEqual(validateRpcLoopGuardReceipt(result.loopGuard, 1), []);
  }
});

test("bounded RPC client binds a leased first-turn tool through OMP's provider-level force command", async () => {
  const result = await runRpcSession(options("expect-forced-read", { requiredInitialTool: "read" }));
  assert.equal(result.text, "The fixture invariant is 42.");
  assert.deepEqual(result.observedTools, ["read"]);
  assert.throws(() => runRpcSession(options("success", { requiredInitialTool: "bash" })), /invalid or unleased/u);
  assert.throws(() => runRpcSession(options("success", { requiredInitialTool: "read\n\/force:bash" })), /invalid or unleased/u);
});

test("bounded RPC client permits an empty final report only when its caller opts in", async () => {
  await assert.rejects(runRpcSession(options("empty-result")), /RPC result is empty/u);
  await assert.rejects(runRpcSession(options("missing-result")), /RPC result is invalid/u);
  const result = await runRpcSession(options("empty-result", { allowEmptyResult: true }));
  assert.equal(result.text, "");
  const missing = await runRpcSession(options("missing-result", { allowEmptyResult: true }));
  assert.equal(missing.text, "");
  assert.throws(() => runRpcSession(options("success", { allowEmptyResult: "yes" })), /allowEmptyResult is invalid/u);
});

test("bounded RPC client recovers a deferred leased tool call without executing model-supplied arguments", async () => {
  const result = await runRpcSession(options("deferred-tool-recovery", {
    requiredInitialTool: "read",
    maxDeferredToolRecoveries: 2,
  }));
  assert.equal(result.text, "The fixture invariant is 42.");
  assert.equal(result.deferredToolRecoveries, 1);
  assert.equal(result.toolCalls, 2);
  assert.deepEqual(result.observedTools, ["grep", "read"]);
});

test("bounded RPC client fails closed at its deferred tool-call recovery ceiling", async () => {
  await assert.rejects(runRpcSession(options("deferred-tool-ceiling", {
    requiredInitialTool: "read",
    maxDeferredToolRecoveries: 1,
  })), /recovery ceiling/u);
  assert.throws(() => runRpcSession(options("success", { maxDeferredToolRecoveries: 10 })), /maxDeferredToolRecoveries is invalid/u);
  assert.throws(() => runRpcSession(options("success", { maxDeferredToolRecoveries: 10, maxToolCalls: 10 })), /maxDeferredToolRecoveries is invalid/u);
});

test("bounded RPC client accepts OMP task progress snapshots before and after async task completion", async () => {
  const result = await runRpcSession(options("task-async-update", { allowedTools: ["read", "grep", "glob", "task"] }));
  assert.equal(result.toolCalls, 1);
  assert.deepEqual(result.observedTools, ["task"]);
  assert.deepEqual(validateRpcLoopGuardReceipt(result.loopGuard, 1), []);
  await assert.rejects(
    runRpcSession(options("task-post-end-drift", { allowedTools: ["read", "grep", "glob", "task"] })),
    /completed-task update arguments differ/u,
  );
});

test("bounded RPC client waits across a declared nonterminal OMP continuation", async () => {
  const result = await runRpcSession(options("nonterminal-agent-end"));
  assert.equal(result.text, "The fixture invariant is 42.");
  assert.equal(result.toolCalls, 2);
  assert.deepEqual(result.observedTools, ["read"]);
});

test("RPC observation guard stops only outcome-equivalent loops and emits content-free evidence", async () => {
  for (const [scenario, reason, completed] of [
    ["repeat-success", "repeated-equivalent-outcome", 3],
    ["repeat-error", "repeated-equivalent-failure", 2],
    ["oscillation", "equivalent-outcome-oscillation", 6],
  ]) {
    await assert.rejects(runRpcSession(options(scenario)), (error) => {
      assert.ok(error instanceof RpcClientError);
      assert.equal(error.loopGuard?.status, "stopped");
      assert.equal(error.loopGuard?.reason, reason);
      assert.equal(error.loopGuard?.completedToolCalls, completed);
      assert.match(error.loopGuard?.eventHeadSha256, /^[a-f0-9]{64}$/);
      assert.equal(JSON.stringify(error.loopGuard).includes("sensitive"), false);
      return true;
    });
  }
});

test("RPC observation guard treats changed output as progress", async () => {
  const result = await runRpcSession(options("changed-result"));
  assert.equal(result.toolCalls, 3);
  assert.deepEqual(validateRpcLoopGuardReceipt(result.loopGuard, 3), []);
});

test("RPC observation fingerprints are canonical and bounded", () => {
  const fingerprintKey = Buffer.alloc(32, 7);
  const left = new RpcObservationLoopGuard({ fingerprintKey });
  const right = new RpcObservationLoopGuard({ fingerprintKey });
  left.start({ toolCallId: "left", toolName: "read", args: { path: "a", line: 1 } });
  right.start({ toolCallId: "right", toolName: "read", args: { line: 1, path: "a" } });
  left.end({ toolCallId: "left", toolName: "read", result: { content: [], details: { z: 2, a: 1 } } });
  right.end({ toolCallId: "right", toolName: "read", result: { details: { a: 1, z: 2 }, content: [] } });
  assert.equal(left.receipt().eventHeadSha256, right.receipt().eventHeadSha256);
  const independentlyKeyed = new RpcObservationLoopGuard();
  independentlyKeyed.start({ toolCallId: "independent", toolName: "read", args: { path: "a", line: 1 } });
  independentlyKeyed.end({ toolCallId: "independent", toolName: "read", result: { content: [], details: { z: 2, a: 1 } } });
  assert.notEqual(left.receipt().eventHeadSha256, independentlyKeyed.receipt().eventHeadSha256);

  const deep = new RpcObservationLoopGuard({ maxJsonDepth: 2 });
  assert.throws(() => deep.start({ toolCallId: "deep", toolName: "read", args: { a: { b: { c: true } } } }), /depth ceiling/);
  const wide = new RpcObservationLoopGuard({ maxJsonNodes: 16 });
  assert.throws(() => wide.start({ toolCallId: "wide", toolName: "read", args: Array.from({ length: 17 }, () => null) }), /node ceiling/);
});

test("bounded RPC client rejects authority expansion and malformed output", async () => {
  for (const [scenario, pattern] of [
    ["unexpected-tool", /unleased/],
    ["host-tool", /forbidden/],
    ["widget-content", /forbidden/],
    ["widget-substitution", /forbidden/],
    ["external-catalog", /catalog widened/],
    ["malformed", /framing/],
    ["oversize", /framing/],
    ["truncated", /framing/],
    ["bad-ready", /ready contract/],
    ["mismatched-end", /identity differs/],
    ["reused-id", /start identity/],
    ["malformed-agent-end", /malformed completion marker/],
    ["unknown-update", /update identity/],
    ["argument-drift", /arguments differ/],
    ["post-end-tool", /after agent completion/],
  ]) await assert.rejects(runRpcSession(options(scenario)), pattern);
});

test("bounded RPC client kills a non-progressing executor at the deadline", async () => {
  const started = Date.now();
  await assert.rejects(runRpcSession(options("hang", { maxRuntimeMs: 100 })), RpcClientError);
  assert.ok(Date.now() - started < 2000);
});

test("RPC failure waits for bounded asynchronous outer cleanup", async () => {
  let cleaned = false;
  const started = Date.now();
  await assert.rejects(runRpcSession(options("hang", {
    maxRuntimeMs: 50,
    terminationTimeoutMs: 1000,
    onTerminate: async () => {
      await new Promise((resolve) => setTimeout(resolve, 75));
      cleaned = true;
    },
  })), RpcClientError);
  assert.equal(cleaned, true);
  assert.ok(Date.now() - started >= 100);
});

test("RPC failure cannot hang forever in outer cleanup", async () => {
  const started = Date.now();
  await assert.rejects(runRpcSession(options("hang", {
    maxRuntimeMs: 50,
    terminationTimeoutMs: 75,
    onTerminate: () => new Promise(() => {}),
  })), RpcClientError);
  assert.ok(Date.now() - started < 1000);
});
