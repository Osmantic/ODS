#!/usr/bin/env python3
"""Dream Server mDNS announcer.

Publishes the device on the local network as `<DREAM_DEVICE_NAME>.local`
(default `dream.local`) plus per-service `_http._tcp` records so any device
on the same LAN can find the dashboard and chat UI without knowing the IP.

When paired with dream-proxy on port 80, this makes the "open `dream.local`
from any phone" UX work: the device boots, joins WiFi, and starts announcing
itself within seconds.

Reads `DREAM_DEVICE_NAME` and the service ports from `.env`. Re-publishes
when the file changes (poll-based, 30s cadence) so renaming the device or
changing a port doesn't require a service restart.

Linux-first: relies on `avahi-daemon` being installed and running (already
standard on Ubuntu / Debian / Fedora / Arch desktop installs). macOS has
built-in mDNS via mDNSResponder and announces hostname.local automatically;
this script is a no-op on Darwin (logs and exits 0). Windows mDNS support
varies — see BRANDING.md / docs/MDNS.md for follow-up.

Run via:
  python3 /opt/dream-server/bin/dream-mdns.py
or via the dream-mdns.service systemd unit.
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
from typing import Any

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
    level=os.environ.get("DREAM_MDNS_LOG_LEVEL", "INFO"),
    format="%(asctime)s [dream-mdns] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

INSTALL_DIR = Path(os.environ.get("DREAM_INSTALL_DIR", "/opt/dream-server"))
ENV_FILE = INSTALL_DIR / ".env"
EXTENSIONS_DIR = INSTALL_DIR / "extensions" / "services"
USER_EXTENSIONS_DIR = INSTALL_DIR / "data" / "user-extensions"

# Subdomains owned by the hand-written core dream-proxy Caddyfile. Extensions
# must not claim these or the generated fragment would shadow the hand-written
# block at Caddy parse time; mirrors RESERVED_PROXY_SUBDOMAINS in
# scripts/audit-extensions.py.
_CORE_SUBDOMAINS = {"chat", "dashboard", "auth", "api", "hermes", "talk", "root", "www"}

# PyYAML is the canonical manifest parser everywhere else (resolver, audit).
# Loaded best-effort here so a Linux host with python3-zeroconf installed but
# missing python3-yaml still publishes the hardcoded core subdomains — the
# extension subdomains are gravy and degrade gracefully rather than crashing
# the mDNS announcer on import.
try:
    import yaml  # type: ignore
    _HAS_YAML = True
except ImportError:  # pragma: no cover — exercised only on minimal hosts
    yaml = None  # type: ignore
    _HAS_YAML = False


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


POLL_INTERVAL = _safe_positive_int_env("DREAM_MDNS_POLL_INTERVAL", 30)

# Hostname-safe pattern matches DREAM_DEVICE_NAME schema in .env.schema.json.
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

    The default Dream posture keeps service ports on 127.0.0.1 and exposes only
    dream-proxy on the LAN. In that default, advertise the proxy hostnames but
    do not publish direct-port SRV records that would point LAN clients at
    unreachable endpoints.
    """
    bind = _normalized_bind_address(env)
    return bind not in {"", "127.0.0.1", "localhost", "::1"}


def _load_extension_subdomains() -> list[tuple[str, str]]:
    """Walk extension manifests for `proxy.subdomain` declarations.

    Returns a sorted list of (subdomain, label) pairs for every manifest in
    `extensions/services/` and `data/user-extensions/` that declares a proxy
    block. Skips entries whose compose stanza is disabled or missing — same
    gating as scripts/resolve-proxy-config.sh, so the announcer never
    publishes a hostname the proxy doesn't have a route for.

    Best-effort: parse errors on a single manifest do not abort the scan, and
    the whole helper short-circuits to `[]` when PyYAML isn't importable.
    """
    if not _HAS_YAML:
        logger.info("PyYAML not available; skipping extension subdomain scan")
        return []
    results: list[tuple[str, str]] = []
    seen: set[str] = set()
    for base_dir in (EXTENSIONS_DIR, USER_EXTENSIONS_DIR):
        if not base_dir.is_dir():
            continue
        for service_dir in sorted(base_dir.iterdir()):
            if not service_dir.is_dir():
                continue
            manifest_path = next(
                (service_dir / n for n in ("manifest.yaml", "manifest.yml")
                 if (service_dir / n).exists()),
                None,
            )
            if manifest_path is None:
                continue
            try:
                with manifest_path.open(encoding="utf-8") as fh:
                    manifest = yaml.safe_load(fh)
            except (OSError, yaml.YAMLError) as exc:
                logger.warning("Skipping malformed manifest %s: %s", manifest_path, exc)
                continue
            if not isinstance(manifest, dict):
                continue
            if manifest.get("schema_version") != "dream.services.v1":
                continue
            proxy = manifest.get("proxy")
            if not isinstance(proxy, dict):
                continue
            subdomain = str(proxy.get("subdomain") or "").strip().lower()
            if not subdomain or subdomain in seen:
                continue
            service = manifest.get("service") or {}
            if not isinstance(service, dict):
                continue
            # Skip disabled/missing composes — mirror resolve-proxy-config.sh.
            compose_rel = str(service.get("compose_file") or "")
            category = str(service.get("category") or "optional")
            if compose_rel:
                if (service_dir / f"{compose_rel}.disabled").exists():
                    continue
                if not (service_dir / compose_rel).exists() and category != "core":
                    continue
            elif category != "core":
                continue
            service_name = str(service.get("name") or service.get("id") or service_dir.name)
            seen.add(subdomain)
            results.append((subdomain, f"{service_name} (via proxy)"))
    results.sort()
    return results


