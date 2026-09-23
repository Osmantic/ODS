import { createHash } from "node:crypto";

import {
  canonical, validateWorkCapabilityJobAuthorization, validateWorkCapabilityPack,
  validateWorkCapabilityToolCatalog,
} from "../../scripts/lib/work-contract.mjs";
import { capabilityPackSha256 } from "./capability-packs.mjs";

const boundary = "Read-only job-scoped OMP catalog derived from one exact authorization and signed pack. It registers only namespaced tools for this worker and grants no call; every request still requires controller and watchdog admission.";
const authority = Object.freeze({ grantsToolCall: false, grantsCredentials: false, grantsNetwork: false, grantsExternalEffects: false, grantsScopeExpansion: false, grantsCompletion: false });
const SHA_RE = /^[a-f0-9]{64}$/u;

export class CapabilityToolCatalogError extends Error {}

function fail(message) { throw new CapabilityToolCatalogError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function schema(label, errors) { if (errors.length) fail(`${label} is invalid: ${errors[0]}`); }

function exposedName(packId, toolName) {
  const stem = `pixel_cap_${packId}_${toolName}`.toLowerCase().replace(/[^a-z0-9_]/gu, "_");
  return `${stem.slice(0, 55)}_${sha(`${packId}\0${toolName}`).slice(0, 8)}`;
}

export function createCapabilityToolCatalog({ authorization, pack, expectedPackSha256, now = new Date() } = {}) {
  schema("capability catalog authorization", validateWorkCapabilityJobAuthorization(authorization));
  schema("capability catalog signed pack", validateWorkCapabilityPack(pack));
  if (!SHA_RE.test(expectedPackSha256 ?? "") || capabilityPackSha256(pack) !== expectedPackSha256) fail("capability catalog pack differs from its exact signed binding");
  if (
    authorization.pack.id !== pack.id || authorization.pack.version !== pack.version
    || authorization.pack.packSha256 !== expectedPackSha256 || authorization.pack.treeSha256 !== pack.provenance.treeSha256
  ) fail("capability catalog pack differs from its job authorization");
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < Date.parse(authorization.authorizedAt) || now.getTime() >= Date.parse(authorization.expiresAt)) fail("capability catalog time is outside its authorization");
  const declared = new Map(pack.tools.map((tool) => [tool.name, tool]));
  const tools = authorization.tools.map((selected) => {
    const tool = declared.get(selected.name);
    if (!tool || tool.effectClass !== selected.effectClass) fail("capability catalog selection differs from its signed pack");
    return {
      name: tool.name, exposedName: exposedName(pack.id, tool.name), title: tool.title, description: tool.description,
      effectClass: tool.effectClass, inputSchema: structuredClone(tool.inputSchema), inputSchemaSha256: tool.inputSchemaSha256,
      outputSchema: structuredClone(tool.outputSchema), outputSchemaSha256: tool.outputSchemaSha256,
    };
  });
  if (new Set(tools.map((tool) => tool.exposedName)).size !== tools.length) fail("capability catalog exposed tool names collide");
  const catalog = {
    $schema: "https://osmantic.com/pixel/schemas/work-capability-tool-catalog-v1.schema.json", schemaVersion: 1,
    operation: "pixel-work-capability-tool-catalog", createdAt: now.toISOString(), expiresAt: authorization.expiresAt,
    jobId: authorization.jobId, authorizationSha256: sha(authorization), planSha256: authorization.planSha256,
    leaseSha256: authorization.leaseSha256, checkpointSha256: authorization.checkpointSha256, pack: { ...authorization.pack },
    dataClassification: authorization.dataClassification, maxSessions: authorization.maxSessions,
    grantLifetimeMs: authorization.grantLifetimeMs, limits: { ...authorization.limits }, initialEventHeadSha256: null,
    settledSessions: 0, tools, authority: { ...authority }, boundary,
  };
  schema("capability tool catalog", validateWorkCapabilityToolCatalog(catalog));
  return Object.freeze(catalog);
}

export const capabilityToolCatalogBoundary = boundary;
