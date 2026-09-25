import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { link, lstat, mkdir, open, readFile, statfs, unlink } from "node:fs/promises";
import { arch, cpus, platform as osPlatform, totalmem } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { execFile } from "node:child_process";

import { canonical, validateWorkGoalFleet, validateWorkGoalFleetController, validateWorkGoalFleetHostProbe } from "../../scripts/lib/work-contract.mjs";
import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { validateGoalFleetHostEvidence, WorkGoalFleetError } from "./goal-fleet.mjs";
import {
  appendGoalFleetHostEvidence, goalFleetHostEvidenceRecordName, recoverGoalFleetHostEvidenceLedger,
  recoverInterruptedGoalFleetHostEvidencePublication, GoalFleetHostEvidenceLedgerError,
} from "./goal-fleet-host-evidence-ledger.mjs";

const MAX_CONFIG_BYTES = 512 * 1024;
const MAX_FLEET_BYTES = 2 * 1024 * 1024;
const MAX_CONTROLLER_BYTES = 256 * 1024;
const OUTPUT_RE = /^[A-Za-z0-9._-]{1,128}$/u;
const authority = Object.freeze({ grantsExecution: false, grantsLease: false, grantsReplay: false, grantsScopeExpansion: false, grantsExternalEffects: false, grantsCompletion: false });
const boundary = "Short-lived hash-chained host-capacity evidence. It reserves operating-system and shared-service ceilings before fleet admission and grants no execution, lease, replay, scope expansion, external effect, or completion authority.";

export class GoalFleetHostEvidenceCliError extends Error {}

function fail(message) { throw new GoalFleetHostEvidenceCliError(message); }
function sha(value) { return createHash("sha256").update(typeof value === "string" || Buffer.isBuffer(value) ? value : canonical(value)).digest("hex"); }

function parseArguments(argv) {
  if (Array.isArray(argv) && argv.length === 5 && argv[0] === "create" && argv[1] === "--config" && argv[3] === "--output" && argv[2] && argv[4]) {
    return { operation: "create", configPath: resolve(argv[2]), outputPath: resolve(argv[4]) };
  }
  if (Array.isArray(argv) && argv.length === 3 && argv[0] === "refresh" && argv[1] === "--controller" && argv[2]) {
    return { operation: "refresh", controllerPath: resolve(argv[2]) };
  }
  fail("Usage: goal-fleet-host-evidence-cli.mjs create --config PROBE --output FILE | refresh --controller FLEET_CONTROLLER");
}

async function readPrivateJson(path, maximum, label, expectedOwnerUid) {
  const { bytes, details } = await readBoundedRegularFile(path, maximum, label);
  if (details.nlink !== 1 || process.platform !== "win32" && (details.uid !== expectedOwnerUid || (details.mode & 0o077) !== 0)) fail(`${label} is not owner-private and single-link`);
  const text = bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(bytes)) fail(`${label} is not strict UTF-8`);
  try { return JSON.parse(text); } catch { fail(`${label} is not JSON`); }
}

