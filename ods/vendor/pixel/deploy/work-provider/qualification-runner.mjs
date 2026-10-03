import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, stat } from "node:fs/promises";
import { isAbsolute, parse, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import {
  buildNeutralCorpus, NEUTRAL_CORPUS_VERSION, QUALIFICATION_LANES, QUALIFICATION_TASKS,
  laneModelFor, laneReasoningEvidence, laneRequiresReasoning, LONG_CONTEXT_MIN_INPUT_TOKENS,
  matchesNeutralToolArguments, OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS, validateNeutralToolChoiceReplay,
} from "./neutral-corpus.mjs";
import { resolveWorkProvider } from "./provider-registry.mjs";
import { resolveWorkProviderTransport } from "./transport-registry.mjs";
import { inspectWorkProviderCredentialCustody } from "./credential-custody.mjs";
import { readOwnerPrivateWorkProviderPolicy } from "./dev-proxy-cli.mjs";
import { parseBoundWorkProviderPrivatePolicy } from "./egress-proxy.mjs";
import { defaultWorkProviderLocalPolicy } from "./local-policy.mjs";
import { gradeDeterministic } from "./grading.mjs";
import { extractPatchProposal, verifyPatchString } from "./patch-extraction.mjs";
import { promoteQualificationTrial } from "./qualification-promotion.mjs";
import {
  claimWorkProviderRequest, closeWorkProviderRun, createQualificationTrialBinding, createWorkProviderRunLedger,
  providerIdempotencyKey, settleWorkProviderRequest, workProviderInputSha256,
} from "./run-ledger.mjs";
import {
  initializeWorkProviderRunStore, replaceWorkProviderRunLedger, storeNewWorkProviderRunLedger,
} from "./run-store.mjs";

const SHA = /^[a-f0-9]{64}$/u;
const PROXY_ROUTES = ["loopback", "container"];
const TRIALS_MIN = 3;
const TRIALS_MAX = 5;
const TRIALS_DEFAULT = 3;
const FAILURE_CODES = new Set(["provider-http", "proxy-connection", "proxy-refused", "request-validation", "response-adapter", "response-framing", "response-identifier", "response-json", "response-metadata", "response-model", "response-size", "response-usage", "response-utf8", "tls", "transport-timeout", "transport-unknown"]);
const INPUT_MICROS_PER_TOKEN = 1;
const OUTPUT_MICROS_PER_TOKEN = 3;
const EVIDENCE_BOUNDARY = "Content-free owner-private neutral qualification-trial evidence. It records only case identity, task, trial index, request count, terminal request state, closed failure category, deterministic rubric pass/fail flags, exact token counts, and exact corpus/lane/ledger/input/reasoning hashes. It never contains exception text, prompt, response, reasoning, tool arguments, credential, account, path, external-effect, deployment, publication, completion, or security-testing authority; an uncertain or failed trial never qualifies semantics and is never retried automatically.";

export class WorkProviderQualificationRunnerError extends Error {}
function fail(message) { throw new WorkProviderQualificationRunnerError(message); }
function contentFreeFailureCode(error) {
  if (FAILURE_CODES.has(error?.failureCode)) return error.failureCode;
  const message = typeof error?.message === "string" ? error.message : "";
  let code = "transport-unknown";
  if (/timed out/iu.test(message)) code = "transport-timeout";
  else if (/proxy connection failed/iu.test(message)) code = "proxy-connection";
  else if (/proxy refused/iu.test(message)) code = "proxy-refused";
  else if (/TLS/iu.test(message)) code = "tls";
  else if (/exceeded.*byte|byte ceiling|oversized/iu.test(message)) code = "response-size";
  else if (/response parsing|chunk framing|chunk extensions|chunk size|final chunk|content length|transfer encoding|body framing|neither Content-Length/iu.test(message)) code = "response-framing";
  else if (/returned HTTP/iu.test(message)) code = "provider-http";
  else if (/strict UTF-8/iu.test(message)) code = "response-utf8";
  else if (/strict JSON/iu.test(message)) code = "response-json";
  else if (/adapter validation/iu.test(message)) code = "response-adapter";
  else if (/response omitted exact token usage/iu.test(message)) code = "response-usage";
  else if (/response model|pinned run model|omitted the exact pinned model/iu.test(message)) code = "response-model";
  else if (/request identifier/iu.test(message)) code = "response-identifier";
  else if (/response metadata|response headers?|response status line|content encoding/iu.test(message)) code = "response-metadata";
  else if (/differs from|escaped|invalid|requires|cannot accept|credential|policy|claim/iu.test(message)) code = "request-validation";
  if (!FAILURE_CODES.has(code)) fail("qualification failure code escaped its closed registry");
  return code;
}
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function parseStructuredJsonResponse(value, label) {
  if (typeof value !== "string") throw new Error(`${label} is not strict JSON`);
  let candidate = value.trim();
  const fenced = /^```json[\t ]*\r?\n([\s\S]*?)\r?\n```$/iu.exec(candidate);
  if (fenced) candidate = fenced[1].trim();
  return parseStrictJson(candidate, label);
}
function iso(value) { return value.toISOString().replace(/\.000Z$/u, "Z"); }
function deepFreeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    for (const nested of Object.values(value)) deepFreeze(nested);
    Object.freeze(value);
  }
  return value;
}
function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) === parse(resolve(value)).root) fail(`${label} is invalid`);
  return resolve(value);
}
function suffix() { return randomBytes(6).toString("hex"); }
async function writeNewPrivateJson(path, value, label) {
  const parent = resolve(path, "..");
  try { await mkdir(parent, { recursive: true, mode: 0o700 }); }
  catch (error) { fail(`${label} parent could not be prepared: ${error.code ?? "mkdir-failed"}`); }
  const parentDetails = await stat(parent).catch(() => null);
  if (!parentDetails?.isDirectory()) fail(`${label} parent must be one real directory`);
  if (process.platform !== "win32" && (parentDetails.uid !== process.geteuid() || (parentDetails.mode & 0o077) !== 0)) fail(`${label} parent must be owner-private`);
  const flags = constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0) | (constants.O_CLOEXEC ?? 0);
  const handle = await open(path, flags, 0o600).catch((error) => fail(`${label} could not be created once: ${error.code ?? "open-failed"}`));
  const payload = Buffer.from(`${JSON.stringify(value, null, 2)}\n`, "utf8");
  try {
    await handle.writeFile(payload);
    await handle.sync();
  } catch (error) {
    fail(`${label} could not be written completely: ${error.code ?? "write-failed"}`);
  } finally {
    payload.fill(0);
    await handle.close().catch(() => {});
  }
}
function estimateInputTokens(input) {
  const bytes = Buffer.byteLength(canonical(input), "utf8");
  return Math.max(1, Math.ceil(bytes / 4));
}
function estimateCostMicros(input, maxOutputTokens, policy, remote) {
  if (!remote) return 0;
  const cost = estimateInputTokens(input) * INPUT_MICROS_PER_TOKEN + maxOutputTokens * OUTPUT_MICROS_PER_TOKEN;
  return Math.min(cost, policy.budgets.maxEstimatedCostMicrosPerRun);
}

