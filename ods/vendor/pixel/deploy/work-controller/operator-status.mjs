import { createHmac, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, rename, unlink } from "node:fs/promises";
import { join, resolve } from "node:path";

import { validateWorkOperatorStatus } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";

const JOB_RE = /^work-[0-9]{13}-[a-f0-9]{12}$/u;
const SUFFIX_RE = /^[a-f0-9]{12}$/u;
const serviceOrder = ["controller", "local-model", "worker", "verifier", "research", "data-lab", "knowledge-vault", "capability-adapter"];
const failureStages = new Set([
  "profile-execution", "rpc-execution", "proxy-receipt", "proposal-parse", "proposal-contract",
  "evidence-finalization", "artifact-retention", "candidate-contract", "cleanup", "authorization-expired", "interrupted",
]);
const boundary = "Content-free local Deep Work orientation only. This snapshot carries no job authority and cannot pause, cancel, resume, expand boundaries, approve egress, reveal private evidence, or open artifacts.";
const controls = Object.freeze({ browserCanPause: false, browserCanCancel: false, browserCanResume: false, browserCanExpandBoundary: false, browserCanOpenArtifacts: false });
const privacy = Object.freeze({ objectiveText: false, promptText: false, toolArguments: false, artifactNames: false, paths: false, hashes: false, credentials: false, providerContent: false });
const goalBudgetFields = Object.freeze(["jobs", "runtimeSeconds", "modelRequests", "inputTokens", "outputTokens", "networkBytes", "artifactBytes", "failures"]);

export class WorkOperatorStatusError extends Error {}

