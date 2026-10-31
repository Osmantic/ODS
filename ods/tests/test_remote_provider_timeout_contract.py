"""Keep provider apply and rollback inside their outer transport budgets."""
import re
from pathlib import Path


ODS = Path(__file__).resolve().parents[1]


def read(path):
    return (ODS / path).read_text(encoding="utf-8")


def number(pattern, text):
    found = re.search(pattern, text)
    assert found, f"Missing timeout: {pattern}"
    return int(found[1])


def test_remote_apply_timeout_chain_and_authenticated_route_scope():
    nginx = read("extensions/services/dashboard/nginx.conf")
    api = read("extensions/services/dashboard-api/routers/remote_provider_status.py")
    ui = read("extensions/services/dashboard/src/pages/RemoteProvider.jsx")
    api_seconds = number(r"LIFECYCLE_APPLY_TIMEOUT_SECONDS\s*=\s*(\d+)", api)
    # A connection check records its route proof, which can activate the
    # route and runs its vision probe, inside the same lifecycle budget.
    for path, ui_timeout in (("apply", r"LIFECYCLE_TIMEOUT_MS\s*=\s*(\d+)"), ("probe", r"PROBE_TIMEOUT_MS\s*=\s*(\d+)")):
        route = re.search(rf"location = /api/remote-provider/{path} \{{(.*?)\n    \}}", nginx, re.S)
        assert route, f"Provider {path} must not inherit the generic 180-second proxy timeout"
        block = route[1]
        ui_seconds = number(ui_timeout, ui) / 1000
        read_seconds = number(r"proxy_read_timeout\s+(\d+)s;", block)
        send_seconds = number(r"proxy_send_timeout\s+(\d+)s;", block)
        assert api_seconds < ui_seconds < read_seconds <= 1900
        assert send_seconds == read_seconds
        assert "auth_request /_ods_dashboard_gate;" in block
        assert 'proxy_set_header Authorization "Bearer ${DASHBOARD_API_KEY}";' in block
        assert "proxy_set_header Host $http_host;" in block
        assert "proxy_set_header X-Forwarded-Proto $scheme;" in block
    generic = re.search(r"location /api/ \{(.*?)\n    \}", nginx, re.S)
    assert generic and number(r"proxy_read_timeout\s+(\d+)s;", generic[1]) == 180
