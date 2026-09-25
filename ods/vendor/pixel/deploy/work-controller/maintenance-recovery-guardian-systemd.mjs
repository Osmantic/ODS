import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, readFile, realpath } from "node:fs/promises";
import { userInfo } from "node:os";
import { dirname, isAbsolute, resolve } from "node:path";
import { promisify } from "node:util";

const execute = promisify(execFile);

export class WorkMaintenanceRecoverySystemdError extends Error {}

function fail(message) { throw new WorkMaintenanceRecoverySystemdError(message); }
function samePath(left, right) { return process.platform === "win32" ? left.toLowerCase() === right.toLowerCase() : left === right; }
// The hardened guardian sandbox (PrivateTmp) can run under a user namespace in
// which a root-owned system binary/parent is presented with the unmapped-root
// overflow uid (65534 = nobody). Production trusts ONLY uid 0 or the exact
// expected caller as owners; the overflow-root representation is trusted only
// when an explicit test-only allowUnmappedRootForSandbox option is set AND
// /proc/self/uid_map proves the current process is inside a user namespace
// where host root is unmapped. No production caller sets that option.
const UNMAPPED_ROOT_UID = 65534;
export function isTrustedOwner(uid, expectedOwnerUid, sandboxRootAllowed) {
  if (uid === 0 || uid === expectedOwnerUid) return true;
  return Boolean(sandboxRootAllowed) && uid === UNMAPPED_ROOT_UID;
}

// Prove the current process is running inside a user namespace in which host
// root (uid 0) is not mapped, so a root-owned host path legitimately presents
// as the overflow uid (65534 = nobody). This is the unforgeable gate that keeps
// uid 65534 from ever being trusted on a normal (non-sandboxed) host. The
// hardened guardian unit (ProtectSystem=strict/PrivateTmp) runs under exactly
// such a namespace; the DSV4 Codex test sandbox is the same shape.
// A pure parser over the uid_map text; it is exported separately so tests can
// exercise every malformed/empty/zero-length shape without ever mutating
// /proc. A mapping row is syntactically valid only when it carries exactly
// three non-negative integers with a strictly positive length. If no valid
// positive-length row exists, sawValidMapping is false so callers fail closed.
export function parseUidMapRootState(uidMap) {
  let sawValidMapping = false;
  if (typeof uidMap !== "string") return { sawValidMapping: false, rootMapped: false };
  for (const rawLine of uidMap.split("\n")) {
    const parts = rawLine.trim().split(/\s+/u).filter((part) => part.length > 0);
    if (parts.length !== 3) continue;
    const inside = Number(parts[0]);
    const outside = Number(parts[1]);
    const length = Number(parts[2]);
    if (!Number.isSafeInteger(inside) || inside < 0 || !Number.isSafeInteger(outside) || outside < 0 || !Number.isSafeInteger(length) || length <= 0) continue;
    sawValidMapping = true;
    if (outside === 0) return { sawValidMapping: true, rootMapped: true };
  }
  return { sawValidMapping, rootMapped: false };
}

// hostRootIsUnmappedInCurrentNamespace reads /proc/self/uid_map (with an
// injectable test-only reader seam) and fails closed: a missing/unreadable map
// or a map with no syntactically valid positive-length row is never treated as
// provably unmapped.
export async function hostRootIsUnmappedInCurrentNamespace(options = {}) {
  const readUidMap = options.uidMapReader ?? (async () => readFile("/proc/self/uid_map", "utf8"));
  let uidMap;
  try { uidMap = await readUidMap(); }
  catch { return false; }
  const state = parseUidMapRootState(uidMap);
  if (!state.sawValidMapping) return false;
  return !state.rootMapped;
}