async function checkedPrivateDirectory(path, label, expectedOwnerUid) {
  const info = await lstat(path).catch(() => null);
  if (!info?.isDirectory() || info.isSymbolicLink() || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${label} is not owner-private`);
}

async function loadRefreshController(controllerPath, expectedOwnerUid) {
  const config = await readPrivateJson(controllerPath, MAX_CONTROLLER_BYTES, "private fleet controller configuration", expectedOwnerUid);
  const errors = validateWorkGoalFleetController(config);
  if (errors.length) fail(`private fleet controller configuration is invalid: ${errors[0]}`);
  for (const path of [config.stateRoot, config.fleetPath, config.hostProbePath, config.hostEvidenceDirectory]) {
    if (resolve(path) !== path) fail("private fleet controller paths must be absolute and normalized");
  }
  await checkedPrivateDirectory(config.stateRoot, "private fleet state root", expectedOwnerUid);
  const evidenceDirectory = config.hostEvidenceDirectory;
  if (evidenceDirectory !== join(config.stateRoot, "host-evidence")) fail("private fleet host evidence ledger is outside its fixed state directory");
  await mkdir(evidenceDirectory, { mode: 0o700 }).catch((error) => { if (error?.code !== "EEXIST") throw error; });
  await checkedPrivateDirectory(evidenceDirectory, "private fleet host evidence directory", expectedOwnerUid);
  const probe = await readPrivateJson(config.hostProbePath, MAX_CONFIG_BYTES, "private fleet host probe configuration", expectedOwnerUid);
  const probeErrors = validateWorkGoalFleetHostProbe(probe);
  if (probeErrors.length || sha(probe) !== config.hostProbeSha256 || probe.fleetPath !== config.fleetPath) fail(`private fleet host probe differs from its exact configuration binding: ${probeErrors[0] ?? "digest or fleet path differs"}`);
  return { configPath: config.hostProbePath, evidenceDirectory, genesisSha256: config.hostEvidenceGenesisSha256, probe, operation: "refresh" };
}

function parsedProperties(text, unitName) {
  if (typeof text !== "string" || Buffer.byteLength(text) > 64 * 1024) fail(`shared service ${unitName} returned invalid systemd evidence`);
  const expected = new Set(["LoadState", "ActiveState", "CPUQuotaPerSecUSec", "MemoryMax"]);
  const values = {};
  for (const line of text.trim().split(/\r?\n/u)) {
    const index = line.indexOf("=");
    const key = line.slice(0, index), value = line.slice(index + 1);
    if (index < 1 || !expected.has(key) || Object.hasOwn(values, key) || !value) fail(`shared service ${unitName} returned malformed systemd evidence`);
    values[key] = value;
  }
  if (Object.keys(values).length !== expected.size || values.LoadState !== "loaded" || values.ActiveState !== "active") fail(`shared service ${unitName} is not loaded and active`);
  return values;
}

function cpuQuotaCores(value) {
  const match = /^(\d+)(us|ms|s|min)$/u.exec(value);
  if (!match) fail("shared service CPU quota is absent, infinite, or invalid");
  const multipliers = { us: 1, ms: 1000, s: 1000000, min: 60000000 };
  const microseconds = Number(match[1]) * multipliers[match[2]];
  if (!Number.isSafeInteger(microseconds) || microseconds < 1000000 || microseconds % 1000000 !== 0) fail("shared service CPU quota must be a finite whole-core ceiling");
  return microseconds / 1000000;
}

function memoryLimitMiB(value) {
  if (!/^[1-9][0-9]*$/u.test(value)) fail("shared service memory limit is absent, infinite, or invalid");
  const bytes = Number(value);
  if (!Number.isSafeInteger(bytes)) fail("shared service memory limit is too large");
  return Math.ceil(bytes / 1048576);
}

export function buildGoalFleetHostEvidence({ fleet, probe, host, serviceProperties, sequence = 0, previousEvidenceSha256 = null, now = new Date(), suffix = randomBytes(6).toString("hex") }) {
  const probeErrors = validateWorkGoalFleetHostProbe(probe);
  const fleetErrors = validateWorkGoalFleet(fleet);
  if (probeErrors.length || fleetErrors.length) fail(`fleet host probe input is invalid: ${probeErrors[0] ?? fleetErrors[0]}`);
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime()) || now.getTime() < 0 || !Number.isSafeInteger(sequence) || sequence < 0 || sequence > 1000000 || (sequence === 0 ? previousEvidenceSha256 !== null : !/^[a-f0-9]{64}$/u.test(previousEvidenceSha256 ?? "")) || !/^[a-f0-9]{12}$/u.test(suffix)) fail("fleet host evidence identity or chain position is invalid");
  if (
    !host || !Number.isSafeInteger(host.cpuCores) || !Number.isSafeInteger(host.memoryMiB) || !Number.isSafeInteger(host.disposableDiskBytes)
    || host.cpuCores < 1 || host.memoryMiB < 256 || host.disposableDiskBytes < 1048576 || typeof host.fingerprintSource !== "string"
    || !host.fingerprintSource || Buffer.byteLength(host.fingerprintSource) > 4096
  ) fail("fleet host measurement is invalid");
  if (!Array.isArray(serviceProperties) || serviceProperties.length !== probe.sharedServices.length) fail("fleet shared-service evidence set is incomplete");
  const byUnit = new Map(serviceProperties.map((entry) => [entry?.unitName, entry?.properties]));
  if (byUnit.size !== serviceProperties.length) fail("fleet shared-service evidence set is duplicated");
  const sharedServices = probe.sharedServices.map((service) => {
    const properties = parsedProperties(byUnit.get(service.unitName), service.unitName);
    const liveCpu = cpuQuotaCores(properties.CPUQuotaPerSecUSec);
    const liveMemory = memoryLimitMiB(properties.MemoryMax);
    if (liveCpu > service.maxCpuCores || liveMemory > service.maxMemoryMiB) fail(`shared service ${service.unitName} exceeds its reviewed reserve`);
    return {
      serviceId: service.serviceId, unitName: service.unitName,
      limitEvidenceSha256: sha({ unitName: service.unitName, properties, diskLimitEvidenceSha256: service.diskLimitEvidenceSha256 }),
      maxCpuCores: service.maxCpuCores, maxMemoryMiB: service.maxMemoryMiB, maxDiskBytes: service.maxDiskBytes,
    };
  });
  const sharedCpu = sharedServices.reduce((total, service) => total + service.maxCpuCores, 0);
  const sharedMemory = sharedServices.reduce((total, service) => total + service.maxMemoryMiB, 0);
  const sharedDisk = sharedServices.reduce((total, service) => total + service.maxDiskBytes, 0);
  const evidence = {
    $schema: "https://osmantic.com/pixel/schemas/work-goal-fleet-host-evidence-v2.schema.json", schemaVersion: 2,
    sequence, previousEvidenceSha256,
    evidenceId: `workhostevidence-${String(now.getTime()).padStart(13, "0")}-${suffix}`,
    fleetId: fleet.fleetId, fleetSha256: sha(fleet), hostProbeSha256: sha(probe), observedAt: now.toISOString(),
    expiresAt: new Date(now.getTime() + probe.evidenceLifetimeSeconds * 1000).toISOString(), hostFingerprintSha256: sha(host.fingerprintSource),
    measurement: {
      source: "reviewed-linux-capacity-and-service-limits",
      physical: { cpuCores: host.cpuCores, memoryMiB: host.memoryMiB, disposableDiskBytes: host.disposableDiskBytes },
      systemReserve: { ...probe.systemReserve }, sharedServices,
      admitted: {
        cpuCores: host.cpuCores - probe.systemReserve.cpuCores - sharedCpu,
        memoryMiB: host.memoryMiB - probe.systemReserve.memoryMiB - sharedMemory,
        disposableDiskBytes: host.disposableDiskBytes - probe.systemReserve.diskBytes - sharedDisk,
      },
    },
    authority: { ...authority }, boundary,
  };
  validateGoalFleetHostEvidence({ fleet, probe, evidence, now });
  return Object.freeze(evidence);
}

function runSystemctl(unitName) {
  return new Promise((done) => execFile("/usr/bin/systemctl", [
    "show", unitName, "--property=LoadState", "--property=ActiveState", "--property=CPUQuotaPerSecUSec", "--property=MemoryMax",
  ], { cwd: "/", env: { HOME: "/nonexistent", PATH: "/usr/bin:/bin", LANG: "C.UTF-8" }, encoding: "utf8", timeout: 30000, maxBuffer: 64 * 1024, windowsHide: true, shell: false },
  (error, stdout, stderr) => done({ code: typeof error?.code === "number" ? error.code : error ? 1 : 0, stdout: stdout ?? "", stderr: stderr ?? "" })));
}

async function observeHost(disposableDiskPath) {
  const [filesystem, machineId] = await Promise.all([statfs(disposableDiskPath, { bigint: true }), readFile("/etc/machine-id", "utf8")]);
  if (!machineId.trim() || Buffer.byteLength(machineId) > 4096) fail("fleet host identity source is invalid");
  const disk = filesystem.bavail * filesystem.bsize;
  if (disk > BigInt(Number.MAX_SAFE_INTEGER)) fail("fleet host free disk exceeds safe accounting range");
  return {
    cpuCores: cpus().length, memoryMiB: Math.floor(totalmem() / 1048576), disposableDiskBytes: Number(disk),
    fingerprintSource: `${machineId.trim()}\0${osPlatform()}\0${arch()}\0${cpus().length}`,
  };
}

async function publishEvidence(outputPath, serialized, expectedOwnerUid) {
  const existing = await lstat(outputPath).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
  if (existing && (!existing.isFile() || existing.isSymbolicLink() || existing.nlink !== 1 || process.platform !== "win32" && (existing.uid !== expectedOwnerUid || (existing.mode & 0o077) !== 0))) fail("existing fleet host evidence is not owner-private, regular, and single-link");
  if (existing) fail("fleet host evidence output already exists");
  const temporary = join(dirname(outputPath), `.host-evidence-${randomBytes(8).toString("hex")}`);
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(serialized, "utf8"); await handle.sync(); } finally { await handle.close(); }
  try {
    await link(temporary, outputPath); await unlink(temporary);
    if (process.platform !== "win32") {
      const directory = await open(dirname(outputPath), constants.O_RDONLY);
      try { await directory.sync(); } finally { await directory.close(); }
    }
  }
  catch (error) {
    await unlink(temporary).catch(() => {});
    if (error?.code === "EEXIST") fail("fleet host evidence output already exists");
    throw error;
  }
}

export async function generateGoalFleetHostEvidence(argv, dependencies = {}) {
  const parsed = parseArguments(argv);
  const expectedOwnerUid = dependencies.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 0) fail("fleet host probe owner is invalid");
  if ((dependencies.platform ?? process.platform) !== "linux") fail("fleet host evidence generation requires Linux");
  const options = parsed.operation === "refresh" ? await loadRefreshController(parsed.controllerPath, expectedOwnerUid) : parsed;
  const { configPath } = options;
  if (parsed.operation === "create") {
    if (join(dirname(options.outputPath), basename(options.outputPath)) !== options.outputPath || !OUTPUT_RE.test(basename(options.outputPath))) fail("fleet host evidence output name is invalid");
    if (basename(options.outputPath) !== goalFleetHostEvidenceRecordName(0)) fail("fleet host evidence genesis output must be named 0000000.json");
    const parent = await lstat(dirname(options.outputPath)).catch(() => null);
    if (!parent?.isDirectory() || parent.isSymbolicLink() || process.platform !== "win32" && (parent.uid !== expectedOwnerUid || (parent.mode & 0o077) !== 0)) fail("fleet host evidence output parent is not owner-private");
  }
  const probe = options.probe ?? await readPrivateJson(configPath, MAX_CONFIG_BYTES, "private fleet host probe configuration", expectedOwnerUid);
  const probeErrors = validateWorkGoalFleetHostProbe(probe);
  if (probeErrors.length || resolve(probe.fleetPath) !== probe.fleetPath || resolve(probe.disposableDiskPath) !== probe.disposableDiskPath) fail(`private fleet host probe configuration is invalid: ${probeErrors[0] ?? "paths are not absolute and normalized"}`);
  const fleet = await readPrivateJson(probe.fleetPath, MAX_FLEET_BYTES, "private immutable goal fleet", expectedOwnerUid);
  const now = dependencies.now ?? new Date();
  if (!(now instanceof Date) || !Number.isSafeInteger(now.getTime())) fail("fleet host evidence generation time is invalid");
  let sequence = 0, previousEvidenceSha256 = null, evidenceDirectory = null, publicationRecoveryAction = "not-applicable";
  if (parsed.operation === "refresh") {
    evidenceDirectory = options.evidenceDirectory;
    const publicationRecovery = await recoverInterruptedGoalFleetHostEvidencePublication({
      directory: evidenceDirectory, expectedOwnerUid, now,
      minimumAgeMs: dependencies.publicationRecoveryMinimumAgeMs ?? 300000,
    });
    publicationRecoveryAction = publicationRecovery.action;
    const recovered = await recoverGoalFleetHostEvidenceLedger({
      directory: evidenceDirectory, genesisSha256: options.genesisSha256, fleet, probe,
      expectedOwnerUid, now, requireCurrent: false,
    });
    const observedAt = Date.parse(recovered.head.observedAt), expiresAt = Date.parse(recovered.head.expiresAt);
    const renewalFloor = observedAt + Math.floor((expiresAt - observedAt) / 2);
    if (now.getTime() >= observedAt && now.getTime() < renewalFloor) {
      return Object.freeze({
        schemaVersion: 1, operation: "pixel-work-goal-fleet-host-evidence", fleetId: fleet.fleetId,
        action: "current-evidence-retained", publicationRecoveryAction, evidenceId: recovered.head.evidenceId, evidenceSha256: recovered.headSha256,
        expiresAt: recovered.head.expiresAt, outputName: goalFleetHostEvidenceRecordName(recovered.head.sequence),
        authority: { ...authority }, boundary: "Content-free host-evidence refresh receipt. A still-current exact chained observation was retained; no service was probed or mutated and no work authority was granted.",
      });
    }
    if (now.getTime() <= observedAt) fail("fleet host evidence refresh time did not advance");
    sequence = recovered.head.sequence + 1;
    previousEvidenceSha256 = recovered.headSha256;
  }
  const runner = dependencies.systemctlRunner ?? (async (unitName) => runSystemctl(unitName));
  const hostObserver = dependencies.hostObserver ?? observeHost;
  if (typeof runner !== "function" || typeof hostObserver !== "function") fail("fleet host probe dependencies are invalid");
  const serviceProperties = await Promise.all(probe.sharedServices.map(async (service) => {
    const result = await runner(service.unitName);
    if (!result || result.code !== 0 || typeof result.stdout !== "string" || typeof result.stderr !== "string") fail(`shared service ${service.unitName} inspection failed closed`);
    return { unitName: service.unitName, properties: result.stdout };
  }));
  const evidence = buildGoalFleetHostEvidence({ fleet, probe, host: await hostObserver(probe.disposableDiskPath), serviceProperties, sequence, previousEvidenceSha256, now, ...(dependencies.suffix ? { suffix: dependencies.suffix } : {}) });
  const serialized = `${JSON.stringify(evidence, null, 2)}\n`;
  let outputName;
  if (parsed.operation === "refresh") {
    await appendGoalFleetHostEvidence({ directory: evidenceDirectory, evidence, expectedOwnerUid });
    outputName = goalFleetHostEvidenceRecordName(evidence.sequence);
  } else {
    await publishEvidence(options.outputPath, serialized, expectedOwnerUid);
    outputName = basename(options.outputPath);
  }
  return Object.freeze({
    schemaVersion: 1, operation: "pixel-work-goal-fleet-host-evidence", fleetId: fleet.fleetId,
    action: parsed.operation === "refresh" ? "refreshed-current-evidence" : "created-evidence", publicationRecoveryAction, evidenceId: evidence.evidenceId,
    evidenceSha256: sha(evidence), expiresAt: evidence.expiresAt, outputName,
    authority: { ...authority }, boundary: "Content-free host-evidence generation receipt. Probing reads capacity and exact systemd limits but mutates no service and grants no work authority.",
  });
}

export async function refreshGoalFleetHostEvidence(controllerPath, dependencies = {}) {
  return generateGoalFleetHostEvidence(["refresh", "--controller", controllerPath], dependencies);
}

export async function main(argv = process.argv.slice(2)) {
  process.stdout.write(`${JSON.stringify(await generateGoalFleetHostEvidence(argv))}\n`);
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().catch((error) => {
    const expected = error instanceof GoalFleetHostEvidenceCliError || error instanceof GoalFleetHostEvidenceLedgerError || error instanceof WorkGoalFleetError;
    process.stderr.write(`pixel-work-fleet-host-evidence: ${expected ? error.message : "unexpected failure"}\n`);
    process.exitCode = 1;
  });
}
