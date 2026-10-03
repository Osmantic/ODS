import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, realpath } from "node:fs/promises";
import { basename, dirname, isAbsolute, parse, resolve } from "node:path";

import { canonical, validateWorkProviderCustodyReceipt } from "../../scripts/lib/work-contract.mjs";
import { bindWorkProviderPrivatePolicy } from "./private-policy.mjs";

const HANDLES = new WeakMap();
const SUFFIX = /^[a-f0-9]{12}$/u;
const RECEIPT_BOUNDARY = "Content-free owner-private provider credential custody evidence. It exposes no path, owner, account, key, token, credential bytes or hash, prompt, response, reasoning, tool arguments, external-effect, deployment, or security-testing authority.";

export class WorkProviderCredentialCustodyError extends Error {}
function fail(message) { throw new WorkProviderCredentialCustodyError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function binding(info) { return Object.freeze({ dev: info.dev, ino: info.ino, size: info.size, mtimeMs: info.mtimeMs, ctimeMs: info.ctimeMs }); }

async function privateDirectory(path) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || await realpath(path).catch(() => null) !== resolve(path)) fail("provider credential directory must be real and private");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("provider credential directory must be owner-only");
}

async function readCredential(path, maximumBytes) {
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch(() => fail("provider credential could not be opened safely"));
  try {
    const opened = await handle.stat();
    const pathInfo = await lstat(path).catch(() => null);
    if (!opened.isFile() || !pathInfo?.isFile() || pathInfo.isSymbolicLink() || opened.dev !== pathInfo.dev || opened.ino !== pathInfo.ino
      || opened.nlink !== 1 || opened.size < 20 || opened.size > maximumBytes) fail("provider credential must be one bounded real regular file");
    if (process.platform !== "win32" && (opened.uid !== process.geteuid() || (opened.mode & 0o077) !== 0)) fail("provider credential must be owner-only");
    const bytes = Buffer.allocUnsafe(maximumBytes + 1);
    let total = 0;
    while (total <= maximumBytes) {
      const { bytesRead } = await handle.read(bytes, total, maximumBytes + 1 - total, null);
      if (bytesRead === 0) break;
      total += bytesRead;
    }
    const after = await handle.stat();
    if (total !== opened.size || canonical(binding(after)) !== canonical(binding(opened))) {
      bytes.fill(0);
      fail("provider credential changed during validation");
    }
    const observed = bytes.subarray(0, total);
    const trailingNewline = observed.at(-1) === 0x0a, value = Buffer.from(trailingNewline ? observed.subarray(0, -1) : observed);
    const invalid = value.length < 20 || value.length > maximumBytes || value.some((byte) => byte < 0x21 || byte > 0x7e) || value.includes(0x0a) || observed.includes(0x0d);
    bytes.fill(0);
    if (invalid) { value.fill(0); fail("provider credential has an invalid bounded one-line shape"); }
    return { value, binding: binding(after) };
  } finally { await handle.close(); }
}

function stateFor(resolvedProvider, policy, path, observed) {
  const handle = Object.freeze(Object.create(null));
  HANDLES.set(handle, Object.freeze({
    providerId: resolvedProvider.profile.id, profileSha256: resolvedProvider.profileSha256,
    policyId: policy.policyId, policySha256: sha(policy), credentialId: policy.credentialCustody.credentialId,
    path, binding: observed.binding,
  }));
  return handle;
}

function assertState(resolvedProvider, policy, handle) {
  const state = handle && HANDLES.get(handle);
  if (!state || state.providerId !== resolvedProvider.profile.id || state.profileSha256 !== resolvedProvider.profileSha256
    || state.policyId !== policy.policyId || state.policySha256 !== sha(policy) || state.credentialId !== policy.credentialCustody.credentialId) fail("provider credential custody handle differs from provider policy");
  return state;
}

function takeCredentialAfterBindingCheck(observed, expectedBinding) {
  if (canonical(observed.binding) !== canonical(expectedBinding)) {
    observed.value.fill(0);
    fail("provider credential changed after custody inspection");
  }
  return observed.value;
}

function custodyReceipt(resolvedProvider, policy, now, suffix) {
  const receipt = Object.freeze({
    $schema: "https://osmantic.com/pixel/schemas/work-provider-custody-receipt-v1.schema.json", schemaVersion: 1,
    receiptId: `workprovidercustody-${String(now.getTime()).padStart(13, "0")}-${suffix}`,
    inspectedAt: now.toISOString().replace(/\.000Z$/u, "Z"), policyId: policy.policyId, policySha256: sha(policy),
    profileSha256: resolvedProvider.profileSha256, providerId: resolvedProvider.profile.id,
    credentialId: policy.credentialCustody.credentialId, status: "available", credentialsProjected: false,
    credentialPathProjected: false, credentialContentsHashed: false, boundary: RECEIPT_BOUNDARY,
  });
  const errors = validateWorkProviderCustodyReceipt(receipt);
  if (errors.length) fail(`provider credential custody receipt failed validation: ${errors.join("; ")}`);
  return receipt;
}

export async function inspectWorkProviderCredentialCustody({ resolvedProvider, policy: rawPolicy, credentialPath, now = new Date(), suffix }) {
  const policy = bindWorkProviderPrivatePolicy(resolvedProvider, rawPolicy);
  if (!(now instanceof Date) || Number.isNaN(now.getTime()) || !SUFFIX.test(suffix ?? "") || typeof credentialPath !== "string" || !isAbsolute(credentialPath)) fail("provider credential custody inputs are invalid");
  const path = resolve(credentialPath);
  if (path === parse(path).root || basename(path) !== policy.credentialCustody.fileName) fail("provider credential path is invalid");
  await privateDirectory(dirname(path));
  const observed = await readCredential(path, policy.credentialCustody.maxBytes);
  try { return Object.freeze({ receipt: custodyReceipt(resolvedProvider, policy, now, suffix), handle: stateFor(resolvedProvider, policy, path, observed) }); }
  finally { observed.value.fill(0); }
}

export async function readWorkProviderCredential({ resolvedProvider, policy: rawPolicy, handle }) {
  const policy = bindWorkProviderPrivatePolicy(resolvedProvider, rawPolicy, { requireEnabled: true });
  const state = assertState(resolvedProvider, policy, handle);
  await privateDirectory(dirname(state.path));
  const observed = await readCredential(state.path, policy.credentialCustody.maxBytes);
  return takeCredentialAfterBindingCheck(observed, state.binding);
}

export const workProviderCredentialCustodyInternals = Object.freeze({ takeCredentialAfterBindingCheck });
