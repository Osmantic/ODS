#!/usr/bin/python3
"""Thin fixed-verb Pixel bundle/reboot operator (root-only managed helper).

This module wraps the repository's already-tested goal, fleet-goal, and deep-work-soak
lifecycle CLIs instead of reimplementing their lifecycle semantics in Python. The Node
lifecycle CLI is the semantic authority for bundle schema, live binding, exact installed
bytes, daemon-reload, activation, removal, and compensation. This module only:

  * validates a root-owned exact-key config (owner UID, pixel-work UID/GID, owner-private
    inbox roots, pixel-work-owned stable bundle roots, exact root-owned runtime path +
    tree manifest SHA, fixed Node/systemctl/systemd-analyze paths, receipt root, and
    disabled optional readers);
  * securely, boundedly stages a regular-file-only candidate bundle from the owner-private
    inbox to the pixel-work-owned stable bundle root, verifying every file identity before
    and after copy with short-write-safe loops and fail-closed no-follow cleanup;
  * invokes the fixed, kind-specific, root-owned Node lifecycle CLI with an argv array
    (never a shell) for install/inspect/activate/remove and a real install-then-switch
    rollback with exact compensation;
  * reports content-free service status evidence closed over unit names derived from a
    verified stable manifest, distinguishing expected inactive/disabled states from
    execution errors (never raw journal lines or environment); and
  * implements a two-phase reboot protocol (prepare/execute/status/reconcile) with atomic
    durable state transitions, current-boot equality on execute, exact kind/unit/evidence
    binding, safe expired-grant handling, and a one-use reconciliation archive.

Every operation writes a content-free, root-owned, fsynced, hash-addressed receipt with
exact operation inputs and outcome. Reboot tests never invoke a real reboot: the fixed
reboot binary is always /usr/bin/systemctl and is only reachable through a testing
override.
"""

from __future__ import annotations

import fcntl
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from pixel_release_grammar import KINDS, SHA256

TESTING = os.environ.get("PIXEL_RELEASE_MANAGED_TESTING") == "1" and getattr(os, "geteuid", lambda: 1)() != 0
# Dedicated config; the existing gateway/courier operator keeps its own config untouched.
MAX_CONFIG = 1024 * 1024
MAX_UNIT_BYTES = 256 * 1024
MAX_BUNDLE_FILES = 64
MAX_BUNDLE_TOTAL = 4 * 1024 * 1024
MAX_OUTPUT = 128 * 1024
REBOOT_GRANT_TTL_SECONDS = 15 * 60
REBOOT_EVIDENCE_TTL_SECONDS = 15 * 60
ARCHIVE_MAX_KEEP = 8
OPERATOR_IDENTITY = "pixel-release-operator"

BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"
REBOOT_KINDS = ("goal", "deep-work-soak")
UNIT_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_.-]*\.(service|timer|path)")
_GRANT_RE = re.compile(r"[a-f0-9]{64}")


def reboot_bin():
    return os.environ.get("PIXEL_OPERATOR_REBOOT", "/usr/bin/systemctl") if TESTING else "/usr/bin/systemctl"


def systemctl_bin():
    return os.environ.get("PIXEL_RELEASE_MANAGED_SYSTEMCTL", "/usr/bin/systemctl") if TESTING else "/usr/bin/systemctl"


def runtime_parent_dir():
    if TESTING:
        return Path(os.environ.get("PIXEL_OPERATOR_RUNTIME_PARENT", "/opt/pixel-release-runtime"))
    return Path("/opt/pixel-release-runtime")


def operator_config_dir():
    if TESTING:
        return Path(os.environ.get("PIXEL_OPERATOR_CONFIG_DIR", "/etc/pixel-release-operator"))
    return Path("/etc/pixel-release-operator")


def operator_libexec_dir():
    if TESTING:
        return Path(os.environ.get("PIXEL_OPERATOR_LIBEXEC", "/usr/local/libexec"))
    return Path("/usr/local/libexec")


# Fixed kind -> lifecycle CLI module (relative to the runtime path). Only these exact
# reviewed CLIs may be invoked; no arbitrary module or shell is ever run. The goal kind
# uses the lifecycle CLI (not the render-only goal-service-cli.mjs).
KIND_CLI = {
    "goal": "deploy/work-controller/goal-service-lifecycle-cli.mjs",
    "fleet-goal": "deploy/work-controller/goal-fleet-service-lifecycle-cli.mjs",
    "deep-work-soak": "deploy/work-controller/deep-work-soak-service-cli.mjs",
}

# The bundle manifest filename is kind-specific: fleet-goal bundles use the fleet manifest.
MANIFEST_FILENAME = {
    "goal": "service-bundle.json",
    "fleet-goal": "fleet-service-bundle.json",
    "deep-work-soak": "service-bundle.json",
}

# Root-custodied broker-byte authority. Callers select only one of four fixed verbs;
# every byte, destination, service, auxiliary unit, mode, and legacy compatibility
# record is closed over this reviewed contract plus the root-owned operator config.
BROKER_LIMBS = ("source", "ops", "frontier")
BROKER_UNITS = {
    "source": "pixel-source-broker.service",
    "ops": "pixel-ops-broker.service",
    "frontier": "pixel-frontier-broker.service",
}
BROKER_SOURCE_TIMER = "pixel-source-broker.timer"
BROKER_SOURCE_DIRECT_PATH = "pixel-source-direct.path"
BROKER_FILES = {
    "source": (
        ("broker.py", 0o755, "deploy/source-broker/broker.py", True),
        # Backup-only compatibility record. Older releases installed this package;
        # current releases bundle it into broker.py and commonly record it absent.
        ("action_journal/__init__.py", 0o644, None, False),
    ),
    "ops": (("broker.py", 0o755, "deploy/ops-broker/broker.py", True),),
    "frontier": (
        ("broker.py", 0o755, "deploy/frontier-broker/broker.py", True),
        ("verify-codex.py", 0o755, "scripts/verify-frontier-codex.py", True),
    ),
}
BROKER_STATE_KIND = "pixel-broker-bytes-state"
BROKER_SNAPSHOT_KIND = "pixel-broker-bytes-snapshot"
BROKER_PHASES = ("backed-up", "mutation-started", "installed", "restored")

CONFIG_KEYS = frozenset({
    "schemaVersion", "ownerUid", "pixelWorkUid", "pixelWorkGid", "inboxRoot", "bundleRoot",
    "runtimePath", "runtimeTreeSha256", "nodePath", "systemctlPath", "systemdAnalyzePath",
    "receiptRoot", "rebootRoot", "brokerBytes", "readersEnabled",
})


class PixelOperatorError(RuntimeError):
    pass


def fail(message):
    raise PixelOperatorError(message)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_identity(info) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def secure_read(path: Path, label: str, limit: int, *, allow_missing: bool = False):
    """Read a bounded regular file without following symlinks, fail-closed on change."""
    try:
        raw = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        fail(f"{label} is missing")
    if stat.S_ISLNK(raw.st_mode):
        fail(f"{label} must not be a symlink")
    if not stat.S_ISREG(raw.st_mode):
        fail(f"{label} must be a regular file")
    if raw.st_size > limit:
        fail(f"{label} exceeds its size limit")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > limit or _stable_identity(opened) != _stable_identity(raw):
            fail(f"{label} changed during secure open")
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                fail(f"{label} exceeds its size limit")
        payload = b"".join(chunks)
        final_fd = os.fstat(fd)
        final_path = path.lstat()
        if _stable_identity(final_fd) != _stable_identity(opened) or _stable_identity(final_path) != _stable_identity(raw):
            fail(f"{label} changed during secure open")
        return payload, opened
    finally:
        os.close(fd)


# ---- Per-boundary ownership/mode policies -------------------------------------
# Each boundary names an explicit expected owner (UID, optional GID) and a private bit:
#   * "inbox"        ownerUid-owned owner-private (no group/world access) inboxes.
#   * "bundle"       pixelWorkUid:Gid-owned owner-private stable bundle roots/bundles.
#   * "root"         root-owned, group/world readable but never writable (runtime tree).
#   * "root_private" root-owned owner-private (config, receipts, reboot state).
def boundary(value, which):
    if which == "inbox":
        return (value["ownerUid"], None, True)
    if which == "bundle":
        return (value["pixelWorkUid"], value["pixelWorkGid"], True)
    if which == "root":
        return (0, 0, False)
    if which == "root_private":
        return (0, 0, True)
    raise ValueError(f"unknown boundary {which}")


def owner_mode_ok(*, uid, gid, mode, owner_uid, owner_gid, private):
    """Pure per-boundary ownership/mode policy (unit-testable without root)."""
    if owner_uid is not None and uid != owner_uid:
        return False
    if owner_gid is not None and gid != owner_gid:
        return False
    mask = 0o077 if private else 0o022
    return (mode & mask) == 0


def _check_mode(info, label, private):
    mask = 0o077 if private else 0o022
    if info.st_mode & mask:
        fail(f"{label} has unsafe permissions")


def require_dir(path: Path, label: str, which: str, value: dict):
    """Require a real (non-link) directory matching the boundary policy, no-follow."""
    try:
        raw = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
        fail(f"{label} must be a real directory")
    if TESTING:
        return raw
    owner_uid, owner_gid, private = boundary(value, which)
    if not owner_mode_ok(uid=raw.st_uid, gid=raw.st_gid, mode=raw.st_mode, owner_uid=owner_uid, owner_gid=owner_gid, private=private):
        fail(f"{label} ownership or mode is invalid")
    return raw


def require_file(info, label: str, which: str, value: dict, *, executable=False):
    """Require a file's boundary ownership/mode policy (called with secure_read info)."""
    if TESTING:
        return
    owner_uid, owner_gid, private = boundary(value, which)
    if not owner_mode_ok(uid=info.st_uid, gid=info.st_gid, mode=info.st_mode, owner_uid=owner_uid, owner_gid=owner_gid, private=private):
        fail(f"{label} ownership or mode is invalid")
    if not executable and (info.st_mode & 0o111):
        fail(f"{label} must not be executable")


def require_inbox_file(info, label: str, value: dict):
    """Require an inbox source file match the owner-private inbox policy.

    The mode (no group/world bits, non-executable) and link count are enforced for real in
    every environment because they are filesystem properties. Ownership is enforced under
    root and simulated under TESTING (where chown is unavailable).
    """
    if info.st_nlink > 1:
        fail(f"{label} must not be a hardlink")
    if info.st_mode & 0o077:
        fail(f"{label} has unsafe permissions for the owner-private inbox")
    if info.st_mode & 0o111:
        fail(f"{label} must not be executable")
    if TESTING:
        return
    owner_uid, _, private = boundary(value, "inbox")
    if not owner_mode_ok(uid=info.st_uid, gid=info.st_gid, mode=info.st_mode,
                         owner_uid=owner_uid, owner_gid=None, private=private):
        fail(f"{label} ownership or mode is invalid for the inbox")


def _chown(path, uid, gid):
    """Set ownership; no-op under TESTING (non-root) and recorded for the harness."""
    if not TESTING:
        # A boundary with no group constraint deliberately preserves the directory's
        # inherited group. Python's os.chown uses -1 for that POSIX sentinel; passing
        # None raises TypeError on the real first-provision path.
        os.chown(path, uid, -1 if gid is None else gid)


def fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_all(descriptor, payload):
    view = memoryview(payload)
    while view:
        try:
            written = os.write(descriptor, view)
        except InterruptedError:
            continue
        if written <= 0:
            fail("write made no progress")
        view = view[written:]


def _renameat2_noreplace(src_bytes, dst_bytes):
    """Rename via Linux renameat2(RENAME_NOREPLACE), failing closed on unavailability."""
    libc = ctypes.CDLL(None, use_errno=True)
    AT_FDCWD = -100
    RENAME_NOREPLACE = 1
    result = libc.renameat2(AT_FDCWD, src_bytes, AT_FDCWD, dst_bytes, RENAME_NOREPLACE)
    if result == 0:
        return
    code = ctypes.get_errno()
    if code in (errno.EEXIST, errno.ENOTEMPTY):
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(dst_bytes))
    raise OSError(code, os.strerror(code), str(dst_bytes))


