import { randomBytes, timingSafeEqual } from "node:crypto";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  auditKnowledgeVault, loadKnowledgeVaultKeyCredential, reconcileKnowledgeVaultDeletionLedger, rotateKnowledgeVaultKey,
  WorkKnowledgeVaultError,
} from "./knowledge-vault.mjs";

const VAULT_RE = /^knowledgevault-[a-f0-9]{12}$/u;
const commands = Object.freeze({
  adopt: ["--restored-vault", "--vault-id", "--restored-credential", "--target-credential"],
  reconcile: ["--authoritative-vault", "--restored-vault", "--vault-id", "--authoritative-credential", "--restored-credential", "--target-credential"],
  verify: ["--vault", "--vault-id", "--credential"],
});
const boundary = "Offline local restore hook only. It deep-audits one staged vault, propagates an active vault's authoritative tombstones before activation, rotates historical wrapping to the current external key when needed, and emits content-free evidence. It grants no service restart, general restore, network, cross-vault, external-effect, scope-expansion, or completion authority.";

export class KnowledgeVaultRestoreError extends Error {}

function fail(message) { throw new KnowledgeVaultRestoreError(message); }
function exactArguments(argv) {
  const command = argv?.[0], required = commands[command];
  if (!required || argv.length !== required.length * 2 + 1) fail("Usage: knowledge-vault-restore-cli.mjs adopt|reconcile|verify with the exact documented options");
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const option = argv[index], value = argv[index + 1];
    if (!required.includes(option) || Object.hasOwn(values, option) || typeof value !== "string" || !value || /[\0\r\n]/u.test(value)) fail("knowledge restore arguments are invalid, unknown, duplicated, or incomplete");
    values[option] = option === "--vault-id" ? value : resolve(value);
  }
  for (const option of required) if (!values[option]) fail(`knowledge restore is missing ${option}`);
  if (!VAULT_RE.test(values["--vault-id"] ?? "")) fail("knowledge restore vault identity is invalid");
  return { command, values };
}
function instant(value) {
  const observed = value instanceof Date ? value : new Date(value);
  if (!Number.isSafeInteger(observed.getTime())) fail("knowledge restore clock is invalid");
  return observed;
}
function suffix(factory) {
  const value = factory();
  if (!/^[a-f0-9]{12}$/u.test(value)) fail("knowledge restore identity suffix is invalid");
  return value;
}

export async function prepareKnowledgeVaultRestore({
  authoritativeRoot = null, restoredRoot, vaultId, authoritativeCredentialPath = null,
  restoredCredentialPath, targetCredentialPath, now = new Date(), suffixFactory = () => randomBytes(6).toString("hex"),
} = {}) {
  if (!VAULT_RE.test(vaultId ?? "") || typeof restoredRoot !== "string" || typeof restoredCredentialPath !== "string" || typeof targetCredentialPath !== "string" || typeof suffixFactory !== "function") fail("knowledge restore configuration is invalid");
  const restored = resolve(restoredRoot), authoritative = authoritativeRoot === null ? null : resolve(authoritativeRoot);
  if (restored !== restoredRoot || authoritative !== null && (authoritative !== authoritativeRoot || authoritative === restored)) fail("knowledge restore roots must be absolute, normalized, and separate");
  if (Boolean(authoritative) !== Boolean(authoritativeCredentialPath)) fail("knowledge restore authoritative vault and credential must be provided together");
  const observed = instant(now);
  const [restoredKey, targetKey, authoritativeKey] = await Promise.all([
    loadKnowledgeVaultKeyCredential({ credentialPath: restoredCredentialPath }),
    loadKnowledgeVaultKeyCredential({ credentialPath: targetCredentialPath }),
    authoritative ? loadKnowledgeVaultKeyCredential({ credentialPath: authoritativeCredentialPath }) : Promise.resolve(null),
  ]);
  if (authoritativeKey && !timingSafeEqual(authoritativeKey, targetKey)) fail("knowledge restore target key differs from the active authoritative key");
  await auditKnowledgeVault({ root: restored, vaultId, masterKey: restoredKey, deep: true });
  let reconciliation = null;
  if (authoritative) {
    await auditKnowledgeVault({ root: authoritative, vaultId, masterKey: authoritativeKey, deep: true });
    reconciliation = await reconcileKnowledgeVaultDeletionLedger({
      authoritativeRoot: authoritative, restoredRoot: restored, vaultId,
      authoritativeMasterKey: authoritativeKey, restoredMasterKey: restoredKey,
      now: observed, suffix: suffix(suffixFactory),
    });
  }
  let rotation = null;
  if (!timingSafeEqual(restoredKey, targetKey)) {
    rotation = await rotateKnowledgeVaultKey({
      root: restored, vaultId, oldMasterKey: restoredKey, newMasterKey: targetKey,
      now: new Date(observed.getTime() + (reconciliation ? 1 : 0)), suffix: suffix(suffixFactory),
    });
  }
  const audit = await auditKnowledgeVault({ root: restored, vaultId, masterKey: targetKey, deep: true });
  return Object.freeze({
    schemaVersion: 1, operation: authoritative ? "reconcile-restore" : "adopt-restore", status: "pass", vaultId,
    sources: audit.sources, chunks: audit.chunks, deletions: audit.deletions,
    authoritativeDeletionReconciled: authoritative !== null,
    tombstonesApplied: reconciliation?.receipt.applied.length ?? 0,
    tombstonesAlreadyPresent: reconciliation?.receipt.alreadyPresent.length ?? 0,
    rotatedToCurrentKey: rotation !== null, residualHistoricalKeyWrapping: false, plaintextExposed: false,
    authority: "none", boundary,
  });
}

export async function runKnowledgeVaultRestoreCommand(argv = process.argv.slice(2), dependencies = {}) {
  const { command, values } = exactArguments(argv), vaultId = values["--vault-id"];
  if (command === "verify") {
    const masterKey = await loadKnowledgeVaultKeyCredential({ credentialPath: values["--credential"] });
    const audit = await auditKnowledgeVault({ root: values["--vault"], vaultId, masterKey, deep: true });
    return Object.freeze({ schemaVersion: 1, operation: "verify-restored-vault", status: "pass", vaultId, sources: audit.sources, chunks: audit.chunks, deletions: audit.deletions, plaintextExposed: false, authority: "none", boundary });
  }
  return prepareKnowledgeVaultRestore({
    ...(command === "reconcile" ? { authoritativeRoot: values["--authoritative-vault"], authoritativeCredentialPath: values["--authoritative-credential"] } : {}),
    restoredRoot: values["--restored-vault"], vaultId,
    restoredCredentialPath: values["--restored-credential"], targetCredentialPath: values["--target-credential"],
    ...(dependencies.now ? { now: dependencies.now() } : {}), ...(dependencies.suffixFactory ? { suffixFactory: dependencies.suffixFactory } : {}),
  });
}

export async function main(argv = process.argv.slice(2)) { process.stdout.write(`${JSON.stringify(await runKnowledgeVaultRestoreCommand(argv))}\n`); }

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) main().catch((error) => { process.stderr.write(`pixel-work-knowledge-restore: ${error instanceof KnowledgeVaultRestoreError || error instanceof WorkKnowledgeVaultError ? error.message : "unexpected failure"}\n`); process.exitCode = 1; });

export const knowledgeVaultRestoreBoundary = boundary;
