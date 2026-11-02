"""The owner's apps for the integrations service: what Portal asks of them, page watching,
and notifications for finished scheduled tasks. Mixed into the service's Integrations class,
which provides store, _approval, failing_tasks and the digest_* run memory.

Portal reaches the apps through its MCP tool, where each app the owner turned on is listed as
a built-in server (documents, photos, watch, news) next to the owner's MCP servers. The
gateway's plugin may declare only a few tools, so the apps add none of their own.
"""
import re
import time
import uuid as uuid_module
from datetime import datetime
from zoneinfo import ZoneInfo

import integrations_apps as apps
from integrations_common import IntegrationError, digest, watch_context, redact_secrets, app_context
from integrations_ntfy import send_notification

APP_ACTIONS = {"documents-search": "documents", "documents-read": "documents", "photos-search": "photos",
               "watch-list": "watch", "watch-add": "watch", "watch-remove": "watch", "news-unread": "news", "news-digest": "news"}
TEXT = {"type": "string"}
# Each app as a built-in server: its tools, what they do, and the action and arguments behind them.
APP_TOOLS = {
    "documents": {
        "search": ("documents-search", "Find the owner's documents (letters, bills, contracts, manuals) holding these words.",
                   {"query": TEXT}),
        "read": ("documents-read", "Read one document's text; id comes from search.", {"id": {"type": "integer"}}),
    },
    "photos": {
        "search": ("photos-search", "Find the owner's photos by what is in them (query), where (place: a city or country) "
                   "or when (after and before, YYYY-MM-DD). Returns links that open the photos.",
                   {"query": TEXT, "place": TEXT, "after": TEXT, "before": TEXT}),
    },
    "watch": {
        "list": ("watch-list", "Show the watched web pages.", {}),
        "add": ("watch-add", "Watch a web page (url, optional title) and notify the owner when it changes. Give below to "
                "watch a product's price and notify when it is at or under that price.",
                {"url": TEXT, "title": TEXT, "below": {"type": "number"}}),
        "remove": ("watch-remove", "Stop watching a page; id comes from list.", {"id": TEXT}),
    },
    "news": {
        "unread": ("news-unread", "The owner's unread news stories, grouped by feed, with links.", {}),
        "digest": ("news-digest", "For the daily digest: the unread stories no earlier digest sent.", {}),
    },
}
APP_SERVER_IDS = {"app:" + app: app for app in APP_TOOLS}
APP_SERVER_NAMES = {"documents": "Documents (Paperless-ngx)", "photos": "Photos (Immich)",
                    "watch": "Watched pages (changedetection.io)", "news": "News (FreshRSS)"}


def _watch_line(watch):
    line = f"Watch {watch['id']}: {watch['title'] or watch['url']}"
    if watch["below"] is not None:
        seen = f"; last seen at {watch['lastPrice']:g}" if watch["lastPrice"] is not None else ""
        line += f" (notifies when the price is at or under {watch['below']:g}{seen})"
    return f"{line}\n  {watch['url']}"


def app_text(action, reply):
    """What Portal reads back from an app."""
    if action == "documents-search":
        if not reply["results"]:
            return "No document holds those words. Try other words."
        shown = len(reply["results"])
        more = (f"\n\nShowing {shown} of {reply['total']} documents. Use more specific words to find the others."
                if reply["total"] > shown else "")
        return "\n".join(f"Document {hit['id']}: {hit['title']} ({hit['created']})" + (f" — {hit['snippet']}" if hit["snippet"] else "")
                         + f"\n  {hit['link']}" for hit in reply["results"]) + more + "\n\nRead a document (tool read with its id) before quoting it."
    if action == "documents-read":
        return f"{reply['title']} ({reply['created']})\n{reply['link']}\n\n{reply['text']}"
    if action == "photos-search":
        if not reply["results"]:
            return reply.get("note") or "No photos match. Try another place or other dates."
        more = (f"\n\nShowing the first {len(reply['results'])}; more photos match. Narrow the search by place or dates."
                if reply.get("more") else "")
        if reply.get("note"):
            more += f"\n\n{reply['note']}"
        return "\n".join(f"{photo['file']} — taken {photo['taken'] or 'on an unknown date'}" + (f" in {photo['place']}" if photo["place"] else "")
                         + f"\n  {photo['link']}" for photo in reply["results"]) + more + "\n\nGive the owner the links; they open the photos in Immich."
    if action == "watch-list":
        if not reply["watches"]:
            return "No pages are watched."
        more = (f"\n\nShowing {len(reply['watches'])} of {reply['total']} watched pages." if reply["total"] > len(reply["watches"]) else "")
        return "\n".join(_watch_line(watch) for watch in reply["watches"]) + more
    if action == "watch-add":
        return f"Done: {reply['done']}. The owner gets a notification when it changes."
    if action == "watch-remove":
        return f"Done: {reply['done']}."
    return reply["text"]


