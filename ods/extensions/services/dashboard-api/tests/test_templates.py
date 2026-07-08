"""Tests for service template loading, preview, and apply."""

from unittest.mock import MagicMock, patch

import pytest
import yaml


# --- Template loading tests ---


def test_load_templates_valid(tmp_path):
    """Valid template YAML files are loaded correctly."""
    (tmp_path / "test.yaml").write_text(yaml.dump({
        "schema_version": "ods.templates.v1",
        "template": {
            "id": "test-tmpl",
            "name": "Test Template",
            "services": ["llama-server", "open-webui"],
        },
    }))

    with patch("config.TEMPLATES_DIR", tmp_path):
        # Re-import to trigger load
        from config import load_templates
        templates = load_templates()

    assert len(templates) == 1
    assert templates[0]["id"] == "test-tmpl"
    assert templates[0]["services"] == ["llama-server", "open-webui"]


def test_load_templates_invalid_schema(tmp_path):
    """Template with wrong schema_version is skipped."""
    (tmp_path / "bad.yaml").write_text(yaml.dump({
        "schema_version": "wrong.version",
        "template": {
            "id": "bad-tmpl",
            "name": "Bad",
            "services": ["foo"],
        },
    }))
    (tmp_path / "good.yaml").write_text(yaml.dump({
        "schema_version": "ods.templates.v1",
        "template": {
            "id": "good-tmpl",
            "name": "Good",
            "services": ["llama-server"],
        },
    }))

    with patch("config.TEMPLATES_DIR", tmp_path):
        from config import load_templates
        templates = load_templates()

    assert len(templates) == 1
    assert templates[0]["id"] == "good-tmpl"


def test_load_templates_missing_dir(tmp_path):
    """Missing templates directory returns empty list."""
    missing = tmp_path / "nonexistent"

    with patch("config.TEMPLATES_DIR", missing):
        from config import load_templates
        templates = load_templates()

    assert templates == []


def test_load_templates_malformed_yaml(tmp_path):
    """Malformed YAML is skipped with warning."""
    (tmp_path / "broken.yaml").write_text(":::invalid yaml{{{}}")

    with patch("config.TEMPLATES_DIR", tmp_path):
        from config import load_templates
        templates = load_templates()

    assert templates == []


# --- Preview tests ---


@pytest.mark.asyncio
async def test_template_preview_diff():
    """Preview shows correct to_enable, already_enabled, incompatible."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["llama-server", "open-webui", "comfyui"],
    }]

    mock_services_config = {
        "llama-server": {"port": 8080, "gpu_backends": ["amd", "nvidia", "apple"]},
        "open-webui": {"port": 3000, "gpu_backends": ["amd", "nvidia", "apple"]},
        "comfyui": {"port": 8188, "gpu_backends": ["nvidia", "amd"]},
    }

    class MockSvc:
        def __init__(self, id_, status):
            self.id = id_
            self.status = status

    mock_svc_list = [
        MockSvc("llama-server", "healthy"),
        MockSvc("open-webui", "unhealthy"),
    ]

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates.SERVICES", mock_services_config),
        patch("routers.templates.GPU_BACKEND", "apple"),
        patch("helpers.get_cached_services", return_value=mock_svc_list),
    ):
        from routers.templates import preview_template
        result = await preview_template("test-tmpl", api_key="test")

    assert result["template"]["id"] == "test-tmpl"
    # Both are base compose services → always already_enabled
    assert "llama-server" in result["changes"]["already_enabled"]
    assert "open-webui" in result["changes"]["already_enabled"]
    # Apple backend — all Docker services compatible, so comfyui should be in to_enable
    assert "comfyui" in result["changes"]["to_enable"]


@pytest.mark.asyncio
async def test_template_preview_in_progress_bucket():
    """Services in installing/setting_up state land in `in_progress`, not `to_enable`."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["svc-installing", "svc-setting-up", "svc-fresh"],
    }]
    mock_catalog = [
        {"id": "svc-installing", "gpu_backends": ["all"]},
        {"id": "svc-setting-up", "gpu_backends": ["all"]},
        {"id": "svc-fresh", "gpu_backends": ["all"]},
    ]
    mock_services_config = {
        "svc-installing": {"gpu_backends": ["all"]},
        "svc-setting-up": {"gpu_backends": ["all"]},
        "svc-fresh": {"gpu_backends": ["all"]},
    }

    def fake_compute_status(ext, _services_by_id):
        return {
            "svc-installing": "installing",
            "svc-setting-up": "setting_up",
            "svc-fresh": "not_installed",
        }[ext["id"]]

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates.EXTENSION_CATALOG", mock_catalog),
        patch("routers.templates.SERVICES", mock_services_config),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.GPU_BACKEND", "apple"),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._compute_extension_status", side_effect=fake_compute_status),
    ):
        from routers.templates import preview_template
        result = await preview_template("test-tmpl", api_key="test")

    changes = result["changes"]
    assert "svc-installing" in changes["in_progress"]
    assert "svc-setting-up" in changes["in_progress"]
    assert "svc-fresh" in changes["to_enable"]
    assert "svc-installing" not in changes["to_enable"]
    assert changes["has_errors"] == []


