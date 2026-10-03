import { createHash, randomBytes } from "node:crypto";
import { isAbsolute, parse, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { inspectWorkProviderCredentialCustody } from "./credential-custody.mjs";
import { readOwnerPrivateWorkProviderPolicy } from "./dev-proxy-cli.mjs";
import { parseBoundWorkProviderPrivatePolicy } from "./egress-proxy.mjs";
import { executeMoonshotChatTurn, MoonshotWorkProviderTransportError } from "./moonshot-transport.mjs";
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

const SHA = /^[a-f0-9]{64}$/u;
const EXPECTED = "PIXEL_K3_SMOKE_OK";

export class MoonshotSmokeCliError extends Error {}
function fail(message) { throw new MoonshotSmokeCliError(message); }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }
function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) === parse(resolve(value)).root) fail(`${label} is invalid`);
  return resolve(value);
}
function argumentsFor(argv) {
  if (!Array.isArray(argv) || argv.length !== 8) fail("usage: moonshot-smoke-cli.mjs --policy PATH --sha256 HEX --credential PATH --ledger-root PATH");
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index], value = argv[index + 1];
    if (!["--credential", "--ledger-root", "--policy", "--sha256"].includes(name) || values.has(name)) fail("Moonshot smoke arguments are invalid");
    values.set(name, value);
  }
  const sha256 = values.get("--sha256");
  if (!SHA.test(sha256 ?? "")) fail("Moonshot smoke policy SHA-256 is invalid");
  return Object.freeze({
    policyPath: absolutePath(values.get("--policy"), "Moonshot smoke policy path"),
    credentialPath: absolutePath(values.get("--credential"), "Moonshot smoke credential path"),
    ledgerRoot: absolutePath(values.get("--ledger-root"), "Moonshot smoke ledger root"),
    sha256,
  });
}
function suffix() { return randomBytes(6).toString("hex"); }
function fixedRequest(model) {
  return Object.freeze({
    schemaVersion: 1,
    model,
    messages: Object.freeze([{ role: "user", content: `Return exactly ${EXPECTED} and nothing else.` }]),
    maxOutputTokens: 256,
    reasoningEffort: "low",
  });
}
async function replace(root, stored, ledger) {
  const next = await replaceWorkProviderRunLedger({ root, expectedSha256: stored.sha256, ledger });
  return Object.freeze({ runId: next.runId, sha256: next.sha256 });
}

export async function runMoonshotSmoke(argv = process.argv.slice(2), { execute = executeMoonshotChatTurn, now = () => new Date(), proxyHost = "127.0.0.1" } = {}) {
  if (!["127.0.0.1", "pixel-provider-egress"].includes(proxyHost)) fail("Moonshot smoke proxy host is invalid");
  const options = argumentsFor(argv);
  const parsed = parseBoundWorkProviderPrivatePolicy(await readOwnerPrivateWorkProviderPolicy(options.policyPath), options.sha256);
  const { policy, resolvedProvider } = parsed;
  const custody = await inspectWorkProviderCredentialCustody({
    resolvedProvider, policy, credentialPath: options.credentialPath, now: now(), suffix: suffix(),
  });
  const root = await initializeWorkProviderRunStore({ root: options.ledgerRoot });
  const smokeModel = resolvedProvider.profile.defaultModel;
  const smokeRequest = fixedRequest(smokeModel);
  const binding = createConnectivitySmokeBinding({ purpose: "moonshot-connectivity-smoke-v1", requestSha256: sha(smokeRequest), model: smokeModel });
  let ledger = createWorkProviderRunLedger({
    resolvedProvider, policy, taskId: "pixel-k3-moonshot-smoke", model: smokeModel, binding, now: now(), suffix: suffix(),
  });
  let stored = await storeNewWorkProviderRunLedger({ root, ledger });
  const idempotencyKey = providerIdempotencyKey({ runId: ledger.runId, turn: 1, purpose: "moonshot-connectivity-smoke-v1" });
  ledger = claimWorkProviderRequest({
    ledger, policy, resolvedProvider, idempotencyKey, inputSha256: sha(smokeRequest), estimatedInputTokens: 128, maxOutputTokens: 256,
    maxEstimatedCostMicros: 100000, now: now(),
  });
  stored = await replace(root, stored, ledger);
  let result;
  try {
    result = await execute({
      resolvedProvider, policy, credentialHandle: custody.handle, ledger, idempotencyKey,
      input: smokeRequest, proxyHost, proxyPort: 3128,
    });
  } catch (error) {
    const outcome = error instanceof MoonshotWorkProviderTransportError && error.outcome === "failed-known" ? "failed-known" : "uncertain";
    ledger = settleWorkProviderRequest({ ledger, policy, resolvedProvider, idempotencyKey, outcome, now: now() });
    stored = await replace(root, stored, ledger);
    if (outcome === "failed-known") {
      ledger = closeWorkProviderRun({ ledger, now: now() });
      await replace(root, stored, ledger);
      fail("Moonshot smoke failed with a known terminal provider outcome");
    }
    fail("Moonshot smoke outcome is uncertain; do not retry automatically and inspect the content-free run ledger");
  }
  ledger = settleWorkProviderRequest({
    ledger, policy, resolvedProvider, idempotencyKey, outcome: "succeeded", usage: result.assistant.usage,
    providerRequestId: result.providerRequestId, now: now(),
  });
  stored = await replace(root, stored, ledger);
  ledger = closeWorkProviderRun({ ledger, now: now() });
  stored = await replace(root, stored, ledger);
  const matched = result.assistant.finishReason === "stop" && result.assistant.message.content?.trim() === EXPECTED
    && !result.assistant.message.toolCalls?.length;
  return Object.freeze({
    schemaVersion: 1, status: matched ? "passed" : "provider-response-mismatch", runId: ledger.runId,
    providerId: ledger.providerId, model: ledger.model, inputTokens: ledger.totals.inputTokens,
    outputTokens: ledger.totals.outputTokens, providerContentIncluded: false, credentialIncluded: false,
    ledgerSha256: stored.sha256,
  });
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runMoonshotSmoke().then((receipt) => {
    process.stdout.write(`${JSON.stringify(receipt)}\n`);
    if (receipt.status !== "passed") process.exitCode = 1;
  }).catch(() => {
    process.stderr.write("Pixel Moonshot smoke failed closed. Inspect the content-free run ledger before any retry.\n");
    process.exitCode = 70;
  });
}
