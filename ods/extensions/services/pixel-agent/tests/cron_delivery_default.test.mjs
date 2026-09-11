// Focused tests for the Pixel-only cron delivery default helper.
// Each distinct behavior is asserted once; no repeated coverage padding.

import test from "node:test";
import assert from "node:assert/strict";
import { withPixelCronDeliveryDefault } from "../plugin/cron-delivery-default.mjs";

const PIXEL = "pixel";

function apply(event, context, guardResult) {
  return withPixelCronDeliveryDefault(guardResult, event, context, PIXEL);
}

function addJob(overrides = {}, host = {}) {
  return {
    action: "add",
    job: { name: "n", schedule: { kind: "at", at: "t" }, ...overrides },
    ...host,
  };
}

const CTX = { agentId: PIXEL, toolName: "cron" };

test("adds delivery none to canonical isolated agentTurn add (nested job)", () => {
  const params = addJob({
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
  });
  const result = apply({ params }, CTX);
  assert.equal(result.params.job.delivery.mode, "none");
  assert.notEqual(result.params, params);
});

// Flat + nested variants covered separately below.

test("adds delivery none to flat cron fields", () => {
  const result = apply(
    {
      params: {
        action: "add",
        name: "n",
        schedule: { kind: "at", at: "t" },
        sessionTarget: "isolated",
        payload: { kind: "agentTurn", message: "m" },
      },
    },
    CTX,
  );
  assert.equal(result.params.delivery.mode, "none");
});

test("wrapped tool_call with cron id and args object is defaulted", () => {
  const args = addJob({
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
  });
  const result = apply({ params: { id: "cron", args } }, {
    agentId: PIXEL,
    toolName: "tool_call",
  });
  assert.equal(result.params.args.job.delivery.mode, "none");
  assert.equal(result.params.args.action, "add");
});

test("wrapped tool_call with openclaw:core:cron id is defaulted", () => {
  const result = apply(
    {
      params: {
        id: "openclaw:core:cron",
        args: addJob({
          sessionTarget: "isolated",
          payload: { kind: "agentTurn", message: "m" },
        }),
      },
    },
    { agentId: PIXEL, toolName: "tool_call" },
  );
  assert.equal(result.params.args.job.delivery.mode, "none");
});

// Non-cron wrapper ids are covered separately.

// Canonical nested-job no-op covered by identity test below.

test("no-op returns guard result with unchanged object identity", () => {
  const guard = { params: { action: "list" } };
  const result = apply({ params: guard.params }, CTX, guard);
  assert.equal(result, guard);
});

test("no-op without guard result returns undefined identity-free", () => {
  assert.equal(apply({ params: { action: "list" } }, CTX), undefined);
});

test("frozen input is never mutated", () => {
  const params = Object.freeze(
    addJob({
      sessionTarget: "isolated",
      payload: { kind: "agentTurn", message: "m" },
    }),
  );
  assert.doesNotThrow(() => apply({ params }, CTX));
  const result = apply({ params }, CTX);
  assert.equal(result.params.job.delivery.mode, "none");
  assert.equal(params.delivery, undefined);
  assert.equal(params.job.delivery, undefined);
});

test("explicit delivery mode announce is preserved untouched", () => {
  const params = addJob(
    {
      sessionTarget: "isolated",
      payload: { kind: "agentTurn", message: "m" },
      delivery: { mode: "announce", channel: "x" },
    },
  );
  const guard = { params };
  const result = apply({ params }, CTX, guard);
  assert.equal(result, guard);
});

test("explicit delivery webhook preserved on flat form", () => {
  const params = {
    action: "add",
    name: "n",
    schedule: { kind: "at", at: "t" },
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
    delivery: { mode: "webhook" },
  };
  const guard = { params };
  const result = apply({ params }, CTX, guard);
  assert.equal(result, guard);
});

test("explicit null delivery left for original validator (no default applied)", () => {
  const params = addJob(
    {
      sessionTarget: "isolated",
      payload: { kind: "agentTurn", message: "m" },
      delivery: null,
    },
  );
  const guard = { params };
  assert.equal(apply({ params }, CTX, guard), guard);
});

test("non-Pixel context agentId is never modified", () => {
  const params = addJob({
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
  });
  const result = apply({ params }, { agentId: "other", toolName: "cron" });
  assert.equal(result, undefined);
});

