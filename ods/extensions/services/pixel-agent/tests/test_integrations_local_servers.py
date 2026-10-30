import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires process groups; run under Linux/WSL")

import json
import fcntl
import os
import pathlib
import tempfile
import textwrap
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import integrations_mcp as mcp  # noqa: E402
import integrations_service as service  # noqa: E402
from integrations_common import Store  # noqa: E402

# A local MCP server that misbehaves on purpose. Its first argument picks how.
SERVER = textwrap.dedent('''
    import json, os, subprocess, sys, time
    mode = sys.argv[1]
    if mode == "child":
        # Like npx: the real server is a child process.
        child = subprocess.Popen([sys.executable, sys.argv[0], "plain", sys.argv[2]])
        sys.exit(child.wait())
    if mode == "crash-on-start":
        print("Error: API_KEY is not set", file=sys.stderr, flush=True)
        sys.exit(1)
    calls = 0
    def send(message):
        sys.stdout.write(json.dumps(message) + "\\n")
        sys.stdout.flush()
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message or "method" not in message:
            continue
        method = message["method"]
        if method == "initialize":
            if mode == "silent-start":
                continue
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif method == "tools/list":
            names = ["count", "env", "where", "pid"] + (["added"] if os.path.exists(os.environ.get("EXTRA_TOOL", "/nonexistent")) else [])
            result = {"tools": [{"name": name, "inputSchema": {"type": "object"}} for name in names]}
        else:
            name = message["params"]["name"]
            calls += 1
            if mode == "hang":
                time.sleep(3600)
            if mode == "stderr-secret":
                print("x" * 290 + os.environ["API_KEY"], file=sys.stderr, flush=True)
                sys.exit(3)
            if mode == "error-secret":
                send({"jsonrpc": "2.0", "id": message["id"], "error": {
                    "code": -32000, "message": "x" * 100 + os.environ["API_KEY"]}})
                continue
            if mode == "exit-mid-call":
                print("Fatal: lost the database", file=sys.stderr, flush=True)
                sys.exit(3)
            if mode == "junk":
                print("not json at all")
                print("{broken")
                send({"jsonrpc": "2.0", "id": 9999, "result": {}})
            if mode == "huge":
                sys.stdout.write("x" * (400 * 1024) + "\\n")
                sys.stdout.flush()
                continue
            if mode == "asks":
                # A server that asks the client something mid-call and waits for the answer.
                send({"jsonrpc": "2.0", "id": "q1", "method": "roots/list"})
                answer = json.loads(sys.stdin.readline())
                send({"jsonrpc": "2.0", "id": "q2", "method": "sampling/createMessage", "params": {}})
                refusal = json.loads(sys.stdin.readline())
                text = f"roots={answer['result']['roots']} sampling={refusal['error']['code']}"
            elif name == "count":
                text = f"call {calls}"
            elif name == "env":
                text = f"{os.environ.get('API_KEY')}|{'PORTAL_SECRET' in os.environ}"
            elif name == "where":
                text = os.getcwd()
            else:
                text = str(os.getpid())
            result = {"content": [{"type": "text", "text": text}]}
        send({"jsonrpc": "2.0", "id": message["id"], "result": result})
''')


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie is gone for every practical purpose.
    status = pathlib.Path(f"/proc/{pid}/status")
    return not status.exists() or "State:\tZ" not in status.read_text()


