#!/usr/bin/env python3
"""Narrow stdlib helper that reserves and finalizes the private 0600 restore receipt.

The restore path is atomically reserved (as a non-passing marker) before any live-state
transaction, then finalized into the exact pass receipt only after the live restore is
committed, and aborted (removing only the exact reservation) if the restore rolls back.
This keeps a bad/occupied/unsafe/full receipt path from failing the command after an
irreversible live commit with no receipt and no accurate recovery message.

Only the Python standard library is used; supported clean hosts install python3 only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from datetime import datetime, timezone


KIND = "pixel-restore-receipt"
RESERVATION_KIND = "pixel-restore-receipt-reservation"
VERSION_RE = re.compile(r"^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$")
SOURCE_PIXEL = "3.2.2"
_RELEASE_VERSION_MAX_BYTES = 64
_RELEASE_MANIFEST_MAX_BYTES = 1024 * 1024


def _release_identity(entry) -> tuple:
    """Stable release-identity metadata proven equal across pre fstat, post fstat, and path
    lstat: device, inode, regular file type, single link, size, mtime_ns, ctime_ns."""
    return (entry.st_dev, entry.st_ino, stat.S_ISREG(entry.st_mode), entry.st_nlink,
            entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)


def _read_release_file(repo: str, name: str, *, maximum: int) -> bytes:
    """Secure in-tree release-identity read, mirroring the helper's file-custody style.

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
    path = os.path.join(repo, name)
    try:
        descriptor = os.open(path, flags)
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
        current = os.lstat(path)
        if _release_identity(post) != _release_identity(current):
            raise RuntimeError(f"release {name} changed during read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _parse_release_manifest(payload: bytes) -> dict:
    """Strict release-manifest JSON that fails closed on duplicate keys at any depth and on
    non-finite numbers, so a crafted manifest cannot silently bind a second value."""
    def reject_duplicates(pairs):
        value = {}
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


def _load_release_contract() -> str:
    """Derive the exact legacy migration target from the COHERENT in-tree release identity
    (VERSION == RELEASE-MANIFEST pixel == legacy target) of the release tree that physically
    contains this tool.

    This loader establishes coherence only, never authenticity: incoherent field or source
    substitution fails closed, while a coherent future signed release identity is accepted
    only as a release identity. Authenticity and custody come from the exact clean/signed
    release and release-tree gates, never from these agreeing raw files. No CLI/env override,
    basename inference, loose semver, or fallback."""
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
MAX_MARKER_BYTES = 4096

RESERVATION_KEYS = (
    "schemaVersion", "kind", "status", "operation", "generatedAt",
    "reservationId", "dev", "ino", "nlink", "fileMode", "markerSha256",
)

# Test-only hook invoked after the exact reservation is read and validated but before the
# pass receipt is written. An injected test can replace the path here to prove a replaced
# regular file is never truncated by the fd-first rewrite.
_PRE_TRUNCATE_HOOK = None

# Test-only hook invoked after the exact reservation inode is created and captured but
# before the marker is written. An injected test can replace the path here to prove a
# replaced regular file is never removed by the partial-write cleanup.
_RESERVE_PRE_WRITE_HOOK = None

BOUNDARY = (
    "Content-free confirmed restore receipt. The backup was authentically validated, "
    "transactionally swapped, verified live, and automatic rollback was armed. No paths, "
    "identities, credentials, host, provider, or model identity, and no user content are included."
)

PRIVACY = {
    "pathsIncluded": False,
    "hostIdentityIncluded": False,
    "credentialsIncluded": False,
    "signerIdentityIncluded": False,
    "modelProviderIdentityIncluded": False,
    "userContentIncluded": False,
}


class ReceiptError(RuntimeError):
    pass


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        written += os.write(descriptor, view[written:])
    if written != len(payload):
        raise ReceiptError("restore receipt reservation write failed")


def _check_size_via_fd(descriptor: int, expected: int) -> None:
    if os.fstat(descriptor).st_size != expected:
        raise ReceiptError("restore receipt reservation size mismatch")


def _unlink_exact_reservation(receipt_path: str, created_dev: int, created_ino: int) -> None:
    try:
        current = os.lstat(receipt_path)
    except FileNotFoundError:
        return
    if (
        current.st_dev != created_dev
        or current.st_ino != created_ino
        or current.st_uid != os.geteuid()
        or current.st_nlink != 1
        or not stat.S_ISREG(current.st_mode)
        or stat.S_IMODE(current.st_mode) != 0o600
    ):
        raise ReceiptError("restore receipt reservation was replaced; refusing to remove it")
    os.unlink(receipt_path)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> object:
    raise ValueError("non-finite number")


def _validate_target(receipt_path: str, repo: str) -> str:
    if not os.path.isabs(receipt_path) or receipt_path == os.sep:
        raise ReceiptError("restore receipt path must be an absolute non-root path")
    if os.path.realpath(receipt_path) != receipt_path:
        raise ReceiptError("restore receipt path must be normalized")
    receipt_real = os.path.realpath(receipt_path)
    repo_real = os.path.realpath(repo)
    if receipt_real == repo_real or os.path.commonpath([receipt_real, repo_real]) == repo_real:
        raise ReceiptError("restore receipt must remain outside the source repository")
    parent = os.path.dirname(receipt_path)
    if os.path.islink(parent):
        raise ReceiptError("restore receipt directory must not be a symlink")
    if not os.path.isdir(parent):
        raise ReceiptError("restore receipt directory must already exist")
    pinfo = os.lstat(parent)
    if (
        not stat.S_ISDIR(pinfo.st_mode)
        or pinfo.st_uid != os.geteuid()
        or stat.S_IMODE(pinfo.st_mode) != 0o700
    ):
        raise ReceiptError("restore receipt directory must be owner-bound mode 0700")
    return parent


def _validate_reservation(value: dict) -> None:
    if set(value) != set(RESERVATION_KEYS):
        raise ReceiptError("restore receipt reservation has missing or extra fields")
    if value["schemaVersion"] != 1:
        raise ReceiptError("restore receipt reservation schemaVersion is invalid")
    if value["kind"] != RESERVATION_KIND:
        raise ReceiptError("restore receipt reservation kind is invalid")
    if value["status"] != "reserved":
        raise ReceiptError("restore receipt reservation status is invalid")
    if value["operation"] != "restore":
        raise ReceiptError("restore receipt reservation operation is invalid")
    if not isinstance(value["generatedAt"], str):
        raise ReceiptError("restore receipt reservation generatedAt is invalid")
    generated_at = value["generatedAt"]
    if (
        not generated_at.endswith("Z")
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", generated_at) is None
    ):
        raise ReceiptError("restore receipt reservation generatedAt is invalid")
    try:
        datetime.fromisoformat(generated_at[:-1] + "+00:00")
    except ValueError:
        raise ReceiptError("restore receipt reservation generatedAt is invalid")
    if not isinstance(value["reservationId"], str) or re.fullmatch(r"[a-f0-9]{32}", value["reservationId"]) is None:
        raise ReceiptError("restore receipt reservation identity is invalid")
    for key in ("dev", "ino", "nlink", "fileMode"):
        if type(value[key]) is not int:
            raise ReceiptError("restore receipt reservation inode metadata is invalid")
    if value["dev"] < 0 or value["ino"] < 0:
        raise ReceiptError("restore receipt reservation inode metadata is invalid")
    if value["nlink"] != 1:
        raise ReceiptError("restore receipt reservation must be a single-link file")
    if value["fileMode"] != 0o600:
        raise ReceiptError("restore receipt reservation must be owner-bound mode 0600")
    body = {key: child for key, child in value.items() if key != "markerSha256"}
    if value["markerSha256"] != hashlib.sha256(_canonical(body)).hexdigest():
        raise ReceiptError("restore receipt reservation hash is stale")


def _open_reservation_fd(path: str) -> int:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise ReceiptError("restore receipt reservation is unreadable") from exc


def _read_marker_from_fd(descriptor: int) -> tuple[dict, os.stat_result]:
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ReceiptError("restore receipt reservation must be a regular single-link file")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ReceiptError("restore receipt reservation must be owner-bound mode 0600")
    payload = os.read(descriptor, MAX_MARKER_BYTES + 1)
    if not payload:
        raise ReceiptError("restore receipt reservation is empty")
    if len(payload) > MAX_MARKER_BYTES:
        raise ReceiptError("restore receipt reservation is oversized")
    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_nonfinite,
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReceiptError("restore receipt reservation is malformed") from exc
    if not isinstance(value, dict):
        raise ReceiptError("restore receipt reservation is malformed")
    _validate_reservation(value)
    after = os.fstat(descriptor)
    if (
        after.st_ino != info.st_ino
        or after.st_dev != info.st_dev
        or after.st_nlink != info.st_nlink
        or stat.S_IMODE(after.st_mode) != stat.S_IMODE(info.st_mode)
    ):
        raise ReceiptError("restore receipt reservation was replaced or altered")
    if (
        value["ino"] != info.st_ino
        or value["dev"] != info.st_dev
        or value["nlink"] != info.st_nlink
        or value["fileMode"] != stat.S_IMODE(info.st_mode)
    ):
        raise ReceiptError("restore receipt reservation was replaced or altered")
    return value, info


def reserve(receipt_path: str, repo: str) -> None:
    parent = _validate_target(receipt_path, repo)
    if os.path.lexists(receipt_path):
        raise ReceiptError("restore receipt already exists")
    descriptor = -1
    created = None
    try:
        descriptor = os.open(
            receipt_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        created = (info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ReceiptError("restore receipt reservation must be a regular single-link file")
        if _RESERVE_PRE_WRITE_HOOK is not None:
            _RESERVE_PRE_WRITE_HOOK(receipt_path)
        marker = {
            "schemaVersion": 1,
            "kind": RESERVATION_KIND,
            "status": "reserved",
            "operation": "restore",
            "generatedAt": _utc_now(),
            "reservationId": secrets.token_hex(16),
            "dev": info.st_dev,
            "ino": info.st_ino,
            "nlink": info.st_nlink,
            "fileMode": stat.S_IMODE(info.st_mode),
            "markerSha256": None,
        }
        body = {key: value for key, value in marker.items() if key != "markerSha256"}
        marker["markerSha256"] = hashlib.sha256(_canonical(body)).hexdigest()
        payload = _canonical(marker) + b"\n"
        _write_all(descriptor, payload)
        _check_size_via_fd(descriptor, len(payload))
        os.fsync(descriptor)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if created is not None:
            _unlink_exact_reservation(receipt_path, created[0], created[1])
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    _ = parent


def _validate_finalize_args(
    backup_sha: str, source_pixel: str, target_pixel: str, repo: str,
) -> None:
    if not isinstance(backup_sha, str) or re.fullmatch(r"[a-f0-9]{64}", backup_sha) is None:
        raise ReceiptError("invalid backup checksum")
    if source_pixel != SOURCE_PIXEL:
        raise ReceiptError("invalid source pixel")
    if target_pixel != TARGET_PIXEL:
        raise ReceiptError("invalid target pixel")
    if not os.path.isabs(repo) or not os.path.isdir(repo) or os.path.realpath(repo) != repo:
        raise ReceiptError("invalid source repository")


def finalize(
    receipt_path: str, backup_sha: str, source_pixel: str, target_pixel: str,
    repo: str, knowledge_in_backup: bool,
) -> None:
    _validate_target(receipt_path, repo)
    _validate_finalize_args(backup_sha, source_pixel, target_pixel, repo)
    if type(knowledge_in_backup) is not bool:
        raise ReceiptError("invalid knowledge_in_backup")
    descriptor = _open_reservation_fd(receipt_path)
    try:
        marker, info = _read_marker_from_fd(descriptor)
        if _PRE_TRUNCATE_HOOK is not None:
            _PRE_TRUNCATE_HOOK(receipt_path)
        receipt = {
            "schemaVersion": 1,
            "kind": KIND,
            "status": "pass",
            "mode": "restore",
            "verified": True,
            "automaticRollbackArmed": True,
            "knowledgeDeletionReconciled": knowledge_in_backup,
            "historicalKeyWrappingRemoved": knowledge_in_backup,
            "backupSha256": backup_sha,
            "sourcePixel": source_pixel,
            "targetPixel": target_pixel,
            "receiptSha256": None,
            "generatedAt": _utc_now(),
            "privacy": PRIVACY,
            "boundary": BOUNDARY,
        }
        body = {key: value for key, value in receipt.items() if key != "receiptSha256"}
        receipt["receiptSha256"] = hashlib.sha256(_canonical(body)).hexdigest()
        payload = json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
        before = (info.st_ino, info.st_dev, info.st_nlink, stat.S_IMODE(info.st_mode))
        os.fchmod(descriptor, 0o600)
        os.lseek(descriptor, 0, os.SEEK_SET)
        os.ftruncate(descriptor, 0)
        _write_all(descriptor, payload)
        _check_size_via_fd(descriptor, len(payload))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        after = os.lstat(receipt_path)
        if (
            after.st_ino != before[0]
            or after.st_dev != before[1]
            or after.st_nlink != before[2]
            or stat.S_IMODE(after.st_mode) != 0o600
        ):
            raise ReceiptError("restore receipt reservation was replaced or altered")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def abort(receipt_path: str, repo: str) -> None:
    _validate_target(receipt_path, repo)
    if not os.path.lexists(receipt_path):
        return
    descriptor = _open_reservation_fd(receipt_path)
    try:
        marker, info = _read_marker_from_fd(descriptor)
        os.close(descriptor)
        descriptor = -1
        current = os.lstat(receipt_path)
        if (
            current.st_ino != info.st_ino
            or current.st_dev != info.st_dev
            or current.st_nlink != info.st_nlink
            or stat.S_IMODE(current.st_mode) != stat.S_IMODE(info.st_mode)
        ):
            raise ReceiptError("restore receipt reservation was replaced or altered")
        os.unlink(receipt_path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _as_bool(value: str) -> bool:
    if value == "1":
        return True
    if value == "0":
        return False
    raise ReceiptError("knowledge_in_backup must be 0 or 1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reserve, finalize, or abort a private restore receipt")
    subparsers = parser.add_subparsers(dest="command", required=True)

    reserve_p = subparsers.add_parser("reserve")
    reserve_p.add_argument("receipt", help="absolute non-root new receipt path")
    reserve_p.add_argument("repo", help="source repository root")

    finalize_p = subparsers.add_parser("finalize")
    finalize_p.add_argument("receipt", help="absolute non-root receipt path")
    finalize_p.add_argument("backup_sha", help="authenticated backup SHA-256")
    finalize_p.add_argument("source_pixel", help="bound source Pixel version")
    finalize_p.add_argument("target_pixel", help="bound target Pixel version")
    finalize_p.add_argument("repo", help="source repository root")
    finalize_p.add_argument("knowledge_in_backup", help="1 if the knowledge vault was in the backup")

    abort_p = subparsers.add_parser("abort")
    abort_p.add_argument("receipt", help="absolute non-root receipt path")
    abort_p.add_argument("repo", help="source repository root")

    args = parser.parse_args(argv)
    try:
        if args.command == "reserve":
            reserve(args.receipt, args.repo)
        elif args.command == "finalize":
            finalize(
                args.receipt, args.backup_sha, args.source_pixel, args.target_pixel,
                args.repo, _as_bool(args.knowledge_in_backup),
            )
        else:
            abort(args.receipt, args.repo)
    except ReceiptError as exc:
        print(f"restore-receipt: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("restore-receipt: restore receipt failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
