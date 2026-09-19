"""Bounded dashboard bridge to the internal Pixel edge service."""

from __future__ import annotations
import asyncio

try:
    from asyncio import timeout as async_timeout
except ImportError:  # Python 3.10; installed by this runtime's requirements.
    from async_timeout import timeout as async_timeout
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import AsyncIterator, Callable, Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from host_agent_client import AgentClientError, AgentHTTPError, async_request_json as request_agent_json
from pixel_runtime_state import begin_pixel_stream, end_pixel_stream, try_begin_pixel_stream
from pixel_chat_results import ChatResultStore, ResultCapacity, ResultConflict, owner_namespace
from security import verify_api_key
from config import read_live_env_value
from helpers import get_loaded_model, get_llama_context_size
from pixel_chat_identity import asks_display_name, confirmed_display_name, display_name_stream, messages_with_identity
from pixel_chat_context import HistorySnapshot, public_context


logger = logging.getLogger(__name__)


_DEFAULT_EDGE_URL = "http://pixel-edge:9595"
_MODEL = "pixel/default"
_CHAT_STREAM_TIMEOUT_SECONDS = 2040.0
_CLIENT_DISCONNECT_POLL_SECONDS = 0.25
_STREAM_KEEPALIVE_SECONDS = 15.0
_STREAM_KEEPALIVE = b": pixel working\n\n"
_CLIENT_CANCEL_TIMEOUT_SECONDS = 7.0
_MAX_KEY_LENGTH = 4096
_MAX_STATUS_BYTES = 64 * 1024
_MAX_SSE_LINE_BYTES = 1024 * 1024
_MAX_MESSAGE_CHARS = 16 * 1024
_MAX_TOTAL_MESSAGE_BYTES = 256 * 1024
_SAFE_CHAT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_OPS_JOB_ID = re.compile(r"^ops-[0-9]{13}-[a-f0-9]{12}$")
_OPS_PLAN_HASH = re.compile(r"^[a-f0-9]{64}$")
_OPS_STATUSES = frozenset(
    {
        "awaiting-approval",
        "paused",
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "rejected",
    }
)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_MODEL_SWITCH_DETAIL = "Model switch in progress; Pixel will be ready when activation completes"
_MODEL_IDENTITY_DETAIL = (
    "Pixel cannot verify its recorded model against the loaded Lemonade model. "
    "Re-select the model in Models before using Pixel."
)
_MODEL_ADAPTIVE_DETAIL = (
    "Pixel is ready and adapts its tool flow for this model. Model capability "
    "affects the quality and persistence of complex work, not access or the "
    "broker-enforced safety boundary."
)


def _validate_edge_url(raw: str) -> str:
    """Accept exactly the fixed internal Pixel edge origin."""
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ValueError("PIXEL_EDGE_URL is invalid") from exc
    if parsed.scheme != "http":
        raise ValueError("PIXEL_EDGE_URL scheme must be http")
    if parsed.hostname != "pixel-edge" or port != 9595:
        raise ValueError("PIXEL_EDGE_URL must use pixel-edge:9595")
    if parsed.username or parsed.password or parsed.path or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("PIXEL_EDGE_URL must be an origin without userinfo, path, query, or fragment")
    return _DEFAULT_EDGE_URL


def _pixel_config() -> tuple[str, str] | None:
    """Return validated runtime config, or None when Pixel is not enabled."""
    raw_key = os.environ.get("PIXEL_OPENWEBUI_KEY", "")
    if not raw_key:
        return None
    if raw_key != raw_key.strip() or len(raw_key) < 32 or len(raw_key) > _MAX_KEY_LENGTH:
        raise RuntimeError("PIXEL_OPENWEBUI_KEY is invalid")
    if _CONTROL.search(raw_key):
        raise RuntimeError("PIXEL_OPENWEBUI_KEY is invalid")
    raw_url = os.environ.get("PIXEL_EDGE_URL", _DEFAULT_EDGE_URL)
    return _validate_edge_url(raw_url), raw_key


def _edge_headers(key: str, *, accept: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "Accept": accept,
        "Content-Type": "application/json",
    }


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    role: str
    content: str = Field(max_length=_MAX_MESSAGE_CHARS)

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        if value not in {"system", "user", "assistant"}:
            raise ValueError("unsupported message role")
        return value


class ChatStreamRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chat_id: str
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")
    messages: list[_Message] = Field(min_length=1, max_length=50)
    history_snapshot: HistorySnapshot | None = None

    @field_validator("chat_id")
    @classmethod
    def _chat_id(cls, value: str) -> str:
        if not _SAFE_CHAT_ID.fullmatch(value):
            raise ValueError("invalid chat_id")
        return value

    @field_validator("messages")
    @classmethod
    def _total_size(cls, messages: list[_Message]) -> list[_Message]:
        total = sum(len(item.content.encode("utf-8")) for item in messages)
        if total > _MAX_TOTAL_MESSAGE_BYTES:
            raise ValueError("aggregate message content is too large")
        return messages

    @model_validator(mode="after")
    def _history_matches_turn(self):
        if self.history_snapshot is not None:
            if self.request_id is None:
                raise ValueError("Persistent history requires a request_id")
            latest = self.history_snapshot.messages[-1]
            if latest.role != "user" or latest.model_dump() != self.messages[-1].model_dump():
                raise ValueError("Conversation history must end with the submitted user message")
        return self


class ChatCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    chat_id: str
    request_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$")

    @field_validator("chat_id")
    @classmethod
    def _chat_id(cls, value: str) -> str:
        if not _SAFE_CHAT_ID.fullmatch(value):
            raise ValueError("invalid chat_id")
        return value


router = APIRouter(prefix="/api/pixel", tags=["pixel"])

_result_store: ChatResultStore | None = None
_result_tasks: dict[tuple[str, str, str], asyncio.Task] = {}
_result_stops: set[tuple[str, str]] = set()
_result_abort_ack: set[tuple[str, str, str]] = set()


def _chat_results() -> ChatResultStore:
    global _result_store
    if _result_store is None:
        _result_store = ChatResultStore(Path(os.environ.get("ODS_DATA_DIR", "/data")) / "pixel-chat-results")
    return _result_store


def _result_state(store, identity):
    row = store.get(identity)
    task = _result_tasks.get(identity)
    if row is not None and row["state"] == "active" and (task is None or task.done()):
        # A producer may fail while committing its last bytes. The API process
        # being alive does not prove that this particular task is still running.
        row["state"] = "unresolved"
    return row


class ChatResultRequest(ChatCancelRequest):
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


async def _chat_context_request(body: ChatCancelRequest, *, compact: bool = False):
    config = _pixel_config()
    if config is None:
        raise HTTPException(503, "Portal is not enabled")
    edge_url, key = config
    payload = {"user": body.chat_id}
    if compact:
        payload["request_id"] = body.request_id
    try:
        # Starting a compaction returns a job receipt promptly. CPU/model time
        # belongs to the runtime job, not the browser's HTTP connection.
        timeout = httpx.Timeout(connect=3.0, read=20.0, write=5.0, pool=3.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            async with client.stream(
                "POST", f"{edge_url}/v1/chat/{'compact' if compact else 'context'}",
                json=payload, headers=_edge_headers(key, accept="application/json"),
            ) as response:
                if response.status_code in {409, 423, 429}:
                    raise HTTPException(response.status_code, "Portal is busy. Wait for the current task to finish.")
                if response.status_code != 200 or not response.headers.get("content-type", "").lower().startswith("application/json"):
                    raise ValueError("Invalid context response")
                raw = await _bounded_response_bytes(response, 16 * 1024)
        return public_context(json.loads(raw))
    except (httpx.HTTPError, asyncio.TimeoutError):
        # A timeout does not cancel a native compaction. The caller retains its
        # request ID and reads /context before attempting any further mutation.
        raise HTTPException(503, "Could not confirm context status. Check again before retrying compaction.") from None
    except (ValueError, TypeError):
        raise HTTPException(502, "Portal context status could not be verified") from None


@router.post("/chat/context", dependencies=[Depends(verify_api_key)])
async def pixel_chat_context(body: ChatCancelRequest):
    return await _chat_context_request(body)


@router.post("/chat/compact")
async def pixel_chat_compact(body: ChatResultRequest, owner: str = Depends(verify_api_key)):
    store = _chat_results()
    if store.has_pending((owner_namespace(owner), body.chat_id)):
        raise HTTPException(423, "Recover or finish the current response before compacting this conversation")
    issue = await _model_readiness_issue()
    if issue is not None:
        raise HTTPException(409, issue[1])
    # The ingress serializes this mutation with chat admission, including other
    # dashboard processes and clients. Never invoke native sessions.compact
    # directly here: that RPC may abort an active run.
    return await _chat_context_request(body, compact=True)


@router.post("/chat/result")
async def pixel_chat_result(body: ChatResultRequest, owner: str = Depends(verify_api_key)):
    """Read an owner's attempt without resubmitting any model request."""
    store = _chat_results()
    key = (owner_namespace(owner), body.chat_id, body.request_id)
    row = _result_state(store, key)
    if row is None:
        return {"state": "unknown", "events": ""}
    if row["state"] == "unresolved":
        activity = await pixel_chat_activity(ChatCancelRequest(chat_id=body.chat_id))
        if activity["state"] == "terminal":
            store.finish(key, "interrupted")
            row = store.get(key)
    events = b"" if row["state"] == "active" else b"".join(chunk["data"] for chunk in store.chunks(key))
    return {"state": row["state"], "events": events.decode("utf-8", errors="replace")}


class PixelAccessChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mode: Literal["sandboxed", "full-access"]
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: bool


class PixelAccessStatus(BaseModel):
    # Project only the public mode contract; host credentials and receipts never
    # cross into browser state, even if the upstream gains new fields.
    model_config = ConfigDict(extra="ignore", strict=True)
    available: bool
    surface: Literal["linux-systemd", "wsl-systemd", "linux", "darwin", "windows"]
    configured_mode: Literal["sandboxed", "full-access", "unknown"]
    effective_mode: Literal["sandboxed", "full-access", "unknown"]
    runtime_verified: bool
    revision: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    busy: bool
    pending: bool
    reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,95}$")
    scope: Literal["owner-host"]


def _access_projection(value):
    try:
        status = PixelAccessStatus.model_validate(value)
        if (status.runtime_verified != (status.effective_mode != "unknown")
                or status.runtime_verified and (not status.available or status.pending
                    or status.effective_mode != status.configured_mode)):
            raise ValueError("inconsistent runtime proof")
        return status.model_dump()
    except (ValueError, TypeError):
        raise HTTPException(status_code=502, detail="Pixel access status could not be verified") from None