function fail(message) { throw new WorkOperatorStatusError(message); }
function exactKeys(value, keys, label) { if (!value || typeof value !== "object" || Array.isArray(value) || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) fail(`${label} shape is invalid`); }
function timestamp(value, label) { if (!(value instanceof Date) || !Number.isSafeInteger(value.getTime())) fail(`${label} is invalid`); return value.toISOString(); }
function opaque(secret, namespace, value, length = 24) {
  if (!Buffer.isBuffer(secret) || secret.length !== 32 || typeof value !== "string" || value.length < 1 || value.length > 256) fail("operator projection identity input is invalid");
  return createHmac("sha256", secret).update(`${namespace}\0${value}`, "utf8").digest("hex").slice(0, length);
}
function projectGoal(goal) {
  exactKeys(goal, ["state", "updatedAt", "progress", "usage", "budgets", "continuity", "nextAction"], "operator goal");
  exactKeys(goal.progress, ["milestonesTotal", "milestonesCompleted", "jobsStarted", "failures"], "operator goal progress");
  exactKeys(goal.usage, ["runtimeSeconds", "modelRequests", "inputTokens", "outputTokens", "networkBytes", "artifactBytes", "failures"], "operator goal usage");
  exactKeys(goal.budgets, ["accounting", "used", "limits", "remaining"], "operator goal budgets");
  if (goal.budgets.accounting !== "settled-plus-active-observed") fail("operator goal budget accounting is invalid");
  exactKeys(goal.budgets.used, goalBudgetFields, "operator goal used budgets");
  exactKeys(goal.budgets.limits, goalBudgetFields, "operator goal budget limits");
  exactKeys(goal.budgets.remaining, goalBudgetFields, "operator goal remaining budgets");
  exactKeys(goal.continuity, ["checkpointSequence", "restartSafe", "completionRequiresIndependentVerification", "progressModel", "watchdogRole"], "operator goal continuity");
  for (const field of goalBudgetFields) {
    const used = goal.budgets.used[field], limit = goal.budgets.limits[field], remaining = goal.budgets.remaining[field];
    if (!Number.isSafeInteger(limit) || !Number.isSafeInteger(remaining) || !Number.isSafeInteger(used) || limit < used || remaining !== limit - used) {
      fail("operator goal remaining budget is incoherent");
    }
  }
  if (goal.budgets.used.jobs !== goal.progress.jobsStarted || Object.keys(goal.usage).some((field) => goal.budgets.used[field] < goal.usage[field])) fail("operator goal observed budget use moved behind its durable ledger");
  return {
    state: goal.state, updatedAt: goal.updatedAt, progress: { ...goal.progress }, usage: { ...goal.usage },
    budgets: { accounting: goal.budgets.accounting, used: { ...goal.budgets.used }, limits: { ...goal.budgets.limits }, remaining: { ...goal.budgets.remaining } },
    continuity: { ...goal.continuity }, nextAction: goal.nextAction,
  };
}
function projectSession(session, secret) {
  exactKeys(session, ["jobId", "current", "mode", "state", "startedAt", "updatedAt", "progress", "usage", "artifacts", "verification", "capability", "boundaryState", "activity"], "operator session");
  if (typeof session.current !== "boolean") fail("operator session current-milestone state is invalid");
  if (!JOB_RE.test(session.jobId ?? "") || typeof session.startedAt !== "string" || typeof session.updatedAt !== "string") fail("operator session identity or time is invalid");
  const progressKeys = ["criteriaTotal", "criteriaPassing", "criteriaFailing", "iteration", "maxIterations", "noProgressCount"];
  if (Object.hasOwn(session.progress ?? {}, "failureStage")) progressKeys.push("failureStage");
  exactKeys(session.progress, progressKeys, "operator session progress");
  if (session.progress.failureStage !== undefined && session.progress.failureStage !== null && !failureStages.has(session.progress.failureStage)) fail("operator session failure stage is invalid");
  exactKeys(session.usage, ["runtimeSeconds", "modelRequests", "inputTokens", "outputTokens", "networkBytes", "artifactBytes", "failures"], "operator session usage");
  exactKeys(session.artifacts, ["count", "totalBytes", "kinds"], "operator artifact summary");
  exactKeys(session.capability, ["state", "toolCount", "singleUseCalls", "networkAccess", "externalEffects"], "operator capability summary");
  if (
    !Number.isSafeInteger(session.capability.toolCount) || session.capability.toolCount < 0 || session.capability.toolCount > 64
    || (session.capability.state === "not-configured") !== (session.capability.toolCount === 0)
    || session.capability.singleUseCalls !== true || session.capability.networkAccess !== false || session.capability.externalEffects !== false
  ) fail("operator capability summary is incoherent");
  exactKeys(session.boundaryState, ["state", "requestedExpansion"], "operator boundary summary");
  if (!Array.isArray(session.activity) || session.activity.length > 20 || !Array.isArray(session.artifacts.kinds)) fail("operator session lists are invalid");
  const activity = session.activity.map((event) => {
    exactKeys(event, ["identity", "at", "category", "summaryCode", "outcome"], "operator activity event");
    return { eventId: `workevent-${opaque(secret, "event", `${session.jobId}:${event.identity}`)}`, at: event.at, category: event.category, summaryCode: event.summaryCode, outcome: event.outcome };
  }).sort((a, b) => b.at.localeCompare(a.at) || a.eventId.localeCompare(b.eventId));
  const kinds = session.artifacts.kinds.map((item) => { exactKeys(item, ["kind", "count"], "operator artifact kind"); return { kind: item.kind, count: item.count }; }).sort((a, b) => a.kind.localeCompare(b.kind));
  return {
    sessionHandle: `workdisplay-${opaque(secret, "session", session.jobId)}`, current: session.current, mode: session.mode, state: session.state,
    startedAt: session.startedAt, updatedAt: session.updatedAt,
    progress: { ...session.progress, failureStage: session.progress.failureStage ?? null }, usage: { ...session.usage }, artifacts: { count: session.artifacts.count, totalBytes: session.artifacts.totalBytes, kinds },
    verification: session.verification, capability: { ...session.capability }, boundaryState: { ...session.boundaryState }, activity, controls: { ...controls },
  };
}

