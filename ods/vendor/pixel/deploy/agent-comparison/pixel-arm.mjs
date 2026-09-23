import { readFile } from "node:fs/promises";

import { canonical, compileBuilder, compileResearcher, reviewWorkJobAgainstPlannedPolicy, sha256 } from "../work-broker/broker.mjs";
import {
  claimLease, createLeaseConsumption, discardPreparedRun, prepareBuilderRun, prepareResearcherRun,
} from "../work-runner/runner-core.mjs";
import { runResearcherDockerLifecycle } from "../work-runner/docker-supervisor.mjs";
import {
  validatePlanVerificationEvidence, validateWorkResearchBatch, validateWorkResearchReport,
  validateWorkResearchRevisionHistory, validateWorkResearchRevisionReview, validateWorkResearchVerification,
} from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { executeBuilderIteration } from "../work-controller/builder-loop.mjs";
import { initializeCheckpointLedger, recoverCheckpointLedger } from "../work-controller/checkpoints.mjs";
import {
  completeGoal, dispatchGoalMilestone, goalCheckpointSha256, goalSha256,
  initializeGoalLedger, observeGoalMilestone, recoverGoalLedger,
} from "../work-controller/goals.mjs";
import {
  admitGoalRunBundle, appendContinuationGoalRunBundle, initializeGoalRunBundle, recoverGoalRunBundles,
} from "../work-controller/goal-run-bundles.mjs";
import { validateResearchRevisionReview } from "../work-controller/research-revision-review.mjs";

const JOB_BOUNDARY = "This request proposes a bounded disposable job only. It grants no capability, credential, network, merge, deployment, policy-change, production, publication, purchase, or external-effect authority.";
const RESEARCH_PROVENANCE_BOUNDARY = "Content-free broker provenance for one public Researcher result only. URLs, domains, queries, source text, titles, snippets, and evidence passages are omitted; hashes, source types, ranks, retrieval times, and citation bindings grant no truth, semantic-entailment, freshness, publication, action, or completion authority.";
const TASK_COMPATIBILITY_BOUNDARY = "Content-free read-only proof that one exact admitted comparison task compiles against the exact Pixel policy, tools, services, budgets, verifier, model, and inference contracts. It starts no model, runs no task or tool, reads no source content, and grants no execution, network, credential, external effect, completion, publication, deployment, acceptance, or promotion authority.";
const PLANNED_TASK_COMPATIBILITY_BOUNDARY = "Content-free non-authorizing proof that one exact admitted comparison task fits the tools, services, budgets, verifier, model identity, inference, inputs, and output envelope of one exact disabled or unqualified Pixel policy. It emits no plan or lease, does not assert model qualification or execution readiness, starts no model, container, network, task, or tool, reads no source content, and grants no execution, credential, external effect, completion, publication, deployment, acceptance, or promotion authority.";
const ASSISTANT_PLAN_BOUNDARY = "Private profile-exact preparation contract for one disposable Pixel portal Assistant turn. It narrows the admitted neutral tools to an explicit OpenClaw lease and binds the exact source, request, model, inference, verifier, services, limits, and disabled external-effect boundary; it grants no host access, ambient credential, direct network, package installation, external effect, merge, deployment, policy mutation, completion, publication, acceptance, or promotion authority.";
const CONTROLLER_GOAL_BOUNDARY = "Private immutable long-horizon objective and exact child-job graph. It schedules no work by itself and grants no lease, credential, scope expansion, external effect, or completion authority.";
const CONTROLLER_EVIDENCE_BOUNDARY = "Content-free exact Pixel Controller evidence for one admitted long-horizon comparison objective. It binds the immutable goal, DSV4-backed Builder child, durable run custody, parent and child checkpoint lineage, independent verification, usage, and cleanup disposition; it grants no execution, replay, lease, scope expansion, external effect, completion, publication, deployment, acceptance, or promotion authority.";
const INTERACTION_BOUNDARY = "Content-free mechanically observed interaction telemetry for one single-admission noninteractive comparison run. It records only event classes, counts, and an observation digest; it contains no prompt, response, tool argument, result, path, credential, provider content, approval authority, or completion authority.";
const CLASSIFICATION = Object.freeze({ public: "public", internal: "internal", confidential: "confidential", private: "restricted" });
const TOOL_MAP = Object.freeze({
  read: "read", search: "search", write: "write", edit: "edit", shell: "bash", eval: "eval",
  lsp: "lsp", debug: "debug", task: "task", hub: "hub",
});
const ASSISTANT_TOOL_MAP = Object.freeze({
  browser: Object.freeze(["web_fetch", "web_search"]),
  "calendar-read": Object.freeze(["pixel_calendar_get", "pixel_calendar_list", "pixel_limb_status"]),
  "calendar-write": Object.freeze(["pixel_calendar_get", "pixel_calendar_list", "pixel_calendar_propose_create", "pixel_calendar_propose_delete", "pixel_calendar_propose_update", "pixel_limb_status"]),
  debug: Object.freeze(["debug"]),
  edit: Object.freeze(["apply_patch", "edit"]),
  "email-read": Object.freeze(["pixel_gmail_inbox", "pixel_gmail_read", "pixel_gmail_search", "pixel_gmail_sent", "pixel_gmail_thread", "pixel_limb_status"]),
  eval: Object.freeze(["eval"]),
  "fleet-read": Object.freeze(["pixel_ops_inventory"]),
  hub: Object.freeze(["hub"]),
  lsp: Object.freeze(["lsp"]),
  "public-research": Object.freeze(["web_fetch", "web_search"]),
  read: Object.freeze(["read"]),
  search: Object.freeze(["glob", "grep", "memory_get", "memory_search"]),
  shell: Object.freeze(["exec", "process"]),
  task: Object.freeze(["session_status", "sessions_history", "sessions_list", "sessions_send", "sessions_spawn", "sessions_yield", "subagents", "task", "todo", "yield"]),
  write: Object.freeze(["write"]),
});
const ASSISTANT_SERVICE_BY_TOOL = Object.freeze({
  browser: "public-research", "public-research": "public-research",
  "email-read": "email", "calendar-read": "calendar", "calendar-write": "calendar", "fleet-read": "fleet",
  "github-read": "github", "github-write": "github",
});

export class PixelArmError extends Error {}

function fail(message) { throw new PixelArmError(message); }
function same(left, right) { return canonical(left) === canonical(right); }
function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}
function utc(value, label) {
  if (typeof value !== "string" || !value.endsWith("Z") || !Number.isFinite(Date.parse(value))) fail(`${label} is invalid`);
  return value;
}
function suffix(value) {
  if (typeof value !== "string" || !/^[a-f0-9]{12}$/.test(value)) fail("Pixel arm suffix is invalid");
  return value;
}

function interactionReceipt(profile, source, observation, counts = {}) {
  const operatorAttentionRequests = integer(counts.operatorAttentionRequests ?? 0, 0, 1000000, "Pixel operator attention requests");
  const approvalRequests = integer(counts.approvalRequests ?? 0, 0, 1000000, "Pixel approval requests");
  const scopeExpansionRequests = integer(counts.scopeExpansionRequests ?? 0, 0, 1000000, "Pixel scope expansion requests");
  const safetyBlocks = integer(counts.safetyBlocks ?? 0, 0, 1, "Pixel safety blocks");
  const interruptions = integer(counts.interruptions ?? 0, 0, 1000000, "Pixel interruptions");
  if (approvalRequests > operatorAttentionRequests || scopeExpansionRequests > operatorAttentionRequests) {
    fail("Pixel interaction sub-count exceeds observed operator attention requests");
  }
  return Object.freeze({
    schemaVersion: 1,
    mode: "single-admission-noninteractive",
    source,
    observationSha256: sha256(observation),
    operatorInputsAfterAdmission: 0,
    operatorAttentionRequests,
    approvalRequests,
    scopeExpansionRequests,
    safetyBlocks,
    interruptions,
    complete: true,
    workerSelfReported: false,
    boundary: INTERACTION_BOUNDARY,
  });
}

export function outcomeInferencePolicy(contract) {
  const sampling = contract?.sampling;
  const request = contract?.request;
  if (!sampling || !request) fail("shared inference contract is unavailable");
  return {
    enforcement: "exact-request-boundary-v1", wireApi: request.wireApi,
    temperaturePermille: sampling.temperaturePermille, topPPermille: sampling.topPPermille,
    topK: sampling.topK, minPPermille: sampling.minPPermille,
    repeatPenaltyPermille: sampling.repeatPenaltyPermille, seed: sampling.seed,
    reasoningEffort: sampling.reasoningEffort, reasoningVisibility: sampling.reasoningVisibility,
  };
}

export function outcomeInferenceBoundaryPolicy(contract) {
  const request = contract?.request;
  if (!request) fail("shared inference request contract is unavailable");
  return {
    ...outcomeInferencePolicy(contract), stream: request.stream,
    maxOutputTokens: request.maxOutputTokens, toolEncoding: request.toolEncoding,
  };
}

