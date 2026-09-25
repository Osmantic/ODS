import { constants, readFileSync } from "node:fs";
import { createHash, randomBytes } from "node:crypto";
import { lstat, mkdir, open, rm } from "node:fs/promises";
import { isAbsolute, join, parse, resolve } from "node:path";

import { assertWorkCodexProviderOutputText, compileWorkCodexPreview } from "./compiler.mjs";
import { verifyWorkCodexAuthenticationEvidence } from "./authentication.mjs";
import {
  canonical, validateWorkCodexAuthenticationConsumption, validateWorkCodexAuthenticationEvidence,
  validateWorkCodexAuthorization, validateWorkCodexCapsule, validateWorkCodexExecutionClaim,
  validateWorkCodexOutput, validateWorkCodexPlan, validateWorkCodexPolicy, validateWorkCodexRequest,
  validateWorkCodexResult,
} from "../../scripts/lib/work-contract.mjs";

const AUTH_SCHEMA = "https://osmantic.com/pixel/schemas/work-codex-authorization-v1.schema.json";
const AUTHENTICATION_CONSUMPTION_SCHEMA = "https://osmantic.com/pixel/schemas/work-codex-authentication-consumption-v1.schema.json";
const CLAIM_SCHEMA = "https://osmantic.com/pixel/schemas/work-codex-execution-claim-v1.schema.json";
const RESULT_SCHEMA = "https://osmantic.com/pixel/schemas/work-codex-result-v1.schema.json";
const OUTPUT_SCHEMA = JSON.parse(readFileSync(new URL("../../schemas/work-codex-output-v1.schema.json", import.meta.url), "utf8"));
const AUTH_BOUNDARY = "External approval for one exact sanitized Codex work plan. It is single-use, expires within fifteen minutes, and grants only the plan's one advisory provider turn; it grants no tools, local execution, merge, deployment, publication, message, purchase, production, policy, scope-expansion, verification, or completion authority.";
const CLAIM_BOUNDARY = "Durable pre-invocation claim. The authorization is consumed before adapter entry and can never be replayed; a crash or uncertain turn requires recovery evidence, never an automatic retry.";
const RESULT_BOUNDARY = "Content-free execution evidence only. Any stored advisory output is private and untrusted; success proves schema and boundary validation, not correctness, local verification, completion, or authority.";
const NO_AUTHORITY = Object.freeze({ tools: false, localExecution: false, merge: false, deployment: false, publication: false, message: false, purchase: false, production: false, policyMutation: false, scopeExpansion: false, verification: false, completion: false });
const HEX_64 = /^[a-f0-9]{64}$/u, SUFFIX = /^[a-f0-9]{12}$/u;
const AUTH_ID = /^workcodexauth-[0-9]{13}-[a-f0-9]{12}$/u;

export class WorkCodexExecutionError extends Error {}
function fail(message) { throw new WorkCodexExecutionError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function iso(value) { return value.toISOString().replace(/\.000Z$/u, "Z"); }
function schema(label, errors) { if (errors.length) fail(`${label} failed validation: ${errors.join("; ")}`); }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} has unsupported fields`);
}
function frozen(value) {
  if (value && typeof value === "object") { for (const child of Object.values(value)) frozen(child); Object.freeze(value); }
  return value;
}

async function privateDirectory(path, label, create = false) {
  if (create) await mkdir(path, { mode: 0o700, recursive: false }).catch((error) => { if (error.code !== "EEXIST") throw error; });
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink()) fail(`${label} must be a real private directory`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-only`);
  return path;
}

async function writeNew(path, value, label) {
  const encoded = Buffer.from(`${canonical(value)}\n`, "utf8");
  const handle = await open(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600).catch((error) => {
    fail(`${label} could not be created once: ${error.code ?? "open-failed"}`);
  });
  try { await handle.writeFile(encoded); await handle.sync(); }
  catch (error) { await handle.close().catch(() => {}); await rm(path, { force: true }).catch(() => {}); fail(`${label} could not be written durably: ${error.code ?? "write-failed"}`); }
  await handle.close();
}

