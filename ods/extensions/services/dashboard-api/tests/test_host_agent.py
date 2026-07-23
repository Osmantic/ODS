"""Tests for ods-host-agent.py — _parse_mem_value and _iso_now."""

import hashlib
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
import threading
import time
import types
from pathlib import Path, PurePosixPath

import pytest

# Import the host agent module from bin/ using importlib.
# The module has an ``if __name__ == "__main__":`` guard so no server starts.
_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent"] = _mod
_spec.loader.exec_module(_mod)

_parse_mem_value = _mod._parse_mem_value
_iso_now = _mod._iso_now
_to_bash_path = _mod._to_bash_path
_resolve_agent_bind_addr = _mod._resolve_agent_bind_addr
_disable_conflicting_macos_bridge = _mod._disable_conflicting_macos_bridge
resolve_compose_flags = _mod.resolve_compose_flags
validate_core_recreate_ids = _mod.validate_core_recreate_ids
invalidate_compose_cache = _mod.invalidate_compose_cache
_post_install_core_recreate = _mod._post_install_core_recreate
_split_nmcli_terse = _mod._split_nmcli_terse
_request_server_shutdown = _mod._request_server_shutdown


@pytest.fixture(autouse=True)
def _isolate_opencode_config(monkeypatch, tmp_path):
    """Keep host-agent integration tests out of the user's OpenCode config."""
    config_dir = tmp_path / "isolated-home" / ".config" / "opencode"
    monkeypatch.setattr(
        _mod,
        "_opencode_config_paths",
        lambda: (config_dir / "opencode.json", config_dir / "config.json"),
    )


def can_create_symlinks(tmp_path: Path) -> bool:
    target = tmp_path / "symlink-target"
    link = tmp_path / "symlink-probe"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    return link.is_symlink()


@pytest.fixture
def host_agent_wire_client(monkeypatch):
    """Point the shared sync transport at a test server with isolated state."""
    import host_agent_client

    def reset_client():
        client = host_agent_client._sync_client
        if client is not None and not client.is_closed:
            client.close()
        host_agent_client._sync_client = None

    def configure(port, *, key="wire-test-secret"):
        reset_client()
        monkeypatch.setattr(
            host_agent_client,
            "AGENT_URL",
            f"http://127.0.0.1:{port}",
        )
        monkeypatch.setattr(
            host_agent_client,
            "_headers",
            lambda: {"Authorization": f"Bearer {key}"},
        )

    reset_client()
    yield configure
    reset_client()


class TestHostAgentShutdown:

    def test_signal_shutdown_runs_from_helper_thread(self, monkeypatch):
        calls = []

        class FakeServer:
            def shutdown(self):
                calls.append("shutdown")

        class FakeThread:
            def __init__(self, target, name=None, daemon=None):
                calls.append(("thread", name, daemon))
                self._target = target

            def start(self):
                calls.append("start")
                self._target()

        monkeypatch.setattr(_mod.threading, "Thread", FakeThread)

        _request_server_shutdown(FakeServer(), signum=15)

        assert ("thread", "ods-host-agent-shutdown", True) in calls
        assert "start" in calls
        assert "shutdown" in calls


class TestProgressWrites:

    def test_write_progress_retries_windows_replace_race(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod, "DATA_DIR", tmp_path)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append((src, dst))
            if len(calls) == 1:
                raise PermissionError("[WinError 5] Access is denied")
            return real_replace(src, dst)

        monkeypatch.setattr(_mod.os, "replace", flaky_replace)

        _mod._write_progress("aider", "pulling", "Downloading image...")

        progress = tmp_path / "extension-progress" / "aider.json"
        assert len(calls) == 2
        assert progress.exists()
        payload = json.loads(progress.read_text(encoding="utf-8"))
        assert payload["service_id"] == "aider"
        assert payload["status"] == "pulling"
        assert payload["phase_label"] == "Downloading image..."


class TestResolveAgentBindAddr:

    def test_explicit_bind_wins(self):
        assert _resolve_agent_bind_addr({"ODS_AGENT_BIND": "0.0.0.0"}, "Linux") == "0.0.0.0"
        assert _resolve_agent_bind_addr({"ODS_AGENT_BIND": "192.168.1.10"}, "Linux") == "192.168.1.10"

    def test_darwin_ipv6_wildcard_uses_ipv4_all_interfaces(self):
        assert _resolve_agent_bind_addr({"ODS_AGENT_BIND": "::"}, "Darwin") == "0.0.0.0"

    @pytest.mark.parametrize("system_name", ["Linux", "Windows"])
    def test_non_darwin_ipv6_wildcard_is_unchanged(self, system_name):
        assert _resolve_agent_bind_addr({"ODS_AGENT_BIND": "::"}, system_name) == "::"

    def test_desktop_platforms_default_loopback(self, monkeypatch):
        monkeypatch.setattr(_mod, "_detect_docker_network_gateway", lambda network: "172.18.0.1")
        monkeypatch.setattr(_mod, "_detect_docker_bridge_gateway", lambda: "172.17.0.1")

        assert _resolve_agent_bind_addr({}, "Windows") == "127.0.0.1"
        assert _resolve_agent_bind_addr({}, "Darwin") == "127.0.0.1"

    def test_linux_prefers_ods_network_gateway(self, monkeypatch):
        monkeypatch.setattr(_mod, "_detect_docker_network_gateway", lambda network: "172.18.0.1")
        monkeypatch.setattr(_mod, "_detect_docker_bridge_gateway", lambda: "172.17.0.1")

        assert _resolve_agent_bind_addr({}, "Linux") == "172.18.0.1"

    def test_linux_falls_back_to_bridge_gateway(self, monkeypatch):
        monkeypatch.setattr(_mod, "_detect_docker_network_gateway", lambda network: "")
        monkeypatch.setattr(_mod, "_detect_docker_bridge_gateway", lambda: "172.17.0.1")

        assert _resolve_agent_bind_addr({}, "Linux") == "172.17.0.1"

    def test_linux_falls_back_to_loopback(self, monkeypatch):
        monkeypatch.setattr(_mod, "_detect_docker_network_gateway", lambda network: "")
        monkeypatch.setattr(_mod, "_detect_docker_bridge_gateway", lambda: "")

        assert _resolve_agent_bind_addr({}, "Linux") == "127.0.0.1"


class TestMacosDirectBindBridgeCollision:

    @pytest.mark.parametrize(
        ("bind_addr", "gateway_addr"),
        [
            ("0.0.0.0", "192.168.106.1"),
            ("::", "192.168.106.1"),
            ("192.168.106.1", "192.168.106.1"),
        ],
    )
    @pytest.mark.parametrize(
        "label",
        ["com.ods.llm-bridge", "com.ods.host-agent-bridge"],
    )
    def test_darwin_direct_bind_boots_out_requested_bridge(
        self,
        monkeypatch,
        bind_addr,
        gateway_addr,
        label,
    ):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(_mod.os, "getuid", lambda: 501, raising=False)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _disable_conflicting_macos_bridge(
            {"ODS_MACOS_HOST_GATEWAY": gateway_addr},
            bind_addr,
            label,
        ) is True
        assert calls == [
            (
                ["launchctl", "bootout", f"gui/501/{label}"],
                {
                    "capture_output": True,
                    "text": True,
                    "timeout": 10,
                    "check": False,
                },
            ),
        ]

    def test_darwin_loopback_does_not_touch_launchctl(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")

        def unexpected_run(*_args, **_kwargs):
            raise AssertionError("launchctl must not run for loopback")

        monkeypatch.setattr(_mod.subprocess, "run", unexpected_run)

        assert _disable_conflicting_macos_bridge(
            {"ODS_MACOS_HOST_GATEWAY": "192.168.106.1"},
            "127.0.0.1",
            "com.ods.llm-bridge",
        ) is False

    @pytest.mark.parametrize("system_name", ["Linux", "Windows"])
    @pytest.mark.parametrize("bind_addr", ["0.0.0.0", "::", "192.168.106.1"])
    def test_non_darwin_does_not_touch_launchctl(
        self,
        monkeypatch,
        system_name,
        bind_addr,
    ):
        monkeypatch.setattr(_mod.platform, "system", lambda: system_name)

        def unexpected_run(*_args, **_kwargs):
            raise AssertionError("launchctl must not run outside Darwin")

        monkeypatch.setattr(_mod.subprocess, "run", unexpected_run)

        assert _disable_conflicting_macos_bridge(
            {"ODS_MACOS_HOST_GATEWAY": "192.168.106.1"},
            bind_addr,
            "com.ods.host-agent-bridge",
        ) is False

    def test_launchctl_nonzero_is_warning_and_nonfatal(self, monkeypatch, caplog):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(_mod.os, "getuid", lambda: 501, raising=False)
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(
                cmd,
                3,
                stdout="",
                stderr="service not loaded",
            ),
        )

        with caplog.at_level(logging.WARNING, logger="ods-host-agent"):
            assert _disable_conflicting_macos_bridge(
                {},
                "0.0.0.0",
                "com.ods.llm-bridge",
            ) is False

        assert "launchctl exit 3: service not loaded" in caplog.text
        assert "continuing" in caplog.text

    def test_launchctl_exception_is_warning_and_nonfatal(self, monkeypatch, caplog):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(_mod.os, "getuid", lambda: 501, raising=False)

        def failed_run(*_args, **_kwargs):
            raise OSError("launchctl unavailable")

        monkeypatch.setattr(_mod.subprocess, "run", failed_run)

        with caplog.at_level(logging.WARNING, logger="ods-host-agent"):
            assert _disable_conflicting_macos_bridge(
                {},
                "::",
                "com.ods.host-agent-bridge",
            ) is False

        assert "launchctl unavailable" in caplog.text
        assert "continuing" in caplog.text

    def test_host_agent_bridge_uses_normalized_bind_before_server(self, monkeypatch):
        events = []
        sentinel_server = object()

        def fake_disable(env, bind_addr, label):
            events.append(("bootout", env, bind_addr, label))
            return True

        def fake_server(address, handler):
            events.append(("bind", address, handler))
            return sentinel_server

        monkeypatch.setattr(_mod, "_disable_conflicting_macos_bridge", fake_disable)
        monkeypatch.setattr(_mod, "ThreadedHTTPServer", fake_server)
        env = {
            "ODS_AGENT_BIND": "::",
            "ODS_MACOS_HOST_GATEWAY": "192.168.106.1",
        }
        bind_addr = _resolve_agent_bind_addr(env, "Darwin")

        server = _mod._create_host_agent_server(env, bind_addr, 7710)

        assert server is sentinel_server
        assert events == [
            ("bootout", env, "0.0.0.0", "com.ods.host-agent-bridge"),
            ("bind", ("0.0.0.0", 7710), _mod.AgentHandler),
        ]


class TestResolveComposeFlags:

    def test_windows_passes_host_python_to_bash_resolver(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "ods"
        scripts_dir = install_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "resolve-compose-stack.sh").write_text("#!/usr/bin/env bash\n")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "TIER", "1")
        monkeypatch.setattr(_mod, "GPU_BACKEND", "nvidia")
        monkeypatch.setattr(_mod, "GPU_COUNT", "1")
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(
            _mod.sys,
            "executable",
            r"C:\Users\odser\AppData\Local\Programs\Python\Python313\python.exe",
        )
        monkeypatch.setenv("ODS_PYTHON_CMD", "python3")
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        monkeypatch.setattr(_mod, "_find_usable_bash", lambda: git_bash)
        monkeypatch.setattr(_mod, "_ensure_windows_resolver_pyyaml", lambda python_cmd: None)
        monkeypatch.setattr(_mod, "_windows_whisper_cuda_supported", lambda _env: True)

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="-f docker-compose.base.yml\n",
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert resolve_compose_flags() == ["-f", "docker-compose.base.yml"]
        assert calls
        assert calls[0][0][0] == git_bash
        env = calls[0][1]["env"]
        assert env["ODS_PYTHON_CMD"] == (
            "/c/Users/odser/AppData/Local/Programs/Python/Python313/python.exe"
        )

    @pytest.mark.parametrize(
        ("env_text", "expected_mode"),
        [
            ("ODS_MODE=cloud\n", "cloud"),
            ('ODS_MODE="hybrid"\n', "hybrid"),
            ("GPU_BACKEND=apple\n", "local"),
        ],
    )
    def test_cache_invalidation_reresolves_with_persisted_ods_mode(
        self,
        tmp_path,
        monkeypatch,
        env_text,
        expected_mode,
    ):
        install_dir = tmp_path / "ods"
        scripts_dir = install_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (install_dir / ".env").write_text(env_text, encoding="utf-8")
        cache_file = install_dir / ".compose-flags"
        cache_file.write_text("-f stale-local-overlay.yml\n", encoding="utf-8")
        (scripts_dir / "resolve-compose-stack.sh").write_text(
            "#!/usr/bin/env bash\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "TIER", "1")
        monkeypatch.setattr(_mod, "GPU_BACKEND", "apple")
        monkeypatch.setattr(_mod, "GPU_COUNT", "1")
        monkeypatch.setattr(_mod.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "/bin/bash")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="-f docker-compose.base.yml\n",
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        invalidate_compose_cache()
        assert not cache_file.exists()
        assert resolve_compose_flags() == ["-f", "docker-compose.base.yml"]

        cmd = calls[0][0]
        assert cmd[cmd.index("--ods-mode") + 1] == expected_mode
        assert cmd[cmd.index("--gpu-count") + 1] == "1"

    def test_windows_resolver_skips_whisper_overlay_for_cpu_fallback(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir = tmp_path / "ods"
        scripts_dir = install_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (install_dir / ".env").write_text(
            "ODS_MODE=local\nGPU_BACKEND=nvidia\nWHISPER_ACCELERATION=cpu\n",
            encoding="utf-8",
        )
        (scripts_dir / "resolve-compose-stack.sh").write_text(
            "#!/usr/bin/env bash\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "TIER", "1")
        monkeypatch.setattr(_mod, "GPU_BACKEND", "nvidia")
        monkeypatch.setattr(_mod, "GPU_COUNT", "1")
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod, "_find_usable_bash", lambda: r"C:\Program Files\Git\bin\bash.exe")
        monkeypatch.setattr(_mod, "_ensure_windows_resolver_pyyaml", lambda _python: None)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="-f docker-compose.base.yml -f extensions/services/whisper/compose.yaml\n",
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert resolve_compose_flags() == [
            "-f", "docker-compose.base.yml",
            "-f", "extensions/services/whisper/compose.yaml",
        ]
        cmd = calls[0][0]
        assert cmd[cmd.index("--skip-gpu-overlays") + 1] == "whisper"

    def test_windows_installs_pyyaml_for_resolver_python(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        python_cmd = r"C:\Users\odser\AppData\Local\Programs\Python\Python312\python.exe"
        calls = []
        import_attempts = {"count": 0}

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd == [python_cmd, "-c", "import yaml"]:
                import_attempts["count"] += 1
                return subprocess.CompletedProcess(
                    cmd,
                    0 if import_attempts["count"] > 1 else 1,
                    "",
                    "" if import_attempts["count"] > 1 else "ModuleNotFoundError",
                )
            if cmd[:4] == [python_cmd, "-m", "pip", "install"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        process_attempts = {"count": 0}

        def fake_process_can_import(module):
            assert module == "yaml"
            process_attempts["count"] += 1
            return True

        monkeypatch.setattr(_mod, "_process_can_import", fake_process_can_import)

        _mod._ensure_windows_resolver_pyyaml(python_cmd)

        assert [python_cmd, "-m", "pip", "install"] == calls[1][:4]
        assert "--user" in calls[1]
        assert "PyYAML" in calls[1]
        assert import_attempts["count"] == 2
        assert process_attempts["count"] == 1

    def test_windows_pyyaml_check_verifies_running_process_import(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        python_cmd = r"C:\Users\odser\AppData\Local\Programs\Python\Python312\python.exe"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd == [python_cmd, "-c", "import yaml"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[:4] == [python_cmd, "-m", "pip", "install"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(f"unexpected command: {cmd}")

        process_results = iter([False, True])

        def fake_process_can_import(module):
            assert module == "yaml"
            return next(process_results)

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "_process_can_import", fake_process_can_import)

        _mod._ensure_windows_resolver_pyyaml(python_cmd)

        assert [python_cmd, "-m", "pip", "install"] == calls[1][:4]

    def test_windows_bash_discovery_skips_unusable_path_bash(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod.shutil, "which", lambda name: r"C:\Windows\System32\bash.exe")
        monkeypatch.setattr(_mod, "_update_usable_bash", None)
        monkeypatch.setattr(_mod, "_usable_bash", None)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == r"C:\Program Files\Git\bin\bash.exe":
                return subprocess.CompletedProcess(cmd, 0, "ok", "")
            return subprocess.CompletedProcess(cmd, 1, "", "WSL has no distro")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._find_update_bash() == r"C:\Program Files\Git\bin\bash.exe"
        assert calls[0][0] == r"C:\Windows\System32\bash.exe"
        assert calls[-1][0] == r"C:\Program Files\Git\bin\bash.exe"


# --- _split_nmcli_terse — parser for nmcli -t (terse) output ---


class TestSplitNmcliTerse:
    """Reviewer flagged `line.split(':')` as fragile for SSIDs / connection
    names containing ':' (#1159 audit, point 3). `_split_nmcli_terse` is
    the replacement that respects nmcli's documented `\\:` escaping in
    terse mode.
    """

    def test_empty_line(self):
        assert _split_nmcli_terse("") == []

    def test_simple_four_fields(self):
        # SSID:SIGNAL:SECURITY:IN-USE from `nmcli -t -f ... device wifi list`
        assert _split_nmcli_terse("HomeWiFi:88:WPA2:*") == ["HomeWiFi", "88", "WPA2", "*"]

    def test_trailing_empty_field(self):
        # IN-USE is empty when this row isn't the connected network.
        assert _split_nmcli_terse("Guest:50:WPA2:") == ["Guest", "50", "WPA2", ""]

    def test_ssid_with_escaped_colon(self):
        # SSID literally named "Cafe:Lounge" comes back as "Cafe\:Lounge"
        # under default nmcli -t escaping. Naive str.split(':') corrupts it.
        assert _split_nmcli_terse(r"Cafe\:Lounge:67:WPA2:") == ["Cafe:Lounge", "67", "WPA2", ""]

    def test_connection_name_with_escaped_backslash(self):
        # Backslashes also escaped (as '\\\\') in terse mode.
        assert _split_nmcli_terse(r"home\\net:wifi:connected:Home") == ["home\\net", "wifi", "connected", "Home"]

    def test_multiple_escaped_colons_in_one_field(self):
        # SSID containing multiple colons.
        assert _split_nmcli_terse(r"a\:b\:c:1:open:") == ["a:b:c", "1", "open", ""]

    def test_no_unescaped_colons_returns_one_part(self):
        assert _split_nmcli_terse("solo") == ["solo"]


class TestNetworkHandlers:

    @pytest.fixture(autouse=True)
    def _network_env(self, monkeypatch):
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "nmcli" else None)

    def test_wifi_scan_keeps_strongest_duplicate_ssid(self, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            if cmd[:4] == ["nmcli", "device", "wifi", "rescan"]:
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")
            if cmd[:3] == ["nmcli", "-t", "-f"]:
                stdout = "\n".join([
                    "Cafe:20:WPA2:",
                    "Cafe:80:WPA2:*",
                    "Guest:40::",
                ])
                return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=stdout, stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _FakeHandler(b"")

        _mod.AgentHandler._handle_network_wifi_scan(handler)

        assert handler.response_code == 200
        body = handler.parse_response()
        assert body["networks"][0] == {
            "ssid": "Cafe",
            "signal": 80,
            "security": "WPA2",
            "in_use": True,
        }
        assert body["networks"][1]["ssid"] == "Guest"

    def test_wifi_forget_refuses_non_wifi_profile(self, monkeypatch):
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            if cmd[:3] == ["nmcli", "-t", "-f"]:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="connection.type:802-3-ethernet\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _FakeHandler(json.dumps({"connection": "Wired"}).encode("utf-8"))

        _mod.AgentHandler._handle_network_wifi_forget(handler)

        assert handler.response_code == 400
        assert "non-Wi-Fi" in handler.parse_response()["error"]
        assert all(cmd[:3] != ["nmcli", "connection", "delete"] for cmd in calls)

    def test_network_status_reports_nmcli_status_failure_as_unsupported(self, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            if cmd[:3] == ["nmcli", "-t", "-f"]:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=8,
                    stdout="",
                    stderr="NetworkManager is not running",
                )
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _FakeHandler(b"")

        _mod.AgentHandler._handle_network_status(handler)

        assert handler.response_code == 200
        body = handler.parse_response()
        assert body["platform_supported"] is False
        assert "NetworkManager is not running" in body["reason"]


# --- _parse_mem_value ---


class TestParseMemValue:

    def test_mib(self):
        assert _parse_mem_value("256MiB") == 256.0

    def test_gib(self):
        assert _parse_mem_value("4GiB") == 4096.0

    def test_tib(self):
        assert _parse_mem_value("1TiB") == 1024 * 1024

    def test_kib(self):
        assert _parse_mem_value("512KiB") == 0.5

    def test_bytes(self):
        result = _parse_mem_value("1024B")
        assert abs(result - 1024 / (1024 * 1024)) < 1e-9

    def test_fractional_gib(self):
        assert _parse_mem_value("1.5GiB") == 1536.0

    def test_zero_bytes(self):
        assert _parse_mem_value("0B") == 0.0

    def test_dash_dash(self):
        assert _parse_mem_value("--") == 0.0

    def test_empty_string(self):
        assert _parse_mem_value("") == 0.0

    def test_invalid_number(self):
        assert _parse_mem_value("xyzMiB") == 0.0

    def test_whitespace_padding(self):
        assert _parse_mem_value("  256MiB  ") == 256.0


# --- _iso_now ---


class TestIsoNow:

    def test_returns_utc_iso_string(self):
        result = _iso_now()
        assert isinstance(result, str)
        # UTC ISO strings end with +00:00
        assert "+00:00" in result

    def test_contains_t_separator(self):
        result = _iso_now()
        assert "T" in result


class TestToBashPath:

    def test_leaves_posix_paths_unchanged(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        assert _to_bash_path(PurePosixPath("/opt/ods")) == "/opt/ods"

    def test_converts_windows_drive_path(self, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        assert _to_bash_path(Path(r"C:\Users\Gabriel\ods")) == "/c/Users/Gabriel/ods"


class TestFindUsableBash:

    def test_windows_skips_unusable_path_bash_and_uses_git_bash(self, monkeypatch):
        bad_bash = r"C:\Windows\System32\bash.exe"
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        seen = []

        monkeypatch.setattr(_mod, "_usable_bash", None)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod.shutil, "which", lambda name: bad_bash if name == "bash" else None)

        real_exists = _mod.Path.exists

        def fake_exists(path):
            if str(path) in {bad_bash, git_bash}:
                return True
            return real_exists(path)

        def fake_run(cmd, *args, **kwargs):
            seen.append(cmd[0])
            if cmd[0] == bad_bash:
                return subprocess.CompletedProcess(cmd, 127, "", "WSL execvpe(/bin/bash) failed")
            if cmd[0] == git_bash:
                return subprocess.CompletedProcess(cmd, 0, "ok", "")
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(_mod.Path, "exists", fake_exists)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        assert _mod._find_usable_bash() == git_bash
        assert seen[:2] == [bad_bash, git_bash]


class TestValidateCoreRecreateIds:

    def test_accepts_allowed_core_service(self, monkeypatch):
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"llama-server", "dashboard-api"})
        ok, error = validate_core_recreate_ids(["llama-server"])
        assert ok is True
        assert error == ""

    @pytest.mark.parametrize("service_id", ["hermes", "hermes-proxy"])
    def test_accepts_hermes_core_recreate_services(self, monkeypatch, service_id):
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {service_id})
        ok, error = validate_core_recreate_ids([service_id])
        assert ok is True
        assert error == ""

    def test_accepts_model_router_core_recreate_service(self, monkeypatch):
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"model-router"})
        ok, error = validate_core_recreate_ids(["model-router"])
        assert ok is True
        assert error == ""

    def test_rejects_non_core_service(self, monkeypatch):
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"dashboard-api"})
        ok, error = validate_core_recreate_ids(["llama-server"])
        assert ok is False
        assert "not a core" in error.lower()

    def test_rejects_disallowed_core_service(self, monkeypatch):
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", {"dashboard-api"})
        ok, error = validate_core_recreate_ids(["dashboard-api"])
        assert ok is False
        assert "not eligible" in error.lower()


