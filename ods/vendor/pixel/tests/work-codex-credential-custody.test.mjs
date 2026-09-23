import assert from "node:assert/strict";
import { chmod, link, mkdtemp, readFile, rename, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  getWorkCodexChatgptHome, inspectWorkCodexCredentialCustody, readWorkCodexApiCredential,
  readWorkCodexChatgptAuthCache, replaceWorkCodexChatgptAuthCache, revalidateWorkCodexCredentialCustody,
} from "../deploy/work-codex-provider/credential-custody.mjs";
import { validateWorkCodexCredentialCustody, validateWorkCodexPolicy } from "../scripts/lib/work-contract.mjs";

const baseTime = Date.parse("2026-08-10T23:00:00Z");
const policyTemplate = JSON.parse(await readFile(new URL("../deploy/work-codex-provider/policy.example.json", import.meta.url), "utf8"));

function chatPolicy() { const policy = structuredClone(policyTemplate); policy.enabled = true; policy.taskClasses["structural-review"].enabled = true; return policy; }
function apiPolicy() {
  const policy = chatPolicy(); policy.provider.authMode = "api-key";
  policy.transport.allowedHosts = ["api.openai.com"];
  policy.provider.billing = { mode: "metered", boundary: "Separately billed OpenAI API Platform usage; ChatGPT subscription access does not cover this route.", currency: "USD", inputMicrosPerMillionTokens: 1000000, outputMicrosPerMillionTokens: 2000000, source: "https://openai.com/api/pricing/", asOf: "2026-08-10", maxEstimatedCostMicros: 1000000 };
  policy.credentialCustody = { mode: "broker-private-api-key-file", credentialId: "codex-api-key", maxBytes: 8192, mutableRefresh: false, runtimeAccess: "stdin-only-ephemeral-login", materialProjectedToPixel: false, ambientEnvironment: false };
  return policy;
}

async function privateRoot(t, label) {
  const root = await mkdtemp(join(tmpdir(), `pixel-codex-custody-${label}-`)); t.after(() => rm(root, { recursive: true, force: true })); if (process.platform !== "win32") await chmod(root, 0o700); return root;
}

test("ChatGPT auth cache inspection is private, mutable, content-free, and pathless", async (t) => {
  const root = await privateRoot(t, "chat"), path = join(root, "auth.json"), policy = chatPolicy();
  await writeFile(path, JSON.stringify({ auth_mode: "chatgpt", fixture: "initial-material" }), { mode: 0o600 }); if (process.platform !== "win32") await chmod(path, 0o600);
  const inspected = await inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: "000000000101" });
  assert.deepEqual(validateWorkCodexCredentialCustody(inspected.receipt), []); assert.equal(inspected.receipt.authMode, "chatgpt"); assert.equal(inspected.receipt.mutableRefresh, true);
  assert.equal(JSON.stringify(inspected.handle), "{}"); assert.deepEqual(Reflect.ownKeys(inspected.handle), []); assert.equal(Object.getPrototypeOf(inspected.handle), null); assert.equal(JSON.stringify(inspected.receipt).includes(root), false); assert.equal(JSON.stringify(inspected.receipt).includes("initial-material"), false);
  assert.equal(getWorkCodexChatgptHome({ policy, handle: inspected.handle }), root); await assert.rejects(() => readWorkCodexApiCredential({ policy, handle: inspected.handle }), /cannot be read/u);
  assert.match(await readWorkCodexChatgptAuthCache({ policy, handle: inspected.handle }), /initial-material/u);
  const replacement = join(root, "replacement"); await writeFile(replacement, JSON.stringify({ auth_mode: "chatgpt", fixture: "rotated-material" }), { mode: 0o600 }); if (process.platform !== "win32") await chmod(replacement, 0o600); await rename(replacement, path);
  const revalidated = await revalidateWorkCodexCredentialCustody({ policy, handle: inspected.handle, now: new Date(baseTime + 1000), suffix: "000000000102" });
  assert.equal(revalidated.receipt.status, "available"); assert.equal(JSON.stringify(revalidated.receipt).includes("rotated-material"), false);
  const refresh = join(root, "refresh.json"); await writeFile(refresh, JSON.stringify({ auth_mode: "chatgpt", fixture: "container-refresh" }), { mode: 0o600 }); if (process.platform !== "win32") await chmod(refresh, 0o600);
  const installed = await replaceWorkCodexChatgptAuthCache({ policy, handle: revalidated.handle, replacementPath: refresh, now: new Date(baseTime + 2000), suffix: "000000000105" });
  assert.match(await readWorkCodexChatgptAuthCache({ policy, handle: installed.handle }), /container-refresh/u); assert.equal(JSON.stringify(installed.receipt).includes("container-refresh"), false);
});

