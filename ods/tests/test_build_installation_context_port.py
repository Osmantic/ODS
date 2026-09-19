#!/usr/bin/env python3
"""LLM host port resolution in installation context builder."""

import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_context", str(ROOT_DIR / "scripts/build-installation-context.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_default_llm_port():
    with tempfile.TemporaryDirectory() as td:
        env_path = Path(td) / ".env"
        env_path.write_text("ODS_DEVICE_NAME=ods-test\n")

        with patch.object(mod, "_loaded_model", return_value="default-model") as mock_loaded:
            block = mod.build_context_block(env_path)
            mock_loaded.assert_called_once_with(llm_port=11434)
            assert "default-model" in block


def test_ollama_port_precedence():
    with tempfile.TemporaryDirectory() as td:
        env_path = Path(td) / ".env"
        env_path.write_text("OLLAMA_PORT=11435\nLLM_PORT=9090\nODS_DEVICE_NAME=ods-test\n")

        with patch.object(mod, "_loaded_model", return_value="custom-model") as mock_loaded:
            block = mod.build_context_block(env_path)
            mock_loaded.assert_called_once_with(llm_port=11435)
            assert "custom-model" in block


def test_llama_server_port_and_invalid_fallback():
    with tempfile.TemporaryDirectory() as td:
        env_path = Path(td) / ".env"
        env_path.write_text("LLAMA_SERVER_PORT=11436\nODS_DEVICE_NAME=ods-test\n")

        with patch.object(mod, "_loaded_model", return_value=None) as mock_loaded:
            mod.build_context_block(env_path)
            mock_loaded.assert_called_once_with(llm_port=11436)

        env_path.write_text("OLLAMA_PORT=not-a-number\nODS_DEVICE_NAME=ods-test\n")
        with patch.object(mod, "_loaded_model", return_value=None) as mock_loaded:
            mod.build_context_block(env_path)
            mock_loaded.assert_called_once_with(llm_port=11434)


if __name__ == "__main__":
    test_default_llm_port()
    test_ollama_port_precedence()
    test_llama_server_port_and_invalid_fallback()
    print("test_build_installation_context_port passed.")