export function buildWorkOperatorStatus({ goal, sessions, services, controllerState, secret, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  if (!Array.isArray(sessions) || sessions.length > 20 || !Array.isArray(services) || services.length > 8 || !SUFFIX_RE.test(suffix ?? "")) fail("operator status collection or identity is invalid");
  const projectedSessions = sessions.map((session) => projectSession(session, secret)).sort((a, b) => b.updatedAt.localeCompare(a.updatedAt) || a.sessionHandle.localeCompare(b.sessionHandle));
  const projectedServices = services.map((service) => { exactKeys(service, ["id", "state", "observedAt"], "operator service"); return { ...service }; }).sort((a, b) => serviceOrder.indexOf(a.id) - serviceOrder.indexOf(b.id));
  const generatedAt = timestamp(now, "operator status generation time");
  const projectedGoal = projectGoal(goal);
  const currentSessions = projectedSessions.filter((session) => session.current);
  if (currentSessions.length > 1 || currentSessions.length === 1 && !["running", "waiting-authority", "paused"].includes(projectedGoal.state)) fail("operator current session differs from the durable goal state");
  const capabilityService = projectedServices.find((service) => service.id === "capability-adapter");
  if (projectedSessions.some((session) => session.capability.state === "recovery-required") && capabilityService?.state !== "degraded") fail("operator capability recovery differs from its service state");
  if (currentSessions.some((session) => session.capability.state === "authorized") && capabilityService?.state !== "busy") fail("operator active capability differs from its service state");
  if (!projectedSessions.some((session) => ["authorized", "recovery-required"].includes(session.capability.state)) && capabilityService?.state === "busy") fail("operator capability service claims unsupported activity");
  const expectedUsed = { jobs: projectedGoal.progress.jobsStarted, ...projectedGoal.usage };
  if (currentSessions.length === 1) for (const field of Object.keys(currentSessions[0].usage)) expectedUsed[field] += currentSessions[0].usage[field];
  if (goalBudgetFields.some((field) => projectedGoal.budgets.used[field] !== expectedUsed[field])) fail("operator goal budget use differs from its settled and current session evidence");
  const status = {
    $schema: "https://osmantic.com/pixel/schemas/work-operator-status-v1.schema.json", schemaVersion: 1,
    projectionId: `workoperatorstatus-${String(now.getTime()).padStart(13, "0")}-${suffix}`, generatedAt, controllerState, goal: projectedGoal,
    sessions: projectedSessions, services: projectedServices, privacy: { ...privacy }, boundary,
  };
  const errors = validateWorkOperatorStatus(status); if (errors.length) fail(`operator status is invalid: ${errors[0]}`); return status;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700 }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} is not a real directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-only`);
}

async function atomicReplace(source, destination) {
  for (let attempt = 0; ; attempt += 1) {
    try { await rename(source, destination); return; }
    catch (error) {
      const transientWindowsSharingRace = process.platform === "win32" && ["EACCES", "EBUSY", "EPERM"].includes(error?.code);
      if (!transientWindowsSharingRace || attempt >= 20) throw error;
      await new Promise((done) => setTimeout(done, 5 * (attempt + 1)));
    }
  }
}

async function requireSafeExistingStatus(destination) {
  for (let attempt = 0; ; attempt += 1) {
    const existing = await lstat(destination).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (existing === null) return;
    const privateRegular = existing.isFile() && !existing.isSymbolicLink()
      && (process.platform === "win32" || existing.uid === process.geteuid() && (existing.mode & 0o077) === 0);
    if (privateRegular && existing.nlink === 1) return;
    // A concurrent atomic replacement can unlink the inode after pathname lookup,
    // leaving this lstat result with nlink=0. Re-read the current pathname; never
    // relax the rejection of linked, non-private, or non-regular destinations.
    if (privateRegular && existing.nlink === 0 && attempt < 20) {
      await new Promise((done) => setTimeout(done, attempt + 1));
      continue;
    }
    fail("existing operator status is unsafe");
  }
}

export async function publishWorkOperatorStatus({ stateRoot, status }) {
  const errors = validateWorkOperatorStatus(status); if (errors.length) fail(`operator status is invalid: ${errors[0]}`);
  const root = resolve(stateRoot); await privateDirectory(root, "operator state root");
  const directory = join(root, "operator-status"); await privateDirectory(directory, "operator status root", true);
  const destination = join(directory, "status.json");
  await requireSafeExistingStatus(destination);
  const temporary = join(directory, `.status-${randomBytes(8).toString("hex")}`), handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(status, null, 2)}\n`, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await atomicReplace(temporary, destination);
    if (process.platform !== "win32") { const parent = await open(directory, constants.O_RDONLY); try { await parent.sync(); } finally { await parent.close(); } }
  } catch (error) { await unlink(temporary).catch(() => {}); throw error; }
  return { path: destination, projectionId: status.projectionId };
}

export async function readWorkOperatorStatus({ stateRoot }) {
  const root = resolve(stateRoot); await privateDirectory(root, "operator state root");
  const path = join(root, "operator-status", "status.json"), { text, details } = await readBoundedRegularText(path, 256 * 1024, "operator status");
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== process.geteuid() || (details.mode & 0o077) !== 0)) fail("operator status is not private and single-link");
  let status; try { status = JSON.parse(text); } catch { fail("operator status is not JSON"); }
  const errors = validateWorkOperatorStatus(status); if (errors.length) fail(`operator status is invalid: ${errors[0]}`); return status;
}

export const operatorStatusContract = Object.freeze({ boundary, controls, privacy });
