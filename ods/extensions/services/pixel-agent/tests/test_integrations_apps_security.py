"""Provider identity, exact admission and transport boundaries for built-in apps."""
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.server
import json
import socket
from pathlib import Path
import sys
import threading

import pytest

if sys.platform == "win32":
    pytest.skip("Unix integration service; run in WSL", allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
import integrations_apps as apps
import integrations_ntfy as ntfy
from integrations_common import Store, default_config, IntegrationError, safe_urlopen, MAX_RESULT_CHARS
from integrations_service import Integrations, server_context
from integrations_common import digest, app_context


@pytest.fixture
def world(tmp_path):
    store = Store(tmp_path / "state")
    config = default_config()
    config["apps"]["watch"].update(enabled=True, url="https://watch.example", changes="ask")
    config["apps"]["documents"].update(enabled=True, url="https://documents.example")
    store.save_config(config)
    store.save_secrets({"ntfyPassword": "", "mcp": {}, "mcpEnv": {},
                        "apps": {"watch": "watch-key", "documents": "document-key"}})
    service = Integrations(store)
    save(service, store)
    return service, store


def call(service, tool="add", **arguments):
    return service.handle("agent", {"schemaVersion": 1, "action": "mcp-call",
        "server": "app:watch", "tool": tool, "arguments": arguments})


def approve(service, waiting):
    service.handle("owner", {"schemaVersion": 1, "action": "decide",
        "approvalId": waiting["approvalId"], "decision": "approve"})


def save(service, store, settings=None, secrets=None):
    config = store.config()
    service.save(config["revision"], settings or {key: config[key]
        for key in ("notes", "notifications", "mcp", "apps")}, secrets or {})


def test_watch_health_is_visible_without_exposing_provider_credentials(world, monkeypatch):
    service, _store = world
    listing = {"owned": {"url": "https://page.example", "title": "Product watch-key",
                         "last_checked": 123, "paused": False,
                         "last_error": "x" * 395 + "watch-key" + " provider could not extract a price"}}
    monkeypatch.setattr(apps, "watches", lambda *_: copy.deepcopy(listing))
    reply = service.handle("owner", {"schemaVersion": 1, "action": "watch-list"})
    watch = reply["watches"]["watches"][0]
    assert watch["lastChecked"] == 123 and watch["paused"] is False
    assert watch["error"] and len(watch["error"]) <= 400
    assert "watch-key" not in json.dumps(reply)
    native = call(service, "list")
    assert "Last check failed:" in native["text"]
    assert "watch-key" not in native["text"]


def test_named_watch_approvals_show_the_exact_page_address(world, monkeypatch):
    service, _store = world
    monkeypatch.setattr(apps, "watches", lambda *_: {})
    first = call(service, "add", url="https://first.example/product", title="Same title", below=70)
    second = call(service, "add", url="https://second.example/product", title="Same title", below=70)
    assert first["status"] == second["status"] == "approval-required"
    assert "https://first.example/product" in first["summary"]
    assert "https://second.example/product" in second["summary"]
    assert first["approvalId"] != second["approvalId"]


def test_watch_coverage_survives_long_provider_failures(world, monkeypatch):
    service, _store = world
    listing = {f"w{i}": {"url": f"https://page.example/{i}", "title": f"Product {i}",
                         "last_error": "Provider could not read the page. " * 30}
               for i in range(apps.MAX_WATCHES + 1)}
    monkeypatch.setattr(apps, "watches", lambda *_: listing)
    reply = call(service, "list")
    assert len(reply["text"]) <= MAX_RESULT_CHARS + len("\n[truncated]")
    assert reply["text"].startswith(f"Showing up to {apps.MAX_WATCHES} of {len(listing)} watched pages;")
    assert "[truncated]" in reply["text"]


@pytest.mark.parametrize("change", ["endpoint", "key", "target"])
def test_watch_approval_never_spends_for_changed_resource(world, monkeypatch, change):
    service, store = world
    listing = {"same-id": {"url": "https://original.example", "title": "Original"}}
    effects = []
    monkeypatch.setattr(apps, "watches", lambda *_: copy.deepcopy(listing))
    monkeypatch.setattr(apps, "remove_watch", lambda *args: effects.append(args))
    pending = call(service, "remove", id="same-id")
    assert pending["status"] == "approval-required"
    approve(service, pending)
    # Deliberately bypass normal Save's revocation: digest checks must still bind
    # the full endpoint/account/resource identity rather than just the provider ID.
    if change == "endpoint":
        config = store.config()
        config["apps"]["watch"]["url"] = "https://replacement.example"
        store.save_config(config)
    elif change == "key":
        secrets = store.secrets()
        secrets["apps"]["watch"] = "replacement-key"
        store.save_secrets(secrets)
    else:
        listing["same-id"]["url"] = "https://replacement-target.example"
    if change in ("endpoint", "key"):
        with pytest.raises(IntegrationError) as refused:
            call(service, "remove", id="same-id")
        assert refused.value.code == "app-check-required"
    else:
        newer = call(service, "remove", id="same-id")
        assert newer["status"] == "approval-required"
        assert newer["approvalId"] != pending["approvalId"]
    assert effects == []


def test_same_exact_approval_executes_once_and_retry_needs_new_request(world, monkeypatch):
    service, _store = world
    listing = {}
    effects = []
    monkeypatch.setattr(apps, "watches", lambda *_: copy.deepcopy(listing))
    def added(_settings, _key, url, title, below):
        effects.append(url)
        listing["new-id"] = {"url": url, "title": title}
        return "new-id"
    monkeypatch.setattr(apps, "add_watch", added)
    pending = call(service, url="https://page.example", title="Page")
    assert effects == []
    approve(service, pending)
    result = call(service, url="https://page.example", title="Page")
    assert result["isError"] is False
    assert effects == ["https://page.example"]
    assert "already watched" in call(service, url="https://page.example")["text"]
    assert effects == ["https://page.example"]


def test_parallel_allow_adds_do_not_create_duplicate_remote_watches(world, monkeypatch):
    service, store = world
    config = store.config()
    config["apps"]["watch"]["changes"] = "allow"
    save(service, store, {key: config[key] for key in ("notes", "notifications", "mcp", "apps")})
    listing, effects = {}, []
    monkeypatch.setattr(apps, "watches", lambda *_: copy.deepcopy(listing))
    def added(_settings, _key, url, title, below):
        effects.append(url)
        listing["same-id"] = {"url": url, "title": title}
        return "same-id"
    monkeypatch.setattr(apps, "add_watch", added)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(call, service, url="https://page.example") for _ in range(2)]
        replies = [future.result(timeout=5) for future in futures]
    assert len(effects) == 1
    assert sum("already watched" in reply["text"] for reply in replies) == 1


