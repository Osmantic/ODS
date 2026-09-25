/**
 * Provider credential ingress test-only module.
 *
 * Exposes a thin wrapper that imports the SAME core implementation
 * (`writeCredentialExclusiveImpl`) used by production, injecting only
 * the deterministic boundary callback needed for the race test.
 * Production must not import this — the static gate in tests/static.sh
 * enforces this.
 *
 * @module
 */
import { unlink, writeFile } from "node:fs/promises";

import {
  writeCredentialExclusiveImpl,
  ProviderCredentialIngressCoreError,
} from "./provider-credential-ingress-internal.mjs";

// ---------------------------------------------------------------------------
// Test-only: deterministic race seam wrapper
//
// Fires `onPostDirFsync` after the directory fsync but before the final
// publication rename. This is the exact vulnerable boundary where a
// concurrent process could create a conflicting final name.
// ---------------------------------------------------------------------------
export async function writeCredentialExclusiveTestOnly(dirPath, fileName, secret, options = {}) {
  const { onPostDirFsync, seams } = options;

  try {
    await writeCredentialExclusiveImpl(dirPath, fileName, secret, onPostDirFsync, seams);
  } catch (err) {
    if (err instanceof ProviderCredentialIngressCoreError) {
      throw new Error(err.message);
    }
    throw err;
  }
}

export { unlink, writeFile };
