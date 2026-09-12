#!/usr/bin/env python3
"""Generate a static extensions catalog JSON from extension manifest files.

Scans the product-owned library and first-party service manifests,
extracts catalog-relevant fields, and writes a sorted JSON catalog
to ods/config/extensions-catalog.json.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


SCHEMA_VERSIONS = {"ods.services.v1", "ods.services.v2"}
CATALOG_SCHEMA_VERSION = "1.0.0"
EXCLUDED_IDS = {"privacy-shield"}
SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RELATIVE_COMPOSE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")

PLANNER_PATH = (
    Path(__file__).resolve().parent.parent
    / "extensions/services/dashboard-api/assistant_first_planner.py"
)
PLANNER_SPEC = importlib.util.spec_from_file_location("assistant_first_planner", PLANNER_PATH)
if PLANNER_SPEC is None or PLANNER_SPEC.loader is None:
    raise RuntimeError("Assistant First planner module is unavailable")
PLANNER = importlib.util.module_from_spec(PLANNER_SPEC)
PLANNER_SPEC.loader.exec_module(PLANNER)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict:
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _load_yaml(text: str) -> object:
    return yaml.load(text, Loader=_UniqueKeyLoader)


def parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Generate extensions catalog from manifest files.",
    )
    parser.add_argument(
        "--library-dir",
        type=Path,
        default=script_dir / ".." / "extensions" / "library" / "services",
        help="Path to extensions/library/services directory",
    )
    parser.add_argument(
        "--services-dir",
        type=Path,
        help="Also include first-party service manifests from this directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=script_dir / ".." / "config" / "extensions-catalog.json",
        help="Output path for the catalog JSON",
    )
    return parser.parse_args()


def strip_secrets(env_vars: list[dict]) -> list[dict]:
    """Return env_vars list with the 'secret' field removed from each entry."""
    cleaned = []
    for var in env_vars:
        entry = {k: v for k, v in var.items() if k != "secret"}
        cleaned.append(entry)
    return cleaned


def _validate_json_document(value: object, active: set[int] | None = None) -> None:
    """Reject YAML-only values that cannot have one portable JSON identity."""

    if active is None:
        active = set()
    if value is None or type(value) in {str, bool, int}:
        if isinstance(value, str) and any(
            0xD800 <= ord(character) <= 0xDFFF for character in value
        ):
            raise ValueError("document contains invalid Unicode")
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("document contains a non-finite number")
        return
    if isinstance(value, (list, dict)):
        identity = id(value)
        if identity in active:
            raise ValueError("document contains a cyclic YAML alias")
        active.add(identity)
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise ValueError("document contains a non-string object key")
            children = value.values()
        else:
            children = value
        for child in children:
            _validate_json_document(child, active)
        active.remove(identity)
        return
    raise ValueError(f"document contains unsupported YAML value: {type(value).__name__}")


def canonical_document_sha256(path: Path) -> str:
    """Hash parsed YAML/JSON semantics so checkout newline policy cannot drift plans."""

    try:
        data = _load_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"cannot parse document: {path}") from exc
    _validate_json_document(data)
    serialized = json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256((serialized + "\n").encode("utf-8")).hexdigest()


def _compose_path(manifest_path: Path, compose_name: str) -> Path | None:
    """Resolve an ordinary file below the manifest directory without symlink traversal."""

    if (
        not RELATIVE_COMPOSE_RE.fullmatch(compose_name)
        or ".." in Path(compose_name).parts
        or Path(compose_name).is_absolute()
    ):
        raise ValueError(f"unsafe compose_file path: {compose_name}")
    candidate = manifest_path.parent / compose_name
    if not candidate.exists():
        return None
    root = manifest_path.parent.resolve(strict=True)
    current = candidate
    while current != manifest_path.parent:
        if current.is_symlink():
            raise ValueError(f"unsafe compose_file symlink: {compose_name}")
        current = current.parent
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"compose_file escapes service directory: {compose_name}") from exc
    if not resolved.is_file():
        raise ValueError(f"compose_file is not an ordinary file: {compose_name}")
    return resolved


def load_manifest(manifest_path: Path) -> dict | None:
    """Load and validate a single manifest file. Returns None on failure."""
    try:
        data = _load_yaml(manifest_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeError) as e:
        print(f"WARNING: Failed to read {manifest_path}: {e}", file=sys.stderr)
        return None

    if not isinstance(data, dict):
        print(f"WARNING: Skipping {manifest_path}: root is not a mapping", file=sys.stderr)
        return None

    if data.get("schema_version") not in SCHEMA_VERSIONS:
        print(
            f"WARNING: Skipping {manifest_path}: "
            f"unsupported schema_version '{data.get('schema_version')}'",
            file=sys.stderr,
        )
        return None

    return data


def extract_entry(manifest: dict, manifest_path: Path | None = None) -> dict | None:
    """Extract a catalog entry from a validated manifest dict."""
    service = manifest.get("service")
    if not isinstance(service, dict):
        return None

    service_id = service.get("id")
    if not service_id or not SERVICE_ID_RE.match(service_id):
        return None

    if service_id in EXCLUDED_IDS:
        return None

    env_vars = service.get("env_vars", [])
    if not isinstance(env_vars, list):
        env_vars = []

    definition_sha256 = ""
    compose_sha256 = ""
    if manifest_path is not None:
        definition_sha256 = canonical_document_sha256(manifest_path)
        compose_name = service.get("compose_file")
        if isinstance(compose_name, str) and compose_name:
            compose_path = _compose_path(manifest_path, compose_name)
            if compose_path is not None:
                compose_sha256 = canonical_document_sha256(compose_path)
    planning_record = PLANNER.adapt_manifest(
        {
            **manifest,
            "_catalog": {
                "definition_sha256": definition_sha256,
                "compose_sha256": compose_sha256,
            },
        }
    )
    planning = {
        "serviceType": planning_record["serviceType"],
        "version": planning_record["version"],
        "dataSchemaVersion": planning_record["dataSchemaVersion"],
        "odsCompatibility": planning_record["odsCompatibility"],
        "definitionSha256": planning_record["definitionSha256"],
        "composeSha256": planning_record["composeSha256"],
        "dependsOn": list(planning_record["dependsOn"]),
        "provides": list(planning_record["provides"]),
        "requires": list(planning_record["requires"]),
        "optional": list(planning_record["optional"]),
        "conflicts": list(planning_record["conflicts"]),
        "providerPriority": planning_record["providerPriority"],
        "requirements": PLANNER.public_json_value(planning_record["requirements"]),
        "estimates": PLANNER.public_json_value(planning_record["estimates"]),
        "resources": PLANNER.public_json_value(planning_record["resources"]),
        "configuration": PLANNER.public_json_value(planning_record["configuration"]),
        "artifacts": PLANNER.public_json_value(planning_record["artifacts"]),
        "lifecycle": PLANNER.public_json_value(planning_record["lifecycle"]),
        "data": PLANNER.public_json_value(planning_record["data"]),
        "trust": PLANNER.public_json_value(planning_record["trust"]),
        "support": PLANNER.public_json_value(planning_record["support"]),
        "legacy": planning_record["legacy"],
    }
    if planning_record["legacy"]:
        planning = {
            key: planning[key]
            for key in (
                "serviceType",
                "version",
                "dataSchemaVersion",
                "odsCompatibility",
                "definitionSha256",
                "composeSha256",
                "dependsOn",
                "legacy",
            )
        }

    entry = {
        "id": service_id,
        "manifest_schema_version": manifest["schema_version"],
        "planning": planning,
        "name": service.get("name", service_id),
        "description": service.get("description", ""),
        "category": service.get("category", ""),
        "gpu_backends": service.get("gpu_backends", []),
        "compose_file": service.get("compose_file", ""),
        "depends_on": service.get("depends_on", []),
        "port": service.get("port", 0),
        "external_port_default": service.get("external_port_default", 0),
        "health_endpoint": service.get("health", ""),
        "env_vars": strip_secrets(env_vars),
        "tags": manifest.get("tags") or service.get("tags", []),
        "features": manifest.get("features") or service.get("features", []),
    }

    if isinstance(service.get("llm"), dict):
        entry["llm"] = service["llm"]

    if "startup_check" in service:
        entry["startup_check"] = service.get("startup_check")
    if "startup_timeout" in service:
        entry["startup_timeout"] = service.get("startup_timeout")

    return entry


def catalog_revision(entries: list[dict]) -> str:
    """Hash only deterministic planning material, never display timestamps."""

    material = {
        "schema": "ods.extensions.planning-catalog.v1",
        "extensions": [
            {
                "id": entry["id"],
                "manifestSchemaVersion": entry["manifest_schema_version"],
                "planning": entry["planning"],
            }
            for entry in sorted(entries, key=lambda item: item["id"])
        ],
    }
    return hashlib.sha256(PLANNER.canonical_json_bytes(material)).hexdigest()


def generate_catalog(library_dir: Path, services_dir: Path | None = None) -> list[dict]:
    """Scan manifest files and return sorted catalog entries."""
    if not library_dir.is_dir():
        print(f"ERROR: Library directory not found: {library_dir}", file=sys.stderr)
        sys.exit(1)

    entries: dict[str, dict] = {}
    roots = [(library_dir, "library")]
    if services_dir is not None:
        if not services_dir.is_dir() or services_dir.is_symlink():
            raise ValueError("First-party service directory is unavailable")
        roots.append((services_dir, "builtin"))
    for root, source in roots:
        for service_dir in sorted(root.iterdir()):
            if not service_dir.is_dir() or service_dir.is_symlink():
                continue
            manifest_path = service_dir / "manifest.yaml"
            if not manifest_path.is_file() or manifest_path.is_symlink():
                continue
            manifest = load_manifest(manifest_path)
            if manifest is None:
                continue
            entry = extract_entry(manifest, manifest_path)
            if entry is None or entry["id"] != service_dir.name:
                continue
            if source == "builtin":
                # Native services can be disabled or managed outside Docker.
                # Their manifests remain discoverable; this grants no mutation.
                descriptions = [f.get("description") for f in entry["features"]
                                if isinstance(f, dict) and isinstance(f.get("description"), str)]
                entry["description"] = entry["description"] or next(iter(descriptions), entry["name"])
                entry["category"] = entry["category"] or "optional"
                entry["catalog_source"] = "builtin"
            entry["configuration_scope"] = "declared-environment-keys"
            # The installed native definition takes precedence over a library
            # alternative with the same service ID, matching ODS resolution.
            entries[entry["id"]] = entry
    return sorted(entries.values(), key=lambda entry: entry["id"])


def main() -> None:
    args = parse_args()
    library_dir = args.library_dir.resolve()
    output_path = args.output.resolve()

    default_library = Path(__file__).resolve().parent.parent / "extensions/library/services"
    services_dir = args.services_dir
    if services_dir is None and library_dir == default_library.resolve():
        services_dir = default_library.parent.parent / "services"
    entries = generate_catalog(library_dir, services_dir.resolve() if services_dir else None)

    catalog = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": CATALOG_SCHEMA_VERSION,
        "catalog_revision": catalog_revision(entries),
        "extensions": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Generated catalog with {len(entries)} extensions at {output_path}")


if __name__ == "__main__":
    main()
