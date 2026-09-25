import { createHash } from "node:crypto";
import { posix } from "node:path";

import { canonical, validateWorkGoalFleet, validateWorkGoalFleetController, validateWorkGoalFleetHostProbe } from "../../scripts/lib/work-contract.mjs";

const SAFE_PATH_RE = /^\/[A-Za-z0-9._/-]+$/u;
const ACCOUNT_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const forbiddenPrivateRoots = Object.freeze(["/home", "/root", "/run/user"]);

export class GoalFleetServiceUnitError extends Error {}

function fail(message) { throw new GoalFleetServiceUnitError(message); }

function safeLinuxPath(value, label, directory = false) {
  if (
    typeof value !== "string" || !SAFE_PATH_RE.test(value) || value.includes("//") || posix.normalize(value) !== value
    || value === "/" || directory && value.endsWith("/")
  ) fail(`${label} is not a canonical safe Linux path`);
  return value;
}

function nonHomePath(value, label, directory = false) {
  const path = safeLinuxPath(value, label, directory);
  if (forbiddenPrivateRoots.some((root) => path === root || path.startsWith(`${root}/`))) fail(`${label} conflicts with ProtectHome`);
  return path;
}

function account(value, label) {
  if (!ACCOUNT_RE.test(value ?? "") || value === "root") fail(`${label} is invalid or privileged`);
  return value;
}

function unique(values) { return [...new Set(values)]; }
function sha(value) { return createHash("sha256").update(canonical(value)).digest("hex"); }

function checkedBindings(config, fleet, hostProbe, goalControllers) {
  const configErrors = validateWorkGoalFleetController(config);
  const fleetErrors = validateWorkGoalFleet(fleet);
  const probeErrors = validateWorkGoalFleetHostProbe(hostProbe);
  if (configErrors.length || fleetErrors.length || probeErrors.length || sha(hostProbe) !== config.hostProbeSha256 || hostProbe.fleetPath !== config.fleetPath || !Array.isArray(goalControllers) || goalControllers.length !== fleet.goals.length) fail("fleet service contracts are invalid");
  const expected = fleet.goals.map((goal) => goal.goalId);
  const actual = goalControllers.map((entry) => entry?.goalId);
  if (canonical(actual) !== canonical(expected) || canonical(config.goals.map((goal) => goal.goalId)) !== canonical(expected)) fail("fleet service goal registrations differ");
  for (let index = 0; index < goalControllers.length; index += 1) {
    const entry = goalControllers[index];
    if (
      !entry?.config || entry.config.enabled !== true || entry.config.controller?.maxTransitions !== 1
      || entry.config.stateRoot !== config.stateRoot || entry.configPath !== config.goals[index].controllerConfigPath
    ) fail("fleet service per-goal controller binding differs");
  }
  return goalControllers;
}

