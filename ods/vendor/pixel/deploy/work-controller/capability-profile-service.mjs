import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, rm, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";

import {
  canonical, validateWorkCapabilityJobAuthorization, validateWorkCapabilityToolCatalog,
  validateWorkCapabilityToolRequest, validateWorkCapabilityToolResponse,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { createCapabilityToolCatalog } from "./capability-tool-catalog.mjs";
import {
  enqueueCapabilityToolRequest, initializeCapabilityToolQueue, processCapabilityToolQueue,
  readCapabilityToolResponse, statusCapabilityToolQueue,
} from "./capability-tool-queue.mjs";
import {
  initializeCapabilityToolQueueV2, processCapabilityToolQueueV2, statusCapabilityToolQueueV2,
} from "./capability-tool-queue-v2.mjs";

const REQUEST_FILE_RE = /^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u;
const CATALOG_FILE_RE = /^catalog-[a-f0-9]{64}\.json$/u;
const QUEUE_ID_RE = /^workcapqueuev2-[0-9]{13}-[a-f0-9]{12}$/u;
const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 18 * 1024 * 1024;
const authority = Object.freeze({ grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const boundary = "Private job-scoped bridge between one trusted OMP extension queue and Pixel's durable capability controller. Status and cleanup receipts are content-free; no request or result content leaves job custody.";
const markerBoundary = "Content-free exact-root marker for one disposable capability profile service. It grants no execution, retention, cleanup, or completion authority.";

export class CapabilityProfileServiceError extends Error {}

function fail(message, cause) { throw new CapabilityProfileServiceError(message, cause === undefined ? undefined : { cause }); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} is invalid: ${errors[0]}`); }
function overlap(left, right) { return left === right || left.startsWith(`${right}/`) || right.startsWith(`${left}/`); }

function v2Binding(v2Config) {
  if (v2Config === undefined) return null;
  if (v2Config === null || typeof v2Config !== "object" || Array.isArray(v2Config) || typeof v2Config.queueId !== "string" || !QUEUE_ID_RE.test(v2Config.queueId)) fail("capability v2 profile binding is invalid");
  return Object.freeze({ present: true, queueId: v2Config.queueId, configSha256: sha(v2Config) });
}

async function privateDirectory(path, label, create = false) {
  if (!isAbsolute(path) || resolve(path) !== path) fail(`${label} path is not absolute and canonical`);
  if (create) await mkdir(path, { mode: 0o700 });
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

async function privateJson(path, maximum, label, validator) {
  let observed;
  try { observed = await readBoundedRegularText(path, maximum, label); } catch (error) { fail(`${label} could not be opened safely`, error); }
  if (observed.details.nlink !== 1 || (process.platform !== "win32" && (observed.details.uid !== process.geteuid() || (observed.details.mode & 0o077) !== 0))) fail(`${label} is not private and single-link`);
  let value;
  try { value = JSON.parse(observed.text); } catch (error) { fail(`${label} is not JSON`, error); }
  schema(label, validator(value));
  return value;
}

async function writeAtomic(path, value, maximum) {
  const bytes = Buffer.from(`${JSON.stringify(value)}\n`, "utf8");
  if (bytes.length > maximum) fail("capability profile object exceeds its byte ceiling");
  const temporary = join(dirname(path), `.stage-${basename(path)}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
  try { await link(temporary, path); await unlink(temporary); await syncDirectory(dirname(path)); }
  catch (error) { await unlink(temporary).catch(() => {}); fail("capability profile publication failed closed", error); }
}

function paths(serviceRoot, catalog) {
  if (!isAbsolute(serviceRoot) || resolve(serviceRoot) !== serviceRoot) fail("capability profile service root is not absolute and canonical");
  const workerRoot = join(serviceRoot, "worker"), controllerRoot = join(serviceRoot, "controller");
  if (overlap(workerRoot, controllerRoot)) fail("capability worker and controller custody overlap");
  const catalogSha256 = sha(catalog), catalogName = `catalog-${catalogSha256}.json`;
  return Object.freeze({ serviceRoot, workerRoot, controllerRoot, v2QueueRoot: join(controllerRoot, "v2"), markerPath: join(serviceRoot, "profile.json"), requestDirectory: join(workerRoot, "requests"), responseDirectory: join(workerRoot, "responses"), catalogPath: join(workerRoot, catalogName), catalogName, catalogSha256 });
}

function marker(authorization, catalogSha256, v2Binding = null) {
  const base = { schemaVersion: 1, operation: "pixel-work-capability-profile-root", authorizationSha256: sha(authorization), catalogSha256, boundary: markerBoundary };
  return v2Binding === null ? base : { ...base, v2: v2Binding };
}

async function exactRoot(value, authorization, v2Binding = null) {
  await privateDirectory(value.serviceRoot, "capability profile service root");
  const names = (await readdir(value.serviceRoot)).sort();
  if (canonical(names) !== canonical(["controller", "profile.json", "worker"])) fail("capability profile service root contains unexpected state");
  const v2Present = await lstat(value.v2QueueRoot).then(() => true, () => false);
  if (v2Present !== Boolean(v2Binding)) fail("v2 custody presence differs from the exact profile marker");
  const expected = marker(authorization, value.catalogSha256, v2Binding);
  const observed = await privateJson(value.markerPath, 4096, "capability profile root marker", (entry) => canonical(entry) === canonical(expected) ? [] : ["$: marker differs from exact profile"]);
  if (canonical(observed) !== canonical(expected)) fail("capability profile root marker differs from exact custody");
}

function exactCatalog({ authorization, catalog, pack, expectedPackSha256 }) {
  schema("capability profile authorization", validateWorkCapabilityJobAuthorization(authorization));
  schema("capability profile catalog", validateWorkCapabilityToolCatalog(catalog));
  const expected = createCapabilityToolCatalog({ authorization, pack, expectedPackSha256, now: new Date(catalog.createdAt) });
  if (canonical(expected) !== canonical(catalog) || catalog.authorizationSha256 !== sha(authorization)) fail("capability profile catalog differs from its exact controller derivation");
}

async function exactWorkerLayout(value, catalog) {
  await privateDirectory(value.workerRoot, "capability worker root");
  const names = (await readdir(value.workerRoot)).sort();
  if (canonical(names) !== canonical([value.catalogName, "requests", "responses"].sort())) fail("capability worker root does not have the exact split-queue shape");
  await privateDirectory(value.requestDirectory, "capability worker request directory");
  await privateDirectory(value.responseDirectory, "capability worker response directory");
  const observed = await privateJson(value.catalogPath, MAX_REQUEST_BYTES, "capability worker catalog", validateWorkCapabilityToolCatalog);
  if (sha(observed) !== value.catalogSha256 || canonical(observed) !== canonical(catalog)) fail("capability worker catalog differs from its content-bound name");
}

export async function initializeCapabilityProfileService({ serviceRoot, authorization, catalog, pack, expectedPackSha256, v2Config }) {
  exactCatalog({ authorization, catalog, pack, expectedPackSha256 });
  const value = paths(serviceRoot, catalog);
  const binding = v2Binding(v2Config);
  const markerExists = await lstat(value.markerPath).then(() => true, () => false);
  const v2QueueRootExists = await lstat(value.v2QueueRoot).then(() => true, () => false);
  if (markerExists) {
    if (v2QueueRootExists !== Boolean(binding)) fail("v2 custody presence differs from the exact profile marker");
  } else if (v2QueueRootExists && binding === null) {
    fail("v2 custody presence differs from the exact profile marker");
  }
  await privateDirectory(dirname(serviceRoot), "capability profile parent");
  const exists = await lstat(serviceRoot).then(() => true, () => false);
  if (!exists) await mkdir(serviceRoot, { mode: 0o700 });
  await privateDirectory(serviceRoot, "capability profile service root");
  const existingNames = await readdir(serviceRoot);
  if (existingNames.some((name) => !["controller", "profile.json", "worker"].includes(name))) fail("capability profile service root contains unexpected state");
  if (!(await lstat(value.workerRoot).then(() => true, () => false))) await mkdir(value.workerRoot, { mode: 0o700 });
  await privateDirectory(value.workerRoot, "capability worker root");
  for (const path of [value.requestDirectory, value.responseDirectory]) if (!(await lstat(path).then(() => true, () => false))) await mkdir(path, { mode: 0o700 });
  if (!(await lstat(value.catalogPath).then(() => true, () => false))) await writeAtomic(value.catalogPath, catalog, MAX_REQUEST_BYTES);
  await exactWorkerLayout(value, catalog);
  await initializeCapabilityToolQueue({ queueRoot: value.controllerRoot, authorization });
  if (binding !== null) await initializeCapabilityToolQueueV2({ queueRoot: value.v2QueueRoot, config: v2Config });
  if (!(await lstat(value.markerPath).then(() => true, () => false))) await writeAtomic(value.markerPath, marker(authorization, value.catalogSha256, binding), 4096);
  await exactRoot(value, authorization, binding);
  await syncDirectory(serviceRoot);
  const receipt = { schemaVersion: 1, operation: "pixel-work-capability-profile-initialize", status: "initialized-disabled", authorizationSha256: sha(authorization), catalogSha256: value.catalogSha256, tools: catalog.tools.length, enabled: false, authority: { ...authority }, boundary };
  if (binding !== null) receipt.v2 = binding;
  return Object.freeze(receipt);
}

async function publishResponse(path, response) {
  const exists = await lstat(path).then(() => true, () => false);
  if (exists) {
    const current = await privateJson(path, MAX_RESPONSE_BYTES, "capability worker response", validateWorkCapabilityToolResponse);
    if (canonical(current) !== canonical(response)) fail("capability worker response differs from the durable settlement");
    return;
  }
  await writeAtomic(path, response, MAX_RESPONSE_BYTES);
}

function delay(milliseconds) { return new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds)); }

