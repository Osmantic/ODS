import { posix } from "node:path";

const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9_.:+/-]{0,127}$/u;
const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,127}$/u;
const SAFE_PATH_RE = /^\/[A-Za-z0-9._/-]+$/u;
const PROVIDERS = new Set(["llama.cpp", "vllm"]);
const CONTAINER_REGISTRY_PATH = "/tmp/agent/models.yml";

export class OmpModelRegistryError extends Error {}

function fail(message) {
  throw new OmpModelRegistryError(message);
}

function safePath(value, label) {
  if (typeof value !== "string" || !posix.isAbsolute(value) || !SAFE_PATH_RE.test(value) || value.includes("//") || posix.normalize(value) !== value) {
    fail(`${label} is not a canonical safe Linux path`);
  }
  return value;
}

function positiveId(value, label) {
  if (!Number.isSafeInteger(value) || value < 1 || value > 2147483647) fail(`${label} is invalid`);
  return value;
}

function exactRuntime(prepared, runtime) {
  const model = prepared?.plan?.model;
  const local = prepared?.policy?.localModel;
  if (
    !model || !local || !PROVIDERS.has(model.provider)
    || model.provider !== local.provider || !MODEL_RE.test(model.id ?? "") || runtime?.modelId !== model.id
    || !Number.isSafeInteger(model.contextWindow) || model.contextWindow < 1024 || model.contextWindow > 2000000
    || typeof model.supportsVision !== "boolean" || local.id !== model.id
    || !Number.isSafeInteger(local.contextWindow) || local.contextWindow < model.contextWindow || local.contextWindow > 2000000
    || !Number.isSafeInteger(local.maxRequestOutputTokens) || local.maxRequestOutputTokens < 1 || local.maxRequestOutputTokens > 500000000
    || local.supportsVision !== model.supportsVision
  ) fail("OMP model registry differs from the admitted local model");
  if (!NAME_RE.test(runtime?.modelAlias ?? "")) fail("OMP model proxy alias is invalid");
  safePath(runtime?.modelRegistryPath, "OMP model registry path");
  positiveId(runtime?.uid, "OMP runtime UID");
  positiveId(runtime?.gid, "OMP runtime GID");
  return { model, local };
}

export function buildOmpModelRegistry(prepared, runtime) {
  const { model, local } = exactRuntime(prepared, runtime);
  const api = model.provider === "vllm" ? "openai-completions" : "openai-responses";
  const baseUrl = model.provider === "vllm" ? `http://${runtime.modelAlias}:8080/v1` : `http://${runtime.modelAlias}:8080`;
  return {
    providers: {
      "llama.cpp": {
        baseUrl,
        auth: "none",
        api,
        models: [{
          id: model.id,
          name: model.id,
          api,
          reasoning: model.provider === "vllm",
          input: model.supportsVision ? ["text", "image"] : ["text"],
          cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
          contextWindow: model.contextWindow,
          maxTokens: local.maxRequestOutputTokens,
        }],
      },
    },
  };
}

export function ompModelRegistryDockerArgs(prepared, runtime, tmpMiB) {
  exactRuntime(prepared, runtime);
  if (!Number.isSafeInteger(tmpMiB) || tmpMiB < 64 || tmpMiB > 1024) fail("OMP agent tmpfs budget is invalid");
  return [
    "--tmpfs", `/tmp/agent:rw,nosuid,nodev,noexec,size=${tmpMiB}m,mode=0700,uid=${runtime.uid},gid=${runtime.gid}`,
    "--mount", `type=bind,src=${runtime.modelRegistryPath},dst=${CONTAINER_REGISTRY_PATH},readonly`,
  ];
}

export const ompModelRegistryContainerPath = CONTAINER_REGISTRY_PATH;