def no_replace_rename(src, dst):
    """Atomically rename src to dst only when dst does not already exist.

    Uses Linux renameat2(RENAME_NOREPLACE) as the single supported no-replace primitive.
    On a supported Linux host where the primitive is unavailable (ENOSYS/EINVAL/ENOTSUP)
    this fails closed rather than falling back to os.rename, which could silently replace
    an empty destination directory and violate the no-replace promise. Non-Linux targets
    are unsupported and fail closed too.
    """
    if not sys.platform.startswith("linux"):
        raise OSError(errno.ENOSYS, "no-replace rename is only supported on Linux", str(dst))
    _renameat2_noreplace(os.fsencode(src), os.fsencode(dst))


def _uid(value, label):
    if not isinstance(value, int) or not (0 <= value <= 2**32 - 1):
        fail(f"{label} must be a non-negative integer UID")
    return value


def _path(value, label):
    if not isinstance(value, str) or not value:
        fail(f"{label} must be a non-empty path string")
    p = Path(value)
    if str(p) != str(p.resolve()):
        fail(f"{label} must be a normalized absolute path")
    return p


def config_path():
    """Resolve the dedicated operator config path (env override only under TESTING)."""
    if TESTING:
        return Path(os.environ.get("PIXEL_OPERATOR_CONFIG", "/etc/pixel-release-operator/pixel.json"))
    return Path("/etc/pixel-release-operator/pixel.json")


def load_config():
    payload, info = secure_read(config_path(), "pixel operator config", MAX_CONFIG)
    require_file(info, "pixel operator config", "root_private", {}, executable=False)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("pixel operator config is not valid JSON")
    validate_config(value)
    return value


def _validate_broker_bytes_config(value):
    broker = value.get("brokerBytes")
    if not isinstance(broker, dict) or set(broker.keys()) != {"backupRoot", "limbs"}:
        fail("brokerBytes must contain exactly backupRoot and limbs")
    backup_root = _path(broker["backupRoot"], "brokerBytes.backupRoot")
    if not TESTING and backup_root != Path("/var/lib/pixel-release-operator/broker-bytes"):
        fail("brokerBytes.backupRoot must be the fixed operator-private path")
    limbs = broker["limbs"]
    if not isinstance(limbs, dict) or tuple(sorted(limbs.keys())) != tuple(sorted(BROKER_LIMBS)):
        fail("brokerBytes.limbs must contain exactly source, ops, and frontier")
    install_dirs = []
    for limb in BROKER_LIMBS:
        item = limbs[limb]
        expected = {"enabled", "installDir", "directPathEnabled"} if limb == "source" else {"enabled", "installDir"}
        if not isinstance(item, dict) or set(item.keys()) != expected:
            fail(f"brokerBytes.limbs.{limb} has unexpected or missing keys")
        if not isinstance(item["enabled"], bool):
            fail(f"brokerBytes.limbs.{limb}.enabled must be a boolean")
        install_dir = _path(item["installDir"], f"brokerBytes.limbs.{limb}.installDir")
        if not TESTING and install_dir != Path(f"/opt/pixel-{limb}-broker"):
            fail(f"brokerBytes.limbs.{limb}.installDir must be the fixed broker path")
        install_dirs.append(install_dir)
        if limb == "source" and not isinstance(item["directPathEnabled"], bool):
            fail("brokerBytes.limbs.source.directPathEnabled must be a boolean")
    if len(set(install_dirs)) != len(install_dirs):
        fail("brokerBytes install directories must be distinct")
    for install_dir in install_dirs:
        if install_dir == backup_root or install_dir in backup_root.parents or backup_root in install_dir.parents:
            fail("brokerBytes backup and install roots must be disjoint")
    return broker


def validate_config(value):
    if not isinstance(value, dict):
        fail("pixel operator config must be a JSON object")
    if set(value.keys()) != set(CONFIG_KEYS):
        fail("pixel operator config has unexpected or missing keys")
    if value["schemaVersion"] != 1:
        fail("pixel operator config must use schemaVersion 1")
    owner_uid = _uid(value["ownerUid"], "ownerUid")
    pixel_work_uid = _uid(value["pixelWorkUid"], "pixelWorkUid")
    pixel_work_gid = _uid(value["pixelWorkGid"], "pixelWorkGid")
    if pixel_work_uid == 0 or pixel_work_gid == 0:
        fail("pixelWorkUid/pixelWorkGid must not be privileged root")
    if owner_uid == 0:
        fail("ownerUid must not be root")
    if value["readersEnabled"] is not True and value["readersEnabled"] is not False:
        fail("readersEnabled must be a boolean")
    for key in ("inboxRoot", "bundleRoot", "runtimePath", "receiptRoot", "rebootRoot"):
        _path(value[key], key)
    for key in ("nodePath", "systemctlPath", "systemdAnalyzePath"):
        p = _path(value[key], key)
        if p.parent != Path("/usr/bin"):
            fail(f"{key} must be a fixed system path")
    if not isinstance(value["runtimeTreeSha256"], str) or not SHA256.fullmatch(value["runtimeTreeSha256"]):
        fail("runtimeTreeSha256 must be a lowercase SHA-256")
    _validate_broker_bytes_config(value)
    return value


def _kind_cli_path(value, kind):
    cli = KIND_CLI.get(kind)
    if cli is None:
        fail(f"unsupported bundle kind: {kind}")
    return Path(str(value["runtimePath"])) / cli


def _scan_tree(root: Path, label: str):
    """Return the set of relative regular-file paths under root, rejecting everything else.

    Any symlink, special entry, or hardlink fails closed so the runtime tree can be required
    to equal exactly its manifest closure (runtime-manifest.json plus declared files).
    """
    found = set()
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as it:
            for entry in it:
                rel = os.path.relpath(entry.path, root)
                try:
                    raw = entry.stat(follow_symlinks=False)
                except OSError:
                    fail(f"{label} contains an unreadable entry")
                if stat.S_ISLNK(raw.st_mode):
                    fail(f"{label} must not contain a symlink: {rel}")
                if stat.S_ISDIR(raw.st_mode):
                    stack.append(Path(entry.path))
                    continue
                if not stat.S_ISREG(raw.st_mode):
                    fail(f"{label} contains a special entry: {rel}")
                if raw.st_nlink > 1:
                    fail(f"{label} must not contain a hardlink: {rel}")
                found.add(rel)
    return found


def verify_runtime_tree(value, *, check_owner=True):
    """Verify the exact root-owned runtime snapshot against its tree manifest SHA."""
    runtime = Path(str(value["runtimePath"]))
    if check_owner:
        require_dir(runtime, "runtime root", "root", value)
    else:
        try:
            raw = runtime.lstat()
        except FileNotFoundError:
            fail("staged runtime root is missing")
        if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
            fail("staged runtime root must be a real directory")
    manifest_path = runtime / "runtime-manifest.json"
    payload, info = secure_read(manifest_path, "runtime tree manifest", MAX_CONFIG)
    if check_owner:
        require_file(info, "runtime tree manifest", "root", value, executable=False)
    if digest(payload) != value["runtimeTreeSha256"]:
        fail("runtime tree manifest does not match the configured tree SHA")
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("runtime tree manifest is not valid JSON")
    if not isinstance(manifest, dict) or set(manifest.keys()) != {"schemaVersion", "files"} or manifest.get("schemaVersion") != 1:
        fail("runtime tree manifest shape is invalid")
    files = manifest["files"]
    if not isinstance(files, dict) or not files:
        fail("runtime tree manifest must declare files")
    total = 0
    for rel, expected in files.items():
        if not isinstance(rel, str) or rel.startswith("/") or ".." in rel.split("/"):
            fail("runtime tree manifest contains an unsafe relative path")
        if not isinstance(expected, str) or not SHA256.fullmatch(expected):
            fail("runtime tree manifest contains an invalid SHA")
        fpath = runtime / rel
        bytes_, info_ = secure_read(fpath, f"runtime file {rel}", MAX_UNIT_BYTES * 4)
        if check_owner:
            require_file(info_, f"runtime file {rel}", "root", value, executable=False)
        if digest(bytes_) != expected:
            fail(f"runtime file {rel} differs from its tree manifest")
        total += len(bytes_)
    expected_paths = set(files.keys()) | {"runtime-manifest.json"}
    actual = _scan_tree(runtime, "runtime tree")
    if actual != expected_paths:
        missing = sorted(expected_paths - actual)
        extra = sorted(actual - expected_paths)
        fail(f"runtime tree does not equal its manifest closure (missing={missing}, extra={extra})")
    return files


def _manifest_name(kind):
    name = MANIFEST_FILENAME.get(kind)
    if name is None:
        fail(f"unsupported bundle kind: {kind}")
    return name


def resolve_stable_bundle(value, kind, sha):
    """Resolve the exact stable bundle path for kind+sha; fail closed if absent."""
    root = Path(str(value["bundleRoot"])) / kind / sha
    require_dir(root, "stable bundle", "bundle", value)
    manifest = root / _manifest_name(kind)
    bytes_, info = secure_read(manifest, "stable bundle manifest", MAX_UNIT_BYTES)
    if digest(bytes_) != sha:
        fail("stable bundle manifest does not match the requested SHA")
    require_file(info, "stable bundle manifest", "bundle", value, executable=False)
    return root


def _list_regular_files(directory: Path, label: str):
    names = []
    with os.scandir(directory) as it:
        for entry in it:
            try:
                raw = entry.stat(follow_symlinks=False)
            except OSError:
                fail(f"{label} contains an unreadable entry")
            if stat.S_ISLNK(raw.st_mode):
                fail(f"{label} must not contain a symlink")
            if stat.S_ISREG(raw.st_mode):
                if raw.st_nlink > 1:
                    fail(f"{label} must not contain a hardlink")
                names.append(entry.name)
            else:
                fail(f"{label} must contain only regular files and directories")
    names.sort()
    return names


def _cleanup_stage(tmp: Path, tmp_id, created):
    """Fail-closed cleanup of a root-owned staging directory; never touch a raced path.

    Verifies the path is still the exact staging directory we created (by device+inode)
    before unlinking only the files we wrote, so a concurrently swapped replacement is
    never unlinked and a symlink/special is never followed.
    """
    try:
        raw = tmp.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(raw.st_mode) or stat.S_ISLNK(raw.st_mode):
        return
    if (raw.st_dev, raw.st_ino) != tmp_id:
        return
    for name in created:
        try:
            (tmp / name).unlink()
        except FileNotFoundError:
            pass
    try:
        tmp.rmdir()
    except OSError:
        pass


