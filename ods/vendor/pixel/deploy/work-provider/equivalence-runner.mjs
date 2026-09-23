import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, stat } from "node:fs/promises";
import { isAbsolute, parse, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularText } from "../../scripts/lib/secure-files.mjs";
import { buildNeutralCorpus, laneModelFor, NEUTRAL_CORPUS_VERSION, QUALIFICATION_LANES } from "./neutral-corpus.mjs";
import { resolveWorkProvider } from "./provider-registry.mjs";
import { inspectWorkProviderCredentialCustody } from "./credential-custody.mjs";
import { readOwnerPrivateWorkProviderPolicy } from "./dev-proxy-cli.mjs";
import { parseBoundWorkProviderPrivatePolicy } from "./egress-proxy.mjs";
import { defaultWorkProviderLocalPolicy } from "./local-policy.mjs";
import { runNeutralLane, workProviderHarnessTestOnly } from "./harness.mjs";
import { workProviderExecutorTestOnly } from "./executor.mjs";
import { gradeDeterministic } from "./grading.mjs";
import { verifyPatchString } from "./patch-extraction.mjs";
import { providerIdempotencyKey } from "./run-ledger.mjs";
import { initializeWorkProviderRunStore } from "./run-store.mjs";
import { routeWorkProvider } from "../work-provider-router/router.mjs";
import { validateQualification } from "../work-provider-router/qualification.mjs";

const SHA = /^[a-f0-9]{64}$/u;
const PROXY_ROUTES = ["loopback", "container"];
// Genuine semantic corpus cases that the production routed equivalence harness exercises
// through router -> semantic binding -> durable executor -> closed transport -> grader. The
// qualification controls (tool-choice-continuation, long-context-sentinel, output-capacity)
// remain in the sealed qualification runner and are never rerouted here.
const SEMANTIC_EQUIVALENCE_TASKS = Object.freeze(["failure-triage", "patch-proposal", "structural-review"]);
const REQUEST_BOUNDARY = "Content-free local-first router request. It declares only task class, data classification, sensitive categories, context/tool/vision need, input/output token estimates, and a cost ceiling. It carries no prompt, response, reasoning, tool arguments, credential, path, or owner content.";
const POLICY_BOUNDARY = "Owner-private content-free local-first work-provider router policy. It pins the ordered provider preference and the exact enabled remote private-policy SHA-256 set; remote providers stay disabled by default. It grants no execution, credential, network, merge, deploy, publish, external-message, security-testing, or policy-mutation authority.";
const EQUIVALENCE_BOUNDARY = "Content-free provider equivalence run evidence. It records only task identity, provider, model, request/decision/ledger/evidence hashes, terminal request states, exact token counts, and deterministic rubric pass flags. It never contains prompt, response, reasoning, tool arguments, credential, account, path, external-effect, deployment, publication, completion, or security-testing authority; an uncertain outcome is never retried or rerouted.";

