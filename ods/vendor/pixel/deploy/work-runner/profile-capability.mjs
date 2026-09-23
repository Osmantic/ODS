import { createHash } from "node:crypto";
import { posix } from "node:path";

import {
  canonical, validateWorkCapabilityJobAuthorization, validateWorkCapabilityToolCatalog,
} from "../../scripts/lib/work-contract.mjs";

const SAFE_PATH_RE = /^\/[A-Za-z0-9._/-]+$/u;
const EXPOSED_RE = /^pixel_cap_[a-z0-9_]{1,54}$/u;
const CATALOG_RE = /^catalog-[a-f0-9]{64}\.json$/u;
const TRUSTED_EXTENSION = "/opt/pixel/deploy/work-runner/capability-tool.mjs";
const CONTAINER_ROOT = "/run/pixel/capability";

export class ProfileCapabilityError extends Error {}

function fail(message) { throw new ProfileCapabilityError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" ? value : canonical(value)).digest("hex"); }
function safePath(value, label) {
  if (typeof value !== "string" || !posix.isAbsolute(value) || !SAFE_PATH_RE.test(value) || value.includes("//") || posix.normalize(value) !== value) fail(`${label} is not a canonical safe Linux path`);
  return value;
}
function bind(source, target, readonly = true) { return `type=bind,src=${source},dst=${target}${readonly ? ",readonly" : ""}`; }

export function deriveProfileCapabilityRuntime(prepared, claim, runRoot, capability) {
  if (capability === undefined || capability === null) return null;
  if (!capability || typeof capability !== "object" || Array.isArray(capability)) fail("profile capability configuration is invalid");
  const { authorization, catalog, requestContext } = capability;
  const authorizationErrors = validateWorkCapabilityJobAuthorization(authorization), catalogErrors = validateWorkCapabilityToolCatalog(catalog);
  if (authorizationErrors.length || catalogErrors.length) fail(`profile capability contracts are invalid: ${authorizationErrors[0] ?? catalogErrors[0]}`);
  if (
    authorization.jobId !== prepared?.plan?.jobId || authorization.jobId !== claim?.jobId
    || authorization.planSha256 !== claim.planSha256 || authorization.leaseSha256 !== claim.leaseSha256
    || authorization.consumptionSha256 !== sha(claim) || authorization.dataClassification !== prepared.plan.dataClassification
    || authorization.checkpointSha256 !== sha(requestContext?.checkpoint)
    || canonical(requestContext?.plan) !== canonical(prepared.plan) || canonical(requestContext?.lease) !== canonical(prepared.lease)
    || canonical(requestContext?.consumption) !== canonical(claim) || requestContext?.checkpoint?.state !== "running"
    || catalog.jobId !== claim.jobId || catalog.authorizationSha256 !== sha(authorization)
    || catalog.planSha256 !== claim.planSha256 || catalog.leaseSha256 !== claim.leaseSha256
    || catalog.checkpointSha256 !== authorization.checkpointSha256 || catalog.dataClassification !== authorization.dataClassification
  ) fail("profile capability differs from the consumed job and running checkpoint");
  if (!capability.pack || typeof capability.pack !== "object" || !capability.expectedPackSha256 || !capability.runtime || typeof capability.runtime !== "object" || Array.isArray(capability.runtime)) fail("profile capability controller inputs are incomplete");
  safePath(runRoot, "profile capability run root");
  const serviceRoot = posix.join(runRoot, "capability"), workerRoot = posix.join(serviceRoot, "worker");
  const catalogSha256 = sha(catalog), catalogName = `catalog-${catalogSha256}.json`;
  const exposedTools = catalog.tools.map((tool) => tool.exposedName);
  if (new Set(exposedTools).size !== exposedTools.length || exposedTools.some((name) => !EXPOSED_RE.test(name))) fail("profile capability exposed tool set is invalid");
  return Object.freeze({
    serviceRoot, workerRoot, catalogPath: posix.join(workerRoot, catalogName),
    requestDirectory: posix.join(workerRoot, "requests"), responseDirectory: posix.join(workerRoot, "responses"),
    catalogName, catalogSha256, exposedTools: Object.freeze([...exposedTools]),
  });
}

export function capabilityWorkerCommand(runtimeCapability) {
  if (runtimeCapability === null || runtimeCapability === undefined) return Object.freeze({ mountArgs: [], extensionArgs: [], exposedTools: [], trustedExtension: null });
  const catalogPath = safePath(runtimeCapability.catalogPath, "capability catalog path");
  const requestDirectory = safePath(runtimeCapability.requestDirectory, "capability request path");
  const responseDirectory = safePath(runtimeCapability.responseDirectory, "capability response path");
  if (!CATALOG_RE.test(posix.basename(catalogPath)) || posix.dirname(catalogPath) !== runtimeCapability.workerRoot || posix.dirname(requestDirectory) !== runtimeCapability.workerRoot || posix.dirname(responseDirectory) !== runtimeCapability.workerRoot || posix.basename(requestDirectory) !== "requests" || posix.basename(responseDirectory) !== "responses") fail("profile capability worker paths differ from the exact split queue");
  const exposedTools = [...runtimeCapability.exposedTools];
  if (!exposedTools.length || new Set(exposedTools).size !== exposedTools.length || exposedTools.some((name) => !EXPOSED_RE.test(name))) fail("profile capability exposed tools are invalid");
  return Object.freeze({
    mountArgs: Object.freeze([
      "--mount", bind(catalogPath, posix.join(CONTAINER_ROOT, posix.basename(catalogPath))),
      "--mount", bind(requestDirectory, posix.join(CONTAINER_ROOT, "requests"), false),
      "--mount", bind(responseDirectory, posix.join(CONTAINER_ROOT, "responses")),
    ]),
    extensionArgs: Object.freeze(["--trusted-extension", TRUSTED_EXTENSION]),
    exposedTools: Object.freeze(exposedTools), trustedExtension: TRUSTED_EXTENSION,
  });
}

export function profileAllowedTools(baseTools, runtimeCapability) {
  if (!Array.isArray(baseTools) || baseTools.some((name) => typeof name !== "string")) fail("profile base tools are invalid");
  if (runtimeCapability === null || runtimeCapability === undefined) return [...baseTools];
  const capability = capabilityWorkerCommand(runtimeCapability);
  const combined = [...new Set([...baseTools, ...capability.exposedTools])].sort();
  if (combined.length !== baseTools.length + capability.exposedTools.length) fail("profile capability tool name collides with an existing tool");
  return combined;
}

export const profileCapabilityContract = Object.freeze({ trustedExtension: TRUSTED_EXTENSION, containerRoot: CONTAINER_ROOT });
