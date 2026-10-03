import { createHash } from "node:crypto";
import { dirname, isAbsolute, resolve } from "node:path";
import { pathToFileURL } from "node:url";

import { validateWorkGoalControllerEnvironment, validateWorkModelBackendConfig } from "../../scripts/lib/work-contract.mjs";
import {
  productionMaintenancePrimitives,
  validateModelQualificationMaintenanceConfiguration,
} from "./model-qualification-maintenance.mjs";
import { validateDockerQualificationConfiguration } from "./model-qualification-docker.mjs";

export class WorkMaintenanceRecoveryUnitError extends Error {}

function fail(message) { throw new WorkMaintenanceRecoveryUnitError(message); }
function canonicalAbs(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}
// Every path embedded into the systemd unit (ExecStart and ReadWritePaths) must
// be systemd-safe: no specifier (%), no quoting, no escaping, no whitespace, and
// no control characters. Anything else is rejected before review rather than
// emitted unsafely.
const SYSTEMD_SAFE_PATH_RE = /^[A-Za-z0-9/._@+,-]+$/u;
export function systemdSafeAbs(value, label) {
  const path = canonicalAbs(value, label);
  if (!path.startsWith("/") || !SYSTEMD_SAFE_PATH_RE.test(path)) fail(`${label} is not systemd-safe for a unit path`);
  return path;
}

async function readValidatedPrivateJson(path, label, expectedOwnerUid) {
  const record = await productionMaintenancePrimitives.readPrivateJsonRecord(path, label, expectedOwnerUid);
  return { value: record.value, bytes: record.bytes };
}

export async function deriveGuardianWritablePaths(config, expectedOwnerUid) {
  if (!config || typeof config !== "object" || Array.isArray(config)) fail("maintenance configuration is invalid");
  const custodyLock = systemdSafeAbs(config.custody?.lockPath, "custody lock path");
  const writable = new Set([dirname(custodyLock)]);
  const dockerQualPath = systemdSafeAbs(config.qualificationDockerConfigPath, "qualification Docker configuration path");
  const dockerQualRecord = await readValidatedPrivateJson(dockerQualPath, "private Docker qualification configuration", expectedOwnerUid);
  const dockerQual = validateDockerQualificationConfiguration(dockerQualRecord.value);
  const backendConfigPath = systemdSafeAbs(dockerQual.backendConfigPath, "model backend configuration path");
  const backendRecord = await readValidatedPrivateJson(backendConfigPath, "private model backend configuration", expectedOwnerUid);
  const backendErrors = validateWorkModelBackendConfig(backendRecord.value);
  if (backendErrors.length) fail(`private model backend configuration is invalid: ${backendErrors[0]}`);
  const environmentPath = systemdSafeAbs(backendRecord.value.environmentPath, "goal controller environment path");
  const environmentRecord = await readValidatedPrivateJson(environmentPath, "private goal controller environment", expectedOwnerUid);
  const environmentErrors = validateWorkGoalControllerEnvironment(environmentRecord.value);
  if (environmentErrors.length) fail(`private goal controller environment is invalid: ${environmentErrors[0]}`);
  if (typeof environmentRecord.value.stateRoot !== "string") fail("backend state root is unavailable");
  const stateRoot = systemdSafeAbs(environmentRecord.value.stateRoot, "backend state root");
  writable.add(stateRoot);
  return [...writable].sort();
}

// Frozen descriptor seam for the shared guardian-unit renderer. Qualification
// remains the default wrapper below; the campaign unit module imports the same
// engine with its own descriptor so the two never diverge into separate
// renderers. Every campaign identity, label, validator, and least-privilege
// writable-path derivation is expressed by the descriptor, never by branching.
const QUALIFICATION_UNIT_DESCRIPTOR = Object.freeze({
  configLabel: "private qualification maintenance configuration",
  configPathLabel: "maintenance configuration path",
  nodeLabel: "guardian node executable",
  guardianLabel: "guardian module path",
  custodyLabel: "custody lock path",
  validateConfig: validateModelQualificationMaintenanceConfiguration,
  deriveWritablePaths: deriveGuardianWritablePaths,
  unitDescription: (configPath) => `Pixel maintenance recovery guardian for ${configPath}`,
  supervisionCommand: "watch",
});

export async function renderGuardianUnitWith(descriptor, { configPath, nodePath, guardianPath, expectedOwnerUid = process.geteuid?.() ?? 0 }) {
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail("guardian unit requires a non-root service identity");
  if (!descriptor || typeof descriptor !== "object") fail("guardian unit descriptor is invalid");
  const config = systemdSafeAbs(configPath, descriptor.configPathLabel);
  const node = systemdSafeAbs(nodePath, descriptor.nodeLabel);
  const guardian = systemdSafeAbs(guardianPath, descriptor.guardianLabel);
  const configRecord = await readValidatedPrivateJson(config, descriptor.configLabel, expectedOwnerUid);
  const configuration = descriptor.validateConfig(configRecord.value);
  const writablePaths = await descriptor.deriveWritablePaths(configuration, expectedOwnerUid);
  const unit = [
    "[Unit]",
    `Description=${descriptor.unitDescription(config)}`,
    "After=network-online.target",
    "Wants=network-online.target",
    "StartLimitIntervalSec=300",
    "StartLimitBurst=20",
    "",
    "[Service]",
    "Type=exec",
    `ExecStart=${node} ${guardian} ${descriptor.supervisionCommand} --config ${config}`,
    "Restart=always",
    "RestartSec=30",
    "Environment=HOME=/nonexistent",
    "Environment=PATH=/usr/bin:/bin",
    "Environment=LANG=C.UTF-8",
    "Environment=DOCKER_CONFIG=/nonexistent",
    "NoNewPrivileges=true",
    "ProtectSystem=strict",
    "ProtectHome=read-only",
    `ReadWritePaths=${writablePaths.join(" ")}`,
    "ProtectKernelTunables=true",
    "ProtectControlGroups=true",
    "PrivateTmp=true",
    "LockPersonality=true",
    "RestrictSUIDSGID=true",
    "",
    "[Install]",
    "WantedBy=default.target",
    "",
  ].join("\n");
  return {
    unit,
    renderSha256: createHash("sha256").update(unit, "utf8").digest("hex"),
    writablePaths,
    configPath: config,
  };
}

export async function renderGuardianUnit(options = {}) {
  return renderGuardianUnitWith(QUALIFICATION_UNIT_DESCRIPTOR, options);
}

export async function main(argv = process.argv.slice(2)) {
  if (!Array.isArray(argv) || argv.length !== 7 || argv[0] !== "render" || argv[1] !== "--config" || argv[3] !== "--node" || argv[5] !== "--guardian") {
    fail("Usage: maintenance-recovery-guardian-unit.mjs render --config PRIVATE_JSON --node NODE --guardian GUARDIAN");
  }
  const rendered = await renderGuardianUnit({ configPath: argv[2], nodePath: argv[4], guardianPath: argv[6] });
  process.stdout.write(rendered.unit);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`pixel-maintenance-recovery-guardian-unit: ${error instanceof WorkMaintenanceRecoveryUnitError ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
