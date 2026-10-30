"""A small MCP client for Portal integrations.

Speaks JSON-RPC 2.0 to a server reached over Streamable HTTP (one POST per message, the
reply as JSON or as server-sent events) or started as a local command (one JSON message
per line on stdin and stdout). A web server gets a short session per listing or call. A
local server keeps running between calls, so a slow start (npx, uvx) is paid once and a
server that remembers things between calls (a browser, a database) works; it stops after
IDLE_SECONDS without calls and starts again on the next one. The owner chose the server;
nothing here picks one.
"""
import collections
import glob
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import selectors
import subprocess
import threading
import time
import urllib.error
import urllib.request

from integrations_common import (IntegrationError, MAX_RESPONSE_BYTES, NETWORK_TIMEOUT, clip, digest,
                                 redact_secrets, safe_urlopen)

MCP_PROTOCOL = "2025-03-26"
MAX_TOOLS = 200
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
START_TIMEOUT = 90  # npx and uvx may download the server on its first start.
CALL_TIMEOUT = 30
IDLE_SECONDS = 600
STOP_SECONDS = 3
SESSION_TIMEOUT = 120  # Whole initialize/list/call journey; cleanup fits the130s client.
ERROR_LINES = 20
# The service runs with a minimal PATH; these are where owners usually install npx, uvx and friends.
SEARCH_PATH = ("~/.local/bin", "~/bin", "~/.npm-global/bin", "~/.cargo/bin", "/usr/local/bin", "/usr/bin", "/bin",
               "/snap/bin", "/home/linuxbrew/.linuxbrew/bin")
PASSED_ENV = ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "TZ")


def _result(name, reply, secrets=()):
    if "error" in reply:
        error = reply["error"] if isinstance(reply["error"], dict) else {}
        detail = redact_secrets(f"{name}: {str(error.get('message', 'request failed'))}", secrets)
        raise IntegrationError("mcp-error", detail[:300])
    return reply.get("result")