class TestResolveComposeFlagsCache:

    def test_prefers_saved_compose_flags_file(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "ods"
        install_dir.mkdir()
        (install_dir / ".compose-flags").write_text("--env-file .env -f docker-compose.base.yml", encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        assert resolve_compose_flags() == ["--env-file", ".env", "-f", "docker-compose.base.yml"]


class TestComposeCacheInvalidationWire:
    """End-to-end HTTP test: dashboard-api client talks to the real host-agent handler."""

    def test_client_posts_to_host_agent_and_unlinks_cache(
        self, tmp_path, monkeypatch, host_agent_wire_client,
    ):
        import threading
        from http.server import HTTPServer

        from routers import extensions as ext_router

        install_dir = tmp_path / "ods"
        install_dir.mkdir()
        cache_file = install_dir / ".compose-flags"
        cache_file.write_text("stale-flags", encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host_agent_wire_client(port)

            # Correct key → cache file is unlinked, helper returns without raising.
            ext_router._call_agent_invalidate_compose_cache()
            assert not cache_file.exists()

            # Wrong key → handler rejects with 403, helper logs and returns; cache
            # stays put. Proves the Authorization: Bearer <key> header is checked.
            cache_file.write_text("stale-again", encoding="utf-8")
            host_agent_wire_client(port, key="wrong-secret")
            ext_router._call_agent_invalidate_compose_cache()
            assert cache_file.exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class TestUpdateWire:
    """End-to-end HTTP tests for host-agent managed update actions."""

    def test_check_runs_update_script_on_host_agent(self, tmp_path, monkeypatch):
        import threading
        import urllib.request
        from http.server import HTTPServer

        install_dir = tmp_path / "ods"
        install_dir.mkdir()

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")
        monkeypatch.setattr(
            _mod,
            "_run_update_script",
            lambda action, *args, timeout: subprocess.CompletedProcess(
                ["ods-update", action, *args],
                2,
                "update available\n",
                "",
            ),
        )

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/update/check",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer wire-test-secret",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            assert resp.status == 200
            assert data["success"] is True
            assert data["update_available"] is True
            assert data["returncode"] == 2
            assert "update available" in data["output"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_start_records_background_update_status(self, tmp_path, monkeypatch):
        import threading
        import urllib.request
        from http.server import HTTPServer

        install_dir = tmp_path / "ods"
        install_dir.mkdir()

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")
        monkeypatch.setattr(
            _mod,
            "_run_update_script",
            lambda action, *args, timeout: subprocess.CompletedProcess(
                ["ods-update", action, *args],
                0,
                "updated\n",
                "",
            ),
        )

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/update/start",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer wire-test-secret",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                accepted = json.loads(resp.read().decode("utf-8"))

            assert resp.status == 202
            assert accepted["status"] == "started"

            deadline = time.time() + 2
            status = {}
            while time.time() < deadline:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/update/status",
                    headers={"Authorization": "Bearer wire-test-secret"},
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    status = json.loads(resp.read().decode("utf-8"))
                if status.get("status") == "succeeded":
                    break
                time.sleep(0.05)

            assert status["status"] == "succeeded"
            assert status["returncode"] == 0
            assert "updated" in status["output_tail"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_status_marks_stale_running_update_failed(self, tmp_path, monkeypatch):
        import threading
        import urllib.request
        from http.server import HTTPServer

        install_dir = tmp_path / "ods"
        install_dir.mkdir()

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")
        monkeypatch.setattr(_mod, "_update_thread", None)
        _mod._write_update_status("running", "update", started_at="2026-01-01T00:00:00+00:00")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/update/status",
                headers={"Authorization": "Bearer wire-test-secret"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                status = json.loads(resp.read().decode("utf-8"))

            assert resp.status == 200
            assert status["status"] == "failed"
            assert status["action"] == "update"
            assert "before reporting completion" in status["error"]
            assert _mod._read_update_status()["status"] == "failed"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_start_records_unexpected_background_failure(self, tmp_path, monkeypatch):
        import threading
        import urllib.request
        from http.server import HTTPServer

        install_dir = tmp_path / "ods"
        install_dir.mkdir()

        def fail_update(action, *args, timeout):
            raise ValueError("boom")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")
        monkeypatch.setattr(_mod, "_update_thread", None)
        monkeypatch.setattr(_mod, "_run_update_script", fail_update)

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/update/start",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer wire-test-secret",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                accepted = json.loads(resp.read().decode("utf-8"))

            assert resp.status == 202
            assert accepted["status"] == "started"

            deadline = time.time() + 2
            status = {}
            while time.time() < deadline:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/update/status",
                    headers={"Authorization": "Bearer wire-test-secret"},
                )
                with urllib.request.urlopen(req, timeout=2) as resp:
                    status = json.loads(resp.read().decode("utf-8"))
                if status.get("status") == "failed":
                    break
                time.sleep(0.05)

            assert status["status"] == "failed"
            assert "unexpectedly" in status["error"]
            assert "boom" in status["error"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_backup_rejects_path_traversal_backup_id(self, tmp_path, monkeypatch):
        import threading
        import urllib.error
        import urllib.request
        from http.server import HTTPServer

        install_dir = tmp_path / "ods"
        install_dir.mkdir()

        calls = []

        def spy_run(action, *args, timeout):
            calls.append((action, args))
            return subprocess.CompletedProcess(["ods-update", action, *args], 0, "", "")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")
        monkeypatch.setattr(_mod, "_run_update_script", spy_run)

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/update/backup",
                data=json.dumps({"backup_id": "../../../../tmp/exfil"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer wire-test-secret",
                },
                method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as excinfo:
                urllib.request.urlopen(req, timeout=2)

            assert excinfo.value.code == 400
            # The traversal id must be rejected before it ever reaches the
            # update script that would copy .env into the escaped directory.
            assert calls == []
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_backup_accepts_plain_backup_id(self, tmp_path, monkeypatch):
        import threading
        import urllib.request
        from http.server import HTTPServer

        install_dir = tmp_path / "ods"
        install_dir.mkdir()

        calls = []

        def spy_run(action, *args, timeout):
            calls.append((action, args))
            return subprocess.CompletedProcess(
                ["ods-update", action, *args], 0, "backed up\n", ""
            )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")
        monkeypatch.setattr(_mod, "_run_update_script", spy_run)

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/update/backup",
                data=json.dumps({"backup_id": "dashboard-20260715-143022"}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer wire-test-secret",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            assert resp.status == 200
            assert data["success"] is True
            assert calls == [("backup", ("dashboard-20260715-143022",))]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class TestComposeToggleWire:
    """End-to-end HTTP test for built-in compose toggles via the host agent."""

    def test_client_posts_to_host_agent_and_renames_builtin_compose(
        self, tmp_path, monkeypatch, host_agent_wire_client,
    ):
        import threading
        from http.server import HTTPServer

        from routers import extensions as ext_router

        builtin_root = tmp_path / "builtin"
        user_root = tmp_path / "user"
        builtin_root.mkdir()
        user_root.mkdir()
        ext_dir = builtin_root / "fakesvc"
        ext_dir.mkdir()
        (ext_dir / "compose.yaml.disabled").write_text(
            "services:\n  svc:\n    image: test:latest\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host_agent_wire_client(port)

            assert ext_router._call_agent_compose_rename("activate", "fakesvc") is True
            assert (ext_dir / "compose.yaml").exists()
            assert not (ext_dir / "compose.yaml.disabled").exists()

            assert ext_router._call_agent_compose_rename("deactivate", "fakesvc") is True
            assert (ext_dir / "compose.yaml.disabled").exists()
            assert not (ext_dir / "compose.yaml").exists()

            host_agent_wire_client(port, key="wrong-secret")
            assert ext_router._call_agent_compose_rename("activate", "fakesvc") is False
            assert (ext_dir / "compose.yaml.disabled").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class TestSyncExtensionConfigWire:
    """End-to-end HTTP test: dashboard-api client + real host-agent handler.

    Proves that ${INSTALL_DIR}/config/<svc>/ ends up populated even though
    the dashboard-api side only does an HTTP call (not filesystem work).
    """

    def _make_extension(self, user_root, sid):
        ext = user_root / sid
        cfg = ext / "config" / sid
        cfg.mkdir(parents=True)
        (cfg / "settings.yaml").write_text("server: ok\n", encoding="utf-8")
        (cfg / "entrypoint.sh").write_text("#!/bin/sh\necho run\n", encoding="utf-8")
        return ext

    def test_client_posts_and_host_agent_copies_config(
        self, tmp_path, monkeypatch, host_agent_wire_client,
    ):
        import threading
        from http.server import HTTPServer

        from routers import extensions as ext_router

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        self._make_extension(user_root, "fakesvc")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host_agent_wire_client(port)

            assert ext_router._call_agent_sync_config("fakesvc") is True

            target = install_dir / "config" / "fakesvc"
            assert (target / "settings.yaml").read_text(encoding="utf-8") == "server: ok\n"
            # .sh files become executable
            if os.name != "nt":
                import stat as _s
                mode = (target / "entrypoint.sh").stat().st_mode
                assert mode & _s.S_IXUSR
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_noop_when_extension_has_no_config_subdir(
        self, tmp_path, monkeypatch, host_agent_wire_client,
    ):
        import threading
        from http.server import HTTPServer

        from routers import extensions as ext_router

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        (user_root / "noconfig").mkdir()  # no config/ subdir

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host_agent_wire_client(port)

            # No config/ → server returns 200 with empty synced list, helper True.
            assert ext_router._call_agent_sync_config("noconfig") is True
            # No INSTALL_DIR/config/noconfig should have been created.
            assert not (install_dir / "config" / "noconfig").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_wrong_auth(
        self, tmp_path, monkeypatch, host_agent_wire_client,
    ):
        import threading
        from http.server import HTTPServer

        from routers import extensions as ext_router

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        self._make_extension(user_root, "fakesvc")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host_agent_wire_client(port, key="wrong-secret")

            assert ext_router._call_agent_sync_config("fakesvc") is False
            # Nothing copied — auth was rejected before any work.
            assert not (install_dir / "config" / "fakesvc").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @staticmethod
    def _post(port, sid, *, key="wire-test-secret", preserve_existing=False):
        """Direct HTTP POST to /v1/extension/sync_config so callers can
        assert on the raw status code (the dashboard-api helper masks
        4xx as a generic False, which is too coarse for these tests)."""
        import json as _json
        import urllib.request
        import urllib.error
        url = f"http://127.0.0.1:{port}/v1/extension/sync_config"
        req = urllib.request.Request(
            url,
            data=_json.dumps({
                "service_id": sid,
                "preserve_existing": preserve_existing,
            }).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, _json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                body = _json.loads(exc.read() or b"{}")
            except ValueError:
                body = {}
            return exc.code, body

    def test_preserve_existing_only_adds_missing_config(
        self, tmp_path, monkeypatch,
    ):
        import threading
        from http.server import HTTPServer

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        self._make_extension(user_root, "fakesvc")
        source = user_root / "fakesvc" / "config" / "fakesvc"
        (source / "new.yaml").write_text("new: default\n", encoding="utf-8")
        target = install_dir / "config" / "fakesvc"
        target.mkdir(parents=True)
        (target / "settings.yaml").write_text("server: customized\n", encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, _body = self._post(port, "fakesvc", preserve_existing=True)
            assert status == 200
            assert _body.get("preserve_existing") is True
            assert (target / "settings.yaml").read_text(encoding="utf-8") == "server: customized\n"
            assert (target / "new.yaml").read_text(encoding="utf-8") == "new: default\n"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_existing_symlink_in_config_target(
        self, tmp_path, monkeypatch,
    ):
        if os.name == "nt" and not can_create_symlinks(tmp_path):
            pytest.skip("Windows symlink creation requires Developer Mode or administrator privileges")
        import threading
        from http.server import HTTPServer

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        self._make_extension(user_root, "fakesvc")
        outside = tmp_path / "outside"
        outside.mkdir()
        target = install_dir / "config" / "fakesvc"
        target.mkdir(parents=True)
        (target / "linked").symlink_to(outside, target_is_directory=True)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post(port, "fakesvc", preserve_existing=True)
            assert status == 400
            assert "target symlink" in body.get("error", "")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_symlink_in_config_tree(self, tmp_path, monkeypatch):
        """Symlinks (file or directory, top-level or nested) must be rejected
        outright. _copytree_safe strips symlinks at install time so legitimate
        user extensions never have any; one here implies tampering."""
        if os.name == "nt" and not can_create_symlinks(tmp_path):
            pytest.skip("Windows symlink creation requires Developer Mode or administrator privileges")
        import threading
        from http.server import HTTPServer

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        secret_dir = tmp_path / "secret"
        secret_dir.mkdir()
        (secret_dir / "private.key").write_text("EXFIL ME", encoding="utf-8")

        # Build an extension whose config/ contains a symlinked directory
        # pointing OUTSIDE the extension.  Pre-fix this got dereferenced by
        # shutil.copytree(symlinks=False) and the secret leaked into
        # INSTALL_DIR/config/leak/private.key.
        ext = user_root / "fakesvc"
        cfg = ext / "config"
        cfg.mkdir(parents=True)
        # Plus a normal file in the same tree to prove nothing was copied.
        (cfg / "ok.yaml").write_text("ok: true\n", encoding="utf-8")
        (cfg / "leak").symlink_to(secret_dir)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post(port, "fakesvc")
            assert status == 400, f"expected 400, got {status}: {body}"
            assert "symlink" in body.get("error", "").lower()
            # Crucially: the secret file did NOT make it into INSTALL_DIR.
            assert not (install_dir / "config" / "fakesvc").exists()
            assert not (install_dir / "config" / "leak").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_symlinked_file_in_config_tree(self, tmp_path, monkeypatch):
        """Symlinked files (not just directories) are also rejected."""
        if os.name == "nt" and not can_create_symlinks(tmp_path):
            pytest.skip("Windows symlink creation requires Developer Mode or administrator privileges")
        import threading
        from http.server import HTTPServer

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        secret = tmp_path / "secret.txt"
        secret.write_text("nope", encoding="utf-8")

        ext = user_root / "fakesvc"
        cfg = ext / "config" / "fakesvc"
        cfg.mkdir(parents=True)
        (cfg / "leak.txt").symlink_to(secret)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post(port, "fakesvc")
            assert status == 400
            assert "symlink" in body.get("error", "").lower()
            assert not (install_dir / "config" / "fakesvc").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_when_config_dir_itself_is_symlink(self, tmp_path, monkeypatch):
        """Top-level `config/` itself a symlink — covers the upfront
        ext_config.is_symlink() guard, separate from the dirs+files walk."""
        if os.name == "nt" and not can_create_symlinks(tmp_path):
            pytest.skip("Windows symlink creation requires Developer Mode or administrator privileges")
        import threading
        from http.server import HTTPServer

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        secret_dir = tmp_path / "secret"
        secret_dir.mkdir()
        (secret_dir / "private.key").write_text("EXFIL ME", encoding="utf-8")

        # Build an extension whose `config` IS the symlink (not a child of it).
        ext = user_root / "fakesvc"
        ext.mkdir()
        (ext / "config").symlink_to(secret_dir)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post(port, "fakesvc")
            assert status == 400, f"expected 400, got {status}: {body}"
            assert "symlink" in body.get("error", "").lower()
            assert not (install_dir / "config" / "fakesvc").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_rejects_invalid_service_id(self, tmp_path, monkeypatch):
        """SERVICE_ID_RE rejection — match the auth/symlink reject style."""
        import threading
        from http.server import HTTPServer

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for bad in ("../escape", "Bad-ID", "with space", "", "..", "FAKE"):
                status, body = self._post(port, bad)
                assert status == 400, f"bad={bad!r} -> {status}: {body}"
                assert "service_id" in body.get("error", "").lower()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_noop_for_builtin_only_service(self, tmp_path, monkeypatch):
        """Built-in extensions (not in USER_EXTENSIONS_DIR) get a 200 no-op.

        Pins the deliberate decision NOT to overwrite installer-managed
        configs when a built-in's compose toggle re-enables it.
        """
        import threading
        from http.server import HTTPServer

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        builtin_root = tmp_path / "builtin"
        install_dir.mkdir()
        user_root.mkdir(parents=True)
        builtin_root.mkdir()
        # Built-in present, with a config/ subdir that should NOT be touched.
        builtin_ext = builtin_root / "core-svc" / "config" / "core-svc"
        builtin_ext.mkdir(parents=True)
        (builtin_ext / "should_not_be_synced.yaml").write_text("x: 1\n", encoding="utf-8")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post(port, "core-svc")
            assert status == 200
            assert body.get("synced") == []
            # No file should have been written into INSTALL_DIR/config/.
            assert not (install_dir / "config" / "core-svc").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_default_contract_only_copies_own_service_subdir(
        self, tmp_path, monkeypatch,
    ):
        """Strict regression for the audit-flagged copy-contract weakness.

        An extension shipping `<ext>/config/open-webui/` (or any other
        sibling directory) must NOT have that tree copied into
        `INSTALL_DIR/config/open-webui/` — that would let an extension
        overwrite installer-managed core-service config (open-webui,
        litellm, etc.) or another extension's config tree.

        Default contract: only `<ext>/config/<service_id>/` is synced.
        Sibling entries are logged as out-of-scope and reported in the
        response's `skipped` array, but never written to INSTALL_DIR.
        """
        import threading
        from http.server import HTTPServer

        install_dir = tmp_path / "install"
        user_root = install_dir / "data" / "user-extensions"
        install_dir.mkdir()
        user_root.mkdir(parents=True)

        # Build a malicious-shape extension: `evil-ext` that ships its OWN
        # legitimate config subdir AND tries to overwrite open-webui's.
        ext = user_root / "evil-ext"
        own = ext / "config" / "evil-ext"
        own.mkdir(parents=True)
        (own / "settings.yaml").write_text("ok: true\n", encoding="utf-8")

        clobber_target = ext / "config" / "open-webui"
        clobber_target.mkdir(parents=True)
        (clobber_target / "config.json").write_text(
            "OVERWRITTEN", encoding="utf-8",
        )
        # Also a file directly under config/ (not in any subdir), proving the
        # contract restriction applies to file siblings too.
        (ext / "config" / "stray.txt").write_text("stray", encoding="utf-8")

        # Pre-create open-webui core config so the test can prove byte-for-byte
        # that it was NOT touched by the sync call.
        existing_owui = install_dir / "config" / "open-webui"
        existing_owui.mkdir(parents=True)
        (existing_owui / "config.json").write_text(
            "ORIGINAL", encoding="utf-8",
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "builtin-empty")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            status, body = self._post(port, "evil-ext")
            assert status == 200
            # In-scope copy succeeded.
            assert body.get("synced") == ["evil-ext"]
            assert (install_dir / "config" / "evil-ext" / "settings.yaml").read_text(
                encoding="utf-8",
            ) == "ok: true\n"
            # Out-of-scope entries reported (order not guaranteed).
            skipped = set(body.get("skipped", []))
            assert "open-webui" in skipped
            assert "stray.txt" in skipped
            # Crucially: open-webui core config remains BYTE-FOR-BYTE untouched.
            assert (existing_owui / "config.json").read_text(
                encoding="utf-8",
            ) == "ORIGINAL"
            # The malicious overwrite payload did NOT escape into INSTALL_DIR.
            assert not (install_dir / "config" / "stray.txt").exists()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class TestInvalidateComposeCache:

    def test_unlinks_existing_cache_file(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "ods"
        install_dir.mkdir()
        cache_file = install_dir / ".compose-flags"
        cache_file.write_text("--env-file .env", encoding="utf-8")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        invalidate_compose_cache()

        assert not cache_file.exists()

    def test_missing_cache_file_is_noop(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "ods"
        install_dir.mkdir()
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        invalidate_compose_cache()  # must not raise


# --- Install setup-hook env allowlist (regression) ---
#
# Locks in the fix that strips host-agent secrets from the env passed to
# extension setup hooks. The env-construction + subprocess.run call lives in
# the shared `_run_post_install_hook` helper (used by both _handle_install
# and _enable_retry_work). A source-level check is used because the helper
# is invoked from a nested closure on a daemon thread, which makes dynamic
# mocking fragile.


class TestInstallHookEnvAllowlist:

    def _hook_helper_source(self):
        import inspect
        return inspect.getsource(_mod._run_post_install_hook)

    def _install_source(self):
        import inspect
        return inspect.getsource(_mod.AgentHandler._handle_install)

    def test_setup_hook_subprocess_run_passes_env_kwarg(self):
        src = self._hook_helper_source()
        assert "env=hook_env" in src, (
            "setup_hook subprocess.run must pass env=hook_env "
            "(regression: do not fall back to inheriting os.environ)"
        )

    def test_setup_hook_env_excludes_host_agent_secrets(self):
        # Both the helper and the call-site must stay secret-free.
        for src in (self._hook_helper_source(), self._install_source()):
            for secret in ("AGENT_API_KEY", "ODS_AGENT_KEY", "DASHBOARD_API_KEY"):
                assert secret not in src, (
                    f"setup_hook code path must not reference {secret}; "
                    "extension setup hooks must not receive host-agent secrets"
                )

    def test_setup_hook_env_contains_allowlist_keys(self):
        src = self._hook_helper_source()
        for key in (
            "PATH", "HOME", "SERVICE_ID", "SERVICE_PORT",
            "SERVICE_DATA_DIR", "ODS_VERSION", "GPU_BACKEND", "HOOK_NAME",
        ):
            assert f'"{key}"' in src, (
                f"setup_hook env allowlist missing required key {key}"
            )

    def test_setup_hook_uses_resolve_hook_with_post_install(self):
        src = self._hook_helper_source()
        assert '_resolve_hook(ext_dir, "post_install")' in src, (
            "setup_hook must use _resolve_hook(..., 'post_install'); "
            "the legacy _resolve_setup_hook has been removed"
        )


# --- Install "up -d" must not use --no-deps (regression) ---
#
# _handle_install previously passed --no-deps to `docker compose up -d`, which
# prevented an extension's private sidecar services (declared in its own
# compose fragment) from starting — including cross-extension depends_on
# relationships like perplexica -> searxng. The fix removes --no-deps from
# the install path only; docker_compose_recreate (used for core-service
# force-recreate after a model swap) intentionally keeps --no-deps.


class TestInstallStartCommandNoDeps:

    def _install_source(self):
        import inspect
        return inspect.getsource(_mod.AgentHandler._handle_install)

    def _recreate_source(self):
        import inspect
        return inspect.getsource(_mod.docker_compose_recreate)

    def test_install_up_command_does_not_pass_no_deps(self):
        src = self._install_source()
        assert '"--no-deps"' not in src and "'--no-deps'" not in src, (
            "_handle_install must not pass --no-deps to `docker compose up -d`; "
            "extensions with private sidecars or cross-extension depends_on "
            "need compose to bring dependencies up."
        )

    def test_docker_compose_recreate_still_uses_no_deps(self):
        src = self._recreate_source()
        assert '"--no-deps"' in src or "'--no-deps'" in src, (
            "docker_compose_recreate must keep --no-deps; "
            "core-service recreation (e.g. after a model swap) is intentionally "
            "scoped to the named services only."
        )


# --- _post_install_core_recreate ---
#
# openclaw's compose.yaml adds OPENAI_API_BASE_URLS to open-webui as an overlay;
# `docker compose up -d openclaw` (used by _handle_install) won't pick up
# overlay changes targeting already-running core services without
# `--force-recreate`. Hence the post-install recreate of open-webui whenever
# openclaw is installed.


class TestPostInstallCoreRecreate:

    def test_openclaw_triggers_open_webui_recreate(self, monkeypatch):
        calls = []

        def _fake_recreate(ids):
            calls.append(list(ids))
            return True, ""

        monkeypatch.setattr(_mod, "docker_compose_recreate", _fake_recreate)
        _post_install_core_recreate("openclaw")
        assert calls == [["open-webui"]]

    def test_non_openclaw_service_is_noop(self, monkeypatch):
        calls = []

        def _fake_recreate(ids):
            calls.append(list(ids))
            return True, ""

        monkeypatch.setattr(_mod, "docker_compose_recreate", _fake_recreate)
        for svc in ("litellm", "n8n", "perplexica", "whisper", "comfyui"):
            _post_install_core_recreate(svc)
        assert calls == []

    def test_recreate_failure_is_swallowed(self, monkeypatch):
        """Install must not fail if the post-install recreate errors — openclaw
        is already running; the overlay just won't take effect until a manual
        core restart."""

        def _fake_recreate(_ids):
            return False, "docker compose exploded"

        monkeypatch.setattr(_mod, "docker_compose_recreate", _fake_recreate)
        # Must not raise
        _post_install_core_recreate("openclaw")


class TestRunInstallCallsPostInstallRecreate:
    """Source-level check that the install closure calls
    _post_install_core_recreate after the "started" progress write.

    The dynamic flow runs in a daemon thread + nested closure, which makes
    runtime mocking fragile (see TestInstallHookEnvAllowlist for the same
    reasoning). Source-level assertion is sufficient to lock the wiring."""

    def _install_source(self):
        import inspect
        return inspect.getsource(_mod.AgentHandler._handle_install)

    def test_install_calls_post_install_core_recreate(self):
        src = self._install_source()
        assert "_post_install_core_recreate(service_id)" in src, (
            "_run_install must invoke _post_install_core_recreate(service_id) "
            "after emitting the 'started' progress record"
        )

    def test_recreate_is_after_started_progress_write(self):
        src = self._install_source()
        started_idx = src.find('"started"')
        recreate_idx = src.find("_post_install_core_recreate(")
        assert started_idx != -1, "expected 'started' progress write in _handle_install"
        assert recreate_idx != -1, "expected _post_install_core_recreate call in _handle_install"
        assert started_idx < recreate_idx, (
            "_post_install_core_recreate must run AFTER the 'started' progress "
            "write so the client sees success even if the recreate fails"
        )


# --- _handle_env_update ---


class _FakeHandler:
    """Minimal stand-in for BaseHTTPRequestHandler used by _handle_env_update."""

    def __init__(self, body: bytes, headers=None):
        merged = {
            "Authorization": "Bearer test-key",
            "Content-Length": str(len(body)),
        }
        if headers:
            merged.update(headers)
        self.headers = merged
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 12345)
        self.response_code = None
        self.response_headers = []

    def send_response(self, code):
        self.response_code = code

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass

    def parse_response(self):
        # json_response writes the JSON body via wfile.write()
        return json.loads(self.wfile.getvalue().decode("utf-8"))


class TestTailscaleStatus:
    """Direct host-agent tests for /v1/tailscale/status behavior."""

    @pytest.fixture(autouse=True)
    def _auth(self, monkeypatch):
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")

    def _handler(self):
        handler = _FakeHandler(b"")
        handler._write_tailscale_status_payload = types.MethodType(
            _mod.AgentHandler._write_tailscale_status_payload, handler
        )
        handler._find_native_tailscale_cli = types.MethodType(
            _mod.AgentHandler._find_native_tailscale_cli, handler
        )
        handler._try_native_tailscale_status = types.MethodType(
            _mod.AgentHandler._try_native_tailscale_status, handler
        )
        return handler

    def test_falls_back_to_native_tailscale_when_container_absent(self, monkeypatch):
        payload = {
            "BackendState": "Running",
            "Self": {
                "HostName": "ods-win",
                "DNSName": "ods-win.tail-example.ts.net.",
                "TailscaleIPs": ["100.64.0.42"],
                "Online": True,
            },
            "MagicDNSSuffix": "tail-example.ts.net",
            "CurrentTailnet": {"Name": "example.com"},
        }

        def fake_run(cmd, *args, **kwargs):
            if cmd[:2] == ["docker", "exec"]:
                return subprocess.CompletedProcess(cmd, 1, "", "No such container: ods-tailscale")
            if cmd[:3] == ["tailscale", "status", "--json"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
            raise AssertionError(f"unexpected command: {cmd}")

        monkeypatch.setattr(_mod.shutil, "which", lambda name: "tailscale" if name == "tailscale" else None)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        handler = self._handler()
        _mod.AgentHandler._handle_tailscale_status(handler)
        body = handler.parse_response()

        assert handler.response_code == 200
        assert body["running"] is True
        assert body["authenticated"] is True
        assert body["source"] == "native"
        assert body["self"]["dns_name"] == "ods-win.tail-example.ts.net"

    def test_absent_container_without_native_tailscale_is_not_running(self, monkeypatch):
        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, "", "No such container: ods-tailscale")

        monkeypatch.setattr(_mod.shutil, "which", lambda name: None)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        handler = self._handler()
        _mod.AgentHandler._handle_tailscale_status(handler)
        body = handler.parse_response()

        assert handler.response_code == 200
        assert body == {"running": False}


class TestServiceRestart:
    """Direct host-agent tests for POST /v1/service/restart behavior."""

    def _write_manifest(self, ext_root: Path, service_id: str, service_block: str):
        pytest.importorskip("yaml")
        ext_dir = ext_root / service_id
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n"
            f"  id: {service_id}\n"
            f"{service_block}",
            encoding="utf-8",
        )

    def _configure(self, tmp_path, monkeypatch):
        builtin_root = tmp_path / "builtin"
        user_root = tmp_path / "user"
        builtin_root.mkdir()
        user_root.mkdir()
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "CORE_SERVICE_IDS", set())
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        return builtin_root, user_root

    def _body(self, service_id: str, **extra) -> bytes:
        return json.dumps({"service_id": service_id, **extra}).encode("utf-8")

    def test_valid_restart_calls_docker_restart(self, tmp_path, monkeypatch):
        builtin_root, _ = self._configure(tmp_path, monkeypatch)
        self._write_manifest(
            builtin_root,
            "ape",
            "  name: APE\n"
            "  container_name: ods-ape\n",
        )
        _mod._service_locks.pop("ape", None)
        monkeypatch.setattr(_mod, "_resolve_container_name", lambda sid: "ods-ape")

        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        handler = _FakeHandler(self._body("ape"))
        _mod.AgentHandler._handle_service_restart(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["action"] == "restart"
        assert calls == [["docker", "restart", "ods-ape"]]

    def test_restart_requires_auth(self, tmp_path, monkeypatch):
        self._configure(tmp_path, monkeypatch)
        handler = _FakeHandler(self._body("ape"), headers={"Authorization": "Bearer wrong"})

        _mod.AgentHandler._handle_service_restart(handler)

        assert handler.response_code == 403

    def test_invalid_service_id_rejected(self, tmp_path, monkeypatch):
        self._configure(tmp_path, monkeypatch)
        handler = _FakeHandler(self._body("../ape"))

        _mod.AgentHandler._handle_service_restart(handler)

        assert handler.response_code == 400
        assert handler.parse_response()["error"] == "Invalid service_id"

    def test_unknown_service_rejected(self, tmp_path, monkeypatch):
        self._configure(tmp_path, monkeypatch)
        handler = _FakeHandler(self._body("not-installed"))

        _mod.AgentHandler._handle_service_restart(handler)

        assert handler.response_code == 404
        assert "not-installed" in handler.parse_response()["error"]

    def test_host_systemd_service_rejected_before_docker(self, tmp_path, monkeypatch):
        builtin_root, _ = self._configure(tmp_path, monkeypatch)
        self._write_manifest(
            builtin_root,
            "opencode",
            "  name: OpenCode\n"
            "  type: host-systemd\n"
            "  container_name: \"\"\n",
        )

        def fake_run(*args, **kwargs):
            pytest.fail("host-systemd service must not run docker restart")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        handler = _FakeHandler(self._body("opencode"))
        _mod.AgentHandler._handle_service_restart(handler)

        assert handler.response_code == 400
        assert "host-level" in handler.parse_response()["error"].lower()

    def test_extension_without_manifest_is_not_restartable(self, tmp_path, monkeypatch):
        builtin_root, _ = self._configure(tmp_path, monkeypatch)
        (builtin_root / "broken").mkdir()

        def fake_run(*args, **kwargs):
            pytest.fail("missing manifest service must not run docker restart")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        handler = _FakeHandler(self._body("broken"))
        _mod.AgentHandler._handle_service_restart(handler)

        assert handler.response_code == 400
        assert "manifest" in handler.parse_response()["error"].lower()

    def test_concurrent_restart_returns_409(self, tmp_path, monkeypatch):
        builtin_root, _ = self._configure(tmp_path, monkeypatch)
        self._write_manifest(
            builtin_root,
            "ape",
            "  name: APE\n"
            "  container_name: ods-ape\n",
        )
        lock = _mod._service_locks["ape"]
        assert lock.acquire(blocking=False) is True
        try:
            handler = _FakeHandler(self._body("ape"))
            _mod.AgentHandler._handle_service_restart(handler)
        finally:
            lock.release()
            _mod._service_locks.pop("ape", None)

        assert handler.response_code == 409

    def test_delayed_restart_returns_accepted_and_releases_lock(self, tmp_path, monkeypatch):
        builtin_root, _ = self._configure(tmp_path, monkeypatch)
        self._write_manifest(
            builtin_root,
            "dashboard-api",
            "  name: Dashboard API\n"
            "  container_name: ods-dashboard-api\n",
        )
        _mod._service_locks.pop("dashboard-api", None)
        monkeypatch.setattr(_mod, "_resolve_container_name", lambda sid: "ods-dashboard-api")
        monkeypatch.setattr(_mod.time, "sleep", lambda *_args: None)

        class ImmediateThread:
            def __init__(self, target=None, daemon=None, **kwargs):
                self._target = target

            def start(self):
                self._target()

        monkeypatch.setattr(_mod.threading, "Thread", ImmediateThread)

        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        handler = _FakeHandler(self._body("dashboard-api", delay_seconds=1))
        _mod.AgentHandler._handle_service_restart(handler)

        assert handler.response_code == 202
        assert handler.parse_response()["status"] == "accepted"
        assert calls == [["docker", "restart", "ods-dashboard-api"]]
        assert _mod._service_locks["dashboard-api"].acquire(blocking=False) is True
        _mod._service_locks["dashboard-api"].release()


@pytest.fixture
def env_update_env(tmp_path, monkeypatch):
    """Wire up INSTALL_DIR/DATA_DIR/AGENT_API_KEY for _handle_env_update tests."""
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    schema = {
        "properties": {
            "ODS_AGENT_KEY": {"type": "string"},
            "GGUF_FILE": {"type": "string"},
        }
    }
    (install_dir / ".env.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    (install_dir / ".env").write_text("ODS_AGENT_KEY=existing\n", encoding="utf-8")

    monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
    monkeypatch.setattr(_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    return install_dir, data_dir


def _make_body(raw_text: str, backup: bool = True) -> bytes:
    return json.dumps({"raw_text": raw_text, "backup": backup}).encode("utf-8")


class TestHandleEnvUpdate:

    def test_happy_path_writes_file_and_returns_backup(self, env_update_env):
        install_dir, data_dir = env_update_env
        body = _make_body("ODS_AGENT_KEY=newvalue\nGGUF_FILE=/models/foo.gguf\n")
        handler = _FakeHandler(body)

        _mod.AgentHandler._handle_env_update(handler)

        assert handler.response_code == 200
        resp = handler.parse_response()
        assert resp["status"] == "ok"
        assert resp["backup_path"].startswith("data/config-backups/.env.backup.")
        env_text = (install_dir / ".env").read_text(encoding="utf-8")
        assert "ODS_AGENT_KEY=newvalue" in env_text
        assert "GGUF_FILE=/models/foo.gguf" in env_text
        # backup file actually exists where the response says it does
        backup_files = list((data_dir / "config-backups").glob(".env.backup.*"))
        assert len(backup_files) == 1

    def test_413_oversize_body(self, env_update_env):
        # Construct headers claiming body is too large; rfile content is irrelevant.
        handler = _FakeHandler(b"x", headers={"Content-Length": str(_mod.MAX_BODY + 999999) if hasattr(_mod, "MAX_BODY") else "100000"})
        # MAX_ENV_BODY is hard-coded to 65536 inside the handler.
        handler.headers["Content-Length"] = "70000"

        _mod.AgentHandler._handle_env_update(handler)

        assert handler.response_code == 413
        assert "too large" in handler.parse_response()["error"].lower()

    def test_accepts_unknown_key_with_warning(self, env_update_env):
        """Non-schema keys are accepted (warn, not reject) so extension-added
        keys (e.g. JWT_SECRET from LibreChat) don't break Settings save."""
        install_dir, data_dir = env_update_env
        (install_dir / ".env").write_text("ODS_AGENT_KEY=old\n", encoding="utf-8")
        body = _make_body("ODS_AGENT_KEY=old\nNOT_IN_SCHEMA=foo\n")
        handler = _FakeHandler(body)

        _mod.AgentHandler._handle_env_update(handler)

        assert handler.response_code == 200
        env_text = (install_dir / ".env").read_text(encoding="utf-8")
        assert "NOT_IN_SCHEMA=foo" in env_text

    def test_400_malformed_line(self, env_update_env):
        body = _make_body("THIS_LINE_HAS_NO_EQUALS\n")
        handler = _FakeHandler(body)

        _mod.AgentHandler._handle_env_update(handler)

        assert handler.response_code == 400
        assert "Malformed line" in handler.parse_response()["error"]

    def test_400_control_char_in_value(self, env_update_env):
        body = _make_body("ODS_AGENT_KEY=foo\x00bar\n")
        handler = _FakeHandler(body)

        _mod.AgentHandler._handle_env_update(handler)

        assert handler.response_code == 400
        assert "control characters" in handler.parse_response()["error"]

    def test_400_control_char_escape_sequence(self, env_update_env):
        # ESC (0x1b) — common in injected ANSI sequences
        body = _make_body("ODS_AGENT_KEY=foo\x1b[31mbar\n")
        handler = _FakeHandler(body)

        _mod.AgentHandler._handle_env_update(handler)

        assert handler.response_code == 400

    def test_tab_in_value_is_allowed(self, env_update_env):
        # Tab is the only sub-32 char that should pass through.
        body = _make_body("ODS_AGENT_KEY=foo\tbar\n")
        handler = _FakeHandler(body)

        _mod.AgentHandler._handle_env_update(handler)

        assert handler.response_code == 200

    def test_409_lock_contention(self, env_update_env):
        body = _make_body("ODS_AGENT_KEY=newvalue\n")
        handler = _FakeHandler(body)

        assert _mod._model_activate_lock.acquire(blocking=False)
        try:
            _mod.AgentHandler._handle_env_update(handler)
        finally:
            _mod._model_activate_lock.release()

        assert handler.response_code == 409
        assert "in progress" in handler.parse_response()["error"]

    def test_500_missing_schema(self, env_update_env):
        install_dir, _ = env_update_env
        (install_dir / ".env.schema.json").unlink()
        body = _make_body("ODS_AGENT_KEY=newvalue\n")
        handler = _FakeHandler(body)

        _mod.AgentHandler._handle_env_update(handler)

        assert handler.response_code == 500
        assert ".env.schema.json not found" in handler.parse_response()["error"]


class TestHandleModelDownloadCancel:

    def test_returns_no_download_when_idle(self, monkeypatch):
        handler = _FakeHandler(b"")
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        monkeypatch.setattr(_mod, "_model_download_thread", None)
        _mod._model_download_cancel.clear()

        _mod.AgentHandler._handle_model_download_cancel(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "no_download"
        assert _mod._model_download_cancel.is_set() is False

    def test_sets_cancel_flag_and_kills_active_proc(self, monkeypatch):
        class _AliveThread:
            def is_alive(self):
                return True

        class _FakeProc:
            def __init__(self):
                self.killed = False

            def kill(self):
                self.killed = True

        handler = _FakeHandler(b"")
        proc = _FakeProc()
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        monkeypatch.setattr(_mod, "_model_download_thread", _AliveThread())
        monkeypatch.setattr(_mod, "_model_download_proc", proc)
        monkeypatch.setattr(_mod, "_model_download_cancelable", True)
        _mod._model_download_cancel.clear()

        _mod.AgentHandler._handle_model_download_cancel(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "cancelling"
        assert _mod._model_download_cancel.is_set() is True
        assert proc.killed is True


class TestModelActivationOwnership:

    @pytest.mark.parametrize("contending_model", ["target-a", "target-b"])
    def test_concurrent_request_reports_exact_active_target(
        self,
        monkeypatch,
        contending_model,
    ):
        entered = _mod.threading.Event()
        release = _mod.threading.Event()

        def blocking_activate(handler, model_id):
            assert model_id == "target-a"
            entered.set()
            assert release.wait(timeout=2)
            _mod.json_response(handler, 200, {"status": "activated", "model_id": model_id})

        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        owner = _FakeHandler(json.dumps({"model_id": "target-a"}).encode("utf-8"))
        owner._do_model_activate = types.MethodType(blocking_activate, owner)
        owner_thread = _mod.threading.Thread(
            target=_mod.AgentHandler._handle_model_activate,
            args=(owner,),
        )
        owner_thread.start()
        assert entered.wait(timeout=2)

        contender = _FakeHandler(
            json.dumps({"model_id": contending_model}).encode("utf-8")
        )
        _mod.AgentHandler._handle_model_activate(contender)

        assert contender.response_code == 409
        response = contender.parse_response()
        assert response["activeModelId"] == "target-a"
        release.set()
        owner_thread.join(timeout=2)
        assert not owner_thread.is_alive()
        assert owner.response_code == 200
        assert _mod._model_activation_target is None
        assert _mod._model_activate_lock.acquire(blocking=False)
        _mod._model_activate_lock.release()

    def test_target_is_cleared_when_activation_raises(self, monkeypatch):
        def failed_activate(_handler, _model_id):
            raise RuntimeError("activation failed")

        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        handler = _FakeHandler(json.dumps({"model_id": "target-a"}).encode("utf-8"))
        handler._do_model_activate = types.MethodType(failed_activate, handler)

        with pytest.raises(RuntimeError, match="activation failed"):
            _mod.AgentHandler._handle_model_activate(handler)

        assert _mod._model_activation_target is None
        assert _mod._model_activate_lock.acquire(blocking=False)
        _mod._model_activate_lock.release()

    def test_model_status_reports_activation_lifecycle(self, monkeypatch):
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        acquired, _active = _mod._begin_model_lifecycle("model_activation", "target-a")
        assert acquired
        try:
            handler = _FakeHandler(b"")
            _mod.AgentHandler._handle_model_status(handler)
        finally:
            _mod._end_model_lifecycle("model_activation")

        assert handler.response_code == 200
        response = handler.parse_response()
        assert response["status"] == "idle"
        assert response["lifecycleActive"] is True
        assert response["activeOperation"] == "model_activation"
        assert response["activeTarget"] == "target-a"
        assert response["activeModelId"] == "target-a"

    def test_non_activation_lock_owner_reports_unknown_target(self, monkeypatch):
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        handler = _FakeHandler(json.dumps({"model_id": "target-a"}).encode("utf-8"))
        assert _mod._model_activate_lock.acquire(blocking=False)
        try:
            _mod.AgentHandler._handle_model_activate(handler)
        finally:
            _mod._model_activate_lock.release()

        assert handler.response_code == 409
        assert handler.parse_response()["activeModelId"] is None


class TestModelLifecycleSerialization:

    @pytest.fixture(autouse=True)
    def _auth(self, monkeypatch):
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")

    def test_activation_reports_background_update_owner(self):
        acquired, _active = _mod._begin_model_lifecycle("system_update")
        assert acquired
        try:
            handler = _FakeHandler(json.dumps({"model_id": "target"}).encode("utf-8"))
            _mod.AgentHandler._handle_model_activate(handler)
        finally:
            _mod._end_model_lifecycle("system_update")

        assert handler.response_code == 409
        response = handler.parse_response()
        assert response["code"] == "model_lifecycle_busy"
        assert response["activeOperation"] == "system_update"

    def test_delete_reports_artifact_verification_owner(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "install"
        models_dir = install_dir / "data" / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "target.gguf").write_bytes(b"model")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        acquired, _active = _mod._begin_model_lifecycle(
            "artifact_verification",
            "other.gguf",
        )
        assert acquired
        try:
            handler = _FakeHandler(
                json.dumps({"gguf_file": "target.gguf"}).encode("utf-8")
            )
            _mod.AgentHandler._handle_model_delete(handler)
        finally:
            _mod._end_model_lifecycle("artifact_verification")

        assert handler.response_code == 409
        response = handler.parse_response()
        assert response["activeOperation"] == "artifact_verification"
        assert response["activeTarget"] == "other.gguf"

    def test_update_reports_model_delete_owner(self, monkeypatch):
        monkeypatch.setattr(_mod, "_update_thread", None)
        acquired, _active = _mod._begin_model_lifecycle("model_delete", "target.gguf")
        assert acquired
        try:
            handler = _FakeHandler(b"{}")
            _mod.AgentHandler._handle_update_start(handler)
        finally:
            _mod._end_model_lifecycle("model_delete")

        assert handler.response_code == 409
        response = handler.parse_response()
        assert response["code"] == "model_lifecycle_busy"
        assert response["activeOperation"] == "model_delete"

    def test_download_reports_model_activation_owner(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "install"
        (install_dir / "config").mkdir(parents=True)
        (install_dir / "data" / "models").mkdir(parents=True)
        model = {
            "gguf_file": "target.gguf",
            "gguf_url": "https://example.test/target.gguf",
            "gguf_sha256": hashlib.sha256(b"model").hexdigest(),
        }
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": [model]}),
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        acquired, _active = _mod._begin_model_lifecycle("model_activation", "other")
        assert acquired
        try:
            handler = _FakeHandler(json.dumps({
                "gguf_file": model["gguf_file"],
                "gguf_url": model["gguf_url"],
            }).encode("utf-8"))
            _mod.AgentHandler._handle_model_download(handler)
        finally:
            _mod._end_model_lifecycle("model_activation")

        assert handler.response_code == 409
        response = handler.parse_response()
        assert response["activeOperation"] == "model_activation"
        assert response["activeTarget"] == "other"


class TestModelActivationModeAndMacosBridge:

    def test_cloud_mode_rejects_local_activation_before_any_state_change(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        env_path = install_dir / ".env"
        original_env = (
            "ODS_MODE=cloud\n"
            "GPU_BACKEND=apple\n"
            "LLM_MODEL=cloud-router-model\n"
            "GGUF_FILE=\n"
            "LLM_API_URL=http://litellm:4000\n"
        )
        env_path.write_text(original_env, encoding="utf-8")
        original_mtime = env_path.stat().st_mtime_ns
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        def unexpected_mutation(*_args, **_kwargs):
            raise AssertionError("cloud rejection must occur before runtime or bridge mutation")

        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        monkeypatch.setattr(_mod, "_configure_macos_llm_bridge", unexpected_mutation)
        monkeypatch.setattr(_mod.subprocess, "run", unexpected_mutation)
        handler = _FakeHandler(json.dumps({"model_id": "local-target"}).encode("utf-8"))
        handler._do_model_activate = types.MethodType(
            _mod.AgentHandler._do_model_activate,
            handler,
        )

        _mod.AgentHandler._handle_model_activate(handler)

        assert handler.response_code == 409
        response = handler.parse_response()
        assert response == {
            "error": "local_mode_required",
            "code": "local_mode_required",
            "reason": "effective_mode_not_local",
            "message": "Local model activation is unavailable while effective ODS mode is 'cloud'.",
            "effectiveMode": "cloud",
            "configuredMode": "cloud",
            "mode": "cloud",
            "requestedModelId": "local-target",
            "activeModelId": "cloud-router-model",
        }
        assert env_path.read_text(encoding="utf-8") == original_env
        assert env_path.stat().st_mtime_ns == original_mtime
        assert sorted(path.name for path in install_dir.iterdir()) == [".env"]
        assert _mod._model_activation_target is None
        assert _mod._model_activate_lock.acquire(blocking=False)
        _mod._model_activate_lock.release()

    def test_cloud_startup_cannot_be_changed_to_local_by_editing_env(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        env_path = install_dir / ".env"
        original_env = (
            "ODS_MODE=local\n"
            "LLM_MODEL=cloud-router-model\n"
            "GGUF_FILE=\n"
        )
        env_path.write_text(original_env, encoding="utf-8")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "STARTUP_ODS_MODE", "cloud")

        def unexpected_mutation(*_args, **_kwargs):
            raise AssertionError("mode mismatch must fail before runtime mutation")

        monkeypatch.setattr(_mod.subprocess, "run", unexpected_mutation)
        handler = _FakeHandler(b"")

        _mod.AgentHandler._do_model_activate(handler, "local-target")

        assert handler.response_code == 409
        response = handler.parse_response()
        assert response["code"] == "ods_mode_mismatch"
        assert response["reason"] == "mode_mismatch"
        assert response["effectiveMode"] == "cloud"
        assert response["configuredMode"] == "local"
        assert response["requestedModelId"] == "local-target"
        assert response["activeModelId"] == "cloud-router-model"
        assert env_path.read_text(encoding="utf-8") == original_env
        assert sorted(path.name for path in install_dir.iterdir()) == [".env"]

    def test_unknown_startup_mode_fails_closed(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        env_path = install_dir / ".env"
        env_path.write_text("ODS_MODE=local\n", encoding="utf-8")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "STARTUP_ODS_MODE", "unknown")
        handler = _FakeHandler(b"")

        _mod.AgentHandler._do_model_activate(handler, "local-target")

        assert handler.response_code == 409
        response = handler.parse_response()
        assert response["code"] == "ods_mode_unknown"
        assert response["reason"] == "mode_unknown"
        assert response["effectiveMode"] == "unknown"
        assert response["configuredMode"] == "local"

    @pytest.mark.parametrize("mode", ["local", "hybrid", "lemonade"])
    def test_matching_local_capable_modes_are_allowed(self, mode):
        assert _mod._model_activation_mode_denial(mode, mode) is None

    def test_cloud_mode_without_active_target_returns_explicit_null(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        (install_dir / ".env").write_text("ODS_MODE=cloud\n", encoding="utf-8")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        handler = _FakeHandler(b"")

        _mod.AgentHandler._do_model_activate(handler, "local-target")

        assert handler.response_code == 409
        assert handler.parse_response()["activeModelId"] is None

    def test_unreadable_persisted_mode_fails_without_writing_state(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        env_path = install_dir / ".env"
        original_env = "ODS_MODE=local\nGGUF_FILE=old-model.gguf\n"
        env_path.write_text(original_env, encoding="utf-8")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        def fail_load_env(_path):
            raise OSError("cannot read env")

        monkeypatch.setattr(_mod, "load_env", fail_load_env)
        handler = _FakeHandler(b"")

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert "cannot read env" in handler.parse_response()["error"]
        assert env_path.read_text(encoding="utf-8") == original_env
        assert sorted(path.name for path in install_dir.iterdir()) == [".env"]

    def test_direct_to_loopback_activation_recreates_bridge_after_old_listener_shutdown(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir = tmp_path / "install"
        models_dir = install_dir / "data" / "models"
        config_dir = install_dir / "config"
        llama_config_dir = config_dir / "llama-server"
        bin_dir = install_dir / "bin"
        models_dir.mkdir(parents=True)
        llama_config_dir.mkdir(parents=True)
        bin_dir.mkdir(parents=True)
        env_path = install_dir / ".env"
        env_path.write_text(
            "ODS_MODE=local\n"
            "GPU_BACKEND=apple\n"
            "BIND_ADDRESS=127.0.0.1\n"
            "ODS_MACOS_HOST_GATEWAY=192.168.106.1\n"
            "ODS_MACOS_VM_IP=192.168.106.2\n"
            "ODS_NATIVE_LLAMA_PORT=9090\n"
            "OLLAMA_PORT=8080\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "CTX_SIZE=2048\n",
            encoding="utf-8",
        )
        (config_dir / "model-library.json").write_text(
            json.dumps({
                "models": [{
                    "id": "target-model",
                    "gguf_file": "new-model.gguf",
                    "gguf_url": "https://example.test/new-model.gguf",
                    "gguf_sha256": hashlib.sha256(b"model").hexdigest(),
                    "llm_model_name": "new-model",
                    "context_length": 4096,
                }],
            }),
            encoding="utf-8",
        )
        (models_dir / "new-model.gguf").write_bytes(b"model")
        (llama_config_dir / "models.ini").write_text(
            "[old-model]\nfilename = old-model.gguf\n",
            encoding="utf-8",
        )
        llama_bin = bin_dir / "llama-server"
        llama_bin.write_bytes(b"binary")
        events = []

        def record_preflight(path):
            events.append(("bridge-preflight", _mod.load_env(path)["BIND_ADDRESS"]))
            return install_dir / "lib" / "constants.sh", install_dir / "lib" / "bridge-manager.sh"

        def record_stop(pid_file):
            events.append(("stop-old-direct-listener", pid_file.name))

        def record_bridge(path):
            current = _mod.load_env(path)
            assert current["BIND_ADDRESS"] == "127.0.0.1"
            assert current["GGUF_FILE"] == "new-model.gguf"
            events.append(("recreate-loopback-bridge", current["ODS_MACOS_HOST_GATEWAY"]))

        def record_launch(path, binary, log_path, pid_file):
            events.append(("launch-loopback-listener", binary.name, pid_file.name))

        def fake_run(cmd, **_kwargs):
            if cmd and cmd[0] == "curl":
                events.append(("validate-runtime", cmd[-1]))
                if str(cmd[-1]).endswith("/props"):
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=json.dumps({
                            "default_generation_settings": {"n_ctx": 4096},
                        }),
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    cmd,
                    0,
                    stdout=json.dumps({
                        "data": [{
                            "id": "new-model",
                            "status": {"value": "loaded"},
                        }],
                    }),
                    stderr="",
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(_mod.time, "sleep", lambda _seconds: None)
        monkeypatch.setattr(_mod, "_require_macos_bridge_manager", record_preflight)
        monkeypatch.setattr(_mod, "_stop_macos_native_llama_server", record_stop)
        monkeypatch.setattr(_mod, "_configure_macos_llm_bridge", record_bridge)
        monkeypatch.setattr(_mod, "_launch_native_llama_server", record_launch)
        monkeypatch.setattr(_mod, "_chat_completion_ready", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        handler = _FakeHandler(b"")

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 200
        receipt = handler.parse_response()
        assert receipt["status"] == "activated"
        assert receipt["model_id"] == "target-model"
        assert receipt["llm_model"] == "new-model"
        assert receipt["gguf_file"] == "new-model.gguf"
        assert receipt["context_length"] == 4096
        assert receipt["consumers"]["dashboard"] == "live_env"
        assert events[:5] == [
            ("bridge-preflight", "127.0.0.1"),
            ("stop-old-direct-listener", ".llama-server.pid"),
            ("recreate-loopback-bridge", "192.168.106.1"),
            ("launch-loopback-listener", "llama-server", ".llama-server.pid"),
            ("validate-runtime", "http://127.0.0.1:9090/v1/models"),
        ]

    def test_bridge_adapter_invokes_installed_shared_manager(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "install"
        lib_dir = install_dir / "lib"
        lib_dir.mkdir(parents=True)
        env_path = install_dir / ".env"
        env_path.write_text("ODS_MODE=local\nBIND_ADDRESS=127.0.0.1\n", encoding="utf-8")
        (lib_dir / "constants.sh").write_text("# constants\n", encoding="utf-8")
        (lib_dir / "bridge-manager.sh").write_text("# manager\n", encoding="utf-8")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "/bin/bash")
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        _mod._configure_macos_llm_bridge(env_path)

        assert len(calls) == 1
        cmd, kwargs = calls[0]
        assert cmd[:2] == ["/bin/bash", "-c"]
        assert cmd[-4:] == [
            str(install_dir),
            str(env_path),
            str(lib_dir / "constants.sh"),
            str(lib_dir / "bridge-manager.sh"),
        ]
        assert 'source "$bridge_manager_file"' in cmd[2]
        assert 'macos_configure_llm_bridge_from_env "$env_file" "$install_dir"' in cmd[2]
        assert 'cp -p "$target_file" "$tmp_file"' in cmd[2]
        assert kwargs == {
            "capture_output": True,
            "text": True,
            "timeout": 45,
            "check": False,
        }

    def test_bridge_adapter_falls_back_to_source_macos_lib(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "install"
        source_lib_dir = install_dir / "installers" / "macos" / "lib"
        source_lib_dir.mkdir(parents=True)
        env_path = install_dir / ".env"
        env_path.write_text("ODS_MODE=local\nBIND_ADDRESS=127.0.0.1\n", encoding="utf-8")
        (source_lib_dir / "constants.sh").write_text("# constants\n", encoding="utf-8")
        (source_lib_dir / "bridge-manager.sh").write_text("# manager\n", encoding="utf-8")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "/bin/bash")
        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        _mod._configure_macos_llm_bridge(env_path)

        assert len(calls) == 1
        cmd, _kwargs = calls[0]
        assert cmd[-4:] == [
            str(install_dir),
            str(env_path),
            str(source_lib_dir / "constants.sh"),
            str(source_lib_dir / "bridge-manager.sh"),
        ]

    def test_missing_bridge_manager_fails_before_listener_shutdown(
        self,
        tmp_path,
        monkeypatch,
    ):
        env_path = tmp_path / ".env"
        env_path.write_text("ODS_MODE=local\n", encoding="utf-8")

        def missing_manager(_path):
            raise RuntimeError("bridge manager missing")

        def unexpected_transition(*_args, **_kwargs):
            raise AssertionError("listener must remain untouched after failed preflight")

        monkeypatch.setattr(_mod, "_require_macos_bridge_manager", missing_manager)
        monkeypatch.setattr(_mod, "_stop_macos_native_llama_server", unexpected_transition)
        monkeypatch.setattr(_mod, "_configure_macos_llm_bridge", unexpected_transition)
        monkeypatch.setattr(_mod, "_launch_native_llama_server", unexpected_transition)

        with pytest.raises(RuntimeError, match="bridge manager missing"):
            _mod._restart_macos_native_llama_server(
                env_path,
                tmp_path / "llama-server",
                tmp_path / "llama.log",
                tmp_path / "llama.pid",
            )


class TestModelActivationLemonadePersistence:

    def test_activation_never_persists_blank_lemonade_model_during_restore(
        self,
        tmp_path,
        monkeypatch,
    ):
        install_dir = tmp_path / "install"
        models_dir = install_dir / "data" / "models"
        config_dir = install_dir / "config"
        (config_dir / "llama-server").mkdir(parents=True)
        (config_dir / "litellm").mkdir(parents=True)
        models_dir.mkdir(parents=True)
        env_path = install_dir / ".env"
        env_path.write_text(
            "ODS_MODE=local\n"
            "GPU_BACKEND=amd\n"
            "GGUF_FILE=old-model.gguf\n"
            "LLM_MODEL=old-model\n"
            "LEMONADE_MODEL=extra.old-model.gguf\n"
            "OLLAMA_PORT=11434\n"
            "MAX_CONTEXT=32768\n"
            "CTX_SIZE=32768\n",
            encoding="utf-8",
        )
        payload = b"new model"
        (models_dir / "new-model.gguf").write_bytes(payload)
        (config_dir / "model-library.json").write_text(
            json.dumps({
                "models": [{
                    "id": "target-model",
                    "gguf_file": "new-model.gguf",
                    "gguf_url": "https://example.test/new-model.gguf",
                    "gguf_sha256": hashlib.sha256(payload).hexdigest(),
                    "llm_model_name": "new-model",
                    "context_length": 65536,
                }],
            }),
            encoding="utf-8",
        )
        observed_envs = []

        def fake_compose_restart(_env):
            observed_envs.append(_mod.load_env(env_path).copy())
            raise RuntimeError("stop after env write")

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.delenv("ODS_HOST_INSTALL_DIR", raising=False)
        monkeypatch.setattr(
            _mod,
            "_resolve_lemonade_model_id",
            lambda _env, gguf_file, **_kwargs: f"extra.{gguf_file}",
        )
        monkeypatch.setattr(
            _mod, "_compose_restart_llama_server", fake_compose_restart
        )
        monkeypatch.setattr(
            _mod,
            "_capture_hermes_live_config",
            lambda *_args, **_kwargs: {"exists": False},
        )
        monkeypatch.setattr(
            _mod, "_remove_hermes_live_config", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            _mod, "_capture_perplexica_config", lambda *_args, **_kwargs: None
        )
        monkeypatch.setattr(
            _mod,
            "_capture_container_state",
            lambda _name: {"exists": False, "running": False},
        )
        handler = _FakeHandler(b"")

        _mod.AgentHandler._do_model_activate(handler, "target-model")

        assert handler.response_code == 500
        assert observed_envs
        pending_env = observed_envs[0]
        assert pending_env["GGUF_FILE"] == "new-model.gguf"
        assert pending_env["LEMONADE_MODEL"] == "extra.new-model.gguf"


class TestModelActivationRuntimeIdentity:

    @pytest.mark.parametrize(
        ("loaded", "expected"),
        [
            ("extra.target-model.gguf", True),
            ("target-model.gguf", True),
            ("extra.other-model.gguf", False),
            (False, False),
            ("", False),
            (None, False),
        ],
    )
    def test_lemonade_requires_exact_nonempty_target(self, loaded, expected):
        body = json.dumps({"status": "ok", "model_loaded": loaded})
        assert _mod._check_lemonade_health(body, "target-model.gguf") is expected

    def test_lemonade_rejects_target_when_health_is_not_ok(self):
        body = json.dumps({
            "status": "loading",
            "model_loaded": "extra.target-model.gguf",
        })
        assert _mod._check_lemonade_health(body, "target-model.gguf") is False

    @pytest.mark.parametrize(
        ("loaded", "expected"),
        [
            ("extra.target-model.gguf", True),
            (False, False),
            ("", False),
            (None, False),
        ],
    )
    def test_generic_lemonade_health_rejects_false_and_empty_identity(
        self,
        loaded,
        expected,
    ):
        assert _mod._check_lemonade_health(
            json.dumps({"status": "ok", "model_loaded": loaded})
        ) is expected

    @pytest.mark.parametrize(
        ("runtime_id", "status", "expected"),
        [
            ("target-runtime", "loaded", True),
            ("/models/Target-Model.GGUF", "loaded", True),
            ("other-runtime", "loaded", False),
            ("target-runtime", "loading", False),
            (False, "loaded", False),
            ("", "loaded", False),
        ],
    )
    def test_llama_requires_exact_loaded_target(self, runtime_id, status, expected):
        body = json.dumps({
            "data": [{"id": runtime_id, "status": {"value": status}}],
        })
        assert _mod._check_llama_model_identity(
            body,
            model_id="target-catalog-id",
            gguf_file="target-model.gguf",
            llm_model_name="target-runtime",
        ) is expected

    def test_llama_rejects_generic_health_and_nearby_model_name(self):
        assert _mod._check_llama_model_identity(
            '{"status":"ok"}',
            model_id="phi-4-q4",
            gguf_file="Phi-4-Q4_K_M.gguf",
            llm_model_name="phi-4",
        ) is False
        assert _mod._check_llama_model_identity(
            json.dumps({"data": [{"id": "phi-4-mini", "status": {"value": "loaded"}}]}),
            model_id="phi-4-q4",
            gguf_file="Phi-4-Q4_K_M.gguf",
            llm_model_name="phi-4",
        ) is False


class TestNarrowInstallPullFlags:
    """Filter flags used by the install pull step.

    Audit follow-up on PR #1057: narrowing must drop -f entries
    pointing at OTHER extensions, but keep base/GPU overlay and the
    target extension's own fragments.
    """

    def _ext_dirs(self, tmp_path):
        builtins = tmp_path / "extensions" / "services"
        users = tmp_path / "user-extensions"
        builtins.mkdir(parents=True)
        users.mkdir(parents=True)
        return builtins, users

    def test_drops_other_extension_compose(self, tmp_path, monkeypatch):
        builtins, users = self._ext_dirs(tmp_path)
        target_dir = builtins / "perplexica"
        other_dir = builtins / "searxng"
        target_dir.mkdir()
        other_dir.mkdir()
        target_compose = target_dir / "compose.yaml"
        other_compose = other_dir / "compose.yaml"
        target_compose.write_text("services: {}\n")
        other_compose.write_text("services: {}\n")

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtins)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", users)

        flags = [
            "-f", str(tmp_path / "docker-compose.base.yml"),
            "-f", str(tmp_path / "docker-compose.nvidia.yml"),
            "-f", str(target_compose),
            "-f", str(other_compose),
        ]
        narrowed = _mod._narrow_install_pull_flags(flags, "perplexica")

        assert "-f" in narrowed
        assert str(other_compose) not in narrowed
        assert str(target_compose) in narrowed

    def test_keeps_base_and_gpu_overlay(self, tmp_path, monkeypatch):
        builtins, users = self._ext_dirs(tmp_path)
        target_dir = builtins / "perplexica"
        target_dir.mkdir()
        target_compose = target_dir / "compose.yaml"
        target_compose.write_text("services: {}\n")

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtins)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", users)

        base = str(tmp_path / "docker-compose.base.yml")
        gpu = str(tmp_path / "docker-compose.nvidia.yml")
        flags = ["-f", base, "-f", gpu, "-f", str(target_compose)]
        narrowed = _mod._narrow_install_pull_flags(flags, "perplexica")

        assert base in narrowed
        assert gpu in narrowed
        assert str(target_compose) in narrowed

    def test_user_extension_target_is_kept(self, tmp_path, monkeypatch):
        builtins, users = self._ext_dirs(tmp_path)
        target_dir = users / "my-ext"
        other_dir = builtins / "searxng"
        target_dir.mkdir()
        other_dir.mkdir()
        target_compose = target_dir / "compose.yaml"
        other_compose = other_dir / "compose.yaml"
        target_compose.write_text("services: {}\n")
        other_compose.write_text("services: {}\n")

        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtins)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", users)

        flags = ["-f", str(target_compose), "-f", str(other_compose)]
        narrowed = _mod._narrow_install_pull_flags(flags, "my-ext")

        assert str(target_compose) in narrowed
        assert str(other_compose) not in narrowed


class TestNarrowedComposeSetResolves:
    """Validate that the narrowed compose set parses and contains the
    target service. Audit follow-up on PR #1057.
    """

    def test_returns_false_when_config_exits_nonzero(self, monkeypatch):
        recorded = []

        def fake_run(cmd, **kwargs):
            recorded.append(cmd)
            return _SubprocessResult(returncode=1, stdout="", stderr="depends on undefined service")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        ok = _mod._narrowed_compose_set_resolves(
            ["-f", "/tmp/base.yml"], "perplexica", "/tmp", 60,
        )
        assert ok is False
        assert recorded[0][:2] == ["docker", "compose"]
        assert "config" in recorded[0] and "--services" in recorded[0]

    def test_returns_false_when_target_service_missing_from_output(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return _SubprocessResult(returncode=0, stdout="searxng\nllama-server\n", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        ok = _mod._narrowed_compose_set_resolves([], "perplexica", "/tmp", 60)
        assert ok is False

    def test_returns_true_when_target_service_listed(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            return _SubprocessResult(returncode=0, stdout="perplexica\nsearxng\n", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        ok = _mod._narrowed_compose_set_resolves([], "perplexica", "/tmp", 60)
        assert ok is True

    def test_returns_false_on_subprocess_error(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise OSError("docker not found")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        ok = _mod._narrowed_compose_set_resolves([], "perplexica", "/tmp", 60)
        assert ok is False


class TestInstallPullFallsBackOnUnresolvedNarrow:
    """Source-level wire-up checks for the install pull fallback.

    These tests assert only that `_handle_install` *references* the
    helpers and contains the fallback assignment token; behavioural
    correctness of the narrow filter and the validator is covered by
    `TestNarrowInstallPullFlags` and `TestNarrowedComposeSetResolves`
    above. The token-presence pattern matches the established
    `TestInstallStartCommandNoDeps` convention in this file.
    """

    def test_install_references_narrow_helpers(self):
        import inspect
        src = inspect.getsource(_mod.AgentHandler._handle_install)
        assert "_narrowed_compose_set_resolves" in src, (
            "_handle_install source must reference _narrowed_compose_set_resolves"
        )
        assert "_narrow_install_pull_flags" in src, (
            "_handle_install source must reference _narrow_install_pull_flags"
        )

    def test_install_source_contains_full_flags_fallback_token(self):
        import inspect
        src = inspect.getsource(_mod.AgentHandler._handle_install)
        # Token-only check: confirms a `pull_flags = flags` assignment
        # exists somewhere in the handler. Does not verify control flow.
        assert "pull_flags = flags" in src, (
            "_handle_install source must contain a `pull_flags = flags` "
            "assignment (the fallback token)"
        )


class _SubprocessResult:
    """Minimal stand-in for subprocess.CompletedProcess."""

    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
# --- _precreate_data_dirs + install flow (PR 2A regressions) ---
#
# Defect 1/2: _run_install and _precreate_data_dirs must use _find_ext_dir()
# so built-in extensions (under EXTENSIONS_DIR) are found — the old
# USER_EXTENSIONS_DIR-only path silently no-op'd for every built-in.
#
# Defect 3: _precreate_data_dirs must create dirs for any relative bind
# source (not just "./data/..."), so extensions with "./upload:/..." style
# mounts also get their dirs pre-created. Anchored on INSTALL_DIR because
# Docker Compose v2 resolves relative bind paths against the project
# directory (the first -f file's parent = INSTALL_DIR), not against the
# individual fragment's directory.
#
# Defect 5: _handle_install must verify the container reached "running"
# state before reporting success — compose `up -d` returns 0 even for
# Created/Exited/Restarting containers.


class TestPrecreateDataDirs:

    def _write_compose(self, ext_dir: Path, volumes: list[str]):
        vol_yaml = "\n".join(f"      - {v}" for v in volumes)
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "compose.yaml").write_text(
            "services:\n"
            "  svc:\n"
            "    image: test:latest\n"
            "    volumes:\n" + vol_yaml + "\n",
            encoding="utf-8",
        )

    def _write_compose_with_user(
        self, ext_dir: Path, volumes: list[str], user: str,
    ):
        vol_yaml = "\n".join(f"      - {v}" for v in volumes)
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "compose.yaml").write_text(
            "services:\n"
            "  svc:\n"
            "    image: test:latest\n"
            f"    user: \"{user}\"\n"
            "    volumes:\n" + vol_yaml + "\n",
            encoding="utf-8",
        )

    def _write_manifest(self, ext_dir: Path, service_id: str, container_uid: int):
        (ext_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n"
            f"  id: {service_id}\n"
            f"  name: {service_id}\n"
            f"  container_uid: {container_uid}\n",
            encoding="utf-8",
        )

    def test_creates_dirs_for_builtin_ext_via_find_ext_dir(self, tmp_path, monkeypatch):
        """Defect 1/2: built-in extensions resolved via _find_ext_dir, not USER_EXTENSIONS_DIR."""
        pytest.importorskip("yaml")
        builtin_root = tmp_path / "builtin"
        user_root = tmp_path / "user"
        install_dir = tmp_path / "install"
        builtin_root.mkdir()
        user_root.mkdir()
        install_dir.mkdir()
        ext_dir = builtin_root / "svc-b"
        self._write_compose(ext_dir, ["./data/state:/state"])

        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        _mod._precreate_data_dirs("svc-b")

        # Dir lives under INSTALL_DIR (the Compose project directory),
        # NOT under ext_dir — matching where Compose actually mounts.
        assert (install_dir / "data" / "state").is_dir()
        assert not (ext_dir / "data" / "state").exists()

    def test_creates_dirs_for_non_data_prefix(self, tmp_path, monkeypatch):
        """Defect 3: relative bind sources outside './data/' must still be created."""
        pytest.importorskip("yaml")
        user_root = tmp_path / "user"
        builtin_root = tmp_path / "builtin"
        install_dir = tmp_path / "install"
        user_root.mkdir()
        builtin_root.mkdir()
        install_dir.mkdir()
        ext_dir = user_root / "svc-u"
        self._write_compose(ext_dir, ["./upload:/upload", "./data/state:/state"])

        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        _mod._precreate_data_dirs("svc-u")

        # Both non-"./data/" and "./data/..." mounts must materialise under
        # INSTALL_DIR (the Compose project directory).
        assert (install_dir / "upload").is_dir()
        assert (install_dir / "data" / "state").is_dir()

    def test_manifest_container_uid_fallback_chowns_when_root(
        self, tmp_path, monkeypatch,
    ):
        """Manifest container_uid covers images that set USER in Dockerfile."""
        pytest.importorskip("yaml")
        user_root = tmp_path / "user"
        builtin_root = tmp_path / "builtin"
        install_dir = tmp_path / "install"
        user_root.mkdir()
        builtin_root.mkdir()
        install_dir.mkdir()
        ext_dir = user_root / "svc-u"
        self._write_compose(ext_dir, ["./data/gaia:/home/gaia/.gaia"])
        self._write_manifest(ext_dir, "svc-u", 10001)
        chowns = []

        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_fs_type", lambda path: None)
        monkeypatch.setattr(_mod.os, "getuid", lambda: 0, raising=False)
        monkeypatch.setattr(
            _mod.os,
            "chown",
            lambda path, uid, gid: chowns.append((Path(path), uid, gid)),
            raising=False,
        )

        _mod._precreate_data_dirs("svc-u")

        data_dir = install_dir / "data" / "gaia"
        assert data_dir.is_dir()
        assert chowns == [(data_dir, 10001, 10001)]

    def test_compose_user_takes_precedence_over_manifest_uid(
        self, tmp_path, monkeypatch,
    ):
        """Explicit compose user remains the source of truth when present."""
        pytest.importorskip("yaml")
        user_root = tmp_path / "user"
        builtin_root = tmp_path / "builtin"
        install_dir = tmp_path / "install"
        user_root.mkdir()
        builtin_root.mkdir()
        install_dir.mkdir()
        ext_dir = user_root / "svc-u"
        self._write_compose_with_user(
            ext_dir,
            ["./data/gaia:/home/gaia/.gaia"],
            "12345:12345",
        )
        self._write_manifest(ext_dir, "svc-u", 10001)
        chowns = []

        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "_fs_type", lambda path: None)
        monkeypatch.setattr(_mod.os, "getuid", lambda: 0, raising=False)
        monkeypatch.setattr(
            _mod.os,
            "chown",
            lambda path, uid, gid: chowns.append((Path(path), uid, gid)),
            raising=False,
        )

        _mod._precreate_data_dirs("svc-u")

        data_dir = install_dir / "data" / "gaia"
        assert data_dir.is_dir()
        assert chowns == [(data_dir, 12345, 12345)]

    def test_skips_named_volumes(self, tmp_path, monkeypatch):
        """Named volumes (no '/') must not trigger filesystem creation."""
        pytest.importorskip("yaml")
        user_root = tmp_path / "user"
        builtin_root = tmp_path / "builtin"
        install_dir = tmp_path / "install"
        user_root.mkdir()
        builtin_root.mkdir()
        install_dir.mkdir()
        ext_dir = user_root / "svc-n"
        self._write_compose(ext_dir, ["named_vol:/var/lib/data"])

        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)

        _mod._precreate_data_dirs("svc-n")

        # Named volume must not materialize as a directory anywhere we own.
        assert not (ext_dir / "named_vol").exists()
        assert not (install_dir / "named_vol").exists()


class TestInstallRunningStateVerification:
    """Defect 5: `_handle_install` must poll container state before reporting success."""

    def _install_source(self):
        import inspect
        return inspect.getsource(_mod.AgentHandler._handle_install)

    def test_install_uses_find_ext_dir(self):
        """Defect 1: _run_install resolves ext_dir via _find_ext_dir, not USER_EXTENSIONS_DIR."""
        src = self._install_source()
        assert "_find_ext_dir(service_id)" in src
        assert "USER_EXTENSIONS_DIR / service_id" not in src

    def test_install_polls_docker_inspect_state(self):
        """State-poll loop must run `docker inspect` and check running state."""
        src = self._install_source()
        assert "docker" in src and "inspect" in src
        assert "{{.State.Status}}" in src
        assert 'state == "running"' in src

    def test_install_writes_error_when_state_not_running(self):
        """Failed state-poll must surface as progress error, not 'started'.

        The error message is built via f-string with the
        manifest-driven `startup_timeout` rather than the literal "15s",
        so we assert the constant prefix and the timeout reference, not
        a hardcoded duration.
        """
        src = self._install_source()
        # Error path uses the existing _write_progress("error", ...) API.
        assert '_write_progress(service_id, "error"' in src
        # Error message template carries the dynamic startup_timeout.
        assert "did not reach running state within" in src
        assert "{startup_timeout}s" in src

    def test_install_supports_startup_check_opt_out(self):
        """One-shot / setup-only extensions can set
        `service.startup_check: false` in their manifest to skip the
        running-state poll. The install completes after `compose up -d`
        returns 0; the inspect loop is gated on `if startup_check:`.
        """
        src = self._install_source()
        # Manifest field is read with True default for back-compat.
        assert 'startup_check = install_service_def.get("startup_check", True)' in src
        # The state-poll loop is conditionally entered.
        assert "if startup_check:" in src


# --- Enable-retry (PR 3A regression) ---
#
# When /v1/extension/start is called against a service whose extension-progress
# file shows status=error (prior failed install), the host agent must:
#   * re-run the post_install hook if declared (env vars populated by the hook
#     may be missing from the previous failure),
#   * write progress transitions (starting → setup_hook → started/error) so the
#     dashboard UI updates instead of displaying the stale error, and
#   * fall back to the existing synchronous compose path for any service that
#     isn't in an error state.
#
# Pre-fix, _handle_extension hit docker_compose_action directly without writing
# progress or re-running the hook, leaving the UI permanently stuck.


class _ImmediateThread:
    """Run thread targets synchronously so tests can assert on results."""
    def __init__(self, target=None, daemon=None, **kwargs):
        self._target = target

    def start(self):
        self._target()


class TestEnableRetry:

    def _write_manifest(self, ext_dir: Path, with_hook: bool = True,
                        service_lines: str = ""):
        ext_dir.mkdir(parents=True, exist_ok=True)
        service_block = "service:\n  port: 1234\n"
        if service_lines:
            service_block += service_lines
        if with_hook:
            hook = ext_dir / "setup.sh"
            hook.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            hook.chmod(0o755)
            (ext_dir / "manifest.yaml").write_text(
                service_block +
                "  hooks:\n"
                "    post_install: setup.sh\n",
                encoding="utf-8",
            )
        else:
            (ext_dir / "manifest.yaml").write_text(
                service_block,
                encoding="utf-8",
            )

    def _write_progress_file(self, data_dir: Path, service_id: str, status: str):
        progress_dir = data_dir / "extension-progress"
        progress_dir.mkdir(parents=True, exist_ok=True)
        (progress_dir / f"{service_id}.json").write_text(
            json.dumps({"service_id": service_id, "status": status}),
            encoding="utf-8",
        )

    def _progress(self, data_dir: Path, service_id: str):
        pf = data_dir / "extension-progress" / f"{service_id}.json"
        if not pf.exists():
            return None
        return json.loads(pf.read_text(encoding="utf-8"))

    def _body(self, service_id: str) -> bytes:
        return json.dumps({"service_id": service_id}).encode("utf-8")

    @pytest.fixture
    def retry_env(self, tmp_path, monkeypatch):
        pytest.importorskip("yaml")
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        builtin_root = tmp_path / "builtin"
        user_root = tmp_path / "user"
        builtin_root.mkdir()
        user_root.mkdir()

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "DATA_DIR", data_dir)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        monkeypatch.setattr(_mod, "_usable_bash", "bash")

        # Drop the per-service lock so retries across tests don't deadlock.
        _mod._service_locks.pop("fakesvc", None)

        # Force threading.Thread to run targets synchronously so the assertions
        # below execute after the retry worker completes.
        monkeypatch.setattr(_mod.threading, "Thread", _ImmediateThread)

        return install_dir, data_dir, builtin_root, user_root

    def test_retry_after_error_runs_hook_and_writes_started(self, retry_env, monkeypatch):
        _, data_dir, builtin_root, _ = retry_env
        ext_dir = builtin_root / "fakesvc"
        self._write_manifest(ext_dir, with_hook=True)
        self._write_progress_file(data_dir, "fakesvc", "error")

        hook_cmds = []

        def fake_run(cmd, *args, **kwargs):
            if cmd[:3] == ["docker", "inspect", "--format"]:
                return subprocess.CompletedProcess(args=cmd, returncode=0,
                                                   stdout="running|", stderr="")
            hook_cmds.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0,
                                               stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        compose_calls = []

        def fake_compose(sid, action):
            compose_calls.append((sid, action))
            return True, ""

        monkeypatch.setattr(_mod, "docker_compose_action", fake_compose)

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        assert handler.response_code == 202
        assert handler.parse_response()["status"] == "retrying"
        # post_install hook was invoked via bash against setup.sh
        assert any(
            len(c) >= 2 and c[0] == _mod._usable_bash and c[1].endswith("setup.sh")
            for c in hook_cmds
        ), f"expected setup.sh bash invocation, saw {hook_cmds}"
        # docker compose start was called after the hook
        assert ("fakesvc", "start") in compose_calls
        # Progress landed on 'started'
        progress = self._progress(data_dir, "fakesvc")
        assert progress is not None
        assert progress["status"] == "started"

    def test_retry_startup_check_failure_writes_error(self, retry_env, monkeypatch):
        _, data_dir, builtin_root, _ = retry_env
        ext_dir = builtin_root / "fakesvc"
        self._write_manifest(
            ext_dir, with_hook=False,
            service_lines="  startup_timeout: 1\n",
        )
        self._write_progress_file(data_dir, "fakesvc", "error")

        monkeypatch.setattr(_mod, "docker_compose_action",
                            lambda sid, act: (True, ""))
        ticks = iter([0, 0, 2])
        monkeypatch.setattr(_mod.time, "monotonic", lambda: next(ticks, 2))
        monkeypatch.setattr(_mod.time, "sleep", lambda *_args: None)

        inspect_calls = []

        def fake_run(cmd, *args, **kwargs):
            inspect_calls.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0,
                                               stdout="exited|boom", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        assert handler.response_code == 202
        assert any(cmd[:3] == ["docker", "inspect", "--format"]
                   for cmd in inspect_calls)
        progress = self._progress(data_dir, "fakesvc")
        assert progress is not None
        assert progress["status"] == "error"
        assert "state=exited" in (progress["error"] or "")
        assert "boom" in (progress["error"] or "")

    def test_retry_startup_check_opt_out_writes_started(self, retry_env, monkeypatch):
        _, data_dir, builtin_root, _ = retry_env
        ext_dir = builtin_root / "fakesvc"
        self._write_manifest(
            ext_dir, with_hook=False,
            service_lines="  startup_check: false\n",
        )
        self._write_progress_file(data_dir, "fakesvc", "error")

        monkeypatch.setattr(_mod, "docker_compose_action",
                            lambda sid, act: (True, ""))

        def fake_run(*args, **kwargs):
            pytest.fail("startup_check false should not inspect container state")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        assert handler.response_code == 202
        progress = self._progress(data_dir, "fakesvc")
        assert progress is not None
        assert progress["status"] == "started"

    def test_retry_hook_failure_writes_error_and_skips_compose(self, retry_env, monkeypatch):
        _, data_dir, builtin_root, _ = retry_env
        ext_dir = builtin_root / "fakesvc"
        self._write_manifest(ext_dir, with_hook=True)
        self._write_progress_file(data_dir, "fakesvc", "error")

        def fake_run(cmd, *args, **kwargs):
            return subprocess.CompletedProcess(args=cmd, returncode=1,
                                               stdout="", stderr="hook boom")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)

        compose_calls = []
        monkeypatch.setattr(
            _mod, "docker_compose_action",
            lambda sid, act: (compose_calls.append((sid, act)) or (True, "")),
        )

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        assert handler.response_code == 202
        progress = self._progress(data_dir, "fakesvc")
        assert progress is not None
        assert progress["status"] == "error"
        assert "hook boom" in (progress["error"] or "")
        # Hook failure must NOT proceed to compose start
        assert compose_calls == []

    def test_no_progress_file_uses_sync_path_without_progress_write(self, retry_env, monkeypatch):
        _, data_dir, builtin_root, _ = retry_env
        ext_dir = builtin_root / "fakesvc"
        self._write_manifest(ext_dir, with_hook=True)
        # Deliberately no progress file.

        hook_cmds = []

        def fake_run(cmd, *args, **kwargs):
            hook_cmds.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0,
                                               stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "docker_compose_action",
                            lambda sid, act: (True, ""))

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        # Synchronous success → 200, not 202
        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "ok"
        # Sync path must not re-run the hook
        assert not any(
            len(c) >= 2 and c[0] == "bash" and c[1].endswith("setup.sh")
            for c in hook_cmds
        )
        # Sync path must not write a progress file
        assert self._progress(data_dir, "fakesvc") is None

    def test_progress_status_started_uses_sync_path(self, retry_env, monkeypatch):
        _, data_dir, builtin_root, _ = retry_env
        ext_dir = builtin_root / "fakesvc"
        self._write_manifest(ext_dir, with_hook=True)
        self._write_progress_file(data_dir, "fakesvc", "started")

        hook_cmds = []

        def fake_run(cmd, *args, **kwargs):
            hook_cmds.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0,
                                               stdout="", stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(_mod, "docker_compose_action",
                            lambda sid, act: (True, ""))

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        assert handler.response_code == 200
        # Sync path must not re-run the hook
        assert not any(
            len(c) >= 2 and c[0] == "bash" and c[1].endswith("setup.sh")
            for c in hook_cmds
        )
        # Progress must be unchanged
        assert self._progress(data_dir, "fakesvc")["status"] == "started"


class TestInstallStatePollBehavior:
    """End-to-end behavioral tests for the running-state poll inside
    ``AgentHandler._handle_install``.

    The existing :class:`TestInstallRunningStateVerification` class above is
    100% source-inspection (asserts substrings appear in the function body).
    This class drives the real handler over HTTP with a mocked
    ``subprocess.run`` so that a refactor that preserves the substrings but
    breaks the runtime behavior would still get caught.

    Pattern matches :class:`TestComposeCacheInvalidationWire` and
    :class:`TestComposeToggleWire` above:
      * Spin up an in-process ``HTTPServer`` bound to ``AgentHandler``.
      * POST to ``/v1/extension/install`` with the bearer token.
      * Wait for the install thread to write a terminal status to the
        progress file (``status in {'started', 'error'}``).
      * Assert on the progress payload + on the recorded subprocess calls.

    Time is virtualised — ``time.sleep`` and ``time.monotonic`` are
    monkeypatched on the host-agent module so a 15-second deadline elapses
    instantly. Tests must not actually wait wall-clock seconds.
    """

    PROGRESS_WAIT_SECONDS = 5.0

    def _make_extension(self, user_root, sid, *, startup_check=True,
                        startup_timeout=None, container_name=None):
        """Create a minimal user-extension dir with manifest only.

        No ``compose.yaml`` is written — that keeps ``_precreate_data_dirs``
        an early-return no-op so the only ``subprocess.run`` invocations are
        the ones the install path itself issues (compose pull / compose up /
        docker inspect).
        """
        import yaml  # PyYAML is a hard dep of dashboard-api; if missing the
                    # whole test module would already have failed at import.
        ext_dir = user_root / sid
        ext_dir.mkdir(parents=True)
        service_def = {}
        if startup_check is False:
            service_def["startup_check"] = False
        if startup_timeout is not None:
            service_def["startup_timeout"] = startup_timeout
        if container_name is not None:
            service_def["container_name"] = container_name
        manifest = {
            "schema_version": "ods.services.v1",
            "id": sid,
            "service": service_def,
        }
        (ext_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
        return ext_dir

    def _post_install(self, port, key, sid):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/extension/install",
            data=json.dumps({"service_id": sid}).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 202
            return json.loads(resp.read())

    def _wait_for_terminal(self, progress_file, timeout=None):
        import time as _real_time
        timeout = timeout if timeout is not None else self.PROGRESS_WAIT_SECONDS
        deadline = _real_time.monotonic() + timeout
        while _real_time.monotonic() < deadline:
            if progress_file.exists():
                try:
                    payload = json.loads(progress_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    payload = None
                if payload and payload.get("status") in {"started", "error"}:
                    return payload
            _real_time.sleep(0.05)
        raise AssertionError(
            f"Install thread did not reach a terminal status within {timeout}s; "
            f"last seen: {progress_file.read_text(encoding='utf-8') if progress_file.exists() else '<missing>'}"
        )

    def _setup_agent(self, tmp_path, monkeypatch, *, sid, startup_check=True,
                     startup_timeout=None, container_name=None):
        """Common scaffolding: temp dirs, manifest, monkeypatched module
        constants, virtual clock, returns ``(install_dir, progress_file, ext_dir)``."""
        install_dir = tmp_path / "install"
        data_dir = tmp_path / "data"
        user_root = tmp_path / "user-extensions"
        builtin_root = tmp_path / "builtin-empty"
        install_dir.mkdir()
        data_dir.mkdir()
        user_root.mkdir()
        builtin_root.mkdir()
        # Pre-populate compose flags so resolve_compose_flags() doesn't
        # shell out to resolve-compose-stack.sh (which doesn't exist here).
        (install_dir / ".compose-flags").write_text(
            "--env-file .env -f docker-compose.base.yml", encoding="utf-8",
        )

        ext_dir = self._make_extension(
            user_root, sid,
            startup_check=startup_check,
            startup_timeout=startup_timeout,
            container_name=container_name,
        )

        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "DATA_DIR", data_dir)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")

        # Virtual clock so the 15s startup_timeout deadline elapses
        # instantly. ``time.sleep(1)`` advances the fake clock by 1.0 and
        # returns immediately; ``time.monotonic()`` returns the current value.
        # Replace ``_mod.time`` with a stub namespace rather than mutating
        # attributes on the real ``time`` module — otherwise the test helper
        # itself (which uses real ``time.sleep`` to wait for the install
        # thread) ends up calling the no-op fake and busy-loops in zero
        # wall-clock time, racing the install thread.
        import time as _real_time
        clock = [0.0]
        def fake_monotonic():
            return clock[0]
        def fake_sleep(seconds):
            clock[0] += float(seconds)
        fake_time = types.SimpleNamespace(
            monotonic=fake_monotonic,
            sleep=fake_sleep,
            time=_real_time.time,
        )
        monkeypatch.setattr(_mod, "time", fake_time)

        progress_file = data_dir / "extension-progress" / f"{sid}.json"
        return install_dir, progress_file, ext_dir

    def _start_server(self, monkeypatch):
        import threading
        from http.server import HTTPServer
        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, port

    @staticmethod
    def _is_inspect_call(call):
        """Return True if ``call`` is the docker-inspect state probe."""
        argv = call["argv"]
        return (
            len(argv) >= 2
            and argv[0] == "docker"
            and argv[1] == "inspect"
        )

    @staticmethod
    def _is_compose_call(call, verb):
        """Return True if ``call`` is a ``docker compose <verb> ...`` call."""
        argv = call["argv"]
        if len(argv) < 3 or argv[0] != "docker" or argv[1] != "compose":
            return False
        return verb in argv

    def _install_subprocess_mock(self, monkeypatch, inspect_responses):
        """Install a ``subprocess.run`` patch on the host-agent module.

        ``inspect_responses`` is a list of items consumed in order for each
        ``docker inspect`` call. Each item is either:
          * a tuple ``(state, error)`` -> return rc=0 with ``"<state>|<error>"``
          * the exception class ``subprocess.TimeoutExpired`` (or an instance) ->
            raise it for that call
          * a callable ``(argv) -> CompletedProcess`` for full custom control
        Compose ``pull`` and ``up`` always succeed (rc=0).
        Returns a ``calls`` list (each entry: ``{'argv': [...], 'kwargs': {...}}``).
        """
        calls = []
        responses = list(inspect_responses)

        class _CP:  # minimal stand-in for subprocess.CompletedProcess
            def __init__(self, returncode, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_run(argv, **kwargs):
            calls.append({"argv": list(argv), "kwargs": dict(kwargs)})

            # docker inspect ... -> consume next scripted response
            if (len(argv) >= 2 and argv[0] == "docker" and argv[1] == "inspect"):
                if not responses:
                    return _CP(0, "running|", "")
                resp = responses.pop(0)
                if isinstance(resp, type) and issubclass(resp, BaseException):
                    raise resp(cmd=argv, timeout=5)
                if isinstance(resp, BaseException):
                    raise resp
                if callable(resp):
                    return resp(argv)
                state, err = resp
                return _CP(0, f"{state}|{err}", "")

            # docker compose ... -> always success.
            if (len(argv) >= 2 and argv[0] == "docker" and argv[1] == "compose"):
                return _CP(0, "", "")

            # Anything else: refuse so the test fails loudly rather than
            # silently shelling out.
            raise AssertionError(f"unexpected subprocess.run argv: {argv}")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        return calls

    # ------------------------------------------------------------------
    # Test cases
    # ------------------------------------------------------------------

    def test_install_writes_error_progress_when_state_never_running(
        self, tmp_path, monkeypatch,
    ):
        """Container stuck in ``created`` for the whole startup window must
        surface as ``status=error`` with a ``did not reach running state``
        message — not as a false ``started``."""
        sid = "fakesvc"
        install_dir, progress_file, _ = self._setup_agent(
            tmp_path, monkeypatch, sid=sid, startup_timeout=3,
        )
        # All inspect calls report 'created'; deadline must elapse.
        # 3s timeout / 1s fake sleep -> 3 inspect calls.
        calls = self._install_subprocess_mock(
            monkeypatch,
            inspect_responses=[("created", "")] * 10,
        )

        server, thread, port = self._start_server(monkeypatch)
        try:
            self._post_install(port, "wire-test-secret", sid)
            payload = self._wait_for_terminal(progress_file)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        assert payload["status"] == "error", payload
        assert "did not reach running state" in (payload.get("error") or "")
        # At least one inspect call happened (otherwise the gate isn't running).
        assert any(self._is_inspect_call(c) for c in calls), calls

    def test_install_skips_state_poll_when_startup_check_false(
        self, tmp_path, monkeypatch,
    ):
        """``service.startup_check: false`` must skip the docker-inspect
        poll entirely and report ``started`` as soon as ``compose up``
        returns 0."""
        sid = "fakesvc"
        install_dir, progress_file, _ = self._setup_agent(
            tmp_path, monkeypatch, sid=sid, startup_check=False,
        )
        calls = self._install_subprocess_mock(
            monkeypatch,
            # Empty list: any inspect call would still get a default
            # "running|" response — but the test asserts none happened.
            inspect_responses=[],
        )

        server, thread, port = self._start_server(monkeypatch)
        try:
            self._post_install(port, "wire-test-secret", sid)
            payload = self._wait_for_terminal(progress_file)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        assert payload["status"] == "started", payload
        inspect_calls = [c for c in calls if self._is_inspect_call(c)]
        assert inspect_calls == [], (
            f"docker inspect must NOT be called when startup_check is false; "
            f"saw: {inspect_calls}"
        )
        # Sanity: compose up did happen.
        assert any(self._is_compose_call(c, "up") for c in calls), calls

    def test_install_records_started_on_state_transition(
        self, tmp_path, monkeypatch,
    ):
        """First inspect returns ``starting``, second returns ``running`` —
        the loop must break on the transition and report ``started``."""
        sid = "fakesvc"
        install_dir, progress_file, _ = self._setup_agent(
            tmp_path, monkeypatch, sid=sid, startup_timeout=10,
        )
        calls = self._install_subprocess_mock(
            monkeypatch,
            inspect_responses=[("starting", ""), ("running", "")],
        )

        server, thread, port = self._start_server(monkeypatch)
        try:
            self._post_install(port, "wire-test-secret", sid)
            payload = self._wait_for_terminal(progress_file)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        assert payload["status"] == "started", payload
        inspect_calls = [c for c in calls if self._is_inspect_call(c)]
        # Exactly two inspect calls: starting -> running, then break.
        # Allow >=2 to accept any future implementation that double-checks.
        assert len(inspect_calls) >= 2, inspect_calls

    def test_install_tolerates_docker_inspect_timeout(
        self, tmp_path, monkeypatch,
    ):
        """A single ``subprocess.TimeoutExpired`` from docker inspect must
        be absorbed by the poll loop — the install thread must NOT abort
        the whole install on a one-off probe failure."""
        import subprocess as _real_subprocess
        sid = "fakesvc"
        install_dir, progress_file, _ = self._setup_agent(
            tmp_path, monkeypatch, sid=sid, startup_timeout=10,
        )
        # First inspect call raises TimeoutExpired; second returns "running".
        # If the install thread propagates the timeout up to the outer
        # try/except, _write_progress would be called with status="error"
        # and message "timed out (...)". The test asserts "started" instead.
        calls = self._install_subprocess_mock(
            monkeypatch,
            inspect_responses=[
                _real_subprocess.TimeoutExpired,
                ("running", ""),
            ],
        )

        server, thread, port = self._start_server(monkeypatch)
        try:
            self._post_install(port, "wire-test-secret", sid)
            payload = self._wait_for_terminal(progress_file)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        assert payload["status"] == "started", payload
        inspect_calls = [c for c in calls if self._is_inspect_call(c)]
        # At least the timeout + the success call.
        assert len(inspect_calls) >= 2, inspect_calls
# --- Enable-retry edge cases (fork issue #493) ---
#
# Companion coverage to PR #1039's TestEnableRetry. These tests pin the
# three dispatch edge cases that #1039's contract introduces but does not
# directly exercise:
#   a. retry path with no post_install hook → must reach 'started' without
#      writing a 'setup_hook' transition;
#   b. malformed progress JSON when /v1/extension/start arrives → handler
#      must fall back to the synchronous compose path (not retry);
#   c. progress.status == "setup_hook" (mid-install) → also falls back to
#      the sync path; only "error" is the retry trigger.
#
# The retry helper from PR #1039 is now on main. These tests keep the
# dispatch edge cases pinned so future host-agent changes do not regress
# retry-versus-sync routing.


class TestEnableRetryEdgeCases:

    def _setup_env(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "install"
        install_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        builtin_root = tmp_path / "builtin"
        user_root = tmp_path / "user"
        builtin_root.mkdir()
        user_root.mkdir()
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "DATA_DIR", data_dir)
        monkeypatch.setattr(_mod, "EXTENSIONS_DIR", builtin_root)
        monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", user_root)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        # Drop the per-service lock so retries across tests don't deadlock.
        _mod._service_locks.pop("fakesvc", None)
        return data_dir, builtin_root

    def _write_progress_raw(self, data_dir, sid, raw_text):
        d = data_dir / "extension-progress"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{sid}.json").write_text(raw_text, encoding="utf-8")

    def _read_progress(self, data_dir, sid):
        f = data_dir / "extension-progress" / f"{sid}.json"
        if not f.exists():
            return None
        return json.loads(f.read_text(encoding="utf-8"))

    def _body(self, sid):
        return json.dumps({"service_id": sid}).encode("utf-8")

    def test_no_hook_retry_completes_with_started_progress(
        self, tmp_path, monkeypatch,
    ):
        """error progress + manifest without post_install hook → retry skips
        the setup_hook step and lands on 'started'.

        Pins the no-hook branch in _enable_retry_work: when
        _resolve_hook(ext_dir, "post_install") returns None, no
        setup_hook progress write occurs and no subprocess is spawned;
        the worker proceeds straight to docker_compose_action.
        """
        # Run the retry worker thread synchronously so we can assert on
        # final progress state from the test thread.
        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kwargs):
                self._target = target

            def start(self):
                self._target()

        monkeypatch.setattr(_mod.threading, "Thread", _SyncThread)

        data_dir, builtin_root = self._setup_env(tmp_path, monkeypatch)
        ext_dir = builtin_root / "fakesvc"
        ext_dir.mkdir()
        # Manifest with NO post_install hook.
        (ext_dir / "manifest.yaml").write_text(
            "service:\n  port: 1234\n", encoding="utf-8",
        )
        self._write_progress_raw(
            data_dir, "fakesvc",
            json.dumps({"service_id": "fakesvc", "status": "error",
                        "error": "prior failure"}),
        )

        hook_cmds: list = []

        def fake_run(cmd, *a, **k):
            hook_cmds.append(cmd)
            import subprocess as _sp
            # Startup_check (PR #1039) polls `docker inspect --format
            # '{{.State.Status}}|{{.State.Error}}' ods-<svc>` after the
            # compose action and reads stdout. Empty output keeps the poll
            # in 'not running' forever and the retry path eventually writes
            # progress='error'. Return a clean 'running|' for those calls
            # so the poll satisfies and the worker reaches 'started';
            # every other subprocess (which there shouldn't be in the
            # no-hook branch) still gets an empty response.
            is_docker_inspect = (
                isinstance(cmd, list) and len(cmd) >= 2
                and cmd[0] == "docker" and cmd[1] == "inspect"
            )
            stdout = "running|" if is_docker_inspect else ""
            return _sp.CompletedProcess(args=cmd, returncode=0,
                                        stdout=stdout, stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(
            _mod, "docker_compose_action",
            lambda sid, action: (True, ""),
        )

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        # Retry path engaged → 202 (accept-then-thread).
        assert handler.response_code == 202
        # No hook-script invocation: there is no hook to run. (The startup_check
        # path from PR #1039 still calls subprocess.run for `docker inspect`
        # to poll container state — filter those out and only assert no hook ran.)
        hook_invocations = [
            c for c in hook_cmds
            if not (isinstance(c, list) and len(c) >= 2 and c[0] == "docker" and c[1] == "inspect")
        ]
        assert hook_invocations == []
        # Worker landed on 'started' (not 'setup_hook' or 'error').
        progress = self._read_progress(data_dir, "fakesvc")
        assert progress is not None
        assert progress["status"] == "started"

    def test_corrupted_progress_json_falls_back_to_sync_path(
        self, tmp_path, monkeypatch,
    ):
        """Malformed JSON in progress file → _read_progress_status returns
        None → retry trigger is NOT engaged; the synchronous compose
        path runs and the corrupted file is left untouched.
        """
        data_dir, builtin_root = self._setup_env(tmp_path, monkeypatch)
        ext_dir = builtin_root / "fakesvc"
        ext_dir.mkdir()
        (ext_dir / "manifest.yaml").write_text(
            "service:\n  port: 1234\n", encoding="utf-8",
        )
        self._write_progress_raw(data_dir, "fakesvc", "{not-json")

        # Pre-condition: helper added by PR #1039 must report None for
        # malformed JSON so the dispatch in _handle_extension cannot
        # treat the corrupt state as "error" and trigger a retry.
        assert _mod._read_progress_status("fakesvc") is None

        compose_calls: list = []

        def fake_compose(sid, act):
            compose_calls.append((sid, act))
            return True, ""

        monkeypatch.setattr(_mod, "docker_compose_action", fake_compose)

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        # Synchronous success → 200, not 202.
        assert handler.response_code == 200
        assert compose_calls == [("fakesvc", "start")]
        # Corrupted progress file left exactly as it was — the retry
        # path would have rewritten it.
        raw = (data_dir / "extension-progress" / "fakesvc.json").read_text(
            encoding="utf-8",
        )
        assert raw == "{not-json"

    def test_mid_install_setup_hook_status_uses_sync_path(
        self, tmp_path, monkeypatch,
    ):
        """status='setup_hook' is a mid-install state, not a terminal
        error. The retry trigger only fires for status='error', so a
        'start' against a service mid-install must use the sync path
        and leave progress untouched.
        """
        data_dir, builtin_root = self._setup_env(tmp_path, monkeypatch)
        ext_dir = builtin_root / "fakesvc"
        ext_dir.mkdir()
        (ext_dir / "manifest.yaml").write_text(
            "service:\n  port: 1234\n", encoding="utf-8",
        )
        self._write_progress_raw(
            data_dir, "fakesvc",
            json.dumps({"service_id": "fakesvc", "status": "setup_hook"}),
        )

        # Pre-condition: the PR #1039 helper reports the in-flight status
        # exactly so the dispatch can compare against the literal "error".
        assert _mod._read_progress_status("fakesvc") == "setup_hook"

        compose_calls: list = []

        def fake_compose(sid, act):
            compose_calls.append((sid, act))
            return True, ""

        monkeypatch.setattr(_mod, "docker_compose_action", fake_compose)

        handler = _FakeHandler(self._body("fakesvc"))
        _mod.AgentHandler._handle_extension(handler, "start")

        # Sync path used → 200, not 202.
        assert handler.response_code == 200
        assert compose_calls == [("fakesvc", "start")]
        # Sync path must not rewrite the in-flight progress.
        progress = self._read_progress(data_dir, "fakesvc")
        assert progress is not None
        assert progress["status"] == "setup_hook"


# --- Model download catalog unavailability (fork issue #512) ---
#
# Tests _handle_model_download's response shape when the model-library.json
# catalog is missing or corrupt. Pre-PR #1057 the handler conflates these
# real install-corruption cases with the policy denial "Model not in
# library catalog", returning 403 in all three. PR #1057 distinguishes
# unreadable/missing (500) from genuinely-not-listed (403).
#
# Cases (a) and (b) cover the post-#1057 corruption/error distinction.
# Case (c) is the existing-behaviour-preserved baseline for a clean catalog
# that simply does not list the requested model.


class TestModelDownloadCatalogUnavailable:

    def _setup_env(self, tmp_path, monkeypatch):
        install_dir = tmp_path / "install"
        (install_dir / "config").mkdir(parents=True)
        (install_dir / "data" / "models").mkdir(parents=True)
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        return install_dir

    def _body(self):
        return json.dumps({
            "gguf_file": "test-model.gguf",
            "gguf_url": "https://example.com/test-model.gguf",
        }).encode("utf-8")

    def test_missing_catalog_returns_500(self, tmp_path, monkeypatch):
        """No model-library.json → 500 'Model catalog unavailable'.

        Pre-#1057 returns 403 (the catalog check sees library_path
        missing, leaves allowed=False, falls through to the 'not in
        library catalog' branch).
        """
        install_dir = self._setup_env(tmp_path, monkeypatch)
        assert not (install_dir / "config" / "model-library.json").exists()

        handler = _FakeHandler(self._body())
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 500
        body = handler.parse_response()
        assert body["error"] == "Model catalog unavailable"

    def test_corrupt_catalog_returns_500(self, tmp_path, monkeypatch):
        """Catalog file exists but is malformed JSON → 500.

        Pre-#1057 the JSONDecodeError is swallowed by a bare ``pass``,
        leaving allowed=False and returning 403 — masking the corrupt
        install as a policy denial.
        """
        install_dir = self._setup_env(tmp_path, monkeypatch)
        (install_dir / "config" / "model-library.json").write_text(
            "{not-json", encoding="utf-8",
        )

        handler = _FakeHandler(self._body())
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 500
        body = handler.parse_response()
        assert body["error"] == "Model catalog unavailable"

    def test_model_not_in_clean_catalog_still_returns_403(
        self, tmp_path, monkeypatch,
    ):
        """Catalog parses cleanly and lists other models but not the one
        being requested → 403 'Model not in library catalog' (the
        existing policy-denial behaviour, preserved across #1057).
        """
        install_dir = self._setup_env(tmp_path, monkeypatch)
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": [{
                "gguf_file": "different-model.gguf",
                "gguf_url": "https://example.com/different.gguf",
            }]}),
            encoding="utf-8",
        )

        handler = _FakeHandler(self._body())
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 403
        body = handler.parse_response()
        assert body["error"] == "Model not in library catalog"


class TestModelDeleteSafety:

    def _setup(self, tmp_path, monkeypatch, *, active="other.gguf"):
        install_dir = tmp_path / "install"
        models_dir = install_dir / "data" / "models"
        (install_dir / "config").mkdir(parents=True)
        models_dir.mkdir(parents=True)
        (install_dir / ".env").write_text(
            f"GPU_BACKEND=nvidia\nGGUF_FILE={active}\nOLLAMA_PORT=8080\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        return install_dir, models_dir

    def test_split_delete_clears_status_naming_deleted_part(self, tmp_path, monkeypatch):
        install_dir, models_dir = self._setup(tmp_path, monkeypatch)
        parts = ["split-00001-of-00002.gguf", "split-00002-of-00002.gguf"]
        for part in parts:
            (models_dir / part).write_bytes(b"model part")
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": [{
                "gguf_file": parts[0],
                "gguf_parts": [
                    {"file": part, "url": f"https://example.test/{part}"}
                    for part in parts
                ],
            }]}),
            encoding="utf-8",
        )
        status_path = install_dir / "data" / "model-download-status.json"
        status_path.write_text(
            json.dumps({
                "status": "complete",
                "model": f"{parts[1]} (part 2/2)",
                "bytesDownloaded": 10,
                "bytesTotal": 10,
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "_live_runtime_has_model", lambda _env, _model: False)
        handler = _FakeHandler(json.dumps({"gguf_file": parts[0]}).encode("utf-8"))

        _mod.AgentHandler._handle_model_delete(handler)

        assert handler.response_code == 200
        assert all(not (models_dir / part).exists() for part in parts)
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["status"] == "idle"
        assert status["model"] == ""
        assert not any(part in json.dumps(status) for part in parts)

    def test_delete_refuses_persisted_active_model_without_touching_status(
        self, tmp_path, monkeypatch,
    ):
        install_dir, models_dir = self._setup(
            tmp_path,
            monkeypatch,
            active="active.gguf",
        )
        target = models_dir / "active.gguf"
        target.write_bytes(b"active")
        status_path = install_dir / "data" / "model-download-status.json"
        original_status = json.dumps({"status": "complete", "model": "active.gguf"})
        status_path.write_text(original_status, encoding="utf-8")
        monkeypatch.setattr(
            _mod,
            "_live_runtime_has_model",
            lambda *_args: pytest.fail("persisted active identity should refuse first"),
        )
        handler = _FakeHandler(json.dumps({"gguf_file": "active.gguf"}).encode("utf-8"))

        _mod.AgentHandler._handle_model_delete(handler)

        assert handler.response_code == 409
        assert target.exists()
        assert status_path.read_text(encoding="utf-8") == original_status

    def test_delete_refuses_model_reported_active_only_by_live_runtime(
        self, tmp_path, monkeypatch,
    ):
        _install_dir, models_dir = self._setup(tmp_path, monkeypatch)
        target = models_dir / "live-active.gguf"
        target.write_bytes(b"active")
        monkeypatch.setattr(_mod, "_live_runtime_has_model", lambda _env, _model: True)
        handler = _FakeHandler(
            json.dumps({"gguf_file": "live-active.gguf"}).encode("utf-8")
        )

        _mod.AgentHandler._handle_model_delete(handler)

        assert handler.response_code == 409
        assert "live runtime" in handler.parse_response()["error"]
        assert target.exists()


class TestModelDownloadFileIntegrity:

    class _NoCancel:
        def clear(self):
            pass

        def is_set(self):
            return False

        def wait(self, timeout=None):
            return False

    def _setup_env(
        self,
        tmp_path,
        monkeypatch,
        library_models=None,
        expected_payload=b"valid gguf bytes",
    ):
        install_dir = tmp_path / "install"
        (install_dir / "config").mkdir(parents=True)
        (install_dir / "data" / "models").mkdir(parents=True)
        models = library_models
        if models is None:
            models = [{
                "gguf_file": "test-model.gguf",
                "gguf_url": "https://example.com/test-model.gguf",
                "gguf_sha256": hashlib.sha256(expected_payload).hexdigest(),
            }]
        (install_dir / "config" / "model-library.json").write_text(
            json.dumps({"models": models}),
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
        monkeypatch.setattr(_mod, "_model_download_thread", None)
        monkeypatch.setattr(_mod, "_model_download_proc", None)
        monkeypatch.setattr(_mod, "_model_download_cancelable", False)
        monkeypatch.setattr(_mod, "_model_download_cancel", self._NoCancel())
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            lambda cmd, *a, **kw: subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="content-length: 123456\n",
                stderr="",
            ),
        )
        return install_dir

    def _body(self):
        return json.dumps({
            "gguf_file": "test-model.gguf",
            "gguf_url": "https://example.com/test-model.gguf",
        }).encode("utf-8")

    def _patch_curl_download(self, monkeypatch, payload: bytes):
        outputs = []

        class FakeProc:
            def __init__(self, cmd, *args, **kwargs):
                self.cmd = cmd
                self.returncode = None
                self.output = Path(cmd[cmd.index("-o") + 1])
                outputs.append(self.output.name)

            def wait(self, timeout=None):
                self.output.parent.mkdir(parents=True, exist_ok=True)
                self.output.write_bytes(payload)
                self.returncode = 0
                return 0

            def communicate(self, timeout=None):
                self.wait(timeout=timeout)
                return "", ""

            def kill(self):
                self.returncode = -9

        monkeypatch.setattr(_mod.subprocess, "Popen", FakeProc)
        return outputs

    def test_empty_existing_model_is_redownloaded(self, tmp_path, monkeypatch):
        install_dir = self._setup_env(tmp_path, monkeypatch)
        self._patch_curl_download(monkeypatch, b"valid gguf bytes")
        model_path = install_dir / "data" / "models" / "test-model.gguf"
        model_path.write_bytes(b"")

        handler = _FakeHandler(self._body())
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "started"
        _mod._model_download_thread.join(timeout=2)
        assert not _mod._model_download_thread.is_alive()
        assert model_path.read_bytes() == b"valid gguf bytes"
        status = json.loads((install_dir / "data" / "model-download-status.json").read_text(encoding="utf-8"))
        assert status["status"] == "complete"

    def test_existing_model_clears_stale_downloading_status(self, tmp_path, monkeypatch):
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            expected_payload=b"already here",
        )
        model_path = install_dir / "data" / "models" / "test-model.gguf"
        model_path.write_bytes(b"already here")
        status_path = install_dir / "data" / "model-download-status.json"
        status_path.write_text(
            json.dumps({"status": "downloading", "model": "test-model.gguf"}),
            encoding="utf-8",
        )

        handler = _FakeHandler(self._body())
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "already_downloaded"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["status"] == "complete"
        assert status["model"] == "test-model.gguf"

    def test_status_marks_dead_active_download_failed(self, tmp_path, monkeypatch):
        install_dir = self._setup_env(tmp_path, monkeypatch)
        status_path = install_dir / "data" / "model-download-status.json"
        status_path.write_text(
            json.dumps({
                "status": "downloading",
                "model": "test-model.gguf",
                "bytesDownloaded": 0,
                "bytesTotal": 123456,
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "_model_download_thread", None)

        handler = _FakeHandler(b"")
        _mod.AgentHandler._handle_model_status(handler)
        _mod._model_status_verify_thread.join(timeout=2)
        handler = _FakeHandler(b"")
        _mod.AgentHandler._handle_model_status(handler)

        assert handler.response_code == 200
        body = handler.parse_response()
        assert body["status"] == "failed"
        assert body["model"] == "test-model.gguf"
        assert "not running" in body["error"]
        persisted = json.loads(status_path.read_text(encoding="utf-8"))
        assert persisted["status"] == "failed"

    def test_model_status_schedules_stale_bootstrap_route_proof_without_inline_warmup(
        self, tmp_path, monkeypatch,
    ):
        bootstrap_model = {
            "id": "qwen3.5-2b-q4",
            "gguf_file": "Qwen3.5-2B-Q4_K_M.gguf",
            "llm_model_name": "qwen3.5-2b-q4",
            "ctx_size": 65536,
        }
        full_model = {
            "id": "qwen3-coder-next",
            "gguf_file": "qwen3-coder-next-Q4_K_M.gguf",
            "llm_model_name": "qwen3-coder-next",
            "ctx_size": 131072,
        }
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            library_models=[bootstrap_model, full_model],
        )
        env_path = install_dir / ".env"
        env_path.write_text(
            "ODS_MODE=local\n"
            "GPU_BACKEND=nvidia\n"
            "GGUF_FILE=qwen3-coder-next-Q4_K_M.gguf\n"
            "LLM_MODEL=qwen3-coder-next\n"
            "CTX_SIZE=131072\n",
            encoding="utf-8",
        )
        state_path = install_dir / "data" / "model-state.json"
        _mod._switchboard_state.record_verified_route(
            state_path,
            catalog_id="qwen3.5-2b-q4",
            runtime_model_id="Qwen3.5-2B-Q4_K_M.gguf",
            backend_kind="llama-server",
            endpoint_id="llama-server-default",
            context_length=65536,
            capabilities={
                "chat": True,
                "tools": False,
                "vision": False,
                "agentViable": True,
            },
            proof_identity="Qwen3.5-2B-Q4_K_M.gguf",
        )
        (install_dir / "data" / "bootstrap-status.json").write_text(
            json.dumps({
                "status": "complete",
                "model": "qwen3-coder-next-Q4_K_M.gguf",
            }),
            encoding="utf-8",
        )

        readiness_calls = []

        def readiness(*_args, **kwargs):
            readiness_calls.append(kwargs)
            return {
                "identity": "qwen3-coder-next-Q4_K_M.gguf",
                "contextLength": 131072,
                "contextVerified": True,
                "verifiedAt": "2026-07-21T00:00:00Z",
            }

        scheduled = []
        monkeypatch.setattr(_mod, "_wait_for_model_readiness", readiness)
        monkeypatch.setattr(
            _mod,
            "_schedule_initial_switchboard_verification",
            lambda reason: scheduled.append(reason),
        )

        handler = _FakeHandler(b"")
        _mod.AgentHandler._handle_model_status(handler)

        assert handler.response_code == 200
        assert handler.parse_response() == {"status": "idle"}
        assert readiness_calls == []
        assert scheduled == ["model-status"]
        doc = json.loads(state_path.read_text(encoding="utf-8"))
        assert doc["active"]["catalogId"] == "qwen3.5-2b-q4"
        assert doc["active"]["runtimeModelId"] == "Qwen3.5-2B-Q4_K_M.gguf"
        assert doc["active"]["contextLength"] == 65536
        assert doc["active"]["proof"] == {
            "identity": "Qwen3.5-2B-Q4_K_M.gguf",
            "completion": True,
        }
        assert doc["history"] == []

    def test_model_status_schedules_route_proof_while_bootstrap_swap_is_verifying(
        self, tmp_path, monkeypatch,
    ):
        install_dir = self._setup_env(tmp_path, monkeypatch)
        (install_dir / "data" / "bootstrap-status.json").write_text(
            json.dumps({
                "status": "swapping",
                "model": "test-model.gguf",
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            _mod,
            "_switchboard_state_needs_current_env_verification",
            lambda _path: True,
        )
        scheduled = []
        monkeypatch.setattr(
            _mod,
            "_schedule_initial_switchboard_verification",
            lambda reason: scheduled.append(reason),
        )

        handler = _FakeHandler(b"")
        _mod.AgentHandler._handle_model_status(handler)

        assert handler.response_code == 200
        assert handler.parse_response() == {"status": "idle"}
        assert scheduled == ["model-status"]

    def test_empty_finished_download_is_failed_not_complete(self, tmp_path, monkeypatch):
        install_dir = self._setup_env(tmp_path, monkeypatch)
        self._patch_curl_download(monkeypatch, b"")

        handler = _FakeHandler(self._body())
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "started"
        _mod._model_download_thread.join(timeout=2)
        assert not _mod._model_download_thread.is_alive()
        status = json.loads((install_dir / "data" / "model-download-status.json").read_text(encoding="utf-8"))
        assert status["status"] == "failed"
        assert "missing or empty" in status["error"]

    def test_curl_failure_status_includes_stderr(self, tmp_path, monkeypatch):
        install_dir = self._setup_env(tmp_path, monkeypatch)

        class FailingProc:
            def __init__(self, cmd, *args, **kwargs):
                self.returncode = None

            def communicate(self, timeout=None):
                self.returncode = 22
                return "", "curl: (22) The requested URL returned error: 403"

            def wait(self, timeout=None):
                self.returncode = 22
                return 22

            def kill(self):
                self.returncode = -9

        monkeypatch.setattr(_mod.subprocess, "Popen", FailingProc)

        handler = _FakeHandler(self._body())
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "started"
        _mod._model_download_thread.join(timeout=2)
        assert not _mod._model_download_thread.is_alive()
        status = json.loads((install_dir / "data" / "model-download-status.json").read_text(encoding="utf-8"))
        assert status["status"] == "failed"
        assert "curl exited with code 22" in status["error"]
        assert "requested URL returned error: 403" in status["error"]

    def test_huggingface_resolve_download_falls_back_to_hub_client(self, tmp_path, monkeypatch):
        payload = b"downloaded through hf hub"
        model = {
            "gguf_file": "hf-model.gguf",
            "gguf_url": "https://huggingface.co/org/model-GGUF/resolve/main/subdir/hf-model.gguf",
            "gguf_sha256": hashlib.sha256(payload).hexdigest(),
        }
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            library_models=[model],
            expected_payload=payload,
        )
        calls = []

        class FallbackProc:
            def __init__(self, cmd, *args, **kwargs):
                self.cmd = cmd
                self.returncode = None
                calls.append(cmd)

            def communicate(self, timeout=None):
                if self.cmd and self.cmd[0] == "curl":
                    self.returncode = 22
                    return "", "curl: (22) The requested URL returned error: 403"
                dest = Path(self.cmd[6])
                dest.write_bytes(payload)
                self.returncode = 0
                return "", ""

            def wait(self, timeout=None):
                return self.returncode if self.returncode is not None else 0

            def kill(self):
                self.returncode = -9

        monkeypatch.setattr(_mod.subprocess, "Popen", FallbackProc)

        handler = _FakeHandler(json.dumps({
            "gguf_file": model["gguf_file"],
            "gguf_url": model["gguf_url"],
        }).encode("utf-8"))
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "started"
        _mod._model_download_thread.join(timeout=2)
        assert not _mod._model_download_thread.is_alive()
        assert (install_dir / "data" / "models" / "hf-model.gguf").read_bytes() == payload
        assert any(cmd and cmd[0] == "curl" for cmd in calls)
        assert any(cmd and cmd[0] == sys.executable for cmd in calls)
        status = json.loads((install_dir / "data" / "model-download-status.json").read_text(encoding="utf-8"))
        assert status["status"] == "complete"

    def test_huggingface_fallback_reports_active_status(self, tmp_path, monkeypatch):
        payload = b"downloaded through hf hub"
        model = {
            "gguf_file": "hf-model.gguf",
            "gguf_url": "https://huggingface.co/org/model-GGUF/resolve/main/subdir/hf-model.gguf",
            "gguf_sha256": hashlib.sha256(payload).hexdigest(),
        }
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            library_models=[model],
            expected_payload=payload,
        )
        monkeypatch.delenv("HF_HUB_ETAG_TIMEOUT", raising=False)
        monkeypatch.delenv("HF_HUB_DOWNLOAD_TIMEOUT", raising=False)
        child_envs = []
        status_path = install_dir / "data" / "model-download-status.json"
        part_tmp = install_dir / "data" / "models" / "hf-model.gguf.part"

        class HubProc:
            def __init__(self, cmd, *args, **kwargs):
                self.cmd = cmd
                self.returncode = None
                child_envs.append(kwargs.get("env", {}))

            def communicate(self, timeout=None):
                deadline = time.time() + 2
                while time.time() < deadline and not status_path.exists():
                    time.sleep(0.01)
                dest = Path(self.cmd[6])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(payload)
                self.returncode = 0
                return "", ""

            def wait(self, timeout=None):
                return self.returncode if self.returncode is not None else 0

            def kill(self):
                self.returncode = -9

        monkeypatch.setattr(_mod.subprocess, "Popen", HubProc)

        ok, error = _mod._download_huggingface_artifact(
            model["gguf_url"],
            part_tmp,
            self._NoCancel(),
            status_path=status_path,
            status_label=model["gguf_file"],
            part_total=len(payload),
            status_error="Retry 1/3: curl exited with code 35",
        )

        assert ok is True
        assert error == ""
        assert child_envs[0]["HF_HUB_ETAG_TIMEOUT"] == "30"
        assert child_envs[0]["HF_HUB_DOWNLOAD_TIMEOUT"] == "30"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["status"] == "downloading"
        assert status["model"] == "hf-model.gguf"
        assert status["bytesTotal"] == len(payload)
        assert "curl exited with code 35" in status["error"]
        assert "Hugging Face Hub fallback active" in status["error"]

    def test_huggingface_fallback_timeout_is_bounded(self, tmp_path, monkeypatch):
        model = {
            "gguf_file": "hf-model.gguf",
            "gguf_url": "https://huggingface.co/org/model-GGUF/resolve/main/subdir/hf-model.gguf",
            "gguf_sha256": hashlib.sha256(b"payload").hexdigest(),
        }
        install_dir = self._setup_env(tmp_path, monkeypatch, library_models=[model])
        monkeypatch.setenv("ODS_HF_HUB_FALLBACK_TIMEOUT_SECONDS", "1")
        part_tmp = install_dir / "data" / "models" / "hf-model.gguf.part"
        timeouts = []

        class HangingHubProc:
            def __init__(self, cmd, *args, **kwargs):
                self.cmd = cmd
                self.returncode = None
                self.killed = False

            def communicate(self, timeout=None):
                timeouts.append(timeout)
                if self.killed:
                    self.returncode = -9
                    return "", ""
                raise subprocess.TimeoutExpired(self.cmd, timeout)

            def wait(self, timeout=None):
                return self.returncode if self.returncode is not None else -9

            def kill(self):
                self.killed = True
                self.returncode = -9

        monkeypatch.setattr(_mod.subprocess, "Popen", HangingHubProc)

        ok, error = _mod._download_huggingface_artifact(
            model["gguf_url"],
            part_tmp,
            self._NoCancel(),
        )

        assert ok is False
        assert timeouts[0] == 30
        assert "Hugging Face Hub fallback timed out after 30s" in error

    def test_split_download_skips_existing_non_empty_parts(self, tmp_path, monkeypatch):
        parts = [
            {
                "file": "split-model-00001-of-00002.gguf",
                "url": "https://example.com/split-model-00001-of-00002.gguf",
                "sha256": hashlib.sha256(b"existing first part").hexdigest(),
            },
            {
                "file": "split-model-00002-of-00002.gguf",
                "url": "https://example.com/split-model-00002-of-00002.gguf",
                "sha256": hashlib.sha256(b"downloaded second part").hexdigest(),
            },
        ]
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            library_models=[{
                "gguf_file": "split-model-00001-of-00002.gguf",
                "gguf_url": "",
                "gguf_sha256": "",
                "gguf_parts": parts,
            }],
        )
        calls = self._patch_curl_download(monkeypatch, b"downloaded second part")
        models_dir = install_dir / "data" / "models"
        (models_dir / "split-model-00001-of-00002.gguf").write_bytes(b"existing first part")

        handler = _FakeHandler(json.dumps({
            "gguf_file": "split-model-00001-of-00002.gguf",
            "gguf_parts": parts,
        }).encode("utf-8"))
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "started"
        _mod._model_download_thread.join(timeout=2)
        assert not _mod._model_download_thread.is_alive()
        assert calls == ["split-model-00002-of-00002.gguf.part"]
        assert (models_dir / "split-model-00001-of-00002.gguf").read_bytes() == b"existing first part"
        assert (models_dir / "split-model-00002-of-00002.gguf").read_bytes() == b"downloaded second part"
        status = json.loads((install_dir / "data" / "model-download-status.json").read_text(encoding="utf-8"))
        assert status["status"] == "complete"

    def test_exact_catalog_size_is_enforced_without_checksum(self, tmp_path):
        model_path = tmp_path / "sized-model.gguf"
        model_path.write_bytes(b"five!")

        assert _mod._verify_model_artifact(
            model_path,
            {"size_bytes": 5, "sha256": ""},
        ) == (True, "")
        valid, reason = _mod._verify_model_artifact(
            model_path,
            {"size_bytes": 4, "sha256": ""},
        )
        assert valid is False
        assert "size mismatch" in reason

    def test_stale_split_status_rejects_missing_second_part(self, tmp_path, monkeypatch):
        first_payload = b"verified first part"
        second_payload = b"verified second part"
        parts = [
            {
                "file": "split-00001-of-00002.gguf",
                "url": "https://example.com/split-00001-of-00002.gguf",
                "sha256": hashlib.sha256(first_payload).hexdigest(),
            },
            {
                "file": "split-00002-of-00002.gguf",
                "url": "https://example.com/split-00002-of-00002.gguf",
                "sha256": hashlib.sha256(second_payload).hexdigest(),
            },
        ]
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            library_models=[{
                "gguf_file": parts[0]["file"],
                "gguf_parts": parts,
            }],
        )
        (install_dir / "data" / "models" / parts[0]["file"]).write_bytes(first_payload)
        status_path = install_dir / "data" / "model-download-status.json"
        status_path.write_text(
            json.dumps({
                "status": "verifying",
                "model": f"{parts[0]['file']} (part 1/2)",
                "bytesDownloaded": len(first_payload),
                "bytesTotal": len(first_payload),
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(_mod, "_model_download_thread", None)

        handler = _FakeHandler(b"")
        _mod.AgentHandler._handle_model_status(handler)
        _mod._model_status_verify_thread.join(timeout=2)
        handler = _FakeHandler(b"")
        _mod.AgentHandler._handle_model_status(handler)

        assert handler.response_code == 200
        response = handler.parse_response()
        assert response["status"] == "failed"
        assert parts[1]["file"] in response["error"]
        assert "missing" in response["error"]

    def test_stale_status_verification_is_background_single_flight(
        self, tmp_path, monkeypatch,
    ):
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            expected_payload=b"already downloaded",
        )
        model_path = install_dir / "data" / "models" / "test-model.gguf"
        model_path.write_bytes(b"already downloaded")
        status_path = install_dir / "data" / "model-download-status.json"
        status_path.write_text(
            json.dumps({
                "status": "downloading",
                "model": "test-model.gguf",
                "bytesDownloaded": model_path.stat().st_size,
                "bytesTotal": model_path.stat().st_size,
            }),
            encoding="utf-8",
        )
        entered = threading.Event()
        release = threading.Event()
        verification_calls = []

        def blocking_verify(*_args, **_kwargs):
            verification_calls.append(True)
            entered.set()
            assert release.wait(timeout=2)
            return True, ""

        monkeypatch.setattr(_mod, "_verify_model_manifest", blocking_verify)

        first = _FakeHandler(b"")
        _mod.AgentHandler._handle_model_status(first)
        assert first.response_code == 200
        assert first.parse_response()["status"] == "verifying"
        assert entered.wait(timeout=2)

        second = _FakeHandler(b"")
        _mod.AgentHandler._handle_model_status(second)
        assert second.response_code == 200
        assert second.parse_response()["status"] == "verifying"
        assert len(verification_calls) == 1

        release.set()
        _mod._model_status_verify_thread.join(timeout=2)
        assert not _mod._model_status_verify_thread.is_alive()
        assert json.loads(status_path.read_text(encoding="utf-8"))["status"] == "complete"

    def test_corrupt_existing_single_file_is_replaced_before_reuse(
        self,
        tmp_path,
        monkeypatch,
    ):
        valid_payload = b"catalog verified single file"
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            expected_payload=valid_payload,
        )
        calls = self._patch_curl_download(monkeypatch, valid_payload)
        model_path = install_dir / "data" / "models" / "test-model.gguf"
        model_path.write_bytes(b"corrupt but non-empty")

        handler = _FakeHandler(self._body())
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "started"
        _mod._model_download_thread.join(timeout=2)
        assert not _mod._model_download_thread.is_alive()
        assert calls == ["test-model.gguf.part"]
        assert model_path.read_bytes() == valid_payload
        status = json.loads(
            (install_dir / "data" / "model-download-status.json").read_text(encoding="utf-8")
        )
        assert status["status"] == "complete"

    def test_corrupt_existing_split_part_is_replaced_without_redownloading_valid_part(
        self,
        tmp_path,
        monkeypatch,
    ):
        first_payload = b"preexisting verified part"
        second_payload = b"replacement verified part"
        parts = [
            {
                "file": "split-00001-of-00002.gguf",
                "url": "https://example.com/split-00001-of-00002.gguf",
                "sha256": hashlib.sha256(first_payload).hexdigest(),
            },
            {
                "file": "split-00002-of-00002.gguf",
                "url": "https://example.com/split-00002-of-00002.gguf",
                "sha256": hashlib.sha256(second_payload).hexdigest(),
            },
        ]
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            library_models=[{
                "gguf_file": parts[0]["file"],
                "gguf_parts": parts,
            }],
        )
        calls = self._patch_curl_download(monkeypatch, second_payload)
        models_dir = install_dir / "data" / "models"
        (models_dir / parts[0]["file"]).write_bytes(first_payload)
        (models_dir / parts[1]["file"]).write_bytes(b"corrupt but non-empty")

        handler = _FakeHandler(json.dumps({
            "gguf_file": parts[0]["file"],
            "gguf_parts": parts,
        }).encode("utf-8"))
        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "started"
        _mod._model_download_thread.join(timeout=2)
        assert not _mod._model_download_thread.is_alive()
        assert calls == [f"{parts[1]['file']}.part"]
        assert (models_dir / parts[0]["file"]).read_bytes() == first_payload
        assert (models_dir / parts[1]["file"]).read_bytes() == second_payload

    def test_cancelled_split_download_removes_job_files_but_preserves_verified_parts(
        self,
        tmp_path,
        monkeypatch,
    ):
        payloads = [b"preexisting valid", b"created by this job", b"expected final"]
        parts = [
            {
                "file": f"split-0000{index + 1}-of-00003.gguf",
                "url": f"https://example.com/split-{index + 1}.gguf",
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for index, payload in enumerate(payloads)
        ]
        install_dir = self._setup_env(
            tmp_path,
            monkeypatch,
            library_models=[{
                "gguf_file": parts[0]["file"],
                "gguf_parts": parts,
            }],
        )
        cancel_event = _mod.threading.Event()
        monkeypatch.setattr(_mod, "_model_download_cancel", cancel_event)
        models_dir = install_dir / "data" / "models"
        preexisting = models_dir / parts[0]["file"]
        preexisting.write_bytes(payloads[0])
        popen_outputs = []

        class CancellingProc:
            def __init__(self, cmd, *args, **kwargs):
                self.output = Path(cmd[cmd.index("-o") + 1])
                self.returncode = None
                popen_outputs.append(self.output)

            def wait(self, timeout=None):
                if len(popen_outputs) == 1:
                    self.output.write_bytes(payloads[1])
                    self.returncode = 0
                else:
                    self.output.write_bytes(b"partial third part")
                    cancel_event.set()
                    self.returncode = -9
                return self.returncode

            def communicate(self, timeout=None):
                self.wait(timeout=timeout)
                return "", ""

            def kill(self):
                self.returncode = -9

        monkeypatch.setattr(_mod.subprocess, "Popen", CancellingProc)
        handler = _FakeHandler(json.dumps({
            "gguf_file": parts[0]["file"],
            "gguf_parts": parts,
        }).encode("utf-8"))

        _mod.AgentHandler._handle_model_download(handler)

        assert handler.response_code == 200
        assert handler.parse_response()["status"] == "started"
        _mod._model_download_thread.join(timeout=2)
        assert not _mod._model_download_thread.is_alive()
        assert preexisting.read_bytes() == payloads[0]
        assert not (models_dir / parts[1]["file"]).exists()
        assert not (models_dir / parts[2]["file"]).exists()
        assert not (models_dir / f"{parts[1]['file']}.part").exists()
        assert not (models_dir / f"{parts[2]['file']}.part").exists()
        status = json.loads(
            (install_dir / "data" / "model-download-status.json").read_text(encoding="utf-8")
        )
        assert status["status"] == "cancelled"

def test_read_json_body_rejects_non_object():
    """A syntactically-valid but non-object JSON body ([]) must be rejected with
    400, not returned as a list — every caller immediately does body.get(...),
    which would raise AttributeError and drop the connection."""
    handler = _FakeHandler(b"[]")
    assert _mod.read_json_body(handler) is None
    assert handler.response_code == 400


def test_read_json_body_accepts_object():
    handler = _FakeHandler(b'{"a": 1}')
    assert _mod.read_json_body(handler) == {"a": 1}


class TestWindowsObservability:

    def test_adapter_selection_prefers_configured_discrete_amd(self, monkeypatch):
        monkeypatch.setattr(_mod, "GPU_BACKEND", "amd")
        monkeypatch.setattr(_mod, "GPU_COUNT", "1")
        adapters = [
            {"name": "AMD Radeon Graphics", "vendor_id": 0x1002, "memory_total_mb": 512, "software": False},
            {"name": "Microsoft Basic Render Driver", "vendor_id": 0x1414, "memory_total_mb": 0, "software": True},
            {"name": "AMD Radeon RX 9070 XT", "vendor_id": 0x1002, "memory_total_mb": 16368, "software": False},
        ]

        selected = _mod._select_windows_gpu_adapters(adapters, "Radeon RX 9070 XT")

        assert [item["name"] for item in selected] == ["AMD Radeon RX 9070 XT"]

    def test_adapter_selection_supports_identical_multi_gpu(self, monkeypatch):
        monkeypatch.setattr(_mod, "GPU_BACKEND", "nvidia")
        monkeypatch.setattr(_mod, "GPU_COUNT", "2")
        adapters = [
            {"name": "NVIDIA RTX PRO 6000", "vendor_id": 0x10DE, "memory_total_mb": 97887, "software": False},
            {"name": "NVIDIA RTX PRO 6000", "vendor_id": 0x10DE, "memory_total_mb": 97887, "software": False},
        ]

        selected = _mod._select_windows_gpu_adapters(adapters, "RTX PRO 6000 \u00d7 2")

        assert len(selected) == 2

    def test_adapter_selection_infers_all_discrete_amd_without_persisted_count(
        self, monkeypatch,
    ):
        monkeypatch.setattr(_mod, "GPU_BACKEND", "amd")
        monkeypatch.setattr(_mod, "GPU_COUNT", "1")
        adapters = [
            {"name": "AMD Radeon RX 7900 XTX", "vendor_id": 0x1002, "memory_total_mb": 24560, "software": False},
            {"name": "AMD Radeon RX 7900 XTX", "vendor_id": 0x1002, "memory_total_mb": 24560, "software": False},
        ]

        assert len(_mod._select_windows_gpu_adapters(adapters)) == 2

    def test_adapter_selection_does_not_collapse_configured_dual_amd_without_count(
        self, monkeypatch,
    ):
        monkeypatch.setattr(_mod, "GPU_BACKEND", "amd")
        monkeypatch.setattr(_mod, "GPU_COUNT", "1")
        adapters = [
            {"name": "AMD Radeon RX 7900 XTX", "vendor_id": 0x1002, "memory_total_mb": 24560, "software": False},
            {"name": "AMD Radeon RX 7800 XT", "vendor_id": 0x1002, "memory_total_mb": 16368, "software": False},
        ]

        selected = _mod._select_windows_gpu_adapters(adapters, "AMD Radeon RX 7900 XTX")

        assert [item["name"] for item in selected] == [
            "AMD Radeon RX 7900 XTX", "AMD Radeon RX 7800 XT",
        ]

    def test_adapter_selection_excludes_integrated_amd_when_discrete_exists(
        self, monkeypatch,
    ):
        monkeypatch.setattr(_mod, "GPU_BACKEND", "amd")
        adapters = [
            {"name": "AMD Radeon Graphics", "vendor_id": 0x1002, "memory_total_mb": 512, "software": False},
            {"name": "AMD Radeon RX 9070 XT", "vendor_id": 0x1002, "memory_total_mb": 16368, "software": False},
        ]

        selected = _mod._select_windows_gpu_adapters(adapters)

        assert [item["name"] for item in selected] == ["AMD Radeon RX 9070 XT"]

    def test_windows_gpu_metrics_are_bounded_and_cached(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod, "GPU_BACKEND", "amd")
        monkeypatch.setattr(_mod, "GPU_COUNT", "1")
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        (tmp_path / ".env").write_text("HOST_GPU_NAME=AMD Radeon RX 9070 XT\n")
        monkeypatch.setattr(_mod, "_windows_gpu_metrics_cache", (0.0, None))
        monkeypatch.setattr(_mod, "_windows_dxgi_adapters_cache", (0.0, []))
        monkeypatch.setattr(_mod, "_windows_dxgi_adapters", lambda: [{
            "name": "AMD Radeon RX 9070 XT", "vendor_id": 0x1002,
            "memory_total_mb": 16368, "luid_high": 1, "luid_low": 2,
            "shared_memory_total_mb": 16384,
            "software": False,
        }])
        calls = []

        def run(*args, **kwargs):
            calls.append(args[0])
            return types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"adapters": [{
                    "prefix": "luid_0x00000001_0x00000002",
                    "utilization_percent": 140,
                    "utilization_available": True,
                    "dedicated_used_bytes": 99 * 1024**3,
                    "shared_used_bytes": 0,
                    "memory_usage_available": True,
                }]}),
                stderr="",
            )

        monkeypatch.setattr(_mod.subprocess, "run", run)

        first = _mod._windows_gpu_metrics()
        second = _mod._windows_gpu_metrics()

        assert first == second
        assert first["utilization_percent"] == 100
        assert first["memory_used_mb"] == first["memory_total_mb"]
        assert first["temperature_available"] is False
        assert first["gpus"][0]["uuid"] == "luid-00000001-00000002"
        assert len(calls) == 1

    def test_windows_unified_gpu_uses_shared_memory_and_system_ram(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod, "GPU_BACKEND", "amd")
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        (tmp_path / ".env").write_text("SYSTEM_RAM_GB=128\n")
        monkeypatch.setattr(_mod, "_windows_gpu_metrics_cache", (0.0, None))
        monkeypatch.setattr(_mod, "_windows_dxgi_adapters_cache", (0.0, []))
        monkeypatch.setattr(_mod, "_windows_dxgi_adapters", lambda: [{
            "name": "AMD Radeon 8060S Graphics", "vendor_id": 0x1002,
            "memory_total_mb": 2048, "shared_memory_total_mb": 65536,
            "luid_high": 3, "luid_low": 4, "software": False,
        }])
        monkeypatch.setattr(_mod.subprocess, "run", lambda *args, **kwargs: types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"adapters": [{
                "prefix": "luid_0x00000003_0x00000004",
                "utilization_percent": 61,
                "utilization_available": True,
                "dedicated_used_bytes": 1024 * 1024**2,
                "shared_used_bytes": 8 * 1024**3,
                "memory_usage_available": True,
            }]}),
            stderr="",
        ))

        payload = _mod._windows_gpu_metrics()

        assert payload["memory_type"] == "unified"
        assert payload["memory_total_mb"] == 96 * 1024
        assert payload["memory_used_mb"] == 9 * 1024
        assert payload["gpus"][0]["memory_type"] == "unified"

    def test_windows_llm_health_survives_optional_stats_failure(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        (tmp_path / ".env").write_text("AMD_INFERENCE_PORT=99999\n")
        monkeypatch.setattr(_mod, "_windows_llm_status_cache", (0.0, None))
        requested = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size=-1):
                return b'{"status":"ok","model_loaded":"test"}'

        def urlopen(url, timeout):
            requested_url = url.full_url if hasattr(url, "full_url") else str(url)
            requested.append(requested_url)
            if requested_url.endswith("/stats"):
                raise _mod.urllib_error.URLError("not supported")
            return Response()

        monkeypatch.setattr(_mod.urllib_request, "urlopen", urlopen)

        payload = _mod._windows_llm_status()

        assert payload["health"]["status"] == "ok"
        assert payload["stats"] is None
        assert requested[0] == "http://127.0.0.1:8080/api/v1/health"

    def test_windows_llm_status_redacts_runtime_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        (tmp_path / ".env").write_text(
            "AMD_INFERENCE_PORT=8080\nLEMONADE_API_KEY=secret-key\n"
        )
        monkeypatch.setattr(_mod, "_windows_llm_status_cache", (0.0, None))
        auth_headers = []

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size=-1):
                return json.dumps(self.payload).encode("utf-8")

        def urlopen(url, timeout):
            requested_url = url.full_url if hasattr(url, "full_url") else str(url)
            auth_headers.append(url.get_header("Authorization"))
            if requested_url.endswith("/health"):
                return Response({
                    "status": "ok", "version": "10.0.0",
                    "model_loaded": r"C:\Users\private\model.gguf",
                    "all_models_loaded": [{"checkpoint": r"C:\Users\private\model.gguf", "last_use": 123}],
                })
            return Response({"output_tokens": 7, "tokens_per_second": 188.49})

        monkeypatch.setattr(_mod.urllib_request, "urlopen", urlopen)

        payload = _mod._windows_llm_status()

        assert payload["health"] == {
            "status": "ok", "version": "10.0.0", "model_loaded": "model.gguf",
        }
        assert set(payload["stats"]) == {
            "time_to_first_token", "tokens_per_second", "input_tokens",
            "output_tokens", "prompt_tokens",
        }
        assert "private" not in json.dumps(payload)
        assert auth_headers == ["Bearer secret-key", "Bearer secret-key"]

    def test_windows_llm_stats_exclude_unrelated_health_fields(
        self, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        (tmp_path / ".env").write_text("AMD_INFERENCE_PORT=8080\n")
        health_counter = iter((1, 2))

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _size=-1):
                return json.dumps(self.payload).encode("utf-8")

        def urlopen(url, timeout):
            if url.full_url.endswith("/health"):
                return Response({
                    "status": "ok", "model_loaded": "model",
                    "unrelated_health_counter": next(health_counter),
                })
            return Response({
                "output_tokens": 7, "tokens_per_second": 42.0,
                "last_use": "2026-07-20T22:00:00Z",
            })

        monkeypatch.setattr(_mod.urllib_request, "urlopen", urlopen)
        monkeypatch.setattr(_mod, "_windows_llm_status_cache", (0.0, None))
        first = _mod._windows_llm_status()
        monkeypatch.setattr(_mod, "_windows_llm_status_cache", (0.0, None))
        second = _mod._windows_llm_status()

        assert first["stats"] == second["stats"]

    def test_windows_llm_rejects_oversized_runtime_response(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
        (tmp_path / ".env").write_text("AMD_INFERENCE_PORT=8080\n")
        monkeypatch.setattr(_mod, "_windows_llm_status_cache", (0.0, None))

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, size=-1):
                return b"x" * size

        monkeypatch.setattr(_mod.urllib_request, "urlopen", lambda *args, **kwargs: Response())

        assert _mod._windows_llm_status() is None


class TestDockerServiceHealthSnapshot:

    def test_uses_compose_service_labels_and_caches_snapshot(self, monkeypatch):
        monkeypatch.setattr(_mod, "_service_health_cache", (0.0, None))
        calls = []

        def run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "ps":
                return types.SimpleNamespace(returncode=0, stdout="ods-dashboard\n", stderr="")
            return types.SimpleNamespace(returncode=0, stdout=json.dumps([{
                "Name": "/ods-dashboard",
                "State": {"Status": "running", "Health": {"Status": "healthy"}},
                "Config": {"Labels": {"com.docker.compose.service": "dashboard"}},
            }]), stderr="")

        monkeypatch.setattr(_mod.subprocess, "run", run)

        first = _mod._docker_service_health_snapshot()
        second = _mod._docker_service_health_snapshot()

        assert first == second
        assert first["containers"] == [{
            "service_id": "dashboard",
            "container_name": "ods-dashboard",
            "state": "running",
            "health": "healthy",
        }]
        assert len(calls) == 2


class TestObservabilityWire:

    def test_read_only_endpoints_require_auth_and_return_versioned_contracts(
        self, monkeypatch,
    ):
        import urllib.error
        import urllib.request
        from http.server import HTTPServer

        monkeypatch.setattr(_mod, "AGENT_API_KEY", "wire-test-secret")
        monkeypatch.setattr(_mod, "_windows_gpu_metrics", lambda: {
            "schema_version": "ods.host-gpu-metrics.v1", "name": "GPU",
        })
        monkeypatch.setattr(_mod, "_windows_llm_status", lambda: {
            "schema_version": "ods.host-llm-status.v1", "health": {"status": "ok"},
        })
        monkeypatch.setattr(_mod, "_docker_service_health_snapshot", lambda: {
            "schema_version": "ods.host-service-health.v1", "containers": [],
        })

        server = HTTPServer(("127.0.0.1", 0), _mod.AgentHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with pytest.raises(urllib.error.HTTPError) as denied:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/gpu/metrics", timeout=2)
            assert denied.value.code == 401

            expected = {
                "/v1/gpu/metrics": "ods.host-gpu-metrics.v1",
                "/v1/llm/status": "ods.host-llm-status.v1",
                "/v1/service/health": "ods.host-service-health.v1",
            }
            for path, schema_version in expected.items():
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}{path}",
                    headers={"Authorization": "Bearer wire-test-secret"},
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                assert response.status == 200
                assert payload["schema_version"] == schema_version
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
