import { createInterface } from "node:readline/promises";
import { stdin, stdout } from "node:process";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { KnowledgeVaultCliError, runKnowledgeVaultCommand } from "./knowledge-vault-cli.mjs";

const operations = Object.freeze({
  setup: Object.freeze({ review: "setup-review", apply: "setup-apply", options: ["--vault", "--vault-id", "--credential"] }),
  add: Object.freeze({ review: "ingest-review", apply: "ingest-apply", options: ["--vault", "--vault-id", "--credential", "--owner", "--client", "--title-file", "--source", "--classification", "--delete-after"] }),
  remove: Object.freeze({ review: "delete-review", apply: "delete-apply", options: ["--vault", "--vault-id", "--credential", "--owner", "--client", "--source-id", "--reason"] }),
  find: Object.freeze({ review: "query-review", apply: "query-apply", options: ["--vault", "--vault-id", "--credential", "--owner", "--client", "--job-id", "--checkpoint-sha256", "--query-file", "--maximum-classification", "--minimum-score-bps", "--max-results", "--output"] }),
  rotate: Object.freeze({ review: "rotate-review", apply: "rotate-apply", options: ["--vault", "--vault-id", "--old-credential", "--new-credential", "--controller-offline"] }),
  reconcile: Object.freeze({ review: "reconcile-review", apply: "reconcile-apply", options: ["--authoritative-vault", "--restored-vault", "--vault-id", "--authoritative-credential", "--restored-credential", "--controller-offline"] }),
});

export class KnowledgeVaultGuideError extends Error {}

function fail(message) { throw new KnowledgeVaultGuideError(message); }
function safe(value) { return String(value).replace(/[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/gu, (character) => `\\u${character.codePointAt(0).toString(16).padStart(4, "0")}`); }

function parse(argv) {
  const name = argv?.[0], operation = operations[name];
  if (!operation || argv.length !== operation.options.length * 2 + 1) fail("Usage: knowledge-vault-guide.mjs setup|add|remove|find|rotate|reconcile with the exact documented options");
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const option = argv[index], value = argv[index + 1];
    if (!operation.options.includes(option) || Object.hasOwn(values, option) || typeof value !== "string" || !value) fail("guided knowledge options are invalid, unknown, duplicated, or incomplete");
    values[option] = value;
  }
  if (operation.options.some((option) => !Object.hasOwn(values, option))) fail("guided knowledge options are incomplete");
  if (values["--controller-offline"] !== undefined && values["--controller-offline"] !== "confirmed") fail("rotation or reconciliation requires --controller-offline confirmed");
  const review = [operation.review], apply = [operation.apply];
  for (const option of operation.options) {
    if (option === "--controller-offline") continue;
    review.push(option, values[option]); apply.push(option, values[option]);
  }
  return { name, operation, review, apply, offline: values["--controller-offline"] };
}

function reviewLines(name, review) {
  if (name === "setup") return ["Create a new encrypted private-knowledge vault.", `Vault: ${safe(review.vaultDirectory)}`, `External key: ${safe(review.credentialFile)} (${safe(review.credentialAction)})`, `Resume interrupted setup: ${review.resumesStagedInitialization ? "yes" : "no"}`];
  if (name === "add") return ["Add one exact local text source.", `Title: ${safe(review.title)}`, `File: ${safe(review.sourceFile)}`, `Size: ${review.sourceBytes} bytes`, `Classification: ${safe(review.classification)}`, `Delete after: ${safe(review.deleteAfter ?? "no automatic date")}`];
  if (name === "remove") return ["Permanently delete one exact source and retain only its tombstone.", `Title: ${safe(review.title)}`, `Source: ${safe(review.sourceId)}`, `Classification: ${safe(review.classification)}`, `Reason: ${safe(review.reasonCode)}`];
  if (name === "find") return ["Read matching local knowledge into one new private context file.", `Query file: ${safe(review.queryFile)}`, `Output file: ${safe(review.outputFile)}`, `Maximum classification: ${safe(review.maximumClassification)}`, `Maximum results: ${review.maxResults}`, "Returned text remains untrusted data."];
  if (name === "rotate") return ["Rotate all encrypted vault wrapping to a distinct external key.", `Active sources: ${review.sourceCount}`, `Deletion tombstones: ${review.deletionCount}`, "The controller must already be stopped and verified offline."];
  return ["Propagate current deletion tombstones into one historical restored vault.", `Current tombstones: ${review.authoritativeDeletionCount}`, `Historical active sources: ${review.restoredSourceCount}`, "The controller must already be stopped and verified offline."];
}

