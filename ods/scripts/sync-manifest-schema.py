#!/usr/bin/env python3
"""Generate or verify the standalone extension-library schema mirror."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
MANIFEST_FILE = ROOT_DIR / "manifest.json"
LIBRARY_SCHEMA = ROOT_DIR / "extensions" / "library" / "schema" / "service-manifest.v1.json"


def canonical_schema_path() -> Path:
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    relative_path = manifest["contracts"]["extensions"]["serviceManifestSchema"]
    if not isinstance(relative_path, str):
        raise TypeError("manifest schema contract path must be a string")
    schema_path = (ROOT_DIR / relative_path).resolve()
    try:
        schema_path.relative_to(ROOT_DIR.resolve())
    except ValueError as exc:
        raise ValueError(
            f"declared manifest schema escapes the repository: {relative_path}"
        ) from exc
    if not schema_path.is_file():
        raise FileNotFoundError(f"declared manifest schema not found: {relative_path}")
    return schema_path


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
        source = canonical_schema_path()
        expected = source.read_bytes()
        actual = LIBRARY_SCHEMA.read_bytes() if LIBRARY_SCHEMA.exists() else None
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot resolve manifest schema contract: {exc}", file=sys.stderr)
        return 2

    if actual == expected:
        print(f"Manifest schema mirror is current: {LIBRARY_SCHEMA.relative_to(ROOT_DIR)}")
        return 0

    if args.check:
        print(
            "ERROR: generated manifest schema mirror is stale; run "
            "python3 scripts/sync-manifest-schema.py",
            file=sys.stderr,
        )
        return 1

    LIBRARY_SCHEMA.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_SCHEMA.write_bytes(expected)
    print(
        "Updated generated manifest schema mirror from "
        f"{source.relative_to(ROOT_DIR)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
