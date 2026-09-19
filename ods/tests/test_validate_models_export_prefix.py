#!/usr/bin/env python3
"""Regression test: verify env_value reads export-prefixed variables."""
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "scripts"))

import importlib.util
spec = importlib.util.spec_from_file_location("validate_models", repo_root / "scripts" / "validate-models.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["validate_models"] = mod
spec.loader.exec_module(mod)

def test_export_prefixed_env_value():
    with tempfile.TemporaryDirectory() as tmpdir:
        env_path = Path(tmpdir) / ".env"
        env_path.write_text("export ODS_MODE=cloud\nexport GGUF_FILE=custom.gguf\n", encoding="utf-8")
        assert mod.env_value(Path(tmpdir), "ODS_MODE", "local") == "cloud"
        assert mod.env_value(Path(tmpdir), "GGUF_FILE", "") == "custom.gguf"

if __name__ == "__main__":
    test_export_prefixed_env_value()
    print("test_validate_models_export_prefix: PASS")
