"""Service base URLs from config/service-urls.json.

The map is the one place that names each service's base URL (the manifest
default_host and port on the compose network). Code reads a service's URL
through ``service_url`` rather than writing ``http://<service>:<port>``, so a
runtime that serves the same services elsewhere only rewrites the map.
"""

import json
import os
from functools import cache
from pathlib import Path


def service_urls_path() -> Path:
    configured = os.environ.get("ODS_SERVICE_URLS_FILE")
    if configured:
        return Path(configured)
    install_dir = os.environ.get("ODS_INSTALL_DIR", os.path.expanduser("~/ods"))
    return Path(install_dir) / "config" / "service-urls.json"


@cache
def _load(path: Path) -> dict[str, str]:
    urls = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(urls, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in urls.items()
    ):
        raise ValueError(f"{path} must map service ids to URL strings")
    return urls


def service_url(service_id: str) -> str:
    """Base URL (no trailing slash) of *service_id*; KeyError if unmapped."""
    return _load(service_urls_path())[service_id]