function exactBuilderTools(toolPolicy, profile) {
  const mapped = [];
  for (const tool of toolPolicy.tools) {
    const translated = TOOL_MAP[tool];
    if (!translated) fail(`neutral tool ${tool} has no Builder authority-preserving mapping`);
    mapped.push(translated);
  }
  if (new Set(mapped).size !== mapped.length || !same([...mapped].sort(), [...profile.tools].sort())) {
    fail("neutral tools differ from the private Builder policy");
  }
  return [...profile.tools];
}

function exactBuilderServices(toolPolicy, profile) {
  const mapped = toolPolicy.brokeredServices.map((service) => {
    if (service !== "local-model") fail(`neutral service ${service} has no Builder authority-preserving mapping`);
    return "local-model";
  });
  if (!same([...mapped].sort(), [...profile.services].sort())) fail("neutral services differ from the private Builder policy");
  return [...profile.services];
}

function exactResearcherTools(toolPolicy, profile) {
  const mapped = [];
  let researchTool = false;
  for (const tool of toolPolicy.tools) {
    if (tool === "public-research") { researchTool = true; continue; }
    const translated = TOOL_MAP[tool];
    if (!translated || !["read", "search", "write", "edit", "bash"].includes(translated)) {
      fail(`neutral tool ${tool} has no Researcher authority-preserving mapping`);
    }
    mapped.push(translated);
  }
  if (!researchTool || new Set(mapped).size !== mapped.length || !same([...mapped].sort(), [...profile.tools].sort())) {
    fail("neutral tools differ from the private Researcher policy");
  }
  return [...profile.tools];
}

function exactResearcherServices(toolPolicy, profile) {
  const mapped = toolPolicy.brokeredServices.map((service) => {
    if (service === "local-model") return "local-model";
    if (service === "public-research") return "research-broker";
    fail(`neutral service ${service} has no Researcher authority-preserving mapping`);
  });
  if (!same([...mapped].sort(), [...profile.services].sort())) fail("neutral services differ from the private Researcher policy");
  return [...profile.services];
}

function verifyModelBinding(model, inference, policy, { requirePrepared = true } = {}) {
  const local = policy.localModel;
  const runtime = model?.runtime;
  const artifact = model?.artifact;
  if (
    (requirePrepared && !local?.prepared) || !runtime || !artifact || model.modelId !== local.id
    || runtime.implementation !== local.provider || runtime.imageDigest !== local.imageDigest
    || artifact.sha256 !== local.modelArtifactSha256 || runtime.contextWindow !== local.contextWindow
    || inference?.request?.maxOutputTokens !== local.maxRequestOutputTokens
    || inference?.request?.stream !== true || inference?.request?.toolEncoding !== "function"
    || inference?.request?.promptCachePolicy !== "empty-at-run-start"
    || inference?.request?.requestFieldPolicySha256 !== sha256(outcomeInferenceBoundaryPolicy(inference))
    || !same(outcomeInferencePolicy(inference), local.inference)
  ) fail("Pixel private policy is not bound to the exact shared model and inference contracts");
}

function verifyEnvironmentBinding(environment, verifier, toolPolicy, policy) {
  const limits = environment.limits;
  const privateVerifier = policy.verifier;
  if (
    environment.platform?.operatingSystem !== "linux" || environment.platform?.distribution !== "debian-12"
    || environment.isolation?.controlFiles !== "inert" || environment.isolation?.directNetwork !== false
    || environment.isolation?.packageInstallation !== false || environment.isolation?.crossRunState !== false
    || limits.maxConcurrentSubagents !== toolPolicy.maximumSubagents
  ) fail("Pixel environment differs from the neutral deterministic envelope");
  if (environment.verifier.imageDigest !== policy.runner.imageDigest) fail("Pixel verifier image differs from the private runner policy");
  if (verifier !== null && (
    !privateVerifier?.enabled
    || !same([...environment.verifier.allowedExecutables].sort(), [...privateVerifier.allowedExecutables].sort())
    || verifier.maxRuntimeSeconds > environment.verifier.maxRuntimeSeconds
    || verifier.maxOutputBytes > environment.verifier.maxOutputBytes
    || verifier.checks.length > environment.verifier.maxChecks
  )) fail("Pixel independent verifier differs from the neutral environment");
}

function workBudgets(admission, environment) {
  const admitted = admission.budgets;
  const limits = environment.limits;
  return {
    maxRuntimeSeconds: admitted.wallTimeSeconds,
    maxIterations: limits.maxIterations,
    maxToolCalls: limits.maxToolCalls,
    maxConcurrentSubagents: limits.maxConcurrentSubagents,
    maxModelRequests: admitted.modelRequests,
    maxInputTokens: admitted.inputTokens,
    maxOutputTokens: admitted.outputTokens,
    maxCpuCores: limits.maxCpuCores,
    maxMemoryMiB: limits.maxMemoryMiB,
    maxDiskBytes: limits.maxDiskBytes,
    maxArtifactBytes: admitted.artifactBytes,
    maxNetworkBytes: limits.maxNetworkBytes,
    maxFailures: limits.maxFailures,
    noProgressLimit: limits.noProgressLimit,
  };
}

function assistantToolsAndServices(toolPolicy) {
  if (!toolPolicy || !Array.isArray(toolPolicy.tools) || !Array.isArray(toolPolicy.brokeredServices)) {
    fail("Pixel Assistant tool policy is unavailable");
  }
  const allowedTools = [];
  const requiredServices = new Set(["local-model"]);
  for (const neutral of toolPolicy.tools) {
    const mapped = ASSISTANT_TOOL_MAP[neutral];
    if (!mapped) fail(`neutral tool ${neutral} has no Assistant authority-preserving mapping`);
    allowedTools.push(...mapped);
    const service = ASSISTANT_SERVICE_BY_TOOL[neutral];
    if (service) requiredServices.add(service);
  }
  const tools = [...new Set(allowedTools)].sort();
  const services = [...requiredServices].sort();
  if (tools.length < 1 || tools.length > 256 || !same([...toolPolicy.brokeredServices].sort(), services)) {
    fail("neutral Assistant services or expanded tool lease differ from the exact admitted policy");
  }
  return { tools, services };
}

export function buildPixelAssistantExecution({
  admission, task, requestPayload, sourceReference, environment, toolPolicy, verifierDefinition,
  modelContract, inferenceContract, workPolicy, now, idSuffix,
}, { policyReadiness = "ready" } = {}) {
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime())) fail("Pixel Assistant clock is invalid");
  if (admission?.status !== "admitted-inert" || admission.profile !== "assistant" || task?.profile !== "assistant") {
    fail("Pixel Assistant arm requires a formally admitted Assistant task");
  }
  if (admission.comparisonLane !== "same-model-harness" || task.comparisonLane !== "same-model-harness") {
    fail("Pixel Assistant arm requires the same-model-harness lane");
  }
  const lifecycle = workPolicy?.profiles?.scout;
  if (policyReadiness === "ready") {
    if (!workPolicy?.enabled || !workPolicy.runner?.prepared || !workPolicy.localModel?.prepared || !lifecycle?.enabled) fail("Pixel private Assistant lifecycle policy is disabled or unprepared");
  } else if (policyReadiness === "planned") {
    if (!workPolicy?.runner?.prepared || !lifecycle?.enabled) fail("Pixel private Assistant planned lifecycle envelope is disabled or runner-unprepared");
  } else fail("Pixel Assistant policy readiness mode is invalid");
  if (
    sourceReference?.mediaType !== "application/x-tar"
    || sourceReference.sha256 !== admission.bindings.sourceSnapshotSha256
    || !Number.isSafeInteger(sourceReference.bytes) || sourceReference.bytes < 1
  ) fail("Pixel Assistant source is not the exact admitted tar snapshot");
  if (!(requestPayload instanceof Uint8Array) || requestPayload.length < 1 || sha256(Buffer.from(requestPayload)) !== admission.bindings.userRequestSha256) {
    fail("Pixel Assistant request differs from its admission");
  }
  try {
    const objective = new TextDecoder("utf-8", { fatal: true }).decode(requestPayload).trim();
    if (!objective || objective.length > 8000) fail("Pixel Assistant objective is empty or oversized");
  } catch (error) {
    if (error instanceof PixelArmError) throw error;
    fail("Pixel Assistant request is not UTF-8");
  }
  if (!Array.isArray(verifierDefinition?.acceptanceCriteria) || verifierDefinition.acceptanceCriteria.length < 1) {
    fail("Pixel Assistant has no controller-selected acceptance criteria");
  }
  verifyModelBinding(modelContract, inferenceContract, workPolicy, { requirePrepared: policyReadiness === "ready" });
  verifyEnvironmentBinding(environment, verifierDefinition.workspaceVerification, toolPolicy, workPolicy);
  if (
    (toolPolicy.workspace === "read-only") !== (environment.isolation.workspace === "fresh-read-only")
    || toolPolicy.maximumSubagents !== environment.limits.maxConcurrentSubagents
  ) fail("Pixel Assistant workspace or subagent lease differs from the neutral environment");
  const capabilityLease = assistantToolsAndServices(toolPolicy);
  const budgets = workBudgets(admission, environment);
  const limbs = {
    email: capabilityLease.services.includes("email"),
    calendar: capabilityLease.services.includes("calendar"),
    web: capabilityLease.services.includes("public-research"),
    operations: capabilityLease.services.includes("fleet"),
    social: false, frontier: false,
  };
  const request = {
    schemaVersion: 1, operation: "pixel-portal-outcome-assistant-request", profile: "assistant",
    requestSha256: sha256(Buffer.from(requestPayload)),
    source: { sha256: sourceReference.sha256, bytes: sourceReference.bytes, mediaType: sourceReference.mediaType },
    workspace: toolPolicy.workspace, tools: capabilityLease.tools, services: capabilityLease.services,
    budgets, verifierSha256: sha256(verifierDefinition), modelContractSha256: sha256(modelContract),
    inferenceContractSha256: sha256(inferenceContract), externalEffects: false,
    boundary: ASSISTANT_PLAN_BOUNDARY,
  };
  const plan = {
    schemaVersion: 1, operation: "pixel-portal-outcome-assistant-plan", profile: "assistant",
    requestSha256: sha256(request), allowedTools: capabilityLease.tools, brokeredServices: capabilityLease.services,
    limbs, workspace: toolPolicy.workspace, maximumSubagents: toolPolicy.maximumSubagents,
    limits: budgets, idSuffix: suffix(idSuffix), boundary: ASSISTANT_PLAN_BOUNDARY,
  };
  return { request, plan };
}

