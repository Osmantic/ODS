import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import {
  chmod, lstat, mkdir, open, readFile, realpath,
} from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical, compileResearcher, sha256 } from "../work-broker/broker.mjs";
import {
  claimLease, createLeaseConsumption, discardPreparedRun, prepareResearcherRun,
} from "../work-runner/runner-core.mjs";
import { serveResearchToolQueue } from "../work-research-broker/research-service.mjs";
import {
  finalizeResearchReport, parseResearchReportProposal,
} from "../work-research-broker/report-finalizer.mjs";
import { verifyResearchReport } from "../work-research-broker/citation-verifier.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { buildPixelResearcherJob, researchProvenance } from "./pixel-arm.mjs";
import { loadResearchFixturePipelineOptions } from "./research-fixture-adapter.mjs";
import {
  loadPixelSystemConfig, materializeRun, prepare, removePixelRunRoot, stageSource,
  verifyPreparedComparison,
} from "./pixel-system-cli.mjs";

const INPUT_BOUNDARY = "Owner-private input for one same-model Codex Researcher run through Pixel's exact job-scoped Research Broker authority. It grants no model start, direct public network, credential, external write, publication, purchase, merge, deploy, policy, scope, completion, acceptance, or promotion authority.";
const READY_BOUNDARY = "Content-free private readiness evidence for one Codex Researcher authority service. It binds the split tool queue to Pixel's exact Researcher plan, lease, claim, policy, environment, and local broker without granting research truth, model, network, credential, external-effect, completion, acceptance, or promotion authority.";
const FINALIZE_BOUNDARY = "Owner-private handoff of one untrusted structured Codex Researcher proposal after its MCP transport has stopped. The proposal grants no verification, publication, action, completion, acceptance, or promotion authority.";
const RECEIPT_BOUNDARY = "Content-free private lifecycle evidence for one Codex Researcher authority service. It proves bounded broker processing, deterministic offline citation verification, retained artifact identities, and cleanup only; it grants no semantic entailment, source truth, publication, action, completion, acceptance, or promotion authority.";
const AUTHORITY = Object.freeze({
  directPublicNetworkToModel: false,
  credentialsToModel: false,
  externalWrites: false,
  publish: false,
  purchase: false,
  merge: false,
  deploy: false,
  policyMutation: false,
  scopeExpansion: false,
});
const RUN_RE = /^outcomerun-[0-9]{13}-[a-f0-9]{12}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const MAX_INPUT_BYTES = 16 * 1024 * 1024;
const MAX_FINALIZE_BYTES = 5 * 1024 * 1024;

