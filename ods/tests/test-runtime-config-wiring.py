#!/usr/bin/env python3
"""Contract checks for runtime config renderer wiring."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def test_linux_installer_uses_renderer_with_fallback() -> None:
    text = read("installers/phases/06-directories.sh")
    assert "scripts/render-runtime-configs.py" in text
    assert "--surface litellm-lemonade" in text
    assert "LITELLM_EOF" in text
    assert "falling back to inline writer" in text


def test_bootstrap_upgrade_uses_renderer_with_fallback() -> None:
    text = read("scripts/bootstrap-upgrade.sh")
    assert "scripts/render-runtime-configs.py" in text
    assert "--surface litellm-lemonade" in text
    assert "LITELLM_UPGRADE_EOF" in text
    assert "falling back to inline writer" in text


def test_bootstrap_upgrade_promotes_lemonade_model_id() -> None:
    text = read("scripts/bootstrap-upgrade.sh")
    assert 'write_env_value LEMONADE_MODEL "$_promotion_lemonade_model_id"' in text
    assert 'lemonade_model_id_matches_gguf "$_loaded_model_id" "$FULL_GGUF_FILE"' in text
    assert 'resolve_live_lemonade_model_id "${OLLAMA_PORT:-8080}" "$FULL_GGUF_FILE"' in text
    assert 'json_has_id "$models_json" "$model_id"' in text


def test_host_agent_uses_renderer_with_fallback() -> None:
    text = read("bin/ods-host-agent.py")
    assert "def _render_runtime_config" in text
    assert "--surface" in text
    assert '"--lemonade-model-id"' in text
    assert "litellm-lemonade" in text
    assert "Runtime config renderer failed" in text
    assert "model_list:\\n" in text


def test_openclaw_receives_persisted_lemonade_model_id() -> None:
    compose = read("extensions/services/openclaw/compose.yaml")
    injector = read("config/openclaw/inject-token.js")
    assert "LEMONADE_MODEL=${LEMONADE_MODEL:-}" in compose
    assert "LEMONADE_MODEL ||" in injector
    assert "`extra.${GGUF_FILE}`" in injector


def main() -> int:
    for test in (
        test_linux_installer_uses_renderer_with_fallback,
        test_bootstrap_upgrade_uses_renderer_with_fallback,
        test_bootstrap_upgrade_promotes_lemonade_model_id,
        test_host_agent_uses_renderer_with_fallback,
        test_openclaw_receives_persisted_lemonade_model_id,
    ):
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