const QUALIFICATION_ARTIFACT_BOUNDARY = "Content-free owner-private hash-bound provider qualification artifact. It records only the exact promoted semantic qualification, its qualification/evidence/ledger hashes, and the lane, provider, model, corpus version/hash, trials, and status of the qualified trial that produced it. It never contains prompt, response, reasoning, tool arguments, credential, account, path, external-effect, deployment, publication, completion, or security-testing authority.";

const ARG_NAMES = ["--corpus-version", "--lane", "--policy", "--sha256", "--credential", "--ledger-root", "--proxy-route", "--report", "--qualification-report", "--trials"];

function argumentsFor(argv) {
  if (!Array.isArray(argv) || argv.length < 4 || argv.length % 2 !== 0) fail(`usage: qualification-runner.mjs --lane local-only|moonshot-kimi|openai|anthropic|openrouter --corpus-version ${NEUTRAL_CORPUS_VERSION} --ledger-root ABSOLUTE_PATH [--trials 3|4|5] [--policy PATH --sha256 HEX] [--credential PATH] [--proxy-route loopback|container] [--report PATH] [--qualification-report ABSOLUTE_PATH]`);
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index], value = argv[index + 1];
    if (typeof name !== "string" || !ARG_NAMES.includes(name) || values.has(name)) fail("qualification-trial arguments are invalid");
    if (typeof value !== "string" || value.length < 1) fail(`qualification-trial argument ${name} is invalid`);
    values.set(name, value);
  }
  const lane = values.get("--lane");
  if (!QUALIFICATION_LANES.includes(lane)) fail("qualification-trial lane is not in the closed registry");
  if (values.get("--corpus-version") !== NEUTRAL_CORPUS_VERSION) fail(`qualification-trial corpus version must be the bound neutral corpus version ${NEUTRAL_CORPUS_VERSION}`);
  const proxyRoute = values.get("--proxy-route");
  if (proxyRoute !== undefined && !PROXY_ROUTES.includes(proxyRoute)) fail("qualification-trial proxy route is invalid");
  const reportPath = values.get("--report");
  if (reportPath !== undefined) absolutePath(reportPath, "qualification-trial report path");
  const qualificationReportPath = values.get("--qualification-report");
  if (qualificationReportPath !== undefined) absolutePath(qualificationReportPath, "qualification-report path");
  if (reportPath !== undefined && qualificationReportPath !== undefined && resolve(reportPath) === resolve(qualificationReportPath)) fail("qualification trial report and qualification artifact paths must differ");
  let trials = TRIALS_DEFAULT;
  if (values.has("--trials")) {
    if (!/^[0-9]+$/u.test(values.get("--trials"))) fail("qualification-trial --trials must be an integer");
    trials = Number.parseInt(values.get("--trials"), 10);
    if (!Number.isSafeInteger(trials) || trials < TRIALS_MIN || trials > TRIALS_MAX) fail(`qualification-trial --trials must be between ${TRIALS_MIN} and ${TRIALS_MAX}`);
  }
  if (lane !== "local-only") {
    if (!values.has("--policy") || !values.has("--sha256") || !values.has("--credential") || !proxyRoute) fail("remote qualification-trial requires --policy, --sha256, --credential, and --proxy-route");
    if (!SHA.test(values.get("--sha256"))) fail("remote qualification-trial policy SHA-256 is invalid");
    absolutePath(values.get("--policy"), "remote qualification-trial policy path");
    absolutePath(values.get("--credential"), "remote qualification-trial credential path");
  } else {
    for (const name of ["--policy", "--sha256", "--credential", "--proxy-route"]) {
      if (values.has(name)) fail(`local-only qualification-trial cannot accept ${name}`);
    }
  }
  return Object.freeze({
    lane,
    corpusVersion: NEUTRAL_CORPUS_VERSION,
    trials,
    policyPath: values.get("--policy") === undefined ? null : absolutePath(values.get("--policy"), "policy path"),
    sha256: values.get("--sha256") ?? null,
    credentialPath: values.get("--credential") === undefined ? null : absolutePath(values.get("--credential"), "credential path"),
    ledgerRoot: absolutePath(values.get("--ledger-root"), "ledger root"),
    proxyRoute: proxyRoute ?? null,
    reportPath: reportPath === undefined ? null : absolutePath(reportPath, "report path"),
    qualificationReportPath: qualificationReportPath === undefined ? null : absolutePath(qualificationReportPath, "qualification-report path"),
  });
}

