"""Owner integrations for Portal: an Obsidian vault, ntfy notifications and MCP servers.

One local service holds the owner's integration settings and secrets and performs every
integration action, so the sandboxed gateway never holds a token or a vault path. Requests
are one JSON line over a Unix socket. The gateway runs as the owner's user, so the socket,
not the caller's uid, sets what a request may do: the agent socket under /run is the only
one the gateway sandbox can see, and the owner socket sits in the state folder, which its
ProtectHome=tmpfs hides. The owner (through the host agent) changes settings and decides
approvals. The gateway may only use what the owner turned on: search and read notes, write
notes as the owner allowed, send notifications, and call the MCP tools the owner allowed. A
tool or note change set to "ask first" waits for the owner's approval, bound to the exact
call and usable once.
"""
import base64
import json
import os
import pathlib
import re
import shlex
import socket
import socketserver
import stat
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import integrations_notes as notes  # noqa: E402
from integrations_common import (IntegrationError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,  # noqa: E402
                                 NETWORK_TIMEOUT, SCHEMA_VERSION, Store, digest, now)
from integrations_mcp import McpSession, describe_tool  # noqa: E402

MAX_SERVERS = 20
APPROVAL_SECONDS = 24 * 3600
POLICIES = ("off", "ask", "allow")
SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
OWNER_ACTIONS = {"get", "save", "test-notification", "discover", "decide"}
OWNER_SOCKET = "owner.sock"


# ---------------------------------------------------------------- settings

def _text(value, field, limit, *, empty=True):
    if (not isinstance(value, str) or len(value) > limit or (not empty and not value.strip())
            or any(ord(char) < 32 for char in value)):
        raise IntegrationError("invalid-settings", f"Check {field}.")
    return value.strip()


def _http_url(value, field):
    value = _text(value, field, 500, empty=False)
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise IntegrationError("invalid-settings", f"{field} must be an http or https address.")
    return value.rstrip("/")


def _notifications(value):
    if not isinstance(value, dict) or set(value) != {"enabled", "url", "topic", "username", "notifyApprovals"}:
        raise IntegrationError("invalid-settings", "Check the notification settings.")
    topic = _text(value["topic"], "the topic", 64, empty=False)
    if not TOPIC.match(topic):
        raise IntegrationError("invalid-settings", "The topic may use letters, numbers, - and _.")
    return {"enabled": value["enabled"] is True, "url": _http_url(value["url"], "The ntfy address"), "topic": topic,
            "username": _text(value["username"], "the ntfy user", 64), "notifyApprovals": value["notifyApprovals"] is True}


def _notes(value):
    if not isinstance(value, dict) or set(value) != {"enabled", "vaultPath", "write"} or value["write"] not in POLICIES:
        raise IntegrationError("invalid-settings", "Check the notes settings.")
    vault = _text(value["vaultPath"], "the vault folder", 1024)
    if value["enabled"] is True and not (pathlib.Path(vault).is_absolute() and pathlib.Path(vault).is_dir()):
        raise IntegrationError("invalid-settings", "The vault folder must be an existing folder, given as a full path.")
    return {"enabled": value["enabled"] is True, "vaultPath": vault, "write": value["write"]}


