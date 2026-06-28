#!/usr/bin/env python3
"""ODS mDNS announcer.

Publishes the device on the local network as `<ODS_DEVICE_NAME>.local`
(default `ods.local`) plus per-service `_http._tcp` records so any device
on the same LAN can find the dashboard and chat UI without knowing the IP.

When paired with ods-proxy on port 80, this makes the "open `ods.local`
from any phone" UX work: the device boots, joins WiFi, and starts announcing
itself within seconds.

Reads `ODS_DEVICE_NAME` and the service ports from `.env`. Re-publishes
when the file changes (poll-based, 30s cadence) so renaming the device or
changing a port doesn't require a service restart.

Linux-first: relies on `avahi-daemon` being installed and running (already
standard on Ubuntu / Debian / Fedora / Arch desktop installs). macOS has
built-in mDNS via mDNSResponder and announces hostname.local automatically;
this script is a no-op on Darwin (logs and exits 0). Windows mDNS support
varies — see BRANDING.md / docs/MDNS.md for follow-up.

Run via:
  python3 /opt/ods/bin/ods-mdns.py
or via the ods-mdns.service systemd unit.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import signal
import socket
import sys
import time
from pathlib import Path

# zeroconf import is deferred past the platform gate in main(). macOS has
# built-in Bonjour and Windows isn't covered yet — neither should hard-fail
# at start just because the package isn't installed.
IPVersion = None  # type: ignore
ServiceInfo = None  # type: ignore
Zeroconf = None  # type: ignore


def _import_zeroconf_or_die() -> None:
    """Linux-only path. Imports zeroconf lazily so non-Linux platforms can
    exit cleanly without the package installed."""
    global IPVersion, ServiceInfo, Zeroconf
    try:
        from zeroconf import IPVersion as _IPVersion  # noqa: PLC0415
        from zeroconf import ServiceInfo as _ServiceInfo  # noqa: PLC0415
        from zeroconf import Zeroconf as _Zeroconf  # noqa: PLC0415
    except ImportError:
        print(
            "ERROR: `zeroconf` Python package not installed. "
            "On Debian/Ubuntu: sudo apt install python3-zeroconf. "
            "On Fedora: sudo dnf install python3-zeroconf. "
            "On Arch: sudo pacman -S python-zeroconf.",
            file=sys.stderr,
        )
        sys.exit(1)
    IPVersion = _IPVersion
    ServiceInfo = _ServiceInfo
    Zeroconf = _Zeroconf

logging.basicConfig(
    level=os.environ.get("ODS_MDNS_LOG_LEVEL", "INFO"),
    format="%(asctime)s [ods-mdns] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

INSTALL_DIR = Path(os.environ.get("ODS_INSTALL_DIR", "/opt/ods"))
ENV_FILE = INSTALL_DIR / ".env"


def _safe_positive_int_env(key: str, default: int) -> int:
    raw = os.environ.get(key, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("Env %s=%r is not a valid integer; using default %d", key, raw, default)
        return default
    if value <= 0:
        logger.warning("Env %s=%r is non-positive; using default %d", key, raw, default)
        return default
    return value


POLL_INTERVAL = _safe_positive_int_env("ODS_MDNS_POLL_INTERVAL", 30)

# Hostname-safe pattern matches ODS_DEVICE_NAME schema in .env.schema.json.
_HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,30}[a-zA-Z0-9])?$")


def _read_env() -> dict[str, str]:
    """Return current .env values, ignoring comments and blank lines."""
    if not ENV_FILE.is_file():
        return {}
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _get_local_ip() -> str:
    """Best-effort local IPv4 address for the LAN-facing interface.

    Opens a UDP socket to a non-routable address — the kernel picks the
    interface it would use to reach the public internet, which is the same
    interface we want to announce on. Never actually sends a packet.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip: str = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def _safe_port(env: dict[str, str], key: str, default: int) -> int:
    """Parse a port from .env. Tolerant of blank/non-numeric values.

    The .env file is intentionally user-editable, so a typo in WEBUI_PORT
    must not crash the announcer (which would then restart on a systemd
    loop, spamming logs). On bad input we log and fall back to the default;
    the next refresh picks up the fix once the user corrects .env.
    """
    raw = env.get(key, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Env %s=%r is not a valid integer; using default %d", key, raw, default,
        )
        return default
    if value <= 0 or value > 65535:
        logger.warning(
            "Env %s=%r is outside the valid TCP/UDP port range; using default %d", key, raw, default,
        )
        return default
    return value


