"""Explicit preparation uses the real Store/host and invented loopback providers."""
import copy
import http.server
import json
from pathlib import Path
import sys
import threading

import pytest

if sys.platform == "win32":
    pytest.skip("Unix integration host; run in WSL", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import integrations_notes as notes
from integrations_common import IntegrationError, Store, default_config, digest, now
from integrations_service import Integrations, server_context


class Provider(http.server.BaseHTTPRequestHandler):
    def respond(self, result):
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.requests.append(("GET", self.path))
        assert self.path == "/api/v1/watch"
        if self.server.before_get:
            self.server.before_get()
        self.respond(copy.deepcopy(self.server.watches))

    def do_POST(self):
        message = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(("POST", self.path))
        if self.path == "/api/v1/watch":
            self.server.mutations.append(("add", message))
            self.respond({"uuid": "new-watch"})
            return
        assert self.path == "/mcp"
        if "id" not in message:
            self.send_response(202)
            self.end_headers()
            return
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": [{"name": "change", "inputSchema": {"type": "object"}}]}
        else:
            assert method == "tools/call"
            self.server.mutations.append(("mcp", message["params"]))
            result = {"content": [{"type": "text", "text": "Invented change completed."}]}
        self.respond({"jsonrpc": "2.0", "id": message["id"], "result": result})

    def do_DELETE(self):
        self.server.requests.append(("DELETE", self.path))
        self.server.mutations.append(("remove", self.path))
        self.respond({})

    def log_message(self, *_):
        pass


@pytest.fixture
def world(tmp_path, monkeypatch):
    endpoint = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    endpoint.requests, endpoint.mutations = [], []
    endpoint.watches, endpoint.before_get = {}, None
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "Owned.md").write_text("Invented original.\n")
    store = Store(tmp_path / "state")
    config = default_config()
    config["notes"].update(enabled=True, vaultPath=str(vault), write="ask")
    url = f"http://127.0.0.1:{endpoint.server_port}"
    config["apps"]["watch"].update(enabled=True, url=url, changes="ask")
    server = {"id": "invented", "name": "Invented", "kind": "http", "preset": "", "enabled": True,
              "url": url + "/mcp", "command": "", "workingFolder": "", "envNames": [],
              "tools": [{"name": "change", "description": "Invented change", "inputSchema": {"type": "object"}, "policy": "ask"}],
              "discoveredAt": now()}
    secrets = {"ntfyPassword": "", "mcp": {"invented": "invented-key"}, "mcpEnv": {}, "apps": {"watch": "invented-watch-key"}}
    server["checkedBinding"] = digest(server_context(server, secrets))
    config["mcp"]["servers"] = [server]
    store.save_config(config)
    store.save_secrets(secrets)
    service = Integrations(store)
    service.save(config["revision"], {key: config[key] for key in ("notes", "notifications", "mcp", "apps")}, {})
    notifications, note_mutations = [], []
    monkeypatch.setattr(service, "_notify_approval", lambda summary: notifications.append(summary))
    real_write = notes.write

    def counted_write(*args, **kwargs):
        note_mutations.append(True)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(notes, "write", counted_write)
    yield service, store, vault, endpoint, notifications, note_mutations
    endpoint.shutdown()
    endpoint.server_close()
    thread.join(timeout=2)


def policy(world, value):
    service, store, *_ = world
    config = store.config()
    config["notes"]["write"] = value
    config["apps"]["watch"].update(enabled=value != "off", changes="ask" if value == "off" else value)
    server = config["mcp"]["servers"][0]
    server["policies"] = {"change": value}
    service.save(config["revision"], {key: config[key] for key in ("notes", "notifications", "mcp", "apps")}, {})


def call(service, action, **fields):
    return service.handle("agent", {"schemaVersion": 1, "action": action, **fields})


def approve(service, reply):
    service.handle("owner", {"schemaVersion": 1, "action": "decide", "approvalId": reply["approvalId"], "decision": "approve"})


def prepared(reply, status="approval-required"):
    assert reply["status"] == status
    assert reply["preparationOnly"] is True
    assert reply["actionExecuted"] is reply["approvalConsumed"] is False