export class WorkProviderEquivalenceError extends Error {}
function fail(message) { throw new WorkProviderEquivalenceError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
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
function identity(prefix, isoTime, suff) { return `${prefix}-${String(Date.parse(isoTime)).padStart(13, "0")}-${suff}`; }
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

const QUALIFICATION_ARTIFACT_MAX_BYTES = 256 * 1024;
const ARG_NAMES = ["--lane", "--policy", "--sha256", "--credential", "--ledger-root", "--proxy-route", "--report", "--qualification-report", "--qualification-sha256"];

function argumentsFor(argv) {
  if (!Array.isArray(argv) || argv.length < 4 || argv.length % 2 !== 0) fail("usage: equivalence-runner.mjs --lane local-only|moonshot-kimi|openai|anthropic|openrouter --qualification-report ABSOLUTE_PATH --qualification-sha256 HEX --ledger-root ABSOLUTE_PATH [--policy PATH --sha256 HEX --credential PATH --proxy-route loopback|container] [--report PATH]");
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index], value = argv[index + 1];
    if (typeof name !== "string" || !ARG_NAMES.includes(name) || values.has(name)) fail("equivalence-runner arguments are invalid");
    if (typeof value !== "string" || value.length < 1) fail(`equivalence-runner argument ${name} is invalid`);
    values.set(name, value);
  }
  const lane = values.get("--lane");
  if (!QUALIFICATION_LANES.includes(lane)) fail("equivalence-runner lane is not in the closed registry");
  const proxyRoute = values.get("--proxy-route");
  if (proxyRoute !== undefined && !PROXY_ROUTES.includes(proxyRoute)) fail("equivalence-runner proxy route is invalid");
  const reportPath = values.get("--report");
  if (reportPath !== undefined) absolutePath(reportPath, "equivalence-runner report path");
  if (!values.has("--qualification-report") || !values.has("--qualification-sha256")) fail("equivalence-runner requires --qualification-report and --qualification-sha256");
  if (!SHA.test(values.get("--qualification-sha256"))) fail("equivalence-runner qualification SHA-256 is invalid");
  absolutePath(values.get("--qualification-report"), "equivalence-runner qualification report path");
  if (reportPath !== undefined && resolve(reportPath) === resolve(values.get("--qualification-report"))) fail("equivalence report and qualification artifact paths must differ");
  if (lane !== "local-only") {
    if (!values.has("--policy") || !values.has("--sha256") || !values.has("--credential") || !proxyRoute) fail("remote equivalence-runner requires --policy, --sha256, --credential, and --proxy-route");
    if (!SHA.test(values.get("--sha256"))) fail("remote equivalence-runner policy SHA-256 is invalid");
    absolutePath(values.get("--policy"), "remote equivalence-runner policy path");
    absolutePath(values.get("--credential"), "remote equivalence-runner credential path");
  } else {
    for (const name of ["--policy", "--sha256", "--credential", "--proxy-route"]) {
      if (values.has(name)) fail(`local-only equivalence-runner cannot accept ${name}`);
    }
  }
  return Object.freeze({
    lane,
    policyPath: values.get("--policy") === undefined ? null : absolutePath(values.get("--policy"), "policy path"),
    sha256: values.get("--sha256") ?? null,
    credentialPath: values.get("--credential") === undefined ? null : absolutePath(values.get("--credential"), "credential path"),
    ledgerRoot: absolutePath(values.get("--ledger-root"), "ledger root"),
    proxyRoute: proxyRoute ?? null,
    reportPath: reportPath === undefined ? null : absolutePath(reportPath, "report path"),
    qualificationReportPath: absolutePath(values.get("--qualification-report"), "equivalence-runner qualification report path"),
    qualificationSha256: values.get("--qualification-sha256"),
  });
}

async function resolveLane(options, now) {
  if (options.lane === "local-only") {
    const resolvedProvider = resolveWorkProvider("local");
    const policy = defaultWorkProviderLocalPolicy(resolvedProvider);
    return Object.freeze({ resolvedProvider, policy, privatePolicy: null, credentialHandle: null, proxyOptions: {} });
  }
  const resolvedProvider = resolveWorkProvider(options.lane, { enabledRemoteProviders: [options.lane] });
  const parsed = parseBoundWorkProviderPrivatePolicy(await readOwnerPrivateWorkProviderPolicy(options.policyPath), options.sha256);
  if (parsed.resolvedProvider.profile.id !== options.lane) fail("equivalence-runner lane differs from the bound private policy");
  const custody = await inspectWorkProviderCredentialCustody({
    resolvedProvider: parsed.resolvedProvider, policy: parsed.policy, credentialPath: options.credentialPath, now: now(), suffix: suffix(),
  });
  const proxyOptions = options.proxyRoute === "container" ? {} : { proxyHost: "127.0.0.1", proxyPort: 3128 };
  return Object.freeze({
    resolvedProvider: parsed.resolvedProvider, policy: parsed.policy, privatePolicy: parsed.policy,
    credentialHandle: custody.handle, proxyOptions,
  });
}

