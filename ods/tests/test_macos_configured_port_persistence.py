"""Exercise the real Mac env generator; only Compose config may use Docker."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

ROOT = Path(os.environ.get("ODS_MAC_PORT_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
BASH = os.environ.get("ODS_TEST_BASH", "bash")
PORTS = {
    "DASHBOARD_PORT": "3001", "DASHBOARD_API_PORT": "3002", "WEBUI_PORT": "3000",
    "LITELLM_PORT": "4000", "PERPLEXICA_PORT": "3004", "N8N_PORT": "5678",
}
CUSTOM = {key: str(13000 + index) for index, key in enumerate(PORTS)}
SCRIPT = r'''
set -euo pipefail
source "$SOURCE_ROOT/installers/macos/lib/constants.sh"
source "$SOURCE_ROOT/installers/macos/lib/tier-map.sh"
source "$SOURCE_ROOT/installers/macos/lib/env-generator.sh"
calculate_llama_cpu_budget() { printf '4 1 8\n'; }
detect_device_name() { printf 'fixture\n'; }
detect_timezone() { printf 'UTC\n'; }
SYSTEM_RAM_GB=64; DOCKER_BACKEND=docker-desktop
resolve_tier_config 1
generate_ods_env "$FIXTURE_INSTALL" 1 "$FORCE_GENERATION"
'''


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if key not in (*PORTS, "N8N_WEBHOOK_URL", "COMPOSE_FILE", "COMPOSE_ENV_FILES",
                           "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_DISABLE_ENV_FILE")}


def generate(tmp_path, *, saved=None, overrides=None, force=False):
    install = tmp_path / "install"
    (install / "config/searxng").mkdir(parents=True, exist_ok=True)
    env_file = install / ".env"
    if saved is not None:
        env_file.write_bytes(saved.encode())
    env = {**clean_environment(), "SOURCE_ROOT": str(ROOT), "FIXTURE_INSTALL": str(install),
           "ODS_INSTALL_DIR": str(install), "ODS_HOME": str(install), "HOME": str(tmp_path),
           "FORCE_GENERATION": str(force).lower(), **(overrides or {})}
    result = subprocess.run([BASH, "-c", SCRIPT], env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=30)
    return result, env_file


def saved_values(env_file):
    return {key: shlex.split(value, comments=True)[0] if value else ""
            for line in env_file.read_text().splitlines()
            if line and not line.startswith("#") and "=" in line
            for key, value in [line.split("=", 1)]}


def assert_ports(env_file, expected):
    values = saved_values(env_file)
    assert {key: values.get(key) for key in PORTS} == expected
    return values


def test_fresh_generation_persists_defaults(tmp_path):
    result, env_file = generate(tmp_path)
    assert result.returncode == 0, result.stderr
    values = assert_ports(env_file, PORTS)
    assert values["N8N_WEBHOOK_URL"] == "http://localhost:5678"


def test_fresh_generation_persists_all_process_ports(tmp_path):
    result, env_file = generate(tmp_path, overrides=CUSTOM)
    assert result.returncode == 0, result.stderr
    values = assert_ports(env_file, CUSTOM)
    assert values["N8N_WEBHOOK_URL"] == "http://localhost:" + CUSTOM["N8N_PORT"]


@pytest.mark.parametrize("force", [False, True])
def test_saved_quoted_ports_survive_retained_and_forced_rerun(tmp_path, force):
    saved = "\r\n".join(f'{key}="{value}" # owner' for key, value in CUSTOM.items())
    result, env_file = generate(tmp_path, saved=saved, force=force)
    assert result.returncode == 0, result.stderr
    assert_ports(env_file, CUSTOM)


@pytest.mark.parametrize("force", [False, True])
def test_explicit_process_wins_saved_and_custom_webhook_is_preserved(tmp_path, force):
    saved = "".join(f"{key}={value}\n" for key, value in PORTS.items())
    saved += "N8N_WEBHOOK_URL='https://workflow.owner.test/hook?one=two' # owner\n"
    result, env_file = generate(tmp_path, saved=saved, overrides=CUSTOM, force=force)
    assert result.returncode == 0, result.stderr
    values = assert_ports(env_file, CUSTOM)
    assert values["N8N_WEBHOOK_URL"] == "https://workflow.owner.test/hook?one=two"


def test_independent_rerun_keeps_saved_ports_without_process_overrides(tmp_path):
    first, env_file = generate(tmp_path, overrides=CUSTOM)
    assert first.returncode == 0, first.stderr
    inode = env_file.stat().st_ino
    key_before = saved_values(env_file)["DASHBOARD_API_KEY"]
    second, env_file = generate(tmp_path)
    assert second.returncode == 0, second.stderr
    values = assert_ports(env_file, CUSTOM)
    assert env_file.stat().st_ino == inode
    assert values["DASHBOARD_API_KEY"] == key_before
    assert values["N8N_WEBHOOK_URL"] == "http://localhost:" + CUSTOM["N8N_PORT"]


@pytest.mark.parametrize("key", PORTS)
@pytest.mark.parametrize("invalid", ["", "65536"])
@pytest.mark.parametrize("mode", ["fresh", "retained", "force"])
def test_invalid_process_setting_refuses_before_any_env_write(tmp_path, key, invalid, mode):
    saved = None if mode == "fresh" else "OWNER_SENTINEL=unchanged\n" + "".join(
        f"{name}={value}\n" for name, value in PORTS.items())
    result, env_file = generate(tmp_path, saved=saved, overrides={key: invalid}, force=mode == "force")
    assert result.returncode != 0
    assert key + " must be a port from 1 to 65535" in result.stderr
    assert not env_file.exists() if saved is None else env_file.read_bytes() == saved.encode()


@pytest.mark.parametrize("invalid", ["", "0", "-1", "03", "1.5", "$(touch unexpected)"])
@pytest.mark.parametrize("force", [False, True])
def test_invalid_saved_setting_refuses_before_any_env_write(tmp_path, invalid, force):
    saved = "OWNER_SENTINEL=unchanged\nDASHBOARD_API_PORT=" + invalid + "\n"
    result, env_file = generate(tmp_path, saved=saved, force=force)
    assert result.returncode != 0
    assert "DASHBOARD_API_PORT must be a port from 1 to 65535" in result.stderr
    assert env_file.read_bytes() == saved.encode()
    assert not (tmp_path / "unexpected").exists()


@pytest.mark.parametrize("value", ["1", "65535"])
def test_valid_port_boundaries_are_saved(tmp_path, value):
    result, env_file = generate(tmp_path, overrides={"DASHBOARD_API_PORT": value})
    assert result.returncode == 0, result.stderr
    assert saved_values(env_file)["DASHBOARD_API_PORT"] == value


def test_saved_ports_match_native_readiness_and_real_compose_without_overrides(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose config parser unavailable")
    result, env_file = generate(tmp_path, overrides=CUSTOM)
    assert result.returncode == 0, result.stderr
    values = saved_values(env_file)
    spec = importlib.util.spec_from_file_location(
        "mac_port_native_readiness", ROOT / "installers/macos/lib/pixel-native-readiness.py")
    readiness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(readiness)
    assert readiness.port(values, "DASHBOARD_PORT", 3001) == int(CUSTOM["DASHBOARD_PORT"])
    assert readiness.port(values, "DASHBOARD_API_PORT", 3002) == int(CUSTOM["DASHBOARD_API_PORT"])
    # Render the shipped recipe, in a separate process with no port overrides.
    command = ["docker", "compose", "--env-file", str(env_file),
               "-f", str(ROOT / "docker-compose.base.yml"), "config", "--format", "json"]
    rendered = subprocess.run(command, env=clean_environment(), capture_output=True, text=True, timeout=30)
    assert rendered.returncode == 0, rendered.stderr
    services = json.loads(rendered.stdout)["services"]
    for service, key in (("dashboard", "DASHBOARD_PORT"), ("dashboard-api", "DASHBOARD_API_PORT"),
                         ("open-webui", "WEBUI_PORT")):
        assert services[service]["ports"][0]["published"] == CUSTOM[key]
    # Exercise the public status port resolver against that same recipe.
    script = 'set -euo pipefail\nsource "$SOURCE_ROOT/installers/macos/lib/service-ports.sh"\n'
    script += ('macos_load_service_ports "$FIXTURE_INSTALL" "dashboard dashboard-api open-webui" '
               '--env-file "$FIXTURE_INSTALL/.env" -f "$SOURCE_ROOT/docker-compose.base.yml"\n')
    script += 'macos_service_port dashboard; printf "\\n"; macos_service_port dashboard-api\n'
    resolved = subprocess.run([BASH, "-c", script], env={**clean_environment(),
        "SOURCE_ROOT": str(ROOT), "FIXTURE_INSTALL": str(env_file.parent)},
        capture_output=True, text=True, timeout=30)
    assert resolved.returncode == 0, resolved.stderr
    assert resolved.stdout.splitlines() == [CUSTOM["DASHBOARD_PORT"], CUSTOM["DASHBOARD_API_PORT"]]
