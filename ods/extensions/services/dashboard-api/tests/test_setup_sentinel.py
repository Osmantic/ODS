"""Tests for the __ODS_RESULT__ sentinel emission on /api/setup/test.

The frontend SetupWizard parser treats the sentinel as the source of truth
for diagnostic success/failure. Absence falls back to scraping log lines
for "All tests passed!", which is fragile, so the contract is that every
terminal state of the streaming endpoint MUST yield exactly one sentinel
line as its last line.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest


SENTINEL_PREFIX = "__ODS_RESULT__:"


class _AsyncByteStream:
    def __init__(self, text: str):
        self._lines = iter(text.encode().splitlines(keepends=True))

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._lines)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _mock_diagnostic_process(monkeypatch, *, output: str, returncode: int) -> None:
    """Run sentinel assertions without depending on a host Bash installation."""

    class Process:
        def __init__(self):
            self.stdout = _AsyncByteStream(output)
            self.returncode = None

        async def wait(self):
            self.returncode = returncode
            return returncode

        def kill(self):
            self.returncode = returncode

    async def create_subprocess_exec(program, script_path, **_kwargs):
        assert program == "bash"
        assert Path(script_path).is_file()
        return Process()

    monkeypatch.setattr(
        "routers.setup.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )


def _last_sentinel_line(lines):
    """Return the parsed (status, rc) tuple for the last sentinel found.

    Yields a tuple even when several sentinel lines exist in the stream
    so we can assert the final terminator wins. ``rc`` is returned as a
    string to keep the parse symmetric with the wire format.
    """
    sentinel = None
    for line in lines:
        if line.startswith(SENTINEL_PREFIX):
            sentinel = line
    assert sentinel is not None, f"no __ODS_RESULT__ line found in stream: {lines!r}"
    payload = sentinel[len(SENTINEL_PREFIX):]
    status, _, rc = payload.partition(":")
    return status, rc


def _write_test_script(install_root: Path, body: str) -> Path:
    """Write a bash script to install_root/scripts/ods-test-functional.sh."""
    scripts = install_root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    path = scripts / "ods-test-functional.sh"
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture()
def setup_install_dir(tmp_path, monkeypatch):
    """Provide an isolated INSTALL_DIR for the setup router.

    The setup endpoint resolves the diagnostic script via
    ``Path(INSTALL_DIR) / "scripts" / "ods-test-functional.sh"``; if it's
    not there it falls back to ``Path(os.getcwd()) / "ods-test-functional.sh"``.
    We point INSTALL_DIR at a tmp dir and chdir to another tmp dir so the
    cwd fallback is also empty unless the test opts in.
    """
    install_root = tmp_path / "ods"
    install_root.mkdir()
    cwd_root = tmp_path / "cwd"
    cwd_root.mkdir()

    monkeypatch.setattr("routers.setup.INSTALL_DIR", str(install_root))
    monkeypatch.chdir(cwd_root)
    return install_root


def test_setup_test_emits_pass_sentinel_on_success(
    test_client, setup_install_dir, monkeypatch,
):
    """A diagnostic script that exits 0 must terminate with PASS:0."""
    _write_test_script(setup_install_dir, "#!/bin/bash\necho 'check 1 ok'\necho 'check 2 ok'\nexit 0\n")
    _mock_diagnostic_process(
        monkeypatch,
        output="check 1 ok\ncheck 2 ok\n",
        returncode=0,
    )

    with test_client.stream("POST", "/api/setup/test", headers=test_client.auth_headers) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    status, rc = _last_sentinel_line(lines)
    assert status == "PASS"
    assert rc == "0"
    # Sentinel must be the LAST non-empty line on the wire.
    assert lines[-1].startswith(SENTINEL_PREFIX), (
        f"sentinel was emitted but not last; tail was: {lines[-3:]!r}"
    )


def test_setup_test_emits_fail_sentinel_with_returncode_on_failure(
    test_client, setup_install_dir, monkeypatch,
):
    """A diagnostic script that exits non-zero must terminate with FAIL:<rc>."""
    _write_test_script(setup_install_dir, "#!/bin/bash\necho 'check failed'\nexit 3\n")
    _mock_diagnostic_process(
        monkeypatch,
        output="check failed\n",
        returncode=3,
    )

    with test_client.stream("POST", "/api/setup/test", headers=test_client.auth_headers) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    status, rc = _last_sentinel_line(lines)
    assert status == "FAIL"
    assert rc == "3", f"expected the script's literal exit code, got {rc!r}"
    assert lines[-1].startswith(SENTINEL_PREFIX)


def test_setup_test_emits_sentinel_when_script_missing(test_client, setup_install_dir):
    """When neither INSTALL_DIR/scripts nor cwd has the script, the
    error_stream() fallback runs aiohttp probes against configured
    services and still terminates with a sentinel."""
    # No script written — both lookup paths miss; error_stream() runs.
    # In the test environment the dashboard SERVICES map is populated but
    # nothing is actually listening, so every probe will fail and the
    # sentinel should be FAIL:1.

    with test_client.stream("POST", "/api/setup/test", headers=test_client.auth_headers) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    status, rc = _last_sentinel_line(lines)
    assert status in {"PASS", "FAIL"}, f"unknown sentinel status {status!r}"
    # The FAIL path is the realistic outcome — services aren't running in
    # the test harness — but assert only the structural contract here so
    # this test stays robust against fixture changes.
    assert rc.lstrip("-").isdigit(), f"sentinel rc must be numeric, got {rc!r}"
    assert lines[-1].startswith(SENTINEL_PREFIX)


def test_setup_test_sentinel_format_is_machine_parseable(
    test_client, setup_install_dir, monkeypatch,
):
    """The on-the-wire format must match the regex the SetupWizard frontend
    pins: ``^__ODS_RESULT__:(PASS|FAIL):(-?\\d+)$``. This test guards
    against accidental whitespace, prefix, or trailing-character drift on
    either side of the contract."""
    import re

    _write_test_script(setup_install_dir, "#!/bin/bash\nexit 0\n")
    _mock_diagnostic_process(monkeypatch, output="", returncode=0)
    sentinel_re = re.compile(r"^__ODS_RESULT__:(PASS|FAIL):(-?\d+)$")

    with test_client.stream("POST", "/api/setup/test", headers=test_client.auth_headers) as response:
        assert response.status_code == 200
        lines = [line for line in response.iter_lines() if line]

    sentinel_lines = [line for line in lines if line.startswith(SENTINEL_PREFIX)]
    assert len(sentinel_lines) == 1, (
        f"expected exactly one sentinel line, got {len(sentinel_lines)}: {sentinel_lines!r}"
    )
    assert sentinel_re.match(sentinel_lines[0]), (
        f"sentinel does not match frontend parser regex: {sentinel_lines[0]!r}"
    )


def test_setup_test_requires_auth(test_client):
    """Unauthenticated POST must fail before anything is streamed."""
    response = test_client.post("/api/setup/test")
    assert response.status_code == 401
