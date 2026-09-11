"""Pure domain validation for ODS Pixel provider configuration."""

import copy
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

_ID_RE = r"^[a-z][a-z0-9_-]{0,63}$"


class ConfigError(ValueError):
    """Configuration validation error with machine-readable code."""

    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _has_control(s):
    """Return True if any character in s is a Unicode control character."""
    return any(unicodedata.category(c).startswith("C") for c in s)


def _check_id(value, label):
    if not isinstance(value, str):
        raise ConfigError(f"{label}: invalid identifier", "invalid_identifier")
    if not re.fullmatch(_ID_RE, value):
        raise ConfigError(f"{label}: invalid identifier", "invalid_identifier")


def _check_nonempty_str(value, label, max_len=256):
    if not isinstance(value, str):
        raise ConfigError(f"{label}: must be a non-empty string", "invalid_string")
    if _has_control(value):
        raise ConfigError(f"{label}: control characters not allowed", "invalid_string")
    trimmed = value.strip()
    if not trimmed or len(trimmed) > max_len:
        raise ConfigError(f"{label}: must be 1-{max_len} non-control characters", "invalid_string")
    return trimmed


def _check_strict_bool(value, label):
    if not isinstance(value, bool):
        raise ConfigError(f"{label}: must be a strict boolean", "invalid_boolean")


def _check_int(value, label, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{label}: must be an integer", "invalid_integer")
    if lo is not None and value < lo:
        raise ConfigError(f"{label}: below minimum", "range_error")
    if hi is not None and value > hi:
        raise ConfigError(f"{label}: above maximum", "range_error")


_METADATA_ADDRS = {
    ipaddress.IPv4Address("169.254.169.254"),
    ipaddress.IPv4Address("169.254.170.2"),
}


def _validate_base_url(value, label):
    # This is syntax validation, not network authorization. The transport must
    # resolve/pin addresses, reject forbidden destinations and disable redirects.
    if not isinstance(value, str) or len(value) > 2048 or _has_control(value):
        raise ConfigError(f"{label}: invalid URL", "invalid_url")
    raw = value.strip()
    if any(c in raw for c in ("\\", "?", "#", "@")) or any(c.isspace() for c in raw):
        raise ConfigError(f"{label}: invalid URL", "invalid_url")
    try:
        parts = urlsplit(raw)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        raise ConfigError(f"{label}: malformed URL", "invalid_url") from None
    if parts.scheme not in ("http", "https") or not hostname or parts.netloc.endswith(":"):
        raise ConfigError(f"{label}: invalid origin", "invalid_url")
    if port is not None and not 1 <= port <= 65535:
        raise ConfigError(f"{label}: invalid port", "invalid_url")
    path = parts.path.rstrip("/")
    if not path.endswith("/v1") or not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)+", path):
        raise ConfigError(f"{label}: path must end with /v1", "invalid_url")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if isinstance(address, ipaddress.IPv6Address):
        if not re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::[0-9]+)?", parts.netloc):
            raise ConfigError(f"{label}: invalid IPv6 authority", "invalid_url")
    elif "[" in parts.netloc or "]" in parts.netloc:
        raise ConfigError(f"{label}: invalid authority", "invalid_url")
    if address is None:
        if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?", hostname):
            raise ConfigError(f"{label}: invalid hostname", "invalid_url")
        if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
               for label in hostname.split(".")):
            raise ConfigError(f"{label}: invalid hostname", "invalid_url")
    if parts.scheme == "http" and not (
        address is not None and address.is_loopback or hostname == "localhost"
    ):
        raise ConfigError(f"{label}: HTTP requires loopback", "unsafe_url")
    # IPv4-mapped IPv6 must receive the same destination checks.
    checked = getattr(address, "ipv4_mapped", None) or address
    if checked is not None and (
        checked in _METADATA_ADDRS or checked.is_unspecified or checked.is_multicast
    ):
        raise ConfigError(f"{label}: unsafe address", "unsafe_url")
    host = f"[{address.compressed}]" if isinstance(address, ipaddress.IPv6Address) else hostname
    authority = host if port is None else f"{host}:{port}"
    return f"{parts.scheme}://{authority}{path}"

