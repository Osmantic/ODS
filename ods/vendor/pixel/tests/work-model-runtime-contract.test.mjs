import assert from "node:assert/strict";
import test from "node:test";

import {
  localModelPromptContract, localModelPromptContractSha256,
  localModelRuntimeContractVersion, localModelToolContract, localModelToolContractSha256,
} from "../deploy/work-controller/model-runtime-contract.mjs";

const SHA_RE = /^[a-f0-9]{64}$/u;

test("local-model qualification hashes the exact four-profile prompt and tool surface", () => {
  const prompts = localModelPromptContract();
  const tools = localModelToolContract();
  assert.equal(localModelRuntimeContractVersion, 1);
  assert.deepEqual(Object.keys(prompts.profiles), ["scout", "builder", "data-lab", "researcher"]);
  assert.deepEqual(Object.keys(tools.profiles), ["scout", "builder", "data-lab", "researcher"]);
  assert.match(localModelPromptContractSha256(), SHA_RE);
  assert.match(localModelToolContractSha256(), SHA_RE);
  assert.notEqual(localModelPromptContractSha256(), localModelToolContractSha256());
  assert.ok(Object.values(prompts.profiles).every((value) => typeof value === "string" && value.length > 100));
  assert.ok(Object.values(tools.profiles).every((value) => Array.isArray(value.tools) && value.tools.length > 0));
});
