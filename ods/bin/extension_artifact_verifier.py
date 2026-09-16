"""Verify plan-bound extension definition artifacts without granting effects.

The lifecycle plan binder proves which immutable definition material an owner
approved.  This module reads only the exact source root named by that material,
returns the bytes it verified, and never discovers a replacement by precedence.
It has no caller in the production lifecycle until a later reviewed boundary.
"""

from __future__ import annotations

import errno
import os
import re
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from extension_document_digest import (
    CanonicalDocumentError,
    canonical_document_sha256,
)
from extension_library_tree_digest import (
    LibraryTreeDigestError,
    digest_extension_tree,
)
from extension_lifecycle_plan import PlannedDefinition


MAX_ARTIFACT_BYTES = 2 * 1024 * 1024

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_SOURCES = frozenset({"builtin", "library", "user"})


class HostArtifactError(ValueError):
    """A stable, value-free artifact verification failure."""

    def __init__(
        self,
        code: str,
        *,
        field: str | None = None,
        cause_code: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.cause_code = cause_code


@dataclass(frozen=True)
class HostArtifactRoots:
    """Explicit definition roots; only the plan-selected member is opened."""

    builtin: str | os.PathLike[str] | None = None
    library: str | os.PathLike[str] | None = None
    user: str | os.PathLike[str] | None = None


@dataclass(frozen=True)
class VerifiedArtifactFile:
    """Exact bytes and descriptor identity accepted by the verifier."""

    relative_path: str
    content: bytes
    semantic_sha256: str
    device: int
    inode: int
    size: int


@dataclass(frozen=True)
class VerifiedDefinitionArtifacts:
    """One definition's complete plan-bound file verification result."""

    service_id: str
    definition_source: str
    manifest: VerifiedArtifactFile
    compose: VerifiedArtifactFile | None


def _fail(
    code: str,
    *,
    field: str | None = None,
    cause_code: str | None = None,
) -> None:
    raise HostArtifactError(code, field=field, cause_code=cause_code) from None


def _validate_platform() -> None:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if (
        os.name != "posix"
        or any(not hasattr(os, name) for name in required)
        or os.open not in os.supports_dir_fd
    ):
        _fail("artifact-platform-unsupported")


def _validate_plan(definition: PlannedDefinition) -> tuple[str, tuple[str, ...]]:
    if not isinstance(definition, PlannedDefinition):
        raise TypeError("definition must be PlannedDefinition")
    source = definition.definition_source
    if source is None:
        _fail("artifact-legacy-definition", field="definitionSource")
    if not isinstance(source, str) or source not in _SOURCES:
        _fail("artifact-plan-invalid", field="definitionSource")
    if (
        not isinstance(definition.service_id, str)
        or _SERVICE_ID_RE.fullmatch(definition.service_id) is None
    ):
        _fail("artifact-plan-invalid", field="serviceId")
    if (
        not isinstance(definition.definition_sha256, str)
        or _DIGEST_RE.fullmatch(definition.definition_sha256) is None
    ):
        _fail("artifact-plan-invalid", field="definitionSha256")
    tree_digest = definition.source_tree_sha256
    if tree_digest is not None and (
        source != "library"
        or not isinstance(tree_digest, str)
        or _DIGEST_RE.fullmatch(tree_digest) is None
    ):
        _fail("artifact-plan-invalid", field="sourceTreeSha256")

    compose_file = definition.compose_file
    compose_digest = definition.compose_sha256
    if (compose_file is None) != (compose_digest is None):
        _fail("artifact-compose-inconsistent", field="compose")
    if compose_digest is not None and (
        not isinstance(compose_digest, str)
        or _DIGEST_RE.fullmatch(compose_digest) is None
    ):
        _fail("artifact-plan-invalid", field="composeSha256")
    if compose_file is None:
        return source, ()
    if (
        not isinstance(compose_file, str)
        or _RELATIVE_PATH_RE.fullmatch(compose_file) is None
        or compose_file.startswith("/")
        or "\\" in compose_file
    ):
        _fail("artifact-plan-invalid", field="composeFile")
    parts = tuple(compose_file.split("/"))
    if any(part in {"", ".", ".."} for part in parts):
        _fail("artifact-plan-invalid", field="composeFile")
    return source, parts


def _selected_root(roots: HostArtifactRoots, source: str) -> Path:
    if not isinstance(roots, HostArtifactRoots):
        raise TypeError("roots must be HostArtifactRoots")
    raw = getattr(roots, source)
    if raw is None:
        _fail("artifact-root-missing", field="root")
    try:
        value = os.fspath(raw)
        root = Path(value)
    except (TypeError, ValueError):
        _fail("artifact-root-invalid", field="root")
    if (
        not isinstance(value, str)
        or "\x00" in value
        or not root.is_absolute()
        or root == Path(root.anchor)
        or ".." in root.parts
    ):
        _fail("artifact-root-invalid", field="root")
    return root


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _file_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _map_open_error(exc: OSError, *, field: str, root: bool = False) -> None:
    if isinstance(exc, FileNotFoundError):
        _fail("artifact-root-missing" if root else "artifact-file-missing", field=field)
    if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
        _fail("artifact-path-rejected", field=field)
    _fail("artifact-read-failed", field=field)


def _check_directory(descriptor: int, *, field: str, custody: bool) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail("artifact-read-failed", field=field)
    if not stat.S_ISDIR(info.st_mode):
        _fail("artifact-path-rejected", field=field)
    if custody and (info.st_uid != os.geteuid() or info.st_mode & 0o022):
        _fail("artifact-custody-violation", field=field)
    return info


def _open_directory_at(
    parent: int,
    name: str,
    *,
    field: str,
    custody: bool,
    root: bool = False,
) -> int:
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
    except OSError as exc:
        _map_open_error(exc, field=field, root=root)
    try:
        _check_directory(descriptor, field=field, custody=custody)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_root(root: Path) -> int:
    try:
        descriptor = os.open(root.anchor, _directory_flags())
    except OSError as exc:
        _map_open_error(exc, field="root", root=True)
    try:
        _check_directory(descriptor, field="root", custody=False)
        for component in root.parts[1:]:
            child = _open_directory_at(
                descriptor,
                component,
                field="root",
                custody=False,
                root=True,
            )
            os.close(descriptor)
            descriptor = child
        _check_directory(descriptor, field="root", custody=True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _check_file(descriptor: int, *, field: str) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail("artifact-read-failed", field=field)
    if not stat.S_ISREG(info.st_mode):
        _fail("artifact-not-regular-file", field=field)
    if info.st_nlink != 1:
        _fail("artifact-hardlink-rejected", field=field)
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        _fail("artifact-custody-violation", field=field)
    if info.st_size > MAX_ARTIFACT_BYTES:
        _fail("artifact-size-exceeded", field=field)
    return info


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_verified_file(
    parent: int,
    name: str,
    *,
    relative_path: str,
    expected_digest: str,
    field: str,
) -> VerifiedArtifactFile:
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=parent)
    except OSError as exc:
        _map_open_error(exc, field=field)
    try:
        before = _check_file(descriptor, field=field)
        content = bytearray()
        try:
            while len(content) <= MAX_ARTIFACT_BYTES:
                remaining = MAX_ARTIFACT_BYTES + 1 - len(content)
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                content.extend(chunk)
        except OSError:
            _fail("artifact-read-failed", field=field)
        if len(content) > MAX_ARTIFACT_BYTES:
            _fail("artifact-size-exceeded", field=field)
        after = _check_file(descriptor, field=field)
        if _identity(before) != _identity(after) or len(content) != after.st_size:
            _fail("artifact-state-changed", field=field)
        payload = bytes(content)
        try:
            actual_digest = canonical_document_sha256(payload)
        except CanonicalDocumentError as exc:
            _fail(
                "artifact-canonicalization-failed",
                field=field,
                cause_code=exc.code,
            )
        if actual_digest != expected_digest:
            _fail("artifact-digest-mismatch", field=field)
        return VerifiedArtifactFile(
            relative_path=relative_path,
            content=payload,
            semantic_sha256=actual_digest,
            device=after.st_dev,
            inode=after.st_ino,
            size=after.st_size,
        )
    finally:
        os.close(descriptor)


def verify_planned_definition(
    definition: PlannedDefinition,
    roots: HostArtifactRoots,
) -> VerifiedDefinitionArtifacts:
    """Read and verify exactly one plan-bound definition source snapshot."""

    source, compose_parts = _validate_plan(definition)
    root = _selected_root(roots, source)
    _validate_platform()

    def verify_library_tree() -> None:
        expected = definition.source_tree_sha256
        if expected is None:
            return
        try:
            actual = digest_extension_tree(root / definition.service_id)
        except LibraryTreeDigestError:
            _fail("artifact-library-tree-invalid", field="sourceTreeSha256")
        if actual != expected:
            _fail("artifact-library-tree-mismatch", field="sourceTreeSha256")

    # The plan binds the entire library payload, not only manifest/Compose.
    # Effect adapters must re-prove or consume a staged copy before mutation.
    verify_library_tree()

    with ExitStack() as stack:
        root_descriptor = _open_root(root)
        stack.callback(os.close, root_descriptor)
        service_descriptor = _open_directory_at(
            root_descriptor,
            definition.service_id,
            field="service",
            custody=True,
        )
        stack.callback(os.close, service_descriptor)
        manifest = _read_verified_file(
            service_descriptor,
            "manifest.yaml",
            relative_path="manifest.yaml",
            expected_digest=definition.definition_sha256,
            field="manifest",
        )

        compose = None
        if compose_parts:
            compose_parent = service_descriptor
            for component in compose_parts[:-1]:
                compose_parent = _open_directory_at(
                    compose_parent,
                    component,
                    field="compose",
                    custody=True,
                )
                stack.callback(os.close, compose_parent)
            assert definition.compose_file is not None
            assert definition.compose_sha256 is not None
            compose = _read_verified_file(
                compose_parent,
                compose_parts[-1],
                relative_path=definition.compose_file,
                expected_digest=definition.compose_sha256,
                field="compose",
            )

        verify_library_tree()

    return VerifiedDefinitionArtifacts(
        service_id=definition.service_id,
        definition_source=source,
        manifest=manifest,
        compose=compose,
    )


__all__ = [
    "HostArtifactError",
    "HostArtifactRoots",
    "MAX_ARTIFACT_BYTES",
    "VerifiedArtifactFile",
    "VerifiedDefinitionArtifacts",
    "verify_planned_definition",
]
