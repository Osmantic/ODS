"""Read-only remote-provider lifecycle status for Dashboard/CLI consumers."""

from __future__ import annotations

import ipaddress
import hashlib
import json
import logging
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException

from config import DATA_DIR
from host_agent_client import (
    AgentHTTPError,
    AgentProtocolError,
    AgentUnavailable,
    async_request_json as async_request_agent_json,
)
from security import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["remote-provider"])

ROUTE_STATE_SCHEMA = "ods.remote-routing-state.v1"
ACTIVATION_STATE_SCHEMA = "ods.remote-provider-activation-state.v1"
EGRESS_URL = os.environ.get("REMOTE_PROVIDER_EGRESS_URL", "http://remote-provider-egress:8091")
EGRESS_TIMEOUT_SECONDS = 3.0
EGRESS_PROBE_TIMEOUT_SECONDS = 60.0
LIFECYCLE_APPLY_TIMEOUT_SECONDS = 1800.0
PEER_PROXY_TIMEOUT_SECONDS = 30.0
PEER_PROXY_LOAD_TIMEOUT_SECONDS = 2700.0
PEER_PROXY_RESPONSE_MAX_BYTES = 2_000_000
PEER_REDACTED = "[REDACTED]"

_SAFE_PROVIDER_KEYS = {
    "capability", "baseUrl", "model", "transport",
    "contextLength", "maxTokens", "reasoning",
}
_SAFE_PROJECTION_KEYS = {"publicModel", "gateway", "egressBaseUrl", "consumerRoute"}
_SSH_SUPERVISOR_SCHEMA = "ods.remote-provider-ssh-supervisor-plan.v1"
_EGRESS_PROBE_SCHEMA = "ods.remote-provider-egress-probe.v1"
_PROOF_RECORD_SCHEMA = "ods.remote-provider-proof-record.v1"
_SSH_CONTROL_TUNNEL_HOST = "remote-provider-ssh-tunnel"
_SSH_CONTROL_TUNNEL_PORT = 18092
_LOCAL_HOSTNAMES = {
    "gateway.docker.internal",
    "host.docker.internal",
    "localhost",
    "localhost.localdomain",
}
_EGRESS_ERROR_MESSAGES = {
    "invalid_route": "Remote provider route is invalid",
    "invalid_route_state": "Remote provider route state is invalid",
    "missing_provider_secret": "Remote provider secret is missing",
    "provider_http_error": "Remote provider probe returned an HTTP error",
    "provider_probe_too_large": "Remote provider probe response exceeded the safety limit",
    "provider_resolution_empty": "Remote provider DNS resolution returned no addresses",
    "provider_resolution_rejected": "Remote provider DNS resolution was rejected",
    "provider_unreachable": "Remote provider is unreachable",
    "remote_route_disabled": "Remote provider route is disabled",
    "route_policy_rejected": "Remote provider route was rejected by policy",
    "ssh_tunnel_not_ready": "SSH tunnel is not ready",
    "transport_probe_unavailable": "Remote provider test is unavailable for this transport",
}


def _state_path() -> Path:
    override = os.environ.get("ODS_REMOTE_PROVIDER_ROUTE_PATH")
    if override:
        return Path(override)
    return Path(DATA_DIR) / "remote-provider" / "routing-state.json"


def _activation_path() -> Path:
    override = os.environ.get("ODS_REMOTE_PROVIDER_ACTIVATION_PATH")
    if override:
        return Path(override)
    return Path(DATA_DIR) / "remote-provider" / "activation-public.json"


def _route_fingerprint(route_state: Mapping[str, Any]) -> str:
    provider = route_state.get("provider")
    provider = provider if isinstance(provider, Mapping) else {}
    identity = {
        key: provider.get(key)
        for key in (
            "transport", "baseUrl", "model", "contextLength", "maxTokens", "reasoning",
        )
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _read_activation() -> dict[str, Any]:
    path = _activation_path()
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return {"valid": False, "active": False, "proven": False, "reason": "unsafe"}
        if metadata.st_size > 65536:
            return {"valid": False, "active": False, "proven": False, "reason": "oversized"}
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"valid": True, "active": False, "proven": False, "reason": "not_activated"}
    except (OSError, ValueError):
        return {"valid": False, "active": False, "proven": False, "reason": "unreadable"}
    if not isinstance(value, Mapping) or value.get("schema") != ACTIVATION_STATE_SCHEMA:
        return {"valid": False, "active": False, "proven": False, "reason": "invalid"}
    model = _safe_text(value.get("model"), max_length=256)
    route_fingerprint = _safe_text(value.get("routeFingerprint"), max_length=64)
    context_length = _safe_int(value.get("contextLength"))
    max_tokens = _safe_int(value.get("maxTokens"))
    reasoning = value.get("reasoning") if type(value.get("reasoning")) is bool else None
    gateway = _safe_text(value.get("gateway"), max_length=64)
    public_model = _safe_text(value.get("publicModel"), max_length=64)
    pixel = _safe_text(value.get("pixel"), max_length=64)
    active = value.get("active") is True
    proven = active and value.get("proven") is True
    contract_valid = bool(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}", model)
        and type(context_length) is int
        and 16384 <= context_length <= 10_000_000
        and type(max_tokens) is int
        and 1 <= max_tokens <= context_length
        and type(reasoning) is bool
        and gateway == "litellm-cloud"
        and public_model == "ods/current"
        and pixel in {"reconciled", "not_installed"}
        and re.fullmatch(r"[a-f0-9]{64}", route_fingerprint)
    )
    if not active or not proven or not contract_valid:
        return {
            "valid": False,
            "active": active,
            "proven": False,
            "reason": "invalid_activation_contract",
        }
    return {
        "valid": True,
        "active": active,
        "proven": proven,
        "reason": "active_and_proven",
        "gateway": gateway,
        "publicModel": public_model,
        "model": model,
        "routeFingerprint": route_fingerprint,
        "contextLength": context_length,
        "maxTokens": max_tokens,
        "reasoning": reasoning,
        "pixel": pixel,
        "updatedAt": _safe_text(value.get("updatedAt"), max_length=64),
    }


