"""Exercise retained Windows Perplexica setup against Vane's hydrated API."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "installers/windows/lib/env-generator.ps1"
EMBEDDING = {"key": "Xenova/all-MiniLM-L6-v2", "name": "MiniLM"}


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell installer")
def test_retained_windows_config_does_not_persist_hydrated_models():
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell is required")

    state = {
        "setupComplete": True,
        "modelProviders": [
            {"id": "chat", "type": "openai", "chatModels": [],
             "config": {"baseURL": "http://old/v1", "apiKey": "old-key"}},
            {"id": "cpu", "type": "transformers", "embeddingModels": [EMBEDDING] * 9},
            {"id": "owner", "type": "custom", "embeddingModels": [{"key": "chosen"}]},
        ],
        "preferences": {"defaultChatProvider": "chat", "defaultChatModel": "old",
                        "defaultEmbeddingProvider": "owner", "defaultEmbeddingModel": "chosen",
                        "theme": "dark"},
    }
    original_non_chat = copy.deepcopy(state["modelProviders"][1:])
    writes = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            values = copy.deepcopy(state)
            values["modelProviders"][1]["embeddingModels"].append(EMBEDDING)
            self.respond({"values": values})

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            writes.append(payload)
            if self.path == "/api/config/setup-complete":
                state["setupComplete"] = True
            else:
                target = state
                parts = payload["key"].split(".")
                for part in parts[:-1]:
                    target = target[int(part)] if isinstance(target, list) else target[part]
                target[parts[-1]] = payload["value"]
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
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        quoted_script = str(SCRIPT).replace("'", "''")
        for model in ("ods/current", "ods/next"):
            command = (f". '{quoted_script}'; "
                       f"if (-not (Set-PerplexicaConfig -PerplexicaPort {server.server_port} "
                       f"-LlmModel '{model}' -LlmBaseUrl 'http://new/v1' "
                       "-ApiKey 'fixture-key')) { exit 1 }")
            result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                     "-Command", command],
                                    capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, result.stderr
            assert state["modelProviders"][1:] == original_non_chat
            assert state["preferences"]["defaultEmbeddingProvider"] == "owner"
            assert state["preferences"]["defaultEmbeddingModel"] == "chosen"
            assert state["preferences"]["theme"] == "dark"
            assert state["preferences"]["defaultChatModel"] == model
            assert not any(write.get("key") == "modelProviders" for write in writes)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
