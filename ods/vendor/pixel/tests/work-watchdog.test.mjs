import assert from "node:assert/strict";
import test from "node:test";

import { assessWatchdog, createWatchdogEvent, WorkWatchdogError } from "../deploy/work-controller/watchdog.mjs";
import { validateWorkWatchdogDecision } from "../scripts/lib/work-contract.mjs";

const digest = (character) => character.repeat(64);
const baseTime = Date.parse("2026-08-10T12:00:00Z");
const limits = { maxToolCalls: 20, maxFailures: 4, maxRepeatedEquivalent: 3, maxRepeatedFailure: 2, maxEventsWithoutVerifiedProgress: 8 };
const base = (events, proposal, tick) => assessWatchdog({
  jobId: "work-1786363200000-abcdef123456", checkpointSha256: digest("a"), events, proposal, limits,
  expectedEventHeadSha256: events.length ? events.at(-1).recordSha256 : null,
  allowedEffectClasses: ["read-only", "workspace", "brokered-network"],
  now: new Date(baseTime + tick), suffix: String(tick).padStart(12, "0"),
});
const observe = (decision, tick, overrides = {}) => createWatchdogEvent({
  decision, outcome: "success", verifiedProgressEpoch: 0, observedAt: new Date(baseTime + tick), ...overrides,
});

test("watchdog fingerprints canonical arguments and emits content-free preflight evidence", () => {
  const left = base([], { tool: "read", arguments: { path: "src/a.js", line: 1 }, effectClass: "read-only" }, 1);
  const right = base([], { tool: "read", arguments: { line: 1, path: "src/a.js" }, effectClass: "read-only" }, 2);
  assert.equal(left.proposal.eventFingerprintSha256, right.proposal.eventFingerprintSha256);
  assert.equal(left.decision, "continue");
  assert.deepEqual(validateWorkWatchdogDecision(left), []);
  assert.equal(JSON.stringify(left).includes("src/a.js"), false);
  assert.equal(left.authority.grantsToolCall, false);
});

test("watchdog stops an equivalent-call loop before another call", () => {
  const proposal = { tool: "grep", arguments: { pattern: "TODO", path: "src" }, effectClass: "read-only" };
  const first = base([], proposal, 1);
  const events = [observe(first, 2)];
  const second = base(events, proposal, 3);
  events.push(observe(second, 4));
  const third = base(events, proposal, 5);
  assert.equal(third.decision, "stop");
  assert.equal(third.terminalState, "no-progress");
  assert.equal(third.reason, "repeated-equivalent-call");
});

test("watchdog stops oscillation, repeated failure, budgets, and ungranted effects", () => {
  const events = [];
  for (let index = 0; index < 5; index += 1) {
    const proposal = { tool: index % 2 ? "edit" : "read", arguments: { target: index % 2 ? "b" : "a" }, effectClass: index % 2 ? "workspace" : "read-only" };
    const decision = base(events, proposal, index * 2 + 1);
    events.push(observe(decision, index * 2 + 2));
  }
  const oscillation = base(events, { tool: "edit", arguments: { target: "b" }, effectClass: "workspace" }, 11);
  assert.equal(oscillation.reason, "oscillation");
  const unsafe = base([], { tool: "publish", arguments: { target: "remote" }, effectClass: "external" }, 12);
  assert.equal(unsafe.terminalState, "safety-stopped");
  const failureEvents = [];
  for (let index = 0; index < 2; index += 1) {
    const decision = base(failureEvents, { tool: `test${index}`, arguments: { run: index }, effectClass: "workspace" }, 20 + index * 2);
    failureEvents.push(observe(decision, 21 + index * 2, { outcome: "error", failureFingerprintSha256: digest("f") }));
  }
  assert.equal(base(failureEvents, { tool: "repair", arguments: { run: 3 }, effectClass: "workspace" }, 25).reason, "repeated-failure");
  const exhaustedLimits = { ...limits, maxToolCalls: 1 };
  assert.equal(assessWatchdog({
    jobId: "work-1786363200000-abcdef123456", checkpointSha256: digest("a"), events: [events[0]],
    expectedEventHeadSha256: events[0].recordSha256,
    proposal: { tool: "read", arguments: { path: "b" }, effectClass: "read-only" }, limits: exhaustedLimits,
    allowedEffectClasses: ["read-only"], now: new Date(baseTime + 100), suffix: "abcdef123456",
  }).terminalState, "budget-exhausted");
});

test("watchdog rejects forged event chains and records only valid continue decisions", () => {
  const decision = base([], { tool: "read", arguments: { path: "a" }, effectClass: "read-only" }, 1);
  const forged = observe(decision, 2);
  forged.eventFingerprintSha256 = digest("0");
  assert.throws(() => base([forged], { tool: "read", arguments: { path: "b" }, effectClass: "read-only" }, 3), WorkWatchdogError);
  assert.throws(() => assessWatchdog({
    jobId: "work-1786363200000-abcdef123456", checkpointSha256: digest("a"), expectedEventHeadSha256: digest("0"), events: [observe(decision, 2)],
    proposal: { tool: "read", arguments: { path: "b" }, effectClass: "read-only" }, limits,
    allowedEffectClasses: ["read-only"], now: new Date(baseTime + 3), suffix: "abcdef123456",
  }), /trusted head/);
  const stopped = base([], { tool: "publish", arguments: { target: "remote" }, effectClass: "external" }, 4);
  assert.throws(() => createWatchdogEvent({ decision: stopped, outcome: "success", verifiedProgressEpoch: 0, observedAt: new Date(baseTime + 5) }), /continue decision/);
});