test("non-isolated sessionTarget untouched", () => {
  const params = addJob({
    sessionTarget: "main",
    payload: { kind: "systemEvent", text: "t" },
  });
  assert.equal(apply({ params }, CTX), undefined);
});

test("omitted sessionTarget with explicit agentTurn payload defaults (canonical SDK default shape)", () => {
  const params = addJob({ payload: { kind: "agentTurn", message: "m" } });
  const result = apply({ params }, CTX);
  assert.equal(result.params.job.delivery.mode, "none");
});

test("omitted sessionTarget with systemEvent payload untouched", () => {
  const params = addJob({ payload: { kind: "systemEvent", text: "t" } });
  assert.equal(apply({ params }, CTX), undefined);
});

test("non-agentTurn payload untouched", () => {
  const params = addJob({
    sessionTarget: "isolated",
    payload: { kind: "systemEvent", text: "t" },
  });
  assert.equal(apply({ params }, CTX), undefined);
});

test("job-level agentId different from Pixel blocks the default", () => {
  const params = addJob(
    {
      sessionTarget: "isolated",
      payload: { kind: "agentTurn", message: "m" },
      agentId: "other",
    },
  );
  assert.equal(apply({ params }, CTX), undefined);
});

test("flat agentId different from Pixel blocks the default", () => {
  const params = {
    action: "add",
    name: "n",
    schedule: { kind: "at", at: "t" },
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
    agentId: "main",
  };
  assert.equal(apply({ params }, CTX), undefined);
});

test("nested job and flat job-related conflict leaves params untouched", () => {
  const params = addJob(
    {
      sessionTarget: "isolated",
      payload: { kind: "agentTurn", message: "m" },
    },
    { sessionTarget: "main" },
  );
  assert.equal(apply({ params }, CTX), undefined);
});

test("matching flat+nested values are not treated as conflict and default applies", () => {
  const shared = {
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
  };
  const params = addJob(shared, { ...shared });
  const result = apply({ params }, CTX);
  assert.equal(result.params.job.delivery.mode, "none");
  assert.equal(result.params.sessionTarget, "isolated");
});

test("update action untouched", () => {
  const params = { action: "update", jobId: "j1", patch: {} };
  assert.equal(apply({ params }, CTX), undefined);
});

test("remove/get/list actions untouched", () => {
  for (const action of ["remove", "get", "list"]) {
    const params = { action, jobId: "j1" };
    assert.equal(apply({ params }, CTX), undefined);
  }
});

test("unrelated tools untouched", () => {
  const params = addJob({
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
  });
  assert.equal(
    apply({ params }, { agentId: PIXEL, toolName: "calendar" }),
    undefined,
  );
});

test("inner second dispatch is idempotent (already defaulted params pass through unchanged)", () => {
  const params = addJob({
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
    delivery: { mode: "none" },
  });
  const guard = { params };
  assert.equal(apply({ params }, CTX, guard), guard);
});

test("blocked guard result is preserved with exact identity", () => {
  const guard = { block: true, blockReason: "OPS_REASON" };
  const params = addJob({
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
  });
  const result = apply({ params }, CTX, guard);
  assert.equal(result, guard);
});

test("guard params take precedence over event params when both present", () => {
  const guardParams = addJob({
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
  });
  const guard = { params: guardParams };
  const result = apply(
    { params: { action: "list" } },
    CTX,
    guard,
  );
  assert.equal(result.params.job.delivery.mode, "none");
  assert.notEqual(result.params, guardParams);
});

test("guard result without params falls back to event.params", () => {
  const params = addJob({
    sessionTarget: "isolated",
    payload: { kind: "agentTurn", message: "m" },
  });
  const result = apply({ params }, CTX, { ok: true });
  assert.equal(result.params.job.delivery.mode, "none");
  assert.equal(result.ok, true);
});

test("wrapped form without args object untouched", () => {
  assert.equal(
    apply(
      { params: { id: "cron", args: "not-an-object" } },
      { agentId: PIXEL, toolName: "tool_call" },
    ),
    undefined,
  );
});

test("wrapped form with non-cron id untouched", () => {
  assert.equal(
    apply(
      { params: { id: "exec", args: {} } },
      { agentId: PIXEL, toolName: "tool_call" },
    ),
    undefined,
  );
});

test("nested job non-object leaves params untouched", () => {
  const params = { action: "add", job: "nope" };
  assert.equal(apply({ params }, CTX), undefined);
});
