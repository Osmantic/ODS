import {
  createCipheriv, createDecipheriv, createHash, createHmac, hkdfSync, randomBytes,
} from "node:crypto";
import { constants } from "node:fs";
import {
  cp, lstat, mkdir, open, readdir, rename, rm,
} from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";

import {
  canonical, validateWorkKnowledgeDeletion, validateWorkKnowledgeIngestion,
  validateWorkKnowledgeKeyRotation, validateWorkKnowledgeQuery, validateWorkKnowledgeReconciliation,
  validateWorkKnowledgeRetrieval, validateWorkKnowledgeSource,
} from "../../scripts/lib/work-contract.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const TENANT_RE = /^[a-z][a-z0-9-]{2,63}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const SOURCE_RE = /^workknowledgesource-[0-9]{13}-[a-f0-9]{12}$/u;
const CHUNK_RE = /^workknowledgesource-[0-9]{13}-[a-f0-9]{12}-[0-9]{6}$/u;
const VAULT_RE = /^knowledgevault-[a-f0-9]{12}$/u;
const ROTATION_RE = /^workknowledgekeyrotation-[0-9]{13}-[a-f0-9]{12}$/u;
const RECONCILIATION_RE = /^workknowledgereconcile-[0-9]{13}-[a-f0-9]{12}$/u;
const INGESTION_RE = /^workknowledgeingest-[0-9]{13}-[a-f0-9]{12}$/u;
const QUERY_RE = /^workknowledgequery-[0-9]{13}-[a-f0-9]{12}$/u;
const DELETION_RE = /^workknowledgedelete-[0-9]{13}-[a-f0-9]{12}$/u;
const MAX_SOURCE_BYTES = 1048576;
const MAX_QUERY_BYTES = 4096;
const CHUNK_CODE_POINTS = 1200;
const CHUNK_OVERLAP = 120;
const classificationRank = Object.freeze({ public: 0, internal: 1, confidential: 2, restricted: 3 });
const stopwords = new Set(["a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "were", "with"]);
const vaultBoundary = "Private local encrypted knowledge vault. It stores no provider credential and grants no instruction, tool, network, cross-client, external-effect, scope-expansion, or completion authority.";

export class WorkKnowledgeVaultError extends Error {}

export function generateKnowledgeVaultKey() { return randomBytes(32); }

function fail(message) { throw new WorkKnowledgeVaultError(message); }
function sha(value) { return createHash("sha256").update(Buffer.isBuffer(value) || typeof value === "string" ? value : canonical(value)).digest("hex"); }
function instant(value, label) { if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`); return value; }
function exactKeys(value, keys, label) { if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`); }
function tenant(value, label) { if (!TENANT_RE.test(value ?? "") || basename(value) !== value) fail(`${label} is invalid`); return value; }
function suffix(value) { if (!SUFFIX_RE.test(value ?? "")) fail("knowledge identifier suffix is invalid"); return value; }
function master(value) { if (!Buffer.isBuffer(value) || value.length !== 32) fail("knowledge vault master key must be exactly 32 bytes"); return value; }
function safeText(value, maximumBytes, label) {
  if (typeof value !== "string" || value.length < 1 || Buffer.byteLength(value, "utf8") > maximumBytes || Buffer.from(value, "utf8").toString("utf8") !== value || value.includes("\0")) fail(`${label} is invalid or oversized UTF-8 text`);
  return value;
}
function key(masterKey, vaultId, clientId, purpose) {
  return Buffer.from(hkdfSync("sha256", master(masterKey), Buffer.from(`${vaultId}\0${clientId}`, "utf8"), Buffer.from(`pixel-knowledge-${purpose}-v1`, "utf8"), 32));
}
function keyCheck(masterKey, vaultId) { return createHmac("sha256", key(masterKey, vaultId, "vault", "check")).update("pixel-knowledge-vault-key-check-v1").digest("hex"); }
function encrypt(keyBytes, plaintext, aad) {
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", keyBytes, iv);
  cipher.setAAD(Buffer.from(aad, "utf8"));
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  return { iv: iv.toString("base64"), ciphertext: ciphertext.toString("base64"), tag: cipher.getAuthTag().toString("base64") };
}
function decrypt(keyBytes, envelope, aad, label) {
  try {
    exactKeys(envelope, ["iv", "ciphertext", "tag"], label);
    const decipher = createDecipheriv("aes-256-gcm", keyBytes, Buffer.from(envelope.iv, "base64"));
    decipher.setAAD(Buffer.from(aad, "utf8")); decipher.setAuthTag(Buffer.from(envelope.tag, "base64"));
    return Buffer.concat([decipher.update(Buffer.from(envelope.ciphertext, "base64")), decipher.final()]);
  } catch { fail(`${label} authentication failed`); }
}
function tokenize(value) {
  const observed = value.normalize("NFKC").toLocaleLowerCase("en-US").match(/[\p{L}\p{N}][\p{L}\p{N}_-]{1,63}/gu) ?? [];
  return [...new Set(observed.filter((tokenValue) => !stopwords.has(tokenValue)))];
}
function termHmacs(searchKey, value, maximum, label) {
  const tokens = tokenize(value);
  if (tokens.length > maximum) fail(`${label} exceeds its searchable-term ceiling`);
  return tokens.map((tokenValue) => createHmac("sha256", searchKey).update(tokenValue, "utf8").digest("hex").slice(0, 32)).sort();
}
function chunkText(value) {
  const points = [...value];
  const chunks = [];
  for (let start = 0, index = 0; start < points.length; index += 1) {
    const end = Math.min(points.length, start + CHUNK_CODE_POINTS);
    chunks.push({ index, startCodePoint: start, endCodePoint: end, text: points.slice(start, end).join("") });
    if (end === points.length) break;
    start = end - CHUNK_OVERLAP;
    if (index >= 1023) fail("knowledge source exceeds its chunk ceiling");
  }
  return chunks;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { recursive: true, mode: 0o700 });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return path;
}
async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}
async function writeJsonExclusive(path, value) {
  const handle = await open(path, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
  await syncDirectory(resolve(path, ".."));
}
async function readPrivateJson(path, maximumBytes, label) {
  const noFollow = constants.O_NOFOLLOW ?? 0;
  const pathInfo = await lstat(path).catch(() => null);
  if (!pathInfo?.isFile() || pathInfo.isSymbolicLink()) fail(`${label} is not a real file`);
  let handle;
  try { handle = await open(path, constants.O_RDONLY | noFollow); } catch { fail(`${label} cannot be opened safely`); }
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.dev !== pathInfo.dev || before.ino !== pathInfo.ino || before.nlink !== 1 || before.size < 2 || before.size > maximumBytes || process.platform !== "win32" && (before.uid !== process.geteuid() || (before.mode & 0o077) !== 0)) fail(`${label} is not a private bounded regular file`);
    const bytes = await handle.readFile();
    const after = await handle.stat();
    if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size || bytes.length !== before.size) fail(`${label} changed while it was read`);
    try { return JSON.parse(bytes.toString("utf8")); } catch { fail(`${label} is not valid JSON`); }
  } finally { await handle.close(); }
}

function transactionPaths(rootValue) {
  const root = resolve(rootValue), parent = resolve(root, ".."), name = basename(root);
  if (root === parent) fail("knowledge vault root cannot be a filesystem root");
  return {
    root, parent,
    journal: join(parent, `.${name}.knowledge-transaction.json`),
    stage: join(parent, `.${name}.knowledge-transaction-stage`),
    previous: join(parent, `.${name}.knowledge-transaction-previous`),
  };
}
async function transactionResidue(paths) {
  const result = {};
  for (const name of ["journal", "stage", "previous"]) result[name] = await lstat(paths[name]).catch(() => null);
  return result;
}
async function requireNoTransaction(rootValue) {
  const paths = transactionPaths(rootValue), residue = await transactionResidue(paths);
  if (residue.journal || residue.stage || residue.previous) fail("knowledge vault has an unfinished atomic transaction");
  return paths;
}
async function replacePrivateJson(path, value) {
  const temporary = `${path}.replace-${randomBytes(6).toString("hex")}`;
  await writeJsonExclusive(temporary, value);
  try { await rm(path, { force: false }); await rename(temporary, path); }
  catch (error) { await rm(temporary, { force: true }).catch(() => {}); throw error; }
  await syncDirectory(resolve(path, ".."));
}

