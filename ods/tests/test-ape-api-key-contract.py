#!/usr/bin/env python3
"""Cross-platform boundary contracts for APE authentication."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
KEY = "APE_API_KEY"


def _read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def _assert_key(value: str) -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", value), value


def test_runtime_and_all_compose_entrypoints_require_a_stable_key() -> None:
    compose = yaml.safe_load(
        (ROOT / "extensions/services/ape/compose.yaml").read_text(encoding="utf-8")
    )
    assert (
        f"{KEY}=${{{KEY}:?installer must generate {KEY}}}"
        in compose["services"]["ape"]["environment"]
    )

    linux_cli = (ROOT / "ods-cli").read_text(encoding="utf-8")
    macos_cli = (ROOT / "installers/macos/ods-macos.sh").read_text(encoding="utf-8")
    windows_cli = (ROOT / "installers/windows/ods.ps1").read_text(encoding="utf-8")
    assert "_ensure_ape_api_key" in linux_cli
    assert "ensure_ape_api_key" in macos_cli
    assert "Ensure-ApeApiKey" in windows_cli

    process_env = os.environ.copy()
    process_env.pop(KEY, None)
    imported = subprocess.run(
        [shutil.which("python") or "python", "-c", "import main"],
        cwd=ROOT / "extensions/services/ape",
        env=process_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert imported.returncode != 0
    assert f"{KEY} is required" in imported.stderr


def test_all_installers_generate_and_persist_key() -> None:
    linux = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")
    macos = (ROOT / "installers/macos/lib/env-generator.sh").read_text(encoding="utf-8")
    windows = (ROOT / "installers/windows/lib/env-generator.ps1").read_text(encoding="utf-8")

    assert f"{KEY}=$(_env_get {KEY}" in linux
    assert f"{KEY}=${{{KEY}}}" in linux
    assert f'read_env_value "$env_path" "{KEY}"' in macos
    assert f'upsert_env_value "$env_path" "{KEY}"' in macos
    assert f"{KEY}=${{ape_api_key}}" in macos
    assert f'Get-EnvOrNew "{KEY}"' in windows
    assert f"{KEY}=$apeApiKey" in windows


@pytest.mark.skipif(
    sys.platform != "darwin" or shutil.which("bash") is None,
    reason="macOS generator contract requires macOS tooling",
)
def test_macos_generator_backfills_then_preserves_key() -> None:
    with tempfile.TemporaryDirectory(prefix="ods-ape-key-macos-") as temp_dir:
        env_path = Path(temp_dir) / ".env"
        env_path.write_text("WEBUI_AUTH=false\n", encoding="utf-8")
        command = (
            "source installers/macos/lib/detection.sh; "
            "source installers/macos/lib/env-generator.sh; "
            'generate_ods_env "$1" 3 false >/dev/null'
        )
        subprocess.run(
            ["bash", "-c", command, "ods-ape-key", temp_dir],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        first = _read_env(env_path)[KEY]
        _assert_key(first)

        process_env = os.environ.copy()
        process_env[KEY] = "replacement-must-not-win"
        subprocess.run(
            ["bash", "-c", command, "ods-ape-key", temp_dir],
            cwd=ROOT,
            env=process_env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert _read_env(env_path)[KEY] == first


@pytest.mark.skipif(
    shutil.which("pwsh") is None,
    reason="Windows generator contract requires PowerShell",
)
def test_windows_generator_backfills_then_preserves_key() -> None:
    with tempfile.TemporaryDirectory(prefix="ods-ape-key-windows-") as temp_dir:
        env = os.environ.copy()
        env["ODS_TEST_ROOT"] = str(ROOT)
        env["ODS_TEST_DIR"] = temp_dir
        script = r'''
$ErrorActionPreference = "Stop"
function Write-AIWarn { param([string]$Message) }
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/lib/detection.ps1")
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/lib/env-generator.ps1")
$tier = @{
    TierName = "APE key contract"
    LlmModel = "test-model"
    GgufFile = "test.gguf"
    MaxContext = 4096
}
New-ODSEnv -InstallDir $env:ODS_TEST_DIR -TierConfig $tier -Tier "3" -GpuBackend "nvidia" -ODSMode "local" | Out-Null
$env:APE_API_KEY = "replacement-must-not-win"
New-ODSEnv -InstallDir $env:ODS_TEST_DIR -TierConfig $tier -Tier "3" -GpuBackend "nvidia" -ODSMode "local" | Out-Null
'''
        subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        key = _read_env(Path(temp_dir) / ".env")[KEY]
        _assert_key(key)
        assert key != "replacement-must-not-win"
