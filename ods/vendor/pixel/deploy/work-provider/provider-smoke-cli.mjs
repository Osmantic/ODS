/**
 * Production provider smoke CLI — thin wrapper around provider-smoke-core.mjs.
 *
 * Resolves the transport through the closed transport-registry.mjs and passes
 * only the real clock. Accepts no caller-supplied execution, test seams, or
 * unknown option shapes.
 */
import { isAbsolute, parse, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { readOwnerPrivateWorkProviderPolicy } from "./dev-proxy-cli.mjs";
import { parseBoundWorkProviderPrivatePolicy } from "./egress-proxy.mjs";
import { resolveWorkProviderTransport } from "./transport-registry.mjs";
import { runProviderSmokeCore } from "./provider-smoke-core.mjs";

const SHA = /^[a-f0-9]{64}$/u;

export class ProviderSmokeCliError extends Error {
  constructor(message) {
    super(message);
    this.name = "ProviderSmokeCliError";
  }
}

function fail(message) {
  throw new ProviderSmokeCliError(message);
}

function absolutePath(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) === parse(resolve(value)).root) {
    fail(`${label} is invalid`);
  }
  return resolve(value);
}

function parseSmokeArgs(argv) {
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
  return Object.freeze({
    policyPath: absolutePath(values.get("--policy"), "provider smoke policy path"),
    credentialPath: absolutePath(values.get("--credential"), "provider smoke credential path"),
    ledgerRoot: absolutePath(values.get("--ledger-root"), "provider smoke ledger root"),
    sha256,
  });
}

/**
 * Run a unified provider smoke test for moonshot-kimi, openai, or anthropic.
 *
 * Production API: accepts only `argv`. Uses the real clock, the fixed
 * production proxy host (127.0.0.1), and the closed transport-registry
 * resolve. No caller-supplied clock, proxy, transport, policy object,
 * credential path, or ledger root injection — all come from CLI arguments
 * and fixed implementation.
 *
 * This is a connectivity qualification only — it proves credential and wire
 * path are functional. It is NOT effectiveness parity with local or other
 * provider lanes. Parity requires independent neutral-corpus execution and
 * grading.
 *
 * @param {string[]} [argv] - CLI arguments (defaults to process.argv.slice(2)).
 * @returns {Promise<object>} Content-free smoke receipt.
 */
export async function runProviderSmoke(
  argv = process.argv.slice(2),
) {
  // Fail closed: reject any second argument before path reads or other effects.
  // JavaScript ignores extra arguments silently — we must not.
  if (arguments.length > 1) {
    fail("runProviderSmoke accepts exactly one argument (argv)");
  }

  const parsedArgs = parseSmokeArgs(argv);
  const parsed = parseBoundWorkProviderPrivatePolicy(
    await readOwnerPrivateWorkProviderPolicy(parsedArgs.policyPath),
    parsedArgs.sha256,
  );
  const providerId = parsed.resolvedProvider.profile.id;

  // Resolve the transport through the closed registry.
  const executeTransport = resolveWorkProviderTransport(providerId);

  // Fixed production values — no injection
  const productionProxyHost = "127.0.0.1";

  return runProviderSmokeCore({
    executeTransport,
    // Real clock — no override
    proxyHost: productionProxyHost,
    parsed,
    credentialPath: parsedArgs.credentialPath,
    ledgerRoot: parsedArgs.ledgerRoot,
    providerId,
  });
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runProviderSmoke().then((receipt) => {
    process.stdout.write(`${JSON.stringify(receipt)}\n`);
    if (receipt.status !== "passed") process.exitCode = 1;
  }).catch((err) => {
    const message =
      err instanceof ProviderSmokeCliError
        ? err.message
        : "provider smoke failed";
    process.stderr.write(
      `Pixel provider smoke failed closed: ${message}. Inspect the content-free run ledger before any retry.\n`,
    );
    process.exitCode = 70;
  });
}
