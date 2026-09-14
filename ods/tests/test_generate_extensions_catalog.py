"""Tests for generate-extensions-catalog.py atomic generation."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib.util
spec = importlib.util.spec_from_file_location("generate_catalog_mod", str(Path(__file__).parent.parent / "scripts" / "generate-extensions-catalog.py"))
cat_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cat_mod)


def test_generate_catalog_writes_atomically_without_leftover_temp(tmp_path, monkeypatch):
    lib_dir = tmp_path / "library"
    lib_dir.mkdir()
    ext_dir = lib_dir / "ext-a"
    ext_dir.mkdir()
    (ext_dir / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (ext_dir / "manifest.yaml").write_text("id: ext-a\nname: Extension A\ndescription: Test\n", encoding="utf-8")

    out_file = tmp_path / "output" / "catalog.json"
    monkeypatch.setattr("sys.argv", ["generate-extensions-catalog.py", "--library-dir", str(lib_dir), "--output", str(out_file)])
    cat_mod.main()

    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "extensions" in data
    # Verify no remaining temporary files in parent directory
    temps = list(out_file.parent.glob("*.tmp"))
    assert len(temps) == 0