test("API credential stays single-link and unchanged between inspection and stdin projection", async (t) => {
  const root = await privateRoot(t, "api"), path = join(root, "provider-key"), policy = apiPolicy(), material = "fixture-provider-material-1234567890";
  await writeFile(path, `${material}\n`, { mode: 0o600 }); if (process.platform !== "win32") await chmod(path, 0o600);
  const inspected = await inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: "000000000103" });
  assert.equal(inspected.receipt.authMode, "api-key"); assert.equal(inspected.receipt.mutableRefresh, false); assert.equal(await readWorkCodexApiCredential({ policy, handle: inspected.handle }), material);
  assert.equal(JSON.stringify(inspected.receipt).includes(material), false); assert.equal(JSON.stringify(inspected.handle), "{}");
  await new Promise((resolve) => setTimeout(resolve, 5)); await writeFile(path, "changed-provider-material-123456789\n", { mode: 0o600 });
  await assert.rejects(() => readWorkCodexApiCredential({ policy, handle: inspected.handle }), /changed after custody inspection/u);
  await assert.rejects(() => revalidateWorkCodexCredentialCustody({ policy, handle: inspected.handle, now: new Date(baseTime + 1000), suffix: "000000000104" }), /changed after custody inspection/u);
});

test("custody rejects linked, broad, malformed, oversized, wrong-name, and wrong-mode material", async (t) => {
  const root = await privateRoot(t, "hostile"), policy = chatPolicy(), path = join(root, "auth.json");
  await writeFile(path, JSON.stringify({ auth_mode: "chatgpt", fixture: "bounded" }), { mode: 0o600 }); if (process.platform !== "win32") await chmod(path, 0o600);
  await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: join(root, "wrong.json"), now: new Date(baseTime), suffix: "000000000105" }), /filename/u);
  await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: "auth.json", now: new Date(baseTime), suffix: "000000000105" }), /inputs/u);
  if (process.platform !== "win32") {
    await chmod(path, 0o644); await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: "000000000105" }), /owner-only/u); await chmod(path, 0o600);
    const linked = join(root, "linked"); await link(path, linked); await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: "000000000105" }), /single-link/u); await rm(linked);
    const outside = await privateRoot(t, "outside"), target = join(outside, "auth.json"); await writeFile(target, JSON.stringify({ auth_mode: "chatgpt" }), { mode: 0o600 }); await chmod(target, 0o600);
    const linkRoot = join(root, "linked-parent"); await symlink(outside, linkRoot, "dir"); await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: join(linkRoot, "auth.json"), now: new Date(baseTime), suffix: "000000000105" }), /link or junction|private directory/u);
  }
  await writeFile(path, "{}", { mode: 0o600 }); await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: "000000000105" }), /wrong authentication mode/u);
  await writeFile(path, "{x", { mode: 0o600 }); await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: "000000000105" }), /not valid JSON/u);
  await writeFile(path, "X".repeat(policy.credentialCustody.maxBytes + 1), { mode: 0o600 }); await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: "000000000105" }), /bounded single-link/u);
  const mismatched = chatPolicy(); mismatched.provider.authMode = "api-key"; assert.ok(validateWorkCodexPolicy(mismatched).some((error) => /custody|billing/u.test(error)));
});

test("API custody rejects whitespace, controls, empty lines, ChatGPT projection, and forged handles", async (t) => {
  const root = await privateRoot(t, "api-hostile"), path = join(root, "provider-key"), policy = apiPolicy();
  for (const [index, material] of ["short", "fixture material with spaces 123456", "fixture-material-123456\nextra", "fixture-material-123456\u0000tail"].entries()) {
    await writeFile(path, material, { mode: 0o600 }); if (process.platform !== "win32") await chmod(path, 0o600);
    await assert.rejects(() => inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: (200 + index).toString(16).padStart(12, "0") }), /invalid bounded one-line shape|NUL/u);
  }
  await writeFile(path, "fixture-provider-material-1234567890\n", { mode: 0o600 });
  const inspected = await inspectWorkCodexCredentialCustody({ policy, credentialPath: path, now: new Date(baseTime), suffix: "000000000109" });
  assert.throws(() => getWorkCodexChatgptHome({ policy, handle: inspected.handle }), /no persistent ChatGPT/u);
  await assert.rejects(() => readWorkCodexChatgptAuthCache({ policy, handle: inspected.handle }), /no ChatGPT/u);
  await assert.rejects(() => readWorkCodexApiCredential({ policy, handle: {} }), /handle differs/u);
});
