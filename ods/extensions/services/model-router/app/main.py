"""ODS model-router: the stable-alias data plane (Switchboard PR 3).

One job: requests arrive for the public alias (``ods/current`` or a
compatibility alias), and the router forwards them to the concrete backend
and runtime model named by the host agent's ``model-state.json`` — rewriting
the request ``model`` on the way in and restoring the requested alias on the
way out (including every SSE chunk).

Security boundary (plan §3.6):
- Internal Compose service only; no host port. Only the explicit OpenAI
  paths below are forwarded; everything else is rejected.
- ``endpointId`` resolves through a read-only allowlist file generated at
  install; state can never name an arbitrary upstream.
- Hop-by-hop and client authorization headers are stripped; backend
  credentials come from the router's own environment.
- Bounded body size, queue depth, and timeouts, all covered by tests.

Route evidence (plan §3.4): bounded in-memory records correlated by a
signed probe marker, exposed only on the internal network with a bearer
key. No prompts, generations, or credentials are ever stored.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("ods-model-router")

PUBLIC_ALIASES = ("ods/current", "default")
STATE_PATH = Path(os.environ.get("ODS_MODEL_STATE_PATH", "/state/model-state.json"))
ENDPOINTS_PATH = Path(
    os.environ.get("ODS_ROUTER_ENDPOINTS_PATH", "/config/endpoints.json")
)
INTERNAL_KEY = os.environ.get("ODS_ROUTER_INTERNAL_KEY", "") or os.environ.get(
    "DASHBOARD_API_KEY", ""
)
PROBE_KEY = os.environ.get("ODS_FLEET_PROBE_KEY", "")
_PROBE_KEY_PATH_VALUE = os.environ.get("ODS_FLEET_PROBE_KEY_PATH", "")
PROBE_KEY_PATH: Path | None = (
    Path(_PROBE_KEY_PATH_VALUE) if _PROBE_KEY_PATH_VALUE else None
)
INSTANCE_ID = str(uuid.uuid4())

MAX_BODY_BYTES = int(os.environ.get("ODS_ROUTER_MAX_BODY_BYTES", str(2 * 1024 * 1024)))
MAX_QUEUE_DEPTH = int(os.environ.get("ODS_ROUTER_MAX_QUEUE_DEPTH", "64"))
QUEUE_WAIT_SECONDS = int(os.environ.get("ODS_ROUTER_QUEUE_WAIT_SECONDS", "60"))
UPSTREAM_TIMEOUT_SECONDS = float(os.environ.get("ODS_ROUTER_UPSTREAM_TIMEOUT", "600"))
UPSTREAM_MAX_CONNECTIONS = max(
    1, int(os.environ.get(
        "ODS_ROUTER_UPSTREAM_MAX_CONNECTIONS", str(MAX_QUEUE_DEPTH)
    ))
)
UPSTREAM_MAX_KEEPALIVE = max(
    0, int(os.environ.get("ODS_ROUTER_UPSTREAM_MAX_KEEPALIVE", "20"))
)
TOKEN_SPY_URL = os.environ.get("TOKEN_SPY_URL", "").rstrip("/")
TOKEN_SPY_API_KEY = os.environ.get("TOKEN_SPY_API_KEY", "")
TELEMETRY_QUEUE_DEPTH = max(
    1, int(os.environ.get("ODS_ROUTER_TELEMETRY_QUEUE_DEPTH", "1024"))
)
TELEMETRY_TIMEOUT_SECONDS = max(
    0.1, float(os.environ.get("ODS_ROUTER_TELEMETRY_TIMEOUT", "3"))
)
EVIDENCE_LIMIT = 2048
EVIDENCE_TTL_SECONDS = 15 * 60

FORWARD_PATHS = {
    "/v1/chat/completions": "POST",
    "/v1/completions": "POST",
    "/v1/responses": "POST",
}

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "authorization",
    "content-length",
}

_PROBE_RE = re.compile(
    r"\[ODS_PROBE id=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) "
    r"sig=([A-Za-z0-9_-]+)\]"
)
_SSE_DELIMITER_RE = re.compile(rb"(?:\r\n|\r|\n)(?:\r\n|\r|\n)")
_CHAT_TEMPLATE_ARTIFACTS = (
    re.compile(r"<\|im_start\|>\s*(?:assistant|user|system|tool)?\s*<\|im_end\|>"),
    re.compile(r"<\|start_header_id\|>\s*(?:assistant|user|system|tool)?\s*<\|end_header_id\|>"),
    re.compile(r"<\|(?:im_start|im_end|eot_id|endoftext|end)\|>"),
)

app = FastAPI(title="ODS Model Router", docs_url=None, redoc_url=None,
              openapi_url=None)

_inflight = 0
_inflight_lock = asyncio.Lock()

_state_cache: dict[str, Any] = {"mtime": None, "doc": None}
_endpoints_cache: dict[str, Any] = {"mtime": None, "endpoints": {}}
_probe_key_cache: dict[str, Any] = {"mtime": None, "key": ""}
_evidence: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


class _TelemetrySink:
    """Best-effort telemetry transport that never blocks model responses."""

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.enabled = bool(self.url and self.api_key)
        self.queue: asyncio.Queue[dict[str, Any]] | None = (
            asyncio.Queue(maxsize=TELEMETRY_QUEUE_DEPTH)
            if self.enabled
            else None
        )
        self.client = client
        self.task: asyncio.Task[None] | None = None
        self._last_warning = 0.0

    async def start(self) -> None:
        if not self.enabled:
            return
        if self.client is None:
            self.client = httpx.AsyncClient(
                follow_redirects=False,
                timeout=TELEMETRY_TIMEOUT_SECONDS,
            )
        self.task = asyncio.create_task(
            self._run(), name="model-router-token-spy"
        )

    def emit(self, event: dict[str, Any]) -> bool:
        if self.queue is None:
            return False
        try:
            self.queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            self._warn("Token Spy telemetry queue is full; dropping event")
            return False

    async def _run(self) -> None:
        assert self.queue is not None
        assert self.client is not None
        while True:
            event = await self.queue.get()
            try:
                response = await self.client.post(
                    f"{self.url}/api/ingest/routed",
                    json=event,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if response.status_code != 202:
                    self._warn(
                        "Token Spy rejected routed telemetry "
                        f"with HTTP {response.status_code}"
                    )
            except (httpx.HTTPError, RuntimeError) as exc:
                self._warn(f"Token Spy telemetry unavailable: {exc}")
            finally:
                self.queue.task_done()

    def _warn(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_warning >= 60:
            logger.warning(message)
            self._last_warning = now

    async def stop(self) -> None:
        if self.queue is not None:
            try:
                await asyncio.wait_for(self.queue.join(), timeout=1.0)
            except asyncio.TimeoutError:
                self._warn("Timed out draining Token Spy telemetry queue")
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if self.client is not None:
            await self.client.aclose()


def _usage_from_response(payload: Any) -> tuple[dict[str, int], str]:
    """Normalize OpenAI Chat/Completions and Responses API usage fields."""
    if not isinstance(payload, dict):
        payload = {}
    response = payload.get("response")
    source = response if isinstance(response, dict) else payload
    usage = source.get("usage")
    usage = usage if isinstance(usage, dict) else {}

    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = usage.get("input_tokens_details")
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}

    normalized = {
        "input_tokens": _nonnegative_int(
            usage.get("prompt_tokens", usage.get("input_tokens", 0))
        ),
        "output_tokens": _nonnegative_int(
            usage.get("completion_tokens", usage.get("output_tokens", 0))
        ),
        "cache_read_tokens": _nonnegative_int(
            prompt_details.get("cached_tokens", usage.get("cache_read_tokens", 0))
        ),
        "cache_write_tokens": _nonnegative_int(
            usage.get("cache_write_tokens", 0)
        ),
    }

    stop_reason = source.get("stop_reason") or source.get("status") or ""
    choices = source.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        stop_reason = choices[0].get("finish_reason") or stop_reason
    return normalized, str(stop_reason)[:128]


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _build_telemetry_event(
    payload: dict[str, Any],
    *,
    raw_body_bytes: int,
    model: str,
    backend: str,
    path: str,
    duration_ms: int,
    usage: dict[str, int],
    stop_reason: str,
) -> dict[str, Any]:
    messages = payload.get("messages")
    messages = messages if isinstance(messages, list) else []
    roles = [
        item.get("role")
        for item in messages
        if isinstance(item, dict)
    ]
    tools = payload.get("tools")
    tools = tools if isinstance(tools, list) else []
    return {
        "agent": "model-router",
        "model": str(model)[:512],
        "provider_name": str(backend or "unknown")[:128],
        "path": path,
        "request_body_bytes": min(max(raw_body_bytes, 0), MAX_BODY_BYTES),
        "message_count": min(len(messages), 100_000),
        "user_message_count": min(roles.count("user"), 100_000),
        "assistant_message_count": min(roles.count("assistant"), 100_000),
        "tool_count": min(len(tools), 100_000),
        "input_tokens": min(
            _nonnegative_int(usage.get("input_tokens")), 2_000_000_000
        ),
        "output_tokens": min(
            _nonnegative_int(usage.get("output_tokens")), 2_000_000_000
        ),
        "cache_read_tokens": min(
            _nonnegative_int(usage.get("cache_read_tokens")), 2_000_000_000
        ),
        "cache_write_tokens": min(
            _nonnegative_int(usage.get("cache_write_tokens")), 2_000_000_000
        ),
        "duration_ms": min(max(duration_ms, 0), 86_400_000),
        "stop_reason": str(stop_reason or "")[:128],
    }


def _emit_telemetry(event: dict[str, Any]) -> bool:
    sink: _TelemetrySink | None = getattr(app.state, "telemetry", None)
    if sink is None:
        return False
    try:
        return sink.emit(event)
    except Exception as exc:
        logger.warning("Token Spy telemetry enqueue failed: %s", exc)
        return False


class RouterError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _load_endpoints() -> dict[str, dict[str, Any]]:
    """Allowlist endpointId -> {baseUrl, apiKeyEnv?}, re-read when the file
    changes on disk (activation re-renders it). Retains the last good
    allowlist while the file is missing or invalid."""
    try:
        stat = ENDPOINTS_PATH.stat()
    except OSError as exc:
        if _endpoints_cache["mtime"] is None:
            logger.error("endpoints allowlist unavailable: %s", exc)
        return _endpoints_cache["endpoints"]
    mtime = (stat.st_mtime_ns, stat.st_size)
    if _endpoints_cache["mtime"] == mtime:
        return _endpoints_cache["endpoints"]
    endpoints: dict[str, dict[str, Any]] = {}
    try:
        raw = json.loads(ENDPOINTS_PATH.read_text(encoding="utf-8"))
        for entry in raw.get("endpoints", []):
            endpoint_id = str(entry.get("id") or "")
            base_url = str(entry.get("baseUrl") or "")
            if not endpoint_id or not base_url.startswith(("http://", "https://")):
                continue
            endpoints[endpoint_id] = {
                "baseUrl": base_url.rstrip("/"),
                "apiKeyEnv": str(entry.get("apiKeyEnv") or ""),
            }
    except (OSError, ValueError) as exc:
        logger.error("endpoints allowlist unreadable; retaining previous: %s", exc)
        return _endpoints_cache["endpoints"]
    _endpoints_cache["mtime"] = mtime
    _endpoints_cache["endpoints"] = endpoints
    return endpoints


def _has_keys(value: Any, required: set[str], allowed: set[str]) -> bool:
    return (
        isinstance(value, dict)
        and required <= set(value)
        and set(value) <= allowed
    )


def _is_nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _validate_state_schema(doc: Any) -> bool:
    """Validate the checked-in ``ods.model-state.v1`` contract."""
    root_keys = {
        "schema", "seq", "routeSeq", "operation", "desired", "active",
        "history", "availability",
    }
    if not _has_keys(
        doc,
        {"schema", "seq", "routeSeq", "desired", "active", "history",
         "availability"},
        root_keys,
    ):
        return False
    if doc["schema"] != "ods.model-state.v1":
        return False
    if not _is_nonnegative_int(doc["seq"]) or not _is_nonnegative_int(doc["routeSeq"]):
        return False

    operation = doc.get("operation")
    if operation is not None:
        if not _has_keys(
            operation,
            {"id", "phase", "requestedModelId", "startedAt"},
            {"id", "phase", "requestedModelId", "startedAt", "error"},
        ):
            return False
        if not isinstance(operation["id"], str) or not operation["id"]:
            return False
        if operation["phase"] not in {
            "requested", "staging", "verifying", "publishing", "flipping",
            "serving", "failed", "rolling_back",
        }:
            return False
        if not isinstance(operation["requestedModelId"], str):
            return False
        if not isinstance(operation["startedAt"], str):
            return False
        if "error" in operation and operation["error"] is not None \
                and not isinstance(operation["error"], str):
            return False

    desired = doc["desired"]
    if desired is not None:
        if not _has_keys(desired, {"catalogId"}, {"catalogId"}):
            return False
        if not isinstance(desired["catalogId"], str) or not desired["catalogId"]:
            return False

    active = doc["active"]
    if active is not None:
        active_keys = {
            "routeSeq", "catalogId", "runtimeModelId", "publicModel", "backend",
            "contextLength", "capabilities", "verifiedAt", "reconstructed", "proof",
        }
        if not _has_keys(
            active,
            {"routeSeq", "catalogId", "runtimeModelId", "publicModel", "backend",
             "contextLength", "capabilities", "verifiedAt", "proof"},
            active_keys,
        ):
            return False
        if not _is_nonnegative_int(active["routeSeq"]):
            return False
        for key in ("catalogId", "runtimeModelId", "publicModel"):
            if not isinstance(active[key], str) or not active[key]:
                return False
        if not _is_nonnegative_int(active["contextLength"]):
            return False
        if active["verifiedAt"] is not None and not isinstance(active["verifiedAt"], str):
            return False
        if "reconstructed" in active and type(active["reconstructed"]) is not bool:
            return False

        backend = active["backend"]
        if not _has_keys(
            backend, {"kind", "endpointId"}, {"kind", "endpointId", "nativeRoute"}
        ):
            return False
        if backend["kind"] not in {"llama-server", "lemonade", "hipfire", "unknown"}:
            return False
        if not isinstance(backend["endpointId"], str) or not backend["endpointId"]:
            return False
        if "nativeRoute" in backend and backend["nativeRoute"] is not None \
                and not isinstance(backend["nativeRoute"], str):
            return False

        capabilities = active["capabilities"]
        capability_keys = {"chat", "tools", "vision", "agentViable"}
        if not _has_keys(capabilities, capability_keys, capability_keys):
            return False
        if any(type(capabilities[key]) is not bool for key in capability_keys):
            return False

        proof = active["proof"]
        if not _has_keys(proof, {"identity", "completion"}, {"identity", "completion"}):
            return False
        if proof["identity"] is not None and not isinstance(proof["identity"], str):
            return False
        if type(proof["completion"]) is not bool:
            return False

    history = doc["history"]
    if not isinstance(history, list) or len(history) > 10:
        return False
    for entry in history:
        if not isinstance(entry, dict) or not {
            "routeSeq", "catalogId", "runtimeModelId", "verifiedAt"
        } <= set(entry):
            return False
        if not _is_nonnegative_int(entry["routeSeq"]):
            return False
        if not isinstance(entry["catalogId"], str):
            return False
        if not isinstance(entry["runtimeModelId"], str):
            return False
        if entry["verifiedAt"] is not None and not isinstance(entry["verifiedAt"], str):
            return False

    availability = doc["availability"]
    if not _has_keys(
        availability, {"mode", "queueDeadline"}, {"mode", "queueDeadline"}
    ):
        return False
    if availability["mode"] not in {"serve_active", "queue"}:
        return False
    if availability["queueDeadline"] is not None \
            and not isinstance(availability["queueDeadline"], str):
        return False
    return True


def _has_verified_active_route(doc: dict[str, Any]) -> bool:
    active = doc.get("active")
    if not isinstance(active, dict) or active.get("reconstructed") is True:
        return False
    proof = active.get("proof")
    runtime_model = active.get("runtimeModelId")
    return (
        isinstance(runtime_model, str)
        and bool(runtime_model)
        and isinstance(active.get("verifiedAt"), str)
        and bool(active["verifiedAt"])
        and isinstance(proof, dict)
        and proof.get("completion") is True
        and proof.get("identity") == runtime_model
        and active.get("routeSeq") == doc.get("routeSeq")
        and doc.get("routeSeq", 0) <= doc.get("seq", -1)
    )


def _read_state() -> dict[str, Any] | None:
    """Return only verified, monotonic state, retaining verified last-known-good."""
    try:
        stat = STATE_PATH.stat()
    except OSError:
        return _state_cache["doc"]
    mtime = (stat.st_mtime_ns, stat.st_size)
    if _state_cache["mtime"] == mtime:
        return _state_cache["doc"]
    try:
        doc = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _state_cache["doc"]
    if not _validate_state_schema(doc) or not _has_verified_active_route(doc):
        logger.warning("rejecting invalid or unverified model state")
        return _state_cache["doc"]
    cached = _state_cache["doc"]
    if cached is not None:
        if doc["seq"] < cached["seq"] or doc["routeSeq"] < cached["routeSeq"]:
            logger.warning("rejecting regressed model state sequence")
            return cached
        if doc["seq"] == cached["seq"] and doc != cached:
            logger.warning("rejecting mutated model state at unchanged sequence")
            return cached
    _state_cache["mtime"] = mtime
    _state_cache["doc"] = doc
    return doc


def _active_route() -> dict[str, Any]:
    doc = _read_state()
    active = (doc or {}).get("active")
    if not isinstance(active, dict):
        raise RouterError(503, "no_active_route",
                          "No verified active model route is available yet")
    endpoint_id = str(((active.get("backend") or {}).get("endpointId")) or "")
    endpoint = _load_endpoints().get(endpoint_id)
    if endpoint is None:
        raise RouterError(503, "endpoint_not_allowlisted",
                          f"Active endpointId {endpoint_id!r} is not in the "
                          "router allowlist")
    availability = (doc or {}).get("availability") or {}
    return {
        "routeSeq": int(active.get("routeSeq") or 0),
        "runtimeModelId": str(active.get("runtimeModelId") or ""),
        "backendKind": str((active.get("backend") or {}).get("kind") or "unknown"),
        "endpointId": endpoint_id,
        "baseUrl": endpoint["baseUrl"],
        "apiKeyEnv": endpoint["apiKeyEnv"],
        "queueMode": str(availability.get("mode") or "serve_active") == "queue",
    }


def _record_evidence(record: dict[str, Any]) -> None:
    now = time.monotonic()
    record["storedAt"] = now
    record["instanceId"] = INSTANCE_ID
    _evidence[record["probeId"]] = record
    while len(_evidence) > EVIDENCE_LIMIT:
        _evidence.popitem(last=False)
    stale = [k for k, v in _evidence.items()
             if now - v["storedAt"] > EVIDENCE_TTL_SECONDS]
    for key in stale:
        _evidence.pop(key, None)


def _current_probe_key() -> str:
    """Fleet probe key: file-based (mtime-cached re-read) when
    ODS_FLEET_PROBE_KEY_PATH is configured and the file holds a value,
    falling back to the ODS_FLEET_PROBE_KEY environment value. The file
    form lets the fleet harness rotate or remove the key without
    recreating the router (which would destroy in-memory evidence)."""
    if PROBE_KEY_PATH is None:
        return PROBE_KEY
    try:
        stat = PROBE_KEY_PATH.stat()
    except OSError:
        _probe_key_cache["mtime"] = None
        _probe_key_cache["key"] = ""
        return PROBE_KEY
    mtime = (stat.st_mtime_ns, stat.st_size)
    if _probe_key_cache["mtime"] != mtime:
        try:
            key = PROBE_KEY_PATH.read_text(encoding="utf-8").strip()
        except OSError:
            _probe_key_cache["mtime"] = None
            _probe_key_cache["key"] = ""
            return PROBE_KEY
        _probe_key_cache["mtime"] = mtime
        _probe_key_cache["key"] = key
    return _probe_key_cache["key"] or PROBE_KEY


def _verify_probe_marker(body_text: str) -> str | None:
    """Return the probe UUID only for exactly one validly signed marker."""
    probe_key = _current_probe_key()
    if not probe_key:
        return None
    matches = _PROBE_RE.findall(body_text)
    if len(matches) != 1:
        return None
    probe_id, signature = matches[0]
    expected = base64.urlsafe_b64encode(
        hmac.new(probe_key.encode("utf-8"), probe_id.encode("utf-8"),
                 hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(expected, signature):
        return None
    return probe_id


def _sanitize_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        if name.lower() in _HOP_BY_HOP:
            continue
        headers[name] = value
    headers["content-type"] = "application/json"
    return headers


def _strip_chat_template_artifacts(text: str) -> str:
    cleaned = text
    for pattern in _CHAT_TEMPLATE_ARTIFACTS:
        cleaned = pattern.sub("", cleaned)
    return cleaned


def _sanitize_content_value(value: Any) -> tuple[Any, bool]:
    if isinstance(value, str):
        cleaned = _strip_chat_template_artifacts(value)
        return cleaned, cleaned != value
    if isinstance(value, list):
        changed = False
        cleaned_items: list[Any] = []
        for item in value:
            if isinstance(item, dict):
                item = dict(item)
                for key in ("text", "content"):
                    if isinstance(item.get(key), str):
                        item[key], item_changed = _sanitize_content_value(item[key])
                        changed = changed or item_changed
            cleaned_items.append(item)
        return cleaned_items, changed
    return value, False


def _sanitize_choice_content(obj: dict[str, Any]) -> bool:
    changed = False
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return False
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        for container_key in ("message", "delta"):
            container = choice.get(container_key)
            if isinstance(container, dict) and "content" in container:
                container["content"], item_changed = _sanitize_content_value(
                    container["content"]
                )
                changed = changed or item_changed
        if "text" in choice:
            choice["text"], item_changed = _sanitize_content_value(choice["text"])
            changed = changed or item_changed
    return changed


def _rewrite_sse_event(
    event: bytes, alias: str
) -> tuple[bytes, list[str], list[dict[str, Any]], bool]:
    """Rewrite complete SSE data lines and return observable response metadata."""
    out_lines: list[bytes] = []
    models: list[str] = []
    payloads: list[dict[str, Any]] = []
    saw_done = False
    for line in event.splitlines(keepends=True):
        content = line.rstrip(b"\r\n")
        ending = line[len(content):]
        if content.startswith(b"data:"):
            payload = content[5:].strip()
            if payload == b"[DONE]":
                saw_done = True
            elif payload:
                try:
                    obj = json.loads(payload)
                except ValueError:
                    obj = None
                if isinstance(obj, dict):
                    payloads.append(obj)
                    if isinstance(obj.get("model"), str):
                        if obj["model"]:
                            models.append(obj["model"])
                        obj["model"] = alias
                    _sanitize_choice_content(obj)
                    content = (
                        b"data: "
                        + json.dumps(obj, separators=(",", ":")).encode("utf-8")
                    )
        out_lines.append(content + ending)
    return b"".join(out_lines), models, payloads, saw_done


def _is_terminal_stream_payload(payload: dict[str, Any]) -> bool:
    """Recognize terminal Chat/Completions and Responses API stream events."""
    choices = payload.get("choices")
    if isinstance(choices, list) and any(
        isinstance(choice, dict) and choice.get("finish_reason") is not None
        for choice in choices
    ):
        return True

    event_type = payload.get("type")
    if event_type == "response.completed":
        return True
    response = payload.get("response")
    if isinstance(response, dict) and response.get("status") == "completed":
        return True
    return payload.get("status") == "completed"


class _SSERewriter:
    """Incrementally frames SSE so transport chunk boundaries are irrelevant."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.buffer = b""
        self.models: list[str] = []
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
        self.stop_reason = ""
        self.completed = False

    def _observe(
        self, payloads: list[dict[str, Any]], *, saw_done: bool = False
    ) -> None:
        self.completed = self.completed or saw_done
        for payload in payloads:
            usage, stop_reason = _usage_from_response(payload)
            for key, value in usage.items():
                self.usage[key] = max(self.usage[key], value)
            if stop_reason:
                self.stop_reason = stop_reason
            self.completed = self.completed or _is_terminal_stream_payload(payload)

    def feed(self, chunk: bytes) -> list[bytes]:
        self.buffer += chunk
        output: list[bytes] = []
        while match := _SSE_DELIMITER_RE.search(self.buffer):
            event = self.buffer[:match.start()]
            delimiter = self.buffer[match.start():match.end()]
            self.buffer = self.buffer[match.end():]
            rewritten, models, payloads, saw_done = _rewrite_sse_event(
                event, self.alias
            )
            self.models.extend(models)
            self._observe(payloads, saw_done=saw_done)
            output.append(rewritten + delimiter)
        return output

    def finish(self) -> bytes:
        if not self.buffer:
            return b""
        rewritten, models, payloads, saw_done = _rewrite_sse_event(
            self.buffer, self.alias
        )
        self.models.extend(models)
        self._observe(payloads, saw_done=saw_done)
        self.buffer = b""
        return rewritten


