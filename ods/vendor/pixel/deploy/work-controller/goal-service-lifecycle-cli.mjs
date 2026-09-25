import { lstat } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

import {
  activateGoalServiceBundle, execLifecycleCommand, inspectGoalServiceBundle, installGoalServiceBundle, removeGoalServiceBundle,
  verifyGoalServiceLiveBinding,
  GoalServiceLifecycleError,
} from "./goal-service-lifecycle.mjs";

const MUTATIONS = new Set(["install", "activate", "remove"]);

export class GoalServiceLifecycleCliError extends Error {}

function fail(message) { throw new GoalServiceLifecycleCliError(message); }

function parseArguments(argv) {
  if (!Array.isArray(argv) || !["inspect", ...MUTATIONS].includes(argv[0])) {
    fail("Usage: goal-service-cli.mjs inspect --bundle DIR | (install|activate|remove) --bundle DIR --confirm-manifest-sha256 HASH");
  }
  const operation = argv[0], values = {};
  if ((argv.length - 1) % 2 !== 0) fail("goal service lifecycle arguments are invalid");
  const allowed = operation === "inspect" ? new Set(["--bundle", "--expected-owner-uid"]) : new Set(["--bundle", "--confirm-manifest-sha256"]);
  for (let index = 1; index < argv.length; index += 2) {
    const key = argv[index], value = argv[index + 1];
    if (!allowed.has(key) || typeof value !== "string" || !value || Object.hasOwn(values, key)) fail("goal service lifecycle arguments are invalid, unknown, or duplicated");
    values[key] = value;
  }
  if (!values["--bundle"] || operation === "inspect" && !(Object.keys(values).length === 1 || Object.keys(values).length === 2)) fail("goal service lifecycle requires exactly one bundle path");
  if (MUTATIONS.has(operation) && (!values["--confirm-manifest-sha256"] || Object.keys(values).length !== 2)) fail("goal service lifecycle mutation requires the exact manifest confirmation");
  return { operation, bundlePath: resolve(values["--bundle"]), confirmation: values["--confirm-manifest-sha256"], expectedOwnerUid: values["--expected-owner-uid"] };
}

function exactUid(value, label) {
  if (typeof value === "number" && Number.isSafeInteger(value) && value >= 0) return value;
  if (typeof value === "string" && /^(?:0|[1-9][0-9]*)$/u.test(value)) {
    const parsed = Number(value);
    if (Number.isSafeInteger(parsed)) return parsed;
  }
  fail(`${label} is invalid`);
}

function readiness(value) {
  if (
    !value || typeof value !== "object" || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(["jobCount", "packCount", "state", "toolCount"])
    || !["disabled", "ready"].includes(value.state)
    || ![value.jobCount, value.packCount, value.toolCount].every((count) => Number.isSafeInteger(count) && count >= 0)
    || value.state === "disabled" && (value.jobCount !== 0 || value.packCount !== 0 || value.toolCount !== 0)
    || value.state === "ready" && (value.jobCount < 1 || value.packCount < 1 || value.toolCount < 1)
  ) fail("goal service capability readiness receipt is invalid");
  return { state: value.state, jobCount: value.jobCount, packCount: value.packCount, toolCount: value.toolCount };
}

function inspectionReceipt(bundle, live) {
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-service-inspect", goalId: bundle.manifest.goalId,
    manifestSha256: bundle.manifestSha256, serviceName: bundle.manifest.serviceName,
    serviceSha256: bundle.manifest.serviceSha256, timerName: bundle.manifest.timerName,
    timerSha256: bundle.manifest.timerSha256, pathName: bundle.manifest.pathName,
    pathSha256: bundle.manifest.pathSha256, state: "bundle-verified-no-side-effects",
    executionModel: "durable-event-driven", watchdogRole: "liveness-only", capability: readiness(live?.capability),
    authority: {
      grantsExecution: false, grantsInstall: false, grantsEnable: false, grantsLease: false,
      grantsScopeExpansion: false, grantsExternalEffects: false,
    },
    boundary: "Content-free bundle inspection receipt. Inspection performs no installation, activation, lease grant, work execution, or external effect.",
  });
}

export async function runGoalServiceLifecycleCommand(argv, runtime = {}) {
  const options = parseArguments(argv);
  const platform = runtime.platform ?? process.platform;
  let euid = exactUid(runtime.euid ?? process.geteuid?.() ?? 0, "effective user ID");
  if (options.expectedOwnerUid !== undefined) {
    // Narrow root-only inspect override: the root operator may inspect a bundle owned by a
    // configured unprivileged service identity. Only honored when the effective user is root.
    if (euid !== 0) fail("--expected-owner-uid requires root authority");
    euid = exactUid(options.expectedOwnerUid, "expected owner UID");
  }
  if (options.operation === "inspect") {
    const bundle = await inspectGoalServiceBundle(options.bundlePath, { expectedOwnerUid: euid });
    const live = await (runtime.liveBindingVerifier ?? verifyGoalServiceLiveBinding)(bundle, euid);
    return inspectionReceipt(bundle, live);
  }
  if (platform !== "linux" || euid !== 0) fail("goal service installation, activation, and removal require Linux root authority");
  const bundleInfo = await lstat(options.bundlePath).catch(() => null);
  if (!bundleInfo?.isDirectory() || bundleInfo.isSymbolicLink()) fail("goal service bundle is not a real directory");
  const expectedBundleUid = exactUid(bundleInfo.uid, "goal service bundle owner");
  const inspected = await inspectGoalServiceBundle(options.bundlePath, { expectedOwnerUid: expectedBundleUid });
  const identityRunner = runtime.identityRunner ?? execLifecycleCommand;
  const identity = await identityRunner(runtime.idPath ?? "/usr/bin/id", ["-u", inspected.serviceUser]);
  if (
    !identity || identity.code !== 0 || identity.executionError !== undefined && identity.executionError !== null
    || typeof identity.stdout !== "string" || typeof identity.stderr !== "string"
  ) fail("goal service identity lookup failed closed");
  const serviceUid = exactUid(identity.stdout.trim(), "goal service identity UID");
  if (serviceUid !== expectedBundleUid) fail("goal service bundle must be owned by the configured unprivileged service identity");
  const lifecycle = {
    bundlePath: options.bundlePath, confirmation: options.confirmation, expectedBundleUid,
    systemdDirectory: runtime.systemdDirectory ?? "/etc/systemd/system",
    expectedSystemUid: runtime.expectedSystemUid ?? 0,
    commandRunner: runtime.commandRunner,
    systemdAnalyzePath: runtime.systemdAnalyzePath ?? "/usr/bin/systemd-analyze",
    systemctlPath: runtime.systemctlPath ?? "/usr/bin/systemctl",
    liveBindingVerifier: runtime.liveBindingVerifier,
  };
  if (lifecycle.commandRunner === undefined) delete lifecycle.commandRunner;
  if (lifecycle.liveBindingVerifier === undefined) delete lifecycle.liveBindingVerifier;
  if (options.operation === "install") return installGoalServiceBundle(lifecycle);
  delete lifecycle.systemdAnalyzePath;
  if (options.operation === "activate") return activateGoalServiceBundle(lifecycle);
  return removeGoalServiceBundle(lifecycle);
}

export async function main(argv = process.argv.slice(2)) {
  const receipt = await runGoalServiceLifecycleCommand(argv);
  process.stdout.write(`${JSON.stringify(receipt)}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    const expected = error instanceof GoalServiceLifecycleCliError || error instanceof GoalServiceLifecycleError;
    process.stderr.write(`pixel-work-service-lifecycle: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
