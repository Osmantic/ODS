import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires Unix sockets; run under Linux/WSL")

import http.server
import json
import os
import pathlib
import stat
import tempfile
import textwrap
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import integrations_service as service  # noqa: E402
from integrations_common import Store  # noqa: E402

TOOLS = [
    {"name": "get_state", "description": "Read a device state", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"entity": {"type": "string"}}}},
    {"name": "turn_on", "description": "Turn a device on", "inputSchema": {"type": "object"}},
    {"name": "unlock", "description": "Unlock a door", "annotations": {"destructiveHint": True}, "inputSchema": {"type": "object"}},
]


class FakeMcp(http.server.BaseHTTPRequestHandler):
    """A Streamable HTTP MCP server that answers with server-sent events."""
    calls = []

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != "Bearer secret-token":
            self.send_response(401)
            self.end_headers()
            return
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        if message["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}}
        elif message["method"] == "tools/list":
            result = {"tools": TOOLS}
        else:
            FakeMcp.calls.append(message["params"])
            result = {"content": [{"type": "text", "text": f"done {message['params']['name']}"}]}
        body = f"event: message\ndata: {json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result})}\n\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Mcp-Session-Id", "session-1")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class FakeNtfy(http.server.BaseHTTPRequestHandler):
    sent = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        if self.headers.get("Authorization") != "Basic cG9ydGFsOnB3":  # portal:pw
            self.send_response(403)
            self.end_headers()
            return
        FakeNtfy.sent.append({"topic": self.path.strip("/"), "title": self.headers["Title"], "message": body,
                              "priority": self.headers["Priority"]})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_):
        pass


