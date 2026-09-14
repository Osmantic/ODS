"""Read-only filesystem adapter for extension-aware ODS core updates.

The compatibility engine is intentionally pure.  This adapter gives source
update callers one bounded way to load the installed desired-state lockfile and
an exact candidate source tree without trusting shell-parsed JSON or a mutable
working-tree version string.  It never creates directories, lock files,
receipts, snapshots, or other state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from assistant_first_planner import (
    PlanningError,
    canonical_json_bytes,
    semver_precedence,
)
from extension_lockfile import (
    ExtensionLockfileError,
    canonical_lockfile_bytes,
    validate_lockfile_envelope,
)
from extension_update_compatibility import (
    ExtensionUpdateCompatibilityError,
    assess_update_compatibility,
)


PREFLIGHT_SCHEMA = "ods.extensions.update-preflight.v1"
PREFLIGHT_ERROR_SCHEMA = "ods.extensions.update-preflight-error.v1"

EXIT_READY = 0
EXIT_EXTENSION_PLAN_REQUIRED = 10
EXIT_BLOCKED = 11
EXIT_INVALID_INPUT = 12

_MAX_ENV_BYTES = 512 * 1024
_MAX_JSON_BYTES = 4 * 1024 * 1024
_REVISION_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


class ExtensionUpdatePreflightError(RuntimeError):
    """A bounded adapter failure that never includes file content or paths."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = dict(details)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "details": dict(self.details)}


def _fail(code: str, **details: Any) -> None:
    raise ExtensionUpdatePreflightError(code, **details)


def _validate_root(root: Path, field: str) -> Path:
    root = Path(root)
    if not root.is_absolute() or root == Path(root.anchor):
        _fail("invalid-root", field=field)
    root_stat: os.stat_result | None = None
    for candidate in reversed([root, *root.parents]):
        if candidate == Path(candidate.anchor):
            continue
        try:
            candidate_stat = os.lstat(candidate)
        except OSError as exc:
            raise ExtensionUpdatePreflightError(
                "root-unavailable", field=field
            ) from exc
        if stat.S_ISLNK(candidate_stat.st_mode):
            _fail("root-component-symlink", field=field)
        if candidate == root:
            root_stat = candidate_stat
    assert root_stat is not None
    if not stat.S_ISDIR(root_stat.st_mode):
        _fail("root-not-directory", field=field)
    return root


def _read_file(
    root: Path,
    relative: str,
    *,
    field: str,
    maximum: int,
    required: bool = True,
    owner_only: bool = False,
) -> bytes | None:
    root = _validate_root(root, field.split(".", 1)[0])
    path = root / relative
    if path.parent != root and root not in path.parents:
        _fail("invalid-relative-path", field=field)

    current = root
    for component in Path(relative).parts:
        current = current / component
        try:
            component_stat = os.lstat(current)
        except FileNotFoundError:
            if not required:
                return None
            raise ExtensionUpdatePreflightError("file-missing", field=field)
        except OSError as exc:
            raise ExtensionUpdatePreflightError(
                "file-unavailable", field=field
            ) from exc
        if stat.S_ISLNK(component_stat.st_mode):
            _fail("file-symlink", field=field)

    path_stat = component_stat
    if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
        _fail("unsafe-file", field=field)
    if path_stat.st_size > maximum:
        _fail("file-too-large", field=field)
    if owner_only and os.name == "posix":
        parent_stat = os.lstat(path.parent)
        if parent_stat.st_uid != os.getuid() or parent_stat.st_mode & 0o777 != 0o700:
            _fail("file-parent-custody", field=field)
        if path_stat.st_uid != os.getuid() or path_stat.st_mode & 0o777 != 0o600:
            _fail("file-custody", field=field)

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ExtensionUpdatePreflightError("file-open-failed", field=field) from exc
    try:
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISREG(descriptor_stat.st_mode) or descriptor_stat.st_nlink != 1:
            _fail("unsafe-file", field=field)
        if os.name == "posix" and (
            descriptor_stat.st_dev,
            descriptor_stat.st_ino,
        ) != (path_stat.st_dev, path_stat.st_ino):
            _fail("file-replaced", field=field)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                _fail("file-too-large", field=field)
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("json-duplicate-key")
        result[key] = value
    return result


