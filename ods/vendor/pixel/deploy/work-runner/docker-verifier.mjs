import { posix } from "node:path";

import { canonical, validateWorkPlan, validateWorkPolicy } from "../../scripts/lib/work-contract.mjs";

const NAME_RE = /^[a-z0-9][a-z0-9_.-]{0,127}$/;
const IMAGE_RE = /^(?:sha256:[a-f0-9]{64}|[a-z0-9][a-z0-9._/:+-]{1,255}@sha256:[a-f0-9]{64})$/;
const SAFE_PATH_RE = /^\/[A-Za-z0-9._\/-]+$/;
const SHA_RE = /^[a-f0-9]{64}$/;
const COMMAND_RECEIPT_BOUNDARY = "Content-free independent command receipt. It records only fixed identifiers, counters, digests, and pass/fail state and grants no action authority.";

export class DockerVerifierError extends Error {}

function fail(message) {
  throw new DockerVerifierError(message);
}

function safeName(value, label) {
  if (!NAME_RE.test(value ?? "")) fail(`${label} is invalid`);
  return value;
}

function safePath(value, label) {
  if (typeof value !== "string" || !SAFE_PATH_RE.test(value) || !posix.isAbsolute(value) || posix.normalize(value) !== value || value.includes("//")) fail(`${label} is invalid`);
  return value;
}

function positiveId(value, label) {
  if (!Number.isSafeInteger(value) || value < 1 || value > 2147483647) fail(`${label} is invalid`);
  return value;
}

function imageIdentifier(prepared, runtime) {
  const value = runtime.imageIdentifier ?? prepared.policy.runner.imageRef;
  if (!IMAGE_RE.test(value ?? "") || (value !== prepared.plan.isolation.runnerImageDigest && value !== prepared.policy.runner.imageRef)) fail("verifier image differs from the immutable plan");
  return value;
}

function exactVerifier(prepared, claim, check, runtime) {
  const planErrors = validateWorkPlan(prepared?.plan);
  const policyErrors = validateWorkPolicy(prepared?.policy);
  if (planErrors.length || policyErrors.length || prepared.plan.profile !== "builder") fail("verifier received an invalid Builder plan or policy");
  if (!prepared.policy.verifier.enabled || prepared.plan.verification.network !== "none") fail("independent verifier is disabled or networked");
  const selected = prepared.plan.verification.checks.find((candidate) => candidate.id === check?.id);
  if (!selected || canonical(selected) !== canonical(check)) fail("verifier check differs from the immutable plan");
  if (check.kind === "command" && !prepared.policy.verifier.allowedExecutables.includes(check.argv[0])) fail("verifier executable is not allowed by private policy");
  if (
    claim?.status !== "consumed" || claim.externalEffects !== false || claim.jobId !== prepared.plan.jobId
    || claim.planSha256 !== prepared.bindings?.planSha256 || claim.workspaceSha256 !== prepared.workspace?.sha256
    || claim.runnerImageDigest !== prepared.plan.isolation.runnerImageDigest
  ) fail("verifier claim differs from the consumed Builder lease");
  if (prepared.workspace?.originalPath === prepared.workspace?.path || prepared.workspace?.disposable !== true) fail("verifier source boundary is invalid");
  safePath(prepared.workspace.originalPath, "verifier source path");
  safePath(runtime.dockerPath, "Docker client path");
  for (const [label, value] of [
    ["verifier patch path", runtime.patchPath], ["verifier check path", runtime.checkPath], ["verifier keeper CID path", runtime.keeperCidFile],
    ["verifier command CID path", runtime.commandCidFile],
  ]) safePath(value, label);
  for (const [label, value] of [
    ["verifier volume name", runtime.volumeName], ["verifier keeper name", runtime.keeperName], ["verifier apply name", runtime.applyName], ["verifier command name", runtime.commandName],
  ]) safeName(value, label);
  positiveId(runtime.uid, "verifier UID");
  positiveId(runtime.gid, "verifier GID");
  if (runtime.volumeSizeBytes !== Math.floor(prepared.lease.budgets.maxDiskBytes / 2) || runtime.volumeSizeBytes < 524288) fail("verifier volume size differs from the Builder disk boundary");
  if (
    !SHA_RE.test(runtime.patchSha256 ?? "") || !Number.isSafeInteger(runtime.patchBytes) || runtime.patchBytes < 2 || runtime.patchBytes > prepared.lease.budgets.maxArtifactBytes
    || !Number.isSafeInteger(runtime.patchChanges) || runtime.patchChanges < 0 || runtime.patchChanges > 100000
  ) fail("verifier patch artifact binding is invalid");
  return true;
}

