import { execFile } from "node:child_process";
import { createHash, randomBytes } from "node:crypto";
import { constants } from "node:fs";
import { lstat, mkdir, open, realpath, rename, unlink } from "node:fs/promises";
import { userInfo } from "node:os";
import { basename, dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { promisify } from "node:util";

import { readBoundedRegularFile } from "../../scripts/lib/secure-files.mjs";
import { withMaintenanceCustody } from "./maintenance-custody.mjs";
import { deriveGuardianLeasePath, GUARDIAN_UNIT } from "./maintenance-recovery-guardian-lease.mjs";
import { assertNoActiveRecoveryJournal } from "./maintenance-recovery-journal.mjs";
import { resolveTrustedExecutable, resolveTrustedSystemctl } from "./maintenance-recovery-guardian-systemd.mjs";
import { renderGuardianUnit } from "./maintenance-recovery-guardian-unit.mjs";
import {
  productionMaintenancePrimitives,
  validateModelQualificationMaintenanceConfiguration,
} from "./model-qualification-maintenance.mjs";

const execute = promisify(execFile);
const SHA_RE = /^[a-f0-9]{64}$/u;
const MAX_UNIT_BYTES = 1024 * 1024;
const SHOW_KEYS = ["ActiveState", "SubState", "MainPID", "InvocationID", "ControlGroup", "FragmentPath", "LoadState", "DropInPaths"];
const ALLOWED_SECTIONS = Object.freeze(["Unit", "Service", "Install"]);
// Directives that may legitimately repeat within a section (the guardian unit
// emits multiple Environment= bindings). All other directives must be singular.
const REPEATABLE_DIRECTIVES = new Set(["Environment"]);
const ALLOWED_DIRECTIVES = Object.freeze({
  Unit: new Set(["Description", "After", "Wants", "StartLimitIntervalSec", "StartLimitBurst"]),
  Service: new Set(["Type", "ExecStart", "Restart", "RestartSec", "Environment", "NoNewPrivileges", "ProtectSystem", "ProtectHome", "ReadWritePaths", "ProtectKernelTunables", "ProtectControlGroups", "PrivateTmp", "LockPersonality", "RestrictSUIDSGID"]),
  Install: new Set(["WantedBy"]),
});
// A tiny allowlist of benign tunable directives whose VALUE may legitimately
// differ between a reviewed prior Pixel unit and the current render. Everything
// else must match the render byte-for-byte after a closed parse.
const BENIGN_TUNABLES = new Set(["RestartSec"]);

// Fixed production defaults derived from trusted local facts. The production
// CLI never accepts an ambient override for any of these; exported functions
// retain explicit dependency injection only for tests.
const PRODUCTION_NODE_PATH = "/usr/bin/node";
const PRODUCTION_LEASE_WAIT_SECONDS = 30;
const UNIT_NAME_RE = /^[A-Za-z0-9_.-]+\.service$/u;

export class WorkMaintenanceRecoverySupervisorError extends Error {}

function fail(message) { throw new WorkMaintenanceRecoverySupervisorError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}
function canonicalAbs(value, label) {
  if (typeof value !== "string" || !isAbsolute(value) || resolve(value) !== value || value.includes("\0") || /[\r\n]/u.test(value)) fail(`${label} is not an absolute canonical path`);
  return value;
}
function parseMainPid(value) {
  if (value === "0") return 0;
  if (!/^[1-9][0-9]*$/u.test(value ?? "")) fail("systemd MainPID is not a closed integer");
  return Number(value);
}
function fsyncDirectory(directory) {
  if (process.platform === "win32") return Promise.resolve();
  return (async () => {
    let handle;
    try { handle = await open(directory, constants.O_RDONLY | (constants.O_DIRECTORY ?? 0)); await handle.sync(); }
    finally { if (handle) await handle.close(); }
  })();
}
async function lstatExact(path) {
  try { return await lstat(path); } catch (error) { if (error && error.code === "ENOENT") return null; throw error; }
}
function randomHex() { return randomBytes(12).toString("hex"); }
export function parseArgs(argv = []) {
  const out = { mode: null, configPath: null, confirmSha256: null };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (["--review", "--render", "--validate", "--install", "--remove"].includes(arg)) {
      if (out.mode) fail("conflicting supervise operation flags");
      out.mode = arg.slice(2);
    } else if (arg === "--config") {
      out.configPath = argv[++index];
    } else if (arg === "--confirm-install-sha256" || arg === "--confirm-remove-sha256") {
      out.confirmSha256 = argv[++index];
    } else {
      fail(`unexpected argument ${arg}`);
    }
  }
  if (!out.mode) fail("a supervise operation (--review|--render|--validate|--install|--remove) is required");
  if (out.mode === "install" || out.mode === "remove") {
    if (!out.confirmSha256) fail(`${out.mode} requires an exact confirmation SHA256`);
  }
  if (!out.configPath) fail("--config PRIVATE_JSON is required");
  return out;
}

// ---- Secure user unit directory + byte-exact prior-unit contract ----------

function resolveExpectedUserHome(expectedOwnerUid) {
  if (process.platform === "win32") fail("systemd-user supervision is not supported on this platform");
  let ui;
  try { ui = userInfo(); } catch { fail("service user home could not be resolved from the passwd database"); }
  if (Number(ui.uid) !== Number(expectedOwnerUid) || typeof ui.homedir !== "string" || !isAbsolute(ui.homedir) || ui.homedir === "/") {
    fail("service user home could not be resolved to the expected non-root owner");
  }
  return ui.homedir.replace(/\/+$/u, "");
}

function deriveUserSystemdDir(expectedOwnerUid, override) {
  if (override !== null && override !== undefined) {
    // Test-only explicit override; production never sets it.
    return canonicalAbs(override, "systemd user unit directory");
  }
  return `${resolveExpectedUserHome(expectedOwnerUid)}/.config/systemd/user`;
}

// The fixed production unit name cannot carry path separators, traversal,
// instances, or arbitrary names that could escape the expected unit.
export function validateUnitName(name) {
  if (typeof name !== "string" || !UNIT_NAME_RE.test(name) || name.includes("/") || name.includes("\\") || name.includes("\0")) {
    fail("systemd unit name is unsafe");
  }
  return name;
}

