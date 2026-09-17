"""Cross-site state-changing requests must not reach the routes.

CORS governs whether a page may read a response, not whether the request runs.
A cross-site form POST is a CORS "simple request": no preflight, the endpoint
executes, and the attacker page simply never sees the reply. The dashboard
container supplies the credential itself (nginx injects
`Authorization: Bearer $DASHBOARD_API_KEY` on every /api/ request), so no
cookie and no prior session is needed for such a request to be authenticated.
"""

from unittest.mock import patch


ATTACKER = "https://evil.invalid"


def _simple_form_post_headers(origin=ATTACKER, sec_fetch_site="cross-site"):
    """Exactly what a browser sends for an auto-submitted cross-site form.

    Content-Type is one of the three CORS-simple values, so no preflight is
    issued and there is nothing for the browser to block.
    """
    headers = {
        "Origin": origin,
        "Referer": f"{origin}/attack.html",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if sec_fetch_site is not None:
        headers["Sec-Fetch-Site"] = sec_fetch_site
    return headers


class TestCrossSiteStateChangeRejected:

    def test_cross_site_post_is_rejected_before_the_route_runs(
        self, test_client, monkeypatch
    ):
        """The canonical attack: no cookies, attacker Origin, real credential."""
        monkeypatch.setattr("routers.resources.SERVICES", {
            "ape": {"name": "APE", "container_name": "ods-ape"},
        })

        with patch("routers.resources._post_agent_json") as post_agent:
            resp = test_client.post(
                "/api/services/ape/restart",
                headers={**test_client.auth_headers, **_simple_form_post_headers()},
                content="a=1",
            )

        assert resp.status_code == 403
        assert "Cross-origin" in resp.json()["detail"], (
            f"must be rejected by the CSRF guard, not incidentally: {resp.json()}"
        )
        assert not test_client.cookies, "the attack needs no cookie at all"
        post_agent.assert_not_called(), "the host agent must never be reached"

    def test_cross_site_post_rejected_even_without_sec_fetch_site(
        self, test_client, monkeypatch
    ):
        """Origin alone is enough; Sec-Fetch-Site is belt and braces."""
        monkeypatch.setattr("routers.resources.SERVICES", {
            "ape": {"name": "APE", "container_name": "ods-ape"},
        })

        with patch("routers.resources._post_agent_json") as post_agent:
            resp = test_client.post(
                "/api/services/ape/restart",
                headers={
                    **test_client.auth_headers,
                    **_simple_form_post_headers(sec_fetch_site=None),
                },
                content="a=1",
            )

        assert resp.status_code == 403
        assert "Cross-origin" in resp.json()["detail"]
        post_agent.assert_not_called()

    def test_opaque_origin_is_rejected(self, test_client):
        """A sandboxed iframe posts Origin: null."""
        resp = test_client.post(
            "/api/auth/admin-session",
            headers={**test_client.auth_headers, **_simple_form_post_headers(origin="null")},
        )
        assert resp.status_code == 403
        assert "Cross-origin" in resp.json()["detail"], (
            f"must be the CSRF guard, not the route failing for another reason: {resp.json()}"
        )

    def test_get_is_not_blocked(self, test_client):
        """Only state-changing methods are gated; reads are unaffected."""
        resp = test_client.get(
            "/api/services/resources",
            headers={**test_client.auth_headers, "Origin": ATTACKER},
        )
        assert resp.status_code != 403


class TestLegitimateOriginsStillWork:
    """Every shape the dashboard is legitimately reached at must keep working."""

    def _restart(self, test_client, host, origin):
        with patch("routers.resources._post_agent_json", return_value={
            "status": "ok", "service_id": "ape", "action": "restart",
        }) as post_agent:
            resp = test_client.post(
                "/api/services/ape/restart",
                headers={
                    **test_client.auth_headers,
                    "Host": host,
                    "Origin": origin,
                    "Sec-Fetch-Site": "same-origin",
                },
            )
        return resp, post_agent

    def test_loopback_origin_allowed(self, test_client, monkeypatch):
        monkeypatch.setattr("routers.resources.SERVICES", {
            "ape": {"name": "APE", "container_name": "ods-ape"},
        })
        resp, post_agent = self._restart(
            test_client, "localhost:3001", "http://localhost:3001")
        assert resp.status_code == 200
        post_agent.assert_called_once()

    def test_lan_ip_origin_allowed(self, test_client, monkeypatch):
        """BIND_ADDRESS on a LAN IP: Origin and Host agree, so it passes."""
        monkeypatch.setattr("routers.resources.SERVICES", {
            "ape": {"name": "APE", "container_name": "ods-ape"},
        })
        resp, _ = self._restart(
            test_client, "192.168.1.50:3001", "http://192.168.1.50:3001")
        assert resp.status_code == 200

    def test_ods_proxy_hostname_allowed(self, test_client, monkeypatch):
        """Through ods-proxy the browser sees dashboard.<device>.local on :80.

        Caddy and nginx both preserve the browser's Host, so Origin and Host
        still agree even though this origin is in no allowlist.
        """
        monkeypatch.setattr("routers.resources.SERVICES", {
            "ape": {"name": "APE", "container_name": "ods-ape"},
        })
        resp, _ = self._restart(
            test_client, "dashboard.ods.local", "http://dashboard.ods.local")
        assert resp.status_code == 200

    def test_non_browser_client_without_origin_allowed(self, test_client, monkeypatch):
        """ods-cli, the host agent and curl send no Origin header."""
        monkeypatch.setattr("routers.resources.SERVICES", {
            "ape": {"name": "APE", "container_name": "ods-ape"},
        })
        with patch("routers.resources._post_agent_json", return_value={
            "status": "ok", "service_id": "ape", "action": "restart",
        }) as post_agent:
            resp = test_client.post(
                "/api/services/ape/restart",
                headers=test_client.auth_headers,
            )
        assert resp.status_code == 200
        post_agent.assert_called_once()
