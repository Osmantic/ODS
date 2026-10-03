import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { validateJsonSchema } from "../scripts/lib/json-schema.mjs";

const run = promisify(execFile);
const repo = dirname(dirname(fileURLToPath(import.meta.url)));

async function fixture() {
  const directory = await mkdtemp(join(tmpdir(), "pixel-release-identity-repo-"));
  const release = JSON.parse(await readFile(join(repo, "RELEASE-MANIFEST.json"), "utf8"));
  await mkdir(join(directory, "scripts"));
  await writeFile(join(directory, "scripts", "release-identity.mjs"), await readFile(join(repo, "scripts", "release-identity.mjs")));
  await writeFile(join(directory, ".gitignore"), await readFile(join(repo, ".gitignore")));
  await writeFile(join(directory, "VERSION"), `${release.pixel}\n`);
  await writeFile(join(directory, "RELEASE-MANIFEST.json"), JSON.stringify({
    pixel: release.pixel, openclaw: release.openclaw, openclawPlugins: release.openclawPlugins,
  }));
  await writeFile(join(directory, "QUALIFICATION-MATRIX.json"), "{}\n");
  await writeFile(join(directory, "OPENCLAW-COMPATIBILITY.json"), JSON.stringify({ combinations: [] }));
  await run("git", ["init", "-q"], { cwd: directory });
  await run("git", ["config", "user.email", "test@example.invalid"], { cwd: directory });
  await run("git", ["config", "user.name", "Test"], { cwd: directory });
  await run("git", ["add", "."], { cwd: directory });
  await run("git", ["commit", "-qm", "baseline"], { cwd: directory });
  const { stdout } = await run("git", ["rev-parse", "HEAD"], { cwd: directory });
  await writeFile(join(directory, "OPENCLAW-COMPATIBILITY.json"), JSON.stringify({
    combinations: [{
      pixel: release.pixel, openclaw: release.openclaw, plugins: release.openclawPlugins,
      status: "candidate", qualifiedAt: "2026-08-11",
      evidence: { sourceCommit: stdout.trim(), liveAudit: "LIVE-AUDIT-fixture.md" },
    }],
  }));
  await run("git", ["add", "."], { cwd: directory });
  await run("git", ["commit", "-qm", "candidate"], { cwd: directory });
  return directory;
}

test("release identity binds clean Git source, manifests, and baseline qualification lineage", async () => {
  const directory = await fixture();
  try {
    const output = join(directory, "identity.json");
    await run(process.execPath, [join(directory, "scripts", "release-identity.mjs"), "--output", output], { cwd: directory });
    const identity = JSON.parse(await readFile(output, "utf8"));
    const schema = JSON.parse(await readFile(join(repo, "schemas", "release-identity-v1.schema.json"), "utf8"));
    assert.deepEqual(validateJsonSchema(identity, schema), []);
    assert.equal(identity.kind, "pixel-release-source-identity");
    assert.match(identity.source.commit, /^[a-f0-9]{40}$/u);
    assert.match(identity.source.tree, /^[a-f0-9]{40}$/u);
    assert.equal(identity.qualification.recordStatus, "candidate");
    assert.ok(["same-source", "qualified-ancestor"].includes(identity.qualification.relationship));
    assert.match(identity.manifests.releaseSha256, /^[a-f0-9]{64}$/u);
    assert.match(identity.manifests.compatibilitySha256, /^[a-f0-9]{64}$/u);
    assert.match(identity.manifests.qualificationMatrixSha256, /^[a-f0-9]{64}$/u);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("release identity refuses to borrow qualification from a different plugin set", async () => {
  const directory = await fixture();
  try {
    const compatibilityPath = join(directory, "OPENCLAW-COMPATIBILITY.json");
    const compatibility = JSON.parse(await readFile(compatibilityPath, "utf8"));
    compatibility.combinations[0].plugins["@openclaw/discord"] = "9999.1.1";
    await writeFile(compatibilityPath, JSON.stringify(compatibility));
    await run("git", ["add", "."], { cwd: directory });
    await run("git", ["commit", "-qm", "different plugin set"], { cwd: directory });
    const output = join(directory, "identity.json");
    await run(process.execPath, [join(directory, "scripts", "release-identity.mjs"), "--output", output], { cwd: directory });
    const identity = JSON.parse(await readFile(output, "utf8"));
    assert.equal(identity.qualification.recordStatus, null);
    assert.equal(identity.qualification.sourceCommit, null);
    assert.equal(identity.qualification.relationship, "unverified");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("release identity permits only the fixed armed env sibling and still detects other untracked source", async () => {
  const directory = await fixture();
  const outputs = await mkdtemp(join(tmpdir(), "pixel-release-identity-output-"));
  try {
    await writeFile(join(directory, ".pixel-restore-.env-12345-7.old"), "armed-private-state\n");
    const armedOutput = join(outputs, "armed.json");
    await run(process.execPath, [join(directory, "scripts", "release-identity.mjs"), "--output", armedOutput], { cwd: directory });
    const armedIdentity = JSON.parse(await readFile(armedOutput, "utf8"));
    assert.equal(armedIdentity.source.state, "git-clean");

    await writeFile(join(directory, "unexpected-untracked.js"), "export default true;\n");
    const dirtyOutput = join(outputs, "dirty.json");
    await run(process.execPath, [join(directory, "scripts", "release-identity.mjs"), "--output", dirtyOutput], { cwd: directory });
    const dirtyIdentity = JSON.parse(await readFile(dirtyOutput, "utf8"));
    assert.equal(dirtyIdentity.source.state, "unavailable");
  } finally {
    await rm(directory, { recursive: true, force: true });
    await rm(outputs, { recursive: true, force: true });
  }
});

test("embedded identity is rejected when its bound manifests differ", async () => {
  const directory = await fixture();
  try {
    await writeFile(join(directory, "RELEASE-IDENTITY.json"), JSON.stringify({ schemaVersion: 1 }));
    await assert.rejects(
      run(process.execPath, [join(directory, "scripts", "release-identity.mjs"), "--output", join(directory, "out.json")]),
      /release identity has missing or unknown fields/u,
    );
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
