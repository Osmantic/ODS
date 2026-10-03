import { constants } from "node:fs";
import { randomBytes } from "node:crypto";
import { lstat, open, readFile, realpath, rename, stat, unlink } from "node:fs/promises";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { canonical } from "../../scripts/lib/work-contract.mjs";
import { parseStrictJson, readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import {
  WorkMaintenanceRecoverySystemdError,
  assertCgroupContainsUnit,
  assertExactGuardianArgv,
  deriveSystemdUnitFacts,
  hostRootIsUnmappedInCurrentNamespace,
  readInvocationIdFromProc,
} from "./maintenance-recovery-guardian-systemd.mjs";

const MAX_LEASE_BYTES = 128 * 1024;
const SHA_RE = /^[a-f0-9]{64}$/u;
const ZERO_SHA256 = "0".repeat(64);
const STARTED_RE = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$/u;
const UNIT_RE = /^[A-Za-z0-9_.-]+\.service$/u;
const INVOCATION_ID_RE = /^[0-9a-f]{32}$/u;
const CLOCK_TOLERANCE_MS = 30 * 1000;
export const GUARDIAN_UNIT = "pixel-maintenance-recovery.service";
export const GUARDIAN_MODULE_FILENAME = "maintenance-recovery-guardian.mjs";
export const DEFAULT_GUARDIAN_LEASE_TTL_MS = 5 * 60 * 1000;
export const GUARDIAN_LEASE_REFRESH_MS = 20 * 1000;
export const EXPECTED_RUNNING_SUBSTATE = "running";

export class WorkMaintenanceRecoveryGuardianLeaseError extends Error {}

function fail(message) { throw new WorkMaintenanceRecoveryGuardianLeaseError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function exactKeys(value, keys, label) {
  if (!value || typeof value !== "object" || Array.isArray(value) || canonical(Object.keys(value).sort()) !== canonical([...keys].sort())) fail(`${label} shape is invalid`);
}
function validateCustodyLockPath(custodyLockPath) {
  if (typeof custodyLockPath !== "string" || !isAbsolute(custodyLockPath) || resolve(custodyLockPath) !== custodyLockPath || custodyLockPath.includes("\0") || /[\r\n]/u.test(custodyLockPath)) fail("guardian lease custody lock path is noncanonical or unsafe");
}
function isNonEmptyString(value, maxLength) {
  return typeof value === "string" && value.length > 0 && value.length <= maxLength;
}
function argvEquals(left, right) {
  return Array.isArray(left) && Array.isArray(right) && left.length === right.length && left.every((entry, index) => entry === right[index]);
}

const BOOT_ID_MAX_LENGTH = 128;
const EXE_MAX_LENGTH = 4096;
const ARGV_MAX_ELEMENTS = 256;
const ARGV_ELEMENT_MAX_BYTES = 8192;
const ARGV_TOTAL_MAX_BYTES = 256 * 1024;
const CGROUP_MAX_LINES = 128;
const CGROUP_LINE_MAX_BYTES = 1024;
const UID_MAX_LENGTH = 64;
const BOOT_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;

function integer(value, minimum, maximum, label) {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail(`${label} is invalid`);
  return value;
}

// Shared strict validator for the exact Linux /proc process-identity shape
// emitted by deriveProcessIdentity(). This is the single source of truth for
// both guardian leases and campaign recovery journals so the identity contract
// cannot drift. It rejects any wall-clock timestamp as identity; only the exact
// seven /proc-derived keys are accepted and every mutable value is bounded.
export function validateProcessIdentity(identity) {
  exactKeys(identity, ["bootId", "pid", "startTicks", "exe", "argv", "cgroup", "uid"], "process identity");
  if (typeof identity.bootId !== "string" || !BOOT_ID_RE.test(identity.bootId)) fail("process identity boot id is invalid");
  integer(identity.pid, 1, 2147483647, "process identity pid");
  integer(identity.startTicks, 1, Number.MAX_SAFE_INTEGER, "process identity start ticks");
  if (typeof identity.exe !== "string" || identity.exe.length === 0 || Buffer.byteLength(identity.exe, "utf8") > EXE_MAX_LENGTH || !isAbsolute(identity.exe) || resolve(identity.exe) !== identity.exe || /[\r\n\0]/u.test(identity.exe)) fail("process identity executable is invalid");
  if (!Array.isArray(identity.argv) || identity.argv.length < 1 || identity.argv.length > ARGV_MAX_ELEMENTS) fail("process identity argv is invalid");
  let argvBytes = 0;
  for (const entry of identity.argv) {
    if (typeof entry !== "string" || entry.length === 0 || entry.includes("\0") || Buffer.byteLength(entry, "utf8") > ARGV_ELEMENT_MAX_BYTES) fail("process identity argv is invalid");
    argvBytes += Buffer.byteLength(entry, "utf8");
  }
  if (argvBytes > ARGV_TOTAL_MAX_BYTES) fail("process identity argv is invalid");
  if (!Array.isArray(identity.cgroup) || identity.cgroup.length < 1 || identity.cgroup.length > CGROUP_MAX_LINES) fail("process identity cgroup is invalid");
  for (const line of identity.cgroup) {
    if (typeof line !== "string" || line.length === 0 || Buffer.byteLength(line, "utf8") > CGROUP_LINE_MAX_BYTES || /[\r\n\0]/u.test(line)) fail("process identity cgroup is invalid");
  }
  if (typeof identity.uid !== "string" || identity.uid.length === 0 || identity.uid.length > UID_MAX_LENGTH || !/^[0-9]+$/u.test(identity.uid) || !Number.isSafeInteger(Number(identity.uid)) || String(Number(identity.uid)) !== identity.uid) fail("process identity uid is invalid");
  return structuredClone(identity);
}

// The shared common guardian lease keys. Every operation window (qualification
// or campaign) serializes exactly these shared guardian facts plus its own
// operation-specific authority bindings; the operation/kind and path suffix
// stay distinct so cross-operation substitution always fails closed.
const COMMON_LEASE_KEYS = [
  "schemaVersion", "kind", "operation", "configSha256", "custodyIdentitySha256",
  "guardianModuleSha256", "expectedUnitSha256", "expectedNodePath", "invocationId",
  "unit", "unitSha256", "installedFragmentSha256", "fragmentPath", "controlGroup",
  "activeState", "subState", "mainPid", "bootId", "pid", "startTicks", "exe",
  "argv", "cgroup", "uid", "ready", "readyAt", "createdAt", "refreshedAt",
];

// Descriptor-driven hardened lease engine. Each operation window shares the
// exact same storage/validation/proof primitives; only the serialized
// authority bindings and identity contract differ. No copy/fork of the engine.
const QUALIFICATION_LEASE = Object.freeze({
  kind: "pixel-maintenance-recovery-guardian-lease",
  operation: "pixel-work-model-qualification-maintenance-recovery",
  pathSuffix: "guardian-lease.json",
  label: "guardian lease",
  readyLabel: "guardian",
  authorityKeys: [],
  // Qualification carries no authority, so refresh must never parse an existing
  // lease merely to run a no-op unchanged-authority check. A corrupt/truncated/
  // stale-shape prior lease must not block the guardian from atomically
  // replacing it after all live watcher/systemd/process/storage checks pass
  // (M1 recovery semantics). Campaign windows carry durable immutable identity
  // bindings, so refresh reads and fails closed on the prior lease instead.
  readExistingLeaseOnRefresh: false,
  checkAuthorityOptions() {},
  assertAuthority() {},
  assertUnchangedAuthority() {},
  extractAuthority() { return {}; },
  validateAuthority() {},
});

const CAMPAIGN_LEASE = Object.freeze({
  kind: "pixel-campaign-maintenance-recovery-guardian-lease",
  operation: "pixel-work-model-campaign-maintenance-recovery",
  pathSuffix: "campaign-guardian-lease.json",
  label: "campaign guardian lease",
  readyLabel: "guardian",
  authorityKeys: ["campaignOperationSha256", "journalIdentitySha256"],
  // Campaign refresh keeps the existing-lease authority check mandatory and
  // fail-closed: a prior valid lease is never re-pointed to a different
  // immutable identity, and a malformed/substituted existing lease blocks the
  // write. This is expressed by the descriptor contract (not by label/path).
  readExistingLeaseOnRefresh: true,
  checkAuthorityOptions(options) {
    if (!SHA_RE.test(options.campaignOperationSha256 ?? "") || options.campaignOperationSha256 === ZERO_SHA256) fail("campaign guardian lease requires the exact nonzero reviewed campaign operation hash");
    if (!SHA_RE.test(options.journalIdentitySha256 ?? "") || options.journalIdentitySha256 === ZERO_SHA256) fail("campaign guardian lease requires the exact nonzero immutable campaign journal identity hash");
  },
  assertAuthority(lease, options) {
    if (lease.campaignOperationSha256 !== (options.campaignOperationSha256 ?? "")) fail("campaign guardian lease is bound to a different reviewed campaign operation");
    if (lease.journalIdentitySha256 !== (options.journalIdentitySha256 ?? "")) fail("campaign guardian lease is bound to a different immutable campaign journal identity");
  },
  assertUnchangedAuthority(existing, options) {
    // A campaign lease cannot be silently re-pointed to a different reviewed
    // operation or immutable journal identity after it has been durably bound;
    // refresh refuses to replace the prior valid lease under an identity change.
    if (existing.campaignOperationSha256 !== (options.campaignOperationSha256 ?? "")) fail("campaign guardian lease immutable identity has changed; refusing to replace the prior valid lease");
    if (existing.journalIdentitySha256 !== (options.journalIdentitySha256 ?? "")) fail("campaign guardian lease immutable identity has changed; refusing to replace the prior valid lease");
  },
  extractAuthority(options) {
    return {
      campaignOperationSha256: options.campaignOperationSha256,
      journalIdentitySha256: options.journalIdentitySha256,
    };
  },
  validateAuthority(value) {
    if (!SHA_RE.test(value.campaignOperationSha256 ?? "") || value.campaignOperationSha256 === ZERO_SHA256) fail("campaign guardian lease campaign operation hash is invalid");
    if (!SHA_RE.test(value.journalIdentitySha256 ?? "") || value.journalIdentitySha256 === ZERO_SHA256) fail("campaign guardian lease immutable journal identity hash is invalid");
  },
});

function deriveLeasePath(descriptor, custodyLockPath) {
  validateCustodyLockPath(custodyLockPath);
  return join(dirname(custodyLockPath), `.${basename(custodyLockPath)}.${descriptor.pathSuffix}`);
}

export function deriveGuardianLeasePath(custodyLockPath) { return deriveLeasePath(QUALIFICATION_LEASE, custodyLockPath); }
export function deriveCampaignGuardianLeasePath(custodyLockPath) { return deriveLeasePath(CAMPAIGN_LEASE, custodyLockPath); }

// ---- Secure /proc process-identity reads (Linux) -------------------------
async function readProc(pid, name) {
  return readFile(`/proc/${pid}/${name}`, "utf8");
}

function parseStatStartTicks(statText) {
  const close = statText.lastIndexOf(")");
  if (close < 0) return null;
  const fields = statText.slice(close + 1).trim().split(/\s+/);
  const starttime = Number(fields[19]);
  return Number.isSafeInteger(starttime) && starttime > 0 ? starttime : null;
}

function parseArgv(cmdlineText) {
  const parts = cmdlineText.split("\0");
  if (parts.length && parts[parts.length - 1] === "") parts.pop();
  return parts.filter((entry) => entry.length > 0);
}

export async function deriveProcessIdentity(pid) {
  if (!Number.isSafeInteger(pid) || pid < 1) fail("guardian lease process identity is invalid");
  let bootId;
  try {
    const text = await readFile("/proc/sys/kernel/random/boot_id", "utf8");
    bootId = text.trim();
  } catch { bootId = null; }
  if (!isNonEmptyString(bootId, 128)) fail("guardian lease boot identity cannot be proven; refusing to run without a secure boot identity");
  const [statText, cmdlineText, cgroupText, exePath] = await Promise.all([
    readProc(pid, "stat").catch(() => null),
    readProc(pid, "cmdline").catch(() => null),
    readProc(pid, "cgroup").catch(() => null),
    realpath(`/proc/${pid}/exe`).catch(() => null),
  ]);
  if (statText === null || cmdlineText === null || cgroupText === null || exePath === null) fail("guardian lease process identity could not be proven from /proc; refusing to run");
  const startTicks = parseStatStartTicks(statText);
  if (startTicks === null) fail("guardian lease process start identity could not be proven");
  const argv = parseArgv(cmdlineText);
  if (argv.length === 0) fail("guardian lease process argv could not be proven");
  const cgroup = cgroupText.split("\n").filter((line) => line.length > 0);
  let uid = null;
  try { uid = String((await stat(`/proc/${pid}`)).uid); } catch { fail("guardian lease process owner could not be proven"); }
  return Object.freeze({ bootId, pid, startTicks, exe: exePath, argv, cgroup, uid });
}

export async function readCurrentBootId() {
  try {
    const text = await readFile("/proc/sys/kernel/random/boot_id", "utf8");
    const bootId = text.trim();
    return isNonEmptyString(bootId, 128) ? bootId : null;
  } catch { return null; }
}

export function isProcessAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid < 1) return false;
  try { process.kill(pid, 0); return true; }
  catch (error) { return error?.code === "EPERM"; }
}

