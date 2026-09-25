#!/usr/bin/env python3
"""Narrow ODS extension lifecycle proxy for Pixel Operations.

The server owns the ODS dashboard credential and exposes only a fixed local
grammar over a Unix socket.  The Pixel Operations Broker never receives the
credential, Docker access, an arbitrary HTTP client, or a generic host shell.
"""

from __future__ import annotations

import http.client
import hashlib
import importlib.util
import json
import os
import pathlib
import pwd
import re
import secrets
import socket
import stat
import sys
import time
import urllib.parse
from typing import Any

_peer_spec = importlib.util.spec_from_file_location("ods_unix_peer", pathlib.Path(__file__).with_name("unix_peer.py"))
_peer_module = importlib.util.module_from_spec(_peer_spec)
_peer_spec.loader.exec_module(_peer_module)
peer_ids = _peer_module.peer_ids

SCHEMA_VERSION = 1
KIND = "ods-pixel-extension-lifecycle"
INVENTORY_KIND = "ods-pixel-extension-inventory"
INSTALLATION_KIND = "ods-pixel-extension-installation"
REPOSITORY_KIND = "ods-pixel-extension-repository"
REPOSITORY_FILE_KIND = "ods-pixel-extension-repository-file"
RECIPE_VALIDATION_KIND = "ods-pixel-extension-recipe-validation"
RECIPE_DRAFT_KIND = "ods-pixel-extension-recipe-draft"
RECIPE_RECOVERY_KIND = "ods-pixel-extension-recipe-recovery"
RECIPE_PREPARATION_KIND = "ods-pixel-extension-recipe-preparation"
OPS_STATUS_KIND = "ods-pixel-operations-status"
BOUNDARY = (
    "Scoped ODS extension lifecycle proxy; it grants no Docker, shell, "
    "credential, arbitrary HTTP, or data-purge authority."
)
SERVICE_ID = re.compile(r"^[a-z0-9](?:[a-z0-9_-]|\.(?=[a-z0-9])){0,63}$")
HEX_KEY = re.compile(r"^[0-9a-f]{64}$")
JOB_ID = re.compile(r"^ops-[0-9]{13}-[a-f0-9]{12}$")
INVENTORY_BOUNDARY = (
    "Read-only live ODS extension inventory; it exposes only bounded status metadata "
    "and grants no installation, configuration, credential, Docker, or shell authority."
)
ALLOWED_ACTIONS = frozenset({"list", "inspect", "install", "install-next", "enable", "disable", "remove"})
OPS_STATUSES = frozenset(
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
MAX_REQUEST_BYTES = 4096
MAX_RECIPE_BYTES = 32768
MAX_FRAME_BYTES = 65536
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_OPS_STATUS_BYTES = 64 * 1024
BROKER_USER = "_ods_pixel_ops" if sys.platform == "darwin" else "pixel-ops-broker"
SOCKET_PATH = pathlib.Path("/private/var/lib/ods-pixel-manager/extension-manager.sock" if sys.platform == "darwin" else "/run/ods-pixel-manager/extension-manager.sock")
OPS_RESULTS_DIR = pathlib.Path("/private/var/lib/pixel-ops-broker/results" if sys.platform == "darwin" else "/var/lib/pixel-ops-broker/results")
TERMINAL_PROGRESS = frozenset({"started", "error", "idle"})
SUCCESS_STATUS = {
    "install": frozenset({"enabled", "cli_installed"}),
    "enable": frozenset({"enabled", "cli_installed"}),
    "disable": frozenset({"disabled"}),
    "remove": frozenset({"not_installed"}),
}


class ManagerError(RuntimeError):
    """A bounded manager failure safe to report without raw response data."""


class RepositoryRateLimitError(ManagerError):
    def __init__(self, retry_after: int):
        super().__init__('GitHub evidence is rate-limited')
        self.retry_after = retry_after


def _exact_object(value: Any, required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != required:
        raise ManagerError("invalid lifecycle request")
    return value


def _parse_request(payload: bytes) -> tuple[str, str]:
    if not payload or len(payload) > MAX_REQUEST_BYTES or b"\0" in payload:
        raise ManagerError("invalid lifecycle request")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("invalid lifecycle request") from exc
    value = _exact_object(value, {"schemaVersion", "action", "extensionId"})
    action = value.get("action")
    extension_id = value.get("extensionId")
    if value.get("schemaVersion") != SCHEMA_VERSION or action not in ALLOWED_ACTIONS:
        raise ManagerError("invalid lifecycle request")
    if not isinstance(extension_id, str) or SERVICE_ID.fullmatch(extension_id) is None:
        raise ManagerError("invalid extension id")
    if (action == "list") != (extension_id == "all"):
        raise ManagerError("invalid extension inventory request")
    return action, extension_id


def _repository_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 512 or any(ord(char) < 33 for char in value):
        raise ManagerError("invalid repository URL")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'https' or parsed.netloc.lower() != 'github.com' or parsed.query or parsed.fragment:
        raise ManagerError("invalid repository URL")
    path = parsed.path.strip('/')
    if path.endswith('.git'):
        path = path[:-4]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}', path):
        raise ManagerError("invalid repository URL")
    return 'https://github.com/' + path


def _parse_repository_request(payload: bytes) -> str:
    if not payload or len(payload) > MAX_REQUEST_BYTES or b'\0' in payload:
        raise ManagerError("invalid repository request")
    value = _exact_object(json.loads(payload.decode('utf-8')), {'schemaVersion', 'action', 'repositoryUrl'})
    if value.get('schemaVersion') != 1 or value.get('action') != 'github-inspect':
        raise ManagerError("invalid repository request")
    return _repository_url(value.get('repositoryUrl'))


def _repository_file_fields(repository: Any, commit: Any, path: Any) -> dict:
    repository = _repository_url(repository)
    if not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ManagerError('invalid repository revision')
    if (not isinstance(path, str) or not 1 <= len(path) <= 512 or '\\' in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or any(part in {'', '.', '..'} for part in path.split('/'))):
        raise ManagerError('invalid repository file path')
    return {'url': repository, 'commit': commit, 'path': path}


def _parse_repository_file_request(payload: bytes) -> dict:
    if not payload or len(payload) > MAX_REQUEST_BYTES or b'\0' in payload:
        raise ManagerError('invalid repository file request')
    value = _exact_object(json.loads(payload.decode('utf-8')),
                          {'schemaVersion', 'action', 'repositoryUrl', 'commit', 'path'})
    if value.get('schemaVersion') != 1 or value.get('action') != 'github-file':
        raise ManagerError('invalid repository file request')
    return _repository_file_fields(value['repositoryUrl'], value['commit'], value['path'])


def _recipe_candidate(value: Any) -> dict:
    value = _exact_object(value, {'repository', 'commit', 'manifest', 'compose'})
    _repository_url(value['repository'])
    if (not isinstance(value['commit'], str) or not re.fullmatch('[a-f0-9]{40}', value['commit'])
            or not isinstance(value['manifest'], dict) or not isinstance(value['compose'], dict)):
        raise ManagerError('invalid recipe proposal')
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(encoded) > MAX_RECIPE_BYTES:
        raise ManagerError('recipe proposal exceeds the tool limit')
    return value


def _recipe_parts(parts: list[str]) -> dict:
    # One broker request, no temporary files or partially saved uploads. Each
    # argument respects the broker limit; the assembled proposal keeps its own.
    if len(parts) != 8 or any(not isinstance(part, str) or len(part) > 4096 or '\0' in part for part in parts):
        raise ManagerError('invalid recipe parts')
    payload = ''.join(parts)
    if len(payload.encode('utf-8')) > MAX_RECIPE_BYTES:
        raise ManagerError('recipe proposal exceeds the tool limit')
    return _recipe_candidate(json.loads(payload))


def _parse_recipe_request(payload: bytes, action='github-validate') -> dict:
    if not payload or len(payload) > MAX_FRAME_BYTES or b'\0' in payload:
        raise ManagerError('invalid recipe validation request')
    value = _exact_object(json.loads(payload.decode()), {'schemaVersion', 'action', 'recipe'})
    if action not in ('github-validate', 'github-draft-save') or value.get('schemaVersion') != 1 or value.get('action') != action:
        raise ManagerError('invalid recipe validation request')
    return _recipe_candidate(value['recipe'])


def _parse_status_request(payload: bytes) -> tuple[str, str]:
    if not payload or len(payload) > MAX_REQUEST_BYTES or b"\0" in payload:
        raise ManagerError("invalid Operations status request")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("invalid Operations status request") from exc
    value = _exact_object(value, {"schemaVersion", "action", "jobId", "planHash"})
    job_id = value.get("jobId")
    plan_hash = value.get("planHash")
    if (
        value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("action") != "opsStatus"
        or not isinstance(job_id, str)
        or JOB_ID.fullmatch(job_id) is None
        or not isinstance(plan_hash, str)
        or HEX_KEY.fullmatch(plan_hash) is None
    ):
        raise ManagerError("invalid Operations status request")
    return job_id, plan_hash


def _read_operations_status(
    *, results_dir: pathlib.Path, broker_uid: int, job_id: str, plan_hash: str
) -> dict[str, Any]:
    """Project one nonsecret broker result without exposing protected plans."""
    if results_dir != OPS_RESULTS_DIR or JOB_ID.fullmatch(job_id) is None:
        raise ManagerError("Operations status is unavailable")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        directory = os.open(results_dir, directory_flags)
    except OSError as exc:
        raise ManagerError("Operations status is unavailable") from exc
    descriptor = -1
    try:
        directory_info = os.fstat(directory)
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or directory_info.st_uid != broker_uid
            or directory_info.st_mode & 0o022
        ):
            raise ManagerError("Operations status is unavailable")
        descriptor = os.open(
            f"{job_id}.json",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory,
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != broker_uid
            or before.st_mode & 0o022
            or not 1 <= before.st_size <= MAX_OPS_STATUS_BYTES
        ):
            raise ManagerError("Operations status is unavailable")
        payload = bytearray()
        while len(payload) < before.st_size:
            piece = os.read(descriptor, before.st_size - len(payload))
            if not piece:
                raise ManagerError("Operations status is unavailable")
            payload.extend(piece)
        if os.read(descriptor, 1):
            raise ManagerError("Operations status is unavailable")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ManagerError("Operations status is unavailable")
    except OSError as exc:
        raise ManagerError("Operations status is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory)
    try:
        value = json.loads(bytes(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("Operations status is unavailable") from exc
    if not isinstance(value, dict):
        raise ManagerError("Operations status is unavailable")
    status = value.get("status")
    risk_tier = value.get("riskTier")
    updated_at = value.get("updatedAt")
    approval_required = value.get("approvalRequired")
    if (
        value.get("schemaVersion") != 2
        or value.get("jobId") != job_id
        or value.get("planHash") != plan_hash
        or status not in OPS_STATUSES
        or not isinstance(risk_tier, str)
        or re.fullmatch(r"[a-z][a-z-]{0,31}", risk_tier) is None
        or not isinstance(updated_at, str)
        or not 1 <= len(updated_at) <= 64
        or not isinstance(approval_required, bool)
    ):
        raise ManagerError("Operations status is unavailable")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": OPS_STATUS_KIND,
        "jobId": job_id,
        "planHash": plan_hash,
        "status": status,
        "riskTier": risk_tier,
        "approvalRequired": approval_required,
        "updatedAt": updated_at,
    }


def _read_env(env_path: pathlib.Path) -> dict[str, str]:
    if not env_path.is_absolute() or env_path == pathlib.Path("/"):
        raise ManagerError("invalid ODS environment path")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(env_path, flags)
    except OSError as exc:
        raise ManagerError("ODS environment is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        current = env_path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or info.st_size > 2 * 1024 * 1024
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ManagerError("unsafe ODS environment file")
        payload = bytearray()
        while len(payload) <= 2 * 1024 * 1024:
            piece = os.read(descriptor, min(65536, 2 * 1024 * 1024 + 1 - len(payload)))
            if not piece:
                break
            payload.extend(piece)
    finally:
        os.close(descriptor)
    if len(payload) > 2 * 1024 * 1024 or b"\0" in payload:
        raise ManagerError("unsafe ODS environment file")
    try:
        lines = bytes(payload).decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ManagerError("ODS environment is unreadable") from exc
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key) is None:
            continue
        if key in values:
            raise ManagerError("ODS environment contains duplicate keys")
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def _read_env_key(values: dict[str, str], name: str) -> str:
    value = values.get(name, "")
    if HEX_KEY.fullmatch(value) is None:
        raise ManagerError("ODS dashboard credential is unavailable")
    return value


def _request_json(
    *, port: int, credential: str, method: str, path: str, timeout: float, body: dict | None = None
) -> tuple[int, dict[str, Any]]:
    if method not in {"GET", "POST", "DELETE"} or not path.startswith("/api/extensions/"):
        raise ManagerError("invalid internal ODS request")
    if body is not None:
        if method != 'POST' or not isinstance(body, dict):
            raise ManagerError('invalid repository API request')
        if path == '/api/extensions/github/inspect' and set(body) == {'url'}:
            body = {'url': _repository_url(body['url'])}
        elif path == '/api/extensions/github/file' and set(body) == {'url', 'commit', 'path'}:
            body = _repository_file_fields(body['url'], body['commit'], body['path'])
        elif path in ('/api/extensions/github/validate-recipe', '/api/extensions/github/drafts'):
            body = _recipe_candidate(body)
        elif path == '/api/extensions/github/requests/resolve':
            body = _exact_object(body, {'sessionHash'})
            if not isinstance(body['sessionHash'], str) or not re.fullmatch(r'[a-f0-9]{64}', body['sessionHash']):
                raise ManagerError('invalid request session hash')
        elif path == '/api/extensions/github/requests':
            # Commit pinning only needs to read the saved owner request. Keep
            # create/cancel outside this manager transport's authority.
            body = _exact_object(body, {'action', 'chatId', 'requestId'})
            if (body['action'] != 'read'
                    or any(not isinstance(body[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', body[key])
                           for key in ('chatId', 'requestId'))):
                raise ManagerError('invalid request-scoped read identity')
        elif path in {'/api/extensions/github/requests/status', '/api/extensions/github/requests/prepare',
                     '/api/extensions/github/requests/advance', '/api/extensions/github/requests/retry'}:
            extra = {'extensionId'} if path.endswith('/prepare') and 'extensionId' in body else set()
            body = _exact_object(body, {'chatId', 'requestId'} | extra)
            if extra and (not isinstance(body['extensionId'], str) or not SERVICE_ID.fullmatch(body['extensionId'])):
                raise ManagerError('invalid existing integration identity')
            if any(not isinstance(body[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', body[key])
                   for key in ('chatId', 'requestId')):
                raise ManagerError('invalid request-scoped status identity')
        elif path == '/api/extensions/github/requests/proposal':
            body = _exact_object(body, {'chatId', 'requestId', 'candidate'})
            if any(not isinstance(body[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', body[key])
                   for key in ('chatId', 'requestId')):
                raise ManagerError('invalid request-scoped proposal identity')
            body = {**body, 'candidate': _recipe_candidate(body['candidate'])}
        else:
            raise ManagerError('invalid repository API request')
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    response: http.client.HTTPResponse | None = None
    try:
        connection.request(
            method,
            path,
            body=json.dumps(body or {}).encode() if method == "POST" else None,
            headers={
                "Authorization": f"Bearer {credential}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        status = int(response.status)
        content_type = response.headers.get_content_type()
        declared = response.headers.get("Content-Length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES):
            raise ManagerError("ODS extension API response is oversized")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (TimeoutError, OSError, http.client.HTTPException) as exc:
        raise ManagerError("ODS extension API is unavailable") from exc
    finally:
        if response is not None:
            response.close()
        connection.close()
    if len(payload) > MAX_RESPONSE_BYTES or content_type != "application/json":
        raise ManagerError("ODS extension API returned an invalid response")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("ODS extension API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ManagerError("ODS extension API returned an invalid object")
    return status, value


def _configuration_keys(detail: dict[str, Any]) -> tuple[list[str], list[str]]:
    required: list[str] = []
    optional: list[str] = []
    classifications: dict[str, bool] = {}
    env_vars = detail.get("env_vars", [])
    if not isinstance(env_vars, list) or len(env_vars) > 128:
        raise ManagerError("extension configuration metadata is invalid")
    for item in env_vars:
        if not isinstance(item, dict):
            raise ManagerError("extension configuration metadata is invalid")
        key = item.get("key")
        if not isinstance(key, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", key) is None:
            raise ManagerError("extension configuration metadata is invalid")
        is_required = item.get("required", False)
        if not isinstance(is_required, bool):
            # Malformed declarations must never downgrade a prerequisite into
            # optional configuration at the mutation boundary.
            raise ManagerError("extension configuration metadata is invalid")
        if key in classifications and classifications[key] != is_required:
            raise ManagerError("extension configuration metadata is ambiguous")
        classifications[key] = is_required
        destination = required if is_required else optional
        if key not in destination:
            destination.append(key)
    return sorted(required), sorted(optional)


def _bounded_status(value: Any) -> str:
    allowed = {
        "enabled", "cli_installed", "disabled", "stopped", "unhealthy",
        "installing", "setting_up", "error", "not_installed", "incompatible",
    }
    if not isinstance(value, str) or value not in allowed:
        raise ManagerError("extension status is invalid")
    return value


def _same_effective_status(left: str, right: str) -> bool:
    return left == right or {left, right} <= {"enabled", "cli_installed"}


def _bounded_text(value: Any, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ManagerError(f"extension {label} is invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ManagerError(f"extension {label} is invalid")
    return value


def _extension_inventory(port: int, credential: str) -> dict[str, Any]:
    status, value = _request_json(
        port=port,
        credential=credential,
        method="GET",
        path="/api/extensions/catalog",
        timeout=30,
    )
    rows = value.get("extensions")
    if status != 200 or not isinstance(rows, list) or len(rows) > 256:
        raise ManagerError("extension inventory is unavailable")
    projected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ManagerError("extension inventory is invalid")
        extension_id = row.get("id")
        extension_status = _bounded_status(row.get("status"))
        source = row.get("source")
        installable = row.get("installable")
        if (
            not isinstance(extension_id, str)
            or SERVICE_ID.fullmatch(extension_id) is None
            or extension_id in seen
            or source not in {"core", "user", "library"}
            or not isinstance(installable, bool)
        ):
            raise ManagerError("extension inventory is invalid")
        seen.add(extension_id)
        projected.append(
            {
                "id": extension_id,
                "name": _bounded_text(row.get("name"), "name", 128),
                "category": _bounded_text(row.get("category"), "category", 64),
                "status": extension_status,
                "source": source,
                "installable": installable,
            }
        )
    projected.sort(key=lambda item: (item["source"], item["name"].casefold(), item["id"]))
    counts = {
        name: sum(1 for row in projected if row["status"] == status_name)
        for name, status_name in (
            ("enabled", "enabled"),
            ("cliInstalled", "cli_installed"),
            ("disabled", "disabled"),
            ("stopped", "stopped"),
            ("unhealthy", "unhealthy"),
            ("installing", "installing"),
            ("settingUp", "setting_up"),
            ("error", "error"),
            ("notInstalled", "not_installed"),
            ("incompatible", "incompatible"),
        )
    }
    installed_statuses = {
        "enabled", "cli_installed", "disabled", "stopped", "unhealthy",
    }
    summary = {
        "total": len(projected),
        "installed": sum(1 for row in projected if row["status"] in installed_statuses),
        **counts,
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": INVENTORY_KIND,
        "outcome": "succeeded",
        "summary": summary,
        "extensions": projected,
        "boundary": INVENTORY_BOUNDARY,
    }


def _detail(port: int, credential: str, extension_id: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(extension_id, safe="")
    status, value = _request_json(
        port=port,
        credential=credential,
        method="GET",
        path=f"/api/extensions/{encoded}",
        timeout=20,
    )
    if status != 200 or value.get("id") != extension_id:
        raise ManagerError("extension is not present in the ODS catalog")
    _bounded_status(value.get("status"))
    return value


def _public_result(
    *,
    action: str,
    extension_id: str,
    outcome: str,
    previous_status: str,
    current_status: str,
    changed: bool,
    external_effect: bool,
    required_configuration: list[str],
    optional_configuration: list[str],
    missing_configuration: list[str],
    rollback_attempted: bool = False,
    rollback_succeeded: bool | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "action": action,
        "extensionId": extension_id,
        "outcome": outcome,
        "previousStatus": previous_status,
        "currentStatus": current_status,
        "changed": changed,
        "externalEffectOccurred": external_effect,
        "requiredConfiguration": required_configuration,
        "optionalConfiguration": optional_configuration,
        "missingConfiguration": missing_configuration,
        # These keys describe the manifest's environment contract. Even a
        # successful lifecycle action does not qualify every runtime feature.
        "configurationScope": "declared-environment-keys",
        "runtimeRequirementsVerified": False,
        "rollback": {
            "attempted": rollback_attempted,
            "succeeded": rollback_succeeded,
        },
        "boundary": BOUNDARY,
    }


def _project_installation_plan(value: dict, extension_id: str) -> dict[str, Any]:
    """Bounded read-only prerequisites; never forward manifest/env values."""
    unavailable = {"state": "unavailable", "steps": []}
    try:
        rows = value.get("steps")
        if (value.get("schemaVersion") != 1
                or value.get("extensionId") != extension_id
                or not isinstance(rows, list) or not 1 <= len(rows) <= 128):
            return unavailable
        projected, seen = [], set()
        for row in rows:
            if not isinstance(row, dict):
                return unavailable
            key, action, missing = row.get("extensionId"), row.get("action"), row.get("missingConfiguration")
            if (not isinstance(key, str) or SERVICE_ID.fullmatch(key) is None or key in seen
                    or action not in ("none", "install", "enable", "wait", "blocked")
                    or not isinstance(missing, list) or len(missing) > 128
                    or any(not isinstance(k, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", k) is None for k in missing)
                    or len(set(missing)) != len(missing)):
                return unavailable
            current = _bounded_status(row.get("status"))
            expected = {"enabled": "none", "cli_installed": "none", "disabled": "enable",
                        "stopped": "enable", "not_installed": "install",
                        "installing": "wait", "setting_up": "wait"}.get(current, "blocked")
            if action not in (expected, "blocked"):
                return unavailable
            seen.add(key)
            projected.append({"extensionId": key, "status": current, "action": action,
                              "missingConfiguration": sorted(missing)})
        if projected[-1]["extensionId"] != extension_id:
            return unavailable
        state = "ready"
        if any(s["action"] == "blocked" for s in projected):
            state = "blocked"
        elif any(s["missingConfiguration"] for s in projected):
            state = "configuration_required"
        elif any(s["action"] == "wait" for s in projected):
            state = "pending"
        elif any(s["action"] != "none" for s in projected[:-1]):
            state = "dependencies_required"
        return {"state": state, "steps": projected}
    except (ManagerError, ValueError, TypeError):
        return unavailable


def _installation_prerequisites(port: int, credential: str, extension_id: str) -> dict[str, Any]:
    try:
        status, value = _request_json(
            port=port, credential=credential, method="GET",
            path=f"/api/extensions/{urllib.parse.quote(extension_id, safe='')}/install-plan", timeout=60,
        )
        if status == 200:
            return _project_installation_plan(value, extension_id)
    except ManagerError:
        pass
    return {"state": "unavailable", "steps": []}


def _installation_result(extension_id: str, state="reconciliation_required", active=None,
                         dispatched=True, prerequisites=None):
    return {"schemaVersion": 1, "kind": INSTALLATION_KIND, "action": "install-next",
            "extensionId": extension_id, "state": state, "activeExtensionId": active,
            "externalEffectAttempted": dispatched,
            "prerequisites": prerequisites or {"state": "unavailable", "steps": []},
            "boundary": BOUNDARY}


def _install_next(port: int, credential: str, extension_id: str) -> dict[str, Any]:
    # A missing reply may follow an accepted host action. Never retry the POST
    # here; the coordinator's durable journal owns reconciliation.
    try:
        status, value = _request_json(
            port=port, credential=credential, method="POST",
            path=f"/api/extensions/{urllib.parse.quote(extension_id, safe='')}/install-next", timeout=180,
        )
        if (status != 200 or value.get("schemaVersion") != 1
                or value.get("extensionId") != extension_id
                or not isinstance(value.get("plan"), dict)
                or type(value.get("dispatched")) is not bool):
            return _installation_result(extension_id)
        prerequisites = _project_installation_plan(value["plan"], extension_id)
        state, active = value.get("state"), value.get("activeExtensionId")
        if (prerequisites['state'] == 'unavailable'
                or state not in ('succeeded', 'pending', 'blocked', 'configuration_required', 'reconciliation_required')
                or (active is not None and active not in [s['extensionId'] for s in prerequisites['steps']])):
            return _installation_result(extension_id)
        if state == 'succeeded' and (value['dispatched'] or active is not None
                or any(s['action'] != 'none' for s in prerequisites['steps'])):
            return _installation_result(extension_id)
        if value['dispatched'] and (state not in ('pending', 'reconciliation_required') or active is None):
            return _installation_result(extension_id)
        return _installation_result(extension_id, state, active, value['dispatched'], prerequisites)
    except ManagerError:
        return _installation_result(extension_id)


def _wait_for_status(
    *, port: int, credential: str, extension_id: str, expected: frozenset[str], deadline: float
) -> str:
    last_status = "not_installed"
    while time.monotonic() < deadline:
        detail = _detail(port, credential, extension_id)
        last_status = _bounded_status(detail.get("status"))
        if last_status in expected or last_status in {"error", "unhealthy", "incompatible"}:
            return last_status
        time.sleep(1.0)
    return last_status


def _mutate(
    *, port: int, credential: str, action: str, extension_id: str, previous: str
) -> None:
    encoded = urllib.parse.quote(extension_id, safe="")
    if action == "install":
        method, suffix = "POST", "/install"
    elif action == "enable":
        # Dependency changes must be separate exact owner-approved plans.
        method, suffix = "POST", "/enable?auto_enable_deps=false"
    elif action == "disable":
        method, suffix = "POST", "/disable?include_data_info=false"
    elif action == "remove":
        method, suffix = "DELETE", "?include_data_info=false"
    else:
        raise ManagerError("unsupported lifecycle action")
    status, _value = _request_json(
        port=port,
        credential=credential,
        method=method,
        path=f"/api/extensions/{encoded}{suffix}",
        timeout=120,
    )
    if status not in {200, 202}:
        # Idempotency is determined from the pre-mutation catalog state, not
        # from error strings returned by the internal API.
        raise ManagerError(f"ODS rejected extension {action} from state {previous}")


def _validation_receipt(value: dict, recipe: dict) -> dict:
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if (value.get('schemaVersion') != 1 or type(value.get('valid')) is not bool
            or _repository_url(value.get('repository')).lower() != _repository_url(recipe['repository']).lower()
            or value.get('commit') != recipe['commit'] or value.get('recipeDigest') != digest
            or value.get('validationScope') != 'static-manifest-and-compose'
            or any(value.get(key) is not False for key in
                   ('provenanceVerified', 'runtimeVerified', 'installationStarted', 'registered'))):
        raise ManagerError('invalid recipe validation evidence')
    errors, existing = value.get('errors'), value.get('existingExtensionIds')
    if (not isinstance(errors, list) or len(errors) > 32 or not isinstance(existing, list) or len(existing) > 256
            or any(not isinstance(key, str) or not SERVICE_ID.fullmatch(key) for key in existing)):
        raise ManagerError('invalid recipe diagnostics')
    for error in errors:
        if (not isinstance(error, dict) or set(error) != {'code', 'path'}
                or not isinstance(error['code'], str) or not re.fullmatch('[a-z-]{1,80}', error['code'])
                or not isinstance(error['path'], str) or not re.fullmatch(r'[A-Za-z0-9_/$.-]{1,512}', error['path'])):
            raise ManagerError('invalid recipe diagnostics')
    if value['valid'] != (not errors) or (existing and value['valid']):
        raise ManagerError('inconsistent recipe validation evidence')
    return {'schemaVersion': 1, 'kind': RECIPE_VALIDATION_KIND,
            'repository': _repository_url(recipe['repository']), 'commit': recipe['commit'],
            'recipeDigest': digest, 'valid': value['valid'], 'errors': errors,
            'existingExtensionIds': sorted(set(existing)), 'validationScope': 'static-manifest-and-compose',
            'provenanceVerified': False, 'runtimeVerified': False, 'installationStarted': False, 'registered': False,
            'boundary': 'Static recipe diagnostics only; this does not authorize or perform installation.'}


def _draft_receipt(value, recipe):
    digest = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if (not isinstance(value, dict) or value.get('schemaVersion') != 1
            or not isinstance(value.get('draftId'), str) or not re.fullmatch('[a-f0-9]{64}', value['draftId'])
            or value.get('recipeDigest') != digest or value.get('state') != 'draft'
            or value.get('requiresRevalidation') is not True
            or value.get('installationStarted') is not False or value.get('registered') is not False):
        raise ManagerError('invalid recipe draft receipt')
    return {'schemaVersion': 1, 'kind': RECIPE_DRAFT_KIND, 'repository': recipe['repository'],
            'draftId': value['draftId'], 'recipeDigest': digest, 'state': 'draft',
            'requiresRevalidation': True, 'installationStarted': False, 'registered': False}


def _preparation_receipt(value, draft_id, recipe=None):
    draft_id = _draft_id(draft_id)
    if (not isinstance(value, dict) or value.get('schemaVersion') != 1
            or value.get('state') != 'available'
            or not isinstance(value.get('extensionId'), str) or not SERVICE_ID.fullmatch(value['extensionId'])
            or not isinstance(value.get('recipeDigest'), str) or not HEX_KEY.fullmatch(value['recipeDigest'])
            or any(value.get(key) is not False for key in ('installationStarted', 'registered', 'runtimeVerified'))):
        raise ManagerError('invalid recipe preparation receipt')
    if recipe is not None:
        digest = hashlib.sha256(json.dumps(recipe, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        if value['recipeDigest'] != digest or value['extensionId'] != recipe.get('manifest', {}).get('service', {}).get('id'):
            raise ManagerError('recipe preparation identity mismatch')
    elif value.get('draftId') != draft_id or value.get('kind') != RECIPE_PREPARATION_KIND:
        raise ManagerError('recipe preparation draft mismatch')
    return {'schemaVersion': 1, 'kind': RECIPE_PREPARATION_KIND, 'draftId': draft_id,
            'extensionId': value['extensionId'], 'recipeDigest': value['recipeDigest'], 'state': 'available',
            'installationStarted': False, 'registered': False, 'runtimeVerified': False}


def _prepare_repository_draft(env_path, port, draft_id):
    draft_id = _draft_id(draft_id)
    recovered = _read_repository_draft(env_path, port, draft_id)
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/drafts/' + draft_id + '/prepare', timeout=90, body={})
    if status != 200:
        raise ManagerError('recipe preparation requires inspection')
    return _preparation_receipt(value, draft_id, recovered['candidate'])


def _draft_id(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{64}', value):
        raise ManagerError('invalid recipe draft ID')
    return value


def _refresh_projected_credential(source: pathlib.Path, destination: pathlib.Path) -> None:
    """Refresh an explicitly service-configured Windows/WSL projection.

    The source follows its host's access controls, not synthesized DrvFS mode
    bits. This opt-in never changes the normal protected environment reader.
    Socket callers cannot supply either path. Only one key is copied.
    """
    if (not source.is_absolute() or source.name != '.env' or source.resolve() != source
            or not destination.is_absolute() or destination.name != '.env'
            or destination.parent.resolve() != destination.parent or source == destination):
        raise ManagerError('invalid credential projection configuration')
    if destination.lstat().st_mode & 0o077:
        raise ManagerError('credential projection is not private')
    existing = _read_env(destination)
    if set(existing) != {'DASHBOARD_API_KEY'}:
        raise ManagerError('projection must contain only the dashboard credential')
    _read_env_key(existing, 'DASHBOARD_API_KEY')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(source, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 1 <= before.st_size <= 2 * 1024 * 1024:
            raise ManagerError('invalid credential source')
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        after = os.fstat(descriptor)
        current = source.lstat()
        signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        if signature(before) != signature(after) or signature(after) != signature(current) or len(data) != before.st_size or b'\0' in data:
            raise ManagerError('credential source changed during read')
    finally:
        os.close(descriptor)
    matches = []
    for line in data.decode('utf-8').splitlines():
        key, separator, value = line.strip().partition('=')
        if separator and key.strip() == 'DASHBOARD_API_KEY':
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            matches.append(value)
    if len(matches) != 1 or HEX_KEY.fullmatch(matches[0]) is None:
        raise ManagerError('invalid projected credential')
    if matches[0] == existing['DASHBOARD_API_KEY']:
        return
    directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | getattr(os, 'O_NOFOLLOW', 0))
    temporary = '.credential-' + secrets.token_hex(12)
    try:
        info = os.fstat(directory)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ManagerError('unsafe credential projection directory')
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600, dir_fd=directory)
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(f'DASHBOARD_API_KEY={matches[0]}\n'.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def _recover_draft_receipt(value, draft_id):
    draft_id = _draft_id(draft_id)
    if (not isinstance(value, dict) or value.get('schemaVersion') != 1
            or value.get('draftId') != draft_id or value.get('state') != 'draft'
            or value.get('requiresRevalidation') is not True
            or value.get('installationStarted') is not False or value.get('registered') is not False):
        raise ManagerError('invalid draft recovery receipt')
    recipe = _recipe_candidate(value.get('candidate'))
    return {'schemaVersion': 1, 'kind': RECIPE_RECOVERY_KIND, 'draftId': draft_id,
            'state': 'draft', 'candidate': recipe, 'requiresRevalidation': True,
            'installationStarted': False, 'registered': False,
            'contentTrust': 'untrusted-recipe-proposal'}


def _read_repository_draft(env_path, port, draft_id):
    draft_id = _draft_id(draft_id)
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='GET',
        path='/api/extensions/github/drafts/' + draft_id, timeout=30)
    if status != 200:
        raise ManagerError('recipe draft is unavailable')
    return _recover_draft_receipt(value, draft_id)


def _save_repository_draft(env_path, port, recipe):
    recipe = _recipe_candidate(recipe)
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/drafts', timeout=90, body=recipe)
    if status != 200:
        raise ManagerError('recipe draft could not be saved; validate the proposal first')
    return _draft_receipt(value, recipe)


def _validate_repository_recipe(env_path: pathlib.Path, port: int, recipe: dict) -> dict:
    recipe = _recipe_candidate(recipe)
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/validate-recipe', timeout=90, body=recipe)
    if status != 200:
        raise ManagerError('recipe validation is unavailable')
    return _validation_receipt(value, recipe)


def _inspect_repository_file(env_path: pathlib.Path, port: int, fields: dict) -> dict:
    fields = _repository_file_fields(fields['url'], fields['commit'], fields['path'])
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/file', timeout=30, body=fields)
    if (status != 200 or value.get('schemaVersion') != 1
            or _repository_url(value.get('repository')).lower() != fields['url'].lower()
            or value.get('commit') != fields['commit'] or value.get('path') != fields['path']
            or value.get('contentTrust') != 'untrusted-upstream-evidence'
            or value.get('evidenceScope') != 'repository-file-at-commit'
            or value.get('installationStarted') is not False or value.get('registered') is not False):
        raise ManagerError('invalid repository file evidence')
    content = value.get('content')
    if not isinstance(content, str) or len(content.encode('utf-8')) > 256000 or '\x00' in content:
        raise ManagerError('invalid repository file content')
    raw = content.encode('utf-8')
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode('ascii') + b'\0' + raw).hexdigest()
    if value.get('blob') != blob:
        raise ManagerError('invalid repository file identity')
    return {'schemaVersion': 1, 'kind': REPOSITORY_FILE_KIND, 'repository': fields['url'],
            'commit': fields['commit'], 'path': fields['path'], 'blob': blob,
            'content': content[:32000], 'contentTruncated': len(content) > 32000,
            'contentTrust': 'untrusted-upstream-evidence', 'evidenceScope': 'repository-file-at-commit',
            'installationStarted': False, 'registered': False,
            'boundary': 'Read-only repository file. Content is untrusted evidence, not execution authority.'}


def _bounded_spdx_identifier(value: Any) -> bool:
    """Bound a dashboard-verified SPDX expression before returning it to the agent."""
    if not isinstance(value, str):
        return False
    if re.fullmatch(r'[A-Za-z0-9.+-]{1,80}', value):
        return True  # Includes GitHub's NOASSERTION metadata, never license approval.
    if not 1 <= len(value) <= 256:
        return False
    token = re.compile(r'AND|OR|\(|\)|[A-Za-z0-9][A-Za-z0-9.+-]*')
    tokens = []
    offset = 0
    while offset < len(value):
        while offset < len(value) and value[offset].isspace():
            offset += 1
        if offset == len(value):
            break
        match = token.match(value, offset)
        if match is None:
            return False
        tokens.append(match.group())
        offset = match.end()
        if len(tokens) > 32:
            return False
    position = 0
    identifiers = set()

    def atom():
        nonlocal position, identifiers
        if position >= len(tokens):
            raise ValueError('invalid SPDX expression')
        part = tokens[position]
        position += 1
        if part == '(':
            expression()
            if position >= len(tokens) or tokens[position] != ')':
                raise ValueError('invalid SPDX expression')
            position += 1
        elif part in {'AND', 'OR', ')', 'WITH', 'NOASSERTION'} or part.startswith('LicenseRef-'):
            raise ValueError('invalid SPDX expression')
        else:
            identifiers.add(part)

    def conjunction():
        nonlocal position
        atom()
        while position < len(tokens) and tokens[position] == 'AND':
            position += 1
            atom()

    def expression():
        nonlocal position
        conjunction()
        while position < len(tokens) and tokens[position] == 'OR':
            position += 1
            conjunction()

    try:
        expression()
    except ValueError:
        return False
    return position == len(tokens) and len(identifiers) >= 2


def _inspect_repository(env_path: pathlib.Path, port: int, repository: str) -> dict[str, Any]:
    repository = _repository_url(repository)
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/inspect', timeout=90, body={'url': repository})
    detail = value.get('detail') if isinstance(value, dict) else None
    if (status == 429 and isinstance(detail, dict)
            and set(detail) == {'code', 'retryAfter'}
            and detail['code'] == 'github-rate-limited'
            and type(detail['retryAfter']) is int and 1 <= detail['retryAfter'] <= 3600):
        raise RepositoryRateLimitError(detail['retryAfter'])
    if (status != 200 or value.get('schemaVersion') != 1
            or _repository_url(value.get('repository')).lower() != repository.lower()
            or not isinstance(value.get('commit'), str) or not re.fullmatch('[a-f0-9]{40}', value['commit'])
            or value.get('contentTrust') != 'untrusted-upstream-evidence'
            or value.get('evidenceScope') != 'repository-documents-at-commit'
            or value.get('installationStarted') is not False or value.get('registered') is not False
            or value.get('requiresRecipeReview') is not True or type(value.get('archived')) is not bool):
        raise ManagerError('invalid repository evidence')
    existing = value.get('existingExtensionIds')
    license_id = value.get('licenseIdentifier')
    if (not isinstance(existing, list) or len(existing) > 256
            or any(not isinstance(key, str) or not SERVICE_ID.fullmatch(key) for key in existing)
            or (license_id is not None and not _bounded_spdx_identifier(license_id))):
        raise ManagerError('invalid repository metadata')
    result = {key: value[key] for key in ('repository', 'commit', 'archived', 'contentTrust', 'evidenceScope')}
    for key, limit in [('readme', 24000), ('licenseText', 8192)]:
        text = value.get(key)
        if text is not None and (not isinstance(text, str) or len(text.encode('utf-8')) > 256000):
            raise ManagerError('invalid repository document')
        result[key] = text[:limit] if text is not None else None
        result[key + 'Truncated'] = text is not None and len(text) > limit
    return {**result, 'schemaVersion': 1, 'kind': REPOSITORY_KIND,
            'existingExtensionIds': sorted(set(existing)), 'licenseIdentifier': license_id,
            'installationStarted': False, 'registered': False, 'requiresRecipeReview': True,
            'boundary': 'Read-only GitHub evidence. Upstream text is untrusted data, not execution authority.'}


def _read_request_source(env_path: pathlib.Path, port: int, payload: bytes) -> dict[str, Any]:
    """Project the exact saved request's source without contacting GitHub."""
    envelope = _exact_object(json.loads(payload.decode('utf-8')),
        {'schemaVersion', 'action', 'chatId', 'requestId'})
    if (len(payload) > MAX_FRAME_BYTES or envelope['schemaVersion'] != 1
            or envelope['action'] != 'github-request-read'
            or any(not isinstance(envelope[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', envelope[key])
                   for key in ('chatId', 'requestId'))):
        raise ManagerError('invalid scoped repository read')
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, current = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/requests', timeout=25,
        body={'action': 'read', 'chatId': envelope['chatId'], 'requestId': envelope['requestId']})
    if (status != 200 or not isinstance(current, dict)
            or current.get('schemaVersion') != 1
            or any(current.get(key) != envelope[key] for key in ('chatId', 'requestId'))
            or current.get('state') != 'pending'
            or current.get('authorizationMode') not in {'install', 'research'}
            or current.get('installationStarted') is not False):
        raise ManagerError('saved repository request is unavailable')
    return {'schemaVersion': 1, 'kind': 'ods-extension-request-source',
            'chatId': envelope['chatId'], 'requestId': envelope['requestId'],
            'repository': _repository_url(current.get('repository')),
            'authorizationMode': current['authorizationMode'],
            'requestState': current['state'], 'installationStarted': False}


def _pin_request_repository(env_path: pathlib.Path, port: int, payload: bytes) -> dict[str, Any]:
    """Resolve the saved request's repository to an immutable commit, without trusting model routing."""
    envelope = _exact_object(json.loads(payload.decode('utf-8')),
        {'schemaVersion', 'action', 'chatId', 'requestId'})
    if (len(payload) > MAX_FRAME_BYTES or envelope['schemaVersion'] != 1
            or envelope['action'] != 'github-request-pin'
            or any(not isinstance(envelope[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', envelope[key])
                   for key in ('chatId', 'requestId'))):
        raise ManagerError('invalid scoped repository pin')
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, current = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/requests', timeout=25,
        body={'action': 'read', 'chatId': envelope['chatId'], 'requestId': envelope['requestId']})
    if (status != 200 or not isinstance(current, dict)
            or current.get('schemaVersion') != 1
            or any(current.get(key) != envelope[key] for key in ('chatId', 'requestId'))
            or current.get('state') != 'pending'
            or current.get('authorizationMode') not in {'install', 'research'}
            or current.get('installationStarted') is not False):
        raise ManagerError('saved repository request is unavailable')
    repository = _repository_url(current.get('repository'))
    try:
        evidence = _inspect_repository(env_path, port, repository)
    except RepositoryRateLimitError as error:
        return {'schemaVersion': 1, 'kind': 'ods-extension-request-repository-unavailable',
                'chatId': envelope['chatId'], 'requestId': envelope['requestId'],
                'repository': repository, 'reason': 'github-rate-limited',
                'retryAfter': error.retry_after, 'installationStarted': False}
    if (evidence['repository'].lower() != repository.lower()
            or not re.fullmatch(r'[a-f0-9]{40}', evidence['commit'])
            or evidence['evidenceScope'] != 'repository-documents-at-commit'
            or evidence['installationStarted'] is not False):
        raise ManagerError('repository pin could not be verified')
    return {'schemaVersion': 1, 'kind': 'ods-extension-request-commit',
            'chatId': envelope['chatId'], 'requestId': envelope['requestId'],
            'repository': repository, 'commit': evidence['commit'],
            'evidenceScope': 'repository-default-branch-at-inspection',
            'installationStarted': False}


def _resolve_request(env_path, port, payload):
    envelope = _exact_object(json.loads(payload.decode('utf-8')),
        {'schemaVersion', 'action', 'sessionHash'})
    if (len(payload) > MAX_FRAME_BYTES or envelope['schemaVersion'] != 1
            or envelope['action'] != 'github-request-resolve'
            or not isinstance(envelope['sessionHash'], str)
            or not re.fullmatch(r'[a-f0-9]{64}', envelope['sessionHash'])):
        raise ManagerError('invalid request session')
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/requests/resolve', timeout=25,
        body={'sessionHash': envelope['sessionHash']})
    value = _exact_object(value, {'schemaVersion', 'kind', 'sessionHash', 'request', 'authorizationMode'})
    if (status != 200 or value['schemaVersion'] != 1 or value['kind'] != 'ods-extension-request-scope'
            or value['sessionHash'] != envelope['sessionHash']
            or value['authorizationMode'] not in {'install', 'research', None}
            or (value['request'] is None) != (value['authorizationMode'] is None)):
        raise ManagerError('invalid request scope receipt')
    if value['request'] is not None:
        identity = _exact_object(value['request'], {'chatId', 'requestId'})
        if (any(not isinstance(identity[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity[key])
                for key in ('chatId', 'requestId'))
                or hashlib.sha256(identity['chatId'].encode()).hexdigest() != envelope['sessionHash']):
            raise ManagerError('request session changed')
    return value


def _advance_request(env_path, port, payload):
    envelope = _exact_object(json.loads(payload.decode('utf-8')),
        {'schemaVersion', 'action', 'chatId', 'requestId'})
    if (len(payload) > MAX_FRAME_BYTES or envelope['schemaVersion'] != 1
            or envelope['action'] not in {'github-request-advance', 'github-request-retry'}
            or any(not isinstance(envelope[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', envelope[key])
                   for key in ('chatId', 'requestId'))):
        raise ManagerError('invalid scoped installation request')
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/requests/' + ('retry' if envelope['action'] == 'github-request-retry' else 'advance'), timeout=90,
        body={key: envelope[key] for key in ('chatId', 'requestId')})
    value = _exact_object(value, {'schemaVersion', 'kind', 'chatId', 'requestId', 'extensionId',
        'state', 'activeExtensionId', 'operationId', 'dispatched'})
    if (status != 200 or value['schemaVersion'] != 1 or value['kind'] != 'ods-extension-request-installation'
            or any(value[key] != envelope[key] for key in ('chatId', 'requestId'))
            or value['state'] not in {'pending', 'succeeded', 'failed', 'blocked', 'configuration_required', 'reconciliation_required'}
            or not isinstance(value['extensionId'], str) or not SERVICE_ID.fullmatch(value['extensionId'])
            or (value['activeExtensionId'] is not None and (not isinstance(value['activeExtensionId'], str)
                or not SERVICE_ID.fullmatch(value['activeExtensionId'])))
            or (value['operationId'] is not None and (not isinstance(value['operationId'], str)
                or not re.fullmatch(r'[a-f0-9]{32}', value['operationId'])))
            or type(value['dispatched']) is not bool
            or (value['state'] == 'succeeded' and (value['dispatched'] or value['activeExtensionId'] is not None))):
        raise ManagerError('invalid scoped installation receipt')
    return value


def _prepare_request(env_path, port, payload):
    envelope = json.loads(payload.decode('utf-8'))
    extra = {'extensionId'} if isinstance(envelope, dict) and 'extensionId' in envelope else set()
    envelope = _exact_object(envelope, {'schemaVersion', 'action', 'chatId', 'requestId'} | extra)
    if (len(payload) > MAX_FRAME_BYTES or envelope['schemaVersion'] != 1
            or envelope['action'] != 'github-request-prepare'
            or any(not isinstance(envelope[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', envelope[key])
                   for key in ('chatId', 'requestId'))):
        raise ManagerError('invalid scoped preparation request')
    if extra and (not isinstance(envelope['extensionId'], str) or not SERVICE_ID.fullmatch(envelope['extensionId'])):
        raise ManagerError('invalid existing integration')
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/requests/prepare', timeout=90,
        body={key: envelope[key] for key in ({'chatId', 'requestId'} | extra)})
    if status == 409 and isinstance(value, dict) and set(value) == {'detail'}:
        rejection = _exact_object(value['detail'], {'schemaVersion', 'kind', 'chatId',
                                                  'requestId', 'reason', 'installationStarted'})
        if (rejection['schemaVersion'] != 1
                or rejection['kind'] != 'ods-extension-request-preparation-rejected'
                or any(rejection[key] != envelope[key] for key in ('chatId', 'requestId'))
                or rejection['reason'] not in ('proposal_required', 'integration_selection_required',
                    'license_review_required', 'repository_evidence_unavailable',
                    'recipe_inspection_required', 'request_changed')
                or rejection['installationStarted'] is not False):
            raise ManagerError('invalid preparation rejection')
        return rejection
    if extra or isinstance(value, dict) and value.get('kind') == 'ods-extension-request-binding':
        value = _exact_object(value, {'schemaVersion', 'kind', 'chatId', 'requestId',
            'extensionId', 'definitionDigest', 'state', 'installationStarted', 'runtimeVerified'})
        if (status != 200 or value['schemaVersion'] != 1 or value['kind'] != 'ods-extension-request-binding'
                or any(value[key] != envelope[key] for key in ('chatId', 'requestId'))
                or not isinstance(value['extensionId'], str) or not SERVICE_ID.fullmatch(value['extensionId'])
                or (extra and value['extensionId'] != envelope['extensionId'])
                or value['state'] != 'bound' or not isinstance(value['definitionDigest'], str)
                or not HEX_KEY.fullmatch(value['definitionDigest'])
                or value['installationStarted'] is not False or value['runtimeVerified'] is not False):
            raise ManagerError('invalid integration binding receipt')
        return value
    value = _exact_object(value, {'schemaVersion', 'kind', 'chatId', 'requestId', 'draftId',
        'extensionId', 'recipeDigest', 'state', 'installationStarted', 'registered', 'runtimeVerified'})
    if (status != 200 or value['schemaVersion'] != 1 or value['kind'] != 'ods-extension-request-preparation'
            or any(value[key] != envelope[key] for key in ('chatId', 'requestId'))
            or value['state'] != 'available'
            or any(not isinstance(value[key], str) or not HEX_KEY.fullmatch(value[key]) for key in ('draftId', 'recipeDigest'))
            or not isinstance(value['extensionId'], str) or not SERVICE_ID.fullmatch(value['extensionId'])
            or any(value[key] is not False for key in ('installationStarted', 'registered', 'runtimeVerified'))):
        raise ManagerError('invalid scoped preparation receipt')
    return value


def _read_request_status(env_path, port, payload):
    envelope = _exact_object(json.loads(payload.decode('utf-8')),
        {'schemaVersion', 'action', 'chatId', 'requestId'})
    if (len(payload) > MAX_FRAME_BYTES or envelope['schemaVersion'] != 1
            or envelope['action'] != 'github-request-status'
            or any(not isinstance(envelope[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', envelope[key])
                   for key in ('chatId', 'requestId'))):
        raise ManagerError('invalid scoped status request')
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/requests/status', timeout=25,
        body={key: envelope[key] for key in ('chatId', 'requestId')})
    extra = {key for key in ('existingExtensionIds', 'integrationBound', 'runtimeError',
                             'installationVerified') if isinstance(value, dict) and key in value}
    value = _exact_object(value, {'schemaVersion', 'kind', 'chatId', 'requestId', 'requestState',
                                  'authorizationMode', 'proposalAccepted', 'prepared', 'extensionId', 'runtimeStatus'} | extra)
    matches = value.get('existingExtensionIds', [])
    if 'runtimeError' in value and (value['runtimeStatus'] != 'error'
            or not isinstance(value['runtimeError'], str) or not value['runtimeError'].strip()
            or len(value['runtimeError']) > 8192):
        raise ManagerError('invalid scoped runtime diagnostic')
    if (not isinstance(matches, list) or len(matches) > 64
            or any(not isinstance(item, str) or not SERVICE_ID.fullmatch(item) for item in matches)
            or len(set(matches)) != len(matches)):
        raise ManagerError('invalid repository matches')
    if (status != 200 or value['schemaVersion'] != 1 or value['kind'] != 'ods-extension-request-status'
            or any(value[key] != envelope[key] for key in ('chatId', 'requestId'))
            or value['requestState'] not in {'pending', 'cancelled', 'expired'}
            or value['authorizationMode'] not in {'install', 'research'}
            or any(type(value[key]) is not bool for key in ('proposalAccepted', 'prepared'))
            or type(value.get('integrationBound', False)) is not bool
            or (value.get('integrationBound', False) and value['proposalAccepted'])
            or (value['extensionId'] is not None and (not isinstance(value['extensionId'], str)
                or not SERVICE_ID.fullmatch(value['extensionId'])))
            or value['runtimeStatus'] not in {'not_observed', 'enabled', 'cli_installed', 'disabled',
                'stopped', 'not_installed', 'installing', 'setting_up', 'unhealthy', 'error', 'unavailable'}
            or (value['prepared'] and (not (value['proposalAccepted'] or value.get('integrationBound', False)) or not value['extensionId']))
            or (value['runtimeStatus'] != 'not_observed' and not value['prepared'])):
        raise ManagerError('invalid scoped status receipt')
    if ('installationVerified' in value and
            (type(value['installationVerified']) is not bool or
             value['installationVerified'] != (value['prepared'] and
                 value['requestState'] == 'pending' and
                 value['runtimeStatus'] in {'enabled', 'cli_installed'}))):
        raise ManagerError('invalid installation verification')
    return value


def _submit_request_proposal(env_path, port, payload):
    envelope = _exact_object(json.loads(payload.decode('utf-8')),
        {'schemaVersion', 'action', 'chatId', 'requestId', 'candidate'})
    if (len(payload) > MAX_FRAME_BYTES or envelope['schemaVersion'] != 1
            or envelope['action'] != 'github-request-propose'
            or any(not isinstance(envelope[key], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', envelope[key])
                   for key in ('chatId', 'requestId'))):
        raise ManagerError('invalid scoped proposal')
    candidate = _recipe_candidate(envelope['candidate'])
    credential = _read_env_key(_read_env(env_path), 'DASHBOARD_API_KEY')
    status, value = _request_json(port=port, credential=credential, method='POST',
        path='/api/extensions/github/requests/proposal', timeout=40,
        body={key: envelope[key] for key in ('chatId', 'requestId', 'candidate')})
    if status == 422:
        detail = value.get('detail')
        errors = detail.get('errors') if isinstance(detail, dict) else None
        existing = detail.get('existingExtensionIds', []) if isinstance(detail, dict) else None
        if (isinstance(detail, dict) and detail.get('code') == 'recipe-validation-failed'
                and isinstance(existing, list) and len(existing) <= 64
                and all(isinstance(item, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', item) for item in existing)
                and isinstance(errors, list) and 1 <= len(errors) <= 32
                and all(isinstance(item, dict) and set(item) == {'code', 'path'}
                        and isinstance(item['code'], str) and re.fullmatch('[a-z-]{1,80}', item['code'])
                        and isinstance(item['path'], str) and re.fullmatch('[A-Za-z0-9_/$.-]{1,256}', item['path'])
                        for item in errors)):
            return {'schemaVersion': 1, 'kind': 'ods-extension-request-proposal',
                    'chatId': envelope['chatId'], 'requestId': envelope['requestId'],
                    'state': 'invalid-recipe', 'errors': errors,
                    'existingExtensionIds': sorted(set(existing)), 'installationStarted': False}
        raise ManagerError('invalid recipe diagnostics')
    digest = hashlib.sha256(json.dumps(candidate, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    proposal = value.get('proposal', {})
    if (status != 200 or value.get('schemaVersion') != 1
            or value.get('chatId') != envelope['chatId'] or value.get('requestId') != envelope['requestId']
            or value.get('state') != 'pending' or value.get('installationStarted') is not False
            or _repository_url(value.get('repository')).lower() != _repository_url(candidate['repository']).lower()
            or not isinstance(proposal, dict) or set(proposal) != {'draftId', 'recipeDigest', 'extensionId'}
            or not isinstance(proposal['draftId'], str) or not re.fullmatch('[a-f0-9]{64}', proposal['draftId'])
            or proposal['recipeDigest'] != digest or proposal['extensionId'] != candidate['manifest'].get('service', {}).get('id')):
        raise ManagerError('invalid scoped proposal receipt')
    return {'schemaVersion': 1, 'kind': 'ods-extension-request-proposal',
            'chatId': envelope['chatId'], 'requestId': envelope['requestId'], 'state': 'pending',
            'proposal': proposal, 'installationStarted': False}


def _integration_guidance(value, extension_id):
    if value is None:
        return None
    expected = {'schemaVersion', 'extensionId', 'scope', 'contentTrust', 'description',
                'declaredConnection', 'documentation', 'documentationTruncated',
                'connectivityVerified', 'projectIntegrationVerified'}
    value = _exact_object(value, expected)
    if (value['schemaVersion'] != 1 or value['extensionId'] != extension_id
            or value['scope'] != 'recipe-integration-guidance' or value['contentTrust'] != 'untrusted-recipe-evidence'
            or value['connectivityVerified'] is not False or value['projectIntegrationVerified'] is not False
            or type(value['documentationTruncated']) is not bool):
        raise ManagerError('invalid integration guidance')
    for key, maximum in [('description', 2000), ('documentation', 24000)]:
        text = value[key]
        if key == 'documentation' and text is None:
            continue
        if (not isinstance(text, str) or len(text) > maximum
                or any((ord(char) < 32 and char not in '\n\r\t') or ord(char) == 127 for char in text)):
            raise ManagerError('invalid integration text')
    connection = value['declaredConnection']
    if not isinstance(connection, dict):
        raise ManagerError('invalid declared connection')
    for key, item in connection.items():
        if key in {'port', 'external_port_default'}:
            if type(item) is not int or not 1 <= item <= 65535:
                raise ManagerError('invalid declared port')
        elif key in {'type', 'container_name', 'default_host', 'host_env', 'external_port_env'}:
            if not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', item):
                raise ManagerError('invalid declared connection field')
        else:
            raise ManagerError('unexpected connection field')
    return value


def _execute(
    *, env_path: pathlib.Path, port: int, action: str, extension_id: str
) -> dict[str, Any]:
    values = _read_env(env_path)
    credential = _read_env_key(values, "DASHBOARD_API_KEY")
    if action == "list":
        return _extension_inventory(port, credential)
    if action == "install-next":
        return _install_next(port, credential, extension_id)
    before = _detail(port, credential, extension_id)
    previous_status = _bounded_status(before.get("status"))
    required, optional = _configuration_keys(before)
    missing = [key for key in required if not values.get(key)]

    if action in {'inspect', 'install', 'enable'}:
        prerequisites = _installation_prerequisites(port, credential, extension_id)
        # The API reads the actual host environment. A WSL-side credential
        # projection may predate configuration supplied through the dashboard.
        if prerequisites.get('state') != 'unavailable' and prerequisites.get('steps'):
            root = prerequisites['steps'][-1]
            if root.get('extensionId') == extension_id and set(root['missingConfiguration']).issubset(required):
                missing = root['missingConfiguration']
                # The plan is fetched after detail and carries a validated
                # observation of the same root. Use one snapshot for both
                # status and prerequisites; otherwise a newly healthy service
                # can still be advertised as stopped and needlessly started.
                previous_status = _bounded_status(root['status'])
            else:
                prerequisites = {'state': 'unavailable', 'steps': []}
                missing = required
    if action == "inspect":
        # A completed read cannot establish runtime readiness. In particular,
        # an absent environment specification does not mean no setup is needed.
        result = _public_result(
            action=action,
            extension_id=extension_id,
            outcome="inspected" if not missing else "blocked",
            previous_status=previous_status,
            current_status=previous_status,
            changed=False,
            external_effect=False,
            required_configuration=required,
            optional_configuration=optional,
            missing_configuration=missing,
        )
        result["installationPrerequisites"] = prerequisites
        result["integration"] = _integration_guidance(before.get('integration'), extension_id)
        return result

    idempotent = {
        "install": previous_status in {"enabled", "cli_installed", "disabled", "stopped"},
        "enable": previous_status in {"enabled", "cli_installed"},
        "disable": previous_status in {"disabled", "not_installed"},
        "remove": previous_status == "not_installed",
    }[action]
    if idempotent:
        return _public_result(
            action=action,
            extension_id=extension_id,
            outcome="noop",
            previous_status=previous_status,
            current_status=previous_status,
            changed=False,
            external_effect=False,
            required_configuration=required,
            optional_configuration=optional,
            missing_configuration=missing,
        )
    if action in {"install", "enable"} and missing:
        return _public_result(
            action=action,
            extension_id=extension_id,
            outcome="blocked",
            previous_status=previous_status,
            current_status=previous_status,
            changed=False,
            external_effect=False,
            required_configuration=required,
            optional_configuration=optional,
            missing_configuration=missing,
        )
    transition_allowed = {
        "install": previous_status == "not_installed",
        # The dashboard's enable endpoint also starts an installed service
        # whose Compose definition is enabled but whose container is stopped.
        "enable": previous_status in {"disabled", "stopped"},
        "disable": previous_status in {"enabled", "cli_installed"},
        # Removal owns its safe stop prerequisite. The immutable broker plan
        # still grants only this one typed lifecycle action, while the manager
        # verifies disabled before issuing the data-preserving removal request.
        "remove": previous_status in {"enabled", "cli_installed", "disabled"},
    }[action]
    if not transition_allowed:
        return _public_result(
            action=action,
            extension_id=extension_id,
            outcome="blocked",
            previous_status=previous_status,
            current_status=previous_status,
            changed=False,
            external_effect=False,
            required_configuration=required,
            optional_configuration=optional,
            missing_configuration=missing,
        )

    current_status = previous_status
    try:
        if action == "remove" and previous_status in {"enabled", "cli_installed"}:
            _mutate(
                port=port,
                credential=credential,
                action="disable",
                extension_id=extension_id,
                previous=previous_status,
            )
            current_status = _wait_for_status(
                port=port,
                credential=credential,
                extension_id=extension_id,
                expected=frozenset({"disabled"}),
                deadline=time.monotonic() + 120,
            )
            if current_status != "disabled":
                raise ManagerError("extension did not reach the removal prerequisite")
        _mutate(
            port=port,
            credential=credential,
            action=action,
            extension_id=extension_id,
            previous=current_status,
        )
        current_status = _wait_for_status(
            port=port,
            credential=credential,
            extension_id=extension_id,
            expected=SUCCESS_STATUS[action],
            deadline=time.monotonic() + (600 if action == "install" else 120),
        )
    except ManagerError:
        # A timeout or unavailable response can happen after the internal API
        # accepted the request. Reconcile from a fresh read and conservatively
        # report that an external effect was attempted.
        try:
            current_status = _bounded_status(
                _detail(port, credential, extension_id).get("status")
            )
        except ManagerError:
            current_status = previous_status
    succeeded = current_status in SUCCESS_STATUS[action]
    rollback_attempted = False
    rollback_succeeded: bool | None = None
    # A failed start of an already-enabled definition must not disable it.
    # The narrow API has no stop-without-disabling rollback operation. Preserve
    # its configuration and report the observed failure instead of pretending
    # that disabling restores the original stopped state.
    can_restore_definition = not (action == "enable" and previous_status == "stopped")
    if not succeeded and action in {"install", "enable", "disable"} and can_restore_definition:
        try:
            observed = _bounded_status(_detail(port, credential, extension_id).get("status"))
            # Installation may complete after the bounded waiter returned.
            # Never undo freshly confirmed success or race an active setup.
            # A later inspect can reconcile an unfinished operation without
            # submitting the same mutation again.
            if observed in SUCCESS_STATUS[action] or observed in {"installing", "setting_up"}:
                return _public_result(
                    action=action,
                    extension_id=extension_id,
                    outcome="succeeded" if observed in SUCCESS_STATUS[action] else "pending",
                    previous_status=previous_status,
                    current_status=observed,
                    changed=not _same_effective_status(observed, previous_status),
                    external_effect=True,
                    required_configuration=required,
                    optional_configuration=optional,
                    missing_configuration=[],
                )
            rollback_attempted = True
            if action == "install":
                if observed in {"enabled", "cli_installed", "stopped", "unhealthy", "error"}:
                    _mutate(
                        port=port,
                        credential=credential,
                        action="disable",
                        extension_id=extension_id,
                        previous=observed,
                    )
                    observed = _wait_for_status(
                        port=port,
                        credential=credential,
                        extension_id=extension_id,
                        expected=frozenset({"disabled"}),
                        deadline=time.monotonic() + 120,
                    )
                if observed == "disabled":
                    _mutate(
                        port=port,
                        credential=credential,
                        action="remove",
                        extension_id=extension_id,
                        previous=observed,
                    )
                rollback_expected = frozenset({"not_installed"})
            elif action == "enable" and observed in {
                "enabled", "cli_installed", "stopped", "unhealthy", "error",
            }:
                _mutate(
                    port=port,
                    credential=credential,
                    action="disable",
                    extension_id=extension_id,
                    previous=observed,
                )
                rollback_expected = frozenset({"disabled"})
            elif action == "disable" and observed == "disabled":
                _mutate(
                    port=port,
                    credential=credential,
                    action="enable",
                    extension_id=extension_id,
                    previous=observed,
                )
                rollback_expected = frozenset({"enabled", "cli_installed"})
            else:
                rollback_expected = frozenset({previous_status})
            rolled_back_status = _wait_for_status(
                port=port,
                credential=credential,
                extension_id=extension_id,
                expected=rollback_expected,
                deadline=time.monotonic() + 120,
            )
            rollback_succeeded = rolled_back_status in rollback_expected
            current_status = rolled_back_status
        except ManagerError:
            if rollback_attempted:
                rollback_succeeded = False
    elif not succeeded and action == "remove":
        # A remove request is irreversible once the host reaches
        # not_installed. Before that point, restore an enabled extension if
        # the safe stop prerequisite succeeded but removal did not.
        try:
            observed = _bounded_status(_detail(port, credential, extension_id).get("status"))
            current_status = observed
            if observed == "disabled" and previous_status in {"enabled", "cli_installed"}:
                rollback_attempted = True
                _mutate(
                    port=port,
                    credential=credential,
                    action="enable",
                    extension_id=extension_id,
                    previous=observed,
                )
                rolled_back_status = _wait_for_status(
                    port=port,
                    credential=credential,
                    extension_id=extension_id,
                    expected=frozenset({"enabled", "cli_installed"}),
                    deadline=time.monotonic() + 120,
                )
                rollback_succeeded = rolled_back_status in {"enabled", "cli_installed"}
                current_status = rolled_back_status
        except ManagerError:
            if rollback_attempted:
                rollback_succeeded = False
    return _public_result(
        action=action,
        extension_id=extension_id,
        outcome="succeeded" if succeeded else "failed",
        previous_status=previous_status,
        current_status=current_status,
        changed=not _same_effective_status(current_status, previous_status),
        external_effect=True,
        required_configuration=required,
        optional_configuration=optional,
        missing_configuration=[],
        rollback_attempted=rollback_attempted,
        rollback_succeeded=rollback_succeeded,
    )


def _error_result(action: str, extension_id: str) -> dict[str, Any]:
    if action == "install-next":
        return _installation_result(extension_id)
    if action == "list":
        return {
            "schemaVersion": SCHEMA_VERSION,
            "kind": INVENTORY_KIND,
            "outcome": "failed",
            "summary": {
                "total": 0,
                "installed": 0,
                "enabled": 0,
                "cliInstalled": 0,
                "disabled": 0,
                "stopped": 0,
                "unhealthy": 0,
                "installing": 0,
                "settingUp": 0,
                "error": 0,
                "notInstalled": 0,
                "incompatible": 0,
            },
            "extensions": [],
            "boundary": INVENTORY_BOUNDARY,
        }
    return _public_result(
        action=action if action in ALLOWED_ACTIONS else "inspect",
        extension_id=extension_id if SERVICE_ID.fullmatch(extension_id or "") else "invalid",
        outcome="failed",
        # A rejected identifier, unavailable API or invalid receipt gives no
        # evidence about installation. Absence is only a catalog observation.
        previous_status="unknown",
        current_status="unknown",
        changed=False,
        external_effect=False,
        required_configuration=[],
        optional_configuration=[],
        missing_configuration=[],
    )


def _serve_connection(
    connection: socket.socket, *, expected_uid: int, env_path: pathlib.Path, port: int,
    credential_source: pathlib.Path | None = None,
) -> None:
    action = "inspect"
    extension_id = "invalid"
    try:
        uid, _gid = peer_ids(connection)
        connection.settimeout(15)
        chunks = bytearray()
        while len(chunks) <= MAX_FRAME_BYTES:
            piece = connection.recv(min(4096, MAX_FRAME_BYTES + 1 - len(chunks)))
            if not piece:
                break
            chunks.extend(piece)
            if b"\n" in piece:
                break
        if b"\n" not in chunks or chunks.count(b"\n") != 1 or not chunks.endswith(b"\n"):
            raise ManagerError("invalid lifecycle request framing")
        request_payload = bytes(chunks[:-1])
        if uid not in {expected_uid, os.getuid()}:
            raise ManagerError('unauthorized lifecycle peer')
        if uid == expected_uid:
            if credential_source is not None:
                _refresh_projected_credential(credential_source, env_path)
            envelope = json.loads(request_payload.decode('utf-8'))
            if isinstance(envelope, dict) and envelope.get('action') == 'github-inspect':
                repository = _parse_repository_request(request_payload)
                result = _inspect_repository(env_path, port, repository)
            elif isinstance(envelope, dict) and envelope.get('action') == 'github-file':
                fields = _parse_repository_file_request(request_payload)
                result = _inspect_repository_file(env_path, port, fields)
            elif isinstance(envelope, dict) and envelope.get('action') == 'github-validate':
                recipe = _parse_recipe_request(request_payload)
                result = _validate_repository_recipe(env_path, port, recipe)
            elif isinstance(envelope, dict) and envelope.get('action') == 'github-draft-save':
                recipe = _parse_recipe_request(request_payload, 'github-draft-save')
                result = _save_repository_draft(env_path, port, recipe)
            elif isinstance(envelope, dict) and envelope.get('action') in ('github-draft-read', 'github-draft-prepare'):
                envelope = _exact_object(envelope, {'schemaVersion', 'action', 'draftId'})
                if envelope['schemaVersion'] != 1 or len(request_payload) > MAX_REQUEST_BYTES:
                    raise ManagerError('invalid draft recovery request')
                operation = _prepare_repository_draft if envelope['action'] == 'github-draft-prepare' else _read_repository_draft
                result = operation(env_path, port, _draft_id(envelope['draftId']))
            else:
                action, extension_id = _parse_request(request_payload)
                result = _execute(
                    env_path=env_path,
                    port=port,
                    action=action,
                    extension_id=extension_id,
                )
        elif uid == os.getuid():
            envelope = json.loads(request_payload.decode('utf-8'))
            if isinstance(envelope, dict) and envelope.get('action') == 'github-request-read':
                if credential_source is not None:
                    _refresh_projected_credential(credential_source, env_path)
                result = _read_request_source(env_path, port, request_payload)
            elif isinstance(envelope, dict) and envelope.get('action') == 'github-request-pin':
                if credential_source is not None:
                    _refresh_projected_credential(credential_source, env_path)
                result = _pin_request_repository(env_path, port, request_payload)
            elif isinstance(envelope, dict) and envelope.get('action') == 'github-request-resolve':
                if credential_source is not None:
                    _refresh_projected_credential(credential_source, env_path)
                result = _resolve_request(env_path, port, request_payload)
            elif isinstance(envelope, dict) and envelope.get('action') in {'github-request-advance', 'github-request-retry'}:
                # Only the saved, owner-bound prepared recipe can advance.
                # Arbitrary host commands/lifecycle targets remain broker-only.
                if credential_source is not None:
                    _refresh_projected_credential(credential_source, env_path)
                result = _advance_request(env_path, port, request_payload)
            elif isinstance(envelope, dict) and envelope.get('action') == 'github-request-prepare':
                if credential_source is not None:
                    _refresh_projected_credential(credential_source, env_path)
                result = _prepare_request(env_path, port, request_payload)
            elif isinstance(envelope, dict) and envelope.get('action') == 'github-request-status':
                if credential_source is not None:
                    _refresh_projected_credential(credential_source, env_path)
                result = _read_request_status(env_path, port, request_payload)
            elif isinstance(envelope, dict) and envelope.get('action') == 'github-request-propose':
                # The same-owner agent may only propose; active request/repo
                # binding is enforced by the API. Lifecycle remains broker-only.
                if credential_source is not None:
                    _refresh_projected_credential(credential_source, env_path)
                result = _submit_request_proposal(env_path, port, request_payload)
            else:
                job_id, plan_hash = _parse_status_request(request_payload)
                result = _read_operations_status(
                    results_dir=OPS_RESULTS_DIR,
                    broker_uid=expected_uid,
                    job_id=job_id,
                    plan_hash=plan_hash,
                )
        else:
            raise ManagerError("unauthorized lifecycle peer")
    except (ManagerError, OSError, ValueError, TypeError, RecursionError):
        result = _error_result(action, extension_id)
    payload = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
    connection.sendall(payload)


def serve(socket_path: pathlib.Path, env_path: pathlib.Path, port: int, *, credential_source=None) -> int:
    if (
        socket_path != SOCKET_PATH
        or not 1 <= port <= 65535
    ):
        raise ManagerError("invalid lifecycle server configuration")
    broker_uid = pwd.getpwnam(BROKER_USER).pw_uid
    parent = socket_path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_mode & 0o007:
        raise ManagerError("unsafe lifecycle socket directory")
    if socket_path.exists() or socket_path.is_symlink():
        info = socket_path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ManagerError("unsafe existing lifecycle socket")
        socket_path.unlink()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind(str(socket_path))
        os.chmod(socket_path, 0o660)
        listener.listen(8)
        while True:
            connection, _address = listener.accept()
            with connection:
                _serve_connection(
                    connection,
                    expected_uid=broker_uid,
                    env_path=env_path,
                    port=port,
                    credential_source=credential_source,
                )
    finally:
        listener.close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def client(socket_path: pathlib.Path, action: str, extension_id: str) -> int:
    if (
        socket_path != SOCKET_PATH
        or action not in ALLOWED_ACTIONS
        or SERVICE_ID.fullmatch(extension_id) is None
        or (action == "list") != (extension_id == "all")
    ):
        raise ManagerError("invalid lifecycle client request")
    request = {
        "schemaVersion": SCHEMA_VERSION,
        "action": action,
        "extensionId": extension_id,
    }
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(900)
    try:
        connection.connect(str(socket_path))
        connection.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        chunks = bytearray()
        while len(chunks) <= MAX_RESPONSE_BYTES:
            piece = connection.recv(min(65536, MAX_RESPONSE_BYTES + 1 - len(chunks)))
            if not piece:
                break
            chunks.extend(piece)
    finally:
        connection.close()
    if len(chunks) > MAX_RESPONSE_BYTES or chunks.count(b"\n") != 1 or not chunks.endswith(b"\n"):
        raise ManagerError("invalid lifecycle manager response")
    try:
        value = json.loads(bytes(chunks[:-1]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("invalid lifecycle manager response") from exc
    expected_kind = INVENTORY_KIND if action == "list" else INSTALLATION_KIND if action == "install-next" else KIND
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != expected_kind
    ):
        raise ManagerError("invalid lifecycle manager response")
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def repository_client(socket_path: pathlib.Path, repository=None, commit=None, path=None, recipe=None, save_draft=False, draft_id=None, prepare_draft=False) -> int:
    if socket_path != SOCKET_PATH:
        raise ManagerError('invalid repository socket')
    if recipe is not None:
        recipe = _recipe_candidate(recipe)
        repository = recipe['repository']
    repository = _repository_url(repository) if draft_id is None else None
    request = {'schemaVersion': 1, 'action': 'github-inspect', 'repositoryUrl': repository}
    expected_kind = REPOSITORY_KIND
    if commit is not None or path is not None:
        fields = _repository_file_fields(repository, commit, path)
        request.update(action='github-file', commit=fields['commit'], path=fields['path'])
        expected_kind = REPOSITORY_FILE_KIND
    if recipe is not None:
        request = {'schemaVersion': 1, 'action': 'github-validate', 'recipe': recipe}
        expected_kind = RECIPE_VALIDATION_KIND
        if save_draft:
            request['action'] = 'github-draft-save'
            expected_kind = RECIPE_DRAFT_KIND
    if draft_id is not None:
        request = {'schemaVersion': 1, 'action': 'github-draft-read', 'draftId': _draft_id(draft_id)}
        expected_kind = RECIPE_RECOVERY_KIND
        if prepare_draft:
            request['action'] = 'github-draft-prepare'
            expected_kind = RECIPE_PREPARATION_KIND
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(100)
    try:
        connection.connect(str(socket_path))
        connection.sendall((json.dumps(request, separators=(',', ':')) + '\n').encode())
        chunks = bytearray()
        while len(chunks) <= MAX_RESPONSE_BYTES:
            part = connection.recv(min(65536, MAX_RESPONSE_BYTES + 1 - len(chunks)))
            if not part:
                break
            chunks.extend(part)
            if b'\n' in part:
                break
    finally:
        connection.close()
    if len(chunks) > MAX_RESPONSE_BYTES or chunks.count(b'\n') != 1 or not chunks.endswith(b'\n'):
        raise ManagerError('invalid repository response framing')
    value = json.loads(chunks)
    if expected_kind == RECIPE_PREPARATION_KIND:
        value = _preparation_receipt(value, draft_id)
        sys.stdout.write(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')
        return 0
    if expected_kind == RECIPE_RECOVERY_KIND:
        if not isinstance(value, dict) or value.get('kind') != expected_kind:
            raise ManagerError('invalid draft recovery response')
        value = _recover_draft_receipt(value, draft_id)
        sys.stdout.write(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')
        return 0
    if (not isinstance(value, dict) or value.get('schemaVersion') != 1
            or value.get('kind') != expected_kind
            or _repository_url(value.get('repository')).lower() != repository.lower()
            or value.get('installationStarted') is not False or value.get('registered') is not False):
        raise ManagerError('invalid repository response')
    if expected_kind == REPOSITORY_FILE_KIND and (value.get('commit') != commit or value.get('path') != path):
        raise ManagerError('mismatched repository file response')
    if expected_kind == RECIPE_VALIDATION_KIND:
        value = _validation_receipt(value, recipe)
    if expected_kind == RECIPE_DRAFT_KIND:
        value = _draft_receipt(value, recipe)
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n')
    return 0


def status_client(socket_path: pathlib.Path, job_id: str, plan_hash: str) -> int:
    if (
        socket_path != SOCKET_PATH
        or JOB_ID.fullmatch(job_id) is None
        or HEX_KEY.fullmatch(plan_hash) is None
    ):
        raise ManagerError("invalid Operations status client request")
    request = {
        "schemaVersion": SCHEMA_VERSION,
        "action": "opsStatus",
        "jobId": job_id,
        "planHash": plan_hash,
    }
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5)
    try:
        connection.connect(str(socket_path))
        connection.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        chunks = bytearray()
        while len(chunks) <= MAX_OPS_STATUS_BYTES:
            piece = connection.recv(min(8192, MAX_OPS_STATUS_BYTES + 1 - len(chunks)))
            if not piece:
                break
            chunks.extend(piece)
    finally:
        connection.close()
    if (
        len(chunks) > MAX_OPS_STATUS_BYTES
        or chunks.count(b"\n") != 1
        or not chunks.endswith(b"\n")
    ):
        raise ManagerError("invalid Operations status response")
    try:
        value = json.loads(bytes(chunks[:-1]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("invalid Operations status response") from exc
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != OPS_STATUS_KIND
        or value.get("jobId") != job_id
        or value.get("planHash") != plan_hash
    ):
        raise ManagerError("invalid Operations status response")
    sys.stdout.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 5 and argv[1] == "serve":
            port = int(argv[4], 10)
            return serve(pathlib.Path(argv[2]), pathlib.Path(argv[3]), port)
        if len(argv) == 7 and argv[1] == 'serve' and argv[5] == '--credential-source':
            return serve(pathlib.Path(argv[2]), pathlib.Path(argv[3]), int(argv[4], 10),
                         credential_source=pathlib.Path(argv[6]))
        if len(argv) == 5 and argv[1] == "client":
            return client(pathlib.Path(argv[2]), argv[3], argv[4])
        if len(argv) == 5 and argv[1] == "status":
            return status_client(pathlib.Path(argv[2]), argv[3], argv[4])
        if len(argv) == 4 and argv[1] == 'repository':
            return repository_client(pathlib.Path(argv[2]), argv[3])
        if len(argv) == 6 and argv[1] == 'repository-file':
            return repository_client(pathlib.Path(argv[2]), argv[3], argv[4], argv[5])
        if len(argv) == 4 and argv[1] == 'repository-validate' and len(argv[3].encode()) <= MAX_RECIPE_BYTES:
            return repository_client(pathlib.Path(argv[2]), recipe=json.loads(argv[3]))
        if len(argv) == 4 and argv[1] == 'repository-draft-save' and len(argv[3].encode()) <= MAX_RECIPE_BYTES:
            return repository_client(pathlib.Path(argv[2]), recipe=json.loads(argv[3]), save_draft=True)
        if len(argv) == 4 and argv[1] == 'repository-draft-read':
            return repository_client(pathlib.Path(argv[2]), draft_id=_draft_id(argv[3]))
        if len(argv) == 4 and argv[1] == 'repository-draft-prepare':
            return repository_client(pathlib.Path(argv[2]), draft_id=_draft_id(argv[3]), prepare_draft=True)
        if len(argv) == 11 and argv[1] in ('repository-validate-parts', 'repository-draft-save-parts'):
            return repository_client(pathlib.Path(argv[2]), recipe=_recipe_parts(argv[3:]),
                                     save_draft=argv[1] == 'repository-draft-save-parts')
        raise ManagerError(
            "usage: extension_manager.py serve SOCKET ENV PORT | client SOCKET ACTION ID | status SOCKET JOB HASH | repository SOCKET URL"
        )
    except (ManagerError, OSError, ValueError, KeyError, RecursionError):
        # The caller receives only a stable error. Credentials, local paths,
        # upstream bodies, and exception details never cross the boundary.
        sys.stderr.write("ODS extension lifecycle request failed\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