def _normalized_bind_address(env: dict[str, str]) -> str:
    return (env.get("BIND_ADDRESS") or "127.0.0.1").strip().lower()


def _direct_ports_lan_reachable(env: dict[str, str]) -> bool:
    """Direct host ports are only truthful when the services bind to the LAN.

    The default ODS posture keeps service ports on 127.0.0.1 and exposes only
    ods-proxy on the LAN. In that default, advertise the proxy hostnames but
    do not publish direct-port SRV records that would point LAN clients at
    unreachable endpoints.
    """
    bind = _normalized_bind_address(env)
    return bind not in {"", "127.0.0.1", "localhost", "::1"}


def _build_services(env: dict[str, str], device_name: str, ip: str) -> list[ServiceInfo]:
    """Build the ServiceInfo records to publish.

    Two flavors of record:

    1. Direct-port SRV records under `_http._tcp.local.` (the historical
       shape — backward-compatible with MCP clients and service-discovery
       tools). These point at the underlying service ports (3000, 3001,
       3002) for clients that want to bypass the proxy.

    2. Per-subdomain A records (`chat.<device>.local`, `auth.<device>.local`,
       `dashboard.<device>.local`, `hermes.<device>.local`) all pointing at
       the device's LAN IP on port 80 — the ods-proxy. The proxy
       routes by Host header. The A record is a side effect of registering
       a ServiceInfo whose `server` field is the hostname we want
       resolvable.

    Each service that isn't configured (port <= 0 or missing) is dropped
    from publication. Subdomain records are always published as long as
    ODS_DEVICE_NAME is valid — the proxy itself decides at routing
    time whether the upstream is healthy.
    """
    addresses = [socket.inet_aton(ip)]
    proxy_port = _safe_port(env, "ODS_PROXY_PORT", 80)

    infos: list[ServiceInfo] = []

    # ----- Direct-port SRV records (back-compat for service discovery) ----
    if _direct_ports_lan_reachable(env):
        direct: list[tuple[str, int, str, dict[str, str]]] = [
            # (suffix, port, label, extra_txt)
            ("dashboard",     _safe_port(env, "DASHBOARD_PORT", 3001),     "ODS Dashboard", {"path": "/"}),
            ("chat",          _safe_port(env, "WEBUI_PORT", 3000),         "ODS Chat",      {"path": "/"}),
            ("dashboard-api", _safe_port(env, "DASHBOARD_API_PORT", 3002), "ODS API",       {"path": "/health"}),
            # Announce unconditionally when direct ports are LAN-reachable:
            # the Hermes extension is opt-in via `ods enable hermes`, but
            # the failure mode when disabled is the same as any optional
            # service that is not running.
            ("hermes",        _safe_port(env, "HERMES_PORT", 9119),        "Hermes Agent",    {"path": "/api/health"}),
        ]
        for suffix, port, label, txt in direct:
            infos.append(ServiceInfo(
                type_="_http._tcp.local.",
                name=f"{device_name}-{suffix}._http._tcp.local.",
                addresses=addresses,
                port=port,
                properties={**txt, "label": label, "device": device_name, "kind": "direct"},
                server=f"{device_name}.local.",
            ))
    else:
        logger.info("Skipping direct-port SRV records because BIND_ADDRESS is loopback-only")

    # ----- Per-subdomain A records (proxy-routed) -------------------------
    # Each entry registers a `<sub>.<device>.local.` A record (or bare
    # `<device>.local.` for "root") by virtue of being the `server` of the
    # ServiceInfo. Port is the proxy's listen port; the proxy routes by Host
    # header to the right backend.
    subdomain_routes = (
        ("root",     "ODS Root Redirect (via proxy)"),
        ("chat",      "ODS Chat (via proxy)"),
        ("dashboard", "ODS Dashboard (via proxy)"),
        ("auth",      "ODS Auth (magic-link redemption)"),
        ("api",       "ODS API (admin)"),
        # hermes is announced unconditionally; the proxy 502s when the
        # upstream container isn't running, which is the right failure
        # mode for an optional extension.
        ("hermes",    "Hermes Agent (via proxy)"),
        # talk.<device>.local is the owner-card redemption target for the
        # mobile portal added in #1319. magic_link.py's _talk_url() points
        # phones at this hostname; ods-proxy's Caddy block routes it to
        # the dashboard container's /talk route. Without an A record here
        # the redirect lands the phone on an unresolvable host and the
        # owner experience stalls on a white screen.
        ("talk",      "ODS Talk (mobile owner portal)"),
    )
    for suffix, label in subdomain_routes:
        server = f"{device_name}.local." if suffix == "root" else f"{suffix}.{device_name}.local."
        infos.append(ServiceInfo(
            type_="_http._tcp.local.",
            name=f"{device_name}-proxy-{suffix}._http._tcp.local.",
            addresses=addresses,
            port=proxy_port,
            properties={"path": "/", "label": label, "device": device_name, "kind": "proxy"},
            server=server,
        ))

    return infos