function buildPixelBuilderChild({
  admission, task, requestPayload, sourceReference, environment, toolPolicy, verifierDefinition,
  modelContract, inferenceContract, workPolicy, now, idSuffix,
}, { policyReadiness = "ready", admittedProfile = "builder" } = {}) {
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime())) fail("Pixel Builder clock is invalid");
  if (!new Set(["builder", "controller"]).has(admittedProfile)) fail("Pixel Builder child owner profile is invalid");
  if (admission?.status !== "admitted-inert" || admission.profile !== admittedProfile || task?.profile !== admittedProfile) {
    fail(`Pixel Builder child requires a formally admitted ${admittedProfile} task`);
  }
  if (admission.comparisonLane !== "same-model-harness" || task.comparisonLane !== "same-model-harness") {
    fail("Pixel Builder arm requires the same-model-harness lane");
  }
  if (policyReadiness === "ready") {
    if (!workPolicy?.enabled || !workPolicy.runner?.prepared || !workPolicy.localModel?.prepared || !workPolicy.profiles?.builder?.enabled) fail("Pixel private Builder policy is disabled or unprepared");
  } else if (policyReadiness === "planned") {
    if (!workPolicy?.runner?.prepared || !workPolicy.profiles?.builder?.enabled) fail("Pixel private Builder planned envelope is disabled or runner-unprepared");
  } else fail("Pixel Builder policy readiness mode is invalid");
  if (
    sourceReference?.mediaType !== "application/x-tar"
    || sourceReference.sha256 !== admission.bindings.sourceSnapshotSha256
    || !Number.isSafeInteger(sourceReference.bytes) || sourceReference.bytes < 1
  ) {
    fail("Pixel Builder source is not the exact admitted tar snapshot");
  }
  if (!(requestPayload instanceof Uint8Array) || requestPayload.length < 1 || sha256(Buffer.from(requestPayload)) !== admission.bindings.userRequestSha256) {
    fail("Pixel Builder request differs from its admission");
  }
  let objective;
  try { objective = new TextDecoder("utf-8", { fatal: true }).decode(requestPayload).trim(); } catch { fail("Pixel Builder request is not UTF-8"); }
  if (!objective || objective.length > 8000) fail("Pixel Builder objective is empty or oversized");
  const workspaceVerification = verifierDefinition?.workspaceVerification;
  if (!workspaceVerification || !Array.isArray(verifierDefinition.acceptanceCriteria)) {
    fail("Pixel Builder has no controller-selected semantic workspace verifier");
  }
  verifyModelBinding(modelContract, inferenceContract, workPolicy, { requirePrepared: policyReadiness === "ready" });
  verifyEnvironmentBinding(environment, workspaceVerification, toolPolicy, workPolicy);
  const profile = workPolicy.profiles.builder;
  const tools = exactBuilderTools(toolPolicy, profile);
  const services = exactBuilderServices(toolPolicy, profile);
  utc(task.createdAt, "Pixel Builder task creation time");
  const createdAt = utc(now.toISOString(), "Pixel Builder work-request creation time");
  const epoch = String(Date.parse(createdAt)).padStart(13, "0");
  const jobId = `work-${epoch}-${suffix(idSuffix)}`;
  const classification = CLASSIFICATION[task.dataClass];
  if (!classification) fail("Pixel Builder data classification is unsupported");
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt, requester: "pixel", profile: "builder", objective,
    acceptanceCriteria: [...verifierDefinition.acceptanceCriteria], verification: structuredClone(workspaceVerification),
    dataClassification: classification,
    inputs: [{
      id: "source", kind: "repository-snapshot", mountMode: "read-only",
      contentSha256: sourceReference.sha256, maxBytes: sourceReference.bytes, classification,
    }],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools,
      network: { mode: "brokered", services }, modelRoute: "local-only",
      hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: workBudgets(admission, environment),
    outputs: { mode: "patch", requiredKinds: [...profile.outputKinds] }, boundary: JOB_BOUNDARY,
  };
  const entries = [{
    id: "source", kind: "repository-snapshot", objectName: `${sourceReference.sha256}.tar`,
    contentSha256: sourceReference.sha256, bytes: sourceReference.bytes,
    classification, mountMode: "read-only",
  }];
  return { request, entries, compileAt: now, compileSuffix: idSuffix };
}

export function buildPixelBuilderJob(options, configuration = {}) {
  return buildPixelBuilderChild(options, { ...configuration, admittedProfile: "builder" });
}

export function buildPixelControllerGoal(options, { policyReadiness = "ready" } = {}) {
  const built = buildPixelBuilderChild(options, { policyReadiness, admittedProfile: "controller" });
  const epoch = String(Date.parse(built.request.createdAt)).padStart(13, "0");
  const goalSuffix = sha256(`controller-goal:${built.compileSuffix}`).slice(0, 12);
  const goal = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-v1.schema.json", schemaVersion: 1,
    goalId: `workgoal-${epoch}-${goalSuffix}`, createdAt: built.request.createdAt, requester: "pixel",
    objective: built.request.objective, dataClassification: built.request.dataClassification,
    milestones: [{
      milestoneId: "primary", jobId: built.request.jobId, jobSha256: goalSha256(built.request),
      profile: "builder", dependsOn: [],
    }],
    budgets: {
      maxJobs: 1, maxRuntimeSeconds: built.request.budgets.maxRuntimeSeconds,
      maxModelRequests: built.request.budgets.maxModelRequests,
      maxInputTokens: built.request.budgets.maxInputTokens,
      maxOutputTokens: built.request.budgets.maxOutputTokens,
      maxNetworkBytes: built.request.budgets.maxNetworkBytes,
      maxArtifactBytes: built.request.budgets.maxArtifactBytes,
      maxFailures: built.request.budgets.maxFailures,
    },
    completion: { mode: "all-milestones-verified", independentVerificationRequired: true, workerClaimSufficient: false },
    authority: { grantsExecution: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false },
    boundary: CONTROLLER_GOAL_BOUNDARY,
  };
  return { ...built, goal, jobs: [built.request] };
}

function boundedResearch(profile, environment) {
  const ceiling = profile.maxResearch;
  const maxQueries = Math.min(20, ceiling.maxQueries, environment.limits.maxToolCalls);
  const maxResultsPerQuery = Math.min(10, ceiling.maxResultsPerQuery);
  const maxSources = Math.min(ceiling.maxSources, maxQueries * maxResultsPerQuery);
  const maxSourceBytes = Math.min(2097152, ceiling.maxSourceBytes, environment.limits.maxNetworkBytes);
  const maxTotalSourceBytes = Math.min(67108864, ceiling.maxTotalSourceBytes, environment.limits.maxNetworkBytes);
  if (maxQueries < 1 || maxResultsPerQuery < 1 || maxSources < 1 || maxSourceBytes < 1024 || maxTotalSourceBytes < maxSourceBytes) {
    fail("neutral environment cannot fit one bounded Researcher retrieval");
  }
  return {
    mode: "public-web", queryPolicy: "public-sanitized", maxQueries, maxResultsPerQuery, maxSources,
    maxSourceBytes, maxTotalSourceBytes, safeSearch: "strict", allowedDomains: [], deniedDomains: [],
    sourceTypes: [...ceiling.allowedSourceTypes], citationVerification: true, retention: "job-only",
    boundary: "Public sanitized read-only research only. Queries and sources are untrusted data and grant no credential, direct-network, external-action, publication, purchase, policy, or scope authority.",
  };
}