def _activation_matches_host_runtime(
    activation: Mapping[str, Any],
    host_status: Any,
) -> bool:
    if not isinstance(host_status, Mapping) or host_status.get("activeAgentViable") is not True:
        return False
    runtime = host_status.get("activeRuntime")
    if not isinstance(runtime, Mapping) or runtime.get("source") != "remote-provider":
        return False
    return all(
        runtime.get(key) == activation.get(key)
        for key in ("model", "contextLength", "maxTokens", "reasoning")
    )


async def _reconcile_activation_with_host(
    activation: dict[str, Any],
) -> dict[str, Any]:
    """Reject a stale Pixel reconciliation claim using current host custody."""
    if (
        activation.get("valid") is not True
        or activation.get("proven") is not True
        or activation.get("pixel") != "reconciled"
    ):
        return activation
    try:
        host_status = await async_request_agent_json(
            "GET",
            "/v1/model/status",
            timeout=3,
        )
    except (AgentHTTPError, AgentUnavailable, AgentProtocolError):
        return {
            **activation,
            "valid": False,
            "proven": False,
            "reason": "consumer_status_unavailable",
            "pixel": "unverified",
        }
    if _activation_matches_host_runtime(activation, host_status):
        return activation
    return {
        **activation,
        "valid": False,
        "proven": False,
        "reason": "consumer_drift",
        "pixel": "drifted",
    }


def _peer_token_path() -> Path:
    return Path(DATA_DIR) / "remote-provider" / "secrets" / "peer-token"