export async function loadKnowledgeVaultKeyCredential({ credentialPath, expectedName = "pixel-knowledge-vault-key" }) {
  const path = resolve(credentialPath);
  if (basename(path) !== expectedName || basename(expectedName) !== expectedName) fail("knowledge vault credential filename differs");
  const noFollow = constants.O_NOFOLLOW ?? 0, pathInfo = await lstat(path).catch(() => null);
  if (!pathInfo?.isFile() || pathInfo.isSymbolicLink()) fail("knowledge vault credential is not a real file");
  let systemdProjection = false;
  if (process.platform === "linux") {
    const directory = process.env.CREDENTIALS_DIRECTORY;
    if (typeof directory === "string" && resolve(directory) === directory && dirname(directory) === "/run/credentials" && path === join(directory, expectedName)) {
      const [directoryInfo, credentialRootInfo] = await Promise.all([
        lstat(directory).catch(() => null), lstat("/run/credentials").catch(() => null),
      ]);
      systemdProjection = Boolean(
        directoryInfo?.isDirectory() && !directoryInfo.isSymbolicLink() && directoryInfo.uid === 0 && directoryInfo.gid === 0 && (directoryInfo.mode & 0o227) === 0
        && credentialRootInfo?.isDirectory() && !credentialRootInfo.isSymbolicLink() && credentialRootInfo.uid === 0 && credentialRootInfo.gid === 0 && (credentialRootInfo.mode & 0o022) === 0,
      );
    }
  }
  let handle;
  try { handle = await open(path, constants.O_RDONLY | noFollow); } catch { fail("knowledge vault credential cannot be opened safely"); }
  try {
    const before = await handle.stat();
    const ownerPrivate = process.platform === "win32" || before.uid === process.geteuid() && (before.mode & 0o077) === 0;
    const systemdPrivate = systemdProjection && before.uid === 0 && before.gid === 0 && (before.mode & 0o337) === 0 && (before.mode & 0o440) !== 0;
    if (!before.isFile() || before.dev !== pathInfo.dev || before.ino !== pathInfo.ino || before.nlink !== 1 || before.size !== 65 || !ownerPrivate && !systemdPrivate) fail("knowledge vault credential is not a private single-link file or a read-only systemd projection");
    const bytes = await handle.readFile(), after = await handle.stat();
    if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size || bytes.length !== before.size) fail("knowledge vault credential changed while it was read");
    const encoded = bytes.toString("ascii");
    if (!/^[a-f0-9]{64}\n$/u.test(encoded)) fail("knowledge vault credential is not canonical lower-case hex");
    return Buffer.from(encoded.slice(0, 64), "hex");
  } finally { await handle.close(); }
}

async function vault(rootValue, vaultId, create = false, now = new Date(), masterKey = null, allowTransaction = false) {
  if (!VAULT_RE.test(vaultId ?? "")) fail("knowledge vault identity is invalid");
  const root = resolve(rootValue);
  await privateDirectory(root, "knowledge vault root", create);
  if (!allowTransaction) await requireNoTransaction(root);
  const configPath = join(root, "vault.json");
  if (create) master(masterKey);
  const config = { schemaVersion: 1, vaultId, createdAt: instant(now, "vault creation time").toISOString(), keyCheckSha256: create ? keyCheck(masterKey, vaultId) : null, boundary: vaultBoundary };
  if (create) await writeJsonExclusive(configPath, config).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const stored = await readPrivateJson(configPath, 4096, "knowledge vault configuration");
  exactKeys(stored, ["schemaVersion", "vaultId", "createdAt", "keyCheckSha256", "boundary"], "knowledge vault configuration");
  if (stored.schemaVersion !== 1 || stored.vaultId !== vaultId || stored.boundary !== vaultBoundary || !Number.isSafeInteger(Date.parse(stored.createdAt)) || !SHA_RE.test(stored.keyCheckSha256 ?? "")) fail("knowledge vault configuration differs");
  if (masterKey !== null && keyCheck(masterKey, vaultId) !== stored.keyCheckSha256) fail("knowledge vault master key identity differs");
  for (const name of ["ingestion-claims", "retrieval-claims", "deletion-claims", "sources", "deletions", "key-rotations", "reconciliations"]) await privateDirectory(join(root, name), `knowledge vault ${name}`, create);
  return { root, vaultId, config: stored };
}

export async function initializeKnowledgeVault({ root, vaultId, masterKey, now = new Date() }) { return vault(root, vaultId, true, now, masterKey); }