export function buildPixelResearcherJob({
  admission, task, requestPayload, sourceReference, environment, toolPolicy, verifierDefinition,
  modelContract, inferenceContract, workPolicy, now, idSuffix,
}, { policyReadiness = "ready" } = {}) {
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime())) fail("Pixel Researcher clock is invalid");
  if (admission?.status !== "admitted-inert" || admission.profile !== "researcher" || task?.profile !== "researcher") {
    fail("Pixel Researcher arm requires a formally admitted Researcher task");
  }
  if (admission.comparisonLane !== "same-model-harness" || task.comparisonLane !== "same-model-harness") {
    fail("Pixel Researcher arm requires the same-model-harness lane");
  }
  const profile = workPolicy?.profiles?.researcher;
  if (policyReadiness === "ready") {
    if (!workPolicy?.enabled || !workPolicy.runner?.prepared || !workPolicy.localModel?.prepared || !profile?.enabled || !profile.backend?.prepared) fail("Pixel private Researcher policy is disabled or unprepared");
  } else if (policyReadiness === "planned") {
    if (!workPolicy?.runner?.prepared || !profile?.enabled || !profile.backend?.prepared) fail("Pixel private Researcher planned envelope is disabled or runner-unprepared");
  } else fail("Pixel Researcher policy readiness mode is invalid");
  if (task.dataClass !== "public") fail("Pixel Researcher accepts only public comparison data");
  if (
    sourceReference?.mediaType !== "application/x-tar"
    || sourceReference.sha256 !== admission.bindings.sourceSnapshotSha256
    || !Number.isSafeInteger(sourceReference.bytes) || sourceReference.bytes < 1
  ) fail("Pixel Researcher source is not the exact admitted tar snapshot");
  if (!(requestPayload instanceof Uint8Array) || requestPayload.length < 1 || sha256(Buffer.from(requestPayload)) !== admission.bindings.userRequestSha256) {
    fail("Pixel Researcher request differs from its admission");
  }
  let objective;
  try { objective = new TextDecoder("utf-8", { fatal: true }).decode(requestPayload).trim(); } catch { fail("Pixel Researcher request is not UTF-8"); }
  if (!objective || objective.length > 8000) fail("Pixel Researcher objective is empty or oversized");
  if (!Array.isArray(verifierDefinition?.acceptanceCriteria) || verifierDefinition.acceptanceCriteria.length < 1) {
    fail("Pixel Researcher has no controller-selected acceptance criteria");
  }
  verifyModelBinding(modelContract, inferenceContract, workPolicy, { requirePrepared: policyReadiness === "ready" });
  verifyEnvironmentBinding(environment, null, toolPolicy, workPolicy);
  const tools = exactResearcherTools(toolPolicy, profile);
  const services = exactResearcherServices(toolPolicy, profile);
  utc(task.createdAt, "Pixel Researcher task creation time");
  const createdAt = utc(now.toISOString(), "Pixel Researcher work-request creation time");
  const epoch = String(Date.parse(createdAt)).padStart(13, "0");
  const jobId = `work-${epoch}-${suffix(idSuffix)}`;
  const request = {
    $schema: "https://osmantic.com/pixel/schemas/work-job-v1.schema.json", schemaVersion: 1,
    jobId, createdAt, requester: "pixel", profile: "researcher", objective,
    acceptanceCriteria: [...verifierDefinition.acceptanceCriteria], dataClassification: "public",
    inputs: [{
      id: "source", kind: "context", mountMode: "read-only",
      contentSha256: sourceReference.sha256, maxBytes: sourceReference.bytes, classification: "public",
    }],
    requestedCapabilities: {
      filesystem: "disposable-read-write", tools, network: { mode: "brokered", services },
      modelRoute: "local-only", hostAccess: false, ambientCredentials: false, externalEffects: false,
      mergeAuthority: false, deployAuthority: false, policyMutation: false,
    },
    budgets: workBudgets(admission, environment), outputs: { mode: "artifacts", requiredKinds: [...profile.outputKinds] },
    research: boundedResearch(profile, environment), boundary: JOB_BOUNDARY,
  };
  const entries = [{
    id: "source", kind: "context", objectName: `${sourceReference.sha256}.tar`,
    contentSha256: sourceReference.sha256, bytes: sourceReference.bytes,
    classification: "public", mountMode: "read-only",
  }];
  return { request, entries, compileAt: now, compileSuffix: idSuffix };
}

export function reviewPixelProductCompatibility(options) {
  const selectedProfile = options?.admission?.profile;
  if (selectedProfile === "assistant") {
    const built = buildPixelAssistantExecution(options);
    return Object.freeze({
      schemaVersion: 1, operation: "pixel-portal-outcome-task-compatibility", profile: selectedProfile,
      taskAdmissionSha256: sha256(options.admission), workRequestSha256: sha256(built.request),
      workPlanSha256: sha256(built.plan), budgetsSha256: sha256(built.request.budgets),
      capabilitiesSha256: sha256({ tools: built.request.tools, services: built.request.services, workspace: built.request.workspace }),
      boundary: TASK_COMPATIBILITY_BOUNDARY,
    });
  }
  const built = selectedProfile === "builder" ? buildPixelBuilderJob(options)
    : selectedProfile === "controller" ? buildPixelControllerGoal(options)
    : selectedProfile === "researcher" ? buildPixelResearcherJob(options)
      : fail(`Pixel product compatibility has no implemented adapter for ${selectedProfile ?? "unknown"}`);
  const compiled = ["builder", "controller"].includes(selectedProfile)
    ? compileBuilder(built.request, options.workPolicy, built.entries, { now: built.compileAt, suffix: built.compileSuffix })
    : compileResearcher(built.request, options.workPolicy, built.entries, { now: built.compileAt, suffix: built.compileSuffix });
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-portal-outcome-task-compatibility",
    profile: selectedProfile,
    taskAdmissionSha256: sha256(options.admission),
    workRequestSha256: sha256(selectedProfile === "controller" ? { goal: built.goal, jobs: built.jobs } : built.request),
    workPlanSha256: sha256(selectedProfile === "controller" ? { goal: built.goal, childPlan: compiled.plan } : compiled.plan),
    budgetsSha256: sha256(built.request.budgets),
    capabilitiesSha256: sha256(built.request.requestedCapabilities),
    boundary: TASK_COMPATIBILITY_BOUNDARY,
  });
}

export function reviewPixelPlannedCompatibility(options) {
  const selectedProfile = options?.admission?.profile;
  if (selectedProfile === "assistant") {
    const built = buildPixelAssistantExecution(options, { policyReadiness: "planned" });
    return Object.freeze({
      schemaVersion: 1, operation: "pixel-portal-outcome-planned-task-compatibility", profile: selectedProfile,
      readiness: options.workPolicy.enabled && options.workPolicy.localModel.prepared ? "policy-ready" : "qualification-required",
      taskAdmissionSha256: sha256(options.admission), workRequestSha256: sha256(built.request),
      workPolicySha256: sha256(options.workPolicy), inputSetSha256: sha256(built.request.source),
      budgetsSha256: sha256(built.request.budgets),
      capabilitiesSha256: sha256({ tools: built.request.tools, services: built.request.services, workspace: built.request.workspace }),
      outputsSha256: sha256({ mode: "portal-turn", verifierSha256: built.request.verifierSha256 }),
      boundary: PLANNED_TASK_COMPATIBILITY_BOUNDARY,
    });
  }
  const built = selectedProfile === "builder" ? buildPixelBuilderJob(options, { policyReadiness: "planned" })
    : selectedProfile === "controller" ? buildPixelControllerGoal(options, { policyReadiness: "planned" })
    : selectedProfile === "researcher" ? buildPixelResearcherJob(options, { policyReadiness: "planned" })
      : fail(`Pixel planned compatibility has no implemented adapter for ${selectedProfile ?? "unknown"}`);
  const policyProfile = selectedProfile === "controller" ? "builder" : selectedProfile;
  const reviewed = reviewWorkJobAgainstPlannedPolicy(options.workPolicy, built.request, built.entries, policyProfile);
  return Object.freeze({
    schemaVersion: 1,
    operation: "pixel-portal-outcome-planned-task-compatibility",
    profile: selectedProfile,
    readiness: options.workPolicy.enabled && options.workPolicy.localModel.prepared ? "policy-ready" : "qualification-required",
    taskAdmissionSha256: sha256(options.admission),
    workRequestSha256: selectedProfile === "controller" ? sha256({ goal: built.goal, jobs: built.jobs, requestSha256: reviewed.requestSha256 }) : reviewed.requestSha256,
    workPolicySha256: reviewed.policySha256,
    inputSetSha256: reviewed.inputSetSha256,
    budgetsSha256: reviewed.budgetsSha256,
    capabilitiesSha256: reviewed.capabilitiesSha256,
    outputsSha256: reviewed.outputsSha256,
    boundary: PLANNED_TASK_COMPATIBILITY_BOUNDARY,
  });
}

