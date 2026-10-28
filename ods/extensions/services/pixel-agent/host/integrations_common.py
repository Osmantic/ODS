"""Shared pieces of Portal integrations: errors, limits and the owner-only state files."""
import hashlib
import json
import os
import pathlib
import threading
import time
import uuid

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RESULT_CHARS = 8000
NETWORK_TIMEOUT = 20
ACTIVITY_LINES = 500


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
                          "username": "", "notifyApprovals": True},
        "notes": {"enabled": False, "vaultPath": "", "write": "ask"},
        "mcp": {"servers": []},
    }


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
        return value if value is not None else default_config()

    def save_config(self, value):
        self._write("config.json", value)

    def secrets(self):
        return self._read("secrets.json", {"ntfyPassword": "", "mcp": {}})

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

    def activity(self, limit=50):
        path = self.root / "activity.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()[-limit:]][::-1]
