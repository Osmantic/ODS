import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { chmod, lstat, mkdir, mkdtemp, open, readFile, realpath, rm } from "node:fs/promises";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { tmpdir } from "node:os";

import { canonical, validateWorkCapabilitySshApprovalV2 } from "../../scripts/lib/work-contract.mjs";
import { writeOwnerPrivateCreateNoClobber, fsyncDirectory } from "./maintenance-secure-files.mjs";

const APPROVAL_BYTES = 64 * 1024;
const SIGNATURE_BYTES = 64 * 1024;
const SIGNERS_BYTES = 256 * 1024;
const KEY_BYTES = 256 * 1024;
const MAX_APPROVAL_LIFETIME_MS = 10 * 60 * 1000;
const NAMESPACE = "pixel-work-capability-ssh-approval-v2";
const SSH_KEYGEN = process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH\\ssh-keygen.exe" : "/usr/bin/ssh-keygen";
const IDENTITY_RE = /^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$/u;

const CONSUMPTION_BOUNDARY = "Private create-only consumption of one verified owner-signed SSH approval nonce. It prevents replay and grants no future execution, generic shell, command or destination selection, credential disclosure, external effect, scope expansion, or completion.";

export class CapabilitySshApprovalV2Error extends Error {}

function fail(message) { throw new CapabilitySshApprovalV2Error(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} is invalid: ${errors[0]}`); }

function checkedClock(clock) {
  const value = (clock ?? (() => new Date()))();
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail("SSH approval trusted clock is invalid");
  return value;
}

async function snapshot(path, maximum, label, { ownerOnly = false, rejectWritable = false } = {}) {
  if (!isAbsolute(path) || resolve(path) !== path) fail(`${label} path must be absolute and normalized`);
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)); }
  catch (error) { if (error?.code === "ELOOP") fail(`${label} must not be a symlink`); throw error; }
  try {
    const opened = await handle.stat();
    const current = await lstat(path);
    if (!opened.isFile() || opened.nlink !== 1 || opened.size < 1 || opened.size > maximum || current.isSymbolicLink() || opened.dev !== current.dev || opened.ino !== current.ino) fail(`${label} must be a descriptor-bound regular single-link file within its byte limit`);
    if (process.platform !== "win32" && opened.uid !== process.geteuid()) fail(`${label} must be owned by the service user`);
    if (process.platform !== "win32" && ownerOnly && (opened.mode & 0o077) !== 0) fail(`${label} must be owner-only`);
    if (process.platform !== "win32" && rejectWritable && (opened.mode & 0o022) !== 0) fail(`${label} must not be group/world writable`);
    const bytes = await handle.readFile();
    if (bytes.length > maximum) fail(`${label} exceeds its byte limit`);
    return bytes;
  } finally { await handle.close(); }
}

async function privateDirectory(path, label, create = false) {
  if (!isAbsolute(path) || resolve(path) !== path || resolve(path) === dirname(resolve(path))) fail(`${label} must be an absolute normalized non-root directory`);
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || await realpath(path) !== path) fail(`${label} must be a real non-linked directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be service-owner private`);
  return path;
}

function signatureBytes(signature) {
  if (typeof signature !== "string") fail("SSH approval signature is missing");
  const bytes = Buffer.from(signature, "ascii");
  if (bytes.length < 32 || bytes.length > SIGNATURE_BYTES || bytes.toString("ascii") !== signature) fail("SSH approval signature envelope is malformed");
  const lines = signature.trim().split(/\r?\n/u);
  if (lines.length < 3 || lines[0] !== "-----BEGIN SSH SIGNATURE-----" || lines.at(-1) !== "-----END SSH SIGNATURE-----") fail("SSH approval signature envelope is malformed");
  const encoded = lines.slice(1, -1).join("");
  if (!/^[A-Za-z0-9+/]+={0,2}$/u.test(encoded) || encoded.length % 4 !== 0) fail("SSH approval signature envelope is malformed");
  const decoded = Buffer.from(encoded, "base64");
  if (!decoded.length || decoded.toString("base64") !== encoded) fail("SSH approval signature envelope is malformed");
  return bytes;
}

async function withPrivateFiles(files, callback) {
  const directory = await mkdtemp(join(tmpdir(), "pixel-work-ssh-approval-"));
  if (process.platform !== "win32") await chmod(directory, 0o700);
  try {
    const paths = {};
    for (const [name, bytes] of Object.entries(files)) {
      const path = join(directory, name);
      const handle = await open(path, "wx", 0o600);
      try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
      paths[name] = path;
    }
    return await callback(paths);
  } finally { await rm(directory, { recursive: true, force: true }); }
}

function runSshKeygen(args, input) {
  const result = spawnSync(SSH_KEYGEN, args, { input, encoding: input === undefined ? "utf8" : undefined, windowsHide: true, maxBuffer: 512 * 1024, timeout: 30000, shell: false });
  if (result.error || result.status !== 0) fail("SSH approval signature is invalid or its owner is not trusted");
}

function checkedApproval(approval, clock) {
  schema("SSH approval", validateWorkCapabilitySshApprovalV2(approval));
  const now = checkedClock(clock).getTime();
  const issued = Date.parse(approval.issuedAt), expires = Date.parse(approval.expiresAt);
  if (!Number.isFinite(issued) || !Number.isFinite(expires) || issued > now || now >= expires) fail("SSH approval is not currently valid");
  if (expires - issued > MAX_APPROVAL_LIFETIME_MS) fail("SSH approval lifetime exceeds the ten-minute ceiling");
  return approval;
}

export async function verifyCapabilitySshApprovalV2({ approval, signature, allowedSignersPath, identity, clock } = {}) {
  const checked = checkedApproval(approval, clock);
  if (!IDENTITY_RE.test(identity ?? "")) fail("SSH approval signer identity is invalid");
  const approvalBytes = Buffer.from(canonical(checked));
  if (approvalBytes.length > APPROVAL_BYTES) fail("SSH approval exceeds its byte ceiling");
  const signaturePayload = signatureBytes(signature);
  const signers = await snapshot(resolve(allowedSignersPath), SIGNERS_BYTES, "SSH approval allowed signers", { rejectWritable: true });
  await withPrivateFiles({ "approval.json": approvalBytes, "approval.sig": signaturePayload, "allowed_signers": signers }, async (paths) => {
    runSshKeygen(["-Y", "verify", "-f", paths.allowed_signers, "-I", identity, "-n", NAMESPACE, "-s", paths["approval.sig"]], await readFile(paths["approval.json"]));
  });
  return Object.freeze({ approval: Object.freeze(structuredClone(checked)), approvalSha256: sha(approvalBytes), signatureSha256: sha(signaturePayload), signerIdentity: identity });
}

export async function signCapabilitySshApprovalV2({ approval, signingKeyPath, clock } = {}) {
  const checked = checkedApproval(approval, clock);
  const approvalBytes = Buffer.from(canonical(checked));
  const keyPath = resolve(signingKeyPath);
  const key = await snapshot(keyPath, KEY_BYTES, "SSH approval signing key", { ownerOnly: true });
  return withPrivateFiles({ "approval.json": approvalBytes, "signing-key": key }, async (paths) => {
    const signingPath = process.platform === "win32" ? keyPath : paths["signing-key"];
    runSshKeygen(["-Y", "sign", "-f", signingPath, "-n", NAMESPACE, paths["approval.json"]]);
    if (process.platform === "win32" && sha(await snapshot(keyPath, KEY_BYTES, "SSH approval signing key", { ownerOnly: true })) !== sha(key)) fail("SSH approval signing key changed during signing");
    const result = await snapshot(`${paths["approval.json"]}.sig`, SIGNATURE_BYTES, "SSH approval signature");
    signatureBytes(result.toString("ascii"));
    return result.toString("ascii");
  });
}

export async function consumeCapabilitySshApprovalV2({ stateRoot, verified, requestId, destinationAlias, commandId, credentialRef } = {}) {
  if (!verified?.approval || verified.approvalSha256 !== sha(Buffer.from(canonical(verified.approval))) || verified.signatureSha256 == null) fail("SSH approval consumption requires a verified approval");
  const approval = verified.approval;
  if (approval.requestId !== requestId || approval.destinationAlias !== destinationAlias || approval.commandId !== commandId || approval.credentialRef !== credentialRef) fail("SSH approval differs from the exact runtime request");
  const root = await privateDirectory(resolve(stateRoot), "SSH approval state root");
  const runtime = await privateDirectory(join(root, "v2-runtime"), "SSH approval runtime root", true);
  const consumed = await privateDirectory(join(runtime, "ssh-approvals"), "SSH approval consumption root", true);
  const record = Object.freeze({
    schemaVersion: 1, operation: "pixel-work-capability-ssh-approval-consumption-v2",
    nonce: approval.nonce, requestId, approvalSha256: verified.approvalSha256,
    signatureSha256: verified.signatureSha256, signerIdentity: verified.signerIdentity,
    destinationAlias, commandId, credentialRef, consumedAt: new Date().toISOString(),
    boundary: CONSUMPTION_BOUNDARY,
  });
  try { await writeOwnerPrivateCreateNoClobber(join(consumed, `${approval.nonce}.json`), `${JSON.stringify(record, null, 2)}\n`, process.geteuid?.() ?? 0); }
  catch (error) { if (error?.message?.includes("already exists")) fail("SSH approval nonce was already consumed; no replay"); throw error; }
  await fsyncDirectory(consumed);
  return Object.freeze({ record, recordSha256: sha(record) });
}

export const capabilitySshApprovalV2Namespace = NAMESPACE;
export const capabilitySshApprovalV2ConsumptionBoundary = CONSUMPTION_BOUNDARY;
