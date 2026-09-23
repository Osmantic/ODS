import assert from "node:assert/strict";
import test from "node:test";

import { createWorkProviderReceipt, listWorkProviderProfiles, resolveWorkProvider, WorkProviderRegistryError } from "../deploy/work-provider/provider-registry.mjs";
import { validateWorkProviderProfile, validateWorkProviderReceipt } from "../scripts/lib/work-contract.mjs";

test("provider registry is closed, validated, and remote-disabled by default", () => {
  const profiles = listWorkProviderProfiles();
  assert.deepEqual(profiles.map((profile) => profile.id), ["anthropic", "fireworks", "groq", "local", "moonshot-kimi", "openai", "openrouter", "together"]);
  assert.equal(new Set(profiles.map((profile) => profile.profileSha256)).size, profiles.length);
  for (const profile of profiles) {
    const { profileSha256, ...publicProfile } = profile;
    assert.match(profileSha256, /^[a-f0-9]{64}$/u);
    assert.deepEqual(validateWorkProviderProfile(publicProfile), []);
    assert.equal(profile.remote ? profile.enabledByDefault : !profile.enabledByDefault, false);
    if (profile.remote) {
      assert.match(profile.baseUrl, /^https:\/\//u);
      assert.ok(profile.allowedHosts.length > 0);
      assert.throws(() => resolveWorkProvider(profile.id), WorkProviderRegistryError);
    }
  }
  assert.equal(resolveWorkProvider("local").profile.protocol, "local-openai-compatible");
  assert.throws(() => resolveWorkProvider("unknown"), WorkProviderRegistryError);
});

test("one exact owner-private allowlist can enable only a known remote profile", () => {
  const kimi = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });
  assert.equal(kimi.profile.baseUrl, "https://api.moonshot.ai/v1");
  assert.deepEqual(kimi.profile.allowedHosts, ["api.moonshot.ai:443"]);
  assert.equal(kimi.profile.defaultModel, "kimi-k3");
  assert.throws(() => resolveWorkProvider("openai", { enabledRemoteProviders: ["moonshot-kimi"] }), /disabled/u);
  assert.throws(() => resolveWorkProvider("openai", { enabledRemoteProviders: ["local"] }), /invalid/u);
  assert.throws(() => resolveWorkProvider("openai", { enabledRemoteProviders: ["openai", "openai"] }), /duplicates/u);
  assert.equal(Object.isFrozen(kimi.profile.allowedHosts), true);
  assert.throws(() => kimi.profile.allowedHosts.push("example.com:443"), TypeError);
});

test("provider receipts are content-free and bind the exact profile", () => {
  const provider = resolveWorkProvider("moonshot-kimi", { enabledRemoteProviders: ["moonshot-kimi"] });
  const receipt = createWorkProviderReceipt({ provider, phase: "completed", status: "succeeded", requestCount: 2, inputTokens: 1234, outputTokens: 567, now: new Date("2026-08-21T16:00:00Z"), suffix: "abcdef123456" });
  assert.deepEqual(validateWorkProviderReceipt(receipt), []);
  assert.equal(receipt.providerContentIncluded, false);
  assert.equal(receipt.credentialIncluded, false);
  assert.equal(JSON.stringify(receipt).includes("reasoning_content"), false);
  assert.equal(JSON.stringify(receipt).includes("api.moonshot.ai"), false);
  for (const [phase, status] of [["failed", "succeeded"], ["completed", "error"], ["admitted", "succeeded"], ["paused", "accepted"]]) {
    assert.ok(validateWorkProviderReceipt({ ...receipt, phase, status }).some((error) => error.includes("contradicts")));
  }
});
