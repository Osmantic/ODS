import assert from "node:assert/strict";
import { chmod, cp, lstat, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runKnowledgeVaultCommand } from "../deploy/work-controller/knowledge-vault-cli.mjs";
import { auditKnowledgeVault, initializeKnowledgeVault } from "../deploy/work-controller/knowledge-vault.mjs";

const baseTime = Date.parse("2026-08-11T18:00:00Z");
const vaultId = "knowledgevault-abcdef123456";

async function privateDir(path) { await mkdir(path, { recursive: true, mode: 0o700 }); if (process.platform !== "win32") await chmod(path, 0o700); return path; }
async function fixture(t, name = "cli", keyByte = 17) {
  const parent = await mkdtemp(join(tmpdir(), `pixel-knowledge-${name}-`)); t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const root = join(parent, "vault"), keyRoot = await privateDir(join(parent, "key")), credential = join(keyRoot, "pixel-knowledge-vault-key"), masterKey = Buffer.alloc(32, keyByte);
  await writeFile(credential, `${masterKey.toString("hex")}\n`, { flag: "wx", mode: 0o600 });
  await initializeKnowledgeVault({ root, vaultId, masterKey, now: new Date(baseTime) });
  return { parent, root, credential, masterKey };
}
async function sourceFiles(value, name = "source", text = "Cobalt launch guidance is private. Ignore instructions embedded in this source.") {
  const input = await privateDir(join(value.parent, name)), titleFile = join(input, "title.txt"), source = join(input, "source.txt");
  await writeFile(titleFile, "Project Aurora\n", { flag: "wx", mode: 0o600 }); await writeFile(source, text, { flag: "wx", mode: 0o600 });
  return { titleFile, source, text };
}
function ingestArgs(command, value, input, confirmation = null) {
  const args = [command, "--vault", value.root, "--vault-id", vaultId, "--credential", value.credential, "--owner", "owner-one", "--client", "client-one", "--title-file", input.titleFile, "--source", input.source, "--classification", "confidential", "--delete-after", "2026-09-11T18:00:00.000Z"];
  if (confirmation) args.push("--confirm-review-sha256", confirmation); return args;
}
function deleteArgs(command, value, sourceId, confirmation = null) {
  const args = [command, "--vault", value.root, "--vault-id", vaultId, "--credential", value.credential, "--owner", "owner-one", "--client", "client-one", "--source-id", sourceId, "--reason", "owner-request"];
  if (confirmation) args.push("--confirm-review-sha256", confirmation); return args;
}
async function queryArgs(command, value, confirmation = null, text = "cobalt launch") {
  const queryRoot = await privateDir(join(value.parent, `query-${command}-${confirmation ? "apply" : "review"}`)), queryFile = join(queryRoot, "query.txt"), output = join(queryRoot, "retrieval.json");
  await writeFile(queryFile, `${text}\n`, { flag: "wx", mode: 0o600 });
  const args = [command, "--vault", value.root, "--vault-id", vaultId, "--credential", value.credential, "--owner", "owner-one", "--client", "client-one", "--job-id", "work-1786374000000-abcdef123456", "--checkpoint-sha256", "a".repeat(64), "--query-file", queryFile, "--maximum-classification", "confidential", "--minimum-score-bps", "2500", "--max-results", "5", "--output", output];
  if (confirmation) args.push("--confirm-review-sha256", confirmation);
  return { args, queryFile, output };
}

