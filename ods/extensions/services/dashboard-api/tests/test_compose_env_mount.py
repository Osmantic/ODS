from pathlib import Path

import yaml


def test_dashboard_api_mounts_env_parent_instead_of_replaceable_file():
    compose = Path(__file__).resolve().parents[4] / "docker-compose.base.yml"
    service = yaml.safe_load(compose.read_text(encoding="utf-8"))["services"]["dashboard-api"]
    volumes = service["volumes"]

    assert "./:/ods:ro,z" in volumes
    assert not any(volume.startswith("./.env:/ods/.env:") for volume in volumes)