def test_provider_io_does_not_hold_settings_lock(world, monkeypatch):
    service, store = world
    config = store.config()
    config["apps"]["watch"]["changes"] = "allow"
    save(service, store, {key: config[key] for key in ("notes", "notifications", "mcp", "apps")})
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(apps, "watches", lambda *_: {})
    def added(*_):
        entered.set()
        assert release.wait(5)
        return "created"
    monkeypatch.setattr(apps, "add_watch", added)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(call, service, url="https://page.example")
        assert entered.wait(2)
        settings = {key: store.config()[key] for key in ("notes", "notifications", "mcp", "apps")}
        settings["notes"]["write"] = "off"
        saved = pool.submit(save, service, store, settings)
        try:
            saved.result(timeout=2)
        finally:
            release.set()
        assert future.result(timeout=3)["isError"] is False


def test_url_and_key_are_one_admitted_snapshot(world, monkeypatch):
    service, store = world
    entered, release = threading.Event(), threading.Event()
    seen = []
    def search(settings, key, _query):
        seen.append((settings["url"], key))
        entered.set()
        assert release.wait(5)
        return {"results": [], "total": 0}
    monkeypatch.setattr(apps, "documents_search", search)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(service.handle, "agent", {"schemaVersion": 1, "action": "mcp-call",
            "server": "app:documents", "tool": "search", "arguments": {"query": "fixture"}})
        assert entered.wait(2)
        settings = {key: store.config()[key] for key in ("notes", "notifications", "mcp", "apps")}
        settings["apps"]["documents"]["url"] = "https://new-documents.example"
        try:
            save(service, store, settings, {"apps": {"documents": "new-key"}})
        finally:
            release.set()
        assert future.result(timeout=3)["isError"] is False
    assert seen == [("https://documents.example", "document-key")]


