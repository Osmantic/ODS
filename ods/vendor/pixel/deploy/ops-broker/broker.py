#!/usr/bin/env python3
"""Pixel Operations Broker.

The model-facing gateway may submit bounded JSON jobs and read sanitized results. This
separate process owns execution authority, target configuration, approvals, downloads,
and audit records. No credential value is returned through the projection directories.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import fcntl
import fnmatch
import hashlib
import http.client
import ipaddress
import json
import os
import posixpath
import re
import shlex
import signal
import socket
import ssl
import stat
import subprocess
import tempfile
import threading
import time
import urllib.parse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
SUPPORTED_REQUEST_VERSIONS = {1, 2}
SUPPORTED_POLICY_VERSIONS = {1, 2}
JOB_RE = re.compile(r"ops-[0-9]{13}-[a-f0-9]{12}")
STEP_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}")
ACTION_RE = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
TARGET_RE = re.compile(r"[a-z][a-z0-9_-]{1,63}")
PARAMETER_RE = re.compile(r"[a-z][A-Za-z0-9_]{0,63}")
GRANT_RE = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# The qualified *_KEY alternatives catch secret keys whose assignment lands on "KEY" rather than on
# a standalone secret word, e.g. AWS_SECRET_ACCESS_KEY=..., ENCRYPTION_KEY=..., SIGNING_KEY=...
# (bare KEY is intentionally excluded so PRIMARY_KEY/SORT_KEY/FOREIGN_KEY are not touched).
_SECRET_KEYWORDS = (
    r"password|passwd|secret|token|api[_-]?key|private[_-]?key"
    r"|access[_-]?key|secret[_-]?key|encryption[_-]?key|signing[_-]?key|consumer[_-]?key"
)
SECRET_RE = re.compile(
    rf"(?i)(authorization\s*:\s*bearer\s+|(?:{_SECRET_KEYWORDS})\s*[=:]\s*)([^\s,;]+)"
)
SECRET_COMMAND_RE = re.compile(
    rf"(?i)(authorization\s*:\s*bearer\s+\S+|--?(?:{_SECRET_KEYWORDS})\s*(?:=|\s)\s*\S+|https?://[^/@\s]+:[^/@\s]+@)"
)
OUTPUT_SIGNALS = {
    "instruction-override": re.compile(r"(?i)\b(ignore|disregard|override)\b.{0,100}\b(instructions?|polic(?:y|ies)|prompts?|rules?)\b"),
    "agent-targeting": re.compile(r"(?i)\b(pixel|assistant|agent|system prompt)\b.{0,120}\b(run|execute|tool|command|must|should)\b"),
    "credential-request": re.compile(r"(?i)\b(secret|credential|token|private key|\.ssh)\b.{0,100}\b(read|print|send|upload|copy|exfiltrate)\b"),
    "encoded-instruction": re.compile(r"(?i)\b(base64|rot13|hexadecimal|decode)\b.{0,100}\b(payload|instruction|command|next step)\b"),
}
RISK_ORDER = {"read": 0, "staging": 1, "managed": 2, "change": 3, "break-glass": 4}
AUTHORITY_LEVELS = {"disabled", "observe", "propose", "bounded-auto"}
ACTION_EFFECTS = {"observe", "stage", "manage", "change"}
TARGET_ENVIRONMENTS = {"development", "test", "staging", "production", "lab", "unclassified"}
TERMINAL = {"succeeded", "failed", "cancelled", "rejected"}
MAX_REQUEST_BYTES = 256 * 1024
MAX_POLICY_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024
INVENTORY_REFRESH_SECONDS = 30
SSH_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,253}")
DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
SHA256_RE = re.compile(r"[a-f0-9]{64}")
SENSITIVE_QUERY_KEYS = {
    "access_token", "access-token", "accesstoken", "api_key", "api-key", "apikey",
    "authorization", "auth_token", "auth-token", "authtoken", "credential", "credentials",
    "key", "password", "passwd", "private_key", "private-key", "privatekey", "secret",
    "signature", "token", "x-amz-credential", "x-amz-signature", "x-goog-credential",
    "x-goog-signature",
}


class BrokerError(RuntimeError):
    pass


class Cancelled(BrokerError):
    pass


@contextmanager
def locked_file(path: Path, *, blocking: bool = True):
    """Hold a process-wide advisory lock on a bounded, non-symlink state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise BrokerError("state lock is not a regular file")
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as error:
            raise BrokerError("another Operations Broker instance is active") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise BrokerError("artifact write made no progress")
        view = view[written:]