// Reads and strictly validates the owner-private qualification artifact produced by the sealed
// qualification runner. The equivalence harness never mints or derives a semantic qualification
// itself: it must consume the exact promoted semanticQualification bound to a qualified trial.
// The artifact is read bounded and no-follow, its schema/hash and provenance are validated, and
// only then is the exact promoted qualification passed to the router and binding.
async function readQualificationArtifact({ reportPath, expectedSha256, lane, providerId, model, corpusVersion, corpusSha256 }) {
  let observed;
  try {
    observed = await readBoundedRegularText(reportPath, QUALIFICATION_ARTIFACT_MAX_BYTES, "qualification artifact");
  } catch (error) {
    fail(`qualification artifact read failed closed: ${error.message}`);
  }
  const pathDetails = await lstat(reportPath).catch(() => null);
  if (observed.details.nlink !== 1 || !pathDetails?.isFile() || pathDetails.isSymbolicLink()
    || pathDetails.dev !== observed.details.dev || pathDetails.ino !== observed.details.ino) fail("qualification artifact must be one bounded real file");
  if (process.platform !== "win32" && (observed.details.uid !== process.geteuid() || (observed.details.mode & 0o077) !== 0)) fail("qualification artifact must be owner-private");
  let artifact;
  try { artifact = parseStrictJson(observed.text, "qualification artifact"); } catch { fail("qualification artifact is not strict JSON"); }
  if (!artifact || typeof artifact !== "object" || Array.isArray(artifact)) fail("qualification artifact is invalid");
  if (artifact.kind !== "work-provider-qualification-artifact" || artifact.schemaVersion !== 1) fail("qualification artifact kind is invalid");
  if (artifact.status !== "qualified" || artifact.qualified !== true) fail("qualification artifact is not qualified");
  if (artifact.artifactSha256 !== expectedSha256) fail("qualification artifact hash does not match the required bound SHA-256");
  const { artifactSha256: _ignored, ...withoutSha } = artifact;
  if (sha(withoutSha) !== expectedSha256) fail("qualification artifact hash does not match its canonical content");
  if (!artifact.semanticQualification || typeof artifact.semanticQualification !== "object" || Array.isArray(artifact.semanticQualification)) fail("qualification artifact semantic qualification is invalid");
  const qualification = validateQualification(artifact.semanticQualification, undefined);
  if (artifact.lane !== lane) fail("qualification artifact lane differs from the equivalence run");
  if (artifact.providerId !== providerId) fail("qualification artifact provider differs from the equivalence run");
  if (artifact.model !== model) fail("qualification artifact model differs from the equivalence run");
  if (artifact.corpusVersion !== corpusVersion) fail("qualification artifact corpus version differs from the equivalence run");
  if (artifact.corpusSha256 !== corpusSha256) fail("qualification artifact corpus hash differs from the equivalence run");
  if (qualification.providerId !== providerId || qualification.model !== model) fail("qualification provider/model differs from the equivalence run");
  if (qualification.qualificationSha256 !== artifact.qualificationSha256) fail("qualification hash differs from the artifact provenance");
  if (!SHA.test(artifact.evidenceSha256 ?? "") || artifact.evidenceSha256 === corpusSha256) fail("qualification artifact evidence is self-minted, not bound to a qualified trial");
  if (!/^[a-f0-9]{64}$/u.test(artifact.ledgerSha256 ?? "") || typeof artifact.ledgerRunId !== "string" || artifact.ledgerRunId.length < 1) fail("qualification artifact provenance is invalid");
  if (!Number.isSafeInteger(artifact.trials) || artifact.trials < 1) fail("qualification artifact trials are invalid");
  return deepFreeze(structuredClone(qualification));
}

function routerRequest({ taskClass, createdAt, suffix: suff }) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-router-request-v1.schema.json",
    schemaVersion: 1,
    requestId: identity("workproviderrequest", createdAt, suff),
    createdAt,
    mode: "explicit-provider",
    explicitProviderId: null,
    taskClass,
    dataClassification: "internal",
    sensitiveCategories: [],
    contextNeed: "workspace",
    toolsNeed: "standard",
    visionNeed: false,
    inputTokenEstimate: 1000,
    outputTokenEstimate: 400,
    costCeilingMicros: 1000000,
    boundary: REQUEST_BOUNDARY,
  };
}

