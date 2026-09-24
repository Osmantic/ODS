#!/usr/bin/env python3
"""Generate a deterministic CycloneDX inventory of shipped container images.

The inventory is intentionally source-derived: it records every image used by
the core Compose stack, bundled services, and the optional extension library.
It does not contact registries, so maintainers and downstream forks can produce
the same document offline from the same commit.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from types import ModuleType
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
PIN_CHECKER = ROOT / "scripts" / "check-dependency-pins.py"


def _load_pin_checker(path: Path = PIN_CHECKER) -> ModuleType:
    spec = importlib.util.spec_from_file_location("ods_dependency_pins", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load dependency scanner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _ods_version(root: Path) -> str:
    with (root / "manifest.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    version = manifest.get("ods_version")
    if not isinstance(version, str) or not version:
        raise ValueError("manifest.json must define a non-empty ods_version")
    return version


def _component_identity(reference: str) -> tuple[str, str, str]:
    """Return (name, version, purl) for a Docker/OCI reference."""
    without_digest, separator, digest = reference.partition("@sha256:")
    last_slash = without_digest.rfind("/")
    last_colon = without_digest.rfind(":")
    if last_colon > last_slash:
        name = without_digest[:last_colon]
        version = without_digest[last_colon + 1 :]
    else:
        name = without_digest
        version = "latest"

    qualifiers = f"?digest=sha256%3A{digest}" if separator else ""
    purl = f"pkg:docker/{quote(name, safe='/')}@{quote(version, safe='')}{qualifiers}"
    return name, version, purl


def build_bom(
    root: Path = ROOT,
    checker: ModuleType | None = None,
    source_commit: str | None = None,
) -> dict:
    checker = checker or _load_pin_checker(root / "scripts" / "check-dependency-pins.py")
    scoped_refs = [
        ("runtime", ref) for ref in checker.discover_image_refs(root)
    ] + [
        ("extension-library", ref)
        for ref in checker.discover_extension_library_image_refs(root)
    ]

    by_reference: dict[str, list[tuple[str, object]]] = defaultdict(list)
    for scope, ref in scoped_refs:
        by_reference[ref.value].append((scope, ref))

    components = []
    for reference in sorted(by_reference):
        entries = by_reference[reference]
        name, version, purl = _component_identity(reference)
        paths = sorted({entry.path for _, entry in entries})
        scopes = sorted({scope for scope, _ in entries})
        raw_refs = sorted({entry.raw for _, entry in entries})
        component = {
            "type": "container",
            "bom-ref": purl,
            "name": name,
            "version": version,
            "purl": purl,
            "properties": [
                {"name": "ods:image-reference", "value": reference},
                {"name": "ods:source-paths", "value": ",".join(paths)},
                {"name": "ods:scopes", "value": ",".join(scopes)},
                {"name": "ods:raw-references", "value": ",".join(raw_refs)},
            ],
        }
        if "@sha256:" in reference:
            component["hashes"] = [
                {"alg": "SHA-256", "content": reference.rsplit("@sha256:", 1)[1]}
            ]
        components.append(component)

    ods_version = _ods_version(root)
    root_ref = f"pkg:github/Osmantic/ODS@{quote(ods_version, safe='')}"
    root_component = {
        "type": "application",
        "bom-ref": root_ref,
        "name": "ODS",
        "version": ods_version,
        "purl": root_ref,
    }
    metadata_properties = [
        {
            "name": "ods:generation-mode",
            "value": "offline-source-inventory",
        }
    ]
    if source_commit is not None:
        root_component["externalReferences"] = [{
            "type": "vcs",
            "url": f"https://github.com/Osmantic/ODS/tree/{source_commit}",
        }]
        metadata_properties.append({
            "name": "ods:source-commit",
            "value": source_commit,
        })
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": root_component,
            "properties": metadata_properties,
        },
        "components": components,
        "dependencies": [
            {"ref": root_ref, "dependsOn": [item["bom-ref"] for item in components]}
        ],
    }


def _serialized(bom: dict) -> str:
    return json.dumps(bom, indent=2, sort_keys=True) + "\n"


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the ODS runtime image inventory as CycloneDX JSON."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write atomically to this path (default: stdout)",
    )
    parser.add_argument(
        "--source-commit",
        type=_source_commit,
        help="embed the exact 40- or 64-character Git commit in the inventory",
    )
    return parser.parse_args()


def _source_commit(value: str) -> str:
    normalized = value.strip().lower()
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", normalized) is None:
        raise argparse.ArgumentTypeError(
            "source commit must be a full 40- or 64-character hexadecimal object id"
        )
    return normalized


def main() -> int:
    args = parse_args()
    content = _serialized(build_bom(source_commit=args.source_commit))
    if args.output is None:
        # Write bytes so stdout is byte-for-byte reproducible on Windows too;
        # TextIO otherwise translates LF to CRLF.
        sys.stdout.buffer.write(content.encode("utf-8"))
    else:
        _write_atomic(args.output, content)
        print(f"Wrote CycloneDX runtime inventory: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
