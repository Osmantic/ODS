"""Tests for extensions portal endpoints."""

import contextlib
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi import HTTPException
from models import ServiceStatus
from routers.extensions import _assert_not_core


# --- Helpers ---


def _make_catalog_ext(ext_id, name="Test", category="optional",
                      gpu_backends=None, env_vars=None, features=None):
    return {
        "id": ext_id,
        "name": name,
        "description": f"Description for {name}",
        "category": category,
        "gpu_backends": gpu_backends or ["nvidia", "amd", "apple"],
        "compose_file": "compose.yaml",
        "depends_on": [],
        "port": 8080,
        "external_port_default": 8080,
        "health_endpoint": "/health",
        "env_vars": env_vars or [],
        "tags": [],
        "features": features or [],
    }


def _make_service_status(sid, status="healthy"):
    return ServiceStatus(
        id=sid, name=sid, port=8080, external_port=8080, status=status,
    )


def can_create_symlinks(tmp_path: Path) -> bool:
    target = tmp_path / "symlink-target"
    link = tmp_path / "symlink-probe"
    target.write_text("probe", encoding="utf-8")
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return link.is_symlink()


def _patch_extensions_config(monkeypatch, catalog, services=None,
                             gpu_backend="nvidia", tmp_path=None):
    """Apply standard patches for extensions router tests."""
    monkeypatch.setattr("routers.extensions.EXTENSION_CATALOG", catalog)
    monkeypatch.setattr("routers.extensions.SERVICES", services or {})
    monkeypatch.setattr("routers.extensions.GPU_BACKEND", gpu_backend)
    lib_dir = (tmp_path / "lib") if tmp_path else Path("/tmp/nonexistent-lib")
    user_dir = (tmp_path / "user") if tmp_path else Path("/tmp/nonexistent-user")
    monkeypatch.setattr("routers.extensions.EXTENSIONS_LIBRARY_DIR", lib_dir)
    monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
    monkeypatch.setattr("routers.extensions.DATA_DIR",
                        str(tmp_path or "/tmp/nonexistent"))


# --- Catalog endpoint ---