def test_watch_history_is_scoped_and_old_provider_is_preserved(world, monkeypatch):
    service, store = world
    config, _settings, key = service._app_snapshot("watch")
    old_context = service._watch_context(config, key)
    old = {"shared-id": {"lastChanged": 1, "below": 17, "lastPrice": 20}}
    store.save_watch_state(old, old_context)
    settings = {key: store.config()[key] for key in ("notes", "notifications", "mcp", "apps")}
    settings["apps"]["watch"]["url"] = "https://other-watch.example"
    save(service, store, settings)
    assert store.watch_state() == {}
    assert store.watch_state(old_context) == old
    new = {"shared-id": {"lastChanged": 9, "below": None, "lastPrice": None}}
    store.save_watch_state(new)
    assert store.watch_state(old_context) == old
    assert store.watch_state() == new


def test_unknown_legacy_watch_history_is_not_adopted_or_reset(world):
    _service, store = world
    legacy = {"shared-id": {"below": 17, "lastChanged": 1, "lastPrice": 20}}
    store._write("watch-state.json", legacy)
    before = (store.root / "watch-state.json").read_bytes()
    with pytest.raises(IntegrationError, match="no verified provider identity"):
        store.watch_state()
    assert (store.root / "watch-state.json").read_bytes() == before


@pytest.mark.parametrize("url", ["https://[", "https://host:invalid", "https://user:password@host"])
def test_invalid_saved_and_watched_urls_are_explained_without_mutation(world, url):
    service, store = world
    before = (store.root / "config.json").read_bytes()
    settings = {key: store.config()[key] for key in ("notes", "notifications", "mcp", "apps")}
    settings["apps"]["watch"]["url"] = url
    with pytest.raises(IntegrationError):
        save(service, store, settings)
    assert (store.root / "config.json").read_bytes() == before
    with pytest.raises(IntegrationError, match="full web address"):
        apps.valid_watch_url(url)


def test_untrusted_watch_ids_cannot_change_the_api_path(monkeypatch):
    sent = []
    monkeypatch.setattr(apps, "request", lambda *_args, **_kwargs: sent.append(True))
    for identity in ("../settings", "watch?remove=all", "a/b", "", "x" * 129):
        with pytest.raises(IntegrationError, match="invalid watch id"):
            apps.remove_watch({"url": "https://watch.example"}, "key", identity)
    assert sent == []
    monkeypatch.setattr(apps, "request", lambda *_args, **_kwargs: {"../settings": {"url": "https://page.example"}})
    with pytest.raises(IntegrationError, match="invalid watch id"):
        apps.watches({"url": "https://watch.example"}, "key")


def test_provider_echo_cannot_expose_admitted_app_key_after_rotation(world, monkeypatch):
    service, store = world
    def search(settings, key, _query):
        updated = {name: store.config()[name] for name in ("notes", "notifications", "mcp", "apps")}
        save(service, store, updated, {"apps": {"documents": "rotated-key"}})
        return {"results": [{"id": 1, "title": "Fixture", "created": "2026-11-01",
                "snippet": key, "link": "https://documents.example/documents/1/details"}], "total": 1}
    monkeypatch.setattr(apps, "documents_search", search)
    reply = service.handle("agent", {"schemaVersion": 1, "action": "mcp-call",
        "server": "app:documents", "tool": "search", "arguments": {"query": "fixture"}})
    assert "document-key" not in reply["text"]
    assert "Fixture" in reply["text"]