// The sandbox-root allowance is honored ONLY when the caller explicitly opts in
// via the test-only allowUnmappedRootForSandbox option AND the current process
// is provably inside a user namespace where host root is unmapped. It defaults
// to false, so uid 65534 is rejected by default. No external production caller
// may set this option; the only production code that enables it is the trusted
// guardian lease path, which does so solely from its own hostRootIsUnmappedIn
// CurrentNamespace() proof (never from configuration or an external caller).
async function unmappedRootSandboxAllowed(options) {
  if (options?.allowUnmappedRootForSandbox !== true) return false;
  return hostRootIsUnmappedInCurrentNamespace();
}
const MAX_FRAGMENT_BYTES = 1024 * 1024;
const SYSTEM_UNIT_DIRS = ["/etc/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system", "/run/systemd/system"];
const SHOW_KEYS = ["ActiveState", "SubState", "MainPID", "InvocationID", "ControlGroup", "FragmentPath", "LoadState", "DropInPaths"];

export function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

export async function readInvocationIdFromProc(pid) {
  if (!Number.isSafeInteger(pid) || pid < 1) fail("invocation id process is invalid");
  let environ;
  try { environ = await readFile(`/proc/${pid}/environ`, "utf8"); }
  catch { return null; }
  for (const part of environ.split("\0")) {
    const idx = part.indexOf("=");
    if (idx > 0 && part.slice(0, idx) === "INVOCATION_ID" && part.length > idx + 1) return part.slice(idx + 1);
  }
  return null;
}

// Resolve one exact, trusted executable (systemctl, systemd-analyze, node) and
// prove it is safe to execute. Production resolves the real absolute path (e.g.
// /usr/bin/systemctl or /usr/bin/node); it never searches PATH. The resolved
// executable must be a singular real regular file owned by root (or the
// expected caller), executable, not group/world-writable, and located under a
// real trusted parent directory that is not group/world-writable. The parent
// chain is validated recursively so a hostile ancestor directory cannot be
// substituted after the leaf is validated.
export async function resolveTrustedExecutable(requested, options = {}) {
  const expectedOwnerUid = options.expectedOwnerUid ?? 0;
  const sandboxRootAllowed = await unmappedRootSandboxAllowed(options);
  if (typeof requested !== "string" || !isAbsolute(requested) || resolve(requested) !== requested || requested.includes("\0") || /[\r\n]/u.test(requested)) fail("trusted executable path is not an exact absolute canonical path");
  let actual, info;
  try { [actual, info] = await Promise.all([realpath(requested), lstat(requested, { bigint: true })]); }
  catch { fail("trusted executable could not be resolved safely to an exact real path"); }
  if (!samePath(actual, requested)) fail("trusted executable must be an exact real path, not a symlink leaf");
  if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1n) fail("trusted executable is not a singular real regular file");
  if (process.platform !== "win32") {
    const uid = Number(info.uid);
    if (!isTrustedOwner(uid, expectedOwnerUid, sandboxRootAllowed)) fail("trusted executable is not owned by root or the expected caller");
    if ((info.mode & 0o111n) === 0n) fail("trusted executable is not executable");
    if ((info.mode & 0o022n) !== 0n) fail("trusted executable must not be group or world writable");
    await assertTrustedParentChain(dirname(actual), expectedOwnerUid, sandboxRootAllowed);
  }
  return actual;
}

// Walk and validate every ancestor of a trusted executable (each real
// directory, exact real path, owned by root or the expected caller, not
// group/world-writable) up to and including the filesystem root. A single
// hostile or substituted ancestor anywhere in the chain makes the executable
// untrusted, so validation is recursive and never trusts an ambient directory.
async function assertTrustedParentChain(parent, expectedOwnerUid, sandboxRootAllowed) {
  let current = parent;
  while (true) {
    let real, info;
    try { [real, info] = await Promise.all([realpath(current), lstat(current, { bigint: true })]); }
    catch { fail("trusted executable parent directory is not trusted"); }
    if (!samePath(real, current)) fail("trusted executable parent directory is a substituted symlink, not the exact intended real directory");
    if (!info.isDirectory() || info.isSymbolicLink()) fail("trusted executable parent directory is not a real directory");
    const uid = Number(info.uid);
    if (!isTrustedOwner(uid, expectedOwnerUid, sandboxRootAllowed)) fail("trusted executable parent directory is not owned by root or the expected caller");
    if ((info.mode & 0o022n) !== 0n) fail("trusted executable parent directory must not be group or world writable");
    if (current === "/") break;
    current = dirname(current);
  }
}

