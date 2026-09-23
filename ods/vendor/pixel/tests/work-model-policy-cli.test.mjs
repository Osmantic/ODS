import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { chmod, link, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

import { runModelPolicyCommand } from "../deploy/work-controller/model-policy-cli.mjs";
import {
  validateWorkModelCapabilityReceipt, validateWorkModelOperatorStatus,
  validateWorkModelPolicyEnableReview, validateWorkModelPolicyReview, validateWorkPolicy,
} from "../scripts/lib/work-contract.mjs";
import { installFixtureModelQualification } from "./fixtures/work/model-qualification.mjs";
import { fixtureVllmInferencePolicy } from "./fixtures/work/inference-policy.mjs";

const run = promisify(execFile);
const cliPath = fileURLToPath(new URL("../deploy/work-controller/model-policy-cli.mjs", import.meta.url));
const baseTime = new Date("2026-08-11T12:00:00Z");

async function privateWrite(path, value) {
  await writeFile(path, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
}

async function fixture(t, provider = "llama.cpp") {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-policy-"));
  if (process.platform !== "win32") await chmod(root, 0o700);
  t.after(() => rm(root, { recursive: true, force: true }));
  const policy = JSON.parse(await readFile(new URL("../deploy/work-broker/policy.example.json", import.meta.url), "utf8"));
  policy.localModel.provider = provider;
  if (provider === "vllm") {
    policy.localModel.imageRef = `local/pixel-vllm@${policy.localModel.imageDigest}`;
    policy.localModel.inference = fixtureVllmInferencePolicy();
  }
  const installed = await installFixtureModelQualification(policy, root, baseTime);
  installed.receipt.expiresAt = new Date(Date.parse(installed.receipt.observedAt) + 30 * 86400000).toISOString();
  assert.deepEqual(validateWorkModelCapabilityReceipt(installed.receipt), []);
  await privateWrite(installed.path, installed.receipt);
  policy.localModel.prepared = false;
  const policyPath = join(root, "policy.json");
  await privateWrite(policyPath, policy);
  return { root, policy, policyPath, receipt: installed.receipt, receiptPath: installed.path };
}

test("trusted review binds qualification into a new disabled policy without widening authority", async (t) => {
  const value = await fixture(t);
  const review = await runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime });
  assert.deepEqual(validateWorkModelPolicyReview(review), []);
  assert.deepEqual(review.eligibleProfiles, ["assistant", "scout", "builder", "data-lab", "researcher"]);
  assert.deepEqual(review.requiredProfiles, []);
  assert.equal(review.changes.enablesDeepWork, false);
  assert.equal(review.authority.grantsExecution, false);
  const output = join(value.root, "prepared-policy.json");
  await assert.rejects(runModelPolicyCommand([
    "apply", "--policy", value.policyPath, "--receipt", value.receiptPath, "--output", output,
    "--confirm-proposed-policy-sha256", "0".repeat(64),
  ], { now: baseTime }), /confirmation differs/u);
  const applied = await runModelPolicyCommand([
    "apply", "--policy", value.policyPath, "--receipt", value.receiptPath, "--output", output,
    "--confirm-proposed-policy-sha256", review.proposedPolicySha256,
  ], { now: baseTime });
  assert.equal(applied.status, "prepared-policy-written");
  const prepared = JSON.parse(await readFile(output, "utf8"));
  assert.deepEqual(validateWorkPolicy(prepared), []);
  assert.equal(prepared.enabled, value.policy.enabled);
  assert.deepEqual(prepared.profiles, value.policy.profiles);
  assert.equal(prepared.localModel.prepared, true);
  assert.equal(prepared.localModel.qualification.receiptPath, value.receiptPath);
  assert.equal(prepared.localModel.qualification.casesSha256, value.receipt.suite.casesSha256);
  if (process.platform !== "win32") assert.equal((await stat(output)).mode & 0o077, 0);
  await assert.rejects(runModelPolicyCommand([
    "apply", "--policy", value.policyPath, "--receipt", value.receiptPath, "--output", output,
    "--confirm-proposed-policy-sha256", review.proposedPolicySha256,
  ], { now: baseTime }), /must be a new private file/u);

  const status = await runModelPolicyCommand(["status", "--policy", output], { now: baseTime });
  assert.deepEqual(validateWorkModelOperatorStatus(status), []);
  assert.equal(status.state, "ready-disabled");
  assert.equal(status.nextAction, "enable-policy-when-ready");
  assert.equal(status.qualification.current, true);
  const encoded = JSON.stringify(status);
  for (const forbidden of [value.receipt.model.id, value.receipt.model.backendVersion, value.receiptPath, value.receipt.model.modelArtifactSha256]) assert.doesNotMatch(encoded, new RegExp(forbidden.replaceAll("\\", "\\\\"), "u"));
});

