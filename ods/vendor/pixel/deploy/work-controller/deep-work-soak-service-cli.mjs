import { lstat } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  activateDeepWorkSoakServiceBundle, execSoakLifecycleCommand, inspectDeepWorkSoakServiceBundle,
  installDeepWorkSoakServiceBundle, removeDeepWorkSoakServiceBundle, renderDeepWorkSoakServiceBundle,
  verifyDeepWorkSoakServiceLiveBinding,
  DeepWorkSoakServiceLifecycleError,
} from "./deep-work-soak-service-lifecycle.mjs";

const MUTATIONS = new Set(["install", "activate", "remove"]);

export class DeepWorkSoakServiceCliError extends Error {}

function fail(message) { throw new DeepWorkSoakServiceCliError(message); }
function parseArguments(argv) {
  const operation = argv?.[0];
  if (!["render", "inspect", ...MUTATIONS].includes(operation)) fail("Usage: deep-work-soak-service-cli.mjs render --config FILE --output NEW_DIR --install-root DIR | inspect --bundle DIR | (install|activate|remove) --bundle DIR --confirm-manifest-sha256 HASH");
  const values = {};
  if ((argv.length - 1) % 2 !== 0) fail("multi-day soak service arguments are invalid");
  const allowed = operation === "render" ? new Set(["--config", "--output", "--install-root"]) : operation === "inspect" ? new Set(["--bundle", "--expected-owner-uid"]) : new Set(["--bundle", "--confirm-manifest-sha256"]);
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("multi-day soak service arguments are invalid, unknown, or duplicated");
    values[key] = value;
  }
  if (![...allowed].every((key) => key === "--expected-owner-uid" || values[key]) || Object.keys(values).length < allowed.size - 1 || Object.keys(values).length > allowed.size) fail("multi-day soak service arguments are incomplete");
  return {
    operation, bundlePath: values["--bundle"] && resolve(values["--bundle"]), confirmation: values["--confirm-manifest-sha256"],
    configPath: values["--config"] && resolve(values["--config"]), outputPath: values["--output"] && resolve(values["--output"]),
    installRoot: values["--install-root"] && resolve(values["--install-root"]), expectedOwnerUid: values["--expected-owner-uid"],
  };
}
function exactUid(value, label) {
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return value;
  if (typeof value === "string" && /^(?:0|[1-9][0-9]*)$/u.test(value)) { const parsed = Number(value); if (Number.isSafeInteger(parsed)) return parsed; }
  fail(`${label} is invalid`);
}
function inspectionReceipt(bundle) {
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-deep-work-multi-day-soak-service-inspect", campaignId: bundle.manifest.campaignId,
    manifestSha256: bundle.manifestSha256, serviceName: bundle.manifest.serviceName, serviceSha256: bundle.manifest.serviceSha256,
    timerName: bundle.manifest.timerName, timerSha256: bundle.manifest.timerSha256, state: "bundle-verified-no-side-effects",
    authority: { grantsInstall: false, grantsScheduling: false, grantsWork: false, grantsLease: false, grantsExternalEffects: false, grantsRelease: false },
    boundary: "Content-free bundle inspection receipt. Inspection performs no installation, scheduling, work, provider call, deployment, publication, or release action.",
  });
}

export async function runDeepWorkSoakServiceCommand(argv, runtime = {}) {
  const options = parseArguments(argv), platform = runtime.platform ?? process.platform;
  let euid = exactUid(runtime.euid ?? process.geteuid?.() ?? 0, "effective user ID");
  if (options.expectedOwnerUid !== undefined) {
    if (euid !== 0) fail("--expected-owner-uid requires root authority");
    euid = exactUid(options.expectedOwnerUid, "expected owner UID");
  }
  if (options.operation === "render") return renderDeepWorkSoakServiceBundle({
    configPath: options.configPath, outputPath: options.outputPath, installRoot: options.installRoot, expectedOwnerUid: euid,
    serviceUser: runtime.serviceUser ?? "pixel-work", serviceGroup: runtime.serviceGroup ?? "pixel-work",
    nodePath: runtime.nodePath ?? "/usr/bin/node", flockPath: runtime.flockPath ?? "/usr/bin/flock", envPath: runtime.envPath ?? "/usr/bin/env",
  });
  if (options.operation === "inspect") {
    const bundle = await inspectDeepWorkSoakServiceBundle(options.bundlePath, { expectedOwnerUid: euid });
    await (runtime.liveBindingVerifier ?? verifyDeepWorkSoakServiceLiveBinding)(bundle, { expectedOwnerUid: euid });
    return inspectionReceipt(bundle);
  }
  if (platform !== "linux" || euid !== 0) fail("multi-day soak service installation, activation, and removal require Linux root authority");
  const bundleInfo = await lstat(options.bundlePath).catch(() => null);
  if (!bundleInfo?.isDirectory() || bundleInfo.isSymbolicLink()) fail("multi-day soak service bundle is not a real directory");
  const expectedBundleUid = exactUid(bundleInfo.uid, "multi-day soak service bundle owner");
  const inspected = await inspectDeepWorkSoakServiceBundle(options.bundlePath, { expectedOwnerUid: expectedBundleUid });
  const identityRunner = runtime.identityRunner ?? execSoakLifecycleCommand;
  const identity = await identityRunner(runtime.idPath ?? "/usr/bin/id", ["-u", inspected.serviceUser]);
  if (!identity || identity.code !== 0 || identity.executionError !== undefined && identity.executionError !== null || typeof identity.stdout !== "string" || typeof identity.stderr !== "string") fail("multi-day soak service identity lookup failed closed");
  if (exactUid(identity.stdout.trim(), "multi-day soak service identity UID") !== expectedBundleUid) fail("multi-day soak service bundle must be owned by its configured unprivileged identity");
  const lifecycle = {
    bundlePath: options.bundlePath, confirmation: options.confirmation, expectedBundleUid,
    systemdDirectory: runtime.systemdDirectory ?? "/etc/systemd/system", expectedSystemUid: runtime.expectedSystemUid ?? 0,
    commandRunner: runtime.commandRunner, systemdAnalyzePath: runtime.systemdAnalyzePath ?? "/usr/bin/systemd-analyze",
    systemctlPath: runtime.systemctlPath ?? "/usr/bin/systemctl", liveBindingVerifier: runtime.liveBindingVerifier,
  };
  if (lifecycle.commandRunner === undefined) delete lifecycle.commandRunner;
  if (lifecycle.liveBindingVerifier === undefined) delete lifecycle.liveBindingVerifier;
  if (options.operation === "install") return installDeepWorkSoakServiceBundle(lifecycle);
  delete lifecycle.systemdAnalyzePath;
  if (options.operation === "activate") return activateDeepWorkSoakServiceBundle(lifecycle);
  return removeDeepWorkSoakServiceBundle(lifecycle);
}

export async function main(argv = process.argv.slice(2)) { return runDeepWorkSoakServiceCommand(argv); }

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().then((value) => process.stdout.write(`${JSON.stringify(value)}\n`)).catch((error) => {
    const expected = error instanceof DeepWorkSoakServiceCliError || error instanceof DeepWorkSoakServiceLifecycleError;
    process.stderr.write(`pixel-deep-work-soak-service: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
