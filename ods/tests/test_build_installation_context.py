"""Tests for build-installation-context.py atomic writing."""

import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import importlib.util
spec = importlib.util.spec_from_file_location("build_inst_ctx", str(Path(__file__).parent.parent / "scripts" / "build-installation-context.py"))
build_ctx = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_ctx)


def test_build_installation_context_writes_atomically_with_mode_preservation(tmp_path):
    output_path = tmp_path / "SOUL.md"
    original = "Original SOUL content\n"
    output_path.write_text(original, encoding="utf-8")
    output_path.chmod(0o644)

    env_path = tmp_path / ".env"
    env_path.write_text("SOME_VAR=1\n", encoding="utf-8")

    template_path = tmp_path / "template.md"
    template_path.write_text("Template header\n", encoding="utf-8")

    changed = build_ctx.build_soul(
        env_path=env_path,
        output_path=output_path,
        template_path=template_path,
        profile="default",
    )

    assert changed is True
    assert output_path.exists()
    assert "Template header" in output_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o644

    # Verify no temp files remain
    temps = list(tmp_path.glob("*.tmp"))
    assert len(temps) == 0