async function collectResult(result, fileReader, plan, latencyMs, toolCalls, profile = "builder") {
  const candidate = result?.candidate;
  const verification = result?.verification;
  const terminalStates = { completed: "completed", "no-progress": "no-progress", "budget-exhausted": "budget-exhausted" };
  if (
    !Object.hasOwn(terminalStates, result?.action) || result?.checkpoint?.state !== terminalStates[result.action]
    || !candidate?.patch?.path || !candidate?.evidence?.path || !verification?.artifact?.path
  ) {
    fail("Pixel Builder result is missing retained artifacts");
  }
  const verificationErrors = validatePlanVerificationEvidence(plan, verification.evidence);
  if (verificationErrors.length) fail(`Pixel Builder verifier evidence is invalid: ${verificationErrors[0]}`);
  const [patch, workerEvidence, verifierEvidence] = await Promise.all([
    fileReader(candidate.patch.path), fileReader(candidate.evidence.path), fileReader(verification.artifact.path),
  ]);
  for (const [payload, record, label] of [
    [patch, candidate.patch, "patch"], [workerEvidence, candidate.evidence, "worker evidence"],
    [verifierEvidence, verification.artifact, "verifier evidence"],
  ]) {
    if (payload.length !== record.bytes || sha256(payload) !== record.sha256) fail(`Pixel Builder ${label} changed after execution`);
  }
  const usage = result.checkpoint.usage;
  for (const field of ["modelRequests", "inputTokens", "outputTokens", "networkBytes"]) integer(usage?.[field], 0, 1000000000000, `Pixel Builder cumulative ${field}`);
  integer(toolCalls, 0, 1000000000, "Pixel Builder cumulative tool calls");
  if (
    usage.modelRequests < candidate.proxyReceipt.modelRequests || usage.inputTokens < candidate.proxyReceipt.inputTokens
    || usage.outputTokens < candidate.proxyReceipt.outputTokens || usage.networkBytes < candidate.proxyReceipt.networkBytes
  ) fail("Pixel Builder cumulative usage is smaller than its terminal candidate");
  const report = Buffer.from(candidate.text ?? "", "utf8");
  const artifacts = [
    { kind: "patch", relativePath: "pixel-work-patch.json", payload: patch },
    { kind: "test-evidence", relativePath: "pixel-work-evidence.json", payload: workerEvidence },
    { kind: "test-evidence", relativePath: "pixel-independent-verification.json", payload: verifierEvidence },
  ];
  if (report.length) artifacts.unshift({ kind: "finding-report", relativePath: "artifact.md", payload: report });
  return {
    exitCode: result.action === "completed" ? 0 : 2, latencyMs,
    finalMessage: candidate.text ?? "",
    artifacts, independentVerification: structuredClone(verification.evidence),
    usage: {
      modelRequests: usage.modelRequests, inputTokens: usage.inputTokens,
      outputTokens: usage.outputTokens, networkBytes: usage.networkBytes, toolCalls,
    },
    authority: { sourceMutation: false, merge: false, deploy: false, externalEffects: false },
    interaction: interactionReceipt(profile, profile === "controller" ? "pixel-controller-ledger-v1" : "pixel-builder-checkpoint-v1", {
      action: result.action,
      checkpointState: result.checkpoint.state,
      verificationStatus: verification.evidence.status,
      toolCalls,
    }),
  };
}

function decodePublicText(value, label) {
  if (typeof value !== "string" || value.length > 87384 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(value)) fail(`Pixel Researcher ${label} is not bounded base64`);
  const payload = Buffer.from(value, "base64");
  if (!payload.length || payload.toString("base64") !== value) fail(`Pixel Researcher ${label} is not canonical base64`);
  try { return new TextDecoder("utf-8", { fatal: true }).decode(payload); } catch { fail(`Pixel Researcher ${label} is not UTF-8`); }
}

export function researchProvenance(result, reportArtifactSha256, verificationArtifactSha256, format = "pixel-research-provenance-v1") {
  if (!new Set(["pixel-research-provenance-v1", "codex-research-provenance-v1"]).has(format)) fail("Researcher provenance format is invalid");
  if (!Array.isArray(result?.batches) || result.batches.length < 1 || result.batches.length > 200) fail("Pixel Researcher retained batch inventory is invalid");
  const batchSha256s = [], batchRecords = [], sourceIndex = new Map();
  let latestBatchTime = -1;
  for (const [batchIndex, batch] of result.batches.entries()) {
    const errors = validateWorkResearchBatch(batch);
    if (errors.length) fail(`Pixel Researcher batch evidence is invalid: ${errors[0]}`);
    const batchSha256 = sha256(batch), createdTime = Date.parse(batch.createdAt);
    if (!Number.isSafeInteger(createdTime) || createdTime <= latestBatchTime || batchSha256s.includes(batchSha256)) fail("Pixel Researcher batch order or identity is invalid");
    latestBatchTime = createdTime; batchSha256s.push(batchSha256);
    const sources = batch.sources.map((source) => {
      const retrieval = source.retrieval;
      const fetched = retrieval.status === "fetched";
      const record = {
        sourceId: source.sourceId, rank: source.rank, sourceType: source.sourceType,
        canonicalUrlSha256: sha256(source.canonicalUrl), domainSha256: sha256(source.domain),
        retrievalStatus: retrieval.status,
        retrievalTransport: retrieval.status === "metadata-only" ? null : retrieval.transport,
        retrievedAt: fetched ? retrieval.retrievedAt : null,
        contentSha256: fetched ? retrieval.contentSha256 : null,
        receiptSha256: fetched ? retrieval.receiptSha256 : null,
      };
      const key = `${batchSha256}:${source.sourceId}`;
      if (sourceIndex.has(key)) fail("Pixel Researcher source provenance is duplicated");
      sourceIndex.set(key, record);
      return record;
    });
    batchRecords.push({
      batchSha256, createdAt: batch.createdAt, adapter: batch.adapter,
      normalizedQuerySha256: batch.normalizedQuerySha256, sources,
    });
  }
  if (!same(batchSha256s, result.report.batchSha256s) || !same(batchSha256s, result.verification.batchSha256s)) {
    fail("Pixel Researcher report or verification differs from retained broker batches");
  }
  const citationBindings = [];
  for (const finding of result.verification.findings) {
    for (const citation of finding.citations) {
      const source = sourceIndex.get(`${citation.batchSha256}:${citation.sourceId}`);
      if (!source || source.retrievalStatus !== "fetched" || source.contentSha256 !== citation.contentSha256 || source.receiptSha256 !== citation.receiptSha256) {
        fail("Pixel Researcher citation differs from retained source provenance");
      }
      citationBindings.push({
        findingId: finding.findingId, batchSha256: citation.batchSha256, sourceId: citation.sourceId,
        contentSha256: citation.contentSha256, receiptSha256: citation.receiptSha256,
        evidenceSha256: citation.evidenceSha256, status: citation.status,
      });
    }
  }
  return Object.freeze({
    schemaVersion: 1, format,
    reportArtifactSha256, verificationArtifactSha256,
    reportCreatedAt: result.report.createdAt, verificationCreatedAt: result.verification.createdAt,
    latestBatchCreatedAt: batchRecords.at(-1).createdAt, batches: batchRecords, citationBindings,
    primarySourcePreferenceVerified: false, freshnessLabelsVerified: false,
    semanticEntailmentVerified: false, privateDataIncluded: false, externalEffects: false,
    boundary: RESEARCH_PROVENANCE_BOUNDARY,
  });
}