def _safe_provider(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    clean: dict[str, Any] = {}
    for key in _SAFE_PROVIDER_KEYS:
        item = value.get(key)
        if isinstance(item, str):
            clean[key] = item
        elif key in {"contextLength", "maxTokens"} and type(item) is int:
            clean[key] = item
        elif key == "reasoning" and type(item) is bool:
            clean[key] = item
    return clean


def _safe_string_map(value: Any, keys: set[str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        key: item for key in keys
        if isinstance((item := value.get(key)), str)
    }


def _safe_peer_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    clean: dict[str, str] = {}
    control_base_url = _safe_text(value.get("controlBaseUrl"), max_length=512)
    transport = _safe_text(value.get("transport"), max_length=32)
    if control_base_url:
        clean["controlBaseUrl"] = control_base_url
    if transport:
        clean["transport"] = transport
    return clean


def _safe_text(value: Any, *, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    return text[:max_length]


def _safe_int(value: Any) -> int | None:
    return value if type(value) is int else None


def _safe_probe_receipt(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    resolution = value.get("resolution")
    clean_resolution = None
    if isinstance(resolution, Mapping):
        clean_resolution = {
            "ok": bool(resolution.get("ok")),
            "addressCount": _safe_int(resolution.get("addressCount")),
        }
    receipt: dict[str, Any] = {
        "schema": _safe_text(value.get("schema"), max_length=64),
        "ok": bool(value.get("ok")),
        "verifiedAt": _safe_text(value.get("verifiedAt"), max_length=64),
        "endpoint": _safe_text(value.get("endpoint"), max_length=32),
        "httpStatus": _safe_int(value.get("httpStatus")),
        "modelCount": _safe_int(value.get("modelCount")),
        "resolution": clean_resolution,
    }
    content_type = _safe_text(value.get("contentType"), max_length=128)
    if content_type:
        receipt["contentType"] = content_type
    return receipt


def _safe_route_status(value: Any) -> dict[str, Any]:
    status = value if isinstance(value, Mapping) else {}
    clean: dict[str, Any] = {
        "proven": bool(status.get("proven")),
        "reason": _safe_text(status.get("reason"), max_length=128) or "disabled",
    }
    last_probe = _safe_probe_receipt(status.get("lastProbe"))
    if last_probe is not None:
        clean["lastProbe"] = last_probe
    return clean


def _state_response(
    *,
    exists: bool,
    valid: bool,
    enabled: bool = False,
    errors: list[str] | None = None,
    mode: str | None = None,
    provider: Mapping[str, Any] | None = None,
    peer: Mapping[str, Any] | None = None,
    projection: Mapping[str, Any] | None = None,
    status: Mapping[str, Any] | None = None,
    resume_available: bool = False,
) -> dict[str, Any]:
    return {
        "exists": exists,
        "valid": valid,
        "enabled": enabled,
        "mode": mode,
        "provider": _safe_provider(provider) if enabled else None,
        "peer": _safe_peer_metadata(peer) if enabled else None,
        "projection": _safe_string_map(projection, _SAFE_PROJECTION_KEYS),
        "status": _safe_route_status(status),
        "resumeAvailable": bool(resume_available),
        "errors": errors or [],
    }


def _read_route_state() -> dict[str, Any]:
    path = _state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _state_response(exists=False, valid=True)
    except OSError as exc:
        return _state_response(exists=True, valid=False, errors=[f"read failed: {exc}"])

    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return _state_response(exists=True, valid=False, errors=[f"not valid JSON: {exc}"])
    if not isinstance(doc, Mapping):
        return _state_response(exists=True, valid=False, errors=["state root must be an object"])
    if doc.get("schema") != ROUTE_STATE_SCHEMA:
        return _state_response(exists=True, valid=False, errors=["unknown routing-state schema"])
    enabled = doc.get("enabled") is True
    provider = doc.get("provider")
    if enabled and not isinstance(provider, Mapping):
        return _state_response(
            exists=True,
            valid=False,
            enabled=True,
            errors=["enabled route is missing provider metadata"],
        )
    resume = doc.get("resume")
    resume_available = bool(
        not enabled
        and isinstance(resume, Mapping)
        and resume.get("available") is True
        and isinstance(resume.get("profileSha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", resume["profileSha256"])
    )
    return _state_response(
        exists=True,
        valid=True,
        enabled=enabled,
        mode=str(doc.get("mode") or "cloud"),
        provider=provider,
        peer=doc.get("peer"),
        projection=doc.get("projection"),
        status=doc.get("status"),
        resume_available=resume_available,
    )


def _safe_secret_status(value: Any) -> dict[str, Any]:
    secret = value if isinstance(value, Mapping) else {}
    raw_bytes = secret.get("bytes")
    return {
        "configured": bool(secret.get("configured")),
        "bytes": raw_bytes if type(raw_bytes) is int or raw_bytes is None else None,
    }


def _safe_secret_file_status(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink():
            return {"configured": False, "bytes": None}
        metadata = path.stat()
    except FileNotFoundError:
        return {"configured": False, "bytes": 0}
    except OSError:
        return {"configured": False, "bytes": None}
    return {"configured": metadata.st_size > 0, "bytes": metadata.st_size}


def _classify_forbidden_ip_address(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return ""
    if address.version == 4 and str(address) == "255.255.255.255":
        return "broadcast"
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link_local"
    if address.is_multicast:
        return "multicast"
    if address.is_unspecified:
        return "unspecified"
    if address.is_private:
        return "private"
    if address.is_reserved:
        return "reserved"
    if not address.is_global:
        return "non_global"
    return ""


def _peer_model_error(reason: str, message: str, *, status_code: int = 409) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "error": "remote_peer_unavailable",
            "reason": reason,
            "message": message,
        },
    )


def _validated_peer_control_base_url(route_state: Mapping[str, Any]) -> str:
    if route_state.get("valid") is not True:
        raise _peer_model_error(
            "invalid_route_state",
            "Remote ODS peer route state is invalid.",
        )
    if route_state.get("enabled") is not True:
        raise _peer_model_error(
            "not_configured",
            "Remote ODS peer lifecycle is not configured.",
        )
    peer = _safe_peer_metadata(route_state.get("peer"))
    base_url = peer.get("controlBaseUrl") or ""
    transport = peer.get("transport") or ""
    if not base_url:
        raise _peer_model_error(
            "not_configured",
            "Remote ODS peer lifecycle is not configured.",
        )
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError as exc:
        raise _peer_model_error(
            "invalid_peer_url",
            "Remote ODS peer control URL is invalid.",
        ) from exc
    if not parts.scheme or not parts.netloc:
        raise _peer_model_error(
            "invalid_peer_url",
            "Remote ODS peer control URL must include scheme and host.",
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise _peer_model_error(
            "invalid_peer_url",
            "Remote ODS peer control URL must not include credentials, query, or fragment.",
        )
    path = parts.path.rstrip("/")
    if path:
        raise _peer_model_error(
            "invalid_peer_url",
            "Remote ODS peer control URL must be the control-plane root.",
        )
    host = parts.hostname or ""
    lower_host = host.lower()
    scheme = parts.scheme.lower()
    if any(char.isspace() for char in host) or "%" in host:
        raise _peer_model_error(
            "invalid_peer_url",
            "Remote ODS peer control URL host is invalid.",
        )
    if (
        transport == "ssh"
        and scheme == "http"
        and lower_host == _SSH_CONTROL_TUNNEL_HOST
        and port == _SSH_CONTROL_TUNNEL_PORT
    ):
        return urlunsplit((scheme, f"{_SSH_CONTROL_TUNNEL_HOST}:{port}", "", "", ""))
    if lower_host == _SSH_CONTROL_TUNNEL_HOST:
        raise _peer_model_error(
            "invalid_peer_url",
            "Remote ODS peer tunnel URL must use the owned SSH control tunnel.",
        )
    if scheme != "https":
        raise _peer_model_error(
            "invalid_peer_url",
            "Remote ODS peer model management requires HTTPS or the owned SSH control tunnel.",
        )
    if lower_host in _LOCAL_HOSTNAMES or lower_host.endswith(".localhost"):
        raise _peer_model_error(
            "invalid_peer_url",
            "Remote ODS peer control URL must not target local hostnames.",
        )
    forbidden_class = _classify_forbidden_ip_address(lower_host)
    if forbidden_class:
        raise _peer_model_error(
            "invalid_peer_url",
            f"Remote ODS peer control URL must not use {forbidden_class} IP literals.",
        )
    netloc = lower_host if port is None else f"{lower_host}:{port}"
    if ":" in lower_host and not lower_host.startswith("["):
        netloc = f"[{lower_host}]" if port is None else f"[{lower_host}]:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def _read_peer_token() -> str:
    path = _peer_token_path()
    try:
        if path.is_symlink():
            raise _peer_model_error(
                "missing_peer_token",
                "Remote ODS peer token custody is not configured.",
            )
        value = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise _peer_model_error(
            "missing_peer_token",
            "Remote ODS peer token custody is not configured.",
        ) from exc
    except OSError as exc:
        raise _peer_model_error(
            "peer_token_unreadable",
            "Remote ODS peer token custody is unreadable.",
            status_code=503,
        ) from exc
    token = value.strip()
    if not token or any(ord(char) < 32 or ord(char) == 127 for char in token):
        raise _peer_model_error(
            "invalid_peer_token",
            "Remote ODS peer token custody is invalid.",
        )
    return token


def _redact_peer_secret(value: Any, token: str) -> Any:
    if isinstance(value, str):
        return value.replace(token, PEER_REDACTED) if token else value
    if isinstance(value, list):
        return [_redact_peer_secret(item, token) for item in value]
    if isinstance(value, dict):
        return {
            str(key).replace(token, PEER_REDACTED): _redact_peer_secret(item, token)
            for key, item in value.items()
            if isinstance(key, str)
        }
    return value


def _peer_status(
    route_state: Mapping[str, Any],
    ssh_supervisor: Mapping[str, Any],
) -> dict[str, Any]:
    peer = _safe_peer_metadata(route_state.get("peer"))
    configured = bool(route_state.get("enabled")) and bool(peer.get("controlBaseUrl"))
    token = (
        _safe_secret_file_status(_peer_token_path())
        if configured
        else {"configured": False, "bytes": 0}
    )
    reason = "not_configured"
    ready = False
    if not route_state.get("valid"):
        reason = "invalid_route_state"
    elif not configured:
        reason = "not_configured"
    elif not token.get("configured"):
        reason = "missing_peer_token"
    elif peer.get("transport") == "ssh" and not ssh_supervisor.get("ready"):
        reason = "ssh_control_tunnel_not_ready"
    else:
        ready = True
        reason = "ready"
    return {
        "configured": configured,
        "ready": ready,
        "reason": reason,
        "controlBaseUrl": peer.get("controlBaseUrl") if configured else None,
        "transport": peer.get("transport") if configured else None,
        "token": token,
    }


def _safe_ssh_tunnel(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    argv = value.get("argv")
    clean_argv: list[str] = []
    if isinstance(argv, list):
        for item in argv[:64]:
            text = _safe_text(item, max_length=256)
            if text:
                clean_argv.append(text)
    return {
        "name": _safe_text(value.get("name"), max_length=32),
        "listenHost": _safe_text(value.get("listenHost"), max_length=128),
        "listenPort": _safe_int(value.get("listenPort")),
        "targetHost": _safe_text(value.get("targetHost"), max_length=128),
        "targetPort": _safe_int(value.get("targetPort")),
        "argv": clean_argv,
    }


def _safe_ssh_supervisor_status(
    payload: Any,
    *,
    reachable: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {
            "reachable": reachable,
            "valid": False,
            "schema": "",
            "status": "invalid",
            "ready": False,
            "readyToStart": False,
            "reason": "invalid_ssh_supervisor_status",
            "tunnelBaseUrl": None,
            "tunnels": [],
            "secrets": {
                "sshIdentity": {"configured": False, "bytes": None},
                "sshKnownHosts": {"configured": False, "bytes": None},
            },
            "missingSecrets": [],
        }
    tunnels: list[dict[str, Any]] = []
    raw_tunnels = payload.get("tunnels")
    if isinstance(raw_tunnels, list):
        tunnels = [
            tunnel
            for tunnel in (_safe_ssh_tunnel(item) for item in raw_tunnels[:8])
            if tunnel is not None
        ]
    missing = payload.get("missingSecrets")
    missing_secrets = [
        text
        for text in (
            _safe_text(item, max_length=64)
            for item in (missing[:8] if isinstance(missing, list) else [])
        )
        if text
    ]
    secrets = payload.get("secrets") if isinstance(payload.get("secrets"), Mapping) else {}
    schema = _safe_text(payload.get("schema"), max_length=80)
    return {
        "reachable": reachable,
        "valid": schema == _SSH_SUPERVISOR_SCHEMA,
        "schema": schema,
        "status": _safe_text(payload.get("status"), max_length=64) or "unknown",
        "ready": bool(payload.get("ready")),
        "readyToStart": bool(payload.get("readyToStart")),
        "reason": _safe_text(payload.get("reason"), max_length=128) or "unknown",
        "tunnelBaseUrl": _safe_text(payload.get("tunnelBaseUrl"), max_length=256) or None,
        "tunnels": tunnels,
        "secrets": {
            "sshIdentity": _safe_secret_status(secrets.get("sshIdentity")),
            "sshKnownHosts": _safe_secret_status(secrets.get("sshKnownHosts")),
        },
        "missingSecrets": missing_secrets,
    }


def _safe_egress_tunnel(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    process = value.get("process")
    clean_process = None
    if isinstance(process, Mapping):
        clean_process = {
            "status": _safe_text(process.get("status"), max_length=64) or "unknown",
            "pid": _safe_int(process.get("pid")),
        }
    tunnel: dict[str, Any] = {
        "ok": bool(value.get("ok")),
        "ready": bool(value.get("ready")),
        "status": _safe_text(value.get("status"), max_length=64) or "unknown",
        "reason": _safe_text(value.get("reason"), max_length=128) or "unknown",
        "process": clean_process,
    }
    http_status = _safe_int(value.get("httpStatus"))
    if http_status is not None:
        tunnel["httpStatus"] = http_status
    error_type = _safe_text(value.get("errorType"), max_length=128)
    if error_type:
        tunnel["errorType"] = error_type
    return tunnel


async def _fetch_ssh_supervisor_status() -> dict[str, Any]:
    try:
        payload = await async_request_agent_json(
            "GET",
            "/v1/remote-provider/ssh-supervisor",
            timeout=5,
        )
    except AgentHTTPError as exc:
        logger.debug("remote-provider SSH supervisor status returned HTTP error: %s", exc)
        return _safe_ssh_supervisor_status(
            {
                "schema": _SSH_SUPERVISOR_SCHEMA,
                "status": "unavailable",
                "ready": False,
                "readyToStart": False,
                "reason": f"host_agent_http_{exc.status_code}",
                "tunnelBaseUrl": None,
                "tunnels": [],
                "secrets": {},
                "missingSecrets": [],
            },
            reachable=True,
        )
    except (AgentUnavailable, AgentProtocolError) as exc:
        logger.debug("remote-provider SSH supervisor status unavailable: %s", exc)
        return _safe_ssh_supervisor_status(
            {
                "schema": _SSH_SUPERVISOR_SCHEMA,
                "status": "unavailable",
                "ready": False,
                "readyToStart": False,
                "reason": "host_agent_unavailable",
                "tunnelBaseUrl": None,
                "tunnels": [],
                "secrets": {},
                "missingSecrets": [],
            },
            reachable=False,
        )
    return _safe_ssh_supervisor_status(payload)


def _sanitize_egress_health(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {
            "reachable": True,
            "valid": False,
            "ready": False,
            "status": "invalid",
            "reason": "invalid_egress_health",
            "secret": {"configured": False, "bytes": None},
            "resolution": None,
            "tunnel": None,
        }
    resolution = payload.get("resolution")
    clean_resolution = None
    if isinstance(resolution, Mapping):
        clean_resolution = {
            "ok": bool(resolution.get("ok")),
            "reason": _safe_text(resolution.get("reason"), max_length=128),
            "addressCount": (
                resolution.get("addressCount")
                if type(resolution.get("addressCount")) is int
                else None
            ),
        }
    return {
        "reachable": True,
        "valid": True,
        "ready": bool(payload.get("ready")),
        "status": _safe_text(payload.get("status"), max_length=64) or "unknown",
        "reason": _safe_text(payload.get("reason"), max_length=128),
        "secret": _safe_secret_status(payload.get("secret")),
        "resolution": clean_resolution,
        "tunnel": _safe_egress_tunnel(payload.get("tunnel")),
    }


def _safe_egress_error(payload: Any, status_code: int) -> dict[str, str]:
    error = payload.get("error") if isinstance(payload, Mapping) else None
    if not isinstance(error, Mapping):
        error = {}
    error_type = _safe_text(error.get("type"), max_length=128) or "egress_probe_failed"
    return {
        "type": error_type,
        "message": _EGRESS_ERROR_MESSAGES.get(
            error_type,
            f"remote-provider egress returned HTTP {status_code}",
        ),
        "code": str(status_code),
    }


def _sanitize_egress_probe_response(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_egress_probe_response")
    schema = _safe_text(payload.get("schema"), max_length=80)
    probe = _safe_probe_receipt(payload.get("probe"))
    if schema != _EGRESS_PROBE_SCHEMA or probe is None:
        raise ValueError("invalid_egress_probe_response")
    return {
        "schema": schema,
        "ok": bool(payload.get("ok")),
        "transport": _safe_text(payload.get("transport"), max_length=32),
        "probe": probe,
        "tunnel": _safe_egress_tunnel(payload.get("tunnel")),
    }


def _proof_record_failure(
    reason: str,
    *,
    reachable: bool,
    status_code: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "recorded": False,
        "reachable": reachable,
        "reason": reason,
    }
    if status_code is not None:
        result["statusCode"] = status_code
    return result


def _safe_proof_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return _proof_record_failure("invalid_host_agent_response", reachable=True)
    schema = _safe_text(payload.get("schema"), max_length=80)
    if schema != _PROOF_RECORD_SCHEMA or payload.get("recorded") is not True:
        return _proof_record_failure("invalid_host_agent_response", reachable=True)
    result = {
        "recorded": True,
        "reachable": True,
        "schema": schema,
        "status": _safe_route_status(payload.get("status")),
    }
    activation = payload.get("activation")
    if isinstance(activation, Mapping):
        result["activation"] = {
            "active": activation.get("active") is True,
            "proven": activation.get("proven") is True,
            "gateway": _safe_text(activation.get("gateway"), max_length=64),
            "publicModel": _safe_text(activation.get("publicModel"), max_length=64),
            "model": _safe_text(activation.get("model"), max_length=256),
            "pixel": _safe_text(activation.get("pixel"), max_length=64),
        }
    return result


async def _record_egress_probe_proof(probe_response: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = await async_request_agent_json(
            "POST",
            "/v1/remote-provider/proof",
            payload=dict(probe_response),
            timeout=LIFECYCLE_APPLY_TIMEOUT_SECONDS,
        )
    except AgentHTTPError as exc:
        logger.debug(
            "remote-provider proof recording returned HTTP %s",
            exc.status_code,
        )
        return _proof_record_failure(
            f"host_agent_http_{exc.status_code}",
            reachable=True,
            status_code=exc.status_code,
        )
    except (AgentUnavailable, AgentProtocolError):
        logger.debug("remote-provider proof recording unavailable")
        return _proof_record_failure("host_agent_unavailable", reachable=False)
    return _safe_proof_record(payload)


async def _fetch_egress_health() -> dict[str, Any]:
    url = f"{EGRESS_URL.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=EGRESS_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.debug("remote-provider-egress health unavailable: %s", exc)
        return {
            "reachable": False,
            "valid": False,
            "ready": False,
            "status": "unreachable",
            "reason": "egress_unreachable",
            "secret": {"configured": False, "bytes": None},
            "resolution": None,
            "tunnel": None,
        }
    except httpx.HTTPError as exc:
        logger.debug("remote-provider-egress health request failed: %s", exc)
        return {
            "reachable": False,
            "valid": False,
            "ready": False,
            "status": "error",
            "reason": "egress_request_failed",
            "secret": {"configured": False, "bytes": None},
            "resolution": None,
            "tunnel": None,
        }

    if response.status_code >= 400:
        return {
            "reachable": True,
            "valid": False,
            "ready": False,
            "status": "error",
            "reason": f"egress_http_{response.status_code}",
            "secret": {"configured": False, "bytes": None},
            "resolution": None,
            "tunnel": None,
        }
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return _sanitize_egress_health(payload)


async def _post_egress_probe() -> dict[str, Any]:
    url = f"{EGRESS_URL.rstrip('/')}/probe"
    try:
        async with httpx.AsyncClient(timeout=EGRESS_PROBE_TIMEOUT_SECONDS) as client:
            response = await client.post(url)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.debug("remote-provider-egress probe unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "type": "egress_unreachable",
                "message": "Remote provider egress is unreachable",
                "code": "503",
            },
        ) from exc
    except httpx.HTTPError as exc:
        logger.debug("remote-provider-egress probe request failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={
                "type": "egress_request_failed",
                "message": "Remote provider egress probe request failed",
                "code": "502",
            },
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "type": "invalid_egress_probe_response",
                "message": "Remote provider egress returned an invalid probe response",
                "code": "502",
            },
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=_safe_egress_error(payload, response.status_code),
        )
    try:
        return _sanitize_egress_probe_response(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "type": "invalid_egress_probe_response",
                "message": "Remote provider egress returned an invalid probe response",
                "code": "502",
            },
        ) from exc


def _safe_peer_model_id(model_id: str) -> str:
    model = _safe_text(model_id, max_length=256)
    if not model:
        raise HTTPException(status_code=400, detail="Remote peer model id is required.")
    if "/" in model or "\\" in model or model in {".", ".."}:
        raise HTTPException(status_code=400, detail="Remote peer model id is invalid.")
    return model


def _peer_model_action_path(model_id: str, action: str = "") -> str:
    encoded = quote(_safe_peer_model_id(model_id), safe="")
    suffix = f"/{action}" if action else ""
    return f"/api/models/{encoded}{suffix}"


def _peer_response_json(response: httpx.Response, token: str) -> Any:
    content = response.content
    if len(content) > PEER_PROXY_RESPONSE_MAX_BYTES:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "remote_peer_response_too_large",
                "message": "Remote ODS peer response exceeded the safety limit.",
            },
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "invalid_remote_peer_response",
                "message": "Remote ODS peer returned an invalid JSON response.",
            },
        ) from exc
    return _redact_peer_secret(payload, token)


def _peer_http_error(response: httpx.Response, token: str) -> HTTPException:
    try:
        detail = _redact_peer_secret(response.json(), token)
    except ValueError:
        detail = {
            "error": "remote_peer_http_error",
            "message": f"Remote ODS peer returned HTTP {response.status_code}.",
        }
    return HTTPException(status_code=response.status_code, detail=detail)


async def _peer_model_json_request(
    method: str,
    path: str,
    *,
    timeout: float = PEER_PROXY_TIMEOUT_SECONDS,
) -> Any:
    route_state = _read_route_state()
    base_url = _validated_peer_control_base_url(route_state)
    peer = _safe_peer_metadata(route_state.get("peer"))
    if peer.get("transport") == "ssh":
        ssh_supervisor = await _fetch_ssh_supervisor_status()
        if not ssh_supervisor.get("ready"):
            raise _peer_model_error(
                "ssh_control_tunnel_not_ready",
                "Remote ODS peer SSH control tunnel is not running.",
            )
    token = _read_peer_token()
    url = f"{base_url}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(
                method,
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                },
            )
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "remote_peer_unreachable",
                "message": "Remote ODS peer is unreachable.",
            },
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "remote_peer_request_failed",
                "message": "Remote ODS peer request failed.",
            },
        ) from exc

    if response.status_code in {401, 403}:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "remote_peer_auth_rejected",
                "message": "Remote ODS peer rejected the configured peer token.",
                "status": response.status_code,
            },
        )
    if response.status_code >= 400:
        raise _peer_http_error(response, token)
    return _peer_response_json(response, token)


