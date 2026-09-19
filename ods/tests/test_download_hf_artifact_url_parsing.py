#!/usr/bin/env python3
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from importlib.machinery import SourceFileLoader
mod = SourceFileLoader("download_hf_artifact", str(ROOT_DIR / "scripts/download-hf-artifact.py")).load_module()

def test_parse_urls():
    # Test whitespace
    repo, rev, fn = mod.parse_huggingface_resolve_url("  https://huggingface.co/org/repo/resolve/main/model.gguf  ")
    assert repo == "org/repo"
    assert rev == "main"
    assert fn == "model.gguf"

    # Test explicit port 443
    repo, rev, fn = mod.parse_huggingface_resolve_url("https://huggingface.co:443/org/repo/resolve/main/model.gguf")
    assert repo == "org/repo"
    assert rev == "main"
    assert fn == "model.gguf"

    # Test hf.co short domain with port
    repo, rev, fn = mod.parse_huggingface_resolve_url("https://hf.co:443/org/repo/resolve/v1.0/file.bin")
    assert repo == "org/repo"
    assert rev == "v1.0"
    assert fn == "file.bin"
    print("test_download_hf_artifact_url_parsing passed.")

if __name__ == "__main__":
    test_parse_urls()
