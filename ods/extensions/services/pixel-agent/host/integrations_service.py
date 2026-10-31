"""Owner integrations for Portal: an Obsidian vault, ntfy notifications, MCP servers and apps.

One local service holds the owner's integration settings and secrets and performs every
integration action, so the sandboxed gateway never holds a token or a vault path. Requests
are one JSON line over a Unix socket. The gateway runs as the owner's user, so the socket,
not the caller's uid, sets what a request may do: the agent socket under /run is the only
one the gateway sandbox can see, and the owner socket sits in the state folder, which its
ProtectHome=tmpfs hides. The owner (through the host agent) changes settings and decides
approvals. The gateway may only use what the owner turned on: search and read notes, write
notes as the owner allowed, send notifications, call the MCP tools the owner allowed, and use
the owner's apps (documents, photos, watched pages, news). A tool, note change or watch change
set to "ask first" waits for the owner's approval, bound to the exact call and usable once.
The service also tells the owner when a scheduled task finishes and when a watched page changes.
"""
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
import traceback
import urllib.parse
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import integrations_apps as apps  # noqa: E402
import integrations_notes as notes  # noqa: E402
from integrations_common import (IntegrationError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES,  # noqa: E402
                                 SCHEMA_VERSION, Store, digest, now)
from integrations_app_service import APP_TOOLS, AppActions, app_text  # noqa: E402
from integrations_ntfy import send_notification  # noqa: E402
from integrations_mcp import LocalServers, McpSession, describe_tool  # noqa: E402

MAX_SERVERS = 20
MAX_ENV = 20
APPROVAL_SECONDS = 24 * 3600
POLICIES = ("off", "ask", "allow")
SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
OWNER_ACTIONS = {"get", "save", "test-notification", "discover", "test", "test-app", "watch-list", "send-digest", "restart",
                 "decide", "clear-activity"}
TASK_MODES = ("off", "failures", "all")
WATCH_SECONDS = 120
OWNER_SOCKET = "owner.sock"
APPROVAL_HELP = ("Approve or decline it in Settings → Portal integrations. An approval is for this exact action, "
                 "once, within 24 hours; a scheduled task goes ahead on its next run.")


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
    if (not isinstance(value, dict) or set(value) != {"enabled", "url", "topic", "username", "notifyApprovals", "approvalLink",
                                                       "scheduledTasks"} or value["scheduledTasks"] not in TASK_MODES):
        raise IntegrationError("invalid-settings", "Check the notification settings.")
    topic = _text(value["topic"], "the topic", 64, empty=False)
    if not TOPIC.match(topic):
        raise IntegrationError("invalid-settings", "The topic may use letters, numbers, - and _.")
    link = _text(value["approvalLink"], "the approvals link", 500)
    return {"enabled": value["enabled"] is True, "url": _http_url(value["url"], "The ntfy address"), "topic": topic,
            "username": _text(value["username"], "the ntfy user", 64), "notifyApprovals": value["notifyApprovals"] is True,
            "approvalLink": _http_url(link, "The approvals link") if link else "", "scheduledTasks": value["scheduledTasks"]}


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
             "url": "", "command": "", "workingFolder": "", "envNames": []}
    if entry["kind"] == "http":
        entry["url"] = _http_url(value.get("url", ""), "The MCP server address")
    else:
        entry["command"] = _text(value.get("command", ""), "the command", 1000, empty=False)
        try:
            if not shlex.split(entry["command"]):
                raise ValueError
        except ValueError:
            raise IntegrationError("invalid-settings", "The command is not valid.") from None
        entry["workingFolder"] = _text(value.get("workingFolder", ""), "the working folder", 1024)
        if entry["workingFolder"] and not (pathlib.Path(entry["workingFolder"]).is_absolute() and pathlib.Path(entry["workingFolder"]).is_dir()):
            raise IntegrationError("invalid-settings", f"The working folder of {entry['name']} must be an existing folder, given as a full path.")
        names = value.get("envNames", [])
        if (not isinstance(names, list) or len(names) > MAX_ENV or len(set(names)) != len(names)
                or any(not isinstance(name, str) or not ENV_NAME.match(name) or name == "PATH" for name in names)):
            raise IntegrationError("invalid-settings", "Environment variable names use letters, numbers and _, such as GITHUB_TOKEN.")
        entry["envNames"] = names
    same = previous is not None and all(previous[key] == entry[key] for key in ("kind", "url", "command"))
    policies = value.get("policies", {})
    if not isinstance(policies, dict) or any(policy not in POLICIES for policy in policies.values()):
        raise IntegrationError("invalid-settings", "Each tool is off, ask first or allowed.")
    # Saving means the owner has seen the tools, so none is new any more.
    entry["tools"] = [{**tool, "policy": policies.get(tool["name"], tool["policy"]), "new": False}
                      for tool in (previous["tools"] if same else [])]
    entry["discoveredAt"] = previous["discoveredAt"] if same else None
    if entry["enabled"] and entry["discoveredAt"] is None:
        raise IntegrationError("invalid-settings", f"Check the tools of {entry['name']} before turning it on.")
    return entry


