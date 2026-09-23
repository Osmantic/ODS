import assert from "node:assert/strict";
import { chmod, link, mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { buildModelArtifactManifest } from "../deploy/work-controller/model-artifact.mjs";
import { buildModelRuntimeCacheManifest } from "../deploy/work-controller/model-runtime-cache-artifact.mjs";
import { validateWorkModelArtifactManifest, validateWorkModelRuntimeCacheManifest } from "../scripts/lib/work-contract.mjs";

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-model-artifact-"));
  if (process.platform !== "win32") await chmod(root, 0o700);
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

test("single-file model identity is the exact raw byte digest", async (t) => {
  const root = await fixture(t), path = join(root, "model.gguf");
  await writeFile(path, "exact-model-bytes", { mode: 0o600 });
  const manifest = await buildModelArtifactManifest({ sourcePath: path, kind: "file" });
  assert.deepEqual(validateWorkModelArtifactManifest(manifest), []);
  assert.equal(manifest.artifactSha256, "76e2328f2eedf89c41124f02a920851ef8d6f7dbbec2fb3a7573f062a557e44a");
  assert.deepEqual(manifest.files, [{ relativePath: "model", bytes: 17, sha256: manifest.artifactSha256 }]);
  assert.equal(manifest.authority.startsContainer, false);
});

test("directory identity is deterministic across creation order and changes with any byte", async (t) => {
  const root = await fixture(t), left = join(root, "left"), right = join(root, "right");
  await mkdir(join(left, "weights"), { recursive: true, mode: 0o700 });
  await writeFile(join(left, "tokenizer.json"), "tokenizer", { mode: 0o600 });
  await writeFile(join(left, "weights", "model-00002.safetensors"), "second", { mode: 0o600 });
  await writeFile(join(left, "weights", "model-00001.safetensors"), "first", { mode: 0o600 });
  await mkdir(join(right, "weights"), { recursive: true, mode: 0o700 });
  await writeFile(join(right, "weights", "model-00001.safetensors"), "first", { mode: 0o600 });
  await writeFile(join(right, "weights", "model-00002.safetensors"), "second", { mode: 0o600 });
  await writeFile(join(right, "tokenizer.json"), "tokenizer", { mode: 0o600 });
  const first = await buildModelArtifactManifest({ sourcePath: left, kind: "directory" });
  const second = await buildModelArtifactManifest({ sourcePath: right, kind: "directory" });
  assert.deepEqual(validateWorkModelArtifactManifest(first), []);
  assert.equal(first.artifactSha256, second.artifactSha256);
  assert.deepEqual(first.files.map((entry) => entry.relativePath), ["tokenizer.json", "weights/model-00001.safetensors", "weights/model-00002.safetensors"]);
  await writeFile(join(right, "tokenizer.json"), "changed", { mode: 0o600 });
  const changed = await buildModelArtifactManifest({ sourcePath: right, kind: "directory" });
  assert.notEqual(changed.artifactSha256, first.artifactSha256);
});

test("runtime cache identity reuses exact stable-tree measurement but cannot masquerade as model bytes", async (t) => {
  const root = await fixture(t), source = join(root, "cache");
  await mkdir(join(source, "vllm"), { recursive: true, mode: 0o700 });
  await writeFile(join(source, "vllm", "aot.so"), "compiled-code", { mode: 0o600 });
  const manifest = await buildModelRuntimeCacheManifest({ sourcePath: source });
  assert.deepEqual(validateWorkModelRuntimeCacheManifest(manifest), []);
  assert.notEqual(manifest.$schema, "https://osmantic.com/pixel/schemas/work-model-artifact-manifest-v1.schema.json");
  assert.match(manifest.boundary, /executable model-runtime cache bytes/u);
  assert.notDeepEqual(validateWorkModelArtifactManifest(manifest), []);
});

test("artifact measurement rejects links, hard links, unsafe entries, emptiness, and kind mismatch", async (t) => {
  const root = await fixture(t), source = join(root, "source");
  await mkdir(source, { mode: 0o700 });
  await assert.rejects(buildModelArtifactManifest({ sourcePath: source, kind: "directory" }), /empty/u);
  await writeFile(join(source, "valid.bin"), "data", { mode: 0o600 });
  await assert.rejects(buildModelArtifactManifest({ sourcePath: source, kind: "file" }), /not a regular file/u);
  await writeFile(join(source, "unsafe name.bin"), "data", { mode: 0o600 });
  await assert.rejects(buildModelArtifactManifest({ sourcePath: source, kind: "directory" }), /unsafe relative path/u);
  await rm(join(source, "unsafe name.bin"));
  const linked = join(source, "linked.bin");
  await link(join(source, "valid.bin"), linked);
  await assert.rejects(buildModelArtifactManifest({ sourcePath: source, kind: "directory" }), /single-link/u);
  await rm(linked);
  if (process.platform !== "win32") {
    await symlink(join(source, "valid.bin"), linked);
    await assert.rejects(buildModelArtifactManifest({ sourcePath: source, kind: "directory" }), /symbolic link/u);
  }
});

test("manifest validation rejects reordered, substituted, and contradictory identities", async (t) => {
  const root = await fixture(t), source = join(root, "model");
  await mkdir(source, { mode: 0o700 });
  await writeFile(join(source, "a.bin"), "a", { mode: 0o600 });
  await writeFile(join(source, "b.bin"), "b", { mode: 0o600 });
  const manifest = await buildModelArtifactManifest({ sourcePath: source, kind: "directory" });
  for (const mutate of [
    (copy) => { copy.files.reverse(); },
    (copy) => { copy.files[0].sha256 = "0".repeat(64); },
    (copy) => { copy.fileCount += 1; },
    (copy) => { copy.totalBytes += 1; },
    (copy) => { copy.artifactSha256 = "0".repeat(64); },
  ]) {
    const hostile = structuredClone(manifest); mutate(hostile);
    assert.notDeepEqual(validateWorkModelArtifactManifest(hostile), [], mutate.toString());
  }
});

test("supported-host measurement rejects bytes unreadable by the configured container identity", { skip: process.platform === "win32" ? "POSIX mode and identity qualification requires POSIX" : false }, async (t) => {
  const root = await fixture(t), path = join(root, "private-model.gguf");
  await writeFile(path, "private model", { mode: 0o600 });
  const uid = process.geteuid(), gid = process.getegid();
  await assert.rejects(buildModelArtifactManifest({ sourcePath: path, kind: "file", expectedOwnerUid: uid, readerUid: uid + 1000, readerGid: gid + 1000 }), /not readable by the configured container identity/u);
  const manifest = await buildModelArtifactManifest({ sourcePath: path, kind: "file", expectedOwnerUid: uid, readerUid: uid, readerGid: gid });
  assert.equal(manifest.fileCount, 1);
});
