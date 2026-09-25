import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { assertJsonSchema } from "./lib/json-schema.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (path) => readFile(join(root, path), "utf8");
const sha256 = (value) => createHash("sha256").update(value).digest("hex");
execFileSync(process.execPath, [join(root, "scripts/generate-release-files.mjs"), "--check"], { cwd: root, stdio: "inherit" });
const manifest = JSON.parse(await read("RELEASE-MANIFEST.json"));
const compatibility = JSON.parse(await read("OPENCLAW-COMPATIBILITY.json"));
const workPolicy = JSON.parse(await read("deploy/work-broker/policy.example.json"));
const workNativeAddons = JSON.parse(await read("deploy/work-runner/native-addons.json"));
const dataRuntime = JSON.parse(await read("deploy/work-runner/data-runtime.json"));
const dataRuntimeSchema = JSON.parse(await read("schemas/work-data-runtime-v1.schema.json"));
const builderRuntime = JSON.parse(await read("deploy/work-runner/builder-runtime.json"));
const builderRuntimeSchema = JSON.parse(await read("schemas/work-builder-runtime-v1.schema.json"));
const ompRuntime = JSON.parse(await read("deploy/work-runner/omp-runtime.json"));
const ompRuntimeSchema = JSON.parse(await read("schemas/work-omp-runtime-v1.schema.json"));
const controlStatusSchema = JSON.parse(await read("schemas/control-status-v1.schema.json"));
const controlUpdateStatusSchema = JSON.parse(await read("schemas/control-update-status-v1.schema.json"));
const controlRecoveryGuideSchema = JSON.parse(await read("schemas/control-recovery-guide-v1.schema.json"));
const controlFrontierBudgetProposalSchema = JSON.parse(await read("schemas/control-frontier-budget-proposal-v1.schema.json"));
const controlChatSchema = JSON.parse(await read("schemas/control-chat-v1.schema.json"));
const controlChatTurnRequestSchema = JSON.parse(await read("schemas/control-chat-turn-request-v1.schema.json"));
const controlChatHandoffReceiptSchema = JSON.parse(await read("schemas/control-chat-handoff-receipt-v1.schema.json"));
const controlAccessAdapterSchema = JSON.parse(await read("schemas/control-access-adapter-v1.schema.json"));
const controlAccessAdapterExample = JSON.parse(await read("control/access-adapter.example.json"));
const controlWorkDraftReviewsSchema = JSON.parse(await read("schemas/control-work-draft-reviews-v1.schema.json"));
const controlDiagnosticsSchema = JSON.parse(await read("schemas/control-diagnostics-v1.schema.json"));
const qualification = JSON.parse(await read("QUALIFICATION-MATRIX.json"));
const clientOverlaySchema = JSON.parse(await read("schemas/client-overlay-v1.schema.json"));
const clientOverlayExample = JSON.parse(await read("client-overlay.example.json"));
const portalOutcomePixelSystemSchema = JSON.parse(await read("schemas/portal-outcome-pixel-system-v1.schema.json"));
const portalOutcomePixelSystemExample = JSON.parse(await read("deploy/agent-comparison/pixel-system.example.json"));
const portalOutcomePairSystemSchema = JSON.parse(await read("schemas/portal-outcome-pair-system-v1.schema.json"));
const portalOutcomePairSystemExample = JSON.parse(await read("deploy/agent-comparison/pair-system.example.json"));
const codexComparisonPrompt = await read("deploy/agent-comparison/codex-0.147.0-prompt.md");
const localProviderAdapterSource = await read("deploy/work-provider/adapters/local-openai.mjs");
const localProviderTransportSource = await read("deploy/work-provider/local-transport.mjs");
const openAiChatAdapterSource = await read("deploy/work-provider/adapters/openai-chat.mjs");
const workModelQualificationDockerSchema = JSON.parse(await read("schemas/work-model-qualification-docker-v1.schema.json"));
const workModelQualificationDockerExample = JSON.parse(await read("deploy/work-controller/model-qualification-docker.example.json"));
const workModelQualificationMaintenanceSchema = JSON.parse(await read("schemas/work-model-qualification-maintenance-v1.schema.json"));
const workModelQualificationMaintenanceExample = JSON.parse(await read("deploy/work-controller/model-qualification-maintenance.example.json"));
const workModelCampaignMaintenanceSchema = JSON.parse(await read("schemas/work-model-campaign-maintenance-v1.schema.json"));
const workModelCampaignMaintenanceExample = JSON.parse(await read("deploy/work-controller/model-campaign-maintenance.example.json"));
const deepWorkPackSchema = JSON.parse(await read("schemas/work-capability-pack-v2.schema.json"));
const deepWorkToolCatalogSchema = JSON.parse(await read("schemas/work-capability-tool-catalog-v2.schema.json"));
const deepWorkControllerPolicySchema = JSON.parse(await read("schemas/work-capability-controller-policy-v2.schema.json"));
const deepWorkJobAuthorizationSchema = JSON.parse(await read("schemas/work-capability-job-authorization-v2.schema.json"));
const deepWorkToolRequestSchema = JSON.parse(await read("schemas/work-capability-tool-request-v2.schema.json"));
const deepWorkGrantSchema = JSON.parse(await read("schemas/work-capability-grant-v2.schema.json"));
const deepWorkLeaseSchema = JSON.parse(await read("schemas/work-capability-lease-v2.schema.json"));
const deepWorkRuntimeSchema = JSON.parse(await read("schemas/work-capability-runtime-v2.schema.json"));
const deepWorkConsumptionSchema = JSON.parse(await read("schemas/work-capability-consumption-v2.schema.json"));
const releaseManifestSchema = JSON.parse(await read("schemas/release-manifest-v1.schema.json"));
const version = (await read("VERSION")).trim();
if (sha256(codexComparisonPrompt) !== "ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807") throw new Error("Codex 0.147 comparison prompt differs from the exact reviewed source");
assertJsonSchema(dataRuntime, dataRuntimeSchema, "deploy/work-runner/data-runtime.json");
assertJsonSchema(builderRuntime, builderRuntimeSchema, "deploy/work-runner/builder-runtime.json");
assertJsonSchema(ompRuntime, ompRuntimeSchema, "deploy/work-runner/omp-runtime.json");
assertJsonSchema(controlAccessAdapterExample, controlAccessAdapterSchema, "control/access-adapter.example.json");
if (JSON.stringify(dataRuntime.artifacts.map((artifact) => artifact.name)) !== JSON.stringify(["duckdb", "polars", "polars-runtime-32"]) || JSON.stringify(dataRuntime.systemPackages.map((entry) => entry.name)) !== JSON.stringify(["python3", "sqlite3"])) throw new Error("Data Lab runtime supply chain is incomplete or noncanonical");
for (const artifact of dataRuntime.artifacts) {
  const engine = artifact.name === "duckdb" ? dataRuntime.engines.duckdb : dataRuntime.engines.polars;
  if (artifact.version !== engine.version || !artifact.url.endsWith(`/${artifact.filename}`)) throw new Error("Data Lab runtime artifact differs from its engine contract");
}
if (JSON.stringify(builderRuntime.systemPackages.map((entry) => entry.name)) !== JSON.stringify(["gdb", "python-is-python3", "python3-debugpy"]) || JSON.stringify(builderRuntime.artifacts.map((entry) => entry.name)) !== JSON.stringify(["pyright"])) throw new Error("Builder code-intelligence runtime supply chain is incomplete or noncanonical");
if (workPolicy.profiles.builder.minimumMemoryMiB !== builderRuntime.minimumMemoryMiB || workPolicy.profiles.builder.maxBudgets.maxMemoryMiB < builderRuntime.minimumMemoryMiB) throw new Error("Builder policy does not preserve the qualified runtime memory floor");
for (const [profile, policyName] of [["scout", "scout"], ["builder", "builder"]]) {
  if (workPolicy.profiles[policyName].minimumMemoryMiB !== ompRuntime.profileMinimumMemoryMiB[profile] || workPolicy.profiles[policyName].maxBudgets.maxMemoryMiB < ompRuntime.profileMinimumMemoryMiB[profile]) throw new Error(`${profile} policy does not preserve the qualified OMP memory floor`);
}
if (ompRuntime.executor.version !== workPolicy.executor.version || ompRuntime.executor.sourceCommit !== workPolicy.executor.sourceCommit || ompRuntime.executor.sha256 !== workPolicy.executor.sha256) throw new Error("OMP runtime memory evidence differs from the pinned executor");
for (const feature of Object.values(builderRuntime.features)) {
  if (feature.package && builderRuntime.systemPackages.find((entry) => entry.name === feature.package)?.version !== feature.packageVersion) throw new Error("Builder runtime feature differs from its package contract");
}
if (builderRuntime.artifacts[0].version !== builderRuntime.features.pyright.runtimeVersion || !builderRuntime.artifacts[0].url.endsWith(`/${builderRuntime.artifacts[0].filename}`)) throw new Error("Builder Pyright artifact differs from its runtime contract");
if (sha256(await read("deploy/work-runner/builder-agent.md")) !== builderRuntime.agentSurface.definitionSha256 || sha256(await read("deploy/work-runner/builder-entrypoint.sh")) !== builderRuntime.agentSurface.entrypointSha256) throw new Error("Builder delegated-agent package differs from its runtime contract");
if (manifest.$schema !== "./schemas/release-manifest-v1.schema.json" || manifest.schemaVersion !== 1) throw new Error("Release manifest schema contract is invalid");
if (compatibility.$schema !== "./schemas/openclaw-compatibility-v1.schema.json" || compatibility.schemaVersion !== 1) throw new Error("Compatibility matrix schema contract is invalid");
if (manifest.pixel !== version) throw new Error("VERSION and RELEASE-MANIFEST.json disagree");
if (
  workNativeAddons?.schemaVersion !== 1
  || workNativeAddons?.executor?.id !== "omp"
  || workNativeAddons.executor.version !== workPolicy?.executor?.version
  || workNativeAddons.executor.artifactSha256 !== workPolicy?.executor?.sha256
  || workNativeAddons.extraction !== "pinned-executor-offline-read-probe"
  || !Array.isArray(workNativeAddons.addons)
  || workNativeAddons.addons.length !== 2
) throw new Error("Deep Work native-addon provenance differs from its pinned executor");
for (const addon of workNativeAddons.addons) {
  if (!/^linux-x64-(?:modern|baseline)$/.test(addon?.platform ?? "") || !/^\/opt\/pi_natives\.linux-x64-(?:modern|baseline)\.node$/.test(addon?.imagePath ?? "") || !Number.isSafeInteger(addon?.bytes) || addon.bytes < 1 || !/^[a-f0-9]{64}$/.test(addon?.sha256 ?? "")) {
    throw new Error("Deep Work native-addon provenance is invalid");
  }
}
if (new Set(workNativeAddons.addons.map((addon) => addon.platform)).size !== 2 || new Set(workNativeAddons.addons.map((addon) => addon.imagePath)).size !== 2) throw new Error("Deep Work native-addon provenance is duplicated");
if (manifest.sourceBroker?.schemaVersion !== 2) throw new Error("Source Broker release schema must be 2");
if (manifest.sourceBroker?.sentMailProjection !== "metadata-only" || manifest.sourceBroker?.mailboxCoverage !== "exhaustive-query-pagination" || manifest.sourceBroker?.toolPagination !== "bounded-offset") throw new Error("Source Broker Sent-mail or coverage contract is invalid");
if (manifest.sourceBroker?.actionJournalSchema !== "./schemas/external-action-journal-event-v1.schema.json" || manifest.sourceBroker?.uncertainOutcomePolicy !== "reconcile-before-retry-no-false-success" || JSON.stringify(manifest.sourceBroker?.externalActionStates) !== JSON.stringify(["proposed", "submitting", "unknown", "reconciling", "succeeded", "failed", "canceled"])) throw new Error("Source Broker external-action journal contract is invalid");
if (manifest.githubBroker?.schemaVersion !== 1 || manifest.githubBroker?.defaultEnabled !== false || manifest.githubBroker?.apiHost !== "api.github.com" || manifest.githubBroker?.apiVersion !== "2026-03-10" || manifest.githubBroker?.idempotency !== "provider-reconciliation-marker" || manifest.githubBroker?.unknownOutcome !== "reconcile-before-retry-no-false-success" || manifest.githubBroker?.credentialVisibleToAgent !== false) throw new Error("GitHub Broker release contract is invalid");
if (manifest.operationsBroker?.schemaVersion !== 2) throw new Error("Operations Broker release schema must be 2");
if (manifest.frontierBroker?.schemaVersion !== 2 || manifest.frontierBroker?.usageProjection !== "content-free-routing-quality-savings-aggregate") throw new Error("Frontier Broker adaptive-routing release contract is invalid");
if (JSON.stringify(manifest.frontierBroker?.routingDecisions) !== JSON.stringify(["local-only", "local-retry", "operator-context", "preview", "propose", "bounded-auto", "reject"])) throw new Error("Frontier routing decision contract is invalid");
if (manifest.frontierBroker?.dedupBinding !== "sha256-capsule-policy-provider-auth-model-output-contract" || manifest.frontierBroker?.localFinalization !== "content-free-hash-bound-integration-receipt") throw new Error("Frontier deduplication or local-finalization contract is invalid");
const expectedWorkProvider = {
  schemaVersion: 1,
  defaultMode: "local-only",
  modes: ["local-only", "explicit-provider", "policy-router"],
  remoteEnabledByDefault: false,
  profilesDirectory: "./deploy/work-provider/profiles",
  profileSchema: "./schemas/work-provider-profile-v1.schema.json",
  receiptSchema: "./schemas/work-provider-receipt-v1.schema.json",
  privatePolicySchema: "./schemas/work-provider-private-policy-v1.schema.json",
  custodyReceiptSchema: "./schemas/work-provider-custody-receipt-v1.schema.json",
  runLedgerSchema: "./schemas/work-provider-run-ledger-v1.schema.json",
  routerRequestSchema: "./schemas/work-provider-router-request-v1.schema.json",
  routerQualificationSchema: "./schemas/work-provider-router-qualification-v1.schema.json",
  routerPolicySchema: "./schemas/work-provider-router-policy-v1.schema.json",
  routerDecisionSchema: "./schemas/work-provider-router-decision-v1.schema.json",
  registry: "closed-local-openai-anthropic-moonshot-together-fireworks-groq-openrouter",
  router: "content-free-deterministic-local-first-no-silent-cloud-fallback",
  credentialCustody: "owner-private-file-handle-no-env-no-argv-no-log",
  egressProxy: "exact-profile-host-proxy-only-worker-internal-network",
  uncertainOutcome: "reconcile-before-retry-no-reroute",
  executionBinding: "exact-decision-sha-required-no-routing-authority",
  qualification: "current-semantic-hash-envelope-pricing-required-local-and-remote",
  qualificationRunner: "sealed-neutral-corpus-qualification-trial-local-moonshot-openai-anthropic-openrouter",
  neutralCorpusSchema: "./schemas/work-provider-neutral-corpus-v1.schema.json",
  qualificationTrialEvidenceSchema: "./schemas/work-provider-qualification-trial-evidence-v1.schema.json",
  qualificationPromotion: "sealed-deterministic-semantic-capability-promotion",
  qualificationPromotionModule: "./deploy/work-provider/qualification-promotion.mjs",
  modelSelection: "fixed-owner-pinned-qualification-pinned-closed",
  responseModelBinding: "exact-ledger-response-model-before-disclosure",
  genericRemoteTransportModule: "./deploy/work-provider/generic-remote-transport.mjs",
  equivalenceRunnerModule: "./deploy/work-provider/equivalence-runner.mjs",
};
if (JSON.stringify(manifest.workProvider) !== JSON.stringify(expectedWorkProvider)) throw new Error("Local-first multi-provider release contract is invalid");
const expectedDeepWorkCapability = {
  schemaVersion: 2,
  runtimeEnabled: false,
  admissionBoundary: "admission-only-no-runtime-no-tool-call-no-network-no-external-effects",
  packSchema: "./schemas/work-capability-pack-v2.schema.json",
  toolCatalogSchema: "./schemas/work-capability-tool-catalog-v2.schema.json",
  controllerPolicySchema: "./schemas/work-capability-controller-policy-v2.schema.json",
  jobAuthorizationSchema: "./schemas/work-capability-job-authorization-v2.schema.json",
  toolRequestSchema: "./schemas/work-capability-tool-request-v2.schema.json",
  grantSchema: "./schemas/work-capability-grant-v2.schema.json",
  leaseSchema: "./schemas/work-capability-lease-v2.schema.json",
  runtimeSchema: "./schemas/work-capability-runtime-v2.schema.json",
  consumptionSchema: "./schemas/work-capability-consumption-v2.schema.json",
};
if (JSON.stringify(manifest.deepWorkCapability) !== JSON.stringify(expectedDeepWorkCapability)) throw new Error("Deep Work v2 admission-only release contract is invalid");
if (deepWorkPackSchema?.properties?.schemaVersion?.const !== 2 || deepWorkPackSchema?.properties?.provenance?.properties?.admissionOnly?.const !== true || deepWorkPackSchema?.properties?.security?.properties?.admissionOnly?.const !== true) throw new Error("Deep Work v2 pack schema must stay admission-only");
if (deepWorkToolCatalogSchema?.properties?.authority?.const?.grantsToolCall !== false) throw new Error("Deep Work v2 tool catalog must grant no tool-call authority");
if (deepWorkRuntimeSchema?.properties?.enabled?.const !== false || deepWorkRuntimeSchema?.properties?.result?.properties?.externalEffects?.const !== false || deepWorkRuntimeSchema?.properties?.container?.properties?.network?.const !== "none") throw new Error("Deep Work v2 runtime schema must stay disabled with no network or external effects");
const deepWorkAuthority = deepWorkRuntimeSchema?.properties?.authority?.const;
if (!deepWorkAuthority || typeof deepWorkAuthority !== "object" || Object.values(deepWorkAuthority).some((value) => value !== false)) throw new Error("Deep Work v2 runtime schema must grant no authority");
for (const [label, schema] of Object.entries({ controllerPolicy: deepWorkControllerPolicySchema, jobAuthorization: deepWorkJobAuthorizationSchema, toolRequest: deepWorkToolRequestSchema, grant: deepWorkGrantSchema, lease: deepWorkLeaseSchema, consumption: deepWorkConsumptionSchema })) {
  if (schema?.properties?.schemaVersion?.const !== 2) throw new Error(`Deep Work v2 ${label} schemaVersion must be 2`);
}
function releaseManifestSchemaRejects(value, label) {
  try {
    assertJsonSchema(value, releaseManifestSchema, label);
    return false;
  } catch {
    return true;
  }
}
const runtimeEnabledClaim = { ...manifest, deepWorkCapability: { ...manifest.deepWorkCapability, runtimeEnabled: true } };
if (!releaseManifestSchemaRejects(runtimeEnabledClaim, "negative-deep-work-runtime-enabled")) throw new Error("Release manifest schema must reject a v2 runtime-enabled claim");
const authorityClaim = { ...manifest, deepWorkCapability: { ...manifest.deepWorkCapability, admissionBoundary: "admission-only-but-runtime-allowed", toolAuthority: true } };
if (!releaseManifestSchemaRejects(authorityClaim, "negative-deep-work-authority")) throw new Error("Release manifest schema must reject v2 runtime/tool/network/external-effect authority claims");
for (const field of ["ec_transfer_params", "kv_transfer_params", "metrics", "prompt_logprobs", "prompt_text", "prompt_token_ids", "routed_experts", "stop_reason", "token_ids", "annotations", "audio", "function_call", "reasoning"]) {
  if (!localProviderAdapterSource.includes(`\"${field}\"`)) throw new Error(`Local provider adapter is missing the reviewed vLLM ${field} boundary`);
}
for (const field of ["ec_transfer_params", "kv_transfer_params", "prompt_logprobs", "prompt_text", "prompt_token_ids", "routed_experts", "stop_reason", "token_ids"]) {
  if (openAiChatAdapterSource.includes(`\"${field}\"`)) throw new Error(`Remote OpenAI chat adapter was widened with local-only vLLM field ${field}`);
}
if (!/export async function executeLocalChatTurn\(options\)/u.test(localProviderTransportSource)
  || !/Object\.hasOwn\(options, "exchange"\).*Object\.hasOwn\(options, "testSeam"\)/u.test(localProviderTransportSource)
  || !/export const localProviderTransportTestOnly = Object\.freeze/u.test(localProviderTransportSource)) throw new Error("Local provider transport production/test exchange boundary is invalid");
