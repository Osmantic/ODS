import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { canonical, sha256, withPortalToolAudit } from "../plugin/chat-audit.js";

const classification = {
  capability: "source-write", route: "authorized-provider", effect: "external-write-capable",
  brokerReceiptRequired: true, autoEligible: false,
};

function context() {
  return {
    agentId: "pixel", sessionKey: "agent:pixel:portal-conversation-1786697000000-a1b2c3d4e5f6",
    sessionId: "private-session-id", sandboxed: true, fsPolicy: { workspaceOnly: true },
  };
}

test("portal plugin audit retains only hashes and exact content-free broker outcome", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-chat-audit-"));
  t.after(async () => { await rm(root, { recursive: true, force: true }); });
  const previous = process.env.PIXEL_CHAT_AUDIT_DIR;
  process.env.PIXEL_CHAT_AUDIT_DIR = root;
  t.after(() => {
    if (previous === undefined) delete process.env.PIXEL_CHAT_AUDIT_DIR;
    else process.env.PIXEL_CHAT_AUDIT_DIR = previous;
  });
  const result = await withPortalToolAudit({
    agentId: "pixel", context: context(), toolName: "pixel_calendar_propose_update",
    toolCallId: "private-tool-call-id", classification,
    execute: async () => ({ details: { proposalId: "private-proposal-id", status: "applied", private: "PRIVATE RESULT" } }),
    summarize: (value) => ({
      brokerKind: "calendar-action", status: "applied-bounded-direct",
      correlationIdSha256: sha256(value.details.proposalId), approvalRequired: false,
      externalEffectOccurred: true, autoWithinPolicy: true, ambiguous: false,
      observation: null, action: null,
    }),
  });
  assert.equal(result.details.status, "applied");
  const sessionRoot = join(root, sha256(context().sessionKey));
  const names = await readdir(sessionRoot);
  assert.equal(names.length, 1);
  const encoded = await readFile(join(sessionRoot, names[0]), "utf8");
  const receipt = JSON.parse(encoded);
  assert.equal(receipt.state, "succeeded");
  assert.equal(receipt.classification.capability, "source-write");
  assert.equal(receipt.outcome.evidence.autoWithinPolicy, true);
  assert.equal(receipt.outcome.evidence.externalEffectOccurred, true);
  assert.match(receipt.startedReceiptSha256, /^[a-f0-9]{64}$/);
  for (const secret of [context().sessionKey, "private-session-id", "pixel_calendar_propose_update", "private-tool-call-id", "private-proposal-id", "PRIVATE RESULT"]) {
    assert.doesNotMatch(encoded, new RegExp(secret.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }
  assert.equal(encoded, `${canonical(receipt)}\n`);
});

test("failed broker invocation settles as ambiguous and a non-portal invocation writes nothing", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-chat-audit-failure-"));
  t.after(async () => { await rm(root, { recursive: true, force: true }); });
  const previous = process.env.PIXEL_CHAT_AUDIT_DIR;
  process.env.PIXEL_CHAT_AUDIT_DIR = root;
  t.after(() => {
    if (previous === undefined) delete process.env.PIXEL_CHAT_AUDIT_DIR;
    else process.env.PIXEL_CHAT_AUDIT_DIR = previous;
  });
  await assert.rejects(withPortalToolAudit({
    agentId: "pixel", context: context(), toolName: "pixel_calendar_propose_update",
    toolCallId: "failed-call", classification,
    execute: async () => { throw new Error("PRIVATE FAILURE"); }, summarize: () => { throw new Error("unreachable"); },
  }), /PRIVATE FAILURE/);
  const sessionRoot = join(root, sha256(context().sessionKey));
  const names = await readdir(sessionRoot);
  const encoded = await readFile(join(sessionRoot, names[0]), "utf8");
  const receipt = JSON.parse(encoded);
  assert.equal(receipt.state, "failed");
  assert.equal(receipt.outcome.evidence.ambiguous, true);
  assert.equal(receipt.outcome.evidence.externalEffectOccurred, null);
  assert.doesNotMatch(encoded, /PRIVATE FAILURE|failed-call|pixel_calendar_propose_update/);

  const plain = await withPortalToolAudit({
    agentId: "pixel", context: { agentId: "pixel", sessionKey: "agent:pixel:main" },
    toolName: "pixel_calendar_propose_update", toolCallId: "plain", classification,
    execute: async () => "plain-result", summarize: () => { throw new Error("unused"); },
  });
  assert.equal(plain, "plain-result");
  assert.equal((await readdir(root)).length, 1);
});

test("portal audit binds content-free source time and action-journal evidence to the exact result", async (t) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-chat-audit-provenance-"));
  t.after(async () => { await rm(root, { recursive: true, force: true }); });
  const previous = process.env.PIXEL_CHAT_AUDIT_DIR;
  process.env.PIXEL_CHAT_AUDIT_DIR = root;
  t.after(() => {
    if (previous === undefined) delete process.env.PIXEL_CHAT_AUDIT_DIR;
    else process.env.PIXEL_CHAT_AUDIT_DIR = previous;
  });
  const value = {
    details: {
      generatedAt: "2026-08-13T12:00:00.000Z", affectedEventId: "private-provider-event",
      actionJournalHeadSha256: "a".repeat(64), private: "PRIVATE SOURCE",
    },
  };
  await withPortalToolAudit({
    agentId: "pixel", context: context(), toolName: "pixel_calendar_propose_update",
    toolCallId: "provenance-call", classification,
    execute: async () => value,
    summarize: (result) => ({
      brokerKind: "calendar-action", status: "applied-bounded-direct",
      correlationIdSha256: "b".repeat(64), approvalRequired: false,
      externalEffectOccurred: true, autoWithinPolicy: true, ambiguous: false,
      observation: {
        sourceKind: "calendar-action", observedAt: result.details.generatedAt,
        sourceSha256: sha256(result), stale: false, privacyRoute: classification.route,
      },
      action: {
        providerIdentifierSha256: sha256(result.details.affectedEventId),
        actionJournalHeadSha256: result.details.actionJournalHeadSha256,
        actionJournalState: "succeeded",
      },
    }),
  });
  const sessionRoot = join(root, sha256(context().sessionKey));
  const receipt = JSON.parse(await readFile(join(sessionRoot, (await readdir(sessionRoot))[0]), "utf8"));
  assert.equal(receipt.outcome.evidence.observation.sourceSha256, receipt.outcome.resultSha256);
  assert.equal(receipt.outcome.evidence.action.actionJournalState, "succeeded");
  assert.doesNotMatch(JSON.stringify(receipt), /private-provider-event|PRIVATE SOURCE/u);

  await assert.rejects(withPortalToolAudit({
    agentId: "pixel", context: context(), toolName: "pixel_calendar_propose_update",
    toolCallId: "mismatched-provenance", classification,
    execute: async () => value,
    summarize: () => ({
      brokerKind: "calendar-action", status: "applied-bounded-direct",
      correlationIdSha256: "b".repeat(64), approvalRequired: false,
      externalEffectOccurred: true, autoWithinPolicy: true, ambiguous: false,
      observation: {
        sourceKind: "calendar-action", observedAt: "2026-08-13T12:00:00.000Z",
        sourceSha256: "c".repeat(64), stale: false, privacyRoute: classification.route,
      },
      action: null,
    }),
  }), /observation evidence/u);
});
