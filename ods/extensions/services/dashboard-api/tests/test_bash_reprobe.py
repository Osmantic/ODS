"""Regression tests for _find_usable_bash caching behavior.

Bug: a transient startup probe failure (installer still writing, AV scanning,
first-run setup) permanently poisoned _usable_bash to False, causing every
subsequent call to resolve_compose_flags() to raise RuntimeError even when
the Bash binary was genuinely available.

Fix: only cache positive results.  A failed probe resets to None so the next
call re-probes all candidates.

The regression was reproduced by the public-beta Windows fleet lane.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest

_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent"] = _mod
_spec.loader.exec_module(_mod)


class TestFindUsableBashCaching:
    """Verify that _find_usable_bash caches successes but retries failures."""

    def _reset_cache(self, monkeypatch):
        """Reset both cache variables to their initial unloaded state."""
        monkeypatch.setattr(_mod, "_usable_bash", None)
        monkeypatch.setattr(_mod, "_update_usable_bash", None)

    # -- Success is cached, subsequent calls skip the probe ----------------

    def test_successful_probe_is_cached_and_reused(self, monkeypatch):
        """Once _find_usable_bash finds a working bash, it must not re-probe."""
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")

        probe_count = 0

        def counting_run(cmd, **kwargs):
            nonlocal probe_count
            probe_count += 1
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        monkeypatch.setattr(_mod.shutil, "which", lambda name: "/bin/bash")
        monkeypatch.setattr(_mod.subprocess, "run", counting_run)

        result1 = _mod._find_usable_bash()
        assert result1 == "/bin/bash"
        assert probe_count == 1

        result2 = _mod._find_usable_bash()
        assert result2 == "/bin/bash"
        assert probe_count == 1  # no second probe — cached

    # -- Failure is NOT cached, subsequent calls re-probe ------------------

    def test_failed_probe_is_not_cached_permanently(self, monkeypatch):
        """After a failed probe the cache must not block future calls."""
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod.shutil, "which", lambda name: "/bin/bash")

        call_count = 0

        def flaky_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First probe fails (simulating transient startup condition)
                return subprocess.CompletedProcess(cmd, 1, "", "no such shell")
            # Second probe succeeds
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        monkeypatch.setattr(_mod.subprocess, "run", flaky_run)

        assert _mod._find_usable_bash() is None
        assert call_count == 1

        # The cache must NOT be False here — it must allow retry
        assert _mod._usable_bash is None or _mod._usable_bash == "/bin/bash"

        # Second call must re-probe and succeed
        result = _mod._find_usable_bash()
        assert result == "/bin/bash"
        assert call_count == 2

    def test_repeated_failures_do_not_poison_cache(self, monkeypatch):
        """Three consecutive failures must all return None without blocking."""
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod.shutil, "which", lambda name: "/bin/bash")

        call_count = 0

        def always_fail_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            return subprocess.CompletedProcess(cmd, 1, "", "fail")

        monkeypatch.setattr(_mod.subprocess, "run", always_fail_run)

        for _ in range(3):
            assert _mod._find_usable_bash() is None

        assert call_count == 3  # each call re-probes
        assert _mod._usable_bash is None  # never cached as False

    # -- Windows-specific: transient probe failure then availability --------

    def test_windows_transient_startup_failure_then_availability(self, monkeypatch):
        """Simulate the reported bug: startup probe fails, later call succeeds.

        This is the exact Windows scenario: Git Bash exists at
        C:\\Program Files\\Git\\bin\\bash.exe but the first subprocess call
        fails (e.g., installer still extracting, AV lock), and a later call
        to the same endpoint must succeed without restarting the process.
        """
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Windows")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setattr(
            _mod.shutil,
            "which",
            lambda name: (
                r"C:\Program Files\Git\cmd\git.exe"
                if name == "git"
                else r"C:\Program Files\Git\bin\bash.exe"
            ),
        )

        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        monkeypatch.setattr(
            _mod.Path,
            "exists",
            lambda path: str(path) == git_bash,
        )

        call_count = 0

        def flaky_windows_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if cmd[0] == git_bash:
                if call_count == 1:
                    # Simulate timeout during first-run extraction
                    raise subprocess.TimeoutExpired(cmd, timeout=5)
                return subprocess.CompletedProcess(cmd, 0, "ok", "")
            raise AssertionError(f"unexpected: {cmd}")

        monkeypatch.setattr(_mod.subprocess, "run", flaky_windows_run)

        # First call — transient failure
        assert _mod._find_usable_bash() is None
        assert call_count == 1

        # Verify cache state: must not be False
        assert _mod._usable_bash is not False

        # Second call — re-probes and succeeds
        result = _mod._find_usable_bash()
        assert result == git_bash
        assert call_count == 2

    def test_resolve_compose_flags_recovers_after_bash_transient(self, monkeypatch, tmp_path):
        """End-to-end: resolve_compose_flags() raises on first call, then
        succeeds on second call without process restart."""
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod.shutil, "which", lambda name: "/bin/bash")

        install_dir = tmp_path / "ods"
        scripts_dir = install_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "resolve-compose-stack.sh").write_text("#!/bin/bash\n")
        monkeypatch.setattr(_mod, "INSTALL_DIR", install_dir)
        monkeypatch.setattr(_mod, "TIER", "1")
        monkeypatch.setattr(_mod, "GPU_BACKEND", "nvidia")
        monkeypatch.setattr(_mod, "GPU_COUNT", "1")

        call_count = 0

        def flaky_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if cmd[0] == "/bin/bash" and "-lc" in cmd:
                if call_count <= 1:
                    # First bash probe fails
                    return subprocess.CompletedProcess(cmd, 1, "", "fail")
                # Bash probe succeeds
                return subprocess.CompletedProcess(cmd, 0, "ok", "")
            # resolve-compose-stack.sh call
            return subprocess.CompletedProcess(
                cmd, 0, "-f docker-compose.base.yml\n", "",
            )

        monkeypatch.setattr(_mod.subprocess, "run", flaky_run)

        # First call raises because bash probe fails
        with pytest.raises(RuntimeError, match="usable Bash runtime"):
            _mod.resolve_compose_flags()

        # Second call succeeds because bash is re-probed
        flags = _mod.resolve_compose_flags()
        assert flags == ["-f", "docker-compose.base.yml"]

    # -- _find_update_bash follows the same contract -----------------------

    def test_find_update_bash_retries_after_transient(self, monkeypatch):
        """_find_update_bash delegates to _find_usable_bash and must also
        allow retry on transient failure."""
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(_mod.platform, "system", lambda: "Linux")
        monkeypatch.setattr(_mod.shutil, "which", lambda name: "/bin/bash")

        call_count = 0

        def flaky_run(cmd, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return subprocess.CompletedProcess(cmd, 1, "", "fail")
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        monkeypatch.setattr(_mod.subprocess, "run", flaky_run)

        assert _mod._find_update_bash() is None
        assert _mod._find_update_bash() == "/bin/bash"