def serve_http(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


STDIO_SERVER = textwrap.dedent('''
    import json, sys
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        if message["method"] == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {}}
        elif message["method"] == "tools/list":
            result = {"tools": [{"name": "echo", "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object"}}]}
        else:
            result = {"content": [{"type": "text", "text": "echo " + message["params"]["arguments"]["text"]}]}
        print("log line that is not JSON-RPC")
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
''')


class IntegrationsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = pathlib.Path(self.temporary.name)
        self.vault = root / "vault"
        (self.vault / "Home").mkdir(parents=True)
        (self.vault / ".obsidian").mkdir()
        (self.vault / ".obsidian" / "secret.md").write_text("deposit hidden")
        (self.vault / "Home" / "Lease.md").write_text("# Lease\nThe deposit is two months of rent.\nNotice: 60 days.\n")
        (self.vault / "Groceries.md").write_text("milk\neggs\n")
        self.store = Store(root / "state")
        self.service = service.Integrations(self.store)
        self.mcp = serve_http(FakeMcp)
        self.ntfy = serve_http(FakeNtfy)
        self.addCleanup(self.mcp.shutdown)
        self.addCleanup(self.ntfy.shutdown)
        FakeMcp.calls.clear()
        FakeNtfy.sent.clear()
        self.script = root / "server.py"
        self.script.write_text(STDIO_SERVER)

    def owner(self, action, **fields):
        return self.service.handle("owner", {"schemaVersion": 1, "action": action, **fields})

    def agent(self, action, **fields):
        return self.service.handle("agent", {"schemaVersion": 1, "action": action, **fields})

    def settings(self, *, notes=True, write="ask", servers=(), notify=True):
        return {"notifications": {"enabled": notify, "url": f"http://127.0.0.1:{self.ntfy.server_address[1]}",
                                  "topic": "portal-test", "username": "portal", "notifyApprovals": True},
                "notes": {"enabled": notes, "vaultPath": str(self.vault), "write": write},
                "mcp": {"servers": list(servers)}}

    def save(self, settings, secrets=None):
        revision = self.owner("get")["settings"]["revision"]
        return self.owner("save", expectedRevision=revision, settings=settings, secrets=secrets or {})

    def home_server(self, enabled=False, policies=None):
        return {"id": "home", "name": "Home Assistant", "kind": "http", "preset": "home-assistant", "enabled": enabled,
                "url": f"http://127.0.0.1:{self.mcp.server_address[1]}/api/mcp", "policies": policies or {}}

    def test_notes_search_read_and_hidden_folders(self):
        self.save(self.settings())
        results = self.agent("notes-search", query="lease deposit")["results"]
        self.assertEqual([item["path"] for item in results], ["Home/Lease.md"])
        self.assertEqual(results[0]["line"], 2)
        self.assertEqual(results[0]["matched"], 2)
        self.assertIn("60 days", self.agent("notes-read", path="Home/Lease")["text"])
        for bad in ("../outside.md", ".obsidian/secret.md", "/etc/passwd"):
            with self.assertRaises(service.IntegrationError):
                self.agent("notes-read", path=bad)

    def test_note_writes_follow_the_owner_policy_and_approvals_are_single_use(self):
        self.save(self.settings(write="off"), secrets={"ntfyPassword": "pw"})
        with self.assertRaisesRegex(service.IntegrationError, "not allowed"):
            self.agent("notes-write", path="Groceries.md", text="bread", mode="append")
        self.save(self.settings(write="ask"))
        waiting = self.agent("notes-write", path="Groceries.md", text="bread", mode="append")
        self.assertEqual(waiting["status"], "approval-required")
        self.assertNotIn("bread", (self.vault / "Groceries.md").read_text())
        self.assertEqual(FakeNtfy.sent[-1]["title"], "Portal needs your approval")
        self.assertEqual(self.owner("get")["approvals"][0]["summary"], "Add to note Groceries.md")
        self.owner("decide", approvalId=waiting["approvalId"], decision="approve")
        self.assertEqual(self.agent("notes-write", path="Groceries.md", text="bread", mode="append")["written"], "Groceries.md")
        self.assertEqual((self.vault / "Groceries.md").read_text(), "milk\neggs\n\nbread\n")
        self.assertEqual(self.agent("notes-write", path="Groceries.md", text="bread", mode="append")["status"], "approval-required")
        self.save(self.settings(write="allow"))
        self.agent("notes-write", path="Trips/Plan", text="# Plan", mode="create")
        self.assertEqual((self.vault / "Trips" / "Plan.md").read_text(), "# Plan\n")
        with self.assertRaisesRegex(service.IntegrationError, "already exists"):
            self.agent("notes-write", path="Trips/Plan", text="again", mode="create")

    def test_notifications_send_with_the_owner_credentials(self):
        self.save(self.settings(), secrets={"ntfyPassword": "pw"})
        self.assertTrue(self.agent("notify", title="Backup", message="Finished", priority="high")["sent"])
        self.assertEqual(FakeNtfy.sent[-1], {"topic": "portal-test", "title": "Backup", "message": "Finished", "priority": "high"})
        self.save(self.settings(), secrets={"ntfyPassword": "wrong"})
        with self.assertRaisesRegex(service.IntegrationError, "refused"):
            self.agent("notify", message="x")
        view = self.owner("get")["settings"]["notifications"]
        self.assertTrue(view["hasPassword"])
        self.assertNotIn("wrong", json.dumps(self.owner("get")))

    def test_mcp_discovery_policies_and_approved_calls(self):
        self.save(self.settings(servers=[self.home_server()]), secrets={"mcp": {"home": "secret-token"}})
        with self.assertRaisesRegex(service.IntegrationError, "Check the tools"):
            self.save(self.settings(servers=[self.home_server(enabled=True)]))
        tools = self.owner("discover", serverId="home")["settings"]["mcp"]["servers"][0]["tools"]
        self.assertEqual({tool["name"]: tool["policy"] for tool in tools}, {"get_state": "allow", "turn_on": "ask", "unlock": "ask"})
        self.assertTrue(tools[2]["destructive"])
        # As in the MCP specification, a tool without hints may change or delete things.
        self.assertEqual([tool["destructive"] for tool in tools], [False, True, True])
        self.save(self.settings(servers=[self.home_server(enabled=True, policies={"turn_on": "allow", "unlock": "ask"})]))
        listed = self.agent("mcp-tools")["servers"][0]
        self.assertEqual([(tool["name"], tool["needsApproval"]) for tool in listed["tools"]],
                         [("get_state", False), ("turn_on", False), ("unlock", True)])
        self.assertEqual(self.agent("mcp-call", server="home", tool="turn_on", arguments={"entity": "light.kitchen"})["text"], "done turn_on")
        waiting = self.agent("mcp-call", server="home", tool="unlock", arguments={"entity": "lock.front"})
        self.assertEqual(waiting["status"], "approval-required")
        self.assertEqual([call["name"] for call in FakeMcp.calls], ["turn_on"])
        self.owner("decide", approvalId=waiting["approvalId"], decision="approve")
        # The approval is for lock.front only: other arguments need their own.
        self.assertEqual(self.agent("mcp-call", server="home", tool="unlock", arguments={"entity": "lock.back"})["status"],
                         "approval-required")
        self.assertEqual(self.agent("mcp-call", server="home", tool="unlock", arguments={"entity": "lock.front"})["text"], "done unlock")
        self.save(self.settings(servers=[self.home_server(enabled=True, policies={"unlock": "off"})]))
        with self.assertRaisesRegex(service.IntegrationError, "no allowed tool"):
            self.agent("mcp-call", server="home", tool="unlock", arguments={"entity": "lock.front"})

    def test_wrong_token_and_unreachable_server_are_explained(self):
        self.save(self.settings(servers=[self.home_server()]), secrets={"mcp": {"home": "wrong"}})
        with self.assertRaisesRegex(service.IntegrationError, "refused the access token"):
            self.owner("discover", serverId="home")
        server = {**self.home_server(), "url": "http://127.0.0.1:9/api/mcp"}
        self.save(self.settings(servers=[server]))
        with self.assertRaisesRegex(service.IntegrationError, "could not be reached"):
            self.owner("discover", serverId="home")

    def test_stdio_server(self):
        server = {"id": "echo", "name": "Echo", "kind": "stdio", "enabled": False, "command": f"{sys.executable} {self.script}"}
        self.save(self.settings(servers=[server]))
        self.owner("discover", serverId="echo")
        self.save(self.settings(servers=[{**server, "enabled": True}]))
        self.assertEqual(self.agent("mcp-call", server="echo", tool="echo", arguments={"text": "hi"})["text"], "echo hi")

    def test_only_the_owner_socket_changes_settings(self):
        for action in ("get", "decide", "save"):
            with self.assertRaisesRegex(service.IntegrationError, "Only the owner"):
                self.agent(action)
        with self.assertRaisesRegex(service.IntegrationError, "Unknown owner action"):
            self.owner("notes-search", query="x")
        with self.assertRaisesRegex(service.IntegrationError, "another window"):
            self.owner("save", expectedRevision=7, settings=self.settings(), secrets={})

    def test_state_files_are_owner_only(self):
        self.save(self.settings(), secrets={"ntfyPassword": "pw"})
        for name in ("config.json", "secrets.json", "activity.jsonl"):
            self.assertEqual(stat.S_IMODE(os.stat(self.store.root / name).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.root).st_mode), 0o700)

    def test_each_socket_serves_its_own_role(self):
        root = pathlib.Path(self.temporary.name)
        servers = service.start(root / "agent.sock", root / "served")
        for server in servers:
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        owner_socket = root / "served" / "owner.sock"
        self.assertEqual(stat.S_IMODE(os.stat(owner_socket).st_mode), 0o600)
        self.assertEqual(service.client(owner_socket, {"action": "get"})["settings"]["revision"], 0)
        refused = service.client(root / "agent.sock", {"action": "get"})
        self.assertEqual((refused["ok"], refused["error"]), (False, "forbidden"))
        off = service.client(root / "agent.sock", {"action": "notify", "message": "hi"})
        self.assertEqual(off["error"], "notifications-off")
        self.assertEqual(service.client(owner_socket, {"action": "notify", "message": "hi"})["error"], "invalid-request")


if __name__ == "__main__":
    unittest.main()