async function collectResearcherResult(result, fileReader, latencyMs, bindings = {}) {
  if (!result?.cleanupComplete || !result?.artifacts?.report?.path || !result?.artifacts?.verification?.path || !result?.artifacts?.evidence?.path) {
    fail("Pixel Researcher result is missing cleanup proof or retained artifacts");
  }
  const reportErrors = validateWorkResearchReport(result.report), verificationErrors = validateWorkResearchVerification(result.verification);
  if (reportErrors.length || verificationErrors.length) fail(`Pixel Researcher evidence is invalid: ${reportErrors[0] ?? verificationErrors[0]}`);
  if (
    result.verification.status !== "evidence-pass" || result.verification.independent !== true
    || result.verification.network !== "none" || result.verification.modelUsed !== false
    || result.verification.externalEffects !== false || result.report.externalEffects !== false
    || result.report.authority?.publish !== false
  ) fail("Pixel Researcher independent evidence or authority boundary failed");
  const [report, verification, workerEvidence] = await Promise.all([
    fileReader(result.artifacts.report.path), fileReader(result.artifacts.verification.path), fileReader(result.artifacts.evidence.path),
  ]);
  for (const [payload, record, label] of [
    [report, result.artifacts.report, "report"], [verification, result.artifacts.verification, "verification"],
    [workerEvidence, result.artifacts.evidence, "worker evidence"],
  ]) if (payload.length !== record.bytes || sha256(payload) !== record.sha256) fail(`Pixel Researcher ${label} changed after execution`);
  let retainedReport, retainedVerification;
  try {
    retainedReport = parseStrictJson(new TextDecoder("utf-8", { fatal: true }).decode(report), "Pixel Researcher retained report");
    retainedVerification = parseStrictJson(new TextDecoder("utf-8", { fatal: true }).decode(verification), "Pixel Researcher retained verification");
  } catch { fail("Pixel Researcher retained report or verification is not strict UTF-8 JSON"); }
  if (!same(retainedReport, result.report) || !same(retainedVerification, result.verification)) {
    fail("Pixel Researcher retained evidence differs from the validated lifecycle result");
  }
  let revisionReviewArtifact = null, revisionHistoryArtifact = null;
  if (bindings.requireRevisionReview === true) {
    if (!result?.revisionReview || !result?.revisionHistory || !result?.artifacts?.revisionReview?.path || !result?.artifacts?.revisionHistory?.path) fail("Pixel Researcher required revision review or append-only history is absent");
    const schemaErrors = validateWorkResearchRevisionReview(result.revisionReview), consistencyErrors = validateResearchRevisionReview(result.revisionReview);
    if (schemaErrors.length || consistencyErrors.length) fail(`Pixel Researcher revision review is invalid: ${schemaErrors[0] ?? consistencyErrors[0]}`);
    if (
      result.revisionReview.reportSha256 !== sha256(result.report)
      || result.revisionReview.deterministicVerificationSha256 !== sha256(result.verification)
      || result.revisionReview.modelContractSha256 !== bindings.modelContractSha256
      || result.revisionReview.inferenceContractSha256 !== bindings.inferenceContractSha256
      || result.revisionReview.consensus.independentlyScored !== false
      || result.revisionReview.consensus.backendOutputUsedAsScore !== false
      || result.revisionReview.authority.grantsScore !== false
      || result.revisionReview.authority.grantsCompletion !== false
    ) fail("Pixel Researcher revision review differs from its exact candidate or scoring boundary");
    const payload = await fileReader(result.artifacts.revisionReview.path);
    if (payload.length !== result.artifacts.revisionReview.bytes || sha256(payload) !== result.artifacts.revisionReview.sha256) fail("Pixel Researcher revision review changed after execution");
    let retained;
    try { retained = parseStrictJson(new TextDecoder("utf-8", { fatal: true }).decode(payload), "Pixel Researcher retained revision review"); }
    catch { fail("Pixel Researcher retained revision review is not strict UTF-8 JSON"); }
    if (!same(retained, result.revisionReview)) fail("Pixel Researcher retained revision review differs from its lifecycle result");
    revisionReviewArtifact = { kind: "test-evidence", relativePath: "pixel-research-revision-review.json", payload };
    const historyErrors = validateWorkResearchRevisionHistory(result.revisionHistory);
    if (historyErrors.length) fail(`Pixel Researcher revision history is invalid: ${historyErrors[0]}`);
    const finalAttempt = result.revisionHistory.attempts.at(-1);
    if (!same(finalAttempt.report, result.report) || !same(finalAttempt.verification, result.verification) || !same(finalAttempt.review, result.revisionReview)
      || result.revisionHistory.finalReportSha256 !== sha256(result.report)
      || result.revisionHistory.finalVerificationSha256 !== sha256(result.verification)
      || result.revisionHistory.finalReviewSha256 !== sha256(result.revisionReview)
      || result.revisionHistory.independentlyScored !== false || result.revisionHistory.backendOutputUsedAsScore !== false
      || result.revisionHistory.authority.grantsScore !== false || result.revisionHistory.authority.grantsCompletion !== false) fail("Pixel Researcher revision history differs from its terminal candidate or scoring boundary");
    for (const [index, attempt] of result.revisionHistory.attempts.entries()) {
      const attemptReviewErrors = validateResearchRevisionReview(attempt.review);
      if (attemptReviewErrors.length || attempt.review.modelContractSha256 !== bindings.modelContractSha256 || attempt.review.inferenceContractSha256 !== bindings.inferenceContractSha256) fail(`Pixel Researcher revision history attempt ${index} is inconsistent or used another model contract`);
    }
    const historyPayload = await fileReader(result.artifacts.revisionHistory.path);
    if (historyPayload.length !== result.artifacts.revisionHistory.bytes || sha256(historyPayload) !== result.artifacts.revisionHistory.sha256) fail("Pixel Researcher revision history changed after execution");
    let retainedHistory;
    try { retainedHistory = parseStrictJson(new TextDecoder("utf-8", { fatal: true }).decode(historyPayload), "Pixel Researcher retained revision history"); }
    catch { fail("Pixel Researcher retained revision history is not strict UTF-8 JSON"); }
    if (!same(retainedHistory, result.revisionHistory)) fail("Pixel Researcher retained revision history differs from its lifecycle result");
    revisionHistoryArtifact = { kind: "test-evidence", relativePath: "pixel-research-revision-history.json", payload: historyPayload };
  } else if (result?.revisionReview || result?.revisionHistory || result?.artifacts?.revisionReview || result?.artifacts?.revisionHistory) fail("Pixel Researcher returned unbound revision evidence");
  const title = decodePublicText(result.report.titleBase64, "title");
  const statements = result.report.findings.map((finding, index) => {
    const statement = decodePublicText(finding.statementBase64, `finding ${index}`);
    return `- ${statement} [${finding.citations.length} citation${finding.citations.length === 1 ? "" : "s"}]`;
  });
  const limitations = decodePublicText(result.report.limitationsBase64, "limitations");
  const finalMessage = `# ${title}\n\n${statements.join("\n")}\n\nLimitations: ${limitations}`;
  const researchEvidence = researchProvenance(result, result.artifacts.report.sha256, result.artifacts.verification.sha256);
  return {
    exitCode: 0, latencyMs, finalMessage,
    artifacts: [
      { kind: "finding-report", relativePath: "pixel-research-report.json", payload: report },
      { kind: "test-evidence", relativePath: "pixel-research-verification.json", payload: verification },
      { kind: "test-evidence", relativePath: "pixel-research-evidence.json", payload: workerEvidence },
      ...(revisionReviewArtifact ? [revisionReviewArtifact] : []),
      ...(revisionHistoryArtifact ? [revisionHistoryArtifact] : []),
    ],
    independentVerification: structuredClone(result.verification),
    researchEvidence, ...(revisionReviewArtifact ? { researchRevisionReview: structuredClone(result.revisionReview), researchRevisionHistory: structuredClone(result.revisionHistory) } : {}),
    usage: {
      modelRequests: result.proxyReceipt.modelRequests, inputTokens: result.proxyReceipt.inputTokens,
      outputTokens: result.proxyReceipt.outputTokens, networkBytes: result.proxyReceipt.networkBytes,
      toolCalls: result.execution.toolCalls,
    },
    authority: { sourceMutation: false, merge: false, deploy: false, externalEffects: false },
    interaction: interactionReceipt("researcher", "pixel-researcher-lifecycle-v1", {
      cleanupComplete: result.cleanupComplete,
      verificationStatus: result.verification.status,
      batches: result.batches.length,
      toolCalls: result.execution.toolCalls,
    }),
  };
}

