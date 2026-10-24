"""service_url reads config/service-urls.json and has no hardcoded fallback."""

import json

import pytest

import service_urls


@pytest.fixture
def map_file(tmp_path, monkeypatch):
    path = tmp_path / "service-urls.json"
    monkeypatch.setenv("ODS_SERVICE_URLS_FILE", str(path))
    service_urls._load.cache_clear()
    yield path
    service_urls._load.cache_clear()


def test_repo_map_resolves_core_services():
    assert service_urls.service_url("llama-server") == "http://llama-server:8080"
    assert service_urls.service_url("model-router") == "http://model-router:9099"


def test_reads_the_configured_map(map_file):
    map_file.write_text(json.dumps({"llama-server": "http://127.0.0.1:8080"}), encoding="utf-8")
    assert service_urls.service_url("llama-server") == "http://127.0.0.1:8080"


def test_unknown_service_raises(map_file):
    map_file.write_text(json.dumps({"llama-server": "http://llama-server:8080"}), encoding="utf-8")
    with pytest.raises(KeyError):
        service_urls.service_url("not-a-service")


def test_missing_map_raises(map_file):
    with pytest.raises(FileNotFoundError):
        service_urls.service_url("llama-server")


def test_default_path_is_install_config(monkeypatch, tmp_path):
    monkeypatch.delenv("ODS_SERVICE_URLS_FILE", raising=False)
    monkeypatch.setenv("ODS_INSTALL_DIR", str(tmp_path))
    assert service_urls.service_urls_path() == tmp_path / "config" / "service-urls.json"
