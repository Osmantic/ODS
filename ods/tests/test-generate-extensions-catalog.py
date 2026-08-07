#!/usr/bin/env python3
"""Regression tests for extension catalog generation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate-extensions-catalog.py"


def load_module():
    spec = importlib.util.spec_from_file_location("generate_extensions_catalog", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(path: Path, service_id: str) -> None:
    path.parent.mkdir(parents=True)
    path.write_text(
        "schema_version: ods.services.v1\n"
        "service:\n"
        f"  id: {service_id}\n"
        f"  name: {service_id}\n",
        encoding="utf-8",
    )


def test_duplicate_service_ids_are_rejected() -> None:
    module = load_module()
    with TemporaryDirectory() as tmp:
        library = Path(tmp)
        _write_manifest(library / "first" / "manifest.yaml", "duplicate")
        _write_manifest(library / "second" / "manifest.yaml", "duplicate")

        with pytest.raises(ValueError, match="duplicate service id 'duplicate'"):
            module.generate_catalog(library)


if __name__ == "__main__":
    test_duplicate_service_ids_are_rejected()
    print("[PASS] extension catalog generator tests")
