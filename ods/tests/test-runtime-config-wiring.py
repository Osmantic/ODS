#!/usr/bin/env python3
"""Contract checks for runtime config renderer wiring."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def test_linux_installer_uses_renderer_as_sole_writer() -> None:
    text = read("installers/phases/06-directories.sh")
    assert "scripts/render-runtime-configs.py" in text
    assert "--surface litellm-lemonade" in text
    assert "LITELLM_EOF" not in text
    assert "falling back to inline writer" not in text


def test_bootstrap_upgrade_uses_renderer_as_sole_writer() -> None:
    text = read("scripts/bootstrap-upgrade.sh")
    assert "scripts/render-runtime-configs.py" in text
    assert "--surface litellm-lemonade" in text
    assert "LITELLM_UPGRADE_EOF" not in text
    assert "LITELLM_WINDOWS_LEMONADE_EOF" not in text
    assert "falling back to inline writer" not in text


def test_bootstrap_upgrade_promotes_lemonade_model_id() -> None:
    text = read("scripts/bootstrap-upgrade.sh")
    assert 'write_env_value LEMONADE_MODEL "$_promotion_lemonade_model_id"' in text
    assert 'lemonade_model_id_matches_gguf "$_loaded_model_id" "$FULL_GGUF_FILE"' in text
    assert 'resolve_live_lemonade_model_id "${OLLAMA_PORT:-8080}" "$FULL_GGUF_FILE"' in text
    assert 'json_has_id "$models_json" "$model_id"' in text


def test_host_agent_uses_renderer_as_sole_writer() -> None:
    text = read("bin/ods-host-agent.py")
    assert "def _render_runtime_config" in text
    assert "--surface" in text
    assert '"--lemonade-model-id"' in text
    assert "litellm-lemonade" in text
    assert "Runtime config renderer failed" in text
    lemonade_writer = text.split("def _write_lemonade_config(", 1)[1].split(
        "def _write_windows_native_litellm_config(", 1
    )[0]
    assert "model_list:\\n" not in lemonade_writer


def test_windows_lemonade_uses_renderer_as_sole_writer() -> None:
    text = read("installers/windows/lib/env-generator.ps1")
    assert "scripts" in text
    assert "render-runtime-configs.py" in text
    assert '"litellm-lemonade"' in text
    assert '"--lemonade-model-id"' in text
    assert "Install-WindowsODSRuntimeConfigPython" in text
    assert "sys.version_info >= (3, 8)" in text
    assert "IsNullOrWhiteSpace($env:LOCALAPPDATA)" in text
    assert 'Join-Path $env:LOCALAPPDATA "Programs\\Python"' in text
    assert "winget install --exact --id Python.Python.3.12" in text
    lemonade_writer = text.split("function Write-WindowsODSLemonadeLiteLlmConfig", 1)[
        1
    ].split("function Set-WindowsODSLemonadeModelConfiguration", 1)[0]
    assert "model_list:" not in lemonade_writer


def test_openclaw_receives_persisted_lemonade_model_id() -> None:
    compose = read("extensions/services/openclaw/compose.yaml")
    injector = read("config/openclaw/inject-token.js")
    assert "LEMONADE_MODEL=${LEMONADE_MODEL:-}" in compose
    assert "LEMONADE_MODEL ||" in injector
    assert "`extra.${GGUF_FILE}`" in injector


def test_cloud_callers_do_not_render_local_switchboard() -> None:
    linux = read("installers/phases/06-directories.sh")
    macos = read("installers/macos/install-macos.sh")
    host_agent = read("bin/ods-host-agent.py")
    assert 'if [[ "$_router_ods_mode" != "cloud" ]]' in linux
    assert '"ODS_MODE")" != "cloud"' in macos
    assert 'str(common["ods_mode"]).strip().lower() != "cloud"' in host_agent


def test_runtime_renderer_callers_keep_credentials_out_of_process_arguments() -> None:
    callers = [
        read("installers/phases/06-directories.sh"),
        read("installers/macos/install-macos.sh"),
        read("installers/windows/lib/env-generator.ps1"),
        read("scripts/bootstrap-upgrade.sh"),
        read("bin/ods-host-agent.py"),
    ]
    assert all('"--litellm-key"' not in text for text in callers)
    assert all("ODS_RENDER_LITELLM_KEY" in text for text in callers)


def main() -> int:
    for test in (
        test_linux_installer_uses_renderer_as_sole_writer,
        test_bootstrap_upgrade_uses_renderer_as_sole_writer,
        test_bootstrap_upgrade_promotes_lemonade_model_id,
        test_host_agent_uses_renderer_as_sole_writer,
        test_windows_lemonade_uses_renderer_as_sole_writer,
        test_openclaw_receives_persisted_lemonade_model_id,
        test_cloud_callers_do_not_render_local_switchboard,
        test_runtime_renderer_callers_keep_credentials_out_of_process_arguments,
    ):
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