export function buildKnowledgeIngestion({ ownerId, clientId, title, text, classification, originType = "manual-text", originIdentifier = title, capturedAt = null, deleteAfter = null, approverId = ownerId, now = new Date(), expiresAt = new Date(now.getTime() + 60000), suffix: idSuffix = randomBytes(6).toString("hex") }) {
  instant(now, "knowledge ingestion issue time"); instant(expiresAt, "knowledge ingestion expiry time"); suffix(idSuffix); tenant(ownerId, "knowledge owner"); tenant(clientId, "knowledge client"); tenant(approverId, "knowledge approver");
  capturedAt = capturedAt ?? now;
  safeText(title, 2000, "knowledge source title"); if (title.length > 500) fail("knowledge source title exceeds its character ceiling"); safeText(text, MAX_SOURCE_BYTES, "knowledge source"); safeText(originIdentifier, 4096, "knowledge provenance identifier"); instant(capturedAt, "knowledge capture time");
  const timestamp = String(now.getTime()).padStart(13, "0");
  const value = {
    $schema: "https://osmantic.com/pixel/schemas/work-knowledge-ingestion-v1.schema.json", schemaVersion: 1,
    ingestionId: `workknowledgeingest-${timestamp}-${idSuffix}`, sourceId: `workknowledgesource-${timestamp}-${idSuffix}`,
    ownerId, clientId, issuedAt: now.toISOString(), expiresAt: expiresAt.toISOString(), singleUse: true, classification, parser: "plain-text-v1", provenance: { originType, identifierSha256: sha(originIdentifier), capturedAt: capturedAt.toISOString() },
    source: { titleSha256: sha(title), contentSha256: sha(text), byteLength: Buffer.byteLength(text, "utf8") }, retention: { deleteAfter: deleteAfter === null ? null : instant(deleteAfter, "knowledge retention time").toISOString() },
    consent: { approved: true, approverId, approvedAt: now.toISOString() },
    authority: { localVaultWrite: true, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: "Explicit single-use authorization to ingest one exact local text source into one owner and client knowledge partition. It grants no network, cross-client read, external-effect, scope-expansion, or completion authority.",
  };
  const errors = validateWorkKnowledgeIngestion(value); if (errors.length) fail(`knowledge ingestion is invalid: ${errors[0]}`); return value;
}

function sourceAad(vaultId, sourceId, purpose, classification, contentSha256) { return canonical({ vaultId, sourceId, purpose, classification, contentSha256 }); }

export async function ingestKnowledgeText({ root, vaultId, masterKey, ingestion, title, text, now = new Date() }) {
  const context = await vault(root, vaultId, false, now, masterKey); const observed = instant(now, "knowledge ingestion time");
  const errors = validateWorkKnowledgeIngestion(ingestion); if (errors.length) fail(`knowledge ingestion is invalid: ${errors[0]}`);
  if (observed.getTime() < Date.parse(ingestion.issuedAt) || observed.getTime() >= Date.parse(ingestion.expiresAt)) fail("knowledge ingestion authorization is not current");
  if (ingestion.consent.approverId !== ingestion.ownerId) fail("knowledge ingestion requires the exact owner approval");
  title = safeText(title, 2000, "knowledge source title"); if (title.length > 500) fail("knowledge source title exceeds its character ceiling"); text = safeText(text, MAX_SOURCE_BYTES, "knowledge source");
  if (sha(title) !== ingestion.source.titleSha256 || sha(text) !== ingestion.source.contentSha256 || Buffer.byteLength(text, "utf8") !== ingestion.source.byteLength) fail("knowledge source differs from its exact ingestion authorization");
  tenant(ingestion.ownerId, "knowledge owner"); tenant(ingestion.clientId, "knowledge client");
  const searchKey = key(masterKey, vaultId, ingestion.clientId, "search");
  const preparedChunks = chunkText(text).map((chunk) => ({ ...chunk, termHmacs: termHmacs(searchKey, `${title}\n${chunk.text}`, 512, "knowledge chunk") }));
  if (!preparedChunks.some((chunk) => chunk.termHmacs.length > 0)) fail("knowledge source contains no searchable terms");
  const claimPath = join(context.root, "ingestion-claims", `${ingestion.ingestionId}.json`);
  try { await writeJsonExclusive(claimPath, ingestion); } catch (error) { if (error?.code === "EEXIST") fail("knowledge ingestion authorization was already consumed"); throw error; }
  const clientRoot = join(context.root, "sources", ingestion.clientId); await privateDirectory(clientRoot, "knowledge client source root", true);
  const destination = join(clientRoot, ingestion.sourceId);
  if (await lstat(destination).catch(() => null)) fail("knowledge source identity already exists");
  const stage = join(clientRoot, `.stage-${ingestion.ingestionId}`); await privateDirectory(stage, "knowledge source staging root", true);
  const chunksRoot = join(stage, "chunks"); await privateDirectory(chunksRoot, "knowledge chunk staging root", true);
  const sourceKey = randomBytes(32), wrappingKey = key(masterKey, vaultId, ingestion.clientId, "wrap");
  const wrappedKey = encrypt(wrappingKey, sourceKey, sourceAad(vaultId, ingestion.sourceId, "source-key", ingestion.classification, ingestion.source.contentSha256));
  const encryptedTitle = encrypt(sourceKey, Buffer.from(title, "utf8"), sourceAad(vaultId, ingestion.sourceId, "title", ingestion.classification, ingestion.source.contentSha256));
  const chunks = [];
  for (const chunk of preparedChunks) {
    const chunkId = `${ingestion.sourceId}-${String(chunk.index).padStart(6, "0")}`;
    const fileName = `${String(chunk.index).padStart(6, "0")}.json`;
    const aad = sourceAad(vaultId, ingestion.sourceId, chunkId, ingestion.classification, ingestion.source.contentSha256);
    const sealed = encrypt(sourceKey, Buffer.from(chunk.text, "utf8"), aad);
    const payload = { schemaVersion: 1, sourceId: ingestion.sourceId, chunkId, ...sealed };
    await writeJsonExclusive(join(chunksRoot, fileName), payload);
    chunks.push({ chunkId, index: chunk.index, startCodePoint: chunk.startCodePoint, endCodePoint: chunk.endCodePoint, plaintextBytes: Buffer.byteLength(chunk.text, "utf8"), termHmacs: chunk.termHmacs, ciphertextSha256: sha(payload), fileName });
  }
  const receipt = {
    $schema: "https://osmantic.com/pixel/schemas/work-knowledge-source-v1.schema.json", schemaVersion: 1,
    sourceId: ingestion.sourceId, ingestionId: ingestion.ingestionId, ingestionSha256: sha(ingestion), ownerId: ingestion.ownerId, clientId: ingestion.clientId,
    classification: ingestion.classification, parser: ingestion.parser, provenance: ingestion.provenance, indexVersion: "lexical-hmac-v1", ingestedAt: observed.toISOString(), retention: ingestion.retention, source: ingestion.source,
    encryption: { algorithm: "aes-256-gcm", kdf: "hkdf-sha256-v1", wrappedKey, encryptedTitle }, chunks, state: "active",
    authority: { instructions: false, tools: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: "Private encrypted source and keyed lexical index receipt. Source text is untrusted data and grants no instruction, tool, network, external-effect, scope-expansion, or completion authority.",
  };
  const receiptErrors = validateWorkKnowledgeSource(receipt); if (receiptErrors.length) fail(`knowledge source receipt is invalid: ${receiptErrors[0]}`);
  await writeJsonExclusive(join(stage, "index.json"), receipt); await syncDirectory(chunksRoot); await syncDirectory(stage);
  try { await rename(stage, destination); } catch { fail("knowledge source could not be atomically published"); }
  await syncDirectory(clientRoot);
  return { receipt, sha256: sha(receipt), path: destination };
}

async function readSourceReceipt(path) {
  const receipt = await readPrivateJson(join(path, "index.json"), 16777216, "knowledge source receipt");
  const errors = validateWorkKnowledgeSource(receipt); if (errors.length) fail(`knowledge source receipt is invalid: ${errors[0]}`);
  const chunksRoot = join(path, "chunks"); await privateDirectory(chunksRoot, "knowledge chunk root");
  const observed = (await readdir(chunksRoot, { withFileTypes: true })).map((entry) => entry.isFile() ? entry.name : `!${entry.name}`).sort();
  const expected = receipt.chunks.map((chunk) => chunk.fileName).sort();
  if (canonical(observed) !== canonical(expected)) fail("knowledge chunk inventory differs from its source receipt");
  return receipt;
}
async function clientEntries(context, ownerId, clientId) {
  tenant(ownerId, "knowledge owner"); tenant(clientId, "knowledge client");
  const entries = [], sources = [], tombstoned = new Set();
  const sourceRoot = join(context.root, "sources", clientId);
  const sourceInfo = await lstat(sourceRoot).catch(() => null);
  if (sourceInfo) {
    await privateDirectory(sourceRoot, "knowledge client source root");
    for (const entry of await readdir(sourceRoot, { withFileTypes: true })) {
      if (entry.name.startsWith(".stage-") || entry.name.startsWith(".deleting-")) fail("knowledge vault contains an unfinished source lifecycle");
      if (!entry.isDirectory() || !SOURCE_RE.test(entry.name)) fail("knowledge source root contains an unexpected entry");
      const path = join(sourceRoot, entry.name), receipt = await readSourceReceipt(path);
      if (receipt.sourceId !== entry.name || receipt.clientId !== clientId) fail("knowledge source partition binding differs");
      if (receipt.ownerId === ownerId) { const receiptSha256 = sha(receipt); entries.push({ kind: "source", id: receipt.sourceId, sha256: receiptSha256 }); sources.push({ path, receipt, receiptSha256 }); }
    }
  }
  const deletionRoot = join(context.root, "deletions", clientId);
  const deletionInfo = await lstat(deletionRoot).catch(() => null);
  if (deletionInfo) {
    await privateDirectory(deletionRoot, "knowledge client deletion root");
    for (const entry of await readdir(deletionRoot, { withFileTypes: true })) {
      if (!entry.isFile() || !/^workknowledgesource-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(entry.name)) fail("knowledge deletion root contains an unexpected entry");
      const deletion = await readPrivateJson(join(deletionRoot, entry.name), 32768, "knowledge deletion tombstone"), errors = validateWorkKnowledgeDeletion(deletion);
      if (errors.length || deletion.status !== "deleted" || deletion.clientId !== clientId) fail("knowledge deletion tombstone is invalid");
      if (deletion.ownerId === ownerId) { entries.push({ kind: "deletion", id: deletion.sourceId, sha256: sha(deletion) }); tombstoned.add(deletion.sourceId); }
    }
  }
  if (sources.some((source) => tombstoned.has(source.receipt.sourceId))) fail("knowledge vault retains a tombstoned source");
  entries.sort((a, b) => `${a.kind}:${a.id}`.localeCompare(`${b.kind}:${b.id}`));
  return { entries, sources };
}

export async function knowledgeVaultHead({ root, vaultId, masterKey, ownerId, clientId }) {
  master(masterKey);
  const context = await vault(root, vaultId, false, new Date(), masterKey), observed = await clientEntries(context, ownerId, clientId);
  return { sha256: sha({ schemaVersion: 1, vaultId, ownerId, clientId, entries: observed.entries }), sourceCount: observed.sources.length, entries: observed.entries };
}

export async function knowledgeVaultLifecycleHead({ root, vaultId, masterKey }) {
  master(masterKey); const context = await vault(root, vaultId, false, new Date(), masterKey), entries = [];
  for (const clientEntry of (await readdir(join(context.root, "sources"), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (!clientEntry.isDirectory() || !TENANT_RE.test(clientEntry.name)) fail("knowledge lifecycle head found an invalid source partition");
    for (const sourceEntry of (await readdir(join(context.root, "sources", clientEntry.name), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      if (!sourceEntry.isDirectory() || !SOURCE_RE.test(sourceEntry.name)) fail("knowledge lifecycle head requires a clean source lifecycle");
      const receipt = await readSourceReceipt(join(context.root, "sources", clientEntry.name, sourceEntry.name));
      if (receipt.clientId !== clientEntry.name || receipt.sourceId !== sourceEntry.name) fail("knowledge lifecycle source binding differs");
      entries.push({ kind: "source", clientId: receipt.clientId, sourceId: receipt.sourceId, sha256: sha(receipt) });
    }
  }
  for (const entry of await authoritativeDeletionEntries(context)) entries.push({ kind: "deletion", clientId: entry.tombstone.clientId, sourceId: entry.tombstone.sourceId, sha256: entry.sha256 });
  entries.sort((a, b) => `${a.kind}:${a.clientId}:${a.sourceId}`.localeCompare(`${b.kind}:${b.clientId}:${b.sourceId}`));
  return { sha256: sha({ schemaVersion: 1, vaultId, keyCheckSha256: context.config.keyCheckSha256, entries }), sourceCount: entries.filter((entry) => entry.kind === "source").length, deletionCount: entries.filter((entry) => entry.kind === "deletion").length };
}

export async function inspectKnowledgeSource({ root, vaultId, masterKey, ownerId, clientId, sourceId }) {
  master(masterKey); tenant(ownerId, "knowledge owner"); tenant(clientId, "knowledge client");
  if (!SOURCE_RE.test(sourceId ?? "")) fail("knowledge source identity is invalid");
  const context = await vault(root, vaultId, false, new Date(), masterKey), receipt = await readSourceReceipt(join(context.root, "sources", clientId, sourceId));
  if (receipt.ownerId !== ownerId || receipt.clientId !== clientId || receipt.sourceId !== sourceId) fail("knowledge source inspection partition differs");
  const sourceKey = unwrapSourceKey(masterKey, vaultId, receipt), title = decrypt(sourceKey, receipt.encryption.encryptedTitle, sourceAad(vaultId, receipt.sourceId, "title", receipt.classification, receipt.source.contentSha256), "knowledge title").toString("utf8");
  if (sha(title) !== receipt.source.titleSha256) fail("knowledge source title differs during inspection");
  return {
    sourceId, ownerId, clientId, title, classification: receipt.classification, retention: receipt.retention,
    provenance: receipt.provenance, contentSha256: receipt.source.contentSha256, byteLength: receipt.source.byteLength,
    receiptSha256: sha(receipt), authority: "none",
  };
}

export async function buildKnowledgeQuery({ root, vaultId, masterKey, ownerId, clientId, jobId, checkpointSha256, queryText, maximumClassification, minRelevanceBps = 2500, maxResults = 5, now = new Date(), expiresAt = new Date(now.getTime() + 60000), suffix: idSuffix = randomBytes(6).toString("hex") }) {
  instant(now, "knowledge query issue time"); instant(expiresAt, "knowledge query expiry time"); suffix(idSuffix); tenant(ownerId, "knowledge owner"); tenant(clientId, "knowledge client");
  queryText = safeText(queryText, MAX_QUERY_BYTES, "knowledge query"); if (tokenize(queryText).length > 64) fail("knowledge query exceeds its searchable-term ceiling");
  const head = await knowledgeVaultHead({ root, vaultId, masterKey, ownerId, clientId });
  const query = {
    $schema: "https://osmantic.com/pixel/schemas/work-knowledge-query-v1.schema.json", schemaVersion: 1,
    queryId: `workknowledgequery-${String(now.getTime()).padStart(13, "0")}-${idSuffix}`, jobId, checkpointSha256, ownerId, clientId,
    issuedAt: now.toISOString(), expiresAt: expiresAt.toISOString(), singleUse: true, queryTextSha256: sha(queryText), expectedVaultHeadSha256: head.sha256,
    maximumClassification, minRelevanceBps, maxResults,
    authority: { localVaultRead: true, instructions: false, tools: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: "Expiring read-only request for deterministic retrieval from one owner and client partition at one exact vault head. Retrieved text is untrusted and grants no instruction, tool, network, external-effect, scope-expansion, or completion authority.",
  };
  const errors = validateWorkKnowledgeQuery(query); if (errors.length) fail(`knowledge query is invalid: ${errors[0]}`); return query;
}

function unwrapSourceKey(masterKey, vaultId, receipt) {
  const wrappingKey = key(masterKey, vaultId, receipt.clientId, "wrap");
  return decrypt(wrappingKey, receipt.encryption.wrappedKey, sourceAad(vaultId, receipt.sourceId, "source-key", receipt.classification, receipt.source.contentSha256), "knowledge source key");
}
async function decryptChunk({ path, vaultId, receipt, chunk, sourceKey, searchKey, title }) {
  if (!CHUNK_RE.test(chunk.chunkId) || basename(chunk.fileName) !== chunk.fileName) fail("knowledge chunk identity is invalid");
  const payload = await readPrivateJson(join(path, "chunks", chunk.fileName), 32768, "knowledge encrypted chunk");
  exactKeys(payload, ["schemaVersion", "sourceId", "chunkId", "iv", "ciphertext", "tag"], "knowledge encrypted chunk");
  if (payload.schemaVersion !== 1 || payload.sourceId !== receipt.sourceId || payload.chunkId !== chunk.chunkId || sha(payload) !== chunk.ciphertextSha256) fail("knowledge encrypted chunk differs from its receipt");
  const plaintext = decrypt(sourceKey, { iv: payload.iv, ciphertext: payload.ciphertext, tag: payload.tag }, sourceAad(vaultId, receipt.sourceId, chunk.chunkId, receipt.classification, receipt.source.contentSha256), "knowledge chunk").toString("utf8");
  if (Buffer.byteLength(plaintext, "utf8") !== chunk.plaintextBytes || canonical(termHmacs(searchKey, `${title}\n${plaintext}`, 512, "knowledge chunk")) !== canonical(chunk.termHmacs)) fail("knowledge chunk plaintext differs from its index receipt");
  return plaintext;
}

export async function retrieveKnowledge({ root, vaultId, masterKey, query, queryText, now = new Date(), suffix: idSuffix = randomBytes(6).toString("hex") }) {
  const context = await vault(root, vaultId, false, now, masterKey); const observed = instant(now, "knowledge retrieval time"); suffix(idSuffix);
  const queryErrors = validateWorkKnowledgeQuery(query); if (queryErrors.length) fail(`knowledge query is invalid: ${queryErrors[0]}`);
  queryText = safeText(queryText, MAX_QUERY_BYTES, "knowledge query");
  if (sha(queryText) !== query.queryTextSha256 || observed.getTime() < Date.parse(query.issuedAt) || observed.getTime() >= Date.parse(query.expiresAt)) fail("knowledge query text or lifetime differs");
  const terms = termHmacs(key(masterKey, vaultId, query.clientId, "search"), queryText, 64, "knowledge query");
  const current = await clientEntries(context, query.ownerId, query.clientId), head = sha({ schemaVersion: 1, vaultId, ownerId: query.ownerId, clientId: query.clientId, entries: current.entries });
  if (head !== query.expectedVaultHeadSha256) fail("knowledge vault head changed after query authorization");
  const candidates = [];
  for (const source of current.sources) {
    const receipt = source.receipt;
    if (classificationRank[receipt.classification] > classificationRank[query.maximumClassification] || receipt.retention.deleteAfter !== null && observed.getTime() >= Date.parse(receipt.retention.deleteAfter)) continue;
    for (const chunk of receipt.chunks) {
      const indexed = new Set(chunk.termHmacs), matched = terms.filter((term) => indexed.has(term)).length;
      const scoreBps = terms.length === 0 ? 0 : Math.floor(matched * 10000 / terms.length);
      if (scoreBps >= query.minRelevanceBps) candidates.push({ source, chunk, scoreBps });
    }
  }
  const claimPath = join(context.root, "retrieval-claims", `${query.queryId}.json`);
  try { await writeJsonExclusive(claimPath, query); } catch (error) { if (error?.code === "EEXIST") fail("knowledge query was already consumed"); throw error; }
  candidates.sort((a, b) => b.scoreBps - a.scoreBps || `${a.source.receipt.sourceId}:${a.chunk.chunkId}`.localeCompare(`${b.source.receipt.sourceId}:${b.chunk.chunkId}`));
  const results = [];
  for (const candidate of candidates.slice(0, query.maxResults)) {
    const receipt = candidate.source.receipt, sourceKey = unwrapSourceKey(masterKey, vaultId, receipt), searchKey = key(masterKey, vaultId, receipt.clientId, "search");
    const title = decrypt(sourceKey, receipt.encryption.encryptedTitle, sourceAad(vaultId, receipt.sourceId, "title", receipt.classification, receipt.source.contentSha256), "knowledge title").toString("utf8");
    if (sha(title) !== receipt.source.titleSha256) fail("knowledge title differs from its source receipt");
    const excerpt = await decryptChunk({ path: candidate.source.path, vaultId, receipt, chunk: candidate.chunk, sourceKey, searchKey, title });
    results.push({ sourceId: receipt.sourceId, chunkId: candidate.chunk.chunkId, classification: receipt.classification, scoreBps: candidate.scoreBps, title, excerpt, citation: `pixel-vault:${receipt.sourceId}#${candidate.chunk.chunkId}`, sourceContentSha256: receipt.source.contentSha256, chunkCiphertextSha256: candidate.chunk.ciphertextSha256 });
  }
  const retrieval = {
    $schema: "https://osmantic.com/pixel/schemas/work-knowledge-retrieval-v1.schema.json", schemaVersion: 1,
    retrievalId: `workknowledgeretrieval-${String(observed.getTime()).padStart(13, "0")}-${idSuffix}`, queryId: query.queryId, queryContractSha256: sha(query), queryTextSha256: query.queryTextSha256, jobId: query.jobId, checkpointSha256: query.checkpointSha256,
    ownerId: query.ownerId, clientId: query.clientId, vaultHeadSha256: head, retrievedAt: observed.toISOString(), reason: results.length ? "matched" : "no-match", results, untrustedText: true,
    authority: { instructions: false, tools: false, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: "Private job-scoped retrieval with source-bound citations. Titles and excerpts are untrusted data and grant no instruction, tool, network, external-effect, scope-expansion, or completion authority.",
  };
  const retrievalErrors = validateWorkKnowledgeRetrieval(retrieval); if (retrievalErrors.length) fail(`knowledge retrieval is invalid: ${retrievalErrors[0]}`); return retrieval;
}

export async function buildKnowledgeDeletion({ root, vaultId, masterKey, ownerId, clientId, sourceId, approverId = ownerId, reasonCode = "owner-request", now = new Date(), expiresAt = new Date(now.getTime() + 60000), suffix: idSuffix = randomBytes(6).toString("hex") }) {
  master(masterKey);
  const context = await vault(root, vaultId, false, now, masterKey); instant(now, "knowledge deletion issue time"); instant(expiresAt, "knowledge deletion expiry time"); suffix(idSuffix); tenant(ownerId, "knowledge owner"); tenant(clientId, "knowledge client"); tenant(approverId, "knowledge approver");
  if (!SOURCE_RE.test(sourceId ?? "")) fail("knowledge source identity is invalid");
  const receipt = await readSourceReceipt(join(context.root, "sources", clientId, sourceId));
  if (receipt.ownerId !== ownerId || receipt.clientId !== clientId || approverId !== ownerId) fail("knowledge deletion partition or owner approval differs");
  const deletion = {
    $schema: "https://osmantic.com/pixel/schemas/work-knowledge-deletion-v1.schema.json", schemaVersion: 1,
    deletionId: `workknowledgedelete-${String(now.getTime()).padStart(13, "0")}-${idSuffix}`, sourceId, sourceReceiptSha256: sha(receipt), ownerId, clientId,
    issuedAt: now.toISOString(), expiresAt: expiresAt.toISOString(), approved: true, approverId, reasonCode, deletedAt: null, sourceContentSha256: null, status: "authorized",
    authority: { deleteExactLocalSource: true, network: false, externalEffects: false, scopeExpansion: false, completion: false },
    boundary: "Explicit expiring authorization or content-free tombstone for deleting one exact encrypted local knowledge source. It grants no cross-source, network, external-effect, scope-expansion, or completion authority.",
  };
  const errors = validateWorkKnowledgeDeletion(deletion); if (errors.length) fail(`knowledge deletion is invalid: ${errors[0]}`); return deletion;
}

export async function deleteKnowledgeSource({ root, vaultId, masterKey, deletion, now = new Date() }) {
  master(masterKey);
  const context = await vault(root, vaultId, false, now, masterKey), observed = instant(now, "knowledge deletion time"), errors = validateWorkKnowledgeDeletion(deletion);
  if (errors.length || deletion.status !== "authorized" || observed.getTime() < Date.parse(deletion.issuedAt) || observed.getTime() >= Date.parse(deletion.expiresAt) || deletion.approverId !== deletion.ownerId) fail("knowledge deletion authorization is invalid or expired");
  const sourceRoot = join(context.root, "sources", deletion.clientId), sourcePath = join(sourceRoot, deletion.sourceId), receipt = await readSourceReceipt(sourcePath);
  if (receipt.ownerId !== deletion.ownerId || receipt.clientId !== deletion.clientId || sha(receipt) !== deletion.sourceReceiptSha256) fail("knowledge deletion source differs from its authorization");
  const claimPath = join(context.root, "deletion-claims", `${deletion.deletionId}.json`);
  const execution = { schemaVersion: 1, deletionId: deletion.deletionId, startedAt: observed.toISOString(), authorizationSha256: sha(deletion), authorization: deletion };
  try { await writeJsonExclusive(claimPath, execution); } catch (error) { if (error?.code === "EEXIST") fail("knowledge deletion authorization was already consumed"); throw error; }
  const deletingPath = join(sourceRoot, `.deleting-${deletion.deletionId}`);
  try { await rename(sourcePath, deletingPath); } catch { fail("knowledge source could not enter atomic deletion state"); }
  await syncDirectory(sourceRoot);
  const deletionRoot = join(context.root, "deletions", deletion.clientId); await privateDirectory(deletionRoot, "knowledge client deletion root", true);
  const tombstone = { ...deletion, deletedAt: observed.toISOString(), sourceContentSha256: receipt.source.contentSha256, status: "deleted" };
  const tombstoneErrors = validateWorkKnowledgeDeletion(tombstone); if (tombstoneErrors.length) fail(`knowledge deletion tombstone is invalid: ${tombstoneErrors[0]}`);
  await writeJsonExclusive(join(deletionRoot, `${deletion.sourceId}.json`), tombstone);
  await rm(deletingPath, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 }); await syncDirectory(sourceRoot);
  return { tombstone, sha256: sha(tombstone), residualCiphertext: false, backupPropagationRequired: true };
}

function validateDeletionExecution(value) {
  exactKeys(value, ["schemaVersion", "deletionId", "startedAt", "authorizationSha256", "authorization"], "knowledge deletion execution");
  const errors = validateWorkKnowledgeDeletion(value.authorization);
  const started = Date.parse(value.startedAt);
  if (value.schemaVersion !== 1 || value.deletionId !== value.authorization.deletionId || value.authorizationSha256 !== sha(value.authorization) || errors.length || value.authorization.status !== "authorized" || !Number.isSafeInteger(started) || started < Date.parse(value.authorization.issuedAt) || started >= Date.parse(value.authorization.expiresAt)) fail("knowledge deletion execution is invalid");
  return value.authorization;
}

export async function recoverKnowledgeVaultLifecycle({ root, vaultId, masterKey, now = new Date() }) {
  master(masterKey); const context = await vault(root, vaultId, false, now, masterKey), observed = instant(now, "knowledge recovery time");
  let discardedIngestions = 0, resumedDeletions = 0, cleanedDeletions = 0;
  for (const clientEntry of await readdir(join(context.root, "sources"), { withFileTypes: true })) {
    if (!clientEntry.isDirectory() || !TENANT_RE.test(clientEntry.name)) fail("knowledge recovery found an invalid client partition");
    const sourceRoot = join(context.root, "sources", clientEntry.name);
    for (const entry of await readdir(sourceRoot, { withFileTypes: true })) {
      if (!entry.name.startsWith(".stage-")) continue;
      if (!entry.isDirectory()) fail("knowledge recovery found a linked ingestion stage");
      const ingestionId = entry.name.slice(".stage-".length), claim = await readPrivateJson(join(context.root, "ingestion-claims", `${ingestionId}.json`), 32768, "knowledge ingestion claim"), errors = validateWorkKnowledgeIngestion(claim);
      if (errors.length || claim.ingestionId !== ingestionId || claim.clientId !== clientEntry.name) fail("knowledge recovery found an unbound ingestion stage");
      await rm(join(sourceRoot, entry.name), { recursive: true, force: false, maxRetries: 2, retryDelay: 20 }); discardedIngestions += 1;
    }
    await syncDirectory(sourceRoot);
  }
  for (const entry of await readdir(join(context.root, "deletion-claims"), { withFileTypes: true })) {
    if (!entry.isFile() || !/^workknowledgedelete-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(entry.name)) fail("knowledge recovery found an invalid deletion execution file");
    const execution = await readPrivateJson(join(context.root, "deletion-claims", entry.name), 65536, "knowledge deletion execution"), deletion = validateDeletionExecution(execution);
    const sourceRoot = join(context.root, "sources", deletion.clientId); await privateDirectory(sourceRoot, "knowledge recovery source root");
    const sourcePath = join(sourceRoot, deletion.sourceId), deletingPath = join(sourceRoot, `.deleting-${deletion.deletionId}`);
    const sourceExists = Boolean(await lstat(sourcePath).catch(() => null)), deletingExists = Boolean(await lstat(deletingPath).catch(() => null));
    const deletionRoot = join(context.root, "deletions", deletion.clientId), tombstonePath = join(deletionRoot, `${deletion.sourceId}.json`), tombstoneExists = Boolean(await lstat(tombstonePath).catch(() => null));
    if (sourceExists && deletingExists || tombstoneExists && sourceExists) fail("knowledge recovery found an ambiguous deletion state");
    if (tombstoneExists) {
      const tombstone = await readPrivateJson(tombstonePath, 32768, "knowledge deletion tombstone"), errors = validateWorkKnowledgeDeletion(tombstone);
      if (errors.length || tombstone.status !== "deleted" || tombstone.deletionId !== deletion.deletionId || tombstone.sourceReceiptSha256 !== deletion.sourceReceiptSha256) fail("knowledge recovery found a substituted deletion tombstone");
      if (deletingExists) { await rm(deletingPath, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 }); cleanedDeletions += 1; }
      continue;
    }
    if (sourceExists) { await rename(sourcePath, deletingPath); resumedDeletions += 1; }
    else if (!deletingExists) fail("knowledge recovery cannot locate an authorized deletion source");
    const receipt = await readSourceReceipt(deletingPath);
    if (receipt.ownerId !== deletion.ownerId || receipt.clientId !== deletion.clientId || sha(receipt) !== deletion.sourceReceiptSha256) fail("knowledge recovery deletion source differs from its authorization");
    await privateDirectory(deletionRoot, "knowledge recovery deletion root", true);
    const tombstone = { ...deletion, deletedAt: execution.startedAt, sourceContentSha256: receipt.source.contentSha256, status: "deleted" }, errors = validateWorkKnowledgeDeletion(tombstone);
    if (errors.length) fail(`knowledge recovery tombstone is invalid: ${errors[0]}`);
    await writeJsonExclusive(tombstonePath, tombstone);
    await rm(deletingPath, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 }); cleanedDeletions += 1;
    await syncDirectory(sourceRoot);
  }
  for (const clientEntry of await readdir(join(context.root, "sources"), { withFileTypes: true })) {
    if (!clientEntry.isDirectory()) fail("knowledge recovery found an invalid source partition");
    for (const entry of await readdir(join(context.root, "sources", clientEntry.name), { withFileTypes: true })) if (entry.name.startsWith(".stage-") || entry.name.startsWith(".deleting-")) fail("knowledge recovery left an unfinished lifecycle");
  }
  return { recoveredAt: observed.toISOString(), discardedIngestions, resumedDeletions, cleanedDeletions, residualCiphertext: false, authority: "none" };
}

export async function purgeExpiredKnowledgeSources({ root, vaultId, masterKey, now = new Date() }) {
  master(masterKey); const context = await vault(root, vaultId, false, now, masterKey), observed = instant(now, "knowledge retention purge time"), removed = [];
  for (const clientEntry of await readdir(join(context.root, "sources"), { withFileTypes: true })) {
    if (!clientEntry.isDirectory() || !TENANT_RE.test(clientEntry.name)) fail("knowledge retention purge found an invalid client partition");
    const sourceRoot = join(context.root, "sources", clientEntry.name);
    for (const sourceEntry of await readdir(sourceRoot, { withFileTypes: true })) {
      if (!sourceEntry.isDirectory() || !SOURCE_RE.test(sourceEntry.name)) fail("knowledge retention purge requires a clean source lifecycle");
      const receipt = await readSourceReceipt(join(sourceRoot, sourceEntry.name));
      if (receipt.retention.deleteAfter === null || observed.getTime() < Date.parse(receipt.retention.deleteAfter)) continue;
      const deletion = await buildKnowledgeDeletion({ root, vaultId, masterKey, ownerId: receipt.ownerId, clientId: receipt.clientId, sourceId: receipt.sourceId, approverId: receipt.ownerId, reasonCode: "retention-expired", now: observed, expiresAt: new Date(observed.getTime() + 60000), suffix: randomBytes(6).toString("hex") });
      const result = await deleteKnowledgeSource({ root, vaultId, masterKey, deletion, now: observed }); removed.push({ sourceId: receipt.sourceId, tombstoneSha256: result.sha256 });
    }
  }
  removed.sort((a, b) => a.sourceId.localeCompare(b.sourceId));
  return { purgedAt: observed.toISOString(), removed, backupPropagationRequired: removed.length > 0, authority: "none" };
}

async function auditKnowledgeVaultInternal({ root, vaultId, masterKey = null, deep = false, allowTransaction = false }) {
  const context = await vault(root, vaultId, false, new Date(), masterKey, allowTransaction); if (deep) master(masterKey);
  let sources = 0, chunks = 0, deletions = 0;
  for (const clientEntry of await readdir(join(context.root, "sources"), { withFileTypes: true })) {
    if (!clientEntry.isDirectory() || !TENANT_RE.test(clientEntry.name)) fail("knowledge vault source partitions are invalid");
    const clientRoot = join(context.root, "sources", clientEntry.name); await privateDirectory(clientRoot, "knowledge client source root");
    for (const sourceEntry of await readdir(clientRoot, { withFileTypes: true })) {
      if (!sourceEntry.isDirectory() || !SOURCE_RE.test(sourceEntry.name)) fail("knowledge vault has unfinished or unexpected source state");
      const path = join(clientRoot, sourceEntry.name), receipt = await readSourceReceipt(path); sources += 1; chunks += receipt.chunks.length;
      if (receipt.clientId !== clientEntry.name || receipt.sourceId !== sourceEntry.name) fail("knowledge source partition differs during audit");
      const deletionPath = join(context.root, "deletions", receipt.clientId, `${receipt.sourceId}.json`);
      if (await lstat(deletionPath).catch(() => null)) fail("knowledge vault retains a tombstoned source");
      if (deep) {
        const sourceKey = unwrapSourceKey(masterKey, vaultId, receipt), searchKey = key(masterKey, vaultId, receipt.clientId, "search");
        const title = decrypt(sourceKey, receipt.encryption.encryptedTitle, sourceAad(vaultId, receipt.sourceId, "title", receipt.classification, receipt.source.contentSha256), "knowledge title").toString("utf8");
        if (sha(title) !== receipt.source.titleSha256) fail("knowledge title differs during deep audit");
        const recovered = [];
        for (const chunk of receipt.chunks) {
          const text = await decryptChunk({ path, vaultId, receipt, chunk, sourceKey, searchKey, title }), points = [...text];
          if (chunk.index === 0) recovered.push(...points); else recovered.push(...points.slice(receipt.chunks[chunk.index - 1].endCodePoint - chunk.startCodePoint));
        }
        const content = recovered.join("");
        if (sha(content) !== receipt.source.contentSha256 || Buffer.byteLength(content, "utf8") !== receipt.source.byteLength) fail("knowledge source differs during deep audit");
      }
    }
  }
  for (const clientEntry of await readdir(join(context.root, "deletions"), { withFileTypes: true })) {
    if (!clientEntry.isDirectory() || !TENANT_RE.test(clientEntry.name)) fail("knowledge vault deletion partitions are invalid");
    await privateDirectory(join(context.root, "deletions", clientEntry.name), "knowledge vault deletion partition");
    for (const entry of await readdir(join(context.root, "deletions", clientEntry.name), { withFileTypes: true })) {
      const value = await readPrivateJson(join(context.root, "deletions", clientEntry.name, entry.name), 32768, "knowledge deletion tombstone"), errors = validateWorkKnowledgeDeletion(value);
      if (!entry.isFile() || errors.length || value.status !== "deleted" || value.clientId !== clientEntry.name || `${value.sourceId}.json` !== entry.name) fail("knowledge deletion tombstone differs during audit");
      deletions += 1;
    }
  }
  for (const entry of await readdir(join(context.root, "ingestion-claims"), { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".json") || !INGESTION_RE.test(entry.name.slice(0, -5))) fail("knowledge ingestion claim ledger contains an unexpected entry");
    const value = await readPrivateJson(join(context.root, "ingestion-claims", entry.name), 32768, "knowledge ingestion claim"), errors = validateWorkKnowledgeIngestion(value);
    if (errors.length || `${value.ingestionId}.json` !== entry.name) fail("knowledge ingestion claim differs during audit");
  }
  for (const entry of await readdir(join(context.root, "retrieval-claims"), { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".json") || !QUERY_RE.test(entry.name.slice(0, -5))) fail("knowledge retrieval claim ledger contains an unexpected entry");
    const value = await readPrivateJson(join(context.root, "retrieval-claims", entry.name), 32768, "knowledge retrieval claim"), errors = validateWorkKnowledgeQuery(value);
    if (errors.length || `${value.queryId}.json` !== entry.name) fail("knowledge retrieval claim differs during audit");
  }
  for (const entry of await readdir(join(context.root, "deletion-claims"), { withFileTypes: true })) {
    if (!entry.isFile() || !entry.name.endsWith(".json") || !DELETION_RE.test(entry.name.slice(0, -5))) fail("knowledge deletion claim ledger contains an unexpected entry");
    const value = await readPrivateJson(join(context.root, "deletion-claims", entry.name), 65536, "knowledge deletion execution"), authorization = validateDeletionExecution(value);
    if (`${authorization.deletionId}.json` !== entry.name) fail("knowledge deletion execution differs during audit");
  }
  for (const entry of await readdir(join(context.root, "key-rotations"), { withFileTypes: true })) {
    if (!entry.isFile() || !ROTATION_RE.test(entry.name.replace(/\.json$/u, "")) || !entry.name.endsWith(".json")) fail("knowledge key rotation ledger contains an unexpected entry");
    const value = await readPrivateJson(join(context.root, "key-rotations", entry.name), 33554432, "knowledge key rotation receipt"), errors = validateWorkKnowledgeKeyRotation(value);
    if (errors.length || `${value.rotationId}.json` !== entry.name || value.vaultId !== vaultId || value.state !== "committed") fail("knowledge key rotation receipt differs during audit");
  }
  for (const entry of await readdir(join(context.root, "reconciliations"), { withFileTypes: true })) {
    if (!entry.isFile() || !RECONCILIATION_RE.test(entry.name.replace(/\.json$/u, "")) || !entry.name.endsWith(".json")) fail("knowledge reconciliation ledger contains an unexpected entry");
    const value = await readPrivateJson(join(context.root, "reconciliations", entry.name), 33554432, "knowledge reconciliation receipt"), errors = validateWorkKnowledgeReconciliation(value);
    if (errors.length || `${value.reconciliationId}.json` !== entry.name || value.vaultId !== vaultId || value.state !== "committed") fail("knowledge reconciliation receipt differs during audit");
  }
  return { vaultId, sources, chunks, deletions, deep, authority: "none" };
}

export async function auditKnowledgeVault(args) { return auditKnowledgeVaultInternal(args); }

function validateKnowledgeTransactionJournal(value, vaultId) {
  exactKeys(value, ["schemaVersion", "kind", "operationId", "vaultId", "preparedAt", "currentKeyCheckSha256", "targetKeyCheckSha256"], "knowledge vault transaction journal");
  if (value.schemaVersion !== 1 || !["key-rotation", "deletion-reconciliation"].includes(value.kind) || value.vaultId !== vaultId || !Number.isSafeInteger(Date.parse(value.preparedAt)) || !SHA_RE.test(value.currentKeyCheckSha256 ?? "") || !SHA_RE.test(value.targetKeyCheckSha256 ?? "")) fail("knowledge vault transaction journal differs");
  if (value.kind === "key-rotation" && !ROTATION_RE.test(value.operationId ?? "") || value.kind === "deletion-reconciliation" && !RECONCILIATION_RE.test(value.operationId ?? "")) fail("knowledge vault transaction identity differs");
  return value;
}

async function publishStagedVault({ paths, journal, currentMasterKey, targetMasterKey }) {
  await writeJsonExclusive(paths.journal, journal);
  let movedCurrent = false, published = false;
  try {
    await rename(paths.root, paths.previous); movedCurrent = true; await syncDirectory(paths.parent);
    await rename(paths.stage, paths.root); published = true; await syncDirectory(paths.parent);
    await auditKnowledgeVaultInternal({ root: paths.root, vaultId: journal.vaultId, masterKey: targetMasterKey, deep: true, allowTransaction: true });
    await rm(paths.previous, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 });
    await rm(paths.journal, { force: false }); await syncDirectory(paths.parent);
  } catch (error) {
    try {
      if (published) await rm(paths.root, { recursive: true, force: true, maxRetries: 2, retryDelay: 20 });
      if (movedCurrent && await lstat(paths.previous).catch(() => null)) await rename(paths.previous, paths.root);
      await rm(paths.stage, { recursive: true, force: true, maxRetries: 2, retryDelay: 20 });
      await rm(paths.journal, { force: true }); await syncDirectory(paths.parent);
      await auditKnowledgeVaultInternal({ root: paths.root, vaultId: journal.vaultId, masterKey: currentMasterKey, deep: true, allowTransaction: true });
    } catch { fail("knowledge vault atomic transaction rollback requires recovery"); }
    throw error;
  }
}

export async function recoverKnowledgeVaultTransaction({ root, vaultId, currentMasterKey, targetMasterKey = currentMasterKey }) {
  master(currentMasterKey); master(targetMasterKey);
  const paths = transactionPaths(root); await privateDirectory(paths.parent, "knowledge vault transaction parent");
  const residue = await transactionResidue(paths);
  if (!residue.journal) {
    if (residue.stage && !residue.previous && await lstat(paths.root).catch(() => null)) {
      await auditKnowledgeVaultInternal({ root: paths.root, vaultId, masterKey: currentMasterKey, deep: true, allowTransaction: true });
      await rm(paths.stage, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 }); await syncDirectory(paths.parent);
      return { state: "discarded-unpublished-stage", authority: "none" };
    }
    if (!residue.stage && !residue.previous) return { state: "clean", authority: "none" };
    fail("knowledge vault transaction residue has no authoritative journal");
  }
  const journal = validateKnowledgeTransactionJournal(await readPrivateJson(paths.journal, 4096, "knowledge vault transaction journal"), vaultId);
  if (journal.currentKeyCheckSha256 !== keyCheck(currentMasterKey, vaultId) || journal.targetKeyCheckSha256 !== keyCheck(targetMasterKey, vaultId)) fail("knowledge vault recovery key identities differ from the journal");
  const rootInfo = await lstat(paths.root).catch(() => null), stageInfo = await lstat(paths.stage).catch(() => null), previousInfo = await lstat(paths.previous).catch(() => null);
  for (const [name, info] of [["root", rootInfo], ["stage", stageInfo], ["previous", previousInfo]]) if (info && (!info.isDirectory() || info.isSymbolicLink())) fail(`knowledge vault transaction ${name} is not a real directory`);
  if (rootInfo && stageInfo && !previousInfo) {
    await auditKnowledgeVaultInternal({ root: paths.root, vaultId, masterKey: currentMasterKey, deep: true, allowTransaction: true });
    await rm(paths.stage, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 });
    await rm(paths.journal, { force: false }); await syncDirectory(paths.parent);
    return { state: "rolled-back-before-publication", operationId: journal.operationId, authority: "none" };
  }
  if (!rootInfo && stageInfo && previousInfo) {
    await auditKnowledgeVaultInternal({ root: paths.previous, vaultId, masterKey: currentMasterKey, deep: true, allowTransaction: true });
    await auditKnowledgeVaultInternal({ root: paths.stage, vaultId, masterKey: targetMasterKey, deep: true, allowTransaction: true });
    await rename(paths.stage, paths.root); await syncDirectory(paths.parent);
    await rm(paths.previous, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 });
    await rm(paths.journal, { force: false }); await syncDirectory(paths.parent);
    return { state: "rolled-forward", operationId: journal.operationId, authority: "none" };
  }
  if (rootInfo && !stageInfo && previousInfo) {
    await auditKnowledgeVaultInternal({ root: paths.root, vaultId, masterKey: targetMasterKey, deep: true, allowTransaction: true });
    await auditKnowledgeVaultInternal({ root: paths.previous, vaultId, masterKey: currentMasterKey, deep: true, allowTransaction: true });
    await rm(paths.previous, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 });
    await rm(paths.journal, { force: false }); await syncDirectory(paths.parent);
    return { state: "finalized-publication", operationId: journal.operationId, authority: "none" };
  }
  if (rootInfo && !stageInfo && !previousInfo) {
    await auditKnowledgeVaultInternal({ root: paths.root, vaultId, masterKey: targetMasterKey, deep: true, allowTransaction: true });
    await rm(paths.journal, { force: false }); await syncDirectory(paths.parent);
    return { state: "finalized-journal", operationId: journal.operationId, authority: "none" };
  }
  fail("knowledge vault transaction state is ambiguous");
}