export async function serveCapabilityProfileQueue({ serviceRoot, authorization, catalog, pack, expectedPackSha256, requestContext, runtime, signal, pollMilliseconds = 25, dependencies = {}, v2Config }) {
  if (!signal || typeof signal.aborted !== "boolean" || !Number.isSafeInteger(pollMilliseconds) || pollMilliseconds < 1 || pollMilliseconds > 1000) fail("capability profile service controls are invalid");
  if (!requestContext || typeof requestContext !== "object" || Array.isArray(requestContext) || !runtime || typeof runtime !== "object" || Array.isArray(runtime) || !dependencies || typeof dependencies !== "object" || Array.isArray(dependencies)) fail("capability profile controller context is invalid");
  const binding = v2Binding(v2Config);
  await initializeCapabilityProfileService({ serviceRoot, authorization, catalog, pack, expectedPackSha256, v2Config });
  const value = paths(serviceRoot, catalog);
  await exactRoot(value, authorization, binding);
  let iterations = 0;
  while (true) {
    let progressed = false;
    await exactWorkerLayout(value, catalog);
    const names = (await readdir(value.requestDirectory)).sort();
    if (names.length > 1 || names.some((name) => !REQUEST_FILE_RE.test(name))) fail("capability worker request queue is not single-flight and exact");
    if (names.length) {
      const name = names[0], requestId = name.slice(0, -5), workerResponsePath = join(value.responseDirectory, name);
      if (!(await lstat(workerResponsePath).then(() => true, () => false))) {
        const request = await privateJson(join(value.requestDirectory, name), MAX_REQUEST_BYTES, "capability worker request", validateWorkCapabilityToolRequest);
        if (request.requestId !== requestId || request.authorizationSha256 !== sha(authorization) || request.jobId !== authorization.jobId || request.checkpointSha256 !== authorization.checkpointSha256) fail("capability worker request differs from its exact service");
        const durableResponsePath = join(value.controllerRoot, "responses", name);
        let response;
        if (await lstat(durableResponsePath).then(() => true, () => false)) response = await readCapabilityToolResponse({ queueRoot: value.controllerRoot, authorization, requestId });
        else {
          const status = await statusCapabilityToolQueue({ queueRoot: value.controllerRoot, authorization });
          if (!status.active) await enqueueCapabilityToolRequest({ queueRoot: value.controllerRoot, authorization, request });
          const settled = await processCapabilityToolQueue({ queueRoot: value.controllerRoot, authorization, requestContext, runtime, recoverActive: status.active, dependencies });
          response = settled.response;
          if (!response) fail("capability durable controller did not settle the worker request");
        }
        if (response.requestId !== requestId) fail("capability durable settlement belongs to a different worker request");
        await publishResponse(workerResponsePath, response);
        iterations += 1;
        if (iterations > authorization.maxSessions) fail("capability profile service exceeded its session envelope");
        progressed = true;
      }
    }
    if (binding !== null) {
      const before = await statusCapabilityToolQueueV2({ queueRoot: value.v2QueueRoot, config: v2Config });
      if (before.settled > v2Config.authorization.maxSessions) fail("capability v2 profile service exceeded its session envelope");
      if (before.queued > 0 || before.active) {
        await processCapabilityToolQueueV2({ queueRoot: value.v2QueueRoot, config: v2Config });
        const after = await statusCapabilityToolQueueV2({ queueRoot: value.v2QueueRoot, config: v2Config });
        if (after.settled > v2Config.authorization.maxSessions) fail("capability v2 profile service exceeded its session envelope");
        if (canonical(after) !== canonical(before)) progressed = true;
      }
    }
    if (signal.aborted) break;
    if (!progressed) await delay(pollMilliseconds);
  }
  const status = await statusCapabilityProfileService({ serviceRoot, authorization, catalog, pack, expectedPackSha256, v2Config });
  const receipt = { schemaVersion: 1, operation: "pixel-work-capability-profile-service", status: "stopped-disabled", authorizationSha256: sha(authorization), catalogSha256: value.catalogSha256, settled: status.settled, events: status.events, terminal: status.terminal, enabled: false, authority: { ...authority }, boundary };
  if (binding !== null) receipt.v2 = status.v2;
  return Object.freeze(receipt);
}

