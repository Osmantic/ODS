#!/usr/bin/env bash
# Generate dream-proxy site fragments from extension manifests.
#
# Walks every extensions/services/*/manifest.yaml (plus data/user-extensions/*)
# and emits one Caddyfile fragment per service that declares a `proxy:` block.
# Fragments land in data/dream-proxy/sites.d/ which is bind-mounted into the
# dream-proxy container; the hand-written Caddyfile pulls them in via
# `import /etc/caddy/sites.d/*.caddy`.
#
# When DREAM_PROXY_EXCLUSIVE=true is set in the environment, also writes
# docker-compose.proxy-exclusive.yml — an overlay that strips host port
# bindings from every proxied service. resolve-compose-stack.sh picks this
# file up automatically when the flag is on, so the proxy becomes the sole
# host entry point.
#
# This script is idempotent and safe to re-run; stale fragments for services
# that no longer declare a proxy block are removed each run.

set -euo pipefail

SCRIPT_DIR="$(pwd)"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --script-dir)
            SCRIPT_DIR="${2:-$SCRIPT_DIR}"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

PYTHON_CMD="python3"
if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
    # shellcheck source=/dev/null
    . "$SCRIPT_DIR/lib/python-cmd.sh"
    PYTHON_CMD="$(ds_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if ! "$PYTHON_CMD" -c 'import yaml' >/dev/null 2>&1; then
    echo "ERROR: PyYAML is required by resolve-proxy-config.sh." >&2
    echo "       Active Python: $PYTHON_CMD ($(command -v "$PYTHON_CMD" 2>/dev/null || echo "$PYTHON_CMD"))" >&2
    exit 2
fi

"$PYTHON_CMD" - "$SCRIPT_DIR" <<'PY'
import os
import pathlib
import re
import sys

import yaml

script_dir = pathlib.Path(sys.argv[1])
ext_dirs = [script_dir / "extensions" / "services", script_dir / "data" / "user-extensions"]
sites_d = script_dir / "data" / "dream-proxy" / "sites.d"
sites_d.mkdir(parents=True, exist_ok=True)

exclusive = os.environ.get("DREAM_PROXY_EXCLUSIVE", "").strip().lower() in {"1", "true", "yes", "on"}

# Routing trust model. `user` (default) only emits fragments for services
# that opt in with `exposure: user` in the manifest; `developer` adds
# `developer-api` routes (raw OpenAI-shaped APIs, vector DB, etc.);
# `all` adds `internal` routes too and prints a loud warning.
_PROFILE_ORDER = {"user": 0, "developer": 1, "all": 2}
_EXPOSURE_LEVEL = {"user": 0, "developer-api": 1, "internal": 2}
_VALID_EXPOSURES = set(_EXPOSURE_LEVEL.keys())
_VALID_AUTH = {"service", "dream-session", "none"}

profile = (os.environ.get("DREAM_PROXY_PROFILE", "user") or "user").strip().lower()
if profile not in _PROFILE_ORDER:
    print(
        f"ERROR resolve-proxy-config: DREAM_PROXY_PROFILE='{profile}' is not one of {sorted(_PROFILE_ORDER)}",
        file=sys.stderr,
    )
    sys.exit(2)
profile_level = _PROFILE_ORDER[profile]

allow_unauth_user = os.environ.get("DREAM_PROXY_ALLOW_UNAUTHENTICATED_USER", "").strip().lower() in {"1", "true", "yes", "on"}

if profile == "all":
    print(
        "WARN resolve-proxy-config: DREAM_PROXY_PROFILE=all routes every manifest, including `internal`. "
        "Make sure the proxy is fronted by Tailscale/WireGuard or another auth layer.",
        file=sys.stderr,
    )

# Subdomain charset must match the audit script and dream-mdns DREAM_DEVICE_NAME
# pattern so the assembled `<sub>.<device>.local` is always a valid DNS label.
_SUB_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$")
RESERVED = {"chat", "dashboard", "auth", "api", "hermes", "talk", "root", "www"}

routes = []
seen_subdomains = set()

