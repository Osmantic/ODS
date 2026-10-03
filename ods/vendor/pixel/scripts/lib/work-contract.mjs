import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { validateJsonSchema } from "./json-schema.mjs";

function loadSchema(name) {
  return JSON.parse(readFileSync(new URL(`../../schemas/${name}`, import.meta.url), "utf8"));
}

const schemas = Object.freeze({
  job: loadSchema("work-job-v1.schema.json"),
  policy: loadSchema("work-policy-v1.schema.json"),
  plan: loadSchema("work-plan-v1.schema.json"),
  lease: loadSchema("work-capability-lease-v1.schema.json"),
  consumption: loadSchema("work-lease-consumption-v1.schema.json"),
  leaseRevocation: loadSchema("work-lease-revocation-v1.schema.json"),
  checkpoint: loadSchema("work-checkpoint-v1.schema.json"),
  goal: loadSchema("work-goal-v1.schema.json"),
  goalBrief: loadSchema("work-goal-brief-v1.schema.json"),
  inputSelection: loadSchema("work-input-selection-v1.schema.json"),
  inputCatalog: loadSchema("work-input-catalog-v1.schema.json"),
  inputPack: loadSchema("work-input-pack-v1.schema.json"),
  goalDraft: loadSchema("work-goal-draft-v1.schema.json"),
  goalDeclaration: loadSchema("work-goal-declaration-v1.schema.json"),
  goalBundle: loadSchema("work-goal-bundle-v1.schema.json"),
  goalAssembly: loadSchema("work-goal-assembly-v1.schema.json"),
  goalLaunchPreparation: loadSchema("work-goal-launch-preparation-v1.schema.json"),
  goalControllerEnvironment: loadSchema("work-goal-controller-environment-v1.schema.json"),
  goalControllerBundle: loadSchema("work-goal-controller-bundle-v1.schema.json"),
  goalStage: loadSchema("work-goal-stage-v1.schema.json"),
  goalCheckpoint: loadSchema("work-goal-checkpoint-v1.schema.json"),
  goalFleet: loadSchema("work-goal-fleet-v1.schema.json"),
  goalFleetCheckpoint: loadSchema("work-goal-fleet-checkpoint-v1.schema.json"),
  goalFleetController: loadSchema("work-goal-fleet-controller-v2.schema.json"),
  goalFleetHostEvidence: loadSchema("work-goal-fleet-host-evidence-v2.schema.json"),
  goalFleetHostProbe: loadSchema("work-goal-fleet-host-probe-v1.schema.json"),
  goalController: loadSchema("work-goal-controller-v1.schema.json"),
  goalRunBundle: loadSchema("work-goal-run-bundle-v1.schema.json"),
  semanticAcceptance: loadSchema("work-semantic-acceptance-v1.schema.json"),
  verificationEvidence: loadSchema("work-verification-evidence-v1.schema.json"),
  scoutReportProposal: loadSchema("work-scout-report-proposal-v1.schema.json"),
  scoutReport: loadSchema("work-scout-report-v1.schema.json"),
  scoutVerification: loadSchema("work-scout-verification-v1.schema.json"),
  researchQuery: loadSchema("work-research-query-v1.schema.json"),
  researchBatch: loadSchema("work-research-batch-v1.schema.json"),
  researchRetrieval: loadSchema("work-research-retrieval-v1.schema.json"),
  researchReport: loadSchema("work-research-report-v1.schema.json"),
  researchVerification: loadSchema("work-research-verification-v1.schema.json"),
  researchRevisionReview: loadSchema("work-research-revision-review-v1.schema.json"),
  researchRevisionHistory: loadSchema("work-research-revision-history-v1.schema.json"),
  researchReportProposal: loadSchema("work-research-report-proposal-v1.schema.json"),
  researchToolRequest: loadSchema("work-research-tool-request-v1.schema.json"),
  researchToolResponse: loadSchema("work-research-tool-response-v1.schema.json"),
  dataArtifactManifest: loadSchema("work-data-artifact-manifest-v1.schema.json"),
  dataReportProposal: loadSchema("work-data-report-proposal-v1.schema.json"),
  dataReport: loadSchema("work-data-report-v1.schema.json"),
  dataVerification: loadSchema("work-data-verification-v1.schema.json"),
  dataRuntime: loadSchema("work-data-runtime-v1.schema.json"),
  builderRuntime: loadSchema("work-builder-runtime-v1.schema.json"),
  ompRuntime: loadSchema("work-omp-runtime-v1.schema.json"),
  modelCapabilityReceipt: loadSchema("work-model-capability-receipt-v1.schema.json"),
  modelPolicyReview: loadSchema("work-model-policy-review-v1.schema.json"),
  modelPolicyEnableReview: loadSchema("work-model-policy-enable-review-v1.schema.json"),
  modelOperatorStatus: loadSchema("work-model-operator-status-v1.schema.json"),
  modelBackendStatus: loadSchema("work-model-backend-status-v1.schema.json"),
  modelArtifactManifest: loadSchema("work-model-artifact-manifest-v1.schema.json"),
  modelRuntimeCacheManifest: loadSchema("work-model-runtime-cache-manifest-v1.schema.json"),
  modelArtifactReview: loadSchema("work-model-artifact-review-v1.schema.json"),
  modelArtifactRenderReceipt: loadSchema("work-model-artifact-render-receipt-v1.schema.json"),
  modelBackendConfig: loadSchema("work-model-backend-config-v1.schema.json"),
  modelBackendReview: loadSchema("work-model-backend-review-v1.schema.json"),
  modelBackendLaunch: loadSchema("work-model-backend-launch-v1.schema.json"),
  modelBackendLifecycleReview: loadSchema("work-model-backend-lifecycle-review-v1.schema.json"),
  modelBackendLifecycleReceipt: loadSchema("work-model-backend-lifecycle-receipt-v1.schema.json"),
  modelBackendHaltReview: loadSchema("work-model-backend-halt-review-v1.schema.json"),
  modelBackendHaltReceipt: loadSchema("work-model-backend-halt-receipt-v1.schema.json"),
  modelBackendLiveQualification: loadSchema("work-model-backend-live-qualification-v1.schema.json"),
  watchdogDecision: loadSchema("work-watchdog-decision-v1.schema.json"),
  contextSessionInput: loadSchema("work-context-session-input-v1.schema.json"),
  contextCapsule: loadSchema("work-context-capsule-v1.schema.json"),
  capabilityPack: loadSchema("work-capability-pack-v1.schema.json"),
  capabilityGrant: loadSchema("work-capability-grant-v1.schema.json"),
  capabilityConsumption: loadSchema("work-capability-consumption-v1.schema.json"),
  capabilityControllerPolicy: loadSchema("work-capability-controller-policy-v1.schema.json"),
  capabilityJobAuthorization: loadSchema("work-capability-job-authorization-v1.schema.json"),
  capabilityToolRequest: loadSchema("work-capability-tool-request-v1.schema.json"),
  capabilityToolCatalog: loadSchema("work-capability-tool-catalog-v1.schema.json"),
  capabilityWatchdogEvent: loadSchema("work-capability-watchdog-event-v1.schema.json"),
  capabilityToolResponse: loadSchema("work-capability-tool-response-v1.schema.json"),
  capabilityToolCustody: loadSchema("work-capability-tool-custody-v1.schema.json"),
  knowledgeIngestion: loadSchema("work-knowledge-ingestion-v1.schema.json"),
  knowledgeSource: loadSchema("work-knowledge-source-v1.schema.json"),
  knowledgeQuery: loadSchema("work-knowledge-query-v1.schema.json"),
  knowledgeRetrieval: loadSchema("work-knowledge-retrieval-v1.schema.json"),
  knowledgeDeletion: loadSchema("work-knowledge-deletion-v1.schema.json"),
  knowledgeKeyRotation: loadSchema("work-knowledge-key-rotation-v1.schema.json"),
  knowledgeReconciliation: loadSchema("work-knowledge-reconciliation-v1.schema.json"),
  operatorStatus: loadSchema("work-operator-status-v1.schema.json"),
  codexPolicy: loadSchema("work-codex-policy-v1.schema.json"),
  codexRequest: loadSchema("work-codex-request-v1.schema.json"),
  codexCapsule: loadSchema("work-codex-capsule-v1.schema.json"),
  codexPlan: loadSchema("work-codex-plan-v1.schema.json"),
  codexOutput: loadSchema("work-codex-output-v1.schema.json"),
  codexAuthorization: loadSchema("work-codex-authorization-v1.schema.json"),
  codexAuthenticationEvidence: loadSchema("work-codex-authentication-evidence-v1.schema.json"),
  codexAuthenticationConsumption: loadSchema("work-codex-authentication-consumption-v1.schema.json"),
  codexCredentialCustody: loadSchema("work-codex-credential-custody-v1.schema.json"),
  codexEgressPolicy: loadSchema("work-codex-egress-policy-v1.schema.json"),
  codexExecutionClaim: loadSchema("work-codex-execution-claim-v1.schema.json"),
  codexResult: loadSchema("work-codex-result-v1.schema.json"),
  providerProfile: loadSchema("work-provider-profile-v1.schema.json"),
  providerReceipt: loadSchema("work-provider-receipt-v1.schema.json"),
  providerPrivatePolicy: loadSchema("work-provider-private-policy-v1.schema.json"),
  providerCustodyReceipt: loadSchema("work-provider-custody-receipt-v1.schema.json"),
  providerRunLedger: loadSchema("work-provider-run-ledger-v1.schema.json"),
  routerRequest: loadSchema("work-provider-router-request-v1.schema.json"),
  routerQualification: loadSchema("work-provider-router-qualification-v1.schema.json"),
  routerPolicy: loadSchema("work-provider-router-policy-v1.schema.json"),
  routerDecision: loadSchema("work-provider-router-decision-v1.schema.json"),
  result: loadSchema("work-result-v1.schema.json"),
  capabilityPackV2: loadSchema("work-capability-pack-v2.schema.json"),
  capabilityToolCatalogV2: loadSchema("work-capability-tool-catalog-v2.schema.json"),
  capabilityControllerPolicyV2: loadSchema("work-capability-controller-policy-v2.schema.json"),
  capabilityJobAuthorizationV2: loadSchema("work-capability-job-authorization-v2.schema.json"),
  capabilityToolRequestV2: loadSchema("work-capability-tool-request-v2.schema.json"),
  capabilityGrantV2: loadSchema("work-capability-grant-v2.schema.json"),
  capabilityLeaseV2: loadSchema("work-capability-lease-v2.schema.json"),
  capabilityRuntimeV2: loadSchema("work-capability-runtime-v2.schema.json"),
  capabilityConsumptionV2: loadSchema("work-capability-consumption-v2.schema.json"),
  operationalGrantV2: loadSchema("work-capability-operational-grant-v2.schema.json"),
  operationalRuntimeRequestV2: loadSchema("work-capability-operational-runtime-request-v2.schema.json"),
  operationalRuntimeResultV2: loadSchema("work-capability-operational-runtime-result-v2.schema.json"),
  sshApprovalV2: loadSchema("work-capability-ssh-approval-v2.schema.json"),
  queueRequestV2: loadSchema("work-capability-queue-request-v2.schema.json"),
  queueResponseV2: loadSchema("work-capability-queue-response-v2.schema.json"),
});

const classificationRank = Object.freeze({ public: 0, internal: 1, confidential: 2, restricted: 3 });
const mutatingTools = new Set(["write", "edit", "bash", "eval", "debug", "task", "hub"]);
const budgetFields = Object.freeze([
  "maxRuntimeSeconds", "maxIterations", "maxToolCalls", "maxConcurrentSubagents", "maxModelRequests",
  "maxInputTokens", "maxOutputTokens", "maxCpuCores", "maxMemoryMiB", "maxDiskBytes",
  "maxArtifactBytes", "maxNetworkBytes", "maxFailures", "noProgressLimit",
]);
const researchLimitFields = Object.freeze([
  "maxQueries", "maxResultsPerQuery", "maxSources", "maxSourceBytes", "maxTotalSourceBytes",
]);

export function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
}

function hasSchemaErrors(value, schema) {
  return validateJsonSchema(value, schema);
}

function validTimestamp(value) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function validateReadOnlyTools(filesystem, tools, errors, label) {
  if (filesystem !== "read-only-workspace" && filesystem !== "read-only") return;
  for (const tool of tools ?? []) {
    if (mutatingTools.has(tool)) errors.push(`${label}: read-only workspace cannot grant ${tool}`);
  }
}

function validateVerification(verification, acceptanceCriteria, inputs, errors, label) {
  if (!verification) return;
  const ids = new Set();
  const covered = new Set();
  let patchIntegrityChecks = 0;
  let runtimeSeconds = 0;
  let outputBytes = 0;
  const inputIds = new Set(inputs.map((input) => input.id));
  const withinInput = (path) => [...inputIds].some((id) => path === id || path.startsWith(`${id}/`));
  for (const check of verification.checks) {
    if (ids.has(check.id)) errors.push(`${label}.checks.${check.id}: check identifiers must be unique`);
    ids.add(check.id);
    for (const index of check.criterionIndexes) {
      if (index >= acceptanceCriteria.length) errors.push(`${label}.checks.${check.id}: criterion index ${index} is outside the immutable criteria`);
      else covered.add(index);
    }
    if (check.kind === "patch-integrity") {
      patchIntegrityChecks += 1;
      continue;
    }
    runtimeSeconds += check.timeoutSeconds;
    outputBytes += check.maxOutputBytes;
    if (!withinInput(check.workingDirectory)) errors.push(`${label}.checks.${check.id}: working directory is outside a declared input`);
    if (!/^\/(?:[A-Za-z0-9._+-]+\/)*[A-Za-z0-9._+-]+$/.test(check.argv[0]) || check.argv[0].includes("//")) {
      errors.push(`${label}.checks.${check.id}: executable must be a canonical absolute path`);
    }
    if (check.argv.some((argument) => /[\u0000-\u001f\u007f]/.test(argument))) errors.push(`${label}.checks.${check.id}: arguments contain control bytes`);
  }
  if (patchIntegrityChecks !== 1) errors.push(`${label}.checks: exactly one patch-integrity check is required`);
  for (let index = 0; index < acceptanceCriteria.length; index += 1) {
    if (!covered.has(index)) errors.push(`${label}.checks: criterion ${index} has no independent check`);
  }
  if (runtimeSeconds > verification.maxRuntimeSeconds) errors.push(`${label}.maxRuntimeSeconds: command timeouts exceed the aggregate ceiling`);
  if (outputBytes > verification.maxOutputBytes) errors.push(`${label}.maxOutputBytes: command outputs exceed the aggregate ceiling`);
  for (const prefix of verification.immutablePathPrefixes) {
    const normalized = prefix.endsWith("/") ? prefix.slice(0, -1) : prefix;
    if (!withinInput(normalized)) errors.push(`${label}.immutablePathPrefixes: ${prefix} is outside a declared input`);
  }
}

function validateResearch(research, errors, label) {
  if (!research) return;
  if (research.maxSources > research.maxQueries * research.maxResultsPerQuery) {
    errors.push(`${label}.maxSources: source ceiling exceeds the bounded query result space`);
  }
  if (research.maxTotalSourceBytes < research.maxSourceBytes) {
    errors.push(`${label}.maxTotalSourceBytes: total source ceiling is smaller than one source`);
  }
  const withinDomain = (host, domain) => host === domain || host.endsWith(`.${domain}`);
  const overlap = research.allowedDomains.filter((domain) => research.deniedDomains.some((denied) => withinDomain(domain, denied)));
  if (overlap.length) errors.push(`${label}.allowedDomains: allowed and denied domains overlap`);
  for (const [field, values] of [["allowedDomains", research.allowedDomains], ["deniedDomains", research.deniedDomains]]) {
    if (canonical(values) !== canonical([...values].sort())) errors.push(`${label}.${field}: domains must be uniquely sorted`);
  }
  const sourceOrder = ["web", "news", "academic", "forum"];
  if (canonical(research.sourceTypes) !== canonical([...research.sourceTypes].sort((left, right) => sourceOrder.indexOf(left) - sourceOrder.indexOf(right)))) {
    errors.push(`${label}.sourceTypes: source types must use canonical order`);
  }
}

function validateData(data, inputs, budgets, errors, label) {
  if (!data) return;
  const inputById = inputs === null ? null : new Map(inputs.map((input) => [input.id, input]));
  const datasetIds = new Set();
  const datasetPaths = new Set();
  for (const dataset of data.datasets) {
    if (datasetIds.has(dataset.datasetId)) errors.push(`${label}.datasets.${dataset.datasetId}: dataset identifiers must be unique`);
    datasetIds.add(dataset.datasetId);
    const pathKey = `${dataset.inputId}/${dataset.relativePath}`;
    if (dataset.relativePath.split("/").some((segment) => segment === "." || segment === "..")) errors.push(`${label}.datasets.${dataset.datasetId}.relativePath: path segments must be canonical`);
    if (datasetPaths.has(pathKey)) errors.push(`${label}.datasets.${dataset.datasetId}: dataset paths must be unique`);
    datasetPaths.add(pathKey);
    const input = inputById?.get(dataset.inputId);
    if (inputById && (!input || input.kind !== "dataset")) errors.push(`${label}.datasets.${dataset.datasetId}: input must reference a declared dataset`);
    else if (input && input.maxBytes !== undefined && dataset.maxBytes > input.maxBytes) errors.push(`${label}.datasets.${dataset.datasetId}.maxBytes: exceeds the declared input ceiling`);
  }
  if (canonical(data.datasets) !== canonical([...data.datasets].sort((left, right) => left.datasetId.localeCompare(right.datasetId)))) {
    errors.push(`${label}.datasets: datasets must use canonical identifier order`);
  }
  const artifactOrder = ["csv", "json", "jsonl", "markdown", "parquet", "png", "sqlite", "svg"];
  if (canonical(data.allowedArtifactFormats) !== canonical([...data.allowedArtifactFormats].sort((left, right) => artifactOrder.indexOf(left) - artifactOrder.indexOf(right)))) {
    errors.push(`${label}.allowedArtifactFormats: artifact formats must use canonical order`);
  }
  if (data.maxArtifactBytes > budgets.maxArtifactBytes) errors.push(`${label}.maxArtifactBytes: exceeds the job artifact budget`);
}

export function validateWorkJob(job) {
  const errors = hasSchemaErrors(job, schemas.job);
  if (errors.length) return errors;
  const inputIds = new Set();
  for (const input of job.inputs) {
    if (inputIds.has(input.id)) errors.push(`$.inputs.${input.id}: input identifiers must be unique`);
    inputIds.add(input.id);
    if (classificationRank[input.classification] > classificationRank[job.dataClassification]) {
      errors.push(`$.inputs.${input.id}: classification exceeds the job classification`);
    }
  }
  const capabilities = job.requestedCapabilities;
  validateReadOnlyTools(capabilities.filesystem, capabilities.tools, errors, "$.requestedCapabilities.tools");
  if (capabilities.network.mode === "none" && job.budgets.maxNetworkBytes !== 0) {
    errors.push("$.budgets.maxNetworkBytes: network-none jobs must have a zero byte budget");
  }
  if (capabilities.network.mode === "brokered" && capabilities.network.services.length === 0) {
    errors.push("$.requestedCapabilities.network.services: brokered mode requires an explicit service");
  }
  if (capabilities.modelRoute === "local-only" && capabilities.network.services.includes("frontier-work-provider")) {
    errors.push("$.requestedCapabilities: local-only work cannot request the Frontier work provider");
  }
  if (capabilities.modelRoute === "explicit-remote") {
    if (job.dataClassification !== "public") errors.push("$.dataClassification: remote work is public-only in the v1 contract");
    if (!capabilities.network.services.includes("frontier-work-provider")) errors.push("$.requestedCapabilities.network.services: remote work requires the Frontier work provider");
  }
  if (job.outputs.mode === "patch" && capabilities.filesystem !== "disposable-read-write") {
    errors.push("$.outputs.mode: patch output requires a disposable read-write workspace");
  }
  if (job.profile === "builder" && job.budgets.maxArtifactBytes < 1048576) {
    errors.push("$.budgets.maxArtifactBytes: Builder patch and test evidence require at least 1 MiB");
  }
  if (job.profile === "data-lab" && !job.inputs.some((input) => input.kind === "dataset")) {
    errors.push("$.inputs: data-lab jobs require at least one dataset");
  }
  if (job.profile !== "data-lab" && job.data !== undefined) errors.push("$.data: only Data Lab jobs may carry a data contract");
  if (job.profile === "data-lab" && job.verification !== undefined) errors.push("$.verification: Data Lab uses isolated exact-replay verification instead of Builder checks");
  if (job.profile !== "researcher" && job.research !== undefined) errors.push("$.research: only Researcher jobs may carry an egress policy");
  validateData(job.data, job.inputs, job.budgets, errors, "$.data");
  validateResearch(job.research, errors, "$.research");
  validateVerification(job.verification, job.acceptanceCriteria, job.inputs, errors, "$.verification");
  return errors;
}

export function validateWorkPolicy(policy) {
  const errors = hasSchemaErrors(policy, schemas.policy);
  if (errors.length) return errors;
  if (!policy.executor.url.includes(`/v${policy.executor.version}/`)) {
    errors.push("$.executor.url: release URL and executor version differ");
  }
  if (policy.runner.imageRef !== policy.runner.imageDigest && !policy.runner.imageRef.endsWith(`@${policy.runner.imageDigest}`)) {
    errors.push("$.runner.imageRef: image reference and digest differ");
  }
  if (policy.localModel.imageRef !== policy.localModel.imageDigest && !policy.localModel.imageRef.endsWith(`@${policy.localModel.imageDigest}`)) {
    errors.push("$.localModel.imageRef: image reference and digest differ");
  }
  if (policy.localModel.maxRequestContextTokens > policy.localModel.contextWindow) {
    errors.push("$.localModel.maxRequestContextTokens: request context exceeds the physical backend context window");
  }
  if (policy.profiles.builder.maxBudgets.maxArtifactBytes < 1048576 || policy.profiles.builder.maxBudgets.maxArtifactBytes > 268435456) {
    errors.push("$.profiles.builder.maxBudgets.maxArtifactBytes: the v1 Builder requires 1..256 MiB for patch and test evidence");
  }
  for (const executable of policy.verifier.allowedExecutables) {
    if (executable.includes("//") || executable.split("/").includes("..") || executable.split("/").includes(".")) {
      errors.push("$.verifier.allowedExecutables: executable paths must be canonical");
    }
  }
  if (policy.enabled) {
    if (!policy.profiles.scout.enabled) errors.push("$.profiles.scout.enabled: the v1 Work Broker requires Scout when enabled");
    if (!policy.runner.allowedIsolation.includes(policy.profiles.scout.isolation)) {
      errors.push("$.profiles.scout.isolation: Scout isolation is not allowed by the runner policy");
    }
    if (policy.profiles.builder.enabled && !policy.runner.allowedIsolation.includes(policy.profiles.builder.isolation)) {
      errors.push("$.profiles.builder.isolation: Builder isolation is not allowed by the runner policy");
    }
    if (policy.profiles.researcher.enabled && !policy.runner.allowedIsolation.includes(policy.profiles.researcher.isolation)) {
      errors.push("$.profiles.researcher.isolation: Researcher isolation is not allowed by the runner policy");
    }
    if (policy.profiles.dataLab.enabled && !policy.runner.allowedIsolation.includes(policy.profiles.dataLab.isolation)) {
      errors.push("$.profiles.dataLab.isolation: Data Lab isolation is not allowed by the runner policy");
    }
  }
  if (policy.profiles.researcher.enabled) {
    const limits = policy.profiles.researcher.maxResearch;
    if (limits.maxSources > limits.maxQueries * limits.maxResultsPerQuery) errors.push("$.profiles.researcher.maxResearch.maxSources: exceeds the query result space");
    if (limits.maxTotalSourceBytes < limits.maxSourceBytes) errors.push("$.profiles.researcher.maxResearch.maxTotalSourceBytes: is smaller than one source");
  }
  if (policy.profiles.dataLab.enabled) {
    const limits = policy.profiles.dataLab.maxData;
    if (limits.maxArtifactBytes > policy.profiles.dataLab.maxBudgets.maxArtifactBytes) errors.push("$.profiles.dataLab.maxData.maxArtifactBytes: exceeds the Data Lab artifact budget");
    const inputOrder = ["csv", "json", "jsonl", "parquet", "sqlite"];
    const artifactOrder = ["csv", "json", "jsonl", "markdown", "parquet", "png", "sqlite", "svg"];
    if (canonical(limits.allowedInputFormats) !== canonical([...limits.allowedInputFormats].sort((left, right) => inputOrder.indexOf(left) - inputOrder.indexOf(right)))) errors.push("$.profiles.dataLab.maxData.allowedInputFormats: formats must use canonical order");
    if (canonical(limits.allowedArtifactFormats) !== canonical([...limits.allowedArtifactFormats].sort((left, right) => artifactOrder.indexOf(left) - artifactOrder.indexOf(right)))) errors.push("$.profiles.dataLab.maxData.allowedArtifactFormats: formats must use canonical order");
  }
  return errors;
}