export async function statusCapabilityProfileService({ serviceRoot, authorization, catalog, pack, expectedPackSha256, v2Config }) {
  exactCatalog({ authorization, catalog, pack, expectedPackSha256 });
  const value = paths(serviceRoot, catalog);
  const binding = v2Binding(v2Config);
  await exactRoot(value, authorization, binding);
  await exactWorkerLayout(value, catalog);
  const controller = await statusCapabilityToolQueue({ queueRoot: value.controllerRoot, authorization });
  const requests = await readdir(value.requestDirectory), responses = await readdir(value.responseDirectory);
  if (requests.some((name) => !REQUEST_FILE_RE.test(name)) || responses.some((name) => !REQUEST_FILE_RE.test(name)) || requests.length > 1 || responses.length > authorization.maxSessions) fail("capability worker queue status is unsafe or exceeds authorization");
  const receipt = { schemaVersion: 1, operation: "pixel-work-capability-profile-status", status: controller.active ? "active-disabled" : controller.terminal ? "stopped-disabled" : requests.length ? "settled-awaiting-worker-disabled" : "idle-disabled", authorizationSha256: sha(authorization), catalogSha256: value.catalogSha256, workerRequests: requests.length, workerResponses: responses.length, queued: controller.queued, settled: controller.settled, events: controller.events, active: controller.active, terminal: controller.terminal, enabled: false, authority: { ...authority }, boundary };
  if (binding !== null) receipt.v2 = await statusCapabilityToolQueueV2({ queueRoot: value.v2QueueRoot, config: v2Config });
  return Object.freeze(receipt);
}

