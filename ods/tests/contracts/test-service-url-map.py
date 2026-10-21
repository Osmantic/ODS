#!/usr/bin/env python3
"""config/service-urls.json is the one service map, and it matches the stack.

Every entry must be the service's manifest default_host and port, and every
``http://<mapped host>:<port>`` default in docker-compose.base.yml must equal
the map entry, so compose and the map cannot drift apart. The bash reader
(lib/service-urls.sh) must return the same URLs and fail on unknown ids.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import yaml

ODS = Path(__file__).resolve().parents[2]
MAP_FILE = ODS / "config" / "service-urls.json"
SERVICES = ODS / "extensions" / "services"


def _service_map() -> dict[str, str]:
    return json.loads(MAP_FILE.read_text(encoding="utf-8"))


def _manifest_service(service_id: str) -> dict:
    manifest = yaml.safe_load((SERVICES / service_id / "manifest.yaml").read_text(encoding="utf-8"))
    return manifest["service"]


def test_map_entries_match_manifests() -> None:
    for service_id, url in _service_map().items():
        service = _manifest_service(service_id)
        assert service["id"] == service_id
        assert url == f"http://{service['default_host']}:{service['port']}", service_id


def test_compose_base_defaults_match_map() -> None:
    by_host = {
        _manifest_service(service_id)["default_host"]: url
        for service_id, url in _service_map().items()
    }
    compose = (ODS / "docker-compose.base.yml").read_text(encoding="utf-8")
    seen = set()
    for host, port in re.findall(r"http://([a-z][a-z0-9-]*):(\d+)", compose):
        if host in by_host:
            assert f"http://{host}:{port}" == by_host[host], f"compose uses {host}:{port}"
            seen.add(host)
    assert {"llama-server", "model-router", "token-spy", "tts", "whisper"} <= seen


# Resolve through PATH: on Windows, CreateProcess would find the System32 WSL bash.exe first.
BASH = shutil.which("bash") or "bash"


def _bash_service_url(service_id: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, "-c", 'source lib/service-urls.sh && ods_service_url "$1"', "bash", service_id],
        cwd=ODS,
        env={
            **{k: v for k, v in os.environ.items() if k != "ODS_SERVICE_URLS_FILE"},
            "SCRIPT_DIR": str(ODS),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def test_bash_reader_returns_map_entries() -> None:
    for service_id, url in _service_map().items():
        result = _bash_service_url(service_id)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == url


def test_bash_reader_fails_on_unknown_id() -> None:
    result = _bash_service_url("not-a-service")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "unknown service id" in result.stderr
