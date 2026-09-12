#!/usr/bin/env python3
"""Generate or verify the standalone extension-library schema mirror."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
MANIFEST_FILE = ROOT_DIR / "manifest.json"
LIBRARY_SCHEMA_DIR = ROOT_DIR / "extensions" / "library" / "schema"


def canonical_schema_path() -> Path:
    """Return the legacy v1 path for standalone callers kept for compatibility."""
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    relative_path = manifest["contracts"]["extensions"]["serviceManifestSchema"]
    schema_path = (ROOT_DIR / relative_path).resolve()
    if not schema_path.is_file():
        raise FileNotFoundError(f"declared manifest schema not found: {relative_path}")
    return schema_path


def canonical_schema_paths() -> dict[str, Path]:
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    configured = manifest["contracts"]["extensions"]["serviceManifestSchemas"]
    if not isinstance(configured, dict) or set(configured) != {
        "ods.services.v1",
        "ods.services.v2",
    }:
        raise ValueError("serviceManifestSchemas must declare exactly v1 and v2")
    result: dict[str, Path] = {}
    for version, relative_path in configured.items():
        if not isinstance(relative_path, str):
            raise TypeError("manifest schema path must be a string")
        schema_path = (ROOT_DIR / relative_path).resolve()
        if not schema_path.is_file():
            raise FileNotFoundError(f"declared manifest schema not found: {relative_path}")
        result[version] = schema_path
    if result["ods.services.v1"] != canonical_schema_path():
        raise ValueError("legacy serviceManifestSchema must remain the v1 contract")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate the extension-library schema mirror from the canonical schema."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the generated mirror differs instead of updating it",
    )
    args = parser.parse_args()

    try:
        sources = canonical_schema_paths()
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot resolve manifest schema contract: {exc}", file=sys.stderr)
        return 2

    stale: list[tuple[Path, Path, bytes]] = []
    for version, source in sorted(sources.items()):
        destination = LIBRARY_SCHEMA_DIR / source.name
        expected = source.read_bytes()
        actual = destination.read_bytes() if destination.exists() else None
        if actual != expected:
            stale.append((source, destination, expected))

    if not stale:
        print("Manifest schema mirrors are current: v1 and v2")
        return 0

    if args.check:
        print(
            "ERROR: generated manifest schema mirrors are stale; run "
            "python3 scripts/sync-manifest-schema.py",
            file=sys.stderr,
        )
        return 1

    LIBRARY_SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    for source, destination, expected in stale:
        destination.write_bytes(expected)
        print(
            "Updated generated manifest schema mirror from "
            f"{source.relative_to(ROOT_DIR)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
