#!/usr/bin/env python3
"""Terminal-only clean migration contract from supported legacy Pixel 3.2.2 into a separately
installed current target release. This is a terminal-only clean migration: there is no in-place
3.2.2 update path.

The migrate-legacy-clean command is explicitly terminal-only: it never claims or enables an
in-place 3.2.2 update, and there is no update-rollback from a 3.2.2 source (the authenticated
backup is the single rollback boundary). It provides a phased, fail-closed review/rehearsal/
finalization tool that composes existing trusted primitives (authenticated restore validation,
restore --rehearse, ./pixel verify, runtime attestation, Git source identity). Plan and rehearsal
use the exact legacy 3.2.2 root contract that the authenticated backup declares, and bind it to
the executing target source and validator so it cannot change between phases. The activate
subcommand is a self-contained exact transaction: it reserves every output/receipt/journal path
and validates the pre-prepared target deployment/frozen configuration before any live mutation, then
executes the trusted target restore in an explicit migration-only mode that accepts exactly the
authenticated 3.2 root contract (standard restore is never widened), restarts services and keeps the restore's
old-path rollback state armed (services may run through the target verify, which can require them)
through the target verify, runtime attestation validation, restore-receipt finalization, and
completion-receipt write, and only then commits and deletes the old state. On any failure it writes a truthful non-pass receipt that
records whether live mutation was rolled back, and never deletes the authenticated backup. No
deployment configuration is regenerated: the target configuration is pre-prepared and frozen, and
activation proves the frozen files are byte-identical before and after the legacy data swap. The
separate finalize subcommand remains available for operators who perform the restore as an
explicit step and only want evidence finalization.

This module uses only the Python standard library. Supported clean hosts install python3 only; no
third-party runtime dependency (including jsonschema) is permitted here.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable


MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_BACKUP_BYTES = 40 * 1024 * 1024 * 1024
SIGNATURE_MAX_BYTES = 16 * 1024
CHECKSUM_MAX_BYTES = 256
SUPPORT_MAX_BYTES = 1024 * 1024
MANIFEST_READ_TIMEOUT = 900
MANIFEST_MAX_OUTPUT = 1024 * 1024
RELEASE_TREE_SHA_TIMEOUT = 120
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")
RELEASE_TREE_LINE_RE = re.compile(r"^[a-f0-9]{64}\n$")
VERSION_RE = re.compile(r"^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$")
DATETIME_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
SSH_SIG_LINE_RE = re.compile(r"[A-Za-z0-9+/]+={0,2}")
SSH_SIG_BEGIN = "-----BEGIN SSH SIGNATURE-----"
SSH_SIG_END = "-----END SSH SIGNATURE-----"

SOURCE_PIXEL = "3.2.2"
_RELEASE_VERSION_MAX_BYTES = 64
_RELEASE_MANIFEST_MAX_BYTES = 1024 * 1024


def _release_identity(entry) -> tuple:
    """Stable release-identity metadata proven equal across pre fstat, post fstat, and path
    lstat: device, inode, regular file type, single link, size, mtime_ns, ctime_ns."""
    return (entry.st_dev, entry.st_ino, stat.S_ISREG(entry.st_mode), entry.st_nlink,
            entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)


def _read_release_file(repo: Path, name: str, *, maximum: int) -> bytes:
    """Secure in-tree release-identity read, mirroring the module's file-custody style.

    Opens with O_NOFOLLOW + O_CLOEXEC, fstats a regular single-link bounded file, then loops
    os.read until exactly the pre-read st_size bytes are captured (rejecting early EOF and
    any growth), fstats the still-open descriptor again, and finally lstat-verifies the path.
    The pre fstat, post fstat, and path lstat must all agree on the same stable identity
    metadata - device, inode, regular file type, single link, size, and mtime/ctime - so an
    in-place truncate/replace/write race fails closed, not only a pathname swap. This proves
    only bounded single-link regular-file descriptor-path identity/coherence; it does not by
    itself prove ownership or parent-directory custody, and it does not authenticate the
    source or release tree - authenticity/ownership/directory custody come from the later
    exact-source and release-tree gates."""
    if not (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_CLOEXEC")):
        raise RuntimeError("platform lacks O_NOFOLLOW/O_CLOEXEC release-identity guarantees")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(repo / name, flags)
    except OSError as exc:
        raise RuntimeError(f"release {name} is unavailable or unsafe") from exc
    try:
        pre = os.fstat(descriptor)
        if not stat.S_ISREG(pre.st_mode) or pre.st_nlink != 1 \
                or not 0 <= pre.st_size <= maximum:
            raise RuntimeError(f"release {name} is not a bounded single-link regular file")
        payload = bytearray()
        while len(payload) < pre.st_size:
            chunk = os.read(descriptor, pre.st_size - len(payload))
            if not chunk:
                raise RuntimeError(f"release {name} ended early during read")
            payload += chunk
        if os.read(descriptor, 1):
            raise RuntimeError(f"release {name} grew during read")
        post = os.fstat(descriptor)
        if _release_identity(pre) != _release_identity(post):
            raise RuntimeError(f"release {name} changed during read")
        current = (repo / name).lstat()
        if _release_identity(post) != _release_identity(current):
            raise RuntimeError(f"release {name} changed during read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _parse_release_manifest(payload: bytes) -> dict[str, Any]:
    """Strict release-manifest JSON that fails closed on duplicate keys at any depth and on
    non-finite numbers, so a crafted manifest cannot silently bind a second value."""
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise RuntimeError("release manifest contains duplicate fields")
            value[key] = child
        return value
    try:
        value = json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _v: (_ for _ in ()).throw(
                RuntimeError("release manifest contains a non-finite number")),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("release manifest is not valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("release manifest is not an object")
    return value


def _load_release_contract(repo: Path | None = None) -> str:
    """Derive the exact legacy migration target from the COHERENT in-tree release identity
    (VERSION == RELEASE-MANIFEST pixel == legacy target) of the release tree that physically
    contains this tool.

    This loader establishes coherence only, never authenticity: incoherent field or source
    substitution fails closed, while a coherent future signed release identity is accepted
    only as a release identity. Authenticity and custody come from the exact clean/signed
    release and release-tree gates, never from these agreeing raw files. No CLI/env override,
    basename inference, loose semver, or fallback."""
    repo = repo or Path(__file__).resolve().parents[1]
    try:
        version = _read_release_file(repo, "VERSION", maximum=_RELEASE_VERSION_MAX_BYTES) \
            .decode("ascii").strip()
    except RuntimeError as exc:
        raise RuntimeError("release VERSION is unavailable or unsafe") from exc
    except UnicodeDecodeError as exc:
        raise RuntimeError("release VERSION is not ascii") from exc
    if VERSION_RE.fullmatch(version) is None:
        raise RuntimeError("release VERSION is malformed")
    manifest = _parse_release_manifest(
        _read_release_file(repo, "RELEASE-MANIFEST.json", maximum=_RELEASE_MANIFEST_MAX_BYTES))
    legacy = manifest.get("legacyCleanMigration")
    v1 = legacy.get("v1Contract") if isinstance(legacy, dict) else None
    if not isinstance(v1, dict):
        raise RuntimeError("release manifest has no legacy clean migration contract")
    source = v1.get("sourcePixel")
    target = v1.get("targetPixel")
    if source != SOURCE_PIXEL:
        raise RuntimeError("release manifest legacy source does not match the contract")
    if not isinstance(target, str) or VERSION_RE.fullmatch(target) is None:
        raise RuntimeError("release manifest legacy target is malformed")
    if manifest.get("pixel") != version:
        raise RuntimeError("release manifest version does not match VERSION")
    if target != version:
        raise RuntimeError("release manifest legacy target does not match VERSION")
    return target


TARGET_PIXEL = _load_release_contract()
MIGRATION_JOURNAL_CUSTODY = Path("/var/lib/pixel-migration-journals")
MIGRATION_JOURNAL_BASENAME = re.compile(r"^[A-Za-z0-9._-]+$")
MINIMUM_UPGRADABLE = "4.0.0"
QUALIFICATION_MODE = "forward"
RESTORE_RECEIPT_KIND = "pixel-restore-receipt"
RUNTIME_ATTESTATION_KIND = "pixel-runtime-attestation"
ATTESTATION_TOLERANCE = timedelta(seconds=120)

BOUNDARIES = {
    "plan": "Content-free clean-migration plan only. No bootstrap, restore, apply, replace, or rollback is performed by this command.",
    "rehearsal": "Content-free clean-migration rehearsal receipt. Restore was rehearsed into an explicit new non-live root; no live state was changed.",
    "completion": "Content-free clean-migration completion receipt. It binds the authenticated backup, restore receipt, runtime attestation, and Git source identity to the exact migration plan and rehearsal. No in-place 3.2.2 update or update-rollback exists; the authenticated backup is the single rollback boundary.",
    "restore": "Content-free confirmed restore receipt. The backup was authentically validated, transactionally swapped, verified live, and automatic rollback was armed. No paths, identities, credentials, host, provider, or model identity, and no user content are included.",
    "activate": "Content-free self-contained clean-migration activation transaction. It performs the authenticated restore in an explicit migration-only mode that accepts exactly the authenticated 3.2 root contract, proves the pre-prepared target configuration is frozen and unchanged before and after the legacy data swap, verifies the live deployment and runtime attestation while rollback is still armed, and writes the terminal completion receipt only after committing. No in-place 3.2.2 update or update-rollback exists; the authenticated backup is the single rollback boundary.",
}

PRIVACY = {
    "pathsIncluded": False,
    "hostIdentityIncluded": False,
    "credentialsIncluded": False,
    "signerIdentityIncluded": False,
    "modelProviderIdentityIncluded": False,
    "userContentIncluded": False,
}

RESTORE_AUDIT_KEYS = [
    "status", "schemaVersion", "members", "roots", "uncompressedBytes", "pixelVersion",
    "rootsSha256",
]

LEGACY_RESTORE_AUDIT_KEYS = [
    "status", "schemaVersion", "members", "roots", "uncompressedBytes", "pixelVersion",
]

EXPECTED_CONNECTORS = {"email", "calendar", "social", "web", "operations", "frontier"}

RESTORE_RECEIPT_KEYS = [
    "schemaVersion", "kind", "status", "mode", "verified", "automaticRollbackArmed",
    "knowledgeDeletionReconciled", "historicalKeyWrappingRemoved", "backupSha256",
    "sourcePixel", "targetPixel", "receiptSha256", "generatedAt", "privacy", "boundary",
]

PLAN_KEYS = [
    "schemaVersion", "operation", "sourcePixel", "installedPixel", "targetPixel",
    "backupSha256", "backupAudit", "releasePolicy", "sourceCommit", "sourceTree",
    "backupRootsSha256", "planSha256", "generatedAt", "privacy", "boundary",
]

REHEARSAL_KEYS = [
    "schemaVersion", "operation", "sourcePixel", "targetPixel", "planSha256",
    "backupSha256", "sourceCommit", "sourceTree", "rehearsalRootCreated",
    "liveStateChanged", "backupRootsSha256", "rehearsalSha256", "generatedAt", "privacy", "boundary",
]

COMPLETION_KEYS = [
    "schemaVersion", "operation", "sourcePixel", "targetPixel", "activeRelease",
    "planSha256", "rehearsalSha256", "backupSha256", "restoreReceiptSha256",
    "runtimeAttestationSha256", "sourceCommit", "sourceTree", "verified",
    "completionSha256", "generatedAt", "privacy", "boundary",
]

RUNTIME_ATTESTATION_KEYS = [
    "schemaVersion", "kind", "status", "verifiedAt", "pixel", "source", "qualification",
    "release", "configuration", "runtime", "profiles", "connectors", "boundary",
]


class MigrationError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def self_hash(value: dict[str, Any], hash_key: str) -> str:
    body = {key: child for key, child in value.items() if key != hash_key}
    return sha256_hex(canonical(body))


def parse_json(payload: bytes, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, child in pairs:
            if key in value:
                raise MigrationError(f"{label} contains duplicate fields")
            value[key] = child
        return value

    try:
        return json.loads(
            payload,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                MigrationError(f"{label} contains a non-finite number")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"{label} is not valid JSON") from exc


def read_bytes(path: Path, *, private: bool = False, maximum: int = MAX_JSON_BYTES) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MigrationError("migration input is unavailable or unsafe") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 <= info.st_size <= maximum:
            raise MigrationError("migration input is not a bounded single-link file")
        if private and os.name != "nt" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise MigrationError("private migration evidence must be owner-bound mode 0600")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            payload = handle.read(maximum + 1)
        if len(payload) > maximum:
            raise MigrationError("migration input is oversized")
        current = path.lstat()
        if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
            raise MigrationError("migration input changed during read")
        return payload
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_json(path: Path, label: str, *, private: bool = False) -> dict[str, Any]:
    value = parse_json(read_bytes(path, private=private), label)
    if not isinstance(value, dict):
        raise MigrationError(f"{label} must be an object")
    return value


def sha256_file(path: Path, label: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise MigrationError(f"{label} is unavailable or unsafe") from exc
    digest = hashlib.sha256()
    total = 0
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BACKUP_BYTES:
            raise MigrationError(f"{label} is not a bounded single-link file")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BACKUP_BYTES:
                raise MigrationError(f"{label} is oversized")
            digest.update(chunk)
        current = path.lstat()
        if current.st_dev != info.st_dev or current.st_ino != info.st_ino or not stat.S_ISREG(current.st_mode):
            raise MigrationError(f"{label} changed during hashing")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _verified_output_parent(path: Path) -> Path:
    """Return an existing owner-only 0700 non-symlink parent, creating at most one new leaf."""
    if not path.is_absolute() or path == Path(path.anchor):
        raise MigrationError("migration output must be an absolute non-root path")
    parent = path.parent
    try:
        if parent.exists() or parent.is_symlink():
            resolved = parent.resolve(strict=True)
            if resolved != parent:
                raise MigrationError("migration output directory contains a link")
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise MigrationError("migration output directory is unsafe")
            if os.name != "nt" and (
                info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise MigrationError("migration output directory must be owner-bound mode 0700")
            return parent
    except OSError as exc:
        raise MigrationError("migration output directory is unavailable") from exc
    grandparent = parent.parent
    try:
        if not grandparent.exists() or grandparent.is_symlink() or grandparent.resolve(strict=True) != grandparent:
            raise MigrationError("migration output parent chain is unsafe")
    except OSError as exc:
        raise MigrationError("migration output parent chain is unavailable") from exc
    grandparent_info = grandparent.lstat()
    if (
        not stat.S_ISDIR(grandparent_info.st_mode) or stat.S_ISLNK(grandparent_info.st_mode)
        or grandparent_info.st_uid != os.geteuid() or stat.S_IMODE(grandparent_info.st_mode) != 0o700
    ):
        raise MigrationError("migration output grandparent must be owner-bound mode 0700")
    try:
        os.mkdir(parent, 0o700)
    except OSError as exc:
        raise MigrationError("migration output directory cannot be created") from exc
    info = parent.lstat()
    if (
        not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise MigrationError("migration output directory is unsafe after creation")
    return parent


def write_new_private(path: Path, payload: bytes) -> None:
    parent = _verified_output_parent(path)
    temporary = parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path)
        except OSError as exc:
            raise MigrationError("migration output already exists or is unsafe") from exc
        finally:
            temporary.unlink(missing_ok=True)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def git_identity(root: Path) -> tuple[str, str]:
    status_result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=root,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if status_result.returncode or status_result.stdout:
        raise MigrationError("clean migration requires a clean source tree")
    values = []
    for revision in ("HEAD", "HEAD^{tree}"):
        result = subprocess.run(
            ["git", "rev-parse", revision], cwd=root, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False,
        )
        value = result.stdout.strip()
        if result.returncode or COMMIT_RE.fullmatch(value) is None:
            raise MigrationError("migration source identity is unavailable")
        values.append(value)
    return values[0], values[1]


def require_current_source(root: Path, expected_commit: str, expected_tree: str, label: str) -> None:
    source_commit, source_tree = git_identity(root)
    if source_commit != expected_commit or source_tree != expected_tree:
        raise MigrationError(f"{label} source does not match the executing source checkout")


def validate_release_policy(root: Path, target_pixel: str) -> dict[str, Any]:
    manifest = read_json(root / "RELEASE-MANIFEST.json", "release manifest")
    release_update = manifest.get("releaseUpdate")
    legacy = manifest.get("legacyCleanMigration")
    if (
        not isinstance(release_update, dict)
        or release_update.get("qualificationMode") != QUALIFICATION_MODE
        or release_update.get("minimumUpgradablePixel") != MINIMUM_UPGRADABLE
    ):
        raise MigrationError("release update policy is not forward-only clean migration")
    if (
        not isinstance(legacy, dict)
        or legacy.get("schemaVersion") != 1
        or legacy.get("v1Contract", {}).get("sourcePixel") != SOURCE_PIXEL
        or legacy.get("v1Contract", {}).get("targetPixel") != TARGET_PIXEL
    ):
        raise MigrationError("legacy clean migration contract is absent or invalid")
    if manifest.get("pixel") != target_pixel:
        raise MigrationError("release manifest target version does not match the contract")
    return {
        "pixel": str(manifest.get("pixel")),
        "qualificationMode": str(release_update.get("qualificationMode")),
        "minimumUpgradablePixel": str(release_update.get("minimumUpgradablePixel")),
    }


def _check_component_safety(path: Path, label: str) -> None:
    """Require a normalized non-symlink directory with no unsafe writable bits.

    Group/world-writable bits are rejected unless the component is a root-owned
    sticky directory (the standard /tmp, /var/tmp, ... layout) where the sticky bit
    prevents unprivileged users from removing or renaming entries they do not own.
    Only the VERSION file itself is additionally required to be current-owner.
    """
    try:
        if path.exists() or path.is_symlink():
            if path.resolve(strict=True) != path:
                raise MigrationError(f"{label} path contains a link")
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise MigrationError(f"{label} path is unsafe")
            if os.name != "nt" and (stat.S_IMODE(info.st_mode) & 0o022) != 0:
                writable_mode = stat.S_IMODE(info.st_mode)
                root_owned_sticky = info.st_uid == 0 and bool(writable_mode & stat.S_ISVTX)
                if not root_owned_sticky:
                    raise MigrationError(f"{label} path component must not be group/world-writable")
    except OSError as exc:
        raise MigrationError(f"{label} path is unavailable") from exc


def _read_release_version(release_dir: Path) -> str:
    """Read an exact owner-bound current-owner single-link regular VERSION file."""
    version_path = release_dir / "VERSION"
    try:
        info = version_path.lstat()
    except OSError as exc:
        raise MigrationError("installed release VERSION is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise MigrationError("installed release VERSION must be a regular file")
    if info.st_nlink != 1:
        raise MigrationError("installed release VERSION must be a single-link file")
    if os.name != "nt" and (
        info.st_uid != os.geteuid() or (stat.S_IMODE(info.st_mode) & 0o022) != 0
    ):
        raise MigrationError("installed release VERSION must be owner-bound and not group/world-writable")
    version_bytes = read_bytes(version_path, maximum=64, private=False)
    version = version_bytes.decode("ascii").strip()
    if VERSION_RE.fullmatch(version) is None:
        raise MigrationError("installed Pixel version is malformed")
    return version


def check_install_release(install_dir: Path, expected_version: str) -> str:
    """Validate the exact install pointer and return the resolved release version.

    Plan requires ``current`` to resolve exactly to install-dir/releases/3.2.2 and
    finalize to the exact target release, with normalized non-symlink install/releases/release
    components, a current-owner single-link regular VERSION, and no group/world-
    writable path components. Pixel's absolute symlink target is preserved because
    resolution happens through ``Path.resolve``.
    """
    if not install_dir.is_absolute() or install_dir == Path(install_dir.anchor):
        raise MigrationError("active install directory must be an absolute non-root path")
    try:
        if install_dir.resolve(strict=False) != install_dir:
            raise MigrationError("active install directory must contain no path swaps")
    except OSError as exc:
        raise MigrationError("active install directory is unavailable") from exc
    expected = install_dir / "releases" / expected_version
    components: list[Path] = []
    anchor = Path(install_dir.anchor)
    component = install_dir
    while component != anchor:
        components.append(component)
        component = component.parent
    components.append(install_dir / "releases")
    components.append(expected)
    for component_path in components:
        _check_component_safety(component_path, "active install path")
    current = install_dir / "current"
    try:
        info = current.lstat()
    except OSError as exc:
        raise MigrationError("active install pointer is unavailable") from exc
    if not stat.S_ISLNK(info.st_mode):
        raise MigrationError("active install pointer is not a versioned link")
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise MigrationError("active install pointer cannot be resolved") from exc
    if resolved != expected:
        raise MigrationError("active install pointer does not point to the expected release")
    version = _read_release_version(resolved)
    if version != expected_version:
        raise MigrationError(f"installed release {version} is not the expected {expected_version}")
    return version


def check_install_pointer(install_dir: Path) -> str:
    return check_install_release(install_dir, TARGET_PIXEL)


def read_installed_version(install_dir: Path) -> str:
    """Return the active release version, rejecting unsafe links and escapes.

    Kept as a safety primitive for callers that only need the version string; exact
    clean-migration targets are enforced through ``check_install_release``.
    """
    current = install_dir / "current"
    try:
        info = current.lstat()
    except OSError as exc:
        raise MigrationError("active install pointer is unavailable") from exc
    if not stat.S_ISLNK(info.st_mode):
        raise MigrationError("active install pointer is not a versioned link")
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise MigrationError("active install pointer cannot be resolved") from exc
    if resolved == install_dir or install_dir not in resolved.parents:
        raise MigrationError("active install pointer escapes the install directory")
    return _read_release_version(resolved)


# --------------------------------------------------------------------------- #
# Strict standard-library validation.  Each phase has an exact field set and  #
# rejects bool-as-int, strings/negative counts, extra/missing fields, and      #
# malformed hashes, versions, and date-time values.                            #
# --------------------------------------------------------------------------- #

def _exact_keys(value: dict[str, Any], label: str, required: list[str], allowed: list[str]) -> None:
    if not isinstance(value, dict):
        raise MigrationError(f"{label} must be an object")
    missing = [key for key in required if key not in value]
    extra = [key for key in value if key not in allowed]
    if missing or extra:
        raise MigrationError(f"{label} has missing or extra fields")


def _check_string(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise MigrationError(f"{label} is malformed")
    return value


def _check_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise MigrationError(f"{label} must be a boolean")
    return value


def _check_int(value: Any, low: int, high: int, label: str) -> int:
    if type(value) is not int or value < low or value > high:
        raise MigrationError(f"{label} is out of range or not an integer")
    return value


def _check_const(value: Any, expected: Any, label: str) -> None:
    if value != expected:
        raise MigrationError(f"{label} is invalid")


def _check_datetime(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or DATETIME_RE.fullmatch(value) is None:
        raise MigrationError(f"{label} is not a valid date-time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MigrationError(f"{label} is not a valid date-time")
    return parsed


def _check_hash(value: Any, label: str, length: int) -> None:
    pattern = HASH_RE if length == 64 else COMMIT_RE
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise MigrationError(f"{label} must be a {length}-character hex digest")


def _validate_privacy(value: Any, label: str) -> None:
    _exact_keys(value, label, list(PRIVACY), list(PRIVACY))
    for key in PRIVACY:
        if value.get(key) is not False:
            raise MigrationError(f"{label} privacy field must be false")


def _validate_backup_audit(value: Any, label: str) -> None:
    _exact_keys(value, label, ["members", "roots", "uncompressedBytes"], ["members", "roots", "uncompressedBytes"])
    _check_int(value["members"], 0, 100000, f"{label} members")
    _check_int(value["roots"], 1, 100, f"{label} roots")
    _check_int(value["uncompressedBytes"], 0, 21474836480, f"{label} uncompressedBytes")


def _validate_release_policy(value: Any, label: str) -> None:
    _exact_keys(value, label, ["pixel", "qualificationMode", "minimumUpgradablePixel"], ["pixel", "qualificationMode", "minimumUpgradablePixel"])
    if (
        value["pixel"] != TARGET_PIXEL
        or value["qualificationMode"] != QUALIFICATION_MODE
        or value["minimumUpgradablePixel"] != MINIMUM_UPGRADABLE
    ):
        raise MigrationError(f"{label} release policy is not forward-only clean migration")



def validate_restore_audit(audit: dict[str, Any]) -> str:
    """Validate the raw content-free restore-validation audit without coercion.

    The authenticated restore --validate-only output is bound to an exact allowed/
    required field set, status=pass, schemaVersion=1, pixelVersion=3.2.2, and raw
    (uncoerced) integer counts. Both the exact legacy 3.2.2 six-field set (which
    omits rootsSha256) and the exact modern seven-field set (which includes the
    canonical root-contract digest) are accepted. Hostile strings, booleans,
    negatives, and extra or missing fields are rejected before any plan is
    constructed. Returns ``"legacy"`` for the six-field set and ``"modern"`` for the
    seven-field set so the caller can independently derive/normalize the root
    contract digest where the source omits it.
    """
    is_modern = "rootsSha256" in audit
    _exact_keys(
        audit, "restore audit",
        RESTORE_AUDIT_KEYS if is_modern else LEGACY_RESTORE_AUDIT_KEYS,
        RESTORE_AUDIT_KEYS if is_modern else LEGACY_RESTORE_AUDIT_KEYS,
    )
    if audit["status"] != "pass":
        raise MigrationError("restore audit did not pass")
    if audit["schemaVersion"] != 1:
        raise MigrationError("restore audit schemaVersion is invalid")
    _check_int(audit["members"], 0, 100000, "restore audit members")
    _check_int(audit["roots"], 1, 100, "restore audit roots")
    _check_int(audit["uncompressedBytes"], 0, 21474836480, "restore audit uncompressedBytes")
    pixel_version = _check_string(audit["pixelVersion"], VERSION_RE, "restore audit pixelVersion")
    if pixel_version != SOURCE_PIXEL:
        raise MigrationError(f"restore audit pixelVersion {pixel_version} is outside the v1 clean-migration contract")
    if is_modern:
        _check_hash(audit["rootsSha256"], "restore audit rootsSha256", 64)
    return "modern" if is_modern else "legacy"

def validate_plan_doc(plan: dict[str, Any]) -> None:
    _exact_keys(plan, "migration plan", PLAN_KEYS, PLAN_KEYS)
    _check_const(plan["schemaVersion"], 1, "plan schemaVersion")
    _check_const(plan["operation"], "pixel-legacy-clean-migration-plan", "plan operation")
    _check_const(plan["sourcePixel"], SOURCE_PIXEL, "plan sourcePixel")
    _check_const(plan["installedPixel"], SOURCE_PIXEL, "plan installedPixel")
    _check_const(plan["targetPixel"], TARGET_PIXEL, "plan targetPixel")
    _check_hash(plan["backupSha256"], "plan backupSha256", 64)
    _validate_backup_audit(plan["backupAudit"], "plan backupAudit")
    _validate_release_policy(plan["releasePolicy"], "plan releasePolicy")
    _check_hash(plan["sourceCommit"], "plan sourceCommit", 40)
    _check_hash(plan["sourceTree"], "plan sourceTree", 40)
    _check_hash(plan["backupRootsSha256"], "plan backupRootsSha256", 64)
    _check_hash(plan["planSha256"], "plan planSha256", 64)
    _check_datetime(plan["generatedAt"], "plan generatedAt")
    _validate_privacy(plan["privacy"], "plan privacy")
    _check_const(plan["boundary"], BOUNDARIES["plan"], "plan boundary")


def validate_rehearsal_doc(rehearsal: dict[str, Any]) -> None:
    _exact_keys(rehearsal, "migration rehearsal", REHEARSAL_KEYS, REHEARSAL_KEYS)
    _check_const(rehearsal["schemaVersion"], 1, "rehearsal schemaVersion")
    _check_const(rehearsal["operation"], "pixel-legacy-clean-migration-rehearsal", "rehearsal operation")
    _check_const(rehearsal["sourcePixel"], SOURCE_PIXEL, "rehearsal sourcePixel")
    _check_const(rehearsal["targetPixel"], TARGET_PIXEL, "rehearsal targetPixel")
    _check_hash(rehearsal["planSha256"], "rehearsal planSha256", 64)
    _check_hash(rehearsal["backupSha256"], "rehearsal backupSha256", 64)
    _check_hash(rehearsal["sourceCommit"], "rehearsal sourceCommit", 40)
    _check_hash(rehearsal["sourceTree"], "rehearsal sourceTree", 40)
    _check_hash(rehearsal["backupRootsSha256"], "rehearsal backupRootsSha256", 64)
    _check_bool(rehearsal["rehearsalRootCreated"], "rehearsal rehearsalRootCreated")
    if rehearsal["rehearsalRootCreated"] is not True:
        raise MigrationError("rehearsal rehearsalRootCreated must be true")
    _check_bool(rehearsal["liveStateChanged"], "rehearsal liveStateChanged")
    if rehearsal["liveStateChanged"] is not False:
        raise MigrationError("rehearsal liveStateChanged must be false")
    _check_hash(rehearsal["rehearsalSha256"], "rehearsal rehearsalSha256", 64)
    _check_datetime(rehearsal["generatedAt"], "rehearsal generatedAt")
    _validate_privacy(rehearsal["privacy"], "rehearsal privacy")
    _check_const(rehearsal["boundary"], BOUNDARIES["rehearsal"], "rehearsal boundary")


def validate_completion_doc(completion: dict[str, Any]) -> None:
    _exact_keys(completion, "migration completion", COMPLETION_KEYS, COMPLETION_KEYS)
    _check_const(completion["schemaVersion"], 1, "completion schemaVersion")
    _check_const(completion["operation"], "pixel-legacy-clean-migration-completion", "completion operation")
    _check_const(completion["sourcePixel"], SOURCE_PIXEL, "completion sourcePixel")
    _check_const(completion["targetPixel"], TARGET_PIXEL, "completion targetPixel")
    _check_const(completion["activeRelease"], TARGET_PIXEL, "completion activeRelease")
    _check_hash(completion["planSha256"], "completion planSha256", 64)
    _check_hash(completion["rehearsalSha256"], "completion rehearsalSha256", 64)
    _check_hash(completion["backupSha256"], "completion backupSha256", 64)
    _check_hash(completion["restoreReceiptSha256"], "completion restoreReceiptSha256", 64)
    _check_hash(completion["runtimeAttestationSha256"], "completion runtimeAttestationSha256", 64)
    _check_hash(completion["sourceCommit"], "completion sourceCommit", 40)
    _check_hash(completion["sourceTree"], "completion sourceTree", 40)
    _check_bool(completion["verified"], "completion verified")
    if completion["verified"] is not True:
        raise MigrationError("completion verified must be true")
    _check_hash(completion["completionSha256"], "completion completionSha256", 64)
    _check_datetime(completion["generatedAt"], "completion generatedAt")
    _validate_privacy(completion["privacy"], "completion privacy")
    _check_const(completion["boundary"], BOUNDARIES["completion"], "completion boundary")


def validate_restore_receipt(
    receipt: dict[str, Any],
    *,
    rehearsal_generated_at: datetime | None = None,
    finalize_now: datetime | None = None,
) -> None:
    """Validate an exact restore receipt, binding knowledge booleans and time order.

    ``knowledgeDeletionReconciled`` must equal ``historicalKeyWrappingRemoved``; the
    receipt must not predate the exact rehearsal (with a small clock tolerance) and
    must not be implausibly future-dated at finalize. This is additional evidence
    binding, not authority: it never authorizes the migration on its own.
    """
    _exact_keys(receipt, "restore receipt", RESTORE_RECEIPT_KEYS, RESTORE_RECEIPT_KEYS)
    _check_const(receipt["schemaVersion"], 1, "restore receipt schemaVersion")
    _check_const(receipt["kind"], RESTORE_RECEIPT_KIND, "restore receipt kind")
    _check_const(receipt["status"], "pass", "restore receipt status")
    _check_const(receipt["mode"], "restore", "restore receipt mode")
    _check_bool(receipt["verified"], "restore receipt verified")
    if receipt["verified"] is not True:
        raise MigrationError("restore receipt verified must be true")
    _check_bool(receipt["automaticRollbackArmed"], "restore receipt automaticRollbackArmed")
    if receipt["automaticRollbackArmed"] is not True:
        raise MigrationError("restore receipt automaticRollbackArmed must be true")
    knowledge_deletion = _check_bool(receipt["knowledgeDeletionReconciled"], "restore receipt knowledgeDeletionReconciled")
    historical_wrapping = _check_bool(receipt["historicalKeyWrappingRemoved"], "restore receipt historicalKeyWrappingRemoved")
    if knowledge_deletion is not historical_wrapping:
        raise MigrationError("restore receipt knowledge booleans are contradictory")
    _check_hash(receipt["backupSha256"], "restore receipt backupSha256", 64)
    _check_const(receipt["sourcePixel"], SOURCE_PIXEL, "restore receipt sourcePixel")
    _check_const(receipt["targetPixel"], TARGET_PIXEL, "restore receipt targetPixel")
    _check_hash(receipt["receiptSha256"], "restore receipt receiptSha256", 64)
    if receipt["receiptSha256"] != self_hash(receipt, "receiptSha256"):
        raise MigrationError("restore receipt hash is stale")
    generated_at = _check_datetime(receipt["generatedAt"], "restore receipt generatedAt")
    if rehearsal_generated_at is not None:
        if generated_at < rehearsal_generated_at - ATTESTATION_TOLERANCE:
            raise MigrationError("restore receipt predates the exact rehearsal")
    if finalize_now is not None:
        if generated_at > finalize_now + ATTESTATION_TOLERANCE:
            raise MigrationError("restore receipt is implausibly future-dated")
    _validate_privacy(receipt["privacy"], "restore receipt privacy")
    _check_const(receipt["boundary"], BOUNDARIES["restore"], "restore receipt boundary")


def _validate_hash_map(value: Any, label: str, keys: list[str]) -> None:
    _exact_keys(value, label, keys, keys)
    for key in keys:
        _check_hash(value[key], f"{label} {key}", 64)


def validate_runtime_attestation(
    att: dict[str, Any],
    expected_pixel: str,
    expected_commit: str,
    expected_tree: str,
    verify_start: datetime,
    verify_end: datetime,
) -> None:
    _exact_keys(att, "runtime attestation", RUNTIME_ATTESTATION_KEYS, RUNTIME_ATTESTATION_KEYS)
    _check_const(att["schemaVersion"], 1, "attestation schemaVersion")
    _check_const(att["kind"], RUNTIME_ATTESTATION_KIND, "attestation kind")
    _check_const(att["status"], "verified", "attestation status")
    _check_const(att["pixel"], expected_pixel, "attestation pixel")
    verified_at = _check_datetime(att["verifiedAt"], "attestation verifiedAt")
    if not (verify_start - ATTESTATION_TOLERANCE <= verified_at <= verify_end + ATTESTATION_TOLERANCE):
        raise MigrationError("runtime attestation verifiedAt is stale")

    source = att["source"]
    _exact_keys(source, "attestation source", ["state", "commit", "tree"], ["state", "commit", "tree"])
    if source["state"] != "git-clean":
        raise MigrationError("runtime attestation source is unavailable; migration completion requires git-clean source")
    _check_hash(source["commit"], "attestation source commit", 40)
    _check_hash(source["tree"], "attestation source tree", 40)
    if source["commit"] != expected_commit or source["tree"] != expected_tree:
        raise MigrationError("runtime attestation source does not match the executing source")

    qualification = att["qualification"]
    _exact_keys(
        qualification, "attestation qualification",
        ["recordStatus", "sourceCommit", "qualifiedAt", "relationship"],
        ["recordStatus", "sourceCommit", "qualifiedAt", "relationship"],
    )
    if qualification["recordStatus"] != "supported":
        raise MigrationError("attestation qualification recordStatus must be supported")
    _check_hash(qualification["sourceCommit"], "attestation qualification sourceCommit", 40)
    qualified_at = _check_string(qualification["qualifiedAt"], DATE_RE, "attestation qualification qualifiedAt")
    try:
        datetime.strptime(qualified_at, "%Y-%m-%d")
    except ValueError:
        raise MigrationError("attestation qualification qualifiedAt is not a valid date")
    if qualification["relationship"] not in ("same-source", "qualified-ancestor"):
        raise MigrationError("attestation qualification relationship must be same-source or qualified-ancestor")

    _validate_hash_map(
        att["release"], "attestation release",
        ["sourceIdentitySha256", "deploymentInputsSha256", "sourceRuntimeSha256",
         "installManifestSha256", "releaseManifestSha256", "compatibilityManifestSha256",
         "qualificationMatrixSha256"],
    )
    _validate_hash_map(
        att["configuration"], "attestation configuration",
        ["generatedDeploymentSha256", "activeOpenClawSha256"],
    )

    runtime = att["runtime"]
    _exact_keys(
        runtime, "attestation runtime",
        ["state", "openclaw", "routeClass", "providerIdSha256", "modelIdSha256",
         "contextWindow", "maxOutputTokens", "reasoning", "endpointChecks"],
        ["state", "openclaw", "routeClass", "providerIdSha256", "modelIdSha256",
         "contextWindow", "maxOutputTokens", "reasoning", "endpointChecks"],
    )
    _check_const(runtime["state"], "gateway-verified-model-unproven", "attestation runtime state")
    _check_string(runtime["openclaw"], re.compile(r"^[0-9]{4}\.[0-9]+\.[0-9]+(?:-[0-9]+)?$"), "attestation runtime openclaw")
    if runtime["routeClass"] not in ("local", "configured"):
        raise MigrationError("attestation runtime routeClass is invalid")
    _check_hash(runtime["providerIdSha256"], "attestation runtime providerIdSha256", 64)
    _check_hash(runtime["modelIdSha256"], "attestation runtime modelIdSha256", 64)
    _check_int(runtime["contextWindow"], 4096, 10000000, "attestation runtime contextWindow")
    _check_int(runtime["maxOutputTokens"], 256, 1000000, "attestation runtime maxOutputTokens")
    _check_bool(runtime["reasoning"], "attestation runtime reasoning")
    _check_const(runtime["endpointChecks"], "verified", "attestation runtime endpointChecks")

    profiles = att["profiles"]
    _exact_keys(profiles, "attestation profiles", ["deployment", "capability"], ["deployment", "capability"])
    if profiles["deployment"] not in ("prepared", "reference"):
        raise MigrationError("attestation profiles deployment is invalid")
    if profiles["capability"] not in ("minimal", "chief-of-staff", "research", "engineering-operator"):
        raise MigrationError("attestation profiles capability is invalid")

    connectors = att["connectors"]
    if not isinstance(connectors, list) or len(connectors) != 6:
        raise MigrationError("attestation connectors must have exactly six entries")
    connector_ids: set[str] = set()
    for connector in connectors:
        _exact_keys(connector, "attestation connector", ["id", "state"], ["id", "state"])
        connector_ids.add(connector["id"])
        if connector["id"] not in EXPECTED_CONNECTORS:
            raise MigrationError("attestation connector id is invalid")
        if connector["state"] not in ("enabled-verified", "disabled-verified"):
            raise MigrationError("attestation connector state is invalid")
    if connector_ids != EXPECTED_CONNECTORS:
        raise MigrationError("attestation connectors must cover the entire expected connector set exactly once")

    _check_const(
        att["boundary"],
        "Content-free verification receipt for one observed local deployment. It binds source, installed manifests, configuration, profiles, and connector checks. Model capability remains unproven until a separate exact real-backend qualification receipt exists.",
        "attestation boundary",
    )


def validate_schema(value: dict[str, Any]) -> None:
    operation = value.get("operation")
    if operation == "pixel-legacy-clean-migration-plan":
        validate_plan_doc(value)
    elif operation == "pixel-legacy-clean-migration-rehearsal":
        validate_rehearsal_doc(value)
    elif operation == "pixel-legacy-clean-migration-completion":
        validate_completion_doc(value)
    else:
        raise MigrationError("migration evidence has an unknown operation")


# --------------------------------------------------------------------------- #
# Authenticated restore composition                                          #
# --------------------------------------------------------------------------- #

def run_restore_validate(pixel: Path, backup: Path, identity: Path, signers: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(pixel), "restore", str(backup), "--identity", str(identity), "--signers", str(signers), "--validate-only"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if result.returncode:
        raise MigrationError("authenticated backup validation failed")
    audit = parse_json(result.stdout.encode("utf-8"), "backup audit")
    if not isinstance(audit, dict) or audit.get("status") != "pass":
        raise MigrationError("backup audit did not pass")
    return audit


def _freeze_input(source: Path, dest: Path, label: str, *, maximum: int) -> None:
    """Copy one authenticated input into the private freeze dir, bounded and non-symlink.

    The source must be a regular single-link file at or under ``maximum`` bytes, and every
    chunk is written until fully consumed so a partial ``os.write`` can never truncate the
    frozen copy, while a nonpositive write fails closed. The destination is created with O_EXCL and no symlink following, chmodded
    to 0400 and fsynced. Any oversize, unsafe, symlink, or multi-link input fails closed.
    """
    source_fd = None
    dest_fd = None
    total = 0
    try:
        source_fd = os.open(
            source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        source_info = os.fstat(source_fd)
        if not stat.S_ISREG(source_info.st_mode) or source_info.st_nlink != 1:
            raise MigrationError(f"{label} must be a regular single-link file")
        if source_info.st_size > maximum:
            raise MigrationError(f"{label} is oversized")
        dest_fd = os.open(
            dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o600,
        )
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            written = 0
            while written < len(chunk):
                n = os.write(dest_fd, chunk[written:])
                if n <= 0:
                    raise MigrationError(f"{label} could not be frozen")
                written += n
            total += len(chunk)
            if total > maximum:
                raise MigrationError(f"{label} is oversized")
        current = source.lstat()
        if (
            current.st_dev != source_info.st_dev
            or current.st_ino != source_info.st_ino
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or current.st_size > maximum
        ):
            raise MigrationError(f"{label} changed while being frozen")
        os.fchmod(dest_fd, 0o400)
        os.fsync(dest_fd)
    except OSError as exc:
        raise MigrationError(f"{label} could not be frozen") from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if dest_fd is not None:
            os.close(dest_fd)


def _validate_signature_envelope(sig_path: Path) -> None:
    """Reject any non-canonical or non-ASCII ssh signature envelope."""
    try:
        raw = sig_path.read_bytes()
    except OSError as exc:
        raise MigrationError("backup signature is unavailable") from exc
    if not raw or len(raw) > 16384 or not raw.endswith(b"\n"):
        raise MigrationError("backup signature envelope is malformed")
    try:
        lines = raw.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise MigrationError("backup signature envelope is not ASCII") from exc
    if (
        len(lines) < 3
        or lines[0] != SSH_SIG_BEGIN
        or lines[-1] != SSH_SIG_END
        or lines.count(SSH_SIG_BEGIN) != 1
        or lines.count(SSH_SIG_END) != 1
        or any(not SSH_SIG_LINE_RE.fullmatch(line) for line in lines[1:-1])
    ):
        raise MigrationError("backup signature envelope is non-canonical")
    try:
        base64.b64decode("".join(lines[1:-1]), validate=True)
    except (ValueError, TypeError) as exc:
        raise MigrationError("backup signature envelope is non-canonical") from exc


def _terminate(processes: list[subprocess.Popen]) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
    for process in processes:
        process.wait()


def _make_frozen_dir() -> str:
    frozen = tempfile.mkdtemp(prefix="pixel-migrate-cohort-")
    os.chmod(frozen, 0o700)
    return frozen


def _freeze_legacy_inputs(
    frozen: str, backup: Path, identity: Path, signers: Path,
) -> tuple[Path, Path, Path, Path, Path]:
    """Freeze backup, checksum, signature, age identity, and allowed signers into one private cohort."""
    frozen_backup = Path(frozen) / "backup.tar.gz.age"
    frozen_checksum = Path(str(frozen_backup) + ".sha256")
    frozen_signature = Path(frozen) / "backup.tar.gz.age.sig"
    frozen_identity = Path(frozen) / "identity"
    frozen_signers = Path(frozen) / "allowed-signers"
    _freeze_input(backup, frozen_backup, "backup", maximum=MAX_BACKUP_BYTES)
    _freeze_input(Path(str(backup) + ".sha256"), frozen_checksum, "backup checksum", maximum=CHECKSUM_MAX_BYTES)
    _freeze_input(Path(str(backup) + ".sig"), frozen_signature, "backup signature", maximum=SIGNATURE_MAX_BYTES)
    _freeze_input(identity, frozen_identity, "age identity", maximum=SUPPORT_MAX_BYTES)
    _freeze_input(signers, frozen_signers, "allowed signers", maximum=SUPPORT_MAX_BYTES)
    return frozen_backup, frozen_checksum, frozen_signature, frozen_identity, frozen_signers


def _derive_root_contract_from_frozen(
    frozen_backup: Path, frozen_signature: Path, frozen_identity: Path, frozen_signers: Path,
) -> str:
    """Derive the authenticated 3.2 root-contract sha256 from already-frozen bytes.

    The canonical signature envelope is validated, the ssh signature is verified under
    principal ``pixel-backup`` and namespace ``pixel-private-backup``, and the backup is
    decrypted via an explicit argv (no shell interpolation) and streamed into the trusted
    ``read-backup-manifest.py``. The returned digest is the sha256 of the exact
    newline-delimited canonical root list that the migration restore binds to the same
    contract. Any signature, decrypt, drift, or malformed/overlapping/duplicate/unsorted
    root-list failure fails closed. No roots or secret content are returned or emitted.
    """
    before = sha256_file(frozen_backup, "frozen backup")
    _validate_signature_envelope(frozen_signature)
    with open(frozen_backup, "rb") as backup_handle:
        verify = subprocess.run(
            ["ssh-keygen", "-Y", "verify", "-f", str(frozen_signers),
             "-I", "pixel-backup", "-n", "pixel-private-backup",
             "-s", str(frozen_signature)],
            stdin=backup_handle, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    if verify.returncode:
        raise MigrationError("encrypted backup signature is not trusted")

    decrypt = subprocess.Popen(
        ["age", "--decrypt", "--identity", str(frozen_identity), str(frozen_backup)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    manifest = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve().parent / "read-backup-manifest.py")],
        stdin=decrypt.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    decrypt.stdout.close()
    try:
        manifest_stdout, _ = manifest.communicate(timeout=MANIFEST_READ_TIMEOUT)
        _, _decrypt_stderr = decrypt.communicate(timeout=MANIFEST_READ_TIMEOUT)
    except subprocess.TimeoutExpired:
        _terminate([manifest, decrypt])
        raise MigrationError("authenticated 3.2 root contract could not be read")
    if manifest.returncode:
        raise MigrationError("authenticated 3.2 root contract could not be read")
    if decrypt.returncode:
        raise MigrationError("authenticated backup could not be decrypted")
    if len(manifest_stdout) > MANIFEST_MAX_OUTPUT:
        raise MigrationError("authenticated 3.2 root contract is oversized")
    after = sha256_file(frozen_backup, "frozen backup")
    if before != after:
        raise MigrationError("frozen backup changed during contract derivation")
    return sha256_hex(manifest_stdout)


def derive_backup_root_contract(backup: Path, identity: Path, signers: Path) -> str:
    """Securely derive the authenticated 3.2 root-contract sha256 from a private cohort."""
    frozen = _make_frozen_dir()
    try:
        frozen_backup, _frozen_checksum, frozen_signature, frozen_identity, frozen_signers = _freeze_legacy_inputs(
            frozen, backup, identity, signers,
        )
        return _derive_root_contract_from_frozen(
            frozen_backup, frozen_signature, frozen_identity, frozen_signers,
        )
    finally:
        shutil.rmtree(frozen, ignore_errors=True)


def _cohort_fingerprints(
    frozen_backup: Path, frozen_checksum: Path, frozen_signature: Path,
    frozen_identity: Path, frozen_signers: Path,
) -> tuple[str, str, str, str, str]:
    """Hash all five frozen cohort members for the single-cohort guarantee.

    The returned tuple is used only to prove that every member the audit and root-contract
    derivation observed is byte-for-byte the same frozen cohort. No member hash is ever
    placed into a plan or evidence artifact.
    """
    return (
        sha256_file(frozen_backup, "frozen backup"),
        sha256_file(frozen_checksum, "frozen backup checksum"),
        sha256_file(frozen_signature, "frozen signature"),
        sha256_file(frozen_identity, "frozen age identity"),
        sha256_file(frozen_signers, "frozen allowed signers"),
    )


def build_legacy_cohort(
    pixel: Path, backup: Path, identity: Path, signers: Path,
) -> tuple[dict[str, Any], str, str]:
    """Freeze one private cohort and derive audit, backup sha, and root contract from it.

    The backup, its adjacent sha256 checksum, signature envelope, age identity, and
    allowed-signers file are frozen into a single private 0700 temp dir. The restore audit,
    backup sha256, and root-contract sha256 are all derived from that same frozen cohort, so
    a concurrent replacement of any original input cannot bind the audit, root contract, and
    plan to different bytes. The returned triple is ``(audit, backup_sha256,
    backup_roots_sha256)``. No paths, roots, checksums, or secret content are returned or
    emitted.
    """
    frozen = _make_frozen_dir()
    try:
        frozen_backup, frozen_checksum, frozen_signature, frozen_identity, frozen_signers = _freeze_legacy_inputs(
            frozen, backup, identity, signers,
        )
        fingerprints = _cohort_fingerprints(
            frozen_backup, frozen_checksum, frozen_signature, frozen_identity, frozen_signers,
        )
        backup_sha = fingerprints[0]
        audit = run_restore_validate(pixel, frozen_backup, frozen_identity, frozen_signers)
        audit_variant = validate_restore_audit(audit)
        if _cohort_fingerprints(frozen_backup, frozen_checksum, frozen_signature, frozen_identity, frozen_signers) != fingerprints:
            raise MigrationError("frozen cohort changed during audit")
        root_contract_sha = _derive_root_contract_from_frozen(
            frozen_backup, frozen_signature, frozen_identity, frozen_signers,
        )
        if audit_variant == "legacy":
            # The legacy six-field restore audit omits rootsSha256. Independently derive
            # it from the already frozen, signature-verified backup cohort and normalize
            # the audit before any plan is constructed, so downstream phases see the same
            # exact field set as a modern audit.
            audit["rootsSha256"] = root_contract_sha
        elif audit["rootsSha256"] != root_contract_sha:
            raise MigrationError("restore audit root contract does not match the authenticated backup manifest")
        if _cohort_fingerprints(frozen_backup, frozen_checksum, frozen_signature, frozen_identity, frozen_signers) != fingerprints:
            raise MigrationError("frozen cohort changed during root contract derivation")
        return audit, backup_sha, root_contract_sha
    finally:
        shutil.rmtree(frozen, ignore_errors=True)


def run_restore_rehearse(pixel: Path, backup: Path, identity: Path, signers: Path, rehearsal_root: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(pixel), "restore", str(backup), "--identity", str(identity), "--signers", str(signers), "--rehearse", str(rehearsal_root)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
    )
    if result.returncode:
        raise MigrationError("restore rehearsal failed")
    receipt = parse_json(result.stdout.encode("utf-8"), "rehearsal result")
    if (
        not isinstance(receipt, dict) or receipt.get("status") != "pass"
        or receipt.get("mode") != "rehearse" or receipt.get("liveStateChanged") is not False
    ):
        raise MigrationError("restore rehearsal did not produce a non-live result")
    return receipt


def require_same_backup(plan: dict[str, Any], backup: Path) -> str:
    observed = sha256_file(backup, "backup")
    expected = plan.get("backupSha256")
    if not isinstance(expected, str) or HASH_RE.fullmatch(expected) is None or observed != expected:
        raise MigrationError("backup identity does not match the plan")
    return observed


def checked_private_plan(plan_path: Path) -> dict[str, Any]:
    plan = read_json(plan_path, "migration plan", private=True)
    validate_plan_doc(plan)
    if plan.get("operation") != "pixel-legacy-clean-migration-plan":
        raise MigrationError("evidence is not a migration plan")
    if plan.get("planSha256") != self_hash(plan, "planSha256"):
        raise MigrationError("migration plan hash is stale")
    if plan.get("sourcePixel") != SOURCE_PIXEL or plan.get("targetPixel") != TARGET_PIXEL:
        raise MigrationError("migration plan version contract is invalid")
    return plan


def checked_private_rehearsal(rehearsal_path: Path, plan: dict[str, Any]) -> dict[str, Any]:
    rehearsal = read_json(rehearsal_path, "migration rehearsal", private=True)
    validate_rehearsal_doc(rehearsal)
    if rehearsal.get("operation") != "pixel-legacy-clean-migration-rehearsal":
        raise MigrationError("evidence is not a migration rehearsal")
    if rehearsal.get("rehearsalSha256") != self_hash(rehearsal, "rehearsalSha256"):
        raise MigrationError("migration rehearsal hash is stale")
    if (
        rehearsal.get("planSha256") != plan.get("planSha256")
        or rehearsal.get("backupSha256") != plan.get("backupSha256")
        or rehearsal.get("backupRootsSha256") != plan.get("backupRootsSha256")
        or rehearsal.get("sourcePixel") != SOURCE_PIXEL
        or rehearsal.get("targetPixel") != TARGET_PIXEL
        or rehearsal.get("sourceCommit") != plan.get("sourceCommit")
        or rehearsal.get("sourceTree") != plan.get("sourceTree")
    ):
        raise MigrationError("migration rehearsal is not bound to the exact plan and source")
    return rehearsal


def build_plan(
    root: Path, install_dir: Path, audit: dict[str, Any], backup: Path,
    *, now: Callable[[], datetime], source_identity: tuple[str, str] | None = None,
    backup_roots_sha256: str | None = None, backup_sha: str | None = None,
) -> dict[str, Any]:
    audit_variant = validate_restore_audit(audit)
    if not isinstance(backup_roots_sha256, str) or HASH_RE.fullmatch(backup_roots_sha256) is None:
        raise MigrationError("backup root contract must be a 64-character hex digest")
    if audit_variant == "legacy":
        raise MigrationError("restore audit root contract must be normalized before plan construction")
    if audit["rootsSha256"] != backup_roots_sha256:
        raise MigrationError("restore audit root contract does not match the migration root contract")
    source_pixel = audit["pixelVersion"]
    installed = check_install_release(install_dir, SOURCE_PIXEL)
    release_policy = validate_release_policy(root, TARGET_PIXEL)
    observed = sha256_file(backup, "backup")
    if backup_sha is None:
        backup_sha = observed
    elif observed != backup_sha:
        raise MigrationError("original backup changed since it was frozen")
    source_commit, source_tree = source_identity or git_identity(root)
    plan = {
        "schemaVersion": 1,
        "operation": "pixel-legacy-clean-migration-plan",
        "sourcePixel": source_pixel,
        "installedPixel": installed,
        "targetPixel": TARGET_PIXEL,
        "backupSha256": backup_sha,
        "backupRootsSha256": backup_roots_sha256,
        "backupAudit": {
            "members": audit["members"],
            "roots": audit["roots"],
            "uncompressedBytes": audit["uncompressedBytes"],
        },
        "releasePolicy": release_policy,
        "sourceCommit": source_commit,
        "sourceTree": source_tree,
        "planSha256": None,
        "generatedAt": now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "privacy": PRIVACY,
        "boundary": BOUNDARIES["plan"],
    }
    plan["planSha256"] = self_hash(plan, "planSha256")
    validate_plan_doc(plan)
    return plan


def build_rehearsal(
    root: Path, plan: dict[str, Any], backup_sha: str, rehearsal_root: Path, rehearsal_result: dict[str, Any],
    *, now: Callable[[], datetime], source_identity: tuple[str, str] | None = None,
) -> dict[str, Any]:
    source_commit, source_tree = source_identity or git_identity(root)
    if source_commit != plan.get("sourceCommit") or source_tree != plan.get("sourceTree"):
        raise MigrationError("rehearsal source does not match the plan source")
    rehearsal = {
        "schemaVersion": 1,
        "operation": "pixel-legacy-clean-migration-rehearsal",
        "sourcePixel": SOURCE_PIXEL,
        "targetPixel": TARGET_PIXEL,
        "planSha256": plan.get("planSha256"),
        "backupSha256": backup_sha,
        "backupRootsSha256": plan.get("backupRootsSha256"),
        "sourceCommit": source_commit,
        "sourceTree": source_tree,
        "rehearsalRootCreated": True,
        "liveStateChanged": rehearsal_result.get("liveStateChanged"),
        "rehearsalSha256": None,
        "generatedAt": now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "privacy": PRIVACY,
        "boundary": BOUNDARIES["rehearsal"],
    }
    rehearsal["rehearsalSha256"] = self_hash(rehearsal, "rehearsalSha256")
    validate_rehearsal_doc(rehearsal)
    return rehearsal


def build_completion(
    root: Path, install_dir: Path, plan: dict[str, Any], rehearsal: dict[str, Any],
    restore_receipt: dict[str, Any], restore_receipt_bytes: bytes, attestation_bytes: bytes,
    *, now: Callable[[], datetime], source_identity: tuple[str, str] | None = None,
) -> dict[str, Any]:
    active = check_install_pointer(install_dir)
    source_commit, source_tree = source_identity or git_identity(root)
    if (
        source_commit != plan.get("sourceCommit") or source_tree != plan.get("sourceTree")
        or source_commit != rehearsal.get("sourceCommit") or source_tree != rehearsal.get("sourceTree")
    ):
        raise MigrationError("completion source does not match the plan and rehearsal source")
    if (
        restore_receipt.get("backupSha256") != plan.get("backupSha256")
        or restore_receipt.get("sourcePixel") != SOURCE_PIXEL
        or restore_receipt.get("targetPixel") != TARGET_PIXEL
    ):
        raise MigrationError("restore receipt does not match the migration plan")
    completion = {
        "schemaVersion": 1,
        "operation": "pixel-legacy-clean-migration-completion",
        "sourcePixel": SOURCE_PIXEL,
        "targetPixel": TARGET_PIXEL,
        "activeRelease": active,
        "planSha256": plan.get("planSha256"),
        "rehearsalSha256": rehearsal.get("rehearsalSha256"),
        "backupSha256": plan.get("backupSha256"),
        "restoreReceiptSha256": sha256_hex(restore_receipt_bytes),
        "runtimeAttestationSha256": sha256_hex(attestation_bytes),
        "sourceCommit": source_commit,
        "sourceTree": source_tree,
        "verified": True,
        "completionSha256": None,
        "generatedAt": now().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "privacy": PRIVACY,
        "boundary": BOUNDARIES["completion"],
    }
    completion["completionSha256"] = self_hash(completion, "completionSha256")
    validate_completion_doc(completion)
    return completion


# --------------------------------------------------------------------------- #
# Activation (self-contained transaction)                                   #
# --------------------------------------------------------------------------- #

def _require_migration_journal_custody(journal: Path) -> None:
    """Require the migration journal to live directly beneath the fixed root-owned custody dir.

    The custody directory is root-owned mode-0700 in production, so the journal parent is not
    owner-bound to the non-root orchestrator. Ownership trust for the journal itself is derived
    by the privileged restore parser/helper from its own effective uid, and availability inside
    the root-0700 custody can only be proven by the privileged reservation, not by this
    non-root orchestrator (which cannot even observe the directory). So here we enforce only the
    literal fixed custody location and a safe basename, and let the privileged reservation be
    authoritative for occupancy and custody ownership/mode.
    """
    if journal.parent != MIGRATION_JOURNAL_CUSTODY:
        raise MigrationError("migration journal must be directly beneath the fixed custody directory")
    if not MIGRATION_JOURNAL_BASENAME.fullmatch(journal.name):
        raise MigrationError("migration journal basename is unsafe")


def _require_private_output_parent(path: Path, label: str) -> None:
    """Require an existing owner-only 0700 non-symlink parent for a new output path."""
    parent = path.parent
    try:
        if not parent.exists() or parent.is_symlink() or parent.resolve(strict=True) != parent:
            raise MigrationError(f"{label} output directory is unsafe")
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise MigrationError(f"{label} output directory is unsafe")
        if os.name != "nt" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise MigrationError(f"{label} output directory must be owner-bound mode 0700")
    except OSError as exc:
        raise MigrationError(f"{label} output directory is unavailable") from exc


def _rewrite_reserved(path: Path, payload: bytes) -> None:
    """Guarded in-place rewrite of an already-reserved 0600 single-link output."""
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    before: tuple[int, int, int, int] | None = None
    try:
        info = os.fstat(descriptor)
        before = (info.st_ino, info.st_dev, info.st_nlink, stat.S_IMODE(info.st_mode))
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise MigrationError("reserved output is not a regular single-link file")
        if os.name != "nt" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise MigrationError("reserved output is not owner-bound mode 0600")
        os.fchmod(descriptor, 0o600)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        view = memoryview(payload)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        if written != len(payload):
            raise MigrationError("reserved output write failed")
        if os.fstat(descriptor).st_size != len(payload):
            raise MigrationError("reserved output size mismatch")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        after = path.lstat()
        if (
            after.st_ino != before[0] or after.st_dev != before[1]
            or after.st_nlink != before[2] or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise MigrationError("reserved output was replaced or altered")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _write_failure_receipt(completion_path: Path, *, rolled_back: bool) -> None:
    """Leave a truthful, content-free, non-pass receipt in the reserved completion output.

    Called only before the terminal commit. The completion path is already reserved as an
    owner-only 0600 single-link marker, so the failure receipt is written through the same
    guarded in-place rewrite. It uses only fixed content-free fields from this module, never
    arbitrary caller or exception content (which could embed a path or identity), never includes
    credentials, host/provider/model identity, or user content, and never deletes the
    authenticated backup. ``rolled_back`` truthfully records whether any live mutation that
    occurred was rolled back to its exact pre-migration state.
    """
    payload = {
        "schemaVersion": 1,
        "operation": "pixel-legacy-clean-migration-activate",
        "status": "failed",
        "targetPixel": TARGET_PIXEL,
        "sourcePixel": SOURCE_PIXEL,
        "reason": "activation did not reach its terminal commit",
        "backupRetained": True,
        "rolledBack": rolled_back,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "privacy": PRIVACY,
        "boundary": "Content-free non-pass activation receipt. The activation transaction did not reach its terminal commit; the authenticated backup is retained and is the single rollback boundary. rolledBack records whether any live mutation was restored to its exact pre-migration state.",
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    _rewrite_reserved(completion_path, body)


def _snapshot_frozen_config(install_dir: Path) -> dict[str, str]:
    """Fingerprint the complete pre-prepared target release before live mutation.

    Use the single authoritative release-tree verifier instead of maintaining a second tree
    walk here. The shared verifier descriptor-binds every directory and regular file, rejects
    hardlinks and unsafe owner/mode changes, and binds symlink target strings without following
    them. The returned mapping shape keeps the existing before/after comparison explicit.
    """
    release = install_dir / "releases" / TARGET_PIXEL
    return {"releaseTreeSha256": _release_tree_sha(release)}


def _validate_prepared_config(
    install_dir: Path, *, expected_active: str = TARGET_PIXEL,
) -> dict[str, str]:
    """Validate the pre-prepared target deployment and return its frozen-config snapshot.

    This bounded, non-mutating check runs before any live mutation. A deployment-backed clean
    migration must still have the exact legacy source active; the privileged deployment transaction
    switches the pointer to the separately installed target while rollback is armed. The legacy
    compatibility path without prepared deployment artifacts already has the target active. In both
    modes, this check separately verifies and fingerprints the exact frozen target release tree. No
    configuration is regenerated; the prepared target configuration is proven byte-identical before
    and after the swap.
    """
    check_install_release(install_dir, expected_active)
    release = install_dir / "releases" / TARGET_PIXEL
    # The installed release is the runtime payload assembled by install.sh; the controller
    # entrypoint remains in the separately reviewed source tree and is intentionally not copied
    # into $PIXEL_INSTALL_DIR/releases/<version>. Requiring a release-local ``pixel`` path here
    # made every real prepared cutover fail after rehearsal even though the canonical runtime
    # tree had already matched the signed prepare receipt. Keep this shape check aligned with
    # the actual installer output while _require_installed_release remains the authoritative
    # complete-tree digest and identity proof.
    for required in ("VERSION", "plugin", "plugin-ops", "plugin-frontier"):
        candidate = release / required
        try:
            info = candidate.lstat()
        except OSError as exc:
            raise MigrationError("prepared target deployment is missing a required component") from exc
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise MigrationError("prepared target deployment component is unsafe")
    return _snapshot_frozen_config(install_dir)


def _verify_frozen_config(install_dir: Path, snapshot: dict[str, str]) -> None:
    """Prove the pre-prepared target configuration is byte-identical after the legacy data swap."""
    observed = _snapshot_frozen_config(install_dir)
    if observed != snapshot:
        raise MigrationError("prepared target configuration changed during the legacy data swap")


def _run_pixel(pixel: Path, arguments: list[str], *, label: str, env: dict[str, str] | None = None) -> None:
    """Run the trusted target ``pixel`` entry with an exact argument vector.

    Used only for the migration-only restore transaction. The standard restore is never invoked
    through this path with a widened contract; ``--migration`` is an explicit opt-in on the trusted
    restore that builds its allowlist from the authenticated 3.2 root contract.
    """
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        [str(pixel), *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, check=False, env=merged_env,
    )
    if result.returncode:
        if label == "migration commit":
            raise MigrationError("migration commit cleanup failed; the verified new live state is retained and cleanup is pending/failed; the authenticated backup is retained")
        if label == "migration rollback":
            raise MigrationError("migration rollback could not be completed exactly; live state is indeterminate and the authenticated backup is retained")
        raise MigrationError("authenticated migration restore failed; the authenticated backup is retained")


def _backup_recipient_from_identity(identity: Path) -> str:
    """Derive the public age recipient used by the restore's mandatory safety backup.

    A clean-migration activation already authenticates the encrypted backup with ``identity``.
    The nested restore also creates a pre-restore safety backup whenever live destinations
    exist, but that generic restore path normally obtains its recipient from the deployment
    environment. Legacy 3.2.2 installations do not necessarily define that modern variable.
    Derive the public recipient from the exact private identity already bound to this
    activation instead of making the live cutover depend on unrelated environment state.
    """
    try:
        result = subprocess.run(
            ["age-keygen", "-y", str(identity)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise MigrationError(
            "migration safety-backup recipient could not be derived; the authenticated backup is retained"
        ) from exc
    recipient = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"age1[0-9a-z]{58}", recipient):
        raise MigrationError(
            "migration safety-backup recipient could not be derived; the authenticated backup is retained"
        )
    return recipient


def run_restore_migration_swap(
    pixel: Path, backup: Path, identity: Path, signers: Path, receipt: Path, journal: Path,
    contract_sha256: str,
) -> None:
    """Execute the trusted target restore in its explicit migration-only mode.

    The restore builds its allowlist from exactly the authenticated 3.2 root contract (bound to
    ``contract_sha256``), reserves and finalizes the restore receipt, swaps the legacy data,
    restarts services, and writes a root-owned private transaction journal (with rollback still
    armed) before returning without committing. When a prepared deployment spec + stage are
    supplied via the PIXEL_MIGRATION_PREPARE_SPEC/PIXEL_MIGRATION_PREPARE_STAGE environment
    (set by cmd_activate), the same armed transaction also protects the deployment-state
    transitions (the restore passes the strict spec to journal arm and calls the helper install
    before the private-root renames). Standard restore is never widened.
    """
    arguments = ["restore", str(backup), "--identity", str(identity), "--signers", str(signers),
                 "--replace", "--confirm", "--receipt", str(receipt), "--migration", str(journal)]
    spec = os.environ.get("PIXEL_MIGRATION_PREPARE_SPEC")
    stage = os.environ.get("PIXEL_MIGRATION_PREPARE_STAGE")
    if spec and stage:
        arguments += ["--migration-spec", spec, "--migration-stage", stage]
    backup_recipient = _backup_recipient_from_identity(identity)
    _run_pixel(
        pixel,
        arguments,
        label="authenticated migration restore",
        env={
            "PIXEL_MIGRATION_CONTRACT_SHA256": contract_sha256,
            "PIXEL_BACKUP_AGE_RECIPIENT": backup_recipient,
        },
    )


def run_restore_migration_commit(pixel: Path, journal: Path) -> None:
    """Finalize an armed migration swap: delete the old state without restarting services
    (they already run and passed the outer verify)."""
    _run_pixel(pixel, ["restore", "--migration-commit", str(journal)], label="migration commit")


def run_restore_migration_rollback(pixel: Path, journal: Path) -> None:
    """Undo an armed migration swap: restore every pre-migration path byte and restart services."""
    _run_pixel(pixel, ["restore", "--migration-rollback", str(journal)], label="migration rollback")

def _verify_and_build_completion(
    root: Path, install_dir: Path, pixel: Path, plan: dict[str, Any], rehearsal: dict[str, Any],
    restore_receipt: dict[str, Any], restore_receipt_bytes: bytes,
) -> dict[str, Any]:
    """Shared terminal evidence phase: verify the live target deployment and build completion."""
    attestation_path = install_dir / "runtime-attestation.json"
    _remove_preexisting_attestation(attestation_path)
    verify_start = datetime.now(timezone.utc)
    subprocess.run(
        [str(pixel), "verify", "--expected-install-dir", str(install_dir)], cwd=root, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    verify_end = datetime.now(timezone.utc)
    attestation_bytes = read_bytes(attestation_path, private=False)
    attestation = parse_json(attestation_bytes, "runtime attestation")
    if not isinstance(attestation, dict):
        raise MigrationError("runtime attestation must be an object")
    source_commit, source_tree = git_identity(root)
    validate_runtime_attestation(
        attestation, TARGET_PIXEL, source_commit, source_tree, verify_start, verify_end,
    )
    return build_completion(
        root, install_dir, plan, rehearsal, restore_receipt, restore_receipt_bytes,
        attestation_bytes, now=datetime.now, source_identity=(source_commit, source_tree),
    )


# --------------------------------------------------------------------------- #
# Subcommands                                                                 #
# --------------------------------------------------------------------------- #

def _write_evidence(output: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    write_new_private(output, payload)
    print(payload.decode("utf-8"), end="")


def cmd_prepare(args: argparse.Namespace) -> int:
    """Build the exact target release directory and a strict deployment spec (non-live).

    Runs the bounded migration-only prepare phase. It never mutates a live managed path,
    service state, enablement, or the current pointer, and never calls apply.sh against live
    state. Unit-file staging is bounded to the exact Pixel gateway/courier unit dirs from the
    reviewed configure.mjs contract (no arbitrary sudo install surface and no runtime
    unit-parent override). The resulting spec + stage + receipt are the prepared artifacts
    the later activation authenticates and passes to the migration restore.
    """
    root = args.root.resolve()
    install_dir = _as_abs(_require(args.install_dir, "install directory"), "install directory")
    stage = _as_abs(_require(args.stage, "prepare stage"), "prepare stage")
    spec_out = _as_abs(_require(args.spec_out, "prepare spec output"), "prepare spec output")
    receipt_out = _as_abs(_require(args.receipt_out, "prepare receipt output"), "prepare receipt output")
    _outside_repo(stage, root, "prepare stage")
    _outside_repo(spec_out, root, "prepare spec output")
    _outside_repo(receipt_out, root, "prepare receipt output")
    _outside_repo(install_dir, root, "install directory")
    prepare_script = root / "scripts" / "migrate-prepare.sh"
    command = [str(prepare_script), "--install-dir", str(install_dir), "--stage", str(stage),
               "--spec", str(spec_out), "--receipt", str(receipt_out)]
    if args.workspace is not None:
        command += ["--workspace", str(_as_abs(args.workspace, "workspace"))]
    if args.agent_env_dir is not None:
        command += ["--agent-env-dir", str(_as_abs(args.agent_env_dir, "agent env dir"))]
    subprocess.run(command, check=True)
    # Prove the produced spec + receipt are well-formed strict JSON and mutually consistent.
    _load_prepare_spec(spec_out)
    _verify_prepare_receipt(spec_out, receipt_out)
    return 0


def _load_prepare_spec(spec_out: Path) -> dict[str, Any]:
    payload = read_bytes(spec_out, private=True)
    spec = parse_json(payload, "prepare spec")
    if not isinstance(spec, dict) or set(spec.keys()) != {"deploymentItems", "serviceDesired"}:
        raise MigrationError("prepare spec must contain exactly deploymentItems/serviceDesired")
    items = spec["deploymentItems"]
    if not isinstance(items, list) or not items:
        raise MigrationError("prepare spec deploymentItems must be a non-empty list")
    sd = spec.get("serviceDesired")
    if not isinstance(sd, dict) or not sd:
        raise MigrationError("prepare spec serviceDesired must be a non-empty object")
    for unit, rec in sd.items():
        if not isinstance(unit, str) or not isinstance(rec, dict) or set(rec.keys()) != {"enabled", "active"}:
            raise MigrationError("prepare spec serviceDesired entry is malformed")
        if not isinstance(rec["enabled"], bool) or not isinstance(rec["active"], bool):
            raise MigrationError("prepare spec serviceDesired entry must be boolean")
    for item in items:
        if not isinstance(item, dict):
            raise MigrationError("prepare spec item must be an object")
        kind = item.get("kind")
        if kind == "symlink":
            if set(item.keys()) != {"kind", "path", "oldPath", "newPath", "hadOld", "target"}:
                raise MigrationError("prepare spec symlink item must contain exactly kind/path/oldPath/newPath/hadOld/target")
        elif kind in ("config", "unit", "workspace"):
            if set(item.keys()) != {"kind", "path", "oldPath", "newPath", "hadOld", "sha256"}:
                raise MigrationError("prepare spec item must contain exactly kind/path/oldPath/newPath/hadOld/sha256")
        else:
            raise MigrationError("prepare spec item kind is unsupported")
    return spec


PREPARE_RECEIPT_KEYS = {
    "mode", "targetPixel", "specSha256", "candidates", "installManifestSha256",
    "releaseIdentitySha256", "releaseVersion", "releaseTreeSha256", "serviceDesiredSha256",
    "bundleSha256",
}


def _verify_prepare_receipt(spec_path: Path, receipt_path: Path) -> None:
    """Bind the prepared spec + staged candidates + installed release tree to the prepare
    receipt before any live mutation. The receipt records the exact reviewed spec digest,
    every candidate digest, the canonical release-tree digest, and a recomputed bundle digest
    over every exact required field. A stage/spec/release swap after prepare is detected here
    and in the helper's descriptor-bound intake (per-item digest verification during arm).
    """
    spec_bytes = read_bytes(spec_path, private=True)
    spec = parse_json(spec_bytes, "prepare spec")
    if not isinstance(spec, dict) or set(spec.keys()) != {"deploymentItems", "serviceDesired"}:
        raise MigrationError("prepare spec must contain exactly deploymentItems/serviceDesired")
    receipt_bytes = read_bytes(receipt_path, private=True)
    receipt = parse_json(receipt_bytes, "prepare receipt")
    if not isinstance(receipt, dict):
        raise MigrationError("prepare receipt must be an object")
    if set(receipt.keys()) != PREPARE_RECEIPT_KEYS:
        raise MigrationError("prepare receipt must contain exactly the required fields")
    if receipt.get("mode") != "migration-prepare":
        raise MigrationError("prepare receipt mode mismatch")
    if not isinstance(receipt.get("targetPixel"), str) or receipt["targetPixel"] != TARGET_PIXEL:
        raise MigrationError("prepare receipt targetPixel must be the exact target release")
    for key in ("specSha256", "installManifestSha256", "releaseIdentitySha256",
                "releaseTreeSha256", "serviceDesiredSha256", "bundleSha256"):
        _check_hash(receipt.get(key), f"prepare receipt {key}", 64)
    sd_sha = hashlib.sha256(json.dumps(spec["serviceDesired"], sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    if sd_sha != receipt["serviceDesiredSha256"]:
        raise MigrationError("prepare receipt serviceDesired does not match the spec")
    if not isinstance(receipt.get("releaseVersion"), str) or not receipt["releaseVersion"]:
        raise MigrationError("prepare receipt releaseVersion must be present")
    spec_sha = hashlib.sha256(spec_bytes).hexdigest()
    if receipt["specSha256"] != spec_sha:
        raise MigrationError("prepare spec does not match the prepare receipt (specSha256)")
    candidates = receipt.get("candidates")
    if not isinstance(candidates, dict):
        raise MigrationError("prepare receipt has no candidate digests")
    # Exact candidate names/records must match the spec with NO extras and NO omissions.
    expected_names = {os.path.basename(item["newPath"]) for item in spec["deploymentItems"]}
    if set(candidates.keys()) != expected_names:
        raise MigrationError("prepare receipt candidates do not exactly match the spec")
    for item in spec["deploymentItems"]:
        name = os.path.basename(item["newPath"])
        record = candidates.get(name)
        if record is None:
            raise MigrationError(f"prepare receipt is missing candidate binding: {name}")
        if item["kind"] == "symlink":
            if record.get("type") != "symlink" or record.get("target") != item.get("target"):
                raise MigrationError(f"prepare receipt symlink binding mismatch: {name}")
        else:
            if item.get("sha256") is None:
                if record.get("type") != "absent":
                    raise MigrationError(f"prepare receipt absent binding mismatch: {name}")
            elif record.get("type") != "file" or record.get("sha256") != item["sha256"]:
                raise MigrationError(f"prepare receipt candidate digest mismatch: {name}")
    # Recompute the bundle digest over every exact required field; a field dropped/added or
    # a bundle hash drift is a receipt tamper.
    bundle_payload = json.dumps(
        {"specSha256": receipt["specSha256"], "candidates": candidates,
         "installManifestSha256": receipt["installManifestSha256"],
         "releaseIdentitySha256": receipt["releaseIdentitySha256"],
         "releaseVersion": receipt["releaseVersion"],
         "releaseTreeSha256": receipt["releaseTreeSha256"],
         "serviceDesiredSha256": receipt["serviceDesiredSha256"]},
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(bundle_payload).hexdigest() != receipt["bundleSha256"]:
        raise MigrationError("prepare receipt bundle hash does not match its exact fields")


def _release_tree_sha(release_dir: Path) -> str:
    """Fail-closed bounded invocation of the single authoritative release-tree verifier
    (scripts/lib/release-tree-sha.py). This module deliberately carries NO second tree-walk
    implementation: the canonical digest is computed only by the shared helper, invoked with
    ``sys.executable`` and an exact helper path under scripts/lib. The helper must exit 0 and
    print exactly one lowercase 64-hex digest; any nonzero exit, malformed/extra output, or
    timeout is translated into MigrationError without leaking helper output."""
    helper = Path(__file__).resolve().parent / "lib" / "release-tree-sha.py"
    try:
        result = subprocess.run(
            [sys.executable, str(helper), str(release_dir)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
            timeout=RELEASE_TREE_SHA_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise MigrationError("installed release tree verification timed out") from exc
    except OSError as exc:
        raise MigrationError("installed release tree verification could not be run") from exc
    if result.returncode != 0:
        raise MigrationError("installed release tree verification failed")
    if RELEASE_TREE_LINE_RE.fullmatch(result.stdout) is None:
        raise MigrationError("installed release tree verification produced an invalid digest")
    return result.stdout[:-1]


def _require_installed_release(install_dir: Path, receipt_path: Path) -> None:
    """Fail closed unless the exact prepared target release directory is installed and its
    canonical release tree matches the prepare receipt (finding 2). The current pointer must
    never be switched to a nonexistent or untrusted release path, and hashing only the
    manifest file proves nothing about its listed files - the complete tree is reverified at
    every activation point. The durable release-dir install (moving the prepared tree into
    $PIXEL_INSTALL_DIR/releases/$TARGET_PIXEL) is expected to have been completed before activation;
    this guard refuses to proceed otherwise."""
    receipt = parse_json(read_bytes(receipt_path, private=True), "prepare receipt")
    if not isinstance(receipt, dict) or set(receipt.keys()) != PREPARE_RECEIPT_KEYS:
        raise MigrationError("prepare receipt must contain exactly the required fields")
    version = receipt.get("targetPixel")
    if version != TARGET_PIXEL:
        raise MigrationError("prepare receipt targetPixel must be the exact target release")
    release_dir = install_dir / "releases" / version
    if not release_dir.is_dir() or release_dir.is_symlink():
        raise MigrationError(f"prepared release directory is not installed: {release_dir}")
    for key in ("installManifestSha256", "releaseIdentitySha256", "releaseTreeSha256",
                "serviceDesiredSha256", "bundleSha256"):
        _check_hash(receipt.get(key), f"prepare receipt {key}", 64)
    manifest_path = release_dir / "install-manifest.sha256"
    identity_path = release_dir / "release-identity.json"
    version_path = release_dir / "VERSION"
    if not (manifest_path.is_file() and identity_path.is_file() and version_path.is_file()):
        raise MigrationError(f"prepared release directory is incomplete: {release_dir}")
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != receipt["installManifestSha256"]:
        raise MigrationError("installed release manifest does not match the prepared release")
    if hashlib.sha256(identity_path.read_bytes()).hexdigest() != receipt["releaseIdentitySha256"]:
        raise MigrationError("installed release identity does not match the prepared release")
    if version_path.read_text(encoding="utf-8").strip() != receipt["releaseVersion"]:
        raise MigrationError("installed release version does not match the prepared release")
    # Complete-tree reverification (not just the manifest file).
    if _release_tree_sha(release_dir) != receipt["releaseTreeSha256"]:
        raise MigrationError("installed release tree does not match the prepared release")
    # Recompute the bundle digest from the receipt's exact fields; drift is a receipt tamper.
    bundle_payload = json.dumps(
        {"specSha256": receipt["specSha256"], "candidates": receipt["candidates"],
         "installManifestSha256": receipt["installManifestSha256"],
         "releaseIdentitySha256": receipt["releaseIdentitySha256"],
         "releaseVersion": receipt["releaseVersion"],
         "releaseTreeSha256": receipt["releaseTreeSha256"],
         "serviceDesiredSha256": receipt["serviceDesiredSha256"]},
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(bundle_payload).hexdigest() != receipt["bundleSha256"]:
        raise MigrationError("prepare receipt bundle hash does not match its exact fields")
def cmd_plan(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    pixel = _regular_abs(args.pixel or (root / "pixel"), "pixel entry")
    install_dir = _as_abs(_require(args.install_dir, "install directory"), "install directory")
    backup = _regular_abs(_require(args.backup, "backup"), "backup")
    identity = _regular_abs(_require(args.identity, "age identity"), "age identity")
    signers = _regular_abs(_require(args.signers, "allowed signers"), "allowed signers")
    output = _as_abs(_require(args.output, "plan output"), "plan output")
    _outside_repo(output, root, "plan output")
    _outside_repo(install_dir, root, "install directory")
    audit, backup_sha, backup_roots_sha256 = build_legacy_cohort(pixel, backup, identity, signers)
    plan = build_plan(
        root, install_dir, audit, backup, now=datetime.now,
        backup_roots_sha256=backup_roots_sha256, backup_sha=backup_sha,
    )
    _write_evidence(output, plan)
    return 0


def cmd_rehearse(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    pixel = _regular_abs(args.pixel or (root / "pixel"), "pixel entry")
    plan_path = _as_abs(_require(args.plan_path, "plan"), "plan")
    backup = _regular_abs(_require(args.backup, "backup"), "backup")
    identity = _regular_abs(_require(args.identity, "age identity"), "age identity")
    signers = _regular_abs(_require(args.signers, "allowed signers"), "allowed signers")
    rehearsal_root = _as_abs(_require(args.rehearsal_root, "rehearsal root"), "rehearsal root")
    output = _as_abs(_require(args.output, "rehearsal output"), "rehearsal output")
    _outside_repo(output, root, "rehearsal output")
    plan = checked_private_plan(plan_path)
    backup_sha = require_same_backup(plan, backup)
    _rehearsal_root_valid(rehearsal_root, _live_paths(args.workspace, args.openclaw_home))
    require_current_source(root, plan["sourceCommit"], plan["sourceTree"], "rehearsal")
    rehearsal_result = run_restore_rehearse(pixel, backup, identity, signers, rehearsal_root)
    rehearsal = build_rehearsal(root, plan, backup_sha, rehearsal_root, rehearsal_result, now=datetime.now)
    _write_evidence(output, rehearsal)
    return 0


def _remove_preexisting_attestation(attestation_path: Path) -> None:
    if not (attestation_path.exists() or attestation_path.is_symlink()):
        return
    info = attestation_path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
        raise MigrationError("preexisting runtime attestation is unsafe")
    attestation_path.unlink()


def cmd_finalize(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    pixel = _regular_abs(args.pixel or (root / "pixel"), "pixel entry")
    install_dir = _as_abs(_require(args.install_dir, "install directory"), "install directory")
    plan_path = _as_abs(_require(args.plan_path, "plan"), "plan")
    rehearsal_path = _as_abs(_require(args.rehearsal_path, "rehearsal"), "rehearsal")
    backup = _regular_abs(_require(args.backup, "backup"), "backup")
    receipt_path = _regular_abs(_require(args.receipt, "restore receipt"), "restore receipt")
    output = _as_abs(_require(args.output, "completion output"), "completion output")
    _outside_repo(output, root, "completion output")
    _outside_repo(install_dir, root, "install directory")
    _outside_repo(receipt_path, root, "restore receipt")
    _outside_repo(backup, root, "backup")

    plan = checked_private_plan(plan_path)
    rehearsal = checked_private_rehearsal(rehearsal_path, plan)
    require_current_source(root, plan["sourceCommit"], plan["sourceTree"], "finalize")

    backup_sha = require_same_backup(plan, backup)
    if backup_sha != plan["backupSha256"]:
        raise MigrationError("backup identity does not match the plan")

    restore_receipt_bytes = read_bytes(receipt_path, private=True)
    restore_receipt = parse_json(restore_receipt_bytes, "restore receipt")
    if not isinstance(restore_receipt, dict):
        raise MigrationError("restore receipt must be an object")
    rehearsal_generated_at = _check_datetime(rehearsal["generatedAt"], "rehearsal generatedAt")
    validate_restore_receipt(
        restore_receipt,
        rehearsal_generated_at=rehearsal_generated_at,
        finalize_now=datetime.now(timezone.utc),
    )
    if restore_receipt["backupSha256"] != backup_sha or restore_receipt["sourcePixel"] != SOURCE_PIXEL or restore_receipt["targetPixel"] != TARGET_PIXEL:
        raise MigrationError("restore receipt does not match the authenticated backup")

    completion = _verify_and_build_completion(
        root, install_dir, pixel, plan, rehearsal, restore_receipt, restore_receipt_bytes,
    )
    _write_evidence(output, completion)
    return 0


def cmd_activate(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    pixel = _regular_abs(args.pixel or (root / "pixel"), "pixel entry")
    install_dir = _as_abs(_require(args.install_dir, "install directory"), "install directory")
    plan_path = _as_abs(_require(args.plan_path, "plan"), "plan")
    rehearsal_path = _as_abs(_require(args.rehearsal_path, "rehearsal"), "rehearsal")
    backup = _regular_abs(_require(args.backup, "backup"), "backup")
    identity = _regular_abs(_require(args.identity, "age identity"), "age identity")
    signers = _regular_abs(_require(args.signers, "allowed signers"), "allowed signers")
    restore_receipt = _as_abs(_require(args.restore_receipt, "restore receipt output"), "restore receipt output")
    completion = _as_abs(_require(args.completion, "completion output"), "completion output")
    journal = _as_abs(_require(args.journal, "migration journal"), "migration journal")
    migration_spec = _as_abs(args.prepare_spec, "prepare spec") if args.prepare_spec is not None else None
    migration_stage = _as_abs(args.prepare_stage, "prepare stage") if args.prepare_stage is not None else None
    migration_receipt = _as_abs(args.prepare_receipt, "prepare receipt") if args.prepare_receipt is not None else None
    prepare_args = [migration_spec is not None, migration_stage is not None,
                    migration_receipt is not None]
    if any(prepare_args) and not all(prepare_args):
        raise MigrationError("--prepare-spec, --prepare-stage and --prepare-receipt must be supplied together")
    _outside_repo(install_dir, root, "install directory")
    _outside_repo(backup, root, "backup")
    _outside_repo(restore_receipt, root, "restore receipt output")
    _outside_repo(completion, root, "completion output")
    _outside_repo(journal, root, "migration journal")
    if migration_spec is not None:
        _outside_repo(migration_spec, root, "prepare spec")
        _outside_repo(migration_stage, root, "prepare stage")
        _outside_repo(migration_receipt, root, "prepare receipt")
        _verify_prepare_receipt(migration_spec, migration_receipt)
        _require_installed_release(install_dir, migration_receipt)

    plan = checked_private_plan(plan_path)
    rehearsal = checked_private_rehearsal(rehearsal_path, plan)
    require_current_source(root, plan["sourceCommit"], plan["sourceTree"], "activate")

    backup_sha = require_same_backup(plan, backup)
    if backup_sha != plan["backupSha256"]:
        raise MigrationError("backup identity does not match the plan")

    # Validate the pre-prepared target deployment, fingerprint its frozen configuration, and
    # reserve every output/receipt/journal path BEFORE any live mutation.
    expected_active = SOURCE_PIXEL if migration_spec is not None else TARGET_PIXEL
    frozen_config = _validate_prepared_config(install_dir, expected_active=expected_active)
    _require_private_output_parent(restore_receipt, "restore receipt")
    _require_migration_journal_custody(journal)
    subprocess.run(
        [str(Path(__file__).resolve().parents[1] / "scripts/restore-receipt.py"), "reserve", str(completion), str(root)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    swap_attempted = False
    swap_armed = False
    # ``terminal`` marks the boundary after the verified pass completion is durably
    # written and commit begins. Once crossed we never run rollback and never overwrite
    # the pass completion, even if commit cleanup fails: the verified new live state is
    # retained and any old-state cleanup is left pending/failed for an idempotent resume.
    terminal = False
    committed = False
    try:
        # Execute the trusted target restore in its explicit migration-only mode. It accepts
        # exactly the authenticated 3.2 root contract (bound to the plan's root-contract hash),
        # reserves and finalizes the restore receipt, swaps the legacy data, and writes a
        # root-owned private transaction journal. Services are restarted before the swap
        # returns while rollback stays armed (the outer target verify may require them running);
        # commit (delete old state) and rollback are separate explicit control verbs.
        swap_attempted = True
        if migration_spec is not None:
            os.environ["PIXEL_MIGRATION_PREPARE_SPEC"] = str(migration_spec)
            os.environ["PIXEL_MIGRATION_PREPARE_STAGE"] = str(migration_stage)
        try:
            run_restore_migration_swap(
                pixel, backup, identity, signers, restore_receipt, journal, plan["backupRootsSha256"],
            )
        finally:
            os.environ.pop("PIXEL_MIGRATION_PREPARE_SPEC", None)
            os.environ.pop("PIXEL_MIGRATION_PREPARE_STAGE", None)
        swap_armed = True
        restore_receipt_bytes = read_bytes(restore_receipt, private=True)
        restore_receipt_doc = parse_json(restore_receipt_bytes, "restore receipt")
        if not isinstance(restore_receipt_doc, dict):
            raise MigrationError("restore receipt must be an object")
        rehearsal_generated_at = _check_datetime(rehearsal["generatedAt"], "rehearsal generatedAt")
        validate_restore_receipt(
            restore_receipt_doc,
            rehearsal_generated_at=rehearsal_generated_at,
            finalize_now=datetime.now(timezone.utc),
        )
        if (
            restore_receipt_doc["backupSha256"] != backup_sha
            or restore_receipt_doc["sourcePixel"] != SOURCE_PIXEL
            or restore_receipt_doc["targetPixel"] != TARGET_PIXEL
        ):
            raise MigrationError("restore receipt does not match the authenticated backup")

        # Prove the pre-prepared target configuration is byte-identical after the legacy data swap,
        # verify the live deployment and runtime attestation while rollback is still armed, and
        # build the terminal completion receipt.
        _verify_frozen_config(install_dir, frozen_config)
        completion_doc = _verify_and_build_completion(
            root, install_dir, pixel, plan, rehearsal, restore_receipt_doc, restore_receipt_bytes,
        )
        payload = json.dumps(completion_doc, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        _rewrite_reserved(completion, payload)
        print(payload.decode("utf-8"), end="")
        # Terminal boundary: every evidence phase is complete and the verified pass
        # completion is durably written. From here we commit (delete the old state without
        # restarting services; they already run and passed the outer verify) and, on any
        # failure, retain the verified new live state without
        # rolling back or overwriting the pass completion; cleanup stays pending/failed for
        # an idempotent --migration-commit resume.
        terminal = True
        # Reverify the exact installed release (manifest + identity + version) immediately
        # before commit; the current pointer has already switched to it and it must still be
        # byte-exact against the prepare receipt before old state is deleted.
        if migration_receipt is not None:
            _require_installed_release(install_dir, migration_receipt)
        run_restore_migration_commit(pixel, journal)
        committed = True
        return 0
    except MigrationError:
        if terminal:
            # Commit has begun (or completed); never roll back and never rewrite the pass
            # completion. Signal the cleanup-pending/failed state so the operator resumes.
            raise
        rolled_back = False
        if swap_attempted and swap_armed:
            try:
                run_restore_migration_rollback(pixel, journal)
                rolled_back = True
            except MigrationError:
                rolled_back = False
        # If the swap itself failed before arming, its internal rollback is best-effort and
        # unverified, so rolledBack stays conservatively False.
        _write_failure_receipt(completion, rolled_back=rolled_back)
        raise
    except (subprocess.CalledProcessError, OSError, UnicodeError, ValueError, TypeError):
        if terminal:
            raise
        rolled_back = False
        if swap_attempted and swap_armed:
            try:
                run_restore_migration_rollback(pixel, journal)
                rolled_back = True
            except (MigrationError, subprocess.CalledProcessError, OSError, ValueError, TypeError):
                rolled_back = False
        _write_failure_receipt(completion, rolled_back=rolled_back)
        raise


_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "legacy-clean-migration-v1.schema.json"


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Terminal-only clean-migration review, rehearsal, and finalization")
    subparsers = parser.add_subparsers(dest="command", required=True)
    root_default = Path(__file__).resolve().parents[1]

    plan_p = subparsers.add_parser("plan")
    plan_p.set_defaults(command="plan")
    plan_p.add_argument("--root", type=Path, default=root_default)
    plan_p.add_argument("--pixel", type=Path, default=None)
    plan_p.add_argument("--install-dir", type=Path, default=None)
    plan_p.add_argument("--backup", type=Path, default=None)
    plan_p.add_argument("--identity", type=Path, default=None)
    plan_p.add_argument("--signers", type=Path, default=None)
    plan_p.add_argument("--output", type=Path, default=None)

    rehearse_p = subparsers.add_parser("rehearse")
    rehearse_p.set_defaults(command="rehearse")
    rehearse_p.add_argument("--root", type=Path, default=root_default)
    rehearse_p.add_argument("--pixel", type=Path, default=None)
    rehearse_p.add_argument("--plan", type=Path, default=None, dest="plan_path")
    rehearse_p.add_argument("--backup", type=Path, default=None)
    rehearse_p.add_argument("--identity", type=Path, default=None)
    rehearse_p.add_argument("--signers", type=Path, default=None)
    rehearse_p.add_argument("--rehearsal-root", type=Path, default=None)
    rehearse_p.add_argument("--output", type=Path, default=None)
    rehearse_p.add_argument("--workspace", type=Path, default=None)
    rehearse_p.add_argument("--openclaw-home", type=Path, default=None)

    finalize_p = subparsers.add_parser("finalize")
    finalize_p.set_defaults(command="finalize")
    finalize_p.add_argument("--root", type=Path, default=root_default)
    finalize_p.add_argument("--pixel", type=Path, default=None)
    finalize_p.add_argument("--install-dir", type=Path, default=None)
    finalize_p.add_argument("--plan", type=Path, default=None, dest="plan_path")
    finalize_p.add_argument("--rehearsal", type=Path, default=None, dest="rehearsal_path")
    finalize_p.add_argument("--backup", type=Path, default=None)
    finalize_p.add_argument("--receipt", type=Path, default=None)
    finalize_p.add_argument("--output", type=Path, default=None)

    prepare_p = subparsers.add_parser("prepare")
    prepare_p.set_defaults(command="prepare")
    prepare_p.add_argument("--root", type=Path, default=root_default)
    prepare_p.add_argument("--install-dir", type=Path, default=None)
    prepare_p.add_argument("--workspace", type=Path, default=None)
    prepare_p.add_argument("--agent-env-dir", type=Path, default=None)
    prepare_p.add_argument("--stage", type=Path, default=None)
    prepare_p.add_argument("--spec", type=Path, default=None, dest="spec_out")
    prepare_p.add_argument("--receipt", type=Path, default=None, dest="receipt_out")

    activate_p = subparsers.add_parser("activate")
    activate_p.set_defaults(command="activate")
    activate_p.add_argument("--root", type=Path, default=root_default)
    activate_p.add_argument("--pixel", type=Path, default=None)
    activate_p.add_argument("--install-dir", type=Path, default=None)
    activate_p.add_argument("--plan", type=Path, default=None, dest="plan_path")
    activate_p.add_argument("--rehearsal", type=Path, default=None, dest="rehearsal_path")
    activate_p.add_argument("--backup", type=Path, default=None)
    activate_p.add_argument("--identity", type=Path, default=None)
    activate_p.add_argument("--signers", type=Path, default=None)
    activate_p.add_argument("--restore-receipt", type=Path, default=None, dest="restore_receipt")
    activate_p.add_argument("--completion", type=Path, default=None)
    activate_p.add_argument("--journal", type=Path, default=None)
    activate_p.add_argument("--prepare-spec", type=Path, default=None)
    activate_p.add_argument("--prepare-stage", type=Path, default=None)
    activate_p.add_argument("--prepare-receipt", type=Path, default=None)

    return parser.parse_args()


def _require(value: Any, label: str) -> Path:
    if value is None:
        raise MigrationError(f"{label} is required")
    return value


def _as_abs(value: Path, label: str) -> Path:
    path = Path(os.path.abspath(value))
    if path == path.anchor or not path.is_absolute():
        raise MigrationError(f"{label} must be an absolute non-root path")
    return path


def _outside_repo(path: Path, root: Path, label: str) -> None:
    if path == root or root in path.parents:
        raise MigrationError(f"{label} must remain outside the source repository")


def _regular_abs(value: Path, label: str) -> Path:
    path = _as_abs(value, label)
    try:
        if path.resolve(strict=True) != path:
            raise MigrationError(f"{label} path contains a link")
    except OSError as exc:
        raise MigrationError(f"{label} is unavailable") from exc
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
        raise MigrationError(f"{label} must be a regular single-link file")
    return path


def _live_paths(workspace: Path | None, openclaw_home: Path | None) -> list[Path]:
    values = []
    workspace_value = workspace or os.environ.get("PIXEL_WORKSPACE")
    openclaw_value = openclaw_home or os.environ.get("OPENCLAW_HOME")
    for value in (workspace_value, openclaw_value):
        if value:
            values.append(Path(os.path.abspath(value)))
    return values


def _rehearsal_root_valid(rehearsal_root: Path, live: list[Path]) -> None:
    if rehearsal_root == rehearsal_root.anchor or not rehearsal_root.is_absolute():
        raise MigrationError("rehearsal root must be absolute and non-root")
    normalized = Path(os.path.realpath(rehearsal_root))
    if normalized != rehearsal_root:
        raise MigrationError("rehearsal root must be normalized and contain no path swaps")
    if rehearsal_root.exists() or rehearsal_root.is_symlink():
        raise MigrationError("rehearsal root must not already exist")
    for live_root in live:
        if rehearsal_root == live_root or live_root in rehearsal_root.parents:
            raise MigrationError("rehearsal root must be outside live Pixel state")


def main() -> int:
    try:
        args = arguments()
    except SystemExit:
        raise
    try:
        if args.command == "plan":
            return cmd_plan(args)
        if args.command == "prepare":
            return cmd_prepare(args)
        if args.command == "rehearse":
            return cmd_rehearse(args)
        if args.command == "activate":
            return cmd_activate(args)
        return cmd_finalize(args)
    except MigrationError as exc:
        print(f"[pixel] ERROR: {exc}", file=sys.stderr)
        return 2
    except (UnicodeError, OSError, subprocess.CalledProcessError, ValueError, TypeError):
        print("[pixel] ERROR: clean migration failed", file=sys.stderr)
        return 2
    except Exception:
        print("[pixel] ERROR: clean migration failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
