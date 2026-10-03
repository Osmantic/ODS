import assert from "node:assert/strict";
import test from "node:test";

import { runGoalContinuousCommand } from "../deploy/work-controller/goal-continuous-cli.mjs";

const digest = (character) => character.repeat(64);
const goal = Object.freeze({ goalId: "workgoal-1786366800001-abcdef123456" });
const jobs = Object.freeze([{ jobId: "work-1786366800000-abcdef123456" }]);
const config = Object.freeze({ stateRoot: "/state", controller: Object.freeze({ maxTransitions: 1 }) });

function cycle(action, durableProgress, sequence) {
  return {
    action, durableProgress, goalState: action === "terminal" ? "completed" : "running",
    goalCheckpointSha256: digest(String((sequence % 9) + 1)), sequence,
    milestonesCompleted: action === "terminal" ? 1 : 0, milestonesTotal: 1,
  };
}

function dependencies(results, overrides = {}) {
  let calls = 0, statuses = 0;
  return {
    value: {
      loadConfiguration: async () => ({ config, goal, jobs, policy: {} }),
      routerFactory: () => ({ resolveChildRun() {}, driveChild() {} }),
      cycleRunner: async (options) => {
        assert.equal(options.maxControllerTransitions, 1);
        return results[calls++];
      },
      statusPublisher: async () => { statuses += 1; },
      monotonicNow: () => 0,
      ...overrides,
    },
    counts: () => ({ calls, statuses }),
  };
}

test("continuous goal activation immediately follows durable progress until terminal", async () => {
  const fixture = dependencies([
    cycle("controller-yield", true, 1),
    cycle("controller-yield", true, 2),
    cycle("terminal", true, 3),
  ]);
  const receipt = await runGoalContinuousCommand(["--config", "/private/controller.json"], fixture.value);
  assert.equal(receipt.stopReason, "terminal");
  assert.equal(receipt.reconciliations, 3);
  assert.equal(receipt.durableProgressReconciliations, 3);
  assert.equal(receipt.schedulingEffect, "event-noop");
  assert.deepEqual(fixture.counts(), { calls: 3, statuses: 3 });
  assert.deepEqual(Object.values(receipt.authority), [false, false, false, false, false, false]);
});

test("continuous goal activation stops synchronously on no progress, pause, and authority waits", async () => {
  for (const [action, progress, reason] of [
    ["child-in-progress", false, "child-in-progress"],
    ["controller-yield", false, "no-durable-progress"],
    ["paused", true, "paused"],
    ["wait-for-child-authority", true, "wait-for-child-authority"],
    ["child-authority-expired", false, "child-authority-expired"],
  ]) {
    const fixture = dependencies([cycle(action, progress, 1)]);
    const receipt = await runGoalContinuousCommand(["--config", "/private/controller.json"], fixture.value);
    assert.equal(receipt.stopReason, reason);
    assert.deepEqual(fixture.counts(), { calls: 1, statuses: 1 });
  }
});

test("continuous goal activation yields only at hard event or active-time ceilings", async () => {
  const cycleLimited = dependencies([cycle("controller-yield", true, 1), cycle("controller-yield", true, 2)], { maxCycles: 2 });
  const cycleReceipt = await runGoalContinuousCommand(["--config", "/private/controller.json"], cycleLimited.value);
  assert.equal(cycleReceipt.stopReason, "cycle-ceiling");
  assert.equal(cycleReceipt.reconciliations, 2);

  const times = [0, 1, 11];
  const timeLimited = dependencies([cycle("controller-yield", true, 1), cycle("controller-yield", true, 2)], {
    maxCycles: 3, maxActiveMilliseconds: 10, monotonicNow: () => times.shift(),
  });
  const timeReceipt = await runGoalContinuousCommand(["--config", "/private/controller.json"], timeLimited.value);
  assert.equal(timeReceipt.stopReason, "active-time-ceiling");
  assert.equal(timeReceipt.reconciliations, 2);
});

test("continuous goal activation rejects malformed calls and unproven progress", async () => {
  await assert.rejects(runGoalContinuousCommand([], {}), /Usage/);
  const malformed = dependencies([{ action: "controller-yield" }]);
  await assert.rejects(runGoalContinuousCommand(["--config", "/private/controller.json"], malformed.value), /cycle result is invalid/);
});