def _server(value, previous):
    """One MCP server; tools found earlier are kept while its address or command is unchanged."""
    if not isinstance(value, dict) or value.get("kind") not in ("http", "stdio") or value.get("preset", "") not in ("", "home-assistant"):
        raise IntegrationError("invalid-settings", "Check the MCP servers.")
    entry = {"id": value["id"], "name": _text(value.get("name", ""), "the server name", 80, empty=False),
             "kind": value["kind"], "enabled": value.get("enabled") is True, "preset": value.get("preset", ""),
             "url": "", "command": ""}
    if entry["kind"] == "http":
        entry["url"] = _http_url(value.get("url", ""), "The MCP server address")
    else:
        entry["command"] = _text(value.get("command", ""), "the command", 1000, empty=False)
        try:
            shlex.split(entry["command"])
        except ValueError:
            raise IntegrationError("invalid-settings", "The command is not valid.") from None
    same = previous is not None and all(previous[key] == entry[key] for key in ("kind", "url", "command"))
    policies = value.get("policies", {})
    if not isinstance(policies, dict) or any(policy not in POLICIES for policy in policies.values()):
        raise IntegrationError("invalid-settings", "Each tool is off, ask first or allowed.")
    entry["tools"] = [{**tool, "policy": policies.get(tool["name"], tool["policy"])} for tool in (previous["tools"] if same else [])]
    entry["discoveredAt"] = previous["discoveredAt"] if same else None
    if entry["enabled"] and entry["discoveredAt"] is None:
        raise IntegrationError("invalid-settings", f"Check the tools of {entry['name']} before turning it on.")
    return entry


def validate_settings(current, incoming):
    """The owner's new settings, the next revision of the current ones."""
    if not isinstance(incoming, dict) or set(incoming) != {"notifications", "notes", "mcp"} or not isinstance(incoming["mcp"], dict):
        raise IntegrationError("invalid-settings", "Settings are incomplete.")
    servers = incoming["mcp"].get("servers")
    if not isinstance(servers, list) or len(servers) > MAX_SERVERS:
        raise IntegrationError("invalid-settings", f"Use up to {MAX_SERVERS} MCP servers.")
    ids = [server.get("id") if isinstance(server, dict) else None for server in servers]
    if any(not isinstance(item, str) or not SERVER_ID.match(item) for item in ids) or len(set(ids)) != len(ids):
        raise IntegrationError("invalid-settings", "Each MCP server needs a unique id of lowercase letters, numbers and -.")
    known = {server["id"]: server for server in current["mcp"]["servers"]}
    return {"schemaVersion": SCHEMA_VERSION, "revision": current["revision"] + 1,
            "notifications": _notifications(incoming["notifications"]), "notes": _notes(incoming["notes"]),
            "mcp": {"servers": [_server(server, known.get(server["id"])) for server in servers]}}


def public_config(config, secrets):
    """Settings for the owner's screen: whether each secret is set, never its value."""
    value = json.loads(json.dumps(config))
    value["notifications"]["hasPassword"] = bool(secrets.get("ntfyPassword"))
    for server in value["mcp"]["servers"]:
        server["hasToken"] = bool(secrets.get("mcp", {}).get(server["id"]))
    return value


# ---------------------------------------------------------------- notifications (ntfy)

def send_notification(config, secrets, title, message, priority="default"):
    settings = config["notifications"]
    if not settings["enabled"]:
        raise IntegrationError("notifications-off", "Notifications are not set up. The owner can connect ntfy in Settings → Portal integrations.")
    if not isinstance(message, str) or not message.strip() or len(message) > 4000:
        raise IntegrationError("invalid-notification", "Give a message of up to 4,000 characters.")
    title = (title if isinstance(title, str) and title.strip() else "Portal").strip()[:200]
    request = urllib.request.Request(f"{settings['url']}/{settings['topic']}", data=message.strip().encode(), method="POST")
    # ntfy reads non-ASCII header values only when RFC 2047 encoded; keep titles plain.
    request.add_header("Title", title.encode("ascii", "replace").decode())
    request.add_header("Priority", priority if priority in ("min", "low", "default", "high", "urgent") else "default")
    request.add_header("Tags", "portal")
    password = secrets.get("ntfyPassword")
    if password and settings["username"]:
        request.add_header("Authorization", "Basic " + base64.b64encode(f"{settings['username']}:{password}".encode()).decode())
    elif password:
        request.add_header("Authorization", "Bearer " + password)
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT):
            pass
    except urllib.error.HTTPError as error:
        reason = "the user or password was refused" if error.code in (401, 403) else f"ntfy answered {error.code}"
        raise IntegrationError("notification-failed", f"The notification was not sent: {reason}.") from None
    except (urllib.error.URLError, OSError):
        raise IntegrationError("notification-failed", f"The notification was not sent: ntfy at {settings['url']} could not be reached.") from None
    return title


