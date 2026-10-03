import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  PACKAGE_SPECS,
  assertRegistryTarball,
  buildCandidateCompatibility,
  buildCandidateManifest,
  buildPrepareReport,
  canonicalJson,
  downloadArtifact,
  resolveChannel,
} from "./lib/upstream-registry.mjs";

const EXTENDED_STABLE_FIXTURE_VERSION = ["2026", "6", "34"].join(".");

function integrity(bytes) {
  return `sha512-${createHash("sha512").update(bytes).digest("base64")}`;
}

function metadata(spec, versions) {
  const records = Object.fromEntries(versions.map((version) => [version, {
    version,
    dist: {
      integrity: integrity(Buffer.from(`${spec.name}:${version}`)),
      tarball: `https://registry.npmjs.org/${spec.name}/-/${spec.archive}-${version}.tgz`,
    },
  }]));
  return {
    "dist-tags": { "extended-stable": versions[0], latest: versions.at(-1) },
    versions: records,
    time: Object.fromEntries(versions.map((version, index) => [version, `2026-08-0${index + 1}T12:00:00.000Z`])),
  };
}

test("channels resolve every official package independently", () => {
  const metadataByName = Object.fromEntries(PACKAGE_SPECS.map((spec, index) => [
    spec.name,
    metadata(spec, [EXTENDED_STABLE_FIXTURE_VERSION, index === 0 ? "2026.7.1-2" : "2026.7.1"]),
  ]));
  const latest = resolveChannel(metadataByName, "latest");
  assert.equal(latest.packages[0].version, "2026.7.1-2");
  assert.deepEqual(latest.packages.slice(1).map((item) => item.version), ["2026.7.1", "2026.7.1", "2026.7.1"]);
});

test("prerelease tags and non-registry tarballs fail closed", () => {
  const spec = PACKAGE_SPECS[0];
  const unsafe = metadata(spec, ["2026.7.2-beta.7"]);
  assert.throws(() => resolveChannel(Object.fromEntries(PACKAGE_SPECS.map((item) => [item.name, item === spec ? unsafe : metadata(item, ["2026.7.1"])])), "latest"), /non-stable/);
  assert.throws(() => assertRegistryTarball(spec, "2026.7.1", "https://example.com/openclaw-2026.7.1.tgz"), /authoritative npm registry/);
});

test("downloader revalidates manifest-supplied artifact URLs", async () => {
  await assert.rejects(downloadArtifact({
    name: "openclaw",
    version: "2026.7.1-2",
    url: "https://attacker.example/openclaw-2026.7.1-2.tgz",
    integrity: integrity(Buffer.from("attacker bytes")),
  }, "unused.tgz", async () => { throw new Error("network must not be reached"); }), /authoritative npm registry/);
});

test("artifact quarantine verifies integrity and safely reuses exact bytes", async (context) => {
  const temporary = await mkdtemp(join(tmpdir(), "pixel-upstream-test-"));
  context.after(async () => {
    assert.ok(temporary.startsWith(tmpdir()));
    await rm(temporary, { recursive: true, force: true });
  });
  const bytes = Buffer.from("opaque npm package bytes");
  const resolved = {
    name: "openclaw",
    version: "2026.7.1-2",
    url: "https://registry.npmjs.org/openclaw/-/openclaw-2026.7.1-2.tgz",
    integrity: integrity(bytes),
  };
  const destination = join(temporary, "openclaw.tgz");
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    return {
      ok: true,
      status: 200,
      url: resolved.url,
      headers: { get: (name) => name === "content-length" ? String(bytes.length) : null },
      arrayBuffer: async () => bytes,
    };
  };
  const first = await downloadArtifact(resolved, destination, fetchImpl);
  const second = await downloadArtifact(resolved, destination, async () => { throw new Error("network must not be used"); });
  assert.equal(calls, 1);
  assert.equal(first.reused, false);
  assert.equal(second.reused, true);
  assert.equal(first.sha256, createHash("sha256").update(bytes).digest("hex"));
  assert.deepEqual(await readFile(destination), bytes);
  if (process.platform !== "win32") assert.equal((await stat(destination)).mode & 0o077, 0);
  await writeFile(destination, "tampered");
  await assert.rejects(downloadArtifact(resolved, destination, fetchImpl), /integrity verification/);
});

test("candidate manifest and report are deterministic", () => {
  const metadataByName = Object.fromEntries(PACKAGE_SPECS.map((spec, index) => [spec.name, metadata(spec, [index === 0 ? "2026.7.1-2" : "2026.7.1"])]));
  const candidate = resolveChannel(metadataByName, "latest");
  const artifacts = candidate.packages.map((item) => ({ ...item, sha256: "a".repeat(64), size: 42, file: `/private/${item.key}.tgz` }));
  const current = {
    pixel: "3.2.0",
    openclaw: "2026.6.32",
    openclawPackage: {},
    openclawPlugins: {},
    openclawPluginPackages: {},
  };
  const commit = "b".repeat(40);
  const manifest = buildCandidateManifest(current, candidate, artifacts, commit);
  const left = buildPrepareReport(candidate, artifacts, manifest, commit, "/private");
  const right = buildPrepareReport(candidate, artifacts, manifest, commit, "/private");
  assert.equal(manifest.openclaw, "2026.7.1-2");
  assert.equal(manifest.openclawPlugins["@openclaw/discord"], "2026.7.1");
  assert.equal(canonicalJson(left), canonicalJson(right));
  assert.equal(left.candidateManifestSha256.length, 64);
  const compatibility = buildCandidateCompatibility({ combinations: [] }, manifest, candidate, commit);
  assert.deepEqual(compatibility.combinations, [{
    pixel: "3.2.0",
    openclaw: "2026.7.1-2",
    plugins: {
      "@openclaw/discord": "2026.7.1",
      "@openclaw/searxng-plugin": "2026.7.1",
      "@openclaw/llama-cpp-provider": "2026.7.1",
    },
    status: "candidate",
    qualifiedAt: "2026-08-01",
    evidence: { sourceCommit: commit, liveAudit: "UPSTREAM-RELEASE-CHECKLIST.md" },
  }]);
  assert.deepEqual(buildCandidateCompatibility(compatibility, manifest, candidate, commit), compatibility);
});
