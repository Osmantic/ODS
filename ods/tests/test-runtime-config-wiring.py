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
    # AMD serves llama.cpp in the stack (litellm-local); the Windows Portal's
    # host-native llama-server has its own surface; Lemonade has none.
    assert "--surface litellm-local --output-root" in text
    assert "--surface litellm-local-native" in text
    assert "litellm-lemonade" not in text
    assert "LITELLM_EOF" not in text
    assert "falling back to inline writer" not in text


def test_bootstrap_upgrade_writes_no_lemonade_route() -> None:
    text = read("scripts/bootstrap-upgrade.sh")
    # llama-server serves the GGUF it loaded, so a full-model swap re-renders
    # nothing; the Lemonade route and its inline writers are gone.
    assert "litellm-lemonade" not in text
    assert "LITELLM_UPGRADE_EOF" not in text
    assert "LITELLM_WINDOWS_LEMONADE_EOF" not in text
    assert "falling back to inline writer" not in text


def test_bootstrap_upgrade_tracks_no_lemonade_model_id() -> None:
    text = read("scripts/bootstrap-upgrade.sh")
    for retired in ("_promotion_lemonade_model_id", "lemonade_model_id_matches_gguf",
                    "resolve_live_lemonade_model_id", "json_has_id"):
        assert retired not in text, retired


def test_host_agent_uses_renderer_as_sole_writer() -> None:
    text = read("bin/ods-host-agent.py")
    assert "def _render_runtime_config" in text
    assert "--surface" in text
    assert "Runtime config renderer failed" in text
    # Round F: one llama-server runtime family. A host-native key reaches the
    # renderer by its env var name only; no Lemonade surface or id remains.
    assert '"--llm-api-key-env"' in text
    assert "--lemonade-model-id" not in text
    assert "litellm-lemonade" not in text
    native_writer = text.split("def _write_host_native_litellm_config(", 1)[1].split("\ndef ", 1)[0]
    assert '"litellm-local-native"' in native_writer
    assert "model_list:" not in native_writer


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
        read("bin/ods-host-agent.py"),
    ]
    assert all('"--litellm-key"' not in text for text in callers)
    # A caller that runs the renderer passes the key through the environment;
    # the Windows env generator writes its host-native config itself.
    assert all("ODS_RENDER_LITELLM_KEY" in text for text in callers if "render-runtime-configs.py" in text)
    # bootstrap-upgrade.sh no longer renders runtime configs at all.
    assert "render-runtime-configs.py" not in read("scripts/bootstrap-upgrade.sh")


def test_installer_names_the_native_key_for_litellm_and_the_router() -> None:
    # A host-native llama-server answers 401 without its key. The installer's
    # renders must name LLAMA_SERVER_API_KEY as the host agent's do, or LiteLLM
    # and model-router send no key until the first model activation.
    phase06 = read("installers/phases/06-directories.sh")
    native = phase06[phase06.index("--surface litellm-local-native"):]
    native = native[:native.index("--write")]
    assert "--llm-api-key-env LLAMA_SERVER_API_KEY" in native
    router = phase06[phase06.index("_router_common_args=("):phase06.index("_router_surfaces=(model-router-endpoints)")]
    assert '[[ "$NATIVE_LLM_ACTIVE" != "true" ]] || _router_common_args+=(--llm-api-key-env LLAMA_SERVER_API_KEY)' in router


def main() -> int:
    for test in (
        test_linux_installer_uses_renderer_as_sole_writer,
        test_bootstrap_upgrade_writes_no_lemonade_route,
        test_bootstrap_upgrade_tracks_no_lemonade_model_id,
        test_host_agent_uses_renderer_as_sole_writer,
        test_cloud_callers_do_not_render_local_switchboard,
        test_runtime_renderer_callers_keep_credentials_out_of_process_arguments,
        test_installer_names_the_native_key_for_litellm_and_the_router,
    ):
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
