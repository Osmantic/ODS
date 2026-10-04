// Agent cron calls cannot create or edit command jobs, whatever the kind's case
// (GHSA-8xxh-v4vc-qvm4 against the pinned OpenClaw 2026.6.33).

import test from "node:test";
import assert from "node:assert/strict";
import {
  CRON_COMMAND_PAYLOAD_REASON,
  withCronCommandPayloadBlock,
} from "../plugin/cron-command-payload-guard.mjs";

const CRON = { agentId: "pixel", toolName: "cron" };
const BLOCK = { block: true, blockReason: CRON_COMMAND_PAYLOAD_REASON };

function check(params, context = CRON, guardResult = undefined) {
  return withCronCommandPayloadBlock(guardResult, { params }, context);
}

test("blocks a command payload kind in any case or padding", () => {
  for (const kind of ["command", "Command", "COMMAND", " cOmMaNd ", "command\n"]) {
    const params = {
      action: "add",
      job: { schedule: { kind: "every", everyMs: 60000 }, payload: { kind, argv: ["sh", "-c", "id"] } },
    };
    assert.deepEqual(check(params), BLOCK, kind);
  }
});

test("blocks flat, nested, update and wrapped forms", () => {
  const shapes = [
    [{ action: "add", kind: "Command", argv: ["id"], schedule: { kind: "at", at: "t" } }, CRON],
    [{ action: "add", payload: { kind: "Command", argv: ["id"] } }, CRON],
    [{ action: "update", jobId: "j", patch: { payload: { kind: "Command", argv: ["id"] } } }, CRON],
    [{ action: "Add", job: { payload: { kind: "Command" } } }, CRON],
    [{ id: "cron", args: { action: "add", job: { payload: { kind: "Command" } } } },
      { agentId: "pixel", toolName: "tool_call" }],
    [{ action: "add", job: { payload: { kind: "Command" } } },
      { agentId: "subagent", toolName: "openclaw:core:cron" }],
  ];
  for (const [params, context] of shapes) {
    assert.deepEqual(check(params, context), BLOCK, JSON.stringify(params));
  }
});

test("allows agent turns, system events and flat schedule kinds", () => {
  const allowed = [
    { action: "add", job: { schedule: { kind: "cron", expr: "0 9 * * *" }, payload: { kind: "agentTurn", message: "m" } } },
    { action: "add", job: { schedule: { kind: "at", at: "t" }, payload: { kind: "systemEvent", text: "t" } } },
    { action: "add", kind: "at", at: "t", message: "remind me" },
    { action: "add", kind: "every", everyMs: 60000, text: "tick" },
    { action: "update", jobId: "j", patch: { schedule: { kind: "cron", expr: "* * * * *" } } },
  ];
  for (const params of allowed) {
    assert.equal(check(params), undefined, JSON.stringify(params));
  }
});

test("other actions and other tools are untouched", () => {
  for (const action of ["list", "get", "status", "remove", "run", "runs", "wake"]) {
    assert.equal(check({ action, jobId: "j", payload: { kind: "Command" } }), undefined, action);
  }
  assert.equal(check({ action: "add", payload: { kind: "Command" } }, { toolName: "exec" }), undefined);
  assert.equal(check("not an object"), undefined);
});

test("keeps an earlier block and checks an earlier hook's rewritten params", () => {
  const earlier = { block: true, blockReason: "earlier" };
  assert.equal(check({ action: "add", payload: { kind: "Command" } }, CRON, earlier), earlier);

  const rewritten = { params: { action: "add", job: { payload: { kind: "Command" } } } };
  assert.deepEqual(check({ action: "add", job: { payload: { kind: "agentTurn" } } }, CRON, rewritten), BLOCK);

  const passthrough = { params: { action: "add", job: { payload: { kind: "agentTurn", message: "m" } } } };
  assert.equal(check({ action: "add" }, CRON, passthrough), passthrough);
});