async function readPrivate(path, maximumBytes, label) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size < 3 || info.size > maximumBytes) fail(`${label} must be a bounded single-link regular file`);
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o077) !== 0)) fail(`${label} must be owner-only`);
  const handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)).catch((error) => fail(`${label} could not be opened safely: ${error.code ?? "open-failed"}`));
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.nlink !== 1 || opened.size !== info.size) fail(`${label} changed during validation`);
    const bytes = await handle.readFile();
    if (bytes.includes(0)) fail(`${label} contains a NUL byte`);
    let text, value;
    try { text = new TextDecoder("utf-8", { fatal: true }).decode(bytes); value = JSON.parse(text); }
    catch (error) { fail(`${label} is not strict UTF-8 JSON: ${error.message}`); }
    if (text !== `${canonical(value)}\n`) fail(`${label} is not canonical JSON`);
    return value;
  } finally { await handle.close(); }
}

async function roots(root) {
  if (typeof root !== "string" || !isAbsolute(root)) fail("Codex work store root must be an absolute path");
  root = resolve(root);
  if (root === parse(root).root) fail("Codex work store root cannot be a filesystem root");
  await mkdir(root, { mode: 0o700, recursive: true }); await privateDirectory(root, "Codex work store");
  const result = { plans: join(root, "plans"), authorizations: join(root, "authorizations"), authentication: join(root, "authentication"), claims: join(root, "claims") };
  for (const [name, path] of Object.entries(result)) await privateDirectory(path, `Codex work ${name}`, true);
  return result;
}

function validateMapping(mapping, request, plan) {
  exactKeys(mapping, ["schemaVersion", "commitmentNonce", "documents", "entries"], "private replacement map");
  if (mapping.schemaVersion !== 1 || !HEX_64.test(mapping.commitmentNonce) || !Array.isArray(mapping.documents) || !Array.isArray(mapping.entries)) fail("private replacement map identity is invalid");
  if (mapping.documents.length !== request.documents.length || mapping.entries.length !== plan.dlp.placeholderCount || mapping.entries.length > 4096) fail("private replacement map counts differ from the exact request or plan");
  const expectedDocuments = request.documents.map((document, index) => ({ syntheticId: `DOC_${String(index + 1).padStart(3, "0")}`, originalId: document.documentId }));
  if (canonical(mapping.documents) !== canonical(expectedDocuments)) fail("private document mapping differs from the exact request");
  const capsuleText = canonical(plan.capsule), placeholders = new Set();
  for (const entry of mapping.entries) {
    exactKeys(entry, ["placeholder", "kind", "original"], "private replacement entry");
    const match = /^<PIXELWORK_([A-Z]+)_[0-9]{3}>$/u.exec(entry.placeholder);
    if (!match || match[1] !== entry.kind || typeof entry.original !== "string" || entry.original.length === 0 || Buffer.byteLength(entry.original, "utf8") > 8192 || entry.original.includes("PIXELWORK_")) fail("private replacement entry is invalid");
    if (placeholders.has(entry.placeholder) || !capsuleText.includes(entry.placeholder)) fail("private replacement entry is duplicate or absent from the exact capsule");
    placeholders.add(entry.placeholder);
  }
  if (sha(mapping) !== plan.dlp.mappingSha256) fail("private replacement map commitment differs from the plan");
  return placeholders;
}

function validateBundle(bundle) {
  exactKeys(bundle, ["schemaVersion", "request", "policy", "plan", "privateMapping"], "private preview bundle");
  if (bundle.schemaVersion !== 1) fail("private preview bundle version is unsupported");
  schema("Codex work request", validateWorkCodexRequest(bundle.request)); schema("Codex work policy", validateWorkCodexPolicy(bundle.policy)); schema("Codex work plan", validateWorkCodexPlan(bundle.plan));
  if (sha(bundle.request) !== bundle.plan.requestSha256 || sha(bundle.policy) !== bundle.plan.policySha256 || sha(bundle.plan.capsule) !== bundle.plan.capsuleSha256) fail("private preview bundle hashes differ from the plan");
  validateMapping(bundle.privateMapping, bundle.request, bundle.plan);
  let compiled;
  try {
    compiled = compileWorkCodexPreview({
      request: bundle.request, policy: bundle.policy, now: new Date(bundle.plan.createdAt),
      suffix: bundle.plan.planId.split("-").at(-1), mappingNonce: bundle.privateMapping.commitmentNonce,
    });
  } catch (error) { fail(`private preview bundle cannot be reproduced: ${error.message}`); }
  if (canonical(compiled.plan) !== canonical(bundle.plan) || canonical(compiled.privateMapping) !== canonical(bundle.privateMapping)) fail("private preview bundle differs from exact recompilation");
  return bundle;
}

