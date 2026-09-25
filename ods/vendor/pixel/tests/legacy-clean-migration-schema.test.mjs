import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { assertJsonSchema, validateJsonSchema } from "../scripts/lib/json-schema.mjs";

const schema = JSON.parse(await readFile(new URL("../schemas/legacy-clean-migration-v1.schema.json", import.meta.url), "utf8"));

const privacy = {
  pathsIncluded: false, hostIdentityIncluded: false, credentialsIncluded: false,
  signerIdentityIncluded: false, modelProviderIdentityIncluded: false, userContentIncluded: false,
};
const commit = "a".repeat(40);
const tree = "b".repeat(40);

const plan = {
  schemaVersion: 1, operation: "pixel-legacy-clean-migration-plan",
  sourcePixel: "3.2.2", installedPixel: "3.2.2", targetPixel: "4.3.27",
  backupSha256: "c".repeat(64),
  backupAudit: { members: 3, roots: 2, uncompressedBytes: 100 },
  releasePolicy: { pixel: "4.3.27", qualificationMode: "forward", minimumUpgradablePixel: "4.0.0" },
  sourceCommit: commit, sourceTree: tree, backupRootsSha256: "f".repeat(64), planSha256: "d".repeat(64),
  generatedAt: "2026-08-22T12:00:00Z",
  privacy, boundary: "Content-free clean-migration plan only. No bootstrap, restore, apply, replace, or rollback is performed by this command.",
};

const rehearsal = {
  schemaVersion: 1, operation: "pixel-legacy-clean-migration-rehearsal",
  sourcePixel: "3.2.2", targetPixel: "4.3.27",
  planSha256: "d".repeat(64), backupSha256: "c".repeat(64),
  sourceCommit: commit, sourceTree: tree, backupRootsSha256: "f".repeat(64),
  rehearsalRootCreated: true, liveStateChanged: false, rehearsalSha256: "e".repeat(64),
  generatedAt: "2026-08-22T12:00:00Z",
  privacy, boundary: "Content-free clean-migration rehearsal receipt. Restore was rehearsed into an explicit new non-live root; no live state was changed.",
};


const restoreReceipt = {
  schemaVersion: 1, kind: "pixel-restore-receipt", status: "pass", mode: "restore",
  verified: true, automaticRollbackArmed: true,
  knowledgeDeletionReconciled: false, historicalKeyWrappingRemoved: false,
  backupSha256: "c".repeat(64), sourcePixel: "3.2.2", targetPixel: "4.3.27",
  receiptSha256: "9".repeat(64), generatedAt: "2026-08-22T12:00:00Z",
  privacy, boundary: "Content-free confirmed restore receipt. The backup was authentically validated, transactionally swapped, verified live, and automatic rollback was armed. No paths, identities, credentials, host, provider, or model identity, and no user content are included.",
};

const completion = {
  schemaVersion: 1, operation: "pixel-legacy-clean-migration-completion",
  sourcePixel: "3.2.2", targetPixel: "4.3.27", activeRelease: "4.3.27",
  planSha256: "d".repeat(64), rehearsalSha256: "e".repeat(64), backupSha256: "c".repeat(64),
  restoreReceiptSha256: "f".repeat(64), runtimeAttestationSha256: "1".repeat(64),
  sourceCommit: commit, sourceTree: tree, verified: true, completionSha256: "2".repeat(64),
  generatedAt: "2026-08-22T12:00:00Z",
  privacy, boundary: "Content-free clean-migration completion receipt. It binds the authenticated backup, restore receipt, runtime attestation, and Git source identity to the exact migration plan and rehearsal. No in-place 3.2.2 update or update-rollback exists; the authenticated backup is the single rollback boundary.",
};

test("plan, rehearsal, and completion evidence satisfy the strict schema", () => {
  for (const document of [plan, rehearsal, completion, restoreReceipt]) {
    assert.deepEqual(validateJsonSchema(document, schema), []);
    assertJsonSchema(document, schema, "legacy clean migration evidence");
  }
});

test("schema rejects wrong versions, weakened policy, and cross-phase fields", () => {
  const wrongSource = { ...plan, sourcePixel: "4.0.0" };
  const weakened = { ...plan, releasePolicy: { pixel: "4.3.27", qualificationMode: "inplace", minimumUpgradablePixel: "4.0.0" } };
  const wrongFloor = { ...plan, releasePolicy: { pixel: "4.3.27", qualificationMode: "forward", minimumUpgradablePixel: "3.2.2" } };
  const crossPhase = { ...plan, rehearsalSha256: "e".repeat(64) };
  const missing = { ...plan };
  delete missing.planSha256;
  for (const hostile of [wrongSource, weakened, wrongFloor, crossPhase, missing]) {
    assert.ok(validateJsonSchema(hostile, schema).length > 0, JSON.stringify(hostile));
  }
});

test("restore receipt rejects forged and minimal artifacts", () => {
  const wrongBackup = { ...restoreReceipt, backupSha256: "z".repeat(64) };
  const wrongRollback = { ...restoreReceipt, automaticRollbackArmed: false };
  const wrongSource = { ...restoreReceipt, sourcePixel: "4.0.0" };
  const wrongTarget = { ...restoreReceipt, targetPixel: "4.1.0" };
  const staleHash = { ...restoreReceipt, receiptSha256: "1".repeat(63) };
  const minimal = { status: "pass", mode: "restore" };
  for (const hostile of [wrongBackup, wrongRollback, wrongSource, wrongTarget, staleHash, minimal]) {
    assert.ok(validateJsonSchema(hostile, schema).length > 0, JSON.stringify(hostile));
  }
});

test("schema rejects duplicate-key and non-finite hostile payloads", () => {
  const hostile = JSON.parse('{"schemaVersion":1,"schemaVersion":2}');
  assert.ok(validateJsonSchema(hostile, schema).length > 0);
});
