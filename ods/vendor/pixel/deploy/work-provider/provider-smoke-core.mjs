/**
 * Provider smoke core — internal implementation with injected dependencies.
 *
 * This module contains the full smoke flow logic parameterized by transport,
 * clock, and suffix generator. Production and test wrappers each import it
 * and supply only their own closed dependencies.
 *
 * @module
 */
import { createHash, randomBytes } from "node:crypto";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { inspectWorkProviderCredentialCustody } from "./credential-custody.mjs";
import { readOwnerPrivateWorkProviderPolicy } from "./dev-proxy-cli.mjs";
import { parseBoundWorkProviderPrivatePolicy } from "./egress-proxy.mjs";
import {
  REMOTE_WIRE_MAPPINGS,
  RemoteWorkProviderTransportError,
  remoteTransportInternals,
} from "./generic-remote-transport.mjs";
import { resolveWorkProviderTransport } from "./transport-registry.mjs";
import {
  claimWorkProviderRequest,
  closeWorkProviderRun,
  createConnectivitySmokeBinding,
  createWorkProviderRunLedger,
  providerIdempotencyKey,
  settleWorkProviderRequest,
} from "./run-ledger.mjs";
import {
  initializeWorkProviderRunStore,
  replaceWorkProviderRunLedger,
  storeNewWorkProviderRunLedger,
} from "./run-store.mjs";

const { SMOKE_OK } = remoteTransportInternals;

// Closed, per-provider smoke definitions. Each entry binds the exact purpose,
// sentinel, model, and smoke request shape so the unified smoke cannot drift.
// OpenAI/Anthropic use the generic remote transport sentinel; Moonshot/Kimi
// uses PIXEL_K3_SMOKE_OK with reasoningEffort:"low" as enforced by the
// moonshot-transport.mjs stricter path.
export const SMOKE_DEFINITIONS = Object.freeze({
  "moonshot-kimi": Object.freeze({
    purpose: "moonshot-connectivity-smoke-v1",
    sentinel: "PIXEL_K3_SMOKE_OK",
    model: "kimi-k3",
    buildRequest: (model) => Object.freeze({
      schemaVersion: 1,
      model,
      messages: Object.freeze([{ role: "user", content: `Return exactly PIXEL_K3_SMOKE_OK and nothing else.` }]),
      maxOutputTokens: 256,
      reasoningEffort: "low",
    }),
  }),
  openai: Object.freeze({
    purpose: "openai-connectivity-smoke-v1",
    sentinel: SMOKE_OK,
    model: "gpt-5.6",
    buildRequest: (model) => Object.freeze({
      schemaVersion: 1,
      model,
      messages: Object.freeze([{ role: "user", content: `Return exactly ${SMOKE_OK} and nothing else.` }]),
      maxOutputTokens: 256,
    }),
  }),
  anthropic: Object.freeze({
    purpose: "anthropic-connectivity-smoke-v1",
    sentinel: SMOKE_OK,
    model: "claude-sonnet-4-5-20250929",
    buildRequest: (model) => Object.freeze({
      schemaVersion: 1,
      model,
      messages: Object.freeze([{ role: "user", content: `Return exactly ${SMOKE_OK} and nothing else.` }]),
      maxOutputTokens: 256,
    }),
  }),
});

export class ProviderSmokeCoreError extends Error {
  constructor(message) {
    super(message);
    this.name = "ProviderSmokeCoreError";
  }
}

function fail(message) {
  throw new ProviderSmokeCoreError(message);
}

function sha(value) {
  return createHash("sha256").update(canonical(value)).digest("hex");
}

/**
 * Run the full provider smoke flow using injected dependencies.
 *
 * This is a connectivity qualification only — it proves credential and wire
 * path are functional. It is NOT effectiveness parity with local or other
 * provider lanes. Parity requires independent neutral-corpus execution and
 * grading.
 *
 * @param {object} deps
 * @param {Function} deps.executeTransport - Closed transport execute function.
 *   Production uses the transport-registry resolve; tests inject a hermetic fn.
 * @param {Function} [deps.now] - Clock override; defaults to Date constructor.
 * @param {Function} [deps.suffix] - Suffix generator; defaults to random hex.
 * @param {string} [deps.proxyHost] - Proxy host; must be 127.0.0.1 or pixel-provider-egress.
 * @param {object} parsed - Pre-parsed bound policy with {policy, resolvedProvider}.
 * @param {string} credentialPath - Absolute path to credential file.
 * @param {string} ledgerRoot - Absolute path to ledger root directory.
 * @param {string} providerId - The provider id to smoke (must be in SMOKE_DEFINITIONS).
 * @returns {Promise<object>} Content-free smoke receipt.
 */
