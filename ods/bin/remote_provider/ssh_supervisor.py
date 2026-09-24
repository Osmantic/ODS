"""Support-bundle-safe planning for the remote-provider SSH tunnel supervisor.

The functions here are intentionally inert.  They validate route metadata,
summarize secret custody, and build the exact SSH argv plan, but they never
spawn ssh or open sockets.
"""

from __future__ import annotations

import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .transport import (
    DEFAULT_SSH_INFERENCE_LISTEN_PORT,
    SshTunnelSpec,
    build_ssh_tunnel_specs,
)


SSH_SUPERVISOR_PLAN_SCHEMA = "ods.remote-provider-ssh-supervisor-plan.v1"
DEFAULT_SSH_TUNNEL_SERVICE_HOST = "remote-provider-ssh-tunnel"
DEFAULT_SSH_SECRET_DIR = Path("/state/remote-provider/secrets")


def _file_status(path: Path) -> dict[str, Any]:
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return {"configured": False, "bytes": 0}
    except OSError:
        return {"configured": False, "bytes": None}
    if not stat.S_ISREG(stat_result.st_mode):
        return {"configured": False, "bytes": None}
    return {"configured": stat_result.st_size > 0, "bytes": stat_result.st_size}


def ssh_secret_status(secret_dir: str | Path = DEFAULT_SSH_SECRET_DIR) -> dict[str, Any]:
    """Return support-bundle-safe SSH secret custody status."""
    root = Path(secret_dir)
    return {
        "sshIdentity": _file_status(root / "ssh-identity"),
        "sshKnownHosts": _file_status(root / "known_hosts"),
    }


def _secret_ready(secret_status: Mapping[str, Any], key: str) -> bool:
    value = secret_status.get(key)
    return isinstance(value, Mapping) and value.get("configured") is True


def _public_tunnel(spec: SshTunnelSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "listenHost": spec.listen_host,
        "listenPort": spec.listen_port,
        "targetHost": spec.target_host,
        "targetPort": spec.target_port,
        "argv": list(spec.args),
    }


def _tunnel_base_url(specs: tuple[SshTunnelSpec, ...]) -> str:
    inference = next((spec for spec in specs if spec.name == "inference"), None)
    port = inference.listen_port if inference is not None else DEFAULT_SSH_INFERENCE_LISTEN_PORT
    return f"http://{DEFAULT_SSH_TUNNEL_SERVICE_HOST}:{port}/v1"


def ssh_tunnel_base_url(route: Mapping[str, Any]) -> str:
    """Return the internal OpenAI-compatible base URL exposed by the SSH tunnel."""
    return _tunnel_base_url(build_ssh_tunnel_specs(route))


def ssh_supervisor_plan(
    route: Mapping[str, Any],
    *,
    secrets: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a redacted supervisor plan for an SSH route."""
    secret_status = dict(secrets or ssh_secret_status())
    base: dict[str, Any] = {
        "schema": SSH_SUPERVISOR_PLAN_SCHEMA,
        "status": "disabled",
        "ready": False,
        "readyToStart": False,
        "reason": "remote_route_disabled",
        "tunnelBaseUrl": None,
        "tunnels": [],
        "secrets": secret_status,
    }
    if route.get("enabled") is not True:
        return base
    if route.get("transport") != "ssh":
        base["status"] = "inactive"
        base["reason"] = "not_ssh_transport"
        return base

    specs = build_ssh_tunnel_specs(route)
    missing = [
        key
        for key in ("sshIdentity", "sshKnownHosts")
        if not _secret_ready(secret_status, key)
    ]
    base.update(
        {
            "status": "blocked" if missing else "planned",
            "reason": "missing_ssh_secrets" if missing else "tunnel_process_not_started",
            "readyToStart": not missing,
            "tunnelBaseUrl": _tunnel_base_url(specs),
            "tunnels": [_public_tunnel(spec) for spec in specs],
            "missingSecrets": missing,
        }
    )
    return base


__all__ = [
    "DEFAULT_SSH_SECRET_DIR",
    "DEFAULT_SSH_TUNNEL_SERVICE_HOST",
    "SSH_SUPERVISOR_PLAN_SCHEMA",
    "ssh_secret_status",
    "ssh_supervisor_plan",
    "ssh_tunnel_base_url",
]
