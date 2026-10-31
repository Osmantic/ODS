import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires Unix sockets; run under Linux/WSL")

import email.header
import http.server
import json
import pathlib
import tempfile
import threading
import time
import unittest
import unittest.mock
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import integrations_apps as apps  # noqa: E402
import integrations_ntfy as ntfy  # noqa: E402
import integrations_service as service  # noqa: E402
from integrations_common import Store, default_apps  # noqa: E402


class Fake(http.server.BaseHTTPRequestHandler):
    """Shared plumbing for the fake apps: JSON replies and a key check."""
    key_header, key = "", ""

    def reply(self, value, status=200, text=None):
        body = text.encode() if text is not None else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain" if text is not None else "application/json")
        self.end_headers()
        self.wfile.write(body)

    def allowed(self):
        if self.headers.get(self.key_header) != self.key:
            self.reply({"detail": "no"}, 401)
            return False
        return True

    def body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def log_message(self, *_):
        pass


class FakePaperless(Fake):
    key_header, key = "Authorization", "Token doc-token"
    unseen = 0
    documents = {1: {"title": "Car insurance policy", "created": "2026-03-02T00:00:00Z", "content": "Your car insurance covers glass."},
                 2: {"title": "Lease", "created": "2025-09-01T00:00:00Z", "content": "The deposit is two months of rent."}}

    def do_GET(self):
        if not self.allowed():
            return
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if url.path == "/api/documents/":
            words = query.get("query", [""])[0].lower().split()
            hits = [(number, doc) for number, doc in self.documents.items() if words and all(word in doc["content"].lower() for word in words)]
            results = [{"id": number, "title": doc["title"], "created": doc["created"], "content": doc["content"],
                        "__search_hit__": {"highlights": f"Your <span class=\"match\">{words[0]}</span> &amp; more"}} for number, doc in hits]
            # unseen stands for matches past the first page, which Paperless counts but does not send.
            return self.reply({"count": (len(results) + FakePaperless.unseen) if words else len(self.documents), "results": results})
        number = int(url.path.strip("/").split("/")[-1])
        if number not in self.documents:
            return self.reply({"detail": "Not found."}, 404)
        return self.reply({"id": number, **self.documents[number]})


class FakeImmich(Fake):
    key_header, key = "x-api-key", "photo-key"
    smart = False
    smart_fails = False
    next_page = None
    searches = []
    assets = [{"id": "a1", "originalFileName": "beach.jpg", "localDateTime": "2026-07-04T10:00:00.000Z",
               "exifInfo": {"city": "Lisbon", "country": "Portugal"}},
              {"id": "a2", "originalFileName": "snow.jpg", "localDateTime": "2026-01-10T10:00:00.000Z",
               "exifInfo": {"city": "Oslo", "country": "Norway"}}]

    def do_GET(self):
        if not self.allowed():
            return
        if self.path == "/api/server/features":
            return self.reply({"smartSearch": FakeImmich.smart})
        if self.path == "/api/users/me":
            return self.reply({"name": "Owner"})
        if self.path == "/api/search/cities":
            return self.reply([{"id": asset["id"], "exifInfo": asset["exifInfo"]} for asset in self.assets])
        self.reply({}, 404)

    def do_POST(self):
        if not self.allowed():
            return
        body = json.loads(self.body())
        FakeImmich.searches.append((self.path, body))
        if self.path == "/api/search/smart":
            if FakeImmich.smart_fails:
                return self.reply({"message": "Machine learning is not reachable"}, 500)
            items = self.assets[:1]
        else:
            items = [asset for asset in self.assets
                     if all(body.get(field, asset["exifInfo"][field]) == asset["exifInfo"][field] for field in ("city", "country"))
                     and body.get("takenAfter", "") <= asset["localDateTime"] <= body.get("takenBefore", "9999")]
        self.reply({"assets": {"items": items, "total": len(items), "nextPage": FakeImmich.next_page}})


class FakeChanges(Fake):
    key_header, key = "x-api-key", "watch-key"
    watches = {}
    prices = {}
    price_reads = []
    broken = set()  # Watches whose snapshot changedetection.io fails to read.

    def do_GET(self):
        if not self.allowed():
            return
        if self.path == "/api/v1/watch":
            return self.reply(self.watches)
        uuid = self.path.split("/")[4]
        if uuid not in self.watches:
            return self.reply({}, 404)
        if self.path.endswith("/history/latest"):  # As changedetection.io 0.60 writes a price watch's snapshot.
            FakeChanges.price_reads.append(uuid)
            if uuid in FakeChanges.broken:
                return self.reply({"message": "snapshot unreadable"}, 500)
            price = self.prices.get(uuid)
            return self.reply(None, text=f"In Stock: True - Price: {float(price)}") if price is not None else self.reply({}, 404)
        return self.reply(self.watches[uuid])

    def do_POST(self):
        if not self.allowed():
            return
        body = json.loads(self.body())
        uuid = f"w{len(self.watches) + 1}"
        self.watches[uuid] = {"url": body["url"], "title": body.get("title", ""), "last_changed": 0, "processor": body.get("processor", "text_json_diff")}
        self.reply({"uuid": uuid}, 201)

    def do_DELETE(self):
        if not self.allowed():
            return
        self.watches.pop(self.path.rsplit("/", 1)[1], None)
        self.reply({}, 204, text="")


class FakeReader(Fake):
    key_header, key = "Authorization", "GoogleLogin auth=tok123"
    items = [{"title": "Rain &amp; wind tomorrow", "canonical": [{"href": "https://news.example/rain"}], "origin": {"title": "Weather"},
              "summary": {"content": "<p>Bring a coat.</p>"}},
             {"title": "New library opens", "canonical": [{"href": "https://news.example/library"}], "origin": {"title": "City"},
              "summary": {"content": ""}}]

    def do_POST(self):
        form = urllib.parse.parse_qs(self.body().decode())
        if self.path.endswith("/accounts/ClientLogin") and form == {"Email": ["owner"], "Passwd": ["rss-pass"]}:
            return self.reply(None, text="SID=x\nLSID=x\nAuth=tok123\n")
        self.reply(None, 401, text="Error=BadAuthentication")

    def do_GET(self):
        if not self.allowed():
            return
        if "subscription/list" in self.path:
            return self.reply({"subscriptions": [{"id": "feed/1"}, {"id": "feed/2"}]})
        wanted = int(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)["n"][0])
        self.reply({"items": FakeReader.items[:wanted]})