def _decode_json(raw: bytes, field: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ExtensionUpdatePreflightError as exc:
        if exc.code == "json-duplicate-key":
            raise ExtensionUpdatePreflightError(
                "json-duplicate-key", field=field
            ) from exc
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ExtensionUpdatePreflightError("json-invalid", field=field) from exc


def _normalize_version(value: Any, field: str) -> str:
    if not isinstance(value, str):
        _fail("invalid-version", field=field)
    normalized = value.strip()
    if normalized.startswith("v"):
        normalized = normalized[1:]
    try:
        semver_precedence(normalized, "invalid-version")
    except PlanningError as exc:
        raise ExtensionUpdatePreflightError("invalid-version", field=field) from exc
    return normalized


def _manifest_version(raw: bytes, field: str) -> str:
    document = _decode_json(raw, field)
    if not isinstance(document, dict):
        _fail("invalid-manifest", field=field)
    ods_version = _normalize_version(
        document.get("ods_version"), f"{field}.ods_version"
    )
    release = document.get("release")
    if isinstance(release, dict) and release.get("version") is not None:
        release_version = _normalize_version(
            release.get("version"), f"{field}.release.version"
        )
        if release_version != ods_version:
            _fail("manifest-version-mismatch", field=field)
    return ods_version


def _installed_version(install_dir: Path) -> str:
    env_raw = _read_file(
        install_dir,
        ".env",
        field="installed.env",
        maximum=_MAX_ENV_BYTES,
        required=False,
    )
    if env_raw is not None:
        try:
            lines = env_raw.decode("utf-8", errors="strict").splitlines()
        except UnicodeDecodeError as exc:
            raise ExtensionUpdatePreflightError(
                "text-invalid", field="installed.env"
            ) from exc
        values = [
            line.split("=", 1)[1] for line in lines if line.startswith("ODS_VERSION=")
        ]
        if len(values) > 1:
            _fail("duplicate-installed-version")
        if values:
            return _normalize_version(
                values[0].strip().strip("\"'"), "installed.env.ODS_VERSION"
            )

    version_raw = _read_file(
        install_dir,
        ".version",
        field="installed.version",
        maximum=_MAX_ENV_BYTES,
        required=False,
    )
    if version_raw is not None:
        try:
            text = version_raw.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise ExtensionUpdatePreflightError(
                "text-invalid", field="installed.version"
            ) from exc
        value: Any = text
        if text.startswith("{"):
            document = _decode_json(version_raw, "installed.version")
            if not isinstance(document, dict):
                _fail("invalid-version-file")
            value = document.get("version")
        return _normalize_version(value, "installed.version")

    manifest_raw = _read_file(
        install_dir,
        "manifest.json",
        field="installed.manifest",
        maximum=_MAX_JSON_BYTES,
        required=False,
    )
    if manifest_raw is None:
        _fail("installed-version-unavailable")
    return _manifest_version(manifest_raw, "installed.manifest")


def assess_candidate_tree(
    install_dir: Path,
    candidate_dir: Path,
    candidate_revision: str,
) -> dict[str, Any]:
    """Assess an exact candidate tree without writing to either input root."""

    install_dir = _validate_root(Path(install_dir), "install")
    candidate_dir = _validate_root(Path(candidate_dir), "candidate")
    if not isinstance(candidate_revision, str) or not _REVISION_RE.fullmatch(
        candidate_revision
    ):
        _fail("invalid-candidate-revision")

    lockfile_raw = _read_file(
        install_dir,
        "data/assistant-first/desired-state/extensions.lock.json",
        field="installed.lockfile",
        maximum=_MAX_JSON_BYTES,
        owner_only=True,
    )
    assert lockfile_raw is not None
    lockfile_value = _decode_json(lockfile_raw, "installed.lockfile")
    try:
        source_lockfile = validate_lockfile_envelope(lockfile_value)
    except ExtensionLockfileError as exc:
        raise ExtensionUpdatePreflightError(
            "invalid-source-lockfile", lockfileCode=exc.code
        ) from exc
    if lockfile_raw != canonical_lockfile_bytes(source_lockfile):
        _fail("source-lockfile-noncanonical")

    manifest_raw = _read_file(
        candidate_dir,
        "manifest.json",
        field="candidate.manifest",
        maximum=_MAX_JSON_BYTES,
    )
    catalog_raw = _read_file(
        candidate_dir,
        "config/extensions-catalog.json",
        field="candidate.catalog",
        maximum=_MAX_JSON_BYTES,
    )
    assert manifest_raw is not None and catalog_raw is not None
    candidate_version = _manifest_version(manifest_raw, "candidate.manifest")
    catalog_document = _decode_json(catalog_raw, "candidate.catalog")
    if not isinstance(catalog_document, dict):
        _fail("invalid-candidate-catalog")
    revision = catalog_document.get("catalog_revision")
    entries = catalog_document.get("extensions")

    assessment = assess_update_compatibility(
        source_lockfile,
        installed_ods_version=_installed_version(install_dir),
        candidate_ods_version=candidate_version,
        candidate_catalog=entries,
        candidate_catalog_revision=revision,
    )

    material = {
        "schema": PREFLIGHT_SCHEMA,
        "candidateSourceRevision": candidate_revision,
        "candidateManifestSha256": hashlib.sha256(manifest_raw).hexdigest(),
        "candidateCatalogFileSha256": hashlib.sha256(catalog_raw).hexdigest(),
        "assessmentEnvelope": assessment,
    }
    return {
        **material,
        "preflightHash": hashlib.sha256(canonical_json_bytes(material)).hexdigest(),
    }


def exit_code_for_preflight(preflight: dict[str, Any]) -> int:
    assessment = preflight["assessmentEnvelope"]["assessment"]
    if not assessment["canUpdate"]:
        return EXIT_BLOCKED
    if assessment["requiresExtensionPlan"]:
        return EXIT_EXTENSION_PLAN_REQUIRED
    return EXIT_READY


def _write_json(value: Any) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(value) + b"\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", required=True, type=Path)
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--candidate-revision", required=True)
    args = parser.parse_args(argv)
    try:
        preflight = assess_candidate_tree(
            args.install_dir, args.candidate_dir, args.candidate_revision
        )
    except ExtensionUpdatePreflightError as exc:
        _write_json({"schema": PREFLIGHT_ERROR_SCHEMA, "error": exc.as_dict()})
        return EXIT_INVALID_INPUT
    except ExtensionUpdateCompatibilityError as exc:
        _write_json({"schema": PREFLIGHT_ERROR_SCHEMA, "error": exc.as_dict()})
        return EXIT_INVALID_INPUT
    except Exception:
        _write_json(
            {
                "schema": PREFLIGHT_ERROR_SCHEMA,
                "error": {"code": "internal-error", "details": {}},
            }
        )
        return EXIT_INVALID_INPUT
    _write_json(preflight)
    return exit_code_for_preflight(preflight)


__all__ = [
    "EXIT_BLOCKED",
    "EXIT_EXTENSION_PLAN_REQUIRED",
    "EXIT_INVALID_INPUT",
    "EXIT_READY",
    "ExtensionUpdatePreflightError",
    "PREFLIGHT_ERROR_SCHEMA",
    "PREFLIGHT_SCHEMA",
    "assess_candidate_tree",
    "exit_code_for_preflight",
    "main",
]