class HttpSession:
    """One Streamable HTTP session: initialize, then requests, each a POST."""

    def __init__(self, server, token, *, deadline=None):
        self.server, self.token, self.name = server, token, server["name"]
        self.counter = 0
        self.session_id = None
        self.secret_values = (token,)
        self.deadline = deadline

    def open(self):
        result = self.request("initialize", {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                                             "clientInfo": {"name": "ods-portal", "version": "1"}})
        if not isinstance(result, dict):
            raise IntegrationError("mcp-protocol", f"{self.name} did not answer as an MCP server.")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def _headers(self):
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": MCP_PROTOCOL}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, message):
        """The HTTP reply body and content type; a notification gets an empty 202."""
        request = urllib.request.Request(self.server["url"], data=json.dumps(message).encode(),
                                         headers=self._headers(), method="POST")
        try:
            remaining = NETWORK_TIMEOUT if self.deadline is None else min(NETWORK_TIMEOUT, self.deadline - time.monotonic())
            with safe_urlopen(request, timeout=remaining) as response:
                self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                return response.read(MAX_RESPONSE_BYTES + 1), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise IntegrationError("mcp-auth", f"{self.name} refused the access token.") from None
            raise IntegrationError("mcp-unavailable", f"{self.name} answered {error.code}.") from None
        except (urllib.error.URLError, OSError):
            raise IntegrationError("mcp-unavailable", f"{self.name} could not be reached at {self.server['url']}.") from None

    def request(self, method, params):
        self.counter += 1
        message = {"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}
        body, kind = self._post(message)
        if len(body) > MAX_RESPONSE_BYTES:
            raise IntegrationError("mcp-protocol", f"{self.name} sent too much data.")
        text = body.decode("utf-8", "replace")
        candidates = ([line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
                      if "text/event-stream" in kind else [text])
        for candidate in candidates:
            try:
                reply = json.loads(candidate)
            except ValueError:
                continue  # Event-stream keep-alives and comments are not replies.
            for item in reply if isinstance(reply, list) else [reply]:
                if isinstance(item, dict) and item.get("id") == message["id"]:
                    return _result(self.name, item, self.secret_values)
        raise IntegrationError("mcp-protocol", f"{self.name} did not answer as an MCP server.")


def command_env(server, values):
    """The environment a local server starts with: a usable PATH, the owner's basics and its own variables."""
    argv = shlex.split(server["command"])
    folders = [str(pathlib.Path(argv[0]).parent)] if os.path.isabs(argv[0]) else []
    folders += [os.path.expanduser(folder) for folder in SEARCH_PATH]
    folders += sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")), reverse=True)[:1]
    env = {key: os.environ[key] for key in PASSED_ENV if key in os.environ}
    env["PATH"] = os.pathsep.join(dict.fromkeys(folders))
    env.update({name: values[name] for name in server.get("envNames", []) if name in values})
    return argv, env


class LocalServer:
    """One running local-command server, answering one request at a time."""

    def __init__(self, server, values):
        self.name, self.id = server["name"], server["id"]
        self.argv, self.env = command_env(server, values)
        self.secret_values = dict(values)
        self.folder = server.get("workingFolder") or self.env.get("HOME") or "/"
        self.fingerprint = digest(server["command"], self.folder, self.env)
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.replies = {}
        self.counter = 0
        self.errors = collections.deque(maxlen=ERROR_LINES)
        self.process = None
        self.stopped_because = None
        self.last_used = time.monotonic()

    def start(self, *, deadline=None):
        deadline = min(time.monotonic() + START_TIMEOUT, deadline) if deadline is not None else time.monotonic() + START_TIMEOUT
        if deadline <= time.monotonic():
            raise self._timeout()
        program = shutil.which(self.argv[0], path=self.env["PATH"])
        if program is None:
            raise IntegrationError("mcp-unavailable", f"{self.name} could not be started: {self.argv[0]} was not found. "
                                   f"Give its full path, as `which {self.argv[0]}` shows it in a terminal.")
        if not os.path.isdir(self.folder):
            raise IntegrationError("mcp-unavailable", f"{self.name} could not be started: its folder {self.folder} does not exist.")
        try:
            # Its own process group, so stopping it also stops what it started (npx starts node).
            self.process = subprocess.Popen([program, *self.argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, cwd=self.folder, env=self.env, start_new_session=True)
        except OSError as error:
            raise IntegrationError("mcp-unavailable", f"{self.name} could not be started: {error.strerror}.") from None
        os.set_blocking(self.process.stdin.fileno(), False)
        threading.Thread(target=self._read_replies, daemon=True).start()
        threading.Thread(target=self._read_errors, daemon=True).start()
        result = self.request("initialize", {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                                             "clientInfo": {"name": "ods-portal", "version": "1"}}, timeout=max(0, deadline - time.monotonic()))
        if not isinstance(result, dict):
            self.stop("it did not answer as an MCP server")
            raise IntegrationError("mcp-protocol", f"{self.name} did not answer as an MCP server.")
        try:
            self._write({"jsonrpc": "2.0", "method": "notifications/initialized"}, deadline=deadline)
        except IntegrationError:
            self.stop("initialization did not finish")
            raise
        return self

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def detail(self):
        """The program's last error output, for the owner only: it may name local paths."""
        return "\n".join(self.errors)[-1500:]

    def _read_errors(self):
        oversized = False
        while True:
            line = self.process.stderr.readline(MAX_RESPONSE_BYTES + 1)
            if not line:
                break
            if oversized or len(line) > MAX_RESPONSE_BYTES:
                if not oversized:
                    self.errors.append("[oversized stderr line omitted]")
                oversized = not line.endswith(b"\n")
                continue
            # Redact before truncation: a token crossing the displayed boundary
            # must not be emitted as a raw prefix. Oversized lines are dropped.
            text = redact_secrets(line.decode("utf-8", "replace").rstrip(), self.secret_values)
            self.errors.append(text[:300])
        self.process.stderr.close()

    def _read_replies(self):
        stream = self.process.stdout
        while True:
            line = stream.readline(MAX_RESPONSE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_RESPONSE_BYTES:
                self.stop("it sent too much data")
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue  # Servers may print other lines; only JSON-RPC messages count.
            if not isinstance(message, dict):
                continue
            if "method" in message and "id" in message:
                self._answer_server(message)
            elif message.get("id") in self.replies:
                waiting = self.replies[message["id"]]
                waiting[1] = message
                waiting[0].set()
        self.stopped_because = self.stopped_because or "it stopped"
        for waiting in list(self.replies.values()):
            waiting[0].set()
        stream.close()

    def _answer_server(self, message):
        """Portal offers servers no extra client features; answer their questions so they never wait."""
        if message["method"] == "ping":
            reply = {"result": {}}
        elif message["method"] == "roots/list":
            reply = {"result": {"roots": []}}
        else:
            reply = {"error": {"code": -32601, "message": "Portal does not support this request."}}
        try:
            self._write({"jsonrpc": "2.0", "id": message["id"], **reply})
        except IntegrationError:
            pass  # The server stopped; the waiting request reports that.

    def _timeout(self):
        return IntegrationError("mcp-timeout", f"{self.name} did not answer in time, so it was stopped. "
                                "It starts again on the next use.")

    def _write(self, message, *, deadline=None):
        deadline = time.monotonic() + CALL_TIMEOUT if deadline is None else deadline
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self.write_lock.acquire(timeout=remaining):
            raise self._timeout()
        try:
            descriptor = self.process.stdin.fileno()
            data = memoryview((json.dumps(message) + "\n").encode())
            with selectors.DefaultSelector() as poller:
                poller.register(descriptor, selectors.EVENT_WRITE)
                while data:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise self._timeout()
                    try:
                        written = os.write(descriptor, data)
                        if written <= 0:
                            raise OSError("pipe closed")
                        data = data[written:]
                    except BlockingIOError:
                        if not poller.select(remaining):
                            raise self._timeout()
                    except InterruptedError:
                        continue
        except (OSError, ValueError):
            raise IntegrationError("mcp-unavailable", f"{self.name} stopped.") from None
        finally:
            self.write_lock.release()

    def request(self, method, params, timeout=None):
        timeout = CALL_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + timeout
        if not self.lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise IntegrationError("mcp-busy", f"{self.name} is still busy with another request. Try again shortly.")
        try:
            self.last_used = time.monotonic()
            if not self.alive():
                raise IntegrationError("mcp-unavailable", f"{self.name} stopped: {self.stopped_because or 'it is not running'}.")
            self.counter += 1
            waiting = self.replies[self.counter] = [threading.Event(), None]
            try:
                self._write({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}, deadline=deadline)
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not waiting[0].wait(remaining):
                    raise self._timeout()
            except IntegrationError:
                self.stop(f"request did not complete within {timeout} seconds")
                raise
            finally:
                del self.replies[self.counter]
            if waiting[1] is None:
                raise IntegrationError("mcp-unavailable", f"{self.name} stopped: {self.stopped_because}.")
            return _result(self.name, waiting[1], self.secret_values)
        finally:
            self.last_used = time.monotonic()
            self.lock.release()

    def stop(self, reason="it was stopped"):
        if self.process is None:
            return
        self.stopped_because = self.stopped_because or reason
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                break
            try:
                self.process.wait(timeout=STOP_SECONDS)
                break
            except subprocess.TimeoutExpired:
                continue
        try:
            os.killpg(self.process.pid, signal.SIGKILL)  # Anything it started that outlived it.
        except ProcessLookupError:
            pass
        with self.write_lock:
            try:
                self.process.stdin.close()
            except OSError:
                pass  # Its end of the pipe is already gone.


class LocalServers:
    """The running local servers, by server id: started on first use, stopped when idle."""

    def __init__(self, idle_seconds=IDLE_SECONDS):
        self.idle_seconds = idle_seconds
        self.lock = threading.Lock()
        self.running = {}
        self.failures = {}
        self.starting = collections.defaultdict(threading.Lock)
        self.reaper = None

    def get(self, server, values, *, deadline=None):
        deadline = time.monotonic() + SESSION_TIMEOUT if deadline is None else deadline
        candidate = LocalServer(server, values)
        with self.lock:
            starting = self.starting[server["id"]]
        if not starting.acquire(timeout=max(0, deadline - time.monotonic())):
            raise IntegrationError("mcp-busy", f"{candidate.name} is still starting. Try again shortly.")
        try:
            with self.lock:
                current = self.running.get(server["id"])
                if current and current.alive() and current.fingerprint == candidate.fingerprint:
                    return current
                self.running.pop(server["id"], None)
            if current:
                current.stop("its settings changed")
            try:
                candidate.start(deadline=deadline)
            except IntegrationError:
                self.failures[server["id"]] = candidate.detail() or candidate.stopped_because or ""
                candidate.stop("it failed to start")
                raise
            with self.lock:
                self.running[server["id"]] = candidate
                self.failures.pop(server["id"], None)
                if self.reaper is None:
                    self.reaper = threading.Thread(target=self._reap, daemon=True)
                    self.reaper.start()
            return candidate
        finally:
            starting.release()

    def detail(self, server_id):
        with self.lock:
            current = self.running.get(server_id)
        return current.detail() if current else self.failures.get(server_id, "")

    def status(self, server_id):
        with self.lock:
            current = self.running.get(server_id)
        if current and current.alive():
            return {"running": True, "lastError": ""}
        return {"running": False, "lastError": (current.detail() if current else self.failures.get(server_id, ""))[-600:]}

    def stop(self, server_id, reason="it was stopped"):
        with self.lock:
            current = self.running.pop(server_id, None)
        if current:
            current.stop(reason)

    def keep_only(self, server_ids):
        with self.lock:
            gone = [server_id for server_id in self.running if server_id not in server_ids]
        for server_id in gone:
            self.stop(server_id, "it was turned off")

    def stop_idle(self):
        cutoff = time.monotonic() - self.idle_seconds
        with self.lock:
            idle = [server_id for server_id, current in self.running.items()
                    if current.last_used < cutoff and not current.lock.locked()]
        for server_id in idle:
            self.stop(server_id, "it was not used for a while")

    def _reap(self):
        while True:
            time.sleep(min(30, self.idle_seconds))
            self.stop_idle()


class McpSession:
    """List or call tools on one server: a fresh web session, or the running local server."""

    def __init__(self, server, token, env_values, local_servers, *, timeout=SESSION_TIMEOUT):
        self.server, self.name = server, server["name"]
        self.token, self.env_values, self.local_servers = token, env_values, local_servers
        self.timeout, self.deadline = timeout, None

    def __enter__(self):
        self.deadline = time.monotonic() + min(self.timeout, SESSION_TIMEOUT)
        if self.server["kind"] == "stdio":
            self.channel = self.local_servers.get(self.server, self.env_values, deadline=self.deadline)
        else:
            self.channel = HttpSession(self.server, self.token, deadline=self.deadline).open()
        return self

    def __exit__(self, *_):
        return False

    def request(self, method, params):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise IntegrationError("mcp-timeout", f"{self.name} did not finish within the session deadline.")
        if self.server["kind"] == "stdio":
            return self.channel.request(method, params, timeout=min(CALL_TIMEOUT, remaining))
        return self.channel.request(method, params)

    def tools(self):
        found, cursor, cursors, names = [], None, set(), set()
        for _ in range(10):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
                raise IntegrationError("mcp-protocol", f"{self.name} returned an invalid tool list.")
            for tool in result["tools"]:
                if (not isinstance(tool, dict) or not isinstance(tool.get("name"), str)
                        or not TOOL_NAME.fullmatch(tool["name"]) or tool["name"] in names):
                    raise IntegrationError("mcp-protocol", f"{self.name} returned an invalid tool list.")
                names.add(tool["name"])
                # Tool routing names and schema constraints are protocol, not
                # presentation: do not rewrite a valid identifier/type/enum
                # merely because it coincides with a configured secret.
                found.append(self._tool_presentation(tool))
            cursor = result.get("nextCursor")
            if len(found) > MAX_TOOLS or cursor is not None and len(found) >= MAX_TOOLS:
                raise IntegrationError("mcp-tool-limit", f"{self.name} has more tools than Portal can check in one catalog.")
            if cursor is None:
                return found
            if not isinstance(cursor, str) or not cursor or len(cursor) > 4096 or cursor in cursors:
                raise IntegrationError("mcp-protocol", f"{self.name} returned an invalid tool cursor.")
            cursors.add(cursor)
        raise IntegrationError("mcp-tool-limit", f"{self.name} did not finish its tool list within the page limit.")

    def _tool_presentation(self, tool):
        secrets = (self.token, self.env_values)
        def display(value):
            if isinstance(value, dict):
                return {key: redact_secrets(child, secrets) if key in ("description", "title") and isinstance(child, str)
                        else display(child) for key, child in value.items()}
            if isinstance(value, list):
                return [display(child) for child in value]
            return value
        return display(tool)

    def call(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments}) or {}
        parts = [str(item.get("text", "")) if item.get("type") == "text" else f"[{item.get('type', 'content')} not shown]"
                 for item in result.get("content", []) if isinstance(item, dict)]
        text = redact_secrets("\n".join(parts) or "(no output)", (self.token, self.env_values))
        return clip(text), bool(result.get("isError"))


def describe_tool(tool, previous_policy=None, *, new=False):
    """What the owner sees before turning a tool on.

    On a server's first check, read-only tools start allowed and others ask first. A tool
    that appears on a later check starts off, so a server cannot add an allowed tool behind
    the owner's back. As in the MCP specification, a tool that is not read-only may change
    or delete things unless it says destructiveHint false (it only adds things).
    """
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    read_only = annotations.get("readOnlyHint") is True
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"}
    policy = previous_policy or ("off" if new else "allow" if read_only else "ask")
    return {"name": tool["name"], "title": str(annotations.get("title") or tool.get("title") or "")[:120],
            "description": str(tool.get("description", ""))[:500], "readOnly": read_only,
            "destructive": not read_only and annotations.get("destructiveHint") is not False, "inputSchema": schema,
            "policy": policy, "new": new}
