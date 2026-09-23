/**
 * Provider smoke test support module.
 *
 * Thin wrapper around provider-smoke-core.mjs that supplies a hermetic
 * executeTransport and optional clock/proxy overrides. Production modules
 * must not import this — the static gate in tests/static.sh enforces this.
 */
import { isAbsolute, parse, resolve } from "node:path";

import { readOwnerPrivateWorkProviderPolicy } from "./dev-proxy-cli.mjs";
import { parseBoundWorkProviderPrivatePolicy } from "./egress-proxy.mjs";
import { runProviderSmokeCore, ProviderSmokeCoreError } from "./provider-smoke-core.mjs";

const SHA = /^[a-f0-9]{64}$/u;

function fail(message) {
  throw new Error(`provider smoke test-only: ${message}`);
}

function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) === parse(resolve(value)).root) {
    fail(`${label} is invalid`);
  }
  return resolve(value);
}

function parseTestSmokeArgs(argv) {
  if (!Array.isArray(argv) || argv.length !== 8) {
    fail("usage: provider-smoke-cli.mjs --policy PATH --sha256 HEX --credential PATH --ledger-root PATH");
  }
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!["--credential", "--ledger-root", "--policy", "--sha256"].includes(name) || values.has(name)) {
      fail("provider smoke arguments are invalid");
    }
    values.set(name, value);
  }
  const sha256 = values.get("--sha256");
  if (!SHA.test(sha256 ?? "")) {
    fail("provider smoke policy SHA-256 is invalid");
  }
  return {
    policyPath: absolutePath(values.get("--policy"), "provider smoke policy path"),
    credentialPath: absolutePath(values.get("--credential"), "provider smoke credential path"),
    ledgerRoot: absolutePath(values.get("--ledger-root"), "provider smoke ledger root"),
    sha256,
  };
}

/**
 * Test-only provider smoke with hermetic execute injection.
 *
 * @param {string[]} argv - CLI arguments.
 * @param {object} options
 * @param {Function} options.execute - Hermetic execute replacement.
 * @param {Function} [options.now] - Time override.
 * @param {string} [options.proxyHost] - Proxy host override.
 * @returns {Promise<object>} Content-free smoke receipt.
 */
export async function runProviderSmokeTestOnly(argv, { execute, now = () => new Date(), proxyHost = "127.0.0.1" } = {}) {
  if (typeof execute !== "function") {
    fail("execute function is required for test-only smoke");
  }

  const values = parseTestSmokeArgs(argv);
  const parsed = parseBoundWorkProviderPrivatePolicy(
    await readOwnerPrivateWorkProviderPolicy(values.policyPath),
    values.sha256,
  );
  const providerId = parsed.resolvedProvider.profile.id;

  return runProviderSmokeCore({
    executeTransport: execute,
    now,
    proxyHost,
    parsed,
    credentialPath: values.credentialPath,
    ledgerRoot: values.ledgerRoot,
    providerId,
  });
}