export async function runProviderSmokeCore({
  executeTransport,
  now = () => new Date(),
  suffix = () => randomBytes(6).toString("hex"),
  proxyHost = "127.0.0.1",
  parsed,
  credentialPath,
  ledgerRoot,
  providerId,
}) {
  if (typeof executeTransport !== "function") {
    fail("executeTransport function is required");
  }
  if (!["127.0.0.1", "pixel-provider-egress"].includes(proxyHost)) {
    fail("provider smoke proxy host is invalid");
  }

  const definition = SMOKE_DEFINITIONS[providerId];
  if (!definition) {
    fail(`provider ${providerId} is not in the closed smoke provider registry`);
  }

  const { policy, resolvedProvider } = parsed;

  // Verify the provider has a wire mapping
  const wire = REMOTE_WIRE_MAPPINGS[providerId];
  if (!wire || wire.protocol !== resolvedProvider.profile.protocol) {
    fail(`provider ${providerId} has no closed remote wire mapping for smoke`);
  }

  const smokeModel = definition.model;
  if (resolvedProvider.profile.defaultModel !== smokeModel) {
    fail(`provider ${providerId} profile model differs from smoke model`);
  }

  // Inspect credential custody
  const custody = await inspectWorkProviderCredentialCustody({
    resolvedProvider,
    policy,
    credentialPath,
    now: now(),
    suffix: suffix(),
  });

  const root = await initializeWorkProviderRunStore({ root: ledgerRoot });

  // Build the fixed smoke request from the provider-authoritative definition
  const smokeRequest = definition.buildRequest(smokeModel);

  // Create the binding
  const binding = createConnectivitySmokeBinding({
    purpose: definition.purpose,
    requestSha256: sha(smokeRequest),
    model: smokeModel,
  });

  // Create and store the ledger
  let ledger = createWorkProviderRunLedger({
    resolvedProvider,
    policy,
    taskId: `pixel-${providerId}-smoke`,
    model: smokeModel,
    binding,
    now: now(),
    suffix: suffix(),
  });
  let stored = await storeNewWorkProviderRunLedger({ root, ledger });

  // Claim the request
  const idempotencyKey = providerIdempotencyKey({
    runId: ledger.runId,
    turn: 1,
    purpose: definition.purpose,
  });
  ledger = claimWorkProviderRequest({
    ledger,
    policy,
    resolvedProvider,
    idempotencyKey,
    inputSha256: sha(smokeRequest),
    estimatedInputTokens: 128,
    maxOutputTokens: 256,
    maxEstimatedCostMicros: 100000,
    now: now(),
  });
  stored = await _replace(root, stored, ledger);

  // Execute the turn
  let result;
  try {
    result = await executeTransport({
      resolvedProvider,
      policy,
      credentialHandle: custody.handle,
      ledger,
      idempotencyKey,
      input: smokeRequest,
      proxyHost,
      proxyPort: 3128,
    });
  } catch (error) {
    const isRemoteError = error instanceof RemoteWorkProviderTransportError;
    const outcome =
      isRemoteError && error.outcome === "failed-known" ? "failed-known" : "uncertain";

    ledger = settleWorkProviderRequest({
      ledger,
      policy,
      resolvedProvider,
      idempotencyKey,
      outcome,
      now: now(),
    });
    stored = await _replace(root, stored, ledger);

    if (outcome === "failed-known") {
      ledger = closeWorkProviderRun({ ledger, now: now() });
      await _replace(root, stored, ledger);
      fail(`provider ${providerId} smoke failed with a known terminal provider outcome`);
    }
    fail("provider smoke outcome is uncertain; do not retry automatically and inspect the content-free run ledger");
  }

  // Settle the request
  ledger = settleWorkProviderRequest({
    ledger,
    policy,
    resolvedProvider,
    idempotencyKey,
    outcome: "succeeded",
    usage: result.assistant.usage,
    providerRequestId: result.providerRequestId,
    now: now(),
  });
  stored = await _replace(root, stored, ledger);

  // Close the run
  ledger = closeWorkProviderRun({ ledger, now: now() });
  stored = await _replace(root, stored, ledger);

  // Verify the sentinel response
  const content = result.assistant.message.content?.trim();
  const sentinel = definition.sentinel;
  const matched =
    result.assistant.finishReason === "stop" &&
    content === sentinel &&
    !result.assistant.message.toolCalls?.length;

  return Object.freeze({
    schemaVersion: 1,
    status: matched ? "passed" : "provider-response-mismatch",
    runId: ledger.runId,
    providerId: ledger.providerId,
    model: ledger.model,
    inputTokens: ledger.totals.inputTokens,
    outputTokens: ledger.totals.outputTokens,
    providerContentIncluded: false,
    credentialIncluded: false,
    ledgerSha256: stored.sha256,
  });
}

async function _replace(root, stored, ledger) {
  const next = await replaceWorkProviderRunLedger({ root, expectedSha256: stored.sha256, ledger });
  return Object.freeze({ runId: next.runId, sha256: next.sha256 });
}
