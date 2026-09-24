#!/usr/bin/env python3
"""Public-boundary checks for installation-context output recovery."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-installation-context.py"
LINUX_PHASE = ROOT / "installers" / "phases" / "11-services.sh"
WINDOWS_PHASE = ROOT / "installers" / "windows" / "phases" / "06-directories.ps1"


def run_builder(template: Path, env_file: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--template",
            str(template),
            "--env",
            str(env_file),
            "--output",
            str(output),
        ],
        capture_output=True,
        check=False,
        text=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        template = work / "SOUL.md.template"
        env_file = work / ".env"
        template.write_text("# Persona\n\n<!-- INSTALLATION_CONTEXT -->\n", encoding="utf-8")
        env_file.write_text("ODS_DEVICE_NAME=test-node\n", encoding="utf-8")

        empty_output = work / "empty" / "SOUL.md"
        empty_output.mkdir(parents=True)
        result = run_builder(template, env_file, empty_output)
        assert result.returncode == 0, result.stderr
        assert empty_output.is_file()
        assert "test-node" in empty_output.read_text(encoding="utf-8")

        nonempty_output = work / "nonempty" / "SOUL.md"
        nonempty_output.mkdir(parents=True)
        sentinel = nonempty_output / "operator-data.txt"
        sentinel.write_text("preserve me", encoding="utf-8")
        result = run_builder(template, env_file, nonempty_output)
        assert result.returncode != 0
        assert sentinel.read_text(encoding="utf-8") == "preserve me"

    linux_source = LINUX_PHASE.read_text(encoding="utf-8")
    assert 'rmdir "$_soul_output"' in linux_source
    assert 'rm -rf "$_soul_output"' not in linux_source

    windows_source = WINDOWS_PHASE.read_text(encoding="utf-8")
    assert "Remove-Item -LiteralPath $_expectedFilePath -Force -ErrorAction Stop" in windows_source
    assert "Remove-Item -LiteralPath $_expectedFilePath -Recurse" not in windows_source
    soul_refresh = windows_source.split("function Invoke-HermesSoulRefresh", 1)[1]
    soul_refresh = soul_refresh.split("if ($enableHermes)", 1)[0]
    assert "Remove-Item -LiteralPath $_output -Force -ErrorAction Stop" in soul_refresh
    assert "Remove-Item -LiteralPath $_output -Recurse" not in soul_refresh

    print("[PASS] installation-context replaces only empty output directories")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