// Resolve one exact, trusted systemctl executable. Production resolves the real
// absolute path /usr/bin/systemctl (or an explicitly reviewed exact override).
export async function resolveTrustedSystemctl(options = {}) {
  return resolveTrustedExecutable(options.systemctlPath ?? "/usr/bin/systemctl", options);
}

// Derive the trusted owner-private user systemd unit directory. Production never
// trusts an ambient XDG_CONFIG_HOME: it derives the real owner home from the
// passwd database via os.userInfo, which is immune to a sandboxed or absent
// HOME. Tests may inject an exact owner-private userSystemdDir override.
function defaultUserSystemdDir(expectedOwnerUid) {
  if (process.platform !== "win32") {
    try {
      const ui = userInfo();
      if (Number(ui.uid) === Number(expectedOwnerUid) && typeof ui.homedir === "string" && isAbsolute(ui.homedir) && ui.homedir !== "/") {
        return `${ui.homedir.replace(/\/+$/u, "")}/.config/systemd/user`;
      }
    } catch {}
  }
  return "";
}

// The exact fragment path policy for the requested unit. For a user unit the
// fragment must be exactly <trusted userSystemdDir>/<unit>; for a system unit it
// must be exactly <one standard unit directory>/<unit>. Sibling, nested, or
// alternate-unit paths are rejected (no prefix/descendant matching).
export function validateFragmentPath(fragmentPath, expectedOwnerUid, unit, options = {}) {
  if (typeof unit !== "string" || !unit.endsWith(".service") || unit.includes("/") || unit.includes("\0") || /[\r\n]/u.test(unit)) fail("systemd unit name is invalid");
  if (typeof fragmentPath !== "string" || !fragmentPath.startsWith("/") || !isAbsolute(fragmentPath) || resolve(fragmentPath) !== fragmentPath || fragmentPath.includes("\0") || /[\r\n]/u.test(fragmentPath)) fail("systemd fragment path is invalid");
  const userUnitDir = (options.userSystemdDir ?? defaultUserSystemdDir(expectedOwnerUid)).replace(/\/+$/u, "");
  if (userUnitDir && samePath(fragmentPath, `${userUnitDir}/${unit}`)) return fragmentPath;
  for (const dir of SYSTEM_UNIT_DIRS) {
    if (samePath(fragmentPath, `${dir}/${unit}`)) return fragmentPath;
  }
  fail("systemd fragment path does not match the exact unit path policy for the requested unit");
  return fragmentPath;
}

