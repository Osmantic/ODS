"""Tests for patch-hermes-config.py atomic writing and configuration patching."""

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib.util
spec = importlib.util.spec_from_file_location("patch_hermes_config", str(Path(__file__).parent.parent / "scripts" / "patch-hermes-config.py"))
patch_hermes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch_hermes)


def test_patch_hermes_config_writes_atomically_and_preserves_mode(tmp_path):
    target = tmp_path / "config.yaml"
    original = "model: old-model\nbase_url: http://localhost:11434\n"
    target.write_text(original, encoding="utf-8")
    target.chmod(0o640)

    changed = patch_hermes.patch_config(
        target,
        model="new-model",
        base_url="http://localhost:8000",
        context_length=8192,
    )
    assert changed is True
    assert "new-model" in target.read_text(encoding="utf-8")
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