@router.get("/access-mode", dependencies=[Depends(verify_api_key)])
async def pixel_access_status():
    try:
        return _access_projection(await request_agent_json("GET", "/v1/pixel/access-mode", timeout=30.0))
    except AgentClientError:
        raise HTTPException(status_code=503, detail="Pixel access service is unavailable") from None


@router.post("/access-mode", dependencies=[Depends(verify_api_key)])
async def pixel_access_change(change: PixelAccessChange):
    if change.mode == "full-access" and not change.confirmed:
        raise HTTPException(status_code=400, detail="Confirm the Full Access risk before enabling it")
    try:
        value = await request_agent_json("POST", "/v1/pixel/access-mode", payload=change.model_dump(), timeout=330.0)
        return _access_projection(value)
    except AgentClientError:
        raise HTTPException(status_code=409, detail="The access change was not verified. Refresh status before retrying or restoring safer mode.") from None


async def _host_model_status() -> dict[str, object] | None:
    try:
        status = await request_agent_json("GET", "/v1/model/status", timeout=2.0)
    except AgentClientError:
        return None
    return status if isinstance(status, dict) else None


async def _local_inference_issue(host_status: object) -> str | None:
    # Gateway discovery proves the agent exists, not that its model server is
    # reachable. Probe the configured host runtime without spending tokens.
    runtime = _active_runtime_projection(host_status)
    if runtime and runtime.get("source") == "remote-provider":
        return None
    if (read_live_env_value("LLM_BACKEND").lower() != "lemonade"
            or read_live_env_value("AMD_INFERENCE_LOCATION").lower() != "host"):
        return None
    try:
        telemetry = await request_agent_json("GET", "/v1/llm/status", timeout=3.0)
        if (isinstance(telemetry, dict)
                and telemetry.get("schema_version") == "ods.host-llm-status.v1"
                and isinstance(telemetry.get("health"), dict)
                and telemetry["health"].get("status") == "ok"):
            return None
    except AgentHTTPError as error:
        # Linux/WSL hosts do not implement Windows-native telemetry. Its
        # absence cannot declare their otherwise discoverable agent offline.
        if error.status_code == 501:
            return None
    except AgentClientError:
        pass
    return "The local model runtime is unavailable. Restore it in Models before sending another task."


def _model_readiness_issue_from_status(status: object) -> tuple[str, str] | None:
    if isinstance(status, dict) and status.get("modelTransactionPending") is True:
        return "model_switching", _MODEL_SWITCH_DETAIL
    switching = (
        isinstance(status, dict)
        and status.get("activeOperation") == "model_activation"
        and bool(status.get("lifecycleActive") or status.get("activeOperation"))
    )
    if switching:
        return "model_switching", _MODEL_SWITCH_DETAIL
    return None


def _model_support_from_status(status: object) -> dict[str, str] | None:
    """Return fixed advisory metadata without turning model quality into access.

    ODS model qualification is a recommendation signal. Pixel's brokers,
    approvals, and typed capabilities enforce safety independently of model
    intelligence, so an unqualified model remains usable and testable.
    """
    if isinstance(status, dict) and status.get("activeAgentViable") is False:
        return {"tier": "adaptive", "detail": _MODEL_ADAPTIVE_DETAIL}
    return None


async def _model_readiness_issue() -> tuple[str, str] | None:
    """Return a host-proven transition or an unverified Lemonade route.

    A failed host lifecycle probe alone does not take down the Pixel edge.
    A recorded Lemonade route does require live identity proof before chat.
    Model quality metadata remains advisory, not an access restriction.
    """
    return await _model_readiness_issue_for_status(await _host_model_status())


def _active_runtime_projection(status: object) -> dict[str, object] | None:
    if not isinstance(status, dict):
        return None
    runtime = status.get("activeRuntime")
    if isinstance(runtime, dict) and runtime.get("source") == "local-switchboard":
        expected = {"source", "model", "contextLength"}
        if (
            set(runtime) == expected
            and isinstance(runtime.get("model"), str)
            and 1 <= len(runtime["model"]) <= 256
            and type(runtime.get("contextLength")) is int
            and 1 <= runtime["contextLength"] <= 10_000_000
        ):
            return {key: runtime[key] for key in expected}
        return None
    if isinstance(runtime, dict) and runtime.get("source") == "external-host":
        expected = {"source", "model"}
        if (
            expected <= set(runtime) <= expected | {"contextLength"}
            and isinstance(runtime.get("model"), str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,255}", runtime["model"])
            and "://" not in runtime["model"]
            and (
                "contextLength" not in runtime
                or type(runtime["contextLength"]) is int
                and 1 <= runtime["contextLength"] <= 10_000_000
            )
        ):
            return {key: runtime[key] for key in expected | {"contextLength"} if key in runtime}
        return None
    expected = {"source", "model", "contextLength", "maxTokens", "reasoning"}
    if (
        not isinstance(runtime, dict)
        or not expected <= set(runtime) <= expected | {"routeFingerprint"}
        or runtime.get("source") != "remote-provider"
        or not isinstance(runtime.get("model"), str)
        or not 1 <= len(runtime["model"]) <= 256
        or type(runtime.get("contextLength")) is not int
        or not 4096 <= runtime["contextLength"] <= 10_000_000
        or type(runtime.get("maxTokens")) is not int
        or not 1 <= runtime["maxTokens"] <= runtime["contextLength"]
        or type(runtime.get("reasoning")) is not bool
        or "routeFingerprint" in runtime and (
            not isinstance(runtime["routeFingerprint"], str)
            or re.fullmatch(r"[a-f0-9]{64}", runtime["routeFingerprint"]) is None
        )
    ):
        return None
    return {key: runtime[key] for key in expected | {"routeFingerprint"} if key in runtime}


