import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, lstat, open, readdir, rename } from "node:fs/promises";
import { dirname, join } from "node:path";

const RECEIPT_RE = /^tool-[0-9]{13}-[a-f0-9]{24}$/;
const PORTAL_SESSION_RE = /^agent:([a-z][a-z0-9-]{0,62}):portal-(conversation-[0-9]{13}-[a-f0-9]{12})$/;
const HASH_RE = /^[a-f0-9]{64}$/;
const MAX_RECEIPTS = 4096;
const BOUNDARY = "Content-free trusted portal tool receipt. It binds one host-side plugin invocation to an exact private OpenClaw portal session without retaining the session key, tool name, call ID, arguments, result content, paths, prompts, credentials, provider identifiers, or private source data. It grants no execution, provider, credential, approval, completion, retry, or policy authority.";

function canonical(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("portal tool audit contains a non-finite number");
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  throw new Error("portal tool audit contains a non-JSON value");
}

const sha256 = (value) => createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex");

function exactClassification(value) {
  const keys = ["autoEligible", "brokerReceiptRequired", "capability", "effect", "route"];
  if (
    !value || Array.isArray(value) || typeof value !== "object"
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(keys)
    || typeof value.capability !== "string" || !/^[a-z][a-z0-9-]{1,63}$/.test(value.capability)
    || typeof value.route !== "string" || !/^[a-z][a-z0-9-]{1,63}$/.test(value.route)
    || typeof value.effect !== "string" || !/^[a-z][a-z0-9-]{1,63}$/.test(value.effect)
    || typeof value.brokerReceiptRequired !== "boolean" || typeof value.autoEligible !== "boolean"
  ) throw new Error("portal tool audit classification is invalid");
  return { ...value };
}

function exactEvidence(value, classification, resultSha256) {
  const keys = ["action", "ambiguous", "approvalRequired", "autoWithinPolicy", "brokerKind", "correlationIdSha256", "externalEffectOccurred", "observation", "status"];
  if (
    !value || Array.isArray(value) || typeof value !== "object"
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(keys)
    || typeof value.brokerKind !== "string" || !/^[a-z][a-z0-9-]{1,63}$/.test(value.brokerKind)
    || typeof value.status !== "string" || !/^[a-z][a-z0-9-]{1,63}$/.test(value.status)
    || value.correlationIdSha256 !== null && (typeof value.correlationIdSha256 !== "string" || !HASH_RE.test(value.correlationIdSha256))
    || typeof value.approvalRequired !== "boolean"
    || value.externalEffectOccurred !== null && typeof value.externalEffectOccurred !== "boolean"
    || typeof value.autoWithinPolicy !== "boolean" || typeof value.ambiguous !== "boolean"
    || value.ambiguous && (value.externalEffectOccurred !== null || value.autoWithinPolicy)
    || value.approvalRequired && value.autoWithinPolicy
  ) throw new Error("portal tool audit evidence is invalid");
  if (value.observation !== null) {
    const observationKeys = ["observedAt", "privacyRoute", "sourceKind", "sourceSha256", "stale"];
    if (
      !value.observation || Array.isArray(value.observation) || typeof value.observation !== "object"
      || JSON.stringify(Object.keys(value.observation).sort()) !== JSON.stringify(observationKeys)
      || typeof value.observation.sourceKind !== "string" || !/^[a-z][a-z0-9-]{1,63}$/.test(value.observation.sourceKind)
      || typeof value.observation.observedAt !== "string" || !Number.isFinite(Date.parse(value.observation.observedAt))
      || typeof value.observation.sourceSha256 !== "string" || value.observation.sourceSha256 !== resultSha256
      || typeof value.observation.stale !== "boolean"
      || value.observation.privacyRoute !== classification.route
    ) throw new Error("portal tool audit observation evidence is invalid");
  }
  if (value.action !== null) {
    const actionKeys = ["actionJournalHeadSha256", "actionJournalState", "providerIdentifierSha256"];
    if (
      !value.action || Array.isArray(value.action) || typeof value.action !== "object"
      || JSON.stringify(Object.keys(value.action).sort()) !== JSON.stringify(actionKeys)
      || value.action.providerIdentifierSha256 !== null && (typeof value.action.providerIdentifierSha256 !== "string" || !HASH_RE.test(value.action.providerIdentifierSha256))
      || value.action.actionJournalHeadSha256 !== null && (typeof value.action.actionJournalHeadSha256 !== "string" || !HASH_RE.test(value.action.actionJournalHeadSha256))
      || value.action.actionJournalState !== null && (typeof value.action.actionJournalState !== "string" || !/^[a-z][a-z0-9-]{1,63}$/.test(value.action.actionJournalState))
      || value.action.actionJournalState === "succeeded" && (!value.action.providerIdentifierSha256 || !value.action.actionJournalHeadSha256)
    ) throw new Error("portal tool audit action evidence is invalid");
  }
  return { ...value };
}

async function ensurePrivateDirectory(path) {
  await mkdir(path, { recursive: true, mode: 0o700 });
  const info = await lstat(path);
  if (!info.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.mode & 0o077) !== 0) {
    throw new Error("portal tool audit directory is unsafe");
  }
  const entries = await readdir(path);
  if (entries.length >= MAX_RECEIPTS || entries.some((name) => !/^tool-[0-9]{13}-[a-f0-9]{24}\.json$/.test(name) && !/^\.tool-[0-9]{13}-[a-f0-9]{24}\.[a-f0-9]{16}\.tmp$/.test(name))) {
    throw new Error("portal tool audit directory is invalid or full");
  }
}

