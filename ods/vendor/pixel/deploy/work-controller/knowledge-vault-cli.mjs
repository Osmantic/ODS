import { createHash, timingSafeEqual } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, readdir, realpath, rename, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { canonical } from "../../scripts/lib/work-contract.mjs";
import {
  auditKnowledgeVault, buildKnowledgeDeletion, buildKnowledgeIngestion, buildKnowledgeQuery, deleteKnowledgeSource,
  generateKnowledgeVaultKey, ingestKnowledgeText, initializeKnowledgeVault, inspectKnowledgeSource, knowledgeVaultHead, knowledgeVaultLifecycleHead,
  loadKnowledgeVaultKeyCredential, reconcileKnowledgeVaultDeletionLedger, retrieveKnowledge, rotateKnowledgeVaultKey,
} from "./knowledge-vault.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;
const TENANT_RE = /^[a-z][a-z0-9-]{2,63}$/u;
const VAULT_RE = /^knowledgevault-[a-f0-9]{12}$/u;
const SOURCE_RE = /^workknowledgesource-[0-9]{13}-[a-f0-9]{12}$/u;
const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/u;
const classifications = new Set(["public", "internal", "confidential", "restricted"]);
const vaultRootEntries = Object.freeze(["deletion-claims", "deletions", "ingestion-claims", "key-rotations", "reconciliations", "retrieval-claims", "sources", "vault.json"]);
const reviewAuthority = Object.freeze({ mutatesVault: false, grantsRetrieval: false, grantsExecution: false, grantsNetwork: false, grantsExternalEffects: false, grantsCompletion: false });
const mutationAuthority = Object.freeze({ exactLocalVaultMutation: true, grantsRetrieval: false, grantsExecution: false, grantsNetwork: false, grantsExternalEffects: false, grantsCompletion: false });
const retrievalAuthority = Object.freeze({ exactLocalVaultRead: true, writesPrivateContext: true, grantsExecution: false, grantsNetwork: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Trusted-terminal review and exact-confirmed local knowledge-vault lifecycle only. Review grants no retrieval; query apply performs one checkpoint-bound local read into one new owner-private context file. No result grants instruction, tool, process, network, cross-client, external-effect, scope-expansion, or completion authority. Key rotation and restored-vault reconciliation additionally require the controller to be offline.";

export class KnowledgeVaultCliError extends Error {}

function fail(message) { throw new KnowledgeVaultCliError(message); }
function sha(value) { return createHash("sha256").update(Buffer.isBuffer(value) || typeof value === "string" ? value : canonical(value)).digest("hex"); }
function samePath(left, right) { return process.platform === "win32" ? left.toLocaleLowerCase("en-US") === right.toLocaleLowerCase("en-US") : left === right; }

const commandOptions = Object.freeze({
  "setup-review": ["--vault", "--vault-id", "--credential"],
  "setup-apply": ["--vault", "--vault-id", "--credential", "--confirm-review-sha256"],
  status: ["--vault", "--vault-id", "--credential"],
  "ingest-review": ["--vault", "--vault-id", "--credential", "--owner", "--client", "--title-file", "--source", "--classification", "--delete-after"],
  "ingest-apply": ["--vault", "--vault-id", "--credential", "--owner", "--client", "--title-file", "--source", "--classification", "--delete-after", "--confirm-review-sha256"],
  "delete-review": ["--vault", "--vault-id", "--credential", "--owner", "--client", "--source-id", "--reason"],
  "delete-apply": ["--vault", "--vault-id", "--credential", "--owner", "--client", "--source-id", "--reason", "--confirm-review-sha256"],
  "query-review": ["--vault", "--vault-id", "--credential", "--owner", "--client", "--job-id", "--checkpoint-sha256", "--query-file", "--maximum-classification", "--minimum-score-bps", "--max-results", "--output"],
  "query-apply": ["--vault", "--vault-id", "--credential", "--owner", "--client", "--job-id", "--checkpoint-sha256", "--query-file", "--maximum-classification", "--minimum-score-bps", "--max-results", "--output", "--confirm-review-sha256"],
  "rotate-review": ["--vault", "--vault-id", "--old-credential", "--new-credential"],
  "rotate-apply": ["--vault", "--vault-id", "--old-credential", "--new-credential", "--confirm-review-sha256", "--controller-offline"],
  "reconcile-review": ["--authoritative-vault", "--restored-vault", "--vault-id", "--authoritative-credential", "--restored-credential"],
  "reconcile-apply": ["--authoritative-vault", "--restored-vault", "--vault-id", "--authoritative-credential", "--restored-credential", "--confirm-review-sha256", "--controller-offline"],
});

function parseArguments(argv) {
  const command = argv?.[0], required = commandOptions[command];
  if (!required || argv.length !== 1 + required.length * 2) fail("Usage: knowledge-vault-cli.mjs setup-review|setup-apply|status|ingest-review|ingest-apply|delete-review|delete-apply|query-review|query-apply|rotate-review|rotate-apply|reconcile-review|reconcile-apply with the exact documented options");
  const values = {}, pathOptions = new Set(["--vault", "--credential", "--title-file", "--source", "--query-file", "--output", "--old-credential", "--new-credential", "--authoritative-vault", "--restored-vault", "--authoritative-credential", "--restored-credential"]);
  for (let index = 1; index < argv.length; index += 2) {
    const option = argv[index], value = argv[index + 1];
    if (!required.includes(option) || Object.hasOwn(values, option) || typeof value !== "string" || !value) fail("knowledge vault arguments are invalid, unknown, duplicated, or incomplete");
    values[option] = pathOptions.has(option) ? resolve(value) : value;
  }
  for (const option of required) if (!values[option]) fail(`knowledge vault command is missing ${option}`);
  if (!VAULT_RE.test(values["--vault-id"] ?? "")) fail("knowledge vault identity is invalid");
  if (values["--owner"] !== undefined && !TENANT_RE.test(values["--owner"])) fail("knowledge owner is invalid");
  if (values["--client"] !== undefined && !TENANT_RE.test(values["--client"])) fail("knowledge client is invalid");
  if (values["--source-id"] !== undefined && !SOURCE_RE.test(values["--source-id"])) fail("knowledge source identity is invalid");
  if (values["--job-id"] !== undefined && !JOB_RE.test(values["--job-id"])) fail("knowledge query job identity is invalid");
  if (values["--checkpoint-sha256"] !== undefined && !SHA_RE.test(values["--checkpoint-sha256"])) fail("knowledge query checkpoint is invalid");
  if (values["--classification"] !== undefined && !classifications.has(values["--classification"])) fail("knowledge classification is invalid");
  if (values["--maximum-classification"] !== undefined && !classifications.has(values["--maximum-classification"])) fail("knowledge query classification is invalid");
  if (values["--minimum-score-bps"] !== undefined && (!/^(?:0|[1-9][0-9]{0,3}|10000)$/u.test(values["--minimum-score-bps"]) || Number(values["--minimum-score-bps"]) > 10000)) fail("knowledge query minimum score is invalid");
  if (values["--max-results"] !== undefined && (!/^[1-9][0-9]?$/u.test(values["--max-results"]) || Number(values["--max-results"]) > 20)) fail("knowledge query result limit is invalid");
  if (values["--reason"] !== undefined && !["owner-request", "source-revoked", "client-removal"].includes(values["--reason"])) fail("knowledge deletion reason is invalid");
  if (values["--confirm-review-sha256"] !== undefined && !SHA_RE.test(values["--confirm-review-sha256"])) fail("knowledge review confirmation is invalid");
  if (values["--controller-offline"] !== undefined && values["--controller-offline"] !== "confirmed") fail("offline lifecycle mutation requires --controller-offline confirmed");
  return { command, values };
}

async function privateBytes(pathValue, maximum, label) {
  const path = resolve(pathValue); let record, actual;
  try { [record, actual] = await Promise.all([readBoundedRegularFile(path, maximum, label), realpath(path)]); }
  catch { fail(`${label} cannot be opened safely`); }
  if (!samePath(path, actual) || record.details.nlink !== 1 || process.platform !== "win32" && (record.details.uid !== process.geteuid() || (record.details.mode & 0o077) !== 0)) fail(`${label} is not an owner-private single-link real file`);
  return { path, bytes: record.bytes };
}
async function ingestInputs(values) {
  const titleRecord = await privateBytes(values["--title-file"], 2000, "knowledge title file"), sourceRecord = await privateBytes(values["--source"], 1048576, "knowledge source file");
  let title, text;
  try { title = new TextDecoder("utf-8", { fatal: true }).decode(titleRecord.bytes); text = new TextDecoder("utf-8", { fatal: true }).decode(sourceRecord.bytes); }
  catch { fail("knowledge title or source is not strict UTF-8"); }
  if (!title.endsWith("\n") || title.slice(0, -1).includes("\n") || title.includes("\r") || title.length < 2 || [...title.slice(0, -1)].length > 500 || title.includes("\0")) fail("knowledge title file must contain one bounded UTF-8 line ending in one newline");
  title = title.slice(0, -1);
  if (!text || text.includes("\0")) fail("knowledge source text is empty or contains a null character");
  const rawDeleteAfter = values["--delete-after"], parsed = rawDeleteAfter === "none" ? null : new Date(rawDeleteAfter);
  if (parsed !== null && (!Number.isSafeInteger(parsed.getTime()) || parsed.toISOString() !== rawDeleteAfter)) fail("knowledge retention must be none or a canonical UTC timestamp");
  return { title, text, titlePath: titleRecord.path, sourcePath: sourceRecord.path, deleteAfter: parsed };
}
async function credential(path) { return loadKnowledgeVaultKeyCredential({ credentialPath: path }); }
function nowFrom(dependencies) { const value = dependencies.now ? dependencies.now() : new Date(); if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail("knowledge vault clock is invalid"); return value; }

async function queryInput(values) {
  const record = await privateBytes(values["--query-file"], 8000, "knowledge query file");
  let text;
  try { text = new TextDecoder("utf-8", { fatal: true }).decode(record.bytes); } catch { fail("knowledge query is not strict UTF-8"); }
  if (!text || text.includes("\0") || text.includes("\r") || !text.endsWith("\n") || text.slice(0, -1).includes("\n")) fail("knowledge query file must contain one bounded UTF-8 line ending in one newline");
  text = text.slice(0, -1);
  if (!text) fail("knowledge query is empty");
  const output = resolve(values["--output"]), parent = dirname(output);
  if (basename(output) !== values["--output"].split(/[\\/]/u).at(-1) || output === parent) fail("knowledge query output path is invalid");
  const [parentInfo, actualParent, outputInfo] = await Promise.all([lstat(parent).catch(() => null), realpath(parent).catch(() => null), lstat(output).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))]);
  if (!parentInfo?.isDirectory() || parentInfo.isSymbolicLink() || !actualParent || !samePath(parent, actualParent) || outputInfo !== null || process.platform !== "win32" && (parentInfo.uid !== process.geteuid() || (parentInfo.mode & 0o077) !== 0)) fail("knowledge query output must be a new file in an owner-private real directory");
  return { text, path: record.path, bytes: record.bytes.length, output };
}