async def _verified_external_host_runtime(host_status: object) -> dict[str, object] | None:
    """Identify a fixed external model from a live probe, never .env alone.

    This is a status identity, not a model-switch or agent-quality proof. Do not
    expose the configured origin, credentials, or provider response body.
    """
    if (
        not isinstance(host_status, dict)
        or host_status.get("activeRuntime") is not None
        or os.environ.get("LLM_BACKEND", "").strip().casefold() != "external"
        or read_live_env_value("LLM_BACKEND").strip().casefold() != "external"
        or read_live_env_value("ODS_MODEL_SWITCHBOARD").strip().casefold() != "observe"
        or read_live_env_value("EXTERNAL_LLM_PROVIDER").strip().casefold() != "openai-compatible"
    ):
        return None
    expected = read_live_env_value("EXTERNAL_LLM_MODEL").strip()
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,255}", expected) is None
        or "://" in expected
    ):
        return None
    try:
        loaded = await asyncio.wait_for(get_loaded_model(), timeout=3.0)
    except (asyncio.TimeoutError, httpx.HTTPError, OSError, ValueError, TypeError):
        return None
    if loaded != expected:
        return None
    runtime: dict[str, object] = {"source": "external-host", "model": loaded}
    try:
        context = await asyncio.wait_for(get_llama_context_size(loaded), timeout=3.0)
    except (asyncio.TimeoutError, httpx.HTTPError, OSError, ValueError, TypeError):
        context = None
    if type(context) is int and 1 <= context <= 10_000_000:
        runtime["contextLength"] = context
    return _active_runtime_projection({"activeRuntime": runtime})


def _model_identity_tokens(value: str | None) -> set[str]:
    """Compare a Lemonade ID with the equivalent GGUF basename, not a path."""
    if not isinstance(value, str) or not value.strip():
        return set()
    name = Path(value.strip()).name.casefold()
    tokens = {name}
    if name.startswith("extra."):
        tokens.add(name[6:])
    for token in tuple(tokens):
        if token.endswith(".gguf"):
            tokens.add(token[:-5])
    return tokens


async def _model_readiness_issue_for_status(status: object) -> tuple[str, str] | None:
    issue = _model_readiness_issue_from_status(status)
    if issue is not None:
        return issue
    runtime = _active_runtime_projection(status)
    if (runtime is None or runtime.get("source") != "local-switchboard"
            or read_live_env_value("LLM_BACKEND").strip().casefold() != "lemonade"):
        return None
    try:
        loaded = await asyncio.wait_for(get_loaded_model(), timeout=3.0)
    except Exception as exc:
        # Probe failures cannot validate a recorded external route. Do not log
        # exception text; it may contain the private backend origin or key.
        logger.warning("Pixel Lemonade identity probe failed (%s)", type(exc).__name__)
        return "model_unavailable", _MODEL_IDENTITY_DETAIL
    if not (_model_identity_tokens(runtime["model"]) & _model_identity_tokens(loaded)):
        return "model_unavailable", _MODEL_IDENTITY_DETAIL
    return None


async def _model_activation_in_progress() -> bool:
    """Compatibility wrapper retained for focused lifecycle callers/tests."""
    issue = await _model_readiness_issue()
    return issue is not None and issue[0] == "model_switching"