@pytest.mark.asyncio
async def test_template_preview_has_errors_bucket():
    """Services in error state land in `has_errors`, blocking to_enable classification."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["svc-error", "svc-ok"],
    }]
    mock_catalog = [
        {"id": "svc-error", "gpu_backends": ["all"]},
        {"id": "svc-ok", "gpu_backends": ["all"]},
    ]
    mock_services_config = {
        "svc-error": {"gpu_backends": ["all"]},
        "svc-ok": {"gpu_backends": ["all"]},
    }

    def fake_compute_status(ext, _services_by_id):
        return {"svc-error": "error", "svc-ok": "not_installed"}[ext["id"]]

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates.EXTENSION_CATALOG", mock_catalog),
        patch("routers.templates.SERVICES", mock_services_config),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.GPU_BACKEND", "apple"),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._compute_extension_status", side_effect=fake_compute_status),
    ):
        from routers.templates import preview_template
        result = await preview_template("test-tmpl", api_key="test")

    changes = result["changes"]
    assert "svc-error" in changes["has_errors"]
    assert "svc-ok" in changes["to_enable"]
    assert changes["in_progress"] == []


@pytest.mark.asyncio
async def test_template_preview_not_found():
    """Preview returns 404 for unknown template."""
    with patch("routers.templates.TEMPLATES", []):
        from fastapi import HTTPException
        from routers.templates import preview_template
        with pytest.raises(HTTPException) as exc_info:
            await preview_template("nonexistent", api_key="test")
        assert exc_info.value.status_code == 404


# --- Apply tests ---


@pytest.mark.asyncio
async def test_template_apply_additive(tmp_path):
    """Apply enables services that aren't already running."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["svc-a", "svc-b"],
    }]

    class MockSvc:
        def __init__(self, id_, status):
            self.id = id_
            self.status = status

    mock_svc_list = [
        MockSvc("svc-a", "healthy"),
    ]

    mock_activate = MagicMock(return_value={"id": "svc-b", "action": "enabled"})
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", tmp_path / "user-ext"),
        patch("helpers.get_cached_services", return_value=mock_svc_list),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._call_agent", return_value=True),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
    ):
        # Create user ext dir so host agent path resolves for svc-b
        user_ext = tmp_path / "user-ext" / "svc-b"
        user_ext.mkdir(parents=True)
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    assert result["template_id"] == "test-tmpl"
    assert result["results"]["svc-a"] == "already_enabled"
    assert result["results"]["svc-b"] == "enabled"
    assert result["enabled_count"] == 1
    assert "restart_required" in result