async function writePrivateJson(path, value) {
  const payload = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8"), handle = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
  try {
    await handle.writeFile(payload); await handle.sync();
    const [openedInfo, namedInfo] = await Promise.all([handle.stat(), lstat(path)]);
    if (!openedInfo.isFile() || !namedInfo.isFile() || namedInfo.isSymbolicLink()
      || openedInfo.dev !== namedInfo.dev || openedInfo.ino !== namedInfo.ino
      || openedInfo.nlink !== 1 || namedInfo.nlink !== 1 || openedInfo.size !== payload.length || namedInfo.size !== payload.length
      || process.platform !== "win32" && (openedInfo.uid !== process.geteuid() || namedInfo.uid !== process.geteuid() || (openedInfo.mode & 0o077) !== 0 || (namedInfo.mode & 0o077) !== 0)) fail("knowledge query output did not retain private single-link custody");
  } catch (error) { await discardExclusiveFile(handle, path); throw error; }
  await handle.close();
  return { bytes: payload.length, sha256: sha(payload) };
}

async function discardExclusiveFile(handle, path) {
  const openedInfo = await handle.stat().catch(() => null); await handle.close().catch(() => {});
  const namedInfo = await lstat(path).catch(() => null);
  if (openedInfo?.isFile() && namedInfo?.isFile() && !namedInfo.isSymbolicLink() && openedInfo.dev === namedInfo.dev && openedInfo.ino === namedInfo.ino) await unlink(path).catch(() => {});
}

