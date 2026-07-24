"""Tests for user extension manifest scanner."""

from pathlib import Path

import yaml

from user_extensions import (
    _reset_cache,
    get_user_services_cached,
    scan_user_extension_services,
)


def _write_manifest(ext_dir: Path, manifest: dict) -> None:
    """Write a manifest.yaml into the given extension directory."""
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))


def _make_manifest(service_id: str, port: int = 8080, health: str = "/health",
                   name: str | None = None, default_host: str = "badhost") -> dict:
    """Build a minimal manifest dict."""
    svc: dict = {"id": service_id, "port": port, "health": health,
                 "default_host": default_host}
    if name is not None:
        svc["name"] = name
    return {"schema_version": "ods.services.v1", "service": svc}


# --- scan_user_extension_services ---


class TestScanUserExtensions:

    def test_scan_empty_dir(self, tmp_path):
        """Empty directory returns empty dict."""
        d = tmp_path / "user"
        d.mkdir()
        assert scan_user_extension_services(d) == {}

    def test_scan_nonexistent_dir(self, tmp_path):
        """Non-existent directory returns empty dict."""
        assert scan_user_extension_services(tmp_path / "nope") == {}

    def test_scan_enabled_extension(self, tmp_path):
        """Extension with compose.yaml + manifest returns correct config."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        _write_manifest(ext_dir, _make_manifest("my-ext", port=9090,
                                                 health="/api/health",
                                                 name="My Extension"))
        (ext_dir / "compose.yaml").write_text("services:\n  my-ext:\n    image: test\n")

        result = scan_user_extension_services(user_dir)
        assert "my-ext" in result
        cfg = result["my-ext"]
        assert cfg["host"] == "my-ext"
        assert cfg["port"] == 9090
        assert cfg["health"] == "/api/health"
        assert cfg["name"] == "My Extension"

    def test_scan_disabled_extension_skipped(self, tmp_path):
        """Extension with compose.yaml.disabled only is skipped."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        _write_manifest(ext_dir, _make_manifest("my-ext"))
        (ext_dir / "compose.yaml.disabled").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        assert result == {}

    def test_scan_missing_manifest_skipped(self, tmp_path):
        """Extension without manifest.yaml is skipped."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        assert result == {}

    def test_scan_no_health_endpoint_included_with_empty_health(self, tmp_path):
        """Extension without health field is included with empty health."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        ext_dir.mkdir(parents=True)
        manifest = {"schema_version": "ods.services.v1",
                     "service": {"id": "my-ext", "port": 8080}}
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        assert "my-ext" in result
        assert result["my-ext"]["health"] == ""
        assert result["my-ext"]["port"] == 8080

    def test_scan_health_path_validation(self, tmp_path):
        """Reject paths with .., @, ?, #, and scheme prefixes."""
        user_dir = tmp_path / "user"
        bad_paths = [
            "/health/../etc/passwd",
            "/health@evil.com",
            "/health?cmd=exec",
            "/health#fragment",
            "http://evil.com/health",
            "https://evil.com/health",
        ]
        for i, bad_path in enumerate(bad_paths):
            ext_id = f"ext-{i}"
            ext_dir = user_dir / ext_id
            _write_manifest(ext_dir, _make_manifest(ext_id, health=bad_path))
            (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        assert result == {}, f"Expected all bad paths rejected, got: {list(result.keys())}"

    def test_scan_host_is_service_id(self, tmp_path):
        """Returned host must be the directory name, not manifest default_host."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        _write_manifest(ext_dir, _make_manifest("my-ext",
                                                 default_host="evil.attacker.com"))
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        assert result["my-ext"]["host"] == "my-ext"

    def test_scan_name_fallback(self, tmp_path):
        """Extension without name in manifest falls back to service_id."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        # Manifest with no name field
        manifest = {"schema_version": "ods.services.v1",
                     "service": {"id": "my-ext", "port": 8080, "health": "/health"}}
        (ext_dir).mkdir(parents=True)
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        assert result["my-ext"]["name"] == "my-ext"

    def test_scan_symlink_skipped(self, tmp_path):
        """Symlinked directories in user-extensions are skipped."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "real-ext"
        _write_manifest(ext_dir, _make_manifest("real-ext"))
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        (user_dir / "link-ext").symlink_to(ext_dir)

        result = scan_user_extension_services(user_dir)
        assert "real-ext" in result
        assert "link-ext" not in result

    def test_scan_invalid_service_id_skipped(self, tmp_path):
        """Directories with invalid service_id format are skipped."""
        user_dir = tmp_path / "user"
        for bad_name in ["UPPER", "-bad", "has spaces"]:
            ext_dir = user_dir / bad_name
            _write_manifest(ext_dir, _make_manifest("x", health="/health"))
            (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        assert result == {}

    def test_scan_default_health_type_is_http(self, tmp_path):
        """Extension without health_type defaults to http."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        _write_manifest(ext_dir, _make_manifest("my-ext", port=9090,
                                                 health="/api/health"))
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        cfg = result["my-ext"]
        assert cfg["health_type"] == "http"
        assert cfg["health_timeout"] == 5

    def test_scan_explicit_health_type_tcp(self, tmp_path):
        """Extension with health_type=tcp and empty health is included."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "tcp-ext"
        manifest = {
            "schema_version": "ods.services.v1",
            "service": {"id": "tcp-ext", "port": 1234, "health": "",
                         "health_type": "tcp", "health_timeout": 10},
        }
        _write_manifest(ext_dir, manifest)
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        cfg = result["tcp-ext"]
        assert cfg["health_type"] == "tcp"
        assert cfg["health_timeout"] == 10
        assert cfg["health"] == ""

    def test_scan_explicit_health_type_none(self, tmp_path):
        """Extension with health_type=none is included."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "cli-ext"
        manifest = {
            "schema_version": "ods.services.v1",
            "service": {"id": "cli-ext", "port": 0, "health": "",
                         "health_type": "none"},
        }
        _write_manifest(ext_dir, manifest)
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        cfg = result["cli-ext"]
        assert cfg["health_type"] == "none"
        assert cfg["health"] == ""

    def test_scan_invalid_health_type_rejected(self, tmp_path):
        """Extension with unsupported health_type is skipped."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "bad-ext"
        manifest = {
            "schema_version": "ods.services.v1",
            "service": {"id": "bad-ext", "port": 8080, "health": "/health",
                         "health_type": "grpc"},
        }
        _write_manifest(ext_dir, manifest)
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        result = scan_user_extension_services(user_dir)
        assert "bad-ext" not in result


# --- Caching ---


class TestCaching:

    def setup_method(self):
        _reset_cache()

    def teardown_method(self):
        _reset_cache()

    def test_cache_returns_same_result(self, tmp_path):
        """Second call within TTL returns cached result without rescanning."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        _write_manifest(ext_dir, _make_manifest("my-ext"))
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        r1 = get_user_services_cached(user_dir, ttl=60.0)
        assert "my-ext" in r1

        # Remove the extension directory — cache should still return old result
        import shutil
        shutil.rmtree(ext_dir)

        r2 = get_user_services_cached(user_dir, ttl=60.0)
        assert r2 == r1
        assert "my-ext" in r2

    def test_cache_ttl_expires(self, tmp_path, monkeypatch):
        """After TTL, cache rescans the directory."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        _write_manifest(ext_dir, _make_manifest("my-ext"))
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        r1 = get_user_services_cached(user_dir, ttl=0.0)
        assert "my-ext" in r1

        # Remove and call with ttl=0 (always expired)
        import shutil
        shutil.rmtree(ext_dir)

        r2 = get_user_services_cached(user_dir, ttl=0.0)
        assert r2 == {}

    def test_reset_cache(self, tmp_path):
        """_reset_cache() clears cached data."""
        user_dir = tmp_path / "user"
        ext_dir = user_dir / "my-ext"
        _write_manifest(ext_dir, _make_manifest("my-ext"))
        (ext_dir / "compose.yaml").write_text("services: {}\n")

        r1 = get_user_services_cached(user_dir, ttl=300.0)
        assert "my-ext" in r1

        _reset_cache()

        # Remove the extension
        import shutil
        shutil.rmtree(ext_dir)

        r2 = get_user_services_cached(user_dir, ttl=300.0)
        assert r2 == {}
