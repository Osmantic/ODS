import { posix } from "node:path";

import { validateWorkGoal } from "../../scripts/lib/work-contract.mjs";

const SAFE_PATH_RE = /^\/[A-Za-z0-9._/-]+$/u;
const ACCOUNT_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const forbiddenPrivateRoots = Object.freeze(["/home", "/root", "/run/user"]);

export class GoalServiceUnitError extends Error {}

function fail(message) { throw new GoalServiceUnitError(message); }

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

export function renderGoalServiceUnits({
  configPath, config, goal, installRoot, nodePath = "/usr/bin/node", flockPath = "/usr/bin/flock",
  serviceUser = "pixel-work", serviceGroup = "pixel-work", dockerGroup = "docker",
  intervalSeconds = 30,
} = {}) {
  if (validateWorkGoal(goal).length || !config || config.enabled !== true || config.controller?.maxTransitions !== 1) fail("goal service contracts are invalid");
  if (!Number.isSafeInteger(intervalSeconds) || intervalSeconds < 10 || intervalSeconds > 3600) fail("goal service interval is invalid");
  const user = account(serviceUser, "goal service user");
  const group = account(serviceGroup, "goal service group");
  const socketGroup = account(dockerGroup, "goal service Docker group");
  const installed = nonHomePath(installRoot, "goal service install root", true);
  const continuousScript = posix.join(installed, "deploy/work-controller/goal-continuous-cli.mjs");
  const cleanupScript = posix.join(installed, "deploy/work-controller/goal-cleanup-cli.mjs");
  const privateConfig = nonHomePath(configPath, "goal service configuration");
  const node = nonHomePath(nodePath, "goal service Node executable");
  const flock = nonHomePath(flockPath, "goal service lock executable");
  const stateRoot = nonHomePath(config.stateRoot, "goal service state root", true);
  const workspaceRoot = nonHomePath(config.workspaceRoot, "goal service workspace root", true);
  const researchQueueRoot = config.researchRuntime ? nonHomePath(config.researchRuntime.researchCourierQueueRoot, "goal service research queue root", true) : null;
  const knowledgeRoot = config.knowledgeRuntime ? nonHomePath(config.knowledgeRuntime.vaultRoot, "goal service knowledge vault root", true) : null;
  const knowledgeCredentialSource = config.knowledgeRuntime ? nonHomePath(config.knowledgeRuntime.credentialSourcePath, "goal service knowledge credential source") : null;
  if (config.knowledgeRuntime && config.knowledgeRuntime.credentialName !== "pixel-knowledge-vault-key") fail("goal service knowledge credential name is invalid");
  const capabilityReadOnly = config.capabilityRuntime ? [
    nonHomePath(config.capabilityRuntime.controllerPolicyPath, "goal service capability policy"),
    nonHomePath(config.capabilityRuntime.allowedSignersPath, "goal service capability allowed signers"),
    nonHomePath(config.capabilityRuntime.sshKeygenPath, "goal service capability signature verifier"),
    nonHomePath(config.capabilityRuntime.dockerConfigPath, "goal service capability Docker configuration", true),
  ] : [];
  const readOnly = unique([
    installed, privateConfig,
    nonHomePath(config.goalPath, "goal service goal"),
    nonHomePath(config.jobsPath, "goal service jobs"),
    nonHomePath(config.policyPath, "goal service policy"),
    nonHomePath(config.objectStore, "goal service object store", true),
    nonHomePath(config.executorPath, "goal service executor"),
    nonHomePath(config.runtime?.dockerPath, "goal service Docker executable"), ...capabilityReadOnly, node, flock,
  ]);
  const writable = [stateRoot, workspaceRoot, ...(researchQueueRoot ? [researchQueueRoot] : []), ...(knowledgeRoot ? [knowledgeRoot] : [])];
  for (let left = 0; left < writable.length; left += 1) for (let right = left + 1; right < writable.length; right += 1) {
    if (writable[left] === writable[right] || writable[left].startsWith(`${writable[right]}/`) || writable[right].startsWith(`${writable[left]}/`)) fail("goal service writable roots must be disjoint");
  }
  if (knowledgeCredentialSource && writable.some((root) => knowledgeCredentialSource === root || knowledgeCredentialSource.startsWith(`${root}/`))) fail("goal service knowledge credential source is inside a writable root");
  const unitStem = `pixel-work-${goal.goalId}`;
  const serviceName = `${unitStem}.service`;
  const timerName = `${unitStem}.timer`;
  const pathName = `${unitStem}.path`;
  const runtimeDirectory = unitStem;
  const lockPath = `/run/${runtimeDirectory}/cycle.lock`;
  const service = `[Unit]
Description=Pixel supervised continuous Deep Work for ${goal.goalId}
After=docker.service
ConditionPathExists=/run/docker.sock
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
Type=oneshot
User=${user}
Group=${group}
SupplementaryGroups=${socketGroup}
WorkingDirectory=/
ExecStart=${flock} --exclusive --nonblock ${lockPath} ${node} ${continuousScript} --config ${privateConfig}
ExecStopPost=${flock} --exclusive --wait 30 ${lockPath} ${node} ${cleanupScript} --config ${privateConfig}
Environment=HOME=/nonexistent
Environment=PATH=/usr/bin:/bin
Environment=LANG=C.UTF-8
${knowledgeCredentialSource ? `LoadCredential=pixel-knowledge-vault-key:${knowledgeCredentialSource}\n` : ""}NoNewPrivileges=true
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
ReadWritePaths=${writable.join(" ")}
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
RuntimeDirectory=${runtimeDirectory}
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=no
MemoryMax=2G
TasksMax=512
TimeoutStartSec=infinity
TimeoutStopSec=5min
KillMode=control-group
Restart=no
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${unitStem}
`;
  const timer = `[Unit]
Description=Watchdog for Pixel supervised Deep Work goal ${goal.goalId}

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
  const watchPaths = [
    posix.join(stateRoot, "goal-checkpoints", goal.goalId, "records"),
    ...goal.milestones.map((milestone) => posix.join(stateRoot, "checkpoints", milestone.jobId, "records")),
  ];
  const path = `[Unit]
Description=Resume Pixel Deep Work when durable goal state changes for ${goal.goalId}

[Path]
${watchPaths.map((watchPath) => `PathChanged=${watchPath}`).join("\n")}
MakeDirectory=false
TriggerLimitIntervalSec=60s
TriggerLimitBurst=128
Unit=${serviceName}

[Install]
WantedBy=paths.target
`;
  return Object.freeze({
    serviceName, timerName, pathName, service, timer, path,
    boundary: "Static systemd supervision only. Durable checkpoint events resume bounded continuous reconciliation; the timer is only a stalled/restart watchdog. Neither grants a work lease, replay, scope expansion, external effect, or completion authority. Docker socket access remains a high-trust supervisor capability.",
  });
}
