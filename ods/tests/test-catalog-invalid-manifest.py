#!/usr/bin/env python3
"""Regression: catalog generation must not silently omit broken manifests."""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "generate-extensions-catalog.py"


def test_invalid_manifest_fails_catalog_generation(tmp_path):
    library = tmp_path / "services"
    broken = library / "broken-extension"
    broken.mkdir(parents=True)
    (broken / "manifest.yaml").write_text(
        "schema_version: wrong\nservice:\n  id: broken-extension\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--library-dir", str(library), "--output", str(tmp_path / "catalog.json")],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "invalid extension manifest" in result.stderr
    # Windows uses backslashes in the subprocess path.
    assert "broken-extension" in result.stderr
    assert "manifest.yaml" in result.stderr


def test_shipped_catalog_preserves_intentional_exclusions(tmp_path):
    output = tmp_path / "catalog.json"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", str(output)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    ids = {entry["id"] for entry in json.loads(output.read_text(encoding="utf-8"))["extensions"]}
    assert "pixel-agent" in ids
    assert "privacy-shield" not in ids
    assert "dify" not in ids
    assert "apache-answer" in ids
    assert "jan" not in ids
    assert "bookstack" in ids
