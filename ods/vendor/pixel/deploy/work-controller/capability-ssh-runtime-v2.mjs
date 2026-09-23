import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open } from "node:fs/promises";
import { basename, isAbsolute, resolve } from "node:path";
import { isIP } from "node:net";
import { TextDecoder } from "node:util";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { consumeCapabilitySshApprovalV2, verifyCapabilitySshApprovalV2 } from "./capability-ssh-approval-v2.mjs";

const DESTINATION_RE = /^[a-z][a-z0-9-]{1,62}$/u;
const USER_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const CREDENTIAL_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/u;
const HOSTNAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$/u;
const RESULT_BOUNDARY = "Bounded untrusted output from one owner-signed single-use SSH forced-command connection. It proves only that the pinned destination returned the exact trusted hostname and grants no replay, generic shell, command or destination selection, credential disclosure, external effect, scope expansion, or completion.";

export class CapabilitySshRuntimeV2Error extends Error {}
function fail(message) { throw new CapabilitySshRuntimeV2Error(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}

async function secureRegular(path, maximumBytes, label, { executable = false, ownerOnly = false } = {}) {
  if (!isAbsolute(path) || resolve(path) !== path) fail(`${label} path must be absolute and normalized`);
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)); }
  catch (error) { if (error?.code === "ELOOP") fail(`${label} must not be a symlink`); throw error; }
  try {
    const opened = await handle.stat(), current = await lstat(path);
    if (!opened.isFile() || opened.nlink !== 1 || opened.size < 1 || opened.size > maximumBytes || current.isSymbolicLink() || opened.dev !== current.dev || opened.ino !== current.ino) fail(`${label} must be a descriptor-bound regular single-link file within its byte limit`);
    if (process.platform !== "win32" && (opened.mode & 0o022) !== 0) fail(`${label} must not be group/world writable`);
    if (process.platform !== "win32" && ownerOnly && (opened.uid !== process.geteuid() || (opened.mode & 0o077) !== 0)) fail(`${label} must be service-owner private`);
    if (process.platform !== "win32" && executable && (opened.mode & 0o111) === 0) fail(`${label} is not executable`);
    return opened;
  } finally { await handle.close(); }
}

function checkedConfig(config, destinationAlias) {
  exactKeys(config, ["sshBinary", "allowedSignersPath", "operatorIdentity", "destinations", "stateRoot", "clock"], "SSH runtime trusted config");
  if (typeof config.sshBinary !== "string" || !isAbsolute(config.sshBinary) || resolve(config.sshBinary) !== config.sshBinary || basename(config.sshBinary) !== "ssh") fail("SSH runtime binary must be an absolute normalized executable named ssh");
  if (typeof config.allowedSignersPath !== "string" || !isAbsolute(config.allowedSignersPath) || resolve(config.allowedSignersPath) !== config.allowedSignersPath) fail("SSH runtime allowed-signers path is invalid");
  if (typeof config.stateRoot !== "string" || !isAbsolute(config.stateRoot) || resolve(config.stateRoot) !== config.stateRoot) fail("SSH runtime state root is invalid");
  if (typeof config.operatorIdentity !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}$/u.test(config.operatorIdentity)) fail("SSH runtime operator identity is invalid");
  if (typeof config.clock !== "function") fail("SSH runtime requires a trusted clock");
  if (!config.destinations || typeof config.destinations !== "object" || Array.isArray(config.destinations) || !DESTINATION_RE.test(destinationAlias ?? "")) fail("SSH runtime destination map or alias is invalid");
  const destination = config.destinations[destinationAlias];
  exactKeys(destination, ["enabled", "host", "port", "user", "credentialRef", "identityFile", "knownHostsFile", "commandId", "expectedHostname"], "SSH runtime trusted destination");
  if (destination.enabled !== true || isIP(destination.host) === 0 || !Number.isSafeInteger(destination.port) || destination.port < 1 || destination.port > 65535 || !USER_RE.test(destination.user ?? "")) fail("SSH runtime trusted destination is disabled or invalid");
  if (!CREDENTIAL_RE.test(destination.credentialRef ?? "") || destination.commandId !== "hostname" || !HOSTNAME_RE.test(destination.expectedHostname ?? "")) fail("SSH runtime trusted credential, command, or expected hostname is invalid");
  for (const [name, path] of Object.entries({ identityFile: destination.identityFile, knownHostsFile: destination.knownHostsFile })) if (typeof path !== "string" || !isAbsolute(path) || resolve(path) !== path) fail(`SSH runtime trusted ${name} is invalid`);
  return destination;
}

