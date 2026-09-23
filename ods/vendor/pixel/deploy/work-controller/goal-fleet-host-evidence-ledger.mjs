import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, open, readdir, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { validateGoalFleetHostEvidence } from "./goal-fleet.mjs";

const RECORD_RE = /^[0-9]{7}\.json$/u;
const TEMPORARY_RE = /^\.host-evidence-[a-f0-9]{16}$/u;
const MAX_RECORD_BYTES = 512 * 1024;
const MAX_RECORDS = 4096;
const SHA_RE = /^[a-f0-9]{64}$/u;

export class GoalFleetHostEvidenceLedgerError extends Error {}

function fail(message) { throw new GoalFleetHostEvidenceLedgerError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

export function goalFleetHostEvidenceSha256(value) { return sha(value); }

export function goalFleetHostEvidenceRecordName(sequence) {
  if (!Number.isSafeInteger(sequence) || sequence < 0 || sequence > 1000000) fail("fleet host evidence sequence is invalid");
  return `${String(sequence).padStart(7, "0")}.json`;
}

async function checkedDirectory(path, expectedOwnerUid) {
  if (resolve(path) !== path) fail("fleet host evidence ledger path is not absolute and normalized");
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail("fleet host evidence ledger is not owner-private");
}

async function syncDirectory(path) {
  if (process.platform === "win32") return;
  const handle = await open(path, constants.O_RDONLY);
  try { await handle.sync(); } finally { await handle.close(); }
}

export async function recoverInterruptedGoalFleetHostEvidencePublication({ directory, expectedOwnerUid = process.geteuid?.() ?? 0, now = new Date(), minimumAgeMs = 300000 } = {}) {
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0 || !(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || !Number.isSafeInteger(minimumAgeMs) || minimumAgeMs < 0) fail("fleet host evidence publication recovery input is invalid");
  await checkedDirectory(directory, expectedOwnerUid);
  const names = await readdir(directory);
  const records = new Map();
  for (const name of names.filter((name) => RECORD_RE.test(name))) {
    const info = await lstat(join(directory, name));
    records.set(`${info.dev}:${info.ino}`, { name, info });
  }
  let removed = 0;
  for (const name of names.filter((candidate) => TEMPORARY_RE.test(candidate)).sort()) {
    const path = join(directory, name);
    const info = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (!info) continue;
    if (!info.isFile() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0) || ![1, 2].includes(info.nlink)) fail("interrupted fleet host evidence publication is unsafe to recover");
    if (minimumAgeMs > 0 && (now.getTime() - info.mtimeMs < minimumAgeMs || Date.now() - info.ctimeMs < minimumAgeMs)) fail("interrupted fleet host evidence publication recovery delay has not elapsed");
    if (info.nlink === 2) {
      const record = records.get(`${info.dev}:${info.ino}`);
      if (!record || record.info.nlink !== 2) fail("interrupted fleet host evidence publication has an ambiguous linked record");
    }
    const didRemove = await unlink(path).then(() => true, (error) => error?.code === "ENOENT" ? false : Promise.reject(error));
    if (didRemove) removed += 1;
  }
  if (removed) await syncDirectory(directory);
  return Object.freeze({ action: removed ? "recovered-interrupted-publication" : "no-interrupted-publication", removed });
}

async function readEvidence(path, expectedOwnerUid) {
  const { bytes, details } = await readBoundedRegularFile(path, MAX_RECORD_BYTES, "fleet host evidence record");
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail("fleet host evidence record is not owner-private and single-link");
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail("fleet host evidence record is not strict UTF-8");
  try { return JSON.parse(text); } catch { fail("fleet host evidence record is not JSON"); }
}

export async function recoverGoalFleetHostEvidenceLedger({ directory, genesisSha256, fleet, probe, expectedOwnerUid = process.geteuid?.() ?? 0, now = new Date(), requireCurrent = true } = {}) {
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0 || !SHA_RE.test(genesisSha256 ?? "")) fail("fleet host evidence ledger binding is invalid");
  await checkedDirectory(directory, expectedOwnerUid);
  const names = (await readdir(directory)).sort();
  const recordNames = names.filter((name) => RECORD_RE.test(name));
  const temporaryNames = names.filter((name) => TEMPORARY_RE.test(name));
  if (recordNames.length + temporaryNames.length !== names.length || recordNames.length < 1 || recordNames.length > MAX_RECORDS) fail("fleet host evidence ledger file set is invalid");
  if (temporaryNames.length) {
    let publicationStillPresent = false;
    for (const name of temporaryNames) {
      const info = await lstat(join(directory, name)).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
      if (!info) continue;
      publicationStillPresent = true;
      if (!info.isFile() || info.isSymbolicLink() || ![1, 2].includes(info.nlink) || info.size < 1 || info.size > MAX_RECORD_BYTES || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail("fleet host evidence publication in progress is unsafe");
    }
    if (publicationStillPresent) fail("fleet host evidence publication is in progress");
  }
  const records = [];
  let previousSha256 = null, previousObservedAt = -1, fingerprint = null;
  for (let sequence = 0; sequence < recordNames.length; sequence += 1) {
    if (recordNames[sequence] !== goalFleetHostEvidenceRecordName(sequence)) fail("fleet host evidence ledger sequence is incomplete");
    const evidence = await readEvidence(join(directory, recordNames[sequence]), expectedOwnerUid);
    const observedAt = Date.parse(evidence.observedAt);
    validateGoalFleetHostEvidence({ fleet, probe, evidence, now: new Date(observedAt) });
    if (evidence.sequence !== sequence || evidence.previousEvidenceSha256 !== previousSha256) fail("fleet host evidence chain position is invalid");
    if (sequence > 0 && observedAt <= previousObservedAt) fail("fleet host evidence observation time did not advance");
    if (fingerprint !== null && evidence.hostFingerprintSha256 !== fingerprint) fail("fleet host identity changed within one evidence ledger");
    const evidenceSha256 = sha(evidence);
    if (sequence === 0 && evidenceSha256 !== genesisSha256) fail("fleet host evidence genesis differs from its immutable controller binding");
    records.push(Object.freeze({ evidence, evidenceSha256, path: join(directory, recordNames[sequence]) }));
    previousSha256 = evidenceSha256; previousObservedAt = observedAt; fingerprint = evidence.hostFingerprintSha256;
  }
  const head = records.at(-1);
  if (requireCurrent) validateGoalFleetHostEvidence({ fleet, probe, evidence: head.evidence, now });
  return Object.freeze({ records: Object.freeze(records), head: head.evidence, headSha256: head.evidenceSha256, headPath: head.path });
}

export async function appendGoalFleetHostEvidence({ directory, evidence, expectedOwnerUid = process.geteuid?.() ?? 0 } = {}) {
  await checkedDirectory(directory, expectedOwnerUid);
  const publicationTime = new Date(evidence?.observedAt);
  if (!Number.isSafeInteger(publicationTime.getTime())) fail("fleet host evidence publication time is invalid");
  const outputPath = join(directory, goalFleetHostEvidenceRecordName(evidence?.sequence));
  const serialized = `${JSON.stringify(evidence, null, 2)}\n`;
  const temporary = join(directory, `.host-evidence-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try {
    await handle.writeFile(serialized, "utf8");
    await handle.utimes(publicationTime, publicationTime);
    await handle.sync();
  } finally { await handle.close(); }
  try {
    await link(temporary, outputPath); await unlink(temporary);
    await syncDirectory(directory);
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("fleet host evidence sequence already exists");
    throw error;
  }
  return Object.freeze({ path: outputPath, evidenceSha256: sha(evidence) });
}
