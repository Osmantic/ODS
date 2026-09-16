#!/usr/bin/env python3
"""Regression test: verify parse_dotenv preserves export-prefixed variables."""
import sys
import tempfile
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "scripts"))

import importlib.util
spec = importlib.util.spec_from_file_location("preserve_model", repo_root / "scripts" / "preserve-active-model.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["preserve_model"] = mod
spec.loader.exec_module(mod)

def test_export_prefixed_env():
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as f:
        f.write("export GGUF_FILE=test-model.gguf\nexport LLAMA_PARALLEL=4\n")
        f.flush()
        parsed = mod.parse_dotenv(Path(f.name))
        assert parsed.get("GGUF_FILE") == "test-model.gguf"
        assert parsed.get("LLAMA_PARALLEL") == "4"

if __name__ == "__main__":
    test_export_prefixed_env()
    print("test_preserve_active_model_export_prefix: PASS")
