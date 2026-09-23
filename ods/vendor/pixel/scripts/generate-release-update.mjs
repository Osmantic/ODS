import { execFileSync } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { mkdir, open, readFile, rename, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { readBoundedRegularFile } from "./lib/secure-files.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const limits = { artifact: 512 * 1024 * 1024, sbom: 32 * 1024 * 1024, provenance: 2 * 1024 * 1024 };

function fail(message) {
  throw new Error(message);
}

function argumentsByName() {
  const values = {};
  for (let index = 2; index < process.argv.length; index += 2) {
    const key = process.argv[index];
    const value = process.argv[index + 1];
    if (!value || !["--artifact", "--sbom", "--provenance", "--output"].includes(key)) fail("Invalid release-update arguments");
    values[key.slice(2)] = resolve(value);
  }
  if (Object.keys(values).length !== 4) {
    fail("Usage: node scripts/generate-release-update.mjs --artifact PATH --sbom PATH --provenance PATH --output PATH");
  }
  return values;
}

async function readInput(path, maximum, label) {
  try {
    return (await readBoundedRegularFile(path, maximum, label)).bytes;
  } catch {
    fail(`${path} is not a bounded regular ${label}`);
  }
}

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

async function atomicWrite(path, contents) {
  await mkdir(dirname(path), { recursive: true });
  const temporary = join(dirname(path), `.${basename(path)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`);
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(contents, "utf8");
    await handle.sync();
    await handle.chmod(0o644);
    await handle.close();
    handle = undefined;
    await rename(temporary, path);
  } finally {
    if (handle) await handle.close().catch(() => {});
    await unlink(temporary).catch((error) => {
      if (error?.code !== "ENOENT") throw error;
    });
  }
}

