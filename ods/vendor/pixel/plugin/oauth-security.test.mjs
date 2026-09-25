import assert from "node:assert/strict";
import { chmod, lstat, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { GOOGLE_AUTH_URI, GOOGLE_TOKEN_URI, installedGoogleClient, readPrivateJson, writePrivateJson } from "./oauth-security.mjs";

const client = () => ({ installed: { client_id: "client-id", client_secret: "client-secret", auth_uri: GOOGLE_AUTH_URI, token_uri: GOOGLE_TOKEN_URI } });

test("OAuth endpoints are fixed to Google", () => {
  assert.equal(installedGoogleClient(client()).token_uri, GOOGLE_TOKEN_URI);
  assert.throws(() => installedGoogleClient({ installed: { ...client().installed, token_uri: "https://attacker.invalid/token" } }), /fixed Google/);
  assert.throws(() => installedGoogleClient({ installed: { ...client().installed, auth_uri: "https://attacker.invalid/auth" } }), /fixed Google/);
});

test("OAuth credential reads and writes are private and no-follow", async (context) => {
  const root = await mkdtemp(join(tmpdir(), "pixel-oauth-security-"));
  try {
    const token = join(root, "private", "token.json");
    await writePrivateJson(token, { refresh_token: "fixture" });
    assert.deepEqual(await readPrivateJson(token), { refresh_token: "fixture" });
    if (process.platform !== "win32") {
      assert.equal((await lstat(token)).mode & 0o777, 0o600);
      assert.equal((await lstat(join(root, "private"))).mode & 0o777, 0o700);
      await chmod(token, 0o644);
      await assert.rejects(readPrivateJson(token), /not private/);
      await chmod(token, 0o600);
    }
    const outside = join(root, "outside.json");
    const linked = join(root, "linked.json");
    await writeFile(outside, "untouched\n");
    try { await symlink(outside, linked); }
    catch { context.skip("symlinks are unavailable on this host"); return; }
    await assert.rejects(readPrivateJson(linked));
    await writePrivateJson(linked, { safe: true });
    assert.equal(await readFile(outside, "utf8"), "untouched\n");
    assert.deepEqual(JSON.parse(await readFile(linked, "utf8")), { safe: true });
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