export async function cleanupCapabilityProfileService({ serviceRoot, authorization, catalog, pack, expectedPackSha256, v2Config }) {
  const status = await statusCapabilityProfileService({ serviceRoot, authorization, catalog, pack, expectedPackSha256, v2Config });
  const value = paths(serviceRoot, catalog);
  const binding = v2Binding(v2Config);
  if (binding !== null) fail("v2-bound capability profile cleanup requires a response acknowledgement contract");
  const requestNames = (await readdir(value.requestDirectory)).sort(), responseNames = (await readdir(value.responseDirectory)).sort();
  if (status.active || status.queued !== 0 || status.workerResponses !== status.settled || requestNames.some((name) => !responseNames.includes(name))) fail("capability profile cleanup would discard active or unsettled work");
  await exactRoot(value, authorization);
  await rm(serviceRoot, { recursive: true });
  if (await lstat(serviceRoot).then(() => true, () => false)) fail("capability profile service cleanup could not prove absence");
  await syncDirectory(dirname(serviceRoot));
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-profile-cleanup", status: "removed", authorizationSha256: sha(authorization), catalogSha256: value.catalogSha256, settled: status.settled, events: status.events, terminal: status.terminal, contentRemoved: true, enabled: false, authority: { ...authority }, boundary });
}

export const capabilityProfileServiceBoundary = boundary;