export function renderGoalFleetServiceUnits({
  configPath, config, fleet, hostProbe, goalControllers, installRoot, nodePath = "/usr/bin/node", flockPath = "/usr/bin/flock",
  serviceUser = "pixel-work", serviceGroup = "pixel-work", dockerGroup = "docker", intervalSeconds = 30,
} = {}) {
  const entries = checkedBindings(config, fleet, hostProbe, goalControllers);
  if (!Number.isSafeInteger(intervalSeconds) || intervalSeconds < 10 || intervalSeconds > 3600) fail("fleet service interval is invalid");
  const user = account(serviceUser, "fleet service user");
  const group = account(serviceGroup, "fleet service group");
  const socketGroup = account(dockerGroup, "fleet service Docker group");
  const installed = nonHomePath(installRoot, "fleet service install root", true);
  const cycleScript = posix.join(installed, "deploy/work-controller/goal-fleet-supervised-cycle-cli.mjs");
  const cleanupScript = posix.join(installed, "deploy/work-controller/goal-fleet-cleanup-cli.mjs");
  const privateConfig = nonHomePath(configPath, "fleet service configuration");
  const node = nonHomePath(nodePath, "fleet service Node executable");
  const flock = nonHomePath(flockPath, "fleet service lock executable");
  const stateRoot = nonHomePath(config.stateRoot, "fleet service state root", true);
  const hostProbePath = nonHomePath(config.hostProbePath, "fleet service host probe");
  const hostEvidenceDirectory = nonHomePath(config.hostEvidenceDirectory, "fleet service host evidence ledger", true);
  if (hostEvidenceDirectory !== posix.join(stateRoot, "host-evidence")) fail("fleet service host evidence must use its fixed writable state ledger");
  const workspaceRoots = entries.map((entry) => nonHomePath(entry.config.workspaceRoot, `fleet service workspace for ${entry.goalId}`, true));
  if (new Set(workspaceRoots).size !== workspaceRoots.length) fail("fleet service workspaces must be unique");
  for (const workspace of workspaceRoots) {
    if (workspace === stateRoot || workspace.startsWith(`${stateRoot}/`) || stateRoot.startsWith(`${workspace}/`)) fail("fleet service state and workspace roots must be disjoint");
  }
  const courierRoots = unique(entries.flatMap((entry) => entry.config.researchRuntime ? [nonHomePath(entry.config.researchRuntime.researchCourierQueueRoot, `fleet service research queue for ${entry.goalId}`, true)] : []));
  const readOnly = unique([
    installed, privateConfig, nonHomePath(config.fleetPath, "fleet service immutable fleet"), hostProbePath,
    nonHomePath(hostProbe.disposableDiskPath, "fleet service disposable filesystem", true), node, flock,
    ...entries.flatMap((entry) => [
      nonHomePath(entry.configPath, `fleet service controller for ${entry.goalId}`),
      nonHomePath(entry.config.goalPath, `fleet service goal for ${entry.goalId}`),
      nonHomePath(entry.config.jobsPath, `fleet service jobs for ${entry.goalId}`),
      nonHomePath(entry.config.policyPath, `fleet service policy for ${entry.goalId}`),
      nonHomePath(entry.config.objectStore, `fleet service object store for ${entry.goalId}`, true),
      nonHomePath(entry.config.executorPath, `fleet service executor for ${entry.goalId}`),
      nonHomePath(entry.config.runtime?.dockerPath, `fleet service Docker executable for ${entry.goalId}`),
    ]),
  ]);
  const unitStem = `pixel-work-${fleet.fleetId}`;
  const serviceName = `${unitStem}.service`;
  const timerName = `${unitStem}.timer`;
  const lockPath = `/run/${unitStem}/cycle.lock`;
  const legacyConditions = fleet.goals.flatMap((goal) => [
    `ConditionPathExists=!/etc/systemd/system/pixel-work-${goal.goalId}.service`,
    `ConditionPathExists=!/etc/systemd/system/pixel-work-${goal.goalId}.timer`,
  ]).join("\n");
  const service = `[Unit]
Description=Pixel supervised Deep Work fleet cycle for ${fleet.fleetId}
After=docker.service
ConditionPathExists=/run/docker.sock
${legacyConditions}
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=oneshot
User=${user}
Group=${group}
SupplementaryGroups=${socketGroup}
WorkingDirectory=/
ExecStart=${flock} --exclusive --wait 30 ${lockPath} ${node} ${cycleScript} --config ${privateConfig}
ExecStopPost=${flock} --exclusive --wait 300 ${lockPath} ${node} ${cleanupScript} --config ${privateConfig}
Environment=HOME=/nonexistent
Environment=PATH=/usr/bin:/bin
Environment=LANG=C.UTF-8
NoNewPrivileges=true
PrivateDevices=true
PrivateMounts=true
PrivateNetwork=true
PrivateTmp=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectHome=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectProc=invisible
ProcSubset=pid
ProtectSystem=strict
ReadOnlyPaths=${readOnly.join(" ")}
ReadWritePaths=${unique([stateRoot, ...workspaceRoots, ...courierRoots]).join(" ")}
BindReadOnlyPaths=/run/docker.sock
RemoveIPC=true
RestrictAddressFamilies=AF_UNIX
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
DevicePolicy=closed
UMask=0077
RuntimeDirectory=${unitStem}
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=no
MemoryMax=512M
CPUQuota=100%
TasksMax=512
TimeoutStartSec=${fleet.hostCapacity.maxTurnSeconds + 60}s
TimeoutStopSec=5min
KillMode=control-group
Restart=no
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${unitStem}
`;
  const timer = `[Unit]
Description=Schedule Pixel supervised Deep Work fleet cycle for ${fleet.fleetId}

[Timer]
OnBootSec=30s
OnUnitInactiveSec=${intervalSeconds}s
RandomizedDelaySec=5s
AccuracySec=1s
Persistent=true
Unit=${serviceName}

[Install]
WantedBy=timers.target
`;
  return Object.freeze({
    serviceName, timerName, service, timer,
    legacyUnitNames: Object.freeze(fleet.goals.flatMap((goal) => [`pixel-work-${goal.goalId}.service`, `pixel-work-${goal.goalId}.timer`])),
    boundary: "Static fleet systemd supervision only. One host lock serializes live capacity refresh, cycle, and stop-cleanup; installed legacy per-goal units block startup. Docker socket access remains a high-trust supervisor capability, and supported-host endurance remains a release gate.",
  });
}