class Announcer:
    """Manages the lifecycle of mDNS service publications.

    Holds onto the active Zeroconf handle and the currently-registered
    services so a config change can re-register cleanly.
    """

    def __init__(self) -> None:
        self.zc: Zeroconf | None = None
        self.registered: list[ServiceInfo] = []
        self.last_signature: tuple[str, str, str, int, int, int, int, int] | None = None

    def _config_signature(self, device_name: str, ip: str, env: dict[str, str]) -> tuple[str, str, str, int, int, int, int, int]:
        """Compact summary of what we'd publish — re-announce on change.

        Includes ODS_PROXY_PORT so that flipping the proxy to a non-
        standard port (rare, but supported) triggers re-announcement of
        the per-subdomain SRV records.
        """
        return (
            device_name,
            ip,
            _normalized_bind_address(env),
            _safe_port(env, "DASHBOARD_PORT", 3001),
            _safe_port(env, "WEBUI_PORT", 3000),
            _safe_port(env, "DASHBOARD_API_PORT", 3002),
            _safe_port(env, "HERMES_PORT", 9119),
            _safe_port(env, "ODS_PROXY_PORT", 80),
        )

    def refresh(self) -> None:
        env = _read_env()
        device_name = env.get("ODS_DEVICE_NAME", "ods") or "ods"
        if not _HOSTNAME_RE.match(device_name):
            logger.warning(
                "ODS_DEVICE_NAME %r is not hostname-safe; falling back to 'ods'",
                device_name,
            )
            device_name = "ods"
        ip = _get_local_ip()
        signature = self._config_signature(device_name, ip, env)
        if signature == self.last_signature and self.zc is not None:
            return

        if self.zc is not None:
            logger.info("Config changed — re-registering mDNS services")
            self._teardown()

        self.zc = Zeroconf(ip_version=IPVersion.V4Only)
        services = _build_services(env, device_name, ip)
        for info in services:
            self.zc.register_service(info)
            self.registered.append(info)
            logger.info(
                "Published %s -> %s:%d (server %s)",
                info.name, ip, info.port, info.server,
            )
        self.last_signature = signature

    def _teardown(self) -> None:
        if self.zc is None:
            return
        for info in self.registered:
            try:
                self.zc.unregister_service(info)
            except (OSError, RuntimeError) as exc:
                logger.warning("Failed to unregister %s: %s", info.name, exc)
        self.zc.close()
        self.zc = None
        self.registered = []

    def shutdown(self) -> None:
        logger.info("Shutting down mDNS announcer")
        self._teardown()


def main() -> int:
    if platform.system() == "Darwin":
        logger.info(
            "Darwin host detected — macOS mDNSResponder already announces hostname.local; "
            "this script is a no-op on macOS. Exiting cleanly."
        )
        return 0
    if platform.system() == "Windows":
        logger.info(
            "Windows host detected — mDNS support varies; this script is not yet supported on Windows. "
            "See docs for follow-up. Exiting cleanly."
        )
        return 0
    # Linux path: zeroconf is required from here on. Imported lazily so the
    # no-op exits above don't trip on a missing package.
    _import_zeroconf_or_die()

    if not ENV_FILE.is_file():
        logger.error("Env file not found at %s — cannot determine device config.", ENV_FILE)
        return 1

    announcer = Announcer()

    def _on_signal(signum: int, _frame: object) -> None:
        logger.info("Received signal %d", signum)
        announcer.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    logger.info("Starting ODS mDNS announcer (poll every %ds)", POLL_INTERVAL)
    while True:
        try:
            announcer.refresh()
        except (OSError, RuntimeError, ValueError) as exc:
            # ValueError covers any int-conversion failures we missed above —
            # the alternative is a crashloop that masks the real config bug.
            logger.exception("Refresh failed: %s", exc)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
