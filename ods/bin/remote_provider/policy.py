"""Remote provider egress policy helpers.

The helpers in this module are deliberately inert: they normalize and classify
configuration, produce public receipts, and reject unsafe public config, but
they never open sockets or read secret material. The future egress service owns
network I/O and private credential loading.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = ROOT / "config" / "remote-provider-egress-policy.json"
SCHEMA = "ods.remote-provider-egress-policy.v1"
REMOTE_ROUTE_SCHEMA = "ods.remote-provider-route.v1"
ACTIVATION_RECEIPT_SCHEMA = "ods.remote-provider-activation-receipt.v1"
PUBLIC_MODEL_ALIAS = "ods/current"
INTERNAL_EGRESS_BASE_URL = "http://remote-provider-egress:8091/v1"
INTERNAL_SSH_CONTROL_BASE_URL = "http://remote-provider-ssh-tunnel:18092"
REDACTED = "[REDACTED]"

REMOTE_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")
LOCAL_HOSTNAMES = {
    "gateway.docker.internal",
    "host.docker.internal",
    "localhost",
    "localhost.localdomain",
}
REQUIRED_SSH_FIELDS = (
    "REMOTE_LLM_SSH_HOST",
    "REMOTE_LLM_SSH_USER",
    "REMOTE_LLM_SSH_PORT",
    "REMOTE_LLM_SSH_INFERENCE_HOST",
    "REMOTE_LLM_SSH_INFERENCE_PORT",
)
FORBIDDEN_PUBLIC_SECRET_ENV = frozenset(
    {
        "REMOTE_LLM_API_KEY",
        "REMOTE_ODS_PEER_TOKEN",
        "REMOTE_LLM_SSH_PRIVATE_KEY",
        "REMOTE_LLM_SSH_KEY_FILE",
        "REMOTE_LLM_SSH_KNOWN_HOSTS",
        "REMOTE_LLM_TLS_CA_PEM",
        "REMOTE_LLM_TLS_CLIENT_CERT",
        "REMOTE_LLM_TLS_CLIENT_KEY",
    }
)


class PolicyError(ValueError):
    """Raised when remote-provider metadata violates the egress policy."""


def _has_control_chars(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _string(value: object) -> str:
    return str(value or "").strip()


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> dict[str, Any]:
    """Load and minimally validate the checked-in policy document."""
    policy_path = Path(path)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    if payload.get("schema") != SCHEMA:
        raise PolicyError(f"{policy_path} does not declare {SCHEMA}")
    if payload.get("version") != 1:
        raise PolicyError(f"{policy_path} must be version 1")
    egress = payload.get("egress_service")
    if not isinstance(egress, dict):
        raise PolicyError(f"{policy_path} is missing egress_service")
    if egress.get("internal_base_url") != INTERNAL_EGRESS_BASE_URL:
        raise PolicyError("remote-provider egress internal URL drifted")
    if egress.get("public_model_alias") != PUBLIC_MODEL_ALIAS:
        raise PolicyError("remote-provider public model alias drifted")
    return payload


def _transport_url_policy(policy: Mapping[str, Any], transport: str) -> Mapping[str, Any]:
    transports = policy.get("transports")
    if not isinstance(transports, Mapping):
        raise PolicyError("policy is missing transports")
    transport_policy = transports.get(transport)
    if not isinstance(transport_policy, Mapping):
        raise PolicyError(f"unsupported remote transport: {transport}")
    url_policy = transport_policy.get("provider_base_url")
    if not isinstance(url_policy, Mapping):
        raise PolicyError(f"{transport} transport is missing provider_base_url policy")
    return url_policy


def classify_forbidden_ip_address(host: str) -> str:
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


def _normalize_netloc(host: str, port: int | None) -> str:
    normalized_host = host.lower()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    if port is None:
        return normalized_host
    return f"{normalized_host}:{port}"


def normalize_provider_base_url(
    value: str,
    *,
    transport: str,
    policy: Mapping[str, Any] | None = None,
) -> str:
    """Normalize a remote OpenAI-compatible base URL or raise PolicyError."""
    policy = policy or load_policy()
    transport_name = transport.strip().lower()
    url_policy = _transport_url_policy(policy, transport_name)
    allowed_schemes = set(url_policy.get("schemes") or ())
    allowed_paths = set(url_policy.get("allowed_paths") or ())
    default_path = _string(url_policy.get("default_path") or "/v1")

    raw = value.strip()
    if not raw:
        raise PolicyError("remote provider base URL is required")
    if _has_control_chars(raw):
        raise PolicyError("remote provider base URL contains control characters")
    if "\\" in raw:
        raise PolicyError("remote provider base URL must use forward slashes")

    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        raise PolicyError("remote provider base URL must include scheme and host")
    if parts.scheme.lower() not in allowed_schemes:
        allowed = ", ".join(sorted(allowed_schemes))
        raise PolicyError(f"{transport_name} transport requires scheme: {allowed}")
    if parts.username or parts.password:
        raise PolicyError("remote provider base URL must not embed credentials")
    if parts.query:
        raise PolicyError("remote provider base URL must not include a query string")
    if parts.fragment:
        raise PolicyError("remote provider base URL must not include a fragment")

    try:
        port = parts.port
    except ValueError as exc:
        raise PolicyError(f"remote provider base URL has an invalid port: {exc}") from exc

    host = parts.hostname or ""
    if not host:
        raise PolicyError("remote provider base URL must include a host")
    if _has_control_chars(host) or any(char.isspace() for char in host):
        raise PolicyError("remote provider base URL host is invalid")
    if "%" in host:
        raise PolicyError("remote provider base URL host must not include a zone id")

    lower_host = host.lower()
    if transport_name == "direct":
        if lower_host in LOCAL_HOSTNAMES or lower_host.endswith(".localhost"):
            raise PolicyError("direct remote provider URL must not target local hostnames")
        forbidden_class = classify_forbidden_ip_address(lower_host)
        if forbidden_class:
            raise PolicyError(
                f"direct remote provider URL must not use {forbidden_class} IP literals"
            )

    path = parts.path.rstrip("/")
    if path in {"", "/"}:
        path = default_path
    if path not in allowed_paths:
        allowed = ", ".join(sorted(allowed_paths))
        raise PolicyError(f"remote provider base URL path must be one of: {allowed}")

    return urlunsplit(
        (
            parts.scheme.lower(),
            _normalize_netloc(host, port),
            path,
            "",
            "",
        )
    )


def normalize_peer_control_url(value: str, *, transport: str) -> str:
    """Normalize a paired ODS control-plane root URL or raise PolicyError."""
    transport_name = transport.strip().lower()
    if transport_name not in {"direct", "ssh"}:
        raise PolicyError("REMOTE_LLM_TRANSPORT must be direct or ssh")

    raw = value.strip()
    if not raw:
        raise PolicyError("remote ODS peer URL is required")
    if _has_control_chars(raw):
        raise PolicyError("remote ODS peer URL contains control characters")
    if "\\" in raw:
        raise PolicyError("remote ODS peer URL must use forward slashes")

    parts = urlsplit(raw)
    if not parts.scheme or not parts.netloc:
        raise PolicyError("remote ODS peer URL must include scheme and host")
    scheme = parts.scheme.lower()
    if scheme != "https" and not (
        transport_name == "ssh"
        and scheme == "http"
        and (parts.hostname or "").lower() == "remote-provider-ssh-tunnel"
    ):
        allowed = "https"
        if transport_name == "ssh":
            allowed += ", or http for remote-provider-ssh-tunnel"
        raise PolicyError(f"{transport_name} peer transport requires scheme: {allowed}")
    if parts.username or parts.password:
        raise PolicyError("remote ODS peer URL must not embed credentials")
    if parts.query:
        raise PolicyError("remote ODS peer URL must not include a query string")
    if parts.fragment:
        raise PolicyError("remote ODS peer URL must not include a fragment")

    try:
        port = parts.port
    except ValueError as exc:
        raise PolicyError(f"remote ODS peer URL has an invalid port: {exc}") from exc

    host = parts.hostname or ""
    if not host:
        raise PolicyError("remote ODS peer URL must include a host")
    if _has_control_chars(host) or any(char.isspace() for char in host):
        raise PolicyError("remote ODS peer URL host is invalid")
    if "%" in host:
        raise PolicyError("remote ODS peer URL host must not include a zone id")

    lower_host = host.lower()
    if lower_host == "remote-provider-ssh-tunnel":
        if transport_name != "ssh" or scheme != "http" or port != 18092:
            raise PolicyError("remote ODS peer tunnel URL must use the SSH control tunnel")
    else:
        if lower_host in LOCAL_HOSTNAMES or lower_host.endswith(".localhost"):
            raise PolicyError("remote ODS peer URL must not target local hostnames")
        forbidden_class = classify_forbidden_ip_address(lower_host)
        if forbidden_class:
            raise PolicyError(
                f"remote ODS peer URL must not use {forbidden_class} IP literals"
            )

    path = parts.path.rstrip("/")
    if path:
        raise PolicyError("remote ODS peer URL must be the control-plane root")

    return urlunsplit(
        (
            parts.scheme.lower(),
            _normalize_netloc(host, port),
            "",
            "",
            "",
        )
    )


def validate_remote_model_id(model_id: str) -> str:
    """Return a trimmed remote model id if it is safe for public config."""
    model = model_id.strip()
    if not model:
        raise PolicyError("remote provider model id is required")
    if _has_control_chars(model):
        raise PolicyError("remote provider model id contains control characters")
    if REMOTE_MODEL_ID_RE.fullmatch(model) is None:
        raise PolicyError(
            "remote provider model id must not contain spaces or shell metacharacters"
        )
    return model


def _require_port(env: Mapping[str, object], key: str) -> int:
    value = _string(env.get(key))
    if not value:
        raise PolicyError(f"{key} is required for SSH remote provider transport")
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise PolicyError(f"{key} must be an integer port") from exc
    if port < 1 or port > 65535:
        raise PolicyError(f"{key} must be between 1 and 65535")
    return port


def _require_text(env: Mapping[str, object], key: str) -> str:
    value = _string(env.get(key))
    if not value:
        raise PolicyError(f"{key} is required for SSH remote provider transport")
    if _has_control_chars(value):
        raise PolicyError(f"{key} contains control characters")
    return value


def _ssh_metadata(env: Mapping[str, object]) -> dict[str, object]:
    for key in REQUIRED_SSH_FIELDS:
        if not _string(env.get(key)):
            raise PolicyError(f"{key} is required for SSH remote provider transport")
    metadata: dict[str, object] = {
        "host": _require_text(env, "REMOTE_LLM_SSH_HOST"),
        "user": _require_text(env, "REMOTE_LLM_SSH_USER"),
        "port": _require_port(env, "REMOTE_LLM_SSH_PORT"),
        "inferenceHost": _require_text(env, "REMOTE_LLM_SSH_INFERENCE_HOST"),
        "inferencePort": _require_port(env, "REMOTE_LLM_SSH_INFERENCE_PORT"),
    }
    control_host = _string(env.get("REMOTE_LLM_SSH_CONTROL_HOST"))
    control_port = _string(env.get("REMOTE_LLM_SSH_CONTROL_PORT"))
    if control_host or control_port:
        metadata["controlHost"] = _require_text(env, "REMOTE_LLM_SSH_CONTROL_HOST")
        metadata["controlPort"] = _require_port(env, "REMOTE_LLM_SSH_CONTROL_PORT")
    return metadata


def _peer_metadata(
    env: Mapping[str, object],
    *,
    transport: str,
    ssh: Mapping[str, object] | None,
) -> dict[str, str] | None:
    peer_url = _string(env.get("REMOTE_ODS_PEER_URL"))
    if peer_url:
        return {
            "controlBaseUrl": normalize_peer_control_url(
                peer_url,
                transport=transport,
            ),
            "transport": transport,
        }
    if (
        transport == "ssh"
        and isinstance(ssh, Mapping)
        and _string(ssh.get("controlHost"))
        and _string(ssh.get("controlPort"))
    ):
        return {
            "controlBaseUrl": INTERNAL_SSH_CONTROL_BASE_URL,
            "transport": "ssh",
        }
    return None


def plan_route(
    env: Mapping[str, object],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a safe public route description from non-secret remote metadata."""
    policy = policy or load_policy()
    enabled = _string(env.get("REMOTE_LLM_ENABLED")).lower() == "true"
    mode = _string(env.get("ODS_MODE")) or "local"
    if not enabled:
        return {
            "schema": REMOTE_ROUTE_SCHEMA,
            "enabled": False,
            "mode": mode,
            "transport": None,
            "provider": None,
            "ssh": None,
            "peer": None,
            "egress": {
                "internalBaseUrl": INTERNAL_EGRESS_BASE_URL,
                "publicModel": PUBLIC_MODEL_ALIAS,
                "consumerRoute": "gateway",
            },
        }
    if mode != "cloud":
        raise PolicyError("remote provider routing requires ODS_MODE=cloud")
    transport = _string(env.get("REMOTE_LLM_TRANSPORT")).lower()
    if transport not in {"direct", "ssh"}:
        raise PolicyError("REMOTE_LLM_TRANSPORT must be direct or ssh")
    base_url = normalize_provider_base_url(
        _string(env.get("REMOTE_LLM_BASE_URL")),
        transport=transport,
        policy=policy,
    )
    model = validate_remote_model_id(_string(env.get("REMOTE_LLM_MODEL")))
    ssh = _ssh_metadata(env) if transport == "ssh" else None
    peer = _peer_metadata(env, transport=transport, ssh=ssh)
    return {
        "schema": REMOTE_ROUTE_SCHEMA,
        "enabled": True,
        "mode": mode,
        "transport": transport,
        "provider": {
            "capability": "openai-compatible",
            "baseUrl": base_url,
            "model": model,
            "transport": transport,
        },
        "ssh": ssh,
        "peer": peer,
        "egress": {
            "internalBaseUrl": INTERNAL_EGRESS_BASE_URL,
            "publicModel": PUBLIC_MODEL_ALIAS,
            "consumerRoute": "gateway",
        },
    }


