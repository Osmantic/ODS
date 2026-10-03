// Supported-host temp hygiene: a bounded, least-privilege sweep of the per-operation
// native shared-object files that the local-agent runtime self-extracts into the system
// temp directory and does not remove. On a long-lived deployment these accumulate and can
// fill the root filesystem. This unit is defense-in-depth for supported hosts and is
// independent of any upstream runtime fix. It runs as the unprivileged service account, so
// the sticky temp directory permits it to remove only files that account owns (the leaked
// extracts), and it deletes only files matching the fixed leak name pattern that are older
// than a bounded age. It never removes recent files (which a running agent may hold mapped).

const ACCOUNT_RE = /^[a-z_][a-z0-9_-]{0,31}$/u;
const SAFE_PATH_RE = /^\/[A-Za-z0-9._/-]+$/u;

// Hardcoded, never caller-supplied: the observed leak is `.dd<hex>-<counter>.so`.
const LEAK_NAME_PATTERN = ".dd*-*.so";
const SWEEPABLE_DIRECTORIES = new Set(["/tmp", "/var/tmp"]);

export class TmpHygieneUnitError extends Error {}

function fail(message) { throw new TmpHygieneUnitError(message); }

function account(value, label) {
  if (!ACCOUNT_RE.test(value ?? "") || value === "root") fail(`${label} is invalid or privileged`);
  return value;
}

function safePath(value, label) {
  if (
    typeof value !== "string" || !SAFE_PATH_RE.test(value) || value.includes("//")
    || value.endsWith("/") || value === "/"
  ) fail(`${label} is not a canonical safe path`);
  return value;
}

function boundedInt(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is out of range`);
  return value;
}

export function renderTmpHygieneUnits({
  serviceUser = "pixel-work", serviceGroup = "pixel-work",
  tmpDir = "/tmp", findPath = "/usr/bin/find",
  ageMinutes = 360, intervalMinutes = 60,
} = {}) {
  const user = account(serviceUser, "hygiene service user");
  const group = account(serviceGroup, "hygiene service group");
  const find = safePath(findPath, "hygiene find executable");
  const directory = safePath(tmpDir, "hygiene sweep directory");
  if (!SWEEPABLE_DIRECTORIES.has(directory)) fail("hygiene sweep directory must be a system temp directory");
  const age = boundedInt(ageMinutes, 60, 43200, "hygiene age minutes");
  const interval = boundedInt(intervalMinutes, 15, 1440, "hygiene interval minutes");

  // No shell is involved: systemd passes the argument vector directly, and find applies the
  // `-name` glob itself. The `-mmin +age` and `-name` predicates are both mandatory here, so
  // the command can never become an unbounded delete.
  const execStart = `${find} ${directory} -xdev -mindepth 1 -maxdepth 1 -type f -name ${LEAK_NAME_PATTERN} -mmin +${age} -delete`;

  const service = [
    "[Unit]",
    "Description=Pixel supported-host temp hygiene (bounded native-extract leak sweep)",
    "Documentation=https://osmantic.com/pixel/supported-host/tmp-hygiene",
    "",
    "[Service]",
    "Type=oneshot",
    `User=${user}`,
    `Group=${group}`,
    "NoNewPrivileges=true",
    "ProtectSystem=strict",
    "ProtectHome=true",
    "PrivateNetwork=true",
    "PrivateDevices=true",
    "ProtectKernelTunables=true",
    "ProtectControlGroups=true",
    "RestrictAddressFamilies=AF_UNIX",
    "RestrictNamespaces=true",
    "LockPersonality=true",
    "MemoryDenyWriteExecute=true",
    "SystemCallFilter=@system-service",
    "SystemCallErrorNumber=EPERM",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
    `ReadWritePaths=${directory}`,
    `ExecStart=${execStart}`,
    "",
  ].join("\n");

  const timer = [
    "[Unit]",
    "Description=Pixel supported-host temp hygiene schedule",
    "",
    "[Timer]",
    `OnBootSec=${interval}min`,
    `OnUnitActiveSec=${interval}min`,
    "Persistent=true",
    "",
    "[Install]",
    "WantedBy=timers.target",
    "",
  ].join("\n");

  return { service, timer, execStart, leakNamePattern: LEAK_NAME_PATTERN, ageMinutes: age, intervalMinutes: interval };
}