def validate_settings(current, incoming):
    """The owner's new settings, the next revision of the current ones."""
    if not isinstance(incoming, dict) or set(incoming) != {"notifications", "notes", "mcp", "apps"} or not isinstance(incoming["mcp"], dict):
        raise IntegrationError("invalid-settings", "Settings are incomplete.")
    servers = incoming["mcp"].get("servers")
    if not isinstance(servers, list) or len(servers) > MAX_SERVERS:
        raise IntegrationError("invalid-settings", f"Use up to {MAX_SERVERS} MCP servers.")
    ids = [server.get("id") if isinstance(server, dict) else None for server in servers]
    if any(not isinstance(item, str) or not SERVER_ID.match(item) for item in ids) or len(set(ids)) != len(ids):
        raise IntegrationError("invalid-settings", "Each MCP server needs a unique id of lowercase letters, numbers and -.")
    if set(ids) & set(APP_TOOLS):
        raise IntegrationError("invalid-settings", "The ids documents, photos, watch and news belong to the apps; give the MCP server another name.")
    known = {server["id"]: server for server in current["mcp"]["servers"]}
    return {"schemaVersion": SCHEMA_VERSION, "revision": current["revision"] + 1,
            "notifications": _notifications(incoming["notifications"]), "notes": _notes(incoming["notes"]),
            "mcp": {"servers": [_server(server, known.get(server["id"])) for server in servers]},
            "apps": apps.validate_apps(incoming["apps"], _text, _http_url)}


def public_config(config, secrets):
    """Settings for the owner's screen: whether each secret is set, never its value."""
    value = json.loads(json.dumps(config))
    value["notifications"]["hasPassword"] = bool(secrets.get("ntfyPassword"))
    for server in value["mcp"]["servers"]:
        server["hasToken"] = bool(secrets["mcp"].get(server["id"]))
        saved = secrets["mcpEnv"].get(server["id"], {})
        server["hasEnv"] = {name: bool(saved.get(name)) for name in server["envNames"]}
    for app, settings in value["apps"].items():
        settings["hasKey"] = bool(secrets["apps"].get(app))
    return value


# ---------------------------------------------------------------- the service