def _build_services(env: dict[str, str], device_name: str, ip: str) -> list[ServiceInfo]:
    """Build the ServiceInfo records to publish.

    Two flavors of record:

    1. Direct-port SRV records under `_http._tcp.local.` (the historical
       shape — backward-compatible with MCP clients and service-discovery
       tools). These point at the underlying service ports (3000, 3001,
       3002) for clients that want to bypass the proxy.

    2. Per-subdomain A records (`chat.<device>.local`, `auth.<device>.local`,
       `dashboard.<device>.local`, `hermes.<device>.local`) all pointing at
       the device's LAN IP on port 80 — the dream-proxy. The proxy
       routes by Host header. The A record is a side effect of registering
       a ServiceInfo whose `server` field is the hostname we want
       resolvable.

    Each service that isn't configured (port <= 0 or missing) is dropped
    from publication. Subdomain records are always published as long as
    DREAM_DEVICE_NAME is valid — the proxy itself decides at routing
    time whether the upstream is healthy.
    """
    addresses = [socket.inet_aton(ip)]
    proxy_port = _safe_port(env, "DREAM_PROXY_PORT", 80)

    infos: list[ServiceInfo] = []

    # ----- Direct-port SRV records (back-compat for service discovery) ----
    if _direct_ports_lan_reachable(env):
        direct: list[tuple[str, int, str, dict[str, str]]] = [
            # (suffix, port, label, extra_txt)
            ("dashboard",     _safe_port(env, "DASHBOARD_PORT", 3001),     "Dream Dashboard", {"path": "/"}),
            ("chat",          _safe_port(env, "WEBUI_PORT", 3000),         "Dream Chat",      {"path": "/"}),
            ("dashboard-api", _safe_port(env, "DASHBOARD_API_PORT", 3002), "Dream API",       {"path": "/health"}),
            # Announce unconditionally when direct ports are LAN-reachable:
            # the Hermes extension is opt-in via `dream enable hermes`, but
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
    #
    # Core routes (chat/dashboard/auth/api/hermes/talk/root) are owned by the
    # hand-written dream-proxy Caddyfile. Extension routes are sourced from
    # each extensions/services/*/manifest.yaml `proxy.subdomain` field — the
    # same data scripts/resolve-proxy-config.sh uses to emit Caddy fragments,
    # so announcement and routing stay in sync without a second source of
    # truth.
    core_subdomain_routes = (
        ("root",     "Dream Root Redirect (via proxy)"),
        ("chat",      "Dream Chat (via proxy)"),
        ("dashboard", "Dream Dashboard (via proxy)"),
        ("auth",      "Dream Auth (magic-link redemption)"),
        ("api",       "Dream API (admin)"),
        # hermes is announced unconditionally; the proxy 502s when the
        # upstream container isn't running, which is the right failure
        # mode for an optional extension.
        ("hermes",    "Hermes Agent (via proxy)"),
        # talk.<device>.local is the owner-card redemption target for the
        # mobile portal added in #1319. magic_link.py's _talk_url() points
        # phones at this hostname; dream-proxy's Caddy block routes it to
        # the dashboard container's /talk route. Without an A record here
        # the redirect lands the phone on an unresolvable host and the
        # owner experience stalls on a white screen.
        ("talk",      "Dream Talk (mobile owner portal)"),
    )
    extension_subdomain_routes = _load_extension_subdomains()
    # Core routes win on collision — a misbehaving extension manifest can't
    # hijack a hostname owned by the hand-written Caddyfile.
    seen = set(_CORE_SUBDOMAINS)
    subdomain_routes = list(core_subdomain_routes)
    for suffix, label in extension_subdomain_routes:
        if suffix in seen:
            continue
        seen.add(suffix)
        subdomain_routes.append((suffix, label))
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
        self.last_signature: tuple[Any, ...] | None = None

    def _config_signature(self, device_name: str, ip: str, env: dict[str, str]) -> tuple[Any, ...]:
        """Compact summary of what we'd publish — re-announce on change.

        Includes DREAM_PROXY_PORT so that flipping the proxy to a non-
        standard port (rare, but supported) triggers re-announcement of
        the per-subdomain SRV records. Includes the sorted set of
        extension subdomains so enabling/disabling an extension re-fires
        the announcement on the next poll without waiting for a service
        restart.
        """
        extension_subs = tuple(sub for sub, _ in _load_extension_subdomains())
        return (
            device_name,
            ip,
            _normalized_bind_address(env),
            _safe_port(env, "DASHBOARD_PORT", 3001),
            _safe_port(env, "WEBUI_PORT", 3000),
            _safe_port(env, "DASHBOARD_API_PORT", 3002),
            _safe_port(env, "HERMES_PORT", 9119),
            _safe_port(env, "DREAM_PROXY_PORT", 80),
            extension_subs,
        )

    def refresh(self) -> None:
        env = _read_env()
        device_name = env.get("DREAM_DEVICE_NAME", "dream") or "dream"
        if not _HOSTNAME_RE.match(device_name):
            logger.warning(
                "DREAM_DEVICE_NAME %r is not hostname-safe; falling back to 'dream'",
                device_name,
            )
            device_name = "dream"
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

    logger.info("Starting Dream mDNS announcer (poll every %ds)", POLL_INTERVAL)
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
