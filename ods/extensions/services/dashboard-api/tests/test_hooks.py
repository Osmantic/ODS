"""Tests for lifecycle hook resolution in ods-host-agent.py."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import yaml

# Import the host agent module from bin/ using importlib.
_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent"] = _mod
_spec.loader.exec_module(_mod)

_resolve_hook = _mod._resolve_hook
_validate_hook_path = _mod._validate_hook_path
_read_manifest = _mod._read_manifest
_check_bash_version = _mod._check_bash_version


# --- _resolve_hook ---


class TestResolveHook:

    def test_resolve_hook_from_hooks_map(self, tmp_path):
        """Hook resolved from hooks map in manifest."""
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()
        hook_script = ext_dir / "hooks" / "pre_start.sh"
        hook_script.parent.mkdir()
        hook_script.write_text("#!/bin/bash\necho pre_start")

        manifest = {
            "schema_version": "ods.services.v1",
            "service": {
                "id": "my-ext",
                "name": "My Extension",
                "port": 8080,
                "health": "/health",
                "hooks": {
                    "pre_start": "hooks/pre_start.sh",
                },
            },
        }
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))

        result = _resolve_hook(ext_dir, "pre_start")
        assert result is not None
        assert result.name == "pre_start.sh"

    def test_resolve_hook_fallback_to_setup_hook(self, tmp_path):
        """post_install falls back to setup_hook when hooks map is absent."""
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()
        setup_script = ext_dir / "setup.sh"
        setup_script.write_text("#!/bin/bash\necho setup")

        manifest = {
            "schema_version": "ods.services.v1",
            "service": {
                "id": "my-ext",
                "name": "My Extension",
                "port": 8080,
                "health": "/health",
                "setup_hook": "setup.sh",
            },
        }
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))

        result = _resolve_hook(ext_dir, "post_install")
        assert result is not None
        assert result.name == "setup.sh"

    def test_resolve_hook_no_fallback_for_non_post_install(self, tmp_path):
        """setup_hook fallback only applies to post_install, not other hooks."""
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()
        setup_script = ext_dir / "setup.sh"
        setup_script.write_text("#!/bin/bash\necho setup")

        manifest = {
            "schema_version": "ods.services.v1",
            "service": {
                "id": "my-ext",
                "name": "My Extension",
                "port": 8080,
                "health": "/health",
                "setup_hook": "setup.sh",
            },
        }
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))

        result = _resolve_hook(ext_dir, "pre_start")
        assert result is None

    def test_resolve_hook_hooks_map_wins_over_setup_hook(self, tmp_path):
        """hooks.post_install takes precedence over setup_hook."""
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()
        (ext_dir / "setup.sh").write_text("#!/bin/bash\necho old")
        (ext_dir / "new-setup.sh").write_text("#!/bin/bash\necho new")

        manifest = {
            "schema_version": "ods.services.v1",
            "service": {
                "id": "my-ext",
                "name": "My Extension",
                "port": 8080,
                "health": "/health",
                "setup_hook": "setup.sh",
                "hooks": {
                    "post_install": "new-setup.sh",
                },
            },
        }
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))

        result = _resolve_hook(ext_dir, "post_install")
        assert result is not None
        assert result.name == "new-setup.sh"

    def test_resolve_hook_path_traversal_blocked(self, tmp_path):
        """Hook path that escapes extension directory is rejected."""
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()

        manifest = {
            "schema_version": "ods.services.v1",
            "service": {
                "id": "my-ext",
                "name": "My Extension",
                "port": 8080,
                "health": "/health",
                "hooks": {
                    "pre_start": "../../../etc/passwd",
                },
            },
        }
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))

        result = _resolve_hook(ext_dir, "pre_start")
        assert result is None

    def test_resolve_hook_missing_file_returns_none(self, tmp_path):
        """Hook script that doesn't exist returns None."""
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()

        manifest = {
            "schema_version": "ods.services.v1",
            "service": {
                "id": "my-ext",
                "name": "My Extension",
                "port": 8080,
                "health": "/health",
                "hooks": {
                    "pre_start": "nonexistent.sh",
                },
            },
        }
        (ext_dir / "manifest.yaml").write_text(yaml.dump(manifest))

        result = _resolve_hook(ext_dir, "pre_start")
        assert result is None

    def test_resolve_hook_no_manifest(self, tmp_path):
        """Extension directory without manifest returns None."""
        ext_dir = tmp_path / "my-ext"
        ext_dir.mkdir()

        result = _resolve_hook(ext_dir, "pre_start")
        assert result is None


