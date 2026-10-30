"""Shared pieces of Portal integrations: errors, limits and the owner-only state files."""
import base64
import email.message
import hashlib
import http.client
import io
import math
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import urllib.error
import uuid
import urllib.request

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MAX_RESULT_CHARS = 8000
NETWORK_TIMEOUT = 20
# UTF8 agent JSON can expand3x as MCP ASCII escapes, then4/3 as private base64.
MAX_HTTP_WORKER_INPUT = 8 * MAX_REQUEST_BYTES
ACTIVITY_LINES = 500


class IntegrationError(Exception):
    """A refusal or failure with a code for programs and a sentence for people."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message



class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Authorization must never leave the configured endpoint, even if it
        # redirects to another port on the same machine. The owner can review
        # and save the final address explicitly instead.
        raise IntegrationError("redirect-refused", "The integration endpoint redirected the request. "
                               "Use its final address instead.")


class _BufferedResponse(io.BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status = self.code = status
        self.headers = email.message.Message()
        for name, value in headers.items():
            self.headers[name] = value

    def getcode(self):
        return self.status


def safe_urlopen(request, *, timeout=NETWORK_TIMEOUT):
    """One bounded HTTP operation, including DNS, headers and the full body.

    urllib's socket timeout only bounds inactivity. A private owned worker lets
    us stop/reap an operation even when a peer drips headers or body forever.
    Credentials travel only through its private pipe, never argv/environment.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise IntegrationError("integration-timeout", "The integration request timed out; its result could not be verified.")
    timeout = min(float(timeout), NETWORK_TIMEOUT)
    deadline = time.monotonic() + timeout
    payload = json.dumps({"url": request.full_url, "method": request.get_method(),
                          "headers": dict(request.header_items()),
                          "body": base64.b64encode(request.data or b"").decode("ascii")}).encode()
    if len(payload) > MAX_HTTP_WORKER_INPUT:
        raise IntegrationError("request-too-large", "The integration request is too large.")
    worker = subprocess.Popen([sys.executable, "-I", "-B", str(pathlib.Path(__file__).resolve()), "--http-worker"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    try:
        output, _ = worker.communicate(payload, timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        worker.kill()
        worker.communicate()  # No terminal receipt until this exact owned worker is reaped.
        raise IntegrationError("integration-timeout", "The integration request timed out; its result could not be verified. "
                               "Check the service before trying again.") from None
    if worker.returncode != 0 or len(output) > 2 * MAX_RESPONSE_BYTES:
        raise IntegrationError("integration-unavailable", "The integration request did not complete.")
    try:
        value = json.loads(output)
        if value.get("kind") == "http-error":
            raise urllib.error.HTTPError(request.full_url, value["status"], "Integration endpoint refused the request.", {}, None)
        if value.get("kind") == "network-error":
            raise urllib.error.URLError("Integration endpoint could not be reached.")
        if value.get("kind") == "timeout":
            raise IntegrationError("integration-timeout", "The integration request timed out; its result could not be verified. "
                                   "Check the service before trying again.")
        if value.get("kind") == "redirect-refused":
            raise IntegrationError("redirect-refused", "The integration endpoint redirected the request. Use its final address instead.")
        if value.get("kind") == "too-large":
            raise IntegrationError("integration-response-too-large", "The integration endpoint sent too much data.")
        if value.get("kind") != "response":
            raise ValueError
        body = base64.b64decode(value["body"], validate=True)
        if len(body) > MAX_RESPONSE_BYTES:
            raise IntegrationError("integration-response-too-large", "The integration endpoint sent too much data.")
        return _BufferedResponse(body, value["status"], value["headers"])
    except (KeyError, TypeError, ValueError):
        raise IntegrationError("integration-protocol", "The integration endpoint returned an invalid response.") from None


def _http_worker():
    # No inherited owner environment, hooks, plugins or subprocess commands.
    # This worker performs only one HTTP request and writes a bounded envelope.
    http.client._MAXLINE = 16 * 1024
    http.client._MAXHEADERS = 64
    try:
        raw = sys.stdin.buffer.read(MAX_HTTP_WORKER_INPUT + 1)
        if len(raw) > MAX_HTTP_WORKER_INPUT:
            raise ValueError
        value = json.loads(raw)
        request = urllib.request.Request(value["url"], data=base64.b64decode(value["body"], validate=True),
                                         headers=value["headers"], method=value["method"])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirects())
        with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
            if sum(len(key) + len(item) for key, item in response.headers.items()) > 16 * 1024:
                raise ValueError
            body = response.read(MAX_RESPONSE_BYTES + 1)
            # Keep the size failure fixed; never echo remote content or URLs.
            if len(body) > MAX_RESPONSE_BYTES:
                result = {"kind": "too-large"}
            else:
                headers = {key: response.headers[key] for key in ("Content-Type", "Mcp-Session-Id") if key in response.headers}
                result = {"kind": "response", "status": response.status, "headers": headers,
                          "body": base64.b64encode(body).decode("ascii")}
    except urllib.error.HTTPError as error:
        result = {"kind": "http-error", "status": error.code}
        error.close()
    except IntegrationError:
        result = {"kind": "redirect-refused"}
    except TimeoutError:
        result = {"kind": "timeout"}
    except urllib.error.URLError as error:
        result = {"kind": "timeout" if isinstance(error.reason, TimeoutError) else "network-error"}
    except OSError:
        result = {"kind": "network-error"}
    except Exception:
        result = {"kind": "protocol-error"}
    sys.stdout.buffer.write(json.dumps(result, separators=(",", ":")).encode())
    sys.stdout.buffer.flush()


def redact_secrets(value, secrets):
    """Remove known configured plaintext string values before output clipping.

    Preserve structural JSON field names, including when a short secret is
    itself a word such as "id" or "name".

    This is output hygiene, not a sandbox for a malicious server: transformed,
    encoded or independently exfiltrated values are outside this guarantee.
    """
    known = set()

    def collect(item):
        if isinstance(item, str):
            if item:
                known.add(item)
                # stderr is consumed line by line; a multiline configured
                # value must not reveal its individual nonempty lines either.
                known.update(part for part in item.splitlines() if part)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, (list, tuple, set, frozenset)):
            for child in item:
                collect(child)

    collect(secrets)
    ordered = sorted(known, key=lambda item: (-len(item), item))

    def clean(item):
        if isinstance(item, str):
            for secret in ordered:
                item = item.replace(secret, "[redacted]")
            return item
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, tuple):
            return tuple(clean(child) for child in item)
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items()}
        return item

    return clean(value)


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
        # Approval consumption is committed before the external attempt. The
        # rename must be durable too; a failed sync must not qualify an effect.
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

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


if __name__ == "__main__":
    if sys.argv[1:] != ["--http-worker"]:
        raise SystemExit(2)
    _http_worker()
