import hashlib
import http.server
import json
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import portal_outcome_assistant_evidence as assistant_evidence
import portal_outcome_assistant_system as assistant_system
from tests.test_control_server import control_module


MODEL = "DeepSeek-V4-Flash-0731"
MESSAGE = "Inspect the isolated fixture and return only verified local state."


def unused_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    return port


class Backend(http.server.ThreadingHTTPServer):
    allow_reuse_address = False


class BackendHandler(http.server.BaseHTTPRequestHandler):
    requests = 0

    def do_POST(self):
        type(self).requests += 1
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        if self.path != "/v1/chat/completions" or body.get("model") != MODEL or body.get("stream") is not True:
            self.send_response(400); self.end_headers(); return
        payload = (
            'data: {"choices":[{"delta":{"content":"VERIFIED"},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":120,"completion_tokens":30}}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format, *_args):
        return


class ProxyCallingRunner:
    def __init__(self, port, *, reported_input=120, tool_name="pixel_limb_status"):
        self.port = port
        self.reported_input = reported_input
        self.tool_name = tool_name

    def __call__(self, _command, _root, _timeout_seconds, _environment):
        body = json.dumps({
            "model": MODEL, "stream": True, "max_tokens": 64,
            "messages": [{"role": "user", "content": MESSAGE}],
            "tools": [{
                "type": "function", "function": {
                    "name": self.tool_name, "description": "fixture", "parameters": {"type": "object"},
                },
            }],
        }).encode()
        connection = __import__("http.client").client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("POST", "/v1/chat/completions", body, {"content-type": "application/json"})
            response = connection.getresponse()
            response.read()
        finally:
            connection.close()
        if response.status != 200:
            return 1, b"proxy request denied"
        output = {
            "status": "ok", "result": {
                "payloads": [{"text": "The isolated fixture was verified."}],
                "meta": {
                    "durationMs": 250,
                    "agentMeta": {
                        "provider": "local", "model": MODEL,
                        "usage": {"input": self.reported_input, "output": 30, "total": self.reported_input + 30},
                        "promptTokens": 100,
                    },
                    "toolSummary": {"calls": 0, "failures": 0, "tools": []},
                },
            },
        }
        return 0, json.dumps(output).encode()


class PortalOutcomeAssistantSystemTests(unittest.TestCase):
    def setUp(self):
        self.node = shutil.which("node")
        if self.node is None:
            self.skipTest("Node.js is unavailable")
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.backend = Backend(("127.0.0.1", 0), BackendHandler)
        BackendHandler.requests = 0
        self.thread = threading.Thread(target=self.backend.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.backend.shutdown(); self.backend.server_close(); self.thread.join(timeout=2)
        self.temp.cleanup()

    def fixture(self, name):
        run = self.base / name
        run.mkdir(mode=0o700)
        home = run / "openclaw-home"; home.mkdir(mode=0o700)
        control_module.atomic_json(home / "openclaw.json", {"gateway": {"mode": "local"}}, 0o600)
        binary = run / "openclaw"
        binary.write_text("fixture", encoding="utf-8"); binary.chmod(0o700)
        onboarding = run / "onboarding.json"
        configured = control_module.merge_onboarding({}, control_module.default_onboarding())
        configured.update({
            "openclawBin": str(binary), "openclawHome": str(home), "agentId": "pixel",
            "modelProvider": "local", "modelId": MODEL,
        })
        control_module.atomic_json(onboarding, configured, 0o600)
        port = unused_port()
        config = {
            "schemaVersion": 1, "jobId": "work-1786690000000-a1b2c3d4e5f6",
            "claimId": "workclaim-1786690000000-a1b2c3d4e5f6", "planSha256": "1" * 64,
            "provider": "vllm", "modelId": MODEL, "contextWindow": 32768, "supportsVision": False,
            "backendOrigin": f"http://127.0.0.1:{self.backend.server_address[1]}",
            "listenHost": "127.0.0.1", "listenPort": port, "allowedClientIpv4": "127.0.0.1",
            "allowedTools": ["pixel_limb_status"],
            "receiptPath": "/run/pixel-work-output/model-proxy-receipt.json",
            "qualification": {
                "qualificationId": "modelqual-1786689900000-a1b2c3d4e5f6",
                "receiptSha256": "2" * 64, "casesSha256": "3" * 64, "evaluatorSha256": "4" * 64,
                "profile": "assistant", "maxContextTokens": 32768, "maxOutputTokens": 64, "exactUsage": True,
            },
            "inference": {
                "enforcement": "exact-request-boundary-v1", "wireApi": "openai-chat-completions",
                "temperaturePermille": 700, "topPPermille": 950, "topK": 40, "minPPermille": 50,
                "repeatPenaltyPermille": 1100, "seed": 42, "reasoningEffort": "backend-default",
                "reasoningVisibility": "hidden", "stream": True, "maxOutputTokens": 64,
                "toolEncoding": "function",
            },
            "budgets": {
                "maxRuntimeSeconds": 30, "maxModelRequests": 4, "maxInputTokens": 4096,
                "maxOutputTokens": 256, "maxNetworkBytes": 1048576, "maxRequestBytes": 131072,
                "maxResponseBytes": 131072, "maxRequestSeconds": 5,
            },
        }
        proxy_config = run / "proxy.json"
        proxy_config.write_text(json.dumps(config) + "\n", encoding="utf-8"); proxy_config.chmod(0o600)
        return run, onboarding, proxy_config, run / "proxy-receipt.json", port

    def execute(self, name, runner):
        run, onboarding, proxy_config, receipt, port = self.fixture(name)
        payload = MESSAGE.encode()
        result = assistant_system.execute_assistant_turn(
            root=ROOT, run_root=run, onboarding_path=onboarding,
            proxy_config_path=proxy_config, proxy_receipt_path=receipt,
            node_binary=Path(self.node).resolve(),
            proxy_launcher_path=(ROOT / "deploy/agent-comparison/assistant-model-proxy.mjs").resolve(),
            request_payload=payload, request_sha256=hashlib.sha256(payload).hexdigest(),
            expected_provider="local", expected_model=MODEL, control_runner=runner(port),
        )
        return run, receipt, port, result

    def test_real_loopback_proxy_control_turn_accounting_and_teardown(self):
        run, receipt, port, envelope = self.execute("green", ProxyCallingRunner)
        turn = envelope["conversation"]["turns"][0]
        self.assertEqual(turn["state"], "succeeded")
        self.assertEqual(envelope["modelProxyInitialReceipt"]["modelRequests"], 0)
        self.assertEqual(envelope["modelProxyFinalReceipt"]["modelRequests"], 1)
        self.assertEqual(envelope["modelProxyFinalReceipt"]["inputTokens"], 120)
        self.assertTrue(assistant_system._listener_absent(port))
        output = run / "evidence"; output.mkdir(mode=0o700)
        emitted = assistant_evidence.validate_and_emit(
            root=ROOT, run_dir=output, assistant_evidence=envelope, request_payload=MESSAGE.encode(),
            request_sha256=hashlib.sha256(MESSAGE.encode()).hexdigest(),
            required_evidence={"exact-source", "privacy-route"},
            expected_provider="local", expected_model=MODEL,
            source_sha256="7" * 64,
        )
        self.assertEqual({item["type"] for item in emitted}, {"exact-source", "privacy-route"})
        self.assertNotIn(MESSAGE, receipt.read_text(encoding="utf-8"))

    def test_usage_substitution_and_unleased_tool_fail_closed_with_teardown(self):
        run, onboarding, proxy_config, receipt, port = self.fixture("mismatch")
        payload = MESSAGE.encode()
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "accounting differs"):
            assistant_system.execute_assistant_turn(
                root=ROOT, run_root=run, onboarding_path=onboarding,
                proxy_config_path=proxy_config, proxy_receipt_path=receipt,
                node_binary=Path(self.node).resolve(),
                proxy_launcher_path=(ROOT / "deploy/agent-comparison/assistant-model-proxy.mjs").resolve(),
                request_payload=payload, request_sha256=hashlib.sha256(payload).hexdigest(),
                expected_provider="local", expected_model=MODEL,
                control_runner=ProxyCallingRunner(port, reported_input=121),
            )
        self.assertTrue(assistant_system._listener_absent(port))

        run, onboarding, proxy_config, receipt, port = self.fixture("widening")
        with self.assertRaisesRegex(assistant_evidence.evaluation.OutcomeError, "does not select"):
            assistant_system.execute_assistant_turn(
                root=ROOT, run_root=run, onboarding_path=onboarding,
                proxy_config_path=proxy_config, proxy_receipt_path=receipt,
                node_binary=Path(self.node).resolve(),
                proxy_launcher_path=(ROOT / "deploy/agent-comparison/assistant-model-proxy.mjs").resolve(),
                request_payload=payload, request_sha256=hashlib.sha256(payload).hexdigest(),
                expected_provider="local", expected_model=MODEL,
                control_runner=ProxyCallingRunner(port, tool_name="pixel_calendar_list"),
            )
        self.assertTrue(assistant_system._listener_absent(port))
        self.assertEqual(BackendHandler.requests, 1)


if __name__ == "__main__":
    unittest.main()
