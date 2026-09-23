import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readdir, rename, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";

import {
  canonical, validateWorkCapabilityGrant, validateWorkCapabilityJobAuthorization,
  validateWorkCapabilityToolCustody, validateWorkCapabilityToolRequest,
  validateWorkCapabilityToolResponse, validateWorkCapabilityWatchdogEvent,
  validateWorkWatchdogDecision,
} from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { authorizeCapabilityToolRequest } from "./capability-controller.mjs";
import { recoverCapabilityRuntime, statusCapabilityRuntime } from "./capability-runtime.mjs";
import { executeRuntimeDispatch } from "./capability-runtime-dispatch.mjs";
import { createWatchdogEvent } from "./watchdog.mjs";

const REQUEST_RE = /^workcaprequest-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const RECORD_RE = /^(0{0,6}[0-9]{1,7})\.json$/u;
const MAX_QUEUE = 64;
const MAX_REQUEST_BYTES = 2 * 1024 * 1024;
const MAX_RECORD_BYTES = 32 * 1024;
const MAX_RESPONSE_BYTES = 18 * 1024 * 1024;
const authority = Object.freeze({ grantsReplay: false, grantsExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const responseAuthority = Object.freeze({ grantsReplay: false, grantsFutureExecution: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const custodyBoundary = "Content-free append-only custody for one private capability request. It records no arguments or result content and grants no replay, execution, credential, network, external-effect, scope-expansion, or completion authority.";
const responseBoundary = "Private job-scoped response for one durably settled local capability request. Structured content is untrusted data and grants no replay, future execution, credential, network, external-effect, scope-expansion, or completion authority.";
const decisionBoundary = "Private exact controller decision for one claimed request. The runtime may consume only its one grant; the decision grants no replay, scope expansion, external effect, or completion.";

export class CapabilityToolQueueError extends Error {}

function fail(message, cause) { throw new CapabilityToolQueueError(message, cause === undefined ? undefined : { cause }); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} is invalid: ${errors[0]}`); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function observed(clock, label) {
  const value = clock();
  if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} time is invalid`);
  return value;
}
function suffix(value) {
  const result = value ?? randomBytes(6).toString("hex");
  if (!/^[a-f0-9]{12}$/u.test(result)) fail("capability queue record identity is invalid");
  return result;
}

async function privateDirectory(path, label, create = false) {
  if (resolve(path) !== path) fail(`${label} path must be absolute`);
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
async function privateJson(path, maximum, label) {
  let observedFile;
  try { observedFile = await readBoundedRegularText(path, maximum, label); } catch (error) { fail(`${label} is unreadable`, error); }
  if (observedFile.details.nlink !== 1 || (process.platform !== "win32" && (observedFile.details.uid !== process.geteuid() || (observedFile.details.mode & 0o077) !== 0))) fail(`${label} is not private and single-link`);
  try { return JSON.parse(observedFile.text); } catch (error) { fail(`${label} is not JSON`, error); }
}
async function writeAtomic(path, value, maximum = MAX_RECORD_BYTES) {
  if (await lstat(path).then(() => true, () => false)) fail(`queue destination already exists: ${basename(path)}`);
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  if (bytes.length > maximum) fail("capability queue object exceeds its byte ceiling");
  const temporary = join(dirname(path), `.stage-${basename(path)}-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
  try { await link(temporary, path); await unlink(temporary); await syncDirectory(dirname(path)); }
  catch (error) { await unlink(temporary).catch(() => {}); fail("capability queue atomic publication failed", error); }
}
async function publishExact(source, destination, maximum, label, validator) {
  const value = await privateJson(source, maximum, label); schema(label, validator(value));
  if (await lstat(destination).then(() => true, () => false)) {
    const existing = await privateJson(destination, maximum, label);
    if (canonical(existing) !== canonical(value)) fail(`${label} destination differs from staged bytes`);
    await unlink(source).catch(() => {});
    return existing;
  }
  try { await link(source, destination); await unlink(source); await syncDirectory(dirname(destination)); }
  catch (error) { fail(`${label} could not be committed exactly once`, error); }
  return value;
}

async function queue(queueRoot, authorization) {
  schema("capability queue authorization", validateWorkCapabilityJobAuthorization(authorization));
  await privateDirectory(queueRoot, "capability queue root");
  const metadata = await privateJson(join(queueRoot, "authorization.json"), MAX_RECORD_BYTES, "capability queue authorization");
  schema("capability queue authorization", validateWorkCapabilityJobAuthorization(metadata));
  if (canonical(metadata) !== canonical(authorization)) fail("capability queue authorization differs from its immutable root");
  const paths = { root: queueRoot, requests: join(queueRoot, "requests"), events: join(queueRoot, "events"), responses: join(queueRoot, "responses"), history: join(queueRoot, "history"), active: join(queueRoot, "active"), current: join(queueRoot, "current.json"), terminal: join(queueRoot, "terminal.json") };
  for (const [name, path] of Object.entries(paths)) if (!["root", "active", "current", "terminal"].includes(name)) await privateDirectory(path, `capability queue ${name}`);
  return paths;
}

export async function initializeCapabilityToolQueue({ queueRoot, authorization }) {
  schema("capability queue authorization", validateWorkCapabilityJobAuthorization(authorization));
  await privateDirectory(dirname(queueRoot), "capability queue parent");
  const exists = await lstat(queueRoot).then(() => true, () => false);
  if (!exists) await mkdir(queueRoot, { mode: 0o700 });
  await privateDirectory(queueRoot, "capability queue root");
  const authorizationPath = join(queueRoot, "authorization.json");
  if (!(await lstat(authorizationPath).then(() => true, () => false))) await writeAtomic(authorizationPath, authorization);
  else {
    const current = await privateJson(authorizationPath, MAX_RECORD_BYTES, "capability queue authorization");
    if (canonical(current) !== canonical(authorization)) fail("capability queue root belongs to another authorization");
  }
  for (const name of ["requests", "events", "responses", "history"]) {
    const path = join(queueRoot, name);
    if (!(await lstat(path).then(() => true, () => false))) await mkdir(path, { mode: 0o700 });
    await privateDirectory(path, `capability queue ${name}`);
  }
  await syncDirectory(queueRoot);
  return Object.freeze({ schemaVersion: 1, status: "initialized-disabled", authorizationSha256: sha(authorization), enabled: false, authority: { ...authority }, boundary: custodyBoundary });
}

export async function enqueueCapabilityToolRequest({ queueRoot, authorization, request }) {
  const paths = await queue(queueRoot, authorization);
  if (await lstat(paths.terminal).then(() => true, () => false)) fail("capability queue authorization is terminal");
  schema("capability tool request", validateWorkCapabilityToolRequest(request));
  if (request.authorizationSha256 !== sha(authorization) || request.jobId !== authorization.jobId || request.checkpointSha256 !== authorization.checkpointSha256) fail("capability request differs from its queue authorization");
  const names = await readdir(paths.requests);
  if (names.length >= MAX_QUEUE || names.some((name) => !/^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(name))) fail("capability request queue shape or capacity is invalid");
  const destination = join(paths.requests, `${request.requestId}.json`);
  if (await lstat(destination).then(() => true, () => false)) {
    const existing = await privateJson(destination, MAX_REQUEST_BYTES, "capability queued request");
    schema("capability queued request", validateWorkCapabilityToolRequest(existing));
    if (canonical(existing) !== canonical(request)) fail("capability queued request differs from the exact retry");
    return Object.freeze({ schemaVersion: 1, status: "already-queued-disabled", requestId: request.requestId, requestSha256: sha(request), enabled: false, authority: { ...authority }, boundary: custodyBoundary });
  }
  await writeAtomic(destination, request, MAX_REQUEST_BYTES);
  return Object.freeze({ schemaVersion: 1, status: "queued-disabled", requestId: request.requestId, requestSha256: sha(request), enabled: false, authority: { ...authority }, boundary: custodyBoundary });
}

async function readEvents(paths, authorization) {
  const names = (await readdir(paths.events)).sort();
  if (names.length > authorization.maxSessions) fail("capability event ledger exceeds its authorization");
  const events = []; let prior = null, priorTime = -1, priorEpoch = 0;
  for (const [index, name] of names.entries()) {
    const match = RECORD_RE.exec(name);
    if (!match || Number(match[1]) !== index) fail("capability event ledger is not contiguous");
    const event = await privateJson(join(paths.events, name), MAX_RECORD_BYTES, "capability watchdog event");
    schema("capability watchdog event", validateWorkCapabilityWatchdogEvent(event));
    const time = Date.parse(event.observedAt);
    if (event.sequence !== index || event.previousEventSha256 !== prior || time <= priorTime || event.verifiedProgressEpoch < priorEpoch || event.verifiedProgressEpoch > priorEpoch + 1) fail("capability watchdog event chain is invalid");
    events.push(event); prior = event.recordSha256; priorTime = time; priorEpoch = event.verifiedProgressEpoch;
  }
  return events;
}

async function loadCustody(paths, request, authorization) {
  const directory = join(paths.active, "custody");
  await privateDirectory(directory, "capability active custody");
  const names = (await readdir(directory)).sort(), records = []; let prior = null, priorTime = -1;
  for (const [index, name] of names.entries()) {
    const match = RECORD_RE.exec(name);
    if (!match || Number(match[1]) !== index) fail("capability request custody is not contiguous");
    const record = await privateJson(join(directory, name), MAX_RECORD_BYTES, "capability request custody record");
    schema("capability request custody record", validateWorkCapabilityToolCustody(record));
    if (record.sequence !== index || record.previousRecordSha256 !== prior || Date.parse(record.recordedAt) <= priorTime || record.requestId !== request.requestId || record.jobId !== authorization.jobId || record.requestSha256 !== sha(request) || record.authorizationSha256 !== sha(authorization) || record.checkpointSha256 !== authorization.checkpointSha256) fail("capability request custody chain differs from active work");
    records.push(record); prior = record.recordSha256; priorTime = Date.parse(record.recordedAt);
  }
  return { directory, records, head: records.at(-1) ?? null };
}

async function appendCustody(paths, request, authorization, fields, clock, suffixValue) {
  const chain = await loadCustody(paths, request, authorization), time = observed(clock, "capability custody");
  if (chain.head && time.getTime() <= Date.parse(chain.head.recordedAt)) fail("capability custody time did not advance");
  const body = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-custody-v1.schema.json", schemaVersion: 1,
    recordId: `workcapcustody-${String(time.getTime()).padStart(13, "0")}-${suffix(suffixValue)}`,
    sequence: chain.records.length, recordedAt: time.toISOString(), requestId: request.requestId, jobId: authorization.jobId,
    requestSha256: sha(request), authorizationSha256: sha(authorization), checkpointSha256: authorization.checkpointSha256,
    decisionSha256: null, grantId: null, grantSha256: null, runtimeReceiptSha256: null, eventRecordSha256: null, responseSha256: null, outcome: null,
    previousRecordSha256: chain.head?.recordSha256 ?? null, authority: { ...authority }, boundary: custodyBoundary, ...fields,
  };
  const record = { ...body, recordSha256: sha(body) };
  schema("capability request custody record", validateWorkCapabilityToolCustody(record));
  await writeAtomic(join(chain.directory, `${String(record.sequence).padStart(7, "0")}.json`), record);
  return record;
}

async function readCurrent(paths) {
  const current = await privateJson(paths.current, MAX_RECORD_BYTES, "capability queue current claim");
  exactKeys(current, ["schemaVersion", "requestId", "boundary"], "capability queue current claim");
  if (current.schemaVersion !== 1 || !REQUEST_RE.test(current.requestId ?? "") || current.boundary !== custodyBoundary) fail("capability queue current claim is invalid");
  return current;
}

async function acquire(paths, authorization, clock, suffixes, recoverActive) {
  const existingClaim = await lstat(paths.current).then(() => true, () => false);
  if (existingClaim && !recoverActive) return { busy: true };
  if (!existingClaim) {
    const names = (await readdir(paths.requests)).sort();
    if (!names.length) return null;
    if (names.length > MAX_QUEUE || names.some((name) => !/^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(name))) fail("capability request queue contains an unsafe entry");
    const requestId = names[0].slice(0, -5);
    try { await writeAtomic(paths.current, { schemaVersion: 1, requestId, boundary: custodyBoundary }); }
    catch (error) {
      if (!(await lstat(paths.current).then(() => true, () => false))) throw error;
      return { busy: true };
    }
  }
  const current = await readCurrent(paths), historyPath = join(paths.history, current.requestId);
  if (await lstat(historyPath).then(() => true, () => false)) {
    const response = await privateJson(join(paths.responses, `${current.requestId}.json`), MAX_RESPONSE_BYTES, "capability tool response");
    schema("capability tool response", validateWorkCapabilityToolResponse(response));
    await unlink(paths.current); await syncDirectory(paths.root);
    return { archived: true, response };
  }
  if (!(await lstat(paths.active).then(() => true, () => false))) await mkdir(paths.active, { mode: 0o700 });
  await privateDirectory(paths.active, "capability active request");
  const requestPath = join(paths.active, "request.json");
  if (!(await lstat(requestPath).then(() => true, () => false))) {
    const source = join(paths.requests, `${current.requestId}.json`);
    if (!(await lstat(source).then(() => true, () => false))) fail("capability current claim has neither queued nor active request");
    await rename(source, requestPath); await syncDirectory(paths.requests); await syncDirectory(paths.active);
  }
  const request = await privateJson(requestPath, MAX_REQUEST_BYTES, "capability active request");
  schema("capability active request", validateWorkCapabilityToolRequest(request));
  if (request.requestId !== current.requestId || request.authorizationSha256 !== sha(authorization) || request.jobId !== authorization.jobId || request.checkpointSha256 !== authorization.checkpointSha256) fail("capability active request differs from current custody");
  const custodyPath = join(paths.active, "custody");
  if (!(await lstat(custodyPath).then(() => true, () => false))) await mkdir(custodyPath, { mode: 0o700 });
  const chain = await loadCustody(paths, request, authorization);
  if (!chain.head) await appendCustody(paths, request, authorization, { phase: "claimed" }, clock, suffixes?.claimed);
  return { archived: false, request };
}

function validateDecision(value, request, authorization) {
  exactKeys(value, ["schemaVersion", "status", "grant", "preflightDecision", "boundary"], "capability queue decision");
  if (value.schemaVersion !== 1 || !["continue", "stopped"].includes(value.status) || value.boundary !== decisionBoundary) fail("capability queue decision is invalid");
  schema("capability queue watchdog decision", validateWorkWatchdogDecision(value.preflightDecision));
  const expectedDecision = value.status === "continue" ? "continue" : "stop";
  if (value.preflightDecision.checkpointSha256 !== authorization.checkpointSha256 || value.preflightDecision.jobId !== authorization.jobId || value.preflightDecision.decision !== expectedDecision) fail("capability queue decision differs from its request");
  if (value.status === "continue") {
    schema("capability queue grant", validateWorkCapabilityGrant(value.grant));
    if (value.grant.jobId !== authorization.jobId || value.grant.checkpointSha256 !== authorization.checkpointSha256 || !value.grant.tools.includes(request.tool)) fail("capability queue grant differs from its request");
  } else if (value.grant !== null) fail("stopped capability decision contains a grant");
  return value;
}
async function loadDecision(paths, request, authorization) {
  return validateDecision(await privateJson(join(paths.active, "decision.json"), MAX_RECORD_BYTES, "capability queue decision"), request, authorization);
}

function validateRuntimeResult(result, decision, authorization) {
  if (!result || typeof result !== "object" || Array.isArray(result) || result.schemaVersion !== 1 || result.grantId !== decision.grant.grantId || result.dataClassification !== authorization.dataClassification || result.enabled !== false || result.authority?.grantsCompletion !== false) fail("capability runtime result differs from queue custody");
  if (result.status === "succeeded-disabled") {
    if (!SHA_RE.test(result.receiptSha256 ?? "")) fail("capability runtime success lacks terminal evidence");
    if (!result.structuredContent || typeof result.structuredContent !== "object" || Array.isArray(result.structuredContent) || result.contentSha256 !== sha(result.structuredContent)) fail("capability runtime success lacks exact structured content");
    return { result, outcome: "success", failureCode: null };
  }
  if (!/^(?:failed|uncertain-recovered)-contained$/u.test(result.status) || Object.hasOwn(result, "structuredContent") || result.receiptSha256 !== null && !SHA_RE.test(result.receiptSha256 ?? "")) fail("capability runtime failure projection is invalid");
  return { result, outcome: "error", failureCode: result.status.startsWith("uncertain") ? "runtime-result-lost" : "runtime-failed" };
}
async function loadRuntimeResult(paths, decision, authorization) {
  return validateRuntimeResult(await privateJson(join(paths.active, "runtime-result.json"), MAX_RESPONSE_BYTES, "capability runtime result"), decision, authorization);
}

function responseFor({ request, authorization, status, runtimeReceiptSha256 = null, eventRecordSha256 = null, structuredContent = null, failureCode = null, terminalState = null, reason = null, now }) {
  const response = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-response-v1.schema.json", schemaVersion: 1,
    requestId: request.requestId, jobId: authorization.jobId, createdAt: now.toISOString(), authorizationSha256: sha(authorization), checkpointSha256: authorization.checkpointSha256,
    status, runtimeReceiptSha256, eventRecordSha256, dataClassification: authorization.dataClassification,
    structuredContent: structuredContent === null ? null : structuredClone(structuredContent), contentSha256: structuredContent === null ? null : sha(structuredContent),
    failureCode, terminalState, reason, authority: { ...responseAuthority }, boundary: responseBoundary,
  };
  schema("capability tool response", validateWorkCapabilityToolResponse(response));
  return response;
}

async function appendEvent(paths, event) {
  schema("capability watchdog event", validateWorkCapabilityWatchdogEvent(event));
  const path = join(paths.events, `${String(event.sequence).padStart(7, "0")}.json`);
  if (await lstat(path).then(() => true, () => false)) {
    const existing = await privateJson(path, MAX_RECORD_BYTES, "capability watchdog event");
    if (canonical(existing) !== canonical(event)) fail("capability event settlement differs from existing evidence");
    return existing;
  }
  await writeAtomic(path, event);
  return event;
}

async function archive(paths, request, response) {
  const stage = join(paths.active, "response.stage.json"), destination = join(paths.responses, `${request.requestId}.json`);
  const published = await publishExact(stage, destination, MAX_RESPONSE_BYTES, "capability tool response", validateWorkCapabilityToolResponse);
  if (canonical(published) !== canonical(response)) fail("published capability response differs from settlement");
  if (response.status === "stopped") {
    const terminal = { schemaVersion: 1, requestId: request.requestId, authorizationSha256: response.authorizationSha256, responseSha256: sha(response), terminalState: response.terminalState, reason: response.reason, boundary: custodyBoundary };
    if (!(await lstat(paths.terminal).then(() => true, () => false))) await writeAtomic(paths.terminal, terminal);
    else {
      const existing = await privateJson(paths.terminal, MAX_RECORD_BYTES, "capability queue terminal record");
      if (canonical(existing) !== canonical(terminal)) fail("capability queue terminal record differs from settlement");
    }
  }
  const history = join(paths.history, request.requestId);
  if (!(await lstat(history).then(() => true, () => false))) await rename(paths.active, history);
  await syncDirectory(paths.history);
  await unlink(paths.current); await syncDirectory(paths.root);
  return published;
}

function dependencies(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("capability queue dependencies are invalid");
  for (const name of ["executeRuntime", "statusRuntime", "recoverRuntime", "afterTransition"]) if (value[name] !== undefined && typeof value[name] !== "function") fail("capability queue dependency is invalid");
  return { executeRuntime: value.executeRuntime ?? executeRuntimeDispatch, statusRuntime: value.statusRuntime ?? statusCapabilityRuntime, recoverRuntime: value.recoverRuntime ?? recoverCapabilityRuntime, afterTransition: value.afterTransition };
}

async function transition(hook, phase) { if (hook) await hook(phase); }

export async function processCapabilityToolQueue({ queueRoot, authorization, requestContext, runtime, recoverActive = false, clock = () => new Date(), suffixes = {}, dependencies: dependencyOverrides = {} }) {
  if (typeof recoverActive !== "boolean") fail("capability queue recovery mode is invalid");
  const paths = await queue(queueRoot, authorization), acquired = await acquire(paths, authorization, clock, suffixes, recoverActive);
  if (acquired === null) return Object.freeze({ schemaVersion: 1, status: "idle-disabled", response: null, enabled: false, authority: { ...authority }, boundary: custodyBoundary });
  if (acquired.busy) return Object.freeze({ schemaVersion: 1, status: "busy-disabled", response: null, enabled: false, authority: { ...authority }, boundary: custodyBoundary });
  if (acquired.archived) return Object.freeze({ schemaVersion: 1, status: "already-settled-disabled", response: acquired.response, enabled: false, authority: { ...authority }, boundary: custodyBoundary });
  const request = acquired.request, deps = dependencies(dependencyOverrides);
  let chain = await loadCustody(paths, request, authorization), head = chain.head;
  if (head.phase === "claimed") {
    const events = await readEvents(paths, authorization);
    const compiled = authorizeCapabilityToolRequest({ authorization, request, events, ...requestContext, now: observed(clock, "capability decision"), grantSuffix: suffixes.grant, watchdogSuffix: suffixes.watchdog });
    const decision = { schemaVersion: 1, status: compiled.status, grant: compiled.grant, preflightDecision: compiled.preflightDecision, boundary: decisionBoundary };
    await writeAtomic(join(paths.active, "decision.json"), decision, MAX_RECORD_BYTES);
    if (compiled.status === "stopped") {
      const response = responseFor({ request, authorization, status: "stopped", failureCode: "watchdog-stopped", terminalState: compiled.preflightDecision.terminalState, reason: compiled.preflightDecision.reason, now: observed(clock, "capability stopped response") });
      await writeAtomic(join(paths.active, "response.stage.json"), response, MAX_RESPONSE_BYTES);
      head = await appendCustody(paths, request, authorization, { phase: "stopped", decisionSha256: sha(decision), responseSha256: sha(response), outcome: "stopped" }, clock, suffixes.stopped);
      await transition(deps.afterTransition, "stopped");
      const published = await archive(paths, request, response);
      return Object.freeze({ schemaVersion: 1, status: "stopped-disabled", response: published, enabled: false, authority: { ...authority }, boundary: custodyBoundary });
    }
    head = await appendCustody(paths, request, authorization, { phase: "authorized", decisionSha256: sha(decision), grantId: decision.grant.grantId, grantSha256: sha(decision.grant) }, clock, suffixes.authorized);
    await transition(deps.afterTransition, "authorized");
  }
  const decision = await loadDecision(paths, request, authorization);
  if (head.phase === "authorized") {
    head = await appendCustody(paths, request, authorization, { phase: "launching", decisionSha256: sha(decision), grantId: decision.grant.grantId, grantSha256: sha(decision.grant) }, clock, suffixes.launching);
    await transition(deps.afterTransition, "launching");
    let result;
    try {
      result = await deps.executeRuntime({ ...runtime, runtimeRequest: request, grant: decision.grant, toolName: request.tool, input: request.arguments, preflightDecision: decision.preflightDecision });
    } catch (error) {
      fail("capability runtime did not return a settleable result; recovery is required", error);
    }
    const checked = validateRuntimeResult(result, decision, authorization);
    await writeAtomic(join(paths.active, "runtime-result.json"), checked.result, MAX_RESPONSE_BYTES);
    await transition(deps.afterTransition, "runtime-result-staged");
    head = await appendCustody(paths, request, authorization, { phase: "runtime-returned", decisionSha256: sha(decision), grantId: decision.grant.grantId, grantSha256: sha(decision.grant), runtimeReceiptSha256: checked.result.receiptSha256, outcome: checked.outcome }, clock, suffixes.runtimeReturned);
    await transition(deps.afterTransition, "runtime-returned");
  }
  if (head.phase === "launching") {
    const resultPath = join(paths.active, "runtime-result.json");
    if (!(await lstat(resultPath).then(() => true, () => false))) {
      const status = await deps.statusRuntime(runtime);
      let recovered = null;
      if (status?.status === "cleanup-required") recovered = await deps.recoverRuntime(runtime);
      const receiptSha256 = recovered?.receiptSha256 ?? (status?.latest?.grantId === decision.grant.grantId ? status.latest.receiptSha256 : null);
      const uncertain = { schemaVersion: 1, operation: "pixel-work-capability-runtime-result", status: "uncertain-recovered-contained", pack: { ...authorization.pack }, grantId: decision.grant.grantId, receiptSha256, dataClassification: authorization.dataClassification, enabled: false, authority: { grantsCompletion: false }, boundary: "Recovered without replay; transient output is unavailable." };
      await writeAtomic(resultPath, uncertain, MAX_RESPONSE_BYTES);
    }
    const checked = await loadRuntimeResult(paths, decision, authorization);
    head = await appendCustody(paths, request, authorization, { phase: "runtime-returned", decisionSha256: sha(decision), grantId: decision.grant.grantId, grantSha256: sha(decision.grant), runtimeReceiptSha256: checked.result.receiptSha256, outcome: "error" }, clock, suffixes.runtimeReturned);
    await transition(deps.afterTransition, "runtime-returned");
  }
  if (head.phase === "runtime-returned") {
    const checked = await loadRuntimeResult(paths, decision, authorization);
    const failureFingerprintSha256 = checked.outcome === "success" ? null : sha({ failureCode: checked.failureCode, runtimeReceiptSha256: checked.result.receiptSha256 });
    const eventPath = join(paths.events, `${String(decision.preflightDecision.proposal.sequence).padStart(7, "0")}.json`);
    let event;
    if (await lstat(eventPath).then(() => true, () => false)) {
      event = await privateJson(eventPath, MAX_RECORD_BYTES, "capability watchdog event"); schema("capability watchdog event", validateWorkCapabilityWatchdogEvent(event));
      if (event.eventFingerprintSha256 !== decision.preflightDecision.proposal.eventFingerprintSha256 || event.outcome !== checked.outcome || event.failureFingerprintSha256 !== failureFingerprintSha256 || event.previousEventSha256 !== decision.preflightDecision.eventHeadSha256) fail("existing capability event differs from runtime settlement");
    } else {
      event = createWatchdogEvent({ decision: decision.preflightDecision, outcome: checked.outcome, failureFingerprintSha256, verifiedProgressEpoch: 0, observedAt: observed(clock, "capability event settlement") });
      await appendEvent(paths, event);
    }
    await transition(deps.afterTransition, "event-appended");
    const response = responseFor({ request, authorization, status: checked.outcome === "success" ? "succeeded" : checked.failureCode === "runtime-result-lost" ? "uncertain-no-replay" : "failed-contained", runtimeReceiptSha256: checked.result.receiptSha256, eventRecordSha256: event.recordSha256, structuredContent: checked.outcome === "success" ? checked.result.structuredContent : null, failureCode: checked.failureCode, now: new Date(event.observedAt) });
    const stage = join(paths.active, "response.stage.json");
    if (!(await lstat(stage).then(() => true, () => false))) await writeAtomic(stage, response, MAX_RESPONSE_BYTES);
    else {
      const existing = await privateJson(stage, MAX_RESPONSE_BYTES, "capability tool response");
      if (canonical(existing) !== canonical(response)) fail("staged capability response differs from event settlement");
    }
    await transition(deps.afterTransition, "response-staged");
    head = await appendCustody(paths, request, authorization, { phase: "settled", decisionSha256: sha(decision), grantId: decision.grant.grantId, grantSha256: sha(decision.grant), runtimeReceiptSha256: checked.result.receiptSha256, eventRecordSha256: event.recordSha256, responseSha256: sha(response), outcome: checked.outcome }, clock, suffixes.settled);
    await transition(deps.afterTransition, "settled");
  }
  if (!["settled", "stopped"].includes(head.phase)) fail("capability queue reached an unsupported custody phase");
  const response = await privateJson(join(paths.active, "response.stage.json"), MAX_RESPONSE_BYTES, "capability tool response");
  const published = await archive(paths, request, response); await transition(deps.afterTransition, "archived");
  return Object.freeze({ schemaVersion: 1, status: `${published.status}-disabled`, response: published, enabled: false, authority: { ...authority }, boundary: custodyBoundary });
}

export async function statusCapabilityToolQueue({ queueRoot, authorization }) {
  const paths = await queue(queueRoot, authorization), requests = await readdir(paths.requests), responses = await readdir(paths.responses), history = await readdir(paths.history), events = await readEvents(paths, authorization);
  const active = await lstat(paths.current).then(() => true, () => false);
  if (requests.length > MAX_QUEUE || responses.length > authorization.maxSessions + 1 || history.length > authorization.maxSessions + 1 || events.length > authorization.maxSessions || requests.some((name) => !/^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(name)) || responses.some((name) => !/^workcaprequest-[0-9]{13}-[a-f0-9]{12}\.json$/u.test(name)) || history.some((name) => !REQUEST_RE.test(name))) fail("capability queue status exceeds its authorization or contains an unsafe entry");
  if (!active && canonical(responses.map((name) => name.slice(0, -5)).sort()) !== canonical([...history].sort())) fail("capability queue response and history sets differ");
  const terminal = await lstat(paths.terminal).then(() => true, () => false);
  if (terminal) {
    const value = await privateJson(paths.terminal, MAX_RECORD_BYTES, "capability queue terminal record");
    exactKeys(value, ["schemaVersion", "requestId", "authorizationSha256", "responseSha256", "terminalState", "reason", "boundary"], "capability queue terminal record");
    if (value.schemaVersion !== 1 || !REQUEST_RE.test(value.requestId ?? "") || value.authorizationSha256 !== sha(authorization) || !SHA_RE.test(value.responseSha256 ?? "") || value.boundary !== custodyBoundary) fail("capability queue terminal record is invalid");
  }
  return Object.freeze({ schemaVersion: 1, operation: "pixel-work-capability-tool-queue-status", status: active ? "active-disabled" : terminal ? "stopped-disabled" : "idle-disabled", queued: requests.length, settled: responses.length, events: events.length, active, terminal, enabled: false, authority: { ...authority }, boundary: custodyBoundary });
}

export async function readCapabilityToolResponse({ queueRoot, authorization, requestId }) {
  if (!REQUEST_RE.test(requestId ?? "")) fail("capability response identity is invalid");
  const paths = await queue(queueRoot, authorization), response = await privateJson(join(paths.responses, `${requestId}.json`), MAX_RESPONSE_BYTES, "capability tool response");
  schema("capability tool response", validateWorkCapabilityToolResponse(response));
  if (response.requestId !== requestId || response.jobId !== authorization.jobId || response.authorizationSha256 !== sha(authorization) || response.checkpointSha256 !== authorization.checkpointSha256 || response.dataClassification !== authorization.dataClassification) fail("capability response differs from its queue authorization");
  return response;
}

export const capabilityToolQueueBoundary = custodyBoundary;
