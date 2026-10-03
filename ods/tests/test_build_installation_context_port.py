#!/usr/bin/env python3
import tempfile
from pathlib import Path
from unittest.mock import patch
from importlib.machinery import SourceFileLoader

ROOT_DIR = Path(__file__).resolve().parents[1]
mod = SourceFileLoader("build_context", str(ROOT_DIR / "scripts/build-installation-context.py")).load_module()

def test_configured_llm_port():
    with tempfile.TemporaryDirectory() as td:
        env_path = Path(td) / ".env"
        env_path.write_text("LLM_PORT=9090\nODS_DEVICE_NAME=ods-test\n")

        with patch.object(mod, "_loaded_model", return_value="custom-model") as mock_loaded:
            block = mod.build_context_block(env_path)
            mock_loaded.assert_called_once_with(llm_port=9090)
            assert "custom-model" in block
    print("test_build_installation_context_port passed.")

if __name__ == "__main__":
    test_configured_llm_port()
