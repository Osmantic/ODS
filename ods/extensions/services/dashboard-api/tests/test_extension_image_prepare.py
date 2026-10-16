"""The Extensions page downloads a bundled service's images before enabling it.

A first download can take far longer on a slow link than an enable request
may stay open (Mac mini, 2026-10-04: Hermes and Open WebUI died at 600 s).
/prepare asks the host agent to download the images the enable plan will use,
selecting nothing; the page follows /progress and then enables as before.
"""

from unittest.mock import Mock

import pytest

from host_agent_client import AgentHTTPError, AgentUnavailable
from routers import extensions
from test_feature_companions import installation  # noqa: F401  (fixture)


@pytest.fixture
def agent(monkeypatch, installation):  # noqa: F811
    call = Mock(return_value={"status": "accepted", "service_ids": [], "pulling": ["hermes"]})
    monkeypatch.setattr(extensions, "request_agent_json", call)
    return call


def _prepare(test_client, service_id, query=""):
    return test_client.post(f"/api/extensions/{service_id}/prepare{query}", headers=test_client.auth_headers)


def test_prepare_requires_dashboard_auth(test_client, agent):
    response = test_client.post("/api/extensions/hermes/prepare")
    assert response.status_code in {401, 403}
    agent.assert_not_called()


def test_prepare_covers_the_whole_enable_plan_and_selects_nothing(test_client, installation, agent):  # noqa: F811
    bundled, start, selections = installation

    response = _prepare(test_client, "hermes", "?auto_enable_deps=true")

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "service_ids": ["searxng", "hermes", "hermes-proxy"]}
    agent.assert_called_once_with(
        "POST", "/v1/extension/prepare-images",
        payload={"service_ids": ["searxng", "hermes", "hermes-proxy"], "progress_id": "hermes"},
        timeout=120)
    assert selections == [] and start.call_count == 0
    assert all((bundled / name / "compose.yaml.disabled").is_file() for name in ("searxng", "hermes"))


def test_prepare_without_dependencies_prepares_what_enable_would_start(test_client, installation, agent):  # noqa: F811
    response = _prepare(test_client, "hermes")
    assert response.status_code == 202
    assert agent.call_args.kwargs["payload"]["service_ids"] == ["hermes"]


def test_prepare_reports_ready_when_the_images_are_here(test_client, installation, agent):  # noqa: F811
    agent.return_value = {"status": "ready", "service_ids": ["hermes"]}
    response = _prepare(test_client, "hermes")
    assert response.status_code == 200 and response.json()["status"] == "ready"


def test_open_webui_prepares_its_own_image(test_client, installation, agent):  # noqa: F811
    response = _prepare(test_client, "open-webui")
    assert response.status_code == 202
    assert agent.call_args.kwargs["payload"] == {"service_ids": ["open-webui"], "progress_id": "open-webui"}


@pytest.mark.parametrize("failure,status", [
    (AgentHTTPError(409, "busy"), 409),
    (AgentHTTPError(503, "private host detail"), 502),
    (AgentUnavailable("down"), 503),
])
def test_prepare_maps_host_failures_without_leaking_them(test_client, installation, agent, failure, status):  # noqa: F811
    agent.side_effect = failure
    response = _prepare(test_client, "hermes")
    assert response.status_code == status
    assert "private host detail" not in response.text


def test_prepare_refuses_an_unverifiable_answer(test_client, installation, agent):  # noqa: F811
    agent.return_value = {"status": "maybe"}
    assert _prepare(test_client, "hermes").status_code == 502


def test_prepare_refuses_library_recipes_and_core_services(test_client, installation, agent, tmp_path):  # noqa: F811
    library = tmp_path / "user" / "gotify"
    library.mkdir(parents=True)
    (library / "compose.yaml.disabled").write_text("services:\n  gotify:\n    image: alpine:3.22\n")
    assert _prepare(test_client, "gotify").status_code == 400
    assert _prepare(test_client, "llama-server").status_code == 403
    agent.assert_not_called()


def test_prepare_refuses_a_service_this_hardware_cannot_run(test_client, installation, agent, monkeypatch):  # noqa: F811
    monkeypatch.setattr(extensions, "EXTENSION_CATALOG", [{"id": "hermes", "name": "Hermes Agent",
                                                           "gpu_backends": ["nvidia"]}])
    monkeypatch.setattr(extensions, "GPU_BACKEND", "apple")
    response = _prepare(test_client, "hermes")
    assert response.status_code == 409 and "not available on this hardware" in response.json()["detail"]
    agent.assert_not_called()
