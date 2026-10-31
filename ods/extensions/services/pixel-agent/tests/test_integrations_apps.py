import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires Unix sockets; run under Linux/WSL")

import http.server
import json
import pathlib
import tempfile
import threading
import time
import unittest
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "host"))
import integrations_apps as apps  # noqa: E402
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
            return self.reply({"count": len(results) if words else len(self.documents), "results": results})
        number = int(url.path.strip("/").split("/")[-1])
        if number not in self.documents:
            return self.reply({"detail": "Not found."}, 404)
        return self.reply({"id": number, **self.documents[number]})


class FakeImmich(Fake):
    key_header, key = "x-api-key", "photo-key"
    smart = False
    smart_fails = False
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
        self.reply({"assets": {"items": items, "total": len(items)}})


class FakeChanges(Fake):
    key_header, key = "x-api-key", "watch-key"
    watches = {}
    prices = {}

    def do_GET(self):
        if not self.allowed():
            return
        if self.path == "/api/v1/watch":
            return self.reply(self.watches)
        uuid = self.path.split("/")[4]
        if uuid not in self.watches:
            return self.reply({}, 404)
        if self.path.endswith("/history/latest"):  # As changedetection.io 0.60 writes a price watch's snapshot.
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
        self.reply({"items": self.items})


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
        FakeImmich.smart = False
        FakeChanges.watches, FakeChanges.prices = {}, {}

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
        for bad in ("1", 0, True, None):
            with self.assertRaisesRegex(service.IntegrationError, "document id"):
                self.app("documents", "read", id=bad)
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
        self.assertEqual(self.app("watch", "list")["watches"], [{"id": "w1", "title": "Shop", "url": "https://shop.example/page",
                                                              "lastChanged": 0, "below": None}])
        FakeNtfy.sent.clear()
        FakeChanges.watches["w1"]["last_changed"] = 100  # Its first snapshot: only remembered.
        self.service.check_watches()
        self.assertEqual(FakeNtfy.sent, [])
        FakeChanges.watches["w1"]["last_changed"] = 200
        self.service.check_watches()
        self.service.check_watches()
        self.assertEqual([(note["title"], note["click"], note["actions"]) for note in FakeNtfy.sent],
                         [("Page changed: Shop", "https://shop.example/page", "view, Open, https://shop.example/page")])
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
        done = {"name": "Water plants", "status": "ok", "summary": "Reminded you.", "runAtMs": 1}
        failed = {"name": "Backup", "status": "error", "error": "Disk full", "runAtMs": 1}
        skipped = {"name": "Backup", "status": "skipped", "runAtMs": 1}
        expected = {"all": [True, True, False], "failures": [False, True, False], "off": [False, False, False]}
        for mode, outcome in expected.items():
            self.save(self.settings(tasks=mode))
            self.assertEqual([self.agent("task-finished", **event)["notified"] for event in (done, failed, skipped)], outcome, mode)
        self.assertEqual(FakeNtfy.sent[:2], [
            {"title": "Scheduled task done: Water plants", "message": "Reminded you.", "priority": "default", "click": None, "actions": None},
            {"title": "Scheduled task failed: Backup", "message": "Disk full", "priority": "high", "click": None, "actions": None}])
        self.save(self.settings(tasks="all", notify=False))
        self.assertFalse(self.agent("task-finished", **done)["notified"])

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
                                   "photos": "Immich answered as Owner. Searching by what is in a photo is off until Immich's machine learning runs.",
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


if __name__ == "__main__":
    unittest.main()
