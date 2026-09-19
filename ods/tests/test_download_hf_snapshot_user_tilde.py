#!/usr/bin/env python3
"""Regression test: verify download-hf-snapshot expands user tilde paths."""
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Provide mock for huggingface_hub if not installed in runner environment
if "huggingface_hub" not in sys.modules:
    sys.modules["huggingface_hub"] = MagicMock()

repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "scripts"))

import importlib.util
spec = importlib.util.spec_from_file_location("download_snapshot", repo_root / "scripts" / "download-hf-snapshot.py")
mod = importlib.util.module_from_spec(spec)
sys.modules["download_snapshot"] = mod
spec.loader.exec_module(mod)

def test_tilde_expansion():
    with tempfile.TemporaryDirectory() as tmpdir:
        tilde_path = Path(f"{tmpdir}/~/test_cache")
        with patch("huggingface_hub.snapshot_download", return_value=str(Path(tmpdir) / "repo")):
            (Path(tmpdir) / "repo").mkdir(parents=True, exist_ok=True)
            res = mod.download_snapshot("test-org/test-model", tilde_path)
            assert not (Path.cwd() / "~").exists()

if __name__ == "__main__":
    test_tilde_expansion()
    print("test_download_hf_snapshot_user_tilde: PASS")