async def _bounded_response_bytes(response: httpx.Response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise ValueError("response too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/status", dependencies=[Depends(verify_api_key)])
async def pixel_status() -> dict[str, object]:
    """Return a fixed, nonsecret Pixel availability projection."""
    config = _pixel_config()
    if config is None:
        return {"available": False, "model": None, "detail": "Pixel is not enabled"}
    host_status = await _host_model_status()
    readiness_issue = await _model_readiness_issue_for_status(host_status)
    if readiness_issue is not None:
        state, detail = readiness_issue
        return {
            "available": False,
            "model": None,
            "state": state,
            "detail": detail,
        }
    edge_url, key = config
    try:
        timeout = httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            async with client.stream(
                "GET",
                f"{edge_url}/v1/models",
                headers=_edge_headers(key, accept="application/json"),
            ) as response:
                if response.status_code != 200:
                    return {"available": False, "model": None, "detail": "Pixel edge is unavailable"}
                if not response.headers.get("content-type", "").lower().startswith("application/json"):
                    return {"available": False, "model": None, "detail": "Pixel edge returned an invalid response"}
                raw = await _bounded_response_bytes(response, _MAX_STATUS_BYTES)
        payload = json.loads(raw)
        models = payload.get("data") if isinstance(payload, dict) else None
        available = isinstance(models, list) and any(
            isinstance(item, dict) and item.get("id") == _MODEL for item in models
        )
        result: dict[str, object] = {
            "available": available,
            "model": _MODEL if available else None,
            "detail": "Owner agent ready" if available else "pixel/default is unavailable",
        }
        if available:
            inference_issue = await _local_inference_issue(host_status)
            if inference_issue:
                return {"available": False, "model": None, "state": "model_unavailable", "detail": inference_issue}
        runtime = _active_runtime_projection(host_status)
        if available and runtime is None:
            runtime = await _verified_external_host_runtime(host_status)
        if available and runtime is not None:
            result["runtime"] = runtime
        model_support = _model_support_from_status(host_status)
        if available and model_support is not None:
            result["modelSupport"] = model_support
        return result
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        # Exception text and request objects can contain upstream credentials.
        # Retain the failure phase/type without logging those sensitive values.
        logger.warning("Pixel edge status request failed (%s)", type(exc).__name__)
        return {"available": False, "model": None, "detail": "Pixel edge is unavailable"}
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
        return {"available": False, "model": None, "detail": "Pixel edge returned an invalid response"}


@router.get("/ops/{job_id}", dependencies=[Depends(verify_api_key)])
async def pixel_operations_status(job_id: str, plan_hash: str) -> dict[str, object]:
    """Return only a host-verified, nonsecret Operations status receipt."""
    if _OPS_JOB_ID.fullmatch(job_id) is None or _OPS_PLAN_HASH.fullmatch(plan_hash) is None:
        raise HTTPException(status_code=400, detail="Invalid Pixel Operations receipt")
    try:
        value = await request_agent_json(
            "GET",
            "/v1/pixel/ops-status",
            params={"job_id": job_id, "plan_hash": plan_hash},
            timeout=7.0,
        )
    except AgentClientError as exc:
        raise HTTPException(status_code=503, detail="Pixel Operations status is unavailable") from exc
    expected = {
        "schemaVersion",
        "kind",
        "jobId",
        "planHash",
        "status",
        "riskTier",
        "approvalRequired",
        "updatedAt",
        "approvalCommand",
    }
    command = value.get("approvalCommand")
    if (
        set(value) != expected
        or value.get("schemaVersion") != 1
        or value.get("kind") != "ods-pixel-operations-status"
        or value.get("jobId") != job_id
        or value.get("planHash") != plan_hash
        or value.get("status") not in _OPS_STATUSES
        or not isinstance(value.get("riskTier"), str)
        or re.fullmatch(r"[a-z][a-z-]{0,31}", value["riskTier"]) is None
        or not isinstance(value.get("approvalRequired"), bool)
        or not isinstance(value.get("updatedAt"), str)
        or not 1 <= len(value["updatedAt"]) <= 64
        or (command is not None and (not isinstance(command, str) or not 1 <= len(command) <= 4096))
    ):
        raise HTTPException(status_code=502, detail="Pixel Operations returned an invalid status")
    return {key: value[key] for key in expected}


def _error_event(message: str) -> bytes:
    payload = {"error": {"message": message, "type": "pixel_dashboard_error"}}
    return f"data: {json.dumps(payload)}\n\n".encode()


async def _cancel_edge_run(edge_url: str, key: str, chat_id: str) -> bool:
    """Best-effort cancellation over the fixed authenticated internal edge."""
    timeout = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            async with client.stream(
                "POST",
                f"{edge_url}/v1/chat/cancel",
                json={"user": chat_id},
                headers=_edge_headers(key, accept="application/json"),
            ) as response:
                if response.status_code != 200:
                    return False
                if not response.headers.get("content-type", "").lower().startswith(
                    "application/json"
                ):
                    return False
                raw = await _bounded_response_bytes(response, 1024)
        parsed = json.loads(raw)
        return (
            isinstance(parsed, dict)
            and set(parsed) == {"aborted"}
            and parsed.get("aborted") is True
        )
    except (
        httpx.HTTPError,
        asyncio.TimeoutError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
        TypeError,
    ):
        return False


@router.post("/chat/cancel")
async def pixel_chat_cancel(body: ChatCancelRequest, owner: str = Depends(verify_api_key)) -> dict[str, bool]:
    """Cancel only the active run for this validated dashboard conversation.

    The explicit endpoint makes the owner's Stop action independent of HTTP
    disconnect propagation. The stream finalizer retains the same cancellation
    call as an idempotent fallback for tab closes and network failures.
    """
    config = _pixel_config()
    if config is None:
        raise HTTPException(status_code=503, detail="Pixel is not enabled")
    edge_url, key = config
    if body.request_id is not None:
        store = _chat_results()
        identity = (owner_namespace(owner), body.chat_id, body.request_id)
        row = _result_state(store, identity)
        # A late Stop for a completed/unknown attempt must not stop a newer run.
        if row is None or row["state"] not in {"active", "unresolved"}:
            return {"aborted": False}
        if identity[:2] in _result_stops:
            return {"aborted": False}
        _result_stops.add(identity[:2])
        try:
            aborted = await _cancel_edge_run(edge_url, key, body.chat_id)
            if aborted:
                if store.get(identity)["state"] == "complete":
                    return {"aborted": False}
                _result_abort_ack.add(identity)
                task = _result_tasks.get(identity)
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                if store.get(identity)["state"] == "complete":
                    return {"aborted": False}
                store.finish(identity, "cancelled")
            return {"aborted": aborted}
        finally:
            _result_abort_ack.discard(identity)
            _result_stops.discard(identity[:2])
    if isinstance(owner, str) and _result_store is not None and _result_store.has_pending((owner_namespace(owner), body.chat_id)):
        return {"aborted": False}
    return {"aborted": await _cancel_edge_run(edge_url, key, body.chat_id)}


@router.post("/chat/activity", dependencies=[Depends(verify_api_key)])
async def pixel_chat_activity(body: ChatCancelRequest) -> dict[str, str]:
    """One authenticated chat lookup; global activity never supplies its state."""
    config = _pixel_config()
    if config is None:
        return {"state": "unknown"}
    edge_url, key = config
    try:
        timeout = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
            async with client.stream("POST", f"{edge_url}/v1/chat/activity",
                                     json={"user": body.chat_id},
                                     headers=_edge_headers(key, accept="application/json")) as response:
                if response.status_code != 200 or not response.headers.get("content-type", "").lower().startswith("application/json"):
                    return {"state": "unknown"}
                raw = await _bounded_response_bytes(response, 1024)
        parsed = json.loads(raw)
        if (isinstance(parsed, dict) and set(parsed) == {"state"}
                and isinstance(parsed["state"], str) and parsed["state"] in {"active", "terminal", "unknown"}):
            return {"state": parsed["state"]}
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError):
        pass
    return {"state": "unknown"}