def test_document_projection_clips_after_secret_redaction(world, monkeypatch):
    service, store = world
    save(service, store, secrets={"apps": {"documents": "SECRET-FIXTURE-KEY"}})
    link = "https://documents.example/documents/1/details"
    header = "Fixture (2026-11-01)\n" + link + "\n\n"
    body = "x" * (7997 - len(header)) + "SECRET-FIXTURE-KEY" + "z" * 5000
    monkeypatch.setattr(apps, "documents_read", lambda *_: {
        "title": "Fixture", "created": "2026-11-01", "link": link, "text": body})
    reply = service.handle("agent", {"schemaVersion": 1, "action": "mcp-call",
        "server": "app:documents", "tool": "read", "arguments": {"id": 1}})
    assert reply["text"].endswith("\n[truncated]")
    assert len(reply["text"]) == 8000 + len("\n[truncated]")
    assert "SECRET" not in reply["text"]
    assert not reply["text"].endswith("SEC\n[truncated]")


@pytest.mark.parametrize("partial", ["new-key-old-config", "new-config-old-key"])
def test_partial_app_save_cannot_send_a_key_to_the_wrong_destination(world, monkeypatch, partial):
    service, store = world
    effects = []
    monkeypatch.setattr(apps, "documents_search", lambda *args: effects.append(args))
    monkeypatch.setattr(apps, "documents_test", lambda *args: effects.append(args))
    changed = store.config()
    changed["apps"]["documents"]["url"] = "https://new-documents.example"
    if partial == "new-key-old-config":
        actual_save = store.save_config
        def fail_config(_value):
            raise OSError("injected after secrets commit")
        monkeypatch.setattr(store, "save_config", fail_config)
        with pytest.raises(OSError, match="after secrets"):
            save(service, store, {key: changed[key] for key in ("notes", "notifications", "mcp", "apps")},
                 {"apps": {"documents": "new-destination-key"}})
        monkeypatch.setattr(store, "save_config", actual_save)
        assert store.config()["apps"]["documents"]["url"] == "https://documents.example"
        assert store.secrets()["apps"]["documents"] == "new-destination-key"
    else:
        changed["approvalBindings"]["appContexts"]["documents"] = app_context(changed, "documents", "new-destination-key")
        store.save_config(changed)
        assert store.secrets()["apps"]["documents"] == "document-key"
    with pytest.raises(IntegrationError) as agent_refusal:
        service.handle("agent", {"schemaVersion": 1, "action": "mcp-call", "server": "app:documents",
            "tool": "search", "arguments": {"query": "fixture"}})
    assert agent_refusal.value.code == "app-check-required"
    with pytest.raises(IntegrationError) as owner_refusal:
        service.handle("owner", {"schemaVersion": 1, "action": "test-app", "app": "documents"})
    assert owner_refusal.value.code == "app-check-required"
    assert effects == []
    assert "new-destination-key" not in repr(service.view())


def test_new_and_legacy_ntfy_connections_do_not_opt_into_task_output(world, monkeypatch):
    service, store = world
    assert default_config()["notifications"]["scheduledTasks"] == "off"
    old = store.config()
    old["notifications"]["enabled"] = True
    old["notifications"].pop("scheduledTasks")
    store.save_config(old)
    before = (store.root / "config.json").read_bytes()
    sent = []
    monkeypatch.setattr(service, "_tell", lambda *_args, **_kwargs: sent.append(True))
    assert service.view()["settings"]["notifications"]["scheduledTasks"] == "off"
    result = service.handle("agent", {"schemaVersion": 1, "action": "task-finished", "jobId": "private-task",
        "runAtMs": 1, "status": "ok", "summary": "sensitive task result"})
    assert result["notified"] is False
    assert sent == []
    assert (store.root / "config.json").read_bytes() == before