@pytest.mark.parametrize("value", ["ask", "allow"])
@pytest.mark.parametrize("mode,path", [("append", "Owned.md"), ("create", "New.md")])
def test_notes_prepare_never_writes_and_exact_normal_retry_preserves_policy(world, value, mode, path):
    service, store, vault, endpoint, notifications, mutations = world
    policy(world, value)
    args = {"path": path, "mode": mode, "text": "Exact invented Unicode: café 🐈.\n"}
    first = call(service, "notes-prepare", **args)
    prepared(first)
    assert call(service, "notes-prepare", **args)["approvalId"] == first["approvalId"]
    assert (vault / "Owned.md").read_text() == "Invented original.\n"
    assert not (vault / "New.md").exists()
    assert not mutations and not endpoint.requests and not notifications
    approve(service, first)
    before = (store.root / "approvals.json").read_bytes()
    prepared(call(service, "notes-prepare", **args), "approval-ready")
    assert (store.root / "approvals.json").read_bytes() == before
    assert not mutations and not notifications
    reply = call(service, "notes-write", **args)
    assert reply["written"] == path and len(mutations) == 1
    assert (vault / path).read_text().endswith(args["text"])
    assert store.approvals()[0]["state"] == ("used" if value == "ask" else "approved")


@pytest.mark.parametrize("value", ["ask", "allow"])
def test_custom_prepare_never_starts_provider_and_normal_retry_uses_exact_digest(world, value):
    service, store, _vault, endpoint, notifications, _mutations = world
    policy(world, value)
    args = {"server": "invented", "tool": "change", "arguments": {"text": "Exact invented value", "count": 0}}
    first = call(service, "mcp-prepare", **args)
    prepared(first)
    assert call(service, "mcp-prepare", **args)["approvalId"] == first["approvalId"]
    assert not endpoint.requests and not notifications
    approve(service, first)
    before = (store.root / "approvals.json").read_bytes()
    prepared(call(service, "mcp-prepare", **args), "approval-ready")
    assert (store.root / "approvals.json").read_bytes() == before
    assert not endpoint.requests
    assert call(service, "mcp-call", **args)["isError"] is False
    assert endpoint.mutations == [("mcp", {"name": "change", "arguments": args["arguments"]})]
    assert store.approvals()[0]["state"] == ("used" if value == "ask" else "approved")


@pytest.mark.parametrize("value", ["ask", "allow"])
@pytest.mark.parametrize("tool", ["add", "remove"])
def test_watch_prepare_reads_only_metadata_and_preserves_exact_normal_digest(world, value, tool):
    service, store, _vault, endpoint, notifications, mutations = world
    policy(world, value)
    endpoint.watches = {"owned": {"url": "https://invented.example/old", "title": "Owned"}}
    arguments = {"url": "https://invented.example/new", "title": "New", "below": 40} if tool == "add" else {"id": "owned"}
    args = {"server": "app:watch", "tool": tool, "arguments": arguments}
    first = call(service, "mcp-prepare", **args)
    prepared(first)
    assert endpoint.requests == [("GET", "/api/v1/watch")]
    assert not endpoint.mutations and not notifications and not mutations
    approve(service, first)
    before = (store.root / "approvals.json").read_bytes()
    prepared(call(service, "mcp-prepare", **args), "approval-ready")
    assert (store.root / "approvals.json").read_bytes() == before
    assert all(method == "GET" for method, _path in endpoint.requests)
    assert not endpoint.mutations and not notifications
    call(service, "mcp-call", **args)
    assert len(endpoint.mutations) == 1 and endpoint.mutations[0][0] == tool
    assert store.approvals()[0]["state"] == ("used" if value == "ask" else "approved")


@pytest.mark.parametrize("action,args", [
    ("notes-prepare", {"path": "Owned.md", "text": "No effect"}),
    ("mcp-prepare", {"server": "invented", "tool": "change", "arguments": {}}),
    ("mcp-prepare", {"server": "app:watch", "tool": "add", "arguments": {"url": "https://invented.example/new"}}),
])
def test_off_refuses_before_reads_or_ledger(world, action, args):
    service, store, _vault, endpoint, notifications, mutations = world
    policy(world, "off")
    with pytest.raises(IntegrationError):
        call(service, action, **args)
    assert not store.approvals() and not endpoint.requests and not endpoint.mutations and not notifications and not mutations


def test_watch_revoked_during_metadata_get_cannot_prepare(world):
    service, store, _vault, endpoint, notifications, mutations = world
    endpoint.before_get = lambda: policy(world, "off")
    with pytest.raises(IntegrationError):
        call(service, "mcp-prepare", server="app:watch", tool="add", arguments={"url": "https://invented.example/new"})
    assert endpoint.requests == [("GET", "/api/v1/watch")]
    assert not store.approvals() and not endpoint.mutations and not notifications and not mutations


