#!/usr/bin/env node
// Build the dependency-complete, exact runtime snapshot for the thin Pixel bundle operator.
//
// Usage: node build-runtime-snapshot.mjs REPO_ROOT OUTPUT_DIR
//
// The snapshot is a bounded reviewed closure of every actual runtime dependency of the
// three fixed kind-specific lifecycle CLIs (goal, fleet-goal, deep-work-soak):
//   * the transitive static ESM import closure of each lifecycle CLI entry, and
//   * the explicit non-import runtime assets the rendered systemd units execute
//     (goal-continuous-cli, goal-cleanup-cli, goal-fleet-supervised-cycle-cli,
//     goal-fleet-cleanup-cli, deep-work-multi-day-soak) plus their transitive imports.
// Every module/asset is copied preserving its repo-relative path into OUTPUT_DIR and the
// exact tree SHA-256 manifest is written as OUTPUT_DIR/runtime-manifest.json. The printed
// manifest SHA-256 is the runtime tree SHA that provisioning binds in the operator config.
import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { dirname, join, normalize, relative, resolve, sep } from "node:path";

const ENTRIES = [
  "deploy/work-controller/goal-service-lifecycle-cli.mjs",
  "deploy/work-controller/goal-fleet-service-lifecycle-cli.mjs",
  "deploy/work-controller/deep-work-soak-service-cli.mjs",
];

// Non-import runtime assets: exact scripts the reviewed rendered units execute at runtime.
// These are part of "dependency-complete" even though no lifecycle CLI imports them.
const RUNTIME_ASSETS = [
  "deploy/work-controller/goal-continuous-cli.mjs",
  "deploy/work-controller/goal-cleanup-cli.mjs",
  "deploy/work-controller/goal-fleet-supervised-cycle-cli.mjs",
  "deploy/work-controller/goal-fleet-cleanup-cli.mjs",
  "scripts/deep-work-multi-day-soak.mjs",
  // Fixed source bytes for the root-custodied broker-byte transaction. The operator
  // maps these manifest-bound paths to fixed root-owned targets; callers never supply
  // a path, unit, command, or byte digest.
  "deploy/source-broker/broker.py",
  "deploy/ops-broker/broker.py",
  "deploy/frontier-broker/broker.py",
  "scripts/verify-frontier-codex.py",
];

const MAX_FILES = 4000;

function fail(message) { throw new Error(message); }
function sha(value) { return createHash("sha256").update(value).digest("hex"); }

function importSpecifiers(source) {
  const out = [];
  let re = /import\s[^;]*?from\s+["'](\.[^"']+)["']/g, m;
  while ((m = re.exec(source)) !== null) out.push(m[1]);
  re = /import\s+["'](\.[^"']+)["']/g;
  while ((m = re.exec(source)) !== null) out.push(m[1]);
  re = /import\s*\(\s*["'](\.[^"']+)["']\s*\)/g;
  while ((m = re.exec(source)) !== null) out.push(m[1]);
  return out;
}

function runtimeAssetSpecifiers(source) {
  // Non-import runtime assets loaded relative to the module (schema JSON, policy examples,
  // sibling modules). Static literals only; template-literal schema loads are covered by
  // including the entire schemas/ directory as a reviewed runtime asset set.
  const out = [];
  let re = /new\s+URL\(\s*["'](\.[^"']+)["']\s*,\s*import\.meta\.url\s*\)/g, m;
  while ((m = re.exec(source)) !== null) out.push(m[1]);
  return out;
}

async function listSchemas(repo) {
  const dir = join(repo, "schemas");
  const out = [];
  for (const name of await readdir(dir)) {
    if (name.endsWith(".json")) out.push(join("schemas", name));
  }
  return out.sort();
}

async function closure(repo) {
  const seen = new Set();
  const stack = [...ENTRIES, ...RUNTIME_ASSETS, ...(await listSchemas(repo))];
  while (stack.length) {
    const rel = stack.pop();
    if (seen.has(rel)) continue;
    if (rel.startsWith(sep) || rel.split(sep).includes("..") || resolve(repo, rel) === repo) {
      fail(`runtime path escapes the repository: ${rel}`);
    }
    seen.add(rel);
    if (seen.size > MAX_FILES) fail(`runtime closure exceeds the bounded file limit (${MAX_FILES})`);
    const abs = resolve(repo, rel);
    let source = null;
    if (rel.endsWith(".json")) {
      try { await readFile(abs); } catch { fail(`runtime asset is missing: ${rel}`); }
      continue;
    }
    try { source = await readFile(abs, "utf8"); }
    catch { fail(`runtime entry is missing: ${rel}`); }
    for (const spec of importSpecifiers(source)) {
      const next = normalize(join(dirname(rel), spec));
      const nextAbs = resolve(repo, next);
      try { await readFile(nextAbs); }
      catch { fail(`runtime import is missing: ${rel} -> ${spec}`); }
      if (!seen.has(next)) stack.push(next);
    }
    for (const spec of runtimeAssetSpecifiers(source)) {
      const next = normalize(join(dirname(rel), spec));
      const nextAbs = resolve(repo, next);
      try { await readFile(nextAbs); }
      catch { fail(`runtime asset is missing: ${rel} -> ${spec}`); }
      if (!seen.has(next)) stack.push(next);
    }
  }
  return [...seen].sort();
}

async function main() {
  const [repo, output] = process.argv.slice(2);
  if (!repo || !output) fail("Usage: build-runtime-snapshot.mjs REPO_ROOT OUTPUT_DIR");
  const files = await closure(repo);
  const manifest = { schemaVersion: 1, files: {} };
  await mkdir(output, { recursive: true });
  for (const rel of files) {
    const src = resolve(repo, rel);
    const dst = resolve(output, rel);
    if (relative(resolve(output), dst).startsWith("..")) fail(`runtime output path escapes OUTPUT_DIR: ${rel}`);
    await mkdir(dirname(dst), { recursive: true });
    await copyFile(src, dst);
    const bytes = await readFile(dst);
    manifest.files[rel] = sha(bytes);
  }
  const manifestPath = resolve(output, "runtime-manifest.json");
  const manifestBytes = Buffer.from(JSON.stringify(manifest, null, 2) + "\n", "utf8");
  await writeFile(manifestPath, manifestBytes);
  console.log(sha(manifestBytes));
  console.error(`built ${files.length} runtime files into ${output}`);
}

main().catch((error) => { console.error(`build-runtime-snapshot: ${error.message}`); process.exit(1); });
