"""Unsupported telemetry must remain distinct from an offline model server."""
import json
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

import pytest
from test_host_agent import _mod


_WINDOWS_HOST = ('GPU_BACKEND=amd\nAMD_INFERENCE_LOCATION=host\nAMD_INFERENCE_RUNTIME=llama-server\n'
                 'AMD_INFERENCE_RUNTIME_MODE=windows-native-llama-server\n')


@pytest.mark.parametrize('system,env,expected', [
    # Containers publish their own /metrics; only a host-native server needs the agent.
    ('Linux', 'GPU_BACKEND=amd\nLLM_BACKEND=llama-server\n', 501),
    ('Darwin', 'GPU_BACKEND=apple\n', 501),
    ('Windows', 'GPU_BACKEND=nvidia\n', 501),
    ('Windows', _WINDOWS_HOST, 503),
    # The WSL Portal reaches its Windows-owned server through the model-router.
    ('Linux', 'GPU_BACKEND=cpu\nODS_HOST_LLM_TRANSPORT=model-router\n', 503),
])
def test_host_inference_capability_on_the_authenticated_wire(monkeypatch, tmp_path, system, env, expected):
    monkeypatch.setattr(_mod, 'AGENT_API_KEY', 'test-telemetry-capability')
    monkeypatch.setattr(_mod, 'INSTALL_DIR', tmp_path)
    (tmp_path / '.env').write_text(env, encoding='utf-8')
    monkeypatch.setattr(_mod.platform, 'system', lambda: system)
    monkeypatch.setattr(_mod._wsl_runtime, 'candidate', lambda values: system == 'Linux' and _mod._wsl_runtime.env_value(
        values, _mod._wsl_runtime.TRANSPORT_KEY)[1] == 'model-router')
    monkeypatch.setattr(_mod, '_host_llm_status', lambda: None)
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