// A fixed minimal environment bound to the expected uid and passwd-derived
// home. It carries no ambient SYSTEMD_*, XDG_*, DBUS, HOME, PATH, or loader
// (LD_*) influence; every value is derived from trusted local facts.
export function buildSystemdEnv(expectedOwnerUid) {
  const home = resolveExpectedUserHome(expectedOwnerUid);
  const runtimeDir = `/run/user/${expectedOwnerUid}`;
  return Object.freeze({
    HOME: home,
    PATH: "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    LANG: "C.UTF-8",
    LC_ALL: "C.UTF-8",
    XDG_RUNTIME_DIR: runtimeDir,
    DBUS_SESSION_BUS_ADDRESS: `unix:path=${runtimeDir}/bus`,
    SYSTEMD_COLORS: "0",
    SYSTEMD_PAGER: "cat",
    SYSTEMD_LOG_LEVEL: "warning",
  });
}

// Validate the exact runtime directory and user bus socket before mutating
// production systemd state. A missing, substituted, or non-owned runtime path
// must fail closed rather than route to an attacker-controlled bus.
export async function validateSystemdRuntime(expectedOwnerUid) {
  if (process.platform === "win32") return;
  const runtimeDir = `/run/user/${expectedOwnerUid}`;
  let runtime;
  try { runtime = await lstat(runtimeDir, { bigint: true }); }
  catch { fail("systemd runtime directory is not accessible"); }
  if (!runtime.isDirectory() || runtime.isSymbolicLink()) fail("systemd runtime directory is not a real directory");
  if (Number(runtime.uid) !== expectedOwnerUid) fail("systemd runtime directory is not owned by the expected caller");
  if ((runtime.mode & 0o022n) !== 0n) fail("systemd runtime directory is group/world writable");
  const bus = join(runtimeDir, "bus");
  let socket;
  try { socket = await lstat(bus, { bigint: true }); }
  catch { fail("user bus socket is not accessible"); }
  if (!socket.isSocket()) fail("user bus socket is not a socket");
  if (Number(socket.uid) !== expectedOwnerUid) fail("user bus socket is not owned by the expected caller");
}

async function validateUnitDirectory(userSystemdDir, expectedOwnerUid) {
  let actual, info;
  try { [actual, info] = await Promise.all([realpath(userSystemdDir), lstat(userSystemdDir, { bigint: true })]); }
  catch { fail("systemd user unit directory is not accessible"); }
  if (!samePath(actual, userSystemdDir)) fail("systemd user unit directory is a substituted symlink, not the exact intended real directory");
  if (!info.isDirectory() || info.isSymbolicLink()) fail("systemd user unit directory is not a real directory");
  if (process.platform !== "win32" && (Number(info.uid) !== expectedOwnerUid || (info.mode & 0o022n) !== 0n)) fail("systemd user unit directory is not owned by the caller and free of group/world write");
  return userSystemdDir;
}

async function ensureUnitDir(userSystemdDir, expectedOwnerUid) {
  await mkdir(userSystemdDir, { recursive: true, mode: 0o700 });
  if (process.platform !== "win32") {
    const info = await lstat(userSystemdDir);
    if (!info.isDirectory() || info.isSymbolicLink()) fail("systemd user unit directory is not a real directory after creation");
    if (Number(info.uid) !== expectedOwnerUid) fail("systemd user unit directory is not owned by the caller after creation");
    if ((info.mode & 0o022) !== 0) fail("systemd user unit directory is group/world writable after creation");
  }
  return validateUnitDirectory(userSystemdDir, expectedOwnerUid);
}

// Securely read the installed unit as a singular real owner-private regular
// file, closing the fd/path TOCTOU: the authority-bearing fd identity returned
// by readBoundedRegularFile is compared with a pre-read path lstat and a
// post-read realpath + lstat (device, inode, owner, type, link count, and
// restrictive mode). A substituted path at any point fails closed. The
// __testBeforeOpen/__testAfterOpen seams are test-only (never set in
// production) to prove the swap detection deterministically.
export async function secureReadUnitFile(unitPath, expectedOwnerUid, options = {}) {
  let info;
  try { info = await lstat(unitPath, { bigint: true }); }
  catch (error) { if (error && error.code === "ENOENT") return null; throw error; }
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1n) fail("installed unit is not a singular real regular file");
  if (process.platform !== "win32" && (Number(info.uid) !== expectedOwnerUid || (info.mode & 0o077n) !== 0n)) fail("installed unit is not owner-private, singular, and real");
  if (typeof options.__testBeforeOpen === "function") {
    await options.__testBeforeOpen(unitPath);
    try { info = await lstat(unitPath, { bigint: true }); }
    catch (error) { if (error && error.code === "ENOENT") return null; throw error; }
  }
  const record = await readBoundedRegularFile(unitPath, MAX_UNIT_BYTES, "installed unit");
  const fd = record.details;
  const toBig = (value) => (typeof value === "bigint" ? value : BigInt(value));
  const identity = { dev: toBig(fd.dev), ino: toBig(fd.ino), nlink: toBig(fd.nlink), mode: toBig(fd.mode), uid: toBig(fd.uid) };
  if (info.dev !== identity.dev || info.ino !== identity.ino || info.nlink !== identity.nlink || info.mode !== identity.mode || info.uid !== identity.uid) {
    fail("installed unit path no longer names the opened inode before read");
  }
  if (typeof options.__testAfterOpen === "function") await options.__testAfterOpen(unitPath);
  let actual;
  try { actual = await realpath(unitPath); } catch { fail("installed unit real path could not be resolved"); }
  if (!samePath(actual, unitPath)) fail("installed unit real path differs from the expected unit path");
  let after;
  try { after = await lstat(unitPath, { bigint: true }); }
  catch { fail("installed unit path could not be re-lstated after read"); }
  if (after.dev !== identity.dev || after.ino !== identity.ino || after.nlink !== identity.nlink || after.mode !== identity.mode || after.uid !== identity.uid || !after.isFile() || after.isSymbolicLink()) {
    fail("installed unit path changed during read");
  }
  return record.bytes;
}

