"""Pixel Edge — internal OpenAI-compatible proxy.

Routes:
  GET  /health                — unauthenticated liveness
  GET  /preview/<site>/<path> — bearer-auth, immutable preview UDS relay
  GET  /v1/models             — bearer-auth, synthetic listing only
  GET  /v1/activity           — bearer-auth, content-free active-turn count
  GET  /v1/transition         — owner-service-auth, durable gate capability/status
  POST /v1/transition/{acquire,release,recover} — owner-service-auth, bound gate control
  POST /v1/chat/completions   — bearer-auth, model rewrite, SSE passthrough

All other paths/methods return 404 (catch-all).
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import sys
from urllib.parse import quote

from aiohttp import web, ClientSession, UnixConnector, ClientTimeout
from transition_gate import TransitionGate, GateError, strict_json, valid_binding

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_BEARER_TOKEN = os.environ.get("PIXEL_OPENWEBUI_KEY", "")
_SOCKET_PATH = os.environ.get("PIXEL_INGRESS_SOCKET", "/pixel-runtime/pixel-ingress.sock")
_PREVIEW_BEARER_TOKEN = os.environ.get("PIXEL_PREVIEW_PROXY_KEY", "")
_PREVIEW_SOCKET_PATH = os.environ.get(
    "PIXEL_PREVIEW_SOCKET", "/pixel-preview-runtime/http.sock"
)
_ALLOWED_MODELS = ("pixel/default",)
_LISTEN_PORT = int(os.environ.get("PIXEL_EDGE_PORT_INTERNAL", "9595"))

# Header names (case-insensitive) that may be forwarded to upstream.
_SAFE_HEADERS = frozenset({
    "accept",
    "user-agent",
})

# Hop-by-hop headers per RFC 7230.
_HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
})

_MAX_BODY = 2 * 1024 * 1024          # 2 MiB request body
_MAX_CANCEL_BODY = 256
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024  # 2 MiB non-stream response cap
_MAX_CANCEL_RESPONSE_BYTES = 1024
_MAX_PREVIEW_RESPONSE_BYTES = 4 * 1024 * 1024

_CONNECT_TIMEOUT = 5
# The host ingress is capped at 32 minutes. Pixel Edge sits outside that
# trusted boundary, so both its total and no-first-byte budgets allow one
# additional minute before failing closed.
_TOTAL_TIMEOUT = 1980
_SOCK_READ_TIMEOUT = 1980
_MAX_SSE_LINE = 1024 * 1024

_UPSTREAM_REWRITE = "openclaw/default"
_PIXEL_REWRITE = "pixel/default"
_SAFE_CHAT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_PREVIEW_SITE_ID = re.compile(r"^site-[a-f0-9]{24}$")
_PREVIEW_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ARTIFACT_DRAFT_PREFIX = re.compile(
    r"^\s*(?:please\s+)?(?:build|write|draft|document|compose|create|edit|update|"
    r"refactor|implement|generate)\b",
    re.IGNORECASE,
)
_ARTIFACT_NOUN = re.compile(
    r"\b(?:app(?:lication)?|code|config(?:uration)?|documentation|example|file|"
    r"fixture|page|project|readme|script|site|snippet|test|web(?:site|page)?|"
    r"workspace)\b",
    re.IGNORECASE,
)
_RESERVED_ASSISTANT_REPLIES = frozenset({
    "NO_REPLY",
    "No response from OpenClaw.",
})
_SHORT_TEST_MESSAGE = re.compile(
    r"^\s*(?:(?:just\s+)?test(?:ing)?(?:\s+[\w.-]+){0,4}|ping|hello|hi|hey)"
    r"\s*[.!?]*\s*$",
    re.IGNORECASE,
)
_SHORT_TEST_REPLY = (
    "Pixel is online and responding. What would you like me to help with?"
)
_EMPTY_REPLY = (
    "I couldn't produce a useful response to that request. Please try again or "
    "tell me what you'd like me to do differently."
)
_INTERACTIVE_DELIVERY_CONTRACT = (
    "\n\n[ODS Pixel delivery requirement: Answer the owner's complete message above. "
    "If it asks for exact text, copy that full exact text. Do not answer with a "
    "generic acknowledgement. Do not output NO_REPLY.]"
)
_HOST_INSPECTION_SCOPE = re.compile(
    r"\b(?:computer|host|laptop|machine|system)\b",
    re.IGNORECASE,
)
_HOST_INSPECTION_INTENT = re.compile(
    r"\b(?:check|describe|inspect|list|report|show|tell|verify)\b",
    re.IGNORECASE,
)
_WORKSPACE_MUTATION_SCOPE = re.compile(
    r"(?:/workspace(?:/[A-Za-z0-9._/-]+)?|\b(?:directory|directories|file|files)\b)",
    re.IGNORECASE,
)
_WORKSPACE_MUTATION_INTENT = re.compile(
    r"\b(?:add|build|change|create|edit|generate|implement|make|modify|patch|"
    r"save|update|write)\b",
    re.IGNORECASE,
)
_RUN_COMMAND_AND_WAIT = re.compile(
    r"\b(?:run|execute)\b[\s\S]{0,512}\bcommand\b[\s\S]{0,4096}"
    r"\b(?:wait|finish|complete|terminal|real\s+result)\b",
    re.IGNORECASE,
)
_EXACT_SINGLE_LINE_FILE = re.compile(
    r"\bcreate\s+(?:the\s+)?file\s+"
    r"(?P<path>(?:/workspace/)?[A-Za-z0-9_-][A-Za-z0-9._-]*"
    r"(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*)"
    r"(?:\s+in\s+(?:the|your)\s+writable\s+workspace)?"
    r"\s+with\s+exactly\s+this\s+"
    r"single\s+line\s+followed\s+by\s+(?:a|one)\s+newline:\s*"
    r"(?P<content>[^\r\n]{1,4096}?)(?=\s+Then\b)",
    re.IGNORECASE,
)
_WORKSPACE_MUTATION_ROUTE = (
    "\n[ODS Pixel workspace task route: Perform the requested workspace mutation "
    "before verification. When tool_call is visible, use it with id write and normal "
    "write args for every new file. edit cannot create a file and requires a non-empty "
    "oldText copied from an existing file. Use edit or apply_patch only after reading "
    "an existing file. Then use exec only for "
    "readback, tests, or the requested digest. Do not repeatedly list directories "
    "or hash proposed text instead of the created file.]"
)
_RUN_COMMAND_AND_WAIT_ROUTE = (
    "\n[ODS Pixel command completion route: Call exec exactly once for the owner's "
    "command. If exec returns a running process session, do not call exec again. "
    "Use the visible tool_call control with id process and args containing action "
    "poll plus that exact returned sessionId, and keep polling only that session "
    "until its terminal output is available. Report the real terminal output; do "
    "not simulate, shorten, or restart the command.]"
)
_HOST_ACTION_RULES = (
    (re.compile(r"\b(?:operating\s+system|os|platform)\b", re.IGNORECASE), "host.os-release"),
    (re.compile(r"\bkernel\b", re.IGNORECASE), "host.kernel"),
    (re.compile(r"\b(?:memory|ram)\b", re.IGNORECASE), "host.memory"),
    (re.compile(r"\b(?:disks?|filesystem|mount(?:ed|s)?|storage)\b", re.IGNORECASE), "host.storage"),
    (re.compile(r"\bprocess(?:es)?\b", re.IGNORECASE), "host.processes"),
    (re.compile(r"\b(?:cpu|processor|architecture)\b", re.IGNORECASE), "host.cpu"),
    (re.compile(r"\b(?:gpu|graphics(?:\s+(?:card|processor))?|video\s+card)\b", re.IGNORECASE), "host.gpu"),
    (re.compile(r"\b(?:hostname|identity)\b", re.IGNORECASE), "host.identity"),
    (re.compile(r"\buptime\b", re.IGNORECASE), "host.uptime"),
    (re.compile(r"\bservices?\b", re.IGNORECASE), "host.services"),
    (re.compile(r"\b(?:listening\s+ports?|open\s+ports?)\b", re.IGNORECASE), "host.listening-ports"),
    (re.compile(r"\b(?:network\s+addresses?|ip\s+addresses?)\b", re.IGNORECASE), "host.network-addresses"),
    (re.compile(r"\b(?:network\s+routes?|routing\s+table)\b", re.IGNORECASE), "host.network-routes"),
    (re.compile(r"\btailscale\b", re.IGNORECASE), "host.tailscale"),
)
_NETWORK_DISCLOSURE_EXCLUSION = re.compile(
    r"\b(?:do\s+not|don't|never|must\s+not|should\s+not)\s+"
    r"(?:include|report|show|list|reveal|disclose|expose)\b"
    r"[^.!?;\n]{0,120}\b(?:network(?:\s+(?:location|details?))?|"
    r"interfaces?|addresses?|ip\s+addresses?)\b",
    re.IGNORECASE,
)
_ADDRESS_BEARING_HOST_ACTIONS = {
    "host.network-addresses",
    "host.network-routes",
    "host.listening-ports",
}
_CANCEL_EVENTS_KEY = web.AppKey("pixel_cancel_events", dict)
_CHAT_ACTIVITY_KEY = web.AppKey("pixel_chat_activity", dict)
_ACTIVE_REQUESTS_KEY = web.AppKey("pixel_active_requests", set)
_TRANSITION_GATE_KEY = web.AppKey("pixel_transition_gate", TransitionGate)


def _validate_config() -> str:
    """Return the raw bearer token after validating it is well-formed.

    Exits with a non-zero code if the token is missing/blank/oversized.
    """
    if not _BEARER_TOKEN or _BEARER_TOKEN != _BEARER_TOKEN.strip():
        print("FATAL: PIXEL_OPENWEBUI_KEY is not set or blank", file=sys.stderr)
        sys.exit(1)
    if len(_BEARER_TOKEN) < 32 or len(_BEARER_TOKEN) > 4096 or any(ord(ch) < 33 for ch in _BEARER_TOKEN):
        print("FATAL: PIXEL_OPENWEBUI_KEY has an invalid length or character", file=sys.stderr)
        sys.exit(1)
    return _BEARER_TOKEN


def _validate_preview_config() -> str:
    if (
        not _PREVIEW_BEARER_TOKEN
        or _PREVIEW_BEARER_TOKEN != _PREVIEW_BEARER_TOKEN.strip()
        or len(_PREVIEW_BEARER_TOKEN) < 32
        or len(_PREVIEW_BEARER_TOKEN) > 4096
        or any(ord(ch) < 33 for ch in _PREVIEW_BEARER_TOKEN)
    ):
        print("FATAL: PIXEL_PREVIEW_PROXY_KEY is invalid", file=sys.stderr)
        sys.exit(1)
    return _PREVIEW_BEARER_TOKEN


config_token = _validate_config()
preview_proxy_token = _validate_preview_config()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8", "replace"),
                               b.encode("utf-8", "replace"))


def _check_auth(request: web.Request):
    """Return a 401 Response on failure, or None on success."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return web.json_response({"error": "unauthorized"}, status=401)
    token = auth[len("Bearer "):]
    if not _constant_time_compare(token, config_token):
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


