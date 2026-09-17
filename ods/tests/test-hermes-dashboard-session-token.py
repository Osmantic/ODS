#!/usr/bin/env python3
"""Cross-platform contract tests for the Hermes dashboard session token."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
TOKEN_KEY = "HERMES_DASHBOARD_SESSION_TOKEN"
HERMES_IMAGE = "nousresearch/hermes-agent:v2026.6.5"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def assert_valid_token(value: str) -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", value), value


def test_pin_and_compose_contract_are_consistent() -> None:
    compose_path = ROOT / "extensions/services/hermes/compose.yaml"
    compose_text = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(compose_text)
    service = compose["services"]["hermes"]

    assert HERMES_IMAGE in service["image"]
    assert (
        f"{TOKEN_KEY}=${{{TOKEN_KEY}:?installer must generate {TOKEN_KEY}}}"
        in service["environment"]
    )

    lock = json.loads((ROOT / "config/dependency-lock.json").read_text(encoding="utf-8"))
    hermes_pin = next(pin for pin in lock["entries"] if pin.get("id") == "hermes.agent")
    assert hermes_pin["value"] == HERMES_IMAGE

    pin_surfaces = (
        ROOT / ".env.example",
        ROOT / ".env.schema.json",
        ROOT / "installers/phases/08-images.sh",
        ROOT / "installers/macos/install-macos.sh",
        ROOT / "installers/windows/install-windows.ps1",
    )
    for path in pin_surfaces:
        assert HERMES_IMAGE in path.read_text(encoding="utf-8"), path


def test_all_installers_generate_and_persist_token() -> None:
    linux = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")
    macos = (ROOT / "installers/macos/lib/env-generator.sh").read_text(encoding="utf-8")
    windows = (ROOT / "installers/windows/lib/env-generator.ps1").read_text(encoding="utf-8")

    assert f"{TOKEN_KEY}=$(_env_get {TOKEN_KEY}" in linux
    assert f"{TOKEN_KEY}=${{{TOKEN_KEY}}}" in linux
    assert f'read_env_value "$env_path" "{TOKEN_KEY}"' in macos
    assert f'upsert_env_value "$env_path" "{TOKEN_KEY}"' in macos
    assert f"{TOKEN_KEY}=${{hermes_dashboard_session_token}}" in macos
    assert f'Get-EnvOrNew "{TOKEN_KEY}"' in windows
    assert f"{TOKEN_KEY}=$hermesDashboardSessionToken" in windows


def test_all_update_clis_backfill_before_compose() -> None:
    linux = (ROOT / "ods-cli").read_text(encoding="utf-8")
    macos = (ROOT / "installers/macos/ods-macos.sh").read_text(encoding="utf-8")
    windows = (ROOT / "installers/windows/ods.ps1").read_text(encoding="utf-8")

    linux_start = linux.index("get_compose_flags() {")
    macos_start = macos.index("get_compose_flags() {")
    windows_start = windows.index("function Get-ComposeFlags {")
    linux_flags = linux[linux_start : linux.index("\n}", linux_start)]
    macos_flags = macos[macos_start : macos.index("\n}", macos_start)]
    windows_flags = windows[
        windows_start : windows.index("\nfunction ", windows_start + 1)
    ]

    assert "_ensure_hermes_dashboard_session_token" in linux_flags
    assert "ensure_hermes_dashboard_session_token" in macos_flags
    assert "Ensure-HermesDashboardSessionToken" in windows_flags


@pytest.mark.skipif(
    os.name == "nt" or shutil.which("bash") is None,
    reason="macOS generator contract requires a POSIX bash environment",
)
def test_macos_generator_backfills_then_preserves_token() -> None:
    with tempfile.TemporaryDirectory(prefix="ods-hermes-token-macos-") as temp_dir:
        install_dir = Path(temp_dir)
        env_path = install_dir / ".env"
        env_path.write_text("WEBUI_AUTH=false\n", encoding="utf-8")
        command = (
            "source installers/macos/lib/detection.sh; "
            "source installers/macos/lib/env-generator.sh; "
            'generate_ods_env "$1" 3 false >/dev/null'
        )

        subprocess.run(
            ["bash", "-c", command, "ods-hermes-token", temp_dir],
            cwd=ROOT,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        first = read_env(env_path)[TOKEN_KEY]
        assert_valid_token(first)

        process_env = os.environ.copy()
        process_env[TOKEN_KEY] = "replacement-must-not-win"
        subprocess.run(
            ["bash", "-c", command, "ods-hermes-token", temp_dir],
            cwd=ROOT,
            env=process_env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert read_env(env_path)[TOKEN_KEY] == first


@pytest.mark.skipif(
    shutil.which("pwsh") is None,
    reason="Windows generator contract requires PowerShell",
)
def test_windows_generator_backfills_then_preserves_token() -> None:
    with tempfile.TemporaryDirectory(prefix="ods-hermes-token-windows-") as temp_dir:
        env = os.environ.copy()
        env["ODS_TEST_ROOT"] = str(ROOT)
        env["ODS_TEST_DIR"] = temp_dir
        script = r'''
$ErrorActionPreference = "Stop"
function Write-AIWarn { param([string]$Message) }
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/lib/detection.ps1")
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/lib/env-generator.ps1")
$tier = @{
    TierName = "Hermes token contract"
    LlmModel = "test-model"
    GgufFile = "test.gguf"
    MaxContext = 4096
}
New-ODSEnv -InstallDir $env:ODS_TEST_DIR -TierConfig $tier -Tier "3" -GpuBackend "nvidia" -ODSMode "local" | Out-Null
$env:HERMES_DASHBOARD_SESSION_TOKEN = "replacement-must-not-win"
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

        token = read_env(Path(temp_dir) / ".env")[TOKEN_KEY]
        assert_valid_token(token)
        assert token != "replacement-must-not-win"


@pytest.mark.skipif(
    shutil.which("pwsh") is None,
    reason="Windows CLI migration contract requires PowerShell",
)
def test_windows_cli_migration_backfills_then_preserves_token() -> None:
    with tempfile.TemporaryDirectory(prefix="ods-hermes-token-windows-cli-") as temp_dir:
        install_dir = Path(temp_dir)
        (install_dir / ".env").write_text("ODS_VERSION=2.6.0\n", encoding="utf-8")
        env = os.environ.copy()
        env["ODS_HOME"] = temp_dir
        env["ODS_TEST_ROOT"] = str(ROOT)
        script = r'''
$ErrorActionPreference = "Stop"
. (Join-Path $env:ODS_TEST_ROOT "installers/windows/ods.ps1") -Command help *> $null
Ensure-HermesDashboardSessionToken
$first = Get-ODSEnvValue -Name "HERMES_DASHBOARD_SESSION_TOKEN"
Ensure-HermesDashboardSessionToken
$second = Get-ODSEnvValue -Name "HERMES_DASHBOARD_SESSION_TOKEN"
Write-Output "$first`n$second"
'''
        completed = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tokens = [
            line.strip()
            for line in completed.stdout.splitlines()
            if re.fullmatch(r"[0-9a-f]{64}", line.strip())
        ]
        assert len(tokens) == 2, completed.stdout
        assert_valid_token(tokens[0])
        assert tokens[1] == tokens[0]
