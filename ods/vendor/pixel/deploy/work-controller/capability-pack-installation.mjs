import { spawnSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { tmpdir } from "node:os";

import { assertJsonSchema } from "../../scripts/lib/json-schema.mjs";
import { canonical, validateWorkCapabilityPack, validateWorkCapabilityPackV2 } from "../../scripts/lib/work-contract.mjs";
import {
  acquireCapabilityPackOperation, auditCapabilityPackOperationRoot, CapabilityPackOperationError,
  loadCapabilityPackOperation, releaseCapabilityPackOperation,
} from "./capability-pack-operation.mjs";

const PACK_BYTES = 512 * 1024;
const SIGNATURE_BYTES = 64 * 1024;
const SIGNERS_BYTES = 256 * 1024;
const KEY_BYTES = 256 * 1024;
const SIGNATURE_NAMESPACE = "pixel-work-capability-pack";
const SSH_KEYGEN = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const ID_RE = /^[a-z][a-z0-9-]{1,62}$/u;
const VERSION_RE = /^(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})$/u;
const IDENTITY_RE = /^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const authority = Object.freeze({
  grantsToolUse: false, grantsDataAccess: false, grantsImagePull: false,
  grantsExecution: false, grantsNetwork: false, grantsExternalEffects: false,
  grantsCompletion: false,
});
const boundary = "Verified private installation of one signed capability declaration. Installation is disabled and grants no tool use, data access, image pull, execution, network, external effect, or completion authority.";
const receiptSchema = JSON.parse(await readFile(new URL("../../schemas/work-capability-pack-installation-v1.schema.json", import.meta.url), "utf8"));
const removalSchema = JSON.parse(await readFile(new URL("../../schemas/work-capability-pack-removal-v1.schema.json", import.meta.url), "utf8"));
const removalBoundary = "Content-free tombstone for exact removal of one never-enabled capability declaration. It grants no tool use, data access, image pull, execution, network, external effect, completion, reinstall, or trust authority.";
const removalReviewBoundary = "Content-free review of one exact disabled declaration removal. Review grants no deletion, tool use, data access, image pull, execution, network, external effect, completion, reinstall, or trust authority.";
const removalCustodyBoundary = "Content-free proof that one exact installed capability declaration entered its private removal transaction before deletion. It grants no deletion, reinstall, trust, tool use, data access, image pull, execution, network, external effect, or completion authority.";

export class CapabilityPackInstallationError extends Error {}

function fail(message) { throw new CapabilityPackInstallationError(message); }
function sha(payload) { return createHash("sha256").update(payload).digest("hex"); }
function packSha(pack) { return sha(canonical(pack)); }
async function operationCall(promise) { try { return await promise; } catch (error) { if (error instanceof CapabilityPackOperationError) fail(error.message); throw error; } }

async function descriptorSnapshot(path, maximumBytes, label, { ownerOnly = false, rejectWritable = false } = {}) {
  if (!isAbsolute(path)) fail(`${label} path must be absolute`);
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)); }
  catch (error) {
    if (error?.code === "ELOOP") fail(`${label} must be a descriptor-bound regular file`);
    throw error;
  }
  try {
    const info = await handle.stat();
    const current = await lstat(path);
    if (!info.isFile() || info.nlink !== 1 || info.size < 1 || info.size > maximumBytes || current.isSymbolicLink() || current.dev !== info.dev || current.ino !== info.ino) {
      fail(`${label} must be a descriptor-bound regular single-link file within its byte limit`);
    }
    if (process.platform !== "win32" && info.uid !== process.geteuid()) fail(`${label} must be owned by the current user`);
    if (process.platform !== "win32" && ownerOnly && (info.mode & 0o077) !== 0) fail(`${label} must be owner-only`);
    if (process.platform !== "win32" && rejectWritable && (info.mode & 0o022) !== 0) fail(`${label} must not be group/world writable`);
    const payload = await handle.readFile();
    if (payload.length > maximumBytes) fail(`${label} exceeds its byte limit`);
    return payload;
  } finally { await handle.close(); }
}