function summarizeForTask(task, assistant) {
  if (!assistant) return null;
  const content = assistant.message?.content ?? null;
  switch (task) {
    case "structural-review":
    case "failure-triage":
    case "structured-output":
      if (typeof content === "string") { try { return parseStructuredJsonResponse(content, `${task} response`); } catch { return { raw: content }; } }
      return content;
    case "long-context-sentinel":
      return typeof content === "string" ? content : "";
    default:
      return content;
  }
}

function gradeCaseEntry(caseEntry, actual, reportedInputTokens, reportedOutputTokens) {
  const grade = gradeDeterministic({ task: caseEntry.task, actual, expected: caseEntry.expected, base: caseEntry.base });
  if (caseEntry.task === "long-context-sentinel") {
    // Byte-exact final content (no trimming) proves the provider retrieved the unique sentinel
    // from the beginning, and the provider-reported input count must reach the required long
    // context floor for every repetition; an estimator alone is insufficient.
    const exact = typeof actual === "string" && actual === caseEntry.expected;
    const tokenOk = Number.isSafeInteger(reportedInputTokens) && reportedInputTokens >= LONG_CONTEXT_MIN_INPUT_TOKENS;
    return Object.freeze({ ...grade, pass: grade.pass && exact && tokenOk, exactReply: exact, inputTokenOk: tokenOk });
  }
  if (caseEntry.task === "output-capacity") {
    // The reply must contain only the fixed neutral word at or above the word floor, and the
    // provider-reported output count must independently reach the token floor.
    const tokenOk = Number.isSafeInteger(reportedOutputTokens) && reportedOutputTokens >= OUTPUT_CAPACITY_MIN_OUTPUT_TOKENS;
    return Object.freeze({ ...grade, pass: grade.pass && tokenOk, outputTokenOk: tokenOk });
  }
  return grade;
}

