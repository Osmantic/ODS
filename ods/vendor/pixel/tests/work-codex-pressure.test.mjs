import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { compileWorkCodexPreview } from "../deploy/work-codex-provider/compiler.mjs";
import { validateWorkCodexOutput, validateWorkCodexPlan } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T19:00:00Z"), digest = (value) => createHash("sha256").update(value).digest("hex");
const policy = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));
policy.enabled = true; policy.taskClasses["structural-review"].enabled = true;

function request(index, content, sensitiveTerms = []) {
  const text = content.normalize("NFC");
  return {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-request-v1.schema.json", schemaVersion: 1,
    requestId: `workcodexrequest-${baseTime}-${index.toString(16).padStart(12, "0")}`, createdAt: new Date(baseTime).toISOString(), expiresAt: new Date(baseTime + 1200000).toISOString(),
    jobId: `work-${baseTime}-${(index + 1000).toString(16).padStart(12, "0")}`, checkpointSha256: digest(`checkpoint-${index}`), ownerId: "pressure-owner", clientId: "pressure-client",
    taskClass: "structural-review", egressMode: "structural-only", classification: "internal", dataCategories: ["personal-identifiers", "structural"],
    localAttempt: { attempts: 3, outcome: "completed-needs-review", reasonCodes: ["quality-check"], receiptSha256: digest(`receipt-${index}`) },
    objective: `Review case ${index} without external effects.`, constraints: ["Keep all identifiers private."], acceptanceCriteria: ["Return one bounded structural finding."],
    sensitiveTerms, documents: [{ documentId: `case_${index.toString().padStart(4, "0")}`, kind: "structure", language: "text", content: text, contentSha256: digest(text) }],
    maxOutputTokens: 512,
    boundary: "Private local request for one sanitized Codex work proposal. Compilation does not authorize or invoke a provider, expose credentials, or grant any external effect.",
  };
}

test("2,500 varied PII and identifier appearances are replaced without entering a preview", () => {
  let replacements = 0;
  for (let index = 1; index <= 500; index += 1) {
    const customer = `Client-${index.toString().padStart(4, "0")}`;
    const privateValues = [
      customer, `résumé${index}@exämple.test`, `203.0.113.${index % 250 + 1}`, `2001:db8::${(index % 65535).toString(16)}`,
      `https://internal${index}.example.test/private/${index}`, `C:\\Users\\client${index}\\private.txt`, `+1415${(5550000 + index).toString()}`,
    ];
    const input = request(index, `Customer ${privateValues.join(" uses ")}.`, [{ kind: "customer", value: customer }]);
    const { plan, privateMapping } = compileWorkCodexPreview({ request: input, policy, now: new Date(baseTime + 1), suffix: index.toString(16).padStart(12, "0"), mappingNonce: digest(`nonce-${index}`) });
    const preview = JSON.stringify(plan);
    for (const value of privateValues) assert.equal(preview.includes(value), false, `case ${index} leaked ${value}`);
    assert.deepEqual(validateWorkCodexPlan(plan), []); replacements += privateMapping.entries.length;
  }
  assert.ok(replacements >= 2500);
});

test("1,000 credential and opaque-token variants fail before a plan exists", () => {
  for (let index = 1; index <= 500; index += 1) {
    const secret = `sk-proj-${digest(`provider-${index}`)}`;
    assert.throws(() => compileWorkCodexPreview({ request: request(index, `credential=${secret}`), policy, now: new Date(baseTime + 1) }), /credential-like secret/);
  }
  for (let index = 501; index <= 1000; index += 1) {
    const secret = `Aa9Z${Buffer.from(digest(`opaque-${index}`), "hex").toString("base64url")}`;
    assert.throws(() => compileWorkCodexPreview({ request: request(index, `opaque ${secret}`), policy, now: new Date(baseTime + 1) }), /high-entropy token/, `opaque case ${index}`);
  }
});

test("structured output is tightly bounded and extra authority fails schema validation", () => {
  const valid = {
    $schema: "https://osmantic.com/pixel/schemas/work-codex-output-v1.schema.json", schemaVersion: 1, taskClass: "structural-review",
    summary: "The sanitized interface has one unresolved boundary.",
    findings: [{ severity: "medium", title: "Boundary is implicit", evidence: "DOC_001 does not name the check.", recommendation: "Add a local acceptance check." }],
    proposals: [], verificationSuggestions: ["Run the local boundary test."], unresolvedRisks: ["Provider output remains untrusted."],
    boundary: "Untrusted advisory output only. Pixel must validate, locally rehydrate only approved placeholders, and independently verify every proposed change; this output grants no execution or external-effect authority.",
  };
  assert.deepEqual(validateWorkCodexOutput(valid), []);
  const authority = structuredClone(valid); authority.deploy = true;
  assert.ok(validateWorkCodexOutput(authority).some((error) => /unexpected property deploy/u.test(error)));
  const huge = structuredClone(valid); huge.summary = "x".repeat(8001);
  assert.ok(validateWorkCodexOutput(huge).some((error) => /too long/u.test(error)));
  const loose = structuredClone(valid); loose.findings[0].severity = "execute-now";
  assert.ok(validateWorkCodexOutput(loose).some((error) => /outside the schema enum/u.test(error)));
  const incoherent = structuredClone(valid); incoherent.proposals = [{ targetDocumentId: "DOC_001", operation: "delete", rationale: "Remove it.", content: "smuggled replacement" }];
  assert.ok(validateWorkCodexOutput(incoherent).some((error) => /cannot carry replacement content/u.test(error)));
});
