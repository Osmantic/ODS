import { createHash } from "node:crypto";
import { lstat, mkdir, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";
import { readBoundedRegularFile } from "./secure-files.mjs";

export const REGISTRY_ORIGIN = "https://registry.npmjs.org";
export const RELEASE_CHANNELS = ["extended-stable", "latest"];
export const PACKAGE_SPECS = [
  { key: "openclaw", name: "openclaw", archive: "openclaw" },
  { key: "discord", name: "@openclaw/discord", archive: "discord" },
  { key: "searxng", name: "@openclaw/searxng-plugin", archive: "searxng-plugin" },
  { key: "llamaCpp", name: "@openclaw/llama-cpp-provider", archive: "llama-cpp-provider" },
];

const MAX_METADATA_BYTES = 25 * 1024 * 1024;
const MAX_ARTIFACT_BYTES = 100 * 1024 * 1024;
const STABLE_VERSION = /^\d{4}\.\d+\.\d+(?:-\d+)?$/;
const INTEGRITY = /^sha512-([A-Za-z0-9+/]+=*)$/;

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function metadataUrl(packageName) {
  requireValue(PACKAGE_SPECS.some((item) => item.name === packageName), `Unsupported upstream package: ${packageName}`);
  return `${REGISTRY_ORIGIN}/${encodeURIComponent(packageName)}`;
}

export function assertRegistryTarball(spec, version, tarball) {
  const url = new URL(tarball);
  requireValue(url.origin === REGISTRY_ORIGIN && url.protocol === "https:" && !url.username && !url.password && !url.port, `${spec.name}@${version} tarball is outside the authoritative npm registry`);
  requireValue(decodeURIComponent(url.pathname).endsWith(`/-/${spec.archive}-${version}.tgz`), `${spec.name}@${version} tarball path does not match the selected version`);
  return url.href;
}

export function resolvePackage(spec, metadata, channel) {
  requireValue(RELEASE_CHANNELS.includes(channel), `Unsupported release channel: ${channel}`);
  const version = metadata?.["dist-tags"]?.[channel];
  requireValue(typeof version === "string", `${spec.name} has no ${channel} dist-tag`);
  requireValue(STABLE_VERSION.test(version), `${spec.name} ${channel} resolved to non-stable version ${version}`);
  const release = metadata?.versions?.[version];
  requireValue(release?.version === version, `${spec.name}@${version} metadata is incomplete`);
  const integrity = release?.dist?.integrity;
  requireValue(INTEGRITY.test(integrity ?? ""), `${spec.name}@${version} has no valid sha512 npm integrity`);
  const publishedAt = metadata?.time?.[version];
  requireValue(typeof publishedAt === "string" && !Number.isNaN(Date.parse(publishedAt)), `${spec.name}@${version} has no valid publication time`);
  return {
    key: spec.key,
    name: spec.name,
    version,
    url: assertRegistryTarball(spec, version, release.dist.tarball),
    integrity,
    publishedAt,
  };
}

export function resolveChannel(metadataByName, channel) {
  const packages = PACKAGE_SPECS.map((spec) => resolvePackage(spec, metadataByName[spec.name], channel));
  return { channel, registry: REGISTRY_ORIGIN, packages };
}

export async function fetchPackageMetadata(spec, fetchImpl = fetch) {
  const url = metadataUrl(spec.name);
  const response = await fetchImpl(url, {
    headers: { accept: "application/json", "user-agent": "Pixel-upstream-intake/1" },
    redirect: "error",
    signal: AbortSignal.timeout(30_000),
  });
  requireValue(response.ok && response.url.startsWith(`${REGISTRY_ORIGIN}/`), `${spec.name} registry metadata request failed with HTTP ${response.status}`);
  const declared = Number(response.headers.get("content-length") ?? 0);
  requireValue(!declared || declared <= MAX_METADATA_BYTES, `${spec.name} registry metadata exceeded the size limit`);
  const text = await response.text();
  requireValue(Buffer.byteLength(text) <= MAX_METADATA_BYTES, `${spec.name} registry metadata exceeded the size limit`);
  return JSON.parse(text);
}

export async function fetchAllMetadata(fetchImpl = fetch) {
  const entries = await Promise.all(PACKAGE_SPECS.map(async (spec) => [spec.name, await fetchPackageMetadata(spec, fetchImpl)]));
  return Object.fromEntries(entries);
}

function verifyBytes(bytes, expectedIntegrity) {
  const match = INTEGRITY.exec(expectedIntegrity);
  requireValue(match, "Artifact npm integrity is invalid");
  const integrity = `sha512-${createHash("sha512").update(bytes).digest("base64")}`;
  requireValue(integrity === expectedIntegrity, "Artifact failed npm integrity verification");
  return { sha256: sha256(bytes), integrity, size: bytes.length };
}

async function readExistingArtifact(path, expectedIntegrity) {
  const { bytes } = await readBoundedRegularFile(path, MAX_ARTIFACT_BYTES, "Quarantine artifact");
  return { bytes, ...verifyBytes(bytes, expectedIntegrity), reused: true };
}

export async function downloadArtifact(resolved, destination, fetchImpl = fetch) {
  const spec = PACKAGE_SPECS.find((item) => item.name === resolved.name);
  requireValue(spec, `Unsupported upstream package: ${resolved.name}`);
  requireValue(assertRegistryTarball(spec, resolved.version, resolved.url) === resolved.url, `${resolved.name}@${resolved.version} tarball URL is not canonical`);
  try {
    return await readExistingArtifact(destination, resolved.integrity);
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  const response = await fetchImpl(resolved.url, {
    headers: { accept: "application/octet-stream", "user-agent": "Pixel-upstream-intake/1" },
    redirect: "error",
    signal: AbortSignal.timeout(300_000),
  });
  requireValue(response.ok && response.url === resolved.url, `${resolved.name}@${resolved.version} download failed with HTTP ${response.status}`);
  const declared = Number(response.headers.get("content-length") ?? 0);
  requireValue(!declared || declared <= MAX_ARTIFACT_BYTES, `${resolved.name}@${resolved.version} exceeded the artifact size limit`);
  const bytes = Buffer.from(await response.arrayBuffer());
  requireValue(bytes.length <= MAX_ARTIFACT_BYTES, `${resolved.name}@${resolved.version} exceeded the artifact size limit`);
  const verified = verifyBytes(bytes, resolved.integrity);
  await writeFile(destination, bytes, { flag: "wx", mode: 0o600 });
  return { bytes, ...verified, reused: false };
}

export async function ensurePrivateQuarantine(path) {
  await mkdir(path, { recursive: true, mode: 0o700 });
  const details = await lstat(path);
  requireValue(details.isDirectory() && !details.isSymbolicLink(), `Quarantine is not a real directory: ${path}`);
  if (process.platform !== "win32") requireValue((details.mode & 0o077) === 0, `Quarantine permissions are not private: ${path}`);
}

export function buildCandidateManifest(current, candidate, artifacts, sourceCommit) {
  const byKey = Object.fromEntries(candidate.packages.map((item) => [item.key, item]));
  const evidence = Object.fromEntries(artifacts.map((item) => [item.key, item]));
  const artifact = (key) => ({
    url: byKey[key].url,
    sha256: evidence[key].sha256,
    integrity: byKey[key].integrity,
    publishedAt: byKey[key].publishedAt,
  });
  return {
    ...current,
    openclaw: byKey.openclaw.version,
    openclawPackage: artifact("openclaw"),
    openclawPlugins: {
      "@openclaw/discord": byKey.discord.version,
      "@openclaw/searxng-plugin": byKey.searxng.version,
      "@openclaw/llama-cpp-provider": byKey.llamaCpp.version,
    },
    openclawPluginPackages: {
      discord: artifact("discord"),
      searxng: artifact("searxng"),
      llamaCpp: artifact("llamaCpp"),
    },
    upstreamIntake: {
      schemaVersion: 1,
      registry: REGISTRY_ORIGIN,
      channel: candidate.channel,
      sourceCommit,
    },
  };
}

export function buildCandidateCompatibility(current, candidateManifest, candidate, sourceCommit) {
  const combinations = current.combinations ?? [];
  const plugins = candidateManifest.openclawPlugins;
  const sameIdentity = (item) => item.pixel === candidateManifest.pixel
    && item.openclaw === candidateManifest.openclaw
    && canonicalJson(item.plugins) === canonicalJson(plugins);
  const openclaw = candidate.packages.find((item) => item.key === "openclaw");
  requireValue(openclaw?.publishedAt, "Candidate OpenClaw publication time is missing");
  const record = {
    pixel: candidateManifest.pixel,
    openclaw: candidateManifest.openclaw,
    plugins,
    status: "candidate",
    qualifiedAt: openclaw.publishedAt.slice(0, 10),
    evidence: {
      sourceCommit,
      liveAudit: "UPSTREAM-RELEASE-CHECKLIST.md",
    },
  };
  const existing = combinations.filter(sameIdentity);
  requireValue(existing.length <= 1, "Compatibility matrix has duplicate candidate identity records");
  if (existing.length === 1) {
    requireValue(canonicalJson(existing[0]) === canonicalJson(record), "Existing candidate compatibility record differs from the resolved intake");
    return current;
  }
  requireValue(!combinations.some((item) => item.status === "candidate"), "Compatibility matrix already contains another Candidate combination");
  return { ...current, combinations: [...combinations, record] };
}

export function buildPrepareReport(candidate, artifacts, candidateManifest, sourceCommit, quarantine) {
  const records = artifacts.map(({ key, name, version, url, integrity, publishedAt, sha256: digest, size, file }) => ({
    key, name, version, url, integrity, publishedAt, sha256: digest, size, file: basename(file),
  }));
  const manifestSha256 = sha256(Buffer.from(canonicalJson(candidateManifest)));
  return {
    schemaVersion: 1,
    operation: "upstream-prepare",
    registry: REGISTRY_ORIGIN,
    channel: candidate.channel,
    sourceCommit,
    quarantine,
    candidateManifestSha256: manifestSha256,
    artifacts: records,
    candidateBranch: `candidate/openclaw-${candidate.packages[0].version}`,
    pullRequestTitle: `Qualify OpenClaw ${candidate.packages[0].version} for Pixel`,
    nextCommands: [
      "node scripts/generate-release-files.mjs --write",
      "./pixel upstream diff",
      "./pixel upstream qualify",
    ],
  };
}

export function artifactPath(quarantine, resolved) {
  return join(quarantine, `${resolved.key}-${resolved.version}.tgz`);
}
