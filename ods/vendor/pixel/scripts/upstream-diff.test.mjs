import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { buildContractDiff, diffSnapshots, renderContractDiff, snapshotPackage } from "./lib/upstream-contract-diff.mjs";

const SUPPORTED_FIXTURE_VERSION = ["2026", "6", "33"].join(".");
const CANDIDATE_FIXTURE_VERSION = ["2026", "6", "34"].join(".");

async function fixture(root, version, source) {
  await mkdir(join(root, "src", "gateway"), { recursive: true });
  await writeFile(join(root, "package.json"), `${JSON.stringify({ name: "openclaw", version, bin: { openclaw: "bin/cli.js" }, scripts: { test: "node test.js" } })}\n`);
  await writeFile(join(root, "src", "gateway", "auth.js"), source);
  await writeFile(join(root, ".pixel-extraction.json"), `${JSON.stringify({ archiveSha256: version.padEnd(64, "0").slice(0, 64), treeSha256: version.padEnd(64, "1").slice(0, 64), fileCount: 2, totalBytes: source.length })}\n`);
}

test("contract diff blocks authority, denial, credential, and surface changes", async (context) => {
  const temporary = await mkdtemp(join(tmpdir(), "pixel-upstream-diff-"));
  context.after(async () => {
    assert.ok(temporary.startsWith(tmpdir()));
    await rm(temporary, { recursive: true, force: true });
  });
  const supportedRoot = join(temporary, "supported");
  const candidateRoot = join(temporary, "candidate");
  await fixture(supportedRoot, CANDIDATE_FIXTURE_VERSION, "export const policy = 'deny network; loopback readOnly';\n");
  await fixture(candidateRoot, "2026.7.1-2", "export const command = '--unsafe-shell'; exec(fetch(token));\n");
  const supported = await snapshotPackage(supportedRoot, { name: "openclaw", version: CANDIDATE_FIXTURE_VERSION });
  const candidate = await snapshotPackage(candidateRoot, { name: "openclaw", version: "2026.7.1-2" });
  const packageDiff = diffSnapshots(supported, candidate);
  const report = buildContractDiff([packageDiff], "a".repeat(40));
  const types = new Set(report.blockers.map((item) => item.type));
  assert.equal(report.status, "blocked");
  assert.ok(types.has("new-authority"));
  assert.ok(types.has("removed-denial"));
  assert.ok(types.has("credential-path-change"));
  assert.ok(types.has("surface-change"));
  assert.ok(types.has("sensitive-interface"));
  assert.ok(report.blockers.length < 20);
  assert.ok(report.blockers.every((item) => item.pathSamples.length <= 20));
  assert.ok(report.blockers.every((item) => /^[a-f0-9]{64}$/.test(item.evidenceSha256)));
  assert.ok(packageDiff.changes.signals.added.every((item) => item.pathSamples.length <= 20));
  assert.ok(packageDiff.changes.signals.added.every((item) => /^[a-f0-9]{64}$/.test(item.evidenceSha256)));
  assert.ok(packageDiff.changes.signals.totals.added > 0);
  assert.equal(JSON.stringify(report).includes("exec(fetch(token))"), false);
  assert.match(renderContractDiff(report), /Every blocker requires an explicit reviewed disposition/);
  assert.deepEqual(buildContractDiff([diffSnapshots(supported, candidate)], "a".repeat(40)), report);
});

test("identical snapshots require no review", async (context) => {
  const temporary = await mkdtemp(join(tmpdir(), "pixel-upstream-diff-same-"));
  context.after(async () => {
    assert.ok(temporary.startsWith(tmpdir()));
    await rm(temporary, { recursive: true, force: true });
  });
  await fixture(temporary, CANDIDATE_FIXTURE_VERSION, "export const value = 1;\n");
  const snapshot = await snapshotPackage(temporary, { name: "openclaw", version: CANDIDATE_FIXTURE_VERSION });
  const report = buildContractDiff([diffSnapshots(snapshot, snapshot)], "b".repeat(40));
  assert.equal(report.status, "no-change");
  assert.equal(report.blockers.length, 0);
});

