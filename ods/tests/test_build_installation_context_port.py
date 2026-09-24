#!/usr/bin/env python3
import tempfile
from pathlib import Path
from unittest.mock import patch
from importlib.machinery import SourceFileLoader

ROOT_DIR = Path(__file__).resolve().parents[1]
mod = SourceFileLoader("build_context", str(ROOT_DIR / "scripts/build-installation-context.py")).load_module()

def _probed_port(env_text: str) -> int:
    with tempfile.TemporaryDirectory() as td:
        env_path = Path(td) / ".env"
        env_path.write_text(env_text)
        with patch.object(mod, "_loaded_model", return_value=None) as mock_loaded:
            mod.build_context_block(env_path)
            return mock_loaded.call_args.kwargs["llm_port"]


def test_configured_llm_port():
    with tempfile.TemporaryDirectory() as td:
        env_path = Path(td) / ".env"
        env_path.write_text("LLM_PORT=9090\nODS_DEVICE_NAME=ods-test\n")

        with patch.object(mod, "_loaded_model", return_value="custom-model") as mock_loaded:
            block = mod.build_context_block(env_path)
            mock_loaded.assert_called_once_with(llm_port=9090)
            assert "custom-model" in block
    print("test_build_installation_context_port passed.")


def test_default_port_is_external_host_port():
    """No port keys at all -> canonical external default 11434, never the
    container-internal 8080 (config/ports.json external_default)."""
    assert _probed_port("ODS_DEVICE_NAME=ods-test\n") == 11434


def test_ollama_port_is_read():
    """The canonical .env key drives the host-side probe."""
    assert _probed_port("OLLAMA_PORT=12345\n") == 12345


def test_llama_server_port_alias():
    """Deprecated alias still resolves when OLLAMA_PORT is absent."""
    assert _probed_port("LLAMA_SERVER_PORT=11444\n") == 11444


def test_lemonade_uses_amd_inference_port():
    """Lemonade installs probe AMD_INFERENCE_PORT (native default 8080)."""
    env = "LLM_BACKEND=lemonade\nAMD_INFERENCE_PORT=8080\n"
    assert _probed_port(env) == 8080
    # Lemonade without the key still defaults to its native 8080.
    assert _probed_port("LLM_BACKEND=lemonade\n") == 8080


if __name__ == "__main__":
    test_configured_llm_port()
    test_default_port_is_external_host_port()
    test_ollama_port_is_read()
    test_llama_server_port_alias()
    test_lemonade_uses_amd_inference_port()