def _check_preview_auth(request: web.Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return web.json_response({"error": "unauthorized"}, status=401)
    token = auth[len("Bearer "):]
    if not _constant_time_compare(token, preview_proxy_token):
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


def _sanitize_headers(headers: dict) -> dict:
    """Strip blocked/hop-by-hop headers and anything not on the safe list."""
    out = {}
    for name, value in headers.items():
        low = name.lower()
        if low.startswith("x-openclaw-") or low in _HOP_BY_HOP or low not in _SAFE_HEADERS:
            continue
        out[name] = value
    return out


def _rewrite_json_model(raw: bytes) -> bytes:
    """Rewrite exact JSON ``model`` fields without altering assistant text."""
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw

    def _walk(obj):
        if isinstance(obj, list):
            return [_walk(v) for v in obj]
        if isinstance(obj, dict):
            return {
                k: (_PIXEL_REWRITE if k == "model" and v == _UPSTREAM_REWRITE else _walk(v))
                for k, v in obj.items()
            }
        return obj

    return json.dumps(_walk(parsed)).encode("utf-8")


def _latest_user_text(data: dict) -> str:
    messages = data.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message_text(message.get("content")).strip()
    return ""


def _empty_reply_fallback(data: dict) -> str:
    text = _latest_user_text(data)
    if len(text) <= 160 and _SHORT_TEST_MESSAGE.fullmatch(text):
        return _SHORT_TEST_REPLY
    return _EMPTY_REPLY


def _positive_host_inspection_clauses(text: str) -> list[str]:
    """Select requested host facts without reviving excluded or historical ones."""
    if not _HOST_INSPECTION_SCOPE.search(text):
        return []
    clauses = re.split(
        r"[.!?;\n]+|,\s*(?=(?:and\s+then|but|however|instead|then)\b)|"
        r"\b(?:but|however|instead)\s+|"
        r"\band\s+(?=(?:do\s+not|don['\u2019]t|never|avoid|skip|omit|exclude)\b)",
        text,
        flags=re.IGNORECASE,
    )
    negation = re.compile(
        r"^\s*(?:(?:and|but|then)\s+)?(?:please\s+)?"
        r"(?:do\s+not|don['\u2019]t|never|must\s+not|should\s+not|"
        r"avoid|skip|omit|exclude|no)\b",
        re.IGNORECASE,
    )
    return [
        clause for clause in clauses
        if not negation.search(clause)
        and _HOST_INSPECTION_INTENT.search(clause)
        and (_HOST_INSPECTION_SCOPE.search(clause)
             or any(pattern.search(clause) for pattern, _ in _HOST_ACTION_RULES))
    ]


def _workspace_mutation_positions(text: str) -> set[int]:
    """Find positive owner directives for coaching, not tool authorization."""
    def mask_content(match):
        value = match.group(0)
        # A quoted destination is still a path; other quoted bytes are data.
        if re.fullmatch(r"['\"`](/workspace(?:/[A-Za-z0-9._/-]+)?)['\"`]", value):
            return " " + value[1:-1] + " "
        return "".join("\n" if char == "\n" else " " for char in value)

    instructions = re.sub(
        r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|(?m:^\s*>[^\n]*)|"
        r"`[^`\n]*`|\"(?:\\.|[^\"\\])*(?:\"|$)|"
        r"(?<!\w)'(?:\\.|[^'\\])*(?:'|$)|"
        r"\u201c[^\u201d]*(?:\u201d|$)|(?<!\w)\u2018[^\u2019]*(?:\u2019|$)",
        mask_content, text,
    )
    if not _WORKSPACE_MUTATION_SCOPE.search(instructions):
        return set()
    negated = re.compile(
        r"^\s*(?:please\s+)?(?:do\s+not|don['\u2019]t|never|must\s+not|"
        r"should\s+not|avoid|skip|omit|exclude|no)\b", re.IGNORECASE,
    )
    directive = re.compile(
        r"(?:^|\band\s+)(?:(?:please|now|first|next|also)\s+)*"
        r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
        r"I\s+(?:want|need)\s+you\s+to\s+|you\s+(?:may|can|should|must)\s+)?"
        r"(?P<verb>" + _WORKSPACE_MUTATION_INTENT.pattern + r")", re.IGNORECASE,
    )
    positions = set()
    for clause in re.finditer(
        r"(?:^|[.!?;\n]|\b(?:but|however|instead|then)\b)\s*"
        r"(?P<clause>.*?)(?=$|[.!?;\n]|\b(?:but|however|instead|then)\b)",
        instructions, re.IGNORECASE,
    ):
        value = clause.group("clause")
        if not negated.search(value):
            positions.update(clause.start("clause") + match.start("verb")
                             for match in directive.finditer(value))
    return positions


def _with_interactive_delivery_contract(data: dict) -> dict:
    """Append the ODS delivery contract to the latest user content.

    OpenClaw's generic system prompt documents ``NO_REPLY`` for asynchronous
    channel turns. Small local models can over-select that sentinel even though
    Pixel's later system overlay forbids it for owner-authored webchat. Keeping
    the correction adjacent to the current user instruction gives every model
    the same first-turn contract without retrying (and potentially repeating)
    a tool-using operation.
    """
    owner_text = _latest_user_text(data)
    contract = _INTERACTIVE_DELIVERY_CONTRACT
    host_clauses = _positive_host_inspection_clauses(owner_text)
    remote_inspection = re.search(
        r"\b(?:reachability|connectivity|ping|probe|resolve|network[ -]peer|"
        r"remote[ -](?:host|hostname|identity)|(?:LAN|network)\s+target)\b",
        owner_text, re.IGNORECASE,
    ) and re.search(r"\b(?:check|inspect|test|verify|ping|probe|resolve)\b", owner_text, re.IGNORECASE) and re.search(
        r"\b(?:LAN|network|SSH|Tailscale|remote|reachability|connectivity)\b", owner_text, re.IGNORECASE,
    )
    if remote_inspection:
        # Guidance is not endpoint authorization. The runtime binds the owner
        # target and permissions. Do not prescribe a local identity receipt as
        # a substitute for a remote operation or force one tool-call sequence.
        contract += (
            "\n[ODS Pixel network inspection route: Discover the available "
            "Operations tools using tool_call. Use host.network-peer for bounded "
            "reachability of the owner's explicit endpoint and requested ports. "
            "Keep remote reachability, protocol banners and authenticated remote "
            "identity distinct. A local host identity receipt does not establish "
            "remote identity. Honor exclusions and resolve ambiguous targets "
            "before contact. Complete independently requested local observations "
            "and workspace files in the order the task needs.]"
        )
    elif (
        host_clauses
        and not (_ARTIFACT_DRAFT_PREFIX.search(owner_text) and _ARTIFACT_NOUN.search(owner_text))
    ):
        positive_host_text = " ".join(host_clauses)
        excludes_network_location = bool(_NETWORK_DISCLOSURE_EXCLUSION.search(owner_text))
        actions = [
            action
            for pattern, action in _HOST_ACTION_RULES
            if pattern.search(positive_host_text)
            and not (excludes_network_location and action in _ADDRESS_BEARING_HOST_ACTIONS)
        ]
        route = (
            "\n[ODS Pixel host inspection route: Generic sandbox commands and "
            "status projections cannot establish host facts. Use the visible "
            "tool_call Tool Search control for the deferred Operations tools. "
        )
        if actions:
            observe_args = json.dumps({"actions": actions}, separators=(",", ":"))
            route += (
                "Use tool_call with id pixel_ods_host_observe "
                f"and args {observe_args}. This one read-only tool returns the "
                "terminal Operations receipt. "
            )
        else:
            route += (
                "Call tool_call with id pixel_ops_inventory and args {} to select "
                "the matching read-only ods-host actions. "
            )
        route += (
            "After terminal host evidence, continue any separately required ODS "
            "projection or workspace step before answering. Do not use generic "
            "sandbox commands as host evidence.]"
        )
        contract += route
    mutation_positions = _workspace_mutation_positions(owner_text)
    if mutation_positions:
        exact_file = next((match for match in _EXACT_SINGLE_LINE_FILE.finditer(owner_text)
                           if match.start() in mutation_positions), None)
        if exact_file:
            path = exact_file.group("path")
            content = exact_file.group("content") + "\n"
            write_args = json.dumps(
                {"path": path, "content": content}, separators=(",", ":")
            )
            read_args = json.dumps({"path": path}, separators=(",", ":"))
            digest_args = json.dumps(
                {
                    "command": f"sha256sum -- {path}",
                    "workdir": "/workspace",
                },
                separators=(",", ":"),
            )
            contract += (
                "\n[ODS Pixel exact workspace route: Use the visible tool_call control. "
                "Call tool_call exactly once with id write and args "
                f"{write_args}. After it succeeds, call tool_call once with id read and "
                f"args {read_args}, then call tool_call once with id exec and args "
                f"{digest_args}. Report only the verified created file; do not hash "
                "proposed text or omit the encoded trailing newline.]"
            )
        else:
            contract += _WORKSPACE_MUTATION_ROUTE
    if _RUN_COMMAND_AND_WAIT.search(owner_text):
        contract += _RUN_COMMAND_AND_WAIT_ROUTE

    messages = data.get("messages")
    if not isinstance(messages, list):
        return data
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            if contract in content:
                return data
            updated_content = content + contract
        elif isinstance(content, list):
            if any(
                isinstance(part, dict)
                and part.get("type") == "text"
                and contract in str(part.get("text", ""))
                for part in content
            ):
                return data
            updated_content = [
                *content,
                {"type": "text", "text": contract.lstrip()},
            ]
        else:
            return data
        updated_messages = list(messages)
        updated_message = dict(message)
        updated_message["content"] = updated_content
        updated_messages[index] = updated_message
        updated = dict(data)
        updated["messages"] = updated_messages
        return updated
    return data


def _reserved_reply(value) -> bool:
    return isinstance(value, str) and (
        not value.strip() or value.strip() in _RESERVED_ASSISTANT_REPLIES
    )


def _rewrite_json_response(raw: bytes, fallback: str) -> bytes:
    """Rewrite model identity and replace only an exact reserved/empty final reply."""
    try:
        parsed = json.loads(_rewrite_json_model(raw))
    except (json.JSONDecodeError, ValueError):
        return raw
    choices = parsed.get("choices") if isinstance(parsed, dict) else None
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict) and _reserved_reply(message.get("content")):
                message["content"] = fallback
    return json.dumps(parsed).encode("utf-8")


