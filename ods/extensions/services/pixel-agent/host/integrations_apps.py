"""The owner's own apps for Portal: documents, photos, watched web pages and news feeds.

Each app runs on this computer, usually installed from the ODS extension catalog:
Paperless-ngx (documents), Immich (photos), changedetection.io (watched pages) and FreshRSS
(news). Portal reaches them only through the narrow requests below, with the owner's token
kept by the integrations service. Documents, photos and news are only read; adding or
removing a page watch follows the owner's choice, ask first or allowed.
"""
import datetime
import html
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import zoneinfo

from integrations_common import APP_URLS, IntegrationError, NETWORK_TIMEOUT, clip

APPS = tuple(APP_URLS)
NAMES = {"documents": "Paperless-ngx", "photos": "Immich", "watch": "changedetection.io", "news": "FreshRSS"}
MAX_APP_BYTES = 4 * 1024 * 1024
MAX_HITS = 10
MAX_DOCUMENT_CHARS = 12000
MAX_NEWS_ITEMS = 40
MAX_BRIEF_STORIES = 15
MAX_WATCHES = 100
# ntfy turns a longer message into a file attachment (4,096 bytes); stay under it.
MAX_NOTICE_BYTES = 3900
DIGEST_NAME = "Daily news digest"
DIGEST_MARKER = "Created by Portal integrations for the daily news digest."
# Portal reaches plugin tools through tool_call, so the prompt names the exact call.
DIGEST_PROMPT = ('Use tool_call with id "pixel_ods_mcp" and args {"action": "call", "server": "news", "tool": "digest", '
                 '"arguments": {}}. From its result, write the '
                 f"owner's news brief for today: one short line per story, grouped by topic, at most {MAX_BRIEF_STORIES} "
                 "stories, without links. Do not search the web. Reply with the brief only.")