// Securely read the installed unit fragment as a singular regular file owned by
// the caller or root, whose real path equals the reported FragmentPath and which
// is not group/world-writable. This uses one authority-bearing file descriptor
// opened with O_RDONLY|O_NOFOLLOW (content is read only from that fd): the exact
// path is securely lstat'ed before and after the read and must name the same
// inode as the fd (dev+ino/type/nlink/mode/uid), the exact parent directory is
// validated (realpath, owner, non-writable mode), and the fd fstat must be
// stable before and after. A read, path, or identity failure is never converted
// to null: it fails closed. __testAfterOpen/__testAfterRead are test-only seams
// (impossible in production defaults) that swap the path to prove fail-closed.
export async function installedFragmentSha256(fragmentPath, expectedOwnerUid, unit, options = {}) {
  validateFragmentPath(fragmentPath, expectedOwnerUid, unit, options);
  const flags = constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0) | (constants.O_CLOEXEC ?? 0) | (constants.O_NONBLOCK ?? 0);
  let handle;
  try { handle = await open(fragmentPath, flags); }
  catch { fail("systemd fragment could not be opened safely"); }
  try {
    const before = await handle.stat({ bigint: true });
    if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1n) fail("systemd fragment is not a singular real regular file");
    if (process.platform !== "win32") {
      const uid = Number(before.uid);
      if (uid !== expectedOwnerUid && uid !== 0) fail("systemd fragment is not owned by the caller or root");
      if ((before.mode & 0o022n) !== 0n) fail("systemd fragment must not be group or world writable");
    }
    const identity = { dev: before.dev, ino: before.ino, size: before.size, nlink: before.nlink, mode: before.mode, uid: before.uid };
    // Test-only seam: swap the path after fd open but before path identity proof.
    if (typeof options.__testAfterOpen === "function") await options.__testAfterOpen(fragmentPath);
    let actual;
    try { actual = await realpath(fragmentPath); }
    catch { fail("systemd fragment real path could not be resolved"); }
    if (!samePath(actual, fragmentPath)) fail("systemd fragment real path differs from the reported fragment path");
    // Prove the exact path still names the same inode the fd was opened on.
    let pathStat;
    try { pathStat = await lstat(fragmentPath, { bigint: true }); }
    catch { fail("systemd fragment path could not be re-lstated"); }
    if (pathStat.dev !== identity.dev || pathStat.ino !== identity.ino || !pathStat.isFile() || pathStat.isSymbolicLink() || pathStat.nlink !== identity.nlink || pathStat.mode !== identity.mode || pathStat.uid !== identity.uid) {
      fail("systemd fragment path no longer names the opened inode");
    }
    // Securely validate the exact parent directory: realpath identity, owner
    // root/expected caller, and non-group/world-writable mode.
    const parent = dirname(fragmentPath);
    let parentReal, parentInfo;
    try { [parentReal, parentInfo] = await Promise.all([realpath(parent), lstat(parent, { bigint: true })]); }
    catch { fail("systemd fragment parent directory is not trusted"); }
    if (!samePath(parentReal, parent)) fail("systemd fragment parent directory is a substituted symlink, not the exact intended real directory");
    if (!parentInfo.isDirectory() || parentInfo.isSymbolicLink()) fail("systemd fragment parent directory is not a real directory");
    if (process.platform !== "win32") {
      const parentUid = Number(parentInfo.uid);
      if (parentUid !== expectedOwnerUid && parentUid !== 0) fail("systemd fragment parent directory is not owned by the caller or root");
      if ((parentInfo.mode & 0o022n) !== 0n) fail("systemd fragment parent directory must not be group or world writable");
    }
    if (before.size > BigInt(MAX_FRAGMENT_BYTES)) fail("systemd fragment exceeds its size limit");
    const chunks = [];
    let total = 0;
    while (total <= MAX_FRAGMENT_BYTES) {
      const buffer = Buffer.allocUnsafe(Math.min(64 * 1024, MAX_FRAGMENT_BYTES + 1 - total));
      const { bytesRead } = await handle.read(buffer, 0, buffer.length, null);
      if (bytesRead === 0) break;
      chunks.push(buffer.subarray(0, bytesRead));
      total += bytesRead;
    }
    if (total > MAX_FRAGMENT_BYTES) fail("systemd fragment exceeded its size limit");
    const after = await handle.stat({ bigint: true });
    if (after.dev !== identity.dev || after.ino !== identity.ino || after.size !== identity.size || after.nlink !== identity.nlink || after.mode !== identity.mode || after.uid !== identity.uid) {
      fail("systemd fragment identity changed during read");
    }
    // Test-only seam: swap the path after the content read to prove the final
    // path re-lstat fails closed.
    if (typeof options.__testAfterRead === "function") await options.__testAfterRead(fragmentPath);
    let pathStatAfter;
    try { pathStatAfter = await lstat(fragmentPath, { bigint: true }); }
    catch { fail("systemd fragment path could not be re-lstated after read"); }
    if (pathStatAfter.dev !== identity.dev || pathStatAfter.ino !== identity.ino || !pathStatAfter.isFile() || pathStatAfter.isSymbolicLink() || pathStatAfter.nlink !== identity.nlink || pathStatAfter.mode !== identity.mode || pathStatAfter.uid !== identity.uid) {
      fail("systemd fragment path changed during read");
    }
    return sha256(Buffer.concat(chunks, total));
  } finally {
    await handle.close();
  }
}