// Write the unit atomically and remove the exact temporary file on every
// write/sync/close/rename failure without ever deleting an unrelated path. The
// failAt seam is a test-only injection point used to prove temp cleanup on each
// failure stage; production never sets it.
export async function writeUnitFileAtomically(unitPath, bytes, expectedOwnerUid, options = {}) {
  const directory = dirname(unitPath);
  const temporary = join(directory, `.${basename(unitPath)}.tmp-${process.pid}-${randomHex()}`);
  let created = false;
  let handle;
  try {
    handle = await open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_CLOEXEC ?? 0), 0o600);
    created = true;
    if (options.failAt === "write") throw new Error("injected atomic write failure");
    await handle.writeFile(bytes);
    if (options.failAt === "sync") throw new Error("injected atomic sync failure");
    await handle.sync();
    if (options.failAt === "close") { await handle.close(); handle = undefined; throw new Error("injected atomic close failure"); }
    await handle.close();
    handle = undefined;
    if (options.failAt === "rename") throw new Error("injected atomic rename failure");
    await rename(temporary, unitPath);
  } catch (error) {
    if (handle) { try { await handle.close(); } catch { /* close already failed */ } }
    if (created) { await unlink(temporary).catch((unlinkError) => { if (unlinkError && unlinkError.code !== "ENOENT") throw unlinkError; }); }
    throw error;
  }
  await fsyncDirectory(directory);
  const after = await secureReadUnitFile(unitPath, expectedOwnerUid);
  if (!after || !after.equals(bytes)) fail("installed unit was substituted after atomic write");
  return unitPath;
}

async function durableUnlink(unitPath) {
  await unlink(unitPath).catch((error) => { if (error && error.code !== "ENOENT") throw error; });
  await fsyncDirectory(dirname(unitPath));
}

// ---- Closed exact prior-Pixel-unit parser/validator -----------------------

function parseGuardianUnit(content) {
  const lines = String(content).split("\n");
  const sections = [];
  const seenSection = new Set();
  let current = null;
  for (const raw of lines) {
    if (raw === "") continue;
    if (raw !== raw.trim()) fail("unit line carries leading/trailing whitespace (prefix/suffix trick)");
    if (raw.startsWith("#") || raw.startsWith(";")) fail("unit contains a comment line");
    if (raw.startsWith("[")) {
      const match = /^\[([A-Za-z0-9-]+)\]$/u.exec(raw);
      if (!match) fail("unit section header is malformed");
      const name = match[1];
      if (!ALLOWED_SECTIONS.includes(name)) fail(`unit contains a disallowed section ${name}`);
      if (seenSection.has(name)) fail(`unit contains a duplicate section ${name}`);
      seenSection.add(name);
      current = { name, directives: [] };
      sections.push(current);
      continue;
    }
    if (!current) fail("unit directive appears before any section");
    const eq = raw.indexOf("=");
    if (eq <= 0) fail("unit directive is malformed");
    const key = raw.slice(0, eq);
    const value = raw.slice(eq + 1);
    if (!ALLOWED_DIRECTIVES[current.name]?.has(key)) fail(`unit contains a disallowed directive ${key} in [${current.name}]`);
    if (!REPEATABLE_DIRECTIVES.has(key) && current.directives.some((entry) => entry.key === key)) fail(`unit contains a duplicate directive ${key} in [${current.name}]`);
    current.directives.push({ key, value });
  }
  return { sections };
}

function assertExactGuardianExecStart(value, { nodePath, guardianPath, configPath }) {
  const tokens = String(value).split(/\s+/u);
  if (tokens.length !== 5) fail("ExecStart does not prove the exact guardian watch argv contract");
  if (tokens[0] !== nodePath) fail("ExecStart does not bind the exact supervised node real path");
  if (tokens[1] !== guardianPath) fail("ExecStart does not bind the exact guardian module path");
  if (tokens[2] !== "watch") fail("ExecStart does not prove the exact guardian watch command");
  if (tokens[3] !== "--config") fail("ExecStart does not prove the exact guardian config flag");
  if (tokens[4] !== configPath) fail("ExecStart does not bind the exact maintenance configuration path");
  if (value.includes(" -c ") || value.startsWith("/bin/sh ") || value.startsWith("/bin/bash ")) fail("ExecStart carries a shell interpreter");
}

export function validatePriorGuardianUnit(content, { nodePath, guardianPath, configPath, renderUnit }) {
  if (typeof content !== "string") fail("prior unit content is not textual");
  if (content === renderUnit) return true; // byte equality is the idempotent case
  const prior = parseGuardianUnit(content);
  const render = parseGuardianUnit(renderUnit);
  const priorSections = prior.sections.map((section) => section.name).join(",");
  const renderSections = render.sections.map((section) => section.name).join(",");
  if (priorSections !== renderSections) fail("prior unit sections do not match the exact Pixel guardian contract");
  for (const renderSection of render.sections) {
    const priorSection = prior.sections.find((section) => section.name === renderSection.name);
    const countRender = new Map();
    const countPrior = new Map();
    for (const entry of renderSection.directives) countRender.set(entry.key, (countRender.get(entry.key) || 0) + 1);
    for (const entry of priorSection.directives) countPrior.set(entry.key, (countPrior.get(entry.key) || 0) + 1);
    if (JSON.stringify([...countRender.entries()].sort()) !== JSON.stringify([...countPrior.entries()].sort())) {
      fail(`prior unit [${renderSection.name}] directive cardinality differs from the Pixel guardian contract`);
    }
    const valuesRender = new Map();
    const valuesPrior = new Map();
    for (const entry of renderSection.directives) { valuesRender.set(entry.key, [...(valuesRender.get(entry.key) ?? []), entry.value]); }
    for (const entry of priorSection.directives) { valuesPrior.set(entry.key, [...(valuesPrior.get(entry.key) ?? []), entry.value]); }
    for (const [key, renderValues] of valuesRender) {
      const priorValues = valuesPrior.get(key);
      for (let index = 0; index < renderValues.length; index += 1) {
        const priorValue = priorValues[index];
        if (key === "ExecStart") {
          assertExactGuardianExecStart(priorValue, { nodePath, guardianPath, configPath });
        } else if (key === "ReadWritePaths") {
          if (priorValue !== renderValues[index]) fail("prior unit ReadWritePaths would grant broader writable authority than the render");
        } else if (!BENIGN_TUNABLES.has(key)) {
          if (priorValue !== renderValues[index]) fail(`prior unit ${key} value differs from the Pixel guardian render`);
        } else if (!/^[1-9][0-9]*$/u.test(priorValue)) {
          fail(`prior unit ${key} value is not a benign positive integer`);
        }
      }
    }
  }
  return true;
}

// ---- Confirmation binding -------------------------------------------------

function installConfirmation(descriptor, { unitPath, configSha256, existingBytesSha256, renderSha256 }) {
  return sha256(JSON.stringify({ operation: descriptor.installConfirmOperation, unitPath, configSha256, existingBytesSha256, renderSha256 }));
}
function removeConfirmation(descriptor, { unitPath, configSha256, installedBytesSha256 }) {
  return sha256(JSON.stringify({ operation: descriptor.removeConfirmOperation, unitPath, configSha256, installedBytesSha256 }));
}

// ---- Closed systemd results -----------------------------------------------