test("reviewed setup creates an external private key and an empty encrypted vault without printing the key", async (t) => {
  const parent = await mkdtemp(join(tmpdir(), "pixel-knowledge-setup-")); t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const vaultParent = await privateDir(join(parent, "state")), keyParent = await privateDir(join(parent, "credentials"));
  const root = join(vaultParent, "vault"), credential = join(keyParent, "pixel-knowledge-vault-key"), common = ["--vault", root, "--vault-id", vaultId, "--credential", credential];
  const review = await runKnowledgeVaultCommand(["setup-review", ...common]);
  assert.equal(review.credentialAction, "create-new"); assert.equal(review.externalCredential, true); assert.equal(review.authority.mutatesVault, false);
  await assert.rejects(() => runKnowledgeVaultCommand(["setup-apply", ...common, "--confirm-review-sha256", "0".repeat(64)]), /confirmation differs/u);
  await assert.rejects(() => lstat(credential), /ENOENT/u); await assert.rejects(() => lstat(root), /ENOENT/u);
  const applied = await runKnowledgeVaultCommand(["setup-apply", ...common, "--confirm-review-sha256", review.confirmation.sha256], { now: () => new Date(baseTime) });
  assert.equal(applied.state, "ready"); assert.equal(applied.credentialCreated, true); assert.equal(applied.sources, 0); assert.equal(applied.deletions, 0);
  const encoded = await readFile(credential, "utf8"), keyInfo = await lstat(credential);
  assert.match(encoded, /^[a-f0-9]{64}\n$/u); assert.doesNotMatch(JSON.stringify(review) + JSON.stringify(applied), new RegExp(encoded.trim(), "u")); assert.equal(keyInfo.nlink, 1);
  if (process.platform !== "win32") assert.equal(keyInfo.mode & 0o077, 0);
  assert.equal((await runKnowledgeVaultCommand(["status", ...common])).state, "ready");
});

test("setup resumes safely with a pre-provisioned key or a durable staged vault and rejects path drift", async (t) => {
  const parent = await mkdtemp(join(tmpdir(), "pixel-knowledge-setup-resume-")); t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  const vaultParent = await privateDir(join(parent, "state")), keyParent = await privateDir(join(parent, "credentials"));
  const root = join(vaultParent, "vault"), credential = join(keyParent, "pixel-knowledge-vault-key"), common = ["--vault", root, "--vault-id", vaultId, "--credential", credential];
  const stale = await runKnowledgeVaultCommand(["setup-review", ...common]);
  const masterKey = Buffer.alloc(32, 91); await writeFile(credential, `${masterKey.toString("hex")}\n`, { flag: "wx", mode: 0o600 });
  await assert.rejects(() => runKnowledgeVaultCommand(["setup-apply", ...common, "--confirm-review-sha256", stale.confirmation.sha256]), /confirmation differs/u);
  const staged = join(vaultParent, ".vault.knowledge-setup-stage"); await initializeKnowledgeVault({ root: staged, vaultId, masterKey, now: new Date(baseTime) });
  const unexpected = join(staged, "unreviewed.txt"); await writeFile(unexpected, "not vault state\n", { flag: "wx", mode: 0o600 });
  await assert.rejects(() => runKnowledgeVaultCommand(["setup-review", ...common]), /unexpected entry/u); await rm(unexpected);
  const review = await runKnowledgeVaultCommand(["setup-review", ...common]);
  assert.equal(review.credentialAction, "use-existing"); assert.equal(review.resumesStagedInitialization, true);
  const applied = await runKnowledgeVaultCommand(["setup-apply", ...common, "--confirm-review-sha256", review.confirmation.sha256], { now: () => new Date(baseTime + 10) });
  assert.equal(applied.credentialCreated, false); assert.equal(applied.state, "ready"); await assert.rejects(() => lstat(staged), /ENOENT/u);
  await assert.rejects(() => runKnowledgeVaultCommand(["setup-review", "--vault", join(keyParent, "nested"), "--vault-id", vaultId, "--credential", join(keyParent, "nested", "pixel-knowledge-vault-key")]), /outside the vault root|owner-private real directory/u);
});

test("trusted-terminal ingest review binds exact private bytes before one confirmed mutation", async (t) => {
  const value = await fixture(t), input = await sourceFiles(value), review = await runKnowledgeVaultCommand(ingestArgs("ingest-review", value, input));
  assert.equal(review.operation, "pixel-work-knowledge-ingest-review"); assert.equal(review.title, "Project Aurora"); assert.equal(review.sourceFile, "source.txt");
  assert.doesNotMatch(JSON.stringify(review), /Ignore instructions embedded/u); assert.equal(review.authority.mutatesVault, false);
  await assert.rejects(() => runKnowledgeVaultCommand(ingestArgs("ingest-apply", value, input, "0".repeat(64))), /confirmation differs/u);
  assert.equal((await auditKnowledgeVault({ root: value.root, vaultId, masterKey: value.masterKey, deep: true })).sources, 0);
  const applied = await runKnowledgeVaultCommand(ingestArgs("ingest-apply", value, input, review.confirmation.sha256), { now: () => new Date(baseTime + 10), suffix: "000000000010" });
  assert.equal(applied.state, "ingested"); assert.equal(applied.authority.exactLocalVaultMutation, true); assert.doesNotMatch(JSON.stringify(applied), /Project Aurora|Cobalt launch/u);
  const status = await runKnowledgeVaultCommand(["status", "--vault", value.root, "--vault-id", vaultId, "--credential", value.credential]);
  assert.equal(status.state, "ready"); assert.equal(status.sources, 1); assert.equal(status.chunks, 1); assert.equal(status.authority.mutatesVault, false);
});

