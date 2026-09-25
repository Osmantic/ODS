import { createHash, randomBytes } from "node:crypto";
import { mkdir, open, readFile, rename, unlink } from "node:fs/promises";
import { basename, dirname, join, resolve } from "node:path";
import { readBoundedRegularFile } from "./lib/secure-files.mjs";

const boundary = "Content-free verification receipt for one observed local deployment. It binds source, installed manifests, configuration, profiles, and connector checks. Model capability remains unproven until a separate exact real-backend qualification receipt exists.";
const sourceBoundary = "Content-free source and baseline-qualification identity only. This record does not prove installed-byte integrity, runtime health, provider capability, release promotion, publication, deployment approval, or client acceptance.";
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const sha256Pattern = /^[a-f0-9]{64}$/u;
const gitPattern = /^[a-f0-9]{40}$/u;

function fail(message) { throw new Error(message); }

function exactKeys(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value)
    && Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}

function validatedIdentity(identity, version) {
  if (!exactKeys(identity, ["schemaVersion", "kind", "pixel", "source", "manifests", "qualification", "boundary"])
      || identity.schemaVersion !== 1 || identity.kind !== "pixel-release-source-identity"
      || identity.pixel !== version || identity.boundary !== sourceBoundary) fail("release source identity is invalid");
  const source = identity.source;
  if (!exactKeys(source, ["state", "commit", "tree"]) || !["git-clean", "unavailable"].includes(source.state)) fail("release source identity is invalid");
  if (source.state === "git-clean") {
    if (!gitPattern.test(source.commit) || !gitPattern.test(source.tree)) fail("release source identity is invalid");
  } else if (source.commit !== null || source.tree !== null) fail("release source identity is invalid");
  if (!exactKeys(identity.manifests, ["releaseSha256", "compatibilitySha256", "qualificationMatrixSha256"])
      || !Object.values(identity.manifests).every((value) => typeof value === "string" && sha256Pattern.test(value))) fail("release manifest identity is invalid");
  const qualification = identity.qualification;
  if (!exactKeys(qualification, ["recordStatus", "sourceCommit", "qualifiedAt", "liveAudit", "relationship"])
      || !["supported", "candidate", "blocked", "retired", null].includes(qualification.recordStatus)
      || (qualification.sourceCommit !== null && (typeof qualification.sourceCommit !== "string" || !gitPattern.test(qualification.sourceCommit)))
      || (qualification.qualifiedAt !== null && (typeof qualification.qualifiedAt !== "string" || !/^\d{4}-\d{2}-\d{2}$/u.test(qualification.qualifiedAt)))
      || (qualification.liveAudit !== null && (typeof qualification.liveAudit !== "string" || !/^.{1,255}$/u.test(qualification.liveAudit)))
      || !["same-source", "qualified-ancestor", "unverified", "unavailable"].includes(qualification.relationship)) fail("release qualification identity is invalid");
  if ((source.state === "unavailable") !== (qualification.relationship === "unavailable")) fail("release qualification relationship is invalid");
  if (["same-source", "qualified-ancestor"].includes(qualification.relationship) && qualification.sourceCommit === null) fail("release qualification relationship is invalid");
  if (qualification.relationship === "same-source" && qualification.sourceCommit !== source.commit) fail("release qualification relationship is invalid");
  return identity;
}

function argumentsByName() {
  const allowed = new Set(["--source-root", "--active-release", "--configuration", "--output", "--endpoint-state"]);
  const values = {};
  for (let index = 2; index < process.argv.length; index += 2) {
    const key = process.argv[index];
    const value = process.argv[index + 1];
    if (!allowed.has(key) || !value || values[key]) fail("Invalid runtime attestation arguments");
    values[key] = value;
  }
  if (Object.keys(values).length !== allowed.size || !["verified", "skipped"].includes(values["--endpoint-state"])) fail("Usage: runtime-attestation.mjs --source-root PATH --active-release PATH --configuration PATH --output PATH --endpoint-state verified|skipped");
  return {
    sourceRoot: resolve(values["--source-root"]), activeRelease: resolve(values["--active-release"]),
    configuration: resolve(values["--configuration"]), output: resolve(values["--output"]),
    endpointState: values["--endpoint-state"],
  };
}

async function bounded(path, maximum = 8 * 1024 * 1024) {
  return (await readBoundedRegularFile(path, maximum, "runtime attestation input")).bytes;
}

