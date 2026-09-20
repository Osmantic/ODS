#!/usr/bin/env python3
"""Generate a static extensions catalog JSON from extension manifest files.

Scans the product-owned extension library for valid ods.services.v1 manifests,
extracts catalog-relevant fields, and writes a sorted JSON catalog
to ods/config/extensions-catalog.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


SCHEMA_VERSION = "ods.services.v1"
CATALOG_SCHEMA_VERSION = "1.0.0"
EXCLUDED_IDS = {"privacy-shield"}
SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class CatalogGenerationError(ValueError):
    """Raised when a manifest cannot be represented in the catalog."""


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


def load_manifest(manifest_path: Path) -> dict:
    """Load and validate one manifest, failing with its path on any error."""
    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as e:
        raise CatalogGenerationError(f"{manifest_path}: failed to read manifest: {e}") from e

    if not isinstance(data, dict):
        raise CatalogGenerationError(f"{manifest_path}: root is not a mapping")

    if data.get("schema_version") != SCHEMA_VERSION:
        raise CatalogGenerationError(
            f"{manifest_path}: schema_version is '{data.get('schema_version')}', "
            f"expected '{SCHEMA_VERSION}'"
        )

    return data


def extract_entry(manifest: dict, manifest_path: Path) -> dict:
    """Extract a catalog entry from a validated manifest dict."""
    service = manifest.get("service")
    if not isinstance(service, dict):
        raise CatalogGenerationError(f"{manifest_path}: service must be a mapping")

    service_id = service.get("id")
    if not isinstance(service_id, str) or not service_id or not SERVICE_ID_RE.match(service_id):
        raise CatalogGenerationError(f"{manifest_path}: service.id is missing or invalid")

    if service_id in EXCLUDED_IDS:
        return None

    env_vars = service.get("env_vars", [])
    if not isinstance(env_vars, list):
        env_vars = []

    entry = {
        "id": service_id,
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


def generate_catalog(library_dir: Path) -> list[dict]:
    """Scan manifest files and return sorted catalog entries."""
    if not library_dir.is_dir():
        print(f"ERROR: Library directory not found: {library_dir}", file=sys.stderr)
        sys.exit(1)

    entries = []
    for service_dir in sorted(library_dir.iterdir()):
        if not service_dir.is_dir():
            continue

        manifest_path = service_dir / "manifest.yaml"
        if not manifest_path.exists():
            continue

        manifest = load_manifest(manifest_path)
        if manifest is None:
            continue

        entry = extract_entry(manifest, manifest_path)
        entries.append(entry)

    entries.sort(key=lambda e: e["id"])
    return entries


def main() -> None:
    args = parse_args()
    library_dir = args.library_dir.resolve()
    output_path = args.output.resolve()

    try:
        entries = generate_catalog(library_dir)
    except CatalogGenerationError as exc:
        print(f"ERROR: catalog generation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    catalog = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": CATALOG_SCHEMA_VERSION,
        "extensions": entries,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Generated catalog with {len(entries)} extensions at {output_path}")


if __name__ == "__main__":
    main()