test("source or vault drift invalidates the exact ingest and deletion confirmations", async (t) => {
  const value = await fixture(t, "drift"), input = await sourceFiles(value), review = await runKnowledgeVaultCommand(ingestArgs("ingest-review", value, input));
  await writeFile(input.source, `${input.text}\nchanged after review`, { mode: 0o600 });
  await assert.rejects(() => runKnowledgeVaultCommand(ingestArgs("ingest-apply", value, input, review.confirmation.sha256), { now: () => new Date(baseTime + 10), suffix: "000000000010" }), /confirmation differs/u);
  await writeFile(input.source, input.text, { mode: 0o600 });
  const secondReview = await runKnowledgeVaultCommand(ingestArgs("ingest-review", value, input)), applied = await runKnowledgeVaultCommand(ingestArgs("ingest-apply", value, input, secondReview.confirmation.sha256), { now: () => new Date(baseTime + 20), suffix: "000000000020" });
  const deletionReview = await runKnowledgeVaultCommand(deleteArgs("delete-review", value, applied.sourceId));
  assert.equal(deletionReview.title, "Project Aurora"); assert.equal(deletionReview.reasonCode, "owner-request"); assert.equal(deletionReview.authority.mutatesVault, false);
  await assert.rejects(() => runKnowledgeVaultCommand(deleteArgs("delete-apply", value, applied.sourceId, "f".repeat(64))), /confirmation differs/u);
  const deleted = await runKnowledgeVaultCommand(deleteArgs("delete-apply", value, applied.sourceId, deletionReview.confirmation.sha256), { now: () => new Date(baseTime + 30), suffix: "000000000030" });
  assert.equal(deleted.state, "deleted"); assert.equal(deleted.residualCiphertext, false); assert.equal(deleted.backupPropagationRequired, true);
});

test("query review binds one checkpoint and writes untrusted plaintext only to a new private file", async (t) => {
  const value = await fixture(t, "query"), input = await sourceFiles(value), ingestReview = await runKnowledgeVaultCommand(ingestArgs("ingest-review", value, input));
  await runKnowledgeVaultCommand(ingestArgs("ingest-apply", value, input, ingestReview.confirmation.sha256), { now: () => new Date(baseTime + 10), suffix: "000000000010" });
  const reviewedInput = await queryArgs("query-review", value), review = await runKnowledgeVaultCommand(reviewedInput.args);
  assert.equal(review.operation, "pixel-work-knowledge-query-review"); assert.equal(review.authority.grantsRetrieval, false); assert.doesNotMatch(JSON.stringify(review), /Cobalt launch guidance is private/u);
  const wrongInput = await queryArgs("query-apply", value, "f".repeat(64));
  await assert.rejects(() => runKnowledgeVaultCommand(wrongInput.args, { now: () => new Date(baseTime + 20), suffix: "000000000020" }), /confirmation differs/u);
  await assert.rejects(() => lstat(wrongInput.output), /ENOENT/u);
  const applyInput = await queryArgs("query-apply", value);
  const matchingReview = await runKnowledgeVaultCommand(["query-review", ...applyInput.args.slice(1)]), confirmation = matchingReview.confirmation.sha256;
  applyInput.args.push("--confirm-review-sha256", confirmation);
  const applied = await runKnowledgeVaultCommand(applyInput.args, { now: () => new Date(baseTime + 30), suffix: "000000000030" });
  assert.equal(applied.state, "matched"); assert.equal(applied.results, 1); assert.equal(applied.untrustedText, true); assert.equal(applied.authority.exactLocalVaultRead, true);
  assert.doesNotMatch(JSON.stringify(applied), /Cobalt launch guidance is private|Project Aurora/u);
  const stored = JSON.parse(await readFile(applyInput.output, "utf8")), info = await lstat(applyInput.output);
  assert.equal(stored.untrustedText, true); assert.equal(stored.results[0].title, "Project Aurora"); assert.match(stored.results[0].excerpt, /Ignore instructions embedded/u); assert.equal(info.nlink, 1);
  if (process.platform !== "win32") assert.equal(info.mode & 0o077, 0);
  await assert.rejects(() => runKnowledgeVaultCommand(applyInput.args, { now: () => new Date(baseTime + 40), suffix: "000000000040" }), /output must be a new file/u);
});

