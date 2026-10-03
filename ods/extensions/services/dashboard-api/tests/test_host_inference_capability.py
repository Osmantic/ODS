"""Unsupported telemetry must remain distinct from an offline model server."""
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest
from test_host_agent import _mod


@pytest.mark.parametrize('system,expected', [('Linux', 501), ('Darwin', 501), ('Windows', 503)])
def test_host_inference_capability_on_the_authenticated_wire(monkeypatch, system, expected):
    monkeypatch.setattr(_mod, 'AGENT_API_KEY', 'test-telemetry-capability')
    monkeypatch.setattr(_mod.platform, 'system', lambda: system)
    monkeypatch.setattr(_mod, '_windows_llm_status', lambda: None)
    server = HTTPServer(('127.0.0.1', 0), _mod.AgentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_address[1]}/v1/llm/status'
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(url, timeout=2)
        assert denied.value.code == 401
        request = urllib.request.Request(url, headers={'Authorization': 'Bearer test-telemetry-capability'})
        with pytest.raises(urllib.error.HTTPError) as result:
            urllib.request.urlopen(request, timeout=2)
        assert result.value.code == expected
        detail = json.loads(result.value.read())['error']
        assert ('unsupported' if expected == 501 else 'unavailable') in detail
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
