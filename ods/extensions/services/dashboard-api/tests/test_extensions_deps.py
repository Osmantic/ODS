"""Tests for dependency enrichment and auto_enable_deps in extensions portal."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from models import ServiceStatus


# --- Helpers ---


def _make_catalog_ext(ext_id, name="Test", depends_on=None, gpu_backends=None):
    return {
        "id": ext_id,
        "name": name,
        "description": f"Description for {name}",
        "category": "optional",
        "gpu_backends": gpu_backends or ["nvidia", "amd", "apple"],
        "compose_file": "compose.yaml",
        "depends_on": depends_on or [],
        "port": 8080,
        "external_port_default": 8080,
        "health_endpoint": "/health",
        "env_vars": [],
        "tags": [],
        "features": [],
    }


def _make_service_status(sid, status="healthy"):
    return ServiceStatus(
        id=sid, name=sid, port=8080, external_port=8080, status=status,
    )


def _patch_deps_config(monkeypatch, catalog, services=None,
                       gpu_backend="nvidia", tmp_path=None):
    """Apply standard patches for dependency tests."""
    monkeypatch.setattr("routers.extensions.EXTENSION_CATALOG", catalog)
    monkeypatch.setattr("routers.extensions.SERVICES", services or {})
    monkeypatch.setattr("routers.extensions.GPU_BACKEND", gpu_backend)
    monkeypatch.setattr("routers.extensions.CORE_SERVICE_IDS", frozenset({"llama-server"}))
    lib_dir = (tmp_path / "lib") if tmp_path else Path("/tmp/nonexistent-lib")
    user_dir = (tmp_path / "user") if tmp_path else Path("/tmp/nonexistent-user")
    monkeypatch.setattr("routers.extensions.EXTENSIONS_LIBRARY_DIR", lib_dir)
    monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
    monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR",
                        tmp_path / "ext" if tmp_path else Path("/tmp/nonexistent-ext"))
    monkeypatch.setattr("routers.extensions.DATA_DIR",
                        str(tmp_path or "/tmp/nonexistent"))


# --- Catalog dependency enrichment ---


class TestCatalogDependencies:

    def test_catalog_includes_depends_on(self, test_client, monkeypatch, tmp_path):
        """Catalog returns depends_on for each extension."""
        catalog = [_make_catalog_ext("svc-a", "A", depends_on=["svc-b"])]
        _patch_deps_config(monkeypatch, catalog, tmp_path=tmp_path)

        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[]):
            resp = test_client.get(
                "/api/extensions/catalog",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        ext = resp.json()["extensions"][0]
        assert ext["depends_on"] == ["svc-b"]

    def test_catalog_dependents_computed(self, test_client, monkeypatch, tmp_path):
        """Catalog computes reverse dependents."""
        catalog = [
            _make_catalog_ext("svc-a", "A", depends_on=["svc-b"]),
            _make_catalog_ext("svc-b", "B"),
        ]
        _patch_deps_config(monkeypatch, catalog, tmp_path=tmp_path)

        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[]):
            resp = test_client.get(
                "/api/extensions/catalog",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        exts = {e["id"]: e for e in resp.json()["extensions"]}
        assert exts["svc-b"]["dependents"] == ["svc-a"]
        assert exts["svc-a"]["dependents"] == []

    def test_catalog_dependency_status(self, test_client, monkeypatch, tmp_path):
        """Catalog includes dependency_status for each dep."""
        catalog = [
            _make_catalog_ext("svc-a", "A", depends_on=["svc-b"]),
            _make_catalog_ext("svc-b", "B"),
        ]
        services = {"svc-b": {"host": "localhost", "port": 8080, "name": "B"}}
        _patch_deps_config(monkeypatch, catalog, services, tmp_path=tmp_path)

        mock_svc = _make_service_status("svc-b", "healthy")
        with patch("helpers.get_all_services", new_callable=AsyncMock,
                   return_value=[mock_svc]):
            resp = test_client.get(
                "/api/extensions/catalog",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        ext_a = next(e for e in resp.json()["extensions"] if e["id"] == "svc-a")
        assert ext_a["dependency_status"]["svc-b"] == "enabled"


# --- Auto-enable deps ---


class TestAutoEnableDeps:

    def test_enable_missing_deps_returns_list(self, test_client, monkeypatch, tmp_path):
        """Enable with missing deps and auto_enable_deps=false returns 400 with dep list."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "svc-a"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml.disabled").write_text("services: {}")
        (ext_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {
                "id": "svc-a",
                "name": "A",
                "port": 8080,
                "health": "/health",
                "depends_on": ["svc-b"],
            },
        }))

        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
        monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR",
                            tmp_path / "ext")
        monkeypatch.setattr("routers.extensions.CORE_SERVICE_IDS", frozenset())
        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))

        resp = test_client.post(
            "/api/extensions/svc-a/enable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 400
        body = resp.json()["detail"]
        assert "svc-b" in body["missing_dependencies"]
        assert body["auto_enable_available"] is True

    def test_enable_auto_deps(self, test_client, monkeypatch, tmp_path):
        """Enable with auto_enable_deps=true enables deps first."""
        user_dir = tmp_path / "user"
        # Create dep extension
        dep_dir = user_dir / "svc-b"
        dep_dir.mkdir(parents=True)
        (dep_dir / "compose.yaml.disabled").write_text("services: {}")
        (dep_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "svc-b", "name": "B", "port": 8081, "health": "/health"},
        }))

        # Create target extension
        ext_dir = user_dir / "svc-a"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml.disabled").write_text("services: {}")
        (ext_dir / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {
                "id": "svc-a", "name": "A", "port": 8080, "health": "/health",
                "depends_on": ["svc-b"],
            },
        }))

        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
        monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR",
                            tmp_path / "ext")
        monkeypatch.setattr("routers.extensions.CORE_SERVICE_IDS", frozenset())
        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))

        with patch("routers.extensions._call_agent", return_value=True):
            resp = test_client.post(
                "/api/extensions/svc-a/enable?auto_enable_deps=true",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "svc-b" in body["enabled_services"]
        assert "svc-a" in body["enabled_services"]
        # Both compose files should be renamed
        assert (dep_dir / "compose.yaml").exists()
        assert (ext_dir / "compose.yaml").exists()

    def test_enable_auto_deps_circular_guard(self, test_client, monkeypatch, tmp_path):
        """Circular deps are caught and return 400."""
        user_dir = tmp_path / "user"

        for sid, deps in [("svc-a", ["svc-b"]), ("svc-b", ["svc-a"])]:
            d = user_dir / sid
            d.mkdir(parents=True, exist_ok=True)
            (d / "compose.yaml.disabled").write_text("services: {}")
            (d / "manifest.yaml").write_text(yaml.dump({
                "schema_version": "ods.services.v1",
                "service": {
                    "id": sid, "name": sid, "port": 8080, "health": "/health",
                    "depends_on": deps,
                },
            }))

        monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
        monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR",
                            tmp_path / "ext")
        monkeypatch.setattr("routers.extensions.CORE_SERVICE_IDS", frozenset())
        monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))

        with patch("routers.extensions._call_agent", return_value=True):
            resp = test_client.post(
                "/api/extensions/svc-a/enable?auto_enable_deps=true",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 400
        assert "Circular" in resp.json().get("detail", "")


# --- Built-in extension dependencies ---


def _setup_builtin_ext(tmp_path, sid, deps=None, enabled=False):
    """Create a built-in extension under tmp_path/ext with a manifest."""
    d = tmp_path / "ext" / sid
    d.mkdir(parents=True)
    compose_name = "compose.yaml" if enabled else "compose.yaml.disabled"
    (d / compose_name).write_text("services: {}")
    (d / "manifest.yaml").write_text(yaml.dump({
        "schema_version": "ods.services.v1",
        "service": {
            "id": sid, "name": sid, "port": 8080, "health": "/health",
            "depends_on": deps or [],
        },
    }))
    return d


def _patch_builtin_config(monkeypatch, tmp_path):
    user_dir = tmp_path / "user"
    user_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("routers.extensions.USER_EXTENSIONS_DIR", user_dir)
    monkeypatch.setattr("routers.extensions.EXTENSIONS_DIR", tmp_path / "ext")
    monkeypatch.setattr("routers.extensions.CORE_SERVICE_IDS", frozenset())
    monkeypatch.setattr("routers.extensions.DATA_DIR", str(tmp_path))


class TestBuiltinExtensionDeps:
    """Dependency resolution must read built-in extension manifests too.

    Built-in extensions live under EXTENSIONS_DIR (e.g. hermes-proxy with
    depends_on: [hermes]); enabling one with a disabled dependency, or
    disabling a dependency while a built-in dependent is enabled, breaks
    the merged compose project for the entire stack.
    """

    def test_enable_builtin_missing_deps_returns_list(
        self, test_client, monkeypatch, tmp_path,
    ):
        """Enable of a built-in with a disabled built-in dep → 400 + dep list."""
        _setup_builtin_ext(tmp_path, "svc-b", enabled=False)
        _setup_builtin_ext(tmp_path, "svc-a", deps=["svc-b"], enabled=False)
        _patch_builtin_config(monkeypatch, tmp_path)

        resp = test_client.post(
            "/api/extensions/svc-a/enable",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 400
        body = resp.json()["detail"]
        assert "svc-b" in body["missing_dependencies"]
        assert body["auto_enable_available"] is True

    def test_enable_builtin_auto_deps(self, test_client, monkeypatch, tmp_path):
        """auto_enable_deps=true activates the built-in dep first via host agent."""
        dep_dir = _setup_builtin_ext(tmp_path, "svc-b", enabled=False)
        ext_dir = _setup_builtin_ext(tmp_path, "svc-a", deps=["svc-b"], enabled=False)
        _patch_builtin_config(monkeypatch, tmp_path)

        rename_calls = []

        def _mock_compose_rename(action, service_id):
            rename_calls.append((action, service_id))
            d = dep_dir if service_id == "svc-b" else ext_dir
            (d / "compose.yaml.disabled").rename(d / "compose.yaml")
            return True

        with patch("routers.extensions._call_agent", return_value=True), \
             patch("routers.extensions._call_agent_hook", return_value=True), \
             patch("routers.extensions._call_agent_compose_rename",
                   side_effect=_mock_compose_rename):
            resp = test_client.post(
                "/api/extensions/svc-a/enable?auto_enable_deps=true",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled_services"] == ["svc-b", "svc-a"]
        assert rename_calls == [("activate", "svc-b"), ("activate", "svc-a")]
        assert (dep_dir / "compose.yaml").exists()
        assert (ext_dir / "compose.yaml").exists()

    def test_user_manifest_shadows_builtin(self, test_client, monkeypatch, tmp_path):
        """A user extension of the same id takes precedence over the built-in manifest."""
        # Built-in declares a dep that would be missing; the user copy declares none.
        _setup_builtin_ext(tmp_path, "svc-a", deps=["svc-missing"], enabled=False)
        _patch_builtin_config(monkeypatch, tmp_path)

        user_ext = tmp_path / "user" / "svc-a"
        user_ext.mkdir(parents=True)
        (user_ext / "compose.yaml.disabled").write_text("services: {}")
        (user_ext / "manifest.yaml").write_text(yaml.dump({
            "schema_version": "ods.services.v1",
            "service": {"id": "svc-a", "name": "A", "port": 8080, "health": "/health"},
        }))

        with patch("routers.extensions._call_agent", return_value=True), \
             patch("routers.extensions._call_agent_hook", return_value=True):
            resp = test_client.post(
                "/api/extensions/svc-a/enable",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        assert (user_ext / "compose.yaml").exists()
