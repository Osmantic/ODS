// Deterministic, model-free verifier for neutral non-security provider tasks. It performs
import { canonical } from "../../scripts/lib/work-contract.mjs";

// only exact/structural string, JSON, and patch assertions supplied by a fixed rubric. It
// never invokes a model, never calls an adapter, and never opens a network connection.

function plain(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }

export class WorkProviderGraderError extends Error {}
function fail(message) { throw new WorkProviderGraderError(message); }

function canonicalCompare(actual, expected) {
  return canonical(actual) === canonical(expected);
}

export function gradeDeterministic({ task, actual, expected, base }) {
  if (typeof task !== "string" || task.length < 1) fail("grading task is invalid");
  const criteria = [];
  const add = (id, pass, detail) => criteria.push({ id, pass, detail });

  switch (task) {
    case "structured-output": {
      const structured = plain(actual) && !Array.isArray(actual) && expected !== undefined && typeof expected === "object";
      add("shape", structured, "response is a structured JSON object");
      if (structured) {
        add("fields", canonicalCompare(actual, expected), "response fields match the fixed rubric exactly");
      } else {
        add("fields", false, "response fields do not match the fixed rubric");
      }
      break;
    }
    case "structural-review": {
      const structured = plain(actual) && !Array.isArray(actual) && expected !== undefined && typeof expected === "object";
      add("shape", structured, "response is a structured JSON object");
      if (structured) add("fields", canonicalCompare(actual, expected), "structural-review fields match the fixed rubric exactly");
      else add("fields", false, "structural-review fields do not match the fixed rubric");
      break;
    }
    case "failure-triage": {
      const structured = plain(actual) && !Array.isArray(actual) && expected !== undefined && typeof expected === "object";
      add("shape", structured, "response is a structured JSON object");
      if (structured) add("fields", canonicalCompare(actual, expected), "failure-triage fields match the fixed rubric exactly");
      else add("fields", false, "failure-triage fields do not match the fixed rubric");
      break;
    }
    case "patch-proposal":
    case "disposable-coding-patch": {
      add("check", plain(actual) && actual.checkPassed === true, "git apply --check accepts the one-file disposable patch");
      add("applied", plain(actual) && actual.applied === true, "the patch applies to the exact disposable base");
      add("exact", plain(actual) && actual.exact === true, "the patched file equals the fixed expected result byte-for-byte");
      add("fixpoint", plain(actual) && actual.secondApplyRejected === true, "a second application is rejected");
      add("reversible", plain(actual) && actual.reverseCheckPassed === true, "the applied patch passes an exact reverse check");
      break;
    }
    case "tool-choice-continuation": {
      const hasTool = plain(actual) && typeof actual.chosenTool === "string" && actual.chosenTool.length > 0;
      add("chosen-tool", hasTool, "chosen tool identity is present and non-empty");
      const hasContinuation = plain(actual) && (typeof actual.continuation === "string" && actual.continuation.length > 0 || plain(actual.continuation));
      add("continuation", hasContinuation, "tool-result continuation is present");
      if (expected !== undefined) add("expected", canonicalCompare(actual, expected), "tool continuation matches the fixed rubric");
      break;
    }
    case "long-context-sentinel": {
      const text = typeof actual === "string" ? actual : plain(actual) ? JSON.stringify(actual) : "";
      add("sentinel", typeof expected === "string" && text.includes(expected), "long-context sentinel is preserved verbatim");
      break;
    }
    case "output-capacity": {
      const text = typeof actual === "string" ? actual : "";
      const words = text.trim().length === 0 ? [] : text.trim().split(/\s+/u);
      const validExpected = plain(expected) && typeof expected.word === "string" && expected.word.length > 0
        && Number.isSafeInteger(expected.minimumWords) && expected.minimumWords > 0;
      add("minimum", validExpected && words.length >= expected.minimumWords, "output-capacity reply reaches the fixed word floor");
      add("content", validExpected && words.every((word) => word === expected.word), "every output-capacity word matches the fixed neutral token");
      break;
    }
    case "known-uncertain-recovery": {
      const known = plain(actual) && ["failed-known", "uncertain"].includes(actual.outcome);
      add("classified", known, "outcome is classified as failed-known or uncertain");
      add("no-reroute", plain(actual) && actual.rerouted === false, "no automatic retry or reroute occurred on an uncertain outcome");
      break;
    }
    default:
      fail(`unknown neutral grading task ${task}`);
  }
  const pass = criteria.every((criterion) => criterion.pass);
  const score = pass ? 1 : 0;
  return Object.freeze({ task, criteria: Object.freeze(criteria), pass, score });
}

export const NEUTRAL_TASK_MANIFEST = Object.freeze([
  "structured-output",
  "disposable-coding-patch",
  "structural-review",
  "patch-proposal",
  "failure-triage",
  "tool-choice-continuation",
  "long-context-sentinel",
  "output-capacity",
  "known-uncertain-recovery",
]);