class _ClientDisconnected(Exception):
    """The dashboard consumer left while Pixel was still producing a turn."""


async def _retained_chat_stream(request, body, owner):
    store = _chat_results()
    identity = (owner_namespace(owner), body.chat_id, body.request_id)
    fingerprint_input = [m.model_dump() for m in body.messages]
    if body.history_snapshot is not None:
        fingerprint_input = {"messages": fingerprint_input, "history_snapshot": body.history_snapshot.model_dump()}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_input, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
    existing = store.get(identity)
    direct_reply = None
    if existing is None:
        if asks_display_name(body.messages):
            direct_reply = display_name_stream(await confirmed_display_name())
        else:
            config = _pixel_config()
            if config is None:
                raise HTTPException(status_code=503, detail="Pixel is not enabled")
            issue = await _model_readiness_issue()
            if issue is not None:
                raise HTTPException(status_code=409, detail=issue[1])
            messages = await messages_with_identity(body.messages)
    try:
        if identity[:2] in _result_stops:
            raise ResultConflict("Stop is still being confirmed")
        created = store.reserve(identity, fingerprint)
    except ResultConflict as exc:
        raise HTTPException(status_code=423, detail=str(exc)) from None
    except ResultCapacity as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from None
    if created and direct_reply is not None:
        try:
            store.complete_direct(identity, direct_reply)
        except Exception as exc:
            # No background agent owns this attempt. Never leave it apparently
            # running when the local reply could not be committed.
            try:
                store.finish(identity, "interrupted")
            except Exception:
                logger.error("Could not mark local identity reply interrupted")
            logger.warning("Local identity reply persistence failed (%s)", type(exc).__name__)
            raise HTTPException(503, "Could not save the reply. Please retry.") from None
    elif created:
        begin_pixel_stream()
        task = asyncio.create_task(_produce_retained_result(store, identity, body, config, messages))
        _result_tasks[identity] = task
        def release(finished):
            _result_tasks.pop(identity, None)
            end_pixel_stream()
            if not finished.cancelled() and finished.exception() is not None:
                logger.error("Pixel result persistence failed (%s)", type(finished.exception()).__name__)
        task.add_done_callback(release)

    async def subscribe():
        after = -1
        last_sent = time.monotonic()
        while True:
            # Snapshot terminal state before yielding any bytes. Sending a chunk
            # can suspend this subscriber while the producer commits its tail.
            # If it was active, take another snapshot before deciding to close.
            row = _result_state(store, identity)
            for chunk in store.chunks(identity, after):
                after = chunk["sequence"]
                yield chunk["data"]
                last_sent = time.monotonic()
            if row is None or row["state"] != "active":
                return
            if await request.is_disconnected():
                return
            if time.monotonic() - last_sent >= _STREAM_KEEPALIVE_SECONDS:
                # A CPU-backed local model can spend minutes in prompt prefill.
                # Keep the subscriber alive without inventing an answer or
                # persisting transport-only comments in the result receipt.
                yield _STREAM_KEEPALIVE
                last_sent = time.monotonic()
            # Subscriber disposal never cancels the independent bounded producer.
            await asyncio.sleep(_CLIENT_DISCONNECT_POLL_SECONDS)

    return StreamingResponse(subscribe(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no",
    })