def _overall_status(
    route_state: Mapping[str, Any],
    egress: Mapping[str, Any],
    activation: Mapping[str, Any],
) -> str:
    if not route_state.get("valid"):
        return "invalid"
    if route_state.get("enabled") is not True:
        return "disabled"
    if not egress.get("reachable") or not egress.get("ready"):
        return "degraded"
    if not activation.get("valid") or not activation.get("proven"):
        return "degraded"
    if activation.get("routeFingerprint") != _route_fingerprint(route_state):
        return "degraded"
    return "ready"


@router.get("/api/remote-provider/status", dependencies=[Depends(verify_api_key)])
async def remote_provider_status() -> dict[str, Any]:
    """Return sanitized remote-provider status without mutating configuration."""
    route_state = _read_route_state()
    activation = await _reconcile_activation_with_host(_read_activation())
    egress = await _fetch_egress_health()
    ssh_supervisor = await _fetch_ssh_supervisor_status()
    peer = _peer_status(route_state, ssh_supervisor)
    overall = _overall_status(route_state, egress, activation)
    return {
        "status": overall,
        "routeState": route_state,
        "activation": activation,
        "sshSupervisor": ssh_supervisor,
        "peer": peer,
        "egress": egress,
        "capabilities": {
            "inference": overall == "ready",
            "odsPeerLifecycle": bool(peer.get("ready")),
        },
        "availableActions": {
            "configure": True,
            "test": bool(route_state.get("enabled")),
            "enable": bool(route_state.get("resumeAvailable")) or (
                bool(route_state.get("enabled")) and overall != "ready"
            ),
            "disable": bool(route_state.get("enabled")),
            "remove": bool(route_state.get("exists")),
        },
    }