if (manifest.controlSurface?.listener !== "exact-ipv4-loopback" || manifest.controlSurface?.browserCredentialAccess !== "none" || manifest.controlSurface?.genericCommandSurface !== false) throw new Error("Local control listener or credential boundary is invalid");
if (manifest.controlSurface?.actionBinding !== "sha256-exact-preview-expiring-single-use" || JSON.stringify(manifest.controlSurface?.allowedActions) !== JSON.stringify(["configure", "plan", "verify", "update-check", "backup-create", "operations-pause", "frontier-pause", "deep-work-pause", "deep-work-resume", "deep-work-cancel", "deep-work-draft", "deep-work-prepare", "deep-work-stage", "deep-work-service-render"]) || manifest.controlSurface?.activationAuthority !== "external-operator-only") throw new Error("Local control action authority is invalid");
if (manifest.controlSurface?.onboardingSchema !== "./schemas/control-onboarding-v1.schema.json" || manifest.controlSurface?.actionSchema !== "./schemas/control-action-v1.schema.json" || manifest.controlSurface?.statusSchema !== "./schemas/control-status-v1.schema.json" || manifest.controlSurface?.updateStatusSchema !== "./schemas/control-update-status-v1.schema.json" || manifest.controlSurface?.recoveryGuideSchema !== "./schemas/control-recovery-guide-v1.schema.json" || manifest.controlSurface?.doctorSchema !== "./schemas/control-doctor-v1.schema.json" || manifest.controlSurface?.diagnosticsSchema !== "./schemas/control-diagnostics-v1.schema.json" || manifest.controlSurface?.policySchema !== "./schemas/control-policy-v1.schema.json" || manifest.controlSurface?.reviewSchema !== "./schemas/control-frontier-review-v1.schema.json" || manifest.controlSurface?.frontierBudgetRequestSchema !== "./schemas/control-frontier-budget-request-v1.schema.json" || manifest.controlSurface?.frontierBudgetProposalSchema !== "./schemas/control-frontier-budget-proposal-v1.schema.json" || manifest.controlSurface?.workDraftReviewSchema !== "./schemas/control-work-draft-reviews-v1.schema.json" || manifest.controlSurface?.workLaunchConfigSchema !== "./schemas/control-work-launch-config-v1.schema.json" || manifest.controlSurface?.workServiceConfigSchema !== "./schemas/control-work-service-config-v1.schema.json" || manifest.controlSurface?.workDraftReviewProjection !== "token-gated-exact-retained-objective-graph-budget-capability-verifier") throw new Error("Local control schema contract is invalid");
if (manifest.controlSurface?.chatSchema !== "./schemas/control-chat-v1.schema.json" || manifest.controlSurface?.chatTurnRequestSchema !== "./schemas/control-chat-turn-request-v1.schema.json" || manifest.controlSurface?.chatHandoffReceiptSchema !== "./schemas/control-chat-handoff-receipt-v1.schema.json" || manifest.controlSurface?.chatProjection !== "token-gated-private-bounded-text-content-free-turn-state-exact-inert-goal-handoff") throw new Error("Local control chat schema contract is invalid");
if (manifest.controlSurface?.accessAdapterConfigSchema !== "./schemas/control-access-adapter-v1.schema.json" || manifest.controlSurface?.accessAdapterBoundary !== "cloudflare-access-signed-jwt-distinct-workspace-and-mfa-approval-audiences" || manifest.controlSurface?.remoteGenericControlProxy !== false || manifest.controlSurface?.remotePortalDeploymentQualified !== false) throw new Error("Remote portal adapter boundary is invalid");
if (controlAccessAdapterSchema.properties?.listen?.properties?.host?.const !== "127.0.0.1" || controlAccessAdapterSchema.properties?.boundary?.const === undefined) throw new Error("Remote portal adapter schema boundary is invalid");
if (controlChatSchema.properties?.privacy?.properties?.reviewTokenRequired?.const !== true || controlChatSchema.properties?.privacy?.properties?.credentialsExposed?.const !== false || controlChatSchema.properties?.privacy?.properties?.pathsExposed?.const !== false || controlChatSchema.properties?.privacy?.properties?.rawLauncherOutputExposed?.const !== false || controlChatSchema.properties?.privacy?.properties?.genericCommandSurface?.const !== false || controlChatTurnRequestSchema.additionalProperties !== false || controlChatTurnRequestSchema.properties?.conversationHandle?.oneOf?.[1]?.pattern !== "^chatview-[a-f0-9]{24}$") throw new Error("Local control chat authority boundary is invalid");
const controlChatTurn = controlChatSchema.$defs?.turn;
if (controlChatTurn?.properties?.taskHandle?.pattern !== "^chattask-[a-f0-9]{24}$" || controlChatTurn?.properties?.activity?.maxItems !== 3 || !controlChatTurn?.required?.includes("phase") || !controlChatTurn?.required?.includes("activity") || !controlChatTurn?.required?.includes("handoff")) throw new Error("Local control durable chat task contract is invalid");
if (controlChatHandoffReceiptSchema.properties?.authority?.properties?.grantsExecution?.const !== false || controlChatHandoffReceiptSchema.properties?.target?.properties?.goalDeclarationSha256?.$ref !== "#/$defs/sha256" || controlChatHandoffReceiptSchema.properties?.source?.properties?.turnRecordSha256?.$ref !== "#/$defs/sha256") throw new Error("Local control chat handoff authority boundary is invalid");
if (controlWorkDraftReviewsSchema.properties?.privacy?.properties?.pathsExposed?.const !== false || controlWorkDraftReviewsSchema.properties?.privacy?.properties?.inputIdentitiesExposed?.const !== false || controlWorkDraftReviewsSchema.properties?.privacy?.properties?.grantsExecution?.const !== false || controlWorkDraftReviewsSchema.properties?.privacy?.properties?.grantsExternalEffects?.const !== false) throw new Error("Local control retained-draft review authority boundary is invalid");
if (controlUpdateStatusSchema.properties?.privacy?.properties?.browserCanActivate?.const !== false || controlUpdateStatusSchema.properties?.privacy?.properties?.browserCanRollback?.const !== false || controlUpdateStatusSchema.properties?.privacy?.properties?.browserCanRecover?.const !== false || controlUpdateStatusSchema.properties?.migration?.properties?.browserCanMigrate?.const !== false) throw new Error("Local control update-status authority boundary is invalid");
if (controlRecoveryGuideSchema.properties?.backup?.properties?.browserCanDecrypt?.const !== false || controlRecoveryGuideSchema.properties?.backup?.properties?.browserCanRestore?.const !== false || controlRecoveryGuideSchema.properties?.incident?.properties?.browserCanRecover?.const !== false || controlRecoveryGuideSchema.properties?.incident?.properties?.browserCanResume?.const !== false || controlRecoveryGuideSchema.properties?.incident?.properties?.pauseStateVerified?.const !== false) throw new Error("Local control recovery-guide authority boundary is invalid");
if (manifest.controlSurface?.frontierBudgetProposalBoundary !== "private-expiring-hash-bound-terminal-apply" || manifest.controlSurface?.browserBudgetApplyAuthority !== false || controlFrontierBudgetProposalSchema.properties?.browserCanApply?.const !== false || controlFrontierBudgetProposalSchema.properties?.browserCanActivate?.const !== false) throw new Error("Local control Frontier budget authority boundary is invalid");
if (JSON.stringify(controlStatusSchema.$defs?.diagnosticsSummary) !== JSON.stringify(controlDiagnosticsSchema.$defs?.summary)) throw new Error("Local control status and diagnostics summaries diverge");
if (manifest.controlSurface?.operatorActionPolicy !== "private-disabled-by-default" || manifest.controlSurface?.doctorProjection !== "rounded-read-only-no-process-no-network" || manifest.controlSurface?.incidentProjection !== "content-free-private-receipt-bound" || manifest.controlSurface?.incidentEvidenceBinding !== "sha256-action-result-private-log" || manifest.controlSurface?.browserReviewProjection !== "private-policy-gated-sanitized-frontier-capsule" || manifest.controlSurface?.reviewAccessBoundary !== "random-per-process-terminal-fragment-bearer" || manifest.controlSurface?.browserApprovalAuthority !== false || manifest.controlSurface?.browserIncidentRecoveryAuthority !== false || manifest.controlSurface?.browserResumeAuthority !== false || manifest.controlSurface?.browserRestoreAuthority !== false) throw new Error("Local control operator policy is invalid");
if (manifest.operationsRunner?.transportUser !== "pixel-ops-transport" || manifest.operationsRunner?.workloadUser !== "pixel-runner" || manifest.operationsRunner?.managedAuthority !== "transport-only-sudo" || manifest.operationsRunner?.workloadLogin !== false) throw new Error("Operations runner identity-separation contract is invalid");
if (manifest.webCourier?.requirementsHashMode !== "pip-require-hashes" || JSON.stringify(manifest.webCourier?.defaultAllowedPorts) !== "[80,443]") throw new Error("Web Courier dependency or egress contract is invalid");
if (manifest.privateBackup?.authentication !== "ssh-ed25519-detached-signature" || manifest.privateBackup?.restore !== "audited-transactional-rollback") throw new Error("Private backup authenticity or recovery contract is invalid");
const expectedReleaseUpdate = {
  schemaVersion: 1,
  channel: "stable",
  envelopeSchema: "./schemas/release-update-v1.schema.json",
  stageReceiptSchema: "./schemas/release-update-stage-v1.schema.json",
  rehearsalReceiptSchema: "./schemas/release-update-rehearsal-v1.schema.json",
  activationReceiptSchema: "./schemas/release-update-activation-v1.schema.json",
  activationResultSchema: "./schemas/release-update-activation-result-v1.schema.json",
  rollbackReceiptSchema: "./schemas/release-update-rollback-v1.schema.json",
  rollbackResultSchema: "./schemas/release-update-rollback-result-v1.schema.json",
  recoverySchema: "./schemas/release-update-recovery-v1.schema.json",
  cleanupReceiptSchema: "./schemas/release-update-cleanup-v1.schema.json",
  archiveReceiptSchema: "./schemas/release-update-archive-v1.schema.json",
  signature: "openssh-ed25519-detached",
  signatureNamespace: "pixel-release-update",
  qualificationSignature: "openssh-ed25519-detached",
  qualificationSignatureNamespace: "pixel-release-update-qualification",
  qualificationAuthority: "verify-only-no-publication-staging-activation",
  qualificationMode: "forward",
  minimumUpgradablePixel: "4.0.0",
  preparation: "verify-without-execution",
  staging: "private-copy-without-extraction",
  rehearsal: "private-syntax-and-host-contract-no-candidate-execution",
  activation: "external-exact-confirmation-transactional-apply",
  rollback: "single-use-update-bound-last-apply",
  recovery: "content-free-receipt-finalization-no-candidate-execution",
  cleanup: "exact-quarantine-audit-tombstone",
  archive: "exact-failed-rollback-preservation-outside-bounded-staging",
};
if (JSON.stringify(manifest.releaseUpdate) !== JSON.stringify(expectedReleaseUpdate)) throw new Error("Signed release update contract is invalid");
const expectedReactivationArchiveBridge = {
  schemaVersion: 1,
  receiptSchema: "./schemas/release-update-reactivation-archive-v1.schema.json",
  operation: "exact-terminal-no-live-mutation-reactivation-preservation-outside-bounded-staging",
  boundary: "terminal-no-live-mutation-reactivation-evidence-only-no-active-deployment-change",
};
if (version !== "4.3.27" || JSON.stringify(manifest.releaseReactivationArchive) !== JSON.stringify(expectedReactivationArchiveBridge)) throw new Error("Release reactivation archive bridge contract is invalid");
if (manifest.legacyCleanMigration?.schemaVersion !== 1 || manifest.legacyCleanMigration?.cli !== "./scripts/migrate-legacy-clean.py" || manifest.legacyCleanMigration?.evidenceSchema !== "./schemas/legacy-clean-migration-v1.schema.json" || manifest.legacyCleanMigration?.boundary !== "terminal-only-clean-migration-no-in-place-update" || manifest.legacyCleanMigration?.v1Contract?.sourcePixel !== "3.2.2" || manifest.legacyCleanMigration?.v1Contract?.targetPixel !== version || manifest.legacyCleanMigration?.activation !== "self-contained-migrate-legacy-clean-activate-transaction") throw new Error("Legacy clean-migration contract is invalid");
if (manifest.customizationKit?.schemaVersion !== 1 || manifest.customizationKit?.defaultEnabled !== false || manifest.customizationKit?.signature !== "openssh-ed25519-detached") throw new Error("Customization kit trust contract is invalid");
if (manifest.customizationKit?.clientOverlaySchema !== "./schemas/client-overlay-v1.schema.json" || manifest.customizationKit?.clientOverlayExample !== "./client-overlay.example.json" || manifest.customizationKit?.clientOverlayBoundary !== "private-client-state-outside-golden-core") throw new Error("Client overlay boundary is invalid");
assertJsonSchema(clientOverlayExample, clientOverlaySchema, "client-overlay.example.json");
assertJsonSchema(portalOutcomePixelSystemExample, portalOutcomePixelSystemSchema, "deploy/agent-comparison/pixel-system.example.json");
assertJsonSchema(portalOutcomePairSystemExample, portalOutcomePairSystemSchema, "deploy/agent-comparison/pair-system.example.json");
assertJsonSchema(workModelQualificationDockerExample, workModelQualificationDockerSchema, "deploy/work-controller/model-qualification-docker.example.json");
assertJsonSchema(workModelQualificationMaintenanceExample, workModelQualificationMaintenanceSchema, "deploy/work-controller/model-qualification-maintenance.example.json");
assertJsonSchema(workModelCampaignMaintenanceExample, workModelCampaignMaintenanceSchema, "deploy/work-controller/model-campaign-maintenance.example.json");
assertJsonSchema(manifest, releaseManifestSchema, "RELEASE-MANIFEST.json");
if (manifest.customizationKit?.gatewayAuthority !== "read-only-projection-no-network-no-credentials" || manifest.customizationKit?.serviceAuthority !== "offline-projection-only-no-credentials") throw new Error("Customization kit authority contract is invalid");
if (manifest.customizationKit?.serviceIdentity !== "dedicated-non-login-per-pack" || manifest.customizationKit?.projectionSharing !== "enabled-gateway-user-acl-only") throw new Error("Customization kit identity or projection-sharing contract is invalid");
if (manifest.customizationKit?.policyPackSchemas?.local !== "./schemas/local-capability-pack-v1.schema.json" || manifest.customizationKit?.policyPackSchemas?.operations !== "./schemas/operations-action-pack-v1.schema.json" || manifest.customizationKit?.policyPackSchemas?.frontier !== "./schemas/frontier-task-pack-v1.schema.json") throw new Error("Customization policy-pack schema contract is invalid");
if (manifest.qualification?.matrix !== "./QUALIFICATION-MATRIX.json" || manifest.qualification?.matrixSchema !== "./schemas/qualification-matrix-v1.schema.json" || manifest.qualification?.supportedHostSystemdEvidenceSchema !== "./schemas/supported-host-systemd-evidence-v1.schema.json" || manifest.qualification?.deepWorkEnduranceEvidenceSchema !== "./schemas/deep-work-endurance-evidence-v1.schema.json" || manifest.qualification?.optionalDeepWorkMultiDaySoakEvidenceSchema !== "./schemas/deep-work-multi-day-soak-evidence-v1.schema.json" || manifest.qualification?.promotionClaimsSchema !== "./schemas/promotion-claims-v1.schema.json" || manifest.qualification?.promotionReadinessSchema !== "./schemas/promotion-readiness-v1.schema.json" || manifest.qualification?.portalOutcomeTaskSchema !== "./schemas/portal-outcome-task-v1.schema.json" || manifest.qualification?.portalOutcomeTaskAdmissionSchema !== "./schemas/portal-outcome-task-admission-v1.schema.json" || manifest.qualification?.portalOutcomeTaskAdmitter !== "./scripts/portal_outcome_task.py" || manifest.qualification?.portalOutcomeModelContractSchema !== "./schemas/portal-outcome-model-contract-v1.schema.json" || manifest.qualification?.portalOutcomeInferenceContractSchema !== "./schemas/portal-outcome-inference-contract-v1.schema.json" || manifest.qualification?.portalOutcomeToolPolicySchema !== "./schemas/portal-outcome-tool-policy-v1.schema.json" || manifest.qualification?.portalOutcomeEnvironmentSchema !== "./schemas/portal-outcome-environment-v1.schema.json" || manifest.qualification?.portalOutcomeVerifierSchema !== "./schemas/portal-outcome-verifier-v1.schema.json" || manifest.qualification?.portalOutcomeSourceMaterializer !== "./scripts/portal_outcome_materialize_source.mjs" || manifest.qualification?.portalOutcomeBatteryMaterializer !== "./scripts/portal_outcome_materialize_battery.py" || manifest.qualification?.portalOutcomeRehearsalFixtureTool !== "./scripts/agent_comparison_fixture_tool.py" || manifest.qualification?.portalOutcomeRunAssembler !== "./scripts/portal_outcome_runner.py" || manifest.qualification?.portalOutcomeDeterministicVerifier !== "./scripts/portal_outcome_verifier.py" || manifest.qualification?.portalOutcomeCodexOrchestrator !== "./scripts/portal_outcome_orchestrate.py" || manifest.qualification?.portalOutcomePixelOrchestrator !== "./scripts/portal_outcome_pixel_orchestrate.py" || manifest.qualification?.portalOutcomePixelArm !== "./deploy/agent-comparison/pixel-arm.mjs" || manifest.qualification?.portalOutcomePixelSystemSchema !== "./schemas/portal-outcome-pixel-system-v1.schema.json" || manifest.qualification?.portalOutcomePixelSystemConfig !== "./deploy/agent-comparison/pixel-system.example.json" || manifest.qualification?.portalOutcomePixelSystemCli !== "./deploy/agent-comparison/pixel-system-cli.mjs" || manifest.qualification?.portalOutcomePixelLiveSystem !== "./scripts/portal_outcome_pixel_livesystem.py" || manifest.qualification?.portalOutcomePairSystemSchema !== "./schemas/portal-outcome-pair-system-v1.schema.json" || manifest.qualification?.portalOutcomePairSystemConfig !== "./deploy/agent-comparison/pair-system.example.json" || manifest.qualification?.portalOutcomePairRunner !== "./scripts/portal_outcome_pair.py" || manifest.qualification?.portalOutcomePairPreflightSchema !== "./schemas/portal-outcome-pair-preflight-v1.schema.json" || manifest.qualification?.portalOutcomePairPreflight !== "./scripts/portal_outcome_pair_preflight.py" || manifest.qualification?.portalOutcomePolicyCompatibility !== "./scripts/portal_outcome_policy_compatibility.py" || manifest.qualification?.portalOutcomeBatteryCampaignRunner !== "./scripts/portal_outcome_battery_campaign.py" || manifest.qualification?.portalOutcomeDsv4Dockerfile !== "./deploy/agent-comparison/Dockerfile.dsv4-vllm" || manifest.qualification?.portalOutcomeDsv4Launcher !== "./deploy/agent-comparison/dsv4-serve.sh" || manifest.qualification?.portalOutcomeDsv4ImageQualifier !== "./scripts/qualify-dsv4-image.sh" || manifest.qualification?.portalOutcomeCodexComparisonDockerfile !== "./deploy/agent-comparison/Dockerfile.codex-runner" || manifest.qualification?.portalOutcomeCodexWorkspaceCopier !== "./scripts/codex_comparison_workspace.py" || manifest.qualification?.portalOutcomeCodexSurfaceQualifier !== "./scripts/qualify_codex_comparison_surface.py" || manifest.qualification?.workCodexImageQualifier !== "./scripts/qualify-work-codex-images.sh" || manifest.qualification?.workRunnerImageQualifier !== "./scripts/qualify-work-runner-image.sh" || manifest.qualification?.portalOutcomeLiveSystem !== "./scripts/portal_outcome_livesystem.py" || manifest.qualification?.portalOutcomeRunSchema !== "./schemas/portal-outcome-run-v1.schema.json" || manifest.qualification?.portalOutcomeComparisonSchema !== "./schemas/portal-outcome-comparison-v1.schema.json" || manifest.qualification?.portalOutcomeComparator !== "./scripts/portal_outcome_evaluation.py" || manifest.qualification?.portalOutcomeCampaignPlanSchema !== "./schemas/portal-outcome-campaign-plan-v1.schema.json" || manifest.qualification?.portalOutcomeCampaignSchema !== "./schemas/portal-outcome-campaign-v1.schema.json" || manifest.qualification?.portalOutcomeCampaignRunner !== "./scripts/portal_outcome_campaign.py" || manifest.qualification?.requiredConsecutivePasses !== 2 || manifest.qualification?.automatedProviderCallsAllowed !== false || manifest.qualification?.publicationRequiresAllGates !== true) throw new Error("Qualification release contract is invalid");
if (manifest.qualification?.portalOutcomeTuningBaselineFreezeSchema !== "./schemas/portal-outcome-tuning-baseline-freeze-v1.schema.json") throw new Error("Held-out tuning-baseline freeze release contract is invalid");
if (manifest.qualification?.portalOutcomePairSystemBindingSchema !== "./schemas/portal-outcome-pair-system-binding-v1.schema.json" || manifest.qualification?.portalOutcomePairSystemBinding !== "./scripts/portal_outcome_pair_system.py") throw new Error("Destination-bound pair-system binding release contract is invalid");
if (manifest.qualification?.portalOutcomeRuntimeControl !== "./scripts/portal_outcome_runtime_control.py") throw new Error("Portal outcome cold/warm runtime-control release contract is invalid");
if (manifest.qualification?.portalOutcomeProductPathRouter !== "./scripts/portal_outcome_product_path.py") throw new Error("Portal outcome product-path router release contract is invalid");
if (manifest.qualification?.portalOutcomeResearchEvidence !== "./scripts/portal_outcome_research_evidence.py") throw new Error("Portal outcome Researcher evidence release contract is invalid");
if (manifest.qualification?.portalOutcomeAssistantEvidence !== "./scripts/portal_outcome_assistant_evidence.py" || manifest.qualification?.portalOutcomeAssistantRuntime !== "./scripts/portal_outcome_assistant_runtime.py" || manifest.qualification?.portalOutcomeAssistantSystem !== "./scripts/portal_outcome_assistant_system.py" || manifest.qualification?.portalOutcomeAssistantModelProxy !== "./deploy/agent-comparison/assistant-model-proxy.mjs" || manifest.qualification?.portalOutcomeAssistantTemplateSchema !== "./schemas/portal-outcome-assistant-template-v1.schema.json" || manifest.qualification?.portalOutcomeAssistantTemplateConfig !== "./deploy/agent-comparison/assistant-template.example.json") throw new Error("Portal outcome Assistant lifecycle release contract is invalid");
if (manifest.qualification?.portalOutcomeResearchFixtureSchema !== "./schemas/portal-outcome-research-fixture-v1.schema.json" || manifest.qualification?.portalOutcomeResearchFixtureAdapter !== "./deploy/agent-comparison/research-fixture-adapter.mjs") throw new Error("Portal outcome admitted Researcher fixture release contract is invalid");
if (manifest.qualification?.portalOutcomeTrialCoverageSchema !== "./schemas/portal-outcome-trial-coverage-v1.schema.json" || manifest.qualification?.portalOutcomeTrialCoverage !== "./scripts/portal_outcome_trial_coverage.py") throw new Error("Portal outcome owner-trial coverage release contract is invalid");
if (manifest.qualification?.workModelQualificationDockerSchema !== "./schemas/work-model-qualification-docker-v1.schema.json" || manifest.qualification?.workModelQualificationDockerConfig !== "./deploy/work-controller/model-qualification-docker.example.json" || manifest.qualification?.workModelQualificationDockerRunner !== "./deploy/work-controller/model-qualification-docker.mjs" || manifest.qualification?.workModelQualificationContainer !== "./deploy/work-controller/model-qualification-container.mjs" || manifest.qualification?.workModelQualificationBridge !== "./deploy/work-controller/model-qualification-bridge.mjs") throw new Error("Docker model qualification release contract is invalid");
if (manifest.qualification?.workModelQualificationMaintenanceSchema !== "./schemas/work-model-qualification-maintenance-v1.schema.json" || manifest.qualification?.workModelQualificationMaintenanceConfig !== "./deploy/work-controller/model-qualification-maintenance.example.json" || manifest.qualification?.workModelQualificationMaintenanceRunner !== "./deploy/work-controller/model-qualification-maintenance.mjs" || manifest.qualification?.workModelMaintenanceCustody !== "./deploy/work-controller/maintenance-custody.mjs") throw new Error("Model qualification maintenance release contract is invalid");
if (manifest.qualification?.workModelCampaignMaintenanceSchema !== "./schemas/work-model-campaign-maintenance-v1.schema.json" || manifest.qualification?.workModelCampaignMaintenanceConfig !== "./deploy/work-controller/model-campaign-maintenance.example.json" || manifest.qualification?.workModelCampaignMaintenanceRunner !== "./deploy/work-controller/model-campaign-maintenance.mjs") throw new Error("Model campaign maintenance release contract is invalid");
if (JSON.stringify(qualification.supportedHosts) !== JSON.stringify(manifest.supportedHosts) || JSON.stringify(qualification.capabilityProfiles?.map((item) => item.id)) !== JSON.stringify(manifest.capabilityProfiles) || qualification.promotion?.requiredConsecutivePasses !== 2 || qualification.promotion?.automatedProviderCallsAllowed !== false || qualification.promotion?.publicationRequiresAllGates !== true) throw new Error("Qualification matrix differs from the release manifest");
if (manifest.customizationKit?.policyComposition?.local !== "signed-observe-only-inventory" || manifest.customizationKit?.policyComposition?.operations !== "explicit-private-target-binding" || manifest.customizationKit?.policyComposition?.frontier !== "base-policy-restrictions-only") throw new Error("Customization policy-pack composition contract is invalid");
if (manifest.customizationKit?.minimumOpenClawPluginApi !== "2026.5.17") throw new Error("Customization kit OpenClaw compatibility floor is invalid");
for (const [label, artifact] of Object.entries({
  nodeRuntime: manifest.nodeRuntime,
  openclawInstaller: manifest.openclawInstaller,
  openclawPackage: manifest.openclawPackage,
  discordPlugin: manifest.openclawPluginPackages?.discord,
  searxngPlugin: manifest.openclawPluginPackages?.searxng,
  llamaCppPlugin: manifest.openclawPluginPackages?.llamaCpp,
})) {
  if (!artifact?.url?.startsWith("https://")) throw new Error(`${label} must use an HTTPS release URL`);
  if (!/^[0-9a-f]{64}$/.test(artifact?.sha256 ?? "")) throw new Error(`${label} must have a pinned SHA-256`);
  if (artifact?.mirrors !== undefined) {
    if (!Array.isArray(artifact.mirrors) || artifact.mirrors.length > 4 || new Set(artifact.mirrors).size !== artifact.mirrors.length) {
      throw new Error(`${label} mirrors must be a bounded unique array`);
    }
    for (const mirror of artifact.mirrors) {
      if (!/^https:\/\//.test(mirror)) throw new Error(`${label} mirror must use HTTPS`);
    }
  }
}
for (const [label, artifact] of Object.entries({ openclaw: manifest.openclawPackage, ...manifest.openclawPluginPackages })) {
  if (!/^sha512-[A-Za-z0-9+/]+=*$/.test(artifact.integrity)) {
    throw new Error(`${label} package must have pinned npm integrity`);
  }
}

const currentCombinations = compatibility.combinations.filter((item) =>
  item.pixel === manifest.pixel
  && item.openclaw === manifest.openclaw
  && JSON.stringify(Object.entries(item.plugins).sort()) === JSON.stringify(Object.entries(manifest.openclawPlugins).sort())
  && ["supported", "candidate"].includes(item.status));
if (currentCombinations.length !== 1) throw new Error("Current release has no unique supported/candidate compatibility record");
if (!/^[0-9a-f]{40}$/.test(currentCombinations[0].evidence?.sourceCommit ?? "")) throw new Error("Current release qualification source commit is invalid");
const frozenPixelFour = compatibility.combinations.filter((item) => item.pixel === "4.0.0");
if (frozenPixelFour.length !== 1 || sha256(JSON.stringify(frozenPixelFour[0])) !== "c5cfbf72f8c36c9280b969db39c8298325b76726f315a2e36494625f8f9ed8c4") throw new Error("Qualified Pixel 4.0 compatibility evidence changed");
if (sha256(await read("LIVE-AUDIT-4.0.0.md")) !== "8217dea6a08f1f48ca634f3ae7ddf845e96f332e02abde8b59c77244a1b65305") throw new Error("Qualified Pixel 4.0 live-audit evidence changed");
const frozenPixelFourOne = compatibility.combinations.filter((item) => item.pixel === "4.1.0");
if (frozenPixelFourOne.length !== 1 || sha256(JSON.stringify(frozenPixelFourOne[0])) !== "b46b5682dd4bff5e4271502ac1de8f43246bd0cbab5258c4a932342f3a7c5f13") throw new Error("Qualified Pixel 4.1 candidate compatibility evidence changed");
if (sha256(await read("LIVE-AUDIT-4.1.0.md")) !== "1f33d81173383256627f402ba2aa86e856cdb351887e09c8ce19f129ac9d47f5") throw new Error("Qualified Pixel 4.1 candidate live-audit evidence changed");

const allowedGeneratedPins = new Set([
  ".env.example",
  "OPENCLAW-COMPATIBILITY.json",
  "OPENCLAW-COMPATIBILITY.md",
  "RELEASE-MANIFEST.json",
  "THIRD_PARTY_NOTICES.md",
  "configs/openclaw.patch.example.json5",
  "deploy/compose.yaml",
  "deploy/sandbox/Dockerfile",
  "deploy/work-runner/Dockerfile",
  "deploy/work-codex-provider/Dockerfile",
  "deploy/work-codex-provider/Dockerfile.egress-proxy",
  "deploy/agent-comparison/assistant-template.example.json",
  "deploy/work-runner/native-addons.json",
  "deploy/work-broker/policy.example.json",
  "scripts/generated/release-constants.json",
  "scripts/generated/release.env",
]);
const ignoredEvidence = new Set(["CHANGELOG.md", "LIVE-AUDIT.md"]);
const pinValues = new Set([
  manifest.openclaw,
  manifest.nodeRuntime.version,
  manifest.nodeRuntime.url,
  manifest.openclawInstaller.url,
  ...(manifest.openclawInstaller.mirrors ?? []),
  manifest.openclawPackage.url,
  manifest.sandboxImage,
  manifest.baseImage,
  ...Object.values(manifest.openclawPluginPackages).map((item) => item.url),
  ...Object.values(manifest.referenceImages),
]);
const ignoredDirectories = new Set([".git", ".generated", ".openclaw", ".runtime", ".secrets", "dist", "node_modules", "secrets", "__pycache__"]);
const authoredTextExtensions = new Set(["", ".env", ".js", ".json", ".json5", ".md", ".mjs", ".sh", ".yaml", ".yml"]);
async function findAuthoredPinDrift(directory = root) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) continue;
    const absolute = join(directory, entry.name);
    const relative = absolute.slice(root.length + 1).replaceAll("\\", "/");
    if (entry.isDirectory()) {
      if (!ignoredDirectories.has(entry.name)) await findAuthoredPinDrift(absolute);
      continue;
    }
    const extension = entry.name === ".env.example" ? ".env" : entry.name.includes(".") ? entry.name.slice(entry.name.lastIndexOf(".")) : "";
    if (relative === ".env" || (entry.name.startsWith(".env.") && entry.name !== ".env.example")) continue;
    if (!authoredTextExtensions.has(extension) || allowedGeneratedPins.has(relative) || ignoredEvidence.has(relative) || /^LIVE-AUDIT(?:-[0-9.]+)?\.md$/.test(relative)) continue;
    const contents = await readFile(absolute, "utf8");
    for (const value of pinValues) {
      if (contents.includes(value)) throw new Error(`${relative} duplicates an authored release pin; generate it from RELEASE-MANIFEST.json instead`);
    }
  }
}
await findAuthoredPinDrift();

