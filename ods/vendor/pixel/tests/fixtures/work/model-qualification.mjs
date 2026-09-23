import { chmod, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { evaluateModelQualification, modelCapabilityReceiptSha256 } from "../../../deploy/work-controller/model-qualification.mjs";

const categories = Object.freeze([
  "argument-fidelity", "context-retention", "recovery-discipline",
  "structured-output", "tool-selection", "usage-accounting",
]);
const profiles = Object.freeze(["assistant", "scout", "builder", "data-lab", "researcher"]);

export async function installFixtureModelQualification(policy, root, now = new Date("2026-08-10T13:00:00Z"), { failingProfile = null } = {}) {
  const model = {
    provider: policy.localModel.provider,
    id: policy.localModel.id,
    modelArtifactSha256: policy.localModel.modelArtifactSha256,
    backendImageDigest: policy.localModel.imageDigest,
    backendVersion: policy.localModel.backendVersion,
    acceleratorClass: policy.localModel.acceleratorClass,
    promptContractSha256: policy.localModel.promptContractSha256,
    toolSchemaSha256: policy.localModel.toolSchemaSha256,
    contextWindow: policy.localModel.contextWindow,
    supportsVision: policy.localModel.supportsVision,
  };
  const cases = categories.map((category, index) => ({
    id: `fixture-${category}`,
    category,
    profiles: [...profiles],
    caseSha256: String(index + 1).repeat(64),
    passed: true,
    failureClass: "pass",
    intentMutationObserved: false,
    inputTokens: category === "context-retention" ? policy.localModel.contextWindow : 128,
    outputTokens: category === "structured-output" ? policy.localModel.maxRequestOutputTokens + 256 : 16,
    usageSource: "backend-observed",
    latencyMs: 10,
    contextTokensTested: category === "context-retention" ? policy.localModel.contextWindow : 128,
    outputTokensRequired: category === "structured-output" ? policy.localModel.maxRequestOutputTokens : 16,
  }));
  if (failingProfile !== null) {
    if (!profiles.includes(failingProfile)) throw new Error("fixture failing profile is invalid");
    cases.push({
      id: `fixture-${failingProfile}-tool-selection-regression`, category: "tool-selection",
      profiles: [failingProfile], caseSha256: "7".repeat(64), passed: false, failureClass: "tool-name-mismatch",
      intentMutationObserved: false, inputTokens: 128, outputTokens: 16,
      usageSource: "backend-observed", latencyMs: 10, contextTokensTested: 128,
      outputTokensRequired: 16,
    });
  }
  const observedAt = new Date(now.getTime() - 60000);
  const receipt = evaluateModelQualification({
    model, cases, evaluatorSha256: "e".repeat(64), observedAt,
    expiresAt: new Date(observedAt.getTime() + 7 * 86400000), suffix: "f123456789ab",
  });
  const path = join(root, "model-qualification.json");
  await writeFile(path, `${JSON.stringify(receipt)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(path, 0o600);
  policy.localModel.qualification = {
    receiptPath: path,
    receiptSha256: modelCapabilityReceiptSha256(receipt),
    casesSha256: receipt.suite.casesSha256,
    evaluatorSha256: receipt.suite.evaluatorSha256,
  };
  return { path, receipt };
}