export async function initializeWorkCodexStore({ root }) { await roots(root); }

export async function storeWorkCodexPreview({ root, request, policy, plan, privateMapping }) {
  const paths = await roots(root), bundle = validateBundle({ schemaVersion: 1, request, policy, plan, privateMapping });
  const directory = join(paths.plans, plan.planId);
  await mkdir(directory, { mode: 0o700 }).catch((error) => fail(`Codex work plan was already stored or could not be reserved: ${error.code ?? "mkdir-failed"}`));
  await privateDirectory(directory, "Codex work plan directory");
  await writeNew(join(directory, "bundle.json"), bundle, "private preview bundle");
  return { planId: plan.planId, planSha256: sha(plan), stored: true };
}

async function loadBundle(paths, planId) {
  if (!/^workcodexplan-[0-9]{13}-[a-f0-9]{12}$/u.test(planId)) fail("Codex work plan identity is invalid");
  await privateDirectory(join(paths.plans, planId), "Codex work plan directory");
  return validateBundle(await readPrivate(join(paths.plans, planId, "bundle.json"), 8 * 1024 * 1024, "private preview bundle"));
}

export function buildWorkCodexAuthorization({ plan, confirmation, authorizationSource = "test-mock", now = new Date(), expiresAt = new Date(now.getTime() + 10 * 60000), suffix = randomBytes(6).toString("hex") }) {
  schema("Codex work plan", validateWorkCodexPlan(plan));
  exactKeys(confirmation, ["planInspected", "dataOwnerConfirmed", "billingAcknowledgment", "externalHumanAuthentication", "authenticationEvidenceSha256"], "external Codex work confirmation");
  if (!(now instanceof Date) || Number.isNaN(now.getTime()) || !(expiresAt instanceof Date) || Number.isNaN(expiresAt.getTime()) || !SUFFIX.test(suffix) || !HEX_64.test(confirmation.authenticationEvidenceSha256) || !["test-mock", "external-signed-mfa"].includes(authorizationSource)) fail("Codex work authorization inputs are invalid");
  if (Date.parse(plan.expiresAt) <= now.getTime()) fail("authorization cannot be created for an expired plan");
  if (expiresAt.getTime() > Date.parse(plan.expiresAt)) fail("authorization cannot outlive the exact plan");
  const authorization = {
    $schema: AUTH_SCHEMA, schemaVersion: 1, authorizationId: `workcodexauth-${String(now.getTime()).padStart(13, "0")}-${suffix}`,
    createdAt: iso(now), expiresAt: iso(expiresAt), planId: plan.planId, planSha256: sha(plan), requestSha256: plan.requestSha256,
    policySha256: plan.policySha256, capsuleSha256: plan.capsuleSha256, outputSchemaSha256: plan.outputSchemaSha256, jobId: plan.jobId,
    checkpointSha256: plan.checkpointSha256, provider: { authMode: plan.provider.authMode, model: plan.provider.model, billingBoundary: plan.provider.billingBoundary, transportSha256: plan.provider.transportSha256 },
    confirmation: structuredClone(confirmation), authorizationSource,
    singleUse: true, providerInvoked: false, boundary: AUTH_BOUNDARY,
  };
  schema("Codex work authorization", validateWorkCodexAuthorization(authorization)); return authorization;
}

