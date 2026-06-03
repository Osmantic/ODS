#!/usr/bin/env python3
"""Windows Python resolver contract checks."""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def test_windows_python_resolver_is_shared() -> None:
    resolver = ROOT / "installers/windows/lib/python-resolver.ps1"
    assert resolver.exists(), "Windows Python resolver helper is missing"

    install = (ROOT / "installers/windows/install-windows.ps1").read_text(encoding="utf-8")
    dream = (ROOT / "installers/windows/dream.ps1").read_text(encoding="utf-8")

    assert 'python-resolver.ps1' in install
    assert 'python-resolver.ps1' in dream


def test_windows_python_resolver_validates_runnable_python3() -> None:
    resolver = (ROOT / "installers/windows/lib/python-resolver.ps1").read_text(encoding="utf-8")

    assert "Resolve-DreamWindowsPython" in resolver
    assert "DREAM_PYTHON" in resolver
    assert '"py"' in resolver and '"-3"' in resolver
    assert "sys.version_info >= ($MinimumMajor, $MinimumMinor)" in resolver
    assert "Microsoft Store" in resolver


def test_windows_agent_paths_use_resolved_python_args() -> None:
    dream = (ROOT / "installers/windows/dream.ps1").read_text(encoding="utf-8")
    devtools = (ROOT / "installers/windows/phases/07-devtools.ps1").read_text(encoding="utf-8")
    directories = (ROOT / "installers/windows/phases/06-directories.ps1").read_text(encoding="utf-8")

    for text in (dream, devtools):
        assert "Resolve-DreamWindowsPython" in text
        assert "ConvertTo-DreamPowerShellArrayExpression @($_python.PythonArgs)" in text
        assert "$agentArgs = $_pythonArgsLiteral +" in text

    assert "$_pyCmd = Resolve-DreamWindowsPython" in directories
    assert "& $_pyCmd.Source @($_pyCmd.PythonArgs) -c" in directories


def test_windows_scripts_do_not_bypass_python_resolver() -> None:
    helper = ROOT / "installers/windows/lib/python-resolver.ps1"
    violations: list[str] = []
    direct_python_lookup = re.compile(r"Get-Command\s+['\"]?(?:python|python3|py)['\"]?\b", re.IGNORECASE)

    for path in (ROOT / "installers/windows").rglob("*.ps1"):
        if path == helper:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if direct_python_lookup.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")

    assert not violations, "Windows scripts must use Resolve-DreamWindowsPython:\n" + "\n".join(violations)