function routerPolicy({ providerId, privatePolicy, createdAt, suffix: suff }) {
  const entries = [{ providerId, privatePolicySha256: sha(privatePolicy) }];
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-router-policy-v1.schema.json",
    schemaVersion: 1,
    routerPolicyId: identity("workproviderrouterpolicy", createdAt, suff),
    createdAt,
    mode: "explicit-provider",
    allowedModes: ["explicit-provider", "local-only", "policy-router"],
    preference: ["local", providerId],
    enabledRemoteProviders: entries,
    qualification: { required: true, maxAgeSeconds: 86400, semanticTaskClasses: [...SEMANTIC_EQUIVALENCE_TASKS] },
    authority: { execution: false, credential: false, network: false, merge: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false, policyMutation: false },
    boundary: POLICY_BOUNDARY,
  };
}

async function runEquivalenceCore(argv, harnessRun) {
  const now = () => new Date();
  const options = argumentsFor(argv);
  const laneState = await resolveLane(options, now);
  const { resolvedProvider, policy, privatePolicy, credentialHandle, proxyOptions } = laneState;
  const model = laneModelFor(options.lane, resolvedProvider, privatePolicy);
  const corpus = buildNeutralCorpus({ lane: options.lane, model });
  if (corpus.corpusVersion !== NEUTRAL_CORPUS_VERSION) fail("neutral corpus version is outside the bound registry");
  const root = await initializeWorkProviderRunStore({ root: options.ledgerRoot });
  const startedAt = now().toISOString().replace(/\.000Z$/u, "Z");
  const qualification = await readQualificationArtifact({
    reportPath: options.qualificationReportPath, expectedSha256: options.qualificationSha256,
    lane: options.lane, providerId: resolvedProvider.profile.id, model,
    corpusVersion: corpus.corpusVersion, corpusSha256: corpus.corpusSha256,
  });
  const qualificationSha256 = qualification.qualificationSha256;
  const runId = `workproviderequiv-${String(Date.parse(startedAt)).padStart(13, "0")}-${suffix()}`;

  const cases = [];
  const perCase = [];
  let passed = 0, failed = 0, uncertain = 0;
  for (const caseEntry of corpus.cases) {
    if (!SEMANTIC_EQUIVALENCE_TASKS.includes(caseEntry.task)) continue;
    const caseStart = now().toISOString().replace(/\.000Z$/u, "Z");
    const req = routerRequest({ taskClass: caseEntry.task, createdAt: caseStart, suffix: suffix() });
    const enabledPrivatePolicies = resolvedProvider.profile.remote ? { [resolvedProvider.profile.id]: privatePolicy } : undefined;
    const qualifications = resolvedProvider.profile.remote
      ? { local: null, [resolvedProvider.profile.id]: qualification }
      : { local: qualification };
    let routerPolicyDoc;
    if (resolvedProvider.profile.remote) {
      req.explicitProviderId = resolvedProvider.profile.id;
      routerPolicyDoc = routerPolicy({ providerId: resolvedProvider.profile.id, privatePolicy, createdAt: caseStart, suffix: suffix() });
    } else {
      req.mode = "local-only";
      req.costCeilingMicros = 1;
      delete req.explicitProviderId;
      routerPolicyDoc = defaultRouterPolicyLocal(caseStart, suffix());
    }
    const { decision } = routeWorkProvider({ request: req, routerPolicy: routerPolicyDoc, enabledPrivatePolicies, qualifications, now: new Date(caseStart), suffix: suffix() });
    const idempotencyKey = providerIdempotencyKey({ runId, caseId: caseEntry.caseId, lane: options.lane, task: caseEntry.task });
    const input = caseEntry.input;
    const lane = await harnessRun({
      task: caseEntry.task, decision, request: req, routerPolicy: routerPolicyDoc, enabledPrivatePolicies, qualifications,
      resolvedProvider, policy, privatePolicy: resolvedProvider.profile.remote ? privatePolicy : null, qualification,
      model, input, idempotencyKey, credentialHandle, proxyRoute: options.proxyRoute ?? undefined,
      runStoreRoot: root, estimatedInputTokens: 1000, maxEstimatedCostMicros: resolvedProvider.profile.remote ? 100000 : 0,
      now: new Date(caseStart), suffix: suffix(), expected: caseEntry.expected, base: caseEntry.base,
    });
    let grade;
    if (lane.status === "uncertain") {
      uncertain += 1;
      grade = gradeDeterministic({ task: caseEntry.task, actual: { outcome: "uncertain", rerouted: false }, expected: undefined, base: null });
    } else {
      let actual = lane.actual;
      if (caseEntry.task === "patch-proposal" && lane.status === "succeeded") actual = await verifyPatchString({ patch: actual, base: caseEntry.base, expected: caseEntry.expected });
      grade = gradeDeterministic({ task: caseEntry.task, actual, expected: caseEntry.expected, base: caseEntry.base });
      if (lane.status === "succeeded" && grade.pass) passed += 1; else failed += 1;
    }
    // Bind the report to the ledger state that the harness actually left on disk.
    // Successful and failed-known runs are closed after grading, so the executor
    // outcome hash is stale at this point. Uncertain runs remain open and retain
    // their last durably persisted ledger instead.
    const evidenceLedger = lane.ledger ?? lane.outcome?.ledger ?? null;
    perCase.push(deepFreeze({
      caseId: caseEntry.caseId, task: caseEntry.task, status: lane.status,
      gradePass: grade.pass, requestSha256: lane.outcome?.ledger?.binding?.requestSha256 ?? null,
      decisionSha256: lane.outcome?.ledger?.binding?.decisionSha256 ?? null,
      ledgerRunId: evidenceLedger?.runId ?? null,
      ledgerSha256: lane.ledgerSha256 ?? null,
      inputTokens: evidenceLedger?.totals?.inputTokens ?? 0,
      outputTokens: evidenceLedger?.totals?.outputTokens ?? 0,
      uncertain: evidenceLedger?.totals?.uncertain ?? 0,
    }));
    cases.push(caseEntry.caseId);
  }

  const report = deepFreeze({
    schemaVersion: 1,
    title: "Pixel content-free routed provider equivalence run",
    runId,
    providerId: resolvedProvider.profile.id,
    model,
    lane: options.lane,
    corpusVersion: corpus.corpusVersion,
    corpusSha256: corpus.corpusSha256,
    qualificationSha256,
    startedAt,
    finishedAt: iso(now()),
    cases,
    counters: { passed, failed, uncertain },
    perCase,
    boundary: EQUIVALENCE_BOUNDARY,
  });
  if (options.reportPath !== null) {
    await writeNewPrivateJson(options.reportPath, report, "equivalence report");
  }
  return report;
}