const contracts = [
  [".env.example", manifest.openclaw],
  [".env.example", manifest.openclawPlugins["@openclaw/discord"]],
  [".env.example", manifest.openclawPlugins["@openclaw/searxng-plugin"]],
  ["scripts/package-release.sh", "generate-release-sbom.mjs"],
  ["scripts/package-release.sh", "generate-release-provenance.mjs"],
  ["scripts/package-release.sh", "generate-release-update.mjs"],
  ["scripts/package-release.sh", "release-identity.mjs"],
  ["scripts/plan.sh", "dist/release-identity.json"],
  ["scripts/plan.sh", "dist/source-runtime.sha256"],
  ["scripts/lib/release-build.sh", "install-manifest.sha256"],
  ["scripts/lib/release-build.sh", 'mktemp "$(dirname "$target")/.install-manifest.XXXXXXXXXX"'],
  ["scripts/verify.sh", "Installed release source identity does not match the active source"],
  ["tests/static.sh", "tests/release-identity.test.mjs"],
  ["tests/static.sh", "tests/runtime-attestation.test.mjs"],
  ["tests/e2e.sh", "personal_occurrences_before"],
  ["scripts/verify.sh", "runtime-attestation.mjs"],
  ["deploy/source-broker/broker.py", "calendar_action_journal"],
  ["deploy/source-broker/broker.py", "PIXEL-BUNDLED-JOURNAL-VENDOR-SECRETS-BEGIN"],
  ["deploy/github-broker/broker.py", "PIXEL-BUNDLED-JOURNAL-VENDOR-SECRETS-BEGIN"],
  ["deploy/action_journal/__init__.py", "reconcile-before-retry"],
  ["scripts/install-source-broker.sh", "PIXEL_SOURCE_BROKER_INSTALL_DIR/broker.py"],
  ["scripts/lib/release-build.sh", "deploy/source-broker/broker.py"],
  ["scripts/lib/broker-bytes.sh", "pixel_broker_backup_files"],
  ["scripts/lib/broker-bytes.sh", "action_journal/__init__.py|0644|source-broker/action_journal/__init__.py"],
  ["tests/static.sh", "vendor-secrets-install.test.sh"],
  ["tests/static.sh", "test_action_journal.py"],
  ["tests/static.sh", "schemas/external-action-journal-event-v1.schema.json"],
  ["pixel", "github-action"],
  ["pixel", "action-journal"],
  ["deploy/action_journal/cli.py", "confirm-head-sha256"],
  ["deploy/github-broker/broker.py", "provider-reconciliation-marker"],
  ["deploy/github-broker/broker.py", "MAX_RECONCILE_PAGES"],
  ["tests/static.sh", "test_github_broker.py"],
  ["tests/static.sh", "schemas/github-action-proposal-v1.schema.json"],
  ["scripts/package-release.sh", "pixel-$version.cdx.json"],
  ["scripts/package-release.sh", "pixel-$version.intoto.jsonl"],
  ["scripts/package-release.sh", "pixel-$version.update.json"],
  ["tests/static.sh", "tests/portal-user-journey-corpus.test.mjs"],
  ["tests/static.sh", "schemas/portal-user-journey-corpus-v1.schema.json"],
  ["tests/static.sh", "security-evals/portal-user-journeys/corpus-v1.json"],
  ["tests/static.sh", "test_agent_comparison_fixture_tool.py"],
  ["tests/static.sh", "test_agent_comparison_rehearsals.py"],
  ["tests/static.sh", "test_portal_outcome_product_path.py"],
  ["tests/static.sh", "test_portal_outcome_assistant_evidence.py"],
  ["tests/static.sh", "test_portal_outcome_assistant_runtime.py"],
  ["tests/static.sh", "test_portal_outcome_assistant_system.py"],
  ["scripts/agent_comparison_fixture_tool.py", "non-promotional comparison rehearsal"],
  ["scripts/portal_outcome_product_path.py", "surrogateProfileAllowed"],
  ["scripts/portal_outcome_product_path.py", "deep-work-controller"],
  ["scripts/portal_outcome_product_path.py", "require_exact_evidence"],
  ["scripts/portal_outcome_research_evidence.py", "semanticEntailmentVerified"],
  ["scripts/portal_outcome_research_evidence.py", "primarySourcePreferenceVerified"],
  ["scripts/portal_outcome_research_evidence.py", "freshnessLabelsVerified"],
  ["scripts/portal_outcome_assistant_evidence.py", "expected_model"],
  ["scripts/portal_outcome_assistant_evidence.py", "validate_chat_model_receipt"],
  ["scripts/portal_outcome_assistant_runtime.py", "ControlState.chat_turn"],
  ["scripts/portal_outcome_assistant_system.py", "validate_model_proxy_pair"],
  ["scripts/portal_outcome_assistant_system.py", "Assistant model proxy listener survived teardown"],
  ["deploy/agent-comparison/assistant-model-proxy.mjs", "createModelProxyFromConfigFile"],
  ["scripts/render-config.mjs", "PIXEL_AGENT_TOOL_ALLOWLIST"],
  ["schemas/portal-outcome-assistant-template-v1.schema.json", "proxyListenPort"],
  ["security-evals/agent-comparison/task-battery-v1.json", '"proofClass": "deterministic-non-promotional-rehearsal"'],
  ["PRODUCTIZATION.md", "A mock, scripted answer, self-grade, or synthetic-only run cannot prove"],
  ["PRODUCTIZATION.md", "outcome is reconciled before retry"],
  ["PRODUCTIZATION.md", "Pixel and Codex against the same admitted task specification"],
  ["PRODUCTIZATION.md", "A first-time user must not need to discover a separate"],
  ["PRODUCTIZATION.md", "always ask, automatically"],
  ["security-evals/portal-user-journeys/corpus-v1.json", '"unexplainedCapabilityDeltaMayPass": false'],
  ["security-evals/portal-user-journeys/corpus-v1.json", '"id": "chat-inline-authority"'],
  ["deploy/source-broker/broker.py", "--reconcile-create"],
  ["deploy/source-broker/broker.py", "--reconcile-update"],
  ["deploy/source-broker/broker.py", "legacy-provider-etag-proves-not-applied"],
  ["deploy/source-broker/broker.py", "pixelProposalSha256"],
  ["tests/test_source_broker.py", "test_create_precommits_provider_identity_and_reconciles_lost_success_without_retry"],
  ["pixel", "source-reconcile"],
  ["pixel", "migrate-legacy-clean"],
  ["scripts/verify.sh", "--expected-install-dir"],
  ["scripts/migrate-legacy-clean.py", "terminal-only clean migration"],
  ["scripts/migrate-legacy-clean.py", "self-contained exact transaction"],
  ["scripts/migrate-legacy-clean.py", "restoreReceiptSha256"],
  ["scripts/migrate-legacy-clean.py", "validate_restore_audit"],
  ["scripts/migrate-legacy-clean.py", "check_install_release"],
  ["scripts/migrate-legacy-clean.py", "restore receipt predates the exact rehearsal"],
  ["scripts/migrate-legacy-clean.py", "restore receipt is implausibly future-dated"],
  ["scripts/migrate-legacy-clean.py", "restore receipt knowledge booleans are contradictory"],
  ["scripts/migrate-legacy-clean.py", "attestation connectors must cover the entire expected connector set exactly once"],
  ["schemas/legacy-clean-migration-v1.schema.json", "pixel-legacy-clean-migration-completion"],
  ["schemas/legacy-clean-migration-v1.schema.json", "restoreReceiptSha256"],
  ["scripts/restore-receipt.py", "Reserve, finalize, or abort a private restore receipt"],
  ["scripts/restore-receipt.py", "pixel-restore-receipt-reservation"],
  ["scripts/restore-receipt.py", "restore receipt reservation was replaced or altered"],
  ["scripts/restore-private-state.sh", "restore-receipt.py\" reserve"],
  ["scripts/restore-private-state.sh", "restore-receipt.py\" finalize"],
  ["scripts/restore-private-state.sh", "restore succeeded but receipt finalization failed; do not run migration finalize"],
  ["tests/test_restore_receipt.py", "test_finalize_refuses_swapped_inode"],
  ["tests/static.sh", "tests/test_restore_receipt.py"],
  ["RELEASE-MANIFEST.json", "legacyCleanMigration"],
  ["tests/static.sh", "tests/test_legacy_clean_migration.py"],
  ["tests/static.sh", "tests/legacy-clean-migration-schema.test.mjs"],
  ["scripts/reconcile-source-action.sh", "PIXEL_SOURCE_RECONCILE_UNIT"],
  ["scripts/configure.mjs", "pixel-source-reconcile@.service"],
  ["tests/static.sh", "tests/deep-work-contract.test.mjs"],
  ["tests/static.sh", "tests/work-broker.test.mjs"],
  ["tests/static.sh", "tests/work-research-broker.test.mjs"],
  ["tests/static.sh", "tests/work-research-pressure.test.mjs"],
  ["tests/static.sh", "tests/work-research-tool.test.mjs"],
  ["tests/static.sh", "deploy/work-research-broker/*.mjs"],
  ["tests/static.sh", "schemas/work-research-query-v1.schema.json"],
  ["tests/static.sh", "schemas/work-research-batch-v1.schema.json"],
  ["tests/static.sh", "schemas/work-research-retrieval-v1.schema.json"],
  ["tests/static.sh", "schemas/work-research-report-proposal-v1.schema.json"],
  ["tests/static.sh", "schemas/work-research-report-v1.schema.json"],
  ["tests/static.sh", "schemas/work-research-verification-v1.schema.json"],
  ["tests/static.sh", "schemas/work-research-revision-review-v1.schema.json"],
  ["tests/static.sh", "schemas/work-research-revision-history-v1.schema.json"],
  ["tests/static.sh", "tests/work-research-revision-review.test.mjs"],
  ["tests/static.sh", "schemas/portal-outcome-trial-coverage-v1.schema.json"],
  ["tests/static.sh", "tests/test_portal_outcome_trial_coverage.py"],
  ["tests/static.sh", "schemas/work-research-tool-request-v1.schema.json"],
  ["tests/static.sh", "schemas/work-research-tool-response-v1.schema.json"],
  ["tests/static.sh", "schemas/work-data-artifact-manifest-v1.schema.json"],
  ["tests/static.sh", "schemas/work-data-report-proposal-v1.schema.json"],
  ["tests/static.sh", "schemas/work-data-report-v1.schema.json"],
  ["tests/static.sh", "schemas/work-data-verification-v1.schema.json"],
  ["tests/static.sh", "schemas/work-data-runtime-v1.schema.json"],
  ["tests/static.sh", "tests/work-data-runtime.test.mjs"],
  ["tests/static.sh", "schemas/work-builder-runtime-v1.schema.json"],
  ["tests/static.sh", "tests/work-builder-runtime.test.mjs"],
  ["tests/static.sh", "schemas/work-capability-retention-evidence-v1.schema.json"],
  ["tests/static.sh", "security-evals/assurance/builder-capability-corpus-v1.json"],
  ["tests/static.sh", "tests/work-capability-retention.test.mjs"],
  ["tests/static.sh", "tests/work-capability-retention-live.mjs"],
  ["tests/static.sh", "tests/work-data-artifacts.test.mjs"],
  ["tests/static.sh", "tests/work-docker-data-lab.test.mjs"],
  ["tests/static.sh", "tests/work-data-lab-live.mjs"],
  ["tests/static.sh", "tests/fixtures/work/fake-llama-data-lab.mjs"],
  ["deploy/work-broker/broker.mjs", "compile-researcher"],
  ["deploy/work-broker/broker.mjs", "compile-data-lab"],
  ["deploy/work-research-broker/ledger.mjs", "conservative"],
  ["deploy/work-research-broker/search.mjs", "127.0.0.1"],
  ["deploy/work-research-broker/retrieval.mjs", "research_receipt"],
  ["deploy/work-research-broker/pipeline.mjs", "failReservedResearchQuery"],
  ["deploy/work-research-broker/citation-verifier.mjs", "semanticEntailmentVerified"],
  ["deploy/work-research-broker/report-finalizer.mjs", "batchSha256s"],
  ["deploy/work-controller/research-revision-review.mjs", "backendOutputUsedAsScore"],
  ["deploy/work-controller/research-revision-review.mjs", "buildResearchRevisionHistory"],
  ["deploy/work-research-broker/research-service.mjs", "maximumQueueEntries"],
  ["deploy/work-research-broker/tool-queue.mjs", "job-scoped tool call"],
  ["deploy/work-runner/research-tool.mjs", "/run/pixel/research"],
  ["deploy/work-runner/research-tool.mjs", "directNetwork: false"],
  ["deploy/work-runner/docker-researcher.mjs", "--trusted-extension"],
  ["deploy/work-runner/docker-researcher.mjs", "split-job-scoped-queue"],
  ["deploy/work-runner/docker-supervisor.mjs", "runResearcherDockerLifecycle"],
  ["deploy/work-runner/docker-supervisor.mjs", "runDataLabDockerLifecycle"],
  ["deploy/work-runner/docker-supervisor.mjs", "runScoutDockerLifecycle"],
  ["deploy/work-runner/docker-supervisor.mjs", "job-only-until-successful-cleanup"],
  ["deploy/work-runner/scout-report.mjs", "semanticEntailmentVerified: false"],
  ["deploy/work-runner/docker-data-lab.mjs", "private-entrypoint.sh"],
  ["deploy/work-runner/docker-data-lab.mjs", "fresh networkless replay"],
  ["deploy/work-runner/data-artifacts.mjs", "Exact replay proves reproducibility, not semantic truth"],
  ["deploy/work-model-proxy/proxy.mjs", "pixel_research"],
  ["deploy/work-model-proxy/proxy.mjs", "MAX_PIXEL_PLUGIN_TOOLS = 128"],
  ["deploy/work-model-proxy/proxy.mjs", "pixelPluginToolPattern"],
  ["tests/work-model-proxy.test.mjs", "an unlisted valid tool is denied"],
  ["tests/static.sh", "tests/work-safe-tar.test.mjs"],
  ["tests/static.sh", "tests/work-rpc-framing.test.mjs"],
  ["tests/static.sh", "tests/work-rpc-client.test.mjs"],
  ["tests/static.sh", "tests/work-runner-core.test.mjs"],
  ["tests/static.sh", "tests/work-builder-volume.test.mjs"],
  ["tests/static.sh", "tests/work-checkpoints.test.mjs"],
  ["tests/static.sh", "tests/work-goals.test.mjs"],
  ["tests/static.sh", "tests/work-goal-draft.test.mjs"],
  ["tests/static.sh", "tests/work-goal-assemble.test.mjs"],
  ["tests/static.sh", "tests/work-goal-launch-prepare.test.mjs"],
  ["tests/static.sh", "tests/work-goal-real-crash-e2e.test.mjs"],
  ["tests/static.sh", "tests/work-input-pack.test.mjs"],
  ["tests/static.sh", "schemas/work-input-selection-v1.schema.json"],
  ["tests/static.sh", "schemas/work-input-pack-v1.schema.json"],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-input-selection-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-input-pack-v1.schema.json")'],
  ["deploy/work-controller/input-pack-cli.mjs", "safeTarInternals.projectedPath"],
  ["deploy/work-controller/input-pack-cli.mjs", "dataset ${dataset.datasetId} resolves to an inert control path"],
  ["deploy/work-controller/goal-draft-cli.mjs", '"analyze-data": "data-lab"'],
  ["deploy/work-controller/goal-draft-cli.mjs", "local-reproducible"],
  ["deploy/work-controller/input-pack-cli.mjs", "grantsExecution: false"],
  ["pixel", 'work-input-pack) exec node "$ROOT/deploy/work-controller/input-pack-cli.mjs"'],
  ["tests/static.sh", "schemas/work-goal-brief-v1.schema.json"],
  ["tests/static.sh", "schemas/work-input-catalog-v1.schema.json"],
  ["tests/static.sh", "schemas/work-goal-draft-v1.schema.json"],
  ["tests/static.sh", "schemas/work-goal-assembly-v1.schema.json"],
  ["tests/static.sh", "schemas/work-goal-launch-preparation-v1.schema.json"],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-brief-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-input-catalog-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-draft-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-assembly-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-launch-preparation-v1.schema.json")'],
  ["deploy/work-controller/goal-draft-cli.mjs", "validateWorkJobAgainstPolicy"],
  ["deploy/work-controller/goal-draft-cli.mjs", "verifyInputObjects"],
  ["deploy/work-controller/goal-draft-cli.mjs", "grantsScheduling: false"],
  ["pixel", 'work-goal-draft) exec node "$ROOT/deploy/work-controller/goal-draft-cli.mjs"'],
  ["pixel", 'work-goal-assemble) exec node "$ROOT/deploy/work-controller/goal-assemble-cli.mjs"'],
  ["pixel", 'work-goal-launch) exec node "$ROOT/deploy/work-controller/goal-launch-prepare-cli.mjs"'],
  ["deploy/work-controller/goal-assemble-cli.mjs", "confirm-draft-sha256"],
  ["deploy/work-controller/goal-assemble-cli.mjs", "bindingOutputPath"],
  ["deploy/work-controller/goal-assemble-cli.mjs", "grantsServiceActivation: false"],
  ["deploy/work-controller/goal-launch-prepare-cli.mjs", "containsExpiringChildLeases: true"],
  ["deploy/work-controller/goal-launch-prepare-cli.mjs", "createsReadyGoalState: false"],
  ["deploy/work-controller/goal-launch-prepare-cli.mjs", "verifyInputObjects"],
  ["deploy/work-controller/goal-launch-prepare-cli.mjs", "bindingOutputPath"],
  ["deploy/work-controller/goal-launch-prepare-cli.mjs", "pixel-work-goal-launch-inspect"],
  ["deploy/work-controller/goal-launch-prepare-cli.mjs", "pixel-work-goal-launch-stage"],
  ["schemas/control-work-draft-request-v1.schema.json", "dependsOn"],
  ["control/server.py", "unique earlier milestone numbers"],
  ["deploy/work-controller/goal-controller-prepare-cli.mjs", "bindingOutputPath"],
  ["README.md", "work-goal-assemble"],
  ["README.md", "work-goal-launch"],
  ["DEEP-WORK.md", "work-goal-assemble"],
  ["DEEP-WORK.md", "work-goal-launch"],
  ["scripts/deep-work-endurance-probe.mjs", "SIGKILL"],
  ["scripts/deep-work-endurance-probe.mjs", "minimalWorkerEnvironment"],
  ["scripts/deep-work-endurance-probe.mjs", "freshProcessRecoveries"],
  ["scripts/deep-work-endurance-probe.mjs", "validateEnduranceEvidence"],
  ["scripts/deep-work-endurance-probe.mjs", "repeated-diamond"],
  ["scripts/deep-work-endurance-probe.mjs", "buildEnduranceGraph"],
  ["scripts/deep-work-service-qualification.mjs", "prepared-paused"],
  ["scripts/deep-work-service-qualification.mjs", "watchdogRole: \"liveness-only\""],
  ["tests/static.sh", "tests/deep-work-service-qualification.test.mjs"],
  ["schemas/deep-work-endurance-evidence-v1.schema.json", "pixel-deep-work-real-process-endurance"],
  ["scripts/deep-work-multi-day-soak.mjs", "minimumElapsedSeconds: 172800"],
  ["scripts/deep-work-multi-day-soak.mjs", "requires a valid systemd INVOCATION_ID"],
  ["scripts/deep-work-multi-day-soak.mjs", "is not running inside its exact systemd service cgroup"],
  ["scripts/deep-work-multi-day-soak.mjs", "preAdmissionLeaseRefreshes"],
  ["scripts/deep-work-multi-day-soak.mjs", "validateMultiDaySoakEvidence"],
  ["scripts/deep-work-multi-day-soak.mjs", "runtimeSnapshotSha256"],
  ["scripts/deep-work-multi-day-soak.mjs", "source commit/tree differs from the clean checked-out source"],
  ["schemas/deep-work-multi-day-soak-evidence-v1.schema.json", "pixel-deep-work-multi-day-soak-evidence"],
  ["deploy/work-controller/deep-work-soak-service-units.mjs", "OnCalendar=*-*-* *:00:00 UTC"],
  ["deploy/work-controller/deep-work-soak-service-units.mjs", "PrivateNetwork=true"],
  ["deploy/work-controller/deep-work-soak-service-units.mjs", "INVOCATION_ID=\\${INVOCATION_ID}"],
  ["deploy/work-controller/deep-work-soak-service-units.mjs", "--confirm-config-sha256"],
  ["deploy/work-controller/deep-work-soak-service-lifecycle.mjs", "installed-inactive"],
  ["deploy/work-controller/deep-work-soak-service-lifecycle.mjs", "removed-private-campaign-retained"],
  ["tests/static.sh", "tests/work-goal-multi-day-soak.test.mjs"],
  ["tests/static.sh", "tests/work-goal-multi-day-soak-service-units.test.mjs"],
  ["tests/static.sh", "tests/work-goal-multi-day-soak-service-lifecycle.test.mjs"],
  ["QUALIFICATION-MATRIX.json", "deep-work-event-horizon"],
  ["QUALIFICATION.md", "385 fresh operating-system processes"],
  ["QUALIFICATION.md", "multi-provider-local-first"],
  ["QUALIFICATION-MATRIX.json", "deep-work-real-crash-endurance"],
  ["QUALIFICATION-MATRIX.json", "capability-pack-live-runtime"],
  ["QUALIFICATION.md", "49 fresh controller processes"],
  ["scripts/promotion_readiness.py", "deep-work-event-horizon"],
  ["THREAT-MODEL.md", "Guided draft assembly"],
  ["tests/static.sh", "tests/work-goal-prepare.test.mjs"],
  ["tests/static.sh", "tests/work-goal-controller-prepare.test.mjs"],
  ["tests/static.sh", "tests/work-goal-stage.test.mjs"],
  ["tests/static.sh", "schemas/work-goal-bundle-v1.schema.json"],
  ["tests/static.sh", "deploy/work-controller/goal-declaration.example.json"],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-declaration-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-bundle-v1.schema.json")'],
  ["deploy/work-controller/goal-prepare-cli.mjs", "validateWorkGoalBundleManifest"],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-controller-environment-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-controller-bundle-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-stage-v1.schema.json")'],
  ["deploy/work-controller/goal-controller-prepare-cli.mjs", "validateWorkJobAgainstPolicy"],
  ["deploy/work-controller/goal-controller-prepare-cli.mjs", "exactExecutableSha256"],
  ["deploy/work-controller/goal-stage-cli.mjs", "containsExactChildLeases: true"],
  ["deploy/work-controller/goal-stage-cli.mjs", "discardPreparedRun"],
  ["deploy/work-controller/goal-prepare-cli.mjs", "exactAggregateBudgets"],
  ["deploy/work-controller/goal-prepare-cli.mjs", "grantsScheduling: false"],
  ["pixel", 'work-goal-prepare) exec node "$ROOT/deploy/work-controller/goal-prepare-cli.mjs"'],
  ["pixel", 'work-goal-controller-prepare) exec node "$ROOT/deploy/work-controller/goal-controller-prepare-cli.mjs"'],
  ["pixel", 'work-goal-stage) exec node "$ROOT/deploy/work-controller/goal-stage-cli.mjs"'],
  ["pixel", 'work-compile) exec node "$ROOT/deploy/work-broker/broker.mjs"'],
  ["tests/static.sh", "tests/work-goal-fleet.test.mjs"],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-fleet-v1.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-fleet-controller-v2.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-fleet-host-evidence-v2.schema.json")'],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-goal-fleet-host-probe-v1.schema.json")'],
  ["deploy/work-controller/goal-fleet.mjs", "recover-active-turn"],
  ["deploy/work-controller/goal-fleet.mjs", "goal fleet child jobs exceed aggregate"],
  ["deploy/work-controller/goal-fleet-cycle-cli.mjs", "reconciled-prior-turn"],
  ["deploy/work-controller/goal-fleet-cycle-cli.mjs", "authoritative goal ledger"],
  ["deploy/work-controller/goal-fleet-cycle-cli.mjs", "pixel-work-goal-fleet-status"],
  ["tests/work-goal-fleet.test.mjs", "diagnoses due or expired capacity"],
  ["deploy/work-controller/goal-fleet-cycle-cli.mjs", "hostEvidenceLedger"],
  ["deploy/work-controller/goal-fleet.mjs", "disposable and retained disk ceilings exceed measured headroom"],
  ["deploy/work-controller/goal-fleet-host-evidence-cli.mjs", "CPU quota must be a finite whole-core ceiling"],
  ["deploy/work-controller/goal-fleet-host-evidence-cli.mjs", "exceeds its reviewed reserve"],
  ["deploy/work-controller/goal-fleet-host-evidence-ledger.mjs", "genesis differs from its immutable controller binding"],
  ["deploy/work-controller/goal-fleet-supervised-cycle-cli.mjs", "refreshGoalFleetHostEvidence"],
  ["tests/work-goal-fleet.test.mjs", "across virtual days without configuration drift"],
  ["tests/static.sh", "tests/work-goal-fleet-host-evidence.test.mjs"],
  ["deploy/work-controller/goal-fleet-cleanup-cli.mjs", "cleanup-settled"],
  ["deploy/work-controller/goal-fleet-service-units.mjs", "installed legacy per-goal units block startup"],
  ["tests/static.sh", "tests/work-goal-fleet-service-units.test.mjs"],
  ["deploy/work-controller/goal-fleet-service-cli.mjs", "pixel-work-goal-fleet-service-render"],
  ["deploy/work-controller/goal-fleet-service-lifecycle.mjs", "legacy per-goal service unit must be removed"],
  ["deploy/work-controller/goal-fleet-service-lifecycle.mjs", "durable goal or fleet ledgers are unavailable"],
  ["tests/static.sh", "tests/work-goal-fleet-service-lifecycle.test.mjs"],
  ["THREAT-MODEL.md", "Several long goals oversubscribe one host"],
  ["tests/static.sh", "tests/work-goal-cli.test.mjs"],
  ["tests/static.sh", "tests/work-goal-runtime.test.mjs"],
  ["tests/static.sh", "tests/work-goal-builder-driver.test.mjs"],
  ["tests/static.sh", "tests/work-candidate-wait-loop.test.mjs"],
  ["deploy/work-controller/goal-candidate-adapter.mjs", "createGoalCandidateDriver"],
  ["deploy/work-controller/goal-profile-router.mjs", "Exact-profile supervised router only"],
  ["deploy/work-controller/candidate-wait-loop.mjs", "pixel-semantic-candidate-artifact-set-v1"],
  ["deploy/work-controller/semantic-acceptance.mjs", "acceptSemanticCandidate"],
  ["deploy/work-controller/goal-accept-cli.mjs", "--confirm-review-sha256"],
  ["deploy/work-controller/goal-accept-cli.mjs", "retained artifact set differs from the waiting checkpoint"],
  ["deploy/work-controller/goal-review-cli.mjs", "pixel-control-work-semantic-review"],
  ["deploy/work-controller/goal-review-cli.mjs", "semanticAccuracyVerified"],
  ["schemas/control-work-semantic-review-v1.schema.json", "none-read-only"],
  ["tests/static.sh", "schemas/work-semantic-acceptance-v1.schema.json"],
  ["tests/static.sh", "schemas/work-scout-report-proposal-v1.schema.json"],
  ["tests/static.sh", "schemas/work-scout-report-v1.schema.json"],
  ["tests/static.sh", "schemas/work-scout-verification-v1.schema.json"],
  ["tests/static.sh", "tests/work-goal-run-bundles.test.mjs"],
  ["tests/static.sh", "tests/work-model-qualification.test.mjs"],
  ["tests/static.sh", "tests/work-model-qualification-runner.test.mjs"],
  ["tests/static.sh", "tests/work-model-runtime-contract.test.mjs"],
  ["tests/static.sh", "tests/work-omp-runtime.test.mjs"],
  ["tests/static.sh", "tests/work-watchdog.test.mjs"],
  ["tests/static.sh", "tests/work-context-capsules.test.mjs"],
  ["tests/static.sh", "tests/work-capability-packs.test.mjs"],
  ["tests/static.sh", "tests/work-capability-pack-installation.test.mjs"],
  ["tests/static.sh", "tests/work-capability-image-admission.test.mjs"],
  ["tests/static.sh", "tests/work-capability-health.test.mjs"],
  ["tests/static.sh", "tests/work-capability-runtime.test.mjs"],
  ["tests/static.sh", "tests/work-capability-controller.test.mjs"],
  ["tests/static.sh", "tests/work-capability-tool.test.mjs"],
  ["tests/static.sh", "tests/work-capability-profile-service.test.mjs"],
  ["tests/static.sh", "tests/work-capability-image-live.mjs"],
  ["scripts/run-debian-release-gate.sh", '"${PIXEL_LIVE_DOCKER:-0}" == "1"'],
  ["scripts/run-debian-release-gate.sh", "tests/work-capability-image-live.mjs"],
  ["tests/work-capability-image-live.mjs", "PIXEL_LIVE_DOCKER"],
  ["tests/work-capability-image-live.mjs", 'network: "none"'],
  ["tests/work-capability-image-live.mjs", '"image", "import"'],
  ["tests/work-capability-image-live.mjs", '"--owner=1000"'],
  ["tests/work-capability-image-live.mjs", "const imageRef = imageId"],
  ["deploy/work-controller/capability-packs.mjs", "localImageIdentity"],
  ["schemas/work-capability-pack-v1.schema.json", '"pattern": "^sha256:[a-f0-9]{64}$"'],
  ["tests/work-capability-image-live.mjs", "image-admitted-disabled"],
  ["tests/static.sh", "tests/work-knowledge-vault.test.mjs"],
  ["tests/static.sh", "tests/work-operator-status.test.mjs"],
  ["tests/static.sh", "tests/work-goal-operator-status.test.mjs"],
  ["tests/static.sh", "tests/work-codex-compiler.test.mjs"],
  ["tests/static.sh", "tests/work-codex-pressure.test.mjs"],
  ["tests/static.sh", "tests/work-codex-authentication.test.mjs"],
  ["tests/static.sh", "tests/work-codex-credential-custody.test.mjs"],
  ["tests/static.sh", "tests/work-codex-container-protocol.test.mjs"],
  ["tests/static.sh", "tests/work-codex-container-entrypoint.test.mjs"],
  ["tests/static.sh", "tests/work-codex-container-images.test.mjs"],
  ["tests/static.sh", "tests/work-codex-egress-proxy.test.mjs"],
  ["tests/static.sh", "tests/work-codex-isolated-cli.test.mjs"],
  ["tests/static.sh", "tests/work-codex-execution.test.mjs"],
  ["tests/static.sh", "tests/work-codex-cli-qualification.test.mjs"],
  ["tests/static.sh", "schemas/work-model-capability-receipt-v1.schema.json"],
  ["tests/static.sh", "schemas/work-goal-v1.schema.json"],
  ["tests/static.sh", "schemas/work-goal-checkpoint-v1.schema.json"],
  ["tests/static.sh", "schemas/work-goal-run-bundle-v1.schema.json"],
  ["tests/static.sh", "schemas/work-lease-revocation-v1.schema.json"],
  ["tests/static.sh", "schemas/work-watchdog-decision-v1.schema.json"],
  ["tests/static.sh", "schemas/work-context-capsule-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-pack-v1.schema.json"],
  ["RELEASE-MANIFEST.json", "deepWorkCapability"],
  ["tests/static.sh", "schemas/work-capability-pack-v2.schema.json"],
  ["tests/static.sh", "schemas/work-capability-tool-catalog-v2.schema.json"],
  ["tests/static.sh", "schemas/work-capability-controller-policy-v2.schema.json"],
  ["tests/static.sh", "schemas/work-capability-job-authorization-v2.schema.json"],
  ["tests/static.sh", "schemas/work-capability-tool-request-v2.schema.json"],
  ["tests/static.sh", "schemas/work-capability-grant-v2.schema.json"],
  ["tests/static.sh", "schemas/work-capability-lease-v2.schema.json"],
  ["tests/static.sh", "schemas/work-capability-runtime-v2.schema.json"],
  ["tests/static.sh", "schemas/work-capability-consumption-v2.schema.json"],
  ["tests/static.sh", "tests/work-capability-v2-contract.test.mjs"],
  ["tests/static.sh", "tests/work-capability-v2-installation.test.mjs"],
  ["deploy/work-controller/capability-image-admission.mjs", "v2 capability runtime is not enabled"],
  ["deploy/work-controller/capability-pack-installation.mjs", "validateWorkCapabilityPackV2"],
  ["schemas/work-capability-pack-v2.schema.json", '"admissionOnly"'],
  ["schemas/work-capability-runtime-v2.schema.json", '"enabled"'],
  ["tests/static.sh", "schemas/work-capability-pack-installation-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-pack-removal-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-image-admission-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-image-revocation-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-health-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-runtime-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-pack-operation-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-grant-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-consumption-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-controller-policy-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-tool-catalog-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-job-authorization-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-tool-request-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-watchdog-event-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-tool-response-v1.schema.json"],
  ["tests/static.sh", "schemas/work-capability-tool-custody-v1.schema.json"],
  ["tests/static.sh", "schemas/work-knowledge-ingestion-v1.schema.json"],
  ["tests/static.sh", "schemas/work-knowledge-source-v1.schema.json"],
  ["tests/static.sh", "schemas/work-knowledge-query-v1.schema.json"],
  ["tests/static.sh", "schemas/work-knowledge-retrieval-v1.schema.json"],
  ["tests/static.sh", "schemas/work-knowledge-deletion-v1.schema.json"],
  ["tests/static.sh", "schemas/work-knowledge-key-rotation-v1.schema.json"],
  ["tests/static.sh", "schemas/work-knowledge-reconciliation-v1.schema.json"],
  ["tests/static.sh", "schemas/work-operator-status-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-policy-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-request-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-capsule-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-plan-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-output-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-authorization-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-authentication-evidence-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-authentication-consumption-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-credential-custody-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-egress-policy-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-execution-claim-v1.schema.json"],
  ["tests/static.sh", "schemas/work-codex-result-v1.schema.json"],
  ["deploy/work-codex-provider/compiler.mjs", "local work is already sufficient; remote work is unnecessary"],
  ["deploy/work-codex-provider/compiler.mjs", "private mapping commitment nonce is invalid"],
  ["deploy/work-codex-provider/compiler.mjs", "ephemeral-read-only-no-tools-no-network"],
  ["deploy/work-codex-provider/compiler.mjs", "providerInvoked: false"],
  ["deploy/work-codex-provider/execution.mjs", "exactly one explicitly enabled adapter boundary"],
  ["deploy/work-codex-provider/execution.mjs", "production Codex work authorization requires a verified external MFA assertion"],
  ["deploy/work-codex-provider/execution.mjs", "singleUseConsumed: true"],
  ["deploy/work-codex-provider/authentication.mjs", "verifyWorkCodexAuthenticationEvidence"],
  ["deploy/work-codex-provider/authentication.mjs", "Codex work authentication verifier accepts only a public key"],
  ["deploy/work-codex-provider/credential-custody.mjs", "PRIVATE_HANDLES"],
  ["deploy/work-codex-provider/credential-custody.mjs", "Codex API credential changed after custody inspection"],
  ["deploy/work-codex-provider/container-protocol.mjs", "PIXEL-CODEX-CONTAINER-V1"],
  ["deploy/work-codex-provider/container-entrypoint.mjs", "--with-api-key"],
  ["deploy/work-codex-provider/isolated-codex-cli.mjs", "codex-cli-isolated"],
  ["deploy/work-codex-provider/isolated-codex-cli.mjs", "Codex runner image differs from its manifest, local-image, binary, or entrypoint pins"],
  ["deploy/work-codex-provider/egress-proxy.mjs", "Codex proxy DNS resolved to an unsafe address set"],
  ["deploy/work-codex-provider/Dockerfile", "@openai/codex/-/codex-0.147.0-linux-x64.tgz"],
  ["deploy/agent-comparison/Dockerfile.codex-runner", "pixel-codex-comparison-runtime-v1"],
  ["scripts/qualify_codex_comparison_surface.py", "pixel-codex-comparison-surface-qualified"],
  ["scripts/codex_comparison_workspace.py", "codex-comparison-workspace-export"],
  ["deploy/work-codex-provider/Dockerfile", "cb0a15567e9a60a5820d54b0f6ae86d504dc3805c1eab21a47f70e3eb7b73a40"],
  ["deploy/work-codex-provider/Dockerfile.egress-proxy", "USER 65532:65532"],
  ["deploy/work-codex-provider/Dockerfile", "ARG SOURCE_DATE_EPOCH=0"],
  ["deploy/work-codex-provider/Dockerfile.egress-proxy", "ARG SOURCE_DATE_EPOCH=0"],
  ["deploy/work-codex-provider/Dockerfile", manifest.baseImage],
  ["deploy/work-codex-provider/Dockerfile", manifest.nodeRuntime.url],
  ["deploy/work-codex-provider/Dockerfile", manifest.nodeRuntime.sha256],
  ["deploy/work-codex-provider/Dockerfile.egress-proxy", manifest.baseImage],
  ["deploy/work-codex-provider/Dockerfile.egress-proxy", manifest.nodeRuntime.url],
  ["deploy/work-codex-provider/Dockerfile.egress-proxy", manifest.nodeRuntime.sha256],
  ["scripts/generate-release-files.mjs", "deploy/work-codex-provider/Dockerfile.egress-proxy"],
  ["scripts/qualify-work-codex-images.sh", "moby/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec"],
  ["scripts/qualify-work-codex-images.sh", "rewrite-timestamp=true"],
  ["scripts/qualify-work-codex-images.sh", "cmp --silent"],
  ["scripts/qualify-dsv4-image.sh", "moby/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec"],
  ["scripts/qualify-dsv4-image.sh", "rewrite-timestamp=true"],
  ["scripts/qualify-dsv4-image.sh", "cmp --silent"],
  ["scripts/qualify-dsv4-image.sh", "without starting a container"],
  ["scripts/qualify-work-runner-image.sh", "moby/buildkit@sha256:2f5adac4ecd194d9f8c10b7b5d7bceb5186853db1b26e5abd3a657af0b7e26ec"],
  ["scripts/qualify-work-runner-image.sh", "type=docker"],
  ["scripts/qualify-work-runner-image.sh", "docker load --input"],
  ["deploy/work-codex-provider/codex-cli-qualification.mjs", "codex-cli-qualified-fake"],
  ["deploy/work-codex-provider/codex-cli-qualification.mjs", "features.shell_tool=false"],
  ["deploy/work-codex-provider/codex-cli-qualification.mjs", "Codex CLI emitted an error, tool, effect, or unsupported event"],
  ["deploy/work-codex-provider/policy.example.json", "Separately governed sanitized Codex work only"],
  ["CODEX-WORK-PROVIDER.md", "signed-MFA and credential-custody foundations"],
  ["deploy/work-controller/model-qualification.mjs", "modelArtifactSha256"],
  ["deploy/work-controller/model-qualification.mjs", "acceleratorClass"],
  ["deploy/work-controller/model-qualification.mjs", "caseManifest"],
  ["deploy/work-controller/model-qualification.mjs", "expectedEvaluatorSha256"],
  ["deploy/work-controller/model-qualification.mjs", "intentMutationObserved"],
  ["deploy/work-controller/model-qualification-runner.mjs", "fixedModelQualificationCasesSha256"],
  ["deploy/work-controller/model-qualification-runner.mjs", "sustained-structured-output"],
  ["deploy/work-controller/model-qualification-runner.mjs", "assistant-tool-selection"],
  ["deploy/work-controller/model-qualification-runner.mjs", "assistant-argument-fidelity"],
  ["deploy/work-controller/model-qualification-runner.mjs", "pixel_calendar_propose_delete"],
  ["deploy/work-controller/model-qualification-runner.mjs", "targetBytes: 262144"],
  ["deploy/work-controller/model-qualification-runner.mjs", "qualification backend must be an explicit credential-free IPv4 loopback origin"],
  ["deploy/work-controller/model-qualification-runner.mjs", "qualification evaluator changed during the benchmark"],
  ["deploy/work-controller/model-qualification-runner.mjs", "qualificationSeed"],
  ["deploy/work-controller/model-qualification-runner.mjs", "parseInt(sha(entry).slice(0, 8), 16)"],
  ["deploy/work-controller/model-qualification-runner.mjs", "body.seed = qualificationSeed(entry)"],
  ["deploy/work-controller/model-qualification-bridge.mjs", "MODEL_QUALIFICATION_UPSTREAM"],
  ["deploy/work-controller/model-qualification-container.mjs", "MODEL_QUALIFICATION_CONFIG_PATH"],
  ["deploy/work-controller/model-qualification-docker.mjs", "--confirm-qualification-operation-sha256"],
  ["deploy/work-controller/model-qualification-docker.mjs", "ready-already-running"],
  ["deploy/work-controller/model-qualification-docker.mjs", "usesExternalNetwork: false"],
  ["deploy/work-controller/model-qualification-maintenance.mjs", "--confirm-maintenance-operation-sha256"],
  ["deploy/work-controller/model-qualification-maintenance.mjs", "manual-attention-production-held"],
  ["deploy/work-controller/model-qualification-maintenance.mjs", "qualificationIsolationAbsent"],
  ["deploy/work-controller/maintenance-custody.mjs", "/usr/bin/flock"],
  ["deploy/work-controller/maintenance-custody.mjs", "maintenance custody lock changed during acquisition"],
  ["deploy/work-controller/maintenance-recovery-journal.mjs", "guardian-production-restored"],
  ["deploy/work-controller/maintenance-recovery-journal.mjs", "recovery settlement receipt already exists with a different status or identity"],
  ["deploy/work-controller/maintenance-recovery-guardian.mjs", "recover|watch --config PRIVATE_JSON"],
  ["deploy/work-controller/maintenance-recovery-guardian.mjs", "manual-attention-recovery-substituted-resource"],
  ["deploy/work-controller/maintenance-recovery-guardian.mjs", "guardian-production-restored"],
  ["deploy/work-controller/maintenance-recovery-guardian.mjs", "removes only the exact-bound qualification runner/backend/network"],
  ["deploy/work-controller/maintenance-recovery-guardian-supervise.sh", "systemd-analyze verify"],
  ["deploy/work-controller/maintenance-recovery-guardian@.service", "Restart=always"],
  ["deploy/work-controller/maintenance-recovery-guardian-unit.mjs", "render --config PRIVATE_JSON --node NODE --guardian GUARDIAN"],
  ["tests/static.sh", "node --test tests/maintenance-recovery-guardian.test.mjs"],
  ["tests/static.sh", "node --test tests/maintenance-recovery-guardian-systemd.test.mjs"],
  ["deploy/work-controller/model-campaign-maintenance.mjs", "--confirm-campaign-maintenance-operation-sha256"],
  ["deploy/work-controller/model-campaign-maintenance.mjs", "manual-attention-production-held"],
  ["deploy/work-controller/model-campaign-maintenance.mjs", "exclusiveAcceleratorCustody"],
  ["deploy/work-controller/model-campaign-maintenance.mjs", "runsBoundedLocalPairs"],
  ["deploy/work-controller/model-policy-cli.mjs", "--confirm-proposed-policy-sha256"],
  ["deploy/work-controller/model-policy-cli.mjs", "prepared policy output must be a new private file"],
  ["deploy/work-controller/model-policy-cli.mjs", "enable-review"],
  ["deploy/work-controller/model-policy-cli.mjs", "changesTools: false"],
  ["deploy/work-controller/model-policy-cli.mjs", "separately installing the resulting policy"],
  ["deploy/work-controller/model-policy-cli.mjs", "Content-free local-model readiness only"],
  ["deploy/agent-comparison/pixel-system-cli.mjs", "policy-review"],
  ["deploy/agent-comparison/pixel-system-cli.mjs", "--confirm-operation-sha256"],
  ["deploy/agent-comparison/pixel-system-cli.mjs", "changesOnlyPolicyTemplatePath: true"],
  ["deploy/agent-comparison/pixel-system-cli.mjs", "destination-bound publication"],
  ["schemas/work-model-policy-review-v1.schema.json", "enablesDeepWork"],
  ["schemas/work-model-policy-enable-review-v1.schema.json", "changesTools"],
  ["schemas/work-model-policy-enable-review-v1.schema.json", "separately installing the resulting policy"],
  ["schemas/work-model-operator-status-v1.schema.json", "browserCanQualify"],
  ["schemas/work-model-backend-status-v1.schema.json", "acceleratorBound"],
  ["schemas/work-model-backend-status-v1.schema.json", "environmentBound"],
  ["schemas/work-model-artifact-manifest-v1.schema.json", "artifactSha256"],
  ["schemas/work-model-runtime-cache-manifest-v1.schema.json", "image-bound read-only launch contract"],
  ["schemas/work-model-artifact-review-v1.schema.json", "--confirm-artifact-measurement-sha256"],
  ["schemas/work-model-artifact-render-receipt-v1.schema.json", "private-inert-manifest-written"],
  ["schemas/work-model-backend-config-v1.schema.json", "gpuMemoryUtilizationPermille"],
  ["schemas/work-policy-v1.schema.json", "(?:sha256:[a-f0-9]{64}"],
  ["schemas/work-model-backend-config-v1.schema.json", "cacheMiB"],
  ["schemas/work-model-backend-config-v1.schema.json", "backendNetwork"],
  ["schemas/work-model-backend-config-v1.schema.json", "startupTimeoutSeconds"],
  ["schemas/work-model-backend-review-v1.schema.json", "--confirm-launch-bundle-sha256"],
  ["schemas/work-model-backend-launch-v1.schema.json", "separate exact-confirmed lifecycle"],
  ["schemas/work-model-backend-lifecycle-review-v1.schema.json", "--confirm-lifecycle-sha256"],
  ["schemas/work-model-backend-lifecycle-receipt-v1.schema.json", "ready-already-running"],
  ["schemas/work-model-backend-halt-review-v1.schema.json", "--confirm-halt-sha256"],
  ["schemas/work-model-backend-halt-receipt-v1.schema.json", "forensicResourcesRetained"],
  ["schemas/work-model-backend-live-qualification-v1.schema.json", "public-fixed-synthetic-model"],
  ["schemas/work-model-backend-live-qualification-v1.schema.json", "haltRetainedForensics"],
  ["deploy/work-controller/model-backend-cli.mjs", "validateModelBackendInspect"],
  ["deploy/work-controller/model-backend-cli.mjs", "Content-free read-only inspection"],
  ["deploy/work-controller/model-backend-cli.mjs", "confirmation differs from the exact private artifact measurement request"],
  ["deploy/work-controller/model-artifact.mjs", "model artifact file changed while it was measured"],
  ["deploy/work-controller/model-artifact.mjs", "single-link bounded regular file"],
  ["deploy/work-controller/model-runtime-cache-artifact.mjs", "executable model-runtime cache bytes"],
  ["deploy/work-controller/model-backend-launch.mjs", "\"--pull\", \"never\""],
  ["deploy/work-controller/model-backend-launch.mjs", "--no-enable-log-requests"],
  ["deploy/work-controller/model-backend-launch.mjs", "--no-enable-log-outputs"],
  ["deploy/work-controller/model-backend-launch.mjs", "--no-enable-log-deltas"],
  ["deploy/work-controller/model-backend-launch.mjs", "--disable-uvicorn-access-log"],
  ["deploy/work-controller/model-backend-launch.mjs", "FLASHINFER_WORKSPACE_BASE=/var/cache/pixel-model/flashinfer"],
  ["deploy/work-controller/model-backend-launch.mjs", "NUMBA_CACHE_DIR=/var/cache/pixel-model/numba"],
  ["deploy/work-controller/model-backend-launch.mjs", "vLLM tensor parallelism must equal"],
  ["deploy/work-controller/model-backend-launch.mjs", "VLLM_NO_USAGE_STATS=1"],
  ["deploy/work-controller/model-backend-runtime.mjs", "credential-bearing variable"],
  ["deploy/work-controller/model-backend-runtime.mjs", "resource ceilings differ from the exact contract"],
  ["deploy/work-controller/model-backend-runtime.mjs", "not effectively bound by Docker"],
  ["deploy/work-controller/model-backend-lifecycle.mjs", "readiness event was not observed before its safety deadline"],
  ["deploy/work-controller/model-backend-lifecycle.mjs", "resumedPartialState"],
  ["deploy/work-controller/model-backend-lifecycle.mjs", "image is unavailable; lifecycle never pulls images"],
  ["deploy/work-controller/model-backend-halt.mjs", "reads no model bytes"],
  ["deploy/work-runner/model-backend-coordination.mjs", "lock is held by another live operation"],
  ["deploy/work-runner/docker-supervisor.mjs", "attachModelProxyToBackend"],
  ["tests/work-model-backend-coordination.test.mjs", "serializes critical sections"],
  ["tests/static.sh", "node --test tests/work-model-backend-lifecycle.test.mjs"],
  ["tests/work-model-backend-lifecycle-live.mjs", "live qualification requires a clean exact source tree"],
  ["tests/work-model-backend-lifecycle-live.mjs", "responsesExposed: false"],
  ["scripts/lib/secure-files.mjs", "contains a duplicate object key"],
  ["deploy/work-runner/docker-boundary.mjs", "artifact-sha256"],
  ["deploy/work-runner/docker-boundary.mjs", "exactly one bounded GPU request"],
  ["deploy/work-runner/docker-boundary.mjs", "imageRef !== prepared.plan.isolation.runnerImageDigest"],
  ["deploy/work-runner/runner-core.mjs", "requireQualifiedModel"],
  ["deploy/work-runner/runner-core.mjs", "model qualification receipt is not owner-private, single-link, and real"],
  ["deploy/work-model-proxy/proxy.mjs", "backend-usage-missing"],
  ["deploy/work-model-proxy/proxy.mjs", "config.qualification.maxContextTokens"],
  ["deploy/work-model-proxy/inference-policy.mjs", "exact-request-boundary-v1"],
  ["deploy/work-model-proxy/inference-policy.mjs", "repetition_penalty"],
  ["deploy/work-runner/Dockerfile", "inference-policy.mjs"],
  ["deploy/agent-comparison/inference-boundary.mjs", "Exact inference boundary denied the request"],
  ["deploy/agent-comparison/research-mcp-server.mjs", "Pixel Public Research"],
  ["deploy/agent-comparison/codex-research-authority.mjs", "pixel-outcome-codex-research-authority-ready"],
  ["deploy/agent-comparison/pixel-system-cli.mjs", "verifierImageDigest: prepared.policy.runner.imageDigest"],
  ["scripts/portal_outcome_livesystem.py", "def container_user_args()"],
  ["scripts/portal_outcome_livesystem.py", "def private_tmpfs("],
  ["scripts/portal_outcome_livesystem.py", "def codex_container_resource_args("],
  ["scripts/portal_outcome_livesystem.py", "--memory-swap"],
  ["deploy/agent-comparison/README.md", "same admitted backend-neutral environment"],
  ["scripts/portal_outcome_livesystem.py", "CODEX_MODEL_CATALOG_CONTAINER_PATH = \"/input/model-catalog.json\""],
  ["scripts/portal_outcome_livesystem.py", "CODEX_PINNED_PROMPT_SHA256 = \"ac8ae107a0d72fe3476b430afb161ea4e67da2e446d778aefc44828160559807\""],
  ["scripts/ci-release-gate-inner.sh", "deploy/agent-comparison/codex-0.147.0-prompt.md"],
  ["deploy/work-runner/Dockerfile", "inference-boundary.mjs"],
  ["deploy/work-runner/Dockerfile", "research-mcp-server.mjs"],
  ["deploy/work-broker/policy.example.json", "maxRequestOutputTokens"],
  ["pixel", "work-model-qualify"],
  ["pixel", "work-model-policy"],
  ["pixel", "work-model-backend"],
  ["README.md", "work-model-backend review"],
  ["deploy/work-controller/watchdog.mjs", "expectedEventHeadSha256"],
  ["deploy/work-controller/watchdog.mjs", "effect-not-granted"],
  ["deploy/work-controller/goals.mjs", "goal dispatch is not the deterministic next milestone"],
  ["deploy/work-controller/goals.mjs", "replaysChild: false"],
  ["deploy/work-controller/goals.mjs", "goal child evidence differs from the immutable milestone job and plan"],
  ["deploy/work-controller/goal-cli.mjs", "Content-free local goal-control receipt only"],
  ["deploy/work-controller/goal-runtime.mjs", "after.headSha256 === before"],
  ["deploy/work-controller/goal-runtime.mjs", "replaysChild: false"],
  ["deploy/work-controller/goal-runtime.mjs", "maxControllerTransitions"],
  ["deploy/work-controller/goal-runtime.mjs", "child-authority-${authority}"],
  ["deploy/work-controller/goal-runtime.mjs", "justInTimeGoal"],
  ["deploy/work-controller/builder-loop.mjs", "exactRecoveredClaim"],
  ["deploy/work-controller/checkpoints.mjs", "executionBudgetReached"],
  ["deploy/work-controller/goal-builder-driver.mjs", "Internal exact-profile bridge only"],
  ["deploy/work-controller/goal-builder-driver.mjs", "containsWorkerOutput: false"],
  ["deploy/work-controller/goal-builder-preparer.mjs", "never accepted from a worker or caller"],
  ["deploy/work-controller/goal-builder-resolver.mjs", "allowNotYetValid: true"],
  ["deploy/work-controller/goal-builder-resolver.mjs", "renew one expired unclaimed pre-admission lease with byte-equivalent authority"],
  ["deploy/work-controller/goal-cycle-cli.mjs", "maxControllerTransitions: config.controller.maxTransitions"],
  ["deploy/work-controller/goal-cycle-cli.mjs", "Scheduling another cycle grants no execution"],
  ["deploy/work-controller/goal-cycle-cli.mjs", "research runtime presence differs from its exact jobs"],
  ["deploy/work-controller/goal-cleanup-cli.mjs", "one exact consumed running Scout, Builder, Researcher, or Data Lab attempt"],
  ["deploy/work-controller/goal-cleanup-cli.mjs", "cleanup-not-required"],
  ["deploy/work-controller/goal-cancel-cli.mjs", "An inactive goal may end directly; an unlaunched child requires an atomic lease revocation; a started child requires terminal cleanup evidence"],
  ["deploy/work-controller/goal-cancel-cli.mjs", "none-until-confirmed"],
  ["deploy/work-controller/goal-cancel-cli.mjs", "child-stop-cleanup-required"],
  ["deploy/work-controller/goal-cancel-cli.mjs", "inactive-goal"],
  ["deploy/work-controller/goal-cancel-cli.mjs", "unlaunched-child-revocation"],
  ["deploy/work-controller/goal-cancel-cli.mjs", "cancelled-before-launch"],
  ["deploy/work-controller/goal-cancel-cli.mjs", "goal cancellation child custody advanced after review"],
  ["deploy/work-controller/goal-pause-cli.mjs", "currentBoundedStepMayFinish"],
  ["deploy/work-controller/goal-pause-cli.mjs", "future-controller-cycles-noop"],
  ["deploy/work-controller/goal-resume-cli.mjs", "none-until-confirmed"],
  ["deploy/work-controller/goal-resume-cli.mjs", "future-controller-cycles-enabled"],
  ["deploy/work-controller/goal-resume-cli.mjs", "pausedCheckpointSha256"],
  ["deploy/work-controller/goals.mjs", "cancel-after-supervised-cleanup"],
  ["deploy/work-controller/goals.mjs", "cancelGoalAfterLeaseRevocation"],
  ["deploy/work-controller/goals.mjs", "goal revocation cancellation lacks an exact unlaunched cancelled child"],
  ["deploy/work-controller/goals.mjs", "goal revocation cancellation evidence is not the exact durable lease disposition"],
  ["deploy/work-controller/goal-service-cli.mjs", "Rendering grants no work execution, installation, service enablement"],
  ["deploy/work-controller/goal-service-units.mjs", "Docker socket access remains a high-trust supervisor capability"],
  ["deploy/work-controller/goal-service-units.mjs", "timer is only a stalled/restart watchdog"],
  ["deploy/work-controller/goal-service-units.mjs", "PathChanged=${watchPath}"],
  ["deploy/work-controller/goal-service-units.mjs", "ExecStopPost=${flock} --exclusive --wait 30"],
  ["deploy/work-controller/goal-continuous-cli.mjs", "Immediate continuation requires a newly durable checkpoint"],
  ["deploy/work-controller/goal-continuous-cli.mjs", "no-durable-progress"],
  ["deploy/work-controller/goal-service-lifecycle.mjs", "leave a check/use race"],
  ["deploy/work-controller/goal-service-lifecycle.mjs", "leave event and watchdog units disabled"],
  ["deploy/work-controller/goal-service-lifecycle.mjs", "live private contracts differ from the exact rendered manifest"],
  ["deploy/work-controller/goal-service-lifecycle.mjs", '["start", "--no-block", bundle.manifest.serviceName]'],
  ["deploy/work-controller/goal-service-lifecycle-cli.mjs", "must be owned by the configured unprivileged service identity"],
  ["deploy/work-runner/runner-core.mjs", "prepareResearcherCleanupRecovery"],
  ["deploy/work-runner/runner-core.mjs", "prepareDataLabCleanupRecovery"],
  ["deploy/work-runner/runner-core.mjs", "lease was already consumed or revoked"],
  ["deploy/work-runner/runner-core.mjs", "createLeaseRevocation"],
  ["deploy/work-runner/runner-core.mjs", "inspectLeaseDisposition"],
  ["deploy/work-runner/docker-supervisor.mjs", "cleanupInterruptedProfileAttempt"],
  ["deploy/work-runner/docker-researcher.mjs", "Researcher cleanup boundary is invalid"],
  ["deploy/work-runner/docker-data-lab.mjs", "Data Lab cleanup boundary is invalid"],
  ["pixel", "work-cycle) exec node"],
  ["pixel", "work-continuous) exec node"],
  ["pixel", "work-pause) exec node"],
  ["pixel", "work-resume) exec node"],
  ["pixel", "work-accept) exec node"],
  ["pixel", "work-cancel) exec node"],
  ["pixel", "work-service) exec node"],
  ["pixel", 'work-fleet) exec node "$ROOT/deploy/work-controller/goal-fleet-cycle-cli.mjs"'],
  ["pixel", 'work-fleet-cleanup) exec node "$ROOT/deploy/work-controller/goal-fleet-cleanup-cli.mjs"'],
  ["pixel", 'work-fleet-host-evidence) exec node "$ROOT/deploy/work-controller/goal-fleet-host-evidence-cli.mjs"'],
  ["pixel", 'work-fleet-service) exec node "$ROOT/deploy/work-controller/goal-fleet-service-cli.mjs"'],
  ["deploy/work-controller/goal-fleet-service-cli.mjs", "runGoalFleetServiceLifecycleCommand"],
  ["tests/static.sh", "tests/work-goal-service-units.test.mjs"],
  ["tests/static.sh", "tests/work-goal-service-lifecycle.test.mjs"],
  ["tests/static.sh", "tests/work-goal-continuous.test.mjs"],
  ["scripts/ci-release-gate-inner.sh", "deploy/work-controller/goal-continuous-cli.mjs"],
  ["tests/static.sh", "tests/work-goal-pause-cli.test.mjs"],
  ["tests/static.sh", "tests/work-goal-resume-cli.test.mjs"],
  ["tests/static.sh", "tests/work-goal-cancel-cli.test.mjs"],
  ["tests/static.sh", "tests/work-goal-terminal-journey-e2e.test.mjs"],
  ["deploy/work-controller/goals.mjs", "goal pause changed child, evidence, progress, usage, or safety facts"],
  ["deploy/work-controller/goals.mjs", "paused goal child count is invalid"],
  ["deploy/work-controller/goals.mjs", "active goal cancellation requires supervised child cleanup"],
  ["deploy/work-controller/goal-cli.mjs", "--confirm-goal-sha256"],
  ["deploy/work-controller/goal-run-bundles.mjs", "containsExactLease: true"],
  ["deploy/work-controller/goal-run-bundles.mjs", "superseded goal run lease was already consumed"],
  ["deploy/work-controller/goal-run-bundles.mjs", "goal run admission lease was already consumed or has an invalid revocation"],
  ["deploy/work-controller/goal-run-bundles.mjs", "superseded goal run lease was consumed or has an invalid revocation"],
  ["deploy/work-controller/goal-run-bundles.mjs", "goal run bundle cannot refresh after durable child admission"],
  ["deploy/work-controller/goal-run-bundles.mjs", "goal run bundle refresh changed immutable plan or lease authority"],
  ["deploy/work-controller/goal-run-bundles.mjs", "refreshExpiredGoalRunBundle"],
  ["deploy/work-broker/broker.mjs", "compileDurableGoalRefresh"],
  ["deploy/work-controller/goal-run-bundles.mjs", "goal run child admission has no durable custody marker"],
  ["deploy/work-controller/goal-run-bundles.mjs", "continuation differs from prior claim or cumulative usage"],
  ["THREAT-MODEL.md", "lease consumption and revocation compete for the same immutable per-lease slot"],
  ["deploy/work-controller/operator-status.mjs", "settled-plus-active-observed"],
  ["deploy/work-controller/operator-status.mjs", "operator goal budget use differs from its settled and current session evidence"],
  ["control/server.py", "Deep Work goal budget use differs from settled and current session evidence"],
  ["control/ui/index.html", "Remaining goal capacity"],
  ["THREAT-MODEL.md", "understates budget use"],
  ["pixel", 'work-goal) exec node "$ROOT/deploy/work-controller/goal-cli.mjs"'],
  ["deploy/work-controller/context-capsules.mjs", "grantsLeaseReuse"],
  ["deploy/work-controller/context-capsules.mjs", "parentCapsuleSha256"],
  ["deploy/work-controller/capability-packs.mjs", "capability execution was already started"],
  ["deploy/work-controller/capability-pack-installation.mjs", "installed capability version contains unknown or missing files"],
  ["deploy/work-controller/capability-pack-installation.mjs", "grantsImagePull: false"],
  ["deploy/work-controller/capability-pack-installation.mjs", "allowed signers changed during capability installation"],
  ["deploy/work-controller/capability-pack-installation.mjs", "capability removal confirmation differs from the current review"],
  ["deploy/work-controller/capability-pack-installation.mjs", "capability removal payload differs from its intent"],
  ["deploy/work-controller/capability-pack-installation.mjs", "capability removal custody receipt is invalid"],
  ["deploy/work-controller/capability-pack-operation.mjs", "Private single-writer custody for one exact installed capability-pack operation"],
  ["deploy/work-controller/capability-image-admission.mjs", "const DOCKER_PATH = \"/usr/bin/docker\""],
  ["deploy/work-controller/capability-image-admission.mjs", "capability executable archive contains extra data or entries"],
  ["deploy/work-controller/capability-image-admission.mjs", "capability image inspection container absence was not proven"],
  ["deploy/work-controller/capability-pack-cli.mjs", "image-cleanup-recover"],
  ["deploy/work-controller/capability-pack-installation.mjs", "publish a new signed version instead of reinstalling it"],
  ["pixel", 'work-capability-pack) exec node "$ROOT/deploy/work-controller/capability-pack-cli.mjs"'],
  ["deploy/work-controller/capability-packs.mjs", "buildCapabilityAdapterCleanupDockerCommands"],
  ["deploy/work-controller/capability-packs.mjs", "--pull"],
  ["deploy/work-controller/capability-packs.mjs", "nosuid,nodev,noexec"],
  ["deploy/work-controller/mcp-stdio-client.mjs", "2026-07-28"],
  ["deploy/work-controller/mcp-stdio-client.mjs", "server/discover"],
  ["deploy/work-controller/mcp-stdio-client.mjs", "notifications/cancelled"],
  ["deploy/work-controller/mcp-stdio-client.mjs", "dataClassification"],
  ["deploy/work-controller/capability-runtime.mjs", "loadPassingCapabilityHealth"],
  ["deploy/work-controller/capability-runtime.mjs", "refuses to remove a container outside exact claim custody"],
  ["deploy/work-controller/capability-runtime.mjs", "grantsReplay: false"],
  ["deploy/work-controller/capability-controller.mjs", "checkpoint.state !== \"running\""],
  ["deploy/work-controller/capability-controller.mjs", "expectedEventHeadSha256"],
  ["deploy/work-controller/capability-controller.mjs", "preflightDecision.decision !== \"continue\""],
  ["deploy/work-controller/capability-tool-queue.mjs", "runtime-result-staged"],
  ["deploy/work-controller/capability-tool-queue.mjs", "uncertain-no-replay"],
  ["deploy/work-controller/capability-tool-queue.mjs", "capability queue authorization is terminal"],
  ["scripts/lib/work-contract.mjs", 'loadSchema("work-capability-tool-catalog-v1.schema.json")'],
  ["deploy/work-controller/capability-tool-catalog.mjs", "exposed tool names collide"],
  ["deploy/work-runner/capability-tool.mjs", "Type.Unsafe"],
  ["deploy/work-runner/capability-tool.mjs", "content-bound name"],
  ["deploy/work-runner/capability-tool.mjs", "requestPublished"],
  ["deploy/work-controller/capability-profile-service.mjs", "cleanup would discard active or unsettled work"],
  ["deploy/work-controller/capability-tool-queue.mjs", "already-queued-disabled"],
  ["deploy/work-controller/goal-capability-runtime.mjs", "capability-job-authorizations"],
  ["deploy/work-controller/goal-capability-runtime.mjs", "verifyGoalCapabilityRuntimeReadiness"],
  ["deploy/work-controller/goal-capability-runtime.mjs", "if (mode === \"cleanup\")"],
  ["deploy/work-controller/goal-capability-runtime.mjs", "custodied capability policy differs from the immutable controller copy"],
  ["deploy/work-controller/goal-controller-prepare-cli.mjs", "capabilityBindingsSha256"],
  ["schemas/work-goal-controller-environment-v1.schema.json", "capabilityRuntime"],
  ["deploy/work-controller/goal-controller-capability-environment.example.json", "maxHealthAgeMs"],
  ["deploy/work-controller/goal-controller-knowledge-environment.example.json", "pixel-knowledge-vault-key"],
  ["deploy/work-runner/profile-capability.mjs", "/run/pixel/capability"],
  ["deploy/work-runner/docker-supervisor.mjs", "capability custody exists without its exact recovery authorization"],
  ["deploy/work-runner/docker-supervisor.mjs", "capabilityService: result.capabilityService"],
  ["deploy/work-runner/Dockerfile", "capability-tool.mjs"],
  ["deploy/work-runner/Dockerfile", "/run/pixel/capability/requests"],
  ["tests/work-capability-image-live.mjs", "executeCapabilityTool"],
  ["deploy/work-controller/knowledge-vault.mjs", "aes-256-gcm"],
  ["deploy/work-controller/knowledge-vault.mjs", "lexical-hmac-v1"],
  ["deploy/work-controller/knowledge-vault.mjs", "knowledge query was already consumed"],
  ["deploy/work-controller/knowledge-vault.mjs", "recoverKnowledgeVaultLifecycle"],
  ["deploy/work-controller/knowledge-vault.mjs", "purgeExpiredKnowledgeSources"],
  ["deploy/work-controller/knowledge-vault.mjs", "loadKnowledgeVaultKeyCredential"],
  ["deploy/work-controller/knowledge-vault.mjs", "rotateKnowledgeVaultKey"],
  ["deploy/work-controller/knowledge-vault.mjs", "reconcileKnowledgeVaultDeletionLedger"],
  ["deploy/work-controller/knowledge-vault.mjs", "recoverKnowledgeVaultTransaction"],
  ["deploy/work-controller/knowledge-vault-cli.mjs", "--confirm-review-sha256"],
  ["deploy/work-controller/knowledge-vault-cli.mjs", "pixel-work-knowledge-setup-review"],
  ["deploy/work-controller/knowledge-vault-cli.mjs", "knowledge setup stage contains an unexpected entry"],
  ["tests/work-knowledge-vault-cli.test.mjs", "reviewed setup creates an external private key"],
  ["deploy/work-controller/knowledge-vault-guide.mjs", "Review this local-only operation"],
  ["deploy/work-controller/knowledge-vault-guide.mjs", "APPLY ${code.slice(-12)}"],
  ["tests/work-knowledge-vault-guide.test.mjs", "plain-language add and remove"],
  ["deploy/work-controller/knowledge-vault-cli.mjs", "--controller-offline confirmed"],
  ["deploy/work-controller/knowledge-vault-cli.mjs", "controller to be offline"],
  ["deploy/work-controller/knowledge-vault-cli.mjs", "pixel-work-knowledge-query-apply"],
  ["deploy/work-controller/knowledge-vault-restore-cli.mjs", "propagates an active vault's authoritative tombstones before activation"],
  ["scripts/backup-private-state.sh", "PIXEL_DEEP_WORK_BACKUP_ENABLED"],
  ["scripts/restore-private-state.sh", "knowledgeDeletionReconciled"],
  ["scripts/supported-host-systemd-probe.py", 'checks["knowledge-vault-backup-recovery"] = "pass"'],
  ["scripts/supported-host-systemd-probe.py", "knowledge-vault-systemd-credential"],
  ["QUALIFICATION-MATRIX.json", "knowledge-vault-backup-recovery"],
  ["deploy/work-controller/goal-knowledge-runtime.mjs", "One exact checkpoint-bound local-vault retrieval"],
  ["deploy/work-controller/goal-knowledge-runtime.mjs", "maximumClassification"],
  ["deploy/work-controller/goal-service-units.mjs", "LoadCredential=pixel-knowledge-vault-key"],
  ["deploy/work-runner/docker-supervisor.mjs", "bindKnowledgeToWorkerPrompt"],
  ["schemas/work-job-v1.schema.json", "attempt-only"],
  ["tests/static.sh", "tests/work-goal-knowledge-runtime.test.mjs"],
  ["tests/static.sh", "tests/work-knowledge-vault-restore.test.mjs"],
  ["pixel", 'work-knowledge) exec node "$ROOT/deploy/work-controller/knowledge-vault-cli.mjs"'],
  ["pixel", 'work-knowledge-guide) exec node "$ROOT/deploy/work-controller/knowledge-vault-guide.mjs"'],
  ["pixel", 'work-knowledge-restore) exec node "$ROOT/deploy/work-controller/knowledge-vault-restore-cli.mjs"'],
  ["deploy/work-controller/operator-status.mjs", "buildWorkOperatorStatus"],
  ["deploy/work-controller/operator-status.mjs", "Content-free local Deep Work orientation only"],
  ["deploy/work-controller/operator-status.mjs", "completionRequiresIndependentVerification"],
  ["deploy/work-controller/operator-status.mjs", "progressModel"],
  ["deploy/work-controller/operator-status.mjs", "capability summary"],
  ["deploy/work-controller/goal-operator-status.mjs", "Authoritative content-free heartbeat"],
  ["deploy/work-controller/goal-operator-status.mjs", "checkpointSequence"],
  ["deploy/work-controller/goal-cycle-cli.mjs", "publishGoalOperatorStatus"],
  ["deploy/work-controller/goal-service-lifecycle.mjs", "capability runtime is not release-ready"],
  ["deploy/work-controller/goal-service-lifecycle-cli.mjs", "durable-event-driven"],
  ["control/server.py", 'self.path == "/api/v1/deep-work"'],
  ["control/server.py", 'self.path == "/api/v1/chat/tasks"'],
  ["control/server.py", "CHAT_ACTIVITY_CODES"],
  ["control/ui/app.js", 'api("/api/v1/chat/tasks"'],
  ["control/ui/app.js", "Progress is durable; this page can reconnect"],
  ["control/ui/app.js", "function chatDeepWorkCard()"],
  ["control/ui/app.js", "Load exact evidence and artifacts"],
  ["control/ui/app.js", "unless an explicit handoff receipt is shown"],
  ["schemas/control-chat-v1.schema.json", "chattask-"],
  ["schemas/control-chat-handoff-receipt-v1.schema.json", "settled Pixel chat turn"],
  ["control/server.py", "validate_chat_handoff_receipt"],
  ["control/ui/app.js", "Plan as Deep Work"],
  ["control/ui/app.js", "Deep Work handoff sealed"],
  ["control/server.py", "work_status_path=args.work_status"],
  ["control/server.py", '"deep-work-pause": {'],
  ["control/server.py", '"deep-work-resume": {'],
  ["control/server.py", '"deep-work-cancel": {'],
  ["control/server.py", "work_controller_config_path=args.work_controller_config"],
  ["control/server.py", 'input_snapshot = self.state / f"input-{action_id}.json"'],
  ["control/server.py", 'self.path == "/api/v1/deep-work/authoring"'],
  ["control/server.py", 'self.path == "/api/v1/deep-work/drafts"'],
  ["control/server.py", "validate_work_draft_for_control"],
  ["control/server.py", '"deep-work-prepare": {'],
  ["control/server.py", '"deep-work-stage": {'],
  ["control/server.py", '"deep-work-service-render": {'],
  ["control/server.py", "work_launch_config_path=args.work_launch_config"],
  ["control/server.py", "work_service_config_path=args.work_service_config"],
  ["control/server.py", '"--confirm-config-sha256", selected_package["controllerBundle"]["configSha256"]'],
  ["deploy/work-controller/goal-service-cli.mjs", "goal service render configuration differs from its exact confirmation"],
  ["control/server.py", 'self.path == "/api/v1/reviews/deep-work"'],
  ["control/server.py", '"deepWorkSemanticReviews"'],
  ["control/server.py", '"deep-work-draft": {'],
  ["control/server.py", "work_authoring_config_path=args.work_authoring_config"],
  ["control/server.py", 'policy_snapshot = self.state / f"policy-{action_id}.json"'],
  ["control/server.py", 'catalog_snapshot = self.state / f"catalog-{action_id}.json"'],
  ["control/server.py", '"goal-draft-cli.mjs"'],
  ["control/ui/app.js", "function renderDeepWork(value)"],
  ["control/ui/app.js", "function deepWorkDraftRequest()"],
  ["control/ui/app.js", "function renderWorkDraftReviews(value)"],
  ["schemas/control-work-draft-reviews-v1.schema.json", "inputIdentitiesExposed"],
  ["schemas/control-work-launch-config-v1.schema.json", "maxLaunches"],
  ["control/work-launch.example.json", "launchDirectory"],
  ["schemas/control-work-service-config-v1.schema.json", "maxBundles"],
  ["control/work-service.example.json", "serviceDirectory"],
  ["control/ui/app.js", "Each durable checkpoint triggers the next bounded decision"],
  ["control/ui/app.js", "function deepWorkCapabilityLabel(value)"],
  ["control/ui/app.js", "function renderPrivateKnowledge(value)"],
  ["control/ui/app.js", 'api("/api/v1/deep-work")'],
  ["control/ui/app.js", 'api("/api/v1/reviews/deep-work"'],
  ["control/ui/index.html", 'id="load-deep-work-review"'],
  ["control/ui/index.html", "Content-free with checkpoint-bound lifecycle controls"],
  ["control/ui/index.html", 'data-action="deep-work-pause"'],
  ["control/ui/index.html", 'data-action="deep-work-resume"'],
  ["control/ui/index.html", 'data-action="deep-work-cancel"'],
  ["control/ui/index.html", "Review inert draft creation"],
  ["control/ui/index.html", 'id="knowledge-title">Private knowledge'],
  ["control/ui/index.html", "This page never receives source text, titles, file paths, vault keys, or deletion authority"],
  ["THREAT-MODEL.md", "single-use owner-private byte snapshot"],
  ["THREAT-MODEL.md", "single-use private brief, policy, and catalog byte snapshots"],
  ["tests/control-deep-work-pause-e2e.test.mjs", "local control HTTP pause, resume, and safe cancel bind exact durable goal state"],
  ["tests/control-deep-work-draft-e2e.test.mjs", "loopback authoring reviews, prepares, stages, and renders inactive supervision without exposing private material"],
  ["tests/static.sh", "tests/control-deep-work-draft-e2e.test.mjs"],
  ["tests/work-goal-terminal-journey-e2e.test.mjs", "real Pixel terminal commands survive process restarts"],
  ["KNOWLEDGE-VAULT.md", "No match means no context"],
  ["tests/static.sh", "tests/work-verifier-command.test.mjs"],
  ["tests/static.sh", "tests/work-docker-verifier.test.mjs"],
  ["tests/static.sh", "tests/work-docker-builder.test.mjs"],
  ["tests/static.sh", "tests/work-docker-researcher.test.mjs"],
  ["tests/static.sh", "tests/work-builder-live.mjs"],
  ["tests/static.sh", "tests/work-researcher-live.mjs"],
  ["tests/static.sh", "tests/fixtures/work/fake-llama-researcher.mjs"],
  ["tests/static.sh", "tests/work-builder-loop-live.mjs"],
  ["tests/static.sh", "tests/work-docker-scout.test.mjs"],
  ["tests/static.sh", "tests/work-docker-boundary.test.mjs"],
  ["tests/static.sh", "tests/work-model-proxy.test.mjs"],
  [".github/workflows/security.yml", "Debian 12 / full release gate"],
  ["scripts/run-debian-release-gate.sh", "RELEASE-MANIFEST.json"],
  ["scripts/ci-release-gate-inner.sh", "Supported-host release gate passed."],
  ["scripts/bootstrap.sh", 'source "$ROOT/scripts/generated/release.env"'],
  ["scripts/generated/release.env", manifest.nodeRuntime.url],
  ["scripts/bootstrap.sh", "openclaw_version=$PIXEL_GENERATED_OPENCLAW_VERSION"],
  ["scripts/bootstrap.sh", "openclaw_package_integrity=$PIXEL_GENERATED_OPENCLAW_PACKAGE_INTEGRITY"],
  ["scripts/lib/bootstrap-download.sh", "download_verified"],
  ["scripts/bootstrap.sh", 'candidate_sandbox_ref=$(pixel_sandbox_candidate_tag "$pixel_version" "$sandbox_uid")'],
  ["scripts/preflight.sh", 'sandbox_reference=$PIXEL_SANDBOX_IMAGE'],
  ["scripts/preflight.sh", 'sandbox_reference=$(pixel_sandbox_candidate_tag "$source_version" "$expected_sandbox_uid")'],
  ["scripts/preflight.sh", "pixel_plugin_release_matches"],
  ["scripts/configure.mjs", `PIXEL_RELEASE_VERSION: version`],
  ["scripts/configure.mjs", "PIXEL_PRIVATE_ONBOARDING_PATH: privateOnboardingPath"],
  ["scripts/configure.mjs", "PIXEL_OPENCLAW_VERSION: release.openclaw"],
  ["deploy/sandbox/Dockerfile", manifest.baseImage],
  ["deploy/work-runner/Dockerfile", manifest.baseImage],
  ["deploy/work-runner/Dockerfile", manifest.nodeRuntime.url],
  ["deploy/work-runner/Dockerfile", manifest.nodeRuntime.sha256],
  ["deploy/work-runner/Dockerfile", "install -d -m 0755 /opt/node /opt/pixel/deploy/agent-comparison /opt/pixel/deploy/work-controller /opt/pixel/deploy/work-model-proxy /opt/pixel/deploy/work-runner /opt/pixel/scripts/lib /opt/pixel/schemas"],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0444 deploy/work-runner/builder-volume.mjs /opt/pixel/deploy/work-runner/builder-volume.mjs"],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0444 deploy/work-runner/verifier-command.mjs /opt/pixel/deploy/work-runner/verifier-command.mjs"],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0444 deploy/work-runner/research-tool.mjs /opt/pixel/deploy/work-runner/research-tool.mjs"],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0444 deploy/work-runner/data-runtime.json /opt/pixel/deploy/work-runner/data-runtime.json"],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0444 deploy/work-runner/builder-runtime.json /opt/pixel/deploy/work-runner/builder-runtime.json"],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0444 deploy/work-runner/omp-runtime.json /opt/pixel/deploy/work-runner/omp-runtime.json"],
  ["deploy/work-runner/Dockerfile", `COPY --chmod=0444 deploy/work-runner/builder-agent.md ${builderRuntime.agentSurface.definitionPath}`],
  ["deploy/work-runner/Dockerfile", `COPY --chmod=0555 deploy/work-runner/builder-entrypoint.sh ${builderRuntime.agentSurface.entrypointPath}`],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0444 deploy/work-runner/data-artifacts.mjs /opt/pixel/deploy/work-runner/data-artifacts.mjs"],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0555 deploy/work-runner/private-entrypoint.sh /opt/pixel/deploy/work-runner/private-entrypoint.sh"],
  ["deploy/work-runner/Dockerfile", "COPY --chmod=0444 schemas/*.json /opt/pixel/schemas/"],
  ["deploy/work-runner/Dockerfile", workPolicy.executor.url],
  ["deploy/work-runner/Dockerfile", workPolicy.executor.sha256],
  ["deploy/work-runner/Dockerfile", "RUN --network=none"],
  ...dataRuntime.artifacts.flatMap((artifact) => [
    ["deploy/work-runner/Dockerfile", artifact.url],
    ["deploy/work-runner/Dockerfile", artifact.sha256],
  ]),
  ...dataRuntime.systemPackages.map((entry) => ["deploy/work-runner/Dockerfile", `${entry.name}=${entry.version}`]),
  ...Object.entries(dataRuntime.engines).map(([name, engine]) => ["deploy/work-runner/Dockerfile", `org.osmantic.pixel.${name}-version=\"${engine.version}\"`]),
  ...builderRuntime.systemPackages.map((entry) => ["deploy/work-runner/Dockerfile", `${entry.name}=${entry.version}`]),
  ...builderRuntime.artifacts.flatMap((artifact) => [["deploy/work-runner/Dockerfile", artifact.url], ["deploy/work-runner/Dockerfile", artifact.sha256]]),
  ["deploy/work-runner/Dockerfile", `org.osmantic.pixel.builder-runtime=\"${builderRuntime.contract}\"`],
  ["deploy/work-runner/Dockerfile", `org.osmantic.pixel.builder-minimum-memory-mib=\"${builderRuntime.minimumMemoryMiB}\"`],
  ["deploy/work-runner/Dockerfile", `org.osmantic.pixel.omp-runtime=\"${ompRuntime.contract}\"`],
  ["deploy/work-runner/Dockerfile", `org.osmantic.pixel.omp-minimum-worker-memory-mib=\"${ompRuntime.minimumWorkerMemoryMiB}\"`],
  ["deploy/work-runner/Dockerfile", `org.osmantic.pixel.gdb-version=\"${builderRuntime.features.gdb.runtimeVersion}\"`],
  ["deploy/work-runner/Dockerfile", `org.osmantic.pixel.pyright-version=\"${builderRuntime.features.pyright.runtimeVersion}\"`],
  ["deploy/work-runner/Dockerfile", `org.osmantic.pixel.debugpy-version=\"${builderRuntime.features.debugpy.runtimeVersion}\"`],
  ...workNativeAddons.addons.flatMap((addon) => [
    ["deploy/work-runner/Dockerfile", addon.imagePath],
    ["deploy/work-runner/Dockerfile", addon.sha256],
    ["deploy/work-runner/Dockerfile", String(addon.bytes)],
  ]),
  ["deploy/work-broker/policy.example.json", manifest.referenceImages.llamaCpp],
  ["deploy/compose.yaml", "model-backend:"],
  ["deploy/compose.yaml", "internal: true"],
  ["deploy/sandbox/Dockerfile", "python3-pil"],
  ["deploy/sandbox/Dockerfile", "rsync"],
  ["deploy/compose.yaml", manifest.referenceImages.searxng],
  ["deploy/compose.yaml", 'driver: "none"'],
  ["deploy/searxng/settings.yml.template", 'method: "POST"'],
  ["deploy/searxng/settings.yml.template", "safe_search: 2"],
  ["deploy/searxng/settings.yml.template", "enable_metrics: false"],
  ["deploy/compose.yaml", manifest.referenceImages.llamaCpp],
  ["scripts/configure.mjs", "release.referenceImages.searxng"],
  ["scripts/configure.mjs", "release.referenceImages.llamaCpp"],
  ["scripts/bootstrap.sh", "org.osmantic.pixel.sandbox-version=$pixel_version"],
  ["scripts/bootstrap.sh", "org.osmantic.pixel.sandbox-uid=$sandbox_uid"],
  ["pixel", 'upstream) exec node "$ROOT/scripts/upstream.mjs"'],
  ["pixel", 'limb-kit) exec python3 "$ROOT/scripts/limb-kit.py"'],
  ["pixel", 'release-sign) exec python3 "$ROOT/scripts/release-update.py" sign'],
  ["pixel", 'update-inspect) exec python3 "$ROOT/scripts/release-update.py" inspect'],
  ["pixel", 'update-prepare) exec bash "$ROOT/scripts/prepare-release-update.sh"'],
  ["scripts/prepare-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["pixel", 'update-rehearse) exec bash "$ROOT/scripts/rehearse-release-update.sh"'],
  ["scripts/rehearse-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["pixel", 'update-activate) exec bash "$ROOT/scripts/activate-release-update.sh"'],
  ["scripts/activate-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/activate-release-update.sh", "activation-claim"],
  ["scripts/activate-release-update.sh", "activation-result"],
  ["pixel", 'update-reactivate) exec bash "$ROOT/scripts/reactivate-release-update.sh"'],
  ["scripts/reactivate-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/reactivate-release-update.sh", "reactivation-claim"],
  ["scripts/reactivate-release-update.sh", "reactivation-result"],
  ["pixel", 'update-reactivation-rollback) exec bash "$ROOT/scripts/rollback-reactivated-release-update.sh"'],
  ["scripts/rollback-reactivated-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/rollback-reactivated-release-update.sh", "reactivation-rollback-claim"],
  ["scripts/rollback-reactivated-release-update.sh", "reactivation-rollback-result"],
  ["pixel", 'update-reactivation-recover) exec bash "$ROOT/scripts/recover-reactivated-release-update.sh"'],
  ["scripts/recover-reactivated-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/recover-reactivated-release-update.sh", "reactivation-recovery-finalize"],
  ["pixel", 'update-rollback) exec bash "$ROOT/scripts/rollback-release-update.sh"'],
  ["scripts/rollback-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/rollback-release-update.sh", "rollback-claim"],
  ["scripts/rollback-release-update.sh", "rollback-result"],
  ["pixel", 'update-recover) exec bash "$ROOT/scripts/recover-release-update.sh"'],
  ["scripts/recover-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/recover-release-update.sh", "recovery-preview"],
  ["scripts/recover-release-update.sh", "recovery-finalize"],
  ["pixel", 'update-cleanup) exec bash "$ROOT/scripts/cleanup-release-update.sh"'],
  ["pixel", 'doctor) exec python3 "$ROOT/scripts/pixel-doctor.py"'],
  ["scripts/cleanup-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/cleanup-release-update.sh", "cleanup-preview"],
  ["scripts/cleanup-release-update.sh", "cleanup-hash"],
  ["pixel", 'update-archive) exec bash "$ROOT/scripts/archive-release-update.sh"'],
  ["scripts/archive-release-update.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/archive-release-update.sh", "archive-preview"],
  ["scripts/archive-release-update.sh", "archive-hash"],
  ["control/server.py", 'self.path == "/api/v1/diagnostics"'],
  ["control/server.py", 'self.path == "/api/v1/doctor"'],
  ["control/doctor.py", "networkProbesPerformed"],
  ["control/server.py", "privateEvidenceHashesProjected"],
  ["scripts/prepare-release-update.sh", 'prepare "$@" --staging-root "$staging_root"'],
  ["scripts/rehearse-release-update.sh", 'rehearse "$@" --staging-root "$staging_root"'],
  ["scripts/limb-kit.py", "PrivateNetwork=true"],
  ["scripts/limb-kit.py", "SIGNATURE_NAMESPACE = \"pixel-limb-pack\""],
  ["scripts/upstream.mjs", "stateChanged: false"],
  ["scripts/upstream.mjs", "sourceChanged: false"],
  ["scripts/upstream.mjs", "Upstream quarantine must be outside the source repository"],
  ["scripts/lib/upstream-registry.mjs", 'REGISTRY_ORIGIN = "https://registry.npmjs.org"'],
  ["scripts/lib/upstream-registry.mjs", 'redirect: "error"'],
  ["scripts/lib/upstream-registry.mjs", "Artifact failed npm integrity verification"],
  ["scripts/lib/upstream-registry.mjs", "non-stable version"],
  ["scripts/extract-upstream-package.py", "unsupported archive member type"],
  ["scripts/lib/upstream-contract-diff.mjs", 'newAuthority: "blocking"'],
  ["scripts/lib/upstream-contract-diff.mjs", 'removedDenial: "blocking"'],
  ["scripts/lib/upstream-contract-diff.mjs", 'credentialPathChange: "blocking"'],
  ["scripts/lib/upstream-contract-diff.mjs", 'networkOrFilesystemSurfaceChange: "blocking"'],
  ["scripts/upstream-runtime-probe.py", "pixel_gmail_inbox"],
  ["scripts/upstream-runtime-probe.py", "pixel_social_feed"],
  ["scripts/upstream-runtime-probe.py", "NoNewPrivileges=true"],
  ["scripts/upstream-runtime-probe.py", "rollback-version"],
  ["scripts/run-upstream-runtime-matrix.sh", ".qualificationImages[$key].fingerprint"],
  ["scripts/run-upstream-runtime-matrix.sh", 'incus image copy "images:$fingerprint"'],
  ["scripts/run-upstream-runtime-matrix.sh", 'incus query "/1.0/images/$fingerprint"'],
  ["scripts/run-upstream-runtime-matrix.sh", "--vm"],
  ["scripts/run-supported-host-systemd-matrix.sh", "debian12Vm"],
  ["scripts/run-supported-host-systemd-matrix.sh", "env -i"],
  ["scripts/run-supported-host-systemd-matrix.sh", "/tmp/dream-fleet-heavy.lock"],
  ["scripts/run-supported-host-systemd-matrix.sh", "--immutable-source /opt/pixel-qualification-source"],
  ["scripts/supported-host-systemd-probe.py", "assert_credential_free_environment"],
  ["scripts/supported-host-systemd-probe.py", '"providerCalls": 0'],
  ["scripts/supported-host-systemd-probe.py", "sandboxed-agent-turn"],
  ["scripts/supported-host-systemd-probe.py", "deep-work-real-crash-endurance"],
  ["scripts/supported-host-systemd-probe.py", "deep-work-supervised-service"],
  ["scripts/supported-host-systemd-probe.py", "deep-work-service-install-inactive"],
  ["scripts/supported-host-systemd-probe.py", "deep-work-service-watchdog-isolate-path"],
  ["scripts/supported-host-systemd-probe.py", "wait_for_systemd_service_success(service, after=initial_start)"],
  ["scripts/supported-host-systemd-probe.py", "capability-pack-live-runtime"],
  ["scripts/supported-host-systemd-probe.py", "PIXEL_LIVE_DOCKER"],
  ["scripts/run-supported-host-systemd-matrix.sh", "libc6-dev"],
  [".github/workflows/product-qualification.yml", "workflow_dispatch"],
  [".github/workflows/product-qualification.yml", "persist-credentials: false"],
  [".github/workflows/product-qualification.yml", "Codex work-provider images / reproducible construction"],
  [".github/workflows/product-qualification.yml", "./scripts/qualify-work-codex-images.sh"],
  ["deploy/web-courier/requirements.lock", `playwright==${manifest.webCourier.playwright}`],
  ["deploy/web-courier/requirements.lock", `trafilatura==${manifest.webCourier.trafilatura}`],
  ["deploy/web-courier/requirements.lock", "--hash=sha256:"],
  ["scripts/bootstrap.sh", "--require-hashes"],
  ["scripts/preflight.sh", "--require-hashes"],
  ["scripts/lib/release-build.sh", "--require-hashes"],
  ["scripts/configure.mjs", "NoNewPrivileges=true"],
  ["scripts/configure.mjs", "ProtectSystem=strict"],
  ["scripts/configure.mjs", "ProtectHome=tmpfs"],
  ["scripts/configure.mjs", "CapabilityBoundingSet="],
  ["scripts/configure.mjs", 'ReadOnlyPaths=${systemdPath(join(openclawHome, "npm"))}'],
  ["scripts/configure.mjs", "User=${serviceUser}"],
  ["scripts/configure.mjs", "gateway --bind loopback --auth token"],
  ["scripts/configure.mjs", "PIXEL_GATEWAY_SYSTEMD_DIR"],
  ["scripts/render-config.mjs", 'treeSessionTools = ["sessions_list", "sessions_history", "sessions_send"]'],
  ["scripts/render-config.mjs", 'profile: "coding"'],
  ["scripts/render-config.mjs", 'sessions: { visibility: "tree" }'],
  ["scripts/render-config.mjs", 'dmScope: "per-account-channel-peer"'],
  ["scripts/render-config.mjs", 'fs: { workspaceOnly: true }'],
  ["scripts/render-config.mjs", 'capDrop: ["ALL"]'],
  ["scripts/render-config.mjs", 'channels: extensionChannels'],
  ["scripts/render-config.mjs", 'for (const name of ["chatCompletions", "responses"])'],
  ["scripts/render-config.mjs", 'messages: { suppressToolErrors: current.messages?.suppressToolErrors === true }'],
  ["scripts/render-config.mjs", "skills: agentSkills"],
  ["scripts/render-config.mjs", "differs from its approved SHA-256 tree digest"],
  ["scripts/configure.mjs", "Custom gateway extensions cannot be loaded from gateway-writable state"],
  ["scripts/configure.mjs", "ID-only gatewayExtensions must be in Pixel's pinned extension catalog"],
  ["scripts/configure.mjs", "WantedBy=multi-user.target"],
  ["scripts/configure.mjs", `PIXEL_SOURCE_BROKER_USER: "${manifest.sourceBroker.systemUser}"`],
  ["scripts/configure.mjs", 'PIXEL_SOURCE_GMAIL_QUERY: "in:inbox"'],
  ["scripts/configure.mjs", 'PIXEL_SOURCE_GMAIL_PAGE_SIZE: "100"'],
  ["scripts/configure.mjs", 'PIXEL_SOURCE_GMAIL_MAX_PAGES: "1000"'],
  ["scripts/configure.mjs", 'PIXEL_SOURCE_GMAIL_SENT_QUERY: "in:sent"'],
  ["scripts/configure.mjs", 'PIXEL_SOURCE_GMAIL_SENT_PAGE_SIZE: "100"'],
  ["scripts/configure.mjs", 'PIXEL_SOURCE_GMAIL_SENT_MAX_PAGES: "1000"'],
  ["scripts/configure.mjs", `PIXEL_OPS_BROKER_USER: "${manifest.operationsBroker.systemUser}"`],
  ["scripts/configure.mjs", "User=${values.PIXEL_SOURCE_BROKER_USER}"],
  ["scripts/configure.mjs", "ReadOnlyPaths=${systemdPath(sourceBrokerInstallDir)}"],
  ["deploy/source-broker/broker.py", "rawContentAvailableToPixel"],
  ["deploy/source-broker/broker.py", '"metadataHeaders": ["From", "To", "Cc", "Subject", "Date"]'],
  ["scripts/render-config.mjs", '"pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search"'],
  ["scripts/migrate-source-broker-config.mjs", '"pixel_gmail_inbox", "pixel_gmail_sent", "pixel_gmail_search"'],
  ["scripts/migrate-workspace-source-boundary.mjs", "pixel_gmail_sent"],
  ["deploy/source-broker/broker.py", '"contentProjection": "sanitized-summary" if include_content else "metadata-only"'],
  ["deploy/source-broker/broker.py", "instructionsAuthorized"],
  ["deploy/source-broker/broker.py", "Potential instruction-targeting content was quarantined"],
  ["deploy/source-broker/broker.py", manifest.sourceBroker.approvalBinding],
  ["deploy/source-broker/broker.py", "claim_proposal"],
  ["deploy/source-broker/broker.py", "expectedEtag"],
  ["deploy/source-broker/broker.py", '{"if-match": fields["expectedEtag"]}'],
  ["scripts/show-source-action.sh", "snapshot-proposal.py"],
  ["deploy/ops-broker/broker.py", "only public HTTPS downloads are allowed"],
  ["deploy/ops-broker/broker.py", "approval does not match the immutable plan"],
  ["deploy/ops-broker/broker.py", "hostname resolves to a forbidden network address"],
  ["deploy/ops-broker/broker.py", "download redirect escaped the reviewed source domain"],
  ["deploy/ops-broker/broker.py", "download content does not match expectedSha256"],
  ["deploy/ops-broker/broker.py", "untrustedOutput"],
  ["deploy/web-courier/courier.py", "O_NOFOLLOW"],
  ["deploy/web-courier/courier.py", "check_url_policy"],
  ["deploy/web-courier/courier.py", "connect_public_url"],
  ["deploy/web-courier/courier.py", "SAFE_HTTP_METHODS"],
  ["deploy/web-courier/courier.py", 'ALLOWED_PORTS = _env_ports("PIXEL_WEB_COURIER_ALLOWED_PORTS", "80,443")'],
  ["deploy/web-courier/courier.py", "validate_research_request"],
  ["deploy/web-courier/courier.py", "java_script_enabled=research is None"],
  ["deploy/web-courier/courier.py", "research retrieval allows only the navigation document"],
  ["deploy/ops-runner/provision-target.sh", 'restrict,command="/usr/local/libexec/pixel-ops-dispatch'],
  ["deploy/ops-runner/provision-target.sh", "authority=transport-only"],
  ["deploy/ops-runner/dispatch.py", "transportIdentitySeparated"],
  ["scripts/configure-ops-target-actions.sh", "pixel-ops-transport ALL=(root)"],
  ["scripts/provision-ops-target.sh", "dispatch.py"],
  ["scripts/refresh-ops-target.sh", "dispatch.py"],
  ["scripts/restore-private-state.sh", "ssh-keygen -Y verify"],
  ["scripts/rotate-gateway-token.sh", "pixel_acquire_deployment_lock exclusive"],
  ["scripts/lib/common.sh", "Another Pixel deployment, backup, restore, rollback, or rotation is active"],
  ["workspace-template/WEB-NAVIGATION.md", "untrusted data"],
  ["workspace-template/scripts/browse.sh", "media/webq"],
];
for (const [path, expected] of contracts) {
  if (!(await read(path)).includes(expected)) throw new Error(`${path} is missing release contract value: ${expected}`);
}
const bootstrapSandboxSource = await read("scripts/bootstrap.sh");
for (const forbidden of [
  'docker image tag ',
  'pixel_preserve_sandbox_image ',
  '-t "$PIXEL_SANDBOX_IMAGE"',
]) {
  if (bootstrapSandboxSource.includes(forbidden)) {
    throw new Error(`scripts/bootstrap.sh must stage only the candidate sandbox tag; forbidden live-tag mutation: ${forbidden}`);
  }
}
// The shared release-build primitive (scripts/lib/release-build.sh) is the ONLY authority
// that builds an installable Pixel release tree and writes install-manifest.sha256. Both the
// ordinary apply path and the migration prepare path must source it and build through it, so
// the two can never drift: whatever apply installs as current is byte-contract-equivalent to
// what migration prepare stages.
for (const caller of ["scripts/apply.sh", "scripts/migrate-prepare.sh"]) {
  const callerSource = await read(caller);
  if (!callerSource.includes('source "$ROOT/scripts/lib/release-build.sh"')) {
    throw new Error(`${caller} must source the shared release-build primitive (scripts/lib/release-build.sh)`);
  }
  if (!callerSource.includes("pixel_build_release_stage ")) {
    throw new Error(`${caller} must build the release through the shared release-build primitive (pixel_build_release_stage)`);
  }
}
for (const [path, preview] of [
  ["scripts/activate-release-update.sh", "activation-preview"],
  ["scripts/reactivate-release-update.sh", "reactivation-preview"],
  ["scripts/rollback-reactivated-release-update.sh", "reactivation-rollback-preview"],
  ["scripts/recover-reactivated-release-update.sh", "reactivation-recovery-preview"],
  ["scripts/rollback-release-update.sh", "rollback-preview"],
  ["scripts/recover-release-update.sh", "recovery-preview"],
  ["scripts/cleanup-release-update.sh", "cleanup-preview"],
  ["scripts/archive-release-update.sh", "archive-preview"],
]) {
  const source = await read(path);
  const previewIndex = source.indexOf(preview);
  const forwardedIndex = source.indexOf('"$@"', previewIndex);
  const fixedIndex = source.indexOf('--staging-root "$staging_root"', forwardedIndex);
  if (previewIndex < 0 || forwardedIndex < previewIndex || fixedIndex < forwardedIndex) {
    throw new Error(path + " must apply the trusted staging root after forwarded preview arguments");
  }
}
const authorization = await read("plugin/authorize.mjs");
const implementedScopes = [...new Set([...authorization.matchAll(/"(https:\/\/www\.googleapis\.com\/auth\/[^"]+)"/g)].map((match) => match[1]))].sort();
if (JSON.stringify(implementedScopes) !== JSON.stringify([...manifest.googleScopes].sort())) {
  throw new Error(`OAuth scopes differ from the release contract: ${implementedScopes.join(", ")}`);
}
const plugin = await read("plugin/index.js");
const webTool = await read("plugin/web-tool.js");
const toolNames = [...(plugin + webTool).matchAll(/register\(api, "([^"]+)"/g)].map((match) => match[1]).sort();
if (JSON.stringify(toolNames) !== JSON.stringify([...manifest.googleTools].sort())) throw new Error("Google tool surface differs from the release contract");
const pluginManifest = JSON.parse(await read("plugin/openclaw.plugin.json"));
if (JSON.stringify([...(pluginManifest.contracts?.tools ?? [])].sort()) !== JSON.stringify([...manifest.googleTools].sort())) throw new Error("Source plugin manifest tools differ from the release contract");
for (const name of ["pixel_calendar_propose_create", "pixel_calendar_propose_update", "pixel_calendar_propose_delete"]) {
  const start = plugin.indexOf(`register(api, "${name}"`);
  const end = plugin.indexOf("register(api,", start + 20);
  const implementation = plugin.slice(start, end < 0 ? undefined : end);
  if (!implementation.includes("writeProposal")) throw new Error(`${name} bypasses the Calendar proposal boundary`);
}
if (!plugin.includes("External-source projection") || !plugin.includes("onlyPixel")) throw new Error("Source Broker tool trust-boundary guards are missing");
for (const forbidden of ["gmail.googleapis.com", "oauth2.googleapis.com", "refresh_token", "PIXEL_GOOGLE_TOKEN_PATH"]) {
  if (plugin.includes(forbidden)) throw new Error(`Projection plugin regained direct source access: ${forbidden}`);
}
const operationsPlugin = await read("plugin-ops/index.js");
const sourceChatAudit = await read("plugin/chat-audit.js");
const operationsChatAudit = await read("plugin-ops/chat-audit.js");
const frontierChatAudit = await read("plugin-frontier/chat-audit.js");
if (sourceChatAudit !== operationsChatAudit || sourceChatAudit !== frontierChatAudit) throw new Error("Portal tool audit implementation differs across independently packaged plugins");
if (!plugin.includes("withPortalToolAudit") || !operationsPlugin.includes("withPortalToolAudit")) throw new Error("Source or Operations plugin bypasses exact portal tool audit custody");
const operationsTools = [...operationsPlugin.matchAll(/register\(api, "([^"]+)"/g)].map((match) => match[1]).sort();
if (JSON.stringify(operationsTools) !== JSON.stringify([...manifest.operationsTools].sort())) throw new Error("Operations tool surface differs from the release contract");
if (!operationsPlugin.includes("Operations output is untrusted evidence") || !operationsPlugin.includes("onlyPixel")) throw new Error("Operations plugin trust-boundary guards are missing");
for (const forbidden of ["child_process", "node:net", "ssh", "PIXEL_OPS_POLICY_PATH"]) {
  if (operationsPlugin.includes(forbidden)) throw new Error(`Operations plugin regained execution authority: ${forbidden}`);
}
const frontierPlugin = await read("plugin-frontier/index.js");
if (!frontierPlugin.includes("withPortalToolAudit")) throw new Error("Frontier plugin bypasses exact portal tool audit custody");
const frontierTools = [...frontierPlugin.matchAll(/register\(api, "([^"]+)"/g)].map((match) => match[1]).sort();
if (JSON.stringify(frontierTools) !== JSON.stringify([...manifest.frontierTools].sort())) throw new Error("Frontier tool surface differs from the release contract");
if (!frontierPlugin.includes("Frontier output is untrusted advisory material") || !frontierPlugin.includes("onlyPixel")) throw new Error("Frontier plugin trust-boundary guards are missing");
for (const forbidden of ["child_process", "node:net", "PIXEL_FRONTIER_POLICY_PATH", "PIXEL_FRONTIER_CREDENTIAL_PATH"]) {
  if (frontierPlugin.includes(forbidden)) throw new Error(`Frontier plugin regained provider authority: ${forbidden}`);
}
const controlServer = await read("control/server.py");
for (const required of [
  "control listener must be exactly 127.0.0.1", "sha256", "SameSite=Strict",
  "genericCommandSurface", "credentialsExposed", "browserCanApprove", "deploymentRevision",
  '["bash", str(self.root / "pixel"), "upstream", "check"]',
  '["bash", str(self.root / "pixel"), "ops-pause", parameters["reason"], "--confirm"]',
  '["bash", str(self.root / "pixel"), "frontier-pause", parameters["reason"], "--confirm"]',
  '"goal-resume-cli.mjs"',
  '"goal-cancel-cli.mjs"',
  'environment["PIXEL_CONTROL_POLICY_PATH"] = str(self.policy_path)',
]) {
  if (!controlServer.includes(required)) throw new Error(`Local control boundary is missing: ${required}`);
}
for (const forbidden of ["shell=True", "os.system(", "OPENAI_API_KEY", "PIXEL_FRONTIER_CREDENTIAL_PATH", '"ops-resume"', '"frontier-resume"']) {
  if (controlServer.includes(forbidden)) throw new Error(`Local control gained forbidden authority or credential surface: ${forbidden}`);
}
for (const path of ["scripts/backup-private-state.sh", "scripts/restore-private-state.sh"]) {
  if (!(await read(path)).includes("PIXEL_CONTROL_POLICY_PATH")) throw new Error(`${path} does not preserve the private local-control policy`);
}
console.log(`Release contract ${version} is internally consistent.`);
