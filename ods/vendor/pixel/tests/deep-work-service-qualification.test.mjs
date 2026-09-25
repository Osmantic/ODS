import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  buildQualificationContracts, deepWorkServiceQualificationBoundary, DeepWorkServiceQualificationError,
} from "../scripts/deep-work-service-qualification.mjs";

const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));

function build(overrides = {}) {
  return buildQualificationContracts({
    root: "/var/lib/pixel-supervisor-qualification-test", now: new Date("2026-08-12T12:00:00.000Z"),
    suffix: "abcdef123456", executorSha256: "a".repeat(64), uid: 1999, gid: 1999,
    policyTemplate, ...overrides,
  });
}

test("supported-host supervisor qualification builds one valid non-authorizing dormant goal", () => {
  const value = build();
  assert.equal(value.goal.milestones.length, 1);
  assert.equal(value.goal.milestones[0].jobId, value.job.jobId);
  assert.equal(value.goal.authority.grantsExecution, false);
  assert.equal(value.goal.authority.grantsLease, false);
  assert.equal(value.job.requestedCapabilities.externalEffects, false);
  assert.equal(value.job.requestedCapabilities.modelRoute, "local-only");
  assert.equal(value.config.executorPath, "/usr/bin/true");
  assert.equal(value.config.controller.maxTransitions, 1);
  assert.equal(value.config.stateRoot, "/var/lib/pixel-supervisor-qualification-test/state");
  assert.equal(value.policy.enabled, true);
  assert.equal(value.policy.profiles.scout.enabled, true);
  assert.equal(value.policy.security.directNetwork, false);
  assert.equal(value.policy.security.ambientCredentials, false);
  assert.equal(value.policy.security.automaticDeployment, false);
  assert.match(deepWorkServiceQualificationBoundary, /deliberately paused goal/u);
  assert.match(deepWorkServiceQualificationBoundary, /no worker execution/u);
});

test("supported-host supervisor qualification rejects privileged and non-Linux-shaped contract inputs", () => {
  for (const overrides of [
    { root: "/tmp/pixel-supervisor" }, { root: "/var/lib/../tmp/pixel-supervisor" },
    { uid: 0 }, { gid: 0 }, { suffix: "not-a-suffix" }, { executorSha256: "a".repeat(63) },
  ]) assert.throws(() => build(overrides), DeepWorkServiceQualificationError);
});
