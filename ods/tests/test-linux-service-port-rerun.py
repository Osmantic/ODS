"""Execute phase 06's actual port producer, serializer and service registry."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PHASE = (ROOT / "installers/phases/06-directories.sh").read_text()
PORTS = {
    "DASHBOARD_PORT": ("dashboard", 3001), "DASHBOARD_REMOTE_PORT": (None, 3011),
    "WEBUI_PORT": ("open-webui", 3000), "PERPLEXICA_PORT": ("perplexica", 3004),
    "TTS_PORT": ("tts", 8880), "N8N_PORT": ("n8n", 5678),
    "QDRANT_PORT": ("qdrant", 6333), "QDRANT_GRPC_PORT": (None, 6334),
    "EMBEDDINGS_PORT": ("embeddings", 8090), "LITELLM_PORT": ("litellm", 4000),
    "HERMES_PROXY_PORT": ("hermes-proxy", 9120),
}


def reader(name):
    start = PHASE.index(f"    {name}() {{")
    end = PHASE.index("\n    }", start) + len("\n    }")
    return PHASE[start:end]


def run(tmp_path, saved, overrides=None):
    existing = tmp_path / "existing.env"
    existing.write_text(saved)
    output = tmp_path / "output.env"
    block = ""
    marker = "    # Resolve every fixed service-port assignment"
    if marker in PHASE:
        block = PHASE[PHASE.index(marker):PHASE.index("    # The local llama-server port", PHASE.index(marker))]
    template = PHASE.split('cat > "$INSTALL_DIR/.env" << ENV_EOF', 1)[1].split("\nENV_EOF", 1)[0]
    lines = [line for line in template.splitlines()
             if line.split("=", 1)[0] in {*PORTS, "N8N_WEBHOOK_URL"}]
    payload = f'''set -e
source "$1/lib/safe-env.sh"
source "$1/lib/dotenv-quote.sh"
SCRIPT_DIR="$1"
source "$1/lib/service-registry.sh"
sr_load
_env_existing="$2"
error() {{ printf '%s\\n' "$*" >&2; return 1; }}
{reader('_env_get')}
{reader('_env_get_explicit_first')}
{block}
cat > "$3" << ENV_EOF
{chr(10).join(lines)}
ENV_EOF
for key in "${{!SERVICE_PORTS[@]}}"; do printf '%s=%s\\n' "$key" "${{SERVICE_PORTS[$key]}}"; done
'''
    environment = {key: value for key, value in os.environ.items()
                   if key not in {*PORTS, "N8N_WEBHOOK_URL"}}
    environment.update(overrides or {})
    result = subprocess.run(["bash", "-c", payload, "port-rerun", str(ROOT), str(existing), str(output)],
                            env=environment, text=True, capture_output=True)
    return result, output


def test_saved_ports_survive_serialization_and_reach_registry(tmp_path):
    saved = {key: str(default + 100) for key, (_, default) in PORTS.items()}
    result, output = run(tmp_path, "".join(f'{key}="{value}"\n' for key, value in saved.items()))
    assert result.returncode == 0, result.stderr
    generated = dict(line.split("=", 1) for line in output.read_text().splitlines())
    registry = dict(line.split("=", 1) for line in result.stdout.splitlines())
    for key, value in saved.items():
        assert generated[key] == value
        service = PORTS[key][0]
        if service:
            assert registry[service] == value
    assert generated["N8N_WEBHOOK_URL"] == f'http://localhost:{saved["N8N_PORT"]}'

    # Render the actual n8n port mapping without starting a container.
    import json
    import shutil
    if shutil.which("docker"):
        with output.open("a") as handle:
            handle.write("N8N_USER=owner@example.net\nN8N_PASS=test-only-password\n")
        rendered = subprocess.run(["docker", "compose", "--env-file", str(output),
            "-f", str(ROOT / "extensions/services/n8n/compose.yaml"), "config", "--format", "json"],
            text=True, capture_output=True)
        assert rendered.returncode == 0, rendered.stderr
        ports = json.loads(rendered.stdout)["services"]["n8n"]["ports"]
        assert ports[0]["published"] == saved["N8N_PORT"]
        assert ports[0]["target"] == 5678


def test_explicit_override_wins_and_missing_values_use_defaults(tmp_path):
    result, output = run(tmp_path, "N8N_PORT=5778\n", {"N8N_PORT": "5900"})
    assert result.returncode == 0, result.stderr
    generated = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert generated["N8N_PORT"] == "5900"
    assert generated["WEBUI_PORT"] == "3000"
    assert generated["N8N_WEBHOOK_URL"] == "http://localhost:5900"
    assert generated["DASHBOARD_PORT"] == "3001"
    assert generated["DASHBOARD_REMOTE_PORT"] == "3011"


def test_explicit_dashboard_ports_override_saved_values(tmp_path):
    result, output = run(tmp_path, "DASHBOARD_PORT=3301\nDASHBOARD_REMOTE_PORT=3311\n",
                         {"DASHBOARD_PORT": "3401", "DASHBOARD_REMOTE_PORT": "3411"})
    assert result.returncode == 0, result.stderr
    generated = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert generated["DASHBOARD_PORT"] == "3401"
    assert generated["DASHBOARD_REMOTE_PORT"] == "3411"
    assert "dashboard=3401" in result.stdout.splitlines()


def test_owner_webhook_url_survives_rerun(tmp_path):
    result, output = run(tmp_path, "N8N_PORT=5778\nN8N_WEBHOOK_URL=https://workflow.owner.test\n")
    assert result.returncode == 0, result.stderr
    assert "N8N_WEBHOOK_URL=https://workflow.owner.test" in output.read_text()


def test_saved_inline_comment_is_decoded_as_a_port(tmp_path):
    result, output = run(tmp_path, 'N8N_PORT="5878" # owner-selected port\n')
    assert result.returncode == 0, result.stderr
    assert "N8N_PORT=5878" in output.read_text()


def test_invalid_saved_or_explicit_port_refuses_before_serialization(tmp_path):
    for value in ("0", "65536", "-1", "3000; touch unwanted", "03"):
        result, output = run(tmp_path, "N8N_PORT=5778\n", {"N8N_PORT": value})
        assert result.returncode != 0
        assert not output.exists()
        assert (tmp_path / "existing.env").read_text() == "N8N_PORT=5778\n"


def test_failure_can_be_corrected_and_rerun(tmp_path):
    result, _ = run(tmp_path, "N8N_PORT=invalid\n")
    assert result.returncode != 0
    result, output = run(tmp_path, "N8N_PORT=5878\n")
    assert result.returncode == 0, result.stderr
    assert "N8N_PORT=5878" in output.read_text()


def test_summary_uses_resolved_webui_port(tmp_path):
    summary = (ROOT / "installers/phases/13-summary.sh").read_text()
    start = summary.index('    _summary_chat_url=""')
    end = summary.index('    _summary_lan_address=""', start)
    script = 'declare -A SERVICE_PORTS=([open-webui]=3100); ENABLE_OPEN_WEBUI=true\n'
    script += summary[start:end] + '\nprintf "%s" "$_summary_chat_url"\n'
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "http://localhost:3100"


def test_operator_diagnostics_honor_selected_environment_file(tmp_path):
    # Keep BASH_SOURCE self-location real while executing the diagnostics'
    # shipped initialization without starting its network probes.
    import shutil
    fake = tmp_path / "source"
    (fake / "scripts").mkdir(parents=True)
    (fake / "lib").symlink_to(ROOT / "lib", target_is_directory=True)
    shutil.copy(ROOT / "manifest.json", fake / "manifest.json")
    (fake / "extensions").symlink_to(ROOT / "extensions", target_is_directory=True)
    (fake / ".env").write_text("OLLAMA_PORT=11434\n")
    selected = tmp_path / "selected.env"
    selected.write_text("OLLAMA_PORT=12534\n")
    script = (ROOT / "scripts/ods-test.sh").read_text().split("# Colors", 1)[0]
    driver = fake / "scripts/diagnostic-init.sh"
    driver.write_text(script + '\nprintf "%s" "$LLM_URL"\n')
    result = subprocess.run(["bash", str(driver)], env={**os.environ, "ENV_FILE": str(selected)},
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "http://localhost:12534"
