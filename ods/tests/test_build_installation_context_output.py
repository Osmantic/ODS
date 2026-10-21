#!/usr/bin/env python3
"""Regression tests for build-installation-context output path safety."""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SCRIPT = ROOT_DIR / "scripts" / "build-installation-context.py"


def _run(template: Path, env_path: Path, output: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--template",
        str(template),
        "--env",
        str(env_path),
        "--output",
        str(output),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _setup(td: Path) -> tuple[Path, Path]:
    tpl = td / "SOUL.md.template"
    tpl.write_text("# Persona\n\n<!-- INSTALLATION_CONTEXT -->\n", encoding="utf-8")
    env = td / ".env"
    env.write_text("DEVICE_TIER=1\nODS_DEVICE_NAME=ods-test\n", encoding="utf-8")
    return tpl, env


def test_empty_directory_replaced_by_file():
    with tempfile.TemporaryDirectory() as td:
        tpl, env = _setup(Path(td))
        output = Path(td) / "SOUL.md"
        output.mkdir()
        res = _run(tpl, env, output)
        assert res.returncode == 0, res.stderr
        assert output.is_file() and not output.is_dir()
        assert "ods-test" in output.read_text(encoding="utf-8")


def test_populated_directory_rejected_and_preserved():
    with tempfile.TemporaryDirectory() as td:
        tpl, env = _setup(Path(td))
        output = Path(td) / "SOUL.md"
        output.mkdir()
        sentinel = output / "important.txt"
        sentinel.write_text("operator data", encoding="utf-8")
        res = _run(tpl, env, output)
        assert res.returncode != 0
        assert "Traceback" not in res.stderr and "ERROR:" in res.stderr
        assert output.is_dir()
        assert sentinel.read_text(encoding="utf-8") == "operator data"


def test_symlink_directory_rejected_and_preserved():
    with tempfile.TemporaryDirectory() as td:
        tpl, env = _setup(Path(td))
        real_dir = Path(td) / "target_dir"
        real_dir.mkdir()
        canary = real_dir / "target_file.txt"
        canary.write_text("target data", encoding="utf-8")
        output = Path(td) / "SOUL.md"
        output.symlink_to(real_dir)
        res = _run(tpl, env, output)
        assert res.returncode != 0
        assert "Traceback" not in res.stderr and "ERROR:" in res.stderr
        assert output.is_symlink()
        assert canary.read_text(encoding="utf-8") == "target data"


def test_symlink_file_overwritten():
    with tempfile.TemporaryDirectory() as td:
        tpl, env = _setup(Path(td))
        real_file = Path(td) / "real_soul.md"
        real_file.write_text("old persona", encoding="utf-8")
        output = Path(td) / "SOUL.md"
        output.symlink_to(real_file)
        res = _run(tpl, env, output)
        assert res.returncode == 0, res.stderr
        assert output.is_symlink()
        assert "ods-test" in real_file.read_text(encoding="utf-8")


def test_normal_file_overwrite():
    with tempfile.TemporaryDirectory() as td:
        tpl, env = _setup(Path(td))
        output = Path(td) / "SOUL.md"
        output.write_text("old persona", encoding="utf-8")
        res = _run(tpl, env, output)
        assert res.returncode == 0, res.stderr
        assert output.is_file()
        assert "ods-test" in output.read_text(encoding="utf-8")


if __name__ == "__main__":
    test_empty_directory_replaced_by_file()
    test_populated_directory_rejected_and_preserved()
    test_symlink_directory_rejected_and_preserved()
    test_symlink_file_overwritten()
    test_normal_file_overwrite()
    print("[PASS] All build-installation-context output safety tests passed.")