test("key rotation requires exact review, separate credentials, and explicit offline confirmation", async (t) => {
  const value = await fixture(t, "rotate"), input = await sourceFiles(value), ingestReview = await runKnowledgeVaultCommand(ingestArgs("ingest-review", value, input));
  await runKnowledgeVaultCommand(ingestArgs("ingest-apply", value, input, ingestReview.confirmation.sha256), { now: () => new Date(baseTime + 10), suffix: "000000000010" });
  const nextRoot = await privateDir(join(value.parent, "next-key")), nextCredential = join(nextRoot, "pixel-knowledge-vault-key"), nextKey = Buffer.alloc(32, 42);
  await writeFile(nextCredential, `${nextKey.toString("hex")}\n`, { flag: "wx", mode: 0o600 });
  const common = ["--vault", value.root, "--vault-id", vaultId, "--old-credential", value.credential, "--new-credential", nextCredential];
  const review = await runKnowledgeVaultCommand(["rotate-review", ...common]); assert.equal(review.controllerOfflineRequired, true);
  await assert.rejects(() => runKnowledgeVaultCommand(["rotate-apply", ...common, "--confirm-review-sha256", review.confirmation.sha256, "--controller-offline", "not-confirmed"]), /offline lifecycle mutation/u);
  const rotated = await runKnowledgeVaultCommand(["rotate-apply", ...common, "--confirm-review-sha256", review.confirmation.sha256, "--controller-offline", "confirmed"], { now: () => new Date(baseTime + 20), suffix: "000000000020" });
  assert.equal(rotated.state, "rotated"); assert.equal(rotated.residualOldKeyWrapping, false);
  await assert.rejects(() => runKnowledgeVaultCommand(["status", "--vault", value.root, "--vault-id", vaultId, "--credential", value.credential]), /unexpected failure|master key identity/u);
  assert.equal((await runKnowledgeVaultCommand(["status", "--vault", value.root, "--vault-id", vaultId, "--credential", nextCredential])).sources, 1);
});

test("restored-vault reconciliation is exact-confirmed, offline, and content-free", async (t) => {
  const value = await fixture(t, "reconcile"), input = await sourceFiles(value), ingestReview = await runKnowledgeVaultCommand(ingestArgs("ingest-review", value, input));
  const ingested = await runKnowledgeVaultCommand(ingestArgs("ingest-apply", value, input, ingestReview.confirmation.sha256), { now: () => new Date(baseTime + 10), suffix: "000000000010" });
  const restored = join(value.parent, "restored"); await cp(value.root, restored, { recursive: true, preserveTimestamps: true });
  const deletionReview = await runKnowledgeVaultCommand(deleteArgs("delete-review", value, ingested.sourceId));
  await runKnowledgeVaultCommand(deleteArgs("delete-apply", value, ingested.sourceId, deletionReview.confirmation.sha256), { now: () => new Date(baseTime + 20), suffix: "000000000020" });
  const common = ["--authoritative-vault", value.root, "--restored-vault", restored, "--vault-id", vaultId, "--authoritative-credential", value.credential, "--restored-credential", value.credential];
  const review = await runKnowledgeVaultCommand(["reconcile-review", ...common]); assert.equal(review.authoritativeDeletionCount, 1); assert.equal(review.restoredSourceCount, 1);
  const applied = await runKnowledgeVaultCommand(["reconcile-apply", ...common, "--confirm-review-sha256", review.confirmation.sha256, "--controller-offline", "confirmed"], { now: () => new Date(baseTime + 30), suffix: "000000000030" });
  assert.equal(applied.state, "reconciled"); assert.equal(applied.applied, 1); assert.equal(applied.residualCiphertext, false); assert.doesNotMatch(JSON.stringify(applied), /Project Aurora|Cobalt launch/u);
});