export function validateWorkPlan(plan) {
  const errors = hasSchemaErrors(plan, schemas.plan);
  if (errors.length) return errors;
  const inputIds = new Set();
  for (const input of plan.inputs) {
    if (inputIds.has(input.id)) errors.push(`$.inputs.${input.id}: input identifiers must be unique`);
    inputIds.add(input.id);
    if (input.objectName !== `${input.contentSha256}.tar`) errors.push(`$.inputs.${input.id}: object name must equal its content hash`);
    if (classificationRank[input.classification] > classificationRank[plan.dataClassification]) {
      errors.push(`$.inputs.${input.id}: classification exceeds the plan classification`);
    }
  }
  if (plan.profile !== "researcher" && plan.research !== undefined) errors.push("$.research: only Researcher plans may carry an egress policy");
  if (plan.profile !== "researcher" && plan.researchBackend !== undefined) errors.push("$.researchBackend: only Researcher plans may bind a research backend");
  if (plan.profile !== "data-lab" && (plan.data !== undefined || plan.dataRuntime !== undefined)) errors.push("$.data: only Data Lab plans may bind a data runtime");
  validateData(plan.data, plan.inputs, plan.budgets, errors, "$.data");
  validateResearch(plan.research, errors, "$.research");
  validateVerification(plan.verification, plan.acceptanceCriteria, plan.inputs, errors, "$.verification");
  return errors;
}

export function validateWorkLease(lease) {
  const errors = hasSchemaErrors(lease, schemas.lease);
  if (errors.length) return errors;
  const issuedAt = validTimestamp(lease.issuedAt);
  const expiresAt = validTimestamp(lease.expiresAt);
  if (issuedAt !== null && expiresAt !== null) {
    if (expiresAt <= issuedAt) errors.push("$.expiresAt: lease must expire after issuance");
    if (expiresAt - issuedAt > 604800000) errors.push("$.expiresAt: lease lifetime exceeds seven days");
  }
  validateReadOnlyTools(lease.isolation.workspaceMount, lease.grantedCapabilities.tools, errors, "$.grantedCapabilities.tools");
  if (lease.grantedCapabilities.network.mode === "none" && lease.budgets.maxNetworkBytes !== 0) {
    errors.push("$.budgets.maxNetworkBytes: network-none leases must have a zero byte budget");
  }
  if (lease.grantedCapabilities.network.mode === "brokered" && lease.grantedCapabilities.network.services.length === 0) {
    errors.push("$.grantedCapabilities.network.services: brokered mode requires an explicit service");
  }
  validateResearch(lease.research, errors, "$.research");
  validateData(lease.data, null, lease.budgets, errors, "$.data");
  return errors;
}

export function validateWorkGoalRunBundle(bundle) {
  const errors = hasSchemaErrors(bundle, schemas.goalRunBundle);
  if (errors.length) return errors;
  errors.push(...validatePlanLease(bundle.plan, bundle.lease).map((error) => `bundle ${error}`));
  const createdAt = validTimestamp(bundle.createdAt);
  if (createdAt !== null && Number(bundle.bundleId.split("-")[1]) !== createdAt) errors.push("$.bundleId: identity time must equal createdAt");
  if (bundle.jobId !== bundle.plan.jobId || bundle.jobId !== bundle.lease.jobId) errors.push("$: bundle job differs from its plan or lease");
  if (bundle.purpose === "initial") {
    if (bundle.sequence !== 0 || bundle.previousBundleSha256 !== null || bundle.lease.iteration !== 1 || bundle.lease.continuation !== null) errors.push("$: initial bundle position or lease is invalid");
  } else if (bundle.sequence < 1 || bundle.previousBundleSha256 === null) {
    errors.push("$: appended bundle lacks prior lineage");
  }
  if (bundle.purpose === "pre-admission-refresh" && (bundle.lease.iteration !== 1 || bundle.lease.continuation !== null)) errors.push("$: refreshed bundle must carry a first-iteration lease");
  if (bundle.purpose === "admission" && (bundle.lease.iteration !== 1 || bundle.lease.continuation !== null)) errors.push("$: admission bundle must carry a first-iteration lease");
  if (bundle.purpose === "continuation" && (bundle.lease.iteration < 2 || bundle.lease.continuation === null)) errors.push("$: continuation bundle lacks a continuation lease");
  if (createdAt !== null && (createdAt < Date.parse(bundle.lease.issuedAt) || createdAt >= Date.parse(bundle.lease.expiresAt))) errors.push("$.createdAt: bundle lease is not current at publication");
  return errors;
}

export function validateJobLease(job, lease) {
  const errors = [
    ...validateWorkJob(job).map((error) => `job ${error}`),
    ...validateWorkLease(lease).map((error) => `lease ${error}`),
  ];
  if (errors.length) return errors;
  if (job.jobId !== lease.jobId) errors.push("job/lease: job identifiers differ");
  const requestedTools = new Set(job.requestedCapabilities.tools);
  for (const tool of lease.grantedCapabilities.tools) {
    if (!requestedTools.has(tool)) errors.push(`job/lease: lease widens tool ${tool}`);
  }
  const requestedServices = new Set(job.requestedCapabilities.network.services);
  for (const service of lease.grantedCapabilities.network.services) {
    if (!requestedServices.has(service)) errors.push(`job/lease: lease widens service ${service}`);
  }
  for (const field of budgetFields) {
    if (lease.budgets[field] > job.budgets[field]) errors.push(`job/lease: lease widens budget ${field}`);
  }
  const expectedMount = job.requestedCapabilities.filesystem === "read-only-workspace" ? "read-only" : "disposable-read-write";
  if (lease.isolation.workspaceMount !== expectedMount) errors.push("job/lease: workspace mount differs from the request");
  const requestedOutputs = new Set(job.outputs.requiredKinds);
  for (const kind of lease.outputGate.allowedKinds) {
    if (!requestedOutputs.has(kind)) errors.push(`job/lease: lease widens output kind ${kind}`);
  }
  if (canonical(job.research ?? null) !== canonical(lease.research ?? null)) errors.push("job/lease: research egress policy differs");
  if (canonical(job.data ?? null) !== canonical(lease.data ?? null)) errors.push("job/lease: data contract differs");
  return errors;
}

export function validatePlanLease(plan, lease) {
  const errors = [
    ...validateWorkPlan(plan).map((error) => `plan ${error}`),
    ...validateWorkLease(lease).map((error) => `lease ${error}`),
  ];
  if (errors.length) return errors;
  if (plan.jobId !== lease.jobId) errors.push("plan/lease: job identifiers differ");
  const planSha256 = createHash("sha256").update(canonical(plan)).digest("hex");
  if (planSha256 !== lease.planSha256) errors.push("plan/lease: plan hash differs");
  for (const field of ["inputSetSha256", "policySha256"]) {
    if (plan[field] !== lease[field]) errors.push(`plan/lease: ${field} differs`);
  }
  for (const field of ["executor", "model", "isolation", "grantedCapabilities", "outputGate", "authority", "research", "researchBackend", "data", "dataRuntime"]) {
    if (canonical(plan[field]) !== canonical(lease[field])) errors.push(`plan/lease: ${field} differs`);
  }
  for (const field of budgetFields) if (lease.budgets[field] > plan.budgets[field]) errors.push(`plan/lease: lease widens budget ${field}`);
  if (lease.iteration > plan.budgets.maxIterations) errors.push("plan/lease: iteration exceeds the immutable plan budget");
  return errors;
}

export function validateJobPlanLease(job, plan, lease) {
  const errors = [
    ...validatePlanLease(plan, lease),
    ...validateJobLease(job, lease),
  ];
  if (errors.length) return errors;
  const requestSha256 = createHash("sha256").update(canonical(job)).digest("hex");
  if (plan.requestSha256 !== requestSha256) errors.push("job/plan: request hash differs");
  for (const field of ["jobId", "profile", "dataClassification", "objective", "acceptanceCriteria", "verification", "research", "data"]) {
    if (canonical(plan[field]) !== canonical(job[field])) errors.push(`job/plan: ${field} differs`);
  }
  for (const field of budgetFields) {
    if (plan.budgets[field] > job.budgets[field]) errors.push(`job/plan: plan widens budget ${field}`);
  }
  if (plan.inputs.length !== job.inputs.length) {
    errors.push("job/plan: input counts differ");
  } else {
    const requestedInputs = new Map(job.inputs.map((input) => [input.id, input]));
    const observedIds = new Set();
    for (const input of plan.inputs) {
      const requested = requestedInputs.get(input.id);
      if (observedIds.has(input.id)) errors.push(`job/plan: input ${input.id} is duplicated`);
      observedIds.add(input.id);
      if (
        !requested
        || input.kind !== requested.kind
        || input.mountMode !== requested.mountMode
        || input.contentSha256 !== requested.contentSha256
        || input.objectName !== `${requested.contentSha256}.tar`
        || input.classification !== requested.classification
        || input.bytes > requested.maxBytes
      ) errors.push(`job/plan: input ${input.id} differs or widens the request`);
    }
  }
  const inputSetSha256 = createHash("sha256").update(canonical(plan.inputs)).digest("hex");
  if (plan.inputSetSha256 !== inputSetSha256) errors.push("job/plan: input set hash differs");
  return errors;
}

export function validateWorkConsumption(consumption) {
  return hasSchemaErrors(consumption, schemas.consumption);
}

export function validateWorkLeaseRevocation(revocation) {
  const errors = hasSchemaErrors(revocation, schemas.leaseRevocation);
  if (errors.length) return errors;
  const identityTime = Number(revocation.revocationId.split("-")[1]);
  if (identityTime !== Date.parse(revocation.revokedAt)) errors.push("$.revocationId: identity time must equal revokedAt");
  return errors;
}

export function validateWorkCheckpoint(checkpoint) {
  const errors = hasSchemaErrors(checkpoint, schemas.checkpoint);
  if (errors.length) return errors;
  const { criteriaTotal, criteriaPassing, criteriaFailing } = checkpoint.progress;
  const evidence = [checkpoint.workerSessionSha256, checkpoint.artifactManifestSha256, checkpoint.verificationEvidenceSha256];
  if (criteriaPassing + criteriaFailing !== criteriaTotal) errors.push("$.progress: passing and failing counts must equal total criteria");
  if (checkpoint.sequence === 0 && checkpoint.iteration !== 0) errors.push("$.iteration: the initial checkpoint must be iteration zero");
  if (checkpoint.state === "authorized" && evidence.some((value) => value !== null)) {
    errors.push("$: authorized checkpoints cannot contain execution evidence");
  }
  if (checkpoint.state === "running" && (checkpoint.workerSessionSha256 === null || checkpoint.artifactManifestSha256 !== null || checkpoint.verificationEvidenceSha256 !== null)) {
    errors.push("$: running checkpoints require only worker-session evidence");
  }
  if (checkpoint.state === "verifying" && (checkpoint.workerSessionSha256 === null || checkpoint.artifactManifestSha256 === null || checkpoint.verificationEvidenceSha256 !== null)) {
    errors.push("$: verifying checkpoints require worker and artifact evidence but no verifier evidence");
  }
  if (["verified", "completed"].includes(checkpoint.state) && evidence.some((value) => value === null)) {
    errors.push("$: verified checkpoints require worker, artifact, and verifier evidence");
  }
  if (checkpoint.state === "completed" && (criteriaPassing !== criteriaTotal || criteriaFailing !== 0)) {
    errors.push("$.progress: completed checkpoints require every criterion to pass");
  }
  if (["cleanup-failed", "recovery-inconclusive"].includes(checkpoint.state) && checkpoint.progress.failureFingerprintSha256 === null) {
    errors.push("$.progress.failureFingerprintSha256: recovery states require a fingerprint");
  }
  const safetyIncident = checkpoint.authorityExpansionObserved
    || checkpoint.acceptanceCriteriaMutationObserved
    || checkpoint.externalEffectsObserved;
  if (safetyIncident && !["failed", "cancelled", "cleanup-failed", "recovery-inconclusive"].includes(checkpoint.state)) {
    errors.push("$: a safety incident requires a failed, cancelled, cleanup-failed, or recovery-inconclusive checkpoint");
  }
  return errors;
}

export function validateWorkGoal(goal) {
  const errors = hasSchemaErrors(goal, schemas.goal);
  if (errors.length) return errors;
  const milestoneIds = goal.milestones.map((milestone) => milestone.milestoneId);
  const jobIds = goal.milestones.map((milestone) => milestone.jobId);
  if (new Set(milestoneIds).size !== milestoneIds.length) errors.push("$.milestones: milestone identifiers must be unique");
  if (new Set(jobIds).size !== jobIds.length) errors.push("$.milestones: child job identifiers must be unique");
  if (canonical(milestoneIds) !== canonical([...milestoneIds].sort())) errors.push("$.milestones: milestones must use canonical identifier order");
  const known = new Set(milestoneIds);
  for (const milestone of goal.milestones) {
    if (canonical(milestone.dependsOn) !== canonical([...milestone.dependsOn].sort())) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: dependencies must use canonical order`);
    if (milestone.dependsOn.includes(milestone.milestoneId)) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: a milestone cannot depend on itself`);
    for (const dependency of milestone.dependsOn) if (!known.has(dependency)) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: ${dependency} is unknown`);
  }
  const byId = new Map(goal.milestones.map((milestone) => [milestone.milestoneId, milestone]));
  const visiting = new Set();
  const visited = new Set();
  const visit = (milestoneId) => {
    if (visited.has(milestoneId) || !byId.has(milestoneId)) return;
    if (visiting.has(milestoneId)) { errors.push("$.milestones: dependency graph contains a cycle"); return; }
    visiting.add(milestoneId);
    for (const dependency of byId.get(milestoneId).dependsOn) visit(dependency);
    visiting.delete(milestoneId);
    visited.add(milestoneId);
  };
  for (const milestoneId of milestoneIds) visit(milestoneId);
  if (goal.budgets.maxJobs < goal.milestones.length) errors.push("$.budgets.maxJobs: goal cannot start every declared milestone");
  return errors;
}

export function validateWorkGoalBrief(brief) {
  const errors = hasSchemaErrors(brief, schemas.goalBrief);
  if (errors.length) return errors;
  const milestoneIds = brief.milestones.map((milestone) => milestone.milestoneId);
  if (new Set(milestoneIds).size !== milestoneIds.length || canonical(milestoneIds) !== canonical([...milestoneIds].sort())) {
    errors.push("$.milestones: milestone identifiers must be uniquely sorted");
  }
  const known = new Set(milestoneIds);
  const sourceOrder = ["web", "news", "academic", "forum"];
  for (const milestone of brief.milestones) {
    if (canonical(milestone.dependsOn) !== canonical([...milestone.dependsOn].sort())) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: dependencies must use canonical order`);
    if (canonical(milestone.inputIds) !== canonical([...milestone.inputIds].sort())) errors.push(`$.milestones.${milestone.milestoneId}.inputIds: inputs must use canonical order`);
    if (milestone.dependsOn.includes(milestone.milestoneId)) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: a milestone cannot depend on itself`);
    for (const dependency of milestone.dependsOn) if (!known.has(dependency)) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: ${dependency} is unknown`);
    if (milestone.kind === "research") {
      if (brief.dataClassification !== "public") errors.push(`$.milestones.${milestone.milestoneId}: public research cannot receive ${brief.dataClassification} data`);
      const research = milestone.research;
      if (canonical(research.allowedDomains) !== canonical([...research.allowedDomains].sort())) errors.push(`$.milestones.${milestone.milestoneId}.research.allowedDomains: domains must use canonical order`);
      if (canonical(research.deniedDomains) !== canonical([...research.deniedDomains].sort())) errors.push(`$.milestones.${milestone.milestoneId}.research.deniedDomains: domains must use canonical order`);
      if (canonical(research.sourceTypes) !== canonical([...research.sourceTypes].sort((left, right) => sourceOrder.indexOf(left) - sourceOrder.indexOf(right)))) errors.push(`$.milestones.${milestone.milestoneId}.research.sourceTypes: source types must use canonical order`);
      const within = (host, domain) => host === domain || host.endsWith(`.${domain}`);
      if (research.allowedDomains.some((allowed) => research.deniedDomains.some((denied) => within(allowed, denied)))) errors.push(`$.milestones.${milestone.milestoneId}.research.allowedDomains: allowed and denied domains overlap`);
    }
  }
  const byId = new Map(brief.milestones.map((milestone) => [milestone.milestoneId, milestone]));
  const visiting = new Set(), visited = new Set();
  const visit = (milestoneId) => {
    if (visiting.has(milestoneId)) { errors.push("$.milestones: dependency graph contains a cycle"); return; }
    if (visited.has(milestoneId)) return;
    visiting.add(milestoneId);
    for (const dependency of byId.get(milestoneId)?.dependsOn ?? []) visit(dependency);
    visiting.delete(milestoneId); visited.add(milestoneId);
  };
  for (const milestoneId of milestoneIds) visit(milestoneId);
  return errors;
}

export function validateWorkInputCatalog(catalog) {
  const errors = hasSchemaErrors(catalog, schemas.inputCatalog);
  if (errors.length) return errors;
  const identityTime = Number(catalog.catalogId.split("-")[1]);
  if (identityTime !== Date.parse(catalog.createdAt)) errors.push("$.catalogId: identity time must equal createdAt");
  const ids = catalog.entries.map((entry) => entry.id);
  if (new Set(ids).size !== ids.length || canonical(ids) !== canonical([...ids].sort())) errors.push("$.entries: input identifiers must be uniquely sorted");
  const datasetIds = new Set();
  for (const entry of catalog.entries) {
    if (entry.objectName !== `${entry.contentSha256}.tar`) errors.push(`$.entries.${entry.id}.objectName: object name differs from content hash`);
    validateInputDatasets(entry, errors, `$.entries.${entry.id}`, datasetIds);
    for (const dataset of entry.datasets ?? []) if (dataset.bytes > entry.bytes) errors.push(`$.entries.${entry.id}.datasets.${dataset.datasetId}.bytes: dataset exceeds its archive object`);
  }
  return errors;
}

const datasetExtension = Object.freeze({ csv: ".csv", json: ".json", jsonl: ".jsonl", parquet: ".parquet", sqlite: ".sqlite" });

function validateInputDatasets(entry, errors, label, globalIds = null) {
  if (!entry.datasets) return;
  const ids = entry.datasets.map((dataset) => dataset.datasetId), paths = entry.datasets.map((dataset) => dataset.relativePath);
  if (new Set(ids).size !== ids.length || canonical(ids) !== canonical([...ids].sort())) errors.push(`${label}.datasets: dataset identifiers must be uniquely sorted`);
  if (new Set(paths).size !== paths.length) errors.push(`${label}.datasets: dataset paths must be unique`);
  for (const dataset of entry.datasets) {
    if (dataset.relativePath.split("/").some((part) => part === "." || part === "..")) errors.push(`${label}.datasets.${dataset.datasetId}.relativePath: path segments must be canonical`);
    if (!dataset.relativePath.toLocaleLowerCase("en-US").endsWith(datasetExtension[dataset.format])) errors.push(`${label}.datasets.${dataset.datasetId}.format: format differs from the file extension`);
    if (globalIds?.has(dataset.datasetId)) errors.push(`${label}.datasets.${dataset.datasetId}: dataset identifier is duplicated across inputs`);
    globalIds?.add(dataset.datasetId);
  }
}

export function validateWorkInputSelection(selection) {
  const errors = hasSchemaErrors(selection, schemas.inputSelection);
  if (errors.length) return errors;
  const identityTime = Number(selection.selectionId.split("-")[1]);
  if (identityTime !== Date.parse(selection.createdAt)) errors.push("$.selectionId: identity time must equal createdAt");
  const ids = selection.entries.map((entry) => entry.id);
  if (new Set(ids).size !== ids.length || canonical(ids) !== canonical([...ids].sort())) errors.push("$.entries: input identifiers must be uniquely sorted");
  const datasetIds = new Set();
  for (const entry of selection.entries) {
    if (/[\u0000-\u001f\u007f]/u.test(entry.sourceDirectory) || entry.sourceDirectory.normalize("NFC") !== entry.sourceDirectory) errors.push(`$.entries.${entry.id}.sourceDirectory: path contains control bytes or is not normalized`);
    validateInputDatasets(entry, errors, `$.entries.${entry.id}`, datasetIds);
  }
  return errors;
}

export function validateWorkInputPack(pack) {
  const errors = hasSchemaErrors(pack, schemas.inputPack);
  if (errors.length) return errors;
  const identityTime = Number(pack.packId.split("-")[1]);
  if (identityTime !== Date.parse(pack.createdAt)) errors.push("$.packId: identity time must equal createdAt");
  const ids = pack.entries.map((entry) => entry.id);
  if (new Set(ids).size !== ids.length || canonical(ids) !== canonical([...ids].sort())) errors.push("$.entries: input identifiers must be uniquely sorted");
  const datasetIds = new Set();
  for (const entry of pack.entries) {
    if (entry.objectName !== `${entry.archiveSha256}.tar`) errors.push(`$.entries.${entry.id}.objectName: object name differs from archive hash`);
    validateInputDatasets(entry, errors, `$.entries.${entry.id}`, datasetIds);
    for (const dataset of entry.datasets ?? []) if (dataset.bytes > entry.extractedBytes) errors.push(`$.entries.${entry.id}.datasets.${dataset.datasetId}.bytes: dataset exceeds extracted input bytes`);
  }
  const uniqueObjects = new Set(pack.entries.map((entry) => entry.objectName)).size;
  const expected = {
    inputs: pack.entries.length, objects: uniqueObjects,
    archiveBytes: [...new Map(pack.entries.map((entry) => [entry.objectName, entry.archiveBytes])).values()].reduce((sum, value) => sum + value, 0),
    extractedBytes: pack.entries.reduce((sum, entry) => sum + entry.extractedBytes, 0),
    files: pack.entries.reduce((sum, entry) => sum + entry.files, 0),
    directories: pack.entries.reduce((sum, entry) => sum + entry.directories, 0),
    datasets: pack.entries.reduce((sum, entry) => sum + (entry.datasets?.length ?? 0), 0),
  };
  for (const [field, value] of Object.entries(expected)) if (pack.totals[field] !== value) errors.push(`$.totals.${field}: total differs from the packed inventory`);
  return errors;
}

