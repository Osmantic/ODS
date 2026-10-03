import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  chmod, link, lstat, mkdir, mkdtemp, readFile, readdir, rm, stat, symlink, writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  applyBuilderPatch, exportBuilderPatch, initializeBuilderVolume, validateBuilderPatchForApplication,
} from "../deploy/work-runner/builder-volume.mjs";

const binding = Object.freeze({
  jobId: "work-1700000000000-012345abcdef",
  claimId: "workclaim-1700000000000-fedcba654321",
  planSha256: "1".repeat(64),
  workspaceSha256: "2".repeat(64),
});
const limits = Object.freeze({ maxTreeBytes: 1024 * 1024, maxArtifactBytes: 1024 * 1024 });
const hash = (value) => createHash("sha256").update(value).digest("hex");

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "pixel-builder-volume-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = join(root, "source");
  const workspace = join(root, "workspace");
  const output = join(root, "output");
  await Promise.all([source, workspace, output].map((path) => mkdir(path, { mode: 0o700 })));
  if (process.platform !== "win32") await Promise.all([source, workspace, output].map((path) => chmod(path, 0o700)));
  await mkdir(join(source, "src"), { mode: 0o700 });
  await writeFile(join(source, "src", "change.js"), "export const value = 1;\n", { mode: 0o600 });
  await writeFile(join(source, "src", "delete.js"), "delete me\n", { mode: 0o600 });
  await writeFile(join(source, "unchanged.txt"), "same\n", { mode: 0o600 });
  return { root, source, workspace, output };
}

test("Builder volume is a writable copy and exports a deterministic, authority-free patch", async (t) => {
  const value = await fixture(t);
  const initialized = await initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes });
  assert.equal(initialized.copiedEntries, 4);
  assert.equal(await readFile(join(value.workspace, "src", "change.js"), "utf8"), "export const value = 1;\n");
  if (process.platform !== "win32") assert.equal((await stat(join(value.workspace, "src", "change.js"))).mode & 0o777, 0o600);

  await writeFile(join(value.workspace, "src", "change.js"), "export const value = 2;\n", "utf8");
  await rm(join(value.workspace, "src", "delete.js"));
  await writeFile(join(value.workspace, "src", "new.js"), "export const added = true;\n", "utf8");
  const result = await exportBuilderPatch(value.source, value.workspace, value.output, binding, limits);
  const serialized = await readFile(result.path);
  const patch = JSON.parse(serialized);

  assert.equal(result.bytes, serialized.length);
  assert.equal(result.sha256, hash(serialized));
  assert.deepEqual(patch.authority, { sourceMutation: false, merge: false, deploy: false, externalEffects: false });
  assert.deepEqual(patch.summary, { added: 1, modified: 1, deleted: 1, beforeBytes: 39, afterBytes: 56 });
  assert.deepEqual(patch.changes.map(({ path, operation }) => ({ path, operation })), [
    { path: "src/change.js", operation: "modify" },
    { path: "src/delete.js", operation: "delete" },
    { path: "src/new.js", operation: "add" },
  ]);
  assert.equal(Buffer.from(patch.changes[0].contentBase64, "base64").toString(), "export const value = 2;\n");
  assert.equal(patch.changes[1].contentBase64, null);
  assert.equal(Buffer.from(patch.changes[2].contentBase64, "base64").toString(), "export const added = true;\n");
  assert.equal(await readFile(join(value.source, "src", "change.js"), "utf8"), "export const value = 1;\n");
  assert.equal(serialized.includes(Buffer.from(value.root)), false);
  if (process.platform !== "win32") assert.equal((await stat(result.path)).mode & 0o777, 0o600);

  const secondOutput = join(value.root, "second-output");
  await mkdir(secondOutput, { mode: 0o700 });
  if (process.platform !== "win32") await chmod(secondOutput, 0o700);
  const second = await exportBuilderPatch(value.source, value.workspace, secondOutput, binding, limits);
  assert.deepEqual(await readFile(second.path), serialized);

  await assert.rejects(exportBuilderPatch(value.source, value.workspace, value.output, binding, limits), /not empty/);
  assert.deepEqual(await readFile(result.path), serialized);
});

test("Builder volume rejects overlap, unsafe state, hard links, symlinks, and byte-limit abuse", async (t) => {
  const value = await fixture(t);
  await writeFile(join(value.workspace, "occupied"), "x");
  await assert.rejects(initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes }), /not empty/);
  await rm(join(value.workspace, "occupied"));
  await assert.rejects(initializeBuilderVolume(value.source, value.workspace, { maxBytes: 1 }), /volume budget/);

  const linked = join(value.source, "linked.txt");
  await link(join(value.source, "unchanged.txt"), linked);
  await assert.rejects(initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes }), /hard-linked/);
  await rm(linked);

  const symlinkPath = join(value.source, "escape-link");
  try {
    await symlink(join(value.root, "outside"), symlinkPath, "file");
    await assert.rejects(initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes }), /symbolic link/);
    await rm(symlinkPath);
  } catch (error) {
    if (error?.code !== "EPERM") throw error;
  }

  await initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes });
  await assert.rejects(exportBuilderPatch(value.source, value.workspace, join(value.source, "nested"), binding, limits), /overlap/);
  await writeFile(join(value.output, "unexpected"), "x");
  await assert.rejects(exportBuilderPatch(value.source, value.workspace, value.output, binding, limits), /not empty/);
  await rm(join(value.output, "unexpected"));
  await assert.rejects(exportBuilderPatch(value.source, value.workspace, value.output, { ...binding, claimId: "bad" }, limits), /binding/);
  await assert.rejects(exportBuilderPatch(value.source, value.workspace, value.output, binding, { ...limits, maxArtifactBytes: 8 }), /oversized file|artifact budget/);
  await assert.rejects(exportBuilderPatch(value.source, value.workspace, value.output, binding, { ...limits, maxArtifactBytes: 268435457 }), /artifact byte ceiling/);
  assert.deepEqual(await readdir(value.output), []);
});