function buildToolChoiceReplayInput({ caseEntry, assistantMessage, lane }) {
  const call = assistantMessage?.toolCalls?.[0];
  if (!call || assistantMessage.role !== "assistant") fail("tool-choice continuation requires an exact returned assistant tool call");
  return {
    schemaVersion: 1,
    model: caseEntry.firstRequest.model,
    messages: [
      caseEntry.firstRequest.messages[0],
      assistantMessage,
      { role: "tool", toolCallId: call.id, content: caseEntry.syntheticToolResult },
      { role: "user", content: caseEntry.followUpUser },
    ],
    maxOutputTokens: caseEntry.firstRequest.maxOutputTokens,
    reasoningEffort: lane === "local-only" ? "none" : caseEntry.firstRequest.reasoningEffort,
    ...(lane === "local-only" ? { temperature: caseEntry.firstRequest.temperature } : {}),
  };
}

async function resolveLane(options, now) {
  if (options.lane === "local-only") {
    const resolvedProvider = resolveWorkProvider("local");
    const policy = defaultWorkProviderLocalPolicy(resolvedProvider);
    return Object.freeze({ resolvedProvider, policy, privatePolicy: null, credentialHandle: null, proxyOptions: {} });
  }
  const resolvedProvider = resolveWorkProvider(options.lane, { enabledRemoteProviders: [options.lane] });
  const parsed = parseBoundWorkProviderPrivatePolicy(await readOwnerPrivateWorkProviderPolicy(options.policyPath), options.sha256);
  if (parsed.resolvedProvider.profile.id !== options.lane) fail("qualification-trial lane differs from the bound private policy");
  const custody = await inspectWorkProviderCredentialCustody({
    resolvedProvider: parsed.resolvedProvider, policy: parsed.policy, credentialPath: options.credentialPath, now: now(), suffix: suffix(),
  });
  const proxyOptions = options.proxyRoute === "container" ? {} : { proxyHost: "127.0.0.1", proxyPort: 3128 };
  return Object.freeze({
    resolvedProvider: parsed.resolvedProvider, policy: parsed.policy, privatePolicy: parsed.policy,
    credentialHandle: custody.handle, proxyOptions,
  });
}

async function replace(root, stored, ledger) {
  const next = await replaceWorkProviderRunLedger({ root, expectedSha256: stored.sha256, ledger });
  return Object.freeze({ runId: next.runId, sha256: next.sha256 });
}