function validateSystemdFacts(facts) {
  if (!facts || typeof facts !== "object") fail("guardian lease systemd facts are unavailable");
  if (!INVOCATION_ID_RE.test(facts.invocationId ?? "")) fail("guardian lease requires a nonempty exact systemd invocation id");
  if (facts.activeState !== "active") fail("guardian lease requires ActiveState=active");
  if (facts.subState !== EXPECTED_RUNNING_SUBSTATE) fail(`guardian lease requires the expected running substate ${EXPECTED_RUNNING_SUBSTATE}`);
  if (!Number.isSafeInteger(facts.mainPid) || facts.mainPid < 1) fail("guardian lease requires an exact systemd MainPID");
  if (typeof facts.controlGroup !== "string" || !facts.controlGroup.endsWith(".service") || facts.controlGroup.includes("\0") || /[\r\n]/u.test(facts.controlGroup)) fail("guardian lease requires an exact systemd ControlGroup");
  if (typeof facts.fragmentPath !== "string" || !facts.fragmentPath.startsWith("/") || facts.fragmentPath.includes("\0") || /[\r\n]/u.test(facts.fragmentPath)) fail("guardian lease requires an exact systemd FragmentPath");
  if (!SHA_RE.test(facts.fragmentSha256 ?? "")) fail("guardian lease requires an exact installed fragment sha");
  // A drop-in could override ExecStart even when the fragment bytes are
  // trusted, so the lease publisher must prove the exact empty drop-in set
  // before any lease can be written, refreshed, or live-validated. This is an
  // internal proof fact only; it is never persisted into the lease schema.
  if (!Array.isArray(facts.dropInPaths) || facts.dropInPaths.length !== 0) fail("guardian lease requires the exact empty systemd drop-in set");
  return facts;
}

