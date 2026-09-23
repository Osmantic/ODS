// Completion assurance for an explicit GitHub /extensions installation turn.
// The model chooses the recipe and tools. This gate only observes request-bound
// receipts; a page read, draft, or prepared package is never installation proof.
const STATUS = 'pixel_ods_extension_request_status';
const PREPARE = 'pixel_ods_extension_request_prepare';
const ADVANCE = new Set(['pixel_ods_extension_request_advance', 'pixel_ods_extension_request_retry']);
const PROPOSE = new Set(['pixel_ods_python_library_proposal', 'pixel_ods_source_proposal', 'pixel_ods_extension_proposal']);
const READY = new Set(['enabled', 'cli_installed']);
const IN_PROGRESS = new Set(['installing', 'setting_up']);
const PREPARATION_REASONS = new Set(['proposal_required', 'integration_selection_required',
  'license_review_required', 'repository_evidence_unavailable', 'recipe_inspection_required', 'request_changed']);
const PREPARATION_BLOCKERS = new Set(['license_review_required', 'repository_evidence_unavailable',
  'recipe_inspection_required', 'request_changed']);
const MAX_REVISIONS = 10;
const EXTENSION_ID = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export function githubExtensionInstallRequested(text) {
  // A slash route creates provisional tracking only. The durable request's
  // authorizationMode, observed via its exact status tool, decides whether
  // installation recovery can run. Never infer authority from prose suffixes.
  return typeof text === 'string' &&
    /^\s*(?:\/goal\s+)?\/extensions?\s+(?:(?:install|inspect|research)\s+)?https:\/\/github\.com\/[^\s]+/i.test(text);
}

function validIdentity(value) {
  return typeof value?.chatId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value.chatId) &&
    typeof value?.requestId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value.requestId);
}

function validatedStatus(result) {
  const value = result?.details;
  return !result?.isError && value?.schemaVersion === 1 && value.kind === 'ods-extension-request-status' &&
    validIdentity(value) && ['pending', 'expired', 'cancelled'].includes(value.requestState) &&
    ['install', 'research'].includes(value.authorizationMode) &&
    typeof value.proposalAccepted === 'boolean' && typeof value.prepared === 'boolean' &&
    typeof value.integrationBound === 'boolean' &&
    !(value.integrationBound && value.proposalAccepted) &&
    (value.extensionId === null || (typeof value.extensionId === 'string' && EXTENSION_ID.test(value.extensionId))) &&
    (!value.prepared || (value.extensionId && (value.proposalAccepted || value.integrationBound))) &&
    ['not_observed', 'enabled', 'cli_installed', 'disabled', 'stopped', 'not_installed',
      'installing', 'setting_up', 'unhealthy', 'error', 'unavailable'].includes(value.runtimeStatus) &&
    (value.installationVerified === undefined ||
      (typeof value.installationVerified === 'boolean' &&
       value.installationVerified === (value.prepared && value.requestState === 'pending' &&
         READY.has(value.runtimeStatus)))) &&
    (value.runtimeStatus === 'not_observed' || value.prepared) ? value : undefined;
}

function validatedAdvance(result) {
  const value = result?.details;
  return !result?.isError && value?.schemaVersion === 1 && value.kind === 'ods-extension-request-installation' &&
    validIdentity(value) && typeof value.extensionId === 'string' && EXTENSION_ID.test(value.extensionId) &&
    ['pending', 'succeeded', 'failed', 'blocked', 'configuration_required', 'reconciliation_required'].includes(value.state) &&
    (value.activeExtensionId === null || (typeof value.activeExtensionId === 'string' && EXTENSION_ID.test(value.activeExtensionId))) &&
    (value.operationId === null || (typeof value.operationId === 'string' && /^[a-f0-9]{32}$/.test(value.operationId))) &&
    typeof value.dispatched === 'boolean' &&
    (value.state !== 'succeeded' || (!value.dispatched && value.activeExtensionId === null)) ? value : undefined;
}

function validatedPrepareRejection(result) {
  const value = result?.details;
  return result?.isError === true && value?.schemaVersion === 1 &&
    value.kind === 'ods-extension-request-preparation-rejected' && validIdentity(value) &&
    PREPARATION_REASONS.has(value.reason) && value.installationStarted === false ? value : undefined;
}

