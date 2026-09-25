import { posix } from "node:path";

const SAFE_PATH_RE = /^\/[A-Za-z0-9._/-]+$/u;
const ACCOUNT_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const CAMPAIGN_RE = /^deepworksoak-[0-9]{13}-[a-f0-9]{12}$/u;
const forbiddenPrivateRoots = Object.freeze(["/home", "/root", "/run/user"]);

export class DeepWorkSoakServiceUnitError extends Error {}

function fail(message) { throw new DeepWorkSoakServiceUnitError(message); }
function safeLinuxPath(value, label, directory = false) {
  if (typeof value !== "string" || !SAFE_PATH_RE.test(value) || value.includes("//") || posix.normalize(value) !== value || value === "/" || directory && value.endsWith("/")) fail(`${label} is not a canonical safe Linux path`);
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
function overlaps(left, right) { return left === right || left.startsWith(`${right}/`) || right.startsWith(`${left}/`); }
function exactCampaign(config, configPath) {
  if (
    !config || config.schemaVersion !== 1 || config.operation !== "pixel-deep-work-multi-day-soak"
    || !CAMPAIGN_RE.test(config.campaignId ?? "")
    || config.schedule?.intervalSeconds !== 3600 || config.schedule?.minimumGapSeconds !== 3300
    || config.schedule?.maximumGapSeconds !== 7200 || config.schedule?.minimumElapsedSeconds !== 172800
    || config.schedule?.milestones !== 24 || config.schedule?.expectedCycles !== 49
    || config.paths?.root !== posix.dirname(configPath) || configPath !== posix.join(config.paths.root, "campaign.json")
    || config.paths.stateRoot !== posix.join(config.paths.root, "state")
    || config.paths.objectStore !== posix.join(config.paths.root, "objects")
    || config.paths.goalPath !== posix.join(config.paths.root, "contracts/goal.json")
    || config.paths.jobsPath !== posix.join(config.paths.root, "contracts/jobs.json")
    || config.paths.policyPath !== posix.join(config.paths.root, "contracts/policy.json")
    || config.paths.invocationLedgerRoot !== posix.join(config.paths.root, "invocations")
  ) fail("multi-day soak service campaign contract is invalid");
}

export function renderDeepWorkSoakServiceUnits({
  configPath, config, configSha256, installRoot, nodePath = "/usr/bin/node", flockPath = "/usr/bin/flock",
  envPath = "/usr/bin/env",
  serviceUser = "pixel-work", serviceGroup = "pixel-work",
} = {}) {
  const privateConfig = nonHomePath(configPath, "multi-day soak service configuration");
  exactCampaign(config, privateConfig);
  if (!/^[a-f0-9]{64}$/u.test(configSha256 ?? "")) fail("multi-day soak service configuration hash is invalid");
  const user = account(serviceUser, "multi-day soak service user");
  const group = account(serviceGroup, "multi-day soak service group");
  const installed = nonHomePath(installRoot, "multi-day soak install root", true);
  const node = nonHomePath(nodePath, "multi-day soak Node executable");
  const flock = nonHomePath(flockPath, "multi-day soak lock executable");
  const env = nonHomePath(envPath, "multi-day soak sterile environment executable");
  const cycleScript = posix.join(installed, "scripts/deep-work-multi-day-soak.mjs");
  const stateRoot = nonHomePath(config.paths.stateRoot, "multi-day soak state root", true);
  const invocationRoot = nonHomePath(config.paths.invocationLedgerRoot, "multi-day soak invocation root", true);
  nonHomePath(config.paths.root, "multi-day soak campaign root", true);
  const objectStore = nonHomePath(config.paths.objectStore, "multi-day soak object store", true);
  const contractsRoot = nonHomePath(posix.dirname(config.paths.goalPath), "multi-day soak contracts root", true);
  if (stateRoot === invocationRoot || stateRoot.startsWith(`${invocationRoot}/`) || invocationRoot.startsWith(`${stateRoot}/`)) fail("multi-day soak writable roots must be disjoint");
  for (const writable of [stateRoot, invocationRoot]) for (const protectedPath of [installed, privateConfig, objectStore, contractsRoot, node, flock, env]) {
    if (overlaps(writable, protectedPath)) fail("multi-day soak writable and protected paths must be disjoint");
  }
  const unitStem = `pixel-deep-work-${config.campaignId}`;
  const serviceName = `${unitStem}.service`, timerName = `${unitStem}.timer`;
  const lockPath = `/run/${unitStem}/cycle.lock`;
  const service = `[Unit]
Description=Pixel Deep Work 48-hour qualification cycle for ${config.campaignId}
After=local-fs.target time-sync.target
ConditionPathExists=${privateConfig}
StartLimitIntervalSec=7200
StartLimitBurst=3

[Service]
Type=oneshot
User=${user}
Group=${group}
WorkingDirectory=/
ExecStart=${flock} --exclusive --nonblock ${lockPath} ${env} -i HOME=/nonexistent PATH=/usr/bin:/bin LANG=C.UTF-8 INVOCATION_ID=\${INVOCATION_ID} ${node} ${cycleScript} cycle --config ${privateConfig} --confirm-config-sha256 ${configSha256}
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
ProtectSystem=strict
ReadOnlyPaths=${installed} ${privateConfig} ${contractsRoot} ${objectStore}
ReadWritePaths=${stateRoot} ${invocationRoot}
InaccessiblePaths=-/root -/home -/run/user -/run/docker.sock -/var/run/docker.sock
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
TasksMax=128
CPUQuota=50%
TimeoutStartSec=5min
TimeoutStopSec=30s
KillMode=control-group
Restart=no
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${unitStem}
`;
  const timer = `[Unit]
Description=Schedule Pixel Deep Work 48-hour qualification for ${config.campaignId}

[Timer]
OnCalendar=*-*-* *:00:00 UTC
Persistent=true
AccuracySec=1s
RandomizedDelaySec=0
Unit=${serviceName}

[Install]
WantedBy=timers.target
`;
  return Object.freeze({
    serviceName, timerName, service, timer,
    boundary: "Static qualification-only systemd supervision. The UTC hourly timer grants no work lease, provider, network, credential, external effect, deployment, publication, or release authority.",
  });
}