function validateLeaseValue(descriptor, value) {
  exactKeys(value, [...COMMON_LEASE_KEYS, ...descriptor.authorityKeys], descriptor.label);
  if (value.schemaVersion !== 1 || value.kind !== descriptor.kind || value.operation !== descriptor.operation) fail(`${descriptor.label} kind or operation is invalid`);
  for (const key of ["configSha256", "custodyIdentitySha256", "guardianModuleSha256", "expectedUnitSha256", "unitSha256", "installedFragmentSha256"]) {
    if (!SHA_RE.test(value[key] ?? "")) fail(`${descriptor.label} ${key} is invalid`);
  }
  if (typeof value.expectedNodePath !== "string" || !isAbsolute(value.expectedNodePath) || resolve(value.expectedNodePath) !== value.expectedNodePath || value.expectedNodePath.includes("\0") || /[\r\n]/u.test(value.expectedNodePath)) fail(`${descriptor.label} expected node path is invalid`);
  if (!INVOCATION_ID_RE.test(value.invocationId ?? "")) fail(`${descriptor.label} invocation id is invalid or empty`);
  if (typeof value.unit !== "string" || !UNIT_RE.test(value.unit)) fail(`${descriptor.label} unit contract is invalid`);
  if (value.unitSha256 !== value.installedFragmentSha256) fail(`${descriptor.label} unit sha does not bind the installed fragment bytes`);
  if (value.unitSha256 !== value.expectedUnitSha256) fail(`${descriptor.label} unit sha does not bind the reviewed expected render bytes`);
  if (typeof value.fragmentPath !== "string" || !value.fragmentPath.startsWith("/") || value.fragmentPath.includes("\0") || /[\r\n]/u.test(value.fragmentPath)) fail(`${descriptor.label} fragment path is invalid`);
  if (typeof value.controlGroup !== "string" || !value.controlGroup.endsWith(".service") || value.controlGroup.includes("\0") || /[\r\n]/u.test(value.controlGroup)) fail(`${descriptor.label} control group is invalid`);
  if (value.activeState !== "active" || value.subState !== EXPECTED_RUNNING_SUBSTATE) fail(`${descriptor.label} systemd active/substate is invalid`);
  if (!Number.isSafeInteger(value.mainPid) || value.mainPid < 1) fail(`${descriptor.label} systemd main pid is invalid`);
  validateProcessIdentity({ bootId: value.bootId, pid: value.pid, startTicks: value.startTicks, exe: value.exe, argv: value.argv, cgroup: value.cgroup, uid: value.uid });
  descriptor.validateAuthority(value);
  if (typeof value.ready !== "boolean") fail(`${descriptor.label} readiness is invalid`);
  if (value.readyAt !== null && (!STARTED_RE.test(value.readyAt ?? "") || !Number.isFinite(Date.parse(value.readyAt)))) fail(`${descriptor.label} readiness time is invalid`);
  if (value.ready && value.readyAt === null) fail(`${descriptor.label} ready state lacks a readiness time`);
  if (!value.ready && value.readyAt !== null) fail(`${descriptor.label} non-ready state carries a readiness time`);
  for (const key of ["createdAt", "refreshedAt"]) {
    if (!STARTED_RE.test(value[key] ?? "") || !Number.isFinite(Date.parse(value[key]))) fail(`${descriptor.label} ${key} is invalid`);
  }
  return structuredClone(value);
}

