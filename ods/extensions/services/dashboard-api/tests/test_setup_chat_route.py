"""Setup chat follows persisted inference settings without restarting the API."""

from unittest.mock import AsyncMock, MagicMock
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

import config
import routers.setup as setup
from setup_chat_route import KEYS, resolve_chat_route


@pytest.fixture(autouse=True)
def isolated_route_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("base,path,expected", [
    ("http://host.docker.internal:13305", "/api/v1", "http://host.docker.internal:13305/api/v1"),
    ("http://llama-server:8080/v1/", "/v1/", "http://llama-server:8080/v1"),
    ("http://lemonade:13305/api/v1/", "api/v1/", "http://lemonade:13305/api/v1"),
    ("http://external:1234", "v1", "http://external:1234/v1"),
    ("http://proxy:8080/custom/v2/", "custom/v2/", "http://proxy:8080/custom/v2"),
    ("", "/v1", "http://stale-container:8080/v1"),
])
def test_chat_uses_live_url_path_and_model(test_client, monkeypatch, tmp_path, base, path, expected):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("OLLAMA_URL", "http://stale-container:8080")
    monkeypatch.setenv("LLM_API_BASE_PATH", "/stale")
    (tmp_path / ".env").write_text(f"LLM_API_URL={base}\nLLM_API_BASE_PATH={path}\nLLM_MODEL=current-model\n", encoding="utf-8")
    response = MagicMock(status=200)
    response.json = AsyncMock(return_value={"choices": [{"message": {"content": "ready"}}]})
    post = MagicMock()
    post.__aenter__ = AsyncMock(return_value=response)
    post.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = post
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(setup.aiohttp, "ClientSession", lambda **kwargs: context)

    result = test_client.post("/api/chat", json={"message":"hello", "system":"Be brief"}, headers=test_client.auth_headers)
    assert result.status_code == 200
    assert result.json() == {"success": True, "response":"ready"}
    assert session.post.call_args.args == (expected + "/chat/completions",)
    assert session.post.call_args.kwargs["json"]["model"] == "current-model"

    (tmp_path / ".env").write_text("LLM_API_URL=http://replacement:8080/v1\nLLM_MODEL=replaced\n", encoding="utf-8")
    result = test_client.post("/api/chat", json={"message":"again", "system":"Be brief"}, headers=test_client.auth_headers)
    assert result.status_code == 200
    assert session.post.call_args.args == ("http://replacement:8080/v1/chat/completions",)
    assert session.post.call_args.kwargs["json"]["model"] == "replaced"


@pytest.mark.parametrize("values,url,model,key", [
    ({"LLM_API_URL":"http://litellm:4000", "LITELLM_KEY":"gateway-key", "LLM_API_BASE_PATH":"/api/v1",
      "ODS_MODEL_SWITCHBOARD":"enabled"}, "http://litellm:4000/v1", "ods/current", "gateway-key"),
    ({"LLM_API_URL":"http://litellm:4000/v1", "LITELLM_KEY":"gateway-key", "LLM_BACKEND":"external"},
     "http://litellm:4000/v1", "ods/current", "gateway-key"),
    ({"LLM_API_URL":"http://litellm:4000", "LITELLM_MASTER_KEY":"master-key"},
     "http://litellm:4000/v1", "default", "master-key"),
    ({"LLM_API_URL":"http://remote:1234/v1", "LITELLM_KEY":"must-not-send",
      "OPEN_WEBUI_LLM_BASE_URL":"http://remote:1234/v1", "OPEN_WEBUI_LLM_API_KEY":"remote-key",
      "OPEN_WEBUI_TASK_MODEL":"remote-model"}, "http://remote:1234/v1", "remote-model", "remote-key"),
    ({"LLM_API_URL":"http://litellm.example:4000/v1", "LITELLM_KEY":"must-not-send",
      "OPEN_WEBUI_LLM_BASE_URL":"http://other:4000/v1", "OPEN_WEBUI_LLM_API_KEY":"also-private"},
     "http://litellm.example:4000/v1", "configured-model", ""),
    ({"LLM_API_URL":"http://remote:4000/v1", "OPEN_WEBUI_LLM_BASE_URL":"http://remote:4000/other/v1",
      "OPEN_WEBUI_LLM_API_KEY":"path-bound-key"}, "http://remote:4000/v1", "configured-model", ""),
    ({"LLM_API_URL":"http://lemonade:13305/api/v1", "LEMONADE_BASE_URL":"http://lemonade:13305",
      "LEMONADE_API_KEY":"lemonade-key", "LEMONADE_MODEL":"actual-loaded-id"},
     "http://lemonade:13305/api/v1", "actual-loaded-id", "lemonade-key"),
    ({"LLM_API_URL":"http://other:13305/api/v1", "LEMONADE_BASE_URL":"http://lemonade:13305",
      "LEMONADE_API_KEY":"must-not-send"}, "http://other:13305/api/v1", "configured-model", ""),
    ({"LLM_API_URL":"http://llama-server:8080", "GGUF_FILE":"actual-model.gguf"},
     "http://llama-server:8080/v1", "actual-model.gguf", ""),
    ({"LLM_API_URL":"http://host.docker.internal:13305", "LLM_BACKEND":"lemonade",
      "AMD_INFERENCE_PORT":"13305", "LEMONADE_BASE_URL":"", "LEMONADE_CONTAINER_BASE_URL":"",
      "LEMONADE_MODEL":"extra.imported-model.gguf", "GGUF_FILE":"imported-model.gguf", "LEMONADE_API_KEY":"native-key"},
     "http://host.docker.internal:13305/api/v1", "extra.imported-model.gguf", "native-key"),
    ({"LLM_API_URL":"http://llama-server:8080", "AMD_INFERENCE_RUNTIME":"lemonade", "GGUF_FILE":"imported.gguf"},
     "http://llama-server:8080/api/v1", "extra.imported.gguf", ""),
    ({"LLM_API_URL":"http://other-host:13305", "LLM_BACKEND":"lemonade", "AMD_INFERENCE_PORT":"13305",
      "LEMONADE_API_KEY":"must-not-send", "LEMONADE_MODEL":"must-not-assume"},
     "http://other-host:13305/api/v1", "configured-model", ""),
    ({"LLM_API_URL":"http://host.docker.internal:9999", "LLM_BACKEND":"lemonade", "AMD_INFERENCE_PORT":"13305",
      "LEMONADE_API_KEY":"must-not-send"}, "http://host.docker.internal:9999/api/v1", "configured-model", ""),
])
def test_credentials_and_model_are_bound_to_the_selected_backend(monkeypatch, tmp_path, values, url, model, key):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    values = {"LLM_MODEL":"configured-model", **values}
    (tmp_path/".env").write_text("".join(f"{k}={v}\n" for k,v in values.items()))
    actual_url, actual_model, headers = resolve_chat_route("http://fallback:8080")
    assert actual_url == url+"/chat/completions"
    assert actual_model == model
    assert headers.get("Authorization", "") == ("Bearer "+key if key else "")