function mount(source, target, type = "bind", readonly = true) {
  return `type=${type},src=${source},dst=${target}${readonly ? ",readonly" : ""}${type === "volume" ? ",volume-nocopy" : ""}`;
}

function containerBase(prepared, claim, check, runtime, role, name) {
  exactVerifier(prepared, claim, check, runtime);
  const memoryMiB = Math.min(2048, Math.max(256, prepared.lease.budgets.maxMemoryMiB));
  return [
    "run", "--rm", "--pull", "never", "--name", name,
    "--label", `com.osmantic.pixel.work-claim=${claim.claimId}`,
    "--label", `com.osmantic.pixel.work-job=${claim.jobId}`,
    "--label", `com.osmantic.pixel.work-check=${check.id}`,
    "--label", `com.osmantic.pixel.work-role=${role}`,
    "--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
    "--pids-limit", role === "verifier-command" ? "256" : "64",
    "--memory", `${memoryMiB}m`, "--memory-swap", `${memoryMiB}m`, "--cpus", String(Math.min(4, prepared.lease.budgets.maxCpuCores)),
    "--ulimit", "nofile=256:256", "--ulimit", `fsize=${runtime.volumeSizeBytes}:${runtime.volumeSizeBytes}`,
    "--ipc", "none", "--cgroupns", "private", "--stop-timeout", "3", "--log-driver", "none",
    "--user", `${runtime.uid}:${runtime.gid}`,
  ];
}

export function buildVerifierVolumeCreate(prepared, claim, check, runtime) {
  exactVerifier(prepared, claim, check, runtime);
  const option = `size=${runtime.volumeSizeBytes},uid=${runtime.uid},gid=${runtime.gid},mode=0700,nosuid,nodev`;
  return {
    command: runtime.dockerPath,
    args: [
      "volume", "create", "--driver", "local", "--opt", "type=tmpfs", "--opt", "device=tmpfs", "--opt", `o=${option}`,
      "--label", `com.osmantic.pixel.work-claim=${claim.claimId}`,
      "--label", `com.osmantic.pixel.work-job=${claim.jobId}`,
      "--label", `com.osmantic.pixel.work-check=${check.id}`,
      "--label", "com.osmantic.pixel.work-role=verifier-candidate", runtime.volumeName,
    ],
    option,
  };
}

export function validateVerifierVolumeInspect(volume, prepared, claim, check, runtime) {
  const expected = buildVerifierVolumeCreate(prepared, claim, check, runtime);
  const labels = volume?.Labels ?? {};
  if (
    volume?.Name !== runtime.volumeName || volume.Driver !== "local" || volume.Scope !== "local"
    || canonical(labels) !== canonical({
      "com.osmantic.pixel.work-check": check.id, "com.osmantic.pixel.work-claim": claim.claimId,
      "com.osmantic.pixel.work-job": claim.jobId, "com.osmantic.pixel.work-role": "verifier-candidate",
    })
    || canonical(volume.Options) !== canonical({ device: "tmpfs", o: expected.option, type: "tmpfs" })
  ) fail("verifier candidate volume differs from its exact tmpfs boundary");
  return true;
}

export function buildVerifierKeeperDockerCommand(prepared, claim, check, runtime) {
  const image = imageIdentifier(prepared, runtime);
  return {
    command: runtime.dockerPath,
    args: [
      ...containerBase(prepared, claim, check, runtime, "verifier-keeper", runtime.keeperName), "-d", "--cidfile", runtime.keeperCidFile,
      "--mount", mount(runtime.volumeName, "/workspace", "volume", true), "--entrypoint", "/bin/sleep", image, "infinity",
    ],
  };
}