async function writeExclusive(path, value) {
  const flags = constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0);
  const handle = await open(path, flags, 0o600);
  try {
    await handle.writeFile(`${canonical(value)}\n`, "utf8");
    await handle.sync();
  } finally {
    await handle.close();
  }
}

function portalAuditContext(agentId, context, toolName, toolCallId, classification) {
  const sessionKey = context?.sessionKey;
  if (typeof sessionKey !== "string" || !sessionKey.includes(":portal-")) return null;
  const match = PORTAL_SESSION_RE.exec(sessionKey);
  if (!match || match[1] !== agentId || context.agentId !== agentId) throw new Error("portal tool audit session identity is invalid");
  if (typeof toolName !== "string" || !/^pixel_[a-z0-9_]{2,63}$/.test(toolName)) throw new Error("portal tool audit tool name is invalid");
  if (typeof toolCallId !== "string" || !/^[\x21-\x7e]{1,256}$/.test(toolCallId)) throw new Error("portal tool audit call identity is invalid");
  const state = process.env.OPENCLAW_STATE_DIR;
  const configured = process.env.PIXEL_CHAT_AUDIT_DIR;
  const root = configured || (typeof state === "string" && state.startsWith("/") ? join(state, "pixel-chat-audit") : null);
  if (!root) throw new Error("portal tool audit directory is not configured");
  const sessionKeySha256 = sha256(sessionKey);
  return {
    root: join(root, sessionKeySha256), sessionKeySha256,
    sessionIdSha256: typeof context.sessionId === "string" && context.sessionId ? sha256(context.sessionId) : null,
    toolCallIdSha256: sha256(toolCallId), toolNameSha256: sha256(toolName),
    classification: exactClassification(classification),
    isolation: {
      sandboxed: typeof context.sandboxed === "boolean" ? context.sandboxed : null,
      workspaceOnly: typeof context.fsPolicy?.workspaceOnly === "boolean" ? context.fsPolicy.workspaceOnly : null,
    },
  };
}

function commonReceipt(audit, receiptId, startedAt) {
  return {
    schemaVersion: 1, operation: "pixel-portal-tool-receipt", receiptId,
    sessionKeySha256: audit.sessionKeySha256, sessionIdSha256: audit.sessionIdSha256,
    toolCallIdSha256: audit.toolCallIdSha256, toolNameSha256: audit.toolNameSha256,
    classification: audit.classification, isolation: audit.isolation, startedAt,
    privacy: {
      sessionKeyExposed: false, toolNameExposed: false, callIdExposed: false,
      argumentsExposed: false, resultExposed: false, pathsExposed: false,
      promptsExposed: false, credentialsExposed: false, providerIdentifiersExposed: false,
    },
    authority: {
      grantsExecution: false, grantsProviderCall: false, grantsCredentialUse: false,
      grantsApproval: false, grantsExternalEffect: false, grantsCompletion: false,
      grantsRetry: false, grantsPolicyMutation: false,
    },
    boundary: BOUNDARY,
  };
}

async function settle(path, started, state, outcome) {
  const settled = {
    ...started, state, finishedAt: new Date().toISOString(),
    startedReceiptSha256: sha256(started), outcome,
  };
  const temporary = join(dirname(path), `.${started.receiptId}.${randomBytes(8).toString("hex")}.tmp`);
  await writeExclusive(temporary, settled);
  await rename(temporary, path);
  return settled;
}

export async function withPortalToolAudit({ agentId, context, toolName, toolCallId, classification, execute, summarize }) {
  if (typeof execute !== "function" || typeof summarize !== "function") throw new Error("portal tool audit callback is invalid");
  const audit = portalAuditContext(agentId, context, toolName, toolCallId, classification);
  if (!audit) return execute();
  await ensurePrivateDirectory(audit.root);
  const receiptId = `tool-${Date.now()}-${randomBytes(12).toString("hex")}`;
  if (!RECEIPT_RE.test(receiptId)) throw new Error("portal tool audit receipt identity is invalid");
  const started = {
    ...commonReceipt(audit, receiptId, new Date().toISOString()),
    state: "started", finishedAt: null, startedReceiptSha256: null, outcome: null,
  };
  const path = join(audit.root, `${receiptId}.json`);
  await writeExclusive(path, started);
  try {
    const value = await execute();
    const resultSha256 = sha256(value);
    const evidence = exactEvidence(summarize(value), audit.classification, resultSha256);
    await settle(path, started, "succeeded", {
      resultSha256, errorSha256: null, evidence,
    });
    return value;
  } catch (error) {
    const evidence = {
      brokerKind: classification.brokerReceiptRequired ? "broker" : "local-tool",
      status: "failed-ambiguous", correlationIdSha256: null, approvalRequired: false,
      externalEffectOccurred: null, autoWithinPolicy: false, ambiguous: true,
      observation: null, action: null,
    };
    try {
      await settle(path, started, "failed", {
        resultSha256: null,
        errorSha256: sha256(error instanceof Error ? `${error.name}:${error.message}` : String(error)),
        evidence,
      });
    } catch {
      // The durable started receipt intentionally remains as ambiguous evidence.
    }
    throw error;
  }
}

export { BOUNDARY as CHAT_TOOL_AUDIT_BOUNDARY, canonical, sha256 };
