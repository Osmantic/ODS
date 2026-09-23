import { randomBytes } from "node:crypto";
import { lstat, readdir, unlink } from "node:fs/promises";
import { basename, join, resolve } from "node:path";

import { processResearchToolQueueFile, ResearchToolQueueError } from "./tool-queue.mjs";

const REQUEST_NAME_RE = /^req-researchtool-[0-9]{13}-[a-f0-9]{32}\.json$/u;
const MAX_QUEUE_ENTRIES = 1024;

export class ResearchServiceError extends Error {}

function fail(message, cause) {
  throw new ResearchServiceError(message, cause === undefined ? undefined : { cause });
}

async function privateDirectory(path, label) {
  const absolute = resolve(path);
  const info = await lstat(absolute).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
  return absolute;
}

function delay(milliseconds, signal) {
  return new Promise((resolvePromise) => {
    if (signal?.aborted) { resolvePromise(); return; }
    const timer = setTimeout(resolvePromise, milliseconds);
    timer.unref?.();
    signal?.addEventListener("abort", () => { clearTimeout(timer); resolvePromise(); }, { once: true });
  });
}

async function discardInvalidRequest(path) {
  const info = await lstat(path).catch(() => null);
  if (!info) return;
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1) fail("untrusted research queue contains a non-regular request entry");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail("untrusted research queue request is not private");
  await unlink(path);
}

export async function serveResearchToolQueue({
  requestDirectory,
  responseDirectory,
  plan,
  lease,
  claim,
  stateRoot,
  courierQueueRoot,
  objectRoot,
  endpoint,
  signal,
  pollMilliseconds = 25,
  processor = processResearchToolQueueFile,
  now = () => new Date(),
  suffix = () => randomBytes(6).toString("hex"),
  pipelineOptions = {},
  onCompleted = () => {},
}) {
  if (!Number.isSafeInteger(pollMilliseconds) || pollMilliseconds < 1 || pollMilliseconds > 1000 || typeof processor !== "function" || typeof now !== "function" || typeof suffix !== "function" || typeof onCompleted !== "function") fail("research service configuration is invalid");
  const requests = await privateDirectory(requestDirectory, "research request queue");
  const responses = await privateDirectory(responseDirectory, "research response queue");
  if (requests === responses) fail("research request and response queues must be distinct");
  let completed = 0;
  let rejected = 0;
  let errors = 0;
  let invalid = 0;
  while (!signal?.aborted) {
    const current = now();
    if (!(current instanceof Date) || !Number.isSafeInteger(current.getTime())) fail("research service clock is invalid");
    if (current.getTime() >= Date.parse(lease.expiresAt)) fail("research service reached the immutable lease expiry");
    const names = await readdir(requests);
    if (names.length > MAX_QUEUE_ENTRIES) fail("research request queue exceeds its entry ceiling");
    const candidates = names.filter((name) => REQUEST_NAME_RE.test(name)).sort();
    for (const name of candidates) {
      if (signal?.aborted) break;
      const requestPath = join(requests, name);
      try {
        const result = await processor({
          requestPath,
          responseDirectory: responses,
          plan,
          lease,
          claim,
          stateRoot,
          courierQueueRoot,
          objectRoot,
          endpoint,
          now: current,
          suffix: suffix(),
          pipelineOptions,
        });
        if (result.response.status === "completed") {
          if (!result.batch) fail("completed research service result lacks its private batch evidence");
          await onCompleted(result);
          completed += 1;
        }
        else if (result.response.status === "rejected") rejected += 1;
        else errors += 1;
      } catch (error) {
        if (error instanceof ResearchToolQueueError && ["request-contract", "queue-filesystem"].includes(error.code)) {
          await discardInvalidRequest(requestPath);
          invalid += 1;
          continue;
        }
        throw error;
      }
    }
    if (!signal?.aborted) await delay(pollMilliseconds, signal);
  }
  return Object.freeze({
    completed,
    rejected,
    errors,
    invalid,
    contentStoredBeyondJob: false,
    credentialsExposed: false,
    directNetworkGrantedToWorker: false,
    externalWritesPerformed: false,
  });
}

export function validateResearchServiceQueueName(value) {
  return typeof value === "string" && basename(value) === value && REQUEST_NAME_RE.test(value);
}

export const researchServiceContract = Object.freeze({ maximumQueueEntries: MAX_QUEUE_ENTRIES, requestNamePattern: REQUEST_NAME_RE.source });