class Integrations(AppActions):
    def __init__(self, store, local_servers=None):
        self.store = store
        self.local = local_servers or LocalServers()
        self.digest_called_at = 0  # When Portal last asked for the news digest, to tell its runs apart.
        self.failing_tasks = set()  # Scheduled tasks whose last run failed, so a retry does not notify again.

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
        elif action == "test":
            return {**self.view(), "notice": self.test(request.get("serverId"))}
        elif action == "test-app":
            return {**self.view(), "notice": self.test_app(request.get("app"))}
        elif action == "watch-list":
            return {**self.view(), "watches": self.watch_overview()}
        elif action == "send-digest":
            return {**self.view(), "notice": self.send_digest_now()}
        elif action == "restart":
            server = self._server(self.store.config(), request.get("serverId"), enabled=False)
            self.local.stop(server["id"], "the owner restarted it")
            self.store.log("mcp-restart", server["name"], "stopped; starts on next use")
        elif action == "decide":
            self.decide(request.get("approvalId"), request.get("decision"))
        elif action == "clear-activity":
            with self.store.lock:
                self.store.clear_activity()
        return self.view()

    def view(self):
        with self.store.lock:
            config, secrets, approvals = self.store.config(), self.store.secrets(), self.store.approvals()
        waiting = [{key: item[key] for key in ("id", "kind", "summary", "createdAt", "expiresAt")}
                   for item in approvals if item["state"] == "pending"]
        processes = {server["id"]: self.local.status(server["id"])
                     for server in config["mcp"]["servers"] if server["kind"] == "stdio"}
        return {"settings": public_config(config, secrets), "approvals": waiting, "activity": self.store.activity(),
                "processes": processes}

    def save(self, expected_revision, settings, changes):
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
            if expected_revision != config["revision"]:
                raise IntegrationError("stale-revision", "These settings changed in another window. Reload them and try again.")
            updated = validate_settings(config, settings)
            if (not isinstance(changes, dict) or set(changes) - {"ntfyPassword", "mcp", "mcpEnv", "apps"}
                    or not isinstance(changes.get("mcp", {}), dict)):
                raise IntegrationError("invalid-settings", "Check the passwords and tokens.")
            if "ntfyPassword" in changes:
                secrets["ntfyPassword"] = _text(changes["ntfyPassword"], "the ntfy password", 256)
            servers = {server["id"]: server for server in updated["mcp"]["servers"]}
            for server_id, token in changes.get("mcp", {}).items():
                if server_id not in servers:
                    raise IntegrationError("invalid-settings", "A token names an unknown MCP server.")
                secrets["mcp"][server_id] = _text(token, "the access token", 4096)
            env_changes = changes.get("mcpEnv", {})
            if not isinstance(env_changes, dict):
                raise IntegrationError("invalid-settings", "Check the environment variables.")
            for server_id, values in env_changes.items():
                if server_id not in servers or not isinstance(values, dict) or set(values) - set(servers[server_id]["envNames"]):
                    raise IntegrationError("invalid-settings", "A value names an unknown environment variable.")
                for name, value in values.items():
                    secrets["mcpEnv"].setdefault(server_id, {})[name] = _text(value, f"the value of {name}", 4096)
            secrets["mcp"] = {key: value for key, value in secrets["mcp"].items() if key in servers and value}
            kept = {key: {name: value for name, value in values.items() if name in servers[key]["envNames"] and value}
                    for key, values in secrets["mcpEnv"].items() if key in servers}
            secrets["mcpEnv"] = {key: values for key, values in kept.items() if values}
            app_changes = changes.get("apps", {})
            if not isinstance(app_changes, dict) or set(app_changes) - set(apps.APPS):
                raise IntegrationError("invalid-settings", "Check the app tokens.")
            for app, value in app_changes.items():
                secrets["apps"][app] = _text(value, f"the {apps.NAMES[app]} token", 4096)
            secrets["apps"] = {app: value for app, value in secrets["apps"].items() if value}
            # Turned on without a key, Portal would list the app and every call would be refused.
            missing = [apps.NAMES[app] for app in apps.APPS if updated["apps"][app]["enabled"] and not secrets["apps"].get(app)]
            if missing:
                raise IntegrationError("invalid-settings", f"Give the {missing[0]} key, or turn it off.")
            self.store.save_secrets(secrets)
            self.store.save_config(updated)
            self.store.log("settings", "integrations", "saved")
        # A server that was removed or turned off stops now; a changed one restarts on its next use.
        self.local.keep_only({server["id"] for server in updated["mcp"]["servers"] if server["enabled"]})

    def _session(self, server):
        secrets = self.store.secrets()
        return McpSession(server, secrets["mcp"].get(server["id"], ""), secrets["mcpEnv"].get(server["id"], {}), self.local)

    def _owner_tools(self, server):
        """The server's tools; a local program's failure also shows the owner its last error output."""
        try:
            with self._session(server) as session:
                return session.tools()
        except IntegrationError as error:
            output = self.local.detail(server["id"]) if server["kind"] == "stdio" else ""
            if output:
                raise IntegrationError(error.code, f"{error.message} Its last output:\n{output}") from None
            raise

    def discover(self, server_id):
        """Connect and record the server's tools, keeping the owner's earlier choices.

        A tool that was not there at the last check starts off and is marked new.
        """
        tools = self._owner_tools(self._server(self.store.config(), server_id, enabled=False))
        with self.store.lock:
            config = self.store.config()
            server = self._server(config, server_id, enabled=False)
            previous = {tool["name"]: tool["policy"] for tool in server["tools"]}
            checked_before = server["discoveredAt"] is not None
            server["tools"] = [describe_tool(tool, previous.get(tool["name"]), new=checked_before and tool["name"] not in previous)
                               for tool in tools]
            server["discoveredAt"] = now()
            config["revision"] += 1
            self.store.save_config(config)
            added = sum(tool["new"] for tool in server["tools"])
            self.store.log("mcp-discover", server["name"], f"{len(tools)} tools" + (f", {added} new" if added else ""))

    def test(self, server_id):
        """Connect and list the tools without changing anything; a sentence for the owner."""
        server = self._server(self.store.config(), server_id, enabled=False)
        names = {tool["name"] for tool in self._owner_tools(server)}
        known = {tool["name"] for tool in server["tools"]}
        added, gone = len(names - known), len(known - names)
        self.store.log("mcp-test", server["name"], f"{len(names)} tools")
        changes = []
        if added and server["discoveredAt"]:
            changes.append(f"{added} new")
        if gone:
            changes.append(f"{gone} gone")
        return (f"{server['name']} answered with {len(names)} tools."
                + (f" Since the last check: {', '.join(changes)}. Check tools to review them." if changes else ""))

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
            return {"servers": self.app_servers(config) + self._agent_servers(config)}
        if action == "mcp-call":
            return self._mcp_call(config, request)
        if action == "task-finished":
            return self.task_finished(config, request)
        if action == "digest-plan":
            return {"plan": apps.digest_plan(config)}
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
        with self.store.lock:  # Two writes to one note never interleave.
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
        name, arguments = request.get("tool"), request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise IntegrationError("invalid-request", "Tool arguments must be an object.")
        if request.get("server") in APP_TOOLS:
            action, reply = self.use_app(config, request["server"], name, arguments)
            if reply.get("status") == "approval-required":
                return reply
            return {"server": request["server"], "tool": name, "text": app_text(action, reply), "isError": False}
        server = self._server(config, request.get("server"), enabled=True)
        tool = next((item for item in server["tools"] if item["name"] == name), None)
        if tool is None or tool["policy"] == "off":
            raise IntegrationError("tool-not-allowed", f"{server['name']} has no allowed tool named {name}. List its tools first.")
        if tool["policy"] == "ask":
            summary = f"{server['name']}: {name} {json.dumps(arguments, sort_keys=True)[:300]}"
            waiting = self._approval("mcp", summary, digest("mcp", server["id"], name, arguments))
            if waiting:
                return waiting
        with self._session(server) as session:
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
        settings = config["notifications"]
        if not (settings["enabled"] and settings["notifyApprovals"]):
            return
        try:
            send_notification(config, self.store.secrets(), "Portal needs your approval", f"{summary}\n\n{APPROVAL_HELP}",
                              "high", settings["approvalLink"])
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


def watch_pages(service, stop, seconds=WATCH_SECONDS):
    """Look at the owner's watched pages every few minutes until stop is set."""
    while not stop.wait(seconds):
        try:
            service.check_watches()
        except IntegrationError as error:
            # changedetection.io may be stopped or restarting; the next look tries again.
            service.store.log("watch-check", "watched pages", error.message)


def watch_or_exit(service, stop, seconds=WATCH_SECONDS, exit=os._exit):
    """Watch pages; any other error ends the whole service, which systemd restarts with watching.

    Left to itself, a crash would end only this thread, and pages would go unwatched in silence.
    """
    try:
        watch_pages(service, stop, seconds)
    except BaseException:
        traceback.print_exc()
        exit(1)


def start(agent_socket, state_dir):
    """Both listeners and the page watcher, in background threads; the owner socket is STATE_DIR/owner.sock."""
    service = Integrations(Store(state_dir))
    servers = [_listener(service, agent_socket, "agent"), _listener(service, pathlib.Path(state_dir) / OWNER_SOCKET, "owner")]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=watch_or_exit, args=(service, threading.Event()), daemon=True).start()
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
