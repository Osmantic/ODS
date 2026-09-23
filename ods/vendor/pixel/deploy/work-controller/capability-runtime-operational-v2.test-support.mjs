// TEST-ONLY seam. This module is imported ONLY by tests; production modules must
// never import it (a static gate forbids it). It exposes the descriptor-bound
// create-only workspace effect (with enforced single-filename-component
// validation) for the deterministic root-swap and traversal tests. The effect is
// NOT exported from the production module surface.
import { createFileInWorkspace } from "./capability-runtime-operational-v2-workspace.mjs";

export { createFileInWorkspace };