function completionLine(name, result) {
  return {
    setup: `Setup complete. Vault state: ${safe(result.state)}.`,
    add: `Source added. Local source ID: ${safe(result.sourceId)}.`,
    remove: `Source deleted. Residual ciphertext: ${result.residualCiphertext ? "attention required" : "none"}.`,
    find: `Private context written. Matches: ${result.results}. Output digest: ${safe(result.outputSha256)}.`,
    rotate: `Key rotation complete. Old-key wrapping remains: ${result.residualOldKeyWrapping ? "yes" : "no"}.`,
    reconcile: `Historical deletion reconciliation complete. Tombstones applied: ${result.applied}.`,
  }[name];
}

async function terminalPrompt(message) {
  const terminal = createInterface({ input: stdin, output: stdout });
  try { return await terminal.question(message); } finally { terminal.close(); }
}

export async function runKnowledgeVaultGuide(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)
    || dependencies.prompt !== undefined && typeof dependencies.prompt !== "function"
    || dependencies.write !== undefined && typeof dependencies.write !== "function"
    || dependencies.knowledgeDependencies !== undefined && (!dependencies.knowledgeDependencies || typeof dependencies.knowledgeDependencies !== "object" || Array.isArray(dependencies.knowledgeDependencies))) fail("guided knowledge dependencies are invalid");
  const parsed = parse(argv), write = dependencies.write ?? ((value) => stdout.write(value)), prompt = dependencies.prompt ?? terminalPrompt;
  const review = await runKnowledgeVaultCommand(parsed.review, dependencies.knowledgeDependencies ?? {}), code = review.confirmation?.sha256;
  if (!/^[a-f0-9]{64}$/u.test(code ?? "")) fail("guided knowledge review did not return an exact confirmation");
  write("\nReview this local-only operation\n");
  for (const line of reviewLines(parsed.name, review)) write(`- ${line}\n`);
  write(`- Review code: ${code}\n`);
  const phrase = `APPLY ${code.slice(-12)}`, answer = (await prompt(`\nType ${phrase} to continue, or press Enter to cancel: `)).trim();
  if (answer !== phrase) { write("Cancelled. Nothing was changed.\n"); return { schemaVersion: 1, operation: "pixel-work-knowledge-guide", state: "cancelled", mutated: false }; }
  parsed.apply.push("--confirm-review-sha256", code);
  if (parsed.offline !== undefined) parsed.apply.push("--controller-offline", parsed.offline);
  const result = await runKnowledgeVaultCommand(parsed.apply, dependencies.knowledgeDependencies ?? {});
  write(`${completionLine(parsed.name, result)}\n`);
  return { schemaVersion: 1, operation: "pixel-work-knowledge-guide", state: "completed", guidedOperation: parsed.name, mutated: parsed.name !== "find", localRead: parsed.name === "find", result };
}

export async function main(argv = process.argv.slice(2)) { await runKnowledgeVaultGuide(argv); }

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) main().catch((error) => {
  process.stderr.write(`pixel-work-knowledge-guide: ${error instanceof KnowledgeVaultGuideError || error instanceof KnowledgeVaultCliError ? error.message : "safe operation failed; inspect the selected local files and vault state"}\n`); process.exitCode = 1;
});

export const knowledgeVaultGuideOperations = operations;
