// Closed production module for the controller-owned durable v2 capability queue.
//
// This module exports ONLY the fail-closed disabled processing API and the other
// safe public queue operations (initialize/enqueue/status/read). The full durable
// processing implementation, including the test-only injected-executor seam, lives
// in the separate INTERNAL module capability-tool-queue-v2-core.mjs and is
// reachable only there — never through this production surface. Production
// importers of this module can therefore never reach an injected runtime or
// transition executor.
import { canonical } from "../../scripts/lib/work-contract.mjs";
import {
  CapabilityToolQueueV2Error, authority, config, custodyBoundary, exists, fail,
  capabilityToolQueueV2Authority, capabilityToolQueueV2Boundary, capabilityToolQueueV2ConfigBoundary,
  enqueueCapabilityToolRequestV2, initializeCapabilityToolQueueV2,
  processCapabilityToolQueueV2ProductionClosed,
  readCapabilityToolApprovalV2, readCapabilityToolResponseV2,
  statusCapabilityToolQueueV2, submitCapabilityToolApprovalSignatureV2,
} from "./capability-tool-queue-v2-core.mjs";

export { CapabilityToolQueueV2Error };
export {
  capabilityToolQueueV2Authority, capabilityToolQueueV2Boundary, capabilityToolQueueV2ConfigBoundary,
  enqueueCapabilityToolRequestV2, initializeCapabilityToolQueueV2,
  readCapabilityToolApprovalV2, readCapabilityToolResponseV2,
  statusCapabilityToolQueueV2, submitCapabilityToolApprovalSignatureV2,
};

// The production processing path is the deployment enablement gate. When the
// release manifest/config keeps v2 runtime disabled, this exported path NEVER
// acquires, launches, or executes any workspace effect: it fails closed and
// reports the honest disabled state. It does not accept any test-only seam.
// When the immutable config is enabled, processing is delegated to the closed
// production closure in the internal core module, which also exposes no clock,
// suffix, dependency, runtime, transition, or executor seam.
export async function processCapabilityToolQueueV2({ queueRoot, config: candidate }) {
  const { config: cfg, paths } = await config(queueRoot);
  if (candidate !== undefined && canonical(candidate) !== canonical(cfg)) fail("capability v2 queue config seam differs from the immutable root");
  if (cfg.enabled !== true && cfg.enabled !== false) fail("capability v2 queue enabled flag is invalid");
  if (cfg.enabled === true) {
    return processCapabilityToolQueueV2ProductionClosed({ queueRoot, config: cfg });
  }
  if (await exists(paths.terminal)) return Object.freeze({ schemaVersion: 2, status: "stopped-disabled", enabled: false, authority: { ...authority }, boundary: custodyBoundary });
  return Object.freeze({ schemaVersion: 2, status: "disabled", enabled: false, authority: { ...authority }, boundary: custodyBoundary });
}