@contextmanager
def backend(expected_key="", redirect=""):
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append((self.path, self.headers.get('Authorization', ''), payload))
            if redirect:
                self.send_response(307)
                self.send_header('Location', redirect)
                self.end_headers()
                return
            authorized = seen[-1][1] == ("Bearer "+expected_key if expected_key else "")
            self.send_response(200 if authorized else 401)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"choices":[{"message":{"content":"local fixture reply"}}]}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_chat_switches_actual_authenticated_http_backend_without_restart(test_client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    with backend('first-key') as (first, first_seen), backend('second-key') as (second, second_seen):
        for url, key, model in ((first, 'first-key', 'first-model'), (second, 'second-key', 'second-model')):
            (tmp_path/'.env').write_text(f'LLM_API_URL={url}\nOPEN_WEBUI_LLM_BASE_URL={url}\n'
                                        f'OPEN_WEBUI_LLM_API_KEY={key}\nLLM_MODEL={model}\n')
            result = test_client.post('/api/chat', json={'message':'hello','system':'brief'}, headers=test_client.auth_headers)
            assert result.status_code == 200, result.text
            assert result.json()['response'] == 'local fixture reply'
        for seen, key, model in ((first_seen,'first-key','first-model'),(second_seen,'second-key','second-model')):
            assert len(seen) == 1
            assert seen[0][:2] == ('/v1/chat/completions', 'Bearer '+key)
            assert seen[0][2]['model'] == model


def test_chat_never_follows_credentialed_redirects(test_client, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    with backend() as (destination, leaked), backend(redirect=destination+'/chat/completions') as (source, seen):
        (tmp_path/'.env').write_text(f'LLM_API_URL={source}\nOPEN_WEBUI_LLM_BASE_URL={source}\nOPEN_WEBUI_LLM_API_KEY=private-key\n')
        result = test_client.post('/api/chat', json={'message':'hello','system':'brief'}, headers=test_client.auth_headers)
        assert result.status_code == 307
        assert seen[0][1] == 'Bearer private-key'
        assert leaked == []


@pytest.mark.parametrize('url', ['file:///tmp/private', 'http://user:password@host/v1', 'http://host:0/v1', 'http://host/v1?key=secret'])
def test_invalid_routes_fail_without_network_or_secret_details(test_client, monkeypatch, tmp_path, url):
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    (tmp_path/'.env').write_text(f'LLM_API_URL={url}\n')
    session = MagicMock(side_effect=AssertionError('network must not be opened'))
    monkeypatch.setattr(setup.aiohttp, 'ClientSession', session)
    result = test_client.post('/api/chat', json={'message':'hello','system':'brief'}, headers=test_client.auth_headers)
    assert result.status_code == 503
    assert result.json()['detail'] == 'Invalid LLM route configuration'
    session.assert_not_called()