for base_dir in ext_dirs:
    if not base_dir.exists():
        continue
    for service_dir in sorted(base_dir.iterdir()):
        if not service_dir.is_dir():
            continue
        manifest_path = next(
            (service_dir / n for n in ("manifest.yaml", "manifest.yml", "manifest.json")
             if (service_dir / n).exists()),
            None,
        )
        if manifest_path is None:
            continue
        try:
            with manifest_path.open(encoding="utf-8") as fh:
                manifest = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as exc:
            # Mirror resolve-compose-stack.sh: warn and skip rather than crash
            # the whole resolver if one extension has a malformed manifest.
            print(f"WARN resolve-proxy-config: skipping {manifest_path}: {exc}", file=sys.stderr)
            continue
        if not isinstance(manifest, dict):
            continue
        if manifest.get("schema_version") != "dream.services.v1":
            continue

        proxy = manifest.get("proxy")
        if not isinstance(proxy, dict):
            continue

        service = manifest.get("service") or {}
        if not isinstance(service, dict):
            continue
        service_id = str(service.get("id") or service_dir.name)

        subdomain = str(proxy.get("subdomain") or "").strip().lower()
        if not subdomain or not _SUB_RE.match(subdomain) or subdomain in RESERVED:
            # Audit script catches these statically; here we just refuse to
            # emit a fragment for a broken entry so Caddy keeps loading.
            print(f"WARN resolve-proxy-config: {service_id}: invalid or reserved proxy.subdomain '{subdomain}', skipping", file=sys.stderr)
            continue
        # Duplicate / reserved checks run before the profile filter on purpose:
        # the audit should fire regardless of which profile is active, so a
        # mis-configured manifest can't hide behind `DREAM_PROXY_PROFILE=user`.
        if subdomain in seen_subdomains:
            print(f"WARN resolve-proxy-config: {service_id}: duplicate proxy.subdomain '{subdomain}', skipping", file=sys.stderr)
            continue
        seen_subdomains.add(subdomain)

        # Exposure classification. Missing field defaults to `developer-api`
        # so third-party extensions without the new schema don't get auto-
        # LAN'd when an admin flips to the default profile.
        exposure = str(proxy.get("exposure") or "developer-api").strip().lower()
        if exposure not in _VALID_EXPOSURES:
            print(f"WARN resolve-proxy-config: {service_id}: invalid proxy.exposure '{exposure}', skipping", file=sys.stderr)
            continue
        if _EXPOSURE_LEVEL[exposure] > profile_level:
            # Out of scope for the active profile — silent skip; the audit
            # script prints the full classification table separately.
            continue

        auth = str(proxy.get("auth") or "none").strip().lower()
        if auth not in _VALID_AUTH:
            print(f"WARN resolve-proxy-config: {service_id}: invalid proxy.auth '{auth}', skipping", file=sys.stderr)
            continue
        if exposure == "user" and auth == "none" and not allow_unauth_user:
            print(
                f"ERROR resolve-proxy-config: {service_id}: exposure=user with auth=none requires "
                f"DREAM_PROXY_ALLOW_UNAUTHENTICATED_USER=true. Refusing to emit an unauthenticated LAN route.",
                file=sys.stderr,
            )
            sys.exit(3)

        # Skip services whose compose stanza is disabled or missing. Mirrors
        # the gating in resolve-compose-stack.sh so we don't 502-route to a
        # container that won't be brought up.
        compose_rel = str(service.get("compose_file") or "")
        category = str(service.get("category") or "optional")
        if compose_rel:
            if (service_dir / f"{compose_rel}.disabled").exists():
                continue
            if not (service_dir / compose_rel).exists() and category != "core":
                continue
        elif category != "core":
            continue

        upstream_host = str(proxy.get("upstream_host") or service.get("default_host") or service_id)
        upstream_port_raw = proxy.get("upstream_port") or service.get("port")
        try:
            upstream_port = int(upstream_port_raw)
        except (TypeError, ValueError):
            print(f"WARN resolve-proxy-config: {service_id}: no usable upstream port, skipping", file=sys.stderr)
            continue
        if upstream_port <= 0:
            continue

        client_max_body = proxy.get("client_max_body")
        client_max_body = str(client_max_body) if client_max_body else None

        routes.append({
            "service_id": service_id,
            "subdomain": subdomain,
            "exposure": exposure,
            "auth": auth,
            "upstream_host": upstream_host,
            "upstream_port": upstream_port,
            "client_max_body": client_max_body,
        })


def _reverse_proxy_block(route, indent="\t"):
    lines = [
        f"{indent}reverse_proxy {route['upstream_host']}:{route['upstream_port']} {{",
        f"{indent}\theader_up X-Forwarded-Proto {{scheme}}",
        f"{indent}\theader_up X-Forwarded-Host  {{host}}",
    ]
    if route["auth"] == "dream-session":
        # Mirrors the marker hermes-proxy stamps on auth-gated requests so
        # backends can tell proxy traffic from direct hits.
        lines.append(f"{indent}\theader_up X-Dream-Auth-Proxy \"1\"")
    lines.append(f"{indent}}}")
    return lines


