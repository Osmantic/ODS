import assert from "node:assert/strict";
import { readdir } from "node:fs/promises";
import { join, resolve } from "node:path";

import {
  auditKnowledgeVault, buildKnowledgeDeletion, buildKnowledgeIngestion, deleteKnowledgeSource,
  ingestKnowledgeText, initializeKnowledgeVault, loadKnowledgeVaultKeyCredential, rotateKnowledgeVaultKey,
} from "../../../deploy/work-controller/knowledge-vault.mjs";

const VAULT_RE = /^knowledgevault-[a-f0-9]{12}$/u;
const SOURCE_RE = /^workknowledgesource-[0-9]{13}-[a-f0-9]{12}$/u;

function parse(argv) {
  const command = argv[0], required = command === "initialize" ? ["--vault", "--vault-id", "--historical-key"]
    : command === "delete-rotate" ? ["--vault", "--vault-id", "--historical-key", "--current-key"]
      : command === "verify" ? ["--vault", "--vault-id", "--current-key"]
        : command === "credential-audit" ? ["--vault", "--vault-id"] : null;
  assert.ok(required && argv.length === required.length * 2 + 1, "invalid knowledge backup probe command");
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    assert.ok(required.includes(argv[index]) && !Object.hasOwn(values, argv[index]), "invalid knowledge backup probe option");
    values[argv[index]] = argv[index] === "--vault-id" ? argv[index + 1] : resolve(argv[index + 1]);
  }
  assert.match(values["--vault-id"], VAULT_RE);
  return { command, values };
}

async function sourceId(root) {
  const entries = await readdir(join(root, "sources", "client-one"));
  assert.equal(entries.length, 1); assert.match(entries[0], SOURCE_RE); return entries[0];
}

async function main() {
  const { command, values } = parse(process.argv.slice(2)), root = values["--vault"], vaultId = values["--vault-id"];
  if (command === "initialize") {
    const historicalKey = await loadKnowledgeVaultKeyCredential({ credentialPath: values["--historical-key"] }), now = new Date();
    await initializeKnowledgeVault({ root, vaultId, masterKey: historicalKey, now });
    const ingestion = buildKnowledgeIngestion({ ownerId: "owner-one", clientId: "client-one", title: "Historical recovery canary", text: "Deleted private recovery canary must never return after restore.", classification: "restricted", now: new Date(now.getTime() + 1), suffix: "a10000000001" });
    await ingestKnowledgeText({ root, vaultId, masterKey: historicalKey, ingestion, title: "Historical recovery canary", text: "Deleted private recovery canary must never return after restore.", now: new Date(now.getTime() + 2) });
    process.stdout.write('{"status":"pass","operation":"initialize","sources":1}\n'); return;
  }
  if (command === "delete-rotate") {
    const [historicalKey, currentKey] = await Promise.all([
      loadKnowledgeVaultKeyCredential({ credentialPath: values["--historical-key"] }), loadKnowledgeVaultKeyCredential({ credentialPath: values["--current-key"] }),
    ]), now = new Date(), selectedSource = await sourceId(root);
    const deletion = await buildKnowledgeDeletion({ root, vaultId, masterKey: historicalKey, ownerId: "owner-one", clientId: "client-one", sourceId: selectedSource, now, suffix: "a20000000001" });
    await deleteKnowledgeSource({ root, vaultId, masterKey: historicalKey, deletion, now: new Date(now.getTime() + 1) });
    await rotateKnowledgeVaultKey({ root, vaultId, oldMasterKey: historicalKey, newMasterKey: currentKey, now: new Date(now.getTime() + 2), suffix: "a20000000002" });
    process.stdout.write('{"status":"pass","operation":"delete-rotate","sources":0,"deletions":1}\n'); return;
  }
  if (command === "credential-audit") {
    assert.ok(process.env.CREDENTIALS_DIRECTORY, "systemd credentials directory is unavailable");
    const credentialPath = join(resolve(process.env.CREDENTIALS_DIRECTORY), "pixel-knowledge-vault-key");
    const projectedKey = await loadKnowledgeVaultKeyCredential({ credentialPath }), audit = await auditKnowledgeVault({ root, vaultId, masterKey: projectedKey, deep: true });
    assert.equal(audit.sources, 1); assert.equal(audit.deletions, 0);
    process.stdout.write('{"status":"pass","operation":"credential-audit","sources":1,"deletions":0,"credentialProjected":true}\n'); return;
  }
  const currentKey = await loadKnowledgeVaultKeyCredential({ credentialPath: values["--current-key"] });
  const audit = await auditKnowledgeVault({ root, vaultId, masterKey: currentKey, deep: true });
  assert.equal(audit.sources, 0); assert.equal(audit.deletions, 1);
  assert.ok((await readdir(join(root, "reconciliations"))).length >= 1); assert.ok((await readdir(join(root, "key-rotations"))).length >= 1);
  process.stdout.write('{"status":"pass","operation":"verify","sources":0,"deletions":1,"reconciled":true,"rotated":true}\n');
}

main().catch((error) => { process.stderr.write(`knowledge-backup-probe: ${error.message}\n`); process.exitCode = 1; });