def atomic_json(path: Path, value: Any, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_regular_json(path: Path, limit: int) -> Any:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise BrokerError(f"{path.name} is not a bounded regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_size > limit:
            raise BrokerError(f"{path.name} changed during secure open")
        chunks, total = [], 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise BrokerError(f"{path.name} exceeds its size limit")
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        os.close(descriptor)


def safe_text(value: Any, limit: int = 8192) -> str:
    text = ANSI_RE.sub("", str(value or ""))
    text = CONTROL_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = SECRET_RE.sub(lambda match: f"{match.group(1)}[REDACTED]", text)
    return text[:limit]


def output_signals(value: str) -> list[str]:
    return sorted(name for name, pattern in OUTPUT_SIGNALS.items() if pattern.search(value))


def normalized_hostname(value: str) -> str:
    try:
        host = value.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as error:
        raise BrokerError("hostname is invalid") from error
    if not host or len(host) > 253 or any(not DNS_LABEL_RE.fullmatch(label) for label in host.split(".")):
        try:
            ipaddress.ip_address(host)
        except ValueError as error:
            raise BrokerError("hostname is invalid") from error
    return host


def domain_allowed(host: str, allowed_domains: list[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in allowed_domains)


def public_url(url: str, resolver=socket.getaddrinfo) -> tuple[urllib.parse.SplitResult, list[str]]:
    if not url or len(url) > 4096 or any(ord(character) < 32 for character in url):
        raise BrokerError("URL is empty, too long, or contains control characters")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError as error:
        raise BrokerError("URL could not be parsed") from error
    if parts.scheme != "https":
        raise BrokerError("only public HTTPS downloads are allowed")
    if parts.username is not None or parts.password is not None:
        raise BrokerError("credentials in URLs are not allowed")
    if parts.fragment:
        raise BrokerError("URL fragments are not allowed")
    host = normalized_hostname(parts.hostname or "")
    if not host or host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise BrokerError("private or local hostnames are not allowed")
    try:
        query_keys = [key.lower() for key, _value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True, strict_parsing=False)]
    except ValueError as error:
        raise BrokerError("URL query could not be parsed") from error
    if any(key in SENSITIVE_QUERY_KEYS for key in query_keys):
        raise BrokerError("credential-bearing URL query parameters are not allowed")
    try:
        addresses = [host] if _public_ip(host) else []
    except ValueError:
        addresses = []
    if not addresses:
        try:
            infos = resolver(host, port or (443 if parts.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        except (socket.gaierror, OSError) as error:
            raise BrokerError("DNS resolution failed") from error
        addresses = sorted({info[4][0] for info in infos})
        if not addresses:
            raise BrokerError("hostname resolved to no addresses")
        for address in addresses:
            if not _public_ip(address):
                raise BrokerError("hostname resolves to a forbidden network address")
    return parts, addresses


def _public_ip(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if not address.is_global:
        raise BrokerError("private, loopback, link-local, multicast, reserved, or unspecified addresses are forbidden")
    return True


def safe_url_for_log(value: str) -> str:
    try:
        parts = urllib.parse.urlsplit(value)
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host + (f":{parts.port}" if parts.port else "")
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", ""))[:2048]
    except ValueError:
        return "<invalid-url>"


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, address: str, port: int, server_name: str, timeout: float):
        super().__init__(server_name, port=port, timeout=timeout, context=ssl.create_default_context())
        self._address = address
        self._server_name = server_name

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self._server_name)


def highest_risk(values: list[str]) -> str:
    if not values:
        return "read"
    for value in values:
        if value not in RISK_ORDER:
            raise BrokerError(f"unknown risk tier: {value}")
    return max(values, key=RISK_ORDER.__getitem__)


def normalized_remote_path(value: str) -> str:
    if not value.startswith("/") or "\x00" in value:
        raise BrokerError("working directory must be an absolute POSIX path")
    return posixpath.normpath(value)


def path_allowed(value: str, roots: list[str]) -> bool:
    candidate = normalized_remote_path(value)
    for root in roots:
        allowed = normalized_remote_path(str(root))
        if candidate == allowed or candidate.startswith(allowed.rstrip("/") + "/"):
            return True
    return False


def open_hashed_artifact(path: Path, maximum: int) -> tuple[int, os.stat_result, str]:
    """Open and hash one artifact once so path replacement cannot change the transfer."""
    initial = path.lstat()
    if not stat.S_ISREG(initial.st_mode) or path.is_symlink() or initial.st_size > maximum:
        raise BrokerError("artifact source is not a bounded regular non-symlink file")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size) != (initial.st_dev, initial.st_ino, initial.st_size)
            or opened.st_size > maximum
        ):
            raise BrokerError("artifact source changed during secure open")
        checksum = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            checksum.update(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return descriptor, opened, checksum.hexdigest()
    except Exception:
        os.close(descriptor)
        raise


def effect_for_tier(tier: str) -> str:
    return {
        "read": "observe",
        "staging": "stage",
        "managed": "manage",
        "change": "change",
        "break-glass": "change",
    }[tier]


def default_authority_for_tier(tier: str) -> str:
    return "observe" if tier == "read" else "propose"


def parse_utc(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise BrokerError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise BrokerError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def bounded_integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise BrokerError(f"{label} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise BrokerError(f"{label} must be an integer") from error
    if isinstance(value, float) and not value.is_integer():
        raise BrokerError(f"{label} must be an integer")
    if not minimum <= number <= maximum:
        raise BrokerError(f"{label} is outside its safe range")
    return number


def safe_parameter_pattern(value: Any, label: str) -> str:
    """Accept a deliberately small, linear-time regex dialect for model values."""
    pattern = str(value)
    if len(pattern) > 1000 or not pattern.startswith("^") or not pattern.endswith("$"):
        raise BrokerError(f"{label} requires a bounded anchored pattern")
    body = pattern[1:-1]
    index = 0
    previous_atom = ""
    group_depth = 0
    while index < len(body):
        character = body[index]
        if character == "\\":
            index += 1
            if index >= len(body) or body[index].isdigit():
                raise BrokerError(f"{label} contains an unsafe escape")
            previous_atom = "class" if body[index] in "dDsSwW" else "literal"
        elif character == "[":
            end = index + 1
            escaped = False
            while end < len(body):
                if not escaped and body[end] == "]":
                    break
                escaped = (not escaped and body[end] == "\\")
                if body[end] != "\\":
                    escaped = False
                end += 1
            if end >= len(body) or end == index + 1:
                raise BrokerError(f"{label} contains an invalid character class")
            index = end
            previous_atom = "class"
        elif character == "(":
            if index + 1 < len(body) and body[index + 1] == "?":
                raise BrokerError(f"{label} contains an unsafe regex extension")
            group_depth += 1
            previous_atom = ""
        elif character == ")":
            if group_depth == 0:
                raise BrokerError(f"{label} contains an unmatched group")
            group_depth -= 1
            previous_atom = "group"
        elif character == "|":
            if group_depth == 0:
                raise BrokerError(f"{label} may only use alternatives inside a group")
            previous_atom = ""
        elif character in "*+?":
            if previous_atom != "class":
                raise BrokerError(f"{label} may only repeat a character class")
            previous_atom = "quantifier"
        elif character == "{":
            end = body.find("}", index + 1)
            if previous_atom != "class" or end < 0 or not re.fullmatch(r"[0-9]+(?:,[0-9]*)?", body[index + 1:end]):
                raise BrokerError(f"{label} contains an unsafe repetition")
            limits = body[index + 1:end].split(",", 1)
            minimum = int(limits[0])
            maximum = int(limits[1]) if len(limits) == 2 and limits[1] else 4096
            if minimum > maximum or maximum > 4096:
                raise BrokerError(f"{label} repetition is outside its safe range")
            index = end
            previous_atom = "quantifier"
        elif character in ".^$":
            raise BrokerError(f"{label} contains an unsafe metacharacter")
        else:
            previous_atom = "literal"
        index += 1
    if group_depth:
        raise BrokerError(f"{label} contains an unmatched group")
    try:
        re.compile(pattern)
    except re.error as error:
        raise BrokerError(f"{label} is invalid") from error
    return pattern


def migrate_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schemaVersion") not in SUPPORTED_POLICY_VERSIONS:
        raise BrokerError("operations policy must use schemaVersion 1 or 2")
    migrated = copy.deepcopy(value)
    if migrated["schemaVersion"] == 2:
        return migrated
    legacy_auto_tiers = migrated.get("autoTiers", ["read"])
    migrated["schemaVersion"] = 2
    migrated["migratedFromSchemaVersion"] = 1
    for target in migrated.get("targets", {}).values():
        if isinstance(target, dict):
            target.setdefault("environment", "unclassified")
    for action in migrated.get("actions", {}).values():
        if isinstance(action, dict) and action.get("tier") in RISK_ORDER:
            action.setdefault("effect", effect_for_tier(action["tier"]))
            action.setdefault("defaultAuthority", default_authority_for_tier(action["tier"]))
            action.setdefault("idempotent", action["tier"] == "read")
            action.setdefault("reversible", False)
    grants = []
    if isinstance(legacy_auto_tiers, list):
        for tier in legacy_auto_tiers:
            if tier in RISK_ORDER and tier != "break-glass":
                grants.append({
                    "id": f"legacy-auto-{tier}",
                    "level": "bounded-auto",
                    "actions": ["*"],
                    "targets": ["*"],
                    "tiers": [tier],
                    "environments": sorted(TARGET_ENVIRONMENTS),
                    "allowProduction": True,
                    "compatibilityV1": True,
                })
    migrated["authority"] = {
        "defaultLevel": "propose",
        "grants": grants,
        "migrationNotice": "Legacy autoTiers were converted in memory; write schemaVersion 2 with explicit action grants.",
    }
    migrated.pop("autoTiers", None)
    return migrated


def validate_selector(value: Any, label: str, pattern: re.Pattern[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BrokerError(f"{label} must be a non-empty list")
    result = []
    for item in value:
        text = str(item)
        if text != "*" and ("?" in text or "[" in text or "]" in text or not pattern.fullmatch(text.replace("*", "a"))):
            raise BrokerError(f"{label} contains an unsafe selector")
        result.append(text)
    return result


def validate_grant(value: Any, *, source: str = "policy") -> dict[str, Any]:
    if not isinstance(value, dict) or not GRANT_RE.fullmatch(str(value.get("id", ""))):
        raise BrokerError(f"{source} authority grant requires a safe id")
    grant = copy.deepcopy(value)
    for flag in ("allowProduction", "compatibilityV1"):
        if flag in grant and not isinstance(grant[flag], bool):
            raise BrokerError(f"authority grant {grant['id']} {flag} must be boolean")
    if grant.get("level") != "bounded-auto":
        raise BrokerError(f"authority grant {grant['id']} must use level bounded-auto")
    grant["actions"] = validate_selector(grant.get("actions"), f"authority grant {grant['id']} actions", ACTION_RE)
    grant["targets"] = validate_selector(grant.get("targets"), f"authority grant {grant['id']} targets", TARGET_RE)
    tiers = grant.get("tiers", ["read", "staging"])
    if not isinstance(tiers, list) or not tiers or any(tier not in RISK_ORDER or tier == "break-glass" for tier in tiers):
        raise BrokerError(f"authority grant {grant['id']} has invalid tiers")
    grant["tiers"] = list(dict.fromkeys(tiers))
    environments = grant.get("environments", ["development", "test", "staging", "lab"])
    if not isinstance(environments, list) or not environments or any(item not in TARGET_ENVIRONMENTS for item in environments):
        raise BrokerError(f"authority grant {grant['id']} has invalid environments")
    grant["environments"] = list(dict.fromkeys(environments))
    labels = grant.get("targetLabels", [])
    if not isinstance(labels, list) or any(not TARGET_RE.fullmatch(str(item)) for item in labels):
        raise BrokerError(f"authority grant {grant['id']} has invalid targetLabels")
    grant["targetLabels"] = [str(item) for item in labels]
    constraints = grant.get("parameterConstraints", {})
    if not isinstance(constraints, dict) or any(not PARAMETER_RE.fullmatch(str(name)) for name in constraints):
        raise BrokerError(f"authority grant {grant['id']} has invalid parameterConstraints")
    for name, rules in constraints.items():
        if not isinstance(rules, dict):
            raise BrokerError(f"authority grant {grant['id']} parameter constraint {name} is invalid")
        if "values" in rules:
            if (
                not isinstance(rules["values"], list) or not rules["values"] or len(rules["values"]) > 100
                or any(not isinstance(item, (str, int)) or isinstance(item, bool) or "\x00" in str(item) or len(str(item)) > 4096 for item in rules["values"])
            ):
                raise BrokerError(f"authority grant {grant['id']} parameter values are invalid")
            rules["values"] = [str(item) for item in rules["values"]]
        if "pattern" in rules:
            rules["pattern"] = safe_parameter_pattern(rules["pattern"], f"authority grant {grant['id']} parameter {name}")
        if set(rules) - {"values", "pattern"} or not rules:
            raise BrokerError(f"authority grant {grant['id']} parameter constraint {name} has unknown or empty rules")
    for name, minimum, maximum in (
        ("maxExecutions", 1, 100_000),
        ("windowSeconds", 60, 31_536_000),
        ("maxConcurrent", 1, 32),
        ("maxRuntimeSeconds", 1, 86_400),
        ("maxFailures", 1, 10_000),
        ("maxOutputBytes", 1024, 16 * 1024 * 1024),
        ("maxArtifactBytes", 1, 2 * 1024 * 1024 * 1024),
    ):
        if name in grant:
            grant[name] = bounded_integer(grant[name], f"authority grant {grant['id']} {name}", minimum, maximum)
    if ("maxExecutions" in grant) != ("windowSeconds" in grant):
        raise BrokerError(f"authority grant {grant['id']} requires maxExecutions and windowSeconds together")
    if "notBefore" in grant:
        parse_utc(grant["notBefore"], f"authority grant {grant['id']} notBefore")
    if "expiresAt" in grant:
        parse_utc(grant["expiresAt"], f"authority grant {grant['id']} expiresAt")
    if "notBefore" in grant and "expiresAt" in grant and parse_utc(grant["notBefore"], "notBefore") >= parse_utc(grant["expiresAt"], "expiresAt"):
        raise BrokerError(f"authority grant {grant['id']} expires before it becomes active")
    grant["source"] = source
    return grant


def validate_policy(value: Any) -> dict[str, Any]:
    value = migrate_policy(value)
    if not isinstance(value.get("targets"), dict) or not isinstance(value.get("actions"), dict):
        raise BrokerError("operations policy requires targets and actions objects")
    policy_fields = {
        "schemaVersion", "deployment", "maxWorkers", "workflowWorkers", "maxWorkflowSteps",
        "defaultTimeoutSeconds", "maxTimeoutSeconds", "maxOutputBytes", "planTtlMinutes",
        "identityCacheSeconds", "sshBinary", "download", "targets", "actions", "authority",
        "migratedFromSchemaVersion", "compatibilityGrantsRemovedAt",
    }
    if set(value) - policy_fields:
        raise BrokerError("operations policy contains unknown fields")
    ssh_binary = value.get("sshBinary", "/usr/bin/ssh")
    if (
        not isinstance(ssh_binary, str) or len(ssh_binary) > 4096
        or not Path(ssh_binary).is_absolute() or Path(ssh_binary).name != "ssh"
        or os.path.normpath(ssh_binary) != ssh_binary
    ):
        raise BrokerError("operations policy sshBinary must be an absolute normalized ssh executable path")
    value["sshBinary"] = ssh_binary
    deployment = value.get("deployment", "pixel")
    if not isinstance(deployment, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", deployment):
        raise BrokerError("operations policy deployment identifier is unsafe")
    for field, default, minimum, maximum in (
        ("maxWorkers", 4, 1, 32),
        ("workflowWorkers", 4, 1, 16),
        ("maxWorkflowSteps", 32, 1, 128),
        ("defaultTimeoutSeconds", 60, 1, 3600),
        ("maxTimeoutSeconds", 3600, 1, 86400),
        ("maxOutputBytes", 256 * 1024, 1024, 16 * 1024 * 1024),
        ("planTtlMinutes", 30, 1, 1440),
        ("identityCacheSeconds", 30, 0, 300),
    ):
        value[field] = bounded_integer(value.get(field, default), f"operations policy {field}", minimum, maximum)
    if value["defaultTimeoutSeconds"] > value["maxTimeoutSeconds"]:
        raise BrokerError("operations policy default timeout exceeds its maximum")
    download = value.get("download", {})
    if not isinstance(download, dict):
        raise BrokerError("operations policy download must be an object")
    staging_root = download.get("stagingRoot", "/var/lib/pixel-ops-broker/artifacts")
    if (
        not isinstance(staging_root, str)
        or staging_root == "/"
        or normalized_remote_path(staging_root) != staging_root
    ):
        raise BrokerError("operations policy download stagingRoot must be an absolute normalized non-root path")
    allowed_domains = download.get("allowedDomains", [])
    if not isinstance(allowed_domains, list) or len(allowed_domains) > 100:
        raise BrokerError("operations policy download allowedDomains must be a bounded list")
    normalized_domains: list[str] = []
    for domain in allowed_domains:
        if not isinstance(domain, str):
            raise BrokerError("operations policy download allowedDomains entries must be hostnames")
        normalized = normalized_hostname(domain)
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            pass
        else:
            raise BrokerError("operations policy download allowedDomains cannot contain IP addresses")
        normalized_domains.append(normalized)
    download["stagingRoot"] = staging_root
    download["maxBytes"] = bounded_integer(
        download.get("maxBytes", 512 * 1024 * 1024), "operations policy download maxBytes", 1, 2 * 1024 * 1024 * 1024,
    )
    download["maxRedirects"] = bounded_integer(
        download.get("maxRedirects", 5), "operations policy download maxRedirects", 0, 10,
    )
    download["allowedDomains"] = list(dict.fromkeys(normalized_domains))
    if set(download) - {"stagingRoot", "maxBytes", "maxRedirects", "allowedDomains"}:
        raise BrokerError("operations policy download contains unknown fields")
    value["download"] = download
    for name, target in value["targets"].items():
        if not TARGET_RE.fullmatch(name) or not isinstance(target, dict):
            raise BrokerError(f"invalid target: {name}")
        if target.get("backend") not in {"local", "ssh"}:
            raise BrokerError(f"target {name} has unsupported backend")
        if not target.get("expectedHostname"):
            raise BrokerError(f"target {name} requires expectedHostname")
        if target.get("backend") == "ssh" and not target.get("sshHost"):
            raise BrokerError(f"target {name} requires sshHost")
        for flag in ("enabled", "allowRaw", "dedicatedRunner", "ephemeralRunner"):
            if flag in target and not isinstance(target[flag], bool):
                raise BrokerError(f"target {name} {flag} must be boolean")
        target_fields = {
            "enabled", "backend", "sshHost", "expectedHostname", "environment", "defaultCwd",
            "allowedRoots", "writableRoots", "shell", "dedicatedRunner", "ephemeralRunner",
            "allowRaw", "labels", "capabilities",
        }
        if set(target) - target_fields:
            raise BrokerError(f"target {name} contains unknown fields")
        if target.get("backend") != "ssh" and (target.get("dedicatedRunner") is True or target.get("ephemeralRunner") is True):
            raise BrokerError(f"target {name} cannot claim runner isolation on a local backend")
        if target.get("backend") == "ssh" and not SSH_HOST_RE.fullmatch(str(target["sshHost"])):
            raise BrokerError(f"target {name} has an unsafe SSH host alias")
        expected_hostname = str(target.get("expectedHostname", ""))
        if len(expected_hostname) > 253 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", expected_hostname):
            raise BrokerError(f"target {name} has an unsafe expectedHostname")
        roots = target.get("allowedRoots", [])
        if (
            not isinstance(roots, list) or not roots
            or any(not isinstance(item, str) or item == "/" or normalized_remote_path(item) != item for item in roots)
        ):
            raise BrokerError(f"target {name} has invalid allowedRoots")
        default_cwd = target.get("defaultCwd", roots[0])
        if not isinstance(default_cwd, str) or normalized_remote_path(default_cwd) != default_cwd or not path_allowed(default_cwd, roots):
            raise BrokerError(f"target {name} has an invalid defaultCwd")
        target["defaultCwd"] = default_cwd
        writable_roots = target.get("writableRoots", [])
        if (
            not isinstance(writable_roots, list)
            or any(
                not isinstance(item, str) or item == "/" or normalized_remote_path(item) != item
                or not path_allowed(item, roots)
                for item in writable_roots
            )
            or (writable_roots and target.get("backend") != "local")
        ):
            raise BrokerError(f"target {name} has invalid writableRoots")
        if target.get("shell", "/bin/bash") not in {"/bin/bash", "/bin/sh"}:
            raise BrokerError(f"target {name} has an unsafe break-glass shell")
        labels = target.get("labels", [])
        if not isinstance(labels, list) or any(not isinstance(item, str) or not TARGET_RE.fullmatch(item) for item in labels):
            raise BrokerError(f"target {name} has invalid labels")
        capabilities = target.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(item, str) or not ACTION_RE.fullmatch(item) for item in capabilities):
            raise BrokerError(f"target {name} has invalid capabilities")
        environment = target.get("environment", "unclassified")
        if environment not in TARGET_ENVIRONMENTS:
            raise BrokerError(f"target {name} has an invalid environment")
        target["environment"] = environment
    for name, action in value["actions"].items():
        if not ACTION_RE.fullmatch(name) or not isinstance(action, dict):
            raise BrokerError(f"invalid action: {name}")
        if action.get("tier") not in RISK_ORDER:
            raise BrokerError(f"action {name} has an invalid tier")
        action_fields = {
            "description", "tier", "effect", "defaultAuthority", "idempotent", "reversible",
            "rollbackAction", "verificationAction", "targets", "parameters", "argv", "cwd",
            "timeoutSeconds", "exclusiveTarget", "isolation",
        }
        if set(action) - action_fields:
            raise BrokerError(f"action {name} contains unknown fields")
        if not isinstance(action.get("description", ""), str) or len(action.get("description", "")) > 500:
            raise BrokerError(f"action {name} has an invalid description")
        argv = action.get("argv")
        if (
            not isinstance(argv, list) or not 1 <= len(argv) <= 128
            or any(not isinstance(item, str) or "\x00" in item or len(item) > 16_384 for item in argv)
        ):
            raise BrokerError(f"action {name} requires a safe argv array")
        parameters = action.get("parameters", {})
        if not isinstance(parameters, dict) or any(not PARAMETER_RE.fullmatch(key) for key in parameters):
            raise BrokerError(f"action {name} has invalid parameter definitions")
        for parameter_name, rules in parameters.items():
            if not isinstance(rules, dict):
                raise BrokerError(f"action {name} parameter {parameter_name} must be an object")
            rules["pattern"] = safe_parameter_pattern(
                rules.get("pattern", r"^[A-Za-z0-9._/:@+-]+$"),
                f"action {name} parameter {parameter_name}",
            )
            rules["maxLength"] = bounded_integer(
                rules.get("maxLength", 1000), f"action {name} parameter {parameter_name} maxLength", 1, 4096,
            )
            if set(rules) - {"pattern", "maxLength"}:
                raise BrokerError(f"action {name} parameter {parameter_name} has unknown rules")
        permitted_targets = action.get("targets", ["*"])
        if not isinstance(permitted_targets, list) or not permitted_targets or any(
            item != "*" and item not in value["targets"] for item in permitted_targets
        ):
            raise BrokerError(f"action {name} has invalid targets")
        if "exclusiveTarget" in action and not isinstance(action["exclusiveTarget"], bool):
            raise BrokerError(f"action {name} exclusiveTarget must be boolean")
        if action.get("isolation", "none") not in {"none", "dedicated-runner", "ephemeral"}:
            raise BrokerError(f"action {name} has an invalid isolation mode")
        placeholders = set()
        for argument in [*argv, str(action.get("cwd", ""))]:
            placeholders.update(re.findall(r"\{([A-Za-z][A-Za-z0-9_]*)\}", argument))
        if placeholders != set(parameters):
            raise BrokerError(f"action {name} parameters and placeholders must match exactly")
        if re.search(r"\{[A-Za-z][A-Za-z0-9_]*\}", argv[0]):
            raise BrokerError(f"action {name} cannot parameterize its executable")
        action.setdefault("effect", effect_for_tier(action["tier"]))
        if action["effect"] not in ACTION_EFFECTS or action["effect"] != effect_for_tier(action["tier"]):
            raise BrokerError(f"action {name} effect does not match its risk tier")
        action.setdefault("defaultAuthority", default_authority_for_tier(action["tier"]))
        if action["defaultAuthority"] not in AUTHORITY_LEVELS or action["defaultAuthority"] == "bounded-auto":
            raise BrokerError(f"action {name} must default to disabled, observe, or propose")
        for field in ("idempotent", "reversible"):
            if field in action and not isinstance(action[field], bool):
                raise BrokerError(f"action {name} {field} must be boolean")
        action.setdefault("idempotent", action["tier"] == "read")
        action.setdefault("reversible", False)
        for field in ("rollbackAction", "verificationAction"):
            if field in action and not ACTION_RE.fullmatch(str(action[field])):
                raise BrokerError(f"action {name} has an invalid {field}")
        action["timeoutSeconds"] = bounded_integer(
            action.get("timeoutSeconds", value["defaultTimeoutSeconds"]),
            f"action {name} timeoutSeconds", 1, value["maxTimeoutSeconds"],
        )
    for name, action in value["actions"].items():
        for field in ("rollbackAction", "verificationAction"):
            reference = action.get(field)
            if reference and reference not in value["actions"]:
                raise BrokerError(f"action {name} references unknown {field} {reference}")
        verification = action.get("verificationAction")
        if verification:
            verification_action = value["actions"][verification]
            if verification_action["tier"] not in {"read", "staging"}:
                raise BrokerError(f"action {name} verificationAction must be read or staging tier")
            if not set(verification_action.get("parameters", {})) <= set(action.get("parameters", {})):
                raise BrokerError(f"action {name} verificationAction requires incompatible parameters")
            action_targets = set(action.get("targets", ["*"]))
            verification_targets = set(verification_action.get("targets", ["*"]))
            if "*" not in verification_targets and not action_targets <= verification_targets:
                raise BrokerError(f"action {name} verificationAction does not cover all action targets")
        rollback = action.get("rollbackAction")
        if rollback:
            rollback_action = value["actions"][rollback]
            if rollback_action["tier"] not in {"managed", "change"}:
                raise BrokerError(f"action {name} rollbackAction must be managed or change tier")
            if not set(rollback_action.get("parameters", {})) <= set(action.get("parameters", {})):
                raise BrokerError(f"action {name} rollbackAction requires incompatible parameters")
            action_targets = set(action.get("targets", ["*"]))
            rollback_targets = set(rollback_action.get("targets", ["*"]))
            if "*" not in rollback_targets and not action_targets <= rollback_targets:
                raise BrokerError(f"action {name} rollbackAction does not cover all action targets")
    authority = value.get("authority", {})
    if not isinstance(authority, dict):
        raise BrokerError("operations policy authority must be an object")
    default_level = authority.get("defaultLevel", "propose")
    if default_level not in {"disabled", "observe", "propose"}:
        raise BrokerError("authority defaultLevel must be disabled, observe, or propose")
    grants = authority.get("grants", [])
    if not isinstance(grants, list):
        raise BrokerError("authority grants must be a list")
    validated_grants = [validate_grant(grant) for grant in grants]
    identifiers = [grant["id"] for grant in validated_grants]
    if len(identifiers) != len(set(identifiers)):
        raise BrokerError("authority grant ids must be unique")
    for grant in validated_grants:
        elevated = any(tier in {"managed", "change"} for tier in grant["tiers"])
        if grant.get("compatibilityV1") and elevated:
            raise BrokerError(f"authority grant {grant['id']} cannot use legacy compatibility for elevated authority")
        if elevated:
            if "*" in grant["actions"] or "*" in grant["targets"]:
                raise BrokerError(f"authority grant {grant['id']} cannot wildcard managed or change actions or targets")
            if "maxExecutions" not in grant or "maxFailures" not in grant or "maxConcurrent" not in grant:
                raise BrokerError(f"authority grant {grant['id']} requires execution, failure, and concurrency budgets")
            for action_name in grant["actions"]:
                if action_name not in value["actions"]:
                    raise BrokerError(f"authority grant {grant['id']} references unknown elevated action {action_name}")
                expected_parameters = set(value["actions"][action_name].get("parameters", {}))
                if set(grant.get("parameterConstraints", {})) != expected_parameters:
                    raise BrokerError(f"authority grant {grant['id']} must constrain every parameter of elevated action {action_name}")
        for target_name in grant["targets"]:
            if target_name != "*" and target_name not in value["targets"]:
                raise BrokerError(f"authority grant {grant['id']} references unknown target {target_name}")
        if "production" in grant["environments"] and not grant.get("allowProduction", False):
            raise BrokerError(f"authority grant {grant['id']} must explicitly allow production")
    value["authority"] = {**authority, "defaultLevel": default_level, "grants": validated_grants}
    return value


def selector_matches(patterns: list[str], value: str) -> bool:
    return any(pattern == "*" or fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def grant_is_active(grant: dict[str, Any], now: datetime) -> bool:
    if "notBefore" in grant and now < parse_utc(grant["notBefore"], f"authority grant {grant['id']} notBefore"):
        return False
    if "expiresAt" in grant and now >= parse_utc(grant["expiresAt"], f"authority grant {grant['id']} expiresAt"):
        return False
    return True


def grant_parameters_match(grant: dict[str, Any], parameters: dict[str, str], elevated: bool) -> bool:
    constraints = grant.get("parameterConstraints", {})
    if elevated and set(parameters) - set(constraints):
        return False
    for name, rules in constraints.items():
        if name not in parameters:
            return False
        value = parameters[name]
        if "values" in rules and value not in {str(item) for item in rules["values"]}:
            return False
        if "pattern" in rules:
            try:
                if re.fullmatch(str(rules["pattern"]), value) is None:
                    return False
            except re.error:
                return False
    return True


def matching_grants(
    policy: dict[str, Any], step: dict[str, Any], extra_grants: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current = now or utc_now()
    target = policy["targets"].get(step["target"], {})
    environment = target.get("environment", "lab" if step["target"] == "broker" else "unclassified")
    labels = set(str(item) for item in target.get("labels", []))
    candidates = list(policy.get("authority", {}).get("grants", [])) + list(extra_grants or [])
    result = []
    for grant in candidates:
        if not grant_is_active(grant, current):
            continue
        if step["tier"] not in grant["tiers"]:
            continue
        if not selector_matches(grant["actions"], step["action"]) or not selector_matches(grant["targets"], step["target"]):
            continue
        if environment not in grant["environments"]:
            continue
        if grant.get("targetLabels") and not set(grant["targetLabels"]) <= labels:
            continue
        if step["timeoutSeconds"] > int(grant.get("maxRuntimeSeconds", step["timeoutSeconds"])):
            continue
        elevated = step["tier"] in {"managed", "change"}
        if not grant_parameters_match(grant, step.get("parameters", {}), elevated):
            continue
        if environment == "production" and (not grant.get("allowProduction") or grant.get("source") != "lease"):
            continue
        if step["tier"] == "change" and grant.get("source") != "lease":
            continue
        result.append(grant)
    return result


def resolve_authority(
    policy: dict[str, Any], step: dict[str, Any], extra_grants: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    action = policy["actions"].get(step["action"], {})
    default_level = action.get("defaultAuthority", default_authority_for_tier(step["tier"]))
    if step["tier"] == "break-glass" or step["action"] == "raw-shell":
        return {
            "level": "propose", "outcome": "approval-required", "grantId": None,
            "source": "hard-safety-rule", "reason": "break-glass shell always requires exact-plan approval",
        }
    if default_level == "disabled":
        return {
            "level": "disabled", "outcome": "rejected", "grantId": None,
            "source": "action-default", "reason": "action is disabled by policy",
        }
    if default_level == "observe" and step["tier"] != "read":
        return {
            "level": "observe", "outcome": "rejected", "grantId": None,
            "source": "action-default", "reason": "observe authority cannot propose or execute a state-changing action",
        }
    eligible = bool(step.get("autoEligible", False))
    candidates = matching_grants(policy, step, extra_grants, now)
    if candidates and eligible:
        def score(grant: dict[str, Any]) -> tuple[int, int, int, int, str]:
            return (
                1 if grant.get("source") == "lease" else 0,
                1 if "*" not in grant["actions"] else 0,
                1 if "*" not in grant["targets"] else 0,
                -len(grant.get("environments", [])),
                grant["id"],
            )
        selected = max(candidates, key=score)
        if step["tier"] in {"managed", "change"}:
            rollback_capable = bool(step.get("rollbackAction")) or step["action"].endswith(".rollback")
            if not step.get("reversible") or not step.get("verificationAction") or not rollback_capable:
                return {
                    "level": "propose", "outcome": "approval-required", "grantId": None,
                    "source": "hard-safety-rule", "reason": "autonomous state change requires rollback and verification metadata",
                }
        public = {key: value for key, value in selected.items() if key not in {"parameterConstraints"}}
        resource_limits = {
            key: selected[key] for key in ("maxRuntimeSeconds", "maxOutputBytes", "maxArtifactBytes") if key in selected
        }
        return {
            "level": "bounded-auto", "outcome": "execute", "grantId": selected["id"],
            "source": selected.get("source", "policy"), "reason": "request matches an active bounded authority grant",
            "constraintsHash": digest(public),
            "resourceLimits": resource_limits,
        }
    if default_level == "observe" and step["tier"] == "read" and eligible:
        return {
            "level": "observe", "outcome": "execute", "grantId": None,
            "source": "action-default", "reason": "read-only observation is enabled",
        }
    return {
        "level": "propose", "outcome": "approval-required", "grantId": None,
        "source": "action-default", "reason": "no active bounded authority grant matched",
    }


def policy_inventory(policy: dict[str, Any]) -> dict[str, Any]:
    targets = []
    for name, target in policy["targets"].items():
        if target.get("enabled", True):
            targets.append({
                "id": name,
                "backend": target["backend"],
                "labels": target.get("labels", []),
                "capabilities": target.get("capabilities", []),
                "expectedHostname": target["expectedHostname"],
                "environment": target.get("environment", "unclassified"),
            })
    actions = []
    for name, action in policy["actions"].items():
        actions.append({
            "id": name,
            "requestKind": "action",
            "callableVia": "pixel_ops_run",
            "description": safe_text(action.get("description", ""), 500),
            "tier": action["tier"],
            "effect": action.get("effect", effect_for_tier(action["tier"])),
            "defaultAuthority": action.get("defaultAuthority", default_authority_for_tier(action["tier"])),
            "idempotent": action.get("idempotent", False),
            "reversible": action.get("reversible", False),
            "targets": action.get("targets", ["*"]),
            "parameters": sorted(action.get("parameters", {}).keys()),
        })
    # Broker-native operations are not authored in the policy file but are part of
    # the validated execution surface. Project them truthfully so reconciliation is
    # complete. download.stage auto-eligibility is request-host dependent (the host
    # must be in allowedDomains); never claim unconditional auto authority here.
    actions.append({
        "id": "download.stage",
        "requestKind": "download",
        "callableVia": "pixel_ops_download_stage",
        "tier": "staging",
        "effect": "stage",
        "defaultAuthority": "propose",
        "idempotent": False,
        "reversible": True,
        "targets": ["broker"],
        "autoEligible": False,
        "autoEligibleWhen": "host-in-allowedDomains",
        "authorityCondition": {
            "kind": "download-host-allowlist",
            "whenMatched": {"tier": "staging", "effect": "stage", "autoEligible": True},
            "whenNotMatched": {"tier": "change", "effect": "change", "autoEligible": False},
        },
        "parameters": ["expectedSha256", "filename", "timeoutSeconds", "url"],
    })
    for name, target in policy["targets"].items():
        if (
            target.get("enabled", True)
            and target.get("backend") == "ssh"
            and target.get("dedicatedRunner") is True
        ):
            actions.append({
                "id": "artifact.transfer",
                "requestKind": "transfer",
                "callableVia": "pixel_ops_artifact_transfer",
                "tier": "staging",
                "effect": "stage",
                "defaultAuthority": "propose",
                "idempotent": False,
                "reversible": True,
                "targets": [name],
                "autoEligible": True,
                "parameters": ["filename", "sourceJobId", "timeoutSeconds"],
            })
    download = policy.get("download", {})
    download_limits = {
        "allowedDomains": download.get("allowedDomains", []),
        "maxBytes": download.get("maxBytes"),
        "maxRedirects": download.get("maxRedirects"),
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": iso(),
        "policySha256": digest(policy),
        "boundary": "Inventory only. It grants no execution authority.",
        "download": download_limits,
        "authority": {
            "defaultLevel": policy.get("authority", {}).get("defaultLevel", "propose"),
            "standingGrantIds": [grant["id"] for grant in policy.get("authority", {}).get("grants", [])],
            "boundary": "Only the external broker can issue, consume, or revoke authority.",
        },
        "targets": targets,
        "actions": actions,
    }


def validate_parameters(specification: dict[str, Any], supplied: Any) -> dict[str, str]:
    values = supplied if isinstance(supplied, dict) else {}
    if set(values) != set(specification):
        missing = sorted(set(specification) - set(values))
        extra = sorted(set(values) - set(specification))
        raise BrokerError(f"action parameters do not match policy (missing={missing}, extra={extra})")
    result: dict[str, str] = {}
    for name, rules in specification.items():
        if not isinstance(rules, dict):
            raise BrokerError(f"parameter policy is invalid: {name}")
        value = str(values[name])
        if "\x00" in value or len(value) > min(int(rules.get("maxLength", 1000)), 4096):
            raise BrokerError(f"parameter is too long or contains NUL: {name}")
        pattern = str(rules.get("pattern", r"^[A-Za-z0-9._/:@+-]+$"))
        if len(pattern) > 1000 or re.fullmatch(pattern, value) is None:
            raise BrokerError(f"parameter does not match policy: {name}")
        result[name] = value
    return result


def interpolate(value: str, parameters: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise BrokerError(f"unknown action placeholder: {name}")
        return parameters[name]
    return re.sub(r"\{([A-Za-z][A-Za-z0-9_]*)\}", replace, value)


def compile_action(policy: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    target_name, action_name = str(item.get("target", "")), str(item.get("action", ""))
    target, action = policy["targets"].get(target_name), policy["actions"].get(action_name)
    if not TARGET_RE.fullmatch(target_name) or not target or not target.get("enabled", True):
        raise BrokerError("target is unknown or disabled")
    if not ACTION_RE.fullmatch(action_name) or not action:
        raise BrokerError("action is unknown")
    permitted = action.get("targets", ["*"])
    if "*" not in permitted and target_name not in permitted:
        raise BrokerError("action is not permitted on this target")
    parameters = validate_parameters(action.get("parameters", {}), item.get("parameters", {}))
    argv = [interpolate(argument, parameters) for argument in action["argv"]]
    if any(SECRET_COMMAND_RE.search(argument) for argument in argv):
        raise BrokerError("literal credentials are forbidden in operation arguments")
    cwd = interpolate(str(action.get("cwd", target.get("defaultCwd", "/"))), parameters)
    if not path_allowed(cwd, target.get("allowedRoots", [])):
        raise BrokerError("working directory falls outside target allowedRoots")
    timeout = min(max(int(action.get("timeoutSeconds", policy.get("defaultTimeoutSeconds", 60))), 1), int(policy.get("maxTimeoutSeconds", 3600)))
    isolation = str(action.get("isolation", "none"))
    if isolation not in {"none", "dedicated-runner", "ephemeral"}:
        raise BrokerError("action has an unknown isolation mode")
    auto_eligible = action["tier"] == "read" or (
        isolation == "dedicated-runner" and target["backend"] == "ssh" and target.get("dedicatedRunner") is True
    ) or (isolation == "ephemeral" and target.get("ephemeralRunner") is True)
    return {
        "id": str(item.get("id") or "step"),
        "target": target_name,
        "action": action_name,
        "tier": action["tier"],
        "argv": argv,
        "cwd": normalized_remote_path(cwd),
        "timeoutSeconds": timeout,
        "exclusiveTarget": bool(action.get("exclusiveTarget", action["tier"] != "read")),
        "isolation": isolation,
        "autoEligible": auto_eligible,
        "parameters": parameters,
        "effect": action.get("effect", effect_for_tier(action["tier"])),
        "idempotent": action.get("idempotent", action["tier"] == "read"),
        "reversible": action.get("reversible", False),
        "rollbackAction": action.get("rollbackAction"),
        "verificationAction": action.get("verificationAction"),
        "dependsOn": item.get("dependsOn", []),
    }


def validate_dag(steps: list[dict[str, Any]]) -> None:
    identifiers = [step["id"] for step in steps]
    if len(identifiers) != len(set(identifiers)) or any(not STEP_RE.fullmatch(item) for item in identifiers):
        raise BrokerError("workflow step IDs must be safe and unique")
    known = set(identifiers)
    graph = {}
    for step in steps:
        dependencies = step.get("dependsOn", [])
        if not isinstance(dependencies, list) or any(item not in known or item == step["id"] for item in dependencies):
            raise BrokerError("workflow contains an invalid dependency")
        graph[step["id"]] = set(dependencies)
    ready, seen = [name for name, dependencies in graph.items() if not dependencies], set()
    while ready:
        current = ready.pop()
        if current in seen:
            continue
        seen.add(current)
        for name, dependencies in graph.items():
            if name not in seen and dependencies <= seen:
                ready.append(name)
    if seen != known:
        raise BrokerError("workflow dependency graph contains a cycle")


def compile_request(
    policy: dict[str, Any], request: Any, extra_grants: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("schemaVersion") not in SUPPORTED_REQUEST_VERSIONS:
        raise BrokerError("request must use schemaVersion 1 or 2")
    job_id = str(request.get("jobId", ""))
    if not JOB_RE.fullmatch(job_id):
        raise BrokerError("invalid job ID")
    created = datetime.fromisoformat(str(request.get("createdAt", "")).replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise BrokerError("request timestamp must include a timezone")
    if abs((utc_now() - created.astimezone(timezone.utc)).total_seconds()) > 900:
        raise BrokerError("request timestamp is stale or in the future")
    kind = request.get("kind")
    steps: list[dict[str, Any]]
    if kind == "action":
        steps = [compile_action(policy, {**request, "id": "step"})]
    elif kind == "workflow":
        supplied = request.get("steps")
        if not isinstance(supplied, list) or not 1 <= len(supplied) <= int(policy.get("maxWorkflowSteps", 32)):
            raise BrokerError("workflow has an invalid number of steps")
        steps = [compile_action(policy, item) for item in supplied if isinstance(item, dict)]
        if len(steps) != len(supplied):
            raise BrokerError("workflow steps must be objects")
        validate_dag(steps)
    elif kind == "shell":
        target_name = str(request.get("target", ""))
        target = policy["targets"].get(target_name)
        command = str(request.get("command", ""))
        cwd = str(request.get("cwd", target.get("defaultCwd", "/") if target else "/"))
        if not target or not target.get("enabled", True) or not target.get("allowRaw", False):
            raise BrokerError("raw shell is disabled for this target")
        if target.get("backend") == "ssh":
            raise BrokerError("raw shell is unavailable on forced-command SSH targets")
        if not command or len(command) > 16_384 or "\x00" in command or SECRET_COMMAND_RE.search(command):
            raise BrokerError("raw shell command is empty, too large, or contains a literal credential")
        if not path_allowed(cwd, target.get("allowedRoots", [])):
            raise BrokerError("raw shell working directory falls outside target allowedRoots")
        timeout = min(max(int(request.get("timeoutSeconds", 300)), 1), int(policy.get("maxTimeoutSeconds", 3600)))
        steps = [{
            "id": "step", "target": target_name, "action": "raw-shell", "tier": "break-glass",
            "argv": [str(target.get("shell", "/bin/bash")), "-lc", command], "cwd": normalized_remote_path(cwd),
            "timeoutSeconds": timeout, "exclusiveTarget": True, "isolation": "operator-break-glass", "autoEligible": False,
            "parameters": {}, "effect": "change", "idempotent": False, "reversible": False,
            "rollbackAction": None, "verificationAction": None, "dependsOn": [],
        }]
    elif kind == "download":
        url, filename = str(request.get("url", "")), str(request.get("filename", ""))
        parts, _addresses = public_url(url)
        if not FILENAME_RE.fullmatch(filename) or filename in {".", ".."}:
            raise BrokerError("download filename is unsafe")
        expected_sha256 = request.get("expectedSha256")
        if expected_sha256 is not None and (not isinstance(expected_sha256, str) or not SHA256_RE.fullmatch(expected_sha256)):
            raise BrokerError("download expectedSha256 must be 64 lowercase hexadecimal characters")
        allowed_domains = policy.get("download", {}).get("allowedDomains", [])
        host = normalized_hostname(parts.hostname or "")
        approved_domain = domain_allowed(host, allowed_domains)
        steps = [{
            "id": "step", "target": "broker", "action": "download.stage",
            "tier": "staging" if approved_domain else "change", "url": url, "filename": filename,
            "originalHost": host, "expectedSha256": expected_sha256,
            "timeoutSeconds": min(max(int(request.get("timeoutSeconds", 120)), 1), 600),
            "exclusiveTarget": False, "isolation": "broker-quarantine", "autoEligible": approved_domain,
            "parameters": {}, "effect": "stage" if approved_domain else "change", "idempotent": False,
            "reversible": True, "rollbackAction": None, "verificationAction": None, "dependsOn": [],
        }]
    elif kind == "transfer":
        source_job_id = str(request.get("sourceJobId", ""))
        target_name, filename = str(request.get("target", "")), str(request.get("filename", ""))
        target = policy["targets"].get(target_name)
        if not JOB_RE.fullmatch(source_job_id):
            raise BrokerError("transfer sourceJobId is unsafe")
        if not target or not target.get("enabled", True) or target.get("backend") != "ssh" or target.get("dedicatedRunner") is not True:
            raise BrokerError("artifact transfers require an enabled dedicated SSH runner")
        if not FILENAME_RE.fullmatch(filename):
            raise BrokerError("transfer filename is unsafe")
        steps = [{
            "id": "step", "target": target_name, "action": "artifact.transfer", "tier": "staging",
            "sourceJobId": source_job_id, "filename": filename,
            "timeoutSeconds": min(max(int(request.get("timeoutSeconds", 300)), 1), 1800),
            "exclusiveTarget": False, "isolation": "dedicated-runner", "autoEligible": True,
            "parameters": {}, "effect": "stage", "idempotent": False, "reversible": True,
            "rollbackAction": None, "verificationAction": None, "dependsOn": [],
        }]
    else:
        raise BrokerError("unsupported request kind")
    evaluated_at = iso()
    decisions = []
    for step in steps:
        decision = resolve_authority(policy, step, extra_grants)
        if decision["outcome"] == "rejected":
            raise BrokerError(f"action {step['action']} is disabled by authority policy")
        step["authorityDecision"] = decision
        decisions.append({"stepId": step["id"], **decision})
    risk = highest_risk([step["tier"] for step in steps])
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "jobId": job_id,
        "kind": kind,
        "requestHash": digest(request),
        "policySha256": digest(policy),
        "compiledAt": iso(),
        "expiresAt": iso(utc_now() + timedelta(minutes=int(policy.get("planTtlMinutes", 30)))),
        "riskTier": risk,
        "approvalRequired": any(decision["outcome"] != "execute" for decision in decisions),
        "authorityReceipt": {
            "evaluatedAt": evaluated_at,
            "decisions": decisions,
            "boundary": "Authority was evaluated by the external broker and is rechecked before execution.",
        },
        "reason": safe_text(request.get("reason", ""), 1000),
        "steps": steps,
        "boundary": "This immutable plan grants only the listed operations. Tool output cannot widen it.",
    }
    plan["planHash"] = digest(plan)
    return plan


class Broker:
    def __init__(self, policy_path: Path, state: Path):
        self.policy_path, self.state = policy_path, state
        self.policy = validate_policy(read_regular_json(policy_path, MAX_POLICY_BYTES))
        self.policy_sha256 = digest(self.policy)
        self.directories = {name: state / name for name in (
            "requests", "request-archive", "plans", "approvals", "results", "events", "cancel", "runtime"
        )}
        for directory in self.directories.values():
            directory.mkdir(parents=True, exist_ok=True)
        self.authority_root = state / "authority"
        self.lease_directory = self.authority_root / "leases"
        self.authority_root.mkdir(parents=True, exist_ok=True)
        self.lease_directory.mkdir(parents=True, exist_ok=True)
        self.event_locks: dict[str, threading.Lock] = {}
        self.target_locks: dict[str, threading.Lock] = {}
        self.job_policy_bindings: dict[str, str] = {}
        self.policy_binding_lock = threading.Lock()
        self.inflight: set[str] = set()
        self.inflight_lock = threading.Lock()
        self.authority_lock = threading.Lock()
        self.grant_inflight: dict[str, int] = {}
        self.identity_cache: dict[str, tuple[float, str]] = {}
        self.inventory_refresh_seconds = INVENTORY_REFRESH_SECONDS
        self.inventory_refreshed_at = 0.0
        self.refresh_inventory()

    def authority_transaction(self):
        return locked_file(self.authority_root / ".mutation.lock")

    def path(self, kind: str, job_id: str, suffix: str = ".json") -> Path:
        if not JOB_RE.fullmatch(job_id):
            raise BrokerError("unsafe job ID")
        return self.directories[kind] / f"{job_id}{suffix}"

    def bound_policy_sha256(self, job_id: str, supplied: Any = None) -> str:
        if supplied is not None and (not isinstance(supplied, str) or not re.fullmatch(r"[a-f0-9]{64}", supplied)):
            raise BrokerError("job projection supplied an invalid policy binding")
        with self.policy_binding_lock:
            binding = self.job_policy_bindings.get(job_id)
        if binding is None:
            plan_path = self.path("plans", job_id)
            if plan_path.exists():
                try:
                    plan = read_regular_json(plan_path, MAX_REQUEST_BYTES)
                    plan_hash = plan.get("planHash")
                    plan_policy = plan.get("policySha256")
                    if (
                        plan.get("jobId") == job_id
                        and isinstance(plan_hash, str)
                        and plan_hash == digest({key: value for key, value in plan.items() if key != "planHash"})
                        and isinstance(plan_policy, str)
                        and re.fullmatch(r"[a-f0-9]{64}", plan_policy)
                    ):
                        binding = plan_policy
                except Exception:
                    binding = None
            if binding is None:
                result_path = self.path("results", job_id)
                if result_path.exists():
                    try:
                        prior = read_regular_json(result_path, MAX_RESULT_BYTES).get("policySha256")
                        if isinstance(prior, str) and re.fullmatch(r"[a-f0-9]{64}", prior):
                            binding = prior
                    except Exception:
                        binding = None
            if binding is None:
                binding = self.policy_sha256
            with self.policy_binding_lock:
                previous = self.job_policy_bindings.setdefault(job_id, binding)
                if previous != binding:
                    raise BrokerError("job policy binding changed during reconciliation")
                binding = previous
        if supplied is not None and supplied != binding:
            raise BrokerError("job projection policy binding differs from the immutable job")
        return binding

    def paused(self) -> bool:
        path = self.authority_root / "pause.json"
        if not path.exists():
            return False
        try:
            return read_regular_json(path, MAX_REQUEST_BYTES).get("paused") is True
        except Exception:
            return True

    def active_leases(self) -> list[dict[str, Any]]:
        leases = []
        for path in sorted(self.lease_directory.glob("*.json")):
            try:
                raw = read_regular_json(path, MAX_REQUEST_BYTES)
                if path.stem != raw.get("id") or raw.get("revokedAt"):
                    continue
                grant = validate_grant(raw, source="lease")
                if grant_is_active(grant, utc_now()):
                    leases.append(grant)
            except Exception:
                continue
        return leases

    def refresh_inventory(self) -> None:
        inventory = policy_inventory(self.policy)
        inventory["authority"]["paused"] = self.paused()
        inventory["authority"]["activeLeaseIds"] = [grant["id"] for grant in self.active_leases()]
        atomic_json(self.state / "inventory.json", inventory)
        self.inventory_refreshed_at = time.monotonic()

    def refresh_inventory_if_due(self) -> bool:
        if time.monotonic() - self.inventory_refreshed_at < self.inventory_refresh_seconds:
            return False
        # The serve loop is the only in-process caller. The cross-process authority
        # lock prevents a periodic projection from overwriting a concurrent grant,
        # revoke, pause, or resume projection with an older lease snapshot.
        with self.authority_transaction():
            self.refresh_inventory()
        return True

    def authority_audit(self, kind: str, **details: Any) -> None:
        record = {"schemaVersion": SCHEMA_VERSION, "time": iso(), "kind": kind, **details}
        payload = canonical(record) + b"\n"
        path = self.authority_root / "audit.jsonl"
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            write_all(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def recheck_authority(self, plan: dict[str, Any]) -> None:
        if plan.get("approvalRequired"):
            return
        leases = self.active_leases()
        for step in plan["steps"]:
            previous = step.get("authorityDecision", {})
            current = resolve_authority(self.policy, step, leases)
            if current.get("outcome") != "execute":
                raise BrokerError(f"authority no longer permits step {step['id']}")
            if previous.get("grantId") != current.get("grantId") or previous.get("constraintsHash") != current.get("constraintsHash"):
                raise BrokerError(f"authority changed after plan compilation for step {step['id']}")

    def execution_allowed(self, step: dict[str, Any]) -> bool:
        if self.paused():
            return False
        previous = step.get("authorityDecision", {})
        if not previous.get("grantId"):
            return True
        current = resolve_authority(self.policy, step, self.active_leases())
        return (
            current.get("outcome") == "execute"
            and current.get("grantId") == previous.get("grantId")
            and current.get("constraintsHash") == previous.get("constraintsHash")
        )

    def reserve_authority(self, plan: dict[str, Any]) -> dict[str, int]:
        if plan.get("approvalRequired"):
            return {}
        counts: dict[str, int] = {}
        for step in plan["steps"]:
            grant_id = step.get("authorityDecision", {}).get("grantId")
            if grant_id:
                counts[grant_id] = counts.get(grant_id, 0) + 1
        if not counts:
            return {}
        with self.authority_lock:
            with self.authority_transaction():
                leases = self.active_leases()
                grants = {grant["id"]: grant for grant in self.policy.get("authority", {}).get("grants", []) + leases}
                usage_path = self.authority_root / "usage.json"
                usage = read_regular_json(usage_path, MAX_REQUEST_BYTES) if usage_path.exists() else {"schemaVersion": SCHEMA_VERSION, "grants": {}}
                usage.setdefault("grants", {})
                now = utc_now()
                for grant_id, requested in counts.items():
                    grant = grants.get(grant_id)
                    if grant is None or not grant_is_active(grant, now):
                        raise BrokerError(f"authority grant {grant_id} is absent or expired")
                    concurrent_limit = int(grant.get("maxConcurrent", 32))
                    if self.grant_inflight.get(grant_id, 0) + requested > concurrent_limit:
                        raise BrokerError(f"authority grant {grant_id} concurrency budget is exhausted")
                    if "maxExecutions" in grant:
                        window = int(grant["windowSeconds"])
                        bucket = int(now.timestamp()) // window * window
                        entry = usage["grants"].get(grant_id, {})
                        if entry.get("bucketStart") != bucket:
                            entry = {"bucketStart": bucket, "executions": 0, "failures": 0}
                        if int(entry.get("executions", 0)) + requested > int(grant["maxExecutions"]):
                            raise BrokerError(f"authority grant {grant_id} execution budget is exhausted")
                        if int(entry.get("failures", 0)) >= int(grant.get("maxFailures", 10_000)):
                            raise BrokerError(f"authority grant {grant_id} failure circuit is open")
                        entry["executions"] = int(entry.get("executions", 0)) + requested
                        usage["grants"][grant_id] = entry
                for grant_id, requested in counts.items():
                    self.grant_inflight[grant_id] = self.grant_inflight.get(grant_id, 0) + requested
                atomic_json(usage_path, usage, 0o600)
                self.authority_audit("authority-reserved", jobId=plan["jobId"], grants=counts)
        return counts

    def release_authority(self, job_id: str, reservations: dict[str, int], failed: bool) -> None:
        if not reservations:
            return
        with self.authority_lock:
            with self.authority_transaction():
                for grant_id, count in reservations.items():
                    self.grant_inflight[grant_id] = max(0, self.grant_inflight.get(grant_id, 0) - count)
                if failed:
                    usage_path = self.authority_root / "usage.json"
                    usage = read_regular_json(usage_path, MAX_REQUEST_BYTES) if usage_path.exists() else {"schemaVersion": SCHEMA_VERSION, "grants": {}}
                    for grant_id in reservations:
                        entry = usage.setdefault("grants", {}).setdefault(grant_id, {})
                        entry["failures"] = int(entry.get("failures", 0)) + 1
                    atomic_json(usage_path, usage, 0o600)
                self.authority_audit("authority-released", jobId=job_id, grants=reservations, failed=failed)

    def append_event(self, job_id: str, kind: str, **details: Any) -> None:
        details["policySha256"] = self.bound_policy_sha256(job_id, details.get("policySha256"))
        record = {"schemaVersion": SCHEMA_VERSION, "time": iso(), "jobId": job_id, "kind": kind, **details}
        payload = canonical(record) + b"\n"
        lock = self.event_locks.setdefault(job_id, threading.Lock())
        with lock:
            descriptor = os.open(self.path("events", job_id, ".jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o640)
            try:
                os.write(descriptor, payload)
            finally:
                os.close(descriptor)

    def result(self, job_id: str, status: str, **values: Any) -> None:
        current: dict[str, Any] = {}
        path = self.path("results", job_id)
        if path.exists():
            try:
                current = read_regular_json(path, MAX_RESULT_BYTES)
            except Exception:
                current = {}
        supplied = values.get("policySha256")
        values["policySha256"] = self.bound_policy_sha256(job_id, supplied)
        atomic_json(path, {**current, "schemaVersion": SCHEMA_VERSION, "jobId": job_id, "status": status, "updatedAt": iso(), **values})

    def ingest(self, request_path: Path) -> str:
        job_id = request_path.stem
        archive_path = self.path("request-archive", job_id) if JOB_RE.fullmatch(job_id) else None
        try:
            if archive_path is None or archive_path.exists():
                raise BrokerError("job ID has already been used or is unsafe")
            os.replace(request_path, archive_path)
            request = read_regular_json(archive_path, MAX_REQUEST_BYTES)
            if request.get("jobId") != job_id:
                raise BrokerError("request filename and job ID differ")
            plan = compile_request(self.policy, request, self.active_leases())
            plan_path = self.path("plans", job_id)
            if plan_path.exists() or self.path("results", job_id).exists():
                raise BrokerError("job ID has already been used")
            atomic_json(plan_path, plan)
            status = "awaiting-approval" if plan["approvalRequired"] else ("paused" if self.paused() else "queued")
            self.result(
                job_id, status, planHash=plan["planHash"], policySha256=plan["policySha256"], riskTier=plan["riskTier"],
                approvalRequired=plan["approvalRequired"], authorityReceipt=plan["authorityReceipt"],
            )
            self.append_event(
                job_id, "plan-compiled", status=status, planHash=plan["planHash"], policySha256=plan["policySha256"],
                riskTier=plan["riskTier"], authorityReceipt=plan["authorityReceipt"],
            )
            return job_id
        except Exception as error:
            request_path.unlink(missing_ok=True)
            if JOB_RE.fullmatch(job_id):
                self.result(job_id, "rejected", error=safe_text(error, 1000))
                self.append_event(job_id, "request-rejected", error=safe_text(error, 1000))
            raise

    def approved(self, plan: dict[str, Any]) -> bool:
        if not plan["approvalRequired"]:
            return True
        path = self.path("approvals", plan["jobId"])
        if not path.exists():
            return False
        approval = read_regular_json(path, MAX_REQUEST_BYTES)
        if approval.get("jobId") != plan["jobId"] or approval.get("planHash") != plan["planHash"]:
            raise BrokerError("approval does not match the immutable plan")
        expires = datetime.fromisoformat(str(approval.get("expiresAt", "")).replace("Z", "+00:00"))
        if utc_now() >= expires.astimezone(timezone.utc):
            raise BrokerError("approval expired")
        return True

    def cancelled(self, job_id: str) -> bool:
        return self.path("cancel", job_id).exists()

    def ensure_identity(self, target_name: str) -> None:
        target = self.policy["targets"][target_name]
        cached = self.identity_cache.get(target_name)
        if cached and time.monotonic() - cached[0] < int(self.policy.get("identityCacheSeconds", 30)):
            if cached[1] == target["expectedHostname"]:
                return
        if target["backend"] == "local":
            observed = socket.gethostname()
        else:
            command = self.ssh_prefix(target) + ["hostname"]
            completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15, check=False)
            if completed.returncode != 0:
                raise BrokerError(f"target identity probe failed: {safe_text(completed.stderr, 500)}")
            observed = completed.stdout.strip()
        if observed != target["expectedHostname"]:
            raise BrokerError(f"target identity mismatch: expected {target['expectedHostname']}, observed {safe_text(observed, 200)}")
        self.identity_cache[target_name] = (time.monotonic(), observed)

    def ssh_prefix(self, target: dict[str, Any]) -> list[str]:
        control = self.directories["runtime"] / "ssh-%C"
        return [
            str(self.policy.get("sshBinary", "/usr/bin/ssh")), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ConnectTimeout=10", "-o", "ControlMaster=auto", "-o", "ControlPersist=60s",
            "-o", f"ControlPath={control}", str(target["sshHost"]),
        ]

    def command_for(self, step: dict[str, Any]) -> list[str]:
        target = self.policy["targets"][step["target"]]
        if target["backend"] == "local":
            return step["argv"]
        if step.get("action") == "raw-shell":
            raise BrokerError("raw shell cannot cross the forced-command SSH boundary")
        remote = f"cd -- {shlex.quote(step['cwd'])} && exec {shlex.join(step['argv'])}"
        return self.ssh_prefix(target) + [remote]

    def run_step(self, job_id: str, step: dict[str, Any], abort: threading.Event | None = None) -> dict[str, Any]:
        if self.cancelled(job_id):
            raise Cancelled("job was cancelled")
        if abort and abort.is_set():
            raise BrokerError("workflow sibling failed before this step started")
        if not self.execution_allowed(step):
            raise Cancelled("job stopped because emergency pause or authority revocation became active")
        if step["action"] == "download.stage":
            return self.run_download(job_id, step, abort)
        if step["action"] == "artifact.transfer":
            return self.run_transfer(job_id, step, abort)
        self.ensure_identity(step["target"])
        target = self.policy["targets"][step["target"]]
        if target["backend"] == "local":
            real_cwd = os.path.realpath(step["cwd"])
            if not path_allowed(real_cwd, target.get("allowedRoots", [])):
                raise BrokerError("local working directory escaped allowedRoots")
            cwd = real_cwd
        else:
            cwd = None
        command = self.command_for(step)
        lock = self.target_locks.setdefault(step["target"], threading.Lock()) if step["exclusiveTarget"] else None
        if lock:
            lock.acquire()
        process: subprocess.Popen[bytes] | None = None
        started = time.monotonic()
        max_output = min(
            int(self.policy.get("maxOutputBytes", 256 * 1024)),
            int(step.get("authorityDecision", {}).get("resourceLimits", {}).get("maxOutputBytes", 16 * 1024 * 1024)),
        )
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = {"stdout": False, "stderr": False}
        reader_threads: list[threading.Thread] = []

        def capture(stream_name: str, handle) -> None:
            while True:
                try:
                    chunk = handle.read(8192)
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                remaining = max_output - len(captured[stream_name])
                accepted = chunk[:max(remaining, 0)]
                if accepted:
                    captured[stream_name].extend(accepted)
                    clean = safe_text(accepted.decode("utf-8", "replace"), 8192)
                    self.append_event(job_id, "step-output", stepId=step["id"], stream=stream_name, text=clean, untrusted=True, riskSignals=output_signals(clean))
                if len(chunk) > len(accepted) and not truncated[stream_name]:
                    truncated[stream_name] = True
                    self.append_event(job_id, "step-output-truncated", stepId=step["id"], stream=stream_name, maxBytes=max_output)
        try:
            self.append_event(job_id, "step-started", stepId=step["id"], target=step["target"], action=step["action"])
            process = subprocess.Popen(
                command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
                start_new_session=True,
            )
            assert process.stdout is not None and process.stderr is not None
            for stream_name, handle in (("stdout", process.stdout), ("stderr", process.stderr)):
                thread = threading.Thread(target=capture, args=(stream_name, handle), name=f"pixel-ops-{job_id}-{stream_name}", daemon=True)
                thread.start(); reader_threads.append(thread)
            while process.poll() is None:
                authority_stopped = step["tier"] in {"read", "staging"} and not self.execution_allowed(step)
                if self.cancelled(job_id) or (abort and abort.is_set()) or authority_stopped:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    if self.cancelled(job_id):
                        raise Cancelled("job was cancelled")
                    if authority_stopped:
                        raise Cancelled("job stopped because emergency pause or authority revocation became active")
                    raise BrokerError("workflow sibling failed; step was terminated")
                if time.monotonic() - started > step["timeoutSeconds"]:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                    raise BrokerError("step exceeded its hard timeout")
                time.sleep(0.2)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for thread in reader_threads:
                thread.join(timeout=5)
            streams = {name: safe_text(value.decode("utf-8", "replace"), max_output) for name, value in captured.items()}
            elapsed = round(time.monotonic() - started, 3)
            result = {
                "stepId": step["id"], "target": step["target"], "action": step["action"],
                "exitCode": process.returncode, "durationSeconds": elapsed,
                "stdout": streams["stdout"], "stderr": streams["stderr"], "untrustedOutput": True,
                "outputTruncated": truncated,
                "riskSignals": output_signals(streams["stdout"] + "\n" + streams["stderr"]),
            }
            self.append_event(job_id, "step-finished", stepId=step["id"], exitCode=process.returncode, durationSeconds=elapsed)
            if process.returncode != 0:
                raise BrokerError(f"step {step['id']} exited with {process.returncode}")
            return result
        finally:
            if process:
                for handle in (process.stdout, process.stderr):
                    if handle:
                        try: handle.close()
                        except OSError: pass
            if lock:
                lock.release()

    def run_download(self, job_id: str, step: dict[str, Any], abort: threading.Event | None = None) -> dict[str, Any]:
        configuration = self.policy.get("download", {})
        limit = min(
            int(configuration.get("maxBytes", 512 * 1024 * 1024)),
            int(step.get("authorityDecision", {}).get("resourceLimits", {}).get("maxArtifactBytes", 2 * 1024 * 1024 * 1024)),
            2 * 1024 * 1024 * 1024,
        )
        url, redirects, started = step["url"], [], time.monotonic()
        original_host = str(step.get("originalHost") or normalized_hostname(urllib.parse.urlsplit(url).hostname or ""))
        allowed_domains = configuration.get("allowedDomains", [])
        response: http.client.HTTPResponse | None = None
        connection: http.client.HTTPConnection | None = None
        try:
            for _ in range(min(int(configuration.get("maxRedirects", 5)), 10) + 1):
                if self.cancelled(job_id):
                    raise Cancelled("job was cancelled")
                if abort and abort.is_set():
                    raise BrokerError("workflow sibling failed; download was terminated")
                if not self.execution_allowed(step):
                    raise Cancelled("download stopped because emergency pause or authority revocation became active")
                remaining = step["timeoutSeconds"] - (time.monotonic() - started)
                if remaining <= 0:
                    raise BrokerError("download exceeded its hard timeout")
                parts, addresses = public_url(url)
                current_host = normalized_hostname(parts.hostname or "")
                if current_host != original_host and not domain_allowed(current_host, allowed_domains):
                    raise BrokerError("download redirect escaped the reviewed source domain")
                port = parts.port or (443 if parts.scheme == "https" else 80)
                path = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
                last_error: Exception | None = None
                for address in addresses:
                    try:
                        connection_timeout = max(min(20.0, remaining), 0.1)
                        connection = PinnedHTTPSConnection(address, port, current_host, connection_timeout)
                        host_header = f"[{current_host}]" if ":" in current_host else current_host
                        if parts.port:
                            host_header += f":{parts.port}"
                        connection.request("GET", path, headers={"Host": host_header, "User-Agent": "Pixel-Operations-Broker/1", "Accept": "*/*", "Accept-Encoding": "identity"})
                        response = connection.getresponse()
                        break
                    except (OSError, ssl.SSLError, http.client.HTTPException) as error:
                        last_error = error
                        if connection:
                            connection.close()
                        connection, response = None, None
                if response is None:
                    raise BrokerError("public download connection failed") from last_error
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise BrokerError("redirect has no Location header")
                    redirects.append(safe_url_for_log(url))
                    url = urllib.parse.urljoin(url, location)
                    response.read(4096)
                    connection.close()
                    response, connection = None, None
                    continue
                if not 200 <= response.status < 300:
                    raise BrokerError(f"download returned HTTP {response.status}")
                length = response.getheader("Content-Length")
                if length:
                    try:
                        declared_length = int(length, 10)
                    except ValueError as error:
                        raise BrokerError("download returned an invalid Content-Length") from error
                    if declared_length < 0:
                        raise BrokerError("download returned an invalid Content-Length")
                    if declared_length > limit:
                        raise BrokerError("download exceeds the configured size limit")
                break
            else:
                raise BrokerError("download exceeded its redirect limit")
            if response is None:
                raise BrokerError("download produced no response")
            staging_root = Path(str(configuration.get("stagingRoot", self.state / "artifacts")))
            if not staging_root.is_absolute() or staging_root == Path("/"):
                raise BrokerError("download stagingRoot must be an absolute non-root path")
            job_dir = staging_root / job_id
            job_dir.mkdir(mode=0o750, parents=True, exist_ok=False)
            destination = job_dir / step["filename"]
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
            total, checksum = 0, hashlib.sha256()
            try:
                while True:
                    if self.cancelled(job_id):
                        raise Cancelled("job was cancelled")
                    if abort and abort.is_set():
                        raise BrokerError("workflow sibling failed; download was terminated")
                    if not self.execution_allowed(step):
                        raise Cancelled("download stopped because emergency pause or authority revocation became active")
                    if time.monotonic() - started > step["timeoutSeconds"]:
                        raise BrokerError("download exceeded its hard timeout")
                    if connection and connection.sock:
                        remaining = step["timeoutSeconds"] - (time.monotonic() - started)
                        connection.sock.settimeout(max(min(20.0, remaining), 0.1))
                    chunk = response.read(min(64 * 1024, limit + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise BrokerError("download exceeds the configured size limit")
                    checksum.update(chunk)
                    write_all(descriptor, chunk)
                if step.get("expectedSha256") and checksum.hexdigest() != step["expectedSha256"]:
                    raise BrokerError("download content does not match expectedSha256")
            except Exception:
                os.close(descriptor)
                destination.unlink(missing_ok=True)
                raise
            else:
                os.fsync(descriptor); os.close(descriptor)
            result = {
                "stepId": step["id"], "target": "broker", "action": step["action"], "exitCode": 0,
                "durationSeconds": round(time.monotonic() - started, 3), "artifact": {
                    "path": str(destination), "filename": step["filename"], "bytes": total,
                    "sha256": checksum.hexdigest(), "source": safe_url_for_log(url), "redirects": redirects,
                    "contentType": safe_text(response.getheader("Content-Type") or "", 256),
                    "expectedSha256Matched": bool(step.get("expectedSha256")), "executable": False,
                }, "untrustedOutput": True, "riskSignals": [],
            }
            self.append_event(job_id, "artifact-staged", stepId=step["id"], bytes=total, sha256=checksum.hexdigest(), source=safe_url_for_log(url))
            return result
        finally:
            if connection:
                connection.close()

    def run_transfer(self, job_id: str, step: dict[str, Any], abort: threading.Event | None = None) -> dict[str, Any]:
        if self.cancelled(job_id):
            raise Cancelled("job was cancelled")
        if abort and abort.is_set():
            raise BrokerError("workflow sibling failed before artifact transfer")
        if not self.execution_allowed(step):
            raise Cancelled("artifact transfer stopped because emergency pause or authority revocation became active")
        self.ensure_identity(step["target"])
        source_result_path = self.path("results", step["sourceJobId"])
        source_result = read_regular_json(source_result_path, MAX_RESULT_BYTES)
        if source_result.get("status") != "succeeded":
            raise BrokerError("artifact source job has not succeeded")
        artifacts = [item.get("artifact") for item in source_result.get("steps", []) if isinstance(item, dict) and isinstance(item.get("artifact"), dict)]
        artifact = next((item for item in artifacts if item.get("filename") == step["filename"]), None)
        if artifact is None:
            raise BrokerError("artifact is absent from the source job")
        source = Path(str(artifact.get("path", "")))
        artifact_limit = int(step.get("authorityDecision", {}).get("resourceLimits", {}).get("maxArtifactBytes", 2 * 1024 * 1024 * 1024))
        staging_root = Path(str(self.policy.get("download", {}).get("stagingRoot", self.state / "artifacts"))).resolve()
        resolved = source.resolve()
        if staging_root not in resolved.parents or step["sourceJobId"] not in resolved.parts:
            raise BrokerError("artifact source escaped broker quarantine")
        target = self.policy["targets"][step["target"]]
        ssh_command = self.ssh_prefix(target)
        started = time.monotonic()
        self.append_event(job_id, "step-started", stepId=step["id"], target=step["target"], action=step["action"])
        source_descriptor, info, observed_hash = open_hashed_artifact(source, artifact_limit)
        if observed_hash != artifact.get("sha256"):
            os.close(source_descriptor)
            raise BrokerError("artifact changed after staging")
        remote = shlex.join(["/usr/local/libexec/pixel-ops-receive-artifact", job_id, step["filename"], observed_hash])
        command = ssh_command + [remote]
        process: subprocess.Popen[bytes] | None = None
        captured = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = {"stdout": False, "stderr": False}
        reader_threads: list[threading.Thread] = []

        def capture(stream_name: str, handle) -> None:
            maximum = 64 * 1024
            while True:
                try:
                    chunk = handle.read(8192)
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                remaining = maximum - len(captured[stream_name])
                if remaining > 0:
                    captured[stream_name].extend(chunk[:remaining])
                if len(chunk) > max(remaining, 0):
                    truncated[stream_name] = True

        with os.fdopen(os.dup(source_descriptor), "rb") as payload:
            try:
                process = subprocess.Popen(command, stdin=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                assert process.stdout is not None and process.stderr is not None
                for stream_name, handle in (("stdout", process.stdout), ("stderr", process.stderr)):
                    thread = threading.Thread(target=capture, args=(stream_name, handle), name=f"pixel-transfer-{job_id}-{stream_name}", daemon=True)
                    thread.start(); reader_threads.append(thread)
                while process.poll() is None:
                    cancelled = self.cancelled(job_id)
                    aborted = bool(abort and abort.is_set())
                    timed_out = time.monotonic() - started > step["timeoutSeconds"]
                    authority_stopped = not self.execution_allowed(step)
                    if cancelled or aborted or timed_out or authority_stopped:
                        os.killpg(process.pid, signal.SIGTERM)
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait(timeout=5)
                        if cancelled:
                            raise Cancelled("job was cancelled")
                        if aborted:
                            raise BrokerError("workflow sibling failed; artifact transfer was terminated")
                        if authority_stopped:
                            raise Cancelled("artifact transfer stopped because emergency pause or authority revocation became active")
                        raise BrokerError("artifact transfer exceeded its hard timeout")
                    time.sleep(0.2)
                for thread in reader_threads:
                    thread.join(timeout=5)
            finally:
                if process:
                    for handle in (process.stdout, process.stderr):
                        if handle:
                            try: handle.close()
                            except OSError: pass
                os.close(source_descriptor)
        stdout = safe_text(captured["stdout"].decode("utf-8", "replace"), 64 * 1024)
        stderr = safe_text(captured["stderr"].decode("utf-8", "replace"), 64 * 1024)
        if process is None or process.returncode != 0:
            code = process.returncode if process else "unknown"
            raise BrokerError(f"artifact receiver failed with {code}: {stderr}")
        try:
            remote_result = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise BrokerError("artifact receiver returned invalid evidence") from error
        if remote_result.get("sha256") != observed_hash or remote_result.get("bytes") != info.st_size or remote_result.get("executable") is not False:
            raise BrokerError("artifact receiver evidence does not match the staged source")
        elapsed = round(time.monotonic() - started, 3)
        self.append_event(job_id, "artifact-transferred", stepId=step["id"], target=step["target"], bytes=info.st_size, sha256=observed_hash)
        self.append_event(job_id, "step-finished", stepId=step["id"], exitCode=0, durationSeconds=elapsed)
        return {
            "stepId": step["id"], "target": step["target"], "action": step["action"], "exitCode": 0,
            "durationSeconds": elapsed, "artifact": remote_result, "untrustedOutput": True,
            "outputTruncated": truncated, "riskSignals": output_signals(stdout + "\n" + stderr),
        }

    def execute_plan(self, plan: dict[str, Any]) -> None:
        job_id = plan["jobId"]
        reservations: dict[str, int] = {}
        authority_failed = False
        try:
            if self.paused():
                self.result(job_id, "paused", planHash=plan["planHash"], policySha256=plan["policySha256"], riskTier=plan["riskTier"])
                self.append_event(job_id, "job-paused", policySha256=plan["policySha256"], reason="emergency pause is active")
                return
            expected_hash = digest({key: value for key, value in plan.items() if key != "planHash"})
            if plan.get("planHash") != expected_hash:
                raise BrokerError("stored plan failed its integrity hash")
            expires = datetime.fromisoformat(plan["expiresAt"].replace("Z", "+00:00"))
            if utc_now() >= expires.astimezone(timezone.utc):
                raise BrokerError("compiled plan expired before execution")
            if not self.approved(plan):
                return
            self.recheck_authority(plan)
            reservations = self.reserve_authority(plan)
            self.result(
                job_id, "running", startedAt=iso(), planHash=plan["planHash"], policySha256=plan["policySha256"], riskTier=plan["riskTier"],
                authorityReceipt=plan.get("authorityReceipt"), authorityGrantIds=sorted(reservations),
            )
            self.append_event(job_id, "job-started", planHash=plan["planHash"], policySha256=plan["policySha256"])
            results: dict[str, dict[str, Any]] = {}
            pending = {step["id"]: step for step in plan["steps"]}
            running: dict[concurrent.futures.Future[dict[str, Any]], str] = {}
            abort = threading.Event()
            workers = min(max(int(self.policy.get("workflowWorkers", 4)), 1), 16)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                while pending or running:
                    if self.cancelled(job_id):
                        raise Cancelled("job was cancelled")
                    ready = [step for step in pending.values() if set(step.get("dependsOn", [])) <= set(results)]
                    for step in ready:
                        running[executor.submit(self.run_step, job_id, step, abort)] = step["id"]
                        del pending[step["id"]]
                    if not running:
                        raise BrokerError("workflow made no progress")
                    completed, _ = concurrent.futures.wait(running, timeout=0.25, return_when=concurrent.futures.FIRST_COMPLETED)
                    for future in completed:
                        step_id = running.pop(future)
                        try:
                            results[step_id] = future.result()
                        except Exception:
                            abort.set()
                            for sibling in running:
                                sibling.cancel()
                            raise
            ordered = [results[step["id"]] for step in plan["steps"]]
            self.result(
                job_id, "succeeded", completedAt=iso(), steps=ordered, planHash=plan["planHash"],
                policySha256=plan["policySha256"], riskTier=plan["riskTier"], authorityReceipt=plan.get("authorityReceipt"),
                authorityGrantIds=sorted(reservations),
            )
            self.append_event(job_id, "job-finished", status="succeeded", policySha256=plan["policySha256"])
        except Cancelled as error:
            self.result(job_id, "cancelled", completedAt=iso(), error=safe_text(error, 1000), planHash=plan.get("planHash"), policySha256=plan.get("policySha256"))
            self.append_event(job_id, "job-finished", status="cancelled", policySha256=plan.get("policySha256"))
        except Exception as error:
            authority_failed = True
            self.result(job_id, "failed", completedAt=iso(), error=safe_text(error, 1000), planHash=plan.get("planHash"), policySha256=plan.get("policySha256"))
            self.append_event(job_id, "job-finished", status="failed", error=safe_text(error, 1000), policySha256=plan.get("policySha256"))
        finally:
            self.release_authority(job_id, reservations, authority_failed)
            with self.inflight_lock:
                self.inflight.discard(job_id)

    def schedule(self, executor: concurrent.futures.Executor, plan: dict[str, Any]) -> None:
        job_id = plan["jobId"]
        result_path = self.path("results", job_id)
        result = read_regular_json(result_path, MAX_RESULT_BYTES) if result_path.exists() else {}
        if result.get("status") in TERMINAL or result.get("status") == "running":
            return
        if self.cancelled(job_id):
            self.result(job_id, "cancelled", completedAt=iso(), planHash=plan["planHash"], policySha256=plan["policySha256"])
            self.append_event(job_id, "job-finished", status="cancelled", policySha256=plan["policySha256"])
            return
        if self.paused():
            self.result(job_id, "paused", planHash=plan["planHash"], policySha256=plan["policySha256"], riskTier=plan["riskTier"])
            return
        if not self.approved(plan):
            return
        with self.inflight_lock:
            if job_id in self.inflight:
                return
            self.inflight.add(job_id)
        executor.submit(self.execute_plan, plan)

    def recover_interrupted(self) -> None:
        """Fail closed after a broker process exits during an in-flight operation."""
        for result_path in sorted(self.directories["results"].glob("ops-*.json")):
            job_id = result_path.stem
            try:
                result = read_regular_json(result_path, MAX_RESULT_BYTES)
            except Exception:
                continue
            if result.get("status") != "running":
                continue
            self.result(
                job_id,
                "failed",
                completedAt=iso(),
                error="broker process was interrupted; automatic replay is forbidden",
                recoveryRequired=True,
                policySha256=result.get("policySha256"),
            )
            self.append_event(job_id, "job-recovered", status="failed", automaticReplay=False, policySha256=result.get("policySha256"))

    def serve(self, once: bool = False) -> int:
        with locked_file(self.state / ".broker.lock", blocking=False):
            self.recover_interrupted()
            workers = min(max(int(self.policy.get("maxWorkers", 4)), 1), 32)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                while True:
                    self.refresh_inventory_if_due()
                    for request_path in sorted(self.directories["requests"].glob("ops-*.json")):
                        try:
                            self.ingest(request_path)
                        except Exception:
                            pass
                    for plan_path in sorted(self.directories["plans"].glob("ops-*.json")):
                        try:
                            self.schedule(executor, read_regular_json(plan_path, MAX_REQUEST_BYTES))
                        except Exception as error:
                            job_id = plan_path.stem
                            if JOB_RE.fullmatch(job_id):
                                self.result(
                                    job_id, "failed", error=safe_text(error, 1000),
                                )
                    if once:
                        while True:
                            with self.inflight_lock:
                                active = bool(self.inflight)
                            if not active:
                                return 0
                            time.sleep(0.05)
                    time.sleep(0.25)


def approve(policy_path: Path, state: Path, job_id: str, plan_hash: str, ttl_minutes: int) -> int:
    validate_policy(read_regular_json(policy_path, MAX_POLICY_BYTES))
    if not JOB_RE.fullmatch(job_id) or not re.fullmatch(r"[a-f0-9]{64}", plan_hash):
        raise BrokerError("approval requires a safe job ID and SHA-256 plan hash")
    with locked_file(state / "authority" / ".mutation.lock"):
        plan = read_regular_json(state / "plans" / f"{job_id}.json", MAX_REQUEST_BYTES)
        if plan.get("jobId") != job_id or plan.get("planHash") != plan_hash or digest({key: value for key, value in plan.items() if key != "planHash"}) != plan_hash:
            raise BrokerError("approval hash does not match the stored immutable plan")
        if not plan.get("approvalRequired"):
            raise BrokerError("this job does not require approval")
        expires = min(max(ttl_minutes, 1), 60)
        atomic_json(state / "approvals" / f"{job_id}.json", {
            "schemaVersion": SCHEMA_VERSION, "jobId": job_id, "planHash": plan_hash,
            "approvedAt": iso(), "expiresAt": iso(utc_now() + timedelta(minutes=expires)),
            "boundary": "Approval applies only to the immutable plan with this hash.",
        }, 0o600)
    print(json.dumps({"jobId": job_id, "planHash": plan_hash, "approved": True, "ttlMinutes": expires}))
    return 0


def authority_show(policy_path: Path, state: Path) -> int:
    broker = Broker(policy_path, state)
    usage_path = broker.authority_root / "usage.json"
    usage = read_regular_json(usage_path, MAX_REQUEST_BYTES) if usage_path.exists() else {"schemaVersion": SCHEMA_VERSION, "grants": {}}
    print(json.dumps({
        "schemaVersion": SCHEMA_VERSION,
        "paused": broker.paused(),
        "standingGrants": broker.policy.get("authority", {}).get("grants", []),
        "activeLeases": broker.active_leases(),
        "usage": usage.get("grants", {}),
        "boundary": "Only an external operator can issue, revoke, pause, or resume authority.",
    }, indent=2))
    return 0


def authority_grant(policy_path: Path, state: Path, grant_path: Path, ttl_minutes: int) -> int:
    broker = Broker(policy_path, state)
    grant = validate_grant(read_regular_json(grant_path, MAX_REQUEST_BYTES), source="lease")
    if grant["id"] in {item["id"] for item in broker.policy.get("authority", {}).get("grants", [])}:
        raise BrokerError("authority lease id collides with a standing grant")
    if any(tier in {"managed", "change"} for tier in grant["tiers"]):
        if "*" in grant["actions"] or "*" in grant["targets"]:
            raise BrokerError("elevated authority leases cannot wildcard actions or targets")
        if "maxExecutions" not in grant or "maxFailures" not in grant or "maxConcurrent" not in grant:
            raise BrokerError("elevated authority leases require execution, failure, and concurrency budgets")
    known_actions = set(broker.policy["actions"]) | {"download.stage", "artifact.transfer"}
    for selector in grant["actions"]:
        if "*" not in selector and selector not in known_actions:
            raise BrokerError(f"authority lease references unknown action {selector}")
    known_targets = set(broker.policy["targets"]) | {"broker"}
    for selector in grant["targets"]:
        if "*" not in selector and selector not in known_targets:
            raise BrokerError(f"authority lease references unknown target {selector}")
    expires = min(max(int(ttl_minutes), 1), 10_080)
    now = utc_now()
    grant["issuedAt"] = iso(now)
    grant["expiresAt"] = iso(now + timedelta(minutes=expires))
    destination = broker.lease_directory / f"{grant['id']}.json"
    with broker.authority_transaction():
        if destination.exists():
            raise BrokerError("authority lease id has already been used")
        atomic_json(destination, grant, 0o600)
        broker.authority_audit(
            "authority-granted", grantId=grant["id"], expiresAt=grant["expiresAt"],
            grantHash=digest(grant), tiers=grant["tiers"], actions=grant["actions"], targets=grant["targets"],
        )
        broker.refresh_inventory()
    print(json.dumps({
        "grantId": grant["id"], "granted": True, "expiresAt": grant["expiresAt"],
        "grantHash": digest(grant), "boundary": "The lease can only authorize requests matching every stored constraint.",
    }))
    return 0


def authority_revoke(policy_path: Path, state: Path, grant_id: str) -> int:
    if not GRANT_RE.fullmatch(grant_id):
        raise BrokerError("authority revoke requires a safe grant id")
    broker = Broker(policy_path, state)
    path = broker.lease_directory / f"{grant_id}.json"
    with broker.authority_transaction():
        if not path.exists():
            raise BrokerError("authority lease does not exist")
        grant = read_regular_json(path, MAX_REQUEST_BYTES)
        if grant.get("revokedAt"):
            raise BrokerError("authority lease is already revoked")
        grant["revokedAt"] = iso()
        atomic_json(path, grant, 0o600)
        broker.authority_audit("authority-revoked", grantId=grant_id, grantHash=digest(grant))
        broker.refresh_inventory()
    print(json.dumps({"grantId": grant_id, "revoked": True}))
    return 0


def authority_pause(policy_path: Path, state: Path, paused: bool, reason: str) -> int:
    broker = Broker(policy_path, state)
    safe_reason = safe_text(reason, 500)
    with broker.authority_transaction():
        atomic_json(broker.authority_root / "pause.json", {
            "schemaVersion": SCHEMA_VERSION, "paused": paused, "changedAt": iso(), "reason": safe_reason,
        }, 0o600)
        broker.authority_audit("authority-paused" if paused else "authority-resumed", reason=safe_reason)
        broker.refresh_inventory()
    print(json.dumps({"paused": paused, "reason": safe_reason}))
    return 0


def authority_audit_show(policy_path: Path, state: Path, limit: int) -> int:
    broker = Broker(policy_path, state)
    path = broker.authority_root / "audit.jsonl"
    records: list[Any] = []
    if path.exists():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise BrokerError("authority audit is not a regular file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            window = min(info.st_size, MAX_RESULT_BYTES)
            offset = max(0, info.st_size - window)
            os.lseek(descriptor, offset, os.SEEK_SET)
            chunks, total = [], 0
            while total < window:
                chunk = os.read(descriptor, min(64 * 1024, window - total))
                if not chunk:
                    break
                chunks.append(chunk); total += len(chunk)
            payload = b"".join(chunks)
        finally:
            os.close(descriptor)
        lines = payload.decode("utf-8", "replace").splitlines()
        if offset and lines:
            lines = lines[1:]
        for line in lines[-min(max(limit, 1), 1000):]:
            records.append(json.loads(line))
    print(json.dumps({"schemaVersion": SCHEMA_VERSION, "records": records}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", default=os.environ.get("PIXEL_OPS_POLICY_PATH", "/etc/pixel-ops-broker/policy.json"))
    parser.add_argument("--state", default=os.environ.get("PIXEL_OPS_STATE_DIR", "/var/lib/pixel-ops-broker"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--approve", metavar="JOB_ID")
    parser.add_argument("--plan-hash")
    parser.add_argument("--approval-ttl-minutes", type=int, default=15)
    parser.add_argument("--authority-show", action="store_true")
    parser.add_argument("--authority-grant", metavar="GRANT_FILE")
    parser.add_argument("--lease-ttl-minutes", type=int, default=60)
    parser.add_argument("--authority-revoke", metavar="GRANT_ID")
    parser.add_argument("--authority-pause", action="store_true")
    parser.add_argument("--authority-resume", action="store_true")
    parser.add_argument("--authority-audit", action="store_true")
    parser.add_argument("--authority-audit-limit", type=int, default=100)
    parser.add_argument("--reason", default="operator request")
    arguments = parser.parse_args()
    policy_path, state = Path(arguments.policy), Path(arguments.state)
    authority_commands = sum(bool(value) for value in (
        arguments.authority_show, arguments.authority_grant, arguments.authority_revoke,
        arguments.authority_pause, arguments.authority_resume, arguments.authority_audit,
    ))
    if authority_commands > 1:
        raise BrokerError("select only one authority command")
    if arguments.authority_show:
        return authority_show(policy_path, state)
    if arguments.authority_grant:
        return authority_grant(policy_path, state, Path(arguments.authority_grant), arguments.lease_ttl_minutes)
    if arguments.authority_revoke:
        return authority_revoke(policy_path, state, arguments.authority_revoke)
    if arguments.authority_pause or arguments.authority_resume:
        return authority_pause(policy_path, state, arguments.authority_pause, arguments.reason)
    if arguments.authority_audit:
        return authority_audit_show(policy_path, state, arguments.authority_audit_limit)
    if arguments.approve:
        if not arguments.plan_hash:
            raise BrokerError("--approve requires --plan-hash")
        return approve(policy_path, state, arguments.approve, arguments.plan_hash, arguments.approval_ttl_minutes)
    return Broker(policy_path, state).serve(once=arguments.once)


if __name__ == "__main__":
    raise SystemExit(main())