@pytest.mark.parametrize("mode", ["all", "failures"])
def test_explicit_task_notice_choices_survive_upgrade(world, monkeypatch, mode):
    service, store = world
    config = store.config()
    config["notifications"].update(enabled=True, scheduledTasks=mode)
    store.save_config(config)
    sent = []
    monkeypatch.setattr(service, "_tell", lambda *_args, **_kwargs: sent.append(True) or True)
    assert service.view()["settings"]["notifications"]["scheduledTasks"] == mode
    assert service.handle("agent", {"schemaVersion": 1, "action": "task-finished", "jobId": "chosen-task",
        "runAtMs": 1, "status": "error", "error": "fixture"})["notified"] is True
    assert sent == [True]


@pytest.fixture
def ntfy_endpoint():
    calls = []
    class Reply(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, self.headers.get("Authorization"),
                          self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Reply)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()


def legacy_ntfy_store(tmp_path, url):
    store = Store(tmp_path / "legacy-state")
    config = default_config()
    config["notifications"].update(enabled=True, url=url)
    store.save_config(config)
    store.save_secrets({"ntfyPassword": "legacy-password", "mcp": {}, "mcpEnv": {}, "apps": {}})
    return Integrations(store), store


def test_untouched_legacy_ntfy_pair_and_normal_save_both_work(tmp_path, ntfy_endpoint):
    url, calls = ntfy_endpoint
    service, store = legacy_ntfy_store(tmp_path, url)
    ntfy.send_notification(store.config(), store.secrets(), "Legacy", "first")
    assert calls == [("/portal", "Bearer legacy-password", b"first")]
    save(service, store, secrets={"ntfyPassword": "new-password"})
    config, secrets = store.config(), store.secrets()
    marker = config["approvalBindings"]["notificationContext"]
    assert marker == secrets["notificationContext"]
    ntfy.send_notification(config, secrets, "Saved", "second")
    assert calls[-1] == ("/portal", "Bearer new-password", b"second")
    projected = json.dumps(service.view())
    assert marker not in projected and "new-password" not in projected


@pytest.mark.parametrize("partial", ["new-secret-old-config", "new-config-old-secret", "held-legacy-config"])
def test_torn_ntfy_pair_never_transmits_a_credential(tmp_path, ntfy_endpoint, monkeypatch, partial):
    url, calls = ntfy_endpoint
    service, store = legacy_ntfy_store(tmp_path, url)
    old_config, old_secrets = store.config(), store.secrets()
    changed = copy.deepcopy(old_config)
    changed["notifications"]["url"] = url + "/new-destination"
    if partial == "new-secret-old-config":
        actual_save = store.save_config
        monkeypatch.setattr(store, "save_config", lambda _value: (_ for _ in ()).throw(OSError("injected after secret write")))
        with pytest.raises(OSError):
            save(service, store, {key: changed[key] for key in ("notes", "notifications", "mcp", "apps")},
                 {"ntfyPassword": "new-destination-password"})
        monkeypatch.setattr(store, "save_config", actual_save)
        config, secrets = store.config(), store.secrets()
        assert config["notifications"]["url"] == url
        assert secrets["ntfyPassword"] == "new-destination-password"
    else:
        save(service, store, {key: changed[key] for key in ("notes", "notifications", "mcp", "apps")},
             {"ntfyPassword": "new-destination-password"})
        config, secrets = ((store.config(), old_secrets) if partial == "new-config-old-secret"
                           else (old_config, store.secrets()))
    with pytest.raises(IntegrationError) as refused:
        ntfy.send_notification(config, secrets, "Blocked", "must not leave")
    assert refused.value.code == "notification-check-required"
    assert calls == []


