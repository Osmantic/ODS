"""Adding a feature's service brings its companions; disabling it stops them.

Hermes Agent is reachable only through hermes-proxy (its manifest's feature
lists both). Before companions, Add enabled Hermes alone and the owner had
nothing to open (fleet run 2026-10-04: ods-hermes healthy, :9120 closed).
"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from routers import extensions

SOURCE = Path(__file__).resolve().parents[2]
COMPOSE = {
    "searxng": "services:\n  searxng:\n    image: alpine:3.22\n",
    "hermes": "services:\n  hermes:\n    image: alpine:3.22\n",
    "hermes-proxy": "services:\n  hermes-proxy:\n    image: alpine:3.22\n    depends_on:\n      - hermes\n",
}


@pytest.fixture
def installation(monkeypatch, tmp_path):
    bundled = tmp_path / "bundled"
    for name, compose in COMPOSE.items():
        directory = bundled / name
        directory.mkdir(parents=True)
        (directory / "manifest.yaml").write_bytes((SOURCE / name / "manifest.yaml").read_bytes())
        (directory / "compose.yaml.disabled").write_text(compose)

    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", bundled)
    monkeypatch.setattr(extensions, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(extensions, "LIBRARY_MANAGEABLE_BUILTINS",
                        frozenset({"searxng", "hermes", "hermes-proxy"}))
    start, hook = Mock(return_value=True), Mock(return_value=True)
    monkeypatch.setattr(extensions, "_call_agent", start)
    monkeypatch.setattr(extensions, "_call_agent_hook", hook)
    selections = []

    def select(action, service_ids, expected_sha256=None):
        selections.append((action, list(service_ids)))
        if action == "enable":
            assert set(expected_sha256) == set(service_ids)
        for name in service_ids:
            directory = bundled / name
            if action == "enable" and (directory / "compose.yaml.disabled").exists():
                (directory / "compose.yaml.disabled").rename(directory / "compose.yaml")
            elif action == "disable":
                (directory / "compose.yaml").rename(directory / "compose.yaml.disabled")
        return {"action": "enabled" if action == "enable" else "disabled", "service_ids": service_ids}

    monkeypatch.setattr(extensions, "_select_extensions_on_host", select)
    monkeypatch.setattr(extensions, "request_agent_json",
                        lambda *args, **kwargs: pytest.fail("unexpected host-agent transport"))
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", Mock())
    return bundled, start, selections


def _select(bundled, *names):
    for name in names:
        (bundled / name / "compose.yaml.disabled").rename(bundled / name / "compose.yaml")


def test_adding_hermes_starts_its_proxy_after_it(test_client, installation):
    bundled, start, _ = installation

    response = test_client.post("/api/extensions/hermes/enable?auto_enable_deps=true",
                                headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["enabled_services"] == ["searxng", "hermes", "hermes-proxy"]
    assert [call.args for call in start.call_args_list] == [
        ("start", "searxng"), ("start", "hermes"), ("start", "hermes-proxy"),
    ]
    assert all((bundled / name / "compose.yaml").is_file() for name in COMPOSE)


def test_an_already_running_proxy_is_not_started_twice(test_client, installation):
    bundled, start, _ = installation
    _select(bundled, "searxng", "hermes-proxy")

    response = test_client.post("/api/extensions/hermes/enable", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["enabled_services"] == ["hermes"]
    assert [call.args for call in start.call_args_list] == [("start", "hermes")]


def test_disabling_hermes_stops_its_proxy_first(test_client, installation):
    bundled, _, selections = installation
    _select(bundled, "searxng", "hermes", "hermes-proxy")

    response = test_client.post("/api/extensions/hermes/disable", headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["companions_disabled"] == ["hermes-proxy"]
    assert selections == [("disable", ["hermes-proxy"]), ("disable", ["hermes"])]
    assert (bundled / "searxng" / "compose.yaml").is_file()


def test_a_dependent_that_is_not_a_companion_still_blocks_disable(test_client, installation):
    bundled, _, selections = installation
    _select(bundled, "searxng", "hermes", "hermes-proxy")

    response = test_client.post("/api/extensions/hermes-proxy/disable", headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()["companions_disabled"] == []

    # SearXNG is a dependency of Hermes, not the other way round: nothing pairs.
    (bundled / "hermes" / "compose.yaml").write_text(
        "services:\n  hermes:\n    image: alpine:3.22\n    depends_on:\n      - searxng\n")
    response = test_client.post("/api/extensions/searxng/disable", headers=test_client.auth_headers)
    assert response.status_code == 409
    assert "hermes" in response.json()["detail"]
    assert selections == [("disable", ["hermes-proxy"])]


def test_companions_come_only_from_the_allowlist(monkeypatch, installation):
    monkeypatch.setattr(extensions, "LIBRARY_MANAGEABLE_BUILTINS", frozenset({"searxng", "hermes"}))
    assert extensions._feature_companions("hermes") == []
    monkeypatch.setattr(extensions, "LIBRARY_MANAGEABLE_BUILTINS", frozenset({"hermes", "hermes-proxy"}))
    assert extensions._feature_companions("hermes") == ["hermes-proxy"]
    assert extensions._feature_companions("searxng") == []
