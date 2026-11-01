"""The owner's apps for the integrations service: what Portal asks of them, page watching,
and notifications for finished scheduled tasks. Mixed into the service's Integrations class,
which provides store, _approval, digest_called_at and failing_tasks.

Portal reaches the apps through its MCP tool, where each app the owner turned on is listed as
a built-in server (documents, photos, watch, news) next to the owner's MCP servers. The
gateway's plugin may declare only a few tools, so the apps add none of their own.
"""
import time

import integrations_apps as apps
from integrations_common import IntegrationError, digest
from integrations_ntfy import send_notification

APP_ACTIONS = {"documents-search": "documents", "documents-read": "documents", "photos-search": "photos",
               "watch-list": "watch", "watch-add": "watch", "watch-remove": "watch", "news-digest": "news"}
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
        "digest": ("news-digest", "The owner's unread news stories, grouped by feed, with links.", {}),
    },
}
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
        config, key = self.store.config(), self.store.secrets()["apps"].get(app, "")
        if not key:
            raise IntegrationError("app-no-key", f"Save the {apps.NAMES[app]} token first.")
        settings = config["apps"][app]
        notice = {"documents": apps.documents_test, "photos": apps.photos_test, "watch": apps.watch_test,
                  "news": apps.news_test}[app](settings, key)
        self.store.log("app-test", apps.NAMES[app], "answered")
        return notice

    def _key(self, app):
        return self.store.secrets()["apps"].get(app, "")

    @staticmethod
    def app_servers(config):
        """The apps the owner turned on, listed like MCP servers for Portal."""
        servers = []
        for app, tools in APP_TOOLS.items():
            if not config["apps"][app]["enabled"]:
                continue
            asks = app == "watch" and config["apps"]["watch"]["changes"] == "ask"
            servers.append({"id": app, "name": APP_SERVER_NAMES[app], "tools": [
                {"name": name, "description": description, "needsApproval": asks and name in ("add", "remove"),
                 "inputSchema": {"type": "object", "properties": properties}}
                for name, (_action, description, properties) in tools.items()]})
        return servers

    def use_app(self, config, server, tool, arguments):
        """One app tool, called the way Portal calls an MCP tool; the app's reply."""
        if tool not in APP_TOOLS[server]:
            raise IntegrationError("tool-not-allowed", f"{APP_SERVER_NAMES[server]} has no tool named {tool}. List its tools first.")
        action, _description, properties = APP_TOOLS[server][tool]
        return action, self._app_action(config, action, {key: arguments[key] for key in properties if key in arguments})

    def _app_action(self, config, action, request):
        app = APP_ACTIONS[action]
        settings, key = apps.app_settings(config, app), self._key(app)
        if action == "documents-search":
            reply = apps.documents_search(settings, key, request.get("query"))
            self.store.log("documents-search", str(request.get("query", ""))[:80], f"{len(reply['results'])} documents")
            return reply
        if action == "documents-read":
            reply = apps.documents_read(settings, key, request.get("id"))
            self.store.log("documents-read", reply["title"], "read")
            return reply
        if action == "photos-search":
            reply = apps.photos_search(settings, key, query=request.get("query", ""), place=request.get("place", ""),
                                       after=request.get("after", ""), before=request.get("before", ""))
            self.store.log("photos-search", str(request.get("query") or request.get("place") or "dates")[:80],
                           f"{len(reply['results'])} photos")
            return reply
        if action == "news-digest":
            items = apps.unread_news(settings, key)
            self.digest_called_at = time.time()
            self.store.log("news-digest", "unread news", f"{len(items)} stories")
            return {"items": items, "text": apps.digest_text(items)}
        if action == "watch-list":
            return self._watch_list(settings, key)
        return self._watch_change(config, settings, key, action, request)

    def _watch_list(self, settings, key):
        """The watched pages, with each price watch's limit and the last price Portal saw."""
        listing = apps.watches(settings, key)
        with self.store.lock:
            state = self.store.watch_state()
        watches = [{"id": uuid, "title": str(watch.get("title") or "")[:200], "url": str(watch.get("url", ""))[:500],
                    "lastChanged": watch.get("last_changed") or 0, "below": state.get(uuid, {}).get("below"),
                    "lastPrice": state.get(uuid, {}).get("lastPrice")}
                   for uuid, watch in list(listing.items())[:apps.MAX_WATCHES]]
        return {"watches": watches, "total": len(listing)}

    def watch_overview(self):
        """The watched pages for Settings."""
        # Saving refuses an app turned on without its key.
        return self._watch_list(apps.app_settings(self.store.config(), "watch"), self._key("watch"))

    def send_digest_now(self):
        """Send the owner today's news brief right away, without waiting for the scheduled time."""
        config = self.store.config()
        settings = apps.app_settings(config, "news")
        if not config["notifications"]["enabled"]:
            raise IntegrationError("notifications-off", "Turn notifications on and save before sending a digest.")
        items = apps.unread_news(settings, self._key("news"))
        send_notification(config, self.store.secrets(), apps.DIGEST_NAME,
                          apps.digest_brief(items, len(items) >= apps.MAX_NEWS_ITEMS))
        self.store.log("notify", apps.DIGEST_NAME, "sent")
        shown = min(len(items), apps.MAX_BRIEF_STORIES)
        if not shown:
            return "Digest sent: there is no unread news."
        return f"Digest sent with {shown} {'story' if shown == 1 else 'stories'}."

    def _watch_change(self, config, settings, key, action, request):
        if action == "watch-add":
            url, title = apps.valid_watch_url(request.get("url")), str(request.get("title") or "").strip()[:200]
            below = None if request.get("below") in (None, "") else apps.price_limit(request.get("below"))
            summary = f"Watch {title or url}" + (f" for a price at or under {below:g}" if below is not None else "")
            details = ("watch-add", url, title, below)
            unchanged = self._already_watched(settings, key, url, title, below)
            if unchanged:
                return unchanged  # Nothing would change, so there is nothing to approve.
        else:
            uuid = str(request.get("id", ""))
            watch = apps.watches(settings, key).get(uuid)
            if watch is None:
                raise IntegrationError("watch-missing", "No watch has that id. List the watches first.")
            summary, details = f"Stop watching {watch.get('title') or watch.get('url')}", ("watch-remove", uuid)
        if config["apps"]["watch"]["changes"] == "ask":
            waiting = self._approval("watch", summary, digest(*details))
            if waiting:
                return waiting
        with self.store.lock:
            state = self.store.watch_state()
            if action == "watch-add":
                listing = apps.watches(settings, key)
                uuid = next((uuid for uuid, watch in listing.items() if watch.get("url") == url), None)
                if uuid is not None:
                    # Asked again, often a retried call: keep the one watch, so each change notifies once.
                    summary = self._rewatch(state, uuid, title or listing[uuid].get("title") or url, below)
                else:
                    uuid = apps.add_watch(settings, key, url, title, below)
                    # Not yet seen: the first look only records the page (a price watch still checks its price).
                    state[uuid] = {"lastChanged": None, "below": below, "lastPrice": None}
            else:
                apps.remove_watch(settings, key, uuid)
                state.pop(uuid, None)
            self.store.save_watch_state(state)
        self.store.log(action, summary, "done")
        return {"done": summary, "id": uuid}

    def _already_watched(self, settings, key, url, title, below):
        """The reply for a page watched already with this limit (or none asked), else None."""
        listing = apps.watches(settings, key)
        uuid = next((uuid for uuid, watch in listing.items() if watch.get("url") == url), None)
        if uuid is None:
            return None
        with self.store.lock:
            limit = (self.store.watch_state().get(uuid) or {}).get("below")
        if below is not None and limit != below:
            return None
        return {"done": f"{title or listing[uuid].get('title') or url} is already watched", "id": uuid}

    @staticmethod
    def _rewatch(state, uuid, name, below):
        """A page already watched: its price limit changes, or nothing does; the summary to report."""
        entry = state.setdefault(uuid, {"lastChanged": None, "below": None, "lastPrice": None})
        if below is None or entry["below"] == below:
            return f"{name} is already watched"
        if entry["below"] is None:
            # A page watch compares the page text; reading a price needs a price watch of its own.
            raise IntegrationError("watch-exists", f"{name} is already watched for any change. Remove that watch first "
                                   "to watch its price instead.")
        entry["below"] = below
        return f"Price limit for {name} is now {below:g}"

    def check_watches(self):
        """Tell the owner about watched pages that changed since the last look."""
        config = self.store.config()
        settings, key = config["apps"]["watch"], self._key("watch")
        if not (settings["enabled"] and key):
            return
        listing = apps.watches(settings, key)
        with self.store.lock:
            state = self.store.watch_state()
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
            current = self.store.watch_state()
            fresh = {uuid: entry for uuid, entry in current.items() if uuid not in listing and entry.get("lastChanged") is None}
            seen = {uuid: entry for uuid, entry in kept.items() if uuid in current or uuid not in state}
            self.store.save_watch_state({**fresh, **seen})
        for notice in notices:
            self._tell(config, notice["title"], notice["message"], link=notice["link"])

    UNREAD = object()

    def _price(self, settings, key, uuid, watch):
        """One watch's price, or UNREAD when it cannot be read now; one broken watch must not stop the others."""
        try:
            return apps.watch_price(settings, key, uuid)
        except IntegrationError as error:
            self.store.log("watch-check", str(watch.get("title") or watch.get("url") or uuid)[:80], error.message)
            return self.UNREAD

    # Scheduled tasks.
    def task_finished(self, config, event):
        """A scheduled task finished: the daily digest goes to the owner; others as they chose."""
        news = config["apps"]["news"]
        if event.get("description") == apps.DIGEST_MARKER and news["enabled"] and news["digest"]["enabled"]:
            return {"notified": self._send_digest(config, event)}
        job = str(event.get("jobId") or event.get("name") or "")
        was_failing = job in self.failing_tasks
        if event.get("status") == "error":
            self.failing_tasks.add(job)
        elif event.get("status") == "ok":
            self.failing_tasks.discard(job)
        notice = apps.task_notice(config["notifications"]["scheduledTasks"], event, was_failing)
        if notice is None:
            return {"notified": False}
        return {"notified": self._tell(config, notice["title"], notice["message"], notice["priority"])}

    def _send_digest(self, config, event):
        """The brief Portal wrote in this run, or the plain brief of unread stories when it wrote none."""
        started = (event.get("runAtMs") or 0) / 1000
        summary = str(event.get("summary") or "").strip()
        if event.get("status") == "ok" and summary and self.digest_called_at >= started:
            text = apps.fit_notice(summary)
        else:
            try:
                items = apps.unread_news(config["apps"]["news"], self._key("news"))
            except IntegrationError as error:
                # The owner expects the digest at this time; say why it did not come instead of staying silent.
                return self._tell(config, apps.DIGEST_NAME, f"Today's digest could not be made. {error.message}", "high")
            text = apps.digest_brief(items, len(items) >= apps.MAX_NEWS_ITEMS)
        return self._tell(config, apps.DIGEST_NAME, text)

    def _tell(self, config, title, message, priority="default", link=""):
        """Send the owner a notification; a failure is logged, since nobody is waiting for it."""
        if not config["notifications"]["enabled"]:
            return False
        try:
            send_notification(config, self.store.secrets(), title, message, priority, link, link_label="Open")
        except IntegrationError as error:
            self.store.log("notify", title, error.message)
            return False
        self.store.log("notify", title, "sent")
        return True