export async function systemdShowUnit(unit, options = {}) {
  if (typeof unit !== "string" || !unit.endsWith(".service") || unit.includes("\0") || /[\r\n]/u.test(unit)) fail("systemd unit name is invalid");
  const systemctlPath = await resolveTrustedSystemctl(options);
  const args = ["--user", "show", unit, `--property=${SHOW_KEYS.join(",")}`];
  let stdout;
  const execOptions = { encoding: "utf8", timeout: options.timeout ?? 20000, maxBuffer: 1024 * 1024 };
  if (options.env) execOptions.env = options.env;
  try { ({ stdout } = await execute(systemctlPath, args, execOptions)); }
  catch (error) {
    if (options.systemctlPath && /ENOENT|EACCES|not found/u.test(String(error?.message ?? ""))) {
      const resolved = await resolveTrustedSystemctl(options);
      fail(`systemctl could not be executed at the exact resolved path ${resolved}`);
    }
    fail("systemctl show failed");
  }
  const facts = {};
  for (const line of stdout.split("\n")) {
    if (line === "") continue;
    const idx = line.indexOf("=");
    if (idx <= 0) fail("systemctl show output has a malformed or unknown line");
    const key = line.slice(0, idx).trim();
    if (!SHOW_KEYS.includes(key)) fail(`systemctl show output contains an unknown property ${key}`);
    if (key === "DropInPaths") {
      // DropInPaths is mandatory and fail-closed. Supported systemd prints the
      // exact empty set as a single "DropInPaths=" line; any drop-in, a
      // duplicate/ambiguous line, a malformed value, or a missing property is
      // rejected before a lease can be written, refreshed, or live-validated.
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

export async function deriveSystemdUnitFacts(unit, options = {}) {
  const show = await systemdShowUnit(unit, options);
  const expectedOwnerUid = options.expectedOwnerUid ?? 0;
  let fragmentSha256 = null;
  if (show.FragmentPath) fragmentSha256 = await installedFragmentSha256(show.FragmentPath, expectedOwnerUid, unit, options);
  const mainPid = /^[1-9][0-9]*$/u.test(show.MainPID ?? "") ? Number(show.MainPID) : 0;
  return Object.freeze({
    unit,
    activeState: show.ActiveState || null,
    subState: show.SubState || null,
    mainPid,
    invocationId: show.InvocationID || null,
    controlGroup: show.ControlGroup || null,
    fragmentPath: show.FragmentPath || null,
    fragmentSha256,
    dropInPaths: [],
  });
}

// Exact real-path argv contract proof: the supervised watcher must be invoked as
// "node <guardianModulePath> watch --config <configPath>" where node, the
// guardian module, and the config are exact absolute real paths (never basenames).
// nodePath is always required so argv[0] must equal the exact expected node real
// path (never any absolute argv[0]).
export function assertExactGuardianArgv(argv, { nodePath, guardianModulePath, configPath }) {
  if (!Array.isArray(argv) || argv.length !== 5) return "argv does not prove the exact guardian watch contract";
  if (typeof argv[0] !== "string" || !argv[0].startsWith("/") || argv[0].includes("\0")) return "argv node path is not an exact absolute real path";
  if (typeof nodePath !== "string" || !nodePath.startsWith("/") || argv[0] !== nodePath) return "argv node path does not match the exact supervised node real path";
  if (typeof guardianModulePath !== "string" || !guardianModulePath.startsWith("/") || argv[1] !== guardianModulePath) return "argv does not bind the exact guardian module path";
  if (argv[2] !== "watch") return "argv does not prove the exact guardian watch command";
  if (argv[3] !== "--config") return "argv does not prove the exact guardian config flag";
  if (typeof configPath !== "string" || !configPath.startsWith("/") || argv[4] !== configPath) return "argv does not bind the exact maintenance configuration path";
  return null;
}

// The cgroup must contain the exact expected .service unit path. There is no
// session-scoped/no-service fallback: a process parked under a plain session
// scope (or an unrelated named service) cannot claim a guardian lease.
export function assertCgroupContainsUnit(cgroup, unit) {
  if (!Array.isArray(cgroup) || cgroup.length === 0) return "cgroup does not prove a supervised user unit identity";
  const needle = unit.replace(/\.service$/u, "");
  let sawService = false;
  for (const line of cgroup) {
    for (const segment of line.split("/")) {
      if (segment.endsWith(".service")) {
        sawService = true;
        if (segment.replace(/\.service$/u, "") === needle) return null;
      }
    }
  }
  if (!sawService) return "cgroup is session-scoped and does not contain the expected .service unit path";
  return "cgroup does not prove the exact supervised user unit identity";
}
