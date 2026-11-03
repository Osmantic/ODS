"""Real filesystem/HTTP boundaries and deterministic settings/approval races."""
import sys
from unittest import SkipTest
if sys.platform == "win32":
    raise SkipTest("Unix descriptor and socket contracts; run on Linux/WSL")

from concurrent.futures import ThreadPoolExecutor
import copy
import http.server
import os
from pathlib import Path
import threading
import time
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from integrations_common import Store, default_config, IntegrationError  # noqa: E402
import integrations_notes as notes  # noqa: E402
from integrations_mcp import HttpSession  # noqa: E402
from integrations_service import Integrations, send_notification  # noqa: E402
import integrations_service as integration_service  # noqa: E402


@pytest.fixture
def world(tmp_path):
    root = tmp_path.resolve()
    vault = root / "vault"
    vault.mkdir()
    (vault / "Note.md").write_text("original\n")
    config = default_config()
    config["notes"] = {"enabled": True, "vaultPath": str(vault), "write": "ask"}
    store = Store(root / "state")
    store.save_config(config)
    service = Integrations(store)
    yield service, store, vault
    for identity in list(service.local.running):
        service.local.stop(identity)


def incoming(store):
    config = store.config()
    return {key: copy.deepcopy(config[key]) for key in ("notes", "notifications", "mcp", "apps")}


def save(service, store, value=None, secrets=None):
    service.handle("owner", {"schemaVersion": 1, "action": "save", "expectedRevision": store.config()["revision"],
                             "settings": value or incoming(store), "secrets": secrets or {}})


def agent(service, action, **kwargs):
    return service.handle("agent", {"schemaVersion": 1, "action": action, **kwargs})


def approve(service, result):
    service.handle("owner", {"schemaVersion": 1, "action": "decide", "approvalId": result["approvalId"], "decision": "approve"})


@pytest.mark.parametrize("change", ["vault", "disable-enable", "save", "credential", "replace-directory"])
def test_old_note_approval_never_authorizes_changed_state(world, change):
    service, store, vault = world
    request = dict(path="Note.md", text="approved-before-change", mode="append")
    approve(service, agent(service, "notes-write", **request))
    value = incoming(store)
    target = vault
    if change == "vault":
        target = vault.with_name("other")
        target.mkdir()
        (target / "Note.md").write_text("different\n")
        value["notes"]["vaultPath"] = str(target)
    elif change == "disable-enable":
        value["notes"]["enabled"] = False
        save(service, store, value)
        value["notes"]["enabled"] = True
    elif change == "replace-directory":
        vault.rename(vault.with_name("old-vault"))
        vault.mkdir()
        (vault / "Note.md").write_text("replacement\n")
    if change != "replace-directory":
        save(service, store, value, {"ntfyPassword": "synthetic-new"} if change == "credential" else {})
    assert agent(service, "notes-write", **request)["status"] == "approval-required"
    assert "approved-before-change" not in (target / "Note.md").read_text()


@pytest.mark.parametrize("replace", ["rename", "recreate"])
def test_note_grant_is_not_reusable_for_a_replacement_at_the_same_path(world, replace):
    service, _, vault = world
    note = vault / "Note.md"
    request = dict(path=note.name, text="requested line", mode="append")
    approve(service, agent(service, "notes-write", **request))
    if replace == "rename":
        note.rename(vault / "previous.md")
    else:
        note.unlink()
    note.write_text("replacement\n")
    renewed = agent(service, "notes-write", **request)
    assert renewed["status"] == "approval-required"
    assert note.read_text() == "replacement\n"
    approve(service, renewed)
    assert agent(service, "notes-write", **request)["written"] == note.name
    assert note.read_text().splitlines().count("requested line") == 1


def test_unrelated_note_does_not_revoke_an_append_approval(world):
    service, _, vault = world
    request = dict(path="Note.md", text="requested line", mode="append")
    approve(service, agent(service, "notes-write", **request))
    (vault / "Other.md").write_text("another note")
    assert agent(service, "notes-write", **request)["written"] == "Note.md"


