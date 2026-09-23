#!/usr/bin/env node
import { constants } from "node:fs";
import { lstat, open, readFile, realpath } from "node:fs/promises";
import { basename, dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { assertJsonSchema } from "./lib/json-schema.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const schema = JSON.parse(await readFile(resolve(root, "schemas/client-overlay-v1.schema.json"), "utf8"));
const maximumBytes = 256 * 1024;

function fail(message) { throw new Error(message); }

function argumentsByName() {
  const [command, ...rest] = process.argv.slice(2);
  if (!command || !["generate", "validate"].includes(command)) fail("Usage: ./pixel client-kit generate|validate [options]");
  const values = { command };
  for (let index = 0; index < rest.length; index += 2) {
    const key = rest[index], value = rest[index + 1];
    if (!value || !["--output", "--client-id", "--profile", "--overlay"].includes(key)) fail(`Invalid client-kit option: ${key ?? "missing"}`);
    if (Object.hasOwn(values, key)) fail(`Duplicate client-kit option: ${key}`);
    values[key] = value;
  }
  return values;
}

async function requirePrivateParent(path) {
  const parent = dirname(path);
  const info = await lstat(parent);
  if (!info.isDirectory() || info.isSymbolicLink()) fail("Client overlay parent must be a real directory");
  if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o777) !== 0o700)) {
    fail("Client overlay parent must be owner-bound mode 0700");
  }
  return realpath(parent);
}

async function readPrivateOverlay(path) {
  if (!isAbsolute(path)) fail("Client overlay path must be absolute");
  let handle;
  try { handle = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0)); }
  catch (error) {
    if (error?.code === "ELOOP") fail("Client overlay must be one descriptor-bound regular single-link file");
    throw error;
  }
  try {
    const info = await handle.stat();
    const current = await lstat(path);
    if (
      !info.isFile() || info.nlink !== 1 || info.size < 2 || info.size > maximumBytes
      || current.isSymbolicLink() || current.dev !== info.dev || current.ino !== info.ino
    ) {
      fail("Client overlay must be one descriptor-bound regular single-link file");
    }
    if (process.platform !== "win32" && (info.uid !== process.geteuid() || (info.mode & 0o777) !== 0o600)) {
      fail("Client overlay must be owner-bound mode 0600");
    }
    const payload = await handle.readFile("utf8");
    if (Buffer.byteLength(payload) > maximumBytes) fail("Client overlay exceeds its byte limit");
    return JSON.parse(payload);
  } finally {
    await handle.close();
  }
}

function draft(clientId, profile) {
  return {
    $schema: "./schemas/client-overlay-v1.schema.json",
    schemaVersion: 1,
    clientId,
    core: { pixel: "4.3.27", modificationAllowed: false, privateStateOutsideCore: true },
    capabilityProfile: profile,
    frontier: {
      defaultRoute: "local-first", advertisedAuthModes: ["chatgpt"],
      apiKeyModeAdvertised: false, clientDataEgressAllowed: false,
    },
    operatorAccess: {
      mode: "local-only", pixelListener: "exact-ipv4-loopback", remoteAccessEnabled: false,
      remoteAccessPolicy: {
        requiredIfLaterEnabled: true, enforcement: "external-identity-provider", mfaRequired: true,
        preferredFactor: "totp-authenticator-app", fallbackFactor: "email-one-time-code",
        credentialsVisibleToPixel: false,
      },
    },
    privacy: {
      clientDataInCore: false, credentialsInOverlay: false,
      remoteReceivesSanitizedOnly: true, localFinalComposition: true,
    },
    extensions: { localCapabilityPackIds: [], operationsActionPackIds: [], frontierTaskPackIds: [] },
    licensing: { required: true, recorded: false, evidenceSha256: null },
    usability: { required: true, recorded: false, evidenceSha256: null },
  };
}

async function generate(values) {
  const output = values["--output"], clientId = values["--client-id"], profile = values["--profile"] ?? "minimal";
  if (!output || !isAbsolute(output) || !clientId || values["--overlay"]) fail("Generate requires absolute --output and --client-id");
  if (!/^[a-z][a-z0-9-]{1,62}[a-z0-9]$/.test(clientId)) fail("Client ID must be a non-identifying lowercase slug");
  const value = draft(clientId, profile);
  assertJsonSchema(value, schema, "generated client overlay");
  const parent = await requirePrivateParent(output);
  if (resolve(parent, basename(output)) !== resolve(output)) fail("Client overlay output path is unsafe");
  let handle;
  try { handle = await open(output, "wx", 0o600); }
  catch (error) {
    if (error?.code === "EEXIST") fail("Client overlay output already exists");
    throw error;
  }
  try { await handle.writeFile(`${JSON.stringify(value, null, 2)}\n`, "utf8"); await handle.sync(); }
  finally { await handle.close(); }
  return { status: "generated-draft", licensingEvidenceRecorded: false, usabilityEvidenceRecorded: false, ready: false };
}

async function validate(values) {
  const overlay = values["--overlay"];
  if (!overlay || !isAbsolute(overlay) || values["--output"] || values["--client-id"] || values["--profile"]) fail("Validate requires only absolute --overlay");
  const value = await readPrivateOverlay(overlay);
  assertJsonSchema(value, schema, "client overlay");
  const blocked = [
    ...(!value.licensing.recorded ? ["licensing"] : []),
    ...(!value.usability.recorded ? ["usability"] : []),
  ];
  return { status: blocked.length ? "valid-draft" : "ready", ready: blocked.length === 0, blockedGates: blocked };
}

try {
  const values = argumentsByName();
  const result = values.command === "generate" ? await generate(values) : await validate(values);
  process.stdout.write(`${JSON.stringify(result)}\n`);
} catch (error) {
  process.stderr.write(`Pixel client kit rejected: ${error instanceof Error ? error.message : "unknown error"}\n`);
  process.exitCode = 1;
}
