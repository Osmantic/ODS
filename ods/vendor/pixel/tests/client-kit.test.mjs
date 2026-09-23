import assert from "node:assert/strict";
import { chmod, mkdtemp, readFile, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";

const root = resolve(import.meta.dirname, "..");
const script = resolve(root, "scripts/client-kit.mjs");

function run(...arguments_) {
  return spawnSync(process.execPath, [script, ...arguments_], {
    cwd: root, encoding: "utf8", windowsHide: true,
  });
}

async function generated() {
  const directory = await mkdtemp(join(tmpdir(), "pixel-client-kit-"));
  if (process.platform !== "win32") await chmod(directory, 0o700);
  const overlay = join(directory, "overlay.json");
  const result = run("generate", "--output", overlay, "--client-id", "fixture-client", "--profile", "research");
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), {
    status: "generated-draft", licensingEvidenceRecorded: false,
    usabilityEvidenceRecorded: false, ready: false,
  });
  return { directory, overlay };
}

test("client overlay generation is local-first, MFA-bound, and not falsely ready", async () => {
  const { overlay } = await generated();
  const value = JSON.parse(await readFile(overlay, "utf8"));
  assert.equal(value.core.modificationAllowed, false);
  assert.equal(value.frontier.advertisedAuthModes[0], "chatgpt");
  assert.equal(value.frontier.apiKeyModeAdvertised, false);
  assert.equal(value.operatorAccess.remoteAccessEnabled, false);
  assert.equal(value.operatorAccess.remoteAccessPolicy.preferredFactor, "totp-authenticator-app");
  assert.equal(value.operatorAccess.remoteAccessPolicy.fallbackFactor, "email-one-time-code");
  assert.equal(value.operatorAccess.remoteAccessPolicy.credentialsVisibleToPixel, false);
  const validation = run("validate", "--overlay", overlay);
  assert.equal(validation.status, 0, validation.stderr);
  assert.deepEqual(JSON.parse(validation.stdout), {
    status: "valid-draft", ready: false, blockedGates: ["licensing", "usability"],
  });
});

test("client overlay readiness requires exact licensing and usability evidence", async () => {
  const { overlay } = await generated();
  const value = JSON.parse(await readFile(overlay, "utf8"));
  value.licensing = { required: true, recorded: true, evidenceSha256: "a".repeat(64) };
  value.usability = { required: true, recorded: true, evidenceSha256: "b".repeat(64) };
  await writeFile(overlay, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  if (process.platform !== "win32") await chmod(overlay, 0o600);
  const result = run("validate", "--overlay", overlay);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), { status: "ready", ready: true, blockedGates: [] });
});

test("client overlay rejects remote exposure, weakened MFA, API advertising, and unknown fields", async () => {
  for (const mutate of [
    (value) => { value.operatorAccess.remoteAccessEnabled = true; },
    (value) => { value.operatorAccess.remoteAccessPolicy.preferredFactor = "email-one-time-code"; },
    (value) => { value.frontier.apiKeyModeAdvertised = true; },
    (value) => { value.privatePath = "/client/secret"; },
  ]) {
    const { overlay } = await generated();
    const value = JSON.parse(await readFile(overlay, "utf8"));
    mutate(value);
    await writeFile(overlay, `${JSON.stringify(value)}\n`, { mode: 0o600 });
    if (process.platform !== "win32") await chmod(overlay, 0o600);
    const result = run("validate", "--overlay", overlay);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /failed schema validation/);
  }
});

test("client overlay refuses overwrite, relative paths, and linked private state", async (context) => {
  const { directory, overlay } = await generated();
  const duplicate = run("generate", "--output", overlay, "--client-id", "fixture-client");
  assert.notEqual(duplicate.status, 0);
  const relative = run("validate", "--overlay", "overlay.json");
  assert.notEqual(relative.status, 0);
  if (process.platform === "win32") return context.skip("symlink creation is not generally available on Windows");
  const linked = join(directory, "linked.json");
  await symlink(overlay, linked);
  const result = run("validate", "--overlay", linked);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /descriptor-bound regular single-link file/);
});