def test_weaker_saved_note_grants_require_a_fresh_owner_request(world):
    service, store, vault = world
    req = dict(path="Note.md", text="review again", mode="append")
    current = agent(service, "notes-write", **req)
    original, = store.approvals()
    legacy = {**original, "digest": "a" * 64}
    mcp = {**legacy, "id": "other-tool", "kind": "mcp"}
    store.save_approvals([legacy, {**legacy, "id": "previously-approved", "state": "approved"}, mcp])
    assert store.approvals() == [mcp]
    with pytest.raises(IntegrationError, match="no longer waiting"):
        approve(service, current)
    fresh = agent(service, "notes-write", **req)
    assert fresh["status"] == "approval-required" and fresh["approvalId"] != current["approvalId"]
    assert (vault / "Note.md").read_text() == "original\n"


def test_create_approval_tracks_the_existing_parent_without_creating_folders(world):
    service, _, vault = world
    parent = vault / "Inbox"
    parent.mkdir()
    request = dict(path="Inbox/Week/Status.md", text="status", mode="create")
    approve(service, agent(service, "notes-write", **request))
    assert not (parent / "Week").exists()
    parent.rename(vault / "OldInbox")
    parent.mkdir()
    replacement = agent(service, "notes-write", **request)
    assert replacement["status"] == "approval-required" and not (parent / "Week").exists()
    approve(service, replacement)
    assert agent(service, "notes-write", **request)["written"] == request["path"]
    assert (parent / "Week/Status.md").read_text() == "status\n"


def test_replacement_at_final_open_cannot_receive_the_approved_append(world, monkeypatch):
    service, _, vault = world
    note = vault / "Note.md"
    request = dict(path=note.name, text="approved", mode="append")
    approve(service, agent(service, "notes-write", **request))
    actual_open = os.open
    replaced = []

    def replace_at_open(path, flags, *args, **kwargs):
        if path == note.name and flags & os.O_APPEND and not replaced:
            replaced.append(True)
            note.unlink()
            note.write_text("different file\n")
        return actual_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(notes.os, "open", replace_at_open)
    with pytest.raises(IntegrationError, match="destination changed"):
        agent(service, "notes-write", **request)
    assert replaced and note.read_text() == "different file\n"


def test_saved_settings_revoke_pending_ids_before_any_other_write(world):
    service, store, _ = world
    pending = agent(service, "notes-write", path="Note.md", text="later", mode="append")
    with patch.object(store, "save_secrets", side_effect=OSError("simulated interruption")):
        with pytest.raises(OSError):
            save(service, store)
    with pytest.raises(IntegrationError, match="no longer waiting"):
        approve(service, pending)


class Session:
    def __init__(self, calls):
        self.calls = calls
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def call(self, name, arguments):
        self.calls.append((name, arguments))
        return "synthetic-completion", False


def set_server(service, store, vault, kind="http"):
    server = {"id": "review", "name": "Review", "kind": kind, "enabled": False,
              "url": "http://127.0.0.1:9/old", "command": "/synthetic/program", "workingFolder": str(vault),
              "envNames": ["FIXTURE_TOKEN"]}
    value = incoming(store)
    value["mcp"]["servers"] = [server]
    save(service, store, value, {"mcp": {"review": "old-token"}})
    with patch.object(service, "_owner_tools", return_value=[{"name": "change", "inputSchema": {"type": "object"}}]):
        service.handle("owner", {"schemaVersion": 1, "action": "discover", "serverId": "review"})
    value = incoming(store)
    value["mcp"]["servers"][0]["enabled"] = True
    save(service, store, value)


