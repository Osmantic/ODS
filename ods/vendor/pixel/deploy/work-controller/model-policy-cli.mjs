import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, open, realpath, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  canonical, validateWorkModelCapabilityReceipt, validateWorkModelOperatorStatus,
  validateWorkModelPolicyEnableReview, validateWorkModelPolicyReview, validateWorkPolicy,
} from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";

const MAX_POLICY_BYTES = 1024 * 1024;
const MAX_RECEIPT_BYTES = 1024 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const SHA_RE = /^[a-f0-9]{64}$/u;
const profileOrder = Object.freeze(["assistant", "scout", "builder", "data-lab", "researcher"]);
const authority = Object.freeze({ grantsExecution: false, activatesService: false, grantsNetwork: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });
const reviewBoundary = "Trusted-terminal review of one exact local-model qualification binding. Confirmation may write a new private policy candidate but cannot overwrite policy, enable Deep Work or profiles, start a model, activate a service, or grant execution, network, credential, external-effect, or completion authority.";
const enableBoundary = "Trusted-terminal review of one exact prepared policy enablement. Confirmation may write one new enabled private policy with the already-reviewed profiles, tools, budgets, security, runner, and qualified local-model binding unchanged. It does not install the policy, start a model or service, route a job, use credentials or network, perform an external effect, deploy, or claim completion; separately installing the resulting policy makes its enabled profiles eligible for contained execution.";
const statusBoundary = "Content-free local-model readiness only. Status reveals no model, backend, accelerator, path, hash, credential, prompt, or response and grants no qualification, policy mutation, enablement, execution, network, external-effect, or completion authority.";

export class WorkModelPolicyCliError extends Error {}

function fail(message) { throw new WorkModelPolicyCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }

function parseArguments(argv) {
  if (!Array.isArray(argv) || !["review", "apply", "status", "enable-review", "enable-apply"].includes(argv[0])) fail("Usage: model-policy-cli.mjs review|apply|status|enable-review|enable-apply --policy PRIVATE_JSON [--receipt PRIVATE_JSON] [--output NEW_PRIVATE_JSON --confirm-proposed-policy-sha256 HASH]");
  const command = argv[0], allowed = ["status", "enable-review"].includes(command) ? new Set(["--policy"])
    : command === "review" ? new Set(["--policy", "--receipt"])
      : command === "enable-apply" ? new Set(["--policy", "--output", "--confirm-proposed-policy-sha256"])
        : new Set(["--policy", "--receipt", "--output", "--confirm-proposed-policy-sha256"]);
  if (argv.length !== 1 + allowed.size * 2) fail("local-model policy arguments are incomplete");
  const values = {};
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("local-model policy arguments are invalid, unknown, or duplicated");
    values[key] = key === "--confirm-proposed-policy-sha256" ? value : resolve(value);
  }
  for (const key of allowed) if (!values[key]) fail(`local-model policy command is missing ${key}`);
  if (["apply", "enable-apply"].includes(command) && !SHA_RE.test(values["--confirm-proposed-policy-sha256"])) fail("local-model policy confirmation is invalid");
  return { command, policyPath: values["--policy"], receiptPath: values["--receipt"] ?? null, outputPath: values["--output"] ?? null, confirmation: values["--confirm-proposed-policy-sha256"] ?? null };
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid) {
  let record, actual;
  try { [record, actual] = await Promise.all([readBoundedRegularFile(path, maximum, label), realpath(path)]); }
  catch { fail(`${label} could not be opened safely`); }
  if (!samePath(actual, path) || record.details.nlink !== 1 || process.platform !== "win32" && (record.details.uid !== expectedOwnerUid || (record.details.mode & 0o077) !== 0)) fail(`${label} is not owner-private, single-link, and real`);
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${label} is not strict UTF-8`);
  try { return parseStrictJson(text, label); } catch { fail(`${label} is not strict JSON`); }
}

async function requirePrivateOutput(path, expectedOwnerUid) {
  const name = basename(path), parent = dirname(path);
  if (!OUTPUT_RE.test(name) || join(parent, name) !== path) fail("prepared policy output name is invalid");
  let info, actual;
  try { [info, actual] = await Promise.all([lstat(parent), realpath(parent)]); }
  catch { fail("prepared policy output parent could not be opened safely"); }
  if (!info.isDirectory() || info.isSymbolicLink() || !samePath(actual, parent) || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail("prepared policy output parent is not owner-private and real");
  const existing = await lstat(path).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (existing !== null) fail("prepared policy output must be a new private file");
}

async function writePrivateNew(path, value, expectedOwnerUid) {
  await requirePrivateOutput(path, expectedOwnerUid);
  const temporary = join(dirname(path), `.pixel-model-policy-${process.pid}-${randomBytes(8).toString("hex")}.json`);
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); }
  finally { await handle.close(); }
  try {
    await link(temporary, path); await unlink(temporary);
    if (process.platform !== "win32") { const parent = await open(dirname(path), constants.O_RDONLY); try { await parent.sync(); } finally { await parent.close(); } }
  } catch (error) {
    await unlink(temporary).catch(() => {});
    if (["EEXIST", "EPERM", "EACCES"].includes(error?.code)) fail("prepared policy output could not be atomically published as a new private file");
    throw error;
  }
}

function requiredProfiles(policy) {
  const enabled = { scout: policy.profiles.scout.enabled, builder: policy.profiles.builder.enabled, "data-lab": policy.profiles.dataLab.enabled, researcher: policy.profiles.researcher.enabled };
  return profileOrder.filter((profile) => enabled[profile]);
}

function modelIdentity(policy) {
  const local = policy.localModel;
  return {
    provider: local.provider, id: local.id, modelArtifactSha256: local.modelArtifactSha256,
    backendImageDigest: local.imageDigest, backendVersion: local.backendVersion,
    acceleratorClass: local.acceleratorClass, promptContractSha256: local.promptContractSha256,
    toolSchemaSha256: local.toolSchemaSha256, contextWindow: local.contextWindow,
    supportsVision: local.supportsVision,
  };
}

function checkPolicy(policy) {
  const errors = validateWorkPolicy(policy);
  if (errors.length) fail(`private work policy is invalid: ${errors[0]}`);
}

function checkReceipt(receipt) {
  const errors = validateWorkModelCapabilityReceipt(receipt);
  if (errors.length || receipt.status !== "qualified" || receipt.envelope.exactUsage !== true || receipt.envelope.eligibleProfiles.length < 1) fail("model qualification evidence is not empirically qualified");
}

function exactIdentityMatches(policy, receipt) {
  return canonical(modelIdentity(policy)) === canonical(receipt.model);
}

function proposal(policy, receipt, receiptPath, now) {
  checkPolicy(policy); checkReceipt(receipt);
  if (!exactIdentityMatches(policy, receipt)) fail("model qualification identity differs from the private policy");
  const observed = Date.parse(receipt.observedAt), expires = Date.parse(receipt.expiresAt), checked = now.getTime();
  if (!Number.isFinite(observed) || !Number.isFinite(expires) || checked < observed || checked >= expires) fail("model qualification evidence is not current");
  if (policy.localModel.maxRequestOutputTokens > receipt.envelope.maxOutputTokens) fail("private policy output request exceeds the measured model envelope");
  if (policy.localModel.maxRequestContextTokens > receipt.envelope.maxContextTokens) fail("private policy context request exceeds the measured model envelope");
  const required = requiredProfiles(policy);
  if (required.some((profile) => !receipt.envelope.eligibleProfiles.includes(profile))) fail("model qualification does not cover every policy-enabled profile");
  const prepared = structuredClone(policy);
  prepared.localModel.prepared = true;
  prepared.localModel.qualification = {
    receiptPath, receiptSha256: sha(receipt), casesSha256: receipt.suite.casesSha256,
    evaluatorSha256: receipt.suite.evaluatorSha256,
  };
  checkPolicy(prepared);
  const proposedPolicySha256 = sha(prepared);
  const review = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-policy-review-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-policy-review", status: "confirmation-required",
    eligibleProfiles: [...receipt.envelope.eligibleProfiles], requiredProfiles: required,
    expiresAt: receipt.expiresAt,
    measuredEnvelope: { maxContextTokens: receipt.envelope.maxContextTokens, maxOutputTokens: receipt.envelope.maxOutputTokens, exactUsage: true },
    proposedPolicySha256, confirmation: { option: "--confirm-proposed-policy-sha256", sha256: proposedPolicySha256 },
    changes: { setsLocalModelPrepared: true, bindsExactQualification: true, enablesDeepWork: false, enablesProfiles: false, changesBudgets: false, changesAuthority: false },
    authority: { ...authority }, boundary: reviewBoundary,
  };
  const errors = validateWorkModelPolicyReview(review);
  if (errors.length) fail(`model policy review is invalid: ${errors[0]}`);
  return Object.freeze({ prepared: Object.freeze(prepared), review: Object.freeze(review) });
}

function enableProposal(policy, receipt, now) {
  checkPolicy(policy);
  if (policy.enabled) fail("private work policy is already enabled");
  if (!policy.runner.prepared || !policy.localModel.prepared) fail("private work policy is not fully prepared");
  const required = requiredProfiles(policy);
  if (required.length < 1) fail("private work policy has no enabled execution profile");
  const readiness = operatorStatus(policy, receipt, now);
  if (!["ready-disabled", "renew-soon"].includes(readiness.state)) fail("private work policy does not have current exact qualification for every enabled profile");
  const enabled = structuredClone(policy);
  enabled.enabled = true;
  checkPolicy(enabled);
  const currentPolicySha256 = sha(policy), proposedPolicySha256 = sha(enabled);
  const review = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-policy-enable-review-v1.schema.json",
    schemaVersion: 1, operation: "pixel-work-model-policy-enable-review", status: "confirmation-required",
    currentPolicySha256, proposedPolicySha256,
    enabledProfiles: required, qualificationExpiresAt: receipt.expiresAt,
    confirmation: { option: "--confirm-proposed-policy-sha256", sha256: proposedPolicySha256 },
    changes: {
      writesNewPrivatePolicy: true, enablesDeepWork: true, enablesProfiles: false,
      changesTools: false, changesBudgets: false, changesSecurity: false,
      startsModel: false, startsService: false, routesJob: false,
    },
    authority: { ...authority }, boundary: enableBoundary,
  };
  const errors = validateWorkModelPolicyEnableReview(review);
  if (errors.length) fail(`model policy enablement review is invalid: ${errors[0]}`);
  return Object.freeze({
    enabled: Object.freeze(enabled),
    review: Object.freeze(review),
  });
}

function emptyQualification(policy) {
  return { present: false, current: false, eligibleProfiles: [], requiredProfiles: requiredProfiles(policy), expiresAt: null, maxContextTokens: null, maxOutputTokens: null, exactUsage: null };
}

function operatorStatus(policy, receipt, now, evidenceState = "valid") {
  checkPolicy(policy);
  const base = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-operator-status-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-model-status", generatedAt: now.toISOString(),
    policy: { deepWorkEnabled: policy.enabled, localModelPrepared: policy.localModel.prepared },
    controls: { browserCanQualify: false, browserCanBindPolicy: false, browserCanEnableDeepWork: false },
    privacy: { modelIdentifier: false, backendIdentifier: false, acceleratorIdentifier: false, paths: false, hashes: false, credentials: false, prompts: false, responses: false },
    authority: { ...authority }, boundary: statusBoundary,
  };
  let status;
  if (!policy.localModel.prepared) {
    status = { ...base, state: "not-prepared", qualification: emptyQualification(policy), nextAction: "run-qualification" };
  } else if (evidenceState !== "valid" || receipt === null) {
    status = { ...base, state: "unavailable", qualification: emptyQualification(policy), nextAction: "inspect-private-evidence" };
  } else {
    if (validateWorkModelCapabilityReceipt(receipt).length !== 0) {
      status = { ...base, state: "unavailable", qualification: emptyQualification(policy), nextAction: "inspect-private-evidence" };
      const errors = validateWorkModelOperatorStatus(status);
      if (errors.length) fail(`model operator status is invalid: ${errors[0]}`);
      return Object.freeze(status);
    }
    const required = requiredProfiles(policy), eligible = [...receipt.envelope.eligibleProfiles];
    const observed = Date.parse(receipt.observedAt), expires = Date.parse(receipt.expiresAt), checked = now.getTime();
    const bindingValid = receipt.status === "qualified" && receipt.envelope.exactUsage === true
      && exactIdentityMatches(policy, receipt) && policy.localModel.qualification.receiptSha256 === sha(receipt)
      && policy.localModel.qualification.casesSha256 === receipt.suite.casesSha256
      && policy.localModel.qualification.evaluatorSha256 === receipt.suite.evaluatorSha256
      && policy.localModel.maxRequestContextTokens <= receipt.envelope.maxContextTokens
      && policy.localModel.maxRequestOutputTokens <= receipt.envelope.maxOutputTokens;
    if (!bindingValid || !Number.isFinite(observed) || !Number.isFinite(expires) || checked < observed) {
      status = { ...base, state: "unavailable", qualification: emptyQualification(policy), nextAction: "inspect-private-evidence" };
    } else {
      const current = checked < expires;
      const qualification = { present: true, current, eligibleProfiles: eligible, requiredProfiles: required, expiresAt: receipt.expiresAt, maxContextTokens: receipt.envelope.maxContextTokens, maxOutputTokens: receipt.envelope.maxOutputTokens, exactUsage: true };
      if (!current) status = { ...base, state: "expired", qualification, nextAction: "renew-qualification" };
      else if (required.some((profile) => !eligible.includes(profile))) status = { ...base, state: "degraded", qualification, nextAction: "review-and-bind" };
      else if (expires - checked <= 7 * 86400000) status = { ...base, state: "renew-soon", qualification, nextAction: "renew-qualification" };
      else if (policy.enabled) status = { ...base, state: "ready", qualification, nextAction: "ready" };
      else status = { ...base, state: "ready-disabled", qualification, nextAction: "enable-policy-when-ready" };
    }
  }
  const errors = validateWorkModelOperatorStatus(status);
  if (errors.length) fail(`model operator status is invalid: ${errors[0]}`);
  return Object.freeze(status);
}

export async function runModelPolicyCommand(argv, dependencies = {}) {
  const options = parseArguments(argv), now = dependencies.now ?? new Date();
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < 0 || !Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("local-model policy command environment is invalid");
  const policy = await readPrivateJson(options.policyPath, MAX_POLICY_BYTES, "private work policy", expectedOwnerUid);
  checkPolicy(policy);
  if (options.command === "status") {
    if (!policy.localModel.prepared) return operatorStatus(policy, null, now, "absent");
    let receipt;
    try { receipt = await readPrivateJson(policy.localModel.qualification.receiptPath, MAX_RECEIPT_BYTES, "private model qualification", expectedOwnerUid); }
    catch { return operatorStatus(policy, null, now, "unavailable"); }
    return operatorStatus(policy, receipt, now);
  }
  if (["enable-review", "enable-apply"].includes(options.command)) {
    if (!policy.localModel.prepared) fail("private work policy is not model-prepared");
    const receipt = await readPrivateJson(policy.localModel.qualification.receiptPath, MAX_RECEIPT_BYTES, "private model qualification", expectedOwnerUid);
    const proposed = enableProposal(policy, receipt, now);
    if (options.command === "enable-review") return proposed.review;
    if (proposed.review.proposedPolicySha256 !== options.confirmation) fail("confirmation differs from the exact proposed enabled private policy");
    await writePrivateNew(options.outputPath, proposed.enabled, expectedOwnerUid);
    return Object.freeze({
      schemaVersion: 1, operation: "pixel-work-model-policy-enable-apply", status: "enabled-policy-written",
      outputName: basename(options.outputPath), currentPolicySha256: proposed.review.currentPolicySha256,
      proposedPolicySha256: proposed.review.proposedPolicySha256,
      enabledProfiles: [...proposed.review.enabledProfiles], qualificationExpiresAt: proposed.review.qualificationExpiresAt,
      changes: { ...proposed.review.changes }, authority: { ...authority }, boundary: enableBoundary,
    });
  }
  const receipt = await readPrivateJson(options.receiptPath, MAX_RECEIPT_BYTES, "private model qualification", expectedOwnerUid);
  const proposed = proposal(policy, receipt, options.receiptPath, now);
  if (options.command === "review") return proposed.review;
  if (proposed.review.proposedPolicySha256 !== options.confirmation) fail("confirmation differs from the exact proposed private policy");
  await writePrivateNew(options.outputPath, proposed.prepared, expectedOwnerUid);
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-model-policy-apply", status: "prepared-policy-written",
    outputName: basename(options.outputPath), proposedPolicySha256: proposed.review.proposedPolicySha256,
    qualificationExpiresAt: proposed.review.expiresAt,
    changes: { ...proposed.review.changes }, authority: { ...authority }, boundary: reviewBoundary,
  });
}

export async function main(argv = process.argv.slice(2)) {
  const result = await runModelPolicyCommand(argv);
  process.stdout.write(`${JSON.stringify(result)}\n`);
  if (result.operation === "pixel-work-model-status" && !["ready", "ready-disabled", "renew-soon"].includes(result.state)) process.exitCode = 2;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-work-model-policy: ${error instanceof WorkModelPolicyCliError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