def _validate_provider(p, label, seen_ids):
    if not isinstance(p, dict):
        raise ConfigError(f"{label}: each provider must be an object", "invalid_type")
    exact_keys = {"id", "label", "kind", "baseUrl", "model", "contextTokens",
                  "maxOutputTokens", "supportsTools", "supportsVision", "reasoning",
                  "credentialRef", "enabled"}
    if set(p.keys()) != exact_keys:
        raise ConfigError(f"{label}: unknown or missing provider keys", "unknown_keys")
    pid = p["id"]
    _check_id(pid, "provider.id")
    if pid in seen_ids:
        raise ConfigError("duplicate provider id", "duplicate_id")
    seen_ids.add(pid)
    lbl = _check_nonempty_str(p["label"], "provider.label")
    kind = p["kind"]
    if kind not in ("local", "ods-peer", "cloud"):
        raise ConfigError("provider.kind: invalid value", "invalid_value")
    base_url = _validate_base_url(p["baseUrl"], "provider.baseUrl")
    model = _check_nonempty_str(p["model"], "provider.model")
    ct = p["contextTokens"]
    _check_int(ct, "provider.contextTokens", 256, 10_000_000)
    mot = p["maxOutputTokens"]
    _check_int(mot, "provider.maxOutputTokens", 1, ct)
    for bname in ("supportsTools", "supportsVision", "reasoning", "enabled"):
        _check_strict_bool(p[bname], f"provider.{bname}")
    cr = p["credentialRef"]
    if kind == "cloud" and p["enabled"]:
        if cr is None:
            raise ConfigError("provider.credentialRef: cloud enabled requires credential", "invalid_credential_ref")
        if not isinstance(cr, str):
            raise ConfigError("provider.credentialRef: must be null or identifier", "invalid_credential_ref")
        if _has_control(cr):
            raise ConfigError("provider.credentialRef: control characters not allowed", "invalid_credential_ref")
        if not re.fullmatch(_ID_RE, cr):
            raise ConfigError("provider.credentialRef: must be null or identifier", "invalid_credential_ref")
    elif cr is not None:
        if not isinstance(cr, str):
            raise ConfigError("provider.credentialRef: must be null or identifier", "invalid_credential_ref")
        if _has_control(cr):
            raise ConfigError("provider.credentialRef: control characters not allowed", "invalid_credential_ref")
        if not re.fullmatch(_ID_RE, cr):
            raise ConfigError("provider.credentialRef: must be null or identifier", "invalid_credential_ref")
    return {
        "id": pid, "label": lbl, "kind": kind, "baseUrl": base_url,
        "model": model, "contextTokens": ct, "maxOutputTokens": mot,
        "supportsTools": p["supportsTools"], "supportsVision": p["supportsVision"],
        "reasoning": p["reasoning"], "credentialRef": cr, "enabled": p["enabled"],
    }


def _validate_roles(roles, provider_map):
    if not isinstance(roles, dict):
        raise ConfigError("roles: must be an object", "invalid_type")
    exact_keys = {"leader", "backups", "advisor", "handoff"}
    if set(roles.keys()) != exact_keys:
        raise ConfigError("roles: unknown or missing keys", "unknown_keys")
    leader = roles["leader"]
    if leader is not None:
        _check_id(leader, "roles.leader")
        if leader not in provider_map:
            raise ConfigError("roles.leader: references unknown provider", "unknown_role_ref")
    backups = roles["backups"]
    if not isinstance(backups, list):
        raise ConfigError("roles.backups: must be a list", "invalid_type")
    if len(backups) > 8:
        raise ConfigError("roles.backups: too many entries", "range_error")
    backup_ids = set()
    for b in backups:
        _check_id(b, "roles.backups item")
        if b not in provider_map:
            raise ConfigError("roles.backups: references unknown provider", "unknown_role_ref")
        if b in backup_ids:
            raise ConfigError("roles.backups: duplicate entry", "duplicate_id")
        backup_ids.add(b)
    if leader is not None and leader in backup_ids:
        raise ConfigError("roles.leader cannot appear in backups", "role_conflict")
    for rname in ("advisor", "handoff"):
        rv = roles[rname]
        if rv is not None:
            _check_id(rv, f"roles.{rname}")
            if rv not in provider_map:
                raise ConfigError(f"roles.{rname}: references unknown provider", "unknown_role_ref")
    return copy.deepcopy(roles)


