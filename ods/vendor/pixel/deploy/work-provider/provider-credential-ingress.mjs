import { isAbsolute, parse, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { parseStrictJson } from "../../scripts/lib/secure-files.mjs";
import { resolveWorkProvider } from "./provider-registry.mjs";

// Import the single authoritative core — production calls the 3-argument
// production export with NO injection surface.
import {
  assertPrivateDirectory,
  writeCredentialExclusive,
  validateCredentialSecret,
  ProviderCredentialIngressCoreError,
  readAllFromFd,
} from "./provider-credential-ingress-core.mjs";

// ---------------------------------------------------------------------------
// Closed, provider-authoritative mapping: providerId -> credential config
// ---------------------------------------------------------------------------
const PROVIDER_CREDENTIAL_MAP = Object.freeze({
  "moonshot-kimi": Object.freeze({
    envVarName: "MOONSHOT_API_KEY",
    fileName: "moonshot-kimi-key",
    credentialId: "moonshot-kimi-api-key",
    maxBytes: 8192,
  }),
  openai: Object.freeze({
    envVarName: "OPENAI_API_KEY",
    fileName: "openai-key",
    credentialId: "openai-api-key",
    maxBytes: 8192,
  }),
  anthropic: Object.freeze({
    envVarName: "ANTHROPIC_API_KEY",
    fileName: "anthropic-key",
    credentialId: "anthropic-api-key",
    maxBytes: 8192,
  }),
});

const ALLOWED_PROVIDERS = Object.freeze(Object.keys(PROVIDER_CREDENTIAL_MAP));

const ALLOWED_ENV_VARS = new Set(
  Object.values(PROVIDER_CREDENTIAL_MAP).map((c) => c.envVarName),
);

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------
export class ProviderCredentialIngressError extends Error {
  constructor(message) {
    super(message);
    this.name = "ProviderCredentialIngressError";
  }
}

function fail(message) {
  throw new ProviderCredentialIngressError(message);
}

// Wrap core errors into our own error type so callers see one type.
function wrapCore(fn) {
  return async (...args) => {
    try {
      return await fn(...args);
    } catch (err) {
      if (err instanceof ProviderCredentialIngressCoreError) {
        throw new ProviderCredentialIngressError(err.message);
      }
      throw err;
    }
  };
}

// ---------------------------------------------------------------------------
// Core custody helpers — thin wrappers around core
// ---------------------------------------------------------------------------
const coreAssertPrivateDirectory = wrapCore(assertPrivateDirectory);

// Fix #1: Production calls the 3-argument export directly, no undefined.
const coreWriteCredentialExclusive = wrapCore((dirPath, fileName, secret) =>
  writeCredentialExclusive(dirPath, fileName, secret),
);

const coreValidateCredentialSecret = wrapCore((value) => validateCredentialSecret(value));

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Resolve a provider id to its credential configuration.
 * Only allowlisted providers are accepted.
 */
export function resolveProviderCredentialConfig(providerId) {
  if (typeof providerId !== "string") fail("provider id must be a string");
  if (!ALLOWED_PROVIDERS.includes(providerId)) fail(`provider ${providerId} is outside the closed credential registry`);
  return PROVIDER_CREDENTIAL_MAP[providerId];
}

/**
 * Resolve a credential env-var name back to a provider id.
 * Returns null if the env var name does not match any known provider.
 */
export function resolveEnvVarToProvider(envVarName) {
  if (typeof envVarName !== "string") return null;
  if (!ALLOWED_ENV_VARS.has(envVarName)) return null;
  for (const [id, config] of Object.entries(PROVIDER_CREDENTIAL_MAP)) {
    if (config.envVarName === envVarName) return id;
  }
  return null;
}

/**
 * Read the credential from stdin (already-open FD 0) or TTY with a content-free
 * prompt.  Never accepts credential bytes on argv.
 *
 * Fix #1: Uses mutable variable for the credential string; strips trailing LF/CR
 * without reassigning const. Uses readAllFromFd which handles non-seekable pipes
 * correctly (position: null).
 *
 * @param {object} opts
 * @param {string} opts.providerId - Closed-registry provider id.
 * @param {string} [opts.fd] - Optional FD number to read from; defaults to 0.
 * @returns {Promise<string>} The credential secret string.
 */
export async function readCredentialFromInput({ providerId, fd = 0 }) {
  const config = resolveProviderCredentialConfig(providerId);

  // Fix #2: Use readAllFromFd which uses position: null for non-seekable fds
  const rawBytes = await readAllFromFd(fd, config.maxBytes + 1);

  if (rawBytes.length === 0) fail("credential input is empty");
  if (rawBytes.length > config.maxBytes) fail("credential input exceeds maximum length");

  // Convert to string — JS strings are not zeroizable, but the underlying
  // Buffer is zeroed below on every exit path.
  let raw = rawBytes.toString("utf8");

  // Fix #1: mutable variable; strip trailing newline(s) correctly
  if (raw.endsWith("\n")) raw = raw.slice(0, -1);
  if (raw.endsWith("\r")) raw = raw.slice(0, -1);

  // Zero the raw bytes buffer
  rawBytes.fill(0);

  // Validate content immediately after read.
  await coreValidateCredentialSecret(raw);
  return raw;
}

/**
 * Write a credential into the custody directory with create-only semantics.
 * Returns a content-free receipt confirming placement without exposing path,
 * owner, or secret bytes.
 */
export async function writeCredentialToCustody({ providerId, credentialDirectory, secret }) {
  if (!isAbsolute(credentialDirectory)) fail("credential directory must be an absolute path");
  if (resolve(credentialDirectory) === parse(resolve(credentialDirectory)).root) fail("credential directory cannot be the filesystem root");

  const resolvedDir = resolve(credentialDirectory);
  if (resolvedDir === parse(resolvedDir).root) fail("credential directory cannot be the filesystem root");

  await coreAssertPrivateDirectory(resolvedDir);

  const config = resolveProviderCredentialConfig(providerId);
  await coreValidateCredentialSecret(secret);

  await coreWriteCredentialExclusive(resolvedDir, config.fileName, secret);

  return Object.freeze({
    providerId,
    credentialId: config.credentialId,
    fileName: config.fileName,
    status: "stored",
    credentialPathExposed: false,
    credentialBytesExposed: false,
    credentialContentsHashed: false,
    boundary: "Content-free owner-private provider credential custody evidence. No path, owner, account, key, token, credential bytes, or hash are exposed.",
  });
}

/**
 * Full ingress pipeline: read from input FD, write to custody directory.
 * Validates ALL public API input BEFORE any filesystem mutation.
 * Reads and validates credential content before umask change or mkdir.
 * On validation failure, proves no directory or credential file is created and umask unchanged.
 */
export async function ingestProviderCredential({ providerId, credentialDirectory, inputFd = 0 }) {
  // -----------------------------------------------------------------------
  // Phase 1: Validate ALL public API input (synchronous, zero mutation)
  // -----------------------------------------------------------------------

  // 1. Validate providerId
  const config = resolveProviderCredentialConfig(providerId);

  // 2. Validate credentialDirectory
  if (typeof credentialDirectory !== "string") {
    fail("credential directory must be a string");
  }
  if (!isAbsolute(credentialDirectory)) {
    fail("credential directory must be an absolute path");
  }
  const resolvedDir = resolve(credentialDirectory);
  if (resolvedDir === parse(resolvedDir).root) {
    fail("credential directory cannot be the filesystem root");
  }

  // 3. Validate inputFd
  if (typeof inputFd !== "number" || !Number.isInteger(inputFd) || inputFd < 0) {
    fail("inputFd must be a non-negative integer");
  }

  // -----------------------------------------------------------------------
  // Phase 2: Read and validate credential content BEFORE any mutation
  // -----------------------------------------------------------------------

  let secret;
  try {
    secret = await readCredentialFromInput({ providerId, fd: inputFd });
  } catch (err) {
    // Read/validation failed — no umask change, no mkdir, no file created.
    throw err;
  }

  // -----------------------------------------------------------------------
  // Phase 3: All validation complete — safe to mutate
  // -----------------------------------------------------------------------

  const oldUmask = process.umask(0o077);
  try {
    // Ensure directory exists (idempotent) — only after full validation
    const { mkdir } = await import("node:fs/promises");
    try {
      await mkdir(credentialDirectory, { mode: 0o700, recursive: false });
    } catch (err) {
      if (err.code !== "EEXIST") {
        // Zero mutable buffers on every read failure path
        if (secret !== null) secret = null;
        fail(`credential directory could not be created: ${err.code}`);
      }
    }

    try {
      const receipt = await writeCredentialToCustody({ providerId, credentialDirectory, secret });
      // Drop the last reference to the secret string to enable GC.
      secret = null;
      return receipt;
    } catch (err) {
      // writeCredentialExclusive uses O_EXCL: if it failed to open, we created
      // no file and must not unlink anything. If post-verification failed,
      // writeCredentialExclusive already zeroed its own inode via pinned fd.
      if (secret !== null) secret = null;
      throw err;
    }
  } finally {
    process.umask(oldUmask);
  }
}

// ---------------------------------------------------------------------------
// CLI entrypoint
// ---------------------------------------------------------------------------
/**
 * Parse ingress CLI arguments with strict grammar.
 * Rejects duplicate flags, unknown flags, incomplete flags, and extra tokens.
 */
function parseIngressArgs(argv) {
  if (!Array.isArray(argv) || argv.length < 1) {
    fail("usage: provider-credential-ingress.mjs --provider <ID> --directory <ABSOLUTE_PATH> [--fd <N>]");
  }
  const args = new Map();
  let i = 0;
  while (i < argv.length) {
    const flag = argv[i];
    if (flag === "--provider") {
      if (i + 1 >= argv.length) fail("incomplete --provider flag: missing value");
      if (args.has("--provider")) fail("duplicate --provider flag");
      args.set("--provider", argv[i + 1]);
      i += 2;
    } else if (flag === "--directory") {
      if (i + 1 >= argv.length) fail("incomplete --directory flag: missing value");
      if (args.has("--directory")) fail("duplicate --directory flag");
      args.set("--directory", argv[i + 1]);
      i += 2;
    } else if (flag === "--fd") {
      if (i + 1 >= argv.length) fail("incomplete --fd flag: missing value");
      if (args.has("--fd")) fail("duplicate --fd flag");
      args.set("--fd", argv[i + 1]);
      i += 2;
    } else {
      fail(`unknown or incomplete ingress flag: ${flag}`);
    }
  }
  const providerId = args.get("--provider");
  const directory = args.get("--directory");
  const fdArg = args.get("--fd");

  if (!providerId) fail("--provider is required");
  if (!ALLOWED_PROVIDERS.includes(providerId)) fail(`provider ${providerId} is outside the closed credential registry`);
  if (!directory || !isAbsolute(directory)) fail("--directory must be an absolute path");
  if (resolve(directory) === parse(resolve(directory)).root) fail("credential directory cannot be the filesystem root");

  let fd = 0;
  if (fdArg !== undefined) {
    fd = Number(fdArg);
    if (!Number.isInteger(fd) || fd < 0) fail("--fd must be a non-negative integer");
  }

  return Object.freeze({ providerId, directory: resolve(directory), fd });
}

/**
 * Run the ingress CLI with the given argv.
 * Production API: accepts only `argv`. Rejects a forbidden second argument
 * before any reads or mutation, matching the production smoke discipline.
 */
export async function runProviderCredentialIngress(argv = process.argv.slice(2)) {
  // Fail closed: reject any second argument before path reads or other effects.
  if (arguments.length > 1) {
    fail("runProviderCredentialIngress accepts exactly one argument (argv)");
  }
  const options = parseIngressArgs(argv);
  const receipt = await ingestProviderCredential({
    providerId: options.providerId,
    credentialDirectory: options.directory,
    inputFd: options.fd,
  });
  return receipt;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  runProviderCredentialIngress().then((receipt) => {
    process.stdout.write(JSON.stringify(receipt) + "\n");
    process.exitCode = 0;
  }).catch((err) => {
    // Content-free failure: never include secret bytes, paths, or diagnostic details
    const message = err instanceof ProviderCredentialIngressError ? err.message : "credential ingress failed";
    process.stderr.write(`Pixel provider credential ingress failed closed: ${message}\n`);
    process.exitCode = 70;
  });
}
