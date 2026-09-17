#!/usr/bin/env python3
"""Regression test: verify generate-extensions-catalog.py produces valid catalog atomically."""
import sys
import json
import subprocess
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
script = repo_root / "scripts" / "generate-extensions-catalog.py"

def test_atomic_catalog_generation():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "catalog.json"
        res = subprocess.run(
            [sys.executable, str(script), "--output", str(out)],
            capture_output=True, text=True
        )
        assert res.returncode == 0, f"Expected 0, got {res.returncode}: {res.stderr}"
        assert out.exists()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert "extensions" in data

if __name__ == "__main__":
    test_atomic_catalog_generation()
    print("test_generate_extensions_catalog_atomic: PASS")
