"""Host-agent side of connected apps: the owner's actions and Google's loopback redirect.

It runs where the owner's browser is: on the ODS machine, and on Windows for a WSL
install. Root decides everything; this module parses and relays requests, and
serves the one-shot page that Google's redirect lands on. That page never logs
its request line, because the address holds the sign-in code.
"""
from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from typing import Any, Callable
import urllib.parse

from . import oauth

DISCORD = "https://discord.gg/4ntNp9MAwC"
Relay = Callable[..., tuple]
BEGIN_KEYS = {"client", "capabilities", "cloudModels", "ownerTimeZone", "loginHint"}
# Sign-in results the owner fixes by simply trying again; everything else is a real failure.
OWNER_FIXABLE = {"access-denied", "consent-failed", "missing-scope", "flow-expired"}


class _Listener:
    """One loopback address for one sign-in, open until Google's redirect is handled."""

    def __init__(self):
        listener = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server's method name
                parts = urllib.parse.urlsplit(self.path)
                if parts.path != "/" or not parts.query:
                    self.send_error(404)
                    return
                title, message, help_link, finished = listener.callback(parts.query)
                page = _page(title, message, help_link).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(page)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
                self.end_headers()
                self.wfile.write(page)
                if finished:
                    threading.Thread(target=listener.close, daemon=True).start()

            def log_message(self, format, *args):  # noqa: A002 - http.server's signature
                pass  # The request line carries the sign-in code; nothing about it is logged.

        self._handle: Callable[[str], tuple] | None = None
        # One redirect is handled at a time; closing never waits for one in progress.
        self._handling = threading.Lock()
        self._lock = threading.Lock()
        self._closed = False
        self._serving = False
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.flow_id: str | None = None
        self._timer: threading.Timer | None = None

    def serve(self, flow_id: str, handle: Callable[[str], tuple]) -> None:
        self.flow_id, self._handle = flow_id, handle
        self._serving = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._timer = threading.Timer(oauth.FLOW_SECONDS, self.close)
        self._timer.daemon = True
        self._timer.start()

    def callback(self, query: str) -> tuple:
        with self._handling:
            with self._lock:
                handle = None if self._closed else self._handle
            if handle is None:
                return "This sign-in has ended", "Go back to ODS and start again.", None, True
            return handle(query)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._timer is not None:
            self._timer.cancel()
        # shutdown() waits for serve_forever, so it would block on a listener never served.
        if self._serving:
            self.server.shutdown()
        self.server.server_close()


def _page(title: str, message: str, help_link: str | None) -> str:
    link = (f'<p>Need help? <a href="{help_link}" rel="noreferrer">Ask on the ODS Discord</a></p>'
            if help_link else "")
    return ("<!doctype html><html lang=\"en\"><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>ODS</title>"
            "<body style=\"font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem;"
            "line-height:1.5\">"
            f"<h1 style=\"font-size:1.25rem\">{html.escape(title)}</h1><p>{html.escape(message)}</p>{link}</body></html>")


_active: dict[str, _Listener | None] = {"listener": None}
_active_lock = threading.Lock()


def _replace_listener(listener: _Listener | None) -> None:
    with _active_lock:
        previous, _active["listener"] = _active["listener"], listener
    if previous is not None and previous is not listener:
        previous.close()


def _close_listener(flow_id: str) -> None:
    with _active_lock:
        listener = _active["listener"]
        if listener is None or listener.flow_id != flow_id:
            return
        _active["listener"] = None
    listener.close()


def _activate_later(relay: Relay, config: dict, log: Callable[[str], None]) -> None:
    """Update Portal's gateway in the background; root records any failure for Settings."""
    def run():
        try:
            relay("connection-activate", {}, config=config)
        except (OSError, ValueError, RuntimeError) as error:
            # Root's journal still says the step is pending; Settings offers Finish.
            log(f"connected apps: Portal's gateway update did not run ({type(error).__name__})")
    threading.Thread(target=run, daemon=True).start()


def _complete(flow_id: str, fields: dict[str, str], *, config: dict, relay: Relay,
              log: Callable[[str], None]) -> tuple:
    status, reply = relay("connection-complete", {"flowId": flow_id, **fields}, config=config)
    if status == 200 and reply["outcome"] == "connected":
        _activate_later(relay, config, log)
    return status, reply


