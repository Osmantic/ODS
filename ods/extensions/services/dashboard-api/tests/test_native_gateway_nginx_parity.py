"""native_gateway must stay a faithful copy of dashboard/nginx.conf.

The native runtime has no nginx, so native_gateway reproduces its sign-in
gate, key injection, body limits, forwarding headers, cache rules and security
headers. This test parses nginx.conf and fails when either side changes alone.
A new nginx location fails until it is classified in LOCATION_SAMPLES.
"""

import re
from pathlib import Path

import native_gateway as gw

NGINX = Path(__file__).resolve().parents[2] / "dashboard" / "nginx.conf"
UNITS = {"": 1, "k": 1024, "m": 1024 * 1024}

# nginx location -> a request path it matches.
LOCATION_SAMPLES = {
    "= /_ods_dashboard_gate": None,  # internal auth subrequest; native checks in-process
    "@ods_sign_in_required": None,  # the 401 body is checked below
    "~ ^/api/auth/dashboard-session/(login|logout)$": "/api/auth/dashboard-session/login",
    "/": "/settings",
    "= /index.html": "/index.html",
    "^~ /api/talk/": "/api/talk/message",
    "~ ^/api/templates/[a-z0-9-]+/apply$": "/api/templates/coding/apply",
    "~ ^/api/models/.+/load$": "/api/models/qwen/load",
    "= /api/remote-provider/apply": "/api/remote-provider/apply",
    "= /api/pixel/chat/stream": "/api/pixel/chat/stream",
    '~ "^/api/pixel/images/[A-Za-z0-9_-]{1,128}(?:/img-[a-f0-9]{32})?$"': "/api/pixel/images/abc",
    "= /api/pixel/access-mode": "/api/pixel/access-mode",
    "= /api/models/recovery": "/api/models/recovery",
    "= /api/pixel/approval-terminal": "/api/pixel/approval-terminal",
    "/api/": "/api/status",
    "^~ /pixel-preview/": None,  # Pixel is Docker-only (native answers native_unavailable)
    "~ ^/api/extensions/[a-z0-9_-]+/enable$": "/api/extensions/n8n/enable",
    "= /api/webui/selection": "/api/webui/selection",
    "~ ^/api/extensions/[a-z0-9_-]+/(update|rollback)$": "/api/extensions/n8n/update",
    "= /pixel-mascot.js": "/pixel-mascot.js",
    "= /sw.js": "/sw.js",
    "= /theme-init.js": "/theme-init.js",
    "= /sw-register.js": "/sw-register.js",
    "~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$": "/assets/app.js",
}


def _source() -> str:
    return NGINX.read_text(encoding="utf-8")


def _locations() -> dict[str, str]:
    """Location spec -> its block body (nginx.conf nests no locations)."""
    blocks = {}
    for match in re.finditer(r"^    location (.+?) \{\n(.*?)^    \}", _source(), re.MULTILINE | re.DOTALL):
        blocks[match.group(1).strip()] = match.group(2)
    return blocks


def _body_limit(block: str) -> int:
    match = re.search(r"client_max_body_size (\d+)([km]?);", block)
    return int(match.group(1)) * UNITS[match.group(2)] if match else gw.DEFAULT_BODY_LIMIT


def test_every_nginx_location_is_classified():
    assert set(_locations()) == set(LOCATION_SAMPLES)


def test_api_locations_match_gate_key_and_limits():
    for spec, block in _locations().items():
        sample = LOCATION_SAMPLES[spec]
        if sample is None or "proxy_pass http://$dashboard_api_upstream" not in block:
            continue
        route = gw.api_route(sample)
        strips_key = 'proxy_set_header Authorization "";' in block
        gated = "auth_request /_ods_dashboard_gate;" in block
        injects_key = 'proxy_set_header Authorization "Bearer ${DASHBOARD_API_KEY}";' in block
        assert strips_key != injects_key, spec
        assert route.public == strips_key == (not gated), spec
        assert route.body_limit == _body_limit(block), spec
        assert route.cookie_scheme == ("X-Forwarded-Proto $ods_cookie_scheme" in block), spec
        assert "proxy_set_header X-Real-IP $remote_addr;" in block, spec
        assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in block, spec


def test_static_cache_rules_match():
    locations = _locations()
    stable = {spec.split(" ", 1)[1] for spec, block in locations.items()
              if spec.startswith("= /") and "expires -1;" in block and "proxy_pass" not in block}
    assert stable == gw.STABLE_NAMES
    asset_spec = next(spec for spec in locations if spec.startswith("~* "))
    assert asset_spec[3:] == gw.STATIC_ASSET_RE.pattern
    assert "expires 1y;" in locations[asset_spec]
    assert 'add_header Cache-Control "public, immutable";' in locations[asset_spec]
    assert "try_files $uri $uri/ /index.html;" in locations["/"]


def test_security_headers_match():
    server_headers = re.findall(r'^    add_header (\S+) "(.*)" always;$', _source(), re.MULTILINE)
    assert tuple(server_headers) == gw.SECURITY_HEADERS_TEMPLATE


def test_sign_in_answer_matches():
    block = _locations()["@ods_sign_in_required"]
    assert f"return 401 '{gw.SIGN_IN_REQUIRED_BODY}';" in block
    assert "add_header X-ODS-Sign-In required always;" in block
    assert "add_header Cache-Control no-store always;" in block


def test_trusted_local_inputs_match():
    source = _source()
    host_map = re.search(r'map \$http_host \$ods_loopback_host \{.*?"~\^(.*?)\$" 1;', source, re.DOTALL)
    assert host_map.group(1) == gw.LOOPBACK_HOST_RE.pattern
    forwarded = re.search(r'map "((?:\$http_\w+)+)" \$ods_forwarded', source).group(1)
    names = {name.replace("_", "-").encode() for name in re.findall(r"\$http_(\w+)", forwarded)}
    assert names == gw.FORWARDING_HEADERS
