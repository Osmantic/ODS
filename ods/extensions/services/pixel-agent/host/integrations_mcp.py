"""A small MCP client for Portal integrations: one short session per listing or call.

Speaks JSON-RPC 2.0 to a server reached over Streamable HTTP (one POST per message, the
reply as JSON or as server-sent events) or started as a local command (one JSON message
per line on stdin and stdout). The owner chose the server; nothing here picks one.
"""
import json
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request

from integrations_common import IntegrationError, MAX_RESPONSE_BYTES, NETWORK_TIMEOUT, clip

MCP_PROTOCOL = "2025-03-26"
MAX_TOOLS = 200
TOOL_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class McpSession:
    """Initialize, then list or call tools, then close."""

    def __init__(self, server, token):
        self.server, self.token = server, token
        self.name = server["name"]
        self.counter = 0
        self.session_id = None
        self.process = None

    def __enter__(self):
        if self.server["kind"] == "stdio":
            try:
                self.process = subprocess.Popen(shlex.split(self.server["command"]), stdin=subprocess.PIPE,
                                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                                bufsize=1, start_new_session=True)
            except OSError:
                raise IntegrationError("mcp-unavailable", f"{self.name} could not be started.") from None
        result = self.request("initialize", {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                                             "clientInfo": {"name": "ods-portal", "version": "1"}})
        if not isinstance(result, dict):
            raise IntegrationError("mcp-protocol", f"{self.name} did not answer as an MCP server.")
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return self

    def __exit__(self, *_):
        if self.process:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
        return False

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
            with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT) as response:
                self.session_id = response.headers.get("Mcp-Session-Id") or self.session_id
                return response.read(MAX_RESPONSE_BYTES + 1), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise IntegrationError("mcp-auth", f"{self.name} refused the access token.") from None
            raise IntegrationError("mcp-unavailable", f"{self.name} answered {error.code}.") from None
        except (urllib.error.URLError, OSError):
            raise IntegrationError("mcp-unavailable", f"{self.name} could not be reached at {self.server['url']}.") from None

    def _send(self, message):
        if not self.process:
            self._post(message)
            return
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except OSError:
            raise IntegrationError("mcp-unavailable", f"{self.name} stopped.") from None

    def request(self, method, params):
        self.counter += 1
        message = {"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params}
        reply = self._stdio_reply(message) if self.process else self._http_reply(message)
        if "error" in reply:
            error = reply["error"] if isinstance(reply["error"], dict) else {}
            raise IntegrationError("mcp-error", f"{self.name}: {str(error.get('message', 'request failed'))[:300]}")
        return reply.get("result")

    def _stdio_reply(self, message):
        self._send(message)
        deadline = time.time() + NETWORK_TIMEOUT
        while time.time() < deadline:
            line = self.process.stdout.readline()
            if not line:
                raise IntegrationError("mcp-unavailable", f"{self.name} stopped.")
            try:
                reply = json.loads(line)
            except ValueError:
                continue  # Servers may print other lines; only JSON-RPC replies count.
            if isinstance(reply, dict) and reply.get("id") == message["id"]:
                return reply
        raise IntegrationError("mcp-timeout", f"{self.name} did not answer in time.")

    def _http_reply(self, message):
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
                    return item
        raise IntegrationError("mcp-protocol", f"{self.name} did not answer as an MCP server.")

    def tools(self):
        found, cursor = [], None
        for _ in range(10):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {}) or {}
            found += [tool for tool in result.get("tools", [])
                      if isinstance(tool, dict) and isinstance(tool.get("name"), str) and TOOL_NAME.match(tool["name"])]
            cursor = result.get("nextCursor")
            if not cursor or len(found) >= MAX_TOOLS:
                break
        return found[:MAX_TOOLS]

    def call(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments}) or {}
        parts = [str(item.get("text", "")) if item.get("type") == "text" else f"[{item.get('type', 'content')} not shown]"
                 for item in result.get("content", []) if isinstance(item, dict)]
        return clip("\n".join(parts) or "(no output)"), bool(result.get("isError"))


def describe_tool(tool, previous_policy=None):
    """What the owner sees before turning a tool on; read-only tools start allowed, others ask first.

    As in the MCP specification, a tool that is not read-only may change or delete things
    unless it says destructiveHint false (it only adds things).
    """
    annotations = tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {}
    read_only = annotations.get("readOnlyHint") is True
    schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"}
    return {"name": tool["name"], "title": str(annotations.get("title") or tool.get("title") or "")[:120],
            "description": str(tool.get("description", ""))[:500], "readOnly": read_only,
            "destructive": not read_only and annotations.get("destructiveHint") is not False, "inputSchema": schema,
            "policy": previous_policy or ("allow" if read_only else "ask")}
