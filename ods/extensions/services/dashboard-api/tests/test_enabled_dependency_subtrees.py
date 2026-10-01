"""Exercise the shipped Hermes Proxy -> Hermes -> SearXNG dependency chain."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from routers import extensions


@pytest.fixture(params=[False, True], ids=["disabled-target", "enabled-target"])
def installation(monkeypatch, tmp_path, request):
    bundled = tmp_path / "bundled"
    source = Path(__file__).resolve().parents[2]
    for name in ("hermes-proxy", "hermes", "searxng"):
        directory = bundled / name
        directory.mkdir(parents=True)
        (directory / "manifest.yaml").write_bytes((source / name / "manifest.yaml").read_bytes())
        filename = "compose.yaml.disabled" if name == "hermes-proxy" and not request.param else "compose.yaml"
        (directory / filename).write_text(f"services:\n  {name}:\n    image: alpine:3.22\n")

    def rename(action, name):
        before, after = ("compose.yaml.disabled", "compose.yaml")
        if action == "deactivate":
            before, after = after, before
        (bundled / name / before).rename(bundled / name / after)
        return True

    monkeypatch.setattr(extensions, "USER_EXTENSIONS_DIR", tmp_path / "user")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", bundled)
    monkeypatch.setattr(extensions, "DATA_DIR", str(tmp_path))
    start = Mock(return_value=True)
    monkeypatch.setattr(extensions, "_call_agent", start)
    def select(action, service_ids, expected_sha256=None):
        assert action == "enable"
        assert set(expected_sha256) == set(service_ids)
        for name in service_ids:
            if (bundled / name / "compose.yaml.disabled").exists():
                rename("activate", name)
        return {"action": "enabled", "service_ids": service_ids}

    monkeypatch.setattr(extensions, "_select_extensions_on_host", select)
    monkeypatch.setattr(extensions, "request_agent_json",
                        lambda *args, **kwargs: pytest.fail("unexpected host-agent transport"))
    monkeypatch.setattr(extensions, "_call_agent_hook", Mock(return_value=True))
    monkeypatch.setattr(extensions, "_call_agent_invalidate_compose_cache", Mock())
    return bundled, start


def simulate_disabled_search(bundled, start):
    """Retain coverage for an older or manually edited inconsistent install."""
    (bundled / "searxng/compose.yaml").rename(
        bundled / "searxng/compose.yaml.disabled")
    start.reset_mock()


def test_disable_search_blocks_enabled_hermes(test_client, installation):
    bundled, start = installation
    response = test_client.post("/api/extensions/searxng/disable?include_data_info=false",
                                headers=test_client.auth_headers)
    assert response.status_code == 409
    assert "hermes" in response.json()["detail"]
    start.assert_not_called()
    assert (bundled / "searxng/compose.yaml").is_file()


def test_user_file_does_not_shadow_bundled_enabled_dependent(
    test_client, installation,
):
    bundled, start = installation
    extensions.USER_EXTENSIONS_DIR.mkdir()
    (extensions.USER_EXTENSIONS_DIR / "hermes").write_text("not an extension directory")

    response = test_client.post(
        "/api/extensions/searxng/disable?include_data_info=false",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 409
    assert "hermes" in response.json()["detail"]
    start.assert_not_called()
    assert (bundled / "searxng/compose.yaml").is_file()


def test_disable_search_fails_closed_when_dependents_cannot_be_scanned(
    test_client, installation, monkeypatch,
):
    bundled, start = installation
    extensions.USER_EXTENSIONS_DIR.mkdir()
    original_iterdir = Path.iterdir

    def unreadable_user_extensions(path):
        if path == extensions.USER_EXTENSIONS_DIR:
            raise PermissionError("test: user extension directory is unreadable")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable_user_extensions)
    response = test_client.post(
        "/api/extensions/searxng/disable?include_data_info=false",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 503
    assert "Cannot inspect enabled extension dependencies" in response.json()["detail"]
    start.assert_not_called()
    assert (bundled / "searxng/compose.yaml").is_file()
    assert not (bundled / "searxng/compose.yaml.disabled").exists()


@pytest.mark.parametrize("compose_depends_on", [
    "depends_on: [n8n]",
    "depends_on:\n      n8n:\n        condition: service_started",
])
def test_disable_reads_compose_deps_missing_from_manifest(
    test_client, installation, compose_depends_on,
):
    bundled, start = installation
    n8n = bundled / "n8n"
    n8n.mkdir()
    (n8n / "manifest.yaml").write_text("service:\n  id: n8n\n", encoding="utf-8")
    (n8n / "compose.yaml").write_text(
        "services:\n  n8n:\n    image: alpine:3.22\n", encoding="utf-8",
    )
    consumer = extensions.USER_EXTENSIONS_DIR / "n8n-consumer"
    consumer.mkdir(parents=True)
    (consumer / "manifest.yaml").write_text(
        "service:\n  id: n8n-consumer\n  depends_on: []\n", encoding="utf-8",
    )
    (consumer / "compose.yaml").write_text(
        "services:\n  n8n-consumer:\n    image: alpine:3.22\n"
        f"    {compose_depends_on}\n",
        encoding="utf-8",
    )

    response = test_client.post(
        "/api/extensions/n8n/disable?include_data_info=false",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 409
    assert "n8n-consumer" in response.json()["detail"]
    start.assert_not_called()
    assert (n8n / "compose.yaml").is_file()


def test_disable_refuses_unresolved_compose_dependency(
    test_client, installation,
):
    bundled, start = installation
    n8n = bundled / "n8n"
    n8n.mkdir()
    (n8n / "manifest.yaml").write_text("service:\n  id: n8n\n", encoding="utf-8")
    (n8n / "compose.yaml").write_text(
        "services:\n  n8n:\n    image: alpine:3.22\n", encoding="utf-8",
    )
    consumer = extensions.USER_EXTENSIONS_DIR / "n8n-consumer"
    consumer.mkdir(parents=True)
    (consumer / "compose.yaml").write_text(
        "services:\n  n8n-consumer:\n    image: alpine:3.22\n"
        "    depends_on: [\"${UNRESOLVED_SERVICE}\"]\n",
        encoding="utf-8",
    )

    response = test_client.post(
        "/api/extensions/n8n/disable?include_data_info=false",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 503
    assert "no service was disabled" in response.json()["detail"]
    start.assert_not_called()
    assert (n8n / "compose.yaml").is_file()


def test_enable_requires_confirmation_for_disabled_transitive_service(test_client, installation):
    bundled, start = installation
    target_enabled = (bundled / "hermes-proxy" / "compose.yaml").exists()
    simulate_disabled_search(bundled, start)

    response = test_client.post("/api/extensions/hermes-proxy/enable",
                                headers=test_client.auth_headers)

    assert response.status_code == 400
    assert response.json()["detail"]["missing_dependencies"] == ["searxng"]
    assert (bundled / "hermes-proxy" / "compose.yaml").exists() is target_enabled
    assert (bundled / "hermes-proxy" / "compose.yaml.disabled").exists() is not target_enabled
    start.assert_not_called()


def test_confirmed_enable_repairs_transitive_service_before_target(test_client, installation):
    bundled, start = installation
    simulate_disabled_search(bundled, start)
    hermes_before = (bundled / "hermes" / "compose.yaml").read_bytes()

    response = test_client.post("/api/extensions/hermes-proxy/enable?auto_enable_deps=true",
                                headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["enabled_services"] == ["searxng", "hermes-proxy"]
    assert (bundled / "searxng" / "compose.yaml").exists()
    assert (bundled / "hermes-proxy" / "compose.yaml").exists()
    assert [call.args for call in start.call_args_list] == [
        ("start", "searxng"), ("start", "hermes-proxy"),
    ]
    assert (bundled / "hermes" / "compose.yaml").read_bytes() == hermes_before


def test_host_rejects_stale_enable_plan_before_any_start(
    test_client, installation, monkeypatch,
):
    from fastapi import HTTPException

    bundled, start = installation
    simulate_disabled_search(bundled, start)

    def stale_plan(action, service_ids, expected_sha256=None):
        assert action == "enable"
        assert "searxng" in service_ids
        raise HTTPException(status_code=409, detail="Dependency selection changed")

    monkeypatch.setattr(extensions, "_select_extensions_on_host", stale_plan)
    response = test_client.post(
        "/api/extensions/hermes-proxy/enable?auto_enable_deps=true",
        headers=test_client.auth_headers,
    )
    assert response.status_code == 409
    start.assert_not_called()
    assert (bundled / "searxng/compose.yaml.disabled").is_file()


def test_healthy_dependency_tree_does_not_require_confirmation(test_client, installation):
    _, start = installation

    response = test_client.post("/api/extensions/hermes-proxy/enable",
                                headers=test_client.auth_headers)

    assert response.status_code == 200
    assert response.json()["enabled_services"] == ["hermes-proxy"]
    start.assert_called_once_with("start", "hermes-proxy")


@pytest.mark.parametrize("user_definition", ["disabled", "missing-compose"])
def test_user_definition_cannot_borrow_bundled_dependency_activation(
    test_client, installation, user_definition,
):
    bundled, start = installation
    user_search = extensions.USER_EXTENSIONS_DIR / "searxng"
    user_search.mkdir(parents=True)
    (user_search / "manifest.yaml").write_bytes((bundled / "searxng/manifest.yaml").read_bytes())
    if user_definition == "disabled":
        (user_search / "compose.yaml.disabled").write_text(
            "services:\n  searxng:\n    image: alpine:3.22\n")

    response = test_client.post("/api/extensions/hermes-proxy/enable",
                                headers=test_client.auth_headers)

    assert response.status_code == 400
    assert response.json()["detail"]["missing_dependencies"] == ["searxng"]
    start.assert_not_called()
    assert (bundled / "searxng/compose.yaml").is_file()
    assert not (user_search / "compose.yaml").exists()


def test_corrupt_transitive_manifest_blocks_activation(test_client, installation):
    bundled, start = installation
    target = bundled / "hermes-proxy"
    active_before = (target / "compose.yaml").exists()
    (bundled / "hermes" / "manifest.yaml").write_text(
        "service:\n  id: hermes\n  depends_on: searxng\n", encoding="utf-8",
    )

    response = test_client.post(
        "/api/extensions/hermes-proxy/enable?auto_enable_deps=true",
        headers=test_client.auth_headers,
    )

    assert response.status_code == 400
    assert "Invalid dependency manifest" in response.json()["detail"]
    assert (target / "compose.yaml").exists() is active_before
    start.assert_not_called()


def test_enabled_user_definition_wins_over_disabled_bundle(installation):
    bundled, _ = installation
    (bundled / "searxng/compose.yaml").rename(bundled / "searxng/compose.yaml.disabled")
    user_search = extensions.USER_EXTENSIONS_DIR / "searxng"
    user_search.mkdir(parents=True)
    (user_search / "compose.yaml").write_text("services: {}\n")
    assert extensions._is_dep_satisfied("searxng")