export function validateGuardianLease(value) { return validateLeaseValue(QUALIFICATION_LEASE, value); }
export function validateCampaignGuardianLease(value) { return validateLeaseValue(CAMPAIGN_LEASE, value); }

async function readLeaseFile(descriptor, path, expectedOwnerUid) {
  let actual, record;
  try { [record, actual] = await Promise.all([readBoundedRegularFile(path, MAX_LEASE_BYTES, descriptor.label), realpath(path)]); }
  catch { fail(`${descriptor.label} could not be opened safely`); }
  const info = record.details;
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || !samePath(actual, path) || process.platform !== "win32" && (info.uid !== expectedOwnerUid || (info.mode & 0o077) !== 0)) fail(`${descriptor.label} is not owner-private, singular, and real`);
  const text = record.bytes.toString("utf8");
  if (!Buffer.from(text, "utf8").equals(record.bytes)) fail(`${descriptor.label} is not strict UTF-8`);
  let value;
  try { value = parseStrictJson(text, descriptor.label); } catch { fail(`${descriptor.label} is not strict JSON`); }
  return validateLeaseValue(descriptor, value);
}

function isFresh(lease, now, maxAgeMs) {
  const refreshed = Date.parse(lease.refreshedAt);
  const created = Date.parse(lease.createdAt);
  if (!Number.isFinite(refreshed) || !Number.isFinite(created)) return false;
  if (refreshed < created - CLOCK_TOLERANCE_MS) return false;
  if (refreshed > now + CLOCK_TOLERANCE_MS) return false;
  if (created > now + CLOCK_TOLERANCE_MS) return false;
  return now - refreshed <= maxAgeMs;
}