async function invokeRequest({ caseEntry, trialIndex, requestIndex, input, lane, providerTransport, resolvedProvider, policy, credentialHandle, proxyOptions, ledger, stored, root, now }) {
  const idempotencyKey = providerIdempotencyKey({ runId: ledger.runId, caseId: caseEntry.caseId, lane, trialIndex, requestIndex });
  const estimatedInputTokens = estimateInputTokens(input);
  const maxEstimatedCostMicros = estimateCostMicros(input, input.maxOutputTokens, policy, resolvedProvider.profile.remote);
  const claimed = claimWorkProviderRequest({
    ledger, policy, resolvedProvider, idempotencyKey,
    inputSha256: workProviderInputSha256(input),
    estimatedInputTokens, maxOutputTokens: input.maxOutputTokens, maxEstimatedCostMicros, now: now(),
  });
  stored = await replace(root, stored, claimed);
  let result;
  try {
    result = await providerTransport({
      resolvedProvider, policy, credentialHandle, ledger: claimed, idempotencyKey, input, ...proxyOptions,
    });
  } catch (error) {
    const outcome = error && (error.outcome === "failed-known" || error.outcome === "uncertain") ? error.outcome : "uncertain";
    const settled = settleWorkProviderRequest({ ledger: claimed, policy, resolvedProvider, idempotencyKey, outcome, now: now() });
    stored = await replace(root, stored, settled);
    return { kind: outcome === "uncertain" ? "uncertain" : "failed", outcome, failureCode: contentFreeFailureCode(error), ledger: settled, stored };
  }
  const assistant = result?.assistant;
  if (!assistant || typeof assistant !== "object" || assistant.usage === undefined) {
    const settled = settleWorkProviderRequest({ ledger: claimed, policy, resolvedProvider, idempotencyKey, outcome: "uncertain", now: now() });
    stored = await replace(root, stored, settled);
    return { kind: "uncertain", outcome: "uncertain", failureCode: "response-metadata", ledger: settled, stored };
  }
  const settled = settleWorkProviderRequest({
    ledger: claimed, policy, resolvedProvider, idempotencyKey, outcome: "succeeded",
    usage: assistant.usage, providerRequestId: result?.providerRequestId ?? null, responseModel: assistant?.providerModel ?? null, now: now(),
  });
  stored = await replace(root, stored, settled);
  return { kind: "succeeded", assistant, outcome: "succeeded", failureCode: null, ledger: settled, stored };
}

