#!/usr/bin/env python3
"""Regression test: verify download-hf-artifact handles URL whitespace and port-affixed hostnames."""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if not (repo_root / "scripts").exists():
    repo_root = Path.cwd() / "ods" if (Path.cwd() / "ods").exists() else Path.cwd()
sys.path.insert(0, str(repo_root / "scripts"))

import importlib.util
spec = importlib.util.spec_from_file_location("download_hf", repo_root / "scripts" / "download-hf-artifact.py")
hf_mod = importlib.util.module_from_spec(spec)
sys.modules["download_hf"] = hf_mod
spec.loader.exec_module(hf_mod)

def test_url_normalization():
    url = "  https://huggingface.co:443/TheBloke/Model/resolve/main/model.gguf  \n"
    repo, rev, filename = hf_mod.parse_huggingface_resolve_url(url)
    assert repo == "TheBloke/Model"
    assert rev == "main"
    assert filename == "model.gguf"

if __name__ == "__main__":
    test_url_normalization()
    print("test_download_hf_artifact_url_hostname: PASS")
