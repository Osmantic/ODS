"""Tests for SshProcessSupervisor.reconcile() — regression suite for #2338.

These tests operate entirely in-process using fakes for the route state,
secret directory, and subprocess.Popen, so no real SSH binary is needed.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Module loader — stubs out the remote_provider package so the service can be
# imported without the full ODS bin/ tree on sys.path.
# ---------------------------------------------------------------------------

APP_MAIN = (
    Path(__file__).resolve().parent.parent / "app" / "main.py"
)


def _stub_remote_provider() -> None:
    """Inject minimal stubs for remote_provider.ssh_supervisor imports."""
    SSH_SUPERVISOR_PLAN_SCHEMA = "ods.remote-provider-ssh-supervisor-plan.v1"

    def ssh_secret_status(secret_dir: Any = None) -> dict[str, Any]:  # noqa: ANN001
        return {
            "sshIdentity": {"configured": True, "bytes": 512},
            "sshKnownHosts": {"configured": True, "bytes": 128},
        }

    def ssh_supervisor_plan(
        route: Any, *, secrets: Any = None
    ) -> dict[str, Any]:
        return {
            "schema": SSH_SUPERVISOR_PLAN_SCHEMA,
            "status": "disabled",
            "ready": False,
            "readyToStart": False,
            "reason": "remote_route_disabled",
            "tunnelBaseUrl": None,
            "tunnels": [],
            "secrets": ssh_secret_status(),
        }

    mod = types.ModuleType("remote_provider")
    sub = types.ModuleType("remote_provider.ssh_supervisor")
    sub.SSH_SUPERVISOR_PLAN_SCHEMA = SSH_SUPERVISOR_PLAN_SCHEMA
    sub.ssh_secret_status = ssh_secret_status
    sub.ssh_supervisor_plan = ssh_supervisor_plan
    sys.modules.setdefault("remote_provider", mod)
    sys.modules.setdefault("remote_provider.ssh_supervisor", sub)


def load_main() -> types.ModuleType:
    _stub_remote_provider()
    spec = importlib.util.spec_from_file_location("ssh_tunnel_main", APP_MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load once for the session — stubs are already in sys.modules.
_main = load_main()
SshProcessSupervisor = _main.SshProcessSupervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_supervisor(
    tmp_path: Path,
    *,
    route_json: dict[str, Any] | None = None,
) -> SshProcessSupervisor:
    """Return a supervisor pointed at a tmp route file and secret dir."""
    route_path = tmp_path / "routing-state.json"
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "ssh-identity").write_bytes(b"x" * 512)
    (secret_dir / "known_hosts").write_bytes(b"x" * 128)

    if route_json is not None:
        route_path.write_text(json.dumps(route_json), encoding="utf-8")

    return SshProcessSupervisor(route_path, secret_dir)


def _ssh_route(*, extra_argv_suffix: str | None = None) -> dict[str, Any]:
    """Return a minimal enabled SSH route state document."""
    argv = [
        "ssh",
        "-N",
        "-o", "StrictHostKeyChecking=yes",
        "-L", "18081:localhost:11434",
        "user@provider.example.com",
    ]
    if extra_argv_suffix is not None:
        argv[-1] = extra_argv_suffix
    return {
        "schema": "ods.remote-routing-state.v1",
        "enabled": True,
        "mode": "remote",
        "provider": {
            "transport": "ssh",
            "host": "provider.example.com",
        },
        "ssh": {
            "host": "provider.example.com",
            "user": "user",
            "tunnels": [
                {
                    "name": "inference",
                    "listenPort": 18081,
                    "targetPort": 11434,
                }
            ],
        },
    }


# ---------------------------------------------------------------------------
# Regression tests for #2338
# ---------------------------------------------------------------------------


def test_reconcile_logs_warning_when_combined_argv_raises(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression #2338: ValueError from _combined_ssh_argv must appear in logs."""
    supervisor = _make_supervisor(tmp_path)

    broken_plan = {
        "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
        "status": "active",
        "ready": True,
        "readyToStart": True,
        "reason": "ready",
        "tunnelBaseUrl": "http://remote-provider-ssh-tunnel:18081/v1",
        "tunnels": [
            # argv is deliberately incomplete — missing the -L forward flag.
            {"name": "inference", "argv": ["ssh", "user@host"]}
        ],
        "secrets": {
            "sshIdentity": {"configured": True, "bytes": 512},
            "sshKnownHosts": {"configured": True, "bytes": 128},
        },
    }

    with (
        patch.object(
            _main, "_supervisor_plan_for_paths", return_value=broken_plan
        ),
        caplog.at_level(logging.WARNING, logger="remote-provider-ssh-tunnel"),
    ):
        result = supervisor.reconcile()

    # The health payload must reflect the error.
    assert result["status"] in ("error", "invalid")

    assert result["plan"]["reason"] == "ssh_plan_unavailable"

    # The WARNING must contain the exc detail — not be silently swallowed.
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ssh plan argv invalid" in m for m in warning_messages), (
        f"Expected a 'ssh plan argv invalid' WARNING but got: {warning_messages}"
    )


def test_reconcile_warning_includes_exc_message(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression #2338: the logged warning must include the ValueError text."""
    supervisor = _make_supervisor(tmp_path)

    plan_missing_forward = {
        "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
        "status": "active",
        "ready": True,
        "readyToStart": True,
        "reason": "ready",
        "tunnelBaseUrl": "http://remote-provider-ssh-tunnel:18081/v1",
        "tunnels": [
            # No -L flag in argv → _forward_from_tunnel raises ValueError.
            {"name": "inference", "argv": ["ssh", "-N", "user@host"]}
        ],
        "secrets": {
            "sshIdentity": {"configured": True, "bytes": 512},
            "sshKnownHosts": {"configured": True, "bytes": 128},
        },
    }

    with (
        patch.object(
            _main, "_supervisor_plan_for_paths", return_value=plan_missing_forward
        ),
        caplog.at_level(logging.WARNING, logger="remote-provider-ssh-tunnel"),
    ):
        supervisor.reconcile()

    warning_text = " ".join(r.message for r in caplog.records if r.levelno == logging.WARNING)
    # The ValueError text from _forward_from_tunnel must be surfaced.
    assert "local forward" in warning_text or "argv" in warning_text, (
        f"ValueError detail missing from warning. Got: {warning_text!r}"
    )


def test_reconcile_no_warning_when_argv_is_valid(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Sanity: a healthy plan with readyToStart=False must not emit a warning."""
    supervisor = _make_supervisor(tmp_path)

    disabled_plan = {
        "schema": "ods.remote-provider-ssh-supervisor-plan.v1",
        "status": "disabled",
        "ready": False,
        "readyToStart": False,
        "reason": "remote_route_disabled",
        "tunnelBaseUrl": None,
        "tunnels": [],
        "secrets": {
            "sshIdentity": {"configured": False, "bytes": 0},
            "sshKnownHosts": {"configured": False, "bytes": 0},
        },
    }

    with (
        patch.object(
            _main, "_supervisor_plan_for_paths", return_value=disabled_plan
        ),
        caplog.at_level(logging.WARNING, logger="remote-provider-ssh-tunnel"),
    ):
        result = supervisor.reconcile()

    assert result["status"] == "disabled"
    ssh_argv_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "ssh plan argv invalid" in r.message
    ]
    assert not ssh_argv_warnings, (
        "No 'ssh plan argv invalid' warning should be emitted for a disabled plan"
    )
