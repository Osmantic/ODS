// TEST-ONLY seam. This module is imported ONLY by tests. Production modules must
// never import it (a static gate forbids it), and the public production API
// processCapabilityToolQueueV2 never accepts a runtime or transition seam. The
// deterministic crash/fault and injection tests exercise the real durable
// implementation here through an unmistakably test-only runner. The injected
// executor is imported from the INTERNAL core module, never from the closed
// production surface.
import { processCapabilityToolQueueV2Core, submitCapabilityToolApprovalSignatureV2Core, writeClaimExclusive } from "./capability-tool-queue-v2-core.mjs";

export async function processCapabilityToolQueueV2TestOnly({ queueRoot, config, clock, suffixes, dependencyOverrides = {} }) {
  return processCapabilityToolQueueV2Core({ queueRoot, config, clock, suffixes, deps: dependencyOverrides });
}

// Deterministic inode-swap/collision primitive tests: runs the REAL atomic
// create-only claim with an optional testHook executed after creation+fsync and
// before the no-follow read-back identity check. Production never passes a hook.
export async function writeClaimExclusiveTestOnly(path, value, testHook) {
  return writeClaimExclusive(path, value, undefined, testHook);
}

export async function submitCapabilityToolApprovalSignatureV2TestOnly({ queueRoot, config, requestId, approvalSha256, signature, clock }) {
  return submitCapabilityToolApprovalSignatureV2Core({ queueRoot, config, requestId, approvalSha256, signature, clock });
}

export { processCapabilityToolQueueV2Core };