function hasAll(value) {
  return Array.isArray(value) && value.some((item) => String(item).toUpperCase() === "ALL");
}

function hasNoNewPrivileges(value) {
  return Array.isArray(value) && value.some((item) => String(item).toLowerCase().replaceAll("=", ":") === "no-new-privileges:true");
}

export function validateVerifierKeeperInspect(container, image, prepared, claim, check, runtime) {
  exactVerifier(prepared, claim, check, runtime);
  const labels = container?.Config?.Labels ?? {};
  const host = container?.HostConfig ?? {};
  const mounts = container?.Mounts ?? [];
  if (
    !/^[a-f0-9]{64}$/.test(container?.Id ?? "") || container.Name !== `/${runtime.keeperName}` || container.Image !== image?.Id || container.State?.Running !== true
    || labels["com.osmantic.pixel.work-check"] !== check.id || labels["com.osmantic.pixel.work-claim"] !== claim.claimId
    || labels["com.osmantic.pixel.work-job"] !== claim.jobId || labels["com.osmantic.pixel.work-role"] !== "verifier-keeper"
  ) fail("verifier keeper identity is invalid");
  if (
    !hasAll(host.CapDrop) || !hasNoNewPrivileges(host.SecurityOpt) || host.ReadonlyRootfs !== true || host.Privileged === true
    || host.NetworkMode !== "none" || host.IpcMode !== "none" || host.CgroupnsMode !== "private" || host.PidsLimit !== 64
    || host.Memory < 268435456 || host.MemorySwap !== host.Memory || host.LogConfig?.Type !== "none"
    || Object.keys(host.PortBindings ?? {}).length !== 0 || (host.Devices ?? []).length !== 0
    || mounts.length !== 1 || mounts[0].Type !== "volume" || mounts[0].Name !== runtime.volumeName || mounts[0].Destination !== "/workspace" || mounts[0].RW !== false
  ) fail("verifier keeper hardening is incomplete");
  const networks = container.NetworkSettings?.Networks ?? {};
  const none = networks.none;
  if (
    JSON.stringify(Object.keys(networks)) !== JSON.stringify(["none"]) || !none || none.IPAddress !== "" || none.Gateway !== ""
    || none.GlobalIPv6Address !== "" || none.IPv6Gateway !== "" || none.IPPrefixLen !== 0 || none.GlobalIPv6PrefixLen !== 0
  ) fail("verifier keeper gained a network attachment");
  return true;
}

export function buildVerifierApplyDockerCommand(prepared, claim, check, runtime) {
  const image = imageIdentifier(prepared, runtime);
  const prefixes = Buffer.from(JSON.stringify(prepared.plan.verification.immutablePathPrefixes), "utf8").toString("base64url");
  return {
    command: runtime.dockerPath,
    args: [
      ...containerBase(prepared, claim, check, runtime, "verifier-apply", runtime.applyName),
      "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
      "--mount", mount(prepared.workspace.originalPath, "/source"),
      "--mount", mount(runtime.patchPath, "/run/pixel/builder-patch.json"),
      "--mount", mount(runtime.volumeName, "/workspace", "volume", false),
      "--entrypoint", "/opt/node/bin/node", image,
      "/opt/pixel/deploy/work-runner/builder-volume.mjs", "apply", "/source", "/workspace", "/run/pixel/builder-patch.json",
      runtime.patchSha256, String(runtime.volumeSizeBytes), String(prepared.lease.budgets.maxArtifactBytes), claim.jobId, claim.claimId,
      claim.planSha256, claim.workspaceSha256, prefixes,
    ],
  };
}

