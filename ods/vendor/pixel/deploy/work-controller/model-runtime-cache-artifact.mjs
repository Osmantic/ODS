import { validateWorkModelRuntimeCacheManifest } from "../../scripts/lib/work-contract.mjs";
import { buildModelArtifactManifest } from "./model-artifact.mjs";

const authority = Object.freeze({ grantsExecution: false, startsContainer: false, grantsNetwork: false, grantsDevices: false, grantsCredentials: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Private deterministic identity for exact owner-selected executable model-runtime cache bytes. Measurement grants no cache trust, execution, container start, network, device, credential, external-effect, or completion authority; use requires an image-bound read-only launch contract.";

export class WorkModelRuntimeCacheArtifactError extends Error {}

export async function buildModelRuntimeCacheManifest({ sourcePath, expectedOwnerUid = process.geteuid?.() ?? 0, readerUid, readerGid }) {
  const measured = await buildModelArtifactManifest({ sourcePath, kind: "directory", expectedOwnerUid, readerUid, readerGid });
  const manifest = {
    $schema: "https://osmantic.com/pixel/schemas/work-model-runtime-cache-manifest-v1.schema.json",
    schemaVersion: 1, kind: "directory", artifactSha256: measured.artifactSha256,
    fileCount: measured.fileCount, totalBytes: measured.totalBytes, files: structuredClone(measured.files),
    authority: { ...authority }, boundary,
  };
  const errors = validateWorkModelRuntimeCacheManifest(manifest);
  if (errors.length) throw new WorkModelRuntimeCacheArtifactError(`model runtime cache manifest is invalid: ${errors[0]}`);
  return Object.freeze(manifest);
}
