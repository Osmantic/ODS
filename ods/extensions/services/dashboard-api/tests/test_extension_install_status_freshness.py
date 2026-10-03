"""The catalog reports a fresh install as soon as its service answers.

Regression: the catalog read user extension manifests through a 30 s scan
cache. A catalog request made just before an install (the Extensions page
load) cached a scan without the new extension, so the catalog did not probe
it and reported ``installing`` until the cache expired. Fleet installs of
unrelated extensions all took ~27.5 s from Install to ``enabled``.
"""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import yaml

import user_extensions
from models import ServiceStatus


def _catalog_entry(ext_id):
    return {
        "id": ext_id, "name": ext_id, "description": ext_id, "category": "optional",
        "gpu_backends": ["all"], "compose_file": "compose.yaml", "depends_on": [],
        "port": 8080, "external_port_default": 8080, "health_endpoint": "/health",
        "env_vars": [], "tags": [], "features": [],
    }


@pytest.fixture
def catalog_env(monkeypatch, tmp_path):
    user_dir = tmp_path / "user"
    user_dir.mkdir()
    monkeypatch.setattr("routers.extensions.EXTENSION_CATALOG", [_catalog_entry("my-ext")])
    monkeypatch.setattr("routers.extensions.SERVICES", {})
    monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
    monkeypatch.setattr("routers.extensions.EXTENSIONS_LIBRARY_DIR", tmp_path / "lib")
    monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
    monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
    user_extensions._reset_cache()
    yield tmp_path, user_dir
    user_extensions._reset_cache()


def _install(tmp_path, user_dir):
    """Leave the files a completed dashboard install leaves behind."""
    ext_dir = user_dir / "my-ext"
    ext_dir.mkdir()
    (ext_dir / "compose.yaml").write_text("services:\n  my-ext:\n    image: test:latest\n")
    (ext_dir / "manifest.yaml").write_text(yaml.dump({
        "schema_version": "ods.services.v1",
        "service": {"id": "my-ext", "name": "my-ext", "port": 8080, "health": "/health"},
    }))
    progress_dir = tmp_path / "extension-progress"
    progress_dir.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    (progress_dir / "my-ext.json").write_text(json.dumps({
        "service_id": "my-ext", "status": "started", "phase_label": "Service started",
        "error": None, "started_at": now, "updated_at": now,
    }))


def _status(test_client, probe):
    with patch("helpers.get_all_services", new_callable=AsyncMock, return_value=[]), \
            patch("helpers.check_service_health", new=probe):
        resp = test_client.get("/api/extensions/catalog", headers=test_client.auth_headers)
    assert resp.status_code == 200
    return {ext["id"]: ext["status"] for ext in resp.json()["extensions"]}["my-ext"]


def _probe(status):
    async def probe(service_id, config, **kwargs):
        return ServiceStatus(id=service_id, name=service_id, port=config["port"],
                             external_port=config["port"], status=status)
    return AsyncMock(side_effect=probe)


def test_catalog_reports_enabled_once_new_install_answers(test_client, catalog_env):
    tmp_path, user_dir = catalog_env
    probe = _probe("healthy")

    # The Extensions page loads the catalog, and so scans, before Install.
    assert _status(test_client, probe) == "not_installed"

    _install(tmp_path, user_dir)

    # Well within the 30 s scan TTL: the new service is probed and answers.
    assert _status(test_client, probe) == "enabled"
    probed = [call.args[0] for call in probe.await_args_list]
    assert probed == ["my-ext"]


def test_new_install_stays_installing_until_it_answers(test_client, catalog_env):
    """Probing earlier must not relax what 'enabled' means."""
    tmp_path, user_dir = catalog_env
    assert _status(test_client, _probe("healthy")) == "not_installed"
    _install(tmp_path, user_dir)

    assert _status(test_client, _probe("down")) == "installing"
    assert _status(test_client, _probe("unhealthy")) == "installing"
    assert _status(test_client, _probe("healthy")) == "enabled"


def test_disabled_extension_is_not_probed_from_a_stale_scan(test_client, catalog_env):
    """A stopped container's cached address can stall the probe for its full
    timeout; a disabled extension must drop out of the scan immediately."""
    tmp_path, user_dir = catalog_env
    _install(tmp_path, user_dir)
    (tmp_path / "extension-progress" / "my-ext.json").unlink()
    assert _status(test_client, _probe("healthy")) == "enabled"

    ext_dir = user_dir / "my-ext"
    (ext_dir / "compose.yaml").rename(ext_dir / "compose.yaml.disabled")
    probe = _probe("healthy")
    assert _status(test_client, probe) == "disabled"
    assert probe.await_count == 0