export function createExtensionCompletionGate(ownerText) {
  let active = githubExtensionInstallRequested(ownerText), serverAuthorized = false;
  let identity, status, advancement, preparationRejection, repositoryCooldown, advanceOutcomeUnknown = false;
  let mutationEpoch = 0, revisions = 0;
  let terminal;
  const stageAttempts = new Map();
  const bind = value => {
    if (!identity) identity = {chatId:value.chatId, requestId:value.requestId};
    return identity.chatId === value.chatId && identity.requestId === value.requestId;
  };
  const progress = tool => {
    if (PROPOSE.has(tool) || tool === PREPARE) {
      mutationEpoch += 1;
      status = undefined; // A prior read cannot prove the new stage.
      advancement = undefined;
      if (PROPOSE.has(tool)) preparationRejection = undefined;
    }
  };
  return {
    get active() { return active; },
    get observedInstallStatus() {
      // This is a session-bound, schema-validated read receipt. It never
      // authorizes a new request or proves an installation by itself.
      return serverAuthorized && status?.authorizationMode === 'install' &&
        status.requestState === 'pending' ? {chatId:identity.chatId, requestId:identity.requestId} : undefined;
    },
    get proposalRequiredNoWork() {
      // This is only a validated, request-bound rejection. The host route must
      // re-read the durable request before granting any continuation.
      return active && preparationRejection?.reason === 'proposal_required' && identity
        ? {chatId:identity.chatId, requestId:identity.requestId} : undefined;
    },
    observe(tool, result) {
      if (!active) return;
      if (tool === STATUS) {
        const value = validatedStatus(result);
        if (value && bind(value)) {
          if (value.authorizationMode === 'research') {
            active = false;
            terminal = undefined;
          } else {
            serverAuthorized = true;
            status = value;
            if (value.prepared) preparationRejection = undefined;
          }
        }
      } else if (tool === PREPARE) {
        const rejected = validatedPrepareRejection(result);
        if (rejected && bind(rejected)) preparationRejection = rejected;
        else if (!result?.isError) preparationRejection = undefined;
        progress(tool);
      } else if (ADVANCE.has(tool)) {
        const value = validatedAdvance(result);
        if (value && bind(value)) {
          advancement = value;
          advanceOutcomeUnknown = false;
          status = undefined;
          mutationEpoch += 1;
        } else {
          // A missing/invalid reply is not proof that the side effect did not
          // occur. Read the saved request; never ask for a blind replay.
          advanceOutcomeUnknown = true;
          advancement = undefined;
          status = undefined;
          mutationEpoch += 1;
        }
      } else if (PROPOSE.has(tool)) {
        const cooldown = result?.details;
        if ((result?.isError === true || cooldown?.ok === false)
            && cooldown?.schemaVersion === 1 && cooldown.kind === 'ods-extension-request-repository-unavailable'
            && validIdentity(cooldown) && cooldown.reason === 'github-rate-limited'
            && Number.isInteger(cooldown.retryAfter) && cooldown.retryAfter >= 1 && cooldown.retryAfter <= 3600
            && cooldown.installationStarted === false && bind(cooldown)) repositoryCooldown = cooldown;
        // An authorized proposal may coordinate preparation and advancement
        // internally. Its returned managed installation receipt is authoritative;
        // a plain draft or uncertain error instead requires a fresh status read.
        const coordinated = validatedAdvance(result);
        if (coordinated && bind(coordinated)) {
          if (coordinated.authorizationMode === 'install') serverAuthorized = true;
          advancement = coordinated;
          advanceOutcomeUnknown = false;
          status = undefined;
          mutationEpoch += 1;
        } else {
          const rejected = validatedPrepareRejection(result);
          progress(tool);
          if (rejected && bind(rejected)) preparationRejection = rejected;
        }
      } else progress(tool);
    },
    finalize() {
      if (!active) return undefined;
      if (repositoryCooldown) {
        terminal = {status:'failed', text:`GitHub limited repository evidence requests. Retry after ${repositoryCooldown.retryAfter} seconds. This request did not start an installation.`};
        return undefined;
      }
      const success = serverAuthorized && (advancement?.state === 'succeeded' ||
        (status?.prepared && READY.has(status.runtimeStatus)));
      if (success) {
        const extensionId = advancement?.state === 'succeeded' ? advancement.extensionId : status.extensionId;
        terminal = {status:'passed', text:`ODS observed managed installation readiness for \`${extensionId}\` in this request. This receipt does not verify untested application behavior.`, deliveryMode:'append'};
        return undefined;
      }
      if (serverAuthorized && preparationRejection && PREPARATION_BLOCKERS.has(preparationRejection.reason)) {
        terminal = {status:'failed', text:`ODS refused preparation (${preparationRejection.reason}); installation did not start. Inspect this saved request and correct the repository evidence or recipe before trying again. No installation is claimed.`};
        return undefined;
      }
      const failedAdvance = advancement && !['pending', 'succeeded'].includes(advancement.state);
      if (failedAdvance || (status && (status.requestState !== 'pending' || status.runtimeStatus === 'error'))) {
        const cause = failedAdvance ? `managed operation is \`${advancement.state}\`` :
          status.requestState !== 'pending' ? `request is \`${status.requestState}\`` :
          `runtime is \`${status.runtimeStatus}\``;
        terminal = {status:'failed', text:`ODS could not verify installation: ${cause}. Inspect the recorded request and its diagnostic before retrying; no success is claimed.`};
        return undefined;
      }
      if (advanceOutcomeUnknown && status) {
        terminal = {status:'pending', text:'The last managed installation attempt did not return a validated terminal receipt. ODS installation outcome remains unconfirmed. Inspect this saved request later; do not submit another install blindly.'};
        return undefined;
      }
      if (advancement?.state === 'pending' || (status?.prepared && IN_PROGRESS.has(status.runtimeStatus))) {
        const extensionId = advancement?.extensionId ?? status?.extensionId;
        terminal = {status:'pending', text:`The managed installation for \`${extensionId}\` is pending. ODS has not verified readiness yet; inspect this same request later rather than starting another installation.`};
        return undefined;
      }
      let stage, instruction;
      if (!status) {
        stage = 'status';
        instruction = 'Call pixel_ods_extension_request_status with {} to observe this conversation\'s exact managed request. A web page, sandbox command or draft is not installation evidence.';
      } else if (!status.proposalAccepted && !status.integrationBound) {
        if (status.existingExtensionIds?.length) {
          stage = 'bind';
          instruction = 'The request status lists existing repository integrations. Call pixel_ods_extension_request_prepare with {} if there is one match, or its observed extensionId if there are several. Do not create a duplicate recipe.';
        } else {
          stage = 'propose';
          instruction = 'The managed request has no accepted proposal. Use the actual repository evidence and pinned commit to call pixel_ods_python_library_proposal for a standard Python library, pixel_ods_source_proposal for another single application, or pixel_ods_extension_proposal for a researched multi-service recipe. Do not invent a package, entrypoint or host command. If the necessary evidence is unavailable, report that concrete blocker.';
        }
      } else if (!status.prepared) {
        stage = 'prepare';
        instruction = 'The request has an accepted draft, not an installation. Call pixel_ods_extension_request_prepare with {} and inspect its receipt. Do not ask permission again: the owner explicitly requested installation.';
      } else {
        stage = 'advance';
        instruction = 'The request is prepared but installation is unverified. Call pixel_ods_extension_request_advance with {} once and inspect its managed receipt. Pending is not success; do not install through sandbox exec.';
      }
      const key = `${stage}:${mutationEpoch}`;
      const attempts = stageAttempts.get(key) ?? 0;
      if (attempts >= 2 || revisions >= MAX_REVISIONS) {
        terminal = {status:'failed', text:`ODS could not verify installation. The managed request remained at the \`${stage}\` stage after bounded recovery; no completed installation receipt was observed.`};
        return {action:'finalize', reason:'Bounded extension installation recovery exhausted.'};
      }
      stageAttempts.set(key, attempts + 1);
      revisions += 1;
      terminal = undefined;
      return {action:'revise', reason:'Explicit extension installation has no verified terminal receipt yet.', retry:{
        idempotencyKey:`ods-extension-${key}`, maxAttempts:2,
        instruction:instruction + ' Continue the owner task now. Use exact exposed tool schemas; never claim installation from research, a draft, preparation or a pending operation. If a tool is unavailable, state the concrete limitation and incomplete outcome.',
      }};
    },
    get verification() {
      return terminal;
    },
  };
}