# A run that takes longer ends as an error, and the owner gets the plain list of stories instead.
DIGEST_SECONDS = 600
# The run may only read the news: with web search at hand the small model wandered off for minutes.
DIGEST_TOOLS = ["pixel_ods_mcp"]
TIME = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
TIMEZONE = re.compile(r"^[A-Za-z][A-Za-z0-9_+/-]{0,63}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TAG = re.compile(r"<[^>]+>")
PRICE = re.compile(r"Price:\s*([0-9]+(?:\.[0-9]+)?)")


def plain(value, limit=300):
    """Text from an app's HTML snippet, on one line."""
    text = html.unescape(TAG.sub("", value or ""))
    return " ".join(text.split())[:limit]


def count(number, thing):
    return f"{number} {thing}" + ("" if number == 1 else "s")


def fit_notice(text, limit=MAX_NOTICE_BYTES):
    """Text that fits in one ntfy message, cut at a line or word when it is too long."""
    raw = text.encode()
    if len(raw) <= limit:
        return text
    cut = raw[:limit - 4].decode("utf-8", "ignore")  # Room for " …"; a split character is dropped.
    edge = max(cut.rfind("\n"), cut.rfind(" "))
    if edge > 0:
        cut = cut[:edge]
    return cut.rstrip() + " …"


def app_settings(config, app):
    settings = config["apps"][app]
    if not settings["enabled"]:
        raise IntegrationError("app-off", f"{NAMES[app]} is not set up. The owner can connect it in Settings → Portal integrations.")
    return settings


# ---------------------------------------------------------------- HTTP

def request(app, url, *, method="GET", headers=None, body=None, form=None, parse=True):
    """One request to an owner's app; its JSON answer (or text when parse is False)."""
    data = json.dumps(body).encode() if body is not None else urllib.parse.urlencode(form).encode() if form else None
    sent = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        sent["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=sent, method=method),
                                    timeout=NETWORK_TIMEOUT) as response:
            raw = response.read(MAX_APP_BYTES + 1)
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise IntegrationError("app-refused", f"{NAMES[app]} refused the token or password. Check it in Settings → Portal integrations.") from None
        if error.code == 404:
            raise IntegrationError("app-not-found", f"{NAMES[app]} has no such item, or this is not its address.") from None
        raise IntegrationError("app-failed", f"{NAMES[app]} answered {error.code}.") from None
    except (urllib.error.URLError, OSError):
        raise IntegrationError("app-unreachable", f"{NAMES[app]} at {url.split('/api')[0]} could not be reached.") from None
    if len(raw) > MAX_APP_BYTES:
        raise IntegrationError("app-too-large", f"{NAMES[app]} sent too much at once.")
    if not parse:
        return raw.decode("utf-8", "replace")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise IntegrationError("app-protocol", f"{NAMES[app]} did not answer as expected. Check its address.") from None


def _shape(app, check):
    """Raise when an app's answer lacks what Portal needs."""
    if not check:
        raise IntegrationError("app-protocol", f"{NAMES[app]} did not answer as expected. Check its address.")


def _records(value, *keys):
    """Whether value is a list of objects, each with these keys."""
    return isinstance(value, list) and all(isinstance(item, dict) and all(key in item for key in keys) for item in value)


# ---------------------------------------------------------------- documents (Paperless-ngx)

def _paperless(settings, token, path, **params):
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    return request("documents", f"{settings['url']}{path}{query}", headers={"Authorization": f"Token {token}"})


def documents_search(settings, token, query):
    query = " ".join(str(query or "").split())[:200]
    if not query:
        raise IntegrationError("invalid-request", "Give words to look for, such as \"car insurance\".")
    reply = _paperless(settings, token, "/api/documents/", query=query, page_size=MAX_HITS)
    _shape("documents", isinstance(reply, dict) and _records(reply.get("results"), "id"))
    hits = []
    for item in reply["results"][:MAX_HITS]:
        hit = item.get("__search_hit__") or {}
        snippet = plain(hit.get("highlights")) or plain(item.get("content"), 200)
        hits.append({"id": item["id"], "title": str(item.get("title", ""))[:200], "created": str(item.get("created", ""))[:10],
                     "snippet": snippet, "link": f"{settings['url']}/documents/{item['id']}/details"})
    total = reply.get("count") if isinstance(reply.get("count"), int) else len(hits)
    return {"results": hits, "total": max(total, len(hits))}


def documents_read(settings, token, document_id):
    if isinstance(document_id, str) and document_id.strip().isdecimal():
        document_id = int(document_id)  # The model often sends the id as text.
    if not isinstance(document_id, int) or isinstance(document_id, bool) or document_id < 1:
        raise IntegrationError("invalid-request", "Give the document id from a search.")
    item = _paperless(settings, token, f"/api/documents/{document_id}/")
    _shape("documents", isinstance(item, dict) and "content" in item)
    return {"id": document_id, "title": str(item.get("title", ""))[:200], "created": str(item.get("created", ""))[:10],
            "text": clip(str(item.get("content") or ""), MAX_DOCUMENT_CHARS),
            "link": f"{settings['url']}/documents/{document_id}/details"}


def documents_test(settings, token):
    reply = _paperless(settings, token, "/api/documents/", page_size=1)
    _shape("documents", isinstance(reply, dict) and "count" in reply)
    return f"Paperless-ngx answered: {count(reply['count'], 'document')}."


# ---------------------------------------------------------------- photos (Immich)

def _immich(settings, key, path, body=None):
    return request("photos", f"{settings['url']}{path}", method="POST" if body is not None else "GET",
                   headers={"x-api-key": key}, body=body)


def day(value):
    """A calendar date given as YYYY-MM-DD; Immich refuses one that does not exist, such as February 30."""
    if not DATE.match(str(value)):
        raise IntegrationError("invalid-request", "Give dates as YYYY-MM-DD.")
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        raise IntegrationError("invalid-request", f"{value} is not a date in the calendar.") from None


def describes_photos(settings, key):
    """Whether searching by what is in a photo is turned on in Immich; it also needs its machine learning running."""
    features = _immich(settings, key, "/api/server/features")
    _shape("photos", isinstance(features, dict))
    return features.get("smartSearch") is True


def _city(settings, key, place):
    """The city or country name Immich knows that matches the owner's words."""
    cities = _immich(settings, key, "/api/search/cities")
    _shape("photos", _records(cities))
    known = []
    for asset in cities:
        exif = asset.get("exifInfo") or {}
        known += [(field, exif[field]) for field in ("city", "country") if exif.get(field)]
    return best_place(place, known)


def best_place(place, known):
    """The best (field, name) in known for words such as "Lisbon" or "Lisbon, Portugal", or None.

    Each part between commas is tried in turn. An exact name wins over one that starts with the
    words, which wins over one that only holds them, so "Nice" finds Nice, not Venice.
    """
    for part in (piece.strip().casefold() for piece in place.split(",")):
        if not part:
            continue
        for matches in (lambda name: name == part, lambda name: name.startswith(part), lambda name: part in name):
            found = next(((field, name) for field, name in known if matches(name.casefold())), None)
            if found:
                return found
    return None


def photos_search(settings, key, *, query="", place="", after="", before=""):
    query, place = " ".join(str(query or "").split())[:200], " ".join(str(place or "").split())[:100]
    for value in (after, before):
        if value:
            day(value)
    if after and before and str(after) > str(before):
        raise IntegrationError("invalid-request", "The first date (after) must not come after the second (before).")
    if not (query or place or after or before):
        raise IntegrationError("invalid-request", "Say what is in the photos, where or when they were taken.")
    # Where and when narrow either search; withExif brings each photo's place back.
    filters = {"size": MAX_HITS, "withExif": True}
    if place:
        found = _city(settings, key, place)
        if found is None:
            return {"results": [], "mode": "details", "more": False, "note": f"No photos are from a place named {place}."}
        filters[found[0]] = found[1]
    if after:
        filters["takenAfter"] = f"{after}T00:00:00.000Z"
    if before:
        filters["takenBefore"] = f"{before}T23:59:59.999Z"
    note = ""
    if query and describes_photos(settings, key):
        try:
            reply = _immich(settings, key, "/api/search/smart", {"query": query, **filters})
        except IntegrationError as error:
            if error.code != "app-failed":
                raise
            # Turned on, but Immich could not reach its machine learning.
            raise IntegrationError("photos-no-description", "Immich could not search by what is in a photo; its machine "
                                   "learning may not be running. Search by place or date instead.") from None
        mode = "description"
    elif query and not (place or after or before):
        raise IntegrationError("photos-no-description", "Immich cannot search by what is in a photo until its machine "
                               "learning is running. Search by place or date instead.")
    else:
        if query:
            note = (f"Searched by place and date only, not for \"{query}\": searching by what is in a photo needs "
                    "Immich's machine learning.")
        reply = _immich(settings, key, "/api/search/metadata", {**filters, "order": "desc"})
        mode = "details"
    _shape("photos", isinstance(reply, dict) and isinstance(reply.get("assets"), dict) and _records(reply["assets"].get("items"), "id"))
    found = reply["assets"]["items"]
    # Immich gives the next page's number while more photos match.
    more = len(found) > MAX_HITS or bool(reply["assets"].get("nextPage"))
    results = []
    for asset in found[:MAX_HITS]:
        exif = asset.get("exifInfo") or {}
        where = ", ".join(part for part in (exif.get("city"), exif.get("country")) if part)
        results.append({"id": asset["id"], "file": str(asset.get("originalFileName", ""))[:200],
                        "taken": str(asset.get("localDateTime") or asset.get("fileCreatedAt") or "")[:10], "place": where,
                        "link": f"{settings['url']}/photos/{asset['id']}"})
    return {"results": results, "mode": mode, "more": more, **({"note": note} if note else {})}


def _machine_learning_runs(settings, key):
    """Whether Immich can search by what is in a photo right now: one tiny search tells."""
    try:
        _immich(settings, key, "/api/search/smart", {"query": "photo", "size": 1})
    except IntegrationError as error:
        if error.code != "app-failed":
            raise
        return False
    return True


def photos_test(settings, key):
    me = _immich(settings, key, "/api/users/me")
    _shape("photos", isinstance(me, dict) and "name" in me)
    answered = f"Immich answered as {me['name']}."
    if not describes_photos(settings, key):
        return (f"{answered} Searching by place and date works. To also search by what is in a photo, turn on Smart "
                "Search in Immich under Administration → Settings → Machine Learning.")
    if not _machine_learning_runs(settings, key):
        return (f"{answered} Searching by place and date works. Searching by what is in a photo needs Immich's machine "
                "learning, which is not running. Start Immich's machine-learning service to use it.")
    return f"{answered} Searching by place, date and what is in a photo works."


# ---------------------------------------------------------------- watched pages (changedetection.io)

def _changes(settings, key, path, *, method="GET", body=None):
    return request("watch", f"{settings['url']}/api/v1{path}", method=method, headers={"x-api-key": key}, body=body)


def watches(settings, key):
    """Every watch, as {uuid: {...}}, the way changedetection.io lists them."""
    reply = _changes(settings, key, "/watch")
    # Every entry is looked into; one of another shape would stop the page watcher.
    _shape("watch", isinstance(reply, dict) and all(isinstance(watch, dict) for watch in reply.values()))
    return reply


def watch_price(settings, key, uuid):
    """The price changedetection.io last read from a product page, or None before its first look.

    Its API leaves the price out of a watch's details; the latest snapshot of a price watch
    reads like "In Stock: True - Price: 60.0".
    """
    try:
        text = request("watch", f"{settings['url']}/api/v1/watch/{uuid}/history/latest", headers={"x-api-key": key}, parse=False)
    except IntegrationError as error:
        if error.code == "app-not-found":
            return None  # No snapshot yet.
        raise
    found = PRICE.search(text)
    return float(found.group(1)) if found else None


def valid_watch_url(url):
    url = str(url or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if (len(url) > 2000 or parsed.scheme not in ("http", "https") or not parsed.hostname
            or any(char.isspace() or ord(char) < 32 for char in url)):
        raise IntegrationError("invalid-request", "Give the page's full web address, starting with https://.")
    return url


def price_limit(value):
    """The owner's price limit as a positive number; the model often sends it as text, such as "49.99"."""
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            value = None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise IntegrationError("invalid-request", "Give the price limit as a number, such as 49.99.")
    return float(value)


def add_watch(settings, key, url, title, below):
    """A new watch; a price watch reads the product's price and stock instead of the page text."""
    body = {"url": url, "title": title[:200]} if title else {"url": url}
    if below is not None:
        body["processor"] = "restock_diff"
    reply = _changes(settings, key, "/watch", method="POST", body=body)
    _shape("watch", isinstance(reply, dict) and isinstance(reply.get("uuid"), str))
    return reply["uuid"]


def remove_watch(settings, key, uuid):
    _changes(settings, key, f"/watch/{uuid}", method="DELETE")


def watch_test(settings, key):
    return f"changedetection.io answered: {count(len(watches(settings, key)), 'watched page')}."


def prices_to_read(listing, state):
    """The price watches whose price may have moved since the last look.

    changedetection.io marks a watch changed when its price or stock changes, so a price is
    read only for a watch Portal has not priced yet, or one that changed since the last look.
    """
    wanted = []
    for uuid, watch in listing.items():
        entry = state.get(uuid) or {}
        if entry.get("below") is None:
            continue
        changed = watch.get("last_changed") if isinstance(watch.get("last_changed"), int) else 0
        if entry.get("lastPrice") is None or entry.get("lastChanged") is None or changed > entry["lastChanged"]:
            wanted.append(uuid)
    return wanted


def page_notices(listing, state, prices):
    """What to tell the owner about watched pages since the last look, and the state to keep.

    listing is changedetection.io's watch list; state holds, per watch, the last change seen
    (None until the first look), a price limit and the last price; prices holds the current
    price of each price watch. A page watch notifies when the page changed after it was first
    seen. A price watch notifies when the price is at or under its limit, including at the
    first look, and not again until the price has gone back above it.
    """
    notices, kept = [], {}
    for uuid, watch in listing.items():
        changed = watch.get("last_changed") if isinstance(watch.get("last_changed"), int) else 0
        before = state.get(uuid) or {}
        entry = {"lastChanged": changed, "below": before.get("below"), "lastPrice": before.get("lastPrice")}
        title = str(watch.get("title") or watch.get("url") or "A watched page")[:200]
        # A watch made in changedetection.io itself may hold any address; the phone opens only web pages.
        link = str(watch.get("url", ""))
        link = link if link.lower().startswith(("http://", "https://")) else ""
        if entry["below"] is None:
            if before.get("lastChanged") is not None and changed > before["lastChanged"]:
                notices.append({"title": f"Page changed: {title}", "message": f"{title} changed.", "link": link})
        elif prices.get(uuid) is not None:
            price = prices[uuid]
            if price <= entry["below"] and (entry["lastPrice"] is None or entry["lastPrice"] > entry["below"]):
                notices.append({"title": f"Price drop: {title}",
                                "message": f"{title} is now {price:g}, at or under your limit of {entry['below']:g}.",
                                "link": link})
            entry["lastPrice"] = price
        kept[uuid] = entry
    return notices, kept


# ---------------------------------------------------------------- news (FreshRSS)

def _reader(settings, password):
    """FreshRSS's Google Reader API, signed in as the owner with their API password."""
    base = f"{settings['url']}/api/greader.php"
    reply = request("news", f"{base}/accounts/ClientLogin", method="POST",
                    form={"Email": settings["username"], "Passwd": password}, parse=False)
    auth = next((line[5:] for line in reply.splitlines() if line.startswith("Auth=")), "")
    _shape("news", auth)
    return base, {"Authorization": f"GoogleLogin auth={auth}"}


def unread_news(settings, password, limit=MAX_NEWS_ITEMS):
    base, headers = _reader(settings, password)
    stream = urllib.parse.quote("user/-/state/com.google/reading-list", safe="")
    query = urllib.parse.urlencode({"xt": "user/-/state/com.google/read", "n": limit, "output": "json"})
    reply = request("news", f"{base}/reader/api/0/stream/contents/{stream}?{query}", headers=headers)
    _shape("news", isinstance(reply, dict) and _records(reply.get("items")))
    items = []
    for item in reply["items"][:limit]:
        links = item.get("canonical") or item.get("alternate") or [{}]
        items.append({"feed": str((item.get("origin") or {}).get("title", ""))[:100], "title": plain(item.get("title"), 200),
                      "link": str(links[0].get("href", ""))[:500], "summary": plain((item.get("summary") or {}).get("content"), 240)})
    return items


def by_feed(items):
    """Stories grouped by feed, feeds in the order their first story came (newest first)."""
    feeds = {}
    for item in items:
        feeds.setdefault(item["feed"] or "News", []).append(item)
    return feeds


def digest_text(items):
    """Unread stories grouped by feed, with links: what Portal reads when asked for the news."""
    if not items:
        return "No unread news."
    return "\n\n".join(f"{feed}:\n" + "\n".join(f"- {item['title']}" + (f" ({item['link']})" if item["link"] else "")
                                             for item in stories) for feed, stories in by_feed(items).items())


def digest_brief(items, capped=False):
    """The digest for a phone: story titles grouped by feed, at most MAX_BRIEF_STORIES of them.

    capped means FreshRSS had more unread stories than Portal fetched.
    """
    if not items:
        return "No unread news."
    shown = items[:MAX_BRIEF_STORIES]
    brief = "\n\n".join(f"{feed}:\n" + "\n".join(f"- {item['title']}" for item in stories)
                         for feed, stories in by_feed(shown).items())
    left = len(items) - len(shown)
    if left:
        brief += f"\n\n…and {left}{'+' if capped else ''} more in FreshRSS."
    return fit_notice(brief)


def news_test(settings, password):
    base, headers = _reader(settings, password)
    reply = request("news", f"{base}/reader/api/0/subscription/list?output=json", headers=headers)
    _shape("news", isinstance(reply, dict) and isinstance(reply.get("subscriptions"), list))
    return f"FreshRSS answered: {count(len(reply['subscriptions']), 'feed')}."


def digest_plan(settings):
    """The scheduled task the gateway should keep for the daily digest, in OpenClaw's cron terms."""
    news = settings["apps"]["news"]
    hour, minute = news["digest"]["time"].split(":")
    return {"enabled": news["enabled"] and news["digest"]["enabled"], "name": DIGEST_NAME, "description": DIGEST_MARKER,
            "expr": f"{int(minute)} {int(hour)} * * *", "tz": news["digest"]["timezone"], "message": DIGEST_PROMPT,
            "timeoutSeconds": DIGEST_SECONDS, "tools": DIGEST_TOOLS}


# ---------------------------------------------------------------- scheduled tasks

def task_notice(mode, event, was_failing):
    """The notification for a finished scheduled task, or None. mode is off, failures or all.

    was_failing means the task's previous run failed too. OpenClaw retries a failed task a few
    times within minutes, so only the first failure in a row notifies, and the next good run
    says the task works again.
    """
    status = event.get("status")
    if mode == "off" or status not in ("ok", "error"):
        return None
    name = str(event.get("name") or "A scheduled task")[:120]
    if status == "error":
        if was_failing:
            return None
        reason = clip(str(event.get("error") or "It stopped with an error."), 1500)
        return {"title": f"Scheduled task failed: {name}", "priority": "high",
                "message": f"{reason}\n\nOpenClaw may try it again. You hear from Portal again when it works."}
    if mode == "failures" and not was_failing:
        return None
    title = f"Scheduled task works again: {name}" if was_failing else f"Scheduled task done: {name}"
    return {"title": title, "priority": "default", "message": clip(str(event.get("summary") or "It finished."), 1500)}


# ---------------------------------------------------------------- settings

def validate_apps(value, text, http_url):
    """The owner's app settings; text and http_url are the service's field checks."""
    if not isinstance(value, dict) or set(value) != set(APPS):
        raise IntegrationError("invalid-settings", "Check the apps.")
    result = {}
    for app in APPS:
        entry = value[app]
        expected = {"enabled", "url"} | {"watch": {"changes"}, "news": {"username", "digest"}}.get(app, set())
        if not isinstance(entry, dict) or set(entry) != expected:
            raise IntegrationError("invalid-settings", f"Check the {NAMES[app]} settings.")
        result[app] = {"enabled": entry["enabled"] is True, "url": http_url(entry["url"], f"The {NAMES[app]} address")}
    if value["watch"]["changes"] not in ("ask", "allow"):
        raise IntegrationError("invalid-settings", "Adding and removing watches is ask first or allowed.")
    result["watch"]["changes"] = value["watch"]["changes"]
    result["news"]["username"] = text(value["news"]["username"], "the FreshRSS user", 100)
    if result["news"]["enabled"] and not result["news"]["username"]:
        raise IntegrationError("invalid-settings", "Give your FreshRSS user name.")
    digest = value["news"]["digest"]
    if (not isinstance(digest, dict) or set(digest) != {"enabled", "time", "timezone"}
            or not isinstance(digest["time"], str) or not TIME.match(digest["time"])
            or not isinstance(digest["timezone"], str) or not TIMEZONE.match(digest["timezone"])):
        raise IntegrationError("invalid-settings", "Give the digest time as HH:MM.")
    # OpenClaw refuses a cron job in a time zone it does not know, and the digest would never come.
    if digest["enabled"] is True and not known_timezone(digest["timezone"]):
        raise IntegrationError("invalid-settings", f"{digest['timezone']} is not a time zone. Choose one such as Europe/Lisbon.")
    result["news"]["digest"] = {"enabled": digest["enabled"] is True, "time": digest["time"], "timezone": digest["timezone"]}
    return result


def known_timezone(name):
    """Whether name is an IANA time zone; UTC always is, even where the zone files are missing."""
    if name == "UTC":
        return True
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return False
    return True