async function resolveSystemctl(deps) {
  const systemctlPath = deps.systemctlPath ?? "/usr/bin/systemctl";
  return resolveTrustedSystemctl({ systemctlPath, expectedOwnerUid: deps.expectedOwnerUid });
}
async function runSystemctl(args, deps) {
  const systemctlPath = await resolveSystemctl(deps);
  const execOptions = { encoding: "utf8", timeout: deps.timeout ?? 20000, maxBuffer: 1024 * 1024, env: deps.env };
  try {
    const { stdout, stderr } = await execute(systemctlPath, args, execOptions);
    return { code: 0, stdout: stdout ?? "", stderr: stderr ?? "" };
  } catch (error) {
    const code = typeof error?.code === "number" ? error.code : 1;
    return { code, stdout: error?.stdout ?? "", stderr: error?.stderr ?? "" };
  }
}
function parseSystemdShow(stdout) {
  const facts = {};
  for (const line of String(stdout).split("\n")) {
    if (line === "") continue;
    const idx = line.indexOf("=");
    if (idx <= 0) fail("systemctl show output has a malformed or unknown line");
    const key = line.slice(0, idx).trim();
    if (!SHOW_KEYS.includes(key)) fail(`systemctl show output contains an unknown property ${key}`);
    if (key === "DropInPaths") {
      // DropInPaths is mandatory and fail-closed. Supported systemd prints the
      // exact empty set as a single "DropInPaths=" line; any drop-in (one or
      // more space-separated paths), a duplicate/ambiguous line, a malformed
      // value, or a missing property is rejected before authority can be
      // granted. Absence is never defaulted to an empty set.
      if (Object.prototype.hasOwnProperty.call(facts, "DropInPaths")) fail("systemctl show output contains a duplicate or ambiguous DropInPaths property");
      if (line.slice(idx + 1) !== "") fail("systemd reports a unit drop-in that could override ExecStart; refusing");
      facts.DropInPaths = [];
      continue;
    }
    if (Object.prototype.hasOwnProperty.call(facts, key)) fail(`systemctl show output contains a duplicate property ${key}`);
    facts[key] = line.slice(idx + 1);
  }
  for (const key of SHOW_KEYS) {
    if (!Object.prototype.hasOwnProperty.call(facts, key)) fail(`systemctl show output is missing property ${key}`);
  }
  return facts;
}

// A drop-in can override ExecStart even when the FragmentPath bytes are
// trusted, so any non-empty DropInPaths set fails closed. Both qualification
// and campaign supervisors enforce this at review/install/postflight/remove.
function assertNoUnitDropIns(facts) {
  const paths = facts?.DropInPaths;
  if (Array.isArray(paths) && paths.length > 0) fail("systemd reports a unit drop-in that could override ExecStart; refusing");
  if (typeof paths === "string" && paths !== "") fail("systemd reports a unit drop-in that could override ExecStart; refusing");
}
// Closed systemd show: distinguishes a real absent unit (LoadState=not-found at
// exit 0) from bus/permission/spawn/parse errors (any nonzero exit, malformed
// output, or unknown LoadState). A failed command is never collapsed into an
// absent or present unit.
export async function systemdShowUnit(unit, deps, run = runSystemctl) {
  const result = await run(["--user", "show", unit, `--property=${SHOW_KEYS.join(",")}`], deps);
  if (result.code !== 0) return { ok: false, code: result.code, error: "systemctl show failed", facts: null };
  let facts;
  try { facts = parseSystemdShow(result.stdout); }
  catch (error) { return { ok: false, code: result.code, error: error.message, facts: null }; }
  if (facts.LoadState !== "loaded" && facts.LoadState !== "not-found") {
    return { ok: false, code: result.code, error: `systemctl show reported an unknown LoadState ${facts.LoadState}`, facts: null };
  }
  return { ok: true, code: 0, facts };
}
async function systemctlIsEnabled(unit, deps, run = runSystemctl) {
  const result = await run(["--user", "is-enabled", unit], deps);
  return { code: result.code, state: result.stdout.trim() };
}
// Closed enabled classification. Only the exact observed exit/state pairs are
// accepted: 0/enabled, 1/disabled, and 4/not-found. Any other exit (arbitrary
// error, spawn, or timeout code) is never collapsed into disabled or absent
// even if stdout happens to carry an accepted word.
export function classifyIsEnabled(result) {
  const state = String(result?.state ?? "").trim();
  if (result?.code === 0 && state === "enabled") return "enabled";
  if (result?.code === 1 && state === "disabled") return "disabled";
  if (result?.code === 4 && state === "not-found") return "not-found";
  fail(`systemctl is-enabled returned an unknown or unclosed result (exit ${result?.code}, state ${JSON.stringify(state)})`);
}

// ---- State capture / transactional rollback -------------------------------