def secure_copy_bundle(value, kind, sha):
    """Copy the candidate bundle from the owner-private inbox to the pixel-work stable root.

    Reads only <inboxRoot>/<kind>/<sha>/. Verifies the manifest SHA and every file's SHA
    before and after copy with short-write-safe loops, fsync, no-follow, link-count, and
    inbox ownership/mode checks. Staging stays root-owned and inaccessible (0700) until
    every file is verified, ownership is then transferred exactly, and the directory is
    published with Linux no-replace semantics so a concurrent destination is never
    replaced. Cleanup is fail-closed and never unlinks a raced replacement.
    """
    inbox = Path(str(value["inboxRoot"])) / kind / sha
    require_dir(inbox, "inbox bundle", "inbox", value)
    bundle_root = Path(str(value["bundleRoot"])) / kind
    require_dir(bundle_root, "bundle root", "bundle", value)
    dest = bundle_root / sha
    try:
        dest.lstat()
        return resolve_stable_bundle(value, kind, sha)
    except FileNotFoundError:
        pass
    files = _list_regular_files(inbox, "inbox bundle")
    if not files:
        fail("inbox bundle is empty")
    if len(files) > MAX_BUNDLE_FILES:
        fail("inbox bundle has too many files")
    total = 0
    sources = {}
    manifest_name = _manifest_name(kind)
    for name in files:
        fpath = inbox / name
        bytes_, info = secure_read(fpath, f"inbox bundle file {name}", MAX_UNIT_BYTES)
        require_inbox_file(info, f"inbox bundle file {name}", value)
        sources[name] = bytes_
        total += len(bytes_)
    if total > MAX_BUNDLE_TOTAL:
        fail("inbox bundle exceeds its total size limit")
    manifest_bytes = sources.get(manifest_name)
    if manifest_bytes is None:
        fail(f"inbox bundle is missing {manifest_name}")
    if digest(manifest_bytes) != sha:
        fail("inbox bundle manifest does not match the requested SHA")
    parent = bundle_root
    tmp = parent / (f".stage-{sha}-{os.getpid()}-{secrets.token_hex(4)}")
    created = []
    os.mkdir(tmp, 0o700)
    tmp_stat = tmp.stat(follow_symlinks=False)
    tmp_id = (tmp_stat.st_dev, tmp_stat.st_ino)
    try:
        for name, data in sources.items():
            target = tmp / name
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                write_all(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            written, w_info = secure_read(target, f"staged bundle file {name}", MAX_UNIT_BYTES)
            if digest(written) != digest(data):
                fail(f"staged bundle file {name} differs from its source")
            created.append(name)
        fsync_dir(tmp)
        # Transfer exact final ownership only after every file is verified, while the
        # staging directory is still root-owned and inaccessible to pixel-work (0700).
        for name in created:
            target = tmp / name
            _chown(target, value["pixelWorkUid"], value["pixelWorkGid"])
            os.chmod(target, 0o600)
        _chown(tmp, value["pixelWorkUid"], value["pixelWorkGid"])
        os.chmod(tmp, 0o700)
        fsync_dir(tmp)
        no_replace_rename(tmp, dest)
    except FileExistsError:
        # A concurrent identical (content-addressed) publication won; dest is valid.
        pass
    finally:
        _cleanup_stage(tmp, tmp_id, created)
    fsync_dir(parent)
    return resolve_stable_bundle(value, kind, sha)


class RunResult:
    def __init__(self, returncode, stdout, stderr, truncated):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.truncated = truncated


# Hard bound (seconds) to wait for reader threads to reach EOF after the child exits. A
# descendant holding a pipe past this bound means output would be silently incomplete, so
# run_bounded terminates the process group and fails closed instead of returning normally.
RUN_DRAIN_TIMEOUT = 10


def run_bounded(argv, timeout=300):
    """Run a fixed argv array with a bounded fixed environment; output capped while running.

    Child stdout/stderr are hard-bounded to MAX_OUTPUT during execution (never truncated
    only afterward); a hard timeout, a killed child, or a descendant holding a pipe open
    past the drain bound all fail closed via a raised error rather than returning normally.
    """
    deadline = time.monotonic() + min(max(int(timeout), 1), 3600)
    process = subprocess.Popen(
        argv, cwd="/", stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True,
        env={"HOME": "/", "LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}

    def drain(name, stream):
        while True:
            try:
                chunk = stream.read(8192)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            remaining = MAX_OUTPUT - len(captured[name])
            if remaining > 0:
                captured[name].extend(chunk[:remaining])
            if len(chunk) > max(remaining, 0):
                truncated[name] = True

    assert process.stdout is not None and process.stderr is not None
    readers = [threading.Thread(target=drain, args=(name, stream), daemon=True) for name, stream in (("stdout", process.stdout), ("stderr", process.stderr))]
    for reader in readers:
        reader.start()
    timed_out = False
    while process.poll() is None:
        if time.monotonic() >= deadline:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            break
        time.sleep(0.05)
    # The child has exited, so its write ends are closed and the reader threads should
    # reach EOF. Join/drain before closing the pipes; closing first would truncate
    # buffered output or error the readers before they reach EOF. If a descendant keeps a
    # pipe open past the hard drain bound, the captured output would be silently
    # incomplete: terminate the process group and fail closed, never returning normally.
    for reader in readers:
        reader.join(timeout=RUN_DRAIN_TIMEOUT)
    stalled = [reader for reader in readers if reader.is_alive()]
    if stalled:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for reader in readers:
            reader.join()
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        fail(f"command {Path(argv[0]).name} output did not drain; a descendant held a pipe")
    for stream in (process.stdout, process.stderr):
        try:
            stream.close()
        except OSError:
            pass
    if timed_out:
        fail(f"command {Path(argv[0]).name} exceeded its hard timeout")
    return RunResult(
        process.returncode,
        captured["stdout"].decode("utf-8", "replace"),
        captured["stderr"].decode("utf-8", "replace"),
        truncated["stdout"] or truncated["stderr"],
    )


def _run(argv, check=True):
    """Run a fixed argv array, failing closed on truncation or unexpected nonzero status."""
    result = run_bounded(argv)
    if result.truncated:
        fail(f"{Path(argv[0]).name} output exceeded its hard bound")
    if check and result.returncode != 0:
        label = Path(argv[1]).name if len(argv) > 1 and str(argv[1]).endswith(".mjs") else Path(argv[0]).name
        fail(f"{label} failed with exit code {result.returncode}")
    return result


def invoke_node_cli(value, kind, argv_tail):
    node = str(value["nodePath"])
    cli = str(_kind_cli_path(value, kind))
    verify_runtime_tree(value)
    return _run([node, cli, *argv_tail], check=True)


def _inspect_args(value, kind, bundle):
    return ["inspect", "--bundle", str(bundle), "--expected-owner-uid", str(value["pixelWorkUid"])]


def bundle_install(value, kind, sha):
    secure_copy_bundle(value, kind, sha)
    invoke_node_cli(value, kind, ["install", "--bundle", str(Path(str(value["bundleRoot"])) / kind / sha), "--confirm-manifest-sha256", sha])
    return {"operation": "pixel-operator-bundle-install", "kind": kind, "manifestSha256": sha, "state": "installed-inactive"}


def bundle_inspect(value, kind, sha):
    bundle = resolve_stable_bundle(value, kind, sha)
    invoke_node_cli(value, kind, _inspect_args(value, kind, bundle))
    return {"operation": "pixel-operator-bundle-inspect", "kind": kind, "manifestSha256": sha, "state": "bundle-verified-no-side-effects"}


def bundle_activate(value, kind, sha):
    bundle = resolve_stable_bundle(value, kind, sha)
    invoke_node_cli(value, kind, ["activate", "--bundle", str(bundle), "--confirm-manifest-sha256", sha])
    return {"operation": "pixel-operator-bundle-activate", "kind": kind, "manifestSha256": sha, "state": "active"}


def bundle_remove(value, kind, sha):
    bundle = resolve_stable_bundle(value, kind, sha)
    invoke_node_cli(value, kind, ["remove", "--bundle", str(bundle), "--confirm-manifest-sha256", sha])
    return {"operation": "pixel-operator-bundle-remove", "kind": kind, "manifestSha256": sha, "state": "removed-private-state-retained"}


def bundle_rollback(value, kind, current_sha, prior_sha):
    """Roll back to a prior bundle with the real lifecycle sequence and exact compensation.

    The prior bundle may never have been installed: install prior (idempotent if already
    installed), then activate prior, then remove current. On any failure the current
    bundle is reinstalled and reactivated so the host is never left without a release.
    """
    if current_sha == prior_sha:
        fail("rollback requires two distinct manifest SHAs")
    current_bundle = resolve_stable_bundle(value, kind, current_sha)
    prior_bundle = resolve_stable_bundle(value, kind, prior_sha)
    try:
        invoke_node_cli(value, kind, ["install", "--bundle", str(prior_bundle), "--confirm-manifest-sha256", prior_sha])
        invoke_node_cli(value, kind, ["activate", "--bundle", str(prior_bundle), "--confirm-manifest-sha256", prior_sha])
        invoke_node_cli(value, kind, ["remove", "--bundle", str(current_bundle), "--confirm-manifest-sha256", current_sha])
    except PixelOperatorError:
        try:
            invoke_node_cli(value, kind, ["install", "--bundle", str(current_bundle), "--confirm-manifest-sha256", current_sha])
            invoke_node_cli(value, kind, ["activate", "--bundle", str(current_bundle), "--confirm-manifest-sha256", current_sha])
        except PixelOperatorError:
            fail("rollback failed and compensation could not restore the current bundle")
        raise
    return {"operation": "pixel-operator-bundle-rollback", "kind": kind, "from": current_sha, "to": prior_sha, "state": "rolled-back"}


def _manifest_unit_names(value, kind, sha):
    bundle = resolve_stable_bundle(value, kind, sha)
    bytes_, info = secure_read(bundle / _manifest_name(kind), "stable bundle manifest", MAX_UNIT_BYTES)
    require_file(info, "stable bundle manifest", "bundle", value, executable=False)
    try:
        manifest = json.loads(bytes_.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("stable bundle manifest is not valid JSON")
    names = []
    for key in ("serviceName", "timerName", "pathName"):
        name = manifest.get(key)
        if isinstance(name, str) and name:
            names.append(name)
    if not names:
        fail("stable bundle manifest declares no unit names")
    names = sorted(set(names))
    for name in names:
        if not UNIT_NAME_RE.fullmatch(name):
            fail(f"stable bundle unit name is unsafe: {name}")
    return names


def _systemctl_state(binpath, verb, unit):
    """Return (state, error). Distinguishes expected inactive/disabled from errors."""
    result = _run([binpath, verb, unit], check=False)
    if result.truncated:
        fail(f"systemctl {verb} output exceeded its hard bound")
    out = result.stdout.strip()
    if verb == "is-enabled":
        if result.returncode == 0:
            return out or "enabled", None
        if result.returncode == 1:
            return out or "disabled", None
        return None, f"systemctl is-enabled failed for {unit} (exit {result.returncode})"
    if verb == "is-active":
        if result.returncode == 0:
            return out or "active", None
        if result.returncode == 3:
            return out or "inactive", None
        return None, f"systemctl is-active failed for {unit} (exit {result.returncode})"
    fail(f"unsupported systemctl verb {verb}")


def service_status(value, kind, sha):
    names = _manifest_unit_names(value, kind, sha)
    evidence = {}
    all_ready = True
    for name in names:
        enabled_state, enabled_err = _systemctl_state(systemctl_bin(), "is-enabled", name)
        if enabled_err:
            fail(enabled_err)
        active_state, active_err = _systemctl_state(systemctl_bin(), "is-active", name)
        if active_err:
            fail(active_err)
        evidence[name] = {"enabled": enabled_state, "active": active_state}
        if enabled_state != "enabled" or active_state != "active":
            all_ready = False
    result = {"operation": "pixel-operator-service-status", "kind": kind, "manifestSha256": sha, "units": evidence}
    if all_ready:
        result["evidenceSha256"] = _write_evidence_receipt(value, kind, sha, evidence)
    return result


def _publish_content_addressed(root: Path, payload: bytes, value: dict, label: str):
    """Publish a root-private content-addressed file with true no-replace semantics.

    The final name is the payload's SHA-256. The payload is first written to a root-private
    temporary inode in the same directory, fsynced, and only then published with Linux
    no-replace semantics, so a crash or short write can never surface a partial file at the
    final content-addressed name. A concurrent identical publication is accepted; a
    differing collision fails closed.
    """
    sha256 = digest(payload)
    name = f"{sha256}.json"
    path = root / name
    tmp = root / f".{name}.{os.getpid()}.{secrets.token_hex(4)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not TESTING:
            os.fchown(fd, 0, 0)
        write_all(fd, payload)
        os.fsync(fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    fsync_dir(root)
    try:
        no_replace_rename(tmp, path)
    except FileExistsError:
        existing, info = secure_read(path, label, MAX_CONFIG)
        require_file(info, label, "root_private", value, executable=False)
        if existing != payload:
            fail(f"{label} name collision with differing content")
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        fsync_dir(root)
    else:
        fsync_dir(root)
    return sha256, name


def _write_evidence_receipt(value, kind, sha, units):
    """Persist a hash-addressed, root-private reboot evidence receipt.

    Created only after exact manifest-derived units are verified enabled+active. Binds
    kind, the manifest SHA, the exact unit set/states, the observation time, and the
    operator receipt identity, so reboot prepare can authenticate and validate freshness.
    The receipt is written to a root-private temp inode, fsynced, then published with true
    no-replace semantics so a partial receipt can never collide with the final name.
    """
    record = {
        "schemaVersion": 1,
        "operation": "pixel-operator-service-status",
        "kind": kind,
        "manifestSha256": sha,
        "units": units,
        "observedAt": int(time.time()),
        "operator": OPERATOR_IDENTITY,
    }
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    root = Path(str(value["rebootRoot"])) / "evidence"
    require_dir(root, "reboot evidence root", "root_private", value)
    evidence_sha, _ = _publish_content_addressed(root, payload, value, "reboot evidence receipt")
    return evidence_sha


def _unit_enabled_active(binpath, unit):
    enabled = _run([binpath, "is-enabled", "--quiet", unit], check=False)
    active = _run([binpath, "is-active", "--quiet", unit], check=False)
    if enabled.truncated or active.truncated:
        fail(f"systemctl precondition output exceeded its hard bound for {unit}")
    return enabled.returncode == 0 and active.returncode == 0


def _units_enabled_active(binpath, units):
    for unit in units:
        if not _unit_enabled_active(binpath, unit):
            return False
    return True


def read_boot_id():
    """Fail closed: return the exact current boot ID or raise; never 'unknown'."""
    try:
        raw, info = secure_read(Path(BOOT_ID_PATH), "boot id", 4096)
    except PixelOperatorError as error:
        fail(f"reboot cannot read boot id: {error}")
    value = raw.decode("utf-8").strip().lower()
    if not (re.fullmatch(r"[0-9a-f]{32}", value) or re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", value)):
        fail("boot id is unreadable or malformed")
    return value


def _root_private_value():
    return {"pixelWorkUid": 0, "pixelWorkGid": 0, "ownerUid": 0}


def _atomic_write_json(path: Path, data: dict, *, owner_uid=0, owner_gid=0):
    """Write a JSON state file atomically via temp + rename + fsync; never truncate in place."""
    parent = path.parent
    require_dir(parent, f"{path.name} parent", "root_private", _root_private_value())
    tmp = parent / (f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not TESTING:
            os.fchown(fd, owner_uid, owner_gid)
        write_all(fd, json.dumps(data, sort_keys=True).encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.rename(tmp, path)
    fsync_dir(parent)


def _evidence_receipt(value, evidence_sha):
    """Authenticate a root-owned durable evidence receipt and return its bound fields.

    Validates the full evidence contract (operation, kind, manifest SHA, exact
    enabled+active unit set/states, observation time, and operator receipt identity) and
    returns (receipt, kind, sorted unit names, manifest_sha, observed_at).
    """
    root = Path(str(value["rebootRoot"])) / "evidence"
    require_dir(root, "reboot evidence root", "root_private", value)
    path = root / f"{evidence_sha}.json"
    payload, info = secure_read(path, "reboot evidence receipt", MAX_CONFIG)
    require_file(info, "reboot evidence receipt", "root_private", value, executable=False)
    if digest(payload) != evidence_sha:
        fail("reboot evidence receipt does not match its filename SHA")
    try:
        receipt = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("reboot evidence receipt is not valid JSON")
    if not isinstance(receipt, dict) or receipt.get("schemaVersion") != 1:
        fail("reboot evidence receipt schema is invalid")
    if receipt.get("operation") != "pixel-operator-service-status":
        fail("reboot evidence receipt operation is invalid")
    if receipt.get("operator") != OPERATOR_IDENTITY:
        fail("reboot evidence receipt operator identity is invalid")
    kind = receipt.get("kind")
    if not isinstance(kind, str) or kind not in REBOOT_KINDS:
        fail("reboot evidence receipt must declare a supported reboot kind")
    manifest_sha = receipt.get("manifestSha256")
    if not isinstance(manifest_sha, str) or not SHA256.fullmatch(manifest_sha):
        fail("reboot evidence receipt manifest SHA is invalid")
    observed_at = receipt.get("observedAt")
    if not isinstance(observed_at, int) or observed_at <= 0:
        fail("reboot evidence receipt observation time is invalid")
    units = receipt.get("units")
    if not isinstance(units, dict) or not units:
        fail("reboot evidence receipt must declare exact units")
    unit_names = []
    for name, state in units.items():
        if not isinstance(name, str) or not UNIT_NAME_RE.fullmatch(name):
            fail("reboot evidence receipt unit name is unsafe")
        if not isinstance(state, dict) or set(state.keys()) != {"enabled", "active"}:
            fail("reboot evidence receipt unit state is invalid")
        if state.get("enabled") != "enabled" or state.get("active") != "active":
            fail("reboot evidence receipt unit is not enabled and active")
        unit_names.append(name)
    return receipt, kind, sorted(set(unit_names)), manifest_sha, observed_at


def _expired(record, now):
    return record.get("consumed") is not True and int(record.get("expiresAt", 0)) < now


def _prune_archive(archive_dir: Path, max_keep: int):
    entries = []
    with os.scandir(archive_dir) as it:
        for entry in it:
            try:
                raw = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(raw.st_mode):
                continue
            entries.append((raw.st_mtime_ns, entry.path))
    if len(entries) > max_keep:
        entries.sort()
        for _, path in entries[: len(entries) - max_keep]:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        fsync_dir(archive_dir)


def _archive_entry(source: Path, archive_dir: Path, prefix: str, max_keep: int):
    """Move a state file into a bounded archive directory; never loses the source."""
    _ensure_scaffold(archive_dir, f"{archive_dir.name} archive", "root_private", _root_private_value())
    require_dir(archive_dir, f"{archive_dir.name} archive", "root_private", _root_private_value())
    dest = archive_dir / f"{prefix}-{secrets.token_hex(8)}.json"
    no_replace_rename(source, dest)
    fsync_dir(archive_dir)
    if source.parent != archive_dir:
        fsync_dir(source.parent)
    _prune_archive(archive_dir, max_keep)


def _sweep_grants(value, now):
    """Remove expired unconsumed grants and archive old consumed grants (bounded)."""
    grant_root = Path(str(value["rebootRoot"])) / "grants"
    archive_root = grant_root / "archive"
    with os.scandir(grant_root) as it:
        for entry in it:
            if not entry.name.endswith(".json") or entry.is_symlink():
                continue
            path = Path(entry.path)
            payload, info = secure_read(path, "reboot grant", MAX_CONFIG)
            if not TESTING and info.st_uid != 0:
                fail("reboot grant must be root-owned")
            try:
                record = json.loads(payload.decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                fail("reboot grant is not valid JSON")
            if record.get("consumed") is not True:
                if _expired(record, now):
                    os.unlink(path)
                    fsync_dir(grant_root)
                continue
            consumed_at = int(record.get("consumedAt", 0) or 0)
            if consumed_at and now - consumed_at > REBOOT_GRANT_TTL_SECONDS:
                try:
                    path.lstat()
                except FileNotFoundError:
                    continue
                _archive_entry(path, archive_root, "consumed", ARCHIVE_MAX_KEEP)


def _pending_grant(value, now):
    """Return the first valid (non-expired, unconsumed) pending grant file, else None."""
    _sweep_grants(value, now)
    grant_root = Path(str(value["rebootRoot"])) / "grants"
    require_dir(grant_root, "reboot grant root", "root_private", value)
    with os.scandir(grant_root) as it:
        for entry in it:
            if not entry.name.endswith(".json") or entry.is_symlink():
                continue
            path = Path(entry.path)
            payload, info = secure_read(path, "reboot grant", MAX_CONFIG)
            if not TESTING and info.st_uid != 0:
                fail("reboot grant must be root-owned")
            try:
                record = json.loads(payload.decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                fail("reboot grant is not valid JSON")
            if record.get("consumed") is not True:
                return path, record
    return None, None


def reboot_prepare(value, kind, evidence_sha):
    if kind not in REBOOT_KINDS:
        fail("reboot prepare supports only goal or deep-work-soak units")
    receipt, rkind, unit_names, manifest_sha, observed_at = _evidence_receipt(value, evidence_sha)
    if rkind != kind:
        fail("reboot evidence receipt kind does not match the requested kind")
    now = int(time.time())
    if observed_at > now:
        fail("reboot evidence receipt observation time is in the future")
    if now - observed_at > REBOOT_EVIDENCE_TTL_SECONDS:
        fail("reboot evidence receipt is stale; re-observe service status")
    declared = _manifest_unit_names(value, kind, manifest_sha)
    if sorted(declared) != sorted(unit_names):
        fail("reboot evidence unit set does not match the stable manifest")
    if not _units_enabled_active(systemctl_bin(), unit_names):
        fail("reboot precondition units are not all enabled and active")
    grant_root = Path(str(value["rebootRoot"])) / "grants"
    require_dir(grant_root, "reboot grant root", "root_private", value)
    pending, pending_record = _pending_grant(value, now)
    if pending is not None:
        fail("a valid reboot grant is already pending; consume or expire it first")
    if _load_intent(value) is not None:
        fail("a committed reboot intent must be reconciled before a new prepare")
    boot_id = read_boot_id()
    grant = secrets.token_hex(32)
    expiry = now + REBOOT_GRANT_TTL_SECONDS
    record = {
        "schemaVersion": 1, "operation": "pixel-operator-reboot-prepare",
        "kind": kind, "units": unit_names, "evidenceSha256": evidence_sha,
        "manifestSha256": manifest_sha, "grant": grant, "bootId": boot_id,
        "expiresAt": expiry, "consumed": False,
    }
    path = grant_root / f"{grant}.json"
    _atomic_write_json(path, record)
    return {
        "schemaVersion": 1, "operation": "pixel-operator-reboot-prepare", "kind": kind,
        "units": unit_names, "grant": grant, "expiresAt": expiry,
        "bootIdSha256": digest(boot_id.encode("utf-8")), "state": "prepared",
    }


def _load_grant(value, grant):
    if not _GRANT_RE.fullmatch(grant):
        fail("reboot grant is malformed")
    grant_root = Path(str(value["rebootRoot"])) / "grants"
    require_dir(grant_root, "reboot grant root", "root_private", value)
    path = grant_root / f"{grant}.json"
    payload, info = secure_read(path, "reboot grant", MAX_CONFIG)
    require_file(info, "reboot grant", "root_private", value, executable=False)
    try:
        record = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("reboot grant is not valid JSON")
    if record.get("grant") != grant:
        fail("reboot grant token mismatch")
    return path, record


def _consume_grant(path, record):
    """Atomically replace a grant with a consumed record via temp + rename (no truncate)."""
    consumed = dict(record)
    consumed["consumed"] = True
    consumed["consumedAt"] = int(time.time())
    _atomic_write_json(path, consumed)


def _validate_grant(value, record):
    kind = record.get("kind")
    if not isinstance(kind, str) or kind not in REBOOT_KINDS:
        fail("reboot grant kind is invalid")
    units = record.get("units")
    if not isinstance(units, list) or not units:
        fail("reboot grant units are invalid")
    units = sorted(set(units))
    for name in units:
        if not isinstance(name, str) or not UNIT_NAME_RE.fullmatch(name):
            fail("reboot grant unit is invalid")
    if not _units_enabled_active(systemctl_bin(), units):
        fail("reboot precondition units are not all enabled and active")
    return kind, units


def _recover_prepared(value, now):
    """Recover a crashed reboot execute without replaying or misclassifying authority.

    A leftover <grant>.prepared.json with no current.json means the reboot command never
    ran. If its grant was already consumed the authority is burned but no intent committed:
    classify as a pre-reboot failure and archive the prepared intent and consumed grant.
    If its grant is still unconsumed, no authority was burned: remove the prepared intent.
    Corrupt or ambiguous grant state (malformed, partial, wrong-owner/mode, or missing)
    fails closed rather than being reclassified and deleted: the prepared intent and grant
    are left untouched so the ambiguous authority state is never destroyed.
    """
    intent_root = Path(str(value["rebootRoot"])) / "intent"
    require_dir(intent_root, "reboot intent root", "root_private", value)
    grant_root = Path(str(value["rebootRoot"])) / "grants"
    archive_intent = intent_root / "archive"
    archive_grant = grant_root / "archive"
    with os.scandir(intent_root) as it:
        for entry in it:
            if not entry.name.endswith(".prepared.json") or entry.is_symlink():
                continue
            path = Path(entry.path)
            payload, info = secure_read(path, "reboot prepared intent", MAX_CONFIG)
            try:
                prepared = json.loads(payload.decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                fail("reboot prepared intent is not valid JSON")
            grant = prepared.get("grant")
            if not isinstance(grant, str) or not _GRANT_RE.fullmatch(grant):
                fail("reboot prepared intent has an ambiguous grant reference")
            grant_path = grant_root / f"{grant}.json"
            try:
                grant_payload, g_info = secure_read(grant_path, "reboot grant", MAX_CONFIG)
            except PixelOperatorError as error:
                fail(f"reboot grant referenced by prepared intent is missing or unreadable: {error}")
            if not TESTING and g_info.st_uid != 0:
                fail("reboot grant must be root-owned")
            try:
                grant_record = json.loads(grant_payload.decode("utf-8"))
            except (ValueError, json.JSONDecodeError):
                fail("reboot grant is not valid JSON")
            consumed = bool(grant_record.get("consumed"))
            if consumed:
                _archive_entry(path, archive_intent, "pre-reboot-failed", ARCHIVE_MAX_KEEP)
                try:
                    (grant_root / f"{grant}.json").lstat()
                    _archive_entry(grant_root / f"{grant}.json", archive_grant, "pre-reboot-failed", ARCHIVE_MAX_KEEP)
                except FileNotFoundError:
                    pass
            else:
                os.unlink(path)
                fsync_dir(intent_root)


def reboot_execute(value, grant):
    grant_root = Path(str(value["rebootRoot"])) / "grants"
    require_dir(grant_root, "reboot grant root", "root_private", value)
    path, record = _load_grant(value, grant)
    if record.get("consumed"):
        fail("reboot grant was already consumed (replay refused)")
    if int(record.get("expiresAt", 0)) < int(time.time()):
        fail("reboot grant has expired")
    kind, units = _validate_grant(value, record)
    boot_id = read_boot_id()
    if boot_id != record.get("bootId"):
        fail("reboot grant boot id does not match the current boot id")
    intent_root = Path(str(value["rebootRoot"])) / "intent"
    require_dir(intent_root, "reboot intent root", "root_private", value)
    if _load_intent(value) is not None:
        fail("a committed reboot intent must be reconciled before a new execute")
    prepared_path = intent_root / f"{grant}.prepared.json"
    prepared = {
        "schemaVersion": 1, "operation": "pixel-operator-reboot-execute",
        "kind": kind, "units": units, "grant": grant,
        "evidenceSha256": record.get("evidenceSha256"), "manifestSha256": record.get("manifestSha256"),
        "bootIdBefore": boot_id, "state": "prepared", "preparedAt": int(time.time()),
    }
    # 1. Durably write a uniquely-bound prepared intent BEFORE consuming authority.
    _atomic_write_json(prepared_path, prepared)
    # 2. Only now consume the grant; a crash after this is recoverable (pre-reboot failure).
    _consume_grant(path, record)
    # 3. Commit the single durable state transition (no-replace prepared -> current.json).
    no_replace_rename(prepared_path, intent_root / "current.json")
    fsync_dir(intent_root)
    result = _run([reboot_bin(), "reboot"], check=False)
    if result.truncated:
        fail("reboot command output exceeded its hard bound")
    if result.returncode != 0:
        fail("reboot command failed")
    return {"schemaVersion": 1, "operation": "pixel-operator-reboot-execute", "grant": grant, "kind": kind, "units": units, "state": "accepted-asynchronous"}


def _load_intent(value):
    intent_root = Path(str(value["rebootRoot"])) / "intent"
    require_dir(intent_root, "reboot intent root", "root_private", value)
    intent_path = intent_root / "current.json"
    payload = secure_read(intent_path, "reboot intent", MAX_CONFIG, allow_missing=True)
    if payload is None:
        return None
    require_file(payload[1], "reboot intent", "root_private", value, executable=False)
    try:
        intent = json.loads(payload[0].decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("reboot intent is not valid JSON")
    if not isinstance(intent, dict) or intent.get("schemaVersion") != 1:
        fail("reboot intent schema is invalid")
    return intent


def reboot_status(value):
    _recover_prepared(value, int(time.time()))
    intent = _load_intent(value)
    current_boot = read_boot_id()
    if intent is None:
        return {"schemaVersion": 1, "operation": "pixel-operator-reboot-status", "intent": None, "bootIdSha256": digest(current_boot.encode("utf-8")), "bootChanged": False}
    boot_changed = intent.get("bootIdBefore") is not None and intent["bootIdBefore"] != current_boot
    return {"schemaVersion": 1, "operation": "pixel-operator-reboot-status", "intent": {"grant": intent.get("grant"), "kind": intent.get("kind"), "units": intent.get("units")}, "bootIdSha256": digest(current_boot.encode("utf-8")), "bootChanged": boot_changed}


def _archive_intent(value, intent):
    intent_root = Path(str(value["rebootRoot"])) / "intent"
    archive_root = intent_root / "archive"
    current_path = intent_root / "current.json"
    _archive_entry(current_path, archive_root, "reconciled", ARCHIVE_MAX_KEEP)


def _archive_grant(value, grant):
    if not isinstance(grant, str) or not _GRANT_RE.fullmatch(grant):
        return
    grant_root = Path(str(value["rebootRoot"])) / "grants"
    archive_root = grant_root / "archive"
    path = grant_root / f"{grant}.json"
    try:
        path.lstat()
    except FileNotFoundError:
        return
    _archive_entry(path, archive_root, "consumed", ARCHIVE_MAX_KEEP)


def reboot_reconcile(value):
    _recover_prepared(value, int(time.time()))
    intent = _load_intent(value)
    if intent is None:
        fail("reboot reconcile requires a committed intent")
    current_boot = read_boot_id()
    if intent.get("bootIdBefore") is None or intent["bootIdBefore"] == current_boot:
        fail("reboot reconcile: boot id did not change since intent")
    units = intent.get("units")
    kind = intent.get("kind")
    if not isinstance(units, list) or not units:
        fail("reboot reconcile intent units are invalid")
    units = sorted(set(units))
    for name in units:
        if not isinstance(name, str) or not UNIT_NAME_RE.fullmatch(name):
            fail("reboot reconcile intent unit is invalid")
    if not isinstance(kind, str) or kind not in REBOOT_KINDS:
        fail("reboot reconcile intent kind is invalid")
    if not _units_enabled_active(systemctl_bin(), units):
        fail("reboot reconcile: exact services did not resume")
    _archive_intent(value, intent)
    _archive_grant(value, intent.get("grant"))
    return {"schemaVersion": 1, "operation": "pixel-operator-reboot-reconcile", "kind": kind, "units": units, "state": "resumed", "bootChanged": True}


# ---- Root-custodied broker-byte transaction ------------------------------------
def _broker_root(value):
    root = Path(str(value["brokerBytes"]["backupRoot"]))
    _broker_require_directory(root, "broker bytes root", 0o700)
    _broker_require_directory(root / "snapshots", "broker snapshot root", 0o700)
    return root


def _broker_require_directory(path: Path, label: str, mode: int):
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"{label} must be a real directory")
    if stat.S_IMODE(info.st_mode) != mode:
        fail(f"{label} must be mode {mode:04o}")
    if not TESTING and (info.st_uid != 0 or info.st_gid != 0):
        fail(f"{label} must be root-owned")
    return info


def _broker_read_file(path: Path, label: str, mode: int, *, allow_missing=False):
    result = secure_read(path, label, MAX_UNIT_BYTES * 4, allow_missing=allow_missing)
    if result is None:
        return None
    payload, info = result
    if info.st_nlink != 1:
        fail(f"{label} must have exactly one link")
    if stat.S_IMODE(info.st_mode) != mode:
        fail(f"{label} must be mode {mode:04o}")
    if not TESTING and (info.st_uid != 0 or info.st_gid != 0):
        fail(f"{label} must be root-owned")
    return payload


def _broker_limb_config(value, limb):
    if limb not in BROKER_LIMBS:
        fail("broker-byte limb is outside the fixed authority")
    return value["brokerBytes"]["limbs"][limb]


def _broker_install_dir(value, limb):
    path = Path(str(_broker_limb_config(value, limb)["installDir"]))
    _broker_require_directory(path, f"{limb} broker install directory", 0o755)
    return path


def _broker_target_path(value, limb, relative, *, allow_missing_parent=False):
    allowed = {item[0] for item in BROKER_FILES[limb]}
    if relative not in allowed or relative.startswith("/") or ".." in relative.split("/"):
        fail("broker-byte target differs from the fixed file contract")
    install_dir = _broker_install_dir(value, limb)
    target = install_dir / relative
    parent = install_dir
    for part in relative.split("/")[:-1]:
        parent = parent / part
        try:
            parent.lstat()
        except FileNotFoundError:
            if allow_missing_parent:
                return target
            fail(f"broker-byte destination parent is missing: {limb}/{relative}")
        _broker_require_directory(parent, f"{limb} broker destination parent", 0o755)
    return target


def _broker_active_limbs(value):
    active = []
    for limb in BROKER_LIMBS:
        item = _broker_limb_config(value, limb)
        if item["enabled"] is not True:
            continue
        install_dir = Path(str(item["installDir"]))
        try:
            directory_info = install_dir.lstat()
        except FileNotFoundError:
            fail(f"enabled {limb} broker install directory is missing")
        if stat.S_ISLNK(directory_info.st_mode) or not stat.S_ISDIR(directory_info.st_mode):
            fail(f"{limb} broker install directory must be a real directory")
        _broker_require_directory(install_dir, f"{limb} broker install directory", 0o755)
        primary = install_dir / "broker.py"
        try:
            primary.lstat()
        except FileNotFoundError:
            fail(f"enabled {limb} broker.py is missing")
        _broker_read_file(primary, f"installed {limb} broker.py", 0o755)
        active.append(limb)
    return active


def _replace_durable_file(path: Path, payload: bytes, mode: int):
    """Atomically replace one fixed file beneath an already-verified root directory."""
    parent = path.parent
    tmp = parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}"
    try:
        current = path.lstat()
    except FileNotFoundError:
        current = None
    if current is not None and (stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1):
        fail(f"broker-byte destination is linked or not a single regular file: {path.name}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not TESTING:
            os.fchown(fd, 0, 0)
        write_all(fd, payload)
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
        fsync_dir(parent)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _broker_state_bytes(state):
    return (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _broker_state_path(value):
    return _broker_root(value) / "state.json"


def _broker_validate_state(value, state):
    expected = {
        "schemaVersion", "kind", "phase", "snapshotSha256", "runtimeTreeSha256", "limbs",
    }
    if not isinstance(state, dict) or set(state.keys()) != expected:
        fail("broker-byte state shape is invalid")
    if state["schemaVersion"] != 1 or state["kind"] != BROKER_STATE_KIND or state["phase"] not in BROKER_PHASES:
        fail("broker-byte state identity is invalid")
    for key in ("snapshotSha256", "runtimeTreeSha256"):
        if not isinstance(state[key], str) or not SHA256.fullmatch(state[key]):
            fail(f"broker-byte state {key} is invalid")
    limbs = state["limbs"]
    if not isinstance(limbs, list) or limbs != sorted(set(limbs)) or any(limb not in BROKER_LIMBS for limb in limbs):
        fail("broker-byte state limbs are invalid")
    for limb in limbs:
        if _broker_limb_config(value, limb)["enabled"] is not True:
            fail("broker-byte state names a disabled limb")
    return state


def _broker_load_state(value, *, allow_missing=False):
    path = _broker_state_path(value)
    result = _broker_read_file(path, "broker-byte state", 0o600, allow_missing=allow_missing)
    if result is None:
        return None
    try:
        state = json.loads(result.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        fail("broker-byte state is not valid JSON")
    return _broker_validate_state(value, state)


def _broker_write_state(value, *, phase, snapshot_sha, limbs):
    state = {
        "schemaVersion": 1,
        "kind": BROKER_STATE_KIND,
        "phase": phase,
        "snapshotSha256": snapshot_sha,
        "runtimeTreeSha256": value["runtimeTreeSha256"],
        "limbs": sorted(limbs),
    }
    _broker_validate_state(value, state)
    _replace_durable_file(_broker_state_path(value), _broker_state_bytes(state), 0o600)
    return state


def _broker_snapshot_bytes(manifest):
    return (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _broker_snapshot_path(value, snapshot_sha):
    return _broker_root(value) / "snapshots" / snapshot_sha


def _broker_snapshot_file(root, limb, relative):
    return root / limb / "files" / relative


def _broker_snapshot_manifest(value, limbs):
    manifest = {"schemaVersion": 1, "kind": BROKER_SNAPSHOT_KIND, "limbs": {}}
    payloads = {}
    for limb in limbs:
        install_dir = _broker_install_dir(value, limb)
        records = []
        for relative, mode, runtime_relative, installable in BROKER_FILES[limb]:
            target = _broker_target_path(value, limb, relative, allow_missing_parent=True)
            payload = _broker_read_file(target, f"installed {limb} {relative}", mode, allow_missing=True)
            if payload is None:
                record = {"path": relative, "mode": f"{mode:04o}", "state": "absent", "sha256": None}
            else:
                record = {"path": relative, "mode": f"{mode:04o}", "state": "present", "sha256": digest(payload)}
                payloads[(limb, relative)] = payload
            records.append(record)
        manifest["limbs"][limb] = records
    return manifest, payloads


def _broker_publish_snapshot(value, manifest, payloads):
    manifest_bytes = _broker_snapshot_bytes(manifest)
    snapshot_sha = digest(manifest_bytes)
    snapshots = _broker_root(value) / "snapshots"
    final = snapshots / snapshot_sha
    try:
        final.lstat()
        _broker_validate_snapshot(value, snapshot_sha, manifest["limbs"].keys())
        return snapshot_sha
    except FileNotFoundError:
        pass
    staging = snapshots / f".stage-{snapshot_sha}-{os.getpid()}-{secrets.token_hex(4)}"
    os.mkdir(staging, 0o700)
    if not TESTING:
        os.chown(staging, 0, 0)
    staging_info = staging.stat(follow_symlinks=False)
    staging_id = (staging_info.st_dev, staging_info.st_ino)
    try:
        for (limb, relative), payload in sorted(payloads.items()):
            parent = (_broker_snapshot_file(staging, limb, relative)).parent
            parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            for candidate in (staging / limb, staging / limb / "files", parent):
                os.chmod(candidate, 0o700)
                if not TESTING:
                    os.chown(candidate, 0, 0)
            _write_durable_file(_broker_snapshot_file(staging, limb, relative), payload, 0o600)
        _write_durable_file(staging / "snapshot.json", manifest_bytes, 0o600)
        _fsync_tree(staging)
        no_replace_rename(staging, final)
        fsync_dir(snapshots)
    except FileExistsError:
        pass
    finally:
        _cleanup_runtime_stage(staging, staging_id)
    _broker_validate_snapshot(value, snapshot_sha, manifest["limbs"].keys())
    return snapshot_sha


def _broker_validate_snapshot(value, snapshot_sha, expected_limbs):
    if not SHA256.fullmatch(snapshot_sha):
        fail("broker snapshot identity is invalid")
    root = _broker_snapshot_path(value, snapshot_sha)
    _broker_require_directory(root, "broker snapshot", 0o700)
    payload = _broker_read_file(root / "snapshot.json", "broker snapshot manifest", 0o600)
    if digest(payload) != snapshot_sha:
        fail("broker snapshot manifest differs from its content address")
    try:
        manifest = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        fail("broker snapshot manifest is not valid JSON")
    if not isinstance(manifest, dict) or set(manifest.keys()) != {"schemaVersion", "kind", "limbs"}:
        fail("broker snapshot manifest shape is invalid")
    if manifest["schemaVersion"] != 1 or manifest["kind"] != BROKER_SNAPSHOT_KIND:
        fail("broker snapshot manifest identity is invalid")
    limbs = manifest["limbs"]
    expected_limbs = sorted(expected_limbs)
    if not isinstance(limbs, dict) or sorted(limbs.keys()) != expected_limbs:
        fail("broker snapshot limbs differ from durable state")
    expected_paths = {"snapshot.json"}
    for limb in expected_limbs:
        records = limbs[limb]
        contract = BROKER_FILES[limb]
        if not isinstance(records, list) or len(records) != len(contract):
            fail(f"broker snapshot {limb} file contract is incomplete")
        for record, (relative, mode, runtime_relative, installable) in zip(records, contract):
            if not isinstance(record, dict) or set(record.keys()) != {"path", "mode", "state", "sha256"}:
                fail(f"broker snapshot {limb} record shape is invalid")
            if record["path"] != relative or record["mode"] != f"{mode:04o}" or record["state"] not in ("present", "absent"):
                fail(f"broker snapshot {limb} record differs from the fixed contract")
            stored = _broker_snapshot_file(root, limb, relative)
            if record["state"] == "present":
                if not isinstance(record["sha256"], str) or not SHA256.fullmatch(record["sha256"]):
                    fail(f"broker snapshot {limb} digest is invalid")
                stored_bytes = _broker_read_file(stored, f"broker snapshot {limb} {relative}", 0o600)
                if digest(stored_bytes) != record["sha256"]:
                    fail(f"broker snapshot {limb} {relative} hash mismatch")
                expected_paths.add(str(stored.relative_to(root)))
            else:
                if record["sha256"] is not None:
                    fail(f"broker snapshot {limb} absent digest must be null")
                try:
                    stored.lstat()
                    fail(f"broker snapshot {limb} absent file unexpectedly exists")
                except FileNotFoundError:
                    pass
    actual_paths = _scan_tree(root, "broker snapshot")
    if actual_paths != expected_paths:
        fail("broker snapshot tree does not equal its fixed manifest closure")
    return manifest


def _broker_runtime_payloads(value, limbs):
    runtime_manifest = verify_runtime_tree(value)
    payloads = {}
    runtime_root = Path(str(value["runtimePath"]))
    for limb in limbs:
        for relative, mode, runtime_relative, installable in BROKER_FILES[limb]:
            if not installable:
                continue
            if runtime_relative not in runtime_manifest:
                fail(f"runtime manifest omits fixed {limb} broker byte")
            payload = _broker_read_file(runtime_root / runtime_relative, f"runtime {limb} {relative}", 0o644)
            if digest(payload) != runtime_manifest[runtime_relative]:
                fail(f"runtime {limb} {relative} differs from its manifest")
            payloads[(limb, relative)] = (payload, mode)
    return payloads


def _broker_auxiliary_units(value, limb):
    if limb != "source":
        return []
    units = [BROKER_SOURCE_TIMER]
    if _broker_limb_config(value, "source")["directPathEnabled"]:
        units.append(BROKER_SOURCE_DIRECT_PATH)
    return units


def _broker_quiesce(value, limb):
    binpath = systemctl_bin()
    for unit in _broker_auxiliary_units(value, limb):
        _run([binpath, "stop", unit])
    _run([binpath, "stop", BROKER_UNITS[limb]])


def _broker_restart(value, limb):
    binpath = systemctl_bin()
    unit = BROKER_UNITS[limb]
    if limb == "source":
        _run([binpath, "start", unit])
        observed = _run([binpath, "show", unit, "-p", "Result", "--value"]).stdout.strip()
        if observed != "success":
            fail("source broker refresh failed after the byte update")
        for auxiliary in _broker_auxiliary_units(value, limb):
            _run([binpath, "start", auxiliary])
            state, error = _systemctl_state(binpath, "is-active", auxiliary)
            if error or state != "active":
                fail(f"source broker auxiliary unit did not resume: {auxiliary}")
        return
    _run([binpath, "restart", unit])
    state, error = _systemctl_state(binpath, "is-active", unit)
    if error or state != "active":
        fail(f"broker service did not resume: {unit}")


def _broker_remove_file(value, limb, relative):
    install_dir = _broker_install_dir(value, limb)
    target = _broker_target_path(value, limb, relative, allow_missing_parent=True)
    if target != install_dir / relative or install_dir not in target.parents:
        fail("broker-byte removal escaped the fixed install directory")
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        fail(f"broker-byte removal target is unsafe: {limb}/{relative}")
    if not TESTING and (info.st_uid != 0 or info.st_gid != 0):
        fail(f"broker-byte removal target is not root-owned: {limb}/{relative}")
    target.unlink()
    fsync_dir(target.parent)


def _broker_verify_candidate(value, limbs):
    payloads = _broker_runtime_payloads(value, limbs)
    files = 0
    for (limb, relative), (expected, mode) in sorted(payloads.items()):
        observed = _broker_read_file(_broker_target_path(value, limb, relative), f"installed {limb} {relative}", mode)
        if digest(observed) != digest(expected):
            fail(f"installed {limb} broker byte differs from the operator runtime: {relative}")
        files += 1
    return files


def _broker_verify_restored(value, manifest):
    files = 0
    for limb in sorted(manifest["limbs"].keys()):
        for record in manifest["limbs"][limb]:
            target = _broker_target_path(value, limb, record["path"], allow_missing_parent=record["state"] == "absent")
            if record["state"] == "present":
                observed = _broker_read_file(target, f"restored {limb} {record['path']}", int(record["mode"], 8))
                if digest(observed) != record["sha256"]:
                    fail(f"restored {limb} broker byte differs from its snapshot: {record['path']}")
                files += 1
            else:
                try:
                    target.lstat()
                    fail(f"restored absent broker byte still exists: {limb}/{record['path']}")
                except FileNotFoundError:
                    pass
    return files


def broker_bytes_backup(value):
    prior = _broker_load_state(value, allow_missing=True)
    limbs = sorted(_broker_active_limbs(value))
    if prior is not None:
        if prior["phase"] == "mutation-started":
            fail("broker-byte mutation is incomplete; restore is required before backup")
        if prior["runtimeTreeSha256"] == value["runtimeTreeSha256"]:
            manifest = _broker_validate_snapshot(value, prior["snapshotSha256"], prior["limbs"])
            if prior["limbs"] != limbs:
                fail("broker-byte enabled limbs changed within one operator runtime")
            if prior["phase"] == "installed":
                fail("broker-byte current runtime is already installed; a new runtime is required before backup")
            files = _broker_verify_restored(value, manifest)
            if prior["phase"] == "restored":
                _broker_write_state(
                    value, phase="backed-up", snapshot_sha=prior["snapshotSha256"], limbs=prior["limbs"],
                )
            return {
                "operation": "pixel-operator-broker-bytes-backup", "state": "backed-up",
                "limbs": prior["limbs"], "files": files, "snapshotSha256": prior["snapshotSha256"],
                "runtimeTreeSha256": value["runtimeTreeSha256"],
            }
        if prior["phase"] in ("backed-up", "restored"):
            manifest = _broker_validate_snapshot(value, prior["snapshotSha256"], prior["limbs"])
            _broker_verify_restored(value, manifest)
    manifest, payloads = _broker_snapshot_manifest(value, limbs)
    snapshot_sha = _broker_publish_snapshot(value, manifest, payloads)
    _broker_write_state(value, phase="backed-up", snapshot_sha=snapshot_sha, limbs=limbs)
    return {
        "operation": "pixel-operator-broker-bytes-backup", "state": "backed-up",
        "limbs": limbs, "snapshotSha256": snapshot_sha,
        "runtimeTreeSha256": value["runtimeTreeSha256"],
    }


def broker_bytes_install(value):
    state = _broker_load_state(value)
    if state["runtimeTreeSha256"] != value["runtimeTreeSha256"]:
        fail("broker-byte state is bound to a different operator runtime")
    if state["phase"] == "installed":
        files = _broker_verify_candidate(value, state["limbs"])
        return {
            "operation": "pixel-operator-broker-bytes-install", "state": "installed",
            "limbs": state["limbs"], "files": files,
            "snapshotSha256": state["snapshotSha256"], "runtimeTreeSha256": value["runtimeTreeSha256"],
        }
    if state["phase"] != "backed-up":
        fail("broker-byte install requires a fresh backup or restore reconciliation")
    _broker_validate_snapshot(value, state["snapshotSha256"], state["limbs"])
    payloads = _broker_runtime_payloads(value, state["limbs"])
    _broker_write_state(value, phase="mutation-started", snapshot_sha=state["snapshotSha256"], limbs=state["limbs"])
    for limb in state["limbs"]:
        _broker_quiesce(value, limb)
    for (limb, relative), (payload, mode) in sorted(payloads.items()):
        _replace_durable_file(_broker_target_path(value, limb, relative), payload, mode)
    for limb in state["limbs"]:
        _broker_restart(value, limb)
    files = _broker_verify_candidate(value, state["limbs"])
    _broker_write_state(value, phase="installed", snapshot_sha=state["snapshotSha256"], limbs=state["limbs"])
    return {
        "operation": "pixel-operator-broker-bytes-install", "state": "installed",
        "limbs": state["limbs"], "files": files,
        "snapshotSha256": state["snapshotSha256"], "runtimeTreeSha256": value["runtimeTreeSha256"],
    }


def broker_bytes_restore(value):
    state = _broker_load_state(value, allow_missing=True)
    if state is None:
        return {"operation": "pixel-operator-broker-bytes-restore", "state": "no-backup-no-mutation", "limbs": []}
    if state["runtimeTreeSha256"] != value["runtimeTreeSha256"]:
        fail("broker-byte state is bound to a different operator runtime")
    manifest = _broker_validate_snapshot(value, state["snapshotSha256"], state["limbs"])
    if state["phase"] == "backed-up":
        files = _broker_verify_restored(value, manifest)
        return {
            "operation": "pixel-operator-broker-bytes-restore", "state": "no-mutation",
            "limbs": state["limbs"], "files": files, "snapshotSha256": state["snapshotSha256"],
        }
    if state["phase"] == "restored":
        files = _broker_verify_restored(value, manifest)
        return {
            "operation": "pixel-operator-broker-bytes-restore", "state": "restored",
            "limbs": state["limbs"], "files": files, "snapshotSha256": state["snapshotSha256"],
        }
    if state["phase"] not in ("mutation-started", "installed"):
        fail("broker-byte restore state is invalid")
    for limb in state["limbs"]:
        _broker_quiesce(value, limb)
    snapshot_root = _broker_snapshot_path(value, state["snapshotSha256"])
    for limb in state["limbs"]:
        for record in manifest["limbs"][limb]:
            relative = record["path"]
            if record["state"] == "present":
                payload = _broker_read_file(
                    _broker_snapshot_file(snapshot_root, limb, relative),
                    f"broker snapshot {limb} {relative}", 0o600,
                )
                _replace_durable_file(_broker_target_path(value, limb, relative), payload, int(record["mode"], 8))
            else:
                _broker_remove_file(value, limb, relative)
    for limb in state["limbs"]:
        _broker_restart(value, limb)
    files = _broker_verify_restored(value, manifest)
    _broker_write_state(value, phase="restored", snapshot_sha=state["snapshotSha256"], limbs=state["limbs"])
    return {
        "operation": "pixel-operator-broker-bytes-restore", "state": "restored",
        "limbs": state["limbs"], "files": files,
        "snapshotSha256": state["snapshotSha256"], "runtimeTreeSha256": value["runtimeTreeSha256"],
    }


def broker_bytes_verify(value):
    state = _broker_load_state(value)
    if state["runtimeTreeSha256"] != value["runtimeTreeSha256"] or state["phase"] != "installed":
        fail("broker-byte verify requires the installed state for the current operator runtime")
    files = _broker_verify_candidate(value, state["limbs"])
    return {
        "operation": "pixel-operator-broker-bytes-verify", "state": "verified",
        "limbs": state["limbs"], "files": files,
        "snapshotSha256": state["snapshotSha256"], "runtimeTreeSha256": value["runtimeTreeSha256"],
    }


# ---- Durable content-free hash-addressed receipts ------------------------------
def _receipt_root(value) -> Path:
    root = Path(str(value["receiptRoot"]))
    root.mkdir(parents=True, exist_ok=True)
    require_dir(root, "receipt root", "root_private", value)
    return root


def write_receipt(value, label, inputs, outcome=None, error=None):
    """Write a content-free, root-owned, fsynced, hash-addressed receipt.

    The receipt records schema/timestamp/operation, exact operation inputs (kind/sha/grant),
    and an outcome identity or error digest only. It never contains raw stdout/stderr,
    journal lines, secrets, environment, or provider content. The payload is written to a
    root-private temp inode, fsynced, and published with true no-replace semantics, so a
    partial receipt can never surface at the final content-addressed name.
    """
    root = _receipt_root(value)
    data = {
        "schemaVersion": 1,
        "timestamp": time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        "operation": label,
        "inputs": inputs,
        "exitCode": 0 if error is None else 1,
    }
    if outcome is not None:
        data["outcome"] = outcome.get("operation", outcome.get("state", label))
        data["outcomeSha256"] = digest(json.dumps(outcome, sort_keys=True, separators=(",", ":")).encode())
    if error is not None:
        data["errorSha256"] = digest(str(error).encode())
    payload = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    sha256, name = _publish_content_addressed(root, payload, value, "receipt")
    return name, sha256


# ---- Operation dispatch --------------------------------------------------------
def _operation_meta(operation):
    if operation[0] == "broker-bytes":
        return (f"pixel-operator-broker-bytes-{operation[1]}", {"action": operation[1]})
    if operation[0] == "bundle":
        if operation[1] == "rollback":
            return ("pixel-operator-bundle-rollback", {"kind": operation[2], "from": operation[3], "to": operation[4]})
        return (f"pixel-operator-bundle-{operation[1]}", {"kind": operation[2], "manifestSha256": operation[3]})
    if operation[0] == "service":
        return (f"pixel-operator-service-{operation[1]}", {"kind": operation[2], "manifestSha256": operation[3]})
    if operation[0] == "reboot":
        if operation[1] == "prepare":
            return ("pixel-operator-reboot-prepare", {"kind": operation[2], "evidenceSha256": operation[3]})
        if operation[1] == "execute":
            return ("pixel-operator-reboot-execute", {"grant": operation[2]})
        return (f"pixel-operator-reboot-{operation[1]}", {})
    fail("unsupported pixel operator operation")


def _dispatch(value, operation):
    if operation[0] == "broker-bytes":
        if operation[1] == "backup":
            return broker_bytes_backup(value)
        if operation[1] == "install":
            return broker_bytes_install(value)
        if operation[1] == "restore":
            return broker_bytes_restore(value)
        if operation[1] == "verify":
            return broker_bytes_verify(value)
    if operation[0] == "bundle":
        if operation[1] == "rollback":
            return bundle_rollback(value, operation[2], operation[3], operation[4])
        kind, sha = operation[2], operation[3]
        if operation[1] == "install":
            return bundle_install(value, kind, sha)
        if operation[1] == "inspect":
            return bundle_inspect(value, kind, sha)
        if operation[1] == "status":
            bundle_inspect(value, kind, sha)
            return service_status(value, kind, sha)
        if operation[1] == "activate":
            return bundle_activate(value, kind, sha)
        if operation[1] == "remove":
            return bundle_remove(value, kind, sha)
    if operation[0] == "service":
        kind, sha = operation[2], operation[3]
        if operation[1] == "status":
            return service_status(value, kind, sha)
    if operation[0] == "reboot":
        if operation[1] == "prepare":
            return reboot_prepare(value, operation[2], operation[3])
        if operation[1] == "execute":
            return reboot_execute(value, operation[2])
        if operation[1] == "status":
            return reboot_status(value)
        if operation[1] == "reconcile":
            return reboot_reconcile(value)
    fail("unsupported pixel operator operation")


def run_operation(operation):
    value = load_config()
    label, inputs = _operation_meta(operation)
    try:
        result = _dispatch(value, operation)
    except Exception as error:
        try:
            write_receipt(value, label, inputs, error=error)
        except Exception:
            pass
        raise
    write_receipt(value, label, inputs, outcome=result)
    return result


# ---- Staged validation for provisioning (not in the transport grammar) --------
def _validate_staging_bytes(config_path: str, runtime_dir: str):
    path = Path(config_path)
    if not path.is_absolute() or path == Path("/"):
        fail("staging config path must be absolute and non-root")
    payload, info = secure_read(path, "staging pixel operator config", MAX_CONFIG)
    if info.st_nlink > 1:
        fail("staging config must not be a hardlink")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("staging config is not valid JSON")
    validate_config(value)
    staged_value = dict(value)
    staged_value["runtimePath"] = str(Path(runtime_dir).resolve())
    verify_runtime_tree(staged_value, check_owner=False)
    return True


def validate_staging(config_path: str, runtime_dir: str):
    """Root-only validation of a staged operator config + runtime snapshot before install.

    This is deliberately outside the transport grammar and sudoers surface so the transport
    can never pass an arbitrary config path. It validates the staged config schema, that the
    staged runtime-manifest.json SHA equals the configured tree SHA, and that the staged
    runtime tree bytes match the manifest (ownership is established at install time).
    """
    if not TESTING:
        if getattr(os, "geteuid", lambda: 1)() != 0:
            fail("staging validation must run as root")
    return _validate_staging_bytes(config_path, runtime_dir)


def _set_directory_mode_exact(path, mode, label):
    """Set one real directory's exact mode through a stable no-follow descriptor.

    The lstat/open/fstat identity check makes replacement between inspection and open a
    fail-closed error. fchmod then applies only to that verified inode, never to a later
    path resolution. The descriptor fsync makes the metadata transition durable before
    callers publish or rely on the directory.
    """
    path = Path(path)
    raw = path.lstat()
    if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
        fail(f"{label} must be a real directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (raw.st_dev, raw.st_ino)
        ):
            fail(f"{label} changed during secure open")
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


def _ensure_scaffold(path, label, which, value):
    """Create or verify a managed scaffold path with an exact boundary owner/mode.

    Missing intermediate components are created root:root 0755. The leaf itself is created
    with the boundary ownership and 0700 (or 0755 for a shared root boundary). A preexisting
    leaf or component that is a symlink, wrong-owner, or wrong-mode fails closed and is
    never recursively chowned.
    """
    path = Path(path)
    if not path.is_absolute() or path == Path("/"):
        fail(f"{label} must be an absolute non-root path")
    probe = Path("/")
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        probe = probe / part
        is_leaf = index == len(parts) - 1
        try:
            raw = probe.lstat()
        except FileNotFoundError:
            if is_leaf:
                owner_uid, owner_gid, private = boundary(value, which)
                leaf_mode = 0o700 if private else 0o755
                os.mkdir(probe, leaf_mode)
                _chown(probe, owner_uid, owner_gid)
                _set_directory_mode_exact(probe, leaf_mode, label)
            else:
                os.mkdir(probe, 0o755)
                _chown(probe, 0, 0)
                _set_directory_mode_exact(probe, 0o755, f"{label} component {probe}")
            fsync_dir(probe.parent)
            continue
        if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
            fail(f"{label} component {probe} must be a real directory")
        if is_leaf:
            owner_uid, owner_gid, private = boundary(value, which)
            if TESTING:
                mask = 0o077 if private else 0o022
                if raw.st_mode & mask:
                    fail(f"{label} ownership or mode is invalid (refusing to recursively chown)")
            elif not owner_mode_ok(uid=raw.st_uid, gid=raw.st_gid, mode=raw.st_mode,
                                   owner_uid=owner_uid, owner_gid=owner_gid, private=private):
                fail(f"{label} ownership or mode is invalid (refusing to recursively chown)")
    return path


def provision_scaffolding(value):
    """Create the exact inbox/bundle/reboot scaffolding from the validated final config.

    Rejects a preexisting link/wrong-owner/wrong-mode path and never recursively chowns an
    ambiguous preexisting tree.
    """
    _ensure_scaffold(Path(str(value["inboxRoot"])), "inbox root", "inbox", value)
    _ensure_scaffold(Path(str(value["bundleRoot"])), "bundle root", "bundle", value)
    for kind in KINDS:
        _ensure_scaffold(Path(str(value["bundleRoot"])) / kind, f"bundle kind root {kind}", "bundle", value)
    _ensure_scaffold(Path(str(value["receiptRoot"])), "receipt root", "root_private", value)
    _ensure_scaffold(Path(str(value["rebootRoot"])), "reboot root", "root_private", value)
    broker_root = Path(str(value["brokerBytes"]["backupRoot"]))
    _ensure_scaffold(broker_root, "broker bytes root", "root_private", value)
    _ensure_scaffold(broker_root / "snapshots", "broker snapshot root", "root_private", value)
    for sub in ("evidence", "grants", "intent"):
        _ensure_scaffold(Path(str(value["rebootRoot"])) / sub, f"reboot {sub} root", "root_private", value)
    _ensure_scaffold(Path(str(value["rebootRoot"])) / "intent" / "archive", "reboot intent archive", "root_private", value)
    return True


def _write_runtime_file(target: Path, data: bytes):
    # Directories carry the 0755 contract intent; the mkdir mode argument is umask-masked,
    # so the directory tree is normalized exactly before publication (see
    # _normalize_runtime_tree_modes). The staging root stays 0700 until then, so an
    # incomplete tree is never traversable by another identity.
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    # Root-owned, non-secret runtime source is intentionally readable by the constrained
    # service identity.
    # codeql[py/overly-permissive-file]
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
    try:
        write_all(fd, data)
        # os.open's mode argument is umask-masked; descriptor-safe fchmod is the sole
        # regular-file mode setter and enforces the exact 0644 contract even under a
        # restrictive umask (0077).
        os.fchmod(fd, 0o644)
        os.fsync(fd)
    finally:
        os.close(fd)


def _normalize_runtime_tree_modes(staging: Path):
    """Normalize every staging directory to the exact 0755 contract, nested-first/root-last.

    Regular-file modes are deliberately not touched here: the descriptor-safe
    os.fchmod(fd, 0o644) in _write_runtime_file is the sole regular-file mode setter. Runs
    only after every byte is written and before fsync/verification/publication; nested
    directories are normalized first and the staging root last, so the tree becomes
    service-traversable exactly when the whole tree already satisfies the contract.
    """
    directories = []
    for dirpath, _dirnames, _filenames in os.walk(staging):
        directories.append(dirpath)
    for directory in reversed(directories):
        _set_directory_mode_exact(directory, 0o755, f"runtime directory {directory}")


def _fsync_tree(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            fd = os.open(os.path.join(dirpath, name), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fsync_dir(Path(dirpath))


def _cleanup_runtime_stage(staging: Path, staging_id):
    try:
        raw = staging.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(raw.st_mode) or stat.S_ISLNK(raw.st_mode):
        return
    if (raw.st_dev, raw.st_ino) != staging_id:
        return
    shutil.rmtree(staging)


def _verify_runtime_dir(dirpath: Path, manifest_bytes: bytes, tree_sha: str):
    """Require a runtime dir exactly equal its manifest closure with matching SHAs.

    Also fails closed on exact contract modes: the root and every nested directory must be
    ordinary non-symlink directories with mode exactly 0755, and every manifest/declared
    regular file (runtime-manifest.json plus declared files) exactly 0644. A tree installed
    under a bad umask or later drifted in mode is never accepted idempotently, and this
    verifier never repairs a published immutable runtime.
    """
    if digest(manifest_bytes) != tree_sha:
        fail("runtime manifest does not match the configured tree SHA")
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("runtime manifest is not valid JSON")
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1 or set(manifest.keys()) != {"schemaVersion", "files"}:
        fail("runtime manifest shape is invalid")
    files = manifest["files"]
    if not isinstance(files, dict) or not files:
        fail("runtime manifest must declare files")
    expected = set(files.keys()) | {"runtime-manifest.json"}
    actual = _scan_tree(dirpath, "runtime tree")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        fail(f"runtime tree does not equal its manifest closure (missing={missing}, extra={extra})")
    root_raw = dirpath.lstat()
    if stat.S_ISLNK(root_raw.st_mode) or not stat.S_ISDIR(root_raw.st_mode):
        fail("runtime tree root must be a real directory")
    if stat.S_IMODE(root_raw.st_mode) != 0o755:
        fail("runtime tree root mode must be exactly 0755")
    for current, _dirnames, filenames in os.walk(dirpath):
        rel_current = os.path.relpath(current, dirpath)
        if rel_current != ".":
            raw = os.lstat(current)
            if stat.S_ISLNK(raw.st_mode) or not stat.S_ISDIR(raw.st_mode):
                fail(f"runtime tree must not contain a non-directory: {rel_current}")
            if stat.S_IMODE(raw.st_mode) != 0o755:
                fail(f"runtime directory mode must be exactly 0755: {rel_current}")
        for name in filenames:
            rel = os.path.relpath(os.path.join(current, name), dirpath)
            raw = os.lstat(os.path.join(current, name))
            if stat.S_ISLNK(raw.st_mode) or not stat.S_ISREG(raw.st_mode):
                fail(f"runtime tree must not contain a non-regular file: {rel}")
            if stat.S_IMODE(raw.st_mode) != 0o644:
                fail(f"runtime file mode must be exactly 0644: {rel}")
    mb, info = secure_read(dirpath / "runtime-manifest.json", "runtime tree manifest", MAX_CONFIG)
    if digest(mb) != tree_sha:
        fail("installed runtime manifest does not match the configured tree SHA")
    for rel, expected_sha in files.items():
        if not isinstance(rel, str) or rel.startswith("/") or ".." in rel.split("/"):
            fail("runtime tree manifest contains an unsafe relative path")
        if not isinstance(expected_sha, str) or not SHA256.fullmatch(expected_sha):
            fail("runtime tree manifest contains an invalid SHA")
        data, finfo = secure_read(dirpath / rel, f"runtime file {rel}", MAX_UNIT_BYTES * 4)
        if digest(data) != expected_sha:
            fail(f"runtime file {rel} differs from its tree manifest")
    return len(files)


def provision_install_runtime(staged_runtime, runtime_parent):
    """Atomically install a versioned runtime with Linux no-replace semantics.

    Builds the full tree in a root-only sibling staging directory, rejects links/special/
    extra/undeclared entries, verifies the exact manifest closure and fsyncs, then publishes
    with no-replace semantics so a partial final path is never selected. Prior versions are
    retained and re-provisioning the same tree SHA is idempotent.
    """
    staged = Path(staged_runtime)
    parent = Path(runtime_parent)
    _ensure_scaffold(parent, "runtime parent", "root", _root_private_value())
    mb, info = secure_read(staged / "runtime-manifest.json", "runtime tree manifest", MAX_CONFIG)
    tree_sha = digest(mb)
    final = parent / tree_sha
    try:
        final.lstat()
        _verify_runtime_dir(final, mb, tree_sha)
        return str(final)
    except FileNotFoundError:
        pass
    staging = parent / (f".stage-{tree_sha}-{os.getpid()}-{secrets.token_hex(4)}")
    os.mkdir(staging, 0o700)
    staging_stat = staging.stat(follow_symlinks=False)
    staging_id = (staging_stat.st_dev, staging_stat.st_ino)
    try:
        manifest = json.loads(mb.decode("utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1 or set(manifest.keys()) != {"schemaVersion", "files"}:
            fail("runtime manifest shape is invalid")
        declared = set(manifest["files"].keys())
        expected = declared | {"runtime-manifest.json"}
        actual = _scan_tree(staged, "staged runtime")
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            fail(f"staged runtime tree does not equal its manifest closure (missing={missing}, extra={extra})")
        _write_runtime_file(staging / "runtime-manifest.json", mb)
        for rel in sorted(declared):
            data, finfo = secure_read(staged / rel, f"runtime file {rel}", MAX_UNIT_BYTES * 4)
            _write_runtime_file(staging / rel, data)
        # The immutable root-owned runtime tree must be traversable by the constrained
        # service identity: regular files are exact 0644 via descriptor-safe fchmod at
        # creation, and every directory is normalized to exact 0755 (nested-first, root
        # last) only after all bytes are written and before fsync/verification.
        # codeql[py/overly-permissive-file]
        _normalize_runtime_tree_modes(staging)
        _fsync_tree(staging)
        _verify_runtime_dir(staging, mb, tree_sha)
        no_replace_rename(staging, final)
        fsync_dir(parent)
    except FileExistsError:
        pass
    finally:
        _cleanup_runtime_stage(staging, staging_id)
    _verify_runtime_dir(final, mb, tree_sha)
    return str(final)


def _write_durable_file(path, data: bytes, mode: int):
    """Write a file durably with write-all, fsync(file+parent), no-follow and no-replace."""
    path = Path(path)
    parent = path.parent
    tmp = parent / (f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        if not TESTING:
            os.fchown(fd, 0, 0)
        write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    no_replace_rename(tmp, path)
    fsync_dir(parent)


def provision_build_final_config(staged_config_path, runtime_dir, dest_stage_path):
    """Build the exact final config bytes once (runtimePath rewritten) and write them durably.

    The stage file is written root-private (0600) with write-all, fsync(file+parent),
    no-follow and no-replace; the provisioning script then installs those exact bytes and
    compares the stage to the destination.
    """
    staged = Path(staged_config_path)
    payload, info = secure_read(staged, "staging pixel operator config", MAX_CONFIG)
    if info.st_nlink > 1:
        fail("staging config must not be a hardlink")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        fail("staging config is not valid JSON")
    validate_config(value)
    value["runtimePath"] = str(Path(runtime_dir).resolve())
    final_bytes = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _write_durable_file(dest_stage_path, final_bytes, 0o600)
    return final_bytes


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if getattr(os, "geteuid", lambda: 1)() != 0 and not TESTING:
        fail("pixel operator actions must run as root")
    if argv == ["--validate-config"]:
        load_config()
        print(json.dumps({"schemaVersion": 1, "valid": True}, sort_keys=True))
        return 0
    if argv == ["--validate-runtime"]:
        value = load_config()
        files = verify_runtime_tree(value)
        print(json.dumps({"schemaVersion": 1, "runtimeValid": True, "files": len(files)}, sort_keys=True))
        return 0
    if len(argv) == 3 and argv[0] == "--validate-staging":
        validate_staging(argv[1], argv[2])
        print(json.dumps({"schemaVersion": 1, "stagingValid": True}, sort_keys=True))
        return 0
    if len(argv) == 2 and argv[0] == "--provision-scaffolding":
        payload, _ = secure_read(Path(argv[1]), "staging pixel operator config", MAX_CONFIG)
        value = json.loads(payload.decode("utf-8"))
        validate_config(value)
        provision_scaffolding(value)
        print(json.dumps({"schemaVersion": 1, "scaffoldingProvisioned": True}, sort_keys=True))
        return 0
    if len(argv) in (3, 4) and argv[0] == "--install-runtime":
        runtime = provision_install_runtime(argv[1], argv[2])
        if len(argv) == 4:
            _write_durable_file(Path(argv[3]), (str(runtime) + "\n").encode("utf-8"), 0o600)
        print(json.dumps({"schemaVersion": 1, "runtimePath": runtime}, sort_keys=True))
        return 0
    if len(argv) == 4 and argv[0] == "--build-final-config":
        provision_build_final_config(argv[1], argv[3], argv[2])
        print(json.dumps({"schemaVersion": 1, "finalConfigBuilt": True}, sort_keys=True))
        return 0
    from pixel_release_grammar import validate_operation
    operation = validate_operation(argv)
    result = run_operation(operation)
    print(json.dumps({"schemaVersion": 1, **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PixelOperatorError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"pixel-operator: {error}", file=sys.stderr)
        raise SystemExit(1)