function authorizationMatchesPlan(authorization, plan) {
  const expected = {
    planId: plan.planId, planSha256: sha(plan), requestSha256: plan.requestSha256, policySha256: plan.policySha256,
    capsuleSha256: plan.capsuleSha256, outputSchemaSha256: plan.outputSchemaSha256, jobId: plan.jobId, checkpointSha256: plan.checkpointSha256,
    provider: { authMode: plan.provider.authMode, model: plan.provider.model, billingBoundary: plan.provider.billingBoundary, transportSha256: plan.provider.transportSha256 },
  };
  for (const [key, value] of Object.entries(expected)) if (canonical(authorization[key]) !== canonical(value)) fail(`authorization ${key} differs from the exact plan`);
}

export async function storeWorkCodexAuthorization({ root, authorization, allowMockAuthorization = false }) {
  if (!allowMockAuthorization) fail("production Codex work authorization requires a verified external MFA assertion");
  const paths = await roots(root); schema("Codex work authorization", validateWorkCodexAuthorization(authorization));
  if (authorization.authorizationSource !== "test-mock") fail("mock Codex work storage accepts only a test-mock authorization");
  const bundle = await loadBundle(paths, authorization.planId); authorizationMatchesPlan(authorization, bundle.plan);
  await writeNew(join(paths.authorizations, `${authorization.authorizationId}.json`), authorization, "Codex work authorization");
  return { authorizationId: authorization.authorizationId, authorizationSha256: sha(authorization), stored: true };
}