class FakeNtfy(Fake):
    sent = []

    def do_POST(self):
        FakeNtfy.sent.append({"title": self.headers["Title"], "message": self.body().decode(), "priority": self.headers["Priority"],
                              "click": self.headers.get("Click"), "actions": self.headers.get("Actions")})
        self.reply({})


def serve(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = Store(pathlib.Path(self.temporary.name) / "state")
        self.service = service.Integrations(self.store)
        self.servers = {name: serve(handler) for name, handler in
                        (("documents", FakePaperless), ("photos", FakeImmich), ("watch", FakeChanges), ("news", FakeReader), ("ntfy", FakeNtfy))}
        for server in self.servers.values():
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
        FakeNtfy.sent.clear()
        FakeImmich.searches.clear()
        FakeImmich.smart, FakeImmich.next_page = False, None
        FakeChanges.watches, FakeChanges.prices, FakeChanges.price_reads, FakeChanges.broken = {}, {}, [], set()
        FakePaperless.unseen = 0
        self.addCleanup(setattr, FakeReader, "items", FakeReader.items)

    def url(self, name):
        return f"http://127.0.0.1:{self.servers[name].server_address[1]}"

    def owner(self, action, **fields):
        return self.service.handle("owner", {"schemaVersion": 1, "action": action, **fields})

    def agent(self, action, **fields):
        return self.service.handle("agent", {"schemaVersion": 1, "action": action, **fields})

    def app(self, server, tool, **arguments):
        """An app tool as Portal calls it through the MCP tool; the app's structured reply."""
        return self.service.use_app(self.store.config(), server, tool, arguments)[1]

    def settings(self, *, tasks="all", changes="ask", digest=False, notify=True, enabled=True):
        configured = default_apps()
        for app in apps.APPS:
            configured[app].update(enabled=enabled, url=self.url(app))
        configured["watch"]["changes"] = changes
        configured["news"]["username"] = "owner"
        configured["news"]["digest"] = {"enabled": digest, "time": "06:30", "timezone": "Europe/Lisbon"}
        return {"notifications": {"enabled": notify, "url": self.url("ntfy"), "topic": "portal", "username": "", "notifyApprovals": True,
                                  "approvalLink": "", "scheduledTasks": tasks},
                "notes": {"enabled": False, "vaultPath": "", "write": "ask"}, "mcp": {"servers": []}, "apps": configured}

    KEYS = {"apps": {"documents": "doc-token", "photos": "photo-key", "watch": "watch-key", "news": "rss-pass"}}

    def save(self, settings, secrets=None):
        revision = self.owner("get")["settings"]["revision"]
        return self.owner("save", expectedRevision=revision, settings=settings, secrets=self.KEYS if secrets is None else secrets)

    # Documents.
    def test_documents_are_searched_and_read_with_links(self):
        self.save(self.settings())
        found = self.app("documents", "search", query="car insurance")
        self.assertEqual([hit["id"] for hit in found["results"]], [1])
        self.assertEqual(found["results"][0]["snippet"], "Your car & more")
        self.assertEqual(found["results"][0]["created"], "2026-03-02")
        self.assertEqual(found["results"][0]["link"], f"{self.url('documents')}/documents/1/details")
        read = self.app("documents", "read", id=1)
        self.assertEqual((read["title"], read["text"]), ("Car insurance policy", "Your car insurance covers glass."))
        self.assertEqual(self.app("documents", "search", query="spaceship")["results"], [])
        for bad in ("one", "1.5", "-1", 0, True, None):
            with self.assertRaisesRegex(service.IntegrationError, "document id"):
                self.app("documents", "read", id=bad)
        self.assertEqual(self.app("documents", "read", id=" 1 ")["title"], "Car insurance policy")  # The model sends text.
        with self.assertRaisesRegex(service.IntegrationError, "no such item"):
            self.app("documents", "read", id=99)
        with self.assertRaisesRegex(service.IntegrationError, "words to look for"):
            self.app("documents", "search", query="  ")

    def test_app_failures_are_explained(self):
        self.save(self.settings(enabled=False))
        with self.assertRaisesRegex(service.IntegrationError, "Paperless-ngx is not set up"):
            self.app("documents", "search", query="lease")
        self.save(self.settings(), secrets={"apps": {"documents": "wrong"}})
        with self.assertRaisesRegex(service.IntegrationError, "refused the token"):
            self.app("documents", "search", query="lease")
        broken = self.settings()
        broken["apps"]["documents"]["url"] = "http://127.0.0.1:9"
        self.save(broken, secrets={})
        with self.assertRaisesRegex(service.IntegrationError, r"Paperless-ngx at http://127.0.0.1:9 could not be reached"):
            self.app("documents", "search", query="lease")
        wrong_app = self.settings()
        wrong_app["apps"]["documents"]["url"] = self.url("ntfy")  # Answers, but not as Paperless does.
        self.save(wrong_app, secrets={})
        with self.assertRaisesRegex(service.IntegrationError, "did not answer as expected|answered 501"):
            self.app("documents", "search", query="lease")

    # Photos.
    def test_photos_by_place_and_date_without_machine_learning(self):
        self.save(self.settings())
        found = self.app("photos", "search", place="lisbon")
        self.assertEqual((found["mode"], [photo["file"] for photo in found["results"]]), ("details", ["beach.jpg"]))
        self.assertEqual(found["results"][0], {"id": "a1", "file": "beach.jpg", "taken": "2026-07-04", "place": "Lisbon, Portugal",
                                               "link": f"{self.url('photos')}/photos/a1"})
        self.assertEqual(FakeImmich.searches[-1][1]["city"], "Lisbon")
        self.assertEqual([photo["file"] for photo in self.app("photos", "search", place="Norway")["results"]], ["snow.jpg"])
        self.assertEqual([p["file"] for p in self.app("photos", "search", after="2026-06-01", before="2026-12-31")["results"]], ["beach.jpg"])
        self.assertIn("No photos are from a place named Mars", self.app("photos", "search", place="Mars")["note"])
        with self.assertRaisesRegex(service.IntegrationError, "machine learning"):
            self.app("photos", "search", query="dog on the beach")
        with self.assertRaisesRegex(service.IntegrationError, "YYYY-MM-DD"):
            self.app("photos", "search", after="last summer")
        with self.assertRaisesRegex(service.IntegrationError, "what is in the photos"):
            self.app("photos", "search")

    def test_photos_by_description_when_immich_can(self):
        FakeImmich.smart = True
        self.save(self.settings())
        found = self.app("photos", "search", query="dog on the beach")
        self.assertEqual((found["mode"], FakeImmich.searches[-1]), ("description", ("/api/search/smart", {"query": "dog on the beach", "size": 10})))
        FakeImmich.smart_fails = True  # Turned on, but its machine learning is not running.
        self.addCleanup(setattr, FakeImmich, "smart_fails", False)
        with self.assertRaisesRegex(service.IntegrationError, "machine learning may not be running"):
            self.app("photos", "search", query="dog on the beach")

    # Watched pages.
    def test_watching_a_page_asks_first_then_notifies_on_changes(self):
        self.save(self.settings(changes="ask"))
        waiting = self.app("watch", "add", url="https://shop.example/page", title="Shop")
        self.assertEqual(waiting["status"], "approval-required")
        self.assertEqual(FakeChanges.watches, {})
        self.owner("decide", approvalId=waiting["approvalId"], decision="approve")
        added = self.app("watch", "add", url="https://shop.example/page", title="Shop")
        self.assertEqual(added["id"], "w1")
        self.assertEqual(self.app("watch", "list"), {"watches": [{"id": "w1", "title": "Shop", "url": "https://shop.example/page",
                                                                  "lastChanged": 0, "below": None, "lastPrice": None}], "total": 1})
        FakeNtfy.sent.clear()
        FakeChanges.watches["w1"]["last_changed"] = 100  # Its first snapshot: only remembered.
        self.service.check_watches()
        self.assertEqual(FakeNtfy.sent, [])
        FakeChanges.watches["w1"]["last_changed"] = 200
        self.service.check_watches()
        self.service.check_watches()
        self.assertEqual([(note["title"], note["click"], note["actions"]) for note in FakeNtfy.sent],
                         [("Page changed: Shop", "https://shop.example/page", 'view, Open, "https://shop.example/page"')])
        for bad in ("ftp://x", "shop.example", "https://" + "a" * 2000):
            with self.assertRaisesRegex(service.IntegrationError, "full web address"):
                self.app("watch", "add", url=bad)
        with self.assertRaisesRegex(service.IntegrationError, "List the watches first"):
            self.app("watch", "remove", id="nope")

    def test_a_price_watch_notifies_once_at_or_under_its_limit(self):
        self.save(self.settings(changes="allow"))
        self.app("watch", "add", url="https://shop.example/kettle", title="Kettle", below=50)
        self.assertEqual(FakeChanges.watches["w1"]["processor"], "restock_diff")
        notes = []
        for price in (60, 49.5, 45, 55, 50):
            FakeChanges.prices["w1"] = price
            FakeChanges.watches["w1"]["last_changed"] += 10
            self.service.check_watches()
            notes.append(len(FakeNtfy.sent))
        self.assertEqual(notes, [0, 1, 1, 1, 2])
        self.assertEqual(FakeNtfy.sent[0]["message"], "Kettle is now 49.5, at or under your limit of 50.")
        self.assertEqual(self.app("watch", "list")["watches"][0]["below"], 50.0)
        for bad in (0, -3, "cheap", True):
            with self.assertRaisesRegex(service.IntegrationError, "price limit"):
                self.app("watch", "add", url="https://shop.example/x", below=bad)
        self.assertEqual(self.app("watch", "remove", id="w1")["done"], "Stop watching Kettle")
        self.assertEqual((FakeChanges.watches, self.store.watch_state()), ({}, {}))

    def test_any_page_title_and_address_reach_the_phone_intact(self):
        self.save(self.settings())
        # An en dash is outside Latin-1: sent raw, it would stop the watcher with UnicodeEncodeError.
        FakeChanges.watches["x1"] = {"url": "https://shop.example/café–menu?ids=1,2", "title": "Café – menu 🍰", "last_changed": 5}
        self.service.check_watches()
        FakeChanges.watches["x1"]["last_changed"] = 6
        self.service.check_watches()
        note = FakeNtfy.sent[-1]
        self.assertEqual(str(email.header.make_header(email.header.decode_header(note["title"]))), "Page changed: Café – menu 🍰")
        self.assertEqual(note["click"], "https://shop.example/caf%C3%A9%E2%80%93menu?ids=1,2")
        self.assertEqual(note["actions"], 'view, Open, "https://shop.example/caf%C3%A9%E2%80%93menu?ids=1,2"')
        self.assertEqual(note["message"], "Café – menu 🍰 changed.")

    def test_header_values_stay_readable(self):
        self.assertEqual(ntfy.header_text("Plain title"), "Plain title")
        self.assertEqual(ntfy.header_link('https://x.example/a b"c?q=1,2#top'), "https://x.example/a%20b%22c?q=1,2#top")
        self.assertEqual(ntfy.header_link("https://x.example/a%20b"), "https://x.example/a%20b")  # Already encoded stays.

    def test_a_price_already_under_the_limit_notifies_at_the_first_look(self):
        self.save(self.settings(changes="allow"))
        self.app("watch", "add", url="https://shop.example/lamp", title="Lamp", below=30)
        FakeChanges.prices["w1"] = 25
        self.service.check_watches()
        self.assertEqual([note["title"] for note in FakeNtfy.sent], ["Price drop: Lamp"])

    def test_watches_added_elsewhere_are_remembered_before_they_notify(self):
        self.save(self.settings())
        FakeChanges.watches["x9"] = {"url": "https://blog.example", "title": "", "last_changed": 500}
        self.service.check_watches()
        FakeChanges.watches["x9"]["last_changed"] = 600
        self.service.check_watches()
        self.assertEqual([note["title"] for note in FakeNtfy.sent], ["Page changed: https://blog.example"])

    def test_the_page_watcher_logs_a_stopped_app_and_keeps_going(self):
        broken = self.settings()
        broken["apps"]["watch"]["url"] = "http://127.0.0.1:9"
        self.save(broken)
        stop = threading.Event()
        watcher = threading.Thread(target=service.watch_pages, args=(self.service, stop, 0.01))
        watcher.start()
        deadline = time.monotonic() + 10
        while not any(entry["action"] == "watch-check" for entry in self.store.activity()) and time.monotonic() < deadline:
            time.sleep(0.01)
        stop.set()
        watcher.join(5)
        self.assertFalse(watcher.is_alive())
        self.assertIn("could not be reached", next(entry for entry in self.store.activity() if entry["action"] == "watch-check")["outcome"])

    def test_an_unexpected_watcher_error_ends_the_service_for_a_restart(self):
        class Broken:
            def check_watches(self):
                raise RuntimeError("a bug")
        exits = []
        with unittest.mock.patch("traceback.print_exc"):
            watcher = threading.Thread(target=service.watch_or_exit, args=(Broken(), threading.Event(), 0.01),
                                       kwargs={"exit": exits.append})
            watcher.start()
            watcher.join(5)
        self.assertEqual(exits, [1])

    def test_a_watch_added_during_a_look_keeps_its_price_limit(self):
        self.save(self.settings(changes="allow"))
        original = apps.watches

        def slow_listing(settings, key):
            listing = original(settings, key)
            self.app("watch", "add", url="https://shop.example/late", title="Late", below=10)  # Added meanwhile.
            return listing
        apps.watches = slow_listing
        self.addCleanup(setattr, apps, "watches", original)
        self.service.check_watches()
        self.assertEqual(self.store.watch_state()["w1"]["below"], 10.0)

    def test_watching_is_quiet_while_the_app_is_off_or_has_no_key(self):
        self.save(self.settings(enabled=False))
        self.service.check_watches()  # Would fail if it reached the fake without a key.
        self.save(self.settings(), secrets={"apps": {"watch": ""}})
        self.service.check_watches()
        self.assertEqual(FakeNtfy.sent, [])

    # News and the digest.
    def test_news_digest_groups_unread_stories_by_feed(self):
        self.save(self.settings())
        reply = self.app("news", "digest")
        self.assertEqual(reply["text"], "Weather:\n- Rain & wind tomorrow (https://news.example/rain)\n\n"
                                        "City:\n- New library opens (https://news.example/library)")
        self.assertEqual(reply["items"][0]["summary"], "Bring a coat.")
        self.save(self.settings(), secrets={"apps": {"news": "wrong"}})
        with self.assertRaisesRegex(service.IntegrationError, "FreshRSS refused"):
            self.app("news", "digest")

    def test_digest_plan_follows_the_owner_settings(self):
        self.save(self.settings(digest=True))
        plan = self.agent("digest-plan")["plan"]
        self.assertEqual({key: plan[key] for key in ("enabled", "name", "expr", "tz")},
                         {"enabled": True, "name": "Daily news digest", "expr": "30 6 * * *", "tz": "Europe/Lisbon"})
        self.assertIn('"server": "news", "tool": "digest"', plan["message"])
        self.assertEqual((plan["timeoutSeconds"], plan["tools"]), (600, ["pixel_ods_mcp"]))
        self.save(self.settings(digest=False))
        self.assertFalse(self.agent("digest-plan")["plan"]["enabled"])

    # Scheduled tasks.
    def test_finished_tasks_notify_as_the_owner_chose(self):
        expected = {"all": [True, True, False], "failures": [False, True, False], "off": [False, False, False]}
        for mode, outcome in expected.items():
            self.save(self.settings(tasks=mode))
            done = {"jobId": f"plants-{mode}", "name": "Water plants", "status": "ok", "summary": "Reminded you.", "runAtMs": 1}
            failed = {"jobId": f"backup-{mode}", "name": "Backup", "status": "error", "error": "Disk full", "runAtMs": 1}
            skipped = {"jobId": f"other-{mode}", "name": "Backup", "status": "skipped", "runAtMs": 1}
            self.assertEqual([self.agent("task-finished", **event)["notified"] for event in (done, failed, skipped)], outcome, mode)
        self.assertEqual(FakeNtfy.sent[:2], [
            {"title": "Scheduled task done: Water plants", "message": "Reminded you.", "priority": "default", "click": None, "actions": None},
            {"title": "Scheduled task failed: Backup", "priority": "high", "click": None, "actions": None,
             "message": "Disk full\n\nOpenClaw may try it again. You hear from Portal again when it works."}])
        self.save(self.settings(tasks="all", notify=False))
        self.assertFalse(self.agent("task-finished", jobId="plants", name="Water plants", status="ok", runAtMs=1)["notified"])

    def test_a_retried_failure_notifies_once_and_its_recovery_once(self):
        self.save(self.settings(tasks="failures"))
        failed = {"jobId": "j1", "name": "Backup", "status": "error", "error": "Disk full", "runAtMs": 1}
        # OpenClaw retries a failed run a few times within minutes.
        self.assertEqual([self.agent("task-finished", **failed)["notified"] for _ in range(4)], [True, False, False, False])
        self.assertTrue(self.agent("task-finished", jobId="j1", name="Backup", status="ok", summary="Backed up.", runAtMs=2)["notified"])
        self.assertFalse(self.agent("task-finished", jobId="j1", name="Backup", status="ok", summary="Backed up.", runAtMs=3)["notified"])
        self.assertTrue(self.agent("task-finished", **failed)["notified"])  # A new failure after it worked again.
        self.assertEqual([note["title"] for note in FakeNtfy.sent],
                         ["Scheduled task failed: Backup", "Scheduled task works again: Backup", "Scheduled task failed: Backup"])
        # Another task failing at the same time is told on its own.
        self.assertTrue(self.agent("task-finished", jobId="j2", name="Report", status="error", error="x", runAtMs=1)["notified"])
        # A skipped run does not end a failure streak.
        self.agent("task-finished", jobId="j2", name="Report", status="skipped", runAtMs=2)
        self.assertFalse(self.agent("task-finished", jobId="j2", name="Report", status="error", error="x", runAtMs=3)["notified"])

    def test_task_notice_is_a_pure_choice(self):
        failed, done = {"name": "T", "status": "error", "error": "e"}, {"name": "T", "status": "ok", "summary": "s"}
        self.assertEqual(apps.task_notice("all", done, False)["title"], "Scheduled task done: T")
        self.assertEqual(apps.task_notice("all", done, True)["title"], "Scheduled task works again: T")
        self.assertIsNone(apps.task_notice("failures", done, False))
        self.assertIsNone(apps.task_notice("all", failed, True))
        self.assertIsNone(apps.task_notice("off", failed, False))
        self.assertIsNone(apps.task_notice("all", {"name": "T", "status": "skipped"}, False))
        self.assertEqual(apps.task_notice("all", {"status": "error"}, False)["title"], "Scheduled task failed: A scheduled task")
        self.assertLessEqual(len(apps.task_notice("all", {"name": "T", "status": "error", "error": "x" * 9000}, False)["message"]), 1600)

    def test_the_digest_task_sends_portal_s_summary_or_the_plain_list(self):
        self.save(self.settings(digest=True, tasks="off"))
        self.app("news", "digest")  # Portal asked for the stories in this run.
        self.assertTrue(self.agent("task-finished", name="Daily news digest", description=apps.DIGEST_MARKER, status="ok", summary="Rain tomorrow; a library opens.",
                                   runAtMs=1000)["notified"])
        self.assertEqual(FakeNtfy.sent[-1]["message"], "Rain tomorrow; a library opens.")
        # A run where Portal never called the news tool still delivers the stories.
        self.agent("task-finished", name="Daily news digest", description=apps.DIGEST_MARKER, status="ok", summary="I could not find any news.", runAtMs=9_999_999_999_999)
        self.assertIn("Rain & wind tomorrow", FakeNtfy.sent[-1]["message"])
        self.agent("task-finished", name="Daily news digest", description=apps.DIGEST_MARKER, status="error", error="model failed", runAtMs=1)
        self.assertIn("New library opens", FakeNtfy.sent[-1]["message"])
        self.assertEqual({note["title"] for note in FakeNtfy.sent}, {"Daily news digest"})
        # The owner's own task that happens to have the same name is an ordinary task.
        self.agent("task-finished", name="Daily news digest", status="ok", summary="Mine.", runAtMs=1)
        self.save(self.settings(digest=True, tasks="all"))
        self.agent("task-finished", name="Daily news digest", status="ok", summary="Mine.", runAtMs=1)
        self.assertEqual(FakeNtfy.sent[-1]["title"], "Scheduled task done: Daily news digest")

    # Testing and settings.
    def test_each_app_test_says_what_answered(self):
        self.save(self.settings())
        notices = {app: self.owner("test-app", app=app)["notice"] for app in apps.APPS}
        self.assertEqual(notices, {"documents": "Paperless-ngx answered: 2 documents.",
                                   "photos": "Immich answered as Owner. Searching by place and date works. To also search by what is "
                                             "in a photo, turn on Smart Search in Immich under Administration → Settings → Machine Learning.",
                                   "watch": "changedetection.io answered: 0 watched pages.", "news": "FreshRSS answered: 2 feeds."})
        with self.assertRaisesRegex(service.IntegrationError, "Unknown app"):
            self.owner("test-app", app="mail")
        self.save(self.settings(), secrets={"apps": {"photos": ""}})
        with self.assertRaisesRegex(service.IntegrationError, "Save the Immich token first"):
            self.owner("test-app", app="photos")
        with self.assertRaises(service.IntegrationError):
            self.agent("test-app", app="news")

    def test_app_secrets_are_kept_hidden_and_validated(self):
        self.save(self.settings())
        view = self.owner("get")["settings"]["apps"]
        self.assertEqual({app: view[app]["hasKey"] for app in apps.APPS}, dict.fromkeys(apps.APPS, True))
        self.assertNotIn("doc-token", json.dumps(self.owner("get")))
        self.save(self.settings(), secrets={"apps": {"photos": ""}})  # An empty value removes the key.
        self.assertFalse(self.owner("get")["settings"]["apps"]["photos"]["hasKey"])
        with self.assertRaisesRegex(service.IntegrationError, "app tokens"):
            self.save(self.settings(), secrets={"apps": {"mail": "x"}})
        with self.assertRaisesRegex(service.IntegrationError, "passwords and tokens"):
            self.save(self.settings(), secrets={"documents": "x"})

    def test_app_settings_are_checked(self):
        cases = [(lambda s: s["apps"]["news"]["digest"].update(time="7am"), "HH:MM"),
                 (lambda s: s["apps"]["news"]["digest"].update(timezone="Europe/Lisbon; rm"), "HH:MM"),
                 (lambda s: s["apps"]["watch"].update(changes="off"), "ask first or allowed"),
                 (lambda s: s["apps"]["news"].update(username=""), "FreshRSS user"),
                 (lambda s: s["apps"]["photos"].update(url="file:///etc"), "Immich address"),
                 (lambda s: s["apps"].pop("photos"), "Check the apps"),
                 (lambda s: s["apps"]["documents"].update(extra=1), "Paperless-ngx settings"),
                 (lambda s: s["notifications"].update(scheduledTasks="sometimes"), "notification settings")]
        for change, message in cases:
            settings = self.settings()
            change(settings)
            with self.assertRaisesRegex(service.IntegrationError, message):
                self.save(settings)

    def test_page_notices_is_a_pure_comparison(self):
        listing = {"a": {"url": "https://a", "title": "A", "last_changed": 5}}
        notices, kept = apps.page_notices(listing, {"a": {"lastChanged": 5, "below": None, "lastPrice": None}}, {})
        self.assertEqual((notices, kept), ([], {"a": {"lastChanged": 5, "below": None, "lastPrice": None}}))
        notices, _ = apps.page_notices({"a": {**listing["a"], "last_changed": 6}}, kept, {})
        self.assertEqual(notices, [{"title": "Page changed: A", "message": "A changed.", "link": "https://a"}])
        self.assertEqual(apps.page_notices({}, kept, {}), ([], {}))  # A removed watch is forgotten.
        odd = {"a": {"url": "javascript:alert(1)", "title": "Odd", "last_changed": 7}}
        self.assertEqual(apps.page_notices(odd, kept, {})[0], [{"title": "Page changed: Odd", "message": "Odd changed.", "link": ""}])
        self.assertEqual([apps.count(n, "feed") for n in (0, 1, 2)], ["0 feeds", "1 feed", "2 feeds"])

    # The apps as Portal sees them: built-in servers in its MCP tool.
    def test_enabled_apps_are_listed_next_to_mcp_servers(self):
        self.save(self.settings())
        servers = self.agent("mcp-tools")["servers"]
        self.assertEqual([server["id"] for server in servers], ["documents", "photos", "watch", "news"])
        watch = next(server for server in servers if server["id"] == "watch")
        self.assertEqual({tool["name"]: tool["needsApproval"] for tool in watch["tools"]}, {"list": False, "add": True, "remove": True})
        self.save(self.settings(enabled=False))
        self.assertEqual(self.agent("mcp-tools")["servers"], [])

    def test_calling_an_app_returns_text_for_portal(self):
        self.save(self.settings())
        reply = self.agent("mcp-call", server="documents", tool="search", arguments={"query": "car insurance"})
        self.assertEqual((reply["server"], reply["isError"]), ("documents", False))
        self.assertTrue(reply["text"].startswith("Document 1: Car insurance policy (2026-03-02) — Your car & more\n"))
        self.assertIn("Lisbon, Portugal", self.agent("mcp-call", server="photos", tool="search", arguments={"place": "Lisbon"})["text"])
        self.assertIn("Weather:\n- Rain & wind", self.agent("mcp-call", server="news", tool="digest", arguments={})["text"])
        waiting = self.agent("mcp-call", server="watch", tool="add", arguments={"url": "https://shop.example/x"})
        self.assertEqual(waiting["status"], "approval-required")
        with self.assertRaisesRegex(service.IntegrationError, "no tool named delete"):
            self.agent("mcp-call", server="documents", tool="delete", arguments={})
        # Unknown argument names are dropped, not passed to the app.
        self.assertIn("No document", self.agent("mcp-call", server="documents", tool="search", arguments={"query": "spaceship", "x": 1})["text"])

    def test_custom_servers_cannot_take_an_app_id(self):
        settings = self.settings()
        settings["mcp"]["servers"] = [{"id": "news", "name": "News", "kind": "http", "enabled": False, "url": "http://127.0.0.1:9/mcp"}]
        with self.assertRaisesRegex(service.IntegrationError, "belong to the apps"):
            self.save(settings)

    # Many results.
    def test_many_documents_say_how_many_more_match(self):
        self.save(self.settings())
        FakePaperless.unseen = 41
        found = self.app("documents", "search", query="insurance")
        self.assertEqual(found["total"], 42)
        text = self.agent("mcp-call", server="documents", tool="search", arguments={"query": "insurance"})["text"]
        self.assertIn("Showing 1 of 42 documents. Use more specific words to find the others.", text)
        FakePaperless.unseen = 0
        self.assertNotIn("Showing", self.agent("mcp-call", server="documents", tool="search", arguments={"query": "insurance"})["text"])

    def test_many_photos_say_more_match(self):
        self.save(self.settings())
        FakeImmich.next_page = "2"
        self.assertTrue(self.app("photos", "search", place="Lisbon")["more"])
        self.assertIn("Showing the first 1; more photos match.", self.agent("mcp-call", server="photos", tool="search",
                                                                           arguments={"place": "Lisbon"})["text"])
        FakeImmich.next_page = None
        self.assertFalse(self.app("photos", "search", place="Lisbon")["more"])
        many = [{**FakeImmich.assets[0], "id": f"p{number}"} for number in range(apps.MAX_HITS + 3)]
        original = FakeImmich.assets
        FakeImmich.assets = many
        self.addCleanup(setattr, FakeImmich, "assets", original)
        found = self.app("photos", "search", place="Lisbon")
        self.assertEqual((len(found["results"]), found["more"]), (apps.MAX_HITS, True))

    def test_many_watches_say_how_many_are_shown(self):
        self.save(self.settings())
        FakeChanges.watches = {f"w{n}": {"url": f"https://shop.example/{n}", "title": f"Item {n}", "last_changed": 0}
                               for n in range(apps.MAX_WATCHES + 5)}
        listed = self.app("watch", "list")
        self.assertEqual((len(listed["watches"]), listed["total"]), (apps.MAX_WATCHES, apps.MAX_WATCHES + 5))
        self.assertIn(f"Showing {apps.MAX_WATCHES} of {apps.MAX_WATCHES + 5} watched pages.",
                      self.agent("mcp-call", server="watch", tool="list", arguments={})["text"])

    # Immich's test explains search by what is in a photo.
    def test_the_immich_test_says_what_searches_work(self):
        self.save(self.settings())
        FakeImmich.smart = True
        self.assertEqual(self.owner("test-app", app="photos")["notice"],
                         "Immich answered as Owner. Searching by place, date and what is in a photo works.")
        FakeImmich.smart_fails = True
        self.addCleanup(setattr, FakeImmich, "smart_fails", False)
        self.assertEqual(self.owner("test-app", app="photos")["notice"],
                         "Immich answered as Owner. Searching by place and date works. Searching by what is in a photo needs "
                         "Immich's machine learning, which is not running. Start Immich's machine-learning service to use it.")

    def test_an_app_can_be_tested_before_it_is_turned_on(self):
        self.save(self.settings(enabled=False))
        self.assertEqual(self.owner("test-app", app="news")["notice"], "FreshRSS answered: 2 feeds.")
        self.assertEqual(self.agent("mcp-tools")["servers"], [])  # Still off for Portal.

    # The digest as a brief.
    def stories(self, number, feeds=("Weather", "City")):
        return [{"title": f"Story {n}", "canonical": [{"href": f"https://news.example/{n}"}],
                 "origin": {"title": feeds[n % len(feeds)]}, "summary": {"content": ""}} for n in range(number)]

    def test_the_brief_groups_feeds_and_says_how_many_more(self):
        items = [{"feed": feed, "title": title, "link": "https://x", "summary": ""}
                 for feed, title in (("A", "a1"), ("B", "b1"), ("A", "a2"), ("", "n1"))]
        self.assertEqual(apps.digest_brief(items), "A:\n- a1\n- a2\n\nB:\n- b1\n\nNews:\n- n1")
        self.assertEqual(apps.digest_text(items[:3]), "A:\n- a1 (https://x)\n- a2 (https://x)\n\nB:\n- b1 (https://x)")
        many = [{**items[0], "title": f"t{n}"} for n in range(20)]
        brief = apps.digest_brief(many)
        self.assertEqual(brief.count("\n- "), apps.MAX_BRIEF_STORIES)
        self.assertTrue(brief.endswith("\n\n…and 5 more in FreshRSS."))
        self.assertTrue(apps.digest_brief(many * 2, capped=True).endswith("…and 25+ more in FreshRSS."))
        self.assertEqual(apps.digest_brief([]), "No unread news.")

    def test_a_long_brief_fits_one_ntfy_message(self):
        items = [{"feed": "Ünïcödé feed", "title": "é" * 300 + f" {n}", "link": "", "summary": ""} for n in range(15)]
        brief = apps.digest_brief(items)
        self.assertLessEqual(len(brief.encode()), apps.MAX_NOTICE_BYTES)
        self.assertTrue(brief.endswith(" …"))
        self.assertEqual(apps.fit_notice("short"), "short")
        self.assertLessEqual(len(apps.fit_notice("x" * 10000).encode()), apps.MAX_NOTICE_BYTES)  # No space to cut at.

    def test_the_scheduled_digest_sends_a_brief(self):
        FakeReader.items = self.stories(apps.MAX_NEWS_ITEMS)
        self.save(self.settings(digest=True, tasks="off"))
        self.agent("task-finished", jobId="d", name="Daily news digest", description=apps.DIGEST_MARKER, status="error", error="slow", runAtMs=1)
        message = FakeNtfy.sent[-1]["message"]
        self.assertEqual(message.count("\n- "), apps.MAX_BRIEF_STORIES)
        self.assertEqual((message.count("Weather:"), message.count("City:")), (1, 1))  # Grouped, though the stories alternate.
        self.assertTrue(message.endswith("…and 25+ more in FreshRSS."))
        self.assertNotIn("https://", message)
        self.app("news", "digest")
        self.agent("task-finished", jobId="d", name="Daily news digest", description=apps.DIGEST_MARKER, status="ok",
                   summary="Big news. " * 1000, runAtMs=1)
        self.assertLessEqual(len(FakeNtfy.sent[-1]["message"].encode()), apps.MAX_NOTICE_BYTES)
        self.assertIn("at most 15 stories, without links", apps.DIGEST_PROMPT)

    def test_a_digest_can_be_sent_now(self):
        self.save(self.settings())
        self.assertEqual(self.owner("send-digest")["notice"], "Digest sent with 2 stories.")
        self.assertEqual((FakeNtfy.sent[-1]["title"], FakeNtfy.sent[-1]["message"]),
                         ("Daily news digest", "Weather:\n- Rain & wind tomorrow\n\nCity:\n- New library opens"))
        FakeReader.items = []
        self.assertEqual(self.owner("send-digest")["notice"], "Digest sent: there is no unread news.")
        FakeReader.items = self.stories(1)
        self.assertEqual(self.owner("send-digest")["notice"], "Digest sent with 1 story.")
        self.save(self.settings(notify=False))
        with self.assertRaisesRegex(service.IntegrationError, "Turn notifications on"):
            self.owner("send-digest")
        self.save(self.settings(enabled=False))
        with self.assertRaisesRegex(service.IntegrationError, "FreshRSS is not set up"):
            self.owner("send-digest")
        with self.assertRaisesRegex(service.IntegrationError, "Only the owner"):
            self.agent("send-digest")

    # Watched pages in Settings, and fewer price reads.
    def test_settings_lists_the_watches_with_their_last_price(self):
        self.save(self.settings(changes="allow"))
        self.app("watch", "add", url="https://shop.example/kettle", title="Kettle", below=50)
        self.app("watch", "add", url="https://shop.example/hours", title="Hours")
        FakeChanges.prices["w1"] = 55
        self.service.check_watches()
        listed = self.owner("watch-list")["watches"]
        self.assertEqual([(w["title"], w["below"], w["lastPrice"]) for w in listed["watches"]], [("Kettle", 50.0, 55.0), ("Hours", None, None)])
        self.assertIn("(notifies when the price is at or under 50; last seen at 55)",
                      self.agent("mcp-call", server="watch", tool="list", arguments={})["text"])
        self.save(self.settings(), secrets={"apps": {"watch": ""}})
        with self.assertRaisesRegex(service.IntegrationError, "Save the changedetection.io API key first"):
            self.owner("watch-list")
        self.save(self.settings(enabled=False))
        with self.assertRaisesRegex(service.IntegrationError, "changedetection.io is not set up"):
            self.owner("watch-list")

    def test_a_price_is_read_only_when_the_watch_changed(self):
        self.save(self.settings(changes="allow"))
        self.app("watch", "add", url="https://shop.example/kettle", title="Kettle", below=50)
        self.service.check_watches()  # No snapshot yet: read, nothing found.
        FakeChanges.prices["w1"] = 60
        self.service.check_watches()  # Still no price known: read again.
        self.service.check_watches()  # Priced and unchanged: not read.
        FakeChanges.watches["w1"]["last_changed"] = 50
        FakeChanges.prices["w1"] = 40
        self.service.check_watches()  # Changed: read, and notifies.
        self.service.check_watches()
        self.assertEqual(FakeChanges.price_reads, ["w1", "w1", "w1"])
        self.assertEqual([note["title"] for note in FakeNtfy.sent], ["Price drop: Kettle"])

    # Bugs found end to end.
    def test_a_watch_that_cannot_be_read_does_not_silence_the_others(self):
        self.save(self.settings(changes="allow"))
        self.app("watch", "add", url="https://shop.example/kettle", title="Kettle", below=50)
        FakeChanges.watches["p1"] = {"url": "https://blog.example", "title": "Blog", "last_changed": 5}
        FakeChanges.broken.add("w1")
        self.service.check_watches()
        FakeChanges.watches["p1"]["last_changed"] = 6
        FakeChanges.watches["w1"]["last_changed"] = 6
        FakeChanges.prices["w1"] = 40
        self.service.check_watches()
        self.assertEqual([note["title"] for note in FakeNtfy.sent], ["Page changed: Blog"])
        self.assertIn("answered 500", next(entry for entry in self.store.activity() if entry["target"] == "Kettle")["outcome"])
        FakeChanges.broken.clear()  # Readable again: the change it missed still notifies.
        self.service.check_watches()
        self.assertEqual([note["title"] for note in FakeNtfy.sent], ["Page changed: Blog", "Price drop: Kettle"])

    def test_a_digest_that_cannot_be_made_says_why(self):
        self.save(self.settings(digest=True, tasks="off"))
        broken = self.settings(digest=True, tasks="off")
        broken["apps"]["news"]["url"] = "http://127.0.0.1:9"
        self.save(broken, secrets={})
        self.assertTrue(self.agent("task-finished", jobId="d", name="Daily news digest", description=apps.DIGEST_MARKER,
                                   status="error", error="model failed", runAtMs=1)["notified"])
        self.assertEqual((FakeNtfy.sent[-1]["message"], FakeNtfy.sent[-1]["priority"]),
                         ("Today's digest could not be made. FreshRSS at http://127.0.0.1:9 could not be reached.", "high"))

    def test_the_digest_time_zone_must_exist(self):
        settings = self.settings(digest=True)
        settings["apps"]["news"]["digest"]["timezone"] = "Mars/Base"
        with self.assertRaisesRegex(service.IntegrationError, "Mars/Base is not a time zone"):
            self.save(settings)
        settings["apps"]["news"]["digest"].update(enabled=False)  # Only checked while the digest is on.
        self.save(settings)
        for zone in ("UTC", "Europe/Lisbon", "America/Argentina/Buenos_Aires", "Etc/GMT+3"):
            self.assertTrue(apps.known_timezone(zone), zone)
        for zone in ("Mars/Base", "Europe/Lisbonn", "../../etc/passwd", "/etc/localtime"):
            self.assertFalse(apps.known_timezone(zone), zone)

    def test_photo_dates_must_be_real_and_in_order(self):
        self.save(self.settings())
        with self.assertRaisesRegex(service.IntegrationError, "2026-02-30 is not a date in the calendar"):
            self.app("photos", "search", after="2026-02-30")
        with self.assertRaisesRegex(service.IntegrationError, "must not come after"):
            self.app("photos", "search", after="2026-03-01", before="2026-01-01")
        self.assertEqual([p["file"] for p in self.app("photos", "search", after="2026-01-10", before="2026-01-10")["results"]], ["snow.jpg"])
        self.assertEqual(FakeImmich.searches[-1][1]["takenBefore"], "2026-01-10T23:59:59.999Z")

    def test_a_price_limit_given_as_text_is_understood(self):
        self.save(self.settings(changes="allow"))
        self.app("watch", "add", url="https://shop.example/kettle", title="Kettle", below=" 49.99 ")
        self.assertEqual(self.store.watch_state()["w1"]["below"], 49.99)
        self.app("watch", "add", url="https://shop.example/page", title="Page", below="")  # Empty: an ordinary page watch.
        self.assertIsNone(self.store.watch_state()["w2"]["below"])
        for bad in ("cheap", "NaN", "inf", "-5", "0", float("nan"), float("inf"), [50]):
            with self.assertRaisesRegex(service.IntegrationError, "price limit", msg=repr(bad)):
                self.app("watch", "add", url="https://shop.example/x", below=bad)

    def test_a_place_with_its_country_finds_the_city(self):
        self.save(self.settings())
        found = self.app("photos", "search", place="Lisbon, Portugal")
        self.assertEqual([photo["file"] for photo in found["results"]], ["beach.jpg"])
        self.assertEqual(FakeImmich.searches[-1][1].get("city"), "Lisbon")

    def test_best_place_prefers_the_exact_name(self):
        known = [("city", "Venice"), ("city", "Nice"), ("country", "France"), ("city", "Portland"), ("country", "Portugal"),
                 ("city", "São Paulo")]
        self.assertEqual(apps.best_place("nice", known), ("city", "Nice"))
        self.assertEqual(apps.best_place("Port", known), ("city", "Portland"))  # Starts with, in Immich's order.
        self.assertEqual(apps.best_place("tugal", known), ("country", "Portugal"))
        self.assertEqual(apps.best_place("Nice, France", known), ("city", "Nice"))
        self.assertEqual(apps.best_place("Atlantis, France", known), ("country", "France"))
        self.assertEqual(apps.best_place("são paulo", known), ("city", "São Paulo"))
        self.assertIsNone(apps.best_place("Mars", known))
        self.assertIsNone(apps.best_place(" , ", known))

    def test_an_odd_watch_list_is_an_explained_error(self):
        self.save(self.settings())
        FakeChanges.watches = {"w1": "not a watch"}
        with self.assertRaisesRegex(service.IntegrationError, "did not answer as expected"):
            self.service.check_watches()
        with self.assertRaisesRegex(service.IntegrationError, "did not answer as expected"):
            self.app("watch", "list")

    def test_odd_app_answers_are_explained_errors_not_crashes(self):
        self.save(self.settings())
        original = apps.request
        self.addCleanup(setattr, apps, "request", original)
        cases = [({"results": ["x"], "count": 1}, lambda: self.app("documents", "search", query="car")),
                 ({"assets": {"items": [{"no": "id"}]}}, lambda: self.app("photos", "search", after="2026-01-01")),
                 ({"assets": ["x"]}, lambda: self.app("photos", "search", after="2026-01-01")),
                 ({"city": "Lisbon"}, lambda: self.app("photos", "search", place="Lisbon")),
                 ({"items": [None]}, lambda: apps.unread_news({"url": self.url("news"), "username": "owner"}, "rss-pass"))]
        for answer, ask in cases:
            # FreshRSS signs in first with a text answer; everything else gets the odd answer.
            apps.request = lambda app, url, *, answer=answer, **kw: "Auth=tok" if url.endswith("ClientLogin") else answer
            with self.assertRaisesRegex(service.IntegrationError, "did not answer as expected", msg=repr(answer)):
                ask()

    def test_a_watch_address_with_spaces_is_refused(self):
        self.save(self.settings(changes="allow"))
        for bad in ("https://shop.example/a b", "https://shop.example/a\tb", "https://shop.example/\nx"):
            with self.assertRaisesRegex(service.IntegrationError, "full web address"):
                self.app("watch", "add", url=bad)
        self.assertEqual(FakeChanges.watches, {})

    def test_prices_to_read_is_a_pure_choice(self):
        listing = {"page": {"last_changed": 9}, "new": {"last_changed": 0}, "same": {"last_changed": 5}, "moved": {"last_changed": 7}}
        state = {"page": {"below": None, "lastChanged": 1}, "new": {"below": 5.0, "lastChanged": None, "lastPrice": None},
                 "same": {"below": 5.0, "lastChanged": 5, "lastPrice": 6.0}, "moved": {"below": 5.0, "lastChanged": 5, "lastPrice": 6.0}}
        self.assertEqual(apps.prices_to_read(listing, state), ["new", "moved"])
        self.assertEqual(apps.prices_to_read(listing, {}), [])


if __name__ == "__main__":
    unittest.main()
