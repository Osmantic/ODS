import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { stageConfiguration } from "../host/native_ingress_config.mjs";
import { readGatewayConfiguration } from "../host/pixel_ingress.mjs";

const hash = (body) => crypto.createHash("sha256").update(body).digest("hex");
const body = (token) => Buffer.from(JSON.stringify({ gateway: { auth: { token } } }));
function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "native-config-test-"));
  fs.chmodSync(dir, 0o700);
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return dir;
}

test("refresh retains a usable previous selection and identical staging is idempotent", t => {
  const dir = fixture(t), previous = body("synthetic-previous-token"), next = body("synthetic-next-token");
  assert.deepEqual(stageConfiguration(dir, previous, hash(previous)), { sha256: hash(previous), reused: false });
  const old = path.join(dir, hash(previous) + ".json");
  const inode = fs.statSync(old).ino;
  stageConfiguration(dir, next, hash(next));
  assert.equal(readGatewayConfiguration(path.join(dir, hash(next) + ".json")).token, "synthetic-next-token");
  assert.equal(readGatewayConfiguration(old).token, "synthetic-previous-token");
  assert.equal(fs.statSync(old).ino, inode);
  assert.deepEqual(stageConfiguration(dir, previous, hash(previous)), { sha256: hash(previous), reused: true });
  assert.equal(fs.statSync(old).mode & 0o777, 0o600);
  assert.equal(fs.statSync(old).nlink, 1);
  assert.equal(fs.statSync(old).uid, process.geteuid());
});

test("staging preserves runtime metadata consumed by the strict production reader", t => {
  const dir = fixture(t);
  const raw = Buffer.from(JSON.stringify({ gateway: { auth: { token: "synthetic-token" } },
    agents: { list: [{ id: "pixel", model: "ods-local/test-model" }] },
    models: { providers: { "ods-local": { models: [{ id: "test-model", name: "ODS Local test-model",
      contextWindow: 32768, maxTokens: 2048, reasoning: false }] } } } }));
  stageConfiguration(dir, raw, hash(raw));
  assert.deepEqual(readGatewayConfiguration(path.join(dir, hash(raw) + ".json")).runtime,
    { model: "test-model", context_length: 32768 });
});

for (const fault of ["wrong-hash", "oversize", "invalid-json", "missing-token", "whitespace-token"]) {
  test(`rejects ${fault} without publishing a snapshot`, t => {
    const dir = fixture(t);
    const raw = fault === "oversize" ? Buffer.alloc(2 * 1024 * 1024 + 1)
      : fault === "invalid-json" ? Buffer.from("{")
      : fault === "missing-token" ? Buffer.from("{}")
      : body(fault === "whitespace-token" ? " surrounding-whitespace " : "synthetic-token");
    assert.throws(() => stageConfiguration(dir, raw, fault === "wrong-hash" ? "a".repeat(64) : hash(raw)));
    assert.deepEqual(fs.readdirSync(dir), []);
  });
}

for (const fault of ["symlink", "mode", "tamper", "directory", "oversize"]) {
  test(`existing ${fault} snapshot fails closed and is never overwritten`, t => {
    const dir = fixture(t), raw = body("synthetic-token"), target = path.join(dir, hash(raw) + ".json");
    if (fault === "symlink") {
      const outside = path.join(dir, "other"); fs.writeFileSync(outside, raw, { mode: 0o600 });
      fs.symlinkSync(outside, target);
    } else if (fault === "directory") fs.mkdirSync(target);
    else fs.writeFileSync(target, fault === "tamper" ? body("other-token") : raw, { mode: fault === "mode" ? 0o644 : 0o600 });
    if (fault === "mode") fs.chmodSync(target, 0o644); // do not let the test runner's private umask erase this fault
    if (fault === "oversize") fs.appendFileSync(target, Buffer.alloc(2 * 1024 * 1024, " "));
    const before = fs.lstatSync(target);
    assert.throws(() => stageConfiguration(dir, raw, hash(raw)));
    assert.equal(fs.lstatSync(target).ino, before.ino);
  });
}

test("strict owner rejection remains unchanged after staging", t => {
  const dir = fixture(t), raw = body("synthetic-token");
  stageConfiguration(dir, raw, hash(raw));
  assert.throws(() => readGatewayConfiguration(path.join(dir, hash(raw) + ".json"), process.geteuid() + 1), /owner mismatch/);
});