def validate_public_env_keys(env: Mapping[str, object]) -> None:
    """Reject private remote-provider secret names from public .env surfaces."""
    present = sorted(key for key in env if key in FORBIDDEN_PUBLIC_SECRET_ENV)
    if present:
        names = ", ".join(present)
        raise PolicyError(f"remote provider secrets are not public env keys: {names}")


def redacted_secret_refs(
    secret_refs: Mapping[str, object] | Iterable[str],
) -> dict[str, dict[str, object]]:
    """Return public secret references without exposing any values."""
    if isinstance(secret_refs, Mapping):
        names = sorted(str(key) for key, value in secret_refs.items() if _string(value))
    else:
        names = sorted(str(name) for name in secret_refs if _string(name))
    return {
        name: {
            "present": True,
            "value": REDACTED,
        }
        for name in names
    }


def public_activation_receipt(
    route: Mapping[str, Any],
    *,
    phase: str,
    ok: bool,
    detail: str = "",
    secret_refs: Mapping[str, object] | Iterable[str] = (),
) -> dict[str, Any]:
    """Build a support-bundle-safe activation receipt."""
    return {
        "schema": ACTIVATION_RECEIPT_SCHEMA,
        "ok": bool(ok),
        "phase": phase,
        "detail": str(detail),
        "enabled": bool(route.get("enabled")),
        "mode": route.get("mode"),
        "transport": route.get("transport"),
        "provider": route.get("provider") if route.get("enabled") else None,
        "peer": route.get("peer") if route.get("enabled") else None,
        "egress": route.get("egress"),
        "secretRefs": redacted_secret_refs(secret_refs),
    }