export async function rotateKnowledgeVaultKey({ root, vaultId, oldMasterKey, newMasterKey, now = new Date(), suffix: idSuffix = randomBytes(6).toString("hex") }) {
  master(oldMasterKey); master(newMasterKey); const observed = instant(now, "knowledge key rotation time"); suffix(idSuffix);
  const oldCheck = keyCheck(oldMasterKey, vaultId), newCheck = keyCheck(newMasterKey, vaultId);
  if (oldCheck === newCheck) fail("knowledge key rotation requires a distinct new key");
  await auditKnowledgeVaultInternal({ root, vaultId, masterKey: oldMasterKey, deep: true });
  const paths = await requireNoTransaction(root); await privateDirectory(paths.parent, "knowledge vault transaction parent");
  try { await cp(paths.root, paths.stage, { recursive: true, errorOnExist: true, force: false, preserveTimestamps: true }); }
  catch (error) { await rm(paths.stage, { recursive: true, force: true }).catch(() => {}); throw error; }
  const rotationId = `workknowledgekeyrotation-${String(observed.getTime()).padStart(13, "0")}-${idSuffix}`, sources = [];
  try {
    const context = await vault(paths.stage, vaultId, false, observed, oldMasterKey);
    for (const clientEntry of (await readdir(join(context.root, "sources"), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      if (!clientEntry.isDirectory() || !TENANT_RE.test(clientEntry.name)) fail("knowledge key rotation found an invalid client partition");
      const clientRoot = join(context.root, "sources", clientEntry.name);
      for (const sourceEntry of (await readdir(clientRoot, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
        if (!sourceEntry.isDirectory() || !SOURCE_RE.test(sourceEntry.name)) fail("knowledge key rotation requires a clean source lifecycle");
        const path = join(clientRoot, sourceEntry.name), receipt = await readSourceReceipt(path), beforeReceiptSha256 = sha(receipt);
        const sourceKey = unwrapSourceKey(oldMasterKey, vaultId, receipt), oldSearchKey = key(oldMasterKey, vaultId, receipt.clientId, "search"), newSearchKey = key(newMasterKey, vaultId, receipt.clientId, "search");
        const title = decrypt(sourceKey, receipt.encryption.encryptedTitle, sourceAad(vaultId, receipt.sourceId, "title", receipt.classification, receipt.source.contentSha256), "knowledge title").toString("utf8");
        if (sha(title) !== receipt.source.titleSha256) fail("knowledge title differs during key rotation");
        const rotatedChunks = [];
        for (const chunk of receipt.chunks) {
          const plaintext = await decryptChunk({ path, vaultId, receipt, chunk, sourceKey, searchKey: oldSearchKey, title });
          rotatedChunks.push({ ...chunk, termHmacs: termHmacs(newSearchKey, `${title}\n${plaintext}`, 512, "knowledge chunk") });
        }
        const wrappedKey = encrypt(key(newMasterKey, vaultId, receipt.clientId, "wrap"), sourceKey, sourceAad(vaultId, receipt.sourceId, "source-key", receipt.classification, receipt.source.contentSha256));
        const rotated = { ...receipt, encryption: { ...receipt.encryption, wrappedKey }, chunks: rotatedChunks };
        const errors = validateWorkKnowledgeSource(rotated); if (errors.length) fail(`rotated knowledge source receipt is invalid: ${errors[0]}`);
        await replacePrivateJson(join(path, "index.json"), rotated);
        sources.push({ sourceId: receipt.sourceId, clientId: receipt.clientId, beforeReceiptSha256, afterReceiptSha256: sha(rotated) });
      }
    }
    sources.sort((a, b) => `${a.clientId}:${a.sourceId}`.localeCompare(`${b.clientId}:${b.sourceId}`));
    const config = await readPrivateJson(join(context.root, "vault.json"), 4096, "knowledge vault configuration");
    await replacePrivateJson(join(context.root, "vault.json"), { ...config, keyCheckSha256: newCheck });
    const receipt = {
      $schema: "https://osmantic.com/pixel/schemas/work-knowledge-key-rotation-v1.schema.json", schemaVersion: 1, rotationId, vaultId, rotatedAt: observed.toISOString(),
      oldKeyCheckSha256: oldCheck, newKeyCheckSha256: newCheck, sources, state: "committed",
      authority: { localKeyRotation: true, network: false, externalEffects: false, scopeExpansion: false, completion: false },
      boundary: "Content-free receipt for one atomic local knowledge-vault key rotation. It contains no key or plaintext and grants no network, cross-vault, external-effect, scope-expansion, or completion authority.",
    };
    const errors = validateWorkKnowledgeKeyRotation(receipt); if (errors.length) fail(`knowledge key rotation receipt is invalid: ${errors[0]}`);
    await writeJsonExclusive(join(context.root, "key-rotations", `${rotationId}.json`), receipt);
    await auditKnowledgeVaultInternal({ root: paths.stage, vaultId, masterKey: newMasterKey, deep: true });
    const journal = { schemaVersion: 1, kind: "key-rotation", operationId: rotationId, vaultId, preparedAt: observed.toISOString(), currentKeyCheckSha256: oldCheck, targetKeyCheckSha256: newCheck };
    await publishStagedVault({ paths, journal, currentMasterKey: oldMasterKey, targetMasterKey: newMasterKey });
    return { receipt, sha256: sha(receipt), residualOldKeyWrapping: false, authority: "none" };
  } catch (error) {
    await rm(paths.stage, { recursive: true, force: true, maxRetries: 2, retryDelay: 20 }).catch(() => {});
    throw error;
  }
}

async function authoritativeDeletionEntries(context) {
  const entries = [];
  for (const clientEntry of (await readdir(join(context.root, "deletions"), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
    if (!clientEntry.isDirectory() || !TENANT_RE.test(clientEntry.name)) fail("authoritative knowledge deletion partition is invalid");
    for (const entry of (await readdir(join(context.root, "deletions", clientEntry.name), { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      const tombstone = await readPrivateJson(join(context.root, "deletions", clientEntry.name, entry.name), 32768, "authoritative knowledge deletion tombstone"), errors = validateWorkKnowledgeDeletion(tombstone);
      if (!entry.isFile() || errors.length || tombstone.status !== "deleted" || tombstone.clientId !== clientEntry.name || `${tombstone.sourceId}.json` !== entry.name) fail("authoritative knowledge deletion tombstone differs");
      entries.push({ tombstone, sha256: sha(tombstone) });
    }
  }
  entries.sort((a, b) => `${a.tombstone.clientId}:${a.tombstone.sourceId}`.localeCompare(`${b.tombstone.clientId}:${b.tombstone.sourceId}`));
  return entries;
}

export async function reconcileKnowledgeVaultDeletionLedger({ authoritativeRoot, restoredRoot, vaultId, authoritativeMasterKey, restoredMasterKey, now = new Date(), suffix: idSuffix = randomBytes(6).toString("hex") }) {
  master(authoritativeMasterKey); master(restoredMasterKey); const observed = instant(now, "knowledge deletion reconciliation time"); suffix(idSuffix);
  const authoritative = resolve(authoritativeRoot), restored = resolve(restoredRoot), authoritativeIdentity = process.platform === "win32" ? authoritative.toLocaleLowerCase("en-US") : authoritative, restoredIdentity = process.platform === "win32" ? restored.toLocaleLowerCase("en-US") : restored;
  if (authoritativeIdentity === restoredIdentity || authoritativeIdentity.startsWith(`${restoredIdentity}\\`) || restoredIdentity.startsWith(`${authoritativeIdentity}\\`) || authoritativeIdentity.startsWith(`${restoredIdentity}/`) || restoredIdentity.startsWith(`${authoritativeIdentity}/`)) fail("knowledge reconciliation roots must be separate and non-nested");
  await auditKnowledgeVaultInternal({ root: authoritative, vaultId, masterKey: authoritativeMasterKey, deep: true });
  await auditKnowledgeVaultInternal({ root: restored, vaultId, masterKey: restoredMasterKey, deep: true });
  const authoritativeContext = await vault(authoritative, vaultId, false, observed, authoritativeMasterKey), tombstones = await authoritativeDeletionEntries(authoritativeContext);
  const paths = await requireNoTransaction(restored); await privateDirectory(paths.parent, "knowledge vault transaction parent");
  try { await cp(paths.root, paths.stage, { recursive: true, errorOnExist: true, force: false, preserveTimestamps: true }); }
  catch (error) { await rm(paths.stage, { recursive: true, force: true }).catch(() => {}); throw error; }
  const reconciliationId = `workknowledgereconcile-${String(observed.getTime()).padStart(13, "0")}-${idSuffix}`, applied = [], alreadyPresent = [];
  try {
    const stageContext = await vault(paths.stage, vaultId, false, observed, restoredMasterKey);
    for (const entry of tombstones) {
      const tombstone = entry.tombstone, deletionRoot = join(stageContext.root, "deletions", tombstone.clientId), tombstonePath = join(deletionRoot, `${tombstone.sourceId}.json`);
      const existing = await lstat(tombstonePath).catch(() => null);
      if (existing) {
        const restoredTombstone = await readPrivateJson(tombstonePath, 32768, "restored knowledge deletion tombstone");
        if (canonical(restoredTombstone) !== canonical(tombstone)) fail("restored deletion tombstone conflicts with the authoritative ledger");
        alreadyPresent.push({ sourceId: tombstone.sourceId, clientId: tombstone.clientId, tombstoneSha256: entry.sha256 });
        continue;
      }
      let matchedSource = null;
      for (const clientEntry of await readdir(join(stageContext.root, "sources"), { withFileTypes: true })) {
        if (!clientEntry.isDirectory() || !TENANT_RE.test(clientEntry.name)) fail("restored knowledge source partition is invalid");
        const candidate = join(stageContext.root, "sources", clientEntry.name, tombstone.sourceId), info = await lstat(candidate).catch(() => null);
        if (!info) continue;
        if (matchedSource || clientEntry.name !== tombstone.clientId || !info.isDirectory() || info.isSymbolicLink()) fail("restored knowledge source conflicts with the authoritative deletion partition");
        matchedSource = candidate;
      }
      if (matchedSource) {
        const receipt = await readSourceReceipt(matchedSource);
        if (receipt.sourceId !== tombstone.sourceId || receipt.ownerId !== tombstone.ownerId || receipt.clientId !== tombstone.clientId || receipt.source.contentSha256 !== tombstone.sourceContentSha256) fail("restored knowledge source differs from the authoritative tombstone");
        const deleting = join(stageContext.root, "sources", tombstone.clientId, `.reconciling-${reconciliationId}-${tombstone.sourceId}`);
        await rename(matchedSource, deleting); await syncDirectory(resolve(matchedSource, ".."));
        await privateDirectory(deletionRoot, "restored knowledge deletion root", true); await writeJsonExclusive(tombstonePath, tombstone);
        await rm(deleting, { recursive: true, force: false, maxRetries: 2, retryDelay: 20 }); await syncDirectory(resolve(matchedSource, ".."));
      } else {
        await privateDirectory(deletionRoot, "restored knowledge deletion root", true); await writeJsonExclusive(tombstonePath, tombstone);
      }
      applied.push({ sourceId: tombstone.sourceId, clientId: tombstone.clientId, tombstoneSha256: entry.sha256 });
    }
    const receipt = {
      $schema: "https://osmantic.com/pixel/schemas/work-knowledge-reconciliation-v1.schema.json", schemaVersion: 1, reconciliationId, vaultId, reconciledAt: observed.toISOString(),
      authoritativeDeletionHeadSha256: sha(tombstones.map((entry) => ({ clientId: entry.tombstone.clientId, sourceId: entry.tombstone.sourceId, tombstoneSha256: entry.sha256 }))),
      applied, alreadyPresent, residualCiphertext: false, state: "committed",
      authority: { applyAuthoritativeTombstones: true, network: false, externalEffects: false, scopeExpansion: false, completion: false },
      boundary: "Content-free receipt for atomically reconciling an authoritative deletion ledger into one restored local vault. It grants no restore, network, cross-vault, external-effect, scope-expansion, or completion authority.",
    };
    const errors = validateWorkKnowledgeReconciliation(receipt); if (errors.length) fail(`knowledge deletion reconciliation receipt is invalid: ${errors[0]}`);
    await writeJsonExclusive(join(stageContext.root, "reconciliations", `${reconciliationId}.json`), receipt);
    await auditKnowledgeVaultInternal({ root: paths.stage, vaultId, masterKey: restoredMasterKey, deep: true });
    const restoredCheck = keyCheck(restoredMasterKey, vaultId), journal = { schemaVersion: 1, kind: "deletion-reconciliation", operationId: reconciliationId, vaultId, preparedAt: observed.toISOString(), currentKeyCheckSha256: restoredCheck, targetKeyCheckSha256: restoredCheck };
    await publishStagedVault({ paths, journal, currentMasterKey: restoredMasterKey, targetMasterKey: restoredMasterKey });
    return { receipt, sha256: sha(receipt), authority: "none" };
  } catch (error) {
    await rm(paths.stage, { recursive: true, force: true, maxRetries: 2, retryDelay: 20 }).catch(() => {});
    throw error;
  }
}