@pytest.mark.parametrize("change", ["url", "token", "command", "workingFolder", "environment", "remove-readd", "discover"])
def test_mcp_approval_is_bound_to_full_effective_destination(world, change):
    service, store, vault = world
    set_server(service, store, vault, "stdio" if change in ("command", "workingFolder", "environment") else "http")
    request = dict(server="review", tool="change", arguments={"value": 1})
    calls = []
    with patch.object(service, "_session", return_value=Session(calls)):
        approve(service, agent(service, "mcp-call", **request))
        value = incoming(store)
        server = value["mcp"]["servers"][0]
        secrets = {}
        if change == "url":
            server.update(url="http://127.0.0.1:10/new", enabled=False)
        elif change == "command":
            server.update(command="/synthetic/replacement", enabled=False)
        elif change == "workingFolder":
            server["workingFolder"] = str(vault.parent)
        elif change == "environment":
            secrets = {"mcpEnv": {"review": {"FIXTURE_TOKEN": "new-env"}}}
        elif change == "token":
            secrets = {"mcp": {"review": "new-token"}}
        elif change == "remove-readd":
            empty = incoming(store)
            empty["mcp"]["servers"] = []
            save(service, store, empty)
            server["enabled"] = False
        context_changes = ("url", "token", "command", "workingFolder", "environment", "remove-readd")
        if change in context_changes:
            # The composition also requires a fresh catalog check before an
            # endpoint, command or credential change can be enabled again.
            server["enabled"] = False
        if change != "discover":
            save(service, store, value, secrets)
        if change in (*context_changes, "discover"):
            with patch.object(service, "_owner_tools", return_value=[{"name": "change", "inputSchema": {"type": "object"}}]):
                service.handle("owner", {"schemaVersion": 1, "action": "discover", "serverId": "review"})
        if change in context_changes:
            value = incoming(store)
            value["mcp"]["servers"][0]["enabled"] = True
            save(service, store, value)
        assert agent(service, "mcp-call", **request)["status"] == "approval-required"
        assert calls == []


def test_concurrent_retries_consume_one_approval_once(world):
    service, _, vault = world
    request = dict(path="Note.md", text="only-once", mode="append")
    approve(service, agent(service, "notes-write", **request))
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: agent(service, "notes-write", **request), range(2)))
    assert sum("written" in reply for reply in replies) == 1
    assert (vault / "Note.md").read_text().count("only-once") == 1


def test_save_cannot_mix_an_old_target_with_new_credentials(world):
    service, store, vault = world
    set_server(service, store, vault)
    request = dict(server="review", tool="change", arguments={})
    approve(service, agent(service, "mcp-call", **request))
    entered, release, saved = threading.Event(), threading.Event(), threading.Event()
    captured = []
    class AdmittedSession(Session):
        def __init__(self, server, token, *_):
            super().__init__([])
            self.destination = (server["url"], token)
        def __enter__(self):
            entered.set()
            assert release.wait(5)
            captured.append(self.destination)
            return self
    def change():
        value = incoming(store)
        value["mcp"]["servers"][0]["enabled"] = False
        save(service, store, value, secrets={"mcp": {"review": "new-token"}})
        saved.set()
    with patch.object(integration_service, "McpSession", AdmittedSession), ThreadPoolExecutor(max_workers=2) as pool:
        call = pool.submit(agent, service, "mcp-call", **request)
        assert entered.wait(3)
        update = pool.submit(change)
        try:
            assert saved.wait(2), "Owner settings must remain available during provider I/O"
        finally:
            release.set()
        assert call.result()["text"] == "synthetic-completion"
        update.result()
    assert captured == [("http://127.0.0.1:9/old", "old-token")]


def test_discovery_cannot_attach_old_tools_to_a_replaced_server(world):
    service, store, vault = world
    set_server(service, store, vault)
    entered, release, saved = threading.Event(), threading.Event(), threading.Event()
    value = incoming(store)
    value["mcp"]["servers"][0].update(url="http://127.0.0.1:10/new", enabled=False)
    def discover(_server):
        entered.set()
        assert release.wait(5)
        return [{"name": "old-only", "annotations": {"readOnlyHint": True}}]
    def change():
        save(service, store, value)
        saved.set()
    with patch.object(service, "_owner_tools", side_effect=discover), ThreadPoolExecutor(max_workers=2) as pool:
        scan = pool.submit(service.discover, "review")
        assert entered.wait(3)
        update = pool.submit(change)
        try:
            assert saved.wait(2), "Owner settings must remain available during discovery"
        finally:
            release.set()
        with pytest.raises(IntegrationError, match="changed while checking tools"):
            scan.result()
        update.result()
    server = store.config()["mcp"]["servers"][0]
    assert server["tools"] == [] and server["discoveredAt"] is None


def test_slow_approval_notification_does_not_lock_owner_settings(world):
    service, store, _ = world
    value = incoming(store)
    value["notifications"]["enabled"] = True
    save(service, store, value)
    entered, release = threading.Event(), threading.Event()
    def slow_notification(*_args, **_kwargs):
        entered.set()
        assert release.wait(5)
    with patch.object(integration_service, "send_notification", side_effect=slow_notification), ThreadPoolExecutor(max_workers=2) as pool:
        call = pool.submit(agent, service, "notes-write", path="Note.md", text="later", mode="append")
        assert entered.wait(3)
        update = pool.submit(save, service, store)
        try:
            update.result(timeout=2)
        finally:
            release.set()
        call.result()
    assert store.approvals() == []