def fragment_for(route):
    lines = [
        "# Generated by scripts/resolve-proxy-config.sh - DO NOT EDIT BY HAND",
        f"# Service: {route['service_id']}",
        f"# Profile: {profile}  exposure={route['exposure']}  auth={route['auth']}",
        "http://" + route["subdomain"] + ".{$DREAM_DEVICE_NAME:dream}.local {",
    ]

    if route["auth"] == "dream-session":
        # Use the same shape as extensions/services/hermes-proxy/Caddyfile:
        # a literal-order `route { ... }` block so the health matcher and
        # forward_auth fire before reverse_proxy regardless of Caddy's
        # default directive order. Health stays public for Docker probes;
        # everything else gets bounced through dashboard-api's
        # /api/auth/verify-session.
        lines.append("\troute {")
        lines.append("\t\t@health path /health /healthz")
        lines.append("\t\thandle @health {")
        lines.append("\t\t\trespond \"ok\" 200")
        lines.append("\t\t}")
        lines.append("")
        lines.append("\t\tforward_auth {$DREAM_AUTH_UPSTREAM:dream-dashboard-api:3002} {")
        lines.append("\t\t\turi /api/auth/verify-session")
        lines.append("\t\t\tcopy_headers Cookie")
        # Strip WebSocket upgrade headers from the auth sub-request — see
        # the long comment block in hermes-proxy/Caddyfile for the gory
        # debugging story behind this. Short version: without these,
        # FastAPI 403s the auth check on any WS-upgrade request.
        lines.append("\t\t\theader_up -Connection")
        lines.append("\t\t\theader_up -Upgrade")
        lines.append("\t\t\theader_up -Sec-Websocket-Key")
        lines.append("\t\t\theader_up -Sec-Websocket-Version")
        lines.append("\t\t\theader_up -Sec-Websocket-Protocol")
        lines.append("\t\t\theader_up -Sec-Websocket-Extensions")
        lines.append("")
        lines.append("\t\t\t@denied status 401 403")
        lines.append("\t\t\thandle_response @denied {")
        # The leading `*` matcher is intentional — see hermes-proxy comment.
        lines.append("\t\t\t\tredir * /auth/required 303")
        lines.append("\t\t\t}")
        lines.append("\t\t}")
        lines.append("")
        lines.extend(_reverse_proxy_block(route, indent="\t\t"))
        lines.append("\t}")
    else:
        # auth: service (service handles its own auth) or auth: none
        # (developer-api opt-in only). Both get the bare reverse_proxy
        # shape — no extra Caddy-layer auth.
        lines.extend(_reverse_proxy_block(route, indent="\t"))

    if route["client_max_body"]:
        lines.append("\trequest_body {")
        lines.append(f"\t\tmax_size {route['client_max_body']}")
        lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


desired = set()
for route in routes:
    name = f"{route['service_id']}.caddy"
    desired.add(name)
    target = sites_d / name
    tmp = sites_d / f"{name}.tmp"
    tmp.write_text(fragment_for(route), encoding="utf-8")
    os.replace(tmp, target)

# Sweep stale fragments from previous runs.
for existing in sites_d.glob("*.caddy"):
    if existing.name not in desired:
        existing.unlink()

# Optional: write the host-port-strip overlay when DREAM_PROXY_EXCLUSIVE is on.
overlay = script_dir / "docker-compose.proxy-exclusive.yml"
if exclusive and routes:
    body = [
        "# Generated by scripts/resolve-proxy-config.sh when DREAM_PROXY_EXCLUSIVE=true.",
        "# Strips host port bindings from services routed through dream-proxy so",
        "# the proxy on :80 becomes the sole host-reachable entry point. The",
        "# containers stay reachable via the Docker bridge network so the proxy",
        "# can still talk to them.",
        "#",
        "# `!reset null` is the Compose 2.20+ override tag that REPLACES the",
        "# merged list rather than appending to it. Plain `ports: []` would be",
        "# concatenated with the base ports list and silently leave the host",
        "# bindings in place; the unit test for this overlay caught that gotcha.",
        "services:",
    ]
    for route in sorted(routes, key=lambda r: r["service_id"]):
        body.append(f"  {route['service_id']}:")
        body.append("    ports: !reset null")
    tmp = overlay.with_suffix(".yml.tmp")
    tmp.write_text("\n".join(body) + "\n", encoding="utf-8")
    os.replace(tmp, overlay)
elif overlay.exists():
    overlay.unlink()

print(
    f"resolve-proxy-config: profile={profile} wrote {len(routes)} fragment(s) to {sites_d}"
    + (f"; exclusive overlay -> {overlay}" if exclusive and routes else ""),
    file=sys.stderr,
)
PY
