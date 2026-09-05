"""Cross-layer timeout contract for slow, CPU-only Pixel first turns."""

from __future__ import annotations

import re
from pathlib import Path


ODS_ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ODS_ROOT / relative).read_text(encoding="utf-8")


def _integer(pattern: str, text: str) -> int:
    match = re.search(pattern, text)
    assert match is not None, f"missing timeout contract: {pattern}"
    return int(match.group(1))


def test_pixel_timeout_chain_has_ordered_bounded_headroom() -> None:
    installer = _read("installers/lib/pixel-host-install.sh")
    ingress = _read("extensions/services/pixel-agent/host/pixel_ingress.mjs")
    edge = _read("extensions/services/pixel-edge/pixel_edge.py")
    dashboard_api = _read("extensions/services/dashboard-api/routers/pixel.py")
    nginx = _read("extensions/services/dashboard/nginx.conf")

    provider_seconds = _integer(
        r'updated_provider\["timeoutSeconds"\]\s*=\s*(\d+)', installer
    )
    agent_seconds = _integer(
        r'updated_defaults\["timeoutSeconds"\]\s*=\s*(\d+)', installer
    )
    diagnostic_seconds = _integer(
        r'updated_diagnostics\["stuckSessionAbortMs"\]\s*=\s*(\d+)', installer
    ) // 1000
    lock_seconds = _integer(
        r'write_lock\["maxHoldMs"\]\s*=\s*(\d+)', installer
    ) // 1000
    ingress_seconds = _integer(r"TOTAL_TIMEOUT_MS\s*=\s*(\d+)", ingress) // 1000
    edge_total_seconds = _integer(r"_TOTAL_TIMEOUT\s*=\s*(\d+)", edge)
    edge_idle_seconds = _integer(r"_SOCK_READ_TIMEOUT\s*=\s*(\d+)", edge)
    dashboard_seconds = _integer(
        r"_CHAT_STREAM_TIMEOUT_SECONDS\s*=\s*(\d+)(?:\.0)?", dashboard_api
    )
    nginx_read_seconds = _integer(
        r"location = /api/pixel/chat/stream \{[\s\S]*?proxy_read_timeout\s+(\d+)s;",
        nginx,
    )
    nginx_send_seconds = _integer(
        r"location = /api/pixel/chat/stream \{[\s\S]*?proxy_send_timeout\s+(\d+)s;",
        nginx,
    )

    assert provider_seconds == agent_seconds == 1800
    assert diagnostic_seconds == 1860
    assert lock_seconds == ingress_seconds == 1920
    assert edge_total_seconds == edge_idle_seconds == 1980
    assert dashboard_seconds == 2040
    assert nginx_read_seconds == nginx_send_seconds == 2100
    assert provider_seconds < diagnostic_seconds < ingress_seconds
    assert provider_seconds < lock_seconds < edge_total_seconds < dashboard_seconds < nginx_read_seconds


def test_ingress_does_not_inherit_fetch_body_idle_timeout() -> None:
    ingress = _read("extensions/services/pixel-agent/host/pixel_ingress.mjs")

    assert 'import http from "node:http"' in ingress
    assert 'import { Readable } from "node:stream"' in ingress
    assert "export function gatewayFetch" in ingress
    assert "http.request(" in ingress
    assert "body: Readable.toWeb(response)" in ingress
    assert "fetch: gatewayFetch" in ingress
    assert "fetch: globalThis.fetch" not in ingress
    assert "request.once(\"socket\"" in ingress
    assert "socket.once(\"connect\", clearConnectTimer)" in ingress
    assert "headerTimer" not in ingress
