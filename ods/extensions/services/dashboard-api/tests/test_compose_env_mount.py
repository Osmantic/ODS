from pathlib import Path

import yaml


def test_dashboard_api_mounts_env_parent_instead_of_replaceable_file():
    compose = Path(__file__).resolve().parents[4] / "docker-compose.base.yml"
    service = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]["dashboard-api"]
    volumes = service["volumes"]

    assert "./:/ods:ro,z" in volumes
    assert not any(volume.startswith("./.env:/ods/.env:") for volume in volumes)


def test_dashboard_api_receives_the_open_webui_switch_for_an_unreadable_env():
    # DGX Station: the install owner is UID 1001, the API runs as 1000, so the
    # 0600 .env is unreadable and only this copy says Open WebUI is switched off.
    compose = Path(__file__).resolve().parents[4] / "docker-compose.base.yml"
    service = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]["dashboard-api"]

    assert "ENABLE_OPEN_WEBUI=${ENABLE_OPEN_WEBUI:-true}" in service["environment"]


def test_dashboard_api_receives_the_offline_update_switch_for_an_unreadable_env():
    compose = Path(__file__).resolve().parents[4] / "docker-compose.base.yml"
    service = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]["dashboard-api"]

    assert "DISABLE_UPDATE_CHECK=${DISABLE_UPDATE_CHECK:-false}" in service["environment"]
