import { createHash } from "node:crypto";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { resolveWorkProviderTransport, assertClosedTransport } from "./transport-registry.mjs";
import {
  assertWorkProviderRunBound, claimWorkProviderRequest, createWorkProviderRunLedger,
  createSemanticRunBinding, settleWorkProviderRequest, closeWorkProviderRun,
} from "./run-ledger.mjs";
import {
  initializeWorkProviderRunStore, readWorkProviderRunLedger, replaceWorkProviderRunLedger,
  storeNewWorkProviderRunLedger,
} from "./run-store.mjs";
import { WorkProviderTransportRegistryError } from "./transport-registry.mjs";

export class WorkProviderExecutorError extends Error {}
function fail(message) { throw new WorkProviderExecutorError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

function assertReplayComplete({ input, resolvedProvider }) {
  const replay = resolvedProvider.profile.reasoning?.replayCompleteAssistantMessage;
  if (!replay || !input?.messages) return;
  for (const message of input.messages) {
    if (message?.role !== "assistant" || !message.providerState) continue;
    const protocol = resolvedProvider.profile.protocol;
    if (message.providerState.protocol !== protocol) fail("assistant replay provider state protocol differs from the resolved provider");
    // The assistant replay state shape is provider-native: Anthropic stores the complete
    // assistant content blocks (thinking + tool_use) in providerState.content, while every
    // OpenAI-chat-compatible provider stores the full assistant message in
    // providerState.assistantMessage. Enforce completeness against the exact wire shape so a
    // provider never silently loses private reasoning or a tool-use block across a turn.
    if (protocol === "anthropic-messages") {
      const saved = message.providerState.content;
      if (!Array.isArray(saved) || saved.length === 0) fail("Anthropic assistant replay provider state is incomplete");
      if (message.toolCalls?.length) {
        const toolUseIds = saved.filter((block) => block && block.type === "tool_use").map((block) => block.id);
        for (const call of message.toolCalls) {
          if (!toolUseIds.includes(call.id)) fail("Anthropic tool-call replay omitted its tool_use block");
        }
      }
      continue;
    }
    const saved = message.providerState.assistantMessage;
    if (!saved || typeof saved !== "object" || saved.role !== "assistant") fail("assistant replay provider state is incomplete");
    if (resolvedProvider.profile.id === "moonshot-kimi" && message.toolCalls?.length && !Object.hasOwn(saved, "reasoning_content")) fail("K3 tool-call replay omitted reasoning_content");
    if (Object.hasOwn(saved, "reasoning_content") && (typeof saved.reasoning_content !== "string" || saved.reasoning_content.length === 0)) fail("K3 reasoning_content replay is incomplete or truncated");
  }
}

function validPositive(value, label) {
  if (!Number.isSafeInteger(value) || value < 1) fail(`${label} is invalid`);
  return value;
}
function validNonNegative(value, label) {
  if (!Number.isSafeInteger(value) || value < 0) fail(`${label} is invalid`);
  return value;
}

async function executeProviderTurnCore({
  decision, request, routerPolicy, enabledPrivatePolicies, qualifications, resolvedProvider, policy, privatePolicy, qualification,
  taskId, model, binding: providedBinding, ledger: providedLedger, idempotencyKey,
  input, estimatedInputTokens, maxOutputTokens, maxEstimatedCostMicros,
  credentialHandle, now = new Date(), suffix,
  runStoreRoot, expectedLedgerSha256, proxyRoute,
}, forcedTransport = null, requirePersistence = false) {
  if (input?.maxOutputTokens !== undefined && input.maxOutputTokens !== maxOutputTokens) fail("provider input output limit differs from the admitted claim");
  const estimated = validPositive(estimatedInputTokens, "estimated input tokens");
  const output = validPositive(maxOutputTokens, "maximum output tokens");
  const cost = validNonNegative(maxEstimatedCostMicros, "maximum estimated cost");
  const idem = idempotencyKey;

  // A routed production execution requires a complete immutable semantic decision
  // binding. An unbound semantic call must be impossible.
  let ledger = providedLedger ?? createWorkProviderRunLedger({
    resolvedProvider, policy, taskId, model,
    binding: providedBinding ?? createSemanticRunBinding({ decision, request, routerPolicy, enabledPrivatePolicies, qualifications, resolvedProvider, privatePolicy, qualification, model }),
    now, suffix,
  });
  ledger = assertWorkProviderRunBound({ ledger, decision, request, routerPolicy, enabledPrivatePolicies, qualifications, resolvedProvider, privatePolicy, qualification });

  let store = null;
  if (requirePersistence) {
    if (typeof runStoreRoot !== "string" || runStoreRoot.length < 1) fail("production provider execution requires a durable owner-private run store");
    const root = await initializeWorkProviderRunStore({ root: runStoreRoot });
    if (providedLedger) {
      if (!/^[a-f0-9]{64}$/u.test(expectedLedgerSha256 ?? "")) fail("continued production provider execution requires the exact stored ledger SHA-256");
      const observed = await readWorkProviderRunLedger({ root, runId: ledger.runId });
      if (observed.sha256 !== expectedLedgerSha256 || canonical(observed.ledger) !== canonical(ledger)) fail("continued provider ledger differs from its durable store");
      store = Object.freeze({ root, sha256: observed.sha256 });
    } else {
      const created = await storeNewWorkProviderRunLedger({ root, ledger });
      store = Object.freeze({ root, sha256: created.sha256 });
    }
  }

  assertReplayComplete({ input, resolvedProvider });

  const providerTransport = forcedTransport ?? resolveWorkProviderTransport(resolvedProvider.profile.id);
  if (typeof providerTransport !== "function") fail("provider transport is invalid");

  let transportOptions = {};
  if (proxyRoute !== undefined) {
    if (!resolvedProvider.profile.remote || !["container", "loopback"].includes(proxyRoute)) fail("provider proxy route is invalid for the selected provider");
    transportOptions = proxyRoute === "loopback" ? { proxyHost: "127.0.0.1", proxyPort: 3128 } : {};
  }

  const claimed = claimWorkProviderRequest({ ledger, policy, resolvedProvider, idempotencyKey: idem, inputSha256: sha(input), estimatedInputTokens: estimated, maxOutputTokens: output, maxEstimatedCostMicros: cost, now });
  if (store) {
    const persisted = await replaceWorkProviderRunLedger({ root: store.root, expectedSha256: store.sha256, ledger: claimed });
    store = Object.freeze({ root: store.root, sha256: persisted.sha256 });
  }

  let result;
  try {
    result = await providerTransport({ resolvedProvider, policy, credentialHandle, ledger: claimed, idempotencyKey: idem, input, ...transportOptions });
  } catch (error) {
    const outcome = error && (error.outcome === "failed-known" || error.outcome === "uncertain") ? error.outcome : "uncertain";
    const terminal = settleWorkProviderRequest({ ledger: claimed, policy, resolvedProvider, idempotencyKey: idem, outcome, now });
    if (store) {
      const persisted = await replaceWorkProviderRunLedger({ root: store.root, expectedSha256: store.sha256, ledger: terminal });
      store = Object.freeze({ root: store.root, sha256: persisted.sha256 });
    }
    if (outcome === "uncertain") {
      return Object.freeze({ status: "uncertain", ledger: terminal, ledgerSha256: store?.sha256 ?? null, outcome, networkBytes: 0, assistant: null, providerRequestId: null });
    }
    return Object.freeze({ status: "failed-known", ledger: terminal, ledgerSha256: store?.sha256 ?? null, outcome, networkBytes: 0, assistant: null, providerRequestId: null });
  }

  const assistant = result.assistant;
  if (!assistant || typeof assistant !== "object" || assistant.usage === undefined) {
    const uncertain = settleWorkProviderRequest({ ledger: claimed, policy, resolvedProvider, idempotencyKey: idem, outcome: "uncertain", now });
    if (store) {
      const persisted = await replaceWorkProviderRunLedger({ root: store.root, expectedSha256: store.sha256, ledger: uncertain });
      store = Object.freeze({ root: store.root, sha256: persisted.sha256 });
    }
    return Object.freeze({ status: "uncertain", ledger: uncertain, ledgerSha256: store?.sha256 ?? null, outcome: "uncertain", networkBytes: 0, assistant: null, providerRequestId: null });
  }
  const terminal = settleWorkProviderRequest({ ledger: claimed, policy, resolvedProvider, idempotencyKey: idem, outcome: "succeeded", usage: assistant.usage, providerRequestId: result.providerRequestId ?? null, responseModel: assistant.providerModel ?? null, now });
  if (store) {
    const persisted = await replaceWorkProviderRunLedger({ root: store.root, expectedSha256: store.sha256, ledger: terminal });
    store = Object.freeze({ root: store.root, sha256: persisted.sha256 });
  }
  return Object.freeze({
    status: "succeeded",
    ledger: terminal,
    ledgerSha256: store?.sha256 ?? null,
    assistant: Object.freeze({ message: assistant.message, finishReason: assistant.finishReason, usage: assistant.usage }),
    providerRequestId: result.providerRequestId ?? null,
    networkBytes: result.networkBytes ?? 0,
  });
}

export async function executeProviderTurn(options) {
  if (!options || typeof options !== "object" || Array.isArray(options)) fail("provider executor options are invalid");
  if (Object.hasOwn(options, "transport") || Object.hasOwn(options, "testSeam")) fail("production provider execution cannot accept a caller-supplied transport");
  return executeProviderTurnCore(options, null, true);
}

export async function closeProviderRun({ ledger, runStoreRoot, expectedLedgerSha256, now = new Date() }) {
  const closed = closeWorkProviderRun({ ledger, now });
  if (runStoreRoot === undefined) return Object.freeze({ ledger: closed, ledgerSha256: null });
  if (!/^[a-f0-9]{64}$/u.test(expectedLedgerSha256 ?? "")) fail("closing a durable provider run requires its exact stored SHA-256");
  const persisted = await replaceWorkProviderRunLedger({ root: runStoreRoot, expectedSha256: expectedLedgerSha256, ledger: closed });
  return Object.freeze({ ledger: closed, ledgerSha256: persisted.sha256 });
}

export { assertClosedTransport };
export function assertTransportIsClosed(transport) {
  try { return assertClosedTransport(transport); }
  catch (error) { if (error instanceof WorkProviderTransportRegistryError) fail(error.message); throw error; }
}

// Test-only export. Release checks forbid production modules from importing this symbol.
// It exists solely so hermetic tests can exercise state transitions without live network use.
export const workProviderExecutorTestOnly = Object.freeze({
  executeProviderTurnWithTransport(options, transport) {
    if (typeof transport !== "function") fail("test provider transport is invalid");
    return executeProviderTurnCore(options, transport, false);
  },
  executeProviderTurnWithTransportAndStore(options, transport) {
    if (typeof transport !== "function") fail("test provider transport is invalid");
    return executeProviderTurnCore(options, transport, true);
  },
});