async function json(path) {
  const value = JSON.parse((await bounded(path, 2 * 1024 * 1024)).toString("utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(`${basename(path)} is not a JSON object`);
  return value;
}

function safeText(value, pattern, label) {
  if (typeof value !== "string" || !pattern.test(value)) fail(`${label} is invalid`);
  return value;
}

function safeInteger(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}

async function atomicJson(path, value) {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = join(dirname(path), `.${basename(path)}.${process.pid}.${randomBytes(8).toString("hex")}.tmp`);
  let handle;
  try {
    handle = await open(temporary, "wx", 0o600);
    await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8");
    await handle.sync();
    await handle.chmod(0o600);
    await handle.close();
    handle = undefined;
    await rename(temporary, path);
  } finally {
    if (handle) await handle.close().catch(() => {});
    await unlink(temporary).catch((error) => { if (error?.code !== "ENOENT") throw error; });
  }
}

const paths = argumentsByName();
const releaseManifest = await json(join(paths.sourceRoot, "RELEASE-MANIFEST.json"));
const identityBytes = await bounded(join(paths.activeRelease, "release-identity.json"));
const identity = JSON.parse(identityBytes.toString("utf8"));
const deploymentBytes = await bounded(join(paths.sourceRoot, ".generated", "deployment.json"));
const deployment = JSON.parse(deploymentBytes.toString("utf8"));
const configurationBytes = await bounded(paths.configuration);
const version = safeText(releaseManifest.pixel, /^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$/u, "Pixel version");
validatedIdentity(identity, version);
const source = identity.source;
const deploymentProfile = safeText(deployment.deploymentProfile, /^(?:prepared|reference)$/u, "deployment profile");
const capabilityProfile = safeText(deployment.capabilityProfile, /^(?:minimal|chief-of-staff|research|engineering-operator)$/u, "capability profile");
const modelProvider = safeText(deployment.modelProvider, /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/u, "model provider");
const modelId = safeText(deployment.modelId, /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$/u, "model ID");
const limbs = deployment.limbs;
const connectorIds = ["email", "calendar", "social", "web", "operations", "frontier"];
if (!limbs || connectorIds.some((id) => typeof limbs[id] !== "boolean")) fail("connector configuration is invalid");
const fileDigest = async (name) => hash(await bounded(join(paths.activeRelease, name)));
const receipt = {
  schemaVersion: 1,
  kind: "pixel-runtime-attestation",
  status: paths.endpointState === "verified" ? "verified" : "limited",
  verifiedAt: new Date().toISOString(),
  pixel: version,
  source: { state: source.state, commit: source.commit ?? null, tree: source.tree ?? null },
  qualification: {
    recordStatus: identity.qualification?.recordStatus ?? null,
    sourceCommit: identity.qualification?.sourceCommit ?? null,
    qualifiedAt: identity.qualification?.qualifiedAt ?? null,
    relationship: identity.qualification?.relationship ?? "unavailable",
  },
  release: {
    sourceIdentitySha256: hash(identityBytes),
    deploymentInputsSha256: await fileDigest("deployment-inputs.sha256"),
    sourceRuntimeSha256: await fileDigest("source-runtime.sha256"),
    installManifestSha256: await fileDigest("install-manifest.sha256"),
    releaseManifestSha256: identity.manifests?.releaseSha256,
    compatibilityManifestSha256: identity.manifests?.compatibilitySha256,
    qualificationMatrixSha256: identity.manifests?.qualificationMatrixSha256,
  },
  configuration: { generatedDeploymentSha256: hash(deploymentBytes), activeOpenClawSha256: hash(configurationBytes) },
  runtime: {
    state: "gateway-verified-model-unproven",
    openclaw: safeText(releaseManifest.openclaw, /^[0-9]{4}\.[0-9]+\.[0-9]+(?:-[0-9]+)?$/u, "OpenClaw version"),
    routeClass: modelProvider === "local" ? "local" : "configured",
    providerIdSha256: hash(Buffer.from(modelProvider, "utf8")), modelIdSha256: hash(Buffer.from(modelId, "utf8")),
    contextWindow: safeInteger(deployment.modelContextWindow, 4096, 10_000_000, "model context"),
    maxOutputTokens: safeInteger(deployment.modelMaxTokens, 256, 1_000_000, "model output limit"),
    reasoning: typeof deployment.modelReasoning === "boolean" ? deployment.modelReasoning : fail("model reasoning flag is invalid"),
    endpointChecks: paths.endpointState,
  },
  profiles: { deployment: deploymentProfile, capability: capabilityProfile },
  connectors: connectorIds.map((id) => ({ id, state: limbs[id] ? "enabled-verified" : "disabled-verified" })),
  boundary,
};
for (const digest of Object.values(receipt.release)) if (!sha256Pattern.test(digest ?? "")) fail("release digest is invalid");
await atomicJson(paths.output, receipt);
console.log(JSON.stringify({ status: receipt.status, verifiedAt: receipt.verifiedAt, pixel: receipt.pixel }));
