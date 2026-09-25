import assert from "node:assert/strict";
import test from "node:test";

import {
  buildOmpModelRegistry, ompModelRegistryContainerPath, ompModelRegistryDockerArgs, OmpModelRegistryError,
} from "../deploy/work-runner/omp-model-registry.mjs";

function fixture() {
  return {
    prepared: {
      plan: { model: { provider: "vllm", id: "DeepSeek-V4-Flash-0731", contextWindow: 131072, supportsVision: false } },
      policy: { localModel: { provider: "vllm", id: "DeepSeek-V4-Flash-0731", contextWindow: 1048576, supportsVision: false, maxRequestOutputTokens: 16384 } },
    },
    runtime: {
      modelId: "DeepSeek-V4-Flash-0731", modelAlias: "pixel-model", uid: 900, gid: 900,
      modelRegistryPath: "/var/lib/pixel-work/runs/workclaim-1786366800000-abcdef123456/config/models.yml",
    },
  };
}

test("OMP registry binds the admitted model to the credential-free local proxy", () => {
  const value = fixture();
  assert.deepEqual(buildOmpModelRegistry(value.prepared, value.runtime), {
    providers: {
      "llama.cpp": {
        baseUrl: "http://pixel-model:8080/v1", auth: "none", api: "openai-completions",
        models: [{
          id: "DeepSeek-V4-Flash-0731", name: "DeepSeek-V4-Flash-0731", api: "openai-completions",
          reasoning: true, input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: 131072, maxTokens: 16384,
        }],
      },
    },
  });
  assert.equal(ompModelRegistryContainerPath, "/tmp/agent/models.yml");
  assert.deepEqual(ompModelRegistryDockerArgs(value.prepared, value.runtime, 512), [
    "--tmpfs", "/tmp/agent:rw,nosuid,nodev,noexec,size=512m,mode=0700,uid=900,gid=900",
    "--mount", `type=bind,src=${value.runtime.modelRegistryPath},dst=/tmp/agent/models.yml,readonly`,
  ]);
});

test("OMP registry supports legacy local backends without claiming reasoning or credentials", () => {
  const value = fixture();
  value.prepared.plan.model.provider = "llama.cpp";
  value.prepared.policy.localModel.provider = "llama.cpp";
  value.prepared.plan.model.supportsVision = true;
  value.prepared.policy.localModel.supportsVision = true;
  const registry = buildOmpModelRegistry(value.prepared, value.runtime);
  assert.equal(registry.providers["llama.cpp"].models[0].reasoning, false);
  assert.deepEqual(registry.providers["llama.cpp"].models[0].input, ["text", "image"]);
  assert.equal(registry.providers["llama.cpp"].baseUrl, "http://pixel-model:8080");
  assert.equal(registry.providers["llama.cpp"].api, "openai-responses");
  assert.equal(registry.providers["llama.cpp"].models[0].api, "openai-responses");
  assert.equal(JSON.stringify(registry).includes("apiKey"), false);
});

test("OMP registry rejects model, proxy, path, identity, and policy widening", () => {
  for (const mutate of [
    (value) => { value.runtime.modelId = "other"; },
    (value) => { value.runtime.modelAlias = "pixel-model:8080"; },
    (value) => { value.runtime.modelRegistryPath = "/var/lib/pixel-work/../escape/models.yml"; },
    (value) => { value.runtime.uid = 0; },
    (value) => { value.prepared.policy.localModel.provider = "llama.cpp"; },
    (value) => { value.prepared.policy.localModel.maxRequestOutputTokens = 0; },
  ]) {
    const value = fixture();
    mutate(value);
    assert.throws(() => buildOmpModelRegistry(value.prepared, value.runtime), OmpModelRegistryError);
  }
  const value = fixture();
  assert.throws(() => ompModelRegistryDockerArgs(value.prepared, value.runtime, 16), OmpModelRegistryError);
  assert.throws(() => ompModelRegistryDockerArgs(value.prepared, value.runtime, 2048), OmpModelRegistryError);
});