@pytest.mark.parametrize("zone", ["UTC", "Mars/Base"])
def test_manual_digest_works_without_an_active_timezone(world, ntfy_endpoint, monkeypatch, zone):
    service, store = world
    url, calls = ntfy_endpoint
    config = store.config()
    config["notifications"].update(enabled=True, url=url)
    config["apps"]["news"].update(enabled=True, username="fixture")
    config["apps"]["news"]["digest"].update(enabled=False, timezone=zone)
    save(service, store, {key: config[key] for key in ("notes", "notifications", "mcp", "apps")},
         {"apps": {"news": "synthetic-news-key"}})
    monkeypatch.setattr(apps, "unread_news", lambda *_args, **_kwargs: [
        {"id": "one", "feed": "Feed", "title": "Fixture headline", "link": "", "summary": ""}])
    assert service.handle("owner", {"schemaVersion": 1, "action": "send-digest"})["notice"] == "Digest sent with 1 story."
    assert len(calls) == 1 and calls[0][2] == b"Feed:\n- Fixture headline"
    if zone == "Mars/Base":
        before = (store.root / "config.json").read_bytes()
        config = store.config()
        config["apps"]["news"]["digest"]["enabled"] = True
        with pytest.raises(IntegrationError, match="not a time zone"):
            save(service, store, {key: config[key] for key in ("notes", "notifications", "mcp", "apps")})
        assert (store.root / "config.json").read_bytes() == before


def test_builtin_news_and_custom_news_keep_separate_identity(world, monkeypatch):
    service, store = world
    config = store.config()
    config["apps"]["news"]["enabled"] = True
    server = {"id": "news", "name": "Existing custom news", "kind": "http", "enabled": True,
        "url": "https://custom.example/mcp", "command": "", "workingFolder": "", "envNames": [],
        "tools": [{"name": "read", "policy": "allow", "description": "Custom", "inputSchema": {"type": "object"}}]}
    server["checkedBinding"] = digest(server_context(server, store.secrets()))
    config["mcp"]["servers"] = [server]
    store.save_config(config)
    @contextmanager
    def session(_server):
        class Reply:
            def call(self, _name, _arguments):
                return "custom result", False
        yield Reply()
    monkeypatch.setattr(service, "_session", session)
    monkeypatch.setattr(service, "use_app", lambda *_args, **_kwargs:
        ("news-unread", {"text": "builtin result"}))
    names = [item["id"] for item in service.handle("agent", {"schemaVersion": 1, "action": "mcp-tools"})["servers"]]
    assert "news" in names and "app:news" in names
    custom = service.handle("agent", {"schemaVersion": 1, "action": "mcp-call", "server": "news", "tool": "read"})
    builtin = service.handle("agent", {"schemaVersion": 1, "action": "mcp-call", "server": "app:news", "tool": "unread"})
    assert (custom["server"], custom["text"]) == ("news", "custom result")
    assert (builtin["server"], builtin["text"]) == ("app:news", "builtin result")


def test_apps_and_ntfy_refuse_redirects_without_forwarding_credentials():
    leaked = []
    class Sink(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            leaked.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
        do_POST = do_GET
        def log_message(self, *_):
            pass
    sink = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    class Redirect(Sink):
        def do_GET(self):
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/stolen")
            self.end_headers()
        do_POST = do_GET
    source = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for server in (sink, source):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{source.server_port}"
        with pytest.raises(IntegrationError, match="redirected"):
            apps.documents_test({"url": url}, "synthetic-token")
        config = default_config()
        config["notifications"].update(enabled=True, url=url)
        with pytest.raises(IntegrationError, match="redirected"):
            ntfy.send_notification(config, {"ntfyPassword": "synthetic-password"}, "Fixture", "body")
        assert leaked == []
    finally:
        for server in (source, sink):
            server.shutdown()
            server.server_close()


def test_ntfy_distinguishes_preconnect_refusal_from_unknown_after_body():
    reservation = socket.socket()
    reservation.bind(("127.0.0.1", 0))
    port = reservation.getsockname()[1]
    reservation.close()
    config = default_config()
    config["notifications"].update(enabled=True, url=f"http://127.0.0.1:{port}")
    with pytest.raises(IntegrationError) as refused:
        ntfy.send_notification(config, {}, "Fixture", "body")
    assert refused.value.code == "notification-not-connected"
    received = []
    class DropAfterBody(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), DropAfterBody)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config["notifications"]["url"] = f"http://127.0.0.1:{server.server_port}"
        with pytest.raises(IntegrationError) as uncertain:
            ntfy.send_notification(config, {}, "Fixture", "body")
        assert uncertain.value.code not in ("notification-not-connected", "notification-refused")
        assert received == [b"body"]
    finally:
        server.shutdown()
        server.server_close()