# ---------------------------------------------------------------- the service

class Integrations:
    def __init__(self, store):
        self.store = store

    def handle(self, role, request):
        """role is "owner" or "agent", set by the socket the request came in on."""
        if not isinstance(request, dict) or request.get("schemaVersion") != SCHEMA_VERSION:
            raise IntegrationError("invalid-request", "Unsupported request.")
        action = request.get("action")
        if role == "owner":
            if action not in OWNER_ACTIONS:
                raise IntegrationError("invalid-request", "Unknown owner action.")
            return self._owner(action, request)
        if action in OWNER_ACTIONS:
            raise IntegrationError("forbidden", "Only the owner can change integrations.")
        return self._agent(action, request)

    # Owner (host agent) actions.
    def _owner(self, action, request):
        if action == "save":
            self.save(request.get("expectedRevision"), request.get("settings"), request.get("secrets", {}))
        elif action == "test-notification":
            config = self.store.config()
            if not config["notifications"]["enabled"]:
                raise IntegrationError("notifications-off", "Turn notifications on and save before sending a test.")
            send_notification(config, self.store.secrets(), "Portal", "Notifications from Portal are working.")
            self.store.log("notify", "test", "sent")
        elif action == "discover":
            self.discover(request.get("serverId"))
        elif action == "decide":
            self.decide(request.get("approvalId"), request.get("decision"))
        return self.view()

    def view(self):
        with self.store.lock:
            config, secrets, approvals = self.store.config(), self.store.secrets(), self.store.approvals()
        waiting = [{key: item[key] for key in ("id", "kind", "summary", "createdAt", "expiresAt")}
                   for item in approvals if item["state"] == "pending"]
        return {"settings": public_config(config, secrets), "approvals": waiting, "activity": self.store.activity()}

    def save(self, expected_revision, settings, changes):
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
            if expected_revision != config["revision"]:
                raise IntegrationError("stale-revision", "These settings changed in another window. Reload them and try again.")
            updated = validate_settings(config, settings)
            if not isinstance(changes, dict) or not isinstance(changes.get("mcp", {}), dict):
                raise IntegrationError("invalid-settings", "Check the passwords and tokens.")
            if "ntfyPassword" in changes:
                secrets["ntfyPassword"] = _text(changes["ntfyPassword"], "the ntfy password", 256)
            ids = {server["id"] for server in updated["mcp"]["servers"]}
            for server_id, token in changes.get("mcp", {}).items():
                if server_id not in ids:
                    raise IntegrationError("invalid-settings", "A token names an unknown MCP server.")
                secrets.setdefault("mcp", {})[server_id] = _text(token, "the access token", 4096)
            secrets["mcp"] = {key: value for key, value in secrets.get("mcp", {}).items() if key in ids and value}
            self.store.save_secrets(secrets)
            self.store.save_config(updated)
            self.store.log("settings", "integrations", "saved")

    def discover(self, server_id):
        """Connect once and record the server's tools, keeping the owner's earlier choices."""
        server = self._server(self.store.config(), server_id, enabled=False)
        with McpSession(server, self.store.secrets().get("mcp", {}).get(server_id, "")) as session:
            tools = session.tools()
        with self.store.lock:
            config = self.store.config()
            server = self._server(config, server_id, enabled=False)
            previous = {tool["name"]: tool["policy"] for tool in server["tools"]}
            server["tools"] = [describe_tool(tool, previous.get(tool["name"])) for tool in tools]
            server["discoveredAt"] = now()
            config["revision"] += 1
            self.store.save_config(config)
            self.store.log("mcp-discover", server["name"], f"{len(tools)} tools")

    def decide(self, approval_id, decision):
        if decision not in ("approve", "decline"):
            raise IntegrationError("invalid-request", "Approve or decline.")
        with self.store.lock:
            approvals = self.store.approvals()
            match = next((item for item in approvals if item["id"] == approval_id and item["state"] == "pending"), None)
            if match is None:
                raise IntegrationError("approval-missing", "This request is no longer waiting for approval.")
            match["state"] = "approved" if decision == "approve" else "declined"
            match["decidedAt"] = now()
            self.store.save_approvals(approvals)
            self.store.log("approval", match["summary"], match["state"])

    # Gateway (Portal agent) actions.
    def _agent(self, action, request):
        config = self.store.config()
        if action == "notes-search":
            query = str(request.get("query", ""))[:300]
            hits = notes.search(config, query)
            self.store.log("notes-search", query[:80], f"{len(hits)} notes")
            return {"results": hits}
        if action == "notes-read":
            relative, text = notes.read(config, request.get("path"))
            self.store.log("notes-read", relative, "read")
            return {"path": relative, "text": text}
        if action == "notes-write":
            return self._notes_write(config, request)
        if action == "notify":
            title = send_notification(config, self.store.secrets(), request.get("title"), request.get("message"),
                                      request.get("priority", "default"))
            self.store.log("notify", title, "sent")
            return {"sent": True}
        if action == "mcp-tools":
            return {"servers": self._agent_servers(config)}
        if action == "mcp-call":
            return self._mcp_call(config, request)
        raise IntegrationError("invalid-request", "Unknown integration action.")

    def _notes_write(self, config, request):
        mode, relative = request.get("mode", "append"), str(request.get("path", ""))
        policy = config["notes"]["write"]
        if policy == "off":
            raise IntegrationError("notes-write-off", "The owner has not allowed Portal to change notes.")
        _path, relative = notes.note_path(notes.vault(config), relative, must_exist=False)
        if policy == "ask":
            summary = f"{'Add to' if mode == 'append' else 'Create'} note {relative}"
            waiting = self._approval("note", summary, digest("note", relative, mode, request.get("text")))
            if waiting:
                return waiting
        written = notes.write(config, relative, request.get("text"), mode)
        self.store.log("notes-write", written, mode)
        return {"written": written, "mode": mode}

    @staticmethod
    def _agent_servers(config):
        return [{"id": server["id"], "name": server["name"],
                 "tools": [{"name": tool["name"], "description": tool["description"], "inputSchema": tool["inputSchema"],
                            "needsApproval": tool["policy"] == "ask"} for tool in server["tools"] if tool["policy"] != "off"]}
                for server in config["mcp"]["servers"] if server["enabled"]]

    def _mcp_call(self, config, request):
        server = self._server(config, request.get("server"), enabled=True)
        name, arguments = request.get("tool"), request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise IntegrationError("invalid-request", "Tool arguments must be an object.")
        tool = next((item for item in server["tools"] if item["name"] == name), None)
        if tool is None or tool["policy"] == "off":
            raise IntegrationError("tool-not-allowed", f"{server['name']} has no allowed tool named {name}. List its tools first.")
        if tool["policy"] == "ask":
            summary = f"{server['name']}: {name} {json.dumps(arguments, sort_keys=True)[:300]}"
            waiting = self._approval("mcp", summary, digest("mcp", server["id"], name, arguments))
            if waiting:
                return waiting
        with McpSession(server, self.store.secrets().get("mcp", {}).get(server["id"], "")) as session:
            text, failed = session.call(name, arguments)
        self.store.log("mcp-call", f"{server['name']}: {name}", "error" if failed else "done")
        return {"server": server["id"], "tool": name, "text": text, "isError": failed}

    def _approval(self, kind, summary, request_digest):
        """None when this exact request was approved (the approval is used up), else the waiting reply."""
        with self.store.lock:
            approvals = self.store.approvals()
            approved = next((item for item in approvals if item["digest"] == request_digest and item["state"] == "approved"), None)
            if approved:
                approved["state"], approved["usedAt"] = "used", now()
                self.store.save_approvals(approvals)
                return None
            pending = next((item for item in approvals if item["digest"] == request_digest and item["state"] == "pending"), None)
            created = pending is None
            if created:
                pending = {"id": uuid.uuid4().hex[:12], "kind": kind, "summary": summary[:400], "digest": request_digest,
                           "state": "pending", "createdAt": now(), "expiresAt": now() + APPROVAL_SECONDS}
                self.store.save_approvals(approvals + [pending])
                self.store.log("approval", summary, "requested")
        if created:
            self._notify_approval(summary)
        return {"status": "approval-required", "approvalId": pending["id"], "summary": pending["summary"]}

    def _notify_approval(self, summary):
        config = self.store.config()
        if not (config["notifications"]["enabled"] and config["notifications"]["notifyApprovals"]):
            return
        try:
            send_notification(config, self.store.secrets(), "Portal needs your approval", summary, "high")
        except IntegrationError as error:
            # The approval is still waiting in Settings; only the ping failed.
            self.store.log("notify", "approval", error.message)

    @staticmethod
    def _server(config, server_id, *, enabled):
        server = next((item for item in config["mcp"]["servers"] if item["id"] == server_id), None)
        if server is None or (enabled and not server["enabled"]):
            raise IntegrationError("server-missing", "That MCP server is not turned on.")
        return server


