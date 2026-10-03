#!/usr/bin/env python3
"""Verify installed plugin package files against a pinned npm archive."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import tempfile


MAX_MEMBERS = 10_000
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
GENERATED_INSTALL_LOCK = Path("node_modules/.package-lock.json")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
PACKAGE_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
EXTRA_LOCK_RECORD_KEYS = {
    "bin", "cpu", "dependencies", "engines", "funding", "inBundle", "integrity",
    "license", "optional", "optionalDependencies", "os", "peer", "peerDependencies",
    "peerDependenciesMeta", "resolved", "version",
}
MAX_INSTALL_ENTRIES = 100_000
MAX_INSTALL_FILE_BYTES = 2 * 1024 * 1024 * 1024
MAX_INSTALL_TOTAL_BYTES = 4 * 1024 * 1024 * 1024


class VerificationError(RuntimeError):
    pass


def digest_stream(stream, expected_size: int) -> str:
    digest = hashlib.sha256()
    observed = 0
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            break
        observed += len(block)
        if observed > expected_size or observed > MAX_FILE_BYTES:
            raise VerificationError("archive member exceeded its declared or allowed size")
        digest.update(block)
    if observed != expected_size:
        raise VerificationError("archive member size does not match its payload")
    return digest.hexdigest()


def package_relative(name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or path.parts[0] != "package":
        raise VerificationError(f"archive member is outside package/: {name}")
    relative = path.relative_to("package")
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise VerificationError(f"archive member has an unsafe path: {name}")
    return Path(*relative.parts)


def archive_json(bundle: tarfile.TarFile, member: tarfile.TarInfo, label: str) -> dict:
    if not member.isfile() or member.size < 2 or member.size > MAX_FILE_BYTES:
        raise VerificationError(f"{label} is not a bounded regular file")
    extracted = bundle.extractfile(member)
    if extracted is None:
        raise VerificationError(f"{label} cannot be read")
    try:
        value = json.loads(extracted.read(MAX_FILE_BYTES + 1))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"{label} must be an object")
    return value


def archive_install_metadata(
    bundle: tarfile.TarFile, members: dict[Path, tarfile.TarInfo],
) -> tuple[set[Path], dict | None]:
    shrinkwrap_member = members.get(Path("npm-shrinkwrap.json"))
    if shrinkwrap_member is None:
        return set(), None
    shrinkwrap = archive_json(bundle, shrinkwrap_member, "npm-shrinkwrap.json")
    packages = shrinkwrap.get("packages")
    if not isinstance(packages, dict):
        raise VerificationError("npm-shrinkwrap.json has no package inventory")
    roots: set[Path] = set()
    for name, record in packages.items():
        if not isinstance(name, str) or not isinstance(record, dict) or record.get("optional") is not True:
            continue
        pure = PurePosixPath(name)
        if (
            pure.is_absolute() or not pure.parts or pure.parts[0] != "node_modules"
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise VerificationError("npm-shrinkwrap.json has an unsafe optional package path")
        relative = Path(*pure.parts)
        package_json = members.get(relative / "package.json")
        if package_json is None:
            has_archived_payload = any(
                candidate == relative or relative in candidate.parents for candidate in members
            )
            if has_archived_payload:
                raise VerificationError(f"optional package has no archived package.json: {relative}")
            continue
        metadata = archive_json(bundle, package_json, f"{relative}/package.json")
        package_name = metadata.get("name")
        expected_name = pure.parts[-1] if len(pure.parts) < 3 or not pure.parts[-2].startswith("@") else f"{pure.parts[-2]}/{pure.parts[-1]}"
        if package_name != expected_name:
            raise VerificationError(f"optional package identity differs from its path: {relative}")
        roots.add(relative)
    return roots, shrinkwrap


def absent_optional_root(relative: Path, root: Path, optional_roots: set[Path]) -> bool:
    for optional in optional_roots:
        if relative == optional or optional in relative.parents:
            destination = root / optional
            return not os.path.lexists(destination)
    return False


def installed_json(path: Path, label: str) -> dict:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise VerificationError(f"installed {label} is missing") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise VerificationError(f"installed {label} is not a regular file")
    if metadata.st_size < 2 or metadata.st_size > MAX_FILE_BYTES:
        raise VerificationError(f"installed {label} is not bounded")
    try:
        value = json.loads(path.read_bytes())
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"installed {label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise VerificationError(f"installed {label} must be an object")
    return value


def flat_lock_package(name: str) -> tuple[Path, str]:
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise VerificationError("installed shrinkwrap has an unsafe package path")
    if len(pure.parts) == 2 and pure.parts[0] == "node_modules" and not pure.parts[1].startswith("@"):
        return Path(*pure.parts), pure.parts[1]
    if (
        len(pure.parts) == 3 and pure.parts[0] == "node_modules"
        and pure.parts[1].startswith("@") and len(pure.parts[1]) > 1
        and not pure.parts[2].startswith("@")
    ):
        return Path(*pure.parts), f"{pure.parts[1]}/{pure.parts[2]}"
    raise VerificationError("installed shrinkwrap added a non-flat package path")


def version_matches(spec: str, version: str) -> bool:
    observed = SEMVER.fullmatch(version)
    if observed is None:
        return False
    if spec == version:
        return True
    if spec.startswith(">="):
        minimum = SEMVER.fullmatch(spec[2:])
        return minimum is not None and tuple(int(value) for value in observed.groups()) >= tuple(
            int(value) for value in minimum.groups()
        )
    if len(spec) < 2 or spec[0] not in {"^", "~"}:
        return False
    minimum = SEMVER.fullmatch(spec[1:])
    if minimum is None:
        return False
    current = tuple(int(value) for value in observed.groups())
    floor = tuple(int(value) for value in minimum.groups())
    if current < floor:
        return False
    if spec[0] == "~":
        return current[:2] == floor[:2]
    if floor[0] > 0:
        return current[0] == floor[0]
    if floor[1] > 0:
        return current[:2] == floor[:2]
    return current == floor


def parse_allowed_peers(values: list[str]) -> dict[str, tuple[str, Path]]:
    result: dict[str, tuple[str, Path]] = {}
    for value in values:
        try:
            identity, raw_path = value.split("=", 1)
            package_name, version = identity.rsplit("@", 1)
        except ValueError as exc:
            raise VerificationError("allowed peer must be NAME@VERSION=ABSOLUTE_PATH") from exc
        if (
            PACKAGE_NAME.fullmatch(package_name) is None
            or SEMVER.fullmatch(version) is None
            or package_name in result
        ):
            raise VerificationError("allowed peer identity is invalid or duplicated")
        path = Path(raw_path)
        if not path.is_absolute():
            raise VerificationError("allowed peer path must be absolute")
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise VerificationError("allowed peer path does not exist") from exc
        if not resolved.is_dir():
            raise VerificationError("allowed peer path must be a directory")
        result[package_name] = version, resolved
    return result


def peer_relative_path(package_name: str) -> Path:
    if package_name.startswith("@"):
        scope, leaf = package_name.split("/", 1)
        return Path("node_modules", scope, leaf)
    return Path("node_modules", package_name)


def verify_allowed_peer_link(
    candidate: Path,
    relative: Path,
    package_manifest: dict,
    allowed_peers: dict[str, tuple[str, Path]],
) -> bool:
    matching = [name for name in allowed_peers if peer_relative_path(name) == relative]
    if not matching:
        return False
    package_name = matching[0]
    expected_version, expected_root = allowed_peers[package_name]
    peer_dependencies = package_manifest.get("peerDependencies", {})
    peer_metadata = package_manifest.get("peerDependenciesMeta", {})
    if not isinstance(peer_dependencies, dict) or not isinstance(peer_metadata, dict):
        raise VerificationError("package.json peer dependency metadata must be objects")
    metadata = peer_metadata.get(package_name)
    spec = peer_dependencies.get(package_name)
    if not isinstance(metadata, dict) or metadata.get("optional") is not True or not isinstance(spec, str):
        raise VerificationError(f"installed peer link is not an optional declared peer: {relative}")
    if not candidate.is_symlink():
        raise VerificationError(f"installed peer link is not a symlink: {relative}")
    try:
        observed_root = candidate.resolve(strict=True)
    except OSError as exc:
        raise VerificationError(f"installed peer link is broken: {relative}") from exc
    if observed_root != expected_root:
        raise VerificationError(f"installed peer link targets a different runtime: {relative}")
    peer_manifest = installed_json(expected_root / "package.json", f"{package_name} peer package.json")
    if (
        peer_manifest.get("name") != package_name
        or peer_manifest.get("version") != expected_version
        or not version_matches(spec, expected_version)
    ):
        raise VerificationError(f"installed peer link has a different identity: {relative}")
    return True


def dependency_specs(record: dict) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for field in ("dependencies", "optionalDependencies", "peerDependencies"):
        values = record.get(field, {})
        if not isinstance(values, dict):
            raise VerificationError(f"installed shrinkwrap {field} must be an object")
        for name, spec in values.items():
            if not isinstance(name, str) or not isinstance(spec, str):
                raise VerificationError(f"installed shrinkwrap {field} entries must be strings")
            result.setdefault(name, set()).add(spec)
    return result


def merge_specs(target: dict[str, set[str]], record: dict) -> None:
    for name, specs in dependency_specs(record).items():
        target.setdefault(name, set()).update(specs)


def verify_added_peer_record(name: str, record: dict, root: Path) -> tuple[Path, str, str]:
    relative, package_name = flat_lock_package(name)
    if set(record) - EXTRA_LOCK_RECORD_KEYS:
        raise VerificationError(f"installed shrinkwrap added unsupported metadata: {relative}")
    if record.get("optional") is not True or record.get("peer") is not True or record.get("inBundle") is not True:
        raise VerificationError(f"installed shrinkwrap added non-optional peer metadata: {relative}")
    version = record.get("version")
    resolved = record.get("resolved")
    integrity = record.get("integrity")
    if not isinstance(version, str) or SEMVER.fullmatch(version) is None:
        raise VerificationError(f"installed shrinkwrap added an invalid version: {relative}")
    leaf = package_name.split("/", 1)[-1]
    expected_url = f"https://registry.npmjs.org/{package_name}/-/{leaf}-{version}.tgz"
    if resolved != expected_url or not isinstance(integrity, str) or not integrity.startswith("sha512-"):
        raise VerificationError(f"installed shrinkwrap added an untrusted package source: {relative}")
    try:
        decoded = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VerificationError(f"installed shrinkwrap added invalid integrity: {relative}") from exc
    if len(decoded) != 64:
        raise VerificationError(f"installed shrinkwrap added invalid integrity: {relative}")
    if os.path.lexists(root / relative):
        raise VerificationError(f"unarchived peer package was installed: {relative}")
    return relative, package_name, version


def lock_package_path(name: str) -> tuple[Path, str]:
    pure = PurePosixPath(name)
    if (
        pure.is_absolute() or not pure.parts or pure.parts[0] != "node_modules"
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise VerificationError("npm-shrinkwrap.json has an unsafe package path")
    indexes = [index for index, part in enumerate(pure.parts) if part == "node_modules"]
    tail = pure.parts[indexes[-1] + 1:]
    if len(tail) == 1 and not tail[0].startswith("@"):
        package_name = tail[0]
    elif len(tail) == 2 and tail[0].startswith("@") and len(tail[0]) > 1 and not tail[1].startswith("@"):
        package_name = f"{tail[0]}/{tail[1]}"
    else:
        raise VerificationError("npm-shrinkwrap.json has an invalid package identity path")
    if PACKAGE_NAME.fullmatch(package_name) is None:
        raise VerificationError("npm-shrinkwrap.json has an invalid package name")
    return Path(*pure.parts), package_name


def verify_registry_record(relative: Path, package_name: str, record: dict) -> str:
    version = record.get("version")
    resolved = record.get("resolved")
    integrity = record.get("integrity")
    if not isinstance(version, str) or SEMVER.fullmatch(version) is None:
        raise VerificationError(f"npm-shrinkwrap.json has an invalid package version: {relative}")
    leaf = package_name.split("/", 1)[-1]
    expected_url = f"https://registry.npmjs.org/{package_name}/-/{leaf}-{version}.tgz"
    if resolved != expected_url or not isinstance(integrity, str) or not integrity.startswith("sha512-"):
        raise VerificationError(f"npm-shrinkwrap.json has an untrusted package source: {relative}")
    try:
        decoded = base64.b64decode(integrity.removeprefix("sha512-"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VerificationError(f"npm-shrinkwrap.json has invalid package integrity: {relative}") from exc
    if len(decoded) != 64:
        raise VerificationError(f"npm-shrinkwrap.json has invalid package integrity: {relative}")
    return version


def installed_lock_package_roots(
    root: Path,
    members: dict[Path, tarfile.TarInfo],
    archived_shrinkwrap: dict | None,
) -> dict[Path, str]:
    if archived_shrinkwrap is None:
        return {}
    packages = archived_shrinkwrap.get("packages")
    if not isinstance(packages, dict):
        raise VerificationError("npm-shrinkwrap.json has no package inventory")
    installed: dict[Path, str] = {}
    for name, record in packages.items():
        if name == "":
            continue
        if not isinstance(name, str) or not isinstance(record, dict):
            raise VerificationError("npm-shrinkwrap.json package record must be an object")
        relative, package_name = lock_package_path(name)
        package_root = root / relative
        if not os.path.lexists(package_root):
            continue
        if members.get(relative / "package.json") is not None:
            continue
        if package_root.is_symlink() or not package_root.is_dir():
            raise VerificationError(f"lock-declared package root is not a real directory: {relative}")
        version = verify_registry_record(relative, package_name, record)
        metadata = installed_json(package_root / "package.json", f"{relative}/package.json")
        if metadata.get("name") != package_name or metadata.get("version") != version:
            raise VerificationError(f"installed lock-declared package has a different identity: {relative}")
        installed[relative] = package_name
    return installed


def belongs_to(relative: Path, roots: dict[Path, str]) -> bool:
    return any(relative == package_root or package_root in relative.parents for package_root in roots)


def safe_internal_symlink(candidate: Path, root: Path, lock_roots: dict[Path, str]) -> None:
    if not candidate.is_symlink():
        return
    try:
        resolved = candidate.resolve(strict=True)
        resolved_relative = resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise VerificationError("installed dependency has an escaping or broken symlink") from exc
    if not belongs_to(resolved_relative, lock_roots):
        raise VerificationError("installed dependency symlink does not target a locked package")


def verify_installed_shrinkwrap(
    bundle: tarfile.TarFile,
    members: dict[Path, tarfile.TarInfo],
    archived: dict,
    destination: Path,
    root: Path,
) -> int:
    observed = installed_json(destination, "npm-shrinkwrap.json")
    archived_packages = archived.get("packages")
    observed_packages = observed.get("packages")
    if not isinstance(archived_packages, dict) or not isinstance(observed_packages, dict):
        raise VerificationError("npm-shrinkwrap.json has no package inventory")
    if {key: value for key, value in observed.items() if key != "packages"} != {
        key: value for key, value in archived.items() if key != "packages"
    }:
        raise VerificationError("installed npm-shrinkwrap.json changed release metadata")

    package_member = members.get(Path("package.json"))
    if package_member is None:
        raise VerificationError("archive does not contain package.json")
    package_manifest = archive_json(bundle, package_member, "package.json")
    bundle_dependencies = package_manifest.get(
        "bundledDependencies", package_manifest.get("bundleDependencies", []),
    )
    if not isinstance(bundle_dependencies, list) or not all(isinstance(value, str) for value in bundle_dependencies):
        raise VerificationError("package.json bundledDependencies must be a string array")

    for name, expected in archived_packages.items():
        actual = observed_packages.get(name)
        if not isinstance(name, str) or not isinstance(expected, dict) or not isinstance(actual, dict):
            raise VerificationError("installed npm-shrinkwrap.json removed or changed a package record")
        normalized = dict(actual)
        if "inBundle" not in expected and "inBundle" in normalized:
            if normalized.pop("inBundle") is not True:
                raise VerificationError("installed npm-shrinkwrap.json has invalid bundle metadata")
        if name == "" and "bundleDependencies" not in expected and "bundleDependencies" in normalized:
            if normalized.pop("bundleDependencies") != bundle_dependencies:
                raise VerificationError("installed npm-shrinkwrap.json changed bundled dependencies")
        if normalized != expected:
            raise VerificationError(f"installed npm-shrinkwrap.json changed package metadata: {name or '<root>'}")

    additions = {name: value for name, value in observed_packages.items() if name not in archived_packages}
    pending: dict[str, tuple[Path, str, str, dict]] = {}
    for name, record in additions.items():
        if not isinstance(name, str) or not isinstance(record, dict):
            raise VerificationError("installed npm-shrinkwrap.json added an invalid package record")
        relative, package_name, version = verify_added_peer_record(name, record, root)
        pending[name] = (relative, package_name, version, record)

    references: dict[str, set[str]] = {}
    for record in archived_packages.values():
        if not isinstance(record, dict):
            raise VerificationError("npm-shrinkwrap.json package record must be an object")
        merge_specs(references, record)
    while pending:
        accepted = None
        for lock_name, (_, package_name, version, record) in pending.items():
            if any(version_matches(spec, version) for spec in references.get(package_name, set())):
                accepted = lock_name, record
                break
        if accepted is None:
            raise VerificationError("installed npm-shrinkwrap.json added unreachable peer metadata")
        lock_name, record = accepted
        merge_specs(references, record)
        del pending[lock_name]
    return len(additions)


def verify_installed_inventory(
    root: Path,
    members: dict[Path, tarfile.TarInfo],
    package_manifest: dict,
    allowed_peers: dict[str, tuple[str, Path]],
    archived_shrinkwrap: dict | None,
) -> tuple[int, int]:
    expected = set(members)
    lock_roots = installed_lock_package_roots(root, members, archived_shrinkwrap)
    saw_generated_lock = False
    linked_peers = 0
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*subdirectories, *filenames]:
            candidate = base / name
            relative = candidate.relative_to(root)
            if candidate.is_dir() and not candidate.is_symlink():
                continue
            if relative in expected:
                continue
            if relative == GENERATED_INSTALL_LOCK:
                installed_json(candidate, str(GENERATED_INSTALL_LOCK))
                saw_generated_lock = True
                continue
            if verify_allowed_peer_link(candidate, relative, package_manifest, allowed_peers):
                linked_peers += 1
                continue
            if belongs_to(relative, lock_roots):
                safe_internal_symlink(candidate, root, lock_roots)
                continue
            if (
                len(relative.parts) == 3 and relative.parts[:2] == ("node_modules", ".bin")
                and candidate.is_symlink()
            ):
                safe_internal_symlink(candidate, root, lock_roots)
                continue
            raise VerificationError(f"installed package has an unexpected payload: {relative}")
    if (root / GENERATED_INSTALL_LOCK).exists() and not saw_generated_lock:
        raise VerificationError("generated npm install lock was not inspected")
    return linked_peers, len(lock_roots)


def digest_file(path: Path, metadata: os.stat_result) -> tuple[str, int]:
    if metadata.st_size > MAX_INSTALL_FILE_BYTES:
        raise VerificationError("installed dependency file exceeds its size limit")
    flags = (
        os.O_RDONLY | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise VerificationError("installed dependency changed during hashing")
        digest = hashlib.sha256()
        total = 0
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > metadata.st_size or total > MAX_INSTALL_FILE_BYTES:
                raise VerificationError("installed dependency changed size during hashing")
            digest.update(block)
        if total != metadata.st_size:
            raise VerificationError("installed dependency changed size during hashing")
        return digest.hexdigest(), total
    finally:
        os.close(descriptor)


def installed_tree(root: Path) -> dict[str, int | str]:
    records: list[tuple[str, str, int, int, str]] = []
    total_bytes = 0
    for directory, subdirectories, filenames in os.walk(root, followlinks=False):
        subdirectories.sort()
        filenames.sort()
        base = Path(directory)
        for name in [*subdirectories, *filenames]:
            candidate = base / name
            relative = candidate.relative_to(root).as_posix()
            metadata = candidate.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                records.append((relative, "link", mode, 0, os.readlink(candidate)))
            elif stat.S_ISDIR(metadata.st_mode):
                records.append((relative, "directory", mode, 0, ""))
            elif stat.S_ISREG(metadata.st_mode):
                file_digest, size = digest_file(candidate, metadata)
                total_bytes += size
                if total_bytes > MAX_INSTALL_TOTAL_BYTES:
                    raise VerificationError("installed dependency tree exceeds its size limit")
                records.append((relative, "file", mode, size, file_digest))
            else:
                raise VerificationError("installed dependency tree contains a special file")
            if len(records) > MAX_INSTALL_ENTRIES:
                raise VerificationError("installed dependency tree has too many entries")
    digest = hashlib.sha256()
    for record in sorted(records):
        digest.update(json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return {"sha256": digest.hexdigest(), "entries": len(records), "bytes": total_bytes}


def archive_digest(archive: Path) -> str:
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def expected_receipt(
    archive: Path,
    root: Path,
    package_manifest: dict,
    allowed_peers: dict[str, tuple[str, Path]],
    lock_packages: int,
) -> dict:
    package_name = package_manifest.get("name")
    package_version = package_manifest.get("version")
    if not isinstance(package_name, str) or not isinstance(package_version, str):
        raise VerificationError("package.json has no exact package identity")
    return {
        "schemaVersion": 1,
        "archiveSha256": archive_digest(archive),
        "package": {"name": package_name, "version": package_version},
        "allowedPeers": [
            {"name": name, "version": version}
            for name, (version, _) in sorted(allowed_peers.items())
        ],
        "verifiedLockPackages": lock_packages,
        "tree": installed_tree(root),
    }


def write_private_json(path: Path, value: dict) -> None:
    if not path.is_absolute():
        raise VerificationError("plugin integrity receipt path must be absolute")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise VerificationError("plugin integrity receipt parent must be a directory")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def bind_receipt(
    receipt: Path | None,
    write_receipt: Path | None,
    required: bool,
    expected: dict,
) -> bool:
    if receipt is not None and write_receipt is not None:
        raise VerificationError("choose either receipt verification or receipt recording")
    if write_receipt is not None:
        write_private_json(write_receipt, expected)
        receipt = write_receipt
    if receipt is None:
        if required:
            raise VerificationError("installed lock dependencies require an integrity receipt")
        return False
    if not receipt.exists():
        if required:
            raise VerificationError("installed lock dependency integrity receipt is missing")
        return False
    observed = installed_json(receipt, "plugin integrity receipt")
    if os.name != "nt" and stat.S_IMODE(receipt.lstat().st_mode) & 0o077:
        raise VerificationError("plugin integrity receipt permissions are not private")
    if observed != expected:
        raise VerificationError("installed plugin tree differs from its integrity receipt")
    return True


def verify(
    archive: Path,
    root: Path,
    allowed_peers: dict[str, tuple[str, Path]] | None = None,
    receipt: Path | None = None,
    write_receipt: Path | None = None,
) -> dict[str, int | bool]:
    archive = archive.resolve(strict=True)
    root = root.resolve(strict=True)
    if not archive.is_file() or not root.is_dir():
        raise VerificationError("archive and installed root must exist")
    files = 0
    total_bytes = 0
    saw_package_json = False
    omitted_optional: set[Path] = set()
    with tarfile.open(archive, mode="r:gz") as bundle:
        raw_members = bundle.getmembers()
        if len(raw_members) > MAX_MEMBERS:
            raise VerificationError("archive has too many members")
        members: dict[Path, tarfile.TarInfo] = {}
        for member in raw_members:
            relative = package_relative(member.name)
            if relative in members:
                raise VerificationError(f"archive contains a duplicate member: {relative}")
            members[relative] = member
        optional_roots, archived_shrinkwrap = archive_install_metadata(bundle, members)
        package_member = members.get(Path("package.json"))
        if package_member is None:
            raise VerificationError("archive does not contain package.json")
        package_manifest = archive_json(bundle, package_member, "package.json")
        omitted_peer_metadata = 0
        for relative, member in members.items():
            destination = root / relative
            if absent_optional_root(relative, root, optional_roots):
                omitted_optional.add(next(value for value in optional_roots if relative == value or value in relative.parents))
                continue
            try:
                resolved_parent = destination.parent.resolve(strict=True)
                resolved_parent.relative_to(root)
            except (FileNotFoundError, ValueError) as exc:
                raise VerificationError(f"installed path escapes or is missing: {relative}") from exc
            if member.isdir():
                if not destination.is_dir():
                    raise VerificationError(f"installed directory is missing: {relative}")
                continue
            if member.issym():
                if not destination.is_symlink() or destination.readlink().as_posix() != member.linkname:
                    raise VerificationError(f"installed symlink differs: {relative}")
                continue
            if not member.isfile():
                raise VerificationError(f"unsupported archive member type: {relative}")
            if member.size > MAX_FILE_BYTES:
                raise VerificationError(f"archive member is too large: {relative}")
            total_bytes += member.size
            if total_bytes > MAX_TOTAL_BYTES:
                raise VerificationError("archive payload is too large")
            extracted = bundle.extractfile(member)
            if extracted is None:
                raise VerificationError(f"archive member cannot be read: {relative}")
            expected_digest = digest_stream(extracted, member.size)
            if destination.is_symlink() or not destination.is_file():
                raise VerificationError(f"installed file is missing or linked: {relative}")
            if relative == Path("npm-shrinkwrap.json") and archived_shrinkwrap is not None:
                omitted_peer_metadata = verify_installed_shrinkwrap(
                    bundle, members, archived_shrinkwrap, destination, root,
                )
                files += 1
                saw_package_json = saw_package_json or relative.as_posix() == "package.json"
                continue
            if destination.stat().st_size != member.size:
                raise VerificationError(f"installed file size differs: {relative}")
            actual_digest = hashlib.sha256(destination.read_bytes()).hexdigest()
            if actual_digest != expected_digest:
                raise VerificationError(f"installed file digest differs: {relative}")
            files += 1
            saw_package_json = saw_package_json or relative.as_posix() == "package.json"
        linked_peers, lock_packages = verify_installed_inventory(
            root, members, package_manifest, allowed_peers or {}, archived_shrinkwrap,
        )
    if not saw_package_json or files == 0:
        raise VerificationError("archive does not contain a package.json payload")
    receipt_bound = False
    if lock_packages > 0 or receipt is not None or write_receipt is not None:
        receipt_value = expected_receipt(archive, root, package_manifest, allowed_peers or {}, lock_packages)
        receipt_bound = bind_receipt(receipt, write_receipt, lock_packages > 0, receipt_value)
    return {
        "verifiedFiles": files,
        "verifiedBytes": total_bytes,
        "omittedOptionalPackages": len(omitted_optional),
        "omittedPeerMetadata": omitted_peer_metadata,
        "linkedPeers": linked_peers,
        "verifiedLockPackages": lock_packages,
        "receiptBound": receipt_bound,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowed-peer", action="append", default=[], metavar="NAME@VERSION=ABSOLUTE_PATH")
    receipts = parser.add_mutually_exclusive_group()
    receipts.add_argument("--receipt", type=Path)
    receipts.add_argument("--write-receipt", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("installed_root", type=Path)
    args = parser.parse_args()
    try:
        result = verify(
            args.archive,
            args.installed_root,
            parse_allowed_peers(args.allowed_peer),
            receipt=args.receipt,
            write_receipt=args.write_receipt,
        )
    except (VerificationError, OSError, tarfile.TarError) as exc:
        print(f"plugin archive verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