export function buildVerifierCommandDockerCommand(prepared, claim, check, runtime, candidateSha256) {
  if (check.kind !== "command" || !SHA_RE.test(candidateSha256 ?? "")) fail("verifier command candidate binding is invalid");
  const image = imageIdentifier(prepared, runtime);
  return {
    command: runtime.dockerPath,
    args: [
      ...containerBase(prepared, claim, check, runtime, "verifier-command", runtime.commandName), "--cidfile", runtime.commandCidFile,
      "--tmpfs", "/tmp:rw,nosuid,nodev,exec,size=256m,mode=1777",
      "--env", "HOME=/tmp/home", "--env", "LANG=C.UTF-8", "--env", "PATH=/opt/node/bin:/usr/bin:/bin", "--env", "TMPDIR=/tmp",
      "--mount", mount(runtime.volumeName, "/workspace", "volume", true),
      "--mount", mount(runtime.checkPath, "/run/pixel/verifier-check.json"),
      "--entrypoint", "/opt/node/bin/node", image,
      "/opt/pixel/deploy/work-runner/verifier-command.mjs", "run", "/run/pixel/verifier-check.json", claim.planSha256, candidateSha256,
    ],
  };
}

export function validateVerifierApplyReceipt(value, prepared, claim, check, runtime) {
  exactVerifier(prepared, claim, check, runtime);
  if (
    canonical(Object.keys(value ?? {}).sort()) !== canonical(["bytes", "candidateSha256", "changes", "files", "operation", "schemaVersion"].sort())
    || value.schemaVersion !== 1 || value.operation !== "builder-volume-apply" || !SHA_RE.test(value.candidateSha256 ?? "")
    || value.changes !== runtime.patchChanges
    || !Number.isSafeInteger(value.files) || value.files < 0 || value.files > 100000
    || !Number.isSafeInteger(value.bytes) || value.bytes < 0 || value.bytes > runtime.volumeSizeBytes
  ) fail("verifier candidate receipt is invalid");
  return true;
}

export function validateVerifierCommandReceipt(value, prepared, claim, check, runtime, candidateSha256) {
  exactVerifier(prepared, claim, check, runtime);
  const keys = ["schemaVersion", "operation", "checkId", "kind", "criterionIndexes", "planSha256", "candidateSha256", "status", "exitCode", "signal", "timedOut", "outputLimitExceeded", "spawnFailed", "durationMilliseconds", "stdout", "stderr", "boundary"];
  if (!value || canonical(Object.keys(value).sort()) !== canonical(keys.sort())) fail("verifier command receipt shape is invalid");
  if (
    value.schemaVersion !== 1 || value.operation !== "pixel-independent-command-check" || value.checkId !== check.id || value.kind !== "command"
    || canonical(value.criterionIndexes) !== canonical(check.criterionIndexes) || value.planSha256 !== claim.planSha256 || value.candidateSha256 !== candidateSha256
    || !["pass", "fail"].includes(value.status) || !Number.isSafeInteger(value.durationMilliseconds) || value.durationMilliseconds < 0 || value.durationMilliseconds > check.timeoutSeconds * 1000 + 30000
    || ![value.timedOut, value.outputLimitExceeded, value.spawnFailed].every((item) => typeof item === "boolean")
    || !(value.exitCode === null || (Number.isSafeInteger(value.exitCode) && value.exitCode >= 0 && value.exitCode <= 255))
    || !(value.signal === null || (typeof value.signal === "string" && /^SIG[A-Z0-9]{1,16}$/.test(value.signal)))
    || value.boundary !== COMMAND_RECEIPT_BOUNDARY
  ) fail("verifier command receipt binding is invalid");
  for (const stream of [value.stdout, value.stderr]) {
    if (!stream || canonical(Object.keys(stream).sort()) !== canonical(["bytes", "sha256"].sort()) || !Number.isSafeInteger(stream.bytes) || stream.bytes < 0 || !SHA_RE.test(stream.sha256 ?? "")) fail("verifier output digest is invalid");
  }
  if (value.stdout.bytes + value.stderr.bytes > check.maxOutputBytes + 1048576) fail("verifier output exceeded its hard receipt ceiling");
  if (value.stdout.bytes + value.stderr.bytes > check.maxOutputBytes && value.outputLimitExceeded !== true) fail("verifier output ceiling was not enforced");
  const passed = value.exitCode === 0 && value.signal === null && value.timedOut === false && value.outputLimitExceeded === false && value.spawnFailed === false;
  if ((value.status === "pass") !== passed) fail("verifier pass state is inconsistent");
  return true;
}

export const dockerVerifierInternals = Object.freeze({ exactVerifier });
