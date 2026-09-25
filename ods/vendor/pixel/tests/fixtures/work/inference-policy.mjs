export function fixtureVllmInferencePolicy(overrides = {}) {
  return {
    enforcement: "exact-request-boundary-v1",
    wireApi: "openai-chat-completions",
    temperaturePermille: 700,
    topPPermille: 950,
    topK: 40,
    minPPermille: 50,
    repeatPenaltyPermille: 1100,
    seed: 42,
    reasoningEffort: "backend-default",
    reasoningVisibility: "hidden",
    ...overrides,
  };
}