def _sse_event(line: bytes):
    if not line.startswith(b"data: ") or line == b"data: [DONE]":
        return None, None, None
    try:
        event = json.loads(line[6:])
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None, None, None
    choices = event.get("choices") if isinstance(event, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return event, None, None
    choice = choices[0]
    delta = choice.get("delta")
    content = delta.get("content") if isinstance(delta, dict) else None
    return event, content if isinstance(content, str) else None, choice.get("finish_reason")


def _fallback_sse_line(template: dict, fallback: str, *, finished: bool) -> bytes:
    event = {
        key: template[key]
        for key in ("id", "object", "created", "model")
        if key in template
    }
    event["choices"] = [{
        "index": 0,
        "delta": {} if finished else {"content": fallback},
        "finish_reason": "stop" if finished else None,
    }]
    return b"data: " + json.dumps(event).encode("utf-8")


async def _read_bounded(content, limit: int) -> bytes:
    chunks = []
    total = 0
    async for chunk in content.iter_any():
        total += len(chunk)
        if total > limit:
            raise ValueError("response too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _message_text(content) -> str:
    """Extract text from one OpenAI-compatible message content value."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if not isinstance(part, dict):
            continue
        value = part.get("text")
        if not isinstance(value, str):
            value = part.get("content")
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

async def _ingress_ready() -> bool:
    connector = UnixConnector(path=_SOCKET_PATH)
    timeout = ClientTimeout(total=3, sock_connect=2, sock_read=2)
    try:
        async with ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get("http://pixel-upstream/health") as resp:
                if resp.status != 200:
                    raise ConnectionError("ingress not ready")
                body = await resp.json(content_type=None)
                if body != {"status": "ok"}:
                    raise ConnectionError("ingress not ready")
    except Exception:
        return False
    return True


async def handle_health(_request: web.Request):
    """Report ready only when the private host ingress and gateway are ready."""
    if not await _ingress_ready():
        return web.json_response({"status": "unavailable"}, status=503)
    return web.json_response({"status": "ok"})


async def handle_models(request: web.Request):
    fail = _check_auth(request)
    # aiohttp Response objects may be falsey, including an intentional 401.
    # Check the sentinel explicitly or an unauthenticated request can fall
    # through to the protected handler.
    if fail is not None:
        return fail
    if not await _ingress_ready():
        return web.json_response({"error": "service unavailable"}, status=503)

    data = [{"id": m, "object": "model", "owned_by": "pixel"} for m in _ALLOWED_MODELS]
    return web.json_response({"object": "list", "data": data})


async def handle_activity(request: web.Request):
    """Return only the number of in-flight turns for safe model coordination."""
    fail = _check_auth(request)
    if fail is not None:
        return fail
    streams = len(request.app[_ACTIVE_REQUESTS_KEY])
    return web.json_response({"active": streams > 0, "streams": streams})


async def handle_chat_activity(request: web.Request):
    """Project only the requested opaque chat's witnessed lifecycle."""
    fail = _check_auth(request)
    if fail is not None:
        return fail
    if request.content_type != "application/json":
        return web.json_response({"error": "Content-Type must be application/json"}, status=415)
    if request.query_string or (request.content_length and request.content_length > _MAX_CANCEL_BODY):
        return web.json_response({"error": "invalid activity request"}, status=400)
    try:
        raw = await request.read()
        if len(raw) > _MAX_CANCEL_BODY:
            raise ValueError("request too large")
        data = strict_json(raw)
        if (not isinstance(data, dict) or set(data) != {"user"}
                or not isinstance(data["user"], str) or not _SAFE_CHAT_ID.fullmatch(data["user"])):
            raise ValueError("invalid activity request")
    except (ValueError, UnicodeDecodeError, TypeError):
        return web.json_response({"error": "invalid activity request"}, status=400)
    chat_id = data["user"]
    state = "active" if request.app[_CANCEL_EVENTS_KEY].get(chat_id) else request.app[_CHAT_ACTIVITY_KEY].get(chat_id, "unknown")
    return web.json_response({"state": state}, headers={"Cache-Control": "no-store"})


def _finish_chat_activity(app, chat_id, cancel_event, terminal):
    active = app[_CANCEL_EVENTS_KEY].get(chat_id)
    if active is None:
        return
    active.discard(cancel_event)
    history = app[_CHAT_ACTIVITY_KEY]
    # One uncertain sibling means this batch cannot be called terminal.
    state = "unknown" if not terminal or history.get(chat_id) == "unknown" else "terminal"
    if not active:
        app[_CANCEL_EVENTS_KEY].pop(chat_id, None)
    _remember_chat_activity(app, chat_id, state)


def _remember_chat_activity(app, chat_id, state):
    history = app[_CHAT_ACTIVITY_KEY]
    history[chat_id] = state
    # No chat content or request IDs persist; eviction/restart means unknown.
    for previous in tuple(history):
        if len(history) <= 1024:
            break
        if previous not in app[_CANCEL_EVENTS_KEY]:
            history.pop(previous, None)


async def handle_transition(request: web.Request):
    # Reuse the server-injected Dashboard credential, never the model/chat key.
    # This credential already authenticates the private preview relay and must
    # remain inaccessible to browsers, model text, and generated artifacts.
    fail = _check_preview_auth(request)
    if fail is not None:
        return fail
    if request.query_string:
        return web.json_response({"error": "query parameters are not allowed"}, status=400)
    gate = request.app[_TRANSITION_GATE_KEY]
    if request.method in ("GET", "HEAD"):
        return web.json_response(await gate.status(), headers={"Cache-Control": "no-store"})
    if request.content_type != "application/json":
        return web.json_response({"error": "Content-Type must be application/json"}, status=415)
    raw = bytearray()
    try:
        async for chunk in request.content.iter_chunked(512):
            raw.extend(chunk)
            if len(raw) > 1024:
                return web.json_response({"error": "request too large"}, status=413)
        data = strict_json(raw)
    except (ValueError, OSError, RecursionError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if (not isinstance(data, dict) or set(data) != {"token", "revision"}
            or not all(valid_binding(value) for value in data.values())):
        return web.json_response({"error": "exact token and revision bindings required"}, status=400)
    try:
        operation = request.match_info["operation"]
        if operation == "release":
            result = await gate.release(data["token"], data["revision"])
        else:
            result = await gate.acquire(data["token"], data["revision"], recover=operation == "recover")
    except GateError as exc:
        return web.json_response({"error": exc.reason}, status=exc.status,
                                 headers={"Cache-Control": "no-store"})
    return web.json_response(result, headers={"Cache-Control": "no-store"})


async def handle_chat_completions(request: web.Request):
    fail = _check_auth(request)
    if fail is not None:
        return fail

    if request.content_type != "application/json":
        return web.json_response({"error": "Content-Type must be application/json"},
                                 status=415)

    if request.content_length and request.content_length > _MAX_BODY:
        return web.json_response({"error": "request too large"}, status=413)

    try:
        body = await request.read()
    except Exception:
        return web.json_response({"error": "bad request"}, status=400)

    if len(body) > _MAX_BODY:
        return web.json_response({"error": "request too large"}, status=413)

    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)

    if not isinstance(data, dict):
        return web.json_response({"error": "JSON object required"}, status=400)

    req_model = data.get("model", "")
    if req_model not in _ALLOWED_MODELS:
        return web.json_response({"error": "model not allowed"}, status=400)
    empty_reply_fallback = _empty_reply_fallback(data)
    upstream_data = _with_interactive_delivery_contract(data)
    # Let the selected model's runtime/profile supply omitted sampling values.
    # A universal greedy override can defeat the model's recommended settings
    # and cause repetitive generations. Explicit client settings pass through;
    # tool authorization remains the responsibility of the broker and harness.
    request_token = object()
    chat_id = data.get("user")
    cancel_event = None
    activity = {"terminal": False}
    upstream_data["model"] = _UPSTREAM_REWRITE

    fwd_headers = _sanitize_headers(dict(request.headers))
    fwd_headers["Content-Type"] = "application/json"

    try:
        await request.app[_TRANSITION_GATE_KEY].admit(request_token)
    except GateError as exc:
        return web.json_response({"error": exc.reason}, status=exc.status)
    try:
        if isinstance(chat_id, str) and _SAFE_CHAT_ID.fullmatch(chat_id):
            cancel_event = asyncio.Event()
            if not request.app[_CANCEL_EVENTS_KEY].get(chat_id):
                request.app[_CHAT_ACTIVITY_KEY].pop(chat_id, None)
            request.app[_CANCEL_EVENTS_KEY].setdefault(chat_id, set()).add(cancel_event)
        # The gateway knows the owner's enabled tools and their network policy.
        # A URL in chat is not an outbound fetch by this transport. Let an
        # authorized browser handle local previews; public-fetch restrictions
        # and the unavailable-browser response remain enforced by the harness.
        connector = UnixConnector(path=_SOCKET_PATH)
        timeout = ClientTimeout(total=_TOTAL_TIMEOUT,
                                sock_connect=_CONNECT_TIMEOUT,
                                sock_read=_SOCK_READ_TIMEOUT)
        async with ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post("http://pixel-upstream/v1/chat/completions",
                                    json=upstream_data, headers=fwd_headers) as resp:
                ctype = resp.headers.get("Content-Type", "").lower()

                if resp.status >= 400:
                    status = 400 if 400 <= resp.status < 500 else 502
                    return web.json_response({"error": "pixel request rejected"}, status=status)

                if "text/event-stream" in ctype:
                    return await _stream_upstream(
                        request, resp, empty_reply_fallback, cancel_event, activity
                    )

                if "application/json" not in ctype:
                    return web.json_response({"error": "invalid upstream response"}, status=502)

                # Non-streaming: cap bytes, then rewrite model identifiers.
                resp_body = bytearray()
                async for chunk in resp.content.iter_any():
                    resp_body.extend(chunk)
                    if len(resp_body) > _MAX_RESPONSE_BYTES:
                        return web.json_response({"error": "upstream response too large"},
                                                 status=502)

                rewritten = _rewrite_json_response(bytes(resp_body), empty_reply_fallback)
                activity["terminal"] = True
                return web.Response(status=resp.status, body=rewritten,
                                    content_type="application/json")
    except (ConnectionError, OSError, asyncio.TimeoutError):
        return web.json_response({"error": "service unavailable"}, status=502)
    except Exception:
        return web.json_response({"error": "bad gateway"}, status=502)
    finally:
        await request.app[_TRANSITION_GATE_KEY].finish(request_token)
        if cancel_event is not None:
            _finish_chat_activity(request.app, chat_id, cancel_event,
                                  activity["terminal"] or cancel_event.is_set())


async def handle_chat_cancel(request: web.Request):
    """Relay one authenticated, bounded cancellation to the private ingress."""
    fail = _check_auth(request)
    if fail is not None:
        return fail
    if request.content_type != "application/json":
        return web.json_response({"error": "Content-Type must be application/json"},
                                 status=415)
    if request.content_length and request.content_length > _MAX_CANCEL_BODY:
        return web.json_response({"error": "request too large"}, status=413)

    try:
        raw = await request.read()
    except Exception:
        return web.json_response({"error": "bad request"}, status=400)
    if len(raw) > _MAX_CANCEL_BODY:
        return web.json_response({"error": "request too large"}, status=413)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": "invalid JSON"}, status=400)
    if (
        not isinstance(data, dict)
        or set(data) != {"user"}
        or not isinstance(data.get("user"), str)
        or not _SAFE_CHAT_ID.fullmatch(data["user"])
    ):
        return web.json_response({"error": "invalid cancellation request"}, status=400)

    connector = UnixConnector(path=_SOCKET_PATH)
    timeout = ClientTimeout(total=6, sock_connect=2, sock_read=5)
    try:
        async with ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(
                "http://pixel-upstream/v1/chat/cancel",
                json={"user": data["user"]},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            ) as resp:
                if resp.status != 200:
                    return web.json_response({"error": "pixel cancellation failed"}, status=502)
                if "application/json" not in resp.headers.get("Content-Type", "").lower():
                    return web.json_response({"error": "pixel cancellation failed"}, status=502)
                raw_result = await _read_bounded(resp.content, _MAX_CANCEL_RESPONSE_BYTES)
                result = json.loads(raw_result)
                if (
                    not isinstance(result, dict)
                    or set(result) != {"aborted"}
                    or not isinstance(result.get("aborted"), bool)
                ):
                    return web.json_response({"error": "pixel cancellation failed"}, status=502)
                if result["aborted"]:
                    active = request.app[_CANCEL_EVENTS_KEY].get(data["user"], ())
                    for event in tuple(active):
                        event.set()
                    if not active:
                        # A native run can outlive the disconnected edge stream.
                        # Only its exact cancellation acknowledgement resolves it.
                        _remember_chat_activity(request.app, data["user"], "terminal")
                return web.json_response({"aborted": result["aborted"]})
    except (ConnectionError, OSError, asyncio.TimeoutError, json.JSONDecodeError, ValueError):
        return web.json_response({"error": "pixel cancellation failed"}, status=502)
    except Exception:
        return web.json_response({"error": "pixel cancellation failed"}, status=502)


async def _stream_upstream(
    request: web.Request,
    resp,
    empty_reply_fallback: str,
    cancel_event: asyncio.Event | None = None,
    activity: dict | None = None,
):
    """Stream bounded SSE while replacing only a reserved/empty final reply."""
    response = web.StreamResponse(
        status=resp.status,
        headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
    )
    await response.prepare(request)
    buffered = bytearray()
    pending = []
    pending_text = ""
    passthrough = False

    async def flush_pending():
        nonlocal pending
        for item in pending:
            await response.write(item[0] + b"\n")
        pending = []

    async def replace_pending(template: dict, *, synthesize_finish: bool):
        nonlocal pending
        # Preserve role/metadata events, but never expose the reserved text.
        for line, _event, content, finish_reason in pending:
            if content is None and finish_reason is None:
                await response.write(line + b"\n")
        await response.write(
            _fallback_sse_line(template, empty_reply_fallback, finished=False) + b"\n"
        )
        if synthesize_finish:
            await response.write(
                _fallback_sse_line(template, empty_reply_fallback, finished=True) + b"\n"
            )
        pending = []

    try:
        async for chunk in resp.content.iter_any():
            buffered.extend(chunk)
            if len(buffered) > _MAX_SSE_LINE and b"\n" not in buffered:
                raise ValueError("SSE line exceeded limit")
            while b"\n" in buffered:
                line, _, remainder = buffered.partition(b"\n")
                buffered = bytearray(remainder)
                if cancel_event is not None and cancel_event.is_set():
                    pending = []
                    await response.write(b"data: [DONE]\n\n")
                    passthrough = True
                    return response
                if len(line) > _MAX_SSE_LINE:
                    raise ValueError("SSE line exceeded limit")
                # Only the real upstream terminator is lifecycle evidence;
                # locally synthesized error/fallback frames do not prove it.
                if line.rstrip(b"\r") == b"data: [DONE]" and activity is not None:
                    activity["terminal"] = True
                if line.startswith(b"data: ") and line != b"data: [DONE]":
                    line = b"data: " + _rewrite_json_model(line[6:])
                if passthrough:
                    await response.write(line + b"\n")
                    continue

                if line == b"data: [DONE]":
                    normalized = pending_text.strip()
                    if not normalized or normalized in _RESERVED_ASSISTANT_REPLIES:
                        template = next(
                            (item[1] for item in reversed(pending) if isinstance(item[1], dict)),
                            {"model": _PIXEL_REWRITE},
                        )
                        await replace_pending(template, synthesize_finish=True)
                    else:
                        await flush_pending()
                    await response.write(line + b"\n")
                    passthrough = True
                    continue

                event, content, finish_reason = _sse_event(line)
                if isinstance(event, dict) and "error" in event:
                    await flush_pending()
                    await response.write(line + b"\n")
                    passthrough = True
                    continue
                if finish_reason is not None:
                    normalized = pending_text.strip()
                    if not normalized or normalized in _RESERVED_ASSISTANT_REPLIES:
                        template = event if isinstance(event, dict) else next(
                            (item[1] for item in reversed(pending) if isinstance(item[1], dict)),
                            {"model": _PIXEL_REWRITE},
                        )
                        await replace_pending(template, synthesize_finish=False)
                    else:
                        await flush_pending()
                    await response.write(line + b"\n")
                    passthrough = True
                    continue

                pending.append((line, event, content, finish_reason))
                if content is not None:
                    pending_text += content
                    normalized = pending_text.strip()
                    if normalized and not any(
                        reserved.startswith(normalized)
                        for reserved in _RESERVED_ASSISTANT_REPLIES
                    ):
                        await flush_pending()
                        passthrough = True
        if cancel_event is not None and cancel_event.is_set():
            pending = []
            await response.write(b"data: [DONE]\n\n")
            return response
        if buffered:
            if len(buffered) > _MAX_SSE_LINE:
                raise ValueError("SSE line exceeded limit")
            line = bytes(buffered)
            if line.rstrip(b"\r") == b"data: [DONE]" and activity is not None:
                activity["terminal"] = True
            if line.startswith(b"data: ") and line != b"data: [DONE]":
                line = b"data: " + _rewrite_json_model(line[6:])
            if passthrough:
                await response.write(line)
            else:
                event, content, finish_reason = _sse_event(line)
                pending.append((line, event, content, finish_reason))
        if not passthrough and pending:
            normalized = pending_text.strip()
            if not normalized or normalized in _RESERVED_ASSISTANT_REPLIES:
                template = next(
                    (item[1] for item in reversed(pending) if isinstance(item[1], dict)),
                    {"model": _PIXEL_REWRITE},
                )
                await replace_pending(template, synthesize_finish=False)
            else:
                await flush_pending()
    except (ConnectionError, OSError, asyncio.TimeoutError):
        if cancel_event is not None and cancel_event.is_set():
            await response.write(b"data: [DONE]\n\n")
        else:
            await response.write(b'data: {"error":"upstream error"}\n\ndata: [DONE]\n\n')
    except Exception:
        if cancel_event is not None and cancel_event.is_set():
            await response.write(b"data: [DONE]\n\n")
        else:
            await response.write(b'data: {"error":"upstream error"}\n\ndata: [DONE]\n\n')
    return response


_REMOTE_PREVIEW_CSP = (
    "sandbox allow-scripts allow-forms allow-downloads; default-src 'self' data: blob:; "
    "connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
    "font-src 'self' data:; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'self'"
)


def _preview_upstream_path(site_id: str, tail: str) -> str | None:
    if _PREVIEW_SITE_ID.fullmatch(site_id) is None:
        return None
    if not tail:
        return f"/{site_id}/"
    parts = tail.split("/")
    if any(_PREVIEW_PATH_COMPONENT.fullmatch(part) is None for part in parts):
        return None
    encoded = "/".join(quote(part, safe="") for part in parts)
    return f"/{site_id}/{encoded}"


async def handle_preview(request: web.Request):
    """Relay one immutable host snapshot without exposing its host listener."""
    fail = _check_preview_auth(request)
    if fail is not None:
        return fail
    upstream_path = _preview_upstream_path(
        request.match_info.get("site_id", ""), request.match_info.get("tail", "")
    )
    if upstream_path is None:
        return web.json_response({"error": "not found"}, status=404)

    connector = UnixConnector(path=_PREVIEW_SOCKET_PATH)
    timeout = ClientTimeout(total=10, sock_connect=3, sock_read=5)
    try:
        async with ClientSession(connector=connector, timeout=timeout) as session:
            async with session.request(
                "GET",
                f"http://pixel-preview.internal{upstream_path}",
                headers={"Host": "pixel-preview.internal"},
            ) as upstream:
                if upstream.status not in {200, 404}:
                    return web.json_response({"error": "preview unavailable"}, status=502)
                # read(n) may return only the first available transport chunk.
                # Verify the complete bounded snapshot, including for client HEAD.
                try:
                    body = await _read_bounded(upstream.content, _MAX_PREVIEW_RESPONSE_BYTES)
                except ValueError:
                    return web.json_response({"error": "preview too large"}, status=502)
                if upstream.status == 404:
                    return web.json_response({"error": "not found"}, status=404)
                content_type = upstream.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                content_length = upstream.headers.get("Content-Length", str(len(body)))
                digest = upstream.headers.get("X-Preview-SHA256", "")
                if (
                    not content_length.isdigit()
                    or int(content_length) > _MAX_PREVIEW_RESPONSE_BYTES
                    or int(content_length) != len(body)
                    or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or not hmac.compare_digest(
                        hashlib.sha256(body).hexdigest(), digest
                    )
                ):
                    return web.json_response({"error": "invalid preview response"}, status=502)
    except (OSError, asyncio.TimeoutError):
        return web.json_response({"error": "preview unavailable"}, status=503)
    except Exception:
        return web.json_response({"error": "preview unavailable"}, status=502)

    headers = {
        "Content-Type": content_type,
        "Cache-Control": "no-store",
        "Content-Security-Policy": _REMOTE_PREVIEW_CSP,
        "Cross-Origin-Opener-Policy": "same-origin",
        # The preview document has an opaque sandbox origin. Its own CSS and
        # scripts are cross-origin loads even on the same Dashboard route.
        # These digest-verified snapshot bytes carry no portal authority;
        # retain the opaque CSP sandbox and the proxy authentication above.
        "Cross-Origin-Resource-Policy": "cross-origin",
        # CORP alone does not authorize module imports or fetch() from an
        # opaque frame. This applies only to authenticated, digest-verified
        # static snapshots, without allowing browser credentials.
        "Access-Control-Allow-Origin": "*",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Preview-SHA256": digest,
    }
    return web.Response(status=200, body=body, headers=headers)


async def handle_not_found(_request: web.Request):
    return web.json_response({"error": "not found"}, status=404)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application()
    app[_CANCEL_EVENTS_KEY] = {}
    app[_CHAT_ACTIVITY_KEY] = {}
    app[_ACTIVE_REQUESTS_KEY] = set()
    gate = TransitionGate(
        os.environ.get("PIXEL_TRANSITION_STATE_DIR", ""), app[_ACTIVE_REQUESTS_KEY],
        owner_key_distinct=not _constant_time_compare(config_token, preview_proxy_token),
    )
    app[_TRANSITION_GATE_KEY] = gate

    async def stop_admission(_application):
        await gate.shutdown()

    async def close_gate(_application):
        gate.close()

    app.on_shutdown.append(stop_admission)
    app.on_cleanup.append(close_gate)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/preview/{site_id}/{tail:.*}", handle_preview)
    app.router.add_get("/v1/models", handle_models)
    app.router.add_get("/v1/activity", handle_activity)
    app.router.add_get("/v1/transition", handle_transition)
    app.router.add_post("/v1/transition/{operation:acquire|release|recover}", handle_transition)
    app.router.add_post("/v1/chat/completions", handle_chat_completions)
    app.router.add_post("/v1/chat/cancel", handle_chat_cancel)
    app.router.add_post("/v1/chat/activity", handle_chat_activity)
    # Catch-all registered last: unmatched paths AND unmatched methods → 404.
    app.router.add_route("*", "/{tail:.*}", handle_not_found)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host="0.0.0.0", port=_LISTEN_PORT)