export function buildCapabilitySshHostnameInvocationV2(config, approval) {
  const destination = checkedConfig(config, approval?.destinationAlias);
  if (approval.commandId !== destination.commandId || approval.credentialRef !== destination.credentialRef) fail("SSH approval does not match the trusted destination command and credential");
  const target = `${destination.user}@${destination.host}`;
  const args = [
    "-F", process.platform === "win32" ? "NUL" : "/dev/null",
    "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "IdentityAgent=none",
    "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
    "-o", "PreferredAuthentications=publickey", "-o", "StrictHostKeyChecking=yes",
    "-o", `UserKnownHostsFile=${destination.knownHostsFile}`, "-o", `GlobalKnownHostsFile=${process.platform === "win32" ? "NUL" : "/dev/null"}`,
    "-o", "CheckHostIP=yes", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
    "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no", "-o", "RequestTTY=no",
    "-o", "ProxyCommand=none", "-o", "CanonicalizeHostname=no", "-o", "UpdateHostKeys=no",
    "-o", "AddKeysToAgent=no", "-o", "VerifyHostKeyDNS=no", "-o", "ControlMaster=no", "-o", "ControlPath=none",
    "-o", `ConnectTimeout=${Math.max(1, Math.ceil(approval.timeoutMs / 1000))}`,
    "-p", String(destination.port), "-i", destination.identityFile, "--", target,
  ];
  return Object.freeze({ binary: config.sshBinary, args: Object.freeze(args), destination: Object.freeze({ alias: approval.destinationAlias, expectedHostname: destination.expectedHostname, credentialRef: destination.credentialRef }) });
}

function decodeBounded(bytes, label) {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { fail(`SSH ${label} is not valid UTF-8`); }
}

export async function executeCapabilitySshHostnameV2({ config, approval, signature } = {}) {
  const invocation = buildCapabilitySshHostnameInvocationV2(config, approval);
  await secureRegular(invocation.binary, 16 * 1024 * 1024, "SSH runtime binary", { executable: true });
  await secureRegular(config.destinations[approval.destinationAlias].identityFile, 256 * 1024, "SSH runtime identity credential", { ownerOnly: true });
  await secureRegular(config.destinations[approval.destinationAlias].knownHostsFile, 1024 * 1024, "SSH runtime pinned known-hosts file");
  const verified = await verifyCapabilitySshApprovalV2({ approval, signature, allowedSignersPath: config.allowedSignersPath, identity: config.operatorIdentity, clock: config.clock });
  const consumed = await consumeCapabilitySshApprovalV2({
    stateRoot: config.stateRoot, verified, requestId: approval.requestId, destinationAlias: approval.destinationAlias,
    commandId: approval.commandId, credentialRef: approval.credentialRef,
  });
  const result = spawnSync(invocation.binary, invocation.args, {
    shell: false, windowsHide: true, timeout: approval.timeoutMs, maxBuffer: approval.maxOutputBytes,
    encoding: null, input: Buffer.alloc(0),
    env: { PATH: process.platform === "win32" ? "C:\\Windows\\System32\\OpenSSH;C:\\Windows\\System32" : "/usr/bin:/bin", LANG: "C", LC_ALL: "C", HOME: process.platform === "win32" ? "C:\\Windows\\Temp" : "/nonexistent" },
  });
  if (result.error) fail(result.error.code === "ETIMEDOUT" ? "SSH forced-command timed out after approval consumption; no replay" : "SSH forced-command transport failed after approval consumption; no replay");
  const stdout = Buffer.isBuffer(result.stdout) ? result.stdout : Buffer.alloc(0);
  const stderr = Buffer.isBuffer(result.stderr) ? result.stderr : Buffer.alloc(0);
  if (stdout.length + stderr.length > approval.maxOutputBytes) fail("SSH forced-command output exceeded its owner-approved ceiling; no replay");
  const stdoutText = decodeBounded(stdout, "stdout"), stderrText = decodeBounded(stderr, "stderr");
  if (result.status !== 0 || result.signal !== null || stderrText !== "") fail("SSH forced-command did not return a clean success after approval consumption; no replay");
  const hostname = stdoutText.replace(/\r?\n$/u, "");
  if (!HOSTNAME_RE.test(hostname) || hostname !== invocation.destination.expectedHostname) fail("SSH forced-command returned an unexpected hostname after approval consumption; no replay");
  const outputBytes = Buffer.byteLength(hostname, "utf8"), outputSha256 = sha(hostname);
  return Object.freeze({
    status: "succeeded", destinationAlias: approval.destinationAlias, commandId: "hostname",
    hostname, outputBytes, outputSha256, approvalSha256: verified.approvalSha256,
    signatureSha256: verified.signatureSha256, approvalConsumptionSha256: consumed.recordSha256,
    authority: Object.freeze({ grantsReplay: false, grantsGenericShell: false, grantsCommandSelection: false, grantsDestinationSelection: false, grantsCredentialDisclosure: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false }),
    boundary: RESULT_BOUNDARY,
  });
}

export const capabilitySshRuntimeV2ResultBoundary = RESULT_BOUNDARY;
