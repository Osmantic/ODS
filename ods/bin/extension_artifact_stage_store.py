"""Durably stage exact, plan-bound extension definition bytes.

This module consumes only in-memory output from ``extension_artifact_verifier``.
It never reopens the definition source and it grants no Compose, container,
service, network, cleanup, or lifecycle authority.  One ordered transaction
batch is serialized as one immutable bundle and published create-if-absent.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import struct
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from extension_artifact_verifier import (
    MAX_ARTIFACT_BYTES,
    VerifiedArtifactFile,
    VerifiedDefinitionArtifacts,
)
from extension_document_digest import (
    CanonicalDocumentError,
    canonical_document_sha256,
)
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
)
from extension_lifecycle_work import LifecycleWorkCommand


STAGE_SCHEMA = "ods.extension-artifact-stage.v1"
MAGIC = b"ODS-EXTENSION-ARTIFACT-STAGE-V1\n"
MAX_STAGE_DEFINITIONS = 64
MAX_STAGE_HEADER_BYTES = 256 * 1024
MAX_STAGE_BUNDLE_BYTES = (
    MAX_STAGE_DEFINITIONS * 2 * MAX_ARTIFACT_BYTES
    + MAX_STAGE_HEADER_BYTES
    + len(MAGIC)
    + 4
)

_ROOT_MODE = 0o700
_TEMP_MODE = 0o600
_PUBLISHED_MODE = 0o400
_NLINK_RETRIES = 4
_NLINK_RETRY_SECONDS = 0.005
_FILE_NAME_DOMAIN = b"ods-extension-artifact-stage-file-v1"
_TEMP_PREFIX = "tmp-"
_TEMP_SUFFIX = ".artifact-stage"
_FINAL_SUFFIX = ".artifact-stage"

_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_RELATIVE_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_SOURCES = frozenset({"builtin", "library", "user"})


class ArtifactStageError(RuntimeError):
    """A stable, value-free artifact staging failure."""

    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


@dataclass(frozen=True)
class StagedArtifactFile:
    relative_path: str
    content: bytes
    semantic_sha256: str
    raw_sha256: str
    size: int


@dataclass(frozen=True)
class StagedDefinitionArtifacts:
    service_id: str
    definition_source: str
    manifest: StagedArtifactFile
    compose: StagedArtifactFile | None


@dataclass(frozen=True)
class StagedArtifactBatch:
    transaction_id: str
    plan_hash: str
    service_ids: tuple[str, ...]
    definitions: tuple[StagedDefinitionArtifacts, ...]
    bundle_sha256: str
    duplicate: bool


def _fail(code: str, *, field: str | None = None) -> None:
    raise ArtifactStageError(code, field=field) from None


def _validate_platform() -> None:
    required = ("O_DIRECTORY", "O_NOFOLLOW")
    if (
        os.name != "posix"
        or any(not hasattr(os, name) for name in required)
        or os.open not in os.supports_dir_fd
        or os.link not in os.supports_dir_fd
        or os.link not in os.supports_follow_symlinks
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
        or os.unlink not in os.supports_dir_fd
    ):
        _fail("artifact-stage-platform-unsupported")


def _validate_transaction(value: Any) -> str:
    if not isinstance(value, str) or _TRANSACTION_RE.fullmatch(value) is None:
        _fail("artifact-stage-binding-invalid", field="transactionId")
    return value


def _validate_plan_hash(value: Any) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail("artifact-stage-binding-invalid", field="planHash")
    return value


def _validate_service_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, tuple) or not value or len(value) > MAX_STAGE_DEFINITIONS:
        _fail("artifact-stage-binding-invalid", field="serviceIds")
    if any(
        not isinstance(item, str) or _SERVICE_RE.fullmatch(item) is None
        for item in value
    ):
        _fail("artifact-stage-binding-invalid", field="serviceIds")
    if len(set(value)) != len(value):
        _fail("artifact-stage-binding-invalid", field="serviceIds")
    return value


def _validate_relative_path(value: Any, *, manifest: bool = False) -> str:
    if (
        not isinstance(value, str)
        or _RELATIVE_PATH_RE.fullmatch(value) is None
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or (manifest and value != "manifest.yaml")
    ):
        _fail("artifact-stage-integrity", field="relativePath")
    return value


def _validate_digest(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        _fail("artifact-stage-integrity", field=field)
    return value


def _validate_verified_file(
    value: Any,
    *,
    expected_path: str,
    expected_digest: str,
) -> VerifiedArtifactFile:
    if not isinstance(value, VerifiedArtifactFile):
        _fail("artifact-stage-binding-invalid", field="artifact")
    if (
        value.relative_path != expected_path
        or value.semantic_sha256 != expected_digest
        or not isinstance(value.content, bytes)
        or isinstance(value.size, bool)
        or not isinstance(value.size, int)
        or value.size != len(value.content)
        or value.size > MAX_ARTIFACT_BYTES
        or isinstance(value.device, bool)
        or not isinstance(value.device, int)
        or value.device < 0
        or isinstance(value.inode, bool)
        or not isinstance(value.inode, int)
        or value.inode < 0
    ):
        _fail("artifact-stage-binding-invalid", field="artifact")
    try:
        actual = canonical_document_sha256(value.content)
    except CanonicalDocumentError:
        _fail("artifact-stage-binding-invalid", field="artifact")
    if actual != expected_digest:
        _fail("artifact-stage-binding-invalid", field="artifact")
    return value


def _planned_by_service(
    material: LifecyclePlanMaterial,
) -> dict[str, PlannedDefinition]:
    if not isinstance(material.definitions, tuple) or not isinstance(
        material.operations, tuple
    ):
        _fail("artifact-stage-binding-invalid", field="planMaterial")
    if not material.operations or len(material.operations) != len(material.definitions):
        _fail("artifact-stage-binding-invalid", field="planMaterial")
    operation_ids: list[str] = []
    planned: dict[str, PlannedDefinition] = {}
    for operation, definition in zip(
        material.operations, material.definitions, strict=True
    ):
        service_id = getattr(operation, "service_id", None)
        action = getattr(operation, "action", None)
        if (
            not isinstance(service_id, str)
            or _SERVICE_RE.fullmatch(service_id) is None
            or not isinstance(action, str)
            or action not in {"install", "enable", "repair", "update", "noop"}
            or not isinstance(definition, PlannedDefinition)
            or definition.service_id != service_id
            or service_id in planned
        ):
            _fail("artifact-stage-binding-invalid", field="planMaterial")
        operation_ids.append(service_id)
        planned[service_id] = definition
    if tuple(operation_ids) != tuple(item.service_id for item in material.definitions):
        _fail("artifact-stage-binding-invalid", field="planMaterial")
    return planned


def _validate_stage_command(
    command: Any,
) -> tuple[
    str,
    str,
    tuple[str, ...],
    tuple[PlannedDefinition, ...],
]:
    if not isinstance(command, LifecycleWorkCommand):
        _fail("artifact-stage-binding-invalid", field="command")
    transaction_id = _validate_transaction(command.transaction_id)
    plan_hash = _validate_plan_hash(command.plan_hash)
    service_ids = _validate_service_ids(command.service_ids)
    if command.operation_key != "stage":
        _fail("artifact-stage-binding-invalid", field="operationKey")
    material = command.plan_material
    if (
        not isinstance(material, LifecyclePlanMaterial)
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != transaction_id
        or material.plan_hash != plan_hash
        or material.state != "staged"
    ):
        _fail("artifact-stage-binding-invalid", field="planMaterial")
    planned = _planned_by_service(material)
    mutable_ids = tuple(
        operation.service_id
        for operation in material.operations
        if operation.action != "noop"
    )
    if mutable_ids != service_ids:
        _fail("artifact-stage-binding-invalid", field="serviceIds")
    return (
        transaction_id,
        plan_hash,
        service_ids,
        tuple(planned[service_id] for service_id in service_ids),
    )


def select_stage_definitions(command: Any) -> tuple[PlannedDefinition, ...]:
    """Purely select the exact mutable plan definitions for staging."""

    return _validate_stage_command(command)[3]


def _validate_stage_input(
    command: Any,
    artifacts: Any,
) -> tuple[
    str,
    str,
    tuple[str, ...],
    tuple[VerifiedDefinitionArtifacts, ...],
    tuple[PlannedDefinition, ...],
]:
    transaction_id, plan_hash, service_ids, selected = _validate_stage_command(
        command
    )
    if not isinstance(artifacts, tuple) or len(artifacts) != len(service_ids):
        _fail("artifact-stage-binding-invalid", field="artifacts")

    for service_id, definition, verified in zip(
        service_ids, selected, artifacts, strict=True
    ):
        if (
            not isinstance(verified, VerifiedDefinitionArtifacts)
            or verified.service_id != service_id
            or not isinstance(definition.definition_source, str)
            or definition.definition_source not in _SOURCES
            or verified.definition_source != definition.definition_source
        ):
            _fail("artifact-stage-binding-invalid", field="artifacts")
        _validate_verified_file(
            verified.manifest,
            expected_path="manifest.yaml",
            expected_digest=definition.definition_sha256,
        )
        if (definition.compose_file is None) != (definition.compose_sha256 is None):
            _fail("artifact-stage-binding-invalid", field="compose")
        if definition.compose_file is None:
            if verified.compose is not None:
                _fail("artifact-stage-binding-invalid", field="compose")
        else:
            if verified.compose is None:
                _fail("artifact-stage-binding-invalid", field="compose")
            _validate_relative_path(definition.compose_file)
            _validate_verified_file(
                verified.compose,
                expected_path=definition.compose_file,
                expected_digest=definition.compose_sha256,
            )
    return transaction_id, plan_hash, service_ids, artifacts, selected


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("artifact-stage-integrity", field="header")


def _file_header(
    value: VerifiedArtifactFile,
    *,
    offset: int,
) -> dict[str, Any]:
    return {
        "offset": offset,
        "rawSha256": "sha256:" + hashlib.sha256(value.content).hexdigest(),
        "relativePath": value.relative_path,
        "semanticSha256": value.semantic_sha256,
        "size": len(value.content),
    }


def _build_bundle(
    transaction_id: str,
    plan_hash: str,
    service_ids: tuple[str, ...],
    artifacts: tuple[VerifiedDefinitionArtifacts, ...],
) -> bytes:
    offset = 0
    payloads: list[bytes] = []
    definitions: list[dict[str, Any]] = []
    for verified in artifacts:
        manifest = _file_header(verified.manifest, offset=offset)
        payloads.append(verified.manifest.content)
        offset += len(verified.manifest.content)
        compose = None
        if verified.compose is not None:
            compose = _file_header(verified.compose, offset=offset)
            payloads.append(verified.compose.content)
            offset += len(verified.compose.content)
        definitions.append(
            {
                "compose": compose,
                "definitionSource": verified.definition_source,
                "manifest": manifest,
                "serviceId": verified.service_id,
            }
        )
    header = _canonical_json(
        {
            "definitions": definitions,
            "planHash": plan_hash,
            "schema": STAGE_SCHEMA,
            "serviceIds": list(service_ids),
            "transactionId": transaction_id,
        }
    )
    if not header or len(header) > MAX_STAGE_HEADER_BYTES:
        _fail("artifact-stage-size-exceeded", field="header")
    bundle = MAGIC + struct.pack(">I", len(header)) + header + b"".join(payloads)
    if len(bundle) > MAX_STAGE_BUNDLE_BYTES:
        _fail("artifact-stage-size-exceeded", field="bundle")
    return bundle


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_number(_value: str) -> Any:
    raise ValueError("unsupported number")


def _parse_header(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("artifact-stage-integrity", field="header")
    if not isinstance(value, dict) or _canonical_json(value) != raw:
        _fail("artifact-stage-integrity", field="header")
    return value


def _parse_file(
    value: Any,
    payload: bytes,
    *,
    expected_offset: int,
    manifest: bool,
) -> tuple[StagedArtifactFile, int]:
    keys = {"offset", "rawSha256", "relativePath", "semanticSha256", "size"}
    if not isinstance(value, dict) or set(value) != keys:
        _fail("artifact-stage-integrity", field="file")
    if type(value.get("offset")) is not int or value["offset"] != expected_offset:
        _fail("artifact-stage-integrity", field="offset")
    size = value.get("size")
    if type(size) is not int or size < 0 or size > MAX_ARTIFACT_BYTES:
        _fail("artifact-stage-integrity", field="size")
    end = expected_offset + size
    if end > len(payload):
        _fail("artifact-stage-integrity", field="size")
    relative_path = _validate_relative_path(
        value.get("relativePath"), manifest=manifest
    )
    semantic = _validate_digest(value.get("semanticSha256"), field="semanticSha256")
    raw_digest = _validate_digest(value.get("rawSha256"), field="rawSha256")
    content = payload[expected_offset:end]
    if "sha256:" + hashlib.sha256(content).hexdigest() != raw_digest:
        _fail("artifact-stage-integrity", field="rawSha256")
    try:
        actual_semantic = canonical_document_sha256(content)
    except CanonicalDocumentError:
        _fail("artifact-stage-integrity", field="semanticSha256")
    if actual_semantic != semantic:
        _fail("artifact-stage-integrity", field="semanticSha256")
    return (
        StagedArtifactFile(
            relative_path=relative_path,
            content=content,
            semantic_sha256=semantic,
            raw_sha256=raw_digest,
            size=size,
        ),
        end,
    )


def _parse_bundle(
    bundle: bytes,
    *,
    transaction_id: str,
    plan_hash: str,
    service_ids: tuple[str, ...],
    duplicate: bool,
) -> StagedArtifactBatch:
    if len(bundle) > MAX_STAGE_BUNDLE_BYTES or not bundle.startswith(MAGIC):
        _fail("artifact-stage-integrity", field="bundle")
    prefix = len(MAGIC)
    if len(bundle) < prefix + 4:
        _fail("artifact-stage-integrity", field="bundle")
    header_size = struct.unpack(">I", bundle[prefix : prefix + 4])[0]
    if header_size == 0 or header_size > MAX_STAGE_HEADER_BYTES:
        _fail("artifact-stage-integrity", field="header")
    header_start = prefix + 4
    header_end = header_start + header_size
    if header_end > len(bundle):
        _fail("artifact-stage-integrity", field="header")
    header = _parse_header(bundle[header_start:header_end])
    if set(header) != {
        "definitions",
        "planHash",
        "schema",
        "serviceIds",
        "transactionId",
    }:
        _fail("artifact-stage-integrity", field="header")
    if (
        header.get("schema") != STAGE_SCHEMA
        or header.get("transactionId") != transaction_id
        or header.get("planHash") != plan_hash
        or header.get("serviceIds") != list(service_ids)
    ):
        _fail("artifact-stage-integrity", field="binding")
    raw_definitions = header.get("definitions")
    if not isinstance(raw_definitions, list) or len(raw_definitions) != len(
        service_ids
    ):
        _fail("artifact-stage-integrity", field="definitions")

    payload = bundle[header_end:]
    offset = 0
    parsed: list[StagedDefinitionArtifacts] = []
    for service_id, value in zip(service_ids, raw_definitions, strict=True):
        if not isinstance(value, dict) or set(value) != {
            "compose",
            "definitionSource",
            "manifest",
            "serviceId",
        }:
            _fail("artifact-stage-integrity", field="definition")
        source = value.get("definitionSource")
        if (
            value.get("serviceId") != service_id
            or not isinstance(source, str)
            or source not in _SOURCES
        ):
            _fail("artifact-stage-integrity", field="definition")
        manifest_file, offset = _parse_file(
            value.get("manifest"), payload, expected_offset=offset, manifest=True
        )
        compose_file = None
        if value.get("compose") is not None:
            compose_file, offset = _parse_file(
                value["compose"], payload, expected_offset=offset, manifest=False
            )
        parsed.append(
            StagedDefinitionArtifacts(
                service_id=service_id,
                definition_source=source,
                manifest=manifest_file,
                compose=compose_file,
            )
        )
    if offset != len(payload):
        _fail("artifact-stage-integrity", field="payload")
    return StagedArtifactBatch(
        transaction_id=transaction_id,
        plan_hash=plan_hash,
        service_ids=service_ids,
        definitions=tuple(parsed),
        bundle_sha256=hashlib.sha256(bundle).hexdigest(),
        duplicate=duplicate,
    )


def _binding_name(
    transaction_id: str, plan_hash: str, service_ids: tuple[str, ...]
) -> str:
    binding = _canonical_json(
        {
            "planHash": plan_hash,
            "serviceIds": list(service_ids),
            "transactionId": transaction_id,
        }
    )
    return hashlib.sha256(_FILE_NAME_DOMAIN + b"\0" + binding).hexdigest() + _FINAL_SUFFIX


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _file_read_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _validate_root_value(value: Any) -> Path:
    try:
        raw = os.fspath(value)
        root = Path(raw)
    except (TypeError, ValueError):
        _fail("artifact-stage-root-invalid")
    if (
        not isinstance(raw, str)
        or "\x00" in raw
        or not root.is_absolute()
        or root == Path(root.anchor)
        or ".." in root.parts
    ):
        _fail("artifact-stage-root-invalid")
    return root


def _check_directory(
    descriptor: int, *, final: bool
) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail("artifact-stage-io-error")
    if not stat.S_ISDIR(info.st_mode):
        _fail("artifact-stage-root-invalid")
    if final and (
        info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != _ROOT_MODE
    ):
        _fail("artifact-stage-custody-violation")
    return info


def _open_root(root: Path) -> int:
    try:
        descriptor = os.open(root.anchor, _directory_flags())
    except OSError:
        _fail("artifact-stage-root-missing")
    try:
        _check_directory(descriptor, final=False)
        for component in root.parts[1:]:
            parent = descriptor
            try:
                descriptor = os.open(component, _directory_flags(), dir_fd=parent)
            except FileNotFoundError:
                _fail("artifact-stage-root-missing")
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    _fail("artifact-stage-root-invalid")
                _fail("artifact-stage-io-error")
            os.close(parent)
            _check_directory(descriptor, final=False)
        _check_directory(descriptor, final=True)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


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


def _check_published_file(descriptor: int) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail("artifact-stage-io-error")
    if not stat.S_ISREG(info.st_mode):
        _fail("artifact-stage-integrity")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != _PUBLISHED_MODE:
        _fail("artifact-stage-custody-violation")
    if info.st_size > MAX_STAGE_BUNDLE_BYTES:
        _fail("artifact-stage-size-exceeded")
    for _attempt in range(_NLINK_RETRIES):
        if info.st_nlink == 1:
            return info
        time.sleep(_NLINK_RETRY_SECONDS)
        try:
            info = os.fstat(descriptor)
        except OSError:
            _fail("artifact-stage-io-error")
    _fail("artifact-stage-integrity")


def _read_all(descriptor: int, size: int) -> bytes:
    content = bytearray()
    try:
        while len(content) <= MAX_STAGE_BUNDLE_BYTES:
            remaining = MAX_STAGE_BUNDLE_BYTES + 1 - len(content)
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            content.extend(chunk)
    except OSError:
        _fail("artifact-stage-io-error")
    if len(content) > MAX_STAGE_BUNDLE_BYTES:
        _fail("artifact-stage-size-exceeded")
    if len(content) != size:
        _fail("artifact-stage-integrity")
    return bytes(content)


def _read_bundle_at(root_descriptor: int, name: str) -> bytes:
    try:
        descriptor = os.open(name, _file_read_flags(), dir_fd=root_descriptor)
    except FileNotFoundError:
        _fail("artifact-stage-missing")
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            _fail("artifact-stage-integrity")
        _fail("artifact-stage-io-error")
    try:
        before = _check_published_file(descriptor)
        content = _read_all(descriptor, before.st_size)
        after = _check_published_file(descriptor)
        if _identity(before) != _identity(after):
            _fail("artifact-stage-integrity")
        try:
            path_info = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        except OSError:
            _fail("artifact-stage-integrity")
        if (path_info.st_dev, path_info.st_ino) != (after.st_dev, after.st_ino):
            _fail("artifact-stage-integrity")
        return content
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    try:
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                _fail("artifact-stage-io-error")
            offset += written
    except OSError:
        _fail("artifact-stage-io-error")


def _path_matches(
    root_descriptor: int, name: str, expected: os.stat_result
) -> bool:
    try:
        current = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
    except OSError:
        return False
    return (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)


def _unlink_authentic_temp(
    root_descriptor: int, name: str, expected: os.stat_result
) -> None:
    if not _path_matches(root_descriptor, name, expected):
        _fail("artifact-stage-temp-integrity")
    try:
        os.unlink(name, dir_fd=root_descriptor)
    except OSError:
        _fail("artifact-stage-io-error")


def _publish_bundle(root_descriptor: int, final_name: str, bundle: bytes) -> bool:
    temp_name = _TEMP_PREFIX + uuid.uuid4().hex + _TEMP_SUFFIX
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(temp_name, flags, _TEMP_MODE, dir_fd=root_descriptor)
    except OSError:
        _fail("artifact-stage-io-error")
    temp_info: os.stat_result | None = None
    published = False
    try:
        try:
            temp_info = os.fstat(descriptor)
        except OSError:
            _fail("artifact-stage-io-error")
        if (
            not stat.S_ISREG(temp_info.st_mode)
            or temp_info.st_uid != os.geteuid()
            or stat.S_IMODE(temp_info.st_mode) != _TEMP_MODE
            or temp_info.st_nlink != 1
        ):
            _fail("artifact-stage-temp-integrity")
        _write_all(descriptor, bundle)
        try:
            os.fsync(descriptor)
            os.fchmod(descriptor, _PUBLISHED_MODE)
            os.fsync(descriptor)
            sealed = os.fstat(descriptor)
        except OSError:
            _fail("artifact-stage-io-error")
        if (
            (sealed.st_dev, sealed.st_ino) != (temp_info.st_dev, temp_info.st_ino)
            or sealed.st_size != len(bundle)
            or stat.S_IMODE(sealed.st_mode) != _PUBLISHED_MODE
            or sealed.st_nlink != 1
        ):
            _fail("artifact-stage-temp-integrity")
        temp_info = sealed
        if not _path_matches(root_descriptor, temp_name, sealed):
            _fail("artifact-stage-temp-integrity")
        try:
            os.link(
                temp_name,
                final_name,
                src_dir_fd=root_descriptor,
                dst_dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            published = True
        except FileExistsError:
            published = False
        except OSError as exc:
            if exc.errno in {
                errno.EPERM,
                errno.EOPNOTSUPP,
                getattr(errno, "ENOTSUP", errno.EOPNOTSUPP),
            }:
                _fail("artifact-stage-link-unsupported")
            _fail("artifact-stage-io-error")
        if published:
            try:
                final_info = os.stat(
                    final_name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                _fail("artifact-stage-integrity")
            if (
                (final_info.st_dev, final_info.st_ino)
                != (sealed.st_dev, sealed.st_ino)
                or final_info.st_nlink != 2
            ):
                _fail("artifact-stage-integrity")
        _unlink_authentic_temp(root_descriptor, temp_name, sealed)
        temp_info = None
        try:
            if published:
                final_info = os.stat(
                    final_name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            os.fsync(root_descriptor)
        except OSError:
            _fail("artifact-stage-io-error")
        if published:
            if (
                (final_info.st_dev, final_info.st_ino)
                != (sealed.st_dev, sealed.st_ino)
                or final_info.st_nlink != 1
                or stat.S_IMODE(final_info.st_mode) != _PUBLISHED_MODE
            ):
                _fail("artifact-stage-integrity")
        return published
    finally:
        try:
            os.close(descriptor)
        except OSError:
            if published:
                _fail("artifact-stage-io-error")
        if temp_info is not None and _path_matches(
            root_descriptor, temp_name, temp_info
        ):
            try:
                os.unlink(temp_name, dir_fd=root_descriptor)
            except OSError:
                pass


class ArtifactStageStore:
    """Explicit owner-private root for immutable staged artifact batches."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root_value = root

    def stage(
        self,
        command: LifecycleWorkCommand,
        artifacts: tuple[VerifiedDefinitionArtifacts, ...],
    ) -> StagedArtifactBatch:
        (
            transaction_id,
            plan_hash,
            service_ids,
            verified,
            _planned,
        ) = _validate_stage_input(command, artifacts)
        bundle = _build_bundle(transaction_id, plan_hash, service_ids, verified)
        expected = _parse_bundle(
            bundle,
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            service_ids=service_ids,
            duplicate=False,
        )
        _validate_platform()
        root = _validate_root_value(self._root_value)
        root_descriptor = _open_root(root)
        try:
            name = _binding_name(transaction_id, plan_hash, service_ids)
            published = _publish_bundle(root_descriptor, name, bundle)
            if published:
                return expected
            winner = _read_bundle_at(root_descriptor, name)
            if winner != bundle:
                try:
                    _parse_bundle(
                        winner,
                        transaction_id=transaction_id,
                        plan_hash=plan_hash,
                        service_ids=service_ids,
                        duplicate=True,
                    )
                except ArtifactStageError:
                    _fail("artifact-stage-integrity")
                _fail("artifact-stage-conflict")
            return replace(expected, duplicate=True)
        finally:
            os.close(root_descriptor)

    def read(
        self,
        transaction_id: str,
        plan_hash: str,
        service_ids: tuple[str, ...],
    ) -> StagedArtifactBatch:
        transaction_id = _validate_transaction(transaction_id)
        plan_hash = _validate_plan_hash(plan_hash)
        service_ids = _validate_service_ids(service_ids)
        _validate_platform()
        root = _validate_root_value(self._root_value)
        root_descriptor = _open_root(root)
        try:
            name = _binding_name(transaction_id, plan_hash, service_ids)
            bundle = _read_bundle_at(root_descriptor, name)
        finally:
            os.close(root_descriptor)
        return _parse_bundle(
            bundle,
            transaction_id=transaction_id,
            plan_hash=plan_hash,
            service_ids=service_ids,
            duplicate=False,
        )


__all__ = [
    "ArtifactStageError",
    "ArtifactStageStore",
    "MAX_STAGE_BUNDLE_BYTES",
    "MAX_STAGE_DEFINITIONS",
    "MAX_STAGE_HEADER_BYTES",
    "STAGE_SCHEMA",
    "StagedArtifactBatch",
    "StagedArtifactFile",
    "StagedDefinitionArtifacts",
    "select_stage_definitions",
]