@pytest.mark.parametrize("change", ["disable", "restart"])
def test_stale_local_start_is_stopped_and_never_published(world, change):
    service, store, vault = world
    set_server(service, store, vault, "stdio")
    request = dict(server="review", tool="change", arguments={})
    approve(service, agent(service, "mcp-call", **request))
    entered, release, stopped = threading.Event(), threading.Event(), threading.Event()
    class StartingServer:
        fingerprint = "synthetic"
        stopped_because = ""
        def __init__(self, *_):
            pass
        def start(self, *, deadline=None):
            entered.set()
            assert release.wait(5)
        def stop(self, reason):
            self.stopped_because = reason
            stopped.set()
        def detail(self):
            return ""
    def update():
        if change == "disable":
            value = incoming(store)
            value["mcp"]["servers"][0]["enabled"] = False
            save(service, store, value)
        else:
            service.handle("owner", {"schemaVersion": 1, "action": "restart", "serverId": "review"})
    with patch.object(integration_service, "LocalServer", StartingServer), ThreadPoolExecutor(max_workers=2) as pool:
        call = pool.submit(agent, service, "mcp-call", **request)
        assert entered.wait(3)
        change_future = pool.submit(update)
        try:
            change_future.result(timeout=2)
        finally:
            release.set()
        with pytest.raises(IntegrationError, match="changed while the local server started"):
            call.result()
    assert stopped.is_set()
    assert service.local.running == {}


def test_queued_old_start_cannot_replace_a_newer_admission(world):
    service, store, vault = world
    set_server(service, store, vault, "stdio")
    server = store.config()["mcp"]["servers"][0]
    admitted = service._session(server)
    starting = service.local.starting["review"]
    entered = threading.Event()
    class NewerServer:
        stopped = False
        def alive(self):
            return True
        def stop(self, *_):
            self.stopped = True
    newer = NewerServer()
    def enter():
        entered.set()
        return admitted.__enter__()
    starting.acquire()
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(enter)
        assert entered.wait(2)
        try:
            value = incoming(store)
            value["mcp"]["servers"][0]["enabled"] = False
            save(service, store, value, secrets={"mcpEnv": {"review": {"FIXTURE_TOKEN": "new-env"}}})
            service.local.running["review"] = newer
        finally:
            starting.release()
        with pytest.raises(IntegrationError, match="changed before the local server started"):
            old.result()
    assert service.local.running["review"] is newer
    assert not newer.stopped


def test_admitted_local_start_keeps_the_shared_session_deadline(world):
    service, store, vault = world
    set_server(service, store, vault, "stdio")
    server = store.config()["mcp"]["servers"][0]
    admitted = service._session(server)
    starting = service.local.starting["review"]
    starting.acquire()
    try:
        deadline = time.monotonic() + .05
        with pytest.raises(IntegrationError, match="still starting"):
            admitted.local_servers.get(server, {}, deadline=deadline)
        assert time.monotonic() - deadline < 1
        assert service.local.running == {}
    finally:
        starting.release()


def test_note_search_remains_on_the_opened_vault_when_its_path_is_replaced(world):
    _, store, vault = world
    config = store.config()
    real_listdir = os.listdir
    moved = vault.with_name("opened-vault")
    swapped = False

    def replace_after_open(directory):
        nonlocal swapped
        if not swapped:
            swapped = True
            vault.rename(moved)
            vault.mkdir()
            (vault / "Replacement.md").write_text("needle-other-vault")
        return real_listdir(directory)

    with patch.object(notes.os, "listdir", side_effect=replace_after_open):
        assert notes.search(config, "needle") == []
    assert (vault / "Replacement.md").read_text() == "needle-other-vault"