async function privateDirectory(path, label, create = false) {
  if (!isAbsolute(path) || resolve(path) === dirname(resolve(path))) fail(`${label} must be an absolute non-root directory`);
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real directory`);
  if (await realpath(path) !== resolve(path)) fail(`${label} must not traverse a linked directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-private`);
  return resolve(path);
}

async function privateRegular(path, maximumBytes, label) {
  const payload = await descriptorSnapshot(path, maximumBytes, label, { ownerOnly: true });
  return payload;
}

function parsePack(payload, label = "capability pack") {
  let pack;
  try { pack = JSON.parse(payload.toString("utf8")); }
  catch { fail(`${label} is not valid JSON`); }
  const schemaVersion = pack?.schemaVersion;
  let errors;
  if (schemaVersion === 1) errors = validateWorkCapabilityPack(pack);
  else if (schemaVersion === 2) errors = validateWorkCapabilityPackV2(pack);
  else errors = [`${label} declares an unsupported schemaVersion`];
  if (errors.length) fail(`${label} is invalid: ${errors[0]}`);
  return pack;
}

function validateSignature(payload) {
  let lines;
  try { lines = payload.toString("ascii").trim().split(/\r?\n/u); }
  catch { fail("capability signature envelope is malformed"); }
  if (lines.length < 3 || lines[0] !== "-----BEGIN SSH SIGNATURE-----" || lines.at(-1) !== "-----END SSH SIGNATURE-----") fail("capability signature envelope is malformed");
  const body = lines.slice(1, -1);
  if (body.some((line) => !/^[A-Za-z0-9+/]+={0,2}$/u.test(line))) fail("capability signature envelope is malformed");
  const encoded = body.join("");
  if (encoded.length % 4 !== 0) fail("capability signature envelope is malformed");
  const decoded = Buffer.from(encoded, "base64");
  if (!decoded.length || decoded.toString("base64") !== encoded) fail("capability signature envelope is malformed");
}

async function writeExclusive(path, payload, mode = 0o600) {
  const handle = await open(path, "wx", mode);
  try { await handle.writeFile(payload); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

function checkedSshKeygen(path) {
  if (path !== SSH_KEYGEN) fail("ssh-keygen path differs from Pixel's fixed platform helper");
  return path;
}

function runSshKeygen(path, args, input = undefined) {
  const result = spawnSync(checkedSshKeygen(path), args, {
    input, encoding: input === undefined ? "utf8" : undefined, windowsHide: true,
    maxBuffer: 512 * 1024, timeout: 30000, shell: false,
  });
  if (result.error || result.status !== 0) fail("capability signature is invalid or its publisher is not trusted");
}

async function withSnapshots(files, callback) {
  const directory = await mkdtemp(join(tmpdir(), "pixel-work-capability-"));
  if (process.platform !== "win32") await chmod(directory, 0o700);
  try {
    const paths = {};
    for (const [name, payload] of Object.entries(files)) {
      paths[name] = join(directory, name);
      await writeExclusive(paths[name], payload);
    }
    return await callback(paths);
  } finally { await rm(directory, { recursive: true, force: true }); }
}

export async function inspectCapabilityPack(packPath) {
  const pack = parsePack(await descriptorSnapshot(resolve(packPath), PACK_BYTES, "capability pack"));
  return Object.freeze({
    schemaVersion: pack.schemaVersion, operation: "pixel-work-capability-pack-inspection", status: "valid-untrusted-declaration",
    pack: { id: pack.id, version: pack.version, packSha256: packSha(pack), treeSha256: pack.provenance.treeSha256, signerIdentity: pack.provenance.signerIdentity },
    tools: pack.tools.length, acceptedClassifications: [...pack.data.acceptedClassifications], trusted: false, installed: false, enabled: false,
    authority: { ...authority },
    boundary: "Structural inspection only. It establishes neither publisher trust nor installation, tool use, data access, image pull, execution, network, external-effect, or completion authority.",
  });
}

export async function verifyCapabilityPack({ packPath, signaturePath, allowedSignersPath, identity, sshKeygenPath = SSH_KEYGEN }) {
  if (!IDENTITY_RE.test(identity ?? "")) fail("capability signer identity is invalid");
  const packPayload = await descriptorSnapshot(resolve(packPath), PACK_BYTES, "capability pack");
  const pack = parsePack(packPayload);
  if (identity !== pack.provenance.signerIdentity || pack.provenance.signatureNamespace !== SIGNATURE_NAMESPACE) fail("capability signer identity differs from the pack declaration");
  const signature = await descriptorSnapshot(resolve(signaturePath), SIGNATURE_BYTES, "capability signature");
  validateSignature(signature);
  const allowedSigners = await descriptorSnapshot(resolve(allowedSignersPath), SIGNERS_BYTES, "allowed signers", { rejectWritable: true });
  await withSnapshots({ "pack.json": Buffer.from(canonical(pack)), "pack.sig": signature, "allowed_signers": allowedSigners }, async (paths) => {
    runSshKeygen(sshKeygenPath, ["-Y", "verify", "-f", paths.allowed_signers, "-I", identity, "-n", SIGNATURE_NAMESPACE, "-s", paths["pack.sig"]], await readFile(paths["pack.json"]));
  });
  return Object.freeze({
    schemaVersion: pack.schemaVersion, operation: "pixel-work-capability-pack-verification", status: "verified-disabled",
    pack: { id: pack.id, version: pack.version, packSha256: packSha(pack), treeSha256: pack.provenance.treeSha256, signerIdentity: identity },
    signatureSha256: sha(signature), allowedSignersSha256: sha(allowedSigners), trusted: true, installed: false, enabled: false,
    authority: { ...authority }, boundary,
  });
}

export async function signCapabilityPack({ packPath, signingKeyPath, signatureOutputPath, identity, sshKeygenPath = SSH_KEYGEN }) {
  if (!IDENTITY_RE.test(identity ?? "")) fail("capability signer identity is invalid");
  const pack = parsePack(await descriptorSnapshot(resolve(packPath), PACK_BYTES, "capability pack"));
  if (identity !== pack.provenance.signerIdentity) fail("capability signer identity differs from the pack declaration");
  const key = await descriptorSnapshot(resolve(signingKeyPath), KEY_BYTES, "capability signing key", { ownerOnly: true });
  const output = resolve(signatureOutputPath);
  const parent = await privateDirectory(dirname(output), "capability signature output parent");
  if (join(parent, basename(output)) !== output) fail("capability signature output path is unsafe");
  const signature = await withSnapshots({ "pack.json": Buffer.from(canonical(pack)), "signing-key": key }, async (paths) => {
    // Windows OpenSSH rejects a byte-identical private-key copy because its inherited ACL
    // is broader than the ACL ssh-keygen placed on the original. Use the already checked
    // original there and prove that it did not change across signing. POSIX uses the private
    // owner-only snapshot so the command cannot race the caller's key path.
    const keyForSigning = process.platform === "win32" ? resolve(signingKeyPath) : paths["signing-key"];
    runSshKeygen(sshKeygenPath, ["-Y", "sign", "-f", keyForSigning, "-n", SIGNATURE_NAMESPACE, paths["pack.json"]]);
    if (process.platform === "win32" && sha(await descriptorSnapshot(resolve(signingKeyPath), KEY_BYTES, "capability signing key", { ownerOnly: true })) !== sha(key)) fail("capability signing key changed during signing");
    if (process.platform !== "win32") await chmod(`${paths["pack.json"]}.sig`, 0o600);
    const payload = await privateRegular(`${paths["pack.json"]}.sig`, SIGNATURE_BYTES, "generated capability signature");
    validateSignature(payload);
    return payload;
  });
  await writeExclusive(output, signature);
  await syncDirectory(parent);
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-capability-pack-signature", status: "signed-not-installed",
    pack: { id: pack.id, version: pack.version, packSha256: packSha(pack), treeSha256: pack.provenance.treeSha256, signerIdentity: identity },
    signatureSha256: sha(signature), installed: false, enabled: false, authority: { ...authority }, boundary,
  });
}

function installationReceipt(pack, verification, installedAt) {
  if (!(installedAt instanceof Date) || !Number.isSafeInteger(installedAt.getTime())) fail("capability installation time is invalid");
  const receipt = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-installation-v1.schema.json",
    schemaVersion: 1, operation: "pixel-work-capability-pack-installation",
    pack: { ...verification.pack }, signatureSha256: verification.signatureSha256,
    allowedSignersSha256: verification.allowedSignersSha256, installedAt: installedAt.toISOString(),
    enabled: false, image: { pulled: false, inspected: false, executed: false }, authority: { ...authority }, boundary,
  };
  try { assertJsonSchema(receipt, receiptSchema, "capability installation receipt"); }
  catch { fail("capability installation receipt is invalid"); }
  if (receipt.pack.packSha256 !== packSha(pack)) fail("capability installation receipt differs from its pack");
  return receipt;
}

async function installedPackFromDirectory(path, allowedSignersPath, sshKeygenPath) {
  await privateDirectory(path, "installed capability version");
  const names = (await readdir(path)).sort();
  if (canonical(names) !== canonical(["install-receipt.json", "pack.json", "pack.sig"])) fail("installed capability version contains unknown or missing files");
  const packPath = join(path, "pack.json"), signaturePath = join(path, "pack.sig"), receiptPath = join(path, "install-receipt.json");
  const packPayload = await privateRegular(packPath, PACK_BYTES, "installed capability pack");
  const pack = parsePack(packPayload, "installed capability pack");
  if (packPayload.toString("utf8") !== `${canonical(pack)}\n`) fail("installed capability pack is not its canonical private copy");
  let receipt;
  try { receipt = JSON.parse((await privateRegular(receiptPath, PACK_BYTES, "capability installation receipt")).toString("utf8")); }
  catch (error) { if (error instanceof CapabilityPackInstallationError) throw error; fail("capability installation receipt is not valid JSON"); }
  try { assertJsonSchema(receipt, receiptSchema, "capability installation receipt"); }
  catch { fail("capability installation receipt is invalid"); }
  const verification = await verifyCapabilityPack({ packPath, signaturePath, allowedSignersPath, identity: pack.provenance.signerIdentity, sshKeygenPath });
  const expected = installationReceipt(pack, { ...verification, allowedSignersSha256: receipt.allowedSignersSha256 }, new Date(receipt.installedAt));
  if (canonical(expected) !== canonical(receipt)) fail("capability installation receipt differs from the installed declaration or current trust root");
  return { pack, receipt, verification };
}

async function installedPackLocation(stateRoot, id, version, allowedSignersPath, sshKeygenPath) {
  if (!ID_RE.test(id ?? "") || !VERSION_RE.test(version ?? "")) fail("capability removal identity is invalid");
  const root = await privateDirectory(resolve(stateRoot), "capability state root");
  const packs = await privateDirectory(join(root, "capability-packs"), "capability installation root");
  const packRoot = await privateDirectory(join(packs, id), "capability pack identity root");
  const destination = join(packRoot, version);
  const installed = await installedPackFromDirectory(destination, resolve(allowedSignersPath), sshKeygenPath);
  if (installed.pack.id !== id || installed.pack.version !== version) fail("installed capability identity differs from its directory");
  return { root, packs, packRoot, destination, ...installed };
}

export async function loadInstalledCapabilityPack(options) {
  return installedPackLocation(options.stateRoot, options.id, options.version, options.allowedSignersPath, options.sshKeygenPath ?? SSH_KEYGEN);
}

export async function reviewCapabilityPackRemoval({ stateRoot, id, version, allowedSignersPath, sshKeygenPath = SSH_KEYGEN }) {
  const installed = await installedPackLocation(stateRoot, id, version, allowedSignersPath, sshKeygenPath);
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: installed.root, id, version, packSha256: installed.verification.pack.packSha256, optional: true }));
  const activeImageAdmission = join(installed.root, "capability-pack-image-admissions", id, version);
  if (await lstat(activeImageAdmission).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) { await privateDirectory(activeImageAdmission, "capability image admission directory"); fail("capability pack still has an active image admission; revoke it before removal"); }
  const imageRevocation = join(installed.root, "capability-pack-image-revocations", "in-progress", id);
  const imageRevocationInfo = await lstat(imageRevocation).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (imageRevocationInfo) {
    await privateDirectory(imageRevocation, "capability image in-progress revocation identity root");
    if ((await readdir(imageRevocation)).some((name) => name.startsWith(`${version}-`) || name.startsWith(`.revocation-${version}-`))) fail("capability pack has an incomplete image-admission revocation; recover it before removal");
  }
  const imageCleanup = join(installed.root, "capability-pack-image-cleanups", id, `${version}-${installed.verification.pack.packSha256}`);
  const imageCleanupInfo = await lstat(imageCleanup).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (imageCleanupInfo) {
    await privateDirectory(imageCleanup, "capability image cleanup version root");
    if ((await readdir(imageCleanup)).length !== 0) fail("capability pack has cleanup-required image inspection state; recover it before removal");
  }
  if (operation) fail(`capability pack has an in-progress ${operation.kind} mutation; recover it before removal`);
  const review = {
    schemaVersion: 1, operation: "pixel-work-capability-pack-removal-review", status: "review-required",
    pack: { ...installed.verification.pack }, signatureSha256: installed.verification.signatureSha256,
    installationReceiptSha256: sha(canonical(installed.receipt)),
    effect: { deleteInstalledDeclaration: true, deleteImage: false, stopContainer: false, deleteWorkspace: false, changeController: false },
    enabled: false, authority: { ...authority }, boundary: removalReviewBoundary,
  };
  return Object.freeze({ ...review, confirmationSha256: sha(canonical(review)) });
}

function removalIntent(review, requestedAt) {
  if (!(requestedAt instanceof Date) || !Number.isSafeInteger(requestedAt.getTime())) fail("capability removal time is invalid");
  return {
    schemaVersion: 1, operation: "pixel-work-capability-pack-removal-intent",
    reviewSha256: review.confirmationSha256, pack: { ...review.pack }, signatureSha256: review.signatureSha256,
    installationReceiptSha256: review.installationReceiptSha256, requestedAt: requestedAt.toISOString(),
    enabled: false, authority: { ...authority }, boundary: removalReviewBoundary,
  };
}

function checkedRemovalIntent(value) {
  const keys = ["authority", "boundary", "enabled", "installationReceiptSha256", "operation", "pack", "requestedAt", "reviewSha256", "schemaVersion", "signatureSha256"];
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys)) fail("capability removal intent is invalid");
  if (value.schemaVersion !== 1 || value.operation !== "pixel-work-capability-pack-removal-intent" || value.enabled !== false || canonical(value.authority) !== canonical(authority) || value.boundary !== removalReviewBoundary) fail("capability removal intent is invalid");
  if (!ID_RE.test(value.pack?.id ?? "") || !VERSION_RE.test(value.pack?.version ?? "") || !SHA_RE.test(value.pack?.packSha256 ?? "") || !SHA_RE.test(value.pack?.treeSha256 ?? "") || !IDENTITY_RE.test(value.pack?.signerIdentity ?? "") || Object.keys(value.pack ?? {}).length !== 5) fail("capability removal intent pack is invalid");
  if (![value.reviewSha256, value.signatureSha256, value.installationReceiptSha256].every((item) => SHA_RE.test(item ?? "")) || !Number.isSafeInteger(Date.parse(value.requestedAt))) fail("capability removal intent binding is invalid");
  return value;
}

function removalCustody(intent) {
  return { schemaVersion: 1, operation: "pixel-work-capability-pack-removal-custody", pack: { ...intent.pack }, reviewSha256: intent.reviewSha256, signatureSha256: intent.signatureSha256, installationReceiptSha256: intent.installationReceiptSha256, enabled: false, authority: { ...authority }, boundary: removalCustodyBoundary };
}

function checkedRemovalCustody(value, intent) {
  const keys = ["authority", "boundary", "enabled", "installationReceiptSha256", "operation", "pack", "reviewSha256", "schemaVersion", "signatureSha256"];
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical(keys) || canonical(value) !== canonical(removalCustody(intent))) fail("capability removal custody receipt is invalid");
  return value;
}

function removalReceipt(intent, removedAt) {
  if (!(removedAt instanceof Date) || !Number.isSafeInteger(removedAt.getTime()) || removedAt.getTime() < Date.parse(intent.requestedAt)) fail("capability removal completion time is invalid");
  const receipt = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-pack-removal-v1.schema.json",
    schemaVersion: 1, operation: "pixel-work-capability-pack-removal", pack: { ...intent.pack },
    signatureSha256: intent.signatureSha256, installationReceiptSha256: intent.installationReceiptSha256,
    requestedAt: intent.requestedAt, removedAt: removedAt.toISOString(), enabled: false,
    residue: { installedDeclaration: false, image: false, container: false, workspace: false, controllerRegistration: false },
    authority: { ...authority }, boundary: removalBoundary,
  };
  try { assertJsonSchema(receipt, removalSchema, "capability removal receipt"); }
  catch { fail("capability removal receipt is invalid"); }
  return receipt;
}

async function readPrivateJson(path, maximumBytes, label) {
  try { return JSON.parse((await privateRegular(path, maximumBytes, label)).toString("utf8")); }
  catch (error) { if (error instanceof CapabilityPackInstallationError) throw error; fail(`${label} is not valid JSON`); }
}

async function safelyRemovePayload(payload, transaction) {
  const actualTransaction = await realpath(transaction), actualPayload = await realpath(payload);
  if (dirname(actualPayload) !== actualTransaction || basename(actualPayload) !== "payload") fail("capability removal payload escaped its transaction");
  await rm(actualPayload, { recursive: true, force: false });
  await syncDirectory(transaction);
}

async function finalizeRemoval({ root, packRoot, transaction, completedRoot, finalizedRoot, allowedSignersPath, sshKeygenPath, now, dependencies = {} }) {
  await privateDirectory(transaction, "capability removal transaction");
  const names = (await readdir(transaction)).sort();
  const accepted = [["intent.json", "payload"], ["custody.json", "intent.json", "payload"], ["custody.json", "intent.json"]];
  if (!accepted.some((value) => canonical(names) === canonical(value))) fail("capability removal transaction contains unknown files or lacks custody proof");
  const intent = checkedRemovalIntent(await readPrivateJson(join(transaction, "intent.json"), PACK_BYTES, "capability removal intent"));
  const payload = join(transaction, "payload");
  if (names.includes("payload")) {
    const installed = await installedPackFromDirectory(payload, resolve(allowedSignersPath), sshKeygenPath);
    if (canonical(installed.verification.pack) !== canonical(intent.pack) || installed.verification.signatureSha256 !== intent.signatureSha256 || sha(canonical(installed.receipt)) !== intent.installationReceiptSha256) fail("capability removal payload differs from its intent");
    const expectedCustody = removalCustody(intent), custodyPath = join(transaction, "custody.json");
    if (names.includes("custody.json")) checkedRemovalCustody(await readPrivateJson(custodyPath, PACK_BYTES, "capability removal custody receipt"), intent);
    else { await writeExclusive(custodyPath, `${JSON.stringify(expectedCustody, null, 2)}\n`); await syncDirectory(transaction); }
    if (dependencies.afterCustody) await dependencies.afterCustody();
    await safelyRemovePayload(payload, transaction);
  } else checkedRemovalCustody(await readPrivateJson(join(transaction, "custody.json"), PACK_BYTES, "capability removal custody receipt"), intent);
  const completedIdRoot = await privateDirectory(join(completedRoot, intent.pack.id), "capability removal tombstone identity root", true);
  const tombstonePath = join(completedIdRoot, `${intent.pack.version}-${intent.pack.packSha256}.json`);
  let receipt;
  const existing = await lstat(tombstonePath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (existing) {
    receipt = await readPrivateJson(tombstonePath, PACK_BYTES, "capability removal tombstone");
    try { assertJsonSchema(receipt, removalSchema, "capability removal tombstone"); }
    catch { fail("capability removal tombstone is invalid"); }
    const expected = removalReceipt(intent, new Date(receipt.removedAt));
    if (canonical(receipt) !== canonical(expected)) fail("capability removal tombstone differs from its transaction");
  } else {
    receipt = removalReceipt(intent, now);
    await writeExclusive(tombstonePath, `${JSON.stringify(receipt, null, 2)}\n`);
    await syncDirectory(completedIdRoot);
  }
  if (dependencies.afterTombstone) await dependencies.afterTombstone();
  const finalizedIdRoot = await privateDirectory(join(finalizedRoot, intent.pack.id), "capability finalized removal identity root", true);
  const finalizedTransaction = join(finalizedIdRoot, `${intent.pack.version}-${intent.pack.packSha256}`);
  if (await lstat(finalizedTransaction).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("capability finalized removal transaction already exists");
  await rename(transaction, finalizedTransaction);
  await syncDirectory(dirname(transaction)); await syncDirectory(finalizedIdRoot);
  await rmdir(packRoot).catch((error) => { if (!["ENOTEMPTY", "EEXIST", "ENOENT"].includes(error?.code)) throw error; });
  await syncDirectory(join(root, "capability-packs"));
  return Object.freeze({
    schemaVersion: 1, operation: receipt.operation, status: "removed-no-residue", pack: { ...receipt.pack },
    removalReceiptSha256: sha(canonical(receipt)), installed: false, enabled: false, residue: { ...receipt.residue },
    authority: { ...authority }, boundary: removalBoundary,
  });
}

async function removalRoots(root, id, create) {
  const removals = await privateDirectory(join(root, "capability-pack-removals"), "capability removal root", create);
  const inProgress = await privateDirectory(join(removals, "in-progress"), "capability in-progress removal root", create);
  const completed = await privateDirectory(join(removals, "completed"), "capability completed removal root", create);
  const finalized = await privateDirectory(join(removals, "finalized"), "capability finalized removal root", create);
  const identity = await privateDirectory(join(inProgress, id), "capability removal identity root", create);
  return { removals, inProgress, completed, finalized, identity };
}

export async function removeCapabilityPack({ stateRoot, id, version, confirmReviewSha256, allowedSignersPath, sshKeygenPath = SSH_KEYGEN, now = new Date(), dependencies = {} }) {
  if (!dependencies || typeof dependencies !== "object" || Array.isArray(dependencies) || ["afterIntent", "afterMove", "afterCustody", "afterTombstone", "afterFinalize"].some((key) => dependencies[key] !== undefined && typeof dependencies[key] !== "function")) fail("capability removal dependencies are invalid");
  const review = await reviewCapabilityPackRemoval({ stateRoot, id, version, allowedSignersPath, sshKeygenPath });
  if (!SHA_RE.test(confirmReviewSha256 ?? "") || confirmReviewSha256 !== review.confirmationSha256) fail("capability removal confirmation differs from the current review");
  const intent = removalIntent(review, now);
  const operation = await operationCall(acquireCapabilityPackOperation({ stateRoot, pack: review.pack, kind: "pack-removal", bindingSha256: review.confirmationSha256 }));
  let installed;
  try { installed = await installedPackLocation(stateRoot, id, version, allowedSignersPath, sshKeygenPath); }
  catch (error) { await operationCall(releaseCapabilityPackOperation({ stateRoot, operation })); throw error; }
  if (canonical(installed.verification.pack) !== canonical(review.pack) || installed.verification.signatureSha256 !== review.signatureSha256 || sha(canonical(installed.receipt)) !== review.installationReceiptSha256) {
    await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
    fail("capability installation changed after removal review");
  }
  let roots;
  try { roots = await removalRoots(installed.root, id, true); }
  catch (error) { await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation })); throw error; }
  const transaction = join(roots.identity, `${version}-${review.pack.packSha256}`);
  try { await mkdir(transaction, { mode: 0o700 }); }
  catch (error) {
    await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
    if (error?.code === "EEXIST") fail("capability removal already has an in-progress transaction; recover it"); throw error;
  }
  let moved = false;
  try {
    await writeExclusive(join(transaction, "intent.json"), `${JSON.stringify(intent, null, 2)}\n`);
    await syncDirectory(transaction);
  } catch (error) {
    await unlink(join(transaction, "intent.json")).catch(() => {});
    await rmdir(transaction).catch(() => {});
    await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
    throw error;
  }
  if (dependencies.afterIntent) await dependencies.afterIntent();
  try {
    await rename(installed.destination, join(transaction, "payload"));
    moved = true;
    await syncDirectory(installed.packRoot); await syncDirectory(transaction);
  } catch (error) {
    if (!moved) {
      await unlink(join(transaction, "intent.json")).catch(() => {});
      await rmdir(transaction).catch(() => {});
      await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
    }
    throw error;
  }
  if (dependencies.afterMove) await dependencies.afterMove();
  const result = await finalizeRemoval({ root: installed.root, packRoot: installed.packRoot, transaction, completedRoot: roots.completed, finalizedRoot: roots.finalized, allowedSignersPath, sshKeygenPath, now, dependencies });
  if (dependencies.afterFinalize) await dependencies.afterFinalize();
  await operationCall(releaseCapabilityPackOperation({ stateRoot: installed.root, operation }));
  return result;
}

async function finalizedRemovalResult({ root, roots, id, version, packSha256, confirmReviewSha256 }) {
  const transaction = join(roots.finalized, id, `${version}-${packSha256}`);
  await privateDirectory(transaction, "capability finalized removal transaction");
  if (canonical((await readdir(transaction)).sort()) !== canonical(["custody.json", "intent.json"])) fail("capability finalized removal transaction contains unknown files");
  const intent = checkedRemovalIntent(await readPrivateJson(join(transaction, "intent.json"), PACK_BYTES, "capability removal intent"));
  if (intent.pack.id !== id || intent.pack.version !== version || intent.pack.packSha256 !== packSha256 || intent.reviewSha256 !== confirmReviewSha256) fail("capability finalized removal differs from its recovery binding");
  checkedRemovalCustody(await readPrivateJson(join(transaction, "custody.json"), PACK_BYTES, "capability removal custody receipt"), intent);
  const tombstonePath = join(roots.completed, id, `${version}-${packSha256}.json`);
  const receipt = await readPrivateJson(tombstonePath, PACK_BYTES, "capability removal tombstone");
  try { assertJsonSchema(receipt, removalSchema, "capability removal tombstone"); } catch { fail("capability removal tombstone is invalid"); }
  if (canonical(receipt) !== canonical(removalReceipt(intent, new Date(receipt.removedAt)))) fail("capability removal tombstone differs from its finalized transaction");
  if (await lstat(join(root, "capability-packs", id, version)).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("capability finalized removal still has an installed declaration");
  return Object.freeze({ schemaVersion: 1, operation: receipt.operation, status: "removed-no-residue", pack: { ...receipt.pack }, removalReceiptSha256: sha(canonical(receipt)), installed: false, enabled: false, residue: { ...receipt.residue }, authority: { ...authority }, boundary: removalBoundary });
}

export async function recoverCapabilityPackRemoval({ stateRoot, id, version, packSha256, confirmReviewSha256, allowedSignersPath, sshKeygenPath = SSH_KEYGEN, now = new Date() }) {
  if (!ID_RE.test(id ?? "") || !VERSION_RE.test(version ?? "") || !SHA_RE.test(packSha256 ?? "") || !SHA_RE.test(confirmReviewSha256 ?? "")) fail("capability removal recovery binding is invalid");
  const root = await privateDirectory(resolve(stateRoot), "capability state root");
  const operation = await operationCall(loadCapabilityPackOperation({ stateRoot: root, id, version, packSha256 }));
  if (operation.kind !== "pack-removal" || operation.bindingSha256 !== confirmReviewSha256) fail("capability removal recovery differs from its transaction or mutation custody");
  const packs = await privateDirectory(join(root, "capability-packs"), "capability installation root");
  const packRootPath = join(packs, id);
  const packRootInfo = await lstat(packRootPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (packRootInfo) await privateDirectory(packRootPath, "capability pack identity root");
  const roots = await removalRoots(root, id, false);
  const transaction = join(roots.identity, `${version}-${packSha256}`);
  const transactionInfo = await lstat(transaction).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!transactionInfo) {
    const finalized = join(roots.finalized, id, `${version}-${packSha256}`), finalizedInfo = await lstat(finalized).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (finalizedInfo) {
      const settled = await finalizedRemovalResult({ root, roots, id, version, packSha256, confirmReviewSha256 });
      await operationCall(releaseCapabilityPackOperation({ stateRoot: root, operation }));
      return settled;
    }
    const installed = await installedPackLocation(root, id, version, allowedSignersPath, sshKeygenPath);
    if (canonical(installed.verification.pack) !== canonical(operation.pack)) fail("capability removal recovery no-change state differs from its mutation custody");
    await operationCall(releaseCapabilityPackOperation({ stateRoot: root, operation }));
    return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-pack-removal-recovery", status: "aborted-no-change", pack: { ...operation.pack }, installed: true, enabled: false, authority: { ...authority }, boundary: removalReviewBoundary });
  }
  await privateDirectory(transaction, "capability removal transaction");
  let transactionNames = (await readdir(transaction)).sort();
  if (transactionNames.length === 0) {
    const installed = await installedPackLocation(root, id, version, allowedSignersPath, sshKeygenPath);
    if (canonical(installed.verification.pack) !== canonical(operation.pack)) fail("empty capability removal transaction differs from its mutation custody");
    await rmdir(transaction); await syncDirectory(roots.identity);
    await operationCall(releaseCapabilityPackOperation({ stateRoot: root, operation }));
    return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-pack-removal-recovery", status: "aborted-no-change", pack: { ...operation.pack }, installed: true, enabled: false, authority: { ...authority }, boundary: removalReviewBoundary });
  }
  const intent = checkedRemovalIntent(await readPrivateJson(join(transaction, "intent.json"), PACK_BYTES, "capability removal intent"));
  if (intent.pack.id !== id || intent.pack.version !== version || intent.pack.packSha256 !== packSha256 || intent.reviewSha256 !== confirmReviewSha256) fail("capability removal recovery differs from its transaction");
  if (canonical(transactionNames) === canonical(["intent.json"])) {
    const installed = await installedPackLocation(root, id, version, allowedSignersPath, sshKeygenPath);
    if (canonical(installed.verification.pack) !== canonical(intent.pack) || installed.verification.signatureSha256 !== intent.signatureSha256 || sha(canonical(installed.receipt)) !== intent.installationReceiptSha256) fail("capability removal recovery payload differs from its intent");
    await rename(installed.destination, join(transaction, "payload")); await syncDirectory(installed.packRoot); await syncDirectory(transaction);
    transactionNames = ["intent.json", "payload"];
  }
  const result = await finalizeRemoval({ root, packRoot: packRootInfo ? packRootPath : join(packs, id), transaction, completedRoot: roots.completed, finalizedRoot: roots.finalized, allowedSignersPath, sshKeygenPath, now });
  await operationCall(releaseCapabilityPackOperation({ stateRoot: root, operation }));
  return result;
}

async function capabilityRemovalStatus(root) {
  const removalsPath = join(root, "capability-pack-removals");
  const removalInfo = await lstat(removalsPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!removalInfo) return { completed: 0, recoveryRequired: 0 };
  const removals = await privateDirectory(removalsPath, "capability removal root");
  const rootNames = (await readdir(removals)).sort();
  if (canonical(rootNames) !== canonical(["completed", "finalized", "in-progress"])) fail("capability removal root contains unknown entries");
  const inProgress = await privateDirectory(join(removals, "in-progress"), "capability in-progress removal root");
  const completed = await privateDirectory(join(removals, "completed"), "capability completed removal root");
  const finalized = await privateDirectory(join(removals, "finalized"), "capability finalized removal root");
  let recoveryRequired = 0, completedCount = 0;
  const inProgressKeys = new Set(), finalizedKeys = new Set(), completedKeys = new Set();
  for (const id of (await readdir(inProgress)).sort()) {
    if (!ID_RE.test(id)) fail("capability in-progress removal root contains an unsafe entry");
    const identity = await privateDirectory(join(inProgress, id), "capability removal identity root");
    for (const name of (await readdir(identity)).sort()) {
      if (!VERSION_RE.test(name.slice(0, name.length - 65)) || name.at(-65) !== "-" || !SHA_RE.test(name.slice(-64))) fail("capability removal identity root contains an unsafe entry");
      const transaction = await privateDirectory(join(identity, name), "capability removal transaction");
      const names = (await readdir(transaction)).sort();
      const accepted = [[], ["intent.json"], ["intent.json", "payload"], ["custody.json", "intent.json", "payload"], ["custody.json", "intent.json"]];
      if (!accepted.some((value) => canonical(names) === canonical(value))) fail("capability removal transaction contains unknown files");
      if (names.length !== 0) {
        const intent = checkedRemovalIntent(await readPrivateJson(join(transaction, "intent.json"), PACK_BYTES, "capability removal intent"));
        if (intent.pack.id !== id || `${intent.pack.version}-${intent.pack.packSha256}` !== name) fail("capability removal transaction identity differs from its directory");
        if (names.includes("custody.json")) checkedRemovalCustody(await readPrivateJson(join(transaction, "custody.json"), PACK_BYTES, "capability removal custody receipt"), intent);
      }
      inProgressKeys.add(`${id}/${name}`);
      recoveryRequired += 1;
    }
  }
  for (const id of (await readdir(finalized)).sort()) {
    if (!ID_RE.test(id)) fail("capability finalized removal root contains an unsafe entry");
    const identity = await privateDirectory(join(finalized, id), "capability finalized removal identity root");
    for (const name of (await readdir(identity)).sort()) {
      if (!VERSION_RE.test(name.slice(0, name.length - 65)) || name.at(-65) !== "-" || !SHA_RE.test(name.slice(-64))) fail("capability finalized removal identity root contains an unsafe entry");
      const transaction = await privateDirectory(join(identity, name), "capability finalized removal transaction");
      if (canonical((await readdir(transaction)).sort()) !== canonical(["custody.json", "intent.json"])) fail("capability finalized removal transaction contains unknown files");
      const intent = checkedRemovalIntent(await readPrivateJson(join(transaction, "intent.json"), PACK_BYTES, "capability removal intent"));
      if (intent.pack.id !== id || `${intent.pack.version}-${intent.pack.packSha256}` !== name) fail("capability finalized removal transaction identity differs from its directory");
      checkedRemovalCustody(await readPrivateJson(join(transaction, "custody.json"), PACK_BYTES, "capability removal custody receipt"), intent);
      finalizedKeys.add(`${id}/${name}`);
    }
  }
  for (const id of (await readdir(completed)).sort()) {
    if (!ID_RE.test(id)) fail("capability completed removal root contains an unsafe entry");
    const identity = await privateDirectory(join(completed, id), "capability removal tombstone identity root");
    for (const name of (await readdir(identity)).sort()) {
      if (!name.endsWith(".json") || !VERSION_RE.test(name.slice(0, name.length - 70)) || name.at(-70) !== "-" || !SHA_RE.test(name.slice(-69, -5))) fail("capability removal tombstone root contains an unsafe entry");
      const receipt = await readPrivateJson(join(identity, name), PACK_BYTES, "capability removal tombstone");
      try { assertJsonSchema(receipt, removalSchema, "capability removal tombstone"); }
      catch { fail("capability removal tombstone is invalid"); }
      if (receipt.pack.id !== id || `${receipt.pack.version}-${receipt.pack.packSha256}.json` !== name) fail("capability removal tombstone identity differs from its directory");
      completedKeys.add(`${id}/${name.slice(0, -5)}`);
      completedCount += 1;
    }
  }
  if ([...finalizedKeys].some((key) => !completedKeys.has(key)) || [...completedKeys].some((key) => !finalizedKeys.has(key) && !inProgressKeys.has(key))) fail("capability finalized removal audit differs from its tombstones");
  return { completed: completedCount, recoveryRequired };
}

export async function installCapabilityPack({ stateRoot, packPath, signaturePath, allowedSignersPath, identity, sshKeygenPath = SSH_KEYGEN, now = new Date() }) {
  const verification = await verifyCapabilityPack({ packPath, signaturePath, allowedSignersPath, identity, sshKeygenPath });
  const pack = parsePack(await descriptorSnapshot(resolve(packPath), PACK_BYTES, "capability pack"));
  if (verification.pack.packSha256 !== packSha(pack)) fail("capability pack changed after signature verification");
  const root = await privateDirectory(resolve(stateRoot), "capability state root");
  const packs = await privateDirectory(join(root, "capability-packs"), "capability installation root", true);
  const removedIdentity = join(root, "capability-pack-removals", "completed", pack.id);
  const removedInfo = await lstat(removedIdentity).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (removedInfo) {
    await privateDirectory(removedIdentity, "capability removal tombstone identity root");
    if ((await readdir(removedIdentity)).some((name) => name.startsWith(`${pack.version}-`))) fail("capability pack version was previously removed; publish a new signed version instead of reinstalling it");
  }
  const packRoot = await privateDirectory(join(packs, pack.id), "capability pack identity root", true);
  const destination = join(packRoot, pack.version);
  if (await lstat(destination).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error))) fail("capability pack version is already installed");
  const stage = join(packRoot, `.install-${randomBytes(8).toString("hex")}`);
  const signature = await descriptorSnapshot(resolve(signaturePath), SIGNATURE_BYTES, "capability signature");
  const receipt = installationReceipt(pack, verification, now);
  try {
    await mkdir(stage, { mode: 0o700 });
    await writeExclusive(join(stage, "pack.json"), `${canonical(pack)}\n`);
    await writeExclusive(join(stage, "pack.sig"), signature);
    await writeExclusive(join(stage, "install-receipt.json"), `${JSON.stringify(receipt, null, 2)}\n`);
    await syncDirectory(stage);
    const staged = await installedPackFromDirectory(stage, resolve(allowedSignersPath), sshKeygenPath);
    if (staged.verification.allowedSignersSha256 !== verification.allowedSignersSha256) fail("allowed signers changed during capability installation");
    await rename(stage, destination);
    await syncDirectory(packRoot);
  } catch (error) {
    await rm(stage, { recursive: true, force: true }).catch(() => {});
    throw error;
  }
  return Object.freeze({
    schemaVersion: pack.schemaVersion, operation: receipt.operation, status: "installed-disabled",
    pack: { ...receipt.pack }, signatureSha256: receipt.signatureSha256,
    installed: true, enabled: false, image: { ...receipt.image }, authority: { ...authority }, boundary,
  });
}

export async function statusCapabilityPacks({ stateRoot, allowedSignersPath, sshKeygenPath = SSH_KEYGEN }) {
  const root = await privateDirectory(resolve(stateRoot), "capability state root");
  const removals = await capabilityRemovalStatus(root), operations = await operationCall(auditCapabilityPackOperationRoot(root));
  const packsPath = join(root, "capability-packs");
  const exists = await lstat(packsPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (!exists) return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-pack-status", status: removals.recoveryRequired || operations.active ? "recovery-required" : "empty", packs: [], enabled: 0, removals, operations, authority: { ...authority }, boundary });
  const packs = await privateDirectory(packsPath, "capability installation root");
  const records = [];
  for (const id of (await readdir(packs)).sort()) {
    if (!ID_RE.test(id)) fail("capability installation root contains an unsafe or incomplete entry");
    const idRoot = await privateDirectory(join(packs, id), "capability pack identity root");
    for (const version of (await readdir(idRoot)).sort()) {
      if (!VERSION_RE.test(version)) fail("capability pack identity root contains an unsafe or incomplete entry");
      const installed = await installedPackFromDirectory(join(idRoot, version), resolve(allowedSignersPath), sshKeygenPath);
      if (installed.pack.id !== id || installed.pack.version !== version) fail("installed capability identity differs from its directory");
      records.push({ schemaVersion: installed.pack.schemaVersion, id, version, packSha256: installed.verification.pack.packSha256, treeSha256: installed.pack.provenance.treeSha256, signerIdentity: installed.pack.provenance.signerIdentity, trusted: true, installed: true, enabled: false, image: { ...installed.receipt.image } });
    }
  }
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-pack-status", status: removals.recoveryRequired || operations.active ? "recovery-required" : records.length ? "verified-disabled" : "empty", packs: records, enabled: 0, removals, operations, authority: { ...authority }, boundary });
}

export const capabilityPackInstallationBoundary = boundary;
export const capabilityPackSshKeygenPath = SSH_KEYGEN;
