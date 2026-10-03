"""Windows host telemetry credentials through actual owner/runtime HTTP boundaries."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import test_host_agent as fixtures

host = fixtures._mod


@pytest.mark.parametrize("explicit,gateway,runtime_key,status,stats_fail", [
    ("owner-key", "generated-key", "owner-key", 200, False),
    ("", "gateway-key", "gateway-key", 200, False),
    ("wrong-owner-key", "gateway-key", "gateway-key", 503, False),
    ("owner-key", "generated-key", "owner-key", 200, True),
    ("", "", "", 200, False),
])
def test_windows_llm_status_wire_uses_runtime_credential(
    tmp_path, monkeypatch, explicit, gateway, runtime_key, status, stats_fail,
):
    requests = []

    class Runtime(BaseHTTPRequestHandler):
        def do_GET(self):
            authorization = self.headers.get("Authorization")
            requests.append((self.path, authorization))
            denied = bool(runtime_key and authorization != f"Bearer {runtime_key}")
            is_stats = self.path.endswith("/stats")
            code = 401 if denied else (503 if is_stats and stats_fail else 200)
            payload = ({"error": "unavailable"} if code != 200 else
                       {"output_tokens": 7, "tokens_per_second": 42} if is_stats else
                       {"status": "ok", "version": "10.0.0", "model_loaded": r"C:\private\model.gguf"})
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    runtime = ThreadingHTTPServer(("127.0.0.1", 0), Runtime)
    agent = ThreadingHTTPServer(("127.0.0.1", 0), host.AgentHandler)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (runtime, agent)]
    for thread in threads:
        thread.start()
    monkeypatch.setattr(host.platform, "system", lambda: "Windows")
    monkeypatch.setattr(host, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(host, "AGENT_API_KEY", "owner-agent-key")
    monkeypatch.setattr(host, "_windows_llm_status_cache", (0.0, None))
    env = tmp_path / ".env"
    env.write_text(
        f'AMD_INFERENCE_PORT={runtime.server_port}\nLEMONADE_API_KEY="{explicit}"\n'
        f'LITELLM_LEMONADE_API_KEY="{gateway}"\n', encoding="utf-8",
    )
    url = f"http://127.0.0.1:{agent.server_port}/v1/llm/status"
    try:
        assert httpx.get(url).status_code == 401
        assert requests == []
        response = httpx.get(url, headers={"Authorization": "Bearer owner-agent-key"})
        assert response.status_code == status, response.text
        chosen = explicit or gateway
        assert all(auth == (f"Bearer {chosen}" if chosen else None) for _, auth in requests)
        assert requests[0][0] == "/api/v1/health"
        if status == 200:
            payload = response.json()
            assert payload["schema_version"] == "ods.host-llm-status.v1"
            assert payload["health"]["model_loaded"] == "model.gguf"
            if stats_fail:
                assert payload["stats"] is None
            else:
                assert payload["stats"]["output_tokens"] == 7
            assert any(path.endswith("/stats") for path, _ in requests)
        for secret in ("private", "owner-key", "gateway-key", "generated-key"):
            assert secret not in response.text

        if status == 503:
            # An explicit wrong credential must not fall back to another key.
            assert all(path.endswith("/health") for path, _ in requests)
            env.write_text(f"AMD_INFERENCE_PORT={runtime.server_port}\nLEMONADE_API_KEY={runtime_key}\n", encoding="utf-8")
            monkeypatch.setattr(host, "_windows_llm_status_cache", (0.0, None))
            assert httpx.get(url, headers={"Authorization": "Bearer owner-agent-key"}).status_code == 200
    finally:
        for server in (agent, runtime):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)