@pytest.mark.parametrize("kind", ["hidden-file", "hidden-parent", "outside", "vault", "ancestor", "hardlink", "fifo"])
def test_note_read_and_append_refuse_links_and_special_files(world, kind):
    _, store, vault = world
    hidden = vault / ".hidden"
    hidden.mkdir()
    secret = hidden / "secret.md"
    secret.write_text("synthetic-private")
    config = store.config()
    relative = "alias.md"
    if kind == "hidden-parent":
        (vault / "alias").symlink_to(hidden, target_is_directory=True)
        relative = "alias/secret.md"
    elif kind == "outside":
        outside = vault.parent / "outside.md"
        outside.write_text("synthetic-private")
        (vault / relative).symlink_to(outside)
    elif kind in ("vault", "ancestor"):
        alias = vault.parent / "alias"
        alias.symlink_to(vault if kind == "vault" else vault.parent, target_is_directory=True)
        config["notes"]["vaultPath"] = str(alias if kind == "vault" else alias / vault.name)
        relative = "Note.md"
    elif kind == "hardlink":
        os.link(secret, vault / relative)
    elif kind == "fifo":
        os.mkfifo(vault / relative)
    else:
        (vault / relative).symlink_to(secret)
    for operation in (lambda: notes.read(config, relative), lambda: notes.write(config, relative, "forbidden", "append")):
        with pytest.raises(IntegrationError):
            operation()
    assert secret.read_text() == "synthetic-private"