async def _produce_retained_result(store, identity, body, config, messages):
    edge_url, key = config
    done_seen = False
    answer_seen = False
    empty_done_seen = False
    terminal_error_seen = False
    cancelled = False
    failed = False
    stopped = False
    rejected = False
    try:
        timeout = httpx.Timeout(connect=5.0, read=_CHAT_STREAM_TIMEOUT_SECONDS, write=30.0, pool=5.0)
        async with async_timeout(_CHAT_STREAM_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False) as client:
                async with client.stream("POST", f"{edge_url}/v1/chat/completions",
                        json=_edge_chat_body(body, messages),
                        headers=_edge_headers(key, accept="text/event-stream")) as upstream:
                    rejected = 400 <= upstream.status_code < 500
                    if upstream.status_code != 200 or not upstream.headers.get("content-type", "").lower().startswith("text/event-stream"):
                        raise ValueError("Invalid upstream stream")
                    buffered = bytearray()
                    async for chunk in upstream.aiter_bytes():
                        buffered.extend(chunk)
                        while b"\n" in buffered:
                            newline = buffered.index(b"\n")
                            line = bytes(buffered[:newline + 1])
                            del buffered[:newline + 1]
                            if len(line.rstrip(b"\r\n")) > _MAX_SSE_LINE_BYTES:
                                raise ResultCapacity("SSE line limit")
                            stripped = line.rstrip(b"\r\n")
                            if stripped.startswith(b"data: ") and stripped != b"data: [DONE]":
                                try:
                                    event = json.loads(stripped[6:])
                                except (json.JSONDecodeError, UnicodeDecodeError):
                                    event = None
                                if isinstance(event, dict):
                                    if "error" in event:
                                        # A syntactically terminal SSE stream can still be
                                        # a failed attempt. Keep its sanitized error bytes
                                        # for replay, but never publish it as complete.
                                        terminal_error_seen = True
                                        failed = True
                                    choices = event.get("choices")
                                    for choice in choices if isinstance(choices, list) else []:
                                        if not isinstance(choice, dict):
                                            continue
                                        for field in ("delta", "message"):
                                            payload = choice.get(field)
                                            if isinstance(payload, dict) and isinstance(payload.get("content"), str) \
                                                    and payload["content"]:
                                                answer_seen = True
                            if stripped == b"data: [DONE]":
                                if not answer_seen and not terminal_error_seen:
                                    # Live Pixel Edge cancellations can end with only
                                    # [DONE]. The host session reports zero output and
                                    # aborted, so a syntactic DONE is not a user answer.
                                    # Do not persist it as a successful receipt.
                                    terminal_error_seen = True
                                    empty_done_seen = True
                                    failed = True
                                    store.append(identity, _error_event("Pixel returned no answer. Try again.")
                                                 + b"data: [DONE]\n\n", terminal=True)
                                else:
                                    store.append(identity, line)
                                done_seen = True
                                break
                            store.append(identity, line)
                        if done_seen:
                            break
                        if len(buffered) > _MAX_SSE_LINE_BYTES:
                            raise ResultCapacity("SSE line limit")
                    if not done_seen:
                        failed = True
    except asyncio.CancelledError:
        cancelled = identity in _result_abort_ack
        failed = not cancelled
    except Exception as exc:
        failed = True
        logger.warning("Pixel retained stream failed (%s)", type(exc).__name__)
    finally:
        # Keep this conversation reserved until cancellation has finished. A late
        # native cancellation must never target the next attempt in this chat.
        if not done_seen and not cancelled and not rejected:
            try:
                stopped = await asyncio.wait_for(_cancel_edge_run(edge_url, key, body.chat_id), _CLIENT_CANCEL_TIMEOUT_SECONDS)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        try:
            if not done_seen:
                text = ("Pixel was stopped." if cancelled else
                        "Portal did not accept this turn. Check the conversation's context status before continuing." if rejected else
                        "Pixel could not complete the response. Check saved work before continuing.")
                store.append(identity, _error_event(text) + b"data: [DONE]\n\n", terminal=True)
        finally:
            state = (
                "complete" if done_seen and not terminal_error_seen
                else "cancelled" if cancelled
                # A DONE-only frame can overtake the Edge Stop acknowledgment.
                # Keep the attempt reserved until Stop resolves; an acknowledged
                # abort then commits cancelled, while an unacknowledged native
                # run must remain unresolved instead of admitting a successor.
                else "unresolved" if empty_done_seen and identity[:2] in _result_stops
                else "interrupted" if rejected or terminal_error_seen or failed and stopped
                else "unresolved" if failed
                else "complete"
            )
            store.finish(identity, state)


def _edge_chat_body(body, messages):
    result = {"model": _MODEL, "stream": True, "user": body.chat_id, "messages": messages}
    if body.history_snapshot is not None:
        result["history_snapshot"] = body.history_snapshot.model_dump()
        result["request_id"] = body.request_id
    return result


async def _iter_upstream_chunks(
    upstream: httpx.Response,
    request: Request,
    can_emit_keepalive: Callable[[], bool],
) -> AsyncIterator[bytes]:
    """Yield upstream bytes while promptly observing a silent client exit."""
    iterator = upstream.aiter_bytes().__aiter__()
    pending: asyncio.Task[bytes] | None = None
    last_sent = time.monotonic()
    try:
        while True:
            pending = asyncio.create_task(anext(iterator))
            while not pending.done():
                done, _ = await asyncio.wait(
                    {pending},
                    timeout=_CLIENT_DISCONNECT_POLL_SECONDS,
                )
                if done:
                    break
                if await request.is_disconnected():
                    raise _ClientDisconnected
                # A comment is safe only between complete SSE lines. The
                # caller may be holding an upstream fragment without a newline;
                # injecting a comment there would corrupt that data line.
                if can_emit_keepalive() and time.monotonic() - last_sent >= _STREAM_KEEPALIVE_SECONDS:
                    yield _STREAM_KEEPALIVE
                    last_sent = time.monotonic()
            try:
                chunk = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield chunk
            last_sent = time.monotonic()
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()


