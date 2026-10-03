import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, realpath, rename, rm } from "node:fs/promises";
import { basename, dirname, isAbsolute, parse, resolve } from "node:path";

import { canonical, validateWorkCodexCredentialCustody, validateWorkCodexPolicy } from "../../scripts/lib/work-contract.mjs";

const SUFFIX = /^[a-f0-9]{12}$/u;
const PRIVATE_HANDLES = new WeakMap();
const RECEIPT_SCHEMA = "https://osmantic.com/pixel/schemas/work-codex-credential-custody-v1.schema.json";
const RECEIPT_BOUNDARY = "Content-free broker-private credential custody evidence only. It exposes no path, owner, account, token, key, cache contents, content hash, provider response, prompt, capsule, tool authority, or external-effect authority.";

export class WorkCodexCredentialCustodyError extends Error {}
function fail(message) { throw new WorkCodexCredentialCustodyError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function iso(value) { return value.toISOString().replace(/\.000Z$/u, "Z"); }
function schema(label, errors) { if (errors.length) fail(`${label} failed validation: ${errors.join("; ")}`); }

async function privateDirectory(path, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real private directory`);
  if (await realpath(path).catch(() => null) !== resolve(path)) fail(`${label} cannot traverse a link or junction`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-only`);
  return info;
}