async function runQualificationTrialCore(argv, transportOverride) {
  const now = () => new Date();
  const options = argumentsFor(argv);
  const laneState = await resolveLane(options, now);
  const { resolvedProvider, policy, privatePolicy, credentialHandle, proxyOptions } = laneState;
  const model = laneModelFor(options.lane, resolvedProvider, privatePolicy);
  const corpus = buildNeutralCorpus({ lane: options.lane, model });
  if (corpus.corpusVersion !== NEUTRAL_CORPUS_VERSION) fail("neutral corpus version is outside the bound registry");
  if (QUALIFICATION_TASKS.some((task) => !corpus.cases.some((entry) => entry.task === task))) fail("neutral corpus is missing a required semantic task");
  const providerTransport = transportOverride ?? resolveWorkProviderTransport(resolvedProvider.profile.id);
  if (typeof providerTransport !== "function") fail("qualification-trial transport is invalid");

  const requiredRequests = corpus.cases.reduce((sum, entry) => sum + entry.requestCount * options.trials, 0);
  if (requiredRequests > policy.budgets.maxRequestsPerRun) fail("qualification-trial corpus exceeds the per-run request budget");
  const longContextCase = corpus.cases.find((entry) => entry.task === "long-context-sentinel");
  if (longContextCase && estimateInputTokens(longContextCase.input) < Math.ceil(LONG_CONTEXT_MIN_INPUT_TOKENS * 1.5)) fail("long-context case does not reach the required neutral input margin");

  const binding = createQualificationTrialBinding({
    corpusVersion: corpus.corpusVersion, corpusSha256: corpus.corpusSha256, lane: options.lane,
    resolvedProvider, privatePolicy: privatePolicy ?? null,
  });
  const taskId = `pixel-qualification-trial-${options.lane}`;
  let ledger = createWorkProviderRunLedger({
    resolvedProvider, policy, taskId, model, binding, now: now(), suffix: suffix(),
  });
  const root = await initializeWorkProviderRunStore({ root: options.ledgerRoot });
  let stored = await storeNewWorkProviderRunLedger({ root, ledger });

  const evidence = [];
  let passed = 0, failed = 0, uncertain = 0;
  let halted = false;

  for (const caseEntry of corpus.cases) {
    for (let trialIndex = 1; trialIndex <= options.trials && !halted; trialIndex += 1) {
      let executedRequests = 0;
      let caseGrade = false;
      let status = "passed";
      let reasoningSha = null;
      const requests = [];
      const addRequest = (requestIndex, state, inputSha256, inputTokens, outputTokens, responseModel, failureCode) => {
        requests.push(deepFreeze({ requestIndex, state, inputSha256, inputTokens, outputTokens, responseModel, failureCode }));
      };

      if (caseEntry.task === "tool-choice-continuation") {
        const first = await invokeRequest({
          caseEntry, trialIndex, requestIndex: 1, input: caseEntry.firstRequest, lane: options.lane,
          providerTransport, resolvedProvider, policy, credentialHandle, proxyOptions, ledger, stored, root, now,
        });
        ledger = first.ledger; stored = first.stored; executedRequests = 1;
        addRequest(1, first.kind === "uncertain" ? "uncertain" : first.kind === "failed" ? "failed-known" : "succeeded",
          caseEntry.firstRequestSha256, first.assistant?.usage?.inputTokens ?? null, first.assistant?.usage?.outputTokens ?? null, first.assistant?.providerModel ?? null, first.failureCode);
        if (first.kind === "uncertain") { status = "uncertain"; uncertain += 1; halted = true; }
        else if (first.kind === "failed") { status = "failed"; failed += 1; }
        else {
          const assistantMessage = first.assistant.message;
          const call = assistantMessage?.toolCalls?.[0];
          const toolChoiceOk = call?.name === caseEntry.toolName && matchesNeutralToolArguments(call?.arguments);
          let reasoningOk = true;
          const returnedEvidence = laneRequiresReasoning(options.lane) ? laneReasoningEvidence(options.lane, assistantMessage) : null;
          if (toolChoiceOk && laneRequiresReasoning(options.lane)) {
            if (returnedEvidence === null) { reasoningOk = false; }
            else reasoningSha = sha(returnedEvidence);
          }
          if (!toolChoiceOk || !reasoningOk) { status = "failed"; failed += 1; }
          else {
            const replayInput = buildToolChoiceReplayInput({ caseEntry, assistantMessage, lane: options.lane });
            let reasoningPreserved = true;
            if (laneRequiresReasoning(options.lane)) {
              const replayed = laneReasoningEvidence(options.lane, replayInput.messages[1]);
              reasoningPreserved = returnedEvidence !== null && replayed !== null && replayed === returnedEvidence;
              if (!reasoningPreserved) reasoningSha = null;
            }
            if (!reasoningPreserved) { status = "failed"; failed += 1; }
            else {
              const second = await invokeRequest({
                caseEntry, trialIndex, requestIndex: 2, input: replayInput, lane: options.lane,
                providerTransport, resolvedProvider, policy, credentialHandle, proxyOptions, ledger, stored, root, now,
              });
              ledger = second.ledger; stored = second.stored; executedRequests = 2;
              addRequest(2, second.kind === "uncertain" ? "uncertain" : second.kind === "failed" ? "failed-known" : "succeeded",
                workProviderInputSha256(replayInput), second.assistant?.usage?.inputTokens ?? null, second.assistant?.usage?.outputTokens ?? null, second.assistant?.providerModel ?? null, second.failureCode);
              if (second.kind === "uncertain") { status = "uncertain"; uncertain += 1; halted = true; }
              else if (second.kind === "failed") { status = "failed"; failed += 1; }
              else {
                const finalAnswer = typeof second.assistant.message?.content === "string" ? second.assistant.message.content : "";
                let continuation = null;
                try { continuation = parseStructuredJsonResponse(finalAnswer, "tool-choice continuation"); } catch {}
                const actual = { chosenTool: caseEntry.toolName, continuation };
                const grade = gradeDeterministic({ task: caseEntry.task, actual, expected: caseEntry.expected, base: null });
                caseGrade = grade.pass; status = grade.pass ? "passed" : "failed";
                if (grade.pass) passed += 1; else failed += 1;
              }
            }
          }
        }
      } else {
        const result = await invokeRequest({
          caseEntry, trialIndex, requestIndex: 1, input: caseEntry.input, lane: options.lane,
          providerTransport, resolvedProvider, policy, credentialHandle, proxyOptions, ledger, stored, root, now,
        });
        ledger = result.ledger; stored = result.stored; executedRequests = 1;
        addRequest(1, result.kind === "uncertain" ? "uncertain" : result.kind === "failed" ? "failed-known" : "succeeded",
          caseEntry.inputSha256, result.assistant?.usage?.inputTokens ?? null, result.assistant?.usage?.outputTokens ?? null, result.assistant?.providerModel ?? null, result.failureCode);
        if (result.kind === "uncertain") { status = "uncertain"; uncertain += 1; halted = true; }
        else if (result.kind === "failed") { status = "failed"; failed += 1; }
        else {
          let actual;
          if (caseEntry.task === "patch-proposal" || caseEntry.task === "disposable-coding-patch") {
            actual = await verifyPatchString({
              patch: extractPatchProposal({ task: caseEntry.task, lane: options.lane, assistant: result.assistant }),
              base: caseEntry.base, expected: caseEntry.expected,
            });
          } else {
            actual = summarizeForTask(caseEntry.task, result.assistant);
          }
          const grade = gradeCaseEntry(caseEntry, actual, result.assistant?.usage?.inputTokens, result.assistant?.usage?.outputTokens);
          caseGrade = grade.pass; status = grade.pass ? "passed" : "failed";
          if (grade.pass) passed += 1; else failed += 1;
        }
      }

      evidence.push(deepFreeze({
        caseId: caseEntry.caseId, task: caseEntry.task, trialIndex, status,
        gradePass: status === "passed" ? true : false,
        caseSha256: caseEntry.caseSha256, reasoningContentSha256: reasoningSha,
        requests: deepFreeze(requests.map((request) => deepFreeze({ ...request }))),
      }));
      if (halted) break;
    }
  }

  const distinctCaseCount = corpus.cases.length;
  const requiredRepetitions = distinctCaseCount * options.trials;
  const qualified = uncertain === 0 && failed === 0 && passed === requiredRepetitions;
  let ledgerSha256 = stored.sha256;
  if (uncertain === 0) {
    ledger = closeWorkProviderRun({ ledger, now: now() });
    stored = await replace(root, stored, ledger);
    ledgerSha256 = stored.sha256;
  }

  const status = uncertain > 0 ? "uncertain" : qualified ? "qualified" : "not-qualified";
  const evidenceSha256 = sha(evidence);
  const attestation = deepFreeze({
    $schema: "https://osmantic.com/pixel/schemas/work-provider-qualification-trial-evidence-v1.schema.json",
    schemaVersion: 1,
    attestationId: `workproviderqualification-${String(now().getTime()).padStart(13, "0")}-${suffix()}`,
    recordedAt: iso(now()),
    lane: options.lane,
    providerId: resolvedProvider.profile.id,
    model,
    corpusVersion: corpus.corpusVersion,
    corpusSha256: corpus.corpusSha256,
    trials: options.trials,
    status,
    distinctCaseCount,
    requiredRepetitions,
    passedRepetitions: passed,
    failedRepetitions: failed,
    uncertainRepetitions: uncertain,
    evidenceSha256,
    ledgerRunId: ledger.runId,
    ledgerSha256,
    perCase: evidence,
    providerContentIncluded: false,
    credentialIncluded: false,
    boundary: EVIDENCE_BOUNDARY,
  });

  const corpusTargetInputTokens = longContextCase ? estimateInputTokens(longContextCase.input) : null;
  const semanticQualification = corpusTargetInputTokens === null
    ? null
    : promoteQualificationTrial({ attestation, ledger, privatePolicy, corpusTargetInputTokens });

  if (options.reportPath !== null) {
    await writeNewPrivateJson(options.reportPath, renderReport(attestation, semanticQualification), "qualification trial report");
  }

  // The qualification artifact is the only owner-private content-free hand-off that the routed
  // equivalence runner may consume. It is mandatory for any successful (qualified) campaign and
  // is written only when a genuine semantic qualification has been promoted from the full trial
  // attestation and the closed durable run ledger.
  if (semanticQualification !== null) {
    if (options.qualificationReportPath === null) fail("a qualified qualification campaign requires --qualification-report");
    const artifact = buildQualificationArtifact({
      semanticQualification, attestation, lane: options.lane, providerId: resolvedProvider.profile.id,
      model, corpusVersion: corpus.corpusVersion, corpusSha256: corpus.corpusSha256, trials: options.trials, ledger,
    });
    await writeNewPrivateJson(options.qualificationReportPath, artifact, "qualification artifact");
  }

  return Object.freeze({
    schemaVersion: 1,
    status,
    qualified,
    lane: options.lane,
    providerId: resolvedProvider.profile.id,
    model,
    corpusVersion: corpus.corpusVersion,
    corpusSha256: corpus.corpusSha256,
    trials: options.trials,
    distinctCaseCount,
    requiredRepetitions,
    passedRepetitions: passed,
    failedRepetitions: failed,
    uncertainRepetitions: uncertain,
    evidenceSha256,
    ledgerRunId: ledger.runId,
    ledgerSha256,
    attestation,
    semanticQualification,
  });
}

