import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { validateJsonSchema } from "../scripts/lib/json-schema.mjs";

const run = promisify(execFile);
const repo = dirname(dirname(fileURLToPath(import.meta.url)));
const digest = (value) => createHash("sha256").update(value).digest("hex");

test("runtime receipt is content-free, exact-source bound, and honest about model proof", async () => {
  const directory = await mkdtemp(join(tmpdir(), "pixel-runtime-attestation-"));
  try {
    const source = join(directory, "source");
    const active = join(directory, "release");
    await mkdir(join(source, ".generated"), { recursive: true });
    await mkdir(active);
    const manifest = JSON.parse(await readFile(join(repo, "RELEASE-MANIFEST.json"), "utf8"));
    await writeFile(join(source, "RELEASE-MANIFEST.json"), JSON.stringify({ pixel: manifest.pixel, openclaw: manifest.openclaw }));
    await writeFile(join(source, ".generated", "deployment.json"), JSON.stringify({
      deploymentProfile: "prepared", capabilityProfile: "chief-of-staff",
      modelProvider: "local", modelId: "PRIVATE_MODEL_CANARY", modelContextWindow: 8192,
      modelMaxTokens: 1024, modelReasoning: true,
      limbs: { email: true, calendar: true, social: false, web: true, operations: false, frontier: false },
    }));
    const identity = {
      schemaVersion: 1, kind: "pixel-release-source-identity", pixel: manifest.pixel,
      source: { state: "git-clean", commit: "a".repeat(40), tree: "b".repeat(40) },
      manifests: { releaseSha256: "c".repeat(64), compatibilitySha256: "d".repeat(64), qualificationMatrixSha256: "e".repeat(64) },
      qualification: { recordStatus: "candidate", sourceCommit: "f".repeat(40), qualifiedAt: "2026-08-11", liveAudit: "PRIVATE_AUDIT_CANARY", relationship: "qualified-ancestor" },
      boundary: "Content-free source and baseline-qualification identity only. This record does not prove installed-byte integrity, runtime health, provider capability, release promotion, publication, deployment approval, or client acceptance.",
    };
    await writeFile(join(active, "release-identity.json"), JSON.stringify(identity));
    for (const name of ["deployment-inputs.sha256", "source-runtime.sha256", "install-manifest.sha256"]) await writeFile(join(active, name), `${digest(name)}  fixture\n`);
    const configuration = join(directory, "openclaw.json");
    await writeFile(configuration, '{"private":"PRIVATE_CONFIGURATION_CANARY"}\n');
    const output = join(directory, "runtime.json");
    await run(process.execPath, [join(repo, "scripts", "runtime-attestation.mjs"),
      "--source-root", source, "--active-release", active, "--configuration", configuration,
      "--output", output, "--endpoint-state", "verified"]);
    const receipt = JSON.parse(await readFile(output, "utf8"));
    const schema = JSON.parse(await readFile(join(repo, "schemas", "runtime-attestation-v1.schema.json"), "utf8"));
    assert.deepEqual(validateJsonSchema(receipt, schema), []);
    assert.equal(receipt.status, "verified");
    assert.equal(receipt.runtime.state, "gateway-verified-model-unproven");
    assert.equal(receipt.runtime.routeClass, "local");
    assert.notEqual(receipt.runtime.modelIdSha256, digest("different"));
    const encoded = JSON.stringify(receipt);
    for (const secret of ["PRIVATE_MODEL_CANARY", "PRIVATE_CONFIGURATION_CANARY", "PRIVATE_AUDIT_CANARY"]) assert.equal(encoded.includes(secret), false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("runtime writer rejects a contradictory source identity instead of blessing it", async () => {
  const directory = await mkdtemp(join(tmpdir(), "pixel-runtime-attestation-invalid-"));
  try {
    const source = join(directory, "source");
    const active = join(directory, "release");
    const manifest = JSON.parse(await readFile(join(repo, "RELEASE-MANIFEST.json"), "utf8"));
    await mkdir(join(source, ".generated"), { recursive: true });
    await mkdir(active);
    await writeFile(join(source, "RELEASE-MANIFEST.json"), JSON.stringify({ pixel: manifest.pixel, openclaw: manifest.openclaw }));
    await writeFile(join(source, ".generated", "deployment.json"), JSON.stringify({
      deploymentProfile: "prepared", capabilityProfile: "minimal", modelProvider: "local", modelId: "model",
      modelContextWindow: 8192, modelMaxTokens: 1024, modelReasoning: false,
      limbs: { email: false, calendar: false, social: false, web: false, operations: false, frontier: false },
    }));
    await writeFile(join(active, "release-identity.json"), JSON.stringify({
      schemaVersion: 1, kind: "pixel-release-source-identity", pixel: manifest.pixel,
      source: { state: "unavailable", commit: null, tree: null },
      manifests: { releaseSha256: "a".repeat(64), compatibilitySha256: "b".repeat(64), qualificationMatrixSha256: "c".repeat(64) },
      qualification: { recordStatus: null, sourceCommit: null, qualifiedAt: null, liveAudit: null, relationship: "same-source" },
      boundary: "Content-free source and baseline-qualification identity only. This record does not prove installed-byte integrity, runtime health, provider capability, release promotion, publication, deployment approval, or client acceptance.",
    }));
    for (const name of ["deployment-inputs.sha256", "source-runtime.sha256", "install-manifest.sha256"]) await writeFile(join(active, name), "fixture\n");
    const configuration = join(directory, "openclaw.json");
    await writeFile(configuration, "{}\n");
    await assert.rejects(run(process.execPath, [join(repo, "scripts", "runtime-attestation.mjs"),
      "--source-root", source, "--active-release", active, "--configuration", configuration,
      "--output", join(directory, "runtime.json"), "--endpoint-state", "verified"]), /qualification relationship is invalid/u);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
