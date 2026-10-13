#!/usr/bin/env python3
"""Library n8n workflow templates must run on ODS's bundled n8n 2.x.

n8n 2.x blocks `$env` in expressions by default (N8N_BLOCK_ENV_ACCESS_IN_NODE),
and the n8n container sets none of the variables the templates once read, so
every service address has to be a literal compose-network URL. This checks
each template under extensions/library/workflows for:

- no `$env.` access,
- no `'http://' +` URL building (which produced `http://http://...`),
- every literal http(s) URL host is an ODS compose service or a placeholder,
- every literal `http://<service>:<port>` uses that service's manifest port
  (or an allowlisted extra HTTP port) and the service speaks HTTP,
- every Code node's jsCode parses (`node --check`, wrapped in an async
  function because n8n code may `return` at top level),
- every Webhook node using headerAuth names its credential.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
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
SERVICE_URL_RE = re.compile(r"http://([A-Za-z0-9._-]+):(\d+)")
# Extra HTTP ports a service's compose exposes besides its manifest port.
EXTRA_HTTP_PORTS = {
    ("text-generation-webui", 5001),  # --api-port, OpenAI-compatible API
}


def service_manifests() -> dict[str, dict]:
    """Map default_host -> manifest `service` block."""
    services: dict[str, dict] = {}
    for pattern in MANIFEST_GLOBS:
        for path in ROOT_DIR.glob(pattern):
            service = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("service") or {}
            if service.get("default_host"):
                services[str(service["default_host"])] = service
    return services


def url_errors(rel: Path, text: str, services: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    for host, port_text in sorted(set(SERVICE_URL_RE.findall(text))):
        service = services.get(host)
        if service is None:
            continue
        port = int(port_text)
        if (host, port) in EXTRA_HTTP_PORTS:
            continue
        if port != int(service.get("port") or 0):
            errors.append(f"{rel}: {host}:{port} does not match manifest port {service.get('port')}")
        elif not str(service.get("health") or "").strip():
            errors.append(f"{rel}: {host}:{port} has no HTTP health endpoint (not an HTTP service?)")
    return errors


def webhook_errors(rel: Path, workflow: dict) -> list[str]:
    errors: list[str] = []
    for node in workflow.get("nodes", []):
        if node.get("type") != "n8n-nodes-base.webhook":
            continue
        if (node.get("parameters") or {}).get("authentication") != "headerAuth":
            continue
        cred = ((node.get("credentials") or {}).get("httpHeaderAuth") or {}).get("name")
        if not cred:
            errors.append(f"{rel}: webhook {node.get('name')!r} uses headerAuth without a credential name")
    return errors


def jscode_errors(rel: Path, workflow: dict, node_bin: str) -> list[str]:
    errors: list[str] = []
    for node in workflow.get("nodes", []):
        code = (node.get("parameters") or {}).get("jsCode")
        if not code:
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write("async function __n8n() {\n" + code + "\n}\n")
            js_path = Path(handle.name)
        result = subprocess.run([node_bin, "--check", str(js_path)], capture_output=True, text=True, check=False)
        js_path.unlink()
        if result.returncode != 0:
            detail = next((line for line in result.stderr.splitlines() if "Error" in line), result.stderr.strip())
            errors.append(f"{rel}: Code node {node.get('name')!r} does not parse: {detail}")
    return errors


def is_allowed(host: str, known: set[str]) -> bool:
    if host in known or host in PLACEHOLDER_HOSTS:
        return True
    return host.endswith(".example.invalid")


def main() -> int:
    services = service_manifests()
    known = set(services)
    node_bin = shutil.which("node")
    if node_bin is None:
        print("[SKIP] node not on PATH: Code node jsCode syntax check skipped")
    templates = sorted(WORKFLOW_DIR.glob("*/*.json"))
    errors: list[str] = []
    if not templates:
        errors.append(f"no workflow templates found under {WORKFLOW_DIR}")

    for path in templates:
        rel = path.relative_to(ROOT_DIR)
        text = path.read_text(encoding="utf-8")
        workflow = json.loads(text)
        if "$env." in text:
            errors.append(f"{rel}: uses $env., which n8n 2.x blocks by default")
        if "'http://' +" in text or "http://http" in text:
            errors.append(f"{rel}: builds a URL with 'http://' + (double-scheme risk)")
        for host in sorted(set(URL_RE.findall(text))):
            if not is_allowed(host, known):
                errors.append(f"{rel}: URL host {host!r} is not an ODS compose service or example.invalid")
        errors.extend(url_errors(rel, text, services))
        errors.extend(webhook_errors(rel, workflow))
        if node_bin is not None:
            errors.extend(jscode_errors(rel, workflow, node_bin))

    if errors:
        print("[FAIL] library n8n workflows are n8n 2.x compatible")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"[PASS] {len(templates)} library n8n workflows use literal ODS service URLs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
