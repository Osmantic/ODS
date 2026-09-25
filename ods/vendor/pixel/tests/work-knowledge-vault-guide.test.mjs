import assert from "node:assert/strict";
import { chmod, lstat, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { runKnowledgeVaultGuide } from "../deploy/work-controller/knowledge-vault-guide.mjs";

const vaultId = "knowledgevault-abcdef123456";
async function privateDir(path) { await mkdir(path, { recursive: true, mode: 0o700 }); if (process.platform !== "win32") await chmod(path, 0o700); return path; }
async function paths(t, name) {
  const parent = await mkdtemp(join(tmpdir(), `pixel-knowledge-guide-${name}-`)); t.after(() => rm(parent, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(parent, 0o700);
  return { parent, vault: join(await privateDir(join(parent, "state")), "vault"), credential: join(await privateDir(join(parent, "keys")), "pixel-knowledge-vault-key") };
}
function dependencies(output, approve = true, suffix = undefined) {
  return {
    write: (value) => output.push(value),
    prompt: async () => approve ? `APPLY ${/Review code: ([a-f0-9]{64})/u.exec(output.join(""))[1].slice(-12)}` : "",
    knowledgeDependencies: { now: () => new Date("2026-08-11T22:00:00.000Z"), ...(suffix ? { suffix } : {}) },
  };
}

test("plain-language setup cancels without mutation and then creates a private external key", async (t) => {
  const value = await paths(t, "setup"), args = ["setup", "--vault", value.vault, "--vault-id", vaultId, "--credential", value.credential], cancelledOutput = [];
  const cancelled = await runKnowledgeVaultGuide(args, dependencies(cancelledOutput, false));
  assert.equal(cancelled.state, "cancelled"); assert.equal(cancelled.mutated, false); await assert.rejects(() => lstat(value.credential), /ENOENT/u);
  const output = [], completed = await runKnowledgeVaultGuide(args, dependencies(output));
  assert.equal(completed.state, "completed"); assert.equal(completed.guidedOperation, "setup"); assert.equal(completed.result.state, "ready");
  const key = await readFile(value.credential, "utf8"); assert.match(key, /^[a-f0-9]{64}\n$/u); assert.doesNotMatch(output.join(""), new RegExp(key.trim(), "u"));
  assert.match(output.join(""), /Create a new encrypted private-knowledge vault/u); assert.match(output.join(""), /Setup complete/u);
});

test("plain-language add and remove preserve the exact core review boundary and escape terminal controls", async (t) => {
  const value = await paths(t, "lifecycle"), setup = ["setup", "--vault", value.vault, "--vault-id", vaultId, "--credential", value.credential];
  await runKnowledgeVaultGuide(setup, dependencies([]));
  const input = await privateDir(join(value.parent, "input")), title = join(input, "title.txt"), source = join(input, "source.txt");
  await writeFile(title, "Private Aurora [31mnotice\n", { flag: "wx", mode: 0o600 }); await writeFile(source, "Cobalt launch context remains untrusted data.", { flag: "wx", mode: 0o600 });
  const addOutput = [], added = await runKnowledgeVaultGuide([
    "add", "--vault", value.vault, "--vault-id", vaultId, "--credential", value.credential, "--owner", "owner-one", "--client", "client-one",
    "--title-file", title, "--source", source, "--classification", "restricted", "--delete-after", "none",
  ], dependencies(addOutput, true, "000000000010"));
  assert.equal(added.result.state, "ingested"); assert.doesNotMatch(addOutput.join(""), /\u001b\[31m/u); assert.match(addOutput.join(""), /\\u001b\[31m/u);
  const query = join(input, "query.txt"), output = join(input, "retrieval.json"); await writeFile(query, "cobalt launch\n", { flag: "wx", mode: 0o600 });
  const findOutput = [], found = await runKnowledgeVaultGuide([
    "find", "--vault", value.vault, "--vault-id", vaultId, "--credential", value.credential, "--owner", "owner-one", "--client", "client-one",
    "--job-id", "work-1786374000000-abcdef123456", "--checkpoint-sha256", "a".repeat(64), "--query-file", query,
    "--maximum-classification", "restricted", "--minimum-score-bps", "2500", "--max-results", "5", "--output", output,
  ], dependencies(findOutput, true, "000000000011"));
  assert.equal(found.result.state, "matched"); assert.equal(found.localRead, true); assert.equal(found.mutated, false); assert.equal(JSON.parse(await readFile(output, "utf8")).results.length, 1);
  assert.doesNotMatch(findOutput.join(""), /Cobalt launch context/u); assert.match(findOutput.join(""), /Returned text remains untrusted data/u);
  const removeOutput = [], removed = await runKnowledgeVaultGuide([
    "remove", "--vault", value.vault, "--vault-id", vaultId, "--credential", value.credential, "--owner", "owner-one", "--client", "client-one",
    "--source-id", added.result.sourceId, "--reason", "owner-request",
  ], dependencies(removeOutput, true, "000000000020"));
  assert.equal(removed.result.state, "deleted"); assert.equal(removed.result.residualCiphertext, false); assert.match(removeOutput.join(""), /Permanently delete/u);
});

test("guide rejects incomplete options and refuses offline lifecycle claims without exact confirmation", async () => {
  await assert.rejects(() => runKnowledgeVaultGuide(["setup"], dependencies([])), /exact documented options/u);
  await assert.rejects(() => runKnowledgeVaultGuide(["rotate", "--vault", "/tmp/vault", "--vault-id", vaultId, "--old-credential", "/tmp/old", "--new-credential", "/tmp/new", "--controller-offline", "no"], dependencies([])), /requires --controller-offline confirmed/u);
});