def test_already_matching_watch_preparation_has_no_new_request_or_recovery_claim(world):
    service, store, _vault, endpoint, notifications, mutations = world
    endpoint.watches = {"owned": {"url": "https://invented.example/page", "title": "Owned"}}
    with pytest.raises(IntegrationError) as error:
        call(service, "mcp-prepare", server="app:watch", tool="add", arguments={"url": "https://invented.example/page"})
    assert error.value.code == "preparation-no-change"
    assert "recovery" not in error.value.message
    assert endpoint.requests == [("GET", "/api/v1/watch")]
    assert not store.approvals() and not endpoint.mutations and not notifications and not mutations


def test_declined_used_expired_and_changed_arguments_do_not_consume_or_reopen(world):
    service, store, _vault, endpoint, notifications, mutations = world
    args = {"path": "Owned.md", "text": "Exact text"}
    first = call(service, "notes-prepare", **args)
    service.decide(first["approvalId"], "decline")
    before = (store.root / "approvals.json").read_bytes()
    with pytest.raises(IntegrationError, match="declined"):
        call(service, "notes-prepare", **args)
    assert (store.root / "approvals.json").read_bytes() == before
    fresh = call(service, "notes-prepare", **{**args, "text": "Changed"})
    entries = store.approvals()
    entries[-1]["state"] = "used"
    store.save_approvals(entries)
    assert call(service, "notes-prepare", **{**args, "text": "Changed"})["approvalId"] != fresh["approvalId"]
    entries = store.approvals()
    entries[-1].update(state="approved", expiresAt=now() - 1)
    store.save_approvals(entries)
    prepared(call(service, "notes-prepare", **{**args, "text": "Changed"}))
    assert not endpoint.requests and not notifications and not mutations


@pytest.mark.parametrize("payload", [
    {"action": "notes-prepare", "path": "Owned.md", "text": "x", "date": "ignored?"},
    {"action": "notes-prepare", "path": "Owned.md", "text": "x", "mode": "overwrite"},
    {"action": "mcp-prepare", "server": "app:watch", "tool": "add", "arguments": {"url": "https://invented.example", "date": "ignored?"}},
    {"action": "mcp-prepare", "server": "app:documents", "tool": "read", "arguments": {"id": "1"}},
    {"action": "mcp-prepare", "server": [], "tool": "change", "arguments": {}},
])
def test_prepare_rejects_unsupported_constraints_and_read_actions_before_io(world, payload):
    service, store, _vault, endpoint, notifications, mutations = world
    with pytest.raises(IntegrationError):
        service.handle("agent", {"schemaVersion": 1, **payload})
    assert not store.approvals() and not endpoint.requests and not notifications and not mutations


@pytest.mark.parametrize("value", ["allow", "off"])
def test_review_tagged_prepare_still_requires_current_ask(world, value):
    service, store, _vault, endpoint, notifications, mutations = world
    policy(world, value)
    for action, args in [("notes-prepare", {"path": "Owned.md", "text": "x"}),
                         ("mcp-prepare", {"server": "app:watch", "tool": "add", "arguments": {"url": "https://invented.example/new"}})]:
        with pytest.raises(IntegrationError) as error:
            call(service, action, recoveryReviewOnly=True, **args)
        assert error.value.code in ({"review-only-policy", "app-off"} if value == "off" else {"review-only-policy"})
    assert not store.approvals() and not endpoint.requests and not notifications and not mutations


def test_review_prepare_can_only_prepare_and_cannot_start_custom_mcp(world):
    service, store, _vault, endpoint, notifications, mutations = world
    first = call(service, "notes-prepare", path="Owned.md", text="x", recoveryReviewOnly=True)
    prepared(first)
    approve(service, first)
    before = (store.root / "approvals.json").read_bytes()
    ready = call(service, "notes-prepare", path="Owned.md", text="x", recoveryReviewOnly=True)
    prepared(ready, "approval-ready")
    assert ready["recoveryReviewOnly"] is True
    assert (store.root / "approvals.json").read_bytes() == before
    with pytest.raises(IntegrationError):
        call(service, "mcp-prepare", server="invented", tool="change", arguments={}, recoveryReviewOnly=True)
    assert not endpoint.requests and not notifications and not mutations