async def _read_bounded_body(request: Request) -> bytes:
    """Read the ASGI body incrementally and stop at the configured limit."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_BODY_BYTES:
                raise RouterError(413, "payload_too_large",
                                  "Request body exceeds the router limit")
        except ValueError:
            pass
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise RouterError(413, "payload_too_large",
                              "Request body exceeds the router limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _release_admission() -> None:
    global _inflight
    async with _inflight_lock:
        _inflight -= 1


@app.get("/health")
async def health() -> dict[str, Any]:
    doc = _read_state()
    endpoints = _load_endpoints()
    # Body-level signal only: with an empty allowlist every forward answers
    # endpoint_not_allowlisted, but the HTTP status stays 200 so the compose
    # healthcheck does not cascade a config gap into container restarts.
    return {
        "status": "ok" if endpoints else "degraded",
        "endpointCount": len(endpoints),
        "hasRoute": bool((doc or {}).get("active")),
        "seq": (doc or {}).get("seq"),
        "routeSeq": (doc or {}).get("routeSeq"),
        "instanceId": INSTANCE_ID,
        "probeKeyConfigured": bool(_current_probe_key()),
    }


@app.get("/v1/models")
async def list_models() -> dict[str, Any]:
    data = [{"id": alias, "object": "model", "owned_by": "ods"}
            for alias in PUBLIC_ALIASES]
    try:
        route = _active_route()
        metadata = {"routedModel": route["runtimeModelId"],
                    "backend": route["backendKind"],
                    "routeSeq": route["routeSeq"]}
    except RouterError:
        metadata = {"routedModel": None, "backend": None, "routeSeq": None}
    return {"object": "list", "data": data, "ods": metadata}


@app.get("/internal/route-evidence/{probe_id}")
async def route_evidence(probe_id: str, request: Request) -> Response:
    provided = request.headers.get("authorization", "")
    if not INTERNAL_KEY or provided != f"Bearer {INTERNAL_KEY}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    record = _evidence.get(probe_id)
    if record is None or time.monotonic() - record["storedAt"] > EVIDENCE_TTL_SECONDS:
        return JSONResponse({"error": "not_found"}, status_code=404)
    public = {k: v for k, v in record.items() if k != "storedAt"}
    return JSONResponse(public)


@app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE",
                                             "PATCH", "HEAD", "OPTIONS", "CONNECT"])
async def forward(full_path: str, request: Request) -> Response:
    path = "/" + full_path
    method = FORWARD_PATHS.get(path)
    if method is None or request.method != method:
        return JSONResponse(
            {"error": {"message": f"Path not served by the ODS model router: {path}",
                       "type": "not_forwarded", "code": "404"}},
            status_code=404,
        )

    try:
        body = await _read_bounded_body(request)
    except RouterError as exc:
        return JSONResponse(
            {"error": {"message": exc.message, "type": exc.code,
                       "code": str(exc.status)}},
            status_code=exc.status,
        )
    try:
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("body must be a JSON object")
    except (ValueError, UnicodeDecodeError) as exc:
        return JSONResponse(
            {"error": {"message": f"Invalid JSON body: {exc}",
                       "type": "invalid_request_error", "code": "400"}},
            status_code=400,
        )

    requested_alias = str(payload.get("model") or PUBLIC_ALIASES[0])

    global _inflight
    async with _inflight_lock:
        if _inflight >= MAX_QUEUE_DEPTH:
            return JSONResponse(
                {"error": {"message": "Router queue is full",
                           "type": "overloaded", "code": "503"}},
                status_code=503, headers={"Retry-After": "5"},
            )
        _inflight += 1
    stream_owns_admission = False
    try:
        response, stream_owns_admission = await _forward_inner(
            request, path, payload, requested_alias, body
        )
        return response
    finally:
        if not stream_owns_admission:
            await _release_admission()


async def _forward_inner(request: Request, path: str, payload: dict[str, Any],
                         requested_alias: str,
                         raw_body: bytes) -> tuple[Response, bool]:
    deadline = time.monotonic() + QUEUE_WAIT_SECONDS
    while True:
        try:
            route = _active_route()
        except RouterError as exc:
            return JSONResponse(
                {"error": {"message": exc.message, "type": exc.code,
                           "code": str(exc.status)}},
                status_code=exc.status,
                headers={"Retry-After": "5"} if exc.status == 503 else {},
            ), False
        if not route["queueMode"]:
            break
        if time.monotonic() >= deadline:
            return JSONResponse(
                {"error": {"message": "A model swap is in progress",
                           "type": "model_swap_in_progress", "code": "503"}},
                status_code=503, headers={"Retry-After": "10"},
            ), False
        await asyncio.sleep(0.25)

    payload["model"] = route["runtimeModelId"]
    request_id = str(uuid.uuid4())
    probe_id = _verify_probe_marker(raw_body.decode("utf-8", "replace"))
    is_stream = bool(payload.get("stream"))

    headers = _sanitize_headers(request)
    api_key = os.environ.get(route["apiKeyEnv"], "") if route["apiKeyEnv"] else ""
    if api_key:
        headers["authorization"] = f"Bearer {api_key}"

    ods_headers = {
        "X-ODS-Request-Id": request_id,
        "X-ODS-Requested-Model": requested_alias,
        "X-ODS-Routed-Model": route["runtimeModelId"],
        "X-ODS-Backend": route["backendKind"],
        "X-ODS-Route-Seq": str(route["routeSeq"]),
    }

    url = route["baseUrl"] + path
    client: httpx.AsyncClient = app.state.http
    telemetry_started = time.monotonic()
    evidence_base = {
        "probeId": probe_id,
        "requestedModel": requested_alias,
        "routedModel": route["runtimeModelId"],
        "backend": route["backendKind"],
        "endpointId": route["endpointId"],
        "routeSeq": route["routeSeq"],
        "path": path,
    }

    try:
        if is_stream:
            upstream_request = client.build_request(
                "POST", url, content=json.dumps(payload).encode("utf-8"),
                headers=headers, timeout=UPSTREAM_TIMEOUT_SECONDS,
            )
            upstream = await client.send(upstream_request, stream=True)
            lemonade_route = upstream.headers.get("x-lemonade-route")
            if lemonade_route:
                ods_headers["X-Lemonade-Route"] = lemonade_route

            async def stream_body() -> AsyncIterator[bytes]:
                rewriter = _SSERewriter(requested_alias)
                completed = False
                try:
                    async for chunk in upstream.aiter_bytes():
                        for event in rewriter.feed(chunk):
                            yield event
                    tail = rewriter.finish()
                    if tail:
                        yield tail
                    completed = True
                finally:
                    await upstream.aclose()
                    if (
                        completed
                        and rewriter.completed
                        and probe_id
                        and 200 <= upstream.status_code < 300
                        and all(
                            model == route["runtimeModelId"]
                            for model in rewriter.models
                        )
                    ):
                        _record_evidence({
                            **evidence_base,
                            "status": upstream.status_code,
                            "responseModel": route["runtimeModelId"],
                            "lemonadeRoute": lemonade_route,
                        })
                    if (
                        completed
                        and rewriter.completed
                        and 200 <= upstream.status_code < 300
                    ):
                        _emit_telemetry(_build_telemetry_event(
                            payload,
                            raw_body_bytes=len(raw_body),
                            model=route["runtimeModelId"],
                            backend=route["backendKind"],
                            path=path,
                            duration_ms=int(
                                (time.monotonic() - telemetry_started) * 1000
                            ),
                            usage=rewriter.usage,
                            stop_reason=rewriter.stop_reason,
                        ))
                    await _release_admission()

            media_type = upstream.headers.get("content-type",
                                              "text/event-stream")
            return StreamingResponse(
                stream_body(), status_code=upstream.status_code,
                media_type=media_type, headers=ods_headers,
            ), True

        upstream = await client.post(
            url, content=json.dumps(payload).encode("utf-8"), headers=headers,
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": {"message": "Upstream model runtime timed out",
                       "type": "upstream_timeout", "code": "504"}},
            status_code=504, headers=ods_headers,
        ), False
    except httpx.HTTPError as exc:
        return JSONResponse(
            {"error": {"message": f"Upstream model runtime unavailable: {exc}",
                       "type": "upstream_unavailable", "code": "502"}},
            status_code=502, headers=ods_headers,
        ), False

    lemonade_route = upstream.headers.get("x-lemonade-route")
    if lemonade_route:
        ods_headers["X-Lemonade-Route"] = lemonade_route

    response_model = None
    response_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    stop_reason = ""
    content = upstream.content
    try:
        parsed = json.loads(content.decode("utf-8"))
        if isinstance(parsed, dict):
            response_usage, stop_reason = _usage_from_response(parsed)
            response_model = parsed.get("model")
            if "model" in parsed:
                parsed["model"] = requested_alias
            _sanitize_choice_content(parsed)
            lemonade_meta = parsed.get("x_lemonade_route")
            if lemonade_meta is not None:
                ods_headers.setdefault("X-Lemonade-Route",
                                       json.dumps(lemonade_meta)
                                       if not isinstance(lemonade_meta, str)
                                       else lemonade_meta)
            content = json.dumps(parsed).encode("utf-8")
    except (ValueError, UnicodeDecodeError):
        pass

    if probe_id:
        _record_evidence({**evidence_base,
                          "status": upstream.status_code,
                          "responseModel": str(response_model or ""),
                          "lemonadeRoute": lemonade_route})

    if 200 <= upstream.status_code < 300:
        _emit_telemetry(_build_telemetry_event(
            payload,
            raw_body_bytes=len(raw_body),
            model=route["runtimeModelId"],
            backend=route["backendKind"],
            path=path,
            duration_ms=int((time.monotonic() - telemetry_started) * 1000),
            usage=response_usage,
            stop_reason=stop_reason,
        ))

    media_type = upstream.headers.get("content-type", "application/json")
    return Response(content=content, status_code=upstream.status_code,
                    media_type=media_type, headers=ods_headers), False


@app.on_event("startup")
async def _startup() -> None:
    app.state.http = httpx.AsyncClient(
        follow_redirects=False,
        limits=httpx.Limits(
            max_connections=UPSTREAM_MAX_CONNECTIONS,
            max_keepalive_connections=min(
                UPSTREAM_MAX_KEEPALIVE, UPSTREAM_MAX_CONNECTIONS
            ),
        ),
    )
    app.state.telemetry = _TelemetrySink(TOKEN_SPY_URL, TOKEN_SPY_API_KEY)
    await app.state.telemetry.start()
    _load_endpoints()


@app.on_event("shutdown")
async def _shutdown() -> None:
    telemetry: _TelemetrySink | None = getattr(app.state, "telemetry", None)
    if telemetry is not None:
        await telemetry.stop()
    await app.state.http.aclose()