// Reads the live systemd unit facts. Production uses the real user manager;
// tests may inject a seam so deterministic negative regressions do not require a
// live systemd session. The seam is never supplied by the production path.
async function readSystemdFacts(unit, options) {
  if (options.systemd && typeof options.systemd.facts === "function") return validateSystemdFacts(await options.systemd.facts(unit));
  // The trusted guardian lease path enables the sandbox-root allowance only from
  // its OWN unforgeable /proc/self/uid_map proof (host root unmapped); it never
  // comes from configuration or an external caller. On a normal host this is
  // false, so uid 65534 stays rejected by default.
  const sandboxOptions = {
    ...options,
    allowUnmappedRootForSandbox: options.allowUnmappedRootForSandbox === true ? true : await hostRootIsUnmappedInCurrentNamespace(),
  };
  return validateSystemdFacts(await deriveSystemdUnitFacts(unit, sandboxOptions));
}

async function requireLiveLease(descriptor, custodyLockPath, expectedOwnerUid, configSha256, options = {}) {
  if (!SHA_RE.test(configSha256 ?? "")) fail(`${descriptor.label} requires an exact configuration hash`);
  if (!SHA_RE.test(options.expectedUnitSha256 ?? "")) fail(`${descriptor.label} requires the exact reviewed expected unit render hash`);
  if (typeof options.expectedNodePath !== "string" || !isAbsolute(options.expectedNodePath) || resolve(options.expectedNodePath) !== options.expectedNodePath || options.expectedNodePath.includes("\0") || /[\r\n]/u.test(options.expectedNodePath)) fail(`${descriptor.label} requires the exact expected node real path`);
  descriptor.checkAuthorityOptions(options);
  const maxAgeMs = Number.isSafeInteger(options.maxAgeMs) && options.maxAgeMs >= 1 ? options.maxAgeMs : DEFAULT_GUARDIAN_LEASE_TTL_MS;
  const expectedUnit = options.unit ?? GUARDIAN_UNIT;
  const leasePath = deriveLeasePath(descriptor, custodyLockPath);
  const lease = await readLeaseFile(descriptor, leasePath, expectedOwnerUid);
  if (lease.configSha256 !== configSha256) fail(`${descriptor.label} is bound to a different maintenance configuration`);
  if (lease.custodyIdentitySha256 !== (options.custodyIdentitySha256 ?? "")) fail(`${descriptor.label} custody identity differs from the reviewed operation`);
  if (lease.guardianModuleSha256 !== (options.guardianModuleSha256 ?? "")) fail(`${descriptor.label} is bound to a different guardian code version`);
  if (lease.expectedUnitSha256 !== options.expectedUnitSha256) fail(`${descriptor.label} does not bind the reviewed expected unit render bytes`);
  if (lease.expectedNodePath !== options.expectedNodePath) fail(`${descriptor.label} does not bind the reviewed expected node real path`);
  if (lease.unit !== expectedUnit) fail(`${descriptor.label} does not match the expected supervised unit contract`);
  descriptor.assertAuthority(lease, options);
  // The boot identity must be provable and equal; a readBootId dependency is a
  // test-only seam and is never supplied by the production path.
  const bootId = await (options.readBootId ?? readCurrentBootId)();
  if (bootId === null || lease.bootId !== bootId) fail(`${descriptor.label} boot identity cannot be proven or differs`);
  if (!isProcessAlive(lease.pid)) fail(`${descriptor.label} process is not alive`);
  const current = await deriveProcessIdentity(lease.pid).catch(() => null);
  if (current === null) fail(`${descriptor.label} process identity could not be reread`);
  if (current.startTicks !== lease.startTicks) fail(`${descriptor.label} process start identity does not match the live process`);
  if (current.exe !== lease.exe) fail(`${descriptor.label} executable identity does not match the live process`);
  if (!argvEquals(current.argv, lease.argv)) fail(`${descriptor.label} argv identity does not match the live process`);
  if (current.bootId !== lease.bootId) fail(`${descriptor.label} boot identity differs from the live process`);
  if (current.cgroup.length !== lease.cgroup.length || current.cgroup.some((line, index) => line !== lease.cgroup[index])) fail(`${descriptor.label} cgroup identity does not match the live process`);
  // Exact UID proof: lease UID must equal the live /proc UID and the expected owner UID.
  if (current.uid !== lease.uid || current.uid !== String(expectedOwnerUid)) fail(`${descriptor.label} UID does not match the live process or the expected owner`);
  // The exact Node executable must equal the reviewed expected node real path,
  // the live /proc exe, and argv[0]. No arbitrary absolute argv[0] is accepted.
  if (lease.exe !== options.expectedNodePath) fail(`${descriptor.label} does not bind the exact expected node real path`);
  if (current.exe !== options.expectedNodePath) fail(`${descriptor.label} live executable differs from the reviewed expected node real path`);
  if (lease.argv[0] !== options.expectedNodePath || lease.argv[0] !== lease.exe) fail(`${descriptor.label} argv node path does not equal the live exe and expected node real path`);
  // Exact argv contract (no basename acceptance); nodePath is always exact.
  const contractError = assertExactGuardianArgv(lease.argv, {
    nodePath: options.expectedNodePath,
    guardianModulePath: options.guardianModulePath ?? "",
    configPath: options.configPath ?? "",
  });
  if (contractError) fail(contractError);
  // cgroup must contain the exact expected .service path; no session-scope fallback.
  const cgroupError = assertCgroupContainsUnit(lease.cgroup, expectedUnit);
  if (cgroupError) fail(cgroupError);
  // Live systemd invocation/unit proof for the exact unit.
  const facts = await readSystemdFacts(expectedUnit, { ...options, expectedOwnerUid });
  if (facts.invocationId !== lease.invocationId) fail(`${descriptor.label} invocation id does not match the live systemd unit`);
  const readInvocation = options.readInvocationIdFromProc ?? readInvocationIdFromProc;
  const procInvocation = await readInvocation(lease.pid).catch(() => null);
  if (procInvocation === null || procInvocation !== lease.invocationId) fail(`${descriptor.label} invocation id does not match the live process environment`);
  if (facts.mainPid !== lease.pid || facts.mainPid !== lease.mainPid) fail(`${descriptor.label} MainPID does not match the live systemd unit`);
  if (facts.controlGroup !== lease.controlGroup) fail(`${descriptor.label} ControlGroup does not match the live systemd unit`);
  if (facts.fragmentPath !== lease.fragmentPath) fail(`${descriptor.label} FragmentPath does not match the live systemd unit`);
  if (facts.fragmentSha256 !== lease.expectedUnitSha256 || facts.fragmentSha256 !== lease.installedFragmentSha256 || facts.fragmentSha256 !== lease.unitSha256 || facts.fragmentSha256 !== options.expectedUnitSha256) fail(`${descriptor.label} live unit bytes do not match the reviewed expected render`);
  if (!isFresh(lease, Date.now(), maxAgeMs)) fail(`${descriptor.label} is stale or has incoherent timestamps`);
  if (lease.ready !== true) fail(`${descriptor.readyLabel} is not proven ready; a plain owner process writing a lease is not sufficient`);
  return lease;
}

