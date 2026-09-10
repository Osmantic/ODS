// Run against the installed pinned SDK, without creating a real cron job.
// OPENCLAW_CRON_NORMALIZE_MODULE identifies its dist/normalize-*.js module.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { withPixelCronDeliveryDefault } from "../plugin/cron-delivery-default.mjs";

const modulePath = process.env.OPENCLAW_CRON_NORMALIZE_MODULE;
assert.ok(modulePath, "OPENCLAW_CRON_NORMALIZE_MODULE is required");
const pkg = JSON.parse(readFileSync(resolve(dirname(modulePath), "../package.json"), "utf8"));
assert.equal(pkg.version, "2026.6.33", "Requalify when the pinned SDK changes");
const sdk = await import(pathToFileURL(modulePath).href);
const normalize = Object.values(sdk).find(value =>
  typeof value === "function" && value.name === "normalizeCronJobCreate");
assert.equal(typeof normalize, "function");
const job = {
  name: "delivery-default-qualification",
  schedule: { kind: "at", at: "2035-01-01T00:00:00Z" },
  sessionTarget: "isolated",
  payload: { kind: "agentTurn", message: "Write a timestamp to the workspace." },
};

test("real SDK reproduces announce default and accepts Pixel's replacement", () => {
  assert.equal(normalize(structuredClone(job)).delivery.mode, "announce");
  const params = { action: "add", job: structuredClone(job) };
  const result = withPixelCronDeliveryDefault(undefined, { params },
    { agentId: "pixel", toolName: "openclaw:core:cron" }, "pixel");
  assert.equal(normalize(result.params.job).delivery.mode, "none");
  assert.deepEqual(params.job, job);
});

test("Tool Search and selected-tool hooks preserve the explicit default", () => {
  const params = { id: "openclaw:core:cron", args: { action: "add", job: structuredClone(job) } };
  const outer = withPixelCronDeliveryDefault(undefined, { params },
    { agentId: "pixel", toolName: "tool_call" }, "pixel");
  const inner = { params: outer.params.args };
  assert.equal(withPixelCronDeliveryDefault(inner, { params: inner.params },
    { agentId: "pixel", toolName: "cron" }, "pixel"), inner);
  assert.equal(normalize(inner.params.job).delivery.mode, "none");
});

test("explicit external delivery survives both adapter and SDK normalization", () => {
  const params = { action: "add", job: { ...structuredClone(job),
    delivery: { mode: "announce", channel: "slack", to: "qualification-only" } } };
  assert.equal(withPixelCronDeliveryDefault(undefined, { params },
    { agentId: "pixel", toolName: "cron" }, "pixel"), undefined);
  const actual = normalize(params.job).delivery;
  assert.equal(actual.mode, "announce");
  assert.equal(actual.channel, "slack");
  assert.equal(actual.to, "qualification-only");
});

test("real SDK defaults current and explicit-session background jobs too", () => {
  for (const sessionTarget of ["current", "session:agent:pixel:qualification-only"]) {
    const params = { action: "add", job: { ...structuredClone(job), sessionTarget } };
    assert.equal(normalize(structuredClone(params.job)).delivery.mode, "announce");
    const result = withPixelCronDeliveryDefault(undefined, { params },
      { agentId: "pixel", toolName: "cron" }, "pixel");
    assert.equal(normalize(result.params.job).delivery.mode, "none");
    assert.equal(result.params.job.sessionTarget, sessionTarget);
  }
});