# --- _check_bash_version ---


class TestCheckBashVersion:

    def test_non_darwin_always_ok(self):
        """Non-Darwin platforms skip bash check."""
        with patch("ods_host_agent.platform.system", return_value="Linux"):
            ok, msg = _check_bash_version()
        assert ok is True
        assert msg == ""

    def test_darwin_with_modern_bash(self):
        """Darwin with bash 5.x passes."""
        from unittest.mock import MagicMock
        mock_result = MagicMock()
        mock_result.stdout = "GNU bash, version 5.2.15(1)-release"
        with patch("ods_host_agent.platform.system", return_value="Darwin"), \
             patch("ods_host_agent.subprocess.run", return_value=mock_result):
            ok, msg = _check_bash_version()
        assert ok is True

    def test_darwin_with_old_bash(self):
        """Darwin with bash 3.2 fails."""
        from unittest.mock import MagicMock
        mock_result = MagicMock()
        mock_result.stdout = "GNU bash, version 3.2.57(1)-release"
        with patch("ods_host_agent.platform.system", return_value="Darwin"), \
             patch("ods_host_agent.subprocess.run", return_value=mock_result):
            ok, msg = _check_bash_version()
        assert ok is False
        assert "too old" in msg


# --- Hook environment allowlist ---


class TestHookEnvAllowlist:

    def test_hook_env_does_not_leak(self):
        """Verify the hook env construction pattern only includes allowlisted vars."""
        # This is a structural test — verifying the pattern matches spec
        import inspect
        source = inspect.getsource(_mod.AgentHandler._execute_hook)
        # The hook_env dict should NOT contain os.environ spread
        assert "**os.environ" not in source
        assert "os.environ.copy()" not in source
        # Should contain the allowlisted keys
        assert "SERVICE_ID" in source
        assert "SERVICE_PORT" in source
        assert "SERVICE_DATA_DIR" in source
        assert "ODS_VERSION" in source
        assert "GPU_BACKEND" in source
        assert "HOOK_NAME" in source


# --- Langfuse manifest setup_hook structural guard ---


class TestLangfuseManifestHook:
    """Structural guard — langfuse manifest setup_hook + hook file must coexist.

    The langfuse postgres uid 70 install fix ships service.setup_hook +
    hooks/post_install.sh. If either drifts (file renamed, manifest field
    removed, hook deleted), _validate_hook_path returns None and
    _handle_install silently skips the hook — langfuse silently regresses
    to the broken Linux postgres uid mismatch behavior with no CI signal.
    This test catches that.
    """

    def test_langfuse_manifest_declares_post_install_hook(self):
        ext_dir = Path(__file__).resolve().parents[2] / "langfuse"
        manifest = yaml.safe_load((ext_dir / "manifest.yaml").read_text())
        setup_hook = manifest.get("service", {}).get("setup_hook")
        assert setup_hook == "hooks/post_install.sh", (
            "langfuse manifest must declare service.setup_hook = 'hooks/post_install.sh' "
            "(part of the langfuse postgres uid 70 install fix). "
            "If this field changed, update the hook file path or this test."
        )

    def test_langfuse_post_install_hook_file_exists(self):
        ext_dir = Path(__file__).resolve().parents[2] / "langfuse"
        hook_path = ext_dir / "hooks" / "post_install.sh"
        assert hook_path.is_file(), (
            f"langfuse hook file missing at {hook_path}. "
            "The langfuse postgres uid 70 install fix ships this file; "
            "if removed, langfuse silently regresses to broken Linux behavior."
        )


class TestGaiaManifestHook:
    """Structural guard for GAIA's native Linux bind-mount uid alignment."""

    def _ext_dir(self) -> Path:
        return Path(__file__).resolve().parents[3] / "library" / "services" / "gaia"

    def test_gaia_manifest_declares_post_install_hook(self):
        ext_dir = self._ext_dir()
        manifest = yaml.safe_load((ext_dir / "manifest.yaml").read_text())
        service = manifest.get("service", {})
        assert service.get("container_uid") == 10001
        assert service.get("setup_hook") == "hooks/post_install.sh"

    def test_gaia_post_install_hook_file_exists(self):
        hook_path = self._ext_dir() / "hooks" / "post_install.sh"
        assert hook_path.is_file()
