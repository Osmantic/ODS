"""Exercise Perplexica bootstrap against its actual configuration API shape."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "extensions/services/perplexica/sync-model-config.js"
KEY = "Xenova/all-MiniLM-L6-v2"
CHAT = {"id": "chat", "type": "openai", "chatModels": [], "config": {}}
CPU = {"id": "cpu", "type": "transformers", "embeddingModels": [{"key": KEY, "name": "MiniLM"}]}
FIELDS = ("defaultEmbeddingProvider", "defaultEmbeddingModel")


@contextmanager
def config_server(preferences, providers=None, corrupt=None, writes=None, hydrate_embeddings=None):
    state = copy.deepcopy({"preferences": preferences, "modelProviders": providers or [CHAT, CPU]})
    wrote_preferences = False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            values = copy.deepcopy(state)
            if hydrate_embeddings:
                for provider in values["modelProviders"]:
                    if provider.get("type") == "transformers":
                        provider["embeddingModels"].extend(copy.deepcopy(hydrate_embeddings))
            if wrote_preferences and corrupt:
                values["preferences"][corrupt] = "unexpected"
            self.respond({"values": values})

        def do_POST(self):
            nonlocal wrote_preferences
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if writes is not None:
                writes.append(copy.deepcopy(payload))
            parts = payload["key"].split(".")
            target = state
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            target[parts[-1]] = payload["value"]
            wrote_preferences |= payload["key"] == "preferences"
            self.respond({})

        def respond(self, value):
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state, f"http://127.0.0.1:{server.server_port}/api/config"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run_sync(url, model=None):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required")
    env = {**os.environ, "ODS_MODEL_SWITCHBOARD": "enabled" if model is None else "disabled",
           "ODS_MODE": "local", "GGUF_FILE": "", "LLM_MODEL": model or "fixture-model",
           "OPENAI_BASE_URL": "http://local/v1",
           "OPENAI_API_KEY": "fixture-private-key", "PERPLEXICA_CONFIG_URL": url}
    result = subprocess.run([node, str(SCRIPT)], capture_output=True, text=True, env=env, timeout=10)
    assert "fixture-private-key" not in result.stdout + result.stderr
    return result


@pytest.mark.parametrize("preferences", [{}, dict.fromkeys(FIELDS), dict.fromkeys(FIELDS, "")])
def test_unselected_defaults_use_advertised_local_model_and_do_not_add_providers(preferences):
    with config_server(preferences) as (state, url):
        for _ in range(2):
            result = run_sync(url)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "ods/current"
            assert [state["preferences"][key] for key in FIELDS] == ["cpu", KEY]
            assert state["preferences"]["defaultChatModel"] == "ods/current"
            assert len(state["modelProviders"]) == 2
            assert state["modelProviders"][1] == CPU


@pytest.mark.parametrize("preferences", [dict(zip(FIELDS, ["owner", "chosen"])),
                                       {FIELDS[0]: "owner"}, {FIELDS[1]: "chosen"}])
def test_preserves_owner_and_incomplete_selections(preferences):
    with config_server(preferences) as (state, url):
        result = run_sync(url)
        assert result.returncode == 0, result.stderr
        assert {key: state["preferences"][key] for key in FIELDS if key in state["preferences"]} == preferences


@pytest.mark.parametrize("providers", [[CHAT], [CHAT, {**CPU, "embeddingModels": []}],
                                      [CHAT, {**CPU, "embeddingModels": {KEY: {}}}]])
def test_does_not_invent_unadvertised_provider_or_wrong_schema(providers):
    with config_server({}, providers) as (state, url):
        result = run_sync(url)
        assert result.returncode == 0, result.stderr
        assert not any(key in state["preferences"] for key in FIELDS)
        assert "local embedding default unavailable" in result.stderr


def test_selects_provider_that_actually_advertises_the_model():
    with config_server({}, [CHAT, {**CPU, "id": "empty", "embeddingModels": []}, CPU]) as (state, url):
        assert run_sync(url).returncode == 0
        assert state["preferences"][FIELDS[0]] == "cpu"


def test_embedding_setup_does_not_rewrite_hydrated_catalog_or_repeat_writes():
    configured_chat = {**CHAT, "chatModels": [{"key": "ods/current", "name": "ods/current"}],
                       "config": {"baseURL": "http://local/v1", "apiKey": "fixture-private-key"}}
    advertised_cpu = {**CPU, "embeddingModels": CPU["embeddingModels"] * 3}
    providers = [configured_chat, advertised_cpu]
    preferences = {"defaultChatProvider": "chat", "defaultChatModel": "ods/current"}
    writes = []
    with config_server(preferences, providers, writes=writes) as (state, url):
        result = run_sync(url)
        assert result.returncode == 0, result.stderr
        assert [item["key"] for item in writes] == ["preferences"]
        assert state["modelProviders"] == providers
        assert [state["preferences"][key] for key in FIELDS] == ["cpu", KEY]
        writes.clear()
        result = run_sync(url)
        assert result.returncode == 0, result.stderr
        assert writes == []
        assert state["modelProviders"] == providers


def test_route_updates_do_not_persist_get_hydrated_embedding_catalog():
    # Pinned Vane GET appends built-ins to the persisted provider. The live
    # retained Windows config had 27 stored entries but returned 30 on GET.
    built_ins = [{"key": f"builtin-{i}", "name": f"Built-in {i}"} for i in range(3)]
    stored_cpu = {**CPU, "embeddingModels": built_ins * 9}
    owner_provider = {"id": "owner", "type": "custom", "embeddingModels": [{"key": "owner-choice"}]}
    providers = [copy.deepcopy(CHAT), stored_cpu, owner_provider]
    preferences = {"defaultEmbeddingProvider": "owner", "defaultEmbeddingModel": "owner-choice"}
    writes = []
    with config_server(preferences, providers, writes=writes,
                       hydrate_embeddings=built_ins) as (state, url):
        for model in ("ods/current", "ods/next", "ods/next"):
            result = run_sync(url, model)
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == model
            assert state["modelProviders"][1] == stored_cpu
            assert state["modelProviders"][2] == owner_provider
            assert state["preferences"]["defaultEmbeddingProvider"] == "owner"
            assert state["preferences"]["defaultEmbeddingModel"] == "owner-choice"
            assert not any(write["key"] == "modelProviders" for write in writes)
            assert all(write["key"].startswith("modelProviders.0.")
                       for write in writes if write["key"].startswith("modelProviders"))
            writes.clear()
        assert writes == []


@pytest.mark.parametrize("preferences", [{}, dict(zip(FIELDS, ["owner", "chosen"]))])
@pytest.mark.parametrize("field", FIELDS)
def test_rejects_changed_new_or_preserved_preferences_on_readback(preferences, field):
    with config_server(preferences, corrupt=field) as (_state, url):
        result = run_sync(url)
        assert result.returncode == 1
        assert "embedding preferences did not persist" in result.stderr
