// LEGACY-ROUTE FIXTURE: qualifies the llama.cpp/qwen local plumbing route only.
// Pixel's product model is DeepSeek-V4-Flash-0731 (vLLM route); results from this
// file must never support product-quality or Codex-parity claims.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { open, realpath, stat } from "node:fs/promises";
import { dirname, resolve } from "node:path";

import {
  createLoopbackModelQualificationInvoker, fixedModelQualificationCasesSha256,
  fixedModelQualificationEvaluatorSha256, runFixedModelQualification,
} from "../deploy/work-controller/model-qualification-runner.mjs";
import { modelCapabilityReceiptSha256 } from "../deploy/work-controller/model-qualification.mjs";
import {
  localModelPromptContractSha256, localModelToolContractSha256,
} from "../deploy/work-controller/model-runtime-contract.mjs";
import { validateWorkModelCapabilityReceipt } from "../scripts/lib/work-contract.mjs";
import { startLoopbackModelBridge } from "./fixtures/work/loopback-model-bridge.mjs";

const MODEL = Object.freeze({
  provider: "llama.cpp",
  id: "qwen2.5-coder-7b-instruct-q4_k_m",
  modelArtifactSha256: "509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c",
  backendImageDigest: "sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f",
  backendVersion: "b9014",
  acceleratorClass: "nvidia-cuda",
  promptContractSha256: localModelPromptContractSha256(),
  toolSchemaSha256: localModelToolContractSha256(),
  contextWindow: 30000,
  supportsVision: false,
});

const outputPath = resolve(process.env.PIXEL_WORK_MODEL_QUALIFICATION_OUTPUT ?? "");
if (!process.env.PIXEL_WORK_MODEL_QUALIFICATION_OUTPUT) {
  throw new Error("Usage: PIXEL_WORK_MODEL_QUALIFICATION_ORIGIN=http://127.0.0.1:PORT PIXEL_WORK_MODEL_QUALIFICATION_OUTPUT=NEW_PRIVATE_JSON node tests/work-model-qualification-real-live.mjs");
}
if (await realpath(dirname(outputPath)).catch(() => null) !== dirname(outputPath)) throw new Error("qualification output parent is not a real directory");
let bridge = null;
let backendOrigin = process.env.PIXEL_WORK_MODEL_QUALIFICATION_ORIGIN;
if (process.env.PIXEL_WORK_MODEL_QUALIFICATION_UPSTREAM === "http://pixel-local-model:8080") {
  bridge = await startLoopbackModelBridge({ origin: process.env.PIXEL_WORK_MODEL_QUALIFICATION_UPSTREAM });
  bridge.unref();
  const address = bridge.address();
  if (!address || typeof address !== "object") throw new Error("qualification loopback bridge did not bind");
  backendOrigin = `http://127.0.0.1:${address.port}`;
}
if (!/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/u.test(backendOrigin ?? "")) throw new Error("qualification backend must be an explicit loopback origin");

const observedAt = new Date();
const invoke = createLoopbackModelQualificationInvoker({
  schemaVersion: 1,
  backendOrigin,
  model: MODEL,
  timeoutMs: 180000,
  maxResponseBytes: 1024 * 1024,
  qualificationLifetimeSeconds: 7 * 86400,
});
const receipt = await runFixedModelQualification({ model: MODEL, invoke, observedAt });
assert.deepEqual(validateWorkModelCapabilityReceipt(receipt), []);
assert.equal(receipt.suite.casesSha256, fixedModelQualificationCasesSha256());
assert.equal(receipt.suite.evaluatorSha256, await fixedModelQualificationEvaluatorSha256());

const bytes = Buffer.from(`${JSON.stringify(receipt, null, 2)}\n`, "utf8");
const handle = await open(outputPath, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW ?? 0), 0o600);
try { await handle.writeFile(bytes); await handle.sync(); } finally { await handle.close(); }
if (process.platform !== "win32") assert.equal((await stat(outputPath)).mode & 0o077, 0);
if (bridge) await new Promise((resolveClose, reject) => bridge.close((error) => error ? reject(error) : resolveClose()));

process.stdout.write(`${JSON.stringify({
  status: receipt.status,
  receiptSha256: modelCapabilityReceiptSha256(receipt),
  fileSha256: createHash("sha256").update(bytes).digest("hex"),
  casesPassed: receipt.observations.casesPassed,
  casesFailed: receipt.observations.casesFailed,
  eligibleProfiles: receipt.envelope.eligibleProfiles,
  profilePass: receipt.observations.profilePass,
  maxContextTokens: receipt.envelope.maxContextTokens,
  maxOutputTokens: receipt.envelope.maxOutputTokens,
  exactUsage: receipt.envelope.exactUsage,
  promptContractSha256: MODEL.promptContractSha256,
  toolSchemaSha256: MODEL.toolSchemaSha256,
})}\n`);
assert.equal(receipt.status, "qualified");
assert.equal(receipt.observations.casesPassed, 15);
assert.equal(receipt.observations.casesFailed, 0);
assert.deepEqual(receipt.envelope.eligibleProfiles, ["assistant", "scout", "builder", "data-lab", "researcher"]);