function renderReport(attestation, semanticQualification = null) {
  return {
    schemaVersion: 1,
    title: "Pixel sealed neutral provider qualification trial",
    status: attestation.status,
    qualified: attestation.status === "qualified",
    lane: attestation.lane,
    providerId: attestation.providerId,
    model: attestation.model,
    corpusVersion: attestation.corpusVersion,
    corpusSha256: attestation.corpusSha256,
    trials: attestation.trials,
    distinctCaseCount: attestation.distinctCaseCount,
    requiredRepetitions: attestation.requiredRepetitions,
    passedRepetitions: attestation.passedRepetitions,
    failedRepetitions: attestation.failedRepetitions,
    uncertainRepetitions: attestation.uncertainRepetitions,
    evidenceSha256: attestation.evidenceSha256,
    ledgerRunId: attestation.ledgerRunId,
    ledgerSha256: attestation.ledgerSha256,
    semanticQualificationSha256: semanticQualification === null ? null : semanticQualification.qualificationSha256,
    perCase: attestation.perCase.map((entry) => ({
      caseId: entry.caseId, task: entry.task, trialIndex: entry.trialIndex, status: entry.status,
      gradePass: entry.gradePass, caseSha256: entry.caseSha256, reasoningContentSha256: entry.reasoningContentSha256,
      requests: entry.requests.map((request) => ({
        requestIndex: request.requestIndex, state: request.state,
        inputSha256: request.inputSha256, inputTokens: request.inputTokens, outputTokens: request.outputTokens,
        responseModel: request.responseModel, failureCode: request.failureCode,
      })),
    })),
    boundary: EVIDENCE_BOUNDARY,
  };
}

