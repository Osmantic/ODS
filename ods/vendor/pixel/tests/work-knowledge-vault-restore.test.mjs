import assert from "node:assert/strict";
import { chmod, cp, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { prepareKnowledgeVaultRestore, runKnowledgeVaultRestoreCommand } from "../deploy/work-controller/knowledge-vault-restore-cli.mjs";
import {
  auditKnowledgeVault, buildKnowledgeDeletion, buildKnowledgeIngestion, deleteKnowledgeSource,
  ingestKnowledgeText, initializeKnowledgeVault, rotateKnowledgeVaultKey,
} from "../deploy/work-controller/knowledge-vault.mjs";

const baseTime = Date.parse("2026-08-11T20:00:00Z");

async function credential(root, name, key) {
  const directory = join(root, name); await mkdir(directory, { mode: 0o700 });
  const path = join(directory, "pixel-knowledge-vault-key"); await writeFile(path, `${key.toString("hex")}\n`, { flag: "wx", mode: 0o600 });
  if (process.platform !== "win32") { await chmod(directory, 0o700); await chmod(path, 0o600); }
  return path;
}

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-knowledge-restore-")); t.after(() => rm(root, { recursive: true, force: true }));
  if (process.platform !== "win32") await chmod(root, 0o700);
  const vaultId = "knowledgevault-abcdef123456", historicalKey = Buffer.alloc(32, 31), currentKey = Buffer.alloc(32, 32);
  const historical = join(root, "historical"), active = join(root, "active"), staged = join(root, "staged");
  await initializeKnowledgeVault({ root: historical, vaultId, masterKey: historicalKey, now: new Date(baseTime) });
  const ingestion = buildKnowledgeIngestion({ ownerId: "owner-one", clientId: "client-one", title: "Delete me", text: "This historical private source must never return.", classification: "restricted", now: new Date(baseTime + 1), suffix: "000000000001" });
  const stored = await ingestKnowledgeText({ root: historical, vaultId, masterKey: historicalKey, ingestion, title: "Delete me", text: "This historical private source must never return.", now: new Date(baseTime + 2) });
  await cp(historical, active, { recursive: true, preserveTimestamps: true });
  const historicalCredential = await credential(root, "historical-key", historicalKey), currentCredential = await credential(root, "current-key", currentKey);
  return { root, vaultId, historicalKey, currentKey, historical, active, staged, stored, historicalCredential, currentCredential };
}

test("restore preparation propagates current tombstones before rotating historical encrypted state", async (t) => {
  const value = await fixture(t);
  const deletion = await buildKnowledgeDeletion({ root: value.active, vaultId: value.vaultId, masterKey: value.historicalKey, ownerId: "owner-one", clientId: "client-one", sourceId: value.stored.receipt.sourceId, now: new Date(baseTime + 10), suffix: "000000000010" });
  await deleteKnowledgeSource({ root: value.active, vaultId: value.vaultId, masterKey: value.historicalKey, deletion, now: new Date(baseTime + 11) });
  await rotateKnowledgeVaultKey({ root: value.active, vaultId: value.vaultId, oldMasterKey: value.historicalKey, newMasterKey: value.currentKey, now: new Date(baseTime + 12), suffix: "000000000012" });
  await cp(value.historical, value.staged, { recursive: true, preserveTimestamps: true });
  const suffixes = ["000000000020", "000000000021"];
  const receipt = await prepareKnowledgeVaultRestore({ authoritativeRoot: value.active, restoredRoot: value.staged, vaultId: value.vaultId, authoritativeCredentialPath: value.currentCredential, restoredCredentialPath: value.historicalCredential, targetCredentialPath: value.currentCredential, now: new Date(baseTime + 20), suffixFactory: () => suffixes.shift() });
  assert.equal(receipt.status, "pass"); assert.equal(receipt.tombstonesApplied, 1); assert.equal(receipt.rotatedToCurrentKey, true); assert.equal(receipt.sources, 0); assert.equal(receipt.deletions, 1); assert.equal(receipt.plaintextExposed, false);
  await auditKnowledgeVault({ root: value.staged, vaultId: value.vaultId, masterKey: value.currentKey, deep: true });
  await assert.rejects(() => auditKnowledgeVault({ root: value.staged, vaultId: value.vaultId, masterKey: value.historicalKey, deep: true }), /master key identity/u);
});

test("clean-host adoption rotates a staged vault and verify remains content-free", async (t) => {
  const value = await fixture(t); await cp(value.historical, value.staged, { recursive: true, preserveTimestamps: true });
  const receipt = await prepareKnowledgeVaultRestore({ restoredRoot: value.staged, vaultId: value.vaultId, restoredCredentialPath: value.historicalCredential, targetCredentialPath: value.currentCredential, now: new Date(baseTime + 30), suffixFactory: () => "000000000030" });
  assert.equal(receipt.authoritativeDeletionReconciled, false); assert.equal(receipt.rotatedToCurrentKey, true); assert.equal(receipt.sources, 1);
  const verified = await runKnowledgeVaultRestoreCommand(["verify", "--vault", value.staged, "--vault-id", value.vaultId, "--credential", value.currentCredential]);
  assert.equal(verified.status, "pass"); assert.doesNotMatch(JSON.stringify(verified), /Delete me|historical private source/u);
});

test("wrong historical credentials fail before staged mutation", async (t) => {
  const value = await fixture(t); await cp(value.historical, value.staged, { recursive: true, preserveTimestamps: true });
  const before = await readFile(join(value.staged, "vault.json"), "utf8");
  await assert.rejects(() => prepareKnowledgeVaultRestore({ restoredRoot: value.staged, vaultId: value.vaultId, restoredCredentialPath: value.currentCredential, targetCredentialPath: value.currentCredential }), /master key identity/u);
  assert.equal(await readFile(join(value.staged, "vault.json"), "utf8"), before);
});