@router.get("/api/remote-provider/peer/models", dependencies=[Depends(verify_api_key)])
async def remote_provider_peer_models() -> Any:
    """List models from the authenticated remote ODS peer."""
    return await _peer_model_json_request("GET", "/api/models")


@router.get(
    "/api/remote-provider/peer/models/download-status",
    dependencies=[Depends(verify_api_key)],
)
async def remote_provider_peer_model_download_status() -> Any:
    """Return remote ODS peer model download status."""
    return await _peer_model_json_request("GET", "/api/models/download-status")


@router.post(
    "/api/remote-provider/peer/models/download/cancel",
    dependencies=[Depends(verify_api_key)],
)
async def remote_provider_peer_cancel_download() -> Any:
    """Cancel an in-progress remote ODS peer model download."""
    return await _peer_model_json_request("POST", "/api/models/download/cancel")


@router.post(
    "/api/remote-provider/peer/models/{model_id}/download",
    dependencies=[Depends(verify_api_key)],
)
async def remote_provider_peer_download_model(model_id: str) -> Any:
    """Start a model download on the authenticated remote ODS peer."""
    return await _peer_model_json_request(
        "POST",
        _peer_model_action_path(model_id, "download"),
    )


@router.post(
    "/api/remote-provider/peer/models/{model_id}/load",
    dependencies=[Depends(verify_api_key)],
)
async def remote_provider_peer_load_model(model_id: str) -> Any:
    """Activate a model on the authenticated remote ODS peer."""
    return await _peer_model_json_request(
        "POST",
        _peer_model_action_path(model_id, "load"),
        timeout=PEER_PROXY_LOAD_TIMEOUT_SECONDS,
    )