def test_ntfy_auth_refusal_is_definitive():
    class Refuse(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(403)
            self.end_headers()
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Refuse)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = default_config()
        config["notifications"].update(enabled=True, url=f"http://127.0.0.1:{server.server_port}")
        with pytest.raises(IntegrationError) as refused:
            ntfy.send_notification(config, {}, "Fixture", "body")
        assert refused.value.code == "notification-refused"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("kind", ["document-hit", "document-content", "photo-city", "photo-exif", "news-origin", "news-links"])
def test_nested_unexpected_app_shapes_are_explained(kind, monkeypatch):
    settings = {"url": "https://app.example", "username": "fixture"}
    if kind == "document-hit":
        monkeypatch.setattr(apps, "request", lambda *_args, **_kwargs: {"results": [{"id": 1, "__search_hit__": "invalid"}]})
        operation = lambda: apps.documents_search(settings, "key", "fixture")
    elif kind == "document-content":
        monkeypatch.setattr(apps, "request", lambda *_args, **_kwargs: {"content": {"invalid": True}})
        operation = lambda: apps.documents_read(settings, "key", 1)
    elif kind == "photo-city":
        monkeypatch.setattr(apps, "_immich", lambda *_args, **_kwargs: [{"exifInfo": "invalid"}])
        operation = lambda: apps.photos_search(settings, "key", place="Lisbon")
    elif kind == "photo-exif":
        monkeypatch.setattr(apps, "_immich", lambda *_args, **_kwargs: {"assets": {"items": [{"id": "one", "exifInfo": {"city": 3}}]}})
        operation = lambda: apps.photos_search(settings, "key", after="2026-01-01")
    else:
        monkeypatch.setattr(apps, "_reader", lambda *_args, **_kwargs: ("https://app.example", {}))
        bad = {"origin": "invalid"} if kind == "news-origin" else {"canonical": "invalid"}
        monkeypatch.setattr(apps, "request", lambda *_args, **_kwargs: {"items": [bad]})
        operation = lambda: apps.unread_news(settings, "key")
    with pytest.raises(IntegrationError, match="did not answer as expected"):
        operation()


def test_numeric_overflow_is_a_refusal_not_a_service_crash(world):
    with pytest.raises(IntegrationError, match="price limit"):
        apps.price_limit(10**4000)
    _service, store = world
    context = store._watch_context()
    store._write("watch-state.json", {"schemaVersion": 2, "providers": {
        context: {"one": {"lastChanged": 1, "below": 10**4000, "lastPrice": None}}}})
    with pytest.raises(IntegrationError):
        store.watch_state()


@pytest.mark.parametrize("limit", [0, True, 4 * 1024 * 1024 + 1, float("inf")])
def test_http_limit_cannot_disable_bound(limit):
    import urllib.request
    with pytest.raises(IntegrationError, match="Invalid integration response limit"):
        safe_urlopen(urllib.request.Request("http://127.0.0.1:9"), max_response_bytes=limit)


def test_large_app_response_uses_explicit_cap_without_relaxing_mcp_default():
    import urllib.request
    body = b"x" * (300 * 1024)
    class Reply(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Reply)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/fixture")
        with pytest.raises(IntegrationError, match="too much data"):
            safe_urlopen(request)
        with safe_urlopen(request, max_response_bytes=4 * 1024 * 1024) as response:
            assert response.read() == body
    finally:
        server.shutdown()
        server.server_close()
