from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml


ROOT = pathlib.Path(__file__).resolve().parents[1]


def _litellm_compose_command() -> str:
    compose = yaml.safe_load((ROOT / "extensions/services/litellm/compose.yaml").read_text(encoding="utf-8"))
    return compose["services"]["litellm"]["command"][0]


def _load_openrouter_litellm_model():
    command = _litellm_compose_command()
    start = command.index("def openrouter_litellm_model")
    end = command.index("\n\nwith open('/app/config.yaml')")
    namespace: dict[str, object] = {}
    exec(command[start:end], namespace)
    return namespace["openrouter_litellm_model"]


def _shell_function(source: str, name: str) -> str:
    match = re.search(rf"(?ms)^(?P<indent>[ \t]*){re.escape(name)}\(\) \{{.*?^\1\}}", source)
    assert match, f"{name}() not found"
    return match.group(0)


def _run_bash(script: str) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash is not available in this environment")
    probe = subprocess.run([bash, "-lc", "printf ok"], capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip("bash is installed but not usable in this environment")
    return subprocess.run([bash, "-lc", script], check=True, capture_output=True, text=True)


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell is not available in this environment")
    args = [powershell, "-NoProfile"]
    if pathlib.Path(powershell).name.lower() == "powershell.exe":
        args.extend(["-ExecutionPolicy", "Bypass"])
    args.extend(["-Command", script])
    return subprocess.run(args, check=True, capture_output=True, text=True)


def _compose_env_file(tmp_path: pathlib.Path) -> pathlib.Path:
    env_file = tmp_path / "compose.env"
    env_file.write_text(
        "\n".join(
            [
                "WEBUI_SECRET=sk-test-webui-secret",
                "DASHBOARD_API_KEY=sk-test-dashboard",
                "ODS_AGENT_KEY=sk-test-agent",
                "ODS_SESSION_SECRET=sk-test-session",
                "SHIELD_API_KEY=sk-test-shield",
                "N8N_PASS=test-password",
                "LITELLM_KEY=sk-test-litellm",
                "LIVEKIT_API_KEY=lk-test",
                "LIVEKIT_API_SECRET=lk-secret",
                "OPENCLAW_TOKEN=oc-token",
                "QDRANT_API_KEY=qdrant-key",
                "TOKEN_SPY_API_KEY=token-key",
                "SEARXNG_SECRET=searxng-secret",
                "ODS_MODE=cloud",
                "LLM_API_URL=http://litellm:4000",
                "BIND_ADDRESS=127.0.0.1",
            ]
        ),
        encoding="utf-8",
    )
    return env_file


def _render_compose(tmp_path: pathlib.Path, files: list[str], profiles: list[str] | None = None) -> dict:
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("docker is not available in this environment")
    env_file = _compose_env_file(tmp_path)
    args = [docker, "compose", "--env-file", str(env_file)]
    for file in files:
        args.extend(["-f", file])
    for profile in profiles or []:
        args.extend(["--profile", profile])
    args.extend(["config", "--format", "json"])
    proc = subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def _cloud_aliases() -> set[str]:
    cfg = yaml.safe_load((ROOT / "config/litellm/cloud.yaml").read_text(encoding="utf-8"))
    aliases = {entry["model_name"] for entry in cfg["model_list"]}
    aliases.add("openrouter")
    return aliases


def test_cloud_litellm_config_exposes_openrouter_presets() -> None:
    cfg = yaml.safe_load((ROOT / "config/litellm/cloud.yaml").read_text(encoding="utf-8"))
    by_name = {entry["model_name"]: entry["litellm_params"] for entry in cfg["model_list"]}

    assert by_name["openrouter-auto"]["model"] == "openrouter/openrouter/auto"
    assert by_name["openrouter-auto"]["api_key"] == "os.environ/OPENROUTER_API_KEY"
    assert by_name["openrouter-free"]["model"] == "openrouter/openrouter/free"
    assert by_name["openrouter-free"]["api_key"] == "os.environ/OPENROUTER_API_KEY"


def test_cloud_alias_allowlists_match_litellm_cloud_config() -> None:
    aliases = _cloud_aliases()
    linux = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")
    macos = (ROOT / "installers/macos/lib/env-generator.sh").read_text(encoding="utf-8")
    windows = (ROOT / "installers/windows/lib/env-generator.ps1").read_text(encoding="utf-8")

    for alias in aliases:
        assert alias in linux
        assert alias in macos
        assert f'"{alias}"' in windows


def test_dynamic_openrouter_alias_renders_litellm_provider_strings() -> None:
    helper = _load_openrouter_litellm_model()

    assert helper(None) == "openrouter/openrouter/auto"
    assert helper("") == "openrouter/openrouter/auto"
    assert helper("openrouter/auto") == "openrouter/openrouter/auto"
    assert helper("openrouter/free") == "openrouter/openrouter/free"
    assert helper("qwen/qwen3-235b-a22b:free") == "openrouter/qwen/qwen3-235b-a22b:free"
    assert helper("openrouter/qwen/qwen3-235b-a22b:free") == "openrouter/qwen/qwen3-235b-a22b:free"


def test_litellm_compose_forwards_openrouter_runtime_env() -> None:
    compose = yaml.safe_load((ROOT / "extensions/services/litellm/compose.yaml").read_text(encoding="utf-8"))
    env = set(compose["services"]["litellm"]["environment"])
    command = _litellm_compose_command()

    assert "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}" in env
    assert "OPENROUTER_MODEL=${OPENROUTER_MODEL:-openrouter/auto}" in env
    assert "OR_SITE_URL=${OPENROUTER_SITE_URL:-}" in env
    assert "OR_APP_NAME=${OPENROUTER_APP_NAME:-ODS}" in env
    assert "OPENROUTER_MODEL_NAME" in command
    assert "openrouter_litellm_model" in command
    assert "exec litellm --config /tmp/config.yaml --port 4000" in command


def test_cloud_overlay_authenticates_open_webui_to_litellm() -> None:
    cloud = yaml.safe_load((ROOT / "docker-compose.cloud.yml").read_text(encoding="utf-8"))

    webui_env = cloud["services"]["open-webui"]["environment"]
    dashboard_env = set(cloud["services"]["dashboard-api"]["environment"])

    assert webui_env["OPENAI_API_KEY"] == "${LITELLM_KEY:-${OPENAI_API_KEY:-}}"
    assert "ODS_TALK_VISION_URL=${ODS_TALK_VISION_URL:-http://litellm:4000/v1}" in dashboard_env
    assert "ODS_TALK_VISION_KEY=${ODS_TALK_VISION_KEY:-${LITELLM_KEY:-}}" in dashboard_env
    assert "ODS_TALK_VISION_MODEL=${ODS_TALK_VISION_MODEL:-${LLM_MODEL:-default}}" in dashboard_env


def test_linux_cloud_env_routes_backend_through_litellm() -> None:
    phase06 = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")

    assert 'elif [[ "$ODS_MODE_VALUE" == "cloud" ]]; then echo "litellm"' in phase06
    assert "_phase06_select_cloud_llm_model" in phase06
    assert "OPENROUTER_API_KEY=$(_env_get OPENROUTER_API_KEY" in phase06
    assert "OPENROUTER_MODEL=${OPENROUTER_MODEL:-openrouter/auto}" in phase06
    assert "printf 'openrouter\\n'" in phase06
    assert "printf 'default\\n'" in phase06


def test_macos_cloud_env_routes_hermes_and_services_through_litellm() -> None:
    env_generator = (ROOT / "installers/macos/lib/env-generator.sh").read_text(encoding="utf-8")
    compose = yaml.safe_load((ROOT / "installers/macos/docker-compose.macos.yml").read_text(encoding="utf-8"))
    installer = (ROOT / "installers/macos/install-macos.sh").read_text(encoding="utf-8")

    assert 'ods_mode_value="cloud"' in env_generator
    assert 'llm_backend_value="litellm"' in env_generator
    assert 'llm_api_url="http://litellm:4000"' in env_generator
    assert 'hermes_llm_base_url="http://litellm:4000/v1"' in env_generator
    assert "select_cloud_llm_model" in env_generator
    assert 'upsert_env_value "$env_path" "ODS_MODE" "cloud"' in env_generator
    assert 'upsert_env_value "$env_path" "LLM_BACKEND" "litellm"' in env_generator
    assert 'upsert_env_value "$env_path" "LLM_MODEL" "$_cloud_llm_model"' in env_generator
    assert 'upsert_env_value "$env_path" "HERMES_LLM_BASE_URL" "http://litellm:4000/v1"' in env_generator
    assert "printf 'openrouter\\n'" in env_generator
    assert "printf 'default\\n'" in env_generator
    assert "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-}" in env_generator
    assert "OLLAMA_URL=${LLM_API_URL:-http://host.docker.internal:8080}" in compose["services"]["dashboard-api"]["environment"]
    assert compose["services"]["open-webui"]["environment"]["OPENAI_API_BASE_URL"] == "${LLM_API_URL:-http://host.docker.internal:8080}/v1"
    assert "docker-compose.cloud.yml" in installer
    assert "docker-compose.macos.cloud.yml" in installer
    assert '"--profile" "local-inference"' in installer


def test_macos_local_open_webui_waits_for_native_llama_ready(tmp_path: pathlib.Path) -> None:
    rendered = _render_compose(
        tmp_path,
        ["docker-compose.base.yml", "installers/macos/docker-compose.macos.yml"],
        profiles=["local-inference"],
    )

    services = rendered["services"]
    assert "llama-server-ready" in services
    assert services["open-webui"]["depends_on"]["llama-server-ready"]["condition"] == "service_healthy"


def test_macos_cloud_open_webui_does_not_wait_for_native_llama_ready(tmp_path: pathlib.Path) -> None:
    rendered = _render_compose(
        tmp_path,
        [
            "docker-compose.base.yml",
            "docker-compose.cloud.yml",
            "installers/macos/docker-compose.macos.cloud.yml",
        ],
    )

    services = rendered["services"]
    assert "llama-server-ready" not in services
    assert "depends_on" not in services["open-webui"]


def test_windows_env_generator_preserves_openrouter_values() -> None:
    env_generator = (ROOT / "installers/windows/lib/env-generator.ps1").read_text(encoding="utf-8")

    assert '$anthropicApiKey = Get-EnvOrNew "ANTHROPIC_API_KEY" $env:ANTHROPIC_API_KEY' in env_generator
    assert '$openrouterApiKey = Get-EnvOrNew "OPENROUTER_API_KEY" $env:OPENROUTER_API_KEY' in env_generator
    assert '$openrouterModel = Get-EnvOrNew "OPENROUTER_MODEL" $(if ($env:OPENROUTER_MODEL) { $env:OPENROUTER_MODEL } else { "openrouter/auto" })' in env_generator
    assert '$openrouterAppName = Get-EnvOrNew "OPENROUTER_APP_NAME" $(if ($env:OPENROUTER_APP_NAME) { $env:OPENROUTER_APP_NAME } else { "ODS" })' in env_generator
    assert "Select-ODSCloudLlmModel" in env_generator
    assert 'return "openrouter"' in env_generator
    assert 'return "default"' in env_generator


def test_linux_cloud_model_selection_does_not_preserve_local_model() -> None:
    phase06 = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")
    script = "\n".join(
        [
            _shell_function(phase06, "_phase06_is_cloud_litellm_alias"),
            _shell_function(phase06, "_phase06_select_cloud_llm_model"),
            "ANTHROPIC_API_KEY= OPENAI_API_KEY= MINIMAX_API_KEY= OPENROUTER_API_KEY=sk-or",
            '[[ "$(_phase06_select_cloud_llm_model qwen3.5-9b local)" == "openrouter" ]]',
            '[[ "$(_phase06_select_cloud_llm_model gpt4o local)" == "gpt4o" ]]',
            '[[ "$(_phase06_select_cloud_llm_model openrouter local)" == "openrouter" ]]',
            '[[ "$(_phase06_select_cloud_llm_model custom-cloud-route cloud)" == "custom-cloud-route" ]]',
        ]
    )

    _run_bash(script)


def test_linux_cloud_rerun_rewrites_local_llm_runtime_routes() -> None:
    phase06 = (ROOT / "installers/phases/06-directories.sh").read_text(encoding="utf-8")
    script = "\n".join(
        [
            "set -euo pipefail",
            _shell_function(phase06, "_env_get"),
            _shell_function(phase06, "_phase06_is_cloud_litellm_alias"),
            _shell_function(phase06, "_phase06_select_cloud_llm_model"),
            _shell_function(phase06, "_phase06_select_llm_api_url"),
            _shell_function(phase06, "_phase06_select_hermes_llm_base_url"),
            _shell_function(phase06, "_phase06_select_hermes_llm_api_key"),
            '_env_existing="$(mktemp)"',
            'trap \'rm -f "$_env_existing"\' EXIT',
            "cat > \"$_env_existing\" <<'EOF'\n"
            "ODS_MODE=local\n"
            "LLM_API_URL=http://llama-server:8080\n"
            "HERMES_LLM_BASE_URL=http://llama-server:8080/v1\n"
            "HERMES_LLM_API_KEY=sk-ods-hermes-local\n"
            "LLM_MODEL=qwen3.5-9b\n"
            "OPENROUTER_API_KEY=sk-or-test\n"
            "EOF",
            "ANTHROPIC_API_KEY= OPENAI_API_KEY= MINIMAX_API_KEY=",
            'OPENROUTER_API_KEY="$(_env_get OPENROUTER_API_KEY "")"',
            "LITELLM_KEY=sk-ods-litellm-test",
            "ODS_MODE_VALUE=cloud",
            'LLM_API_URL_VALUE="$(_phase06_select_llm_api_url "$ODS_MODE_VALUE" "http://litellm:4000" "$(_env_get LLM_API_URL "")")"',
            'HERMES_LLM_BASE_URL_VALUE="$(_phase06_select_hermes_llm_base_url "$ODS_MODE_VALUE" "http://litellm:4000/v1" "$(_env_get HERMES_LLM_BASE_URL "")")"',
            'HERMES_LLM_API_KEY_VALUE="$(_phase06_select_hermes_llm_api_key "$ODS_MODE_VALUE" "$LITELLM_KEY" "$(_env_get HERMES_LLM_API_KEY "")")"',
            'LLM_MODEL="$(_phase06_select_cloud_llm_model "$(_env_get LLM_MODEL "")" "$(_env_get ODS_MODE "")")"',
            'LLM_BACKEND="$(if [[ "$ODS_MODE_VALUE" == "lemonade" ]]; then echo "lemonade"; elif [[ "$ODS_MODE_VALUE" == "cloud" ]]; then echo "litellm"; else echo "llama-server"; fi)"',
            '[[ "$LLM_API_URL_VALUE" == "http://litellm:4000" ]]',
            '[[ "$HERMES_LLM_BASE_URL_VALUE" == "http://litellm:4000/v1" ]]',
            '[[ "$HERMES_LLM_API_KEY_VALUE" == "sk-ods-litellm-test" ]]',
            '[[ "$LLM_MODEL" == "openrouter" ]]',
            '[[ "$LLM_BACKEND" == "litellm" ]]',
        ]
    )

    _run_bash(script)


def test_macos_cloud_model_selection_does_not_preserve_local_model() -> None:
    env_generator = (ROOT / "installers/macos/lib/env-generator.sh").read_text(encoding="utf-8")
    script = "\n".join(
        [
            _shell_function(env_generator, "is_cloud_litellm_alias"),
            _shell_function(env_generator, "select_cloud_llm_model"),
            "ANTHROPIC_API_KEY= OPENAI_API_KEY= MINIMAX_API_KEY= OPENROUTER_API_KEY=sk-or",
            '[[ "$(select_cloud_llm_model qwen3.5-9b local)" == "openrouter" ]]',
            '[[ "$(select_cloud_llm_model gpt4o local)" == "gpt4o" ]]',
            '[[ "$(select_cloud_llm_model openrouter local)" == "openrouter" ]]',
            '[[ "$(select_cloud_llm_model custom-cloud-route cloud)" == "custom-cloud-route" ]]',
        ]
    )

    _run_bash(script)


def test_windows_cloud_model_selection_does_not_preserve_local_model() -> None:
    generator = ROOT / "installers/windows/lib/env-generator.ps1"
    generator_path = str(generator).replace("'", "''")
    script = f"""
. '{generator_path}'
$model = Select-ODSCloudLlmModel -CandidateModel 'qwen3.5-9b' -PreviousOdsMode 'local' -OpenRouterApiKey 'sk-or-test' -AnthropicApiKey '' -OpenAiApiKey '' -MiniMaxApiKey ''
if ($model -ne 'openrouter') {{ throw "Expected openrouter, got $model" }}
"""

    _run_powershell(script)


def test_windows_cloud_model_selection_preserves_cloud_alias() -> None:
    generator = ROOT / "installers/windows/lib/env-generator.ps1"
    generator_path = str(generator).replace("'", "''")
    script = f"""
. '{generator_path}'
$cloudAlias = Select-ODSCloudLlmModel -CandidateModel 'gpt4o' -PreviousOdsMode 'local' -OpenRouterApiKey 'sk-or-test' -AnthropicApiKey '' -OpenAiApiKey '' -MiniMaxApiKey ''
if ($cloudAlias -ne 'gpt4o') {{ throw "Expected gpt4o, got $cloudAlias" }}
$customCloudAlias = Select-ODSCloudLlmModel -CandidateModel 'custom-cloud-route' -PreviousOdsMode 'cloud' -OpenRouterApiKey 'sk-or-test' -AnthropicApiKey '' -OpenAiApiKey '' -MiniMaxApiKey ''
if ($customCloudAlias -ne 'custom-cloud-route') {{ throw "Expected custom-cloud-route, got $customCloudAlias" }}
"""

    _run_powershell(script)
