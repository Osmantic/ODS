"""Structured transport specs for remote-provider routes.

This module is intentionally inert: it builds argument vectors and sanitized
metadata, but it never starts SSH, opens sockets, or reads secret material.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping


DEFAULT_SSH_IDENTITY_PATH = PurePosixPath("/state/remote-provider/secrets/ssh-identity")
DEFAULT_SSH_KNOWN_HOSTS_PATH = PurePosixPath("/state/remote-provider/secrets/known_hosts")
DEFAULT_SSH_LOCAL_BIND_HOST = "0.0.0.0"
DEFAULT_SSH_INFERENCE_LISTEN_PORT = 18091
DEFAULT_SSH_CONTROL_LISTEN_PORT = 18092

_SAFE_SSH_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_SAFE_SSH_USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


class TransportError(ValueError):
    """Raised when route metadata cannot form a safe transport spec."""


@dataclass(frozen=True)
class SshTunnelSpec:
    """One local SSH forward owned by the future transport service."""

    name: str
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int
    args: tuple[str, ...]


def _string(value: object) -> str:
    return str(value or "").strip()


def _safe_host(value: object, label: str) -> str:
    host = _string(value)
    if not _SAFE_SSH_HOST_RE.fullmatch(host):
        raise TransportError(f"{label} is not a safe SSH host token")
    return host


def _safe_user(value: object) -> str:
    user = _string(value)
    if not _SAFE_SSH_USER_RE.fullmatch(user):
        raise TransportError("SSH user is not a safe token")
    return user


def _safe_port(value: object, label: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise TransportError(f"{label} must be an integer port") from exc
    if port < 1 or port > 65535:
        raise TransportError(f"{label} must be between 1 and 65535")
    return port


def _container_path(value: object, label: str) -> str:
    path = str(value or "").strip()
    if not path.startswith("/") or "\\" in path or any(ord(char) < 32 for char in path):
        raise TransportError(f"{label} must be an absolute POSIX container path")
    return path


def _ssh_base_args(
    *,
    ssh_host: str,
    ssh_user: str,
    ssh_port: int,
    identity_path: str,
    known_hosts_path: str,
) -> tuple[str, ...]:
    return (
        "ssh",
        "-F",
        "/dev/null",
        "-N",
        "-T",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ForwardAgent=no",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PermitLocalCommand=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts_path}",
        "-o",
        f"IdentityFile={identity_path}",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-p",
        str(ssh_port),
        f"{ssh_user}@{ssh_host}",
    )


def _tunnel_spec(
    *,
    name: str,
    target_host: str,
    target_port: int,
    listen_port: int,
    base_args: tuple[str, ...],
    listen_host: str,
) -> SshTunnelSpec:
    if ":" in listen_host:
        raise TransportError("SSH local bind host must be an IPv4 or hostname token")
    # ssh(1) splits -L on colons, so an IPv6 literal target has to be bracketed
    # or the field boundaries are ambiguous and ssh rejects the whole forward.
    # The host token itself stays unbracketed on the spec, which is what the
    # redacted plan reports.
    forward_target = f"[{target_host}]" if ":" in target_host else target_host
    forward = f"{listen_host}:{listen_port}:{forward_target}:{target_port}"
    args = base_args[:-1] + ("-L", forward, base_args[-1])
    return SshTunnelSpec(
        name=name,
        listen_host=listen_host,
        listen_port=listen_port,
        target_host=target_host,
        target_port=target_port,
        args=args,
    )


def build_ssh_tunnel_specs(
    route: Mapping[str, Any],
    *,
    identity_path: str | PurePosixPath = DEFAULT_SSH_IDENTITY_PATH,
    known_hosts_path: str | PurePosixPath = DEFAULT_SSH_KNOWN_HOSTS_PATH,
    listen_host: str = DEFAULT_SSH_LOCAL_BIND_HOST,
    inference_listen_port: int = DEFAULT_SSH_INFERENCE_LISTEN_PORT,
    control_listen_port: int = DEFAULT_SSH_CONTROL_LISTEN_PORT,
) -> tuple[SshTunnelSpec, ...]:
    """Build fixed SSH tunnel argv specs from validated route metadata."""
    if route.get("enabled") is not True or route.get("transport") != "ssh":
        raise TransportError("SSH tunnel specs require an enabled SSH route")
    ssh = route.get("ssh")
    if not isinstance(ssh, Mapping):
        raise TransportError("SSH route metadata is missing")

    ssh_host = _safe_host(ssh.get("host"), "SSH host")
    ssh_user = _safe_user(ssh.get("user"))
    ssh_port = _safe_port(ssh.get("port"), "SSH port")
    inference_host = _safe_host(ssh.get("inferenceHost"), "SSH inference host")
    inference_port = _safe_port(ssh.get("inferencePort"), "SSH inference port")

    bind_host = _safe_host(listen_host, "SSH local bind host")
    inference_bind_port = _safe_port(
        inference_listen_port,
        "SSH inference listen port",
    )
    control_bind_port = _safe_port(control_listen_port, "SSH control listen port")
    base_args = _ssh_base_args(
        ssh_host=ssh_host,
        ssh_user=ssh_user,
        ssh_port=ssh_port,
        identity_path=_container_path(identity_path, "SSH identity path"),
        known_hosts_path=_container_path(known_hosts_path, "SSH known_hosts path"),
    )

    specs = [
        _tunnel_spec(
            name="inference",
            target_host=inference_host,
            target_port=inference_port,
            listen_port=inference_bind_port,
            base_args=base_args,
            listen_host=bind_host,
        )
    ]
    control_host = _string(ssh.get("controlHost"))
    control_port = _string(ssh.get("controlPort"))
    if control_host or control_port:
        specs.append(
            _tunnel_spec(
                name="control",
                target_host=_safe_host(control_host, "SSH control host"),
                target_port=_safe_port(control_port, "SSH control port"),
                listen_port=control_bind_port,
                base_args=base_args,
                listen_host=bind_host,
            )
        )
    return tuple(specs)


__all__ = [
    "DEFAULT_SSH_CONTROL_LISTEN_PORT",
    "DEFAULT_SSH_IDENTITY_PATH",
    "DEFAULT_SSH_INFERENCE_LISTEN_PORT",
    "DEFAULT_SSH_KNOWN_HOSTS_PATH",
    "DEFAULT_SSH_LOCAL_BIND_HOST",
    "SshTunnelSpec",
    "TransportError",
    "build_ssh_tunnel_specs",
]