@router.post("/chat/stream")
async def pixel_chat_stream(request: Request, body: ChatStreamRequest, owner: str = Depends(verify_api_key)) -> StreamingResponse:
    """Forward one bounded chat over authenticated, unbuffered SSE."""
    if body.request_id is not None:
        return await _retained_chat_stream(request, body, owner)
    if isinstance(owner, str) and _result_store is not None and _result_store.has_pending((owner_namespace(owner), body.chat_id)):
        raise HTTPException(status_code=423, detail="Recover or stop the retained attempt before starting another turn")
    if asks_display_name(body.messages):
        reply = display_name_stream(await confirmed_display_name())
        async def direct_stream():
            yield reply
        return StreamingResponse(direct_stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no",
        })
    config = _pixel_config()
    if config is None:
        raise HTTPException(status_code=503, detail="Pixel is not enabled")
    readiness_issue = await _model_readiness_issue()
    if readiness_issue is not None:
        _state, detail = readiness_issue
        raise HTTPException(status_code=409, detail=detail)
    edge_url, key = config
    edge_body = _edge_chat_body(body, await messages_with_identity(body.messages))

    # Pixel Edge is capped at 33 minutes; retain one bounded minute of outer
    # headroom so this bridge never aborts a valid CPU-only first turn first.
    timeout = httpx.Timeout(
        connect=5.0,
        read=_CHAT_STREAM_TIMEOUT_SECONDS,
        write=30.0,
        pool=5.0,
    )
    client = None
    upstream_context = None
    entered = False
    released = False
    done_seen = False

    async def release_stream():
        nonlocal released
        if released:
            return
        released = True
        try:
            try:
                if entered and not done_seen:
                    cancel_task = asyncio.create_task(_cancel_edge_run(edge_url, key, body.chat_id))
                    try:
                        await asyncio.wait_for(asyncio.shield(cancel_task), timeout=_CLIENT_CANCEL_TIMEOUT_SECONDS)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        pass
            finally:
                try:
                    if entered:
                        await upstream_context.__aexit__(None, None, None)
                finally:
                    if client is not None:
                        await client.aclose()
        finally:
            end_pixel_stream()

    if not try_begin_pixel_stream():
        raise HTTPException(status_code=429, detail="Pixel stream capacity is busy; retry shortly")

    try:
        client = httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=False)
        upstream_context = client.stream(
            "POST",
            f"{edge_url}/v1/chat/completions",
            json=edge_body,
            headers=_edge_headers(key, accept="text/event-stream"),
        )
        try:
            upstream = await upstream_context.__aenter__()
            entered = True
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.warning("Pixel edge stream connection failed (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="Pixel stream is unavailable") from exc
        if upstream.status_code == 409:
            # Pixel Edge uses 409 while its managed runtime is transitioning.
            # Preserve the actionable retry class without reflecting any
            # upstream response body into the owner-facing dashboard.
            raise HTTPException(status_code=409, detail=_MODEL_SWITCH_DETAIL)
        if upstream.status_code != 200:
            raise HTTPException(status_code=502, detail="Pixel request was rejected")
        if not upstream.headers.get("content-type", "").lower().startswith("text/event-stream"):
            raise HTTPException(status_code=502, detail="Pixel returned an invalid stream")
    except BaseException:
        await release_stream()
        raise

    async def stream() -> AsyncIterator[bytes]:
        nonlocal done_seen
        try:
            async with async_timeout(_CHAT_STREAM_TIMEOUT_SECONDS):
                buffered = bytearray()
                async for chunk in _iter_upstream_chunks(upstream, request, lambda: not buffered):
                    buffered.extend(chunk)
                    while True:
                        newline = buffered.find(b"\n")
                        if newline < 0:
                            break
                        line = bytes(buffered[: newline + 1])
                        del buffered[: newline + 1]
                        if len(line.rstrip(b"\r\n")) > _MAX_SSE_LINE_BYTES:
                            yield _error_event("Pixel stream exceeded its safety limit")
                            if not done_seen:
                                yield b"data: [DONE]\n\n"
                            return
                        yield line
                        if line.rstrip(b"\r\n") == b"data: [DONE]":
                            done_seen = True
                    if len(buffered) > _MAX_SSE_LINE_BYTES:
                        yield _error_event("Pixel stream exceeded its safety limit")
                        if not done_seen:
                            yield b"data: [DONE]\n\n"
                        return
                if buffered:
                    # The final upstream line may lack a newline. Forward it
                    # terminated so it cannot fuse with the appended [DONE],
                    # and recognize a bare trailing marker as a real [DONE].
                    if buffered.rstrip(b"\r\n") == b"data: [DONE]":
                        done_seen = True
                    yield bytes(buffered) + b"\n"
        except _ClientDisconnected:
            return
        except (GeneratorExit, asyncio.CancelledError):
            raise
        except (httpx.HTTPError, asyncio.TimeoutError):
            yield _error_event("Pixel stream is unavailable")
        except Exception:
            yield _error_event("Pixel stream failed")
        finally:
            await release_stream()
        if not done_seen:
            yield b"data: [DONE]\n\n"

    class OwnedStreamResponse(StreamingResponse):
        async def __call__(self, scope, receive, send):
            try:
                await super().__call__(scope, receive, send)
            finally:
                # Sending headers can fail before the iterator ever starts.
                await release_stream()

    return OwnedStreamResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
