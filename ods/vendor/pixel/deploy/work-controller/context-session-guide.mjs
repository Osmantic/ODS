import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { ContextSessionCliError, runContextSessionCommand } from "./context-session-cli.mjs";

const operations = Object.freeze({
  create: Object.freeze({ review: "create-review", apply: "create-apply", options: ["--state-root", "--plan", "--checkpoint", "--context", "--expires-in-days"] }),
  fork: Object.freeze({ review: "fork-review", apply: "fork-apply", options: ["--state-root", "--plan", "--checkpoint", "--context", "--expires-in-days", "--parent-capsule", "--parent-capsule-sha256"] }),
  remove: Object.freeze({ review: "remove-review", apply: "remove-apply", options: ["--state-root", "--job-id", "--capsule-id", "--capsule-sha256"] }),
});

export class ContextSessionGuideError extends Error {}
function fail(message) { throw new ContextSessionGuideError(message); }
function safe(value) { return String(value).replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/gu, (character) => `\\u${character.codePointAt(0).toString(16).padStart(4, "0")}`); }

function parse(argv) {
  const name = argv?.[0], operation = operations[name];
  if (!operation || argv.length !== operation.options.length * 2 + 1) fail("Usage: context-session-guide.mjs create|fork|remove with the exact documented options");
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const option = argv[index], value = argv[index + 1];
    if (!operation.options.includes(option) || Object.hasOwn(values, option) || typeof value !== "string" || !value) fail("guided context options are invalid, unknown, duplicated, or incomplete");
    values[option] = value;
  }
  if (operation.options.some((option) => !Object.hasOwn(values, option))) fail("guided context options are incomplete");
  const review = [operation.review], apply = [operation.apply];
  for (const option of operation.options) { review.push(option, values[option]); apply.push(option, values[option]); }
  return { name, review, apply };
}

function reviewLines(name, review) {
  if (name === "remove") return [
    "Remove one exact private context session.", `Job: ${safe(review.jobId)}`, `Session: ${safe(review.capsuleId)}`,
    "Pixel will retain only a content-free removal tombstone. This is not a secure-erasure claim for storage media or existing backups.",
  ];
  const lines = [
    name === "fork" ? "Start a new child session from one exact parent without reusing its authority." : "Create one new checkpoint-bound private context session.",
    `New job: ${safe(review.jobId)}`, `Classification: ${safe(review.dataClassification)}`, `Checkpoint state: ${safe(review.checkpointState)}`,
    `Decisions: ${review.inventory.decisions}`, `Open risks: ${review.inventory.unresolvedRisks}`, `Artifacts: ${review.inventory.artifacts}`,
    `Expires after: ${review.retention.expiresInDays} days`, "Private context has not been displayed or sent anywhere.",
  ];
  if (name === "fork") lines.splice(2, 0, `Parent job: ${safe(review.lineage.parentJobId)}`, `Lineage depth: ${review.lineage.depth}`);
  return lines;
}

function completionLine(name, result) {
  if (name === "remove") return `Session removed. Content retained in live session storage: ${result.contentRetained ? "attention required" : "no"}. Existing backups may still retain historical bytes.`;
  return `${name === "fork" ? "Child session" : "Session"} ready. Session ID: ${safe(result.capsuleId)}. Exact digest: ${safe(result.capsuleSha256)}.`;
}

async function terminalPrompt(message) {
  const terminal = createInterface({ input: stdin, output: stdout });
  try { return await terminal.question(message); } finally { terminal.close(); }
}

export async function runContextSessionGuide(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)
    || dependencies.prompt !== undefined && typeof dependencies.prompt !== "function"
    || dependencies.write !== undefined && typeof dependencies.write !== "function"
    || dependencies.contextDependencies !== undefined && (!dependencies.contextDependencies || typeof dependencies.contextDependencies !== "object" || Array.isArray(dependencies.contextDependencies))) fail("guided context dependencies are invalid");
  const parsed = parse(argv), write = dependencies.write ?? ((value) => stdout.write(value)), prompt = dependencies.prompt ?? terminalPrompt;
  const review = await runContextSessionCommand(parsed.review, dependencies.contextDependencies ?? {}), code = review.confirmation?.sha256;
  if (!/^[a-f0-9]{64}$/u.test(code ?? "")) fail("guided context review did not return an exact confirmation");
  write("\nReview this local-only context operation\n");
  for (const line of reviewLines(parsed.name, review)) write(`- ${line}\n`);
  write(`- Review code: ${code}\n`);
  const phrase = `APPLY ${code.slice(-12)}`, answer = (await prompt(`\nType ${phrase} to continue, or press Enter to cancel: `)).trim();
  if (answer !== phrase) { write("Cancelled. Nothing was changed.\n"); return { schemaVersion: 1, operation: "pixel-work-context-session-guide", state: "cancelled", mutated: false }; }
  parsed.apply.push("--confirm-review-sha256", code);
  const result = await runContextSessionCommand(parsed.apply, dependencies.contextDependencies ?? {});
  write(`${completionLine(parsed.name, result)}\n`);
  return { schemaVersion: 1, operation: "pixel-work-context-session-guide", state: "completed", guidedOperation: parsed.name, mutated: true, result };
}

export async function main(argv = process.argv.slice(2)) { await runContextSessionGuide(argv); }

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) main().catch((error) => {
  process.stderr.write(`pixel-work-context-session-guide: ${error instanceof ContextSessionGuideError || error instanceof ContextSessionCliError ? error.message : "safe context operation failed; inspect the selected local files and session state"}\n`); process.exitCode = 1;
});

export const contextSessionGuideOperations = operations;