test("content-hashed bundle renames do not invent authority changes", async (context) => {
  const temporary = await mkdtemp(join(tmpdir(), "pixel-upstream-diff-chunks-"));
  context.after(async () => {
    assert.ok(temporary.startsWith(tmpdir()));
    await rm(temporary, { recursive: true, force: true });
  });
  const supportedRoot = join(temporary, "supported");
  const candidateRoot = join(temporary, "candidate");
  await fixture(supportedRoot, SUPPORTED_FIXTURE_VERSION, "export const stable = true;\n");
  await fixture(candidateRoot, CANDIDATE_FIXTURE_VERSION, "export const stable = true;\n");
  await mkdir(join(supportedRoot, "dist"), { recursive: true });
  await mkdir(join(candidateRoot, "dist"), { recursive: true });
  const implementation = "export const policy = 'deny network; loopback token';\n";
  await writeFile(join(supportedRoot, "dist", "gateway-auth-AbC12_xY.js"), implementation);
  await writeFile(join(candidateRoot, "dist", "gateway-auth-QrS34_zW.js"), implementation);
  const supported = await snapshotPackage(supportedRoot, { name: "openclaw", version: SUPPORTED_FIXTURE_VERSION });
  const candidate = await snapshotPackage(candidateRoot, { name: "openclaw", version: CANDIDATE_FIXTURE_VERSION });
  const renamed = buildContractDiff([diffSnapshots(supported, candidate)], "c".repeat(40));
  assert.equal(renamed.status, "review-required");
  assert.equal(renamed.blockers.length, 0);

  await writeFile(join(candidateRoot, "dist", "gateway-auth-QrS34_zW.js"), "export const unsafe = exec(fetch(token));\n");
  const changed = await snapshotPackage(candidateRoot, { name: "openclaw", version: CANDIDATE_FIXTURE_VERSION });
  const changedReport = buildContractDiff([diffSnapshots(supported, changed)], "c".repeat(40));
  assert.equal(changedReport.status, "blocked");
  assert.ok(changedReport.blockers.some((item) => item.type === "new-authority"));
  assert.ok(changedReport.blockers.some((item) => item.type === "removed-denial"));
});

test("signal moves are normalized but removal of a duplicate remains blocking", async (context) => {
  const temporary = await mkdtemp(join(tmpdir(), "pixel-upstream-diff-multiset-"));
  context.after(async () => {
    assert.ok(temporary.startsWith(tmpdir()));
    await rm(temporary, { recursive: true, force: true });
  });
  const supportedRoot = join(temporary, "supported");
  const candidateRoot = join(temporary, "candidate");
  await fixture(supportedRoot, SUPPORTED_FIXTURE_VERSION, "export const stable = true;\n");
  await fixture(candidateRoot, CANDIDATE_FIXTURE_VERSION, "export const stable = true;\n");
  for (const root of [supportedRoot, candidateRoot]) await mkdir(join(root, "dist"), { recursive: true });
  const denial = "export const policy = 'deny network on loopback';\n";
  await writeFile(join(supportedRoot, "dist", "gateway-first-AbC12_xY.js"), denial);
  await writeFile(join(supportedRoot, "dist", "gateway-second-DeF34_zW.js"), denial);
  await writeFile(join(candidateRoot, "dist", "gateway-moved-QrS34_zW.js"), denial);
  const supported = await snapshotPackage(supportedRoot, { name: "openclaw", version: SUPPORTED_FIXTURE_VERSION });
  const candidate = await snapshotPackage(candidateRoot, { name: "openclaw", version: CANDIDATE_FIXTURE_VERSION });
  const report = buildContractDiff([diffSnapshots(supported, candidate)], "d".repeat(40));
  assert.equal(report.status, "blocked");
  assert.ok(report.blockers.some((item) => item.type === "removed-denial" && item.count > 0));
});
