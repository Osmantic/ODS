#!/usr/bin/env node
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { readFile, rename, stat, unlink, writeFile } from "node:fs/promises";
import { dirname, isAbsolute, join, parse, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { assertJsonSchema } from "./lib/json-schema.mjs";
import { buildContractDiff, diffSnapshots, renderContractDiff, snapshotPackage } from "./lib/upstream-contract-diff.mjs";
import { applyContractReview } from "./lib/upstream-contract-review.mjs";
import { readBoundedRegularText } from "./lib/secure-files.mjs";
import {
  PACKAGE_SPECS,
  REGISTRY_ORIGIN,
  RELEASE_CHANNELS,
  artifactPath,
  buildCandidateCompatibility,
  buildCandidateManifest,
  buildPrepareReport,
  canonicalJson,
  downloadArtifact,
  ensurePrivateQuarantine,
  fetchAllMetadata,
  resolveChannel,
} from "./lib/upstream-registry.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const manifestPath = join(root, "RELEASE-MANIFEST.json");
const compatibilityPath = join(root, "OPENCLAW-COMPATIBILITY.json");
const manifestSchema = JSON.parse(await readFile(join(root, "schemas/release-manifest-v1.schema.json"), "utf8"));
const compatibilitySchema = JSON.parse(await readFile(join(root, "schemas/openclaw-compatibility-v1.schema.json"), "utf8"));

function fail(message) {
  throw new Error(message);
}

function parsePrepare(arguments_) {
  let channel;
  let quarantine;
  while (arguments_.length) {
    const option = arguments_.shift();
    if (option === "--channel") {
      if (channel !== undefined) fail("--channel may be supplied only once");
      channel = arguments_.shift();
      continue;
    }
    if (option === "--quarantine") {
      if (quarantine !== undefined) fail("--quarantine may be supplied only once");
      quarantine = arguments_.shift();
      if (!quarantine) fail("--quarantine requires an absolute path");
      continue;
    }
    fail(`Unknown upstream prepare option: ${option}`);
  }
  if (!RELEASE_CHANNELS.includes(channel)) fail(`--channel must be one of: ${RELEASE_CHANNELS.join(", ")}`);
  if (quarantine !== undefined && !isAbsolute(quarantine)) fail("--quarantine must be an absolute path");
  const base = resolve(quarantine ?? join(process.env.XDG_CACHE_HOME || join(homedir(), ".cache"), "pixel", "upstream-quarantine"));
  const fromRoot = relative(root, base);
  if (!fromRoot.startsWith("..") && !isAbsolute(fromRoot)) fail("Upstream quarantine must be outside the source repository");
  if (base === parse(base).root) fail("Upstream quarantine cannot be a filesystem root");
  return { channel, base };
}

function parseDiff(arguments_) {
  let quarantine;
  let evidence;
  while (arguments_.length) {
    const option = arguments_.shift();
    if (option === "--quarantine") {
      if (quarantine !== undefined) fail("--quarantine may be supplied only once");
      quarantine = arguments_.shift();
      if (!quarantine || !isAbsolute(quarantine)) fail("--quarantine requires an absolute path");
      continue;
    }
    if (option === "--evidence") {
      if (evidence !== undefined) fail("--evidence may be supplied only once");
      evidence = arguments_.shift();
      if (!evidence || !isAbsolute(evidence)) fail("--evidence requires an absolute path");
      continue;
    }
    fail(`Unknown upstream diff option: ${option}`);
  }
  const base = resolve(quarantine ?? join(process.env.XDG_CACHE_HOME || join(homedir(), ".cache"), "pixel", "upstream-quarantine"));
  const fromRoot = relative(root, base);
  if (!fromRoot.startsWith("..") && !isAbsolute(fromRoot)) fail("Upstream quarantine must be outside the source repository");
  if (base === parse(base).root) fail("Upstream quarantine cannot be a filesystem root");
  if (evidence) {
    const evidenceFromRoot = relative(root, resolve(evidence));
    if (!evidenceFromRoot.startsWith("..") && !isAbsolute(evidenceFromRoot)) fail("Upstream evidence must be outside the source repository");
  }
  return { base, evidence: evidence ? resolve(evidence) : undefined };
}

function parseQualify(arguments_) {
  let quarantine;
  let evidence;
  let work;
  let mode = "quick";
  while (arguments_.length) {
    const option = arguments_.shift();
    if (["--quarantine", "--evidence", "--work", "--mode"].includes(option)) {
      const value = arguments_.shift();
      if (!value) fail(`${option} requires a value`);
      if (option === "--quarantine") quarantine = value;
      if (option === "--evidence") evidence = value;
      if (option === "--work") work = value;
      if (option === "--mode") mode = value;
      continue;
    }
    fail(`Unknown upstream qualify option: ${option}`);
  }
  if (!["quick", "systemd"].includes(mode)) fail("--mode must be quick or systemd");
  for (const [label, value] of [["--quarantine", quarantine], ["--evidence", evidence], ["--work", work]]) {
    if (value !== undefined && !isAbsolute(value)) fail(`${label} requires an absolute path`);
  }
  const base = resolve(quarantine ?? join(process.env.XDG_CACHE_HOME || join(homedir(), ".cache"), "pixel", "upstream-quarantine"));
  const runId = `qualification-${Date.now()}-${process.pid}`;
  const selectedEvidence = resolve(evidence ?? join(base, "qualification-evidence", runId));
  const selectedWork = resolve(work ?? join(base, "qualification-work", runId));
  for (const [label, path] of [["Upstream quarantine", base], ["Qualification evidence", selectedEvidence], ["Qualification work", selectedWork]]) {
    const fromRoot = relative(root, path);
    if (!fromRoot.startsWith("..") && !isAbsolute(fromRoot)) fail(`${label} must be outside the source repository`);
    if (path === parse(path).root) fail(`${label} cannot be a filesystem root`);
  }
  return { base, evidence: selectedEvidence, work: selectedWork, mode };
}

function sourceCommit() {
  return execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
}

function sourceStatus() {
  return execFileSync("git", ["status", "--porcelain=v1", "--untracked-files=all"], { cwd: root, encoding: "utf8" }).trimEnd();
}

function onlyPreparedFilesModified(status) {
  const lines = status.split(/\r?\n/).filter(Boolean);
  const allowed = new Set(["RELEASE-MANIFEST.json", "OPENCLAW-COMPATIBILITY.json"]);
  return lines.length > 0 && lines.length <= allowed.size
    && lines.every((line) => allowed.has(line.slice(3)) && line.slice(0, 2) !== "??")
    && new Set(lines.map((line) => line.slice(3))).size === lines.length;
}

async function readManifest() {
  return readBoundedRegularText(manifestPath, 1024 * 1024, "RELEASE-MANIFEST.json");
}

async function writeAtomic(path, contents, mode) {
  const temporary = `${path}.upstream-${process.pid}`;
  await writeFile(temporary, contents, { flag: "wx", mode });
  try { await rename(temporary, path); }
  catch (error) {
    await unlink(temporary).catch(() => {});
    throw new Error(`Atomic upstream write failed for ${path}: ${error.message}`);
  }
}

async function writeExclusiveOrMatch(path, contents) {
  try {
    await writeFile(path, contents, { flag: "wx", mode: 0o600 });
    return false;
  } catch (error) {
    if (error?.code !== "EEXIST") throw error;
  }
  if ((await readBoundedRegularText(path, 10 * 1024 * 1024, "Existing intake report")).text !== contents) {
    fail(`Existing intake report differs from the deterministic candidate: ${path}`);
  }
  return true;
}

function manifestPackages(manifest) {
  const records = {
    openclaw: { version: manifest.openclaw, ...manifest.openclawPackage },
    discord: { version: manifest.openclawPlugins["@openclaw/discord"], ...manifest.openclawPluginPackages.discord },
    searxng: { version: manifest.openclawPlugins["@openclaw/searxng-plugin"], ...manifest.openclawPluginPackages.searxng },
    llamaCpp: { version: manifest.openclawPlugins["@openclaw/llama-cpp-provider"], ...manifest.openclawPluginPackages.llamaCpp },
  };
  return PACKAGE_SPECS.map((spec) => ({ key: spec.key, name: spec.name, ...records[spec.key] }));
}

function assertSupportedCombination(manifest, compatibility) {
  const match = compatibility.combinations.find((item) =>
    item.status === "supported"
    && item.pixel === manifest.pixel
    && item.openclaw === manifest.openclaw
    && JSON.stringify(Object.entries(item.plugins).sort()) === JSON.stringify(Object.entries(manifest.openclawPlugins).sort()));
  if (!match) fail("The pre-intake manifest is not a Supported compatibility combination");
}

async function verifiedArchives(manifest, directory) {
  await ensurePrivateQuarantine(directory);
  const output = [];
  for (const resolved of manifestPackages(manifest)) {
    const file = artifactPath(directory, resolved);
    const verified = await downloadArtifact(resolved, file);
    if (verified.sha256 !== resolved.sha256) fail(`${resolved.name}@${resolved.version} SHA-256 differs from the release manifest`);
    output.push({ ...resolved, file, sha256: verified.sha256, size: verified.size });
  }
  return output;
}

async function extractArchive(record, destination) {
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  const output = execFileSync(python, [join(root, "scripts/extract-upstream-package.py"), record.file, destination, record.sha256], {
    cwd: root,
    encoding: "utf8",
    maxBuffer: 10 * 1024 * 1024,
  });
  return JSON.parse(output);
}

async function check() {
  const { text } = await readManifest();
  const manifest = JSON.parse(text);
  assertJsonSchema(manifest, manifestSchema, "RELEASE-MANIFEST.json");
  const metadata = await fetchAllMetadata();
  const channels = Object.fromEntries(RELEASE_CHANNELS.map((channel) => {
    const candidate = resolveChannel(metadata, channel);
    return [channel, {
      openclaw: candidate.packages.find((item) => item.key === "openclaw").version,
      plugins: Object.fromEntries(candidate.packages.filter((item) => item.key !== "openclaw").map((item) => [item.name, item.version])),
      publication: Object.fromEntries(candidate.packages.map((item) => [item.name, item.publishedAt])),
      differsFromManifest: candidate.packages.some((item) => item.key === "openclaw"
        ? item.version !== manifest.openclaw
        : item.version !== manifest.openclawPlugins[item.name]),
    }];
  }));
  console.log(JSON.stringify({
    schemaVersion: 1,
    operation: "upstream-check",
    registry: REGISTRY_ORIGIN,
    current: { openclaw: manifest.openclaw, plugins: manifest.openclawPlugins },
    channels,
    stateChanged: false,
  }, null, 2));
}

async function prepare(arguments_) {
  const { channel, base } = parsePrepare(arguments_);
  const commit = sourceCommit();
  const statusBefore = sourceStatus();
  if (statusBefore && !onlyPreparedFilesModified(statusBefore)) fail("Upstream prepare requires a clean source tree (or identical previously prepared release files)");
  const { text } = await readManifest();
  const current = JSON.parse(text);
  assertJsonSchema(current, manifestSchema, "RELEASE-MANIFEST.json");
  const compatibilityText = await readFile(compatibilityPath, "utf8");
  const compatibility = JSON.parse(compatibilityText);
  assertJsonSchema(compatibility, compatibilitySchema, "OPENCLAW-COMPATIBILITY.json");
  let supportedManifest = current;
  if (current.upstreamIntake) {
    if (current.upstreamIntake.sourceCommit !== commit) fail("Prepared release manifest belongs to a different source commit");
    const supportedText = execFileSync("git", ["show", `${commit}:RELEASE-MANIFEST.json`], { cwd: root, encoding: "utf8", maxBuffer: 10 * 1024 * 1024 });
    supportedManifest = JSON.parse(supportedText);
    assertJsonSchema(supportedManifest, manifestSchema, "pre-intake release manifest");
  }
  assertSupportedCombination(supportedManifest, compatibility);
  const metadata = await fetchAllMetadata();
  const candidate = resolveChannel(metadata, channel);
  const openclaw = candidate.packages.find((item) => item.key === "openclaw");
  const quarantine = join(base, channel, openclaw.version);
  await ensurePrivateQuarantine(quarantine);
  const artifacts = [];
  for (const resolved of candidate.packages) {
    const file = artifactPath(quarantine, resolved);
    const verified = await downloadArtifact(resolved, file);
    artifacts.push({ ...resolved, file, sha256: verified.sha256, size: verified.size, reused: verified.reused });
  }
  const candidateManifest = buildCandidateManifest(current, candidate, artifacts, commit);
  assertJsonSchema(candidateManifest, manifestSchema, "prepared release manifest");
  const candidateCompatibility = buildCandidateCompatibility(compatibility, candidateManifest, candidate, commit);
  assertJsonSchema(candidateCompatibility, compatibilitySchema, "prepared compatibility matrix");
  const report = buildPrepareReport(candidate, artifacts, candidateManifest, commit, quarantine);
  const reportContents = `${JSON.stringify(report, null, 2)}\n`;
  const reportReused = await writeExclusiveOrMatch(join(quarantine, "prepare-report.json"), reportContents);
  const manifestContents = `${JSON.stringify(candidateManifest, null, 2)}\n`;
  const compatibilityContents = `${JSON.stringify(candidateCompatibility, null, 2)}\n`;
  let manifestChanged = false;
  let compatibilityChanged = false;
  const manifestDiffers = canonicalJson(current) !== canonicalJson(candidateManifest);
  const compatibilityDiffers = canonicalJson(compatibility) !== canonicalJson(candidateCompatibility);
  if (statusBefore && (manifestDiffers || compatibilityDiffers)) fail("Modified prepared release files differ from the resolved candidate; refusing to overwrite them");
  if (!statusBefore && (manifestDiffers || compatibilityDiffers)) {
    if (compatibilityDiffers) {
      await writeAtomic(compatibilityPath, compatibilityContents, (await stat(compatibilityPath)).mode & 0o777);
      compatibilityChanged = true;
    }
    try {
      if (manifestDiffers) await writeAtomic(manifestPath, manifestContents, (await stat(manifestPath)).mode & 0o777);
    } catch (error) {
      if (compatibilityChanged) await writeAtomic(compatibilityPath, compatibilityText, (await stat(compatibilityPath)).mode & 0o777);
      throw error;
    }
    manifestChanged = true;
  }
  console.log(JSON.stringify({ ...report, manifestChanged, compatibilityChanged, reportReused }, null, 2));
}

async function diff(arguments_) {
  const { base, evidence: selectedEvidence } = parseDiff(arguments_);
  const { text } = await readManifest();
  const candidateManifest = JSON.parse(text);
  assertJsonSchema(candidateManifest, manifestSchema, "RELEASE-MANIFEST.json");
  const intake = candidateManifest.upstreamIntake;
  if (!intake || !RELEASE_CHANNELS.includes(intake.channel)) fail("Run ./pixel upstream prepare before diff");
  try { execFileSync("git", ["merge-base", "--is-ancestor", intake.sourceCommit, "HEAD"], { cwd: root, stdio: "ignore" }); }
  catch { fail("Prepared source commit is not an ancestor of the current source"); }
  const supportedText = execFileSync("git", ["show", `${intake.sourceCommit}:RELEASE-MANIFEST.json`], { cwd: root, encoding: "utf8", maxBuffer: 10 * 1024 * 1024 });
  const supportedManifest = JSON.parse(supportedText);
  assertJsonSchema(supportedManifest, manifestSchema, "pre-intake release manifest");
  const compatibility = JSON.parse(await readFile(join(root, "OPENCLAW-COMPATIBILITY.json"), "utf8"));
  assertSupportedCombination(supportedManifest, compatibility);
  const candidateRoot = join(base, intake.channel, candidateManifest.openclaw);
  const supportedRoot = join(base, "supported", supportedManifest.openclaw);
  const candidateArchives = await verifiedArchives(candidateManifest, candidateRoot);
  const supportedArchives = await verifiedArchives(supportedManifest, supportedRoot);
  const analysisRoot = join(base, "analysis", `${supportedManifest.openclaw}-to-${candidateManifest.openclaw}`);
  await ensurePrivateQuarantine(analysisRoot);
  const packageDiffs = [];
  for (const spec of PACKAGE_SPECS) {
    const beforeArchive = supportedArchives.find((item) => item.key === spec.key);
    const afterArchive = candidateArchives.find((item) => item.key === spec.key);
    const beforeRoot = join(analysisRoot, spec.key, "supported");
    const afterRoot = join(analysisRoot, spec.key, "candidate");
    await extractArchive(beforeArchive, beforeRoot);
    await extractArchive(afterArchive, afterRoot);
    const before = await snapshotPackage(beforeRoot, { name: spec.name, version: beforeArchive.version, archiveSha256: beforeArchive.sha256 });
    const after = await snapshotPackage(afterRoot, { name: spec.name, version: afterArchive.version, archiveSha256: afterArchive.sha256 });
    packageDiffs.push(diffSnapshots(before, after));
  }
  let report = buildContractDiff(packageDiffs, intake.sourceCommit);
  report.supportedManifestSha256 = createHash("sha256").update(canonicalJson(supportedManifest)).digest("hex");
  report.candidateManifestSha256 = createHash("sha256").update(canonicalJson(candidateManifest)).digest("hex");
  const reviewPath = join(root, "OPENCLAW-UPSTREAM-REVIEW.json");
  try {
    const review = JSON.parse((await readBoundedRegularText(reviewPath, 1024 * 1024, "OPENCLAW-UPSTREAM-REVIEW.json")).text);
    report = applyContractReview(report, review, {
      candidateManifestSha256: report.candidateManifestSha256,
      intakeSourceCommit: intake.sourceCommit,
    });
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const evidence = selectedEvidence ?? join(analysisRoot, "evidence");
  await ensurePrivateQuarantine(evidence);
  const jsonPath = join(evidence, "contract-diff.json");
  const markdownPath = join(evidence, "contract-diff.md");
  const jsonReused = await writeExclusiveOrMatch(jsonPath, `${JSON.stringify(report, null, 2)}\n`);
  const markdownReused = await writeExclusiveOrMatch(markdownPath, renderContractDiff(report));
  console.log(JSON.stringify({
    schemaVersion: 1,
    operation: "upstream-diff",
    status: report.status,
    blockerCount: report.blockers.length,
    evidence: { json: jsonPath, markdown: markdownPath, reused: jsonReused && markdownReused },
    sourceChanged: false,
  }, null, 2));
}

async function qualify(arguments_) {
  if (process.platform !== "linux") fail("Real-runtime qualification must run inside a disposable Linux host or guest");
  const { base, evidence, work, mode } = parseQualify(arguments_);
  const status = sourceStatus();
  if (status && !onlyPreparedFilesModified(status)) fail("Qualification requires a clean candidate checkout or only the prepared release files to be modified");
  const { text } = await readManifest();
  const candidateManifest = JSON.parse(text);
  assertJsonSchema(candidateManifest, manifestSchema, "RELEASE-MANIFEST.json");
  const intake = candidateManifest.upstreamIntake;
  if (!intake || !RELEASE_CHANNELS.includes(intake.channel)) fail("Run ./pixel upstream prepare before qualification");
  try { execFileSync("git", ["merge-base", "--is-ancestor", intake.sourceCommit, "HEAD"], { cwd: root, stdio: "ignore" }); }
  catch { fail("Prepared source commit is not an ancestor of the current source"); }
  const supportedText = execFileSync("git", ["show", `${intake.sourceCommit}:RELEASE-MANIFEST.json`], { cwd: root, encoding: "utf8", maxBuffer: 10 * 1024 * 1024 });
  const supportedManifest = JSON.parse(supportedText);
  assertJsonSchema(supportedManifest, manifestSchema, "pre-intake release manifest");
  const compatibility = JSON.parse(await readFile(join(root, "OPENCLAW-COMPATIBILITY.json"), "utf8"));
  assertSupportedCombination(supportedManifest, compatibility);
  await verifiedArchives(candidateManifest, join(base, intake.channel, candidateManifest.openclaw));
  await verifiedArchives(supportedManifest, join(base, "supported", supportedManifest.openclaw));
  const inputDirectory = join(base, "qualification-input", intake.sourceCommit);
  await ensurePrivateQuarantine(inputDirectory);
  const supportedPath = join(inputDirectory, "supported-manifest.json");
  await writeExclusiveOrMatch(supportedPath, `${JSON.stringify(supportedManifest, null, 2)}\n`);
  const python = process.env.PYTHON || "python3";
  const output = execFileSync(python, [
    join(root, "scripts/upstream-runtime-probe.py"),
    "--source", root,
    "--candidate-manifest", manifestPath,
    "--supported-manifest", supportedPath,
    "--quarantine", base,
    "--work", work,
    "--evidence", evidence,
    "--qualification-source-commit", sourceCommit(),
    "--mode", mode,
  ], { cwd: root, encoding: "utf8", maxBuffer: 10 * 1024 * 1024, stdio: ["ignore", "pipe", "inherit"] });
  const result = JSON.parse(output.trim().split(/\r?\n/).at(-1));
  console.log(JSON.stringify({ schemaVersion: 1, operation: "upstream-qualify", ...result, sourceChanged: false }, null, 2));
}

const [command, ...arguments_] = process.argv.slice(2);
if (command === "check") {
  if (arguments_.length) fail("Usage: ./pixel upstream check");
  await check();
} else if (command === "prepare") {
  await prepare(arguments_);
} else if (command === "diff") {
  await diff(arguments_);
} else if (command === "qualify") {
  await qualify(arguments_);
} else if (command === "promote") {
  const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
  execFileSync(python, [join(root, "scripts/upstream-attestation.py"), "promote", ...arguments_], { cwd: root, stdio: "inherit" });
} else {
  fail("Usage: ./pixel upstream <check | prepare --channel CHANNEL | diff | qualify | promote --attestation FILE --signature FILE --allowed-signers FILE --identity ID --evidence-dir DIR --evidence-reference HTTPS_URL --confirm>");
}