function defaultRouterPolicyLocal(createdAt, suff) {
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-provider-router-policy-v1.schema.json",
    schemaVersion: 1,
    routerPolicyId: identity("workproviderrouterpolicy", createdAt, suff),
    createdAt,
    mode: "local-only",
    allowedModes: ["explicit-provider", "local-only", "policy-router"],
    preference: ["local"],
    enabledRemoteProviders: [],
    qualification: { required: true, maxAgeSeconds: 86400, semanticTaskClasses: [...SEMANTIC_EQUIVALENCE_TASKS] },
    authority: { execution: false, credential: false, network: false, merge: false, deploy: false, publish: false, externalMessages: false, productionCredentials: false, securityTesting: false, policyMutation: false },
    boundary: POLICY_BOUNDARY,
  };
}

export async function runEquivalence(argv = process.argv.slice(2)) {
  return runEquivalenceCore(argv, runNeutralLane);
}

export const workProviderEquivalenceTestSeam = Object.freeze({
  runWithTransport(argv, transport) {
    if (typeof transport !== "function") fail("test equivalence-runner transport is invalid");
    const harnessRun = (options) => workProviderHarnessTestOnly.runNeutralLaneWithExecutor(options, (runOptions) => workProviderExecutorTestOnly.executeProviderTurnWithTransportAndStore(runOptions, transport));
    return runEquivalenceCore(argv, harnessRun);
  },
  internals: Object.freeze({ argumentsFor, resolveLane, readQualificationArtifact, routerRequest, routerPolicy, defaultRouterPolicyLocal, sha }),
});

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runEquivalence().then((report) => {
    process.stdout.write(`${JSON.stringify(report)}\n`);
    if (report.counters.uncertain > 0) process.exitCode = 70;
    else if (report.counters.failed > 0) process.exitCode = 1;
  }).catch(() => {
    process.stderr.write("Pixel provider equivalence-runner failed closed. Inspect the content-free run ledger before any retry.\n");
    process.exitCode = 70;
  });
}