// Content-free hash-bound artifact binding the exact promoted semantic qualification to the
// qualified trial that produced it. The artifact carries only provenance hashes and identity
// fields; it never contains prompt, response, reasoning, credential, or owner content.
function buildQualificationArtifact({ semanticQualification, attestation, lane, providerId, model, corpusVersion, corpusSha256, trials, ledger }) {
  const doc = {
    schemaVersion: 1,
    kind: "work-provider-qualification-artifact",
    status: "qualified",
    qualified: true,
    lane,
    providerId,
    model,
    corpusVersion,
    corpusSha256,
    trials,
    evidenceSha256: attestation.evidenceSha256,
    ledgerRunId: attestation.ledgerRunId,
    ledgerSha256: attestation.ledgerSha256,
    qualificationSha256: semanticQualification.qualificationSha256,
    semanticQualification,
    boundary: QUALIFICATION_ARTIFACT_BOUNDARY,
  };
  const { artifactSha256: _ignored, ...withoutSha } = doc;
  doc.artifactSha256 = sha(withoutSha);
  return deepFreeze(doc);
}

export async function runQualificationTrial(argv = process.argv.slice(2)) {
  return runQualificationTrialCore(argv, null);
}

export const workProviderQualificationTestSeam = Object.freeze({
  runWithTransport(argv, transport) {
    if (typeof transport !== "function") fail("test qualification-trial transport is invalid");
    return runQualificationTrialCore(argv, transport);
  },
  internals: Object.freeze({ argumentsFor, resolveLane, estimateInputTokens, buildToolChoiceReplayInput, validateNeutralToolChoiceReplay, contentFreeFailureCode, sha, buildQualificationArtifact, QUALIFICATION_ARTIFACT_BOUNDARY }),
});

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runQualificationTrial().then((receipt) => {
    process.stdout.write(`${JSON.stringify(receipt)}\n`);
    if (receipt.status !== "qualified") process.exitCode = receipt.status === "uncertain" ? 70 : 1;
  }).catch(() => {
    process.stderr.write("Pixel neutral qualification-trial failed closed. Inspect the content-free run ledger before any retry.\n");
    process.exitCode = 70;
  });
}