export async function storeVerifiedWorkCodexAuthorization({ root, authenticationEvidence, trustedPublicKey, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  schema("Codex work authentication evidence", validateWorkCodexAuthenticationEvidence(authenticationEvidence));
  if (!(now instanceof Date) || Number.isNaN(now.getTime()) || !SUFFIX.test(suffix)) fail("verified Codex work authorization inputs are invalid");
  const paths = await roots(root), bundle = await loadBundle(paths, authenticationEvidence.planId), { plan, policy } = bundle;
  let verified;
  try { verified = verifyWorkCodexAuthenticationEvidence({ evidence: authenticationEvidence, plan, policy, trustedPublicKey, now }); }
  catch (error) { fail(`external Codex work authentication failed: ${error.message}`); }
  const expiresAt = new Date(Math.min(Date.parse(authenticationEvidence.expiresAt), Date.parse(plan.expiresAt), now.getTime() + 10 * 60 * 1000));
  const authorization = buildWorkCodexAuthorization({ plan, confirmation: verified.confirmation, authorizationSource: "external-signed-mfa", now, expiresAt, suffix });
  const consumption = {
    $schema: AUTHENTICATION_CONSUMPTION_SCHEMA, schemaVersion: 1, evidenceId: verified.evidenceId, evidenceSha256: verified.evidenceSha256,
    consumedAt: iso(now), authorizationId: authorization.authorizationId, planId: plan.planId, planSha256: sha(plan), secondFactor: verified.secondFactor,
    singleUseConsumed: true, credentialsStored: false,
    boundary: "Content-free one-use MFA evidence tombstone. It stores no password, one-time code, session token, signature, identity claim, provider credential, prompt, capsule, or output and grants only creation of the bound advisory authorization.",
  };
  schema("Codex work authentication consumption", validateWorkCodexAuthenticationConsumption(consumption));
  await writeNew(join(paths.authentication, `${verified.evidenceId}.json`), consumption, "Codex work authentication consumption");
  await writeNew(join(paths.authorizations, `${authorization.authorizationId}.json`), authorization, "Codex work authorization");
  return { authorizationId: authorization.authorizationId, authorizationSha256: sha(authorization), evidenceSha256: verified.evidenceSha256, stored: true };
}

export async function readWorkCodexAuthenticationConsumption({ root, evidenceId }) {
  if (!/^workcodexauthn-[0-9]{13}-[a-f0-9]{12}$/u.test(evidenceId ?? "")) fail("Codex work authentication evidence identity is invalid");
  const paths = await roots(root), consumption = await readPrivate(join(paths.authentication, `${evidenceId}.json`), 128 * 1024, "Codex work authentication consumption");
  schema("Codex work authentication consumption", validateWorkCodexAuthenticationConsumption(consumption));
  if (consumption.evidenceId !== evidenceId) fail("Codex work authentication consumption identity is mismatched");
  return consumption;
}

function rehydrate(value, replacementByPlaceholder, documentBySynthetic) {
  if (typeof value === "string") {
    let result = value;
    for (const [placeholder, original] of replacementByPlaceholder) result = result.replaceAll(placeholder, original);
    return result;
  }
  if (Array.isArray(value)) return value.map((child) => rehydrate(child, replacementByPlaceholder, documentBySynthetic));
  if (value && typeof value === "object") {
    const result = Object.fromEntries(Object.entries(value).map(([key, child]) => [key, rehydrate(child, replacementByPlaceholder, documentBySynthetic)]));
    if (Array.isArray(result.proposals)) for (const proposal of result.proposals) proposal.targetDocumentId = documentBySynthetic.get(proposal.targetDocumentId) ?? proposal.targetDocumentId;
    return result;
  }
  return value;
}

function untrustedOutputStrings(output) {
  return [
    output.summary,
    ...output.findings.flatMap((finding) => [finding.title, finding.evidence, finding.recommendation]),
    ...output.proposals.flatMap((proposal) => [proposal.rationale, proposal.content]),
    ...output.verificationSuggestions, ...output.unresolvedRisks,
  ];
}

function receiptBase({ authorization, claim, plan, now, suffix, status, source, externalState, usage = null }) {
  const result = {
    $schema: RESULT_SCHEMA, schemaVersion: 1, resultId: `workcodexresult-${String(now.getTime()).padStart(13, "0")}-${suffix}`, createdAt: iso(now),
    authorizationId: authorization.authorizationId, authorizationSha256: sha(authorization), claimSha256: sha(claim), planId: plan.planId, planSha256: sha(plan), capsuleSha256: plan.capsuleSha256,
    status, invocation: { source, adapterInvoked: true, externalState, attempts: 1 },
    usage: usage ?? { reported: false, inputTokens: null, outputTokens: null },
    output: { storedPrivately: false, sanitizedSha256: null, rehydratedSha256: null, referencedPlaceholders: 0 }, authority: { ...NO_AUTHORITY }, boundary: RESULT_BOUNDARY,
  };
  return result;
}

async function finish(directory, receipt, outputBundle = null) {
  schema("Codex work result", validateWorkCodexResult(receipt));
  if (outputBundle) await writeNew(join(directory, "output.json"), outputBundle, "private Codex work output");
  await writeNew(join(directory, "result.json"), receipt, "content-free Codex work result");
  return receipt;
}

export async function executeWorkCodexAuthorization({ root, authorizationId, adapter, allowMock = false, allowIsolatedCodex = false, mockTimeoutMilliseconds = null, adapterTimeoutMilliseconds = null, now = new Date(), claimSuffix = randomBytes(6).toString("hex"), resultSuffix = randomBytes(6).toString("hex"), resultNow = null }) {
  const isMock = allowMock && adapter?.source === "mock" && typeof adapter.run === "function";
  const isIsolated = allowIsolatedCodex && adapter?.source === "codex-cli-isolated" && typeof adapter.run === "function";
  if (isMock === isIsolated) fail("Codex work execution requires exactly one explicitly enabled adapter boundary");
  if (!AUTH_ID.test(authorizationId)) fail("Codex work authorization identity is invalid");
  if (!(now instanceof Date) || Number.isNaN(now.getTime()) || (resultNow !== null && (!(resultNow instanceof Date) || Number.isNaN(resultNow.getTime()) || resultNow < now)) || !SUFFIX.test(claimSuffix) || !SUFFIX.test(resultSuffix)) fail("Codex work execution inputs are invalid");
  const paths = await roots(root), authorization = await readPrivate(join(paths.authorizations, `${authorizationId}.json`), 128 * 1024, "Codex work authorization");
  schema("Codex work authorization", validateWorkCodexAuthorization(authorization));
  if (authorization.authorizationId !== authorizationId || Date.parse(authorization.expiresAt) <= now.getTime()) fail("Codex work authorization is mismatched or expired");
  const expectedAuthorizationSource = isMock ? "test-mock" : "external-signed-mfa";
  if (authorization.authorizationSource !== expectedAuthorizationSource) fail("Codex work authorization source cannot enter the selected adapter boundary");
  const bundle = await loadBundle(paths, authorization.planId), { plan } = bundle; authorizationMatchesPlan(authorization, plan);
  if (Date.parse(plan.expiresAt) <= now.getTime()) fail("Codex work plan is expired");
  const timeoutMilliseconds = adapterTimeoutMilliseconds ?? mockTimeoutMilliseconds ?? plan.limits.timeoutSeconds * 1000;
  if (!Number.isInteger(timeoutMilliseconds) || timeoutMilliseconds < 1 || timeoutMilliseconds > plan.limits.timeoutSeconds * 1000) fail("Codex adapter timeout must be a positive reduction of the exact plan limit");
  const executionSource = isMock ? "mock" : "codex-cli", expectedExternalState = isMock ? "not-invoked" : "invoked-once", failedExternalState = isMock ? "not-invoked" : "uncertain";
  const placeholders = validateMapping(bundle.privateMapping, bundle.request, plan);
  const claim = {
    $schema: CLAIM_SCHEMA, schemaVersion: 1, claimId: `workcodexclaim-${String(now.getTime()).padStart(13, "0")}-${claimSuffix}`, claimedAt: iso(now), authorizationId,
    authorizationSha256: sha(authorization), planId: plan.planId, planSha256: sha(plan), capsuleSha256: plan.capsuleSha256,
    executionSource, singleUseConsumed: true, externalProviderTurnAuthorized: !isMock, authority: { ...NO_AUTHORITY }, boundary: CLAIM_BOUNDARY,
  };
  schema("Codex work execution claim", validateWorkCodexExecutionClaim(claim));
  const claimDirectory = join(paths.claims, authorizationId);
  await mkdir(claimDirectory, { mode: 0o700 }).catch((error) => fail(`Codex work authorization is already consumed or could not be claimed: ${error.code ?? "mkdir-failed"}`));
  await writeNew(join(claimDirectory, "claim.json"), claim, "Codex work execution claim");
  const resultTime = () => resultNow ?? new Date(Math.max(Date.now(), now.getTime()));

  let response;
  let validated;
  const abortController = new AbortController(); let timeout;
  try {
    const payload = frozen(structuredClone({ capsule: plan.capsule, outputSchema: OUTPUT_SCHEMA, provider: plan.provider, limits: plan.limits, planSha256: sha(plan) }));
    const adapterPromise = Promise.resolve().then(() => adapter.run(payload, { signal: abortController.signal }));
    if (isIsolated) {
      const settled = adapterPromise.then((value) => ({ kind: "result", value }), (error) => ({ kind: "error", error }));
      const first = await Promise.race([settled, new Promise((resolvePromise) => { timeout = setTimeout(() => { abortController.abort(); resolvePromise({ kind: "timeout" }); }, timeoutMilliseconds); })]);
      if (first.kind === "timeout") {
        let grace;
        const stopped = await Promise.race([settled.then(() => true), new Promise((resolvePromise) => { grace = setTimeout(() => resolvePromise(false), 15000); })]);
        if (grace) clearTimeout(grace);
        if (!stopped) throw new WorkCodexExecutionError("isolated Codex adapter cleanup did not settle after timeout");
        throw new WorkCodexExecutionError("Codex adapter timed out");
      }
      if (first.kind === "error") throw first.error;
      response = first.value;
    } else {
      response = await Promise.race([adapterPromise, new Promise((_resolve, reject) => { timeout = setTimeout(() => { abortController.abort(); reject(new WorkCodexExecutionError("Codex adapter timed out")); }, timeoutMilliseconds); })]);
    }
  } catch {
    return { receipt: await finish(claimDirectory, receiptBase({ authorization, claim, plan, now: resultTime(), suffix: resultSuffix, status: "provider-failed", source: executionSource, externalState: failedExternalState })), output: null };
  } finally { if (timeout) clearTimeout(timeout); }
  let observedUsage = null;
  try {
    exactKeys(response, ["outputText", "usage", "externalState"], "Codex adapter response");
    if (response.externalState !== expectedExternalState) fail("Codex adapter external state differs from its selected boundary");
    exactKeys(response.usage, ["inputTokens", "outputTokens"], "Codex usage");
    if (!Number.isInteger(response.usage.inputTokens) || response.usage.inputTokens < 0 || !Number.isInteger(response.usage.outputTokens) || response.usage.outputTokens < 0) fail("Codex usage is invalid");
    const usage = { reported: true, inputTokens: response.usage.inputTokens, outputTokens: response.usage.outputTokens };
    observedUsage = usage;
    if (usage.inputTokens > plan.limits.maxInputTokens || usage.outputTokens > plan.limits.maxOutputTokens) {
      validated = { budgetExceeded: true, usage };
    } else {
      const bytes = Buffer.isBuffer(response.outputText) ? response.outputText : Buffer.from(response.outputText, "utf8");
      if (!(typeof response.outputText === "string" || Buffer.isBuffer(response.outputText)) || bytes.length < 2 || bytes.length > plan.limits.maxOutputBytes || bytes.includes(0)) fail("Codex output bytes are invalid or over limit");
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      let output; try { output = JSON.parse(text); } catch { fail("Codex output is not strict JSON"); }
      if (text !== canonical(output)) fail("Codex output is not exact canonical JSON");
      schema("Codex work provider output", validateWorkCodexOutput(output));
      for (const value of untrustedOutputStrings(output)) assertWorkCodexProviderOutputText(value, placeholders);
      if (output.taskClass !== plan.taskClass) fail("Codex work output task differs from the exact plan");
      const documentIds = new Set(plan.capsule.documents.map((document) => document.documentId));
      if (output.proposals.some((proposal) => !documentIds.has(proposal.targetDocumentId))) fail("Codex work output targets an unknown synthetic document");
      const referenced = [...new Set([...text.matchAll(/<PIXELWORK_[A-Z]+_[0-9]{3}>/gu)].map((match) => match[0]))];
      const replacements = new Map(bundle.privateMapping.entries.map((entry) => [entry.placeholder, entry.original]));
      const documents = new Map(bundle.privateMapping.documents.map((entry) => [entry.syntheticId, entry.originalId]));
      const rehydrated = rehydrate(output, replacements, documents);
      if (Buffer.byteLength(canonical(rehydrated), "utf8") > Math.min(8 * plan.limits.maxOutputBytes, 8 * 1024 * 1024)) fail("rehydrated Codex work output exceeds the private byte ceiling");
      validated = { output, rehydrated, referenced, usage };
    }
  } catch {
    const receipt = receiptBase({ authorization, claim, plan, now: resultTime(), suffix: resultSuffix, status: "invalid-output", source: executionSource, externalState: expectedExternalState, usage: observedUsage });
    return { receipt: await finish(claimDirectory, receipt), output: null };
  }
  if (validated.budgetExceeded) {
    const receipt = receiptBase({ authorization, claim, plan, now: resultTime(), suffix: resultSuffix, status: "budget-exceeded", source: executionSource, externalState: expectedExternalState, usage: validated.usage });
    return { receipt: await finish(claimDirectory, receipt), output: null };
  }
  const receipt = receiptBase({ authorization, claim, plan, now: resultTime(), suffix: resultSuffix, status: "succeeded", source: executionSource, externalState: expectedExternalState, usage: validated.usage });
  receipt.output = { storedPrivately: true, sanitizedSha256: sha(validated.output), rehydratedSha256: sha(validated.rehydrated), referencedPlaceholders: validated.referenced.length };
  schema("Codex work result", validateWorkCodexResult(receipt));
  await finish(claimDirectory, receipt, { schemaVersion: 1, planId: plan.planId, sanitizedOutput: validated.output, rehydratedOutput: validated.rehydrated });
  return { receipt, output: validated.rehydrated };
}

export async function readWorkCodexResult({ root, authorizationId }) {
  if (!AUTH_ID.test(authorizationId)) fail("Codex work authorization identity is invalid");
  const paths = await roots(root);
  const directory = join(paths.claims, authorizationId); await privateDirectory(directory, "Codex work claim directory");
  const receipt = await readPrivate(join(directory, "result.json"), 256 * 1024, "content-free Codex work result");
  schema("Codex work result", validateWorkCodexResult(receipt)); return receipt;
}
