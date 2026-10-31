"""Shared pieces of Portal integrations: errors, limits and the owner-only state files."""
import hashlib
import json
import os
import pathlib
import threading
import time
import uuid
import urllib.request

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RESULT_CHARS = 8000
NETWORK_TIMEOUT = 20
ACTIVITY_LINES = 500


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect is a different request, not part of the approved action.
        # Never forward credentials or replay a POST, even same-origin.
        return None


def open_direct(request, *, timeout):
    """Use the configured URL exactly; ordinary TLS verification stays enabled."""
    return urllib.request.build_opener(_NoRedirects()).open(request, timeout=timeout)


class IntegrationError(Exception):
    """A refusal or failure with a code for programs and a sentence for people."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def now():
    return int(time.time())


def digest(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def clip(text, limit=MAX_RESULT_CHARS):
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


def default_config():
    return {
        "schemaVersion": SCHEMA_VERSION,
        "revision": 0,
        "notifications": {"enabled": False, "url": "http://127.0.0.1:8097", "topic": "portal",
                          "username": "", "notifyApprovals": True, "approvalLink": ""},
        "notes": {"enabled": False, "vaultPath": "", "write": "ask"},
        "mcp": {"servers": []},
    }


def upgrade_config(config):
    """Settings saved by an earlier version, with the fields added since."""
    config["notifications"].setdefault("approvalLink", "")
    for server in config["mcp"]["servers"]:
        server.setdefault("workingFolder", "")
        server.setdefault("envNames", [])
        for tool in server["tools"]:
            tool.setdefault("new", False)
    return config


class Store:
    """Settings, secrets, approvals and the activity log, each an owner-only file."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.lock = threading.RLock()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _read(self, name, default):
        try:
            return json.loads((self.root / name).read_text())
        except FileNotFoundError:
            return default
        except ValueError:
            raise IntegrationError("state-damaged", f"The integrations file {self.root / name} is damaged. "
                                   "Restore it from a backup, or remove it to start that part over.") from None

    def _write(self, name, value):
        path = self.root / name
        temporary = path.with_name(f".{name}.{uuid.uuid4().hex}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=1, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def config(self):
        value = self._read("config.json", None)
        return upgrade_config(value) if value is not None else default_config()

    def save_config(self, value):
        self._write("config.json", value)

    def secrets(self):
        return {"ntfyPassword": "", "mcp": {}, "mcpEnv": {}, **self._read("secrets.json", {})}

    def save_secrets(self, value):
        self._write("secrets.json", value)

    def approvals(self):
        current = now()
        return [item for item in self._read("approvals.json", []) if item["expiresAt"] > current]

    def save_approvals(self, value):
        self._write("approvals.json", value[-200:])

    def log(self, action, target, outcome):
        path = self.root / "activity.jsonl"
        entry = json.dumps({"at": now(), "action": action, "target": target[:200], "outcome": outcome[:200]})
        lines = path.read_text().splitlines() if path.exists() else []
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join((lines + [entry])[-ACTIVITY_LINES:]) + "\n")

    def clear_activity(self):
        fd = os.open(self.root / "activity.jsonl", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)

    def activity(self, limit=50):
        path = self.root / "activity.jsonl"
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().splitlines()[-limit:]:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue  # A line cut short by a crash; the rest of the log is still useful.
        return entries[::-1]