def _redirect_page(flow_id: str, *, config: dict, relay: Relay, log: Callable[[str], None]) -> Callable[[str], tuple]:
    def handle(query: str) -> tuple:
        try:
            fields = oauth.callback_fields(query)
        except oauth.OAuthError:
            return ("This isn't the page Google sends you to", "Go back to ODS and start again.", None, False)
        try:
            status, reply = _complete(flow_id, fields, config=config, relay=relay, log=log)
        except (OSError, ValueError, RuntimeError):
            return ("ODS couldn't finish connecting", "Go back to ODS and try again.", DISCORD, True)
        if status == 200 and reply["outcome"] == "connected":
            return ("Google is connected", "You can close this tab and go back to ODS.", None, True)
        if status == 200:
            needs_help = reply["errorClass"] not in OWNER_FIXABLE
            return ("Google didn't connect", "Go back to ODS. Settings shows what happened and what to do next.",
                    DISCORD if needs_help else None, True)
        if reply.get("error") == "sign-in-state-mismatch":
            # Another tab's address; the owner's own sign-in stays open.
            return ("This sign-in page is out of date", "Go back to ODS and start again.", None, False)
        return ("This sign-in has ended", "Go back to ODS and start again.", None, True)
    return handle


def begin(body: Any, *, config: dict, relay: Relay, log: Callable[[str], None] = print) -> tuple:
    """Start a sign-in and open the loopback address Google will redirect the browser to."""
    if not isinstance(body, dict) or set(body) != BEGIN_KEYS:
        return 400, {"error": "invalid-request"}
    try:
        client = oauth.parse_client(body["client"])
    except oauth.OAuthError as error:
        return 400, {"error": error.code}
    listener = _Listener()
    request = {"connector": "google", "client": client, "capabilities": body["capabilities"],
               "cloudModels": body["cloudModels"], "ownerTimeZone": body["ownerTimeZone"],
               "loginHint": body["loginHint"], "redirectPort": listener.port}
    serving = False
    try:
        status, reply = relay("connection-begin", request, config=config)
        if status != 200:
            return status, reply
        _replace_listener(listener)
        listener.serve(reply["flowId"], _redirect_page(reply["flowId"], config=config, relay=relay, log=log))
        serving = True
        return 200, {"flowId": reply["flowId"], "authUrl": reply["authUrl"], "expiresAt": reply["expiresAt"],
                     "redirect": "loopback"}
    finally:
        if not serving:
            listener.close()


def complete(body: Any, *, config: dict, relay: Relay, log: Callable[[str], None] = print) -> tuple:
    """Finish a sign-in with the address the owner copied from another device's browser."""
    if (not isinstance(body, dict) or set(body) != {"flowId", "address"} or type(body["flowId"]) is not str
            or not oauth.FLOW_ID.fullmatch(body["flowId"])):
        return 400, {"error": "invalid-request"}
    try:
        fields = oauth.callback_fields(oauth.pasted_query(body["address"]))
    except oauth.OAuthError as error:
        return 400, {"error": error.code}
    status, reply = _complete(body["flowId"], fields, config=config, relay=relay, log=log)
    if status == 200:
        # The redirect came another way, so the loopback address has nothing left to wait for.
        _close_listener(body["flowId"])
    return status, reply


def cancel(body: Any, *, config: dict, relay: Relay, log: Callable[[str], None] = print) -> tuple:
    if (not isinstance(body, dict) or set(body) != {"flowId"} or type(body["flowId"]) is not str
            or not oauth.FLOW_ID.fullmatch(body["flowId"])):
        return 400, {"error": "invalid-request"}
    _close_listener(body["flowId"])
    return relay("connection-cancel", {"flowId": body["flowId"]}, config=config)


def disconnect(body: Any, *, config: dict, relay: Relay, log: Callable[[str], None] = print) -> tuple:
    if body != {}:
        return 400, {"error": "invalid-request"}
    status, reply = relay("connection-disconnect", {"connector": "google"}, config=config)
    if status == 200:
        _activate_later(relay, config, log)
    return status, reply


def sync(body: Any, *, config: dict, relay: Relay, log: Callable[[str], None] = print) -> tuple:
    if body != {}:
        return 400, {"error": "invalid-request"}
    return relay("connection-sync", {}, config=config)


def finish(body: Any, *, config: dict, relay: Relay, log: Callable[[str], None] = print) -> tuple:
    """The owner's Finish button: bring Portal's gateway in line with the record now."""
    if body != {}:
        return 400, {"error": "invalid-request"}
    return relay("connection-activate", {}, config=config)


ACTIONS = {"begin": begin, "complete": complete, "cancel": cancel, "disconnect": disconnect, "sync": sync,
           "finish": finish}