class AppActions:
    def test_app(self, app):
        """Reach one app with its saved token; a sentence for the owner."""
        if app not in apps.APPS:
            raise IntegrationError("invalid-request", "Unknown app.")
        config, settings, key = self._app_snapshot(app, require_enabled=False)
        if not key:
            raise IntegrationError("app-no-key", f"Save the {apps.NAMES[app]} token first.")
        notice = {"documents": apps.documents_test, "photos": apps.photos_test, "watch": apps.watch_test,
                  "news": apps.news_test}[app](settings, key)
        self._log("app-test", apps.NAMES[app], "answered")
        return notice

    def _app_log(self, action, target, outcome, key):
        # Redact before persistence as well as before presentation: rotating an
        # app key must not expose its old value through historical activity.
        secrets = {"admittedAppKey": key}
        self._log(action, redact_secrets(target, secrets), redact_secrets(outcome, secrets))

    def _key(self, app):
        with self.store.lock:
            return self.store.secrets()["apps"].get(app, "")

    def _app_snapshot(self, app, *, require_enabled=True):
        """Capture a destination and its credential together, before provider I/O."""
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
            config, settings, key = self._verified_app_snapshot(config, secrets, app, require_enabled=require_enabled)
            if app == "watch" and settings["enabled"]:
                self._binding(config, "apps", "watch")
        return config, settings, key

    @staticmethod
    def _verified_app_snapshot(config, secrets, app, *, require_enabled=True):
        settings = apps.app_settings(config, app) if require_enabled else config["apps"][app]
        key = secrets["apps"].get(app, "")
        expected = config.get("approvalBindings", {}).get("appContexts", {}).get(app)
        if key and expected != app_context(config, app, key):
            raise IntegrationError("app-check-required", "The saved app destination and key could not be verified together. "
                                   "Review and save Portal integrations again before using this app.")
        if require_enabled and not key:
            raise IntegrationError("app-no-key", f"Save the {apps.NAMES[app]} token first.")
        return config, settings, key

    def _watch_context(self, config, key):
        return watch_context(config, key)

    def _assert_watch_context(self, config, key):
        # Caller holds store.lock. An admitted provider operation keeps its own
        # immutable destination/key after this check, just like an MCP session.
        current, secrets = self.store.config(), self.store.secrets()
        if (current["apps"]["watch"] != config["apps"]["watch"]
                or self._watch_context(current, secrets["apps"].get("watch", "")) != self._watch_context(config, key)):
            raise IntegrationError("stale-revision", "Watched pages settings changed. Try the action again.")

    @staticmethod
    def app_servers(config):
        """The apps the owner turned on, listed like MCP servers for Portal."""
        servers = []
        for app, tools in APP_TOOLS.items():
            if not config["apps"][app]["enabled"]:
                continue
            asks = app == "watch" and config["apps"]["watch"]["changes"] == "ask"
            servers.append({"id": "app:" + app, "name": APP_SERVER_NAMES[app], "tools": [
                {"name": name, "description": description, "needsApproval": asks and name in ("add", "remove"),
                 "inputSchema": {"type": "object", "properties": properties}}
                for name, (_action, description, properties) in tools.items()]})
        return servers

    def use_app(self, config, server, tool, arguments, *, run_context=None, secrets=None):
        """One app tool, called the way Portal calls an MCP tool; the app's reply."""
        if tool not in APP_TOOLS[server]:
            raise IntegrationError("tool-not-allowed", f"{APP_SERVER_NAMES[server]} has no tool named {tool}. List its tools first.")
        action, _description, properties = APP_TOOLS[server][tool]
        snapshot = None if secrets is None else self._verified_app_snapshot(config, secrets, server)
        return action, self._app_action(config, action, {key: arguments[key] for key in properties if key in arguments},
                                        run_context=run_context, snapshot=snapshot)

    def _app_action(self, config, action, request, *, run_context=None, snapshot=None):
        app = APP_ACTIONS[action]
        config, settings, key = snapshot or self._app_snapshot(app)
        if app == "watch" and snapshot is not None:
            with self.store.lock:
                self._assert_watch_context(config, key)
        if action == "documents-search":
            reply = apps.documents_search(settings, key, request.get("query"))
            self._app_log("documents-search", str(request.get("query", ""))[:80], f"{len(reply['results'])} documents", key)
            return reply
        if action == "documents-read":
            reply = apps.documents_read(settings, key, request.get("id"))
            self._app_log("documents-read", reply["title"], "read", key)
            return reply
        if action == "photos-search":
            reply = apps.photos_search(settings, key, query=request.get("query", ""), place=request.get("place", ""),
                                       after=request.get("after", ""), before=request.get("before", ""))
            self._app_log("photos-search", str(request.get("query") or request.get("place") or "dates")[:80],
                          f"{len(reply['results'])} photos", key)
            return reply
        if action == "news-unread":
            items = apps.unread_news(settings, key)
            self._log("news-unread", "unread news", f"{len(items)} stories")
            return {"items": items, "text": apps.digest_text(items)}
        if action == "news-digest":
            all_items, capped = self._new_stories(settings, key, with_cap=True)
            items = self.remember_digest_read(run_context, all_items, config, key, capped=capped)
            self._log("news-digest", "new stories", f"{len(items)} stories")
            text = apps.digest_text(items) if items else apps.NO_NEW_STORIES
            left = len(all_items) - len(items)
            if left or capped:
                text += f"\n\n…and {left}{'+' if capped else ''} more in FreshRSS for a later digest."
            return {"items": items, "text": text}
        if action == "watch-list":
            return self._watch_list(settings, key, context=self._watch_context(config, key))
        return self._watch_change(config, settings, key, action, request)

    def _watch_list(self, settings, key, *, context):
        """The watched pages, with each price watch's limit and the last price Portal saw."""
        listing = apps.watches(settings, key)
        with self.store.lock:
            state = self.store.watch_state(context)
        watches = [{"id": uuid, "title": str(watch.get("title") or "")[:200], "url": str(watch.get("url", ""))[:500],
                    "lastChanged": watch.get("last_changed") or 0, "below": state.get(uuid, {}).get("below"),
                    "lastPrice": state.get(uuid, {}).get("lastPrice")}
                   for uuid, watch in list(listing.items())[:apps.MAX_WATCHES]]
        return {"watches": watches, "total": len(listing)}

    def watch_overview(self):
        """The watched pages for Settings."""
        # Saving refuses an app turned on without its key.
        config, settings, key = self._app_snapshot("watch")
        return self._watch_list(settings, key, context=self._watch_context(config, key))

    def send_digest_now(self):
        """One delivery admission shared by Settings and native cron."""
        with self.digest_lock:
            config, settings, key = self._app_snapshot("news")
            if not config["notifications"]["enabled"]:
                raise IntegrationError("notifications-off", "Turn notifications on and save before sending a digest.")
            items, capped = self._new_stories(settings, key, with_cap=True)
            selected, text = self._compose_digest(items, capped=capped)
            sent = self._deliver_digest(config, key, selected, text, explicit_empty=not selected)
            if not sent:
                raise IntegrationError("digest-delivery-unknown", "The digest delivery could not be confirmed. It will not be repeated automatically; check the notification activity.")
            shown = len(selected)
            if not shown:
                return "Digest sent: there are no new stories since the last digest."
            return f"Digest sent with {shown} {'story' if shown == 1 else 'stories'}."

    def _new_stories(self, settings, key, with_cap=False):
        """Exclude both confirmed and uncertain prior delivery admissions."""
        unread = apps.unread_news(settings, key)
        binding = self._news_binding(settings, key)
        with self.store.lock:
            history = self._digest_history()
            seen = self._held_stories(binding, history)
            items = [item for item in unread if self._story_id(item) not in seen]
        return (items, len(unread) >= apps.MAX_NEWS_ITEMS) if with_cap else items

    @staticmethod
    def _news_binding(settings, key):
        return digest("news-source-v1", settings["url"], settings.get("username", ""), key)

    @staticmethod
    def _story_id(item):
        return digest("news-story-v1", apps.story_key(item))

    @staticmethod
    def _digest_sources(items):
        return "\n\n".join(feed + ":\n" + "\n".join("- " + item["title"] for item in stories)
                            for feed, stories in apps.by_feed(items).items())

    def _select_digest_batch(self, items):
        selected, seen = [], set()
        for item in items:
            identity = self._story_id(item)
            if identity in seen:
                continue
            trial = selected + [item]
            if (len(trial) > apps.MAX_BRIEF_STORIES or len(self._digest_sources(trial).encode()) > 3300
                    or len(apps.digest_text(trial)) > 7400):
                break
            selected, seen = trial, seen | {identity}
        if items and not selected:
            raise IntegrationError("digest-story-too-large", "A news story cannot fit in one digest; no story was marked delivered.")
        return selected

    def _compose_digest(self, items, summary="", *, extra=0, capped=False):
        selected = self._select_digest_batch(items)
        source = self._digest_sources(selected)
        if not selected:
            return [], apps.NO_NEW_STORIES
        remaining = len(items) - len(selected) + extra
        if remaining or capped:
            source += f"\n\n…and {remaining}{'+' if capped else ''} more in FreshRSS."
        if summary:
            suffix = "\n\nStories included:\n" + source
            room = min(1200, apps.MAX_NOTICE_BYTES - len(suffix.encode()))
            text = apps.fit_notice(apps.plain_brief(summary), room) + suffix if room >= 120 else source
        else:
            text = source
        assert len(text.encode()) <= apps.MAX_NOTICE_BYTES
        return selected, text

    def _digest_history(self):
        value = self.store._read("digest-history.json", {"schemaVersion": 1, "sources": {}})
        if (not isinstance(value, dict) or set(value) != {"schemaVersion", "sources"}
                or value["schemaVersion"] != 1 or not isinstance(value["sources"], dict)
                or len(value["sources"]) > 64):
            raise IntegrationError("state-damaged", "The news delivery history is damaged; it was preserved.")
        for source, record in value["sources"].items():
            if (not isinstance(source, str) or not re.fullmatch("[a-f0-9]{64}", source)
                    or not isinstance(record, dict) or set(record) != {"stories", "lastAttemptDay"}
                    or not (record["lastAttemptDay"] is None or isinstance(record["lastAttemptDay"], str)
                            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["lastAttemptDay"]))
                    or not isinstance(record["stories"], dict) or len(record["stories"]) > 100000
                    or any(not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{64}", identity)
                           or state not in ("admitted", "sent", "unknown") for identity, state in record["stories"].items())):
                raise IntegrationError("state-damaged", "The news delivery history is damaged; it was preserved.")
        return value

    def _digest_attempts(self):
        value = self.store._read("digest-attempts.json", {"schemaVersion": 1, "attempts": []})
        if (not isinstance(value, dict) or set(value) != {"schemaVersion", "attempts"}
                or value["schemaVersion"] != 1 or not isinstance(value["attempts"], list) or len(value["attempts"]) > 200):
            raise IntegrationError("state-damaged", "The digest delivery journal is damaged; it was preserved.")
        seen = set()
        keys = {"id", "at", "binding", "stories", "message", "state", "claimedBy", "retryOf", "outcome"}
        for item in value["attempts"]:
            if (not isinstance(item, dict) or set(item) != keys
                    or not isinstance(item["id"], str) or not re.fullmatch("[a-f0-9]{32}", item["id"]) or item["id"] in seen
                    or type(item["at"]) is not int or item["at"] <= 0
                    or not isinstance(item["binding"], str) or not re.fullmatch("[a-f0-9]{64}", item["binding"])
                    or not isinstance(item["stories"], list) or len(item["stories"]) > apps.MAX_BRIEF_STORIES
                    or any(not isinstance(key, str) or not re.fullmatch("[a-f0-9]{64}", key) for key in item["stories"])
                    or not isinstance(item["message"], str) or not item["message"].strip()
                    or len(item["message"].encode()) > apps.MAX_NOTICE_BYTES
                    or item["state"] not in ("admitted", "sent", "unknown", "refused")
                    or any(item[key] is not None and (not isinstance(item[key], str)
                           or not re.fullmatch("[a-f0-9]{32}", item[key])) for key in ("claimedBy", "retryOf"))
                    or not isinstance(item["outcome"], str) or len(item["outcome"]) > 400):
                raise IntegrationError("state-damaged", "The digest delivery journal is damaged; it was preserved.")
            seen.add(item["id"])
        return value

    def _held_stories(self, binding, history):
        seen = set(history["sources"].get(binding, {}).get("stories", {}))
        # Covers a crash between the durable exact-message journal and the
        # secondary story history write. No external call precedes admission.
        for item in self._digest_attempts()["attempts"]:
            if item["binding"] == binding and item["state"] != "refused" and item["claimedBy"] is None:
                seen.update(item["stories"])
        return seen

    def _admit_digest_attempt(self, config, key, identities, text, *, original=None):
        """Called under digest_lock/store.lock; persist exact recovery payload first."""
        binding = self._news_binding(config["apps"]["news"], key)
        journal = self._digest_attempts()
        attempts = journal["attempts"]
        # Old acknowledged attempts may be removed only after their story
        # history is durable; uncertain unclaimed attempts are never discarded.
        history = self._digest_history()
        retained = []
        for item in attempts:
            recorded = history["sources"].get(item["binding"], {}).get("stories", {})
            resolved = item["claimedBy"] is not None or item["state"] == "sent" and all(key in recorded for key in item["stories"])
            if not resolved:
                retained.append(item)
        item = {"id": uuid_module.uuid4().hex, "at": int(time.time() * 1000), "binding": binding,
                "stories": list(identities), "message": text, "state": "admitted", "claimedBy": None,
                "retryOf": original["id"] if original else None, "outcome": ""}
        if original:
            current = next((row for row in retained if row["id"] == original["id"]), None)
            if current is None or current["claimedBy"] is not None:
                raise IntegrationError("digest-retry-stale", "That digest retry was already used; reload the delivery review.")
            current["claimedBy"] = item["id"]
            retained = [row for row in retained if row["id"] != original["id"]]
        else:
            # A definitive refusal may be retried by a later native run. That
            # new admission supersedes the old manual recovery card.
            for prior in retained:
                if (prior["binding"] == binding and prior["state"] == "refused" and prior["claimedBy"] is None
                        and set(prior["stories"]) & set(identities)):
                    prior["claimedBy"] = item["id"]
        retained = [row for row in retained if row["claimedBy"] is None]
        if len(retained) >= 200:
            raise IntegrationError("digest-recovery-full", "Too many digest deliveries need review. Resolve them in Portal integrations before sending more.")
        journal["attempts"] = retained + [item]
        self.store._write("digest-attempts.json", journal)
        return item

    def _dispatch_digest_attempt(self, config, secrets, attempt):
        state, outcome = "sent", "sent"
        try:
            send_notification(config, secrets, apps.DIGEST_NAME, attempt["message"])
        except Exception as error:
            definitive = isinstance(error, IntegrationError) and error.code in (
                "notification-refused", "notification-not-connected", "notification-check-required")
            state = "refused" if definitive else "unknown"
            outcome = (redact_secrets(error.message, secrets)[:400] if isinstance(error, IntegrationError)
                       else "Delivery could not be confirmed.")
        with self.store.lock:
            journal = self._digest_attempts()
            current = next((item for item in journal["attempts"] if item["id"] == attempt["id"]), None)
            if current is None:
                raise IntegrationError("state-damaged", "The digest delivery receipt disappeared; its outcome must be checked.")
            current["state"], current["outcome"] = state, outcome
            self.store._write("digest-attempts.json", journal)
            history = self._digest_history()
            stories = history["sources"].setdefault(attempt["binding"], {"stories": {}, "lastAttemptDay": None})["stories"]
            if state == "refused":
                for identity in attempt["stories"]:
                    stories.pop(identity, None)
            else:
                stories.update({identity: state for identity in attempt["stories"]})
            self.store._write("digest-history.json", history)
            self.active_digest_attempts.discard(attempt["id"])
            self._log("notify", apps.DIGEST_NAME, "sent" if state == "sent" else outcome)
        return state == "sent"

    def _deliver_digest(self, config, key, items, text, *, explicit_empty=False):
        binding = self._news_binding(config["apps"]["news"], key)
        identities = [self._story_id(item) for item in items]
        with self.store.lock:
            current, secrets = self.store.config(), self.store.secrets()
            if (current["apps"]["news"] != config["apps"]["news"]
                    or current["notifications"] != config["notifications"]
                    or secrets["apps"].get("news", "") != key):
                raise IntegrationError("settings-changed", "News or notification settings changed; no digest was sent.")
            if not config["notifications"]["enabled"]:
                return False
            text = redact_secrets(text, {**secrets, "admittedNewsKey": key})
            if not text.strip() or len(text.encode()) > apps.MAX_NOTICE_BYTES:
                raise IntegrationError("digest-message-too-large", "The sanitized digest cannot fit in one notification; no story was marked delivered.")
            history = self._digest_history()
            if binding not in history["sources"] and len(history["sources"]) >= 64:
                raise IntegrationError("digest-history-full", "The news delivery history is full; it was preserved and nothing was sent.")
            record = history["sources"].setdefault(binding, {"stories": {}, "lastAttemptDay": None})
            schedule = config["apps"]["news"]["digest"]
            # A disabled schedule may retain an unfinished time-zone draft.
            # Manual delivery still works; only an active schedule owns a zone.
            zone = schedule["timezone"] if schedule["enabled"] else "UTC"
            today = datetime.now(ZoneInfo(zone)).date().isoformat()
            if not identities and record["lastAttemptDay"] == today and not explicit_empty:
                return False
            stories = record["stories"]
            if set(identities) & self._held_stories(binding, history):
                return False
            if len(stories) + len(set(identities)) > 100000:
                raise IntegrationError("digest-history-full", "The news delivery history is full; old delivery records were preserved and nothing was sent.")
            attempt = self._admit_digest_attempt(config, key, identities, text)
            stories.update({identity: "admitted" for identity in identities})
            record["lastAttemptDay"] = today
            self.store._write("digest-history.json", history)
            self.active_digest_attempts.add(attempt["id"])
        try:
            return self._dispatch_digest_attempt(config, secrets, attempt)
        finally:
            with self.store.lock:
                self.active_digest_attempts.discard(attempt["id"])

    def digest_recovery(self):
        """Owner-only projection, never a model tool or an execution authority."""
        with self.store.lock:
            journal, history = self._digest_attempts(), self._digest_history()
            config, secrets = self.store.config(), self.store.secrets()
            binding = self._news_binding(config["apps"]["news"], secrets["apps"].get("news", ""))
            destination = redact_secrets(config["notifications"]["url"] + "/" + config["notifications"]["topic"], secrets)
            result = []
            for item in reversed(journal["attempts"]):
                if item["state"] == "sent" or item["claimedBy"] is not None:
                    continue
                reason = ""
                if item["id"] in self.active_digest_attempts:
                    reason = "This delivery is still running."
                elif item["binding"] != binding:
                    reason = "The news connection changed; this delivery cannot be retried with another connection."
                elif not config["apps"]["news"]["enabled"] or not config["notifications"]["enabled"]:
                    reason = "Turn on News and notifications before retrying."
                elif any(history["sources"].get(binding, {}).get("stories", {}).get(identity) == "sent" for identity in item["stories"]):
                    reason = "These stories already have a confirmed delivery."
                result.append({"attemptId": item["id"], "attemptedAt": item["at"], "storyCount": len(item["stories"]),
                               "messagePreview": item["message"], "canRetry": not reason,
                               "reason": reason or item["outcome"] or "The previous delivery could not be confirmed.",
                               "notificationDestination": destination})
                if len(result) >= 20:
                    break
            return result

    def retry_digest(self, request):
        """One explicit owner decision may replay one exact uncertain message."""
        with self.digest_lock:
            with self.store.lock:
                config, secrets = self.store.config(), self.store.secrets()
                if type(request.get("expectedRevision")) is not int or request["expectedRevision"] != config["revision"]:
                    raise IntegrationError("stale-revision", "Settings changed; review the digest and current notification destination again.")
                identity = request.get("attemptId")
                if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{32}", identity):
                    raise IntegrationError("invalid-request", "Choose one exact digest delivery to retry.")
                journal = self._digest_attempts()
                original = next((item for item in journal["attempts"] if item["id"] == identity), None)
                projection = next((item for item in self.digest_recovery() if item["attemptId"] == identity), None)
                if original is None or projection is None or not projection["canRetry"]:
                    raise IntegrationError("digest-retry-stale", "That digest is not available to retry; reload its delivery review.")
                key = secrets["apps"].get("news", "")
                attempt = self._admit_digest_attempt(config, key, original["stories"], original["message"], original=original)
                history = self._digest_history()
                if original["binding"] not in history["sources"] and len(history["sources"]) >= 64:
                    raise IntegrationError("digest-history-full", "The news delivery history is full; no retry was sent.")
                record = history["sources"].setdefault(original["binding"], {"stories": {}, "lastAttemptDay": None})
                if len(set(record["stories"]) | set(original["stories"])) > 100000:
                    raise IntegrationError("digest-history-full", "The news delivery history is full; no retry was sent.")
                record["stories"].update({identity: "admitted" for identity in original["stories"]})
                self.store._write("digest-history.json", history)
                self.active_digest_attempts.add(attempt["id"])
            try:
                sent = self._dispatch_digest_attempt(config, secrets, attempt)
            finally:
                with self.store.lock:
                    self.active_digest_attempts.discard(attempt["id"])
            if not sent:
                raise IntegrationError("digest-delivery-unknown", "The retry delivery could not be confirmed. Review the new delivery entry before deciding whether to retry again.")
            return "The exact reviewed digest was sent to the current notification destination."

    @staticmethod
    def _run_context(value):
        if (not isinstance(value, dict) or set(value) != {"sessionId", "sessionKey"}
                or any(not isinstance(value[key], str) or not 0 < len(value[key]) <= 2048
                       or re.search(r"[\x00-\x1f\x7f]", value[key]) for key in value)):
            return None
        if not value["sessionKey"].startswith("agent:pixel:"):
            return None
        return digest("native-news-run-v1", value["sessionId"], value["sessionKey"])

    def remember_digest_read(self, context, items, config, key, *, capped=False):
        all_items, items = items, self._select_digest_batch(items)
        identity = self._run_context(context)
        if identity is None:
            return items
        with self.digest_lock:
            cutoff = time.time() - 1800
            self.digest_snapshots = {key: value for key, value in self.digest_snapshots.items() if value["at"] >= cutoff}
            if identity not in self.digest_snapshots and len(self.digest_snapshots) >= 128:
                raise IntegrationError("digest-busy", "Too many news digest runs are active.")
            self.digest_snapshots[identity] = {"at": time.time(), "items": items,
                "binding": self._news_binding(config["apps"]["news"], key),
                "remaining": len(all_items) - len(items), "capped": capped}
        return items

    def digest_control(self, action, request):
        """Protected creation journal and native returned ID; never discover by prose alone."""
        with self.digest_lock, self.store.lock:
            config = self.store.config()
            record = self.store._read("digest-job.json", None)
            if record is not None and (not isinstance(record, dict) or set(record) != {"schemaVersion", "nonce", "jobId"}
                    or record["schemaVersion"] != 1 or not isinstance(record["nonce"], str)
                    or not re.fullmatch("[a-f0-9]{32}", record["nonce"])
                    or not (record["jobId"] is None or isinstance(record["jobId"], str) and 0 < len(record["jobId"]) <= 200
                            and not re.search(r"[\x00-\x1f\x7f]", record["jobId"]))):
                raise IntegrationError("state-damaged", "The digest job identity is damaged; no scheduled task was changed.")
            if action == "digest-plan":
                plan = apps.digest_plan(config)
                if record is None and plan["enabled"]:
                    record = {"schemaVersion": 1, "nonce": uuid_module.uuid4().hex, "jobId": None}
                    self.store._write("digest-job.json", record)
                plan["management"] = {key: record[key] for key in ("nonce", "jobId")} if record else None
                if record:
                    plan["description"] += " [ODS managed:" + record["nonce"] + "]"
                return {"plan": plan}
            if record is None or request.get("nonce") != record["nonce"]:
                raise IntegrationError("digest-job-stale", "The digest job journal changed; no task was adopted.")
            job = request.get("jobId")
            if not isinstance(job, str) or not 0 < len(job) <= 200 or re.search(r"[\x00-\x1f\x7f]", job):
                raise IntegrationError("invalid-request", "A native digest job ID is required.")
            if action == "digest-job-record":
                if record["jobId"] not in (None, job):
                    raise IntegrationError("digest-job-conflict", "Another digest job is already recorded; none was replaced.")
                record["jobId"] = job
                self.store._write("digest-job.json", record)
                return {"recorded": True}
            if action != "digest-job-clear" or record["jobId"] != job:
                raise IntegrationError("digest-job-stale", "The recorded digest job changed; its identity was preserved.")
            self.store._write("digest-job.json", None)
            return {"cleared": True}

    def _watch_change(self, config, settings, key, action, request):
        # Serialise mutations of this provider, not unrelated owner Settings.
        with self._watch_lock:
            context = self._watch_context(config, key)
            with self.store.lock:
                self._assert_watch_context(config, key)
                state = self.store.watch_state(context)
            listing = apps.watches(settings, key)
            if action == "watch-add":
                url = apps.valid_watch_url(request.get("url"))
                title = str(request.get("title") or "").strip()[:200]
                below = None if request.get("below") in (None, "") else apps.price_limit(request.get("below"))
                uuid = next((item for item, watch in listing.items() if watch.get("url") == url), None)
                target = listing.get(uuid) if uuid else None
                if uuid is not None:
                    limit = (state.get(uuid) or {}).get("below")
                    name = title or target.get("title") or url
                    if below is not None and limit is None:
                        raise self._page_watched(name)
                    if below is None or limit == below:
                        return {"done": f"{name} is already watched", "id": uuid}
                summary = f"Watch {title or url}" + (f" for a price at or under {below:g}" if below is not None else "")
                details = (action, url, title, below, uuid, target)
            else:
                uuid = str(request.get("id", ""))
                target = listing.get(uuid)
                if target is None:
                    raise IntegrationError("watch-missing", "No watch has that id. List the watches first.")
                summary = f"Stop watching {target.get('title') or target.get('url')}"
                details = (action, uuid, target)
            summary = redact_secrets(summary, {"admittedAppKey": key})
            waiting, notification = None, None
            with self.store.lock:
                self._assert_watch_context(config, key)
                epoch = self._binding(self.store.config(), "apps", "watch")
                if settings["changes"] == "ask":
                    waiting, notification = self._approval("watch", summary,
                        digest("watch-v2", epoch, context, settings, *details))
            if notification:
                self._notify_approval(notification)
            if waiting:
                return waiting
            # The exact endpoint/key/target was admitted above. Provider I/O must
            # not hold store.lock; a concurrent settings save stays responsive.
            if action == "watch-add":
                if uuid is not None:
                    with self.store.lock:
                        state = self.store.watch_state(context)
                        summary = self._rewatch(state, uuid, title or target.get("title") or url, below)
                        self.store.save_watch_state(state, context)
                else:
                    uuid = apps.add_watch(settings, key, url, title, below)
                    with self.store.lock:
                        state = self.store.watch_state(context)
                        state[uuid] = {"lastChanged": None, "below": below, "lastPrice": None}
                        self.store.save_watch_state(state, context)
            else:
                apps.remove_watch(settings, key, uuid)
                with self.store.lock:
                    state = self.store.watch_state(context)
                    state.pop(uuid, None)
                    self.store.save_watch_state(state, context)
            self._app_log(action, summary, "done", key)
            return {"done": summary, "id": uuid}

    @staticmethod
    def _page_watched(name):
        # A page watch compares the page text; reading a price needs a price watch of its own.
        return IntegrationError("watch-exists", f"{name} is already watched for any change. Remove that watch first "
                                "to watch its price instead.")

    @staticmethod
    def _rewatch(state, uuid, name, below):
        """A page already watched: its price limit changes, or nothing does; the summary to report."""
        entry = state.setdefault(uuid, {"lastChanged": None, "below": None, "lastPrice": None})
        if below is None or entry["below"] == below:
            return f"{name} is already watched"
        if entry["below"] is None:
            raise AppActions._page_watched(name)
        entry["below"] = below
        return f"Price limit for {name} is now {below:g}"

    def check_watches(self):
        with self._watch_lock:
            return self._check_watches()

    def _check_watches(self):
        """Tell the owner about watched pages that changed since the last look."""
        config, settings, key = self._app_snapshot("watch", require_enabled=False)
        if not (settings["enabled"] and key):
            return
        context = self._watch_context(config, key)
        listing = apps.watches(settings, key)
        with self.store.lock:
            state = self.store.watch_state(context)
        prices, unread = {}, set()
        for uuid in apps.prices_to_read(listing, state):
            prices[uuid] = self._price(settings, key, uuid, listing[uuid])
            if prices[uuid] is self.UNREAD:
                prices[uuid] = None
                unread.add(uuid)
        notices, kept = apps.page_notices(listing, state, prices)
        for uuid in unread:  # Not read this time: keep the old mark, so the next look reads it again.
            kept[uuid]["lastChanged"] = (state.get(uuid) or {}).get("lastChanged")
        with self.store.lock:
            # Portal may have added or removed a watch during the slow look: a watch no look has
            # seen yet is kept, and one removed meanwhile stays removed.
            current = self.store.watch_state(context)
            fresh = {uuid: entry for uuid, entry in current.items() if uuid not in listing and entry.get("lastChanged") is None}
            seen = {uuid: entry for uuid, entry in kept.items() if uuid in current or uuid not in state}
            self._assert_watch_context(config, key)
            self.store.save_watch_state({**fresh, **seen}, context)
        for notice in notices:
            self._tell(config, notice["title"], notice["message"], link=notice["link"])

    UNREAD = object()

    def _price(self, settings, key, uuid, watch):
        """One watch's price, or UNREAD when it cannot be read now; one broken watch must not stop the others."""
        try:
            return apps.watch_price(settings, key, uuid)
        except IntegrationError as error:
            self._app_log("watch-check", str(watch.get("title") or watch.get("url") or uuid)[:80], error.message, key)
            return self.UNREAD

    # Scheduled tasks.
    def task_finished(self, config, event):
        """Only native identified terminal events qualify; duplicates stay quiet."""
        job, started = event.get("jobId"), event.get("runAtMs")
        if (not isinstance(job, str) or not 0 < len(job) <= 200
                or re.search(r"[\x00-\x1f\x7f]", job)
                or type(started) not in (int, float) or not 0 < started <= 9007199254740991
                or event.get("status") not in ("ok", "error")):
            return {"notified": False, "reason": "unverified-event"}
        with self.task_notice_lock:
            with self.store.lock:
                config = self.store.config()
                managed = self.store._read("digest-job.json", None)
            is_digest = (isinstance(managed, dict) and managed.get("jobId") == job
                         and event.get("description") == apps.DIGEST_MARKER + " [ODS managed:" + str(managed.get("nonce")) + "]")
            with self.store.lock:
                ledger = self.store._read("task-notices.json", {"schemaVersion": 1, "jobs": {}})
                if (not isinstance(ledger, dict) or set(ledger) != {"schemaVersion", "jobs"}
                        or ledger["schemaVersion"] != 1 or not isinstance(ledger["jobs"], dict)
                        or len(ledger["jobs"]) > 2000):
                    raise IntegrationError("state-damaged", "The scheduled-task notification history is damaged; it was preserved.")
                prior = ledger["jobs"].get(job)
                if prior is not None and (not isinstance(prior, dict) or set(prior) != {"at", "failing"}
                        or type(prior["at"]) not in (int, float) or type(prior["failing"]) is not bool):
                    raise IntegrationError("state-damaged", "The scheduled-task notification history is damaged; it was preserved.")
                if prior and started <= prior["at"]:
                    return {"notified": False}
                if prior is None and len(ledger["jobs"]) >= 2000:
                    raise IntegrationError("task-history-full", "The scheduled-task notification history is full; records were preserved.")
                notice = (None if is_digest else apps.task_notice(
                    config["notifications"]["scheduledTasks"], event, bool(prior and prior["failing"])))
                ledger["jobs"][job] = {"at": started, "failing": event["status"] == "error"}
                # Commit the attempt before I/O, so a repeated event or restart
                # cannot send the same failure/recovery notice again.
                self.store._write("task-notices.json", ledger)
            if is_digest:
                return {"notified": self._send_digest(config, event)}
            if notice is None:
                return {"notified": False}
            return {"notified": self._tell(config, notice["title"], notice["message"], notice["priority"])}

    def _send_digest(self, config, event):
        """Model prose is used only with a read receipt for this exact native run."""
        with self.digest_lock:
            if not config["apps"]["news"]["enabled"] or not config["apps"]["news"]["digest"]["enabled"]:
                return False
            key = ""
            try:
                config, settings, key = self._app_snapshot("news")
                if not settings["digest"]["enabled"]:
                    return False
                identity = self._run_context({name: event.get(name) for name in ("sessionId", "sessionKey")})
                snapshot = self.digest_snapshots.pop(identity, None) if identity else None
                summary = str(event.get("summary") or "").strip()
                if (event.get("status") == "ok" and summary and snapshot
                        and snapshot["binding"] == self._news_binding(settings, key)
                        and snapshot["at"] >= event["runAtMs"] / 1000
                        and time.time() - snapshot["at"] <= 1800):
                    with self.store.lock:
                        history = self._digest_history()
                        seen = self._held_stories(snapshot["binding"], history)
                    items = [item for item in snapshot["items"] if self._story_id(item) not in seen]
                    # A concurrent Settings delivery may have consumed part of
                    # this snapshot. Do not send its now-inaccurate model brief.
                    items, text = self._compose_digest(items, summary if len(items) == len(snapshot["items"]) else "",
                                                       extra=snapshot["remaining"], capped=snapshot["capped"])
                else:
                    items, capped = self._new_stories(settings, key, with_cap=True)
                    items, text = self._compose_digest(items, capped=capped)
            except IntegrationError as error:
                with self.store.lock:
                    failure = self.store._read("digest-failure.json", {"failing": False})
                    if not isinstance(failure, dict) or set(failure) != {"failing"} or type(failure["failing"]) is not bool:
                        raise IntegrationError("state-damaged", "The digest failure history is damaged; it was preserved.")
                    if failure["failing"]:
                        return False
                    self.store._write("digest-failure.json", {"failing": True})
                self.digest_failing = True
                message = redact_secrets(error.message, {"admittedNewsKey": key})
                return self._tell(config, apps.DIGEST_NAME, f"Today's digest could not be made. {message}", "high")
            self.digest_failing = False
            with self.store.lock:
                self.store._write("digest-failure.json", {"failing": False})
            if not items:
                return self._deliver_digest(config, key, [], apps.NO_NEW_STORIES)
            return self._deliver_digest(config, key, items, text)

    def _tell(self, config, title, message, priority="default", link=""):
        """Send the owner a notification; a failure is logged, since nobody is waiting for it."""
        with self.store.lock:
            current, secrets = self.store.config(), self.store.secrets()
            if current["notifications"] != config["notifications"] or not current["notifications"]["enabled"]:
                return False
        try:
            send_notification(config, secrets, title, message, priority, link, link_label="Open")
        except IntegrationError as error:
            self._log("notify", redact_secrets(title, secrets), redact_secrets(error.message, secrets))
            return False
        self._log("notify", redact_secrets(title, secrets), "sent")
        return True