export async function runPixelBuilderArm(options, dependencies = {}) {
  const compile = dependencies.compileBuilder ?? compileBuilder;
  const prepare = dependencies.prepareBuilderRun ?? prepareBuilderRun;
  const initialize = dependencies.initializeCheckpointLedger ?? initializeCheckpointLedger;
  const iterate = dependencies.executeBuilderIteration ?? executeBuilderIteration;
  const discard = dependencies.discardPreparedRun ?? discardPreparedRun;
  const fileReader = dependencies.readFile ?? readFile;
  const monotonic = dependencies.monotonic ?? (() => performance.now());
  if (typeof monotonic !== "function") fail("Pixel arm monotonic clock is unavailable");
  const started = monotonic();
  if (!Number.isFinite(started) || started < 0) fail("Pixel arm monotonic clock is invalid");
  const built = buildPixelBuilderJob(options);
  const compiled = compile(built.request, options.workPolicy, built.entries, {
    now: options.now, suffix: built.compileSuffix,
  });
  let lease = compiled.lease, continuation, terminal, cumulativeToolCalls = 0, initialized = false;
  for (let iteration = 1; iteration <= compiled.plan.budgets.maxIterations; iteration += 1) {
    let prepared;
    try {
      const prepareAt = continuation === undefined ? options.now : new Date(Date.parse(lease.issuedAt) + 1);
      prepared = await prepare({
        ...compiled, lease, policy: options.workPolicy, objectStore: options.objectStore,
        workspaceRoot: options.workspaceRoot, executorPath: options.executorPath,
        archiveLimits: options.archiveLimits, now: prepareAt,
        ...(continuation === undefined ? {} : { continuation }),
      });
      if (!initialized) {
        await initialize({
          stateRoot: options.stateRoot, plan: prepared.plan, lease: prepared.lease,
          workspaceSnapshotSha256: prepared.workspace.sha256,
          now: new Date(options.now.getTime() + 1), suffix: built.compileSuffix,
        });
        initialized = true;
      }
      const result = await iterate({
        stateRoot: options.stateRoot, prepared, lifecycleOptions: options.lifecycleOptions,
      });
      integer(result?.candidate?.toolCalls, 0, 1000000000, "Pixel Builder iteration tool calls");
      cumulativeToolCalls += result.candidate.toolCalls;
      if (result.action !== "continue") {
        terminal = result;
        break;
      }
      if (
        !result.nextLease?.lease || result.nextLease.lease.iteration !== prepared.lease.iteration + 1
        || !result.claim || !result.checkpoint || !result.candidate?.patch?.path
      ) fail("Pixel Builder continuation result is incomplete");
      continuation = {
        previousLease: prepared.lease, previousConsumption: result.claim,
        checkpoint: result.checkpoint, patchPath: result.candidate.patch.path,
      };
      lease = result.nextLease.lease;
    } finally {
      if (prepared) await discard(prepared);
    }
  }
  if (!terminal) fail("Pixel Builder exhausted its iteration ceiling without a terminal controller decision");
  const finished = monotonic();
  if (!Number.isFinite(finished) || finished < started) fail("Pixel arm monotonic clock moved backwards");
  return await collectResult(terminal, fileReader, compiled.plan, Math.ceil(finished - started), cumulativeToolCalls);
}

function controllerEvidence({ options, built, compiled, parent, child, custody, terminal, latencyMs, toolCalls, preparedRunsDiscarded }) {
  const custodyRecords = custody?.bundles;
  const expectedCustodyRecords = child?.head?.iteration + 1;
  if (!Array.isArray(custodyRecords) || !Number.isSafeInteger(expectedCustodyRecords) || expectedCustodyRecords < 2
    || custodyRecords.length !== expectedCustodyRecords) {
    fail("Pixel Controller durable run custody record count is invalid");
  }
  for (let index = 0; index < custodyRecords.length; index += 1) {
    const bundle = custodyRecords[index];
    const previous = index === 0 ? null : custodyRecords[index - 1];
    const expectedPurpose = index === 0 ? "initial" : index === 1 ? "admission" : "continuation";
    const expectedLeaseIteration = index === 0 ? 1 : index;
    if (
      bundle.sequence !== index || bundle.purpose !== expectedPurpose
      || bundle.previousBundleSha256 !== (previous === null ? null : sha256(previous))
      || bundle.goalId !== built.goal.goalId || bundle.jobId !== built.request.jobId
      || bundle.goalSha256 !== goalSha256(built.goal) || bundle.jobSha256 !== goalSha256(built.request)
      || bundle.plan.jobId !== built.request.jobId || bundle.lease.iteration !== expectedLeaseIteration
      || bundle.authority.containsExactLease !== true || bundle.authority.grantsBeyondEmbeddedLease !== false
      || bundle.authority.grantsReplay !== false || bundle.authority.grantsScopeExpansion !== false
      || bundle.authority.grantsExternalEffects !== false || bundle.authority.grantsCompletion !== false
    ) fail("Pixel Controller durable run custody lineage is invalid");
  }
  if (
    parent.head.state !== terminal.checkpoint.state
    || parent.head.progress.milestonesTotal !== 1 || parent.head.progress.milestonesCompleted !== (parent.head.state === "completed" ? 1 : 0)
    || parent.head.active !== null || child.headSha256 !== sha256(terminal.checkpoint)
    || custody.head.plan.jobId !== built.request.jobId || custody.headSha256 !== sha256(custody.head)
    || custody.headSha256 !== sha256(custodyRecords.at(-1))
  ) fail("Pixel Controller terminal evidence differs from durable parent, child, or run custody");
  const verification = terminal.verification?.evidence;
  if (!verification || verification.status !== (terminal.action === "completed" ? "pass" : "fail")) {
    fail("Pixel Controller terminal independent verification is missing or inconsistent");
  }
  const lineage = parent.checkpoints.map((checkpoint) => ({
    sequence: checkpoint.sequence, state: checkpoint.state,
    checkpointSha256: goalCheckpointSha256(checkpoint), previousCheckpointSha256: checkpoint.previousCheckpointSha256,
  }));
  const custodyLineage = custodyRecords.map((bundle) => ({
    sequence: bundle.sequence, purpose: bundle.purpose, bundleSha256: sha256(bundle),
    previousBundleSha256: bundle.previousBundleSha256, leaseIteration: bundle.lease.iteration,
  }));
  return Object.freeze({
    schemaVersion: 1, format: "pixel-controller-evidence-v1", status: terminal.action === "completed" ? "pass" : "fail",
    goalId: built.goal.goalId, goalSha256: goalSha256(built.goal), objectiveSha256: sha256(built.goal.objective),
    sourceSnapshotSha256: options.sourceReference.sha256,
    modelContractSha256: options.modelContractSha256 ?? sha256(options.modelContract),
    inferenceContractSha256: options.inferenceContractSha256 ?? sha256(options.inferenceContract),
    parent: {
      state: parent.head.state, checkpointCount: parent.checkpoints.length, headCheckpointSha256: parent.headSha256,
      milestonesTotal: parent.head.progress.milestonesTotal, milestonesCompleted: parent.head.progress.milestonesCompleted,
      jobsStarted: parent.head.progress.jobsStarted, lineage,
    },
    child: {
      profile: "builder", jobId: built.request.jobId, jobSha256: goalSha256(built.request),
      planSha256: sha256(compiled.plan), state: child.head.state, checkpointCount: child.checkpoints.length,
      headCheckpointSha256: child.headSha256, iterations: child.head.iteration,
      verificationEvidenceSha256: child.head.verificationEvidenceSha256,
    },
    custody: {
      recordCount: custodyRecords.length, headBundleSha256: custody.headSha256,
      exactLeaseOnly: true,
      lineage: custodyLineage,
    },
    usage: { ...child.head.usage, toolCalls, latencyMs },
    independentVerification: {
      status: verification.status, evidenceSha256: sha256(verification), workerSelectedChecks: verification.workerSelectedChecks,
      externalEffects: verification.externalEffects,
    },
    cleanup: {
      preparedRunsDiscarded, expectedPreparedRuns: child.head.iteration,
      activeChild: false, ephemeralPreparationComplete: preparedRunsDiscarded === child.head.iteration,
    },
    privacy: { directNetwork: false, credentials: false, privateDataSentRemote: false, externalEffects: false },
    authority: {
      grantsExecution: false, grantsReplay: false, grantsLease: false, grantsScopeExpansion: false,
      grantsExternalEffects: false, grantsCompletion: false, grantsPublication: false, grantsDeployment: false,
      grantsAcceptance: false, grantsPromotion: false,
    },
    boundary: CONTROLLER_EVIDENCE_BOUNDARY,
  });
}

