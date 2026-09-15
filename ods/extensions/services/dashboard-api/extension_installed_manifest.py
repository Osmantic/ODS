"""Bounded, semantic observation of an actually installed extension manifest.

The dashboard container cannot import ods/bin, so this loader is kept in parity
with extension_document_digest and is cross-checked against it in source tests.
It deliberately returns no configuration values beyond the planner projection.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from typing import Any

import yaml

from assistant_first_planner import PlanningError, adapt_manifest


_MAX_MANIFEST_BYTES = 1024 * 1024


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(None, node.start_mark, "unhashable key", key_node.start_mark) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(None, node.start_mark, "duplicate key", key_node.start_mark)
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def _validate_json_document(value: Any, active: set[int] | None = None) -> None:
    """Match bin canonical digest's rejection of YAML-only identities."""
    if active is None:
        active = set()
    if value is None or type(value) in {str, bool, int}:
        if isinstance(value, str) and any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ValueError("invalid Unicode")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active:
            raise ValueError("cyclic YAML alias")
        active.add(identity)
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("non-string key")
            for key in value:
                _validate_json_document(key, active)
            children = value.values()
        else:
            children = value
        for child in children:
            _validate_json_document(child, active)
        active.remove(identity)
        return
    raise ValueError("unsupported YAML value")


def _canonical_manifest(raw: bytes) -> tuple[dict[str, Any], str]:
    try:
        value = yaml.load(raw.decode("utf-8", errors="strict"), Loader=_UniqueKeyLoader)
        _validate_json_document(value)
        canonical = (json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8", errors="strict")
    except (UnicodeError, yaml.YAMLError, TypeError, ValueError, RecursionError) as exc:
        raise PlanningError("installed-manifest-invalid") from exc
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise PlanningError("installed-manifest-invalid")
    return value, f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _safe_root(user_root: Path, builtin_root: Path, service_id: str) -> tuple[Path, str]:
    for base, origin in ((user_root, "user"), (builtin_root, "builtin")):
        root = base / service_id
        try:
            metadata = root.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise PlanningError("installed-manifest-unavailable", serviceId=service_id) from exc
        if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink() or base.is_symlink():
            raise PlanningError("installed-manifest-unsafe", serviceId=service_id)
        return root, origin
    raise PlanningError("installed-manifest-unavailable", serviceId=service_id)


def observe_installed_manifest(
    service_id: str, user_root: Path, builtin_root: Path
) -> dict[str, Any]:
    """Return only validated prior identity/data, never a catalog approximation."""
    root, origin = _safe_root(user_root, builtin_root, service_id)
    manifest_path = root / "manifest.yaml"
    try:
        metadata = manifest_path.lstat()
    except FileNotFoundError as exc:
        raise PlanningError("installed-manifest-unavailable", serviceId=service_id) from exc
    except OSError as exc:
        raise PlanningError("installed-manifest-unavailable", serviceId=service_id) from exc
    if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= _MAX_MANIFEST_BYTES:
        raise PlanningError("installed-manifest-unsafe", serviceId=service_id)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(manifest_path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_size != metadata.st_size
                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            ):
                raise PlanningError("installed-manifest-unsafe", serviceId=service_id)
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_MANIFEST_BYTES + 1)
            if len(raw) != opened.st_size:
                raise PlanningError("installed-manifest-unsafe", serviceId=service_id)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise PlanningError("installed-manifest-unavailable", serviceId=service_id) from exc
    manifest, digest = _canonical_manifest(raw)
    service = manifest.get("service")
    if not isinstance(service, dict) or service.get("id") != service_id:
        raise PlanningError("installed-manifest-id-mismatch", serviceId=service_id)
    record = adapt_manifest({
        **manifest,
        "_catalog": {
            "definition_sha256": digest,
            "definition_source": origin,
            "compose_file": None,
        },
    })
    return {
        "id": service_id,
        "manifestSchemaVersion": record["schemaVersion"],
        "version": record["version"],
        "dataSchemaVersion": record["dataSchemaVersion"],
        "definitionSha256": digest,
        "data": list(record["data"]),
        "resources": {
            "hostPorts": list(record["resources"]["hostPorts"]),
            "exclusive": list(record["resources"]["exclusive"]),
        },
    }