@pytest.mark.parametrize("operation", ["read", "append", "search"])
def test_final_file_replaced_after_inspection_is_never_followed(world, operation):
    _, store, vault = world
    outside = vault.parent / "private.md"
    outside.write_text("needle-private")
    real_open = os.open
    swapped = False
    def race(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "Note.md" and not swapped:
            swapped = True
            (vault / "Note.md").unlink()
            (vault / "Note.md").symlink_to(outside)
        return real_open(path, flags, *args, **kwargs)
    with patch.object(notes.os, "open", side_effect=race):
        if operation == "search":
            assert notes.search(store.config(), "needle") == []
        else:
            with pytest.raises(IntegrationError):
                notes.read(store.config(), "Note.md") if operation == "read" else notes.write(store.config(), "Note.md", "forbidden", "append")
    assert outside.read_text() == "needle-private"


def test_create_parent_replaced_by_symlink_cannot_escape(world):
    _, store, vault = world
    outside = vault.parent / "outside"
    outside.mkdir()
    real_open = os.open
    swapped = False
    def race(path, flags, *args, **kwargs):
        nonlocal swapped
        if path == "new-folder" and not swapped and (vault / "new-folder").is_dir():
            swapped = True
            (vault / "new-folder").rmdir()
            (vault / "new-folder").symlink_to(outside, target_is_directory=True)
        return real_open(path, flags, *args, **kwargs)
    with patch.object(notes.os, "open", side_effect=race), pytest.raises(IntegrationError):
        notes.write(store.config(), "new-folder/Created.md", "forbidden", "create")
    assert not (outside / "Created.md").exists()


def test_valid_unicode_create_append_search_and_size_bound(world):
    _, store, vault = world
    config = store.config()
    relative = "旅行/予定.md"
    assert notes.write(config, relative, "# café agenda", "create") == relative
    notes.write(config, relative, "second", "append")
    assert "second" in notes.read(config, relative)[1]
    assert notes.search(config, "café agenda")[0]["path"] == relative
    (vault / "Large.md").write_bytes(b"x" * (notes.MAX_NOTE_BYTES + 1))
    with pytest.raises(IntegrationError):
        notes.read(config, "Large.md")
    with pytest.raises(IntegrationError):
        notes.write(config, "Large.md", "more", "append")


@pytest.mark.parametrize("relative", ["folder/", "folder//"])
@pytest.mark.parametrize("operation", ["read", "create", "append"])
def test_trailing_separator_cannot_generate_a_hidden_note(world, relative, operation):
    _, store, vault = world
    folder = vault / "folder"
    folder.mkdir()
    hidden = folder / ".md"
    if operation != "create":
        hidden.write_text("synthetic-private")
    with pytest.raises(IntegrationError):
        if operation == "read":
            notes.read(store.config(), relative)
        else:
            notes.write(store.config(), relative, "forbidden", operation)
    assert not hidden.exists() if operation == "create" else hidden.read_text() == "synthetic-private"


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("same_origin", [False, True])
@pytest.mark.parametrize("client", ["mcp", "ntfy"])
def test_redirects_never_forward_secrets_or_replay_the_action(world, code, same_origin, client):
    _, store, _ = world
    received = []
    class Destination(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        do_POST = do_GET
        def log_message(self, *_):
            pass
    target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Destination)
    class Redirect(Destination):
        def do_POST(self):
            if self.path == "/destination":
                return super().do_POST()
            self.send_response(code)
            self.send_header("Location", "/destination" if same_origin else f"http://127.0.0.1:{target.server_port}/destination")
            self.end_headers()
    source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for server in (target, source):
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()
    try:
        with pytest.raises(IntegrationError):
            if client == "mcp":
                session = HttpSession({"name": "Fixture", "url": f"http://127.0.0.1:{source.server_port}/mcp"}, "synthetic-token")
                session.session_id = "synthetic-session"
                session.request("tools/call", {"name": "change", "arguments": {}})
            else:
                config = store.config()
                config["notifications"].update(enabled=True, url=f"http://127.0.0.1:{source.server_port}")
                send_notification(config, {"ntfyPassword": "synthetic-token"}, "Fixture", "synthetic-message")
        assert received == []
    finally:
        for server in (source, target):
            server.shutdown()
            server.server_close()

@pytest.mark.parametrize("after_commit", [False, True])
def test_interrupted_owner_save_keeps_destination_and_credentials_together(world, after_commit):
    service, store, _ = world
    received = []

    class Receiver(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.server.label, self.headers.get("Authorization")))
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(403)
            self.end_headers()

        def log_message(self, *_):
            pass

    servers = []
    try:
        for label in ("old", "new"):
            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
            server.label = label
            servers.append(server)
            threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True).start()

        def settings(target):
            config = incoming(store)
            config["mcp"] = {"servers": [{"id": "home", "name": "Home", "kind": "http", "preset": "home-assistant",
                "enabled": False, "url": f"http://127.0.0.1:{target.server_port}/api/mcp", "policies": {}}]}
            return config

        save(service, store, settings(servers[0]), {"mcp": {"home": "old-key"}})
        write = store.save_config

        def interrupt(value):
            if after_commit:
                write(value)
            raise OSError("fixture interruption")

        with patch.object(store, "save_config", side_effect=interrupt), pytest.raises(OSError):
            save(service, store, settings(servers[1]), {"mcp": {"home": "new-key"}})
        reopened = Integrations(Store(store.root))
        with pytest.raises(IntegrationError) as refused:
            reopened.handle("owner", {"schemaVersion": 1, "action": "discover", "serverId": "home"})
        if after_commit:
            assert received == [("new", "Bearer new-key")]
        else:
            assert refused.value.code == "mcp-check-required" and received == []
            with pytest.raises(IntegrationError) as unrelated:
                save(reopened, reopened.store, settings(servers[0]))
            assert unrelated.value.code == "mcp-check-required"
        received.clear()
        save(reopened, reopened.store, settings(servers[1]), {"mcp": {"home": "new-key"}})
        assert "old-key" not in (store.root / "secrets.json").read_text()
        with pytest.raises(IntegrationError):
            reopened.handle("owner", {"schemaVersion": 1, "action": "discover", "serverId": "home"})
        assert received == [("new", "Bearer new-key")]
        assert "approvalBindings" not in reopened.handle("owner", {"schemaVersion": 1, "action": "get"})["settings"]
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


def test_interrupted_notification_rotation_requires_the_intended_destination_or_password(world):
    service, store, _ = world
    current = incoming(store)
    current["notifications"].update(enabled=True, url="https://previous.invalid", topic="fixture")
    save(service, store, current, {"ntfyPassword": "fixture-old"})
    changed = copy.deepcopy(current)
    changed["notifications"]["url"] = "https://replacement.invalid"
    with patch.object(store, "save_config", side_effect=OSError("fixture")), pytest.raises(OSError):
        save(service, store, changed, {"ntfyPassword": "fixture-new"})
    reopened = Integrations(Store(store.root))
    with pytest.raises(IntegrationError) as refused:
        agent(reopened, "notify", title="Fixture", message="Do not send")
    assert refused.value.code == "notification-check-required"
    with pytest.raises(IntegrationError) as unrelated:
        save(reopened, reopened.store, current)
    assert unrelated.value.code == "notification-check-required"
    save(reopened, reopened.store, changed)
    assert reopened.store.config()["notifications"]["url"] == "https://replacement.invalid"