export async function requireLiveGuardianLease(custodyLockPath, expectedOwnerUid, configSha256, options = {}) {
  return requireLiveLease(QUALIFICATION_LEASE, custodyLockPath, expectedOwnerUid, configSha256, options);
}
export async function requireLiveCampaignGuardianLease(custodyLockPath, expectedOwnerUid, configSha256, options = {}) {
  return requireLiveLease(CAMPAIGN_LEASE, custodyLockPath, expectedOwnerUid, configSha256, options);
}

async function fsyncDirectory(parent) {
  const dir = await open(parent, constants.O_RDONLY);
  try { await dir.sync(); } finally { await dir.close(); }
}

async function refreshLease(descriptor, custodyLockPath, expectedOwnerUid, configSha256, options = {}) {
  if (!SHA_RE.test(configSha256 ?? "")) fail(`${descriptor.label} requires an exact configuration hash`);
  if (!SHA_RE.test(options.custodyIdentitySha256 ?? "")) fail(`${descriptor.label} requires an exact custody identity hash`);
  if (!SHA_RE.test(options.guardianModuleSha256 ?? "")) fail(`${descriptor.label} requires an exact guardian module hash`);
  if (!SHA_RE.test(options.expectedUnitSha256 ?? "")) fail(`${descriptor.label} requires the exact reviewed expected unit render hash`);
  if (typeof options.expectedNodePath !== "string" || !isAbsolute(options.expectedNodePath) || resolve(options.expectedNodePath) !== options.expectedNodePath || options.expectedNodePath.includes("\0") || /[\r\n]/u.test(options.expectedNodePath)) fail(`${descriptor.label} requires the exact expected node real path`);
  descriptor.checkAuthorityOptions(options);
  const unit = options.unit ?? GUARDIAN_UNIT;
  const leasePath = deriveLeasePath(descriptor, custodyLockPath);
  const parent = dirname(leasePath);
  // Verify the lease parent with lstat+realpath as the exact intended real
  // owner-private directory. A substituted parent symlink is rejected by the
  // realpath/identity check, never accepted through a stat-followed path.
  let parentReal, parentInfo;
  try { [parentReal, parentInfo] = await Promise.all([realpath(parent), lstat(parent, { bigint: true })]); }
  catch { fail(`${descriptor.label} parent directory is not accessible`); }
  if (!samePath(parentReal, parent)) fail(`${descriptor.label} parent is a substituted symlink, not the exact intended real directory`);
  if (!parentInfo.isDirectory() || parentInfo.isSymbolicLink()) fail(`${descriptor.label} parent is not a real directory`);
  if (process.platform !== "win32" && (Number(parentInfo.uid) !== expectedOwnerUid || (parentInfo.mode & 0o077n) !== 0n)) fail(`${descriptor.label} parent directory is not owner-private`);
  // An existing lease must not be re-pointed to a different operation authority.
  // The descriptor contract decides whether refresh reads and validates a prior
  // lease. Campaign windows carry durable immutable authority bindings, so the
  // read is mandatory and fail-closed: the prior valid lease is never replaced
  // under an identity change, and any substituted/malformed existing lease
  // blocks the write. Qualification carries no authority, so it must not parse
  // an existing lease merely to run a no-op unchanged-authority check; M1
  // recovery requires atomically replacing a corrupt/truncated/stale-shape
  // prior lease after the live watcher/systemd/process/storage checks pass.
  if (descriptor.readExistingLeaseOnRefresh) {
    let existingInfo = null;
    try { existingInfo = await lstat(leasePath); } catch (error) { if (error && error.code !== "ENOENT") throw error; }
    if (existingInfo) {
      if (!existingInfo.isFile() || existingInfo.isSymbolicLink()) fail(`${descriptor.label} existing lease is not a singular real file`);
      const existing = await readLeaseFile(descriptor, leasePath, expectedOwnerUid);
      descriptor.assertUnchangedAuthority(existing, options);
    }
  }
  // The watcher derives its own live process identity from /proc; tests may not
  // forge these facts through options in the production path.
  const identity = await deriveProcessIdentity(process.pid);
  if (identity.exe !== options.expectedNodePath) fail(`${descriptor.label} process executable does not match the expected node real path`);
  const facts = await readSystemdFacts(unit, { ...options, expectedOwnerUid });
  if (facts.mainPid !== process.pid) fail(`${descriptor.label} systemd MainPID does not match the current process`);
  if (facts.fragmentSha256 !== options.expectedUnitSha256) fail(`${descriptor.label} live unit bytes do not match the reviewed expected render`);
  const readInvocation = options.readInvocationIdFromProc ?? readInvocationIdFromProc;
  const procInvocation = await readInvocation(process.pid).catch(() => null);
  if (procInvocation === null || procInvocation !== facts.invocationId) fail(`${descriptor.label} invocation id is not present in the live process environment`);
  const now = new Date().toISOString();
  const lease = {
    schemaVersion: 1,
    kind: descriptor.kind,
    operation: descriptor.operation,
    configSha256,
    custodyIdentitySha256: options.custodyIdentitySha256,
    guardianModuleSha256: options.guardianModuleSha256,
    expectedUnitSha256: options.expectedUnitSha256,
    expectedNodePath: options.expectedNodePath,
    invocationId: facts.invocationId,
    unit,
    unitSha256: facts.fragmentSha256,
    installedFragmentSha256: facts.fragmentSha256,
    fragmentPath: facts.fragmentPath,
    controlGroup: facts.controlGroup,
    activeState: facts.activeState,
    subState: facts.subState,
    mainPid: facts.mainPid,
    bootId: identity.bootId,
    pid: identity.pid,
    startTicks: identity.startTicks,
    exe: identity.exe,
    argv: identity.argv,
    cgroup: identity.cgroup,
    uid: identity.uid,
    ready: options.ready === true,
    readyAt: options.ready === true ? now : null,
    createdAt: options.createdAt ?? now,
    refreshedAt: now,
    ...descriptor.extractAuthority(options),
  };
  const validated = validateLeaseValue(descriptor, lease);
  // Random collision-resistant temp suffix in addition to PID/start ticks so a
  // stale temp can only fail closed and cannot predictably block a restarted
  // watcher.
  const tempSuffix = typeof options.tempSuffix === "string" && /^[a-f0-9]{32}$/u.test(options.tempSuffix) ? options.tempSuffix : randomBytes(16).toString("hex");
  const temporary = join(parent, `.${basename(leasePath)}.tmp-${process.pid}-${validated.startTicks}-${tempSuffix}`);
  const handle = await open(temporary, "wx", 0o600);
  try { await handle.writeFile(`${JSON.stringify(validated, null, 2)}\n`, "utf8"); await handle.sync(); }
  finally { await handle.close(); }
  await rename(temporary, leasePath);
  await fsyncDirectory(parent);
  const written = await readLeaseFile(descriptor, leasePath, expectedOwnerUid);
  if (canonical(written) !== canonical(validated)) fail(`${descriptor.label} was substituted after write`);
  return Object.freeze({ path: leasePath, lease: written });
}