test("Builder export rejects unsafe workspace entries without writing an artifact", async (t) => {
  const value = await fixture(t);
  await initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes });
  await writeFile(join(value.workspace, "unsafe name.txt"), "x");
  await assert.rejects(exportBuilderPatch(value.source, value.workspace, value.output, binding, limits), /unsafe path/);
  assert.equal(await lstat(join(value.output, "builder-patch.json")).catch(() => null), null);
});

test("Builder volume preserves systemd template unit names without widening unsafe paths", async (t) => {
  const value = await fixture(t);
  const unit = "maintenance-recovery-guardian@.service";
  await writeFile(join(value.source, unit), "[Unit]\nDescription=Template\n", { mode: 0o600 });
  await initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes });
  assert.equal(await readFile(join(value.workspace, unit), "utf8"), "[Unit]\nDescription=Template\n");
  await writeFile(join(value.workspace, unit), "[Unit]\nDescription=Updated template\n", "utf8");
  const exported = await exportBuilderPatch(value.source, value.workspace, value.output, binding, limits);
  const patch = JSON.parse(await readFile(exported.path, "utf8"));
  assert.deepEqual(patch.changes.map(({ path, operation }) => ({ path, operation })), [
    { path: unit, operation: "modify" },
  ]);
  const candidate = join(value.root, "template-candidate");
  await mkdir(candidate, { mode: 0o700 });
  await applyBuilderPatch(value.source, candidate, patch, binding, limits);
  assert.equal(await readFile(join(candidate, unit), "utf8"), "[Unit]\nDescription=Updated template\n");
});

test("independent candidate application revalidates every preimage and protected path", async (t) => {
  const value = await fixture(t);
  await initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes });
  await writeFile(join(value.workspace, "src", "change.js"), "export const value = 2;\n");
  await rm(join(value.workspace, "src", "delete.js"));
  await writeFile(join(value.workspace, "src", "new.js"), "export const added = true;\n");
  const exported = await exportBuilderPatch(value.source, value.workspace, value.output, binding, limits);
  const patch = JSON.parse(await readFile(exported.path, "utf8"));
  const candidate = join(value.root, "candidate");
  await mkdir(candidate, { mode: 0o700 });
  const applied = await applyBuilderPatch(value.source, candidate, patch, binding, { ...limits, immutablePathPrefixes: [] });
  assert.equal(applied.changes, 3);
  assert.match(applied.candidateSha256, /^[a-f0-9]{64}$/);
  assert.equal(await readFile(join(candidate, "src", "change.js"), "utf8"), "export const value = 2;\n");
  assert.equal(await lstat(join(candidate, "src", "delete.js")).catch(() => null), null);
  assert.equal(await readFile(join(candidate, "src", "new.js"), "utf8"), "export const added = true;\n");
  assert.equal(await readFile(join(value.source, "src", "change.js"), "utf8"), "export const value = 1;\n");

  assert.throws(() => validateBuilderPatchForApplication(patch, binding, { ...limits, immutablePathPrefixes: ["src/"] }), /immutable path/);
  const changedBinding = structuredClone(patch);
  changedBinding.planSha256 = "9".repeat(64);
  assert.throws(() => validateBuilderPatchForApplication(changedBinding, binding, limits), /binding/);
  const changedContent = structuredClone(patch);
  changedContent.changes.find((change) => change.operation !== "delete").contentBase64 = Buffer.from("counterfeit\n").toString("base64");
  assert.throws(() => validateBuilderPatchForApplication(changedContent, binding, limits), /digest/);

  const substitutedSource = join(value.root, "substituted-source");
  const rejectedCandidate = join(value.root, "rejected-candidate");
  await Promise.all([substitutedSource, rejectedCandidate].map((path) => mkdir(path, { mode: 0o700 })));
  await initializeBuilderVolume(value.source, substitutedSource, { maxBytes: limits.maxTreeBytes });
  await writeFile(join(substitutedSource, "src", "change.js"), "substituted\n");
  await assert.rejects(applyBuilderPatch(substitutedSource, rejectedCandidate, patch, binding, limits), /before-size|preimage/);
});

test("candidate application handles file-directory shape transitions without escaping", async (t) => {
  const value = await fixture(t);
  await rm(value.source, { recursive: true });
  await mkdir(join(value.source, "shape"), { recursive: true, mode: 0o700 });
  await writeFile(join(value.source, "shape", "old.txt"), "old\n");
  await initializeBuilderVolume(value.source, value.workspace, { maxBytes: limits.maxTreeBytes });
  await rm(join(value.workspace, "shape"), { recursive: true });
  await writeFile(join(value.workspace, "shape"), "now a file\n");
  const exported = await exportBuilderPatch(value.source, value.workspace, value.output, binding, limits);
  const patch = JSON.parse(await readFile(exported.path, "utf8"));
  const candidate = join(value.root, "candidate-shape");
  await mkdir(candidate, { mode: 0o700 });
  await applyBuilderPatch(value.source, candidate, patch, binding, limits);
  assert.equal(await readFile(join(candidate, "shape"), "utf8"), "now a file\n");
});
