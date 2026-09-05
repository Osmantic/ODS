"""Typed remote-provider lifecycle operation planning.

This module is intentionally side-effect free.  It gives the host-agent and
Dashboard/API one shared contract for configure/test/enable/disable/remove before
later slices add file mutation and live probes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .policy import (
    FORBIDDEN_PUBLIC_SECRET_ENV,
    PolicyError,
    plan_route,
    public_activation_receipt,
    redacted_secret_refs,
)


LIFECYCLE_OPERATION_SCHEMA = "ods.remote-provider-lifecycle-operation.v1"
LIFECYCLE_ACTIONS = frozenset({"configure", "test", "enable", "disable", "remove"})

_ACTION_PHASE = {
    "configure": "stage",
    "test": "validate",
    "enable": "stage",
    "disable": "commit",
    "remove": "commit",
}
_ACTION_DETAIL = {
    "configure": "remote provider route staged",
    "test": "remote provider route validated",
    "enable": "saved remote provider route requested for reactivation",
    "disable": "remote provider route disabled",
    "remove": "remote provider route removed",
}
_SECRET_FIELD_TO_REF = {
    "apiKey": "REMOTE_LLM_API_KEY",
    "peerToken": "REMOTE_ODS_PEER_TOKEN",
    "sshPrivateKey": "REMOTE_LLM_SSH_PRIVATE_KEY",
    "sshKnownHosts": "REMOTE_LLM_SSH_KNOWN_HOSTS",
    "tlsCaPem": "REMOTE_LLM_TLS_CA_PEM",
    "tlsClientCert": "REMOTE_LLM_TLS_CLIENT_CERT",
    "tlsClientKey": "REMOTE_LLM_TLS_CLIENT_KEY",
}
_SINGLE_LINE_SECRET_FIELDS = {"apiKey", "peerToken"}
_SSH_SECRET_FIELDS = {"sshPrivateKey", "sshKnownHosts"}
_DEFAULT_CONTEXT_LENGTH = 32768
_DEFAULT_MAX_TOKENS = 4096


class LifecycleError(PolicyError):
    """Raised when a lifecycle action request violates the public contract."""


def _string(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return value
    raise LifecycleError(f"{field} must be an object")


def _has_control_chars(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _has_forbidden_multiline_secret_chars(value: str) -> bool:
    return any(
        (ord(char) < 32 and char not in "\r\n\t") or ord(char) == 127
        for char in value
    )


def _reject_public_secret_keys(payload: Mapping[str, Any]) -> None:
    present = sorted(key for key in payload if key in FORBIDDEN_PUBLIC_SECRET_ENV)
    if present:
        names = ", ".join(present)
        raise LifecycleError(
            "remote provider secrets must be passed under lifecycle secrets: "
            f"{names}"
        )


def _read_provider_field(
    payload: Mapping[str, Any],
    provider: Mapping[str, Any],
    key: str,
    env_name: str,
) -> str:
    return _string(provider.get(key) or payload.get(key) or payload.get(env_name))


def _read_ssh_field(
    payload: Mapping[str, Any],
    ssh: Mapping[str, Any],
    env_name: str,
    camel_name: str,
) -> str:
    return _string(ssh.get(camel_name) or payload.get(env_name) or payload.get(camel_name))


def _route_env(payload: Mapping[str, Any]) -> dict[str, str]:
    provider = _mapping(payload.get("provider"), "provider")
    peer = _mapping(payload.get("peer"), "peer")
    ssh = _mapping(payload.get("ssh"), "ssh")
    env = {
        "ODS_MODE": _string(payload.get("mode") or "cloud"),
        "REMOTE_LLM_ENABLED": "true",
        "REMOTE_LLM_TRANSPORT": _read_provider_field(
            payload, provider, "transport", "REMOTE_LLM_TRANSPORT"
        ).lower(),
        "REMOTE_LLM_BASE_URL": _read_provider_field(
            payload, provider, "baseUrl", "REMOTE_LLM_BASE_URL"
        ),
        "REMOTE_LLM_MODEL": _read_provider_field(
            payload, provider, "model", "REMOTE_LLM_MODEL"
        ),
    }
    for env_name, camel_name in (
        ("REMOTE_LLM_SSH_HOST", "host"),
        ("REMOTE_LLM_SSH_USER", "user"),
        ("REMOTE_LLM_SSH_PORT", "port"),
        ("REMOTE_LLM_SSH_INFERENCE_HOST", "inferenceHost"),
        ("REMOTE_LLM_SSH_INFERENCE_PORT", "inferencePort"),
        ("REMOTE_LLM_SSH_CONTROL_HOST", "controlHost"),
        ("REMOTE_LLM_SSH_CONTROL_PORT", "controlPort"),
    ):
        value = _read_ssh_field(payload, ssh, env_name, camel_name)
        if value:
            env[env_name] = value
    peer_url = _string(
        peer.get("controlBaseUrl")
        or peer.get("baseUrl")
        or payload.get("peerUrl")
        or payload.get("REMOTE_ODS_PEER_URL")
    )
    if peer_url:
        env["REMOTE_ODS_PEER_URL"] = peer_url
    return env


def _provider_runtime_contract(provider: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the model limits Pixel and other agent consumers must know."""
    context_length = provider.get("contextLength", _DEFAULT_CONTEXT_LENGTH)
    if type(context_length) is not int or not 16384 <= context_length <= 10_000_000:
        raise LifecycleError("provider.contextLength must be an integer from 16384 to 10000000")
    max_tokens = provider.get("maxTokens", min(_DEFAULT_MAX_TOKENS, context_length // 8))
    if type(max_tokens) is not int or not 1 <= max_tokens <= context_length:
        raise LifecycleError("provider.maxTokens must be an integer within the context window")
    reasoning = provider.get("reasoning", False)
    if type(reasoning) is not bool:
        raise LifecycleError("provider.reasoning must be a boolean")
    return {
        "contextLength": context_length,
        "maxTokens": max_tokens,
        "reasoning": reasoning,
    }


def _disabled_route(payload: Mapping[str, Any]) -> dict[str, Any]:
    return plan_route(
        {
            "ODS_MODE": _string(payload.get("mode") or "cloud"),
            "REMOTE_LLM_ENABLED": "false",
        }
    )


def _validate_secret_value(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise LifecycleError(f"secrets.{field} must be a string")
    secret = value.strip()
    if not secret:
        raise LifecycleError(f"secrets.{field} must not be empty")
    if field in _SINGLE_LINE_SECRET_FIELDS and _has_control_chars(secret):
        raise LifecycleError(f"secrets.{field} must be a single-line secret")
    if (
        field not in _SINGLE_LINE_SECRET_FIELDS
        and _has_forbidden_multiline_secret_chars(secret)
    ):
        raise LifecycleError(f"secrets.{field} contains unsupported control characters")
    return secret


def _secret_refs_for_action(
    *,
    action: str,
    transport: str,
    secrets: Mapping[str, Any],
) -> dict[str, str]:
    if action not in {"configure", "test"}:
        return {}
    unknown = sorted(key for key in secrets if key not in _SECRET_FIELD_TO_REF)
    if unknown:
        names = ", ".join(unknown)
        raise LifecycleError(f"unsupported remote-provider secret fields: {names}")
    if any(key in FORBIDDEN_PUBLIC_SECRET_ENV for key in secrets):
        raise LifecycleError("remote provider secrets must use lifecycle secret field names")
    if "apiKey" not in secrets:
        raise LifecycleError("secrets.apiKey is required for remote-provider validation")
    required = {"apiKey"}
    if transport == "ssh":
        required |= _SSH_SECRET_FIELDS
    missing = sorted(field for field in required if field not in secrets)
    if missing:
        names = ", ".join(f"secrets.{field}" for field in missing)
        raise LifecycleError(f"{names} required for {transport} remote-provider transport")
    refs: dict[str, str] = {}
    for field, value in secrets.items():
        _validate_secret_value(field, value)
        refs[field] = _SECRET_FIELD_TO_REF[field]
    return refs


def _write_plan(action: str, secret_refs: Mapping[str, str]) -> dict[str, bool]:
    return {
        "routingState": action in {"configure", "enable", "disable"},
        "providerSecret": action == "configure" and "apiKey" in secret_refs,
        "peerToken": action == "configure" and "peerToken" in secret_refs,
        "sshIdentity": action == "configure" and "sshPrivateKey" in secret_refs,
        "sshKnownHosts": action == "configure" and "sshKnownHosts" in secret_refs,
        "removesRoutingState": action == "remove",
        "removesSecrets": action == "remove",
    }


def plan_lifecycle_operation(
    payload: Mapping[str, Any],
    *,
    action: str | None = None,
) -> dict[str, Any]:
    """Return the public, redacted lifecycle operation plan for a request."""
    if not isinstance(payload, Mapping):
        raise LifecycleError("remote-provider lifecycle payload must be an object")
    _reject_public_secret_keys(payload)
    requested_action = _string(action or payload.get("action")).lower()
    if requested_action not in LIFECYCLE_ACTIONS:
        allowed = ", ".join(sorted(LIFECYCLE_ACTIONS))
        raise LifecycleError(f"remote-provider lifecycle action must be one of: {allowed}")

    if requested_action in {"configure", "test"}:
        provider = _mapping(payload.get("provider"), "provider")
        route = plan_route(_route_env(payload))
        route_provider = route.get("provider")
        if not isinstance(route_provider, dict):
            raise LifecycleError("remote-provider route is missing provider metadata")
        route_provider.update(_provider_runtime_contract(provider))
        transport = str(route.get("transport") or "")
    else:
        route = _disabled_route(payload)
        transport = ""

    secrets = _mapping(payload.get("secrets"), "secrets")
    secret_refs = _secret_refs_for_action(
        action=requested_action,
        transport=transport,
        secrets=secrets,
    )
    redacted_refs = redacted_secret_refs(secret_refs.values())
    receipt = public_activation_receipt(
        route,
        phase=_ACTION_PHASE[requested_action],
        ok=True,
        detail=_ACTION_DETAIL[requested_action],
        secret_refs=secret_refs.values(),
    )
    return {
        "schema": LIFECYCLE_OPERATION_SCHEMA,
        "action": requested_action,
        "ok": True,
        "route": route,
        "writes": _write_plan(requested_action, secret_refs),
        "secretRefs": redacted_refs,
        "receipt": receipt,
    }


__all__ = [
    "LIFECYCLE_ACTIONS",
    "LIFECYCLE_OPERATION_SCHEMA",
    "LifecycleError",
    "plan_lifecycle_operation",
]