def _validate_policy(policy):
    if not isinstance(policy, dict):
        raise ConfigError("policy: must be an object", "invalid_type")
    exact_keys = {"allowCloud", "maxAttempts", "deadlineSeconds"}
    if set(policy.keys()) != exact_keys:
        raise ConfigError("policy: unknown or missing keys", "unknown_keys")
    _check_strict_bool(policy["allowCloud"], "policy.allowCloud")
    _check_int(policy["maxAttempts"], "policy.maxAttempts", 1, 9)
    _check_int(policy["deadlineSeconds"], "policy.deadlineSeconds", 1, 3600)
    return copy.deepcopy(policy)


_EXACT_ROOT = {"schemaVersion", "revision", "enabled", "providers",
               "roles", "policy"}


def normalize_config(value):
    """Deep-copy, validate, and return canonical config dict."""
    if not isinstance(value, dict):
        raise ConfigError("config must be a dict", "invalid_type")
    if set(value.keys()) != _EXACT_ROOT:
        raise ConfigError("unknown or missing root keys", "unknown_keys")
    sv = value["schemaVersion"]
    _check_int(sv, "schemaVersion")
    if sv != 1:
        raise ConfigError("schemaVersion must be 1", "invalid_value")
    _check_int(value["revision"], "revision", 0, 2**53 - 1)
    _check_strict_bool(value["enabled"], "enabled")
    providers_raw = value["providers"]
    if not isinstance(providers_raw, list) or len(providers_raw) > 32:
        raise ConfigError("providers: list of <=32 required", "invalid_type")
    seen = set()
    providers = [_validate_provider(p, "providers", seen) for p in providers_raw]
    pmap = {p["id"]: p for p in providers}
    roles = _validate_roles(value["roles"], pmap)
    policy = _validate_policy(value["policy"])
    if value["enabled"]:
        if roles["leader"] is None:
            raise ConfigError("leader required when enabled", "missing_leader")
        for pid in (roles["leader"], *roles["backups"], roles["advisor"], roles["handoff"]):
            if pid and not pmap[pid]["enabled"]:
                raise ConfigError("referenced provider must be enabled", "provider_not_enabled")
        if not policy["allowCloud"]:
            for pid in (roles["leader"], *roles["backups"], roles["advisor"], roles["handoff"]):
                if pid and pmap[pid]["kind"] == "cloud":
                    raise ConfigError("cloud provider not authorized", "cloud_not_authorized")
    return {
        "schemaVersion": 1, "revision": value["revision"], "enabled": value["enabled"],
        "providers": providers, "roles": roles, "policy": policy,
    }


def default_config():
    """Return a fresh independent default config."""
    return {
        "schemaVersion": 1, "revision": 0, "enabled": False,
        "providers": [],
        "roles": {"leader": None, "backups": [], "advisor": None, "handoff": None},
        "policy": {"allowCloud": False, "maxAttempts": 3, "deadlineSeconds": 120},
    }


def public_config(value):
    """Normalize then remove credentialRef, add hasCredential."""
    canon = normalize_config(value)
    out = copy.deepcopy(canon)
    for p in out["providers"]:
        p["hasCredential"] = p.pop("credentialRef") is not None
    return out