const paths = argumentsByName();
const version = (await readFile(join(root, "VERSION"), "utf8")).trim();
const manifestBytes = await readInput(join(root, "RELEASE-MANIFEST.json"), 2 * 1024 * 1024, "release manifest");
const compatibilityBytes = await readInput(join(root, "OPENCLAW-COMPATIBILITY.json"), 2 * 1024 * 1024, "compatibility matrix");
const manifest = JSON.parse(manifestBytes);
const compatibility = JSON.parse(compatibilityBytes);
const expectedUpdatePolicy = {
  schemaVersion: 1,
  channel: "stable",
  envelopeSchema: "./schemas/release-update-v1.schema.json",
  stageReceiptSchema: "./schemas/release-update-stage-v1.schema.json",
  rehearsalReceiptSchema: "./schemas/release-update-rehearsal-v1.schema.json",
  activationReceiptSchema: "./schemas/release-update-activation-v1.schema.json",
  activationResultSchema: "./schemas/release-update-activation-result-v1.schema.json",
  rollbackReceiptSchema: "./schemas/release-update-rollback-v1.schema.json",
  rollbackResultSchema: "./schemas/release-update-rollback-result-v1.schema.json",
  recoverySchema: "./schemas/release-update-recovery-v1.schema.json",
  cleanupReceiptSchema: "./schemas/release-update-cleanup-v1.schema.json",
  archiveReceiptSchema: "./schemas/release-update-archive-v1.schema.json",
  signature: "openssh-ed25519-detached",
  signatureNamespace: "pixel-release-update",
  qualificationSignature: "openssh-ed25519-detached",
  qualificationSignatureNamespace: "pixel-release-update-qualification",
  qualificationAuthority: "verify-only-no-publication-staging-activation",
  qualificationMode: "forward",
  minimumUpgradablePixel: "4.0.0",
  preparation: "verify-without-execution",
  staging: "private-copy-without-extraction",
  rehearsal: "private-syntax-and-host-contract-no-candidate-execution",
  activation: "external-exact-confirmation-transactional-apply",
  rollback: "single-use-update-bound-last-apply",
  recovery: "content-free-receipt-finalization-no-candidate-execution",
  cleanup: "exact-quarantine-audit-tombstone",
  archive: "exact-failed-rollback-preservation-outside-bounded-staging",
};
const expectedReactivationArchiveBridge = {
  schemaVersion: 1,
  receiptSchema: "./schemas/release-update-reactivation-archive-v1.schema.json",
  operation: "exact-terminal-no-live-mutation-reactivation-preservation-outside-bounded-staging",
  boundary: "terminal-no-live-mutation-reactivation-evidence-only-no-active-deployment-change",
};
if (
  manifest.pixel !== version
  || version !== "4.3.27"
  || JSON.stringify(manifest.releaseUpdate) !== JSON.stringify(expectedUpdatePolicy)
  || JSON.stringify(manifest.releaseReactivationArchive) !== JSON.stringify(expectedReactivationArchiveBridge)
) {
  fail("Release update policy and VERSION disagree");
}
const samePlugins = (left, right) => {
  if (!left || !right || typeof left !== "object" || typeof right !== "object" || Array.isArray(left) || Array.isArray(right)) return false;
  const leftEntries = Object.entries(left).sort(([a], [b]) => a.localeCompare(b));
  const rightEntries = Object.entries(right).sort(([a], [b]) => a.localeCompare(b));
  return JSON.stringify(leftEntries) === JSON.stringify(rightEntries);
};
const qualificationRecords = compatibility?.combinations?.filter((item) => (
  item?.pixel === version
  && item?.openclaw === manifest.openclaw
  && samePlugins(item?.plugins, manifest.openclawPlugins)
)) ?? [];
if (qualificationRecords.length !== 1) {
  fail("Release has no unique matching compatibility qualification record");
}
const qualificationSourceCommit = qualificationRecords[0]?.evidence?.sourceCommit;
if (typeof qualificationSourceCommit !== "string" || !/^[0-9a-f]{40}$/.test(qualificationSourceCommit)) {
  fail("Release compatibility qualification source commit is invalid");
}
const expectedNames = {
  artifact: `pixel-${version}.tar.gz`,
  sbom: `pixel-${version}.cdx.json`,
  provenance: `pixel-${version}.intoto.jsonl`,
  output: `pixel-${version}.update.json`,
};
for (const [name, expected] of Object.entries(expectedNames)) {
  if (basename(paths[name]) !== expected) fail(`Release update ${name} filename is invalid`);
}
const artifactBytes = await readInput(paths.artifact, limits.artifact, "release archive");
const sbomBytes = await readInput(paths.sbom, limits.sbom, "release SBOM");
const provenanceBytes = await readInput(paths.provenance, limits.provenance, "release provenance");
const sourceCommit = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
const sourceTree = execFileSync("git", ["rev-parse", "HEAD^{tree}"], { cwd: root, encoding: "utf8" }).trim();
const envelope = {
  schemaVersion: 1,
  operation: "pixel-release-update",
  product: "Pixel",
  version,
  channel: manifest.releaseUpdate.channel,
  minimumUpgradablePixel: manifest.releaseUpdate.minimumUpgradablePixel,
  sourceCommit,
  sourceTree,
  qualificationSourceCommit,
  supportedHosts: manifest.supportedHosts,
  releaseManifestSha256: sha256(manifestBytes),
  compatibilitySha256: sha256(compatibilityBytes),
  artifacts: {
    archive: { name: expectedNames.artifact, sha256: sha256(artifactBytes), bytes: artifactBytes.length },
    sbom: { name: expectedNames.sbom, sha256: sha256(sbomBytes), bytes: sbomBytes.length },
    provenance: { name: expectedNames.provenance, sha256: sha256(provenanceBytes), bytes: provenanceBytes.length },
  },
  boundary: "Signed release metadata for verification and preparation only; activation requires a separate exact confirmation.",
};
await atomicWrite(paths.output, `${JSON.stringify(envelope)}\n`);
console.log(`Release update envelope: ${paths.output}`);