async function readCredential(path, maximumBytes, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size < 1 || info.size > maximumBytes) fail(`${label} must be a bounded single-link regular file`);
  if (await realpath(path).catch(() => null) !== resolve(path)) fail(`${label} cannot traverse a link or junction`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-only`);
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail(`${label} could not be opened safely`));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== info.dev || opened.ino !== info.ino || opened.size !== info.size) fail(`${label} changed during validation`);
    const bytes = await handle.readFile();
    if (bytes.includes(0)) fail(`${label} contains a NUL byte`);
    let text;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail(`${label} is not strict UTF-8`); }
    return { text, binding: Object.freeze({ dev: opened.dev, ino: opened.ino, size: opened.size, mtimeMs: opened.mtimeMs, ctimeMs: opened.ctimeMs }) };
  } finally { await handle.close(); }
}

function validateChatgpt(text) {
  let value;
  try { value = JSON.parse(text); } catch { fail("ChatGPT Codex auth cache is not valid JSON"); }
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length === 0 || value.auth_mode !== "chatgpt") fail("ChatGPT Codex auth cache has the wrong authentication mode");
}

function validateApiKey(text) {
  const value = text.endsWith("\n") ? text.slice(0, -1) : text;
  if (value.length < 20 || Buffer.byteLength(value, "utf8") > 8191 || /\s|[\u0000-\u001f\u007f]/u.test(value) || text !== value && text !== `${value}\n`) fail("Codex API credential has an invalid bounded one-line shape");
  return value;
}

function handleFor({ policy, path, binding }) {
  const handle = Object.freeze(Object.create(null));
  PRIVATE_HANDLES.set(handle, Object.freeze({
    policySha256: sha(policy), authMode: policy.provider.authMode,
    credentialId: policy.credentialCustody.credentialId, path, binding,
  }));
  return handle;
}

function assertHandle(policy, handle) {
  const state = handle && PRIVATE_HANDLES.get(handle);
  if (!state || state.policySha256 !== sha(policy) || state.authMode !== policy.provider.authMode || state.credentialId !== policy.credentialCustody.credentialId) fail("Codex credential custody handle differs from private policy");
  return state;
}

function receiptFor(policy, now, suffix) {
  const receipt = {
    $schema: RECEIPT_SCHEMA, schemaVersion: 1, receiptId: `workcodexcustody-${String(now.getTime()).padStart(13, "0")}-${suffix}`, inspectedAt: iso(now),
    policySha256: sha(policy), authMode: policy.provider.authMode, custodyMode: policy.credentialCustody.mode, status: "available",
    mutableRefresh: policy.credentialCustody.mutableRefresh, credentialsProjected: false, credentialPathProjected: false, credentialContentsHashed: false,
    boundary: RECEIPT_BOUNDARY,
  };
  schema("Codex credential custody receipt", validateWorkCodexCredentialCustody(receipt)); return Object.freeze(receipt);
}

export async function inspectWorkCodexCredentialCustody({ policy, credentialPath, now = new Date(), suffix }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy));
  if (!(now instanceof Date) || Number.isNaN(now.getTime()) || !SUFFIX.test(suffix ?? "") || typeof credentialPath !== "string" || !isAbsolute(credentialPath)) fail("Codex credential custody inspection inputs are invalid");
  const path = resolve(credentialPath);
  if (path === parse(path).root) fail("Codex credential custody path cannot be a filesystem root");
  const expectedName = policy.provider.authMode === "chatgpt" ? "auth.json" : "provider-key";
  if (basename(path) !== expectedName) fail("Codex credential custody filename differs from private policy");
  await privateDirectory(dirname(path), "Codex credential custody directory");
  const observed = await readCredential(path, policy.credentialCustody.maxBytes, "Codex credential");
  if (policy.provider.authMode === "chatgpt") validateChatgpt(observed.text); else validateApiKey(observed.text);
  return Object.freeze({ receipt: receiptFor(policy, now, suffix), handle: handleFor({ policy, path, binding: observed.binding }) });
}

export async function revalidateWorkCodexCredentialCustody({ policy, handle, now = new Date(), suffix }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy)); const state = assertHandle(policy, handle);
  if (!(now instanceof Date) || Number.isNaN(now.getTime()) || !SUFFIX.test(suffix ?? "")) fail("Codex credential custody revalidation inputs are invalid");
  await privateDirectory(dirname(state.path), "Codex credential custody directory");
  const observed = await readCredential(state.path, policy.credentialCustody.maxBytes, "Codex credential");
  if (policy.provider.authMode === "chatgpt") validateChatgpt(observed.text); else {
    validateApiKey(observed.text);
    if (canonical(observed.binding) !== canonical(state.binding)) fail("Codex API credential changed after custody inspection");
  }
  return Object.freeze({ receipt: receiptFor(policy, now, suffix), handle: handleFor({ policy, path: state.path, binding: observed.binding }) });
}

export async function readWorkCodexApiCredential({ policy, handle }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy)); const state = assertHandle(policy, handle);
  if (policy.provider.authMode !== "api-key") fail("ChatGPT credential custody cannot be read as an API key");
  const observed = await readCredential(state.path, policy.credentialCustody.maxBytes, "Codex API credential");
  if (canonical(observed.binding) !== canonical(state.binding)) fail("Codex API credential changed after custody inspection");
  return validateApiKey(observed.text);
}

export async function readWorkCodexChatgptAuthCache({ policy, handle }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy)); const state = assertHandle(policy, handle);
  if (policy.provider.authMode !== "chatgpt") fail("API-key custody has no ChatGPT auth cache");
  const observed = await readCredential(state.path, policy.credentialCustody.maxBytes, "ChatGPT Codex auth cache");
  validateChatgpt(observed.text); return observed.text;
}

export async function replaceWorkCodexChatgptAuthCache({ policy, handle, replacementPath, now = new Date(), suffix }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy)); const state = assertHandle(policy, handle);
  if (policy.provider.authMode !== "chatgpt" || typeof replacementPath !== "string" || !isAbsolute(replacementPath) || !(now instanceof Date) || Number.isNaN(now.getTime()) || !SUFFIX.test(suffix ?? "")) fail("ChatGPT credential refresh inputs are invalid");
  await privateDirectory(dirname(state.path), "Codex credential custody directory");
  const current = await readCredential(state.path, policy.credentialCustody.maxBytes, "ChatGPT Codex auth cache"); validateChatgpt(current.text);
  const replacement = await readCredential(resolve(replacementPath), policy.credentialCustody.maxBytes, "refreshed ChatGPT Codex auth cache"); validateChatgpt(replacement.text);
  const temporary = `${state.path}.pixel-refresh-${suffix}`;
  try {
    const output = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600).catch(() => fail("refreshed ChatGPT Codex auth cache could not be staged once"));
    try { await output.writeFile(replacement.text, "utf8"); await output.sync(); } finally { await output.close(); }
    await rename(temporary, state.path).catch(() => fail("refreshed ChatGPT Codex auth cache could not be installed atomically"));
  } finally { await rm(temporary, { force: true }).catch(() => {}); }
  const installed = await readCredential(state.path, policy.credentialCustody.maxBytes, "installed ChatGPT Codex auth cache"); validateChatgpt(installed.text);
  return Object.freeze({ receipt: receiptFor(policy, now, suffix), handle: handleFor({ policy, path: state.path, binding: installed.binding }) });
}

export function getWorkCodexChatgptHome({ policy, handle }) {
  schema("Codex work policy", validateWorkCodexPolicy(policy)); const state = assertHandle(policy, handle);
  if (policy.provider.authMode !== "chatgpt") fail("API-key custody has no persistent ChatGPT auth home");
  return dirname(state.path);
}