export async function refreshGuardianLease(custodyLockPath, expectedOwnerUid, configSha256, options = {}) {
  return refreshLease(QUALIFICATION_LEASE, custodyLockPath, expectedOwnerUid, configSha256, options);
}
export async function refreshCampaignGuardianLease(custodyLockPath, expectedOwnerUid, configSha256, options = {}) {
  return refreshLease(CAMPAIGN_LEASE, custodyLockPath, expectedOwnerUid, configSha256, options);
}

async function removeLease(descriptor, custodyLockPath, expectedOwnerUid) {
  const leasePath = deriveLeasePath(descriptor, custodyLockPath);
  // Only a genuinely absent lease (ENOENT) is treated as already-removed; any
  // permission/I/O error fails closed rather than masquerading as absence.
  let info;
  try { info = await stat(leasePath); } catch (error) { if (error && error.code === "ENOENT") return; throw error; }
  if (!info.isFile() || info.isSymbolicLink()) fail(`${descriptor.label} to remove is not a singular real file`);
  const lease = await readLeaseFile(descriptor, leasePath, expectedOwnerUid);
  const current = await deriveProcessIdentity(process.pid).catch(() => null);
  if (current === null || lease.pid !== process.pid || lease.startTicks !== current.startTicks || lease.exe !== current.exe || !argvEquals(lease.argv, current.argv)) fail(`refusing to remove a ${descriptor.label} that does not belong to the current watcher identity`);
  await unlink(leasePath).catch((error) => { if (error?.code !== "ENOENT") throw error; });
  await fsyncDirectory(dirname(leasePath));
}

export async function removeGuardianLease(custodyLockPath, expectedOwnerUid) {
  return removeLease(QUALIFICATION_LEASE, custodyLockPath, expectedOwnerUid);
}
export async function removeCampaignGuardianLease(custodyLockPath, expectedOwnerUid) {
  return removeLease(CAMPAIGN_LEASE, custodyLockPath, expectedOwnerUid);
}

export { WorkMaintenanceRecoverySystemdError };