@pytest.mark.asyncio
async def test_template_apply_activates_deps(tmp_path):
    """Apply activates transitive deps before the target service."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["svc-b"],
    }]

    class MockSvc:
        def __init__(self, id_, status):
            self.id = id_
            self.status = status

    mock_activate = MagicMock(side_effect=lambda svc_id: {"id": svc_id, "action": "enabled"})
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    # Create user ext dirs so host agent path resolves
    user_ext = tmp_path / "user-ext"
    (user_ext / "dep-svc").mkdir(parents=True)
    (user_ext / "svc-b").mkdir(parents=True)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", user_ext),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=["dep-svc"]),
        patch("routers.extensions._call_agent", return_value=True),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
    ):
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    # _activate_service must be called for dep-svc AND svc-b
    activated_ids = [call.args[0] for call in mock_activate.call_args_list]
    assert "dep-svc" in activated_ids
    assert "svc-b" in activated_ids
    assert result["results"]["dep-svc"] == "enabled_as_dependency"
    assert result["results"]["svc-b"] == "enabled"
    assert result["enabled_count"] == 2


@pytest.mark.asyncio
async def test_template_apply_incompatible_skipped():
    """Apply skips services that fail activation (e.g., not installed)."""
    from fastapi import HTTPException

    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["missing-svc"],
    }]

    class MockSvc:
        def __init__(self, id_, status):
            self.id = id_
            self.status = status

    def mock_activate(svc_id):
        raise HTTPException(status_code=404, detail=f"Extension not installed: {svc_id}")

    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._validate_service_id"),
    ):
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    assert result["enabled_count"] == 0
    assert "skipped" in result["results"]["missing-svc"]


@pytest.mark.asyncio
async def test_template_apply_builtin_extension(tmp_path):
    """Apply with a built-in extension skips host agent start and sets restart_required."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["builtin-svc"],
    }]

    class MockSvc:
        def __init__(self, id_, status):
            self.id = id_
            self.status = status

    mock_activate = MagicMock(return_value={"id": "builtin-svc", "action": "enabled"})
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    # user-ext dir exists but does NOT contain builtin-svc → built-in path
    user_ext = tmp_path / "user-ext"
    user_ext.mkdir()

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", user_ext),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._call_agent", return_value=True),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
    ):
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    assert result["results"]["builtin-svc"] == "enabled"
    assert result["restart_required"] is True
    assert result["enabled_count"] == 1


@pytest.mark.asyncio
async def test_template_apply_auto_installs_library_extension(tmp_path):
    """Apply copies a library extension into USER_EXTENSIONS_DIR before activating it."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["lib-svc"],
    }]

    user_ext = tmp_path / "user-ext"
    user_ext.mkdir()

    install_calls: list[str] = []

    def mock_install(svc_id):
        # Simulate the library install: create the dir so the host-agent start branch runs
        (user_ext / svc_id).mkdir(parents=True, exist_ok=True)
        install_calls.append(svc_id)

    # After install, _activate_service sees compose.yaml is already in place
    mock_activate = MagicMock(return_value={"id": "lib-svc", "action": "already_enabled"})
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", user_ext),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._call_agent", return_value=True),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
        patch("routers.extensions._is_installable", return_value=True),
        patch("routers.extensions._install_from_library", side_effect=mock_install),
    ):
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    assert install_calls == ["lib-svc"]
    assert result["results"]["lib-svc"] == "library_installed"
    assert result["library_installed"] == ["lib-svc"]
    # Library install → compose stack grew → restart required
    assert result["restart_required"] is True
    assert result["enabled_count"] == 1


@pytest.mark.asyncio
async def test_template_apply_library_install_failure_skips_gracefully(tmp_path):
    """When _install_from_library raises, the service is skipped with a clear reason."""
    from fastapi import HTTPException

    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["lib-svc"],
    }]

    user_ext = tmp_path / "user-ext"
    user_ext.mkdir()

    def mock_install(svc_id):
        raise HTTPException(status_code=503, detail="Extensions library is unavailable")

    mock_activate = MagicMock(return_value={"id": "lib-svc", "action": "enabled"})
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", user_ext),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._call_agent", return_value=True),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
        patch("routers.extensions._is_installable", return_value=True),
        patch("routers.extensions._install_from_library", side_effect=mock_install),
    ):
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    assert "skipped" in result["results"]["lib-svc"]
    assert "install failed" in result["results"]["lib-svc"]
    assert result["library_installed"] == []
    assert result["enabled_count"] == 0
    # _activate_service must NOT have been called after the install failed
    mock_activate.assert_not_called()


@pytest.mark.asyncio
async def test_template_apply_library_already_installed_skips_reinstall(tmp_path):
    """If the library extension is already in USER_EXTENSIONS_DIR, _install_from_library is NOT called."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["lib-svc"],
    }]

    user_ext = tmp_path / "user-ext"
    (user_ext / "lib-svc").mkdir(parents=True)  # Already installed

    mock_install = MagicMock()
    mock_activate = MagicMock(return_value={"id": "lib-svc", "action": "enabled"})
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", user_ext),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._call_agent", return_value=True),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
        patch("routers.extensions._is_installable", return_value=True),
        patch("routers.extensions._install_from_library", mock_install),
    ):
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    mock_install.assert_not_called()
    assert result["library_installed"] == []
    assert result["results"]["lib-svc"] == "enabled"
    assert result["enabled_count"] == 1