test("trusted review binds an exact vLLM receipt without enabling Deep Work", async (t) => {
  const value = await fixture(t, "vllm");
  const review = await runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime });
  const output = join(value.root, "prepared-vllm-policy.json");
  await runModelPolicyCommand([
    "apply", "--policy", value.policyPath, "--receipt", value.receiptPath, "--output", output,
    "--confirm-proposed-policy-sha256", review.proposedPolicySha256,
  ], { now: baseTime });
  const prepared = JSON.parse(await readFile(output, "utf8"));
  assert.deepEqual(validateWorkPolicy(prepared), []);
  assert.equal(prepared.enabled, false);
  assert.equal(prepared.localModel.prepared, true);
  assert.equal(prepared.localModel.provider, "vllm");
  assert.equal(value.receipt.model.provider, "vllm");
});

test("trusted enablement writes only a new enabled policy after exact qualification review", async (t) => {
  const value = await fixture(t);
  value.policy.runner.prepared = true;
  value.policy.runner.imageDigest = `sha256:${"e".repeat(64)}`;
  value.policy.runner.imageRef = value.policy.runner.imageDigest;
  value.policy.profiles.scout.enabled = true;
  value.policy.profiles.builder.enabled = true;
  value.policy.verifier.enabled = true;
  await privateWrite(value.policyPath, value.policy);
  const binding = await runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime });
  const preparedPath = join(value.root, "prepared-capable-policy.json");
  await runModelPolicyCommand([
    "apply", "--policy", value.policyPath, "--receipt", value.receiptPath, "--output", preparedPath,
    "--confirm-proposed-policy-sha256", binding.proposedPolicySha256,
  ], { now: baseTime });

  const review = await runModelPolicyCommand(["enable-review", "--policy", preparedPath], { now: baseTime });
  assert.deepEqual(validateWorkModelPolicyEnableReview(review), []);
  assert.equal(review.status, "confirmation-required");
  assert.deepEqual(review.enabledProfiles, ["scout", "builder"]);
  assert.equal(review.changes.enablesDeepWork, true);
  assert.equal(review.changes.enablesProfiles, false);
  assert.equal(review.changes.changesTools, false);
  assert.equal(review.changes.changesBudgets, false);
  assert.equal(review.changes.changesSecurity, false);
  assert.equal(review.changes.startsModel, false);
  assert.equal(review.authority.grantsExecution, false);
  const renewSoon = await runModelPolicyCommand(["enable-review", "--policy", preparedPath], { now: new Date(Date.parse(value.receipt.expiresAt) - 6 * 86400000) });
  assert.equal(renewSoon.proposedPolicySha256, review.proposedPolicySha256);
  const enabledPath = join(value.root, "enabled-capable-policy.json");
  await assert.rejects(runModelPolicyCommand([
    "enable-apply", "--policy", preparedPath, "--output", enabledPath,
    "--confirm-proposed-policy-sha256", "0".repeat(64),
  ], { now: baseTime }), /confirmation differs/u);
  const result = await runModelPolicyCommand([
    "enable-apply", "--policy", preparedPath, "--output", enabledPath,
    "--confirm-proposed-policy-sha256", review.proposedPolicySha256,
  ], { now: baseTime });
  assert.equal(result.status, "enabled-policy-written");
  const prepared = JSON.parse(await readFile(preparedPath, "utf8"));
  const enabled = JSON.parse(await readFile(enabledPath, "utf8"));
  assert.deepEqual(validateWorkPolicy(enabled), []);
  assert.equal(enabled.enabled, true);
  const changed = structuredClone(enabled); changed.enabled = false;
  assert.deepEqual(changed, prepared);
  const status = await runModelPolicyCommand(["status", "--policy", enabledPath], { now: baseTime });
  assert.equal(status.state, "ready");
  if (process.platform !== "win32") assert.equal((await stat(enabledPath)).mode & 0o077, 0);
});

test("policy enablement rejects unprepared, empty, stale, tampered, and already-enabled inputs", async (t) => {
  const value = await fixture(t);
  await assert.rejects(runModelPolicyCommand(["enable-review", "--policy", value.policyPath], { now: baseTime }), /not model-prepared/u);

  value.policy.runner.prepared = true;
  value.policy.runner.imageDigest = `sha256:${"e".repeat(64)}`;
  value.policy.runner.imageRef = value.policy.runner.imageDigest;
  await privateWrite(value.policyPath, value.policy);
  const binding = await runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime });
  const emptyPath = join(value.root, "prepared-empty.json");
  await runModelPolicyCommand(["apply", "--policy", value.policyPath, "--receipt", value.receiptPath, "--output", emptyPath, "--confirm-proposed-policy-sha256", binding.proposedPolicySha256], { now: baseTime });
  await assert.rejects(runModelPolicyCommand(["enable-review", "--policy", emptyPath], { now: baseTime }), /no enabled execution profile/u);

  const prepared = JSON.parse(await readFile(emptyPath, "utf8"));
  prepared.profiles.scout.enabled = true;
  prepared.profiles.builder.enabled = true;
  prepared.verifier.enabled = true;
  const preparedPath = join(value.root, "prepared-builder.json");
  await privateWrite(preparedPath, prepared);
  await assert.rejects(runModelPolicyCommand(["enable-review", "--policy", preparedPath], { now: new Date(value.receipt.expiresAt) }), /does not have current exact qualification/u);
  const tampered = structuredClone(value.receipt); tampered.suite.evaluatorSha256 = "a".repeat(64);
  await privateWrite(value.receiptPath, tampered);
  await assert.rejects(runModelPolicyCommand(["enable-review", "--policy", preparedPath], { now: baseTime }), /does not have current exact qualification/u);

  await privateWrite(value.receiptPath, value.receipt);
  const review = await runModelPolicyCommand(["enable-review", "--policy", preparedPath], { now: baseTime });
  const enabledPath = join(value.root, "enabled.json");
  await runModelPolicyCommand(["enable-apply", "--policy", preparedPath, "--output", enabledPath, "--confirm-proposed-policy-sha256", review.proposedPolicySha256], { now: baseTime });
  await assert.rejects(runModelPolicyCommand(["enable-review", "--policy", enabledPath], { now: baseTime }), /already enabled/u);
});

