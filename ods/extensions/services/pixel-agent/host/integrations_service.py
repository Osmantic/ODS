"""Owner integrations for Portal: an Obsidian vault, ntfy notifications, MCP servers and apps.

One local service holds integration settings and secrets. Requests are one JSON line over
separate owner and agent Unix endpoints; the listener determines the role, not request
fields. The owner endpoint changes settings and decides approvals. The agent endpoint
enforces enabled resources and exact, single-use approvals for "ask first" actions.

Native macOS additionally authenticates each owner request with a fresh challenge
and a root-provisioned owner-readable key excluded from gateway filesystem policies.
Physical Seatbelt/alias qualification is separate from this protocol and peer check.

These endpoints share the owner's Unix identity. Their permissions exclude other users,
but do not isolate arbitrary owner-account code. In particular Full Access can read owner
files and reach the owner endpoint outside this tool path. Mount hiding alone is not an
absolute same-UID security boundary. Off/ask/allow are policies of the integration tools,
not a claim that broader owner-account access cannot bypass them.
"""
import json
import hmac
import os
import pathlib
import re
import shlex
import signal
import socket
import socketserver
import stat
import sys
import threading
import tempfile
import time
import traceback
import urllib.parse
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import integrations_apps as apps  # noqa: E402
import integrations_notes as notes  # noqa: E402
from integrations_common import (IntegrationError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, note_approval_digest,  # noqa: E402
                                 SCHEMA_VERSION, Store, digest, now, redact_secrets, clip, app_context, notification_context,
                                 native_owner_key, native_owner_request, owner_proof, owner_auth_binding, owner_reply_proof, read_owner_frame, OWNER_AUTH_SECONDS,
                                 stop_http_workers, validate_native_state)
from integrations_app_service import APP_SERVER_IDS, AppActions, app_text  # noqa: E402
from integrations_recovery import REFERENCE_FIELD, RecoveryReviewOnly, review_only_request  # noqa: E402
from integrations_ntfy import send_notification  # noqa: E402
from integrations_mcp import LocalServer, LocalServers, McpSession, SESSION_TIMEOUT, describe_tool  # noqa: E402

MAX_SERVERS = 20
MAX_ENV = 20
APPROVAL_SECONDS = 24 * 3600
POLICIES = ("off", "ask", "allow")
SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
TOPIC = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
OWNER_ACTIONS = {"get", "save", "test-notification", "discover", "test", "test-app", "watch-list", "send-digest", "retry-digest", "restart",
                 "decide", "approval-detail", "clear-activity"}
TASK_MODES = ("off", "failures", "all")
WATCH_SECONDS = 120
REQUEST_SECONDS = 130
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
    try:
        parsed = urllib.parse.urlsplit(value)
        valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username and not parsed.password
        parsed.port
    except ValueError:
        valid = False
    if not valid:
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
    if value["enabled"] is True and not (pathlib.Path(vault).is_absolute() and pathlib.Path(vault).is_dir()
                                         and not pathlib.Path(vault).is_symlink()):
        raise IntegrationError("invalid-settings", "The vault folder must be an existing folder, given as a full path.")
    return {"enabled": value["enabled"] is True,
            "vaultPath": str(pathlib.Path(vault).resolve()) if value['enabled'] is True else vault,
            "write": value["write"]}


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
        entry["envNames"] = sorted(names)
    same = previous is not None and all(previous.get(key) == entry[key]
                                        for key in ("kind", "url", "command", "workingFolder"))
    same = same and sorted(previous.get('envNames', [])) == entry['envNames']
    policies = value.get("policies", {})
    if not isinstance(policies, dict) or any(policy not in POLICIES for policy in policies.values()):
        raise IntegrationError("invalid-settings", "Each tool is off, ask first or allowed.")
    # Saving means the owner has seen the tools, so none is new any more.
    entry["tools"] = [{**tool, "policy": policies.get(tool["name"], tool["policy"]), "new": False}
                      for tool in (previous["tools"] if same else [])]
    entry["discoveredAt"] = previous["discoveredAt"] if same else None
    entry["checkedBinding"] = previous.get("checkedBinding") if same else None
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
    known = {server["id"]: server for server in current["mcp"]["servers"]}
    return {"schemaVersion": SCHEMA_VERSION, "revision": current["revision"] + 1,
            "notifications": _notifications(incoming["notifications"]), "notes": _notes(incoming["notes"]),
            "mcp": {"servers": [_server(server, known.get(server["id"])) for server in servers]},
            "apps": apps.validate_apps(incoming["apps"], _text, _http_url)}


def public_config(config, secrets):
    """Settings for the owner's screen: whether each secret is set, never its value."""
    value = json.loads(json.dumps(config))
    value.pop('approvalBindings', None)
    value["notifications"]["hasPassword"] = bool(secrets.get("ntfyPassword"))
    for server in value["mcp"]["servers"]:
        server.pop('checkedBinding', None)
        server["hasToken"] = bool(secrets["mcp"].get(server["id"]))
        saved = secrets["mcpEnv"].get(server["id"], {})
        server["hasEnv"] = {name: bool(saved.get(name)) for name in server["envNames"]}
    for app, settings in value["apps"].items():
        settings["hasKey"] = bool(secrets["apps"].get(app))
    return value


def server_context(server, secrets):
    """Private identity, never a public credential projection."""
    return {**{key: server.get(key) for key in ('id', 'kind', 'url', 'command', 'workingFolder')},
            'envNames': sorted(server.get('envNames', [])),
            'token': secrets['mcp'].get(server['id'], ''),
            'env': {name: secrets['mcpEnv'].get(server['id'], {}).get(name, '')
                    for name in server.get('envNames', [])}}


# ---------------------------------------------------------------- the service