export async function runPixelControllerArm(options, dependencies = {}) {
  const compile = dependencies.compileBuilder ?? compileBuilder;
  const prepare = dependencies.prepareBuilderRun ?? prepareBuilderRun;
  const initializeChild = dependencies.initializeCheckpointLedger ?? initializeCheckpointLedger;
  const initializeParent = dependencies.initializeGoalLedger ?? initializeGoalLedger;
  const initializeCustody = dependencies.initializeGoalRunBundle ?? initializeGoalRunBundle;
  const admitCustody = dependencies.admitGoalRunBundle ?? admitGoalRunBundle;
  const appendContinuation = dependencies.appendContinuationGoalRunBundle ?? appendContinuationGoalRunBundle;
  const dispatch = dependencies.dispatchGoalMilestone ?? dispatchGoalMilestone;
  const observe = dependencies.observeGoalMilestone ?? observeGoalMilestone;
  const finishGoal = dependencies.completeGoal ?? completeGoal;
  const recoverParent = dependencies.recoverGoalLedger ?? recoverGoalLedger;
  const recoverChild = dependencies.recoverCheckpointLedger ?? recoverCheckpointLedger;
  const recoverCustody = dependencies.recoverGoalRunBundles ?? recoverGoalRunBundles;
  const iterate = dependencies.executeBuilderIteration ?? executeBuilderIteration;
  const discard = dependencies.discardPreparedRun ?? discardPreparedRun;
  const fileReader = dependencies.readFile ?? readFile;
  const monotonic = dependencies.monotonic ?? (() => performance.now());
  if (typeof monotonic !== "function") fail("Pixel Controller monotonic clock is unavailable");
  const started = monotonic();
  if (!Number.isFinite(started) || started < 0) fail("Pixel Controller monotonic clock is invalid");
  const built = buildPixelControllerGoal(options);
  const compiled = compile(built.request, options.workPolicy, built.entries, { now: built.compileAt, suffix: built.compileSuffix });
  let controllerTime = options.now.getTime();
  let controllerRecord = 0;
  const nextTime = (minimum = 0) => new Date(controllerTime = Math.max(controllerTime + 1, minimum));
  const nextSuffix = (label) => sha256(`${built.compileSuffix}:${label}:${controllerRecord++}`).slice(0, 12);
  let lease = compiled.lease, continuation, terminal, cumulativeToolCalls = 0, preparedRunsDiscarded = 0, initialized = false;
  for (let iteration = 1; iteration <= compiled.plan.budgets.maxIterations; iteration += 1) {
    let prepared;
    try {
      const prepareAt = continuation === undefined ? options.now : new Date(Math.max(Date.parse(lease.issuedAt), controllerTime) + 1);
      prepared = await prepare({
        ...compiled, lease, policy: options.workPolicy, objectStore: options.objectStore,
        workspaceRoot: options.workspaceRoot, executorPath: options.executorPath,
        archiveLimits: options.archiveLimits, now: prepareAt,
        ...(continuation === undefined ? {} : { continuation }),
      });
      if (!initialized) {
        await initializeCustody({
          stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs, jobId: built.request.jobId,
          plan: prepared.plan, lease: prepared.lease, workspaceSnapshotSha256: prepared.workspace.sha256,
          now: nextTime(), suffix: nextSuffix("custody-initialize"),
        });
        await admitCustody({
          stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs, jobId: built.request.jobId,
          now: nextTime(), suffix: nextSuffix("custody-admit"),
        });
        await initializeParent({
          stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs,
          now: nextTime(), suffix: nextSuffix("goal-initialize"),
        });
        await dispatch({
          stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs,
          now: nextTime(), suffix: nextSuffix("goal-dispatch"),
        });
        await initializeChild({
          stateRoot: options.stateRoot, plan: prepared.plan, lease: prepared.lease,
          workspaceSnapshotSha256: prepared.workspace.sha256,
          now: nextTime(), suffix: nextSuffix("child-initialize"),
        });
        initialized = true;
      }
      const result = await iterate({
        stateRoot: options.stateRoot, prepared, lifecycleOptions: options.lifecycleOptions,
      });
      integer(result?.candidate?.toolCalls, 0, 1000000000, "Pixel Controller child iteration tool calls");
      cumulativeToolCalls += result.candidate.toolCalls;
      await observe({
        stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs,
        childPlan: prepared.plan, childLease: prepared.lease,
        now: nextTime(), suffix: nextSuffix("goal-observe"),
      });
      if (result.action !== "continue") {
        terminal = result;
        if (result.action === "completed") {
          await finishGoal({
            stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs,
            now: nextTime(), suffix: nextSuffix("goal-complete"),
          });
        }
        break;
      }
      if (
        !result.nextLease?.lease || result.nextLease.lease.iteration !== prepared.lease.iteration + 1
        || !result.claim || !result.checkpoint || !result.candidate?.patch?.path
      ) fail("Pixel Controller Builder continuation result is incomplete");
      const nextLease = result.nextLease.lease;
      await appendContinuation({
        stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs, jobId: built.request.jobId,
        plan: prepared.plan, lease: nextLease, workspaceSnapshotSha256: prepared.workspace.sha256,
        now: nextTime(Date.parse(nextLease.issuedAt)), suffix: nextSuffix("custody-continuation"),
      });
      continuation = {
        previousLease: prepared.lease, previousConsumption: result.claim,
        checkpoint: result.checkpoint, patchPath: result.candidate.patch.path,
      };
      lease = nextLease;
    } finally {
      if (prepared) {
        await discard(prepared);
        preparedRunsDiscarded += 1;
      }
    }
  }
  if (!terminal) fail("Pixel Controller exhausted its child iteration ceiling without a terminal decision");
  const finished = monotonic();
  if (!Number.isFinite(finished) || finished < started) fail("Pixel Controller monotonic clock moved backwards");
  const latencyMs = Math.ceil(finished - started);
  const [parent, child, custody] = await Promise.all([
    recoverParent({ stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs }),
    recoverChild({ stateRoot: options.stateRoot, plan: compiled.plan, lease }),
    recoverCustody({ stateRoot: options.stateRoot, goal: built.goal, jobs: built.jobs, jobId: built.request.jobId }),
  ]);
  const evidence = controllerEvidence({
    options, built, compiled, parent, child, custody, terminal, latencyMs,
    toolCalls: cumulativeToolCalls, preparedRunsDiscarded,
  });
  const outcome = await collectResult(terminal, fileReader, compiled.plan, latencyMs, cumulativeToolCalls, "controller");
  outcome.artifacts.push({
    kind: "test-evidence", relativePath: "pixel-controller-evidence.json",
    payload: Buffer.from(`${JSON.stringify(evidence)}\n`, "utf8"),
  });
  outcome.controllerEvidence = evidence;
  return outcome;
}

export async function runPixelResearcherArm(options, dependencies = {}) {
  const compile = dependencies.compileResearcher ?? compileResearcher;
  const prepare = dependencies.prepareResearcherRun ?? prepareResearcherRun;
  const consume = dependencies.createLeaseConsumption ?? createLeaseConsumption;
  const claim = dependencies.claimLease ?? claimLease;
  const lifecycle = dependencies.runResearcherDockerLifecycle ?? runResearcherDockerLifecycle;
  const discard = dependencies.discardPreparedRun ?? discardPreparedRun;
  const fileReader = dependencies.readFile ?? readFile;
  const monotonic = dependencies.monotonic ?? (() => performance.now());
  if (typeof monotonic !== "function") fail("Pixel Researcher monotonic clock is unavailable");
  const started = monotonic();
  if (!Number.isFinite(started) || started < 0) fail("Pixel Researcher monotonic clock is invalid");
  const built = buildPixelResearcherJob(options);
  const compiled = compile(built.request, options.workPolicy, built.entries, { now: options.now, suffix: built.compileSuffix });
  let prepared;
  try {
    prepared = await prepare({
      ...compiled, policy: options.workPolicy, objectStore: options.objectStore,
      workspaceRoot: options.workspaceRoot, executorPath: options.executorPath,
      archiveLimits: options.archiveLimits, now: options.now,
    });
    const consumption = consume(prepared, { now: options.now, suffix: built.compileSuffix });
    await claim(options.stateRoot, consumption);
    const result = await lifecycle(prepared, consumption, options.lifecycleOptions);
    const finished = monotonic();
    if (!Number.isFinite(finished) || finished < started) fail("Pixel Researcher monotonic clock moved backwards");
    return await collectResearcherResult(result, fileReader, Math.ceil(finished - started), {
      requireRevisionReview: options.lifecycleOptions?.researchRevisionReview === true,
      modelContractSha256: sha256(options.modelContract), inferenceContractSha256: sha256(options.inferenceContract),
    });
  } finally {
    if (prepared) await discard(prepared);
  }
}

export async function runPixelProductArm(options, dependencies = {}) {
  if (options?.admission?.profile === "builder") return runPixelBuilderArm(options, dependencies);
  if (options?.admission?.profile === "controller") return runPixelControllerArm(options, dependencies);
  if (options?.admission?.profile === "researcher") return runPixelResearcherArm(options, dependencies);
  fail(`Pixel product arm has no implemented adapter for ${options?.admission?.profile ?? "unknown"}`);
}

export const pixelArmContract = Object.freeze({
  jobBoundary: JOB_BOUNDARY, toolMap: TOOL_MAP,
  researchProvenanceBoundary: RESEARCH_PROVENANCE_BOUNDARY,
  taskCompatibilityBoundary: TASK_COMPATIBILITY_BOUNDARY,
  plannedTaskCompatibilityBoundary: PLANNED_TASK_COMPATIBILITY_BOUNDARY,
  implementedProfiles: Object.freeze(["builder", "controller", "researcher"]),
  assistantPlanBoundary: ASSISTANT_PLAN_BOUNDARY,
  assistantToolMap: ASSISTANT_TOOL_MAP,
  controllerGoalBoundary: CONTROLLER_GOAL_BOUNDARY,
  controllerEvidenceBoundary: CONTROLLER_EVIDENCE_BOUNDARY,
  interactionBoundary: INTERACTION_BOUNDARY,
});