export function validateWorkGoalDraft(draft) {
  const errors = hasSchemaErrors(draft, schemas.goalDraft);
  if (errors.length) return errors;
  const identityTime = Number(draft.draftId.split("-")[1]);
  if (identityTime !== Date.parse(draft.createdAt)) errors.push("$.draftId: identity time must equal createdAt");
  if (draft.goal.milestones !== draft.milestones.length) errors.push("$.goal.milestones: count differs from the review list");
  const milestoneIds = draft.milestones.map((milestone) => milestone.milestoneId);
  const jobIds = draft.milestones.map((milestone) => milestone.jobId);
  if (new Set(milestoneIds).size !== milestoneIds.length || canonical(milestoneIds) !== canonical([...milestoneIds].sort())) errors.push("$.milestones: milestone identifiers must be uniquely sorted");
  if (new Set(jobIds).size !== jobIds.length) errors.push("$.milestones: child job identifiers must be unique");
  for (const milestone of draft.milestones) {
    if (new Set(milestone.doneWhen).size !== milestone.doneWhen.length) errors.push(`$.milestones.${milestone.milestoneId}.doneWhen: criteria must be unique`);
    if (canonical(milestone.dependsOn) !== canonical([...milestone.dependsOn].sort())) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: dependencies must use canonical order`);
    if (canonical(milestone.inputIds) !== canonical([...milestone.inputIds].sort())) errors.push(`$.milestones.${milestone.milestoneId}.inputIds: inputs must use canonical order`);
  }
  return errors;
}

export function validateWorkGoalDeclaration(declaration) {
  const errors = hasSchemaErrors(declaration, schemas.goalDeclaration);
  if (errors.length) return errors;
  const milestoneIds = declaration.milestones.map((milestone) => milestone.milestoneId);
  const jobIds = declaration.milestones.map((milestone) => milestone.jobId);
  if (new Set(milestoneIds).size !== milestoneIds.length || canonical(milestoneIds) !== canonical([...milestoneIds].sort())) errors.push("$.milestones: milestone identifiers must be uniquely sorted");
  if (new Set(jobIds).size !== jobIds.length) errors.push("$.milestones: child job identifiers must be unique");
  const known = new Set(milestoneIds), byId = new Map(declaration.milestones.map((milestone) => [milestone.milestoneId, milestone]));
  for (const milestone of declaration.milestones) {
    if (canonical(milestone.dependsOn) !== canonical([...milestone.dependsOn].sort())) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: dependencies must use canonical order`);
    if (milestone.dependsOn.includes(milestone.milestoneId)) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: a milestone cannot depend on itself`);
    for (const dependency of milestone.dependsOn) if (!known.has(dependency)) errors.push(`$.milestones.${milestone.milestoneId}.dependsOn: ${dependency} is unknown`);
  }
  const visiting = new Set(), visited = new Set();
  const visit = (milestoneId) => {
    if (visiting.has(milestoneId)) { errors.push("$.milestones: dependency graph contains a cycle"); return; }
    if (visited.has(milestoneId)) return;
    visiting.add(milestoneId);
    for (const dependency of byId.get(milestoneId)?.dependsOn ?? []) visit(dependency);
    visiting.delete(milestoneId); visited.add(milestoneId);
  };
  for (const milestoneId of milestoneIds) visit(milestoneId);
  return errors;
}

export function validateWorkGoalBundleManifest(manifest) {
  return hasSchemaErrors(manifest, schemas.goalBundle);
}

export function validateWorkGoalAssembly(assembly) {
  return hasSchemaErrors(assembly, schemas.goalAssembly);
}

export function validateWorkGoalLaunchPreparation(preparation) {
  const errors = hasSchemaErrors(preparation, schemas.goalLaunchPreparation);
  if (errors.length) return errors;
  const jobIds = preparation.children.map((child) => child.jobId);
  if (new Set(jobIds).size !== jobIds.length || canonical(jobIds) !== canonical([...jobIds].sort())) errors.push("$.children: job identifiers must be uniquely sorted");
  if (preparation.children.some((child) => Date.parse(child.expiresAt) <= Date.parse(preparation.preparedAt))) errors.push("$.children: every child lease must expire after preparation");
  return errors;
}

export function validateWorkGoalControllerEnvironment(environment) {
  return hasSchemaErrors(environment, schemas.goalControllerEnvironment);
}

export function validateWorkGoalControllerBundleManifest(manifest) {
  const errors = hasSchemaErrors(manifest, schemas.goalControllerBundle);
  if (!errors.length && canonical(manifest.profiles) !== canonical([...manifest.profiles].sort())) errors.push("$.profiles: profiles must use canonical order");
  return errors;
}

export function validateWorkGoalStage(stage) {
  const errors = hasSchemaErrors(stage, schemas.goalStage);
  if (!errors.length) {
    const jobIds = stage.children.map((child) => child.jobId);
    if (new Set(jobIds).size !== jobIds.length || canonical(jobIds) !== canonical([...jobIds].sort())) errors.push("$.children: job identifiers must be uniquely sorted");
  }
  return errors;
}

export function validateWorkGoalCheckpoint(checkpoint) {
  const errors = hasSchemaErrors(checkpoint, schemas.goalCheckpoint);
  if (errors.length) return errors;
  if (canonical(checkpoint.completedMilestones) !== canonical([...checkpoint.completedMilestones].sort())) errors.push("$.completedMilestones: identifiers must use canonical order");
  if (checkpoint.progress.milestonesCompleted !== checkpoint.completedMilestones.length) errors.push("$.progress.milestonesCompleted: count differs from the completed milestone set");
  if (checkpoint.progress.milestonesCompleted > checkpoint.progress.milestonesTotal) errors.push("$.progress: completed milestones exceed the immutable total");
  if (checkpoint.progress.jobsStarted < checkpoint.progress.milestonesCompleted) errors.push("$.progress.jobsStarted: cannot be smaller than completed milestones");
  if (checkpoint.progress.failures !== checkpoint.usage.failures) errors.push("$.progress.failures: count differs from aggregate usage");
  if (checkpoint.state === "completed" && checkpoint.progress.milestonesCompleted !== checkpoint.progress.milestonesTotal) errors.push("$.progress: completed goal requires every milestone");
  if (["failed", "no-progress", "recovery-inconclusive"].includes(checkpoint.state) && checkpoint.failureFingerprintSha256 === null) errors.push("$.failureFingerprintSha256: terminal failure requires a fingerprint");
  if (checkpoint.observation?.childState === "completed" && checkpoint.observation.verificationEvidenceSha256 === null) errors.push("$.observation: completed child requires independent verification evidence");
  if (checkpoint.observation && checkpoint.observation.criteriaPassing > checkpoint.observation.criteriaTotal) errors.push("$.observation: passing criteria exceed the child total");
  if (checkpoint.observation?.childState === "completed" && checkpoint.observation.criteriaPassing !== checkpoint.observation.criteriaTotal) errors.push("$.observation: completed child requires every criterion to pass");
  const safetyIncident = checkpoint.authorityExpansionObserved || checkpoint.externalEffectsObserved;
  if (safetyIncident && !["failed", "cancelled", "recovery-inconclusive"].includes(checkpoint.state)) errors.push("$: a safety incident requires a failed, cancelled, or recovery-inconclusive goal checkpoint");
  return errors;
}

export function validateWorkGoalFleet(fleet) {
  const errors = hasSchemaErrors(fleet, schemas.goalFleet);
  if (errors.length) return errors;
  if (Number(fleet.fleetId.split("-")[1]) !== Date.parse(fleet.createdAt)) errors.push("$.fleetId: identity time must equal createdAt");
  const goalIds = fleet.goals.map((goal) => goal.goalId);
  if (new Set(goalIds).size !== goalIds.length || canonical(goalIds) !== canonical([...goalIds].sort())) errors.push("$.goals: goal identifiers must be uniquely sorted");
  for (const goal of fleet.goals) {
    if (goal.resourceEnvelope.maxTurnSeconds !== goal.resourceEnvelope.maxWorkerSeconds + goal.resourceEnvelope.maxVerifierSeconds + goal.resourceEnvelope.maxCleanupSeconds) errors.push(`$.goals.${goal.goalId}.resourceEnvelope.maxTurnSeconds: differs from worker, verifier, and cleanup ceilings`);
    for (const field of ["maxTurnSeconds", "maxCpuCores", "maxMemoryMiB", "maxDiskBytes"]) {
      if (goal.resourceEnvelope[field] > fleet.hostCapacity[field]) errors.push(`$.goals.${goal.goalId}.resourceEnvelope.${field}: exceeds host capacity`);
    }
  }
  const retainedArtifactBytes = fleet.goals.reduce((total, goal) => total + goal.resourceEnvelope.maxRetainedArtifactBytes, 0);
  if (!Number.isSafeInteger(retainedArtifactBytes) || retainedArtifactBytes > fleet.hostCapacity.maxRetainedArtifactBytes) errors.push("$.hostCapacity.maxRetainedArtifactBytes: smaller than the aggregate registered retention ceiling");
  return errors;
}

export function validateWorkGoalFleetCheckpoint(checkpoint) {
  const errors = hasSchemaErrors(checkpoint, schemas.goalFleetCheckpoint);
  if (errors.length) return errors;
  if (Number(checkpoint.checkpointId.split("-")[1]) !== Date.parse(checkpoint.createdAt)) errors.push("$.checkpointId: identity time must equal createdAt");
  if (checkpoint.turns.settled > checkpoint.turns.started || checkpoint.turns.started - checkpoint.turns.settled !== (checkpoint.state === "claimed" ? 1 : 0)) errors.push("$.turns: active and settled turn counts are incoherent");
  if (checkpoint.sequence === 0 && (checkpoint.state !== "ready" || checkpoint.nextIndex !== 0 || checkpoint.lastOutcome !== null || checkpoint.turns.started !== 0)) errors.push("$: initial fleet checkpoint is not empty and ready");
  if (checkpoint.active && (checkpoint.active.selectedAt !== checkpoint.createdAt || Number(checkpoint.active.turnId.split("-")[1]) !== Date.parse(checkpoint.active.selectedAt))) errors.push("$.active: turn identity or selection time differs from its checkpoint");
  if (checkpoint.lastOutcome && Date.parse(checkpoint.lastOutcome.settledAt) > Date.parse(checkpoint.createdAt)) errors.push("$.lastOutcome.settledAt: outcome follows its checkpoint");
  return errors;
}

export function validateWorkGoalFleetController(config) {
  const errors = hasSchemaErrors(config, schemas.goalFleetController);
  if (errors.length) return errors;
  const separator = config.stateRoot.includes("\\") ? "\\" : "/";
  if (config.hostEvidenceDirectory !== `${config.stateRoot}${separator}host-evidence`) errors.push("$.hostEvidenceDirectory: must be the fixed evidence ledger under the fleet state root");
  const goalIds = config.goals.map((goal) => goal.goalId);
  if (new Set(goalIds).size !== goalIds.length || canonical(goalIds) !== canonical([...goalIds].sort())) errors.push("$.goals: goal identifiers must be uniquely sorted");
  const paths = config.goals.map((goal) => goal.controllerConfigPath);
  if (new Set(paths).size !== paths.length) errors.push("$.goals: controller configuration paths must be unique");
  return errors;
}

export function validateWorkGoalFleetHostEvidence(evidence) {
  const errors = hasSchemaErrors(evidence, schemas.goalFleetHostEvidence);
  if (errors.length) return errors;
  if (Number(evidence.evidenceId.split("-")[1]) !== Date.parse(evidence.observedAt)) errors.push("$.evidenceId: identity time must equal observedAt");
  if (evidence.sequence === 0 ? evidence.previousEvidenceSha256 !== null : evidence.previousEvidenceSha256 === null) errors.push("$.previousEvidenceSha256: genesis alone must have no predecessor");
  const lifetime = Date.parse(evidence.expiresAt) - Date.parse(evidence.observedAt);
  if (lifetime < 60000 || lifetime > 86400000) errors.push("$.expiresAt: evidence lifetime must be between one minute and 24 hours");
  const services = evidence.measurement.sharedServices;
  const ids = services.map((service) => service.serviceId);
  if (new Set(ids).size !== ids.length || canonical(ids) !== canonical([...ids].sort())) errors.push("$.measurement.sharedServices: identifiers must be uniquely sorted");
  const physical = evidence.measurement.physical, reserve = evidence.measurement.systemReserve, admitted = evidence.measurement.admitted;
  const sharedCpu = services.reduce((total, service) => total + service.maxCpuCores, 0);
  const sharedMemory = services.reduce((total, service) => total + service.maxMemoryMiB, 0);
  const sharedDisk = services.reduce((total, service) => total + service.maxDiskBytes, 0);
  if (admitted.cpuCores !== physical.cpuCores - reserve.cpuCores - sharedCpu) errors.push("$.measurement.admitted.cpuCores: differs from physical capacity minus exact reserves");
  if (admitted.memoryMiB !== physical.memoryMiB - reserve.memoryMiB - sharedMemory) errors.push("$.measurement.admitted.memoryMiB: differs from physical capacity minus exact reserves");
  if (admitted.disposableDiskBytes !== physical.disposableDiskBytes - reserve.diskBytes - sharedDisk) errors.push("$.measurement.admitted.disposableDiskBytes: differs from physical capacity minus exact reserves");
  return errors;
}

export function validateWorkGoalFleetHostProbe(config) {
  const errors = hasSchemaErrors(config, schemas.goalFleetHostProbe);
  if (errors.length) return errors;
  const ids = config.sharedServices.map((service) => service.serviceId);
  const units = config.sharedServices.map((service) => service.unitName);
  if (new Set(ids).size !== ids.length || canonical(ids) !== canonical([...ids].sort())) errors.push("$.sharedServices: identifiers must be uniquely sorted");
  if (new Set(units).size !== units.length) errors.push("$.sharedServices: systemd units must be unique");
  return errors;
}

export function validateWorkGoalController(config) {
  return hasSchemaErrors(config, schemas.goalController);
}

export function validateWorkModelCapabilityReceipt(receipt) {
  const errors = hasSchemaErrors(receipt, schemas.modelCapabilityReceipt);
  if (errors.length) return errors;
  const categories = ["argument-fidelity", "context-retention", "recovery-discipline", "structured-output", "tool-selection", "usage-accounting"];
  const profiles = ["assistant", "scout", "builder", "data-lab", "researcher"];
  const cases = receipt.observations.caseResults;
  const lifetime = Date.parse(receipt.expiresAt) - Date.parse(receipt.observedAt);
  if (lifetime <= 0 || lifetime > 30 * 86400000) errors.push("$.expiresAt: qualification lifetime must be positive and at most 30 days");
  if (cases.length !== receipt.suite.casesTotal) errors.push("$.observations.caseResults: case count differs from the suite");
  const ids = cases.map((entry) => entry.id);
  if (new Set(ids).size !== ids.length) errors.push("$.observations.caseResults: case identifiers must be unique");
  for (const entry of cases) {
    if (canonical(entry.profiles) !== canonical(profiles.filter((profile) => entry.profiles.includes(profile)))) errors.push(`$.observations.caseResults.${entry.id}.profiles: profiles are not in canonical order`);
    if (entry.contextTokensTested > receipt.model.contextWindow) errors.push(`$.observations.caseResults.${entry.id}.contextTokensTested: exceeds the bound model context window`);
    if (entry.passed !== (entry.failureClass === "pass") || entry.passed && entry.intentMutationObserved) errors.push(`$.observations.caseResults.${entry.id}: pass state differs from failure evidence`);
  }
  const caseManifest = cases.map((entry) => ({ id: entry.id, category: entry.category, profiles: entry.profiles, caseSha256: entry.caseSha256 }));
  if (createHash("sha256").update(canonical(caseManifest)).digest("hex") !== receipt.suite.casesSha256) errors.push("$.suite.casesSha256: differs from exact case results");
  const casesPassed = cases.filter((entry) => entry.passed).length;
  const casesFailed = cases.length - casesPassed;
  const inputTokens = cases.reduce((total, entry) => total + entry.inputTokens, 0);
  const outputTokens = cases.reduce((total, entry) => total + entry.outputTokens, 0);
  const latencies = cases.map((entry) => entry.latencyMs).sort((left, right) => left - right);
  const middle = Math.floor(latencies.length / 2);
  const medianLatencyMs = latencies.length % 2 ? latencies[middle] : Math.floor((latencies[middle - 1] + latencies[middle]) / 2);
  if (receipt.observations.casesPassed !== casesPassed || receipt.observations.casesFailed !== casesFailed) errors.push("$.observations: case totals differ from exact case results");
  if (receipt.observations.inputTokens !== inputTokens || receipt.observations.outputTokens !== outputTokens) errors.push("$.observations: token totals differ from exact case results");
  if (receipt.observations.medianLatencyMs !== medianLatencyMs) errors.push("$.observations.medianLatencyMs: differs from exact case results");
  const intentMutationObserved = cases.some((entry) => entry.intentMutationObserved);
  if (receipt.observations.intentMutationObserved !== intentMutationObserved) errors.push("$.observations.intentMutationObserved: differs from exact case results");
  const categoryPass = Object.fromEntries(categories.map((category) => [category, cases.filter((entry) => entry.category === category).every((entry) => entry.passed && !entry.intentMutationObserved)]));
  if (canonical(receipt.observations.categoryPass) !== canonical(categoryPass)) errors.push("$.observations.categoryPass: differs from exact case results");
  const profilePass = Object.fromEntries(profiles.map((profile) => {
    const relevant = cases.filter((entry) => entry.profiles.includes(profile));
    const represented = new Set(relevant.map((entry) => entry.category));
    return [profile, categories.every((category) => represented.has(category)) && relevant.every((entry) => entry.passed && !entry.intentMutationObserved)];
  }));
  if (canonical(receipt.observations.profilePass) !== canonical(profilePass)) errors.push("$.observations.profilePass: differs from exact case results");
  if (receipt.observations.casesPassed + receipt.observations.casesFailed !== receipt.suite.casesTotal) errors.push("$.observations: case totals differ from the suite");
  const empiricallyPassingProfiles = profiles.filter((profile) => receipt.observations.profilePass[profile]);
  const exactUsage = cases.every((entry) => entry.usageSource === "backend-observed") && categoryPass["usage-accounting"];
  if (receipt.observations.usageSource !== (exactUsage ? "backend-observed" : "estimated")) errors.push("$.observations.usageSource: differs from exact case results");
  const routeable = exactUsage && !receipt.observations.intentMutationObserved ? empiricallyPassingProfiles : [];
  const expectedStatus = routeable.length > 0 ? "qualified" : empiricallyPassingProfiles.length > 0 ? "degraded" : "failed";
  const qualified = receipt.status === "qualified";
  if (receipt.status !== expectedStatus) errors.push("$.status: differs from exact empirical profile, usage, and intent evidence");
  if (qualified !== (receipt.envelope.eligibleProfiles.length > 0)) errors.push("$.envelope.eligibleProfiles: only qualified receipts may route profiles");
  if (canonical(receipt.envelope.eligibleProfiles) !== canonical(routeable)) errors.push("$.envelope.eligibleProfiles: differs from routeable empirically passing profiles");
  if (receipt.envelope.exactUsage !== exactUsage) errors.push("$.envelope.exactUsage: differs from exact usage observations");
  for (const [field, category] of [["structuredOutput", "structured-output"], ["toolSelection", "tool-selection"], ["argumentFidelity", "argument-fidelity"], ["contextRetention", "context-retention"]]) {
    if (receipt.envelope[field] !== receipt.observations.categoryPass[category]) errors.push(`$.envelope.${field}: differs from category evidence`);
  }
  if (receipt.envelope.recoveryDiscipline !== (receipt.observations.categoryPass["recovery-discipline"] && !receipt.observations.intentMutationObserved)) errors.push("$.envelope.recoveryDiscipline: differs from recovery evidence");
  if (qualified && (receipt.observations.intentMutationObserved || !exactUsage || !receipt.envelope.exactUsage)) errors.push("$: qualified receipt lacks complete exact observations");
  if (!qualified && (receipt.envelope.maxContextTokens !== 0 || receipt.envelope.maxOutputTokens !== 0)) errors.push("$.envelope: unqualified receipt cannot advertise token capacity");
  return errors;
}

export function validateWorkModelPolicyReview(review) {
  const errors = hasSchemaErrors(review, schemas.modelPolicyReview);
  if (errors.length) return errors;
  if (review.confirmation.sha256 !== review.proposedPolicySha256) {
    errors.push("$.confirmation.sha256: confirmation must bind the proposed policy");
  }
  const order = ["assistant", "scout", "builder", "data-lab", "researcher"];
  if (canonical(review.eligibleProfiles) !== canonical(order.filter((profile) => review.eligibleProfiles.includes(profile)))) {
    errors.push("$.eligibleProfiles: profiles must use canonical order");
  }
  if (canonical(review.requiredProfiles) !== canonical(order.filter((profile) => review.requiredProfiles.includes(profile)))) {
    errors.push("$.requiredProfiles: profiles must use canonical order");
  }
  if (review.requiredProfiles.some((profile) => !review.eligibleProfiles.includes(profile))) {
    errors.push("$.requiredProfiles: every required profile must be empirically eligible");
  }
  return errors;
}

export function validateWorkModelPolicyEnableReview(review) {
  const errors = hasSchemaErrors(review, schemas.modelPolicyEnableReview);
  if (errors.length) return errors;
  if (review.confirmation.sha256 !== review.proposedPolicySha256) {
    errors.push("$.confirmation.sha256: confirmation must bind the proposed enabled policy");
  }
  const order = ["assistant", "scout", "builder", "data-lab", "researcher"];
  if (canonical(review.enabledProfiles) !== canonical(order.filter((profile) => review.enabledProfiles.includes(profile)))) {
    errors.push("$.enabledProfiles: profiles must use canonical order");
  }
  if (review.currentPolicySha256 === review.proposedPolicySha256) {
    errors.push("$.proposedPolicySha256: enablement must change the policy identity");
  }
  return errors;
}

export function validateWorkModelOperatorStatus(status) {
  const errors = hasSchemaErrors(status, schemas.modelOperatorStatus);
  if (errors.length) return errors;
  const order = ["assistant", "scout", "builder", "data-lab", "researcher"];
  for (const field of ["eligibleProfiles", "requiredProfiles"]) {
    if (canonical(status.qualification[field]) !== canonical(order.filter((profile) => status.qualification[field].includes(profile)))) {
      errors.push(`$.qualification.${field}: profiles must use canonical order`);
    }
  }
  if (status.qualification.current && (!status.qualification.present || status.qualification.expiresAt === null || status.qualification.exactUsage !== true)) {
    errors.push("$.qualification.current: current evidence must be present, expiring, and exact-accounted");
  }
  if (!status.qualification.present && ["expiresAt", "maxContextTokens", "maxOutputTokens", "exactUsage"].some((field) => status.qualification[field] !== null)) {
    errors.push("$.qualification: absent evidence cannot report measured values");
  }
  if (["ready-disabled", "ready", "renew-soon"].includes(status.state)) {
    if (!status.qualification.current || status.qualification.requiredProfiles.some((profile) => !status.qualification.eligibleProfiles.includes(profile))) {
      errors.push("$.state: ready state requires current evidence for every required profile");
    }
  }
  return errors;
}

export function validateWorkModelBackendStatus(status) {
  return hasSchemaErrors(status, schemas.modelBackendStatus);
}

export function validateWorkModelArtifactManifest(manifest) {
  const errors = hasSchemaErrors(manifest, schemas.modelArtifactManifest);
  if (errors.length) return errors;
  if (manifest.fileCount !== manifest.files.length) errors.push("$.fileCount: differs from the exact file set");
  if (manifest.totalBytes !== manifest.files.reduce((total, file) => total + file.bytes, 0)) errors.push("$.totalBytes: differs from the exact file set");
  if (canonical(manifest.files) !== canonical([...manifest.files].sort((left, right) => left.relativePath.localeCompare(right.relativePath, "en")))) errors.push("$.files: paths are not canonically ordered");
  if (new Set(manifest.files.map((file) => file.relativePath)).size !== manifest.files.length) errors.push("$.files: paths must be unique");
  const expected = manifest.kind === "file" ? manifest.files[0]?.sha256 : createHash("sha256").update(canonical({ schemaVersion: 1, kind: manifest.kind, files: manifest.files })).digest("hex");
  if (manifest.kind === "file" && (manifest.fileCount !== 1 || manifest.files[0]?.relativePath !== "model")) errors.push("$.files: file artifacts require exactly the canonical model entry");
  if (manifest.artifactSha256 !== expected) errors.push("$.artifactSha256: differs from the deterministic artifact identity");
  return errors;
}

export function validateWorkModelArtifactReview(review) {
  const errors = hasSchemaErrors(review, schemas.modelArtifactReview);
  if (errors.length) return errors;
  if (review.artifactMeasurementSha256 !== review.confirmation.sha256) errors.push("$.confirmation.sha256: differs from the reviewed artifact measurement request");
  return errors;
}

export function validateWorkModelArtifactRenderReceipt(receipt) {
  return hasSchemaErrors(receipt, schemas.modelArtifactRenderReceipt);
}

export function validateWorkModelRuntimeCacheManifest(manifest) {
  const errors = hasSchemaErrors(manifest, schemas.modelRuntimeCacheManifest);
  if (errors.length) return errors;
  const sorted = [...manifest.files].sort((left, right) => left.relativePath.localeCompare(right.relativePath, "en"));
  if (canonical(sorted) !== canonical(manifest.files)) errors.push("$.files: entries are not in canonical path order");
  if (new Set(manifest.files.map((file) => file.relativePath)).size !== manifest.files.length) errors.push("$.files: duplicate relative paths");
  const total = manifest.files.reduce((sum, file) => sum + file.bytes, 0);
  if (manifest.fileCount !== manifest.files.length) errors.push("$.fileCount: differs from files length");
  if (manifest.totalBytes !== total) errors.push("$.totalBytes: differs from file bytes");
  const expected = manifest.kind === "directory" ? createHash("sha256").update(canonical({ schemaVersion: 1, kind: manifest.kind, files: manifest.files })).digest("hex") : manifest.files[0]?.sha256;
  if (manifest.artifactSha256 !== expected) errors.push("$.artifactSha256: differs from the deterministic cache tree identity");
  return errors;
}

export function validateWorkModelBackendConfig(config) { return hasSchemaErrors(config, schemas.modelBackendConfig); }

export function validateWorkModelBackendReview(review) {
  const errors = hasSchemaErrors(review, schemas.modelBackendReview);
  if (errors.length) return errors;
  if (review.launchBundleSha256 !== review.confirmation.sha256) errors.push("$.confirmation.sha256: differs from the reviewed launch bundle");
  return errors;
}

export function validateWorkModelBackendLaunch(launch) {
  const errors = hasSchemaErrors(launch, schemas.modelBackendLaunch);
  if (errors.length) return errors;
  errors.push(...validateWorkModelArtifactManifest(launch.artifactManifest).map((error) => `$.artifactManifest${error.slice(1)}`));
  if (launch.runtimeCacheArtifactManifest !== null) errors.push(...validateWorkModelRuntimeCacheManifest(launch.runtimeCacheArtifactManifest).map((error) => `$.runtimeCacheArtifactManifest${error.slice(1)}`));
  if (launch.bindings.artifactSha256 !== launch.artifactManifest.artifactSha256) errors.push("$.bindings.artifactSha256: differs from the measured manifest");
  return errors;
}

export function validateWorkModelBackendLifecycleReview(review) {
  const errors = hasSchemaErrors(review, schemas.modelBackendLifecycleReview);
  if (errors.length) return errors;
  if (review.lifecycleSha256 !== review.confirmation.sha256) errors.push("$.confirmation.sha256: differs from the reviewed lifecycle intent");
  const start = review.intent === "start";
  if (review.effects.mayCreateNetwork !== start || review.effects.mayCreateContainer !== start || review.effects.mayStartContainer !== start
    || review.effects.mayStopContainer !== !start || review.effects.mayRemoveContainer !== !start || review.effects.mayRemoveNetwork !== !start) errors.push("$.effects: differs from the selected lifecycle intent");
  return errors;
}

export function validateWorkModelBackendLifecycleReceipt(receipt) {
  const errors = hasSchemaErrors(receipt, schemas.modelBackendLifecycleReceipt);
  if (errors.length) return errors;
  if (receipt.intent === "start" && !["ready-started", "ready-already-running"].includes(receipt.state)) errors.push("$.state: start receipt is not ready");
  if (receipt.intent === "stop" && !["removed", "already-absent"].includes(receipt.state)) errors.push("$.state: stop receipt is not removed");
  if (receipt.intent === "start" && receipt.checks.readinessPassed !== true) errors.push("$.checks.readinessPassed: start requires an observed ready event");
  if (receipt.intent === "stop" && receipt.checks.readinessPassed !== false) errors.push("$.checks.readinessPassed: stop does not claim model readiness");
  return errors;
}

export function validateWorkModelBackendHaltReview(review) {
  const errors = hasSchemaErrors(review, schemas.modelBackendHaltReview);
  if (errors.length) return errors;
  if (review.haltSha256 !== review.confirmation.sha256) errors.push("$.confirmation.sha256: differs from the reviewed emergency halt");
  if (review.effects.mayStopContainer !== (review.observation.state === "running")) errors.push("$.effects.mayStopContainer: differs from the observed runtime state");
  if (review.effects.mayInterruptActiveWorkers !== review.observation.activeWorkerPeersObserved) errors.push("$.effects.mayInterruptActiveWorkers: differs from the observed peer state");
  if (review.observation.identityBound !== (review.observation.state !== "absent")) errors.push("$.observation.identityBound: differs from target presence");
  return errors;
}

export function validateWorkModelBackendHaltReceipt(receipt) {
  const errors = hasSchemaErrors(receipt, schemas.modelBackendHaltReceipt);
  if (errors.length) return errors;
  if (receipt.changes.containerStopped !== (receipt.state === "halted")) errors.push("$.changes.containerStopped: differs from the halt result");
  if (receipt.checks.staticIdentityBound !== (receipt.state !== "already-absent")) errors.push("$.checks.staticIdentityBound: differs from target presence");
  if (receipt.checks.forensicResourcesRetained !== (receipt.state !== "already-absent")) errors.push("$.checks.forensicResourcesRetained: differs from target presence");
  return errors;
}

export function validateWorkModelBackendLiveQualification(qualification) {
  return hasSchemaErrors(qualification, schemas.modelBackendLiveQualification);
}

export function validateWorkWatchdogDecision(decision) {
  const errors = hasSchemaErrors(decision, schemas.watchdogDecision);
  if (errors.length) return errors;
  if (decision.observed.eventCount !== decision.proposal.sequence || decision.observed.toolCalls !== decision.observed.eventCount) errors.push("$.observed: event counts differ from the proposed sequence");
  if (decision.decision === "continue" && (decision.terminalState !== null || decision.reason !== "within-envelope")) errors.push("$: continue decision has a terminal reason");
  if (decision.decision === "stop" && (decision.terminalState === null || decision.reason === "within-envelope")) errors.push("$: stop decision lacks a terminal reason");
  return errors;
}

export function validateWorkContextCapsule(capsule) {
  const errors = hasSchemaErrors(capsule, schemas.contextCapsule);
  if (errors.length) return errors;
  if (Date.parse(capsule.expiresAt) <= Date.parse(capsule.createdAt)) errors.push("$.expiresAt: capsule must expire after creation");
  if (capsule.lineage.depth === 0 && capsule.lineage.rootJobId !== capsule.jobId) errors.push("$.lineage.rootJobId: root capsule must name its job");
  const ids = [...capsule.context.decisions, ...capsule.context.unresolvedRisks].map((entry) => entry.id);
  if (new Set(ids).size !== ids.length) errors.push("$.context: decision and risk identifiers must be unique");
  const paths = capsule.context.artifacts.map((entry) => entry.path);
  if (canonical(paths) !== canonical([...paths].sort()) || new Set(paths).size !== paths.length) errors.push("$.context.artifacts: paths must be uniquely sorted");
  for (const artifact of capsule.context.artifacts) if (artifact.verified !== (artifact.verificationEvidenceSha256 !== null)) errors.push(`$.context.artifacts.${artifact.path}: verification binding is inconsistent`);
  return errors;
}

export function validateWorkContextSessionInput(input) {
  const errors = hasSchemaErrors(input, schemas.contextSessionInput);
  if (errors.length) return errors;
  const decisionIds = input.decisions.map((entry) => entry.id);
  const riskIds = input.unresolvedRisks.map((entry) => entry.id);
  const allIds = [...decisionIds, ...riskIds];
  if (canonical(decisionIds) !== canonical([...decisionIds].sort())) errors.push("$.decisions: identifiers must be canonically sorted");
  if (canonical(riskIds) !== canonical([...riskIds].sort())) errors.push("$.unresolvedRisks: identifiers must be canonically sorted");
  if (new Set(allIds).size !== allIds.length) errors.push("$: decision and risk identifiers must be unique");
  const paths = input.artifacts.map((entry) => entry.path);
  if (canonical(paths) !== canonical([...paths].sort()) || new Set(paths).size !== paths.length) errors.push("$.artifacts: paths must be uniquely sorted");
  for (const artifact of input.artifacts) if (artifact.verified !== (artifact.verificationEvidenceSha256 !== null)) errors.push(`$.artifacts.${artifact.path}: verification binding is inconsistent`);
  return errors;
}

const capabilitySchemaKeywords = new Set([
  "title", "description", "type", "const", "enum", "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
  "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems", "uniqueItems", "contains", "items",
  "minProperties", "maxProperties", "required", "propertyNames", "properties", "additionalProperties",
]);

function validateCapabilityToolSchema(schema, path, errors) {
  let nodes = 0;
  function visit(rule, currentPath, depth) {
    nodes += 1;
    if (nodes > 1024) { if (!errors.some((entry) => entry === `${path}: schema exceeds node ceiling`)) errors.push(`${path}: schema exceeds node ceiling`); return; }
    if (depth > 32) { errors.push(`${currentPath}: schema exceeds depth ceiling`); return; }
    if (rule === true || rule === false) return;
    if (!rule || typeof rule !== "object" || Array.isArray(rule)) { errors.push(`${currentPath}: schema rule must be an object or boolean`); return; }
    for (const key of Object.keys(rule)) if (!capabilitySchemaKeywords.has(key)) errors.push(`${currentPath}.${key}: unsupported capability-schema keyword`);
    for (const key of ["allOf", "anyOf", "oneOf"]) {
      if (rule[key] !== undefined && (!Array.isArray(rule[key]) || rule[key].length > 16)) errors.push(`${currentPath}.${key}: schema branch list is invalid`);
      else for (const [index, child] of (rule[key] ?? []).entries()) visit(child, `${currentPath}.${key}[${index}]`, depth + 1);
    }
    for (const key of ["not", "if", "then", "else", "contains", "items", "propertyNames"]) if (rule[key] !== undefined) visit(rule[key], `${currentPath}.${key}`, depth + 1);
    if (rule.additionalProperties !== undefined && typeof rule.additionalProperties !== "boolean") visit(rule.additionalProperties, `${currentPath}.additionalProperties`, depth + 1);
    if (rule.properties !== undefined) {
      if (!rule.properties || typeof rule.properties !== "object" || Array.isArray(rule.properties) || Object.keys(rule.properties).length > 128) errors.push(`${currentPath}.properties: schema property map is invalid`);
      else for (const [key, child] of Object.entries(rule.properties)) visit(child, `${currentPath}.properties.${key}`, depth + 1);
    }
  }
  visit(schema, path, 0);
}

export function validateWorkCapabilityPack(pack) {
  const errors = hasSchemaErrors(pack, schemas.capabilityPack);
  if (errors.length) return errors;
  const names = pack.tools.map((tool) => tool.name);
  if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push("$.tools: names must be uniquely sorted");
  for (const tool of pack.tools) {
    for (const [field, digest] of [["inputSchema", "inputSchemaSha256"], ["outputSchema", "outputSchemaSha256"]]) {
      if (createHash("sha256").update(canonical(tool[field])).digest("hex") !== tool[digest]) errors.push(`$.tools.${tool.name}.${digest}: schema hash differs`);
      if (Buffer.byteLength(canonical(tool[field])) > 32768) errors.push(`$.tools.${tool.name}.${field}: schema exceeds byte ceiling`);
      if (tool[field]?.type === undefined) errors.push(`$.tools.${tool.name}.${field}: root type is required`);
      validateCapabilityToolSchema(tool[field], `$.tools.${tool.name}.${field}`, errors);
    }
    if (tool.inputSchema.type !== "object" || tool.inputSchema.additionalProperties !== false) errors.push(`$.tools.${tool.name}.inputSchema: root must be a closed object`);
    if (tool.outputSchema.type !== "object" || tool.outputSchema.additionalProperties !== false) errors.push(`$.tools.${tool.name}.outputSchema: root must be a closed object`);
  }
  const localImageIdentity = pack.adapter.imageRef === pack.adapter.imageId;
  if (localImageIdentity ? pack.adapter.imageDigest !== pack.adapter.imageId : pack.adapter.imageRef !== `${pack.adapter.imageRef.split("@sha256:")[0]}@${pack.adapter.imageDigest}`) errors.push("$.adapter.imageRef: digest differs");
  if (pack.adapter.workspace === "none" && pack.tools.some((tool) => tool.effectClass === "workspace")) errors.push("$.tools: workspace tool requires a disposable workspace adapter");
  const classifications = ["public", "internal", "confidential", "restricted"];
  for (const field of ["acceptedClassifications", "returnedClassifications"]) {
    if (canonical(pack.data[field]) !== canonical([...pack.data[field]].sort((a, b) => classifications.indexOf(a) - classifications.indexOf(b)))) errors.push(`$.data.${field}: classifications must use canonical order`);
  }
  if (canonical(pack.data.returnedClassifications) !== canonical(pack.data.acceptedClassifications)) errors.push("$.data.returnedClassifications: capability packs cannot silently reclassify data");
  return errors;
}

export function validateWorkCapabilityGrant(grant) {
  const errors = hasSchemaErrors(grant, schemas.capabilityGrant);
  if (errors.length) return errors;
  if (Date.parse(grant.expiresAt) <= Date.parse(grant.issuedAt)) errors.push("$.expiresAt: grant must expire after issue");
  if (canonical(grant.tools) !== canonical([...grant.tools].sort())) errors.push("$.tools: names must use canonical order");
  return errors;
}

export function validateWorkCapabilityConsumption(consumption) {
  return hasSchemaErrors(consumption, schemas.capabilityConsumption);
}

export function validateWorkCapabilityControllerPolicy(policy) {
  const errors = hasSchemaErrors(policy, schemas.capabilityControllerPolicy);
  if (errors.length) return errors;
  if (Number(policy.policyId.split("-")[1]) !== Date.parse(policy.createdAt)) errors.push("$.policyId: identity time must equal createdAt");
  if (Date.parse(policy.expiresAt) <= Date.parse(policy.createdAt)) errors.push("$.expiresAt: controller policy must expire after creation");
  if (canonical(policy.profiles) !== canonical([...policy.profiles].sort())) errors.push("$.profiles: profiles must use canonical order");
  const packKeys = policy.packs.map((pack) => `${pack.id}@${pack.version}`);
  if (new Set(packKeys).size !== packKeys.length || canonical(packKeys) !== canonical([...packKeys].sort())) errors.push("$.packs: exact pack identities must be uniquely sorted");
  for (const pack of policy.packs) {
    const names = pack.tools.map((tool) => tool.name);
    if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push(`$.packs.${pack.id}.tools: names must be uniquely sorted`);
    if (canonical(pack.classifications) !== canonical([...pack.classifications].sort((left, right) => classificationRank[left] - classificationRank[right]))) errors.push(`$.packs.${pack.id}.classifications: values must use canonical order`);
  }
  return errors;
}

export function validateWorkCapabilityJobAuthorization(authorization) {
  const errors = hasSchemaErrors(authorization, schemas.capabilityJobAuthorization);
  if (errors.length) return errors;
  if (Number(authorization.authorizationId.split("-")[1]) !== Date.parse(authorization.authorizedAt)) errors.push("$.authorizationId: identity time must equal authorizedAt");
  if (Date.parse(authorization.expiresAt) <= Date.parse(authorization.authorizedAt)) errors.push("$.expiresAt: job authorization must expire after issue");
  const names = authorization.tools.map((tool) => tool.name);
  if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push("$.tools: names must be uniquely sorted");
  if (authorization.watchdog.maxToolCalls !== authorization.maxSessions) errors.push("$.watchdog.maxToolCalls: must equal the authorized session budget");
  return errors;
}

export function validateWorkCapabilityToolRequest(request) {
  const errors = hasSchemaErrors(request, schemas.capabilityToolRequest);
  if (errors.length) return errors;
  if (Number(request.requestId.split("-")[1]) !== Date.parse(request.createdAt)) errors.push("$.requestId: identity time must equal createdAt");
  return errors;
}

export function validateWorkCapabilityToolCatalog(catalog) {
  const errors = hasSchemaErrors(catalog, schemas.capabilityToolCatalog);
  if (errors.length) return errors;
  if (Date.parse(catalog.expiresAt) <= Date.parse(catalog.createdAt)) errors.push("$.expiresAt: catalog must expire after creation");
  const names = catalog.tools.map((tool) => tool.name), exposed = catalog.tools.map((tool) => tool.exposedName);
  if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push("$.tools: signed names must be uniquely sorted");
  if (new Set(exposed).size !== exposed.length) errors.push("$.tools: exposed names must be unique");
  for (const tool of catalog.tools) {
    for (const [field, digest] of [["inputSchema", "inputSchemaSha256"], ["outputSchema", "outputSchemaSha256"]]) {
      if (createHash("sha256").update(canonical(tool[field])).digest("hex") !== tool[digest]) errors.push(`$.tools.${tool.name}.${digest}: schema hash differs`);
      validateCapabilityToolSchema(tool[field], `$.tools.${tool.name}.${field}`, errors);
    }
    if (tool.inputSchema.type !== "object" || tool.inputSchema.additionalProperties !== false) errors.push(`$.tools.${tool.name}.inputSchema: root must be a closed object`);
    if (tool.outputSchema.type !== "object" || tool.outputSchema.additionalProperties !== false) errors.push(`$.tools.${tool.name}.outputSchema: root must be a closed object`);
  }
  return errors;
}

export function validateWorkCapabilityWatchdogEvent(event) {
  const errors = hasSchemaErrors(event, schemas.capabilityWatchdogEvent);
  if (errors.length) return errors;
  const { recordSha256, ...body } = event;
  if (createHash("sha256").update(canonical(body)).digest("hex") !== recordSha256) errors.push("$.recordSha256: event record hash differs");
  return errors;
}

export function validateWorkCapabilityToolResponse(response) {
  const errors = hasSchemaErrors(response, schemas.capabilityToolResponse);
  if (errors.length) return errors;
  if (response.structuredContent !== null && createHash("sha256").update(canonical(response.structuredContent)).digest("hex") !== response.contentSha256) errors.push("$.contentSha256: response content hash differs");
  return errors;
}

export function validateWorkCapabilityToolCustody(record) {
  const errors = hasSchemaErrors(record, schemas.capabilityToolCustody);
  if (errors.length) return errors;
  if (Number(record.recordId.split("-")[1]) !== Date.parse(record.recordedAt)) errors.push("$.recordId: identity time must equal recordedAt");
  const { recordSha256, ...body } = record;
  if (createHash("sha256").update(canonical(body)).digest("hex") !== recordSha256) errors.push("$.recordSha256: custody record hash differs");
  if ((record.sequence === 0) !== (record.previousRecordSha256 === null)) errors.push("$.previousRecordSha256: only the first custody record has no predecessor");
  const present = (field) => record[field] !== null;
  if (record.phase === "claimed" && ["decisionSha256", "grantId", "grantSha256", "runtimeReceiptSha256", "eventRecordSha256", "responseSha256", "outcome"].some(present)) errors.push("$: claimed custody already contains later evidence");
  if (["authorized", "launching"].includes(record.phase) && (!present("decisionSha256") || !present("grantId") || !present("grantSha256") || ["runtimeReceiptSha256", "eventRecordSha256", "responseSha256", "outcome"].some(present))) errors.push(`$: ${record.phase} custody evidence is invalid`);
  if (record.phase === "runtime-returned" && (!present("decisionSha256") || !present("grantId") || !present("grantSha256") || present("responseSha256") || present("eventRecordSha256") || !["success", "error"].includes(record.outcome) || record.outcome === "success" && !present("runtimeReceiptSha256"))) errors.push("$: runtime-returned custody evidence is invalid");
  if (record.phase === "stopped" && (!present("decisionSha256") || !present("responseSha256") || record.outcome !== "stopped" || ["grantId", "grantSha256", "runtimeReceiptSha256", "eventRecordSha256"].some(present))) errors.push("$: stopped custody evidence is invalid");
  if (record.phase === "settled" && (!present("decisionSha256") || !present("grantId") || !present("grantSha256") || !present("eventRecordSha256") || !present("responseSha256") || !["success", "error"].includes(record.outcome))) errors.push("$: settled custody evidence is invalid");
  return errors;
}

const V2_NETWORK_TARGETS = new Set(["public-api", "private-lan", "ssh", "browser", "mailbox", "download", "api"]);
const V2_LOCAL_TARGETS = new Set(["local-filesystem", "local-process", "file", "shell"]);
const V2_TARGET_CLASSES = new Set([...V2_NETWORK_TARGETS, ...V2_LOCAL_TARGETS]);
const V2_BUDGET_FIELDS = Object.freeze(["maxCalls", "maxBytes", "maxDurationMs"]);

function v2ToolSchemaProperties(schema) {
  if (!schema || typeof schema !== "object" || !schema.properties || typeof schema.properties !== "object" || Array.isArray(schema.properties)) return [];
  return Object.keys(schema.properties);
}

function v2SortedUnique(values) {
  return [...new Set(values)].sort();
}

function validateV2ToolBindings(pack, errors) {
  const credentialRefs = new Set();
  const networkDestinations = new Set();
  const requiredModes = new Set();
  for (const tool of pack.tools ?? []) {
    const effectClass = tool.effectClass, binding = tool.binding;
    if (effectClass === "workspace") {
      if (binding !== null) errors.push(`$.tools.${tool.name}.binding: workspace tools must have no binding`);
      continue;
    }
    if (effectClass === "read-only" && binding === null) continue;
    if (binding === null || typeof binding !== "object" || Array.isArray(binding)) {
      errors.push(`$.tools.${tool.name}.binding: ${effectClass} requires an explicit binding`);
      continue;
    }
    if (effectClass === "read-only") {
      if (binding.targetClass !== "ssh") errors.push(`$.tools.${tool.name}.binding.targetClass: bound read-only tool must be the narrow SSH target`);
      if (binding.scope?.mode !== "private-allowlist" || binding.egress?.mode !== "private-allowlist") errors.push(`$.tools.${tool.name}.binding.scope: read-only SSH must use exact private-allowlist scope and egress`);
      if ((binding.scope?.destinations?.length ?? 0) === 0 || (binding.scope?.endpoints?.length ?? 0) !== 0) errors.push(`$.tools.${tool.name}.binding.scope: read-only SSH requires explicit private aliases and no public endpoints`);
      if (canonical(binding.scope?.destinations) !== canonical(binding.egress?.destinations)) errors.push(`$.tools.${tool.name}.binding.egress.destinations: read-only SSH egress must equal its exact private scope`);
      if (binding.approval?.required !== true || binding.approval?.mode !== "operator-approval") errors.push(`$.tools.${tool.name}.binding.approval: read-only SSH requires operator approval`);
      if (binding.idempotency?.required !== true || !binding.idempotency?.keys?.includes("requestId")) errors.push(`$.tools.${tool.name}.binding.idempotency: read-only SSH requires requestId idempotency`);
      requiredModes.add("private-allowlist");
      for (const destination of binding.scope?.destinations ?? []) networkDestinations.add(destination);
    } else if (effectClass === "brokered-network") {
      if (binding.egress?.mode !== "none") errors.push(`$.tools.${tool.name}.binding.egress.mode: brokered-network must have no direct egress`);
      if (binding.scope?.mode !== "public") errors.push(`$.tools.${tool.name}.binding.scope.mode: brokered-network scope must be explicit public broker endpoints`);
      if (!["api", "public-api"].includes(binding.targetClass)) errors.push(`$.tools.${tool.name}.binding.targetClass: brokered-network target class must be api or public-api`);
      if ((binding.scope?.endpoints?.length ?? 0) === 0) errors.push(`$.tools.${tool.name}.binding.scope.endpoints: brokered-network requires explicit broker-authorized endpoints`);
      requiredModes.add("brokered");
    } else if (effectClass === "external") {
      const targetClass = binding.targetClass, egressMode = binding.egress?.mode, scopeMode = binding.scope?.mode;
      if (V2_LOCAL_TARGETS.has(targetClass)) {
        if (egressMode !== "none") errors.push(`$.tools.${tool.name}.binding.egress.mode: local-target external tool must have no direct egress`);
        if (scopeMode !== "private-allowlist") errors.push(`$.tools.${tool.name}.binding.scope.mode: local-target external tool requires an explicit private-allowlist scope`);
        if ((binding.scope?.destinations?.length ?? 0) === 0) errors.push(`$.tools.${tool.name}.binding.scope.destinations: local-target external tool requires explicit owner-authorized targets`);
        requiredModes.add("private-allowlist");
        for (const destination of binding.scope?.destinations ?? []) networkDestinations.add(destination);
      } else if (V2_NETWORK_TARGETS.has(targetClass)) {
        if (!["public", "private-allowlist"].includes(egressMode)) errors.push(`$.tools.${tool.name}.binding.egress.mode: network external tool requires public or private-allowlist egress`);
        if (egressMode !== scopeMode) errors.push(`$.tools.${tool.name}.binding.scope: external scope and egress mode must not mix public and private targets`);
        if (egressMode === "public") {
          if ((binding.scope?.endpoints?.length ?? 0) === 0) errors.push(`$.tools.${tool.name}.binding.scope.endpoints: public external tool requires explicit endpoints`);
          if ((binding.scope?.destinations?.length ?? 0) !== 0) errors.push(`$.tools.${tool.name}.binding.scope.destinations: public external tool cannot carry private destinations`);
          requiredModes.add("public");
          for (const endpoint of binding.scope?.endpoints ?? []) networkDestinations.add(endpoint);
        } else {
          if ((binding.scope?.destinations?.length ?? 0) === 0) errors.push(`$.tools.${tool.name}.binding.scope.destinations: private-allowlist external tool requires explicit owner-authorized destinations`);
          if ((binding.scope?.endpoints?.length ?? 0) !== 0) errors.push(`$.tools.${tool.name}.binding.scope.endpoints: private-allowlist external tool cannot carry public endpoints`);
          requiredModes.add("private-allowlist");
          for (const destination of binding.scope?.destinations ?? []) networkDestinations.add(destination);
        }
      } else {
        errors.push(`$.tools.${tool.name}.binding.targetClass: external tool must declare an explicit target class`);
      }
    }
    for (const [field, list] of [["endpoints", binding.scope?.endpoints], ["destinations", binding.scope?.destinations]]) {
      if (!Array.isArray(list)) continue;
      if (list.some((item) => typeof item === "string" && item.includes("*"))) errors.push(`$.tools.${tool.name}.binding.scope.${field}: wildcards are not allowed`);
      if (new Set(list).size !== list.length) errors.push(`$.tools.${tool.name}.binding.scope.${field}: values must be unique`);
      if (canonical(list) !== canonical([...list].sort())) errors.push(`$.tools.${tool.name}.binding.scope.${field}: values must use canonical order`);
    }
    if (binding.egress?.destinations !== undefined) {
      const list = binding.egress.destinations;
      if (Array.isArray(list) && list.some((item) => typeof item === "string" && item.includes("*"))) errors.push(`$.tools.${tool.name}.binding.egress.destinations: wildcards are not allowed`);
      if (Array.isArray(list) && new Set(list).size !== list.length) errors.push(`$.tools.${tool.name}.binding.egress.destinations: destinations must be unique`);
      if (Array.isArray(list) && canonical(list) !== canonical([...list].sort())) errors.push(`$.tools.${tool.name}.binding.egress.destinations: destinations must use canonical order`);
    }
    if (binding.credentials) {
      for (const ref of binding.credentials.refs ?? []) credentialRefs.add(ref);
      if (Array.isArray(binding.credentials.refs) && new Set(binding.credentials.refs).size !== binding.credentials.refs.length) errors.push(`$.tools.${tool.name}.binding.credentials.refs: refs must be unique`);
      if (Array.isArray(binding.credentials.refs) && canonical(binding.credentials.refs) !== canonical([...binding.credentials.refs].sort())) errors.push(`$.tools.${tool.name}.binding.credentials.refs: refs must use canonical order`);
    }
    if (binding.idempotency) {
      if (binding.idempotency.required && (binding.idempotency.keys?.length ?? 0) === 0) errors.push(`$.tools.${tool.name}.binding.idempotency: required idempotency needs at least one key`);
      if (!binding.idempotency.required && (binding.idempotency.keys?.length ?? 0) !== 0) errors.push(`$.tools.${tool.name}.binding.idempotency: optional idempotency cannot carry keys`);
      if (Array.isArray(binding.idempotency.keys) && new Set(binding.idempotency.keys).size !== binding.idempotency.keys.length) errors.push(`$.tools.${tool.name}.binding.idempotency.keys: keys must be unique`);
    }
    if (binding.neverEgress) {
      const props = new Set(v2ToolSchemaProperties(tool.inputSchema));
      for (const field of binding.neverEgress) {
        if (!props.has(field)) errors.push(`$.tools.${tool.name}.binding.neverEgress.${field}: field is not a declared input schema property`);
      }
      if (new Set(binding.neverEgress).size !== binding.neverEgress.length) errors.push(`$.tools.${tool.name}.binding.neverEgress: fields must be unique`);
      if (canonical(binding.neverEgress) !== canonical([...binding.neverEgress].sort())) errors.push(`$.tools.${tool.name}.binding.neverEgress: fields must use canonical order`);
    }
    if (binding.budgets) {
      const perRun = binding.budgets.perRun, cumulative = binding.budgets.cumulative;
      for (const field of V2_BUDGET_FIELDS) {
        if (perRun[field] > cumulative[field]) errors.push(`$.tools.${tool.name}.binding.budgets: per-run ${field} exceeds cumulative ${field}`);
      }
      if (perRun.maxDurationMs > (pack.limits?.maxRuntimeMs ?? Infinity)) errors.push(`$.tools.${tool.name}.binding.budgets.perRun.maxDurationMs: exceeds the pack runtime ceiling`);
      if (perRun.maxBytes > (pack.limits?.maxOutputBytes ?? Infinity)) errors.push(`$.tools.${tool.name}.binding.budgets.perRun.maxBytes: exceeds the pack output ceiling`);
      if (perRun.maxCalls > (pack.limits?.maxCalls ?? Infinity)) errors.push(`$.tools.${tool.name}.binding.budgets.perRun.maxCalls: exceeds the pack call ceiling`);
    }
    if (binding.inputClassification && !new Set(pack.data?.acceptedClassifications ?? []).has(binding.inputClassification)) errors.push(`$.tools.${tool.name}.binding.inputClassification: outside the pack accepted classifications`);
    if (binding.outputClassification && !new Set(pack.data?.returnedClassifications ?? []).has(binding.outputClassification)) errors.push(`$.tools.${tool.name}.binding.outputClassification: outside the pack returned classifications`);
  }
  return {
    credentialRefs: v2SortedUnique(credentialRefs),
    networkDestinations: v2SortedUnique(networkDestinations),
    requiredModes,
  };
}

function v2DerivedNetworkMode(requiredModes) {
  if (requiredModes.size === 0) return "none";
  if (requiredModes.has("public") && requiredModes.has("private-allowlist")) return "mixed";
  if (requiredModes.has("private-allowlist")) return "private-allowlist";
  if (requiredModes.has("public")) return "public";
  return "brokered";
}

function validateV2BindingSummary(tool, label, errors) {
  const summary = tool.bindingSummary;
  if (!summary || typeof summary !== "object" || Array.isArray(summary)) return;
  const target = summary.targetClass;
  if (tool.effectClass === "read-only") {
    if (!V2_LOCAL_TARGETS.has(target) && target !== "ssh") errors.push(`${label}.bindingSummary.targetClass: read-only effect must declare a local target or the narrow SSH target`);
  } else if (tool.effectClass === "workspace") {
    if (!V2_LOCAL_TARGETS.has(target)) errors.push(`${label}.bindingSummary.targetClass: workspace effect class cannot declare a network target`);
  } else if (tool.effectClass === "brokered-network") {
    if (!["api", "public-api"].includes(target)) errors.push(`${label}.bindingSummary.targetClass: brokered-network must declare an api target`);
  } else if (tool.effectClass === "external") {
    if (!V2_TARGET_CLASSES.has(target)) errors.push(`${label}.bindingSummary.targetClass: external must declare an explicit target class`);
  }
}

export function validateWorkCapabilityPackV2(pack) {
  const errors = hasSchemaErrors(pack, schemas.capabilityPackV2);
  if (errors.length) return errors;
  if (pack.provenance?.admissionOnly !== true) errors.push("$.provenance.admissionOnly: v2 pack must be admission-only");
  if (pack.security?.admissionOnly !== true) errors.push("$.security.admissionOnly: v2 pack must be admission-only");
  const names = pack.tools.map((tool) => tool.name);
  if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push("$.tools: names must be uniquely sorted");
  for (const tool of pack.tools) {
    for (const [field, digest] of [["inputSchema", "inputSchemaSha256"], ["outputSchema", "outputSchemaSha256"]]) {
      if (createHash("sha256").update(canonical(tool[field])).digest("hex") !== tool[digest]) errors.push(`$.tools.${tool.name}.${digest}: schema hash differs`);
      if (Buffer.byteLength(canonical(tool[field])) > 32768) errors.push(`$.tools.${tool.name}.${field}: schema exceeds byte ceiling`);
      if (tool[field]?.type === undefined) errors.push(`$.tools.${tool.name}.${field}: root type is required`);
      validateCapabilityToolSchema(tool[field], `$.tools.${tool.name}.${field}`, errors);
    }
    if (tool.inputSchema.type !== "object" || tool.inputSchema.additionalProperties !== false) errors.push(`$.tools.${tool.name}.inputSchema: root must be a closed object`);
    if (tool.outputSchema.type !== "object" || tool.outputSchema.additionalProperties !== false) errors.push(`$.tools.${tool.name}.outputSchema: root must be a closed object`);
  }
  if (pack.adapter.workspace === "none" && pack.tools.some((tool) => tool.effectClass === "workspace")) errors.push("$.tools: workspace tool requires a disposable workspace adapter");
  const classifications = ["public", "internal", "confidential", "restricted"];
  for (const field of ["acceptedClassifications", "returnedClassifications"]) {
    if (canonical(pack.data[field]) !== canonical([...pack.data[field]].sort((a, b) => classifications.indexOf(a) - classifications.indexOf(b)))) errors.push(`$.data.${field}: classifications must use canonical order`);
  }
  if (canonical(pack.data.returnedClassifications) !== canonical(pack.data.acceptedClassifications)) errors.push("$.data.returnedClassifications: capability packs cannot silently reclassify data");
  const { credentialRefs, networkDestinations, requiredModes } = validateV2ToolBindings(pack, errors);
  const derivedMode = v2DerivedNetworkMode(requiredModes);
  if (derivedMode === "mixed") errors.push("$.security: pack mixes public and private-allowlist network scopes");
  else if (pack.security.networkMode !== derivedMode) errors.push(`$.security.networkMode: must be ${derivedMode} for the declared tool effects`);
  if (canonical(pack.security.credentialRefs) !== canonical(credentialRefs)) errors.push("$.security.credentialRefs: must equal the union of tool binding credential references");
  if (canonical(pack.security.networkDestinations) !== canonical(networkDestinations)) errors.push("$.security.networkDestinations: must equal the union of tool binding destinations");
  if (["none", "brokered"].includes(pack.security.networkMode) && networkDestinations.length !== 0) errors.push("$.security.networkDestinations: non-egress pack cannot declare destinations");
  if (["public", "private-allowlist"].includes(pack.security.networkMode) && networkDestinations.length === 0) errors.push("$.security.networkDestinations: egress-capable pack must declare destinations");
  return errors;
}

export function validateWorkCapabilityToolCatalogV2(catalog) {
  const errors = hasSchemaErrors(catalog, schemas.capabilityToolCatalogV2);
  if (errors.length) return errors;
  if (Date.parse(catalog.expiresAt) <= Date.parse(catalog.createdAt)) errors.push("$.expiresAt: catalog must expire after creation");
  const names = catalog.tools.map((tool) => tool.name), exposed = catalog.tools.map((tool) => tool.exposedName);
  if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push("$.tools: signed names must be uniquely sorted");
  if (new Set(exposed).size !== exposed.length) errors.push("$.tools: exposed names must be unique");
  for (const tool of catalog.tools) {
    for (const [field, digest] of [["inputSchema", "inputSchemaSha256"], ["outputSchema", "outputSchemaSha256"]]) {
      if (createHash("sha256").update(canonical(tool[field])).digest("hex") !== tool[digest]) errors.push(`$.tools.${tool.name}.${digest}: schema hash differs`);
      validateCapabilityToolSchema(tool[field], `$.tools.${tool.name}.${field}`, errors);
    }
    if (tool.inputSchema.type !== "object" || tool.inputSchema.additionalProperties !== false) errors.push(`$.tools.${tool.name}.inputSchema: root must be a closed object`);
    if (tool.outputSchema.type !== "object" || tool.outputSchema.additionalProperties !== false) errors.push(`$.tools.${tool.name}.outputSchema: root must be a closed object`);
    validateV2BindingSummary(tool, `$.tools.${tool.name}`, errors);
  }
  return errors;
}

export function validateWorkCapabilityControllerPolicyV2(policy) {
  const errors = hasSchemaErrors(policy, schemas.capabilityControllerPolicyV2);
  if (errors.length) return errors;
  if (Number(policy.policyId.split("-")[1]) !== Date.parse(policy.createdAt)) errors.push("$.policyId: identity time must equal createdAt");
  if (Date.parse(policy.expiresAt) <= Date.parse(policy.createdAt)) errors.push("$.expiresAt: controller policy must expire after creation");
  if (canonical(policy.profiles) !== canonical([...policy.profiles].sort())) errors.push("$.profiles: profiles must use canonical order");
  const packKeys = policy.packs.map((pack) => `${pack.id}@${pack.version}`);
  if (new Set(packKeys).size !== packKeys.length || canonical(packKeys) !== canonical([...packKeys].sort())) errors.push("$.packs: exact pack identities must be uniquely sorted");
  for (const pack of policy.packs) {
    const names = pack.tools.map((tool) => tool.name);
    if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push(`$.packs.${pack.id}.tools: names must be uniquely sorted`);
    if (canonical(pack.classifications) !== canonical([...pack.classifications].sort((left, right) => classificationRank[left] - classificationRank[right]))) errors.push(`$.packs.${pack.id}.classifications: values must use canonical order`);
    for (const tool of pack.tools) validateV2BindingSummary(tool, `$.packs.${pack.id}.tools.${tool.name}`, errors);
  }
  return errors;
}

export function validateWorkCapabilityJobAuthorizationV2(authorization) {
  const errors = hasSchemaErrors(authorization, schemas.capabilityJobAuthorizationV2);
  if (errors.length) return errors;
  if (Number(authorization.authorizationId.split("-")[1]) !== Date.parse(authorization.authorizedAt)) errors.push("$.authorizationId: identity time must equal authorizedAt");
  if (Date.parse(authorization.expiresAt) <= Date.parse(authorization.authorizedAt)) errors.push("$.expiresAt: job authorization must expire after issue");
  const names = authorization.tools.map((tool) => tool.name);
  if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push("$.tools: names must be uniquely sorted");
  if (authorization.watchdog.maxToolCalls !== authorization.maxSessions) errors.push("$.watchdog.maxToolCalls: must equal the authorized session budget");
  for (const tool of authorization.tools) validateV2BindingSummary(tool, `$.tools.${tool.name}`, errors);
  return errors;
}

export function validateWorkCapabilityToolRequestV2(request) {
  const errors = hasSchemaErrors(request, schemas.capabilityToolRequestV2);
  if (errors.length) return errors;
  if (Number(request.requestId.split("-")[1]) !== Date.parse(request.createdAt)) errors.push("$.requestId: identity time must equal createdAt");
  validateV2BindingSummary(request, "$.bindingSummary", errors);
  return errors;
}

export function validateWorkCapabilityGrantV2(grant) {
  const errors = hasSchemaErrors(grant, schemas.capabilityGrantV2);
  if (errors.length) return errors;
  if (Date.parse(grant.expiresAt) <= Date.parse(grant.issuedAt)) errors.push("$.expiresAt: grant must expire after issue");
  const names = grant.tools.map((tool) => tool.name);
  if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push("$.tools: tool identities must be uniquely sorted");
  for (const tool of grant.tools) validateV2BindingSummary(tool, `$.tools.${tool.name}`, errors);
  return errors;
}

export function validateWorkCapabilityOperationalGrantV2(grant) {
  const errors = hasSchemaErrors(grant, schemas.operationalGrantV2);
  if (errors.length) return errors;
  if (Date.parse(grant.expiresAt) <= Date.parse(grant.issuedAt)) errors.push("$.expiresAt: grant must expire after issue");
  if (grant.idempotencyKey !== grant.operation.id) errors.push("$.idempotencyKey: must equal the single-use operation identity");
  if (grant.classification.input !== grant.dataClassification) errors.push("$.classification.input: must equal the authorized data classification");
  if (grant.classification.output !== grant.dataClassification) errors.push("$.classification.output: must equal the authorized data classification");
  if (grant.operation.lane === "filesystem") {
    if (grant.tool.effectClass !== "workspace") errors.push("$.tool.effectClass: filesystem lane must use workspace effect");
    if (grant.tool.targetClass !== "local-filesystem") errors.push("$.tool.targetClass: filesystem lane must target local-filesystem");
    if (grant.egress !== "none") errors.push("$.egress: filesystem lane must have no egress");
    if (!grant.contentSha256) errors.push("$.contentSha256: filesystem lane must carry content hash");
  }
  if (grant.operation.lane === "public-retrieval") {
    if (grant.tool.effectClass !== "brokered-network") errors.push("$.tool.effectClass: public-retrieval lane must use brokered-network effect");
    if (grant.tool.targetClass !== "public-api") errors.push("$.tool.targetClass: public-retrieval lane must target public-api");
    if (grant.egress !== "public") errors.push("$.egress: public-retrieval lane must declare public egress");
    if (!grant.retrievalInput) errors.push("$.retrievalInput: public-retrieval lane must carry retrieval input");
    else if (grant.retrievalInput.maxSourcesToFetch > grant.retrievalInput.maxResults) errors.push("$.retrievalInput.maxSourcesToFetch: cannot exceed maxResults");
  }
  if (grant.operation.lane === "ssh-hostname") {
    if (grant.tool.effectClass !== "read-only") errors.push("$.tool.effectClass: SSH hostname lane must use read-only effect");
    if (grant.tool.targetClass !== "ssh") errors.push("$.tool.targetClass: SSH hostname lane must target SSH");
    if (grant.egress !== "private-allowlist") errors.push("$.egress: SSH hostname lane must declare private-allowlist egress");
    if (grant.approvalMode !== "owner-signed" || !grant.approvalSha256) errors.push("$.approvalMode: SSH hostname lane must carry an owner-signed approval binding");
    if (grant.credentialRefs.length !== 1) errors.push("$.credentialRefs: SSH hostname lane must bind one protected credential reference");
  }
  return errors;
}

export function validateWorkCapabilityOperationalRuntimeRequestV2(request) {
  return hasSchemaErrors(request, schemas.operationalRuntimeRequestV2);
}

export function validateWorkCapabilityOperationalRuntimeResultV2(result) {
  return hasSchemaErrors(result, schemas.operationalRuntimeResultV2);
}

export function validateWorkCapabilitySshApprovalV2(approval) {
  const errors = hasSchemaErrors(approval, schemas.sshApprovalV2);
  if (errors.length) return errors;
  if (Date.parse(approval.expiresAt) <= Date.parse(approval.issuedAt)) errors.push("$.expiresAt: SSH approval must expire after issue");
  return errors;
}

export function validateWorkCapabilityQueueRequestV2(request) {
  const errors = hasSchemaErrors(request, schemas.queueRequestV2);
  if (errors.length) return errors;
  if (Number(request.requestId.split("-")[1]) !== Date.parse(request.createdAt)) errors.push("$.requestId: identity time must equal createdAt");
  if (request.relativePath !== undefined && request.relativePath.split("/").some((segment) => segment === "" || segment === "." || segment === "..")) errors.push("$.relativePath: durable v2 queue accepts a single create-only filename directly in the trusted workspace root only");
  return errors;
}

export function validateWorkCapabilityQueueResponseV2(response) {
  return hasSchemaErrors(response, schemas.queueResponseV2);
}

export function validateWorkCapabilityLeaseV2(lease) {
  const errors = hasSchemaErrors(lease, schemas.capabilityLeaseV2);
  if (errors.length) return errors;
  if (Date.parse(lease.expiresAt) <= Date.parse(lease.issuedAt)) errors.push("$.expiresAt: lease must expire after issue");
  const names = lease.grantedCapabilities.tools.map((tool) => tool.name);
  if (new Set(names).size !== names.length || canonical(names) !== canonical([...names].sort())) errors.push("$.grantedCapabilities.tools: tool identities must be uniquely sorted");
  for (const tool of lease.grantedCapabilities.tools) validateV2BindingSummary(tool, `$.grantedCapabilities.tools.${tool.name}`, errors);
  const network = lease.grantedCapabilities.network;
  if (network.mode === "private-allowlist" && network.destinations.length === 0) errors.push("$.grantedCapabilities.network.destinations: private-allowlist requires explicit destinations");
  if (["none", "brokered"].includes(network.mode) && network.destinations.length !== 0) errors.push("$.grantedCapabilities.network.destinations: mode cannot carry direct destinations");
  if (network.mode === "brokered" && network.services.length === 0) errors.push("$.grantedCapabilities.network.services: brokered network requires at least one broker service");
  if (network.mode !== "brokered" && network.mode !== "none" && network.services.length !== 0) errors.push("$.grantedCapabilities.network.services: non-brokered network cannot carry broker services");
  return errors;
}

export function validateWorkCapabilityRuntimeV2(runtime) {
  const errors = hasSchemaErrors(runtime, schemas.capabilityRuntimeV2);
  if (errors.length) return errors;
  if (Date.parse(runtime.completedAt) < Date.parse(runtime.startedAt)) errors.push("$.completedAt: runtime must complete after start");
  if (runtime.enabled !== false) errors.push("$.enabled: v2 runtime is not enabled");
  return errors;
}

export function validateWorkCapabilityConsumptionV2(consumption) {
  return hasSchemaErrors(consumption, schemas.capabilityConsumptionV2);
}

export function validateWorkKnowledgeIngestion(ingestion) {
  const errors = hasSchemaErrors(ingestion, schemas.knowledgeIngestion);
  if (errors.length) return errors;
  if (Date.parse(ingestion.expiresAt) <= Date.parse(ingestion.issuedAt) || Date.parse(ingestion.expiresAt) - Date.parse(ingestion.issuedAt) > 300000 || Date.parse(ingestion.consent.approvedAt) > Date.parse(ingestion.issuedAt)) errors.push("$.expiresAt: ingestion timing is invalid");
  if (ingestion.consent.approverId !== ingestion.ownerId) errors.push("$.consent.approverId: exact owner approval is required");
  if (Date.parse(ingestion.provenance.capturedAt) > Date.parse(ingestion.issuedAt)) errors.push("$.provenance.capturedAt: source cannot be captured after authorization");
  if (ingestion.retention.deleteAfter !== null && Date.parse(ingestion.retention.deleteAfter) <= Date.parse(ingestion.issuedAt)) errors.push("$.retention.deleteAfter: retention must extend beyond authorization");
  return errors;
}

export function validateWorkKnowledgeSource(source) {
  const errors = hasSchemaErrors(source, schemas.knowledgeSource);
  if (errors.length) return errors;
  if (Date.parse(source.provenance.capturedAt) > Date.parse(source.ingestedAt)) errors.push("$.provenance.capturedAt: source cannot be captured after ingestion");
  for (const [index, chunk] of source.chunks.entries()) {
    if (chunk.index !== index || chunk.chunkId !== `${source.sourceId}-${String(index).padStart(6, "0")}` || chunk.fileName !== `${String(index).padStart(6, "0")}.json` || chunk.endCodePoint <= chunk.startCodePoint) errors.push(`$.chunks[${index}]: chunk identity or range is invalid`);
    if (canonical(chunk.termHmacs) !== canonical([...chunk.termHmacs].sort())) errors.push(`$.chunks[${index}].termHmacs: terms must use canonical order`);
    if (index > 0 && chunk.startCodePoint >= source.chunks[index - 1].endCodePoint) errors.push(`$.chunks[${index}]: deterministic chunks must overlap`);
  }
  return errors;
}

export function validateWorkKnowledgeQuery(query) {
  const errors = hasSchemaErrors(query, schemas.knowledgeQuery);
  if (errors.length) return errors;
  if (Date.parse(query.expiresAt) <= Date.parse(query.issuedAt) || Date.parse(query.expiresAt) - Date.parse(query.issuedAt) > 300000) errors.push("$.expiresAt: query lifetime is invalid");
  return errors;
}

export function validateWorkKnowledgeRetrieval(retrieval) {
  const errors = hasSchemaErrors(retrieval, schemas.knowledgeRetrieval);
  if (errors.length) return errors;
  if ((retrieval.reason === "matched") !== (retrieval.results.length > 0)) errors.push("$.reason: result reason is incoherent");
  for (let index = 1; index < retrieval.results.length; index += 1) {
    const previous = retrieval.results[index - 1], current = retrieval.results[index];
    if (current.scoreBps > previous.scoreBps || current.scoreBps === previous.scoreBps && `${current.sourceId}:${current.chunkId}` < `${previous.sourceId}:${previous.chunkId}`) errors.push("$.results: results are not in deterministic rank order");
  }
  return errors;
}

export function validateWorkKnowledgeDeletion(deletion) {
  const errors = hasSchemaErrors(deletion, schemas.knowledgeDeletion);
  if (errors.length) return errors;
  if (Date.parse(deletion.expiresAt) <= Date.parse(deletion.issuedAt) || Date.parse(deletion.expiresAt) - Date.parse(deletion.issuedAt) > 300000) errors.push("$.expiresAt: deletion lifetime is invalid");
  if (deletion.status === "authorized" && (deletion.deletedAt !== null || deletion.sourceContentSha256 !== null) || deletion.status === "deleted" && (deletion.deletedAt === null || deletion.sourceContentSha256 === null)) errors.push("$.status: deletion state is incoherent");
  return errors;
}

export function validateWorkKnowledgeKeyRotation(rotation) {
  const errors = hasSchemaErrors(rotation, schemas.knowledgeKeyRotation);
  if (errors.length) return errors;
  if (rotation.oldKeyCheckSha256 === rotation.newKeyCheckSha256) errors.push("$.newKeyCheckSha256: rotation must change key identity");
  for (let index = 1; index < rotation.sources.length; index += 1) {
    const previous = rotation.sources[index - 1], current = rotation.sources[index];
    if (`${current.clientId}:${current.sourceId}` <= `${previous.clientId}:${previous.sourceId}`) errors.push("$.sources: entries must be uniquely sorted");
  }
  return errors;
}

export function validateWorkKnowledgeReconciliation(reconciliation) {
  const errors = hasSchemaErrors(reconciliation, schemas.knowledgeReconciliation);
  if (errors.length) return errors;
  for (const name of ["applied", "alreadyPresent"]) {
    for (let index = 1; index < reconciliation[name].length; index += 1) {
      const previous = reconciliation[name][index - 1], current = reconciliation[name][index];
      if (`${current.clientId}:${current.sourceId}` <= `${previous.clientId}:${previous.sourceId}`) errors.push(`$.${name}: entries must be uniquely sorted`);
    }
  }
  const applied = new Set(reconciliation.applied.map((entry) => `${entry.clientId}:${entry.sourceId}`));
  if (reconciliation.alreadyPresent.some((entry) => applied.has(`${entry.clientId}:${entry.sourceId}`))) errors.push("$.alreadyPresent: entry is also applied");
  return errors;
}

export function validateWorkOperatorStatus(status) {
  const errors = hasSchemaErrors(status, schemas.operatorStatus);
  if (errors.length) return errors;
  const generatedAt = Date.parse(status.generatedAt);
  if (Number(status.projectionId.split("-")[1]) !== generatedAt) errors.push("$.projectionId: identity time must equal generatedAt");
  const goalUpdatedAt = Date.parse(status.goal.updatedAt);
  if (goalUpdatedAt > generatedAt) errors.push("$.goal.updatedAt: goal update follows snapshot generation");
  const goalProgress = status.goal.progress;
  if (
    goalProgress.milestonesCompleted > goalProgress.milestonesTotal
    || goalProgress.jobsStarted < goalProgress.milestonesCompleted
    || goalProgress.jobsStarted > goalProgress.milestonesTotal
    || goalProgress.failures !== status.goal.usage.failures
  ) errors.push("$.goal.progress: durable goal progress is incoherent");
  const nextActions = {
    ready: new Set(["dispatch-child", "record-completed", "fail-closed"]),
    running: new Set(["recover-or-continue-child"]),
    "waiting-authority": new Set(["wait-for-child-authority"]),
    paused: new Set(["paused"]),
    "recovery-inconclusive": new Set(["terminal"]), completed: new Set(["terminal"]), failed: new Set(["terminal"]),
    cancelled: new Set(["terminal"]), "budget-exhausted": new Set(["terminal"]), "no-progress": new Set(["terminal"]),
  };
  if (!nextActions[status.goal.state]?.has(status.goal.nextAction)) errors.push("$.goal.nextAction: action differs from durable goal state");
  if (status.goal.state === "completed" && goalProgress.milestonesCompleted !== goalProgress.milestonesTotal) errors.push("$.goal.progress: completed goal lacks every milestone");
  const degradedGoalStates = new Set(["recovery-inconclusive", "failed", "budget-exhausted", "no-progress"]);
  if (degradedGoalStates.has(status.goal.state) && status.controllerState !== "degraded") errors.push("$.controllerState: terminal goal attention cannot be presented as healthy");
  if (status.controllerState === "busy" && status.goal.state !== "running") errors.push("$.controllerState: busy controller requires a running durable goal");
  const handles = status.sessions.map((session) => session.sessionHandle);
  if (new Set(handles).size !== handles.length) errors.push("$.sessions: display handles must be unique");
  for (let index = 1; index < status.sessions.length; index += 1) {
    const previous = status.sessions[index - 1], current = status.sessions[index];
    if (Date.parse(current.updatedAt) > Date.parse(previous.updatedAt) || current.updatedAt === previous.updatedAt && current.sessionHandle < previous.sessionHandle) errors.push("$.sessions: sessions must use deterministic recent-first order");
  }
  const serviceOrder = ["controller", "local-model", "worker", "verifier", "research", "data-lab", "knowledge-vault", "capability-adapter"];
  const serviceIds = status.services.map((service) => service.id);
  if (new Set(serviceIds).size !== serviceIds.length || canonical(serviceIds) !== canonical([...serviceIds].sort((a, b) => serviceOrder.indexOf(a) - serviceOrder.indexOf(b)))) errors.push("$.services: service identities must be uniquely ordered");
  for (const service of status.services) if (Date.parse(service.observedAt) > generatedAt) errors.push(`$.services.${service.id}.observedAt: observation follows snapshot generation`);
  for (const session of status.sessions) {
    if (Date.parse(session.updatedAt) < Date.parse(session.startedAt)) errors.push(`$.sessions.${session.sessionHandle}: update precedes start`);
    if (Date.parse(session.updatedAt) > generatedAt) errors.push(`$.sessions.${session.sessionHandle}: update follows snapshot generation`);
    if (session.progress.criteriaPassing + session.progress.criteriaFailing !== session.progress.criteriaTotal || session.progress.iteration > session.progress.maxIterations) errors.push(`$.sessions.${session.sessionHandle}.progress: progress is incoherent`);
    const artifactKinds = session.artifacts.kinds.map((item) => item.kind);
    if (session.artifacts.kinds.reduce((sum, item) => sum + item.count, 0) !== session.artifacts.count || new Set(artifactKinds).size !== artifactKinds.length || canonical(artifactKinds) !== canonical([...artifactKinds].sort())) errors.push(`$.sessions.${session.sessionHandle}.artifacts: artifact summary is incoherent`);
    if (session.boundaryState.state === "within-authority" && session.boundaryState.requestedExpansion !== "none" || session.boundaryState.state === "waiting-approval" && session.boundaryState.requestedExpansion === "none") errors.push(`$.sessions.${session.sessionHandle}.boundaryState: boundary request is incoherent`);
    if (session.state === "completed" && (session.verification !== "pass" || session.progress.criteriaPassing !== session.progress.criteriaTotal || session.progress.criteriaFailing !== 0)) errors.push(`$.sessions.${session.sessionHandle}.verification: completion requires every criterion and independent verification to pass`);
    const eventIds = new Set();
    for (let index = 0; index < session.activity.length; index += 1) {
      const event = session.activity[index];
      if (eventIds.has(event.eventId)) errors.push(`$.sessions.${session.sessionHandle}.activity: event identities must be unique`);
      eventIds.add(event.eventId);
      if (Date.parse(event.at) < Date.parse(session.startedAt) || Date.parse(event.at) > Date.parse(session.updatedAt)) errors.push(`$.sessions.${session.sessionHandle}.activity: event is outside the session time boundary`);
      if (index > 0 && Date.parse(event.at) > Date.parse(session.activity[index - 1].at)) errors.push(`$.sessions.${session.sessionHandle}.activity: events must be recent-first`);
    }
    if (["cleanup-failed", "recovery-inconclusive"].includes(session.state)) {
      const latest = session.activity[0];
      if (latest?.summaryCode !== session.state || latest.outcome !== "failed") errors.push(`$.sessions.${session.sessionHandle}.activity: recovery state requires a matching latest failed event`);
    }
  }
  return errors;
}

export function validateWorkCodexPolicy(policy) {
  const errors = hasSchemaErrors(policy, schemas.codexPolicy);
  if (errors.length) return errors;
  if (policy.provider.authMode === "chatgpt" && policy.provider.billing.mode !== "subscription") errors.push("$.provider.billing: ChatGPT auth cannot claim API metering");
  if (policy.provider.authMode === "api-key" && policy.provider.billing.mode !== "metered") errors.push("$.provider.billing: API-key auth must use metered API billing");
  const expectedCustody = policy.provider.authMode === "chatgpt"
    ? { mode: "broker-private-chatgpt-auth-cache", mutableRefresh: true, runtimeAccess: "broker-owned-codex-home", maxBytes: 262144, credentialId: "codex-chatgpt-auth" }
    : { mode: "broker-private-api-key-file", mutableRefresh: false, runtimeAccess: "stdin-only-ephemeral-login", maxBytes: 8192, credentialId: "codex-api-key" };
  if (policy.credentialCustody.mode !== expectedCustody.mode || policy.credentialCustody.mutableRefresh !== expectedCustody.mutableRefresh || policy.credentialCustody.runtimeAccess !== expectedCustody.runtimeAccess || policy.credentialCustody.maxBytes !== expectedCustody.maxBytes || policy.credentialCustody.credentialId !== expectedCustody.credentialId) errors.push("$.credentialCustody: custody mode differs from provider authentication");
  const expectedHosts = policy.provider.authMode === "chatgpt" ? ["chatgpt.com"] : ["api.openai.com"];
  if (canonical(policy.transport.allowedHosts) !== canonical(expectedHosts)) errors.push("$.transport: transport host differs from provider authentication");
  const expectedClassifications = ["public", "internal", "confidential"].filter((value) => policy.dataPolicy.allowedClassifications.includes(value));
  if (canonical(policy.dataPolicy.allowedClassifications) !== canonical(expectedClassifications)) errors.push("$.dataPolicy.allowedClassifications: classifications must use canonical order");
  for (const [name, task] of Object.entries(policy.taskClasses)) {
    const expectedModes = ["structural-only", "owner-authorized-content"].filter((value) => task.allowedEgressModes.includes(value));
    if (canonical(task.allowedEgressModes) !== canonical(expectedModes)) errors.push(`$.taskClasses.${name}.allowedEgressModes: modes must use canonical order`);
  }
  if (policy.dataPolicy.maxTotalDocumentBytes < policy.dataPolicy.maxDocumentBytes) errors.push("$.dataPolicy.maxTotalDocumentBytes: total must admit at least one maximum-size document");
  return errors;
}

export function validateWorkCodexRequest(request) {
  const errors = hasSchemaErrors(request, schemas.codexRequest);
  if (errors.length) return errors;
  const created = Date.parse(request.createdAt), expires = Date.parse(request.expiresAt);
  if (Number(request.requestId.split("-")[1]) !== created) errors.push("$.requestId: identity time must equal createdAt");
  if (expires <= created || expires - created > 30 * 60 * 1000) errors.push("$.expiresAt: request lifetime must be positive and at most 30 minutes");
  const categoryOrder = [...request.dataCategories].sort();
  if (canonical(request.dataCategories) !== canonical(categoryOrder)) errors.push("$.dataCategories: categories must use canonical order");
  const reasonOrder = [...request.localAttempt.reasonCodes].sort();
  if (canonical(request.localAttempt.reasonCodes) !== canonical(reasonOrder)) errors.push("$.localAttempt.reasonCodes: reasons must use canonical order");
  const documentIds = request.documents.map((document) => document.documentId);
  if (new Set(documentIds).size !== documentIds.length || canonical(documentIds) !== canonical([...documentIds].sort())) errors.push("$.documents: documents must have unique canonical identifier order");
  for (const document of request.documents) if (createHash("sha256").update(document.content, "utf8").digest("hex") !== document.contentSha256) errors.push(`$.documents.${document.documentId}.contentSha256: hash differs from exact content`);
  const termKeys = request.sensitiveTerms.map((term) => `${term.kind}\0${term.value.normalize("NFC").toLocaleLowerCase("en-US")}`);
  if (new Set(termKeys).size !== termKeys.length || canonical(termKeys) !== canonical([...termKeys].sort())) errors.push("$.sensitiveTerms: terms must be unique and canonically ordered");
  if (request.localAttempt.outcome === "completed-sufficient" && canonical(request.localAttempt.reasonCodes) !== canonical(["local-sufficient"])) errors.push("$.localAttempt: locally sufficient work requires only local-sufficient");
  if (request.localAttempt.outcome !== "completed-sufficient" && request.localAttempt.reasonCodes.includes("local-sufficient")) errors.push("$.localAttempt: remote work cannot follow a locally sufficient outcome");
  if (request.localAttempt.outcome === "failed-after-retries" && !request.localAttempt.reasonCodes.includes("repeated-failure")) errors.push("$.localAttempt: failed retries require repeated-failure");
  if (request.localAttempt.outcome === "capability-unavailable" && !request.localAttempt.reasonCodes.includes("capability-gap")) errors.push("$.localAttempt: unavailable capability requires capability-gap");
  const structuralKinds = new Set(["structure", "interface", "patch-outline", "test-failure", "spec", "schema"]);
  if (request.egressMode === "structural-only" && request.documents.some((document) => !structuralKinds.has(document.kind))) errors.push("$.documents: structural-only egress cannot contain source, diff, or raw build-log documents");
  const hasCode = request.documents.some((document) => ["code-source", "diff"].includes(document.kind));
  if (hasCode && !request.dataCategories.includes("proprietary-code")) errors.push("$.dataCategories: source or diff content must declare proprietary-code");
  return errors;
}

export function validateWorkCodexCapsule(capsule) {
  const errors = hasSchemaErrors(capsule, schemas.codexCapsule);
  if (errors.length) return errors;
  const created = Date.parse(capsule.createdAt);
  if (Number(capsule.capsuleId.split("-")[1]) !== created) errors.push("$.capsuleId: identity time must equal createdAt");
  const expectedIds = capsule.documents.map((_document, index) => `DOC_${String(index + 1).padStart(3, "0")}`);
  if (canonical(capsule.documents.map((document) => document.documentId)) !== canonical(expectedIds)) errors.push("$.documents: capsule document identities must be sequential and synthetic");
  const structuralKinds = new Set(["structure", "interface", "patch-outline", "test-failure", "spec", "schema"]);
  if (capsule.egressMode === "structural-only" && capsule.documents.some((document) => !structuralKinds.has(document.kind))) errors.push("$.documents: structural-only capsule contains raw source, diff, or build-log content");
  return errors;
}

export function validateWorkCodexOutput(output) {
  const errors = hasSchemaErrors(output, schemas.codexOutput);
  if (errors.length) return errors;
  for (const [index, proposal] of output.proposals.entries()) {
    if (["none", "delete"].includes(proposal.operation) && proposal.content !== "") errors.push(`$.proposals[${index}].content: ${proposal.operation} cannot carry replacement content`);
    if (["replace", "insert"].includes(proposal.operation) && proposal.content.length === 0) errors.push(`$.proposals[${index}].content: ${proposal.operation} requires proposed content`);
  }
  return errors;
}

export function validateWorkCodexAuthorization(authorization) {
  const errors = hasSchemaErrors(authorization, schemas.codexAuthorization);
  if (errors.length) return errors;
  const created = Date.parse(authorization.createdAt), expires = Date.parse(authorization.expiresAt);
  if (Number(authorization.authorizationId.split("-")[1]) !== created) errors.push("$.authorizationId: identity time must equal createdAt");
  if (created < Number(authorization.planId.split("-")[1])) errors.push("$.createdAt: authorization cannot precede the exact plan");
  if (expires <= created || expires - created > 15 * 60 * 1000) errors.push("$.expiresAt: authorization lifetime must be positive and at most fifteen minutes");
  const expectedBilling = authorization.provider.authMode === "chatgpt" ? ["chatgpt-plan-or-credits-not-api-billing", "chatgpt-plan-or-credits"] : ["separately-billed-api-platform", "separately-billed-api-platform"];
  if (authorization.provider.billingBoundary !== expectedBilling[0] || authorization.confirmation.billingAcknowledgment !== expectedBilling[1]) errors.push("$.confirmation.billingAcknowledgment: acknowledgment differs from the exact provider billing boundary");
  return errors;
}

export function validateWorkCodexAuthenticationEvidence(evidence) {
  const errors = hasSchemaErrors(evidence, schemas.codexAuthenticationEvidence);
  if (errors.length) return errors;
  const issued = Date.parse(evidence.issuedAt), expires = Date.parse(evidence.expiresAt);
  if (Number(evidence.evidenceId.split("-")[1]) !== issued) errors.push("$.evidenceId: identity time must equal issuedAt");
  if (expires <= issued || expires - issued > 5 * 60 * 1000) errors.push("$.expiresAt: MFA evidence lifetime must be positive and at most five minutes");
  const expectedBilling = evidence.provider.authMode === "chatgpt" ? ["chatgpt-plan-or-credits-not-api-billing", "chatgpt-plan-or-credits"] : ["separately-billed-api-platform", "separately-billed-api-platform"];
  if (evidence.provider.billingBoundary !== expectedBilling[0] || evidence.confirmation.billingAcknowledgment !== expectedBilling[1]) errors.push("$.confirmation.billingAcknowledgment: acknowledgment differs from the exact provider billing boundary");
  return errors;
}

export function validateWorkCodexAuthenticationConsumption(consumption) {
  const errors = hasSchemaErrors(consumption, schemas.codexAuthenticationConsumption);
  if (errors.length) return errors;
  const consumed = Date.parse(consumption.consumedAt), evidenceIssued = Number(consumption.evidenceId.split("-")[1]), authorizationIssued = Number(consumption.authorizationId.split("-")[1]);
  if (consumed < evidenceIssued || consumed !== authorizationIssued) errors.push("$.consumedAt: consumption must equal authorization creation and cannot precede evidence");
  return errors;
}

export function validateWorkCodexCredentialCustody(receipt) {
  const errors = hasSchemaErrors(receipt, schemas.codexCredentialCustody);
  if (errors.length) return errors;
  const inspected = Date.parse(receipt.inspectedAt);
  if (Number(receipt.receiptId.split("-")[1]) !== inspected) errors.push("$.receiptId: identity time must equal inspectedAt");
  const expected = receipt.authMode === "chatgpt" ? ["broker-private-chatgpt-auth-cache", true] : ["broker-private-api-key-file", false];
  if (receipt.custodyMode !== expected[0] || receipt.mutableRefresh !== expected[1]) errors.push("$.custodyMode: receipt differs from authentication mode");
  return errors;
}

export function validateWorkCodexEgressPolicy(policy) {
  const errors = hasSchemaErrors(policy, schemas.codexEgressPolicy);
  if (errors.length) return errors;
  if (canonical(policy.allowedHosts) !== canonical([...policy.allowedHosts].sort())) errors.push("$.allowedHosts: hosts must use canonical order");
  return errors;
}

export function validateWorkProviderProfile(profile) {
  const errors = hasSchemaErrors(profile, schemas.providerProfile);
  if (errors.length) return errors;
  if (canonical(profile.allowedHosts) !== canonical([...profile.allowedHosts].sort())) errors.push("$.allowedHosts: hosts must use canonical order");
  if (profile.remote) {
    const base = new URL(profile.baseUrl);
    const endpoint = `${base.hostname.toLowerCase()}:${base.port || "443"}`;
    if (!profile.allowedHosts.includes(endpoint)) errors.push("$.allowedHosts: exact base URL host and port are not admitted");
  }
  if (profile.reasoning.mode === "kimi-reasoning-content" && (profile.id !== "moonshot-kimi" || !profile.reasoning.replayCompleteAssistantMessage)) errors.push("$.reasoning: Kimi reasoning requires complete assistant-message replay");
  return errors;
}

export function validateWorkProviderReceipt(receipt) {
  const errors = hasSchemaErrors(receipt, schemas.providerReceipt);
  if (errors.length) return errors;
  if (Number(receipt.receiptId.split("-")[1]) !== Date.parse(receipt.recordedAt)) errors.push("$.receiptId: identity time must equal recordedAt");
  const statuses = { admitted: ["accepted"], completed: ["succeeded"], failed: ["denied", "error"], paused: ["paused"] };
  if (!statuses[receipt.phase].includes(receipt.status)) errors.push("$.status: status contradicts the receipt phase");
  return errors;
}

export function validateWorkProviderPrivatePolicy(policy) {
  const errors = hasSchemaErrors(policy, schemas.providerPrivatePolicy);
  if (errors.length) return errors;
  if (Number(policy.policyId.split("-")[1]) !== Date.parse(policy.createdAt)) errors.push("$.policyId: identity time must equal createdAt");
  if (canonical(policy.transport.allowedHosts) !== canonical([...policy.transport.allowedHosts].sort())) errors.push("$.transport.allowedHosts: hosts must use canonical order");
  if (canonical(policy.dataPolicy.allowedClassifications) !== canonical([...policy.dataPolicy.allowedClassifications].sort())) errors.push("$.dataPolicy.allowedClassifications: values must use canonical order");
  if (canonical(policy.dataPolicy.neverEgressCategories) !== canonical([...policy.dataPolicy.neverEgressCategories].sort())) errors.push("$.dataPolicy.neverEgressCategories: values must use canonical order");
  if (policy.budgets.maxEstimatedCostMicrosPerDay < policy.budgets.maxEstimatedCostMicrosPerRun) errors.push("$.budgets: daily cost ceiling cannot be below one run ceiling");
  return errors;
}

export function validateWorkProviderCustodyReceipt(receipt) {
  const errors = hasSchemaErrors(receipt, schemas.providerCustodyReceipt);
  if (errors.length) return errors;
  if (Number(receipt.receiptId.split("-")[1]) !== Date.parse(receipt.inspectedAt)) errors.push("$.receiptId: identity time must equal inspectedAt");
  return errors;
}

const SHA256_RE = /^[a-f0-9]{64}$/u;
const SEMANTIC_BINDING_FIELDS = ["decisionId", "decisionSha256", "requestSha256", "policySha256", "providerProfileSha256", "privatePolicySha256", "qualificationSha256", "model"];
const SEMANTIC_ONLY_BINDING_FIELDS = ["decisionId", "decisionSha256", "policySha256", "providerProfileSha256", "privatePolicySha256", "qualificationSha256"];

export function validateWorkProviderRunLedger(ledger) {
  const errors = hasSchemaErrors(ledger, schemas.providerRunLedger);
  if (errors.length) return errors;
  if (Number(ledger.runId.split("-")[1]) !== Date.parse(ledger.createdAt)) errors.push("$.runId: identity time must equal createdAt");
  if (!ledger.binding || !["semantic", "connectivity-smoke", "qualification-trial"].includes(ledger.binding.kind)) errors.push("$.binding: provider run must declare a semantic, connectivity-smoke, or qualification-trial binding");
  if (ledger.binding?.kind === "semantic") {
    for (const field of SEMANTIC_BINDING_FIELDS) {
      if (ledger.binding[field] === undefined) errors.push(`$.binding.${field}: semantic run binding is incomplete`);
      if (field === "privatePolicySha256" && ledger.binding[field] === null) continue;
      if (ledger.binding[field] === null) errors.push(`$.binding.${field}: semantic run binding is incomplete`);
    }
    if (ledger.binding.purpose !== undefined) errors.push("$.binding.purpose: semantic run cannot carry a smoke purpose");
  } else if (ledger.binding?.kind === "connectivity-smoke") {
    if (typeof ledger.binding.purpose !== "string" || ledger.binding.purpose.length < 1) errors.push("$.binding.purpose: smoke run requires an explicit purpose");
    for (const field of SEMANTIC_ONLY_BINDING_FIELDS) {
      if (ledger.binding[field] !== undefined) errors.push(`$.binding.${field}: connectivity-smoke run cannot carry a semantic field`);
    }
  } else if (ledger.binding?.kind === "qualification-trial") {
    if (typeof ledger.binding.corpusVersion !== "string" || ledger.binding.corpusVersion.length < 1 || ledger.binding.corpusVersion.length > 32) errors.push("$.binding.corpusVersion: qualification-trial run requires an exact corpus version");
    if (!SHA256_RE.test(ledger.binding.corpusSha256 ?? "")) errors.push("$.binding.corpusSha256: qualification-trial run requires an exact neutral corpus hash");
    if (!["local-only", "moonshot-kimi", "openai", "anthropic", "openrouter"].includes(ledger.binding.lane)) errors.push("$.binding.lane: qualification-trial lane must be local-only, moonshot-kimi, openai, anthropic, or openrouter");
    if (typeof ledger.binding.model !== "string" || ledger.binding.model.length < 1 || ledger.binding.model.length > 256) errors.push("$.binding.model: qualification-trial run requires an exact model");
    if (!SHA256_RE.test(ledger.binding.providerProfileSha256 ?? "")) errors.push("$.binding.providerProfileSha256: qualification-trial run requires a provider profile hash");
    if (ledger.binding.privatePolicySha256 !== null && !SHA256_RE.test(ledger.binding.privatePolicySha256 ?? "")) errors.push("$.binding.privatePolicySha256: qualification-trial private policy hash is invalid");
    for (const field of ["decisionId", "decisionSha256", "requestSha256", "policySha256", "qualificationSha256", "purpose"]) {
      if (ledger.binding[field] !== undefined) errors.push(`$.binding.${field}: qualification-trial run cannot carry a semantic or smoke field`);
    }
  }
  if (Date.parse(ledger.updatedAt) < Date.parse(ledger.createdAt)) errors.push("$.updatedAt: ledger cannot predate creation");
  if (new Set(ledger.requests.map((request) => request.idempotencyKey)).size !== ledger.requests.length) errors.push("$.requests: idempotency keys must be unique");
  for (const [index, request] of ledger.requests.entries()) {
    const terminal = request.state !== "claimed";
    if (terminal !== (request.settledAt !== null)) errors.push(`$.requests.${index}: only terminal requests have settledAt`);
    const usageComplete = Number.isInteger(request.inputTokens) && Number.isInteger(request.outputTokens);
    if (["succeeded", "budget-exceeded"].includes(request.state) !== usageComplete) errors.push(`$.requests.${index}: successful provider execution requires complete usage`);
    if (!usageComplete && (request.inputTokens !== null || request.outputTokens !== null)) errors.push(`$.requests.${index}: usage must contain both token counts or neither`);
    if (request.responseDisclosed && request.state !== "succeeded") errors.push(`$.requests.${index}: only an in-budget success may disclose a response`);
    if (request.responseModel === null) {
      if (["semantic", "qualification-trial"].includes(ledger.binding.kind) && ["succeeded", "budget-exceeded"].includes(request.state)) errors.push(`$.requests.${index}: semantic or qualification success requires an exact response model`);
    } else if (typeof request.responseModel !== "string" || request.responseModel.length < 1 || request.responseModel.length > 256 || request.responseModel !== ledger.model) {
      errors.push(`$.requests.${index}: response model must equal the pinned run model`);
    }
    if (!["succeeded", "budget-exceeded"].includes(request.state) && request.responseModel !== null) errors.push(`$.requests.${index}: non-success request cannot claim a response model`);
  }
  const totals = {
    requests: ledger.requests.length,
    inputTokens: ledger.requests.reduce((sum, request) => sum + (request.inputTokens ?? 0), 0),
    outputTokens: ledger.requests.reduce((sum, request) => sum + (request.outputTokens ?? 0), 0),
    reportedCostMicros: ledger.requests.reduce((sum, request) => sum + (request.reportedCostMicros ?? 0), 0),
    uncertain: ledger.requests.filter((request) => request.state === "uncertain").length,
  };
  if (canonical(ledger.totals) !== canonical(totals)) errors.push("$.totals: values differ from request records");
  if ((ledger.status === "uncertain") !== (totals.uncertain > 0)) errors.push("$.status: uncertain requests require an uncertain run");
  if (ledger.status === "completed" && ledger.requests.some((request) => ["claimed", "uncertain"].includes(request.state))) errors.push("$.status: completed run has unsettled requests");
  return errors;
}

export function validateWorkProviderRouterRequest(request) {
  const errors = hasSchemaErrors(request, schemas.routerRequest);
  if (errors.length) return errors;
  if (Number(request.requestId.split("-")[1]) !== Date.parse(request.createdAt)) errors.push("$.requestId: identity time must equal createdAt");
  if (request.mode === "explicit-provider" && request.explicitProviderId === undefined) errors.push("$.explicitProviderId: explicit-provider mode requires one requested provider");
  if (request.mode !== "explicit-provider" && request.explicitProviderId !== undefined) errors.push("$.explicitProviderId: only explicit-provider mode may name a provider");
  if (canonical(request.sensitiveCategories) !== canonical([...request.sensitiveCategories].sort())) errors.push("$.sensitiveCategories: values must use canonical order");
  return errors;
}

export function validateWorkProviderRouterQualification(qualification) {
  const errors = hasSchemaErrors(qualification, schemas.routerQualification);
  if (errors.length) return errors;
  if (Number(qualification.qualificationId.split("-")[1]) !== Date.parse(qualification.attestedAt)) errors.push("$.qualificationId: identity time must equal attestedAt");
  if (Date.parse(qualification.expiresAt) <= Date.parse(qualification.attestedAt)) errors.push("$.expiresAt: qualification cannot expire at or before attestation");
  if (qualification.attestationKind === "semantic-capability" && (typeof qualification.model !== "string" || qualification.model.length < 1)) errors.push("$.model: semantic capability requires an exact qualified model");
  if (canonical(qualification.capability.taskClasses) !== canonical([...qualification.capability.taskClasses].sort())) errors.push("$.capability.taskClasses: values must use canonical order");
  return errors;
}

export function validateWorkProviderRouterPolicy(policy) {
  const errors = hasSchemaErrors(policy, schemas.routerPolicy);
  if (errors.length) return errors;
  if (Number(policy.routerPolicyId.split("-")[1]) !== Date.parse(policy.createdAt)) errors.push("$.routerPolicyId: identity time must equal createdAt");
  if (!policy.allowedModes.includes(policy.mode)) errors.push("$.mode: default mode is outside allowedModes");
  if (policy.preference[0] !== "local") errors.push("$.preference: router preference must be local-first");
  if (canonical(policy.allowedModes) !== canonical([...policy.allowedModes].sort())) errors.push("$.allowedModes: values must use canonical order");
  if (canonical(policy.enabledRemoteProviders.map((entry) => entry.providerId)) !== canonical([...policy.enabledRemoteProviders.map((entry) => entry.providerId)].sort())) errors.push("$.enabledRemoteProviders: entries must use canonical provider order");
  if (new Set(policy.enabledRemoteProviders.map((entry) => entry.providerId)).size !== policy.enabledRemoteProviders.length) errors.push("$.enabledRemoteProviders: provider ids must be unique even when their private-policy hashes differ");
  if (policy.mode === "local-only" && (policy.enabledRemoteProviders.length !== 0 || policy.preference.length !== 1 || policy.preference[0] !== "local")) errors.push("$.mode: local-only policy may not enable or prefer a remote provider");
  for (const entry of policy.enabledRemoteProviders) {
    if (!policy.preference.includes(entry.providerId)) errors.push(`$.enabledRemoteProviders: ${entry.providerId} is absent from the router preference`);
  }
  return errors;
}

export function validateWorkProviderRouterDecision(decisionDoc) {
  const errors = hasSchemaErrors(decisionDoc, schemas.routerDecision);
  if (errors.length) return errors;
  if (Number(decisionDoc.decisionId.split("-")[1]) !== Date.parse(decisionDoc.recordedAt)) errors.push("$.decisionId: identity time must equal recordedAt");
  if (decisionDoc.selectionKind === "local" && decisionDoc.selectedProviderId !== "local") errors.push("$.selectedProviderId: a local decision must select local");
  if (decisionDoc.selectionKind === "reject" && decisionDoc.selectedProviderId !== null) errors.push("$.selectedProviderId: a reject decision selects no provider");
  if (decisionDoc.selectionKind === "reject" && decisionDoc.selectedModel !== null) errors.push("$.selectedModel: a reject decision binds no model");
  if (decisionDoc.selectionKind !== "reject" && (typeof decisionDoc.selectedModel !== "string" || decisionDoc.selectedModel.length < 1)) errors.push("$.selectedModel: a non-reject decision must bind the exact selected model");
  if (decisionDoc.selectionKind === "remote" && (decisionDoc.selectedProviderId === null || decisionDoc.selectedProviderId === "local")) errors.push("$.selectedProviderId: a remote decision must select one remote provider");
  const localReasons = ["local-selected", "local-required"];
  const remoteReasons = ["remote-selected", "requested-provider-selected"];
  const rejectReasons = ["explicit-unavailable", "local-incapable", "remote-not-enabled", "remote-unqualified", "remote-expired", "task-class-not-attested", "cost-ceiling-exceeded", "envelope-incompatible", "classification-rejected", "sensitive-data-forced-local", "no-local-provider", "policy-rejected", "request-invalid"];
  if (decisionDoc.selectionKind === "local" && !localReasons.includes(decisionDoc.reasonCode)) errors.push("$.reasonCode: local decision reason contradicts selection");
  if (decisionDoc.selectionKind === "remote" && !remoteReasons.includes(decisionDoc.reasonCode)) errors.push("$.reasonCode: remote decision reason contradicts selection");
  if (decisionDoc.selectionKind === "reject" && !rejectReasons.includes(decisionDoc.reasonCode)) errors.push("$.reasonCode: reject decision reason contradicts selection");
  return errors;
}

export function validateWorkCodexExecutionClaim(claim) {
  const errors = hasSchemaErrors(claim, schemas.codexExecutionClaim);
  if (errors.length) return errors;
  const claimed = Date.parse(claim.claimedAt);
  if (Number(claim.claimId.split("-")[1]) !== claimed) errors.push("$.claimId: identity time must equal claimedAt");
  if (claimed < Number(claim.authorizationId.split("-")[1]) || claimed < Number(claim.planId.split("-")[1])) errors.push("$.claimedAt: claim cannot precede its authorization or plan");
  if (claim.externalProviderTurnAuthorized !== (claim.executionSource === "codex-cli")) errors.push("$.externalProviderTurnAuthorized: mock execution cannot authorize a provider turn and Codex execution must bind one");
  return errors;
}

export function validateWorkCodexResult(result) {
  const errors = hasSchemaErrors(result, schemas.codexResult);
  if (errors.length) return errors;
  const created = Date.parse(result.createdAt);
  if (Number(result.resultId.split("-")[1]) !== created) errors.push("$.resultId: identity time must equal createdAt");
  if (created < Number(result.authorizationId.split("-")[1])) errors.push("$.createdAt: result cannot precede its authorization");
  if (result.invocation.source === "mock" && result.invocation.externalState !== "not-invoked") errors.push("$.invocation.externalState: mock execution cannot claim an external turn");
  if (result.invocation.source === "codex-cli" && result.invocation.externalState === "not-invoked") errors.push("$.invocation.externalState: Codex execution cannot claim no external turn after adapter entry");
  if (result.status === "succeeded" && result.invocation.source === "codex-cli" && result.invocation.externalState !== "invoked-once") errors.push("$.invocation.externalState: successful Codex execution requires one confirmed turn");
  const usageComplete = Number.isInteger(result.usage.inputTokens) && Number.isInteger(result.usage.outputTokens);
  if (result.usage.reported !== usageComplete) errors.push("$.usage: reported usage must contain both token counts and absent usage must contain neither");
  if (!result.usage.reported && (result.usage.inputTokens !== null || result.usage.outputTokens !== null)) errors.push("$.usage: unreported usage must use two null token counts");
  const outputComplete = result.output.storedPrivately && typeof result.output.sanitizedSha256 === "string" && typeof result.output.rehydratedSha256 === "string";
  if ((result.status === "succeeded") !== outputComplete) errors.push("$.output: only successful validated output may be stored and successful output requires both hashes");
  if (result.status !== "succeeded" && (result.output.storedPrivately || result.output.sanitizedSha256 !== null || result.output.rehydratedSha256 !== null)) errors.push("$.output: failed output must not retain content or hashes");
  if (result.status !== "succeeded" && result.output.referencedPlaceholders !== 0) errors.push("$.output.referencedPlaceholders: failed output cannot claim rehydration");
  return errors;
}

export function validateWorkCodexPlan(plan) {
  const errors = hasSchemaErrors(plan, schemas.codexPlan);
  if (errors.length) return errors;
  errors.push(...validateWorkCodexCapsule(plan.capsule).map((error) => `$.capsule ${error}`));
  const created = Date.parse(plan.createdAt), expires = Date.parse(plan.expiresAt);
  if (Number(plan.planId.split("-")[1]) !== created) errors.push("$.planId: identity time must equal createdAt");
  if (expires <= created || expires - created > 24 * 60 * 60 * 1000) errors.push("$.expiresAt: plan lifetime must be positive and at most 24 hours");
  if (createHash("sha256").update(canonical(plan.capsule)).digest("hex") !== plan.capsuleSha256) errors.push("$.capsuleSha256: hash differs from exact sanitized capsule");
  if (createHash("sha256").update(canonical(schemas.codexOutput)).digest("hex") !== plan.outputSchemaSha256) errors.push("$.outputSchemaSha256: hash differs from the exact structured-output contract");
  const capsuleBytes = Buffer.byteLength(canonical(plan.capsule), "utf8"), estimatedTokens = Math.max(1, Math.ceil(capsuleBytes / 4));
  if (plan.limits.estimatedInputTokens !== estimatedTokens || plan.limits.estimatedInputTokens > plan.limits.maxInputTokens) errors.push("$.limits.estimatedInputTokens: estimate differs from or exceeds the exact capsule limit");
  if (capsuleBytes > plan.limits.maxInputBytes) errors.push("$.limits.maxInputBytes: exact capsule exceeds the provider byte ceiling");
  if (plan.createdAt !== plan.capsule.createdAt || plan.taskClass !== plan.capsule.taskClass || plan.egressMode !== plan.capsule.egressMode) errors.push("$.capsule: identity, task, or egress mode differs from the plan");
  if (plan.limits.maxOutputTokens !== plan.capsule.responseLimits.maxOutputTokens || plan.limits.maxOutputBytes !== plan.capsule.responseLimits.maxOutputBytes) errors.push("$.capsule.responseLimits: response limits differ from the plan");
  const capsuleText = canonical(plan.capsule);
  const placeholders = [...capsuleText.matchAll(/<PIXELWORK_([A-Z]+)_[0-9]{3}>/gu)];
  const uniquePlaceholders = [...new Set(placeholders.map((match) => match[0]))];
  const kinds = [...new Set(placeholders.map((match) => match[1]))].sort();
  if (uniquePlaceholders.length !== plan.dlp.placeholderCount || canonical(kinds) !== canonical([...plan.dlp.placeholderKinds].sort())) errors.push("$.dlp: placeholder inventory differs from the exact capsule");
  if (capsuleText.replaceAll(/<PIXELWORK_[A-Z]+_[0-9]{3}>/gu, "").includes("PIXELWORK_")) errors.push("$.capsule: malformed or unbound PIXELWORK placeholder");
  if (plan.provider.authMode === "chatgpt" && (plan.provider.billingBoundary !== "chatgpt-plan-or-credits-not-api-billing" || plan.limits.maxEstimatedCostMicros !== null)) errors.push("$.provider: ChatGPT plan crossed into API billing");
  if (plan.provider.authMode === "api-key" && (plan.provider.billingBoundary !== "separately-billed-api-platform" || !Number.isInteger(plan.limits.maxEstimatedCostMicros))) errors.push("$.provider: API-key plan lacks a metered cost ceiling");
  if (plan.approval.issuer.length < 2 || plan.approval.credentialsVisibleToPixel || canonical(plan.approval.allowedSecondFactors) !== canonical(["totp-authenticator-app", "email-one-time-code"])) errors.push("$.approval: external MFA boundary is invalid");
  if (plan.dlp.rawContentAuthorized !== (plan.egressMode === "owner-authorized-content")) errors.push("$.dlp.rawContentAuthorized: content authorization differs from egress mode");
  return errors;
}

export function validateWorkVerificationEvidence(evidence) {
  return hasSchemaErrors(evidence, schemas.verificationEvidence);
}

export function validateWorkResearchQuery(query) {
  const errors = hasSchemaErrors(query, schemas.researchQuery);
  if (errors.length) return errors;
  const sourceOrder = ["web", "news", "academic", "forum"];
  if (canonical(query.sourceTypes) !== canonical([...query.sourceTypes].sort((left, right) => sourceOrder.indexOf(left) - sourceOrder.indexOf(right)))) {
    errors.push("$.sourceTypes: source types must use canonical order");
  }
  if (canonical(query.domains) !== canonical([...query.domains].sort())) errors.push("$.domains: domains must be uniquely sorted");
  const identityTime = Number(query.queryId.split("-")[1]);
  if (identityTime !== Date.parse(query.createdAt)) errors.push("$.queryId: identity time must equal createdAt");
  return errors;
}

export function validateWorkResearchBatch(batch) {
  const errors = hasSchemaErrors(batch, schemas.researchBatch);
  if (errors.length) return errors;
  const ids = new Set();
  const urls = new Set();
  const identityTime = Number(batch.batchId.split("-")[1]);
  if (identityTime !== Date.parse(batch.createdAt)) errors.push("$.batchId: identity time must equal createdAt");
  let fetchedBytes = 0;
  let retrievalRequests = 0;
  for (let index = 0; index < batch.sources.length; index += 1) {
    const source = batch.sources[index];
    if (source.rank !== index + 1) errors.push(`$.sources.${index}.rank: ranks must be consecutive`);
    if (ids.has(source.sourceId)) errors.push(`$.sources.${index}.sourceId: source identifiers must be unique`);
    if (urls.has(source.canonicalUrl)) errors.push(`$.sources.${index}.canonicalUrl: canonical URLs must be unique`);
    ids.add(source.sourceId);
    urls.add(source.canonicalUrl);
    let parsed;
    try { parsed = new URL(source.canonicalUrl); } catch { errors.push(`$.sources.${index}.canonicalUrl: URL is invalid`); }
    if (parsed && (parsed.protocol !== "https:" || parsed.hostname !== source.domain || parsed.username || parsed.password || parsed.port || parsed.hash)) {
      errors.push(`$.sources.${index}: URL and domain binding is invalid`);
    }
    for (const field of ["titleBase64", "snippetBase64"]) {
      const decoded = Buffer.from(source[field], "base64");
      if (decoded.toString("base64") !== source[field]) errors.push(`$.sources.${index}.${field}: text is not canonical base64`);
    }
    const title = Buffer.from(source.titleBase64, "base64").toString("utf8");
    const snippet = Buffer.from(source.snippetBase64, "base64").toString("utf8");
    const expectedSourceId = `source-${createHash("sha256").update(source.canonicalUrl).digest("hex").slice(0, 16)}`;
    const expectedEvidence = createHash("sha256").update(canonical({
      rank: source.rank, canonicalUrl: source.canonicalUrl, sourceType: source.sourceType, title, snippet,
    })).digest("hex");
    if (source.sourceId !== expectedSourceId) errors.push(`$.sources.${index}.sourceId: identifier must bind the canonical URL`);
    if (source.searchEvidenceSha256 !== expectedEvidence) errors.push(`$.sources.${index}.searchEvidenceSha256: evidence hash differs from source metadata`);
    if (source.retrieval.status === "fetched") {
      fetchedBytes += source.retrieval.bytes;
      retrievalRequests += 1;
      if (source.retrieval.objectName !== `${source.retrieval.contentSha256}.source`) errors.push(`$.sources.${index}.retrieval.objectName: object name must equal its content hash`);
    }
    if (source.retrieval.status === "rejected") retrievalRequests += 1;
  }
  if (fetchedBytes !== batch.usage.sourceBytes) errors.push("$.usage.sourceBytes: must exactly equal fetched source objects");
  if (retrievalRequests !== batch.usage.retrievalRequests) errors.push("$.usage.retrievalRequests: must exactly equal attempted source retrievals");
  return errors;
}

export function validateWorkResearchRetrieval(receipt) {
  const errors = hasSchemaErrors(receipt, schemas.researchRetrieval);
  if (errors.length) return errors;
  const identityTime = Number(receipt.retrievalId.split("-")[1]);
  if (identityTime > Date.parse(receipt.createdAt)) errors.push("$.retrievalId: identity time cannot follow createdAt");
  if (receipt.responseName !== `res-${receipt.requestId}.md`) errors.push("$.responseName: must bind the queue request identifier");
  if (receipt.status === "fetched" && receipt.transport === "web-courier" && receipt.networkBytes < receipt.bytes) errors.push("$.networkBytes: cannot be smaller than returned source bytes");
  return errors;
}

export function validateWorkResearchToolRequest(request) {
  const errors = hasSchemaErrors(request, schemas.researchToolRequest);
  if (errors.length) return errors;
  const sourceOrder = ["web", "news", "academic", "forum"];
  if (canonical(request.sourceTypes) !== canonical([...request.sourceTypes].sort((left, right) => sourceOrder.indexOf(left) - sourceOrder.indexOf(right)))) {
    errors.push("$.sourceTypes: source types must use canonical order");
  }
  if (canonical(request.domains) !== canonical([...request.domains].sort())) errors.push("$.domains: domains must be uniquely sorted");
  const identityTime = Number(request.requestId.split("-")[1]);
  if (identityTime !== Date.parse(request.createdAt)) errors.push("$.requestId: identity time must equal createdAt");
  if (request.maxSourcesToFetch > request.maxResults) errors.push("$.maxSourcesToFetch: cannot exceed maxResults");
  return errors;
}

export function validateWorkResearchToolResponse(response) {
  const errors = hasSchemaErrors(response, schemas.researchToolResponse);
  if (errors.length) return errors;
  let sourceBytes = 0;
  const ids = new Set();
  for (let index = 0; index < response.sources.length; index += 1) {
    const source = response.sources[index];
    if (ids.has(source.sourceId)) errors.push(`$.sources.${index}.sourceId: source identifiers must be unique`);
    ids.add(source.sourceId);
    const content = canonicalBase64(source.contentBase64);
    if (!content) errors.push(`$.sources.${index}.contentBase64: content is not canonical base64`);
    else {
      sourceBytes += content.length;
      if (createHash("sha256").update(content).digest("hex") !== source.contentSha256) errors.push(`$.sources.${index}.contentSha256: content hash differs`);
    }
    for (const field of ["titleBase64", "snippetBase64"]) if (!canonicalBase64(source[field])) errors.push(`$.sources.${index}.${field}: text is not canonical base64`);
    let parsed;
    try { parsed = new URL(source.canonicalUrl); } catch { errors.push(`$.sources.${index}.canonicalUrl: URL is invalid`); }
    if (parsed && (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.port || parsed.hash)) errors.push(`$.sources.${index}.canonicalUrl: URL is not canonical public HTTPS`);
  }
  if (sourceBytes !== response.usage.sourceBytes) errors.push("$.usage.sourceBytes: must equal returned source content bytes");
  if (response.status === "completed" && response.usage.searchRequests !== 1) errors.push("$.usage.searchRequests: completed response requires one search request");
  return errors;
}

function canonicalBase64(value) {
  const bytes = Buffer.from(value, "base64");
  return bytes.toString("base64") === value ? bytes : null;
}

export function validateWorkScoutReportProposal(proposal) {
  const errors = hasSchemaErrors(proposal, schemas.scoutReportProposal);
  if (errors.length) return errors;
  for (const field of ["titleBase64", "limitationsBase64"]) if (!canonicalBase64(proposal[field])) errors.push(`$.${field}: text is not canonical base64`);
  for (let findingIndex = 0; findingIndex < proposal.findings.length; findingIndex += 1) {
    const finding = proposal.findings[findingIndex];
    if (finding.findingId !== `finding-${findingIndex + 1}`) errors.push(`$.findings.${findingIndex}.findingId: findings must be consecutive`);
    if (!canonicalBase64(finding.statementBase64)) errors.push(`$.findings.${findingIndex}.statementBase64: text is not canonical base64`);
    const observed = new Set();
    for (let evidenceIndex = 0; evidenceIndex < finding.evidence.length; evidenceIndex += 1) {
      const evidence = finding.evidence[evidenceIndex];
      const key = `${evidence.inputId}:${evidence.path}`;
      if (observed.has(key)) errors.push(`$.findings.${findingIndex}.evidence.${evidenceIndex}: evidence paths must be unique per finding`);
      observed.add(key);
      if (evidence.path.startsWith("/") || evidence.path.split("/").some((part) => !part || part === "." || part === "..")) errors.push(`$.findings.${findingIndex}.evidence.${evidenceIndex}.path: path must be canonical and relative`);
      const quote = canonicalBase64(evidence.quoteBase64);
      if (!quote || quote.length < 1 || quote.length > 65536) errors.push(`$.findings.${findingIndex}.evidence.${evidenceIndex}.quoteBase64: quote must be canonical and bounded`);
    }
  }
  return errors;
}

export function validateWorkScoutReport(report) {
  const errors = hasSchemaErrors(report, schemas.scoutReport);
  if (errors.length) return errors;
  if (Number(report.reportId.split("-")[1]) !== Date.parse(report.createdAt)) errors.push("$.reportId: identity time must equal createdAt");
  for (const field of ["titleBase64", "limitationsBase64"]) if (!canonicalBase64(report[field])) errors.push(`$.${field}: text is not canonical base64`);
  for (let findingIndex = 0; findingIndex < report.findings.length; findingIndex += 1) {
    const finding = report.findings[findingIndex];
    if (finding.findingId !== `finding-${findingIndex + 1}`) errors.push(`$.findings.${findingIndex}.findingId: findings must be consecutive`);
    if (!canonicalBase64(finding.statementBase64)) errors.push(`$.findings.${findingIndex}.statementBase64: text is not canonical base64`);
    const observed = new Set();
    for (let evidenceIndex = 0; evidenceIndex < finding.evidence.length; evidenceIndex += 1) {
      const evidence = finding.evidence[evidenceIndex];
      const key = `${evidence.inputId}:${evidence.path}`;
      if (observed.has(key)) errors.push(`$.findings.${findingIndex}.evidence.${evidenceIndex}: evidence paths must be unique per finding`);
      observed.add(key);
      const quote = canonicalBase64(evidence.quoteBase64);
      if (!quote || quote.length !== evidence.bytes || createHash("sha256").update(quote ?? Buffer.alloc(0)).digest("hex") !== evidence.quoteSha256) errors.push(`$.findings.${findingIndex}.evidence.${evidenceIndex}: quote bytes or digest differ`);
    }
  }
  return errors;
}

export function validateWorkScoutVerification(verification) {
  const errors = hasSchemaErrors(verification, schemas.scoutVerification);
  if (errors.length) return errors;
  if (Number(verification.verificationId.split("-")[1]) !== Date.parse(verification.createdAt)) errors.push("$.verificationId: identity time must equal createdAt");
  for (let findingIndex = 0; findingIndex < verification.findings.length; findingIndex += 1) {
    const finding = verification.findings[findingIndex];
    if (finding.findingId !== `finding-${findingIndex + 1}`) errors.push(`$.findings.${findingIndex}.findingId: findings must be consecutive`);
    if (finding.status !== "pass" || finding.evidence.some((entry) => entry.status !== "present")) errors.push(`$.findings.${findingIndex}: evidence status is inconsistent`);
  }
  return errors;
}

export function validateWorkResearchReport(report) {
  const errors = hasSchemaErrors(report, schemas.researchReport);
  if (errors.length) return errors;
  const identityTime = Number(report.reportId.split("-")[1]);
  if (identityTime !== Date.parse(report.createdAt)) errors.push("$.reportId: identity time must equal createdAt");
  const findingIds = new Set();
  const batches = new Set(report.batchSha256s);
  for (let index = 0; index < report.findings.length; index += 1) {
    const finding = report.findings[index];
    if (finding.findingId !== `finding-${index + 1}`) errors.push(`$.findings.${index}.findingId: findings must be consecutive`);
    if (findingIds.has(finding.findingId)) errors.push(`$.findings.${index}.findingId: finding identifiers must be unique`);
    findingIds.add(finding.findingId);
    const citations = new Set();
    for (let citationIndex = 0; citationIndex < finding.citations.length; citationIndex += 1) {
      const citation = finding.citations[citationIndex];
      const citationKey = `${citation.batchSha256}:${citation.sourceId}`;
      if (!batches.has(citation.batchSha256)) errors.push(`$.findings.${index}.citations.${citationIndex}.batchSha256: citation batch is outside the report evidence set`);
      if (citations.has(citationKey)) errors.push(`$.findings.${index}.citations.${citationIndex}: citations must be unique per finding`);
      citations.add(citationKey);
      const evidence = canonicalBase64(citation.evidenceBase64);
      if (!evidence) errors.push(`$.findings.${index}.citations.${citationIndex}.evidenceBase64: evidence is not canonical base64`);
      else if (createHash("sha256").update(evidence).digest("hex") !== citation.evidenceSha256) errors.push(`$.findings.${index}.citations.${citationIndex}.evidenceSha256: evidence hash differs`);
    }
  }
  for (const field of ["titleBase64", "limitationsBase64"]) if (!canonicalBase64(report[field])) errors.push(`$.${field}: text is not canonical base64`);
  return errors;
}

export function validateWorkResearchReportProposal(proposal) {
  const errors = hasSchemaErrors(proposal, schemas.researchReportProposal);
  if (errors.length) return errors;
  const batches = new Set(proposal.batchSha256s);
  for (let findingIndex = 0; findingIndex < proposal.findings.length; findingIndex += 1) {
    const observed = new Set();
    for (let citationIndex = 0; citationIndex < proposal.findings[findingIndex].citations.length; citationIndex += 1) {
      const citation = proposal.findings[findingIndex].citations[citationIndex];
      const key = `${citation.batchSha256}:${citation.sourceId}`;
      if (!batches.has(citation.batchSha256)) errors.push(`$.findings.${findingIndex}.citations.${citationIndex}.batchSha256: citation batch is outside the proposal evidence set`);
      if (observed.has(key)) errors.push(`$.findings.${findingIndex}.citations.${citationIndex}: citations must be unique per finding`);
      observed.add(key);
    }
  }
  return errors;
}

export function validateWorkResearchVerification(verification) {
  const errors = hasSchemaErrors(verification, schemas.researchVerification);
  if (errors.length) return errors;
  const identityTime = Number(verification.verificationId.split("-")[1]);
  if (identityTime !== Date.parse(verification.createdAt)) errors.push("$.verificationId: identity time must equal createdAt");
  const expected = verification.findings.every((finding) => finding.status === "pass" && finding.citations.every((citation) => citation.status === "present")) ? "evidence-pass" : "fail";
  if (verification.status !== expected) errors.push("$.status: aggregate citation status is inconsistent");
  const batches = new Set(verification.batchSha256s);
  for (let findingIndex = 0; findingIndex < verification.findings.length; findingIndex += 1) {
    for (let citationIndex = 0; citationIndex < verification.findings[findingIndex].citations.length; citationIndex += 1) {
      if (!batches.has(verification.findings[findingIndex].citations[citationIndex].batchSha256)) errors.push(`$.findings.${findingIndex}.citations.${citationIndex}.batchSha256: citation batch is outside the verification evidence set`);
    }
  }
  return errors;
}

export function validateWorkResearchRevisionReview(review) {
  return hasSchemaErrors(review, schemas.researchRevisionReview);
}

export function validateWorkResearchRevisionHistory(history) {
  const errors = hasSchemaErrors(history, schemas.researchRevisionHistory);
  if (errors.length) return errors;
  const hash = (value) => createHash("sha256").update(canonical(value)).digest("hex");
  const identityTime = Number(history.historyId.split("-")[1]);
  if (identityTime !== Date.parse(history.createdAt)) errors.push("$.historyId: identity time must equal createdAt");
  if (history.revisionRounds > history.maxRevisionRounds || history.attempts.length !== history.revisionRounds + 1) errors.push("$.attempts: inventory differs from the revision-round count");
  const contexts = new Set(), inferences = new Set(), workerContexts = new Set();
  let previous = null;
  for (const [index, attempt] of history.attempts.entries()) {
    for (const error of validateWorkResearchReportProposal(attempt.proposal)) errors.push(`$.attempts.${index}.proposal: ${error}`);
    for (const error of validateWorkResearchReport(attempt.report)) errors.push(`$.attempts.${index}.report: ${error}`);
    for (const error of validateWorkResearchVerification(attempt.verification)) errors.push(`$.attempts.${index}.verification: ${error}`);
    for (const error of validateWorkResearchRevisionReview(attempt.review)) errors.push(`$.attempts.${index}.review: ${error}`);
    if (attempt.round !== index) errors.push(`$.attempts.${index}.round: attempts must use canonical consecutive order`);
    if (workerContexts.has(attempt.workerContextIsolationReceiptSha256)) errors.push(`$.attempts.${index}: Researcher worker context receipt was reused`);
    workerContexts.add(attempt.workerContextIsolationReceiptSha256);
    if (attempt.report.jobId !== history.jobId || attempt.report.claimId !== history.claimId || attempt.report.planSha256 !== history.planSha256
      || attempt.verification.jobId !== history.jobId || attempt.verification.claimId !== history.claimId || attempt.verification.planSha256 !== history.planSha256
      || attempt.review.jobId !== history.jobId || attempt.review.claimId !== history.claimId || attempt.review.planSha256 !== history.planSha256) errors.push(`$.attempts.${index}: attempt identity differs from the history`);
    if (canonical(attempt.proposal.batchSha256s) !== canonical(attempt.report.batchSha256s)
      || attempt.verification.reportSha256 !== hash(attempt.report) || canonical(attempt.verification.batchSha256s) !== canonical(attempt.report.batchSha256s)
      || attempt.review.reportSha256 !== hash(attempt.report) || attempt.review.deterministicVerificationSha256 !== hash(attempt.verification)) errors.push(`$.attempts.${index}: proposal, report, verification, or review binding differs`);
    const proposalProjection = {
      titleBase64: Buffer.from(attempt.proposal.title ?? "", "utf8").toString("base64"),
      findings: (attempt.proposal.findings ?? []).map((finding, findingIndex) => ({
        findingId: `finding-${findingIndex + 1}`, statementBase64: Buffer.from(finding.statement ?? "", "utf8").toString("base64"), material: true,
        citations: (finding.citations ?? []).map((citation) => {
          const evidence = Buffer.from(citation.evidence ?? "", "utf8");
          return { batchSha256: citation.batchSha256, sourceId: citation.sourceId, evidenceBase64: evidence.toString("base64"), evidenceSha256: createHash("sha256").update(evidence).digest("hex") };
        }),
      })),
      limitationsBase64: Buffer.from(attempt.proposal.limitations ?? "", "utf8").toString("base64"),
      batchSha256s: attempt.proposal.batchSha256s,
    };
    const reportProjection = {
      titleBase64: attempt.report.titleBase64, findings: attempt.report.findings,
      limitationsBase64: attempt.report.limitationsBase64, batchSha256s: attempt.report.batchSha256s,
    };
    if (canonical(proposalProjection) !== canonical(reportProjection)) errors.push(`$.attempts.${index}: retained proposal does not deterministically project to its finalized report`);
    if (previous && (Date.parse(attempt.report.createdAt) <= Date.parse(previous.review.createdAt)
      || hash(attempt.report) === hash(previous.report)
      || attempt.report.batchSha256s.length <= previous.report.batchSha256s.length
      || canonical(attempt.report.batchSha256s.slice(0, previous.report.batchSha256s.length)) !== canonical(previous.report.batchSha256s))) errors.push(`$.attempts.${index}: revision is not a new append-only attempt`);
    for (const pass of attempt.review.passes ?? []) {
      if (contexts.has(pass.contextIsolationReceiptSha256)) errors.push(`$.attempts.${index}: critic context receipt was reused`);
      if (inferences.has(pass.inferenceReceiptSha256)) errors.push(`$.attempts.${index}: critic inference receipt was reused`);
      contexts.add(pass.contextIsolationReceiptSha256); inferences.add(pass.inferenceReceiptSha256);
    }
    previous = attempt;
  }
  const final = history.attempts.at(-1);
  if (final && (history.finalReportSha256 !== hash(final.report) || history.finalVerificationSha256 !== hash(final.verification) || history.finalReviewSha256 !== hash(final.review))) errors.push("$: final artifact hashes differ from the terminal attempt");
  const expectedStatus = final?.review?.outcome === "ready-for-independent-evaluation" ? "ready-for-independent-evaluation" : "revision-budget-exhausted";
  if (history.status !== expectedStatus || history.status === "revision-budget-exhausted" && history.revisionRounds !== history.maxRevisionRounds) errors.push("$.status: terminal status differs from the final review or budget");
  if (history.createdAt !== final?.review?.createdAt) errors.push("$.createdAt: history time must equal the final review time");
  return errors;
}

const dataFormatContract = Object.freeze({
  csv: ["dataset", "text/csv"], json: ["dataset", "application/json"], jsonl: ["dataset", "application/x-ndjson"],
  markdown: ["document", "text/markdown"], parquet: ["dataset", "application/vnd.apache.parquet"],
  png: ["visualization", "image/png"], sqlite: ["dataset", "application/vnd.sqlite3"], svg: ["visualization", "image/svg+xml"],
});

export function validateWorkDataArtifactManifest(manifest) {
  const errors = hasSchemaErrors(manifest, schemas.dataArtifactManifest);
  if (errors.length) return errors;
  if (Number(manifest.manifestId.split("-")[2]) !== Date.parse(manifest.createdAt)) errors.push("$.manifestId: identity time must equal createdAt");
  if (canonical(manifest.datasets) !== canonical([...manifest.datasets].sort((left, right) => left.datasetId.localeCompare(right.datasetId)))) errors.push("$.datasets: datasets must use canonical identifier order");
  const datasetIds = new Set();
  for (const dataset of manifest.datasets) {
    if (datasetIds.has(dataset.datasetId)) errors.push(`$.datasets.${dataset.datasetId}: dataset identifiers must be unique`);
    datasetIds.add(dataset.datasetId);
  }
  let totalBytes = 0;
  const paths = new Set();
  for (let index = 0; index < manifest.artifacts.length; index += 1) {
    const artifact = manifest.artifacts[index];
    if (artifact.artifactId !== `artifact-${index + 1}`) errors.push(`$.artifacts.${index}.artifactId: artifact identifiers must be consecutive`);
    if (paths.has(artifact.path)) errors.push(`$.artifacts.${index}.path: artifact paths must be unique`);
    paths.add(artifact.path);
    const expected = dataFormatContract[artifact.format];
    if (!expected || artifact.kind !== expected[0] || artifact.mediaType !== expected[1]) errors.push(`$.artifacts.${index}: format, kind, and media type disagree`);
    totalBytes += artifact.bytes;
  }
  if (canonical(manifest.artifacts.map((artifact) => artifact.path)) !== canonical([...manifest.artifacts].map((artifact) => artifact.path).sort())) errors.push("$.artifacts: artifacts must use canonical path order");
  if (manifest.totals.files !== manifest.artifacts.length || manifest.totals.bytes !== totalBytes) errors.push("$.totals: totals differ from the artifact inventory");
  return errors;
}

export function validateWorkDataReportProposal(proposal) {
  const errors = hasSchemaErrors(proposal, schemas.dataReportProposal);
  if (errors.length) return errors;
  const paths = proposal.artifacts.map((artifact) => artifact.path);
  if (new Set(paths).size !== paths.length) errors.push("$.artifacts: artifact paths must be unique");
  if (canonical(paths) !== canonical([...paths].sort())) errors.push("$.artifacts: artifacts must use canonical path order");
  const declared = new Set(paths);
  for (let index = 0; index < proposal.findings.length; index += 1) {
    if (proposal.findings[index].evidencePaths.some((path) => !declared.has(path))) errors.push(`$.findings.${index}.evidencePaths: evidence must reference a declared artifact`);
    if (canonical(proposal.findings[index].evidencePaths) !== canonical([...proposal.findings[index].evidencePaths].sort())) errors.push(`$.findings.${index}.evidencePaths: evidence paths must use canonical order`);
  }
  return errors;
}

export function validateWorkDataReport(report) {
  const errors = hasSchemaErrors(report, schemas.dataReport);
  if (errors.length) return errors;
  if (Number(report.reportId.split("-")[2]) !== Date.parse(report.createdAt)) errors.push("$.reportId: identity time must equal createdAt");
  const artifacts = new Map();
  for (let index = 0; index < report.artifacts.length; index += 1) {
    const artifact = report.artifacts[index];
    if (artifact.artifactId !== `artifact-${index + 1}`) errors.push(`$.artifacts.${index}.artifactId: artifact identifiers must be consecutive`);
    if (artifacts.has(artifact.path)) errors.push(`$.artifacts.${index}.path: artifact paths must be unique`);
    const expected = dataFormatContract[artifact.format];
    if (!expected || artifact.kind !== expected[0] || artifact.mediaType !== expected[1]) errors.push(`$.artifacts.${index}: format, kind, and media type disagree`);
    artifacts.set(artifact.path, artifact);
  }
  if (canonical(report.artifacts.map((artifact) => artifact.path)) !== canonical([...report.artifacts].map((artifact) => artifact.path).sort())) errors.push("$.artifacts: artifacts must use canonical path order");
  for (let index = 0; index < report.findings.length; index += 1) {
    const finding = report.findings[index];
    if (finding.findingId !== `finding-${index + 1}`) errors.push(`$.findings.${index}.findingId: finding identifiers must be consecutive`);
    const observed = new Set();
    for (const evidence of finding.evidence) {
      const artifact = artifacts.get(evidence.path);
      if (!artifact || artifact.artifactId !== evidence.artifactId || artifact.sha256 !== evidence.sha256) errors.push(`$.findings.${index}.evidence: evidence differs from the report artifact`);
      if (observed.has(evidence.path)) errors.push(`$.findings.${index}.evidence: evidence paths must be unique`);
      observed.add(evidence.path);
    }
  }
  return errors;
}

export function validateWorkDataVerification(verification) {
  const errors = hasSchemaErrors(verification, schemas.dataVerification);
  if (errors.length) return errors;
  if (Number(verification.verificationId.split("-")[2]) !== Date.parse(verification.createdAt)) errors.push("$.verificationId: identity time must equal createdAt");
  const ids = new Set();
  for (const artifact of verification.artifacts) {
    if (ids.has(artifact.artifactId)) errors.push(`$.artifacts.${artifact.artifactId}: artifact identifiers must be unique`);
    ids.add(artifact.artifactId);
    const match = artifact.observedSha256 === artifact.expectedSha256 && artifact.observedBytes === artifact.expectedBytes;
    const missing = artifact.observedSha256 === null && artifact.observedBytes === null;
    const expected = match ? "match" : missing ? "missing" : "different";
    if (artifact.status !== expected) errors.push(`$.artifacts.${artifact.artifactId}.status: comparison status is inconsistent`);
  }
  const runtimePass = verification.runtime.recipeExitCode === 0 && !verification.runtime.timedOut && !verification.runtime.outputLimitExceeded && verification.runtime.unexpectedArtifacts === 0;
  const expectedStatus = runtimePass && verification.artifacts.every((artifact) => artifact.status === "match") ? "exact-replay-pass" : "fail";
  if (verification.status !== expectedStatus) errors.push("$.status: aggregate replay status is inconsistent");
  return errors;
}

export function validateWorkDataRuntime(runtime) {
  const errors = hasSchemaErrors(runtime, schemas.dataRuntime);
  if (errors.length) return errors;
  const expectedArtifacts = ["duckdb", "polars", "polars-runtime-32"];
  const expectedPackages = ["python3", "sqlite3"];
  if (canonical(runtime.artifacts.map((artifact) => artifact.name)) !== canonical(expectedArtifacts)) errors.push("$.artifacts: artifacts must be complete and canonically ordered");
  if (canonical(runtime.systemPackages.map((entry) => entry.name)) !== canonical(expectedPackages)) errors.push("$.systemPackages: packages must be complete and canonically ordered");
  if (new Set(runtime.artifacts.map((artifact) => artifact.sha256)).size !== runtime.artifacts.length) errors.push("$.artifacts: artifact hashes must be unique");
  for (const artifact of runtime.artifacts) {
    if (!artifact.url.endsWith(`/${artifact.filename}`)) errors.push(`$.artifacts.${artifact.name}: filename differs from distribution URL`);
    const engine = artifact.name === "duckdb" ? runtime.engines.duckdb : runtime.engines.polars;
    if (artifact.version !== engine.version) errors.push(`$.artifacts.${artifact.name}: version differs from engine contract`);
  }
  return errors;
}

export function validateWorkBuilderRuntime(runtime) {
  const errors = hasSchemaErrors(runtime, schemas.builderRuntime);
  if (errors.length) return errors;
  if (runtime.minimumMemoryMiB !== 2048) errors.push("$.minimumMemoryMiB: minimum must remain bound to the qualified live runtime");
  const expectedPackages = ["gdb", "python-is-python3", "python3-debugpy"];
  if (canonical(runtime.systemPackages.map((entry) => entry.name)) !== canonical(expectedPackages)) errors.push("$.systemPackages: packages must be complete and canonically ordered");
  const packages = new Map(runtime.systemPackages.map((entry) => [entry.name, entry.version]));
  for (const [name, feature] of Object.entries(runtime.features)) {
    if (feature.package && packages.get(feature.package) !== feature.packageVersion) errors.push(`$.features.${name}: package version differs from supply-chain contract`);
  }
  if (canonical(runtime.artifacts.map((artifact) => artifact.name)) !== canonical(["pyright"])) errors.push("$.artifacts: artifacts must be complete and canonically ordered");
  const pyright = runtime.artifacts.find((artifact) => artifact.name === runtime.features.pyright.artifact);
  if (!pyright || pyright.version !== runtime.features.pyright.runtimeVersion || !pyright.url.endsWith(`/${pyright.filename}`)) errors.push("$.features.pyright: artifact differs from runtime contract");
    if (runtime.features.debugpy.entrypoint !== runtime.features.pythonCommand.entrypoint) errors.push("$.features.debugpy.entrypoint: debugger must use the pinned Python command");
    if (canonical(runtime.agentSurface.runtimeTools) !== canonical([...runtime.agentSurface.declaredTools].sort().concat("yield").sort())) errors.push("$.agentSurface.runtimeTools: runtime surface must equal the declared local tools plus OMP yield");
    if (runtime.agentSurface.blockedTools.some((tool) => runtime.agentSurface.runtimeTools.includes(tool))) errors.push("$.agentSurface.blockedTools: blocked tools must not enter the delegated runtime surface");
    return errors;
  }

export function validateWorkOmpRuntime(runtime) {
  const errors = hasSchemaErrors(runtime, schemas.ompRuntime);
  if (errors.length) return errors;
  if (runtime.probe.failureCeilingMiB >= runtime.probe.successFloorMiB) errors.push("$.probe: failure ceiling must be lower than the success floor");
  if (runtime.probe.successFloorMiB + runtime.safetyHeadroomMiB !== runtime.minimumWorkerMemoryMiB) errors.push("$.minimumWorkerMemoryMiB: minimum must equal the measured success floor plus headroom");
  if (runtime.probe.testedThroughMiB < Math.max(...Object.values(runtime.profileMinimumMemoryMiB))) errors.push("$.probe.testedThroughMiB: probe did not cover every profile floor");
  if (runtime.profileMinimumMemoryMiB.builder < runtime.minimumWorkerMemoryMiB || runtime.profileMinimumMemoryMiB.scout !== runtime.minimumWorkerMemoryMiB || runtime.profileMinimumMemoryMiB["data-lab"] !== runtime.minimumWorkerMemoryMiB || runtime.profileMinimumMemoryMiB.researcher !== runtime.minimumWorkerMemoryMiB) errors.push("$.profileMinimumMemoryMiB: profile floors differ from the qualified OMP and Builder contracts");
  return errors;
}

export function validatePlanVerificationEvidence(plan, evidence) {
  const errors = [
    ...validateWorkPlan(plan).map((error) => `plan ${error}`),
    ...validateWorkVerificationEvidence(evidence).map((error) => `evidence ${error}`),
  ];
  if (errors.length) return errors;
  if (plan.jobId !== evidence.jobId || createHash("sha256").update(canonical(plan)).digest("hex") !== evidence.planSha256) errors.push("plan/evidence: immutable plan binding differs");
  const planned = new Map(plan.verification.checks.map((check) => [check.id, check]));
  const observed = new Set();
  for (const check of evidence.checks) {
    if (observed.has(check.id)) errors.push(`plan/evidence: duplicate check ${check.id}`);
    observed.add(check.id);
    const expected = planned.get(check.id);
    if (!expected || check.kind !== expected.kind || canonical(check.criterionIndexes) !== canonical(expected.criterionIndexes)) errors.push(`plan/evidence: check ${check.id} differs from the immutable recipe`);
    if (check.candidateSha256 !== evidence.candidateSha256) errors.push(`plan/evidence: check ${check.id} used a different candidate`);
    if (expected?.kind === "command") {
      const passed = check.exitCode === 0 && check.signal === null && check.timedOut === false && check.outputLimitExceeded === false && check.spawnFailed === false;
      if ((check.status === "pass") !== passed) errors.push(`plan/evidence: command check ${check.id} has inconsistent status`);
      if (check.runtimeMilliseconds > expected.timeoutSeconds * 1000 + 30000) errors.push(`plan/evidence: command check ${check.id} exceeded its runtime receipt ceiling`);
      if (check.stdoutBytes + check.stderrBytes > expected.maxOutputBytes + 1048576) errors.push(`plan/evidence: command check ${check.id} exceeded its output receipt ceiling`);
      if (!check.outputLimitExceeded && check.stdoutBytes + check.stderrBytes > expected.maxOutputBytes) errors.push(`plan/evidence: command check ${check.id} did not enforce its output ceiling`);
    }
  }
  if (observed.size !== planned.size || [...planned.keys()].some((id) => !observed.has(id))) errors.push("plan/evidence: check set is incomplete");
  if (evidence.criteria.length !== plan.acceptanceCriteria.length) errors.push("plan/evidence: criterion count differs");
  for (let index = 0; index < evidence.criteria.length; index += 1) {
    const criterion = evidence.criteria[index];
    const relevant = evidence.checks.filter((check) => check.criterionIndexes.includes(index));
    const expectedIds = relevant.map((check) => check.id);
    const expectedStatus = relevant.length && relevant.every((check) => check.status === "pass") ? "pass" : "fail";
    if (criterion.index !== index || canonical(criterion.checkIds) !== canonical(expectedIds) || criterion.status !== expectedStatus) errors.push(`plan/evidence: criterion ${index} result is inconsistent`);
  }
  const expectedStatus = evidence.criteria.every((criterion) => criterion.status === "pass") ? "pass" : "fail";
  if (evidence.status !== expectedStatus) errors.push("plan/evidence: aggregate status is inconsistent");
  return errors;
}

export function validateWorkResult(result) {
  const errors = hasSchemaErrors(result, schemas.result);
  if (errors.length) return errors;
  const { total, passing, failing } = result.acceptance;
  if (passing + failing !== total) errors.push("$.acceptance: passing and failing counts must equal total criteria");
  if (result.status === "pass" && passing !== total) errors.push("$.acceptance: pass requires every criterion to pass");
  if (result.status === "pass" && result.artifacts.length === 0) errors.push("$.artifacts: a passing job must return at least one artifact");
  const retainedBytes = result.artifacts.reduce((sum, artifact) => sum + artifact.bytes, 0);
  if (retainedBytes > result.usage.artifactBytes) errors.push("$.usage.artifactBytes: cannot be smaller than retained artifacts");
  const artifactIds = new Set();
  for (const artifact of result.artifacts) {
    if (artifactIds.has(artifact.id)) errors.push(`$.artifacts.${artifact.id}: artifact identifiers must be unique`);
    artifactIds.add(artifact.id);
  }
  const safetyIncident = Object.entries(result.safety)
    .some(([key, value]) => key.endsWith("Observed") && value === true);
  const externalAction = Object.values(result.authority).some((value) => value === true);
  if ((safetyIncident || externalAction) && result.status !== "failed") {
    errors.push("$: a safety incident or forbidden external action requires failed status");
  }
  return errors;
}

export function validateWorkSemanticAcceptance(acceptance) {
  const errors = hasSchemaErrors(acceptance, schemas.semanticAcceptance);
  if (errors.length) return errors;
  const identityTime = Number(acceptance.acceptanceId.split("-")[1]);
  if (identityTime !== Date.parse(acceptance.createdAt)) errors.push("$.acceptanceId: identity time must equal createdAt");
  for (let index = 0; index < acceptance.criteria.length; index += 1) {
    if (acceptance.criteria[index].index !== index) errors.push(`$.criteria.${index}.index: criteria must be consecutive`);
  }
  return errors;
}

export function assertWorkContract(value, kind, label = kind) {
  const validators = {
    job: validateWorkJob,
    policy: validateWorkPolicy,
    goalBrief: validateWorkGoalBrief,
    inputSelection: validateWorkInputSelection,
    inputCatalog: validateWorkInputCatalog,
    inputPack: validateWorkInputPack,
    goalDraft: validateWorkGoalDraft,
    plan: validateWorkPlan,
    lease: validateWorkLease,
    consumption: validateWorkConsumption,
    checkpoint: validateWorkCheckpoint,
    goal: validateWorkGoal,
    goalCheckpoint: validateWorkGoalCheckpoint,
    goalRunBundle: validateWorkGoalRunBundle,
    semanticAcceptance: validateWorkSemanticAcceptance,
    verificationEvidence: validateWorkVerificationEvidence,
    scoutReportProposal: validateWorkScoutReportProposal,
    scoutReport: validateWorkScoutReport,
    scoutVerification: validateWorkScoutVerification,
    researchQuery: validateWorkResearchQuery,
    researchBatch: validateWorkResearchBatch,
    researchRetrieval: validateWorkResearchRetrieval,
    researchReport: validateWorkResearchReport,
    researchVerification: validateWorkResearchVerification,
    researchRevisionReview: validateWorkResearchRevisionReview,
    researchRevisionHistory: validateWorkResearchRevisionHistory,
    dataArtifactManifest: validateWorkDataArtifactManifest,
    dataReportProposal: validateWorkDataReportProposal,
    dataReport: validateWorkDataReport,
    dataVerification: validateWorkDataVerification,
    dataRuntime: validateWorkDataRuntime,
    builderRuntime: validateWorkBuilderRuntime,
    ompRuntime: validateWorkOmpRuntime,
    modelCapabilityReceipt: validateWorkModelCapabilityReceipt,
    modelPolicyReview: validateWorkModelPolicyReview,
    modelPolicyEnableReview: validateWorkModelPolicyEnableReview,
    modelOperatorStatus: validateWorkModelOperatorStatus,
    modelBackendStatus: validateWorkModelBackendStatus,
    modelArtifactManifest: validateWorkModelArtifactManifest,
    modelRuntimeCacheManifest: validateWorkModelRuntimeCacheManifest,
    modelArtifactReview: validateWorkModelArtifactReview,
    modelArtifactRenderReceipt: validateWorkModelArtifactRenderReceipt,
    modelBackendConfig: validateWorkModelBackendConfig,
    modelBackendReview: validateWorkModelBackendReview,
    modelBackendLaunch: validateWorkModelBackendLaunch,
    modelBackendLifecycleReview: validateWorkModelBackendLifecycleReview,
    modelBackendLifecycleReceipt: validateWorkModelBackendLifecycleReceipt,
    modelBackendHaltReview: validateWorkModelBackendHaltReview,
    modelBackendHaltReceipt: validateWorkModelBackendHaltReceipt,
    modelBackendLiveQualification: validateWorkModelBackendLiveQualification,
    watchdogDecision: validateWorkWatchdogDecision,
    contextSessionInput: validateWorkContextSessionInput,
    contextCapsule: validateWorkContextCapsule,
    capabilityPack: validateWorkCapabilityPack,
    capabilityGrant: validateWorkCapabilityGrant,
    capabilityConsumption: validateWorkCapabilityConsumption,
    capabilityControllerPolicy: validateWorkCapabilityControllerPolicy,
    capabilityJobAuthorization: validateWorkCapabilityJobAuthorization,
    capabilityToolRequest: validateWorkCapabilityToolRequest,
    capabilityWatchdogEvent: validateWorkCapabilityWatchdogEvent,
    capabilityToolResponse: validateWorkCapabilityToolResponse,
    capabilityToolCustody: validateWorkCapabilityToolCustody,
    capabilityPackV2: validateWorkCapabilityPackV2,
    capabilityToolCatalogV2: validateWorkCapabilityToolCatalogV2,
    capabilityControllerPolicyV2: validateWorkCapabilityControllerPolicyV2,
    capabilityJobAuthorizationV2: validateWorkCapabilityJobAuthorizationV2,
    capabilityToolRequestV2: validateWorkCapabilityToolRequestV2,
    capabilityGrantV2: validateWorkCapabilityGrantV2,
    capabilityLeaseV2: validateWorkCapabilityLeaseV2,
    capabilityRuntimeV2: validateWorkCapabilityRuntimeV2,
    capabilityConsumptionV2: validateWorkCapabilityConsumptionV2,
    operationalGrantV2: validateWorkCapabilityOperationalGrantV2,
    operationalRuntimeRequestV2: validateWorkCapabilityOperationalRuntimeRequestV2,
    operationalRuntimeResultV2: validateWorkCapabilityOperationalRuntimeResultV2,
    sshApprovalV2: validateWorkCapabilitySshApprovalV2,
    queueRequestV2: validateWorkCapabilityQueueRequestV2,
    queueResponseV2: validateWorkCapabilityQueueResponseV2,
    knowledgeIngestion: validateWorkKnowledgeIngestion,
    knowledgeSource: validateWorkKnowledgeSource,
    knowledgeQuery: validateWorkKnowledgeQuery,
    knowledgeRetrieval: validateWorkKnowledgeRetrieval,
    knowledgeDeletion: validateWorkKnowledgeDeletion,
    knowledgeKeyRotation: validateWorkKnowledgeKeyRotation,
    knowledgeReconciliation: validateWorkKnowledgeReconciliation,
    operatorStatus: validateWorkOperatorStatus,
    codexPolicy: validateWorkCodexPolicy,
    codexRequest: validateWorkCodexRequest,
    codexCapsule: validateWorkCodexCapsule,
    codexPlan: validateWorkCodexPlan,
    codexOutput: validateWorkCodexOutput,
    codexAuthorization: validateWorkCodexAuthorization,
    codexExecutionClaim: validateWorkCodexExecutionClaim,
    codexResult: validateWorkCodexResult,
    providerProfile: validateWorkProviderProfile,
    providerReceipt: validateWorkProviderReceipt,
    providerPrivatePolicy: validateWorkProviderPrivatePolicy,
    providerCustodyReceipt: validateWorkProviderCustodyReceipt,
    providerRunLedger: validateWorkProviderRunLedger,
    routerRequest: validateWorkProviderRouterRequest,
    routerQualification: validateWorkProviderRouterQualification,
    routerPolicy: validateWorkProviderRouterPolicy,
    routerDecision: validateWorkProviderRouterDecision,
    result: validateWorkResult,
  };
  if (!validators[kind]) throw new Error(`Unknown work contract kind: ${kind}`);
  const errors = validators[kind](value);
  if (errors.length) throw new Error(`${label} failed validation:\n- ${errors.join("\n- ")}`);
}