@pytest.mark.asyncio
async def test_template_apply_mixed_builtin_and_library(tmp_path):
    """Template with both built-in and library extensions handles both correctly."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["builtin-svc", "lib-svc"],
    }]

    user_ext = tmp_path / "user-ext"
    user_ext.mkdir()

    installed: list[str] = []

    def mock_install(svc_id):
        (user_ext / svc_id).mkdir(parents=True, exist_ok=True)
        installed.append(svc_id)

    def mock_is_installable(svc_id):
        return svc_id == "lib-svc"

    def mock_activate(svc_id):
        if svc_id == "lib-svc":
            return {"id": svc_id, "action": "already_enabled"}
        return {"id": svc_id, "action": "enabled"}

    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", user_ext),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._activate_service", side_effect=mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._call_agent", return_value=True),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
        patch("routers.extensions._is_installable", side_effect=mock_is_installable),
        patch("routers.extensions._install_from_library", side_effect=mock_install),
    ):
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    # lib-svc was installed, builtin-svc was not
    assert installed == ["lib-svc"]
    assert result["library_installed"] == ["lib-svc"]
    # Built-in extension: compose file toggled → message set to "enabled" in post loop
    assert result["results"]["builtin-svc"] == "enabled"
    # Library extension: marked as library_installed
    assert result["results"]["lib-svc"] == "library_installed"
    # Both count as enabled
    assert result["enabled_count"] == 2
    # Either the built-in toggle or the library install forces a restart
    assert result["restart_required"] is True


@pytest.mark.asyncio
async def test_template_apply_already_enabled_still_starts(tmp_path):
    """Service with action 'already_enabled' from _activate_service still gets started."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["svc-a"],
    }]

    class MockSvc:
        def __init__(self, id_, status):
            self.id = id_
            self.status = status

    # _activate_service returns already_enabled (compose.yaml exists but container not running)
    mock_activate = MagicMock(return_value={"id": "svc-a", "action": "already_enabled"})
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)
    mock_agent = MagicMock(return_value=True)

    user_ext = tmp_path / "user-ext"
    (user_ext / "svc-a").mkdir(parents=True)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", user_ext),
        # svc-a is NOT healthy — so it won't be skipped by the healthy check
        patch("helpers.get_cached_services", return_value=[MockSvc("svc-a", "unhealthy")]),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._call_agent", mock_agent),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
    ):
        from routers.templates import apply_template
        result = await apply_template("test-tmpl", api_key="test")

    # _call_agent should have been called for svc-a (start attempt)
    mock_agent.assert_called()
    assert result["enabled_count"] == 1


