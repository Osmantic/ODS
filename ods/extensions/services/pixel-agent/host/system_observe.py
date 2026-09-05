#!/usr/bin/env python3
"""Return small, secret-free host capability observations for Pixel Operations."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
from typing import Sequence

MAX_OUTPUT = 64 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+()/@-]{0,95}$")
SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
INTEROP_NAME = re.compile(r"^[1-9][0-9]{0,19}_interop$")
INTEROP_ROOT = Path("/run/WSL")
WINDOWS_POWERSHELL = Path("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
SAFE_PEER_NAME = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$"
)
DEFAULT_PEER_PORTS = (22, 80, 443, 3389, 5985, 5986)
TAILSCALE_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
LAN_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


def _run(
    argv: Sequence[str],
    timeout: int = 8,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    if extra_env:
        environment.update(extra_env)
    try:
        result = subprocess.run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if (
        len(result.stdout.encode("utf-8", "replace")) > MAX_OUTPUT
        or len(result.stderr.encode("utf-8", "replace")) > MAX_OUTPUT
    ):
        return None
    return result


def _trusted_executable(candidates: Sequence[str]) -> str | None:
    for raw in candidates:
        try:
            candidate = Path(raw).resolve(strict=True)
            info = candidate.stat()
        except OSError:
            continue
        if (
            not candidate.is_absolute()
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_mode & 0o022
            or not os.access(candidate, os.X_OK)
        ):
            continue
        return str(candidate)
    return None


def observe_gpu() -> dict:
    executable = _trusted_executable([
        "/usr/lib/wsl/lib/nvidia-smi",
        "/usr/bin/nvidia-smi",
        "/usr/local/bin/nvidia-smi",
    ])
    devices: list[dict] = []
    if executable:
        result = _run(
            [
                executable,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ]
        )
        if result and result.returncode == 0 and not result.stderr.strip():
            for line in result.stdout.splitlines()[:16]:
                fields = [field.strip() for field in line.split(",")]
                if len(fields) != 3 or not SAFE_NAME.fullmatch(fields[0]) or not SAFE_VERSION.fullmatch(fields[2]):
                    devices = []
                    break
                try:
                    memory_mib = int(fields[1])
                except ValueError:
                    devices = []
                    break
                if not 1 <= memory_mib <= 10_000_000:
                    devices = []
                    break
                devices.append(
                    {"name": fields[0], "memoryMiB": memory_mib, "driver": fields[2]}
                )
    return {
        "schemaVersion": 1,
        "kind": "ods-host-gpu",
        "available": bool(devices),
        "backend": "nvidia" if devices else "unavailable",
        "devices": devices,
    }


def _native_tailscale_state() -> tuple[bool, str, bool] | None:
    executable = _trusted_executable(["/usr/bin/tailscale", "/usr/local/bin/tailscale"])
    if not executable:
        return None
    result = _run([executable, "status", "--json"])
    if not result or result.returncode != 0 or result.stderr.strip():
        return True, "unknown", False
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return True, "unknown", False
    raw = str(value.get("BackendState", "")).strip().casefold()
    states = {
        "running": "running",
        "starting": "starting",
        "stopped": "stopped",
        "needslogin": "needs-login",
        "needs-login": "needs-login",
    }
    state = states.get(raw, "unknown")
    return True, state, state == "running"


def _trusted_interop_sockets(root: Path = INTEROP_ROOT) -> list[Path]:
    try:
        root_info = root.lstat()
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or stat.S_ISLNK(root_info.st_mode)
            or root_info.st_uid != 0
            or root_info.st_mode & 0o022
        ):
            return []
        candidates: list[tuple[int, Path]] = []
        for candidate in root.iterdir():
            if not INTEROP_NAME.fullmatch(candidate.name):
                continue
            info = candidate.lstat()
            if stat.S_ISSOCK(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_uid == 0:
                candidates.append((info.st_mtime_ns, candidate))
    except OSError:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [candidate for _, candidate in candidates[:8]]


def _windows_powershell_result(command: str) -> subprocess.CompletedProcess[str] | None:
    try:
        executable = WINDOWS_POWERSHELL.resolve(strict=True)
        info = executable.stat()
    except OSError:
        return None
    if (
        executable != WINDOWS_POWERSHELL
        or not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o022
        or not os.access(executable, os.X_OK)
    ):
        return None
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    argv = [
        str(executable),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded,
    ]
    for socket_path in _trusted_interop_sockets():
        result = _run(argv, timeout=3, extra_env={"WSL_INTEROP": str(socket_path)})
        if result and result.returncode == 0 and result.stdout.strip():
            return result
    return None


def _windows_tailscale_state() -> tuple[bool, str, bool] | None:
    result = _windows_powershell_result(
        "$s=Get-Service -Name Tailscale -ErrorAction SilentlyContinue; "
        "if($null -eq $s){'missing'}else{$s.Status.ToString()}"
    )
    if not result:
        return None
    raw = result.stdout.strip().casefold()
    if raw == "missing":
        return False, "not-installed", False
    if raw == "running":
        return True, "service-running", True
    if raw in {"stopped", "startpending", "stoppending", "paused", "pausepending", "continuepending"}:
        return True, "service-not-running", False
    return True, "unknown", False


def observe_tailscale() -> dict:
    result = _native_tailscale_state() or _windows_tailscale_state()
    available, state, service_running = result or (False, "not-installed", False)
    return {
        "schemaVersion": 1,
        "kind": "ods-host-tailscale",
        "available": available,
        "state": state,
        "serviceRunning": service_running,
    }


def _normalized_peer_target(value: str) -> str:
    target = value.strip().rstrip(".")
    if not target or len(target) > 253:
        raise ValueError("invalid peer target")
    try:
        return str(ipaddress.ip_address(target))
    except ValueError:
        pass
    if not SAFE_PEER_NAME.fullmatch(target):
        raise ValueError("invalid peer target")
    if ".." in target or any(
        label.startswith("-") or label.endswith("-") for label in target.split(".")
    ):
        raise ValueError("invalid peer target")
    return target


def _normalized_peer_ports(value: str) -> tuple[int, ...]:
    if not value:
        return DEFAULT_PEER_PORTS
    raw = value.split(",")
    if len(raw) > 8 or any(not item.isdigit() for item in raw):
        raise ValueError("invalid peer ports")
    ports = tuple(int(item) for item in raw)
    if (
        len(set(ports)) != len(ports)
        or any(port < 1 or port > 65535 for port in ports)
    ):
        raise ValueError("invalid peer ports")
    return ports


def _peer_address_scope(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if address in TAILSCALE_V4 or address in TAILSCALE_V6:
        return "tailscale"
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        raise ValueError("peer target resolved outside the remote private network boundary")
    if address.is_link_local:
        return "link-local"
    if any(address.version == network.version and address in network for network in LAN_NETWORKS):
        return "lan"
    raise ValueError("peer target resolved outside the private network boundary")


def _resolve_peer(target: str) -> list[tuple[int, str, str]]:
    try:
        records = socket.getaddrinfo(target, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    resolved: list[tuple[int, str, str]] = []
    seen: set[str] = set()
    for family, _socktype, _protocol, _canonical, sockaddr in records:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = str(ipaddress.ip_address(sockaddr[0]))
        if address in seen:
            continue
        seen.add(address)
        resolved.append((family, address, _peer_address_scope(ipaddress.ip_address(address))))
        if len(resolved) >= 4:
            break
    return resolved


def _tailscale_status_json() -> dict | None:
    executable = _trusted_executable(["/usr/bin/tailscale", "/usr/local/bin/tailscale"])
    if executable:
        result = _run([executable, "status", "--json"])
    else:
        result = _windows_powershell_result(
            "$p=Join-Path $env:ProgramFiles 'Tailscale\\tailscale.exe'; "
            "if(Test-Path -LiteralPath $p){& $p status --json}"
        )
    if not result or result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _tailscale_peer_status(target: str) -> dict:
    status = _tailscale_status_json()
    if status is None:
        return {"available": False, "found": False, "online": None, "addresses": []}
    target_key = target.casefold()
    peers = status.get("Peer")
    values = peers.values() if isinstance(peers, dict) else []
    matches = []
    for peer in values:
        if not isinstance(peer, dict):
            continue
        hostname = str(peer.get("HostName") or "").strip().rstrip(".")
        dns_name = str(peer.get("DNSName") or "").strip().rstrip(".")
        names = {hostname.casefold(), dns_name.casefold()}
        if dns_name:
            names.add(dns_name.split(".", 1)[0].casefold())
        addresses = peer.get("TailscaleIPs")
        address_strings = [str(item) for item in addresses] if isinstance(addresses, list) else []
        if target_key not in names and target not in address_strings:
            continue
        safe_addresses = []
        for raw in address_strings[:4]:
            try:
                parsed = ipaddress.ip_address(raw)
            except ValueError:
                continue
            if parsed in TAILSCALE_V4 or parsed in TAILSCALE_V6:
                safe_addresses.append(str(parsed))
        matches.append({"online": peer.get("Online") is True, "addresses": safe_addresses})
    if len(matches) != 1:
        return {"available": True, "found": False, "online": None, "addresses": []}
    return {
        "available": True,
        "found": True,
        "online": matches[0]["online"],
        "addresses": matches[0]["addresses"],
    }


def _probe_icmp(family: int, address: str) -> bool | None:
    executable = _trusted_executable(["/usr/bin/ping", "/bin/ping"])
    if not executable:
        return None
    argv = [executable]
    if family == socket.AF_INET6:
        argv.append("-6")
    argv.extend(["-c", "1", "-W", "2", "--", address])
    result = _run(argv, timeout=4)
    return result is not None and result.returncode == 0


def _probe_tcp(family: int, address: str, port: int) -> bool:
    endpoint = (address, port) if family == socket.AF_INET else (address, port, 0, 0)
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.4)
            return connection.connect_ex(endpoint) == 0
    except OSError:
        return False


def observe_network_peer(target: str, ports: str = "") -> dict:
    normalized_target = _normalized_peer_target(target)
    normalized_ports = _normalized_peer_ports(ports)
    resolved = _resolve_peer(normalized_target)
    tailscale = _tailscale_peer_status(normalized_target)
    known = {address for _family, address, _scope in resolved}
    for raw in tailscale["addresses"]:
        if len(resolved) >= 4:
            break
        if raw in known:
            continue
        parsed = ipaddress.ip_address(raw)
        resolved.append((socket.AF_INET if parsed.version == 4 else socket.AF_INET6, raw, "tailscale"))
        known.add(raw)
    observations = []
    for family, address, scope in resolved:
        icmp = _probe_icmp(family, address)
        tcp = [
            {"port": port, "open": _probe_tcp(family, address, port)}
            for port in normalized_ports
        ]
        observations.append({
            "address": address,
            "family": "ipv4" if family == socket.AF_INET else "ipv6",
            "scope": scope,
            "icmpReachable": icmp,
            "tcp": tcp,
        })
    reachable = tailscale["online"] is True or any(
        item["icmpReachable"] is True or any(port["open"] for port in item["tcp"])
        for item in observations
    )
    return {
        "schemaVersion": 1,
        "kind": "ods-host-network-peer",
        "target": normalized_target,
        "ports": list(normalized_ports),
        "resolved": bool(resolved),
        "reachable": reachable,
        "addresses": observations,
        "tailscale": tailscale,
    }


def main(argv: Sequence[str]) -> int:
    if len(argv) == 2 and argv[1] in {"gpu", "tailscale"}:
        value = observe_gpu() if argv[1] == "gpu" else observe_tailscale()
    elif len(argv) == 4 and argv[1] == "network-peer":
        try:
            value = observe_network_peer(argv[2], argv[3])
        except ValueError:
            return 2
    else:
        return 2
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
