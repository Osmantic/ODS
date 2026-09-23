/**
 * Provider credential ingress core — production facade.
 *
 * This module re-exports the production-safe subset of the internal
 * implementation (provider-credential-ingress-internal.mjs). Production
 * code imports only from here and cannot pass test-only seams.
 *
 * The internal module contains the full implementation including test-only
 * seam support. It is imported only by the test wrapper
 * (provider-credential-ingress-test.mjs), not by production code.
 *
 * @module
 */
export {
  ProviderCredentialIngressCoreError,
  assertPrivateDirectory,
  writeCredentialExclusive,
  validateCredentialSecret,
  readAllFromFd,
} from "./provider-credential-ingress-internal.mjs";