class _AdmittedLocalServers:
    """A session's immutable admission, checked again before publishing a local process."""

    def __init__(self, service, revision):
        self.service, self.revision = service, revision

    def get(self, server, values, *, deadline=None):
        return self.service._local_get(server, values, self.revision, deadline=deadline)



class Integrations(RecoveryReviewOnly, AppActions):
    _recovery_server_context = staticmethod(server_context)

    def __init__(self, store, local_servers=None):
        self.store = store
        self.local = local_servers or LocalServers()
        self._runtime_revision = 0
        self._watch_lock = threading.RLock()
        self.digest_lock = threading.RLock()
        self.task_notice_lock = threading.RLock()
        self.digest_snapshots = {}
        self.active_digest_attempts = set()
        self.digest_called_at = 0
        self.digest_items = []
        self.digest_sent_at = 0
        self.digest_failing = False
        self.failing_tasks = set()
        self.discovery_path = None

    def publish_discovery(self):
        """Cached configuration hints only; no provider/credential projection or authority."""
        path, temporary = self.discovery_path, None
        if path is None:
            return False
        try:
            for parent in (path.parent, *path.parent.parents):
                info = parent.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.geteuid()) or info.st_mode & 0o022):
                    return False
            if os.path.lexists(path):
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o077):
                    return False
            with self.store.lock:
                config, secrets = self.store.config(), self.store.secrets()
                value = {"version": 1, "revision": config["revision"], "validUntil": int(now()) + 300,
                         "servers": [], "truncated": False}
                for server in sorted(config["mcp"]["servers"], key=lambda row: row["id"]):
                    if (not server["enabled"] or not self._mcp_context_current(config, secrets, server)
                            or server.get("checkedBinding") != digest(server_context(server, secrets))):
                        continue
                    identity = redact_secrets(server["id"], secrets)
                    if not SERVER_ID.fullmatch(identity):
                        value["truncated"] = True
                        continue
                    label = redact_secrets(server["name"], secrets)[:96]
                    if re.search(r"://|[/\\]|[\x00-\x1f\x7f]", label):
                        label = identity
                    entry = {"id": identity, "name": label, "tools": []}
                    for tool in sorted(server["tools"], key=lambda row: row["name"]):
                        if tool.get("policy") not in ("allow", "ask"):
                            continue
                        name = redact_secrets(tool["name"], secrets)
                        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", name):
                            value["truncated"] = True
                            continue
                        entry["tools"].append({"name": name, "ask": tool["policy"] == "ask"})
                        candidate = {**value, "servers": value["servers"] + [entry]}
                        if len(json.dumps(candidate, ensure_ascii=True).encode()) > 8000:
                            entry["tools"].pop()
                            value["truncated"] = True
                            break
                    if entry["tools"]:
                        value["servers"].append(entry)
                raw = (json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
                if len(raw) > 8192:
                    return False
                fd, temporary = tempfile.mkstemp(prefix=".discovery-", dir=path.parent)
                with os.fdopen(fd, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, path)
                temporary = None
            return True
        except (OSError, ValueError, TypeError, KeyError):
            return False  # A hint failure cannot turn a committed Owner save into a failed transaction.
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def handle(self, role, request):
        """role is "owner" or "agent", set by the socket the request came in on."""
        if not isinstance(request, dict) or request.get("schemaVersion") != SCHEMA_VERSION:
            raise IntegrationError("invalid-request", "Unsupported request.")
        action = request.get("action")
        try:
            if role == "owner":
                if action not in OWNER_ACTIONS:
                    raise IntegrationError("invalid-request", "Unknown owner action.")
                result = self._owner(action, request)
                if action in ("save", "discover"):
                    self.publish_discovery()
                return result
            if action in OWNER_ACTIONS:
                raise IntegrationError("forbidden", "Only the owner can change integrations.")
            return self._agent(action, request)
        except IntegrationError as error:
            # A child/server may echo a configured credential in its diagnosis.
            raise IntegrationError(error.code, redact_secrets(error.message, self.store.secrets())) from None

    # Owner (host agent) actions.
    def _owner(self, action, request):
        if action == "save":
            self.save(request.get("expectedRevision"), request.get("settings"), request.get("secrets", {}))
        elif action == "test-notification":
            with self.store.lock:
                config, secrets = self.store.config(), self.store.secrets()
            if not config["notifications"]["enabled"]:
                raise IntegrationError("notifications-off", "Turn notifications on and save before sending a test.")
            send_notification(config, secrets, "Portal", "Notifications from Portal are working.")
            self._log("notify", "test", "sent")
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
        elif action == "retry-digest":
            notice = self.retry_digest(request)
            return {**self.view(), "notice": notice}
        elif action == "restart":
            with self.store.lock:
                server = self._server(self.store.config(), request.get("serverId"), enabled=False)
                self._runtime_revision += 1
                with self.local.lock:
                    current = self.local.running.pop(server["id"], None)
            if current:
                current.stop("the owner restarted it")
            self._log("mcp-restart", server["name"], "stopped; starts on next use")
        elif action == "decide":
            self.decide(request.get("approvalId"), request.get("decision"))
        elif action == "approval-detail":
            return self.approval_detail(request.get("approvalId"))
        elif action == "clear-activity":
            with self.store.lock:
                self.store.clear_activity()
        return self.view()

    def view(self):
        with self.store.lock:
            config, secrets, approvals = self.store.config(), self.store.secrets(), self.store.approvals()
            activity = self.store.activity()
        waiting = [{key: item[key] for key in ("id", "kind", "summary", "createdAt", "expiresAt")}
                   for item in approvals if item["state"] == "pending"]
        processes = {server["id"]: self.local.status(server["id"])
                     for server in config["mcp"]["servers"] if server["kind"] == "stdio"}
        # Public settings omit secret fields by construction. Do not rewrite
        # routing IDs/paths if a short credential happens to equal that value.
        for item in waiting:
            item['summary'] = redact_secrets(item['summary'], secrets)
        for item in activity:
            for field in ('target', 'outcome'):
                item[field] = redact_secrets(item[field], secrets)
        return {"settings": public_config(config, secrets), "approvals": waiting,
                "activity": activity, "processes": redact_secrets(processes, secrets),
                "digestRecovery": self.digest_recovery()}

    def _log(self, action, target, outcome):
        with self.store.lock:
            self.store.log(action, target, outcome)


    def _revision(self):
        """Call under store.lock; include credentials as well as the public revision."""
        return digest(self.store.config(), self.store.secrets(), self._runtime_revision)


    def save(self, expected_revision, settings, changes):
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
            original_secrets = json.loads(json.dumps(secrets))
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
            bindings = config.get('approvalBindings', {})
            updated['approvalBindings'] = {'notes': bindings.get('notes'), 'mcp': {}, 'apps': dict(bindings.get('apps', {}))}
            old_watch_key = original_secrets['apps'].get('watch', '')
            if updated['notes'] != config['notes'] or not updated['approvalBindings']['notes']:
                updated['approvalBindings']['notes'] = uuid.uuid4().hex
            previous = {server['id']: server for server in config['mcp']['servers']}
            for server in updated['mcp']['servers']:
                old = previous.get(server['id'])
                changed = old is None or server_context(old, original_secrets) != server_context(server, secrets)
                if changed:
                    if server['enabled']:
                        raise IntegrationError('mcp-check-required', f"Turn {server['name']} off and check its tools after changing its context or credentials.")
                    server['tools'], server['discoveredAt'], server['checkedBinding'] = [], None, None
                old_binding = bindings.get('mcp', {}).get(server['id'])
                changed = changed or old.get('enabled') != server['enabled'] or old.get('tools') != server['tools']
                updated['approvalBindings']['mcp'][server['id']] = uuid.uuid4().hex if changed or not old_binding else old_binding
            app_changes = changes.get("apps", {})
            if not isinstance(app_changes, dict) or set(app_changes) - set(apps.APPS):
                raise IntegrationError("invalid-settings", "Check the app tokens.")
            for app, value in app_changes.items():
                secrets["apps"][app] = _text(value, f"the {apps.NAMES[app]} token", 4096)
            secrets["apps"] = {app: value for app, value in secrets["apps"].items() if value}
            missing = [apps.NAMES[app] for app in apps.APPS if updated["apps"][app]["enabled"] and not secrets["apps"].get(app)]
            if missing:
                raise IntegrationError("invalid-settings", f"Give the {missing[0]} key, or turn it off.")
            if (updated['apps']['watch']['url'] != config['apps']['watch']['url']
                    or secrets['apps'].get('watch', '') != old_watch_key
                    or not updated['approvalBindings']['apps'].get('watch')):
                updated['approvalBindings']['apps']['watch'] = uuid.uuid4().hex
            updated['approvalBindings']['appContexts'] = {
                app: app_context(updated, app, secrets['apps'].get(app, '')) for app in apps.APPS}
            notification_binding = notification_context(updated, secrets)
            previous_ntfy = config.get("approvalBindings", {}).get("notificationContext")
            staged_ntfy = secrets.get("notificationContext")
            if (previous_ntfy != staged_ntfy and notification_binding != staged_ntfy
                    and secrets.get("ntfyPassword") and "ntfyPassword" not in changes):
                raise IntegrationError("notification-check-required", "Restore the intended notification destination or re-enter its password before saving.")
            updated['approvalBindings']['notificationContext'] = notification_binding
            secrets['notificationContext'] = notification_binding
            # Revoke before state writes; interrupted saves fail closed.
            # These markers bind the credentials file to the intended provider.
            # The two file renames can be separated by a crash or a disk error.
            mcp_contexts = {server["id"]: digest(server_context(server, secrets))
                            for server in updated["mcp"]["servers"]}
            previous_contexts = config.get("approvalBindings", {}).get("mcpContexts", {})
            saved_contexts = secrets.get("mcpContexts", {})
            for server_id, target in mcp_contexts.items():
                saved_target = saved_contexts.get(server_id)
                if saved_target == previous_contexts.get(server_id) or target == saved_target:
                    continue
                # An unrelated save must not bind a replacement key to the old URL.
                retained_token = secrets["mcp"].get(server_id) and server_id not in changes.get("mcp", {})
                retained_env = set(secrets["mcpEnv"].get(server_id, {})) - set(env_changes.get(server_id, {}))
                if retained_token or retained_env:
                    raise IntegrationError("mcp-check-required", "Restore the intended MCP destination or re-enter its credentials before saving.")
            updated.setdefault("approvalBindings", {})["mcpContexts"] = mcp_contexts
            secrets["mcpContexts"] = mcp_contexts
            self.store.save_approvals([])
            self.store.save_secrets(secrets)
            self.store.save_config(updated)
            self._log("settings", "integrations", "saved")
            # Detach atomically so an older save cannot stop a newer admission.
            enabled = {server["id"] for server in updated["mcp"]["servers"] if server["enabled"]}
            with self.local.lock:
                stopped = [self.local.running.pop(server_id) for server_id in list(self.local.running)
                           if server_id not in enabled]
        for current in stopped:
            current.stop("it was turned off")

    @staticmethod
    def _mcp_context_current(config, secrets, server):
        bindings = config.get("approvalBindings", {})
        if "mcpContexts" not in bindings and "mcpContexts" not in secrets:
            return True  # Existing pre-marker installations retain their admission rules.
        expected = digest(server_context(server, secrets))
        return all(isinstance(contexts, dict) and contexts.get(server["id"]) == expected
                   for contexts in (bindings.get("mcpContexts"), secrets.get("mcpContexts")))

    def _require_mcp_context(self, config, secrets, server):
        if not self._mcp_context_current(config, secrets, server):
            raise IntegrationError("mcp-check-required", "The saved MCP destination and credentials could not be verified together. "
                                   "Review and save Portal integrations again before connecting.")

    def _session(self, server):
        # Construction does no I/O. Capture endpoint/command and credentials in
        # the same admission; __enter__ must run after releasing store.lock.
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
            if self._server(config, server["id"], enabled=False) != server:
                raise IntegrationError("stale-revision", "These settings changed. Reload them and try again.")
            self._require_mcp_context(config, secrets, server)
            admitted = _AdmittedLocalServers(self, digest(config, secrets, self._runtime_revision))
            return McpSession(server, secrets["mcp"].get(server["id"], ""), secrets["mcpEnv"].get(server["id"], {}), admitted)

    def _local_get(self, server, values, revision, *, deadline=None):
        """Start outside admission, but never publish a startup from stale settings.

        The per-server startup lock prevents an older admission replacing a newer
        process after waiting for its startup. Settings/get never wait for it.
        """
        deadline = time.monotonic() + SESSION_TIMEOUT if deadline is None else deadline
        candidate = LocalServer(server, values)
        with self.local.lock:
            starting = self.local.starting[server["id"]]
        if not starting.acquire(timeout=max(0, deadline - time.monotonic())):
            raise IntegrationError("mcp-busy", f"{server['name']} is still starting. Try again shortly.")
        try:
            with self.store.lock:
                if self._revision() != revision:
                    raise IntegrationError("stale-revision", "These settings changed before the local server started. Try again.")
                with self.local.lock:
                    current = self.local.running.get(server["id"])
                    if current and current.alive() and current.fingerprint == candidate.fingerprint:
                        return current
                    self.local.running.pop(server["id"], None)
            if current:
                current.stop("its settings changed")
            try:
                candidate.start(deadline=deadline)
                with self.store.lock:
                    if self._revision() != revision:
                        raise IntegrationError("stale-revision", "These settings changed while the local server started. Try again.")
                    with self.local.lock:
                        self.local.running[server["id"]] = candidate
                        self.local.failures.pop(server["id"], None)
                        if self.local.reaper is None:
                            self.local.reaper = threading.Thread(target=self.local._reap, daemon=True)
                            self.local.reaper.start()
            except BaseException:
                with self.local.lock:
                    self.local.failures[server["id"]] = candidate.detail() or candidate.stopped_because or ""
                candidate.stop("it failed to start or its settings changed")
                raise
            return candidate

        finally:
            starting.release()

    def _owner_tools(self, server):
        """The server's tools; a local program's failure also shows the owner its last error output."""
        try:
            with self.store.lock:
                admitted = self._session(server)
            with admitted as session:
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
        with self.store.lock:
            server = self._server(self.store.config(), server_id, enabled=False)
            secrets = self.store.secrets()
            context = digest(server_context(server, secrets))
            revision = self._revision()
        tools = self._owner_tools(server)
        with self.store.lock:
            if self._revision() != revision:
                raise IntegrationError("stale-revision", "These settings changed while checking tools. Reload them and try again.")
            config = self.store.config()
            server = self._server(config, server_id, enabled=False)
            if context != digest(server_context(server, self.store.secrets())):
                raise IntegrationError('mcp-context-changed', 'The server changed while checking tools. Check it again.')
            previous = {tool['name']: tool for tool in server['tools']}
            checked_before = server["discoveredAt"] is not None
            refreshed = []
            for tool in tools:
                described = describe_tool(tool)
                old = previous.get(tool['name'])
                same = old is not None and server.get('checkedBinding') == context and all(
                    old.get(key) == described[key] for key in ('name', 'title', 'description', 'readOnly', 'destructive', 'inputSchema'))
                refreshed.append(describe_tool(tool, old['policy'] if same else None, new=checked_before and not same))
            if server['tools'] != refreshed or server.get('checkedBinding') != context:
                self._binding(config, 'mcp', server_id, rotate=True)
            server['tools'] = refreshed
            server["discoveredAt"] = now()
            server['checkedBinding'] = context
            config["revision"] += 1
            self.store.save_approvals([])
            self.store.save_config(config)
            added = sum(tool["new"] for tool in server["tools"])
            self._log("mcp-discover", server["name"], f"{len(tools)} tools" + (f", {added} new" if added else ""))

    def test(self, server_id):
        """Connect and list the tools without changing anything; a sentence for the owner."""
        server = self._server(self.store.config(), server_id, enabled=False)
        names = {tool["name"] for tool in self._owner_tools(server)}
        known = {tool["name"] for tool in server["tools"]}
        added, gone = len(names - known), len(known - names)
        self._log("mcp-test", server["name"], f"{len(names)} tools")
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
            self._log("approval", match["summary"], match["state"])

    @staticmethod
    def _approval_snapshot(request):
        """Detached private receipt data, never part of an agent result or list."""
        if request is None:
            return None
        try:
            raw = json.dumps(request, ensure_ascii=True, allow_nan=False)
            # One lazy detail fits the existing 256 KiB owner response envelope.
            if len(raw.encode()) > 2 * MAX_REQUEST_BYTES:
                return None
            return json.loads(raw)
        except (TypeError, ValueError):
            return None

    def _approval_review_fields(self, request_digest, request):
        snapshot = self._approval_snapshot(request)
        return {"reviewPayload": snapshot,
                "reviewPayloadDigest": digest("approval-review-v1", request_digest, snapshot)}

    def approval_detail(self, approval_id):
        """Owner-only, one still-pending request; no provider or file-content read."""
        if not isinstance(approval_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", approval_id):
            raise IntegrationError("invalid-request", "An approval id is required.")
        with self.store.lock:
            item = next((entry for entry in self.store.approvals()
                         if entry["id"] == approval_id and entry["state"] == "pending"), None)
            if item is None:
                raise IntegrationError("approval-missing", "This request is no longer waiting for approval.")
            request = self._approval_snapshot(item.get("reviewPayload"))
            detail = {key: item[key] for key in ("id", "kind", "summary", "createdAt", "expiresAt")}
            secrets = self.store.secrets()
            detail["summary"] = redact_secrets(detail["summary"], secrets)[:400]
            keys = {"notes-write": {"action", "path", "mode", "text"},
                    "mcp-call": {"action", "server", "tool", "arguments"}}
            if (not isinstance(request, dict) or not isinstance(request.get("action"), str)
                    or set(request) != keys.get(request["action"], set())
                    or not isinstance(item.get("reviewPayloadDigest"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["reviewPayloadDigest"])
                    or not hmac.compare_digest(item["reviewPayloadDigest"],
                                               digest("approval-review-v1", item["digest"], request))):
                return {"approval": {**detail, "available": False}}
            if ((request["action"] == "notes-write" and
                 (not all(isinstance(request[key], str) for key in ("path", "text", "mode"))
                  or request["mode"] not in ("append", "create")))
                    or (request["action"] == "mcp-call" and
                        (not all(isinstance(request[key], str) for key in ("server", "tool"))
                         or not isinstance(request["arguments"], dict)))):
                return {"approval": {**detail, "available": False}}
            # Keep JSON types/structure intact: redact string leaves, not JSON text.
            def hide(value):
                if isinstance(value, str):
                    return redact_secrets(value, secrets)
                if isinstance(value, list):
                    return [hide(x) for x in value]
                if isinstance(value, dict):
                    return {hide(k): hide(v) for k, v in value.items()}
                return value
            hidden = hide(request)
            result = {"approval": {**detail, "available": True, "request": hidden, "redacted": hidden != request}}
            if len(json.dumps({"ok": True, **result}, ensure_ascii=True).encode()) > MAX_RESPONSE_BYTES - 2048:
                return {"approval": {**detail, "available": False}}
            return result

    # Gateway (Portal agent) actions.
    def _agent(self, action, request):
        if review_only_request(request):
            return self._review_only_agent(action, request)
        if REFERENCE_FIELD in request:
            raise IntegrationError("invalid-review-reference", "Private lookup context requires the native review path. Nothing ran.")
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
        if action == "notes-search":
            query = str(request.get("query", ""))[:300]
            hits = notes.search(config, query)
            self._log("notes-search", query[:80], f"{len(hits)} notes")
            return {"results": hits}
        if action == "notes-read":
            relative, text = notes.read(config, request.get("path"))
            self._log("notes-read", relative, "read")
            reply = {"path": relative, "text": text}
            if config["notes"].get("write") == "ask":
                reply["canRequestWriteApproval"] = True
            return reply
        if action == "notes-request-approval":
            if request.get("mode") not in ("append", "create"):
                raise IntegrationError("invalid-request", "Choose append or create for the proposed note edit.")
            reply = self._prepare_note_approval(request, notify_owner=True)
            status = "approval-ready" if reply["status"] == "review-only-ready" else reply["status"]
            return {"status": status, "approvalId": reply["approvalId"], "summary": reply["summary"],
                    "proposalOnly": True, "actionExecuted": False, "approvalConsumed": False}
        if action == "notes-write":
            return self._notes_write(config, request)
        if action == "notes-prepare":
            return self._prepare_notes_write(request)
        if action == "notify":
            title = send_notification(config, secrets, request.get("title"), request.get("message"),
                                      request.get("priority", "default"))
            self._log("notify", title, "sent")
            return {"sent": True}
        if action == "mcp-tools":
            return {"servers": self.app_servers(config) + self._agent_servers(config, secrets)}
        if action == "mcp-call":
            return self._mcp_call(config, request)
        if action == "mcp-prepare":
            return self._prepare_mcp_call(request)
        if action == "task-finished":
            return self.task_finished(config, request)
        if action in {"digest-plan", "digest-job-record", "digest-job-clear"}:
            return self.digest_control(action, request)
        raise IntegrationError("invalid-request", "Unknown integration action.")


    def _notes_write(self, config, request):
        mode, relative = request.get("mode", "append"), str(request.get("path", ""))
        notes.validate_write(request.get('text'), mode)
        waiting, notification = None, None
        with self.store.lock:
            config = self.store.config()
            policy = config['notes'].get('write')
            if policy not in ('ask', 'allow'):
                raise IntegrationError('notes-write-off', 'The owner has not allowed Portal to change notes.')
            relative, target = notes.write_binding(config, relative, mode, for_approval=policy == 'ask')
            epoch = self._binding(config, 'notes')
            if policy == 'ask':
                summary = f"{'Add to' if mode == 'append' else 'Create'} note {relative}"
                waiting, notification = self._approval('note', summary, note_approval_digest(epoch, target, relative, mode, request.get('text')),
                    {"action": "notes-write", "path": relative, "mode": mode, "text": request.get("text")})
            if not waiting:
                written = notes.write(config, relative, request.get('text'), mode, expected_binding=target)
                self._log("notes-write", written, mode)
        if notification:
            self._notify_approval(notification)
        if waiting:
            return waiting
        return {"written": written, "mode": mode}

    @staticmethod
    def _agent_servers(config, secrets):
        return [{"id": server["id"], "name": server["name"],
                 "tools": [{"name": tool["name"], "description": tool["description"], "inputSchema": tool["inputSchema"],
                            "needsApproval": tool["policy"] == "ask"} for tool in server["tools"]
                           if tool.get('policy') in ('ask', 'allow') and server.get('checkedBinding') == digest(server_context(server, secrets))]}
                for server in config["mcp"]["servers"] if server["enabled"] and Integrations._mcp_context_current(config, secrets, server)]

    def _mcp_call(self, config, request):
        name, arguments = request.get("tool"), request.get("arguments", {})
        if not isinstance(arguments, dict):
            raise IntegrationError("invalid-request", "Tool arguments must be an object.")
        if request.get("server") in APP_SERVER_IDS:
            self._app_arguments(APP_SERVER_IDS[request["server"]], name, arguments)
            with self.store.lock:
                config, secrets = self.store.config(), self.store.secrets()
                if APP_SERVER_IDS[request["server"]] == "watch":
                    self._binding(config, "apps", "watch")
            action, reply = self.use_app(config, APP_SERVER_IDS[request["server"]], name, arguments,
                                         run_context=request.get("runContext"), secrets=secrets)
            if reply.get("status") == "approval-required":
                return reply
            return {"server": request["server"], "tool": name,
                    "text": clip(redact_secrets(app_text(action, reply), secrets)), "isError": False}
        waiting, notification = None, None
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
            server = self._server(config, request.get('server'), enabled=True)
            self._require_mcp_context(config, secrets, server)
            context = digest(server_context(server, secrets))
            if server.get('checkedBinding') != context:
                raise IntegrationError('mcp-check-required', 'The server context changed. Check its tools again before using it.')
            tool = next((item for item in server['tools'] if item['name'] == name), None)
            if tool is None or tool.get('policy') not in ('ask', 'allow'):
                raise IntegrationError('tool-not-allowed', f"{server['name']} has no allowed tool named {name}. List its tools first.")
            epoch = self._binding(config, 'mcp', server['id'])
            if tool['policy'] == 'ask':
                argument_summary = redact_secrets(json.dumps(arguments, sort_keys=True), secrets)[:300]
                summary = redact_secrets(f"{server['name']}: {name} {argument_summary}", secrets)
                waiting, notification = self._approval('mcp', summary, digest('mcp-v2', epoch, context, tool, name, arguments),
                    {"action": "mcp-call", "server": server["id"], "tool": name, "arguments": arguments})
            if not waiting:
                admitted = self._session(server)
        if notification:
            self._notify_approval(notification)
        if waiting:
            return waiting
        with admitted as session:
            text, failed = session.call(name, arguments)
        self._log("mcp-call", f"{server['name']}: {name}", "error" if failed else "done")
        return {"server": server["id"], "tool": name, "text": redact_secrets(text, secrets), "isError": failed}

    def _binding(self, config, kind, server_id=None, *, rotate=False):
        bindings = config.setdefault('approvalBindings', {'notes': None, 'mcp': {}})
        values = bindings if kind == 'notes' else bindings.setdefault(kind, {})
        key = 'notes' if kind == 'notes' else server_id
        if rotate or not values.get(key):
            values[key] = uuid.uuid4().hex
            self.store.save_config(config)
        return values[key]

    @staticmethod
    def _require_prepare_policy(policy):
        if policy not in ("ask", "allow"):
            raise IntegrationError("preparation-off", "The owner has disabled this action. No request was prepared.")

    def _prepare_notes_write(self, request):
        if set(request) - {"schemaVersion", "action", "path", "text", "mode"} or not isinstance(request.get("path"), str):
            raise IntegrationError("invalid-request", "Prepare needs only the exact note path, text and mode.")
        mode = request.get("mode", "append")
        notes.validate_write(request.get("text"), mode)
        with self.store.lock:
            config = self.store.config()
            if not config["notes"]["enabled"]:
                raise IntegrationError("notes-off", "Obsidian notes are not connected.")
            self._require_prepare_policy(config["notes"].get("write"))
            relative, target = notes.write_binding(config, request["path"], mode, for_approval=True)
            epoch = self._binding(config, "notes")
            summary = f"{'Add to' if mode == 'append' else 'Create'} note {relative}"
            return self._prepare_approval("note", summary,
                                          note_approval_digest(epoch, target, relative, mode, request["text"]),
                                          {"action": "notes-write", "path": relative, "mode": mode, "text": request["text"]})

    def _prepare_mcp_call(self, request):
        if set(request) - {"schemaVersion", "action", "server", "tool", "arguments"}:
            raise IntegrationError("invalid-request", "Prepare needs only the exact server, tool and arguments.")
        name, arguments = request.get("tool"), request.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise IntegrationError("invalid-request", "Tool name and arguments are required.")
        server_id = request.get("server")
        if not isinstance(server_id, str):
            raise IntegrationError("invalid-request", "A server id is required.")
        if server_id in APP_SERVER_IDS:
            app = APP_SERVER_IDS[server_id]
            self._app_arguments(app, name, arguments)
            self._review_app_values(app, name, arguments)
            if app != "watch" or name not in {"add", "remove"}:
                raise IntegrationError("preparation-not-supported", "This builtin has no approval preparation action.")
            with self.store.lock:
                config = self.store.config()
                if not config["apps"]["watch"]["enabled"]:
                    raise IntegrationError("app-off", "Watched pages are not connected.")
                self._require_prepare_policy(config["apps"]["watch"].get("changes"))
                self._binding(config, "apps", "watch")
            return self._review_watch_change(name, arguments, preparation_only=True)
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
            server = self._server(config, server_id, enabled=True)
            self._require_mcp_context(config, secrets, server)
            context = digest(server_context(server, secrets))
            if server.get("checkedBinding") != context:
                raise IntegrationError("mcp-check-required", "The server context changed. Check its tools again before preparing it.")
            tool = next((item for item in server["tools"] if item["name"] == name), None)
            if tool is None or tool.get("policy") not in ("ask", "allow"):
                raise IntegrationError("tool-not-allowed", "This server has no allowed tool with that name.")
            self._require_prepare_policy(tool["policy"])
            epoch = self._binding(config, "mcp", server["id"])
            argument_summary = redact_secrets(json.dumps(arguments, sort_keys=True), secrets)[:300]
            summary = redact_secrets(f"{server['name']}: {name} {argument_summary}", secrets)
            return self._prepare_approval("mcp", summary, digest("mcp-v2", epoch, context, tool, name, arguments),
                                          {"action": "mcp-call", "server": server["id"], "tool": name, "arguments": arguments})

    def _prepare_approval(self, kind, summary, request_digest, review_payload=None):
        """A new RPC ceiling: ledger only, even under Allowed or an approved grant."""
        with self.store.lock:
            approvals = self.store.approvals()
            exact = [entry for entry in approvals if entry.get("digest") == request_digest]
            approved = next((entry for entry in exact if entry.get("state") == "approved"), None)
            if approved:
                return {"status": "approval-ready", "approvalId": approved["id"], "summary": approved["summary"],
                        "preparationOnly": True, "actionExecuted": False, "approvalConsumed": False}
            if any(entry.get("state") == "declined" for entry in exact):
                raise IntegrationError("preparation-declined", "The exact request was declined. It was not reopened.")
            if any(entry.get("state") not in {"pending", "used"} for entry in exact):
                raise IntegrationError("preparation-state-unavailable", "The saved request state cannot be prepared safely.")
            pending = next((entry for entry in exact if entry.get("state") == "pending"), None)
            if pending is None:
                summary = redact_secrets(summary, self.store.secrets())[:400]
                pending = {"id": uuid.uuid4().hex[:12], "kind": kind, "summary": summary, "digest": request_digest,
                           "state": "pending", "createdAt": now(), "expiresAt": now() + APPROVAL_SECONDS,
                           **self._approval_review_fields(request_digest, review_payload)}
                self.store.save_approvals(approvals + [pending])
                self._log("approval", summary, "prepared without execution")
            return {"status": "approval-required", "approvalId": pending["id"], "summary": pending["summary"],
                    "preparationOnly": True, "actionExecuted": False, "approvalConsumed": False}

    def _approval(self, kind, summary, request_digest, review_payload=None):
        """Return (waiting reply, notification); no external I/O under admission.

        (None, None) consumes an exact approval once. Only a new pending request
        supplies a notification summary, sent by the caller after unlocking.
        """
        with self.store.lock:
            approvals = self.store.approvals()
            approved = next((item for item in approvals if item["digest"] == request_digest and item["state"] == "approved"), None)
            if approved:
                approved["state"], approved["usedAt"] = "used", now()
                self.store.save_approvals(approvals)
                return None, None
            pending = next((item for item in approvals if item["digest"] == request_digest and item["state"] == "pending"), None)
            created = pending is None
            if created:
                pending = {"id": uuid.uuid4().hex[:12], "kind": kind, "summary": summary[:400], "digest": request_digest,
                           "state": "pending", "createdAt": now(), "expiresAt": now() + APPROVAL_SECONDS,
                           **self._approval_review_fields(request_digest, review_payload)}
                self.store.save_approvals(approvals + [pending])
                self._log("approval", summary, "requested")
        return ({"status": "approval-required", "approvalId": pending["id"], "summary": pending["summary"]},
                summary if created else None)

    def _notify_approval(self, summary):
        with self.store.lock:
            config, secrets = self.store.config(), self.store.secrets()
        settings = config["notifications"]
        if not (settings["enabled"] and settings["notifyApprovals"]):
            return
        try:
            send_notification(config, secrets, "Portal needs your approval", f"{summary}\n\n{APPROVAL_HELP}",
                              "high", settings["approvalLink"])
        except IntegrationError as error:
            # The approval is still waiting in Settings; only the ping failed.
            self._log("notify", "approval", error.message)

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

    def __init__(self, *args, bounded=False, **kwargs):
        self.slots = threading.BoundedSemaphore(32) if bounded else None
        if bounded:
            self.daemon_threads = False
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if self.slots is not None and not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            if self.slots is not None:
                self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            if self.slots is not None:
                self.slots.release()


def _listener(service, socket_path, role, *, owner_key=None, stop=None):
    """A socket for one role that answers only processes of this service's own user."""
    from unix_peer import peer_ids
    native = sys.platform == 'darwin'
    socket_path = pathlib.Path(socket_path)
    if socket_path.exists() or socket_path.is_symlink():
        if not stat.S_ISSOCK(socket_path.lstat().st_mode):
            raise SystemExit("unsafe existing integrations socket")
        socket_path.unlink()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            authenticated_request = None
            uid, _gid = peer_ids(self.request)
            if uid != os.getuid():
                reply = {"ok": False, "error": "forbidden", "message": "This caller may not use integrations."}
            else:
                self.request.settimeout(OWNER_AUTH_SECONDS if owner_key is not None else REQUEST_SECONDS)
                authentication_deadline = time.monotonic() + OWNER_AUTH_SECONDS
                nonce = os.urandom(32).hex() if owner_key is not None else None
                if nonce is not None:
                    self.wfile.write((json.dumps({"schemaVersion": 1, "challenge": nonce}) + "\n").encode())
                    self.wfile.flush()
                try:
                    if nonce:
                        line = read_owner_frame(self.request, MAX_REQUEST_BYTES + 1024, authentication_deadline)
                    elif native:
                        line = read_owner_frame(self.request, MAX_REQUEST_BYTES, time.monotonic() + REQUEST_SECONDS)
                    else:
                        line = self.rfile.readline(MAX_REQUEST_BYTES + 1)
                    if nonce is not None:
                        envelope = json.loads(line)
                        if (type(envelope) is not dict or set(envelope) != {"schemaVersion", "clientNonce", "request", "proof"}
                                or type(envelope["schemaVersion"]) is not int or envelope["schemaVersion"] != 1
                                or type(envelope["proof"]) is not str
                                or not hmac.compare_digest(envelope["proof"], owner_proof(owner_key,
                                    owner_auth_binding(nonce, envelope['clientNonce']), envelope["request"]))):
                            raise ValueError()
                        line = json.dumps(envelope["request"], separators=(",", ":")).encode()
                        authenticated_request = envelope['request']
                        binding = owner_auth_binding(nonce, envelope['clientNonce'])
                except (ValueError, TypeError, KeyError, OSError):
                    reply = ({"ok": False, "error": "forbidden", "message": "Owner authentication failed."}
                             if owner_key is not None else {"ok": False, "error": "invalid-request", "message": "The request was incomplete."})
                else:
                    if len(line) > MAX_REQUEST_BYTES:
                        reply = {"ok": False, "error": "too-large", "message": "The request is too large."}
                    elif stop is not None and stop.is_set():
                        reply = {"ok": False, "error": "stopping", "message": "The integrations service is stopping."}
                    else:
                        self.request.settimeout(REQUEST_SECONDS)
                        reply = handle_line(service, role, line)
            if authenticated_request is not None:
                reply = {'schemaVersion': 1, 'reply': reply,
                         'proof': owner_reply_proof(owner_key, binding, authenticated_request, reply)}
            self.wfile.write((json.dumps(reply, separators=(",", ":")) + "\n").encode())

    server = _Server(str(socket_path), Handler, bounded=owner_key is not None or native)
    os.chmod(socket_path, 0o600)
    return server


def watch_pages(service, stop, seconds=WATCH_SECONDS):
    """Look at the owner's watched pages every few minutes until stop is set."""
    while not stop.wait(seconds):
        publisher = getattr(service, 'publish_discovery', None)
        if publisher:
            publisher()  # Renews expiry without starting/touching MCP providers.
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


class RunningIntegrations(list):
    """Listeners and their shutdown custody; list-compatible with Linux callers."""

    def close(self):
        self.stop.set()
        errors = []
        if self.native:
            try:
                stop_http_workers()
            except Exception as error:
                errors.append(error)
        for server in self:
            try:
                server.shutdown()
                server.server_close()
            except Exception as error:
                errors.append(error)
        try:
            self.service.local.close()
        except Exception as error:
            errors.append(error)
        if self.native:
            self.watcher.join(5)
            if self.watcher.is_alive():
                errors.append(RuntimeError('native-integrations-watcher-stop-unconfirmed'))
        if errors:
            raise RuntimeError('integrations-stop-unconfirmed') from errors[0]


def start(agent_socket, state_dir):
    """Both listeners and the page watcher, in background threads; the owner socket is STATE_DIR/owner.sock."""
    if sys.platform == 'darwin':
        validate_native_state(state_dir)
    service = Integrations(Store(state_dir))
    stop = threading.Event()
    servers = RunningIntegrations()
    servers.service, servers.stop, servers.failed, servers.native = service, stop, False, sys.platform == 'darwin'
    key = native_owner_key() if sys.platform == "darwin" else None
    try:
        servers.append(_listener(service, agent_socket, "agent", stop=stop))
        servers.append(_listener(service, pathlib.Path(state_dir) / OWNER_SOCKET, "owner", owner_key=key, stop=stop))
        service.discovery_path = pathlib.Path(agent_socket).parent / "discovery.json"
        service.publish_discovery()
    except BaseException:
        for server in servers:
            server.server_close()
        raise
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    def failed(_code):
        servers.failed = True
        stop.set()
    servers.watcher = threading.Thread(target=watch_or_exit, args=(service, stop), kwargs={"exit": failed}, daemon=True)
    servers.watcher.start()
    return servers


def serve(agent_socket, state_dir):
    servers = start(agent_socket, state_dir)
    previous = {}
    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, lambda _number, _frame: servers.stop.set())
        servers.stop.wait()
    finally:
        try:
            servers.close()
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    if servers.failed:
        raise SystemExit(1)


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
    if len(sys.argv) != 4 or sys.argv[1] not in ("serve", "health"):
        raise SystemExit("usage: integrations_service.py serve|health AGENT_SOCKET STATE_DIR")
    if sys.argv[1] == "health":
        request = {"schemaVersion": 1, "action": "get"}
        try:
            owner = (native_owner_request(pathlib.Path(sys.argv[3]) / OWNER_SOCKET, request, timeout=5)
                     if sys.platform == "darwin" else client(pathlib.Path(sys.argv[3]) / OWNER_SOCKET, request, timeout=5))
            agent = client(sys.argv[2], {"schemaVersion": 1, "action": "mcp-tools"}, timeout=5)
        except (FileNotFoundError, ConnectionRefusedError):
            # A freshly bootstrapped service may not have opened both sockets.
            # The installer may retry this read-only probe within its deadline.
            raise SystemExit(75)
        if owner.get("ok") is not True or agent.get("ok") is not True or type(agent.get("servers")) is not list:
            raise SystemExit("native integrations are not ready")
        print(json.dumps({"schemaVersion": 1, "status": "ready", "owner": True, "agent": True}))
    else:
        serve(sys.argv[2], sys.argv[3])