export class CodexResearchAuthorityError extends Error {}
function fail(message) { throw new CodexResearchAuthorityError(message); }
function hash(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} fields are invalid`);
  return value;
}
function absolute(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}
function timestamp(value, label) {
  if (typeof value !== "string" || !value.endsWith("Z") || !Number.isFinite(Date.parse(value))) fail(`${label} is invalid`);
  return new Date(value);
}
function suffix(value, label = "authority suffix") {
  if (typeof value !== "string" || !/^[a-f0-9]{12}$/u.test(value)) fail(`${label} is invalid`);
  return value;
}
function iso(value) { return value.toISOString().replace(/\.000Z$/u, "Z"); }
function decoded(value, label, maximum = MAX_INPUT_BYTES) {
  if (typeof value !== "string" || value.length < 4 || value.length > Math.ceil(maximum / 3) * 4 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(value)) fail(`${label} is not bounded canonical base64`);
  const bytes = Buffer.from(value, "base64");
  if (bytes.length < 1 || bytes.length > maximum || bytes.toString("base64") !== value) fail(`${label} is not bounded canonical base64`);
  return bytes;
}
function decodeContract(input, prefix) {
  const bytes = decoded(input[`${prefix}ContractBase64`], `${prefix} contract`);
  const expected = input[`${prefix}ContractSha256`];
  if (!SHA_RE.test(expected ?? "") || hash(bytes) !== expected) fail(`${prefix} contract differs from its admitted digest`);
  let value;
  try { value = parseStrictJson(bytes.toString("utf8"), `${prefix} contract`); } catch { fail(`${prefix} contract is not strict JSON`); }
  return { bytes, value, sha256: expected };
}

async function privatePath(path, label, directory = false) {
  absolute(path, label);
  const info = await lstat(path).catch(() => null);
  if (!info || info.isSymbolicLink() || (directory ? !info.isDirectory() : !info.isFile() || info.nlink !== 1)) fail(`${label} is not a real ${directory ? "directory" : "singular file"}`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
  const actual = await realpath(path);
  if (actual !== path) fail(`${label} changed during resolution`);
  return info;
}

async function readPrivateJson(path, label, maximumBytes) {
  const info = await privatePath(path, label);
  if (info.size < 2 || info.size > maximumBytes) fail(`${label} is outside its byte boundary`);
  const bytes = await readFile(path);
  if (bytes.length !== info.size || !Buffer.from(bytes.toString("utf8"), "utf8").equals(bytes)) fail(`${label} is not stable strict UTF-8`);
  try { return parseStrictJson(bytes.toString("utf8"), label); } catch { fail(`${label} is not strict JSON`); }
}

async function createPrivateDirectory(path) {
  await mkdir(path, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(path, 0o700);
  await privatePath(path, "new authority directory", true);
  return path;
}

async function writeNew(path, bytes) {
  absolute(path, "authority output path");
  await privatePath(dirname(path), "authority output parent", true);
  const handle = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
  try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
  if (process.platform !== "win32") await chmod(path, 0o600);
}
async function writeJson(path, value) { return writeNew(path, Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8")); }

async function waitForFinalize(path, lease, signal, dependencies = {}) {
  const delay = dependencies.delay ?? ((milliseconds) => new Promise((resolvePromise) => setTimeout(resolvePromise, milliseconds)));
  for (;;) {
    if (signal?.aborted) fail("Codex Researcher authority was cancelled before finalization");
    const now = dependencies.clock?.() ?? new Date();
    if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() >= Date.parse(lease.expiresAt)) fail("Codex Researcher proposal did not arrive before the immutable lease expiry");
    const info = await lstat(path).catch(() => null);
    if (info) return readPrivateJson(path, "private Codex Researcher finalization input", MAX_FINALIZE_BYTES);
    await delay(25);
  }
}

function after(value, minimum, maximum, label) {
  const lower = Date.parse(minimum) + 1;
  const upper = Date.parse(maximum) - 1;
  const observed = value instanceof Date ? value.getTime() : NaN;
  const milliseconds = Math.max(Number.isSafeInteger(observed) ? observed : 0, lower);
  if (!Number.isSafeInteger(milliseconds) || milliseconds > upper) fail(`${label} is outside the immutable lease`);
  return new Date(milliseconds);
}

function validateInput(value) {
  exactKeys(value, [
    "schemaVersion", "operation", "runId", "now", "idSuffix", "pixelSystemConfigPath",
    "admission", "task", "requestPayloadBase64", "sourcePath", "sourceReference", "researchFixturePath", "researchFixtureReference", "environment",
    "toolPolicy", "verifierDefinition", "modelContractBase64", "modelContractSha256",
    "inferenceContractBase64", "inferenceContractSha256", "readyPath", "finalizePath", "outputRoot",
    "boundary",
  ], "Codex Researcher authority input");
  if (value.schemaVersion !== 1 || value.operation !== "pixel-outcome-codex-research-authority" || !RUN_RE.test(value.runId ?? "") || value.idSuffix !== value.runId.slice(-12) || value.boundary !== INPUT_BOUNDARY) fail("Codex Researcher authority input contract is invalid");
  timestamp(value.now, "Codex Researcher authority time"); suffix(value.idSuffix);
  for (const [field, label] of [
    ["pixelSystemConfigPath", "Pixel system configuration path"], ["sourcePath", "source archive path"],
    ["researchFixturePath", "research fixture path"],
    ["readyPath", "authority ready path"], ["finalizePath", "authority finalize path"], ["outputRoot", "authority output root"],
  ]) absolute(value[field], label);
  if (value.admission?.profile !== "researcher" || value.task?.profile !== "researcher") fail("Codex Researcher authority requires an admitted Researcher task");
  return value;
}

export async function runCodexResearchAuthority(inputPath, dependencies = {}) {
  const input = validateInput(await readPrivateJson(absolute(inputPath, "authority input path"), "private Codex Researcher authority input", MAX_INPUT_BYTES));
  const configuration = await (dependencies.loadPixelSystemConfig ?? loadPixelSystemConfig)(input.pixelSystemConfigPath);
  const outputRoot = await privatePath(input.outputRoot, "authority output root", true).then(() => input.outputRoot);
  await privatePath(dirname(input.readyPath), "authority ready parent", true);
  await privatePath(dirname(input.finalizePath), "authority finalize parent", true);
  if (relative(outputRoot, input.readyPath) === "" || relative(outputRoot, input.finalizePath) === "") fail("authority lifecycle files collide with the artifact root");
  const model = decodeContract(input, "model"), inference = decodeContract(input, "inference");
  const requestPayload = decoded(input.requestPayloadBase64, "Codex Researcher request", 65536);
  const researchPipelineOptions = await (dependencies.loadResearchFixturePipelineOptions ?? loadResearchFixturePipelineOptions)({
    fixturePath: input.researchFixturePath, fixtureReference: input.researchFixtureReference,
    admission: input.admission, task: input.task,
  });
  const now = timestamp(input.now, "Codex Researcher authority time");
  let selected, preparedRun, brokerPromise, brokerReceipt, completed;
  let primaryError = null;
  const abortController = new AbortController();
  const externalSignal = dependencies.externalSignal;
  const cancel = () => abortController.abort();
  if (externalSignal?.aborted) abortController.abort();
  else externalSignal?.addEventListener("abort", cancel, { once: true });
  const batches = [];
  try {
    const materialized = await (dependencies.materializeRun ?? materializeRun)(configuration, input.runId, "researcher");
    selected = materialized.selected;
    const preparedBackend = await (dependencies.prepare ?? prepare)(selected.backendPath, dependencies.prepareDependencies ?? {});
    (dependencies.verifyPreparedComparison ?? verifyPreparedComparison)(preparedBackend, model.value, inference.value);
    const source = exactKeys(input.sourceReference, ["relativePath", "mediaType", "sha256", "bytes"], "Codex Researcher source reference");
    if (source.mediaType !== "application/x-tar" || typeof source.relativePath !== "string" || !SHA_RE.test(source.sha256 ?? "") || !Number.isSafeInteger(source.bytes) || source.bytes < 1) fail("Codex Researcher source reference is invalid");
    await (dependencies.stageSource ?? stageSource)(input.sourcePath, join(selected.objectStore, `${source.sha256}.tar`), source.bytes, source.sha256);
    const built = (dependencies.buildPixelResearcherJob ?? buildPixelResearcherJob)({
      admission: input.admission, task: input.task, requestPayload, sourceReference: source,
      environment: input.environment, toolPolicy: input.toolPolicy, verifierDefinition: input.verifierDefinition,
      modelContract: model.value, inferenceContract: inference.value, workPolicy: preparedBackend.policy,
      now, idSuffix: input.idSuffix,
    });
    const compiled = (dependencies.compileResearcher ?? compileResearcher)(built.request, preparedBackend.policy, built.entries, { now, suffix: built.compileSuffix });
    preparedRun = await (dependencies.prepareResearcherRun ?? prepareResearcherRun)({
      ...compiled, policy: preparedBackend.policy, objectStore: selected.objectStore,
      workspaceRoot: selected.workspaceRoot, executorPath: preparedBackend.environment.executorPath,
      archiveLimits: preparedBackend.environment.archiveLimits, now,
    });
    const claim = (dependencies.createLeaseConsumption ?? createLeaseConsumption)(preparedRun, { now, suffix: input.idSuffix });
    await (dependencies.claimLease ?? claimLease)(selected.workState, claim);
    const queueRoot = await createPrivateDirectory(join(selected.root, "codex-research-queue"));
    const requestDirectory = await createPrivateDirectory(join(queueRoot, "requests"));
    const responseDirectory = await createPrivateDirectory(join(queueRoot, "responses"));
    const researchRuntime = preparedBackend.environment.researchRuntime;
    if (!researchRuntime?.researchCourierQueueRoot || !researchRuntime?.researchEndpoint) fail("Pixel Researcher runtime authority is unavailable");
    const serve = dependencies.serveResearchToolQueue ?? serveResearchToolQueue;
    let brokerFailure = null;
    brokerPromise = serve({
      requestDirectory, responseDirectory, plan: preparedRun.plan, lease: preparedRun.lease, claim,
      stateRoot: selected.workState, courierQueueRoot: researchRuntime.researchCourierQueueRoot,
      objectRoot: selected.objectStore, endpoint: researchRuntime.researchEndpoint,
      signal: abortController.signal,
      onCompleted(result) { batches.push(structuredClone(result.batch)); },
      pipelineOptions: researchPipelineOptions,
      ...(dependencies.researchServiceOptions ?? {}),
    }).catch((error) => { brokerFailure = error; throw error; });
    await new Promise((resolvePromise) => setImmediate(resolvePromise));
    if (brokerFailure) throw brokerFailure;
    const ready = {
      schemaVersion: 1, operation: "pixel-outcome-codex-research-authority-ready", runId: input.runId,
      createdAt: iso(dependencies.clock?.() ?? new Date()), jobId: preparedRun.plan.jobId,
      planSha256: sha256(preparedRun.plan), leaseSha256: sha256(preparedRun.lease), claimSha256: sha256(claim),
      workPolicySha256: sha256(preparedBackend.policy),
      environmentSha256: materialized.environmentTemplateSha256,
      runtimeEnvironmentSha256: preparedBackend.launch.bindings.environmentSha256,
      modelContractSha256: model.sha256, inferenceContractSha256: inference.sha256,
      researchFixtureSha256: input.researchFixtureReference.sha256,
      queueRoot, maxCalls: preparedRun.plan.research.maxQueries,
      leaseExpiresAt: preparedRun.lease.expiresAt, authority: AUTHORITY, boundary: READY_BOUNDARY,
    };
    await writeJson(input.readyPath, ready);
    const finalize = exactKeys(await waitForFinalize(input.finalizePath, preparedRun.lease, abortController.signal, dependencies), [
      "schemaVersion", "operation", "runId", "proposalBase64", "mcpReceiptSha256", "boundary",
    ], "Codex Researcher finalization input");
    if (finalize.schemaVersion !== 1 || finalize.operation !== "pixel-outcome-codex-research-finalize" || finalize.runId !== input.runId || finalize.boundary !== FINALIZE_BOUNDARY || !SHA_RE.test(finalize.mcpReceiptSha256 ?? "")) fail("Codex Researcher finalization input contract is invalid");
    abortController.abort();
    brokerReceipt = await brokerPromise; brokerPromise = null;
    if (brokerReceipt.completed !== batches.length || batches.length < 1 || brokerReceipt.errors !== 0 || brokerReceipt.invalid !== 0) fail("Codex Researcher broker lifecycle did not yield a clean retained batch set");
    const proposalBytes = decoded(finalize.proposalBase64, "Codex Researcher proposal", 4 * 1024 * 1024);
    const proposal = (dependencies.parseResearchReportProposal ?? parseResearchReportProposal)(proposalBytes.toString("utf8"));
    const reportNow = after(dependencies.clock?.() ?? new Date(), batches.at(-1).createdAt, preparedRun.lease.expiresAt, "Codex Researcher report time");
    const report = (dependencies.finalizeResearchReport ?? finalizeResearchReport)({
      proposal, batches, plan: preparedRun.plan, lease: preparedRun.lease, claim,
      now: reportNow, suffix: suffix(dependencies.reportSuffix?.() ?? randomBytes(6).toString("hex"), "report suffix"),
    });
    const verificationNow = after(dependencies.clock?.() ?? new Date(), report.createdAt, preparedRun.lease.expiresAt, "Codex Researcher verification time");
    const verification = await (dependencies.verifyResearchReport ?? verifyResearchReport)({
      report, batches, plan: preparedRun.plan, claim, stateRoot: selected.workState,
      objectRoot: selected.objectStore, now: verificationNow,
      suffix: suffix(dependencies.verificationSuffix?.() ?? randomBytes(6).toString("hex"), "verification suffix"),
    });
    if (verification.status !== "evidence-pass") fail("Codex Researcher deterministic citation verification failed");
    const reportBytes = Buffer.from(`${JSON.stringify(report, null, 2)}\n`, "utf8");
    const verificationBytes = Buffer.from(`${JSON.stringify(verification, null, 2)}\n`, "utf8");
    const provenance = (dependencies.researchProvenance ?? researchProvenance)(
      { batches, report, verification }, hash(reportBytes), hash(verificationBytes), "codex-research-provenance-v1",
    );
    const provenanceBytes = Buffer.from(`${JSON.stringify(provenance, null, 2)}\n`, "utf8");
    const reportPath = join(outputRoot, "codex-research-report.json");
    const verificationPath = join(outputRoot, "codex-research-verification.json");
    const provenancePath = join(outputRoot, "codex-research-provenance.json");
    await writeNew(reportPath, reportBytes); await writeNew(verificationPath, verificationBytes); await writeNew(provenancePath, provenanceBytes);
    const receipt = {
      schemaVersion: 1, operation: "pixel-outcome-codex-research-authority-complete", runId: input.runId,
      completedAt: iso(verificationNow), jobId: preparedRun.plan.jobId,
      planSha256: sha256(preparedRun.plan), leaseSha256: sha256(preparedRun.lease), claimSha256: sha256(claim),
      workPolicySha256: sha256(preparedBackend.policy),
      environmentSha256: materialized.environmentTemplateSha256,
      runtimeEnvironmentSha256: preparedBackend.launch.bindings.environmentSha256,
      modelContractSha256: model.sha256, inferenceContractSha256: inference.sha256,
      researchFixtureSha256: input.researchFixtureReference.sha256,
      mcpReceiptSha256: finalize.mcpReceiptSha256, broker: brokerReceipt,
      batches: batches.length, reportSha256: hash(reportBytes), verificationSha256: hash(verificationBytes),
      provenanceSha256: hash(provenanceBytes), contentStoredBeyondJob: false,
      credentialsExposed: false, directPublicNetworkGrantedToModel: false, externalWritesPerformed: false,
      authority: AUTHORITY, boundary: RECEIPT_BOUNDARY,
    };
    completed = Object.freeze({ ready, receipt, report, verification, provenance });
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    externalSignal?.removeEventListener("abort", cancel);
    abortController.abort();
    const cleanupFailures = [];
    if (brokerPromise) {
      try { await brokerPromise; } catch (error) { cleanupFailures.push(error); }
    }
    if (preparedRun) {
      try { await (dependencies.discardPreparedRun ?? discardPreparedRun)(preparedRun); } catch (error) { cleanupFailures.push(error); }
    }
    if (selected) {
      try { await (dependencies.removePixelRunRoot ?? removePixelRunRoot)(configuration, selected); } catch (error) { cleanupFailures.push(error); }
    }
    if (cleanupFailures.length > 0 && primaryError === null) fail("Codex Researcher authority cleanup failed closed");
  }
  if (!completed) fail("Codex Researcher authority did not produce a completed result");
  await writeJson(join(outputRoot, "authority-receipt.json"), completed.receipt);
  return completed;
}

export async function main(argv = process.argv.slice(2)) {
  if (argv.length !== 1) fail("Usage: codex-research-authority.mjs PRIVATE_INPUT_JSON");
  const controller = new AbortController();
  const cancel = () => controller.abort();
  process.once("SIGTERM", cancel); process.once("SIGINT", cancel);
  try { await runCodexResearchAuthority(resolve(argv[0]), { externalSignal: controller.signal }); }
  finally { process.off("SIGTERM", cancel); process.off("SIGINT", cancel); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`pixel-codex-research-authority: ${error instanceof CodexResearchAuthorityError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}

export const codexResearchAuthorityContract = Object.freeze({
  inputBoundary: INPUT_BOUNDARY, readyBoundary: READY_BOUNDARY,
  finalizeBoundary: FINALIZE_BOUNDARY, receiptBoundary: RECEIPT_BOUNDARY, authority: AUTHORITY,
});
