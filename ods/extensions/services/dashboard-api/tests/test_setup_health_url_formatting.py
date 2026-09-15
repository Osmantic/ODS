import pytest

def test_health_url_formatting():
    def _format_probe_url(c: dict, s: str) -> str:
        h = c.get("health", "/")
        if not h.startswith("/"):
            h = f"/{h}"
        return f"http://{c.get('host', s)}:{c.get('port', 80)}{h}"

    assert _format_probe_url({"health": "health"}, "test") == "http://test:80/health"
    assert _format_probe_url({"health": "/health"}, "test") == "http://test:80/health"
    assert _format_probe_url({}, "test") == "http://test:80/"
