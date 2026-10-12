#!/usr/bin/env python3
"""Library n8n workflow templates must run on ODS's bundled n8n 2.x.

n8n 2.x blocks `$env` in expressions by default (N8N_BLOCK_ENV_ACCESS_IN_NODE),
and the n8n container sets none of the variables the templates once read, so
every service address has to be a literal compose-network URL. This checks
each template under extensions/library/workflows for:

- no `$env.` access,
- no `'http://' +` URL building (which produced `http://http://...`),
- every literal http(s) URL host is an ODS compose service or a placeholder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError as exc:
    print(f"[FAIL] Missing Python dependency: {exc}")
    raise SystemExit(1)


ROOT_DIR = Path(__file__).resolve().parents[2]
WORKFLOW_DIR = ROOT_DIR / "extensions" / "library" / "workflows"
MANIFEST_GLOBS = (
    "extensions/services/*/manifest.yaml",
    "extensions/library/services/*/manifest.yaml",
)
PLACEHOLDER_HOSTS = {"example.invalid"}
URL_RE = re.compile(r"https?://([A-Za-z0-9._-]+)")


def service_hosts() -> set[str]:
    hosts: set[str] = set()
    for pattern in MANIFEST_GLOBS:
        for path in ROOT_DIR.glob(pattern):
            service = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("service") or {}
            if service.get("default_host"):
                hosts.add(str(service["default_host"]))
    return hosts


def is_allowed(host: str, known: set[str]) -> bool:
    if host in known or host in PLACEHOLDER_HOSTS:
        return True
    return host.endswith(".example.invalid")


def main() -> int:
    known = service_hosts()
    templates = sorted(WORKFLOW_DIR.glob("*/*.json"))
    errors: list[str] = []
    if not templates:
        errors.append(f"no workflow templates found under {WORKFLOW_DIR}")

    for path in templates:
        rel = path.relative_to(ROOT_DIR)
        text = path.read_text(encoding="utf-8")
        json.loads(text)
        if "$env." in text:
            errors.append(f"{rel}: uses $env., which n8n 2.x blocks by default")
        if "'http://' +" in text or "http://http" in text:
            errors.append(f"{rel}: builds a URL with 'http://' + (double-scheme risk)")
        for host in sorted(set(URL_RE.findall(text))):
            if not is_allowed(host, known):
                errors.append(f"{rel}: URL host {host!r} is not an ODS compose service or example.invalid")

    if errors:
        print("[FAIL] library n8n workflows are n8n 2.x compatible")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"[PASS] {len(templates)} library n8n workflows use literal ODS service URLs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