test("identity, profile, expiry, tamper, link, and output substitution fail closed", async (t) => {
  const value = await fixture(t);
  const provider = structuredClone(value.receipt); provider.model.provider = "vllm";
  await privateWrite(value.receiptPath, provider);
  await assert.rejects(runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime }), /identity differs/u);
  const identity = structuredClone(value.receipt); identity.model.id = "different-model";
  await privateWrite(value.receiptPath, identity);
  await assert.rejects(runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime }), /identity differs/u);

  const profileRoot = join(value.root, "profile-regression");
  await mkdir(profileRoot, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(profileRoot, 0o700);
  const profilePolicy = structuredClone(value.policy);
  const { receipt: profile } = await installFixtureModelQualification(profilePolicy, profileRoot, baseTime, { failingProfile: "scout" });
  assert.deepEqual(validateWorkModelCapabilityReceipt(profile), []);
  await privateWrite(value.receiptPath, profile);
  const requiredPolicy = structuredClone(value.policy); requiredPolicy.profiles.scout.enabled = true;
  await privateWrite(value.policyPath, requiredPolicy);
  await assert.rejects(runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime }), /policy-enabled profile/u);

  await privateWrite(value.receiptPath, value.receipt); await privateWrite(value.policyPath, value.policy);
  const review = await runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime });
  const boundPath = join(value.root, "bound.json");
  await runModelPolicyCommand(["apply", "--policy", value.policyPath, "--receipt", value.receiptPath, "--output", boundPath, "--confirm-proposed-policy-sha256", review.proposedPolicySha256], { now: baseTime });
  const expired = await runModelPolicyCommand(["status", "--policy", boundPath], { now: new Date(value.receipt.expiresAt) });
  assert.equal(expired.state, "expired");
  assert.equal(expired.nextAction, "renew-qualification");
  await privateWrite(value.receiptPath, {});
  const unavailable = await runModelPolicyCommand(["status", "--policy", boundPath], { now: baseTime });
  assert.equal(unavailable.state, "unavailable");
  assert.doesNotMatch(JSON.stringify(unavailable), /model-qualification|bound\.json/u);

  await privateWrite(value.receiptPath, value.receipt);
  const linked = join(value.root, "linked-receipt.json"); await link(value.receiptPath, linked);
  await assert.rejects(runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime }), /single-link/u);
});

test("policy and qualification inputs reject duplicate decoded JSON keys", async (t) => {
  const value = await fixture(t);
  const policyText = await readFile(value.policyPath, "utf8");
  const duplicatePolicy = policyText.replace('"enabled": false,', '"enabled": false,\n  "\\u0065nabled": false,');
  assert.notEqual(duplicatePolicy, policyText);
  await writeFile(value.policyPath, duplicatePolicy, { mode: 0o600 });
  await assert.rejects(runModelPolicyCommand(["status", "--policy", value.policyPath], { now: baseTime }), /not strict JSON/u);

  await privateWrite(value.policyPath, value.policy);
  const receiptText = await readFile(value.receiptPath, "utf8");
  const duplicateReceipt = receiptText.replace('"schemaVersion": 1,', '"schemaVersion": 1,\n  "\\u0073chemaVersion": 1,');
  assert.notEqual(duplicateReceipt, receiptText);
  await writeFile(value.receiptPath, duplicateReceipt, { mode: 0o600 });
  await assert.rejects(
    runModelPolicyCommand(["review", "--policy", value.policyPath, "--receipt", value.receiptPath], { now: baseTime }),
    /not strict JSON/u,
  );
});

test("real CLI emits content-free status and uses a distinct non-ready exit", async (t) => {
  const value = await fixture(t);
  const result = await run(process.execPath, [cliPath, "status", "--policy", value.policyPath], { maxBuffer: 1024 * 1024 }).catch((error) => error);
  assert.equal(result.code, 2);
  const status = JSON.parse(result.stdout.trim());
  assert.equal(status.state, "not-prepared");
  assert.equal(status.nextAction, "run-qualification");
  assert.doesNotMatch(result.stdout, new RegExp(value.receipt.model.id, "u"));
  assert.equal(result.stderr, "");
});