# ---------------------------------------------------------------- socket

def handle_line(service, role, line):
    try:
        request = json.loads(line)
    except ValueError:
        return {"ok": False, "error": "invalid-request", "message": "The request is not JSON."}
    try:
        return {"ok": True, **service.handle(role, request)}
    except IntegrationError as error:
        return {"ok": False, "error": error.code, "message": error.message}


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def _listener(service, socket_path, role):
    """A socket for one role that answers only processes of this service's own user."""
    from unix_peer import peer_ids
    socket_path = pathlib.Path(socket_path)
    if socket_path.exists() or socket_path.is_symlink():
        if not stat.S_ISSOCK(socket_path.lstat().st_mode):
            raise SystemExit("unsafe existing integrations socket")
        socket_path.unlink()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            uid, _gid = peer_ids(self.request)
            self.request.settimeout(130)
            line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if uid != os.getuid():
                reply = {"ok": False, "error": "forbidden", "message": "This caller may not use integrations."}
            elif len(line) > MAX_REQUEST_BYTES:
                reply = {"ok": False, "error": "too-large", "message": "The request is too large."}
            else:
                reply = handle_line(service, role, line)
            self.wfile.write((json.dumps(reply, separators=(",", ":")) + "\n").encode())

    server = _Server(str(socket_path), Handler)
    os.chmod(socket_path, 0o600)
    return server


def start(agent_socket, state_dir):
    """Both listeners, serving in background threads; the owner socket is STATE_DIR/owner.sock."""
    service = Integrations(Store(state_dir))
    servers = [_listener(service, agent_socket, "agent"), _listener(service, pathlib.Path(state_dir) / OWNER_SOCKET, "owner")]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    return servers


def serve(agent_socket, state_dir):
    start(agent_socket, state_dir)
    threading.Event().wait()


def client(socket_path, request, timeout=130):
    """One request to the service, as the host agent sends it."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(str(socket_path))
        connection.sendall((json.dumps({"schemaVersion": SCHEMA_VERSION, **request}) + "\n").encode())
        data = bytearray()
        while not data.endswith(b"\n") and len(data) <= MAX_RESPONSE_BYTES:
            piece = connection.recv(65536)
            if not piece:
                break
            data.extend(piece)
        return json.loads(data)
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "serve":
        raise SystemExit("usage: integrations_service.py serve AGENT_SOCKET STATE_DIR")
    serve(sys.argv[2], sys.argv[3])