@router.delete(
    "/api/remote-provider/peer/models/{model_id}",
    dependencies=[Depends(verify_api_key)],
)
async def remote_provider_peer_delete_model(model_id: str) -> Any:
    """Delete a downloaded model on the authenticated remote ODS peer."""
    return await _peer_model_json_request(
        "DELETE",
        _peer_model_action_path(model_id),
    )


@router.post("/api/remote-provider/plan", dependencies=[Depends(verify_api_key)])
async def remote_provider_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a remote-provider lifecycle request through the host agent."""
    try:
        return await async_request_agent_json(
            "POST",
            "/v1/remote-provider/plan",
            payload=payload,
            timeout=10,
        )
    except AgentHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"Host agent unreachable: {exc}") from exc
    except AgentProtocolError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid host agent response: {exc}") from exc


@router.post("/api/remote-provider/probe", dependencies=[Depends(verify_api_key)])
async def remote_provider_probe() -> dict[str, Any]:
    """Probe the configured remote provider through the internal egress boundary."""
    result = await _post_egress_probe()
    result["routeProof"] = await _record_egress_probe_proof(result)
    return result


@router.post("/api/remote-provider/enable", dependencies=[Depends(verify_api_key)])
async def remote_provider_enable() -> dict[str, Any]:
    """Re-prove and activate a saved route, rolling back to local on failure."""
    staged = await remote_provider_apply({"action": "enable"})
    if staged.get("staged") is not True:
        return staged
    try:
        proof = await remote_provider_probe()
        route_proof = proof.get("routeProof")
        if not isinstance(route_proof, Mapping) or route_proof.get("recorded") is not True:
            raise HTTPException(
                status_code=502,
                detail={
                    "error": "remote_provider_activation_not_recorded",
                    "message": "Remote provider proof did not activate every consumer.",
                },
            )
    except Exception as exc:
        status_code = exc.status_code if isinstance(exc, HTTPException) else 500
        probe_detail = (
            exc.detail
            if isinstance(exc, HTTPException)
            else {"error": "remote_provider_enable_internal_failure"}
        )
        rollback: dict[str, Any]
        try:
            rollback_result = await remote_provider_apply({"action": "disable"})
            rollback = {
                "attempted": True,
                "ok": rollback_result.get("applied") is True,
            }
        except Exception as rollback_exc:
            rollback = {
                "attempted": True,
                "ok": False,
            }
            if isinstance(rollback_exc, HTTPException):
                rollback["statusCode"] = rollback_exc.status_code
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": "remote_provider_enable_failed",
                "message": (
                    "Remote provider reactivation failed; the saved route was paused again."
                    if rollback.get("ok") is True
                    else "Remote provider reactivation failed and automatic pause could not be proved."
                ),
                "probe": probe_detail,
                "rollback": rollback,
            },
        ) from exc
    staged["staged"] = False
    staged["applied"] = True
    staged["probe"] = proof.get("probe")
    staged["routeProof"] = proof.get("routeProof")
    return staged


@router.post("/api/remote-provider/apply", dependencies=[Depends(verify_api_key)])
async def remote_provider_apply(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply a remote-provider lifecycle request through the host agent."""
    try:
        return await async_request_agent_json(
            "POST",
            "/v1/remote-provider/apply",
            payload=payload,
            timeout=LIFECYCLE_APPLY_TIMEOUT_SECONDS,
        )
    except AgentHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except AgentUnavailable as exc:
        raise HTTPException(status_code=503, detail=f"Host agent unreachable: {exc}") from exc
    except AgentProtocolError as exc:
        raise HTTPException(status_code=502, detail=f"Invalid host agent response: {exc}") from exc
