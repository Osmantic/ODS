import { lstat } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  activateGoalFleetServiceBundle, execFleetLifecycleCommand, inspectGoalFleetServiceBundle,
  installGoalFleetServiceBundle, removeGoalFleetServiceBundle, verifyGoalFleetServiceLiveBinding,
  GoalFleetServiceLifecycleError,
} from "./goal-fleet-service-lifecycle.mjs";

const MUTATIONS = new Set(["install", "activate", "remove"]);

export class GoalFleetServiceLifecycleCliError extends Error {}

function fail(message) { throw new GoalFleetServiceLifecycleCliError(message); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || !["inspect", ...MUTATIONS].includes(argv[0])) fail("Usage: goal-fleet-service-lifecycle-cli.mjs inspect --bundle DIR | (install|activate|remove) --bundle DIR --confirm-manifest-sha256 HASH");
  const operation = argv[0], values = {};
  if ((argv.length - 1) % 2 !== 0) fail("fleet service lifecycle arguments are invalid");
  const allowed = operation === "inspect" ? new Set(["--bundle", "--expected-owner-uid"]) : new Set(["--bundle", "--confirm-manifest-sha256"]);
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("fleet service lifecycle arguments are invalid, unknown, or duplicated");
    values[key] = value;
  }
  if (!values["--bundle"] || operation === "inspect" && !(Object.keys(values).length === 1 || Object.keys(values).length === 2)) fail("fleet service lifecycle requires exactly one bundle path");
  if (MUTATIONS.has(operation) && (!values["--confirm-manifest-sha256"] || Object.keys(values).length !== 2)) fail("fleet service lifecycle mutation requires the exact manifest confirmation");
  return { operation, bundlePath: resolve(values["--bundle"]), confirmation: values["--confirm-manifest-sha256"], expectedOwnerUid: values["--expected-owner-uid"] };
}

function exactUid(value, label) {
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return value;
  if (typeof value === "string" && /^(?:0|[1-9][0-9]*)$/u.test(value)) {
    const parsed = Number(value); if (Number.isSafeInteger(parsed)) return parsed;
  }
  fail(`${label} is invalid`);
}

function inspectionReceipt(bundle) {
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-fleet-service-inspect", fleetId: bundle.manifest.fleetId,
    manifestSha256: bundle.manifestSha256, serviceName: bundle.manifest.serviceName, serviceSha256: bundle.manifest.serviceSha256,
    timerName: bundle.manifest.timerName, timerSha256: bundle.manifest.timerSha256, state: "bundle-verified-no-side-effects",
    authority: { grantsExecution: false, grantsInstall: false, grantsEnable: false, grantsLease: false, grantsScopeExpansion: false, grantsExternalEffects: false },
    boundary: "Content-free fleet-service bundle inspection receipt. Inspection performs no installation, activation, lease grant, work execution, or external effect.",
  });
}

export async function runGoalFleetServiceLifecycleCommand(argv, runtime = {}) {
  const options = parseArguments(argv);
  const platform = runtime.platform ?? process.platform;
  let euid = exactUid(runtime.euid ?? process.geteuid?.() ?? 0, "effective user ID");
  if (options.expectedOwnerUid !== undefined) {
    if (euid !== 0) fail("--expected-owner-uid requires root authority");
    euid = exactUid(options.expectedOwnerUid, "expected owner UID");
  }
  if (options.operation === "inspect") {
    const bundle = await inspectGoalFleetServiceBundle(options.bundlePath, { expectedOwnerUid: euid });
    await (runtime.liveBindingVerifier ?? verifyGoalFleetServiceLiveBinding)(bundle, euid);
    return inspectionReceipt(bundle);
  }
  if (platform !== "linux" || euid !== 0) fail("fleet service installation, activation, and removal require Linux root authority");
  const bundleInfo = await lstat(options.bundlePath).catch(() => null);
  if (!bundleInfo?.isDirectory() || bundleInfo.isSymbolicLink()) fail("fleet service bundle is not a real directory");
  const expectedBundleUid = exactUid(bundleInfo.uid, "fleet service bundle owner");
  const inspected = await inspectGoalFleetServiceBundle(options.bundlePath, { expectedOwnerUid: expectedBundleUid });
  const identityRunner = runtime.identityRunner ?? execFleetLifecycleCommand;
  const identity = await identityRunner(runtime.idPath ?? "/usr/bin/id", ["-u", inspected.serviceUser]);
  if (!identity || identity.code !== 0 || identity.executionError !== undefined && identity.executionError !== null || typeof identity.stdout !== "string" || typeof identity.stderr !== "string") fail("fleet service identity lookup failed closed");
  const serviceUid = exactUid(identity.stdout.trim(), "fleet service identity UID");
  if (serviceUid !== expectedBundleUid) fail("fleet service bundle must be owned by the configured unprivileged service identity");
  const lifecycle = {
    bundlePath: options.bundlePath, confirmation: options.confirmation, expectedBundleUid,
    systemdDirectory: runtime.systemdDirectory ?? "/etc/systemd/system", expectedSystemUid: runtime.expectedSystemUid ?? 0,
    commandRunner: runtime.commandRunner, systemdAnalyzePath: runtime.systemdAnalyzePath ?? "/usr/bin/systemd-analyze",
    systemctlPath: runtime.systemctlPath ?? "/usr/bin/systemctl", liveBindingVerifier: runtime.liveBindingVerifier,
  };
  if (lifecycle.commandRunner === undefined) delete lifecycle.commandRunner;
  if (lifecycle.liveBindingVerifier === undefined) delete lifecycle.liveBindingVerifier;
  if (options.operation === "install") return installGoalFleetServiceBundle(lifecycle);
  delete lifecycle.systemdAnalyzePath;
  if (options.operation === "activate") return activateGoalFleetServiceBundle(lifecycle);
  return removeGoalFleetServiceBundle(lifecycle);
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await runGoalFleetServiceLifecycleCommand(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    const expected = error instanceof GoalFleetServiceLifecycleCliError || error instanceof GoalFleetServiceLifecycleError;
    process.stderr.write(`pixel-work-fleet-service-lifecycle: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