class TestExtensionsCatalog:

    def test_catalog_returns_enriched_extensions(self, test_client, monkeypatch, tmp_path):
        """Catalog endpoint returns extensions with status enrichment."""
        catalog = [_make_catalog_ext("test-svc", "Test Service")]
        services = {"test-svc": {"host": "localhost", "port": 8080, "name": "Test"}}
        _patch_extensions_config(monkeypatch, catalog, services, tmp_path=tmp_path)

        mock_svc = _make_service_status("test-svc", "healthy")
        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[mock_svc]):
            resp = test_client.get(
                "/api/extensions/catalog",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["extensions"]) == 1
        assert data["extensions"][0]["status"] == "enabled"
        assert data["extensions"][0]["installable"] is False
        assert "summary" in data
        assert data["gpu_backend"] == "nvidia"

    def test_catalog_category_filter(self, test_client, monkeypatch, tmp_path):
        """Category filter returns only matching extensions."""
        catalog = [
            _make_catalog_ext("svc-a", "A", category="ai"),
            _make_catalog_ext("svc-b", "B", category="tools"),
        ]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)

        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[]):
            resp = test_client.get(
                "/api/extensions/catalog?category=ai",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["extensions"]) == 1
        assert data["extensions"][0]["id"] == "svc-a"

    def test_catalog_gpu_compatible_filter(self, test_client, monkeypatch, tmp_path):
        """gpu_compatible filter excludes incompatible extensions."""
        catalog = [
            _make_catalog_ext("compat", "Compatible", gpu_backends=["nvidia"]),
            _make_catalog_ext("incompat", "Incompatible", gpu_backends=["amd"]),
        ]
        _patch_extensions_config(monkeypatch, catalog, gpu_backend="nvidia",
                                 tmp_path=tmp_path)

        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[]):
            resp = test_client.get(
                "/api/extensions/catalog?gpu_compatible=true",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        ids = [e["id"] for e in data["extensions"]]
        assert "compat" in ids
        assert "incompat" not in ids

    def test_catalog_summary_counts(self, test_client, monkeypatch, tmp_path):
        """Summary counts correctly reflect extension statuses."""
        catalog = [
            _make_catalog_ext("enabled-svc", "Enabled"),
            _make_catalog_ext("disabled-svc", "Disabled"),
            _make_catalog_ext("not-installed", "Not Installed"),
            _make_catalog_ext("incompat", "Incompatible", gpu_backends=["amd"]),
        ]
        services = {
            "enabled-svc": {"host": "localhost", "port": 8080, "name": "Enabled"},
            "disabled-svc": {"host": "localhost", "port": 8081, "name": "Disabled"},
        }
        _patch_extensions_config(monkeypatch, catalog, services,
                                 gpu_backend="nvidia", tmp_path=tmp_path)

        mock_svcs = [
            _make_service_status("enabled-svc", "healthy"),
            _make_service_status("disabled-svc", "down"),
        ]
        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=mock_svcs):
            resp = test_client.get(
                "/api/extensions/catalog",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["total"] == 4
        assert summary["enabled"] == 1
        assert summary["disabled"] == 1
        assert summary["not_installed"] == 1
        assert summary["incompatible"] == 1
        assert summary["installed"] == 2

    def test_catalog_empty_when_no_catalog(self, test_client, monkeypatch, tmp_path):
        """Missing catalog file results in empty extensions list."""
        _patch_extensions_config(monkeypatch, [], tmp_path=tmp_path)

        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[]):
            resp = test_client.get(
                "/api/extensions/catalog",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["extensions"] == []
        assert data["summary"]["total"] == 0

    def test_catalog_requires_auth(self, test_client):
        """GET /api/extensions/catalog without auth → 401."""
        resp = test_client.get("/api/extensions/catalog")
        assert resp.status_code == 401


# --- Detail endpoint ---


class TestExtensionDetail:

    def test_detail_returns_extension(self, test_client, monkeypatch, tmp_path):
        """Detail endpoint returns correct extension with setup instructions."""
        catalog = [_make_catalog_ext("test-svc", "Test Service")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)

        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[]):
            resp = test_client.get(
                "/api/extensions/test-svc",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "test-svc"
        assert data["name"] == "Test Service"
        assert data["status"] == "not_installed"
        assert "manifest" in data
        assert "setup_instructions" in data
        assert data["setup_instructions"]["cli_enable"] == "ods enable test-svc"
        assert data["setup_instructions"]["cli_disable"] == "ods disable test-svc"

    def test_detail_404_for_unknown(self, test_client, monkeypatch, tmp_path):
        """404 for service_id not in catalog."""
        _patch_extensions_config(monkeypatch, [], tmp_path=tmp_path)

        resp = test_client.get(
            "/api/extensions/nonexistent",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 404

    def test_detail_rejects_path_traversal(self, test_client, monkeypatch, tmp_path):
        """Regex validation rejects path traversal and invalid service IDs."""
        _patch_extensions_config(monkeypatch, [], tmp_path=tmp_path)

        for bad_id in ["..etc", ".hidden", "UPPERCASE", "-starts-dash"]:
            resp = test_client.get(
                f"/api/extensions/{bad_id}",
                headers=test_client.auth_headers,
            )
            assert resp.status_code == 404, f"Expected 404 for: {bad_id}"

    def test_detail_path_traversal_with_slashes(self, test_client):
        """Path traversal with slashes never reaches the handler."""
        # Starlette normalizes ../etc/passwd out of the route
        resp = test_client.get(
            "/api/extensions/../etc/passwd",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 404

        resp = test_client.get(
            "/api/extensions/../../",
            headers=test_client.auth_headers,
        )
        assert resp.status_code in (404, 307)

    def test_detail_requires_auth(self, test_client):
        """GET /api/extensions/{id} without auth → 401."""
        resp = test_client.get("/api/extensions/test-svc")
        assert resp.status_code == 401


# --- User-installed extension status ---


class TestUserExtensionStatus:

    def test_user_ext_compose_yaml_healthy(self, test_client, monkeypatch, tmp_path):
        """User extension with compose.yaml + healthy service → enabled."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text("version: '3'")

        catalog = [_make_catalog_ext("my-ext", "My Extension")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)

        mock_svc = _make_service_status("my-ext", "healthy")
        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[mock_svc]):
            resp = test_client.get(
                "/api/extensions/catalog",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        ext = resp.json()["extensions"][0]
        assert ext["id"] == "my-ext"
        assert ext["status"] == "enabled"

    def test_user_ext_compose_yaml_no_service(self, test_client, monkeypatch, tmp_path):
        """User extension with compose.yaml but no running container → stopped."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text("version: '3'")

        catalog = [_make_catalog_ext("my-ext", "My Extension")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)

        # No service in health results — svc is None → stopped
        with patch("user_extensions.get_user_services_cached",
                   return_value={}):
            with patch("helpers.get_all_services", new_callable=AsyncMock,
                       return_value=[]):
                resp = test_client.get(
                    "/api/extensions/catalog",
                    headers=test_client.auth_headers,
                )

        assert resp.status_code == 200
        ext = resp.json()["extensions"][0]
        assert ext["id"] == "my-ext"
        assert ext["status"] == "stopped"

    def test_user_ext_compose_yaml_disabled(self, test_client, monkeypatch, tmp_path):
        """User extension with compose.yaml.disabled → disabled."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml.disabled").write_text("version: '3'")

        catalog = [_make_catalog_ext("my-ext", "My Extension")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)

        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[]):
            resp = test_client.get(
                "/api/extensions/catalog",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        ext = resp.json()["extensions"][0]
        assert ext["id"] == "my-ext"
        assert ext["status"] == "disabled"


# --- Mutation test helpers ---


_SAFE_COMPOSE = "services:\n  svc:\n    image: test:latest\n"


def _setup_library_ext(tmp_path, service_id, compose_content=None):
    """Create a library extension directory with compose.yaml and manifest."""
    lib_dir = tmp_path / "lib"
    lib_dir.mkdir(exist_ok=True)
    ext_dir = lib_dir / service_id
    ext_dir.mkdir(exist_ok=True)
    (ext_dir / "compose.yaml").write_text(compose_content or _SAFE_COMPOSE)
    (ext_dir / "manifest.yaml").write_text(yaml.dump({
        "schema_version": "ods.services.v1",
        "service": {"id": service_id, "name": service_id},
    }))
    return lib_dir


def _setup_user_ext(tmp_path, service_id, enabled=True, manifest=None):
    """Create a user-installed extension directory."""
    user_dir = tmp_path / "user"
    user_dir.mkdir(exist_ok=True)
    ext_dir = user_dir / service_id
    ext_dir.mkdir(exist_ok=True)
    if enabled:
        (ext_dir / "compose.yaml").write_text(_SAFE_COMPOSE)
    else:
        (ext_dir / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)
    if manifest:
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))
    return user_dir


def _patch_mutation_config(monkeypatch, tmp_path, lib_dir=None, user_dir=None):
    """Patch config values for mutation endpoint tests."""
    lib_dir = lib_dir or (tmp_path / "lib")
    user_dir = user_dir or (tmp_path / "user")
    lib_dir.mkdir(exist_ok=True)
    user_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("routers.extensions.EXTENSIONS_LIBRARY_DIR", lib_dir)
    monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
    monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
    monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR",
                        tmp_path / "builtin")
    monkeypatch.setattr("routers.extensions.CORE_SERVICE_IDS",
                        frozenset({"dashboard-api", "open-webui", "hermes", "hermes-proxy"}))


# --- Install endpoint ---


class TestInstallExtension:

    def test_install_copies_and_enables(self, test_client, monkeypatch, tmp_path):
        """Install copies from library and keeps compose.yaml enabled."""
        lib_dir = _setup_library_ext(tmp_path, "my-ext")
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "my-ext"
        assert data["action"] == "installed"
        assert "restart_required" in data

        user_dir = tmp_path / "user"
        assert (user_dir / "my-ext").is_dir()
        assert (user_dir / "my-ext" / "compose.yaml").exists()

    def test_install_cleans_broken_directory(self, test_client, monkeypatch, tmp_path):
        """Install succeeds when dest dir exists but has no compose files (broken state)."""
        lib_dir = _setup_library_ext(tmp_path, "my-ext")
        # Create a broken user extension directory (no compose.yaml or compose.yaml.disabled)
        user_dir = tmp_path / "user"
        user_dir.mkdir(exist_ok=True)
        broken_dir = user_dir / "my-ext"
        broken_dir.mkdir(exist_ok=True)
        (broken_dir / "manifest.yaml").write_text("leftover: true\n")
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir,
                               user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "installed"
        assert (user_dir / "my-ext" / "compose.yaml").exists()

    def test_install_stages_tmp_under_user_extensions_dir(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Library installs must not require write access to the /data mount root."""
        import tempfile

        lib_dir = _setup_library_ext(tmp_path, "my-ext")
        user_dir = tmp_path / "user"
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir,
                               user_dir=user_dir)

        captured = {}
        real_mkdtemp = tempfile.mkdtemp

        def fake_mkdtemp(*args, **kwargs):
            captured["dir"] = Path(kwargs["dir"])
            return real_mkdtemp(*args, **kwargs)

        monkeypatch.setattr("routers.extensions.tempfile.mkdtemp", fake_mkdtemp)

        resp = test_client.post(
            "/api/extensions/my-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        assert captured["dir"] == user_dir / ".tmp"
        assert (user_dir / "my-ext" / "compose.yaml").exists()

    def test_install_already_installed_409(self, test_client, monkeypatch, tmp_path):
        """409 when extension is already installed."""
        lib_dir = _setup_library_ext(tmp_path, "my-ext")
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir,
                               user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 409

    def test_install_retries_after_error_progress(self, test_client, monkeypatch, tmp_path):
        """A terminal install error should allow retrying the library install."""
        lib_dir = _setup_library_ext(tmp_path, "my-ext")
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        (progress_dir / "my-ext.json").write_text(json.dumps({
            "service_id": "my-ext",
            "status": "error",
            "error": "previous compose resolve failed",
            "started_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }))
        (user_dir / "my-ext" / "stale.txt").write_text("left over")
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir,
                               user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        assert not (user_dir / "my-ext" / "stale.txt").exists()
        assert (user_dir / "my-ext" / "compose.yaml").exists()
        assert resp.json()["action"] == "installed"

    def test_install_unknown_extension_404(self, test_client, monkeypatch, tmp_path):
        """404 when extension is not in the library."""
        _patch_mutation_config(monkeypatch, tmp_path)

        resp = test_client.post(
            "/api/extensions/nonexistent/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 404

    def test_install_rejects_library_entry_without_compose(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when the library entry exists but ships no deployable compose.yaml.

        Mirrors the dify/jan/fooocus shape: directory present, manifest
        present, but only `compose.yaml.disabled` or `compose.yaml.reference`
        on disk. The catalog/UI already hides the Install button for these via
        `_is_installable`, but a direct POST must also reject — otherwise the
        copytree succeeds but the host agent can't start anything, surfacing
        as a cryptic post-install failure instead of a clean 400.
        """
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir(exist_ok=True)
        ext_dir = lib_dir / "reference-only"
        ext_dir.mkdir(exist_ok=True)
        # Only .disabled — no deployable compose.yaml
        (ext_dir / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)
        (ext_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "reference-only", "name": "reference-only"},
        }))
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/reference-only/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        body = resp.json()
        assert "compose.yaml" in body["detail"]
        # Verify nothing was copied to user-extensions/
        assert not (tmp_path / "user" / "reference-only").exists()

    def test_install_rejects_library_entry_with_only_reference_compose(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Same shape, .reference suffix variant (mirrors fooocus).

        Some library entries ship `compose.yaml.reference` instead of
        `.disabled`. Either suffix is reference material — only literal
        `compose.yaml` is deployable.
        """
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir(exist_ok=True)
        ext_dir = lib_dir / "reference-only"
        ext_dir.mkdir(exist_ok=True)
        (ext_dir / "compose.yaml.reference").write_text(_SAFE_COMPOSE)
        (ext_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "reference-only", "name": "reference-only"},
        }))
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/reference-only/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert not (tmp_path / "user" / "reference-only").exists()

    def test_install_core_service_403(self, test_client, monkeypatch, tmp_path):
        """403 when trying to install a core service."""
        _patch_mutation_config(monkeypatch, tmp_path)

        resp = test_client.post(
            "/api/extensions/dashboard-api/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 403

    def test_install_rejects_privileged(self, test_client, monkeypatch, tmp_path):
        """400 when compose uses privileged mode."""
        bad_compose = "services:\n  svc:\n    image: test\n    privileged: true\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext",
                                     compose_content=bad_compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "privileged" in resp.json()["detail"]

    def test_install_rejects_docker_socket(self, test_client, monkeypatch, tmp_path):
        """400 when compose mounts Docker socket."""
        bad_compose = (
            "services:\n  svc:\n    image: test\n"
            "    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext",
                                     compose_content=bad_compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "Docker socket mount" in resp.json()["detail"]

    def test_install_allows_library_build_context(self, test_client, monkeypatch, tmp_path):
        """Library extensions with build: context are allowed (trusted)."""
        bad_compose = "services:\n  svc:\n    build: .\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext",
                                     compose_content=bad_compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "installed"

    def test_install_requires_auth(self, test_client):
        """POST install without auth → 401."""
        resp = test_client.post("/api/extensions/my-ext/install")
        assert resp.status_code == 401

    # test_install_writes_pending_change removed — v3 uses host agent, no pending changes file


    def test_install_allows_library_host_gateway_extra_host(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Bundled library extensions may use the host-gateway bridge."""
        compose = (
            "services:\n"
            "  svc:\n"
            "    image: test:latest\n"
            "    extra_hosts:\n"
            "      - host.docker.internal:host-gateway\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "host-gateway-ext",
                                     compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/host-gateway-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["action"] == "installed"

    def test_install_rejects_untrusted_extra_hosts(
        self, test_client, monkeypatch, tmp_path,
    ):
        """User-installed extension compose files cannot add host aliases."""
        compose = (
            "services:\n"
            "  svc:\n"
            "    image: test:latest\n"
            "    extra_hosts:\n"
            "      - host.docker.internal:host-gateway\n"
        )
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "host-gateway-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml.disabled").write_text(compose)
        (ext_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "host-gateway-ext", "name": "host-gateway-ext"},
        }))
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/host-gateway-ext/enable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 400
        assert "extra_hosts" in resp.json()["detail"]

    def test_install_rejects_unapproved_library_extra_hosts(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Trusted library status only permits the known host-gateway mapping."""
        compose = (
            "services:\n"
            "  svc:\n"
            "    image: test:latest\n"
            "    extra_hosts:\n"
            "      - metadata.google.internal:169.254.169.254\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-host-ext",
                                     compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-host-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 400
        assert "unsupported extra_hosts" in resp.json()["detail"]


# --- Enable endpoint ---


class TestEnableExtension:

    def test_enable_renames_to_compose_yaml(self, test_client, monkeypatch, tmp_path):
        """Enable renames compose.yaml.disabled → compose.yaml."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"
        assert data["restart_required"] is True
        assert (user_dir / "my-ext" / "compose.yaml").exists()
        assert not (user_dir / "my-ext" / "compose.yaml.disabled").exists()

    def test_enable_stopped_starts_without_rename(self, test_client, monkeypatch, tmp_path):
        """Enable when compose.yaml exists (stopped) → starts without rename."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"
        # compose.yaml still exists (no rename happened)
        assert (user_dir / "my-ext" / "compose.yaml").exists()

    def test_enable_allows_core_service_dependency(self, test_client, monkeypatch, tmp_path):
        """Enable succeeds when depends_on includes a core service."""
        manifest = {"service": {"depends_on": ["open-webui"]}}
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False,
                                   manifest=manifest)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"

    def test_enable_missing_dependency_400(self, test_client, monkeypatch, tmp_path):
        """400 when a dependency is not enabled."""
        manifest = {"service": {"depends_on": ["missing-dep"]}}
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False,
                                   manifest=manifest)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "missing-dep" in detail["missing_dependencies"]
        assert detail["auto_enable_available"] is True

    def test_enable_core_service_403(self, test_client, monkeypatch, tmp_path):
        """403 when trying to enable a core service."""
        _patch_mutation_config(monkeypatch, tmp_path)

        resp = test_client.post(
            "/api/extensions/open-webui/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 403

    def test_enable_requires_auth(self, test_client):
        """POST enable without auth → 401."""
        resp = test_client.post("/api/extensions/my-ext/enable")
        assert resp.status_code == 401

    def test_enable_rejects_build_context(self, test_client, monkeypatch, tmp_path):
        """400 when user extension compose contains a build context."""
        bad_compose = "services:\n  svc:\n    build: .\n"
        user_dir = tmp_path / "user"
        user_dir.mkdir(exist_ok=True)
        ext_dir = user_dir / "bad-ext"
        ext_dir.mkdir(exist_ok=True)
        (ext_dir / "compose.yaml.disabled").write_text(bad_compose)
        (ext_dir / "manifest.yaml").write_text("schema_version: ods.services.v1\nservice:\n  id: bad-ext\n  name: bad-ext\n")
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "local build" in resp.json()["detail"]


class TestEnableExtensionHookReturnHandling:
    """pre_start failures must block start; post_start failures surface as warnings."""

    def test_enable_pre_start_failure_blocks_start(
        self, test_client, monkeypatch, tmp_path,
    ):
        """pre_start False → start NOT called, error progress written, agent_ok=False."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        start_calls = []

        def fake_start(action, sid):
            start_calls.append((action, sid))
            return True

        monkeypatch.setattr("routers.extensions._call_agent", fake_start)
        # pre_start returns False, post_start would return True (but should not be reached)
        monkeypatch.setattr(
            "routers.extensions._call_agent_hook",
            lambda sid, hook: hook != "pre_start",
        )

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"
        assert data["restart_required"] is True
        assert "Run 'ods restart'" in data["message"]
        assert data["warnings"] == []
        # start was never called for the service whose pre_start failed
        assert ("start", "my-ext") not in start_calls

        # Error progress file should record the pre_start failure
        progress_file = tmp_path / "extension-progress" / "my-ext.json"
        assert progress_file.exists()
        progress = json.loads(progress_file.read_text())
        assert progress["status"] == "error"
        assert "pre_start hook failed" in progress["error"]

    def test_enable_post_start_failure_returns_warning(
        self, test_client, monkeypatch, tmp_path,
    ):
        """post_start False → start IS called, response carries a warning, success path."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        start_calls = []

        def fake_start(action, sid):
            start_calls.append((action, sid))
            return True

        monkeypatch.setattr("routers.extensions._call_agent", fake_start)
        # pre_start succeeds, post_start fails
        monkeypatch.setattr(
            "routers.extensions._call_agent_hook",
            lambda sid, hook: hook != "post_start",
        )

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"
        # post_start failure is non-fatal — service still reports success
        assert data["restart_required"] is False
        assert data["message"] == "Extension enabled and started."
        assert ("start", "my-ext") in start_calls
        assert isinstance(data["warnings"], list)
        assert len(data["warnings"]) == 1
        assert "my-ext" in data["warnings"][0]
        assert "post_start hook failed" in data["warnings"][0]

    def test_enable_both_hooks_succeed_no_warnings(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Baseline: pre_start + post_start True → no warnings, agent_ok True."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        monkeypatch.setattr(
            "routers.extensions._call_agent", lambda action, sid: True,
        )
        monkeypatch.setattr(
            "routers.extensions._call_agent_hook", lambda sid, hook: True,
        )

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"
        assert data["restart_required"] is False
        assert data["warnings"] == []
        assert data["message"] == "Extension enabled and started."

    def test_multi_svc_pre_start_failure_on_dep_does_not_warn(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Multi-svc mixed-outcome: dep pre_start fails, main proceeds.

        Covers fork issue #494's "pre_start failures must be terminal for
        the failing service but must not pollute the warnings array" path.
        Dep's pre_start failure writes its own error progress and is
        treated as terminal (no start, no post_start) — the warnings
        accumulator only collects post_start failures. Main service's
        hooks all succeed but agent_ok stays False because one of the
        services in enabled_services failed pre_start, so
        restart_required is True.
        """
        user_dir = tmp_path / "user"
        user_dir.mkdir(exist_ok=True)
        # Dep ext (no further deps).
        dep_dir = user_dir / "dep"
        dep_dir.mkdir()
        (dep_dir / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)
        (dep_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "dep", "name": "dep"},
        }))
        # Main ext, depends_on dep.
        main_dir = user_dir / "main-ext"
        main_dir.mkdir()
        (main_dir / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)
        (main_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "main-ext", "name": "main-ext",
                         "depends_on": ["dep"]},
        }))
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        monkeypatch.setattr(
            "routers.extensions._call_agent", lambda action, sid: True,
        )
        # pre_start fails for dep; everything else succeeds.
        monkeypatch.setattr(
            "routers.extensions._call_agent_hook",
            lambda sid, hook: not (sid == "dep" and hook == "pre_start"),
        )
        monkeypatch.setattr(
            "routers.extensions._call_agent_invalidate_compose_cache",
            lambda: None,
        )

        resp = test_client.post(
            "/api/extensions/main-ext/enable?auto_enable_deps=true",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"
        # pre_start failure on dep keeps agent_ok False → restart required.
        assert data["restart_required"] is True
        # warnings collects only post_start failures, so dep's pre_start
        # failure must NOT appear here.
        assert data["warnings"] == []
        # Both services were activated (dep auto-enabled, then main).
        assert "dep" in data["enabled_services"]
        assert "main-ext" in data["enabled_services"]

        # Dep got an error-progress file recording the pre_start failure.
        dep_progress = tmp_path / "extension-progress" / "dep.json"
        assert dep_progress.exists()
        progress = json.loads(dep_progress.read_text())
        assert progress["status"] == "error"
        assert "pre_start hook failed" in progress["error"]

    def test_multi_svc_post_start_failure_on_main_only_warns_main(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Multi-svc mixed-outcome: dep all-pass, main post_start fails.

        Covers fork issue #494's "warnings must name the failing service
        and not double-count clean dependencies" path. Dep enables clean
        with no warning entry; main's post_start failure produces exactly
        one warning string identifying main-ext. Post_start is non-fatal,
        so agent_ok stays True and restart_required is False.
        """
        user_dir = tmp_path / "user"
        user_dir.mkdir(exist_ok=True)
        dep_dir = user_dir / "dep"
        dep_dir.mkdir()
        (dep_dir / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)
        (dep_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "dep", "name": "dep"},
        }))
        main_dir = user_dir / "main-ext"
        main_dir.mkdir()
        (main_dir / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)
        (main_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "main-ext", "name": "main-ext",
                         "depends_on": ["dep"]},
        }))
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        monkeypatch.setattr(
            "routers.extensions._call_agent", lambda action, sid: True,
        )
        # post_start fails ONLY for main-ext.
        monkeypatch.setattr(
            "routers.extensions._call_agent_hook",
            lambda sid, hook: not (sid == "main-ext" and hook == "post_start"),
        )
        monkeypatch.setattr(
            "routers.extensions._call_agent_invalidate_compose_cache",
            lambda: None,
        )

        resp = test_client.post(
            "/api/extensions/main-ext/enable?auto_enable_deps=true",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"
        # post_start is non-fatal → agent_ok stays True.
        assert data["restart_required"] is False
        # Exactly one warning, naming the main service only.
        assert isinstance(data["warnings"], list)
        assert len(data["warnings"]) == 1
        assert "main-ext" in data["warnings"][0]
        assert "post_start hook failed" in data["warnings"][0]
        # Dep must NOT show up in warnings (it cleanly enabled).
        assert all("dep" not in w.split(":")[0] for w in data["warnings"])
        assert "dep" in data["enabled_services"]
        assert "main-ext" in data["enabled_services"]


# --- Disable endpoint ---


class TestDisableExtension:

    def test_disable_renames_to_disabled(self, test_client, monkeypatch, tmp_path):
        """Disable renames compose.yaml → compose.yaml.disabled."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/disable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "disabled"
        assert data["restart_required"] is True
        assert (user_dir / "my-ext" / "compose.yaml.disabled").exists()
        assert not (user_dir / "my-ext" / "compose.yaml").exists()

    def test_disable_builtin_delegates_to_host_agent(
        self, test_client, monkeypatch, tmp_path,
    ):
        builtin_root = tmp_path / "builtin"
        ext_dir = builtin_root / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text(_SAFE_COMPOSE)
        _patch_mutation_config(monkeypatch, tmp_path)
        monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr("routers.extensions._call_agent", lambda action, sid: True)

        calls = []

        def _mock_compose_rename(action, service_id):
            calls.append((action, service_id))
            (ext_dir / "compose.yaml").rename(ext_dir / "compose.yaml.disabled")
            return True

        monkeypatch.setattr(
            "routers.extensions._call_agent_compose_rename",
            _mock_compose_rename,
        )

        resp = test_client.post(
            "/api/extensions/my-ext/disable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["action"] == "disabled"
        assert calls == [("deactivate", "my-ext")]
        assert (ext_dir / "compose.yaml.disabled").exists()
        assert not (ext_dir / "compose.yaml").exists()

    def test_disable_unlinks_progress_file(self, test_client, monkeypatch, tmp_path):
        """Disable removes the stale progress file so status reflects reality."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
        progress_file = tmp_path / "extension-progress" / "my-ext.json"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text('{"status": "started", "updated_at": "2026-04-10T00:00:00"}')

        resp = test_client.post(
            "/api/extensions/my-ext/disable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        assert not progress_file.exists()

    def test_disable_already_disabled_409(self, test_client, monkeypatch, tmp_path):
        """409 when extension is already disabled."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/disable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 409

    def test_disable_core_service_403(self, test_client, monkeypatch, tmp_path):
        """403 when trying to disable a core service."""
        _patch_mutation_config(monkeypatch, tmp_path)

        resp = test_client.post(
            "/api/extensions/dashboard-api/disable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 403

    def test_disable_warns_about_dependents(self, test_client, monkeypatch, tmp_path):
        """Disable warns about extensions that depend on this one."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        # Extension to disable
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir()
        (ext_dir / "compose.yaml").write_text(_SAFE_COMPOSE)
        # Dependent extension
        dep_dir = user_dir / "dependent-ext"
        dep_dir.mkdir()
        (dep_dir / "compose.yaml").write_text(_SAFE_COMPOSE)
        (dep_dir / "manifest.yaml").write_text(
            yaml.dump({"service": {"depends_on": ["my-ext"]}}),
        )
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/disable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert "dependent-ext" in data["dependents_warning"]

    def test_disable_requires_auth(self, test_client):
        """POST disable without auth → 401."""
        resp = test_client.post("/api/extensions/my-ext/disable")
        assert resp.status_code == 401

    def test_disable_skips_data_info(self, test_client, monkeypatch, tmp_path):
        """include_data_info=false → data_info is None (skips expensive dir scan)."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/disable?include_data_info=false",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["data_info"] is None


# --- Uninstall endpoint ---


class TestUninstallExtension:

    def test_uninstall_removes_dir(self, test_client, monkeypatch, tmp_path):
        """Uninstall removes the extension directory."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.delete(
            "/api/extensions/my-ext",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "uninstalled"
        assert not (user_dir / "my-ext").exists()

    def test_uninstall_unlinks_progress_file(self, test_client, monkeypatch, tmp_path):
        """Uninstall removes the stale progress file so status reflects reality."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
        progress_file = tmp_path / "extension-progress" / "my-ext.json"
        progress_file.parent.mkdir(parents=True, exist_ok=True)
        progress_file.write_text('{"status": "started", "updated_at": "2026-04-10T00:00:00"}')

        resp = test_client.delete(
            "/api/extensions/my-ext",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        assert not progress_file.exists()

    def test_uninstall_rejects_enabled_400(self, test_client, monkeypatch, tmp_path):
        """400 when extension is still enabled."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.delete(
            "/api/extensions/my-ext",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "Disable extension before uninstalling" in resp.json()["detail"]

    def test_uninstall_core_service_403(self, test_client, monkeypatch, tmp_path):
        """403 when trying to uninstall a core service."""
        _patch_mutation_config(monkeypatch, tmp_path)

        resp = test_client.delete(
            "/api/extensions/open-webui",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 403

    def test_uninstall_requires_auth(self, test_client):
        """DELETE without auth → 401."""
        resp = test_client.delete("/api/extensions/my-ext")
        assert resp.status_code == 401


# --- Compose-flags cache invalidation ---


class TestComposeCacheInvalidation:
    """Every successful compose mutation must invalidate the host .compose-flags cache."""

    def _spy(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "routers.extensions._call_agent_invalidate_compose_cache",
            lambda: calls.append(1),
        )
        return calls

    def test_install_invalidates_cache(self, test_client, monkeypatch, tmp_path):
        lib_dir = _setup_library_ext(tmp_path, "my-ext")
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)
        calls = self._spy(monkeypatch)

        resp = test_client.post(
            "/api/extensions/my-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        assert len(calls) == 1

    def test_enable_invalidates_cache(self, test_client, monkeypatch, tmp_path):
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
        calls = self._spy(monkeypatch)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        assert len(calls) == 1

    def test_enable_stopped_invalidates_cache(self, test_client, monkeypatch, tmp_path):
        """Stopped-start branch: compose.yaml already exists (library extension
        enabled flow). Cache must be invalidated BEFORE the host agent start
        call so it sees the new compose set."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        order: list[str] = []
        monkeypatch.setattr(
            "routers.extensions._call_agent_invalidate_compose_cache",
            lambda: order.append("invalidate"),
        )
        monkeypatch.setattr(
            "routers.extensions._call_agent",
            lambda action, svc: order.append(f"agent:{action}:{svc}") or True,
        )

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        assert order.count("invalidate") == 1
        assert order.index("invalidate") < order.index("agent:start:my-ext")

    def test_disable_invalidates_cache(self, test_client, monkeypatch, tmp_path):
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
        calls = self._spy(monkeypatch)

        resp = test_client.post(
            "/api/extensions/my-ext/disable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        assert len(calls) == 1

    def test_uninstall_invalidates_cache(self, test_client, monkeypatch, tmp_path):
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=False)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
        calls = self._spy(monkeypatch)

        resp = test_client.delete(
            "/api/extensions/my-ext",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200
        assert len(calls) == 1


# --- Path traversal on mutation endpoints ---


class TestMutationPathTraversal:

    def test_path_traversal_all_mutations(self, test_client, monkeypatch, tmp_path):
        """Path traversal IDs are rejected on all mutation endpoints."""
        _patch_mutation_config(monkeypatch, tmp_path)

        bad_ids = ["..etc", ".hidden", "UPPERCASE", "-starts-dash"]
        endpoints = [
            ("POST", "/api/extensions/{}/install"),
            ("POST", "/api/extensions/{}/enable"),
            ("POST", "/api/extensions/{}/disable"),
            ("DELETE", "/api/extensions/{}"),
            ("DELETE", "/api/extensions/{}/data"),
        ]

        for bad_id in bad_ids:
            for method, pattern in endpoints:
                url = pattern.format(bad_id)
                if method == "POST":
                    resp = test_client.post(
                        url, headers=test_client.auth_headers,
                    )
                elif "/data" in pattern:
                    # Purge endpoint requires a JSON body (PurgeRequest)
                    resp = test_client.request(
                        "DELETE", url, headers=test_client.auth_headers,
                        json={"confirm": False},
                    )
                else:
                    resp = test_client.delete(
                        url, headers=test_client.auth_headers,
                    )
                assert resp.status_code == 404, (
                    f"Expected 404 for {method} {url}, got {resp.status_code}"
                )


# --- Compose security scan edge cases ---


class TestComposeScanEdgeCases:

    def test_scan_rejects_cap_add_sys_admin(self, test_client, monkeypatch, tmp_path):
        """400 when compose adds SYS_ADMIN capability."""
        compose = "services:\n  svc:\n    image: test\n    cap_add:\n      - SYS_ADMIN\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "SYS_ADMIN" in resp.json()["detail"]

    def test_scan_rejects_pid_host(self, test_client, monkeypatch, tmp_path):
        """400 when compose uses pid: host."""
        compose = "services:\n  svc:\n    image: test\n    pid: host\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "host PID" in resp.json()["detail"]

    def test_scan_rejects_network_mode_host(self, test_client, monkeypatch, tmp_path):
        """400 when compose uses network_mode: host."""
        compose = "services:\n  svc:\n    image: test\n    network_mode: host\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "host network" in resp.json()["detail"]

    def test_scan_rejects_user_root(self, test_client, monkeypatch, tmp_path):
        """400 when compose runs as user: root."""
        compose = "services:\n  svc:\n    image: test\n    user: root\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "root" in resp.json()["detail"]

    def test_scan_rejects_user_0_colon_0(self, test_client, monkeypatch, tmp_path):
        """400 when compose runs as user: '0:0' (root bypass variant)."""
        compose = 'services:\n  svc:\n    image: test\n    user: "0:0"\n'
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "root" in resp.json()["detail"]

    def test_scan_rejects_absolute_host_path_mount(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose mounts an absolute host path."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    volumes:\n      - /etc/passwd:/etc/passwd:ro\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "absolute host path" in resp.json()["detail"]

    def test_scan_rejects_run_docker_sock(self, test_client, monkeypatch, tmp_path):
        """400 when compose mounts /run/docker.sock (variant path)."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    volumes:\n      - /run/docker.sock:/var/run/docker.sock\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "Docker socket mount" in resp.json()["detail"]

    def test_scan_rejects_bare_port_binding(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose uses bare host:container port binding."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    ports:\n      - '8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "127.0.0.1" in resp.json()["detail"]

    def test_scan_allows_localhost_port_binding(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Safe compose with 127.0.0.1 port binding passes scan."""
        compose = (
            "services:\n  svc:\n    image: test:latest\n"
            "    ports:\n      - '127.0.0.1:8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "safe-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/safe-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200

    def test_scan_rejects_0000_port_binding(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose binds to 0.0.0.0 explicitly."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    ports:\n      - '0.0.0.0:8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "127.0.0.1" in resp.json()["detail"]

    def test_scan_allows_bind_address_var_with_loopback_default(
        self, test_client, monkeypatch, tmp_path,
    ):
        """${BIND_ADDRESS:-127.0.0.1} is the sanctioned LAN-toggle pattern (PR #964)."""
        compose = (
            "services:\n  svc:\n    image: test:latest\n"
            "    ports:\n      - '${BIND_ADDRESS:-127.0.0.1}:8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bind-ok", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bind-ok/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200

    def test_scan_allows_arbitrary_var_name_with_loopback_default(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Any ${VAR:-127.0.0.1} form is accepted, not just BIND_ADDRESS."""
        compose = (
            "services:\n  svc:\n    image: test:latest\n"
            "    ports:\n      - '${MY_HOST:-127.0.0.1}:8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bind-var", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bind-var/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200

    def test_scan_rejects_var_with_non_loopback_default(
        self, test_client, monkeypatch, tmp_path,
    ):
        """A variable defaulting to 0.0.0.0 must NOT be accepted."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    ports:\n      - '${BIND_ADDRESS:-0.0.0.0}:8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-default", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-default/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "127.0.0.1" in resp.json()["detail"]

    def test_scan_rejects_var_without_default(
        self, test_client, monkeypatch, tmp_path,
    ):
        """A bare ${VAR} (no default) is unsafe — it binds 0.0.0.0 when unset."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    ports:\n      - '${BIND_ADDRESS}:8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "no-default", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/no-default/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "127.0.0.1" in resp.json()["detail"]

    def test_scan_allows_dict_port_with_bind_address_default(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Dict-form port with host_ip: ${VAR:-127.0.0.1} is also accepted."""
        compose = (
            "services:\n  svc:\n    image: test:latest\n"
            "    ports:\n      - target: 80\n"
            "        published: 8080\n"
            "        host_ip: '${BIND_ADDRESS:-127.0.0.1}'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "dict-ok", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/dict-ok/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200

    def test_scan_rejects_core_service_name(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose service name collides with a core service."""
        compose = "services:\n  open-webui:\n    image: test:latest\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "conflicts with core service" in resp.json()["detail"]

    @pytest.mark.parametrize("service_name", ["hermes", "hermes-proxy"])
    def test_scan_rejects_hermes_core_service_name(
        self, test_client, monkeypatch, tmp_path, service_name,
    ):
        """Hermes built-ins must be protected from user extension shadowing."""
        compose = f"services:\n  {service_name}:\n    image: test:latest\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 400
        assert "conflicts with core service" in resp.json()["detail"]

    def test_scan_rejects_cap_add_sys_ptrace(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose adds SYS_PTRACE (expanded blocklist)."""
        compose = "services:\n  svc:\n    image: test\n    cap_add:\n      - SYS_PTRACE\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "SYS_PTRACE" in resp.json()["detail"]

    def test_scan_rejects_lowercase_cap(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose adds lowercase capability (case-insensitive check)."""
        compose = "services:\n  svc:\n    image: test\n    cap_add:\n      - sys_admin\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "dangerous capability" in resp.json()["detail"]

    def test_scan_rejects_ipc_host(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose uses ipc: host."""
        compose = "services:\n  svc:\n    image: test\n    ipc: host\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "host IPC" in resp.json()["detail"]

    def test_scan_rejects_userns_mode_host(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose uses userns_mode: host."""
        compose = "services:\n  svc:\n    image: test\n    userns_mode: host\n"
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "host user namespace" in resp.json()["detail"]

    def test_scan_rejects_named_volume_bind_mount(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when top-level volume uses driver_opts to bind-mount host path."""
        compose = (
            "services:\n  svc:\n    image: test:latest\n"
            "    volumes:\n      - mydata:/data\n"
            "volumes:\n  mydata:\n    driver_opts:\n"
            "      type: none\n      o: bind\n      device: /etc\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "bind-mount host path" in resp.json()["detail"]

    def test_scan_rejects_dict_port_without_localhost(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose uses dict-form port binding without 127.0.0.1."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    ports:\n      - target: 80\n        published: 8080\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "127.0.0.1" in resp.json()["detail"]

    def test_scan_rejects_bare_port_no_colon(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose uses bare port without colon (e.g. '8080')."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    ports:\n      - '8080'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "bare port" in resp.json()["detail"]

    def test_scan_rejects_security_opt_equals_separator(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose uses security_opt with '=' separator."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    security_opt:\n      - seccomp=unconfined\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "dangerous security_opt" in resp.json()["detail"]

    def test_scan_rejects_deploy_resources_devices(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when compose requests GPU passthrough via
        deploy.resources.reservations.devices (Compose v2 GPU syntax).
        Library installs default to skip_gpu_passthrough_check=False."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    deploy:\n"
            "      resources:\n"
            "        reservations:\n"
            "          devices:\n"
            "            - driver: nvidia\n"
            "              count: 1\n"
            "              capabilities: [gpu]\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "bad-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "GPU passthrough" in resp.json()["detail"]


# --- Direct unit tests for the port-binding helpers ---


class TestHostPartIsLoopback:
    """Direct unit tests for `_host_part_is_loopback` — pin the regex
    behaviour against future refactors. Triggered through the install
    endpoint by TestComposeScanEdgeCases above; these tests are the
    fast-feedback layer."""

    def test_literal_loopback(self):
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("127.0.0.1") is True

    def test_var_with_loopback_default(self):
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("${BIND_ADDRESS:-127.0.0.1}") is True
        assert _host_part_is_loopback("${MY_HOST:-127.0.0.1}") is True

    def test_rejects_var_without_default(self):
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("${BIND_ADDRESS}") is False

    def test_rejects_non_loopback_default(self):
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("${BIND_ADDRESS:-0.0.0.0}") is False
        assert _host_part_is_loopback("${BIND_ADDRESS:-localhost}") is False

    def test_rejects_assignment_default_form(self):
        """Compose's ${VAR:=default} (assignment) is not the same as
        ${VAR:-default} (substitution); reject defensively."""
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("${BIND_ADDRESS:=127.0.0.1}") is False

    def test_rejects_dash_only_default_form(self):
        """${VAR-default} (only-if-unset) differs from ${VAR:-default}
        (only-if-unset-or-empty). Strictly require the colon form."""
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("${BIND_ADDRESS-127.0.0.1}") is False

    def test_rejects_zero_padded_loopback(self):
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("127.000.000.001") is False
        assert _host_part_is_loopback("${BIND_ADDRESS:-127.000.000.001}") is False

    def test_rejects_ipv6_loopback(self):
        """IPv6 binds aren't in scope for the LAN toggle."""
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("::1") is False
        assert _host_part_is_loopback("[::1]") is False

    def test_rejects_trailing_newline(self):
        """fullmatch must defend against `$` matching before \\n."""
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("127.0.0.1\n") is False
        assert _host_part_is_loopback("${BIND_ADDRESS:-127.0.0.1}\n") is False

    def test_rejects_empty_and_whitespace(self):
        from routers.extensions import _host_part_is_loopback
        assert _host_part_is_loopback("") is False
        assert _host_part_is_loopback(" 127.0.0.1") is False
        assert _host_part_is_loopback("127.0.0.1 ") is False


class TestSplitPortHost:
    """Direct unit tests for `_split_port_host` — naive str.split(':') is
    wrong on the `:-` default operator inside `${VAR:-127.0.0.1}`. These
    tests pin the malformed-input behaviour as fail-closed."""

    def test_literal_host_three_parts(self):
        from routers.extensions import _split_port_host
        assert _split_port_host("127.0.0.1:8080:80") == ("127.0.0.1", "8080:80")

    def test_var_with_default(self):
        from routers.extensions import _split_port_host
        assert _split_port_host("${BIND_ADDRESS:-127.0.0.1}:8080:80") == (
            "${BIND_ADDRESS:-127.0.0.1}", "8080:80",
        )

    def test_var_with_default_and_proto(self):
        from routers.extensions import _split_port_host
        assert _split_port_host("${BIND_ADDRESS:-127.0.0.1}:8554:8554/udp") == (
            "${BIND_ADDRESS:-127.0.0.1}", "8554:8554/udp",
        )

    def test_var_no_default(self):
        """`${VAR}:8080:80` — no `:-` default, but still has the brace."""
        from routers.extensions import _split_port_host
        assert _split_port_host("${BIND_ADDRESS}:8080:80") == (
            "${BIND_ADDRESS}", "8080:80",
        )

    def test_var_with_default_alone(self):
        """`${VAR:-127.0.0.1}` with NO host:container suffix — must
        return rest='' so the caller's `':' not in core` check kicks in."""
        from routers.extensions import _split_port_host
        host, rest = _split_port_host("${BIND_ADDRESS:-127.0.0.1}")
        assert rest == ""

    def test_malformed_no_closing_brace(self):
        """`${VAR:-127.0.0.1` (missing `}`) — fail closed."""
        from routers.extensions import _split_port_host
        host, rest = _split_port_host("${BIND_ADDRESS:-127.0.0.1")
        assert rest == ""

    def test_malformed_no_separator_after_brace(self):
        """`${VAR:-127.0.0.1}8080:80` (no `:` between `}` and host port)."""
        from routers.extensions import _split_port_host
        host, rest = _split_port_host("${BIND_ADDRESS:-127.0.0.1}8080:80")
        assert rest == ""

    def test_two_part_with_digit_host_returns_no_host(self):
        """`8080:80` — host position is a port number, no host_ip; treat
        as no-host so caller rejects (binds 0.0.0.0)."""
        from routers.extensions import _split_port_host
        assert _split_port_host("8080:80") == (None, "8080:80")

    def test_bare_port_returns_no_host(self):
        from routers.extensions import _split_port_host
        assert _split_port_host("8080") == (None, "8080")

    def test_empty_string(self):
        from routers.extensions import _split_port_host
        assert _split_port_host("") == (None, "")


class TestScanComposePortBindingRegressionLocks:
    """Regression locks for forms that must STAY rejected even though
    they vaguely look like loopback bindings."""

    def test_ipv6_loopback_bracketed_rejected(
        self, test_client, monkeypatch, tmp_path,
    ):
        """`[::1]:8080:80` is not in the policy; must be rejected."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    ports:\n      - '[::1]:8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "ipv6-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/ipv6-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "127.0.0.1" in resp.json()["detail"]

    def test_var_with_proto_suffix_accepted(
        self, test_client, monkeypatch, tmp_path,
    ):
        """`${VAR:-127.0.0.1}:8554:8554/udp` is the sanctioned pattern
        with an explicit /proto suffix (e.g. frigate's WebRTC port)."""
        compose = (
            "services:\n  svc:\n    image: test:latest\n"
            "    ports:\n      - '${BIND_ADDRESS:-127.0.0.1}:8554:8554/udp'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "udp-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/udp-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200

    def test_hostname_in_host_position_rejected(
        self, test_client, monkeypatch, tmp_path,
    ):
        """A hostname like `localhost` is not loopback under the regex —
        the runtime resolution might not even resolve to 127.0.0.1
        (IPv6 ::1, /etc/hosts override, etc.)."""
        compose = (
            "services:\n  svc:\n    image: test\n"
            "    ports:\n      - 'localhost:8080:80'\n"
        )
        lib_dir = _setup_library_ext(tmp_path, "host-ext", compose_content=compose)
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/host-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400


# --- skip_name_collision flag isolation ---


class TestScanComposeSkipNameCollision:
    """Direct unit tests for the skip_name_collision parameter added for
    built-in activation (fork issue #338)."""

    def test_rejects_core_name_by_default(self, tmp_path):
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  open-webui:\n    image: test\n")
        with pytest.raises(HTTPException) as exc:
            _scan_compose_content(compose, skip_name_collision=False)
        assert exc.value.status_code == 400
        assert "conflicts with core service" in exc.value.detail

    def test_allows_core_name_when_skipped(self, tmp_path):
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  open-webui:\n    image: test\n")
        _scan_compose_content(compose, skip_name_collision=True)

    def test_privileged_still_blocked_when_skipped(self, tmp_path):
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  svc:\n    image: test\n    privileged: true\n")
        with pytest.raises(HTTPException) as exc:
            _scan_compose_content(compose, skip_name_collision=True)
        assert "privileged" in exc.value.detail

    def test_docker_socket_still_blocked_when_skipped(self, tmp_path):
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text("services:\n  svc:\n    image: test\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n")
        with pytest.raises(HTTPException) as exc:
            _scan_compose_content(compose, skip_name_collision=True)
        assert "Docker socket" in exc.value.detail


class TestScanComposeVolumeBypass:
    """The volume guard must resolve the host source for long-form ({type:
    bind, source: ...}) and relative-traversal entries, not just short-form
    absolute strings."""

    def _scan(self, tmp_path, compose_text):
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(compose_text)
        _scan_compose_content(compose)

    def test_rejects_long_form_absolute_bind(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            self._scan(
                tmp_path,
                "services:\n  svc:\n    image: test\n    volumes:\n"
                "      - type: bind\n        source: /\n        target: /host\n",
            )
        assert exc.value.status_code == 400
        assert "absolute host path" in exc.value.detail

    def test_rejects_relative_traversal_short_form(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            self._scan(
                tmp_path,
                "services:\n  svc:\n    image: test\n"
                "    volumes:\n      - ../../:/host\n",
            )
        assert exc.value.status_code == 400
        assert "escaping the project directory" in exc.value.detail

    def test_rejects_long_form_relative_traversal(self, tmp_path):
        with pytest.raises(HTTPException) as exc:
            self._scan(
                tmp_path,
                "services:\n  svc:\n    image: test\n    volumes:\n"
                "      - type: bind\n        source: ../..\n        target: /host\n",
            )
        assert exc.value.status_code == 400
        assert "escaping the project directory" in exc.value.detail

    def test_allows_named_volume(self, tmp_path):
        # A named volume (no path separator) is not a host bind and must pass.
        self._scan(
            tmp_path,
            "services:\n  svc:\n    image: test\n"
            "    volumes:\n      - mydata:/var/lib/data\n",
        )

    def test_allows_project_relative_subdir_bind(self, tmp_path):
        # A ./subdir bind stays inside the extension's own directory.
        self._scan(
            tmp_path,
            "services:\n  svc:\n    image: test\n"
            "    volumes:\n      - ./data:/data\n",
        )


# --- skip_gpu_passthrough_check flag isolation ---


class TestScanComposeSkipGpuPassthroughCheck:
    """Direct unit tests for the skip_gpu_passthrough_check parameter that
    permits built-in extensions (e.g. comfyui's nvidia overlay) to declare
    deploy.resources.reservations.devices while user extensions cannot."""

    _GPU_COMPOSE = (
        "services:\n  svc:\n    image: test\n"
        "    deploy:\n"
        "      resources:\n"
        "        reservations:\n"
        "          devices:\n"
        "            - driver: nvidia\n"
        "              count: 1\n"
        "              capabilities: [gpu]\n"
    )

    def test_rejects_deploy_devices_by_default(self, tmp_path):
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(self._GPU_COMPOSE)
        with pytest.raises(HTTPException) as exc:
            _scan_compose_content(compose)
        assert exc.value.status_code == 400
        assert "GPU passthrough" in exc.value.detail

    def test_allows_deploy_devices_when_skipped(self, tmp_path):
        """Built-in compose paths pass skip_gpu_passthrough_check=True so the
        legitimate NVIDIA reservation in docker-compose.nvidia.yml does not
        get rejected when the dashboard-api re-scans during activate/enable.
        """
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(self._GPU_COMPOSE)
        # Should not raise
        _scan_compose_content(compose, skip_gpu_passthrough_check=True)

    def test_handles_null_resources_without_500(self, tmp_path):
        """Strict regression for the audit-flagged bug: `deploy: { resources: null }`.

        Pre-fix code did `deploy.get("resources", {}).get("reservations", {})`.
        `dict.get(key, default)` returns the value when the key is present,
        NOT the default — so `{"resources": None}.get("resources", {})` yields
        None, and the next `.get()` AttributeError'd → 500 to the caller.
        The fix's `isinstance(resources, dict)` guard short-circuits cleanly
        because no GPU passthrough request can be expressed via null resources.
        """
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(
            "services:\n  svc:\n    image: test\n"
            "    deploy:\n"
            "      resources: null\n"
        )
        # Should not raise — no GPU request can be expressed via null resources.
        _scan_compose_content(compose)

    def test_handles_null_reservations_without_500(self, tmp_path):
        """Defense-in-depth: `resources: { reservations: null }`.

        The pre-fix code was already safe at this level — its leaf check
        `isinstance(reservations, dict) and reservations.get("devices")`
        short-circuited on `None`. This test locks the behavior in so a
        future refactor that drops the leaf isinstance check (e.g. relying
        only on the new outer guards) cannot reintroduce a 500 here.
        """
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(
            "services:\n  svc:\n    image: test\n"
            "    deploy:\n"
            "      resources:\n"
            "        reservations: null\n"
        )
        # Should not raise.
        _scan_compose_content(compose)

    def test_handles_null_deploy_without_500(self, tmp_path):
        """Defense-in-depth: `deploy: null`.

        The pre-fix code was already safe at this level via
        `deploy = svc_def.get("deploy") or {}` — None is falsy and falls
        through to `{}`. This test locks the behavior in so a future
        refactor that drops the `or {}` short-circuit (e.g. switching to
        explicit isinstance gating without the falsy fallback) cannot
        reintroduce a 500 here.
        """
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(
            "services:\n  svc:\n    image: test\n"
            "    deploy: null\n"
        )
        # Should not raise.
        _scan_compose_content(compose)


# --- skip_root_user_check flag isolation ---


class TestScanComposeSkipRootUserCheck:
    """Direct unit tests for the skip_root_user_check parameter that permits
    built-in extensions (e.g. openclaw, which uses `user: "0:0"` to perform
    init-time chown before dropping privileges via setpriv) to declare a
    root user, while user/library extensions cannot. Regression guard for
    the openclaw init-time chown + setpriv pattern."""

    _ROOT_COMPOSE = (
        'services:\n  svc:\n    image: test\n    user: "0:0"\n'
    )

    def test_builtin_with_root_user_accepted(self, tmp_path):
        """A built-in extension with user: 0:0 (init-time chown + setpriv
        pattern, e.g. openclaw) must be accepted via
        skip_root_user_check=True. Regression guard: built-ins with
        `user: '0:0'` must be accepted when skip_root_user_check=True.
        """
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(self._ROOT_COMPOSE)
        # Should not raise
        _scan_compose_content(compose, skip_root_user_check=True)

    def test_user_extension_with_root_user_rejected(self, tmp_path):
        """User/library extensions (skip_root_user_check defaulting to False)
        still reject user: "0:0". Regression guard to ensure the
        new parameter doesn't accidentally weaken security for non-built-ins.
        """
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(self._ROOT_COMPOSE)
        with pytest.raises(HTTPException) as exc:
            _scan_compose_content(compose)
        assert exc.value.status_code == 400
        assert "runs as root" in exc.value.detail

    def test_privileged_still_blocked_when_skipped(self, tmp_path):
        """Other security checks remain active when skip_root_user_check=True;
        a built-in cannot smuggle in `privileged: true` under the root-user
        exemption.
        """
        from routers.extensions import _scan_compose_content
        compose = tmp_path / "compose.yaml"
        compose.write_text(
            'services:\n  svc:\n    image: test\n    user: "0:0"\n'
            "    privileged: true\n",
        )
        with pytest.raises(HTTPException) as exc:
            _scan_compose_content(compose, skip_root_user_check=True)
        assert "privileged" in exc.value.detail


# --- Size quota enforcement ---


class TestInstallSizeQuota:

    def test_install_rejects_oversized_extension(
        self, test_client, monkeypatch, tmp_path,
    ):
        """400 when extension exceeds 50MB size limit."""
        lib_dir = _setup_library_ext(tmp_path, "huge-ext")
        # Write a file that exceeds the limit
        big_file = lib_dir / "huge-ext" / "big.bin"
        big_file.write_bytes(b"\x00" * (50 * 1024 * 1024 + 1))
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/huge-ext/install",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "50MB" in resp.json()["detail"]


# --- Extension lifecycle status (stopped / health-based) ---


class TestExtensionLifecycleStatus:

    def test_user_extension_enabled_and_healthy(self, test_client, monkeypatch, tmp_path):
        """User extension with compose.yaml + healthy container → enabled."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text(_SAFE_COMPOSE)
        (ext_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "my-ext", "name": "My Ext", "port": 8080,
                         "health": "/health"},
        }))

        catalog = [_make_catalog_ext("my-ext", "My Extension")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)

        mock_svc = _make_service_status("my-ext", "healthy")
        with patch("user_extensions.get_user_services_cached",
                   return_value={"my-ext": {"host": "my-ext", "port": 8080,
                                             "health": "/health", "name": "My Ext"}}):
            with patch("helpers.get_all_services", new_callable=AsyncMock,
                       return_value=[]):
                with patch("helpers.check_service_health", new_callable=AsyncMock,
                           return_value=mock_svc):
                    resp = test_client.get(
                        "/api/extensions/catalog",
                        headers=test_client.auth_headers,
                    )

        assert resp.status_code == 200
        ext = resp.json()["extensions"][0]
        assert ext["status"] == "enabled"

    def test_user_extension_enabled_but_unhealthy(self, test_client, monkeypatch, tmp_path):
        """User extension with compose.yaml + unhealthy container → stopped."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text(_SAFE_COMPOSE)
        (ext_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "my-ext", "name": "My Ext", "port": 8080,
                         "health": "/health"},
        }))

        catalog = [_make_catalog_ext("my-ext", "My Extension")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)

        mock_svc = _make_service_status("my-ext", "down")
        with patch("user_extensions.get_user_services_cached",
                   return_value={"my-ext": {"host": "my-ext", "port": 8080,
                                             "health": "/health", "name": "My Ext"}}):
            with patch("helpers.get_all_services", new_callable=AsyncMock,
                       return_value=[]):
                with patch("helpers.check_service_health", new_callable=AsyncMock,
                           return_value=mock_svc):
                    resp = test_client.get(
                        "/api/extensions/catalog",
                        headers=test_client.auth_headers,
                    )

        assert resp.status_code == 200
        ext = resp.json()["extensions"][0]
        assert ext["status"] == "stopped"

    def test_user_extension_http_unhealthy_returns_unhealthy(self, test_client, monkeypatch, tmp_path):
        """User extension with compose.yaml + HTTP 4xx/5xx health → unhealthy."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text(_SAFE_COMPOSE)
        (ext_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "my-ext", "name": "My Ext", "port": 8080,
                         "health": "/health"},
        }))

        catalog = [_make_catalog_ext("my-ext", "My Extension")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)

        mock_svc = _make_service_status("my-ext", "unhealthy")
        with patch("user_extensions.get_user_services_cached",
                   return_value={"my-ext": {"host": "my-ext", "port": 8080,
                                             "health": "/health", "name": "My Ext"}}):
            with patch("helpers.get_all_services", new_callable=AsyncMock,
                       return_value=[]):
                with patch("helpers.check_service_health", new_callable=AsyncMock,
                           return_value=mock_svc):
                    resp = test_client.get(
                        "/api/extensions/catalog",
                        headers=test_client.auth_headers,
                    )

        assert resp.status_code == 200
        data = resp.json()
        ext = data["extensions"][0]
        assert ext["status"] == "unhealthy"
        # Unhealthy counts toward "installed" and has its own summary bucket
        assert data["summary"]["unhealthy"] == 1
        assert data["summary"]["installed"] == 1
        assert data["summary"]["stopped"] == 0

    def test_user_extension_disabled_unchanged(self, test_client, monkeypatch, tmp_path):
        """User extension with compose.yaml.disabled → disabled (unchanged)."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)

        catalog = [_make_catalog_ext("my-ext", "My Extension")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)

        with patch("user_extensions.get_user_services_cached",
                   return_value={}):
            with patch("helpers.get_all_services", new_callable=AsyncMock,
                       return_value=[]):
                resp = test_client.get(
                    "/api/extensions/catalog",
                    headers=test_client.auth_headers,
                )

        assert resp.status_code == 200
        ext = resp.json()["extensions"][0]
        assert ext["status"] == "disabled"

    def test_core_service_status_unchanged(self, test_client, monkeypatch, tmp_path):
        """Core service healthy → enabled, unhealthy → disabled (unchanged)."""
        catalog = [_make_catalog_ext("core-svc", "Core Service")]
        services = {"core-svc": {"host": "localhost", "port": 8080, "name": "Core"}}
        _patch_extensions_config(monkeypatch, catalog, services, tmp_path=tmp_path)

        mock_svc = _make_service_status("core-svc", "healthy")
        with patch("user_extensions.get_user_services_cached",
                   return_value={}):
            with patch("helpers.get_all_services", new_callable=AsyncMock,
                       return_value=[mock_svc]):
                resp = test_client.get(
                    "/api/extensions/catalog",
                    headers=test_client.auth_headers,
                )

        assert resp.status_code == 200
        ext = resp.json()["extensions"][0]
        assert ext["status"] == "enabled"

    def test_catalog_includes_user_extension_health(self, test_client, monkeypatch, tmp_path):
        """Catalog response includes 'stopped' in summary counts."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text(_SAFE_COMPOSE)

        catalog = [_make_catalog_ext("my-ext", "My Extension")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)

        # No health data → stopped
        with patch("user_extensions.get_user_services_cached",
                   return_value={}):
            with patch("helpers.get_all_services", new_callable=AsyncMock,
                       return_value=[]):
                resp = test_client.get(
                    "/api/extensions/catalog",
                    headers=test_client.auth_headers,
                )

        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["stopped"] == 1
        assert summary["installed"] == 1

    def test_enable_stopped_extension(self, test_client, monkeypatch, tmp_path):
        """Enable when compose.yaml exists (stopped) → starts without rename."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["action"] == "enabled"
        # compose.yaml should still exist (not renamed)
        assert (user_dir / "my-ext" / "compose.yaml").exists()

    def test_enable_stopped_writes_error_progress_on_agent_failure(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Enable-stopped path writes error progress with restart guidance on agent failure."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)
        monkeypatch.setattr("routers.extensions._call_agent",
                            lambda action, sid: False)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        assert resp.json()["restart_required"] is True

        progress_file = Path(tmp_path) / "extension-progress" / "my-ext.json"
        assert progress_file.exists(), "enable-stopped path must write progress on agent failure"
        data = json.loads(progress_file.read_text())
        assert data["status"] == "error"
        assert "ods restart" in data["error"]

    def test_install_error_progress_includes_restart_guidance(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Install failure-path error message contains 'ods restart' actionable guidance."""
        lib_dir = _setup_library_ext(tmp_path, "my-ext")
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)
        monkeypatch.setattr("routers.extensions._call_agent_install",
                            lambda sid: False)

        resp = test_client.post(
            "/api/extensions/my-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        progress_file = Path(tmp_path) / "extension-progress" / "my-ext.json"
        assert progress_file.exists()
        data = json.loads(progress_file.read_text())
        assert data["status"] == "error"
        assert "ods restart" in data["error"]

    def test_enable_stopped_rejects_malicious_compose(self, test_client, monkeypatch, tmp_path):
        """Enable stopped ext with malicious compose.yaml → 400."""
        bad_compose = "services:\n  svc:\n    image: test\n    privileged: true\n"
        user_dir = tmp_path / "user"
        user_dir.mkdir(exist_ok=True)
        ext_dir = user_dir / "bad-ext"
        ext_dir.mkdir(exist_ok=True)
        (ext_dir / "compose.yaml").write_text(bad_compose)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/bad-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "privileged" in resp.json()["detail"]

    def test_stale_started_unhealthy(self, monkeypatch, tmp_path):
        """Stale 'started' progress + container reporting unhealthy → 'unhealthy'.

        Covers fork issue #485 (and the L194 branch added by merged PR #1037):
        when the installer wrote ``status="started"`` more than 5 min ago
        (i.e., past the 300s recency window in _compute_extension_status),
        the progress entry must NOT keep the catalog stuck on "installing".
        Instead the user-extension health-check branch must run and surface
        the container's actual ServiceStatus — here, "unhealthy".

        The progress timestamp is computed as ``now - 305s`` rather than a
        fixed past date because ``_read_progress`` suppresses any
        non-error progress older than 3600s; a fixed date would silently
        return None and exercise the wrong path (the "no progress at all"
        case rather than the "stale started progress" case).
        """
        from datetime import datetime, timedelta, timezone

        from routers.extensions import _compute_extension_status

        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text(_SAFE_COMPOSE)

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
        monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        # 305s old: past the 300s "started"-recency window in
        # _compute_extension_status, but well within _read_progress's
        # 3600s general-staleness ceiling, so the progress is read but
        # ignored and the user-ext health branch runs.
        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=305)).isoformat()
        progress_data = {
            "service_id": "my-ext",
            "status": "started",
            "phase_label": "Service started",
            "error": None,
            "started_at": stale_ts,
            "updated_at": stale_ts,
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        ext = _make_catalog_ext("my-ext")
        services_by_id = {"my-ext": _make_service_status("my-ext", "unhealthy")}
        status = _compute_extension_status(ext, services_by_id)
        assert status == "unhealthy"


# --- Symlink handling ---


class TestSymlinkHandling:

    def test_copytree_safe_skips_symlinks(self, tmp_path):
        """_copytree_safe skips symlinks in source directory."""
        if os.name == "nt" and not can_create_symlinks(tmp_path):
            pytest.skip("Windows symlink creation requires Developer Mode or administrator privileges")
        from routers.extensions import _copytree_safe

        src = tmp_path / "src"
        src.mkdir()
        (src / "real.txt").write_text("real content")
        (src / "link.txt").symlink_to(src / "real.txt")

        dst = tmp_path / "dst"
        _copytree_safe(src, dst)

        assert (dst / "real.txt").exists()
        assert not (dst / "link.txt").exists()

    def test_enable_stopped_rejects_symlinked_compose(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Enable stopped ext rejects a compose.yaml that is a symlink."""
        if os.name == "nt" and not can_create_symlinks(tmp_path):
            pytest.skip("Windows symlink creation requires Developer Mode or administrator privileges")
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        # Create a real file and symlink compose.yaml to it
        real_compose = tmp_path / "real-compose.yaml"
        real_compose.write_text(_SAFE_COMPOSE)
        (ext_dir / "compose.yaml").symlink_to(real_compose)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "symlink" in resp.json()["detail"]

    def test_enable_rejects_symlinked_compose(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Enable rejects a compose.yaml.disabled that is a symlink."""
        if os.name == "nt" and not can_create_symlinks(tmp_path):
            pytest.skip("Windows symlink creation requires Developer Mode or administrator privileges")
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        # Create a real file and symlink the .disabled to it
        real_compose = tmp_path / "real-compose.yaml"
        real_compose.write_text(_SAFE_COMPOSE)
        (ext_dir / "compose.yaml.disabled").symlink_to(real_compose)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/enable",
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 400
        assert "symlink" in resp.json()["detail"]


# --- Purge extension data ---


class TestPurgeExtensionData:

    def test_purge_happy_path(self, test_client, monkeypatch, tmp_path):
        """Purge succeeds for disabled extension with existing data dir."""
        _patch_mutation_config(monkeypatch, tmp_path)
        data_dir = tmp_path / "my-ext"
        data_dir.mkdir()
        (data_dir / "some-file.db").write_text("data")

        with patch("routers.extensions._extensions_lock", return_value=contextlib.nullcontext()), \
             patch("helpers.dir_size_gb", return_value=1.5):
            resp = test_client.request(
                "DELETE", "/api/extensions/my-ext/data",
                headers=test_client.auth_headers,
                json={"confirm": True},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "my-ext"
        assert data["action"] == "purged"
        assert data["size_gb_freed"] == 1.5
        assert not data_dir.exists()

    def test_purge_unlinks_progress_file(self, test_client, monkeypatch, tmp_path):
        """Purge also deletes the per-service install-progress entry so the UI
        does not keep showing a stale 'installing' status."""
        _patch_mutation_config(monkeypatch, tmp_path)
        data_dir = tmp_path / "my-ext"
        data_dir.mkdir()
        (data_dir / "some-file.db").write_text("data")
        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        progress_file = progress_dir / "my-ext.json"
        progress_file.write_text(
            '{"service_id": "my-ext", "status": "started",'
            ' "phase_label": "stale", "error": null,'
            ' "started_at": "2026-04-10T00:00:00+00:00",'
            ' "updated_at": "2026-04-10T00:00:00+00:00"}'
        )

        with patch("routers.extensions._extensions_lock", return_value=contextlib.nullcontext()), \
             patch("helpers.dir_size_gb", return_value=0.1):
            resp = test_client.request(
                "DELETE", "/api/extensions/my-ext/data",
                headers=test_client.auth_headers,
                json={"confirm": True},
            )

        assert resp.status_code == 200
        assert not progress_file.exists(), "purge must unlink the progress file"

    def test_purge_400_when_enabled_builtin(self, test_client, monkeypatch, tmp_path):
        """400 when extension is still enabled (compose.yaml in built-in dir)."""
        _patch_mutation_config(monkeypatch, tmp_path)
        # Create compose.yaml in the built-in extensions dir
        builtin_dir = tmp_path / "builtin" / "my-ext"
        builtin_dir.mkdir(parents=True)
        (builtin_dir / "compose.yaml").write_text("version: '3'")
        # Also need a data dir to get past later checks
        data_dir = tmp_path / "my-ext"
        data_dir.mkdir()

        with patch("routers.extensions._extensions_lock", return_value=contextlib.nullcontext()):
            resp = test_client.request(
                "DELETE", "/api/extensions/my-ext/data",
                headers=test_client.auth_headers,
                json={"confirm": True},
            )

        assert resp.status_code == 400
        assert "still enabled" in resp.json()["detail"]

    def test_purge_400_when_enabled_user(self, test_client, monkeypatch, tmp_path):
        """400 when extension is still enabled (compose.yaml in user dir)."""
        user_dir = _setup_user_ext(tmp_path, "my-ext", enabled=True)
        _patch_mutation_config(monkeypatch, tmp_path, user_dir=user_dir)

        with patch("routers.extensions._extensions_lock", return_value=contextlib.nullcontext()):
            resp = test_client.request(
                "DELETE", "/api/extensions/my-ext/data",
                headers=test_client.auth_headers,
                json={"confirm": True},
            )

        assert resp.status_code == 400
        assert "still enabled" in resp.json()["detail"]

    def test_purge_403_core_service(self, test_client, monkeypatch, tmp_path):
        """403 when trying to purge a core service."""
        _patch_mutation_config(monkeypatch, tmp_path)

        resp = test_client.request(
            "DELETE", "/api/extensions/open-webui/data",
            headers=test_client.auth_headers,
            json={"confirm": True},
        )

        assert resp.status_code == 403
        assert "always-on service" in resp.json()["detail"].lower()

    def test_purge_404_invalid_id(self, test_client, monkeypatch, tmp_path):
        """404 for service_id that fails regex validation."""
        _patch_mutation_config(monkeypatch, tmp_path)

        for bad_id in ["..etc", ".hidden", "UPPERCASE", "-starts-dash"]:
            resp = test_client.request(
                "DELETE", f"/api/extensions/{bad_id}/data",
                headers=test_client.auth_headers,
                json={"confirm": True},
            )
            assert resp.status_code == 404, f"Expected 404 for: {bad_id}"

    def test_purge_404_no_data_dir(self, test_client, monkeypatch, tmp_path):
        """404 when valid ID but no data directory exists."""
        _patch_mutation_config(monkeypatch, tmp_path)

        with patch("routers.extensions._extensions_lock", return_value=contextlib.nullcontext()):
            resp = test_client.request(
                "DELETE", "/api/extensions/my-ext/data",
                headers=test_client.auth_headers,
                json={"confirm": True},
            )

        assert resp.status_code == 404
        assert "No data directory" in resp.json()["detail"]

    def test_purge_400_confirm_false(self, test_client, monkeypatch, tmp_path):
        """400 when data exists but confirm is false."""
        _patch_mutation_config(monkeypatch, tmp_path)
        data_dir = tmp_path / "my-ext"
        data_dir.mkdir()

        with patch("routers.extensions._extensions_lock", return_value=contextlib.nullcontext()):
            resp = test_client.request(
                "DELETE", "/api/extensions/my-ext/data",
                headers=test_client.auth_headers,
                json={"confirm": False},
            )

        assert resp.status_code == 400
        assert "Confirmation required" in resp.json()["detail"]
        # Data dir should still exist
        assert data_dir.exists()

    def test_purge_path_traversal(self, test_client, monkeypatch, tmp_path):
        """Path traversal attempts are blocked by regex or path check."""
        _patch_mutation_config(monkeypatch, tmp_path)

        resp = test_client.request(
            "DELETE", "/api/extensions/..%2fetc/data",
            headers=test_client.auth_headers,
            json={"confirm": True},
        )
        # Should fail at regex or Starlette routing level
        assert resp.status_code in (404, 422)

    def test_purge_requires_auth(self, test_client):
        """DELETE /api/extensions/{id}/data without auth → 401."""
        resp = test_client.request(
            "DELETE", "/api/extensions/my-ext/data",
            json={"confirm": True},
        )
        assert resp.status_code == 401


# --- Orphaned storage ---


class TestOrphanedStorage:

    def test_orphaned_requires_auth(self, test_client):
        """GET /api/storage/orphaned without auth → 401."""
        resp = test_client.get("/api/storage/orphaned")
        assert resp.status_code == 401

    def test_orphaned_empty_data_dir(self, test_client, monkeypatch, tmp_path):
        """Empty data dir returns empty orphaned list."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr("routers.extensions.DATA_DIR", str(data_dir))
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        resp = test_client.get(
            "/api/storage/orphaned",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["orphaned"] == []
        assert data["total_gb"] == 0

    def test_orphaned_nonexistent_data_dir(self, test_client, monkeypatch, tmp_path):
        """Non-existent data dir returns empty orphaned list."""
        monkeypatch.setattr("routers.extensions.DATA_DIR",
                            str(tmp_path / "nonexistent"))
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        resp = test_client.get(
            "/api/storage/orphaned",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["orphaned"] == []
        assert data["total_gb"] == 0

    def test_orphaned_excludes_known_services(self, test_client, monkeypatch, tmp_path):
        """Dirs matching SERVICES keys are not listed as orphaned."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "known-svc").mkdir()
        monkeypatch.setattr("routers.extensions.DATA_DIR", str(data_dir))
        monkeypatch.setattr("routers.extensions.SERVICES",
                            {"known-svc": {"host": "localhost", "port": 8080}})

        with patch("helpers.dir_size_gb", return_value=2.0):
            resp = test_client.get(
                "/api/storage/orphaned",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["orphaned"] == []
        assert data["total_gb"] == 0

    def test_orphaned_excludes_system_dirs(self, test_client, monkeypatch, tmp_path):
        """System dirs (models, config, etc.) are not listed as orphaned."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        for name in ("models", "config", "user-extensions", "extensions-library"):
            (data_dir / name).mkdir()
        monkeypatch.setattr("routers.extensions.DATA_DIR", str(data_dir))
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        with patch("helpers.dir_size_gb", return_value=1.0):
            resp = test_client.get(
                "/api/storage/orphaned",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["orphaned"] == []
        assert data["total_gb"] == 0

    def test_orphaned_includes_unknown_dirs(self, test_client, monkeypatch, tmp_path):
        """Dirs not in SERVICES or system_dirs are listed as orphaned."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "mystery-data").mkdir()
        (data_dir / "leftover-ext").mkdir()
        monkeypatch.setattr("routers.extensions.DATA_DIR", str(data_dir))
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        with patch("helpers.dir_size_gb", return_value=3.0):
            resp = test_client.get(
                "/api/storage/orphaned",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["orphaned"]) == 2
        names = [o["name"] for o in data["orphaned"]]
        assert "mystery-data" in names
        assert "leftover-ext" in names
        assert data["orphaned"][0]["size_gb"] == 3.0
        assert data["total_gb"] == 6.0

    def test_orphaned_skips_files(self, test_client, monkeypatch, tmp_path):
        """Regular files in data dir are not listed."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "some-file.txt").write_text("not a directory")
        (data_dir / "orphan-dir").mkdir()
        monkeypatch.setattr("routers.extensions.DATA_DIR", str(data_dir))
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        with patch("helpers.dir_size_gb", return_value=0.5):
            resp = test_client.get(
                "/api/storage/orphaned",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["orphaned"]) == 1
        assert data["orphaned"][0]["name"] == "orphan-dir"
        assert data["total_gb"] == 0.5

# --- Install progress tracking ---


class TestInstallProgress:

    def test_progress_endpoint_no_progress(self, test_client, monkeypatch, tmp_path):
        """GET progress when no file exists → idle."""
        _patch_mutation_config(monkeypatch, tmp_path)

        resp = test_client.get(
            "/api/extensions/my-ext/progress",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["service_id"] == "my-ext"
        assert data["status"] == "idle"

    def test_progress_endpoint_during_install(self, test_client, monkeypatch, tmp_path):
        """GET progress with active progress file → returns data."""
        _patch_mutation_config(monkeypatch, tmp_path)

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        progress_data = {
            "service_id": "my-ext",
            "status": "pulling",
            "phase_label": "Downloading image...",
            "error": None,
            "started_at": "2026-04-06T10:00:00+00:00",
            "updated_at": "2026-04-06T10:00:05+00:00",
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        resp = test_client.get(
            "/api/extensions/my-ext/progress",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pulling"
        assert data["phase_label"] == "Downloading image..."

    def test_status_installing_when_progress_pulling(self, monkeypatch, tmp_path):
        """Progress file with status 'pulling' → _compute_extension_status returns 'installing'."""
        from routers.extensions import _compute_extension_status

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", tmp_path / "user")
        monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        progress_data = {
            "service_id": "my-ext",
            "status": "pulling",
            "phase_label": "Downloading image...",
            "error": None,
            "started_at": now,
            "updated_at": now,
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        ext = _make_catalog_ext("my-ext")
        status = _compute_extension_status(ext, {})
        assert status == "installing"

    def test_status_installing_when_progress_starting(self, monkeypatch, tmp_path):
        """Progress file with status 'starting' → _compute_extension_status returns 'installing'."""
        from routers.extensions import _compute_extension_status

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", tmp_path / "user")
        monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        progress_data = {
            "service_id": "my-ext",
            "status": "starting",
            "phase_label": "Starting container...",
            "error": None,
            "started_at": now,
            "updated_at": now,
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        ext = _make_catalog_ext("my-ext")
        status = _compute_extension_status(ext, {})
        assert status == "installing"

    def test_status_setting_up_when_progress_setup_hook(self, monkeypatch, tmp_path):
        """Progress file with status 'setup_hook' → returns 'setting_up'."""
        from routers.extensions import _compute_extension_status

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", tmp_path / "user")
        monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        progress_data = {
            "service_id": "my-ext",
            "status": "setup_hook",
            "phase_label": "Running setup...",
            "error": None,
            "started_at": now,
            "updated_at": now,
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        ext = _make_catalog_ext("my-ext")
        status = _compute_extension_status(ext, {})
        assert status == "setting_up"

    def test_status_error_when_progress_error(self, monkeypatch, tmp_path):
        """Progress file with status 'error' → returns 'error'."""
        from routers.extensions import _compute_extension_status

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", tmp_path / "user")
        monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        progress_data = {
            "service_id": "my-ext",
            "status": "error",
            "phase_label": "Installation failed",
            "error": "something went wrong",
            "started_at": now,
            "updated_at": now,
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        ext = _make_catalog_ext("my-ext")
        status = _compute_extension_status(ext, {})
        assert status == "error"

    def test_status_cli_installed_for_oneshot_started_recent(self, monkeypatch, tmp_path):
        """One-shot extension (port=0) with recent 'started' progress →
        'cli_installed'. Regression: previously the install toast cycled
        through 'installing' / 'stopped' because there is no healthcheck
        for a CLI-only container that exits 0 after init."""
        from routers.extensions import _compute_extension_status

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", tmp_path / "user")
        monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        progress_data = {
            "service_id": "aider",
            "status": "started",
            "phase_label": "Service started",
            "error": None,
            "started_at": now,
            "updated_at": now,
        }
        (progress_dir / "aider.json").write_text(json.dumps(progress_data))

        ext = _make_catalog_ext("aider")
        ext["port"] = 0  # one-shot CLI extension marker
        ext["startup_check"] = False
        status = _compute_extension_status(ext, {})
        assert status == "cli_installed"

    def test_status_cli_installed_for_oneshot_user_dir_compose(self, monkeypatch, tmp_path):
        """Steady-state: a one-shot extension (port=0) installed under
        USER_EXTENSIONS_DIR with compose.yaml present should remain
        'cli_installed' even when no recent progress file exists."""
        from routers.extensions import _compute_extension_status

        user_dir = tmp_path / "user"
        user_dir.mkdir()
        aider_dir = user_dir / "aider"
        aider_dir.mkdir()
        (aider_dir / "compose.yaml").write_text(
            "services:\n  aider:\n    image: paulgauthier/aider\n"
        )

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
        monkeypatch.setattr("routers.extensions.GPU_BACKEND", "nvidia")
        monkeypatch.setattr("routers.extensions.SERVICES", {})

        ext = _make_catalog_ext("aider")
        ext["port"] = 0  # one-shot CLI extension marker
        ext["startup_check"] = False
        status = _compute_extension_status(ext, {})
        assert status == "cli_installed"

    def test_stale_progress_ignored(self, monkeypatch, tmp_path):
        """Progress file >1 hour old → _read_progress returns None."""
        from routers.extensions import _read_progress

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        # Set updated_at to far in the past (well over 1 hour)
        progress_data = {
            "service_id": "my-ext",
            "status": "pulling",
            "phase_label": "Downloading image...",
            "error": None,
            "started_at": "2020-01-01T00:00:00+00:00",
            "updated_at": "2020-01-01T00:00:00+00:00",
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        result = _read_progress("my-ext")
        assert result is None

    def test_stale_error_progress_preserved(self, monkeypatch, tmp_path):
        """Stale progress file with status 'error' → _read_progress still returns it (not None)."""
        from routers.extensions import _read_progress

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        progress_data = {
            "service_id": "my-ext",
            "status": "error",
            "phase_label": "Installation failed",
            "error": "something went wrong",
            "started_at": "2020-01-01T00:00:00+00:00",
            "updated_at": "2020-01-01T00:00:00+00:00",
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        result = _read_progress("my-ext")
        assert result is not None
        assert result["status"] == "error"

    def test_progress_cleanup_removes_old_started(self, monkeypatch, tmp_path):
        """_cleanup_stale_progress() removes 'started' files >15 min old."""
        from routers.extensions import _cleanup_stale_progress

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))

        progress_dir = tmp_path / "extension-progress"
        progress_dir.mkdir()
        progress_data = {
            "service_id": "my-ext",
            "status": "started",
            "phase_label": "Service started",
            "error": None,
            "started_at": "2020-01-01T00:00:00+00:00",
            "updated_at": "2020-01-01T00:00:00+00:00",
        }
        (progress_dir / "my-ext.json").write_text(json.dumps(progress_data))

        _cleanup_stale_progress()

        assert not (progress_dir / "my-ext.json").exists()

    def test_install_returns_progress_endpoint(self, test_client, monkeypatch, tmp_path):
        """Install response includes progress_endpoint field."""
        lib_dir = _setup_library_ext(tmp_path, "my-ext")
        _patch_mutation_config(monkeypatch, tmp_path, lib_dir=lib_dir)

        resp = test_client.post(
            "/api/extensions/my-ext/install",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["progress_endpoint"] == "/api/extensions/my-ext/progress"


# --- Config sync (delegated to host agent) ---


class TestSyncExtensionConfig:
    """The dashboard-api container has /ods/config bind-mounted
    read-only, so _sync_extension_config must NOT touch the filesystem
    locally — it forwards to the host agent. End-to-end file-copy behaviour
    is covered by the host-agent wire test (TestSyncExtensionConfigWire)."""

    def test_delegates_to_host_agent(self, monkeypatch):
        """_sync_extension_config calls _call_agent_sync_config with the service id."""
        from routers import extensions as ext_mod

        calls = []

        def _fake(sid):
            calls.append(sid)
            return True

        monkeypatch.setattr(ext_mod, "_call_agent_sync_config", _fake)
        result = ext_mod._sync_extension_config("my-ext")

        assert calls == ["my-ext"]
        assert result is True

    def test_returns_false_on_agent_failure(self, monkeypatch):
        """Agent failure surfaces as a False return; caller decides what to do."""
        from routers import extensions as ext_mod

        monkeypatch.setattr(
            ext_mod, "_call_agent_sync_config", lambda _sid: False,
        )
        assert ext_mod._sync_extension_config("my-ext") is False

    def test_call_agent_sync_config_sends_post(self, monkeypatch):
        """The HTTP helper POSTs to /v1/extension/sync_config with bearer auth."""
        from routers import extensions as ext_mod

        captured = {}

        class _FakeResp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["method"] = req.get_method()
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = req.data
            return _FakeResp()

        monkeypatch.setattr(ext_mod, "AGENT_URL", "http://agent:7710")
        monkeypatch.setattr(ext_mod, "ODS_AGENT_KEY", "secret")
        monkeypatch.setattr(ext_mod.urllib.request, "urlopen", _fake_urlopen)

        assert ext_mod._call_agent_sync_config("my-ext") is True
        assert captured["url"] == "http://agent:7710/v1/extension/sync_config"
        assert captured["method"] == "POST"
        assert captured["auth"] == "Bearer secret"
        assert b'"service_id"' in captured["body"]
        assert b'"my-ext"' in captured["body"]


# --- Error progress ---


class TestWriteErrorProgress:

    def test_sets_error_status_on_existing_progress(self, monkeypatch, tmp_path):
        """Error progress overwrites status but preserves started_at."""
        from routers.extensions import _write_initial_progress, _write_error_progress

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))

        _write_initial_progress("my-ext")
        progress_file = tmp_path / "extension-progress" / "my-ext.json"
        initial = json.loads(progress_file.read_text())
        assert initial["status"] == "pulling"

        _write_error_progress("my-ext", "Host agent failed")
        updated = json.loads(progress_file.read_text())
        assert updated["status"] == "error"
        assert updated["error"] == "Host agent failed"
        assert updated["started_at"] == initial["started_at"]

    def test_creates_error_file_when_no_prior_progress(self, monkeypatch, tmp_path):
        """Error progress can be written even without prior progress file."""
        from routers.extensions import _write_error_progress

        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))

        _write_error_progress("my-ext", "Agent unreachable")
        progress_file = tmp_path / "extension-progress" / "my-ext.json"
        data = json.loads(progress_file.read_text())
        assert data["status"] == "error"
        assert data["error"] == "Agent unreachable"
        assert "phase_label" in data

# --- _activate_service: built-in (EXTENSIONS_DIR) branch ---


class TestActivateServiceBuiltinBranch:
    """_activate_service must resolve services from EXTENSIONS_DIR (built-in)
    when not present under USER_EXTENSIONS_DIR — required so templates can
    enable built-in extensions like n8n, tts, etc."""

    def test_activate_service_resolves_builtin_with_disabled_compose(
        self, monkeypatch, tmp_path,
    ):
        """Built-in extension with compose.yaml.disabled is renamed to compose.yaml."""
        from routers.extensions import _activate_service

        builtin_root = tmp_path / "builtin"
        user_root = tmp_path / "user"
        builtin_root.mkdir()
        user_root.mkdir()
        ext_dir = builtin_root / "fakesvc"
        ext_dir.mkdir()
        (ext_dir / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)

        monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_root)
        calls = []

        def _mock_compose_rename(action, service_id):
            calls.append((action, service_id))
            (ext_dir / "compose.yaml.disabled").rename(ext_dir / "compose.yaml")
            return True

        monkeypatch.setattr(
            "routers.extensions._call_agent_compose_rename",
            _mock_compose_rename,
        )

        result = _activate_service("fakesvc")

        assert result == {"id": "fakesvc", "action": "enabled"}
        assert calls == [("activate", "fakesvc")]
        assert (ext_dir / "compose.yaml").exists()
        assert not (ext_dir / "compose.yaml.disabled").exists()

    def test_activate_service_resolves_builtin_already_enabled(
        self, monkeypatch, tmp_path,
    ):
        """Built-in extension already enabled returns idempotent action without mutation."""
        from routers.extensions import _activate_service

        builtin_root = tmp_path / "builtin"
        user_root = tmp_path / "user"
        builtin_root.mkdir()
        user_root.mkdir()
        ext_dir = builtin_root / "fakesvc"
        ext_dir.mkdir()
        enabled_compose = ext_dir / "compose.yaml"
        enabled_compose.write_text(_SAFE_COMPOSE)

        monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_root)

        result = _activate_service("fakesvc")

        assert result == {"id": "fakesvc", "action": "already_enabled"}
        assert enabled_compose.exists()
        assert not (ext_dir / "compose.yaml.disabled").exists()

    def test_activate_service_user_dir_takes_precedence_over_builtin(
        self, monkeypatch, tmp_path,
    ):
        """When the same id exists in both, the user-installed copy wins."""
        from routers.extensions import _activate_service

        builtin_root = tmp_path / "builtin"
        user_root = tmp_path / "user"
        builtin_root.mkdir()
        user_root.mkdir()

        # User dir: disabled, expected to be activated
        user_ext = user_root / "fakesvc"
        user_ext.mkdir()
        (user_ext / "compose.yaml.disabled").write_text(_SAFE_COMPOSE)

        # Built-in: already enabled, must remain untouched
        builtin_ext = builtin_root / "fakesvc"
        builtin_ext.mkdir()
        builtin_compose = builtin_ext / "compose.yaml"
        builtin_compose.write_text(_SAFE_COMPOSE)

        monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_root)

        result = _activate_service("fakesvc")

        assert result == {"id": "fakesvc", "action": "enabled"}
        assert (user_ext / "compose.yaml").exists()
        assert not (user_ext / "compose.yaml.disabled").exists()
        # Built-in untouched
        assert builtin_compose.exists()


class TestAssertNotCoreAllowsBuiltins:
    """_assert_not_core blocks only the 4 always-on base-compose services."""

    @pytest.mark.parametrize("service_id", [
        "n8n", "tts", "whisper", "comfyui", "litellm", "openclaw",
        "perplexica", "searxng", "privacy-shield", "token-spy", "qdrant",
        "embeddings", "ape", "langfuse", "opencode", "hermes", "hermes-proxy",
    ])
    def test_assert_not_core_allows_builtin_extension(self, service_id):
        """Built-in extensions are toggleable and must not be blocked."""
        _assert_not_core(service_id)

    @pytest.mark.parametrize("service_id", [
        "llama-server", "open-webui", "dashboard", "dashboard-api",
    ])
    def test_assert_not_core_blocks_always_on(self, service_id):
        """Always-on base-compose services must raise 403."""
        with pytest.raises(HTTPException) as exc_info:
            _assert_not_core(service_id)
        assert exc_info.value.status_code == 403


def test_production_core_service_ids_include_hermes_services():
    """The production anti-shadowing allowlist must cover Hermes built-ins."""
    core_ids_path = Path(__file__).resolve().parents[4] / "config" / "core-service-ids.json"
    core_ids = set(json.loads(core_ids_path.read_text(encoding="utf-8")))

    assert {"hermes", "hermes-proxy"} <= core_ids


class TestCallAgentErrorNarrowing:
    """_call_agent swallows network errors but not programmer errors."""

    def test_call_agent_returns_false_on_urlerror(self, monkeypatch, caplog):
        """Network failures produce (False, warning) — callers rely on this."""
        import logging
        import urllib.error
        from routers import extensions as ext_module

        def _raise(*_args, **_kwargs):
            raise urllib.error.URLError("timeout")

        monkeypatch.setattr(ext_module.urllib.request, "urlopen", _raise)
        with caplog.at_level(logging.WARNING, logger="routers.extensions"):
            result = ext_module._call_agent("start", "svc-x")

        assert result is False
        assert any("Host agent unreachable" in r.message for r in caplog.records)

    def test_call_agent_reraises_non_network_errors(self, monkeypatch):
        """Programmer errors (e.g. AttributeError) must not be swallowed."""
        from routers import extensions as ext_module

        def _raise(*_args, **_kwargs):
            raise AttributeError("boom")

        monkeypatch.setattr(ext_module.urllib.request, "urlopen", _raise)
        with pytest.raises(AttributeError):
            ext_module._call_agent("start", "svc-x")

    def test_catalog_logs_when_cleanup_future_fails(
        self, test_client, monkeypatch, tmp_path, caplog,
    ):
        """Stale-progress cleanup failures are logged, not lost to fire-and-forget."""
        import logging

        catalog = [_make_catalog_ext("test-svc", "Test Service")]
        _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)

        def _boom():
            raise RuntimeError("cleanup exploded")

        monkeypatch.setattr(
            "routers.extensions._cleanup_stale_progress", _boom,
        )

        with caplog.at_level(logging.ERROR, logger="routers.extensions"):
            with patch("helpers.get_all_services", new_callable=AsyncMock,
                       return_value=[]):
                resp = test_client.get(
                    "/api/extensions/catalog",
                    headers=test_client.auth_headers,
                )

        assert resp.status_code == 200
        assert any(
            "stale-progress cleanup failed" in r.message for r in caplog.records
        )


def test_extensions_lock_falls_back_when_data_root_is_unwritable(
    tmp_path, monkeypatch,
):
    """Extension installs should still lock when /data itself is not writable."""
    from routers import extensions as ext_module

    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_text("not a directory", encoding="utf-8")
    fallback_lock = tmp_path / "config" / ".extensions-lock"
    monkeypatch.setattr(
        ext_module,
        "_extensions_lock_candidates",
        lambda: [blocked_parent / ".extensions-lock", fallback_lock],
    )

    with ext_module._extensions_lock():
        assert fallback_lock.exists()