function nestedPath(ancestor, candidate) {
  const answer = relative(ancestor, candidate);
  return answer === "" || answer !== ".." && !answer.startsWith(`..${sep}`) && !isAbsolute(answer);
}

async function privateDirectoryIdentity(pathValue, label) {
  const path = resolve(pathValue), [info, actual] = await Promise.all([lstat(path).catch(() => null), realpath(path).catch(() => null)]);
  if (!info?.isDirectory() || info.isSymbolicLink() || !actual || !samePath(path, actual)
    || process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not an owner-private real directory`);
  return { path, identity: `${info.dev}:${info.ino}`, pathSha256: sha(path) };
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

async function writePrivateCredential(path, masterKey) {
  const payload = Buffer.from(`${masterKey.toString("hex")}\n`, "ascii"), handle = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
  try {
    await handle.writeFile(payload); await handle.sync();
    const [openedInfo, namedInfo] = await Promise.all([handle.stat(), lstat(path)]);
    if (!openedInfo.isFile() || !namedInfo.isFile() || namedInfo.isSymbolicLink()
      || openedInfo.dev !== namedInfo.dev || openedInfo.ino !== namedInfo.ino
      || openedInfo.nlink !== 1 || namedInfo.nlink !== 1 || openedInfo.size !== 65 || namedInfo.size !== 65
      || process.platform !== "win32" && (openedInfo.uid !== process.geteuid() || namedInfo.uid !== process.geteuid() || (openedInfo.mode & 0o077) !== 0 || (namedInfo.mode & 0o077) !== 0)) fail("knowledge credential did not retain private single-link custody");
  } catch (error) { await discardExclusiveFile(handle, path); throw error; }
  await handle.close(); await syncDirectory(dirname(path));
}

async function requireExactVaultRoot(path) {
  const observed = (await readdir(path)).sort((left, right) => left.localeCompare(right));
  if (canonical(observed) !== canonical(vaultRootEntries)) fail("knowledge setup stage contains an unexpected entry");
}

async function setupReview(values) {
  const root = resolve(values["--vault"]), credentialPath = resolve(values["--credential"]), rootParent = dirname(root), credentialParent = dirname(credentialPath);
  if (basename(credentialPath) !== "pixel-knowledge-vault-key") fail("knowledge setup credential filename must be pixel-knowledge-vault-key");
  if (nestedPath(root, credentialPath) || nestedPath(credentialPath, root) || nestedPath(root, credentialParent)) fail("knowledge setup credential must be outside the vault root");
  const [vaultParent, keyParent, rootInfo, credentialInfo] = await Promise.all([
    privateDirectoryIdentity(rootParent, "knowledge vault parent"), privateDirectoryIdentity(credentialParent, "knowledge credential parent"),
    lstat(root).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error)), lstat(credentialPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error)),
  ]);
  if (rootInfo !== null) fail("knowledge vault target already exists");
  if (credentialInfo !== null && (!credentialInfo.isFile() || credentialInfo.isSymbolicLink())) fail("knowledge credential target is not a real file");
  const stage = resolve(rootParent, `.${basename(root)}.knowledge-setup-stage`), stageInfo = await lstat(stage).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (stageInfo !== null && (!stageInfo.isDirectory() || stageInfo.isSymbolicLink() || process.platform !== "win32" && (stageInfo.uid !== process.geteuid() || (stageInfo.mode & 0o077) !== 0))) fail("knowledge setup stage is not an owner-private real directory");
  if (stageInfo !== null && credentialInfo === null) fail("knowledge setup stage requires its existing external credential");
  const stageEntries = stageInfo === null ? [] : (await readdir(stage)).sort((left, right) => left.localeCompare(right));
  if (stageEntries.some((entry) => !vaultRootEntries.includes(entry))) fail("knowledge setup stage contains an unexpected entry");
  const masterKey = credentialInfo === null ? null : await credential(credentialPath);
  const binding = {
    operation: "setup", vaultId: values["--vault-id"], vaultPathSha256: sha(root), credentialPathSha256: sha(credentialPath),
    vaultParentIdentity: vaultParent.identity, vaultParentPathSha256: vaultParent.pathSha256,
    credentialParentIdentity: keyParent.identity, credentialParentPathSha256: keyParent.pathSha256,
    credentialState: masterKey === null ? "create-new" : "use-existing", credentialKeySha256: masterKey === null ? null : sha(masterKey),
    stagedInitialization: stageInfo !== null, stageIdentity: stageInfo === null ? null : `${stageInfo.dev}:${stageInfo.ino}`, stageEntriesSha256: stageInfo === null ? null : sha(stageEntries),
  };
  return { binding, reviewSha256: sha(binding), root, rootParent, credentialPath, stage, masterKey };
}

async function ingestReview(values) {
  const masterKey = await credential(values["--credential"]), input = await ingestInputs(values), head = await knowledgeVaultHead({ root: values["--vault"], vaultId: values["--vault-id"], masterKey, ownerId: values["--owner"], clientId: values["--client"] });
  const binding = { operation: "ingest", vaultId: values["--vault-id"], vaultHeadSha256: head.sha256, ownerId: values["--owner"], clientId: values["--client"], titleSha256: sha(input.title), sourceSha256: sha(input.text), sourceBytes: Buffer.byteLength(input.text, "utf8"), titlePathSha256: sha(input.titlePath), sourcePathSha256: sha(input.sourcePath), classification: values["--classification"], deleteAfter: input.deleteAfter?.toISOString() ?? null };
  return { masterKey, input, binding, reviewSha256: sha(binding), head };
}
async function deleteReview(values) {
  const masterKey = await credential(values["--credential"]), head = await knowledgeVaultLifecycleHead({ root: values["--vault"], vaultId: values["--vault-id"], masterKey });
  const source = await inspectKnowledgeSource({ root: values["--vault"], vaultId: values["--vault-id"], masterKey, ownerId: values["--owner"], clientId: values["--client"], sourceId: values["--source-id"] });
  const binding = { operation: "delete", vaultId: values["--vault-id"], lifecycleHeadSha256: head.sha256, ownerId: source.ownerId, clientId: source.clientId, sourceId: source.sourceId, sourceReceiptSha256: source.receiptSha256, sourceContentSha256: source.contentSha256, reasonCode: values["--reason"] };
  return { masterKey, source, binding, reviewSha256: sha(binding), head };
}
async function queryReview(values) {
  const masterKey = await credential(values["--credential"]), input = await queryInput(values), head = await knowledgeVaultHead({ root: values["--vault"], vaultId: values["--vault-id"], masterKey, ownerId: values["--owner"], clientId: values["--client"] });
  const binding = { operation: "query", vaultId: values["--vault-id"], vaultHeadSha256: head.sha256, ownerId: values["--owner"], clientId: values["--client"], jobId: values["--job-id"], checkpointSha256: values["--checkpoint-sha256"], queryTextSha256: sha(input.text), queryBytes: input.bytes, queryPathSha256: sha(input.path), maximumClassification: values["--maximum-classification"], minimumScoreBps: Number(values["--minimum-score-bps"]), maxResults: Number(values["--max-results"]), outputPathSha256: sha(input.output) };
  return { masterKey, input, binding, reviewSha256: sha(binding), head };
}
async function rotationReview(values) {
  const oldMasterKey = await credential(values["--old-credential"]), newMasterKey = await credential(values["--new-credential"]);
  if (timingSafeEqual(oldMasterKey, newMasterKey)) fail("knowledge key rotation requires a distinct new credential");
  const head = await knowledgeVaultLifecycleHead({ root: values["--vault"], vaultId: values["--vault-id"], masterKey: oldMasterKey });
  const binding = { operation: "rotate", vaultId: values["--vault-id"], lifecycleHeadSha256: head.sha256, oldKeySha256: sha(oldMasterKey), newKeySha256: sha(newMasterKey), controllerOfflineRequired: true };
  return { oldMasterKey, newMasterKey, binding, reviewSha256: sha(binding), head };
}
async function reconciliationReview(values) {
  const authoritativeMasterKey = await credential(values["--authoritative-credential"]), restoredMasterKey = await credential(values["--restored-credential"]);
  const authoritativeHead = await knowledgeVaultLifecycleHead({ root: values["--authoritative-vault"], vaultId: values["--vault-id"], masterKey: authoritativeMasterKey });
  const restoredHead = await knowledgeVaultLifecycleHead({ root: values["--restored-vault"], vaultId: values["--vault-id"], masterKey: restoredMasterKey });
  const binding = { operation: "reconcile", vaultId: values["--vault-id"], authoritativeLifecycleHeadSha256: authoritativeHead.sha256, restoredLifecycleHeadSha256: restoredHead.sha256, authoritativeKeySha256: sha(authoritativeMasterKey), restoredKeySha256: sha(restoredMasterKey), controllerOfflineRequired: true };
  return { authoritativeMasterKey, restoredMasterKey, binding, reviewSha256: sha(binding), authoritativeHead, restoredHead };
}

export async function runKnowledgeVaultCommand(argv, dependencies = {}) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies) || dependencies.now !== undefined && typeof dependencies.now !== "function" || dependencies.suffix !== undefined && !/^[a-f0-9]{12}$/u.test(dependencies.suffix)) fail("knowledge vault CLI dependencies are invalid");
  const { command, values } = parseArguments(argv), vaultId = values["--vault-id"];
  if (command.startsWith("setup-")) {
    const review = await setupReview(values);
    if (command === "setup-review") return { schemaVersion: 1, operation: "pixel-work-knowledge-setup-review", vaultId, vaultDirectory: basename(review.root), credentialFile: basename(review.credentialPath), credentialAction: review.binding.credentialState, resumesStagedInitialization: review.binding.stagedInitialization, externalCredential: true, confirmation: { option: "--confirm-review-sha256", sha256: review.reviewSha256 }, authority: reviewAuthority, boundary };
    if (values["--confirm-review-sha256"] !== review.reviewSha256) fail("knowledge setup confirmation differs from the exact current review");
    const masterKey = review.masterKey ?? generateKnowledgeVaultKey();
    if (review.masterKey === null) await writePrivateCredential(review.credentialPath, masterKey);
    await initializeKnowledgeVault({ root: review.stage, vaultId, masterKey, now: nowFrom(dependencies) });
    await requireExactVaultRoot(review.stage); await auditKnowledgeVault({ root: review.stage, vaultId, masterKey, deep: true });
    if (await lstat(review.root).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error)) !== null) fail("knowledge vault target appeared during setup");
    await rename(review.stage, review.root); await syncDirectory(review.rootParent);
    const audit = await auditKnowledgeVault({ root: review.root, vaultId, masterKey, deep: true }), head = await knowledgeVaultLifecycleHead({ root: review.root, vaultId, masterKey });
    return { schemaVersion: 1, operation: "pixel-work-knowledge-setup-apply", state: "ready", vaultId, sources: audit.sources, chunks: audit.chunks, deletions: audit.deletions, lifecycleHeadSha256: head.sha256, externalCredential: true, credentialCreated: review.masterKey === null, authority: mutationAuthority, boundary };
  }
  if (command === "status") {
    const masterKey = await credential(values["--credential"]), audit = await auditKnowledgeVault({ root: values["--vault"], vaultId, masterKey, deep: true }), head = await knowledgeVaultLifecycleHead({ root: values["--vault"], vaultId, masterKey });
    return { schemaVersion: 1, operation: "pixel-work-knowledge-status", state: "ready", sources: audit.sources, chunks: audit.chunks, deletions: audit.deletions, lifecycleHeadSha256: head.sha256, authority: reviewAuthority, boundary };
  }
  if (command.startsWith("ingest-")) {
    const review = await ingestReview(values);
    if (command === "ingest-review") return { schemaVersion: 1, operation: "pixel-work-knowledge-ingest-review", vaultHeadSha256: review.head.sha256, ownerId: values["--owner"], clientId: values["--client"], title: review.input.title, sourceFile: basename(review.input.sourcePath), sourceBytes: review.binding.sourceBytes, sourceSha256: review.binding.sourceSha256, classification: values["--classification"], deleteAfter: review.binding.deleteAfter, confirmation: { option: "--confirm-review-sha256", sha256: review.reviewSha256 }, authority: reviewAuthority, boundary };
    if (values["--confirm-review-sha256"] !== review.reviewSha256) fail("knowledge ingestion confirmation differs from the exact current review");
    const now = nowFrom(dependencies), ingestion = buildKnowledgeIngestion({ ownerId: values["--owner"], clientId: values["--client"], title: review.input.title, text: review.input.text, classification: values["--classification"], originType: "local-file", originIdentifier: review.input.sourcePath, deleteAfter: review.input.deleteAfter, now, expiresAt: new Date(now.getTime() + 60000), ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
    const result = await ingestKnowledgeText({ root: values["--vault"], vaultId, masterKey: review.masterKey, ingestion, title: review.input.title, text: review.input.text, now });
    return { schemaVersion: 1, operation: "pixel-work-knowledge-ingest-apply", state: "ingested", sourceId: result.receipt.sourceId, receiptSha256: result.sha256, classification: result.receipt.classification, deleteAfter: result.receipt.retention.deleteAfter, authority: mutationAuthority, boundary };
  }
  if (command.startsWith("delete-")) {
    const review = await deleteReview(values);
    if (command === "delete-review") return { schemaVersion: 1, operation: "pixel-work-knowledge-delete-review", lifecycleHeadSha256: review.head.sha256, sourceId: review.source.sourceId, ownerId: review.source.ownerId, clientId: review.source.clientId, title: review.source.title, classification: review.source.classification, byteLength: review.source.byteLength, contentSha256: review.source.contentSha256, deleteAfter: review.source.retention.deleteAfter, reasonCode: values["--reason"], confirmation: { option: "--confirm-review-sha256", sha256: review.reviewSha256 }, authority: reviewAuthority, boundary };
    if (values["--confirm-review-sha256"] !== review.reviewSha256) fail("knowledge deletion confirmation differs from the exact current review");
    const now = nowFrom(dependencies), deletion = await buildKnowledgeDeletion({ root: values["--vault"], vaultId, masterKey: review.masterKey, ownerId: values["--owner"], clientId: values["--client"], sourceId: values["--source-id"], reasonCode: values["--reason"], now, expiresAt: new Date(now.getTime() + 60000), ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
    const result = await deleteKnowledgeSource({ root: values["--vault"], vaultId, masterKey: review.masterKey, deletion, now });
    return { schemaVersion: 1, operation: "pixel-work-knowledge-delete-apply", state: "deleted", sourceId: deletion.sourceId, tombstoneSha256: result.sha256, residualCiphertext: result.residualCiphertext, backupPropagationRequired: result.backupPropagationRequired, authority: mutationAuthority, boundary };
  }
  if (command.startsWith("query-")) {
    const review = await queryReview(values);
    if (command === "query-review") return { schemaVersion: 1, operation: "pixel-work-knowledge-query-review", vaultHeadSha256: review.head.sha256, ownerId: values["--owner"], clientId: values["--client"], jobId: values["--job-id"], checkpointSha256: values["--checkpoint-sha256"], queryFile: basename(review.input.path), queryBytes: review.input.bytes, queryTextSha256: review.binding.queryTextSha256, maximumClassification: review.binding.maximumClassification, minimumScoreBps: review.binding.minimumScoreBps, maxResults: review.binding.maxResults, outputFile: basename(review.input.output), confirmation: { option: "--confirm-review-sha256", sha256: review.reviewSha256 }, authority: reviewAuthority, boundary };
    if (values["--confirm-review-sha256"] !== review.reviewSha256) fail("knowledge query confirmation differs from the exact current review");
    const now = nowFrom(dependencies), query = await buildKnowledgeQuery({ root: values["--vault"], vaultId, masterKey: review.masterKey, ownerId: values["--owner"], clientId: values["--client"], jobId: values["--job-id"], checkpointSha256: values["--checkpoint-sha256"], queryText: review.input.text, maximumClassification: values["--maximum-classification"], minRelevanceBps: review.binding.minimumScoreBps, maxResults: review.binding.maxResults, now, expiresAt: new Date(now.getTime() + 60000), ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
    const retrieval = await retrieveKnowledge({ root: values["--vault"], vaultId, masterKey: review.masterKey, query, queryText: review.input.text, now: new Date(now.getTime() + 1), ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
    const output = await writePrivateJson(review.input.output, retrieval);
    return { schemaVersion: 1, operation: "pixel-work-knowledge-query-apply", state: retrieval.reason, retrievalId: retrieval.retrievalId, queryContractSha256: retrieval.queryContractSha256, vaultHeadSha256: retrieval.vaultHeadSha256, results: retrieval.results.length, outputFile: basename(review.input.output), outputBytes: output.bytes, outputSha256: output.sha256, untrustedText: true, authority: retrievalAuthority, boundary };
  }
  if (command.startsWith("rotate-")) {
    const review = await rotationReview(values);
    if (command === "rotate-review") return { schemaVersion: 1, operation: "pixel-work-knowledge-rotate-review", lifecycleHeadSha256: review.head.sha256, sourceCount: review.head.sourceCount, deletionCount: review.head.deletionCount, controllerOfflineRequired: true, confirmation: { option: "--confirm-review-sha256", sha256: review.reviewSha256 }, authority: reviewAuthority, boundary };
    if (values["--confirm-review-sha256"] !== review.reviewSha256) fail("knowledge rotation confirmation differs from the exact current review");
    const result = await rotateKnowledgeVaultKey({ root: values["--vault"], vaultId, oldMasterKey: review.oldMasterKey, newMasterKey: review.newMasterKey, now: nowFrom(dependencies), ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
    return { schemaVersion: 1, operation: "pixel-work-knowledge-rotate-apply", state: "rotated", rotationId: result.receipt.rotationId, receiptSha256: result.sha256, sourceCount: result.receipt.sources.length, residualOldKeyWrapping: result.residualOldKeyWrapping, authority: mutationAuthority, boundary };
  }
  const review = await reconciliationReview(values);
  if (command === "reconcile-review") return { schemaVersion: 1, operation: "pixel-work-knowledge-reconcile-review", authoritativeLifecycleHeadSha256: review.authoritativeHead.sha256, restoredLifecycleHeadSha256: review.restoredHead.sha256, authoritativeDeletionCount: review.authoritativeHead.deletionCount, restoredSourceCount: review.restoredHead.sourceCount, controllerOfflineRequired: true, confirmation: { option: "--confirm-review-sha256", sha256: review.reviewSha256 }, authority: reviewAuthority, boundary };
  if (values["--confirm-review-sha256"] !== review.reviewSha256) fail("knowledge reconciliation confirmation differs from the exact current review");
  const result = await reconcileKnowledgeVaultDeletionLedger({ authoritativeRoot: values["--authoritative-vault"], restoredRoot: values["--restored-vault"], vaultId, authoritativeMasterKey: review.authoritativeMasterKey, restoredMasterKey: review.restoredMasterKey, now: nowFrom(dependencies), ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
  return { schemaVersion: 1, operation: "pixel-work-knowledge-reconcile-apply", state: "reconciled", reconciliationId: result.receipt.reconciliationId, receiptSha256: result.sha256, applied: result.receipt.applied.length, alreadyPresent: result.receipt.alreadyPresent.length, residualCiphertext: result.receipt.residualCiphertext, authority: mutationAuthority, boundary };
}

export async function main(argv = process.argv.slice(2)) { process.stdout.write(`${JSON.stringify(await runKnowledgeVaultCommand(argv))}\n`); }

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) main().catch((error) => { process.stderr.write(`pixel-work-knowledge: ${error instanceof KnowledgeVaultCliError ? error.message : "unexpected failure"}\n`); process.exitCode = 1; });

export const knowledgeVaultCliBoundary = boundary;