@pytest.mark.asyncio
async def test_template_apply_invalidates_compose_flags_cache(tmp_path):
    """Applying a template must invalidate .compose-flags so ods-cli sees the new stack."""
    mock_templates = [{
        "id": "test-tmpl",
        "name": "Test",
        "services": ["svc-b"],
    }]

    class MockSvc:
        def __init__(self, id_, status):
            self.id = id_
            self.status = status

    mock_activate = MagicMock(return_value={"id": "svc-b", "action": "enabled"})
    mock_invalidate = MagicMock()
    mock_lock = MagicMock()
    mock_lock.__enter__ = MagicMock(return_value=None)
    mock_lock.__exit__ = MagicMock(return_value=False)

    with (
        patch("routers.templates.TEMPLATES", mock_templates),
        patch("routers.templates._BASE_COMPOSE_SERVICES", frozenset()),
        patch("routers.templates.USER_EXTENSIONS_DIR", tmp_path / "user-ext"),
        patch("helpers.get_cached_services", return_value=[]),
        patch("routers.extensions._activate_service", mock_activate),
        patch("routers.extensions._extensions_lock", return_value=mock_lock),
        patch("routers.extensions._get_missing_deps_transitive", return_value=[]),
        patch("routers.extensions._call_agent", return_value=True),
        patch("routers.extensions._call_agent_hook", return_value=True),
        patch("routers.extensions._validate_service_id"),
        patch(
            "routers.extensions._call_agent_invalidate_compose_cache",
            mock_invalidate,
        ),
    ):
        user_ext = tmp_path / "user-ext" / "svc-b"
        user_ext.mkdir(parents=True)
        from routers.templates import apply_template
        await apply_template("test-tmpl", api_key="test")

    # Cache invalidation must fire at least once for the activation path.
    assert mock_invalidate.called, "apply_template must invalidate .compose-flags cache"


# --- Event-loop non-blocking guarantee (structural / AST proof) ---


def test_apply_template_blocking_calls_run_in_to_thread():
    """Every blocking helper inside apply_template must be wrapped in
    asyncio.to_thread so the event loop is not stalled while waiting on
    network or filesystem locks.

    This is a structural proof: we walk the AST of apply_template and
    confirm that any Call to a known blocking helper appears as the first
    argument of an asyncio.to_thread(...) call inside an Await.
    Replaces a flaky concurrent-request timing test with a deterministic
    one.
    """
    import ast
    import inspect
    import textwrap

    from routers.templates import apply_template

    blocking_callees = {
        "_call_agent_hook",
        "_call_agent",
        "_call_agent_invalidate_compose_cache",
        "_get_missing_deps_transitive",
        "_install_from_library",
        "_install_with_lock",
        "_activate_with_lock",
    }

    src = textwrap.dedent(inspect.getsource(apply_template))
    tree = ast.parse(src)

    # Collect every Call to a blocking helper
    found_blocking_calls: list[tuple[str, bool]] = []

    class _Walker(ast.NodeVisitor):
        def __init__(self):
            self.in_to_thread_first_arg = False

        def visit_Call(self, node: ast.Call) -> None:
            func_name = None
            if isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                func_name = node.func.id

            # Check whether this is asyncio.to_thread(<callee>, ...) and mark
            # its first positional arg as "wrapped".
            is_to_thread = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "to_thread"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "asyncio"
            )
            if is_to_thread and node.args:
                first = node.args[0]
                # The first argument should be a Name referencing a callable
                if isinstance(first, ast.Name) and first.id in blocking_callees:
                    found_blocking_calls.append((first.id, True))

            # Detect direct (non-wrapped) calls to blocking helpers — these
            # would be the regression we want to prevent.
            if func_name in blocking_callees and not is_to_thread:
                found_blocking_calls.append((func_name, False))

            self.generic_visit(node)

    _Walker().visit(tree)

    # Build a map: callee -> set of (wrapped) booleans seen
    seen: dict[str, set[bool]] = {}
    for name, wrapped in found_blocking_calls:
        seen.setdefault(name, set()).add(wrapped)

    # The blocking helpers we expect apply_template to invoke
    must_be_wrapped = {
        "_call_agent_hook",
        "_call_agent",
        "_get_missing_deps_transitive",
        "_install_with_lock",
        "_activate_with_lock",
    }

    for callee in must_be_wrapped:
        assert callee in seen, (
            f"Expected apply_template to call {callee}() — not found in AST. "
            f"Either the helper was renamed or the to_thread wrapping was removed."
        )
        assert True in seen[callee], (
            f"{callee}() is called but never wrapped in asyncio.to_thread(...). "
            f"This would block the event loop and is a regression of the "
            f"apply_template async fix."
        )
        assert False not in seen[callee], (
            f"{callee}() is called directly (without asyncio.to_thread). "
            f"All blocking helpers must run in the thread pool."
        )
