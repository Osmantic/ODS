import { executeCapabilityTool } from "./capability-runtime.mjs";

// Runtime dispatch is selected ONLY from the controller-compiled, validated
// grant (its schema version), never from any untrusted request field or
// ambient configuration. v1 is the default and is byte-for-byte unchanged.
// A v2 operational grant is dispatched through the operational v2 path below;
// any unrecognized grant fails closed before any effect.
export class CapabilityRuntimeDispatchError extends Error {}
function fail(message) { throw new CapabilityRuntimeDispatchError(message); }

export async function executeRuntimeDispatch(runtime) {
  const grant = runtime?.grant;
  if (!grant || typeof grant !== "object") fail("capability runtime dispatch requires a controller-compiled grant");
  if (grant.schemaVersion === 1) return executeCapabilityTool(runtime);
  if (grant.schemaVersion === 2) return dispatchOperationalV2(runtime);
  fail(`capability runtime dispatch rejects grant schema "${String(grant.schemaVersion)}" before any effect`);
}

// The v2 operational dispatch is implemented in the operational runtime module.
// It derives lane and effective scope exclusively from the validated grant and
// the trusted controller-owned roots; the untrusted request is never an
// authority. Unsupported targets fail before runtime custody.
async function dispatchOperationalV2(runtime) {
  const { executeOperationalV2 } = await import("./capability-runtime-operational-v2.mjs");
  const config = runtime?.v2?.config;
  if (!config || typeof config !== "object") fail("operational v2 dispatch requires a trusted controller-owned config seam");
  return executeOperationalV2({ grant: runtime.grant, runtimeRequest: runtime.runtimeRequest, config });
}

export const capabilityRuntimeDispatchBoundary = "Capability runtime dispatch selected only from the validated controller grant schema. It grants no replay, future execution, credential, network, external effect, scope expansion, or completion.";
