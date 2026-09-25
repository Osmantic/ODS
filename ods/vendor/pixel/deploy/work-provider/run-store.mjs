import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, realpath, rename, unlink } from "node:fs/promises";
import { basename, isAbsolute, join, parse, resolve } from "node:path";

import { canonical, validateWorkProviderRunLedger } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";

const RUN_ID = /^workproviderrun-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA = /^[a-f0-9]{64}$/u;
const MAX_LEDGER_BYTES = 4 * 1024 * 1024;

export class WorkProviderRunStoreError extends Error {}
function fail(message) { throw new WorkProviderRunStoreError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

async function privateDirectory(path, { create = false } = {}) {
  if (typeof path !== "string" || !isAbsolute(path) || resolve(path) === parse(resolve(path)).root) fail("provider run store root is invalid");
  if (create) {
    try { await mkdir(path, { mode: 0o700 }); }
    catch (error) { if (error?.code !== "EEXIST") fail("provider run store root could not be initialized"); }
  }
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || await realpath(path).catch(() => null) !== resolve(path)) fail("provider run store root must be one real private directory");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("provider run store root must be owner-only");
  return resolve(path);
}

function ledgerPath(root, runId) {
  if (!RUN_ID.test(runId ?? "")) fail("provider run id is invalid");
  const path = join(root, `${runId}.json`);
  if (basename(path) !== `${runId}.json`) fail("provider run path escaped its store");
  return path;
}

async function readLedgerPath(path) {
  const { text, details } = await readBoundedRegularText(path, MAX_LEDGER_BYTES, "provider run ledger").catch(() => fail("provider run ledger could not be read safely"));
  if (!details.isFile() || details.nlink !== 1 || process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)) fail("provider run ledger must be owner-only and single-link");
  const value = parseStrictJson(text, "provider run ledger");
  const errors = validateWorkProviderRunLedger(value);
  if (errors.length) fail(`provider run ledger failed validation: ${errors.join("; ")}`);
  if (path !== ledgerPath(resolve(path, ".."), value.runId)) fail("provider run ledger filename differs from its identity");
  return Object.freeze({ ledger: Object.freeze(value), sha256: sha(value) });
}

async function writeExclusive(path, text) {
  const handle = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600).catch(() => fail("provider run store write was not exclusive"));
  try { await handle.writeFile(text, "utf8"); await handle.sync(); } finally { await handle.close(); }
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

export async function initializeWorkProviderRunStore({ root }) {
  return privateDirectory(resolve(root), { create: true });
}

export async function storeNewWorkProviderRunLedger({ root, ledger }) {
  const store = await privateDirectory(resolve(root));
  const errors = validateWorkProviderRunLedger(ledger);
  if (errors.length) fail(`provider run ledger failed validation: ${errors.join("; ")}`);
  const path = ledgerPath(store, ledger.runId); const text = canonical(ledger);
  if (Buffer.byteLength(text, "utf8") > MAX_LEDGER_BYTES) fail("provider run ledger exceeds its store byte ceiling");
  await writeExclusive(path, text); await syncDirectory(store);
  return Object.freeze({ runId: ledger.runId, sha256: sha(ledger) });
}

export async function readWorkProviderRunLedger({ root, runId }) {
  const store = await privateDirectory(resolve(root));
  return readLedgerPath(ledgerPath(store, runId));
}

export async function replaceWorkProviderRunLedger({ root, expectedSha256, ledger }) {
  const store = await privateDirectory(resolve(root));
  if (!SHA.test(expectedSha256 ?? "")) fail("provider run expected SHA-256 is invalid");
  const errors = validateWorkProviderRunLedger(ledger);
  if (errors.length) fail(`replacement provider run ledger failed validation: ${errors.join("; ")}`);
  const path = ledgerPath(store, ledger.runId), lockPath = `${path}.lock`, temporary = `${path}.tmp-${randomBytes(8).toString("hex")}`;
  let lock;
  try {
    lock = await open(lockPath, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600).catch(() => fail("provider run ledger is already being updated or requires lock recovery"));
    await lock.sync();
    const current = await readLedgerPath(path);
    if (current.sha256 !== expectedSha256) fail("provider run ledger changed before atomic replacement");
    for (const field of ["runId", "createdAt", "taskId", "providerId", "model", "profileSha256", "policySha256", "boundary"]) if (ledger[field] !== current.ledger[field]) fail(`replacement provider run ledger changed immutable field ${field}`);
    if (canonical(ledger.binding) !== canonical(current.ledger.binding)) fail("replacement provider run ledger changed immutable field binding");
    const text = canonical(ledger);
    if (Buffer.byteLength(text, "utf8") > MAX_LEDGER_BYTES) fail("replacement provider run ledger exceeds its byte ceiling");
    await writeExclusive(temporary, text);
    await rename(temporary, path).catch(() => fail("provider run ledger atomic replacement failed"));
    await syncDirectory(store);
    return Object.freeze({ runId: ledger.runId, previousSha256: expectedSha256, sha256: sha(ledger) });
  } finally {
    await unlink(temporary).catch(() => {});
    if (lock) { await lock.close().catch(() => {}); await unlink(lockPath).catch(() => {}); }
  }
}
