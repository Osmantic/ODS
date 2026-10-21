"""Ownership and real HTTP-worker checks; no Docker or installed services used."""

import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "bin"))
from model_switchboard import router_transport as transport


CONTAINER_ID = "a" * 64
ORIGIN = "http://host.docker.internal:13305"


class OwnershipTests(unittest.TestCase):
    def test_router_compose_mount_matches_host_agent_contract(self):
        compose = (Path(__file__).parents[1] / "docker-compose.base.yml").read_text(encoding="utf-8")
        self.assertIn("${ODS_CONFIG_DIR:-./config}/model-router:/config:ro", compose)
        self.assertIn("${ODS_DATA_DIR:-./data}:/state:ro", compose)

    def test_the_lemonade_transport_module_is_gone(self):
        self.assertIsNone(importlib.util.find_spec("model_switchboard.lemonade_transport"))
        self.assertEqual(transport.ENDPOINT_ID, "llama-server-default")

    def setUp(self):
        self.root = Path(tempfile.gettempdir(), "ods-transport-fixture").resolve()
        self.info = dict(Id=CONTAINER_ID, Running=True, Project="ods", Service="model-router",
                         Mounts=[dict(Type="bind", RW=False, Destination=destination,
                                      Source=str(self.root / source))
                                 for destination, source in (("/state", "data"),
                                     ("/config", "config/model-router"))])

    def process(self, stdout=b"", code=0, stderr=b""):
        return subprocess.CompletedProcess([], code, stdout, stderr)

    def run_request(self, info=None, candidates=None, response=b'{"status":"ok"}', path="/health", **kwargs):
        results = [self.process(candidates if candidates is not None else (CONTAINER_ID + "\n").encode()),
                   self.process(json.dumps(self.info if info is None else info).encode()),
                   self.process(response)]
        with patch.object(transport.subprocess, "run", side_effect=results) as run:
            result = transport.request(self.root, ORIGIN, path, **kwargs)
        return result, run.call_args_list

    def test_owned_container_is_pinned_and_secrets_only_use_stdin(self):
        result, calls = self.run_request(api_key="private-test-key")
        self.assertEqual(result, '{"status":"ok"}')
        self.assertEqual(calls[1].args[0][-1], CONTAINER_ID)
        self.assertEqual(calls[2].args[0][:8], ["docker", "exec", "-i", CONTAINER_ID, "python", "-I", "-S", "-c"])
        self.assertTrue(all("private-test-key" not in " ".join(call.args[0]) for call in calls))
        message = json.loads(calls[2].kwargs["input"])
        self.assertEqual(message["api_key"], "private-test-key")
        self.assertEqual(message["endpoint_id"], "llama-server-default")
        self.assertEqual(message["origin"], ORIGIN)
        self.assertEqual(calls[2].kwargs["timeout"], 15)
        self.assertNotIn(".Config.Env", calls[1].args[0][5])

    def test_exchange_asks_for_an_envelope_and_returns_status_and_text(self):
        envelope = json.dumps({"status": 400, "body": "Unable to generate parser for this template."}).encode()
        results = [self.process((CONTAINER_ID + "\n").encode()), self.process(json.dumps(self.info).encode()),
                   self.process(envelope)]
        with patch.object(transport.subprocess, "run", side_effect=results) as run:
            status, text = transport.exchange(self.root, ORIGIN, "/v1/chat/completions", {"messages": []},
                                              api_key="private-test-key", timeout=30)
        self.assertEqual((status, text), (400, "Unable to generate parser for this template."))
        message = json.loads(run.call_args_list[2].kwargs["input"])
        self.assertIs(message["envelope"], True)
        self.assertTrue(all("private-test-key" not in " ".join(call.args[0]) for call in run.call_args_list))
        # The plain request keeps its exact message: no envelope key at all.
        _result, calls = self.run_request()
        self.assertNotIn("envelope", json.loads(calls[2].kwargs["input"]))

    def test_exchange_rejects_a_malformed_envelope(self):
        for raw in (b'{"status":"400","body":"x"}', b'{"status":200}', b"not json", b"[1]"):
            results = [self.process((CONTAINER_ID + "\n").encode()), self.process(json.dumps(self.info).encode()),
                       self.process(raw)]
            with patch.object(transport.subprocess, "run", side_effect=results):
                with self.assertRaises(OSError):
                    transport.exchange(self.root, ORIGIN, "/props")

    def test_proof_and_telemetry_routes_use_the_owned_container(self):
        for path in ("/health", "/v1/models", "/props", "/metrics"):
            with self.subTest(path=path):
                result, calls = self.run_request(path=path, response=b"llamacpp:prompt_tokens_total 1\n")
                self.assertEqual(result, "llamacpp:prompt_tokens_total 1\n")
                message = json.loads(calls[2].kwargs["input"])
                self.assertEqual(message["path"], path)
                self.assertIsNone(message["payload"])
        payload = {"model": "Model.gguf", "messages": [{"role": "user", "content": "READY"}]}
        _result, calls = self.run_request(path="/v1/chat/completions", payload=payload,
                                          response=b'{"choices":[]}')
        self.assertEqual(json.loads(calls[2].kwargs["input"])["payload"], payload)

    def test_missing_ambiguous_or_short_container_id_stops_before_inspection(self):
        for candidates in (b"", b"abc\n", ((CONTAINER_ID + "\n") * 2).encode()):
            with self.subTest(candidates=candidates), patch.object(
                    transport.subprocess, "run", return_value=self.process(candidates)) as run:
                with self.assertRaises(OSError):
                    transport.request(self.root, ORIGIN, "/health")
                self.assertEqual(run.call_count, 1)

    def test_changed_identity_labels_state_or_mounts_reject_before_exec(self):
        changes = []
        for key, value in (("Id", "b" * 64), ("Running", False), ("Project", "another"),
                           ("Service", "dashboard-api")):
            changed = copy.deepcopy(self.info)
            changed[key] = value
            changes.append(changed)
        for index in (0, 1):
            for key, value in (("Source", "/some/other/install"), ("Type", "volume"),
                               ("RW", True)):
                changed = copy.deepcopy(self.info)
                changed["Mounts"][index][key] = value
                changes.append(changed)
        changed = copy.deepcopy(self.info)
        changed["Mounts"][1]["Destination"] = "/config/endpoints.json"
        changes.append(changed)
        changed = copy.deepcopy(self.info)
        changed["Mounts"].append(changed["Mounts"][0])
        changes.append(changed)
        changed = copy.deepcopy(self.info)
        changed["Mounts"] = []
        changes.append(changed)
        for changed in changes:
            with self.subTest(changed=changed), patch.object(transport.subprocess, "run", side_effect=[
                    self.process((CONTAINER_ID + "\n").encode()),
                    self.process(json.dumps(changed).encode())]) as run:
                with self.assertRaises(OSError):
                    transport.request(self.root, ORIGIN, "/health")
                self.assertEqual(run.call_count, 2)

    def test_unsafe_requests_fail_before_docker(self):
        requests = [("http://user:pass@host", "/health", {}),
                    ("http://host?x=1", "/health", {}),
                    # An origin never carries an API path; the router appends /v1.
                    ("http://host/v1", "/health", {}),
                    ("http://host/api/v1", "/health", {}),
                    (ORIGIN, "//other/health", {}),
                    # Retired Lemonade routes are not proof routes.
                    (ORIGIN, "/api/v1/health", {}),
                    (ORIGIN, "/api/v1/load", {"payload": {"model_name": "other"}}),
                    (ORIGIN, "/stats", {}),
                    (ORIGIN, "/metrics", {"payload": {}}),
                    (ORIGIN, "/health", {"payload": {}}),
                    (ORIGIN, "/v1/chat/completions", {}),
                    (ORIGIN, "/health", {"api_key": "key\r\ninjected: yes"}),
                    (ORIGIN, "/health", {"timeout": float("inf")}),
                    (ORIGIN, "/health", {"timeout": 0}),
                    (ORIGIN, "/health", {"project": "ods --privileged"}),
                    (ORIGIN, "/v1/chat/completions", {"payload": {"content": "x" * 65536}})]
        with patch.object(transport.subprocess, "run") as run:
            for origin, path, arguments in requests:
                with self.subTest(origin=origin, path=path, arguments=arguments):
                    with self.assertRaises(ValueError):
                        transport.request(self.root, origin, path, **arguments)
            run.assert_not_called()

    def test_malformed_ownership_metadata_is_rejected(self):
        for info in (None, [], {}, dict(self.info, Mounts=None), dict(self.info, Mounts=[None])):
            with self.subTest(info=info), patch.object(transport.subprocess, "run", side_effect=[
                    self.process((CONTAINER_ID + "\n").encode()),
                    self.process(json.dumps(info).encode())]) as run:
                with self.assertRaises(OSError):
                    transport.request(self.root, ORIGIN, "/health")
                self.assertEqual(run.call_count, 2)

    def test_output_limit_and_process_timeout_fail(self):
        with self.assertRaises(OSError):
            self.run_request(response=b"x" * 65537)
        with patch.object(transport.subprocess, "run", side_effect=subprocess.TimeoutExpired("docker", 10)):
            with self.assertRaises(subprocess.TimeoutExpired):
                transport.request(self.root, ORIGIN, "/health")


class HTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get("Authorization"), None))
        if self.server.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/should-never-be-requested")
            self.end_headers()
            return
        if self.server.mode == "error":
            self.send_error(503)
            return
        self.send_response(200)
        self.end_headers()
        try:
            if self.server.mode == "slow":
                for _ in range(30):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.04)
            else:
                sizes = {"large": 65537, "stream": 400000, "huge": 2097153}
                size = sizes.get(self.server.mode)
                self.wfile.write(b"x" * size if size else b'{"status":"ok"}')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # The timeout and size-limit tests intentionally close early.

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.requests.append((self.path, self.headers.get("Authorization"), json.loads(body)))
        if self.server.mode == "parser400":
            error = b'{"error":{"code":400,"message":"Unable to generate parser for this template."}}'
            self.send_response(400)
            self.send_header("Content-Length", str(len(error)))
            self.end_headers()
            self.wfile.write(error)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"choices":[{"message":{"content":"READY"}}]}')


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name) / "endpoints.json"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), HTTPHandler)
        self.server.mode = "ok"
        self.server.requests = []
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.endpoint = {"id": "llama-server-default", "baseUrl": self.origin,
                         "apiKeyEnv": "LLAMA_SERVER_API_KEY"}
        self.write_endpoints([self.endpoint])

    def write_endpoints(self, endpoints):
        self.config.write_text(json.dumps({"endpoints": endpoints}), encoding="utf-8")

    def worker(self, path="/health", payload=None, timeout=2, envelope=False):
        code = transport._WORKER.replace('"/config/endpoints.json"', repr(str(self.config)))
        message = dict(endpoint_id=transport.ENDPOINT_ID, origin=self.origin, path=path,
                       payload=payload, api_key="private-test-key", timeout=timeout)
        if envelope:
            message["envelope"] = True
        data = json.dumps(message).encode()
        # A bogus proxy proves the worker does not inherit ambient routing.
        env = dict(os.environ, HTTP_PROXY="http://127.0.0.1:1", http_proxy="http://127.0.0.1:1",
                   NO_PROXY="", no_proxy="")
        return subprocess.run([sys.executable, "-I", "-S", "-c", code], input=data,
                              capture_output=True, timeout=4, env=env)

    def test_real_get_and_completion_ignore_proxy_and_send_private_header(self):
        result = self.worker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": "ok"})
        payload = {"model": "Model.gguf", "messages": [{"role": "user", "content": "READY"}]}
        result = self.worker("/v1/chat/completions", payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.requests[-1], ("/v1/chat/completions", "Bearer private-test-key", payload))

    def test_props_and_metrics_are_read_only_through_the_same_bounded_worker(self):
        for path in ("/props", "/metrics"):
            result = self.worker(path)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.server.requests, [("/props", "Bearer private-test-key", None),
                                                ("/metrics", "Bearer private-test-key", None)])

    def test_changed_missing_duplicate_or_retired_endpoint_rejects_before_http(self):
        for rows in ([], [self.endpoint, self.endpoint],
                     [dict(self.endpoint, baseUrl=self.origin + "/v1")],
                     [dict(self.endpoint, baseUrl="http://127.0.0.1:1")],
                     # A pre-round-F allowlist row never authorizes a request.
                     [dict(self.endpoint, id="lemonade-default")]):
            self.write_endpoints(rows)
            result = self.worker()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertIn(b"configured runtime endpoint does not match", result.stderr)
        self.assertEqual(self.server.requests, [])

    def test_redirect_and_http_error_are_not_followed_or_accepted(self):
        for mode, status in (("redirect", 302), ("error", 503)):
            self.server.mode = mode
            result = self.worker()
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"HTTP {status}".encode(), result.stderr)
            self.assertEqual(result.stdout, b"")
        self.assertEqual(len(self.server.requests), 2)

    def test_envelope_returns_status_and_the_runtimes_own_error_text(self):
        result = self.worker(envelope=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"status": 200, "body": '{"status":"ok"}'})
        self.server.mode = "parser400"
        payload = {"messages": [{"role": "user", "content": "x"}], "tools": []}
        result = self.worker("/v1/chat/completions", payload, envelope=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        envelope = json.loads(result.stdout)
        self.assertEqual(envelope["status"], 400)
        self.assertIn("Unable to generate parser", envelope["body"])
        # Without the envelope the same answer still fails the request.
        result = self.worker("/v1/chat/completions", payload)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def test_envelope_still_refuses_redirects_and_oversized_answers(self):
        for mode, needle in (("redirect", b"HTTP 302"), ("huge", b"response exceeds 2 MiB")):
            self.server.mode = mode
            result = self.worker(envelope=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertIn(needle, result.stderr)
        self.assertNotIn("/should-never-be-requested", [request[0] for request in self.server.requests])

    def test_envelope_carries_a_streamed_probe_answer(self):
        # One JSON event per token: a thinking model's 1,000 tokens are ~400 KB.
        self.server.mode = "stream"
        result = self.worker(envelope=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)["body"]), 400000)

    def test_oversized_response_produces_no_partial_output(self):
        self.server.mode = "large"
        result = self.worker()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"response exceeds 64 KiB", result.stderr)

    def test_worker_deadline_stops_slow_drip_independent_of_socket_timeout(self):
        self.server.mode = "slow"
        started = time.monotonic()
        result = self.worker(timeout=0.2)
        self.assertEqual(result.returncode, 124, result.stderr)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"timed out", result.stderr)


if __name__ == "__main__":
    unittest.main()
