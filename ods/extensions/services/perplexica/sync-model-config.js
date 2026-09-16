"use strict";

const configUrl = process.env.PERPLEXICA_CONFIG_URL || "http://127.0.0.1:3000/api/config";
const mode = String(process.env.ODS_MODE || "").toLowerCase();
const switchboardMode = String(process.env.ODS_MODEL_SWITCHBOARD || "").toLowerCase();
const runtime = String(
  process.env.AMD_INFERENCE_RUNTIME || process.env.LLM_BACKEND || mode,
).toLowerCase();
const lemonade = runtime === "lemonade" || mode === "lemonade";
const ggufFile = String(process.env.GGUF_FILE || "").trim();
const model = String(
  switchboardMode === "enabled"
    ? "ods/current"
    : mode === "cloud"
    ? "default"
    : lemonade
      ? process.env.LEMONADE_MODEL || (ggufFile ? `extra.${ggufFile}` : "")
      : ggufFile || process.env.LLM_MODEL || "",
).trim();
function normalizeOpenAIBaseURL(value) {
  const trimmed = String(value || "").trim().replace(/\/+$/, "");
  if (!trimmed) {
    return "";
  }
  return /\/(?:api\/)?v1$/i.test(trimmed) ? trimmed : `${trimmed}/v1`;
}

const baseURL = normalizeOpenAIBaseURL(process.env.OPENAI_BASE_URL);
const apiKey = String(process.env.OPENAI_API_KEY || "no-key");

if (!model || !baseURL) {
  process.exit(0);
}

async function request(url, payload) {
  const response = await fetch(url, payload === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  const text = await response.text();
  return text ? JSON.parse(text) : {};
}

async function update() {
  const current = await request(configUrl);
  const values = current && current.values;
  if (!values || !Array.isArray(values.modelProviders)) {
    throw new Error("config response is missing modelProviders");
  }
  const provider = values.modelProviders.find((entry) => entry && entry.type === "openai");
  if (!provider || !provider.id) {
    return;
  }
  const providerBefore = JSON.stringify(provider);
  provider.chatModels = [{ key: model, name: model }];
  provider.config = { ...(provider.config || {}), baseURL, apiKey };
  const preferences = {
    ...(values.preferences || {}),
    defaultChatModel: model,
    defaultChatProvider: provider.id,
  };
  // Perplexica's API needs both defaults even when its UI advertises built-in
  // embedding models. Initialize only an unselected pair, preserving owners'
  // explicit choices and never inventing a provider or a remote dependency.
  const unset = (value) => value === null || value === undefined || value === "";
  const missingProvider = unset(preferences.defaultEmbeddingProvider);
  const missingModel = unset(preferences.defaultEmbeddingModel);
  if (missingProvider && missingModel) {
    const key = "Xenova/all-MiniLM-L6-v2";
    const embeddingProvider = values.modelProviders.find((entry) =>
      entry?.type === "transformers" && typeof entry.id === "string" && entry.id.trim() &&
      Array.isArray(entry.embeddingModels) && entry.embeddingModels.some((candidate) => candidate?.key === key));
    if (embeddingProvider) {
      preferences.defaultEmbeddingProvider = embeddingProvider.id;
      preferences.defaultEmbeddingModel = key;
    } else {
      process.stderr.write("[ods-perplexica] local embedding default unavailable; leaving embedding preferences unchanged\n");
    }
  } else if (missingProvider !== missingModel) {
    process.stderr.write("[ods-perplexica] incomplete embedding selection; leaving embedding preferences unchanged\n");
  }

  // The API includes built-in model entries in its read response. Persisting
  // that hydrated catalog on every start can accumulate duplicate entries.
  // Initializing embedding preferences does not require rewriting providers.
  if (JSON.stringify(provider) !== providerBefore) {
    await request(configUrl, { key: "modelProviders", value: values.modelProviders });
  }
  if (JSON.stringify(preferences) !== JSON.stringify(values.preferences || {})) {
    await request(configUrl, { key: "preferences", value: preferences });
  }

  const verified = (await request(configUrl)).values || {};
  const verifiedProvider = (verified.modelProviders || []).find(
    (entry) => entry && entry.type === "openai",
  );
  const models = verifiedProvider && verifiedProvider.chatModels;
  if (
    !verifiedProvider
    || !Array.isArray(models)
    || !models.some((entry) => entry && (entry.key === model || entry.name === model))
    || !verifiedProvider.config
    || verifiedProvider.config.baseURL !== baseURL
    || verifiedProvider.config.apiKey !== apiKey
    || !verified.preferences
    || verified.preferences.defaultChatModel !== model
    || verified.preferences.defaultChatProvider !== verifiedProvider.id
  ) {
    throw new Error("active model route did not persist");
  }
  for (const key of ["defaultEmbeddingProvider", "defaultEmbeddingModel"]) {
    if (verified.preferences[key] !== preferences[key]) {
      throw new Error("embedding preferences did not persist");
    }
  }
  process.stdout.write(`${model}\n`);
}

update().catch((error) => {
  process.stderr.write(`[ods-perplexica] model-route sync failed: ${error.message}\n`);
  process.exitCode = 1;
});
