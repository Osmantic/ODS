#!/usr/bin/env python3
import tempfile
from pathlib import Path
from importlib.machinery import SourceFileLoader

ROOT_DIR = Path(__file__).resolve().parents[1]
mod = SourceFileLoader("validate_models", str(ROOT_DIR / "scripts/validate-models.py")).load_module()

def test_custom_models_dir():
    with tempfile.TemporaryDirectory() as td:
        tpath = Path(td)
        custom_dir = tpath / "custom_models"
        custom_dir.mkdir()
        dummy_model = custom_dir / "test.gguf"
        dummy_model.write_bytes(b"dummy model data")

        env_file = tpath / ".env"
        env_file.write_text(f"MODELS_DIR={custom_dir}\nGGUF_FILE=test.gguf\n")

        ok, msg = mod.check_llm(tpath)
        assert ok is True
        assert "OK: " in msg
    print("test_validate_models_custom_dir passed.")

if __name__ == "__main__":
    test_custom_models_dir()