async function captureUnitState(ctx) {
  const bytes = await ctx.readUnitFile(ctx.unitPath, ctx.expectedOwnerUid);
  const show = await systemdShowUnit(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
  const isEnabled = await systemctlIsEnabled(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
  const present = bytes !== null;
  if (!show.ok) fail(`prior systemd state could not be captured: ${show.error}`);
  const facts = show.facts;
  // Drop-in closure at the install/remove pre-mutation decision point.
  assertNoUnitDropIns(facts);
  const loadState = facts.LoadState;
  const enabled = classifyIsEnabled(isEnabled);
  const activeState = facts.ActiveState;
  const subState = facts.SubState;
  const mainPid = parseMainPid(facts.MainPID);
  const fragmentPath = facts.FragmentPath;
  if (present) {
    if (loadState !== "loaded") fail("prior unit is present but not in the exact loaded state");
    if (!fragmentPath || !samePath(fragmentPath, ctx.unitPath)) fail("prior unit fragment path does not match the exact unit path");
    if (enabled !== "enabled" && enabled !== "disabled") fail("prior unit enabled state is not closed");
    const activeOk = activeState === "active" && subState === "running" && mainPid > 0;
    const inactiveOk = activeState === "inactive" && subState === "dead" && mainPid === 0;
    if (!activeOk && !inactiveOk) fail("prior unit is in a transitional, failed, or ambiguous state");
  } else {
    if (loadState !== "not-found") fail("absent unit file does not reconcile with a real absent systemd state");
    if (fragmentPath) fail("absent unit still reports a fragment path");
    if (enabled !== "not-found") fail("absent unit does not reconcile with an exact absent is-enabled classification");
    if (activeState !== "inactive" || subState !== "dead" || mainPid !== 0) fail("absent unit does not report the exact inactive/dead/MainPID 0 state");
  }
  return Object.freeze({ present, priorBytes: bytes, priorSha: bytes ? sha256(bytes) : null, loadState, fragmentPath, enabled, activeState, subState, mainPid });
}

// Rollback must prove the exact prior bytes plus the full prior systemd state:
// LoadState, FragmentPath, enabled classification, ActiveState, SubState, and
// MainPID contract. It never checks ActiveState alone.
async function restoreUnitState(ctx, priorState) {
  try {
    if (priorState.present) {
      if (!priorState.priorBytes) return false;
      await ctx.writeUnitFile(ctx.unitPath, priorState.priorBytes, ctx.expectedOwnerUid);
      if ((await ctx.runSystemctl(["--user", "daemon-reload"], ctx.systemdDeps)).code !== 0) return false;
      if (priorState.enabled === "enabled") {
        if ((await ctx.runSystemctl(["--user", "enable", ctx.unitName], ctx.systemdDeps)).code !== 0) return false;
      } else if (priorState.enabled === "disabled") {
        if ((await ctx.runSystemctl(["--user", "disable", ctx.unitName], ctx.systemdDeps)).code !== 0) return false;
      }
      if (priorState.activeState === "active") {
        if ((await ctx.runSystemctl(["--user", "start", ctx.unitName], ctx.systemdDeps)).code !== 0) return false;
      } else if (priorState.activeState === "inactive") {
        if ((await ctx.runSystemctl(["--user", "stop", ctx.unitName], ctx.systemdDeps)).code !== 0) return false;
      }
    } else {
      // The install began from exact absence, so the new unit may have been
      // partially enabled/started. Actively remove residual activation and
      // enablement (best-effort compensation); the closed proof below is the
      // authoritative final gate.
      await ctx.durableUnlink(ctx.unitPath);
      if ((await ctx.runSystemctl(["--user", "daemon-reload"], ctx.systemdDeps)).code !== 0) return false;
      await ctx.runSystemctl(["--user", "disable", ctx.unitName], ctx.systemdDeps);
      await ctx.runSystemctl(["--user", "stop", ctx.unitName], ctx.systemdDeps);
    }
    const bytes = await ctx.readUnitFile(ctx.unitPath, ctx.expectedOwnerUid);
    const bytesMatch = priorState.present ? (bytes !== null && sha256(bytes) === priorState.priorSha) : bytes === null;
    const show = await systemdShowUnit(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
    const isEnabled = await systemctlIsEnabled(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
    if (!show.ok) return false;
    const facts = show.facts;
    // A drop-in introduced during rollback changes effective authority even if
    // the fragment bytes are restored, so rollback must prove the exact empty
    // drop-in set before declaring the prior state restored.
    assertNoUnitDropIns(facts);
    let enabledMatch;
    try { enabledMatch = classifyIsEnabled(isEnabled) === priorState.enabled; }
    catch { enabledMatch = false; }
    const loadStateMatch = facts.LoadState === (priorState.present ? "loaded" : "not-found");
    const fragmentPathMatch = priorState.present
      ? Boolean(facts.FragmentPath && samePath(facts.FragmentPath, ctx.unitPath))
      : !facts.FragmentPath;
    // A real restart legitimately produces a new MainPID, so an active prior
    // is proven by the exact state contract (active/running/MainPID>0) and an
    // inactive prior by (inactive/dead/MainPID=0), never by integer equality.
    let stateMatch = false;
    if (priorState.activeState === "active") {
      stateMatch = facts.ActiveState === "active" && facts.SubState === "running" && parseMainPid(facts.MainPID) > 0;
    } else if (priorState.activeState === "inactive") {
      stateMatch = facts.ActiveState === "inactive" && facts.SubState === "dead" && parseMainPid(facts.MainPID) === 0;
    }
    return bytesMatch && loadStateMatch && fragmentPathMatch && enabledMatch && stateMatch;
  } catch {
    return false;
  }
}

async function leaseExists(leasePath) {
  return (await lstatExact(leasePath)) !== null;
}
function makeWaitForLeaseDisappearance(leasePathFor) {
  return async (ctx) => {
    const leasePath = leasePathFor(ctx.configuration.custody.lockPath);
    const deadline = Date.now() + ctx.leaseWaitSeconds * 1000;
    while (Date.now() < deadline) {
      if (!(await leaseExists(leasePath))) return true;
      await ctx.sleep(ctx.leasePollMs);
    }
    return !(await leaseExists(leasePath));
  };
}
function makeAssertNoGuardianLease(leasePathFor) {
  return async (custodyLockPath) => {
    const leasePath = leasePathFor(custodyLockPath);
    if (await leaseExists(leasePath)) fail("a guardian lease still exists; recovery capability must be retained");
  };
}

// ---- Frozen descriptor seam (shared engine) --------------------------------
// Qualification remains the default wrapper (all existing exports/functions
// unchanged); the campaign supervisor module imports the same engine with its
// own descriptor so the two never fork the supervisor machinery. Every identity
// that must stay distinct (unit name, review operation, confirmation
// discriminators, lease path, journal gate, module, config contract) is carried
// by the descriptor, never by branching inside the engine.
const QUALIFICATION_SUPERVISOR_DESCRIPTOR = Object.freeze({
  label: "maintenance-recovery-guardian-supervise",
  cliLabel: "maintenance-recovery-guardian",
  configLabel: "private qualification maintenance configuration",
  configPathLabel: "maintenance configuration path",
  reviewOperation: "pixel-maintenance-recovery-guardian-supervise-review",
  installConfirmOperation: "install",
  removeConfirmOperation: "remove",
  unitName: GUARDIAN_UNIT,
  defaultGuardianModule: "maintenance-recovery-guardian.mjs",
  leasePathFor: deriveGuardianLeasePath,
  assertNoActiveJournal: assertNoActiveRecoveryJournal,
  defaultRenderUnit: renderGuardianUnit,
  defaultReadConfig: makeDefaultReadConfig({
    configLabel: "private qualification maintenance configuration",
    validateConfig: validateModelQualificationMaintenanceConfiguration,
  }),
  ready: true,
  emitEngineState: false,
});

// ---- Context / config -----------------------------------------------------

function makeDefaultReadConfig(descriptor) {
  return async (configPath, expectedOwnerUid) => {
    const record = await productionMaintenancePrimitives.readPrivateJsonRecord(configPath, descriptor.configLabel, expectedOwnerUid);
    const configuration = descriptor.validateConfig(record.value);
    return { configuration, bytes: record.bytes };
  };
}

async function buildContext(descriptor, options = {}) {
  const expectedOwnerUid = options.expectedOwnerUid ?? (process.geteuid?.() ?? 0);
  if (!Number.isSafeInteger(expectedOwnerUid) || expectedOwnerUid < 1) fail(`${descriptor.label} supervisor requires a non-root service identity`);
  const configPath = canonicalAbs(options.configPath, descriptor.configPathLabel);
  const readConfig = options.readConfig ?? descriptor.defaultReadConfig;
  const configRecord = await readConfig(configPath, expectedOwnerUid);
  const configSha256 = sha256(configRecord.bytes);
  const nodePath = await resolveTrustedExecutable(options.nodePath ?? PRODUCTION_NODE_PATH, { expectedOwnerUid });
  const guardianPath = await realpath(options.guardianPath ?? join(dirname(fileURLToPath(import.meta.url)), descriptor.defaultGuardianModule));
  const userSystemdDir = deriveUserSystemdDir(expectedOwnerUid, options.userSystemdDir);
  const unitName = validateUnitName(options.unitName ?? descriptor.unitName);
  const unitPath = join(userSystemdDir, unitName);
  const renderUnit = options.renderUnit ?? descriptor.defaultRenderUnit;
  const render = await renderUnit({ configPath, nodePath, guardianPath, expectedOwnerUid });
  const renderSha256 = render.renderSha256;
  const systemdDeps = {
    systemctlPath: options.systemctlPath ?? null,
    expectedOwnerUid,
    env: options.systemctlEnv ?? buildSystemdEnv(expectedOwnerUid),
    timeout: options.timeout ?? 20000,
  };
  return Object.freeze({
    descriptor,
    expectedOwnerUid,
    configPath,
    configSha256,
    configuration: configRecord.configuration,
    nodePath,
    guardianPath,
    userSystemdDir,
    unitName,
    unitPath,
    render,
    renderSha256,
    systemdDeps,
    leaseWaitSeconds: options.leaseWaitSeconds ?? PRODUCTION_LEASE_WAIT_SECONDS,
    leasePollMs: options.leasePollMs ?? 250,
    sleep: options.sleep ?? ((ms) => new Promise((resolveSleep) => setTimeout(resolveSleep, ms))),
    readUnitFile: options.readUnitFile ?? secureReadUnitFile,
    validateUnitDirectory: options.validateUnitDirectory ?? validateUnitDirectory,
    ensureUnitDir: options.ensureUnitDir ?? ensureUnitDir,
    validateSystemdRuntime: options.validateSystemdRuntime ?? validateSystemdRuntime,
    writeUnitFile: options.writeUnitFile ?? writeUnitFileAtomically,
    durableUnlink: options.durableUnlink ?? durableUnlink,
    runSystemctl: options.runSystemctl ?? runSystemctl,
    assertNoActiveRecoveryJournal: options.assertNoActiveRecoveryJournal ?? descriptor.assertNoActiveJournal,
    assertNoGuardianLease: options.assertNoGuardianLease ?? makeAssertNoGuardianLease(descriptor.leasePathFor),
    waitForLeaseDisappearance: options.waitForLeaseDisappearance ?? makeWaitForLeaseDisappearance(descriptor.leasePathFor),
    renderUnit,
    readConfig,
  });
}

export async function buildSupervisorContext(descriptor, options = {}) {
  return buildContext(descriptor, options);
}

async function assertNoUnitDropInsForUnit(ctx) {
  const show = await systemdShowUnit(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
  if (!show.ok) fail(`prior systemd state could not be captured for drop-in closure: ${show.error}`);
  assertNoUnitDropIns(show.facts);
}

// ---- Operations -----------------------------------------------------------

async function rereadConfigUnderLock(ctx) {
  const underLock = await ctx.readConfig(ctx.configPath, ctx.expectedOwnerUid);
  if (sha256(underLock.bytes) !== ctx.configSha256) fail("configuration changed after custody acquisition");
  return underLock;
}

export async function superviseReviewWith(descriptor, options = {}) {
  const ctx = await buildContext(descriptor, options);
  const existing = await ctx.readUnitFile(ctx.unitPath, ctx.expectedOwnerUid);
  const existingBytesSha256 = existing ? sha256(existing) : "absent";
  // Drop-in closure at the review decision point: a drop-in could override
  // ExecStart even when the trusted FragmentPath bytes are intact.
  await assertNoUnitDropInsForUnit(ctx);
  const includeEngine = descriptor.emitEngineState === true;
  const installConfirm = includeEngine && descriptor.ready !== true
    ? null
    : installConfirmation(descriptor, { unitPath: ctx.unitPath, configSha256: ctx.configSha256, existingBytesSha256, renderSha256: ctx.renderSha256 });
  const removeConfirm = removeConfirmation(descriptor, { unitPath: ctx.unitPath, configSha256: ctx.configSha256, installedBytesSha256: existingBytesSha256 });
  return Object.freeze({
    schemaVersion: 1,
    operation: descriptor.reviewOperation,
    ...(includeEngine ? { engineReady: descriptor.ready, installAvailable: descriptor.ready } : {}),
    unitPath: ctx.unitPath,
    configSha256: ctx.configSha256,
    renderSha256: ctx.renderSha256,
    existingBytesSha256: existing ? sha256(existing) : null,
    installConfirmSha256: installConfirm,
    removeConfirmSha256: removeConfirm,
    confirm: {
      install: installConfirm ? { option: "--confirm-install-sha256", sha256: installConfirm } : null,
      remove: removeConfirm ? { option: "--confirm-remove-sha256", sha256: removeConfirm } : null,
    },
  });
}

export async function superviseReview(options = {}) {
  return superviseReviewWith(QUALIFICATION_SUPERVISOR_DESCRIPTOR, options);
}


function assertReplaceablePriorUnit(ctx, bytes) {
  try {
    validatePriorGuardianUnit(bytes.toString("utf8"), { nodePath: ctx.nodePath, guardianPath: ctx.guardianPath, configPath: ctx.configPath, renderUnit: ctx.render.unit });
  } catch {
    fail("existing unit is not a valid Pixel-owned prior unit; refuse to replace unrelated or malformed content");
  }
}

async function rollbackAndThrow(ctx, priorState, cause, operation) {
  let restoreError = null;
  let proven = false;
  try { proven = await restoreUnitState(ctx, priorState); }
  catch (error) { restoreError = error; }
  if (!proven) {
    throw new WorkMaintenanceRecoverySupervisorError(
      `${operation} failed and rollback is incomplete; manual attention required and recovery capability is not proven retained. cause: ${cause.message}${restoreError ? `; rollback: ${restoreError.message}` : ""}`,
    );
  }
  throw new WorkMaintenanceRecoverySupervisorError(`${operation} failed after mutation; exact prior unit and full prior state were restored. cause: ${cause.message}`);
}

export async function superviseInstallWith(descriptor, options = {}) {
  // Fail-closed readiness gate: while the campaign engine is not ready this
  // returns before custody acquisition, journal/lease access, unit-directory
  // creation, systemctl, or any filesystem mutation.
  if (descriptor.ready !== true) fail(`${descriptor.label}: recovery engine is not ready; refusing to install`);
  const ctx = await buildContext(descriptor, options);
  const existing = await ctx.readUnitFile(ctx.unitPath, ctx.expectedOwnerUid);
  const existingBytesSha256 = existing ? sha256(existing) : "absent";
  const expectedConfirm = installConfirmation(descriptor, { unitPath: ctx.unitPath, configSha256: ctx.configSha256, existingBytesSha256, renderSha256: ctx.renderSha256 });
  if (typeof options.confirmInstallSha256 !== "string" || options.confirmInstallSha256 !== expectedConfirm) fail("install SHA mismatch; refuse to install");
  if (existing && existingBytesSha256 !== ctx.renderSha256) {
    assertReplaceablePriorUnit(ctx, existing);
  }
  const withCustody = options.withCustody ?? withMaintenanceCustody;
  return withCustody(ctx.configuration.custody, ctx.expectedOwnerUid, async () => {
    await rereadConfigUnderLock(ctx);
    // Unit-directory creation and validation happen under maintenance custody so
    // a pre-lock directory check cannot authorize a later write.
    await ctx.ensureUnitDir(ctx.userSystemdDir, ctx.expectedOwnerUid);
    const reRender = await ctx.renderUnit({ configPath: ctx.configPath, nodePath: ctx.nodePath, guardianPath: ctx.guardianPath, expectedOwnerUid: ctx.expectedOwnerUid });
    if (reRender.renderSha256 !== ctx.renderSha256) fail("unit render changed after custody acquisition");
    const existingUnderLock = await ctx.readUnitFile(ctx.unitPath, ctx.expectedOwnerUid);
    const existingUnderLockSha = existingUnderLock ? sha256(existingUnderLock) : "absent";
    if (existingUnderLockSha !== existingBytesSha256) fail("installed unit changed after custody acquisition");
    if (existingUnderLock && existingUnderLockSha !== ctx.renderSha256) {
      assertReplaceablePriorUnit(ctx, existingUnderLock);
    }
    await ctx.assertNoActiveRecoveryJournal(ctx.configuration.custody.lockPath, ctx.expectedOwnerUid);
    await ctx.validateSystemdRuntime(ctx.expectedOwnerUid);
    const priorState = await captureUnitState(ctx);
    let mutated = false;
    try {
      // Revalidate the exact directory immediately before mutation.
      await ctx.validateUnitDirectory(ctx.userSystemdDir, ctx.expectedOwnerUid);
      // Mark the transaction as potentially mutated before entering the atomic
      // writer: an atomic writer can rename successfully and then throw on
      // directory fsync/post-write verification, so a throw does not prove the
      // destination was left untouched. Treat any writer throw as mutated and
      // roll back exactly.
      mutated = true;
      await ctx.writeUnitFile(ctx.unitPath, Buffer.from(ctx.render.unit, "utf8"), ctx.expectedOwnerUid);
      // Revalidate the exact directory immediately after mutation.
      await ctx.validateUnitDirectory(ctx.userSystemdDir, ctx.expectedOwnerUid);
      if ((await ctx.runSystemctl(["--user", "daemon-reload"], ctx.systemdDeps)).code !== 0) fail("daemon-reload failed during install");
      if ((await ctx.runSystemctl(["--user", "enable", ctx.unitName], ctx.systemdDeps)).code !== 0) fail("enable failed during install");
      if ((await ctx.runSystemctl(["--user", "start", ctx.unitName], ctx.systemdDeps)).code !== 0) fail("start failed during install");
      const show = await systemdShowUnit(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
      if (!show.ok) fail("installed unit could not be shown after start");
      assertNoUnitDropIns(show.facts);
      if (show.facts.LoadState !== "loaded") fail("installed unit is not loaded after start");
      if (show.facts.ActiveState !== "active" || show.facts.SubState !== "running" || parseMainPid(show.facts.MainPID) <= 0) fail("installed unit is not active/running with MainPID > 0");
      if (!show.facts.FragmentPath || !samePath(show.facts.FragmentPath, ctx.unitPath)) fail("installed fragment path does not match the exact unit path");
      const installedBytes = await ctx.readUnitFile(ctx.unitPath, ctx.expectedOwnerUid);
      if (!installedBytes || sha256(installedBytes) !== ctx.renderSha256) fail("installed fragment bytes do not match the exact render");
      const isEnabled = await systemctlIsEnabled(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
      if (classifyIsEnabled(isEnabled) !== "enabled") fail("installed unit is not enabled");
      return Object.freeze({ ok: true, unitPath: ctx.unitPath, renderSha256: ctx.renderSha256, existingWasPresent: existing !== null });
    } catch (error) {
      if (!mutated) throw error;
      await rollbackAndThrow(ctx, priorState, error, "install");
      throw error;
    }
  });
}

export async function superviseInstall(options = {}) {
  return superviseInstallWith(QUALIFICATION_SUPERVISOR_DESCRIPTOR, options);
}

export async function superviseRemoveWith(descriptor, options = {}) {
  const ctx = await buildContext(descriptor, options);
  const withCustody = options.withCustody ?? withMaintenanceCustody;
  return withCustody(ctx.configuration.custody, ctx.expectedOwnerUid, async () => {
    await rereadConfigUnderLock(ctx);
    // Removal validates the exact directory/path under custody before mutation.
    await ctx.validateUnitDirectory(ctx.userSystemdDir, ctx.expectedOwnerUid);
    const existing = await ctx.readUnitFile(ctx.unitPath, ctx.expectedOwnerUid);
    const installedSha = existing ? sha256(existing) : "absent";
    // Removal is bound to a confirmation over the exact present or absent state
    // (installedBytesSha256 of "absent" for a no-op), so an absent no-op still
    // requires the review-provided confirmation.
    const expectedConfirm = removeConfirmation(descriptor, { unitPath: ctx.unitPath, configSha256: ctx.configSha256, installedBytesSha256: installedSha });
    if (typeof options.confirmRemoveSha256 !== "string" || options.confirmRemoveSha256 !== expectedConfirm) {
      fail("remove SHA mismatch; refuse to remove");
    }
    await ctx.assertNoActiveRecoveryJournal(ctx.configuration.custody.lockPath, ctx.expectedOwnerUid);
    // The trusted runtime is validated and the exact present/absent systemd
    // state is captured and reconciled under custody before any outcome.
    await ctx.validateSystemdRuntime(ctx.expectedOwnerUid);
    const priorState = await captureUnitState(ctx);
    if (!existing) {
      // captureUnitState already reconciled the exact absent state
      // (LoadState=not-found, empty FragmentPath, inactive/dead/MainPID0, and
      // exact 4/not-found is-enabled); only then is the no-op reported.
      await ctx.assertNoGuardianLease(ctx.configuration.custody.lockPath);
      return Object.freeze({ ok: true, removed: false, unitPath: ctx.unitPath });
    }
    if (installedSha !== ctx.renderSha256) {
      assertReplaceablePriorUnit(ctx, existing);
    }
    let mutationStarted = false;
    let mutated = false;
    try {
      mutationStarted = true;
      if ((await ctx.runSystemctl(["--user", "disable", ctx.unitName], ctx.systemdDeps)).code !== 0) fail("disable failed during remove");
      if ((await ctx.runSystemctl(["--user", "stop", ctx.unitName], ctx.systemdDeps)).code !== 0) fail("stop failed during remove");
      if (!(await ctx.waitForLeaseDisappearance(ctx))) fail("guardian lease did not disappear after stop; retaining the unit for manual attention");
      const show = await systemdShowUnit(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
      if (!show.ok) fail("guardian service state could not be verified after stop; retaining the unit for manual attention");
      assertNoUnitDropIns(show.facts);
      if (show.facts.ActiveState !== "inactive" || show.facts.SubState !== "dead" || parseMainPid(show.facts.MainPID) !== 0) {
        fail("guardian service is not fully inactive after stop; retaining the unit for manual attention");
      }
      await ctx.durableUnlink(ctx.unitPath);
      mutated = true;
      if ((await ctx.runSystemctl(["--user", "daemon-reload"], ctx.systemdDeps)).code !== 0) fail("daemon-reload failed during remove");
      const postBytes = await ctx.readUnitFile(ctx.unitPath, ctx.expectedOwnerUid);
      if (postBytes) fail("unit file still present after removal");
      const postShow = await systemdShowUnit(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
      if (!postShow.ok) fail("removed unit could not be shown after removal");
      assertNoUnitDropIns(postShow.facts);
      if (postShow.facts.LoadState !== "not-found") fail("removed unit is not absent after removal");
      if (postShow.facts.FragmentPath) fail("removed unit still reports a fragment path");
      if (postShow.facts.ActiveState !== "inactive" || postShow.facts.SubState !== "dead" || parseMainPid(postShow.facts.MainPID) !== 0) {
        fail("removed unit is not inactive/dead with MainPID 0");
      }
      const postEnable = await systemctlIsEnabled(ctx.unitName, ctx.systemdDeps, ctx.runSystemctl);
      if (classifyIsEnabled(postEnable) !== "not-found") fail("postflight is-enabled is not the exact absent/not-found result");
      await ctx.assertNoGuardianLease(ctx.configuration.custody.lockPath);
      return Object.freeze({ ok: true, removed: true, unitPath: ctx.unitPath });
    } catch (error) {
      if (mutationStarted || mutated) await rollbackAndThrow(ctx, priorState, error, "remove");
      throw error;
    }
  });
}

export async function superviseRemove(options = {}) {
  return superviseRemoveWith(QUALIFICATION_SUPERVISOR_DESCRIPTOR, options);
}

// ---- CLI ----------------------------------------------------------------

export async function supervisorCli(descriptor, argv = process.argv.slice(2)) {
  try {
    // parseArgs lives inside the CLI error boundary so a rejected legacy flag
    // fails cleanly with a guard message and nonzero exit, never an unhandled
    // stack trace.
    const parsed = parseArgs(argv);
    if (parsed.mode === "render") {
      const ctx = await buildContext(descriptor, { configPath: parsed.configPath });
      process.stdout.write(ctx.render.unit);
      return 0;
    }
    if (parsed.mode === "review") {
      const review = await superviseReviewWith(descriptor, { configPath: parsed.configPath });
      process.stdout.write(`${JSON.stringify(review, null, 2)}\n`);
      return 0;
    }
    if (parsed.mode === "validate") {
      const ctx = await buildContext(descriptor, { configPath: parsed.configPath });
      await ctx.validateUnitDirectory(ctx.userSystemdDir, ctx.expectedOwnerUid);
      await ctx.validateSystemdRuntime(ctx.expectedOwnerUid);
      const temporary = join(dirname(ctx.unitPath), `.${basename(ctx.unitPath)}.analyze-${process.pid}-${randomHex()}`);
      await writeUnitFileAtomically(temporary, Buffer.from(ctx.render.unit, "utf8"), ctx.expectedOwnerUid);
      try {
        const { code, stderr } = await runSystemctl(["verify", temporary], { ...ctx.systemdDeps, systemctlPath: "/usr/bin/systemd-analyze" });
        if (code !== 0) fail(`systemd-analyze rejected the concrete unit: ${stderr}`);
      } finally {
        await durableUnlink(temporary);
      }
      process.stderr.write(`${descriptor.cliLabel}: systemd-analyze accepted the concrete unit\n`);
      return 0;
    }
    if (parsed.mode === "install") {
      await superviseInstallWith(descriptor, { configPath: parsed.configPath, confirmInstallSha256: parsed.confirmSha256 });
      process.stderr.write(`${descriptor.cliLabel}: installed and started the recovery guardian\n`);
      return 0;
    }
    if (parsed.mode === "remove") {
      await superviseRemoveWith(descriptor, { configPath: parsed.configPath, confirmRemoveSha256: parsed.confirmSha256 });
      process.stderr.write(`${descriptor.cliLabel}: removal completed\n`);
      return 0;
    }
    return 2;
  } catch (error) {
    process.stderr.write(`${descriptor.label}: ${error instanceof WorkMaintenanceRecoverySupervisorError ? error.message : `unexpected failure: ${error.message}`}\n`);
    return 1;
  }
}

export async function main(argv = process.argv.slice(2)) {
  return supervisorCli(QUALIFICATION_SUPERVISOR_DESCRIPTOR, argv);
}


if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  main().then((code) => { process.exitCode = code; });
}