def wait_until(condition, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


class LocalServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.script = self.root / "server.py"
        self.script.write_text(SERVER)
        self.local = mcp.LocalServers(idle_seconds=600)
        self.addCleanup(lambda: [self.local.stop(server_id) for server_id in list(self.local.running)])
        self.service = service.Integrations(Store(self.root / "state"), self.local)
        self.short_timeouts()

    def short_timeouts(self):
        original = (mcp.CALL_TIMEOUT, mcp.START_TIMEOUT)
        mcp.CALL_TIMEOUT, mcp.START_TIMEOUT = 2, 5
        self.addCleanup(lambda: (setattr(mcp, "CALL_TIMEOUT", original[0]), setattr(mcp, "START_TIMEOUT", original[1])))

    def owner(self, action, **fields):
        return self.service.handle("owner", {"schemaVersion": 1, "action": action, **fields})

    def agent(self, action, **fields):
        return self.service.handle("agent", {"schemaVersion": 1, "action": action, **fields})

    def server(self, mode="plain", **fields):
        return {"id": "local", "name": "Local", "kind": "stdio", "enabled": False,
                "command": f"{sys.executable} {self.script} {mode}", "workingFolder": "", "envNames": [], **fields}

    def save(self, *servers, secrets=None):
        revision = self.owner("get")["settings"]["revision"]
        settings = {"notifications": {"enabled": False, "url": "http://127.0.0.1:9", "topic": "t", "username": "",
                                      "notifyApprovals": True, "approvalLink": ""},
                    "notes": {"enabled": False, "vaultPath": "", "write": "ask"}, "mcp": {"servers": list(servers)}}
        return self.owner("save", expectedRevision=revision, settings=settings, secrets=secrets or {})

    def ready(self, mode="plain", policies=None, secrets=None, **fields):
        """A checked, turned-on local server whose tools are all allowed."""
        self.save(self.server(mode, **fields), secrets=secrets)
        self.owner("discover", serverId="local")
        allowed = {name: "allow" for name in ("count", "env", "where", "pid", "added")}
        self.save(self.server(mode, enabled=True, policies={**allowed, **(policies or {})}, **fields))

    def call(self, tool="count"):
        return self.agent("mcp-call", server="local", tool=tool, arguments={})

    def test_the_server_keeps_running_between_calls_and_remembers(self):
        self.ready()
        first = self.call("pid")["text"]
        self.assertEqual(self.call()["text"], "call 2")
        self.assertEqual(self.call()["text"], "call 3")
        self.assertEqual(self.call("pid")["text"], first)
        self.assertTrue(self.owner("get")["processes"]["local"]["running"])

    def test_a_hung_server_times_out_is_stopped_and_starts_again(self):
        self.ready("hang")
        started = time.time()
        with self.assertRaisesRegex(service.IntegrationError, "did not answer in time"):
            self.call()
        self.assertLess(time.time() - started, 6)
        self.assertFalse(self.owner("get")["processes"]["local"]["running"])
        with self.assertRaisesRegex(service.IntegrationError, "did not answer in time"):
            self.call()  # A fresh process, which hangs again; the service itself never blocks.

    def test_junk_lines_and_stray_replies_are_ignored(self):
        self.ready("junk")
        self.assertEqual(self.call()["text"], "call 1")

    def test_a_server_that_exits_mid_call_is_reported_and_its_output_shown_to_the_owner_only(self):
        self.ready("exit-mid-call")
        with self.assertRaises(service.IntegrationError) as refused:
            self.call()
        self.assertEqual(refused.exception.code, "mcp-unavailable")
        self.assertNotIn("database", refused.exception.message)
        self.assertTrue(wait_until(lambda: "lost the database" in self.owner("get")["processes"]["local"]["lastError"]))

    def test_a_server_that_fails_to_start_shows_the_owner_why(self):
        self.save(self.server("crash-on-start"))
        with self.assertRaises(service.IntegrationError) as refused:
            self.owner("discover", serverId="local")
        self.assertIn("API_KEY is not set", refused.exception.message)
        self.assertIn("API_KEY is not set", self.owner("get")["processes"]["local"]["lastError"])

    def test_a_server_that_never_answers_initialize_is_stopped(self):
        self.save(self.server("silent-start"))
        with self.assertRaisesRegex(service.IntegrationError, "did not answer in time"):
            self.owner("discover", serverId="local")
        self.assertEqual(self.local.running, {})

    def test_stopping_a_server_also_stops_what_it_started(self):
        self.ready("child plain")
        child = int(self.call("pid")["text"])
        self.assertTrue(alive(child))
        self.owner("restart", serverId="local")
        self.assertTrue(wait_until(lambda: not alive(child)))
        self.assertNotEqual(int(self.call("pid")["text"]), child)

    def test_turning_a_server_off_or_removing_it_stops_it(self):
        self.ready()
        pid = int(self.call("pid")["text"])
        self.save(self.server(enabled=False))
        self.assertTrue(wait_until(lambda: not alive(pid)))
        self.ready()
        pid = int(self.call("pid")["text"])
        self.save()
        self.assertTrue(wait_until(lambda: not alive(pid)))

    def test_questions_from_the_server_get_safe_answers(self):
        self.ready("asks")
        self.assertEqual(self.call()["text"], "roots=[] sampling=-32601")

    def test_huge_output_is_refused_and_the_server_stopped(self):
        self.ready("huge")
        with self.assertRaisesRegex(service.IntegrationError, "stopped"):
            self.call()
        self.assertIn("too much data", self.local.running["local"].stopped_because)

    def test_secret_variables_reach_only_the_server_and_never_the_screens(self):
        self.ready(envNames=["API_KEY"], policies={"env": "allow"}, secrets={"mcpEnv": {"local": {"API_KEY": "k-123"}}})
        first_pid = self.call("pid")["text"]
        os.environ["PORTAL_SECRET"] = "service environment"
        self.addCleanup(os.environ.pop, "PORTAL_SECRET", None)
        self.assertEqual(self.call("env")["text"], "[redacted]|False")
        view = self.owner("get")
        self.assertEqual(view["settings"]["mcp"]["servers"][0]["hasEnv"], {"API_KEY": True})
        self.assertNotIn("k-123", json.dumps(view))
        self.assertNotIn("k-123", json.dumps(self.agent("mcp-tools")))
        # A changed value restarts the server with it.
        self.save(self.server(enabled=False, envNames=["API_KEY"]), secrets={"mcpEnv": {"local": {"API_KEY": "k-456"}}})
        self.owner("discover", serverId="local")
        self.save(self.server(enabled=True, envNames=["API_KEY"], policies={"env": "allow", "pid": "allow"}))
        self.assertEqual(self.call("env")["text"], "[redacted]|False")
        self.assertNotEqual(self.call("pid")["text"], first_pid)
        # A removed variable loses its value.
        self.save(self.server(enabled=False))
        self.owner("discover", serverId="local")
        self.save(self.server(enabled=True, policies={"env": "allow"}))
        self.assertEqual(self.call("env")["text"], "None|False")
        self.assertEqual(Store(self.root / "state").secrets()["mcpEnv"], {})

    def test_stderr_and_mcp_errors_redact_known_secrets_before_clipping(self):
        secret = "invented-sensitive-prefix-" + "q" * 240
        self.ready("stderr-secret", envNames=["API_KEY"], policies={"count": "allow"},
                   secrets={"mcpEnv": {"local": {"API_KEY": secret}}})
        with self.assertRaises(service.IntegrationError):
            self.call()
        self.assertTrue(wait_until(lambda: "[redacted]" in self.local.detail("local")))
        self.assertNotIn(secret[:20], self.local.detail("local"))
        self.assertNotIn(secret[:20], json.dumps(self.owner("get")))
        self.ready("error-secret", envNames=["API_KEY"], policies={"count": "allow"},
                   secrets={"mcpEnv": {"local": {"API_KEY": secret}}})
        with self.assertRaises(service.IntegrationError) as refused:
            self.call()
        self.assertNotIn(secret[:20], refused.exception.message)
        self.assertIn("[redacted]", refused.exception.message)

    def test_a_valid_maximum_agent_frame_cannot_block_stdin_past_its_deadline(self):
        # Initialize normally, then deliberately never read another byte. This
        # is a VALID service request, not an oversized-input rejection test.
        script = self.root / "does-not-read.py"
        ready = self.root / "pipe-ready"
        script.write_text("import json,sys,time\n"
                          "message=json.loads(sys.stdin.readline())\n"
                          "print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{}}),flush=True)\n"
                          "open(sys.argv[1],'w').close()\n"
                          "time.sleep(30)\n")
        definition = self.server(command=f"{sys.executable} {script} {ready}")
        child = mcp.LocalServer(definition, {}).start()
        self.addCleanup(child.stop)
        self.assertTrue(wait_until(ready.exists))
        frame = {"schemaVersion": 1, "action": "mcp-call", "server": "local", "tool": "count", "arguments": {"value": ""}}
        frame["arguments"]["value"] = "x" * (service.MAX_REQUEST_BYTES - 1 - len(json.dumps(frame, separators=(",", ":")).encode()))
        self.assertEqual(len(json.dumps(frame, separators=(",", ":")).encode()) + 1, service.MAX_REQUEST_BYTES)
        wire = (json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "count", "arguments": frame["arguments"]}}) + "\n").encode()
        capacity = fcntl.fcntl(child.process.stdin.fileno(), fcntl.F_GETPIPE_SZ)
        # A larger host pipe could absorb this fixture; reduce only this owned
        # pipe when needed, without changing the valid64KiB request.
        if capacity >= len(wire):
            fcntl.fcntl(child.process.stdin.fileno(), fcntl.F_SETPIPE_SZ, 4096)
            capacity = fcntl.fcntl(child.process.stdin.fileno(), fcntl.F_GETPIPE_SZ)
        self.assertGreater(len(wire), capacity)
        errors = []
        def invoke():
            try:
                child.request("tools/call", {"name": "count", "arguments": frame["arguments"]}, timeout=0.2)
            except service.IntegrationError as error:
                errors.append(error.code)
        thread = threading.Thread(target=invoke, daemon=True)
        started = time.monotonic()
        thread.start()
        thread.join(timeout=0.8)
        try:
            self.assertFalse(thread.is_alive(), "valid request remained blocked past four request budgets")
            self.assertLess(time.monotonic() - started, 0.8)
            self.assertEqual(errors, ["mcp-timeout"])
            self.assertIsNotNone(child.process.poll())
        finally:
            child.stop("owned test cleanup")
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

    def test_bad_variable_names_and_values_are_refused(self):
        for names in (["PATH"], ["1BAD"], ["A", "A"], ["X-Y"]):
            with self.assertRaisesRegex(service.IntegrationError, "Environment variable names"):
                self.save(self.server(envNames=names))
        with self.assertRaisesRegex(service.IntegrationError, "unknown environment variable"):
            self.save(self.server(envNames=["A"]), secrets={"mcpEnv": {"local": {"B": "x"}}})

    def test_working_folder(self):
        folder = self.root / "work"
        folder.mkdir()
        self.ready(workingFolder=str(folder))
        self.assertEqual(self.call("where")["text"], str(folder))
        with self.assertRaisesRegex(service.IntegrationError, "working folder"):
            self.save(self.server(workingFolder="relative/path"))

    def test_a_missing_program_says_how_to_fix_it(self):
        self.save({**self.server(), "command": "surely-not-installed-mcp --stdio"})
        with self.assertRaisesRegex(service.IntegrationError, "surely-not-installed-mcp was not found. Give its full path"):
            self.owner("discover", serverId="local")

    def test_programs_in_the_owner_folders_are_found(self):
        bin_folder = pathlib.Path(os.path.expanduser("~/.local/bin"))
        argv, env = mcp.command_env({"command": "npx -y pkg", "envNames": []}, {})
        self.assertEqual(argv, ["npx", "-y", "pkg"])
        self.assertIn(str(bin_folder), env["PATH"].split(os.pathsep))
        argv, env = mcp.command_env({"command": "/opt/node/bin/npx -y pkg", "envNames": []}, {})
        # npx runs node through env; the folder of the given program comes first.
        self.assertEqual(env["PATH"].split(os.pathsep)[0], "/opt/node/bin")

    def test_startup_queue_wait_is_inside_the_session_budget(self):
        definition = self.server()
        starting = self.local.starting["local"]
        starting.acquire()
        started = time.monotonic()
        try:
            with self.assertRaises(service.IntegrationError) as refused:
                self.local.get(definition, {}, deadline=started + 0.2)
            self.assertEqual(refused.exception.code, "mcp-busy")
            self.assertLess(time.monotonic() - started, 0.8)
            self.assertEqual(self.local.running, {})
        finally:
            starting.release()

    def test_two_calls_at_once_share_one_server(self):
        self.ready()
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.call("pid")["text"])) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(self.local.running), 1)

    def test_an_idle_server_is_stopped_and_starts_again_on_next_use(self):
        self.ready()
        pid = int(self.call("pid")["text"])
        self.local.idle_seconds = 0
        self.local.stop_idle()
        self.assertTrue(wait_until(lambda: not alive(pid)))
        self.local.idle_seconds = 600
        self.assertNotEqual(int(self.call("pid")["text"]), pid)

    def test_new_tools_start_off_and_test_reports_changes(self):
        marker = self.root / "extra"
        self.ready(envNames=["EXTRA_TOOL"], policies={"count": "allow"},
                   secrets={"mcpEnv": {"local": {"EXTRA_TOOL": str(marker)}}})
        marker.write_text("1")
        self.owner("restart", serverId="local")
        notice = self.owner("test", serverId="local")["notice"]
        self.assertEqual(notice, "Local answered with 5 tools. Since the last check: 1 new. Check tools to review them.")
        tools = {tool["name"]: tool for tool in self.owner("discover", serverId="local")["settings"]["mcp"]["servers"][0]["tools"]}
        self.assertEqual((tools["added"]["policy"], tools["added"]["new"]), ("off", True))
        self.assertEqual((tools["count"]["policy"], tools["count"]["new"]), ("allow", False))
        with self.assertRaisesRegex(service.IntegrationError, "no allowed tool"):
            self.call("added")
        self.save(self.server(enabled=True, envNames=["EXTRA_TOOL"], policies={"count": "allow"}))
        self.assertFalse(any(tool["new"] for tool in self.owner("get")["settings"]["mcp"]["servers"][0]["tools"]))


if __name__ == "__main__":
    unittest.main()
